"""Debug and analysis tools."""
from __future__ import annotations

import json
import csv
import re
import textwrap
import time
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclBridge, TclError
from xcelium_mcp.screenshot import ps_to_png
from xcelium_mcp.sim_runner import (
    UserInputRequired,
    ssh_run,
    build_redirect,
    _get_default_sim_dir,
    _parse_shm_path,
    _parse_time_ns,
    _resolve_exec_cmd,
    _load_or_detect_runner,
    load_sim_config,
)
import xcelium_mcp.csv_cache as csv_cache
import xcelium_mcp.debug_tools as debug_tools
import xcelium_mcp.checkpoint_manager as checkpoint_manager


# ---------------------------------------------------------------------------
# Internal helpers
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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP, bridges: BridgeManager) -> dict:

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
        bridge = bridges.xmsim

        # Parse "signal op value" format for hierarchical signal paths
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

    @mcp.tool()
    async def run_debugger_mode(target: str = "auto") -> list:
        """Comprehensive debug snapshot: simulation state + signal values + screenshot + debugging guide.

        Returns a combined text report and waveform screenshot for AI-assisted hardware debugging.

        Args:
            target: "xmsim" | "simvision" | "auto" (default: auto).
        """
        bridge = bridges.get_bridge(target)
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
        bridge = bridges.xmsim
        result = await bridge.execute(f"__WATCH__ {signal} {op} {value}")
        return f"Watchpoint set: {result}"

    @mcp.tool()
    async def watch_clear(watch_id: str = "all") -> str:
        """Clear watchpoints. Use "all" to clear all, or a specific stop ID.

        Args:
            watch_id: Watchpoint ID to clear, or "all" for all watchpoints.
        """
        bridge = bridges.xmsim
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
        bridge = bridges.xmsim
        cmd = f"__PROBE_CONTROL__ {mode} {scope}" if scope else f"__PROBE_CONTROL__ {mode}"
        result = await bridge.execute(cmd)
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
        bridge = bridges.xmsim
        cmd = f"__BISECT__ {signal} {op} {value} {start_ns} {end_ns} {precision_ns}"
        result = await bridge.execute(cmd, timeout=600.0)
        return result

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

        # 1. Restore — import from checkpoint module's registered tool
        from xcelium_mcp.tools.checkpoint import _restore_checkpoint_impl
        restore_result = await _restore_checkpoint_impl(bridges, checkpoint_name, resolved_dir)
        if "ERROR" in restore_result or "restore failed" in restore_result:
            return f"Restore failed: {restore_result}"

        # 2. Add probe signals
        if probe_signals:
            bridge = bridges.xmsim
            sig_str = " ".join(probe_signals)
            try:
                await bridge.execute(
                    f"probe -create {{{sig_str}}} -shm -depth all", timeout=30.0
                )
            except Exception as e:
                return f"Restore succeeded but probe_add_signals failed: {e}\nRestore result: {restore_result}"

        # 3. Run (with or without watchpoint)
        bridge = bridges.xmsim
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
        bridge = bridges.xmsim
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
        context_signals: list[str] | None = None,
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
        if context_signals is None:
            context_signals = []
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
            prefix = "\u2605 " if i == match_idx else "  "
            vals = " | ".join(row.get(c, "?") for c in cols)
            lines.append(f"{prefix}{row.get('time', '?'):>10} | {vals}")

        return "\n".join(lines)

    @mcp.tool()
    async def request_additional_signals(
        missing_signals: list[str],
        shm_path: str,
        bug_time_ns: int = 0,
        available_checkpoints: list[str] | None = None,
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
        if available_checkpoints is None:
            available_checkpoints = []
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
            "    \u2192 sim_batch_run(dump_signals=[missing_signals])\n"
            "    \u2192 New SHM with all signals included\n"
            "    Cost: full simulation time"
        )
        lines.append("")

        a_prime = (
            "[A'] Restore from nearest checkpoint + add probes + partial run\n"
            "    \u2192 restore_checkpoint + probe_add_signals + sim_run\n"
            "    \u2192 Faster than full re-run\n"
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
            "    \u2192 probe_add_signals(missing_signals) + sim_run\n"
            "    \u2192 Requires active SimVision connection (connect_simulator)\n"
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
        markers: list[dict] | None = None,
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
            center_time_ns: Bug time — waveform zoomed to center \u00b1 zoom_range_ns.
            zoom_range_ns:  Half-width of zoom range in nanoseconds.
            markers:        List of {"time_ns": int, "label": str} dicts.
            context_note:   AI analysis summary printed to SimVision console.
            output_path:    Output .tcl path. Auto-generated in SHM parent dir if empty.
        """
        if markers is None:
            markers = []

        if not output_path:
            ts = int(time.time())
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
        if not output_path:
            output_path = f"/tmp/debug_{test_name}_{int(time.time())}.md"

        content = debug_tools.generate_debug_context_md(
            test_name=test_name,
            bug_description=bug_description,
            root_cause=root_cause,
            evidence=evidence,
            related_code=related_code,
            signals_to_check=signals_to_check,
            suggested_fix=suggested_fix,
        )

        Path(output_path).write_text(content, encoding="utf-8")

        if not Path(output_path).exists():
            return f"ERROR: Failed to write debug context to {output_path}"

        return f"Debug context exported to: {output_path}"

    return {"generate_debug_tcl": generate_debug_tcl}
