# Plan: xcelium-mcp v4.2 — Code Restructure

> **Feature**: God module 분리, BridgeManager 도입, per-user 경로, import 정리, batch 성능 개선, sim_discover 버그 수정
>
> **Date**: 2026-04-01
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: xcelium-mcp v4.1 (100% complete, code review 완료)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | server.py 2400줄 God module (49 tools), sim_runner.py 1900줄 혼재, 양방향 circular import, global mutable state, 공유 서버에서 `/tmp/` 경로 충돌 위험 |
| **Solution** | (1) server.py를 7개 tool group 모듈로 분리. (2) BridgeManager 클래스로 state 캡슐화. (3) sim_runner.py를 4개 모듈로 분리. (4) per-user `/tmp/` 경로. (5) public API naming 정리 |
| **Function UX Effect** | 외부 동작 변경 없음 (internal refactoring). 코드 가독성/테스트 용이성/유지보수성 향상 |
| **Core Value** | v5 runner abstraction을 위한 코드 기반 정비. 다중 사용자 환경 안전성 확보 |

---

## 1. 배경: v4.1 Code Review 결과

v4.1 기능 구현 완료 후 code review에서 발견된 구조 이슈:

| # | Severity | Issue | Impact |
|---|----------|-------|--------|
| H-1 | High | `_xmsim_bridge`/`_simvision_bridge` global mutable state | 테스트 불가, tight coupling |
| H-2 | High | sim_runner.py → server.py 역방향 import (circular) | 모듈 독립성 파괴 |
| H-3 | High | server.py 2400줄 — 49 tools 단일 파일 | 가독성/유지보수 한계 |
| H-4 | High | sim_runner.py 1900줄 — 환경 탐지/배치/레지스트리 혼재 | 관심사 분리 실패 |
| M-3 | Medium | `/tmp/mcp_bridge_ready_*` 공유 경로 — race condition | 다중 사용자 충돌 |
| L-1 | Low | `_` prefix 함수 14개가 cross-module import | naming convention 위반 |
| L-4 | Low | `/tmp/` hardcoded 30+곳 | M-3과 동일 근본 원인 |

---

## 2. 목표

1. **server.py 분리**: 49 tools를 7개 기능 그룹으로 분리, 메인 파일은 등록만
2. **BridgeManager 도입**: global state → 클래스 캡슐화, dependency injection
3. **sim_runner.py 분리**: 4개 모듈로 관심사 분리
4. **Circular import 제거**: BridgeManager를 공유 모듈로 추출
5. **Per-user 경로**: `/tmp/xcelium_mcp_{uid}/` — Python + Tcl 동기화
6. **Public API naming**: cross-module 함수에서 `_` prefix 제거

---

## 3. 설계

### Phase 1: BridgeManager 추출 (H-1, H-2 해결)

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P1-1 | `BridgeManager` 클래스 신규 | `get_xmsim()`, `get_simvision()`, `set_xmsim()`, `set_simvision()`, `get_bridge(target)` | bridge_manager.py (신규) |
| P1-2 | server.py에서 global 제거 | `_xmsim_bridge`/`_simvision_bridge` → `BridgeManager` 인스턴스 | server.py |
| P1-3 | sim_runner.py circular import 제거 | `import server` 대신 `BridgeManager` 인자로 전달 | sim_runner.py |
| P1-4 | `_get_xmsim_bridge()`/`_get_simvision_bridge()` 위임 | BridgeManager 메서드 호출로 변경 | server.py |

**`bridge_manager.py` 구조:**
```python
class BridgeManager:
    def __init__(self):
        self._xmsim: TclBridge | None = None
        self._simvision: TclBridge | None = None

    @property
    def xmsim(self) -> TclBridge:
        if self._xmsim is None or not self._xmsim.connected:
            raise ConnectionError("Not connected to xmsim...")
        return self._xmsim

    @property
    def simvision(self) -> TclBridge:
        if self._simvision is None or not self._simvision.connected:
            raise ConnectionError("Not connected to SimVision...")
        return self._simvision

    def get_bridge(self, target: str = "auto") -> TclBridge:
        ...

    def set_xmsim(self, bridge: TclBridge | None) -> None:
        self._xmsim = bridge

    def set_simvision(self, bridge: TclBridge | None) -> None:
        self._simvision = bridge
```

