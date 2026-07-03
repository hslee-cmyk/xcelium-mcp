# Plan: xcelium-mcp Debugging Workflow

> **Feature**: RTL 시뮬레이션 디버깅 표준 워크플로우
>
> **Date**: 2026-03-26
> **Status**: Draft
> **Based on**: sync-xfr-extension (TOP012~TOP016) 실전 디버깅 경험

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | RTL 디버깅이 코드 추측 → 시뮬레이션 → 실패 반복으로 비효율적. 도구 선택 기준 없음 |
| **Solution** | 5-Phase 표준 워크플로우: 정적 분석 → 시뮬레이션 실행 → 로그 판별 → Waveform 분석 → 수정 검증 |
| **Function UX Effect** | 단계별 판단 기준이 명확하여 불필요한 반복 제거, 적절한 도구를 자동 선택 |
| **Core Value** | 디버깅 시간 50% 이상 단축, 재현 가능한 분석 프로세스, AI-human 협업 최적화 |

---

## Agent 위임 구조 (`verilog-rtl-debugger`, chip-design-skills 신설 예정)

기존 chip-design-skills의 5개 verilog-rtl-* agent(analyst/architect-advisor/coder/prover/reviewer)는 전부 **정적 분석·코드 작성·formal 증명 전용**이며 MCP tool 접근 권한이 없다. 이 문서의 Phase 2~4(xcelium-mcp로 실제 시뮬레이션을 실행하고 bisect/CSV/FSM을 대조하는 **라이브 디버깅 루프**)를 수행할 agent가 지금 하나도 없다 — 이건 기존 5개 agent 스코프 밖의 진짜 공백이다.

신설할 `verilog-rtl-debugger` agent가 이 공백을 메운다: **MCP tool 접근 권한을 가진 유일한 agent**로서 Phase 2(시뮬레이션 실행)~Phase 4(bisect/CSV 추출/FSM 전이 대조, Phase 4E의 AI 자율 디버깅 루프)를 직접 소유·수행하며, 필요 시 아래처럼 기존 agent를 호출하는 라우터 역할도 겸한다:

| 상황 | 위임 대상 | 트리거 Phase |
|------|----------|-------------|
| `.ai/analysis/{module}.analysis.md` 부재/stale | `verilog-rtl-analyst` | Phase 1B |
| `.ai/analysis/tb_{env}_*.analysis.md`(공유 컴포넌트/테스트케이스) 부재/stale | `verilog-tb-analyst` (신설 예정) | Phase 0A/0B, 1A |
| 근본 원인 확정 후 실제 RTL 수정 코드 작성 | `verilog-rtl-coder` | Phase 5A |
| 수정 커밋 전 AI-failure 패턴 리뷰 | `verilog-rtl-reviewer` | Phase 5A~5B |
| 아키텍처 경계 판단 필요 (신규 FSM/모듈/case-arm) | `verilog-rtl-architect-advisor` | Phase 5A (수정 규모가 클 때) |
| self-contained 로직/타이밍 클레임 형식 증명 | `verilog-rtl-prover` | Phase 5B (필요 시) |
| Verilog-A / AMS 아날로그 컴포넌트 디버깅 | (verilog-rtl-debugger 직접 수행, `verilog-a` skill 참조) | Phase 1D-4/4E (AMS Tier 3) |

**배포**: chip-design-skills repo에 기존 5개 agent와 동일하게 정본 관리, `install.py`로 `~/.claude/agents/`에 배포 — 독립 배포가 아니라 기존 agent kit 컨벤션을 그대로 따른다(이 점이 `xcelium-mcp-tool-usage-guide`의 skill 배포 방식과 다름에 유의). 상세 설계(시스템 프롬프트, tool 목록, 트리거 문구)는 chip-design-skills repo 소관의 별도 PDCA 사이클(`chip-design-skills/docs/01-plan/features/verilog-rtl-debugger.plan.md`)에서 진행하며, 이 문서는 "언제·무엇을 위임하는가"만 규정한다.

**TB 분석서(Phase 0A/0B) 위임 — 비대칭 해소 (2026-07-03 결정)**: 위 위임 표는 원래 RTL 분석서(Phase 1B → `verilog-rtl-analyst`)만 다뤘고, Phase 0A/0B(TB 공유 컴포넌트·테스트케이스 분석서 작성 — 파일 스캔·시나리오 추출·시퀀스 파악·템플릿 작성까지 포함하는 자기완결적 방법론)는 위임 대상 없이 `verilog-rtl-debugger`(또는 Claude)가 그때그때 직접 수행하도록 방치돼 있었다. venezia-fpga 세션에서 `~/.claude/skills/xcelium-sim/`의 SKILL.md가 스스로를 "tool 사용법 전용"이라 선언하면서도 정작 이 TB 분석 방법론(§0A/0B)을 통째로 안고 있는 모순이 지적되며 이 공백이 드러났다. RTL 쪽과 대칭되도록 신규 agent `verilog-tb-analyst`(가칭, chip-design-skills 신설 예정)를 도입해 Phase 0A/0B·1A(캐시 미스 시 작성)의 TB 분석서 작성/갱신을 전담시키기로 결정했다. 배포 경로는 `verilog-rtl-debugger`와 동일(chip-design-skills repo, install.py), 상세 설계는 별도 PDCA 사이클(`chip-design-skills/docs/01-plan/features/verilog-tb-analyst.plan.md`, 신설 예정)에서 진행한다. Agent가 아직 배포되지 않은 동안은 기존 컨벤션과 동일하게 `verilog-rtl-debugger`(또는 Claude)가 §0A/0B 절차를 직접 수행하는 fallback을 유지한다.

**방법론 출처 — `references/phase-2~4*.md`가 agent의 "public API"가 됨 (2026-07-03 결정)**: `verilog-rtl-debugger` agent는 Phase 2~4 방법론을 자기 system prompt에 복사하지 않고, **런타임에 `~/.claude/skills/xcelium-sim/references/phase-2~4*.md`(tool-usage-guide FR-02~FR-06 산출물)를 직접 Read**해서 따른다 — 두 repo에 같은 내용이 중복돼 drift되는 것을 막기 위함. 이 때문에 **해당 reference 파일은 더 이상 이 skill 내부용 문서가 아니라, 다른 repo의 agent가 소비하는 계약(contract)**이 된다. `xcelium-mcp-tool-usage-guide.plan.md`에서 이 파일들을 수정할 때는 "Claude가 skill 안에서 읽기 좋은 형태"뿐 아니라 "독립된 agent가 그 자체로 읽고 수행할 수 있는 자기완결적 형태"인지도 함께 검토해야 한다.

---

## 전체 워크플로우 개요

```
Phase 0: 테스트벤치 인프라 분석 (1회성, 캐시)
    │
    ├─ 0-Prep. xcelium-mcp 환경 등록 (sim_discover → list_tests → mcp_config)
    ├─ 0A. 공유 모델 분석 (i2c_model.inc, pcm_model.inc) → Task API 문서화
    ├─ 0B. 테스트케이스별 분석 → 시퀀스·판별 신호·기대값 캐시
    └─ 0C. 캐시 관리 규칙 (env prefix 네이밍 · lazy 작성)

Phase 1: 사전 분석 (로컬, 시뮬레이터 불필요)
    │
    ├─ 1A. 캐시 참조 → 이미 분석된 테스트면 바로 Phase 2로
    ├─ 1B. RTL 분석서 참조 → 관련 FSM/신호 의존성 파악
    ├─ 1C. Dump scope 확인 → 필요 시 dump_depth/dump_signals 조정
    └─ 1D. Regression용 포괄 신호 집합 구성 (regression 직전만, Claude 워크플로우)

Phase 2: 시뮬레이션 실행 (cloud0)
    │
    ├─ 2A. Batch mode (권장) — xcelium-mcp sim_batch_run/sim_regression 사용, SHM dump 자동 생성
    └─ 2B. Bridge mode (필요 시) — xcelium-mcp sim_bridge_run → interactive probing

Phase 3: 1차 판별 — 로그 기반 (가장 빠름)
    │
    ├─ PASS/FAIL/Errors 키워드 검색
    ├─ PASS (전체 성공) → Phase 5E (보고서 갱신)
    └─ FAIL 또는 불확정 → Phase 4 (waveform 분석)

Phase 4: 2차 판별 — Waveform CSV 분석 (핵심)
    │
    ├─ 4A. bisect로 이상 시점 1차 특정 → 해당 구간 CSV 추출 → in-memory 분석
    ├─ 4B. 추가 신호 보충 추출 (dump 신호가 달라질 때만 재추출, 그 외 in-memory 분석)
    ├─ 4C. 근본 원인 특정 → FSM 전이 대조
    └─ (필요 시) 4D. Interactive probing으로 보완

Phase 5: 수정 + 검증
    │
    ├─ 5A. RTL 수정 (로컬)
    ├─ 5B. Verilator lint 확인
    ├─ 5C. cloud0 반영 + 재시뮬레이션 (Phase 2 재진입)
    ├─ 5D. Regression 전체 PASS 확인
    └─ 5E. 분석서 + 보고서 갱신
```

---

## Phase 0: 검증 환경 인프라 분석 (1회성, 캐시)

검증 환경의 공유 컴포넌트와 테스트케이스는 프로젝트 수명 동안 비교적 안정적이다. **한 번 분석하고 캐시하면 이후 모든 디버깅에서 재사용**한다.

**작성 주체 (2026-07-03 추가)**: 0A/0B의 분석서 작성/갱신은 `verilog-tb-analyst` agent(§Agent 위임 구조, chip-design-skills 신설 예정)가 전담한다 — RTL 쪽 `verilog-rtl-analyst`(Phase 1B)와 대칭 구조. Agent 미배포 시 fallback: `verilog-rtl-debugger` 또는 Claude가 아래 절차를 직접 수행한다.

이 Phase는 **검증 환경의 종류에 무관**하게 동일 원칙이 적용된다:

| 환경 | 공유 컴포넌트 | 테스트케이스 | env prefix |
|------|-------------|------------|:---:|
| **Legacy directed Verilog** | `*.inc` 파일의 task/function (i2c_model, pcm_model, jtag_model 등) | `tb_tests/*.v` 내 run_test task | `lgc` |
| **UVM** | Agent (driver/monitor/sequencer), Env, RAL, Scoreboard, Reference Model | `*_test.sv`, `*_seq.sv` (sequence) | `uvm` |
| **Directed SV** | BFM task/function, Checker module | `*_test.sv` 내 initial block | `dsv` |
| **AMS** | Verilog-A, wreal, connect module 컴포넌트 | AMS stimuli + checker | `ams` |
| **Multi-methodology** | 위 조합 | 위 조합 | 각 컴포넌트의 고유 prefix |

