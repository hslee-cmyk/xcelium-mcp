"""Batch log polling for xcelium-mcp.

Extracted from batch_runner.py (F-038 structural split).
Contains: poll_batch_log, watch_pid_and_poll.
"""
from __future__ import annotations

import asyncio
import time as _time

from xcelium_mcp.shell_utils import shell_run


async def poll_batch_log(log_file: str, timeout: float, prefix: str = "") -> tuple[str, bool]:
    """Poll a batch log file until completion keywords found or timeout.

    P6-1: Adaptive polling interval — 2s → 3s → 4.5s → 6.75s → 10s cap.
          Short gap catches fast tests; longer gap reduces SSH overhead for slow ones.
    P6-2: Single SSH call per poll — tail + done-file check in one round-trip.
    P6-5: .done marker file — reliable completion signal even when keywords scroll past tail.

    Returns: (result_str, timed_out) — timed_out=True when poll exhausted without completion.
    """
    deadline = _time.time() + timeout
    interval = 2.0          # P6-1: start at 2s
    done_file = f"{log_file}.done"
    timed_out = True

    while _time.time() < deadline:
        # P6-2: single SSH call — tail for keyword scan + done-file sentinel
        out = await shell_run(
            f"(tail -10 {log_file} || true); "
            f"test -f {done_file} && echo __DONE__"
        )
        if "__DONE__" in out or any(
            kw in out for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")
        ):
            timed_out = False
            break
        # P6-1: adaptive backoff (×1.5, cap 10s)
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 10.0)

    result = await shell_run(
        f"(grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {log_file} || true) | tail -30"
    )
    await shell_run(f"rm -f {done_file}", timeout=5)   # P6-5: cleanup marker
    return prefix + result, timed_out


async def watch_pid_and_poll(
    pid: int,
    log_file: str,
    job_file: str,
    timeout: int,
) -> str:
    """Poll batch log for completion and clean up job file.

    Waits for the batch simulation to complete by polling the log file,
    then removes the job state file.

    Args:
        pid: Process ID of the batch job (unused but kept for future use).
        log_file: Path to the batch log file to poll.
        job_file: Path to the job state file to clean up.
        timeout: Timeout in seconds for polling.

    Returns:
        Result string from log polling.
    """
    result, _ = await poll_batch_log(log_file, timeout)
    await shell_run(f"rm -f {job_file}", timeout=5)
    return result
