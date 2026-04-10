"""Tests for csv_cache.bisect_csv and bisect_signal_dump."""
from __future__ import annotations

import csv
import os
import tempfile

import pytest

from xcelium_mcp.csv_cache import bisect_csv


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
