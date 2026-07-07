# xcelium-mcp-sim-session-reaper Plan

> **Feature**: bridge 모드(`connect_simulator`/`sim_bridge_run`)로 붙인 xmsim/SimVision 프로세스는
> MCP worker의 자식이 아니라 독립 프로세스다. 디버깅 세션 종료 시 `sim_disconnect(action=shutdown)`을
> 명시적으로 호출하지 않으면(비정상 종료, 크래시, 사람이 단순히 잊는 경우 등) 그 프로세스가 host에
> 무기한 남아 disk를 소진할 수 있다 — 실제로 검증 완료 후 수 주간 방치된 시뮬레이션이 host disk를
> 전부 잡아먹은 사고가 있었다. 이를 서버 측에서 자동으로 강제 정리하는 reaper를 추가한다.
>
> **Date**: 2026-07-07
> **Status**: Draft
> **Found in**: `xcelium-mcp-session-state-reattach` 완료 보고 이후, 사용자가 겪은 실제 disk 소진
> 사고를 공유하며 "이걸 방지하는 것을 강제하도록 했으면 좋겠다"고 요청

---

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | `mcp_bridge.tcl`에 이미 안전한 종료 수단(`__SHUTDOWN__` → SHM 보존 + `finish`)이 있고 Python 쪽 `sim_disconnect(action="shutdown")` tool도 있지만, **아무도 강제로 호출하지 않으면 xmsim/SimVision 프로세스가 무기한 방치된다.** bridge 모드의 xmsim은 MCP worker의 자식이 아니므로, worker가 재시작되거나(F-A) idle-culler(F-B)가 worker를 정리해도 xmsim 자체는 전혀 영향받지 않는다. 실제로 검증 완료 후 수 주간 방치된 시뮬레이션이 host disk를 전부 소진시킨 사고가 있었다. |
| **Solution** | (1) xcelium-sim skill Phase 5에 세션 종료 시 `sim_disconnect(shutdown)` 호출을 필수 체크리스트로 추가(정상 종료 경로 커버, 이미 완료). (2) F-C 레지스트리(`registry.py`, sim_dir별 `bridge_port` 이미 기록 중)에 "마지막 명령 실행 시각"을 추가 기록하고, 별도 cron reaper가 주기적으로 순회하며 설정 가능한 TTL(기본 48h)을 넘게 활동 없는 세션을 찾아 그 포트에 직접 접속해 `__SHUTDOWN__`을 보낸다(비정상 종료·방치 경로까지 커버하는 근본 해결책). (3) 현재 살아있는 세션과 마지막 활동 시각을 조회하는 가시성 tool을 추가해, TTL 만료 전에도 사람이 직접 확인·수동 종료할 수 있게 한다. |
| **Function/UX Effect** | 디버깅 세션을 정상 종료하지 않고 방치해도, 설정된 TTL 이후 자동으로 안전하게(SHM 보존) 정리된다. 방치 중인 세션은 신규 tool로 언제든 조회 가능해 TTL을 기다리지 않고도 수동 정리할 수 있다. batch/regression 모드(`sim_batch_run`/`sim_regression`)는 이미 자체 정리 로직이 있어 이 feature의 대상이 아니다. |
| **Core Value** | "몇 주 방치된 시뮬레이션이 disk를 전부 잡아먹는" 사고를 재발 불가능하게 만든다 — 사람/AI가 무엇을 하든(정상 종료, 비정상 종료, 단순 망각) 결과적으로 프로세스가 방치되는 시간에 상한선이 생긴다. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 실제 발생한 host disk 소진 사고(검증 완료 후 수 주 방치된 xmsim)의 재발 방지 |
| **WHO** | xcelium-mcp를 bridge 모드로 사용하는 모든 사용자/agent(`verilog-rtl-debugger` 포함) |
| **RISK** | TTL을 너무 짧게 잡으면 정말 장시간 필요한 세션을 실수로 죽일 위험 — 설정 가능한 TTL + 넉넉한 기본값(48h)으로 완화 |
| **SUCCESS** | 방치된 bridge 세션이 TTL 경과 후 자동으로 안전 종료됨을 실증(pytest + 실배포 검증) |
| **SCOPE** | 레지스트리 활동시각 기록 + cron reaper(자동 종료) + 가시성 tool(수동 확인/종료 지원) |

