"""Xcelium MCP Server — FastMCP server with 25 tools for SimVision control."""

from __future__ import annotations

import asyncio
import textwrap

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.tcl_bridge import TclBridge, TclError
from xcelium_mcp.screenshot import ps_to_png
import xcelium_mcp.csv_cache as csv_cache
import xcelium_mcp.debug_tools as debug_tools
import xcelium_mcp.checkpoint_manager as checkpoint_manager
from xcelium_mcp.sim_runner import (
    UserInputRequired,
    _build_redirect,
    _detect_vnc_display,
    _get_default_sim_dir,
    _load_or_detect_runner,
    _login_shell_cmd,
    _parse_shm_path,
    _parse_time_ns,
    _resolve_exec_cmd,
    _resolve_test_name,
    _run_batch_regression,
    _run_batch_single,
    _update_registry_from_config,
    config_action,
    load_registry,
    load_sim_config,
    run_full_discovery,
    ssh_run,
    start_simulation,
)

# ---------------------------------------------------------------------------
# Server & global bridge instances (v4.1: dual slot)
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "xcelium-mcp",
    instructions="MCP server for Cadence Xcelium/SimVision simulator control",
)

# v4.1: Independent bridge slots (max 1 each)
_xmsim_bridge: TclBridge | None = None
_simvision_bridge: TclBridge | None = None


def _get_xmsim_bridge() -> TclBridge:
    """Return the xmsim bridge or raise."""
    if _xmsim_bridge is None or not _xmsim_bridge.connected:
        raise ConnectionError(
            "Not connected to xmsim. Call sim_start or connect_simulator(target='xmsim') first."
        )
    return _xmsim_bridge


def _get_simvision_bridge() -> TclBridge:
    """Return the SimVision bridge or raise."""
    if _simvision_bridge is None or not _simvision_bridge.connected:
        raise ConnectionError(
            "Not connected to SimVision. Call simvision_start or connect_simulator(target='simvision') first."
        )
    return _simvision_bridge


def _get_bridge(target: str = "auto") -> TclBridge:
    """Return bridge by target. target='auto' → xmsim priority."""
    if target == "xmsim":
        return _get_xmsim_bridge()
    if target == "simvision":
        return _get_simvision_bridge()
    # auto: xmsim priority
    if _xmsim_bridge and _xmsim_bridge.connected:
        return _xmsim_bridge
    if _simvision_bridge and _simvision_bridge.connected:
        return _simvision_bridge
    raise ConnectionError(
        "Not connected. Call sim_start or connect_simulator first."
    )


# ===================================================================
# v4.1 Phase 2 — SimVision GUI + list_tests + live waveform
# ===================================================================


@mcp.tool()
async def database_open(shm_path: str, name: str = "") -> str:
    """Open SHM database. Uses correct syntax based on bridge type.

    SimVision: 'database open path'
    xmsim:     'database -open path -shm'
    Routes to SimVision bridge first, falls back to xmsim.
    """
    # SimVision bridge first
    if _simvision_bridge and _simvision_bridge.connected:
        bridge = _simvision_bridge
        name_opt = f" -name {name}" if name else ""
        try:
            result = await bridge.execute(f"database open {shm_path}{name_opt}")
            return f"Database opened (SimVision): {result}"
        except TclError as e:
            return f"ERROR: SimVision database open failed: {e}"

    # xmsim fallback
    try:
        bridge = _get_xmsim_bridge()
        result = await bridge.execute(f"database -open {shm_path} -shm")
        return f"Database opened (xmsim): {result}"
    except (ConnectionError, TclError) as e:
        return f"ERROR: Could not open database: {e}"


@mcp.tool()
async def simvision_setup(
    shm_path: str = "",
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
) -> str:
    """One-shot SimVision setup: open SHM + create waveform + add signals + zoom.

    Args:
        shm_path:   SHM database path. Empty = skip database open.
        signals:    Signal paths to add to waveform.
        zoom_start: Zoom start time. Empty = full range.
        zoom_end:   Zoom end time. Empty = full range.
    """
    bridge = _get_simvision_bridge()
    results = []

    if shm_path:
        db_result = await database_open(shm_path)
        results.append(db_result)

    # waveform_add_signals handles window creation + dedup
    if signals:
        add_result = await waveform_add_signals(signals=signals)
        results.append(add_result)

    if zoom_start and zoom_end:
        try:
            await bridge.execute(f"waveform xview limits {zoom_start} {zoom_end}")
            results.append(f"Zoomed to {zoom_start} – {zoom_end}")
        except TclError as e:
            results.append(f"Zoom failed: {e}")

    return "\n".join(results) if results else "No actions performed."


@mcp.tool()
async def list_tests(sim_dir: str = "", pattern: str = "") -> str:
    """List available test names using test_discovery.command from registry.

    Args:
        sim_dir: Simulation directory. Empty = registry default.
        pattern: Filter pattern. Empty = all tests.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        try:
            await run_full_discovery(sim_dir)
            resolved_dir = await _get_default_sim_dir()
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

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
            from datetime import datetime
            await config_action("set", "config", "test_discovery.cached_tests",
                                __import__("json").dumps(cached))
            await config_action("set", "config", "test_discovery.cached_at",
                                datetime.now().isoformat())

    if pattern:
        cached = [t for t in cached if pattern in t]

    if not cached:
        return f"No tests found{f' (pattern={pattern})' if pattern else ''}."

    return f"Tests ({len(cached)} found):\n" + "\n".join(f"  {t}" for t in sorted(cached))


@mcp.tool()
async def simvision_start(
    test_name: str = "",
    shm_path: str = "",
    display: str = "",
    sim_dir: str = "",
) -> str:
    """Start SimVision or connect to already running instance.

    Args:
        test_name: Test name for SHM lookup. Empty = latest SHM.
        shm_path:  Explicit SHM path (overrides test_name).
        display:   X11 DISPLAY. Empty = auto-detect user's VNC session.
        sim_dir:   Simulation directory. Empty = registry default.
    """
    global _simvision_bridge

    # 0. Disconnect existing (max 1 constraint)
    if _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None

    # 1. Check existing SimVision bridge → auto-connect
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "simvision":
            port = int(parts[0])
            bridge = TclBridge(host="localhost", port=port)
            try:
                ping = await bridge.connect()
                _simvision_bridge = bridge
                return f"SimVision already running — connected to port {port} (ping={ping})"
            except Exception:
                pass

    # 2. Resolve sim_dir + config
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        return "ERROR: No sim_dir. Run sim_discover first."
    config = await load_sim_config(resolved_dir)
    runner = config.get("runner", {}) if config else {}

    # 3. Resolve SHM (glob)
    if not shm_path:
        if test_name:
            test_name = await _resolve_test_name(test_name, resolved_dir)
        dump_dir = f"{resolved_dir}/dump"
        if test_name:
            r2 = await ssh_run(f"ls -td {dump_dir}/*{test_name}*.shm 2>/dev/null | head -1")
            if not r2.strip():
                r2 = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        else:
            r2 = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        shm_path = r2.strip() if r2.strip() else ""

    # 4. Display
    if not display:
        display = await _detect_vnc_display()
    if not display:
        return (
            "ERROR: No VNC display found.\n"
            "Start VNC: 'vncserver'\nOr specify: simvision_start(display=':1')"
        )
    display_check = await ssh_run(f"xdpyinfo -display {display} 2>/dev/null | head -1")
    if not display_check.strip():
        return f"ERROR: Display {display} not accessible.\nCheck VNC: 'vncserver -list'"

    # 5. Get run_dir
    run_dir = runner.get("run_dir", "run")
    run_dir_path = f"{resolved_dir}/{run_dir}"
    exists = await ssh_run(f"test -d {run_dir_path} && echo YES || echo NO")
    if "YES" not in exists:
        return f"ERROR: run_dir not found: {run_dir_path}. Set via: mcp_config set runner.run_dir <path>"

    # 6. Build + launch
    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", runner.get("login_shell", "/bin/csh"))
    login_shell = runner.get("login_shell", "/bin/sh")

    shm_arg = f" {shm_path}" if shm_path else ""
    inner_parts = [f"setenv DISPLAY {display}"]
    if runner.get("source_separately") and env_files:
        for ef in env_files:
            inner_parts.append(f"source {ef}")
    inner_parts.append(f"cd {run_dir_path}")
    inner_parts.append(f"simvision{shm_arg}")
    inner_cmd = "; ".join(inner_parts)

    if runner.get("source_separately") and env_files:
        shell_cmd = f"{env_shell} -c '{inner_cmd}'"
    else:
        shell_cmd = _login_shell_cmd(login_shell, inner_cmd)

    log_file = "/tmp/simvision_start.log"
    cmd = f"(nohup {shell_cmd} {_build_redirect(log_file)} < /dev/null &)"
    await ssh_run(cmd, timeout=15)

    # 7. Wait for bridge ready + auto-connect
    for i in range(30):
        await __import__("asyncio").sleep(2)
        r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
        for line in r.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "simvision":
                port = int(parts[0])
                bridge = TclBridge(host="localhost", port=port)
                try:
                    ping = await bridge.connect()
                    _simvision_bridge = bridge
                    return (
                        f"SimVision started and connected.\n"
                        f"  display: {display}\n"
                        f"  port: {port}\n"
                        f"  run_dir: {run_dir_path}\n"
                        f"  shm: {shm_path or '(none)'}\n"
                        f"  log: {log_file}"
                    )
                except Exception:
                    continue

    log_tail = await ssh_run(f"tail -10 {log_file} 2>/dev/null")
    return f"ERROR: SimVision bridge not ready after 60s.\nLog:\n{log_tail}"


@mcp.tool()
async def simvision_live(
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
    auto_reload: bool = True,
) -> str:
    """Connect SimVision to running xmsim for live waveform viewing.

    Requires both xmsim and SimVision bridges connected.
    Opens xmsim's SHM in SimVision, adds signals, enables auto-reload.
    """
    xmsim = _get_xmsim_bridge()
    sv = _get_simvision_bridge()
    results = []

    # 1. Get xmsim SHM info + sim time
    shm_info = ""
    sim_time = ""
    try:
        shm_info = await xmsim.execute("database -list")
        sim_time = await xmsim.execute("where")
        results.append(f"xmsim at {sim_time.strip()}, SHM: {shm_info.strip()}")
    except TclError as e:
        results.append(f"xmsim info: {e}")

    # 2. Open SHM in SimVision using _parse_shm_path helper
    if shm_info.strip():
        shm_path = _parse_shm_path(shm_info)
        if not shm_path:
            return (
                f"ERROR: Could not parse SHM path from xmsim database list:\n{shm_info}\n"
                "Open SHM manually: database_open(shm_path='...')"
            )
        try:
            await sv.execute(f"database open {shm_path}")
            results.append(f"SimVision opened: {shm_path}")
        except TclError as e:
            results.append(f"SHM open failed: {e}")

    # 3. Add signals (reuses waveform_add_signals — window auto-create + dedup)
    if signals:
        add_result = await waveform_add_signals(signals=signals)
        results.append(add_result)

    # 4. Zoom — auto-compute from sim time if zoom_start/zoom_end not given
    if not zoom_start or not zoom_end:
        cur_ns = _parse_time_ns(sim_time)
        zoom_start = f"{max(0, cur_ns - 1_000_000)}ns"
        zoom_end = f"{cur_ns}ns"
    try:
        await sv.execute(f"waveform xview limits {zoom_start} {zoom_end}")
        results.append(f"Zoomed to {zoom_start} – {zoom_end}")
    except TclError:
        pass

    # 5. Auto-reload
    if auto_reload:
        try:
            await sv.execute(
                "proc _mcp_auto_reload {} { "
                "  catch {database reload}; "
                "  after 2000 _mcp_auto_reload "
                "}; "
                "after 2000 _mcp_auto_reload"
            )
            results.append("Auto-reload enabled (2s interval)")
        except TclError as e:
            results.append(f"Auto-reload failed: {e}")

    return "\n".join(results)


@mcp.tool()
async def simvision_live_stop() -> str:
    """Stop SimVision live waveform auto-reload."""
    sv = _get_simvision_bridge()
    try:
        await sv.execute("foreach id [after info] { after cancel $id }")
        return "Auto-reload stopped."
    except TclError as e:
        return f"ERROR: {e}"


# ===================================================================
# v4 — Simulation Lifecycle Management
# ===================================================================


@mcp.tool()
async def sim_discover(
    sim_dir: str = "",
    force: bool = False,
) -> str:
    """Discover simulation environment and register in mcp_registry.

    Detects: sim_dir, TB type, runner, shell/EDA env, mcp_bridge.tcl,
    setup TCLs, EDA tool paths, bridge port.
    Also updates ~/.simvisionrc and patches legacy run scripts.

    Args:
        sim_dir: Explicit simulation directory. Empty = auto-discover.
        force:   Re-detect even if registry already exists.
    """
    try:
        return await run_full_discovery(sim_dir, force)
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
        file:   "config" (.mcp_sim_config.json of default sim_dir) or "registry" (mcp_registry.json).
        key:    Dot-notation path (e.g. "runner.default_mode", "bridge.port").
        value:  Value for 'set' action. Auto-parsed: "9876" to int, "true" to bool.
    """
    try:
        return await config_action(action, file, key, value)
    except RuntimeError as e:
        return f"ERROR: {e}"


