# xcelium-mcp-f175-provenance-migration-gap Design Document

> **Summary**: `test_discovery` 설정에 `schema_version` + 마이그레이션 레지스트리를 도입해, F-175 이전에 discovery된 프로젝트가 영구적으로 TB provenance를 못 받는 버그를 구조적으로 해소한다.
>
> **Project**: xcelium-mcp
> **Author**: Claude Code
> **Date**: 2026-07-08
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-f175-provenance-migration-gap.plan.md](../../01-plan/features/xcelium-mcp-f175-provenance-migration-gap.plan.md)

---

## Context Anchor

> Plan 문서에 별도 `## Context Anchor` 섹션이 없어(레거시 Plan), Executive Summary에서 추출.

| Key | Value |
|-----|-------|
| **WHY** | F-175(TB provenance) 도입 이전에 discovery된 `version>=2` 프로젝트는 `cached_test_files`/`tb_type`이 영구히 채워지지 않아, `sim_batch_run` 등이 조용히 provenance를 생략함 — 안전장치가 가장 필요한 오래된 프로젝트에서 오히려 무력화되는 역설. |
| **WHO** | xcelium-mcp를 사용하는 모든 세션(특히 F-175 배포 이전에 최소 1회 discovery를 마친 기존 프로젝트, 실증 사례: venezia-t0) |
| **RISK** | 안전 방향 실패(잘못된 해시가 아니라 아예 안 줌)라 데이터 무결성 사고는 아니지만, `phase-0-discovery.md` §0C의 staleness 비교가 이런 프로젝트에서 항상 "기록없음→stale"로만 귀결되어 캐싱 최적화 자체가 무의미해짐 |
| **SUCCESS** | `list_tests()`/`sim_batch_run` 최초 1회 호출만으로 기존 프로젝트도 `tb_source`/`combined_sha256`을 정상 반환. 향후 `test_discovery` 스키마가 또 바뀌어도 마이그레이션 함수 등록만으로 대응(호출부 수정 불필요) |
| **SCOPE** | `sim_lifecycle.py::list_tests()`, `tb_provenance.py`, `tools/batch.py`, `batch_runner.py` 내 `test_discovery` 읽기 경로. discovery.py::run_full_discovery는 이미 정상(변경 없음) |

---

## 1. Overview

### 1.1 Design Goals

- `test_discovery` 서브 설정에 명시적 스키마 버전을 도입해, "필드가 있는지 없는지"가 아니라 "몇 버전까지 마이그레이션됐는지"로 판단하게 한다.
- 마이그레이션 로직을 한 곳(레지스트리)에 모아, 호출부(`list_tests`, `build_tb_provenance` 등) 각각에 조건문을 중복 작성하지 않는다.
- 이번 갭(F-175 스키마: `tb_type`+`cached_test_files`+`cached_dependency_files`)뿐 아니라, 향후 `test_discovery` 필드가 또 추가될 때도 같은 인프라로 대응 가능해야 한다.
- "이 테스트만 못 찾음"과 "프로젝트 전체가 미마이그레이션 상태"를 구분하는 진단 메시지를 제공한다(완전 침묵 금지).

### 1.2 Design Principles

- **버전 명시 원칙**: 스키마 상태는 필드 존재 여부 추론이 아니라 `schema_version` 정수로 명시한다(표준 DB 마이그레이션 도구와 동일 패턴).
- **단일 진입점**: `test_discovery`를 읽는 모든 코드는 `ensure_test_discovery_current()`를 거친다 — 호출부마다 별도 백필 조건을 두지 않는다.
- **비침습적 마이그레이션**: 마이그레이션은 최초 읽기 시 1회만 수행되고(`schema_version` 갱신으로 재실행 방지), 기존 정상 프로젝트의 동작(성능·결과)을 바꾸지 않는다.

---

## 2. Architecture Options (v1.7.0)

### 2.0 Architecture Comparison

