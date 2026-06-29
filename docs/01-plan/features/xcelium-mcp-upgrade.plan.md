# xcelium-mcp-upgrade Plan

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | 현재 xcelium-mcp는 짧은 시뮬레이션(~17ms)에서만 검증됨. 긴 시뮬(100ms+)에서는 sim_run 블로킹, SHM 비대화, 버그 시점 탐색 비효율, 비정상 종료 위험이 존재 |
| **Solution** | mcp_bridge.tcl에 6개 메타 명령 추가: 안전 종료, 비동기 실행, watchpoint, probe 제어, 체크포인트, 이진 탐색 |
| **Function UX Effect** | AI agent가 수백ms 시뮬레이션에서도 자동으로 버그 시점을 특정하고, SHM 크기를 제어하며, hang 없이 안전하게 디버깅 |
| **Core Value** | 시뮬레이션 길이와 무관하게 일관된 MCP 디버깅 워크플로우 보장 |

---

## 1. Background

### 1.1 현재 상태

xcelium-mcp는 Cadence Xcelium 시뮬레이터를 Claude Code에서 원격 제어하는 MCP 서버.
`mcp_bridge.tcl`이 xmsim/SimVision 내부에서 TCP 서버로 동작하며, xcelium-mcp Python 클라이언트가 연결.

**검증 완료 (2026-03-25, TOP015 17ms):**
- batch 모드 실행 + live probing → 버그 발견 성공
- `finish` 명령으로 정상 종료 (SHM 보존) 확인
- simvisdbutil CSV 오프라인 분석 확인
- SimVision + Xvfb + mcp_bridge 원격 waveform 분석 확인

### 1.2 문제점 — 긴 시뮬레이션 대응 불가

| 문제 | 현재 (17ms) | 100ms+ 예상 |
|------|------------|-------------|
| sim_run 블로킹 | 수 초 | **수 분~수십 분**, 진행 상황 불명 |
| SHM 크기 | 177MB | **~1GB+** (probe all) |
| 버그 시점 탐색 | 5ms 단위 수동 | **수십 번 반복** 필요 |
| bridge 모니터링 | run 중 불가 | run 중 상태 확인 불가 |
| 비정상 종료 | 경험적 회피 | **SHM 손실 위험 증가** |
| 반복 분석 | 매번 재실행 | **초반 정상 구간 반복 실행** 낭비 |

### 1.3 참조 문서

- `.ai/knowledge/mcp-operations-guide.md` — 현재 운용 가이드 (10섹션)
- `docs/03-analysis/xcelium-mcp-debugging-comparison.md` — batch vs GUI 비교 분석
- `.ai/knowledge/i2c-repeated-start-race.md` — TOP015 버그 상세
- `design/top/sim/ncsim/scripts/mcp_bridge.tcl` — 현재 bridge 소스

---

## 2. Goals

### 2.1 Must Have (P0)

1. **`__SHUTDOWN__` 안전 종료 프로토콜**
   - `database -close` → `finish` 원자적 수행
   - 클라이언트에 완료 알림 후 종료
   - 구현 난이도: 낮음, 위험도: 낮음

2. **`__RUN_ASYNC__` 비동기 실행 + `__PROGRESS__` 모니터링**
   - sim_run을 백그라운드로 실행, bridge 이벤트 루프 유지
   - `__PROGRESS__`로 현재 시뮬 시간 조회 (run 중에도)
   - hang 조기 감지 가능
   - 구현 난이도: 중간, 위험도: 중간 (Tcl 이벤트 루프 이해 필요)

3. **Signal Watchpoint (`__WATCH__`)**
   - 특정 신호가 조건을 만족하면 자동 stop
   - `stop -create -condition` Tcl 명령 래핑
   - 긴 시뮬에서 버그 시점 자동 포착
   - 구현 난이도: 중간, 위험도: 낮음

### 2.2 Should Have (P1)

4. **선택적 Probe 제어 (`__PROBE_CONTROL__`)**
   - probe enable/disable로 SHM 크기 관리
   - 관심 구간에서만 SHM 기록
   - 구현 난이도: 낮음, 위험도: 낮음

5. **체크포인트/재시작 (`__SAVE__` / `__RESTORE__`)**
   - xmsim `save`/`restart -from` 명령 래핑
   - 정상 구간 저장 → 버그 구간만 반복 분석
   - 구현 난이도: 낮음 (xmsim 내장 기능), 위험도: 낮음

### 2.3 Nice to Have (P2)

6. **이진 탐색 (`__BISECT__`)**
   - bridge 내부에서 restart → run → check 반복
   - 네트워크 왕복 최소화, 자동으로 버그 시점 특정
   - 구현 난이도: 높음, 위험도: 중간

---

## 3. Scope

