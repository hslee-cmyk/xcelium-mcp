# Plan: `/sim` — Verilog HW Verification Workflow

> **Feature**: Verilog 하드웨어 디자인 검증 워크플로우
>
> **Date**: 2026-04-03
> **Status**: Draft v1.14
> **Predecessor**: `xcelium-mcp-debugging-workflow.plan.md` — Phase 0~5 상세, TB 캐시, 실전 히스토리
> **Scope**: 시뮬레이터 독립적 범용 HW 검증 프레임워크. 첫 번째 백엔드: xcelium-mcp

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Verilog HW 검증은 시뮬레이션 실행·결과 분석·디버깅의 반복이지만, 시뮬레이터별로 도구와 절차가 다르고 표준화된 워크플로우가 없다. 현재 xcelium-mcp의 25개 tool을 개별 호출하는 방식은 세션당 10~20회 trigger가 필요하며, 다른 시뮬레이터로의 확장이 불가능 |
| **Solution** | 5-Layer 범용 검증 프레임워크: `/sim` Skill(워크플로우 orchestration) + Simulator Backend 추상화(교체 가능) + Compound Operations(기계적 시퀀스 1-call) + CLI(사용자 직접 실행) + Hook 자동화(phase 전환·상태 주입) |
| **Function UX Effect** | `/sim verify TOP015` 한 번으로 실행→분석→디버깅 자동 체이닝. 시뮬레이터가 바뀌어도 동일한 `/sim` 명령 사용. CLI로 AI 없이 직접 실행 가능 |
| **Core Value** | 시뮬레이터 독립적 검증 워크플로우 표준화, legacy/UVM/SV 테스트벤치 모두 대응, tool trigger 60% 감소, 프로젝트 간 동일 경험 |

---

## Context Anchor

> Auto-generated per bkit plan template. Design/Do 문서로 전파됨.

| Key | Value |
|-----|-------|
| **WHY** | 시뮬레이터별 도구·절차가 제각각이라 표준화된 검증 워크플로우가 없고, 25개 개별 tool을 세션마다 개별 호출해야 해서 trigger가 과다함 |
| **WHO** | xcelium-mcp로 RTL 검증을 수행하는 AI 에이전트 및 엔지니어 (현재 소비 프로젝트: `venezia-fpga`) |
| **RISK** | `compound.py`가 기존 batch/CSV 로직을 재구현하면 이미 검증된 경로(617 tests)와 별개로 새 버그 표면이 생김(§3.4 주의) — sys.argv 분기 리스크는 2026-07-08 CLI를 독립 console_script로 재설계하며 해소됨(§7.1, §11) |
| **SUCCESS** | `/sim verify {test}` 1회 호출로 run→analyze→(debug) 자동 체이닝; 기존 25 tool 전량 하위호환 유지; tool trigger 세션당 60% 감소 |
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
| FR-05 | sim-state.json: 테스트별 phase/결과/origin_chain 영속 상태 추적(§5.1, 저장 위치는 신규 파일 vs `registry.py` 확장 중 Phase 2 Design에서 결정 — 2026-07-08 §5.1 각주 참조) | High | Pending |
| FR-06 | CLI: AI 없이 `xcelium-mcp-cli run/analyze/regression` 직접 실행(§7, 2026-07-08 갱신 — `server.py` 공유 entry point가 아니라 독립 console_script) | Medium | Pending |
| FR-07 | Backend Interface: compound operation 3종 규격화(§3) — **YAGNI 후보**, 두 번째 backend(vcs-mcp 등) 착수 전까지 interface만 정의하고 범용화 자체는 보류 검토 | Low | Pending |
| FR-08 | Hook 자동화: PostToolUse phase 전환 제안 + UserPromptSubmit 키워드 트리거(§6) — Phase D, A-C 검증 후 후행 | Medium | Pending |
| FR-09 | Tool 사용법 가이드(25개 raw tool의 phase별 선택 매트릭스, `references/tool-map.md` 상당)는 **이 `/sim` skill과 동일한 `~/.claude/skills/xcelium-sim/` 디렉터리의 Phase 1**로 구현 — compound.py(Layer 3) 완성을 기다리지 않고 즉시 착수 가능. 상세 plan: `xcelium-mcp-tool-usage-guide`(§Dependencies 참조, v0.2에서 별도 skill 안이 이 skill로 통합됨) | High | Pending (선행 feature, Phase 1) |

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
- **Phase 1 완료 확인(2026-07-08, `xcelium-mcp-tool-usage-guide.design.md` 대조)**: Phase 1은 실제로 완료됐다(matchRate 98%). 단, 이 문서(v2)가 애초에 가정했던 §4.3의 가상 파일 구조(`run-guide.md`/`analyze-guide.md`/`debug-guide.md`/`backend-interface.md`)는 **실제 Phase 1 산출물과 이름이 다르다** — 실제로는 `phase-0-discovery.md`~`phase-5-fix-regression.md`(6개, 원본 워크플로우 Phase와 1:1 대응이 이미 완료됨) + `tool-map.md` + `server-ops.md`(2026-07-08 F-184 추가, 원격 supervisor 운영)로 총 8개다. §4.3을 이 실제 구조 기준으로 갱신했다(아래 참조) — Phase 2는 새 파일을 만드는 게 아니라 이 8개 파일에 subcommand 라우팅을 얹기만 하면 된다.
- **Tool 개수는 고정값이 아니라 감사 시점의 스냅샷이다(FR-11과 동일 원칙)**: 이 문서 전체의 "25개"는 2026-07-08 `grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py` 기준 실측치다(F-3 `list_active_sessions` 추가로 24→25). Phase 2 Design 착수 시점에 반드시 같은 방식으로 재감사할 것 — 이 숫자를 프로즈로 베끼지 말고 매번 소스에서 직접 셀 것.
- **`verilog-rtl-debugger` agent 현황 갱신**: §4.5에서 "신설 예정"으로 표기했던 이 agent는 이제 실제로 존재한다(chip-design-skills 배포) — 아래 §4.5도 갱신했다.

### Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|--------------------|
| Backward Compatibility | 기존 25 tool 100% 하위호환, `pytest` 617 tests 회귀 없음 | `pytest tests/ -v` 전체 스위트 |
| Efficiency | tool trigger 세션당 60% 감소 | 도입 전/후 세션 로그 tool_use 횟수 비교 |
| Maintainability | `compound.py`가 기존 `batch_runner.py`/`csv_cache.py` 재사용, 로직 중복 없음 | 코드 리뷰 (§3.4) |

---

## 1. 비전: HW 검증 워크플로우 표준화

### 1.1 현재 상황

```
프로젝트 A (venezia-fpga)         프로젝트 B (미래 ASIC)
├─ Xcelium + ncsim legacy         ├─ VCS + UVM
├─ xcelium-mcp 25 tools           ├─ (도구 없음)
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
Layer 2: CLI Commands (backend별, server.py와 무관한 독립 entry point — §7.1)
    │  xcelium-mcp-cli run / analyze / regression
    │  (향후) vcs-mcp-cli run / analyze / regression
    │
Layer 1: Hook 자동화 (Claude Code plugin)
    │  PostToolUse: phase 자동 전환, next-skill 제안
    │  UserPromptSubmit: 자동 트리거 키워드
    │
Layer 0: 개별 MCP Tools (backend별)
    xcelium-mcp 25 tools, (향후) vcs-mcp N tools, ...
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
    "individual_tools": 25,
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
├── cli.py                   ← CLI argparse → compound.py 호출. 독립 entry point(§7.1 갱신 참조) — server.py의 sys.argv 분기 아님
├── tools/compound.py        ← MCP tool 3개 → compound.py 호출
└── server.py                ← compound tool 3개 register() 추가만 — CLI 관련 변경 없음
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

### 4.3 Skill 파일 구조 (2026-07-08, 실제 Phase 1 산출물 기준으로 갱신)

> **v1.9 이전 버전과의 차이**: 이 절은 원래(v1.0~v1.9) `run-guide.md`/`analyze-guide.md`/`debug-guide.md`/`backend-interface.md`라는 **가상의** 파일명을 가정했었다. 실제 Phase 1(`xcelium-mcp-tool-usage-guide`, 완료·matchRate 98%)은 다른 이름으로 이미 이 매핑을 완료했으므로, 아래는 **실제 소스(`skill-src/xcelium-sim/`) 대조 결과**로 전면 교체한 것이다. Phase 2는 새 reference 파일을 만드는 게 아니라 기존 파일들 위에 subcommand 라우팅만 추가한다.

```
skill-src/xcelium-sim/                    (git 정본, cp -r로 ~/.claude/skills/에 배포)
├── SKILL.md                              ← Phase 1(tool 사용법) + Phase 2 확장점 마커 두 섹션 보유(§4.4 참조)
└── references/
    ├── phase-0-discovery.md              ← 검증 환경 인프라 분석(TB 캐시, 공유 컴포넌트)
    ├── phase-1-analysis.md               ← 사전 분석(RTL 분석서, dump scope)
    ├── phase-2-simulation.md             ← 시뮬레이션 실행(Batch/Bridge)
    ├── phase-3-triage.md                 ← 1차 판별(로그 기반)
    ├── phase-4-waveform.md               ← 2차 판별(waveform CSV, bisect, FSM 전이 대조)
    ├── phase-5-fix-regression.md         ← 수정+Regression+세션 종료 정리
    ├── tool-map.md                       ← 25개 tool 전체 결정 매트릭스(전 phase 공통 참조)
    └── server-ops.md                     ← (2026-07-08, F-184 추가) 원격 supervisor 코드 반영 확인+재기동 운영 절차
