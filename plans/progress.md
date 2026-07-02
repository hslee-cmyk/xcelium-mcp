
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
