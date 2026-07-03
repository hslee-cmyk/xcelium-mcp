# Plan: `/sim` — Verilog HW Verification Workflow

> **Feature**: Verilog 하드웨어 디자인 검증 워크플로우
>
> **Date**: 2026-04-03
> **Status**: Draft v1.0
> **Predecessor**: `xcelium-mcp-debugging-workflow.plan.md` — Phase 0~5 상세, TB 캐시, 실전 히스토리
> **Scope**: 시뮬레이터 독립적 범용 HW 검증 프레임워크. 첫 번째 백엔드: xcelium-mcp

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Verilog HW 검증은 시뮬레이션 실행·결과 분석·디버깅의 반복이지만, 시뮬레이터별로 도구와 절차가 다르고 표준화된 워크플로우가 없다. 현재 xcelium-mcp의 24개 tool을 개별 호출하는 방식은 세션당 10~20회 trigger가 필요하며, 다른 시뮬레이터로의 확장이 불가능 |
| **Solution** | 5-Layer 범용 검증 프레임워크: `/sim` Skill(워크플로우 orchestration) + Simulator Backend 추상화(교체 가능) + Compound Operations(기계적 시퀀스 1-call) + CLI(사용자 직접 실행) + Hook 자동화(phase 전환·상태 주입) |
| **Function UX Effect** | `/sim verify TOP015` 한 번으로 실행→분석→디버깅 자동 체이닝. 시뮬레이터가 바뀌어도 동일한 `/sim` 명령 사용. CLI로 AI 없이 직접 실행 가능 |
| **Core Value** | 시뮬레이터 독립적 검증 워크플로우 표준화, legacy/UVM/SV 테스트벤치 모두 대응, tool trigger 60% 감소, 프로젝트 간 동일 경험 |

---

## Context Anchor

> Auto-generated per bkit plan template. Design/Do 문서로 전파됨.

| Key | Value |
|-----|-------|
| **WHY** | 시뮬레이터별 도구·절차가 제각각이라 표준화된 검증 워크플로우가 없고, 24개 개별 tool을 세션마다 개별 호출해야 해서 trigger가 과다함 |
| **WHO** | xcelium-mcp로 RTL 검증을 수행하는 AI 에이전트 및 엔지니어 (현재 소비 프로젝트: `venezia-fpga`) |
| **RISK** | sys.argv 분기로 기존 MCP stdio 진입점이 깨질 수 있음(§11); `compound.py`가 기존 batch/CSV 로직을 재구현하면 이미 검증된 경로(472 tests)와 별개로 새 버그 표면이 생김(§3.4 주의) |
| **SUCCESS** | `/sim verify {test}` 1회 호출로 run→analyze→(debug) 자동 체이닝; 기존 24 tool 전량 하위호환 유지; tool trigger 세션당 60% 감소 |
| **SCOPE** | Phase A-C(Backend 조합 계층 + CLI + Skill) 우선 구현 → 검증 후 Phase D(Hook 자동화) 후행. Backend Interface(§3, 다중 시뮬레이터 추상화)는 두 번째 backend가 실제로 필요해지기 전까지 YAGNI 후보(§D 참조) |

---

## Requirements

### Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Run: batch/bridge 두 모드로 단일 테스트 실행, dump+로그 산출 | High | Pending |
| FR-02 | Analyze: 로그 판별 + CSV 추출/검색 + FAIL 유형 자동 분류(§5.4) | High | Pending |
| FR-03 | Debug: RTL 분석서 기반 FAIL 원인 추적 + 수정 제안, verilog-rtl skill 연계 | High | Pending |
| FR-04 | Verify: run→analyze→(FAIL시)debug 자동 체이닝, 수정 후 재진입 | High | Pending |
| FR-05 | sim-state.json: 테스트별 phase/결과/origin_chain 영속 상태 추적(§5.1) | High | Pending |
| FR-06 | CLI: AI 없이 `xcelium-mcp run/analyze/regression` 직접 실행(§7) | Medium | Pending |
| FR-07 | Backend Interface: compound operation 3종 규격화(§3) — **YAGNI 후보**, 두 번째 backend(vcs-mcp 등) 착수 전까지 interface만 정의하고 범용화 자체는 보류 검토 | Low | Pending |
| FR-08 | Hook 자동화: PostToolUse phase 전환 제안 + UserPromptSubmit 키워드 트리거(§6) — Phase D, A-C 검증 후 후행 | Medium | Pending |
| FR-09 | Tool 사용법 가이드(24개 raw tool의 phase별 선택 매트릭스, `references/tool-map.md` 상당)는 **이 `/sim` skill과 동일한 `~/.claude/skills/xcelium-sim/` 디렉터리의 Phase 1**로 구현 — compound.py(Layer 3) 완성을 기다리지 않고 즉시 착수 가능. 상세 plan: `xcelium-mcp-tool-usage-guide`(§Dependencies 참조, v0.2에서 별도 skill 안이 이 skill로 통합됨) | High | Pending (선행 feature, Phase 1) |

---

## Dependencies

> 이 문서와 관련 있는 다른 두 계획(`xcelium-mcp-v5.1-runner-abstraction.plan.md`, 신규 tool-usage-guide skill)의 관계를 명시한다. "선행 필수" 오독을 막기 위한 섹션.

### xcelium-mcp-v5.1-runner-abstraction.plan.md와의 관계 — **선행 필수 아님, 독립 트랙**

