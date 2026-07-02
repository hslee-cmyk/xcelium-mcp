"""Tests for registry.config_action(file='checkpoint') (F-159).

F-159: config_action's checkpoint branch previously read/wrote
checkpoints/manifest.json directly via a raw Path + generic dot-path
editor, completely bypassing checkpoint_manager's _read_manifest/
_write_manifest (which own the manifest schema: compile_hash, checkpoints,
tb_analysis_cache). This meant `mcp_config(file="checkpoint", ...)` could
write shapes checkpoint_manager doesn't expect, and register_checkpoint()
writes were invisible to config_action reads if either side cached
differently. Now both paths go through the same read/write functions.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp import checkpoint_manager
from xcelium_mcp.registry import config_action


@pytest.mark.asyncio
async def test_checkpoint_set_creates_manifest_dir_if_missing(tmp_path) -> None:
    """No checkpoints/ dir exists yet — config_action set must not crash
    (checkpoint_manager._write_manifest creates the dir; the old raw
    Path.write_text() path did not)."""
    sim_dir = str(tmp_path)
    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        result = await config_action("set", "checkpoint", "note", "hello")

    assert "Set note" in result
    assert (tmp_path / "checkpoints" / "manifest.json").exists()


@pytest.mark.asyncio
async def test_config_action_sees_register_checkpoint_writes(tmp_path) -> None:
    """A checkpoint registered via checkpoint_manager.register_checkpoint()
    must be visible through config_action(action='get') — proves both paths
    read/write the same underlying manifest via the same owner."""
    sim_dir = str(tmp_path)
    checkpoint_manager.register_checkpoint(sim_dir, "L1_TOP015", 1000, origin="bridge")

    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        result = await config_action("get", "checkpoint", "checkpoints.L1_TOP015.origin", "")

    assert result == "bridge"


@pytest.mark.asyncio
async def test_config_action_checkpoint_set_does_not_clobber_existing_schema(tmp_path) -> None:
    """config_action set on one key must preserve compile_hash/checkpoints
    written by register_checkpoint — proves it's read-modify-write through
    the real manifest, not a separate/stale copy."""
    sim_dir = str(tmp_path)
    checkpoint_manager.register_checkpoint(sim_dir, "L1_TOP015", 1000, origin="bridge")

    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        await config_action("set", "checkpoint", "tb_analysis_cache.note", "x")

    manifest = checkpoint_manager._read_manifest(sim_dir)
    assert "L1_TOP015" in manifest["checkpoints"]
    assert manifest["tb_analysis_cache"]["note"] == "x"
    assert "compile_hash" in manifest


@pytest.mark.asyncio
async def test_config_action_checkpoint_delete_persists_via_checkpoint_manager(tmp_path) -> None:
    sim_dir = str(tmp_path)
    checkpoint_manager.register_checkpoint(sim_dir, "L1_TOP015", 1000, origin="bridge")

    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        result = await config_action("delete", "checkpoint", "checkpoints.L1_TOP015", "")

    assert "Deleted" in result
    manifest = checkpoint_manager._read_manifest(sim_dir)
    assert "L1_TOP015" not in manifest.get("checkpoints", {})
