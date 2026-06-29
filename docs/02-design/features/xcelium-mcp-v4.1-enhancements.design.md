# Design: xcelium-mcp v4.1 — Enhancements

> **Feature**: Auto port, multi-bridge, SimVision GUI tool, database_open, simvision_live, _resolve_sim_params, test_discovery
>
> **Date**: 2026-04-01
> **Version**: v1.2 (Plan sync: P1b-8/P1b-9 추가, cached_tests 스키마, 합계 35)
> **Plan**: `docs/01-plan/features/xcelium-mcp-v4.1-enhancements.plan.md`
> **Predecessor**: `docs/02-design/features/xcelium-mcp-v4-sim-lifecycle.design.md`
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | (1) xmsim bridge + SimVision GUI 동시 실행 시 port 9876 충돌. (2) SimVision `database` 구문이 xmsim과 다름 (`database open` vs `database -open -shm`). (3) waveform window 미존재 시 에러 + 신호 중복 추가. (4) SimVision 자동 실행/연결 불가. (5) `sim_start` 후 별도 `connect_simulator` 필요 |
| **Solution** | (1) mcp_bridge.tcl auto port + ready file에 `port type timestamp` 기록. (2) `_xmsim_bridge`/`_simvision_bridge` 독립 slot + 49개 tool 자동 라우팅. (3) `database_open`, `simvision_setup`, `simvision_start`, `simvision_live` 신규 tool. (4) `sim_start`/`simvision_start` auto-connect 통합. (5) `_resolve_sim_params()` 단일 진입점 — `args_format` dict/`mode_defaults` common+mode/`extra_args` 3개 tool 통합. (6) `test_discovery.command` 기반 `list_tests` 범용화 |
| **Function UX Effect** | `sim_start` 1회로 시뮬레이션 + xmsim bridge 자동 연결. SimVision 동시 실행 시 port 충돌 없이 tool 자동 라우팅. `connect_simulator`는 수동 재연결용으로만 사용 |
| **Core Value** | xmsim AI 디버깅 + SimVision 사용자 파형 확인 병행. 동시 운용으로 디버깅 효율 극대화 |

---

## 1. 파일 구조 및 변경 범위

```
src/xcelium_mcp/
├── server.py              # [수정] _bridge → _xmsim_bridge/_simvision_bridge 분리
│                          #        49개 tool bridge getter 변경
│                          #        connect_simulator: port=0, target="auto" 파라미터
│                          #        disconnect_simulator: target 파라미터
│                          #        shutdown_simulator: ready file 삭제
│                          #        신규 tool: database_open, simvision_setup,
│                          #                   simvision_start, simvision_live,
│                          #                   simvision_live_stop, list_tests
│                          #        sim_start auto-connect (connect_simulator 불필요)
├── sim_runner.py          # [수정] run_full_discovery에 D-12 _detect_run_dir 추가
│                          #        _detect_vnc_display() 신규
│                          #        start_simulation _start_bridge auto-connect 통합
│                          #        _parse_shm_path() 신규
│                          #        _parse_time_ns() 신규
├── csv_cache.py           # 변경 없음
├── checkpoint_manager.py  # 변경 없음
├── debug_tools.py         # 변경 없음
├── tcl_bridge.py          # 변경 없음
└── screenshot.py          # 변경 없음

tcl/
└── mcp_bridge.tcl         # [수정] bridge_type 감지 (info commands waveform)
                           #        auto port 루프 (port_range 10)
                           #        ready file 형식: "port type timestamp"
                           #        shutdown 시 ready file 삭제
                           #        vwait 유지, MCP_SETUP_TCL 유지
```

---

## 2. `.mcp_sim_config.json` v2.1 스키마

### 2.1 v4.1 변경 사항

```json
{
  "version": 2,
  "runner": {
    "type": "shell",
    "script": "run_sim",
    "run_dir": "run",
    "script_has_cd": true,
    "login_shell": "/bin/tcsh",
    "script_shell": "/bin/bash",
    "env_files": ["/users/hoseung.lee/git.clone/venezia-t0/design/top/sim/ncsim/eda.env"],
    "env_shell": "tcsh",
    "source_separately": true,
    "args_format": {
      "rtl":      "-test {test_name} --",
      "gate":     "-test {test_name} -gate post --",
      "ams_rtl":  "-test {test_name} -ams --",
      "ams_gate": "-test {test_name} -amsf -gate post --"
    },
    "mode_defaults": {
      "common":   {"timeout": 120, "probe_strategy": "all", "extra_args": ""},
      "rtl":      {},
      "gate":     {"timeout": 1800, "probe_strategy": "selective"},
      "ams_rtl":  {"timeout": 3600, "probe_strategy": "selective"},
      "ams_gate": {"timeout": 3600, "probe_strategy": "selective"}
    },
    "setup_tcls": {
      "rtl": "scripts/setup_rtl.tcl",
      "gate": "scripts/setup_gate.tcl"
    },
    "default_mode": "rtl"
  },
  "bridge": {
    "tcl_path": "/opt/xcelium-mcp/tcl/mcp_bridge.tcl",
    "port": 9876
  },
  "eda_tools": {
    "simvisdbutil": "/apps/eda/cdns/XCELIUM2209/tools/bin/simvisdbutil",
    "xmsim": "/apps/eda/cdns/XCELIUM2209/tools/bin/xmsim",
    "xrun": "/apps/eda/cdns/XCELIUM2209/tools/bin/xrun"
  },
  "test_discovery": {
    "command": "ls tb_tests/*.v | xargs -I{} basename {} .v",
    "cached_tests": ["VENEZIA_TOP000_stimulation_test", "..."],
    "cached_at": "2026-04-01T12:00:00"
  }
}
```

| 필드 | 상태 | 설명 |
|------|------|------|
| `runner.run_dir` | **신규** | 시뮬레이션 실행 디렉토리 (sim_dir 기준 상대경로). cds.lib 위치 |
| `runner.script_has_cd` | **신규** | runner script 내부에 cd 명령이 있는지 여부 (sim_discover가 자동 탐지). `true`면 sim_start는 sim_dir에서 시작, `false`면 run_dir에서 시작 |
| `runner.args_format` | v4 string → **v4.1 mode별 dict** | mode별 test_name 전달 형식. v4 string도 하위 호환 (전 mode 동일 적용) |
| `runner.mode_defaults` | **신규** | mode별 기본 설정 (timeout, probe_strategy). `sim_start`가 sim_mode에 따라 자동 적용 |
| `runner.shm_stem` | **폐기** | glob 기반 SHM 탐색으로 대체 (`ls -td dump/*{test_name}*.shm`) |
| `test_discovery.command` | **신규** | 테스트 이름을 한 줄씩 출력하는 명령. `sim_discover`가 tb_type 기반 자동 설정 |
| `test_discovery.cached_tests` | **신규** | `list_tests` / `sim_discover` 실행 결과 캐시. `_resolve_test_name`이 읽기 전용 사용 |
| `test_discovery.cached_at` | **신규** | 캐시 생성 시각 (ISO 8601) |

### 2.2 v1 호환

- `version` 필드 없거나 `1`: v1 호환 모드 (누락 필드 런타임 fallback)
- `run_dir` 없으면 `"run"` 기본값
- `sim_discover(force=True)`로 v2 업그레이드

---

## 3. Phase 1: Auto Port + Multi-bridge (13항목)

### 3.1 mcp_bridge.tcl 변경: bridge_type 감지 + auto port + ready file

**기존 mcp_bridge.tcl `::mcp_bridge::init` 전체 교체:**

```tcl
proc ::mcp_bridge::init {} {
    variable port
    variable server_socket
    variable bridge_type

    # --- v4.1: Bridge type detection ---
    # SimVision has the 'waveform' command; xmsim does not.
    # This is evaluated once at startup to determine bridge identity.
    if {[info commands waveform] ne ""} {
        set bridge_type "simvision"
    } else {
        set bridge_type "xmsim"
    }
    puts "MCP Bridge: type=$bridge_type"

    # --- v4.1: Auto port discovery ---
    # Allow port override via environment variable (backward compatible)
    if {[info exists ::env(MCP_BRIDGE_PORT)]} {
        set port $::env(MCP_BRIDGE_PORT)
    }
    # else: use default variable port 9876 (set in namespace)

    # Close existing server if re-sourced
    if {$server_socket ne ""} {
        catch {close $server_socket}
        set server_socket ""
    }

    # Try port range: port .. port+port_range-1
    variable port_range
    set base_port $port
    set found 0
    for {set p $base_port} {$p < $base_port + $port_range} {incr p} {
        if {![catch {socket -server ::mcp_bridge::accept $p} sock]} {
            set server_socket $sock
            set port $p
            set found 1
            puts "MCP Bridge: listening on port $p"
            break
        }
        puts "MCP Bridge: port $p busy, trying next..."
    }
    if {!$found} {
        puts "MCP Bridge: ERROR - all ports $base_port-[expr {$base_port + $port_range - 1}] busy"
        return
    }

    # --- v4.1: Ready file with port + type + timestamp ---
    # Format: "port type timestamp"
    # Python side reads this to determine which port and bridge type.
    set ready_file "/tmp/mcp_bridge_ready_$port"
    if {[catch {
        set f [open $ready_file w]
        puts $f "$port $bridge_type [clock seconds]"
        close $f
    } err]} {
        puts "MCP Bridge: WARNING: could not create ready file: $err"
    }

    # Save init snapshot for sim_restart fallback
    ::mcp_bridge::on_init

    # v4: Source project setup TCL via MCP_SETUP_TCL env var
    # (unchanged from v4 — pre-filtered by Python _start_bridge)
    if {[info exists ::env(MCP_SETUP_TCL)] && $::env(MCP_SETUP_TCL) ne ""} {
        if {[file exists $::env(MCP_SETUP_TCL)]} {
            puts "MCP Bridge: sourcing setup TCL: $::env(MCP_SETUP_TCL)"
            source $::env(MCP_SETUP_TCL)
            puts "MCP Bridge: setup TCL loaded"
        } else {
            puts "MCP Bridge: WARNING - MCP_SETUP_TCL not found: $::env(MCP_SETUP_TCL)"
        }
    }
}
```

**namespace 변수 추가 (기존 변수 리스트에 추가):**

```tcl
namespace eval ::mcp_bridge {
    variable server_socket ""
    variable client_channel ""
    variable port 9876
    variable port_range 10          ;# v4.1: 9876~9885 auto-try
    variable bridge_type "xmsim"    ;# v4.1: "xmsim" or "simvision"
    variable cmd_buffer ""
    variable async_running 0
    variable async_done 0
    variable async_stop_reason ""
    variable watch_ids [list]
    variable _checkpoint_dir ""
    variable _checkpoint_name ""
    variable _init_snapshot_dir ""
}
```

**`do_shutdown` 변경 -- ready file 삭제 추가:**

```tcl
proc ::mcp_bridge::do_shutdown {channel} {
    variable port

    # 1. Close all SHM databases to flush data
    if {[catch {
        set dbs [database -list]
        foreach db $dbs {
            catch {database -close $db}
        }
    } err]} {
        catch {database -close ../dump/ci_top.shm}
    }

    # 2. Notify client before termination
    ::mcp_bridge::send_ok $channel "shutdown:ok"

    # 3. v4.1: Remove ready file
    set ready_file "/tmp/mcp_bridge_ready_$port"
    catch {file delete $ready_file}

    # 4. Schedule finish after returning to event loop
    after 100 {
        set ::mcp_bridge::_shutdown_flag 1
        finish
    }
}
```

**파일 끝 (vwait) 유지 -- v4와 동일:**

```tcl
::mcp_bridge::init

puts "MCP Bridge: ready (waiting for client)"
if {![info exists ::mcp_bridge::_shutdown_flag]} {
    set ::mcp_bridge::_shutdown_flag 0
}
vwait ::mcp_bridge::_shutdown_flag
```

### 3.2 Multi-bridge slot: 전역 변수 + getter 함수 (server.py)

**기존 코드 삭제:**

```python
# 삭제:
_bridge: TclBridge | None = None

def _get_bridge() -> TclBridge:
    if _bridge is None or not _bridge.connected:
        raise ConnectionError("Not connected to SimVision. Call connect_simulator first.")
    return _bridge
```

**대체 코드:**

