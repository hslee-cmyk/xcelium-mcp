"""tmp_cleanup.py — Temporary file lifecycle management for xcelium-mcp.

Policy:
  - Logs (*.log, *.log.done): TTL 24h, deleted at sim start
  - Screenshots (screenshot_*.ps): deleted immediately after ps_to_png (call sites)
  - Session cleanup: all logs + screenshots on sim_disconnect
  - Excludes: mcp_registry.json, setup_batch_*.tcl (permanent files)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


async def cleanup_old_logs(user_tmp: str, ttl_sec: int = 86400) -> int:
    """Delete *.log and *.log.done files in user_tmp older than ttl_sec seconds.

    Called at sim_bridge_run and sim_batch_run start.
    Returns count of deleted files.
    """
    deleted = 0
    cutoff = time.time() - ttl_sec
    tmp_path = Path(user_tmp)
    if not tmp_path.is_dir():
        return 0

    for pattern in ("*.log", "*.log.done"):
        for f in tmp_path.glob(pattern):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    deleted += 1
                    logger.debug("cleanup_old_logs: removed %s", f.name)
            except OSError:
                pass
    return deleted


async def cleanup_session_logs(user_tmp: str) -> int:
    """Delete all session logs and leftover screenshot PS files immediately.

    Called at sim_disconnect (shutdown). Handles files not cleaned up inline
    (e.g. due to conversion errors leaving orphaned .ps files).
    Returns count of deleted files.
    """
    deleted = 0
    tmp_path = Path(user_tmp)
    if not tmp_path.is_dir():
        return 0

    for pattern in ("*.log", "*.log.done", "screenshot_*.ps"):
        for f in tmp_path.glob(pattern):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
                logger.debug("cleanup_session_logs: removed %s", f.name)
            except OSError:
                pass
    return deleted