@mcp.tool()
async def sim_start(
    test_name: str,
    sim_dir: str = "",
    mode: str = "bridge",
    sim_mode: str = "",
    run_duration: str = "",
    timeout: int = 120,
    extra_args: str = "",
) -> str:
    """Start simulation using registry configuration.

    Args:
        test_name:    Required — test to run. Short name OK (e.g. "TOP015").
        sim_dir:      Simulation dir. Empty = registry default.
        mode:         "bridge" (interactive) or "batch" (run to end).
        sim_mode:     "rtl"|"gate"|"ams_rtl"|"ams_gate". Empty = default_mode.
        run_duration: Batch mode only — limit sim time.
        timeout:      Bridge mode — max seconds to wait for bridge ready.
        extra_args:   1-shot extra simulation arguments (not saved to registry).
    """
    try:
        # v4.1: resolve short test name → full name
        from xcelium_mcp.sim_runner import _resolve_test_name
        test_name = await _resolve_test_name(test_name, sim_dir)
        return await start_simulation(test_name, sim_dir, mode, sim_mode, run_duration, timeout, extra_args=extra_args)
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"
    except RuntimeError as e:
        return f"ERROR: {e}"


# ===================================================================
# Phase 5 — Connection + Simulation Control (tools 1–7)
# ===================================================================

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
    global _xmsim_bridge, _simvision_bridge

    if port == 0 and target == "auto":
        return await _auto_connect_all(host, timeout)

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
    except Exception as e:
        return f"ERROR: Connection failed: {type(e).__name__}: {e}"

    if target == "simvision":
        _simvision_bridge = bridge
    else:
        _xmsim_bridge = bridge

    try:
        where = await bridge.execute("where")
    except TclError:
        where = "(unknown)"

    return f"Connected to {target} at {host}:{port} (ping={ping})\nCurrent position: {where}"


async def _auto_connect_all(host: str, timeout: float) -> str:
    """Scan all ready files, connect to each, assign to appropriate slot."""
    global _xmsim_bridge, _simvision_bridge
    results = []

    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    if not r.strip():
        return "No bridges found. Run sim_start or simvision_start first."

    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        p, btype = int(parts[0]), parts[1]

        bridge = TclBridge(host=host, port=p, timeout=timeout)
        try:
            ping = await bridge.connect()
            if btype == "simvision":
                _simvision_bridge = bridge
            else:
                _xmsim_bridge = bridge
            results.append(f"  {btype}:{p} (ping={ping})")
        except Exception as e:
            results.append(f"  {btype}:{p} FAILED ({e})")

    if not results:
        return "No bridges found."
    return "Connected:\n" + "\n".join(results)


