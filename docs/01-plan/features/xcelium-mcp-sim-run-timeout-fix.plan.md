# xcelium-mcp-sim-run-timeout-fix Plan

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | `sim_run` MCP tool의 기본 timeout 30초가 RTL 시뮬레이션에 부족. 8ms sim에 25초+ 소요되어 빈번한 timeout 에러 발생. watchpoint stop 시에도 동일 문제. |
| **Solution** | `sim_run`에 timeout 파라미터 추가 + 기본값 600초 (gate sim 대응) + `where` 호출 실패 방어 |
| **Function UX Effect** | `sim_run(duration="20ms")` 호출이 raw TCP 우회 없이 정상 동작 |
| **Core Value** | 가장 자주 사용하는 tool이 안정적으로 동작하여 MCP 워크플로우 신뢰성 확보 |

---

## 1. Problem Detail

### 1.1 실전 검증에서 발생한 에러

```
sim_run(duration="8ms")   → Error (30s timeout, 실제 25s+ 소요)
sim_run(duration="15ms")  → Error (이전 세션에서도 동일)
sim_run(duration="20ms")  → Error (watchpoint stop 시)
```

매번 `disconnect` → raw TCP(timeout=120s)로 우회해야 했음.

### 1.2 Root Cause

`server.py`의 `sim_run`:
```python
async def sim_run(duration: str = "") -> str:
    bridge = _get_bridge()
    cmd = f"run {duration}" if duration else "run"
    await bridge.execute(cmd)           # ← 기본 timeout 30초
    where = await bridge.execute("where")  # ← 위가 실패하면 여기도 실패
    return f"Simulation advanced. Current position: {where}"
```

- `bridge.execute()`는 `self.timeout=30.0` 사용
- `bisect_signal`은 `timeout=600`, `restore_checkpoint`는 `timeout=120` 으로 override 했지만
- **`sim_run`만 override 안 됨**

---

## 2. Fix Items

| # | 수정 | 파일 | 내용 |
|---|------|------|------|
| F1 | `sim_run`에 `timeout` 파라미터 추가 | `server.py` | `timeout: float = 120.0` — ✅ 구현 완료 (기본값 600.0 채택) |
| F2 | `where` 호출 실패 시 graceful fallback | `server.py` | try/except로 timeout 값 반환 — ✅ 구현 완료 (Tcl `catch` 방식으로 처리, `mcp_bridge.tcl:931,938,944`) |
| F3 | ~~`sim_stop`도 동일 패턴 적용~~ | ~~`server.py`~~ | ⚠️ **불필요 — 클로즈**. 현재 `sim_stop`은 `where` 호출 안 함 (`tools/sim_lifecycle.py:318-322`), `stop` 명령만 실행하므로 fallback 방어 불요. |

### F1: sim_run timeout 파라미터

```python
@mcp.tool()
async def sim_run(duration: str = "", timeout: float = 600.0) -> str:
    bridge = _get_bridge()
    cmd = f"run {duration}" if duration else "run"
    await bridge.execute(cmd, timeout=timeout)
    ...
```

**기본값 600초 (10분) 근거**:
- RTL sim: 100ms sim → ~170초. 600초면 충분.
- Gate sim (SDF): RTL 대비 10-100배 느림. 10ms gate sim → ~200초~2000초.
  - 600초면 gate sim 10ms 대응 가능. 더 긴 sim은 timeout 파라미터로 override.
- timeout은 "시뮬이 이 시간 안에 끝나야 한다"가 아니라 **MCP 통신 안전장치**.
  - 넉넉하게 설정하는 것이 올바름. hang 감지는 duration 제한으로 수행.

### F2: where 호출 fallback

```python
    try:
        where = await bridge.execute("where")
    except (TclError, asyncio.TimeoutError, ConnectionError):
        where = "(position unknown — simulation may have finished)"
    return f"Simulation advanced. Current position: {where}"
```

### F3: sim_stop 동일 패턴 — 클로즈 (불필요화)

계획 작성 시점에는 `sim_stop`이 `where`를 호출했으나, 이후 리팩터링으로 `stop` 명령만 실행하도록 단순화됨 (`tools/sim_lifecycle.py:318-322`). `where` 의존성이 제거되어 fallback 방어 대상 없음.

### 추가 구현 (계획 초과)

- **`__RUN_AND_REPORT__` 단일 round-trip 매크로** (`tcl/mcp_bridge.tcl:926`) — run + where를 Tcl에서 1회 통신으로 묶어 MCP 왕복 지연 제거.
- **duration 형식 검증** (`tools/sim_lifecycle.py:297,307`) — `_DURATION_RE` regex로 Tcl injection 방지 (prd.json F-013 보안 항목 대응).
- **RUN_ERROR 명시 반환** (`mcp_bridge.tcl:932,939`) — run 실패 시 "RUN_ERROR:$err" prefix로 구분 가능.

---

## 3. Scope

| 파일 | 변경 라인 |
|------|----------|
| `server.py` | `sim_run`, `sim_stop` 함수 (~10줄) |

mcp_bridge.tcl, tcl_bridge.py 변경 없음.

---

## 4. Test Plan

1. `sim_run(duration="20ms")` → 정상 완료 (timeout 내)
2. `sim_run(duration="20ms")` + watchpoint → stop 후 정상 반환
3. `sim_run(duration="100ms", timeout=300)` → 긴 시뮬 대응
