"""Unit tests for sim_session_reaper.py (F-2).

Design ref: docs/02-design/features/xcelium-mcp-sim-session-reaper.design.md §4.2/§7
Plan ref: docs/01-plan/features/xcelium-mcp-sim-session-reaper.plan.md
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.sim_session_reaper import (
    DEFAULT_TTL_HOURS,
    MIN_MISS_COUNT_TO_KILL,
    effective_ttl_seconds,
    reap_idle_sessions,
    sessions_to_reap,
)

# ---------------------------------------------------------------------------
# effective_ttl_seconds — pure, env-driven
# ---------------------------------------------------------------------------


def test_effective_ttl_seconds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-10: no env var set -> DEFAULT_TTL_HOURS."""
    monkeypatch.delenv("XCELIUM_MCP_SIM_TTL_HOURS", raising=False)
    assert effective_ttl_seconds() == DEFAULT_TTL_HOURS * 3600


def test_effective_ttl_seconds_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XCELIUM_MCP_SIM_TTL_HOURS", "2")
    assert effective_ttl_seconds() == 2 * 3600


def test_effective_ttl_seconds_invalid_value_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-10: garbage env value -> falls back to default rather than crashing."""
    monkeypatch.setenv("XCELIUM_MCP_SIM_TTL_HOURS", "not-a-number")
    assert effective_ttl_seconds() == DEFAULT_TTL_HOURS * 3600


# ---------------------------------------------------------------------------
# sessions_to_reap — pure decision logic
# ---------------------------------------------------------------------------


def _registry(env: dict) -> dict:
    return {"projects": {"/proj": {"environments": {"/proj/sim": env}}}}


def test_ttl_exceeded_twice_marks_for_reap() -> None:
    """T-3/T-4: needs MIN_MISS_COUNT_TO_KILL consecutive exceedances before reaping."""
    now = 100_000.0
    ttl = 3600
    reg = _registry({"bridge_port": 9876, "last_activity": now - ttl - 1, "ttl_miss_count": MIN_MISS_COUNT_TO_KILL - 1})

    to_reap = sessions_to_reap(reg, ttl, now)

    assert to_reap == [("/proj", "/proj/sim", 9876)]
    assert reg["projects"]["/proj"]["environments"]["/proj/sim"]["ttl_miss_count"] == MIN_MISS_COUNT_TO_KILL


def test_ttl_exceeded_first_time_not_yet_reaped() -> None:
    """T-4: first exceedance only increments the miss counter, no reap yet."""
    now = 100_000.0
    ttl = 3600
    reg = _registry({"bridge_port": 9876, "last_activity": now - ttl - 1, "ttl_miss_count": 0})

    to_reap = sessions_to_reap(reg, ttl, now)

    assert to_reap == []
    assert reg["projects"]["/proj"]["environments"]["/proj/sim"]["ttl_miss_count"] == 1


def test_ttl_not_exceeded_resets_miss_count() -> None:
    """T-5: activity within TTL resets any previously accumulated miss count."""
    now = 100_000.0
    ttl = 3600
    reg = _registry({"bridge_port": 9876, "last_activity": now - 10, "ttl_miss_count": 1})

    to_reap = sessions_to_reap(reg, ttl, now)

    assert to_reap == []
    assert reg["projects"]["/proj"]["environments"]["/proj/sim"]["ttl_miss_count"] == 0


def test_legacy_entry_without_last_activity_is_skipped() -> None:
    """T-6: no last_activity recorded yet -> never touched by the reaper."""
    reg = _registry({"bridge_port": 9876})

    to_reap = sessions_to_reap(reg, ttl_seconds=3600, now=100_000.0)

    assert to_reap == []


def test_non_bridge_entry_without_port_is_skipped() -> None:
    """T-8: entries with no bridge_port (batch/regression-only) are ignored."""
    reg = _registry({"last_activity": 0, "ttl_miss_count": 5})

    to_reap = sessions_to_reap(reg, ttl_seconds=3600, now=100_000.0)

    assert to_reap == []


# ---------------------------------------------------------------------------
# reap_idle_sessions — I/O orchestration (registry + TclBridge mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_idle_sessions_shuts_down_and_cleans_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-3: end-to-end — TTL-exceeded session gets __SHUTDOWN__ and is removed."""
    monkeypatch.delenv("XCELIUM_MCP_SIM_TTL_HOURS", raising=False)
    now = 100_000.0
    ttl = effective_ttl_seconds()
    store = {
        "registry": _registry(
            {"bridge_port": 9876, "last_activity": now - ttl - 1, "ttl_miss_count": MIN_MISS_COUNT_TO_KILL - 1}
        )
    }

    mock_bridge = AsyncMock()
    mock_bridge.connect = AsyncMock(return_value="pong")
    mock_bridge.execute_safe = AsyncMock(return_value=None)

    with patch("xcelium_mcp.sim_session_reaper.load_registry", return_value=store["registry"]), \
         patch("xcelium_mcp.sim_session_reaper.save_registry", side_effect=lambda r: store.__setitem__("registry", r)), \
         patch("xcelium_mcp.sim_session_reaper.time.time", return_value=now), \
         patch("xcelium_mcp.sim_session_reaper.TclBridge", return_value=mock_bridge):
        reaped = await reap_idle_sessions()

    assert reaped == ["/proj/sim"]
    mock_bridge.execute_safe.assert_awaited_once_with("__SHUTDOWN__")
    assert "/proj/sim" not in store["registry"]["projects"]["/proj"]["environments"]


