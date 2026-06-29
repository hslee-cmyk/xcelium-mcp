# Plan: xcelium-mcp v4.1 — Enhancements

> **Feature**: Auto port, database_open, SimVision/xmsim 동시 운용
>
> **Date**: 2026-03-31
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: xcelium-mcp v4 (100% complete)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | (1) xmsim bridge와 SimVision GUI가 동일 port 9876을 사용하여 동시 실행 시 충돌. (2) SimVision GUI에서 SHM database를 여는 tool 없음 — xmsim과 SimVision의 `database` 구문이 다름. (3) SimVision GUI 연결 시 waveform window 생성이 수동 |
| **Solution** | (1) mcp_bridge.tcl auto port 탐색 + ready file에 실제 port 기록. (2) `database_open` tool — 환경 자동 감지하여 올바른 구문 사용. (3) `simvision_setup` tool — waveform window + SHM open 일괄 처리 |
| **Function UX Effect** | sim_start로 시뮬레이션 돌리면서 동시에 SimVision으로 파형 확인 가능. database/waveform 수동 설정 불필요 |
| **Core Value** | xmsim bridge + SimVision GUI 동시 운용으로 AI 디버깅 + 사용자 파형 확인 병행 |

---

## 1. 배경: v4 테스트에서 발견된 한계

### 1.1 Port 충돌

```
sim_start → xmsim + mcp_bridge.tcl → port 9876 LISTEN
SimVision → .simvisionrc + mcp_bridge.tcl → port 9876 → "Address already in use"
```

동시 실행 시나리오:
- AI가 `sim_start`로 시뮬레이션 돌리는 중 → 사용자가 SimVision으로 이전 SHM 파형 확인
- 사용자가 SimVision으로 라이브 디버깅 중 → AI가 batch regression 실행

### 1.2 SimVision GUI에서 SHM 미열림

SimVision을 `simvision` (인자 없이) 실행하면 SHM이 안 열림. `database_open` tool 없이는 수동으로 File → Open 또는 Tcl 명령 필요.

xmsim과 SimVision의 구문 차이:
| 환경 | open | close |
|------|------|-------|
| xmsim | `database -open path -shm` | `database -close name` |
| SimVision | `database open path` | `database close name` |

### 1.3 Waveform window 수동 생성 + 신호 중복

SimVision 시작 후 `waveform_add_signals` 호출 시:
- waveform window가 없으면 에러 → **window 자동 생성** 필요
- 같은 신호를 2회 추가하면 중복 표시 → **중복 검사** 필요
- 기존 window가 있으면 **기존 window에 추가**, 없으면 **신규 생성 후 추가**

---

## 2. 목표

1. **Auto port**: mcp_bridge.tcl이 port 충돌 시 자동으로 다음 port 시도
2. **Multi-bridge 구조**: xmsim bridge와 SimVision bridge를 독립 slot으로 관리 — tool 레벨 자동 라우팅
3. **Bridge type 자동 감지**: mcp_bridge.tcl이 자신의 환경(xmsim/SimVision)을 ready file에 기록
4. **`database_open` tool**: bridge type에 따라 올바른 구문 자동 선택
5. **`simvision_setup` tool**: waveform window 생성 + SHM open + signal 추가 일괄 처리
6. **`connect_simulator` 개선**: ready file에서 port + type 읽어서 적절한 slot에 자동 배정

---

## 3. 설계

### 3.1 Auto Port + Bridge Type 감지 — mcp_bridge.tcl 수정

**3.1.1 자기 환경 감지 (xmsim vs SimVision):**
```tcl
# mcp_bridge.tcl — 시작 시 자기 환경 판별
if {[info commands waveform] ne ""} {
    variable bridge_type "simvision"
} else {
    variable bridge_type "xmsim"
}
puts "MCP Bridge: type=$bridge_type"
```

`info commands waveform` — SimVision에만 `waveform` 명령이 존재. xmsim에는 없음.

**3.1.2 Auto port 탐색:**
```tcl
variable port 9876
variable port_range 10  ;# 9876~9885 시도

for {set p $port} {$p < $port + $port_range} {incr p} {
    if {![catch {socket -server ::mcp_bridge::accept $p} sock]} {
        set server_socket $sock
        set port $p
        puts "MCP Bridge: listening on port $p"
        break
    }
    puts "MCP Bridge: port $p busy, trying next..."
}
if {$server_socket eq ""} {
    puts "MCP Bridge: ERROR — all ports $port-[expr {$port + $port_range - 1}] busy"
    return
}
```

**3.1.3 Ready file에 port + type 기록:**
```tcl
# 형식: "port type timestamp"
set ready_file "/tmp/mcp_bridge_ready_$port"
set f [open $ready_file w]
puts $f "$port $bridge_type [clock seconds]"
close $f
```

```
/tmp/mcp_bridge_ready_9876  → "9876 xmsim 1774958600"
/tmp/mcp_bridge_ready_9877  → "9877 simvision 1774958547"
```

connect 전에 ready file만 읽으면 port + type 모두 알 수 있음.

### 3.2 Multi-bridge 구조 + `connect_simulator` 개선

**3.2.1 독립 bridge slot (server.py):**

**제약: 각 type 최대 1개**
- xmsim bridge: 최대 1개 (`_xmsim_bridge`)
- SimVision bridge: 최대 1개 (`_simvision_bridge`)
- 유효 조합: xmsim만 / SimVision만 / xmsim + SimVision 동시 (최대 2개)
- 같은 type 2개 동시 연결 불가 — `sim_start` 재호출 시 기존 xmsim bridge disconnect 후 새 연결

```python
# 기존: 단일 전역 bridge
# _bridge: TclBridge | None = None

# 변경: xmsim/SimVision 독립 slot (각 최대 1개)
_xmsim_bridge: TclBridge | None = None
_simvision_bridge: TclBridge | None = None

def _get_xmsim_bridge() -> TclBridge:
    if _xmsim_bridge is None or not _xmsim_bridge.connected:
        raise ConnectionError("Not connected to xmsim. Call connect_simulator first.")
    return _xmsim_bridge

def _get_simvision_bridge() -> TclBridge:
    if _simvision_bridge is None or not _simvision_bridge.connected:
        raise ConnectionError("Not connected to SimVision. Call connect_simulator first.")
    return _simvision_bridge

def _get_bridge() -> TclBridge:
    """Backward compat: return any connected bridge (xmsim 우선)."""
    if _xmsim_bridge and _xmsim_bridge.connected:
        return _xmsim_bridge
    if _simvision_bridge and _simvision_bridge.connected:
        return _simvision_bridge
    raise ConnectionError("Not connected. Call connect_simulator first.")
```

**3.2.2 Tool 자동 라우팅:**