```python
# ---------------------------------------------------------------------------
# v4.1: Dual bridge slots — xmsim + SimVision 독립 관리
# ---------------------------------------------------------------------------

_xmsim_bridge: TclBridge | None = None
_simvision_bridge: TclBridge | None = None


def _get_xmsim_bridge() -> TclBridge:
    """Return the active xmsim bridge or raise.

    Used by: sim_run, sim_stop, sim_restart, get_signal_value,
    describe_signal, find_drivers, deposit_value, release_signal,
    watch_signal, watch_clear, save_checkpoint, restore_checkpoint,
    bisect_signal, bisect_restore_and_debug, probe_control,
    probe_add_signals, shutdown_simulator, set_breakpoint (18 tools).
    """
    if _xmsim_bridge is None or not _xmsim_bridge.connected:
        raise ConnectionError(
            "Not connected to xmsim. Call sim_start or connect_simulator(target='xmsim') first."
        )
    return _xmsim_bridge


def _get_simvision_bridge() -> TclBridge:
    """Return the active SimVision bridge or raise.

    Used by: waveform_add_signals, waveform_zoom, cursor_set,
    simvision_setup, take_waveform_screenshot, database_open,
    attach_to_simvision, open_debug_view,
    simvision_live, simvision_live_stop (10 tools).
    """
    if _simvision_bridge is None or not _simvision_bridge.connected:
        raise ConnectionError(
            "Not connected to SimVision. "
            "Call simvision_start or connect_simulator(target='simvision') first."
        )
    return _simvision_bridge


def _get_bridge(target: str = "auto") -> TclBridge:
    """Backward-compatible bridge getter. xmsim 우선.

    Used by: execute_tcl, list_signals, sim_status, run_debugger_mode
    (4 tools with target parameter).

    Args:
        target: "xmsim" | "simvision" | "auto" (xmsim 우선).
    """
    if target == "xmsim":
        return _get_xmsim_bridge()
    if target == "simvision":
        return _get_simvision_bridge()
    # auto: xmsim preferred
    if _xmsim_bridge and _xmsim_bridge.connected:
        return _xmsim_bridge
    if _simvision_bridge and _simvision_bridge.connected:
        return _simvision_bridge
    raise ConnectionError(
        "Not connected to any bridge. "
        "Call sim_start, simvision_start, or connect_simulator first."
    )
```

**제약: 각 type 최대 1개**

- xmsim bridge: 최대 1개 (`_xmsim_bridge`)
- SimVision bridge: 최대 1개 (`_simvision_bridge`)
- 유효 조합: xmsim만 / SimVision만 / xmsim + SimVision 동시 (최대 2개)
- 같은 type 재연결 시 기존 disconnect 후 새 연결

### 3.3 `connect_simulator` 개선: port=0 + target="auto"

```python
@mcp.tool()
async def connect_simulator(
    host: str = "localhost",
    port: int = 0,
    target: str = "auto",
    timeout: float = 30.0,
) -> str:
    """Connect to simulator bridge(s).

    v4.1: port=0 + target="auto" enables ready-file-based auto-detection.
    Ready files (/tmp/mcp_bridge_ready_{port}) contain "port type timestamp".

    Auto-detect modes:
      port=0, target="auto"      -> scan all ready files, connect each to its slot
      port=0, target="xmsim"     -> find xmsim ready file, connect to _xmsim_bridge
      port=0, target="simvision" -> find simvision ready file, connect to _simvision_bridge
      port=N, target="auto"      -> read type from ready file for port N
      port=N, target="xmsim"     -> connect port N to _xmsim_bridge (explicit)

    Args:
        host:    Bridge host (default localhost).
        port:    TCP port. 0 = auto-detect from ready files.
        target:  "xmsim" | "simvision" | "auto". Determines which slot to use.
        timeout: Connection timeout in seconds.
    """
    global _xmsim_bridge, _simvision_bridge

    if port == 0 and target == "auto":
        # Scan all ready files, connect each to appropriate slot
        return await _auto_connect_all(host, timeout)

    if port == 0:
        # Find ready file for specific target type
        port, detected_type = await _find_ready_file(target)
        target = detected_type

    if target == "auto":
        # port specified but target=auto: read type from ready file
        target = await _read_bridge_type(port)

    # Disconnect existing same-type bridge (max 1 per type)
    if target == "simvision" and _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
    elif target != "simvision" and _xmsim_bridge and _xmsim_bridge.connected:
        await _xmsim_bridge.disconnect()

    bridge = TclBridge(host=host, port=port, timeout=timeout)
    try:
        ping = await bridge.connect()
    except Exception as e:
        return f"ERROR: Connection to {target} at {host}:{port} failed: {type(e).__name__}: {e}"

    # Assign to appropriate slot
    if target == "simvision":
        _simvision_bridge = bridge
    else:
        _xmsim_bridge = bridge

    # Get context
    try:
        where = await bridge.execute("where")
    except TclError:
        where = "(unknown)"

    return f"Connected to {target} at {host}:{port} (ping={ping})\nCurrent position: {where}"
```

### 3.3b Ready file 스캔/파싱 헬퍼 함수

```python
async def _auto_connect_all(host: str, timeout: float = 30.0) -> str:
    """Scan all ready files, connect to each, assign to appropriate slot.

    Ready file format: "port type timestamp" (one per line in each file).
    Files: /tmp/mcp_bridge_ready_{port}

    Stale files (bridge unreachable) are skipped but not deleted
    (bridge process may still be starting up).
    """
    global _xmsim_bridge, _simvision_bridge
    results: list[str] = []

    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    if not r.strip():
        return "No bridges found. Run sim_start or simvision_start first."

    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        try:
            bport = int(parts[0])
        except ValueError:
            continue
        btype = parts[1]

        # Skip if same-type bridge already connected in this scan
        if btype == "simvision" and _simvision_bridge and _simvision_bridge.connected:
            results.append(f"{btype}:{bport} (already connected)")
            continue
        if btype == "xmsim" and _xmsim_bridge and _xmsim_bridge.connected:
            results.append(f"{btype}:{bport} (already connected)")
            continue

        bridge = TclBridge(host=host, port=bport, timeout=timeout)
        try:
            ping = await bridge.connect()
            if btype == "simvision":
                _simvision_bridge = bridge
            else:
                _xmsim_bridge = bridge
            results.append(f"{btype}:{bport} (ping={ping})")
        except Exception as e:
            results.append(f"{btype}:{bport} FAILED ({e})")

    if not results:
        return "No bridges found. Run sim_start or simvision_start first."
    return "Connected:\n" + "\n".join(f"  {r}" for r in results)


async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type.

    Args:
        target: "xmsim" or "simvision"
    Returns:
        (port, type) tuple
    Raises:
        RuntimeError if no matching ready file found.
    """
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == target:
            return int(parts[0]), parts[1]
    raise RuntimeError(
        f"No {target} bridge found in ready files.\n"
        f"Start {'sim_start' if target == 'xmsim' else 'simvision_start'} first."
    )


async def _read_bridge_type(port: int) -> str:
    """Read bridge type from ready file for given port.

    Returns: "xmsim" or "simvision". Defaults to "xmsim" if file not found.
    """
    r = await ssh_run(f"cat /tmp/mcp_bridge_ready_{port} 2>/dev/null")
    parts = r.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return "xmsim"  # fallback: assume xmsim for backward compatibility
```

### 3.4 `disconnect_simulator` 확장: target 파라미터

```python
@mcp.tool()
async def disconnect_simulator(target: str = "all") -> str:
    """Disconnect from bridge(s).

    v4.1: Supports selective disconnect by target type.

    Args:
        target: "xmsim" | "simvision" | "all" (default).
    """
    global _xmsim_bridge, _simvision_bridge
    results: list[str] = []

    if target in ("xmsim", "all") and _xmsim_bridge and _xmsim_bridge.connected:
        await _xmsim_bridge.disconnect()
        _xmsim_bridge = None
        results.append("xmsim: disconnected")

    if target in ("simvision", "all") and _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None
        results.append("simvision: disconnected")

    return "\n".join(results) if results else f"No {target} bridge connected."
```

### 3.5 Tool 자동 라우팅: 49개 tool 전수 매핑

**49개 tool의 bridge getter 변경 전수 목록:**

#### 3.5.1 xmsim 전용 (18개) -- `_get_bridge()` -> `_get_xmsim_bridge()`

| # | Tool | 변경 위치 (server.py 내) |
|---|------|------------------------|
| 1 | `sim_run` | `bridge = _get_bridge()` -> `bridge = _get_xmsim_bridge()` |
| 2 | `sim_stop` | 동일 패턴 |
| 3 | `sim_restart` | 동일 패턴 |
| 4 | `get_signal_value` | 동일 패턴 |
| 5 | `describe_signal` | 동일 패턴 |
| 6 | `find_drivers` | 동일 패턴 |
| 7 | `deposit_value` | 동일 패턴 |
| 8 | `release_signal` | 동일 패턴 |
| 9 | `watch_signal` | 동일 패턴 |
| 10 | `watch_clear` | 동일 패턴 |
| 11 | `save_checkpoint` | 동일 패턴 |
| 12 | `restore_checkpoint` | `bridge = _get_bridge()` -> `bridge = _get_xmsim_bridge()` |
| 13 | `bisect_signal` | Mode B 분기 내 `bridge = _get_bridge()` -> `bridge = _get_xmsim_bridge()` |
| 14 | `bisect_restore_and_debug` | `bridge = _get_bridge()` (2회) -> `_get_xmsim_bridge()` |
| 15 | `set_breakpoint` | 동일 패턴 |
| 16 | `probe_control` | 동일 패턴 |
| 17 | `probe_add_signals` | 동일 패턴 |
| 18 | `shutdown_simulator` | 특수 처리 (아래 3.5.5 참조) |

**변경 코드 예시 (sim_run):**

```python
@mcp.tool()
async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
    """Run the simulation, optionally for a specified duration.
    ...
    """
    bridge = _get_xmsim_bridge()  # v4.1: xmsim 전용
    cmd = f"run {duration}" if duration else "run"
    await bridge.execute(cmd, timeout=timeout)
    try:
        where = await bridge.execute("where")
    except (TclError, asyncio.TimeoutError, ConnectionError):
        where = "(position unknown)"
    return f"Simulation advanced. Current position: {where}"
```

#### 3.5.2 SimVision 전용 (10개) -- `_get_bridge()` -> `_get_simvision_bridge()`

| # | Tool | 비고 |
|---|------|------|
| 1 | `waveform_add_signals` | 전면 재작성 (3.5.3 참조) |
| 2 | `waveform_zoom` | `_get_bridge()` -> `_get_simvision_bridge()` |
| 3 | `cursor_set` | 동일 패턴 |
| 4 | `take_waveform_screenshot` | 동일 패턴 |
| 5 | `simvision_setup` | **신규** (Phase 2) |
| 6 | `database_open` | **신규** (Phase 2) |
| 7 | `attach_to_simvision` | 내부 `connect_simulator` 호출 -> `connect_simulator(target="simvision")` 변경 |
| 8 | `open_debug_view` | 내부 `connect_simulator` + `_get_bridge()` -> `_get_simvision_bridge()` |
| 9 | `simvision_live` | **신규** (Phase 2) |
| 10 | `simvision_live_stop` | **신규** (Phase 2) |

**변경 코드 예시 (waveform_zoom):**

```python
@mcp.tool()
async def waveform_zoom(start_time: str, end_time: str) -> str:
    """Set the waveform viewer time range (zoom to region).
    ...
    """
    bridge = _get_simvision_bridge()  # v4.1: SimVision 전용
    result = await bridge.execute(f"waveform xview limits {start_time} {end_time}")
    return f"Waveform zoomed to {start_time} - {end_time}. {result}"
```

#### 3.5.3 양쪽/target 지정 (4개) -- `_get_bridge()` 유지 (target 파라미터 추가)

| # | Tool | 변경 |
|---|------|------|
| 1 | `execute_tcl` | `target` 파라미터 추가. `bridge = _get_bridge(target)` |
| 2 | `list_signals` | 동일 |
| 3 | `sim_status` | 동일 |
| 4 | `run_debugger_mode` | 동일 |

**변경 코드 (execute_tcl):**

```python
@mcp.tool()
async def execute_tcl(
    tcl_cmd: str,
    timeout: int = 30,
    target: str = "auto",
) -> str:
    """Execute arbitrary Tcl command in the connected bridge session.

    Args:
        tcl_cmd: Tcl command to execute.
        timeout: Response timeout in seconds.
        target:  "xmsim" | "simvision" | "auto" (xmsim preferred).
    """
    bridge = _get_bridge(target)
    return await bridge.execute(tcl_cmd, timeout=float(timeout))
```

