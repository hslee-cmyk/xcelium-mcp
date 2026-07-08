# xcelium-mcp-f175-provenance-migration-gap Plan

> **Feature**: F-175(`tb_source`/`tb_provenance` combined_sha256)가 F-175 배포 **이전에** 이미 discovery된 프로젝트(config `version` >= 2)에서는 영구적으로 채워지지 않는 버그 수정 — `sim_batch_run`/`sim_regression`/`sim_bridge_run`이 아무 경고 없이 provenance 블록을 계속 생략한다.
>
> **Date**: 2026-07-08
> **Status**: Draft
> **Found in**: venezia-fpga 세션, `verilog-rtl-debugger` agent가 TOP014 V-13(T-002 COV-GAP) 재검증 중 `sim_batch_run` 반환값에 `tb_source`/`tb_provenance`가 3회(신규 agent 수정 후 1회, MCP 재연결 후 재확인 1회, 총 라이브 세션 기준) 연속 부재함을 실측 확인 → 소스 추적으로 근본원인 특정

---

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | F-175(`tb_provenance.py`)는 `test_discovery.cached_test_files`(test명→파일경로 dict)에 의존해 TB 소스를 찾는다. 이 키는 (a) `discovery.py::run_full_discovery`의 최초/강제 재검출, 또는 (b) `tools/sim_lifecycle.py::list_tests()`의 **cache-miss(=`cached_tests`가 비어 있을 때만) 백필** 경로로만 채워진다. `sim_discover(force=False)`는 `config.version >= 2`면 무조건 "이미 존재함"으로 조기 반환(재검출 스킵)하고, `list_tests()`의 백필은 `cached_tests`가 **이미 채워져 있으면** 아예 진입하지 않는다. 그 결과 **F-175가 배포되기 전에 이미 discovery되어 `version=2`에 도달한 프로젝트는, `cached_test_files`/`tb_type`이 빠진 채로 영원히 고정**된다 — `build_tb_provenance()`는 이런 프로젝트의 모든 테스트에 대해 항상 조용히 `None`을 반환하고, 호출부(`tools/batch.py`)는 그 `None`을 "이 테스트만 예외"와 구분 없이 그냥 생략한다. |
| **Solution** | `list_tests()`의 백필 조건을 `cached_tests`뿐 아니라 `test_discovery.tb_type`/`cached_test_files` 부재도 함께 검사하도록 확장 — 기존 name-only 캐시가 있어도 F-175 스키마로 **opportunistic 마이그레이션**을 1회 수행한다. 추가로 `build_tb_provenance()`가 "이 테스트만 못 찾음"과 "프로젝트 전체가 F-175 이전 스키마"를 구분해 후자일 때 진단 메시지를 1줄 남긴다(완전 침묵 금지). |
| **Function/UX Effect** | `list_tests()` 또는 `sim_batch_run` 최초 호출 시 자동으로 `cached_test_files`가 채워져, 이후 모든 `sim_batch_run`/`sim_regression`/`sim_bridge_run` 호출에 `tb_source`/`combined_sha256`이 정상 첨부됨. 별도로 `sim_discover(force=True)`를 수동 실행할 필요가 없어짐. |
| **Core Value** | F-175(TB 소스 provenance)의 존재 이유가 "로컬 사본과 실제 시뮬레이션 소스가 달라도 아무도 모른다"는 사고를 막는 것인데, 이 마이그레이션 갭이 있으면 **F-175 배포 이전부터 있던 프로젝트일수록(=가장 오래돼서 로컬/원격 drift 위험이 가장 큰 프로젝트일수록) 정작 이 안전장치가 조용히 무력화**된다 — 정확히 지켜야 할 대상에서 안 지켜지는 역설. |

---

## 1. Problem Detail

### 1.1 실전 재현 (2026-07-07~08, venezia-fpga 세션)

`verilog-rtl-debugger` agent가 TODO.md T-002(TOP014 V-13 FIFO semaphore COV-GAP)를 xcelium-mcp로 재검증하며 `sim_batch_run(test_name="VENEZIA_TOP014_btnop_interlock_test")`를 3회 호출(agent 정의서 수정 전/후, MCP 재연결 전/후 조합) — **매번** 반환값에 `tb_source:`/`combined_sha256` 블록이 전혀 없었다. MCP 연결이 실제로 살아있는 상태(genuine shm 생성·파형 조회 확인됨)에서도 동일해, "연결 문제로 인한 false negative"라는 가설은 배제됐다.

### 1.2 Root Cause (소스 추적으로 확정)