### 0-Prep. xcelium-mcp 환경 등록 (최초 1회)

본격적인 Phase 0A/0B 분석 전에 xcelium-mcp에 시뮬레이션 환경을 등록한다.

```python
# 1. 자동 감지: TB type, shell, EDA env, sim_dir, setup_tcls, sdf_info, top_module
sim_discover(sim_dir="", force=False)
# → .mcp_sim_config.json 생성, mcp_registry.json에 등록

# 2. 테스트 목록 캐싱
list_tests()
# → cached_tests 저장, 이후 호출은 캐시 사용

# 3. (필요 시) 수동 조정
mcp_config(action="get", key="runner.default_mode")           # rtl/gate/ams_rtl/ams_gate
mcp_config(action="set", key="runner.mode_defaults.gate.timeout", value="1800")
```

이 단계는 xcelium-mcp v4.2 이후 자동화되어 있으며, sim_discover가 TB type을 감지해 `tb_type: ncsim_legacy | uvm | sv_directed | mixed`로 등록한다. 아래 Phase 0A/0B는 이 등록된 환경을 전제로 한다.

### 0A. 공유 컴포넌트 분석서

**파일 네이밍 규칙**: `.ai/analysis/tb_{env}_{component_name}.analysis.md`

- env prefix는 **항상 필수** — single/multi-methodology 무관
- 파일명만 보고도 TB 환경을 즉시 유추 가능하도록 함
- Multi-methodology 프로젝트에서는 충돌 방지 효과도 겸함

**환경 prefix** (TB 방법론 기준, 3자 통일):

| prefix | 환경 | 비고 |
|--------|------|------|
| `lgc` | **Legacy** directed Verilog (`*.inc`, `tb_tests/*.v`) | 구 스타일 directed TB |
| `uvm` | **UVM** methodology | Agent/Env/Sequence/RAL/Scoreboard |
| `dsv` | **Directed SV** (non-UVM) | BFM + Checker 스타일 |
| `ams` | **AMS** / Mixed-Signal 컴포넌트 | Verilog-A, wreal, connect module 사용 |

**AMS prefix 원칙**: 컴포넌트 자체가 analog를 다루는 경우에만 `ams`. 프로젝트가 AMS여도 순수 digital TB 컴포넌트는 원래 방법론(`lgc`/`uvm`/`dsv`) prefix 사용.

| 컴포넌트 유형 | 예시 | prefix |
|---------------|------|:------:|
| Verilog-A / wreal / connect module 사용 | PLL behavioral, ADC wreal | `ams` |
| Analog signal checker | lock_det, freq_monitor | `ams` |
| Digital bus agent (AMS 프로젝트여도) | APB/I2C agent | `lgc`/`uvm`/`dsv` |
| Digital scoreboard/counter | pass/fail counter | `lgc`/`uvm`/`dsv` |

프로젝트의 검증 환경에서 재사용되는 모든 공유 컴포넌트를 분석한다.

**대상 식별 방법:**

```
1. 테스트케이스에서 `include`, `import`, 인스턴스화되는 외부 파일/패키지 추출
2. 여러 테스트에서 공통으로 사용되는 것 = 공유 컴포넌트
3. 테스트 1개에서만 사용되는 것 = 0B의 로컬 컴포넌트로 분류
```

**환경별 공유 컴포넌트 예시:**

| 환경 | 컴포넌트 | 분석서 파일명 |
|------|---------|---------------|
| Legacy | `i2c_model.inc` | `tb_lgc_i2c_model.analysis.md` |
| Legacy | `pcm_model.inc` | `tb_lgc_pcm_model.analysis.md` |
| Legacy | `cola_encoder.inc` | `tb_lgc_cola_encoder.analysis.md` |
| UVM | `i2c_agent` | `tb_uvm_i2c_agent.analysis.md` |
| UVM | `pcm_agent` | `tb_uvm_pcm_agent.analysis.md` |
| UVM | `venezia_scoreboard` | `tb_uvm_venezia_scoreboard.analysis.md` |
| UVM | `venezia_ral_model` | `tb_uvm_venezia_ral.analysis.md` |
| Directed SV | `apb_bfm` | `tb_dsv_apb_bfm.analysis.md` |
| AMS | `pll_behavioral` (Verilog-A) | `tb_ams_pll_behavioral.analysis.md` |
| AMS | `adc_wreal_model` | `tb_ams_adc_wreal_model.analysis.md` |

**필수 포함 항목 (환경 무관):**

| 항목 | ncsim Legacy 예시 | UVM 예시 |
|------|-------------------|----------|
| **인터페이스 API** | task 시그니처 (인자, 반환) | sequence API, RAL read/write |
| **프로토콜/시퀀스** | task 내부 파형 구동 | driver의 bus 트랜잭션 |
| **타이밍** | 소요 클럭 사이클 | 트랜잭션 latency |
| **알려진 제약/주의** | race condition, timing 요구 | sequence ordering 제약 |
| **DUT 계층 참조** | task에서 접근하는 DUT 신호 | monitor에서 샘플링하는 인터페이스 |
| **상위 호출 패턴** | 고수준 task 조합 | virtual sequence 패턴 |
| **판정 기여** | 어떤 출력이 PASS/FAIL에 기여하는지 | scoreboard comparison, assertion |

### 0B. 테스트케이스별 분석 캐시

**파일 네이밍 규칙**: `.ai/analysis/tb_{env}_{test_name}.analysis.md`

- env prefix **항상 필수** (예: `tb_lgc_TOP015.analysis.md`, `tb_uvm_TOP015.analysis.md`)
- env prefix는 0A와 동일: `lgc` / `uvm` / `dsv` / `ams`
- 파일명만 보고 TB 환경을 즉시 유추 가능

각 테스트케이스를 한 번 분석하고, 이후 디버깅에서 재참조한다.

#### 작성 절차 (ncsim Legacy 기준)

**Step 1. 테스트 파일 스캔**

```bash
# DUT 신호 참조 추출
grep -oE 'top\.hw\.\S+' {test_file} | sort -u

# 사용 task 추출
grep -oE '(i2c|pcm|jtag)_[a-zA-Z_]+\(' {test_file} | sort -u

# include 파일 확인
grep "include" {test_file}
```

**Step 2. 시나리오 추출**

```bash
# run_test task에서 test_id + 주석 추출
sed -n '/task run_test/,/endtask/p' {test_file} | grep -E "test_id|//"
```

- `test_id = 5'dN` → 단계 구분점
- 바로 위/아래 주석 → 항목명
- `test_vXX_name()` 호출 → 하위 task 이름에서 항목 유추

**Step 3. 시퀀스 상세 파악**

```bash
# run_test 내용 전체 (처음 60~70줄)
sed -n '/task run_test/,/endtask/p' {test_file} | head -70
```

각 test_id 구간에서 패턴 식별:
- I2C 트랜잭션: `i2c_send_start → send_data → recv_data → send_stop`
- PCM 명령: `pcm_preamble → pcm_config/duration/param → pcm_nop_bitrate`
- 검증: `$display("PASS/FAIL")`, `err_cnt` 비교, waveform 검증 필요 여부

**Step 4. 공통 Dump 신호 추출 (regression 전체)**

```bash
# 모든 테스트에서 DUT 신호 합집합
for f in tb_tests/VENEZIA_TOP0*.v; do
  grep -oE 'top\.hw\.\S+' "$f"
done | sort -u
```

RTL 분석서의 FSM state / 주요 레지스터 신호를 추가하여 포괄 집합 구성.
결과는 `tb_lgc_regression_common_signals.analysis.md`에 저장 (env prefix는 해당 프로젝트 환경에 맞게).

#### 분석서 템플릿

```markdown
# tb_TOPXXX_{name} — Test Analysis

> **상태**: PASS / SKIP / ELAB FAIL
> **파일**: `tb_tests/VENEZIA_TOPXXX.v` (N lines)
> **include**: `i2c_model.inc`, `pcm_model.inc`

## 테스트 목적
한 줄 요약.

## 테스트 시나리오

| tid | 항목 | 시퀀스 |
|-----|------|--------|
| 0 | 초기화 | 구체적 task 호출 순서 |
| 1 | 항목명 | 시퀀스 상세 |

## DUT 신호 참조
- 계층 경로 목록

## 판별 방법
로그 / CSV / waveform 중 어떤 방식으로 판별하는지.

## 버그 이력 (해당 시)
과거 버그와 수정 내용, knowledge 참조.
```

#### 필수 포함 항목 (환경 무관)

| 항목 | 설명 | ncsim Legacy | UVM |
|------|------|-------------|-----|
| **테스트 목적** | 1줄 요약 | `// 파일 헤더 주석` | `class description` |
| **사용 공유 컴포넌트** | include/import 목록 | `include "*.inc"` | `import pkg::*`, agent 인스턴스 |
| **테스트 시나리오** | tid별 항목 + 시퀀스 표 | test_id별 task 호출 순서 | sequence body의 트랜잭션 순서 |
| **판별 방법** | PASS/FAIL 결정 방법 | `$display`, `err_cnt`, waveform | scoreboard match, assertion, `uvm_error` count |
| **판별 신호 + 기대값** | 검증 대상 | `rd_data == 0xAA` | RAL mirror vs actual, scoreboard queue |
| **DUT 내부 참조** | 직접 접근하는 DUT 신호 | `top.hw...r_regAddr` | backdoor access, HDL path assertion |
| **시뮬레이션 길이** | 대략적 종료 시간 | `#(1_000_000) $finish` | `phase_ready_to_end`, timeout |
| **버그 이력** | 과거 수정 내용 (해당 시) | knowledge 문서 참조 | — |

**UVM 테스트 분석서 예시 (발췌):**

```markdown
# tb_venezia_i2c_addr_mode_test — Test Analysis

## 테스트 목적
I2C addressing mode의 write/read-back 검증 (UVM RAL 경유)

## 사용 컴포넌트
- venezia_i2c_agent (driver: I2C master, monitor: SDA/SCL sampling)
- venezia_ral_model (CONFIG_DUR, SYNC_CFG 레지스터)
- venezia_scoreboard (write prediction vs read-back comparison)

## 테스트 시퀀스
1. RAL.CONFIG.addr_mode_en.write(1)
2. RAL.CONFIG_DUR.write(0xAA)
3. RAL.CONFIG_DUR.read() → mirror check
4. RAL.CONFIG_DUR.write(0x55)
5. RAL.CONFIG_DUR.read() → mirror check

## 판별 방법
- **Primary**: scoreboard match count == expected (UVM_ERROR == 0)
- **Secondary**: RAL mirror value == actual register value
- **Waveform 불필요**: scoreboard가 자동 비교

## 판별 신호
| 신호 | 용도 |
|------|------|
| `uvm_test_top...scoreboard.m_matches` | 매칭 횟수 |
| `uvm_report_server.get_severity_count(UVM_ERROR)` | 에러 카운트 |
```

