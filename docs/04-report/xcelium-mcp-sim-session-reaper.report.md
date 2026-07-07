# xcelium-mcp-sim-session-reaper Completion Report

> **Status**: Complete
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Completion Date**: 2026-07-07
> **PDCA Cycle**: Plan → Design(Option C) → Do(F-1/F-2/F-3) → Check(Match Rate 100%, T-11 라이브 검증 포함)

---

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | 방치된 bridge 모드 xmsim 세션의 강제 자동 종료(F-2/F-3) + skill 종료 체크리스트(F-1) |
| Start Date | 2026-07-07 (`xcelium-mcp-session-state-reattach` 완료 직후, 사용자가 실제 겪은 host disk 소진 사고 공유에서 파생) |
| End Date | 2026-07-07 |
| Duration | 반나절(같은 세션 내 Plan→Design→Do→Check, cloud0 라이브 검증까지 전체 완결) |

### 1.2 Results Summary

```
┌─────────────────────────────────────────────┐
│  Match Rate: 100%                            │
├─────────────────────────────────────────────┤
│  ✅ Met:         12 / 12  Test Plan 항목      │
│  ⚠️  Partial:      0 / 12                     │
│  ❌ Critical Gap:  0                          │
└─────────────────────────────────────────────┘
```

### 1.3 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | bridge 모드(`connect_simulator`/`sim_bridge_run`)로 붙인 xmsim/SimVision은 MCP worker의 자식 프로세스가 아니라, F-A(수퍼바이저)/F-B(idle-culler) 어느 쪽도 감지·정리하지 못한다. 세션 종료 시 `sim_disconnect(shutdown)`을 명시적으로 호출하지 않으면(비정상 종료·크래시·단순 망각) 프로세스가 host에 무기한 남는다 — 실제로 검증 완료 후 수 주간 방치된 시뮬레이션이 host disk를 전부 소진시킨 사고가 있었다. |
| **Solution** | F-1: xcelium-sim skill(Phase 5 체크리스트 + Phase 3 라우팅 + SKILL.md 최상단 독립 경고 블록)에 세션 종료 시 `sim_disconnect(shutdown)` 호출을 강제 — 정상 종료 경로의 준수율을 높이는 완화책. F-2: F-C 레지스트리에 `last_activity`/`ttl_miss_count`를 추가하고, 신규 `sim_session_reaper.py`가 idle_culler.py와 동일한 cron 패턴으로 TTL(기본 48h, 환경변수 override 가능) 초과 세션을 연속 2회 확인 후 `__SHUTDOWN__`(SHM 보존)으로 안전 종료 — 사람/AI의 판단에 의존하지 않는 근본 해결책. F-3: `list_active_sessions` tool로 TTL 만료 전에도 방치 세션을 조회·수동 정리 가능. |
| **Function/UX Effect** | 디버깅 세션을 정상 종료하지 않고 방치해도 TTL 이후 자동으로 안전하게 정리된다. 방치 세션은 언제든 조회 가능해 TTL을 기다리지 않고도 수동 정리할 수 있다. batch/regression 모드(F-E가 이미 보호)는 이 reaper의 대상이 아니다. |
| **Core Value** | "몇 주 방치된 시뮬레이션이 disk를 전부 잡아먹는" 사고를 재발 불가능하게 만든다 — 이번 완료 보고 직전 cloud0에서 실제 fake bridge 프로세스로 TTL 2-strike 판정과 `__SHUTDOWN__` 전송을 라이브 재현해, pytest 모킹을 넘어 실제 배포 환경에서의 동작까지 실증했다. |

---

## 1.4 Success Criteria Final Status

