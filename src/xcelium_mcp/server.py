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
    _get_default_sim_dir,
    _load_or_detect_runner,
    _resolve_exec_cmd,
    _run_batch_regression,
    _run_batch_single,
    ssh_run,
)

# ---------------------------------------------------------------------------
# Server & global bridge instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "xcelium-mcp",
    instructions="MCP server for Cadence Xcelium/SimVision simulator control",
)

_bridge: TclBridge | None = None


def _get_bridge() -> TclBridge:
    """Return the active bridge or raise."""
    if _bridge is None or not _bridge.connected:
        raise ConnectionError(
            "Not connected to SimVision. Call connect_simulator first."
        )
    return _bridge


# ===================================================================
# Phase 5 — Connection + Simulation Control (tools 1–7)
# ===================================================================

@mcp.tool()
async def connect_simulator(
    host: str = "localhost",
    port: int = 9876,
    timeout: float = 30.0,
) -> str:
    """Connect to a SimVision instance running mcp_bridge.tcl.

    Args:
        host: SimVision host (use localhost with SSH tunnel for remote).
        port: TCP port of the Tcl bridge (default 9876).
        timeout: Connection timeout in seconds.
    """
    global _bridge

    if _bridge and _bridge.connected:
        await _bridge.disconnect()

    _bridge = TclBridge(host=host, port=port, timeout=timeout)
    ping = await _bridge.connect()

    # Get current simulation context
    try:
        where = await _bridge.execute("where")
    except TclError:
        where = "(unknown — simulation may not be loaded)"

    return f"Connected to SimVision at {host}:{port} (ping={ping})\nCurrent position: {where}"


@mcp.tool()
async def disconnect_simulator() -> str:
    """Disconnect from the SimVision bridge."""
    global _bridge
    if _bridge:
        await _bridge.disconnect()
        _bridge = None
    return "Disconnected from SimVision."


@mcp.tool()
async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
    """Run the simulation, optionally for a specified duration.

    Args:
        duration: Simulation time to run (e.g. "100ns", "1us"). Empty = run until breakpoint or end.
        timeout: MCP response timeout in seconds (default 600s for gate-level sim support).
    """
    bridge = _get_bridge()
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
    bridge = _get_bridge()
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
    bridge = _get_bridge()
    result = await bridge.execute("__RESTART__")
    return f"Simulation restarted to time 0. ({result})"


@mcp.tool()
async def execute_tcl(
    tcl_cmd: str,
    timeout: int = 30,
) -> str:
    """Execute arbitrary Tcl command in the connected SimVision bridge session.

    Returns raw Tcl output. Raises if not connected or command times out.
    Use for commands not covered by dedicated tools: database -open, probe -create, etc.

    WARNING: State-changing commands (finish, exit, restart) can cause unintended
    termination — caller's responsibility. Prefer dedicated tools when available.

    Args:
        tcl_cmd: Tcl command to execute (single or multi-line).
        timeout: Response timeout in seconds.
    """
    bridge = _get_bridge()
    return await bridge.execute(tcl_cmd, timeout=float(timeout))