```

**원본 워크플로우 참조**: `xcelium-mcp-debugging-workflow.plan.md`(원본 6-phase 방법론)와 이미 1:1 대응 완료.

| Reference | 원본 Phase | 비고 |
|-----------|-----------|------|
| `phase-0-discovery.md` | Phase 0 | TB 캐시 규칙, 공유 컴포넌트 분석 |
| `phase-1-analysis.md` | Phase 1 | RTL 분석서, dump scope 결정 |
| `phase-2-simulation.md` | Phase 2 | Batch/Bridge 실행 선택 |
| `phase-3-triage.md` | Phase 3 | 로그 기반 1차 판별 |
| `phase-4-waveform.md` | Phase 4 | waveform CSV 2차 판별, FSM 대조 |
| `phase-5-fix-regression.md` | Phase 5 | 수정 + regression + 세션 종료 정리 |
| `tool-map.md` | 전체 | 25개 tool(2026-07-08 기준, §Dependencies 각주 참조) 결정 매트릭스 |
| `server-ops.md` | (신규, 원본 워크플로우에 없던 항목) | xcelium-mcp 서버 운영(재기동) — Phase 2 범위가 아닌 인프라 문서지만 같은 skill에 위치 |

Phase 2가 추가할 것은 새 reference 파일이 아니라, 위 8개 파일이 이미 구성해 놓은 phase 판단 로직 위에 `/sim run|analyze|debug|verify|status` subcommand가 "어떤 phase reference를 언제 자동으로 골라 로드할지"를 결정하는 라우팅 계층뿐이다 — `backend-interface.md`(compound operation 인터페이스 정의)만 Phase 2가 실제로 신규 추가하는 유일한 reference 파일이 된다.

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
| `debug` (RTL) | — | 로컬에 설치된 `verilog-rtl-debugger` agent 호출(2026-07-08 확인: 생성 완료, chip-design-skills가 install.py로 배포 중 — mcp__xcelium-mcp__* 전 tool 접근 가능한 유일한 verilog-rtl-* agent), 필요 시 analyst/coder/reviewer/architect-advisor/prover로 추가 위임 |
| `verify` | 위 조합 (자동 체이닝) | — |
| `status` | — | mcp_config, ssh_run |

---

## 5. 워크플로우 패턴 (bkit/compound-engineering 기반)

### 5.1 sim-state.json — 테스트별 상태 추적

**출처**: bkit `lib/pdca/status.js`

> **`{project}` 표기 규약**: 이 문서 전체에서 `{project}`는 **xcelium-mcp를 사용하는 RTL 검증 프로젝트**(예: `venezia-fpga`)를 가리키며, xcelium-mcp 저장소 자신을 가리키지 않는다. `sim-state.json`과 TB 분석서 캐시(`.ai/analysis/`)는 항상 호출 측 RTL 프로젝트 루트에 위치한다. 이 plan 문서가 xcelium-mcp repo로 이관된 이후에도(§Migration Note) 이 구분은 변하지 않는다.

**위치**: `{project}/.ai/sim-state.json` (프로젝트별, Git 미추적)

> **2026-07-08 갱신 — 부분 중복 발견**: 이 plan(v1.0) 작성 이후 완료된 `xcelium-mcp-session-state-reattach`(F-D) feature가 `registry.py`의 `environments[sim_dir]` 엔트리에 이미 **sim_dir별 영속 상태 추적**을 만들어놨다 — `update_session_state(sim_dir, test_name, tb_source)`/`get_session_state(sim_dir)`로 `current_test_name`/`current_tb_source`를 저장·복원하며, `sim_bridge_run`이 쓰고 `connect_simulator`가 재연결 시 읽어 복원하는 경로까지 이미 동작 중이다. 즉 "sim_dir별 영속 상태를 담는 그릇" 자체는 이미 있다 — 다만 아래 JSON이 요구하는 `phase`/`result`/`fail_signals`/`fail_type`/`origin_chain` 같은 필드는 아직 없다. **Phase 2 Design 착수 시 반드시 먼저 결정할 것**: (a) 이 아래 스키마 그대로 완전히 별개의 새 파일(`sim-state.json`)을 만들지, (b) 이미 확립된 `registry.py`의 `environments[sim_dir]`을 확장해 위 필드들을 추가할지. 이 저장소가 F-C/F-D에서 이미 (b) 방향(단일 registry 확장)으로 진화해왔다는 점을 감안하면 (b)가 기존 컨벤션과 더 일치한다.

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

> **2026-07-08 갱신 — staleness 필드는 이미 더 나은 방법으로 대체됨**: 이 plan(v1.0)이 제안한 `last_verified: 2026-04-01`(사람이 수동 갱신하는 날짜)은, 이후 완료된 F-175로 이미 **자동화된 더 나은 방법**이 생겼다 — `tb_source`/`tb_provenance`의 `combined_sha256` 체크섬을 `sim_batch_run`/`sim_regression`이 매번 자동 계산해 분석서 헤더에 기록하고, 재사용 전 이 값만 비교해 신선도를 판정한다(수동 날짜 갱신 불필요, `references/phase-0-discovery.md` §0C 참조). 아래 예시에서 `last_verified` 필드는 제거하고 이 자동 체크섬 방식을 그대로 재사용한다 — `pass_signals`/`fail_conditions`의 구조화(YAML) 아이디어 자체는 여전히 유효하다.

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
tb_source: { files: [...], combined_sha256: "..." }   # F-175 자동 산출, 수동 갱신 불필요(위 참조)
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

> **2026-07-08 참고 (약한 발견, Phase D 착수 시 재검토)**: 아래는 hook을 JS(`node`)로 제안하지만, 이 사용자의 다른 환경(chip-design-skills)에서 확립된 hook 컨벤션은 전부 Python이다. Phase D는 이미 후행으로 미뤄져 있으므로 지금 강제 수정하지는 않으나, 실제 착수 시 언어 일관성을 재검토할 것.

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

### 6.4 Hook 파일 구조 (2026-07-08 갱신 — §4.3과 동일한 실제 구조로 정정)

> 이 다이어그램은 §4.3을 실제 Phase 1 산출물 기준으로 재작성할 때 함께 갱신했어야 했는데 누락됐던 중복 다이어그램이다 — §4.3과 반드시 같은 그림이어야 한다.

```
skill-src/xcelium-sim/                    (git 정본, cp -r로 ~/.claude/skills/에 배포)
├── SKILL.md
├── hooks/
│   ├── sim-post-compound.js     ← PostToolUse (compound tool 실행 후) — §6 참고 각주: 언어 일관성 재검토 대상
│   └── sim-prompt-detect.js     ← UserPromptSubmit (키워드 감지)
└── references/
    ├── phase-0-discovery.md
    ├── phase-1-analysis.md
    ├── phase-2-simulation.md
    ├── phase-3-triage.md
    ├── phase-4-waveform.md
    ├── phase-5-fix-regression.md
    ├── tool-map.md
    ├── server-ops.md                ← (2026-07-08, F-184) Phase 1 산출물, Phase 2와 무관
    └── backend-interface.md         ← Phase 2가 실제로 추가하는 유일한 신규 reference(§4.3 참조)
