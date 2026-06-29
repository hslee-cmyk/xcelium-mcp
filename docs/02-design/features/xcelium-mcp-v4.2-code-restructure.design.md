# Design: xcelium-mcp v4.2 — Code Restructure

> **Summary**: God module 분리, BridgeManager 도입, per-user 경로, batch 성능 개선
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-04-02
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-v4.2-code-restructure.plan.md](../../01-plan/features/xcelium-mcp-v4.2-code-restructure.plan.md)
> **Predecessor**: [xcelium-mcp-v4.1-enhancements.design.md](xcelium-mcp-v4.1-enhancements.design.md)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | server.py 2410줄 God module (49 tools + helpers), sim_runner.py 1920줄 혼재, circular import, global mutable state, `/tmp/` 다중 사용자 충돌, batch nohup detach 실패(수정 완료), sim_discover `~` 확장 실패(수정 완료) |
| **Solution** | (1) BridgeManager 클래스로 state 캡슐화 (2) server.py → 7개 tool group 모듈 분리 (3) sim_runner.py → 4개 모듈 분리 (4) per-user `/tmp/` 경로 (5) public API naming 정리 (6) batch polling 개선 |
| **Function UX Effect** | 외부 동작 변경 없음 (internal refactoring). 코드 가독성/테스트 용이성/유지보수성 향상 |
| **Core Value** | 유지보수 가능한 코드 기반 확립. 다중 사용자 환경 안전성 확보 |

---

## 1. Overview

### 1.1 Design Goals

1. server.py를 300줄 이하로 축소 (현재 2410줄)
2. 각 tool 모듈 500줄 이하
3. sim_runner.py를 500줄 이하로 축소 (현재 1920줄)
4. Circular import 완전 제거
5. Global mutable state 제거 → 클래스 캡슐화
6. 49개 MCP tool 전수 등록 보장

### 1.2 Design Principles

- **Single Responsibility**: 각 모듈은 하나의 tool 그룹만 담당
- **Dependency Injection**: BridgeManager를 인자로 전달, global state 제거
- **No Breaking Changes**: 외부 MCP tool 시그니처 변경 없음
- **Phase별 Commit**: 각 Phase 완료 시 21개 regression test 수행

### 1.3 이미 완료된 항목

| 항목 | 커밋 | 상태 |
|------|------|:----:|
| P6-0: nohup detach 수정 | `1b566ee` | ✅ |
| P7-1: sim_discover `~` expanduser | `1b566ee` | ✅ |

---

## 2. Architecture

### 2.1 현재 구조 (v4.1)

```
src/xcelium_mcp/
├── server.py              # 2410줄 — 49 @mcp.tool + helpers + global state
├── sim_runner.py           # 1920줄 — env detection + batch + registry + bridge start
├── tcl_bridge.py           # TCP bridge 클래스
├── csv_cache.py            # SHM→CSV 캐시
├── checkpoint_manager.py   # checkpoint 관리
├── debug_tools.py          # debug context 관련
└── screenshot.py           # waveform screenshot

tcl/
└── mcp_bridge.tcl          # 920줄 — Tcl bridge server
```

**문제점**:
- `server.py` → `sim_runner.py`: 정방향 import (정상)
- `sim_runner.py` → `server.py`: **역방향 import** (circular) — `_start_bridge()` 내에서 `_srv._xmsim_bridge` 직접 접근
- `_xmsim_bridge`, `_simvision_bridge`: module-level global mutable state

### 2.2 목표 구조 (v4.2)

```
src/xcelium_mcp/
├── server.py              # [축소 ~100줄] MCP 인스턴스 + tool 등록만
├── bridge_manager.py      # [신규 ~80줄] BridgeManager 클래스
├── tools/                 # [신규]
│   ├── __init__.py        # 빈 파일
│   ├── sim_lifecycle.py   # 12 tools — sim/connect/disconnect/shutdown/tcl/list
│   ├── signal_inspection.py # 6 tools — get/describe/find/list/deposit/release
│   ├── waveform.py        # 4 tools — add_signals/zoom/cursor/screenshot
│   ├── batch.py           # 2 tools — batch_run/regression
│   ├── checkpoint.py      # 3 tools — save/restore/cleanup
│   ├── simvision.py       # 8 tools — start/setup/live/stop/db_open/attach/debug/compare
│   └── debug.py           # 14 tools — bisect/watch/probe/dump/debugger/csv/generate/export
├── sim_runner.py          # [축소 ~500줄] ssh_run + start_simulation + bridge start
├── env_detection.py       # [신규 ~400줄] 환경 탐지 함수 전체
├── registry.py            # [신규 ~300줄] config/registry CRUD
├── batch_runner.py        # [신규 ~400줄] batch 실행 + polling
├── tcl_bridge.py          # 변경 없음
├── csv_cache.py           # 변경 없음
├── checkpoint_manager.py  # 변경 없음
├── debug_tools.py         # 변경 없음
└── screenshot.py          # 변경 없음

tcl/
└── mcp_bridge.tcl         # [수정] per-user 경로
```

### 2.3 의존성 방향 (v4.2)

