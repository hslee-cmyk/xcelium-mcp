"""Unit tests for registry.py's F-C bridge_port write-back/lookup helpers.

Design ref: docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md §5.4
Plan SC: T-4 — sim_dir-scoped connect_simulator resolves to the right port.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _patch_git_root(root: str):
    """Return a context-manager-ready patch for asyncio.create_subprocess_exec
    that makes _resolve_project_root() see *root* as the git toplevel."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(f"{root}\n".encode(), b""))
    return patch("asyncio.create_subprocess_exec", return_value=fake_proc)


@pytest.mark.asyncio
async def test_update_bridge_port_then_get_bridge_port_roundtrip() -> None:
    """update_bridge_port() writes the port; get_bridge_port() reads it back."""
    from xcelium_mcp.registry import get_bridge_port, update_bridge_port

    store: dict = {}

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    with _patch_git_root("/projects/A"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_bridge_port("/projects/A/sim", 9877)
        port = await get_bridge_port("/projects/A/sim")

    assert port == 9877


@pytest.mark.asyncio
async def test_get_bridge_port_returns_none_when_no_entry() -> None:
    """A sim_dir that was never registered has no bridge_port — must be None,
    not silently fall back to DEFAULT_BRIDGE_PORT (callers decide the fallback)."""
    from xcelium_mcp.registry import get_bridge_port

    with _patch_git_root("/projects/B"), \
         patch("xcelium_mcp.registry._load_registry_sync",
               return_value={"version": 1, "projects": {}}):
        port = await get_bridge_port("/projects/B/sim")

    assert port is None


@pytest.mark.asyncio
async def test_update_bridge_port_overwrites_configured_value() -> None:
    """F-C: sim_discover-time configured port must be overwritten by the actual
    runtime port once a connection succeeds (mcp_bridge.tcl's P1-2 auto-range
    may have picked a different port than .mcp_sim_config.json specified)."""
    from xcelium_mcp.registry import get_bridge_port, update_bridge_port

    store = {
        "registry": {
            "version": 1,
            "projects": {
                "/projects/C": {
                    "environments": {
                        "/projects/C/sim": {"bridge_port": 9876, "tb_type": "uvm"}
                    }
                }
            },
        }
    }

    def _fake_load() -> dict:
        return store["registry"]

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    with _patch_git_root("/projects/C"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_bridge_port("/projects/C/sim", 9881)
        port = await get_bridge_port("/projects/C/sim")

    assert port == 9881
    # Sibling fields (tb_type etc.) must survive the write-back.
    env = store["registry"]["projects"]["/projects/C"]["environments"]["/projects/C/sim"]
    assert env["tb_type"] == "uvm"


@pytest.mark.asyncio
async def test_two_sim_dirs_get_independent_ports() -> None:
    """Two concurrently-debugged sim_dirs must not clobber each other's port
    (this is the concurrency scenario F-C exists to disambiguate)."""
    from xcelium_mcp.registry import get_bridge_port, update_bridge_port

    store: dict = {}

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    with patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        with _patch_git_root("/projects/D"):
            await update_bridge_port("/projects/D/simA", 9876)
        with _patch_git_root("/projects/D"):
            await update_bridge_port("/projects/D/simB", 9877)

        with _patch_git_root("/projects/D"):
            port_a = await get_bridge_port("/projects/D/simA")
        with _patch_git_root("/projects/D"):
            port_b = await get_bridge_port("/projects/D/simB")

    assert port_a == 9876
    assert port_b == 9877


# ---------------------------------------------------------------------------
# F-D (session-state-reattach): update_session_state/get_session_state
# Design ref: docs/02-design/features/xcelium-mcp-session-state-reattach.design.md §5.1
# Plan SC: T-1/T-4
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_session_state_then_get_session_state_roundtrip() -> None:
    """T-1: sim_bridge_run's write path round-trips through get_session_state."""
    from xcelium_mcp.registry import get_session_state, update_session_state

    store: dict = {}

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    tb_source = {
        "files": [{"path": "/projects/E/tb/top015_test.sv", "sha256": "abc123"}],
        "combined_sha256": "def456",
    }

    with _patch_git_root("/projects/E"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_session_state("/projects/E/sim", "TOP015", tb_source)
        test_name, restored_tb_source = await get_session_state("/projects/E/sim")

    assert test_name == "TOP015"
    assert restored_tb_source == tb_source


@pytest.mark.asyncio
async def test_get_session_state_returns_defaults_when_no_entry() -> None:
    """T-4: a sim_dir with no recorded session state returns ("", None) —
    matches BridgeManager's own fresh-instance defaults, no None-check needed
    by callers."""
    from xcelium_mcp.registry import get_session_state

    with _patch_git_root("/projects/F"), \
         patch("xcelium_mcp.registry._load_registry_sync",
               return_value={"version": 1, "projects": {}}):
        test_name, tb_source = await get_session_state("/projects/F/sim")

    assert test_name == ""
    assert tb_source is None


@pytest.mark.asyncio
async def test_update_session_state_preserves_bridge_port_sibling_field() -> None:
    """F-D and F-C write to the same registry entry — one must not clobber
    the other's field."""
    from xcelium_mcp.registry import get_bridge_port, update_bridge_port, update_session_state

    store: dict = {}

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    with _patch_git_root("/projects/G"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_bridge_port("/projects/G/sim", 9876)
        await update_session_state("/projects/G/sim", "TOP015", None)
        port = await get_bridge_port("/projects/G/sim")

    assert port == 9876
