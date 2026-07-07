# Analysis: xcelium-mcp-sim-session-reaper (F-1/F-2/F-3)

> **Summary**: Design v0.1(Option C) 대비 구현 검증. T-1~T-12 전부 Met — T-11(cloud0 실배포 검증)은
> 실제 fake bridge 프로세스 + 실제 레지스트리 파일로 라이브 검증 완료(§8).
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Planning Doc**: [xcelium-mcp-sim-session-reaper.plan.md](../01-plan/features/xcelium-mcp-sim-session-reaper.plan.md)
> **Design Doc**: [xcelium-mcp-sim-session-reaper.design.md](../02-design/features/xcelium-mcp-sim-session-reaper.design.md)

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 실제 발생한 host disk 소진 사고(검증 완료 후 수 주 방치된 xmsim)의 재발 방지 |
| **WHO** | xcelium-mcp를 bridge 모드로 사용하는 모든 사용자/agent(`verilog-rtl-debugger` 포함) |
| **RISK** | TTL을 너무 짧게 잡으면 정말 장시간 필요한 세션을 실수로 죽일 위험 |
| **SUCCESS** | 방치된 bridge 세션이 TTL 경과 후 자동으로 안전 종료됨을 실증 |
| **SCOPE** | 레지스트리 활동시각 기록 + cron reaper(자동 종료) + 가시성 tool |

---

## 1. Strategic Alignment Check

| 질문 | 판정 | 근거 |
|---|---|---|
| Plan의 핵심 문제(방치된 xmsim의 disk 소진)를 해결했는가? | ✅ Met | §2 참조 — TTL 기반 자동 종료가 사람/AI의 판단에 의존하지 않음 |
| Design의 핵심 결정(Option C — 기존 F-C/F-D/F-B 패턴 재사용, `MIN_MISS_COUNT_TO_KILL=2` 안전장치)이 그대로 따라졌는가? | ✅ Met | §3 참조 |
| F-1(skill 체크리스트)이 실제로 배포되어 있는가? | ✅ Met | `~/.claude/skills/xcelium-sim/`에 재배포 완료(이전 세션에서 확인) |

---

## 2. Plan/Design Success Criteria (T-1~T-12) 평가

| # | 기준 | 상태 | 근거 |
|---|------|:---:|------|
| T-1 | bridge 명령 실행 시 `last_activity` 갱신 | ✅ Met | `test_touch_activity_records_last_activity_and_resets_miss_count` |
| T-2 | throttle 윈도우 내 재호출 시 갱신 스킵 | ✅ Met | `test_touch_activity_throttles_rapid_successive_calls` |
| T-3 | TTL 초과 + 연속 2회 도달 시 `__SHUTDOWN__` + 레지스트리 정리 | ✅ Met | `test_ttl_exceeded_twice_marks_for_reap`, `test_reap_idle_sessions_shuts_down_and_cleans_registry` |
| T-4 | TTL 초과 1회차(2회 미달) → 종료 안 함, miss_count만 증가 | ✅ Met | `test_ttl_exceeded_first_time_not_yet_reaped` |
| T-5 | TTL 미초과 → miss_count 리셋, 아무 것도 안 함 | ✅ Met | `test_ttl_not_exceeded_resets_miss_count`, `test_reap_idle_sessions_no_action_when_within_ttl` |
| T-6 | `last_activity` 없는 레거시 엔트리 → 건드리지 않음 | ✅ Met | `test_legacy_entry_without_last_activity_is_skipped` |
| T-7 | 포트가 이미 죽은 고아 엔트리 → 크래시 없이 정리 | ✅ Met | `test_reap_idle_sessions_handles_orphan_port_gracefully` |
| T-8 | `bridge_port` 없는(batch/regression) 엔트리 → 완전히 무시 | ✅ Met | `test_non_bridge_entry_without_port_is_skipped` |
| T-9 | `list_active_sessions`가 세션 목록 + TTL 잔여시간 정확히 반환 | ✅ Met | `test_list_active_sessions_reports_ttl_remaining`, `test_list_active_sessions_flags_ttl_exceeded`, `test_list_active_sessions_skips_batch_only_entries` |
| T-10 | TTL 환경변수 미설정/잘못된 값 → 기본값(48h) 폴백 | ✅ Met | `test_effective_ttl_seconds_default`, `test_effective_ttl_seconds_invalid_value_falls_back` |
| T-11 | 실배포: cloud0 cron 등록 + 실제 방치 세션 자동 종료 확인 | ✅ Met | §8 라이브 검증 — 실제 registry 파일 + fake bridge 프로세스로 재현 |
| T-12 | 회귀 | ✅ Met | pytest 584 passed(부모 기능 563 + 신규 21), ruff 클린 |

