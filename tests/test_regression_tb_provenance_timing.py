"""Tests for run_batch_regression's per-test TB provenance timing (F-175 follow-up).

Before this fix, sim_regression computed tb_provenance for the whole
test_list via asyncio.gather AFTER run_batch_regression fully returned — a
single snapshot taken once at the very end. If a shared TB source file were
edited between an earlier test's run and a later one, the earlier test's
recorded provenance would silently reflect the LATER (wrong) file state
instead of what it was actually run against.

run_batch_regression now captures each test's tb_provenance immediately
after that test's own run, inside the per-test loop — before moving on to
the next test. These tests prove the capture happens per-test, in order,
rather than batched at the end.
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.batch_runner import run_batch_regression


def _make_runner(**overrides) -> dict:
    base = {
        "script": "run_sim.sh",
        "login_shell": "/bin/tcsh",
        "script_shell": True,
        "args_format": "-test {test_name} --",
    }
    base.update(overrides)
    return base


_LOG_NAME_RE = re.compile(r"regression_\d+_(.+)\.log$")


@pytest.mark.asyncio
async def test_provenance_captured_per_test_not_batched_at_end() -> None:
    """provenance for T1 must be captured before T2 even starts polling —
    proving it's read right after T1's own run, not after the whole
    regression completes."""
    runner = _make_runner()
    call_log: list[str] = []

    async def fake_poll(test_log: str, timeout: float):
        m = _LOG_NAME_RE.search(test_log)
        tn = m.group(1) if m else "?"
        call_log.append(f"poll:{tn}")
        return "", False  # (result, timed_out=False)

    async def fake_provenance(test_name: str, sim_dir: str):
        call_log.append(f"provenance:{test_name}")
        return {"path": f"/sim/tb/{test_name}.sv", "sha256": f"hash_{test_name}"}

    with (
        patch("xcelium_mcp.batch_runner.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
        patch("xcelium_mcp.batch_runner.shell_run", new_callable=AsyncMock, return_value=""),
        patch("xcelium_mcp.batch_runner.shell_run_fire_and_forget", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_runner.poll_batch_log", side_effect=fake_poll),
        patch("xcelium_mcp.batch_runner._preprocess_setup_tcl", new_callable=AsyncMock,
              return_value=(None, None)),
        patch("xcelium_mcp.tb_provenance.build_tb_provenance", side_effect=fake_provenance),
    ):
        _log_str, _dump_stats, tb_provenance, _per_test_verdicts = await run_batch_regression(
            sim_dir="/sim",
            test_list=["T1", "T2"],
            runner=runner,
        )

    assert call_log == ["poll:T1", "provenance:T1", "poll:T2", "provenance:T2"]
    assert tb_provenance == {
        "T1": {"path": "/sim/tb/T1.sv", "sha256": "hash_T1"},
        "T2": {"path": "/sim/tb/T2.sv", "sha256": "hash_T2"},
    }


@pytest.mark.asyncio
async def test_shared_tb_file_edited_mid_regression_does_not_taint_earlier_test() -> None:
    """T1 and T2 share the same TB file. The file's content (and therefore
    its provenance checksum) changes between T1's run and T2's run — T1's
    recorded provenance must reflect the checksum at the time T1 actually
    ran, not the value the file had once T2 finished."""
    runner = _make_runner()
    # First call (T1's) sees the OLD checksum; second call (T2's) sees the
    # NEW one — simulating an edit that happened in between.
    provenance_sequence = [
        {"path": "/sim/tb/shared.sv", "sha256": "OLD_HASH"},
        {"path": "/sim/tb/shared.sv", "sha256": "NEW_HASH"},
    ]

    with (
        patch("xcelium_mcp.batch_runner.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
        patch("xcelium_mcp.batch_runner.shell_run", new_callable=AsyncMock, return_value=""),
        patch("xcelium_mcp.batch_runner.shell_run_fire_and_forget", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_runner.poll_batch_log", new_callable=AsyncMock,
              return_value=("", False)),
        patch("xcelium_mcp.batch_runner._preprocess_setup_tcl", new_callable=AsyncMock,
              return_value=(None, None)),
        patch("xcelium_mcp.tb_provenance.build_tb_provenance",
              side_effect=provenance_sequence),
    ):
        _log_str, _dump_stats, tb_provenance, _per_test_verdicts = await run_batch_regression(
            sim_dir="/sim",
            test_list=["T1", "T2"],
            runner=runner,
        )

    # If provenance had been batched at the end (the pre-fix behavior), both
    # entries would have been computed from whatever the file looked like
    # after T2 finished — both would show NEW_HASH, silently mislabeling T1.
    assert tb_provenance["T1"]["sha256"] == "OLD_HASH"
    assert tb_provenance["T2"]["sha256"] == "NEW_HASH"
