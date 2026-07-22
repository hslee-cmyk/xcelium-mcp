"""Tests for cli.py — xcelium-mcp-cli (Layer 2, Phase B).

Targets the module-level _cmd_run/_cmd_analyze/_cmd_regression coroutines and
_build_parser() directly — these are plain module functions (not nested inside
a register() closure like the MCP tools), so no MockMCP fixture is needed here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.cli import _build_parser, _cmd_analyze, _cmd_regression, _cmd_run
from xcelium_mcp.compound import CompoundResult
from xcelium_mcp.shell_utils import UserInputRequired

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestBuildParser:
    def test_run_subcommand_parses(self):
        parser = _build_parser()
        args = parser.parse_args(["run", "TOP015", "--sim-dir", "/sim", "--csv-signals", "a", "b"])
        assert args.command == "run"
        assert args.test_name == "TOP015"
        assert args.sim_dir == "/sim"
        assert args.csv_signals == ["a", "b"]
        assert args.func is _cmd_run

    def test_run_sim_dir_after_positional_also_parses(self):
        """--sim-dir works whether it comes before or after the positional test_name."""
        parser = _build_parser()
        args = parser.parse_args(["run", "TOP015", "--sim-dir", "/sim"])
        assert args.sim_dir == "/sim"

    def test_analyze_subcommand_requires_signals(self):
        parser = _build_parser()
        args = parser.parse_args(["analyze", "/dump/T.shm", "--signals", "dut.a", "dut.b"])
        assert args.dump_path == "/dump/T.shm"
        assert args.signals == ["dut.a", "dut.b"]
        assert args.func is _cmd_analyze

    def test_regression_subcommand_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["regression"])
        assert args.test_list == []
        assert args.csv_on_fail is False
        assert args.func is _cmd_regression

    def test_no_subcommand_is_error(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


# ---------------------------------------------------------------------------
# _cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    @pytest.mark.asyncio
    async def test_pass_returns_exit_code_0(self, capsys):
        args = _build_parser().parse_args(["run", "TOP015"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.resolve_test_name", new_callable=AsyncMock, return_value="TOP015"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock, return_value={}), \
             patch("xcelium_mcp.cli.run_and_check", new_callable=AsyncMock,
                   return_value=CompoundResult(status="PASS", log_summary="COMPLETE. Errors: 0")):
            code = await _cmd_run(args)

        assert code == 0
        out = capsys.readouterr().out
        assert "[RUN] TOP015" in out
        assert "[RESULT] PASS" in out

    @pytest.mark.asyncio
    async def test_fail_returns_exit_code_1(self):
        args = _build_parser().parse_args(["run", "TOP015"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.resolve_test_name", new_callable=AsyncMock, return_value="TOP015"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock, return_value={}), \
             patch("xcelium_mcp.cli.run_and_check", new_callable=AsyncMock,
                   return_value=CompoundResult(status="FAIL", log_summary="COMPLETE. Errors: 1")):
            code = await _cmd_run(args)

        assert code == 1

    @pytest.mark.asyncio
    async def test_sim_dir_resolution_failure_returns_exit_code_2(self, capsys):
        args = _build_parser().parse_args(["run", "TOP015"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock,
                    side_effect=ValueError("no sim_dir configured")):
            code = await _cmd_run(args)

        assert code == 2
        assert "no sim_dir configured" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_user_input_required_returns_exit_code_2(self, capsys):
        args = _build_parser().parse_args(["run", "TOP015"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.resolve_test_name", new_callable=AsyncMock, return_value="TOP015"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock,
                   side_effect=UserInputRequired("which runner?")):
            code = await _cmd_run(args)

        assert code == 2
        assert "USER INPUT REQUIRED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_analyze
# ---------------------------------------------------------------------------

class TestCmdAnalyze:
    @pytest.mark.asyncio
    async def test_pass_returns_exit_code_0(self, capsys):
        args = _build_parser().parse_args(["analyze", "/dump/T.shm", "--signals", "dut.a"])
        with patch("xcelium_mcp.cli.analyze_waveform", new_callable=AsyncMock,
                    return_value=CompoundResult(status="PASS", log_summary="Extracted CSV for 1 signal(s)")):
            code = await _cmd_analyze(args)

        assert code == 0
        assert "[ANALYZE] /dump/T.shm" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_error_returns_exit_code_1(self):
        args = _build_parser().parse_args(["analyze", "/dump/bad.shm", "--signals", "dut.a"])
        with patch("xcelium_mcp.cli.analyze_waveform", new_callable=AsyncMock,
                    return_value=CompoundResult(status="ERROR", log_summary="extract failed")):
            code = await _cmd_analyze(args)

        assert code == 1


# ---------------------------------------------------------------------------
# _cmd_regression
# ---------------------------------------------------------------------------

class TestCmdRegression:
    @pytest.mark.asyncio
    async def test_explicit_test_list(self, capsys):
        args = _build_parser().parse_args(["regression", "--test-list", "T1", "T2"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock, return_value={}), \
             patch("xcelium_mcp.cli.resolve_test_names_batch", new_callable=AsyncMock,
                   return_value=["T1", "T2"]), \
             patch("xcelium_mcp.cli.regression_summary", new_callable=AsyncMock,
                   return_value=CompoundResult(status="PASS", log_summary="2/2 verdict tests PASS")):
            code = await _cmd_regression(args)

        assert code == 0
        assert "[REGRESSION] 2 test(s)" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_empty_test_list_falls_back_to_config(self):
        args = _build_parser().parse_args(["regression"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock, return_value={}), \
             patch("xcelium_mcp.cli.load_sim_config", new_callable=AsyncMock,
                   return_value={"test_list": ["T1"]}), \
             patch("xcelium_mcp.cli.resolve_test_names_batch", new_callable=AsyncMock,
                   return_value=["T1"]), \
             patch("xcelium_mcp.cli.regression_summary", new_callable=AsyncMock,
                   return_value=CompoundResult(status="PASS", log_summary="1/1 verdict tests PASS")) as mock_regr:
            code = await _cmd_regression(args)

        assert code == 0
        mock_regr.assert_called_once()
        assert mock_regr.call_args.kwargs["test_list"] == ["T1"]

    @pytest.mark.asyncio
    async def test_empty_test_list_and_no_config_is_error(self, capsys):
        args = _build_parser().parse_args(["regression"])
        with patch("xcelium_mcp.cli.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"), \
             patch("xcelium_mcp.cli.load_or_detect_runner", new_callable=AsyncMock, return_value={}), \
             patch("xcelium_mcp.cli.load_sim_config", new_callable=AsyncMock, return_value=None):
            code = await _cmd_regression(args)

        assert code == 2
        assert "no --test-list given" in capsys.readouterr().err