**변경 코드 (list_signals):**

```python
@mcp.tool()
async def list_signals(
    scope: str,
    pattern: str = "*",
    target: str = "auto",
) -> str:
    """List signals in a scope.
    ...
    Args:
        scope:   Hierarchical scope path.
        pattern: Glob pattern.
        target:  "xmsim" | "simvision" | "auto".
    """
    bridge = _get_bridge(target)
    result = await bridge.execute(f"describe {scope}.{pattern}")
    return result
```

**변경 코드 (sim_status):**

```python
@mcp.tool()
async def sim_status(target: str = "auto") -> str:
    """Get current simulation status.
    ...
    Args:
        target: "xmsim" | "simvision" | "auto".
    """
    bridge = _get_bridge(target)
    results: list[str] = []
    for label, cmd in [("Position", "where"), ("Scope", "scope")]:
        try:
            val = await bridge.execute(cmd)
            results.append(f"{label}: {val}")
        except TclError as e:
            results.append(f"{label}: (error: {e})")
    return "\n".join(results)
```

**변경 코드 (run_debugger_mode):**

```python
@mcp.tool()
async def run_debugger_mode(target: str = "auto") -> list:
    """Comprehensive debug snapshot.
    ...
    Args:
        target: "xmsim" | "simvision" | "auto".
    """
    bridge = _get_bridge(target)
    # ... (나머지 기존 로직 동일, _get_bridge() 호출을 bridge 변수로 대체)
```

#### 3.5.4 bridge 시작 + auto-connect (2개)

| # | Tool | 변경 |
|---|------|------|
| 1 | `sim_start` | ready 후 `_xmsim_bridge` 자동 배정 (3.6 참조) |
| 2 | `simvision_start` | ready 후 `_simvision_bridge` 자동 배정 (Phase 2) |

#### 3.5.5 `shutdown_simulator` 특수 처리

```python
@mcp.tool()
async def shutdown_simulator(target: str = "xmsim") -> str:
    """Safely shutdown the simulator, preserving SHM waveform data.

    v4.1: Supports target selection. Default xmsim (primary use case).
    SimVision shutdown closes the GUI application.

    Args:
        target: "xmsim" | "simvision". Which bridge to shut down.
    """
    global _xmsim_bridge, _simvision_bridge

    if target == "simvision":
        bridge = _get_simvision_bridge()
        try:
            # SimVision exit: close gracefully
            resp = await bridge.execute_safe("exit")
            return f"SimVision shutdown: {resp.body}"
        except (ConnectionError, asyncio.TimeoutError):
            return "SimVision shutdown completed (connection closed)."
        finally:
            _simvision_bridge = None
    else:
        bridge = _get_xmsim_bridge()
        try:
            resp = await bridge.execute_safe("__SHUTDOWN__")
            return f"Simulator shutdown: {resp.body}"
        except (ConnectionError, asyncio.TimeoutError):
            return "Simulator shutdown completed (connection closed)."
        finally:
            _xmsim_bridge = None
```

#### 3.5.6 수동 재연결/전환 (2개)

- `connect_simulator`: 3.3에서 설계 완료
- `disconnect_simulator`: 3.4에서 설계 완료

#### 3.5.7 bridge 불필요 (13개) -- 변경 없음

| Tool | 이유 |
|------|------|
| `sim_discover` | registry/파일 기반 |
| `mcp_config` | config 파일 편집 |
| `list_tests` | 파일 시스템 탐색 |
| `sim_batch_run` | ssh_run 기반 |
| `sim_batch_regression` | ssh_run 기반 |
| `extract_csv` | simvisdbutil CLI |
| `bisect_signal_dump` | CSV 오프라인 분석 |
| `compare_waveforms` | CSV diff 또는 SimVision 기동 |
| `generate_debug_tcl` | 파일 생성 |
| `export_debug_context` | 파일 생성 |
| `request_additional_signals` | 텍스트 출력 |
| `prepare_dump_scope` | 파일 편집 |
| `cleanup_checkpoints` | 파일 시스템 |

**`compare_waveforms` 특수 처리**: simvision mode에서 `connect_simulator()` -> `connect_simulator(target="simvision")`로 변경, `_get_bridge()` -> `_get_simvision_bridge()`로 변경.

**`open_debug_view` 특수 처리**: 동일하게 `connect_simulator(target="simvision")`, `_get_bridge()` -> `_get_simvision_bridge()`.

**`sim_batch_run` / `bisect_restore_and_debug` 내부의 `_get_bridge()` 호출**: restore 후 probe 추가 시 사용. -> `_get_xmsim_bridge()`로 변경.

### 3.6 `sim_start` auto-connect: bridge ready 후 `_xmsim_bridge` 자동 배정

**`sim_runner.py` `_start_bridge()` 변경:**

기존 v4의 S-5 (ready file polling)에서 "ready file 존재 확인 -> connect_simulator 안내" 패턴을:
"ready file에서 port/type 읽기 -> TclBridge 연결 -> `_xmsim_bridge` 배정" 패턴으로 교체.

```python
async def _start_bridge(
    sim_dir: str,
    config: dict,
    test_name: str,
    setup_tcl: str,
    sim_mode: str,
    timeout: int,
    extra_args: str = "",
) -> str:
    """Start simulation in bridge mode via legacy run script + env vars.

    v4.1: Auto-connect to _xmsim_bridge after bridge ready.
    connect_simulator is no longer required in normal workflow.
    """
    # --- S-1 ~ S-4: 기존 v4 로직 동일 (시뮬레이션 프로세스 시작) ---
    runner = config["runner"]
    bridge_cfg = config["bridge"]
    port = bridge_cfg.get("port", 9876)
    bridge_tcl = bridge_cfg.get("tcl_path", "")
    script = runner.get("script", "run_sim")

    # S-2: Check existing xmsim
    ps = await ssh_run("pgrep -la xmsim 2>/dev/null", timeout=5)
    if ps.strip():
        return (
            f"ERROR: xmsim already running:\n{ps.strip()}\n"
            f"Use shutdown_simulator or 'pkill -f xmsim' first."
        )

    # v4.1: mode_defaults 적용 — common 먼저, mode별로 override
    mode_defaults = runner.get("mode_defaults", {})
    common_cfg = mode_defaults.get("common", {})
    mode_cfg = mode_defaults.get(sim_mode, {})
    effective_cfg = {**common_cfg, **mode_cfg}  # mode가 common override
    effective_timeout = effective_cfg.get("timeout", timeout)
    probe_strategy = effective_cfg.get("probe_strategy", "all")
    # extra_args: mode_defaults에서 가져온 것 + 파라미터로 받은 1회성 override 합침
    cfg_extra = effective_cfg.get("extra_args", "")
    all_extra_args = f"{cfg_extra} {extra_args}".strip()

    # v4.1: Disconnect existing xmsim bridge (max 1)
    import xcelium_mcp.server as _srv
    if _srv._xmsim_bridge and _srv._xmsim_bridge.connected:
        await _srv._xmsim_bridge.disconnect()
        _srv._xmsim_bridge = None

    # S-3: Clean ALL xmsim ready files (not just one port — auto port may differ)
    await ssh_run("rm -f /tmp/mcp_bridge_ready_*", timeout=5)
    # Note: If SimVision bridge is running, its ready file is also removed.
    # This is acceptable because SimVision will recreate it if still alive.
    # Alternative: only remove xmsim-type files, but parsing all files is slower.

    # S-4: Start via run script (identical to v4)
    script_shell = runner.get("script_shell", runner.get("env_shell", "/bin/sh"))
    # args_format: v4.1 mode별 dict 또는 v4 단일 string (하위 호환)
    args_format_raw = runner.get("args_format", "-test {test_name} --")
    if isinstance(args_format_raw, dict):
        args_format = args_format_raw.get(sim_mode, args_format_raw.get("rtl", "-test {test_name} --"))
    else:
        args_format = args_format_raw  # v4 string — 전 mode 동일
    test_args = args_format.format(test_name=test_name)
    if all_extra_args:
        test_args = f"{test_args} {all_extra_args}"
    log_file = f"/tmp/sim_start_{port}.log"

    # Pre-filter setup TCL (identical to v4)
    filtered_tcl = f"/tmp/mcp_setup_filtered_{port}.tcl"
    await ssh_run(
        f"sed '"
        f"/^[[:space:]]*run[[:space:]]*$/d; "
        f"/^[[:space:]]*run[[:space:]]/d; "
        f"/^[[:space:]]*exit[[:space:]]*$/d; "
        f"/^[[:space:]]*exit[[:space:]]/d; "
        f"/^[[:space:]]*finish[[:space:]]*$/d; "
        f"/^[[:space:]]*finish[[:space:]]/d; "
        f"/^[[:space:]]*database[[:space:]]*-close/d"
        f"' {setup_tcl} > {filtered_tcl}",
        timeout=10,
    )

    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", script_shell)
    login_shell = runner.get("login_shell", "/bin/sh")
    inner_parts = [
        f"setenv MCP_INPUT_TCL {bridge_tcl}",
        f"setenv MCP_SETUP_TCL {filtered_tcl}",
    ]
    if runner.get("source_separately") and env_files:
        for ef in env_files:
            inner_parts.append(f"source {ef}")
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = f"{env_shell} -c '{inner_cmd}'"
    else:
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = _login_shell_cmd(login_shell, inner_cmd)
    # Working directory 원칙:
    #   최종 cwd는 항상 {sim_dir}/{run_dir} (cds.lib 위치)
    #   script_has_cd=True  → cd {sim_dir}에서 시작 (script 내부 cd로 run_dir 이동)
    #   script_has_cd=False → cd {sim_dir}/{run_dir}에서 시작 (직접 이동)
    #   simvision_start는 항상 cd {sim_dir}/{run_dir} (simvision에 자체 cd 없음)
    run_dir = runner.get("run_dir", "run")
    if runner.get("script_has_cd", False):
        cwd = sim_dir
    else:
        cwd = f"{sim_dir}/{run_dir}"
    cmd = (
        f"cd {cwd} && "
        f"(nohup {shell_cmd} "
        f"{_build_redirect(log_file)} < /dev/null &)"
    )
    await ssh_run(cmd, timeout=15)

    # --- S-5 v4.1: Poll for bridge ready + AUTO-CONNECT ---
    from xcelium_mcp.tcl_bridge import TclBridge
    for i in range(effective_timeout // 2):
        await asyncio.sleep(2)
        # Scan all ready files for xmsim type (auto port support)
        r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
        for line in r.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "xmsim":
                actual_port = int(parts[0])
                bridge = TclBridge(host="localhost", port=actual_port)
                try:
                    ping = await bridge.connect()
                    _srv._xmsim_bridge = bridge
                    return (
                        f"Simulation started and connected (bridge mode, {sim_mode}).\n"
                        f"  test: {test_name}\n"
                        f"  setup_tcl: {setup_tcl}\n"
                        f"  port: {actual_port}\n"
                        f"  ping: {ping}\n"
                        f"  log: {log_file}\n\n"
                        f"Ready. sim_run, get_signal_value etc. available immediately."
                    )
                except Exception:
                    continue  # retry next poll

    # Timeout
    log_tail = await ssh_run(f"tail -20 {log_file} 2>/dev/null", timeout=5)
    return f"ERROR: bridge not ready after {timeout}s.\nLog tail:\n{log_tail}"
```

### 3.7 `_detect_run_dir()` -- sim_runner.py

