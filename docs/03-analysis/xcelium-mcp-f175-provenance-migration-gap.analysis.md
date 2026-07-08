# xcelium-mcp-f175-provenance-migration-gap Analysis

> **Feature**: F-175 test_discovery 스키마 마이그레이션 갭 수정 (Option B — schema_version + 마이그레이션 레지스트리)
> **Date**: 2026-07-08
> **Design**: [xcelium-mcp-f175-provenance-migration-gap.design.md](../02-design/features/xcelium-mcp-f175-provenance-migration-gap.design.md) (v0.2)
> **Plan**: [xcelium-mcp-f175-provenance-migration-gap.plan.md](../01-plan/features/xcelium-mcp-f175-provenance-migration-gap.plan.md) (v0.2)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | F-175 이전 discovery된 `version>=2` 프로젝트에서 TB provenance가 영구 누락되는 역설 해소 |
| **SUCCESS** | 최초 1회 호출만으로 provenance 정상화, 향후 스키마 변경 시 마이그레이션 함수 등록만으로 대응 |
| **SCOPE** | `schema_migration.py`(신규), `tb_provenance.py`, `tools/sim_lifecycle.py`, `test_resolution.py`(F-4, Do 단계 추가), `tools/batch.py`, `batch_runner.py`(변경 없음, F-3 확인 후 결정) |

## Runtime Verification

`python -m pytest -q` → **592 passed, 0 failed** (기능 관련 12개 포함). `python -c "import xcelium_mcp.server"` 순환 임포트 없음. 실행 서버가 없는 백엔드 도구 특성상 이 pytest 스위트가 L1/L2/L3 대신 runtime layer 역할.

## Match Rate (static-only)

`Overall = Structural×0.2 + Functional×0.4 + Contract×0.4`

| Sub-score | Value | 근거 |
|-----------|:-----:|------|
| Structural | 97% | Design §11.1 파일 전부 존재·역할 일치. ASCII 트리에 F-4 파일 누락(G3, Low) |
| Functional | 90% | 진짜 registry 패턴 + 실제 마이그레이션 로직 + F-2 두 갈래 진단 정확. 실패 시 stamp 처리 1건 이탈(G1) |
| Contract | 86% | §4.2 동작은 일치하나 §4.1/§3.1 시그니처·타입이 실제 코드(async + `sim_dir` 파라미터)와 어긋남(G2, 문서만) |
| **Overall** | **~90%** | 0.97×0.2 + 0.90×0.4 + 0.86×0.4 = 0.898 |

## Decision Record Verification

Option B(schema_version + 마이그레이션 레지스트리) 실제 구현 확인됨 — 정수 `schema_version` 필드 + `dict[int, Callable]` 레지스트리(`TEST_DISCOVERY_MIGRATIONS`), `CURRENT_… = max(...) + 1` 자동 파생. Option A(필드 존재 체크 확장)나 Option C(pydantic 게이트)가 아님.

F-4(`test_resolution.py::resolve_test_name`)는 원 Design 파일 목록에 없었으나 Do 단계에서 발견·승인되어 Plan/Design 양쪽 v0.2에 사후 기록됨 — 문서와 코드 일치 확인(로컬 import로 순환 임포트 회피, 근거 주석 포함).

## Plan Success Criteria

| Criteria | Status | Evidence |
|----------|:------:|----------|
| 최초 1회 호출만으로 기존 프로젝트도 provenance 정상 반환 | ⚠️ **Partial(사실상 Met, 엣지 케이스 有)** | `resolve_test_name`이 실제 hot path에서 1회 호출로 마이그레이션+저장(`test_resolution.py:147,150`), `test_pre_f175_config_gets_migrated_and_resolves_name` 통과로 happy path 증명. **단, G1**: 마이그레이션 중 재스캔이 실패하면 빈 맵인 채로 `schema_version`이 영구 stamp되어 "1회 호출"이 깨지는 엣지 존재 |
| 향후 스키마 변경 시 마이그레이션 함수 등록만으로 대응 | ✅ **Met** | `TEST_DISCOVERY_MIGRATIONS[n] = fn` 등록만으로 모든 호출부(`list_tests`, `resolve_test_name`)가 자동 적용받는 구조 확인(`schema_migration.py:97-101`) |

## Gap List

| # | Severity | Conf. | Gap | Evidence |
|---|:--------:|:-----:|-----|----------|
| G1 | **Important** | 85% | 마이그레이션 재스캔 실패 시에도 `schema_version`이 stamp되어 버림 — Design §4.3("실패 시 schema_version 갱신 안 함, 다음 호출에서 재시도")과 반대. 빈 `cached_test_files`인 채로 영구 "마이그레이션됨" 처리되어 `build_tb_provenance`가 계속 `None`, 진단 메시지도 "미마이그레이션"이 아닌 "이 테스트만 없음"으로 잘못 표시됨. `sim_discover(force=True)`로만 복구 가능(SUCCESS 기준이 불필요하다고 한 수동 개입) | `schema_migration.py:69-70,84-91,122` |
| G2 | Important | 100% | Design §4.1/§3.1 시그니처가 실제 코드(비동기 + `sim_dir` 파라미터)와 다름 — 코드가 맞고 문서만 stale(design drift) | `schema_migration.py:104` vs Design §4.1; `:97` vs §3.1 |
| G3 | Low | 100% | Design §11.1 File Structure 트리에 `test_resolution.py`/`test_resolve_test_name_cache_miss.py`(F-4) 누락 — §11.2/Version History엔 있어 내부 일관성 문제만 | Design §11.1 |
| G4 | Info | 60% | `_migrate_v1` 내 `analyze_tb_type` 호출은 예외 미포장(그 다음 `shell_run` 스캔만 try/except) — 실전 리스크는 낮으나(거의 예외 던지지 않음) §4.3 "안전 방향 실패" 의도가 완전히 보장되진 않음 | `schema_migration.py:62` |

Critical 없음.