### Phase 2: server.py 분리 (H-3 해결)

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P2-1 | `tools/sim_lifecycle.py` | sim_start, sim_run, sim_stop, sim_restart, sim_status, sim_discover, mcp_config, connect_simulator, disconnect_simulator, shutdown_simulator, execute_tcl, list_tests | tools/ (신규) |
| P2-2 | `tools/signal_inspection.py` | get_signal_value, describe_signal, find_drivers, list_signals, deposit_value, release_signal | tools/ (신규) |
| P2-3 | `tools/waveform.py` | waveform_add_signals, waveform_zoom, cursor_set, take_waveform_screenshot | tools/ (신규) |
| P2-4 | `tools/batch.py` | sim_batch_run, sim_batch_regression | tools/ (신규) |
| P2-5 | `tools/checkpoint.py` | save_checkpoint, restore_checkpoint, cleanup_checkpoints | tools/ (신규) |
| P2-6 | `tools/simvision.py` | simvision_start, simvision_setup, simvision_live, simvision_live_stop, database_open, attach_to_simvision, open_debug_view, compare_waveforms | tools/ (신규) |
| P2-7 | `tools/debug.py` | bisect_signal, bisect_signal_dump, bisect_restore_and_debug, set_breakpoint, watch_signal, watch_clear, probe_control, probe_add_signals, prepare_dump_scope, generate_debug_tcl, export_debug_context, request_additional_signals, run_debugger_mode, extract_csv | tools/ (신규) |
| P2-8 | server.py 축소 | MCP 인스턴스 생성 + tool 등록 import만 | server.py |

**파일 구조:**
```
src/xcelium_mcp/
├── server.py              # [축소] mcp 인스턴스 + import tools
├── bridge_manager.py      # [신규] BridgeManager 클래스
├── tools/                 # [신규]
│   ├── __init__.py
│   ├── sim_lifecycle.py   # 12 tools (sim/connect/disconnect/shutdown/execute_tcl/list_tests)
│   ├── signal_inspection.py # 6 tools
│   ├── waveform.py        # 4 tools
│   ├── batch.py           # 2 tools
│   ├── checkpoint.py      # 3 tools
│   ├── simvision.py       # 8 tools
│   └── debug.py           # 14 tools (bisect/watch/probe/debugger/csv/dump)
├── sim_runner.py          # [축소] → Phase 3에서 분리
├── ...
```

**등록 패턴:**
```python
# server.py (축소 후)
from fastmcp import FastMCP
from xcelium_mcp.bridge_manager import BridgeManager

mcp = FastMCP("xcelium-mcp", ...)
bridges = BridgeManager()

# 각 tool 모듈이 register 함수 제공
from xcelium_mcp.tools import sim_lifecycle, signal_inspection, waveform
from xcelium_mcp.tools import batch, checkpoint, simvision, debug

for mod in [sim_lifecycle, signal_inspection, waveform, batch, checkpoint, simvision, debug]:
    mod.register(mcp, bridges)
```

### Phase 3: sim_runner.py 분리 (H-4 해결)

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P3-1 | `env_detection.py` | `_detect_env_shell`, `_detect_eda_env`, `_detect_shell_and_env`, `_auto_detect_runner`, `_analyze_tb_type`, `_discover_sim_dir`, `_detect_run_dir`, `_detect_vnc_display`, `_detect_setup_tcls` | env_detection.py (신규) |
| P3-2 | `registry.py` | `load_registry`, `save_registry`, `load_sim_config`, `save_sim_config`, `_update_registry_from_config`, `_dot_get`, `_dot_set`, `config_action` | registry.py (신규) |
| P3-3 | `batch_runner.py` | `_run_batch_single`, `_run_batch_regression`, `_poll_batch_log`, `_resolve_sim_params`, `_resolve_test_name`, `_validate_extra_args` | batch_runner.py (신규) |
| P3-4 | sim_runner.py 축소 | `ssh_run`, `_login_shell_cmd`, `_build_redirect`, `_sq`, `start_simulation`, `_start_bridge`, `run_full_discovery`, `_patch_legacy_run_script`, `_update_simvisionrc` | sim_runner.py (축소) |

### Phase 4: Per-user 경로 (M-3, L-4 해결)

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P4-1 | `_get_user_tmp_dir()` 헬퍼 | `ssh_run("id -u")` → `/tmp/xcelium_mcp_{uid}/` 반환, 디렉토리 자동 생성 | sim_runner.py |
| P4-2 | Python 측 경로 일원화 | 모든 `/tmp/mcp_*` → `{user_tmp}/mcp_*` | 전체 Python |
| P4-3 | mcp_bridge.tcl 경로 일원화 | `$::env(USER)` 기반 `/tmp/xcelium_mcp_{user}/` | mcp_bridge.tcl |
| P4-4 | 경로 동기화 검증 | Python이 생성한 경로 = Tcl이 읽는 경로 확인 | 전체 |

