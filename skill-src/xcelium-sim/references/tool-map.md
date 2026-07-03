# tool-map.md — xcelium-mcp 24개 tool 결정 매트릭스

> xcelium-mcp는 Cadence Xcelium/SimVision을 MCP로 제어하는 24개 tool(7개 모듈, action 파라미터 기반 통합)을 제공한다. 이 문서는 "지금 어떤 tool을 어떤 파라미터로 써야 하는가"를 phase별로 즉시 답하기 위한 결정 매트릭스다.
>
> **소스**: 2026-07-03 `src/xcelium_mcp/tools/*.py` 직접 대조(`grep -c "@mcp.tool()"` = 24) 기준. CLAUDE.md 프로즈가 아니라 이 감사 결과가 원본이다.
> **자기완결성 안내**: 이 문서는 Claude(skill 세션 내)와 `verilog-rtl-debugger` agent(chip-design-skills, 별도 context) 양쪽에서 단독으로 읽힌다 — "위에서 설명한" 같은 세션-종속 표현 없이 매 항목이 독립적으로 이해 가능해야 한다.

---

## 전체 tool 인벤토리 (24개 네이티브 + ssh-mcp 헬퍼 1건 별도)

### Sim Lifecycle — 환경 설정 + 실행 제어 (10)

| Phase | Tool | Action/파라미터 | 용도 |
|-------|------|-----------------|------|
| 0 | `sim_discover` | `sim_dir`, `force`, `top_module`, `run_dir`, `boundary_depth`(v5.2) | 시뮬레이션 환경 자동 감지(TB type, shell, EDA, sdf_info, top_module). `boundary_depth`는 v5.2 Flow B(Yosys JSON lazy discovery)의 블록 경계 탐색 깊이 |
| 0 | `mcp_config` | `action`: "show"(전체 dump)/"get"(키 조회)/"set"(키 쓰기)/"delete"(키 삭제); `file`: "config"/"registry"/"checkpoint" | mcp_sim_config/mcp_registry/checkpoint manifest 조회·수정 |
| 0 | `list_tests` | `sim_dir`, `pattern` | 테스트케이스 목록 조회(캐시) |
| 2A | `sim_bridge_run` | `test_name`, `sim_mode`, `dump_depth`, `auto_boundaries`(v5.2) | Bridge(interactive) mode 시작. `auto_boundaries=True`는 v5.2 Flow A — SimVision `scope -describe`로 런타임 블록 경계 자동 탐색 |
| 2B | `connect_simulator` | `host`, `port`(0=자동감지), `target`: "xmsim"/"simvision"/"auto" | 기존 xmsim/SimVision에 bridge (재)연결. v4.1 multi-bridge 지원 |
| 2B | `sim_disconnect` | `action`: "bridge"(연결만 해제)/"shutdown"(안전 종료, SHM 보존) | **주의**: 디버깅 세션 종료 시 항상 "shutdown" 사용 — 일반 disconnect나 pkill은 SHM 유실 |
| 2B | `sim_run` | `duration`, `timeout`, `chunk`(기본 100000ns) | 시간/breakpoint 지정 실행. 중단하려면 외부에서 sentinel 파일 생성(아래 참고) |
| 2B | `sim_status` | `target` | 현재 시간/scope/상태 조회 |
| 2B | `sim_restart` | — | 시뮬레이션을 time 0으로 재시작(init snapshot 복원) |
| 2B | `execute_tcl` | `tcl_cmd`, `timeout`, `target` | 임의 Tcl 명령 실행(escape hatch) — 전용 tool 없을 때만 사용 |

**참고(네이티브 아님)**: `ssh_run("kill -s INT {xmsim_pid}")` — 별도 ssh-mcp 서버의 명령. `sim_run` 블로킹 중 xcelium-mcp 자체 tool로는 중단 신호를 못 보내므로(같은 서버 큐잉), sentinel 파일 방식(아래)을 우선 사용하고 SIGINT는 최후 수단으로만 사용한다(xmsim 프로세스 자체가 종료됨).

**sim_run 중단(sentinel 파일)**: `sim_run`은 100µs 단위 chunk 루프로 실행되며, 각 chunk 경계에서 `/tmp/xcelium_mcp_{uid}/stop_{port}` 파일을 감지하면 즉시 정지한다(`status=stopped`). xmsim 프로세스는 생존, bridge도 intact. ssh-mcp의 `ssh_bg_run`으로 별도 채널에서 생성해야 한다(xcelium-mcp 자체 tool 호출은 sim_run과 직렬화되어 병렬 중단 불가).

