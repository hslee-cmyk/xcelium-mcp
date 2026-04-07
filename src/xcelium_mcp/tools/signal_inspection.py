"""Signal inspection and manipulation tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclError


def register(mcp: FastMCP, bridges: BridgeManager) -> None:
    """Register signal inspection tools."""

    @mcp.tool()
    async def inspect_signal(
        action: str,
        signal: str = "",
        signals: list[str] | None = None,
        scope: str = "",
        pattern: str = "*",
        target: str = "auto",
    ) -> str:
        """Read signal values, describe metadata, list signals, or find drivers.

        Args:
            action:  "value" — read current values of one or more signals.
                     "describe" — detailed info (type, width, direction) for a signal.
                     "list" — list signals in a scope, filtered by pattern.
                     "drivers" — find all drivers of a signal (useful for X/Z debugging).
            signal:  Full hierarchical signal path. Required for describe/drivers. Also usable for value (single).
            signals: List of signal paths for "value" action.
            scope:   Hierarchical scope path (e.g. "top.hw.u_ext"). Required for "list".
            pattern: Glob pattern for "list" action (default "*").
            target:  "xmsim" | "simvision" | "auto" (default: auto). Used by "list".
        """
        if action == "value":
            if not signals:
                if signal:
                    signals = [signal]
                else:
                    return "ERROR: 'signals' or 'signal' is required for action='value'."
            bridge = bridges.xmsim
            results: list[str] = []
            for sig in signals:
                try:
                    val = await bridge.execute(f"value {sig}")
                    results.append(f"{sig} = {val}")
                except TclError as e:
                    results.append(f"{sig} = ERROR: {e}")
            return "\n".join(results)

        elif action == "describe":
            if not signal:
                return "ERROR: 'signal' is required for action='describe'."
            bridge = bridges.xmsim
            return await bridge.execute(f"describe {signal}")

        elif action == "list":
            if not scope:
                return "ERROR: 'scope' is required for action='list'."
            bridge = bridges.get_bridge(target)
            return await bridge.execute(f"describe {scope}.{pattern}")

        elif action == "drivers":
            if not signal:
                return "ERROR: 'signal' is required for action='drivers'."
            bridge = bridges.xmsim
            return await bridge.execute(f"drivers {signal}")

        else:
            return f"ERROR: Unknown action '{action}'. Use 'value', 'describe', 'list', or 'drivers'."

    @mcp.tool()
    async def deposit_signal(
        signal: str,
        value: str = "",
        release: bool = False,
    ) -> str:
        """Force-deposit a value onto a signal, or release to restore driven value.

        Args:
            signal:  Full hierarchical signal path.
            value:   Value to deposit (e.g. "1'b1", "8'hFF"). Required unless release=True.
            release: True = release the signal instead of depositing.
        """
        bridge = bridges.xmsim
        if release:
            readback = await bridge.execute(f"__RELEASE_AND_VERIFY__ {signal}")
            return f"Released {signal}. Current value: {readback}"
        else:
            if not value:
                return "ERROR: 'value' is required for deposit (or set release=True)."
            readback = await bridge.execute(f"__DEPOSIT_AND_VERIFY__ {signal} {value}")
            return f"Deposited {value} on {signal}. Readback: {readback}"
