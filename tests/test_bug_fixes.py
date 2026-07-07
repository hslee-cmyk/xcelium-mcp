"""Regression tests for bug fixes F-088E/F, F-091, F-094, F-095, F-096.

Each test class targets one bug fix and verifies the specific behaviour
that was broken before the fix.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _MockMCP:
    """Captures tools registered via @mcp.tool() for direct invocation."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


# ---------------------------------------------------------------------------
# F-088 E/F: Makefile grep stderr filtering in auto_detect_runner
# ---------------------------------------------------------------------------

class TestAutoDetectRunnerMakefileGrep:
    """Grep error lines from Makefile must not pollute target selection."""

    @pytest.mark.asyncio
    async def test_grep_error_line_filtered_from_targets(self) -> None:
        """'grep: /path/Makefile: No such file' must not become best_target."""
        from xcelium_mcp.runner_detection import auto_detect_runner

        shell_responses = {
            # grep -lE returns the Makefile path (step 1 — file exists check)
            0: "/sim/Makefile",
            # grep -oE returns a grep error line (step 2 — target extraction)
            1: "grep: /sim/Makefile: No such file or directory",
        }
        call_count = 0

        async def _fake_shell(cmd: str, **kwargs) -> str:
            nonlocal call_count
            resp = shell_responses.get(call_count, "")
            call_count += 1
            return resp

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell):
            result = await auto_detect_runner("/sim")

        # If Makefile candidate was added, best_target must not be "grep:"
        make_candidates = [c for c in result.get("candidates", []) if c.get("runner") == "make"]
        for c in make_candidates:
            assert not c["exec_cmd"].startswith("make grep:"), (
                f"grep error leaked into exec_cmd: {c['exec_cmd']!r}"
            )

    @pytest.mark.asyncio
    async def test_valid_makefile_target_used(self) -> None:
        """When grep succeeds, the first valid target is used as exec_cmd."""
        from xcelium_mcp.runner_detection import auto_detect_runner

        call_count = 0
        responses = [
            "/sim/Makefile",        # grep -lE: file exists
            "sim\ntest\nrun",       # grep -oE: valid targets
            "",                     # find scripts: none
            "",                     # find xrun: none
            "",                     # find irun: none
            "",                     # find python: none
        ]

        async def _fake_shell(cmd: str, **kwargs) -> str:
            nonlocal call_count
            resp = responses[call_count] if call_count < len(responses) else ""
            call_count += 1
            return resp

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell):
            result = await auto_detect_runner("/sim")

        make_candidates = [c for c in result.get("candidates", []) if c.get("runner") == "make"]
        assert make_candidates, "Expected a make candidate"
        assert "make sim" in make_candidates[0]["exec_cmd"]


# ---------------------------------------------------------------------------
# F-091: realpath normalization in run_full_discovery and registry
# ---------------------------------------------------------------------------

class TestRealpathNormalization:
    """sim_dir should be realpath-resolved before registry lookup/write."""

    @pytest.mark.asyncio
    async def test_run_full_discovery_resolves_symlink_path(self) -> None:
        """Symlink path ~/git.clone/ncsim → realpath before registry lookup."""
        from xcelium_mcp.discovery import run_full_discovery

        symlink_input = "/home/user/git.clone/ncsim"
        real_path = "/usrdata/user/projects/ncsim"

        with patch("os.path.realpath", return_value=real_path) as mock_rp, \
             patch("xcelium_mcp.discovery.load_sim_config", new_callable=AsyncMock,
                   return_value={"version": 2}) as mock_load:
            await run_full_discovery(sim_dir=symlink_input, force=False)

        # realpath must be called with the expanded input
        mock_rp.assert_called()
        # load_sim_config must receive the resolved path, not the symlink
        loaded_path = mock_load.call_args[0][0]
        assert loaded_path == real_path, (
            f"load_sim_config received symlink path {loaded_path!r} instead of realpath"
        )

    @pytest.mark.asyncio
    async def test_update_registry_uses_resolved_paths_as_keys(self) -> None:
        """Registry keys must use Path.resolve() — mock resolve() to verify."""
        from pathlib import Path

        from xcelium_mcp.registry import _update_registry_from_config

        # Use paths that look like symlinks resolved by git
        raw_root = "/projects/real"
        sim_dir_arg = "/projects/real/ncsim"

        git_root_bytes = f"{raw_root}\n".encode()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(git_root_bytes, b""))

        registry_written: list[dict] = []

        def _fake_save(reg: dict) -> None:
            registry_written.append(reg)

        def _fake_load() -> dict:
            return {}

        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        # Patch Path.resolve to return a known value so we can assert on keys.
        original_resolve = Path.resolve

        def _mock_resolve(self, *args, **kwargs):
            s = str(self)
            # Map any variant to the canonical resolved path
            if "real" in s:
                return Path(s)  # already canonical in this test
            return original_resolve(self, *args, **kwargs)

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
             patch("xcelium_mcp.registry._save_registry_sync", side_effect=_fake_save), \
             patch("xcelium_mcp.registry._load_registry_sync", side_effect=_fake_load), \
             patch("asyncio.to_thread", side_effect=_fake_to_thread), \
             patch.object(Path, "resolve", _mock_resolve):
            await _update_registry_from_config(sim_dir_arg, "uvm", {"version": 2})

        assert registry_written, "Registry was never written"
        projects = registry_written[0].get("projects", {})
        # project_root key must come from git rev-parse output via Path.resolve()
        assert any("real" in k for k in projects), (
            f"Expected resolved project_root key, got: {list(projects.keys())}"
        )
        # sim_dir key must also appear in environments
        found_envs = next(iter(projects.values())).get("environments", {})
        assert any("ncsim" in k for k in found_envs), (
            f"Expected ncsim in environment keys, got: {list(found_envs.keys())}"
        )


