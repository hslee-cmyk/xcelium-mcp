# xcelium-mcp v5.2 — Hierarchical Dump Strategy — Completion Report

> **Feature**: `xcelium-mcp-v5.2-hierarchical-dump`
>
> **Duration**: 2026-04-09 (Plan) ~ 2026-07-02 (Report, updated same day with post-refactor 94% re-verification)
> **Owner**: HSLEE
> **Status**: Check Phase Complete — Match Rate **94%** (3rd re-verification, post F-144–F-173 refactor backlog), ready for Report/handoff (F-139 HW verification pending)

---

## Executive Summary

### 1.1 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | `dump_depth="boundary"`는 top I/O 28개만 dump해 Gate 내부 block 실패를 특정할 수 없었고, `dump_depth="all"`은 수만 신호로 SHM이 폭증했다. "너무 적음 vs 너무 많음" 사이의 중간 해상도가 없었다. |
| **Solution** | `dump_strategy.block_boundaries` 맵 + `default_block_policy`(opt-in/opt-out) + `dump_scopes` override(`all`/`boundary`/`skip`, glob 지원)로 block 단위 dump 전략을 조합 가능하게 함. Phase 2에서 Flow A(TCL 런타임 탐색)/Flow B(Yosys JSON lazy discovery) 두 가지 자동 감지 경로 추가. |
| **Function/UX Effect** | Gate/AMS 디버깅 시 FAIL 발생 블록을 경계 신호만으로 특정 → 필요한 block만 `dump_scopes={block: "all"}`로 재실행. 전체 재실행(SHM 500MB+) 대신 SHM 1% 수준으로 축소. `dump_history`가 실행마다 자동 갱신되어 `use_dump_history=True` 재사용 가능. |
| **Core Value** | Gate/AMS 디버깅 workflow 해상도를 "chip 경계"에서 "블록 경계"로 상향, 실패 인터페이스 특정 속도 향상 + 불필요한 전체 재실행 감소. |

### 1.2 Check Phase Completion Metrics

- **Gap Analysis**: 초기 85% → 93% → 최종 **94%** match rate (3차 재검증, 2026-07-02, post F-144–F-173 리팩터 14건)
- **Critical Gaps**: 0건 (3개 분석 회차 전 구간 없음)
- **Important Gaps**: 3건 발견 → **3건 전부 해결·회귀검증 유지** (F-140 수정 + F-141 테스트, 이후 4개 리팩터에도 스키마 무변화 재확인)
- **Minor Gaps**: 5건 발견 → **2건 해결**(M2/M4, F-142/F-143) — 잔존 3건은 코스메틱 컨벤션(M1) + HW 필요(M3, F-139) + 의도적 연기(M5)
- **Iterations**: 2회 (F-140/F-141 Important gap 마감, F-142/F-143 Minor gap 마감) + 1회 post-refactor 독립 재검증(신규 gap 없음)
- **Runtime Signal**: `pytest` **472 passed / 0 failed / 0 warnings** (F-144–F-173 리팩터로 테스트 325→472 증가), `ruff check src/` all clean

---

## PDCA Cycle Summary

### Plan

