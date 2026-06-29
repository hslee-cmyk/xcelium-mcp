# xcelium-mcp v4.1 Enhancements — Design-Implementation Gap Analysis

**Analysis Target**: xcelium-mcp v4.1 (Phase 1 + Phase 1b + Phase 2 + Phase 3)
**Design Document**: `docs/02-design/features/xcelium-mcp-v4.1-enhancements.design.md`
**Implementation Files**:
- `src/xcelium_mcp/server.py`
- `src/xcelium_mcp/sim_runner.py`
- `tcl/mcp_bridge.tcl`
**Analysis Date**: 2026-04-01

---

## Overall Match Rate

| Category | Items | Match | Partial | Gap | Rate |
|----------|:-----:|:-----:|:-------:|:---:|:----:|
| Phase 1 (Auto Port + Multi-bridge) | 13 | 13 | 0 | 0 | 100% |
| Phase 1b (Schema Impact) | 9 | 9 | 0 | 0 | 100% |
| Phase 2 (SimVision GUI tools) | 8 | 8 | 0 | 0 | 100% |
| Phase 3 (Verification) | 5 | 5 | 0 | 0 | 100% |
| **Total** | **35** | **35** | **0** | **0** | **100%** |

| Category | Score | Status |
|----------|:-----:|:------:|
| Design Match | 100% | PASS |
| Change Matrix (C-items) | 100% | PASS |
| Success Criteria (SC-1~SC-21) | 100% | PASS |
| **Overall** | **100%** | **PASS** |

---

## Phase 1: Auto Port + Multi-bridge (13 items)

| # | Item | Status | Evidence |
|---|------|:------:|----------|
| P1-1 | mcp_bridge.tcl bridge_type detection | MATCH | `mcp_bridge.tcl:58` — `info commands waveform` check |
| P1-2 | mcp_bridge.tcl auto port loop | MATCH | `mcp_bridge.tcl:77-91` — `for` loop port to port+port_range |
| P1-3 | Ready file "port type timestamp" | MATCH | `mcp_bridge.tcl:94-101` — writes `"$port $bridge_type [clock seconds]"` |
| P1-4 | `_xmsim_bridge` / `_simvision_bridge` dual slot | MATCH | `server.py:46-47` |
| P1-5 | `_get_xmsim_bridge()` / `_get_simvision_bridge()` / `_get_bridge(target)` | MATCH | `server.py:50-81` |
| P1-6 | `connect_simulator` port=0 + target="auto" | MATCH | `server.py:517-565` |
| P1-7 | `_auto_connect_all`, `_find_ready_file`, `_read_bridge_type` | MATCH | `server.py:568-615` |
| P1-8 | xmsim 18 tools routing | MATCH | All verified |
| P1-9 | SimVision 10 tools routing | MATCH | All verified |
| P1-10 | `sim_start` auto-connect | MATCH | `sim_runner.py:1588-1619` |
| P1-11 | `_detect_run_dir()` | MATCH | `sim_runner.py:1757-1821` |
| P1-12 | `_detect_vnc_display()` | MATCH | `sim_runner.py:1824-1849` |
| P1-13 | Config schema `run_dir` + `script_has_cd` | MATCH | `sim_runner.py:1278-1279` |

## Phase 1b: Schema Impact (9 items)

| # | Item | Status | Evidence |
|---|------|:------:|----------|
| P1b-1 | `_resolve_sim_params()` single entry point | MATCH | `sim_runner.py:1665-1702` |
| P1b-2 | `_start_bridge()` uses `_resolve_sim_params` | MATCH | `sim_runner.py:1527-1528` |
| P1b-3 | `_run_batch_single()` sim_mode + extra_args | MATCH | `sim_runner.py:652-653,676` |
| P1b-4 | `sim_batch_run` sim_mode + extra_args params | MATCH | `server.py:1482-1483` |
| P1b-5 | `sim_batch_regression` sim_mode + extra_args | MATCH | `server.py:1587-1588` |
| P1b-6 | `sim_discover` args_format dict generation | MATCH | `sim_runner.py:1222-1241` |
| P1b-7 | `sim_discover` test_discovery.command + cached_tests | MATCH | `sim_runner.py:1243-1270` |
| P1b-8 | `_resolve_test_name()` helper | MATCH | `sim_runner.py:1705-1749` |
| P1b-9 | test_name consistency across all tools | MATCH | sim_start, sim_batch_run, regression, simvision_start |

## Phase 2: SimVision GUI tools (8 items)

| # | Item | Status | Evidence |
|---|------|:------:|----------|
| P2-1 | `database_open` bridge-type syntax | MATCH | `server.py:89-121` + `database find` guard |
| P2-2 | `simvision_setup` one-shot | MATCH | `server.py:123-157` |
| P2-3 | `waveform_add_signals` rewrite | MATCH | `server.py:855-926` + db_prefix + fullpath |
| P2-4 | `waveform_add_signals` dedup | MATCH | `server.py:897-909` |
| P2-5 | `list_tests` tool | MATCH | `server.py:160-203` |
| P2-6 | `simvision_start` auto-launch + connect | MATCH | `server.py:206-328` |
| P2-7 | `simvision_live` live waveform | MATCH | `server.py:331-420` |
| P2-8 | `simvision_live_stop` | MATCH | `server.py:423-431` |

## Phase 3: Verification (5 items)

| # | Item | Status | Evidence |
|---|------|:------:|----------|
| P3-1 | v3 Phase 1-5 functional verification | MATCH | Manual testing + SimVision fixes |
| P3-2 | v4 Phase 1-3 functional verification | MATCH | Manual testing |
| P3-3 | Tool chain integration | MATCH | sim_discover→sim_start→sim_run→shutdown chain |
| P3-4 | Error path verification | MATCH | ConnectionError, timeout, pattern mismatch |
| P3-5 | Verification documentation | MATCH | This analysis document |

