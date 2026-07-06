# Phase 0 — 검증 환경 인프라 분석 (1회성, 캐시)

## 목적

검증 환경의 공유 컴포넌트와 테스트케이스는 프로젝트 수명 동안 비교적 안정적이다. 한 번 분석하고 캐시하면 이후 모든 디버깅에서 재사용한다. 이 Phase는 검증 환경 종류(Legacy directed Verilog / UVM / Directed SV / AMS / Multi-methodology)에 무관하게 동일 원칙이 적용된다.

> **기본 실행 주체는 Claude 자신이다**: §0A/0B(TB 분석서 작성/갱신)는 아래 절차만으로 Claude가 직접 수행 가능하다(agent 설치 여부와 무관하게 항상 동작).
> **선택적 위임(있으면)**: `verilog-tb-analyst` agent(신설 예정 — chip-design-skills가 install.py로 user/project-level `.claude/agents/`에 배포, chip-design-skills 자체가 호출하는 게 아님)가 로컬에 설치돼 있으면 이 작업을 Task로 위임할 수 있다 — RTL 쪽 `verilog-rtl-analyst`(Phase 1B 위임)와 대칭 구조를 맞추고 여러 세션에서 일관된 품질을 내기 위한 최적화 옵션일 뿐, 필수 경로는 아니다.

## 절차

### 0-Prep. xcelium-mcp 환경 등록 (최초 1회)

```python
sim_discover(sim_dir="", force=False)   # TB type/shell/EDA/sim_dir/setup_tcls/sdf_info/top_module 자동 감지
list_tests()                             # 테스트 목록 캐싱
mcp_config(action="get", key="runner.default_mode")   # 필요 시 수동 조정
```

`sim_discover`는 TB type을 감지해 `tb_type: ncsim_legacy | uvm | sv_directed | mixed`로 등록한다. v5.2 이후 `boundary_depth` 파라미터로 블록 경계 자동 탐색 깊이도 함께 설정 가능(Flow B — Yosys JSON lazy discovery, 실제 탐색은 이후 `sim_batch_run`에서 지연 수행).

### 0-Prep-2. TB 소스 읽기 원칙 — 로컬 사본 금지

0A/0B에서 TB 소스 파일을 읽어 분석서를 작성할 때, 로컬 프로젝트 저장소에 있는 동일 파일명 사본은 읽지 않는다. 항상 `sim_discover`/`mcp_config`가 resolve한 실제 `sim_dir`을 ssh-mcp(`file_read`/`file_grep`)로 직접 읽는다 — 조건 없는 규칙이다(로컬 사본이 있어도 그걸로 대체하지 않는다).

**Why**: 여러 저장소(예: FPGA 검증용 로컬 repo와 실제 시뮬레이션에 쓰이는 ASIC/venezia-t0 repo)에 같은 이름의 TB 테스트 파일이 독립적으로 존재할 수 있고, 두 파일의 내용이 서로 달라져 있을 수 있다(2026-07-06 실전 사례: 로컬 사본엔 있던 drain 로직이 실제 cloud0 TB엔 없어서, 로컬 사본을 읽고 작성한 분석서의 근본원인 서술이 실제 시뮬레이션 결과와 어긋났다). 이 사건 이후 로컬 사본 자체를 두지 않기로 결정했다 — 그러니 "로컬 사본을 검증 후 조건부로 써도 되는지"가 아니라, 애초에 분석 근거로 로컬 경로를 읽지 않는다는 게 원칙이다.

> **별도 저장소의 agent 문서화 필요**: `verilog-tb-analyst`/`verilog-rtl-debugger` agent(chip-design-skills repo 소유, 이 저장소와 별개)가 이 원칙을 따르게 하려면 해당 agent 문서에도 동일 원칙이 반영되어야 한다 — 이 항목은 xcelium-mcp의 skill-src만 수정 대상이며, chip-design-skills 쪽 반영은 별도 작업이다.

### 0A. 공유 컴포넌트 분석서

파일 네이밍: `.ai/analysis/tb_{env}_{component_name}.analysis.md` (env prefix 항상 필수: `lgc`/`uvm`/`dsv`/`ams`)