| # | Criteria | Status | Evidence |
|---|---------|:------:|----------|
| T-1 | bridge 명령 실행 시 `last_activity` 갱신 | ✅ Met | `test_touch_activity_records_last_activity_and_resets_miss_count` |
| T-2 | throttle 윈도우 내 재호출 시 갱신 스킵 | ✅ Met | `test_touch_activity_throttles_rapid_successive_calls` |
| T-3 | TTL 초과 + 연속 2회 도달 시 `__SHUTDOWN__` + 레지스트리 정리 | ✅ Met | pytest + cloud0 라이브 검증(§1.5) |
| T-4 | TTL 초과 1회차(2회 미달) → 종료 안 함 | ✅ Met | pytest + cloud0 라이브 검증(§1.5) |
| T-5 | TTL 미초과 → miss_count 리셋 | ✅ Met | `test_ttl_not_exceeded_resets_miss_count` |
| T-6 | 레거시 엔트리(활동 기록 없음) → 건드리지 않음 | ✅ Met | `test_legacy_entry_without_last_activity_is_skipped` |
| T-7 | 고아 엔트리(포트 죽음) → 크래시 없이 정리 | ✅ Met | `test_reap_idle_sessions_handles_orphan_port_gracefully` |
| T-8 | batch/regression 엔트리 → 완전히 무시 | ✅ Met | `test_non_bridge_entry_without_port_is_skipped` |
| T-9 | `list_active_sessions` 정확한 TTL 잔여시간 반환 | ✅ Met | 3개 테스트(정상/초과/batch-skip) |
| T-10 | TTL 환경변수 미설정/오류 → 기본값(48h) 폴백 | ✅ Met | 3개 테스트 |
| T-11 | 실배포: cloud0 방치 세션 자동 종료 확인 | ✅ Met | fake bridge + 실제 registry로 라이브 재현, crontab 등록 완료 |
| T-12 | 회귀 | ✅ Met | pytest 584 passed, ruff 클린 |

**Success Rate**: 12/12 criteria met (100%)

## 1.5 Decision Record Summary

| Source | Decision | Followed? | Outcome |
|--------|----------|:---------:|---------|
| [Plan] | F-1(skill 체크리스트, 즉시)과 F-2/F-3(자동 reaper+가시성, PDCA 진행)을 병행 | ✅ | 둘 다 완료 |
| [Design Checkpoint 3] | Option C — 기존 F-C/F-D/F-B 패턴 재사용, 신규 파일 최소화(`sim_session_reaper.py` 1개) | ✅ | 그대로 구현, 신규 파일 정확히 1개 |
| [Design] | `MIN_MISS_COUNT_TO_KILL=2` 안전장치(타이밍 경합 오탐 방지) | ✅ | 구현·pytest·cloud0 라이브 검증 3단계 모두에서 확인 |
| [Do, 구현 중 발견] | Design이 "TclBridge가 이미 sim_dir을 보관한다"고 잘못 가정한 부분 발견 → `TclBridge.__init__`에 `sim_dir` 필드 신규 추가로 정정 | ✅ | 설계 오류를 구현 단계에서 스스로 교정, 회귀 없음 |
| [Check→라이브 검증] | T-11을 pytest 모킹에 머물지 않고 cloud0 실제 프로세스·실제 registry로 재현 | ✅ | 실제 `venezia-t0`/`alaska-c1` 엔트리 무변경 확인 후 진행, crontab 등록까지 완료 |

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [xcelium-mcp-sim-session-reaper.plan.md](../01-plan/features/xcelium-mcp-sim-session-reaper.plan.md) | ✅ Finalized (v0.1) |
| Design | [xcelium-mcp-sim-session-reaper.design.md](../02-design/features/xcelium-mcp-sim-session-reaper.design.md) | ✅ Finalized (v0.1) |
| Check | [xcelium-mcp-sim-session-reaper.analysis.md](../03-analysis/xcelium-mcp-sim-session-reaper.analysis.md) | ✅ Complete (Match Rate 100%) |
| Report | Current document | ✅ Complete |

---

## 3. Completed Items

### 3.1 Functional Requirements

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| F-1 | xcelium-sim skill 세션 종료 체크리스트(강제 완화책) | ✅ Complete | `phase-5-fix-regression.md` §5F, `phase-3-triage.md`, `SKILL.md` 최상단 경고 블록. `~/.claude/skills/xcelium-sim/`에 배포 완료 |
| F-2 | 레지스트리 활동시각 기록 + cron reaper(근본 해결) | ✅ Complete | `registry.py`, `tcl_bridge.py`, `sim_session_reaper.py`. cloud0 crontab 등록 완료 |
| F-3 | 가시성 tool | ✅ Complete | `list_active_sessions` |

