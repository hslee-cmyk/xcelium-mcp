# xcelium-mcp 디버깅 방식 비교 분석

**작성**: 2026-03-25
**대상**: TOP015 I2C 8-bit offset test (repeated START race condition)
**검증 환경**: cloud0, Xcelium 22.09-s007

---

## 1. 테스트된 디버깅 방식

### 방식 A: Batch + Live Probing (xcelium-mcp)

**구성**: `xmsim -batch` + `mcp_bridge.tcl` + xcelium-mcp 클라이언트
**라이선스**: xmsim 만 필요 (Affirma_sim_analysis_env 불필요)

| 항목 | 결과 |
|------|------|
| 시뮬 실행 | `sim_run(duration="5ms")` 단계적 실행 |
| 신호 확인 | `get_signal_value` — 현재 시점 값만 |
| 시간 추적 | 수동 (duration 반복, 값 변화 시점 좁히기) |
| 자동화 | 완전 자동화 가능 (MCP 프로토콜) |
| X11 필요 | 불필요 |
| 버그 발견 | **성공** — signal probing으로 race condition 확정 |

**장점**:
- GUI 라이선스 없이 동작
- 완전 자동화 가능 (AI agent가 직접 디버깅)
- SSH만으로 원격 접근

**단점**:
- 현재 시점의 값만 볼 수 있음 (과거 waveform 탐색 불가)
- 버그 시점을 좁히려면 반복 실행 + probing 필요
- sim_run duration 설정에 경험 필요 (너무 길면 hang 위험)

---

### 방식 B: Batch + SHM → simvisdbutil CSV 분석

**구성**: `xmsim -batch` + SHM dump → `simvisdbutil -csv` 후처리
**라이선스**: xmsim 만 필요

| 항목 | 결과 |
|------|------|
| 시뮬 실행 | batch 전체 실행 또는 `finish`로 조기 종료 |
| 신호 확인 | CSV로 시간축 전체 추적 가능 |
| 시간 추적 | `-range` 옵션으로 구간 지정 |
| 자동화 | 완전 자동화 가능 (CLI) |
| X11 필요 | 불필요 |
| 버그 분석 | **성공** — CSV에서 rwState/regAddr/rxData 변화 추적 |

**장점**:
- 시뮬 후 오프라인 분석 — 시뮬 재실행 불필요
- 특정 시간 구간 + 특정 신호만 추출 가능
- 텍스트 기반 — grep, awk, 스크립트 분석 가능
- AI agent가 CSV를 직접 읽고 분석 가능

**단점**:
- SHM 파일 크기 큼 (17ms에 177MB)
- 전체 waveform 시각화 불가
- 신호 이름을 정확히 알아야 함 (계층 경로)

---

### 방식 C: SimVision GUI + mcp_bridge

**구성**: `simvision` GUI + `mcp_bridge.tcl` + xcelium-mcp 클라이언트
**라이선스**: Affirma_sim_analysis_env 필요

| 항목 | 결과 |
|------|------|
| 시뮬 실행 | GUI에서 실행 또는 SHM 오프라인 오픈 |
| 신호 확인 | waveform 시각화 (줌, 스크롤) |
| 시간 추적 | 마우스 커서로 즉시 탐색 |
| 자동화 | Tcl 스크립트로 부분 자동화 |
| X11 필요 | **필요** |
| 버그 분석 | 이전 세션에서 성공 (waveform screenshot) |

**장점**:
- 가장 직관적 — 전체 waveform 한눈에 확인
- 마우스로 시간 구간 탐색 (xview)
- waveform print로 스크린샷 생성

**단점**:
- X11 포워딩 필요 (SSH -X)
- GUI 라이선스 필요
- 스크린샷이 EPS 형식 → PNG 변환 필요 (gs)
- SimVision stdout이 세션 로그로만 출력 → 디버깅 어려움
- Tcl API 명명이 직관적이지 않음 (waveform zoom → xview)

---

## 2. 효율성 비교 매트릭스

