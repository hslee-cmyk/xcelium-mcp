"""Signal inspection and manipulation tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclError


def register(mcp: FastMCP, bridges: BridgeManager) -> None:
    """Register signal inspection tools."""

    @mcp.tool()
    async def get_signal_value(signals: list[str]) -> str:
        """Read current values of one or more signals.

        Args:
            signals: List of signal paths (e.g. ["/tb/dut/clk", "/tb/dut/data[7:0]"]).
        """
        bridge = bridges.xmsim
        results: list[str] = []
        for sig in signals:
            try:
                val = await bridge.execute(f"value {sig}")
                results.append(f"{sig} = {val}")
            except TclError as e:
                results.append(f"{sig} = ERROR: {e}")
        return "\n".join(results)

    @mcp.tool()
    async def inspect_signal(
        action: str,
        signal: str = "",
        scope: str = "",
        pattern: str = "*",
        target: str = "auto",
    ) -> str:
        """Inspect signal metadata: describe, list, or find drivers.

        Args:
            action:  "describe" — detailed info (type, width, direction) for a signal.
                     "list" — list signals in a scope, filtered by pattern.
                     "drivers" — find all drivers of a signal (useful for X/Z debugging).
            signal:  Full hierarchical signal path. Required for "describe" and "drivers".
            scope:   Hierarchical scope path (e.g. "top.hw.u_ext"). Required for "list".
            pattern: Glob pattern for "list" action (default "*").
            target:  "xmsim" | "simvision" | "auto" (default: auto). Used by "list".
        """
        if action == "describe":
            if not signal:
                return "ERROR: 'signal' is required for action='describe'."
            bridge = bridges.xmsim
            result = await bridge.execute(f"describe {signal}")
            return result

        elif action == "list":
            if not scope:
                return "ERROR: 'scope' is required for action='list'."
            bridge = bridges.get_bridge(target)
            result = await bridge.execute(f"describe {scope}.{pattern}")
            return result

        elif action == "drivers":
            if not signal:
                return "ERROR: 'signal' is required for action='drivers'."
            bridge = bridges.xmsim
            result = await bridge.execute(f"drivers {signal}")
            return result

        else:
            return f"ERROR: Unknown action '{action}'. Use 'describe', 'list', or 'drivers'."

    @mcp.tool()
    async def deposit_value(signal: str, value: str, release: bool = False) -> str:
        """Force-deposit a value onto a signal, or release a previously deposited signal.

        Args:
            signal:  Full hierarchical signal path.
            value:   Value to deposit (e.g. "1'b1", "8'hFF", "0"). Ignored when release=True.
            release: True = release the signal (restore driven value) instead of depositing.
        """
        bridge = bridges.xmsim
        if release:
            readback = await bridge.execute(f"__RELEASE_AND_VERIFY__ {signal}")
            return f"Released {signal}. Current value: {readback}"
        else:
            readback = await bridge.execute(f"__DEPOSIT_AND_VERIFY__ {signal} {value}")
            return f"Deposited {value} on {signal}. Readback: {readback}"