### 0C. 캐시 관리 규칙

| 규칙 | 설명 |
|------|------|
| **작성 시점** | 해당 컴포넌트/테스트를 **처음** 디버깅할 때 작성 (lazy) |
| **갱신 시점** | 해당 파일이 수정되었을 때만 갱신 |
| **갱신 불필요** | RTL만 수정되고 TB 파일 미변경 시 — 캐시 그대로 사용 |
| **저장 위치** | `.ai/analysis/tb_*.analysis.md` (RTL 분석서와 동일 디렉토리) |
| **네이밍** | `tb_{env}_{component_or_test_name}.analysis.md` — `tb_` prefix로 RTL과 구분. env prefix(`lgc`/`uvm`/`dsv`/`ams`)는 **항상 필수** — 파일명만 보고 TB 환경 즉시 유추 |
| **참조 관계** | RTL 분석서 → DUT FSM/신호, TB 분석서 → 시퀀스/기대값/API |

### Phase 0 vs Phase 1 관계

```
Phase 0 (1회성, lazy)              Phase 1 (매 디버깅)
─────────────────                  ─────────────
공유 컴포넌트 분석 ───┐
  (i2c_agent 등)     │
테스트 분석 ──────────┤           ┌─ 캐시 있음 → 바로 참조 (재분석 불필요)
  (test_015 등)      ├──캐시──→  ┤
추가 테스트 분석 ─────┘           └─ 캐시 없음 → 이때 작성 후 캐시
```

**Lazy 분석**: 모든 컴포넌트/테스트를 미리 분석하지 않는다. 해당 항목을 처음 디버깅할 때 작성하고, 이후부터 재사용.

---

## Phase 1: 사전 분석

### 1A. 캐시 참조 + 테스트케이스 분석

```
1. .ai/analysis/tb_{env}_TOP0XX_*.analysis.md 존재 확인 (env = lgc/uvm/dsv/ams)
2. 있으면 → 캐시에서 판별 신호/기대값/시퀀스/task 참조 → Phase 2로
3. 없으면 → 테스트케이스 파일 읽기 + Phase 0 형식으로 분석서 작성 → 캐시
```

**분석서 부재 시(3번)**: `verilog-tb-analyst` agent(§Agent 위임 구조)를 호출해 Phase 0 형식으로 작성/캐시한 후 진행한다. 분석서 없이 Phase 4의 판별로 바로 넘어가지 않는다 — Phase 1B(RTL 분석서 부재 시 `verilog-rtl-analyst` 위임)와 동일 원칙. Agent를 찾을 수 없으면 `verilog-rtl-debugger` 또는 Claude가 직접 작성한다.

**분석 항목 (캐시에 없을 때만 수행):**

| 항목 | 확인 방법 | 예시 |
|------|----------|------|
| `$display` PASS/FAIL | grep `PASS\|FAIL\|err_cnt` | TOP015: V-18 explicit PASS/FAIL |
| 기대값 비교 | `if (rd_data == ...)` 패턴 | TOP016: `rd_data[0] === 1'b0` |
| Waveform 검증 필요 | `"verify in waveform"` 문구 | TOP013: V-02~V-05, TOP014: V-07~V-17 |
| 내부 신호 참조 | `top.hw.u_ext...` 계층 경로 | TOP016: `r_sync_xfr_en` direct probe |
| 로컬 helper task | task 정의 추출 | TOP016: `i2c_addr_mode_enable`, `i2c_fpga_read` |
| 공유 모델 task 사용 | include 확인 → `tb_{env}_{model}.analysis.md` 참조 | `i2c_model.inc` → `tb_lgc_i2c_model.analysis.md` |

**캐시 참조 예시 (TOP015):**

| Test ID | 판별 방법 | 판별 신호 | 기대값 | 사용 task |
|---------|----------|----------|--------|----------|
| V-18 | 로그 ($display) | rd_data | 0xAA, 0x55 | `i2c_fpga_write`, `i2c_fpga_read` |
| V-21 | 로그 ($display) | rd_data | 0xBB | `i2c_legacy_read_reg` |
| V-22 | 로그 ($display) | rd_data | 0x00 | `i2c_fpga_read` (bounds check) |

### 1B. RTL 분석서 참조

`.ai/analysis/{module}.analysis.md`에서:

- **FSM 전이 테이블**: 어떤 상태에서 어떤 조건으로 전이하는지
- **신호 의존성 맵**: 판별 신호를 생성하는 FSM/always 블록 식별
- **크로스 FSM 신호**: 다른 모듈에서 오는 신호의 타이밍

**분석서 부재/stale 시**: `verilog-rtl-debugger`(§Agent 위임 구조)가 `verilog-rtl-analyst` agent를 호출해 분석서를 먼저 작성/갱신한 후 진행한다. 분석서 없이 Phase 4C의 FSM 전이 대조로 바로 넘어가지 않는다.

**실전 예시 (TOP015 V-18 FAIL 분석):**

```
1. 판별 신호: rd_data (I2C read-back of CONFIG_DUR)
2. 분석서 참조: ext_i2cSerialInterface.analysis.md
   → r_regAddr 생성: CHK_ADR (STREAM_REG case)
   → 조건: startStopDetState == START_DET 내부
3. 의심 경로: CHK_ADR의 START_DET 게이팅 → STREAM_REG에서 regAddr 미설정?
4. 추가 확인 신호: r_regAddr, r_streamRwState, r_startStopDetState, r_loopState
```

### 1C. Dump Scope 확인

**현재 방식 (수동):**

```bash
# setup_rtl.tcl 확인
cat scripts/setup_rtl.tcl
# → "probe -create top -depth all" → 전체 scope, 추가 불필요
# → "probe -create top.hw -depth 3" → 부분 scope, 추가 필요할 수 있음
```

**v4.3 이후 (자동 — `dump_depth` 파라미터):**

```python
# sim_batch_run / sim_bridge_run의 dump_depth 파라미터로 제어
# mode_defaults가 sim_mode별 안전 기본값을 제공:
#   rtl → dump_depth="all", gate/ams → dump_depth="boundary"

sim_batch_run(test_name="TOP015", dump_depth="all")       # 전체 scope
sim_batch_run(test_name="TOP015", dump_depth="boundary")   # 경계 신호만

# 추가 신호가 필요하면 dump_signals 파라미터로 보충:
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_signals=["top.hw...r_regAddr", "top.hw...r_startStopDetState"])
```

### 1D. Dump 신호 집합 구성

`sim_mode`에 따라 dump 전략이 달라진다. RTL은 포괄 집합, Gate/AMS는 최소 집합을 사용한다.

#### 1D-0. 3-Tier Dump Strategy (sim_mode별 선택)

| Tier | sim_mode | 전략 | dump 대상 | SHM 비율 |
|------|----------|------|----------|----------|
| **Full** | `rtl` | 포괄 집합 (기존) | 내부 FSM + 레지스터 + 경계 | 100% (기준) |
| **Tier 1: Boundary** | `gate`, `ams_rtl`, `ams_gate` | DUT 입출력 경계만 | top I/O + 프로토콜 핸드셰이크 + 클럭/리셋 | ~5~10% |
| **Tier 2: Targeted** | `gate` (FAIL 후) | 실패 블록 내부 추가 | Tier 1 + 실패 인터페이스 내부 신호 | ~20~30% |
| **Tier 3: Windowed** | `ams_rtl`/`ams_gate` (100ms+) | 시간 구간 제한 | Tier 1 + dump_window_*_ms | ~2~5% |

**선택 기준:**

| 상황 | Tier | 이유 |
|------|------|------|
| RTL 기능 검증 / 디버깅 | Full | 재실행 비용 낮음, 전체 가시성 확보 |
| Gate SDF timing 검증 | Tier 1 → FAIL 시 Tier 2 | SHM 폭증 방지, 경계 신호로 실패 인터페이스 특정 |
| AMS 아날로그 연계 검증 | Tier 1 + Tier 3 | 초장시간 sim에서 관심 구간만 dump |
| Gate/AMS FAIL → 기능 버그 | RTL Full로 에스컬레이션 | 내부 원인은 빠른 RTL sim에서 분석 |

**에스컬레이션 플로우:**

```
Gate/AMS FAIL (Tier 1 boundary dump)
    │
    ├─ 경계 신호 CSV로 실패 인터페이스 특정
    │
    ├─ [타이밍 문제?]
    │   ├─ YES → Tier 2 재실행 (해당 블록 내부만 추가 dump)
    │   └─ NO (기능 버그) → RTL sim Full dump로 재현 (빠름)
    │
    └─ [AMS 아날로그 연계 문제?]
        ├─ YES → Tier 3 (시간 윈도우 제한) + 파라미터 sweep
        └─ NO → RTL로 에스컬레이션
```

**Boundary 신호 목록**: `.ai/analysis/boundary_signals.analysis.md`
**포괄 신호 목록 (RTL)**: `.ai/analysis/tb_{env}_regression_common_signals.analysis.md` (env = 프로젝트 환경, 예: `tb_lgc_regression_common_signals.analysis.md`)

#### AI agent의 dump_depth 결정 가이드

mode_defaults가 sim_mode별 안전 기본값(gate/ams→"boundary", rtl→"all")을 제공하지만,
AI agent는 `.ai/analysis/` 분석서를 참조하여 block-level에서는 "all"로 override할 수 있다.

**판단 기준:**

| 기준 | 소스 | dump_depth 판단 |
|------|------|----------------|
| DUT 계층 깊이 | RTL 분석서 — 모듈 계층 구조 | top → 하위 3 depth 이상 → "boundary" |
| 총 신호 수 | RTL 분석서 — 모듈별 신호 수 합산 | ~1000개 이하 → "all", 초과 → "boundary" |
| TB DUT 참조 비율 | TB 분석서 — DUT 신호 참조 목록 | 전체 대비 참조 비율이 높으면 → "all" |

**AI 워크플로우:**