| 카테고리 | 해당 tool | bridge | 이유 |
|----------|----------|:------:|------|
| **xmsim 전용** | sim_run, sim_stop, sim_restart, get_signal_value, describe_signal, find_drivers, deposit_value, release_signal, watch_signal, watch_clear, save_checkpoint, restore_checkpoint, bisect_signal, bisect_restore_and_debug, probe_control, probe_add_signals, shutdown_simulator, set_breakpoint | `_get_xmsim_bridge()` | 시뮬레이션 제어 명령 (18개) |
| **SimVision 전용** | waveform_add_signals, waveform_zoom, cursor_set, simvision_setup, take_waveform_screenshot, database_open, attach_to_simvision, open_debug_view, simvision_live, simvision_live_stop | `_get_simvision_bridge()` | GUI 명령 (10개) |
| **양쪽/지정** | execute_tcl, list_signals, sim_status, run_debugger_mode | `target` 파라미터 | 문맥에 따라 다름 (4개) |
| **bridge 시작 + auto-connect** | sim_start (→ `_xmsim_bridge`), simvision_start (→ `_simvision_bridge`) | 자동 배정 | 프로세스 시작 후 ready → connect 통합 (2개) |
| **수동 재연결/전환** | connect_simulator, disconnect_simulator | target 지정 | 비정상 상황 복구용 (2개) |
| **bridge 불필요** | sim_discover, mcp_config, list_tests, sim_batch_run, sim_batch_regression, extract_csv, bisect_signal_dump, compare_waveforms, generate_debug_tcl, export_debug_context, request_additional_signals, prepare_dump_scope, cleanup_checkpoints | — | registry/파일 기반 (13개) |

**xmsim tool 예시 (변경):**
```python
# 기존: bridge = _get_bridge()
# 변경:
async def sim_run(duration="", timeout=600.0):
    bridge = _get_xmsim_bridge()  # xmsim 전용
    ...

async def waveform_add_signals(signals, group_name=""):
    bridge = _get_simvision_bridge()  # SimVision 전용
    ...
```

**3.2.3 `connect_simulator` — ready file 기반 auto-detect + slot 배정:**

```python
async def connect_simulator(host="localhost", port=0, target="auto"):
    """Connect to simulator bridge.

    Args:
        port: Bridge port. 0 = auto-detect from ready files.
        target: "xmsim" | "simvision" | "auto".
                auto: ready file에서 type 읽어서 자동 배정.
                port=0 + target=auto: 모든 ready file 스캔, 각각 slot 배정.
    """
    global _xmsim_bridge, _simvision_bridge

    if port == 0 and target == "auto":
        # 모든 ready file 스캔 → 각각 자동 배정
        return await _auto_connect_all(host)

    if port == 0:
        # 특정 type의 ready file 찾기
        port, detected_type = await _find_ready_file(target)
        target = detected_type

    if target == "auto":
        # port 지정 + auto: ready file에서 type 읽기
        target = await _read_bridge_type(port)

    bridge = TclBridge(host=host, port=port)
    ping = await bridge.connect()

    if target == "simvision":
        _simvision_bridge = bridge
    else:
        _xmsim_bridge = bridge

    return f"Connected to {target} at {host}:{port} (ping={ping})"


async def _auto_connect_all(host: str) -> str:
    """Scan all ready files, connect to each, assign to appropriate slot."""
    global _xmsim_bridge, _simvision_bridge
    results = []

    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        port, btype = int(parts[0]), parts[1]

        bridge = TclBridge(host=host, port=port)
        try:
            ping = await bridge.connect()
            if btype == "simvision":
                _simvision_bridge = bridge
            else:
                _xmsim_bridge = bridge
            results.append(f"{btype}:{port} (ping={ping})")
        except Exception as e:
            results.append(f"{btype}:{port} FAILED ({e})")

    if not results:
        return "No bridges found. Run sim_start or open SimVision first."
    return "Connected:\n" + "\n".join(f"  {r}" for r in results)


async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type."""
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == target:
            return int(parts[0]), parts[1]
    raise RuntimeError(f"No {target} bridge found in ready files.")


async def _read_bridge_type(port: int) -> str:
    """Read bridge type from ready file for given port."""
    r = await ssh_run(f"cat /tmp/mcp_bridge_ready_{port} 2>/dev/null")
    parts = r.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return "xmsim"  # fallback
```

**`disconnect_simulator` 확장:**

```python
async def disconnect_simulator(target: str = "all") -> str:
    """Disconnect from bridge(s).

    Args:
        target: "xmsim" | "simvision" | "all" (default: all)
    """
    global _xmsim_bridge, _simvision_bridge
    results = []

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

**사용:**
```
disconnect_simulator()                    # 전부 disconnect
disconnect_simulator(target="xmsim")      # xmsim만 — SimVision 유지
disconnect_simulator(target="simvision")  # SimVision만 — xmsim 유지
```

**사용 시나리오:**
```
# AI가 sim_start → xmsim 시작 (port 9876)
# 사용자가 SimVision 실행 (port 9877 auto)

connect_simulator()                  # port=0, target=auto
# → 결과:
#   Connected:
#     xmsim:9876 (ping=pong)
#     simvision:9877 (ping=pong)

sim_run(duration="5ms")              # → _xmsim_bridge (자동)
waveform_add_signals(signals=[...])  # → _simvision_bridge (자동)
get_signal_value(signals=[...])      # → _xmsim_bridge (자동)
cursor_set(time="5ms")               # → _simvision_bridge (자동)
```

### 3.3 `database_open` tool — bridge type 기반 구문 선택

```python
@mcp.tool()
async def database_open(shm_path: str, name: str = "") -> str:
    """Open SHM database. Uses correct syntax based on bridge type.

    SimVision: 'database open path'
    xmsim:     'database -open path -shm'

    Routes to SimVision bridge (primary use case: GUI waveform viewing).
    Falls back to xmsim bridge if SimVision not connected.
    """
    # SimVision bridge 우선 (database open은 주로 GUI용)
    if _simvision_bridge and _simvision_bridge.connected:
        bridge = _simvision_bridge
        name_opt = f" -name {name}" if name else ""
        result = await bridge.execute(f"database open {shm_path}{name_opt}")
        return f"Database opened (SimVision): {result}"

    # xmsim fallback
    bridge = _get_xmsim_bridge()
    result = await bridge.execute(f"database -open {shm_path} -shm")
    return f"Database opened (xmsim): {result}"
```

bridge type을 알고 있으므로 try/except 불필요 — 올바른 구문을 직접 사용.

### 3.4 `simvision_setup` tool — 일괄 환경 설정

```python
@mcp.tool()
async def simvision_setup(
    shm_path: str = "",
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
) -> str:
    """One-shot SimVision setup: open SHM + create waveform + add signals + zoom.

    Convenience tool for SimVision GUI sessions. Equivalent to:
      database_open → waveform new → waveform_add_signals → waveform_zoom

    Args:
        shm_path:   SHM database path. Empty = skip database open.
        signals:    Signal paths to add to waveform.
        zoom_start: Zoom start time (e.g. "0ns"). Empty = full range.
        zoom_end:   Zoom end time (e.g. "10ms"). Empty = full range.
    """
    bridge = _get_simvision_bridge()  # SimVision 전용
    results = []

    # 1. Open SHM database
    if shm_path:
        db_result = await database_open(shm_path)
        results.append(db_result)

    # 2. Create waveform window (if none exists)
    try:
        current = await bridge.execute("waveform using")
    except TclError:
        current = ""
    if not current.strip():
        wname = await bridge.execute("waveform new")
        results.append(f"Waveform window created: {wname}")

    # 3. Add signals
    if signals:
        sig_str = " ".join(signals)
        result = await bridge.execute(f"waveform add -signals {{{sig_str}}}")
        results.append(f"Added {len(signals)} signals: {result}")

    # 4. Zoom
    if zoom_start and zoom_end:
        await bridge.execute(f"waveform xview limits {zoom_start} {zoom_end}")
        results.append(f"Zoomed to {zoom_start} – {zoom_end}")

    return "\n".join(results) if results else "No actions performed."