@pytest.mark.asyncio
async def test_reap_idle_sessions_handles_orphan_port_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-7: connection to an already-dead port must not crash — registry entry
    is still cleaned up."""
    monkeypatch.delenv("XCELIUM_MCP_SIM_TTL_HOURS", raising=False)
    now = 100_000.0
    ttl = effective_ttl_seconds()
    store = {
        "registry": _registry(
            {"bridge_port": 9876, "last_activity": now - ttl - 1, "ttl_miss_count": MIN_MISS_COUNT_TO_KILL - 1}
        )
    }

    mock_bridge = AsyncMock()
    mock_bridge.connect = AsyncMock(side_effect=ConnectionError("refused"))

    with patch("xcelium_mcp.sim_session_reaper.load_registry", return_value=store["registry"]), \
         patch("xcelium_mcp.sim_session_reaper.save_registry", side_effect=lambda r: store.__setitem__("registry", r)), \
         patch("xcelium_mcp.sim_session_reaper.time.time", return_value=now), \
         patch("xcelium_mcp.sim_session_reaper.TclBridge", return_value=mock_bridge):
        reaped = await reap_idle_sessions()

    assert reaped == ["/proj/sim"]
    assert "/proj/sim" not in store["registry"]["projects"]["/proj"]["environments"]


@pytest.mark.asyncio
async def test_reap_idle_sessions_no_action_when_within_ttl() -> None:
    """T-5: nothing reaped, no shutdown attempted, when all sessions are fresh."""
    now = 100_000.0
    store = {"registry": _registry({"bridge_port": 9876, "last_activity": now - 10})}

    with patch("xcelium_mcp.sim_session_reaper.load_registry", return_value=store["registry"]), \
         patch("xcelium_mcp.sim_session_reaper.save_registry", side_effect=lambda r: store.__setitem__("registry", r)), \
         patch("xcelium_mcp.sim_session_reaper.time.time", return_value=now), \
         patch("xcelium_mcp.sim_session_reaper.TclBridge") as mock_cls:
        reaped = await reap_idle_sessions()

    assert reaped == []
    mock_cls.assert_not_called()


def test_main_runs_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke test: main() drives the asyncio loop and returns 0 even with an
    empty registry (no bridge connections attempted)."""
    from xcelium_mcp import sim_session_reaper

    monkeypatch.setattr(sim_session_reaper, "load_registry", lambda: {"projects": {}})
    monkeypatch.setattr(sim_session_reaper, "save_registry", lambda r: None)

    assert sim_session_reaper.main() == 0
