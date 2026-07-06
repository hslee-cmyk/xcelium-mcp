# xcelium-mcp-batch-poll-false-completion Plan

> **Feature**: `poll_batch_log`의 완료 판별 키워드가 과도하게 넓어(`$finish`/`PASS`/`FAIL` 단순 substring) 시뮬레이션이 실제로 끝나기 전에 `sim_batch_run`이 "completed"를 반환하는 false-positive 버그 수정
>
> **Date**: 2026-07-03
> **Status**: Draft
> **Found in**: venezia-fpga 세션, TOP015(`VENEZIA_TOP015_i2c_8bit_offset_test`) 시뮬레이션 실행 중 실전 재현

---

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | `sim_batch_run`이 xmsim이 실제로 종료되기 전에 "completed"를 반환한다. `poll_batch_log`(`batch_polling.py`)가 `tail -10` 결과에 `$finish`/`PASS`/`FAIL` 같은 **일반 substring**이 포함되기만 하면 완료로 오판하기 때문 — 이 문자열들은 TCL 스크립트 주석, 개별 assertion 로그 등 **시뮬레이션 진행 중**에도 자연스럽게 등장한다. |
| **Solution** | 완료 판별을 `.done` 마커 파일(PID watcher, 이미 신뢰 가능한 신호로 설계됨)에 우선순위를 두고, 로그 키워드 fast-path는 오탐 없는 패턴(`Simulation complete via $finish`, 앵커링된 `COMPLETE`/`Errors:`)으로 좁힌다. `$finish`/`PASS`/`FAIL` 단독 substring은 완료 신호에서 제거한다. |
| **Function/UX Effect** | `sim_batch_run`이 실제 xmsim 종료 후에만 log summary(PASS/FAIL 전체, Errors count)를 반환. 조기 반환으로 인한 불완전한 결과 없음. |
| **Core Value** | Batch 모드의 근본 계약("완료 후 결과 반환")이 깨지면 모든 상위 워크플로우(regression 판정, AI 자율 디버깅 루프)가 불완전한 데이터로 판단하게 됨 — 가장 자주 쓰이는 tool의 정확성 확보. |

---

## 1. Problem Detail

### 1.1 실전 재현 (2026-07-03, venezia-fpga 세션)

```
sim_batch_run(test_name="TOP015")
→ 반환 (수 초 내):
  "sim_batch_run VENEZIA_TOP015_i2c_8bit_offset_test completed.

   shm_path: .../dump/ci_top_VENEZIA_TOP015_i2c_8bit_offset_test.shm

   xcelium> # setup_rtl_batch.tcl — batch xmsim: dump all signals + run to $finish (no MCP bridge)"
```

- 반환된 "log" 내용이 PASS/FAIL 요약이 아니라 **TCL 스크립트 첫 줄의 주석 echo 한 줄**뿐.
- `sim_status()` 호출 시 "No simulator connected" (batch 모드라 정상).
- SSH로 직접 확인(`ps aux | grep xmsim`) 결과 **xmsim 프로세스가 95.9% CPU로 계속 실행 중** — 실제로는 이후 **2분 30초**가 더 걸려서야 `$finish`에 도달, `Errors: 0`으로 정상 종료.
- 즉 `sim_batch_run`은 시뮬레이션 시작 직후(추정 2~수 초 이내)에 조기 반환했다.

### 1.2 Root Cause

`src/xcelium_mcp/batch_polling.py::poll_batch_log()` (L29-42):

```python
while _time.time() < deadline:
    out = await shell_run(
        f"(tail -10 {log_file} || true); "
        f"test -f {done_file} && echo __DONE__"
    )
    if "__DONE__" in out or any(
        kw in out for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")
    ):
        timed_out = False
        break
    ...
```

이 fast-path 키워드는 **단순 substring 포함 검사**(`kw in out`)이며, 첫 poll(P6-1: interval 시작값 2.0s)부터 즉시 체크된다. 그런데 이 5개 키워드 중 3개(`$finish`, `PASS`, `FAIL`)는 시뮬레이션이 **끝났다는 뜻이 아닌 문맥**에서도 등장한다:

**(A) 확인된 트리거 — TCL 스크립트 주석**

```
$ ssh cloud0 grep -rn "no MCP bridge" .../ncsim/scripts/setup_rtl_batch.tcl
scripts/setup_rtl_batch.tcl:1:# setup_rtl_batch.tcl — batch xmsim: dump all signals + run to $finish (no MCP bridge)
```

이 파일의 **1번째 줄**(주석)이 xmsim이 `-input`으로 이 TCL을 읽을 때 `xcelium> # ...` 형태로 그대로 echo되어 로그 최상단에 찍힌다. 여기 리터럴로 `$finish`가 포함돼 있다 — 시뮬레이션이 시작되기도 전에 "완료 키워드"가 로그에 등장하는 것.