```

### 3.5 `waveform_add_signals` 개선 — window 자동 생성 + 중복 검사 (P2-3, P2-4)

**현재 문제 2가지**:
1. waveform window가 없으면 `no waveform window` 에러
2. 같은 신호를 여러 번 추가하면 중복 표시

**수정**:
```python
@mcp.tool()
async def waveform_add_signals(
    signals: list[str],
    group_name: str = "",
    window_name: str = "",
) -> str:
    """Add signals to SimVision waveform.

    Args:
        signals:     Signal paths to add.
        group_name:  Group within window. Empty = no group.
        window_name: Target waveform window. Empty = current (or auto-create).
    """
    bridge = _get_simvision_bridge()
    results = []

    # 1. Window 결정: 지정 → 지정 window 사용, 미지정 → current, 없으면 생성
    if window_name:
        # 지정된 window로 전환
        try:
            await bridge.execute(f"waveform using {window_name}")
        except TclError:
            return f"ERROR: Window '{window_name}' not found. Available: {await _list_windows(bridge)}"
    else:
        try:
            current = await bridge.execute("waveform using")
            if not current.strip():
                raise TclError("empty")
        except TclError:
            wname = await bridge.execute("waveform new")
            results.append(f"Waveform window created: {wname}")

    # 2. 현재 window의 기존 signals 조회 (중복 검사)
    try:
        existing = await bridge.execute("waveform signals -format list")
        existing_set = set(existing.strip().splitlines())
    except TclError:
        existing_set = set()

    new_signals = [s for s in signals if s not in existing_set]
    skipped = len(signals) - len(new_signals)

    if not new_signals:
        return f"All {len(signals)} signal(s) already in waveform (skipped)."

    # 3. Add signals (with optional group)
    sig_str = " ".join(new_signals)
    if group_name:
        try:
            await bridge.execute(f"waveform add -groups {{{group_name}}}")
        except TclError:
            pass  # group already exists
        result = await bridge.execute(f"waveform add -using {group_name} -signals {{{sig_str}}}")
    else:
        result = await bridge.execute(f"waveform add -signals {{{sig_str}}}")

    results.append(f"Added {len(new_signals)}, skipped {skipped} (duplicate). {result}")
    return "\n".join(results)


async def _list_windows(bridge) -> str:
    """List available waveform windows."""
    try:
        r = await bridge.execute("waveform get -name")
        return r.strip() if r.strip() else "(none)"
    except TclError:
        return "(error listing windows)"
```

**동작**:
1. `window_name` 지정 시 → 해당 window로 전환 (없으면 에러 + 사용 가능 목록 표시)
2. 미지정 시 → current window 사용, 없으면 `waveform new` 자동 생성
3. 중복 검사 → 이미 있는 신호 skip
4. group 자동 생성 + 신호 추가

**사용 예:**
```
waveform_add_signals(signals=["r_scl", "r_sda"])
# → current window (또는 자동 생성)에 추가

waveform_add_signals(signals=["r_rst"], window_name="Waveform 2")
# → 지정된 "Waveform 2"에 추가