```
$ mcp_config(action="get", file="config", key="version")
→ 2
$ mcp_config(action="get", file="config", key="test_discovery")
→ {"command": "...ls tb_tests/*.v...", "cached_tests": [...18개 테스트명...], "cached_at": "2026-04-16T15:38:04"}
   (tb_type 없음, cached_test_files 없음, cached_dependency_files 없음)
```

- `discovery.py::run_full_discovery()` L371-374:
  ```python
  if not force:
      existing = await load_sim_config(sim_dir)
      if existing and existing.get("version", 1) >= 2:
          return f"Registry already exists for {sim_dir}. Use force=True to re-detect."
  ```
  이 프로젝트의 config는 `version: 2`이므로 `sim_discover(force=False)`를 몇 번을 호출해도 **항상 조기 반환** — Phase A(`analyze_tb_type` 포함)가 재실행되지 않아 `tb_type`/`cached_test_files`/`cached_dependency_files`가 채워질 기회 자체가 없다.
- `tools/sim_lifecycle.py::list_tests()` L172-209의 백필도 `if not cached:`(=`cached_tests`가 비어 있을 때만) 조건이라, 이 프로젝트처럼 **`cached_tests`(이름 목록)는 이미 2026-04-16에 채워져 있고 `cached_test_files`(이름→경로 dict)만 없는** 상태에서는 진입조차 하지 않는다. `list_tests()` 자체 주석(L182-186)도 이 케이스를 "Pre-F-175 config... no file mapping until sim_discover re-runs"라고 명시하지만, 정작 그 "재실행"을 트리거하는 코드 경로가 없다.
- `tb_provenance.py::resolve_tb_source_file()` L174: `cfg.get("test_discovery", {}).get("cached_test_files", {}).get(full_test_name)` → 키 자체가 없으니 항상 `None`.
- `tb_provenance.py::build_tb_provenance()` L236: `if not primary_path: return None` → 항상 `None`.
- `tools/batch.py` L232-233: `if tb_source is not None: parts.append(...)` → **조용히 생략**, 에러도 경고도 없음.

**결론**: `test_discovery.command`가 `version` 필드와 별개로 F-175 스키마(`tb_type`+`cached_test_files`+`cached_dependency_files`)까지 포함하는지 여부를 아무도 체크하지 않는다. F-175 도입 시점(2026-07-06, `docs/03-analysis` F-174/F-175 이력 참조)에 최상위 `version`을 올리지 않았기 때문에, **F-175 이전에 discovery된 모든 `version>=2` 프로젝트가 예외 없이 이 갭에 해당**한다 — 특정 테스트의 예외적 실패(TODO.md의 UVM multi-line class 등)가 아니라 **프로젝트 단위의 구조적 누락**이다.

### 1.3 파급 범위

- `sim_batch_run`(`tools/batch.py` L222), `sim_regression`(`batch_runner.py` L909-912, `run_batch_regression` 내부), `sim_bridge_run`(동일 메커니즘 추정 — 확인 필요)까지 `build_tb_provenance()`를 호출하는 모든 tool이 동일하게 영향받는다.
- F-175 도입 이전에 최소 1회 이상 `sim_discover`가 완료된(=`version>=2`에 도달한) 기존 프로젝트 전부가 대상 — venezia-t0(`cached_at: 2026-04-16`)가 실증 사례. 신규 프로젝트(F-175 이후 최초 discovery)는 영향 없음.
- **안전 방향으로 실패**(잘못된 hash를 주는 게 아니라 그냥 안 줌)라 데이터 무결성 사고로 이어지지는 않지만, phase-0-discovery.md §0C가 정의하는 "분석서 staleness hash 비교"가 이런 프로젝트에서는 **원천적으로 항상 "기록 없음→stale"로만 판정**되어 그 캐싱 최적화 자체가 무의미해진다(venezia-fpga `verilog-rtl-debugger.plan.md` FR-10 / `verilog-tb-analyst.plan.md` FR-14의 전제가 이 갭 때문에 이 프로젝트에서는 항상 최악의 경로로만 동작).

---

## 2. Fix Items

### F-1: `list_tests()` 백필 조건에 F-175 스키마 완전성 체크 추가 (핵심 수정)

**Before** (`tools/sim_lifecycle.py` L172):
```python
if not cached:
    ...
```