async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type."""
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == target:
            return int(parts[0]), parts[1]
    return 0, target


async def _read_bridge_type(port: int) -> str:
    """Read bridge type from ready file for given port."""
    r = await ssh_run(f"cat /tmp/mcp_bridge_ready_{port} 2>/dev/null")
    parts = r.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return "xmsim"


@mcp.tool()
async def disconnect_simulator(target: str = "all") -> str:
    """Disconnect from bridge(s).

    Args:
        target: "xmsim" | "simvision" | "all" (default: all)
    """
    global _xmsim_bridge, _simvision_bridge
    results = []

    if target in ("xmsim", "all") and _xmsim_bridge and _xmsim_bridge.connected:
        await _xmsim_bridge.disconnect()
        _xmsim_bridge = None
        results.append("xmsim: disconnected")

    if target in ("simvision", "all") and _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None
        results.append("simvision: disconnected")

    return "\n".join(results) if results else f"No {target} bridge connected."


@mcp.tool()
async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
    """Run the simulation, optionally for a specified duration.

    Args:
        duration: Simulation time to run (e.g. "100ns", "1us"). Empty = run until breakpoint or end.
        timeout: MCP response timeout in seconds (default 600s for gate-level sim support).
    """
    bridge = _get_xmsim_bridge()
    cmd = f"run {duration}" if duration else "run"
    await bridge.execute(cmd, timeout=timeout)
    try:
        where = await bridge.execute("where")
    except (TclError, asyncio.TimeoutError, ConnectionError):
        where = "(position unknown)"
    return f"Simulation advanced. Current position: {where}"


@mcp.tool()
async def sim_stop() -> str:
    """Stop a running simulation."""
    bridge = _get_xmsim_bridge()
    await bridge.execute("stop")
    try:
        where = await bridge.execute("where")
    except (TclError, asyncio.TimeoutError, ConnectionError):
        where = "(position unknown)"
    return f"Simulation stopped at: {where}"


@mcp.tool()
async def sim_restart() -> str:
    """Restart the simulation from time 0.

    Tries run -clean first, then snapshot restore, then plain restart.
    Returns method used: run-clean | snapshot | plain.
    """
    bridge = _get_xmsim_bridge()
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
    bridge = _get_bridge(target)
    return await bridge.execute(tcl_cmd, timeout=float(timeout))


@mcp.tool()
async def sim_status(target: str = "auto") -> str:
    """Get current simulation status (time, scope, state).

    Args:
        target: "xmsim" | "simvision" | "auto" (default: auto).
    """
    bridge = _get_bridge(target)
    results: list[str] = []

    for label, cmd in [("Position", "where"), ("Scope", "scope")]:
        try:
            val = await bridge.execute(cmd)
            results.append(f"{label}: {val}")
        except TclError as e:
            results.append(f"{label}: (error: {e})")

    return "\n".join(results)


@mcp.tool()
async def set_breakpoint(condition: str, name: str = "") -> str:
    """Set a conditional breakpoint in the simulation.

    For signal-based conditions, uses xmsim's [value] syntax:
      signal="top.hw.r_rst", condition="== 1'b1"
      → stop -create -condition {[value top.hw.r_rst] == "1'b1"}

    For raw Tcl expressions, wraps in braces:
      condition="{$time > 5000000}"

    Args:
        condition: Signal condition "signal op value" (e.g. "top.hw.r_rst == 1'b1")
                   or raw Tcl expression in braces.
        name: Optional breakpoint name.
    """
    bridge = _get_xmsim_bridge()

    # Parse "signal op value" format for hierarchical signal paths
    import re
    m = re.match(r'^(\S+)\s*(==|!=|>|<|>=|<=)\s*(.+)$', condition.strip())
    if m and '.' in m.group(1):
        sig, op, val = m.group(1), m.group(2), m.group(3).strip()
        tcl_cond = '{[value ' + sig + '] ' + op + ' "' + val + '"}'
        cmd = f"stop -create -condition {tcl_cond}"
    else:
        cmd = f"stop -create -condition {condition}"

    if name:
        cmd += f" -name {name}"
    result = await bridge.execute(cmd)
    return f"Breakpoint set: {result}"


# ===================================================================
# Phase 6 — Signal Inspection + Manipulation (tools 8–13)
# ===================================================================

@mcp.tool()
async def get_signal_value(signals: list[str]) -> str:
    """Read current values of one or more signals.

    Args:
        signals: List of signal paths (e.g. ["/tb/dut/clk", "/tb/dut/data[7:0]"]).
    """
    bridge = _get_xmsim_bridge()
    results: list[str] = []
    for sig in signals:
        try:
            val = await bridge.execute(f"value {sig}")
            results.append(f"{sig} = {val}")
        except TclError as e:
            results.append(f"{sig} = ERROR: {e}")
    return "\n".join(results)


@mcp.tool()
async def describe_signal(signal: str) -> str:
    """Get detailed information about a signal (type, width, direction).

    Args:
        signal: Full hierarchical signal path.
    """
    bridge = _get_xmsim_bridge()
    result = await bridge.execute(f"describe {signal}")
    return result


@mcp.tool()
async def find_drivers(signal: str) -> str:
    """Find all drivers of a signal (useful for X/Z debugging).

    Args:
        signal: Full hierarchical signal path.
    """
    bridge = _get_xmsim_bridge()
    result = await bridge.execute(f"drivers {signal}")
    return result


@mcp.tool()
async def list_signals(scope: str, pattern: str = "*", target: str = "auto") -> str:
    """List signals in a scope, optionally filtered by pattern.

    Args:
        scope:   Hierarchical scope path (e.g. "top.hw.u_ext").
        pattern: Glob pattern to filter signals (default "*").
        target:  "xmsim" | "simvision" | "auto" (default: auto).
    """
    bridge = _get_bridge(target)

    # Use 'describe' with hierarchical path + pattern
    # 'scope -describe' does NOT accept pattern args (causes SCMULT error)
    result = await bridge.execute(f"describe {scope}.{pattern}")
    return result


@mcp.tool()
async def deposit_value(signal: str, value: str) -> str:
    """Force-deposit a value onto a signal.

    Args:
        signal: Full hierarchical signal path.
        value: Value to deposit (e.g. "1'b1", "8'hFF", "0").
    """
    bridge = _get_xmsim_bridge()
    await bridge.execute(f"deposit {signal} {value}")
    # Verify
    readback = await bridge.execute(f"value {signal}")
    return f"Deposited {value} on {signal}. Readback: {readback}"


@mcp.tool()
async def release_signal(signal: str) -> str:
    """Release a previously deposited signal, restoring driven value.

    Args:
        signal: Full hierarchical signal path.
    """
    bridge = _get_xmsim_bridge()
    await bridge.execute(f"release {signal}")
    readback = await bridge.execute(f"value {signal}")
    return f"Released {signal}. Current value: {readback}"


# ===================================================================
# Phase 7 — Waveform Control (tools 14–16)
# ===================================================================

@mcp.tool()
async def waveform_add_signals(
    signals: list[str],
    group_name: str = "",
    window_name: str = "",
) -> str:
    """Add signals to SimVision waveform. Auto-creates window, skips duplicates.

    Args:
        signals:     Signal paths to add.
        group_name:  Group within window. Empty = no group.
        window_name: Target waveform window. Empty = current (or auto-create).
    """
    bridge = _get_simvision_bridge()
    results = []

    # 1. Window: specified → switch, unspecified → current or auto-create
    if window_name:
        try:
            await bridge.execute(f"waveform using {window_name}")
        except TclError:
            avail = await _list_waveform_windows(bridge)
            return f"ERROR: Window '{window_name}' not found. Available: {avail}"
    else:
        try:
            current = await bridge.execute("waveform using")
            if not current.strip():
                raise TclError("empty")
        except TclError:
            wname = await bridge.execute("waveform new")
            results.append(f"Waveform window created: {wname}")

    # 2. Dedup: query existing signals
    try:
        existing = await bridge.execute("waveform signals -format list")
        existing_set = set(existing.strip().splitlines())
    except TclError:
        existing_set = set()

    new_signals = [s for s in signals if s not in existing_set]
    skipped = len(signals) - len(new_signals)

    if not new_signals:
        return f"All {len(signals)} signal(s) already in waveform (skipped)."

    # 3. Add signals
    sig_str = " ".join(new_signals)
    if group_name:
        try:
            await bridge.execute(f"waveform add -groups {{{group_name}}}")
        except TclError:
            pass
        result = await bridge.execute(f"waveform add -using {group_name} -signals {{{sig_str}}}")
    else:
        result = await bridge.execute(f"waveform add -signals {{{sig_str}}}")

    results.append(f"Added {len(new_signals)}, skipped {skipped} (duplicate). {result}")
    return "\n".join(results)


async def _list_waveform_windows(bridge) -> str:
    """List available waveform windows."""
    try:
        r = await bridge.execute("waveform get -name")
        return r.strip() if r.strip() else "(none)"
    except TclError:
        return "(error)"


@mcp.tool()
async def waveform_zoom(start_time: str, end_time: str) -> str:
    """Set the waveform viewer time range (zoom to region).

    Args:
        start_time: Start time (e.g. "0ns").
        end_time: End time (e.g. "100ns").
    """
    bridge = _get_simvision_bridge()
    result = await bridge.execute(
        f"waveform xview limits {start_time} {end_time}"
    )
    return f"Waveform zoomed to {start_time} – {end_time}. {result}"


@mcp.tool()
async def cursor_set(time: str, cursor_name: str = "TimeA") -> str:
    """Set a waveform cursor to a specific time.

    Args:
        time: Simulation time (e.g. "50ns").
        cursor_name: Cursor name (default "TimeA").
    """
    bridge = _get_simvision_bridge()
    result = await bridge.execute(
        f"cursor set -using {cursor_name} -time {time}"
    )
    return f"Cursor {cursor_name} set to {time}. {result}"


# ===================================================================
# Phase 9 — Screenshot + Debugger Mode (tools 17–18)
# ===================================================================

@mcp.tool()
async def take_waveform_screenshot() -> Image:
    """Capture a screenshot of the SimVision waveform window.

    Returns the screenshot as a PNG image that Claude can analyze.
    """
    bridge = _get_simvision_bridge()
    ps_path = await bridge.screenshot()
    png_bytes = await ps_to_png(ps_path)
    return Image(data=png_bytes, format="png")


@mcp.tool()
async def run_debugger_mode(target: str = "auto") -> list:
    """Comprehensive debug snapshot: simulation state + signal values + screenshot + debugging guide.

    Returns a combined text report and waveform screenshot for AI-assisted hardware debugging.

    Args:
        target: "xmsim" | "simvision" | "auto" (default: auto).
    """
    bridge = _get_bridge(target)
    sections: list[str] = []

    # 1. Simulation state
    sections.append("## Simulation State")
    for label, cmd in [("Position", "where"), ("Scope", "scope")]:
        try:
            val = await bridge.execute(cmd)
            sections.append(f"- **{label}**: `{val}`")
        except TclError as e:
            sections.append(f"- **{label}**: error — {e}")

    # 2. Signal values in current scope (up to 50)
    sections.append("\n## Signal Values (current scope)")
    try:
        sig_list = await bridge.execute("describe *")
        lines = sig_list.strip().splitlines()[:50]
        if lines:
            for line in lines:
                # Try to get the value of each signal
                sig_name = line.split()[0] if line.split() else ""
                if sig_name:
                    try:
                        val = await bridge.execute(f"value {sig_name}")
                        sections.append(f"- `{sig_name}` = `{val}`")
                    except TclError:
                        sections.append(f"- `{sig_name}` = (could not read)")
        else:
            sections.append("(no signals in current scope)")
    except TclError as e:
        sections.append(f"(could not list signals: {e})")

    # 3. Active breakpoints
    sections.append("\n## Active Breakpoints")
    try:
        bp_list = await bridge.execute("stop -show")
        sections.append(f"```\n{bp_list}\n```")
    except TclError:
        sections.append("(no breakpoints or command not available)")

    # 4. Hardware debugging checklist
    sections.append(textwrap.dedent("""
    ## Hardware Debugging Checklist
    - [ ] **X/Z values**: Check for uninitialized or multi-driven signals
    - [ ] **Clock**: Verify clock is toggling at expected frequency
    - [ ] **Reset**: Confirm reset sequence completed correctly
    - [ ] **FSM state**: Check state machine is not stuck
    - [ ] **CDC**: Look for metastability on clock domain crossings
    - [ ] **Timing**: Verify setup/hold on critical paths
    - [ ] **FIFO**: Check for overflow/underflow conditions

    ## Suggested Next Steps
    - `get_signal_value` — read specific signals of interest
    - `find_drivers` — trace X/Z values to their source
    - `waveform_add_signals` — add signals to waveform for visual inspection
    - `sim_run` with duration — step the simulation forward
    - `set_breakpoint` — set conditional breakpoints on suspicious signals
    """))

    report = "\n".join(sections)

    # 5. Try to capture a screenshot
    try:
        ps_path = await bridge.screenshot()
        png_bytes = await ps_to_png(ps_path)
        screenshot = Image(data=png_bytes, format="png")
        return [report, screenshot]
    except Exception as e:
        report += f"\n\n*(Screenshot unavailable: {e})*"
        return [report]


# ===================================================================
# Phase 10 — Advanced Debug Tools (tools 19–25)
# ===================================================================

@mcp.tool()
async def shutdown_simulator(target: str = "xmsim") -> str:
    """Safely shutdown the simulator, preserving SHM waveform data.

    Closes all SHM databases and terminates the target simulator gracefully.
    Always use this instead of disconnect_simulator when ending a debug session.
    WARNING: exit or pkill will lose SHM data. This is the only safe way.

    Args:
        target: "xmsim" (default) | "simvision". Which bridge to shutdown.
    """
    global _xmsim_bridge, _simvision_bridge
    if target == "simvision":
        bridge = _get_simvision_bridge()
        try:
            resp = await bridge.execute_safe("exit")
            return f"SimVision shutdown: {resp.body}"
        except (ConnectionError, asyncio.TimeoutError):
            return "SimVision shutdown completed (connection closed)."
        finally:
            _simvision_bridge = None
    else:
        bridge = _get_xmsim_bridge()
        try:
            resp = await bridge.execute_safe("__SHUTDOWN__")
            return f"Simulator shutdown: {resp.body}"
        except (ConnectionError, asyncio.TimeoutError):
            return "Simulator shutdown completed (connection closed)."
        finally:
            _xmsim_bridge = None


@mcp.tool()
async def watch_signal(signal: str, op: str = "==", value: str = "") -> str:
    """Set a watchpoint to stop simulation when a signal matches a condition.

    The simulation will automatically stop at the exact clock edge where
    the condition becomes true. Much more efficient than manual probing.

    Args:
        signal: Full hierarchical signal path (e.g. "top.dut.r_state[3:0]").
        op: Comparison operator ("==", "!=", ">", "<", ">=", "<=").
        value: Target value in Verilog format (e.g. "8'h10", "4'b1010").
    """
    bridge = _get_xmsim_bridge()
    result = await bridge.execute(f"__WATCH__ {signal} {op} {value}")
    return f"Watchpoint set: {result}"


@mcp.tool()
async def watch_clear(watch_id: str = "all") -> str:
    """Clear watchpoints. Use "all" to clear all, or a specific stop ID.

    Args:
        watch_id: Watchpoint ID to clear, or "all" for all watchpoints.
    """
    bridge = _get_xmsim_bridge()
    result = await bridge.execute(f"__WATCH_CLEAR__ {watch_id}")
    return result


@mcp.tool()
async def probe_control(mode: str, scope: str = "") -> str:
    """Control SHM waveform recording to manage dump file size.

    Disable probes during uninteresting simulation periods to save disk space.
    Re-enable before the region of interest. Optionally target a specific scope.

    Args:
        mode: "enable" to start recording, "disable" to pause, "status" to check.
        scope: Hierarchical scope to target (e.g. "top.hw.u_ext"). Empty = all probes.
    """
    bridge = _get_xmsim_bridge()
    cmd = f"__PROBE_CONTROL__ {mode} {scope}" if scope else f"__PROBE_CONTROL__ {mode}"
    result = await bridge.execute(cmd)
    return result


@mcp.tool()
async def save_checkpoint(
    name: str = "",
    sim_dir: str = "",
    saved_time_ns: int = 0,
) -> str:
    """Save a simulation checkpoint to persistent storage.

    Checkpoints are saved to {sim_dir}/checkpoints/ and registered in the
    manifest with a compile_hash for automatic invalidation on recompile.
    Use restore_checkpoint to return to this state without re-simulating.

    Args:
        name:          Checkpoint name (alphanumeric, e.g. "L1_common_init").
                       Auto-generated from timestamp if empty.
        sim_dir:       Simulation directory (auto-detected if empty).
        saved_time_ns: Current simulation time in ns for nearest-checkpoint lookup.
    """
    bridge = _get_xmsim_bridge()

    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    import os
    chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else "/tmp/mcp_checkpoints"

    cmd = f"__SAVE__ {name} {chk_base}" if name else f"__SAVE__  {chk_base}"
    result = await bridge.execute(cmd)

    # Register in manifest on success
    if "save failed" not in result and resolved_dir:
        # Extract actual name from response "saved:worklib.{name}:module|dir:..."
        actual_name = name
        if not actual_name and "saved:worklib." in result:
            try:
                actual_name = result.split("saved:worklib.")[1].split(":module")[0]
            except IndexError:
                pass
        if actual_name:
            checkpoint_manager.register_checkpoint(resolved_dir, actual_name, saved_time_ns)

    return result


@mcp.tool()
async def restore_checkpoint(
    name: str = "",
    sim_dir: str = "",
) -> str:
    """Restore simulation to a previously saved checkpoint.

    Verifies compile_hash before restore — rejects stale checkpoints created
    before the last RTL recompile.  Stale breakpoints are cleared automatically
    after restore to prevent spurious $finish.

    Args:
        name:    Checkpoint name to restore. Empty = last saved checkpoint.
        sim_dir: Simulation directory (auto-detected if empty).
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()

    import os
    chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else "/tmp/mcp_checkpoints"

    # compile_hash verification
    if resolved_dir and name:
        valid, reason = checkpoint_manager.verify_checkpoint(resolved_dir, name)
        if not valid:
            stale = checkpoint_manager.invalidate_stale_checkpoints(
                resolved_dir, reason="hash mismatch on restore"
            )
            msg = (
                f"ERROR: {reason}\n"
                f"Stale checkpoints removed: {stale}\n"
                f"Re-run sim_batch_run to create new checkpoints."
            )
            return msg

    bridge = _get_xmsim_bridge()
    cmd = f"__RESTORE__ {name} {chk_base}" if name else f"__RESTORE__  {chk_base}"
    result = await bridge.execute(cmd, timeout=120.0)
    return result