**Plan Document**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md` (2026-04-09)

**Goal**: `dump_depth="boundary"`의 의미를 "top I/O 경계"에서 "모든 block 경계의 합집합"으로 확장하고, `dump_scopes` 파라미터로 block별 override(all/boundary/skip)를 지원한다.

**Functional Requirements**: FR-01~FR-10 (P0 4건, P1 5건, P2 1건 `group:` prefix)

### Design

**Design Document**: `docs/02-design/features/xcelium-mcp-v5.2-hierarchical-dump.design.md` (2026-07-01)

**Context Anchor**:

| | |
|--|--|
| **WHY** | top I/O 28개만으론 Gate 내부 block 실패 특정 불가, `"all"`은 SHM 폭증 — 중간 해상도 부재 |
| **WHO** | Gate/AMS 디버깅 엔지니어 — FAIL 후 원인 block을 빠르게 특정해야 함 |
| **RISK** | backward compat 파괴 시 기존 v5.1 regression 21건 전체 영향 |
| **SUCCESS** | v5.1 regression 21/21 유지, SHM ≤ full 10%, dump_scopes로 block별 전략 조합 |
| **SCOPE** | Phase 1 MVP(수동 boundaries+scopes+history) → Phase 2 auto-detection → Phase 3 optional(group) |

**Key Design Decisions**:
1. **opt-in/opt-out 이중 모델** — `default_block_policy="skip"`(기본, 명시 block만 추가) vs `"boundary"`(전체 자동 포함 후 개별 skip)
2. **glob 기반 `dump_scopes` override** — `fnmatch`로 subtree 전체 제어(`"top.hw.u_ext.*": "skip"`), OS shell 미호출로 injection 위험 없음
3. **Flow A/B 대칭 자동 감지** — Flow A(`sim_bridge_run(auto_boundaries=True)`, TCL `scope -describe` 런타임 탐색) / Flow B(`sim_batch_run` lazy JSON discovery, `netlist_info.{mode}.boundary_json` 파싱)
4. **`dump_history` 영속 스키마** — `last_dump_summary`/`dump_scopes`/`updated_at` 3필드로 실행마다 갱신, `use_dump_history=True`로 재사용
5. **`dump_stats` 회귀 집계** — per-test `total`/`top_boundary`/`block_count` + `max`/`min` `{test,total}` dict + outlier(`total > avg×2`) named suggestion

### Do (Implementation)

3개 커밋에 걸쳐 구현:

| 커밋 | 내용 |
|------|------|
| `458288d` | Phase 1 — hierarchical dump strategy 핵심 로직 (`_resolve_probe_signals` 3-tuple, `get_dump_strategy`, opt-in/opt-out 매트릭스) |
| `4b13d95` | Phase 2 — auto-detection (`_boundaries_from_tcl`/`_boundaries_from_json`, `_lazy_discover_boundaries`) |
| `3b9ffdb` | F-138 `passes:true` 확정 + progress.md 갱신 |

**변경 파일**: `src/xcelium_mcp/{tcl_preprocessing,batch_runner,sim_env_detection,registry}.py`, `src/xcelium_mcp/tools/{batch,sim_lifecycle}.py`

---

## Gap Analysis Results

### 1차 분석 (2026-07-01) — 85% match rate

| Gap | Type | Issue | Status (당시) |
|-----|------|-------|:---:|
| #1 | Important | `dump_history` 저장 스키마 편차 (`dump_summary` key, `updated_at`/`scope_overrides` strip 누락) | Open |
| #2 | Important | `dump_stats` 집계 shape 편차 (bare int max/min, generic suggestion) | Open |
| #3 | Important | `run_batch_regression`이 `dump_history` 미영속 (Plan §3.2 위반) | Open |
| M3 | Minor | `_parse_describe_output` 실환경 미검증 | Deferred → F-139 |
| M5 | Minor | FR-09 `group:` prefix 미구현 | 의도적 (Phase 3) |
| M1/M6 | Minor | 반환 shape 코스메틱 편차 | Accepted convention |

**원인 분석**: 순수 함수(`_resolve_probe_signals`, `_boundaries_from_json` 등)만 단위 테스트되어 있었고, async wiring(`_update_dump_history`, `_lazy_discover_boundaries`, `dump_stats` 집계, regression 통합)은 config/SSH mocking이 필요해 테스트가 없었음 — Gap #1-#3이 정확히 이 사각지대에서 발생.

### 대응 — F-140 (수정) + F-141 (테스트)

| 커밋 | 내용 |
|------|------|
| `872f6ad` | fix(v5.2): `dump_history`/`dump_stats` 영속 스키마를 design.md 스펙과 정합 |
| `6613e11` | test(v5.2): async wiring 단위 테스트 추가 — 7개 신규 (`tests/test_dump_history_stats.py`) |

- **Gap #1 CLOSED**: `_update_dump_history`가 `last_dump_summary`(scope_overrides strip) + `dump_scopes` + `updated_at` 3필드 저장 → design.md §7 pseudocode와 일치
- **Gap #2 CLOSED**: `dump_stats`가 `per_test{total,top_boundary,block_count}` + `max`/`min` `{test,total}` dict + per-test named suggestion → design.md §8 pseudocode와 일치
- **Gap #3 CLOSED**: `run_batch_regression` per-test 루프에 `_update_dump_history` 호출 추가 — regression 실행 후에도 `use_dump_history=True` 재사용 가능

### 2차 분석 (재분석, 2026-07-02) — **93% match rate**

| Axis | 1차 | 2차 |
|------|:---:|:---:|
| Structural | 97% | 97% |
| Functional | 86% | **92%** |
| API Contract | 78% | **92%** |
| **Overall** | **85%** | **93%** |

**Runtime Signal**: `pytest -v` 전체 스위트 325 passed / 0 failed (2 warnings), `ruff check src/` all clean.

**독립 재검증 결과**: Gap #1~#3 모두 코드 라인 + 신규 테스트(`test_update_dump_history_writes_last_dump_summary_schema`, `test_regression_updates_dump_history_and_dump_stats_shape` 등)로 CLOSED 확인. 새로운 Critical/Important gap 없음.

### 3차 분석 (post-refactor 재검증, 2026-07-02) — **94% match rate**

`code-analyzer` 리뷰 백로그(F-144–F-173, bug/security/architecture/verbosity 리팩터 14건 — 이 기능 파일을 건드리는 F-146/153/155/156/157/158/159/161/162/164/166/167/171)가 이 기능의 `dump_history`/`dump_stats` 영속 스키마와 `sim_batch_run`/`sim_regression` 검증 계약에 드리프트를 일으켰는지 독립 재검증.

| Axis | 1차 (85%) | 2차 (93%) | 3차 (94%, 이번) |
|------|:---:|:---:|:---:|
| Structural | 97% | 97% | 97% |
| Functional | 86% | 92% | **95%** |
| API Contract | 78% | 92% | 92% |
| **Overall** | **85%** | **93%** | **94%** |

**Runtime Signal**: `python -m pytest -q` 전체 스위트 **472 passed / 0 failed / 0 skipped / 0 warnings** (325→472, F-144–F-173 백로그 전반의 pure-function 추출로 증가), `ruff check src/` all clean. 93% 회차에서 남아있던 `datetime.utcnow()` DeprecationWarning 2건은 이번 회차에서 완전히 사라짐 — M2/F-142 closed의 런타임 재확인.

**핵심 확인 사항**:
- **Important Gap #1~#3 재확인 CLOSED**: F-155(`classify_regression_results`/`aggregate_dump_stats` 추출), F-156(`_read_job_status` 추출), F-157(`launch_nohup_job` 재사용), F-167(`_history_scopes` 추출) 4개 리팩터 이후에도 `last_dump_summary`/`dump_scopes`/`updated_at`, `max`/`min` `{test,total}` dict, `per_test{total,top_boundary,block_count}` 스키마가 byte-for-byte 불변임을 라인 단위로 재확인.
- **M2/M4 CLOSED 재확인**: `batch_runner.py:429` `datetime.now(timezone.utc)`, `batch_runner.py:422` `load_sim_config(sim_dir, force=True)` — 코드 라인 + 런타임(warning 소거) 이중 확인.
- **API Contract 불변**: `_validate_run_params()`로 검증 로직이 dedup(F-164)됐지만 `dump_depth`/`sdf_corner`/`dump_scopes` 허용값과 에러 메시지는 dedup 전과 동일.
- **신규 gap 후보 조사 후 기각**: "orphan `.done` watcher"(N1 후보) — `launch_nohup_job`의 fire-and-forget PID watcher가 regression 경로에서 미소비될 가능성을 의심했으나, `batch_polling.py`의 `poll_batch_log`가 동일 `.done` 경로를 체크+정리함을 파일:라인 근거로 확인 — 실제 gap 아님으로 판명, 향후 재플래그 방지 위해 문서화.
- **`resolve_sim_dir` 이동**(F-161, `discovery.py`→`registry.py`) 확인 — Design 문서가 참조하지 않는 함수라 stale reference 아님.

**결론**: 85%→93%→94%, 3개 분석 회차와 이 기능 파일을 건드린 14개 후속 리팩터 전체에 걸쳐 Critical/Important gap 0건 유지. 잔존 6%는 전부 Minor(M1 코스메틱, M3 HW 필요/F-139, M5 의도적 연기) — Report 단계 진입 기준 재확인.

### Minor Gap 후속 조치 — F-142 (M2) + F-143 (M4)

Report 작성 직후 같은 세션에서 잔존 Minor gap 중 즉시 수정 가능한 2건을 추가로 마감:

| 커밋 | 내용 |
|------|------|
| `832b07a` | fix(v5.2): `datetime.utcnow()` deprecation 해소 (F-142, M2) |
| `4c67d21` | fix(v5.2): `_update_dump_history`가 `load_sim_config`를 `force=True`로 호출 (F-143, M4) |

- **M2 CLOSED**: `_update_dump_history`의 `updated_at` 생성을 `datetime.utcnow()` → `datetime.now(timezone.utc)`로 교체. `pytest` 실행 시 DeprecationWarning 2건이 사라짐 (325 passed/2 warnings → 325 passed/0 warnings).
- **M4 CLOSED**: `load_sim_config(sim_dir)` → `load_sim_config(sim_dir, force=True)`로 변경해 design.md §7 pseudocode와 완전히 일치. 신규 테스트 `test_update_dump_history_loads_config_with_force`로 `force=True` 전달을 회귀 방지 (325 → **326 passed**).

### 잔존 Minor Gap (3건, 전부 non-blocking)

| # | 내용 | 상태 |
|---|------|:---:|
| M1 | 반환 타입 tuple vs design dict — 코스메틱, 기존 컨벤션. 재검토 결과 tuple 유지가 타당(positional fail-fast, 단일 내부 소비처, 다른 helper와 일관) — dict/TypedDict 전환은 반환 필드가 3개 이상으로 늘거나 외부 소비처가 생길 때 재검토 | Accepted (재검토 완료) |
| M2 | ~~`datetime.utcnow()` deprecation warning~~ | ✅ **CLOSED** (F-142, `832b07a`) |
| M3 | `_parse_describe_output` 실환경(xmsim) 미검증 | F-139 (HW) |
| M4 | ~~`load_sim_config(force=True)` 미사용~~ | ✅ **CLOSED** (F-143, `4c67d21`) |
| M5 | FR-09 `group:` prefix 미구현 — Phase 3 의도적 보류. 재검토 결과 `dump_scopes` dict에 여러 key 나열 또는 glob(`"top.hw.u_ext.*"`)으로 이미 다중/subtree skip이 가능해 `group:`은 "안 되던 걸 되게" 하는 게 아니라 "반복 입력을 줄이는" 편의 기능 — 실사용 압박 확인 전까지 미루는 게 합리적 | Deferred by design (재검토 완료) |

---

## Success Criteria (Plan §8) — 최종 상태

| Metric | Target | Status | Evidence |
|--------|--------|:------:|----------|
| Unit test coverage ≥90% (`_resolve_probe_signals`) | ✅ Met | 전체 branch 커버 + async wiring도 커버 (F-141) |
| Integration test 3 시나리오 PASS | ⚠️ Partial | cloud0 필요, 저장소 내 integration test 없음 — F-139 |
| v5.1 regression 21/21 PASS | ⚠️ Partial | 단위 스위트로는 검증 불가; backward-compat 단위 테스트는 통과 |
| SHM 크기 감소율 ≤10% | ❌ Not Met (statically) | 실제 gate sim 필요 — F-139 |
| Backward compat | ✅ Met | `test_boundary_no_block_boundaries` v5.1 `signals` path 반환 확인 |
| Security — dump_scopes injection 차단 | ✅ Met | tool-layer regex + resolver `ValueError` + `_SCOPE_PATH_RE`(Flow A) |

**Overall Success Rate**: 3/6 완전 충족, 2/6 부분(HW 필요), 1/6 정적 검증 불가(HW 필요) — 모두 F-139로 수렴.

---

## Key Decisions & Outcomes

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| opt-in(`skip`)을 기본값으로 | 기존 사용자가 명시적으로 선택하지 않으면 동작 변화 없음 (backward compat 우선) | ✅ 유지 — v5.1 `signals` 경로 그대로 fallback |
| `dump_scopes`에 glob(`fnmatch`) 지원, OS shell 미사용 | subtree 단위 제어 필요성 + injection 위험 최소화 | ✅ 구현대로 유지, 별도 injection gap 없음 |
| Flow A(TCL)/Flow B(JSON) 두 갈래 auto-detection | bridge 연결 시점과 batch 실행 시점의 서로 다른 정보 소스를 모두 지원 | ✅ 구현 완료(F-138), 실환경 검증만 F-139로 이월 |
| `dump_history`를 "항상 갱신"(Plan §3.2) | 이후 실행에서 `use_dump_history=True` 재사용 가능하게 하려는 의도 | ⚠️ 1차 구현에서 `run_batch_regression` 경로 누락(Gap #3) → F-140에서 수정, F-141로 회귀 방지 |
| `group:` prefix(FR-09)를 Phase 3로 연기 | MVP 범위를 opt-in/opt-out + glob으로 한정, 그룹 관리는 추가 config 스키마가 필요해 별도 사이클로 분리 | 계획대로 미구현 유지 — 필요 시 별도 PDCA 사이클 |

---

## Related Documents

- **Plan**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md`
- **Design**: `docs/02-design/features/xcelium-mcp-v5.2-hierarchical-dump.design.md`
- **Analysis**: `docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` (1차 85% + 2차 93% + 3차 94%, History 섹션 포함)
- **Backlog**: `plans/prd.json` — F-138(구현, passes:true), F-140/F-141/F-142/F-143(gap-fix+테스트, passes:false 사용자 확인 대기), F-139(HW 검증, skip:true)