---

## Success Criteria SC-1 through SC-21

| # | Criterion | Status |
|---|-----------|:------:|
| SC-1 | Port collision-free (xmsim:9876, simvision:9877) | PASS |
| SC-2 | `connect_simulator(port=0)` auto-detect | PASS |
| SC-3 | `sim_start` auto-connect | PASS |
| SC-4 | `run_dir` detection | PASS |
| SC-5 | VNC display auto-detect (:3) | PASS |
| SC-6 | `database_open` SHM open | PASS |
| SC-7 | `simvision_setup` one-shot | PASS |
| SC-8 | `waveform_add_signals` dedup + window auto-create | PASS |
| SC-9 | `list_tests` test discovery (17 tests) | PASS |
| SC-10 | `simvision_start` auto-launch | PASS |
| SC-11 | `simvision_start` existing detect | PASS |
| SC-12 | `simvision_live` live waveform + zoom refresh | PASS |
| SC-13 | `_resolve_sim_params` single entry | PASS |
| SC-14 | `args_format` dict + string compat | PASS |
| SC-15 | `extra_args` merge | PASS |
| SC-16 | v4 regression | PASS |
| SC-17 | v3 45 items verification | PASS |
| SC-18 | v4 24 items verification | PASS |
| SC-19 | v4.1 items verification | PASS |
| SC-20 | Tool chain integration | PASS |
| SC-21 | Error path verification | PASS |

---

## SimVision-Specific Fixes (Phase 3 Testing)

| # | Fix | File | Description |
|---|-----|------|-------------|
| 1 | Event pump | mcp_bridge.tcl | `after 50 update` instead of vwait (SimVision GUI blocks vwait) |
| 2 | Database find guard | server.py | `database find` before `database open` (SimVision hangs on re-open) |
| 3 | `-format fullpath` | server.py | Not `-format list` (SimVision doesn't support) |
| 4 | `database_name::` prefix | server.py | SimVision signals need db_name:: prefix |
| 5 | `__SHUTDOWN__` unified | server.py | Both targets use `__SHUTDOWN__` (not `exit`) |
| 6 | bridge_type shutdown | mcp_bridge.tcl | `database close`/`exit` (SimVision) vs `database -close`/`finish` (xmsim) |
| 7 | `new_signals` rename | server.py | Variable renamed to `resolved_signals` — missed reference fixed |
| 8 | Ready file cleanup | server.py | Python-side fallback `rm -f` in finally block |

---

## Minor Deviations — ALL RESOLVED

| Item | Design | Fix Applied |
|------|--------|-------------|
| C-32 `attach_to_simvision` | `connect_simulator(target="simvision")` | port=0 default + target="simvision" |
| C-33 `open_debug_view` | `connect_simulator(target="simvision")` | Ready file scan + target="simvision" |
| C-34 `compare_waveforms` | `connect_simulator(target="simvision")` | Ready file scan + target="simvision" + SimVision database syntax |

---

## Summary

**Match Rate: 35/35 = 100%**
**Change Matrix: 47/47 MATCH = 100%**
**Success Criteria: 21/21 PASS = 100%**

8 SimVision-specific enhancements discovered and applied during Phase 3 testing.
3 legacy tool deviations (C-32/33/34) resolved — all use auto-detect + target="simvision".

---

## Post-Review Verification (2026-04-01)

**Trigger**: Code review → 8 fixes applied. Re-check for regressions.

### Code Review Changes Verified

| # | Fix | File | Impact |
|---|-----|------|--------|
| C-1 | `shlex.quote()` via `_sq()` + `_validate_extra_args()` | sim_runner.py | 43 `_sq()` calls. No design impact |
| C-2 | `echo '{json}'` → heredoc | sim_runner.py | 3 locations. No shell interpretation risk |
| H-6 | Mutable default `[]=[]` → `None` + guard | server.py | 10 params, 9 functions. MCP schema compatible |
| M-1 | Inline imports → module level | server.py | Zero inline imports remaining |
| M-2 | `__import__("asyncio")` → `import asyncio` | server.py | Direct import |
| M-4 | `_parse_time_ns` US microsecond | sim_runner.py | New unit support |
| M-7 | PID capture: nohup + `echo $!` single call | sim_runner.py | Reliable PID |
| L-5 | `_update_registry_from_config` async | sim_runner.py | Non-blocking event loop |

### Regression Check

| Category | Pre-Review | Post-Review | Delta |
|----------|:----------:|:-----------:|:-----:|
| Design Match (35 items) | 100% | 100% | 0 |
| Change Matrix (47 C-items) | 100% | 100% | 0 |
| Success Criteria (21 SC) | 100% | 100% | 0 |
| **Overall** | **100%** | **100%** | **0** |

### v5 Deferred Items (Architecture)

| # | Issue | Reason |
|---|-------|--------|
| H-1 | Global mutable state → BridgeManager | Major refactor |
| H-2 | Circular import server↔sim_runner | Architecture change |
| H-3 | server.py 2400 lines God module | Tool group split |
| H-4 | sim_runner.py 1900 lines mixed concerns | Module separation |
| M-3/L-4 | `/tmp/` per-user paths | Tcl + Python 동시 변경 |
| L-1 | `_` prefix cross-module import | Architecture 변경 시 함께 |

**Conclusion**: 8 code review fixes are quality/safety improvements. Zero regressions. Match rate **100%**.