```
1. .ai/analysis/{module}.analysis.md 읽기 → 모듈 계층/신호 수 파악
2. .ai/analysis/tb_{env}_TOP0XX.analysis.md 읽기 → TB가 참조하는 DUT 신호 수 파악
3. block-level (단일 모듈, 신호 수 적음) → dump_depth="all" 명시
4. chip-level (ext_d_top 이상, 신호 수 많음) → dump_depth="boundary" (기본값 사용)
5. 판단 불확실 시 → mode_defaults 기본값 그대로 사용 (안전)
```

> xcelium-mcp는 이 판단을 자동화하지 않는다. mode_defaults가 안전 기본값을 제공하고,
> AI agent가 분석서를 참조하여 상황에 따라 dump_depth를 명시적으로 override하는 구조이다.
> 상세 설계: `xcelium-mcp-v4.3-dump-strategy.design.md` §3.3

---

#### 1D-1. RTL 포괄 신호 집합 (기존)

**적용 조건**: Phase 5D regression을 `sim_regression`으로 실행할 때.
단일 테스트 디버깅 시에는 필요 없으며, regression 직전 1회 수행한다.

**목적**: regression의 `dump_signals`는 **특정 테스트의 판별 신호만이 아닌**, regression 내 모든 테스트에서 공유되는 포괄 신호 집합이어야 한다. 그래야 어느 테스트가 FAIL해도 재실행 없이 CSV 분석이 가능하다.

**결과 저장**: `.ai/analysis/tb_{env}_regression_common_signals.analysis.md` (env = 프로젝트 TB 환경)

#### 작성 절차

```
Step 1. 전체 테스트 DUT 신호 합집합 (ssh-mcp)
  → 모든 테스트 파일에서 top.hw.* 참조 추출
  
  for f in tb_tests/VENEZIA_TOP0*.v; do
    grep -oE 'top\.hw\.\S+' "$f"
  done | sed 's/[,;)"]//g' | sort -u

Step 2. RTL 분석서 핵심 신호 추출 (로컬 Read)
  → .ai/analysis/{module}.analysis.md
  → FSM state 레지스터 + 주요 내부 레지스터 목록

Step 3. TB 분석서 판별 신호 추출 (로컬 Read)
  → .ai/analysis/tb_{env}_TOP0XX.analysis.md
  → 각 테스트의 판별 신호 + DUT 신호 참조

Step 4. 합집합 구성
  → Step1(TB 직접 참조) ∪ Step2(RTL FSM/레지스터) ∪ Step3(판별 신호) = 포괄 집합
  → 중복 제거, 계층 경로 정규화

Step 5. 분류하여 문서화
  → tb_{env}_regression_common_signals.analysis.md에 5개 카테고리로 정리
```

#### 신호 분류 카테고리

| 카테고리 | 포함 기준 | 예시 |
|----------|----------|------|
| **TB probe points** | 2개 이상 테스트에서 직접 참조 | `test_id`, `r_rcvData`, `w_pcm_clk` |
| **I2C FSM** | SerialInterface FSM state + 주요 레지스터 | `r_loopState`, `r_regAddr`, `r_restart` |
| **I2C detection** | START/STOP detection 경로 | `r_startEdgeDet`, `r_sclDeb`, `io_sda` |
| **I2C register** | 버그 이력 있는 레지스터 | `o_config_dur`, `o_addressing_mode_en` |
| **PCM/ASK** | PCM register + ASK encoder 상태 | `o_phase_duration`, `r_sync_xfr_en`, `o_fwd_fifo_cnt` |

#### 좋은 예 vs 나쁜 예

```python
# 나쁜 예 — 특정 테스트 실패 신호만:
dump_signals = ["top.hw.u_ext...r_regAddr"]   # TOP015 분석 중 추가한 신호만
# → TOP013이 o_phase_duration 필요 시 regression 재실행 필요

# 좋은 예 — 포괄 집합 (tb_{env}_regression_common_signals.analysis.md 참조):
dump_signals = REGRESSION_DUMP_SIGNALS  # 24개 — 모든 테스트 커버
```

#### 신호 소스별 우선순위

| 소스 | 포함 기준 | 개수 (실전) |
|------|----------|------------|
| TB 직접 참조 | 2개 이상 테스트에서 사용 | 5 |
| RTL 분석서 FSM | 모든 FSM state + 주요 레지스터 | 8 |
| RTL 분석서 물리 | START/STOP detection 경로 | 5 |
| RTL 분석서 레지스터 | 버그 이력 있는 레지스터 | 2 |
| 과거 디버깅 이력 | 이전 버그에서 확인이 필요했던 신호 | 4 |

#### 현재 setup_tcl 참고

현재 `setup_rtl_batch.tcl`은 `probe -create top -depth all`로 **전체 신호를 dump**하므로,
이 포괄 집합은 dump 범위 제한이 아닌 **CSV 추출 시 관심 신호**로 활용한다.
부분 dump 환경(`-depth 3` 등)에서는 이 집합을 `dump_signals` 파라미터로 전달하여 누락을 방지한다.

**스킬 파일 계획**: `/regression-signals` skill로 Step 1~4를 자동화할 예정 (v3 구현 시 작성).
현재는 Claude가 위 절차를 수동으로 수행한다.

---

#### 1D-2. Gate/AMS Tier 1 — Boundary-Only Dump

**적용 조건**: Gate-level SDF timing 검증, AMS 아날로그 연계 검증.
**목적**: DUT 입출력 경계 신호만 dump하여 SHM 크기를 RTL 대비 1/10~1/50로 줄인다.

**Boundary 신호 구성 (28개):**

| 인터페이스 | 신호 | 방향 | 개수 |
|-----------|------|------|------|
| **Clock/Reset** | `i_mainClk`, `i_rst_n` | in | 2 |
| **I2C** | `i_scl`, `io_sda` (또는 `i_sdaIn`+`o_sdaOut`) | in/inout/out | 3~4 |
| **PCM RX** | `i_pcmIn`, `i_pcmSync` | in | 2 |
| **COLA TX** | `o_askData`, `o_askDataInv`, `o_askRefClk`, `o_refClk`, `o_refClkInv`, `o_btCoilShort` | out | 6 |
| **BackTel RX** | `i_backTel_p`, `i_backTel_n` | in | 2 |
| **BackTel PWR** | `o_backTel_pwr_en` | out | 1 |
| **LED** (선택) | `i_led_ctrl_{r,g,b}`, `o_led_{r,g,b}` | in/out | 6 |
| **Misc Control** | `i_earpiece_det_n`, `i_rmClkNum[1:0]`, `i_deep_slp_en`, `i_dyn_slp_en` | in | 4 |
| **Status** | `o_sync_req`, `o_stim_trig`, `o_serial_tp_out` | out | 3 |

**사용법:**

```python
# Gate timing 검증 — Tier 1 boundary only
sim_batch_run(
    test_name="TOP015",
    dump_signals=BOUNDARY_SIGNALS,     # 28개 — boundary_signals.analysis.md 참조
    sim_mode="gate"
)
```

**FAIL 시 판별**: 경계 신호의 타이밍/값만으로 "어느 인터페이스에서 실패했는가" 특정.
내부 원인 분석은 Tier 2 또는 RTL Full로 에스컬레이션.

---

#### 1D-3. Gate Tier 2 — Targeted-Deep (FAIL 후)

**적용 조건**: Tier 1에서 실패 인터페이스가 특정되었으나, 경계 신호만으로 원인 불명확할 때.
**목적**: 실패 블록 내부 신호만 추가 dump. 나머지 블록은 Tier 1 유지.

```python
# 예: I2C 인터페이스 FAIL → I2C 내부만 추가
TIER2_I2C_SIGNALS = BOUNDARY_SIGNALS + [
    "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_loopState",
    "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_regAddr",
    "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_sclDeb",
    "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_sdaDeb",
    # ... 해당 블록의 critical path 신호 (STA 기준)
]

sim_batch_run(
    test_name="TOP015",
    dump_signals=TIER2_I2C_SIGNALS,    # boundary + 실패 블록 내부
    sim_mode="gate"
)
```

**Tier 2 블록별 추가 신호 매핑:**

| 실패 인터페이스 | 추가 dump 대상 | 추가 신호 수 |
|----------------|---------------|-------------|
| I2C | SerialInterface FSM + Slave debounce | ~10 |
| PCM/COLA | pcmInterface FSM + askEncoder state | ~8 |
| BackTel | backTelReceiver FSM + token state | ~6 |
| LED | led_ctrl 내부 PWM 레지스터 | ~4 |

---

#### 1D-4. AMS Tier 3 — Time-Windowed Dump

**적용 조건**: AMS 시뮬레이션 100ms+ 에서 전체 dump가 불가능할 때.
**목적**: `probe(action="enable"/"disable")`로 관심 시간 구간만 dump. SHM을 전체 대비 1/20로 줄임.

```python
# AMS 장시간 시뮬레이션 — 시간 윈도우 dump
sim_batch_run(
    test_name="TOP015",
    dump_signals=BOUNDARY_SIGNALS,
    sim_mode="ams_rtl",         # or "ams_gate"
    dump_window_start_ms=50,    # 관심 구간 시작 (ms)
    dump_window_end_ms=55       # 관심 구간 끝 (ms)
)

# 내부 동작: sim_batch_run이 setup_tcl에 probe on/off + run sequence를
# 자동 주입 (v4.3 _inject_dump_window):
#   probe -disable; run 50ms; probe -enable; run 5ms; probe -disable; run
```

**수동 제어 (Bridge mode):**

```python
sim_bridge_run(test_name="TOP015", dump_depth="boundary")
connect_simulator()

probe(action="disable")                 # settling 구간 — dump 없음
sim_run(duration="50ms")

probe(action="enable")                  # 관심 구간 — dump
sim_run(duration="5ms")

probe(action="disable")                 # 나머지 — dump 없음
sim_run(duration="45ms")

sim_disconnect(action="shutdown")       # SHM 보존 안전 종료
```

**dump_window 사전 결정 방법:**
1. RTL sim에서 전체 dump → bisect로 이상 시점 특정 (Phase 4A)
2. 해당 시간 ±10% 마진을 dump_window로 설정
3. AMS sim에서 Tier 3 실행

---

#### 1D-5. v5.2 `dump_scopes` — Block-level Dump (Tier 2를 대체하는 자동/수동 조합) [2026-07-03 추가]