waveform_add_signals(signals=["r_scl"], window_name="Debug")
# → "Debug" window 없으면: "ERROR: not found. Available: Waveform 1, Waveform 2"
```

### 3.6 `list_tests` tool — `test_discovery.command` 기반 범용 탐색

**배경**: `sim_start(test_name=...)` 호출 시 정확한 test name을 알아야 함. 환경마다 테스트 정의 방식이 다름 (파일명, UVM class, Makefile target, list 파일 등).

**설계**: registry `test_discovery.command`에 **테스트 이름을 한 줄씩 출력하는 명령**을 저장. `list_tests`는 이 command를 실행하고 결과만 반환. 환경 무관.

**Registry 스키마 — `test_discovery` 추가:**

```json
{
  "test_discovery": {
    "command": "ls tb_tests/*.v | xargs -I{} basename {} .v"
  }
}
```

**환경별 command 예시:**

| 환경 | `test_discovery.command` |
|------|------------------------|
| ncsim legacy | `ls tb_tests/*.v \| xargs -I{} basename {} .v` |
| UVM | `grep -rh 'extends uvm_test' tb/ --include='*.sv' \| grep -oE 'class \\w+' \| sed 's/class //' \| sort -u` |
| Makefile | `make list_tests 2>/dev/null` |
| test list 파일 | `cat regression.list` |

**`sim_discover`가 `test_discovery.command`를 자동 설정** (tb_type 기반 기본값). 맞지 않으면 `mcp_config`로 override:
```
mcp_config(action="set", key="test_discovery.command", value="cat my_test_list.txt")
```

**`list_tests` tool — 범용:**

```python
@mcp.tool()
async def list_tests(
    sim_dir: str = "",
    pattern: str = "",
) -> str:
    """List available test names using test_discovery.command from registry.

    The command is environment-specific (set by sim_discover, overridable via mcp_config).
    It should output one test name per line.

    Args:
        sim_dir: Simulation directory. Empty = registry default.
        pattern: Filter pattern. Empty = all tests.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        await run_full_discovery(sim_dir)
        resolved_dir = await _get_default_sim_dir()

    config = await load_sim_config(resolved_dir)
    if not config:
        return "ERROR: No config. Run sim_discover first."

    discovery = config.get("test_discovery", {})
    cmd = discovery.get("command", "")
    if not cmd:
        return "ERROR: test_discovery.command not configured.\nSet via: mcp_config set test_discovery.command '<command>'"

    r = await ssh_run(f"cd {resolved_dir} && {cmd}", timeout=30)
    tests = [t.strip() for t in r.strip().splitlines() if t.strip()]

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
    VENEZIA_TOP001_recording_test
    ...
    VENEZIA_TOP016_sync_xfr_en_gating_test

# 패턴 필터
list_tests(pattern="i2c")
→ Tests (3 found):
    VENEZIA_TOP006_i2c_rw_test
    VENEZIA_TOP012_i2c_address_mode_test
    VENEZIA_TOP015_i2c_8bit_offset_test

# command override (사용자 정의)
mcp_config(action="set", key="test_discovery.command", value="cat regression.list")
list_tests()
→ Tests (5 found):
    ...
```

**라우팅**: bridge 불필요 (파일 시스템 탐색).

**캐시 구조 (`test_discovery` 스키마 확장):**

```json
{
  "test_discovery": {
    "command": "ls tb_tests/*.v | xargs -I{} basename {} .v",
    "cached_tests": [
      "VENEZIA_TOP000_stimulation_test",
      "VENEZIA_TOP001_recording_test",
      "VENEZIA_TOP015_i2c_8bit_offset_test",
      ...
    ],
    "cached_at": "2026-04-01T12:00:00"
  }
}
```

**캐시 쓰기 경로 (일원화 원칙 준수):**
- `sim_discover`: D-14에서 command 실행 + `cached_tests` 초기 캐시 저장 (환경 탐지 시점)
- `list_tests`: 캐시 없으면 command 실행 → **`mcp_config` 경유**로 `cached_tests` 저장 (registry 쓰기 일원화)
- `_resolve_test_name`: **캐시 읽기만** (쓰기 없음)

**`list_tests` 캐시 저장 (mcp_config 경유):**
```python
if not cached:
    r = await ssh_run(f"cd {resolved_dir} && {cmd}", timeout=30)
    tests = [t.strip() for t in r.strip().splitlines() if t.strip()]
    # mcp_config 경유 캐시 저장 (쓰기 일원화 원칙)
    await config_action("set", "config", "test_discovery.cached_tests", json.dumps(tests))
    await config_action("set", "config", "test_discovery.cached_at", datetime.now().isoformat())
```

**`_resolve_test_name` 헬퍼 — short name → full name:**

```python
async def _resolve_test_name(short_name: str, sim_dir: str = "") -> str:
    """Short name → full test name. 캐시에서 검색, 없으면 list_tests 1회 실행.

    "TOP015" → "VENEZIA_TOP015_i2c_8bit_offset_test"
    정확히 1개 매칭 → 반환. 0개 → 에러. 2+ → 후보 표시.
    """
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    config = await load_sim_config(resolved_dir)
    cached = config.get("test_discovery", {}).get("cached_tests", []) if config else []

    if not cached:
        await list_tests(sim_dir=resolved_dir)  # 캐시 생성
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
        raise ValueError(f"No test matching '{short_name}'. Run list_tests() to see available.")
    else:
        raise ValueError(
            f"Multiple tests match '{short_name}':\n"
            + "\n".join(f"  {m}" for m in matches)
            + "\nSpecify more precisely."
        )
```

**모든 test_name 받는 tool에서 자동 호출:**

| tool | 적용 |
|------|------|
| `sim_start` | `test_name = await _resolve_test_name(test_name)` |
| `sim_batch_run` | `test_name = await _resolve_test_name(test_name)` |
| `simvision_start` | `test_name = await _resolve_test_name(test_name)` |
| `sim_batch_regression` | `test_list = [await _resolve_test_name(t) for t in test_list]` |

**사용 예:**
```
sim_start(test_name="TOP015")           → VENEZIA_TOP015_i2c_8bit_offset_test
sim_batch_run(test_name="i2c_8bit")     → VENEZIA_TOP015_i2c_8bit_offset_test
sim_batch_regression(test_list=["TOP006", "TOP012", "TOP015"])
                                        → [VENEZIA_TOP006_..., VENEZIA_TOP012_..., VENEZIA_TOP015_...]
```

**`sim_discover` D-14: `test_discovery.command` 자동 설정:**
```python
# tb_type 기반 기본 command 생성
if tb_type == "ncsim_legacy":
    cmd = f"ls {sim_dir}/tb_tests/*.v 2>/dev/null | xargs -I{{}} basename {{}} .v"
elif tb_type == "uvm":
    cmd = (f"grep -rh 'extends uvm_test' {sim_dir} --include='*.sv' --include='*.svh' 2>/dev/null "
           f"| grep -oE 'class \\w+' | sed 's/class //' | sort -u")
elif tb_type == "sv_directed":
    cmd = (f"grep -rh '^\\s*program ' {sim_dir} --include='*.sv' 2>/dev/null "
           f"| grep -oE 'program \\w+' | sed 's/program //' | sort -u")
else:
    cmd = ""  # 사용자가 mcp_config로 설정
```

### 3.7 `simvision_start` tool — SimVision 자동 실행/연결

**문제**: 현재 사용자가 SSH/VNC로 직접 SimVision을 실행해야 함. AI가 자동으로 처리할 수 없음.

**사전 조건 — registry에 `runner.run_dir` 필요:**

SimVision(과 xmsim)은 특정 작업 디렉토리에서 실행되어야 함 (cds.lib, hdl.var 등 참조). `sim_discover`가 이 디렉토리를 탐지하여 registry에 저장.

```json
// .mcp_sim_config.json v2.1 — run_dir + script_has_cd 추가
{
  "runner": {
    "script": "run_sim",
    "run_dir": "run",              // sim_dir 기준 상대경로
    "script_has_cd": true,         // runner script 내부에 cd 명령 존재 여부
    "args_format": {                         // v4.1: mode별 dict (v4 string도 호환)
      "rtl":      "-test {test_name} --",
      "gate":     "-test {test_name} -gate post --",
      "ams_rtl":  "-test {test_name} -ams --",
      "ams_gate": "-test {test_name} -amsf -gate post --"
    },
    "mode_defaults": {                       // v4.1: common + mode별 설정 (mode가 common override)
      "common":   {"timeout": 120, "probe_strategy": "all", "extra_args": ""},
      "rtl":      {},
      "gate":     {"timeout": 1800, "probe_strategy": "selective"},
      "ams_rtl":  {"timeout": 3600, "probe_strategy": "selective"},
      "ams_gate": {"timeout": 3600, "probe_strategy": "selective"}
    },
    ...
  }
}
```

**v4.1 스키마 변경 요약:**
- `runner.run_dir`: **신규** — 시뮬레이션 실행 디렉토리 (cds.lib 위치)
- `runner.script_has_cd`: **신규** — `sim_discover`가 runner script 파싱하여 자동 설정. `true`면 `sim_start`는 `sim_dir`에서 시작 (script가 자체 cd), `false`면 `run_dir`에서 시작
- `runner.args_format`: v4 string → **v4.1 mode별 dict로 확장**. v4 string 형식도 하위 호환 (전 mode에 동일 적용)
- `runner.mode_defaults`: **신규** — `common` + mode별 설정 (timeout, probe_strategy, extra_args). common을 base로 mode가 override
- `test_discovery.command`: **신규** — 테스트 이름을 한 줄씩 출력하는 명령. `sim_discover`가 tb_type 기반 자동 설정, `mcp_config`로 override 가능

**v4.1 tool signature 확장 — `extra_args` 파라미터 추가:**

| tool | `extra_args` | 용도 |
|------|:----------:|------|
| `sim_start` | 추가 | 1회성 시뮬레이션 옵션 (registry 미변경) |
| `sim_batch_run` | 추가 | batch 실행 시 추가 옵션 |
| `sim_batch_regression` | 추가 | regression 전체에 추가 옵션 |

적용 순서: `mode_defaults.common.extra_args` + `mode_defaults.{mode}.extra_args` + **tool 파라미터 `extra_args`** (1회성, 최우선)
- `runner.shm_stem`: **폐기** — glob 기반 SHM 탐색으로 대체

**`sim_discover`에 run_dir 탐지 추가 (D-12):**

```
탐지 순서:
1. {sim_dir}/run*/ 패턴으로 디렉토리 검색 (run/, run_rtl/, run_gate/ 등)
   → cds.lib 또는 hdl.var 포함하는 것만 candidate
2. runner script 내부에서 'cd' 명령 파싱
   → 예: 'cd run' → run_dir = "run"
   → 예: 'cd $sim_dir/run_rtl' → run_dir = "run_rtl"