| 기준 | A: Live Probing | B: CSV 분석 | C: SimVision GUI |
|------|----------------|------------|-----------------|
| 셋업 시간 | 낮음 (bridge만) | 낮음 (CLI) | 높음 (X11+라이선스) |
| 버그 발견 속도 | 중간 (반복 필요) | **빠름** (한번에 전체) | **빠름** (시각적) |
| 자동화 수준 | **높음** | **높음** | 중간 |
| AI 친화도 | **높음** | **높음** | 낮음 (이미지 의존) |
| 인간 친화도 | 낮음 (값만) | 중간 (텍스트) | **높음** (시각적) |
| 라이선스 비용 | 낮음 | 낮음 | **높음** |
| hang 대응 | **즉시** (sim_stop) | 사후 분석만 | 사후 분석만 |

---

## 3. 권장 워크플로우: 하이브리드 접근

```
[Phase 1] Batch + Live Probing (방식 A)
  │ sim_run(duration) → 단계적 실행
  │ get_signal_value → 실시간 상태 모니터링
  │ hang 감지 시 sim_stop → 즉시 분석
  │
  ├─ 버그 발견 → finish로 정상 종료 (SHM 저장)
  │
[Phase 2] SHM → CSV 분석 (방식 B)
  │ simvisdbutil -csv -range -signal → 핵심 구간 추출
  │ AI가 CSV 읽고 자동 분석
  │
  ├─ 추가 시각 확인 필요 시
  │
[Phase 3] SimVision GUI (방식 C) — 선택적
    simvision ../dump/ci_top.shm
    전체 waveform 시각 확인
```

### 핵심 원칙

1. **항상 duration 제한** — `sim_run(duration="Xms")`로 hang 방지
2. **finish로 정상 종료** — `exit`나 `pkill` 사용 금지 (SHM 손실)
3. **CSV 우선, GUI 보조** — X11 없이도 분석 완결 가능
4. **AI agent 루프** — probing → CSV 추출 → 자동 분석 → 수정 → 재시뮬

---

## 4. 발견된 문제 및 개선 필요사항

### mcp_bridge.tcl
- `__SHUTDOWN__` 메타 명령 필요: `database -close` → `finish` 안전 종료
- 단일 클라이언트 제한 문제: ping 체크와 실 클라이언트 경쟁
- `__SCREENSHOT__`이 EPS 출력: PNG 직접 출력 또는 변환 내장 필요

### xcelium-mcp Python 서버
- `sim_run` 응답 timeout이 짧음: 긴 시뮬레이션 대응 필요
- `list_signals` scope 탐색 실패: Tcl scope 명령 호환성 문제
- `sim_stop` 에러 처리: 이미 정지 상태일 때 에러 대신 OK 반환

### run_sim_mcp 스크립트
- ping 루프가 bridge 단일 클라이언트 슬롯 경쟁: ping 방식 변경 필요
- `vwait forever` 안정성: socket 생성 실패 시 즉시 종료되는 문제

---

## 5. 실험 데이터

### TOP015 V-18 bug 재현 타임라인 (batch probing)

| 시간 | test_id | err_cnt | SDA | SCL | 상태 |
|------|---------|---------|-----|-----|------|
| 5ms | 0 | 0 | 0 | 0 | 초기화 중 |
| 10ms | 1 | 0 | 0 | 0 | V-18 write 진행 |
| 13ms | 1 | 0 | z | 0 | V-18 트랜잭션 진행 |
| 15ms | 1 | 1 | 0 | z | V-18 FAIL, 두 번째 write 시작 |
| 17ms | 1 | 1 | 0 | z | **hang** — SDA stuck LOW |

### CSV 분석 (simvisdbutil, 11~15ms)

| 시간 | rwState | regAddr | rxData | 의미 |
|------|---------|---------|--------|------|
| 11ms | 1 (WRITE) | 0x11 | 0x02 | write 진행, regAddr 비정상 증가 |
| 12ms | 3 (READ) | 0x10 | 0x20 | read 전환, CONFIG_DUR 접근 |
| 13ms | 3 (READ) | 0x11 | 0xFF | read-back FAIL (0xFF) |
| 14ms | 0 (IDLE) | 0x12 | 0x40 | regAddr 비정상 증가, rxData=device byte |
| 15ms | 1 (WRITE) | 0x12 | 0x02 | 두 번째 write, hang 상태 |