---

## 1. Problem Detail

### 1.1 정확한 코드 경로

```tcl
# tcl/mcp_bridge.tcl — 이미 존재하는 안전 종료 수단
__SHUTDOWN__ → do_shutdown → SHM close 후 finish(xmsim) / exit(simvision)
```

```python
# tools/sim_lifecycle.py — 이미 존재하는 Python 측 tool
sim_disconnect(action="shutdown", target="all")
```

두 수단 모두 **누군가 명시적으로 호출해야만** 동작한다. 호출을 강제하는 장치가 지금까지 없었다.

### 1.2 재현 시나리오(실제 발생)

1. bridge 모드로 xmsim에 연결해 디버깅 세션 진행, 검증 완료.
2. `sim_disconnect(shutdown)`을 호출하지 않고 세션 종료(단순 망각, 또는 크래시/네트워크 단절로 정상
   종료 경로 자체가 실행되지 못함).
3. xmsim 프로세스는 host에 계속 살아있음 — MCP worker의 자식이 아니므로 F-A(수퍼바이저)/F-B(idle
   culler) 어느 쪽도 이 프로세스를 감지·정리하지 못함.
4. 수 주가 지난 뒤 host disk 사용량이 한계에 도달 — 원인 추적 결과 방치된 xmsim이 계속 SHM 덤프를
   보유(또는 단순히 프로세스+파일이 방치됨)하고 있었음이 확인됨.

### 1.3 왜 F-A/F-B가 이 문제를 못 막는가

- F-A(수퍼바이저)는 **Python MCP worker 프로세스**만 관리한다. xmsim/SimVision은 별도 이진(binary)
  실행 파일이며, bridge 모드에서는 그 프로세스를 xcelium-mcp가 fork/spawn 하지도 않는다(사람이
  `xrun -gui -input "@simvision {...}"`로 직접 띄우거나, 이전 세션이 이미 띄워둔 것에 접속).
- F-B(idle-culler)의 대상도 **Python worker**다. worker가 idle이라 정리돼도 xmsim은 소켓 연결이
  끊길 뿐 그대로 실행을 계속한다.
- batch/regression 모드(`sim_batch_run`/`sim_regression`)는 `batch_runner.py`가 nohup으로 직접
  띄운 프로세스라 완료/타임아웃 시 스스로 정리한다(`_kill_stale_sim`, timeout 분기의
  `pkill -f xmrm` 등, 세션-state-reattach Plan §1.4에서 이미 확인됨) — **이 feature의 대상이 아니다.**

---

## 2. Fix Items

### F-1: xcelium-sim skill 강제 체크리스트 (완료, 즉시 적용)

`skill-src/xcelium-sim/references/phase-5-fix-regression.md`에 "5F. 세션 종료 — 시뮬레이션 프로세스
정리(필수)" 섹션 추가, `phase-3-triage.md`의 PASS 경로도 5F로 라우팅, `SKILL.md` Phase 5 요약에도
반영. 추가로 `SKILL.md` 본문 최상단(phase 테이블보다 앞)에 독립된 경고 블록을 넣어, **Phase 0~4
중 어디서 대화가 끝나도(Phase 5까지 진행하지 못해 `phase-5-fix-regression.md`를 아예 열지 않아도)**
skill이 로드되는 한 항상 노출되게 함 — verilog-rtl-debugger가 5C/5D(regression) 실행 중에만
`phase-5-fix-regression.md`를 읽는다는 한계(세션이 Phase 5 전에 끝나면 5F를 못 봄)를 보완.
`cp -r skill-src/xcelium-sim ~/.claude/skills/`로 재배포 완료.