**신규 발견(2026-07-03 소스 대조)**: `xcelium-mcp-v5.2-hierarchical-dump` feature(2026-04-09 Plan → 2026-07-02 Report, 94% match rate, 완료됨)가 `sim_batch_run`/`sim_regression`에 `dump_scopes`/`use_dump_history` 파라미터를, `sim_bridge_run`/`sim_discover`에 `auto_boundaries`/`boundary_depth` 파라미터를 추가했다. 이 문서(Phase 1D)는 v5.2 이전(2026-04-09까지)에 작성되어 이 내용이 전혀 반영되어 있지 않았다 — §1D-3(Tier 2 Targeted-Deep)의 수동 신호 나열 방식을 사실상 대체·일반화하는 기능이므로 추가한다.

**핵심 개념**: `dump_depth="boundary"`(top I/O만) vs `"all"`(전체) 사이의 **블록 단위 중간 해상도**를 `dump_scopes` dict로 지정한다.

```python
# 예: I2C 블록만 전체 dump, 나머지는 boundary만 (Tier 2의 수동 TIER2_I2C_SIGNALS 나열을 대체)
sim_batch_run(
    test_name="TOP015",
    dump_depth="boundary",
    dump_scopes={"top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": "all"},  # glob 지원, value: all/boundary/skip
    sim_mode="gate",
)

# 재실행 시 이전 dump_scopes를 재사용 (자동 갱신되는 dump_history 참조)
sim_batch_run(test_name="TOP015", use_dump_history=True)
```

**자동 감지(선택)**: 블록 경계를 수동으로 나열하지 않고 자동 탐색하려면:
- **Flow A** (Bridge, 런타임): `sim_bridge_run(test_name="TOP015", auto_boundaries=True)` — SimVision `scope -describe`로 탐색 후 config에 저장.
- **Flow B** (Batch, lazy): `sim_discover(boundary_depth=3)` — Yosys JSON 기반 netlist 분석으로 지연 탐색.

**Tier 2(§1D-3)와의 관계**: Tier 2의 수동 `TIER2_I2C_SIGNALS` 신호 나열 방식은 여전히 유효하지만(세밀한 개별 신호 제어가 필요할 때), 블록 단위로 충분하면 `dump_scopes`가 더 간결하고 자동 감지까지 지원한다. 신규 작업에서는 `dump_scopes`를 우선 검토하고, 블록 내부 특정 신호까지 필요하면 Tier 2로 세분화한다.

**상세 설계/구현**: `docs/01-plan/features/xcelium-mcp-v5.2-hierarchical-dump.plan.md`, `docs/02-design/features/xcelium-mcp-v5.2-hierarchical-dump.design.md`, `docs/04-report/xcelium-mcp-v5.2-hierarchical-dump.report.md`가 정본.

---

#### 1D 요약: sim_mode별 dump_signals 사용법

```python
# RTL — Full 포괄 집합 (기존)
sim_batch_run(test_name="TOP015", dump_signals=REGRESSION_DUMP_SIGNALS)
sim_regression(test_list=["TOP012", ...], dump_signals=REGRESSION_DUMP_SIGNALS)

# Gate — Tier 1 boundary
sim_batch_run(test_name="TOP015", dump_signals=BOUNDARY_SIGNALS, sim_mode="gate")

# Gate FAIL → Tier 2 targeted
sim_batch_run(test_name="TOP015", dump_signals=TIER2_I2C_SIGNALS, sim_mode="gate")

# AMS — Tier 1 + Tier 3 windowed (ams_rtl or ams_gate)
sim_batch_run(test_name="TOP015", dump_signals=BOUNDARY_SIGNALS,
              sim_mode="ams_rtl", dump_window_start_ms=50, dump_window_end_ms=55)

# Gate FAIL → v5.2 dump_scopes (§1D-5, Tier 2의 블록 단위 대안)
sim_batch_run(test_name="TOP015", dump_depth="boundary",
              dump_scopes={"top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave": "all"}, sim_mode="gate")
```

---

## Phase 2: 시뮬레이션 실행

### 2A. Batch Mode (기본 권장)

시뮬레이터와 상호작용 없이 실행 → dump 확보. 대부분의 디버깅에 충분.
**모든 실행은 xcelium-mcp 툴을 사용한다.**

```python
# 단일 테스트 — xcelium-mcp sim_batch_run
sim_batch_run(test_name="TOP015", dump_signals=["r_regAddr", ...])

# 다중 테스트 (regression) — xcelium-mcp sim_regression
sim_regression(test_list=["TOP012", "TOP013", ...])
```

### 2B. Bridge Mode (특수 상황)

interactive probing이 필요한 경우에만 사용:

- 버그 조건이 매우 정확히 알려져 있어 `watch(action="set")`로 1회 포착 가능
- 실행 중 신호값을 실시간으로 확인해야 할 때
- probe enable/disable로 SHM 크기를 제어해야 할 때

```python
# Bridge mode 시작 — xcelium-mcp 툴 사용
sim_bridge_run(test_name="TEST_NAME")              # 컴파일 + 실행 + 브릿지 자동 연결
# 또는 기존 시뮬레이터에 수동 연결:
# connect_simulator(host="localhost", port=0)       # port=0: ready file에서 자동 감지

# Interactive 작업 (xcelium-mcp 툴)
watch(action="set", signal="...", op="==", value="...")
sim_run(duration="20ms")
inspect_signal(action="value", signals=["..."])

# 종료 (SHM 보존)
sim_disconnect(action="shutdown")
```

#### 실행 중 시뮬레이션 중단 — sentinel 파일 (F-106/F-107, 검증됨)

`sim_run`은 내부적으로 100µs 단위 chunked 루프로 실행된다 (F-106). 각 chunk 경계에서 sentinel 파일을 감지하면 즉시 정지하고 `status=stopped`를 반환한다. **xmsim 프로세스가 생존**하며 bridge도 intact — 이어서 `sim_status`, `inspect_signal` 등을 사용할 수 있다.

```python
# 1. sim_run과 함께 ssh_bg_run으로 sentinel 생성 (병렬 실행)
#    sentinel 경로: /tmp/xcelium_mcp_{uid}/stop_{port}
#    uid는 서버 사용자 UID (보통 1001), port는 bridge port (보통 9876)
ssh_bg_run(f"sleep 3 && touch /tmp/xcelium_mcp_1001/stop_9876")   # 3초 후 중단 신호
sim_run(duration="10ms", timeout=30)
# → "Simulation stopped by user. Current position: TIME: 1600 US + 0 (requested: 10000000ns)"

# 2. 중단 후 bridge 정상 동작 확인
sim_status()          # TIME: 1600 US — xmsim 생존, bridge intact
inspect_signal(...)   # 정상 동작
```

**왜 ssh_bg_run인가**: ssh-mcp는 xcelium-mcp와 완전히 독립된 채널이므로 sim_run 블로킹 중에도 호출 가능. `sim_stop` MCP tool은 같은 MCP 서버 내에서 sim_run과 병렬 호출 시 서버 큐잉으로 sim_run 완료 후 실행되어 효과 없음 → F-108에서 제거됨.

**sentinel 경로 확인**: `sim_bridge_run` result의 `log:` 경로에서 uid 추출 가능. 예: `log: /tmp/xcelium_mcp_1001/...` → uid=1001.

**chunk=0 opt-out**: `sim_run(duration="10ms", chunk=0)` — 기존 1-shot 모드. sentinel 중단 불가.

#### ⚠️ SIGINT — 파괴적 중단 (xmsim 종료)

SIGINT는 xmsim 프로세스 자체를 종료시킨다. waveform/checkpoint가 보존되지 않을 수 있다. sentinel 방식이 불가한 경우(레거시 1-shot 모드 등)의 최후 수단으로만 사용.

```python
ssh_run(f"kill -s INT {xmsim_pid}")   # xmsim graceful shutdown → 프로세스 종료
# 이후 sim_bridge_run으로 재시작 필요
```

**⚠️ SIGINT 실제 동작 (검증됨)**: Xcelium은 run 중 SIGINT를 deferred 처리한다. run이 완료된 후 graceful shutdown으로 처리되어 **xmsim 프로세스가 종료**된다. "run만 중단하고 xmsim 생존" 동작은 재현되지 않음.

#### sim_run timeout 후 브리지 재연결 — F-104/F-105

`sim_run`에 `timeout` 인수를 지정하면 Python MCP가 시간 초과 시 `_force_close()`를 호출한다. xmsim은 run을 계속 실행하며, 이 과정에서 브리지 소켓이 stale 상태가 된다.

**Timeout 발생 시 내부 동작:**

```
[Python MCP]                        [xmsim TCL]
sim_run(timeout=N) ─────────────→   run $duration (이벤트 루프 블로킹)
N초 경과 → TimeoutError
_force_close() → FIN 전송 ──────→   (run 중 — FIN을 커널이 수신하나 deferred)
Python 측 채널 닫힘                  run 완료 → send_ok $channel "..." 시도
                                              ↳ Python 이미 닫혀 → RST 수신
                                    disconnect 미실행 (RST 경로 — on_readable 미거침)
                                    client_channel = stale(dead) channel 유지
                                    vwait (idle 복귀)

connect_simulator() ────────────→   accept 콜백 실행
                                    F-105 v5: client_channel ne "" 감지
                                    → catch {close $client_channel}  ← stale 정리
                                    → client_channel = new channel
                    ←── pong ─────  __PING__ 처리
재연결 성공 ✅
```

**재연결 절차:**

```python
# 1. sim_run timeout 발생 — ERROR 반환, xmsim은 run 계속 중
sim_run(duration="999ms", timeout=30)
# → ERROR: sim_run exceeded 30.0s

# 2. xmsim run 완료를 기다린 후 connect_simulator 호출
#    F-105 v5가 stale client_channel을 자동 감지·정리
connect_simulator()
# → Connected: xmsim:9876 (ping=pong)   ← 재연결 성공
# ncsim.log에 "closing previous client_channel (reconnect)" 기록됨

# 3. 정상 사용 재개
sim_status()          # 현재 시뮬레이션 시간 확인
sim_run(duration="1ms")  # 이어서 실행 가능
```

**중요 조건:**

- `connect_simulator`는 **run이 완료된 후** 호출해야 한다. run 진행 중에는 TCL 이벤트 루프가 블로킹되어 accept 콜백이 실행되지 않으므로 PING timeout으로 실패한다.
- run 완료 대기 방법: `netstat | grep 9876`에서 CLOSE_WAIT가 사라지는 시점, 또는 `tail -f ncsim.log`에서 시뮬레이션 타임스탬프 출력이 멈추는 시점.
- SIGINT로는 해결되지 않는다 — run 중 SIGINT는 run 완료 후 xmsim 종료로 이어진다.

### 2A vs 2B 선택 기준

