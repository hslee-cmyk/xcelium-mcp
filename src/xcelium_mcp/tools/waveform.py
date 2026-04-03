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

        # Single round-trip: __WAVEFORM_ADD__ handles window auto-create,
        # DB prefix resolution, dedup, and group creation — all in Tcl side.
        # Protocol: "__WAVEFORM_ADD__ {group_name_or_empty} sig1 sig2 ..."
        # Empty group_name → "" placeholder so Tcl parser doesn't eat first signal.
        # Group names with spaces are wrapped in {} for Tcl list parsing.
        # Brace characters would break Tcl quoting — reject them.
        if group_name:
            if "{" in group_name or "}" in group_name:
                return "ERROR: Group name cannot contain { or } characters"
            grp = "{" + group_name + "}" if " " in group_name else group_name
        else:
            grp = '""'
        sig_str = " ".join(signals)
        result = await bridge.execute(
            f"__WAVEFORM_ADD__ {grp} {sig_str}", timeout=30.0
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
        # Load config for external tool paths (gs/convert)
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

    @mcp.tool()
    async def waveform_remove_signals(
        signals: list[str] | None = None,
        group_name: str = "",
    ) -> str:
        """Remove signals from the waveform. Mirrors waveform_add_signals.

        Modes:
          - group_name + signals: remove matching signals within that group only.
          - group_name only:      remove the entire group and its child signals.
          - signals only:         remove matching signals from all groups/ungrouped.

        Args:
            signals:    Signal names (or suffixes) to remove.
            group_name: Scope removal to this group. Empty = search all.
        """
        bridge = bridges.simvision

        if group_name:
            if "{" in group_name or "}" in group_name:
                return "ERROR: Group name cannot contain { or } characters"

        if not signals and not group_name:
            return "ERROR: Provide signals to remove, or group_name to remove a group."

        # Protocol: "__WAVEFORM_REMOVE__ {group_name_or_""} sig1 sig2 ..."
        # Matches __WAVEFORM_ADD__ protocol structure.
        if group_name:
            grp = "{" + group_name + "}" if " " in group_name else group_name
        else:
            grp = '""'
        sig_str = " ".join(signals) if signals else ""
        result = await bridge.execute(
            f"__WAVEFORM_REMOVE__ {grp} {sig_str}".strip(), timeout=30.0
        )
        return result

    @mcp.tool()
    async def waveform_clear() -> str:
        """Remove all signals and groups from the waveform window."""
        bridge = bridges.simvision
        await bridge.execute("waveform clearall")
        return "All signals and groups cleared."

    return {"waveform_add_signals": waveform_add_signals}