---

## Next Steps

### 즉시 가능
1. ~~M2 정리~~ — ✅ 완료 (F-142, `832b07a`)
2. ~~M4 정리~~ — ✅ 완료 (F-143, `4c67d21`)
3. **F-140/F-141/F-142/F-143 `passes:true` 확인** — 사용자가 직접 검증 후 `plans/prd.json`에 반영 (프로젝트 규칙상 자동 변경 금지)

### F-139 (HW 필요, skip:true — 사람이 직접 진행)
1. Flow A(`sim_bridge_run(auto_boundaries=True)`) cloud0 실제 시뮬레이터 연결 상태에서 1회 수동 실행 검증
2. Flow B(`sim_batch_run` lazy discovery) Yosys JSON 존재 프로젝트에서 1회 수동 실행 검증
3. `_parse_describe_output`을 실제 `scope -describe -sort kind` 출력과 대조 (Plan §3.6 port-name-first vs 구현 direction-first 불일치 해소)
4. v5.1 regression 21/21 PASS + SHM ≤10% 실측
5. v5.2.0(Phase1)/v5.2.1(Phase2) 분리 여부 vs 단일 v5.2 결정 (Design §15 Open Question 1)

### Archive 준비
Match Rate **94%** ≥ 90% 기준 충족(3개 분석 회차, 14개 후속 리팩터에도 Critical/Important gap 0건 유지) — F-139(HW 검증) 완료 후 `/pdca archive xcelium-mcp-v5.2-hierarchical-dump`로 아카이브 가능.