첫 poll 시점에 아직 xmsim이 snapshot 로딩·라이선스 체크 중이라 실제 `run`이 시작되지 않았다면(로그 라인 수가 아직 10줄 이하), `tail -10`이 이 주석 줄을 그대로 포함 → 즉시 오탐.

**(B) 잠재적 트리거 — 개별 assertion PASS/FAIL 로그**

TOP015 테스트벤치는 시뮬레이션 도중 여러 차례 `[V-18] PASS: ...`, `[V-22] PASS: ...` 같은 개별 assertion 결과를 찍는다(11개). 이 중 하나라도 poll 시점에 `tail -10` 윈도우 안에 있으면(로그가 timestamp 라인으로 빠르게 스크롤되지 않는 순간과 poll 타이밍이 겹치면) 최종 완료 전에도 `PASS` substring 매칭으로 오탐할 수 있다. 이번 재현에서 실제로 걸린 것은 (A)였지만 (B)는 코드 상 동일하게 존재하는 별도 위험이다.

**설계 의도와의 괴리**: 함수 docstring(L20)은 "P6-5: .done marker file — 신뢰 가능한 완료 신호"라고 명시한다. `done_file`(PID watcher가 프로세스 종료 후 `touch`)은 실제로 신뢰 가능한 신호가 맞다. 문제는 그 옆의 **키워드 fast-path가 done_file과 동등한 신뢰도로 취급되어 OR 조건으로 묶여 있다는 것** — 키워드 쪽이 훨씬 느슨한데도 먼저 매칭되면 done_file을 기다리지 않고 바로 종료 판정한다.

### 1.3 파급 범위

- `run_batch_single`(→ `sim_batch_run` MCP tool)뿐 아니라 `parse_existing_job`(재접속 시 resume, L202)과 `run_batch_regression`의 per-test poll(L877 `poll_batch_log(test_log, 600)`) 등 **`poll_batch_log`를 호출하는 모든 경로**가 동일 결함을 공유한다.
- 프로젝트마다 setup TCL의 1번째 줄 주석 내용이 다르면 재현 여부가 갈릴 수 있음 — venezia-t0의 `setup_rtl_batch.tcl` L1처럼 `$finish`라는 단어를 우연히 포함하는 주석이 있으면 100% 재현, 없으면 (B) 경로(assertion PASS 스크롤 타이밍)에 의존하는 낮은 확률의 flaky 버그가 됨. **이 결함이 지금까지 눈에 덜 띈 이유**로 추정.

---

## 2. Fix Items

### F-1: `$finish` 키워드를 앵커링된 패턴으로 교체 (핵심 수정)

xmsim이 실제 종료 시 로그에 남기는 문구는 주석과 달리 고유하다(오늘 재현에서 확인):

```
Simulation complete via $finish(1) at time 110803033 NS + 0
```

**Before:**
```python
kw in out for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")
```

**After:**
```python
_COMPLETION_MARKERS = ("Simulation complete via $finish", "COMPLETE", "Errors:")
kw in out for kw in _COMPLETION_MARKERS
```

- `$finish` 단독 → `"Simulation complete via $finish"`(xmsim이 실제 종료 시에만 출력하는 고정 문구, TCL 주석과 겹치지 않음)로 앵커링.
- `PASS`/`FAIL` 단독 substring은 **제거** — 개별 assertion 로그와 구분 불가능하므로 완료 신호로 부적합. 대신 `done_file` 신호와 `COMPLETE`/`Errors:`(프로젝트 TB 컨벤션상 최종 요약 줄에만 등장, §1.1 실전 로그의 `[TOP015] ... COMPLETE. Errors: 0` 참조)에 위임한다.

### F-2: `done_file`을 1차 신호로, 키워드 fast-path는 2차 확인으로 재배치

현재는 `__DONE__ or 키워드매칭` — 어느 쪽이든 즉시 종료. 키워드 fast-path가 오탐 가능성을 완전히 배제할 수 없으므로(F-1 이후에도 프로젝트별 TB 문구 차이 위험 잔존), **fast-path 매칭 시에도 최소 1회 재확인**(짧은 간격 재poll 후 로그가 더 이상 성장하지 않는지 확인)을 추가하는 방안을 검토한다.