**After**:
```python
needs_backfill = not cached or not discovery.get("tb_type") or "cached_test_files" not in discovery
if needs_backfill:
    ...
    # cached_tests가 이미 있어도 tb_type이 없으면 F-175 스키마 마이그레이션이 필요 —
    # analyze_tb_type()을 여기서도 호출해 discovery.py의 Phase A와 동등하게 만든다.
    if not discovery.get("tb_type"):
        tb_type = await analyze_tb_type(resolved_dir)
    else:
        tb_type = discovery["tb_type"]
    ...
```
- `cached_tests`가 이미 있어도 재활용하지 않고, `tb_type` 기준으로 test discovery command를 다시 구성해 `cached_test_files`까지 채우는 전체 경로를 탄다(기존 "Pre-F-175 config" fallback 분기 제거).
- 부작용 점검: `analyze_tb_type()`이 추가 원격 명령을 유발하므로, 이 백필은 **프로젝트당 1회**(이후 `tb_type`+`cached_test_files`가 채워지면 다시 트리거 안 됨)만 발생함을 확인.

### F-2: `build_tb_provenance()` — "이 테스트만 예외" vs "프로젝트 전체 미마이그레이션" 구분 진단

`tb_provenance.py`에 헬퍼 추가:
```python
async def provenance_unavailable_reason(full_test_name: str, sim_dir: str = "") -> str | None:
    """None이면 정상 동작 가능. 문자열이면 build_tb_provenance()가 None을 반환할 진단 사유."""
    cfg = await load_sim_config(await resolve_sim_dir(sim_dir))
    if not cfg:
        return None
    discovery = cfg.get("test_discovery", {})
    if "cached_test_files" not in discovery:
        return "test_discovery not yet migrated to F-175 schema — run list_tests() or sim_discover(force=True) once"
    if full_test_name not in discovery.get("cached_test_files", {}):
        return f"'{full_test_name}' not found in cached_test_files (see TODO.md F-175 known gaps)"
    return None
```
`tools/batch.py`가 `tb_source is None`일 때 이 함수로 사유를 조회해 `\ntb_provenance: unavailable ({reason})` 한 줄을 덧붙인다 — F-1이 적용되면 이 메시지는 프로젝트당 최초 1회만(그것도 다음 호출부터는 F-1이 자동 해소하므로) 노출되고, F-1 없이 이 항목만 단독 적용해도 최소한 "왜 없는지"는 보이게 된다. **F-1이 근본 수정, F-2는 F-1 적용 전/도입 유예 기간의 가시성 보강**(둘 다 적용 권장, F-2 단독 적용도 가능).

### F-3: `sim_regression`/`sim_bridge_run` 경로 확인 (범위 조사)

`tools/batch.py`/`batch_runner.py` 외에 `sim_bridge_run`도 동일하게 `build_tb_provenance()`를 호출하는지 확인 — 호출한다면 F-1/F-2로 동일하게 해소됨(백필은 config 단위이므로 tool 종류 무관). Design 단계에서 실제 호출 지점 전수 확인.

### F-4 (Do 단계에서 발견, 범위 추가): `test_resolution.py::resolve_test_name()`의 독립적인 동일 버그

Do 단계(module-2) 구현 중 발견: `sim_batch_run`/`sim_regression`이 실제로 호출하는 것은 `list_tests()`가 아니라 `test_resolution.py::resolve_test_name()`(짧은 테스트명 → full name 변환, `tools/batch.py:154,317`)이다. 이 함수 안에 `list_tests()`와 **완전히 동일한 `if not cached:` 조건**이 독립적으로 복사되어 있어(F-1이 고치는 `list_tests()`와는 별개 코드 경로), `list_tests()`만 고쳐서는 Plan §1.1의 실제 재현 시나리오(`sim_batch_run` 직접 호출)가 해소되지 않는다는 것이 확인됨. F-1과 동일하게 `schema_migration.ensure_test_discovery_current()`로 교체 — 사용자 승인 하에 범위 추가.

---

## 3. Scope

| 파일 | 변경 내용 |
|------|----------|
| `src/xcelium_mcp/tools/sim_lifecycle.py` | F-1: `list_tests()` 백필 조건 확장 (`needs_backfill`), `tb_type` 부재 시 `analyze_tb_type()` 호출 추가 |
| `src/xcelium_mcp/tb_provenance.py` | F-2: `provenance_unavailable_reason()` 신규 함수 |
| `src/xcelium_mcp/tools/batch.py` | F-2: `tb_source is None`일 때 진단 메시지 추가 (`sim_batch_run`), `sim_regression` 경로도 동일 적용(F-3 확인 후) |
| `src/xcelium_mcp/batch_runner.py` | F-3 확인 결과에 따라 `run_batch_regression` 쪽도 F-2 적용 |
| `src/xcelium_mcp/test_resolution.py` | F-4(Do 단계 추가): `resolve_test_name()` 내 독립 백필 조건도 `ensure_test_discovery_current()` 호출로 교체 — 실제 hot-path 진입점 |
| `src/xcelium_mcp/schema_migration.py` | F-1 핵심 로직 신규 모듈(Design 참조): `TEST_DISCOVERY_MIGRATIONS`, `ensure_test_discovery_current()` |
| `tests/` | 신규 테스트: `version=2` + `cached_tests`만 있고 `cached_test_files` 없는 mock config로 `list_tests()`/`resolve_test_name()` 호출 시 마이그레이션되는지 검증 |