```python
async def _detect_run_dir(sim_dir: str, runner_info: dict) -> dict:
    """Detect simulation run directory and whether runner script has internal cd.

    Run directory contains cds.lib/hdl.var and is where xmsim/simvision
    should be launched from.

    Search order:
      1. {sim_dir}/run*/ directories with cds.lib or hdl.var
      2. Runner script 'cd' command parsing → also sets script_has_cd
      3. {sim_dir}/ itself (if cds.lib present)
      4. Multiple candidates -> UserInputRequired
      5. No candidate -> UserInputRequired

    Returns: {"run_dir": str, "script_has_cd": bool}
      run_dir: relative path from sim_dir (e.g. "run", "run_rtl", ".")
      script_has_cd: True if runner script contains cd command to run_dir
    """
    candidates: list[str] = []
    script_has_cd = False

    # 1. run*/ pattern directories with cds.lib or hdl.var
    r = await ssh_run(
        f"find {sim_dir} -maxdepth 1 -type d -name 'run*' 2>/dev/null"
    )
    for d in r.strip().splitlines():
        if not d.strip():
            continue
        has_cds = await ssh_run(
            f"test -f {d}/cds.lib -o -L {d}/cds.lib -o -f {d}/hdl.var && echo YES || echo NO"
        )
        if "YES" in has_cds:
            candidates.append(d.split("/")[-1])  # relative path

    # 2. Parse 'cd' from runner script → detect script_has_cd
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    cd_targets: list[str] = []
    r = await ssh_run(f"grep -E '^[[:space:]]*cd[[:space:]]+' {script_path} 2>/dev/null | head -3")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and '$' not in parts[1]:
            cd_target = parts[1].strip("'\"").rstrip("/")
            if cd_target:
                cd_targets.append(cd_target)
                if cd_target not in candidates:
                    candidates.append(cd_target)

    # script_has_cd: script 내부에 run_dir로 cd하는 명령이 있음
    script_has_cd = len(cd_targets) > 0

    # 3. sim_dir itself
    has_cds = await ssh_run(
        f"test -f {sim_dir}/cds.lib -o -L {sim_dir}/cds.lib && echo YES || echo NO"
    )
    if "YES" in has_cds and "." not in candidates:
        candidates.append(".")

    # 4. Single candidate -> return
    if len(candidates) == 1:
        return {"run_dir": candidates[0], "script_has_cd": script_has_cd}

    # 5. Multiple -> ask user
    if len(candidates) > 1:
        raise UserInputRequired(
            f"Multiple run directories found. Select one:\n"
            + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
        )

    # 6. None found -> ask user
    raise UserInputRequired(
        "Could not detect run directory.\n"
        "Enter the directory where xmsim/simvision should run:\n"
        f"  (relative to {sim_dir})\n"
        "  Example: run\n"
        "  Example: ."
    )
```

**`run_full_discovery()` 변경 -- D-12 추가:**

```python
async def run_full_discovery(sim_dir: str = "", force: bool = False) -> str:
    # ... (D-1 ~ D-10 동일) ...

    # D-12 (v4.1): run_dir + script_has_cd detection
    run_info = await _detect_run_dir(sim_dir, runner_info)
    run_dir = run_info["run_dir"]
    script_has_cd = run_info["script_has_cd"]

    # Config v2 조립 (run_dir, script_has_cd 추가)
    config = {
        "version": 2,
        "runner": {
            "type": runner_info.get("runner", "shell"),
            "script": script_name,
            "run_dir": run_dir,           # v4.1 신규
            "script_has_cd": script_has_cd, # v4.1 신규
            **shell_env,
            "setup_tcls": setup_tcls,
            "default_mode": _pick_default_mode(setup_tcls),
        },
        "bridge": {
            "tcl_path": bridge_tcl,
            "port": bridge_port,
        },
        "eda_tools": eda_tools,
    }
    # ... (나머지 동일) ...
```

### 3.8 `_detect_vnc_display()` -- sim_runner.py

```python
async def _detect_vnc_display() -> str:
    """Detect current user's VNC display.

    Search order:
      1. vncserver -list (TigerVNC/TurboVNC) -> parse display number
      2. ps -u $USER | grep Xvnc -> extract :N from args
      3. $DISPLAY env var (if set and not :0, which is physical display)

    Returns: ":N" (e.g. ":1", ":2") or "" if not found.
    """
    # 1. vncserver -list (first active session)
    r = await ssh_run("vncserver -list 2>/dev/null | grep -E '^:'")
    if r.strip():
        # Format: ":2    12345" -> ":2"
        display = r.strip().splitlines()[0].split()[0]
        return display

    # 2. Xvnc process
    r = await ssh_run(
        "ps -u $(whoami) -o args 2>/dev/null | grep Xvnc | grep -v grep | grep -oE ':[0-9]+'"
    )
    if r.strip():
        return r.strip().splitlines()[0]

    # 3. $DISPLAY fallback (skip :0 = physical display, not VNC)
    r = await ssh_run("echo $DISPLAY")
    if r.strip() and r.strip() != ":0":
        return r.strip()

    return ""
```

### 3.9 `.mcp_sim_config.json` 스키마 변경 요약

2.1절에 완전 기술됨. 추가 스키마 변경 사항 없음.

### 3.10 Ready file cleanup: shutdown/disconnect 시 삭제

**mcp_bridge.tcl**: `do_shutdown`에서 ready file 삭제 (3.1절의 `do_shutdown` 참조).

**server.py `disconnect_simulator`**: 서버 측에서는 ready file을 삭제하지 않음. 이유: bridge 프로세스(xmsim/SimVision)가 아직 실행 중일 수 있으며, 재연결 가능성이 있다. Ready file은 bridge 프로세스 종료 시(shutdown) 삭제된다.

**server.py `shutdown_simulator`**: 이미 `__SHUTDOWN__` 메타 커맨드가 Tcl 측에서 ready file을 삭제한다 (3.1절). Python 측 추가 삭제 불필요.

---

## 3b. Phase 1b: 스키마 변경 영향 — 기존 tool 수정 (9항목)

v4.1 스키마 변경(`args_format` dict, `mode_defaults`, `extra_args`, `run_dir`, `script_has_cd`)에 영향받는 기존 tool/함수를 수정.

| # | 항목 | 변경 내용 | 파일 |
|---|------|----------|------|
| P1b-1 | **`_resolve_sim_params()` 신규** — 스키마 파라미터 해석 단일 진입점 | args_format dict/string, mode_defaults common+mode merge, extra_args 합침. 향후 스키마 변경 시 이 함수만 수정 | sim_runner.py |
| P1b-2 | `_start_bridge()` — `_resolve_sim_params` 호출로 전환 | 기존 inline args_format/mode_defaults 로직 제거 → `_resolve_sim_params` 위임 | sim_runner.py |
| P1b-3 | `_run_batch_single()` — `_resolve_sim_params` 호출 + `sim_mode`, `extra_args` 파라미터 추가 | `_resolve_sim_params`에서 timeout/extra_args 가져와 적용 | sim_runner.py |
| P1b-4 | `sim_batch_run` MCP tool — `sim_mode`, `extra_args` 파라미터 추가 | `_run_batch_single`에 전달 | server.py |
| P1b-5 | `sim_batch_regression` MCP tool — `sim_mode`, `extra_args` 파라미터 추가 | P1b-4와 동일 패턴 | server.py |
| P1b-6 | `sim_discover` — `args_format` dict + `mode_defaults` 탐지/생성 | runner script mode flag 파싱, 기본값 생성 | sim_runner.py |
| P1b-7 | `sim_discover` — `test_discovery.command` 자동 설정 + `cached_tests` 초기 캐시 (D-14) | tb_type 기반 command 생성 + command 실행 결과 캐시 | sim_runner.py |
| P1b-8 | **`_resolve_test_name()` 헬퍼 신규** — short name → full test name | 캐시(`test_discovery.cached_tests`) 읽기만. 캐시 없으면 `list_tests` 1회 실행 (mcp_config 경유 캐시 저장). substring 매칭: 1개=반환, 0개=에러, 2+=후보 표시 | sim_runner.py |
| P1b-9 | **test_name 일관성** — 모든 test_name 파라미터에 `_resolve_test_name` 적용 | `sim_start`(필수), `sim_batch_run`(필수), `sim_batch_regression`(리스트), `simvision_start`(선택) | server.py |

### 3b.1 `_resolve_sim_params()` — 스키마 해석 단일 진입점

향후 스키마 변경 시 이 함수만 수정하면 `sim_start`, `sim_batch_run`, `sim_batch_regression` 모두 자동 반영 (Single Point of Change).

```python
def _resolve_sim_params(
    runner: dict,
    sim_mode: str = "rtl",
    extra_args: str = "",
    timeout: int = 600,
) -> dict:
    """스키마에서 시뮬레이션 파라미터를 해석하는 단일 진입점.

    향후 스키마 변경 시 이 함수만 수정하면
    sim_start, sim_batch_run, sim_batch_regression 모두 반영.

    Returns:
        {
            "test_args_format": str,  # mode별 args_format (resolved)
            "timeout": int,           # effective timeout
            "probe_strategy": str,    # "all" | "selective"
            "extra_args": str,        # common + mode + 1회성 합침
        }
    """
    # 1. args_format: dict → mode별 선택, string → 전 mode 동일 (하위 호환)
    args_raw = runner.get("args_format", "-test {test_name} --")
    if isinstance(args_raw, dict):
        test_args_format = args_raw.get(sim_mode, args_raw.get("rtl", "-test {test_name} --"))
    else:
        test_args_format = args_raw  # v4 string — 전 mode 동일

    # 2. mode_defaults: common + mode merge (mode가 common override)
    mode_defaults = runner.get("mode_defaults", {})
    common_cfg = mode_defaults.get("common", {})
    mode_cfg = mode_defaults.get(sim_mode, {})
    effective = {**common_cfg, **mode_cfg}

    # 3. extra_args: config(common+mode) + 1회성 파라미터 합침
    cfg_extra = effective.get("extra_args", "")
    all_extra = f"{cfg_extra} {extra_args}".strip()

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
    }
```

### 3b.2 `_start_bridge()` / `_run_batch_single()` 호출 구조

`_resolve_sim_params`를 호출하는 3개 tool의 공통 패턴:

```
sim_start → _start_bridge
  ├─ params = _resolve_sim_params(runner, sim_mode, extra_args)
  ├─ test_args = params["test_args_format"].format(test_name=test_name)
  ├─ if params["extra_args"]: test_args += " " + params["extra_args"]
  └─ effective_timeout = params["timeout"]

sim_batch_run → _run_batch_single
  ├─ params = _resolve_sim_params(runner, sim_mode, extra_args)
  ├─ info = _resolve_exec_cmd(runner)   ← 명령 구성 (스키마 무관)
  ├─ cmd = info.cmd.format(test_name=test_name)
  └─ cmd += params["extra_args"]

sim_batch_regression → _run_batch_single
  └─ (위와 동일)
```

**`_start_bridge()` 내 inline 로직을 `_resolve_sim_params` 호출로 교체:**

```python
# 기존 (v4 inline):
mode_defaults = runner.get("mode_defaults", {})
common_cfg = mode_defaults.get("common", {})
mode_cfg = mode_defaults.get(sim_mode, {})
effective_cfg = {**common_cfg, **mode_cfg}
effective_timeout = effective_cfg.get("timeout", timeout)
probe_strategy = effective_cfg.get("probe_strategy", "all")
cfg_extra = effective_cfg.get("extra_args", "")
all_extra_args = f"{cfg_extra} {extra_args}".strip()
args_format_raw = runner.get("args_format", "-test {test_name} --")
if isinstance(args_format_raw, dict):
    args_format = args_format_raw.get(sim_mode, args_format_raw.get("rtl", "-test {test_name} --"))
else:
    args_format = args_format_raw
test_args = args_format.format(test_name=test_name)

# 변경 (v4.1 _resolve_sim_params):
params = _resolve_sim_params(runner, sim_mode, extra_args, timeout)
test_args = params["test_args_format"].format(test_name=test_name)
all_extra_args = params["extra_args"]
effective_timeout = params["timeout"]
probe_strategy = params["probe_strategy"]
if all_extra_args:
    test_args = f"{test_args} {all_extra_args}"
```

### 3b.3 `sim_batch_run` / `sim_batch_regression` — `sim_mode`, `extra_args` 파라미터 추가

```python
@mcp.tool()
async def sim_batch_run(
    test_names: list[str],
    sim_dir: str = "",
    sim_mode: str = "rtl",       # v4.1 신규
    extra_args: str = "",         # v4.1 신규
    parallel: int = 1,
    timeout: int = 600,
) -> str:
    """Run multiple simulations in batch.

    v4.1: sim_mode and extra_args now supported.
    sim_mode selects the mode-specific args_format and mode_defaults.
    extra_args appended after mode_defaults.extra_args (one-time override).

    Args:
        test_names: List of test names to run.
        sim_dir:    Simulation directory. Empty = registry default.
        sim_mode:   Simulation mode ("rtl", "gate", "ams_rtl", "ams_gate").
        extra_args: Additional args appended to each test invocation.
        parallel:   Max parallel simulations.
        timeout:    Per-test timeout in seconds.
    """
    ...
    # sim_mode, extra_args를 _run_batch_single에 전달
    await _run_batch_single(
        test_name=t,
        runner=runner,
        sim_dir=resolved_dir,
        sim_mode=sim_mode,
        extra_args=extra_args,
        timeout=timeout,
    )
    ...


@mcp.tool()
async def sim_batch_regression(
    sim_dir: str = "",
    sim_mode: str = "rtl",       # v4.1 신규
    extra_args: str = "",         # v4.1 신규
    parallel: int = 1,
    timeout: int = 600,
) -> str:
    """Run full regression (all tests in test_discovery list).

    v4.1: sim_mode and extra_args now supported.

    Args:
        sim_dir:    Simulation directory. Empty = registry default.
        sim_mode:   Simulation mode.
        extra_args: Additional args for all tests.
        parallel:   Max parallel simulations.
        timeout:    Per-test timeout in seconds.
    """
    ...
```

