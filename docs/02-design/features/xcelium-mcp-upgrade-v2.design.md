# xcelium-mcp-upgrade-v2 Design

## 1. Architecture Overview

```
[Claude Code / AI Agent]
    │
    ├─ mcp__xcelium-mcp__watch_signal      ──→ TclBridge.execute("__WATCH__ ...")
    ├─ mcp__xcelium-mcp__bisect_signal     ──→ TclBridge.execute("__BISECT__ ...", timeout=600)
    ├─ mcp__xcelium-mcp__shutdown_simulator ──→ TclBridge.execute("__SHUTDOWN__")
    ├─ mcp__xcelium-mcp__probe_control     ──→ TclBridge.execute("__PROBE_CONTROL__ ...")
    ├─ mcp__xcelium-mcp__save_checkpoint   ──→ TclBridge.execute("__SAVE__ ...")
    └─ mcp__xcelium-mcp__restore_checkpoint──→ TclBridge.execute("__RESTORE__ ...")
                                                    │
                                              [TCP 9876]
                                                    │
                                            [mcp_bridge.tcl]
                                                    │
                                              [xmsim / SimVision]
```

**변경 없는 레이어**: mcp_bridge.tcl의 메타 명령 인터페이스는 v1과 동일.
**변경되는 레이어**: Python server.py + tcl_bridge.py에서 tool 추가 + timeout 지원.

---

## 2. Phase A: Python MCP Tool 상세 설계

### 2.1 tcl_bridge.py 변경 — timeout override

```python
# 현재
async def execute(self, command: str) -> str:
    resp = await self.execute_safe(command)
    return resp.raise_on_error()

# 변경 — timeout 파라미터 추가
async def execute(self, command: str, timeout: float | None = None) -> str:
    resp = await self.execute_safe(command, timeout=timeout)
    return resp.raise_on_error()

async def execute_safe(self, command: str, timeout: float | None = None) -> TclResponse:
    if not self.connected:
        raise ConnectionError("Not connected to SimVision bridge")
    effective_timeout = timeout if timeout is not None else self.timeout
    async with self._lock:
        return await asyncio.wait_for(
            self._send_and_recv(command),
            timeout=effective_timeout,
        )
```

### 2.2 server.py — 7개 신규 tool

모든 tool은 `server.py`의 Phase 9 섹션 이후에 추가. 기존 18개 tool과 별도 섹션.

```python
# ===================================================================
# Phase 10 — Advanced Debug Tools (tools 19–25)
# ===================================================================
```

#### T1: shutdown_simulator

```python
@mcp.tool()
async def shutdown_simulator() -> str:
    """Safely shutdown the simulator, preserving SHM waveform data.

    Closes all SHM databases and terminates xmsim.
    Always use this instead of disconnect_simulator when ending a debug session.
    WARNING: The connection will be lost after this call.
    """
    bridge = _get_bridge()
    try:
        result = await bridge.execute_safe("__SHUTDOWN__")
        return f"Simulator shutdown: {result.body}"
    except ConnectionError:
        return "Simulator shutdown completed (connection closed)."
    finally:
        global _bridge
        _bridge = None
```

**설계 포인트**: `__SHUTDOWN__` 후 bridge가 소켓을 닫으므로 `ConnectionError` 발생 가능. `execute_safe` + try/finally로 처리.

#### T2: watch_signal

```python
@mcp.tool()
async def watch_signal(signal: str, op: str = "==", value: str = "") -> str:
    """Set a watchpoint to stop simulation when a signal matches a condition.

    The simulation will automatically stop at the exact clock edge where
    the condition becomes true. Much more efficient than manual probing.

    Args:
        signal: Full hierarchical signal path (e.g. "top.dut.r_state[3:0]").
        op: Comparison operator ("==", "!=", ">", "<", ">=", "<=").
        value: Target value in Verilog format (e.g. "8'h10", "4'b1010", "1'b1").
    """
    bridge = _get_bridge()
    result = await bridge.execute(f"__WATCH__ {signal} {op} {value}")
    return f"Watchpoint set: {result}"
```