```
server.py
  ├── bridge_manager.py     (BridgeManager 인스턴스 생성)
  ├── tools/*.py            (tool 등록)
  │     ├── bridge_manager  (BridgeManager 참조, DI)
  │     ├── sim_runner      (start_simulation, ssh_run)
  │     ├── batch_runner    (batch 실행)
  │     ├── env_detection   (discovery)
  │     ├── registry        (config CRUD)
  │     └── tcl_bridge      (TclBridge 타입)
  └── (NO circular imports)

sim_runner.py
  ├── bridge_manager.py     (BridgeManager 인자로 받음 — circular import 제거)
  ├── env_detection.py
  ├── registry.py
  └── batch_runner.py

bridge_manager.py
  └── tcl_bridge.py         (TclBridge 타입만)
```

**핵심**: `sim_runner.py → server.py` 역방향 import가 완전 제거됨. BridgeManager가 공유 모듈로 양쪽에서 접근.

---

## 3. Phase 1: BridgeManager 상세 설계

### 3.1 bridge_manager.py

```python
"""Bridge state management — shared between server.py and sim_runner.py."""
from __future__ import annotations
from xcelium_mcp.tcl_bridge import TclBridge


class BridgeManager:
    """Encapsulates xmsim/SimVision bridge state.
    
    Replaces module-level globals _xmsim_bridge / _simvision_bridge.
    Single instance created in server.py, passed to tools and sim_runner via DI.
    """
    
    def __init__(self) -> None:
        self._xmsim: TclBridge | None = None
        self._simvision: TclBridge | None = None

    @property
    def xmsim(self) -> TclBridge:
        """Get connected xmsim bridge. Raises if not connected."""
        if self._xmsim is None or not self._xmsim.connected:
            raise ConnectionError(
                "Not connected to xmsim. Use connect_simulator or sim_start first."
            )
        return self._xmsim

    @property
    def simvision(self) -> TclBridge:
        """Get connected SimVision bridge. Raises if not connected."""
        if self._simvision is None or not self._simvision.connected:
            raise ConnectionError(
                "Not connected to SimVision. Use simvision_start first."
            )
        return self._simvision

    def get_bridge(self, target: str = "auto") -> TclBridge:
        """Get bridge by target. auto = xmsim first, then simvision."""
        if target == "xmsim":
            return self.xmsim
        elif target == "simvision":
            return self.simvision
        elif target == "auto":
            if self._xmsim and self._xmsim.connected:
                return self._xmsim
            if self._simvision and self._simvision.connected:
                return self._simvision
            raise ConnectionError("No simulator connected.")
        raise ValueError(f"Unknown target: {target}")

    def set_xmsim(self, bridge: TclBridge | None) -> None:
        self._xmsim = bridge

    def set_simvision(self, bridge: TclBridge | None) -> None:
        self._simvision = bridge

    @property
    def xmsim_raw(self) -> TclBridge | None:
        """Raw access without connection check (for disconnect/shutdown)."""
        return self._xmsim

    @property
    def simvision_raw(self) -> TclBridge | None:
        """Raw access without connection check (for disconnect/shutdown)."""
        return self._simvision
```

### 3.2 Circular Import 제거 변경점

**현재** (`sim_runner.py:1620-1626`):
```python
# _start_bridge() 내부
import xcelium_mcp.server as _srv         # ← circular import!
from xcelium_mcp.tcl_bridge import TclBridge as _TB

if _srv._xmsim_bridge and _srv._xmsim_bridge.connected:
    await _srv._xmsim_bridge.disconnect()
    _srv._xmsim_bridge = None
```

**변경 후**:
```python
# _start_bridge(bridges: BridgeManager, ...) — BridgeManager를 인자로 받음
if bridges.xmsim_raw and bridges.xmsim_raw.connected:
    await bridges.xmsim_raw.disconnect()
    bridges.set_xmsim(None)
```

### 3.3 server.py getter 함수 제거 (P1-2, P1-4)

**현재** — 3개 getter + global state:
```python
# server.py L53-88 — 제거 대상 전체
_xmsim_bridge: TclBridge | None = None
_simvision_bridge: TclBridge | None = None

def _get_xmsim_bridge() -> TclBridge: ...
def _get_simvision_bridge() -> TclBridge: ...
def _get_bridge(target: str = "auto") -> TclBridge: ...
```

**변경 후** — BridgeManager 메서드로 완전 대체:
```python
# 이 3개 함수와 2개 global 변수는 전부 삭제.
# 대신 bridges 인스턴스를 각 tool 모듈의 register()에 전달.
```

**호출 패턴 변환** (server.py 내 41곳):

| 현재 패턴 | 변경 후 | 해당 tool 수 |
|----------|---------|:----------:|
| `bridge = _get_xmsim_bridge()` | `bridge = bridges.xmsim` | 22곳 |
| `bridge = _get_simvision_bridge()` | `bridge = bridges.simvision` | 11곳 |
| `bridge = _get_bridge(target)` | `bridge = bridges.get_bridge(target)` | 5곳 |
| `global _xmsim_bridge` + 대입 | `bridges.set_xmsim(bridge)` | 6곳 (L237,553,588,643,1097 + _start_bridge) |
| `global _simvision_bridge` + 대입 | `bridges.set_simvision(bridge)` | 6곳 (L237,553,588,643,1097 + simvision_start) |

**변환 예시** — `disconnect_simulator` (L637-654):
```python
# Before:
async def disconnect_simulator(target: str = "all") -> str:
    global _xmsim_bridge, _simvision_bridge
    if target in ("xmsim", "all") and _xmsim_bridge:
        await _xmsim_bridge.disconnect()
        _xmsim_bridge = None
    if target in ("simvision", "all") and _simvision_bridge:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None

# After (tools/sim_lifecycle.py 내부):
async def disconnect_simulator(target: str = "all") -> str:
    if target in ("xmsim", "all") and bridges.xmsim_raw:
        await bridges.xmsim_raw.disconnect()
        bridges.set_xmsim(None)
    if target in ("simvision", "all") and bridges.simvision_raw:
        await bridges.simvision_raw.disconnect()
        bridges.set_simvision(None)
```

