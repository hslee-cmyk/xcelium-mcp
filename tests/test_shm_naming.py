"""Tests for _replace_shm_stems() — SHM path preprocessing.

Imports the real function from xcelium_mcp.tcl_preprocessing.
(Previous version inlined a copy — see test_dump_strategy.py header.)
"""
from __future__ import annotations

from xcelium_mcp.tcl_preprocessing import _replace_shm_stems


def test_replace_all_three_contexts() -> None:
    """database -open, probe -database, database -close all replaced."""
    content = (
        "database -open ../dump/ci_top.shm -shm\n"
        "probe -create top -unpacked 100 -database ../dump/ci_top.shm -depth all\n"
        "run\n"
        "database -close ../dump/ci_top.shm\n"
        "exit\n"
    )
    result = _replace_shm_stems(content, "VENEZIA_TOP015")
    assert "ci_top.shm" not in result
    assert result.count("ci_top_VENEZIA_TOP015.shm") == 3


def test_already_tagged_skips() -> None:
    """If stem already contains test_name, no double-tagging."""
    content = (
        "database -open ../dump/ci_top_VENEZIA_TOP015.shm -shm\n"
        "probe -create top -database ../dump/ci_top_VENEZIA_TOP015.shm -depth all\n"
        "database -close ../dump/ci_top_VENEZIA_TOP015.shm\n"
    )
    result = _replace_shm_stems(content, "VENEZIA_TOP015")
    assert result == content


def test_no_database_open_no_replacement() -> None:
    """Without database -open, stems are not discovered — no replacement."""
    content = "probe -create top -database ../dump/ci_top.shm\n"
    result = _replace_shm_stems(content, "VENEZIA_TOP015")
    assert result == content


def test_comments_not_replaced() -> None:
    """SHM name in comments should not be touched."""
    content = (
        "database -open ../dump/ci_top.shm -shm\n"
        "# Note: ci_top.shm stores all waveforms\n"
        "probe -create top -database ../dump/ci_top.shm -depth all\n"
    )
    result = _replace_shm_stems(content, "TOP004")
    assert "# Note: ci_top.shm stores all waveforms" in result
    assert result.count("ci_top_TOP004.shm") == 2


def test_bare_shm_name_no_directory() -> None:
    """SHM without directory prefix."""
    content = (
        "database -open ci_top.shm -shm\n"
        "probe -create top -database ci_top.shm\n"
    )
    result = _replace_shm_stems(content, "TOP004")
    assert result.count("ci_top_TOP004.shm") == 2


def test_different_shm_name() -> None:
    """Works with non-ci_top SHM names."""
    content = (
        "database -open ../dump/my_design.shm -shm\n"
        "probe -create top -database ../dump/my_design.shm -depth all\n"
        "database -close ../dump/my_design.shm\n"
    )
    result = _replace_shm_stems(content, "TEST001")
    assert "my_design.shm" not in result
    assert result.count("my_design_TEST001.shm") == 3


def test_no_shm_in_content() -> None:
    """Content without any .shm references returns unchanged."""
    content = "run 10ms\nexit\n"
    result = _replace_shm_stems(content, "TOP004")
    assert result == content
