# CLAUDE.md

## Project Overview

MCP (Model Context Protocol) server that enables AI assistants to control Cadence Xcelium/SimVision simulator in real time. A Tcl socket bridge (`mcp_bridge.tcl`) runs inside SimVision, and a Python FastMCP server communicates with it over TCP to expose 24 tools + 13 meta commands.

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
├── server.py          # FastMCP server entry point, tool module registration
├── tools/              # 24 MCP tool definitions (7 modules, action-param consolidated)
│   ├── sim_lifecycle.py    # 10 tools — discover/connect/run/status/restart/etc.
│   ├── batch.py             # 2 tools — sim_batch_run, sim_regression
│   ├── signal_inspection.py # 2 tools — inspect_signal, deposit_signal
│   ├── debug.py              # 4 tools — bisect_signal, watch, probe, debug_snapshot
│   ├── checkpoint.py         # 1 tool  — checkpoint
│   ├── waveform.py           # 2 tools — waveform, waveform_screenshot
│   └── simvision.py          # 3 tools — simvision_connect, simvision, compare_waveforms
├── tcl_bridge.py       # TclBridge async TCP client
├── bridge_manager.py   # Multi-bridge connection management
├── batch_runner.py     # Batch simulation execution (run_batch_single/regression)
├── csv_cache.py        # SHM→CSV extraction + in-memory bisect (extract/bisect_signal_dump)
├── runner_detection.py # TB/runner auto-detection
├── checkpoint_manager.py, registry.py, screenshot.py, etc.
tcl/
└── mcp_bridge.tcl     # SimVision-side Tcl socket server
tests/                 # 472 tests (pytest, MockTclServer-based, no SimVision required)
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

## Tool Usage

24개 tool의 phase별 사용법·파라미터·결정 매트릭스는 `~/.claude/skills/xcelium-sim/references/tool-map.md`(user-level skill, 정본)를 참조 — 이 파일에 별도로 tool 목록을 유지하지 않는다(중복 방지). 소스 자체는 `src/xcelium_mcp/tools/*.py`(위 Repository Structure).

## Tcl Bridge Protocol

```
Request:  "<command>\n"
Response: "OK <len>\n<body>\n<<<END>>>\n"       (success)
          "ERROR <len>\n<body>\n<<<END>>>\n"     (failure)
```

Meta commands (13): `__PING__`, `__SCREENSHOT__`, `__QUIT__`, `__SHUTDOWN__`, `__WATCH__`, `__WATCH_CLEAR__`, `__PROBE_CONTROL__`, `__SAVE__`, `__RESTORE__`, `__BISECT__`, `__CURSOR__`, `__ZOOM__`, `__LIST_SIGNALS__`

Regular commands are evaluated via `uplevel #0` in SimVision's global Tcl namespace.

## 파일/폴더 작업 규칙

- 파일명/폴더명이 언급되면 작업 전 반드시 `ls` 또는 `Glob`으로 존재 확인
- `git mv`, `mv`, `rm` 등 비가역 작업은 대상을 확인한 후 실행

## Coding Conventions

- All tool functions are `async` and decorated with `@mcp.tool()`
- Tools that need SimVision call `_get_bridge()` which raises `ConnectionError` if disconnected
- `TclBridge.execute()` raises `TclError` on Tcl-side errors; `execute_safe()` returns `TclResponse`
- `Image(data=<raw bytes>, format="png")` — FastMCP handles base64 internally
- Single global `_bridge` instance managed by `connect_simulator`/`disconnect_simulator`

## Debugging Workflow

RTL 시뮬레이션 디버깅(6-phase: 인프라 분석→사전 분석→실행→1차 판별→waveform 분석→수정)은
`~/.claude/skills/xcelium-sim/`(user-level skill, 정본)가 안내한다 — "FAIL 분석", "waveform",
"시뮬레이션" 등 키워드 등장 시 자동 로드됨. 세부 tool 사용법은 skill의
`references/phase-0~5.md` + `tool-map.md` 참조.

원본 방법론(caching 규칙, 실전 히스토리): `docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md`
후속(범용 `/sim` subcommand, Draft, compound.py 대기): `docs/01-plan/features/xcelium-mcp-debug-workflow-v2.plan.md`

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