### 3b.4 `sim_discover` D-14 — `test_discovery.command` 자동 설정

`sim_discover`가 tb_type을 탐지한 후 `test_discovery.command`에 기본값을 자동 저장:

```python
# D-14: test_discovery.command 자동 생성 (tb_type 기반) + cached_tests 초기 캐시
if tb_type == "ncsim_legacy":
    cmd = f"ls {sim_dir}/tb_tests/*.v 2>/dev/null | xargs -I{{}} basename {{}} .v"
elif tb_type == "uvm":
    cmd = (
        f"grep -rh 'extends uvm_test' {sim_dir} --include='*.sv' --include='*.svh' 2>/dev/null "
        f"| grep -oE 'class \\w+' | sed 's/class //' | sort -u"
    )
elif tb_type == "sv_directed":
    cmd = (
        f"grep -rh '^\\s*program ' {sim_dir} --include='*.sv' 2>/dev/null "
        f"| grep -oE 'program \\w+' | sed 's/program //' | sort -u"
    )
else:
    cmd = ""  # 사용자가 mcp_config로 설정

# P1b-7 (v4.1): command 실행 후 cached_tests 초기 캐시 저장 (환경 탐지 시점)
cached_tests: list[str] = []
if cmd:
    r = await ssh_run(f"cd {sim_dir} && {cmd}", timeout=30)
    cached_tests = [t.strip() for t in r.strip().splitlines() if t.strip()]

config["test_discovery"] = {
    "command": cmd,
    **({"cached_tests": cached_tests, "cached_at": datetime.now().isoformat()}
       if cached_tests else {}),
}
```

**캐시 저장 타이밍**: `sim_discover`는 discovery 시점에 바로 command 실행 + 결과 캐시. 이후 `list_tests` 호출 시 캐시가 있으면 command 재실행 없이 즉시 반환.

**호환성**: `mcp_config set test_discovery.command` 로 언제든 override 가능.

### 3b.5 `_resolve_test_name()` — short name → full test name (P1b-8)

캐시(`test_discovery.cached_tests`)에서 substring 매칭으로 full test name을 반환하는 헬퍼. 쓰기 없음.

```python
async def _resolve_test_name(short_name: str, sim_dir: str = "") -> str:
    """Short name → full test name. 캐시에서 검색, 없으면 list_tests 1회 실행.

    "TOP015" → "VENEZIA_TOP015_i2c_8bit_offset_test"
    정확히 1개 매칭 → 반환. 0개 → ValueError. 2+ → ValueError(후보 표시).

    캐시 읽기만 수행. 캐시 갱신은 list_tests → mcp_config 경유 (쓰기 일원화).

    Args:
        short_name: Short or full test name. If full name matches exactly, returned as-is.
        sim_dir:    Simulation directory. Empty = registry default.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    config = await load_sim_config(resolved_dir) if resolved_dir else None
    cached = config.get("test_discovery", {}).get("cached_tests", []) if config else []

    if not cached:
        # 캐시 없으면 list_tests 1회 실행 → mcp_config 경유 캐시 저장
        await list_tests(sim_dir=resolved_dir)
        config = await load_sim_config(resolved_dir)
        cached = config.get("test_discovery", {}).get("cached_tests", []) if config else []

    # 정확 일치
    if short_name in cached:
        return short_name

    # substring 매칭
    matches = [t for t in cached if short_name in t]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) == 0:
        raise ValueError(
            f"No test matching '{short_name}'.\n"
            f"Run list_tests() to see available tests."
        )
    else:
        raise ValueError(
            f"Multiple tests match '{short_name}':\n"
            + "\n".join(f"  {m}" for m in matches)
            + "\nSpecify more precisely."
        )
```

**사용 예:**
```
_resolve_test_name("TOP015")      → "VENEZIA_TOP015_i2c_8bit_offset_test"
_resolve_test_name("i2c_8bit")    → "VENEZIA_TOP015_i2c_8bit_offset_test"
_resolve_test_name("i2c")         → ValueError: Multiple tests match 'i2c': ...
_resolve_test_name("TOP999")      → ValueError: No test matching 'TOP999'.
_resolve_test_name("VENEZIA_TOP015_i2c_8bit_offset_test")  → 그대로 반환 (정확 일치)
```

### 3b.6 test_name 일관성 — 모든 test_name 파라미터에 적용 (P1b-9)

`_resolve_test_name` 호출 위치:

```python
# sim_start: 필수 test_name → resolve (server.py)
async def sim_start(test_name: str, sim_dir: str = "", ...):
    test_name = await _resolve_test_name(test_name, sim_dir)
    ...

# sim_batch_run: 필수 test_name → resolve (server.py)
async def sim_batch_run(test_name: str, sim_dir: str = "", ...):
    test_name = await _resolve_test_name(test_name, sim_dir)
    ...

# sim_batch_regression: test_list 각 항목 → resolve (server.py)
async def sim_batch_regression(test_list: list[str], sim_dir: str = "", ...):
    test_list = [await _resolve_test_name(t, sim_dir) for t in test_list]
    ...

# simvision_start: 선택 test_name → 비어있지 않을 때만 resolve (server.py)
async def simvision_start(test_name: str = "", sim_dir: str = "", ...):
    if test_name:
        test_name = await _resolve_test_name(test_name, sim_dir)
    ...
```

**일관성 매트릭스:**

| tool | test_name | `_resolve_test_name` | `sim_mode` | `extra_args` | `_resolve_sim_params` |
|------|:---------:|:-------------------:|:----------:|:------------:|:---------------------:|
| `sim_start` | 필수 | 필수 | ✅ | ✅ | ✅ |
| `sim_batch_run` | 필수 | 필수 | ✅ | ✅ | ✅ |
| `sim_batch_regression` | list 필수 | 각 항목 | ✅ | ✅ | ✅ |
| `simvision_start` | 선택 | 있을 때만 | ❌ | ❌ | ❌ |

---

## 4. Phase 2: SimVision GUI tool (8항목)

### 4.1 `database_open` tool -- bridge type 기반 구문 선택

```python
@mcp.tool()
async def database_open(shm_path: str, name: str = "") -> str:
    """Open SHM database. Uses correct syntax based on bridge type.

    SimVision: 'database open {shm_path} [-name {name}]'
    xmsim:     'database -open {shm_path} -shm'

    Routes to SimVision bridge (primary use case: GUI waveform viewing).
    Falls back to xmsim bridge if SimVision not connected.

    Args:
        shm_path: SHM database path (absolute or relative to run_dir).
        name:     Optional database alias name.
    """
    # SimVision bridge first (primary use case: GUI waveform viewing)
    if _simvision_bridge and _simvision_bridge.connected:
        bridge = _simvision_bridge
        name_opt = f" -name {name}" if name else ""
        try:
            result = await bridge.execute(f"database open {shm_path}{name_opt}")
        except TclError as e:
            return f"ERROR: SimVision database open failed: {e}"
        return f"Database opened (SimVision): {result}"

    # xmsim fallback (different syntax)
    if _xmsim_bridge and _xmsim_bridge.connected:
        bridge = _xmsim_bridge
        try:
            result = await bridge.execute(f"database -open {shm_path} -shm")
        except TclError as e:
            return f"ERROR: xmsim database open failed: {e}"
        return f"Database opened (xmsim): {result}"

    return (
        "ERROR: No bridge connected.\n"
        "Call sim_start (xmsim) or simvision_start (SimVision) first."
    )
```

### 4.2 `simvision_setup` tool -- 일괄 환경 설정

```python
@mcp.tool()
async def simvision_setup(
    shm_path: str = "",
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
) -> str:
    """One-shot SimVision setup: open SHM + create waveform + add signals + zoom.

    Convenience tool equivalent to:
      database_open -> waveform new -> waveform_add_signals -> waveform_zoom

    Requires active SimVision bridge connection (simvision_start first).

    Args:
        shm_path:   SHM database path. Empty = skip database open.
        signals:    Signal paths to add to waveform.
        zoom_start: Zoom start time (e.g. "0ns"). Empty = full range.
        zoom_end:   Zoom end time (e.g. "10ms"). Empty = full range.
    """
    bridge = _get_simvision_bridge()  # SimVision 전용
    results: list[str] = []

    # 1. Open SHM database
    if shm_path:
        db_result = await database_open(shm_path)
        results.append(db_result)

    # 2. Create waveform window if none exists
    try:
        current = await bridge.execute("waveform using")
    except TclError:
        current = ""
    if not current.strip():
        wname = await bridge.execute("waveform new")
        results.append(f"Waveform window created: {wname}")

    # 3. Add signals (reuse waveform_add_signals for dedup logic)
    if signals:
        sig_result = await waveform_add_signals(signals=signals)
        results.append(sig_result)

    # 4. Zoom
    if zoom_start and zoom_end:
        await bridge.execute(f"waveform xview limits {zoom_start} {zoom_end}")
        results.append(f"Zoomed to {zoom_start} - {zoom_end}")

    return "\n".join(results) if results else "No actions performed."
```

**역할 분리: `simvision_start` vs `simvision_setup`**

| 측면 | `simvision_start` | `simvision_setup` |
|------|-------------------|-------------------|
| 수준 | OS 프로세스 레벨 | SimVision 내부 설정 |
| 기능 | SimVision 프로세스 시작 + bridge 연결 | waveform window + SHM open + signal + zoom |
| 전제 | SimVision 미실행 (또는 기실행 감지) | SimVision bridge 이미 연결됨 |
| 호출 순서 | 먼저 | 나중 |
| 자동 호출 | `simvision_setup` 자동 호출 안 함 | 독립 사용 |

### 4.3 `waveform_add_signals` 개선 -- window 자동 생성 + 중복 검사

**기존 코드 전면 교체:**

```python
@mcp.tool()
async def waveform_add_signals(
    signals: list[str],
    group_name: str = "",
    window_name: str = "",
) -> str:
    """Add signals to SimVision waveform.

    v4.1 improvements:
      - Window auto-creation: if no waveform window exists, creates one
      - Duplicate detection: skips signals already in current window
      - Window selection: target specific window by name

    Args:
        signals:     Signal paths to add.
        group_name:  Group within window. Empty = no group.
        window_name: Target waveform window. Empty = current (or auto-create).
    """
    bridge = _get_simvision_bridge()
    results: list[str] = []

    # 1. Window selection/creation
    if window_name:
        # Switch to specified window
        try:
            await bridge.execute(f"waveform using {window_name}")
        except TclError:
            available = await _list_waveform_windows(bridge)
            return f"ERROR: Window '{window_name}' not found. Available: {available}"
    else:
        # Use current window, or create if none exists
        try:
            current = await bridge.execute("waveform using")
            if not current.strip():
                raise TclError("empty")
        except TclError:
            wname = await bridge.execute("waveform new")
            results.append(f"Waveform window created: {wname}")

    # 2. Duplicate detection -- query existing signals in current window
    try:
        existing_raw = await bridge.execute("waveform signals -format list")
        existing_set = set(existing_raw.strip().splitlines())
    except TclError:
        existing_set = set()

    new_signals = [s for s in signals if s not in existing_set]
    skipped = len(signals) - len(new_signals)

    if not new_signals:
        return f"All {len(signals)} signal(s) already in waveform (skipped)."

    # 3. Add signals (with optional group)
    sig_str = " ".join(new_signals)
    if group_name:
        # Create group if not exists (catch silently if already present)
        try:
            await bridge.execute(f"waveform add -groups {{{group_name}}}")
        except TclError:
            pass  # group already exists
        result = await bridge.execute(
            f"waveform add -using {group_name} -signals {{{sig_str}}}"
        )
    else:
        result = await bridge.execute(f"waveform add -signals {{{sig_str}}}")

    results.append(
        f"Added {len(new_signals)}, skipped {skipped} (duplicate). {result}"
    )
    return "\n".join(results)


async def _list_waveform_windows(bridge: TclBridge) -> str:
    """List available waveform windows. Returns comma-separated names or '(none)'."""
    try:
        r = await bridge.execute("waveform get -name")
        return r.strip() if r.strip() else "(none)"
    except TclError:
        return "(error listing windows)"
```

