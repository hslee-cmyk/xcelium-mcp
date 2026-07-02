"""Tests for simvision_ops._load_rows and compare_csv_diff (F-145).

F-145: _load_rows() independently re-implemented the same SimTime -> int
parsing that csv_cache.bisect_csv() had (F-144) — both crashed on decimal
SimTime. These tests cover the fix, which now reuses csv_cache._parse_sim_time_ns.
"""
from __future__ import annotations

import csv

import pytest

from xcelium_mcp.simvision_ops import _load_rows, compare_csv_diff


def _write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class TestLoadRows:
    def test_integer_simtime(self, tmp_path):
        p = tmp_path / "int_time.csv"
        _write_csv([
            {"SimTime": "0", "sig_a": "0"},
            {"SimTime": "10", "sig_a": "1"},
        ], str(p))
        rows = _load_rows(str(p))
        assert set(rows.keys()) == {0, 10}
        assert rows[10]["sig_a"] == "1"

    def test_decimal_simtime_does_not_crash(self, tmp_path):
        """Before the fix, int(raw_time) raised an uncaught ValueError here."""
        p = tmp_path / "decimal_time.csv"
        _write_csv([
            {"SimTime": "0.0", "sig_a": "0"},
            {"SimTime": "10.5", "sig_a": "1"},
            {"SimTime": "20.25", "sig_a": "0"},
        ], str(p))
        rows = _load_rows(str(p))
        assert set(rows.keys()) == {0, 10, 20}  # rounded to nearest ns
        assert rows[10]["sig_a"] == "1"

    def test_decimal_and_integer_simtime_key_types_match(self, tmp_path):
        """Both int-typed and decimal-typed SimTime CSVs key by plain int —
        so set(rows_b) | set(rows_a) in compare_csv_diff never mismatches."""
        p_int = tmp_path / "int.csv"
        p_dec = tmp_path / "dec.csv"
        _write_csv([{"SimTime": "10", "sig_a": "1"}], str(p_int))
        _write_csv([{"SimTime": "10.0", "sig_a": "1"}], str(p_dec))
        rows_int = _load_rows(str(p_int))
        rows_dec = _load_rows(str(p_dec))
        assert set(rows_int.keys()) == set(rows_dec.keys()) == {10}


class _FakeCsvCache:
    """Duck-typed csv_cache stand-in — extract() returns pre-written CSV paths."""

    def __init__(self, before_path: str, after_path: str):
        self._before = before_path
        self._after = after_path

    async def extract(self, shm_path, signals, start_ns, end_ns, missing_ok=True):
        return self._before if shm_path == "before.shm" else self._after


class TestCompareCsvDiff:
    @pytest.mark.asyncio
    async def test_decimal_simtime_does_not_crash(self, tmp_path):
        """Before the fix, this would raise ValueError deep inside _load_rows,
        surfacing as an unhandled exception rather than a tool error message."""
        before = tmp_path / "before.csv"
        after = tmp_path / "after.csv"
        _write_csv([
            {"SimTime": "0.0", "v_out": "0.0"},
            {"SimTime": "10.5", "v_out": "1.65"},
        ], str(before))
        _write_csv([
            {"SimTime": "0.0", "v_out": "0.0"},
            {"SimTime": "10.5", "v_out": "3.3"},
        ], str(after))

        result = await compare_csv_diff(
            csv_cache=_FakeCsvCache(str(before), str(after)),
            shm_before="before.shm",
            shm_after="after.shm",
            signals=["v_out"],
            start_ns=0,
            end_ns=0,
        )
        assert "1 signal(s) changed" in result
        assert "Time 10ns" in result  # 10.5 rounded to nearest ns