- **한계**: 어디까지나 프롬프트 기반 안내이지 강제가 아니다. LLM이 그 지침을 읽고 실제로 행동할지는
  확률적이고, 애초에 "세션이 끝났다"는 결정적 신호(hook) 자체가 대화형 세션에는 없다 — 사용자가
  그냥 대화를 떠나면 agent가 5F를 실행할 기회조차 생기지 않는다. 정상적으로 차분히 종료하는
  경우의 준수율을 높이는 완화책일 뿐, 비정상 종료(크래시, 네트워크 단절, 세션을 그냥 떠남)를
  포함한 실제 보장은 F-2/F-3에서만 나온다.

### F-2: 레지스트리 활동시각 기록 + cron reaper (근본 해결)

- **활동시각 기록**: bridge 세션에 명령이 전달될 때마다(`TclBridge.execute`/`execute_safe` 경유)
  F-C 레지스트리(`registry.py`)의 `environments[sim_dir]` 엔트리에 `last_activity`(epoch 초)를
  갱신. 매 명령마다 디스크 쓰기가 발생하므로, 과도한 I/O를 피하기 위해 유의미한 간격(예: 마지막
  기록 후 N초 이상 경과했을 때만 갱신)으로 스로틀링하는 방식을 Design에서 확정한다.
- **reaper**: idle_culler.py와 같은 cron 패턴의 신규 모듈(`sim_session_reaper.py`)이 주기적으로
  레지스트리를 순회 → TTL(기본 48h, 환경변수/설정 파일로 override 가능)을 넘게 활동 없는 세션
  발견 → 그 세션의 `bridge_port`로 직접 TCP 접속(`TclBridge` 재사용) → `__SHUTDOWN__` 전송 →
  성공/실패 무관하게 레지스트리에서 해당 세션 항목 정리.
  - 포트가 이미 죽어있으면(고아 레지스트리 엔트리) 접속 실패 → 레지스트리 정리만 수행.
  - batch/regression job(F-E가 이미 보호)과는 무관한 대상이므로 상호 간섭 없음 — bridge 세션만
    이 reaper의 대상.
- **안전장치**: TTL을 넘겼다고 무조건 죽이지 않고, 최소 1회 이상 연속 감지(예: reaper 실행 주기가
  30분이면 최소 2회 연속 TTL 초과 확인) 후 종료하는 방식을 Design에서 검토 — 레지스트리 읽기/쓰기
  타이밍 경합으로 인한 오탐 방지.

### F-3: 가시성 tool 추가

- 신규 tool(예: `list_active_sessions` 또는 기존 `sim_status`의 확장) — 레지스트리에 기록된 모든
  bridge 세션(sim_dir, port, test_name, last_activity, TTL까지 남은 시간)을 조회.
- TTL 만료를 기다리지 않고도 사람이 직접 방치 세션을 확인해 `sim_disconnect(shutdown)`으로 수동
  정리할 수 있게 한다 — reaper의 안전망 역할이자, "내가 뭘 띄워놨는지 까먹었다"는 근본 원인에
  대한 가장 직접적인 대응.

---

## 3. Scope

| 파일 | 변경 내용 |
|------|----------|
| `skill-src/xcelium-sim/references/phase-5-fix-regression.md`, `phase-3-triage.md`, `SKILL.md` | F-1: 세션 종료 체크리스트(완료, 재배포 완료) |
| `src/xcelium_mcp/registry.py` | F-2: `environments[sim_dir]`에 `last_activity` 필드 추가, 갱신/조회 헬퍼 |
| `src/xcelium_mcp/tcl_bridge.py` | F-2: 명령 실행 시 활동시각 갱신 훅(스로틀링 포함) — Design에서 정확한 삽입 지점 확정 |
| `src/xcelium_mcp/sim_session_reaper.py`(신규) | F-2: TTL 순회 + `__SHUTDOWN__` 전송 + 레지스트리 정리 |
| `src/xcelium_mcp/tools/sim_lifecycle.py` | F-3: 가시성 tool 추가(신규 또는 `sim_status` 확장) |
| `deploy/crontab.example` | F-2: `sim_session_reaper` cron 항목 추가 |

