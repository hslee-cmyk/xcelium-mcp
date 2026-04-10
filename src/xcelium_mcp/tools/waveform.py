"""Waveform viewing and control tools."""
from __future__ import annotations

import functools
import re

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.screenshot import ps_to_png
from xcelium_mcp.shell_utils import sanitize_signal_name
from xcelium_mcp.tcl_bridge import TclBridge, TclError

_TIME_RE = re.compile(r'^\d+(\.\d+)?\s*(ns|us|ms|s|ps|fs)?$', re.IGNORECASE)
_CURSOR_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')


def _encode_group_arg(group_name: str) -> str:
    """Validate and encode group_name for Tcl protocol.

    Empty → '""', spaces → {braced}. Raises ValueError for invalid chars.
    """
    if not group_name:
        return '""'
    if "{" in group_name or "}" in group_name:
        raise ValueError("Group name cannot contain { or } characters")
    return "{" + group_name + "}" if " " in group_name else group_name


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

    try:
        grp = _encode_group_arg(group_name)
    except ValueError as e:
        return f"ERROR: {e}"

    try:
        signals = [sanitize_signal_name(s) for s in signals]
    except ValueError as e:
        return f"ERROR: {e}"

    if window_name:
        if any(c in window_name for c in '$;['):
            return f"ERROR: window_name contains forbidden Tcl metachar: {window_name!r}"
        try:
            await bridge.execute(f"waveform using {window_name}")
        except TclError:
            avail = await _list_waveform_windows(bridge)
            return f"ERROR: Window '{window_name}' not found. Available: {avail}"
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
        # zoom params
        start_time: str = "",
        end_time: str = "",
        # cursor params
        time: str = "",
        cursor_name: str = "TimeA",
    ) -> str:
        """Manage waveform: add/remove/clear signals, zoom, or set cursor.

        Args:
            action:      "add" — add signals to waveform (auto-creates window, dedup).
                         "remove" — remove signals or a group from waveform.
                         "clear" — remove all signals and groups.
                         "zoom" — set waveform time range.
                         "cursor" — set a cursor to a specific time.
            signals:     Signal paths (required for add; optional for remove).
            group_name:  Group within window (add/remove).
            window_name: Target waveform window (add only).
            start_time:  Start time for zoom (e.g. "0ns").
            end_time:    End time for zoom (e.g. "100ns").
            time:        Simulation time for cursor (e.g. "50ns").
            cursor_name: Cursor name (default "TimeA").
        """
        if action == "add":
            if not signals:
                return "ERROR: 'signals' is required for action='add'."
            return await _waveform_add_impl(bridges, signals, group_name, window_name)

        elif action == "remove":
            bridge = bridges.simvision
            if not signals and not group_name:
                return "ERROR: Provide signals to remove, or group_name to remove a group."
            if signals:
                try:
                    signals = [sanitize_signal_name(s) for s in signals]
                except ValueError as e:
                    return f"ERROR: {e}"
            try:
                grp = _encode_group_arg(group_name)
            except ValueError as e:
                return f"ERROR: {e}"
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

        elif action == "zoom":
            if not start_time or not end_time:
                return "ERROR: 'start_time' and 'end_time' are required for action='zoom'."
            if not _TIME_RE.fullmatch(start_time.strip()):
                return f"ERROR: Invalid start_time {start_time!r}. Expected format like '100ns', '0', '50.5us'."
            if not _TIME_RE.fullmatch(end_time.strip()):
                return f"ERROR: Invalid end_time {end_time!r}. Expected format like '100ns', '0', '50.5us'."
            bridge = bridges.simvision
            result = await bridge.execute(
                f"waveform xview limits {start_time} {end_time}"
            )
            return f"Waveform zoomed to {start_time} – {end_time}. {result}"

        elif action == "cursor":
            if not time:
                return "ERROR: 'time' is required for action='cursor'."
            if not _TIME_RE.fullmatch(time.strip()):
                return f"ERROR: Invalid time {time!r}. Expected format like '50ns', '100us'."
            if not _CURSOR_NAME_RE.fullmatch(cursor_name):
                return f"ERROR: Invalid cursor_name {cursor_name!r}. Only alphanumeric and underscore allowed."
            bridge = bridges.simvision
            result = await bridge.execute(
                f"cursor set -using {cursor_name} -time {time}"
            )
            return f"Cursor {cursor_name} set to {time}. {result}"

        else:
            return f"ERROR: Unknown action '{action}'. Use 'add', 'remove', 'clear', 'zoom', or 'cursor'."

    @mcp.tool()
    async def waveform_screenshot() -> Image:
        """Capture a screenshot of the SimVision waveform window.

        Returns the screenshot as a PNG image that Claude can analyze.
        """
        from xcelium_mcp.discovery import resolve_sim_dir
        from xcelium_mcp.registry import load_sim_config
        cfg = None
        try:
            sim_dir = await resolve_sim_dir()
            cfg = await load_sim_config(sim_dir)
        except ValueError:
            pass

        bridge = bridges.simvision
        ps_path = await bridge.screenshot()
        png_bytes = await ps_to_png(ps_path, config=cfg)
        return Image(data=png_bytes, format="png")

    return {
        "_waveform_add_impl": functools.partial(_waveform_add_impl, bridges),
    }
