# Phase 5 — 수정 + 검증

## 목적

근본 원인이 확정된 후 RTL을 수정하고, lint→재시뮬레이션→regression→문서 갱신까지 완료한다.

## 절차

### 5A. RTL 수정 — Claude 직접 수행이 기본 절차

아래 5단계는 agent 설치 여부와 무관하게 **Claude가 직접 수행할 수 있는 절차**다. 각 단계에 "있으면 위임 가능"한 agent를 괄호로 표기했으나, agent는 선택적 위임처일 뿐 필수 전제가 아니다 — agent가 없으면 Claude가 아래 근거 문서를 직접 Read해서 같은 기준으로 수행한다.

1. Phase 4C에서 확정한 근본 원인 확인 (있으면 위임: `verilog-rtl-debugger` → `verilog-rtl-coder`)
2. 분석서 참조: `.ai/analysis/{module}.analysis.md` (FSM 전이표/신호 의존성 대조)
3. 수정 코드 작성 — `~/.claude/skills/verilog-rtl/SKILL.md` §1(필수 규칙)/§3(사이클 주석)/§4(네이밍)을 직접 적용. Bit-width safety는 §1 "Bit-Width Truncation 방지"의 `$clog2`/명시적 폭 규칙으로 검증 (있으면 위임: `verilog-rtl-coder`)
4. 수정 규모가 신규 FSM/모듈/case-arm 등 아키텍처 경계를 건드리는지 판단 — 해당하면 자체 판단으로 진행하기 전에 사용자에게 설계 의도를 확인 (있으면 위임: `verilog-rtl-architect-advisor`)
5. 커밋 전 리뷰: `~/.claude/agent-kit/failure-taxonomy.md`(T1~T9 AI-failure 시그니처 카탈로그)와 `~/.claude/skills/verilog-rtl/SKILL.md` §10(코드 리뷰 체크리스트)를 **직접 Read**하여 수정 diff를 그 기준으로 자체 점검(합성/타이밍/코딩스타일/기능 4개 관점 + T1~T9 대조) (있으면 위임: `verilog-rtl-reviewer`)

> 위 5단계는 모두 파일 Read만으로 수행 가능하다 — agent가 설치돼 있지 않은 환경에서도 이 문서와 `verilog-rtl` skill(agent가 아닌 skill 자산이라 chip-design-skills 설치 시 함께 배포됨), `failure-taxonomy.md`만 있으면 동일한 깊이로 수행할 수 있다.

### 5B. Verilator Lint

```bash
C:/msys64/usr/bin/bash.exe -lc "verilator --lint-only -Wall --top-module <top> <files>"
```

기존 warning만 확인, 새 에러 없음을 검증. self-contained 로직/타이밍 클레임(예: FSM count==0 데드락, 포인터 wrap-around)은 formal 증명이 유용할 수 있다(있으면 위임: `verilog-rtl-prover`) — agent가 없으면 `formal-verification` skill(sby/SymbiYosys, BMC)을 직접 활성화해 동일 절차로 property를 작성·검증한다. 필수 단계는 아니며, 5B의 lint 통과만으로도 Phase 5D 진행에는 지장 없다.

### 5C. cloud0 반영 + 재시뮬레이션

```python
# 수정 파일 반영 후 Phase 2A 재진입
sim_batch_run(test_name="FAILING_TEST", dump_signals=[...])
```

### 5D. Regression

수정이 다른 테스트를 깨뜨리지 않는지 전체 확인:

```python
# Phase 1D 워크플로우로 포괄 신호 집합 구성 후
sim_regression(test_list=["TOP012", ..., "TOP016"], dump_signals=[포괄집합])
```

결과의 각 테스트는 Phase 3(로그 판별)로 재진입, FAIL 시 재실행 없이 CSV 분석(Phase 4)으로 진행.

### 5D-2. Regression 결과 검증 및 리포팅

**Step 1. 로그 판별(1차)**: `Simulation complete via $finish`/`[V-XX] PASS/FAIL`/`Errors: N`/`*E,CUVMUR`(elab 실패) → PASS/SKIP/ELAB FAIL 분류.

**Step 2. CSV Waveform 검증(2차, PASS 테스트만)**:

```bash
simvisdbutil {shm_path} -csv -output /tmp/{test}_check.csv -overwrite -sig test_id -sig r_rcvData ...
awk -F',' 'NR==1{next} BEGIN{prev=""} {key=$2","$3; if(key!=prev){printf "  tid=%-3s signal=%-6s time=%s\n",$1,$2,$3; prev=key}}' CSV_FILE
```

