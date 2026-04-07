"""Waveform viewing and control tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.screenshot import ps_to_png
from xcelium_mcp.tcl_bridge import TclBridge, TclError


def _encode_group_arg(group_name: str) -> str:
    """Encode group_name for Tcl protocol. Empty → '""', spaces → {braced}."""
    if not group_name:
        return '""'
    return "{" + group_name + "}" if " " in group_name else group_name


def _validate_group_name(group_name: str) -> str | None:
    """Return error message if group_name contains invalid chars, else None."""
    if group_name and ("{" in group_name or "}" in group_name):
        return "ERROR: Group name cannot contain { or } characters"
    return None


async def _list_waveform_windows(bridge: TclBridge) -> str:
    """List available waveform windows."""
    try:
        r = await bridge.execute("waveform get -name")
        return r.strip() if r.strip() else "(none)"
    except TclError:
        return "(error)"


async def _waveform_add_impl(
    bridges: BridgeManager,
    signals: list[str],
    group_name: str = "",
    window_name: str = "",
) -> str:
    """Internal add implementation — callable from simvision.py via dict reference."""
    bridge = bridges.simvision

    err = _validate_group_name(group_name)
    if err:
        return err

    if window_name:
        try:
            await bridge.execute(f"waveform using {window_name}")
        except TclError:
            avail = await _list_waveform_windows(bridge)
            return f"ERROR: Window '{window_name}' not found. Available: {avail}"

    grp = _encode_group_arg(group_name)
    sig_str = " ".join(signals)
    result = await bridge.execute(
        f"__WAVEFORM_ADD__ {grp} {sig_str}", timeout=30.0
    )
    return result


def register(mcp: FastMCP, bridges: BridgeManager) -> dict:
    @mcp.tool()
    async def waveform(
        action: str,
        signals: list[str] | None = None,
        group_name: str = "",
        window_name: str = "",
    ) -> str:
        """Manage waveform signals: add, remove, or clear.

        Args:
            action:      "add" — add signals to waveform. Auto-creates window, skips duplicates.
                         "remove" — remove signals or a group from waveform.
                         "clear" — remove all signals and groups from waveform.
            signals:     Signal paths (required for add; optional for remove).
            group_name:  Group within window. Empty = no group.
                         For remove: scope removal to this group. Empty = search all.
            window_name: Target waveform window (add only). Empty = current (or auto-create).
        """
        if action == "add":
            if not signals:
                return "ERROR: 'signals' is required for action='add'."
            return await _waveform_add_impl(bridges, signals, group_name, window_name)

        elif action == "remove":
            bridge = bridges.simvision
            err = _validate_group_name(group_name)
            if err:
                return err
            if not signals and not group_name:
                return "ERROR: Provide signals to remove, or group_name to remove a group."
            grp = _encode_group_arg(group_name)
            sig_str = " ".join(signals) if signals else ""
            result = await bridge.execute(
                f"__WAVEFORM_REMOVE__ {grp} {sig_str}".strip(), timeout=30.0
            )
            return result

        elif action == "clear":
            bridge = bridges.simvision
            try:
                await bridge.execute("waveform clearall")
            except TclError:
                return "ERROR: No waveform window open or clearall failed."
            return "All signals and groups cleared."

        else:
            return f"ERROR: Unknown action '{action}'. Use 'add', 'remove', or 'clear'."

    @mcp.tool()
    async def waveform_navigate(
        action: str,
        start_time: str = "",
        end_time: str = "",
        time: str = "",
        cursor_name: str = "TimeA",
    ) -> str:
        """Navigate the waveform viewer: zoom or set cursor.

        Args:
            action:      "zoom" — set waveform time range.
                         "cursor" — set a cursor to a specific time.
            start_time:  Start time for zoom (e.g. "0ns").
            end_time:    End time for zoom (e.g. "100ns").
            time:        Simulation time for cursor (e.g. "50ns").
            cursor_name: Cursor name for cursor action (default "TimeA").
        """
        bridge = bridges.simvision

        if action == "zoom":
            if not start_time or not end_time:
                return "ERROR: 'start_time' and 'end_time' are required for action='zoom'."
            result = await bridge.execute(
                f"waveform xview limits {start_time} {end_time}"
            )
            return f"Waveform zoomed to {start_time} – {end_time}. {result}"

        elif action == "cursor":
            if not time:
                return "ERROR: 'time' is required for action='cursor'."
            result = await bridge.execute(
                f"cursor set -using {cursor_name} -time {time}"
            )
            return f"Cursor {cursor_name} set to {time}. {result}"

        else:
            return f"ERROR: Unknown action '{action}'. Use 'zoom' or 'cursor'."

    @mcp.tool()
    async def take_waveform_screenshot() -> Image:
        """Capture a screenshot of the SimVision waveform window.

        Returns the screenshot as a PNG image that Claude can analyze.
        """
        from xcelium_mcp.sim_runner import get_default_sim_dir
        from xcelium_mcp.registry import load_sim_config
        cfg = None
        sim_dir = await get_default_sim_dir()
        if sim_dir:
            cfg = await load_sim_config(sim_dir)

        bridge = bridges.simvision
        ps_path = await bridge.screenshot()
        png_bytes = await ps_to_png(ps_path, config=cfg)
        return Image(data=png_bytes, format="png")

    # Return dict: waveform_add key points to the unified waveform tool,
    # and _waveform_add_impl is exposed for direct internal calls from simvision.py
    return {
        "waveform_add": waveform,
        "_waveform_add_impl": lambda **kwargs: _waveform_add_impl(bridges, **kwargs),
    }