| Criteria | Option A: 필드 존재 체크 확장 | Option B: schema_version + 마이그레이션 레지스트리 | Option C: 세션 시작 강제 게이트 + pydantic 검증 |
|----------|:-:|:-:|:-:|
| **Approach** | `list_tests()` 진입조건에 `not tb_type or ...` 추가 | `test_discovery.schema_version` 필드 + `TEST_DISCOVERY_MIGRATIONS` 레지스트리, 모든 read 지점에서 공통 함수 호출 | B + `connect_simulator` 시점 강제 마이그레이션 게이트 + pydantic 스키마 검증 |
| **New Files** | 0 | 0 (tb_provenance.py 내 함수 추가) | 1 (schema 정의 모듈) |
| **Modified Files** | 1 (`sim_lifecycle.py`) | 3 (`sim_lifecycle.py`, `tb_provenance.py`, `tools/batch.py`) | B + `bridge_lifecycle.py`/`server.py` 진입점 |
| **Complexity** | Low | Medium | High |
| **Maintainability** | Low (다음 스키마 변경 시 조건문 또 추가 필요) | High (마이그레이션 함수 1개 등록으로 끝) | High (자동 검증까지 포함하지만 인프라 부담 큼) |
| **Effort** | Low | Medium | High |
| **Risk** | 중간 (동일 버그 클래스 재발 가능) | Low | Low (다만 과설계 위험) |
| **Recommendation** | Hotfix only | **Default choice — 선택됨** | Config 스키마가 빈번히 진화할 것이 확실할 때 |

**Selected**: **Option B** — **Rationale**: 이번 F-175 갭 하나만 보면 A로 충분하지만, 근본 원인은 "필드 존재 여부로 마이그레이션 필요성을 판단"하는 패턴 자체다. B는 표준 DB 마이그레이션 도구(Alembic/Flyway 등)와 동일한 "버전 번호 + 등록된 마이그레이션 함수" 패턴을 도입해, 다음에 `test_discovery` 필드가 또 추가돼도 마이그레이션 함수 하나만 레지스트리에 등록하면 모든 호출부가 자동으로 혜택을 받는다. C는 세션 시작 시 강제 게이트 + pydantic 검증까지 요구하는데, config 스키마가 앞으로도 자주 바뀔 것이 확실하지 않은 현재로선 과설계(YAGNI) 위험이 있어 보류.

> 상세 설계는 Option B를 따른다.

### 2.1 Component Diagram

```
┌────────────────────┐     ┌──────────────────────────────┐     ┌───────────────────┐
│ tools/sim_lifecycle │────▶│ ensure_test_discovery_current │────▶│ TEST_DISCOVERY_    │
│  ::list_tests()     │     │  (schema_migration.py, 신규)   │     │  MIGRATIONS 레지스트리│
└────────────────────┘     └──────────────────────────────┘     └───────────────────┘
                                        ▲
┌────────────────────┐                 │
│ tb_provenance.py    │─────────────────┘
│  ::build_tb_        │
│    provenance()     │
└────────────────────┘
                                        │
┌────────────────────┐                 │
│ tools/batch.py       │────────────────┘
│  ::sim_batch_run()   │
└────────────────────┘
```

### 2.2 Data Flow

