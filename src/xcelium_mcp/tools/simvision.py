"""SimVision GUI tools."""
from __future__ import annotations

import asyncio
import csv
from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclBridge, TclError
from xcelium_mcp.sim_runner import (
    ssh_run,
    sq,
    login_shell_cmd,
    build_redirect,
    _parse_shm_path,
    _parse_time_ns,
    get_user_tmp_dir,
    get_default_sim_dir,
)
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.env_detection import _detect_vnc_display
from xcelium_mcp.batch_runner import resolve_test_name

# Type aliases for cross-tool callable references
WaveformAddSignalsFn = Callable[..., Coroutine[Any, Any, str]]
ConnectSimulatorFn = Callable[..., Coroutine[Any, Any, str]]
GenerateDebugTclFn = Callable[..., Coroutine[Any, Any, str]]


def register(
    mcp: FastMCP,
    bridges: BridgeManager,
    *,
    waveform_add_signals_fn: WaveformAddSignalsFn,
    connect_simulator_fn: ConnectSimulatorFn,
    generate_debug_tcl_fn: GenerateDebugTclFn,
    csv_cache: Any,
) -> None:
    """Register SimVision GUI tools.

    Args:
        mcp: FastMCP server instance.
        bridges: BridgeManager for simulator bridge access.
        waveform_add_signals_fn: Reference to waveform_add_signals tool.
        connect_simulator_fn: Reference to connect_simulator tool.
        generate_debug_tcl_fn: Reference to generate_debug_tcl tool.
        csv_cache: csv_cache module (extract, bisect_csv).
    """

    @mcp.tool()
    async def database_open(shm_path: str, name: str = "") -> str:
        """Open SHM database. Uses correct syntax based on bridge type.

        SimVision: 'database open path'
        xmsim:     'database -open path -shm'
        Routes to SimVision bridge first, falls back to xmsim.
        """
        # SimVision bridge first
        if bridges.simvision_raw and bridges.simvision_raw.connected:
            bridge = bridges.simvision_raw
            # Check if already open (database open on already-opened SHM hangs SimVision)
            try:
                existing = await bridge.execute("database find")
                if existing.strip():
                    # Same DB? → skip.  Different DB? → close old, open new.
                    # Compare: shm_path may contain full path, existing is just db name
                    if existing.strip() in shm_path or shm_path in existing.strip():
                        return f"Database already open (SimVision): {existing.strip()}"
                    # Different DB — close existing first
                    try:
                        await bridge.execute(f"database close {existing.strip()}")
                    except (TclError, ConnectionError, TimeoutError):
                        pass
            except (TclError, ConnectionError, TimeoutError):
                pass
            name_opt = f" -name {name}" if name else ""
            try:
                result = await bridge.execute(f"database open {shm_path}{name_opt}")
                return f"Database opened (SimVision): {result}"
            except (TclError, ConnectionError, TimeoutError) as e:
                return f"ERROR: SimVision database open failed: {e}"

        # xmsim fallback
        try:
            bridge = bridges.xmsim
            result = await bridge.execute(f"database -open {shm_path} -shm")
            return f"Database opened (xmsim): {result}"
        except (ConnectionError, TclError, TimeoutError) as e:
            return f"ERROR: Could not open database: {e}"

    @mcp.tool()
    async def simvision_setup(
        shm_path: str = "",
        signals: list[str] | None = None,
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
        if signals is None:
            signals = []
        bridge = bridges.simvision
        results = []

        if shm_path:
            db_result = await database_open(shm_path)
            results.append(db_result)

        # waveform_add_signals handles window creation + dedup
        if signals:
            add_result = await waveform_add_signals_fn(signals=signals)
            results.append(add_result)

        if zoom_start and zoom_end:
            try:
                await bridge.execute(f"waveform xview limits {zoom_start} {zoom_end}")
                results.append(f"Zoomed to {zoom_start} – {zoom_end}")
            except TclError as e:
                results.append(f"Zoom failed: {e}")

        return "\n".join(results) if results else "No actions performed."

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
        # 0. Disconnect existing (max 1 constraint)
        if bridges.simvision_raw and bridges.simvision_raw.connected:
            await bridges.simvision_raw.disconnect()
            bridges.set_simvision(None)

        # 1. Check existing SimVision bridge → auto-connect
        user_tmp = await get_user_tmp_dir()
        r = await ssh_run(f"cat {user_tmp}/bridge_ready_* 2>/dev/null")
        for line in r.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "simvision":
                port = int(parts[0])
                bridge = TclBridge(host="localhost", port=port)
                try:
                    ping = await bridge.connect()
                    bridges.set_simvision(bridge)
                    return f"SimVision already running — connected to port {port} (ping={ping})"
                except Exception:
                    pass

        # 2. Resolve sim_dir + config
        resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
        if not resolved_dir:
            return "ERROR: No sim_dir. Run sim_discover first."
        config = await load_sim_config(resolved_dir)
        runner = config.get("runner", {}) if config else {}

        # 3. Resolve SHM (glob)
        if not shm_path:
            if test_name:
                test_name = await resolve_test_name(test_name, resolved_dir)
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
            shell_cmd = login_shell_cmd(login_shell, inner_cmd)

        user_tmp = await get_user_tmp_dir()
        log_file = f"{user_tmp}/simvision_start.log"
        cmd = f"(nohup {shell_cmd} {build_redirect(log_file)} < /dev/null &)"
        await ssh_run(cmd, timeout=15)

        # 7. Wait for bridge ready + auto-connect
        for i in range(30):
            await asyncio.sleep(2)
            r = await ssh_run(f"cat {user_tmp}/bridge_ready_* 2>/dev/null")
            for line in r.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "simvision":
                    port = int(parts[0])
                    bridge = TclBridge(host="localhost", port=port)
                    try:
                        ping = await bridge.connect()
                        bridges.set_simvision(bridge)
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
        signals: list[str] | None = None,
        zoom_start: str = "",
        zoom_end: str = "",
        auto_reload: bool = True,
    ) -> str:
        """Connect SimVision to running xmsim for live waveform viewing.

        Requires both xmsim and SimVision bridges connected.
        Opens xmsim's SHM in SimVision, adds signals, enables auto-reload.
        """
        if signals is None:
            signals = []
        try:
            xmsim = bridges.xmsim
        except ConnectionError as e:
            return f"ERROR: xmsim bridge not connected — {e}"
        try:
            sv = bridges.simvision
        except ConnectionError as e:
            return f"ERROR: SimVision bridge not connected — {e}"
        results = []

        # 1. Get xmsim SHM info + sim time
        shm_info = ""
        sim_time = ""
        try:
            shm_info = await xmsim.execute("database -show")
            sim_time = await xmsim.execute("where")
            results.append(f"xmsim at {sim_time.strip()}, SHM: {shm_info.strip()}")
        except (TclError, ConnectionError, TimeoutError) as e:
            results.append(f"xmsim info: {e}")

        # 2. Open SHM in SimVision — skip if already open
        sv_db = ""
        try:
            sv_db = await sv.execute("database find")
        except (TclError, ConnectionError, TimeoutError):
            pass

        if sv_db.strip():
            results.append(f"SimVision database already open: {sv_db.strip()}")
        elif shm_info.strip():
            shm_path = _parse_shm_path(shm_info)
            if not shm_path:
                return (
                    f"ERROR: Could not parse SHM path from xmsim database list:\n{shm_info}\n"
                    "Open SHM manually: database_open(shm_path='...')"
                )
            try:
                await sv.execute(f"database open {shm_path}")
                results.append(f"SimVision opened: {shm_path}")
            except (TclError, ConnectionError, TimeoutError) as e:
                results.append(f"SHM open failed: {e}")

        # 3. Add signals (reuses waveform_add_signals — window auto-create + dedup)
        if signals:
            try:
                add_result = await waveform_add_signals_fn(signals=signals)
                results.append(add_result)
            except (ConnectionError, TimeoutError) as e:
                results.append(f"Add signals failed: {e}")

        # 4. Zoom — auto-compute from sim time if zoom_start/zoom_end not given
        if not zoom_start or not zoom_end:
            cur_ns = _parse_time_ns(sim_time)
            zoom_start = f"{max(0, cur_ns - 1_000_000)}ns"
            zoom_end = f"{cur_ns}ns"
        try:
            await sv.execute(f"waveform xview limits {zoom_start} {zoom_end}")
            results.append(f"Zoomed to {zoom_start} – {zoom_end}")
        except (TclError, ConnectionError, TimeoutError):
            pass

        # 5. Auto-reload (with database name for SimVision)
        if auto_reload:
            try:
                db_name = sv_db.strip() if sv_db.strip() else ""
                reload_cmd = f"database reload {db_name}" if db_name else "database reload"
                await sv.execute(
                    f"proc _mcp_auto_reload {{}} {{ "
                    f"  catch {{{reload_cmd}}}; "
                    f"  after 2000 _mcp_auto_reload "
                    f"}}; "
                    f"after 2000 _mcp_auto_reload"
                )
                results.append("Auto-reload enabled (2s interval)")
            except (TclError, ConnectionError, TimeoutError) as e:
                results.append(f"Auto-reload failed: {e}")

        return "\n".join(results)

    @mcp.tool()
    async def simvision_live_stop() -> str:
        """Stop SimVision live waveform auto-reload."""
        sv = bridges.simvision
        try:
            await sv.execute("foreach id [after info] { after cancel $id }")
            return "Auto-reload stopped."
        except TclError as e:
            return f"ERROR: {e}"

    @mcp.tool()
    async def attach_to_simvision(
        port: int = 0,
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
            port:    TCP bridge port. 0 = auto-detect from ready files.
            timeout: Connection wait timeout in seconds.
        """
        return await connect_simulator_fn(host="localhost", port=port, target="simvision", timeout=timeout)

    @mcp.tool()
    async def open_debug_view(
        shm_path: str,
        signals: list[str],
        center_time_ns: int,
        zoom_range_ns: int = 10000,
        cursor_time_ns: int = 0,
        markers: list[dict] | None = None,
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
        if markers is None:
            markers = []
        # 1. VNC check
        vnc_check = await ssh_run(
            f"vncserver -list 2>/dev/null | grep '{display}' || echo NONE",
            timeout=10.0,
        )
        if "NONE" in vnc_check or display not in vnc_check:
            # Fallback: generate offline Tcl script
            tcl_result = await generate_debug_tcl_fn(
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
            f"DISPLAY={sq(display)} simvision {sq(shm_path)} &",
            timeout=5.0,
        )

        # 3. Wait for SimVision bridge ready file (30s)
        user_tmp = await get_user_tmp_dir()
        bridge_ready = False
        for _i in range(15):
            await asyncio.sleep(2)
            r = await ssh_run(f"cat {user_tmp}/bridge_ready_* 2>/dev/null")
            for line in r.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == "simvision":
                    bridge_ready = True
                    break
            if bridge_ready:
                break

        if not bridge_ready:
            tcl_result = await generate_debug_tcl_fn(
                shm_path=shm_path, signals=signals, center_time_ns=center_time_ns,
                zoom_range_ns=zoom_range_ns, markers=markers, context_note=context_note,
            )
            return (
                f"SimVision launched but bridge not ready. Use offline script:\n"
                f"{tcl_result}"
            )

        # 4. Connect (auto-detect SimVision port from ready files)
        await connect_simulator_fn(port=0, target="simvision")
        bridge = bridges.simvision

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
        time_range_ns: list[int] | None = None,
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
        if time_range_ns is None:
            time_range_ns = []

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
                f"DISPLAY={sq(display)} simvision {sq(shm_before)} &",
                timeout=5.0,
            )

            # 3. Wait for SimVision bridge ready file (30s)
            user_tmp = await get_user_tmp_dir()
            bridge_ready = False
            for _i in range(15):
                await asyncio.sleep(2)
                r = await ssh_run(f"cat {user_tmp}/bridge_ready_* 2>/dev/null")
                for line in r.strip().splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[1] == "simvision":
                        bridge_ready = True
                        break
                if bridge_ready:
                    break
            if not bridge_ready:
                return (
                    f"SimVision launched on {display} but bridge not ready after 30s.\n"
                    "Ensure ~/.simvisionrc sources mcp_bridge.tcl.\n"
                    "Fallback: use output_mode='csv_diff' for text-based comparison."
                )

            # 4. Connect bridge (auto-detect SimVision port)
            await connect_simulator_fn(port=0, target="simvision")
            bridge = bridges.simvision

            # 5. Open shm_after as second database (SimVision syntax)
            try:
                await bridge.execute(
                    f'database open {shm_after} -name cmp_after',
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
                for row in csv.DictReader(f):
                    # Use SimTime (simvisdbutil default) or time column, consistent with bisect_csv
                    raw_time = row.get("SimTime") or row.get("time") or "0"
                    rows[int(raw_time)] = row
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
