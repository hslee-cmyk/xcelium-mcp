
---

## 2026-07-02 - F-144: 버그 수정 — bisect CSV 소수점 값 미지원

### 배경
사용자 버그 리포트: "bisect를 csv 파일로 처리할 때 소수점 자리가 지원이 안 되는 문제". Explore 에이전트 조사 + 직접 코드 확인으로 `csv_cache.py`에서 원인 2곳 확정.

### 구현 내용
- `csv_cache.py`에 `_parse_sim_time_ns(raw: str) -> int` 헬퍼 신규 추가 — `int(raw)` 우선 시도, 실패 시 `float(raw)` 후 `round()`로 반올림. 완전히 파싱 불가하면 `ValueError` 전파.
- `bisect_csv()`의 SimTime 파싱 2곳(메인 루프, suffix read-ahead 루프)을 `int(raw_time)` → `_parse_sim_time_ns()` + try/except로 교체 — 파싱 실패 row는 크래시 대신 skip.
- `_to_number(s: str) -> int | float | None` 헬퍼 신규 추가 — `int(s, 0)`(hex/oct/dec literal) 우선 시도, 실패 시 `float(s)`(소수점/과학적 표기법 실수값) 폴백. 둘 다 실패하면 `None` 반환.
- `_eval_condition()`의 숫자 비교를 `int(cur_val, 0)`/`int(target, 0)` → `_to_number()` 기반으로 교체 — `eq`/`ne`/`gt`/`lt` 4개 op 전부 소수점 값에서 정상 동작. 기존 tristate(`x`/`z`) → 문자열 fallback 동작은 그대로 유지.

### 검증
`tests/test_bisect.py`에 21개 신규 테스트 추가 (`TestBisectCsvGtLt`, `TestBisectCsvDecimalValue`, `TestBisectCsvDecimalSimTime`, `TestParseSimTimeNs`, `TestToNumber`) — gt/lt op은 이번 수정 전까지 정수값에 대해서도 테스트가 전무했음을 확인하고 함께 보강.
`python -m pytest` 347 passed (326→347) / `python -m ruff check src/` all checks passed.

### 남은 작업 (별도 태스크로 분리, 2026-07-02 광범위 조사)
- F-145: `simvision_ops.py _load_rows()`(compare_waveforms/compare_csv_diff)에 동일한 `int(raw_time)` 크래시가 독립적으로 존재 — F-144의 `_parse_sim_time_ns` 재사용 권장
- F-146: 시간 문자열 파싱 3곳(`shell_utils.py _parse_time_ns`, `tcl_preprocessing.py _parse_l1_time_ns`, `sim_lifecycle.py _DURATION_RE`)이 `\d+`-only 정규식이라 소수점 미지원
- F-147: `deposit_signal`의 `_DEPOSIT_VALUE_RE`가 digital literal만 허용해 real/wreal(analog) 값 미지원 — bisect(읽기) 버그의 쓰기 경로 짝

---

## 2026-07-02 - F-143: v5.2 gap-fix (Minor M4) — _update_dump_history load_sim_config force=True

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` (2026-07-02 재분석) Minor M4.
`_update_dump_history`가 `load_sim_config(sim_dir)`을 `force=True` 없이 호출 — design.md §7 pseudocode는 `force=True`를 사용. 기존엔 `save_sim_config`의 캐시 무효화에 의존해 순차 regression 루프 안에서는 문제없었지만, 다른 프로세스/세션이 동시에 config를 갱신한 경우 stale 캐시를 읽을 이론적 가능성이 있었음.

### 구현 내용
- `batch_runner.py` `_update_dump_history()`: `load_sim_config(sim_dir)` → `load_sim_config(sim_dir, force=True)` — design.md §7 스펙과 일치
- `tests/test_dump_history_stats.py`: `test_update_dump_history_loads_config_with_force` 신규 추가 — `load_sim_config`가 `force=True`로 호출되는지 mock assertion으로 검증

### 검증
`python -m pytest` 326 passed (325→326, 신규 1개), 0 warnings / `python -m ruff check src/` all checks passed.
`run_batch_regression` per-test 루프에서의 반복 호출도 순차 실행이라 회귀 없음 — `test_regression_updates_dump_history_and_dump_stats_shape`가 `_update_dump_history` 자체를 mock하므로 이번 변경의 영향을 받지 않고 그대로 통과.

---

## 2026-07-02 - F-142: v5.2 gap-fix (Minor M2) — datetime.utcnow() deprecation 해소

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` (2026-07-02 재분석) Minor M2.
`_update_dump_history`가 Python 3.12+에서 deprecated된 `datetime.utcnow()`를 사용 — pytest 스위트에서 DeprecationWarning 2건 발생 중이었음(3.14 환경).