```
config 읽기 (load_sim_config)
  → ensure_test_discovery_current(discovery)
      → schema_version 확인
      → 필요한 마이그레이션 함수들을 순서대로 적용 (예: v1→v2: tb_type/cached_test_files 채움)
      → schema_version 갱신 후 config 저장
  → build_tb_provenance() 정상 동작 (더 이상 None 반환 안 함)
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `sim_lifecycle.py::list_tests()` | `schema_migration.ensure_test_discovery_current()` | 백필 조건 대신 공통 마이그레이션 함수 호출 |
| `tb_provenance.py::build_tb_provenance()` | `schema_migration.ensure_test_discovery_current()`, `provenance_unavailable_reason()` | provenance 조회 전 스키마 보장 + 진단 메시지 |
| `tools/batch.py::sim_batch_run()` | `tb_provenance.provenance_unavailable_reason()` | `tb_source is None`일 때 사유 메시지 첨부 |
| `batch_runner.py::run_batch_regression()` | 동일 | F-3 확인 결과에 따라 동일 적용 |

---

## 3. Data Model

### 3.1 `test_discovery` 스키마 (mcp_registry.json 내 config)

```python
# schema_migration.py (신규) — 실측 시그니처(Check 단계 gap-detector G2 반영):
# 마이그레이션 함수는 원격 명령(analyze_tb_type/shell_run)을 실행하므로
# async이고 sim_dir 파라미터가 필요하다 — 코드가 최종 근거, 아래는 그대로 옮김.
from typing import Awaitable, Callable

TEST_DISCOVERY_MIGRATIONS: dict[int, Callable[[dict, str], Awaitable[dict]]] = {
    1: _migrate_v1_add_tb_type_and_file_map,   # cached_tests(이름 목록)만 있던 상태 → tb_type/cached_test_files/cached_dependency_files 채움
}

CURRENT_TEST_DISCOVERY_SCHEMA_VERSION = max(TEST_DISCOVERY_MIGRATIONS) + 1  # = 2
```

- `schema_version` 부재 시 `1`로 간주(F-175 이전 상태 = "cached_tests만 있는 v1").
- 마이그레이션 함수는 **현재 버전 → 다음 버전**만 책임진다(누적 적용, 순서 보장).
- 새 필드가 또 필요해지면 `TEST_DISCOVERY_MIGRATIONS[2] = _migrate_v2_add_xxx` 형태로 추가하고 `CURRENT_TEST_DISCOVERY_SCHEMA_VERSION`을 올리는 것만으로 끝난다 — 호출부(`list_tests`, `build_tb_provenance` 등) 수정 불필요.

### 3.2 마이그레이션 흐름

```
schema_version: (없음 또는 1)          schema_version: 2
{                                       {
  "command": "...",                      "command": "...",
  "cached_tests": [...],        ──▶       "cached_tests": [...],
  "cached_at": "..."                      "cached_test_files": {...},   # 신규
}                                         "tb_type": "uvm",              # 신규
                                          "cached_dependency_files": {}, # 신규
                                          "schema_version": 2,           # 신규
                                          "cached_at": "..."
                                        }
