# xcelium-mcp-f175-provenance-migration-gap Completion Report

> **Status**: Complete
>
> **Project**: xcelium-mcp
> **Author**: Claude Code
> **Completion Date**: 2026-07-08
> **PDCA Cycle**: #1

---

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | xcelium-mcp-f175-provenance-migration-gap |
| Start Date | 2026-07-08 |
| End Date | 2026-07-08 |
| Duration | 단일 세션(Plan→Design→Do→Check→Act) |

### 1.2 Results Summary

```
┌─────────────────────────────────────────────┐
│  Completion Rate: 100%                       │
├─────────────────────────────────────────────┤
│  ✅ Complete:     4 / 4 items (F-1~F-4)      │
│  ⏳ In Progress:   0 / 4 items                │
│  ❌ Cancelled:     0 / 4 items                │
└─────────────────────────────────────────────┘
```

### 1.3 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | F-175(TB provenance) 이전에 discovery된 `version>=2` 프로젝트는 `cached_test_files`/`tb_type`이 영구히 채워지지 않아 `sim_batch_run`/`sim_regression`이 조용히 provenance를 생략 — 안전장치가 가장 필요한 오래된 프로젝트에서 오히려 무력화되는 역설 |
| **Solution** | `test_discovery.schema_version` + 마이그레이션 레지스트리(Option B, `schema_migration.py` 신규) 도입. 실제 hot path(`resolve_test_name`, Do 단계에서 발견된 F-4)와 `list_tests()` 양쪽에 통합, 실패 시 재시도 가능하도록 버전 stamp 로직 보강(Check 단계 G1) |
| **Function/UX Effect** | 기존 프로젝트도 최초 1회 호출만으로 provenance 정상화. 향후 스키마 변경 시 마이그레이션 함수 등록만으로 대응(호출부 수정 불필요). 진단 메시지로 "미마이그레이션"과 "이 테스트만 없음"을 구분 표시 |
| **Core Value** | Match Rate 90%→98%, 593개 테스트 통과. F-175의 "stale TB 사본을 조용히 놓치지 않는다"는 원래 설계 의도가, 그 의도가 가장 필요했던 오래된 프로젝트에서도 실제로 지켜짐 |

---

## 1.4 Success Criteria Final Status

| # | Criteria | Status | Evidence |
|---|---------|:------:|----------|
| SC-1 | 최초 1회 호출만으로 기존 프로젝트도 `tb_source`/`combined_sha256`을 정상 반환 | ✅ Met | `resolve_test_name()`(실제 hot path, F-4) 1회 호출로 마이그레이션+저장 확인(`test_pre_f175_config_gets_migrated_and_resolves_name` 통과). Check 단계 G1(재스캔 실패 시 영구 고착) 수정으로 실패 시에도 다음 호출에서 자동 재시도 보장 |
| SC-2 | 향후 `test_discovery` 스키마가 또 바뀌어도 마이그레이션 함수 등록만으로 대응 | ✅ Met | `TEST_DISCOVERY_MIGRATIONS[n] = fn` 등록만으로 `list_tests()`/`resolve_test_name()` 양쪽 호출부가 자동 적용받음(`schema_migration.py:97-101`), 호출부 수정 불필요 |

**Success Rate**: 2/2 criteria met (100%)

## 1.5 Decision Record Summary

| Source | Decision | Followed? | Outcome |
|--------|----------|:---------:|---------|
| [Plan] | 3안 중 Option B(schema_version + 마이그레이션 레지스트리) 선택 — 필드 존재 체크(A)의 재발 위험과 강제 게이트(C)의 과설계를 피함 | ✅ | 실제 `dict[int, Callable]` 레지스트리 + 정수 `schema_version`으로 구현 확인(gap-detector 검증) |
| [Design] | `schema_migration.py` 신규 모듈 분리, `tools/sim_lifecycle.py`/`tb_provenance.py`/`tools/batch.py`만 수정, `batch_runner.py`는 F-3 확인 후 결정 | ✅ | 계획대로 구현. `batch_runner.py`는 3-tuple 반환 시그니처(기존 테스트 3곳 의존) 보존을 위해 최종적으로 미변경 — 진단은 호출부(`tools/batch.py::sim_regression`)에서 처리 |
| [Do, 세션 중 발견] | F-4: `test_resolution.py::resolve_test_name()`이 실제 hot path이고 독립적 동일 버그 보유 — 범위 확장 필요 | ✅ (사용자 승인) | 동일 마이그레이션 함수로 교체, 순환 임포트는 로컬 import로 회피. 이 발견이 없었다면 보고된 실제 재현 시나리오가 해소되지 않았을 것 |
| [Check, gap-detector] | Match Rate 90%, G1(재스캔 실패 시 영구 고착)/G2(문서 drift) Important 발견 | ✅ (사용자: "지금 모두 수정") | `_MigrationIncomplete` 내부 예외로 G1 수정, Design §3.1/§4.1/§11.1 문서 정정으로 G2/G3 수정. Match Rate ~98%로 개선 |

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [xcelium-mcp-f175-provenance-migration-gap.plan.md](../01-plan/features/xcelium-mcp-f175-provenance-migration-gap.plan.md) (v0.2) | ✅ Finalized |
| Design | [xcelium-mcp-f175-provenance-migration-gap.design.md](../02-design/features/xcelium-mcp-f175-provenance-migration-gap.design.md) (v0.3) | ✅ Finalized |
| Check | [xcelium-mcp-f175-provenance-migration-gap.analysis.md](../03-analysis/xcelium-mcp-f175-provenance-migration-gap.analysis.md) | ✅ Complete |
| Act | Current document | ✅ Complete |