#### T3: watch_clear

```python
@mcp.tool()
async def watch_clear(watch_id: str = "all") -> str:
    """Clear watchpoints. Use "all" to clear all, or a specific ID.

    Args:
        watch_id: Watchpoint ID to clear, or "all" for all watchpoints.
    """
    bridge = _get_bridge()
    result = await bridge.execute(f"__WATCH_CLEAR__ {watch_id}")
    return result
```

#### T4: probe_control

```python
@mcp.tool()
async def probe_control(mode: str) -> str:
    """Control SHM waveform recording to manage file size.

    Disable probes during uninteresting simulation periods to save disk space.
    Re-enable before the region of interest.

    Args:
        mode: "enable" to start recording, "disable" to pause, "status" to check.
    """
    bridge = _get_bridge()
    result = await bridge.execute(f"__PROBE_CONTROL__ {mode}")
    return result
```

#### T5: bisect_signal (핵심 — timeout override 필요)

```python
@mcp.tool()
async def bisect_signal(
    signal: str,
    op: str,
    value: str,
    start_ns: int,
    end_ns: int,
    precision_ns: int = 1000,
) -> str:
    """Automatically find when a signal condition first becomes true using binary search.

    Internally saves a checkpoint, then repeatedly restores and runs with
    watchpoints to narrow down the time range. Returns the narrowed range
    and iteration log.

    Args:
        signal: Full hierarchical signal path.
        op: Comparison operator (e.g. "==").
        value: Target value (e.g. "8'h11").
        start_ns: Start of search range in nanoseconds.
        end_ns: End of search range in nanoseconds.
        precision_ns: Stop when range is narrower than this (default 1000ns = 1us).
    """
    bridge = _get_bridge()
    cmd = f"__BISECT__ {signal} {op} {value} {start_ns} {end_ns} {precision_ns}"
    # BISECT can take minutes — use 10 minute timeout
    result = await bridge.execute(cmd, timeout=600.0)
    return result
```

**설계 포인트**: `timeout=600.0`으로 10분까지 허용. `tcl_bridge.py`의 `execute(timeout=)` 오버라이드 활용.

#### T6: save_checkpoint

```python
@mcp.tool()
async def save_checkpoint(name: str = "") -> str:
    """Save a simulation checkpoint for later restoration.

    Checkpoints capture the complete simulator state at the current time.
    Use restore_checkpoint to return to this point without re-simulating.

    Args:
        name: Checkpoint name (alphanumeric, e.g. "chk_10ms"). Auto-generated if empty.
    """
    bridge = _get_bridge()
    cmd = f"__SAVE__ {name}" if name else "__SAVE__"
    result = await bridge.execute(cmd)
    return result
```

#### T7: restore_checkpoint

```python
@mcp.tool()
async def restore_checkpoint(name: str = "") -> str:
    """Restore simulation to a previously saved checkpoint.

    Args:
        name: Checkpoint name to restore. If empty, restores the last saved checkpoint.
    """
    bridge = _get_bridge()
    cmd = f"__RESTORE__ {name}" if name else "__RESTORE__"
    result = await bridge.execute(cmd, timeout=120.0)
    return result
```

---

## 3. Phase B: __BISECT__ 최적화 설계

### 3.1 현재 알고리즘 (v1)

```
매 반복:
  restore(time_0_checkpoint)     ← 항상 time 0으로 복귀
  run(start_ns)                  ← start_ns까지 재실행 (낭비)
  set_watchpoint()
  run(mid_ns - start_ns)
  check → narrow range
```

### 3.2 최적화 알고리즘 (v2)