- **v5.1 상태**: Draft(2026-04-01), design/analysis/report 없음, `RunnerInterface`/wrapper script 자동생성/`sim_start()` 미구현 — v4.2/v4.3/v5.2가 완료되는 동안 버전 시퀀스에서 건너뛰어짐.
- **왜 선행조건이 아닌가**: 이 문서 §3.4의 `compound.py`는 **오늘 이미 동작하는** `batch_runner.py`/`bridge_lifecycle.py`를 wrap하도록 설계돼 있고, 이 함수들은 v5.1의 `RunnerInterface`가 아니라 **v4.1-era `runner_detection.py`**(`auto_detect_runner`, registry의 `runner.type`+`args_format`)를 통해 이미 환경 감지를 수행한다. 즉 `compound.py → batch_runner.py → runner_detection.py(v4.1)` 체인이 지금 이미 존재하므로, **ncsim legacy 환경 한정으로는 v5.1 없이 Phase A-C(§8.2) 착수가 가능하다.**
- **Layer 경계**: `/sim` skill(Layer 4)은 compound.py가 `CompoundResult`만 반환하면 되고, 그 밑단이 v4.1 방식이든 v5.1 방식이든 신경 쓰지 않는다 — 나중에 v5.1로 교체해도 Layer 4는 영향받지 않는 클린한 경계.
- **진짜 걸리는 지점**: Executive Summary의 "**시뮬레이터/환경 독립적**" 주장을 UVM/Makefile 환경에서도 신뢰하려면 v5.1이 필요하다. v4.1의 ad-hoc 감지가 UVM/Makefile에서 검증된 적은 없다(§Success Criteria의 M3급 리스크와 동일 계열). **스코프를 ncsim legacy로 한정하면 v5.1 불필요, "환경 독립적" 주장 자체를 스코프에 포함하면 v5.1이 실질적 선행조건이 된다.**
- **권고**: Phase A-C는 v5.1과 무관하게 지금 시작하고, v5.1은 별도 PDCA 사이클로 병행하거나 후행(2번째 backend 필요 시점, 또는 UVM 환경이 실제로 쓰이기 시작하는 시점)한다.

### tool-usage-guide(FR-09)와의 관계 — **동일 skill의 Phase 1 (2026-07-02 통합 결정, v0.2)**

- 최초에는 "별도 skill, 나중에 `/sim`이 흡수"로 설계했으나, 재검토 결과 **같은 `~/.claude/skills/xcelium-sim/` 디렉터리를 목표로 하는 하나의 skill**로 통합했다.
- `xcelium-mcp-tool-usage-guide`(Phase 1): `compound.py` 없이 지금 만들 수 있는 부분 — `references/phase-0~5.md`, `references/tool-map.md`, SKILL.md의 키워드 트리거·라우팅 스켈레톤. 선행 착수한다.
- 이 문서(`xcelium-mcp-debug-workflow-v2`, Phase 2): `compound.py`(Layer 3) 완성 후 같은 SKILL.md에 `/sim run|analyze|debug|verify|status` subcommand 라우팅을 추가.
- **결정(2026-07-03)**: Design 문서는 **별도 유지**한다. bkit matchRate는 feature별로 Design vs 구현을 비교하므로, 병합하면 이 문서(Phase 2, compound.py 대기 중이라 아직 구현 불가)의 Design 요소가 tool-usage-guide의 Do에서 구현되지 않아 matchRate가 부당하게 낮아진다. 이 문서의 Design은 compound.py(Layer 3)가 생긴 뒤 별도로 착수하며, 그때 tool-usage-guide의 Design에서 남겨둔 SKILL.md 확장점(subcommand 라우팅 자리)에 맞춰 작성한다.
- 배포 위치도 재확인: `~/.claude/skills/xcelium-sim/`은 **user-level**이다 — xcelium-mcp repo 안(project-level)에 두면 실제 디버깅이 일어나는 venezia-fpga 등 RTL 프로젝트 세션에서 전혀 로드되지 않으므로, 이 결정은 필수적이다.

### Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|--------------------|
| Backward Compatibility | 기존 24 tool 100% 하위호환, `pytest` 472 tests 회귀 없음 | `pytest tests/ -v` 전체 스위트 |
| Efficiency | tool trigger 세션당 60% 감소 | 도입 전/후 세션 로그 tool_use 횟수 비교 |
| Maintainability | `compound.py`가 기존 `batch_runner.py`/`csv_cache.py` 재사용, 로직 중복 없음 | 코드 리뷰 (§3.4) |

---

## 1. 비전: HW 검증 워크플로우 표준화

### 1.1 현재 상황

```
프로젝트 A (venezia-fpga)         프로젝트 B (미래 ASIC)
├─ Xcelium + ncsim legacy         ├─ VCS + UVM
├─ xcelium-mcp 24 tools           ├─ (도구 없음)
├─ 수동 워크플로우                 ├─ 수동 워크플로우
└─ 프로젝트별 고유 절차            └─ 또 다른 고유 절차
```

### 1.2 목표

```
프로젝트 A (venezia-fpga)         프로젝트 B (미래 ASIC)
├─ /sim run TOP015                ├─ /sim run test_apb_wr
├─ /sim analyze TOP015            ├─ /sim analyze test_apb_wr
├─ /sim debug TOP015              ├─ /sim debug test_apb_wr
│     │                           │     │
│  [xcelium-mcp backend]          │  [vcs-mcp backend]
│                                 │
└── 동일한 워크플로우 ─────────────└── 동일한 워크플로우
    동일한 TB 분석서 형식              동일한 TB 분석서 형식
    동일한 sim-state.json              동일한 sim-state.json
```

### 1.3 3가지 목적 (불변)

| 목적 | 설명 | 산출물 |
|:----:|------|--------|
| **Run** | 시뮬레이션 실행 (legacy/UVM/SV, batch/bridge) | SHM/VPD/FST dump + 로그 |
| **Analyze** | 결과 분석 (pass/fail, coverage, waveform CSV) | PASS/FAIL 판정 + CSV + coverage |
| **Debug** | FAIL 원인 추적 (RTL debugging) | 근본 원인 + 수정 제안 |

`verify` = Run → Analyze → (FAIL시) Debug → (수정 후) Run → ... 체이닝

---

## 2. 5-Layer 아키텍처

```
Layer 4: /sim Skill (범용 HW 검증 워크플로우)
    │  시뮬레이터·프로젝트 독립적
    │  run / analyze / debug / verify / status subcommands
    │  TB frontmatter 파싱, sim-state 참조, next-skill 제안, FAIL 분류
    │
Layer 3: Simulator Backend (교체 가능 추상화)
    │  xcelium-mcp (현재) — Xcelium/SimVision
    │  (향후) vcs-mcp — Synopsys VCS/Verdi
    │  (향후) verilator-mcp — Verilator
    │  각 backend가 compound operations + 개별 tools 제공
    │
Layer 2: CLI Commands (backend별)
    │  xcelium-mcp run / analyze / regression
    │  (향후) vcs-mcp run / analyze / regression
    │
Layer 1: Hook 자동화 (Claude Code plugin)
    │  PostToolUse: phase 자동 전환, next-skill 제안
    │  UserPromptSubmit: 자동 트리거 키워드
    │
Layer 0: 개별 MCP Tools (backend별)
    xcelium-mcp 24 tools, (향후) vcs-mcp N tools, ...
```