### 3.4 sim_runner.py circular import 제거 (P1-3)

**`_start_bridge()` 시그니처 연쇄 변경**:

```python
# Before:
async def start_simulation(test_name, sim_dir, mode, sim_mode, ...) -> str:
    ...
    return await _start_bridge(resolved_dir, config, test_name, setup_tcl, ...)

async def _start_bridge(sim_dir, config, test_name, setup_tcl, sim_mode, timeout, extra_args) -> str:
    import xcelium_mcp.server as _srv  # ← circular!
    if _srv._xmsim_bridge and _srv._xmsim_bridge.connected:
        await _srv._xmsim_bridge.disconnect()
        _srv._xmsim_bridge = None
    ...
    _srv._xmsim_bridge = bridge  # auto-connect 저장

# After:
async def start_simulation(test_name, sim_dir, mode, sim_mode, ...,
                           bridges: BridgeManager | None = None) -> str:
    ...
    return await _start_bridge(bridges, resolved_dir, config, test_name, setup_tcl, ...)

async def _start_bridge(bridges: BridgeManager, sim_dir, config, test_name,
                        setup_tcl, sim_mode, timeout, extra_args) -> str:
    # NO import server — BridgeManager를 인자로 받음
    if bridges.xmsim_raw and bridges.xmsim_raw.connected:
        await bridges.xmsim_raw.disconnect()
        bridges.set_xmsim(None)
    ...
    bridges.set_xmsim(bridge)  # auto-connect 저장
```

**호출 체인**: `tools/sim_lifecycle.py::sim_start()` → `sim_runner.start_simulation(bridges=bridges)` → `sim_runner._start_bridge(bridges, ...)`

---

## 4. Phase 2: server.py Tool 모듈 분리 상세 설계

### 4.1 Tool 등록 패턴

각 모듈은 `register(mcp, bridges)` 함수를 export. 이 함수 내에서 `@mcp.tool()` 데코레이터로 tool 등록.

```python
# tools/signal_inspection.py 예시
from __future__ import annotations
from fastmcp import FastMCP
from xcelium_mcp.bridge_manager import BridgeManager


def register(mcp: FastMCP, bridges: BridgeManager) -> None:
    """Register signal inspection tools."""

    @mcp.tool()
    async def get_signal_value(signals: list[str]) -> str:
        """Get current value of one or more signals."""
        bridge = bridges.get_bridge()
        # ... 기존 로직 그대로
```

### 4.2 server.py 축소 후 구조

```python
# server.py (~100줄)
from __future__ import annotations
from fastmcp import FastMCP
from xcelium_mcp.bridge_manager import BridgeManager

mcp = FastMCP(
    "xcelium-mcp",
    description="MCP server for Cadence Xcelium/SimVision simulator control",
)
bridges = BridgeManager()

# Tool registration
from xcelium_mcp.tools import (
    sim_lifecycle, signal_inspection, waveform,
    batch, checkpoint, simvision, debug,
)
for mod in [sim_lifecycle, signal_inspection, waveform,
            batch, checkpoint, simvision, debug]:
    mod.register(mcp, bridges)
```

### 4.3 register() 내부 구조 정책

```python
# 정책: tool 함수는 register() 안에 nested def로 정의 (closure로 bridges 접근)
#       helper 함수는 register() 밖 모듈 레벨에 정의 (bridges 불필요한 경우)
#       bridges가 필요한 helper는 bridges를 인자로 받음

# tools/sim_lifecycle.py 구조 예시:
from __future__ import annotations
from fastmcp import FastMCP
from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclBridge
from xcelium_mcp.sim_runner import ssh_run, start_simulation, run_full_discovery
from xcelium_mcp.registry import config_action, load_sim_config
from xcelium_mcp.env_detection import _detect_vnc_display
from xcelium_mcp.batch_runner import resolve_test_name


# Module-level helpers (bridges 불필요)
async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type."""
    ...

async def _read_bridge_type(port: int) -> str:
    ...

# bridges 필요한 helper → 인자로 받음
async def _auto_connect_all(bridges: BridgeManager, host: str, timeout: float) -> str:
    ...


def register(mcp: FastMCP, bridges: BridgeManager) -> None:
    """Register simulation lifecycle tools."""

    @mcp.tool()
    async def connect_simulator(host="localhost", port=0, target="auto", timeout=30.0) -> str:
        # bridges는 closure로 접근
        if port == 0 and target == "auto":
            return await _auto_connect_all(bridges, host, timeout)
        ...

    @mcp.tool()
    async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
        bridge = bridges.xmsim  # closure
        ...
```

### 4.4 모듈별 import 매핑