**Tcl 측 변경:**
```tcl
# mcp_bridge.tcl init
set user_tmp "/tmp/xcelium_mcp_[exec id -u]"
file mkdir $user_tmp
set ready_file "$user_tmp/mcp_bridge_ready_$port"
```

### Phase 5: Public API naming (L-1 해결)

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P5-1 | cross-module 함수 rename | `_sq` → `sq`, `_build_redirect` → `build_redirect`, `_login_shell_cmd` → `login_shell_cmd` 등 14개 | 전체 |
| P5-2 | 내부 전용 함수는 `_` 유지 | 모듈 내부에서만 사용되는 함수는 그대로 | — |

---

## 4. 구현 순서 및 의존성

```
Phase 1 (BridgeManager):
  [P1-1] BridgeManager 클래스 ── 독립 (신규 파일)
  [P1-2] server.py global 제거 ── [P1-1] 필요
  [P1-3] sim_runner.py circular import 제거 ── [P1-1] 필요
  [P1-4] getter 함수 위임 ── [P1-2] 필요

Phase 2 (server.py 분리) ── [Phase 1 완료] 필요:
  [P2-1~P2-7] 7개 tool 모듈 ── 병렬 가능
  [P2-8] server.py 축소 ── [P2-1~P2-7] 완료 후

Phase 3 (sim_runner.py 분리) ── [Phase 1 완료] 필요:
  [P3-1] env_detection.py ── 독립
  [P3-2] registry.py ── 독립
  [P3-3] batch_runner.py ── [P3-2] 필요 (config 읽기)
  [P3-4] sim_runner.py 축소 ── [P3-1~P3-3] 완료 후

Phase 4 (per-user 경로) ── [Phase 2, 3 완료] 필요:
  [P4-1] _get_user_tmp_dir ── 독립
  [P4-2] Python 경로 ── [P4-1] 필요
  [P4-3] Tcl 경로 ── 독립
  [P4-4] 동기화 검증 ── [P4-2, P4-3] 필요

Phase 5 (naming) ── [Phase 2, 3 완료] 필요:
  [P5-1] rename ── Phase 2, 3의 새 모듈 구조에서 수행
  [P5-2] 내부 함수 확인 ── [P5-1] 이후

병렬 가능: Phase 2 ∥ Phase 3 (Phase 1 완료 후)

Phase 6 (batch 성능) ── Phase 3 완료 후 (batch_runner.py 분리 후 수정)
  [P6-0] nohup detach ── **최우선** (독립 가능, Phase 3 이전에도 수정 가능)
  [P6-1~P6-5] ── [P6-0] 완료 후

Phase 7 (sim_discover ~) ── **독립** (즉시 수정 가능, 1줄)
```

---

## 5. 성공 기준

| # | 기준 | 검증 방법 |
|---|------|----------|
| SC-1 | server.py 300줄 이하 | wc -l |
| SC-2 | 각 tool 모듈 500줄 이하 | wc -l |
| SC-3 | sim_runner.py 500줄 이하 | wc -l |
| SC-4 | circular import 없음 | `python -c "import xcelium_mcp.server"` 성공 |
| SC-5 | global mutable state 없음 | grep `^_xmsim_bridge\|^_simvision_bridge` 결과 0 |
| SC-6 | 전기능 테스트 21/21 PASS | v4.1 regression test 재실행 |
| SC-7 | MCP tool 49개 전수 등록 | `mcp.list_tools()` 개수 확인 |
| SC-8 | per-user 경로 — 다른 uid로 동시 실행 시 충돌 없음 | 2 사용자 동시 sim_start |
| SC-9 | `_` prefix cross-module import 0건 | grep `from.*import _` 결과 0 |
| | **— Phase 6: Batch 성능 —** | |
| SC-10 | `sim_batch_run` nohup timeout ERROR 해소 — 정상 polling 진입 확인 | TOP015 batch 실행 → ERROR 없이 결과 반환 |
| SC-11 | Polling overhead — SSH round trip 감소 | 개선 전후 SSH 호출 횟수 비교 |
| | **— Phase 7: sim_discover —** | |
| SC-12 | `sim_discover(sim_dir="~/...")` 정상 동작 | `~` 경로 입력 → run_dir 탐지 성공 |