@mcp.tool()
async def sim_status() -> str:
    """Get current simulation status (time, scope, state)."""
    bridge = _get_bridge()
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

    Args:
        condition: Tcl expression (e.g. "{/tb/dut/state == 3}").
        name: Optional breakpoint name.
    """
    bridge = _get_bridge()
    cmd = f"stop -condition {condition}"
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
    bridge = _get_bridge()
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
    bridge = _get_bridge()
    result = await bridge.execute(f"describe {signal}")
    return result


@mcp.tool()
async def find_drivers(signal: str) -> str:
    """Find all drivers of a signal (useful for X/Z debugging).

    Args:
        signal: Full hierarchical signal path.
    """
    bridge = _get_bridge()
    result = await bridge.execute(f"drivers {signal}")
    return result


@mcp.tool()
async def list_signals(scope: str, pattern: str = "*") -> str:
    """List signals in a scope, optionally filtered by pattern.

    Args:
        scope: Hierarchical scope path (e.g. "top.hw.u_ext").
        pattern: Glob pattern to filter signals (default "*").
    """
    bridge = _get_bridge()

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
    bridge = _get_bridge()
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
    bridge = _get_bridge()
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
) -> str:
    """Add signals to the SimVision waveform viewer.

    Args:
        signals: List of signal paths to add.
        group_name: Optional group name for organizing signals.
    """
    bridge = _get_bridge()
    sig_str = " ".join(signals)
    cmd = f"waveform add -signals {{{sig_str}}}"
    if group_name:
        cmd = f"waveform add -using {group_name} -signals {{{sig_str}}}"
    result = await bridge.execute(cmd)
    return f"Added {len(signals)} signal(s) to waveform. {result}"


@mcp.tool()
async def waveform_zoom(start_time: str, end_time: str) -> str:
    """Set the waveform viewer time range (zoom to region).

    Args:
        start_time: Start time (e.g. "0ns").
        end_time: End time (e.g. "100ns").
    """
    bridge = _get_bridge()
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
    bridge = _get_bridge()
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
    bridge = _get_bridge()
    ps_path = await bridge.screenshot()
    png_bytes = await ps_to_png(ps_path)
    return Image(data=png_bytes, format="png")


@mcp.tool()
async def run_debugger_mode() -> list:
    """Comprehensive debug snapshot: simulation state + signal values + screenshot + debugging guide.

    Returns a combined text report and waveform screenshot for AI-assisted hardware debugging.
    """
    bridge = _get_bridge()
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
async def shutdown_simulator() -> str:
    """Safely shutdown the simulator, preserving SHM waveform data.

    Closes all SHM databases and terminates xmsim gracefully.
    Always use this instead of disconnect_simulator when ending a debug session.
    WARNING: exit or pkill will lose SHM data. This is the only safe way.
    """
    global _bridge
    bridge = _get_bridge()
    try:
        resp = await bridge.execute_safe("__SHUTDOWN__")
        return f"Simulator shutdown: {resp.body}"
    except (ConnectionError, asyncio.TimeoutError):
        return "Simulator shutdown completed (connection closed)."
    finally:
        _bridge = None


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
    bridge = _get_bridge()
    result = await bridge.execute(f"__WATCH__ {signal} {op} {value}")
    return f"Watchpoint set: {result}"


@mcp.tool()
async def watch_clear(watch_id: str = "all") -> str:
    """Clear watchpoints. Use "all" to clear all, or a specific stop ID.

    Args:
        watch_id: Watchpoint ID to clear, or "all" for all watchpoints.
    """
    bridge = _get_bridge()
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
    bridge = _get_bridge()
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
    bridge = _get_bridge()

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

    bridge = _get_bridge()
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
    bridge = _get_bridge()
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
        bridge = _get_bridge()
        sig_str = " ".join(probe_signals)
        try:
            await bridge.execute(
                f"probe -create {{{sig_str}}} -shm -depth all", timeout=30.0
            )
        except Exception as e:
            return f"Restore succeeded but probe_add_signals failed: {e}\nRestore result: {restore_result}"

    # 3. Run (with or without watchpoint)
    bridge = _get_bridge()
    if watch_signal_path and watch_value:
        await bridge.execute(
            f"stop -create -signal {watch_signal_path} -op {watch_op} -value {watch_value}",
            timeout=10.0,
        )
        run_result = await bridge.execute("run", timeout=600.0)
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
            return (
                "ERROR: No simulation directory found. "
                "Provide sim_dir or run connect_simulator first to register an environment."
            )
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # Load runner config
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
                bridge = _get_bridge()
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
        log = await _run_batch_single(
            sim_dir=resolved_sim_dir,
            test_name=test_name,
            runner=runner,
            rename_dump=rename_dump,
            run_duration=run_duration,
            timeout=timeout,
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
            return (
                "ERROR: No simulation directory found. "
                "Provide sim_dir or configure mcp_registry.json."
            )
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    # Load runner config
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

    # Execute regression
    try:
        summary = await _run_batch_regression(
            sim_dir=resolved_sim_dir,
            test_list=test_list,
            runner=runner,
            from_checkpoint=from_checkpoint,
            rename_dump=rename_dump,
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
    bridge = _get_bridge()
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
    bridge = _get_bridge()

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
        bridge = _get_bridge()

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
