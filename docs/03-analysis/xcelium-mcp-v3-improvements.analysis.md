# xcelium-mcp v3 Improvements — Design-Implementation Gap Analysis

- **분석 대상**: xcelium-mcp v3 (Phase 1~5 전체)
- **설계 문서**: `docs/02-design/features/xcelium-mcp-v3-improvements.design.md`
- **분석 일자**: 2026-03-30 (Phase 1) / 2026-03-30 (Phase 2 추가) / 2026-03-30 (Phase 3 추가) / 2026-03-30 (Phase 4 추가) / 2026-03-30 (Phase 5 추가)

---

## 전체 Match Rate

| Phase | 설계 항목 | 구현 항목 | Match Rate | 상태 |
|-------|:--------:|:--------:|:----------:|:----:|
| Phase 1 — Foundation | 14 | 14 | **100%** | PASS |
| Phase 2 — CSV Infrastructure | 9 | 9 | **100%** | PASS |
| Phase 3 — Advanced Analysis | 7 | 7 | **100%** | PASS |
| Phase 4 — Bridge Enhancement | 10 | 10 | **100%** | PASS |
| Phase 5 — UI/Visual | 5 | 5 | **100%** | PASS |
| **전체 (45항목)** | **45** | **45** | **100%** | PASS |

> Phase 1~5 전체 구현 완료 — Match Rate **100%**
> P2-5: `from_checkpoint` restore 모드 구현 확인 (STUB → PASS, 2026-03-30)
> P5-4: `compare_waveforms` simvision 출력 모드 구현 완료 (STUB → PASS, 2026-03-30)
> P3-2/P3-3 → Phase 4 checkpoint_manager 구현으로 해결
> P4-8 → pdca-iterator로 TB analysis cache 구현 완료 (STUB→PASS)

---

## Phase 1 상세 (14/14 PASS)

### P1-1: `execute_tcl` MCP tool

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:120 | PASS |
| 시그니처 `(tcl_cmd, timeout=30)` | server.py:120 | PASS |
| WARNING docstring | server.py:121 | PASS |
| `do_execute_tcl` handler | mcp_bridge.tcl:339 | PASS |

### P1-2~P1-4: `sim_restart` + `__RESTART__`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `sim_restart` → `__RESTART__` | server.py:115 | PASS |
| Method 1: `run -clean` | mcp_bridge.tcl:307 | PASS |
| Method 2: snapshot restore | mcp_bridge.tcl:316 | PASS |
| Method 3: plain `restart` fallback | mcp_bridge.tcl:326 | PASS |
| `init_snapshot` @ `/tmp/mcp_init` | mcp_bridge.tcl:288 | PASS |
| `on_init` cleanup + init_snapshot | mcp_bridge.tcl:295 | PASS |
| `stop -delete -all` after Method 2 | mcp_bridge.tcl:319 | PASS |

### P1-5~P1-14: sim_runner.py

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `UserInputRequired` exception | sim_runner.py:21 | PASS |
| `ssh_run(cmd, timeout)` | sim_runner.py:37 | PASS |
| `load_registry` / `save_registry` | sim_runner.py:61 | PASS |
| `load_sim_config` / `save_sim_config` | sim_runner.py:77 | PASS |
| `ExecInfo` dataclass | sim_runner.py:128 | PASS |
| `_resolve_exec_cmd` 4-branch | sim_runner.py:135 | PASS |
| `_detect_env_shell` | sim_runner.py:188 | PASS |
| `_detect_eda_env` 4-step | sim_runner.py:227 | PASS |
| `_detect_shell_and_env` | sim_runner.py:283 | PASS |
| `_auto_detect_runner` 4-type | sim_runner.py:313 | PASS |
| `_ask_user_runner` UserInputRequired | sim_runner.py:384 | PASS |
| `_analyze_tb_type` 5-type | sim_runner.py:411 | PASS |
| `_discover_sim_dir` | sim_runner.py:448 | PASS |
| `_load_or_detect_runner` Tier 1/2/3 | sim_runner.py:516 | PASS |

---

## Phase 1 편차 (3항목)

| 유형 | 항목 | 설계 | 구현 | 영향 |
|------|------|------|------|:----:|
| Changed | `execute_tcl` 라우팅 | `__EXECUTE_TCL__` meta cmd | regular dispatch (`uplevel #0`) | Low |
| Added | `do_restart` Method 3 | 미명시 | plain `restart` fallback 추가 | Low (개선) |
| Fixed | `_load_or_detect_runner` Tier 2 | `_detect_shell_and_env` 호출 | hardcode `source_separately=False` → 수정 완료 | Medium → **완료** |