### 구현 내용
- `batch_runner.py` import: `from datetime import datetime` → `from datetime import datetime, timezone`
- `_update_dump_history()`: `datetime.utcnow().isoformat(timespec="seconds")` → `datetime.now(timezone.utc).isoformat(timespec="seconds")`
- 코드베이스 내 `datetime.utcnow()` 사용처는 이 한 곳뿐이었음 (grep 확인)

### 검증
`python -m pytest` 325 passed, **0 warnings** (기존 2건의 DeprecationWarning 해소 확인) / `python -m ruff check src/` all checks passed.
`tests/test_dump_history_stats.py`의 `datetime.fromisoformat(entry["updated_at"])` 파싱 검증도 그대로 통과 (offset-aware ISO 문자열도 `fromisoformat`으로 정상 파싱됨).

---

## 2026-07-02 - F-141: v5.2 async wiring 단위 테스트 추가

### 구현 내용
`tests/test_dump_history_stats.py` 신규 작성 (7 tests) — F-140에서 손댄 async wiring 영역을 직접 커버:
- `_update_dump_history`: `load_sim_config`/`save_sim_config` mock — `last_dump_summary`/`updated_at`/`scope_overrides` strip/`dump_scopes` 기본값 검증
- `_lazy_discover_boundaries`: `tmp_path`에 실제 Yosys JSON netlist 작성 + config mock 4 케이스 (정상 파싱, `write_discovered_boundaries=True` 시 영속, `netlist_info` 없음, JSON 파일 미존재)
- `run_batch_regression` 3-test 케이스 (`T1=10, T2=5, T3=50` signal count) — `shell_run`/`poll_batch_log`/`_preprocess_setup_tcl`/`_update_dump_history`를 전부 mock하여 실제 함수를 end-to-end 구동:
  - 3개 테스트 모두에서 `_update_dump_history`가 호출되는지 확인 (Gap #3 회귀 방지)
  - `dump_stats`가 design.md §8 스펙(`max`/`min` = `{test,total}` dict, `per_test` = `total`/`top_boundary`/`block_count`, per-test named suggestion)과 정확히 일치하는지 확인 (Gap #2 회귀 방지)

### 패턴
`run_batch_regression`은 900줄 orchestrator라 실제 실행 경로를 태우려면 `shell_run` 호출 순서에 의존하지 않는 catch-all mock(`AsyncMock(return_value="")`)이 필요 — 정확한 call sequence를 하드코딩하는 대신 각 단계가 빈 문자열/성공 응답에도 안전하게 진행되도록 설계된 점을 활용함 (test_batch_helpers.py의 `_fake_shell` catch-all 패턴과 동일 계열, index 기반 대신 default-return 사용).

### 검증
`python -m pytest` 325 passed (318 → 325, 신규 7개) / `python -m ruff check src/` all checks passed.

---

## 2026-07-02 - F-140: v5.2 gap-fix — dump_history/dump_stats 스키마 정합

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` Gap #1-#3 (Important, Match Rate 85%의 API Contract 78%를 끌어내린 원인).

### 구현 내용
- `batch_runner.py` `_update_dump_history()`: 저장 키 `dump_summary` → `last_dump_summary`로 변경, `scope_overrides` strip, `updated_at`(UTC ISO, seconds) 추가 — design.md §7 스펙과 일치
- `run_batch_regression()` per-test 루프: `test_dump_summary`가 나올 때마다 `_update_dump_history` 호출 추가 — 이전에는 `run_batch_single` 경로에서만 갱신되어 regression 실행 후 `use_dump_history=True` 재사용이 불가능했음 (Plan §3.2 "항상 갱신" 정책 위반)
- `dump_stats` 집계: `max_total_signals`/`min_total_signals` bare int → `{"test":…, "total":…}` dict로 변경, `per_test` 엔트리를 `total`/`top_boundary`/`block_count`로 재구성, generic variance>0.5 메시지 대신 per-test `total > avg×2` named suggestion으로 복원 — design.md §8 pseudocode와 일치

### 하위 호환
`run_batch_single`의 read path(`history.get(test_name, {}).get("dump_scopes")`)는 `dump_scopes` 키만 사용 — `dump_summary`→`last_dump_summary` 리네임의 영향 없음. 확인됨.

### 검증
`python -m pytest` 318 passed / `python -m ruff check src/` all checks passed.

### 남은 작업
F-141: 이 async wiring(`_lazy_discover_boundaries`/`_update_dump_history`/`dump_stats`)에 대한 단위 테스트가 여전히 없음 — 분석서 "Coverage gap" 섹션이 정확히 지적한 사각지대. 순수 함수만 테스트되어 있어 이번 Gap #1-#3도 여기서 발생했었음.

---

## 2026-07-01 - F-138: v5.2 Phase 2 Auto-detection

### 구현 내용
- `sim_env_detection.py`: Phase 2 함수 3개 추가
  - `_parse_describe_output(scope, output)` — TCL `scope -describe -sort kind` 파싱, 비트범위 제거
  - `_boundaries_from_tcl(bridge, top_scope, depth, block_filter)` async — SimVision bridge 재귀 scope describe+show, `_SCOPE_PATH_RE` whitelist로 TCL injection 방지
  - `_boundaries_from_json(json_path, top_module, depth, block_filter)` — Yosys JSON modules/cells 계층 파싱, fnmatch block_filter 지원 (str도 자동 리스트 변환)
- `batch_runner.py`: `_lazy_discover_boundaries(sim_dir, dump_strategy, sim_mode)` 추가
  - `netlist_info.{mode}.boundary_json` → `_boundaries_from_json` Flow B 자동 호출
  - `run_batch_single`: `block_boundaries` 비어있고 `default_block_policy` 설정 시 자동 발동
- `tools/sim_lifecycle.py`:
  - `sim_bridge_run(auto_boundaries=False)`: SimVision 연결 후 `_boundaries_from_tcl` 호출 (Flow A)
  - `sim_discover(boundary_depth=3)`: config의 `dump_strategy.{rtl,gate}.boundary_depth` 저장

### 테스트
- `tests/test_hierarchical_dump.py`: 10개 신규 (Phase 1: 16 + Phase 2: 10 = 26개)
- **318/318 PASS**, ruff clean

### 학습
- `_parse_scope_item`은 `tools/signal_inspection.py`에 정의됨 — 순환 임포트 방지를 위해 `sim_env_detection.py`에 local copy `_parse_scope_item_local` 작성
- block_filter는 str 또는 list 모두 허용 (str → [str] 자동 변환)
- `_boundaries_from_json`에서 top_module 자체는 result에서 제외 (sub-block만 수집)
- `sim_discover` boundary_depth 저장: discovery 성공 후 update (result가 "ERROR"/"USER INPUT" 미시작 시)

---

## 2026-04-22 - F-136 + F-137

### F-136: checkpoint.py restore_checkpoint_impl /tmp fallback 제거
- `restore_checkpoint_impl()`: `except ValueError: resolved_dir = ""` → `return "ERROR: Project directory not configured. Run sim_discover first..."`
- BUG-A (예외 무시) + BUG-B (/tmp fallback) + BUG-C (manifest 검증 스킵) 동시 해결
- chk_base 분기 제거: `os.path.join(resolved_dir, "checkpoints")` 단일 경로
- 두 호출부(checkpoint tool, batch.py)는 이미 resolve 완료 상태로 호출 → 추가 수정 불필요

### F-137: Temp 파일 Cleanup 메커니즘
- `tmp_cleanup.py` 신규: `cleanup_old_logs(ttl=86400)`, `cleanup_session_logs()`
- `csv_cache.py`: SHM mtime 파일명 포함 영속 디스크 캐시; `_get_shm_mtime()`, `_cache_key(shm_mtime)`, `_default_output_path(shm_mtime)`, `extract()` disk hit 로직, `cleanup_stale_csv()`
- `-overwrite` 플래그 제거 (mtime-keyed 파일명이 unique)
- `bridge_lifecycle.py`: `start_bridge_simulation()` 시작 시 `cleanup_old_logs()` 호출
- `tools/batch.py`: `sim_batch_run` 시작 시 `cleanup_old_logs()`, 완료 후 `cleanup_stale_csv(shm_path)`
- `tools/sim_lifecycle.py`: `sim_disconnect(shutdown)` 시 `cleanup_session_logs()`
- `tools/waveform.py`, `debug.py`, `simvision_ops.py`: `ps_to_png()` 후 `os.unlink(ps_path)` (try/finally)
- 11 tests in test_f136_f137.py — 292/292 pass, ruff clean
cmd (F-110)
- SIGN-015: Wrong Tcl flag for operation variant (F-109)
- SIGN-016: Using stale ID lists instead of fresh parse (F-115)

**All tasks:** 125/125 passes=true. 258 pytest, ruff clean.

- Explicit display= still takes precedence; no VNC found returns clear error

**Learnings:**
- TCL `stop -create` supports `-object <sig>` (change on any value) vs `-condition <expr>` (comparison). These are mutually exclusive forms.
- `detect_vnc_display()` already existed in sim_env_detection.py — just needed to be called in compare_simvision (was only wired in start_simvision)
- MCP tool modules under `src/xcelium_mcp/tools/`
- BridgeManager DI, 7 tool modules (since v4.2)
- Dev deps: pytest, pytest-asyncio, ruff

## Key Files

- `src/xcelium_mcp/server.py` — MCP server entry point
- `src/xcelium_mcp/tools/` — tool implementations
- `tests/` — pytest test suite
- `pyproject.toml` — project metadata + ruff config

## Verification Command

```
python -m pytest && python -m ruff check src/
```

(Overridden by `verifyCommand` in plans/prd.json.)

---

### Task: Ralph loop — F-005, F-006 (structural refactor)

**Completed:**
- **F-005** — Split sim_runner.py (842→106 lines stub). New: discovery.py (446), bridge_lifecycle.py (343). Moved utils to shell_utils.py. Updated 13 files.
- **F-006** — Extracted 4 helpers from _run_batch_single: parse_existing_job, build_batch_cmd, launch_nohup_job, watch_pid_and_poll. Added 20 unit tests. Total: 81 tests.

**All 21 prd.json tasks complete.**

---

### Task: Ralph loop — F-020, F-021 (performance optimization)

**Completed:**
- **F-020** — Consolidated batch regression SSH calls: merged 2 base64 writes → 1, per-test grep → single glob grep
- **F-021** — TCP connect retry for bridge: first half uses direct TCP (0 subprocess), fallback to scan_ready_files

---

### Task: Ralph loop — F-019 (simvision extraction)

**Completed:**
- **F-019** — Extracted 7 module-level async functions from register() closures
  - open_database, start_simvision, setup_waveform, live_start, reload_waveform, compare_csv_diff, compare_simvision
  - Closure captures → explicit parameters
  - register() wrappers now thin dispatchers (8-19 lines each)
  - _load_rows helper also extracted to module level

---

### Task: Ralph loop — F-011 through F-018 (security + dedup batch)

**Completed (8 tasks):**

1. **F-011** — Tcl denylist bypass via semicolons/tabs: normalize whitespace, split on `;`/`\n`
2. **F-012** — deposit_signal value regex validation: `_DEPOSIT_VALUE_RE`
3. **F-013** — sim_run duration regex validation: `_DURATION_RE`
4. **F-014** — Replace all 67 `2>/dev/null` with `|| true` for tcsh compat
5. **F-015** — csv_cache CSV output moved to per-user tmp dir
6. **F-016** — `scan_ready_files()` helper extracted, 5 duplicates removed
7. **F-017** — `build_eda_command()` helper for EDA env sourcing
8. **F-018** — `DEFAULT_BRIDGE_PORT`, `BRIDGE_ERRORS`, `_PROTECTED_KEYS` consolidated

**Skipped:** F-019 (simvision extraction), F-020 (SSH consolidation), F-021 (inotifywait) — large refactors needing remote testing

---

### Task: Ralph loop — F-002 through F-010 (security + cleanup batch)

**Completed (6 tasks in single iteration):**

1. **F-002** — `compare_waveforms` injection + fd leak + validate_path
   - Added `_DISPLAY_RE` regex for display param validation
   - Switched simvision launch from bare `&` to `(nohup env ... &)` + `build_redirect`
   - Added `validate_path()` for shm_before/shm_after

2. **F-003** — `screenshot.py` temp file leak
   - Wrapped conversion + read in try/finally for guaranteed cleanup

3. **F-004** — `csv_cache.clear_cache` OrderedDict downgrade
   - Replaced dict comprehension with in-place key deletion

4. **F-008** — `debug_tools.generate_debug_tcl_content` Tcl escaping
   - Added `_tcl_escape()` helper for `"`, `[`, `$`, `\`
   - Signals sanitized, shm_path validated, context_note/labels escaped

5. **F-009** — Narrowed bare `except Exception` in connect/retry loops
   - 4 files: tcl_bridge.py, simvision.py, sim_lifecycle.py, sim_runner.py
   - All now catch `(ConnectionError, asyncio.TimeoutError, OSError, TclError)`
   - Last exception included in timeout error message

6. **F-010** — Dead code cleanup in register() return dicts
   - Removed `generate_debug_tcl_fn` param (unused in simvision)
   - Replaced lambda with `functools.partial` in waveform
   - debug.register returns None

**Remaining:** F-005, F-006 skipped (large structural refactors)

---

## 2026-04-10 - Session Notes

### Task: Ralph loop installation

**What was implemented:**
- Added ruff to dev deps in pyproject.toml
- Installed Ralph same-session mode scaffolding (hooks, commands, plans)

**Files changed:**
- pyproject.toml (ruff dev dep + [tool.ruff] config)
- .claude/hooks/stop-hook.sh (new)
- .claude/settings.json (new)
- .claude/commands/ralph-loop.md (new)
- .claude/commands/ralph-cancel.md (new)
- plans/progress.md (this file)
- plans/guardrails.md (seed signs)
- plans/prd.json (empty backlog)
- .gitignore (Ralph state files)

**Learnings:**
- `python -m pytest` / `python -m ruff` keeps verify command PATH-independent on Windows
- stop-hook.sh uses `eval` for VERIFY_COMMAND to allow `&&` chaining

---