```

### 3.3 해당 없음

이 기능은 웹 API/UI/외부 DB 스키마 변경이 없는 내부 config 마이그레이션이므로, API Specification(§4)·UI/UX Design(§5)·MongoDB/SQL 섹션은 N/A.

---

## 4. Function Specification (§4 API Specification 대체)

### 4.1 신규 함수 (`src/xcelium_mcp/schema_migration.py`, 신규 모듈)

> 실측 시그니처(Check 단계 gap-detector G2로 정정 — 원격 명령을 실행하므로 async + `sim_dir` 파라미터 필수, 초안의 동기 `(dict) -> dict`는 오기).

| 함수 | 시그니처 | 역할 |
|------|---------|------|
| `ensure_test_discovery_current` | `async (discovery: dict, sim_dir: str) -> dict` | `schema_version` 확인 후 필요한 마이그레이션을 순서대로 적용, 갱신된 dict 반환. 마이그레이션 단계가 `_MigrationIncomplete`를 던지면 그 버전에서 멈추고 `schema_version`을 진행 전 값으로 되돌려 반환(재시도 가능하게) |
| `_migrate_v1_add_tb_type_and_file_map` | `async (discovery: dict, sim_dir: str) -> dict` (private) | `analyze_tb_type()` 호출로 `tb_type` 채움 + test discovery command 재구성으로 `cached_test_files`/`cached_dependency_files` 채움. 1차 재탐색(`shell_run`) 실패 시 `_MigrationIncomplete` 발생(G1 수정) |
| `_MigrationIncomplete` | `Exception` (private) | 마이그레이션 재스캔 실패를 `ensure_test_discovery_current`에 알리는 내부 시그널 — 호출자에게는 전파되지 않음 |

### 4.2 변경 함수

| 함수 | 파일 | 변경 내용 |
|------|------|----------|
| `list_tests()` | `tools/sim_lifecycle.py` | 기존 `if not cached:` 백필 조건 제거 → `migrated = await ensure_test_discovery_current(discovery, resolved_dir)` 호출로 대체. 변경 시(`migrated != discovery`) `save_sim_config` |
| `resolve_test_name()` **(F-4, Do 단계 추가)** | `test_resolution.py` | `sim_batch_run`/`sim_regression`이 실제로 호출하는 hot path — `list_tests()`와 독립적으로 동일한 `if not cached:` 버그가 있어 동일하게 교체. `schema_migration` 모듈 레벨 import는 순환(그 모듈이 `test_resolution.parse_test_discovery_output`을 가져다 씀)이므로 함수 내부 로컬 import 사용 |
| `provenance_unavailable_reason()` | `tb_provenance.py` | 신규 헬퍼 추가 (F-2) — `list_tests()`/`resolve_test_name()` 경로에서 마이그레이션됐다는 전제하에 `cached_test_files` 존재 여부로 진단 |
| `sim_batch_run()` | `tools/batch.py` | `tb_source is None`일 때 `provenance_unavailable_reason()` 조회해 진단 메시지 추가 (F-2) |
| `sim_regression()` | `tools/batch.py` | F-3 조사 결과(`run_batch_regression()`이 `build_tb_provenance()`를 호출함, 확인됨) — `run_batch_regression()`의 3-tuple 반환 시그니처는 기존 테스트 3곳이 의존해 변경하지 않고, 호출부(`sim_regression()`)에서 `tb_provenance` 딕셔너리에 없는 테스트마다 동일 진단 메시지 적용 |

### 4.3 에러 처리

| 상황 | 처리 |
|------|------|
| 마이그레이션 재스캔(`shell_run`) 실패 | `_MigrationIncomplete` 발생 → `ensure_test_discovery_current`가 `schema_version`을 진행 전 값으로 유지한 채 반환 — 다음 호출에서 해당 마이그레이션 단계 재시도(G1 수정, `sim_discover(force=True)` 없이도 자연 복구) |
| 의존성 스캔(`scan_test_dependencies`) 실패 | `cached_test_files`는 이미 채워진 뒤이므로 더 낮은 위험 — best-effort로 `cached_dependency_files`만 비워둠(예외 전파 안 함) |
| 마이그레이션 무관 예외 | 예외를 삼키지 않고 상위로 전파(`except Exception` 금지, CLAUDE.md 컨벤션과 일치하도록 구체 예외만 처리) |
| `schema_version`이 `CURRENT_TEST_DISCOVERY_SCHEMA_VERSION`보다 큰 경우(다운그레이드 등 비정상) | no-op으로 그대로 통과(미래 버전에 대한 마이그레이션은 정의되지 않으므로 손대지 않음) |

---

## 5. UI/UX Design

N/A — 이 기능은 내부 config 마이그레이션이며 UI 컴포넌트가 없다.

---

## 6. Error Handling

### 6.1 에러/진단 케이스

| 케이스 | 원인 | 처리 |
|------|---------|-------|
| `tb_source: None` + "unavailable (not yet migrated)" | 마이그레이션이 아직 실행되지 않음(스키마 v1) — F-1 적용 전, 또는 `analyze_tb_type()` 이전 시도 실패 후 재시도 대기 | `sim_batch_run` 응답에 진단 메시지 1줄 추가 |
| `tb_source: None` + "not found in cached_test_files" | 스키마는 마이그레이션됐지만 해당 테스트명이 개별적으로 누락(TODO.md 기존 known gap) | 동일하게 진단 메시지로 구분 표시 |

### 6.2 진단 메시지 포맷

```
tb_provenance: unavailable (test_discovery not yet migrated to F-175 schema — run list_tests() or sim_discover(force=True) once)
```

---

## 7. Security Considerations

- [x] 신규 마이그레이션 함수는 기존 `analyze_tb_type()`/`sim_discover` 내부 경로를 재사용하므로 추가 공격 표면 없음
- [x] `schema_version` 필드는 정수 하드코딩 비교만 수행 — 외부 입력 검증 대상 아님(내부 config 파일)
- N/A: 인증/HTTPS/Rate Limiting — 해당 없음(웹 API 아님)

---

## 8. Test Plan (v2.3.0)

> Plan 문서 §4 Test Plan(T-1~T-6)을 그대로 계승, 마이그레이션 레지스트리 관점으로 구체화.

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| Unit | `ensure_test_discovery_current()`, `_migrate_v1_add_tb_type_and_file_map()` | pytest (MockTclServer 불필요, 순수 함수) | Do |
| Integration | `list_tests()` → `build_tb_provenance()` 연쇄 동작 | pytest + MockTclServer | Do |
| Regression | 기존 `list_tests()`/`build_tb_provenance()` 테스트 전체 | pytest | Check |

### 8.2 Test Scenarios (Plan T-1~T-6 매핑)

| # | 테스트 | 검증 | Plan 매핑 |
|---|--------|------|-----------|
| 1 | mock config `schema_version` 없음 + `cached_tests`만 존재 → `ensure_test_discovery_current()` 호출 | `schema_version==2`, `tb_type`/`cached_test_files`/`cached_dependency_files` 채워짐 | T-1 |
| 2 | 위 config 위에서 `build_tb_provenance()` 호출 | `tb_source`/`combined_sha256` 정상 반환(None 아님) | T-2 |
| 3 | 이미 `schema_version==2`인 config로 재호출 | 마이그레이션 함수 호출 안 됨(no-op), 성능 회귀 없음 | T-3 |
| 4 | `cached_test_files`에 없는 특정 테스트명 조회 | "이 테스트만 못 찾음" 메시지 — "미마이그레이션" 메시지와 구분됨 | T-4 |
| 5 | 기존 회귀 테스트 스위트 전체 실행 | 전부 통과 | T-5 |
| 6 | (수동, Do 완료 후) venezia-t0 실제 config로 `list_tests()` 1회 호출 후 재확인 | `tb_source:` 실제 출력에 나타남 | T-6 |
| 7 (G1 수정, Check 단계 추가) | 재스캔(`shell_run`) 실패 상태로 `ensure_test_discovery_current()` 호출 | `schema_version`이 진행 전 값(1)에 머무름, `cached_test_files` 키 자체가 생기지 않음(빈 dict로 stamp 금지) | — (Check 단계 gap-detector G1) |
| 8 (G1 수정, Check 단계 추가) | 시나리오 7 이후 재스캔 성공 상태로 재호출 | 정상적으로 `schema_version==CURRENT`까지 마이그레이션 완료(재시도 경로 실증) | — (Check 단계 gap-detector G1) |

### 8.3 Seed Data Requirements

| Entity | Minimum Count | Key Fields Required |
|--------|:------------:|---------------------|
| mock `test_discovery` config (schema v1) | 1 | `command`, `cached_tests`(list), `cached_at` — `tb_type`/`cached_test_files` 없음 |
| mock `test_discovery` config (schema v2, 정상) | 1 | 위 + `tb_type`, `cached_test_files`, `cached_dependency_files`, `schema_version:2` |

---

## 9. Clean Architecture (Python 모듈 레이어)

> 이 프로젝트는 Next.js/React 스택이 아니므로 §9 템플릿의 Presentation/Application 레이어 대신 기존 `CLAUDE.md` Repository Structure 기준으로 매핑.

### 9.1 Layer Structure

| Layer | Responsibility | Location |
|-------|---------------|----------|
| **Tool (MCP)** | `@mcp.tool()` 데코레이트된 얇은 wrapper | `src/xcelium_mcp/tools/*.py` |
| **Domain Logic** | 마이그레이션/provenance 순수 로직 | `src/xcelium_mcp/schema_migration.py`(신규), `tb_provenance.py` |
| **Infra** | config 로드/저장, TCL 브릿지 호출 | `registry.py`, `tcl_bridge.py`, `bridge_manager.py` |

### 9.2 Dependency Rules

```
tools/*.py (list_tests, sim_batch_run)
    │
    ▼
schema_migration.py ──▶ tb_provenance.py
    │                         │
    ▼                         ▼
registry.py (load/save_sim_config)   discovery.py (analyze_tb_type, 기존 함수 재사용)
```

- `schema_migration.py`는 신규 순수 모듈 — `registry.py`/`discovery.py`에만 의존, `tools/*.py`를 역참조하지 않는다.

### 9.3 이 기능의 레이어 배정

| Component | Layer | Location |
|-----------|-------|----------|
| `ensure_test_discovery_current` | Domain Logic | `src/xcelium_mcp/schema_migration.py` (신규 파일) |
| `provenance_unavailable_reason` | Domain Logic | `src/xcelium_mcp/tb_provenance.py` |
| `list_tests()` wrapper 수정 | Tool | `src/xcelium_mcp/tools/sim_lifecycle.py` |
| `sim_batch_run()` 진단 메시지 추가 | Tool | `src/xcelium_mcp/tools/batch.py` |

---

## 10. Coding Convention Reference

- 함수명: `snake_case`, private 헬퍼는 `_` prefix (프로젝트 기존 컨벤션과 일치)
- 예외 처리: `except Exception` 금지 — `TclError`, `ConnectionError` 등 구체 예외만(F-009/F-031 패턴 계승)
- Import 순서: stdlib → 내부 절대 import(`xcelium_mcp.*`) — 기존 `tools/*.py` 컨벤션과 동일
- 반환 타입 힌트 필수(F-030 패턴 계승): `ensure_test_discovery_current(discovery: dict) -> dict`

---

## 11. Implementation Guide

### 11.1 File Structure

```
src/xcelium_mcp/
├── schema_migration.py        # 신규 — ensure_test_discovery_current + TEST_DISCOVERY_MIGRATIONS + _MigrationIncomplete
├── tb_provenance.py            # 수정 — provenance_unavailable_reason() 추가
├── test_resolution.py           # 수정(F-4, Do 단계 추가) — resolve_test_name() 동일 교체
├── tools/
│   ├── sim_lifecycle.py        # 수정 — list_tests() 백필 조건 → ensure_test_discovery_current 호출로 교체
│   └── batch.py                 # 수정 — sim_batch_run/sim_regression 양쪽에 진단 메시지 (F-2/F-3)
├── batch_runner.py              # 변경 없음 — F-3 확인 결과 build_tb_provenance() 호출 확인됐으나 3-tuple 반환 시그니처 유지, 진단은 호출부(tools/batch.py)에서 처리
tests/
├── test_schema_migration.py    # 신규 — 위 8.2 시나리오 1~5 + G1 수정 검증(재시도)
└── test_resolve_test_name_cache_miss.py  # 재작성(F-4 통합 검증)
```

### 11.2 Implementation Order

1. [ ] `schema_migration.py` 신규 작성: `TEST_DISCOVERY_MIGRATIONS`, `ensure_test_discovery_current()`, `_migrate_v1_add_tb_type_and_file_map()` (F-1 핵심)
2. [ ] `tb_provenance.py`: `provenance_unavailable_reason()` 추가 (F-2)
3. [x] `tools/sim_lifecycle.py::list_tests()`: 기존 `if not cached:` 제거, `ensure_test_discovery_current()` 호출로 교체 + `save_sim_config` 반영
3b. [x] **F-4 (Do 단계에서 발견, 범위 추가)**: `test_resolution.py::resolve_test_name()` — `sim_batch_run`/`sim_regression`이 실제로 호출하는 hot-path 진입점인데 `list_tests()`와 독립적으로 동일한 `if not cached:` 버그를 갖고 있어, 이것만 안 고치면 Plan §1.1의 실제 재현 시나리오가 해소되지 않음(사용자 승인 후 범위 확장). 동일하게 `ensure_test_discovery_current()`로 교체. 순환 임포트 회피를 위해 `resolve_test_name()` 함수 내부 로컬 import 사용(`schema_migration.py`가 이미 `test_resolution.parse_test_discovery_output`을 가져다 쓰므로 모듈 레벨 import는 순환)
4. [x] `tools/batch.py::sim_batch_run()`: `tb_source is None` 분기에 진단 메시지 추가
5. [x] F-3 확인 결과: `batch_runner.py::run_batch_regression()`이 `build_tb_provenance()`를 호출함(확인됨, L909-910) → `tools/batch.py::sim_regression()`에서 `tb_provenance` 딕셔너리에 없는 테스트에 대해 동일 진단 메시지 적용(`batch_runner.py`의 3-tuple 반환 시그니처는 기존 테스트 3곳이 의존하므로 변경하지 않고, 호출부에서 진단을 조회하는 방식으로 최소 침습 구현)
6. [x] `tests/test_schema_migration.py` 작성 (8.2 시나리오 1~5) + `tests/test_resolve_test_name_cache_miss.py` 재작성(F-4 통합 검증)
7. [x] 기존 회귀 테스트 전체 실행 확인 (시나리오 5) — 592 passed, ruff clean, `python -c "import xcelium_mcp.server"` 순환 임포트 없음 확인
8. [ ] (Check 이후, 범위 밖) venezia-t0 실제 프로젝트에서 수동 검증 (시나리오 6)

### 11.3 Session Guide

> 변경 범위가 작아(신규 파일 1개 + 수정 3~4개) 단일 세션으로 충분. 모듈 분리는 참고용.

#### Module Map

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| 마이그레이션 코어 | `module-1` | `schema_migration.py` + `tb_provenance.py` 진단 헬퍼 + 단위 테스트 | 15-20 |
| 호출부 통합 | `module-2` | `list_tests()`/`sim_batch_run()`/`run_batch_regression()`(F-3 결과에 따라) 통합 + 회귀 테스트 | 15-20 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 (현재) | Plan + Design | 전체 | 완료 |
| Session 2 | Do | `--scope module-1` | 15-20 |
| Session 2 (연속) | Do | `--scope module-2` | 15-20 |
| Session 3 | Check + Report | 전체 | 15-20 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-08 | 초안 — Option B(schema_version + 마이그레이션 레지스트리) 선택. Plan의 F-1/F-2/F-3을 스키마 버저닝 관점으로 구체화, 신규 모듈 `schema_migration.py` 설계 | Claude Code |
| 0.2 | 2026-07-08 | module-1(마이그레이션 코어)/module-2(호출부 통합) 구현 완료. Do 단계에서 F-4(`test_resolution.py::resolve_test_name()`) 추가 발견·범위 확장(승인됨) — 실제 hot-path 진입점까지 수정 완료. 592 tests passed, ruff clean | Claude Code |
| 0.3 | 2026-07-08 | Check 단계 gap-detector 결과(Match Rate ~90%) 반영해 iterate: **G1**(Important, 실제 버그) 수정 — 마이그레이션 재스캔 실패 시 `schema_version`을 stamp하지 않고 진행 전 값 유지하도록 `_MigrationIncomplete` 내부 예외 도입, 재시도 경로 테스트 2개 추가. **G2**(Important, 문서 drift) 수정 — §3.1/§4.1의 시그니처를 실제 async+`sim_dir` 코드에 맞게 정정. **G3**(Low) 수정 — §11.1 파일 트리에 F-4 파일 반영. 593 tests passed, ruff clean | Claude Code |