3. {sim_dir}/ 자체에 cds.lib 있으면 run_dir = "."
4. candidate가 여러 개면 사용자 선택 (AskUserQuestion)
5. 탐지 실패 → UserInputRequired: "시뮬레이션 실행 디렉토리를 입력하세요"
```

**탐지 구현:**
```python
async def _detect_run_dir(sim_dir: str, runner_info: dict) -> str:
    """Detect simulation run directory.

    Run directory contains cds.lib/hdl.var and is where xmsim/simvision
    should be launched from.
    """
    candidates = []

    # 1. run*/ 패턴 디렉토리 검색 (cds.lib 또는 hdl.var 포함)
    r = await ssh_run(
        f"find {sim_dir} -maxdepth 1 -type d -name 'run*' 2>/dev/null"
    )
    for d in r.strip().splitlines():
        if not d.strip():
            continue
        has_cds = await ssh_run(f"test -f {d}/cds.lib -o -L {d}/cds.lib && echo YES || echo NO")
        if "YES" in has_cds:
            candidates.append(d.split("/")[-1])  # 상대경로

    # 2. runner script에서 'cd' 파싱
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    r = await ssh_run(f"grep -E '^\\s*cd\\s+' {script_path} 2>/dev/null | head -3")
    for line in r.strip().splitlines():
        # 'cd run' → 'run', 'cd $HOME/run_gate' → skip (변수 포함)
        parts = line.strip().split()
        if len(parts) >= 2 and '$' not in parts[1]:
            cd_target = parts[1].strip("'\"")
            if cd_target not in candidates:
                candidates.append(cd_target)

    # 3. sim_dir 자체
    has_cds = await ssh_run(f"test -f {sim_dir}/cds.lib -o -L {sim_dir}/cds.lib && echo YES || echo NO")
    if "YES" in has_cds and "." not in candidates:
        candidates.append(".")

    # 4. 결과
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise UserInputRequired(
            f"Multiple run directories found. Select one:\n"
            + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
        )
    # 5. 탐지 실패
    raise UserInputRequired(
        "Could not detect run directory.\n"
        "Enter the directory where xmsim/simvision should run:\n"
        f"  (relative to {sim_dir})\n"
        "  Example: run\n"
        "  Example: ."
    )
```

**VNC display 자동 탐지:**

```python
async def _detect_vnc_display() -> str:
    """Detect current user's VNC display.

    Search order:
      1. vncserver -list (TigerVNC) → parse display number
      2. ps -u $USER | grep Xvnc → extract :N from args
      3. $DISPLAY env var (if set and not :0)
    Returns: ":N" or "" if not found.
    """
    # 1. vncserver -list
    r = await ssh_run("vncserver -list 2>/dev/null | grep -E '^:'")
    if r.strip():
        # ":2    12345" → ":2"
        display = r.strip().splitlines()[0].split()[0]
        return display

    # 2. Xvnc process
    r = await ssh_run("ps -u $(whoami) -o args 2>/dev/null | grep Xvnc | grep -oE ':[0-9]+'")
    if r.strip():
        return r.strip().splitlines()[0]

    # 3. $DISPLAY fallback (skip :0 = physical display)
    r = await ssh_run("echo $DISPLAY")
    if r.strip() and r.strip() != ":0":
        return r.strip()

    return ""
```

**`simvision_start` tool — connect 포함:**

```python
@mcp.tool()
async def simvision_start(
    test_name: str = "",
    shm_path: str = "",
    display: str = "",
    sim_dir: str = "",
) -> str:
    """Start SimVision or connect to already running instance.

    1. Check if SimVision bridge already running → auto-connect
    2. If not running → detect VNC display → resolve SHM → start SimVision
    3. Wait for bridge ready → auto-connect to _simvision_bridge

    SHM path resolution (test_name 기반):
      test_name given → glob dump/*{test_name}*.shm
      test_name empty → latest SHM in dump/
      shm_path given → override (직접 지정 우선)

    Display resolution:
      display given → 해당 display 사용
      display empty → 현재 사용자의 VNC 세션 자동 탐지
      VNC 없음 → 에러 + vncserver 안내

    Args:
        test_name: Test name for SHM lookup. Empty = latest SHM.
        shm_path:  Explicit SHM path (overrides test_name lookup).
        display:   X11 DISPLAY. Empty = auto-detect user's VNC session.
        sim_dir:   Simulation directory. Empty = registry default.
    """
    global _simvision_bridge

    # 0. 기존 simvision bridge disconnect (최대 1개 제약)
    if _simvision_bridge and _simvision_bridge.connected:
        await _simvision_bridge.disconnect()
        _simvision_bridge = None

    # 1. Check existing SimVision bridge → auto-connect
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "simvision":
            port = int(parts[0])
            bridge = TclBridge(host="localhost", port=port)
            try:
                ping = await bridge.connect()
                _simvision_bridge = bridge
                return f"SimVision already running — connected to port {port} (ping={ping})"
            except Exception:
                pass  # stale ready file, continue to start

    # 2. Resolve sim_dir + config from registry
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        await run_full_discovery(sim_dir)
        resolved_dir = await _get_default_sim_dir()
    config = await load_sim_config(resolved_dir)
    runner = config.get("runner", {}) if config else {}

    # 3. Resolve SHM path from test_name (glob 방식 — shm_stem 불필요)
    if not shm_path:
        dump_dir = f"{resolved_dir}/dump"
        if test_name:
            # test_name 포함 SHM 검색 → fallback 최신 SHM
            r = await ssh_run(f"ls -td {dump_dir}/*{test_name}*.shm 2>/dev/null | head -1")
            if not r.strip():
                r = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        else:
            # 최신 SHM
            r = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
        shm_path = r.strip() if r.strip() else ""

    # 4. Get run_dir from registry
    run_dir_rel = runner.get("run_dir", "run")
    run_dir = f"{resolved_dir}/{run_dir_rel}"
    # Verify run_dir exists
    exists = await ssh_run(f"test -d {run_dir} && echo YES || echo NO")
    if "YES" not in exists:
        return f"ERROR: run_dir not found: {run_dir}. Set via: mcp_config set runner.run_dir <path>"

    # 4. Display 결정: 지정 → 검증 / 미지정("") → 자동 탐지
    if not display or display == "auto":
        display = await _detect_vnc_display()
    if not display:
        return (
            "ERROR: No VNC display found for current user.\n"
            "Start VNC first: 'vncserver'\n"
            "Or specify: simvision_start(display=':1')"
        )
    # display 접근 가능 확인
    display_check = await ssh_run(f"xdpyinfo -display {display} 2>/dev/null | head -1")
    if not display_check.strip():
        return (
            f"ERROR: Display {display} not accessible.\n"
            f"Check VNC: 'vncserver -list'\n"
            f"Or start:  'vncserver'"
        )

    # 5. Build simvision launch command
    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", runner.get("login_shell", "/bin/csh"))
    login_shell = runner.get("login_shell", "/bin/sh")

    shm_arg = f" {shm_path}" if shm_path else ""
    inner_parts = [f"setenv DISPLAY {display}"]
    if runner.get("source_separately") and env_files:
        for ef in env_files:
            inner_parts.append(f"source {ef}")
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

    # 6. Wait for bridge ready + auto-connect
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

**사용:**
```
# 1줄로 SimVision 시작 + 연결 완료
simvision_start(shm_path="dump/ci_top.shm")
# → "SimVision started and connected. display::1, port:9877, run_dir:.../run"

# 이후 SimVision tool 바로 사용 가능 (_simvision_bridge 이미 연결됨)
waveform_add_signals(signals=["top.hw.r_scl"])
cursor_set(time="5ms")

# 이미 실행 중이면:
simvision_start()
# → "SimVision already running — connected to port 9877"
```