| Tool 모듈 | import 대상 | 설명 |
|----------|-----------|------|
| `tools/sim_lifecycle.py` | `sim_runner.{ssh_run, start_simulation, run_full_discovery}`, `registry.{config_action, load_sim_config, load_registry}`, `env_detection._detect_vnc_display`, `batch_runner.resolve_test_name`, `tcl_bridge.TclBridge` | 가장 넓은 의존성 |
| `tools/signal_inspection.py` | `bridge_manager.BridgeManager`, `tcl_bridge.TclBridge` | bridges만 사용 |
| `tools/waveform.py` | `bridge_manager.BridgeManager`, `tcl_bridge.TclBridge`, `screenshot` | bridges + screenshot |
| `tools/batch.py` | `bridge_manager.BridgeManager`, `batch_runner.{_run_batch_single, _run_batch_regression}`, `registry.load_sim_config`, `sim_runner.{ssh_run, _get_default_sim_dir}` | batch_runner 위임 |
| `tools/checkpoint.py` | `bridge_manager.BridgeManager`, `sim_runner.ssh_run`, `checkpoint_manager`, `registry.load_sim_config` | checkpoint_manager 위임 |
| `tools/simvision.py` | `bridge_manager.BridgeManager`, `sim_runner.{ssh_run, _login_shell_cmd}`, `tcl_bridge.TclBridge`, `registry.load_sim_config`, `env_detection._detect_vnc_display` | SimVision 전용 |
| `tools/debug.py` | `bridge_manager.BridgeManager`, `sim_runner.{ssh_run, _build_redirect}`, `tcl_bridge.TclBridge`, `csv_cache`, `debug_tools`, `checkpoint_manager`, `registry.load_sim_config` | 가장 많은 모듈 참조 |

### 4.5 Tool → 모듈 배분 (49 tools)

#### `tools/sim_lifecycle.py` (12 tools)

| # | Tool | 현재 Line | Helper 함수 |
|---|------|:---------:|-------------|
| 1 | `sim_discover` | L458 | — (env_detection 위임) |
| 2 | `mcp_config` | L479 | — (registry 위임) |
| 3 | `sim_start` | L500 | — (sim_runner 위임) |
| 4 | `connect_simulator` | L536 | `_auto_connect_all`, `_find_ready_file`, `_read_bridge_type` |
| 5 | `disconnect_simulator` | L637 | — |
| 6 | `sim_run` | L660 | — |
| 7 | `sim_stop` | L678 | — |
| 8 | `sim_restart` | L690 | — |
| 9 | `execute_tcl` | L702 | — |
| 10 | `sim_status` | L725 | — |
| 11 | `shutdown_simulator` | L1087 | — |
| 12 | `list_tests` | L178 | — |

**함께 이동하는 helper**: `_auto_connect_all`, `_find_ready_file`, `_read_bridge_type`

#### `tools/signal_inspection.py` (6 tools)

| # | Tool | 현재 Line |
|---|------|:---------:|
| 1 | `get_signal_value` | L782 |
| 2 | `describe_signal` | L800 |
| 3 | `find_drivers` | L812 |
| 4 | `list_signals` | L824 |
| 5 | `deposit_value` | L841 |
| 6 | `release_signal` | L856 |

#### `tools/waveform.py` (4 tools)

| # | Tool | 현재 Line | Helper 함수 |
|---|------|:---------:|-------------|
| 1 | `waveform_add_signals` | L873 | `_list_waveform_windows` |
| 2 | `waveform_zoom` | L956 | — |
| 3 | `cursor_set` | L971 | — |
| 4 | `take_waveform_screenshot` | L990 | — |

#### `tools/batch.py` (2 tools)

| # | Tool | 현재 Line |
|---|------|:---------:|
| 1 | `sim_batch_run` | L1486 |
| 2 | `sim_batch_regression` | L1598 |

**함께 이동**: `_prepare_dump_scope_internal` helper

#### `tools/checkpoint.py` (3 tools)

| # | Tool | 현재 Line |
|---|------|:---------:|
| 1 | `save_checkpoint` | L1173 |
| 2 | `restore_checkpoint` | L1214 |
| 3 | `cleanup_checkpoints` | L1305 |

#### `tools/simvision.py` (8 tools)

| # | Tool | 현재 Line |
|---|------|:---------:|
| 1 | `database_open` | L97 |
| 2 | `simvision_setup` | L139 |
| 3 | `simvision_start` | L223 |
| 4 | `simvision_live` | L348 |
| 5 | `simvision_live_stop` | L442 |
| 6 | `attach_to_simvision` | L2028 |
| 7 | `open_debug_view` | L2052 |
| 8 | `compare_waveforms` | L2180 |

#### `tools/debug.py` (14 tools)

| # | Tool | 현재 Line |
|---|------|:---------:|
| 1 | `set_breakpoint` | L745 |
| 2 | `run_debugger_mode` | L1002 |
| 3 | `watch_signal` | L1127 |
| 4 | `watch_clear` | L1144 |
| 5 | `probe_control` | L1156 |
| 6 | `probe_add_signals` | L1785 |
| 7 | `prepare_dump_scope` | L1750 |
| 8 | `bisect_signal` | L1252 |
| 9 | `bisect_signal_dump` | L1812 |
| 10 | `bisect_restore_and_debug` | L1357 |
| 11 | `extract_csv` | L1444 |
| 12 | `request_additional_signals` | L1899 |
| 13 | `generate_debug_tcl` | L1973 |
| 14 | `export_debug_context` | L2370 |

**합계**: 12+6+4+2+3+8+14 = **49 tools** ✓

---

## 5. Phase 3: sim_runner.py 분리 상세 설계

### 5.1 env_detection.py (~400줄)