| 상황 | 선택 | 이유 |
|------|------|------|
| 첫 시뮬레이션 (버그 위치 모름) | **Batch** | 전체 dump 확보 후 오프라인 분석 |
| Regression (다중 테스트) | **Batch** | 순차 실행, dump 자동 관리 |
| 추가 신호 필요 (checkpoint 있음) | **Batch-restore** | sim_batch_run(from_checkpoint=...) — GUI 불필요 |
| 추가 신호 필요 (checkpoint 없음) | **Batch full 재실행** | dump_depth/dump_signals 조정 → 전체 재실행 |
| 수정 가설 검증 / 실시간 조작 | Bridge | deposit_signal / inspect_signal 필요 시만 |

---

## Phase 3: 1차 판별 — 로그 기반

시뮬레이션 완료 후 **가장 먼저** 로그를 확인한다.

```bash
grep -E "PASS|FAIL|Errors:|COMPLETE" logs/ncsim_${TEST_NAME}.log
```

**판별 매트릭스:**

| 로그 내용 | 판정 | 다음 단계 |
|-----------|------|----------|
| `Errors: 0` + 모든 PASS | **PASS** | Phase 5E (보고서 갱신) |
| `FAIL` 또는 `Errors: N (N>0)` | **FAIL** | Phase 4 (waveform 분석) |
| `COMPLETE` only, PASS/FAIL 없음 | **불확정** | Phase 4 (waveform 분석) |
| "verify in waveform" | **불확정** | Phase 4 (waveform 분석) |
| 시뮬레이션 hang / timeout | **FAIL** | Phase 2B (bridge로 재실행) |

**실전 예시:**

```
# TOP015 (수정 전): 로그에 FAIL 6개 → 즉시 Phase 4
[V-18] FAIL: CONFIG_DUR read-back = 0x00 (expect 0xaa)

# TOP012: 로그에 PASS/FAIL 없음 → Phase 4 필요
(no output)

# TOP016: 로그에 Errors: 0 → PASS 확정
======== [TOP016] sync_xfr_en Gating test COMPLETE. Errors: 0 ========
```

---

## Phase 4: 2차 판별 — Waveform CSV 분석

로그로 판별할 수 없거나, FAIL의 근본 원인을 분석할 때 사용.

### 4A. bisect → CSV 추출 (1회) → In-memory 분석

**Step 1. bisect_signal로 이상 시점 1차 특정:**

넓은 범위(전체 시뮬레이션)에서 binary search로 이상 시점을 자동 탐색한다. 수동 CSV 스캔 불필요.

```
bisect_signal(signal="top.hw...r_streamRwState", op="eq", value="3",
              start_ns=0, end_ns=END_NS, shm_path="dump/ci_top_${TEST}.shm")
```

**Step 2. bisect가 좁혀준 구간에서 CSV 1회 추출:**

Phase 1A에서 정한 판별 신호를, bisect가 특정한 구간 기준으로 **1회 추출**한다.

```bash
# simvisdbutil 1회 추출 — bisect가 좁혀준 구간의 판별 신호
simvisdbutil dump/ci_top_${TEST}.shm/ci_top.trn \
    -csv -output /tmp/${TEST}_check.csv -overwrite -missing \
    -range START:ENDns \
    -sig top.hw...r_regAddr \
    -sig top.hw...r_streamRwState \
    -sig top.hw...r_loopState \
    -sig top.hw...r_startStopDetState
```

**Step 3. In-memory 분석 (추출된 CSV 재사용):**

```bash
# 값이 변하는 시점만 필터 (simvisdbutil 재호출 없음)
awk -F',' 'NR>1 && $2+0 != prev {print; prev=$2+0}' /tmp/check.csv

# 특정 FSM 상태(CHK_ADR=2, INC_ADR=3)만 필터
awk -F',' 'NR>1 && ($4+0==2 || $4+0==3)' /tmp/check.csv

# 특정 시간 구간만 필터 (좁은 범위 재추출 대신)
awk -F',' 'NR>1 && $1+0 >= 8300000000 && $1+0 <= 8500000000' /tmp/check.csv

# STREAM_READ(2) 시점의 read 결과 확인
awk -F',' 'NR>1 && $3+0==2 && $2+0 != prev {print; prev=$2+0}' /tmp/check.csv
```

**원칙: 같은 CSV에서 awk/grep으로 다양한 관점의 필터링. simvisdbutil 재호출 불필요.**

### 4B. 추가 신호 보충 추출 (필요 시만)

1차 CSV의 신호만으로 원인 특정이 안 될 때, **같은 시간 범위**에서 추가 신호만 재추출한다.
시간 범위를 좁히는 것이 아님 — 이미 메모리에 있는 CSV에서 시간 필터링하면 되므로.

```bash
# 추가 신호만 재추출 (같은 범위, 다른 신호)
simvisdbutil dump/... -csv -output /tmp/detail.csv -overwrite \
    -range START:ENDns \
    -sig r_rxData -sig r_dataState -sig r_restart  # 1차에 없던 신호
```

이후 1차 CSV와 2차 CSV를 시간 기준으로 조인하여 분석.

### 4C. 근본 원인 특정 — FSM 전이 대조

CSV 데이터를 RTL 분석서의 FSM 전이 테이블과 대조한다. **이 대조 작업은 `verilog-rtl-debugger` agent(§Agent 위임 구조)가 직접 소유·수행한다** — 기존 5개 verilog-rtl-* agent 중 이 판단을 하는 agent가 없었다.

**실전 예시 (TOP015 V-18):**

```
CSV 관찰:
  t=8318143ns: loopState=2(CHK_ADR), streamRwState=1(STREAM_REG),
               startStopDetState=0(NULL_DET), dataState=2(BIT_ACK)

분석서 참조 (ext_i2cSerialInterface.analysis.md §C-1):
  CHK_ADR | BIT_ACK + START_DET + STREAM_REG → c_regAddr=rxData[7:0]

대조:
  startStopDetState=NULL_DET (START_DET 아님!)
  → CHK_ADR의 START_DET 게이트 조건 FALSE
  → STREAM_REG case 미진입
  → regAddr 미설정 ← 근본 원인!
```

### 4D. Interactive Probing (보완)

CSV 분석만으로 부족한 경우 — 예: 신호가 dump에 없거나, 실시간 조건 변경이 필요할 때.

```python
connect_simulator()
sim_run(duration="8.3ms")  # 이상 시점 직전까지
inspect_signal(action="value", signals=["top.hw...c_regAddr", "top.hw...c_streamRwState"])
# c_ (조합) 신호는 dump에 없을 수 있음 → interactive로만 확인 가능
```

### 4E. AI 자율 디버깅 + Human-in-the-Loop (병렬 프로세스)

**이 절 전체가 `verilog-rtl-debugger` agent(§Agent 위임 구조)의 핵심 책임 범위다** — bisect/CSV/RTL 참조 자율 루프는 MCP tool 접근 권한이 있는 이 agent만 수행할 수 있다.

정적 RTL 분석으로 순환하지 말고, **프로빙 도구로 값 변화 시점을 먼저 특정**한 후 원인을 추적한다.

AI 디버깅과 사람의 시각적 확인은 **병렬로 수행**된다:
- AI는 bisect/CSV/RTL 참조 루프를 자율 반복하여 원인을 좁혀감
- 사람은 언제든 "현재 상태 보여줘"로 SimVision에 최신 candidate를 확인

이것이 가능한 이유: SimVision은 SHM을 읽기 전용으로 열고, AI의 bisect/CSV는 simvisdbutil로 별도 채널에서 동작하므로 충돌이 없다.

```
┌─────────────────────────────────────────────────────────────────┐
│ AI 자율 디버깅 루프 (메인 프로세스)                                 │
│                                                                   │
│   bisect_signal(shm_path=...) — SHM CSV 기반 이상 시점 특정       │
│       → simvisdbutil CSV 추출 (해당 구간 multi-signal)             │
│           → in-memory 분석 (awk/grep: 값 변화, 조건 필터)          │
│               → RTL 분석서 참조 (FSM 전이표 대조)                   │
│                   → 가설 수립 → bisect로 재검증                    │
│                       → (반복 또는 원인 확정)                      │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│ Human-in-the-Loop (병렬, 요청 시)                                 │
│                                                                   │
│   사람: "지금 상태 보여줘"                                         │
│       → AI: SimVision에 현재 candidate 구간 표시                   │
│           simvision_connect(action="start")                       │
│           → waveform(action="add", signals=[현재 분석 신호])       │
│           → waveform(action="zoom", start_time=..., end_time=...) │
│           → waveform_screenshot                                   │
│       → 사람이 waveform 확인 후 추가 지시 가능                     │
│       → AI는 디버깅 루프 계속 진행                                 │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

**실전 예시 (TOP012 repeated START 디버깅):**

```
[AI 자율 루프]
Step 1. bisect_signal(signal="...r_startEdgeDet", op="eq", value="1",
            shm_path="dump/ci_top_TOP012.shm", start_ns=11000000, end_ns=15500000)
    → 결과: test_id=5 구간 내 매칭 없음! → repeated START 미감지 확정

Step 2. bisect_signal(signal="...io_sda", op="change", value="",
            shm_path="...", start_ns=14000000, end_ns=15000000)
    → 결과: 14532213ns에서 SDA=0

Step 3. simvisdbutil CSV 추출: SDA/SCL/sclDeb/sdaDeb (14.2ms ~ 14.6ms)
    → in-memory 분석: SDA falling 시점에 SCL 이미 LOW → START 조건 불성립

Step 4. RTL 참조: ext_i2cSlave.v START detection 조건
    → r_sclDeb==1 && SDA falling 필요, debounce DEB_I2C_LEN=5

Step 5. simvisdbutil CSV 추출: r_scl (TB drive) vs i_scl (DUT input)
    → in-memory 분석: r_scl=1'bz 후 33μs만에 SCL drops — 같은 timestep race

Step 6. knowledge 문서 참조 (.ai/knowledge/i2c-repeated-start-race.md)
    → 수정 패턴: @(posedge w_i2c_clk) 1줄 삽입
    → bisect_signal로 재검증: r_startEdgeDet=1 확인 → rcvData 정상값

[Human-in-the-Loop — 사람 요청 시]
"simvision으로 확인하자" → AI가 Step 3 시점의 신호를 SimVision에 표시
    → simvision_connect(action="start", test_name="TOP012")
    → waveform(action="add", signals=[...], group_name="Repeated START Physical")
    → waveform(action="zoom", start_time="14200000ns", end_time="14600000ns")
    → waveform_screenshot → 사람에게 전달
