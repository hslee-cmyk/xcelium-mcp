# Plan: xcelium-mcp F-106 — Chunked Run + sim_stop

> **Feature**: Bridge mode `sim_run`을 chunk 단위로 분할 실행하여 진행 중인 시뮬레이션을 비파괴적으로 중단 가능하게 하는 기능
>
> **Date**: 2026-04-14
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: F-104 (timeout _force_close), F-105 v5 (stale channel cleanup), F-100 (SIGINT 재연결)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Bridge mode에서 `sim_run`이 진행 중일 때 외부에서 비파괴적으로 중단할 수단이 없음. SIGINT는 xmsim 프로세스 자체를 종료시켜 시뮬레이션 상태(waveform, checkpoint)가 파괴됨. 또한 `run $duration` 블로킹 중 TCL 이벤트 루프가 정지하여 accept/read 콜백이 처리되지 않아 "running 중 재연결"도 불가 |
| **Solution** | `do_run_and_report`를 100µs chunk 단위 while 루프로 재작성. 각 chunk 후 `update`로 이벤트 루프 1회 실행. 파일 센티넬(`/tmp/xcelium_mcp_stop_{port}`)을 chunk 경계에서 감지해 루프 조기 종료. 신규 `sim_stop` MCP tool이 센티넬 파일 생성 |
| **Function/UX Effect** | `sim_run` 실행 중 `sim_stop` 호출 → 최대 100µs 시뮬 시간 내 비파괴적 중단. 중단 후 bridge intact → `sim_status`, `inspect_signal` 정상 동작. 부수 효과로 running 중 재연결도 자동 지원 (update 덕분에 accept 콜백 처리 가능) |
| **Core Value** | xmsim 재시작 없이 long-running 시뮬을 중단·재개할 수 있는 interactive 디버깅 워크플로우 완성 |

---

## 1. 배경

### 1.1 문제

F-104/F-105로 timeout 후 브리지 재연결은 해결됐지만, "진행 중인 `sim_run`을 멈추는 방법"은 여전히 없다.

| 현재 중단 수단 | 결과 | 문제 |
|---------------|------|------|
| SIGINT (kill -INT) | xmsim graceful shutdown 후 종료 | 시뮬레이션 상태 파괴, 브리지 소멸 |
| SIGKILL | xmsim 즉시 종료 | 동일 + SHM 손상 위험 |
| TCL `stop` 명령 | breakpoint 전용 | `run` 블로킹 중 TCL 이벤트 루프 정지 → 명령 수신 불가 |

근본 원인: Xcelium `run $duration`은 TCL 이벤트 루프를 **블로킹**한다. `fileevent`/`accept` 콜백은 이벤트 루프가 실행 중일 때만 처리된다.

### 1.2 연관 이슈

- **F-104**: sim_run timeout → asyncio cancel만, TCP 미닫음 → 이후 명령 desync (완료)
- **F-105 v5**: timeout 후 재연결 시 stale channel 자동 close (완료)
- **F-100 Case 2**: SIGINT 후 서버 소켓 LISTEN 유지 → 재연결 가능 여부 (SIGINT = xmsim exit으로 확인, 재연결 불가)
- **F-106 (본 건)**: 비파괴적 중단 + running 중 재연결

### 1.3 Scope

**포함**: Bridge mode (`sim_bridge_run` + `sim_run`) 전용
**제외**: Batch mode (`sim_batch_run`) — fire-and-forget 구조이므로 해당 없음

---

## 2. 설계

### 2.1 TCL 청킹 — `do_run_and_report` 수정

**파일**: `tcl/mcp_bridge.tcl` L931–950

기존:
```tcl
proc do_run_and_report {duration} {
    run ${duration}ns
    return [format_run_report ...]
}
```