### 2.1 Layer 간 의존성

```
Layer 4 (/sim Skill)
    │
    ├─ Backend Interface (§3)를 통해 Layer 3 호출
    │   └─ "어떤 backend든 run_and_check, analyze_waveform을 제공한다"
    │
    ├─ sim-state.json (§5.1)을 Read tool로 참조
    │
    └─ TB 분석서 YAML frontmatter (§5.2)를 Read tool로 파싱

Layer 3 (Backend)
    │
    ├─ Compound operations 구현 (backend별 Python)
    └─ sim-state.json 갱신 (compound 실행 후 자동)

Layer 1 (Hooks)
    │
    ├─ sim-state.json 읽기 → SessionStart context 주입
    └─ compound tool 실행 감지 → phase 전환 제안
```

### 2.2 각 Layer의 역할 분담

| 판단 영역 | Layer | 이유 |
|-----------|-------|------|
| "어떤 subcommand가 적절한가?" | **Skill** (L4) | 상태·로그·SHM 유무에 따른 AI 판단 |
| "판별 신호는 무엇인가?" | **Skill** (L4) | TB 분석서 자연어 이해 |
| "FSM 전이 대조 → 근본 원인" | **Skill** (L4) | RTL 분석서 + CSV 교차 분석 |
| "현재 시뮬레이션 상태 확인" | **Skill** (L4) | `/sim status`로 on-demand 조회 |
| "batch → 로그 → CSV 추출" | **Backend** (L3) | 기계적 시퀀스, 시뮬레이터별 구현 |
| "CSV 조건 매칭" | **Backend** (L3) | 파라미터만 다름, 동일 로직 |
| "regression 집계" | **Backend** (L3) | 기계적 집계 |
| "compound 실행 후 phase 전환" | **Hook** (L1) | 자동 전환, next-skill 제안 |
| "시뮬레이션 키워드 감지" | **Hook** (L1) | 자동 트리거 |
| "사용자 직접 실행" | **CLI** (L2) | AI 불필요 |
| "bridge mode 세밀 제어" | **개별 tool** (L0) | watch, breakpoint 등 |

---

## 3. Backend Interface (시뮬레이터 추상화)

### 3.1 Backend가 제공해야 하는 Compound Operations

모든 simulator backend는 다음 3개 compound operation을 구현해야 한다. `/sim` Skill은 이 인터페이스만 사용하며, 시뮬레이터 고유 API를 직접 호출하지 않는다.

| Operation | 입력 | 출력 | 설명 |
|-----------|------|------|------|
| `run_and_check` | test_name, csv_signals, csv_mode, find_condition | CompoundResult | 단일 테스트 실행 + 로그 확인 + (선택) CSV 추출/검색 |
| `analyze_waveform` | dump_path, signals, find_conditions, range | CompoundResult | Dump에서 CSV 추출 + 다중 조건 검색 |
| `regression_summary` | test_list, csv_on_fail, csv_signals | CompoundResult | Regression 실행 + 전체 요약 + FAIL CSV |

### 3.2 CompoundResult (공유 결과 타입)

```python
@dataclass
class CompoundResult:
    status: str          # "PASS" | "FAIL" | "ERROR" | "PARTIAL"
    log_summary: str     # 로그 요약 (PASS/FAIL 라인, error count)
    dump_path: str       # Waveform dump 경로 (SHM/VPD/FST)
    csv_path: str        # CSV 경로 (추출한 경우)
    details: dict        # 추가 상세 (bisect 결과, per-test 결과 등)
    
    def to_cli_output(self) -> str:
        """CLI용 [TAG] 형식 출력."""
    
    def to_mcp_output(self) -> str:
        """MCP tool용 상세 텍스트 출력."""
```

**주의**: `shm_path`가 아닌 `dump_path` — 시뮬레이터마다 dump 형식이 다르기 때문 (SHM/VPD/FST/VCD).

### 3.3 Backend 등록

```python
# 각 backend의 __init__.py 또는 server.py
BACKEND_INFO = {
    "name": "xcelium-mcp",
    "simulator": "Xcelium",
    "dump_format": "SHM",
    "compound_tools": ["run_and_check", "analyze_waveform", "regression_summary"],
    "individual_tools": 24,
    "bridge_supported": True,
}
```

`/sim` Skill은 MCP server에 `mcp_config(action="show", file="registry")`로 backend 정보를 조회하여 어떤 시뮬레이터인지 파악한다.

### 3.4 xcelium-mcp Backend 구현 (첫 번째)

```
src/xcelium_mcp/
├── compound.py              ← compound operation 핵심 로직
│   ├── CompoundResult       (§3.2)
│   ├── run_and_check()      batch_run → log_grep → csv_extract → bisect
│   ├── analyze_waveform()   csv_extract → multi-condition bisect
│   └── regression_summary() batch_regression → per-test log → csv on fail
├── sim_state.py             ← sim-state.json 읽기/쓰기 (§5.1)
├── cli.py                   ← CLI argparse → compound.py 호출
├── tools/compound.py        ← MCP tool 3개 → compound.py 호출
└── server.py                ← sys.argv 분기 + compound tool 등록
```

**주의 — 신규 구현이 아니라 조합(wrap)**: `run_and_check`/`analyze_waveform`/`regression_summary`는 새 로직이 아니라 이미 완료된 v3 Improvement Plan(`batch_runner.py`의 `run_batch_single`/`run_batch_regression`, `csv_cache.py`의 `extract`/`bisect_signal_dump`)을 시퀀스로 묶는 **얇은 조합 계층**이다. `compound.py` 구현 시 이 함수들을 그대로 호출·재사용하고, batch 실행이나 CSV 추출 로직 자체를 재작성하지 않는다 — 그러지 않으면 이미 검증된(326 tests passing) 경로와 별개로 새 버그 표면이 생긴다.

---

## 4. `/sim` Skill (Layer 4)

### 4.1 Subcommand 구조

