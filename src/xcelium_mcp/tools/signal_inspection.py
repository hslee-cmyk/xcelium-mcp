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
    async def describe_signal(signal: str) -> str:
        """Get detailed information about a signal (type, width, direction).

        Args:
            signal: Full hierarchical signal path.
        """
        bridge = bridges.xmsim
        result = await bridge.execute(f"describe {signal}")
        return result

    @mcp.tool()
    async def find_drivers(signal: str) -> str:
        """Find all drivers of a signal (useful for X/Z debugging).

        Args:
            signal: Full hierarchical signal path.
        """
        bridge = bridges.xmsim
        result = await bridge.execute(f"drivers {signal}")
        return result

    @mcp.tool()
    async def list_signals(scope: str, pattern: str = "*", target: str = "auto") -> str:
        """List signals in a scope, optionally filtered by pattern.

        Args:
            scope:   Hierarchical scope path (e.g. "top.hw.u_ext").
            pattern: Glob pattern to filter signals (default "*").
            target:  "xmsim" | "simvision" | "auto" (default: auto).
        """
        bridge = bridges.get_bridge(target)

        # Use 'describe' with hierarchical path + pattern
        # 'scope -describe' does NOT accept pattern args (causes SCMULT error)
        result = await bridge.execute(f"describe {scope}.{pattern}")
        return result

    @mcp.tool()
    async def deposit_value(signal: str, value: str) -> str:
        """Force-deposit a value onto a signal.

        Args:
            signal: Full hierarchical signal path.
            value: Value to deposit (e.g. "1'b1", "8'hFF", "0").
        """
        bridge = bridges.xmsim
        await bridge.execute(f"deposit {signal} {value}")
        # Verify
        readback = await bridge.execute(f"value {signal}")
        return f"Deposited {value} on {signal}. Readback: {readback}"

    @mcp.tool()
    async def release_signal(signal: str) -> str:
        """Release a previously deposited signal, restoring driven value.

        Args:
            signal: Full hierarchical signal path.
        """
        bridge = bridges.xmsim
        await bridge.execute(f"release {signal}")
        readback = await bridge.execute(f"value {signal}")
        return f"Released {signal}. Current value: {readback}"
