"""Waveform viewing and control tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.screenshot import ps_to_png
from xcelium_mcp.tcl_bridge import TclBridge, TclError


async def _list_waveform_windows(bridge: TclBridge) -> str:
    """List available waveform windows."""
    try:
        r = await bridge.execute("waveform get -name")
        return r.strip() if r.strip() else "(none)"
    except TclError:
        return "(error)"


def register(mcp: FastMCP, bridges: BridgeManager) -> dict:
    @mcp.tool()
    async def waveform_add_signals(
        signals: list[str],
        group_name: str = "",
        window_name: str = "",
    ) -> str:
        """Add signals to SimVision waveform. Auto-creates window, skips duplicates.

        Args:
            signals:     Signal paths to add.
            group_name:  Group within window. Empty = no group.
            window_name: Target waveform window. Empty = current (or auto-create).
        """
        bridge = bridges.simvision

        # Switch to specific window if requested
        if window_name:
            try:
                await bridge.execute(f"waveform using {window_name}")
            except TclError:
                avail = await _list_waveform_windows(bridge)
                return f"ERROR: Window '{window_name}' not found. Available: {avail}"

        # Single round-trip: __WAVEFORM_ADD_GROUP__ handles window auto-create,
        # DB prefix resolution, dedup, and group creation — all in Tcl side.
        # Protocol: "__WAVEFORM_ADD_GROUP__ {group_name_or_empty} sig1 sig2 ..."
        # Empty group_name → "" placeholder so Tcl parser doesn't eat first signal.
        grp = group_name if group_name else '""'
        sig_str = " ".join(signals)
        result = await bridge.execute(
            f"__WAVEFORM_ADD_GROUP__ {grp} {sig_str}", timeout=30.0
        )
        return result

    @mcp.tool()
    async def waveform_zoom(start_time: str, end_time: str) -> str:
        """Set the waveform viewer time range (zoom to region).

        Args:
            start_time: Start time (e.g. "0ns").
            end_time: End time (e.g. "100ns").
        """
        bridge = bridges.simvision
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
        bridge = bridges.simvision
        result = await bridge.execute(
            f"cursor set -using {cursor_name} -time {time}"
        )
        return f"Cursor {cursor_name} set to {time}. {result}"

    @mcp.tool()
    async def take_waveform_screenshot() -> Image:
        """Capture a screenshot of the SimVision waveform window.

        Returns the screenshot as a PNG image that Claude can analyze.
        """
        bridge = bridges.simvision
        ps_path = await bridge.screenshot()
        png_bytes = await ps_to_png(ps_path)
        return Image(data=png_bytes, format="png")

    return {"waveform_add_signals": waveform_add_signals}
