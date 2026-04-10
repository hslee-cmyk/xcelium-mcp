"""SimVision GUI tools."""
from __future__ import annotations

import asyncio
import csv
import re
from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.batch_runner import resolve_test_name
from xcelium_mcp.bridge_manager import BridgeManager, scan_ready_files
from xcelium_mcp.discovery import resolve_sim_dir
from xcelium_mcp.env_detection import _detect_vnc_display
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.shell_utils import (
    _parse_shm_path,
    _parse_time_ns,
    build_redirect,
    get_user_tmp_dir,
    login_shell_cmd,
    ssh_run,
    validate_path,
)
from xcelium_mcp.shell_utils import (
    shell_quote as sq,
)
from xcelium_mcp.tcl_bridge import BRIDGE_ERRORS, TclBridge, TclError

# Type aliases for cross-tool callable references
WaveformAddImplFn = Callable[..., Coroutine[Any, Any, str]]
ConnectSimulatorFn = Callable[..., Coroutine[Any, Any, str]]

_DISPLAY_RE = re.compile(r'^:?[0-9]+(\.[0-9]+)?$')


# ------------------------------------------------------------------ #
# Module-level orchestration functions (extracted from register)      #
# ------------------------------------------------------------------ #

async def open_database(bridges: BridgeManager, shm_path: str, name: str = "") -> str:
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


async def start_simvision(
    bridges: BridgeManager, test_name: str, shm_path: str, display: str, sim_dir: str,
) -> str:
    """Start SimVision or connect to already running instance."""
    # 0. Disconnect existing (max 1 constraint)
    if bridges.simvision_raw and bridges.simvision_raw.connected:
        await bridges.simvision_raw.disconnect()
        bridges.set_simvision(None)

    # 1. Check existing SimVision bridge -> auto-connect
    for port, _btype in await scan_ready_files(target="simvision"):
        bridge = TclBridge(host="localhost", port=port)
        try:
            ping = await bridge.connect()
            bridges.set_simvision(bridge)
            return f"SimVision already running — connected to port {port} (ping={ping})"
        except BRIDGE_ERRORS:
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
            r2 = await ssh_run(f"(ls -td {dump_dir}/*{test_name}*.shm || true) | head -1")
            if not r2.strip():
                r2 = await ssh_run(f"(ls -td {dump_dir}/*.shm || true) | head -1")
        else:
            r2 = await ssh_run(f"(ls -td {dump_dir}/*.shm || true) | head -1")
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
        for port, _btype in await scan_ready_files(target="simvision"):
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
            except BRIDGE_ERRORS:
                continue

    log_tail = await ssh_run(f"tail -10 {log_file} || true")
    return f"ERROR: SimVision bridge not ready after 60s.\nLog:\n{log_tail}"


async def setup_waveform(
    bridges: BridgeManager,
    waveform_add_fn: WaveformAddImplFn,
    shm_path: str,
    signals: list[str],
    zoom_start: str,
    zoom_end: str,
    screenshot: bool,
) -> str:
    """Open SHM + add signals + zoom (one-shot configuration)."""
    bridge = bridges.simvision
    results = []

    if shm_path:
        db_result = await open_database(bridges, shm_path)
        results.append(db_result)

    if signals:
        add_result = await waveform_add_fn(signals=signals)
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
            await ps_to_png(ps_path, config=cfg)
            results.append("Screenshot captured.")
        except (TclError, ConnectionError, RuntimeError, OSError, TimeoutError) as e:
            results.append(f"Screenshot failed: {e}")

    return "\n".join(results) if results else "No actions performed."


async def live_start(
    bridges: BridgeManager,
    waveform_add_fn: WaveformAddImplFn,
    signals: list[str],
    zoom_start: str,
    zoom_end: str,
    auto_reload: bool,
) -> str:
    """Connect to running xmsim for live waveform viewing."""
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
            add_result = await waveform_add_fn(signals=signals)
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


