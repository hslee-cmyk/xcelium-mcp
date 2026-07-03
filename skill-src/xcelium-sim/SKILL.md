---
name: xcelium-sim
description: |
  xcelium-mcp MCP tool(24개)을 phase별로 언제·어떤 파라미터로 쓸지 안내. RTL 시뮬레이션 디버깅
  워크플로우(Phase 0 인프라 분석~Phase 5 수정+regression)를 단계별로 가이드.
  트리거: xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, debugging, 디버깅, CSV,
    checkpoint, bisect, regression, dump_scopes, dump_depth.
argument-hint: ""
user-invocable: false
---

# xcelium-sim

xcelium-mcp(Cadence Xcelium/SimVision MCP 서버)의 24개 tool을 RTL 디버깅 workflow의 phase별로 언제·어떻게 쓸지 안내한다. `~/.claude/skills/xcelium-sim/`(user-level)에 배포되어, xcelium-mcp를 사용하는 모든 RTL 프로젝트 세션(venezia-fpga 등)에서 동작한다.

## Phase 1 — Tool 사용법 가이드 (이 문서 소관)

이 skill은 6-phase 디버깅 workflow를 phase별 reference로 안내한다. 관련 키워드(위 트리거 목록)가 대화에 등장하면, 현재 상황(로그/dump 유무 등)을 보고 해당 phase reference를 로드한다.

| Phase | Reference | 내용 |
|-------|-----------|------|
| Phase 0 | `references/phase-0-discovery.md` | 검증 환경 인프라 분석(TB 캐시, 공유 컴포넌트) |
| Phase 1 | `references/phase-1-analysis.md` | 사전 분석(캐시 참조, RTL 분석서, dump scope — `dump_scopes` v5.2 포함) |
| Phase 2 | `references/phase-2-simulation.md` | 시뮬레이션 실행(Batch/Bridge, sentinel 중단) |
| Phase 3 | `references/phase-3-triage.md` | 1차 판별(로그 기반) |
| Phase 4 | `references/phase-4-waveform.md` | 2차 판별(waveform CSV, bisect, FSM 전이 대조) |
| Phase 5 | `references/phase-5-fix-regression.md` | 수정 + Regression + 문서 갱신 |
| — | `references/tool-map.md` | 24개 tool 전체 결정 매트릭스 (모든 phase에서 참조) |

### 사용 절차

1. 트리거 키워드 감지 시 이 SKILL.md 로드
2. 대화 맥락(로그 존재 여부, dump 존재 여부, 이미 알려진 판별 신호 등)으로 현재 phase 판단
3. 해당 `references/phase-N-*.md` 로드 → 절차 확인
4. 구체적 tool 호출/파라미터가 필요하면 `references/tool-map.md` 참조
5. Phase 1/4/5에서는 `verilog-rtl-debugger` agent(chip-design-skills) 위임 여부를 각 reference의 "agent 위임" 절에서 확인 — agent가 없으면 Claude가 직접 수행(각 reference의 fallback 문구 참조)

### 트리거 판단 기준 (오탐 방지)

- xcelium-mcp/venezia-fpga와 무관한 프로젝트의 일상 대화에서는 로드하지 않는다 — "테스트" 같은 범용어 단독으로는 트리거하지 않고, xcelium/시뮬레이션 특화 키워드(위 목록)가 명시적으로 나와야 한다.
- 다른 skill(verilog-rtl, chip-verification 등)과 동시 활성화될 수 있다 — 이 skill은 tool 사용법에, 그쪽은 RTL 설계/검증 방법론에 집중한다.

<!-- ============================================================
     PHASE 2 확장점 (xcelium-mcp-debug-workflow-v2가 추가 예정)
     이 마커 아래에 subcommand 라우팅(/sim run|analyze|debug|verify|status)이
     compound.py(Layer 3) 완성 후 삽입된다. Phase 1 구현 시점에는 빈 상태로 둔다.
     xcelium-mcp-debug-workflow-v2의 Design 단계는 이 마커 아래에만 내용을 추가하고,
     위쪽(Phase 1 산출물)은 건드리지 않는다.
     ============================================================ -->

## Phase 2 — Subcommand 라우팅 (Pending, xcelium-mcp-debug-workflow-v2 소관)

*(compound.py 완성 후 채워짐 — 현재는 Phase 1의 phase reference만으로 동작)*