변경:
```tcl
proc do_run_and_report {duration {chunk 100000}} {
    # chunk=0 → 기존 1-shot 경로 유지 (하위 호환)
    if {$chunk <= 0} {
        return [legacy_run_and_report $duration]
    }
    set sentinel "/tmp/xcelium_mcp_stop_[get_port]"
    set remaining $duration
    set status "completed"
    set reason ""
    set err_msg ""
    while {$remaining > 0} {
        if {[file exists $sentinel]} {
            file delete -force $sentinel
            set status "stopped"; set reason "user_stop"; break
        }
        set step [expr {$remaining < $chunk ? $remaining : $chunk}]
        if {[catch {run ${step}ns} err]} {
            set status "error"; set err_msg $err; break
        }
        incr remaining -$step
        update   ;# chunk 끝에서 이벤트 루프 1회 — fileevent/accept 처리 (a안)
    }
    return [format_chunked_run_report $status $reason $err_msg $duration]
}
```

핵심 결정:
- **chunk 기본값**: 100,000 ns (100 µs) — 중단 응답 지연 상한, 오버헤드 수 % 예상
- **update 위치 (a안)**: chunk 끝에서 1회. chunk 내부 삽입은 구조적으로 불가 (run은 단일 블로킹 명령)
- **default-on**: Python에서 chunk 미지정 시 100,000 ns 기본. `chunk=0`으로 opt-out

`format_chunked_run_report` 이름이 chunked 경로 전용임을 명시. 기존 `format_run_report`는 보존.

### 2.2 Stop 메커니즘 — 파일 센티넬

- **경로**: `/tmp/xcelium_mcp_stop_{port}` (per-port 격리, 다중 bridge 환경 대비)
- **동작**: 파일 존재 여부를 chunk 경계에서 체크. 파일시스템은 TCL 이벤트 루프와 독립 → `run` 블로킹 중에도 외부 `touch`가 즉시 반영됨
- **정리**: TCL 측에서 감지 후 `file delete -force`로 즉시 제거 → 다음 run 오동작 방지
- **사전 정리**: `sim_bridge_run` 시작 시 잔여 센티넬 제거 권장

### 2.3 신규 MCP tool: `sim_stop`

**파일**: `src/xcelium_mcp/tools/sim_lifecycle.py` (기존 파일에 추가)

```python
async def sim_stop(port: int | None = None) -> dict:
    """Request graceful stop of ongoing sim_run. Bridge mode only.
    Creates /tmp/xcelium_mcp_stop_{port}; TCL chunking loop detects at
    next chunk boundary and returns status='stopped'.
    """
    p = port or BridgeManager.current_port()
    sentinel = f"/tmp/xcelium_mcp_stop_{p}"
    await ssh_run(f"touch {sentinel}")
    return {
        "ok": True,
        "sentinel": sentinel,
        "note": "Stop requested; effective at next chunk boundary (<= 100us sim time)."
    }
```

- 반환 즉시 (fire-and-forget). 실제 중단 확인은 `sim_run` 응답의 `status` 필드로
- Batch mode에서 호출 시 에러 반환 (tool description에 명시)

### 2.4 `sim_run` API 확장

**파일**: `src/xcelium_mcp/tools/sim_lifecycle.py` L345–373

```python
async def sim_run(duration: str, timeout: int = 30, chunk: int | None = None) -> dict:
    # chunk: None → 기본 100000ns, 0 → 1-shot (하위 호환), >0 → chunked
    chunk_ns = 100000 if chunk is None else chunk
    payload = f"__RUN_AND_REPORT__ {duration_ns} {chunk_ns}"
    ...
```

### 2.5 응답 스키마 — `format_chunked_run_report`

TCL → Python 응답:

```json
{
  "sim_time": "12345ns",
  "requested": "999ms",
  "status": "completed",
  "reason": "",
  "error": null
}
```

| 필드 | 값 | 설명 |
|------|----|------|
| `sim_time` | `"12345ns"` | 실제 실행된 총 시뮬레이션 시간 |
| `requested` | `"999ms"` | 원래 요청 duration |
| `status` | `completed` / `stopped` / `error` | 종료 이유 분류 |
| `reason` | `""` / `"user_stop"` | stopped/error 시 상세 |
| `error` | `null` / `"TCL error msg"` | error 시 xmsim 에러 |