```

---

## 7. CLI Commands (Layer 2)

### 7.1 설계 원칙

- Backend별 CLI — `xcelium-mcp-cli run`, `vcs-mcp-cli run` (향후)
- 공유 CompoundResult 출력 형식 — 어떤 backend든 동일한 `[TAG]` 형식
- `compound.py`에 로직 1번 작성, CLI와 MCP tool이 공유
- **독립 entry point — `server.py`의 sys.argv 분기가 아니다(2026-07-08 갱신)**: 원래 이 문서는 CLI를 `server.py:main()` 안에서 `len(sys.argv) > 1` 분기로 얹는 안을 전제했으나, 이 plan 이후 실제로 완료된 `xcelium-mcp-server-process-lifecycle` feature가 이미 이 저장소의 실제 컨벤션을 확립해놨다 — `pyproject.toml [project.scripts]`에 `xcelium-mcp`(server:main) 외에 `xcelium-mcp-supervisor`(supervisor:main)/`xcelium-mcp-culler`(idle_culler:main)가 **각자 독립 console_script**로 등록돼 있고, `stdio_forward.py`/`sim_session_reaper.py`도 각자 독립 `-m` 모듈이다. 게다가 supervisor 배포 이후 MCP 연결은 `WorkerHandler.handle()`이 `_xcelium_server.main()`을 **fork 후 직접 함수 호출**하는 방식이라(subprocess 재실행이 아님), 연결별로 다른 sys.argv가 애초에 전달될 방법이 없다. 따라서 CLI는 `server.py`를 전혀 건드리지 않고 `xcelium-mcp-cli = "xcelium_mcp.cli:main"`을 새 console_script로 추가하는 것으로 스코프를 변경한다 — 이 저장소 자신이 이미 증명한 패턴("새 관심사 = 새 모듈 + 새 console_script")을 그대로 따르는 것이며, 부수적으로 §11의 "sys.argv 분기로 MCP 깨짐" 리스크 자체가 사라진다(server.py 무변경이므로).

### 7.2 xcelium-mcp-cli

```bash
xcelium-mcp                          # 기존 MCP server(하위호환, 이 feature로 인한 변경 전혀 없음)
xcelium-mcp-cli run TOP015 [옵션]    # 실행 + 로그 + CSV
xcelium-mcp-cli analyze [옵션]       # CSV 추출 + 검색
xcelium-mcp-cli regression [옵션]    # regression + 요약
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
├── compound.py          ← compound operation 핵심 로직(CLI와 MCP tool이 공유)
├── sim_state.py         ← sim-state.json 관리
├── cli.py               ← argparse → compound.py 호출. pyproject.toml에 xcelium-mcp-cli로 등록되는 독립 entry point
└── tools/compound.py    ← MCP tool 3개 → compound.py 호출(server.py가 register()만 추가)
```

`server.py`는 이 다이어그램에 없다 — compound tool 3개를 `register()`하는 것 외에 CLI 관련 변경이 전혀 없기 때문이다(§7.1).

---

## 8. 구현 계획

### 8.1 파일 변경 목록

| 파일 | 변경 | Layer |
|------|------|:-----:|
| `xcelium-mcp/src/xcelium_mcp/compound.py` | **신규**: CompoundResult + 3 compound 함수 | L3 |
| `xcelium-mcp/src/xcelium_mcp/sim_state.py` | **신규**: sim-state.json CRUD + phase 전이 | L3 |
| `xcelium-mcp/src/xcelium_mcp/cli.py` | **신규**: argparse CLI, `xcelium-mcp-cli` 독립 entry point(§7.1, 2026-07-08 갱신 — server.py 분기 아님) | L2 |
| `xcelium-mcp/src/xcelium_mcp/tools/compound.py` | **신규**: MCP tool 3개 | L3 |
| `xcelium-mcp/pyproject.toml` | **수정**: `[project.scripts]`에 `xcelium-mcp-cli = "xcelium_mcp.cli:main"` 추가(2026-07-08 신규 — 기존 `xcelium-mcp`/`xcelium-mcp-supervisor`/`xcelium-mcp-culler`와 동일 패턴) | L2 |
| `xcelium-mcp/src/xcelium_mcp/server.py` | **수정**: compound tool 3개 `register()` 추가만 — CLI 관련 변경 없음(2026-07-08 갱신, 이전엔 sys.argv 분기로 서술돼 있었음) | L3 |
| `~/.claude/skills/xcelium-sim/SKILL.md` | **수정**(2026-07-08 갱신 — Phase 1에서 이미 생성됨, 신규 아님): 기존 `<!-- PHASE 2 확장점 -->` 마커 아래에 subcommand 라우팅만 추가 | L4 |
| `~/.claude/skills/xcelium-sim/references/backend-interface.md` | **신규 1개**(2026-07-08 갱신 — 나머지 phase-0~5/tool-map.md/server-ops.md 7개는 Phase 1에서 이미 완료, §4.3 참조) | L4 |
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
  B-1. cli.py argparse (run/analyze/regression)
  B-2. pyproject.toml에 xcelium-mcp-cli console_script 등록(2026-07-08 갱신 — server.py 분기 아님, §7.1)
  B-3. tools/compound.py register() — 3 MCP tools
  B-4. server.py에 compound tool register() 호출 추가

Phase C: /sim Skill (2026-07-08 갱신 — run-guide/analyze-guide/debug-guide는 이미 phase-0~5.md로 완료돼 있어 C-2~C-4 제거, §4.3 참조)
  C-1. SKILL.md — 기존 `<!-- PHASE 2 확장점 -->` 마커 아래에 subcommand + next-skill-map + 트리거 추가
  C-2. references/backend-interface.md — Phase 2가 실제로 추가하는 유일한 신규 reference
  C-3. TB 분석서 YAML frontmatter 추가 (tb_TOP012~016, §5.2 갱신된 형식 — last_verified 아닌 tb_source.combined_sha256)

Phase D: Hook 자동화
  D-1. hooks/sim-post-compound.js — phase 전환 제안
  D-2. hooks/sim-prompt-detect.js — 키워드 자동 트리거

Phase E: CLAUDE.md + 검증
  E-1. CLAUDE.md 시뮬레이션 섹션 간소화
  E-2. CLI: xcelium-mcp-cli run TOP015 (원격 host)
  E-3. MCP: run_and_check + sim-state 갱신 확인
  E-4. Skill: /sim run → analyze → debug → verify e2e
  E-5. Skill: /sim verify --regression + parallel FAIL
  E-6. Hook: PostToolUse phase 전환 + UserPromptSubmit 트리거 확인
  E-7. 기존 25 tool 하위호환 확인
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
- `xcelium-mcp-cli run TOP015 --csv`
- `xcelium-mcp-cli regression --csv-on-fail`
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
- [ ] 기존 25개 개별 tool 전량 하위호환 확인 (§8.2 E-7)
- [ ] `compound.py`가 `batch_runner.py`/`csv_cache.py` 기존 함수를 호출만 하고 로직을 재구현하지 않음 — 코드 리뷰로 확인 (§3.4)
- [ ] sim-dir별 영속 상태(신규 `sim-state.json` 또는 `registry.py` 확장, §5.1 참조)가 compound 실행마다 자동 갱신되고, `/sim status`로 cross-session 복구 가능 (§4.1, §5.1)
- [ ] TB frontmatter 없는 기존 `.ai/analysis/tb_*.analysis.md`도 fallback으로 정상 동작 (§5.2)

### Quality Criteria

- [ ] `pytest tests/` 전체 스위트 회귀 없음 (도입 전 617 passed 기준선 유지)
- [ ] `ruff check src/` clean
- [ ] Phase D(Hook) 도입 전, Phase A-C만으로 e2e 수동 검증 통과 (§8.2 E-2~E-5)

---

## Impact Analysis

> **2026-07-08 갱신**: 이 plan은 원래 `server.py`(기존 MCP 진입점)에 sys.argv 분기를 추가하는 안이었으나, §7.1에서 확정한 대로 CLI는 이제 `xcelium-mcp-cli`라는 독립 console_script다 — `server.py`는 compound tool 3개 `register()` 추가만 받는다. 아래 표는 그에 맞춰 갱신했다. 또한 원격 배포 모델 자체가 이 plan 작성 이후 완료된 `xcelium-mcp-server-process-lifecycle` feature로 바뀌었다(supervisor+fork 구조, `deploy/README.md`) — "Claude Desktop/Code MCP config"의 실제 현재 형태는 xcelium-mcp 자신의 루트 `CLAUDE.md` "Deployment" 섹션에 아직 반영되지 않은 별개의 문서 부채이며, 이 표는 그 부채에 의존하지 않도록 일반화했다.

### Changed Resources

| Resource | Type | Change Description |
|----------|------|---------------------|
| `src/xcelium_mcp/cli.py` | Python module (신규) | argparse 기반 CLI, `xcelium-mcp-cli` 독립 entry point로 등록 — `server.py`와 무관 |
| `pyproject.toml` | `[project.scripts]` | `xcelium-mcp-cli = "xcelium_mcp.cli:main"` 신규 등록 (기존 `xcelium-mcp`/`xcelium-mcp-supervisor`/`xcelium-mcp-culler`와 동일 패턴) |
| `src/xcelium_mcp/server.py` | MCP entry point | compound tool 3개 `register()` 호출 추가만 — entry point 자체(`main()`)는 무변경 |
| 기존 25 tool | MCP tool | 변경 없음 — compound tool 3개만 신규 추가 |
| `src/xcelium_mcp/` 패키지 | Python module | `compound.py`, `sim_state.py`, `cli.py` 신규 파일 추가 |

### Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `server.py:main()` | INVOKE (MCP 연결 처리, 인자 없음) | 원격 supervisor(`xcelium_mcp.supervisor`)가 연결마다 fork 후 `_xcelium_server.main()`을 **직접 함수 호출**(subprocess 재실행 아님) — 이 경로는 CLI 분기가 애초에 필요 없다(§7.1) | None — `server.py`의 entry point 로직 자체를 건드리지 않으므로 영향 없음 |
| `xcelium-mcp-cli` (신규) | INVOKE | 사람이 원격/로컬 shell에서 직접 실행 — supervisor/MCP 경로와 완전히 분리된 별도 프로세스 | 신규 경로, 기존 소비자 없음, `server.py`와 프로세스 자체가 다름 |
| 기존 25 tool | CALL | Claude Code MCP `tool_use` (bridge 모드 개별 tool 직접 호출) | None — compound tool은 추가일 뿐 기존 tool 제거·시그니처 변경 없음 |
| `xcelium_mcp` 패키지 | IMPORT | `pytest tests/` (617 tests) | Needs verification — `compound.py`/`cli.py` 추가가 기존 import 그래프에 순환참조를 만들지 않는지 확인 필요 |

### Verification

- [ ] `server.py:main()`이 supervisor의 fork-후-직접호출 경로에서 compound tool 3개 register 추가 후에도 그대로 동작하는지 확인(사람이 별도로 argv를 넘길 방법이 없는 경로이므로, sys.argv 분기 자체가 아예 없다는 전제를 재확인하는 성격의 검증)
- [ ] `xcelium-mcp-cli` 신규 console_script가 supervisor/MCP 세션과 무관하게 독립적으로 동작 확인
- [ ] 기존 25 tool 전체 `pytest` 회귀 없음
- [ ] `cli.py`/`compound.py` 추가 후 `python -m pytest --collect-only` 로 import 순환 없음 확인

---

## 11. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| CLI EDA 환경변수 미설정 | batch 실행 실패 | compound.py가 login_shell_cmd 재사용 |
| Compound 중간 실패 | 부분 결과 손실 | CompoundResult `PARTIAL` + 실패 단계 명시 |
| Skill trigger 미동작 | 수동 `/sim` 필요 | SKILL.md trigger + CLAUDE.md + Hook |
| ~~sys.argv 분기로 MCP 깨짐~~ | ~~기존 MCP 불가~~ | **2026-07-08 제거**: CLI를 `xcelium-mcp-cli` 독립 console_script로 재설계(§7.1)해 `server.py`를 아예 건드리지 않으므로 이 리스크 자체가 해당 없음 |
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
| **1.10** | **2026-07-08** | **Phase 1 완료 반영(`xcelium-mcp-tool-usage-guide.design.md`, matchRate 98% 대조) — v1.0 이후 처음 실제 산출물 기준 갱신**: (1) tool 개수 24→25(`grep -c "@mcp.tool()"` 재감사, F-3 `list_active_sessions` 반영) — 전 문서 13개 지점 일괄 수정 + Dependencies에 "고정값 아님, 매번 재감사" 각주 추가; (2) `pytest` 기준선 472→617 tests(3개 지점); (3) **§4.3 전면 재작성** — v1.0~1.9가 가정했던 가상 파일명(`run-guide.md`/`analyze-guide.md`/`debug-guide.md`)을 실제 Phase 1 산출물(`phase-0-discovery.md`~`phase-5-fix-regression.md`+`tool-map.md`+`server-ops.md`, 8개)로 전면 교체 — Phase 2는 새 reference를 만드는 게 아니라 이 8개 위에 라우팅만 얹는 것으로 스코프 재정의(`backend-interface.md`만 Phase 2의 순수 신규 파일로 남음); (4) §4.5 `verilog-rtl-debugger` agent "신설 예정" → 생성 완료로 갱신 |
| **1.11** | **2026-07-08** | **CLI(FR-06) 설계를 실제 확립된 컨벤션에 맞게 재설계 — 논리적 충돌 발견 및 수정**: 이 plan 이후 실제로 완료된 `xcelium-mcp-server-process-lifecycle` feature(supervisor+fork 배포 모델, `pyproject.toml [project.scripts]`에 `xcelium-mcp-supervisor`/`xcelium-mcp-culler` 등 독립 console_script 3개 기존 확립)와 v1.0~1.10의 "CLI를 `server.py:main()` 안에 `sys.argv` 분기로 추가" 설계가 충돌함을 소스 재검증으로 발견. 근거: supervisor가 MCP 연결마다 `_xcelium_server.main()`을 fork 후 **직접 함수 호출**하므로(subprocess 재실행 아님) 연결별 sys.argv 전달 경로 자체가 없고, 이 저장소는 이미 "새 관심사 = 새 모듈 + 새 console_script" 패턴(`stdio_forward.py`/`sim_session_reaper.py`도 독립 `-m` 모듈)을 스스로 확립해놨다. **수정**: FR-06/§3.4/§7(전면)/§8.1/§8.2 B단계/Impact Analysis(Changed Resources·Current Consumers·Verification)/§11 리스크 테이블 전부 갱신 — CLI를 `server.py` 무변경의 독립 console_script `xcelium-mcp-cli`로 재설계, `server.py`는 compound tool 3개 `register()` 추가만 받도록 스코프 축소. "sys.argv 분기로 MCP 깨짐" 리스크 항목 제거(해당 없음). 부수적으로 CLAUDE.md(xcelium-mcp 자신) "Deployment" 섹션이 구 배포 모델(`"command": "xcelium-mcp"`)을 그대로 보여주는 별개의 문서 부채도 함께 확인(이 plan 범위 밖, Impact Analysis에서 그 부채에 의존하지 않도록만 일반화). |
| **1.12** | **2026-07-08** | **§5.1/§5.2 소스 재검증 — 이미 구현된 더 나은 메커니즘과의 중복 발견**: (1) §5.1 `sim-state.json`(신규 파일 제안)이 이 plan 이후 완료된 `xcelium-mcp-session-state-reattach`(F-D)와 부분 중복 — `registry.py`의 `environments[sim_dir]`에 이미 `current_test_name`/`current_tb_source` 영속 추적(`update_session_state`/`get_session_state`)이 구현·배포됨. Phase 2 Design 착수 시 "새 파일 vs 기존 registry 확장" 결정이 선행되어야 함을 각주로 명시(레포 자체가 F-C/F-D로 후자 방향 확립). (2) §5.2 TB YAML frontmatter의 `last_verified`(수동 날짜) 필드를 F-175의 자동 `tb_source.combined_sha256` 체크섬 방식(`phase-0-discovery.md` §0C)으로 교체 — 사람이 날짜를 갱신할 필요가 없는 이미 구현된 방법으로 대체. (3) §6 Hook 자동화에 언어 일관성 참고(JS 제안 vs 이 사용자 환경의 실제 Python hook 컨벤션) 각주 추가(약한 발견, Phase D 후행이라 지금 강제 수정 안 함). |
| **1.13** | **2026-07-08** | **문서 전체 커버리지 완료 — 나머지 미검증 섹션 확인**: §10(향후 확장)/Migration Note를 마저 검증 — 둘 다 forward-looking 또는 순수 사실 기록이라 수정 사항 없음(clean). §5.1 변경(v1.12)의 하위 영향으로 FR-05와 Success Criteria DoD가 여전히 "sim-state.json"을 확정된 파일명처럼 서술하던 것을 "신규 파일 vs registry.py 확장, §5.1 참조"로 정합화(본문 곳곳의 "sim-state.json" 개념적 표기 자체는 유지 — 실제 저장 메커니즘은 구현 세부사항으로 Phase 2 Design에 위임). 이로써 이 문서의 전 섹션(§1~11, Requirements, Dependencies, Impact Analysis, Success Criteria, Migration Note)에 대한 소스 재검증 커버리지 완료. |
| **1.14** | **2026-07-08** | **§6.4 Hook 파일 구조 중복 다이어그램 누락분 수정 + §8.2 Phase C 단계 정합화**: v1.10에서 §4.3의 가상 파일 구조를 실제 Phase 1 산출물로 교체했으나, **동일한 파일 구조를 그리는 §6.4의 중복 다이어그램은 그때 갱신에서 누락**돼 여전히 `run-guide.md`/`analyze-guide.md`/`debug-guide.md`를 보여주고 있었음(사용자 지적). §4.3과 동일한 실제 구조(phase-0~5+tool-map+server-ops+backend-interface)로 교체. 같은 이유로 §8.2 Phase C 구현 순서(C-2~C-4가 존재하지 않는 run-guide/analyze-guide/debug-guide 작성을 지시)도 함께 발견해 정합화 — C-2/C-3(FAIL 분류표·origin linking)를 제거하고 backend-interface.md 작성 + TB frontmatter 갱신(§5.2의 새 combined_sha256 형식)만 남김. **교훈**: 같은 정보를 그리는 중복 다이어그램/목록이 문서 내 여러 곳에 있으면, 한 곳을 고칠 때 나머지도 grep으로 전부 찾아 같이 고쳐야 한다 — §4.3만 고치고 §6.4를 놓친 게 이번 재발 사례. |

---

## Migration Note

이 문서는 원래 `venezia-fpga/docs/01-plan/features/debug-workflow-v2.plan.md`에 있었으나, 2026-07-02 xcelium-mcp 저장소로 이관되었다. Predecessor 문서(`xcelium-mcp-debugging-workflow.plan.md`)와 구현 대상 파일(§8.1)이 모두 이 저장소 소속이라 계획 소유권을 이 프로젝트의 bkit PDCA 사이클로 옮기는 것이 맞다. venezia-fpga 쪽 원본은 삭제되었다(git 히스토리로 복구 가능).