대상 식별 절차:
1. 테스트케이스 파일들에서 `` `include``/`import`/인스턴스화 라인 스캔(예: `grep -oE '\`include \"[^\"]+\"|import [A-Za-z_]+::' tb/**/*.sv`)
2. 스캔 결과를 파일별로 집계 — 2개 이상 테스트케이스에서 공통으로 참조되는 파일·패키지만 "공유 컴포넌트"로 분류
3. 단일 테스트에서만 쓰이는 것은 0A 대상이 아니며 0B(테스트케이스별 분석)에서 그 테스트 문맥으로 함께 기술

필수 포함 항목: 인터페이스 API, 프로토콜/시퀀스, 타이밍, 알려진 제약, DUT 계층 참조, 상위 호출 패턴, 판정 기여.

### 0B. 테스트케이스별 분석 캐시

파일 네이밍: `.ai/analysis/tb_{env}_{test_name}.analysis.md`

작성 절차(ncsim Legacy, env=`lgc` 기준):
1. 테스트 파일 스캔 — DUT 신호 참조(`grep -oE 'top\.hw\.\S+'`), 사용 task, include 파일
2. 시나리오 추출 — `run_test` task의 `test_id` + 인접 주석
3. 시퀀스 상세 파악 — task 시퀀스 패턴(예: I2C `send_start→send_data→recv_data→send_stop`)
4. 공통 Dump 신호 추출(regression 직전 1회) — 전체 테스트 DUT 신호 합집합 → `tb_{env}_regression_common_signals.analysis.md`

필수 포함 항목(환경 무관): 테스트 목적, 사용 공유 컴포넌트, 테스트 시나리오(tid별 표), 판별 방법, 판별 신호+기대값, DUT 내부 참조, 시뮬레이션 길이, 버그 이력.

**env가 `uvm`/`dsv`/`ams`일 때는 위 4단계 절차가 그대로 적용되지 않는다** — 이 절차는 legacy directed Verilog(task 기반 시퀀스)를 전제로 쓰여 있어서, UVM agent/driver/monitor/sequencer 구조나 듀얼탑(hdl_top/hvl_top) 인터페이스, AMS 아날로그 모델을 그대로 grep 패턴으로 스캔할 수 없다. 이 경우 아래 skill을 **같은 방식으로 직접 Read**해서 절차를 그 env에 맞게 재해석한다(전체 skill이 아니라 필요 절만, `verilog-tb-analyst.plan.md` FR-10/FR-11/FR-12와 동일한 근거·동일한 절 한정):
- `~/.claude/skills/verilog-rtl/SKILL.md` §8(Verification 연계 — SVA Assertion, Coverage) + `references/covergroup-patterns.md`/`coverage-methodology.md`/`coverage-examples.md` — TB 코드 자체가 SystemVerilog 문법(covergroup/coverpoint/cross, assertion)으로 작성되므로 정확히 해석하려면 필요
- `~/.claude/skills/chip-verification/SKILL.md` §듀얼탑 아키텍처 + `references/interface-mapping.md` — env 무관 대부분의 TB가 hdl_top(DUT+Interface)/hvl_top(UVM 또는 SV TB) 구조이므로, "필수 포함 항목"의 "인터페이스 API"/"DUT 계층 참조" 작성에 DUT 포트→Interface→Virtual Interface 매핑 이해가 필요
- `~/.claude/skills/uvm-verification/SKILL.md` §UVM 계층 구조 + `references/component-templates.md`/`sequence-patterns.md` — env=`uvm`일 때 "사용 공유 컴포넌트"/"테스트 시나리오"/"판정 기여" 항목이 agent/driver/monitor/sequencer/scoreboard 용어로 기술되므로 필요

RTL 합성·CDC 설계 규칙(verilog-rtl §1~§7, §11)이나 AMS 아날로그 모델 교체 절차(chip-verification §아날로그 모델 교체)는 TB 분석서 작성과 무관하므로 로드 범위에서 제외한다.

### 0C. 캐시 관리 규칙

| 규칙 | 설명 |
|------|------|
| 작성 시점 | 해당 컴포넌트/테스트를 처음 디버깅할 때 (lazy) |
| 갱신 시점 | 해당 파일이 수정되었을 때만 — 판단 방법은 아래 "갱신 필요 판단" 참조 |
| 갱신 불필요 | RTL만 수정되고 TB 파일 미변경 시 |
| 저장 위치 | `.ai/analysis/tb_*.analysis.md` |
| 참조 관계 | RTL 분석서 → DUT FSM/신호, TB 분석서 → 시퀀스/기대값/API |

**갱신 필요 판단(F-175 tb_source/tb_provenance 활용)**: "해당 파일이 수정되었을 때만" 갱신한다는 규칙은 있지만, 캐시된 분석서를 재사용하려는 시점에 TB 소스가 그 사이 바뀌었는지 매번 전체 내용을 다시 읽어 비교하면 캐싱하는 의미가 없다. 대신 가벼운 체크섬만 비교한다:

1. 분석서를 처음 작성할 때, 그 시점 실행의 `tb_source`/`tb_provenance`(경로+sha256 — `sim_batch_run`/`sim_regression`/`sim_bridge_run`이 이미 반환값에 포함하고 있으므로 별도 조회 불필요)를 분석서 헤더에 함께 기록해둔다.
2. 다음 디버깅에서 캐시된 분석서를 재사용하기 전, 이번 실행의 `tb_source.sha256`과 분석서 헤더에 기록된 sha256을 비교한다.
3. 일치하면 TB 소스가 그대로라는 뜻이므로 캐시를 그대로 재사용. 다르면(또는 분석서에 애초에 기록이 없으면 — F-175 이전에 작성된 분석서 등) TB 소스가 바뀌었을 수 있으므로 재작성한다.

이건 "로컬 사본을 믿어도 되는지"와는 무관하다(0-Prep-2 참조 — 로컬 사본은 애초에 안 읽는다) — 순전히 "지금 실행이 만든 결과가 캐시된 분석서 작성 시점의 TB 소스와 같은가"를 싼 값에 판단하기 위한 용도다.

## Tool 예시

```python
sim_discover(sim_dir="", force=False)
list_tests(pattern="TOP01*")
mcp_config(action="show", file="registry")
```

## verilog-tb-analyst agent 위임

| 상황 | 위임 대상 |
|------|----------|
| §0A/0B TB 분석서(공유 컴포넌트/테스트케이스) 신규 작성·갱신 | `verilog-tb-analyst` |

## 다음 단계

캐시가 있으면 phase-1-analysis.md로, 없으면 이 Phase에서 분석서 작성 후 phase-1-analysis.md로 진행.
