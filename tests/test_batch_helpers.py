"""Tests for batch_runner helper functions extracted from _run_batch_single.

Tests pure/thin helpers: parse_existing_job, build_batch_cmd,
launch_nohup_job, watch_pid_and_poll.

Since these helpers use ssh_run (subprocess calls), we mock ssh_run
for unit testing. Pure functions like resolve_sim_params and
_resolve_exec_cmd are tested directly.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.batch_runner import (
    _resolve_exec_cmd,
    build_batch_cmd,
    launch_nohup_job,
    parse_existing_job,
)
from xcelium_mcp.batch_polling import watch_pid_and_poll
from xcelium_mcp.test_resolution import resolve_sim_params


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_runner(**overrides) -> dict:
    """Create a minimal runner config dict."""
    base = {
        "script": "run_sim.sh",
        "login_shell": "/bin/tcsh",
        "script_shell": True,
        "args_format": "-test {test_name} --",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: parse_existing_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_existing_job_no_file() -> None:
    """Empty cat output (no job file) returns None."""
    with patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.return_value = ""
        result = await parse_existing_job("/tmp/batch_job.json", timeout=600)
        assert result is None
        mock_ssh.assert_called_once()


@pytest.mark.asyncio
async def test_parse_existing_job_dead_pid() -> None:
    """Dead PID in job file: cleans up and returns None."""
    job = json.dumps({"pid": 12345, "log_file": "/tmp/batch.log", "test_name": "T1"})
    with patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.side_effect = [
            job,       # cat job_file
            "DEAD",    # kill -0 check
            "",        # rm -f job_file
        ]
        result = await parse_existing_job("/tmp/batch_job.json", timeout=600)
        assert result is None
        assert mock_ssh.call_count == 3


@pytest.mark.asyncio
async def test_parse_existing_job_alive_pid() -> None:
    """Alive PID: resumes polling and returns result."""
    job = json.dumps({"pid": 99, "log_file": "/tmp/batch.log", "test_name": "T1"})
    with (
        patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh,
        patch("xcelium_mcp.batch_runner.poll_batch_log", new_callable=AsyncMock) as mock_poll,
    ):
        mock_ssh.side_effect = [
            job,       # cat job_file
            "ALIVE",   # kill -0 check
            "",        # rm -f job_file (after poll)
        ]
        mock_poll.return_value = ("PASS result", False)
        result = await parse_existing_job("/tmp/batch_job.json", timeout=600)
        assert result == "PASS result"


@pytest.mark.asyncio
async def test_parse_existing_job_invalid_json() -> None:
    """Invalid JSON in job file: cleans up and returns None."""
    with patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.side_effect = [
            "not-valid-json",  # cat job_file
            "",                # rm -f job_file
        ]
        result = await parse_existing_job("/tmp/batch_job.json", timeout=600)
        assert result is None


@pytest.mark.asyncio
async def test_parse_existing_job_zero_pid() -> None:
    """PID=0 in job file is treated as dead (kill -0 0 signals own group)."""
    job = json.dumps({"pid": 0, "log_file": "/tmp/batch.log", "test_name": "T1"})
    with patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh:
        mock_ssh.side_effect = [
            job,   # cat job_file
            "",    # rm -f job_file (pid_alive = "DEAD" without SSH call)
        ]
        result = await parse_existing_job("/tmp/batch_job.json", timeout=600)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: build_batch_cmd
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_batch_cmd_basic() -> None:
    """Verify returns (env_prefix, cmd, preprocessed_tcl) tuple."""
    runner = _make_runner()
    with patch(
        "xcelium_mcp.batch_runner._preprocess_setup_tcl",
        new_callable=AsyncMock,
        return_value=None,
    ):
        env_prefix, cmd, preprocessed_tcl = await build_batch_cmd(
            runner=runner,
            test_name="TEST001",
            sim_mode="rtl",
            extra_args="",
            timeout=600,
            dump_depth=None,
            dump_signals=None,
            dump_window=None,
            sdf_file="",
            sdf_corner="max",
            sim_dir="/sim",
        )
        assert isinstance(env_prefix, str)
        assert isinstance(cmd, str)
        assert "TEST_NAME=" in env_prefix
        assert "TEST001" in env_prefix or "TEST001" in cmd
        assert preprocessed_tcl is None


@pytest.mark.asyncio
async def test_build_batch_cmd_with_preprocessed_tcl() -> None:
    """When preprocess returns a path, env_prefix includes MCP_INPUT_TCL."""
    runner = _make_runner()
    with patch(
        "xcelium_mcp.batch_runner._preprocess_setup_tcl",
        new_callable=AsyncMock,
        return_value="/tmp/setup_modified.tcl",
    ):
        env_prefix, cmd, preprocessed_tcl = await build_batch_cmd(
            runner=runner,
            test_name="TEST002",
            sim_mode="rtl",
            extra_args="",
            timeout=600,
            dump_depth=None,
            dump_signals=None,
            dump_window=None,
            sdf_file="",
            sdf_corner="max",
            sim_dir="/sim",
        )
        assert "MCP_INPUT_TCL=" in env_prefix
        assert preprocessed_tcl == "/tmp/setup_modified.tcl"


@pytest.mark.asyncio
async def test_build_batch_cmd_with_extra_args() -> None:
    """Extra args are appended to the command."""
    runner = _make_runner()
    with patch(
        "xcelium_mcp.batch_runner._preprocess_setup_tcl",
        new_callable=AsyncMock,
        return_value=None,
    ):
        _, cmd, _ = await build_batch_cmd(
            runner=runner,
            test_name="TEST003",
            sim_mode="rtl",
            extra_args="-coverage",
            timeout=600,
            dump_depth=None,
            dump_signals=None,
            dump_window=None,
            sdf_file="",
            sdf_corner="max",
            sim_dir="/sim",
        )
        assert "-coverage" in cmd


@pytest.mark.asyncio
async def test_build_batch_cmd_with_sdf() -> None:
    """SDF override appends extra flags to command."""
    runner = _make_runner()
    with (
        patch(
            "xcelium_mcp.batch_runner._preprocess_setup_tcl",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "xcelium_mcp.batch_runner._handle_sdf_override",
            new_callable=AsyncMock,
            return_value="-sdf_extra_flag",
        ),
    ):
        _, cmd, _ = await build_batch_cmd(
            runner=runner,
            test_name="TEST004",
            sim_mode="gate",
            extra_args="",
            timeout=600,
            dump_depth=None,
            dump_signals=None,
            dump_window=None,
            sdf_file="/path/to/top.sdf",
            sdf_corner="max",
            sim_dir="/sim",
        )
        assert "-sdf_extra_flag" in cmd


# ---------------------------------------------------------------------------
# Tests: launch_nohup_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_nohup_returns_pid() -> None:
    """Verify PID extraction from pid file."""
    with (
        patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh,
        patch(
            "xcelium_mcp.shell_utils.get_user_tmp_dir",
            new_callable=AsyncMock,
            return_value="/tmp/xcelium_mcp_1000",
        ),
    ):
        mock_ssh.side_effect = [
            "",       # nohup launch
            "42",     # cat pid_file
            "",       # rm -f pid_file
            "",       # echo base64 > job_file
            "",       # PID watcher background
        ]
        pid = await launch_nohup_job(
            sim_dir="/sim",
            run_cmd="env TEST_NAME=T1 ./run_sim.sh -test T1 --",
            log_file="/tmp/batch_123.log",
            test_name="T1",
            job_file="/tmp/batch_job.json",
        )
        assert pid == 42


@pytest.mark.asyncio
async def test_launch_nohup_pid_fallback_pgrep() -> None:
    """When pid file is empty, falls back to pgrep."""
    with (
        patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh,
        patch(
            "xcelium_mcp.shell_utils.get_user_tmp_dir",
            new_callable=AsyncMock,
            return_value="/tmp/xcelium_mcp_1000",
        ),
    ):
        mock_ssh.side_effect = [
            "",       # nohup launch
            "",       # cat pid_file (empty)
            "55",     # pgrep fallback
            "",       # rm -f pid_file
            "",       # echo base64 > job_file
            "",       # PID watcher background
        ]
        pid = await launch_nohup_job(
            sim_dir="/sim",
            run_cmd="env TEST_NAME=T1 ./run_sim.sh",
            log_file="/tmp/batch_123.log",
            test_name="T1",
            job_file="/tmp/batch_job.json",
        )
        assert pid == 55


@pytest.mark.asyncio
async def test_launch_nohup_no_pid() -> None:
    """When neither pid file nor pgrep returns a number, pid=0."""
    with (
        patch("xcelium_mcp.batch_runner.ssh_run", new_callable=AsyncMock) as mock_ssh,
        patch(
            "xcelium_mcp.shell_utils.get_user_tmp_dir",
            new_callable=AsyncMock,
            return_value="/tmp/xcelium_mcp_1000",
        ),
    ):
        mock_ssh.side_effect = [
            "",       # nohup launch
            "",       # cat pid_file (empty)
            "",       # pgrep fallback (empty)
            "",       # rm -f pid_file
        ]
        pid = await launch_nohup_job(
            sim_dir="/sim",
            run_cmd="env TEST_NAME=T1 ./run_sim.sh",
            log_file="/tmp/batch_123.log",
            test_name="T1",
            job_file="/tmp/batch_job.json",
        )
        assert pid == 0


# ---------------------------------------------------------------------------
# Tests: watch_pid_and_poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_pid_and_poll_returns_result() -> None:
    """Verify polling result is returned and job file is cleaned."""
    with (
        patch("xcelium_mcp.batch_polling.poll_batch_log", new_callable=AsyncMock) as mock_poll,
        patch("xcelium_mcp.batch_polling.ssh_run", new_callable=AsyncMock) as mock_ssh,
    ):
        mock_poll.return_value = ("PASS: test completed", False)
        mock_ssh.return_value = ""  # rm -f job_file
        result = await watch_pid_and_poll(
            pid=42,
            log_file="/tmp/batch_123.log",
            job_file="/tmp/batch_job.json",
            timeout=600,
        )
        assert result == "PASS: test completed"
        mock_poll.assert_called_once_with("/tmp/batch_123.log", 600)
        mock_ssh.assert_called_once()  # rm -f


# ---------------------------------------------------------------------------
# Tests: resolve_sim_params (pure function, no mocks needed)
# ---------------------------------------------------------------------------


def test_resolve_sim_params_defaults() -> None:
    """Default params with minimal runner."""
    runner = _make_runner()
    params = resolve_sim_params(runner, "rtl")
    assert params["test_args_format"] == "-test {test_name} --"
    assert params["timeout"] == 600
    assert params["dump_depth"] == "all"


def test_resolve_sim_params_mode_override() -> None:
    """Mode-specific config overrides common defaults."""
    runner = _make_runner(
        mode_defaults={
            "common": {"timeout": 120, "dump_depth": "all"},
            "gate": {"timeout": 1800, "dump_depth": "boundary"},
        }
    )
    params = resolve_sim_params(runner, "gate")
    assert params["timeout"] == 1800
    assert params["dump_depth"] == "boundary"


def test_resolve_sim_params_explicit_dump_depth() -> None:
    """Explicit dump_depth overrides mode defaults."""
    runner = _make_runner(
        mode_defaults={
            "gate": {"dump_depth": "boundary"},
        }
    )
    params = resolve_sim_params(runner, "gate", dump_depth="all")
    assert params["dump_depth"] == "all"


def test_resolve_sim_params_extra_args_merge() -> None:
    """Config extra_args and call-time extra_args are merged."""
    runner = _make_runner(
        mode_defaults={
            "rtl": {"extra_args": "-debug"},
        }
    )
    params = resolve_sim_params(runner, "rtl", extra_args="-coverage")
    assert "-debug" in params["extra_args"]
    assert "-coverage" in params["extra_args"]


# ---------------------------------------------------------------------------
# Tests: _resolve_exec_cmd (pure function)
# ---------------------------------------------------------------------------


def test_resolve_exec_cmd_with_shebang() -> None:
    """script_shell=True uses ./script (wrapped in login_shell_cmd)."""
    runner = _make_runner(script_shell=True)
    info = _resolve_exec_cmd(runner, regression=False)
    assert "./run_sim.sh" in info.cmd
    assert info.needs_test_name is True


def test_resolve_exec_cmd_without_shebang() -> None:
    """script_shell=False wraps script invocation with login_shell."""
    runner = _make_runner(script_shell=False)
    info = _resolve_exec_cmd(runner, regression=False)
    assert "/bin/tcsh" in info.cmd
    assert "./run_sim.sh" in info.cmd
    assert info.needs_test_name is True


def test_resolve_exec_cmd_override() -> None:
    """exec_cmd_override takes precedence."""
    runner = _make_runner(exec_cmd_override="custom_cmd --all")
    info = _resolve_exec_cmd(runner, regression=False)
    assert info.cmd == "custom_cmd --all"
    assert info.needs_test_name is False
