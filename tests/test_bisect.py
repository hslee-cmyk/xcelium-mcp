"""Tests for csv_cache.bisect_csv and bisect_signal_dump."""
from __future__ import annotations

import csv

import pytest

from xcelium_mcp.csv_cache import _parse_sim_time_ns, _to_number, bisect_csv


def _write_csv(rows: list[dict], path: str) -> None:
    if not rows:
        with open(path, "w") as f:
            f.write("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def simple_csv(tmp_path):
    """CSV with one signal 'sig_a', values 0→0→1→1→0."""
    rows = [
        {"SimTime": "0",   "sig_a": "0"},
        {"SimTime": "10",  "sig_a": "0"},
        {"SimTime": "20",  "sig_a": "1"},
        {"SimTime": "30",  "sig_a": "1"},
        {"SimTime": "40",  "sig_a": "0"},
    ]
    p = tmp_path / "test.csv"
    _write_csv(rows, str(p))
    return str(p)


class TestBisectCsvEq:
    def test_eq_finds_first_match(self, simple_csv):
        result = bisect_csv(simple_csv, "sig_a", "eq", "1")
        assert result["found"] is True
        assert result["match_time_ns"] == 20
        assert result["match_value"] == "1"

    def test_eq_no_match(self, simple_csv):
        result = bisect_csv(simple_csv, "sig_a", "eq", "99")
        assert result["found"] is False

    def test_eq_with_range(self, simple_csv):
        # range excludes the first match at t=20
        result = bisect_csv(simple_csv, "sig_a", "eq", "1", start_ns=25)
        assert result["found"] is True
        assert result["match_time_ns"] == 30


class TestBisectCsvChange:
    def test_change_detects_first_transition(self, simple_csv):
        result = bisect_csv(simple_csv, "sig_a", "change", "")
        assert result["found"] is True
        assert result["match_time_ns"] == 20  # 0→1


class TestBisectCsvEdgeCases:
    def test_missing_signal_column(self, simple_csv):
        result = bisect_csv(simple_csv, "nonexistent_sig", "eq", "1")
        assert result["found"] is False

    def test_empty_csv(self, tmp_path):
        p = tmp_path / "empty.csv"
        _write_csv([], str(p))
        result = bisect_csv(str(p), "sig_a", "eq", "1")
        assert result["found"] is False

    def test_context_rows_included(self, simple_csv):
        result = bisect_csv(simple_csv, "sig_a", "eq", "1", context_rows=1)
        assert result["found"] is True
        # context should contain rows around the match
        assert isinstance(result["context"], list)
        assert len(result["context"]) >= 1


class TestBisectCsvNe:
    def test_ne_finds_first_nonzero(self, simple_csv):
        # First row where sig_a != "0" is t=20
        result = bisect_csv(simple_csv, "sig_a", "ne", "0")
        assert result["found"] is True
        assert result["match_time_ns"] == 20


# ---------------------------------------------------------------------------
# F-144: gt/lt numeric ops — previously had zero test coverage (any type)
# ---------------------------------------------------------------------------


@pytest.fixture()
def counter_csv(tmp_path):
    """CSV with an integer-valued counter signal: 0, 5, 10, 15, 20."""
    rows = [
        {"SimTime": "0",  "cnt": "0"},
        {"SimTime": "10", "cnt": "5"},
        {"SimTime": "20", "cnt": "10"},
        {"SimTime": "30", "cnt": "15"},
        {"SimTime": "40", "cnt": "20"},
    ]
    p = tmp_path / "counter.csv"
    _write_csv(rows, str(p))
    return str(p)


class TestBisectCsvGtLt:
    def test_gt_finds_first_above_threshold(self, counter_csv):
        result = bisect_csv(counter_csv, "cnt", "gt", "8")
        assert result["found"] is True
        assert result["match_time_ns"] == 20
        assert result["match_value"] == "10"

    def test_lt_finds_first_below_threshold(self, counter_csv):
        # First row where cnt < 12 is t=0 (cnt=0)
        result = bisect_csv(counter_csv, "cnt", "lt", "12")
        assert result["found"] is True
        assert result["match_time_ns"] == 0

    def test_gt_no_match(self, counter_csv):
        result = bisect_csv(counter_csv, "cnt", "gt", "999")
        assert result["found"] is False


# ---------------------------------------------------------------------------
# F-144: decimal/real signal values (AMS/analog) — _eval_condition numeric compare
# ---------------------------------------------------------------------------


@pytest.fixture()
def analog_csv(tmp_path):
    """CSV with a real-valued (wreal/analog) signal ramping 0.0 -> 3.3."""
    rows = [
        {"SimTime": "0",  "v_out": "0.0"},
        {"SimTime": "10", "v_out": "1.65"},
        {"SimTime": "20", "v_out": "3.3"},
        {"SimTime": "30", "v_out": "3.3"},
        {"SimTime": "40", "v_out": "0.0"},
    ]
    p = tmp_path / "analog.csv"
    _write_csv(rows, str(p))
    return str(p)


class TestBisectCsvDecimalValue:
    def test_eq_matches_decimal_value(self, analog_csv):
        result = bisect_csv(analog_csv, "v_out", "eq", "3.3")
        assert result["found"] is True
        assert result["match_time_ns"] == 20

    def test_ne_matches_decimal_value(self, analog_csv):
        result = bisect_csv(analog_csv, "v_out", "ne", "0.0")
        assert result["found"] is True
        assert result["match_time_ns"] == 10

    def test_gt_finds_first_above_decimal_threshold(self, analog_csv):
        """Before the fix, gt/lt silently fell back to string-compare-only
        (eq/ne), so decimal values always returned found=False for gt/lt."""
        result = bisect_csv(analog_csv, "v_out", "gt", "1.0")
        assert result["found"] is True
        assert result["match_time_ns"] == 10
        assert result["match_value"] == "1.65"

    def test_lt_finds_first_below_decimal_threshold(self, analog_csv):
        result = bisect_csv(analog_csv, "v_out", "lt", "2.0")
        assert result["found"] is True
        assert result["match_time_ns"] == 0

    def test_gt_scientific_notation_value(self, analog_csv):
        # 1.65 > 1.0e0 should numerically compare, not string-compare
        result = bisect_csv(analog_csv, "v_out", "gt", "1.0e0")
        assert result["found"] is True
        assert result["match_time_ns"] == 10


# ---------------------------------------------------------------------------
# F-144: decimal SimTime — bisect_csv must not crash on fractional timestamps
# ---------------------------------------------------------------------------


@pytest.fixture()
def decimal_time_csv(tmp_path):
    """CSV with fractional-ns SimTime values (as an AMS/analog dump might emit)."""
    rows = [
        {"SimTime": "0.0",   "sig_a": "0"},
        {"SimTime": "10.5",  "sig_a": "0"},
        {"SimTime": "20.25", "sig_a": "1"},
        {"SimTime": "30.75", "sig_a": "1"},
        {"SimTime": "40.0",  "sig_a": "0"},
    ]
    p = tmp_path / "decimal_time.csv"
    _write_csv(rows, str(p))
    return str(p)


class TestBisectCsvDecimalSimTime:
    def test_decimal_simtime_does_not_crash(self, decimal_time_csv):
        """Before the fix, int(raw_time) raised an uncaught ValueError here."""
        result = bisect_csv(decimal_time_csv, "sig_a", "eq", "1")
        assert result["found"] is True

    def test_decimal_simtime_rounds_to_nearest_ns(self, decimal_time_csv):
        result = bisect_csv(decimal_time_csv, "sig_a", "eq", "1")
        assert result["match_time_ns"] == 20  # round(20.25) == 20

    def test_decimal_simtime_with_context_rows(self, decimal_time_csv):
        """Suffix read-ahead loop also parses decimal SimTime (post-match rows)."""
        result = bisect_csv(decimal_time_csv, "sig_a", "eq", "1", context_rows=1)
        assert result["found"] is True
        assert len(result["context"]) >= 1


# ---------------------------------------------------------------------------
# F-144: unit tests for the parsing helpers directly
# ---------------------------------------------------------------------------


class TestParseSimTimeNs:
    def test_integer_string(self):
        assert _parse_sim_time_ns("1500") == 1500

    def test_decimal_string_rounds(self):
        assert _parse_sim_time_ns("1500.5") == round(1500.5)

    def test_decimal_string_rounds_down_fraction(self):
        assert _parse_sim_time_ns("20.25") == 20

    def test_scientific_notation(self):
        assert _parse_sim_time_ns("1.5e3") == 1500

    def test_unparseable_raises(self):
        with pytest.raises(ValueError):
            _parse_sim_time_ns("not_a_number")


class TestToNumber:
    def test_plain_int(self):
        assert _to_number("42") == 42

    def test_hex_literal(self):
        assert _to_number("0x1A") == 26

    def test_decimal_float(self):
        assert _to_number("3.3") == pytest.approx(3.3)

    def test_scientific_notation(self):
        assert _to_number("1.234e-05") == pytest.approx(1.234e-05)

    def test_tristate_returns_none(self):
        assert _to_number("x") is None
        assert _to_number("z") is None
