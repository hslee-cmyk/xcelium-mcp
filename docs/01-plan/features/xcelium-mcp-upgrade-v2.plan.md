# xcelium-mcp-upgrade-v2 Plan

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | Phase 1-3 메타 명령이 bridge Tcl에만 존재하여 raw TCP로만 호출 가능. AI agent가 xcelium-mcp MCP tool로 직접 사용할 수 없음. __BISECT__ 속도 최적화 필요. run_sim_mcp ping 경쟁 문제. |
| **Solution** | Python MCP 서버(server.py)에 7개 신규 tool 추가 (T1-T7) + bridge Tcl 2건 최적화 + run_sim_mcp 스크립트 수정 |
| **Function UX Effect** | AI agent가 `shutdown_simulator`, `watch_signal`, `bisect_signal` 등을 MCP tool로 직접 호출. __BISECT__ 2-3배 속도 향상. |
| **Core Value** | bridge 메타 명령 → MCP tool 승격으로 AI agent 접근성 완성 |

---

## 1. Background

### 1.1 현재 상태

xcelium-mcp-upgrade Phase 1-3 구현 완료 (2026-03-26):
- `mcp_bridge.tcl`에 6개 메타 명령 추가 (`__SHUTDOWN__`, `__WATCH__`, `__PROBE_CONTROL__`, `__SAVE__`/`__RESTORE__`, `__BISECT__`)
- 모두 cloud0 테스트 PASS, TOP015 실전 검증 성공

### 1.2 남은 문제

| # | 문제 | 영향 |
|---|------|------|
| 1 | **MCP tool 미노출** | `__WATCH__`, `__BISECT__` 등을 사용하려면 raw TCP 전송 필요. xcelium-mcp의 MCP tool로 노출되지 않아 AI agent가 직접 호출 불가 |
| 2 | **`__BISECT__` 속도** | 매 반복마다 time 0에서 restart → start_ns까지 run. start_ns가 클수록 낭비 |
| 3 | **run_sim_mcp ping 경쟁** | 내부 ping 루프가 bridge 단일 클라이언트 슬롯 점유 → ready 감지 실패 |
| 4 | **`__PROBE_CONTROL__` all-or-nothing** | 전체 probe on/off만 가능, scope 단위 선택 불가 |

---

## 2. Goals

### 2.1 Must Have (P0) — Python MCP Tool 추가

`server.py`에 5개 tool 추가. 기존 `TclBridge.execute()`로 bridge 메타 명령 호출.

| # | Tool 이름 | Bridge 명령 | 파라미터 |
|---|----------|------------|---------|
| T1 | `shutdown_simulator` | `__SHUTDOWN__` | 없음 |
| T2 | `watch_signal` | `__WATCH__` | signal, op, value |
| T3 | `watch_clear` | `__WATCH_CLEAR__` | id (기본 "all") |
| T4 | `probe_control` | `__PROBE_CONTROL__` | mode: enable/disable |
| T5 | `bisect_signal` | `__BISECT__` | signal, op, value, start_ns, end_ns, precision_ns |

추가로 `__SAVE__`/`__RESTORE__`도 tool로 노출:

| # | Tool 이름 | Bridge 명령 | 파라미터 |
|---|----------|------------|---------|
| T6 | `save_checkpoint` | `__SAVE__` | name (기본 auto) |
| T7 | `restore_checkpoint` | `__RESTORE__` | name (기본 last) |

### 2.2 Should Have (P1) — __BISECT__ 최적화

**현재**: 매 반복 → restore to time 0 → run to start_ns → set watch → run to mid
**개선**: 반복 시작 시 start_ns에 별도 checkpoint 저장 → 매 반복마다 start_ns에서 바로 restore

```
Before: restore(0) → run(start_ns) → watch → run(mid-start)  ← start_ns 구간 매번 낭비
After:  restore(start_checkpoint) → watch → run(mid-start)    ← 즉시 시작
```

start_ns가 업데이트될 때만 새 checkpoint 저장.

**예상 효과**: start_ns가 7.5ms일 때 매 반복 ~7.5ms 시뮬 시간 절약 → **전체 2-3배 속도 향상**.

### 2.3 Nice to Have (P2)

| # | 개선안 | 설명 |
|---|--------|------|
| N1 | `run_sim_mcp` ping 경쟁 수정 | ready 확인을 bridge가 `/tmp/mcp_bridge_ready` 파일 생성하는 방식으로 변경 |
| N2 | `__PROBE_CONTROL__` scope 지정 | `__PROBE_CONTROL__ enable scope_path` 형태로 확장 |
| N3 | `tcl_bridge.py` timeout 설정 가능 | `__BISECT__` 같은 긴 명령에 대한 timeout override |

---

## 3. Scope

### 3.1 수정 대상 파일

| 파일 | 위치 | 변경 내용 |
|------|------|----------|
| `server.py` | `xcelium-mcp/src/xcelium_mcp/` | T1-T7 tool 추가 |
| `tcl_bridge.py` | `xcelium-mcp/src/xcelium_mcp/` | execute timeout override 지원 |
| `mcp_bridge.tcl` | `xcelium-mcp/tcl/` | __BISECT__ 최적화, bridge ready 파일 생성 |
| `run_sim_mcp` | `venezia-t0/sim/ncsim/` | ping 방식 변경 (P2) |

### 3.2 수정하지 않는 것

- `mcp_bridge.tcl`의 기존 Phase 1-3 메타 명령 인터페이스 (하위 호환 유지)
- `screenshot.py` (변경 불필요)

---

## 4. Implementation Order

```
Phase A (P0 — Python MCP Tool)
  ├─ T1-T4: shutdown, watch, watch_clear, probe_control  (30분)
  ├─ T5: bisect_signal (timeout override 포함)            (1시간)
  └─ T6-T7: save/restore_checkpoint                       (20분)

Phase B (P1 — BISECT 최적화)
  └─ mcp_bridge.tcl __BISECT__ 중간 checkpoint 로직      (1시간)

Phase C (P2 — 부가 개선)
  ├─ N1: run_sim_mcp ping 수정                            (30분)
  ├─ N2: __PROBE_CONTROL__ scope 지정                     (30분)
  └─ N3: tcl_bridge.py timeout 파라미터                    (20분)
```

---

## 5. 테스트 계획

| Phase | 테스트 방법 |
|-------|-----------|
| A | cloud0에서 xmsim 실행 → xcelium-mcp MCP tool로 T1-T7 호출 → TOP015 재현 |
| B | __BISECT__ 최적화 전후 속도 비교 (동일 조건: regAddr==0x11, 0-15ms, 100us) |
| C | run_sim_mcp로 시뮬 시작 → bridge ready 정상 감지 확인 |

---

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| `__BISECT__`의 긴 timeout이 MCP 클라이언트에서 disconnect 유발 | `tcl_bridge.py`에 per-command timeout override 추가 |
| checkpoint 중간 저장이 disk 공간 초과 | `/tmp/mcp_bisect/` 크기 모니터링, 자동 정리 |
| Python tool 추가 시 기존 18개 tool과 이름 충돌 | 기존 tool 이름 확인 후 네이밍 (`shutdown_simulator` vs `disconnect_simulator`) |