@mcp.tool()
async def bisect_signal(
    signal: str,
    op: str,
    value: str,
    start_ns: int,
    end_ns: int,
    precision_ns: int = 1000,
    shm_path: str = "",
) -> str:
    """Find when a signal condition first becomes true.

    Mode A (preferred, no active simulator required): when shm_path is given,
    uses bisect_signal_dump — extracts CSV from SHM and performs in-memory
    binary search.  No bridge connection needed.

    Mode B (bridge, legacy): when shm_path is empty and a bridge is connected,
    uses the simulator's native __BISECT__ binary search with save/restore.

    v2 API: shm_path parameter is new; all other parameters are unchanged.

    Args:
        signal:       Full hierarchical signal path.
        op:           Comparison operator: "eq","ne","gt","lt","change"
                      (bridge mode also accepts "==", "!=", etc.)
        value:        Target value (hex/dec/oct; ignored for "change").
        start_ns:     Start of search range in nanoseconds.
        end_ns:       End of search range in nanoseconds.
        precision_ns: (Bridge mode) Stop when range < this (default 1000ns).
        shm_path:     SHM dump path for Mode A (CSV-based).  Empty = Mode B.
    """
    if shm_path:
        # Mode A: SHM dump → CSV → in-memory search (P4-7)
        return await bisect_signal_dump(
            shm_path=shm_path,
            signal=signal,
            op=op,
            value=value,
            start_ns=start_ns,
            end_ns=end_ns,
        )

    # Mode B: bridge-based binary search (legacy)
    bridge = _get_xmsim_bridge()
    cmd = f"__BISECT__ {signal} {op} {value} {start_ns} {end_ns} {precision_ns}"
    result = await bridge.execute(cmd, timeout=600.0)
    return result


# ===================================================================
# Phase 4 supplement — Checkpoint management tools (inserted before Phase 2)
# ===================================================================