```

**핵심 원칙:**

1. **bisect 먼저** — 넓은 범위에서 이상 시점을 자동 탐색 (수동 CSV 스캔 불필요)
2. **in-memory 분석** — bisect가 좁혀준 구간에서 CSV + awk/grep으로 multi-signal 타이밍 분석
3. **RTL 참조** — FSM 전이표와 CSV 데이터를 대조하여 가설 수립/검증
4. **SimVision은 AI-사람 협업 채널** — AI 분석과 독립적, 사람이 요청할 때 최신 상태를 시각화

### Phase 4 도구 선택 가이드

| 상황 | 도구 | 이유 |
|------|------|------|
| 값 변화 시점 자동 탐색 | **bisect_signal** (shm_path= Mode A) | SHM CSV 기반 binary search, 넓은 범위에서 빠르게 특정 |
| 신호 값 시간 변화 추적 | **simvisdbutil** (ssh_run) | bisect가 좁혀준 구간에서 multi-signal CSV 상세 분석 |
| 시각적 확인/공유 | **simvision_connect** → **waveform** → **waveform_screenshot** | 팀 공유, 리포트 첨부 |
| 특정 시점 조합 신호 확인 | **inspect_signal** (action="value") | c_ 신호는 dump에 없음 |
| 조건부 stop | **watch** (action="set") | 정확한 clock edge 포착 |

---

## Phase 5: 수정 + 검증

### 5A. RTL 수정 (로컬)

1. `verilog-rtl-debugger`가 확정한 근본 원인(Phase 4C)을 `verilog-rtl-coder` agent에 전달해 수정 코드 작성 위임 (verilog-rtl skill 규칙 자동 적용)
2. 분석서 참조: `.ai/analysis/{module}.analysis.md`
3. 수정 코드 작성 — 사이클 주석 포함
4. Bit-width safety 검증
5. 수정 규모가 신규 FSM/모듈/case-arm 등 아키텍처 경계를 건드리면 `verilog-rtl-architect-advisor`에 먼저 에스컬레이션
6. 커밋 전 `verilog-rtl-reviewer`로 AI-failure 시그니처(T1~T9) 리뷰

### 5B. Verilator Lint

```bash
C:/msys64/usr/bin/bash.exe -lc "verilator --lint-only -Wall --top-module <top> <files>"
```

기존 warning만 확인, 새 에러 없음을 검증.

### 5C. cloud0 반영 + 재시뮬레이션

```bash
# 수정 파일 cloud0에 반영 (sed 또는 file_write)
# Phase 2A 재진입: xcelium-mcp batch mode로 수정 확인
sim_batch_run(test_name="FAILING_TEST", dump_signals=[...])
```

~~**v3: RTL/TB 수정 후 compile hash 변경 → L1/L2 checkpoint 자동 무효화 — 폐기됨.**~~

### 5D. Regression

수정이 다른 테스트를 깨뜨리지 않는지 전체 regression 실행.

**xcelium-mcp `sim_regression` + 포괄 신호 집합:**

```
1. Phase 1D 워크플로우 수행 → dump_signals 포괄 집합 구성
2. sim_regression(
       test_list=["TOP012", ..., "TOP016"],
       dump_signals=[포괄 집합],   # ← 1D에서 구성
   )
3. 결과 확인 — FAIL 테스트는 재실행 없이 CSV 분석으로 진행 (Phase 4 재진입)
```

Phase 3 재진입하여 각 테스트의 PASS/FAIL 판별.

### 5D-2. Regression 결과 검증 및 리포팅

Regression 실행 후 **로그 판별 + CSV waveform 검증**의 2단계로 결과를 확인하고 리포트를 작성한다.

**Step 1. 로그 판별 (1차)**

```
sim_regression 반환값에서:
  - "Simulation complete via $finish" → 정상 종료
  - "[V-XX] PASS/FAIL" → 명시적 체커 결과
  - "Errors: N" → 에러 카운트
  - *E,CUVMUR / *F,NOSNAP → 컴파일/elaboration 실패

→ 분류: PASS / SKIP (timeout·미구현) / ELAB FAIL (구버전 TB)
```

**Step 2. CSV Waveform 검증 (2차 — PASS 테스트만)**

```
simvisdbutil {shm_path} -csv -output /tmp/{test}_check.csv -overwrite \
    -sig test_id -sig r_rcvData ...
    ↓
ssh_run + awk: test_id별 값 변화 시점만 필터
    ↓
