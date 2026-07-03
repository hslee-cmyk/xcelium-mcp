# Phase 4 — 2차 판별: Waveform CSV 분석 (핵심)

## 목적

로그로 판별할 수 없거나, FAIL의 근본 원인을 특정할 때 사용한다.

> **verilog-rtl-debugger agent 위임**: 이 Phase 전체(4A~4E)는 `verilog-rtl-debugger` agent(chip-design-skills)의 핵심 책임 범위다 — bisect/CSV/RTL 참조 자율 루프는 xcelium-mcp MCP tool 접근 권한이 있는 agent만 수행할 수 있다(기존 verilog-rtl-analyst/coder/reviewer/prover/architect-advisor는 MCP tool 접근이 없음).
> **Fallback**: agent를 찾을 수 없으면 Claude가 이 문서의 절차를 직접 수행한다.

## 절차

### 4A. bisect → CSV 추출(1회) → In-memory 분석

**Step 1. bisect_signal로 이상 시점 1차 특정** — 넓은 범위에서 binary search로 자동 탐색(수동 CSV 스캔 불필요):

```python
bisect_signal(signal="top.hw...r_streamRwState", op="eq", value="3",
              start_ns=0, end_ns=END_NS, shm_path="dump/ci_top_${TEST}.shm")
```

**Step 2. bisect가 좁힌 구간에서 CSV 1회 추출** (Phase 1A 판별 신호를 이 구간 기준으로):

```bash
simvisdbutil dump/ci_top_${TEST}.shm/ci_top.trn \
    -csv -output /tmp/${TEST}_check.csv -overwrite -missing \
    -range START:ENDns \
    -sig top.hw...r_regAddr -sig top.hw...r_streamRwState \
    -sig top.hw...r_loopState -sig top.hw...r_startStopDetState
```

**Step 3. In-memory 분석** (같은 CSV에서 awk/grep으로 다양한 관점 필터링, simvisdbutil 재호출 없음):

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
| 신호 시간 변화 추적 | `simvisdbutil`(ssh_run, MCP tool 아님) |
| 시각적 확인/공유 | `simvision_connect` → `waveform` → `waveform_screenshot` |
| 특정 시점 조합 신호 | `inspect_signal(action="value")` |
| 조건부 stop | `watch(action="set")` |

## verilog-rtl-debugger agent 위임

이 Phase는 위임하지 않고 agent가 직접 수행한다(위 안내 참조). 근본 원인 확정 후에만 phase-5-fix-regression.md로 넘어가 수정을 위임한다.

## 다음 단계

근본 원인 확정 → phase-5-fix-regression.md.