**Met 12/12 — Partial 없음.**

---

## 3. Structural Match — Design §8.1 vs 실제

| 파일 | Design 계획 | 구현 | 일치 |
|---|---|---|:---:|
| `registry.py` | `last_activity`/`ttl_miss_count` 필드 + `touch_activity()` | 동일 — `_ACTIVITY_THROTTLE_SEC=60`, `OSError` 삼킴(F-D 관례 일치) | ✅ |
| `tcl_bridge.py` | `execute()`/`execute_safe()` 진입부에 활동시각 갱신 훅 | `execute_safe()`에 훅 배치(`execute()`는 내부적으로 `execute_safe()` 호출하므로 중복 없음). `sim_dir` 필드 신규 추가(Design이 "이미 보관하고 있다"고 가정한 부분은 실제로는 없어서, `TclBridge.__init__`에 새로 추가 — 구조 변경 없이 필드 추가로 해결) | ✅(구현 중 발견한 정정 포함) |
| `sim_session_reaper.py` | TTL 순회 + `__SHUTDOWN__` 전송 + 레지스트리 정리, idle_culler.py와 동일 패턴 | 동일 — `sessions_to_reap()`(순수 함수, unit-testable)와 `reap_idle_sessions()`(I/O)를 분리해 idle_culler.py의 "순수 로직/실제 I/O 분리" 관례를 그대로 따름 | ✅ |
| `tools/sim_lifecycle.py` | `list_active_sessions()` 신규 tool | 동일 — 읽기 전용, TTL 잔여시간 계산 포함 | ✅ |
| `deploy/crontab.example` | reaper cron 항목 추가 | 추가됨(30분 간격, TTL override 안내 주석 포함) | ✅ |

**Structural Match: 5/5.**

---

## 4. Functional Depth

- `registry.touch_activity()`: 스로틀링·미스카운트 리셋·sibling field 보존 모두 구현, placeholder 없음.
- `sim_session_reaper.sessions_to_reap()`: Design 의사코드와 거의 동일, 레거시/고아/batch 엔트리 3가지 예외 케이스 모두 명시적으로 처리.
- `list_active_sessions()`: TTL 잔여시간을 사람이 읽을 수 있는 형식(시간 단위)으로 변환, batch 전용 엔트리 자동 제외.
- Design §4.1이 "TclBridge 인스턴스가 이미 sim_dir을 보관하고 있다"고 잘못 가정했던 부분을 구현 중 발견해 정정(§3 참조) — 설계와 실제 코드베이스 사이의 사소한 불일치를 구현 단계에서 스스로 교정한 사례.

**Functional Depth: 결손 없음.**

---

## 5. Decision Record Verification

| 결정(Plan→Design) | 구현에서 지켜졌는가 |
|---|---|
| Option C — 기존 F-C/F-D/F-B 패턴 재사용, 신규 파일 최소화(1개) | ✅ `sim_session_reaper.py` 1개만 신규 |
| `MIN_MISS_COUNT_TO_KILL=2` 안전장치 | ✅ `sessions_to_reap()`에 그대로 구현, 회귀 테스트로 검증(T-3/T-4) |
| batch/regression과 완전 분리(F-E와 간섭 없음) | ✅ `bridge_port` 필드 유무로 자동 분리, `test_non_bridge_entry_without_port_is_skipped`로 확인 |
| SKILL.md 최상단 독립 경고 블록(F-1 보강) | ✅ 이전 세션에서 이미 배포 완료, 이번 세션은 코드(F-2/F-3)만 다룸 |

