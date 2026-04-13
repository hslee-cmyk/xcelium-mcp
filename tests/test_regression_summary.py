"""Tests for sim_regression result counting logic (F-085, F-086).

Tests _COMPLETE_RE pattern and the dual-level summary behavior.
"""
from __future__ import annotations

import pytest

from xcelium_mcp.batch_runner import _COMPLETE_RE


# ---------------------------------------------------------------------------
# F-085: _COMPLETE_RE parses COMPLETE. Errors: N verdict lines
# ---------------------------------------------------------------------------

class TestCompleteRe:
    def test_matches_errors_zero(self):
        m = _COMPLETE_RE.search("COMPLETE. Errors: 0")
        assert m is not None
        assert int(m.group(1)) == 0

    def test_matches_errors_nonzero(self):
        m = _COMPLETE_RE.search("COMPLETE. Errors: 3")
        assert m is not None
        assert int(m.group(1)) == 3

    def test_matches_with_extra_spaces(self):
        m = _COMPLETE_RE.search("COMPLETE.  Errors:  0")
        assert m is not None
        assert int(m.group(1)) == 0

    def test_matches_embedded_in_log_line(self):
        line = "UVM_INFO @ 1000ns: COMPLETE. Errors: 0 UVM_WARNING: 1"
        assert _COMPLETE_RE.search(line) is not None

    def test_no_match_plain_pass(self):
        assert _COMPLETE_RE.search("[V-01] PASS: clock check") is None

    def test_no_match_plain_fail(self):
        assert _COMPLETE_RE.search("[V-02] FAIL: expected 1 got 0") is None

    def test_no_match_partial(self):
        assert _COMPLETE_RE.search("COMPLETE without errors section") is None


# ---------------------------------------------------------------------------
# F-085: summary format — test-level vs check-level counts
# ---------------------------------------------------------------------------

def _make_log(complete_errors: int | None, pass_lines: int, fail_lines: int) -> str:
    """Build a fake test log fragment."""
    lines = []
    for i in range(pass_lines):
        lines.append(f"[V-{i:02}] PASS: check {i}")
    for i in range(fail_lines):
        lines.append(f"[V-{i:02}] FAIL: check {i}")
    if complete_errors is not None:
        lines.append(f"COMPLETE. Errors: {complete_errors}")
    return "\n".join(lines)


class TestSummaryLogic:
    """Simulate the batch_runner aggregation loop in isolation."""

    def _aggregate(self, logs: list[str]) -> dict:
        """Replicate the F-085 aggregation logic for testing."""
        pass_count = 0
        fail_count = 0
        check_pass = 0
        check_fail = 0
        for t_raw in logs:
            m = _COMPLETE_RE.search(t_raw)
            if m:
                if int(m.group(1)) == 0:
                    pass_count += 1
                else:
                    fail_count += 1
            elif "FAIL" in t_raw:
                fail_count += 1
            check_pass += t_raw.count("PASS")
            check_fail += t_raw.count("FAIL")
        return {
            "pass_count": pass_count,
            "fail_count": fail_count,
            "check_pass": check_pass,
            "check_fail": check_fail,
        }

    def test_two_passing_tests_with_many_checks(self):
        """2 tests × 12+3 PASS checks → pass_count=2 not 15."""
        log1 = _make_log(complete_errors=0, pass_lines=12, fail_lines=0)
        log2 = _make_log(complete_errors=0, pass_lines=3, fail_lines=0)
        r = self._aggregate([log1, log2])
        assert r["pass_count"] == 2
        assert r["fail_count"] == 0
        assert r["check_pass"] == 15
        assert r["check_fail"] == 0

    def test_one_pass_one_fail_test(self):
        log_pass = _make_log(complete_errors=0, pass_lines=5, fail_lines=0)
        log_fail = _make_log(complete_errors=2, pass_lines=3, fail_lines=1)
        r = self._aggregate([log_pass, log_fail])
        assert r["pass_count"] == 1
        assert r["fail_count"] == 1

    def test_crash_no_complete_line(self):
        """FAIL present but no COMPLETE → crash/abort → fail_count+1."""
        log = "[ERROR] Segfault\n[V-00] FAIL: fatal"
        r = self._aggregate([log])
        assert r["pass_count"] == 0
        assert r["fail_count"] == 1
