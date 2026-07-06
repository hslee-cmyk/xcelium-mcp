"""Tests for resolve_test_name's cache-miss path populating cached_test_files (F-175).

Before F-175, a cache-miss re-run of test_discovery.command only ever cached
test NAMES (cached_tests). It now also caches the file each test is defined
in (cached_test_files), via the same parse_test_discovery_output() used by
discovery.py and list_tests — the mechanism this feature extends rather than
duplicates.
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
