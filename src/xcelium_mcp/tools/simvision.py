"""SimVision GUI tools."""
from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.shell_utils import validate_path
from xcelium_mcp.simvision_ops import (
    compare_csv_diff,
    compare_simvision,
    live_start,
    open_database,
    reload_waveform,
    setup_waveform,
    start_simvision,
)
from xcelium_mcp.tcl_bridge import TclError

# Type aliases for cross-tool callable references
WaveformAddImplFn = Callable[..., Coroutine[Any, Any, str]]
ConnectSimulatorFn = Callable[..., Coroutine[Any, Any, str]]

_DISPLAY_RE = re.compile(r'^:?[0-9]+(\.[0-9]+)?$')


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