# ---------------------------------------------------------------------------
# F-094: bridge_lifecycle uses absolute script path
# ---------------------------------------------------------------------------

class TestBridgeLifecycleAbsoluteScriptPath:
    """_start_bridge must use {sim_dir}/{script} not ./{script}."""

    @pytest.mark.asyncio
    async def test_script_path_is_absolute_not_relative(self) -> None:
        """Shell command must contain /sim/ncsim/run_sim, not ./run_sim."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        config = {
            "runner": {
                "script": "run_sim",
                "script_shell": "/bin/sh",
                "env_shell": "/bin/sh",
                "login_shell": "/bin/sh",
                "run_dir": "run",
                "script_has_cd": False,
                "args_format": "-test {test_name}",
            },
            "bridge": {"port": 9876, "tcl_path": "/opt/mcp_bridge.tcl"},
            "setup_tcls": {"rtl": "scripts/setup_rtl.tcl"},
        }

        shell_calls: list[str] = []

        async def _fake_shell(cmd: str, **kwargs) -> str:
            shell_calls.append(cmd)
            return ""

        async def _fake_get_user_tmp() -> str:
            return "/tmp/mcp_test"

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
             patch("xcelium_mcp.bridge_lifecycle.resolve_sim_params",
                   return_value={
                       "test_args_format": "-test {test_name}",
                       "extra_args": "",
                       "timeout": 2,   # tcp_deadline=1 → range(0) → no retry loop
                       "dump_args": "",
                   }):
            # Will fail at bridge connect — that's fine, we only check shell_calls
            try:
                await _start_bridge(
                    sim_dir="/sim/ncsim",
                    config=config,
                    test_name="TOP015",
                    setup_tcl="scripts/setup_rtl.tcl",
                    sim_mode="rtl",
                    timeout=2,
                    bridges=None,
                )
            except Exception:
                pass

        # Find the nohup launch command
        launch_cmds = [c for c in shell_calls if "nohup" in c or "run_sim" in c]
        assert launch_cmds, f"No launch command found in: {shell_calls}"
        launch_cmd = launch_cmds[0]

        assert "./run_sim" not in launch_cmd, (
            f"Relative path ./run_sim found — should be absolute: {launch_cmd!r}"
        )
        assert "/sim/ncsim/run_sim" in launch_cmd, (
            f"Absolute path /sim/ncsim/run_sim not found: {launch_cmd!r}"
        )


# ---------------------------------------------------------------------------
# F-095: simvision_connect attach — no duplicate TCP connect when already connected
# ---------------------------------------------------------------------------

class TestSimvisionConnectAttachDedup:
    """attach must return early when simvision bridge is already connected."""

    def _make_register(self, mock_bridges, connect_fn):
        """Register simvision tools with a fake connect_simulator_fn closure."""
        from xcelium_mcp.tools.simvision import register

        mock_mcp = _MockMCP()
        register(
            mock_mcp,
            mock_bridges,
            waveform_add_impl_fn=AsyncMock(),
            connect_simulator_fn=connect_fn,
            csv_cache=MagicMock(),
        )
        return mock_mcp

    @pytest.mark.asyncio
    async def test_attach_when_already_connected_returns_info(self) -> None:
        """Second attach call must NOT trigger a new TCP connection."""
        # Simulate already-connected simvision bridge
        fake_bridge = MagicMock()
        fake_bridge.connected = True
        fake_bridge.host = "localhost"
        fake_bridge.port = 9877

        mock_bridges = MagicMock()
        mock_bridges.simvision_raw = fake_bridge

        connect_called = False

        async def _fake_connect(**kwargs) -> str:
            nonlocal connect_called
            connect_called = True
            return "connected"

        with patch("xcelium_mcp.tools.simvision.start_simvision", new_callable=AsyncMock):
            mock_mcp = self._make_register(mock_bridges, _fake_connect)
            result = await mock_mcp.tools["simvision_connect"](action="attach", port=0)

        assert not connect_called, "connect_simulator_fn must not be called when already connected"
        assert "localhost" in result
        assert "9877" in result
        assert "reusing" in result.lower() or "already" in result.lower()

    @pytest.mark.asyncio
    async def test_attach_when_not_connected_proceeds(self) -> None:
        """When no bridge is connected, attach must call connect_simulator_fn."""
        mock_bridges = MagicMock()
        mock_bridges.simvision_raw = None

        connect_called = False

        async def _fake_connect(**kwargs) -> str:
            nonlocal connect_called
            connect_called = True
            return "Connected to simvision at localhost:9877"

        with patch("xcelium_mcp.tools.simvision.start_simvision", new_callable=AsyncMock):
            mock_mcp = self._make_register(mock_bridges, _fake_connect)
            result = await mock_mcp.tools["simvision_connect"](action="attach", port=0)

        assert connect_called, "connect_simulator_fn must be called when not connected"
        assert "Connected" in result


# ---------------------------------------------------------------------------
# F-096: _start_bridge port occupancy check
# ---------------------------------------------------------------------------

class TestStartBridgePortCheck:
    """_start_bridge must detect non-xmsim port occupancy and error early."""

    @pytest.mark.asyncio
    async def test_port_occupied_by_simvision_returns_error(self) -> None:
        """If port 9876 is held by simvision, return an error before launching xmsim."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        config = {
            "runner": {
                "script": "run_sim",
                "script_shell": "/bin/sh",
                "env_shell": "/bin/sh",
                "login_shell": "/bin/sh",
                "run_dir": "run",
                "script_has_cd": False,
                "args_format": "-test {test_name}",
            },
            "bridge": {"port": 9876, "tcl_path": "/opt/mcp_bridge.tcl"},
            "setup_tcls": {"rtl": "scripts/setup_rtl.tcl"},
        }

        async def _fake_shell(cmd: str, **kwargs) -> str:
            if "pgrep" in cmd:
                return ""                          # xmsim not running
            if "grep" in cmd and "oE" in cmd:
                return "pid=1234"                  # PID extraction (ss format)
            if "grep" in cmd and "9876" in cmd:
                return "LISTEN 0 128 *:9876 *:* users:((\"simvision\",pid=1234,fd=7))"
            if "ps -p" in cmd:
                return "simvision"
            return ""

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"):
            result = await _start_bridge(
                sim_dir="/sim/ncsim",
                config=config,
                test_name="TOP015",
                setup_tcl="scripts/setup_rtl.tcl",
                sim_mode="rtl",
                timeout=5,
                bridges=None,
            )

        assert result.startswith("ERROR"), f"Expected ERROR, got: {result!r}"
        assert "9876" in result
        assert "simvision" in result.lower() or "1234" in result

    @pytest.mark.asyncio
    async def test_port_free_proceeds_to_launch(self) -> None:
        """When port is free, _start_bridge proceeds past the port check."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        config = {
            "runner": {
                "script": "run_sim",
                "script_shell": "/bin/sh",
                "env_shell": "/bin/sh",
                "login_shell": "/bin/sh",
                "run_dir": "run",
                "script_has_cd": False,
                "args_format": "-test {test_name}",
            },
            "bridge": {"port": 9876, "tcl_path": "/opt/mcp_bridge.tcl"},
            "setup_tcls": {"rtl": "scripts/setup_rtl.tcl"},
        }

        async def _fake_shell(cmd: str, **kwargs) -> str:
            if "pgrep" in cmd:
                return ""   # xmsim not running
            if "grep" in cmd and "9876" in cmd:
                return ""   # port free (ss or netstat output is empty)
            return ""

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
             patch("xcelium_mcp.bridge_lifecycle.resolve_sim_params",
                   return_value={
                       "test_args_format": "-test {test_name}",
                       "extra_args": "",
                       "timeout": 2,   # tcp_deadline=1 → range(0) → no retry loop
                       "dump_args": "",
                   }):
            try:
                result = await _start_bridge(
                    sim_dir="/sim/ncsim",
                    config=config,
                    test_name="TOP015",
                    setup_tcl="scripts/setup_rtl.tcl",
                    sim_mode="rtl",
                    timeout=2,
                    bridges=None,
                )
            except Exception:
                result = ""  # bridge connect timeout expected — that's fine

        # Must NOT be a port-occupancy error
        assert "occupied" not in result.lower(), f"Unexpected port error: {result!r}"

    @pytest.mark.asyncio
    async def test_port_occupied_by_xmsim_is_not_blocked(self) -> None:
        """If port is held by xmsim itself, the pgrep check catches it first."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        config = {
            "runner": {
                "script": "run_sim", "script_shell": "/bin/sh",
                "env_shell": "/bin/sh", "login_shell": "/bin/sh",
                "run_dir": "run", "script_has_cd": False,
                "args_format": "-test {test_name}",
            },
            "bridge": {"port": 9876, "tcl_path": "/opt/mcp_bridge.tcl"},
            "setup_tcls": {"rtl": "scripts/setup_rtl.tcl"},
        }

        async def _fake_shell(cmd: str, **kwargs) -> str:
            if "pgrep" in cmd:
                return "5678 xmsim -run TOP015"   # xmsim IS running
            return ""

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"):
            result = await _start_bridge(
                sim_dir="/sim/ncsim",
                config=config,
                test_name="TOP015",
                setup_tcl="scripts/setup_rtl.tcl",
                sim_mode="rtl",
                timeout=5,
                bridges=None,
            )

        # pgrep catches xmsim running — error must mention xmsim, not "occupied"
        assert "xmsim" in result
        assert "occupied" not in result.lower()