변경 없음: `discovery.py::run_full_discovery`(이미 올바르게 동작, force=True 경로는 정상) — 이번 버그는 force=False lazy 경로(`list_tests`)의 조건 누락이 원인.

---

## 4. Test Plan

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | mock config: `version:2`, `test_discovery:{command, cached_tests:[...], cached_at}` (tb_type/cached_test_files 없음)로 `list_tests()` 호출 | `cached_test_files`/`tb_type`/`cached_dependency_files`가 새로 채워짐 |
| T-2 | T-1 config 위에서 `sim_batch_run` 호출(또는 `build_tb_provenance()` 직접 호출) | `tb_source`/`combined_sha256`이 정상 반환됨(더 이상 None 아님) |
| T-3 | 이미 F-175 스키마가 채워진 정상 config로 `list_tests()` 재호출 | 불필요한 재백필 없음(성능 회귀 없음 — `needs_backfill=False`) |
| T-4 | `cached_test_files`에 없는 특정 테스트명(TODO.md 기존 known gap 케이스)만 조회 | F-2 진단 메시지가 "프로젝트 미마이그레이션"이 아니라 "이 테스트만 못 찾음"으로 정확히 구분되어 표시 |
| T-5 | 회귀: 기존 `list_tests()`/`build_tb_provenance()` 관련 테스트 전체 통과 | 기존 정상 케이스(F-175 이후 discovery된 프로젝트) 깨지지 않음 |
| T-6 | 실제 venezia-t0(`version:2`, `cached_at:2026-04-16`) 프로젝트로 수동 검증 — `list_tests()` 또는 `sim_batch_run` 1회 호출 후 재호출 | `tb_source:`/`combined_sha256`이 실제로 출력에 나타남 (venezia-fpga 세션에서 실측) |

---

## 5. Related Documents

- `TODO.md` §F-175 — 이미 알려진 per-test 수준 gap(멀티라인 class 선언, dependency 재귀 미지원) 목록. 이번 버그는 그 목록과 **다른 클래스**(프로젝트 단위 마이그레이션 누락)이며, 수정 후에도 TODO.md의 기존 항목들은 여전히 유효(별개로 유지).
- `src/xcelium_mcp/tb_provenance.py` 모듈 docstring — F-175 설계 의도("동기 부여 사고": stale 로컬 TB 사본 문제) 원문. 이번 갭은 그 의도가 **가장 필요한 오래된 프로젝트에서 오히려 무력화**된다는 역설을 보여줌.
- venezia-fpga `chip-design-skills/docs/01-plan/features/verilog-rtl-debugger.plan.md` FR-10, `verilog-tb-analyst.plan.md` FR-14 — 이 갭의 **소비자측** 영향(§0C staleness 체크가 이 프로젝트에서 항상 "기록없음→stale"로만 귀결). 이 문서(F-175 마이그레이션 갭)가 해소되면 그쪽 FR-10/FR-14의 `tb_provenance` 값이 비로소 실제로 채워질 수 있음 — 두 저장소의 fix가 서로 전제조건 관계.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-07-08 | 초안 — venezia-fpga 세션에서 `verilog-rtl-debugger`가 TOP014 T-002 재검증 중 `tb_source`/`tb_provenance` 3연속 부재를 실측(연결 문제 아님을 확인), 소스 추적으로 root cause 특정: `list_tests()` 백필 조건이 `cached_tests` 존재 여부만 보고 F-175 스키마(`tb_type`/`cached_test_files`) 완전성은 안 봐서, F-175 이전에 discovery된 `version>=2` 프로젝트가 영구 미마이그레이션 상태로 고정됨. F-1(백필 조건 확장, 핵심)/F-2(진단 메시지)/F-3(sim_regression/bridge_run 범위 확인) 정리 |
| 0.2 | 2026-07-08 | Do 단계(module-2)에서 F-4 발견·추가: `sim_batch_run`/`sim_regression`이 실제 호출하는 `test_resolution.py::resolve_test_name()`에 `list_tests()`와 독립적인 동일 버그가 있어 사용자 승인 하에 범위 확장. Design에서 확정한 Option B(schema_version + 마이그레이션 레지스트리, `schema_migration.py`)로 `list_tests()`와 `resolve_test_name()` 양쪽 모두 교체 완료 |