### 3.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|--------|----------|--------|
| 기존 pytest 회귀 없음 | 563 passed 유지(부모 기능 기준) | 584 passed(563 + 신규 21) | ✅ |
| batch/regression과 완전 분리 | F-E와 간섭 없음 | `bridge_port` 필드 유무로 자동 분리, 테스트로 확인 | ✅ |
| 신규 파일 최소화(Option C) | 1개 | `sim_session_reaper.py` 1개만 신규 | ✅ |
| ruff 클린 | 신규/수정 코드 기준 | 클린 | ✅ |
| 실배포 검증 | cloud0 라이브 재현 | fake bridge + 실제 registry, crontab 등록 완료 | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Status |
|-------------|----------|--------|
| F-1 skill 보강 | `skill-src/xcelium-sim/{SKILL.md, references/phase-3-triage.md, references/phase-5-fix-regression.md}` + `~/.claude/skills/xcelium-sim/` 배포 | ✅ |
| F-2/F-3 구현 | `src/xcelium_mcp/{registry.py, tcl_bridge.py, sim_session_reaper.py, tools/sim_lifecycle.py}` | ✅ |
| 배포 설정 | `deploy/crontab.example` + cloud0 실제 crontab 등록 | ✅ |
| 신규 테스트 | `tests/{test_registry_bridge_port.py, test_bridge.py, test_sim_lifecycle.py}`(확장), `tests/test_sim_session_reaper.py`(신규) — 21개 | ✅ |
| PDCA 문서 | `docs/01-plan`~`docs/04-report` | ✅ |

---

## 4. Incomplete Items

없음 — 이번 사이클 스코프(F-1/F-2/F-3) 전부 완료, Carried Over 항목 없음.

---

## 5. Quality Metrics

### 5.1 Final Analysis Results

| Metric | Target | Final | Change |
|--------|--------|-------|--------|
| Match Rate | 90% | 100% | +10%p (Check 시점 97% → 라이브 검증 후 100%) |
| pytest 스위트 | 563 passed 유지 | 584 passed | +21 |
| ruff 오류 | 0 | 0 | 유지 |

### 5.2 Resolved Issues

| Issue | Resolution | Result |
|-------|------------|--------|
| 방치된 xmsim이 disk를 무기한 소진할 위험 | TTL 기반 자동 reaper(F-2) + skill 체크리스트(F-1) | ✅ Resolved |
| 방치 여부를 사람이 확인할 방법 없음 | `list_active_sessions`(F-3) | ✅ Resolved |
| Design의 "TclBridge가 sim_dir을 이미 안다"는 잘못된 가정 | `TclBridge.__init__`에 `sim_dir` 필드 신규 추가로 정정 | ✅ Resolved |
| T-11이 pytest 모킹에만 머물 위험 | cloud0 실제 프로세스·실제 registry로 라이브 재현 | ✅ Resolved |

---

## 6. Lessons Learned & Retrospective

### 6.1 What Went Well (Keep)

- **실사고에서 출발한 스코프 설정**: "지난번 방치된 시뮬레이션이 disk를 다 잡아먹었다"는 구체적 실사고를 출발점으로 삼아, 추상적 "방지 대책"이 아니라 정확히 그 실패 모드(비정상 종료·방치)를 겨냥한 해결책(F-2)을 설계했다. 사용자의 "agent가 새 규율을 읽어서 알고는 있게 되는가?"라는 날카로운 후속 질문이 F-1만으로는 부족함(프롬프트 기반 완화책의 한계)을 명확히 드러냈고, 그 결과로 F-2(근본 해결)의 필요성이 더 분명해졌다.
- **기존 패턴 재사용의 복리 효과**: F-A(수퍼바이저)/F-B(idle-culler)/F-C(레지스트리)/F-D(세션 상태)가 이미 검증한 "registry.py 헬퍼 + cron 독립 모듈" 조합을 그대로 재사용해, 신규 파일 1개로 새로운 안전장치를 추가했다. Design 단계의 아키텍처 선택(Option C)이 매 사이클 반복되며 검증된 패턴이 되어가고 있다.
- **라이브 검증을 pytest로 끝내지 않음**: T-11을 "실배포 검증은 나중에"로 미루지 않고, 실제 프로세스·실제 레지스트리 파일로 즉시 재현해 Match Rate를 97%에서 100%로 끌어올렸다. 특히 실제 운영 중인 레지스트리 파일을 다루면서도 백업·복원·diff 대조를 통해 실제 프로젝트 데이터를 전혀 건드리지 않은 것이 안전한 실측 검증의 좋은 예가 됐다.

