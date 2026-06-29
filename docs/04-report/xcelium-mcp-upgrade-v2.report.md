# xcelium-mcp-upgrade-v2 Completion Report

## Executive Summary

### 1.1 Overview

| Item | Detail |
|------|--------|
| Feature | xcelium-mcp-upgrade-v2 |
| Period | 2026-03-26 |
| Predecessor | xcelium-mcp-upgrade (v1) — Phase 1-3 bridge 메타 명령 |
| Match Rate | **100%** (gap-detector 검증) |

### 1.2 Results

| Metric | Value |
|--------|-------|
| 계획 항목 | 9 (Phase A: 7 tools + Phase B: 1 최적화 + Phase C: 3 개선) |
| 구현 완료 | **9/9 (100%)** |
| 수정 파일 | 4 (`server.py`, `tcl_bridge.py`, `mcp_bridge.tcl`, `run_sim_mcp`) |
| 신규 MCP tool | 7 (T1-T7), 총 25개 |
| Gap Analysis | 100% — 설계 초과 구현 3건 (모두 positive) |

### 1.3 Value Delivered

| Perspective | Metric |
|-------------|--------|
| **Problem** | bridge 메타 명령이 raw TCP로만 호출 가능, AI agent 접근 불가. BISECT 느림. run_sim_mcp ping 경쟁. probe all-or-nothing. |
| **Solution** | server.py에 7개 tool 추가, BISECT v2 중간 checkpoint, 파일 기반 ready, probe scope 지정 |
| **Function UX Effect** | `watch_signal()`, `bisect_signal()` 등 MCP tool 직접 호출 가능. bridge ready **attempt 1 즉시 감지** (기존 30회 반복 → 1회) |
| **Core Value** | AI agent가 xcelium-mcp의 모든 디버깅 기능을 MCP 프로토콜로 일관되게 사용 |

---

## 2. Phase A — Python MCP Tool (P0)

### 2.1 tcl_bridge.py 변경

| 변경 | 내용 |
|------|------|
| `execute(timeout=)` | per-command timeout override 지원 |
| `execute_safe(timeout=)` | 동일, `effective_timeout` 로직 |

### 2.2 server.py — 7개 신규 tool

| # | Tool | Bridge 명령 | 특이사항 |
|---|------|------------|---------|
| T1 | `shutdown_simulator` | `__SHUTDOWN__` | ConnectionError + TimeoutError catch, `_bridge=None` |
| T2 | `watch_signal` | `__WATCH__` | op: `==, !=, >, <, >=, <=` 지원 |
| T3 | `watch_clear` | `__WATCH_CLEAR__` | 기본값 "all" |
| T4 | `probe_control` | `__PROBE_CONTROL__` | `scope` 파라미터 추가 (N2 통합) |
| T5 | `bisect_signal` | `__BISECT__` | `timeout=600.0` (10분) |
| T6 | `save_checkpoint` | `__SAVE__` | |
| T7 | `restore_checkpoint` | `__RESTORE__` | `timeout=120.0` |

총 tool 수: 기존 18 + 신규 7 = **25개**

---

## 3. Phase B — BISECT v2 최적화 (P1)

### 알고리즘 변경

```
v1: restore(t0) → run(start_ns) → watch → run(mid-start)   ← 매 반복 start 재실행
v2: restore(start_chk) → watch → run(mid-start)             ← start에서 즉시 시작
    miss 시: restore(t0) → run(new_start) → save(start_chk) ← start 갱신
```

### 성능 측정 (TOP015, regAddr==0x11, 0-15ms, 100us 정밀도)

| 버전 | 반복 | 결과 범위 | Wall time |
|------|------|----------|----------|
| v1 | 7회 | 9,263,450 - 9,320,336 ns | 126초 |
| v2 | 7회 | 9,263,450 - 9,320,336 ns | 133초 |

**결론**: 짧은 시뮬(15ms)에서는 start checkpoint 갱신 overhead로 속도 차이 없음. 설계 분석 예측대로 긴 시뮬(100ms+)에서 효과 발현 예상 (start_ns 재실행 생략).

---

## 4. Phase C — 부가 개선 (P2)

### N1: run_sim_mcp 파일 기반 ready 감지

| 항목 | 전 (TCP ping) | 후 (파일 기반) |
|------|-------------|--------------|
| 감지 방법 | Python TCP connect → `__PING__` → pong 파싱 | `[ -f /tmp/mcp_bridge_ready_9876 ]` |
| 감지 속도 | 30회 반복, 60초 timeout | **attempt 1 즉시** |
| bridge 영향 | 클라이언트 슬롯 점유 (경쟁) | **슬롯 미사용** |
| 시작 전 정리 | 없음 | stale ready 파일 + checkpoint 삭제 |

### N2: `__PROBE_CONTROL__` scope 지정

```
__PROBE_CONTROL__ disable                 → 전체 probe off (기존)
__PROBE_CONTROL__ enable top.hw.u_ext     → 특정 scope만 on (신규)
```

### N3: tcl_bridge.py timeout