---

## 3. Completed Items

### 3.1 Functional Requirements

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| F-1 | `test_discovery` 스키마 버전 관리 + 마이그레이션 레지스트리(핵심) | ✅ Complete | `schema_migration.py` 신규 — `ensure_test_discovery_current()`, `TEST_DISCOVERY_MIGRATIONS` |
| F-2 | provenance 미가용 사유 진단("미마이그레이션" vs "이 테스트만 없음") | ✅ Complete | `tb_provenance.py::provenance_unavailable_reason()`, `sim_batch_run`/`sim_regression` 양쪽 적용 |
| F-3 | `sim_regression`/`sim_bridge_run` 경로 확인 | ✅ Complete | `batch_runner.py::run_batch_regression()`이 `build_tb_provenance()` 호출함을 확인(L909-910), 진단은 호출부에 적용 |
| F-4 (Do 단계 추가) | `test_resolution.py::resolve_test_name()` 동일 버그 수정 | ✅ Complete | 실제 hot path 진입점 — 사용자 승인 후 범위 확장, 로컬 import로 순환 회피 |
| G1 (Check 단계 추가) | 마이그레이션 재스캔 실패 시 재시도 가능하게 수정 | ✅ Complete | `_MigrationIncomplete` 내부 예외 도입 |
| G2/G3 (Check 단계 추가) | Design 문서 시그니처/파일트리 drift 정정 | ✅ Complete | §3.1/§4.1/§11.1 갱신 |

### 3.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|--------|----------|--------|
| 회귀 테스트 통과 | 전체 통과 | 593 passed, 0 failed | ✅ |
| Lint | ruff clean | All checks passed | ✅ |
| 순환 임포트 없음 | `import xcelium_mcp.server` 성공 | 성공 확인 | ✅ |
| Design Match Rate | ≥90% | 90%→98%(iterate 후) | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Status |
|-------------|----------|--------|
| 마이그레이션 코어 모듈 | `src/xcelium_mcp/schema_migration.py` (신규) | ✅ |
| F-2 진단 헬퍼 | `src/xcelium_mcp/tb_provenance.py::provenance_unavailable_reason` | ✅ |
| 호출부 통합(list_tests) | `src/xcelium_mcp/tools/sim_lifecycle.py::list_tests` | ✅ |
| 호출부 통합(F-4, resolve_test_name) | `src/xcelium_mcp/test_resolution.py::resolve_test_name` | ✅ |
| 호출부 진단(F-2/F-3) | `src/xcelium_mcp/tools/batch.py::{sim_batch_run,sim_regression}` | ✅ |
| 테스트 | `tests/test_schema_migration.py`(신규, 10개), `tests/test_resolve_test_name_cache_miss.py`(재작성, 4개) | ✅ |
| 문서 | `docs/01-plan`, `docs/02-design`, `docs/03-analysis`, `docs/04-report` | ✅ |

---

## 4. Incomplete Items

### 4.1 Carried Over to Next Cycle

| Item | Reason | Priority | Estimated Effort |
|------|--------|----------|------------------|
| T-6: venezia-t0 실제 프로젝트 수동 검증 | 실제 원격 프로젝트 접근이 필요해 이번 세션 범위 밖(Plan/Design에서도 명시적으로 범위 밖) | Medium | 수동 검증 1회 |
| G4(Info): `_migrate_v1` 내 `analyze_tb_type` 호출부 예외 미포장 | gap-detector가 Info(신뢰도 60%)로 분류, 사용자가 이번 iterate 범위를 G1+G2로 한정 | Low | 30분 미만 |

### 4.2 Cancelled/On Hold Items

| Item | Reason | Alternative |
|------|--------|-------------|
| - | - | - |

---

## 5. Quality Metrics

### 5.1 Final Analysis Results

| Metric | Target | Final | Change |
|--------|--------|-------|--------|
| Design Match Rate | 90% | ~98%(추정, Structural 100/Functional ~96/Contract ~98) | +8%p (iterate 전 90%→98%) |
| 회귀 테스트 | 전체 통과 | 593 passed, 0 failed | +9 tests (구현 전 592, 최종 593) |
| Critical Gaps | 0 | 0 | ✅ |
| Important Gaps | - | 0 (G1/G2 모두 해소) | 2→0 |

### 5.2 Resolved Issues