**`simvision_start` vs `simvision_setup` 역할 분리:**
- `simvision_start`: **프로세스** 시작 + bridge **연결** (OS 레벨). SHM open은 simvision 인자로 전달.
- `simvision_setup`: **내부 설정** — waveform window + 신호 추가 + zoom (이미 연결된 상태에서).
- `simvision_start`는 `simvision_setup`을 자동 호출하지 않음 — 분리하여 유연성 확보.

### 3.8 `simvision_live` tool — xmsim 세션에 SimVision 실시간 연결

**배경**: xmsim에서 시뮬레이션이 진행 중일 때, SimVision으로 live waveform을 보면서 디버깅하는 것이 일반적. 현재는 사용자가 수동으로 SimVision을 xmsim에 attach해야 함.

**Xcelium 동작 원리:**
1. xmsim이 SHM probe 기록 중 (`dump/ci_top.shm`)
2. SimVision이 같은 SHM을 열면 → 현재 시점까지의 파형 표시
3. `database reload` → 시뮬레이션 진행분 반영
4. 또는 SimVision이 xmsim PID에 직접 attach → 자동 실시간 갱신

**구현 — `simvision_live` tool:**

```python
@mcp.tool()
async def simvision_live(
    signals: list[str] = [],
    zoom_start: str = "",
    zoom_end: str = "",
    auto_reload: bool = True,
) -> str:
    """Connect SimVision to running xmsim session for live waveform viewing.

    Requires both xmsim and SimVision bridges to be connected.
    Opens xmsim's active SHM in SimVision, adds signals, enables live tracking.

    Args:
        signals:      Signal paths to add to waveform.
        zoom_start:   Zoom start time. Empty = auto (current sim time - 1ms).
        zoom_end:     Zoom end time. Empty = auto (current sim time).
        auto_reload:  Enable periodic database reload for live updates.
    """
    xmsim = _get_xmsim_bridge()
    sv = _get_simvision_bridge()
    results = []

    # 1. Get xmsim's current SHM path and sim time
    shm_info = await xmsim.execute("database -list")
    sim_time = await xmsim.execute("time")
    results.append(f"xmsim at {sim_time}, SHM: {shm_info}")

    # 2. Open same SHM in SimVision (live access)
    # Parse SHM path from xmsim database list
    shm_path = _parse_shm_path(shm_info)
    await sv.execute(f"database open {shm_path}")
    results.append(f"SimVision opened: {shm_path}")

    # 3. Add signals — waveform_add_signals 재사용
    #    (window 자동 생성 + 중복 검사 모두 포함, 별도 window 생성 불필요)
    if signals:
        add_result = await waveform_add_signals(signals=signals)
        results.append(add_result)

    # 5. Zoom to current sim time region
    if not zoom_start or not zoom_end:
        # Auto zoom: current time - 1ms ~ current time
        cur_ns = _parse_time_ns(sim_time)
        zoom_start = f"{max(0, cur_ns - 1000000)}ns"
        zoom_end = f"{cur_ns}ns"
    await sv.execute(f"waveform xview limits {zoom_start} {zoom_end}")
    results.append(f"Zoomed to {zoom_start} – {zoom_end}")

    # 6. Enable auto-reload (SimVision periodically refreshes SHM data)
    if auto_reload:
        # SimVision Tcl: set up periodic reload
        await sv.execute(
            "proc _mcp_auto_reload {} { "
            "  catch {database reload}; "
            "  after 2000 _mcp_auto_reload "
            "}; "
            "after 2000 _mcp_auto_reload"
        )
        results.append("Auto-reload enabled (2s interval)")

    return "\n".join(results)
```

**`simvision_live_stop` — auto-reload 중지:**

```python
@mcp.tool()
async def simvision_live_stop() -> str:
    """Stop SimVision live waveform auto-reload."""
    sv = _get_simvision_bridge()
    await sv.execute("catch {after cancel [after info]}")
    return "Auto-reload stopped."
```

**전체 워크플로우:**

```
# 1. AI가 시뮬레이션 + SimVision 시작 (각각 auto-connect)
sim_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test", mode="bridge")
                                    # → _xmsim_bridge 자동 연결
simvision_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test")
                                    # → _simvision_bridge 자동 연결

# 2. SimVision에 live waveform 연결
simvision_live(
    signals=["top.hw.r_scl", "top.hw.r_sda", "top.hw.r_rst"],
)
# → SimVision이 xmsim의 SHM을 열고 2초마다 자동 갱신

# 3. AI가 시뮬레이션 진행 → 사용자가 SimVision에서 실시간 확인
sim_run(duration="5ms")   # → xmsim bridge
# → SimVision에서 파형이 5ms까지 자동 업데이트됨

sim_run(duration="5ms")   # → 10ms까지
# → SimVision에서 10ms까지 보임

# 4. AI가 특정 시점으로 zoom
cursor_set(time="7ms")              # → SimVision bridge
waveform_zoom(start_time="6ms", end_time="8ms")  # → SimVision bridge

# 5. 라이브 중지
simvision_live_stop()
```

**사용자 경험:**
- AI가 `sim_run` 호출할 때마다 SimVision 파형이 **자동으로 갱신**
- 사용자는 VNC/X11에서 SimVision GUI를 보며 실시간 확인
- AI가 발견한 버그 시점에 cursor/zoom을 자동 설정 → 사용자가 바로 해당 부분 확인

### 3.9 `sim_start` 개선 — auto port + auto-connect (`simvision_start`와 동일 패턴)

v4에서는 `sim_start` → bridge ready → 사용자가 `connect_simulator` 별도 호출 필요.
v4.1에서는 `simvision_start`와 동일하게 **시작 → ready → auto-connect** 통합.

```python
# sim_start bridge mode — v4.1:
# 1. xmsim 시작 (기존 v4 로직)
# 2. ready file에서 type=xmsim 확인 + port 추출
# 3. _xmsim_bridge에 자동 연결 (connect_simulator 통합)

# 기존 xmsim bridge가 있으면 disconnect (최대 1개 제약)
global _xmsim_bridge
if _xmsim_bridge and _xmsim_bridge.connected:
    await _xmsim_bridge.disconnect()
    _xmsim_bridge = None

for i in range(timeout // 2):
    await asyncio.sleep(2)
    r = await ssh_run("cat /tmp/mcp_bridge_ready_* 2>/dev/null")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        # ready file 형식: "port type timestamp"
        if len(parts) >= 2 and parts[1] == "xmsim":
            actual_port = int(parts[0])
            bridge = TclBridge(host="localhost", port=actual_port)
            try:
                ping = await bridge.connect()
                _xmsim_bridge = bridge
                return (
                    f"Simulation started and connected.\n"
                    f"  type: xmsim\n"
                    f"  port: {actual_port}\n"
                    f"  ping: {ping}\n"
                    f"  sim_run, get_signal_value 등 바로 사용 가능."
                )
            except Exception:
                continue  # retry next poll
```