@mcp.tool()
async def cleanup_checkpoints(
    sim_dir: str = "",
    mode: str = "stale",
    project_filter: str = "",
    dry_run: bool = True,
) -> str:
    """List or remove checkpoints from {sim_dir}/checkpoints/.

    mode:
      "list"    — list all checkpoints (no deletion)
      "stale"   — checkpoints whose compile_hash no longer matches (default)
      "project" — checkpoints whose path contains project_filter
      "all"     — every checkpoint

    dry_run=True (default): report candidates only, no deletion.
    Set dry_run=False to actually remove.

    Args:
        sim_dir:        Simulation directory (auto-detected if empty).
        mode:           Cleanup mode (list/stale/project/all).
        project_filter: Path substring for "project" mode.
        dry_run:        True = report only, False = delete.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        return "ERROR: Could not determine sim_dir. Pass sim_dir explicitly."

    result = checkpoint_manager.cleanup_checkpoints(
        resolved_dir, mode=mode, project_filter=project_filter, dry_run=dry_run
    )

    lines = [
        f"sim_dir: {result['sim_dir']}",
        f"mode: {result['mode']}  dry_run: {result['dry_run']}",
        f"compile_hash (current): {result['current_hash']}",
        "",
    ]
    if result["removed"]:
        verb = "Would remove" if dry_run else "Removed"
        lines.append(f"{verb} ({len(result['removed'])}):")
        for n in result["removed"]:
            lines.append(f"  - {n}")
    else:
        lines.append("No checkpoints to remove.")
    if result["kept"]:
        lines.append(f"Kept ({len(result['kept'])}):")
        for n in result["kept"]:
            lines.append(f"  - {n}")
    return "\n".join(lines)


@mcp.tool()
async def bisect_restore_and_debug(
    checkpoint_name: str,
    probe_signals: list[str],
    watch_signal_path: str = "",
    watch_op: str = "==",
    watch_value: str = "",
    run_duration: str = "10000ns",
    shm_path: str = "",
    sim_dir: str = "",
    keep_alive: bool = True,
) -> str:
    """Restore a checkpoint, add probe signals, then run with optional watchpoint stop.

    Pattern: restore → probe_add_signals → [set watchpoint] → sim_run → stop.
    Use for interactive debug after bisect_signal_dump identifies a bug time.
    When shm_path is given, runs bisect_signal_dump after stop for immediate analysis.

    Args:
        checkpoint_name:   Checkpoint name to restore.
        probe_signals:     Signal paths to add via probe after restore.
        watch_signal_path: Signal path to watch (stop when condition met). Empty = no watchpoint.
        watch_op:          Watchpoint operator (==, !=, >, <). Default "==".
        watch_value:       Watchpoint value. Required when watch_signal_path is set.
        run_duration:      Fallback run duration when no watchpoint (e.g. "10000ns").
        shm_path:          If given, run bisect_signal_dump(watch_signal, ...) after stop.
        sim_dir:           Simulation directory (auto-detected if empty).
        keep_alive:        True = leave simulator running after analysis (default).
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()

    # 1. Restore
    restore_result = await restore_checkpoint(checkpoint_name, resolved_dir)
    if "ERROR" in restore_result or "restore failed" in restore_result:
        return f"Restore failed: {restore_result}"

    # 2. Add probe signals
    if probe_signals:
        bridge = _get_xmsim_bridge()
        sig_str = " ".join(probe_signals)
        try:
            await bridge.execute(
                f"probe -create {{{sig_str}}} -shm -depth all", timeout=30.0
            )
        except Exception as e:
            return f"Restore succeeded but probe_add_signals failed: {e}\nRestore result: {restore_result}"

    # 3. Run (with or without watchpoint)
    bridge = _get_xmsim_bridge()
    if watch_signal_path and watch_value:
        # Use __WATCH__ meta command (same as watch_signal tool)
        # xmsim stop -create doesn't support -signal option
        await bridge.execute(
            f"__WATCH__ {watch_signal_path} {watch_op} {watch_value}",
            timeout=10.0,
        )
        run_result = await bridge.execute(f"run {run_duration}", timeout=600.0)
    else:
        run_result = await bridge.execute(f"run {run_duration}", timeout=120.0)

    lines = [f"restore: {restore_result}", f"run: {run_result}"]

    # 4. Optional CSV analysis after stop
    if shm_path and watch_signal_path and watch_value:
        csv_result = await bisect_signal_dump(
            shm_path=shm_path,
            signal=watch_signal_path,
            op="eq" if watch_op == "==" else watch_op,
            value=watch_value,
        )
        lines.append(f"bisect: {csv_result}")

    if not keep_alive:
        try:
            await bridge.execute("stop", timeout=10.0)
        except Exception:
            pass
    else:
        lines.append("(simulator left running)")

    return "\n".join(lines)


# ===================================================================
# Phase 2 — CSV Infrastructure + Batch / Regression Execution (tools 26–28)
# ===================================================================

@mcp.tool()
async def extract_csv(
    shm_path: str,
    signals: list[str],
    start_ns: int = 0,
    end_ns: int = 0,
    output_path: str = "",
    missing_ok: bool = True,
) -> str:
    """Extract signal waveform data from SHM dump to CSV via simvisdbutil.

    Internally runs:
      simvisdbutil {shm_path} -csv -output {output_path} -overwrite
          [-range {start_ns}:{end_ns}ns]
          [-missing]
          -sig {signal_1} -sig {signal_2} ...

    Returns: path to generated CSV file.
    Caches result keyed by (shm_path, signals, start_ns, end_ns).

    Args:
        shm_path: SHM dump file path (e.g. "dump/ci_top_TOP015.shm/ci_top.trn").
        signals: List of signal paths to extract.
        start_ns: Start of time range in nanoseconds (0 = from beginning).
        end_ns: End of time range in nanoseconds (0 = to end).
        output_path: CSV output path. Auto-generated if empty.
        missing_ok: Ignore signals absent from SHM (True) vs raise error (False).
    """
    try:
        path = await csv_cache.extract(
            shm_path=shm_path,
            signals=signals,
            start_ns=start_ns,
            end_ns=end_ns,
            output_path=output_path,
            missing_ok=missing_ok,
        )
        return f"CSV extracted: {path}"
    except RuntimeError as e:
        return f"ERROR: {e}"


@mcp.tool()
async def sim_batch_run(
    test_name: str,
    sim_dir: str = "",
    from_checkpoint: str = "",
    probe_signals: list[str] = [],
    shm_path: str = "",
    run_duration: str = "",
    rename_dump: bool = False,
    dump_signals: list[str] = [],
    timeout: int = 600,
    sim_mode: str = "",
    extra_args: str = "",
) -> str:
    """Run simulation for a single test.

    Normal run ([A]): from_checkpoint="" → compile + run → SHM dump.
    Restore run ([A']): from_checkpoint=name → restore_checkpoint → probe_add → run → new SHM.

    SHM overwrite prevention:
      Method 6-A (default): injects TEST_NAME env var; setup.tcl uses $env(TEST_NAME).
      Method 6-B (rename_dump=True): renames dump/ci_top.shm after simulation.

    Returns: log summary (PASS/FAIL lines, error count, SHM dump path).

    Args:
        test_name: Test name (e.g. "TOP015").
        sim_dir: Simulation directory. Empty → use default from mcp_registry.json.
        from_checkpoint: Checkpoint name for [A'] restore mode.
        probe_signals: Additional signals to probe in [A'] mode.
        shm_path: New SHM path for [A'] mode (default: dump/{test_name}_extra.shm).
        run_duration: Run only up to this time (e.g. "10ms"). Empty = run to end.
        rename_dump: Enable Method 6-B SHM rename fallback.
        dump_signals: Additional signals to dump (prepare_dump_scope).
        timeout: SSH wait timeout in seconds.
    """
    # Resolve sim_dir
    try:
        resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_sim_dir:
            # v4: auto-discover instead of error
            await run_full_discovery(sim_dir)
            resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
            if not resolved_sim_dir:
                return "ERROR: sim_discover failed. Provide sim_dir explicitly."
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # v4.1: resolve short test name → full name
    from xcelium_mcp.sim_runner import _resolve_test_name
    try:
        test_name = await _resolve_test_name(test_name, resolved_sim_dir)
    except ValueError as e:
        return f"ERROR: {e}"

    # Load runner config (v4: delegates to sim_discover if config missing)
    try:
        runner = await _load_or_detect_runner(resolved_sim_dir)
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # [A'] restore mode: restore checkpoint → add probe signals before run
    if from_checkpoint:
        restore_result = await restore_checkpoint(from_checkpoint, resolved_sim_dir)
        if "ERROR" in restore_result or "restore failed" in restore_result:
            return f"ERROR in [A'] restore: {restore_result}"
        if probe_signals:
            try:
                bridge = _get_xmsim_bridge()
                sig_str = " ".join(probe_signals)
                await bridge.execute(
                    f"probe -create {{{sig_str}}} -shm -depth all", timeout=30.0
                )
            except Exception as e:
                return f"Restore succeeded but probe_add_signals failed: {e}"

    # dump_signals: extend probe scope via prepare_dump_scope
    if dump_signals:
        try:
            extended_tcl = await _prepare_dump_scope_internal(
                resolved_sim_dir,
                additional_signals=dump_signals,
            )
            runner = dict(runner)
            runner["_extended_tcl"] = extended_tcl
        except Exception as e:
            return f"ERROR in prepare_dump_scope: {e}"

    # Execute simulation
    try:
        # v4.1: sim_mode + extra_args
        effective_mode = sim_mode or runner.get("default_mode", "rtl")
        log = await _run_batch_single(
            sim_dir=resolved_sim_dir,
            test_name=test_name,
            runner=runner,
            rename_dump=rename_dump,
            run_duration=run_duration,
            timeout=timeout,
            sim_mode=effective_mode,
            extra_args=extra_args,
        )
    except Exception as e:
        return f"ERROR running simulation: {e}"

    return f"sim_batch_run {test_name} completed.\n\n{log}"


