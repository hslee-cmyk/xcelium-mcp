"""idle_culler.py — /proc-only idle worker detection and cleanup (F-B, 안C+).

design.md §1.4 (0.3): no heartbeat file/thread of any kind — this script alone,
run periodically by cron (§7.2), decides which xcelium-mcp workers are idle by
reading kernel-maintained /proc state. Nothing is added to the worker or the
supervisor.

A worker counts as idle when BOTH are true:
  1. it holds no ESTABLISHED TCP connection at all (i.e. no live xmsim/SimVision
     bridge — a worker mid-simulation always has one)
  2. it has been running longer than IDLE_THRESHOLD_SEC

Design-doc refinement: `/proc/<pid>/net/tcp(+tcp6)` is NOT scoped per-process —
every pid on a host that shares the default network namespace sees the exact
same host-wide TCP table through that file. Filtering by pid therefore requires
cross-referencing the pid's own open socket file descriptors (/proc/<pid>/fd/*)
against that table's inode column — see has_established_tcp() below.

2026-07-07 cloud0 실측 정정: the deployed crontab wraps the supervisor as
`flock -n <lock> python3 -m xcelium_mcp.supervisor` (deploy/crontab.example,
root-owned /opt/mcp-env/bin/ ruled out the console-script name this marker
originally assumed). A naive substring search over the raw cmdline bytes also
matches the *flock* process itself, since flock's own argv literally contains
the whole wrapped command as one of its arguments — find_supervisor_pid() must
parse argv and explicitly skip any process whose own argv[0] is the flock
wrapper, or it returns the wrong pid (flock's, not python's), which then makes
find_worker_pids() look at flock's single child (the real supervisor) instead
of the supervisor's actual fork-per-connection worker children.

POSIX only (/proc) — see design.md §8 Test Plan; verification happens on cloud0.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

# Matches both the `python3 -m xcelium_mcp.supervisor` invocation actually used
# today (deploy/crontab.example) and the hyphenated console-script name from
# the optional systemd promotion path (design.md §7.3), in case that's ever
# installed instead.
SUPERVISOR_CMDLINE_MARKERS = (b"xcelium_mcp.supervisor", b"xcelium-mcp-supervisor")
IDLE_THRESHOLD_SEC = int(os.environ.get("XCELIUM_MCP_IDLE_THRESHOLD_SEC", 6 * 3600))
KILL_GRACE_SEC = 5
_TCP_ESTABLISHED = "01"

# F-E (session-state-reattach.design.md §5.3): job files written by
# batch_runner.py's launch_nohup_job() — same path pattern as
# shell_utils.get_user_tmp_dir(), reconstructed here without importing that
# (async, shell_run-based) module — idle_culler stays a dependency-free sync
# script (design.md §1.2 "무변경 우선" carried over from the parent feature).
_BATCH_JOB_FILES = ("batch_job.json", "regression_job.json")
# Comfortably larger than the largest per-call timeout in batch_runner.py
# (3600s) — a job file older than this is treated as stale/abandoned rather
# than a currently-running job, so a corrupt or leftover file can't disable
# idle-culling forever (Checkpoint 3, design.md §2 Option C).
STALE_JOB_FILE_SEC = 4 * 3600


# ---------------------------------------------------------------------------
# Pure parsing helpers — no /proc access, unit-testable on any platform.
# ---------------------------------------------------------------------------


def parse_stat_starttime(stat_text: str) -> int:
    """Extract starttime (clock ticks since boot) from /proc/<pid>/stat content.

    Format: "pid (comm) state ppid ... starttime ...". comm can itself contain
    spaces/parens, so skip past the *last* ')' rather than splitting naively.
    starttime is the 22nd whitespace-separated field overall — the 20th field
    after the trailing ')'.
    """
    _, _, rest = stat_text.rpartition(")")
    fields = rest.split()
    return int(fields[19])


def parse_uptime_seconds(uptime_text: str) -> float:
    """Extract system uptime (seconds) from /proc/uptime content."""
    return float(uptime_text.split()[0])


def parse_tcp_table_established_inodes(tcp_text: str) -> set[int]:
    """Return the set of socket inode numbers in ESTABLISHED state from the
    content of /proc/net/tcp or /proc/net/tcp6 (header line included or not)."""
    inodes: set[int] = set()
    for line in tcp_text.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[0] == "sl":  # header row starts with "sl"
            continue
        state, inode = fields[3], fields[9]
        if state == _TCP_ESTABLISHED:
            try:
                inodes.add(int(inode))
            except ValueError:
                continue
    return inodes


# ---------------------------------------------------------------------------
# /proc-backed lookups — Linux only, exercised on cloud0.
# ---------------------------------------------------------------------------


def parse_cmdline_argv(cmdline_bytes: bytes) -> list[bytes]:
    """Split raw /proc/<pid>/cmdline content (NUL-separated) into argv tokens."""
    return [a for a in cmdline_bytes.split(b"\x00") if a]


def is_supervisor_argv(argv: list[bytes]) -> bool:
    """Whether argv (as parsed by parse_cmdline_argv) is the real supervisor
    process — not the `flock -n <lock> python3 -m xcelium_mcp.supervisor`
    wrapper cron actually launches it through (deploy/crontab.example).

    flock's own argv contains the whole wrapped command as a literal
    substring, so a naive substring match over the joined cmdline would return
    flock's pid instead of the real supervisor's (see module docstring,
    2026-07-07 cloud0 실측) — exclude by checking argv[0]'s basename instead.
    """
    if not argv:
        return False
    if Path(argv[0].decode(errors="replace")).name == "flock":
        return False
    joined = b" ".join(argv)
    return any(marker in joined for marker in SUPERVISOR_CMDLINE_MARKERS)


def find_supervisor_pid() -> int | None:
    """Scan /proc/*/cmdline for the real xcelium-mcp-supervisor process."""
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = parse_cmdline_argv((entry / "cmdline").read_bytes())
        except OSError:
            continue  # pid exited between listdir() and read — not fatal
        if is_supervisor_argv(argv):
            return int(entry.name)
    return None


def find_worker_pids(supervisor_pid: int) -> list[int]:
    """Direct children of the supervisor = the currently-alive fork workers."""
    children_path = Path(f"/proc/{supervisor_pid}/task/{supervisor_pid}/children")
    try:
        text = children_path.read_text().strip()
    except OSError:
        return []
    return [int(p) for p in text.split()] if text else []


def _socket_inodes_for_pid(pid: int) -> set[int]:
    """Inode numbers of every socket fd this pid currently has open."""
    inodes: set[int] = set()
    fd_dir = Path(f"/proc/{pid}/fd")
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return inodes
    for fd_link in entries:
        try:
            target = os.readlink(fd_link)
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            try:
                inodes.add(int(target[len("socket:["):-1]))
            except ValueError:
                continue
    return inodes


def has_established_tcp(pid: int) -> bool:
    """Whether pid currently holds an ESTABLISHED TCP connection (v4 or v6)."""
    inodes = _socket_inodes_for_pid(pid)
    if not inodes:
        return False
    for tcp_file in (f"/proc/{pid}/net/tcp", f"/proc/{pid}/net/tcp6"):
        try:
            text = Path(tcp_file).read_text()
        except OSError:
            continue
        if inodes & parse_tcp_table_established_inodes(text):
            return True
    return False


def process_age_seconds(pid: int) -> float:
    stat_text = Path(f"/proc/{pid}/stat").read_text()
    uptime_text = Path("/proc/uptime").read_text()
    starttime_ticks = parse_stat_starttime(stat_text)
    clk_tck = os.sysconf("SC_CLK_TCK")
    return parse_uptime_seconds(uptime_text) - (starttime_ticks / clk_tck)


# ---------------------------------------------------------------------------
# F-E: batch/regression job awareness (session-state-reattach.design.md §5.3)
# ---------------------------------------------------------------------------


def _default_user_tmp_dir() -> Path:
    """Same path pattern as shell_utils.get_user_tmp_dir() — /tmp/xcelium_mcp_{uid}/.

    Only called from has_live_batch_job() when the caller doesn't supply an
    explicit dir (i.e. real runs via main()) — os.getuid() doesn't exist on
    Windows, so tests always pass user_tmp_dir explicitly instead of hitting
    this function, keeping has_live_batch_job() itself platform-independent.
    """
    return Path(f"/tmp/xcelium_mcp_{os.getuid()}")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # owned by another uid — reused pid, treat as alive (conservative)
    except OSError:
        # ProcessLookupError (pid doesn't exist, the common case) is an OSError
        # subclass and lands here too. Anything else os.kill(pid, 0) could
        # raise for a pid we don't recognize is treated the same way — "can't
        # confirm alive" — rather than risking a spurious platform-specific
        # exception type ever propagating out of a conservative safety check.
        return False


def has_live_batch_job(user_tmp_dir: Path | None = None) -> bool:
    """Whether a still-running sim_batch_run/sim_regression job exists.

    idle_culler must not cull a worker that's mid-poll on a long batch/regression
    run — those workers hold no TCP bridge at all (has_established_tcp() would
    wrongly say "idle"), since batch mode is pure shell_run log polling, not a
    Tcl bridge connection (see plan.md §1.4 for the full investigation). Job
    files are written by batch_runner.py's launch_nohup_job() and already carry
    a "pid" field for exactly this kind of liveness check.
    """
    user_tmp_dir = user_tmp_dir if user_tmp_dir is not None else _default_user_tmp_dir()
    now = time.time()
    for name in _BATCH_JOB_FILES:
        path = user_tmp_dir / name
        try:
            mtime = path.stat().st_mtime
            if now - mtime > STALE_JOB_FILE_SEC:
                continue  # stale/abandoned job file — don't let it block culling forever
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if _pid_alive(data.get("pid", 0)):
            return True
    return False


# ---------------------------------------------------------------------------
# Cull decision + entry point
# ---------------------------------------------------------------------------


def _cull_if_idle(pid: int) -> None:
    if has_established_tcp(pid):
        return
    if process_age_seconds(pid) <= IDLE_THRESHOLD_SEC:
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    time.sleep(KILL_GRACE_SEC)
    try:
        os.kill(pid, 0)  # still alive?
    except ProcessLookupError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main() -> int:
    if sys.platform == "win32":
        print("xcelium-mcp-culler requires /proc — Linux/cloud0 only.", file=sys.stderr)
        return 1

    if has_live_batch_job():
        # F-E: a live batch/regression job exists somewhere for this user — we
        # can't tell which worker (if any) is polling it (job_file records the
        # nohup'd simulation's pid, not the MCP worker's — they aren't in a
        # parent-child relationship, same reason as F-A/F-B's split), so skip
        # this entire round conservatively rather than risk culling it.
        return 0

    supervisor_pid = find_supervisor_pid()
    if supervisor_pid is None:
        return 0  # supervisor not running — cron watchdog (§7.2) handles restart, not us

    for pid in find_worker_pids(supervisor_pid):
        try:
            _cull_if_idle(pid)
        except (OSError, ValueError, IndexError):
            continue  # pid exited between listing and inspection — not fatal

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
