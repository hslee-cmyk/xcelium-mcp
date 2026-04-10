"""Simulation lifecycle management tools."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_lifecycle import start_bridge_simulation
from xcelium_mcp.bridge_manager import BridgeManager, scan_ready_files
from xcelium_mcp.discovery import resolve_sim_dir, run_full_discovery
from xcelium_mcp.registry import config_action, load_sim_config
from xcelium_mcp.shell_utils import UserInputRequired, get_user_tmp_dir, ssh_run
from xcelium_mcp.tcl_bridge import BRIDGE_ERRORS, TclBridge, TclError
from xcelium_mcp.test_resolution import resolve_test_name

# ---------------------------------------------------------------------------
# Module-level helpers (don't need bridges closure)
# ---------------------------------------------------------------------------

async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type."""
    entries = await scan_ready_files(target=target)
    if entries:
        return entries[0]
    return 0, target


async def _read_bridge_type(port: int) -> str:
    """Read bridge type from ready file for given port."""
    user_tmp = await get_user_tmp_dir()
    r = await ssh_run(f"cat {user_tmp}/bridge_ready_{port} || true")
    parts = r.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return "xmsim"


# ---------------------------------------------------------------------------
# Helper that needs bridges parameter explicitly
# ---------------------------------------------------------------------------