**이탈 없음.**

---

## 6. Match Rate

| 축 | 점수 | 근거 |
|---|:---:|---|
| Structural | 100% | 5/5 파일 계획대로 |
| Functional | 100% | placeholder 없음, 설계 오류 1건 구현 중 자체 정정 |
| Contract | 100% | 기존 MCP tool 시그니처 무변경(`list_active_sessions`는 신규 추가일 뿐 기존 tool 영향 없음), `TclBridge(sim_dir="")` 기본값으로 하위호환 |
| Runtime(pytest+실측) | 100% (12/12) | T-11 라이브 검증 완료(§8) |

**Overall Match Rate ≈ 100%**

---

## 7. Checkpoint 5 — Review Decision 대상 이슈

없음 — Critical/Important 이슈 0건.

---

## 8. T-11 라이브 검증 상세 (cloud0)

실제 xmsim/Cadence 라이선스 세션 없이도 reaper의 실제 동작(레지스트리 파일 읽기/쓰기, TCP 접속,
`__SHUTDOWN__` 전송, 연속 2회 확인 안전장치)을 검증하기 위해, mcp_bridge.tcl 프로토콜을 흉내내는
독립 fake bridge 프로세스를 cloud0에 실행해 실제 `mcp_registry.json`(운영 중인 venezia-t0/alaska-c1
엔트리가 실제로 들어있는 파일)에 임시 테스트 엔트리를 추가하는 방식으로 진행했다(작업 전 백업,
작업 후 실제 프로젝트 엔트리가 바이트 단위로 보존됐음을 diff로 확인, 테스트 엔트리는 종료 후 제거).

**절차 및 결과**:
1. `/tmp/fake_bridge_server.py`(asyncio TCP 서버, `__SHUTDOWN__` 수신 시 마커 파일 생성) 실행 — 포트 40021 확보.
2. 실제 레지스트리에 `bridge_port=40021`, `last_activity`를 현재 시각보다 훨씬 과거로 설정한 테스트 엔트리 주입.
3. `python3 -m xcelium_mcp.sim_session_reaper` 1회차 실행 → `ttl_miss_count: 0 → 1`, `__SHUTDOWN__` 미전송(마커 파일 없음) — **T-4 안전장치가 실제 CLI 실행에서도 그대로 동작함을 확인.**
4. 2회차 실행 → 마커 파일 생성 확인(`__SHUTDOWN__`이 실제로 전송·수신됨), 레지스트리 테스트 엔트리 제거됨 — **T-3 end-to-end 동작 확인.**
5. 정리: fake bridge 프로세스 종료, 테스트 엔트리 삭제, 백업과 대조해 `venezia-t0`/`alaska-c1` 실제 엔트리 무변경 확인(`backup == current` 검증 스크립트로 확인).
6. cloud0 실제 crontab에 `*/30 * * * * python3 -m xcelium_mcp.sim_session_reaper` 등록 완료(사용자 승인 후 진행) — 기존 supervisor/idle_culler 항목은 무변경.

이로써 T-11이 요구하는 "실제 방치 세션이 자동 종료됨"을 pytest 모킹이 아닌 실제 프로세스·실제 파일로 재현했다.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Design v0.1(Option C) 대비 구현 검증. Match Rate 97%(T-11 실배포 검증만 Partial, 나머지 11/12 Met). | hoseung.lee |
| 0.2 | 2026-07-07 | T-11 cloud0 라이브 검증 완료(§8) — fake bridge 프로세스 + 실제 레지스트리로 재현, cron 등록. Match Rate 97%→100%. | hoseung.lee |