각 test_id가 순차 진행되었는지 + 핵심 신호값이 정상인지 확인
```

awk 패턴 (표준):
```bash
awk -F',' 'NR==1{next} BEGIN{prev=""} {key=$2","$3; if(key!=prev){
  printf "  tid=%-3s  signal=%-6s  time=%s\n",$1,$2,$3; prev=key
}}' CSV_FILE
```

이상 시점이 있으면 `bisect_signal` → simvisdbutil CSV → in-memory 분석 루프로 정밀 분석 (Phase 4E 참조).

**Step 3. 리포트 출력 형식**

3가지 표로 구성:

(1) 실행 결과 요약표 (전체):

| Test | 상태 | $finish | 사유 |
|------|------|---------|------|
| TOPXXX | PASS / SKIP / ELAB FAIL | 시간 또는 — | 간단한 설명 |

(2) 테스트별 단계 상세표 (PASS 테스트만, 테스트코드 주석에서 항목명 추출):

| tid | 테스트 항목 | CSV 검증 | 판정 |
|-----|------------|----------|------|
| 0 | 초기화 | 정상 시작 | ✅ |
| 1 | 항목명 | rcvData=값 또는 전이 정상 | ✅ |

(3) SKIP/FAIL 사유 상세:

| 분류 | 테스트 | 사유 |
|------|--------|------|
| 장시간 | TOP000 | timeout 내 미완료 |
| ELAB FAIL | TOP001 | 구버전 TB 신호 불일치 |
| 미구현 | TOP006 | 빈 파일 |

**Step 4. md 파일 저장**

```
docs/04-report/features/regression-{scope}.report.md
```

### 5E. 문서 갱신

| 문서 | 갱신 내용 |
|------|----------|
| `.ai/analysis/{module}.analysis.md` | FSM 전이 테이블, 신호 의존성 업데이트 |
| `docs/04-report/{feature}.report.md` | 버그 설명, 수정 내용, regression 결과 |
| `.ai/knowledge/` | 재발 방지용 knowledge 문서 (필요 시) |

---

## 반복 패턴: FAIL → 분석 → 수정 → PASS

실전에서 Phase 2~5는 여러 번 반복된다.

### sync-xfr-extension 실전 히스토리

| 반복 | 증상 | Phase 4 분석 결과 | 수정 |
|:----:|------|-----------------|------|
| 1 | TOP016 V-26 0xFF | stale START_DET (clearStartStopDet 누락) | INC_ADR STREAM_DEV→STREAM_REG에 clearStartStopDet 추가 |
| 2 | TOP015 V-18 6 errors | CHK_ADR STREAM_REG가 START_DET 게이트 내부 | CHK_ADR else-if 분기 추가 (NULL_DET 경로) |
| 3 | TOP012 test_id=5 0xFF | TB SCL race: send_data→send_start 같은 timestep | @(posedge w_i2c_clk) 1 cycle 대기 삽입 |

각 반복에서 Phase 4의 CSV 분석이 근본 원인 특정의 핵심이었다.

---

## xcelium-mcp Tool 맵핑 (v5.0 구조, 실제 24 tools — 2026-07-03 소스 재검증)

7개 모듈, **24개 네이티브 tool**(`grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py` 실측, 2026-07-03). action 파라미터 기반 통합 (v4.2 51개 → v5.0 24개).

> **2026-07-03 수정 노트**: 이 섹션은 원래 "v5.0 — 25 tools"로 표기돼 있었으나, 아래 `ssh_run("kill -s INT ...")` 항목이 xcelium-mcp 네이티브 tool이 아니라 **별도 ssh-mcp 서버의 헬퍼 명령**을 편의상 같은 표에 끼워 넣은 것이었다 — 실제 네이티브 tool을 21개 항목(Sim Lifecycle 10 + Batch 2 + Signal Inspection 2 + Debug 4 + Checkpoint 1 + Waveform 2 + SimVision 3 = 24)만 세면 정확히 24개다. 표 이름/파라미터 자체는 소스 대조 결과 전부 정확했음(수정 불필요) — 총계 표기만 교정.

### Sim Lifecycle — 환경 설정 + 실행 제어 (10 네이티브 + ssh-mcp 헬퍼 1건)

| Phase | Tool | 용도 |
|-------|------|------|
| 0 | `sim_discover` | 시뮬레이션 환경 자동 감지 (TB type, shell, EDA, sdf_info, top_module, boundary_depth — v5.2) |
| 0 | `mcp_config` | mcp_sim_config / mcp_registry 조회·수정 |
| 0 | `list_tests` | 테스트케이스 목록 조회 (캐시) |
| 2A | `sim_bridge_run` | Bridge (interactive) mode 시작 (dump_depth 지원, `auto_boundaries` — v5.2 Flow A) |
| 2B | `connect_simulator` | 기존 xmsim에 bridge 재연결 |
| 2B | `sim_disconnect` | action="bridge" (연결 해제) 또는 "shutdown" (안전 종료, SHM 보존) |
| 2B | `sim_run` | 시간/breakpoint 지정 실행 |
| 2B | `sim_status` | 현재 시간/scope/상태 조회. 정지 위치 확인 용도 (`sim_stop` 대체) |
| 2B | `sim_restart` | 시뮬레이션 재시작 |
| 2B | `execute_tcl` | 임의 Tcl 명령 실행 (escape hatch) |
| 2B | *(참고, 네이티브 아님)* `ssh_run("kill -s INT {xmsim_pid}")` | ssh-mcp 서버의 명령 — 실행 중 강제 중단. xmsim_pid는 sim_bridge_run result에서 획득 |

### Batch — 비대화형 실행 (2)

| Phase | Tool | 용도 |
|-------|------|------|
| 2A | `sim_batch_run` | 단일 테스트 batch 실행. dump_depth/dump_window/sdf_file/sdf_corner. 결과에 shm_path 포함 |
| 5D | `sim_regression` | 다중 테스트 순차 실행. dump_depth/dump_window/sdf_file/sdf_corner |

### Signal Inspection — 신호 조회·조작 (2)

| Phase | Tool | 용도 |
|-------|------|------|
| 1D | `inspect_signal` | action="check_dump" (SHM에 signal 존재 확인 — found/missing 분류) |
| 4D | `inspect_signal` | action="value" (값 읽기), "describe" (타입/폭), "list" (scope 목록), "drivers" (드라이버 추적) |
| 4D | `deposit_signal` | 신호값 강제 주입 (release=True로 해제) |

### Debug — 분석 + 프로빙 (4)

| Phase | Tool | 용도 |
|-------|------|------|
| 4E | `bisect_signal` | SHM CSV 기반 binary search (Mode A, 권장) 또는 bridge 기반 (Mode B). context_signals 지원 |
| 4D | `watch` | action="set" (watchpoint/breakpoint 설정, type 파라미터), "clear" (해제) |
| 4D | `probe` | action="add" (신호 추가), "enable"/"disable" (SHM 기록 on/off) |
| — | `debug_snapshot` | mode="snapshot" (통합 스냅샷), "tcl" (SimVision Tcl 생성), "export" (컨텍스트 내보내기) |

### Checkpoint (1)

| Phase | Tool | 용도 |
|-------|------|------|
| — | `checkpoint` | action="save" (저장), "restore" (복원), "list" (조회), "cleanup" (정리) |

### Waveform — 파형 제어 (2)

| Phase | Tool | 용도 |
|-------|------|------|
| 4E | `waveform` | action="add"/"remove"/"clear" (신호 관리), "zoom"/"cursor" (뷰 네비게이션) |
| 4E | `waveform_screenshot` | 스크린샷 캡처 (PNG, AI 분석용) |

### SimVision — GUI 시각화 (3)

| Phase | Tool | 용도 |
|-------|------|------|
| 4E | `simvision_connect` | action="start" (시작+연결), "attach" (기존 연결), "open_db" (SHM 열기) |
| 4E | `simvision` | action="setup" (신호+줌 일괄, screenshot 옵션), "live_start"/"live_stop" (실시간 모니터링), "reload" (동일/신규 SHM 갱신, waveform context 보존) |
| 4E | `compare_waveforms` | 두 SHM 비교 (CSV diff 또는 SimVision side-by-side) |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-03-26 | sync-xfr-extension 디버깅 경험 기반 초안 |
| 0.2 | 2026-03-26 | Phase 0 추가: TB 인프라 1회성 분석 + 캐시, Phase 1A를 캐시 참조 방식으로 변경 |
| 0.3 | 2026-03-26 | Phase 0 일반화: ncsim/UVM/SV 환경 대응, UVM 분석서 예시 추가 |
| **0.4** | **2026-03-26** | **Phase 4 최적화: CSV 1회 추출 → in-memory 반복 검색 원칙. 4B를 "좁은 범위 재추출"에서 "추가 신호 보충 추출"로 변경** |
| 0.5 | 2026-03-27 | Phase 1D 추가: Regression용 포괄 신호 집합 구성 Claude 워크플로우 (Read+ssh-mcp → AI 추론 → dump_signals). Phase 5D에 sim_batch_regression + dump_signals 연동 절차 추가. Tool 맵에 1D Claude 워크플로우 항목 추가 |
| 0.6 | 2026-03-27 | 일관성 검증 수정: (1) Phase 개요 diagram에 1D 추가, (2) 5C에 RTL 수정 후 compile hash 변경 → checkpoint 자동 무효화 안내 추가, (3) Tool 맵 v3 섹션에 cleanup_checkpoints / save_checkpoint(persistent 위치) / restore_checkpoint(hash 불일치 거부) 항목 추가, (4) sim_batch_run/regression에 auto-cleanup 설명 추가 |
| 0.7 | 2026-04-03 | (1) Phase 4E 추가: AI 자율 디버깅 (bisect→CSV→in-memory→RTL 참조) + Human-in-the-Loop SimVision 병렬 프로세스. TOP012 실전 예시. (2) 5C compile hash checkpoint 자동 무효화 폐기 |
| 0.8 | 2026-04-03 | (1) Phase 5D-2 추가: Regression 결과 검증 및 리포팅 표준 워크플로우 — 로그 판별(1차) + CSV waveform 검증(2차) + 3종 표 리포트 형식. (2) Tool 맵 v4.2 전체 갱신 (7모듈 51 tools). (3) 반복 패턴에 TOP012 TB SCL race fix 추가 |
| 0.9 | 2026-04-03 | Phase 0B 강화: TB 분석서 작성 절차 4단계 (파일 스캔 → 시나리오 추출 → 시퀀스 상세 → 공통 신호), 분석서 템플릿, 필수 항목에 테스트 시나리오 표 + 버그 이력 추가. Phase 1D 갱신: 포괄 신호 작성 절차 5단계, 5개 카테고리 분류, 실전 신호 수, setup_tcl depth all 참고사항 |
| 1.0 | 2026-04-07 | Tool 맵 v4.3 갱신: sim_discover (sdf_info/top_module), sim_start (dump_depth), sim_batch_run/regression (dump_depth/dump_window/sdf_file/sdf_corner). mode_defaults 5개 모드 전체 키 명시 |
| **2.0** | **2026-04-07** | **Tool 맵 v5.0 전면 교체: 51→25 tools. action 파라미터 기반 통합. sim_start→sim_bridge_run, sim_batch_regression→sim_regression, disconnect+shutdown→sim_disconnect, 6개 signal tool→inspect_signal+deposit_signal, 14개 debug tool→4개, 6개 waveform→2개, 8개 simvision→3개** |
| **2.1** | **2026-04-07** | **본문 code snippet + diagram 전면 갱신 (v5.0 tool 반영): 15개소 수정. 구 tool명(sim_start, sim_batch_regression, watch_signal, get_signal_value, extract_csv, shutdown_simulator, simvision_start, waveform_add/zoom, take_waveform_screenshot, probe_control, deposit_value, prepare_dump_scope, keep_alive) → 현재 tool명. 파라미터: sim_type→sim_mode, dump_window→dump_window_start_ms/end_ms. Phase 4E bisect 실전 예시에 실제 파라미터 포함** |
| **2.2** | **2026-04-08** | **Tool 맵 v5.1 갱신: inspect_signal에 "check_dump" action 추가 (Phase 1D — SHM signal 존재 확인, simvisdbutil 기반). simvision에 "reload" action 추가 (동일/신규 SHM 갱신, waveform context 보존). explorefull 제거로 duplicate DB 해결** |
| **2.3** | **2026-04-09** | **(1) Phase 3 판별 분기 수정: "판별 가능→Phase 5"가 모호 — PASS→Phase 5E, FAIL/불확정→Phase 4로 명확화 (본문 표와 일치). (2) TB 분석서 네이밍 규칙: env prefix(`lgc`/`uvm`/`dsv`/`ams`) 항상 필수, 파일명만 보고 TB 환경 즉시 유추 가능. AMS는 컴포넌트 자체가 analog를 다룰 때만 사용. (3) Phase 0-Prep 신설: sim_discover→list_tests→mcp_config 환경 등록 워크플로우 명시. (4) 파라미터 일관성 수정: sim_type→sim_mode (설명 텍스트), sim_mode="ams"→"ams_rtl" (실제 지원 값), bisect_signal start_time/end_time→start_ns/end_ns. (5) Phase 개요 diagram 동기화.** |
| **2.4** | **2026-07-03** | **`verilog-rtl-debugger` agent 위임 구조 신설 (chip-design-skills 신설 예정, 다른 verilog-rtl-* agent와 동일하게 install.py로 배포): 새 `## Agent 위임 구조` 섹션 추가 — 기존 5개 agent가 전부 정적 분석/코드 작성/formal 전용이라 MCP tool 기반 라이브 디버깅(Phase 2~4)을 수행할 agent가 없었던 공백을 이 신규 agent로 메움. Phase 1B(분석서 부재 시 verilog-rtl-analyst 위임), Phase 4C/4E(FSM 대조·자율 디버깅 루프를 verilog-rtl-debugger가 직접 소유), Phase 5A(verilog-rtl-coder/architect-advisor/reviewer로 위임 체인 명시) 갱신** |
| **2.5** | **2026-07-03** | **방법론 중복 방지 결정**: `verilog-rtl-debugger` agent가 Phase 2~4 방법론을 자체 내장하지 않고 `~/.claude/skills/xcelium-sim/references/phase-2~4*.md`를 런타임에 Read하도록 chip-design-skills 쪽과 합의(`verilog-rtl-debugger.plan.md` v0.2). §Agent 위임 구조에 "방법론 출처" 절 추가 — 해당 reference 파일이 이제 다른 repo agent가 소비하는 계약이 됨을 명시 |
| **2.6** | **2026-07-03** | **소스 재검증 전수 감사 결과 반영**(`src/xcelium_mcp/tools/*.py` 실측 대조, tool-usage-guide/debug-workflow-v2 Design 착수 전 정합성 점검): (1) "Tool 맵핑 v5.0 — 25 tools" 표기 오류 수정 — `ssh_run(kill)`이 네이티브 tool이 아닌 ssh-mcp 헬퍼인데 카운트에 포함돼 25로 부풀려짐, 실제 네이티브는 24개(정확 확인). 표 자체의 tool 이름·action 값·파라미터는 전수 대조 결과 전부 정확했음. (2) §1D-5 신설 — 완료된 `xcelium-mcp-v5.2-hierarchical-dump`(94% match rate)의 `dump_scopes`/`use_dump_history`/`auto_boundaries`/`boundary_depth` 파라미터가 이 문서 작성 시점(~2026-04-09) 이후 추가돼 전혀 반영 안 돼 있던 것을 발견해 추가. (3) `sim_discover`/`sim_bridge_run` 표 항목에 v5.2 파라미터 주석 추가 |
| **2.7** | **2026-07-03** | **`verilog-tb-analyst` agent 위임 구조 신설 — TB 분석서(Phase 0A/0B) 위임 비대칭 해소**: venezia-fpga 세션에서 `~/.claude/skills/xcelium-sim/`이 "tool 사용법 전용"이라 자칭하면서도 TB 분석 방법론(§0A/0B)을 통째로 안고 있어 RTL 쪽(Phase 1B → `verilog-rtl-analyst` 위임)과 비대칭이라는 지적을 받아 조사·확정. §Agent 위임 구조 표에 신규 행 추가, §Phase 0 도입부·§1A에 위임/fallback 문구 추가(1B와 동일 패턴). 신규 agent는 `verilog-rtl-debugger`와 동일 경로(chip-design-skills, install.py)로 배포 예정이며 상세 설계는 별도 PDCA(`verilog-tb-analyst.plan.md`, 신설 예정) 소관 |
