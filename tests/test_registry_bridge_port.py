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


# ---------------------------------------------------------------------------
# F-2 (sim-session-reaper): touch_activity
# Design ref: docs/02-design/features/xcelium-mcp-sim-session-reaper.design.md §4.1
# Plan SC: T-1/T-2
# ---------------------------------------------------------------------------


def _sole_env(registry: dict) -> dict:
    """Fetch the single environment dict in a test registry, regardless of the
    exact resolved sim_dir key Path.resolve() produces on this OS (Windows
    normalizes a POSIX-style literal like "/projects/H/sim" to
    "C:\\projects\\H\\sim" — asserting via a hand-typed literal key would
    silently miss the real write and pass against a stale pre-seeded dict)."""
    project = next(iter(registry["projects"].values()))
    return next(iter(project["environments"].values()))


@pytest.mark.asyncio
async def test_touch_activity_records_last_activity_and_resets_miss_count() -> None:
    """T-1: first call records last_activity and zeroes ttl_miss_count."""
    from xcelium_mcp.registry import touch_activity, update_bridge_port

    store: dict = {}

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        store["registry"] = reg

    with _patch_git_root("/projects/H"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_bridge_port("/projects/H/sim", 9876)  # seed a bridge session, sibling field
        with patch("xcelium_mcp.registry.time.time", return_value=1000.0):
            await touch_activity("/projects/H/sim")

    env = _sole_env(store["registry"])
    assert env["last_activity"] == 1000.0
    assert env["ttl_miss_count"] == 0
    assert env["bridge_port"] == 9876  # sibling field preserved


@pytest.mark.asyncio
async def test_touch_activity_throttles_rapid_successive_calls() -> None:
    """T-2: a second call within the throttle window must not rewrite
    last_activity (no disk write triggered)."""
    from xcelium_mcp.registry import touch_activity, update_bridge_port

    store: dict = {}
    save_calls = []

    def _fake_load() -> dict:
        return store.get("registry", {"version": 1, "projects": {}})

    def _fake_save(reg: dict) -> None:
        save_calls.append(reg)
        store["registry"] = reg

    with _patch_git_root("/projects/I"), \
         patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
         patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save):
        await update_bridge_port("/projects/I/sim", 9876)  # seed, not counted below
        save_calls.clear()

        with patch("xcelium_mcp.registry.time.time", return_value=1000.0):
            await touch_activity("/projects/I/sim")

        with patch("xcelium_mcp.registry.time.time", return_value=1010.0):  # +10s, within 60s throttle
            await touch_activity("/projects/I/sim")

    assert len(save_calls) == 1
    env = _sole_env(store["registry"])
    assert env["last_activity"] == 1000.0  # unchanged by the throttled second call