**`sim_start` / `simvision_start` 통일 패턴:**
```
sim_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test")
  → xmsim 시작 → ready file "9876 xmsim ..." → _xmsim_bridge 자동 연결
  → "Simulation started and connected. port:9876"
  → 바로 sim_run() 가능

simvision_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test")
  → SimVision 시작 → ready file "9877 simvision ..." → _simvision_bridge 자동 연결
  → "SimVision started and connected. port:9877"
  → 바로 waveform_add_signals() 가능
```

**`connect_simulator`의 역할 변경 (v4.1):**
- 기존: 필수 (sim_start 후 반드시 호출)
- v4.1: **수동 재연결/전환용**으로만 사용 (예: 연결 끊긴 후, 또는 다른 bridge에 연결)
- `sim_start` / `simvision_start`가 자동 연결하므로 정상 워크플로우에서는 불필요

---

## 4. 구현 항목

### Phase 1: Auto Port + Multi-bridge + connect 개선

| # | 항목 | 파일 |
|---|------|------|
| P1-1 | mcp_bridge.tcl 자기 환경 감지 (`info commands waveform`) | mcp_bridge.tcl |
| P1-2 | mcp_bridge.tcl auto port 탐색 (port_range 루프) | mcp_bridge.tcl |
| P1-3 | ready file에 `port type timestamp` 기록 | mcp_bridge.tcl |
| P1-4 | `_xmsim_bridge` / `_simvision_bridge` 독립 slot | server.py |
| P1-5 | `_get_xmsim_bridge()` / `_get_simvision_bridge()` | server.py |
| P1-6 | `connect_simulator` port=0/target=auto → ready file 기반 자동 배정 | server.py |
| P1-7 | `_auto_connect_all()` + `_find_ready_file()` + `_read_bridge_type()` — ready file 스캔/파싱/slot 배정 | server.py |
| P1-8 | xmsim 전용 tool들 → `_get_xmsim_bridge()` 라우팅 | server.py |
| P1-9 | SimVision 전용 tool들 → `_get_simvision_bridge()` 라우팅 | server.py |
| P1-10 | `sim_start` bridge ready polling auto port 대응 | sim_runner.py |
| P1-11 | `_detect_run_dir()` — `run*/` glob + cds.lib 확인 + runner script `cd` 파싱 + 다중 candidate 질문 | sim_runner.py |
| P1-12 | `_detect_vnc_display()` — vncserver -list + ps Xvnc + $DISPLAY fallback | sim_runner.py |
| P1-13 | `.mcp_sim_config.json` v2 스키마에 `runner.run_dir` 필드 추가 | sim_runner.py |

### Phase 1b: 스키마 변경 영향 — 기존 tool 수정

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
| P1b-8 | `_resolve_test_name()` 헬퍼 — short name → full test name | 캐시 검색, 없으면 `list_tests` → `mcp_config` 경유 캐시 저장 | sim_runner.py |
| P1b-9 | test_name 일관성 — 모든 test_name 파라미터에 `_resolve_test_name` 적용 | 아래 상세 참조 | server.py |

**P1b-9 상세 — test_name 일관성:**

```python
# sim_start: 필수 test_name → resolve
async def sim_start(test_name: str, ...):
    test_name = await _resolve_test_name(test_name, sim_dir)

# sim_batch_run: 필수 test_name → resolve
async def sim_batch_run(test_name: str, ...):
    test_name = await _resolve_test_name(test_name, sim_dir)

# sim_batch_regression: test_list 각 항목 → resolve
async def sim_batch_regression(test_list: list[str], ...):
    test_list = [await _resolve_test_name(t, sim_dir) for t in test_list]

# simvision_start: 선택 test_name → 비어있지 않을 때만 resolve
async def simvision_start(test_name: str = "", ...):
    if test_name:
        test_name = await _resolve_test_name(test_name, sim_dir)
```

**일관성 매트릭스:**

| tool | test_name | _resolve | sim_mode | extra_args | _resolve_sim_params |
|------|:---------:|:--------:|:--------:|:----------:|:-------------------:|
| `sim_start` | 필수 | ✅ | ✅ | ✅ | ✅ |
| `sim_batch_run` | 필수 | ✅ | ✅ | ✅ | ✅ |
| `sim_batch_regression` | list 필수 | ✅ (각 항목) | ✅ | ✅ | ✅ |
| `simvision_start` | 선택 | ✅ (있을 때) | ❌ | ❌ | ❌ |

**핵심: `_resolve_sim_params()` — 스키마 해석 단일 진입점 (Single Point of Change)**

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
    # 1. args_format: dict → mode별 선택, string → 전 mode 동일
    args_raw = runner.get("args_format", "-test {test_name} --")
    if isinstance(args_raw, dict):
        test_args_format = args_raw.get(sim_mode, args_raw.get("rtl", "-test {test_name} --"))
    else:
        test_args_format = args_raw

    # 2. mode_defaults: common + mode merge
    mode_defaults = runner.get("mode_defaults", {})
    common_cfg = mode_defaults.get("common", {})
    mode_cfg = mode_defaults.get(sim_mode, {})
    effective = {**common_cfg, **mode_cfg}

    # 3. extra_args: config + 1회성 합침
    cfg_extra = effective.get("extra_args", "")
    all_extra = f"{cfg_extra} {extra_args}".strip()

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
    }
```

**호출 구조 — 3개 tool 모두 동일 패턴:**

```
sim_start → _start_bridge
  ├─ params = _resolve_sim_params(runner, sim_mode, extra_args)
  ├─ test_args = params["test_args_format"].format(test_name=test_name)
  ├─ if params["extra_args"]: test_args += " " + params["extra_args"]
  └─ effective_timeout = params["timeout"]

sim_batch_run → _run_batch_single
  ├─ params = _resolve_sim_params(runner, sim_mode, extra_args)
  ├─ info = _resolve_exec_cmd(runner)          ← 명령 구성 (스키마 무관)
  ├─ cmd = info.cmd.format(test_name=test_name)
  └─ cmd += params["extra_args"]

sim_batch_regression → _run_batch_single
  └─ (위와 동일)