```python
if "__DONE__" in out:
    timed_out = False
    break
if any(kw in out for kw in _COMPLETION_MARKERS):
    # 2차 확인: 재poll 간격만큼 대기 후 done_file 또는 로그 정체 확인
    await asyncio.sleep(min(interval, 3.0))
    confirm = await shell_run(f"test -f {done_file} && echo __DONE__; tail -1 {log_file}")
    if "__DONE__" in confirm or confirm.strip() == out_last_line:
        timed_out = False
        break
    # 오탐이었으면 루프 계속 (break하지 않음)
```

F-1만으로도 §1.1 재현 케이스(TCL 주석)는 해결되지만, F-2는 (B) 경로(assertion PASS 스크롤 타이밍)에 대한 방어층을 추가한다. **우선순위**: F-1 필수, F-2는 리스크/구현비용 대비 검토 후 결정(Design 단계에서 확정).

### F-3: 최종 `result` 추출 grep도 동일 원칙 적용

L44-45의 최종 결과 추출도 동일한 느슨한 패턴을 쓴다:

```python
result = await shell_run(
    f"(grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {log_file} || true) | tail -30"
)
```

이건 루프 종료 후 "무엇을 보여줄지"라 완료 오판과는 별개 문제지만, F-1 재현 케이스처럼 조기 종료 시 이 grep이 TCL 주석 한 줄만 건져서 반환하는 것도 §1.1의 증상 일부다. F-1로 조기 종료 자체가 없어지면 이 grep은 정상적으로 전체 PASS/FAIL 라인을 반환하게 되므로 **별도 수정 불필요** — F-1의 부수 효과로 해소됨을 Test Plan에서 확인만 한다.

---

## 3. Scope

| 파일 | 변경 내용 |
|------|----------|
| `src/xcelium_mcp/batch_polling.py` | F-1(필수): 완료 키워드 패턴 교체. F-2(검토 후 결정): 2차 확인 로직 추가 |
| `tests/` (해당 테스트 파일) | 신규 테스트: TCL 주석에 `$finish` 포함된 로그로 `poll_batch_log`를 호출했을 때 조기 종료하지 않음을 검증 |

변경 없음: `batch_runner.py`, `tools/batch.py`, `mcp_bridge.tcl`.

---

## 4. Test Plan

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | `tail -10` 결과가 `"# ... run to $finish (no MCP bridge)"` 주석만 포함 | 완료로 오판하지 않고 poll 계속 |
| T-2 | `tail -10` 결과가 `"[V-18] PASS: ..."` 개별 assertion만 포함 (COMPLETE/Errors: 없음) | 완료로 오판하지 않고 poll 계속 |
| T-3 | `tail -10` 결과가 `"Simulation complete via $finish(1) at time ..."` 포함 | 정상적으로 완료 판정 |
| T-4 | `done_file` 존재 | 키워드 매칭 여부와 무관하게 즉시 완료 판정 (기존 동작 유지) |
| T-5 | 회귀: 기존 `test_batch_helpers.py`의 `poll_batch_log` 테스트 전체 통과 | 기존 정상 케이스 깨지지 않음 |
| T-6 | 실제 TOP015 재실행 — `sim_batch_run`이 실제 xmsim 종료(수 분) 후에만 "completed" + 전체 PASS 목록 반환 | 조기 반환 없음 (수동 검증, venezia-fpga 세션) |

---

## 5. Related Documents

- `xcelium-mcp-sim-run-timeout-fix.plan.md` — **다른 문제**: Bridge 모드 `sim_run`의 TCP 통신 timeout(30s→600s). 이 문서는 Batch 모드 `poll_batch_log`의 완료 *판별 로직* 오탐이 원인이라 서로 무관.
- `xcelium-mcp-ssh-timeout-fix.plan.md` — **인접하지만 다른 문제**: SSH 명령 자체의 timeout/PID watcher 분리(F-1~F-4, 이미 구현됨: `nohup bash -c` 패턴, `done_file`). 이 문서가 만든 `done_file` 메커니즘은 정상 동작하며(§1.2), 이번 버그는 그 옆의 **키워드 fast-path**가 원인 — ssh-timeout-fix가 고친 인프라 위에 남아있던 별개 결함.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-07-03 | 초안 — venezia-fpga 세션에서 TOP015 `sim_batch_run` 조기 반환 실전 재현, root cause 특정(`batch_polling.py` L35-37 완료 키워드 substring 오탐), F-1(필수)/F-2(검토)/F-3(부수 해소) 정리 |
| 0.2 | 2026-07-06 | F-1 구현 완료(prd.json F-174) — `_COMPLETION_MARKERS` 앵커링, `tests/test_poll_batch_log.py` 4 tests 신규. F-2(2차 확인)는 이번엔 미적용(별도 검토 필요), F-3은 판단대로 별도 수정 없음. 상세: `plans/progress.md` 2026-07-06 F-174 섹션 |