| Issue | Resolution | Result |
|-------|------------|--------|
| F-175 마이그레이션 갭(핵심 버그) | schema_version + 마이그레이션 레지스트리(Option B) | ✅ Resolved |
| `resolve_test_name`의 독립적 동일 버그(F-4, Do 단계 발견) | 동일 마이그레이션 함수로 교체 | ✅ Resolved |
| 재스캔 실패 시 영구 고착(G1, Check 단계 발견) | `_MigrationIncomplete`로 재시도 가능하게 수정 | ✅ Resolved |
| Design 문서 시그니처 drift(G2) | §3.1/§4.1 실제 코드에 맞게 정정 | ✅ Resolved |

---

## 6. Lessons Learned & Retrospective

### 6.1 What Went Well (Keep)

- Design 단계에서 3가지 아키텍처 옵션(A/B/C)을 명시적으로 비교·기록해둔 덕분에, Do 단계에서 범위가 확장(F-4)됐을 때도 "왜 Option B인지"가 흔들리지 않고 일관되게 적용됨
- Check 단계에서 gap-detector를 실제로 돌려 "설계 의도(재시도)와 코드가 다르다"(G1)는 실제 버그를 잡아냄 — 문서만 비교하는 걸로는 놓쳤을 문제
- 큰 변경 전 사용자 승인을 구하는 습관(F-4 범위 확장 시 AskUserQuestion) 덕분에 scope creep 없이 필요한 확장만 정확히 진행됨

### 6.2 What Needs Improvement (Problem)

- Plan/Design 작성 시점에 "실제로 어느 함수가 hot path인지"를 코드로 먼저 확인하지 않고 문서(이전 세션 기록)의 함수명만 믿었던 것이 F-4를 뒤늦게 발견하게 만든 원인 — Design 단계에서 호출 그래프를 한 번 더 추적했다면 처음부터 스코프에 포함됐을 것
- 마이그레이션 로직을 처음 설계할 때 "실패 시 어떻게 되는가"를 해피패스만큼 구체적으로 설계하지 않아 G1이 Check 단계까지 남아있었음

### 6.3 What to Try Next (Try)

- 버저닝된 마이그레이션 패턴을 도입할 때는 처음부터 "성공/부분실패/완전실패" 3가지 케이스를 표로 먼저 정의하고 시작하기
- Plan/Design 단계에서 "이 버그를 실제로 재현하는 호출 경로"를 `grep`으로 한 번 확인하는 것을 체크리스트 항목으로 추가

---

## 7. Process Improvement Suggestions

### 7.1 PDCA Process

| Phase | Current | Improvement Suggestion |
|-------|---------|------------------------|
| Plan/Design | 실제 호출부를 코드로 확인하지 않고 이전 조사 결과 문서에 의존 | Design 체크포인트에 "영향받는 모든 호출부를 grep으로 재확인" 단계 추가 |
| Check | gap-detector가 설계-구현 불일치를 잘 잡음 | 유지 — 특히 "에러 처리 절 vs 실제 코드" 대조가 이번에 유효했음 |

### 7.2 Tools/Environment

| Area | Improvement Suggestion | Expected Benefit |
|------|------------------------|------------------|
| 테스트 | mock 기반 유닛테스트로 재시도 로직까지 커버 가능함을 확인 — 이 패턴을 다른 캐시/마이그레이션 로직에도 적용 | 유사 버그(G1 클래스) 조기 발견 |

---

## 8. Next Steps

### 8.1 Immediate

- [ ] `/pdca archive xcelium-mcp-f175-provenance-migration-gap` — 문서 아카이브
- [ ] venezia-fpga 세션에서 T-6(실제 venezia-t0 프로젝트로 수동 검증) 진행

### 8.2 Next PDCA Cycle

| Item | Priority | Expected Start |
|------|----------|----------------|
| G4(Info): `analyze_tb_type` 예외 포장 | Low | 필요 시 |
| venezia-fpga `verilog-rtl-debugger.plan.md` FR-10 / `verilog-tb-analyst.plan.md` FR-14 재검증 | Medium | 이 기능이 배포된 후 |

---

## 9. Changelog

### v1.0.0 (2026-07-08)

**Added:**
- `src/xcelium_mcp/schema_migration.py` — `test_discovery` 스키마 버전 관리 + 마이그레이션 레지스트리
- `tb_provenance.py::provenance_unavailable_reason()` — provenance 미가용 사유 진단
- `tests/test_schema_migration.py` (10개 테스트)

**Changed:**
- `tools/sim_lifecycle.py::list_tests()`, `test_resolution.py::resolve_test_name()` — `ensure_test_discovery_current()` 호출로 백필 로직 교체
- `tools/batch.py::{sim_batch_run,sim_regression}` — provenance 미가용 시 진단 메시지 추가
- `tests/test_resolve_test_name_cache_miss.py` — F-4 통합 검증으로 재작성

**Fixed:**
- F-175 이전 discovery된 프로젝트의 TB provenance 영구 누락 버그(핵심)
- `resolve_test_name()`의 독립적 동일 버그(F-4)
- 마이그레이션 재스캔 실패 시 영구 고착 버그(G1)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-07-08 | Completion report 작성 | Claude Code |