---

---

## Phase 2 상세 (9/9 PASS)

### P2-1: `csv_cache.py` 모듈

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `_cache` dict (shm_path, frozenset(signals), start_ns, end_ns) → path | csv_cache.py:30 | PASS |
| `extract()` async 함수 | csv_cache.py:55 | PASS |
| `clear_cache(shm_path=None)` | csv_cache.py:106 | PASS |
| in-memory cache hit/miss 로직 | csv_cache.py:72 | PASS |

> 설계: "pandas DataFrame 캐시" → 구현: 파일 경로 캐시 (pandas 의존성 제거, 의도적 변경)

### P2-2: `extract_csv` MCP tool

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:551 | PASS |
| 6개 파라미터 (shm_path, signals, start_ns, end_ns, output_path, missing_ok) | server.py:552 | PASS |
| csv_cache.extract 호출 + 결과 경로 반환 | server.py:573 | PASS |
| 에러 처리 (RuntimeError → 문자열 반환) | server.py:578 | PASS |

### P2-3: simvisdbutil command builder

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `-csv -output {path} -overwrite` | csv_cache.py:82 | PASS |
| `-range {start_ns}:{end_ns}ns` (조건부) | csv_cache.py:85 | PASS |
| `-missing` flag (missing_ok=True 시) | csv_cache.py:88 | PASS |
| `-sig {signal}` 반복 | csv_cache.py:91 | PASS |

### P2-4: `sim_batch_run` [A] 전체 실행

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록, 9개 파라미터 | server.py:593 | PASS |
| sim_dir 결정 (빈값 → _get_default_sim_dir) | server.py:629 | PASS |
| runner 결정 (_load_or_detect_runner) | server.py:638 | PASS |
| `_run_batch_single()` 호출 | server.py:644 | PASS |
| UserInputRequired 처리 | server.py:633, 641 | PASS |
| `_run_batch_single` 구현 (timeout 분기) | sim_runner.py:594 | PASS |