@mcp.tool()
async def sim_batch_regression(
    test_list: list[str],
    sim_dir: str = "",
    from_checkpoint: str = "",
    dump_signals: list[str] = [],
    rename_dump: bool = False,
    parallel: bool = False,
    sim_mode: str = "",
    extra_args: str = "",
) -> str:
    """Run regression over a list of tests.

    Normal run (from_checkpoint=""): L1 created on first test (Phase 4), L2 per test.
    Restore run (from_checkpoint=name): restore from checkpoint, run each test (Phase 4).

    Uses screen session for background execution with per-test progress polling.
    Parallel execution is reserved for a later phase (parallel=True raises an error).

    Returns: regression summary table (N/M PASS, failures: [...]).

    Args:
        test_list: List of test names. Empty → auto-detect from mcp_sim_config.json.
        sim_dir: Simulation directory. Empty → default from mcp_registry.json.
        from_checkpoint: Checkpoint for [A'] restore mode.
        dump_signals: Additional dump signals.
        rename_dump: Enable Method 6-B SHM rename fallback.
        parallel: Parallel screen execution (reserved for future phase).
    """

    if parallel:
        return "ERROR: parallel=True is reserved for a future phase. Use parallel=False."

    # Resolve sim_dir
    try:
        resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_sim_dir:
            # v4: auto-discover instead of error
            await run_full_discovery(sim_dir)
            resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
            if not resolved_sim_dir:
                return "ERROR: sim_discover failed. Provide sim_dir explicitly."
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # Load runner config (v4: delegates to sim_discover if config missing)
    try:
        runner = await _load_or_detect_runner(resolved_sim_dir)
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # dump_signals: 1회만 prepare_dump_scope → 전 테스트 공유
    if dump_signals:
        try:
            shared_tcl = await _prepare_dump_scope_internal(
                resolved_sim_dir,
                additional_signals=dump_signals,
            )
            runner = dict(runner)
            runner["_extended_tcl"] = shared_tcl
        except Exception as e:
            return f"ERROR in prepare_dump_scope: {e}"

    # Auto-detect test_list from sim config if empty
    if not test_list:
        from xcelium_mcp.sim_runner import load_sim_config
        cfg = await load_sim_config(resolved_sim_dir)
        if cfg:
            test_list = cfg.get("test_list", [])
        if not test_list:
            return (
                "ERROR: test_list is empty and no test_list found in "
                f".mcp_sim_config.json at {resolved_sim_dir}. "
                "Provide test_list explicitly."
            )

    # v4.1: resolve short test names → full names
    from xcelium_mcp.sim_runner import _resolve_test_name
    try:
        test_list = [await _resolve_test_name(t, resolved_sim_dir) for t in test_list]
    except ValueError as e:
        return f"ERROR: {e}"

    # Execute regression
    try:
        summary = await _run_batch_regression(
            sim_dir=resolved_sim_dir,
            test_list=test_list,
            runner=runner,
            from_checkpoint=from_checkpoint,
            rename_dump=rename_dump,
            sim_mode=sim_mode,
            extra_args=extra_args,
        )
    except Exception as e:
        return f"ERROR running regression: {e}"

    chk_note = "\n[Note: L1/L2 checkpoint auto-save requires Phase 4 — skipped]"
    return f"sim_batch_regression completed.\n\n{summary}{chk_note}"


# ===================================================================
# Phase 3 — Advanced Analysis (tools 29–33)
# ===================================================================

# ---------------------------------------------------------------------------
# Internal helper: prepare_dump_scope logic (also used by sim_batch_run/regression)
# ---------------------------------------------------------------------------

async def _prepare_dump_scope_internal(
    sim_dir: str,
    additional_signals: list[str],
    input_tcl: str = "",
) -> str:
    """Extend an existing setup Tcl file with additional probe signals.

    Detects original Tcl from sim_dir if input_tcl is empty.
    Returns path to the extended Tcl file (written as setup_rtl_debug.tcl).
    """
    from pathlib import Path

    # Auto-detect input Tcl if not provided
    if not input_tcl:
        for candidate in ("setup_rtl.tcl", "input.tcl", "setup.tcl"):
            p = Path(sim_dir) / candidate
            if p.exists():
                input_tcl = str(p)
                break
        if not input_tcl:
            # Search sim_dir for any .tcl file
            r = await ssh_run(f"ls {sim_dir}/*.tcl 2>/dev/null | head -1")
            input_tcl = r.strip()

    output_tcl = str(Path(sim_dir) / "setup_rtl_debug.tcl")

    if input_tcl and Path(input_tcl).exists():
        original = Path(input_tcl).read_text()
    else:
        original = ""

    # Append new probe commands for additional signals
    sig_list = " ".join(f'"{s}"' for s in additional_signals)
    extra = (
        f"\n# === Added by xcelium-mcp prepare_dump_scope ===\n"
        f"probe -create {{{sig_list}}} -shm -depth all\n"
        f"# ================================================\n"
    )
    Path(output_tcl).write_text(original + extra)
    return output_tcl


@mcp.tool()
async def prepare_dump_scope(
    additional_signals: list[str],
    input_tcl: str = "",
    sim_dir: str = "",
) -> str:
    """Extend a simulation setup Tcl file with additional probe signals.

    Reads the original setup Tcl (auto-detected from sim_dir if not provided),
    appends `probe -create {signals} -shm -depth all`, and writes the result
    to `{sim_dir}/setup_rtl_debug.tcl`.

    Use with sim_batch_run(dump_signals=[...]) to capture additional signals
    without re-running from scratch.

    Args:
        additional_signals: Signal paths to add to probe scope.
        input_tcl: Path to existing setup Tcl file. Auto-detected if empty.
        sim_dir: Simulation directory for auto-detection and output. Uses default if empty.
    """
    try:
        resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    try:
        out = await _prepare_dump_scope_internal(
            resolved_sim_dir, additional_signals, input_tcl
        )
    except Exception as e:
        return f"ERROR: {e}"

    return f"Extended Tcl written to: {out}\nAdded signals: {additional_signals}"


@mcp.tool()
async def probe_add_signals(
    signals: list[str],
    shm_path: str = "",
    depth: str = "all",
) -> str:
    """Dynamically add probe signals to the connected SimVision bridge session.

    Wraps: probe -create {signals} [-shm {shm_path}] -depth {depth}
    Requires active bridge connection. Use before sim_run to capture
    additional signals not in the original probe scope.

    Args:
        signals:  Signal paths to add (hierarchical, e.g. "top.hw.u_ext.r_state").
        shm_path: SHM file to write probe data to. Empty = current session SHM.
        depth:    Probe depth ("all", "1", "2", ...).
    """
    bridge = _get_xmsim_bridge()
    sig_str = " ".join(signals)
    if shm_path:
        cmd = f"probe -create {{{sig_str}}} -shm {shm_path} -depth {depth}"
    else:
        cmd = f"probe -create {{{sig_str}}} -shm -depth {depth}"
    result = await bridge.execute(cmd)
    return f"Probe added for {len(signals)} signal(s). {result}"


@mcp.tool()
async def bisect_signal_dump(
    shm_path: str,
    signal: str,
    op: str,
    value: str,
    start_ns: int = 0,
    end_ns: int = 0,
    context_signals: list[str] = [],
) -> str:
    """Binary search in SHM dump CSV for first occurrence of a signal condition.

    No simulator connection required — pure offline CSV analysis.
    If signal is absent from SHM, suggests calling request_additional_signals.

    Op values: "eq" (==), "ne" (!=), "gt" (>), "lt" (<), "change" (any change).

    Args:
        shm_path:        SHM dump file path.
        signal:          Signal path to search.
        op:              Comparison operator.
        value:           Target value (hex/dec/oct; ignored for "change").
        start_ns:        Search start time in nanoseconds.
        end_ns:          Search end time in nanoseconds (0 = to end).
        context_signals: Additional signals to include in CSV extract for context.
    """
    all_signals = list({signal} | set(context_signals))

    try:
        csv_path = await csv_cache.extract(
            shm_path=shm_path,
            signals=all_signals,
            start_ns=start_ns,
            end_ns=end_ns,
            missing_ok=True,
        )
    except RuntimeError as e:
        return f"ERROR extracting CSV: {e}"

    result = csv_cache.bisect_csv(
        csv_path=csv_path,
        signal=signal,
        op=op,
        value=value,
        start_ns=start_ns,
        end_ns=end_ns,
        context_rows=2,
    )

    if "error" in result:
        # Signal not in SHM
        return (
            f"Signal '{signal}' not found in SHM.\n"
            f"{result['error']}\n\n"
            "Tip: Call request_additional_signals to re-run simulation with this signal probed."
        )

    if not result["found"]:
        return (
            f"No match found for {signal} {op} {value} "
            f"in range [{start_ns}ns, {end_ns or 'end'}]."
        )

    # Format context table
    ctx = result["context"]
    match_idx = result["match_row"]
    cols = [signal] + context_signals

    lines = [
        f"Match at {result['match_time_ns']}ns: {signal} = {result['match_value']}",
        "",
        "Context:",
    ]
    header = "  time(ns)   | " + " | ".join(f"{c[-20:]}" for c in cols)
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for i, row in enumerate(ctx):
        prefix = "★ " if i == match_idx else "  "
        vals = " | ".join(row.get(c, "?") for c in cols)
        lines.append(f"{prefix}{row.get('time', '?'):>10} | {vals}")

    return "\n".join(lines)