**이동 대상 함수**:

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `_discover_sim_dir` | ~200 | sim_dir 자동 탐색 |
| `_analyze_tb_type` | ~495 | testbench 타입 판별 |
| `_auto_detect_runner` | ~397 | runner 자동 탐지 |
| `_ask_user_runner` | ~468 | runner 선택 UI |
| `_detect_shell_and_env` | ~260 | shell/EDA env 탐지 |
| `_detect_env_shell` | ~310 | xrun PATH 찾기 |
| `_resolve_eda_tools` | ~380 | EDA binary 경로 resolve |
| `_detect_bridge_tcl` | ~530 | mcp_bridge.tcl 위치 |
| `_detect_bridge_port` | ~550 | bridge port 탐지 |
| `_detect_setup_tcls` | ~560 | setup tcl 모드별 분류 |
| `_detect_run_dir` | ~1788 | run directory 탐지 |
| `_detect_vnc_display` | ~1856 | VNC display 탐지 |
| `_pick_default_mode` | ~1200 | default sim_mode 결정 |
| `_extract_script_name` | ~622 | exec_cmd에서 script 이름 추출 |

**추가 이동 대상** (Agent 분석 결과 추가):

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `_load_or_detect_runner` | ~601 | runner config 로드 또는 탐지 |
| `_resolve_eda_tools` | ~1055 | EDA binary 경로 resolve |
| `_detect_bridge_port` | ~1091 | bridge port 탐지 |

**의존성**: `ssh_run`, `_sq`, `_login_shell_cmd` — sim_runner.py에서 import

### 5.2 registry.py (~300줄)

**이동 대상 함수**:

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `_REGISTRY_PATH` | ~137 | registry 파일 경로 상수 |
| `load_registry` | ~141 | registry JSON 로드 |
| `save_registry` | ~148 | registry JSON 저장 |
| `load_sim_config` | ~155 | sim_config JSON 로드 |
| `save_sim_config` | ~165 | sim_config JSON 저장 |
| `_update_registry_from_config` | ~175 | config→registry 동기화 (async) |
| `_dot_get` | ~1370 | dot-notation getter |
| `_dot_set` | ~1380 | dot-notation setter |
| `_dot_delete` | ~1390 | dot-notation deleter |
| `config_action` | ~1400 | mcp_config 실행 로직 |

**추가 이동 대상**:

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `_write_json` | ~171 | JSON 파일 쓰기 헬퍼 |
| `_parse_json_value` | ~1428 | JSON 문자열 → Python 값 파싱 |

**의존성**: `ssh_run` (async registry update), `Path`

### 5.3 batch_runner.py (~400줄)

**이동 대상 함수**:

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `_run_batch_single` | ~670 | 단일 test batch 실행 |
| `_run_batch_regression` | ~794 | regression 순차 실행 |
| `_poll_batch_log` | ~1675 | log polling 루프 |
| `_resolve_sim_params` | ~1665 | sim_mode/extra_args resolve |
| `_resolve_test_name` | ~1736 | short name → full name |
| `_validate_extra_args` | ~58 | extra_args shell metachar 검증 |
| `_resolve_exec_cmd` | ~600 | runner exec_cmd 파싱 |

**추가 이동 대상**:

| 함수 | 현재 Line | 설명 |
|------|:---------:|------|
| `ExecInfo` | ~211 | dataclass — 실행 명령 정보 |
| `_resolve_exec_cmd` | ~217 | runner exec_cmd 파싱 → ExecInfo |

**의존성**: `ssh_run`, `_sq`, `_build_redirect`, `_login_shell_cmd`, `registry.load_sim_config`

### 5.4 sim_runner.py import 구조 변경

**현재** server.py가 sim_runner.py에서 import하는 19개 함수:
```python
from xcelium_mcp.sim_runner import (
    UserInputRequired, _build_redirect, _detect_vnc_display,
    _get_default_sim_dir, _load_or_detect_runner, _login_shell_cmd,
    _parse_shm_path, _parse_time_ns, _resolve_exec_cmd,
    _resolve_test_name, _run_batch_regression, _run_batch_single,
    _update_registry_from_config, config_action, load_registry,
    load_sim_config, run_full_discovery, ssh_run, start_simulation,
)
```

**변경 후** — 각 tool 모듈이 분리된 모듈에서 직접 import:

| 현재 import | 이동 후 모듈 | import 원 |
|------------|-----------|----------|
| `ssh_run` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import ssh_run` |
| `_build_redirect` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import build_redirect` |
| `_login_shell_cmd` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import login_shell_cmd` |
| `start_simulation` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import start_simulation` |
| `run_full_discovery` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import run_full_discovery` |
| `UserInputRequired` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import UserInputRequired` |
| `_parse_shm_path` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import _parse_shm_path` |
| `_parse_time_ns` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import _parse_time_ns` |
| `_get_default_sim_dir` | sim_runner.py (잔류) | `from xcelium_mcp.sim_runner import _get_default_sim_dir` |
| `_detect_vnc_display` | **env_detection.py** | `from xcelium_mcp.env_detection import _detect_vnc_display` |
| `_load_or_detect_runner` | **env_detection.py** | `from xcelium_mcp.env_detection import _load_or_detect_runner` |
| `_resolve_exec_cmd` | **batch_runner.py** | `from xcelium_mcp.batch_runner import _resolve_exec_cmd` |
| `_resolve_test_name` | **batch_runner.py** | `from xcelium_mcp.batch_runner import resolve_test_name` |
| `_run_batch_single` | **batch_runner.py** | `from xcelium_mcp.batch_runner import _run_batch_single` |
| `_run_batch_regression` | **batch_runner.py** | `from xcelium_mcp.batch_runner import _run_batch_regression` |
| `load_registry` | **registry.py** | `from xcelium_mcp.registry import load_registry` |
| `load_sim_config` | **registry.py** | `from xcelium_mcp.registry import load_sim_config` |
| `config_action` | **registry.py** | `from xcelium_mcp.registry import config_action` |
| `_update_registry_from_config` | **registry.py** | `from xcelium_mcp.registry import _update_registry_from_config` |

