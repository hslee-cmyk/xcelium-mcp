"""Tests for poll_batch_log completion-detection (F-174).

F-174 bug: bare "$finish"/"PASS"/"FAIL" substring matching caused
sim_batch_run to return early — a TCL setup script's first-line comment
(literally containing "$finish") or an individual assertion line
("[V-18] PASS: ...") could satisfy the fast-path before the simulation
actually finished. These tests exercise poll_batch_log directly (previously
only ever mocked by its callers).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.batch_polling import poll_batch_log


@pytest.mark.asyncio
async def test_tcl_comment_with_finish_does_not_complete_early() -> None:
    """A setup script's '# ... run to $finish (no MCP bridge)' comment line
    must not be mistaken for the real completion marker."""
    comment_line = "xcelium> # setup_rtl_batch.tcl — batch xmsim: dump all signals + run to $finish (no MCP bridge)"
    with (
        patch("xcelium_mcp.batch_polling.shell_run", new_callable=AsyncMock) as mock_ssh,
        patch("xcelium_mcp.batch_polling.asyncio.sleep", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_polling._time") as mock_time,
    ):
        # deadline calc, then two loop-entry checks (True) that each poll the
        # comment line, then a third check (False) that ends the loop.
        mock_time.time.side_effect = [0.0, 0.0, 0.3, 5.0]
        mock_ssh.side_effect = [
            comment_line,
            comment_line,
            "PASS|FAIL|Errors:|$finish|COMPLETE not found",  # final grep, no matches
            "",  # rm -f done_file
        ]
        result, timed_out = await poll_batch_log("/tmp/x.log", timeout=1.0)
        assert timed_out is True
        # Two tail polls happened, then the final grep + cleanup rm.
        assert mock_ssh.call_count == 4


@pytest.mark.asyncio
async def test_individual_pass_assertion_does_not_complete_early() -> None:
    """A mid-run assertion log line like '[V-18] PASS: ...' must not trigger
    completion on its own (no COMPLETE/Errors: anchor, no done_file)."""
    with (
        patch("xcelium_mcp.batch_polling.shell_run", new_callable=AsyncMock) as mock_ssh,
        patch("xcelium_mcp.batch_polling.asyncio.sleep", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_polling._time") as mock_time,
    ):
        mock_time.time.side_effect = [0.0, 0.0, 5.0]
        mock_ssh.side_effect = [
            "[V-18] PASS: some_check\n[V-22] PASS: other_check",
            "final summary line",  # final grep
            "",  # rm -f done_file
        ]
        result, timed_out = await poll_batch_log("/tmp/x.log", timeout=1.0)
        assert timed_out is True
        assert mock_ssh.call_count == 3


@pytest.mark.asyncio
async def test_simulation_complete_marker_completes_normally() -> None:
    """The literal 'Simulation complete via $finish' phrase xmsim emits on
    real completion must still be detected."""
    with (
        patch("xcelium_mcp.batch_polling.shell_run", new_callable=AsyncMock) as mock_ssh,
        patch("xcelium_mcp.batch_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("xcelium_mcp.batch_polling._time") as mock_time,
    ):
        mock_time.time.side_effect = [0.0, 0.0]
        mock_ssh.side_effect = [
            "Simulation complete via $finish(1) at time 110803033 NS + 0",
            "[TOP015] ... COMPLETE. Errors: 0",  # final grep
            "",  # rm -f done_file
        ]
        result, timed_out = await poll_batch_log("/tmp/x.log", timeout=600.0)
        assert timed_out is False
        assert "COMPLETE" in result
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_done_file_completes_regardless_of_keywords() -> None:
    """done_file (__DONE__) must still short-circuit completion immediately,
    independent of any keyword matching — existing behavior preserved."""
    with (
        patch("xcelium_mcp.batch_polling.shell_run", new_callable=AsyncMock) as mock_ssh,
        patch("xcelium_mcp.batch_polling.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("xcelium_mcp.batch_polling._time") as mock_time,
    ):
        mock_time.time.side_effect = [0.0, 0.0]
        mock_ssh.side_effect = [
            "some unrelated log tail\n__DONE__",
            "final summary",  # final grep
            "",  # rm -f done_file
        ]
        result, timed_out = await poll_batch_log("/tmp/x.log", timeout=600.0)
        assert timed_out is False
        mock_sleep.assert_not_called()
