"""Tests for sim_regression result counting logic (F-085, F-086)
and run_full_discovery registry-first ordering (F-087).

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


# ---------------------------------------------------------------------------
# F-086: NO_VERDICT ($finish-only) classification
# ---------------------------------------------------------------------------


class TestNoVerdictClassification:
    """Simulate 5-way classification logic for NO_VERDICT tests."""

    def _classify(self, t_raw: str, err_raw: str) -> str:
        """Returns 'pass'/'fail'/'complete'/'error' per the 5-way logic."""
        from xcelium_mcp.batch_runner import _COMPLETE_RE
        m = _COMPLETE_RE.search(t_raw)
        if m:
            return "pass" if int(m.group(1)) == 0 else "fail"
        if "FAIL" in t_raw:
            return "fail"
        if "$finish" in t_raw:
            return "error" if err_raw.strip() else "complete"
        return "error"

    def test_finish_no_errors_is_complete(self):
        assert self._classify("$finish called at 1000ns", "") == "complete"

    def test_finish_with_errors_is_error(self):
        assert self._classify("$finish called at 1000ns", "*E some error") == "error"

    def test_no_finish_is_error(self):
        """Timeout or crash — no $finish, no COMPLETE."""
        assert self._classify("Running simulation...", "") == "error"

    def test_has_verdict_complete_zero_is_pass(self):
        assert self._classify("COMPLETE. Errors: 0", "") == "pass"

    def test_has_verdict_complete_nonzero_is_fail(self):
        assert self._classify("COMPLETE. Errors: 2", "") == "fail"

    def test_mixed_regression_summary_lines(self):
        """2 HAS_VERDICT + 3 NO_VERDICT → correct summary counts."""
        logs = [
            ("COMPLETE. Errors: 0\n[V-01] PASS: check", ""),   # pass
            ("COMPLETE. Errors: 1\n[V-02] FAIL: check", ""),   # fail
            ("$finish called", ""),                              # complete
            ("$finish called", "*E fatal"),                     # error
            ("Running...", ""),                                  # error (timeout)
        ]
        pass_count = fail_count = complete_count = error_count = 0
        for t_raw, err_raw in logs:
            cls = self._classify(t_raw, err_raw)
            if cls == "pass":
                pass_count += 1
            elif cls == "fail":
                fail_count += 1
            elif cls == "complete":
                complete_count += 1
            else:
                error_count += 1
        assert pass_count == 1
        assert fail_count == 1
        assert complete_count == 1
        assert error_count == 2


# ---------------------------------------------------------------------------
# F-087: run_full_discovery checks registry before discover_sim_dir()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_full_discovery_uses_registry_default_sim_dir() -> None:
    """When registry has a default sim_dir, discover_sim_dir() must NOT be called."""
    from unittest.mock import AsyncMock, patch

    fake_config = {"version": 2, "sim_dir": "/remote/sim"}

    with patch("xcelium_mcp.discovery.get_default_sim_dir", new_callable=AsyncMock,
               return_value="/remote/sim") as mock_default, \
         patch("xcelium_mcp.discovery.discover_sim_dir", new_callable=AsyncMock) as mock_discover, \
         patch("xcelium_mcp.discovery.load_sim_config", new_callable=AsyncMock,
               return_value=fake_config):
        from xcelium_mcp.discovery import run_full_discovery
        result = await run_full_discovery(sim_dir="", force=False)

    mock_default.assert_called_once()
    mock_discover.assert_not_called()  # registry hit → no CWD scan
    assert "Registry already exists" in result


@pytest.mark.asyncio
async def test_run_full_discovery_falls_back_to_discover_when_no_registry() -> None:
    """When registry has no default sim_dir, discover_sim_dir() is called."""
    from unittest.mock import AsyncMock, patch

    with patch("xcelium_mcp.discovery.get_default_sim_dir", new_callable=AsyncMock,
               return_value="") as mock_default, \
         patch("xcelium_mcp.discovery.discover_sim_dir", new_callable=AsyncMock,
               return_value=[{"sim_dir": "/detected/sim"}]) as mock_discover, \
         patch("xcelium_mcp.discovery.load_sim_config", new_callable=AsyncMock,
               return_value={"version": 2}):
        from xcelium_mcp.discovery import run_full_discovery
        await run_full_discovery(sim_dir="", force=False)

    mock_default.assert_called_once()
    mock_discover.assert_called_once()  # no registry → CWD scan performed