- `status` 필드 부재 = 기존 1-shot 경로 → 하위 호환
- chunk=0이면 기존 `format_run_report` 호출 (신규 필드 없음)

---

## 3. 수정 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `tcl/mcp_bridge.tcl` | `do_run_and_report` 청킹 로직, `format_chunked_run_report` 신규, `get_port` 헬퍼 (없으면 추가), `legacy_run_and_report` 분리 |
| `src/xcelium_mcp/tools/sim_lifecycle.py` | `sim_run`에 `chunk` 파라미터 추가, `sim_stop` tool 신규 |
| `src/xcelium_mcp/server.py` (또는 tool registry) | `sim_stop` MCP tool 등록 |
| `plans/prd.json` | F-106 신규 등록 (execute_tcl 검증 후 notes 작성) |

---

## 4. 하위 호환 / 회귀 위험

| 항목 | 위험 | 대책 |
|------|------|------|
| 기존 `sim_run` 호출 | default-on으로 chunk=100000 적용 | `chunk=0` opt-out으로 기존 동작 강제 가능 |
| 응답 파싱 코드 | `format_run_report` 경로와 `format_chunked_run_report` 경로 분리 | `status` 필드 부재 시 기존 파서 그대로 |
| Batch mode | 영향 없음 (별도 경로) | — |
| 파일 센티넬 잔여 | 이전 stop 요청이 다음 run에 영향 | TCL에서 감지 즉시 delete + sim_bridge_run 시작 시 사전 정리 |
| 오버헤드 | 100µs chunk, 999ms 시뮬 → 9,990회 update | 실측 필요. 10% 초과 시 기본값 1ms로 상향 재검토 |

---

## 5. 검증 계획

### 5.1 사전 검증 (prd.json 등록 전 필수)

`execute_tcl`로 cloud0 xmsim에서 TCL 패턴 동작 확인:

```tcl
# chunked run + update 기본 패턴
run 100000ns
update
# → 에러 없이 실행되는지 확인

# 파일 센티넬 감지
set sentinel "/tmp/xcelium_mcp_stop_9876"
file exists $sentinel   ;# → 0 (파일 없으면)
```

### 5.2 Integration (cloud0)

| # | 시나리오 | 기대 결과 |
|---|----------|-----------|
| 1 | `sim_bridge_run TOP000` → `sim_run("10ms")` (default chunk) | 정상 완료, `status="completed"`, `sim_time ≈ 10ms` |
| 2 | sim_run 실행 중 `sim_stop` 호출 | `status="stopped"`, `sim_time < 10ms`, bridge intact |
| 3 | stop 후 `sim_status`, `inspect_signal` | 정상 동작 (bridge 유지 확인) |
| 4 | sim_run 실행 중 다른 세션에서 `connect_simulator` | 즉시 성공 (update 덕분에 accept 콜백 처리) |
| 5 | `sim_run("1ms", chunk=0)` | 기존 동작, `status` 필드 없음 |
| 6 | 오버헤드 측정: chunk=0 vs chunk=100000, 100ms 시뮬 | 실시간 비교, 10% 이내 확인 |

### 5.3 Regression

```bash
pytest tests/ -x -q       # 전체 unit test
ruff check src/            # lint
```

---

## 6. 다음 단계

1. [ ] `execute_tcl`로 cloud0에서 TCL 패턴 사전 검증
2. [ ] `plans/prd.json`에 F-106 등록 (검증 결과 notes 포함)
3. [ ] ralph-loop으로 구현
4. [ ] Integration 검증 (시나리오 1~6)
5. [ ] `docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md` — sim_stop 워크플로우 섹션 추가

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-14 | Initial draft — F-106 chunked run + sim_stop 설계 | hoseung.lee |