**server.py 축소 후**: server.py 자체는 이 import들을 갖지 않음. 각 `tools/*.py`가 필요한 모듈에서 직접 import.

### 5.5 run_full_discovery() 오케스트레이터 import

```python
# sim_runner.py (축소 후) — run_full_discovery에서 사용하는 import
from xcelium_mcp.env_detection import (
    _discover_sim_dir, _analyze_tb_type, _auto_detect_runner,
    _detect_shell_and_env, _detect_bridge_tcl, _detect_setup_tcls,
    _resolve_eda_tools, _detect_bridge_port, _detect_run_dir,
    _detect_vnc_display, _pick_default_mode, _extract_script_name,
)
from xcelium_mcp.registry import (
    load_sim_config, save_sim_config, _update_registry_from_config,
)
```

### 5.6 sim_runner.py 축소 후 (~500줄)

**잔류 함수**:

| 함수 | 설명 |
|------|------|
| `ssh_run` | 핵심 실행 함수 — 모든 모듈이 사용 |
| `_sq` | `shlex.quote` wrapper |
| `_build_redirect` | tcsh-safe redirect 생성 |
| `_login_shell_cmd` | login shell wrapper |
| `start_simulation` | bridge/batch 모드 분기 |
| `_start_bridge` | bridge mode 시뮬레이션 시작 |
| `_start_batch` | batch mode 위임 (→ batch_runner) |
| `run_full_discovery` | discovery 오케스트레이터 (→ env_detection 함수들 호출) |
| `_patch_legacy_run_script` | run_sim MCP_INPUT_TCL 패치 |
| `_update_simvisionrc` | .simvisionrc 업데이트 |
| `UserInputRequired` | 예외 클래스 |
| `_parse_shm_path` | SHM path 파싱 유틸리티 |
| `_parse_time_ns` | simulation time → ns 변환 |

---

## 6. Phase 4: Per-user 경로 상세 설계

### 6.1 `/tmp/` hardcoded 전수 목록

**sim_runner.py** (10곳):

| Line | 현재 경로 | 용도 |
|:----:|----------|------|
| L723 | `/tmp/mcp_batch_job.json` | batch job state file |
| L747 | `/tmp/mcp_batch_{ts}.log` | batch log |
| L755 | `/tmp/mcp_batch_pid_{ts}` | batch PID file |
| L823 | `/tmp/mcp_regression_job.json` | regression job state |
| L825 | `/tmp/mcp_regression_{ts}.log` | regression main log |
| L888 | `/tmp/mcp_regression_{ts}_{test}.log` | regression per-test log |
| L1573 | `/tmp/mcp_bridge_ready_*` | bridge ready file cleanup |
| L1584 | `/tmp/sim_start_{port}.log` | bridge start log |
| L1588 | `/tmp/mcp_setup_filtered_{port}.tcl` | filtered setup TCL |
| L1651 | `/tmp/mcp_bridge_ready_*` | bridge ready file scan |

**server.py** (9곳):

| Line | 현재 경로 | 용도 |
|:----:|----------|------|
| L245 | `/tmp/mcp_bridge_ready_*` | simvision_start scan |
| L323 | `/tmp/mcp_bridge_ready_*` | simvision_start scan |
| L591 | `/tmp/mcp_bridge_ready_*` | _auto_connect_all scan |
| L619 | `/tmp/mcp_bridge_ready_*` | _find_ready_file |
| L629 | `/tmp/mcp_bridge_ready_{port}` | _read_bridge_type |
| L1110 | `/tmp/mcp_bridge_ready_{port}` | shutdown ready cleanup |
| L1123 | `/tmp/mcp_bridge_ready_{port}` | shutdown ready cleanup |
| L2116 | `/tmp/mcp_bridge_ready_*` | attach_to_simvision |
| L2240 | `/tmp/mcp_bridge_ready_*` | compare_waveforms |

**mcp_bridge.tcl** (6곳):

| Line | 현재 경로 | 용도 |
|:----:|----------|------|
| L94 | `/tmp/mcp_bridge_ready_$port` | ready file 생성 |
| L331 | `/tmp/mcp_screenshot_[clock seconds].ps` | screenshot |
| L357 | `/tmp/mcp_init` | init snapshot dir |
| L443 | `/tmp/mcp_bridge_ready_$port` | ready file 삭제 |
| L649 | `/tmp/mcp_checkpoints` | checkpoint dir |
| L762 | `/tmp/mcp_bisect` | bisect checkpoint dir |

**합계**: sim_runner.py 10 + server.py 9 + mcp_bridge.tcl 6 = **25곳**

### 6.2 경로 패턴