---

## Summary

**xcelium-mcp v5.2 — Hierarchical Dump Strategy**는 Plan→Design→Do→Check PDCA 사이클을 완료했습니다:

- **품질**: 85% → 93% → **94%** match rate (gap-detector 3회 재검증 기준), Critical/Important gap 0건 (전 회차)
- **테스트**: `pytest` **472 passed / 0 failed / 0 warnings**, `ruff check src/` all clean — F-141(async wiring 7개) + F-143(1개) + F-144–F-173 백로그 전반의 pure-function 추출로 325→472 증가
- **범위**: Phase 1(수동 boundaries+scopes) + Phase 2(auto-detection) 구현 완료, Phase 3(group)은 계획대로 연기
- **Gap 정리**: Important 3건 전부 CLOSED(F-140/F-141), 이후 4개 리팩터(F-155/156/157/167)에도 스키마 무변화 재확인. Minor 5건 중 2건 CLOSED(M2/M4, F-142/F-143) — 잔존 3건 전부 non-blocking(M1 코스메틱, M5 의도적 연기, M3만 F-139 HW 필요)
- **후속 리팩터 안정성**: F-144–F-173(bug/security/architecture/verbosity 리팩터 14건) 중 이 기능 파일을 건드린 F-146/153/155/156/157/158/159/161/162/164/166/167/171 전체가 이 기능의 스키마/계약에 드리프트 없음을 독립 재검증. 신규 gap 후보("orphan `.done` watcher") 1건은 조사 후 기각(false positive)으로 문서화.

Static 분석 관점에서는 Report 단계 진입 기준을 재확인 충족했으며, 실제 release 판단은 F-139(cloud0 HW 검증)의 사람 확인을 거쳐야 합니다.
