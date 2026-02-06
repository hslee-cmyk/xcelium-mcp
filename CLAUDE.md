# CLAUDE.md

## Project Overview

MCP (Model Context Protocol) server that enables AI assistants to control Cadence Xcelium/SimVision simulator in real time. A Tcl socket bridge (`mcp_bridge.tcl`) runs inside SimVision, and a Python FastMCP server communicates with it over TCP to expose 18 tools.

## Architecture

```
Claude (stdio) ←→ Python FastMCP Server ←→ (TCP) ←→ mcp_bridge.tcl (SimVision)
```

- **Transport:** stdio (Claude ↔ Python), TCP socket (Python ↔ SimVision)
- **Tcl bridge** is single-threaded — Python side serializes commands with `asyncio.Lock`
- **Screenshot pipeline:** SimVision `hardcopyPrint` → PostScript → ghostscript/ImageMagick → PNG

## Repository Structure

```
src/xcelium_mcp/
├── __init__.py        # Package version
├── server.py          # FastMCP server, 18 tool definitions, entry point
├── tcl_bridge.py      # TclBridge async TCP client
└── screenshot.py      # PostScript → PNG conversion
tcl/
└── mcp_bridge.tcl     # SimVision-side Tcl socket server
tests/
└── test_bridge.py     # MockTclServer-based unit tests
```

## Build & Install

```bash
pip install -e .              # editable install
pip install -e ".[dev]"       # + pytest, pytest-asyncio
pip install -e ".[screenshot]" # + Pillow
```

Entry point: `xcelium-mcp` → `xcelium_mcp.server:main`

## Testing

```bash
pytest tests/ -v
```

Tests use `MockTclServer` (asyncio TCP server) — no SimVision required. All tests must pass before committing.

## Key Dependencies

- `mcp>=1.0.0` (FastMCP framework)
- Python >= 3.10
- Optional: `Pillow`, ghostscript or ImageMagick (screenshot support)

## Tool Groups (18 total)

| Group | Tools | Module |
|-------|-------|--------|
| Connection (1–2) | `connect_simulator`, `disconnect_simulator` | `server.py` |
| Sim Control (3–7) | `sim_run`, `sim_stop`, `sim_restart`, `sim_status`, `set_breakpoint` | `server.py` |
| Signal (8–13) | `get_signal_value`, `describe_signal`, `find_drivers`, `list_signals`, `deposit_value`, `release_signal` | `server.py` |
| Waveform (14–16) | `waveform_add_signals`, `waveform_zoom`, `cursor_set` | `server.py` |
| Debug (17–18) | `take_waveform_screenshot`, `run_debugger_mode` | `server.py` |

## Tcl Bridge Protocol

```
Request:  "<command>\n"
Response: "OK <len>\n<body>\n<<<END>>>\n"       (success)
          "ERROR <len>\n<body>\n<<<END>>>\n"     (failure)
```

Meta commands: `__PING__`, `__SCREENSHOT__ <path>`, `__QUIT__`

Commands are evaluated via `uplevel #0` in SimVision's global Tcl namespace.

## Coding Conventions

- All tool functions are `async` and decorated with `@mcp.tool()`
- Tools that need SimVision call `_get_bridge()` which raises `ConnectionError` if disconnected
- `TclBridge.execute()` raises `TclError` on Tcl-side errors; `execute_safe()` returns `TclResponse`
- `Image(data=<raw bytes>, format="png")` — FastMCP handles base64 internally
- Single global `_bridge` instance managed by `connect_simulator`/`disconnect_simulator`

## Deployment

**SimVision side (Linux):**
```bash
xrun -gui -input "@simvision {source mcp_bridge.tcl}" design.v
```

**Remote access:** `ssh -L 9876:localhost:9876 user@sim-server`

**Claude Desktop/Code config:**
```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp"
    }
  }
}
```
