"""Xcelium MCP Server — FastMCP server with 23 tools for simulator control.

v4.2: Tools split into 7 modules under tools/. This file only creates
the MCP instance, BridgeManager, and registers all tool modules.
v5.0: Tool consolidation — 51 tools → 26 tools via action-parameter dispatch.
"""
from __future__ import annotations

import functools

from mcp.server.fastmcp import FastMCP

import xcelium_mcp.csv_cache as csv_cache
from xcelium_mcp.bridge_manager import BridgeManager

# ---------------------------------------------------------------------------
# Server & bridge state
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "xcelium-mcp",
    instructions="MCP server for Cadence Xcelium/SimVision simulator control",
)

bridges = BridgeManager()

# ---------------------------------------------------------------------------
# Tool registration — 7 modules, 28 tools total
#
# Registration order matters: simvision needs references to tools from
# waveform, sim_lifecycle, and debug modules (cross-tool calls).
# ---------------------------------------------------------------------------
from xcelium_mcp.tools import (  # noqa: E402
    batch,
    checkpoint,
    debug,
    signal_inspection,
    sim_lifecycle,
    simvision,
    waveform,
)
from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl  # noqa: E402

# Phase 1: modules without cross-tool dependencies
signal_inspection.register(mcp, bridges)
checkpoint.register(mcp, bridges)

# Phase 2: modules that return tool references
lifecycle_tools = sim_lifecycle.register(mcp, bridges)
waveform_tools = waveform.register(mcp, bridges)
debug.register(mcp, bridges)

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
    waveform_add_impl_fn=waveform_tools["_waveform_add_impl"],
    connect_simulator_fn=lifecycle_tools["connect_simulator"],
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
