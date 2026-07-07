"""stdio_forward.py — pure-stdlib stdin/stdout <-> unix socket relay.

Replaces socat (unavailable on cloud0 without root — see
docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md §1.3/§5.2).

Usage: python -m xcelium_mcp.stdio_forward <socket_path>

Run as the client's ssh command target: the local ssh process's stdin/stdout
become this process's stdin/stdout, which this module relays to/from the
xcelium-mcp supervisor's unix domain socket.
"""
from __future__ import annotations

import os
import socket
import sys
import threading

_CHUNK_SIZE = 65536


def _pump_sock_to_stdout(sock: socket.socket) -> None:
    """Relay socket -> stdout until the peer (worker) closes its end.

    Runs in a background thread. If the worker side closes first (normal exit,
    or idle-culler killed it), there is nothing left to forward — os._exit(0)
    tears the whole process down immediately rather than leaving the main
    thread blocked forever on stdin.read() waiting for a client that will
    never get a response.
    """
    stdout = sys.stdout.buffer
    try:
        while True:
            chunk = sock.recv(_CHUNK_SIZE)
            if not chunk:
                break
            stdout.write(chunk)
            stdout.flush()
    except OSError:
        pass
    os._exit(0)


def _pump_stdin_to_sock(sock: socket.socket) -> None:
    """Relay stdin -> socket until the client closes stdin (normal disconnect)."""
    stdin = sys.stdin.buffer
    try:
        while True:
            chunk = stdin.read(_CHUNK_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)  # propagate EOF to the worker's stdin
        except OSError:
            pass


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m xcelium_mcp.stdio_forward <socket_path>", file=sys.stderr)
        return 2

    sock_path = sys.argv[1]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(sock_path)
    except OSError as e:
        print(f"stdio_forward: cannot connect to {sock_path}: {e}", file=sys.stderr)
        return 1

    reader = threading.Thread(target=_pump_sock_to_stdout, args=(sock,), daemon=True)
    reader.start()

    _pump_stdin_to_sock(sock)
    reader.join(timeout=5)

    try:
        sock.close()
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
