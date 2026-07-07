"""sim_session_reaper.py — TTL-based auto-shutdown of abandoned bridge-mode
xmsim sessions (F-2, xcelium-mcp-sim-session-reaper.design.md §4.2).

Run periodically by cron (deploy/crontab.example). Complements idle_culler.py
(F-B): that module cleans up idle MCP *worker* processes, which are children
of the supervisor. This module targets the xmsim/SimVision *simulator*
process itself — started outside xcelium-mcp (bridge mode: connect_simulator/
sim_bridge_run) and invisible to F-A/F-B, since it is not a child of any MCP
worker. Left unattended, an abandoned xmsim session can run indefinitely and
exhaust host disk (real incident — see plan.md).

registry.py's touch_activity() (called from TclBridge.execute_safe() whenever
a bridge instance knows its sim_dir) records "last_activity" per bridge
session. This module reads that timestamp, not process state — no /proc
access, so unlike idle_culler.py it is not Linux-only.
"""
from __future__ import annotations

import asyncio
import os
import time

from xcelium_mcp.registry import load_registry, save_registry
from xcelium_mcp.tcl_bridge import BRIDGE_ERRORS, TclBridge

DEFAULT_TTL_HOURS = 48
TTL_ENV_VAR = "XCELIUM_MCP_SIM_TTL_HOURS"
# Consecutive TTL-exceeded checks required before actually shutting a session
# down — absorbs timing races between this reaper's read and a client's
# in-flight touch_activity() write (design.md §4.2).
MIN_MISS_COUNT_TO_KILL = 2


# ---------------------------------------------------------------------------
# Pure decision logic — no I/O, unit-testable on any platform.
# ---------------------------------------------------------------------------


def effective_ttl_seconds() -> int:
    """Configurable TTL (XCELIUM_MCP_SIM_TTL_HOURS), falling back to
    DEFAULT_TTL_HOURS on a missing or invalid value."""
    raw = os.environ.get(TTL_ENV_VAR, "")
    try:
        hours = float(raw) if raw else DEFAULT_TTL_HOURS
    except ValueError:
        hours = DEFAULT_TTL_HOURS
    return int(hours * 3600)


def sessions_to_reap(registry: dict, ttl_seconds: int, now: float) -> list[tuple[str, str, int]]:
    """Scan registry for bridge sessions that have exceeded TTL for
    MIN_MISS_COUNT_TO_KILL consecutive checks.

    Mutates `registry` in place: resets ttl_miss_count to 0 for sessions
    within TTL, increments it for sessions past TTL. The caller persists
    `registry` afterward regardless of what this function returns, so the
    miss-count bookkeeping survives even in rounds where nothing is reaped.

    Returns (project_root, sim_dir, port) for each session that just crossed
    the kill threshold this round — batch/regression entries (no bridge_port)
    and legacy entries (no last_activity recorded yet) are never included.
    """
    to_reap: list[tuple[str, str, int]] = []
    for project_root, proj in registry.get("projects", {}).items():
        for sim_dir, env in proj.get("environments", {}).items():
            port = env.get("bridge_port")
            if not port:
                continue  # not a bridge session (e.g. batch/regression-only entry)
            last_activity = env.get("last_activity")
            if last_activity is None:
                continue  # activity tracking hasn't started for this entry yet
            if now - last_activity <= ttl_seconds:
                env["ttl_miss_count"] = 0
                continue
            env["ttl_miss_count"] = env.get("ttl_miss_count", 0) + 1
            if env["ttl_miss_count"] >= MIN_MISS_COUNT_TO_KILL:
                to_reap.append((project_root, sim_dir, port))
    return to_reap


# ---------------------------------------------------------------------------
# I/O — registry read/write + bridge shutdown.
# ---------------------------------------------------------------------------


async def _shutdown_session(port: int) -> None:
    """Best-effort safe shutdown via the bridge's __SHUTDOWN__ meta command
    (SHM-preserving finish/exit — tcl/mcp_bridge.tcl do_shutdown).

    A connection failure just means the port was already dead — an orphaned
    registry entry from a session that already ended some other way — not an
    error the caller needs to handle differently.
    """
    bridge = TclBridge(host="localhost", port=port, timeout=10.0)
    try:
        await bridge.connect()
        await bridge.execute_safe("__SHUTDOWN__")
    except BRIDGE_ERRORS:
        pass


async def reap_idle_sessions() -> list[str]:
    """Load the registry, reap any TTL-exceeded bridge sessions, persist the
    (possibly miss-count-updated) registry, and return the reaped sim_dirs."""
    registry = await asyncio.to_thread(load_registry)
    ttl_seconds = effective_ttl_seconds()
    now = time.time()
    to_reap = sessions_to_reap(registry, ttl_seconds, now)

    for project_root, sim_dir, port in to_reap:
        await _shutdown_session(port)
        registry["projects"][project_root]["environments"].pop(sim_dir, None)

    await asyncio.to_thread(save_registry, registry)
    return [sim_dir for _, sim_dir, _ in to_reap]


def main() -> int:
    asyncio.run(reap_idle_sessions())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
