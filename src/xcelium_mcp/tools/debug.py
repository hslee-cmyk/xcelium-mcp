"""Debug and analysis tools."""
from __future__ import annotations

import re
import textwrap
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

import xcelium_mcp.csv_cache as csv_cache
import xcelium_mcp.debug_tools as debug_tools
from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.screenshot import ps_to_png
from xcelium_mcp.shell_utils import sanitize_signal_name, validate_path
from xcelium_mcp.sim_runner import (
    resolve_sim_dir,
)
from xcelium_mcp.tcl_bridge import TclError

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

    # _prepare_dump_scope_internal removed in v4.3.
    # dump_signals now flows through _preprocess_setup_tcl → _resolve_probe_signals.


# ---------------------------------------------------------------------------
# Module-level implementation helpers (extracted for safe forward-reference)
# ---------------------------------------------------------------------------

async def _bisect_signal_dump_impl(
    shm_path: str,
    signal: str,
    op: str,
    value: str,
    start_ns: int = 0,
    end_ns: int = 0,
    context_signals: list[str] | None = None,
) -> str:
    """Implementation of bisect_signal Mode A — callable without a registered tool reference."""
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
        return (
            f"Signal '{signal}' not found in SHM.\n"
            f"{result['error']}\n\n"
            "Tip: Re-run with sim_batch_run(dump_signals=[...]) to include this signal."
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
        lines.append(f"{prefix}{row.get('_ns', row.get('SimTime', row.get('time', '?'))):>10} | {vals}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP, bridges: BridgeManager) -> dict:

    @mcp.tool()
    async def watch(
        action: str,
        signal: str = "",
        op: str = "==",
        value: str = "",
        type: str = "watch",
        watch_id: str = "all",
    ) -> str:
        """Manage watchpoints and breakpoints: set or clear.

        Args:
            action:   "set" — create a watchpoint or breakpoint.
                      "clear" — remove watchpoints. Use watch_id="all" or a specific ID.
            signal:   Full hierarchical signal path (required for action="set").
            op:       Comparison operator ("==", "!=", ">", "<", ">=", "<=").
            value:    Target value in Verilog format (e.g. "8'h10", "4'b1010").
            type:     "watch" — watchpoint via __WATCH__ protocol (default).
                      "breakpoint" — conditional breakpoint via stop -create.
            watch_id: Watchpoint ID to clear, or "all" (action="clear" only).
        """
        bridge = bridges.xmsim

        if action == "set":
            if not signal:
                return "ERROR: 'signal' is required for action='set'."
            if type == "breakpoint":
                condition = f"{signal} {op} {value}"
                m = re.match(r'^(\S+)\s*(==|!=|>|<|>=|<=)\s*(.+)$', condition.strip())
                if m and '.' in m.group(1):
                    sig, bop, val = m.group(1), m.group(2), m.group(3).strip()
                    tcl_cond = '{[value ' + sig + '] ' + bop + ' "' + val + '"}'
                    cmd = f"stop -create -condition {tcl_cond}"
                else:
                    cmd = f"stop -create -condition {condition}"
                result = await bridge.execute(cmd)
                return f"Breakpoint set: {result}"
            else:
                result = await bridge.execute(f"__WATCH__ {signal} {op} {value}")
                return f"Watchpoint set: {result}"

        elif action == "clear":
            result = await bridge.execute(f"__WATCH_CLEAR__ {watch_id}")
            return result

        else:
            return f"ERROR: Unknown action '{action}'. Use 'set' or 'clear'."

    @mcp.tool()
    async def probe(
        action: str,
        signals: list[str] | None = None,
        scope: str = "",
        shm_path: str = "",
        depth: str = "all",
    ) -> str:
        """Control SHM waveform probes: add signals or enable/disable recording.

        Args:
            action:   "add" — add probe signals to the session.
                      "enable" — start/resume SHM recording.
                      "disable" — pause SHM recording to save disk space.
            signals:  Signal paths to add (required for action="add").
            scope:    Hierarchical scope for enable/disable (empty = all probes).
            shm_path: SHM file for probe data (action="add" only). Empty = current session SHM.
            depth:    Probe depth for action="add" ("all", "1", "2", ...).
        """
        bridge = bridges.xmsim

        if action == "add":
            if not signals:
                return "ERROR: 'signals' is required for action='add'."
            sig_str = " ".join(signals)
            if shm_path:
                cmd = f"probe -create {{{sig_str}}} -shm {shm_path} -depth {depth}"
            else:
                cmd = f"probe -create {{{sig_str}}} -shm -depth {depth}"
            result = await bridge.execute(cmd)
            return f"Probe added for {len(signals)} signal(s). {result}"

        elif action in ("enable", "disable"):
            cmd = f"__PROBE_CONTROL__ {action} {scope}" if scope else f"__PROBE_CONTROL__ {action}"
            result = await bridge.execute(cmd)
            return result

        else:
            return f"ERROR: Unknown action '{action}'. Use 'add', 'enable', or 'disable'."

    @mcp.tool()
    async def bisect_signal(
        signal: str,
        op: str,
        value: str,
        start_ns: int = 0,
        end_ns: int = 0,
        precision_ns: int = 1000,
        shm_path: str = "",
        context_signals: list[str] | None = None,
    ) -> str:
        """Find when a signal condition first becomes true.

        Mode A (preferred, no active simulator required): when shm_path is given,
        extracts CSV from SHM and performs in-memory binary search.
        No bridge connection needed.

        Mode B (bridge, legacy): when shm_path is empty and a bridge is connected,
        uses the simulator's native __BISECT__ binary search with save/restore.

        Args:
            signal:          Full hierarchical signal path.
            op:              Comparison operator: "eq","ne","gt","lt","change"
                             (bridge mode also accepts "==", "!=", etc.)
            value:           Target value (hex/dec/oct; ignored for "change").
            start_ns:        Start of search range in nanoseconds.
            end_ns:          End of search range in nanoseconds (0 = to end).
            precision_ns:    (Bridge mode) Stop when range < this (default 1000ns).
            shm_path:        SHM dump path for Mode A (CSV-based). Empty = Mode B.
            context_signals: (Mode A) Additional signals to include in CSV for context.
        """
        # S-1 fix: sanitize signal name before Tcl interpolation
        try:
            signal = sanitize_signal_name(signal)
            if context_signals:
                context_signals = [sanitize_signal_name(s) for s in context_signals]
        except ValueError as e:
            return f"ERROR: {e}"

        if shm_path:
            # Mode A: SHM dump → CSV → in-memory search
            return await _bisect_signal_dump_impl(
                shm_path=shm_path,
                signal=signal,
                op=op,
                value=value,
                start_ns=start_ns,
                end_ns=end_ns,
                context_signals=context_signals,
            )

        # Mode B: bridge-based binary search (legacy)
        bridge = bridges.xmsim
        cmd = f"__BISECT__ {signal} {op} {value} {start_ns} {end_ns} {precision_ns}"
        result = await bridge.execute(cmd, timeout=600.0)
        return result

    @mcp.tool()
    async def debug_snapshot(
        mode: str = "snapshot",
        # snapshot mode args
        target: str = "auto",
        # tcl mode args
        shm_path: str = "",
        signals: list[str] | None = None,
        center_time_ns: int = 0,
        zoom_range_ns: int = 10000,
        markers: list[dict] | None = None,
        context_note: str = "",
        output_path: str = "",
        # export mode args
        test_name: str = "",
        bug_description: str = "",
        root_cause: str = "",
        evidence: list[dict] | None = None,
        related_code: list[dict] | None = None,
        signals_to_check: list[str] | None = None,
        suggested_fix: str = "",
    ) -> list | str:
        """Debug snapshot, Tcl script generation, or context export.

        Args:
            mode: "snapshot" — comprehensive debug snapshot with signal values + screenshot.
                  "tcl" — generate SimVision Tcl script for offline debugging.
                  "export" — export AI analysis as Markdown debug context document.
            target:          (snapshot) "xmsim"|"simvision"|"auto".
            shm_path:        (tcl) SHM dump file path.
            signals:         (tcl) Signal paths to add to waveform.
            center_time_ns:  (tcl) Bug time — waveform zoomed to center +/- zoom_range_ns.
            zoom_range_ns:   (tcl) Half-width of zoom range in nanoseconds.
            markers:         (tcl) List of {"time_ns": int, "label": str} dicts.
            context_note:    (tcl/export) AI analysis summary.
            output_path:     (tcl/export) Output file path. Auto-generated if empty.
            test_name:       (export) Test name (e.g. "TOP015").
            bug_description: (export) One-line bug summary.
            root_cause:      (export) AI-inferred root cause.
            evidence:        (export) List of {"time_ns", "signal", "value", "expected", "meaning"}.
            related_code:    (export) List of {"file", "line", "snippet"}.
            signals_to_check:(export) Signal paths for user to inspect in SimVision.
            suggested_fix:   (export) Optional fix suggestion.
        """
        # S-3 fix: validate output_path
        if output_path:
            err = validate_path(output_path, "output_path")
            if err:
                return err

        if mode == "snapshot":
            return await _run_debugger_mode(bridges, target)
        elif mode == "tcl":
            return await _generate_debug_tcl(
                shm_path=shm_path,
                signals=signals or [],
                center_time_ns=center_time_ns,
                zoom_range_ns=zoom_range_ns,
                markers=markers,
                context_note=context_note,
                output_path=output_path,
            )
        elif mode == "export":
            return await _export_debug_context(
                test_name=test_name,
                bug_description=bug_description,
                root_cause=root_cause,
                evidence=evidence or [],
                related_code=related_code or [],
                signals_to_check=signals_to_check or [],
                suggested_fix=suggested_fix,
                output_path=output_path,
            )
        else:
            return f"ERROR: Unknown mode '{mode}'. Use 'snapshot', 'tcl', or 'export'."

    # --- Internal implementations for debug_snapshot ---

    async def _run_debugger_mode(bridges: BridgeManager, target: str) -> list:
        bridge = bridges.get_bridge(target)
        sections: list[str] = []

        # 1. Simulation state + breakpoints (single round-trip)
        try:
            snapshot = await bridge.execute("__DEBUG_SNAPSHOT__")
            pos = scope_val = stops = ""
            for line in snapshot.splitlines():
                if line.startswith("POSITION:"):
                    pos = line[9:]
                elif line.startswith("SCOPE:"):
                    scope_val = line[6:]
                elif line.startswith("STOPS:"):
                    stops = line[6:]
        except (TclError, ConnectionError) as e:
            pos = scope_val = f"(error: {e})"
            stops = "(unavailable)"

        sections.append("## Simulation State")
        sections.append(f"- **Position**: `{pos}`")
        sections.append(f"- **Scope**: `{scope_val}`")

        # 2. Signal values in current scope (up to 50)
        sections.append("\n## Signal Values (current scope)")
        try:
            sig_list = await bridge.execute("describe *")
            lines = sig_list.strip().splitlines()[:50]
            if lines:
                for line in lines:
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
        sections.append(f"```\n{stops}\n```")

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
        - `inspect_signal(action="value")` — read specific signals of interest
        - `inspect_signal(action="drivers")` — trace X/Z values to their source
        - `waveform(action="add")` — add signals to waveform for visual inspection
        - `sim_run` with duration — step the simulation forward
        - `watch(action="set")` — set conditional breakpoints on suspicious signals
        """))

        report = "\n".join(sections)

        # 5. Try to capture a screenshot
        try:
            cfg = None
            try:
                sim_dir = await resolve_sim_dir()
                cfg = await load_sim_config(sim_dir)
            except ValueError:
                pass
            ps_path = await bridge.screenshot()
            png_bytes = await ps_to_png(ps_path, config=cfg)
            screenshot = Image(data=png_bytes, format="png")
            return [report, screenshot]
        except Exception as e:
            report += f"\n\n*(Screenshot unavailable: {e})*"
            return [report]

    async def _generate_debug_tcl(
        shm_path: str,
        signals: list[str],
        center_time_ns: int,
        zoom_range_ns: int,
        markers: list[dict] | None,
        context_note: str,
        output_path: str,
    ) -> str:
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

    async def _export_debug_context(
        test_name: str,
        bug_description: str,
        root_cause: str,
        evidence: list[dict],
        related_code: list[dict],
        signals_to_check: list[str],
        suggested_fix: str,
        output_path: str,
    ) -> str:
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
        return f"Debug context exported to: {output_path}"

    return {"generate_debug_tcl": debug_snapshot}