### 4.4 `list_tests` tool -- `test_discovery.command` 기반 범용 탐색

**배경**: `sim_start(test_name=...)` 호출 시 정확한 test name을 알아야 함. 환경마다 테스트 정의 방식이 다름 (파일명, UVM class, Makefile target, list 파일 등).

**설계**: registry `test_discovery.command`에 **테스트 이름을 한 줄씩 출력하는 명령**을 저장. `list_tests`는 이 command를 실행하고 결과만 반환. 환경 무관.

`sim_discover`가 tb_type 기반으로 기본값을 자동 설정. 맞지 않으면 `mcp_config`로 override:
```
mcp_config(action="set", key="test_discovery.command", value="cat my_test_list.txt")
```

**Registry 스키마 — `test_discovery` 추가 (v4.1 확장):**

```json
{
  "test_discovery": {
    "command": "ls tb_tests/*.v | xargs -I{} basename {} .v",
    "cached_tests": [
      "VENEZIA_TOP000_stimulation_test",
      "VENEZIA_TOP001_recording_test",
      "VENEZIA_TOP015_i2c_8bit_offset_test"
    ],
    "cached_at": "2026-04-01T12:00:00"
  }
}
```

| 필드 | 상태 | 설명 |
|------|------|------|
| `test_discovery.command` | **v4.1 신규** | 테스트 이름 한 줄씩 출력하는 명령. `sim_discover`가 tb_type 기반 자동 설정 |
| `test_discovery.cached_tests` | **v4.1 신규** | `list_tests` 실행 결과 캐시. `_resolve_test_name`이 캐시 읽기만 사용 |
| `test_discovery.cached_at` | **v4.1 신규** | 캐시 생성 시각 (ISO 8601). 스테일 캐시 판단용 |

**캐시 쓰기 경로 (일원화 원칙):**
- `sim_discover`: D-14에서 command 실행 + `cached_tests` 초기 캐시 저장
- `list_tests`: 캐시 없으면 command 실행 → `mcp_config` 경유 `cached_tests` 저장
- `_resolve_test_name`: 캐시 읽기만 (쓰기 없음)

**환경별 command 예시:**

| 환경 | `test_discovery.command` |
|------|------------------------|
| ncsim legacy | `ls tb_tests/*.v \| xargs -I{} basename {} .v` |
| UVM | `grep -rh 'extends uvm_test' tb/ --include='*.sv' \| grep -oE 'class \\w+' \| sed 's/class //' \| sort -u` |
| Makefile | `make list_tests 2>/dev/null` |
| test list 파일 | `cat regression.list` |

```python
@mcp.tool()
async def list_tests(
    sim_dir: str = "",
    pattern: str = "",
) -> str:
    """List available test names using test_discovery.command from registry.

    The command is environment-specific (set by sim_discover, overridable via mcp_config).
    It should output one test name per line.

    No bridge connection required (file system / ssh_run based).

    Args:
        sim_dir: Simulation directory. Empty = registry default.
        pattern: Filter pattern (substring match). Empty = all tests.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        try:
            await run_full_discovery(sim_dir)
            resolved_dir = await _get_default_sim_dir()
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"
    if not resolved_dir:
        return "ERROR: No sim_dir. Run sim_discover first."

    config = await load_sim_config(resolved_dir)
    if not config:
        return "ERROR: No config. Run sim_discover first."

    discovery = config.get("test_discovery", {})
    cmd = discovery.get("command", "")
    if not cmd:
        return (
            "ERROR: test_discovery.command not configured.\n"
            "Set via: mcp_config set test_discovery.command '<command>'"
        )

    # Use cache if available (pattern=="" only -- pattern filter must re-run)
    cached = discovery.get("cached_tests", [])
    if not cached:
        # Run command and cache results via mcp_config (쓰기 일원화 원칙)
        r = await ssh_run(f"cd {resolved_dir} && {cmd}", timeout=30)
        tests_all = [t.strip() for t in r.strip().splitlines() if t.strip()]
        if tests_all:
            # mcp_config 경유 캐시 저장 (registry 쓰기 일원화)
            await config_action("set", "config", "test_discovery.cached_tests",
                                json.dumps(tests_all))
            await config_action("set", "config", "test_discovery.cached_at",
                                datetime.now().isoformat())
    else:
        tests_all = cached

    tests = tests_all
    if pattern:
        tests = [t for t in tests if pattern in t]

    if not tests:
        return f"No tests found (pattern='{pattern}')" if pattern else "No tests found."

    return f"Tests ({len(tests)} found):\n" + "\n".join(f"  {t}" for t in sorted(tests))
```

**사용:**
```
# 전체 목록
list_tests()
→ Tests (17 found):
    VENEZIA_TOP000_stimulation_test
    ...
    VENEZIA_TOP016_sync_xfr_en_gating_test

# 패턴 필터
list_tests(pattern="i2c")
→ Tests (3 found):
    VENEZIA_TOP006_i2c_rw_test
    VENEZIA_TOP012_i2c_address_mode_test
    VENEZIA_TOP015_i2c_8bit_offset_test

# command override
mcp_config(action="set", key="test_discovery.command", value="cat regression.list")
list_tests()
→ Tests (5 found): ...
```

**라우팅**: bridge 불필요 (파일 시스템 탐색).

### 4.5 `simvision_start` tool -- SimVision 자동 실행 + auto-connect

```python
@mcp.tool()
async def simvision_start(
    test_name: str = "",
    shm_path: str = "",
    display: str = "",
    sim_dir: str = "",
) -> str:
    """Start SimVision or connect to already running instance.

    Flow:
      1. Check for existing SimVision bridge (ready file) -> auto-connect
      2. Resolve SHM path from test_name (glob dump/*{test_name}*.shm)
      3. Detect VNC display (or use provided)
      4. Start SimVision process in run_dir with SHM argument
      5. Poll for bridge ready -> auto-connect to _simvision_bridge

    simvision_start does NOT call simvision_setup. They are separate tools:
      simvision_start = OS process + bridge connection
      simvision_setup = waveform window + signals + zoom (post-connect)

    Args:
        test_name: Test name for SHM lookup. Empty = latest SHM.
        shm_path:  Explicit SHM path (overrides test_name lookup).
        display:   X11 DISPLAY. Empty = auto-detect user's VNC session.
        sim_dir:   Simulation directory. Empty = registry default.
    """
    global _simvision_bridge

    # 0. Disconnect existing simvision bridge (max 1)
    if _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None

    # 1. Check existing SimVision bridge via ready files
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "simvision":
            port = int(parts[0])
            bridge = TclBridge(host="localhost", port=port)
            try:
                ping = await bridge.connect()
                _simvision_bridge = bridge
                return f"SimVision already running - connected to port {port} (ping={ping})"
            except Exception:
                pass  # stale ready file, continue to start new instance

    # 2. Resolve sim_dir + config
    from xcelium_mcp.sim_runner import (
        _get_default_sim_dir, run_full_discovery, load_sim_config,
        _detect_vnc_display, _login_shell_cmd, _build_redirect,
    )
    try:
        resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_dir:
            await run_full_discovery(sim_dir)
            resolved_dir = await _get_default_sim_dir()
    except UserInputRequired as e:
        return f"USER INPUT REQUIRED:\n{e.prompt}"

    if not resolved_dir:
        return "ERROR: No sim_dir found. Run sim_discover first."

    config = await load_sim_config(resolved_dir)
    runner = config.get("runner", {}) if config else {}

    # 3. Resolve SHM path
    if not shm_path:
        dump_dir = f"{resolved_dir}/dump"
        if test_name:
            r = await ssh_run(f"ls -td {dump_dir}/*{test_name}*.shm 2>/dev/null | head -1")
            if not r.strip():
                # Fallback: latest SHM regardless of name
                r = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        else:
            r = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        shm_path = r.strip() if r.strip() else ""

    # 4. Resolve run_dir
    run_dir_rel = runner.get("run_dir", "run")
    run_dir = f"{resolved_dir}/{run_dir_rel}" if run_dir_rel != "." else resolved_dir
    exists = await ssh_run(f"test -d {run_dir} && echo YES || echo NO")
    if "YES" not in exists:
        return (
            f"ERROR: run_dir not found: {run_dir}.\n"
            f"Set via: mcp_config set runner.run_dir <path>"
        )

    # 5. Detect VNC display
    if not display or display == "auto":
        display = await _detect_vnc_display()
    if not display:
        return (
            "ERROR: No VNC display found for current user.\n"
            "Start VNC first: 'vncserver'\n"
            "Or specify: simvision_start(display=':1')"
        )
    # Verify display accessibility
    display_check = await ssh_run(
        f"xdpyinfo -display {display} 2>/dev/null | head -1"
    )
    if not display_check.strip():
        return (
            f"ERROR: Display {display} not accessible.\n"
            f"Check VNC: 'vncserver -list'\n"
            f"Or start:  'vncserver'"
        )

    # 6. Build SimVision launch command
    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", runner.get("login_shell", "/bin/csh"))
    login_shell = runner.get("login_shell", "/bin/sh")

    shm_arg = f" {shm_path}" if shm_path else ""
    inner_parts = [f"setenv DISPLAY {display}"]
    if runner.get("source_separately") and env_files:
        for ef in env_files:
            inner_parts.append(f"source {ef}")
    # Working directory 원칙: simvision은 cds.lib가 있는 run_dir에서 실행.
    # sim_start는 sim_dir에서 시작 (run_sim이 자체 cd run_dir).
    # 두 tool 모두 최종 cwd = {sim_dir}/{run_dir}.
    inner_parts.append(f"cd {run_dir}")
    inner_parts.append(f"simvision{shm_arg}")
    inner_cmd = "; ".join(inner_parts)

    if runner.get("source_separately") and env_files:
        shell_cmd = f"{env_shell} -c '{inner_cmd}'"
    else:
        shell_cmd = _login_shell_cmd(login_shell, inner_cmd)

    log_file = "/tmp/simvision_start.log"
    cmd = f"(nohup {shell_cmd} {_build_redirect(log_file)} < /dev/null &)"
    await ssh_run(cmd, timeout=15)

    # 7. Poll for SimVision bridge ready + auto-connect (max 60s)
    for i in range(30):
        await asyncio.sleep(2)
        r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
        for line in r.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "simvision":
                port = int(parts[0])
                bridge = TclBridge(host="localhost", port=port)
                try:
                    ping = await bridge.connect()
                    _simvision_bridge = bridge
                    return (
                        f"SimVision started and connected.\n"
                        f"  display: {display}\n"
                        f"  port: {port}\n"
                        f"  run_dir: {run_dir}\n"
                        f"  shm: {shm_path or '(none)'}\n"
                        f"  log: {log_file}"
                    )
                except Exception:
                    continue

    log_tail = await ssh_run(f"tail -10 {log_file} 2>/dev/null")
    return f"ERROR: SimVision bridge not ready after 60s.\nLog:\n{log_tail}"
```

### 4.6 `simvision_live` tool -- xmsim SHM을 SimVision에서 실시간 표시