```

**`_resolve_exec_cmd`는 스키마 무관 — 변경 최소:**
- script 경로, env sourcing, shell 선택만 담당
- `args_format`, `mode_defaults`, `extra_args`는 `_resolve_sim_params`가 전담
- `sim_mode` 파라미터 불필요 (스키마 관련 로직 없음)

### Phase 2: SimVision GUI 지원 tool

| # | 항목 | 파일 |
|---|------|------|
| P2-1 | `database_open` tool (xmsim/SimVision 자동 감지) | server.py |
| P2-2 | `simvision_setup` tool (일괄 설정) | server.py |
| P2-3 | `waveform_add_signals` window 자동 생성 — 없으면 `waveform new`, 있으면 기존 사용 | server.py |
| P2-4 | `waveform_add_signals` 중복 신호 검사 + group 자동 생성 | server.py |
| P2-5 | `list_tests` tool — tb_type별 test name 자동 탐색 (ncsim/uvm/sv_directed) | server.py, sim_runner.py |
| P2-6 | `simvision_start` tool — 기실행 감지 + VNC display 자동 탐지 + 자동 시작 + auto-connect | server.py, sim_runner.py |
| P2-7 | `simvision_live` tool — xmsim SHM을 SimVision에서 열고 auto-reload 설정 | server.py |
| P2-8 | `simvision_live_stop` tool — auto-reload 중지 | server.py |

### Phase 3: v3/v4 설계 명세 기능 완전성 검증

v3/v4 테스트에서 `waveform_add_signals` 중복 검사 누락이 발견된 것처럼, 다른 tool에도 설계 명세 대비 빠진 기능이 있을 수 있다. 전체 tool을 기능 단위로 상세 검증한다.

**검증 방법**: 각 tool의 Design 명세(v3 Design §3~§8 + v4 Design §3~§8)와 실제 구현을 **기능 단위**로 비교. 기존 Gap Analysis는 "함수 존재 여부"만 확인했으나, 이 검증은 "함수 내부의 기능적 동작"을 확인.

| # | 항목 | 검증 대상 | 파일 |
|---|------|----------|------|
| P3-1 | v3 Phase 1~5 기능 단위 검증 | 45개 설계 항목의 세부 기능 동작 (에러 처리, edge case, fallback) | server.py, sim_runner.py, mcp_bridge.tcl |
| P3-2 | v4 Phase 1~3 기능 단위 검증 | 24개 설계 항목의 세부 기능 동작 | server.py, sim_runner.py, csv_cache.py |
| P3-3 | tool 간 연계 동작 검증 | tool A 출력이 tool B 입력으로 올바르게 전달되는지 (예: save_checkpoint → restore_checkpoint, extract_csv → bisect_signal_dump) | 전체 |
| P3-4 | 에러 경로 검증 | 각 tool의 에러 처리가 설계대로 동작하는지 (timeout, 잘못된 인자, 미연결 상태 등) | 전체 |
| P3-5 | 검증 결과 문서화 | 기능 단위 검증 체크리스트 + 발견된 gap 목록 → `docs/03-analysis/xcelium-mcp-v4.1-functional-verification.analysis.md` | — |

**검증 체크리스트 (기능 단위):**

각 tool에 대해 아래 항목을 확인:

```
□ 정상 동작 (happy path)
□ 인자 누락/잘못된 값 시 에러 메시지
□ 미연결 상태에서 호출 시 동작
□ 중복 호출 시 동작 (idempotency)
□ 설계 명세의 모든 파라미터가 구현에 존재하는지
□ 설계 명세의 반환값 형식이 구현과 일치하는지
□ 설계 명세의 에러 처리가 구현되었는지
```

**중점 검증 대상 (v3/v4 테스트에서 이슈가 발견된 패턴):**

| 패턴 | 설명 | 해당 tool 예시 |
|------|------|---------------|
| 중복 방지 | 같은 작업을 2회 호출 시 중복 발생 여부 | waveform_add_signals, probe_add_signals, save_checkpoint |
| 구문 호환 | xmsim vs SimVision Tcl 구문 차이 | database_open, waveform_*, cursor_set |
| 환경 의존 | EDA PATH, shell, port 등 환경 가정 | extract_csv, sim_start, _login_shell_cmd |
| 상태 전이 | tool 실행 후 시뮬레이터 상태가 올바른지 | sim_restart, restore_checkpoint, shutdown_simulator |
| 경계값 | timeout, 빈 문자열, 경로에 공백 등 | sim_run(duration=""), save_checkpoint(name="") |

---

## 5. 성공 기준

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
| SC-9 | `list_tests` test name 탐색 | test_discovery.command 실행 → 목록 반환, pattern 필터. `sim_batch_regression(test_list=...)` 인자로 직접 활용 가능 | P2 |
| SC-10 | `simvision_start` 자동 실행 | 미실행 → VNC에서 시작 + auto-connect | P2 |
| SC-11 | `simvision_start` 기실행 감지 | 실행 중 → "already running on port X" + auto-connect | P2 |
| SC-12 | `simvision_live` 실시간 파형 | sim_run 진행 → SimVision에서 2초 내 파형 갱신 | P2 |
| | **— 스키마 영향 + Regression —** | | |
| SC-13 | `_resolve_sim_params` 단일 진입점 | sim_start/sim_batch_run/regression 모두 동일 params | P1b |
| SC-14 | `args_format` dict + string 호환 | mode별 dict 동작 + v4 string 하위 호환 | P1b |
| SC-15 | `extra_args` 적용 | common + mode + 1회성 합침 정상 | P1b |
| SC-16 | v4 기능 regression | sim_discover → sim_start → sim_run → shutdown 정상 | P1b |
| | **— 기능 완전성 검증 —** | | |
| SC-17 | v3 45개 항목 기능 단위 검증 | 체크리스트 7항목 × 45 = 전수 검사, gap 0건 | P3 |
| SC-18 | v4 24개 항목 기능 단위 검증 | 체크리스트 7항목 × 24 = 전수 검사, gap 0건 | P3 |
| SC-19 | v4.1 항목 기능 단위 검증 | 체크리스트 7항목 × v4.1 전체 = 전수 검사, gap 0건 | P3 |
| SC-20 | tool 간 연계 동작 | save→restore, extract→bisect, sim_start→sim_run 등 체인 | P3 |
| SC-21 | 에러 경로 검증 | 미연결/잘못된 인자/timeout 에러 시나리오 | P3 |

---

## 6. 전체 워크플로우 (v4.1 이후)

### 시나리오: AI 디버깅 + 사용자 파형 확인 동시

```
# 1. 환경 탐지 (1회)
sim_discover(sim_dir)

# 2. 시뮬레이션 시작 (auto-connect 포함 — connect_simulator 불필요)
sim_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test", mode="bridge")
                                               → xmsim 시작 + _xmsim_bridge 자동 연결

# 3. xmsim 디버깅 — sim_run 등은 자동으로 _xmsim_bridge 사용
sim_run(duration="5ms")
save_checkpoint("debug_5ms")
get_signal_value(["top.hw.r_scl", ...])

# 4. SimVision 시작 (auto-connect 포함)
simvision_start(test_name="VENEZIA_TOP015_i2c_8bit_offset_test")
                                               → VNC display 자동 탐지
                                               → SimVision 시작 + _simvision_bridge 자동 연결

# 5. SimVision 설정 — 자동으로 _simvision_bridge 사용
simvision_setup(
    signals=["top.hw.r_scl", "top.hw.r_sda"],
    zoom_start="4ms", zoom_end="6ms"
)                                              → SHM open + waveform + zoom 일괄

# 6. xmsim/SimVision 자유롭게 혼용 — switch/connect 불필요
sim_run(duration="5ms")                        → _xmsim_bridge (자동)
cursor_set(time="8ms")                         → _simvision_bridge (자동)
get_signal_value(["top.hw.r_rst"])             → _xmsim_bridge (자동)
waveform_zoom(start_time="7ms", end_time="9ms") → _simvision_bridge (자동)
```

---

## 7. 추정 일정

| Phase | 내용 | 항목 수 |
|-------|------|:------:|
| Phase 1 | Auto port + Multi-bridge + connect + run_dir/VNC 탐지 | 13 |
| Phase 1b | 스키마 변경 영향 + test_discovery + _resolve_test_name | 9 |
| Phase 2 | SimVision GUI tool + list_tests + 중복 검사 + live waveform | 8 |
| Phase 3 | v3/v4/v4.1 기능 완전성 검증 | 5 |
| **합계** | | **35** |
