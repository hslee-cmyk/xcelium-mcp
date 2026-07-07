"""supervisor.py — prefork supervisor for xcelium-mcp worker processes (안C+).

Replaces the cold `ssh cloud0 xcelium-mcp` spawn-per-connection model — every
reconnect used to fork a brand-new sshd->tcsh->python tree that nothing ever
reaped (docs/01-plan/features/xcelium-mcp-server-process-lifecycle.plan.md §1).

Uses socketserver.ForkingMixIn (stdlib) instead of hand-rolled os.fork()/SIGCHLD
handling — zombie reaping and a worker-count ceiling come for free (Checkpoint 3,
design.md §2). Application code (server.py, tools/*.py, BridgeManager) is
untouched: each fork still runs the exact same 1-connection-1-process
`mcp.run(transport="stdio")` model it always has — only the *process lifecycle*
around it changes (design.md §1.2 "무변경 우선").

xcelium_mcp.server is imported once here, at supervisor startup — forked
children inherit the already-imported modules via copy-on-write memory, so a
connection doesn't pay the cold-import cost the old per-connection ssh spawn did.

POSIX only (os.fork()) — see design.md §8 Test Plan for why this can't run or
be unit-tested on the Windows dev box; verification happens on cloud0.
"""
from __future__ import annotations

import os
import socketserver
import sys
from pathlib import Path

SOCKET_PATH = Path.home() / ".xcelium_mcp" / "run" / "xcelium-mcp.sock"

# socketserver.ForkingMixIn/UnixStreamServer don't exist on Windows (no os.fork()/
# AF_UNIX support there) — guard the whole platform-specific definition so this
# module stays importable on the Windows dev box (pytest collection, ruff, IDE
# tooling). main() below refuses to run at all on win32 regardless.
if sys.platform == "win32":

    class Supervisor:  # pragma: no cover — Windows import-safety stub only
        pass

    class WorkerHandler:  # pragma: no cover
        pass

else:
    import xcelium_mcp.server as _xcelium_server

    class Supervisor(socketserver.ForkingMixIn, socketserver.UnixStreamServer):
        """Fork-per-connection unix socket server. Zombie reap + worker cap via ForkingMixIn."""

        max_children = 40  # stdlib default; raise if legitimate concurrent session count exceeds this

    class WorkerHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            # Re-bind this (forked) process's stdio to the accepted connection, then
            # run the *unmodified* xcelium-mcp entry point exactly as it always ran
            # under `ssh cloud0 xcelium-mcp` — the worker has no idea it's behind a
            # supervisor (design.md §5.1 — no lifecycle code in the worker at all).
            os.dup2(self.request.fileno(), 0)
            os.dup2(self.request.fileno(), 1)
            _xcelium_server.main()


def _prepare_socket_path() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        # Stale socket from a previous supervisor run (e.g. crash) — bind()
        # would otherwise fail with "address already in use".
        SOCKET_PATH.unlink()


def main() -> None:
    if sys.platform == "win32":
        print("xcelium-mcp-supervisor requires os.fork() — Linux/cloud0 only.", file=sys.stderr)
        raise SystemExit(1)

    _prepare_socket_path()

    # design.md §9: owner-only socket permission. Set restrictive umask around
    # bind() to close the brief window between socket-file creation and chmod()
    # (SELinux is disabled on cloud0 — this is the only access-control layer).
    old_umask = os.umask(0o177)
    try:
        server = Supervisor(str(SOCKET_PATH), WorkerHandler)
    finally:
        os.umask(old_umask)
    os.chmod(SOCKET_PATH, 0o600)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
