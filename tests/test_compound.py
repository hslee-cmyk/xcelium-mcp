"""Tests for compound.py — Layer 3 compound operations (Phase A).

Verifies CompoundResult mapping and, critically, that batch_runner/csv_cache
functions are called rather than reimplemented (Plan §3.4 "조합 우선" —
this is the regression that guards against RISK: 617 existing tests staying
independent of any new bug surface introduced here).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import xcelium_mcp.compound as compound
from xcelium_mcp.compound import (
    CompoundResult,
    analyze_waveform,
    regression_summary,
    run_and_check,
)

RUNNER = {"script": "run.sh", "login_shell": "/bin/csh"}


# ---------------------------------------------------------------------------
# run_and_check
# ---------------------------------------------------------------------------

class TestRunAndCheck:
    @pytest.mark.asyncio
    async def test_pass_case(self):
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            return_value=("COMPLETE. Errors: 0", None),
        ) as mock_run, patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
            return_value="/sim/dump/TOP015.shm",
        ):
            result = await run_and_check(sim_dir="/sim", test_name="TOP015", runner=RUNNER)

        assert isinstance(result, CompoundResult)
        assert result.status == "PASS"
        assert result.dump_path == "/sim/dump/TOP015.shm"
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_fail_case(self):
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            return_value=("COMPLETE. Errors: 3", None),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock, return_value="",
        ):
            result = await run_and_check(sim_dir="/sim", test_name="TOP015", runner=RUNNER)

        assert result.status == "FAIL"

    @pytest.mark.asyncio
    async def test_error_case_exception_from_batch_runner(self):
        """EDA env misconfiguration etc. — run_batch_single raises."""
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            side_effect=RuntimeError("EDA env not configured"),
        ):
            result = await run_and_check(sim_dir="/sim", test_name="TOP015", runner=RUNNER)

        assert result.status == "ERROR"
        assert "EDA env not configured" in result.log_summary

    @pytest.mark.asyncio
    async def test_csv_mode_range_calls_extract(self):
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            return_value=("COMPLETE. Errors: 0", None),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
            return_value="/sim/dump/TOP015.shm",
        ), patch.object(
            compound.csv_cache,
            "extract", new_callable=AsyncMock, return_value="/tmp/out.csv",
        ) as mock_extract:
            result = await run_and_check(
                sim_dir="/sim", test_name="TOP015", runner=RUNNER,
                csv_signals=["dut.sig_a"],
            )

        assert result.csv_path == "/tmp/out.csv"
        mock_extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_csv_mode_bisect_requires_find_condition(self):
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            return_value=("COMPLETE. Errors: 0", None),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
            return_value="/sim/dump/TOP015.shm",
        ):
            result = await run_and_check(
                sim_dir="/sim", test_name="TOP015", runner=RUNNER,
                csv_signals=["dut.sig_a"], csv_mode="bisect",
            )

        assert "csv_error" in result.details
        assert "find_condition is required" in result.details["csv_error"]

    @pytest.mark.asyncio
    async def test_csv_extraction_failure_does_not_downgrade_status(self):
        """A PASS run stays PASS even if the follow-up CSV extraction fails."""
        with patch(
            "xcelium_mcp.compound.run_batch_single", new_callable=AsyncMock,
            return_value=("COMPLETE. Errors: 0", None),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
            return_value="/sim/dump/TOP015.shm",
        ), patch.object(
            compound.csv_cache,
            "extract", new_callable=AsyncMock, side_effect=RuntimeError("simvisdbutil failed"),
        ):
            result = await run_and_check(
                sim_dir="/sim", test_name="TOP015", runner=RUNNER,
                csv_signals=["dut.sig_a"],
            )

        assert result.status == "PASS"
        assert "csv_error" in result.details


# ---------------------------------------------------------------------------
# _classify_status — F-186 (legacy directed-test "[TAG] ...!!" verdict-line
# convention was silently ignored, letting internal check failures be
# classified PASS purely because $finish was reached)
#
# Rev.2: the first version of this fix only matched the literal substring
# "failed!!" and shipped/deployed to cloud0 as such -- re-verifying against
# the REAL log line that actually motivated F-186 (TID_TOP010_register_bank_
# test's bounded-timeout guard, "[REG BANK TEST] register 0x05: back-tel NOT
# sent (timeout)!!") found that it does NOT contain "failed!!" and was still
# misclassified PASS even after that first fix. `_TB_BANG_LINE_RE` replaces
# the literal substring check with the actual TB convention (bracketed [TAG]
# line + "!!", not "passed") so this and any future similarly-worded verdict
# line in the same convention family is caught without needing to enumerate
# every possible wording.
# ---------------------------------------------------------------------------

class TestClassifyStatus:
    def test_real_timeout_guard_message_is_fail(self):
        """The exact real-world case that originally motivated F-186 (verilog-
        tb-reviewer flagged it during TID_TOP010's Fix Sub-cycle): the TB's
        bounded-timeout guard prints this exact line, with no "failed" or
        "passed" in it at all -- only the "[TAG] ...!!" convention marks it as
        a verdict line. A literal "failed!!" substring check (this fix's first,
        insufficient attempt) misses this case entirely."""
        log = (
            "[REG BANK TEST] register 0x05: back-tel NOT sent (timeout)!!\n"
            "Simulation complete via $finish(1) at time 48133520 NS + 0"
        )
        assert compound._classify_status(log) == "FAIL"

    def test_lowercase_failed_marker_is_fail(self):
        """No COMPLETE./no uppercase FAIL, but a legacy TB's own 'failed!!'
        verdict line and a normal $finish — this used to classify PASS,
        silently discarding the TB's own failure."""
        log = (
            "[REG BANK TEST] register 0x03: failed!!, mask value: 0xff, "
            "write data: 0xfa, read data: 0x00\n"
            "Simulation complete via $finish(1) at time 3432520 NS + 0"
        )
        assert compound._classify_status(log) == "FAIL"

    def test_lowercase_passed_only_still_pass(self):
        """No regression: a clean legacy TB log with only 'passed!!' lines and
        a $finish stays PASS."""
        log = (
            "[REG BANK TEST] register 0x03: passed!!, mask value: 0xff, "
            "write data: 0xfa, read data: 0xfa\n"
            "Simulation complete via $finish(1) at time 3432520 NS + 0"
        )
        assert compound._classify_status(log) == "PASS"

    def test_uppercase_fail_still_takes_priority(self):
        """No regression: the existing uppercase 'FAIL' path is untouched and
        still wins regardless of any lowercase 'passed!!' also present."""
        log = "[REG BANK TEST] register 0x03: passed!!\nFAIL: assertion violated"
        assert compound._classify_status(log) == "FAIL"

    def test_complete_errors_marker_still_takes_priority(self):
        """No regression: the UVM COMPLETE./Errors: N marker is checked first
        and still wins even if a stray 'failed!!'-like phrase also appears
        outside of the bracketed [TAG] convention."""
        log = "some noise mentioning failed!! in passing\nCOMPLETE. Errors: 0"
        assert compound._classify_status(log) == "PASS"

    def test_unrelated_bang_noise_without_bracket_tag_does_not_misfire(self):
        """No false positive: '!!' appearing outside the bracketed [TAG] line
        convention (unrelated log noise) must not be treated as a verdict."""
        log = "wow!! that was fast\nSimulation complete via $finish(1) at time 100 NS + 0"
        assert compound._classify_status(log) == "PASS"


# ---------------------------------------------------------------------------
# analyze_waveform
# ---------------------------------------------------------------------------

class TestAnalyzeWaveform:
    @pytest.mark.asyncio
    async def test_multi_condition_search(self):
        with patch.object(
            compound.csv_cache,
            "extract", new_callable=AsyncMock, return_value="/tmp/out.csv",
        ) as mock_extract, patch.object(
            compound.csv_cache,
            "bisect_signal_dump", new_callable=AsyncMock,
            side_effect=["match A", "match B"],
        ) as mock_bisect:
            result = await analyze_waveform(
                dump_path="/sim/dump/TOP015.shm",
                signals=["dut.sig_a", "dut.sig_b"],
                find_conditions=[
                    {"signal": "dut.sig_a", "op": "eq", "value": "1"},
                    {"signal": "dut.sig_b", "op": "change", "value": ""},
                ],
            )

        assert result.status == "PASS"
        assert result.details["conditions"] == ["match A", "match B"]
        mock_extract.assert_called_once()
        assert mock_bisect.call_count == 2

    @pytest.mark.asyncio
    async def test_extract_failure_is_error(self):
        with patch.object(
            compound.csv_cache,
            "extract", new_callable=AsyncMock, side_effect=RuntimeError("no such SHM"),
        ):
            result = await analyze_waveform(dump_path="/sim/dump/bad.shm", signals=["dut.sig_a"])

        assert result.status == "ERROR"
        assert "no such SHM" in result.log_summary


# ---------------------------------------------------------------------------
# regression_summary
# ---------------------------------------------------------------------------

class TestRegressionSummary:
    @pytest.mark.asyncio
    async def test_partial_failure(self):
        summary_text = "1/2 verdict tests PASS (5 checks passed, 1 failed)\n\nLog (...): ..."
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            return_value=(summary_text, None, {"T1": {}, "T2": {}}),
        ) as mock_regr:
            result = await regression_summary(
                sim_dir="/sim", test_list=["T1", "T2"], runner=RUNNER,
            )

        assert result.status == "PARTIAL"
        assert result.details["tb_provenance"] == {"T1": {}, "T2": {}}
        mock_regr.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_pass(self):
        summary_text = "2/2 verdict tests PASS (10 checks passed, 0 failed)"
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            return_value=(summary_text, None, {}),
        ):
            result = await regression_summary(sim_dir="/sim", test_list=["T1", "T2"], runner=RUNNER)

        assert result.status == "PASS"

    @pytest.mark.asyncio
    async def test_all_fail(self):
        summary_text = "0/2 verdict tests PASS (0 checks passed, 4 failed)"
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            return_value=(summary_text, None, {}),
        ):
            result = await regression_summary(sim_dir="/sim", test_list=["T1", "T2"], runner=RUNNER)

        assert result.status == "FAIL"

    @pytest.mark.asyncio
    async def test_error_case_exception(self):
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            side_effect=RuntimeError("ssh connection lost"),
        ):
            result = await regression_summary(sim_dir="/sim", test_list=["T1"], runner=RUNNER)

        assert result.status == "ERROR"

    @pytest.mark.asyncio
    async def test_csv_on_fail_extracts_for_every_test_in_list(self):
        """Documented module-1 simplification: csv_on_fail extracts for the
        whole test_list (not pinpointed failures) when overall status != PASS."""
        summary_text = "1/2 verdict tests PASS (5 checks passed, 1 failed)"
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            return_value=(summary_text, None, {}),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
            side_effect=lambda sim_dir, tn: f"/sim/dump/{tn}.shm",
        ), patch.object(
            compound.csv_cache,
            "extract", new_callable=AsyncMock,
            side_effect=lambda shm_path, signals: f"{shm_path}.csv",
        ):
            result = await regression_summary(
                sim_dir="/sim", test_list=["T1", "T2"], runner=RUNNER,
                csv_on_fail=True, csv_signals=["dut.sig_a"],
            )

        assert result.details["csv_by_test"] == {
            "T1": "/sim/dump/T1.shm.csv",
            "T2": "/sim/dump/T2.shm.csv",
        }

    @pytest.mark.asyncio
    async def test_csv_on_fail_skipped_when_all_pass(self):
        summary_text = "2/2 verdict tests PASS (10 checks passed, 0 failed)"
        with patch(
            "xcelium_mcp.compound.run_batch_regression", new_callable=AsyncMock,
            return_value=(summary_text, None, {}),
        ), patch(
            "xcelium_mcp.compound.find_shm", new_callable=AsyncMock,
        ) as mock_find_shm:
            result = await regression_summary(
                sim_dir="/sim", test_list=["T1", "T2"], runner=RUNNER,
                csv_on_fail=True, csv_signals=["dut.sig_a"],
            )

        assert "csv_by_test" not in result.details
        mock_find_shm.assert_not_called()


# ---------------------------------------------------------------------------
# CompoundResult formatting
# ---------------------------------------------------------------------------

class TestCompoundResultFormatting:
    def test_to_cli_output_format(self):
        r = CompoundResult(status="PASS", log_summary="COMPLETE. Errors: 0\nmore lines",
                            dump_path="/sim/dump/T.shm", csv_path="/tmp/out.csv")
        out = r.to_cli_output()
        assert out.split("\n") == [
            "[LOG] COMPLETE. Errors: 0",
            "[DUMP] /sim/dump/T.shm",
            "[CSV] /tmp/out.csv",
            "[RESULT] PASS",
        ]

    def test_to_mcp_output_includes_details(self):
        r = CompoundResult(status="FAIL", log_summary="COMPLETE. Errors: 1",
                            details={"foo": "bar"})
        out = r.to_mcp_output()
        assert "status: FAIL" in out
        assert '"foo": "bar"' in out
