# Xcelium MCP Server

MCP (Model Context Protocol) server that enables AI assistants (Claude) to control Cadence Xcelium/SimVision in real time via a Tcl socket bridge.

## Architecture

```
┌──────────────┐  stdio   ┌───────────────────┐  TCP    ┌─────────────────────┐
│ Claude       │ <------> │ Python FastMCP     │ <-----> │ mcp_bridge.tcl      │
│ Desktop/Code │          │ Server             │ socket  │ inside SimVision    │
└──────────────┘          │ (xcelium_mcp)      │        │ (uplevel #0 eval)   │
                          └───────────────────-┘        └─────────────────────┘
```

## Installation

```bash
# From the xcelium-mcp directory
pip install -e .

# With screenshot support (requires ghostscript or ImageMagick)
pip install -e ".[screenshot]"

# With dev dependencies
pip install -e ".[dev]"
```

## Setup

### 1. SimVision Side (Linux simulation server)

Load the Tcl bridge when launching SimVision:

```bash
# Option A: via xrun -input
xrun -gui -input "@simvision {source /path/to/mcp_bridge.tcl}" design.v

# Option B: source in SimVision console
simvision% source /path/to/mcp_bridge.tcl
```

The bridge listens on TCP port **9876** by default. Override with:

```bash
export MCP_BRIDGE_PORT=9877
```

Verify the bridge is running:

```bash
echo "__PING__" | nc localhost 9876
# Expected: OK 4\npong\n<<<END>>>
```

### 2. Claude Desktop Configuration

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp",
      "env": {
        "XCELIUM_MCP_HOST": "localhost",
        "XCELIUM_MCP_PORT": "9876"
      }
    }
  }
}
```

### 3. Claude Code Configuration

Add to `.claude.json`:

```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp"
    }
  }
}
```

## Remote Server (SSH Tunnel)

If SimVision runs on a remote Linux server:

```bash
# On your local machine — forward port 9876
ssh -L 9876:localhost:9876 user@sim-server
```

Then configure Claude to connect to `localhost:9876` as usual.

## Available Tools (18)

### Connection
| Tool | Description |
|------|-------------|
| `connect_simulator` | Connect to SimVision bridge (host, port, timeout) |
| `disconnect_simulator` | Disconnect from bridge |

### Simulation Control
| Tool | Description |
|------|-------------|
| `sim_run` | Run simulation (optional duration like "100ns") |
| `sim_stop` | Stop a running simulation |
| `sim_restart` | Restart simulation from time 0 |
| `sim_status` | Get current time, scope, state |
| `set_breakpoint` | Set conditional breakpoint |

### Signal Inspection
| Tool | Description |
|------|-------------|
| `get_signal_value` | Read signal values |
| `describe_signal` | Get signal type, width, direction |
| `find_drivers` | Find all drivers of a signal |
| `list_signals` | List signals in a scope |
| `deposit_value` | Force a value onto a signal |
| `release_signal` | Release a deposited signal |

### Waveform Control
| Tool | Description |
|------|-------------|
| `waveform_add_signals` | Add signals to waveform viewer |
| `waveform_zoom` | Set waveform time range |
| `cursor_set` | Set waveform cursor position |

### Debug & Screenshot
| Tool | Description |
|------|-------------|
| `take_waveform_screenshot` | Capture waveform as PNG image |
| `run_debugger_mode` | Full debug snapshot: state + signals + screenshot + checklist |

## Usage Example

Once connected, you can ask Claude:

> "Connect to the simulator and show me the current state"

> "Run the simulation for 100ns and check the value of /tb/dut/state"

> "Take a waveform screenshot and tell me if the clock is toggling"

> "Run debugger mode — I think there's a stuck FSM"

## Testing

```bash
pytest tests/
```

## Requirements

- **Python** >= 3.10
- **mcp** >= 1.0.0
- **SimVision** (Cadence Xcelium) with Tcl console
- **ghostscript** or **ImageMagick** (optional, for screenshot PNG conversion)

## License

MIT
