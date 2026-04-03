"""Tests for _replace_shm_stems() — SHM path preprocessing.

Uses inline copy of the function to avoid circular import
(batch_runner ↔ sim_runner dependency).
"""
import re as _re


def _replace_shm_stems(content: str, test_name: str) -> str:
    """Copy of batch_runner._replace_shm_stems for isolated testing."""
    pattern = r"database\s+-open\s+(?:\S*/)?(\S+)\.shm"
    matches = _re.findall(pattern, content)
    if not matches:
        return content
    replaced: set[str] = set()
    for stem in matches:
        if stem in replaced or test_name in stem:
            continue
        escaped = _re.escape(stem)
        content = _re.sub(
            r"((?:database\s+(?:-open|-close)|probe\s+.*?-database)\s+(?:\S*/)?)"
            + escaped + r"\.shm",
            rf"\1{stem}_{test_name}.shm",
            content,
        )
        replaced.add(stem)
    return content


def test_replace_all_three_contexts():
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


def test_already_tagged_skips():
    """If stem already contains test_name, no double-tagging."""
    content = (
        "database -open ../dump/ci_top_VENEZIA_TOP015.shm -shm\n"
        "probe -create top -database ../dump/ci_top_VENEZIA_TOP015.shm -depth all\n"
        "database -close ../dump/ci_top_VENEZIA_TOP015.shm\n"
    )
    result = _replace_shm_stems(content, "VENEZIA_TOP015")
    assert result == content


def test_no_database_open_no_replacement():
    """Without database -open, stems are not discovered — no replacement."""
    content = "probe -create top -database ../dump/ci_top.shm\n"
    result = _replace_shm_stems(content, "VENEZIA_TOP015")
    assert result == content


def test_comments_not_replaced():
    """SHM name in comments should not be touched."""
    content = (
        "database -open ../dump/ci_top.shm -shm\n"
        "# Note: ci_top.shm stores all waveforms\n"
        "probe -create top -database ../dump/ci_top.shm -depth all\n"
    )
    result = _replace_shm_stems(content, "TOP004")
    assert "# Note: ci_top.shm stores all waveforms" in result
    assert result.count("ci_top_TOP004.shm") == 2


def test_bare_shm_name_no_directory():
    """SHM without directory prefix."""
    content = (
        "database -open ci_top.shm -shm\n"
        "probe -create top -database ci_top.shm\n"
    )
    result = _replace_shm_stems(content, "TOP004")
    assert result.count("ci_top_TOP004.shm") == 2


def test_different_shm_name():
    """Works with non-ci_top SHM names."""
    content = (
        "database -open ../dump/my_design.shm -shm\n"
        "probe -create top -database ../dump/my_design.shm -depth all\n"
        "database -close ../dump/my_design.shm\n"
    )
    result = _replace_shm_stems(content, "TEST001")
    assert "my_design.shm" not in result
    assert result.count("my_design_TEST001.shm") == 3


def test_no_shm_in_content():
    """Content without any .shm references returns unchanged."""
    content = "run 10ms\nexit\n"
    result = _replace_shm_stems(content, "TOP004")
    assert result == content
