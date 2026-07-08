# Phase 4 — 2차 판별: Waveform CSV 분석 (핵심)

## 목적

로그로 판별할 수 없거나, FAIL의 근본 원인을 특정할 때 사용한다.

> **기본 실행 주체는 Claude 자신이다**: 이 Phase(4A~4E)의 bisect/CSV/RTL 참조 자율 루프는 xcelium-mcp MCP tool(`bisect_signal`, `inspect_signal`, `waveform` 등)에 대한 직접 접근만 있으면 수행 가능하고, **Claude는 이 세션에서 이미 그 tool들에 직접 접근한다** — 이 문서 아래 절차를 그대로 따라 직접 수행하는 것이 기본 경로다(agent 설치 여부와 무관하게 항상 동작).
> **선택적 위임(있으면)**: `verilog-rtl-debugger` agent(chip-design-skills가 install.py로 배포)가 로컬에 설치돼 있으면, 이 Phase를 그 agent에 Task로 위임할 수 있다 — 별도 context에서 격리 수행하고 싶을 때의 최적화 옵션일 뿐, 필수 경로가 아니다. 기존 verilog-rtl-analyst/coder/reviewer/prover/architect-advisor는 MCP tool 접근이 없어 이 Phase를 대신할 수 없다는 점 때문에 `verilog-rtl-debugger`가 신설된 것이지, "agent 없이는 이 Phase 자체가 불가능"하다는 뜻이 아니다.

## 절차

### 4A. bisect → CSV 추출(1회, 캐시 재사용) → In-memory 분석

**Step 1. bisect_signal로 이상 시점 1차 특정** — 넓은 범위에서 binary search로 자동 탐색(수동 CSV 스캔 불필요). **Phase 1A에서 정한 판별 신호 전체를 `context_signals`에 미리 포함**시켜서, 뒤에서 다시 추출할 필요가 없게 한다:

```python
bisect_signal(signal="top.hw...r_streamRwState", op="eq", value="3",
              start_ns=0, end_ns=END_NS, shm_path="dump/ci_top_${TEST}.shm",
              context_signals=["top.hw...r_regAddr", "top.hw...r_loopState",
                                "top.hw...r_startStopDetState"])
```

응답 마지막에 `CSV: {path}`가 포함된다 — 이건 `[signal]+context_signals`를 이미 추출·캐싱(`csv_cache.extract`)해둔 CSV 경로다.

> **비트폭 접미사 주의(2026-07-06, T-002 실전 검증)**: 벡터/버스 신호(예: `w_fwd_fifo_count`)는 `simvisdbutil` CSV 헤더에 `signal[7:0]`처럼 비트범위가 붙은 이름으로 기록된다. `signal`/`context_signals`에 비트범위 없는 base name만 넘기면 그 신호는 매 행 매치 실패로 조용히 지나가다가 마지막에 `"Signal 'X' not in CSV. Available: [...]"` 에러로 실패한다(`csv_cache.py`의 의도된 동작 — MCP 버그 아님). 실패 시 에러의 `Available` 목록에서 정확한 컬럼명(비트범위 포함)을 그대로 복사해 재호출하면 된다.

**Step 2. Step 1의 CSV를 그대로 재사용** — `context_signals`에 필요한 신호를 전부 넣었다면 별도 추출이 필요 없다. `simvisdbutil`을 다시 부르면 **cache를 우회**하게 되므로, 아래처럼 Step 1이 반환한 경로를 직접 쓴다:

```bash
awk -F',' 'NR>1 && $2+0 != prev {print; prev=$2+0}' {Step1이 반환한 CSV 경로}
```

**Step 1에서 빠뜨린 신호가 뒤늦게 필요할 때만** 추가 추출한다(4B 참조) — 이 경우에도 매번 새 파일로 뽑지 말고, 가능하면 같은 `-output` 경로에 `-overwrite`로 덮어써서 파일 수를 늘리지 않는다.

> **왜 이게 중요한가**: `bisect_signal`/`compare_waveforms`/`inspect_signal(action="extract_csv")`를 거치지 않고 `simvisdbutil`을 직접 shell로 호출하면 `csv_cache.py`의 in-memory/disk 캐시를 완전히 우회한다 — 같은 신호·같은 SHM을 반복 분석할 때마다 재추출이 발생한다. **좁은 범위라 bisect가 필요 없다고 판단되는 경우엔 `inspect_signal(action="extract_csv", signals=[...], shm_path=..., start_ns=..., end_ns=...)`로 CSV만 뽑는다**(2026-07-03, F-174) — bisect 조건을 억지로 걸 필요 없이 곧바로 cache 경유 경로를 탄다.

**Step 3. In-memory 분석** (같은 CSV에서 awk/grep으로 다양한 관점 필터링, simvisdbutil 재호출 없음):