async def reload_waveform(bridges: BridgeManager, shm_path: str) -> str:
    """Reload current or new SHM, preserving waveform context."""
    bridge = bridges.simvision
    results = []

    # 1. Capture current waveform state
    wv_state: dict = {}
    try:
        sigs_raw = await bridge.execute("waveform signals -format fullpath")
        wv_state["signals"] = sigs_raw.strip().split() if sigs_raw.strip() else []
    except (TclError, ConnectionError, TimeoutError):
        wv_state["signals"] = []
    try:
        zoom_raw = await bridge.execute("waveform xview limits")
        wv_state["zoom"] = zoom_raw.strip()
    except (TclError, ConnectionError, TimeoutError):
        wv_state["zoom"] = ""
    try:
        old_db_raw = await bridge.execute("database find")
        wv_state["old_db"] = old_db_raw.strip().split()[0] if old_db_raw.strip() else ""
    except (TclError, ConnectionError, TimeoutError):
        wv_state["old_db"] = ""

    # 2. Reload or replace DB
    new_db = wv_state["old_db"]
    if not shm_path:
        try:
            reload_cmd = f"database reload {wv_state['old_db']}" if wv_state["old_db"] else "database reload"
            await bridge.execute(reload_cmd)
            results.append("Database reloaded (same SHM)")
        except (TclError, ConnectionError, TimeoutError) as e:
            return f"ERROR: database reload failed: {e}"
    else:
        try:
            if wv_state["old_db"]:
                await bridge.execute(f"database close {wv_state['old_db']}")
            await bridge.execute(f"database open {shm_path}")
            new_db_raw = await bridge.execute("database find")
            new_db = new_db_raw.strip().split()[0] if new_db_raw.strip() else ""
            results.append(f"Database replaced: {wv_state['old_db']} -> {new_db}")
        except (TclError, ConnectionError, TimeoutError) as e:
            return f"ERROR: database replace failed: {e}"

    # 3. Restore waveform signals (only for new SHM -- same SHM keeps signals)
    saved_signals = wv_state.get("signals", [])
    if shm_path and saved_signals:
        old_prefix = f"{wv_state['old_db']}::" if wv_state["old_db"] else ""
        new_prefix = f"{new_db}::" if new_db else ""

        remapped = []
        for sig in saved_signals:
            if old_prefix and sig.startswith(old_prefix):
                remapped.append(f"{new_prefix}{sig[len(old_prefix):]}")
            else:
                remapped.append(sig)

        try:
            # Clear old signals then re-add with new db prefix
            await bridge.execute("waveform clearall")
            sig_str = " ".join(f"{{{s}}}" for s in remapped)
            await bridge.execute(f"waveform add -signals {sig_str}")
            results.append(f"Restored {len(remapped)} signal(s)")
        except (TclError, ConnectionError, TimeoutError) as e:
            results.append(f"Signal restore partial: {e}")

    # 4. Restore zoom
    if wv_state.get("zoom"):
        try:
            await bridge.execute(f"waveform xview limits {wv_state['zoom']}")
        except (TclError, ConnectionError, TimeoutError):
            pass

    return "\n".join(results) if results else "Reload complete (no state to restore)"