```
/sim run TOP015                 ← 시뮬레이션 실행
/sim run TOP015 --bridge        ← Bridge mode (interactive)
/sim run --regression           ← 전체 regression

/sim analyze TOP015             ← 결과 분석 (로그 + CSV + coverage)
/sim analyze --regression       ← regression 전체 결과 분석

/sim debug TOP015               ← FAIL 원인 추적
/sim debug TOP015 --bridge      ← Interactive debugging

/sim verify TOP015              ← run → analyze → (debug if fail) 체이닝
/sim verify --regression        ← regression → 분석 → FAIL 디버깅

/sim status                     ← 현재 시뮬레이션 상태 (cross-session 복구용)
```

**`/sim status`의 핵심 역할**: sim-state.json을 Read하여 이전 세션의 상태를 현재 context에 로드한다. 새 세션(사용자 또는 Agent)에서 이전 작업을 이어받을 때 **가장 먼저 실행**하여 context를 복구한 후 다른 subcommand를 실행한다.

```
[새 세션 또는 Agent 시작]
  → /sim status           ← sim-state.json → context 로드
  → /sim analyze TOP015   ← 이전 run 결과가 context에 존재, 바로 진행
  → /sim debug TOP015     ← 이전 analyze 결과도 context에 존재
```

별도의 context:fork나 SessionStart hook 없이, `/sim status` 1회 호출로 동일 효과.

### 4.2 각 Subcommand 상세

#### `/sim run`

```
1. TB 분석서 캐시 확인 → {project}/.ai/analysis/tb_{test}.analysis.md
   ├─ YAML frontmatter 있음 → pass_signals, fail_conditions 자동 추출 (§5.2)
   ├─ frontmatter 없음 → AI가 본문 읽어서 판단 (fallback)
   └─ 분석서 자체 없음 → 분석서 작성 후 진행 (Phase 0)

2. Backend compound operation 호출
   ├─ --bridge → backend별 bridge 연결 (xcelium: connect_simulator)
   ├─ --regression → regression_summary compound
   └─ (기본) → run_and_check compound

3. sim-state.json 자동 갱신 (compound가 수행)
4. 결과 반환 + next-skill 제안 (§5.3)
```

#### `/sim analyze`

```
1. sim-state.json에서 이전 run 결과 참조 (§5.1)
   ├─ dump_path, log_summary 로드
   └─ 없으면 → "먼저 /sim run 실행 필요"

2. 로그 판별
   ├─ PASS → 보고 + "regression?" 제안
   ├─ FAIL → Step 3
   └─ 불확정 → Step 3

3. CSV 추출 + 검색 → analyze_waveform compound
   ├─ 신호: TB frontmatter의 pass_signals
   └─ 조건: TB frontmatter의 fail_conditions

4. FAIL 유형 자동 분류 (§5.4)
5. sim-state 갱신 + next-skill 제안
```

#### `/sim debug`

```
1. sim-state.json에서 analyze 결과 참조
   ├─ FAIL 유형, 이상 시점, 관련 신호
   └─ origin chain (§5.6) 확인

2. RTL 분석서 참조 → {project}/.ai/analysis/{module}.analysis.md
   └─ FSM 전이표, 신호 의존성 맵

3. FAIL 유형별 자동 전략 선택 (§5.4)
   ├─ 데이터 불일치 → CSV 심층 분석 + FSM 대조
   ├─ Timeout/Hang → Bridge mode 전환
   ├─ Assertion → 해당 시점 CSV
   └─ Protocol 위반 → 프로토콜 신호 분석

4. (필요 시) Interactive probing — backend별 bridge tool 사용

5. 결과: 근본 원인 + 수정 제안 + verilog-rtl skill 연계
6. sim-state 갱신 (origin chain 포함) + next-skill 제안
```

#### `/sim verify`

```
1. /sim run {test}
2. /sim analyze {test}
3. FAIL이면:
   ├─ /sim debug {test}
   ├─ 수정 제안 → 사용자 확인 대기
   └─ 수정 완료 → Step 1 재진입
4. PASS이면:
   ├─ 단일 테스트 → "regression?" 제안
   └─ --regression → 요약 보고

--regression 시 복수 FAIL → 병렬 Agent 분석 (§5.7)
```

### 4.3 Skill 파일 구조

```
~/.claude/skills/xcelium-sim/
├── SKILL.md                     ← subcommand 라우팅 + 트리거 + next-skill-map
└── references/
    ├── run-guide.md             ← TB frontmatter 파싱, batch/bridge 선택
    ├── analyze-guide.md         ← 로그 판별, CSV 분석, FAIL 유형 분류표
    ├── debug-guide.md           ← FSM 대조, origin linking, probing
    ├── tool-map.md              ← 도구 선택 매트릭스, 병렬 분석 조건
    └── backend-interface.md     ← backend가 제공해야 하는 compound 인터페이스
```

**원본 워크플로우 참조**: 각 reference는 `xcelium-mcp-debugging-workflow.plan.md`의 해당 Phase를 압축.

| Reference | 원본 Phase | 압축 대상 |
|-----------|-----------|----------|
| `run-guide.md` | Phase 0~2 | TB 캐시 규칙, batch/bridge 선택, dump scope |
| `analyze-guide.md` | Phase 3~4A-B | 판별 매트릭스, CSV 1회 추출 원칙 |
| `debug-guide.md` | Phase 4C-D~5 | FSM 대조법, probing, 수정→재검증 |
| `tool-map.md` | 전체 | compound + 개별 tool 선택, 레시피 A~E |
| `backend-interface.md` | (신규) | compound operation 인터페이스 정의 |

### 4.4 SKILL.md 핵심

```yaml
---
name: xcelium-sim
trigger: |
  시뮬레이션, simulation, 테스트 실행, test run, regression,
  FAIL 분석, 결과 분석, waveform, CSV, 디버깅, debugging,
  TOP0, run_sim, sim_batch, coverage, 검증, verification
next-skill-map:
  run.PASS: "sim run --regression"
  run.FAIL: "sim analyze"
  analyze.FOUND: "sim debug"
  analyze.INCONCLUSIVE: "sim analyze --signals"
  debug.FIXED: "verilog-rtl → sim verify"
  verify.PASS: null
  verify.FAIL: "sim debug"
---
```

### 4.5 Skill ↔ Backend 도구 매핑