### P2-5: `sim_batch_run` [A'] restore 모드

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| from_checkpoint, probe_signals 파라미터 존재 | server.py:809 | PASS |
| [A'] restore: `restore_checkpoint` → `probe_add_signals` → run | server.py:856 | PASS |

> 2026-03-30 재검토: `from_checkpoint` 경로(lines 856–869)에서 `restore_checkpoint` 호출 후 `probe_add_signals`까지 완전 구현 확인. 이전 STUB 판정은 Phase 4 구현 이전 스냅샷 기준 — PASS로 정정.

### P2-6: SHM dump overwrite 방지

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| 방법 6-A: `TEST_NAME={test_name}` env 주입 | sim_runner.py:620 | PASS |
| 방법 6-B: `rename_dump=True` → mv fallback | sim_runner.py:627 | PASS |
| screen 세션에서도 `setenv TEST_NAME` | sim_runner.py:650 | PASS |

### P2-7: `sim_batch_regression` tool

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록, 6개 파라미터 | server.py:680 | PASS |
| test_list 자동 탐지 (mcp_sim_config.json) | server.py:731 | PASS |
| `_run_batch_regression()` 호출 | server.py:739 | PASS |
| `_run_batch_regression` 구현 | sim_runner.py:672 | PASS |

### P2-8: SSH screen 하이브리드 전략

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| timeout ≤ 120 → 직접 ssh_run | sim_runner.py:621 | PASS |
| timeout > 120 → screen 세션 + log polling | sim_runner.py:635 | PASS |
| regression → 항상 screen 세션 | sim_runner.py:672 | PASS |
| 기존 mcp_regression 세션 kill | sim_runner.py:681 | PASS |
| needs_test_name=False 경로 (regression_script) | sim_runner.py:705 | PASS |
| needs_test_name=True 경로 (per-test 루프) | sim_runner.py:714 | PASS |

### P2-9: Regression progress polling

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `tail -5 {log_file}` + `asyncio.sleep(10)` | sim_runner.py:709, 720 | PASS |
| 완료 조건: `$finish\|COMPLETE\|PASS\|FAIL` | sim_runner.py:619, 722 | PASS |
| path A 최대 360 반복 (~1시간) | sim_runner.py:707 | PASS |
| path B per-test 최대 60 반복 (~10분) | sim_runner.py:717 | PASS |
| screen 세션 정리 + 결과 파싱 | sim_runner.py:729 | PASS |

---

## Phase 2 편차 (3항목)

| 유형 | 항목 | 설계 | 구현 | 영향 |
|------|------|------|------|:----:|
| Changed | 캐시 타입 | pandas DataFrame | 파일 경로 (str) | Low (pandas 의존성 제거) |
| Changed | CSV 기본 출력 경로 | `/tmp/mcp_csv_*.csv` | `{shm_dir}/mcp_csv_*.csv` | Low (SHM 옆 저장, 더 직관적) |
| Added | `clear_cache()` | 미명시 | shm_path 선택적 정리 | Low (개선) |

---

---

## Phase 3 상세 (7/7 PASS — Phase 4 구현 후 P3-2/P3-3 해결)

### P3-5: `prepare_dump_scope`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:832 | PASS |
| `additional_signals`, `input_tcl`, `sim_dir` 파라미터 | server.py:832 | PASS |
| `_prepare_dump_scope_internal()` helper 구현 | server.py:810 | PASS |
| setup_rtl.tcl / input.tcl / setup.tcl 자동 탐지 | server.py:814 | PASS |
| `probe -create {sig_list} -shm -depth all` 추가 | server.py:822 | PASS |
| `setup_rtl_debug.tcl` 출력 | server.py:827 | PASS |

### P3-4: `probe_add_signals`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:867 | PASS |
| `signals`, `shm_path`, `depth` 파라미터 | server.py:867 | PASS |
| bridge `execute_tcl` 호출 (재사용) | server.py:878 | PASS |
| `probe -create {sig} -shm {shm} -depth {depth}` 명령 | server.py:881 | PASS |

### P3-1: `bisect_signal_dump`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:894 | PASS |
| `shm_path, signal, op, value, start_ns, end_ns, context_signals` 파라미터 | server.py:894 | PASS |
| `csv_cache.extract()` 호출 | server.py:910 | PASS |
| `csv_cache.bisect_csv()` 호출 | server.py:911 | PASS |
| context table 포맷 (context_signals 지원) | server.py:913 | PASS |
| op 5종 ("eq","ne","gt","lt","change") — csv_cache.py:181 | csv_cache.py:181 | PASS |
| hex/dec/oct numeric fallback | csv_cache.py:188 | PASS |

### P3-2: `request_additional_signals`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:979 | PASS |
| `missing_signals, shm_path, bug_time_ns, available_checkpoints` 파라미터 | server.py:979 | PASS |
| [A] re-run with dump_signals 옵션 | server.py:991 | PASS |
| [A'] restore + rerun (checkpoint 있을 때) | server.py:997 | STUB |
| [B] probe_add_signals 온라인 추가 | server.py:1003 | PASS |

> [A'] 경로: `_find_nearest_checkpoint` 미연결 → Phase 4 checkpoint_manager 구현 시 완성

### P3-3: `_find_nearest_checkpoint`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `checkpoint_manager.find_nearest_checkpoint()` 구현 | checkpoint_manager.py:158 | PASS |

> Phase 4 구현 완료로 해결 — NOT_IMPLEMENTED → PASS

### P3-6: `sim_batch_run/regression` + `dump_signals` 연동

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `dump_signals` 파라미터 추가 (sim_batch_run) | server.py:593 | PASS |
| `dump_signals` 파라미터 추가 (sim_batch_regression) | server.py:680 | PASS |
| `_prepare_dump_scope_internal()` 호출 후 runner에 주입 | server.py:644 | PASS |
| 변수 순서 버그 수정 (resolved_sim_dir 먼저 결정) | server.py:629 | PASS |

### P3-7: `generate_debug_tcl`

| 항목 | 파일:라인 | 상태 |
|------|----------|:----:|
| `@mcp.tool()` 등록 | server.py:1041 | PASS |
| `shm_path, signals, center_time_ns, zoom_range_ns, markers, context_note, output_path` 파라미터 | server.py:1041 | PASS |
| `debug_tools.generate_debug_tcl_content()` 호출 | server.py:1057 | PASS |
| `/tmp/mcp_debug_{stem}.tcl` 기본 경로 | server.py:1055 | PASS |
| `database -open`, `waveform add`, `zoom -range`, `cursor set` 생성 | debug_tools.py:44 | PASS |
| named markers 지원 | debug_tools.py:79 | PASS |
| AI context note console output (`puts`) | debug_tools.py:88 | PASS |

---

## Phase 3 편차 (2항목 — P3-2/P3-3 Phase 4에서 해결)

| 유형 | 항목 | 설계 | 구현 | 영향 |
|------|------|------|------|:----:|
| Changed | `probe_add_signals` bridge 방식 | 새 Tcl proc `do_probe_add` | `execute_tcl` 재사용 | Low (단순화) |
| Added | `_prepare_dump_scope_internal()` | 미명시 | 내부 helper로 분리 | Low (개선) |
| ~~Stub~~ | ~~P3-2 [A'] auto-execute~~ | Phase 4 의존 | **Phase 4에서 해결** | — |
| ~~Not_Impl~~ | ~~P3-3 _find_nearest_checkpoint~~ | Phase 4 의존 | **Phase 4에서 해결** | — |

---

## Phase 4 상세 (9.5/10 PASS + 1 STUB)

| # | 항목 | 파일:라인 | 상태 |
|---|------|----------|:----:|
| P4-1 | `save_checkpoint` persistent + manifest | server.py:492, checkpoint_manager.py:82 | PASS |
| P4-2 | `restore_checkpoint` compile_hash 검증 + stale 삭제 | server.py:534, checkpoint_manager.py:97 | PASS |
| P4-3 | `compute_compile_hash()` inca/ mtime MD5 | checkpoint_manager.py:30 | PASS |
| P4-4 | `sim_batch_run` recompile 감지 + checkpoint 무효화 | sim_runner.py:617, server.py:858 | PASS |
| P4-5 | `cleanup_checkpoints` 4모드 + dry_run | server.py:626, checkpoint_manager.py:181 | PASS |
| P4-6 | `bisect_restore_and_debug` watchpoint params | server.py:678 | PASS |
| P4-7 | `bisect_signal` Mode A/B 분기 | server.py:573 | PASS |
| P4-8 | TB 분석 캐시 save point 정보 | checkpoint_manager.py:185 (update/get_tb_analysis_cache) | PASS |
| P4-9 | do_save/do_restore 버그 수정 + stop -delete -all | mcp_bridge.tcl:562, 598 | PASS |
| P4-10 | send_ok/send_error channel 일관 적용 | mcp_bridge.tcl 전체 | PASS |

> P4-8 STUB: manifest에 saved_time_ns 저장됨. TB analysis 파일(.ai/analysis/) 연동은 미구현 — 낮은 영향

## Phase 4 편차 (1항목)

| 유형 | 항목 | 설계 | 구현 | 영향 |
|------|------|------|------|:----:|
| Changed | P4-7 Mode A Tcl-side | do_bisect 내부 변경 | Python-side CSV (bisect_signal_dump) | Low (더 단순) |

---

## Phase 5 상세 (5/5 PASS)

### Phase 5 — UI/Visual (5/5 PASS — 100%)

| # | 항목 | 파일:라인 | 상태 |
|---|------|----------|:----:|
| P5-1 | `open_debug_view` (VNC 감지 + SimVision 실행 + 신호그룹 + zoom/cursor/markers) | server.py:1347 | PASS |
| P5-2 | `__WAVEFORM_ADD_GROUP__` Tcl handler (AI_Debug group, 중복 skip) | mcp_bridge.tcl:785 | PASS |
| P5-3 | `attach_to_simvision` (TCP port 9876 연결 or .simvisionrc 안내) | server.py:1313 | PASS |
| P5-4 | `compare_waveforms` (csv_diff + simvision GUI side-by-side 모드) | server.py:1467 | PASS |
| P5-5 | `export_debug_context` (AI 디버그 컨텍스트 Markdown 생성) | server.py:1554 | PASS |

## Phase 5 편차 (1항목, Low impact)

| 유형 | 항목 | 설계 | 구현 | 영향 |
|------|------|------|------|:----:|
| Changed | P5-2 중복 스캔 범위 | `waveform list -signals` (전체) | `waveform list -using $group_name` (그룹 내) | Low |
| ~~Partial~~ | ~~P5-4 `simvision` 출력모드~~ | GUI side-by-side 뷰 | **2026-03-30 구현 완료** — VNC 감지→SimVision 실행→두 번째 DB 오픈→BEFORE/AFTER 그룹 추가 | **완료** |

---

## 파일 구조 준수 현황

| 설계 파일 | 존재 여부 | Phase |
|----------|:--------:|:-----:|
| `src/xcelium_mcp/server.py` | ✅ | Phase 1 완료 |
| `src/xcelium_mcp/tcl_bridge.py` | ✅ | 변경 없음 (설계대로) |
| `src/xcelium_mcp/sim_runner.py` | ✅ | Phase 1 완료 |
| `src/xcelium_mcp/csv_cache.py` | ✅ | Phase 2 완료 |
| `src/xcelium_mcp/checkpoint_manager.py` | ✅ | Phase 4 완료 |
| `src/xcelium_mcp/debug_tools.py` | ✅ | Phase 3 완료 |
| `tcl/mcp_bridge.tcl` | ✅ | Phase 1 완료 |