### 6.2 What Needs Improvement (Problem)

- Design 문서 작성 시 `TclBridge`가 이미 `sim_dir`을 보관하고 있다고 가정했다가 구현 단계에서야 사실이 아님을 발견했다 — 실제 코드를 읽지 않고 "당연히 그럴 것"이라고 추측한 부분이었다. Design 단계에서 핵심 클래스의 실제 필드 목록을 직접 확인했다면 이 재작업(사소했지만)을 피할 수 있었다.

### 6.3 What to Try Next (Try)

- 다음 Design 문서 작성 시, 의사코드에서 "이미 존재한다"고 가정하는 모든 클래스/필드는 실제 소스를 Read로 확인한 후 서술하는 것을 명시적 체크리스트 항목으로 넣는다 — `xcelium-mcp-session-state-reattach`에서 이미 시도하기로 했던 개선("consumer 코드 경로 전수 조사")과 같은 맥락으로, 이번엔 "의존 대상의 실제 인터페이스 확인"으로 구체화.

---

## 7. Process Improvement Suggestions

### 7.1 PDCA Process

| Phase | Current | Improvement Suggestion |
|-------|---------|------------------------|
| Design | 의존 클래스의 필드/인터페이스를 문서 작성자의 기억에 의존 | Design 문서에서 기존 클래스를 참조할 때는 해당 클래스 정의를 직접 Read한 후 서술하는 것을 체크리스트화 |

### 7.2 Tools/Environment

| Area | Improvement Suggestion | Expected Benefit |
|------|------------------------|------------------|
| 실배포 검증 패턴 | 이번에 사용한 "fake 프로토콜 서버 + 실제 운영 파일(백업/diff 대조)" 패턴을 향후 유사한 라이브 검증(예: 다음 ssh-mcp-process-lifecycle)에도 재사용 | pytest 모킹과 실제 배포 사이의 신뢰 격차를 낮은 비용으로 좁힐 수 있음 |

---

## 8. Next Steps

### 8.1 Immediate

- [x] 전체 PDCA 문서 완결(Plan~Report)
- [x] cloud0 crontab 등록 및 라이브 검증
- [ ] Archive 진행 여부 결정

### 8.2 Next PDCA Cycle

| Item | Priority | Expected Start |
|------|----------|-----------------|
| ssh-mcp-process-lifecycle(동일 유형 문제, `ssh_agent.py`) | Medium | `ssh-mcp` 폴더에서 별도 세션으로 진행 예정(사용자 확인됨) |
| Out of Scope 항목(SHM/dump 디스크 사용량 모니터링, 세션 "pin" 기능) | Low | 필요성 재확인 시 별도 Plan |

---

## 9. Changelog

### v1.0.0 (2026-07-07)

**Added:**
- `registry.py`: `touch_activity(sim_dir)`, `last_activity`/`ttl_miss_count` 필드
- `tcl_bridge.py`: `TclBridge(sim_dir=...)` 필드 + `execute_safe()` 활동시각 훅
- `sim_session_reaper.py`(신규): `effective_ttl_seconds`, `sessions_to_reap`, `reap_idle_sessions`, `main`
- `tools/sim_lifecycle.py`: `list_active_sessions` tool
- `deploy/crontab.example` + cloud0 실제 crontab: `sim_session_reaper` 30분 간격 등록
- `skill-src/xcelium-sim/`: Phase 5 §5F, Phase 3 라우팅, SKILL.md 최상단 경고 블록
- `tests/test_sim_session_reaper.py`(신규), 3개 기존 테스트 파일 확장 — 21개 신규 테스트

**Changed:**
- 없음(기존 tool 시그니처 무변경)

**Fixed:**
- Design의 "TclBridge가 sim_dir을 이미 보관한다"는 잘못된 가정을 구현 중 발견해 정정

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-07-07 | 완료 보고서 작성 — Match Rate 100%(T-11 cloud0 라이브 검증 포함), Critical 이슈 0건. | hoseung.lee |
