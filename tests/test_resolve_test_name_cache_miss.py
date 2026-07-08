"""Tests for resolve_test_name's schema-migration integration (F-175 gap fix).

resolve_test_name is the actual hot-path entry point sim_batch_run/
sim_regression call before build_tb_provenance() — so it's the real
reproduction site for the xcelium-mcp-f175-provenance-migration-gap bug
(a project whose test_discovery was cached before F-175 existed never got
tb_type/cached_test_files backfilled). It used to have its own independent
copy of the "is cached_tests empty" backfill check; it now delegates entirely
to schema_migration.ensure_test_discovery_current(), whose own migration
logic is unit-tested in test_schema_migration.py. These tests only prove the
integration: migration triggers on delegation, persists via save_sim_config,
and a cache hit doesn't re-trigger it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.schema_migration import CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
from xcelium_mcp.test_resolution import resolve_test_name


@pytest.mark.asyncio
async def test_pre_f175_config_gets_migrated_and_resolves_name(tmp_path) -> None:
    """A config with cached_tests already populated but no tb_type/
    cached_test_files (the pre-F-175 shape that used to fall through
    resolve_test_name's own "if not cached" check forever) gets migrated via
    schema_migration and the short name still resolves correctly."""
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            "command": "ls tb_tests/*.v || true",
            "cached_tests": ["VENEZIA_TOP015_test"],
            "cached_at": "2026-04-16T15:38:04",
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
        patch("xcelium_mcp.test_resolution.save_sim_config", side_effect=_fake_save),
        patch("xcelium_mcp.schema_migration.analyze_tb_type", new_callable=AsyncMock,
              return_value="uvm"),
        patch("xcelium_mcp.schema_migration.shell_run", new_callable=AsyncMock,
              return_value=grep_output),
        patch("xcelium_mcp.schema_migration.scan_test_dependencies", new_callable=AsyncMock,
              return_value={"scanned_primary_sha256": "abc123", "deps": []}),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "VENEZIA_TOP015_test"
    assert saved_cfg["test_discovery"]["schema_version"] == CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
    assert saved_cfg["test_discovery"]["cached_test_files"] == {
        "VENEZIA_TOP015_test": "/sim/tb/tests/top015_test.sv",
    }
    assert saved_cfg["test_discovery"]["cached_dependency_files"] == {
        "VENEZIA_TOP015_test": {"scanned_primary_sha256": "abc123", "deps": []},
    }


@pytest.mark.asyncio
async def test_already_migrated_config_short_circuits_without_rerunning_discovery(tmp_path) -> None:
    """A config already at CURRENT_TEST_DISCOVERY_SCHEMA_VERSION must not
    re-trigger migration (no analyze_tb_type/shell_run calls, no save) — no
    regression in the existing fast path."""
    sim_dir = str(tmp_path)
    cfg = {
        "test_discovery": {
            "cached_tests": ["VENEZIA_TOP015_test"],
            "cached_test_files": {"VENEZIA_TOP015_test": "/sim/tb/tests/top015_test.sv"},
            "cached_dependency_files": {},
            "schema_version": CURRENT_TEST_DISCOVERY_SCHEMA_VERSION,
        }
    }
    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.save_sim_config", new_callable=AsyncMock) as mock_save,
        patch("xcelium_mcp.schema_migration.analyze_tb_type", new_callable=AsyncMock) as mock_tb_type,
        patch("xcelium_mcp.schema_migration.shell_run", new_callable=AsyncMock) as mock_shell,
    ):
        result = await resolve_test_name("VENEZIA_TOP015_test", sim_dir)

    assert result == "VENEZIA_TOP015_test"
    mock_tb_type.assert_not_awaited()
    mock_shell.assert_not_awaited()
    mock_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_substring_match_after_migration(tmp_path) -> None:
    """A single substring match still resolves correctly after migration
    populates cached_tests from scratch (no prior command/cached_tests at
    all — schema_version defaults to 1, same as any pre-F-175 config)."""
    sim_dir = str(tmp_path)
    cfg = {"test_discovery": {}}
    grep_output = (
        "/sim/tb/tests/top014_test.sv:10:class VENEZIA_TOP014_test extends uvm_test;\n"
        "/sim/tb/tests/top015_test.sv:42:class VENEZIA_TOP015_i2c_8bit_offset_test extends uvm_test;\n"
    )

    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=cfg),
        patch("xcelium_mcp.test_resolution.save_sim_config", new_callable=AsyncMock),
        patch("xcelium_mcp.schema_migration.analyze_tb_type", new_callable=AsyncMock,
              return_value="uvm"),
        patch("xcelium_mcp.schema_migration.shell_run", new_callable=AsyncMock,
              return_value=grep_output),
        patch("xcelium_mcp.schema_migration.scan_test_dependencies", new_callable=AsyncMock,
              return_value={"scanned_primary_sha256": "x", "deps": []}),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "VENEZIA_TOP015_i2c_8bit_offset_test"


@pytest.mark.asyncio
async def test_no_config_passes_through_short_name(tmp_path) -> None:
    """No config at all → pass the short name through unresolved (existing
    contract, unrelated to migration)."""
    sim_dir = str(tmp_path)
    with (
        patch("xcelium_mcp.test_resolution.resolve_sim_dir", new_callable=AsyncMock,
              return_value=sim_dir),
        patch("xcelium_mcp.test_resolution.load_sim_config", new_callable=AsyncMock,
              return_value=None),
    ):
        result = await resolve_test_name("TOP015", sim_dir)

    assert result == "TOP015"
