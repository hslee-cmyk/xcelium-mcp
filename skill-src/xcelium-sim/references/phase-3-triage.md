# Phase 3 — 1차 판별 (로그 기반, 가장 빠름)

## 목적

시뮬레이션 완료 후 waveform 분석에 들어가기 전, 로그만으로 먼저 판별한다.

## 절차

```bash
grep -E "PASS|FAIL|Errors:|COMPLETE" logs/ncsim_${TEST_NAME}.log
```

### 판별 매트릭스

| 로그 내용 | 판정 | 다음 단계 |
|-----------|------|----------|
| `Errors: 0` + 모든 PASS | **PASS** | Phase 5E(보고서 갱신) |
| `FAIL` 또는 `Errors: N (N>0)` | **FAIL** | Phase 4(waveform 분석) |
| `COMPLETE`만, PASS/FAIL 표시 없음 | **불확정** | Phase 4 |
| "verify in waveform" 문구 | **불확정** | Phase 4 |
| 시뮬레이션 hang/timeout | **FAIL** | Phase 2B(bridge로 재실행) |

### UVM 환경 추가 확인

```
uvm_report_server.get_severity_count(UVM_ERROR)   # 0이면 PASS 후보
```

## Tool 예시

이 phase는 로그 텍스트 검색만 사용하며, xcelium-mcp MCP tool 호출은 없다(Bash `grep`만).

## 다음 단계

PASS → Phase 5E. FAIL/불확정 → phase-4-waveform.md.
