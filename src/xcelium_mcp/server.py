"""Xcelium MCP Server — FastMCP server with 25 tools for SimVision control."""

from __future__ import annotations

import asyncio
import textwrap

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.tcl_bridge import TclBridge, TclError
from xcelium_mcp.screenshot import ps_to_png

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
    """Restart the simulation from time 0."""
    bridge = _get_bridge()
    await bridge.execute("restart")
    return "Simulation restarted to time 0."


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
        scope: Hierarchical scope path (e.g. "/tb/dut").
        pattern: Glob pattern to filter signals (default "*").
    """
    bridge = _get_bridge()

    # Change to the target scope and list
    await bridge.execute(f"scope {scope}")
    result = await bridge.execute(f"scope -describe {pattern}")
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
        sig_list = await bridge.execute("scope -describe *")
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
async def save_checkpoint(name: str = "") -> str:
    """Save a simulation checkpoint for later restoration.

    Checkpoints capture the complete simulator state. Use restore_checkpoint
    to return to this point without re-simulating from time 0.

    Args:
        name: Checkpoint name (alphanumeric, e.g. "chk_10ms"). Auto-generated if empty.
    """
    bridge = _get_bridge()
    cmd = f"__SAVE__ {name}" if name else "__SAVE__"
    result = await bridge.execute(cmd)
    return result


@mcp.tool()
async def restore_checkpoint(name: str = "") -> str:
    """Restore simulation to a previously saved checkpoint.

    Args:
        name: Checkpoint name to restore. Empty = last saved checkpoint.
    """
    bridge = _get_bridge()
    cmd = f"__RESTORE__ {name}" if name else "__RESTORE__"
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
) -> str:
    """Find when a signal condition first becomes true using automated binary search.

    Internally saves checkpoints and repeatedly restores/runs with watchpoints
    to narrow down the exact time. Returns iteration log and final time range.

    Args:
        signal: Full hierarchical signal path.
        op: Comparison operator (e.g. "==").
        value: Target value (e.g. "8'h11").
        start_ns: Start of search range in nanoseconds.
        end_ns: End of search range in nanoseconds.
        precision_ns: Stop when range is narrower than this (default 1000ns).
    """
    bridge = _get_bridge()
    cmd = (
        f"__BISECT__ {signal} {op} {value} {start_ns} {end_ns} {precision_ns}"
    )
    result = await bridge.execute(cmd, timeout=600.0)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