@mcp.tool()
async def request_additional_signals(
    missing_signals: list[str],
    shm_path: str,
    bug_time_ns: int = 0,
    available_checkpoints: list[str] = [],
) -> str:
    """Signal absence handler — presents capture mode options when signals are missing from SHM.

    Called automatically by bisect_signal_dump when a signal is not in the SHM dump.
    Presents 3 options:
      [A]  Full re-run with extended probe scope (sim_batch_run + prepare_dump_scope)
      [A'] Restore from nearest checkpoint + add probes + partial re-run (Phase 4)
      [B]  Bridge mode: attach to simulator, add probes live (requires active connection)

    Args:
        missing_signals:       Signals not found in the SHM dump.
        shm_path:              SHM path being analyzed.
        bug_time_ns:           Approximate bug time (used for checkpoint selection in [A']).
        available_checkpoints: Known checkpoint names (auto-queried from registry if empty).
    """
    # Auto-query nearest checkpoints from checkpoint_manager when not provided
    resolved_checkpoints = list(available_checkpoints)
    if not resolved_checkpoints and bug_time_ns:
        sim_dir = await _get_default_sim_dir()
        if sim_dir:
            nearest = checkpoint_manager.find_nearest_checkpoint(sim_dir, bug_time_ns)
            resolved_checkpoints = [c["name"] for c in nearest[:3]]

    lines = [
        f"Signals not in SHM dump ({shm_path}):",
    ]
    for s in missing_signals:
        lines.append(f"  - {s}")
    lines.append("")
    lines.append("Select a capture strategy:")
    lines.append("")
    lines.append(
        "[A] Full re-run with extended probe scope\n"
        "    → sim_batch_run(dump_signals=[missing_signals])\n"
        "    → New SHM with all signals included\n"
        "    Cost: full simulation time"
    )
    lines.append("")

    a_prime = (
        "[A'] Restore from nearest checkpoint + add probes + partial run\n"
        "    → restore_checkpoint + probe_add_signals + sim_run\n"
        "    → Faster than full re-run\n"
        "    Cost: partial simulation time from checkpoint"
    )
    lines.append(a_prime)

    if bug_time_ns:
        lines.append(f"    Bug time: {bug_time_ns}ns")
    if resolved_checkpoints:
        lines.append(f"    Available checkpoints: {', '.join(resolved_checkpoints)}")
    else:
        lines.append("    No checkpoints found (use [A] for now or save_checkpoint first)")

    lines.append("")
    lines.append(
        "[B] Bridge mode: add probes to live simulator session\n"
        "    → probe_add_signals(missing_signals) + sim_run\n"
        "    → Requires active SimVision connection (connect_simulator)\n"
        "    Cost: none if bridge already connected"
    )
    lines.append("")
    lines.append("Reply with [A], [A'], or [B] to proceed.")
    return "\n".join(lines)


@mcp.tool()
async def generate_debug_tcl(
    shm_path: str,
    signals: list[str],
    center_time_ns: int,
    zoom_range_ns: int = 10000,
    markers: list[dict] = [],
    context_note: str = "",
    output_path: str = "",
) -> str:
    """Generate a SimVision Tcl script for offline debugging.

    Creates a ready-to-use .tcl file that opens the SHM dump, adds the
    specified signals to the waveform viewer, zooms to the bug region,
    sets cursors at key times, and prints the AI analysis context.

    User runs: simvision -input {output_path} {shm_path}
    Returns: path to generated Tcl script.

    Args:
        shm_path:       SHM dump file path.
        signals:        Signal paths to add to waveform.
        center_time_ns: Bug time — waveform zoomed to center ± zoom_range_ns.
        zoom_range_ns:  Half-width of zoom range in nanoseconds.
        markers:        List of {"time_ns": int, "label": str} dicts.
        context_note:   AI analysis summary printed to SimVision console.
        output_path:    Output .tcl path. Auto-generated in SHM parent dir if empty.
    """
    import time as _time
    from pathlib import Path

    if not output_path:
        ts = int(_time.time())
        output_path = str(Path(shm_path).parent / f"debug_{ts}.tcl")

    content = debug_tools.generate_debug_tcl_content(
        shm_path=shm_path,
        signals=signals,
        center_time_ns=center_time_ns,
        zoom_range_ns=zoom_range_ns,
        markers=markers,
        context_note=context_note,
    )

    Path(output_path).write_text(content)
    return (
        f"Debug Tcl script written to: {output_path}\n"
        f"Run: simvision -input {output_path} {shm_path}"
    )


# ===================================================================
# Phase 5 — UI/Visual (tools 34–37)
# ===================================================================

@mcp.tool()
async def attach_to_simvision(
    port: int = 9876,
    timeout: int = 10,
) -> str:
    """Attach to an already-running SimVision session via TCP bridge.

    Precondition: ~/.simvisionrc must source mcp_bridge.tcl so the bridge
    starts automatically when SimVision is launched.

    Setup (once):
      echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc

    Difference from open_debug_view:
      - open_debug_view: launches SimVision + configures waveform view
      - attach_to_simvision: connects to already-running SimVision (no restart)

    Args:
        port:    TCP bridge port (default 9876).
        timeout: Connection wait timeout in seconds.
    """
    check = await ssh_run(
        f"nc -z localhost {port} 2>/dev/null && echo OK || echo FAIL",
        timeout=float(timeout + 2),
    )
    if "OK" in check:
        return await connect_simulator(host="localhost", port=port)
    return (
        f"SimVision bridge not found on port {port}.\n"
        "Setup: echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc\n"
        "Then (re)start SimVision."
    )


@mcp.tool()
async def open_debug_view(
    shm_path: str,
    signals: list[str],
    center_time_ns: int,
    zoom_range_ns: int = 10000,
    cursor_time_ns: int = 0,
    markers: list[dict] = [],
    group_name: str = "AI_Debug",
    context_note: str = "",
    display: str = ":1",
) -> str:
    """Launch SimVision on VNC display with pre-configured AI debug view.

    Flow:
      1. Detect VNC display (vncserver -list)
      2. If no VNC → generate_debug_tcl fallback (offline script)
      3. Launch: DISPLAY={display} simvision {shm_path} &
      4. Wait for TCP bridge on port 9876 (up to 30s)
      5. connect_simulator → waveform_add_signals (AI_Debug group, dup skip)
      6. zoom → cursor → markers → context note

    Args:
        shm_path:       SHM dump file path.
        signals:        Signal paths to add to AI_Debug group.
        center_time_ns: Bug time — waveform zoomed to ±zoom_range_ns.
        zoom_range_ns:  Half-width of zoom in ns.
        cursor_time_ns: Cursor position (0 = center_time_ns).
        markers:        List of {"time_ns": int, "label": str}.
        group_name:     Waveform group for AI signals (default "AI_Debug").
        context_note:   AI analysis summary printed to SimVision console.
        display:        VNC DISPLAY variable (default ":1").
    """
    # 1. VNC check
    vnc_check = await ssh_run(
        f"vncserver -list 2>/dev/null | grep '{display}' || echo NONE",
        timeout=10.0,
    )
    if "NONE" in vnc_check or display not in vnc_check:
        # Fallback: generate offline Tcl script
        tcl_result = await generate_debug_tcl(
            shm_path=shm_path,
            signals=signals,
            center_time_ns=center_time_ns,
            zoom_range_ns=zoom_range_ns,
            markers=markers,
            context_note=context_note,
        )
        return (
            f"VNC display {display} not active. Generated offline debug script:\n"
            f"{tcl_result}"
        )

    # 2. Launch SimVision (detached)
    await ssh_run(
        f"DISPLAY={display} simvision {shm_path} &",
        timeout=5.0,
    )

    # 3. Wait for bridge (15 × 2s = 30s)
    bridge_ready = await ssh_run(
        "for i in $(seq 1 15); do sleep 2; "
        "nc -z localhost 9876 2>/dev/null && echo READY && break; done",
        timeout=35.0,
    )

    if "READY" not in bridge_ready:
        tcl_result = await generate_debug_tcl(
            shm_path=shm_path, signals=signals, center_time_ns=center_time_ns,
            zoom_range_ns=zoom_range_ns, markers=markers, context_note=context_note,
        )
        return (
            f"SimVision launched but bridge not ready. Use offline script:\n"
            f"{tcl_result}"
        )

    # 4. Connect
    await connect_simulator(host="localhost", port=9876)
    bridge = _get_simvision_bridge()

    # 5. Add signals to AI_Debug group (duplicate skip via P5-2)
    if signals:
        sig_str = " ".join(signals)
        await bridge.execute(
            f"__WAVEFORM_ADD_GROUP__ {group_name} {sig_str}", timeout=30.0
        )

    # 6. Zoom
    start_zoom = center_time_ns - zoom_range_ns
    end_zoom = center_time_ns + zoom_range_ns
    await bridge.execute(f"waveform zoom -range {start_zoom}:{end_zoom}ns", timeout=10.0)

    # 7. Cursor
    t_cursor = cursor_time_ns if cursor_time_ns else center_time_ns
    await bridge.execute(f"cursor set -time {t_cursor}ns", timeout=10.0)

    # 8. Markers
    for m in markers:
        t = m.get("time_ns", 0)
        label = m.get("label", "").replace('"', "'")
        try:
            await bridge.execute(f'cursor set -time {t}ns -name "{label}"', timeout=5.0)
        except Exception:
            pass

    # 9. Context note to SimVision console
    if context_note:
        safe_note = context_note.replace('"', "'")
        await bridge.execute(f'puts "=== AI Debug Context: {safe_note} ==="', timeout=5.0)

    display_num = display.lstrip(":")
    vnc_port = 5900 + int(display_num)
    return (
        f"SimVision launched on {display}. "
        f"Connect VNC viewer to localhost:{vnc_port}\n"
        f"AI_Debug group: {len(signals)} signal(s) added, zoomed to "
        f"{start_zoom}–{end_zoom}ns"
    )


