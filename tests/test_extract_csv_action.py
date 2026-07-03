"""Tests for inspect_signal(action="extract_csv") (F-174).

F-174: bisect_signal was the only tool path into csv_cache's in-memory/disk
cache. When the skill/AI judges a bisect search unnecessary (narrow, known
range) it had no cache-aware alternative and fell back to shelling out to
simvisdbutil directly, bypassing the cache entirely. This adds a thin
extract-only action to inspect_signal so a cache-aware path always exists.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from xcelium_mcp.tools.signal_inspection import register


def _make_tool():
    fake_bridges = MagicMock()
    mcp = FastMCP("test")
    register(mcp, fake_bridges)
    return mcp._tool_manager._tools["inspect_signal"]


class TestExtractCsvAction:
    @pytest.mark.asyncio
    async def test_extract_csv_returns_csv_path(self):
        tool = _make_tool()
        with patch(
            "xcelium_mcp.csv_cache.extract",
            AsyncMock(return_value="/tmp/mcp_csv_test_abcd1234.csv"),
        ):
            result = await tool.fn(
                action="extract_csv",
                signals=["top.hw.sig_a", "top.hw.sig_b"],
                shm_path="dump/test.shm",
                start_ns=1000,
                end_ns=2000,
            )
        assert result == "CSV: /tmp/mcp_csv_test_abcd1234.csv"

    @pytest.mark.asyncio
    async def test_extract_csv_requires_signals(self):
        tool = _make_tool()
        result = await tool.fn(action="extract_csv", shm_path="dump/test.shm")
        assert "ERROR" in result
        assert "signals" in result

    @pytest.mark.asyncio
    async def test_extract_csv_auto_detects_shm_when_missing(self):
        tool = _make_tool()
        with (
            patch(
                "xcelium_mcp.tools.signal_inspection.resolve_sim_dir",
                AsyncMock(return_value="/sim/dir"),
            ),
            patch(
                "xcelium_mcp.tools.signal_inspection.find_shm",
                AsyncMock(return_value="dump/auto.shm"),
            ),
            patch(
                "xcelium_mcp.csv_cache.extract", AsyncMock(return_value="/tmp/auto.csv")
            ) as mock_extract,
        ):
            result = await tool.fn(action="extract_csv", signals=["top.hw.sig_a"])

        assert result == "CSV: /tmp/auto.csv"
        assert mock_extract.call_args.kwargs["shm_path"] == "dump/auto.shm"

    @pytest.mark.asyncio
    async def test_extract_csv_no_shm_found_returns_error(self):
        tool = _make_tool()
        with (
            patch(
                "xcelium_mcp.tools.signal_inspection.resolve_sim_dir",
                AsyncMock(return_value="/sim/dir"),
            ),
            patch(
                "xcelium_mcp.tools.signal_inspection.find_shm",
                AsyncMock(return_value=""),
            ),
        ):
            result = await tool.fn(action="extract_csv", signals=["top.hw.sig_a"])
        assert "ERROR" in result
        assert "No SHM found" in result

    @pytest.mark.asyncio
    async def test_extract_csv_propagates_extract_failure(self):
        tool = _make_tool()
        with patch(
            "xcelium_mcp.csv_cache.extract",
            AsyncMock(side_effect=RuntimeError("simvisdbutil failed")),
        ):
            result = await tool.fn(
                action="extract_csv", signals=["top.hw.sig_a"], shm_path="dump/test.shm"
            )
        assert "ERROR extracting CSV" in result
        assert "simvisdbutil failed" in result

    @pytest.mark.asyncio
    async def test_unknown_action_error_lists_extract_csv(self):
        tool = _make_tool()
        result = await tool.fn(action="bogus")
        assert "extract_csv" in result