### Batch — 비대화형 실행 (2)

| Phase | Tool | 주요 파라미터 | 용도 |
|-------|------|--------------|------|
| 2A | `sim_batch_run` | `test_name`, `sim_dir`, `from_checkpoint`, `dump_depth`, `dump_signals`, `dump_window_start_ms`/`dump_window_end_ms`, `sdf_file`, `sdf_corner`, `sim_mode`, `dump_scopes`(v5.2), `use_dump_history`(v5.2) | 단일 테스트 batch 실행. 결과에 `shm_path` 포함. **God node — 아래 §God Node 상세 참조** |
| 5D | `sim_regression` | `test_list`, `dump_signals`, `save_checkpoints`, (batch_run과 동일 dump 파라미터 공유) | 다중 테스트 순차 실행. `save_checkpoints=True`면 테스트당 L1 checkpoint 자동 저장 |

### Signal Inspection — 신호 조회·조작 (2)

| Phase | Tool | Action | 용도 |
|-------|------|--------|------|
| 1D | `inspect_signal` | `action="check_dump"` | SHM에 signal 존재 확인(found/missing 분류) |
| 4D | `inspect_signal` | `action="value"`(값 읽기)/`"describe"`(타입·폭·방향)/`"list"`(scope 목록)/`"drivers"`(드라이버 추적, X/Z 디버깅용) | 신호 조회 |
| 4A/4B | `inspect_signal` | `action="extract_csv"`(`signals`, `shm_path`, `start_ns`, `end_ns`) | **cache 경유 CSV 추출** — bisect 없이 CSV만 필요할 때(2026-07-03, F-174). `csv_cache.extract()`를 직접 노출 — `simvisdbutil`을 shell로 직접 부르는 대신 항상 이걸 우선 사용 |
| 4D | `deposit_signal` | `signal`, `value`, `release=True`(해제) | 신호값 강제 주입/해제(real/wreal 아날로그 net도 지원) |

### Debug — 분석 + 프로빙 (4)

| Phase | Tool | Action/모드 | 용도 |
|-------|------|-------------|------|
| 4E | `bisect_signal` | Mode A(`shm_path` 지정, 권장 — bridge 불필요, SHM CSV 기반 in-memory binary search) / Mode B(`shm_path` 빈 값, bridge 연결 필요 — 네이티브 `__BISECT__`) | 신호 조건이 처음 참이 되는 시점 탐색. `op`: eq/ne/gt/lt/change |
| 4D | `watch` | `action="set"`(watchpoint/breakpoint 생성)/`"clear"`(해제) | 조건부 stop. `type="watch"`(기본) 또는 `"breakpoint"` |
| 4D | `probe` | `action="add"`(신호 추가)/`"enable"`/`"disable"`(SHM 기록 on/off) | SHM waveform probe 제어. AMS Tier 3(시간 윈도우 dump)의 수동 제어에 사용 |
| — | `debug_snapshot` | `mode="snapshot"`(통합 스냅샷)/`"tcl"`(SimVision Tcl 스크립트 생성)/`"export"`(AI 분석 결과 Markdown 내보내기) | 디버깅 컨텍스트 캡처·공유 |

### Checkpoint (1)

| Phase | Tool | Action | 용도 |
|-------|------|--------|------|
| — | `checkpoint` | `action="save"`(저장)/`"restore"`(복원)/`"list"`(조회)/`"cleanup"`(mode: stale/hash/origin/pattern/before/project/all/rebuild) | 시뮬레이션 체크포인트 관리. **God node — 아래 참조** |

### Waveform — 파형 제어 (2)

| Phase | Tool | Action | 용도 |
|-------|------|--------|------|
| 4E | `waveform` | `action="add"`/`"remove"`/`"clear"`(신호 관리) / `"zoom"`/`"cursor"`(뷰 네비게이션) | SimVision waveform 창 제어 |
| 4E | `waveform_screenshot` | — | 스크린샷 캡처(PNG, Claude가 직접 분석 가능) |

### SimVision — GUI 시각화 (3)

| Phase | Tool | Action | 용도 |
|-------|------|--------|------|
| 4E | `simvision_connect` | `action="start"`(시작+연결)/`"attach"`(기존 세션 연결)/`"open_db"`(SHM 열기) | SimVision 연결 관리 |
| 4E | `simvision` | `action="setup"`(SHM+신호+줌 일괄 구성)/`"live_start"`/`"live_stop"`(실시간 모니터링)/`"reload"`(SHM 갱신, waveform context 보존) | SimVision waveform 제어 |
| 4E | `compare_waveforms` | `shm_before`, `shm_after` | 두 SHM dump 비교(csv_diff 모드 기본, simvision side-by-side 모드) |