async def _auto_connect_all(bridges: BridgeManager, host: str, timeout: float) -> str:
    """Scan all ready files, connect to each, assign to appropriate slot."""
    entries = await scan_ready_files()
    if not entries:
        return "No bridges found. Run sim_bridge_run or simvision_start first."

    results = []
    for p, btype in entries:
        bridge = TclBridge(host=host, port=p, timeout=timeout)
        try:
            ping = await bridge.connect()
            if btype == "simvision":
                bridges.set_simvision(bridge)
            else:
                bridges.set_xmsim(bridge)
            results.append(f"  {btype}:{p} (ping={ping})")
        except BRIDGE_ERRORS as e:
            results.append(f"  {btype}:{p} FAILED ({e})")

    if not results:
        return "No bridges found."
    return "Connected:\n" + "\n".join(results)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP, bridges: BridgeManager) -> dict:
    """Register simulation lifecycle tools (12 tools)."""

    @mcp.tool()
    async def list_tests(sim_dir: str = "", pattern: str = "") -> str:
        """List available test names using test_discovery.command from registry.

        Args:
            sim_dir: Simulation directory. Empty = registry default.
            pattern: Filter pattern. Empty = all tests.
        """
        try:
            resolved_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        config = await load_sim_config(resolved_dir)
        if not config:
            return "ERROR: No config. Run sim_discover first."

        discovery = config.get("test_discovery", {})
        cached = discovery.get("cached_tests", [])

        if not cached:
            cmd = discovery.get("command", "")
            if not cmd:
                return "ERROR: test_discovery.command not configured.\nSet via: mcp_config set test_discovery.command '<command>'"
            r = await ssh_run(f"cd {resolved_dir} && {cmd}", timeout=30)
            cached = [t.strip() for t in r.strip().splitlines() if t.strip()]
            if cached:
                # Cache via config_action (write centralization)
                await config_action("set", "config", "test_discovery.cached_tests",
                                    json.dumps(cached))
                await config_action("set", "config", "test_discovery.cached_at",
                                    datetime.now().isoformat())

        if pattern:
            cached = [t for t in cached if pattern in t]

        if not cached:
            return f"No tests found{f' (pattern={pattern})' if pattern else ''}."

        return f"Tests ({len(cached)} found):\n" + "\n".join(f"  {t}" for t in sorted(cached))

    @mcp.tool()
    async def sim_discover(
        sim_dir: str = "",
        force: bool = False,
        top_module: str = "",
    ) -> str:
        """Discover simulation environment and register in mcp_registry.

        Detects: sim_dir, TB type, runner, shell/EDA env, mcp_bridge.tcl,
        setup TCLs, EDA tool paths, bridge port, $sdf_annotate guards (v4.3).

        Args:
            sim_dir:    Explicit simulation directory. Empty = auto-discover.
            force:      Re-detect even if registry already exists.
            top_module: Top module name for SDF analysis. Empty = auto-detect from script.
        """
        try:
            return await run_full_discovery(sim_dir, force, top_module=top_module)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

    @mcp.tool()
    async def mcp_config(
        action: str = "show",
        file: str = "config",
        key: str = "",
        value: str = "",
    ) -> str:
        """View or modify xcelium-mcp registry/config via dot-notation keys.

        Args:
            action: "show" (full dump), "get" (read key), "set" (write key), "delete" (remove key).
            file:   "config" (.mcp_sim_config.json), "registry" (mcp_registry.json),
                    or "checkpoint" (checkpoints/manifest.json).
            key:    Dot-notation path (e.g. "runner.default_mode", "checkpoints.L1_TOP015").
            value:  Value for 'set' action. Auto-parsed: "9876" to int, "true" to bool.
        """
        try:
            return await config_action(action, file, key, value)
        except RuntimeError as e:
            return f"ERROR: {e}"

    @mcp.tool()
    async def sim_bridge_run(
        test_name: str,
        sim_dir: str = "",
        sim_mode: str = "",
        timeout: int = 120,
        extra_args: str = "",
        dump_depth: str = "",
    ) -> str:
        """Start simulation in bridge (interactive) mode. Compile + launch + connect bridge.

        After this tool returns, use sim_run/get_signal_value/bisect_signal for debugging.
        For batch (non-interactive) runs, use sim_batch_run instead.

        Args:
            test_name:  Required — test to run. Short name OK (e.g. "TOP015").
            sim_dir:    Simulation dir. Empty = registry default.
            sim_mode:   "rtl"|"gate"|"ams_rtl"|"ams_gate". Empty = default_mode.
            timeout:    Max seconds to wait for bridge ready.
            extra_args: 1-shot extra simulation arguments (not saved to registry).
            dump_depth: "boundary"|"all"|"" (auto from mode_defaults). v4.3.
        """
        try:
            test_name = await resolve_test_name(test_name, sim_dir)
            return await start_bridge_simulation(
                test_name, sim_dir, sim_mode, timeout,
                extra_args=extra_args, bridges=bridges, dump_depth=dump_depth,
            )
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"
        except RuntimeError as e:
            return f"ERROR: {e}"

    @mcp.tool()
    async def connect_simulator(
        host: str = "localhost",
        port: int = 0,
        target: str = "auto",
        timeout: float = 30.0,
    ) -> str:
        """Connect to simulator bridge(s).

        v4.1: Multi-bridge support. Reads ready file for port + type auto-detection.

        Args:
            host:    Bridge host (default localhost).
            port:    Bridge port. 0 = auto-detect from ready files.
            target:  "xmsim" | "simvision" | "auto". auto = ready file type.
                     port=0 + target=auto → scan all ready files, connect each to slot.
            timeout: Connection timeout in seconds.
        """
        if port == 0 and target == "auto":
            return await _auto_connect_all(bridges, host, timeout)

        if port == 0:
            port, detected_type = await _find_ready_file(target)
            if port == 0:
                return f"ERROR: No {target} bridge found in ready files."
            target = detected_type

        if target == "auto":
            target = await _read_bridge_type(port)

        bridge = TclBridge(host=host, port=port, timeout=timeout)
        try:
            ping = await bridge.connect()
        except BRIDGE_ERRORS as e:
            return f"ERROR: Connection failed: {type(e).__name__}: {e}"

        if target == "simvision":
            bridges.set_simvision(bridge)
        else:
            bridges.set_xmsim(bridge)

        try:
            where = await bridge.execute("where")
        except TclError:
            where = "(unknown)"

        return f"Connected to {target} at {host}:{port} (ping={ping})\nCurrent position: {where}"

    @mcp.tool()
    async def sim_disconnect(
        action: str = "bridge",
        target: str = "all",
    ) -> str:
        """Disconnect or shutdown simulator.

        Args:
            action: "bridge" — disconnect bridge connection only (sim keeps running).
                    "shutdown" — safely shutdown simulator, preserving SHM data.
                    WARNING: Always use "shutdown" when ending a debug session.
                    Plain disconnect or pkill will lose SHM data.
            target: "xmsim" | "simvision" | "all" (default: all for bridge, xmsim for shutdown).
        """
        if action == "bridge":
            results = []
            if target in ("xmsim", "all") and bridges.xmsim_raw and bridges.xmsim_raw.connected:
                await bridges.xmsim_raw.disconnect()
                bridges.set_xmsim(None)
                results.append("xmsim: disconnected")
            if target in ("simvision", "all") and bridges.simvision_raw and bridges.simvision_raw.connected:
                await bridges.simvision_raw.disconnect()
                bridges.set_simvision(None)
                results.append("simvision: disconnected")
            return "\n".join(results) if results else f"No {target} bridge connected."

        elif action == "shutdown":
            shutdown_target = target if target != "all" else "xmsim"
            user_tmp = await get_user_tmp_dir()
            if shutdown_target == "simvision":
                bridge = bridges.simvision
                port = bridge.port if hasattr(bridge, 'port') else 0
                try:
                    resp = await bridge.execute_safe("__SHUTDOWN__")
                    return f"SimVision shutdown: {resp.body}"
                except (ConnectionError, asyncio.TimeoutError):
                    return "SimVision shutdown completed (connection closed)."
                finally:
                    bridges.set_simvision(None)
                    if port:
                        await ssh_run(f"rm -f {user_tmp}/bridge_ready_{port}")
            else:
                bridge = bridges.xmsim
                port = bridge.port if hasattr(bridge, 'port') else 0
                try:
                    resp = await bridge.execute_safe("__SHUTDOWN__")
                    return f"Simulator shutdown: {resp.body}"
                except (ConnectionError, asyncio.TimeoutError):
                    return "Simulator shutdown completed (connection closed)."
                finally:
                    bridges.set_xmsim(None)
                    if port:
                        await ssh_run(f"rm -f {user_tmp}/bridge_ready_{port}")

        else:
            return f"ERROR: Unknown action '{action}'. Use 'bridge' or 'shutdown'."

    _DURATION_RE = re.compile(r'^\d+\s*(ns|us|ms|s|ps|fs)?$', re.IGNORECASE)

    @mcp.tool()
    async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
        """Run the simulation, optionally for a specified duration.

        Args:
            duration: Simulation time to run (e.g. "100ns", "1us"). Empty = run until breakpoint or end.
            timeout: MCP response timeout in seconds (default 600s for gate-level sim support).
        """
        if duration and not _DURATION_RE.fullmatch(duration.strip()):
            return (
                f"ERROR: Invalid duration {duration!r}. "
                "Expected format like '100ns', '1us', '500ps'."
            )
        bridge = bridges.xmsim
        # Single round-trip: run + where combined in Tcl
        where = await bridge.execute(f"__RUN_AND_REPORT__ {duration}", timeout=timeout)
        return f"Simulation advanced. Current position: {where}"

    @mcp.tool()
    async def sim_stop() -> str:
        """Stop a running simulation."""
        bridge = bridges.xmsim
        await bridge.execute("stop")
        return "Simulation stopped."

    @mcp.tool()
    async def sim_restart() -> str:
        """Restart the simulation from time 0.

        Tries run -clean first, then snapshot restore, then plain restart.
        Returns method used: run-clean | snapshot | plain.
        """
        bridge = bridges.xmsim
        result = await bridge.execute("__RESTART__")
        return f"Simulation restarted to time 0. ({result})"

    @mcp.tool()
    async def execute_tcl(
        tcl_cmd: str,
        timeout: int = 30,
        target: str = "auto",
    ) -> str:
        """Execute arbitrary Tcl command in the connected SimVision bridge session.

        Returns raw Tcl output. Raises if not connected or command times out.
        Use for commands not covered by dedicated tools: database -open, probe -create, etc.

        WARNING: State-changing commands (finish, exit, restart) can cause unintended
        termination — caller's responsibility. Prefer dedicated tools when available.

        Args:
            tcl_cmd: Tcl command to execute (single or multi-line).
            timeout: Response timeout in seconds.
            target:  "xmsim" | "simvision" | "auto" (default: auto).
        """
        # Security: block dangerous Tcl commands that could execute arbitrary OS commands
        _TCL_DENYLIST = ["exec", "open", "file delete", "file rename", "exit", "source",
                         "eval", "interp", "package", "load", "uplevel", "after"]
        # Normalize whitespace: collapse tabs/multiple spaces to single space
        cmd_normalized = re.sub(r'[ \t]+', ' ', tcl_cmd).strip().lower()
        # Split on ; and newline to check each segment independently
        for segment in re.split(r'[;\n]', cmd_normalized):
            first_token = segment.strip().split(' ')[0] if segment.strip() else ""
            for denied in _TCL_DENYLIST:
                denied_parts = denied.split()
                # Single-word denylist entry: match first token exactly
                if len(denied_parts) == 1:
                    if first_token == denied_parts[0]:
                        return f"ERROR: Tcl command '{denied}' is blocked for security. Use dedicated MCP tools instead."
                # Multi-word denylist entry: match first N tokens
                else:
                    seg_tokens = segment.strip().split()
                    if len(seg_tokens) >= len(denied_parts) and seg_tokens[:len(denied_parts)] == denied_parts:
                        return f"ERROR: Tcl command '{denied}' is blocked for security. Use dedicated MCP tools instead."
        # S-6 fix: also block embedded [exec ...] and [open ...] in Tcl substitution brackets
        from xcelium_mcp.shell_utils import is_safe_tcl_string
        if not is_safe_tcl_string(tcl_cmd):
            return "ERROR: Tcl command contains embedded [exec] or [open] — blocked for security."

        bridge = bridges.get_bridge(target)
        return await bridge.execute(tcl_cmd, timeout=float(timeout))

    @mcp.tool()
    async def sim_status(target: str = "auto") -> str:
        """Get current simulation status (time, scope, state).

        Args:
            target: "xmsim" | "simvision" | "auto" (default: auto).
        """
        bridge = bridges.get_bridge(target)
        # Single round-trip: where + scope combined in Tcl
        return await bridge.execute("__STATUS__")

    return {"connect_simulator": connect_simulator}