```python
@mcp.tool()
async def simvision_live(
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
    auto_reload: bool = True,
) -> str:
    """Connect SimVision to running xmsim session for live waveform viewing.

    Requires BOTH xmsim and SimVision bridges to be connected.
    Opens xmsim's active SHM in SimVision, adds signals, enables periodic reload.

    Workflow:
      1. Query xmsim for current SHM path and sim time
      2. Open same SHM in SimVision (database open)
      3. Add signals via waveform_add_signals (window 자동 생성 + dedup 포함)
      4. Zoom to current sim time region
      5. Enable auto-reload (SimVision Tcl 'after' timer, 2s interval)

    Args:
        signals:      Signal paths to add to waveform.
        zoom_start:   Zoom start time. Empty = auto (current sim time - 1ms).
        zoom_end:     Zoom end time. Empty = auto (current sim time).
        auto_reload:  Enable periodic database reload for live updates (default True).
    """
    xmsim = _get_xmsim_bridge()
    sv = _get_simvision_bridge()
    results: list[str] = []

    # 1. Get xmsim's current SHM path and sim time
    shm_info = await xmsim.execute("database -list")
    sim_time_str = await xmsim.execute("where")
    results.append(f"xmsim at {sim_time_str}, SHM: {shm_info}")

    # 2. Open same SHM in SimVision
    shm_path = _parse_shm_path(shm_info)
    if not shm_path:
        return (
            f"ERROR: Could not parse SHM path from xmsim database list:\n{shm_info}\n"
            "Open SHM manually: database_open(shm_path='...')"
        )
    try:
        await sv.execute(f"database open {shm_path}")
        results.append(f"SimVision opened: {shm_path}")
    except TclError as e:
        return f"ERROR: SimVision database open failed: {e}"

    # 3. Add signals via waveform_add_signals (재사용)
    #    window 자동 생성 + 중복 검사 모두 waveform_add_signals 내부 처리.
    #    직접 waveform add Tcl 호출 없음. 별도 window 생성 코드 불필요.
    if signals:
        sig_result = await waveform_add_signals(signals=signals)
        results.append(sig_result)

    # 4. Zoom to current sim time region
    if not zoom_start or not zoom_end:
        cur_ns = _parse_time_ns(sim_time_str)
        # Auto zoom: current time - 1ms ~ current time
        zoom_start = f"{max(0, cur_ns - 1000000)}ns"
        zoom_end = f"{cur_ns}ns"
    await sv.execute(f"waveform xview limits {zoom_start} {zoom_end}")
    results.append(f"Zoomed to {zoom_start} - {zoom_end}")

    # 5. Enable auto-reload
    if auto_reload:
        # Define a Tcl proc that reloads the database every 2 seconds.
        # 'database reload' refreshes SHM data written by xmsim.
        # 'after 2000 _mcp_auto_reload' schedules the next reload.
        await sv.execute(
            "proc _mcp_auto_reload {} { "
            "  catch {database reload}; "
            "  after 2000 _mcp_auto_reload "
            "}; "
            "after 2000 _mcp_auto_reload"
        )
        results.append("Auto-reload enabled (2s interval)")

    return "\n".join(results)


def _parse_shm_path(db_list_output: str) -> str:
    """Parse SHM path from xmsim 'database -list' output.

    Typical output:
      '../dump/ci_top.shm'  or  'dump/ci_top.shm'
    May include multiple lines; return the first .shm path found.
    """
    for line in db_list_output.strip().splitlines():
        line = line.strip()
        # Remove leading/trailing quotes
        line = line.strip("'\"")
        if ".shm" in line:
            # Extract path up to and including .shm
            idx = line.index(".shm") + 4
            return line[:idx]
    return ""


def _parse_time_ns(where_output: str) -> int:
    """Parse simulation time from 'where' output into nanoseconds.

    Handles formats:
      '5 MS + 0' -> 5000000
      '100 NS + 500' -> 100500
      '0 FS + 0' -> 0
      '5000000' (raw ns) -> 5000000
    """
    import re
    # "X MS + Y"
    m = re.search(r'(\d+)\s+MS\s*\+\s*(\d+)', where_output)
    if m:
        return int(m.group(1)) * 1000000 + int(m.group(2))
    # "X NS + Y"
    m = re.search(r'(\d+)\s+NS\s*\+\s*(\d+)', where_output)
    if m:
        return int(m.group(1)) + int(m.group(2))
    # Standalone number
    m = re.search(r'(\d+)', where_output)
    if m:
        return int(m.group(1))
    return 0
```

### 4.7 `simvision_live_stop` tool -- auto-reload 중지

```python
@mcp.tool()
async def simvision_live_stop() -> str:
    """Stop SimVision live waveform auto-reload.

    Cancels the periodic 'after' timer set by simvision_live.
    SimVision remains open and connected.
    """
    sv = _get_simvision_bridge()

    # Cancel all pending 'after' callbacks.
    # 'after info' returns list of pending after IDs.
    # We cancel each one. This is safe — worst case cancels unrelated 'after' timers,
    # but in MCP context we own the only 'after' callbacks.
    try:
        await sv.execute(
            "foreach id [after info] { after cancel $id }"
        )
    except TclError:
        pass  # no pending timers

    return "Auto-reload stopped."
```

---

## 5. Phase 3: 기능 완전성 검증 (5항목)

### 5.1 검증 체크리스트 (7항목 x 각 tool)

각 tool에 대해 아래 항목을 확인:

```
[ ] 정상 동작 (happy path): 설계 명세의 기본 시나리오 실행
[ ] 인자 누락/잘못된 값: 에러 메시지가 설계 명세와 일치
[ ] 미연결 상태 호출: ConnectionError 메시지가 올바른 가이드 포함
[ ] 중복 호출 (idempotency): 2회 연속 호출 시 부작용 없음
[ ] 파라미터 완전성: 설계 명세의 모든 파라미터가 구현에 존재
[ ] 반환값 형식: 설계 명세의 출력 포맷과 구현 일치
[ ] 에러 처리: 설계 명세의 에러 경로가 모두 구현됨
```

### 5.2 중점 검증 패턴 (v3/v4 테스트에서 이슈 발견된 패턴)

| 패턴 | 설명 | 해당 tool 예시 |
|------|------|---------------|
| 중복 방지 | 같은 작업 2회 호출 시 중복 발생 | `waveform_add_signals` (v4.1 수정), `probe_add_signals`, `save_checkpoint` |
| 구문 호환 | xmsim vs SimVision Tcl 구문 차이 | `database_open`, `waveform_*`, `cursor_set`, `shutdown_simulator` |
| 환경 의존 | EDA PATH, shell, port 등 가정 | `extract_csv`, `sim_start`, `_login_shell_cmd`, `simvision_start` |
| 상태 전이 | tool 실행 후 시뮬레이터 상태 확인 | `sim_restart`, `restore_checkpoint`, `shutdown_simulator` |
| 경계값 | timeout 0, 빈 문자열, 경로 공백 등 | `sim_run(duration="")`, `save_checkpoint(name="")`, `simvision_start(display="")` |

### 5.3 Phase 3 구현 항목

| # | 항목 | 검증 대상 | 파일 |
|---|------|----------|------|
| P3-1 | v3 Phase 1~5 기능 단위 검증 | 45개 설계 항목 세부 동작 | server.py, sim_runner.py, mcp_bridge.tcl |
| P3-2 | v4 Phase 1~3 기능 단위 검증 | 24개 설계 항목 세부 동작 | server.py, sim_runner.py, csv_cache.py |
| P3-3 | tool 간 연계 동작 검증 | save->restore, extract->bisect, sim_discover->sim_start->connect 등 | 전체 |
| P3-4 | 에러 경로 검증 | 미연결/잘못된 인자/timeout 등 에러 시나리오 | 전체 |
| P3-5 | 검증 결과 문서화 | 체크리스트 + gap 목록 | docs/03-analysis/ |

### Phase 합계

| Phase | 내용 | 항목 수 |
|-------|------|:------:|
| Phase 1 | Auto port + Multi-bridge + connect + run_dir/VNC 탐지 | 13 |
| Phase 1b | 스키마 변경 영향 — 기존 tool 수정 + test_discovery + _resolve_test_name | 9 |
| Phase 2 | SimVision GUI tool + list_tests + 중복 검사 + live waveform | 8 |
| Phase 3 | v3/v4/v4.1 기능 완전성 검증 | 5 |
| **합계** | | **35** |

---

## 6. v4 코드 변경 사항 목록

기존 v4 코드에서 v4.1을 위해 변경해야 할 부분의 전수 목록.

### 6.1 server.py 변경

| # | 위치 | 변경 내용 |
|---|------|----------|
| C-1 | line 39 | `_bridge: TclBridge | None = None` 삭제 -> `_xmsim_bridge`, `_simvision_bridge` 분리 |
| C-2 | line 42-48 | `_get_bridge()` 삭제 -> `_get_xmsim_bridge()`, `_get_simvision_bridge()`, `_get_bridge(target)` |
| C-3 | `connect_simulator` | 시그니처 변경: `port=9876` -> `port=0`, `target="auto"` 추가 |
| C-4 | `disconnect_simulator` | `target` 파라미터 추가, `global _bridge` -> `global _xmsim_bridge, _simvision_bridge` |
| C-5 | `sim_run` | `_get_bridge()` -> `_get_xmsim_bridge()` |
| C-6 | `sim_stop` | 동일 |
| C-7 | `sim_restart` | 동일 |
| C-8 | `execute_tcl` | `target` 파라미터 추가, `_get_bridge(target)` |
| C-9 | `sim_status` | `target` 파라미터 추가 |
| C-10 | `set_breakpoint` | `_get_xmsim_bridge()` |
| C-11 | `get_signal_value` | `_get_xmsim_bridge()` |
| C-12 | `describe_signal` | `_get_xmsim_bridge()` |
| C-13 | `find_drivers` | `_get_xmsim_bridge()` |
| C-14 | `list_signals` | `target` 파라미터 추가 |
| C-15 | `deposit_value` | `_get_xmsim_bridge()` |
| C-16 | `release_signal` | `_get_xmsim_bridge()` |
| C-17 | `waveform_add_signals` | 전면 재작성 (window auto-create, dedup, `_get_simvision_bridge()`) |
| C-18 | `waveform_zoom` | `_get_simvision_bridge()` |
| C-19 | `cursor_set` | `_get_simvision_bridge()` |
| C-20 | `take_waveform_screenshot` | `_get_simvision_bridge()` |
| C-21 | `run_debugger_mode` | `target` 파라미터 추가 |
| C-22 | `shutdown_simulator` | `target` 파라미터, `_xmsim_bridge`/`_simvision_bridge` 분기 |
| C-23 | `watch_signal` | `_get_xmsim_bridge()` |
| C-24 | `watch_clear` | `_get_xmsim_bridge()` |
| C-25 | `probe_control` | `_get_xmsim_bridge()` |
| C-26 | `save_checkpoint` | `_get_xmsim_bridge()` |
| C-27 | `restore_checkpoint` | `_get_xmsim_bridge()` |
| C-28 | `bisect_signal` | Mode B: `_get_xmsim_bridge()` |
| C-29 | `bisect_restore_and_debug` | `_get_xmsim_bridge()` (2회) |
| C-30 | `probe_add_signals` | `_get_xmsim_bridge()` |
| C-31 | `sim_batch_run` 내부 | `_get_bridge()` -> `_get_xmsim_bridge()` (restore mode) |
| C-32 | `attach_to_simvision` | `connect_simulator(target="simvision")` |
| C-33 | `open_debug_view` | `connect_simulator(target="simvision")`, `_get_simvision_bridge()` |
| C-34 | `compare_waveforms` | simvision mode: `connect_simulator(target="simvision")`, `_get_simvision_bridge()` |
| C-35 | 신규 tool 추가 | `database_open`, `simvision_setup`, `simvision_start`, `simvision_live`, `simvision_live_stop`, `list_tests` |
| C-35b | `sim_batch_run` | `sim_mode`, `extra_args` 파라미터 추가 (P1b-4) |
| C-35c | `sim_batch_regression` | `sim_mode`, `extra_args` 파라미터 추가 (P1b-5) |
| C-35d | `sim_start` | `test_name = await _resolve_test_name(test_name, sim_dir)` 추가 (P1b-9) |
| C-35e | `sim_batch_run` | `test_name = await _resolve_test_name(test_name, sim_dir)` 추가 (P1b-9) |
| C-35f | `sim_batch_regression` | `test_list = [await _resolve_test_name(t, sim_dir) for t in test_list]` 추가 (P1b-9) |
| C-35g | `simvision_start` | `if test_name: test_name = await _resolve_test_name(test_name, sim_dir)` 추가 (P1b-9) |

### 6.2 sim_runner.py 변경

| # | 위치 | 변경 내용 |
|---|------|----------|
| C-36 | `_start_bridge()` | auto-connect 통합 (ready file 스캔 -> TclBridge 연결 -> `_srv._xmsim_bridge` 배정) |
| C-36b | `_start_bridge()` | 기존 inline args_format/mode_defaults 로직 → `_resolve_sim_params()` 호출로 교체 (P1b-2) |
| C-36c | `_run_batch_single()` | `sim_mode`, `extra_args` 파라미터 추가 + `_resolve_sim_params()` 호출 (P1b-3) |
| C-37 | `run_full_discovery()` | D-12 `_detect_run_dir()` 호출 추가, config에 `run_dir` 포함 |
| C-37b | `run_full_discovery()` | D-14 `test_discovery.command` 자동 설정 + `cached_tests` 초기 캐시 (P1b-7) |
| C-38 | 신규 함수 | `_detect_run_dir()`, `_detect_vnc_display()`, `_parse_shm_path()`, `_parse_time_ns()`, `_resolve_sim_params()`, `_resolve_test_name()` |