### 3.1 수정 대상 파일

| 파일 | 위치 | 변경 내용 |
|------|------|----------|
| `mcp_bridge.tcl` | `cloud0:~/git.clone/venezia-t0/design/top/sim/ncsim/scripts/` | 메타 명령 6개 추가 |
| `mcp-operations-guide.md` | `.ai/knowledge/` | 새 기능 사용법 추가 |

### 3.2 수정하지 않는 것

- xcelium-mcp Python 서버 (별도 저장소, 이번 스코프 외)
- run_sim_mcp 스크립트 (기존 호환성 유지)
- setup_rtl_mcp_*.tcl (기존 호환성 유지)

### 3.3 테스트 방법

각 기능 구현 후 TOP015 테스트케이스로 검증:
1. `__SHUTDOWN__`: finish 대신 `__SHUTDOWN__` 전송 → SHM 보존 확인
2. `__RUN_ASYNC__` + `__PROGRESS__`: 5ms async run → progress 조회 → 시간 증가 확인
3. `__WATCH__`: `r_regAddr == 0x12` watchpoint → 자동 stop 확인
4. `__PROBE_CONTROL__`: 0~10ms disable, 10~15ms enable → SHM 크기 감소 확인
5. `__SAVE__`/`__RESTORE__`: 10ms 체크포인트 → restore → 재실행 확인
6. `__BISECT__`: r_regAddr 비정상 시점 자동 특정 확인

---

## 4. Implementation Order

```
Phase 1 (P0 — 안전성 기반)
  ├─ F1: __SHUTDOWN__          (1시간)
  ├─ F2: __RUN_ASYNC__ + __PROGRESS__  (2시간)
  └─ F3: __WATCH__             (1시간)

Phase 2 (P1 — 효율성 향상)
  ├─ F4: __PROBE_CONTROL__     (30분)
  └─ F5: __SAVE__ / __RESTORE__ (30분)

Phase 3 (P2 — 고급 자동화)
  └─ F6: __BISECT__            (2시간)
```

### 구현 순서 근거

1. `__SHUTDOWN__`가 최우선 — SHM 손실은 모든 분석을 무효화
2. `__RUN_ASYNC__`가 다음 — 긴 시뮬의 핵심 병목
3. `__WATCH__`는 `__RUN_ASYNC__`와 시너지 (async run + watchpoint = 자동 디버깅)
4. Phase 2는 Phase 1 완료 후, 실제 긴 시뮬에서 테스트하며 진행
5. `__BISECT__`는 Phase 1-2가 안정화된 후 최적화로 추가

---

## 5. Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Tcl `after idle`로 async run 구현 시 이벤트 루프 충돌 | bridge 응답 중단 | 중 | xmsim의 `run -non_blocking` 옵션 먼저 조사 |
| watchpoint 조건식 문법 오류 | stop 미동작 | 낮 | Tcl catch로 에러 핸들링 |
| probe disable 중 관심 신호 누락 | 분석 불가 | 중 | 핵심 신호만 별도 probe 유지 옵션 |
| save 파일 크기 (메모리 스냅샷) | 디스크 부족 | 낮 | /tmp에 저장, 크기 경고 |
| bisect 중 sim_restart가 SHM 덮어쓰기 | 이전 덤프 손실 | 중 | bisect 전 SHM backup |

---

## 6. Success Criteria

| 기준 | 측정 방법 |
|------|----------|
| `__SHUTDOWN__`으로 SHM 100% 보존 | finish 대비 SHM 크기 동일 |
| `__RUN_ASYNC__` 중 `__PROGRESS__` 응답 | run 중 현재 시간 조회 성공 |
| `__WATCH__`로 버그 시점 자동 stop | TOP015 regAddr=0x12 시점에 정확히 멈춤 |
| `__PROBE_CONTROL__`로 SHM 50%+ 감소 | probe 선택적 적용 전후 비교 |
| `__SAVE__`/`__RESTORE__`로 재실행 시간 50%+ 단축 | 체크포인트 복원 vs 처음부터 비교 |
| `__BISECT__`로 버그 시점 10us 이내 특정 | 수동 탐색 대비 시간/정확도 비교 |

---

## 7. Timeline

| Phase | 기간 | 마일스톤 |
|-------|------|---------|
| Phase 1 (P0) | 1일 | __SHUTDOWN__, __RUN_ASYNC__, __WATCH__ 구현 + TOP015 검증 |
| Phase 2 (P1) | 0.5일 | __PROBE_CONTROL__, __SAVE__/__RESTORE__ 구현 |
| Phase 3 (P2) | 1일 | __BISECT__ 구현 + 긴 시뮬 실전 테스트 |
| 문서화 | 0.5일 | mcp-operations-guide 최종 업데이트 |
