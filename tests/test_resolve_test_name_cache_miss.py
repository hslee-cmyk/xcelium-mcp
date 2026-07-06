"""Tests for resolve_test_name's cache-miss path populating cached_test_files (F-175).

Before F-175, a cache-miss re-run of test_discovery.command only ever cached
test NAMES (cached_tests). It now also caches the file each test is defined
in (cached_test_files), via the same parse_test_discovery_output() used by
discovery.py and list_tests — the mechanism this feature extends rather than
duplicates. It also resolves and caches each test's dependency FILE LOCATIONS
(cached_dependency_files) at this same cache-miss moment, so the per-run hot
path (build_tb_provenance) never needs to run find/grep itself.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.test_resolution import resolve_test_name


@pytest.mark.asyncio
async def test_cache_miss_populates_cached_test_files(tmp_path) -> None:
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            "command": "grep -rn 'extends uvm_test' . --include='*.sv' || true",
            "tb_type": "uvm",
            "cached_tests": [],
            "cached_test_files": {},
        }
    }
    grep_output = "/sim/tb/tests/top015_test.sv:42:class VENEZIA_TOP015_test extends uvm_test;"

    saved_cfg: dict = {}

    async def _fake_save(_dir: str, data: dict) -> None:
        saved_cfg.update(data)

    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.shell_run", new_callable=AsyncMock,
              return_value=grep_output),
        patch("xcelium_mcp.test_resolution.save_sim_config", side_effect=_fake_save),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "VENEZIA_TOP015_test"
    assert saved_cfg["test_discovery"]["cached_tests"] == ["VENEZIA_TOP015_test"]
    assert saved_cfg["test_discovery"]["cached_test_files"] == {
        "VENEZIA_TOP015_test": "/sim/tb/tests/top015_test.sv",
    }


@pytest.mark.asyncio
async def test_cache_miss_populates_cached_dependency_files(tmp_path) -> None:
    """Cache-miss resolution must also call scan_test_dependencies() for each
    discovered test and store the result (deps + the primary file's sha256
    at scan time) in cached_dependency_files — once, here, so the per-run
    hot path (build_tb_provenance) only re-scans if the primary file later
    changes. scan_test_dependencies' own scanning correctness is covered
    separately in test_tb_provenance.py; this test only proves the wiring."""
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            "command": "grep -rn 'extends uvm_test' . --include='*.sv' || true",
            "tb_type": "uvm",
            "cached_tests": [],
            "cached_test_files": {},
        }
    }
    grep_output = "/sim/tb/tests/top015_test.sv:2:class VENEZIA_TOP015_test extends uvm_test;"
    scan_entry = {"scanned_primary_sha256": "abc123", "deps": ["/sim/tb/shared/i2c_sequence.svh"]}

    saved_cfg: dict = {}

    async def _fake_save(_dir: str, data: dict) -> None:
        saved_cfg.update(data)

    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.shell_run", new_callable=AsyncMock,
              return_value=grep_output),
        patch("xcelium_mcp.test_resolution.scan_test_dependencies", new_callable=AsyncMock,
              return_value=scan_entry),
        patch("xcelium_mcp.test_resolution.save_sim_config", side_effect=_fake_save),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "VENEZIA_TOP015_test"
    assert saved_cfg["test_discovery"]["cached_dependency_files"] == {
        "VENEZIA_TOP015_test": scan_entry,
    }


@pytest.mark.asyncio
async def test_pre_f175_config_falls_back_without_corrupting_file_map(tmp_path) -> None:
    """A config discovered BEFORE F-175 has no tb_type and its stored
    `command` is the OLD name-only pipeline (grep -rh | grep -oE | sed |
    sort -u) — bare test names, not `file:lineno:content` lines. Without a
    guard, running that old-format output through parse_test_discovery_output
    (which needs tb_type to pick a parser) falls through to the "bare file
    path" branch and stores {name: name} — a wrong mapping (the test name
    masquerading as its own file path), not merely a missing one. Must
    instead fall back to plain name extraction with an empty file map."""
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            # Old-format command: no tb_type key stored alongside it.
            "command": "(grep -rh 'extends uvm_test' . || true) | grep -oE 'class \\w+' | sed 's/class //' | sort -u",
            "cached_tests": [],
        }
    }
    # Old command's actual output shape: bare sorted names, no file info.
    old_format_output = "VENEZIA_TOP014_test\nVENEZIA_TOP015_i2c_8bit_offset_test\n"

    saved_cfg: dict = {}

    async def _fake_save(_dir: str, data: dict) -> None:
        saved_cfg.update(data)

    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.shell_run", new_callable=AsyncMock,
              return_value=old_format_output),
        patch("xcelium_mcp.test_resolution.save_sim_config", side_effect=_fake_save),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "VENEZIA_TOP015_i2c_8bit_offset_test"
    assert saved_cfg["test_discovery"]["cached_tests"] == [
        "VENEZIA_TOP014_test", "VENEZIA_TOP015_i2c_8bit_offset_test",
    ]
    # The critical assertion: no bogus name->name "file" entries.
    assert saved_cfg["test_discovery"]["cached_test_files"] == {}


@pytest.mark.asyncio
async def test_exact_match_short_circuits_without_rerunning_discovery(tmp_path) -> None:
    """A cache hit must not touch cached_test_files at all — no regression
    in the existing fast path."""
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            "cached_tests": ["VENEZIA_TOP015_test"],
            "cached_test_files": {"VENEZIA_TOP015_test": "/sim/tb/tests/top015_test.sv"},
        }
    }
    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.shell_run", new_callable=AsyncMock) as mock_shell,
    ):
        result = await resolve_test_name("VENEZIA_TOP015_test", sim_dir)

    assert result == "VENEZIA_TOP015_test"
    mock_shell.assert_not_called()
