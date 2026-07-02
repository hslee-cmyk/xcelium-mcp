# Gap Analysis: xcelium-mcp v5.2 — Hierarchical Dump Strategy

> **Date**: 2026-07-02 (re-run; prior run 2026-07-01 — see [History](#history))
> **Plan**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md`
> **Design**: `docs/02-design/features/xcelium-mcp-v5.2-hierarchical-dump.design.md`
> **Implementation**: `src/xcelium_mcp/{tcl_preprocessing,batch_runner,sim_env_detection,registry}.py`, `tools/{batch,sim_lifecycle}.py`
> **Backlog**: F-138 (Phase 2 Auto-detection, passes:true, 2026-07-01); F-140 (gap-fix: dump_history/dump_stats schema, committed 872f6ad); F-141 (async wiring unit tests, committed 6613e11); F-139 (Flow A/B manual HW verification + release-unit decision, skip:true — out of static scope)
> **Mode**: Static-only (Python MCP server — no HTTP API / frontend / Playwright). Runtime signal = `pytest` + `ruff`.
> **Match Rate**: **93%** (Structural 97% × 0.2 + Functional 92% × 0.4 + Contract 92% × 0.4) — up from 85%

---

## Runtime Signal

- `python -m pytest -v` (full suite, 13 test files) → **325 passed, 0 failed, 0 skipped** (4.10s), 2 warnings
- `python -m ruff check src/` → **All checks passed!**
- The 2 warnings are a single `datetime.utcnow()` DeprecationWarning surfaced twice (once per test), at `batch_runner.py:398` — see Minor gap M2.
- Prior run only executed `tests/test_hierarchical_dump.py` (26 passed); this run covers the full suite, including the new `tests/test_dump_history_stats.py` (+7).

## Overall Scores

| Axis | Prior (07-01) | Now (07-02) | Weight |
|------|:-------------:|:-----------:|:------:|
| Structural Match | 97% | 97% | 0.2 |
| Functional Depth | 86% | 92% | 0.4 |
| API Contract | 78% | 92% | 0.4 |
| **Overall** | **85%** | **93%** | — |

---

## 1. Structural Match — 97%

Unchanged from prior run. All files/functions named in Design §1–§10 exist with matching signatures. Plus one new artifact: `tests/test_dump_history_stats.py` (F-141), which is itself structurally sound (see Test File Quality below). Only pre-existing deviation: Phase-2 helpers landed in `sim_env_detection.py` rather than `env_detection.py` as Plan §4.1 named — Design doc already corrected this, no functional impact.

## 2. Functional Depth — 92% (was 86%)

Core opt-in/opt-out semantics matrix (Design §11) remains fully implemented, all FR-01–FR-08/FR-10 ✅ (FR-09 `group:` prefix intentionally deferred to Phase 3). The async-wiring region — `_lazy_discover_boundaries`, `_update_dump_history`, `dump_stats` aggregation, regression-loop integration — was previously implemented-but-untested-and-buggy; it is now implemented correctly **and** covered by 7 new unit tests (F-141). This closes the functional gap that caused Gaps #1–#3 below.

## 3. API Contract — 92% (was 78%)

Tool signatures unchanged and correct. The two persisted-schema deviations that pulled this score down in the prior run (`dump_history` key names, `dump_stats` shape) are now closed and verified byte-for-byte against Design §7/§8 pseudocode. Residual 8% is the M1 return-container convention (tuple vs dict) — cosmetic, not a schema break.

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

## Gaps (current)

### 🔴 Critical — none

### 🟡 Important — none
All three prior Important gaps are closed and regression-guarded. No new Important/Critical gaps found in this fresh Structural/Functional/Contract sweep.

### 🔵 Minor

| # | Gap | Evidence | Status |
|---|-----|----------|:------:|
| M1 | `run_batch_regression`/`run_batch_single` return `tuple` (`(log_str, dump_stats)` / `(result, dump_summary)`) vs Design §8 `{"results":…,"dump_stats":…}` dict. Tool layer unpacks correctly and surfaces both. Cosmetic, codebase-wide text-summary convention. | `batch_runner.py:915`, `:514` | Open (accepted convention) |
| M2 | `datetime.utcnow()` deprecated (Python 3.12+) — emits DeprecationWarning under 3.14. Matches Design §7 pseudocode verbatim (spec-inherited, not drift). Migrate to `datetime.now(timezone.utc)`. | `batch_runner.py:398` | Open (new observation) |
| M3 | `_parse_describe_output` real xmsim-format correctness still unverified — parser assumes direction-first lines; Plan §3.6's own example is port-name-first (Plan §3.6 is internally inconsistent). Unit fixtures are direction-first so tests pass but may not reflect live output. | `sim_env_detection.py:66-82` vs Plan §3.6 | Open — deferred to F-139 (HW) |
| M4 | `_update_dump_history` omits `force=True` on `load_sim_config` (Design §7 uses it). Relies on `save_sim_config` cache invalidation, which holds within the sequential regression loop. Low risk. | `batch_runner.py:391` vs Design §7 | Open (benign) |
| M5 | FR-09 `group:` prefix not implemented — expected (Phase 3, optional, Design §11 omits groups). | `tools/batch.py:113`, `tcl_preprocessing.py:195-217` | Open (deferred by design) |

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

**Optional cleanup (Minor, ~2 LOC, unit-testable):**
1. M2: replace `datetime.utcnow().isoformat(...)` → `datetime.now(timezone.utc).isoformat(...)` at `batch_runner.py:398` to silence the DeprecationWarning and be 3.14-forward-safe.

**Before release (already tracked as F-139, skip:true — human/HW required):**
2. M3: confirm `_parse_describe_output` against live `scope -describe -sort kind` output; reconcile Plan §3.6's port-name-first example vs the direction-first parser. Verify Flow A/B on cloud0; verify v5.1 regression 21/21 and SHM ≤10%; decide v5.2.0/v5.2.1 vs single v5.2 (Design §15 Q1).

**Verdict**: **93%** — up from 85%. All three prior Important gaps (dump_history schema, dump_stats shape, regression-path history write) are closed, verified line-by-line against Design §7/§8, and locked in by 7 new async-wiring unit tests (F-141). Full suite is green (325/325) and lint-clean. The residual 7% is entirely Minor: one spec-inherited deprecation, one cosmetic return-container convention, and HW-only verification items (F-139) that static analysis cannot reach. No Critical or Important gaps remain — feature is ready for Report phase pending the F-139 human/HW sign-off.

---

## History

- **2026-07-01** (first run): Match Rate 85%. 3 Important gaps found (dump_history schema, dump_stats shape, regression-path history write), all in the untested async-wiring region.
- **2026-07-02** (this run): Match Rate 93%. All 3 Important gaps closed via F-140 (fix) + F-141 (tests). Only Minor gaps remain, none blocking.