| 항목 | 현재 | 변경 후 |
|------|------|---------|
| Ready file | `/tmp/mcp_bridge_ready_{port}` | `/tmp/xcelium_mcp_{uid}/bridge_ready_{port}` |
| Batch log | `/tmp/mcp_batch_{ts}.log` | `/tmp/xcelium_mcp_{uid}/batch_{ts}.log` |
| PID file | `/tmp/mcp_batch_pid_{ts}` | `/tmp/xcelium_mcp_{uid}/batch_pid_{ts}` |
| Job file | `/tmp/mcp_batch_job.json` | `/tmp/xcelium_mcp_{uid}/batch_job.json` |
| Regression log | `/tmp/mcp_regression_{ts}.log` | `/tmp/xcelium_mcp_{uid}/regression_{ts}.log` |
| sim_start log | `/tmp/sim_start_{port}.log` | `/tmp/xcelium_mcp_{uid}/sim_start_{port}.log` |
| Filtered TCL | `/tmp/mcp_setup_filtered_{port}.tcl` | `/tmp/xcelium_mcp_{uid}/setup_filtered_{port}.tcl` |

### 6.2 Python 측 구현

```python
# sim_runner.py
_USER_TMP: str = ""  # cached after first call

async def _get_user_tmp_dir() -> str:
    """Get per-user temp directory. Creates on first call."""
    global _USER_TMP
    if _USER_TMP:
        return _USER_TMP
    r = await ssh_run("id -u", timeout=5)
    uid = r.strip()
    _USER_TMP = f"/tmp/xcelium_mcp_{uid}"
    await ssh_run(f"mkdir -p {_USER_TMP}", timeout=5)
    return _USER_TMP
```

### 6.3 Tcl 측 구현

```tcl
# mcp_bridge.tcl — init section
set uid [exec id -u]
set user_tmp "/tmp/xcelium_mcp_$uid"
file mkdir $user_tmp
set ready_file "$user_tmp/bridge_ready_$port"
```

### 6.4 동기화 보장

Python과 Tcl이 **동일한 경로 규칙** (`/tmp/xcelium_mcp_{uid}/`)을 사용:
- Python: `id -u` via `ssh_run` → uid
- Tcl: `exec id -u` → uid
- 동일 사용자 = 동일 uid = 동일 디렉토리

---

## 7. Phase 5: Public API Naming

### 7.1 rename 대상

| 현재 (private) | 변경 후 (public) | 사용처 |
|---------------|-----------------|--------|
| `_sq` | `sq` | 전체 (43+ 호출) |
| `_build_redirect` | `build_redirect` | batch_runner, sim_runner |
| `_login_shell_cmd` | `login_shell_cmd` | env_detection, sim_runner |
| `_validate_extra_args` | `validate_extra_args` | batch_runner, tools/batch |
| `_resolve_sim_params` | `resolve_sim_params` | batch_runner, sim_runner |
| `_resolve_test_name` | `resolve_test_name` | tools/sim_lifecycle, batch |
| `_get_user_tmp_dir` | `get_user_tmp_dir` | 전체 |

### 7.2 유지 항목 (모듈 내부 전용)

`_auto_connect_all`, `_find_ready_file`, `_read_bridge_type`, `_list_waveform_windows`, `_prepare_dump_scope_internal`, `_poll_batch_log`, `_detect_*` 시리즈 — 각 모듈 내부에서만 사용.

---

## 8. Phase 6 잔여: Batch Polling 개선

P6-0 (nohup detach)과 P7-1 (`~` expand)은 이미 완료 (커밋 `1b566ee`).

### 8.0 실측 Baseline (2026-04-02, TOP015)

| 모드 | 시간 | 비고 |
|------|:----:|------|
| `run_sim` 직접 실행 | **196s** | compile+elab+sim 전체 |
| Bridge (`sim_start` → `sim_run` 완주 → shutdown) | **222s** | bridge overhead +26s |
| Batch (`sim_batch_run`, B-0 수정 후) | **129s** | incremental compile 효과 |
| Batch (nohup detach 수정 전) | **ERROR** | 15s timeout → 결과 못 받음 |

나머지 항목:

### 8.1 P6-1: Adaptive Polling

```python
# batch_runner.py — _poll_batch_log 수정
async def _poll_batch_log(log_file: str, timeout: float, prefix: str = "") -> str:
    import time as _time
    deadline = _time.time() + timeout
    interval = 2.0  # start at 2s
    while _time.time() < deadline:
        log = await ssh_run(f"tail -5 {log_file} 2>/dev/null")
        if any(kw in log for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")):
            break
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 10.0)  # 2 → 3 → 4.5 → 6.75 → 10 (cap)
    # ...
```

### 8.2 P6-3: Incremental Compile 적용성 분석

**결론: 현재 legacy TB에서는 `xrun -update` 직접 적용 불가.**

이유: `run_sim`은 `xrun`이 아닌 `xmvlog` → `run_compile` → `xmelab` → `xmsim` 분리 호출.
`xrun -update`는 `xrun` 통합 플로우 전용 옵션.

**대안**: 이미 2회째 batch 실행 시 **129s** (1회째 196s 대비 34% 빠름) — Xcelium이 자체적으로 `inca/` 캐시를 활용하여 unchanged 파일을 skip하고 있음. 추가 최적화 불필요.

**P6-3은 삭제**. legacy TB 환경에서는 Xcelium 자체 incremental이 이미 동작 중.

### 8.3 P6-2: SSH Polling 통합

```python
# tail + grep in single command
log = await ssh_run(
    f"tail -5 {log_file} 2>/dev/null; "
    f"grep -cE 'PASS|FAIL|Errors:|\\$finish' {log_file} 2>/dev/null"
)
```

### 8.4 P6-4: Snapshot Reuse 적용성 분석

**결론: legacy TB에서는 적용 불가.**