---

### Phase 6: Batch Mode 성능 개선 (`sim_batch_run` + `sim_batch_regression` + `sim_start(mode="batch")`)

> **영향 tool 3개**: `sim_start(mode="batch")` → `_start_batch()` → `_run_batch_single()` 위임.
> `sim_batch_run` → `_run_batch_single()` 직접 호출.
> `sim_batch_regression` → `_run_batch_regression()` (내부에서 per-test `_poll_batch_log()` 사용).
> 
> 공통 함수 `_run_batch_single()`, `_poll_batch_log()`를 수정하면 **3개 tool 모두 개선**.

#### 6.1 실측 데이터 (2026-04-02, TOP015 테스트)

동일 테스트(VENEZIA_TOP015_i2c_8bit_offset_test, 110ms sim time)에 대한 실측:

| 모드 | 단계 | 시간 | 비고 |
|------|------|:----:|------|
| **run_sim 직접** | 전체 (compile+elab+sim) | **196s** | baseline |
| **Bridge** | sim_start (compile+elab+bridge ready) | 16s | |
| | sim_run (전체 시뮬레이션 완주) | 198s | |
| | shutdown | 8s | |
| | **합계** | **222s** | bridge overhead +26s |
| **Batch** | nohup 시작 | **15s TIMEOUT** | ← **핵심 버그** |
| | 실제 시뮬레이션 (background) | ~196s | run_sim과 동일 |
| | **합계** | **~231s** | (정상 동작 시) |

**핵심 발견: Bridge(222s) vs Batch(231s) vs 직접 실행(196s) — 시뮬레이션 자체는 거의 동일!**

Batch가 "10분 이상"으로 느껴지는 진짜 원인은 시뮬레이션 속도 차이가 아니라 **B-0 nohup detach 실패 버그**.

#### 6.2 Bottleneck 분석

| # | Bottleneck | 심각도 | 원인 | 영향 |
|---|-----------|:------:|------|------|
| **B-0** | **nohup background detach 실패** | **Critical** | `ssh_run`이 `asyncio.create_subprocess_shell(stdout=PIPE, stderr=PIPE)`로 실행. nohup child process가 PIPE fd를 상속받아 `proc.communicate()`가 시뮬레이션 완료까지 EOF를 받지 못함. `>& /tmp/log`로 redirect하지만 손자 프로세스(`tcsh` → `run_sim` → `xmsim`)가 fd를 상속. 15초 timeout 후 ERROR 반환되지만 시뮬레이션은 background에서 계속 실행됨 | **batch 관련 3개 tool 전부 영향** (아래 코드 경로 매핑 참조) |
| B-1 | **Regression 시 매번 재컴파일** | Medium | Bridge는 1회 compile 후 프로세스 유지, Batch는 regression에서 N회 독립 compile | regression에서 (N-1)×compile overhead. **단일 테스트에서는 차이 없음** (실측 확인) |
| B-2 | **10초 polling 간격** | Low | `_poll_batch_log`의 `asyncio.sleep(10)` | B-0 해결 후에만 의미 있음 |
| B-3 | **polling 후 결과 수집 별도 SSH** | Low | `_poll_batch_log`에서 polling 완료 후 `grep -E 'PASS\|FAIL\|...'`로 결과 수집 — polling `tail -5`와 합칠 수 있음 | B-0 해결 후에만 의미 있음 |
| B-4 | **stdbuf 호환성 경고** | Info | `LD_PRELOAD libstdbuf.so cannot be preloaded` — Xcelium 바이너리와 stdbuf 비호환 | 기능에는 영향 없으나 불필요한 overhead |

#### 6.2b 영향 Tool → 코드 경로 매핑

| MCP Tool | 호출 경로 | nohup 발생 위치 | polling |
|----------|----------|----------------|---------|
| `sim_start(mode="batch")` | → `_start_batch()` → **`_run_batch_single()`** L751 | L751 | `_poll_batch_log()` L1675 |
| `sim_batch_run` | → **`_run_batch_single()`** L751 | L751 | `_poll_batch_log()` L1675 |
| `sim_batch_regression` | → **`_run_batch_regression()`** | L860 (single-cmd) / L897 (per-test) | `_poll_batch_log()` L1675 |

**수정 포인트**: nohup 3곳 + `_poll_batch_log()` 1곳 = **4곳 수정으로 3개 tool 모두 개선**.

