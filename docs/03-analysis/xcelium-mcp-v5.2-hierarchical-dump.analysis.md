# Gap Analysis: xcelium-mcp v5.2 — Hierarchical Dump Strategy

> **Date**: 2026-07-02 (3rd run — post-verbosity/architecture-refactor re-verification; prior runs 2026-07-01 and 2026-07-02 — see [History](#history))
> **Plan**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md`
> **Design**: `docs/02-design/features/xcelium-mcp-v5.2-hierarchical-dump.design.md`
> **Implementation**: `src/xcelium_mcp/{tcl_preprocessing,batch_runner,sim_env_detection,registry,batch_polling}.py`, `tools/{batch,sim_lifecycle}.py`
> **Backlog since 93% baseline**: F-142 (datetime.utcnow deprecation fix, closes M2); F-143 (`_update_dump_history` force=True, closes M4); F-144–F-173 (bug/security/architecture/verbosity code-analyzer review backlog — F-146/153/155/156/157/158/159/161/162/164/166/167/171 touch files in this feature's scope; F-160 skip:true, deferred)
> **Mode**: Static-only (Python MCP server — no HTTP API / frontend / Playwright). Runtime signal = `pytest` + `ruff`.
> **Match Rate**: **94%** (Structural 97% × 0.2 + Functional 95% × 0.4 + Contract 92% × 0.4 = 94.2%) — up from 93%

---

## Runtime Signal

- `python -m pytest -q` (full suite) → **472 passed, 0 failed, 0 skipped, 0 warnings** (4.4s) — independently re-run and confirmed, not trusted from changelog.
- `python -m ruff check src/` → **All checks passed!**
- The 2 `datetime.utcnow()` DeprecationWarnings present at the 93% baseline are now **gone** — direct runtime confirmation that M2/F-142 is closed (in addition to the source-line check below).
- Test count grew 325 → 472 (+147) across the whole repo through the F-144–173 backlog; the portion directly attributable to this feature's files is the pure-function extractions (F-155 `classify_regression_results`/`aggregate_dump_stats`, F-156 `_read_job_status`, F-167 `_history_scopes`) each gaining direct unit coverage.

## Overall Scores

| Axis | 07-01 | 07-02 (93%) | 07-02 (94%, this run) | Weight |
|------|:-----:|:-----------:|:---------------------:|:------:|
| Structural Match | 97% | 97% | 97% | 0.2 |
| Functional Depth | 86% | 92% | **95%** | 0.4 |
| API Contract | 78% | 92% | 92% | 0.4 |
| **Overall** | **85%** | **93%** | **94%** | — |

---

## 1. Structural Match — 97%

Unchanged from prior run. All files/functions named in Design §1–§10 exist with matching signatures. Plus one new artifact: `tests/test_dump_history_stats.py` (F-141), which is itself structurally sound (see Test File Quality below). Only pre-existing deviation: Phase-2 helpers landed in `sim_env_detection.py` rather than `env_detection.py` as Plan §4.1 named — Design doc already corrected this, no functional impact.

## 2. Functional Depth — 95% (was 92%)

Core opt-in/opt-out semantics matrix (Design §11) remains fully implemented, all FR-01–FR-08/FR-10 ✅ (FR-09 `group:` prefix intentionally deferred to Phase 3). The async-wiring region — `_lazy_discover_boundaries`, `_update_dump_history`, `dump_stats` aggregation, regression-loop integration — is implemented correctly and covered by unit tests (F-141), and now additionally: M2 and M4 (both Minor gaps from the 93% run) are closed (F-142/F-143), and the `_update_dump_history`/`dump_stats` persisted schemas were independently re-verified byte-for-byte unchanged through four subsequent refactors (F-155 `classify_regression_results`/`aggregate_dump_stats` extraction, F-156 `_read_job_status` extraction, F-157 `launch_nohup_job` reuse, F-167 `_history_scopes` extraction). No schema drift from any of the 14 F-14x/15x/16x refactors that touch this feature's files.

## 3. API Contract — 92% (flat)

Tool signatures unchanged and correct. `sim_batch_run`/`sim_regression`'s dump_depth/sdf_corner/dump_scopes validation was deduplicated into `_validate_run_params()` (F-164) — re-verified that accepted values and error strings are unchanged from before the dedup (`tools/batch.py`: `dump_depth ∈ {boundary, all}`, `sdf_corner ∈ {min,max,typ}`, `dump_scopes` value ∈ `{all,boundary,skip}`, key regex `^[\w.*]+$`). Residual 8% is still the M1 return-container convention (tuple vs dict) — cosmetic, not a schema break, unchanged since the 93% run.

---

## Verdict on Prior Important Gaps #1–#3 — all CLOSED (independently re-verified)

### Gap #1 — `dump_history` persisted schema — CLOSED ✅
`batch_runner.py:383-402` (`_update_dump_history`) now matches Design §7 pseudocode:
- Key is `last_dump_summary` (was `dump_summary`) — `batch_runner.py:394`
- `updated_at` present, ISO seconds precision — `batch_runner.py:398`
- `scope_overrides` stripped via dict comprehension — `batch_runner.py:395-396`

Verified by `test_update_dump_history_writes_last_dump_summary_schema` (`test_dump_history_stats.py:41-73`): asserts `"dump_summary" not in entry`, exact `last_dump_summary` contents, `scope_overrides` absent, `updated_at` is `datetime.fromisoformat`-parseable.

Two benign deviations from §7 pseudocode remain (see M2/M4): impl uses `load_sim_config(sim_dir) or {}` instead of `force=True` + explicit `None` check, and persists `dump_scopes or {}` (an improvement over the spec's bare `dump_scopes`) — codified by `test_update_dump_history_defaults_dump_scopes_to_empty_dict`.

### Gap #2 — `dump_stats` aggregation shape — CLOSED ✅
`batch_runner.py:887-913` now matches Design §8 pseudocode exactly: `max`/`min` are `{"test":…, "total":…}` dicts (`:910-911`), `per_test` entries carry `total`/`top_boundary`/`block_count` (`:892-898`), `suggestions` are per-test named on `total > avg×2` (`:903-907`).

Verified by `test_regression_updates_dump_history_and_dump_stats_shape` (`:224-232`): with totals T1=10/T2=5/T3=50 (avg 21.67), confirms `max == {"test":"T3","total":50}`, `min == {"test":"T2","total":5}`, old bare-int keys absent, exactly one T3 suggestion fires.

### Gap #3 — regression path never persisted `dump_history` — CLOSED ✅
`run_batch_regression` now calls `_update_dump_history` inside the per-test loop — `batch_runner.py:706-710`, guarded by `if test_dump_summary is not None`, with a comment citing Plan §3.2 "항상 갱신".

Verified by the same regression test (`:218-220`): `[c[1] for c in history_calls] == ["T1","T2","T3"]` — history synced for every test.

---

## Verdict on Prior Minor Gaps M2/M4 — CLOSED (this run, independently re-verified)

### M2 — `datetime.utcnow()` deprecation — CLOSED ✅
`batch_runner.py:429` now reads `datetime.now(timezone.utc).isoformat(timespec="seconds")` (was `datetime.utcnow()`), `timezone` imported at `:20` (F-142). Runtime-confirmed: the 2 `DeprecationWarning`s present in the 93%-run `pytest` output are gone in this run's full-suite run (472 passed, 0 warnings).

### M4 — `_update_dump_history` missing `force=True` — CLOSED ✅
`batch_runner.py:422` now calls `load_sim_config(sim_dir, force=True) or {}` (was `load_sim_config(sim_dir) or {}`), matching Design §7 pseudocode exactly (F-143).

### Refactor-survival check (F-155/156/157/167) — no schema drift
`_update_dump_history` (`batch_runner.py:414-433`) and `aggregate_dump_stats` (`:638-669`, extracted from `run_batch_regression` by F-155) were re-read line-by-line against Design §7/§8 pseudocode after the pure-function extraction and dump-history-lookup dedup (`_history_scopes`, F-167, `:436-447`, a read-side-only extraction). All persisted keys (`last_dump_summary`, `dump_scopes`, `updated_at`; `max`/`min` as `{"test":…,"total":…}` dicts; `per_test` with `total`/`top_boundary`/`block_count`) are byte-for-byte unchanged from the 93%-run verification. `run_batch_regression` still calls `_update_dump_history` inside the per-test loop (`:839`), guarded by `if test_dump_summary is not None` (`:835`) — Gap #3 stays closed.

### Investigated and refuted: "orphan `.done` watcher" (N1 candidate, not promoted to a gap)
A first-pass review flagged that `launch_nohup_job`'s fire-and-forget PID watcher (`batch_runner.py:359-361`, touches `{log_file}.done` on process exit) might go unconsumed on the regression path. Re-reading `batch_polling.py:14-48` (`poll_batch_log`) shows this is incorrect: `poll_batch_log` checks the same `{log_file}.done` path as one of its completion signals in its polling loop, and unconditionally `rm -f`s it before returning — and the regression loop's `poll_batch_log(test_log, 600)` call (`batch_runner.py:877`) is passed the exact same `test_log` path used as `log_file` in the preceding `launch_nohup_job(sim_dir, run_cmd, test_log, ...)` call (`:864-874`). The `.done` file is both checked and cleaned up. **Not a real gap** — recorded here so it isn't independently re-flagged by a future review.

---

## Gaps (current)

### 🔴 Critical — none

### 🟡 Important — none
All three prior Important gaps are closed and regression-guarded. No new Important/Critical gaps found across three analysis runs (85%→93%→94%) and 14 subsequent refactors.

### 🔵 Minor

| # | Gap | Evidence | Status |
|---|-----|----------|:------:|
| M1 | `run_batch_regression`/`run_batch_single` return `tuple` (`(log_str, dump_stats)` / `(result, dump_summary)`) vs Design §8 `{"results":…,"dump_stats":…}` dict. Tool layer unpacks correctly and surfaces both. Cosmetic, codebase-wide text-summary convention. | `batch_runner.py:960`, `:554` | Open (accepted convention, unchanged) |
| ~~M2~~ | ~~`datetime.utcnow()` deprecated~~ | `batch_runner.py:429` | **CLOSED (F-142)** |
| M3 | `_parse_describe_output` real xmsim-format correctness still unverified — parser assumes direction-first lines; Plan §3.6's own example is port-name-first (Plan §3.6 is internally inconsistent). Unit fixtures are direction-first so tests pass but may not reflect live output. | `sim_env_detection.py:66-82` vs Plan §3.6 | Open — deferred to F-139 (HW) |
| ~~M4~~ | ~~`_update_dump_history` omits `force=True`~~ | `batch_runner.py:422` | **CLOSED (F-143)** |
| M5 | FR-09 `group:` prefix not implemented — expected (Phase 3, optional, Design §11 omits groups); key regex `^[\w.*]+$` (`tools/batch.py`) rejects `:`. | `tools/batch.py:24`, `tcl_preprocessing.py:195-217` | Open (deferred by design) |

### Test File Quality — `tests/test_dump_history_stats.py` (F-141)

Well-targeted at the exact "async-wiring blind spot" the prior analysis named:
- Proper isolation: `AsyncMock` for `load_sim_config`/`save_sim_config`; `_boundaries_from_json` exercised against a real on-disk temp netlist (`tmp_path`), not over-mocked.
- Asserts the *negative* contract (old `dump_summary`/`max_total_signals` keys must be absent) — this is what actually catches regressions back to the pre-F-140 schema.
- Covers all four `_lazy_discover_boundaries` early-return branches: parse-success, persist-when-flagged, no-`netlist_info`, missing-file.
- One acceptable seam: the regression-path test mocks `_update_dump_history` itself, so it verifies Gap #3 (called per test) but not the persisted schema *through* the regression path — that's covered independently by the two direct `_update_dump_history` tests. Combined coverage is sound.

---

## §8 Success Criteria (Plan) — item-by-item

| Metric | Target | Status | Evidence |
|--------|--------|:------:|----------|
| Unit test coverage ≥90% for `_resolve_probe_signals` | ✅ Met | All branches covered; async wiring now also covered |
| Integration test — 3 scenarios PASS | ⚠️ Partial | Requires cloud0; no integration tests in repo — deferred to F-139 |
| v5.1 regression 21/21 PASS | ⚠️ Partial | Not verifiable from unit suite alone; backward-compat unit tests pass |
| SHM 크기 감소율 ≤10% | ❌ Not Met (statically) | Requires real gate sim — F-139 |
| Backward compat | ✅ Met | `test_boundary_no_block_boundaries` returns v5.1 `signals` path |
| Security — dump_scopes injection 차단 | ✅ Met | Tool-layer regex + `ValueError` in resolver + `_SCOPE_PATH_RE` guard in Flow A |

---

## Recommended Actions

**Closed this run — no action needed:**
1. ~~M2~~: closed by F-142 (`datetime.now(timezone.utc)`).
2. ~~M4~~: closed by F-143 (`force=True`).

**Optional, non-blocking:**
3. N1-candidate (investigated, refuted — see above): no action; documented so it isn't re-flagged.

**Before release (already tracked as F-139, skip:true — human/HW required):**
4. M3: confirm `_parse_describe_output` against live `scope -describe -sort kind` output; reconcile Plan §3.6's port-name-first example vs the direction-first parser. Verify Flow A/B on cloud0; verify v5.1 regression 21/21 and SHM ≤10%; decide v5.2.0/v5.2.1 vs single v5.2 (Design §15 Q1).

**Verdict**: **94%** — up from 93% (85% at first run). All three prior Important gaps stay closed and regression-guarded across three analysis runs. This run additionally closes both remaining fixable Minor gaps (M2, M4) and independently re-verifies that 14 subsequent bug/security/architecture/verbosity refactors (F-144–F-173) introduced **zero drift** in the `dump_history`/`dump_stats` persisted schema or the `sim_batch_run`/`sim_regression` validation contract — every function/file the Design doc names in scope was re-read against the current source. Full suite is green (472/472, 0 warnings) and lint-clean. The residual 6% is entirely Minor: one cosmetic return-container convention (M1), one deferred-by-design feature (M5, FR-09 `group:` prefix), and HW-only verification items (M3, F-139) that static analysis cannot reach. No Critical or Important gaps remain — feature is ready for Report phase pending the F-139 human/HW sign-off (unchanged from the 93% run's recommendation).

---

## History

- **2026-07-01** (first run): Match Rate 85%. 3 Important gaps found (dump_history schema, dump_stats shape, regression-path history write), all in the untested async-wiring region.
- **2026-07-02** (2nd run): Match Rate 93%. All 3 Important gaps closed via F-140 (fix) + F-141 (tests). Only Minor gaps remain, none blocking.
- **2026-07-02** (3rd run, post-refactor re-verification): Match Rate 94%. M2 (datetime.utcnow) and M4 (force=True) closed via F-142/F-143. Re-verified zero schema/contract drift through 14 subsequent refactors (F-144–F-173) touching this feature's files — F-155/156/157/167 pure-function extractions around `dump_history`/`dump_stats`, F-161 `resolve_sim_dir` relocation (discovery.py→registry.py, confirmed not Design-referenced), F-164 validation dedup, F-166 wrapper removal. One candidate new gap ("orphan `.done` watcher") investigated and refuted with file:line evidence — not promoted. Residual 6% is M1 (cosmetic)/M3 (HW-only, F-139)/M5 (deferred by design).