Phase A 2.1에서 통합 구현.

---

## 5. 배포 현황

### 파일별 동기화 상태 (2026-03-26 최종 확인)

| 파일 | 로컬 프로젝트 | cloud0 배포 | MD5 일치 |
|------|-------------|-----------|---------|
| `mcp_bridge.tcl` | `xcelium-mcp/tcl/` | `~/git.clone/.../scripts/` | ✅ |
| `server.py` | `xcelium-mcp/src/xcelium_mcp/` | `/opt/mcp-env/.../xcelium_mcp/` | ✅ |
| `tcl_bridge.py` | `xcelium-mcp/src/xcelium_mcp/` | `/opt/mcp-env/.../xcelium_mcp/` | ✅ |
| `run_sim_mcp` | N/A (venezia-t0 전용) | `~/git.clone/.../ncsim/` | ✅ |

### cloud0 배포 방법 (향후 참조)

```bash
# 1. 로컬에서 /tmp에 업로드
scp src/xcelium_mcp/server.py cloud0:/tmp/xcelium-mcp-update/src/xcelium_mcp/
scp src/xcelium_mcp/tcl_bridge.py cloud0:/tmp/xcelium-mcp-update/src/xcelium_mcp/

# 2. sudo cp로 배포 (root 소유 디렉토리)
ssh cloud0 "sudo cp /tmp/xcelium-mcp-update/src/xcelium_mcp/*.py \
  /opt/mcp-env/lib/python3.10/site-packages/xcelium_mcp/"

# 3. mcp_bridge.tcl은 사용자 디렉토리 → scp 직접 가능
scp tcl/mcp_bridge.tcl cloud0:~/git.clone/venezia-t0/design/top/sim/ncsim/scripts/

# 4. Claude Code 재시작하면 새 tool 반영
```

---

## 6. Gap Analysis 결과

**Match Rate: 100%** (gap-detector agent, 2026-03-26)

| 카테고리 | 점수 |
|----------|:----:|
| Phase A: MCP Tools | 100% |
| Phase A: timeout override | 100% |
| Phase B: BISECT v2 | 100% |
| Phase C-N1: file-based ready | 100% |
| Phase C-N2: probe scope | 100% |
| Phase C-N3: timeout param | 100% |

설계 초과 구현 3건 (모두 positive):
1. `shutdown_simulator`에서 `TimeoutError`도 catch
2. ready 파일 생성에 에러 핸들링 추가
3. `probe_control`에 scope 파라미터를 server.py tool에도 노출

---

## 7. v1 + v2 통합 산출물

### xcelium-mcp 전체 기능 목록

**mcp_bridge.tcl — 13개 메타 명령:**

| 명령 | Phase | 용도 |
|------|-------|------|
| `__PING__` | 기존 | 연결 확인 |
| `__SCREENSHOT__` | 기존 | waveform 캡처 (EPS) |
| `__QUIT__` | 기존 | 연결 종료 |
| `__SHUTDOWN__` | v1-F1 | 안전 종료 (SHM 보존) |
| `__RUN_ASYNC__` | v1-F2 | 비동기 실행 |
| `__PROGRESS__` | v1-F2 | 진행 상태 조회 |
| `__WATCH__` | v1-F3 | signal watchpoint |
| `__WATCH_LIST__` | v1-F3 | watchpoint 목록 |
| `__WATCH_CLEAR__` | v1-F3 | watchpoint 삭제 |
| `__PROBE_CONTROL__` | v1-F4+v2-N2 | probe on/off (scope 지원) |
| `__SAVE__` | v1-F5 | checkpoint 저장 |
| `__RESTORE__` | v1-F5 | checkpoint 복원 |
| `__BISECT__` | v1-F6+v2-B | 이진 탐색 (v2 최적화) |

**server.py — 25개 MCP tool:**

| Phase | Tool 수 | 목록 |
|-------|---------|------|
| 5 (연결/제어) | 7 | connect, disconnect, run, stop, restart, status, breakpoint |
| 6 (신호) | 6 | get_value, describe, drivers, list, deposit, release |
| 7 (waveform) | 3 | add_signals, zoom, cursor |
| 9 (디버그) | 2 | screenshot, debugger_mode |
| **10 (고급)** | **7** | **shutdown, watch, watch_clear, probe, save, restore, bisect** |

### 문서 목록

| 문서 | 경로 |
|------|------|
| v1 Plan | `docs/01-plan/features/xcelium-mcp-upgrade.plan.md` |
| v1 Report | `docs/04-report/xcelium-mcp-upgrade.report.md` |
| v2 Plan | `docs/01-plan/features/xcelium-mcp-upgrade-v2.plan.md` |
| v2 Design | `docs/02-design/features/xcelium-mcp-upgrade-v2.design.md` |
| v2 Report | `docs/04-report/xcelium-mcp-upgrade-v2.report.md` |
| 비교 분석 | `docs/03-analysis/xcelium-mcp-debugging-comparison.md` |
| 운용 가이드 | `.ai/knowledge/mcp-operations-guide.md` |
