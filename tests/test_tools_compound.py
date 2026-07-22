"""Tests for tools/compound.py — MCP wrappers over compound.py (Phase B).

Uses the same _MockMCP fixture pattern established in test_bug_fixes.py for
capturing @mcp.tool()-decorated closures for direct invocation.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.compound import CompoundResult
from xcelium_mcp.shell_utils import UserInputRequired


class _MockMCP:
    """Captures tools registered via @mcp.tool() for direct invocation."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


def _register() -> _MockMCP:
    from xcelium_mcp.tools.compound import register
    mock_mcp = _MockMCP()
    register(mock_mcp)
    return mock_mcp


class TestRegistration:
    def test_registers_exactly_3_tools(self):
        mock_mcp = _register()
        assert set(mock_mcp.tools) == {
            "sim_run_and_check", "sim_analyze_waveform", "sim_regression_summary",
        }


class TestSimRunAndCheck:
    @pytest.mark.asyncio
    async def test_happy_path_calls_compound_run_and_check(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.resolve_sim_dir", new_callable=AsyncMock,
                    return_value="/sim"), \
             patch("xcelium_mcp.tools.compound.resolve_test_name", new_callable=AsyncMock,
                   return_value="TOP015"), \
             patch("xcelium_mcp.tools.compound.load_or_detect_runner", new_callable=AsyncMock,
                   return_value={"default_mode": "rtl"}), \
             patch("xcelium_mcp.tools.compound.run_and_check", new_callable=AsyncMock,
                   return_value=CompoundResult(status="PASS", log_summary="COMPLETE. Errors: 0")) as mock_rac:
            out = await mock_mcp.tools["sim_run_and_check"](test_name="TOP015")

        assert "status: PASS" in out
        mock_rac.assert_called_once()
        assert mock_rac.call_args.kwargs["test_name"] == "TOP015"

    @pytest.mark.asyncio
    async def test_invalid_dump_depth_short_circuits(self):
        mock_mcp = _register()
        out = await mock_mcp.tools["sim_run_and_check"](test_name="TOP015", dump_depth="bogus")
        assert "Invalid dump_depth" in out

    @pytest.mark.asyncio
    async def test_sim_dir_resolution_error(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.resolve_sim_dir", new_callable=AsyncMock,
                    side_effect=ValueError("no default sim_dir")):
            out = await mock_mcp.tools["sim_run_and_check"](test_name="TOP015")
        assert "ERROR" in out and "no default sim_dir" in out

    @pytest.mark.asyncio
    async def test_user_input_required(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.resolve_sim_dir", new_callable=AsyncMock,
                    return_value="/sim"), \
             patch("xcelium_mcp.tools.compound.resolve_test_name", new_callable=AsyncMock,
                   return_value="TOP015"), \
             patch("xcelium_mcp.tools.compound.load_or_detect_runner", new_callable=AsyncMock,
                   side_effect=UserInputRequired("pick a runner")):
            out = await mock_mcp.tools["sim_run_and_check"](test_name="TOP015")
        assert "USER INPUT REQUIRED" in out


class TestSimAnalyzeWaveform:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.analyze_waveform", new_callable=AsyncMock,
                    return_value=CompoundResult(status="PASS", log_summary="Extracted CSV for 1 signal(s)")) as mock_aw:
            out = await mock_mcp.tools["sim_analyze_waveform"](
                dump_path="/sim/dump/T.shm", signals=["dut.a"],
            )
        assert "status: PASS" in out
        mock_aw.assert_called_once()

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        mock_mcp = _register()
        out = await mock_mcp.tools["sim_analyze_waveform"](
            dump_path="../../etc/passwd", signals=["dut.a"],
        )
        assert "ERROR" in out


class TestSimRegressionSummary:
    @pytest.mark.asyncio
    async def test_happy_path_calls_compound_regression_summary(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.resolve_sim_dir", new_callable=AsyncMock,
                    return_value="/sim"), \
             patch("xcelium_mcp.tools.compound.load_or_detect_runner", new_callable=AsyncMock,
                   return_value={}), \
             patch("xcelium_mcp.tools.compound.resolve_test_names_batch", new_callable=AsyncMock,
                   return_value=["T1", "T2"]), \
             patch("xcelium_mcp.tools.compound.regression_summary", new_callable=AsyncMock,
                   return_value=CompoundResult(status="PASS", log_summary="2/2 verdict tests PASS")) as mock_rs:
            out = await mock_mcp.tools["sim_regression_summary"](test_list=["T1", "T2"])

        assert "status: PASS" in out
        mock_rs.assert_called_once()
        assert mock_rs.call_args.kwargs["test_list"] == ["T1", "T2"]

    @pytest.mark.asyncio
    async def test_empty_test_list_without_config_is_error(self):
        mock_mcp = _register()
        with patch("xcelium_mcp.tools.compound.resolve_sim_dir", new_callable=AsyncMock,
                    return_value="/sim"), \
             patch("xcelium_mcp.tools.compound.load_or_detect_runner", new_callable=AsyncMock,
                   return_value={}), \
             patch("xcelium_mcp.tools.compound.load_sim_config", new_callable=AsyncMock,
                   return_value=None):
            out = await mock_mcp.tools["sim_regression_summary"](test_list=[])

        assert "ERROR" in out and "test_list is empty" in out
