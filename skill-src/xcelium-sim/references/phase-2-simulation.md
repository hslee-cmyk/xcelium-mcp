# Phase 2 — 시뮬레이션 실행

## 목적

Batch(권장) 또는 Bridge(interactive) 모드로 시뮬레이션을 실행해 waveform dump를 확보한다.

## 절차

### 2A. Batch Mode (기본 권장)

시뮬레이터와 상호작용 없이 실행 → dump 확보. 대부분의 디버깅에 충분하며 모든 실행에 xcelium-mcp tool을 사용한다.

```python
sim_batch_run(test_name="TOP015", dump_signals=["r_regAddr", ...])
sim_regression(test_list=["TOP012", "TOP013", ...])
```

### 2B. Bridge Mode (특수 상황)

Interactive probing이 필요할 때만: 버그 조건이 정확히 알려져 `watch`로 1회 포착 가능할 때, 실행 중 신호값을 실시간 확인해야 할 때, probe enable/disable로 SHM 크기를 제어해야 할 때.

```python
sim_bridge_run(test_name="TEST_NAME")   # 컴파일+실행+브릿지 자동 연결
watch(action="set", signal="...", op="==", value="...")
sim_run(duration="20ms")
inspect_signal(action="value", signals=["..."])
sim_disconnect(action="shutdown")   # SHM 보존 안전 종료
```

### 실행 중단 (sentinel 파일, 권장)

`sim_run`은 100µs 단위 chunk 루프로 실행되며 chunk 경계에서 sentinel 파일을 감지하면 즉시 정지한다(xmsim 생존, bridge intact).

```python
# ssh-mcp(별도 채널)로 sentinel 생성 — xcelium-mcp 자체 tool은 sim_run과 직렬화되어 병렬 중단 불가
ssh_bg_run("sleep 3 && touch /tmp/xcelium_mcp_{uid}/stop_{port}")
sim_run(duration="10ms", timeout=30)
sim_status()   # 중단 후에도 xmsim 생존, bridge intact 확인
```

`chunk=0`이면 레거시 1-shot 모드(sentinel 중단 불가).

### ⚠️ SIGINT — 파괴적 중단(최후 수단)

Xcelium은 run 중 SIGINT를 deferred 처리 — run 완료 후 xmsim 프로세스 자체가 종료된다("run만 중단, xmsim 생존"은 재현 안 됨). sentinel 방식 불가 시에만 사용:

```python
ssh_run("kill -s INT {xmsim_pid}")   # 이후 sim_bridge_run으로 재시작 필요
```

### sim_run timeout 후 재연결

`sim_run(timeout=N)`이 초과되면 Python MCP가 `_force_close()`하지만 xmsim은 run을 계속한다. run 완료를 기다린 후 재연결한다:

```python
sim_run(duration="999ms", timeout=30)   # ERROR: exceeded — xmsim은 계속 실행 중
# ... run 완료 대기(ncsim.log 타임스탬프 출력 정지 또는 netstat CLOSE_WAIT 소멸 확인) ...
connect_simulator()   # stale channel 자동 정리 후 재연결 성공
sim_status()
```

**주의**: `connect_simulator`는 run 완료 후에만 호출한다 — run 진행 중엔 Tcl 이벤트 루프가 블로킹되어 accept 콜백이 안 돈다.

## 2A vs 2B 선택 기준

| 상황 | 선택 |
|------|------|
| 첫 시뮬레이션(버그 위치 모름) | Batch |
| Regression(다중 테스트) | Batch |
| 추가 신호 필요, checkpoint 있음 | Batch-restore(`from_checkpoint=`) |
| 추가 신호 필요, checkpoint 없음 | Batch 전체 재실행 |
| 수정 가설 검증/실시간 조작 | Bridge |

## Tool 예시

```python
sim_batch_run(test_name="TOP015", dump_depth="boundary", sim_mode="gate")
```

## 다음 단계

Phase 3(1차 판별)으로 진행.