이유: test별로 `top.v`가 `` `include `INC_FNAME ``으로 다른 .v 파일을 compile-time에 포함.
checkpoint는 특정 test의 compile 결과를 포함하므로 다른 test에 재사용할 수 없음.
동일 test 반복 실행 시에는 Xcelium 자체 incremental이 이미 동작 (129s vs 196s).

**P6-4은 삭제**.

### 8.5 P6-5: Completion Marker

시뮬레이션 완료 시 marker 파일 생성:
```python
# _run_batch_single 완료 후
await ssh_run(f"touch {log_file}.done", timeout=5)
```

Polling에서 marker 확인:
```python
done_check = await ssh_run(f"test -f {log_file}.done && echo DONE", timeout=5)
if "DONE" in done_check:
    break
```

---

## 9. Change Matrix

### 9.1 신규 파일

| # | 파일 | 줄 수 (예상) | Phase |
|---|------|:----------:|:-----:|
| C-1 | `bridge_manager.py` | ~80 | Phase 1 |
| C-2 | `tools/__init__.py` | ~1 | Phase 2 |
| C-3 | `tools/sim_lifecycle.py` | ~500 | Phase 2 |
| C-4 | `tools/signal_inspection.py` | ~150 | Phase 2 |
| C-5 | `tools/waveform.py` | ~200 | Phase 2 |
| C-6 | `tools/batch.py` | ~200 | Phase 2 |
| C-7 | `tools/checkpoint.py` | ~200 | Phase 2 |
| C-8 | `tools/simvision.py` | ~450 | Phase 2 |
| C-9 | `tools/debug.py` | ~500 | Phase 2 |
| C-10 | `env_detection.py` | ~400 | Phase 3 |
| C-11 | `registry.py` | ~300 | Phase 3 |
| C-12 | `batch_runner.py` | ~400 | Phase 3 |

### 9.2 수정 파일

| # | 파일 | 변경 내용 | Phase |
|---|------|----------|:-----:|
| C-13 | `server.py` | 2410줄 → ~100줄 (global 제거 + tool import) | Phase 1+2 |
| C-14 | `sim_runner.py` | 1920줄 → ~500줄 (함수 이동 + BridgeManager DI) | Phase 1+3 |
| C-15 | `mcp_bridge.tcl` | per-user 경로 | Phase 4 |

### 9.3 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `tcl_bridge.py` | TCP bridge 로직 독립 |
| `csv_cache.py` | CSV 캐시 독립 |
| `checkpoint_manager.py` | checkpoint 관리 독립 |
| `debug_tools.py` | debug context 독립 |
| `screenshot.py` | screenshot 독립 |

---

## 10. Test Plan

### 10.1 Regression Test

Phase별 완료 시마다 v4.1 21개 기능 테스트 전수 재실행:

| # | Test | 검증 대상 |
|---|------|----------|
| T1 | `sim_discover` | env_detection 분리 후 정상 동작 |
| T2 | `mcp_config` | registry 분리 후 정상 동작 |
| T3 | `sim_start` (bridge) | BridgeManager DI 후 bridge 시작 |
| T4 | `connect_simulator` (auto) | BridgeManager 연결 |
| T5 | `sim_run` | bridge 경유 simulation 실행 |
| T6-T8 | `database_open` | SimVision tool 분리 후 |
| T9-T14 | SimVision tools | simvision.py 분리 후 |
| T15-T16 | `sim_batch_run/regression` | batch.py + batch_runner 분리 후 |
| T17 | `save/restore checkpoint` | checkpoint.py 분리 후 |
| T18 | `shutdown_simulator` | sim_lifecycle 분리 후 |
| T19-T21 | Error paths | 전체 분리 후 |

### 10.2 Phase별 검증 기준

| Phase | 검증 기준 |
|-------|----------|
| Phase 1 | `python -c "import xcelium_mcp.server"` 성공, circular import 없음 |
| Phase 2 | 49 tools 전수 등록 (`mcp.list_tools()` 개수), T1-T21 PASS |
| Phase 3 | sim_runner.py 500줄 이하, T1-T21 PASS |
| Phase 4 | 2 사용자 동시 sim_start 충돌 없음 |
| Phase 5 | `grep "from.*import _" src/` 결과 0건 (cross-module) |

---

## 11. Implementation Order

```
Phase 1 (BridgeManager) ── 1일
  1. bridge_manager.py 신규 생성
  2. server.py: global → BridgeManager 인스턴스
  3. sim_runner.py: import server 제거 → BridgeManager 인자
  4. 21개 regression test

Phase 2 (server.py 분리) ── 2일
  1. tools/ 디렉토리 + __init__.py
  2. 7개 tool 모듈 순차 생성 (signal_inspection → waveform → checkpoint 순으로 작은 것부터)
  3. server.py 축소
  4. 49 tools 등록 확인 + 21개 regression test

Phase 3 (sim_runner.py 분리) ── 1일
  1. registry.py 추출
  2. env_detection.py 추출
  3. batch_runner.py 추출
  4. sim_runner.py 축소 확인 + 21개 regression test

Phase 4 (per-user 경로) ── 0.5일
  1. _get_user_tmp_dir() 구현
  2. Python 경로 일괄 변경
  3. mcp_bridge.tcl 변경
  4. 동기화 검증

Phase 5 (naming) ── 0.5일
  1. cross-module 함수 rename
  2. import 정리
  3. 21개 regression test
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-02 | Initial design draft | hoseung.lee |