@mcp.tool()
async def compare_waveforms(
    shm_before: str,
    shm_after: str,
    signals: list[str],
    time_range_ns: list[int] = [],
    output_mode: str = "csv_diff",
    display: str = ":1",
) -> str:
    """Compare two SHM waveform dumps and report signal differences.

    csv_diff mode (default):
      1. Extract CSV from both SHMs via simvisdbutil
      2. Compare signal values at each timestamp
      3. Return changed signal list + first change time

    simvision mode:
      1. Check VNC availability on {display}
      2. Launch SimVision with shm_before as primary database
      3. Wait for TCP bridge on port 9876 (up to 30s)
      4. Open shm_after as second database via Tcl
      5. Add two waveform groups: "BEFORE" and "AFTER" with signals from each DB
      6. Return VNC connection instructions

    Args:
        shm_before:    Reference SHM (before fix, or failing run).
        shm_after:     Comparison SHM (after fix, or passing run).
        signals:       Signal paths to compare.
        time_range_ns: [start_ns, end_ns] to limit range. Empty = full range.
        output_mode:   "csv_diff" (text diff) or "simvision" (GUI side-by-side).
        display:       VNC DISPLAY for simvision mode (default ":1").
    """
    import csv as _csv

    # ------------------------------------------------------------------ #
    # simvision mode: open both SHMs in SimVision for side-by-side view   #
    # ------------------------------------------------------------------ #
    if output_mode == "simvision":
        # 1. VNC check
        vnc_check = await ssh_run(
            f"vncserver -list 2>/dev/null | grep '{display}' || echo NONE",
            timeout=10.0,
        )
        if "NONE" in vnc_check or display not in vnc_check:
            return (
                f"ERROR: VNC display {display} is not active.\n"
                "Start a VNC session first (e.g. vncserver :1), then retry.\n"
                "Fallback: use output_mode='csv_diff' for text-based comparison."
            )

        # 2. Launch SimVision with shm_before as primary database (detached)
        await ssh_run(
            f"DISPLAY={display} simvision {shm_before} &",
            timeout=5.0,
        )

        # 3. Wait for bridge (15 × 2s = 30s)
        bridge_ready = await ssh_run(
            "for i in $(seq 1 15); do sleep 2; "
            "nc -z localhost 9876 2>/dev/null && echo READY && break; done",
            timeout=35.0,
        )
        if "READY" not in bridge_ready:
            return (
                f"SimVision launched on {display} but TCP bridge not ready on port 9876.\n"
                "Ensure ~/.simvisionrc sources mcp_bridge.tcl.\n"
                "Setup: echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc\n"
                "Fallback: use output_mode='csv_diff' for text-based comparison."
            )

        # 4. Connect bridge
        await connect_simulator(host="localhost", port=9876)
        bridge = _get_simvision_bridge()

        # 5. Open shm_after as second database
        try:
            await bridge.execute(
                f'database -open -shm -into cmp_after {shm_after}',
                timeout=30.0,
            )
        except Exception as e:
            return f"SimVision connected but failed to open shm_after: {e}"

        # 6. Add BEFORE group (signals from primary / default database)
        sig_str = " ".join(signals)
        try:
            await bridge.execute(
                f"__WAVEFORM_ADD_GROUP__ BEFORE {sig_str}",
                timeout=30.0,
            )
        except Exception as e:
            return f"SimVision open but BEFORE group add failed: {e}"

        # 7. Add AFTER group (signals qualified with cmp_after database scope)
        # Signals from a named database are accessed as {db_name}.{signal_path}
        after_signals = " ".join(f"cmp_after.{s}" for s in signals)
        try:
            await bridge.execute(
                f"__WAVEFORM_ADD_GROUP__ AFTER {after_signals}",
                timeout=30.0,
            )
        except Exception as e:
            return f"BEFORE group added but AFTER group failed: {e}"

        display_num = display.lstrip(":")
        vnc_port = 5900 + int(display_num)
        return (
            f"=== compare_waveforms (simvision mode) ===\n"
            f"BEFORE: {shm_before}\n"
            f"AFTER:  {shm_after}\n\n"
            f"SimVision launched on {display}.\n"
            f"Connect VNC viewer to localhost:{vnc_port}\n\n"
            f"Waveform groups added:\n"
            f"  BEFORE — {len(signals)} signal(s) from primary database\n"
            f"  AFTER  — {len(signals)} signal(s) from cmp_after database\n\n"
            f"Use csv_diff mode for automated signal diffing without GUI."
        )

    # ------------------------------------------------------------------ #
    # csv_diff mode (default)                                              #
    # ------------------------------------------------------------------ #
    start_ns = time_range_ns[0] if len(time_range_ns) >= 1 else 0
    end_ns = time_range_ns[1] if len(time_range_ns) >= 2 else 0

    try:
        csv_b = await csv_cache.extract(shm_before, signals, start_ns, end_ns, missing_ok=True)
        csv_a = await csv_cache.extract(shm_after, signals, start_ns, end_ns, missing_ok=True)
    except RuntimeError as e:
        return f"ERROR extracting CSV: {e}"

    def _load_rows(path: str) -> dict[int, dict]:
        rows: dict[int, dict] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                rows[int(row.get("time", 0))] = row
        return rows

    rows_b = _load_rows(csv_b)
    rows_a = _load_rows(csv_a)

    all_times = sorted(set(rows_b) | set(rows_a))
    diffs: dict[str, list[tuple]] = {s: [] for s in signals}

    for t in all_times:
        rb = rows_b.get(t, {})
        ra = rows_a.get(t, {})
        for sig in signals:
            vb = rb.get(sig, "?")
            va = ra.get(sig, "?")
            if vb != va:
                diffs[sig].append((t, vb, va))

    lines = [
        f"=== Waveform Comparison ===",
        f"BEFORE: {shm_before}",
        f"AFTER:  {shm_after}",
    ]
    changed = 0
    first_time: int | None = None

    for sig in signals:
        sig_diffs = diffs[sig]
        lines.append(f"\nSignal: {sig}")
        if not sig_diffs:
            lines.append("  (no differences)")
        else:
            changed += 1
            for t, vb, va in sig_diffs[:10]:
                lines.append(f"  Time {t}ns: BEFORE={vb} | AFTER={va}  ← CHANGED")
                if first_time is None or t < first_time:
                    first_time = t
            if len(sig_diffs) > 10:
                lines.append(f"  ... ({len(sig_diffs) - 10} more)")

    lines.append("")
    lines.append(
        f"Result: {changed} signal(s) changed, "
        f"{len(signals) - changed} unchanged."
    )
    if first_time is not None:
        lines.append(f"First change: {first_time}ns")
    return "\n".join(lines)


@mcp.tool()
async def export_debug_context(
    test_name: str,
    bug_description: str,
    root_cause: str,
    evidence: list[dict],
    related_code: list[dict],
    signals_to_check: list[str],
    suggested_fix: str = "",
    output_path: str = "",
) -> str:
    """Export AI analysis as a human-readable Markdown debug context document.

    Generates a structured report with bug summary, root cause, CSV evidence
    table, related code references, and signals to check in SimVision.

    Args:
        test_name:        Test name (e.g. "TOP015").
        bug_description:  One-line bug summary.
        root_cause:       AI-inferred root cause.
        evidence:         List of {"time_ns", "signal", "value", "expected", "meaning"}.
        related_code:     List of {"file", "line", "snippet"}.
        signals_to_check: Signal paths for user to inspect in SimVision.
        suggested_fix:    Optional fix suggestion.
        output_path:      Output file path. Default: /tmp/debug_{test_name}.md
    """
    import time as _time

    if not output_path:
        output_path = f"/tmp/debug_{test_name}_{int(_time.time())}.md"

    content = debug_tools.generate_debug_context_md(
        test_name=test_name,
        bug_description=bug_description,
        root_cause=root_cause,
        evidence=evidence,
        related_code=related_code,
        signals_to_check=signals_to_check,
        suggested_fix=suggested_fix,
    )

    from pathlib import Path as _Path
    _Path(output_path).write_text(content, encoding="utf-8")

    if not _Path(output_path).exists():
        return f"ERROR: Failed to write debug context to {output_path}"

    return f"Debug context exported to: {output_path}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