```
초기: save checkpoint at time 0 ("bisect_t0")
      save checkpoint at start_ns ("bisect_start")   ← 신규

매 반복:
  restore("bisect_start")        ← start_ns에서 바로 시작 (run 생략)
  set_watchpoint()
  run(mid_ns - start_ns)
  check → narrow range

  start_ns 업데이트 시:
    restore("bisect_t0")         ← time 0으로 돌아가서
    run(new_start_ns)            ← 새 start까지 실행
    save("bisect_start")         ← start checkpoint 갱신
```

### 3.3 성능 분석

TOP015 예시 (0-15ms, regAddr==0x11):
- **v1**: 7회 반복 × (restore + run to start) = 7 × ~8ms avg sim = ~56ms total sim
- **v2**: 7회 반복 중 start 갱신 5회 × run(new_start) + 7회 × run(mid-start)
  - start 갱신: 5 × ~8ms = ~40ms
  - 탐색 run: 7 × ~1ms avg = ~7ms
  - total: ~47ms → **~16% 개선** (start가 작을 때 효과 적음)

긴 시뮬 예시 (0-100ms, 50ms 부근 버그):
- **v1**: 10회 × 50ms avg = ~500ms total sim
- **v2**: 5회 start갱신 × 50ms + 10회 × 5ms = ~300ms → **~40% 개선**

**결론**: 시뮬 길이가 길수록 효과 큼. 현재 TOP015(15ms)에서는 효과 제한적이므로 **P1 우선순위 유지**.

---

## 4. Phase C: 부가 개선 설계

### 4.1 N1: run_sim_mcp ping 경쟁 수정

**현재**: TCP connect → send __PING__ → check pong → disconnect 반복
**문제**: bridge 단일 클라이언트 슬롯 점유

**개선**: bridge init 시 ready 파일 생성

```tcl
# mcp_bridge.tcl init 마지막에 추가
proc ::mcp_bridge::init {} {
    ...
    set server_socket [socket -server ::mcp_bridge::accept $port]
    puts "MCP Bridge: listening on port $port"

    # Signal readiness via file (avoids TCP client slot contention)
    set ready_file "/tmp/mcp_bridge_ready_$port"
    set f [open $ready_file w]
    puts $f [clock seconds]
    close $f
}
```

```bash
# run_sim_mcp에서 파일 기반 ready 확인
for i in $(seq 1 30); do
    sleep 2
    if [ -f /tmp/mcp_bridge_ready_9876 ]; then
        READY=1; break
    fi
done
```

### 4.2 N3: tcl_bridge.py timeout 파라미터

Phase A의 2.1에서 이미 설계됨. `execute(timeout=)` 파라미터 추가.

---

## 5. 파일별 변경 요약

| 파일 | 변경 | 추가 라인 |
|------|------|----------|
| `tcl_bridge.py` | `execute()`, `execute_safe()`에 `timeout` 파라미터 추가 | ~10 |
| `server.py` | Phase 10 섹션 + 7개 tool 함수 | ~120 |
| `mcp_bridge.tcl` | __BISECT__ v2 최적화 + ready 파일 생성 | ~30 |
| `run_sim_mcp` | ping 루프를 파일 기반으로 변경 | ~5 |

---

## 6. 테스트 시나리오

### Phase A 테스트

```
1. connect_simulator → watch_signal(regAddr, ==, 8'h11) → sim_run(20ms)
   → 정확한 시점에서 stop 확인
2. bisect_signal(regAddr, ==, 8'h11, 0, 15000000, 100000)
   → 126초 이내 완료, 범위 100us 이내
3. probe_control(disable) → sim_run(5ms) → probe_control(enable)
   → SHM 크기 변화 확인
4. save_checkpoint(chk1) → sim_run(5ms) → restore_checkpoint(chk1)
   → 시간 복원 확인
5. shutdown_simulator → xmsim 종료 + SHM 보존
```

### Phase B 테스트

```
bisect_signal 동일 조건 → wall time 비교 (v1 126초 vs v2 목표 <90초)
```
