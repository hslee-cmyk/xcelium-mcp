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
        results = []

        # 1. Window: specified → switch, unspecified → current or auto-create
        if window_name:
            try:
                await bridge.execute(f"waveform using {window_name}")
            except TclError:
                avail = await _list_waveform_windows(bridge)
                return f"ERROR: Window '{window_name}' not found. Available: {avail}"
        else:
            try:
                current = await bridge.execute("waveform using")
                if not current.strip():
                    raise TclError("empty")
            except TclError:
                wname = await bridge.execute("waveform new")
                results.append(f"Waveform window created: {wname}")

        # 2. Resolve database prefix for SimVision (db_name::signal_path)
        db_prefix = ""
        try:
            db_name = await bridge.execute("database find")
            if db_name.strip():
                db_prefix = db_name.strip() + "::"
        except (TclError, ConnectionError, TimeoutError):
            pass

        # 3. Dedup: query existing signals
        try:
            existing = await bridge.execute("waveform signals -format fullpath")
            existing_set = set(existing.strip().split())
        except (TclError, ConnectionError, TimeoutError):
            existing_set = set()

        # Normalize: add db_prefix to input signals if not present
        resolved_signals = []
        for s in signals:
            full = s if "::" in s else f"{db_prefix}{s}"
            if full not in existing_set:
                resolved_signals.append(full)
        skipped = len(signals) - len(resolved_signals)

        if not resolved_signals:
            return f"All {len(signals)} signal(s) already in waveform (skipped)."

        # 4. Add signals
        sig_str = " ".join(resolved_signals)
        if group_name:
            try:
                await bridge.execute(f"waveform add -groups {{{group_name}}}")
            except (TclError, ConnectionError, TimeoutError):
                pass
            result = await bridge.execute(f"waveform add -using {group_name} -signals {{{sig_str}}}")
        else:
            result = await bridge.execute(f"waveform add -signals {{{sig_str}}}")

        results.append(f"Added {len(resolved_signals)}, skipped {skipped} (duplicate). {result}")
        return "\n".join(results)

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