이상 시점 발견 시 phase-4-waveform.md의 bisect→CSV→FSM 대조 루프로 정밀 분석.

**Step 3. 리포트**: (1) 실행 결과 요약표(Test/상태/사유) (2) 테스트별 단계 상세표(PASS만) (3) SKIP/FAIL 사유 상세표. `docs/04-report/features/regression-{scope}.report.md`에 저장.

### 5E. 문서 갱신

| 문서 | 갱신 내용 |
|------|----------|
| `.ai/analysis/{module}.analysis.md` | FSM 전이 테이블, 신호 의존성 업데이트 |
| `docs/04-report/{feature}.report.md` | 버그 설명, 수정 내용, regression 결과 |
| `.ai/knowledge/` | 재발 방지용 knowledge 문서(필요 시) |

### 5F. 세션 종료 — 시뮬레이션 프로세스 정리 (필수)

**디버깅 사이클을 마치고 세션을 끝내기 직전, 반드시 아래를 실행한다.** Phase 3에서 PASS로 즉시 종료하는 경우도 동일하게 적용된다(모든 종료 경로가 이 단계로 수렴).

```python
sim_disconnect(action="shutdown", target="all")
```

**이유**: bridge 모드(`connect_simulator`/`sim_bridge_run`)로 붙인 xmsim/SimVision 프로세스는 MCP worker의 자식 프로세스가 아니다 — worker가 재시작되거나 idle-culler가 워커를 정리해도 xmsim 자체는 전혀 영향받지 않고 계속 돈다. `sim_disconnect(shutdown)`을 호출하지 않고 세션을 끝내면 시뮬레이션 프로세스가 host에 그대로 남아 SHM 덤프를 계속 잡고 있거나 방치되어, **수 주 뒤 host disk를 전부 소진시키는 사고**로 이어질 수 있다(실제 발생 이력 있음).

- `action="bridge"`(연결만 해제) 또는 그냥 세션 종료(도구 호출 없이 방치)는 **SHM 유실 위험 + 프로세스 잔류 위험**이 있어 세션 종료 시점에는 사용하지 않는다.
- `action="shutdown"`은 Tcl `finish`를 안전하게 호출해 SHM을 보존하며 xmsim 프로세스를 종료시킨다(`tool-map.md` §2B 참조).
- batch/regression 모드(`sim_batch_run`/`sim_regression`)로 실행한 시뮬레이션은 이 단계 대상이 아니다 — 완료/타임아웃 시 `batch_runner.py`가 이미 자체적으로 프로세스를 정리한다(대상은 bridge 모드로 사람/에이전트가 상시 연결해 둔 세션뿐).

## Tool 예시

```python
sim_batch_run(test_name="TOP015", dump_signals=["r_regAddr", "r_streamRwState"])
sim_regression(test_list=["TOP012", "TOP013", "TOP014", "TOP015", "TOP016"])
```

## verilog-rtl-debugger agent 위임

| 상황 | 있으면 위임 | 없으면 Claude가 직접 Read할 근거 문서 |
|------|-----------|--------------------------------------|
| 근본 원인 확정 후 실제 RTL 수정 코드 작성 | `verilog-rtl-coder` | `~/.claude/skills/verilog-rtl/SKILL.md` §1/§3/§4 |
| 수정 커밋 전 AI-failure 패턴 리뷰 | `verilog-rtl-reviewer` | `~/.claude/agent-kit/failure-taxonomy.md`(T1~T9) + verilog-rtl §10 |
| 아키텍처 경계 판단 필요(신규 FSM/모듈/case-arm) | `verilog-rtl-architect-advisor` | 사용자에게 직접 확인(자동 판단 회피) |
| self-contained 로직/타이밍 클레임 형식 증명 | `verilog-rtl-prover`(필요 시) | `formal-verification` skill(sby/BMC) |

## 다음 단계

Regression PASS + 문서 갱신 완료 → **5F(세션 종료 정리) 실행** → 디버깅 사이클 종료. 새 FAIL 발견 시 Phase 2로 재진입(반복 패턴: FAIL → 분석 → 수정 → PASS). 5F는 사이클을 몇 번을 반복하든 세션을 최종적으로 끝낼 때 딱 한 번만 실행하면 된다(중간 재진입 시점마다 반복할 필요 없음).