| Skill subcommand | Backend compound | Backend 개별 tool (필요 시) |
|-----------------|-----------------|--------------------------|
| `run` (batch) | `run_and_check` | — |
| `run` (bridge) | — | connect_simulator, sim_run |
| `run` (regression) | `regression_summary` | — |
| `analyze` | `analyze_waveform` | — |
| `debug` (CSV) | `analyze_waveform` | — |
| `debug` (bridge) | — | connect, watch, get_signal_value |
| `debug` (RTL) | — | `verilog-rtl-debugger` agent 호출(chip-design-skills, 신설 예정 — `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조), 필요 시 analyst/coder/reviewer/prover로 추가 위임 |
| `verify` | 위 조합 (자동 체이닝) | — |
| `status` | — | mcp_config, ssh_run |

---

## 5. 워크플로우 패턴 (bkit/compound-engineering 기반)

### 5.1 sim-state.json — 테스트별 상태 추적

**출처**: bkit `lib/pdca/status.js`

> **`{project}` 표기 규약**: 이 문서 전체에서 `{project}`는 **xcelium-mcp를 사용하는 RTL 검증 프로젝트**(예: `venezia-fpga`)를 가리키며, xcelium-mcp 저장소 자신을 가리키지 않는다. `sim-state.json`과 TB 분석서 캐시(`.ai/analysis/`)는 항상 호출 측 RTL 프로젝트 루트에 위치한다. 이 plan 문서가 xcelium-mcp repo로 이관된 이후에도(§Migration Note) 이 구분은 변하지 않는다.

**위치**: `{project}/.ai/sim-state.json` (프로젝트별, Git 미추적)

```json
{
  "version": "1.0",
  "backend": "xcelium-mcp",
  "sim_dir": "~/git.clone/venezia-t0/design/top/sim/ncsim",
  "tests": {
    "TOP015": {
      "phase": "analyze",
      "result": "FAIL",
      "dump_path": "~/...dump/ci_top_VENEZIA_TOP015_....shm",
      "csv_path": "/tmp/TOP015_check.csv",
      "log_summary": "Errors: 6 | PASS: 0 | FAIL: 6",
      "fail_signals": ["r_regAddr at 8318143ns"],
      "fail_type": "data_mismatch",
      "origin_chain": {
        "run": { "dump_path": "...", "log": "..." },
        "analyze": { "csv_path": "...", "anomaly_time_ns": 8318143 },
        "debug": { "root_cause": "", "analysis_ref": "" }
      },
      "updated_at": "2026-04-03T10:30:00"
    }
  },
  "regression": {
    "last_run": "2026-04-03T10:20:00",
    "pass_rate": "4/5",
    "fail_tests": ["TOP015"]
  }
}
```

**Phase 전이**:

```
idle → run → analyze → debug → fixed → (run 재진입)
                 │
                 └─ PASS → 완료 (debug 건너뜀)
```

**갱신 주체**: Backend의 compound operation이 실행 후 자동 갱신.

### 5.2 TB 분석서 YAML Frontmatter

**출처**: compound-engineering document-driven state

기존 `.ai/analysis/tb_*.analysis.md`에 YAML frontmatter 추가. Skill이 파싱하여 compound operation 파라미터를 자동 구성.

```markdown
---
test: VENEZIA_TOP015_i2c_8bit_offset_test
short_name: TOP015
env: ncsim-legacy
pass_signals:
  - top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_regAddr
  - top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_streamRwState
  - top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_loopState
  - top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_startStopDetState
fail_conditions:
  - "r_regAddr==0xAA"
  - "r_regAddr==0x55"
sim_duration: "15ms"
last_verified: 2026-04-01
---
```

**Fallback**: frontmatter 없으면 AI가 본문을 읽어 판단 (기존 방식).

### 5.3 Next-Skill 자동 제안

**출처**: bkit SKILL.md `next-skill:` + skill-orchestrator post-execution

| 완료 | 결과 | 제안 |
|------|------|------|
| run | PASS | `/sim run --regression` |
| run | FAIL | `/sim analyze {test}` |
| analyze | 원인 특정 | `/sim debug {test}` |
| analyze | 불확정 | `/sim analyze --signals ...` |
| debug | 수정안 도출 | `verilog-rtl` → `/sim verify {test}` |
| debug | bridge 필요 | `/sim debug {test} --bridge` |
| verify | PASS | 완료 |
| verify | FAIL | `/sim debug {test}` |

### 5.4 조건부 도구 선택 — FAIL 유형 자동 분류

**출처**: compound-engineering 파일 패턴 → agent 자동 트리거

| FAIL 유형 | 자동 판별 기준 | 전략 | 도구 |
|-----------|--------------|------|------|
| `data_mismatch` | `FAIL: read-back != expected` | CSV 값 비교 | `analyze_waveform` |
| `timeout` | 로그에 PASS/FAIL 없음 | Bridge mode | `connect + watch_signal` |
| `assertion` | `UVM_ERROR`, `$error` | 해당 시점 CSV | `analyze_waveform` |
| `protocol` | `protocol error`, checker | 프로토콜 신호 | `analyze_waveform` |
| `coverage` | coverage < threshold | Coverage 분석 | (향후) |

### 5.5 Task Blocking Chain

**출처**: bkit skill-orchestrator blockedBy

`/sim verify` 실행 시:

```
[Run] TOP015       → in_progress
[Analyze] TOP015   → pending (blockedBy: Run)
[Debug] TOP015     → pending (blockedBy: Analyze, FAIL시만)
[Verify] TOP015    → pending (blockedBy: Analyze 또는 Debug)
```

### 5.6 Origin Linking

**출처**: compound-engineering Brainstorm→Plan 참조 체인

sim-state.json의 `origin_chain`에 각 단계 산출물을 기록. debug 결과가 analyze의 CSV, run의 dump를 참조하여 추적 가능한 분석 체인 구성.

### 5.7 Parallel FAIL Analysis

**출처**: compound-engineering parallel subagent

regression 복수 FAIL 시 Agent 병렬 분석:
- FAIL 2개 이상 → 각각 Agent("analyze {test}") 병렬 실행
- Agent는 텍스트만 반환, orchestrator가 취합
- 공통 패턴 도출 ("두 FAIL 모두 CHK_ADR 관련")
- 최대 3개 병렬, 나머지 순차

---

## 6. Hook 자동화 (Layer 1)

### 6.1 왜 Hook이 필요한가

| 없으면 (현재) | 있으면 |
|-------------|-------|
| compound tool 실행 후 수동으로 다음 단계 판단 | PostToolUse가 phase 전환 자동 제안 |
| 사용자가 "시뮬레이션" 입력 시 Skill 수동 호출 | UserPromptSubmit가 자동 트리거 |

**SessionStart는 사용하지 않는다** — 시뮬레이션 무관 세션에서 토큰 낭비. 상태 확인은 `/sim status`로 on-demand 조회.

### 6.2 Hook 구성

```json
{
  "PostToolUse": [{
    "matcher": "mcp__xcelium-mcp__run_and_check|mcp__xcelium-mcp__analyze_waveform|mcp__xcelium-mcp__regression_summary",
    "command": "node ${SKILL_ROOT}/hooks/sim-post-compound.js"
  }],
  "UserPromptSubmit": [{
    "command": "node ${SKILL_ROOT}/hooks/sim-prompt-detect.js"
  }]
}
```

### 6.3 각 Hook의 역할

#### `sim-post-compound.js`

```
입력: tool_name, tool_output (compound tool 실행 결과)
동작:
  1. CompoundResult 파싱
  2. sim-state.json phase 갱신 확인
  3. next-skill 결정
출력 (additionalContext):
  "TOP015 FAIL 확인. → /sim analyze TOP015 권장"
```

#### `sim-prompt-detect.js`

```
입력: user_prompt (사용자 메시지)
동작: 키워드 매칭 ("시뮬레이션", "FAIL", "regression", "TOP0XX" 등)
출력 (additionalContext):
  "시뮬레이션 관련 요청 감지. /sim skill 사용 권장."
```

### 6.4 Hook 파일 구조

```
~/.claude/skills/xcelium-sim/
├── SKILL.md
├── hooks/
│   ├── sim-post-compound.js     ← PostToolUse (compound tool 실행 후)
│   └── sim-prompt-detect.js     ← UserPromptSubmit (키워드 감지)
└── references/
    ├── run-guide.md
    ├── analyze-guide.md
    ├── debug-guide.md
    ├── tool-map.md
    └── backend-interface.md
```

---

## 7. CLI Commands (Layer 2)

### 7.1 설계 원칙

- Backend별 CLI — `xcelium-mcp run`, `vcs-mcp run` (향후)
- 공유 CompoundResult 출력 형식 — 어떤 backend든 동일한 `[TAG]` 형식
- `compound.py`에 로직 1번 작성, CLI와 MCP tool이 공유

### 7.2 xcelium-mcp CLI

```bash
xcelium-mcp                          # 기존 MCP server (하위호환)
xcelium-mcp run TOP015 [옵션]        # 실행 + 로그 + CSV
xcelium-mcp analyze [옵션]           # CSV 추출 + 검색
xcelium-mcp regression [옵션]        # regression + 요약
```

**출력 형식** (모든 backend 공통):

```
[RUN] VENEZIA_TOP015_i2c_8bit_offset_test
[LOG] Errors: 0 | PASS: 6 | FAIL: 0
[DUMP] ~/...dump/ci_top_VENEZIA_TOP015_....shm
[CSV] /tmp/TOP015_check.csv  (4 signals, 8300-8500ns)
[RESULT] PASS
```

### 7.3 CLI ↔ MCP 공유 구조

```
src/xcelium_mcp/
├── compound.py          ← compound operation 핵심 로직
├── sim_state.py         ← sim-state.json 관리
├── cli.py               ← argparse → compound.py
├── tools/compound.py    ← MCP tool → compound.py
└── server.py            ← sys.argv 분기 + MCP 등록
```

---

## 8. 구현 계획

### 8.1 파일 변경 목록

| 파일 | 변경 | Layer |
|------|------|:-----:|
| `xcelium-mcp/src/xcelium_mcp/compound.py` | **신규**: CompoundResult + 3 compound 함수 | L3 |
| `xcelium-mcp/src/xcelium_mcp/sim_state.py` | **신규**: sim-state.json CRUD + phase 전이 | L3 |
| `xcelium-mcp/src/xcelium_mcp/cli.py` | **신규**: argparse CLI | L2 |
| `xcelium-mcp/src/xcelium_mcp/tools/compound.py` | **신규**: MCP tool 3개 | L3 |
| `xcelium-mcp/src/xcelium_mcp/server.py` | **수정**: sys.argv 분기 + compound 등록 | L2+L3 |
| `~/.claude/skills/xcelium-sim/SKILL.md` | **신규**: /sim skill | L4 |
| `~/.claude/skills/xcelium-sim/references/*.md` | **신규**: 5개 reference | L4 |
| `~/.claude/skills/xcelium-sim/hooks/*.js` | **신규**: 2개 hook (PostToolUse, UserPromptSubmit) | L1 |
| `{project}/.ai/analysis/tb_*.analysis.md` | **수정**: YAML frontmatter 추가 | — |
| `venezia-fpga/CLAUDE.md` | **수정**: `/sim` skill 안내로 간소화 | — |

### 8.2 구현 순서

```
Phase A: Backend 공유 로직 (xcelium-mcp)
  A-1. CompoundResult dataclass
  A-2. sim_state.py — CRUD + phase 전이
  A-3. run_and_check() + sim-state 갱신
  A-4. analyze_waveform() + sim-state 갱신
  A-5. regression_summary() + sim-state 갱신

Phase B: CLI + MCP Compound Tools (xcelium-mcp)
  B-1. server.py sys.argv 분기
  B-2. cli.py argparse (run/analyze/regression)
  B-3. tools/compound.py register() — 3 MCP tools
  B-4. server.py Phase 5 등록

Phase C: /sim Skill
  C-1. SKILL.md — subcommand + next-skill-map + 트리거
  C-2. references/run-guide.md
  C-3. references/analyze-guide.md + FAIL 유형 분류표
  C-4. references/debug-guide.md + origin linking
  C-5. references/tool-map.md
  C-6. references/backend-interface.md
  C-7. TB 분석서 YAML frontmatter 추가 (tb_TOP012~016)

Phase D: Hook 자동화
  D-1. hooks/sim-post-compound.js — phase 전환 제안
  D-2. hooks/sim-prompt-detect.js — 키워드 자동 트리거

Phase E: CLAUDE.md + 검증
  E-1. CLAUDE.md 시뮬레이션 섹션 간소화
  E-2. CLI: xcelium-mcp run TOP015 (cloud0)
  E-3. MCP: run_and_check + sim-state 갱신 확인
  E-4. Skill: /sim run → analyze → debug → verify e2e
  E-5. Skill: /sim verify --regression + parallel FAIL
  E-6. Hook: PostToolUse phase 전환 + UserPromptSubmit 트리거 확인
  E-7. 기존 24 tool 하위호환 확인
```

### 8.3 의존성

```
Phase A ──→ Phase B (CLI/MCP는 compound.py 필요)
       ──→ Phase C (Skill은 compound tool 존재 가정)
Phase C ──→ Phase D (Hook은 Skill 구조 참조)
Phase A~D ──→ Phase E (검증은 모든 구현 후)
```

---

## 9. CLAUDE.md 변경 계획

### 변경 후 (15줄)

```markdown
## RTL Simulation & Verification

시뮬레이션 실행·분석·디버깅은 `/sim` skill 사용:

- `/sim run TOP015` — 단일 테스트 실행 + 결과 확인
- `/sim run --regression` — 전체 regression
- `/sim analyze TOP015` — 결과 분석 (로그 + CSV + coverage)
- `/sim debug TOP015` — FAIL 원인 추적
- `/sim verify TOP015` — 실행→분석→(디버깅) 자동 체이닝

세부 도구 제어 필요 시 개별 xcelium-mcp tool 직접 사용.
운용 가이드: `.ai/knowledge/mcp-operations-guide.md`

CLI (AI 없이 직접 실행):
- `xcelium-mcp run TOP015 --csv`
- `xcelium-mcp regression --csv-on-fail`
```

---

## 10. 향후 확장

### 10.1 추가 Backend

| Backend | 시뮬레이터 | Dump 형식 | 구현 시점 |
|---------|----------|----------|----------|
| xcelium-mcp | Xcelium/SimVision | SHM | **현재** |
| vcs-mcp | VCS/Verdi | VPD/FSDB | 필요 시 |
| verilator-mcp | Verilator | VCD/FST | 필요 시 |
| iverilog-mcp | Icarus Verilog | VCD | 필요 시 |

새 backend 추가 시:
1. `compound.py` — CompoundResult 반환하는 3개 함수 구현
2. `sim_state.py` — 동일 모듈 재사용 (시뮬레이터 독립적)
3. `/sim` Skill — 변경 없음 (backend interface만 사용)

### 10.2 추가 Skill 연계

| 기존 Skill | 연계 방식 |
|-----------|----------|
| `verilog-rtl` | `/sim debug` → 수정 제안 시 자동 전환 |
| `chip-verification` | UVM 환경 구축 시 `/sim`과 연계 |
| `uvm-verification` | UVM sequence/agent 작성 후 `/sim run`으로 검증 |
| `lattice-fpga` | FPGA 합성 후 gate-level `/sim run --mode gate` |

### 10.3 Coverage 통합 (향후)

```
/sim analyze --coverage TOP015    ← UVM functional coverage 분석
/sim analyze --coverage --regression  ← regression 전체 coverage 합산
```

Backend가 coverage report 경로를 CompoundResult.details에 포함하면 Skill이 파싱.

---

## Success Criteria

### Definition of Done

- [ ] `/sim run|analyze|debug|verify|status` 5개 subcommand가 xcelium-mcp backend로 정상 동작 (§8.2 E-4~E-5)
- [ ] 기존 24개 개별 tool 전량 하위호환 확인 (§8.2 E-7)
- [ ] `compound.py`가 `batch_runner.py`/`csv_cache.py` 기존 함수를 호출만 하고 로직을 재구현하지 않음 — 코드 리뷰로 확인 (§3.4)
- [ ] `sim-state.json`이 compound 실행마다 자동 갱신되고, `/sim status`로 cross-session 복구 가능 (§4.1, §5.1)
- [ ] TB frontmatter 없는 기존 `.ai/analysis/tb_*.analysis.md`도 fallback으로 정상 동작 (§5.2)

### Quality Criteria

- [ ] `pytest tests/` 전체 스위트 회귀 없음 (도입 전 472 passed 기준선 유지)
- [ ] `ruff check src/` clean
- [ ] Phase D(Hook) 도입 전, Phase A-C만으로 e2e 수동 검증 통과 (§8.2 E-2~E-5)

---

## Impact Analysis

> 이 plan은 `server.py`(기존 MCP 진입점)에 sys.argv 분기를 추가하고 기존 24 tool 위에 compound 계층을 얹는 변경이므로, 기존 소비자 인벤토리를 명시한다.

### Changed Resources

| Resource | Type | Change Description |
|----------|------|---------------------|
| `src/xcelium_mcp/server.py` | MCP entry point | sys.argv 분기 추가 (`len(sys.argv) > 1` → CLI, else → 기존 MCP stdio) |
| 기존 24 tool | MCP tool | 변경 없음 — compound tool 3개만 신규 추가 |
| `src/xcelium_mcp/` 패키지 | Python module | `compound.py`, `sim_state.py`, `cli.py` 신규 파일 추가 |

### Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `server.py` entry point | INVOKE (인자 없음) | Claude Desktop/Code MCP config (`"command": "xcelium-mcp"`, CLAUDE.md Deployment) | None — `len(sys.argv) > 1` 가드가 정확하면 기존 무인자 실행 경로는 변경 없음 |
| `server.py` entry point | INVOKE (인자 있음) | (신규) CLI `xcelium-mcp run/analyze/regression` | 신규 경로, 기존 소비자 없음 |
| 기존 24 tool | CALL | Claude Code MCP `tool_use` (bridge 모드 개별 tool 직접 호출) | None — compound tool은 추가일 뿐 기존 tool 제거·시그니처 변경 없음 |
| `xcelium_mcp` 패키지 | IMPORT | `pytest tests/` (472 tests) | Needs verification — `compound.py`/`cli.py` 추가가 기존 import 그래프에 순환참조를 만들지 않는지 확인 필요 |

### Verification

- [ ] Claude Desktop/Code 설정으로 무인자 실행 시 기존 MCP stdio 동작 불변 확인
- [ ] 기존 24 tool 전체 `pytest` 회귀 없음
- [ ] `cli.py`/`compound.py` 추가 후 `python -m pytest --collect-only` 로 import 순환 없음 확인

---

## 11. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| CLI EDA 환경변수 미설정 | batch 실행 실패 | compound.py가 login_shell_cmd 재사용 |
| Compound 중간 실패 | 부분 결과 손실 | CompoundResult `PARTIAL` + 실패 단계 명시 |
| Skill trigger 미동작 | 수동 `/sim` 필요 | SKILL.md trigger + CLAUDE.md + Hook |
| sys.argv 분기로 MCP 깨짐 | 기존 MCP 불가 | `len(sys.argv) > 1` 조건만 분기 |
| sim-state.json 동시 접근 | 상태 충돌 | 단일 사용자, lock 불필요 |
| TB frontmatter 형식 불일치 | Skill 파싱 실패 | 없으면 AI 본문 읽기 fallback |
| 병렬 FAIL analysis 과부하 | Agent 과다 | 최대 3개 병렬, 나머지 순차 |
| Hook JS 유지보수 부담 | 추가 언어 | Hook 2개뿐, 로직 최소화 |
| Backend 추상화 과설계 | 현재 xcelium만 | interface만 정의, 구현은 xcelium만 |
| origin chain stale | 오래된 참조 | RTL 수정 후 verify 시 chain 초기화 |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-03 | 초안: 3-Layer, `/debug` 단일 skill |
| 0.2 | 2026-04-03 | `/sim` 리네이밍, 3가지 목적 + verify, subcommand 구조 |
| 0.3 | 2026-04-03 | 워크플로우 패턴 7개 추가 (bkit/compound 분석) |
| 1.0 | 2026-04-03 | 전면 재구성: 범용 HW 검증 프레임워크. 5-Layer, Backend Interface, Hook(3개 JS), 향후 확장 |
| 1.1 | 2026-04-03 | SessionStart hook 제거 → `/sim status` on-demand. Hook 2개로 축소 |
| **1.2** | **2026-04-03** | **context:fork 불채택 확정. `/sim status`가 cross-session 복구 + Agent 시작 시 context 로드를 모두 담당. fork/injection/--from 불필요, 단순한 "status 먼저 → 원하는 subcommand" 패턴으로 통일** |
| **1.3** | **2026-07-02** | **venezia-fpga → xcelium-mcp로 migration. 파일명 `debug-workflow-v2.plan.md` → `xcelium-mcp-debug-workflow-v2.plan.md` (predecessor 문서 및 이 프로젝트의 plan 문서 컨벤션과 일치). Layer 2/3 구현 대상(`compound.py`, `sim_state.py`, `cli.py`, `server.py`)이 모두 이 저장소 소속이므로 계획 소유권도 이관** |
| **1.4** | **2026-07-02** | **bkit plan 템플릿 준수 리뷰 반영: (1) "49 tool" 4곳 → 24로 수정(v4.2 restructure 이후 실제 수치); (2) `{project}` 표기 규약 명시(xcelium-mcp 자신이 아니라 호출 측 RTL 프로젝트); (3) §3.4에 compound.py가 기존 batch_runner.py/csv_cache.py를 wrap하는 것이지 재구현이 아님을 명시; (4) Context Anchor·Requirements(FR-01~08)·Success Criteria·Impact Analysis 섹션 신규 추가(템플릿 필수 섹션 보강); (5) FR-07(Backend Interface 범용화)·Phase D(Hook)를 YAGNI/후행 후보로 명시적 표기** |
| **1.5** | **2026-07-02** | **FR-09 추가(tool-usage-guide skill 분리, `/sim` skill과 독립) + `## Dependencies` 섹션 신규: v5.1(runner-abstraction, Draft·미구현)과의 관계를 "선행 필수 아님, ncsim legacy는 v4.1 기반으로 지금 착수 가능, '환경 독립적' 주장만 v5.1이 실질 필요"로 명시. tool-usage-guide skill도 Layer 3/4 완성과 무관하게 즉시 병행 착수 가능함을 명시** |
| **1.6** | **2026-07-02** | **tool-usage-guide skill과 통합 결정** — "독립적인 별도 skill"에서 "같은 `~/.claude/skills/xcelium-sim/` 디렉터리의 Phase 1/Phase 2"로 관계 재정의(FR-09, §Dependencies 갱신). tool-usage-guide plan에서 skill 배포 위치가 project-level에서 user-level로 변경된 것과 일치시킴 — RTL 프로젝트(venezia-fpga) 세션에서 실제로 로드되려면 user-level이 필수라는 점 재확인 |
| **1.7** | **2026-07-03** | **§4.5 `debug` (RTL) 매핑 갱신** — "verilog-rtl skill 연계"를 `verilog-rtl-debugger` agent(chip-design-skills 신설 예정) 호출로 구체화, `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조(v2.4)·`xcelium-mcp-tool-usage-guide` FR-12와 일관성 맞춤 |
| **1.8** | **2026-07-03** | **Design 문서 분리 결정** — tool-usage-guide와 Design을 병합할지 여부 확정: **별도 유지**. 이 문서의 Design은 compound.py(Layer 3) 완성 후 착수, tool-usage-guide Design이 남길 SKILL.md 확장점에 맞춰 진행 |
| **1.9** | **2026-07-03** | **소스 재검증 감사(변경 없음 확인)** — Design 착수 전 §3.4에서 재사용을 전제하는 `batch_runner.py`(`run_batch_single`/`run_batch_regression`)와 `csv_cache.py`(`extract`/`bisect_signal_dump`)가 실제 소스에 정확히 그 이름으로 현재도 존재함을 재확인. `runner_detection.py`도 존재 확인(§Dependencies v5.1 관계 서술 유효). 수정 사항 없음 — 정합성 확인만 기록 |

---

## Migration Note

이 문서는 원래 `venezia-fpga/docs/01-plan/features/debug-workflow-v2.plan.md`에 있었으나, 2026-07-02 xcelium-mcp 저장소로 이관되었다. Predecessor 문서(`xcelium-mcp-debugging-workflow.plan.md`)와 구현 대상 파일(§8.1)이 모두 이 저장소 소속이라 계획 소유권을 이 프로젝트의 bkit PDCA 사이클로 옮기는 것이 맞다. venezia-fpga 쪽 원본은 삭제되었다(git 히스토리로 복구 가능).
