"""Direct unit tests for classify_regression_results / aggregate_dump_stats (F-155).

F-155: these were previously inlined in run_batch_regression's tail (~85 lines
of pure logic buried inside a 400-line I/O orchestrator) and could only be
exercised by mocking the entire regression pipeline (shell_run, poll_batch_log,
_preprocess_setup_tcl, etc. — see tests/test_regression_result_collection.py
and tests/test_dump_history_stats.py). Now extracted as pure functions, they
can be tested directly with plain dicts/lists — no mocking at all.
"""
from __future__ import annotations

from xcelium_mcp.batch_runner import aggregate_dump_stats, classify_regression_results


class TestClassifyRegressionResults:
    def test_has_verdict_pass(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["COMPLETE. Errors: 0"]},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "1/1 verdict tests PASS" in log_str

    def test_has_verdict_fail_via_complete_nonzero(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["COMPLETE. Errors: 2"]},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "0/1 verdict tests PASS" in log_str

    def test_has_verdict_fail_via_fail_without_complete(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["[V-01] FAIL: assertion"]},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "0/1 verdict tests PASS" in log_str

    def test_no_verdict_finish_no_errors_is_complete(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["$finish called at 1000ns"]},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "1/1 waveform tests COMPLETE" in log_str

    def test_no_verdict_finish_with_real_tb_bang_line_is_error(self):
        """F-191: the exact real message that motivated this fix (and F-186
        before it) -- TID_TOP010_register_bank_test's bounded-timeout guard.
        No COMPLETE./no uppercase FAIL/no separate error-grep line, $finish
        reached -- this used to be silently counted as "complete" (waveform
        tests COMPLETE) in a regression run."""
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": [
                "[REG BANK TEST] register 0x05: back-tel NOT sent (timeout)!!",
                "Simulation complete via $finish(1) at time 48133520 NS + 0",
            ]},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "0/1 waveform tests COMPLETE" in log_str

    def test_no_verdict_finish_with_errors_is_error(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["$finish called at 1000ns"]},
            per_test_errors={"T1": "*E fatal error"},
            log_file="/tmp/regression.log",
        )
        assert "0/1 waveform tests COMPLETE" in log_str

    def test_no_verdict_no_finish_is_error_timeout(self):
        """No verdict + no $finish -> error bucket, which still counts toward
        waveform_total (error_count contributes there too)."""
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": []},
            per_test_errors={"T1": ""},
            log_file="/tmp/regression.log",
        )
        assert "0/1 waveform tests COMPLETE" in log_str

    def test_all_categories_absent_shows_fallback(self):
        """The '0/N tests classified' fallback only fires when there is
        nothing at all to summarize (empty test_list)."""
        log_str = classify_regression_results(
            test_list=[],
            per_test_results={},
            per_test_errors={},
            log_file="/tmp/regression.log",
        )
        assert "0/0 tests classified" in log_str

    def test_mixed_regression_all_five_categories(self):
        """2 HAS_VERDICT + 3 NO_VERDICT, matching the classic mixed scenario."""
        test_list = ["pass1", "fail1", "complete1", "error_finish1", "error_timeout1"]
        per_test_results = {
            "pass1": ["COMPLETE. Errors: 0", "[V-01] PASS: check"],
            "fail1": ["COMPLETE. Errors: 1", "[V-02] FAIL: check"],
            "complete1": ["$finish called"],
            "error_finish1": ["$finish called"],
            "error_timeout1": ["Running simulation..."],
        }
        per_test_errors = {
            "pass1": "", "fail1": "", "complete1": "",
            "error_finish1": "*E fatal", "error_timeout1": "",
        }
        log_str = classify_regression_results(
            test_list, per_test_results, per_test_errors, "/tmp/regression.log"
        )
        assert "1/2 verdict tests PASS" in log_str
        # waveform_total = complete_count(1) + error_count(2: error_finish1, error_timeout1) = 3
        assert "1/3 waveform tests COMPLETE" in log_str
        assert "=== pass1 ===" in log_str
        assert "=== error_timeout1 ===" in log_str

    def test_log_file_path_included_in_output(self):
        log_str = classify_regression_results(
            test_list=["T1"],
            per_test_results={"T1": ["COMPLETE. Errors: 0"]},
            per_test_errors={"T1": ""},
            log_file="/tmp/mcp/regression_12345.log",
        )
        assert "/tmp/mcp/regression_12345.log" in log_str

    def test_empty_log_shows_placeholder(self):
        """The placeholder only fires when there's truly nothing to show —
        even a test with no result lines still emits an '=== T1 ===' header,
        so this requires an empty test_list."""
        log_str = classify_regression_results(
            test_list=[],
            per_test_results={},
            per_test_errors={},
            log_file="/tmp/regression.log",
        )
        assert "no PASS/FAIL/$finish lines found" in log_str


class TestAggregateDumpStats:
    def test_empty_input_returns_none(self):
        assert aggregate_dump_stats({}) is None

    def test_single_test_shape(self):
        stats = aggregate_dump_stats({
            "T1": {
                "total_signals": 28,
                "top_boundary_count": 2,
                "block_boundaries": {"top.u_blk_a": 2, "top.u_blk_b": 0},
            },
        })
        assert stats["per_test"]["T1"] == {"total": 28, "top_boundary": 2, "block_count": 1}
        assert stats["max"] == {"test": "T1", "total": 28}
        assert stats["min"] == {"test": "T1", "total": 28}
        assert stats["suggestions"] == []

    def test_outlier_triggers_named_suggestion(self):
        """T3=50 is > avg(21.67)*2=43.33, so only T3 gets a suggestion."""
        stats = aggregate_dump_stats({
            "T1": {"total_signals": 10, "top_boundary_count": 2, "block_boundaries": {}},
            "T2": {"total_signals": 5, "top_boundary_count": 2, "block_boundaries": {}},
            "T3": {"total_signals": 50, "top_boundary_count": 2, "block_boundaries": {"a": 2}},
        })
        assert stats["max"] == {"test": "T3", "total": 50}
        assert stats["min"] == {"test": "T2", "total": 5}
        assert len(stats["suggestions"]) == 1
        assert "T3" in stats["suggestions"][0]

    def test_no_outlier_no_suggestions(self):
        stats = aggregate_dump_stats({
            "T1": {"total_signals": 10, "top_boundary_count": 2, "block_boundaries": {}},
            "T2": {"total_signals": 12, "top_boundary_count": 2, "block_boundaries": {}},
        })
        assert stats["suggestions"] == []

    def test_block_count_only_counts_nonzero_blocks(self):
        stats = aggregate_dump_stats({
            "T1": {
                "total_signals": 10,
                "top_boundary_count": 2,
                "block_boundaries": {"a": 3, "b": 0, "c": 5, "d": 0},
            },
        })
        assert stats["per_test"]["T1"]["block_count"] == 2