### 6.3 mcp_bridge.tcl 변경

| # | 위치 | 변경 내용 |
|---|------|----------|
| C-39 | namespace 변수 | `port_range 10`, `bridge_type "xmsim"` 추가 |
| C-40 | `::mcp_bridge::init` | bridge_type 감지, auto port 루프, ready file 형식 변경 (`port type timestamp`) |
| C-41 | `do_shutdown` | ready file 삭제 추가, `_shutdown_flag` 설정 추가 |

---

## 7. 구현 순서 의존관계 DAG

```
Phase 1 (Auto Port + Multi-bridge):
  [P1-1] mcp_bridge.tcl bridge_type 감지 ─────────────┐
  [P1-2] mcp_bridge.tcl auto port 루프 ───────────────│
  [P1-3] ready file "port type timestamp" ─────────────┤  ─→ [P1-6]
  [P1-4] _xmsim_bridge / _simvision_bridge slot ──────┤     connect_simulator
  [P1-5] _get_xmsim_bridge / _get_simvision_bridge ───┤     ready file 기반
                                                       │
  [P1-8] xmsim 18개 tool 라우팅 ─── [P1-5] 필요       │
  [P1-9] SimVision 10개 tool 라우팅 ── [P1-5] 필요    │
                                                       │
  [P1-7] _auto_connect_all, _find_ready_file ──────────┘
         _read_bridge_type
                                                   ┌──→ [P1-11] _detect_run_dir
  [P1-10] sim_start auto-connect ── [P1-6] 필요   │     (독립)
                                                   │
                                                   └──→ [P1-12] _detect_vnc_display
                                                         (독립)
  [P1-13] config 스키마 run_dir ── [P1-11] 필요

Phase 1b (스키마 영향):
  [P1b-1] _resolve_sim_params ── 독립 (신규 함수)
  [P1b-2] _start_bridge → _resolve_sim_params 호출 ── [P1b-1] 필요
  [P1b-3] _run_batch_single → _resolve_sim_params 호출 ── [P1b-1] 필요
  [P1b-4] sim_batch_run extra_args/sim_mode ── [P1b-3] 필요
  [P1b-5] sim_batch_regression extra_args/sim_mode ── [P1b-3] 필요
  [P1b-6] sim_discover args_format dict 생성 ── 독립
  [P1b-7] sim_discover test_discovery.command + cached_tests ── [P1b-6] 이후 (D-14)
  [P1b-8] _resolve_test_name() ── [P1b-7] 필요 (cached_tests 읽기)
  [P1b-9] sim_start/sim_batch_run/regression/simvision_start → _resolve_test_name 호출
          ── [P1b-8] 필요

Phase 2 (SimVision GUI tool):
  [P2-1] database_open ── [P1-4, P1-5] 필요
  [P2-2] simvision_setup ── [P2-1, P2-3] 필요
  [P2-3] waveform_add_signals 개선 ── [P1-9] 필요
  [P2-4] waveform_add_signals 중복 검사 ── [P2-3] 내포
  [P2-5] list_tests ── [P1b-7] 필요 (test_discovery.command 의존)
  [P2-6] simvision_start ── [P1-7, P1-11, P1-12] 필요
  [P2-7] simvision_live ── [P2-1, P2-3, P1-4] 필요
  [P2-8] simvision_live_stop ── [P2-7] 이후

Phase 3 (검증):
  Phase 1 + Phase 1b + Phase 2 완료 후 착수
  [P3-1] v3 기능 검증 ── 독립
  [P3-2] v4 기능 검증 ── 독립
  [P3-3] 연계 동작 검증 ── [P3-1, P3-2] 이후
  [P3-4] 에러 경로 검증 ── [P3-1, P3-2] 이후
  [P3-5] 문서화 ── [P3-3, P3-4] 이후

병렬 가능 그룹:
  그룹 A: P1-1 ~ P1-3 (mcp_bridge.tcl)
  그룹 B: P1-4, P1-5 (server.py bridge slot)
  그룹 C: P1-11, P1-12 (sim_runner.py 탐지 함수)
  그룹 D: P1b-1, P1b-6 (독립 신규 함수)
  A + B + C + D는 병렬 가능
```

---

## 8. 성공 기준 SC-1~SC-21 (Plan 기준)

| # | 기준 | 검증 방법 | Phase |
|---|------|----------|:-----:|
| | **— Auto port + Multi-bridge —** | | |
| SC-1 | port 충돌 없음 | sim_start + SimVision 동시 → 각각 다른 port | P1 |
| SC-2 | `connect_simulator(port=0)` auto-detect | ready file에서 port+type 자동 탐지 | P1 |
| SC-3 | `sim_start` auto-connect | sim_start 후 connect_simulator 없이 sim_run 성공 | P1 |
| SC-4 | `run_dir` 탐지 | sim_discover에서 run_dir 자동 탐지 | P1 |
| SC-5 | VNC display 자동 탐지 | simvision_start(display="") → 사용자 VNC 세션 발견 | P1 |
| | **— SimVision GUI tool —** | | |
| SC-6 | `database_open` SHM open | SimVision에서 SHM 정상 open + 신호 접근 | P2 |
| SC-7 | `simvision_setup` 일괄 설정 | 1회 호출로 SHM + waveform + signals + zoom 완료 | P2 |
| SC-8 | `waveform_add_signals` 중복 skip + window 자동 생성 | 동일 신호 2회 → skip, window 없으면 자동 생성 | P2 |
| SC-9 | `list_tests` test name 탐색 | test_discovery.command 실행 → 목록 반환, pattern 필터 | P2 |
| SC-10 | `simvision_start` 자동 실행 | 미실행 → VNC에서 시작 + auto-connect | P2 |
| SC-11 | `simvision_start` 기실행 감지 | 실행 중 → "already running on port X" + auto-connect | P2 |
| SC-12 | `simvision_live` 실시간 파형 | sim_run 진행 → SimVision에서 2초 내 파형 갱신 | P2 |
| | **— 스키마 영향 + Regression —** | | |
| SC-13 | `_resolve_sim_params` 단일 진입점 | sim_start/sim_batch_run/regression 모두 동일 params 적용 | P1b |
| SC-14 | `args_format` dict + string 호환 | mode별 dict 동작 + v4 string 하위 호환 | P1b |
| SC-15 | `extra_args` 적용 | common + mode + 1회성 합침 정상 | P1b |
| SC-16 | v4 기능 regression | sim_discover → sim_start → sim_run → shutdown 정상 | P1b |
| | **— 기능 완전성 검증 —** | | |
| SC-17 | v3 45개 항목 기능 단위 검증 | 체크리스트 7항목 × 45 = 전수 검사, gap 0건 | P3 |
| SC-18 | v4 24개 항목 기능 단위 검증 | 체크리스트 7항목 × 24 = 전수 검사, gap 0건 | P3 |
| SC-19 | v4.1 항목 기능 단위 검증 | 체크리스트 7항목 × v4.1 전체 = 전수 검사, gap 0건 | P3 |
| SC-20 | tool 간 연계 동작 | save→restore, extract→bisect, sim_start→sim_run 등 체인 | P3 |
| SC-21 | 에러 경로 검증 | 미연결/잘못된 인자/timeout 에러 시나리오 | P3 |

### SC 매핑 테이블 — P항목 x SC-1~SC-21

| P항목 | SC-1 | SC-2 | SC-3 | SC-4 | SC-5 | SC-6 | SC-7 | SC-8 | SC-9 | SC-10 | SC-11 | SC-12 | SC-13 | SC-14 | SC-15 | SC-16 | SC-17 | SC-18 | SC-19 | SC-20 | SC-21 |
|:-----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|
| P1-1  | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-2  | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-3  | o | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-4  |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-5  |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-6  |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-7  |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1-8  |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |
| P1-9  |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   | o |   |   |   |   |   |
| P1-10 |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |
| P1-11 |   |   |   | o |   |   |   |   |   | o | o |   |   |   |   |   |   |   |   |   |   |
| P1-12 |   |   |   |   | o |   |   |   |   | o | o |   |   |   |   |   |   |   |   |   |   |
| P1-13 |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P1b-1 |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |
| P1b-2 |   |   |   |   |   |   |   |   |   |   |   |   | o | o | o | o |   |   |   |   |   |
| P1b-3 |   |   |   |   |   |   |   |   |   |   |   |   | o | o | o | o |   |   |   |   |   |
| P1b-4 |   |   |   |   |   |   |   |   |   |   |   |   | o |   | o | o |   |   |   |   |   |
| P1b-5 |   |   |   |   |   |   |   |   |   |   |   |   | o |   | o | o |   |   |   |   |   |
| P1b-6 |   |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |
| P1b-7 |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |
| P1b-8 |   |   | o |   |   |   |   |   | o |   |   |   |   |   |   | o |   |   |   |   |   |
| P1b-9 |   |   | o |   |   |   |   |   | o |   |   |   |   |   |   | o |   |   |   |   |   |
| P2-1  |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P2-2  |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P2-3  |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P2-4  |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |   |
| P2-5  |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |   |   |   |
| P2-6  |   |   |   |   |   |   |   |   |   | o | o |   |   |   |   |   |   |   |   |   |   |
| P2-7  |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |
| P2-8  |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |   |   |   |   |   |
| P3-1  |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |   |
| P3-2  |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | o |   |   |   |
| P3-3  |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | o |   |
| P3-4  |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | o |
| P3-5  |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   |   | o | o | o | o | o |

**범례:** `o` = 해당 P항목이 해당 SC 달성에 기여

---

## 9. 전체 워크플로우 (v4.1 이후)

### 시나리오: AI 디버깅 + 사용자 파형 확인 동시

```
# 1. 환경 탐지 (1회)
sim_discover(sim_dir)
  -> run_dir, VNC, EDA env 등 모두 탐지

# 2. 시뮬레이션 시작 (auto-connect 포함)
sim_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test", mode="bridge")
  -> xmsim 시작 (auto port: 9876)
  -> ready file "9876 xmsim 1774958600"
  -> TclBridge(9876) 연결
  -> _xmsim_bridge 배정
  -> "Simulation started and connected. port:9876"

# 3. xmsim 디버깅 (tool -> _xmsim_bridge 자동)
sim_run(duration="5ms")
save_checkpoint("debug_5ms")
get_signal_value(["top.hw.r_scl", "top.hw.r_sda"])

# 4. SimVision 시작 (auto-connect 포함)
simvision_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test")
  -> VNC display :1 자동 탐지
  -> SimVision 시작 (auto port: 9877, 9876은 xmsim 사용 중)
  -> ready file "9877 simvision 1774958700"
  -> TclBridge(9877) 연결
  -> _simvision_bridge 배정
  -> "SimVision started and connected. port:9877"

# 5. SimVision 설정 (tool -> _simvision_bridge 자동)
simvision_setup(
    signals=["top.hw.r_scl", "top.hw.r_sda"],
    zoom_start="4ms", zoom_end="6ms"
)
  -> database_open (SHM) + waveform new + signals 추가 + zoom

# 6. xmsim/SimVision 자유롭게 혼용 (switch/connect 불필요)
sim_run(duration="5ms")              # -> _xmsim_bridge
cursor_set(time="8ms")              # -> _simvision_bridge
get_signal_value(["top.hw.r_rst"])  # -> _xmsim_bridge
waveform_zoom(start_time="7ms", end_time="9ms")  # -> _simvision_bridge

# 7. 라이브 파형 연결 (선택)
simvision_live(signals=["top.hw.r_rst"])
  -> xmsim SHM을 SimVision에서 open
  -> 2초 간격 auto-reload 활성화
sim_run(duration="5ms")              # -> SimVision에서 실시간 파형 갱신
simvision_live_stop()                # -> auto-reload 중지
```

### `connect_simulator`의 역할 변경

| 버전 | 역할 |
|------|------|
| v4 | 필수 -- `sim_start` 후 반드시 호출 |
| v4.1 | 수동 재연결용 -- 연결 끊긴 후 복구, 또는 다른 bridge에 명시적 연결 |
