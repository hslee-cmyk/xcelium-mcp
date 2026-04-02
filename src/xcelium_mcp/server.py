"""Xcelium MCP Server — FastMCP server with 49 tools for simulator control.

v4.2: Tools split into 7 modules under tools/. This file only creates
the MCP instance, BridgeManager, and registers all tool modules.
"""
from __future__ import annotations

import functools

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
import xcelium_mcp.csv_cache as csv_cache

# ---------------------------------------------------------------------------
# Server & bridge state
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "xcelium-mcp",
    instructions="MCP server for Cadence Xcelium/SimVision simulator control",
)

bridges = BridgeManager()

# ---------------------------------------------------------------------------
# Tool registration — 7 modules, 49 tools total
#
# Registration order matters: simvision needs references to tools from
# waveform, sim_lifecycle, and debug modules (cross-tool calls).
# ---------------------------------------------------------------------------
from xcelium_mcp.tools import (  # noqa: E402
    sim_lifecycle,
    signal_inspection,
    waveform,
    batch,
    checkpoint,
    simvision,
    debug,
)
from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl  # noqa: E402

# Phase 1: modules without cross-tool dependencies
signal_inspection.register(mcp, bridges)
checkpoint.register(mcp, bridges)

# Phase 2: modules that return tool references
lifecycle_tools = sim_lifecycle.register(mcp, bridges)
waveform_tools = waveform.register(mcp, bridges)
debug_tools = debug.register(mcp, bridges)

# Phase 3: batch needs restore_checkpoint
# functools.partial pre-fills `bridges` so batch.py can call fn(name, sim_dir)
batch.register(
    mcp, bridges,
    restore_checkpoint_fn=functools.partial(restore_checkpoint_impl, bridges),
)

# Phase 4: simvision needs cross-tool references
simvision.register(
    mcp,
    bridges,
    waveform_add_signals_fn=waveform_tools["waveform_add_signals"],
    connect_simulator_fn=lifecycle_tools["connect_simulator"],
    generate_debug_tcl_fn=debug_tools["generate_debug_tcl"],
    csv_cache=csv_cache,
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Run the MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
