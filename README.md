# Xcelium MCP Server

MCP (Model Context Protocol) server that enables AI assistants to control Cadence Xcelium/SimVision in real time via a Tcl socket bridge. Supports automated RTL/gate-level debugging with watchpoints, binary search, checkpoints, and SHM probe control.

## Architecture

```
┌──────────────┐  stdio   ┌───────────────────┐  TCP    ┌─────────────────────┐
│ AI Assistant  │ <------> │ Python FastMCP     │ <-----> │ mcp_bridge.tcl      │
│ (Claude, etc) │          │ Server (25 tools)  │ :9876  │ inside xmsim/SV     │
└──────────────┘          └────────────────────┘        │ (13 meta commands)  │
                                                         └─────────────────────┘
```

## Installation

```bash
pip install -e .

# With screenshot support (requires ghostscript)
pip install -e ".[screenshot]"

# With dev dependencies
pip install -e ".[dev]"
```

## Setup

### 1. Simulator Side (Linux server)

Load the Tcl bridge when launching xmsim or SimVision:

```bash
# Batch mode (no GUI license needed)
xmsim -64bit -input mcp_bridge.tcl top

# SimVision GUI mode
simvision -64bit -input mcp_bridge.tcl dump.shm
```

The bridge listens on TCP port **9876** by default. Override with:
```bash
export MCP_BRIDGE_PORT=9877
```

Bridge signals readiness by creating `/tmp/mcp_bridge_ready_9876`.

### 2. AI Tool Configuration

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "xcelium-mcp": {
      "type": "stdio",
      "command": "ssh",
      "args": ["-o", "BatchMode=yes", "sim-server", "/path/to/xcelium-mcp"]
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "xcelium": {
      "command": "xcelium-mcp"
    }
  }
}
```

### 3. SSH Tunnel (remote server)

```bash
# Forward port 9876 in ~/.ssh/config
Host sim-server
    LocalForward 9876 localhost:9876
```

## Available Tools (25)

### Connection & Control (1-7)

| Tool | Description |
|------|-------------|
| `connect_simulator` | Connect to bridge (host, port, timeout) |
| `disconnect_simulator` | Disconnect (for reconnection only) |
| `sim_run` | Run simulation with duration and timeout (default 600s for gate sim) |
| `sim_stop` | Stop a running simulation |
| `sim_restart` | Restart from time 0 |
| `sim_status` | Get current time, scope, state |
| `set_breakpoint` | Set conditional breakpoint |

### Signal Inspection (8-13)

| Tool | Description |
|------|-------------|
| `get_signal_value` | Read current signal values |
| `describe_signal` | Get signal type, width, direction |
| `find_drivers` | Find all drivers (X/Z debugging) |
| `list_signals` | List signals in a scope |
| `deposit_value` | Force a value onto a signal |
| `release_signal` | Release a deposited signal |

### Waveform (14-16)

| Tool | Description |
|------|-------------|
| `waveform_add_signals` | Add signals to waveform viewer |
| `waveform_zoom` | Set waveform time range |
| `cursor_set` | Set waveform cursor position |

### Debug & Screenshot (17-18)

| Tool | Description |
|------|-------------|
| `take_waveform_screenshot` | Capture waveform as PNG |
| `run_debugger_mode` | Full debug snapshot with checklist |

### Advanced Debug (19-25)

| Tool | Description |
|------|-------------|
| `shutdown_simulator` | **Safe shutdown** preserving SHM waveform data |
| `watch_signal` | Set watchpoint to stop at exact clock edge when condition is true |
| `watch_clear` | Clear watchpoints (specific ID or all) |
| `probe_control` | Enable/disable SHM recording, optionally per scope |
| `save_checkpoint` | Save simulation state for later restoration |
| `restore_checkpoint` | Restore to a saved checkpoint |
| `bisect_signal` | Binary search to find when a condition first becomes true |

## Debugging Workflows

### Quick Bug Hunt (watchpoint)

```python
connect_simulator()
watch_signal(signal="top.dut.r_state", op="==", value="4'hF")
sim_run(duration="100us")          # stops at exact clock edge
get_signal_value(signals=["top.dut.r_state", "top.dut.r_data"])
watch_clear()
shutdown_simulator()               # always use this, never disconnect
```

### Automated Time Search (bisect)

```python
connect_simulator()
bisect_signal(
    signal="top.dut.r_error", op="==", value="1'b1",
    start_ns=0, end_ns=1000000,    # 0-1ms range
    precision_ns=100                # 100ns precision
)
# Returns iteration log + final narrowed time range
shutdown_simulator()
```

### Long Simulation with SHM Control

```python
connect_simulator()
probe_control(mode="disable")       # no SHM recording
sim_run(duration="50ms")            # skip uninteresting region
probe_control(mode="enable")        # start recording
sim_run(duration="10ms")            # capture region of interest
shutdown_simulator()
```

### Checkpoint & Replay

```python
connect_simulator()
sim_run(duration="10ms")
save_checkpoint(name="before_bug")
sim_run(duration="5ms")             # analyze bug region
restore_checkpoint(name="before_bug")  # go back
sim_run(duration="5ms")             # try different analysis
shutdown_simulator()
```

## Key Rules

1. **Always specify duration** in `sim_run` to prevent hang on infinite loops
2. **Always use `shutdown_simulator`** to end sessions (preserves SHM data)
3. **Never use `disconnect_simulator`** to end sessions (SHM not flushed)
4. **Gate-level sim**: increase timeout with `sim_run(timeout=1800)` if needed
5. **Bridge ready**: check `/tmp/mcp_bridge_ready_<port>` file instead of TCP ping

## Tcl Bridge Meta Commands

The bridge (`mcp_bridge.tcl`) accepts these meta commands over TCP:

| Command | Description |
|---------|-------------|
| `__PING__` | Health check |
| `__QUIT__` | Close connection |
| `__SCREENSHOT__ <path>` | Capture waveform to PostScript |
| `__SHUTDOWN__` | Safe shutdown (database close + finish) |
| `__RUN_ASYNC__ <dur>` | Non-blocking sim run |
| `__PROGRESS__` | Query sim time during async run |
| `__WATCH__ <sig> <op> <val>` | Set signal watchpoint |
| `__WATCH_LIST__` | List active watchpoints |
| `__WATCH_CLEAR__ <id\|all>` | Delete watchpoints |
| `__PROBE_CONTROL__ <mode> [scope]` | Toggle SHM recording |
| `__SAVE__ <name>` | Save checkpoint |
| `__RESTORE__ <name>` | Restore checkpoint |
| `__BISECT__ <sig> <op> <val> <start> <end> [precision]` | Binary search |

Any other input is evaluated as a raw Tcl/SimVision command.

## Testing

```bash
pytest tests/
```

## Requirements

- **Python** >= 3.10
- **mcp** >= 1.0.0
- **xmsim** or **SimVision** (Cadence Xcelium) with Tcl console
- **ghostscript** (optional, for EPS to PNG screenshot conversion)

## License

MIT