---

## God Node 상세화 (사용 빈도·파라미터 복잡도 최상위 3개)

### `sim_batch_run` — 19개 파라미터

가장 많이 쓰이는 tool. 핵심 파라미터 그룹:

```python
# 기본형 — 신규 테스트 첫 실행
sim_batch_run(test_name="TOP015", dump_signals=["top.hw...r_regAddr"])

# [A'] 복원 실행 — checkpoint에서 재시작(전체 재실행 회피)
sim_batch_run(test_name="TOP015", from_checkpoint="L1_TOP015", probe_signals=["추가신호"])

# Gate/AMS — Tier 1 boundary만
sim_batch_run(test_name="TOP015", dump_depth="boundary", sim_mode="gate")

# v5.2 — 블록 단위 중간 해상도(dump_depth="boundary"와 "all" 사이)
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_scopes={"top.hw.u_ext.u_i2cSlave": "all"}, sim_mode="gate")
sim_batch_run(test_name="TOP015", use_dump_history=True)   # 이전 dump_scopes 재사용

# AMS 장시간 sim — 시간 윈도우만 dump
sim_batch_run(test_name="TOP015", sim_mode="ams_rtl",
              dump_window_start_ms=50, dump_window_end_ms=55)

# Gate SDF timing
sim_batch_run(test_name="TOP015", sim_mode="gate", sdf_file="...", sdf_corner="max")
```

`dump_scopes` value는 `"all"`/`"boundary"`/`"skip"`, key는 계층 경로(glob 지원). `use_dump_history=True`면 직전 실행의 `dump_scopes`를 자동 재사용한다.

### `checkpoint` — action 4종

```python
checkpoint(action="save", name="before_fix")
checkpoint(action="restore", name="before_fix")
checkpoint(action="list")
checkpoint(action="cleanup", mode="stale", dry_run=True)   # dry_run=False로 실제 삭제
```

### `bisect_signal` — Mode A/B

```python
# Mode A (권장) — SHM CSV 기반, bridge 불필요
bisect_signal(signal="top.hw...r_streamRwState", op="eq", value="3",
              start_ns=0, end_ns=15000000, shm_path="dump/ci_top_TOP015.shm",
              context_signals=["top.hw...r_regAddr", "top.hw...r_loopState"])

# Mode B — bridge 연결 필요, 네이티브 binary search
bisect_signal(signal="...", op="eq", value="3", start_ns=0, end_ns=15000000)  # shm_path 생략
```

**Mode A 응답에는 `CSV: {path}`가 포함된다** — `[signal]+context_signals`를 이미 추출·캐싱(`csv_cache.extract`)해둔 CSV 경로다. 이후 in-memory 분석(awk/grep)은 이 경로를 재사용한다 — `simvisdbutil`을 직접 shell로 다시 부르면 cache를 우회하게 되므로, **좁은 범위라 bisect가 불필요해 보여도 `bisect_signal`을 통해 CSV 경로를 얻는 편이 낫다** (2026-07-03, F-174).

---

## Phase별 Tool 선택 요약

| 상황 | 우선 Tool |
|------|----------|
| 첫 시뮬레이션, 버그 위치 모름 | `sim_batch_run`(Batch, 전체 dump) |
| Regression | `sim_regression` |
| 추가 신호 필요, checkpoint 있음 | `sim_batch_run(from_checkpoint=...)` |
| 추가 신호 필요, checkpoint 없음 | `sim_batch_run` 전체 재실행(dump_depth/dump_signals 조정) |
| 값 변화 시점 자동 탐색 | `bisect_signal`(Mode A) |
| 신호 시간 변화 상세 추적 | `simvisdbutil`(ssh_run, MCP tool 아님) — bisect가 좁힌 구간에서 |
| 시각적 확인/공유 | `simvision_connect` → `waveform` → `waveform_screenshot` |
| 특정 시점 조합(c_) 신호 확인 | `inspect_signal(action="value")` — c_ 신호는 dump에 없음 |
| 조건부 stop | `watch(action="set")` |
| Gate/AMS 실패 블록 특정 | `sim_batch_run(dump_scopes=...)` (v5.2) |

---

## 원본

이 매트릭스는 `docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md`(v2.6)의 "xcelium-mcp Tool 맵핑" 섹션 + §1D-5(v5.2)를 압축한 것이다. 상세 절차·실전 예시는 phase-0~5 reference 참조.
