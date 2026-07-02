"""Tests for simvision_ops._launch_simvision (F-158).

F-158: start_simvision()/compare_simvision() each re-implemented the same
env_shell/login_shell resolution + source_separately branch + nohup wrapping
inline (near-identical, already drifting in comments). Extracted to a shared
helper that routes through shell_utils.build_eda_command().
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.simvision_ops import _launch_simvision


@pytest.mark.asyncio
async def test_launch_simvision_source_separately_wraps_via_env_shell() -> None:
    runner = {
        "source_separately": True,
        "env_files": ["/opt/cds/cshrc"],
        "env_shell": "/bin/tcsh",
        "login_shell": "/bin/tcsh",
    }
    captured_cmds: list[str] = []

    async def capturing_shell_run(cmd: str, timeout: float = 30) -> str:
        captured_cmds.append(cmd)
        return ""

    with (
        patch("xcelium_mcp.simvision_ops.shell_run", side_effect=capturing_shell_run),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
    ):
        log_file = await _launch_simvision(
            runner, ":1", ["cd /sim/run", "simvision top.shm"], "simvision_start.log",
        )

    assert log_file == "/tmp/mcp_test/simvision_start.log"
    assert len(captured_cmds) == 1
    launch_cmd = captured_cmds[0]
    assert "/bin/tcsh -c '" in launch_cmd
    assert "source /opt/cds/cshrc" in launch_cmd
    assert "setenv DISPLAY :1" in launch_cmd
    assert "cd /sim/run" in launch_cmd
    assert "simvision top.shm" in launch_cmd
    assert "nohup" in launch_cmd
    # DISPLAY must be set after sourcing (build_eda_command sources first) —
    # documented, inert behavior difference from the pre-F-158 manual ordering.
    assert launch_cmd.index("source /opt/cds/cshrc") < launch_cmd.index("setenv DISPLAY")


@pytest.mark.asyncio
async def test_launch_simvision_without_source_separately_uses_login_shell() -> None:
    runner = {"login_shell": "/bin/sh"}
    captured_cmds: list[str] = []

    async def capturing_shell_run(cmd: str, timeout: float = 30) -> str:
        captured_cmds.append(cmd)
        return ""

    with (
        patch("xcelium_mcp.simvision_ops.shell_run", side_effect=capturing_shell_run),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
    ):
        await _launch_simvision(runner, ":2", ["simvision top.shm"], "simvision_compare.log")

    launch_cmd = captured_cmds[0]
    assert "setenv DISPLAY :2" in launch_cmd
    assert "simvision top.shm" in launch_cmd
    assert "source" not in launch_cmd  # no env_files sourcing


@pytest.mark.asyncio
async def test_launch_simvision_uses_given_timeout() -> None:
    runner = {"login_shell": "/bin/sh"}
    captured_timeouts: list[float] = []

    async def capturing_shell_run(cmd: str, timeout: float = 30) -> str:
        captured_timeouts.append(timeout)
        return ""

    with (
        patch("xcelium_mcp.simvision_ops.shell_run", side_effect=capturing_shell_run),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
    ):
        await _launch_simvision(runner, ":1", ["simvision top.shm"], "x.log", launch_timeout=5.0)

    assert captured_timeouts == [5.0]
