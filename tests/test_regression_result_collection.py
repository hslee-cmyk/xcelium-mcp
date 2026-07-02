"""Tests for run_batch_regression's per-test result collection (F-152).

F-152: the "Parse final results" loop ran 4 sequential shell_run round-trips
per test (test -f, ls -t fallback, grep x2). Since each test's log collection
is independent, it now runs concurrently via asyncio.gather. The main
correctness risk of that change is cross-test data mixing — this file proves
each test's PASS/FAIL result is still mapped to the correct test name.
"""
from __future__ import annotations

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


@pytest.mark.asyncio
async def test_per_test_results_not_mixed_up_under_concurrent_collection() -> None:
    """Each test's log content must map to that test's own name, not a sibling's,
    even though log collection for all tests now runs concurrently."""
    runner = _make_runner()
    # Distinct COMPLETE verdicts per test — if results were cross-contaminated,
    # the pass/fail counts below would not match.
    log_content = {
        "T1": "COMPLETE. Errors: 0",   # pass
        "T2": "COMPLETE. Errors: 2",   # fail
        "T3": "COMPLETE. Errors: 0",   # pass
    }

    async def fake_shell(cmd: str, **kwargs) -> str:
        if cmd.startswith("test -f"):
            return "Y"  # always use the current-ts log path (skip ls -t fallback)
        if "grep -E 'PASS|FAIL|Errors:|COMPLETE" in cmd:
            for tn, content in log_content.items():
                if f"_{tn}.log" in cmd:
                    return content
            return ""
        if "grep -iE" in cmd:
            return ""  # no error lines
        return ""

    with (
        patch("xcelium_mcp.batch_runner.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
        patch("xcelium_mcp.batch_runner.shell_run", side_effect=fake_shell),
        patch("xcelium_mcp.batch_runner.shell_run_fire_and_forget", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_runner.poll_batch_log", new_callable=AsyncMock,
              return_value=("", False)),
        patch("xcelium_mcp.batch_runner._preprocess_setup_tcl", new_callable=AsyncMock,
              return_value=(None, None)),
    ):
        log_str, _dump_stats = await run_batch_regression(
            sim_dir="/sim",
            test_list=["T1", "T2", "T3"],
            runner=runner,
        )

    assert "2/3 verdict tests PASS" in log_str  # T1, T3 pass; T2 fails
    # Confirm each test's own content appears next to its own header, not a sibling's.
    t1_section = log_str.split("=== T1 ===")[1].split("=== T2 ===")[0]
    t2_section = log_str.split("=== T2 ===")[1].split("=== T3 ===")[0]
    t3_section = log_str.split("=== T3 ===")[1]
    assert "Errors: 0" in t1_section
    assert "Errors: 2" in t2_section
    assert "Errors: 0" in t3_section


@pytest.mark.asyncio
async def test_missing_log_file_for_one_test_does_not_affect_others() -> None:
    """A test with no discoverable log (test -f -> N, ls -t -> empty) must return
    empty results for itself without breaking concurrent siblings."""
    runner = _make_runner()

    async def fake_shell(cmd: str, **kwargs) -> str:
        if cmd.startswith("test -f") and "_T2.log" in cmd:
            return "N"  # T2 has no log at the expected path
        if cmd.startswith("test -f"):
            return "Y"
        if cmd.startswith("ls -t") and "_T2.log" in cmd:
            return ""  # and no fallback log either
        if "grep -E 'PASS|FAIL|Errors:|COMPLETE" in cmd and "_T1.log" in cmd:
            return "COMPLETE. Errors: 0"
        if "grep -E 'PASS|FAIL|Errors:|COMPLETE" in cmd and "_T3.log" in cmd:
            return "COMPLETE. Errors: 0"
        return ""

    with (
        patch("xcelium_mcp.batch_runner.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
        patch("xcelium_mcp.batch_runner.shell_run", side_effect=fake_shell),
        patch("xcelium_mcp.batch_runner.shell_run_fire_and_forget", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_runner.poll_batch_log", new_callable=AsyncMock,
              return_value=("", False)),
        patch("xcelium_mcp.batch_runner._preprocess_setup_tcl", new_callable=AsyncMock,
              return_value=(None, None)),
    ):
        log_str, _dump_stats = await run_batch_regression(
            sim_dir="/sim",
            test_list=["T1", "T2", "T3"],
            runner=runner,
        )

    # T1 and T3 still classified correctly despite T2 having no log.
    assert "2/2 verdict tests PASS" in log_str
    t2_section = log_str.split("=== T2 ===")[1].split("=== T3 ===")[0]
    assert t2_section.strip() == ""