#### 6.3 B-0 근본 원인 및 수정 방안

**문제 코드** (`sim_runner.py:751-754`):
```python
pid_output = await ssh_run(
    f"cd {_sq(sim_dir)} && nohup {run_cmd} {_build_redirect(log_file)} < /dev/null & echo $!",
    timeout=15.0,
)
```

**원인**: `asyncio.create_subprocess_shell(stdout=PIPE, stderr=PIPE)` — PIPE fd가 nohup child에 상속됨. background `&`로 fork해도 PIPE가 닫히지 않으므로 `communicate()`가 block.

**수정 방안**:

| # | 방안 | 설명 | 복잡도 |
|---|------|------|:------:|
| F-1 | **subshell wrapping** | `(nohup ... >& log < /dev/null &) >& /dev/null; echo $!` — 외부 subshell이 fd를 닫음. (`2>&1` 금지 — tcsh 호환) | 낮음 |
| F-2 | **separate PID file** | `nohup ... &; echo $! > /tmp/pid` 후 별도 ssh_run으로 PID file 읽기 | 낮음 |
| F-3 | **setsid 사용** | `setsid nohup ... >& log < /dev/null &` — 새 session으로 fd 분리 | 낮음 |
| F-4 | **stdbuf 제거** | Xcelium과 비호환. `stdbuf -oL` 없이 실행 — log flush가 느려질 수 있으나 PIPE 문제와 무관 | 낮음 |

**권장**: F-1 + F-4 조합. subshell로 PIPE fd 상속 차단 + stdbuf 제거.

#### 6.4 개선 방안 (B-0 해결 후)

| # | 항목 | 설명 | 예상 효과 | 파일 |
|---|------|------|----------|------|
| P6-0 | **nohup detach 수정 (B-0)** | subshell wrapping + stdbuf 제거. nohup 수정 3곳: `_run_batch_single()` L751, `_run_batch_regression()` L860 + L897 | `sim_start(batch)` + `sim_batch_run` + `sim_batch_regression` 3개 tool 정상 동작 복원 (**필수**) | sim_runner.py |
| P6-1 | **Adaptive polling 간격** | 시작 2s → 점진적 증가 (2→4→8→10s) | 짧은 test 대기시간 -80% | sim_runner.py |
| P6-2 | **SSH polling 1회로 통합** | `tail + grep` 단일 명령 | SSH round trip -50% | sim_runner.py |
| ~~P6-3~~ | ~~Incremental compile~~ | **삭제** — legacy TB는 xmvlog+xmelab 분리 호출이라 `xrun -update` 불가. Xcelium 자체 inca/ 캐시가 이미 동작 (196s→129s) | — | — |
| ~~P6-4~~ | ~~Snapshot reuse~~ | **삭제** — legacy TB에서 test별 `include`로 compile 결과가 test-specific. 다른 test에 재사용 불가 | — | — |
| P6-5 | **Completion marker 파일** | `{log}.done` 파일로 완료 감지 | polling 비용 최소화 | sim_runner.py |
| P6-6 | **성능 baseline 측정** | bridge/batch 실행시간 자동 측정 | 개선 전후 비교 | — |
| ~~P6-7~~ | ~~Regression 병렬화~~ | **삭제** — legacy TB에서 test별 `include`로 compile부터 분리 필수 → sandbox 디렉토리 중복 과다 + license 제약. 순차 실행 유지 | — | — |

#### 6.5 구현 의존성

```
Phase 6 (성능):
  [P6-0] nohup detach 수정 ── **최우선** (이것 없이 다른 개선 무의미)
  
  [P6-0] 완료 후:
    [P6-1] adaptive polling ── 독립
    [P6-2] SSH 통합 ── 독립
    [P6-5] completion marker ── 독립
    [P6-1, P6-2, P6-5] → 병렬 가능

  [P6-3] 삭제 (xrun -update 불가)
  [P6-4] 삭제 (snapshot test-specific)
```

#### 6.6 우선순위

| 우선순위 | 항목 | 이유 |
|:--------:|------|------|
| **0 (긴급)** | **P6-0 nohup detach** | batch 자체가 동작 불능. 이것 없이 다른 개선 무의미 |
| 1 | P6-1 adaptive polling | 즉시 적용 가능, 짧은 test 체감 향상 큼 |
| 1 | P6-2 SSH 통합 | 즉시 적용 가능, polling 비용 -50% |
| 2 | P6-5 completion marker | run_sim wrapper 수정 필요하지만 효과 큼 |
| ~~2~~ | ~~P6-3 incremental compile~~ | 삭제 |
| ~~3~~ | ~~P6-4 snapshot reuse~~ | 삭제 |