# ---------------------------------------------------------------------------
# F-097: port check works on hosts without ss (netstat fallback + PID parsing)
# ---------------------------------------------------------------------------

class TestPortCheckNetstatFallback:
    """Port occupancy check must work when ss is absent (netstat only env)."""

    _config = {
        "runner": {
            "script": "run_sim", "script_shell": "/bin/sh",
            "env_shell": "/bin/sh", "login_shell": "/bin/sh",
            "run_dir": "run", "script_has_cd": False,
            "args_format": "-test {test_name}",
        },
        "bridge": {"port": 9876, "tcl_path": "/opt/mcp_bridge.tcl"},
        "setup_tcls": {"rtl": "scripts/setup_rtl.tcl"},
    }

    @pytest.mark.asyncio
    async def test_netstat_format_pid_detected(self) -> None:
        """netstat N/procname PID format must be parsed correctly."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        async def _fake_shell(cmd: str, **kwargs) -> str:
            if "pgrep" in cmd:
                return ""
            if "grep" in cmd and "oE" in cmd:
                return "1234/simvision"     # netstat PID format
            if "grep" in cmd and "9876" in cmd:
                return "tcp 0 0 0.0.0.0:9876 0.0.0.0:* LISTEN 1234/simvision"
            if "ps -p" in cmd:
                return "simvision"
            return ""

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"):
            result = await _start_bridge(
                sim_dir="/sim/ncsim", config=self._config,
                test_name="TOP015", setup_tcl="scripts/setup_rtl.tcl",
                sim_mode="rtl", timeout=5, bridges=None,
            )

        assert result.startswith("ERROR"), f"Expected ERROR, got: {result!r}"
        assert "9876" in result
        assert "simvision" in result.lower() or "1234" in result

    @pytest.mark.asyncio
    async def test_no_listeners_command_port_free(self) -> None:
        """When both ss and netstat return empty for the port, proceed normally."""
        from xcelium_mcp.bridge_lifecycle import _start_bridge

        async def _fake_shell(cmd: str, **kwargs) -> str:
            if "pgrep" in cmd:
                return ""
            if "grep" in cmd and "9876" in cmd:
                return ""   # port free regardless of ss or netstat
            return ""

        with patch("xcelium_mcp.bridge_lifecycle.shell_run", side_effect=_fake_shell), \
             patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir",
                   new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
             patch("xcelium_mcp.bridge_lifecycle.resolve_sim_params",
                   return_value={
                       "test_args_format": "-test {test_name}",
                       "extra_args": "", "timeout": 2, "dump_args": "",
                   }):
            try:
                result = await _start_bridge(
                    sim_dir="/sim/ncsim", config=self._config,
                    test_name="TOP015", setup_tcl="scripts/setup_rtl.tcl",
                    sim_mode="rtl", timeout=2, bridges=None,
                )
            except Exception:
                result = ""

        assert "occupied" not in result.lower()
