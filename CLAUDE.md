# CLAUDE.md

## Project Overview

MCP (Model Context Protocol) server that enables AI assistants to control Cadence Xcelium/SimVision simulator in real time. A Tcl socket bridge (`mcp_bridge.tcl`) runs inside SimVision, and a Python FastMCP server communicates with it over TCP to expose 25 tools + 13 meta commands.

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

## Tool Groups (25 tools)

| Group | Tools | Module |
|-------|-------|--------|
| Connection (1–2) | `connect_simulator`, `disconnect_simulator` | `server.py` |
| Sim Control (3–8) | `sim_run`, `sim_stop`, `sim_restart`, `sim_status`, `set_breakpoint`, `shutdown_simulator` | `server.py` |
| Signal (9–14) | `get_signal_value`, `describe_signal`, `find_drivers`, `list_signals`, `deposit_value`, `release_signal` | `server.py` |
| Waveform (15–17) | `waveform_add_signals`, `waveform_zoom`, `cursor_set` | `server.py` |
| Debug (18–20) | `take_waveform_screenshot`, `run_debugger_mode`, `probe_control` | `server.py` |
| Watch (21–22) | `watch_signal`, `watch_clear` | `server.py` |
| Checkpoint (23–24) | `save_checkpoint`, `restore_checkpoint` | `server.py` |
| Bisect (25) | `bisect_signal` | `server.py` |

## Tcl Bridge Protocol

```
Request:  "<command>\n"
Response: "OK <len>\n<body>\n<<<END>>>\n"       (success)
          "ERROR <len>\n<body>\n<<<END>>>\n"     (failure)
```

Meta commands (13): `__PING__`, `__SCREENSHOT__`, `__QUIT__`, `__SHUTDOWN__`, `__WATCH__`, `__WATCH_CLEAR__`, `__PROBE_CONTROL__`, `__SAVE__`, `__RESTORE__`, `__BISECT__`, `__CURSOR__`, `__ZOOM__`, `__LIST_SIGNALS__`

Regular commands are evaluated via `uplevel #0` in SimVision's global Tcl namespace.

## Coding Conventions

- All tool functions are `async` and decorated with `@mcp.tool()`
- Tools that need SimVision call `_get_bridge()` which raises `ConnectionError` if disconnected
- `TclBridge.execute()` raises `TclError` on Tcl-side errors; `execute_safe()` returns `TclResponse`
- `Image(data=<raw bytes>, format="png")` — FastMCP handles base64 internally
- Single global `_bridge` instance managed by `connect_simulator`/`disconnect_simulator`

## Debugging Workflow

표준 디버깅 워크플로우 (ncsim legacy / UVM / Directed SV 모든 환경 대응):

```
Phase 0: 검증 환경 인프라 분석 (1회성 캐시)
    공유 컴포넌트 (Agent/BFM/inc task) + 테스트케이스 → .ai/analysis/tb_*.analysis.md

Phase 1: 사전 분석 — 캐시 참조 + RTL 분석서 + dump scope 확인
Phase 2: 시뮬레이션 — Batch (권장) or Bridge (interactive)
Phase 3: 1차 판별 — 로그 (PASS/FAIL/Errors/UVM_ERROR)
Phase 4: 2차 판별 — Waveform CSV (simvisdbutil) + FSM 전이 대조
Phase 5: 수정 + Regression + 문서 갱신
```

**원칙**:
- 시뮬레이션 전에 판별 신호를 정한다 (Phase 0/1)
- Batch mode + CSV가 기본 (save/restore 안정성 문제 회피)
- 분석서 FSM 전이 테이블과 CSV를 대조하여 근본 원인 특정
- TB 공유 컴포넌트와 테스트케이스는 1회 분석 후 캐시하여 재사용

상세: `venezia-fpga/docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md`

## v3 Improvement Plan

7개 개선 항목 계획됨:
1. `sim_restart` snapshot name 에러 수정
2. `bisect` 2-mode (checkpoint + dump 기반)
3. Dump signal scope 사전 분석 (`prepare_dump_scope`)
4. `save/restore` 안정화
5. simvisdbutil CSV 추출 tool (`extract_waveform_csv`)
6. Batch mode simulation tool (`sim_batch_run`, `sim_batch_regression`)
7. Script 재사용 정책 (기존 스크립트 탐색 → 없을 때만 생성)

상세: `venezia-fpga/docs/01-plan/features/xcelium-mcp-v3-improvements.plan.md`

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
