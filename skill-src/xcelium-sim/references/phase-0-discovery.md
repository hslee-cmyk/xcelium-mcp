# Phase 0 — 검증 환경 인프라 분석 (1회성, 캐시)

## 목적

검증 환경의 공유 컴포넌트와 테스트케이스는 프로젝트 수명 동안 비교적 안정적이다. 한 번 분석하고 캐시하면 이후 모든 디버깅에서 재사용한다. 이 Phase는 검증 환경 종류(Legacy directed Verilog / UVM / Directed SV / AMS / Multi-methodology)에 무관하게 동일 원칙이 적용된다.

## 절차

### 0-Prep. xcelium-mcp 환경 등록 (최초 1회)

```python
sim_discover(sim_dir="", force=False)   # TB type/shell/EDA/sim_dir/setup_tcls/sdf_info/top_module 자동 감지
list_tests()                             # 테스트 목록 캐싱
mcp_config(action="get", key="runner.default_mode")   # 필요 시 수동 조정
```

`sim_discover`는 TB type을 감지해 `tb_type: ncsim_legacy | uvm | sv_directed | mixed`로 등록한다. v5.2 이후 `boundary_depth` 파라미터로 블록 경계 자동 탐색 깊이도 함께 설정 가능(Flow B — Yosys JSON lazy discovery, 실제 탐색은 이후 `sim_batch_run`에서 지연 수행).

### 0A. 공유 컴포넌트 분석서

파일 네이밍: `.ai/analysis/tb_{env}_{component_name}.analysis.md` (env prefix 항상 필수: `lgc`/`uvm`/`dsv`/`ams`)

대상 식별: 테스트케이스에서 `include`/`import`/인스턴스화되는 외부 파일·패키지 중 여러 테스트에서 공통으로 쓰이는 것.

필수 포함 항목: 인터페이스 API, 프로토콜/시퀀스, 타이밍, 알려진 제약, DUT 계층 참조, 상위 호출 패턴, 판정 기여.

### 0B. 테스트케이스별 분석 캐시

파일 네이밍: `.ai/analysis/tb_{env}_{test_name}.analysis.md`

작성 절차(ncsim Legacy 기준):
1. 테스트 파일 스캔 — DUT 신호 참조(`grep -oE 'top\.hw\.\S+'`), 사용 task, include 파일
2. 시나리오 추출 — `run_test` task의 `test_id` + 인접 주석
3. 시퀀스 상세 파악 — task 시퀀스 패턴(예: I2C `send_start→send_data→recv_data→send_stop`)
4. 공통 Dump 신호 추출(regression 직전 1회) — 전체 테스트 DUT 신호 합집합 → `tb_{env}_regression_common_signals.analysis.md`

필수 포함 항목(환경 무관): 테스트 목적, 사용 공유 컴포넌트, 테스트 시나리오(tid별 표), 판별 방법, 판별 신호+기대값, DUT 내부 참조, 시뮬레이션 길이, 버그 이력.

### 0C. 캐시 관리 규칙

| 규칙 | 설명 |
|------|------|
| 작성 시점 | 해당 컴포넌트/테스트를 처음 디버깅할 때 (lazy) |
| 갱신 시점 | 해당 파일이 수정되었을 때만 |
| 갱신 불필요 | RTL만 수정되고 TB 파일 미변경 시 |
| 저장 위치 | `.ai/analysis/tb_*.analysis.md` |
| 참조 관계 | RTL 분석서 → DUT FSM/신호, TB 분석서 → 시퀀스/기대값/API |

## Tool 예시

```python
sim_discover(sim_dir="", force=False)
list_tests(pattern="TOP01*")
mcp_config(action="show", file="registry")
```

## 다음 단계

캐시가 있으면 phase-1-analysis.md로, 없으면 이 Phase에서 분석서 작성 후 phase-1-analysis.md로 진행.