> **원격 경로 주의(2026-07-06, T-002 실전 검증)**: `csv_cache.extract`가 반환하는 CSV 경로는 시뮬레이션이 실제로 도는 호스트(예: cloud0)의 파일시스템 경로다(`/tmp/xcelium_mcp_{uid}/...`). venezia-fpga 등 로컬 세션에서 실행 중이라면 이 경로는 로컬 `Read`/`awk`로 직접 열 수 **없다** — `tool-map.md` §Phase별 Tool 선택 요약의 안내대로 ssh-mcp의 `ssh_run`으로 원격에서 awk를 실행하거나, 그럴 툴이 없으면 `bisect_signal`(Mode A)을 반복 호출해 이진탐색으로 좁혀가는 방식으로 대체한다(둘 다 실전에서 검증됨). 아래 예시는 CSV에 로컬로 접근 가능한 환경(예: MCP 서버가 로컬에서 도는 경우) 기준이다:

```bash
awk -F',' 'NR>1 && $2+0 != prev {print; prev=$2+0}' /tmp/check.csv   # 값 변화 시점만
awk -F',' 'NR>1 && ($4+0==2 || $4+0==3)' /tmp/check.csv               # 특정 FSM 상태만
awk -F',' 'NR>1 && $1+0 >= 8300000000 && $1+0 <= 8500000000' /tmp/check.csv   # 시간 구간
```

### 4B. 추가 신호 보충 추출 (필요 시만)

1차 CSV로 원인 특정이 안 될 때, **같은 시간 범위**에서 추가 신호만 재추출(시간 범위 재추출 아님):

```bash
simvisdbutil dump/... -csv -output /tmp/detail.csv -overwrite \
    -range START:ENDns -sig r_rxData -sig r_dataState -sig r_restart
```

이후 1차/2차 CSV를 시간 기준으로 조인 분석.

### 4C. 근본 원인 특정 — FSM 전이 대조

CSV 데이터를 RTL 분석서(`.ai/analysis/{module}.analysis.md`)의 FSM 전이 테이블과 대조한다.

```
CSV 관찰: t=8318143ns: loopState=2(CHK_ADR), streamRwState=1(STREAM_REG), startStopDetState=0(NULL_DET)
분석서 참조: CHK_ADR | BIT_ACK + START_DET + STREAM_REG → c_regAddr=rxData[7:0]
대조: startStopDetState=NULL_DET(START_DET 아님!) → STREAM_REG case 미진입 → regAddr 미설정 ← 근본 원인
```

### 4D. Interactive Probing (보완)

CSV만으론 부족할 때(신호가 dump에 없거나 실시간 조건 변경 필요):

```python
connect_simulator()
sim_run(duration="8.3ms")   # 이상 시점 직전까지
inspect_signal(action="value", signals=["top.hw...c_regAddr"])   # c_(조합) 신호는 dump에 없을 수 있음
```

### 4E. AI 자율 디버깅 + Human-in-the-Loop (병렬)

AI는 bisect/CSV/RTL 참조 루프를 자율 반복, 사람은 언제든 "현재 상태 보여줘"로 SimVision에서 확인(SHM 읽기 전용 오픈이라 충돌 없음):

```
bisect_signal → simvisdbutil CSV 추출 → in-memory 분석 → RTL 분석서 대조 → 가설 수립 → bisect 재검증 → (반복 또는 확정)
```

Human-in-the-Loop 요청 시:

```python
simvision_connect(action="start", test_name="TOP012")
waveform(action="add", signals=[...], group_name="분석 그룹명")
waveform(action="zoom", start_time="14200000ns", end_time="14600000ns")
waveform_screenshot()
```

**핵심 원칙**: (1) bisect 먼저 — 자동 탐색, 수동 스캔 불필요 (2) in-memory 분석 — 좁혀진 구간에서 awk/grep 다각도 분석 (3) RTL 참조 — FSM 전이표와 CSV 대조 (4) SimVision은 AI-사람 협업 채널, AI 분석과 독립.

## Phase 4 도구 선택 가이드

| 상황 | 도구 |
|------|------|
| 값 변화 시점 자동 탐색 | `bisect_signal`(Mode A) |
| bisect 없이 CSV만 필요(좁은 범위 등) | `inspect_signal(action="extract_csv")` — cache 경유, `simvisdbutil` 직접 호출 금지 |
| 신호 시간 변화 추적(추출된 CSV의 in-memory 분석) | awk/grep (simvisdbutil 재호출 없음) — CSV가 원격 호스트에 있으면 로컬 접근 불가, `ssh_run` 경유 또는 `bisect_signal` 반복으로 대체 |
| 시각적 확인/공유 | `simvision_connect` → `waveform` → `waveform_screenshot` |
| 특정 시점 조합 신호 | `inspect_signal(action="value")` |
| 조건부 stop | `watch(action="set")` |

## verilog-rtl-debugger agent 위임

이 Phase는 위임하지 않고 agent가 직접 수행한다(위 안내 참조). 근본 원인 확정 후에만 phase-5-fix-regression.md로 넘어가 수정을 위임한다.

## 다음 단계

근본 원인 확정 → phase-5-fix-regression.md.
