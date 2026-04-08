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
    resolve_sim_dir,
)
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.env_detection import _detect_vnc_display
from xcelium_mcp.batch_runner import resolve_test_name

# Type aliases for cross-tool callable references
WaveformAddImplFn = Callable[..., Coroutine[Any, Any, str]]
ConnectSimulatorFn = Callable[..., Coroutine[Any, Any, str]]
GenerateDebugTclFn = Callable[..., Coroutine[Any, Any, str]]


def register(
    mcp: FastMCP,
    bridges: BridgeManager,
    *,
    waveform_add_impl_fn: WaveformAddImplFn,
    connect_simulator_fn: ConnectSimulatorFn,
    generate_debug_tcl_fn: GenerateDebugTclFn,
    csv_cache: Any,
) -> None:
    """Register SimVision GUI tools.

    Args:
        mcp: FastMCP server instance.
        bridges: BridgeManager for simulator bridge access.
        waveform_add_impl_fn: Reference to internal waveform add implementation.
        connect_simulator_fn: Reference to connect_simulator tool.
        generate_debug_tcl_fn: Reference to debug_snapshot tool (mode="tcl").
        csv_cache: csv_cache module (extract, bisect_csv).
    """

    @mcp.tool()
    async def simvision_connect(
        action: str,
        # start args
        test_name: str = "",
        shm_path: str = "",
        display: str = "",
        sim_dir: str = "",
        # attach args
        port: int = 0,
        timeout: int = 10,
        # open_db args
        name: str = "",
    ) -> str:
        """SimVision connection management: start, attach, or open database.

        Args:
            action:    "start" — start SimVision or connect to already running instance.
                       "attach" — attach to an already-running SimVision session via TCP bridge.
                       "open_db" — open SHM database in SimVision (or xmsim fallback).
            test_name: (start) Test name for SHM lookup. Empty = latest SHM.
            shm_path:  (start/open_db) SHM path. Overrides test_name for start.
            display:   (start) X11 DISPLAY. Empty = auto-detect user's VNC session.
            sim_dir:   (start) Simulation directory. Empty = registry default.
            port:      (attach) TCP bridge port. 0 = auto-detect from ready files.
            timeout:   (attach) Connection wait timeout in seconds.
            name:      (open_db) Database name alias.
        """
        if action == "start":
            return await _simvision_start(test_name, shm_path, display, sim_dir)
        elif action == "attach":
            return await connect_simulator_fn(host="localhost", port=port, target="simvision", timeout=timeout)
        elif action == "open_db":
            return await _database_open(shm_path, name)
        else:
            return f"ERROR: Unknown action '{action}'. Use 'start', 'attach', or 'open_db'."

    async def _database_open(shm_path: str, name: str = "") -> str:
        """Open SHM database. Uses correct syntax based on bridge type."""
        # SimVision bridge first
        if bridges.simvision_raw and bridges.simvision_raw.connected:
            bridge = bridges.simvision_raw
            try:
                existing = await bridge.execute("database find")
                if existing.strip():
                    from pathlib import Path as _Path
                    _shm_p = _Path(shm_path)
                    _shm_stem = _shm_p.parent.stem if _shm_p.parent.suffix == ".shm" else _shm_p.stem
                    if existing.strip() == _shm_stem:
                        return f"Database already open (SimVision): {existing.strip()}"
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

    async def _simvision_start(
        test_name: str, shm_path: str, display: str, sim_dir: str,
    ) -> str:
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
        try:
            resolved_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"
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
                "Start VNC: 'vncserver'\nOr specify: simvision_connect(action='start', display=':1')"
            )
        display_check = await ssh_run(f"xdpyinfo -display {sq(display)} | head -1")
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
                inner_parts.append(f"source {sq(ef)}")
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
    async def simvision(
        action: str,
        # setup params
        shm_path: str = "",
        signals: list[str] | None = None,
        zoom_start: str = "",
        zoom_end: str = "",
        screenshot: bool = False,
        # live params
        auto_reload: bool = True,
    ) -> str:
        """SimVision waveform control: setup, live monitoring start/stop.

        Args:
            action:      "setup" — open SHM + add signals + zoom (one-shot configuration).
                         "live_start" — connect to running xmsim for live waveform viewing.
                         "live_stop" — stop live waveform auto-reload.
            shm_path:    (setup/live) SHM database path. Empty = skip or auto-detect.
            signals:     (setup/live) Signal paths to add to waveform.
            zoom_start:  Zoom start time. Empty = full range (setup) or auto-compute (live).
            zoom_end:    Zoom end time. Empty = full range (setup) or auto-compute (live).
            screenshot:  (setup) True = capture waveform screenshot after setup.
            auto_reload: (live_start) Enable auto-reload (default True).
        """
        if action == "setup":
            if signals is None:
                signals = []
            bridge = bridges.simvision
            results = []

            if shm_path:
                db_result = await _database_open(shm_path)
                results.append(db_result)

            if signals:
                add_result = await waveform_add_impl_fn(signals=signals)
                results.append(add_result)

            if zoom_start and zoom_end:
                try:
                    await bridge.execute(f"waveform xview limits {zoom_start} {zoom_end}")
                    results.append(f"Zoomed to {zoom_start} – {zoom_end}")
                except TclError as e:
                    results.append(f"Zoom failed: {e}")

            if screenshot:
                try:
                    from xcelium_mcp.screenshot import ps_to_png
                    ps_path = await bridge.screenshot()
                    cfg = None
                    try:
                        sim_dir = await resolve_sim_dir()
                        cfg = await load_sim_config(sim_dir)
                    except ValueError:
                        pass
                    png_bytes = await ps_to_png(ps_path, config=cfg)
                    results.append("Screenshot captured.")
                except Exception as e:
                    results.append(f"Screenshot failed: {e}")

            return "\n".join(results) if results else "No actions performed."

        elif action == "live_stop":
            sv = bridges.simvision
            try:
                await sv.execute("foreach id [after info] { after cancel $id }")
                return "Auto-reload stopped."
            except TclError as e:
                return f"ERROR: {e}"

        elif action == "live_start":
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

            shm_info = ""
            sim_time = ""
            try:
                shm_info = await xmsim.execute("database -show")
                sim_time = await xmsim.execute("where")
                results.append(f"xmsim at {sim_time.strip()}, SHM: {shm_info.strip()}")
            except (TclError, ConnectionError, TimeoutError) as e:
                results.append(f"xmsim info: {e}")

            sv_db = ""
            try:
                sv_db = await sv.execute("database find")
            except (TclError, ConnectionError, TimeoutError):
                pass

            if sv_db.strip():
                results.append(f"SimVision database already open: {sv_db.strip()}")
            elif shm_info.strip():
                live_shm = _parse_shm_path(shm_info)
                if not live_shm:
                    return (
                        f"ERROR: Could not parse SHM path from xmsim database list:\n{shm_info}\n"
                        "Open SHM manually: simvision_connect(action='open_db', shm_path='...')"
                    )
                try:
                    await sv.execute(f"database open {live_shm}")
                    results.append(f"SimVision opened: {live_shm}")
                except (TclError, ConnectionError, TimeoutError) as e:
                    results.append(f"SHM open failed: {e}")

            if signals:
                try:
                    add_result = await waveform_add_impl_fn(signals=signals)
                    results.append(add_result)
                except (ConnectionError, TimeoutError) as e:
                    results.append(f"Add signals failed: {e}")

            if not zoom_start or not zoom_end:
                cur_ns = _parse_time_ns(sim_time)
                zoom_start = f"{max(0, cur_ns - 1_000_000)}ns"
                zoom_end = f"{cur_ns}ns"
            try:
                await sv.execute(f"waveform xview limits {zoom_start} {zoom_end}")
                results.append(f"Zoomed to {zoom_start} – {zoom_end}")
            except (TclError, ConnectionError, TimeoutError):
                pass

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

        else:
            return f"ERROR: Unknown action '{action}'. Use 'setup', 'live_start', or 'live_stop'."

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
                    f"__WAVEFORM_ADD__ BEFORE {sig_str}",
                    timeout=30.0,
                )
            except Exception as e:
                return f"SimVision open but BEFORE group add failed: {e}"

            # 7. Add AFTER group (signals qualified with cmp_after database scope)
            after_signals = " ".join(f"cmp_after.{s}" for s in signals)
            try:
                await bridge.execute(
                    f"__WAVEFORM_ADD__ AFTER {after_signals}",
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