---

## 6. 리스크

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 대규모 refactoring으로 regression | v4.1 전기능이 깨질 수 있음 | Phase별 commit + 21개 test regression 수행 |
| FastMCP tool 등록이 다른 모듈에서 작동하는지 | tool이 인식 안 될 수 있음 | FastMCP 문서 확인 + 단위 테스트 |
| per-user 경로 Tcl↔Python 동기화 | ready file 못 찾음 | 환경변수로 경로 공유 (MCP_TMP_DIR) |
| BridgeManager 인자 전달 깊이 | tool → helper → runner 체인이 길어짐 | 모듈 레벨 싱글턴 패턴 허용 |

---

## 7. 항목 합계

| Phase | 항목 수 |
|-------|:-------:|
| Phase 1 (BridgeManager) | 4 |
| Phase 2 (server.py 분리) | 8 |
| Phase 3 (sim_runner.py 분리) | 4 |
| Phase 4 (per-user 경로) | 4 |
| Phase 5 (naming) | 2 |
| Phase 6 (batch 성능 개선) | 5 (P6-3,P6-4,P6-7 삭제) |
| Phase 7 (sim_discover `~` 버그) | 2 |
| **합계** | **29** |

---

## 8. Phase 7: sim_discover `~` 경로 버그

### 7.1 문제: `shlex.quote()`가 `~` 확장을 차단

**재현**: `sim_discover(sim_dir="~/git.clone/.../ncsim")` → "Could not detect run directory"

**근본 원인**: `_sq()` (`shlex.quote()`)가 `~`를 single quote로 감싸서 shell에서 tilde expansion이 일어나지 않음.

```python
_sq("~/git.clone/.../ncsim")  →  "'~/git.clone/.../ncsim'"
```

```bash
# 실패: ~ 확장 안 됨
find '~/git.clone/.../ncsim' -maxdepth 1 -type d -name 'run*'   # exit=1

# 성공: ~ 확장됨
find ~/git.clone/.../ncsim -maxdepth 1 -type d -name 'run*'     # run/ 발견
```

**영향 범위**: sim_runner.py에서 `_sq(sim_dir)`를 사용하는 **13곳** 전부.
단, registry/config에 **이미 resolve된 절대경로**가 저장되면 이후 tool들은 정상 동작 (sim_start, sim_batch_run 등은 registry 경로 사용). **sim_discover만 사용자 입력을 직접 받는 진입점**이라 문제가 드러남.

### 7.2 수정 방안

| # | 항목 | 설명 | 파일 |
|---|------|------|------|
| P7-1 | **`run_full_discovery` 진입점에서 `~` resolve** | `sim_dir`를 받는 즉시 `os.path.expanduser()` + ssh_run `readlink -f` 또는 `realpath`로 정규화. 이후 모든 함수에 resolve된 절대경로 전달 | sim_runner.py |
| P7-2 | **`_sq()` 안전 가드** | `_sq()`에 `~`로 시작하는 경로 감지 + warning 또는 자동 expand 로직 추가. 방어적 프로그래밍 | sim_runner.py |

**권장 수정** (P7-1):
```python
import os

async def run_full_discovery(sim_dir: str = "", force: bool = False) -> str:
    if not sim_dir:
        envs = await _discover_sim_dir()
        sim_dir = envs[0]["sim_dir"]
    
    # ★ resolve ~ to absolute path (Python-side, shell-independent)
    sim_dir = os.path.expanduser(sim_dir)
```

xcelium-mcp는 cloud0에서 직접 실행되므로 Python `os.path.expanduser()`가 올바른 home을 반환한다.
`ssh_run(readlink -f ...)`을 사용하면 `_sq()`로 감쌀 때 **같은 버그 재발** 위험이 있으므로, shell에 의존하지 않는 Python측 해결이 안전하다.
bash/tcsh 모두 single quote 안 `~`는 확장하지 않으므로 shell 종류와 무관한 문제.

### 7.3 우선순위

| 우선순위 | 이유 |
|:--------:|------|
| **0 (긴급, P6-0과 동일)** | sim_discover가 `~` 경로에서 항상 실패. clean 환경에서 초기 설정 자체가 불가능 |