---

## 4. Success Criteria

| # | 기준 | 검증 |
|---|------|------|
| T-1 | bridge 세션에 명령 실행 시 레지스트리 `last_activity` 갱신 | pytest |
| T-2 | TTL 초과 세션을 reaper가 발견해 `__SHUTDOWN__` 전송 | pytest(TclBridge 모킹) |
| T-3 | TTL 미초과 세션은 건드리지 않음 | pytest |
| T-4 | 포트가 이미 죽은 고아 레지스트리 엔트리 → 정리만 수행, 크래시 없음 | pytest |
| T-5 | batch/regression job은 이 reaper의 대상이 아님(간섭 없음) | pytest |
| T-6 | 가시성 tool이 현재 세션 목록 + TTL까지 남은 시간을 정확히 반환 | pytest |
| T-7 | 실배포: cloud0에 cron 등록 후 실제 방치 세션(짧은 TTL로 재현)이 자동 종료됨을 확인 | 실측(SSH) |
| T-8 | 회귀 | 기존 pytest 스위트 전체 통과 |

---

## 5. Out of Scope

- SHM/dump 디렉토리 자체의 디스크 사용량 모니터링(용량 기반 알림) — 이번 feature는 시간(TTL) 기반
  프로세스 정리만 다룬다. 필요 시 후속 feature로 별도 진행.
- batch/regression job의 정리 로직 변경 — 이미 자체 정리가 있어 범위 밖(`xcelium-mcp-session-state-reattach.plan.md` §1.4 F-E에서 확인됨).
- 세션 "pin"(TTL 예외 지정) 기능 — v1 범위 밖, 필요성이 확인되면 후속 논의.

---

## 6. Related Documents

- [xcelium-mcp-server-process-lifecycle.plan.md](xcelium-mcp-server-process-lifecycle.plan.md), [.design.md](../../02-design/features/xcelium-mcp-server-process-lifecycle.design.md) — F-A(수퍼바이저)/F-B(idle-culler) 패턴 재사용 대상.
- [xcelium-mcp-session-state-reattach.plan.md](xcelium-mcp-session-state-reattach.plan.md) §1.4 — batch/regression이 이미 자체 정리를 갖고 있음을 확인한 근거(F-E).
- `skill-src/xcelium-sim/references/phase-5-fix-regression.md` §5F, `phase-3-triage.md`, `SKILL.md`(최상단 경고 블록) — F-1(skill 체크리스트) 실제 반영 위치.
- `src/xcelium_mcp/registry.py`, `tcl_bridge.py`, `batch_runner.py` — F-2/F-3 재사용 대상 인프라.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-07-07 | 초안 — 실제 host disk 소진 사고 재발 방지 요청에 따라 작성. F-1(skill 체크리스트, 완료·재배포)은 즉시 반영, F-2(자동 reaper)/F-3(가시성 tool)은 이 Plan을 거쳐 Design/Do로 진행. 사용자 확인: TTL은 설정 가능하게(기본 48h), 가시성 tool 포함. |
| 0.2 | 2026-07-07 | F-1 보강 — 사용자 질의("agent가 새 규율을 읽어서 알고는 있게 되는가?")에 답하는 과정에서, `phase-5-fix-regression.md`의 5F는 세션이 Phase 5(regression)까지 진행해야만 노출됨을 확인. `SKILL.md` 최상단에 phase 무관 독립 경고 블록 추가·재배포로 인지 범위를 넓힘. 단, 이것도 프롬프트 기반 완화책일 뿐 강제가 아님을 명시(F-2/F-3만이 실제 보장). |
