"""Unit tests for sim_lifecycle tool behaviors — no real MCP or bridge needed."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest


class _MockMCP:
    """Captures tools registered via @mcp.tool() so they can be called directly."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


# ---------------------------------------------------------------------------
# F-078: Surface RUN_ERROR from __RUN_AND_REPORT__ as ERROR response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_run_run_error_surfaces_error() -> None:
    """RUN_ERROR prefix from bridge should be returned as ERROR to caller."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(
        return_value="RUN_ERROR:bad duration\n(pos)"
    )

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert result.startswith("ERROR"), f"Expected ERROR prefix, got: {result!r}"
    assert "RUN_ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_success_returns_position() -> None:
    """Normal bridge response should return 'Simulation advanced' message."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert result.startswith("Simulation advanced"), f"Unexpected result: {result!r}"
    assert "100 NS" in result


# ---------------------------------------------------------------------------
# F-079: _DURATION_RE at module scope + duration.strip() before fullmatch
# ---------------------------------------------------------------------------


def test_duration_re_accessible_at_module_scope() -> None:
    """_DURATION_RE should be importable from module scope (not inside register())."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    assert isinstance(_DURATION_RE, re.Pattern)


def test_duration_re_matches_with_leading_trailing_space() -> None:
    """Stripped duration should match — strip() happens before fullmatch."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    # The regex itself matches clean values; strip is done in sim_run before calling fullmatch
    assert _DURATION_RE.fullmatch("100ns") is not None
    assert _DURATION_RE.fullmatch("  100ns  ") is None  # regex gets pre-stripped value


# ---------------------------------------------------------------------------
# F-080: Harden sim_run duration — length cap + ASCII-only digits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_run_rejects_too_long_duration() -> None:
    """Duration longer than 32 chars should be rejected immediately."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="ok")

    register(mock_mcp, mock_bridges)

    long_dur = "9" * 100 + "ns"
    result = await mock_mcp.tools["sim_run"](duration=long_dur)
    assert "ERROR" in result and "too long" in result


@pytest.mark.asyncio
async def test_sim_run_rejects_unicode_digits() -> None:
    """Unicode digits like '１００ns' should be rejected (ASCII-only check)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="ok")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="１００ns")
    assert "ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_accepts_normal_duration() -> None:
    """'100ns' should pass all validation."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert "ERROR" not in result or "RUN_ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_strips_duration_before_validation() -> None:
    """sim_run with leading/trailing space on duration should pass validation."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    # Duration with whitespace should not fail validation
    result = await mock_mcp.tools["sim_run"](duration="  100ns  ")
    assert "ERROR" not in result or "RUN_ERROR" in result, (
        f"Whitespace duration should not trigger validation error: {result!r}"
    )