def _load_rows(path: str) -> dict[int, dict]:
    """Load CSV rows keyed by timestamp."""
    rows: dict[int, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw_time = row.get("SimTime") or row.get("time") or "0"
            rows[int(raw_time)] = row
    return rows


async def compare_csv_diff(
    csv_cache: Any,
    shm_before: str,
    shm_after: str,
    signals: list[str],
    start_ns: int,
    end_ns: int,
) -> str:
    """Compare two SHM waveform dumps via CSV extraction and diffing."""
    try:
        csv_b = await csv_cache.extract(shm_before, signals, start_ns, end_ns, missing_ok=True)
        csv_a = await csv_cache.extract(shm_after, signals, start_ns, end_ns, missing_ok=True)
    except RuntimeError as e:
        return f"ERROR extracting CSV: {e}"

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
        "=== Waveform Comparison ===",
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


async def compare_simvision(
    bridges: BridgeManager,
    connect_simulator_fn: ConnectSimulatorFn,
    shm_before: str,
    shm_after: str,
    signals: list[str],
    display: str,
) -> str:
    """Open both SHMs in SimVision for side-by-side view."""
    # 0. Validate display parameter
    if not _DISPLAY_RE.match(display):
        return (
            f"ERROR: Invalid display value {display!r}. "
            "Expected format like ':1' or ':1.0'."
        )

    # 1. VNC check
    vnc_check = await ssh_run(
        f"(vncserver -list || true) | grep '{display}' || echo NONE",
        timeout=10.0,
    )
    if "NONE" in vnc_check or display not in vnc_check:
        return (
            f"ERROR: VNC display {display} is not active.\n"
            "Start a VNC session first (e.g. vncserver :1), then retry.\n"
            "Fallback: use output_mode='csv_diff' for text-based comparison."
        )

    # 2. Launch SimVision with shm_before as primary database (detached)
    user_tmp = await get_user_tmp_dir()
    log_file = f"{user_tmp}/simvision_compare.log"
    await ssh_run(
        f"(nohup env DISPLAY={sq(display)} simvision {sq(shm_before)} "
        f"{build_redirect(log_file)} < /dev/null &)",
        timeout=5.0,
    )

    # 3. Wait for SimVision bridge ready file (30s)
    bridge_ready = False
    for _i in range(15):
        await asyncio.sleep(2)
        if await scan_ready_files(target="simvision"):
            bridge_ready = True
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
    except BRIDGE_ERRORS as e:
        return f"SimVision connected but failed to open shm_after: {e}"

    # 6. Add BEFORE group (signals from primary / default database)
    sig_str = " ".join(signals)
    try:
        await bridge.execute(
            f"__WAVEFORM_ADD__ BEFORE {sig_str}",
            timeout=30.0,
        )
    except BRIDGE_ERRORS as e:
        return f"SimVision open but BEFORE group add failed: {e}"

    # 7. Add AFTER group (signals qualified with cmp_after database scope)
    after_signals = " ".join(f"cmp_after.{s}" for s in signals)
    try:
        await bridge.execute(
            f"__WAVEFORM_ADD__ AFTER {after_signals}",
            timeout=30.0,
        )
    except BRIDGE_ERRORS as e:
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
# register() — thin MCP tool wrappers                                #
# ------------------------------------------------------------------ #

def register(
    mcp: FastMCP,
    bridges: BridgeManager,
    *,
    waveform_add_impl_fn: WaveformAddImplFn,
    connect_simulator_fn: ConnectSimulatorFn,
    csv_cache: Any,
) -> None:
    """Register SimVision GUI tools.

    Args:
        mcp: FastMCP server instance.
        bridges: BridgeManager for simulator bridge access.
        waveform_add_impl_fn: Reference to internal waveform add implementation.
        connect_simulator_fn: Reference to connect_simulator tool.
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
            return await start_simvision(bridges, test_name, shm_path, display, sim_dir)
        elif action == "attach":
            return await connect_simulator_fn(host="localhost", port=port, target="simvision", timeout=timeout)
        elif action == "open_db":
            return await open_database(bridges, shm_path, name)
        else:
            return f"ERROR: Unknown action '{action}'. Use 'start', 'attach', or 'open_db'."

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
        """SimVision waveform control: setup, live monitoring, reload.

        Args:
            action:      "setup" — open SHM + add signals + zoom (one-shot configuration).
                         "live_start" — connect to running xmsim for live waveform viewing.
                         "live_stop" — stop live waveform auto-reload.
                         "reload" — reload current or new SHM, preserving waveform context.
            shm_path:    (setup/live/reload) SHM database path.
                         reload: empty = reload same SHM, set = replace with new SHM.
            signals:     (setup/live) Signal paths to add to waveform.
            zoom_start:  Zoom start time. Empty = full range (setup) or auto-compute (live).
            zoom_end:    Zoom end time. Empty = full range (setup) or auto-compute (live).
            screenshot:  (setup) True = capture waveform screenshot after setup.
            auto_reload: (live_start) Enable auto-reload (default True).
        """
        if action == "setup":
            return await setup_waveform(bridges, waveform_add_impl_fn, shm_path, signals or [], zoom_start, zoom_end, screenshot)

        elif action == "live_stop":
            sv = bridges.simvision
            try:
                await sv.execute("foreach id [after info] { after cancel $id }")
                return "Auto-reload stopped."
            except TclError as e:
                return f"ERROR: {e}"

        elif action == "live_start":
            return await live_start(bridges, waveform_add_impl_fn, signals or [], zoom_start, zoom_end, auto_reload)

        elif action == "reload":
            return await reload_waveform(bridges, shm_path)

        else:
            return f"ERROR: Unknown action '{action}'. Use 'setup', 'live_start', 'live_stop', or 'reload'."

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

        # Validate SHM paths
        for label, path in [("shm_before", shm_before), ("shm_after", shm_after)]:
            err = validate_path(path, label)
            if err:
                return err

        if output_mode == "simvision":
            return await compare_simvision(bridges, connect_simulator_fn, shm_before, shm_after, signals, display)

        # csv_diff mode (default)
        start_ns = time_range_ns[0] if len(time_range_ns) >= 1 else 0
        end_ns = time_range_ns[1] if len(time_range_ns) >= 2 else 0
        return await compare_csv_diff(csv_cache, shm_before, shm_after, signals, start_ns, end_ns)
