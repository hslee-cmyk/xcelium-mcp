# Phase 5 — 수정 + 검증

## 목적

근본 원인이 확정된 후 RTL을 수정하고, lint→재시뮬레이션→regression→문서 갱신까지 완료한다.

## 절차

### 5A. RTL 수정 (로컬) — agent 위임 체인

1. `verilog-rtl-debugger`가 Phase 4C에서 확정한 근본 원인을 `verilog-rtl-coder` agent에 전달해 수정 코드 작성 위임(verilog-rtl skill 규칙 자동 적용)
2. 분석서 참조: `.ai/analysis/{module}.analysis.md`
3. 수정 코드 작성 — 사이클 주석 포함, Bit-width safety 검증
4. 수정 규모가 신규 FSM/모듈/case-arm 등 아키텍처 경계를 건드리면 `verilog-rtl-architect-advisor`에 먼저 에스컬레이션
5. 커밋 전 `verilog-rtl-reviewer`로 AI-failure 시그니처(T1~T9) 리뷰

> **Fallback**: 위 agent들을 찾을 수 없으면 Claude가 verilog-rtl skill을 직접 활성화해 동일 절차(분석서 참조 → 수정 → bit-width 검증 → 리뷰 관점 자체 점검)를 수행한다.

### 5B. Verilator Lint

```bash
C:/msys64/usr/bin/bash.exe -lc "verilator --lint-only -Wall --top-module <top> <files>"
```

기존 warning만 확인, 새 에러 없음을 검증. self-contained 로직/타이밍 클레임은 `verilog-rtl-prover`로 formal 증명 검토(필요 시).

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

## Tool 예시

```python
sim_batch_run(test_name="TOP015", dump_signals=["r_regAddr", "r_streamRwState"])
sim_regression(test_list=["TOP012", "TOP013", "TOP014", "TOP015", "TOP016"])
```

## verilog-rtl-debugger agent 위임

| 상황 | 위임 대상 |
|------|----------|
| 근본 원인 확정 후 실제 RTL 수정 코드 작성 | `verilog-rtl-coder` |
| 수정 커밋 전 AI-failure 패턴 리뷰 | `verilog-rtl-reviewer` |
| 아키텍처 경계 판단 필요(신규 FSM/모듈/case-arm) | `verilog-rtl-architect-advisor` |
| self-contained 로직/타이밍 클레임 형식 증명 | `verilog-rtl-prover`(필요 시) |

## 다음 단계

Regression PASS + 문서 갱신 완료 → 디버깅 사이클 종료. 새 FAIL 발견 시 Phase 2로 재진입(반복 패턴: FAIL → 분석 → 수정 → PASS).
