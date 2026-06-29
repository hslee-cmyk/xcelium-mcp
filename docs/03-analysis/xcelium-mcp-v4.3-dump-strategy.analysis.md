# Gap Analysis: xcelium-mcp v4.3 Dump Strategy (Re-analysis)

> **Date**: 2026-04-07
> **Design**: `docs/02-design/features/xcelium-mcp-v4.3-dump-strategy.design.md`
> **Implementation**: `Todoc/fpga/xcelium-mcp/`
> **Match Rate**: 88% → **93%** (54/58) after G1+G6+passthrough fix | Impact-weighted: 95%

---

## Section-by-Section Results

| Design Section | Items | Matched | Score |
|---------------|:-----:|:-------:|:-----:|
| S1. File structure | 5 | 4 | 80% |
| S2. resolve_sim_params | 4 | 4 | 100% |
| S3. Probe signals | 4 | 4 | 100% |
| S4. _preprocess_setup_tcl | 5 | 5 | 100% |
| S5. _run_batch_single/regression | 7 | 6 | 86% |
| S6. SDF override | 7 | 7 | 100% |
| S7. Bridge dump_window | 1 | 1 | 100% |
| S8. MCP tool schema | 8 | 6 | 75% |
| S9. Unit tests | 17 | 14 | 82% |
| **Total** | **58** | **51** | **88%** |

---

## Remaining Gaps (5 items)

| # | Item | Design Loc | Description | Impact |
|---|------|-----------|-------------|--------|
| G1 | `_generate_probe_reset_tcl()` | S5.3 | Checkpoint restore 후 probe 재설정 함수 미구현 | Medium |
| G6 | extra_args combo warnings | S8.4 | sim_mode/extra_args 오용 경고 미구현 | Low |
| G7 | test_handle_sdf_override_with_guard | S9.1 | async/SSH mock 필요 통합 테스트 | Medium |
| G8 | test_handle_sdf_override_no_guard | S9.1 | async/SSH mock 필요 통합 테스트 | Medium |
| G9 | test_checkpoint_with_dump_depth | S9.1 | G1 구현 후 작성 가능 | Medium |

**New finding**: sim_start dump_depth param은 schema에 추가되었으나 `start_simulation()`에 전달되지 않음 (dead parameter).

---

## Added Features (Design X, Implementation O) — 7 items

| # | Item | Description |
|---|------|-------------|
| A1 | `run_with_dump_window` public | Design은 private, 구현은 public (tools 접근) |
| A2 | timeout param (Bridge dump_window) | `timeout: float = 600` — gate sim 대응 |
| A3 | UserInputRequired graceful catch | sim_discover에서 sdf_info 없이 계속 진행 |
| A4-A7 | 4 additional tests | dedup, custom keep, start_zero, no_dup_custom |

---

## Recommended Actions (-> 91%+)

| Priority | Action | Gap | Effort |
|----------|--------|-----|--------|
| 1 | sim_start: dump_depth를 start_simulation()에 전달 | S8 | 20 min |
| 2 | extra_args combo warnings | G6 | 20 min |
| 3 | _generate_probe_reset_tcl | G1 | 30 min |

Deferred (async test infra): G7, G8, G9

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-07 | 초안 — Phase 1~3 구현 후. 82% (50/61) |
| 0.2 | 2026-04-07 | G2+G3+G4+G5 수정. 90% (55/61) |
| 0.3 | 2026-04-07 | 재분석. 항목 재카운트 58개. sim_start passthrough gap 발견. 88% (51/58), impact-weighted 91% |
| 0.4 | 2026-04-07 | G1+G6+passthrough 수정. 93% (54/58). 잔여: G7-G9 (async test only) |
