"""Unit tests for sim_lifecycle tool behaviors — no real MCP or bridge needed."""
from __future__ import annotations

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
