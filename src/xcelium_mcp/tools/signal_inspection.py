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
        value: str = "",
        release: bool = False,
    ) -> str:
        """Unified signal tool: read values, describe, list, find drivers, deposit, or release.

        Args:
            action:  "value" — read current values of one or more signals.
                     "describe" — detailed info (type, width, direction) for a signal.
                     "list" — list signals in a scope, filtered by pattern.
                     "drivers" — find all drivers of a signal (useful for X/Z debugging).
                     "deposit" — force a value onto a signal.
                     "release" — release a previously deposited signal (restore driven value).
            signal:  Full hierarchical signal path. Required for describe/drivers/deposit/release.
            signals: List of signal paths for "value" action.
            scope:   Hierarchical scope path (e.g. "top.hw.u_ext"). Required for "list".
            pattern: Glob pattern for "list" action (default "*").
            target:  "xmsim" | "simvision" | "auto" (default: auto). Used by "list".
            value:   Value to deposit (e.g. "1'b1", "8'hFF"). Required for "deposit".
            release: Alias for action="release" when used with deposit — deprecated, use action="release".
        """
        # Backward compat: release=True with action="deposit" → treat as release
        if release and action == "deposit":
            action = "release"

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

        elif action == "deposit":
            if not signal or not value:
                return "ERROR: 'signal' and 'value' are required for action='deposit'."
            bridge = bridges.xmsim
            readback = await bridge.execute(f"__DEPOSIT_AND_VERIFY__ {signal} {value}")
            return f"Deposited {value} on {signal}. Readback: {readback}"

        elif action == "release":
            if not signal:
                return "ERROR: 'signal' is required for action='release'."
            bridge = bridges.xmsim
            readback = await bridge.execute(f"__RELEASE_AND_VERIFY__ {signal}")
            return f"Released {signal}. Current value: {readback}"

        else:
            return f"ERROR: Unknown action '{action}'. Use 'value', 'describe', 'list', 'drivers', 'deposit', or 'release'."
