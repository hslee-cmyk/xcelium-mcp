# Plan: `/sim` — Verilog HW Verification Workflow

> **Feature**: Verilog 하드웨어 디자인 검증 워크플로우
>
> **Date**: 2026-04-03
> **Status**: Draft v1.36
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
| **RISK** | `compound.py`가 기존 batch/CSV 로직을 재구현하면 이미 검증된 경로(617 tests)와 별개로 새 버그 표면이 생김(§3.4 주의) |
| **SUCCESS** | `/sim verify {test}` 1회 호출로 run→analyze→(debug) 자동 체이닝; 기존 25 tool 전량 하위호환 유지; tool trigger 세션당 60% 감소 |
| **SCOPE** | Phase A-C(Backend 조합 계층 + CLI + Skill) 우선 구현 → 검증 후 Phase D(Hook 자동화) 후행. Backend Interface(§3, 다중 시뮬레이터 추상화)는 두 번째 backend가 실제로 필요해지기 전까지 YAGNI 후보(§10 참조) |

---

## Requirements

### Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Run: batch/bridge 두 모드로 단일 테스트 실행, dump+로그 산출 | High | Pending |
| FR-02 | Analyze: 로그 판별 + CSV 추출/검색 + FAIL 유형 자동 분류(§5.4) | High | Pending |
| FR-03 | Debug: RTL/TB 분석서 기반 FAIL 원인 추적 + fix-plan 문서 작성(근본원인 소재 `fix_target: rtl \| tb` 판정 포함) → 사용자 승인 → **`fix_target=rtl`**: (ARCH면 fix-design/ADR 선행) → 구현(`verilog-rtl-coder` agent 위임 또는 사람이 직접 수정) → `verilog-rtl-reviewer` 정적 리뷰 + (self-contained 항목) `verilog-rtl-prover` formal 증명 필수 게이트(fix-review, STATIC-CONFIRMED 문제 또는 formal 반례 발견 시 재구현) 통과 후 / **`fix_target=tb`**: 구현(`verilog-tb-coder` agent 위임 또는 사람이 직접 수정, spec 대비 정당화 필수 — anti-tautology) → `verilog-tb-reviewer` 필수 정적 리뷰(fix-review, 문제 발견 시 재구현) 통과 후 — `/sim verify`(시뮬레이션) 진입(§4.2/§4.5/§5.8 참조) | High | Pending |
| FR-04 | Verify: run→analyze→(FAIL시)debug 자동 체이닝, 수정 후 재진입 | High | Pending |
| FR-05 | sim-state.json: 테스트별 phase/결과/origin_chain 영속 상태 추적. 저장 위치는 `registry.py` 확장이 아닌 독립 파일 `{project}/.ai/sim-state.json` — 클라이언트(로컬) 머신 전용, 근거는 §5.1 참조 | High | Pending |
| FR-06 | CLI: AI 없이 `xcelium-mcp-cli run/analyze/regression` 직접 실행(§7) — `server.py` 공유 entry point가 아니라 독립 console_script | Medium | Pending |
| FR-07 | Backend Interface: compound operation 3종 규격화(§3) — **YAGNI 후보**, 두 번째 backend(vcs-mcp 등) 착수 전까지 interface만 정의하고 범용화 자체는 보류 검토 | Low | Pending |
| FR-08 | Hook 자동화: PostToolUse phase 전환 제안 + UserPromptSubmit 키워드 트리거(§6) — Phase D, A-C 검증 후 후행 | Medium | Pending |
| FR-09 | Tool 사용법 가이드(25개 raw tool의 phase별 선택 매트릭스, `references/tool-map.md` 상당)는 이 `/sim` skill과 동일한 `~/.claude/skills/xcelium-sim/` 디렉터리의 Phase 1로 구현 — compound.py(Layer 3) 완성을 기다리지 않고 즉시 착수 가능. 상세는 `## Dependencies` 참조 | High | Pending (선행 feature, Phase 1) |

### Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|--------------------|
| Backward Compatibility | 기존 25 tool 100% 하위호환, `pytest` 617 tests 회귀 없음 | `pytest tests/ -v` 전체 스위트 |
| Efficiency | tool trigger 세션당 60% 감소 | 도입 전/후 세션 로그 tool_use 횟수 비교 |
| Maintainability | `compound.py`가 기존 `batch_runner.py`/`csv_cache.py` 재사용, 로직 중복 없음 | 코드 리뷰 (§3.4) |

---

## Dependencies

> 이 문서와 관련 있는 다른 두 계획(`xcelium-mcp-v5.1-runner-abstraction.plan.md`, tool-usage-guide skill)의 관계를 명시한다. "선행 필수" 오독을 막기 위한 섹션.

### `xcelium-mcp-v5.1-runner-abstraction.plan.md`와의 관계 — 선행 필수 아님, 독립 트랙

- **v5.1 상태**: Draft, design/analysis/report 없음, `RunnerInterface`/wrapper script 자동생성/`sim_start()` 미구현.
- **왜 선행조건이 아닌가**: 이 문서 §3.4의 `compound.py`는 이미 동작하는 `batch_runner.py`/`bridge_lifecycle.py`를 wrap하도록 설계돼 있고, 이 함수들은 v5.1의 `RunnerInterface`가 아니라 **v4.1-era `runner_detection.py`**(`auto_detect_runner`, registry의 `runner.type`+`args_format`)를 통해 이미 환경 감지를 수행한다. 즉 `compound.py → batch_runner.py → runner_detection.py(v4.1)` 체인이 지금 이미 존재하므로, ncsim legacy 환경 한정으로는 v5.1 없이 Phase A-C(§8.2) 착수가 가능하다.
- **Layer 경계**: `/sim` skill(Layer 4)은 compound.py가 `CompoundResult`만 반환하면 되고, 그 밑단이 v4.1 방식이든 v5.1 방식이든 신경 쓰지 않는다 — 나중에 v5.1로 교체해도 Layer 4는 영향받지 않는 클린한 경계.
- **진짜 걸리는 지점**: Executive Summary의 "시뮬레이터/환경 독립적" 주장을 UVM/Makefile 환경에서도 신뢰하려면 v5.1이 필요하다. v4.1의 ad-hoc 감지가 UVM/Makefile에서 검증된 적은 없다. 스코프를 ncsim legacy로 한정하면 v5.1 불필요, "환경 독립적" 주장 자체를 스코프에 포함하면 v5.1이 실질적 선행조건이 된다.
- **권고**: Phase A-C는 v5.1과 무관하게 지금 시작하고, v5.1은 별도 PDCA 사이클로 병행하거나 후행(2번째 backend 필요 시점, 또는 UVM 환경이 실제로 쓰이기 시작하는 시점)한다.

### tool-usage-guide(FR-09)와의 관계 — 동일 skill의 Phase 1

- 별도 skill이 아니라 같은 `~/.claude/skills/xcelium-sim/` 디렉터리를 목표로 하는 하나의 skill로 통합돼 있다.
- `xcelium-mcp-tool-usage-guide`(Phase 1, 완료): `compound.py` 없이 만들 수 있는 부분 — `references/phase-0~5.md`, `references/tool-map.md`, SKILL.md의 키워드 트리거·라우팅 스켈레톤.
- 이 문서(`xcelium-mcp-debug-workflow-v2`, Phase 2): `compound.py`(Layer 3) 완성 후 같은 SKILL.md에 `/sim run|analyze|debug|verify|status` subcommand 라우팅을 추가.
- **Design 문서는 별도 유지**한다. bkit matchRate는 feature별로 Design vs 구현을 비교하므로, 병합하면 이 문서(Phase 2, compound.py 대기 중이라 아직 구현 불가)의 Design 요소가 tool-usage-guide의 Do에서 구현되지 않아 matchRate가 부당하게 낮아진다. 이 문서의 Design은 compound.py(Layer 3)가 생긴 뒤 별도로 착수하며, tool-usage-guide의 Design이 남겨둔 SKILL.md 확장점(subcommand 라우팅 자리)에 맞춰 작성한다.
- 배포 위치: `~/.claude/skills/xcelium-sim/`은 **user-level**이다 — xcelium-mcp repo 안(project-level)에 두면 실제 디버깅이 일어나는 venezia-fpga 등 RTL 프로젝트 세션에서 전혀 로드되지 않으므로, 이 결정은 필수적이다.
- **Phase 1 실제 산출물**(`xcelium-mcp-tool-usage-guide.design.md` 대조, matchRate 98%): §4.3이 애초에 가정했던 가상 파일 구조(`run-guide.md`/`analyze-guide.md`/`debug-guide.md`/`backend-interface.md`)와 이름이 다르다 — 실제로는 `phase-0-discovery.md`~`phase-5-fix-regression.md`(6개, 원본 워크플로우 Phase와 1:1 대응) + `tool-map.md` + `server-ops.md`(원격 supervisor 운영)로 총 8개다. Phase 2는 새 파일을 만드는 게 아니라 이 8개 파일에 subcommand 라우팅을 얹기만 하면 된다.
- **Tool 개수는 고정값이 아니라 감사 시점의 스냅샷이다**: 이 문서 전체의 "25개"는 `grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py` 기준 실측치다. Phase 2 Design 착수 시점에 반드시 같은 방식으로 재감사할 것 — 이 숫자를 프로즈로 베끼지 말고 매번 소스에서 직접 셀 것.
- `verilog-rtl-debugger` agent는 실제로 존재한다(chip-design-skills 배포 완료) — §4.5에서 이를 전제로 서술한다.

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
| **Debug** | FAIL 원인 추적 (RTL debugging) | 근본 원인 + 승인 대상인 `fix-plan` 문서(§5.8 Fix Sub-cycle 참조) |

`verify` = Run → Analyze → (FAIL시) Debug → Fix Sub-cycle(승인 → [설계 비준] → 구현(AI 또는 사람) → 필수 리뷰, §5.8) → Run → ... 체이닝

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
    ├─ compound 호출이 반환한 CompoundResult를 받아 sim-state.json(§5.1)에 로컬로 기록/갱신
    │   └─ Backend(원격)는 파일에 관여하지 않음 — Write는 항상 클라이언트 로컬에서 일어남
    │
    └─ TB 분석서 YAML frontmatter (§5.2)를 Read tool로 파싱

Layer 3 (Backend)
    │
    └─ Compound operations 구현(backend별 Python) — CompoundResult 반환까지만 담당, sim-state.json은 건드리지 않음(§5.1 역할 분리 표 참조)

Layer 1 (Hooks, Phase D — 후행)
    │
    ├─ compound tool 실행 감지(PostToolUse) → sim-state.json 갱신 확인 + phase 전환 제안
    └─ 키워드 감지(UserPromptSubmit) → Skill 사용 제안(§6.1 — SessionStart 미사용, `/sim status`로 on-demand 대체)
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
├── cli.py                   ← CLI argparse → compound.py 호출. 독립 entry point(§7.1) — server.py의 sys.argv 분기 아님
├── tools/compound.py        ← MCP tool 3개 → compound.py 호출
└── server.py                ← compound tool 3개 register() 추가만 — CLI 관련 변경 없음
```

`sim_state.py`(sim-state.json 읽기/쓰기)는 이 패키지에 없다 — §5.1이 확정한 대로 `sim-state.json`은 클라이언트 로컬 파일이고, 이 `src/xcelium_mcp/`는 원격 시뮬레이션 서버에서 실행되는 패키지다. 원격 프로세스가 클라이언트 로컬 파일을 직접 다룰 방법이 없으므로, `sim_state.py`는 client-side 위치(`skill-src/xcelium-sim/scripts/`, §4.3)에 둔다.

**주의 — 신규 구현이 아니라 조합(wrap)**: `run_and_check`/`analyze_waveform`/`regression_summary`는 새 로직이 아니라 이미 완료된 v3 Improvement Plan(`batch_runner.py`의 `run_batch_single`/`run_batch_regression`, `csv_cache.py`의 `extract`/`bisect_signal_dump`)을 시퀀스로 묶는 얇은 조합 계층이다. `compound.py` 구현 시 이 함수들을 그대로 호출·재사용하고, batch 실행이나 CSV 추출 로직 자체를 재작성하지 않는다 — 그러지 않으면 이미 검증된 경로와 별개로 새 버그 표면이 생긴다.

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

`phase: "fix-plan"`인 테스트가 있으면 `/sim status`가 "TOP015: fix-plan 단계에서 보류 중(`.ai/sim-state/TOP015/fix-plan.md`) — `/sim debug TOP015`로 이어서 결정하거나 `/sim run TOP015`로 새로 시작할 수 있습니다"처럼 두 재개 경로를 함께 안내한다(§4.2 debug 0단계·run 0단계 참조).

### 4.2 각 Subcommand 상세

#### `/sim run`

```
0. 보류 중인 fix-plan 확인: 이 테스트의 sim-state.json이 `phase: "fix-plan"`(승인 보류 중)이면 "보류 중인 fix-plan.md가 있는데 새로 시작할까요?" 확인 — 승인하면 `sim_state.py`의 `supersede_fix_plan(sim_dir, test)`(§5.1) 호출 → `.ai/sim-state/{test}/fix-plan.md`(및 존재하면 `fix-design.md`)는 그대로 두고 `origin_chain.fix_plan.status`만 `"superseded"`로 표시, phase를 `"run"`으로 되돌린 뒤 아래 1단계로 진행. **`origin_chain.debug`/`debug.md`는 이 호출로 건드리지 않는다** — 이번에 추가한 dump 신호로 다시 `debug`에 들어갔을 때 기존 조사 내용을 이어서 참고하기 위함(§5.1 "debug가 문서 포인터인 이유" 참조). 거부하면 `/sim debug {test}`로 유도(재개 경로, §4.2 debug 0단계)

0-b (run). **사람 구현 완료 감지**: `phase == "fix-implement"`이고 `origin_chain.fix_implement.implementer == "human"`이며 아직 review를 받지 않은 상태(§4.2 debug 6단계 "(b) 사람이 직접 수정" 참조)면, fix-plan.md가 선언한 "영향 모듈/파일" 목록에 대해 fix-plan 승인 시점 이후 실제 git diff가 있는지 확인한다(§5.2가 쓰는 "수동 신호 대신 자동 감지" 원칙과 동일, `combined_sha256` 대신 git diff 유무로 판정) —
   ├─ **diff 있음** → 사람이 구현을 완료한 것으로 간주, `record_fix_implement(sim_dir, test, implementer="human", files_changed=git_diff_결과, report="")`(§5.1)로 `files_changed`를 채우고 §4.2 debug 6단계의 "fix-review 게이트"로 바로 진입(1단계 이하로 내려가지 않음 — 이번 `/sim run` 호출 자체가 review 트리거가 됨)
   └─ **diff 없음** → "아직 fix-plan.md 범위의 파일에 변경이 감지되지 않았습니다. 직접 수정 후 다시 `/sim run`을 실행해 주세요"라고 안내하고 phase는 `fix-implement`에 그대로 머무름(아래 1단계 이하 진행 안 함)

1. TB 분석서 캐시 확인 → {project}/.ai/analysis/tb_{test}.analysis.md
   ├─ YAML frontmatter 있음(정상 경로 — `verilog-tb-analyst`가 작성 시점에 함께 생성, §5.2) → pass_signals, fail_conditions 자동 추출
   ├─ frontmatter 없음(backfill 전 레거시 문서 또는 agent 미설치, §5.2 참조) → AI가 본문 읽어서 판단 (fallback)
   └─ 분석서 자체 없음 → Phase 0(`verilog-tb-analyst`)에 위임해 분석서+frontmatter 함께 작성 후 진행

2. Backend compound operation 호출
   ├─ --bridge → backend별 bridge 연결 (xcelium: connect_simulator)
   ├─ --regression → regression_summary compound
   └─ (기본) → run_and_check compound

3. Skill이 compound 반환값(CompoundResult)을 받아 로컬 sim-state.json 갱신(§5.1 — Backend는 파일에 관여하지 않음)
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
0. **fix-plan 재사용 확인**: 이 테스트에 대해 이미 `phase: "fix-plan"`(승인 보류 중)인 기존 `origin_chain.fix_plan`이 있으면, 1~5단계(재조사)를 건너뛰고 그 기존 fix-plan.md를 그대로 로드해 바로 6단계 승인 게이트로 진입한다 — 같은 근본원인을 두 번 조사하지 않는다.

0-b. **기존 debug.md 확인**: 0단계에 해당하지 않는(=fix-plan이 없거나 이미 superseded된) 신규/재개 조사라도, `origin_chain.debug.iteration_count > 0`이면 아래 1~5단계 착수 전에 `.ai/sim-state/{test}/debug.md`를 먼저 Read한다 — `/sim run`으로 추가 dump 신호를 넣어 재실행한 경우가 전형적 사례다. **목적은 이전 iteration에서 이미 결론 낸 추론(확정적으로 배제한 가설, 이미 설명이 끝난 신호 거동)을 처음부터 다시 도출하는 헛수고를 막는 것이지, 그 가설·신호를 이번 조사 대상에서 빼라는 뜻이 아니다** — 이전에 확인했던 신호라도 이번 조사에 필요하면(새로 추가한 신호와의 교차 확인 등) 당연히 이번 dump/분석 대상에 다시 포함된다. debug.md는 "이미 답이 나온 것을 다시 묻지 않게" 해주는 참고 자료이지, "무엇을 배제해야 하는지" 정하는 제약 목록이 아니다(§5.1 참조).

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

5. 결과: 근본 원인 + 수정 approach 도출(§4.5 `debug (RTL, 근본원인)` 매핑 참조 — `verilog-rtl-debugger` agent 위임) → `sim_state.py`의 `append_debug_note(sim_dir, test, note, context)`(§5.1)로 `.ai/sim-state/{test}/debug.md`에 이번 조사 내용을 append(덮어쓰지 않음) — `context`는 0단계/0-b단계 여부에 따라 `"최초 조사"` 또는 `"재개 조사(추가 정보 기반)"`, `origin_chain.debug.iteration_count` +1

6. **Fix Sub-cycle 진입(§5.8 참조)**: 근본원인+approach를 말로 된 제안이 아니라 **`fix-plan` 문서**로 고정한 뒤, **3-way 승인 게이트**(AskUserQuestion)로 승인받고, 승인된 문서 범위 안에서만 구현하도록 강제한다 — `fix-plan → [fix-design, ARCH일 때만] → fix-implement → fix-review` 4단계(상세 §5.8):
   ├─ **fix-plan**: 근본원인/영향 파일/structural delta 선언/검증 대상을 `{project}/.ai/sim-state/{test}/fix-plan.md`로 작성(`write_fix_plan()`, §5.1) → AskUserQuestion "이 fix-plan대로 진행할까요?" — **승인 / 수정 요청 / 보류** 3택
   ├─ **승인** → `approve_fix_plan()`(§5.1) 호출 → **구현 주체 선택**(AskUserQuestion): **(a) AI 위임** / **(b) 사람이 직접 수정**
   │     ├─ **(a) AI 위임** → `verilog-rtl-coder` agent(Task, fix-plan 파일 경로를 명시적으로 전달 + "이 문서 범위 밖 변경은 멈추고 보고" 지시)에 구현 위임 → 완료 시 `record_fix_implement(sim_dir, test, implementer="verilog-rtl-coder", files_changed=[...], report=...)`(§5.1) 호출 → **이 세션 안에서 곧바로** 아래 "fix-review 게이트"로 진행(Task 반환을 그대로 이어받는 동기 흐름)
   │     └─ **(b) 사람이 직접 수정** → Skill은 코드를 건드리지 않고 "fix-plan.md의 structural delta 선언 범위 안에서 직접 수정한 뒤 `/sim run {test}`(또는 `/sim verify {test}`)를 다시 실행해 주세요"라고 안내 → `record_fix_implement(sim_dir, test, implementer="human", files_changed=[], report="")`(§5.1, 완료 전이라 `files_changed`/`report`는 아직 빈 상태)만 호출하고 phase는 `fix-implement`에 머무른 채 이번 세션 종료 — 사람이 언제 어떻게 고칠지 Skill이 실시간으로 감지할 방법이 없으므로 **비동기**로 처리한다. 완료 감지는 다음 `/sim run` 재호출 시점의 **run 0-b단계**(§4.2)가 담당(fix-plan이 선언한 파일 목록의 git diff 유무로 판정)
   ├─ **수정 요청** → 사용자 피드백 수집 → 두 갈래(어느 쪽이든 개정 내용은 `write_fix_plan()`(§5.1, revision_count +1)으로 반영 후 **6단계 승인 게이트 재진입**, phase는 계속 `fix-plan`, 승인될 때까지 반복 — §5.8 "개정 루프" 참조):
   │     ├─ **가벼운 수정**(approach 조정, 누락 파일 추가 등) → 새 조사가 없으므로 `append_debug_note` 호출 없이 Skill이 직접 fix-plan.md만 Edit
   │     └─ **근본원인 재조사가 필요한 피드백**("다른 원인 아닐까?" 등) → `verilog-rtl-debugger`에 피드백과 함께 재위임 → 재조사 결과를 `append_debug_note(sim_dir, test, note, context="fix-plan 수정 요청 재조사(revision {N})")`(§5.1)로 `debug.md`에 먼저 append한 뒤, 그 결과를 반영해 fix-plan.md를 개정
   ├─ **보류** → `hold_fix_plan()`(§5.1 — 상태 변경 없는 명시적 no-op) 호출, sim-state.json에 `phase: "fix-plan"` + 기존 fix-plan.md 경로를 그대로 보존하고 이번 세션은 여기서 멈춘다(종료가 아님 — 코드에는 손대지 않는다). 다음 세션에서:
   │     ├─ `/sim debug {test}` 재실행 → 위 **0단계**가 감지해 재조사 없이 바로 6단계 승인 게이트 재표시(이어서 결정)
   │     └─ `/sim run {test}` 재실행 → "보류 중인 fix-plan이 있는데 새로 시작할까요?" 확인 후 진행하면 `supersede_fix_plan`(§5.1)이 기존 fix-plan.md의 `status`만 `"superseded"`로 표시(파일은 그대로 둠) — `debug.md`는 손대지 않으므로 다음 debug 재진입 시 0-b단계가 그대로 이어서 활용(§5.8 참조)
   ├─ coder의 A0 게이트가 ARCH(새 FSM/module/instance/case-arm)로 판정 → **fix-design**(§5.8) 진입: `verilog-rtl-architect-advisor` escalate → ADR 산출 → 사용자 재승인 → 비준된 ADR을 다시 coder에게 전달해 **fix-implement** 재개(LOCAL/IFACE면 fix-design 스킵, fix-plan만으로 바로 fix-implement). 사람이 직접 수정하는 경우 coder의 A0 self-check이 없어 ARCH 여부가 자동 판정되지 않는다 — 대신 아래 fix-review 게이트의 `verilog-rtl-reviewer`가 구조적 변경 신호를 정적으로 짚어낼 수 있고, 필요하면 사용자가 직접 `verilog-rtl-architect-advisor`를 호출해 ADR을 남길 수 있다(강제 아님, §5.8 참조)
   └─ **fix-implement 완료(구현 주체 무관)** → **fix-review 게이트 진입(필수, `fix_target` 무관하게 항상 진입)**:
       - `fix_target=rtl`: 1차로 `verilog-rtl-reviewer`가 실제 diff를 정적 관점에서 검토, 2차로 reviewer가 self-contained 로직/timing으로 분류한 항목은 그 자리에서 `verilog-rtl-prover`가 형식 증명
       - `fix_target=tb`: `verilog-tb-reviewer`가 실제 diff를 spec 정당화·checker 완화·fix-plan 범위 준수 관점에서 정적 검토(formal 대응 단계 없음, 상세는 §5.8 "4) fix-review" 참조)
       - **문제 발견**(STATIC-CONFIRMED, formal 반례, 또는 tb-reviewer의 issues_found) → **fix-implement로 되돌아감**: AI 구현이었으면 `append_fix_review_note()`(§5.1)로 findings 원문을 `fix-review.md`에 append하고 findings와 함께 coder/tb-coder에 재위임(다음 Task 프롬프트에 findings가 자동으로 실림) — 사람 구현이었으면 **Skill이 findings+개선 방향을 담은 요약을 먼저 작성**하고, **그 요약 텍스트를 `note`로 `append_fix_review_note()`에 넘겨 `fix-review.md`에 append한 뒤, 동일한 텍스트를 이 응답 안에 그대로 출력**한다("기록=출력" 동일성 — 채팅 출력과 문서 기록을 별개로 작성하면 나중에 무엇이 실제로 전달됐는지 어긋날 수 있으므로, 요약은 한 번만 작성해 두 곳에 같은 텍스트로 반영한다. 상세 포맷은 §5.8 "4) fix-review" 참조): 무엇이 발견됐는지(STATIC-CONFIRMED 목록 또는 formal 반례 corner, agent 원문 근거를 왜곡하지 않고 요약) + 있으면 agent가 제시한 개선 방향, 없으면 "구체적 개선 방향 미제시"라고 명시 — 같은 fix에서 2라운드 이상 반복되면 AskUserQuestion으로 "계속 자동 재시도할지" 확인, §11 참조 / **clean** → sim-state.json origin_chain에 fix_plan/fix_design/fix_implement/fix_review 전부 기록하고 phase를 `run`으로 전환, 아래 7단계 진행. `fix_target=rtl`에서 CDC/protocol-relational처럼 formal로도 못 잡는 진짜 SIM-RISK 항목은 `fix-review.md`에 남아 다음 `/sim run`의 dump 신호 선정 시 참고 자료로 쓰일 수 있음(강제 아님)

7. sim-state 갱신 (origin chain 포함) + next-skill 제안 — **fix-review를 통과해야만** phase가 `run`으로 복귀(§5.8 Phase 전이 참조, 보류 시엔 `fix-plan`에, 리뷰에서 문제가 남아있으면 `fix-implement`에 머무름)
```

#### `/sim verify`

```
1. /sim run {test}
   └─ 이 Step 1 자체가 run 0-b단계(§4.2)를 거친다 — phase가 `fix-implement`(사람 구현 대기 중)면 여기서 diff 유무를 확인해 fix-review로 바로 진입할 수도 있음
2. /sim analyze {test}
3. FAIL이면:
   ├─ /sim debug {test} — 근본 원인 + Fix Sub-cycle(fix-plan → [fix-design] → fix-implement → fix-review) 전체 수행
   ├─ fix-review 통과(clean) → Step 1 재진입
   └─ 재진입하지 않는 경우 — **수정 요청** 중(개정 루프 진행 중), **보류**(사용자가 다음 세션으로 결정 미룸), fix-design 단계에서 ADR 비준 대기 중, **fix-review에서 문제가 발견되어 fix-implement로 되돌아간 경우**: 넷 다 이번 `/sim verify` 호출은 여기서 끝나고, 나중에 `/sim debug {test}`(이어서 결정) 또는 `/sim run {test}`(새로 시작 또는 사람 구현 완료 감지)로 재개(§4.2 debug 0단계·run 0단계/0-b단계)
4. PASS이면:
   ├─ 단일 테스트 → "regression?" 제안
   └─ --regression → 요약 보고

--regression 시 복수 FAIL → 병렬 Agent 분석 (§5.7)
```

### 4.3 Skill 파일 구조

```
skill-src/xcelium-sim/                    (git 정본, cp -r로 ~/.claude/skills/에 배포)
├── SKILL.md                              ← Phase 1(tool 사용법) + Phase 2 확장점 마커 두 섹션 보유(§4.4 참조)
├── scripts/
│   └── sim_state.py                      ← sim-state.json CRUD + phase 전이 — 클라이언트 로컬에서 Skill(Bash 호출)·Hook(Phase D)이 공용
└── references/
    ├── phase-0-discovery.md              ← 검증 환경 인프라 분석(TB 캐시, 공유 컴포넌트)
    ├── phase-1-analysis.md               ← 사전 분석(RTL 분석서, dump scope)
    ├── phase-2-simulation.md             ← 시뮬레이션 실행(Batch/Bridge)
    ├── phase-3-triage.md                 ← 1차 판별(로그 기반)
    ├── phase-4-waveform.md               ← 2차 판별(waveform CSV, bisect, FSM 전이 대조)
    ├── phase-5-fix-regression.md         ← 수정+Regression+세션 종료 정리
    ├── tool-map.md                       ← 25개 tool 전체 결정 매트릭스(전 phase 공통 참조)
    ├── server-ops.md                     ← 원격 supervisor 코드 반영 확인+재기동 운영 절차
    └── fix-plan-template.md              ← (§5.8) fix-plan.md 필수 항목 정의 — Phase 2가 추가하는 신규 reference
```

**`scripts/sim_state.py`가 여기 있는 이유**: `sim-state.json`은 §5.1이 확정한 대로 클라이언트 로컬 파일이므로, 그 CRUD 로직도 원격 서버 패키지(`src/xcelium_mcp/`)가 아니라 Skill과 물리적으로 같은 곳(클라이언트 로컬)에 있어야 한다. `reference/`가 아니라 `scripts/`인 이유는 이게 phase 판단을 돕는 문서가 아니라 Skill(AI, Bash 호출)과 Hook(Phase D)이 공유하는 실행 코드이기 때문이다.

**원본 워크플로우 참조**: `xcelium-mcp-debugging-workflow.plan.md`(원본 6-phase 방법론)와 이미 1:1 대응 완료.

| Reference | 원본 Phase | 비고 |
|-----------|-----------|------|
| `phase-0-discovery.md` | Phase 0 | TB 캐시 규칙, 공유 컴포넌트 분석 |
| `phase-1-analysis.md` | Phase 1 | RTL 분석서, dump scope 결정 |
| `phase-2-simulation.md` | Phase 2 | Batch/Bridge 실행 선택 |
| `phase-3-triage.md` | Phase 3 | 로그 기반 1차 판별 |
| `phase-4-waveform.md` | Phase 4 | waveform CSV 2차 판별, FSM 대조 |
| `phase-5-fix-regression.md` | Phase 5 | 수정 + regression + 세션 종료 정리 |
| `tool-map.md` | 전체 | 25개 tool 결정 매트릭스(감사 시점 스냅샷, `## Dependencies` 각주 참조) |
| `server-ops.md` | (원본 워크플로우에 없던 신규 항목) | xcelium-mcp 서버 운영(재기동) — Phase 2 범위가 아닌 인프라 문서지만 같은 skill에 위치 |

Phase 2가 추가할 것은 새 reference 파일이 아니라, 위 8개 파일이 이미 구성해 놓은 phase 판단 로직 위에 `/sim run|analyze|debug|verify|status` subcommand가 "어떤 phase reference를 언제 자동으로 골라 로드할지"를 결정하는 라우팅 계층뿐이다 — `backend-interface.md`(compound operation 인터페이스 정의) + `fix-plan-template.md`(§5.8 fix-plan.md 필수 항목) **2개**가 Phase 2가 실제로 신규 추가하는 reference 파일이다.

Phase 2는 추가로 `phase-0-discovery.md` §0A/0B에 TB frontmatter YAML 스키마(§5.2) 참조 지시를 삽입한다(`verilog-tb-analyst`가 분석서 작성 시점에 frontmatter를 함께 생성하도록) — 이 8개 파일 중 Phase 2가 내용을 수정하는 유일한 기존 파일이다. `verilog-tb-analyst` agent 정의 문서 자체는 chip-design-skills repo 소유라 이 plan 범위 밖이다. 위 `scripts/sim_state.py`도 Phase 2가 신규 추가하는 파일이다.

### 4.4 SKILL.md 핵심

**설계 원칙**: trigger 키워드는 프로젝트에 무관한 일반 검증/시뮬레이션 어휘로만 구성하고, 특정 프로젝트의 테스트 ID 네이밍 컨벤션(접두어·번호 체계 등)은 절대 포함하지 않는다 — skill `trigger`는 정규식 매칭 엔진이 아니라 AI가 관련성을 판단하는 키워드 힌트일 뿐이고, 테스트 ID 자체는 `/sim run {test}`처럼 사용자가 subcommand 인자로 직접 지정하거나 `list_tests`/`sim_discover`가 알아내므로 trigger가 그 형식을 추측할 필요가 없다.

아래는 실제 배포된 `~/.claude/skills/xcelium-sim/SKILL.md`의 trigger로 동기화한 최종 형태다.

```yaml
---
name: xcelium-sim
trigger: |
  xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, simulation,
  debugging, 디버깅, CSV, checkpoint, bisect, regression,
  dump_scopes, dump_depth, 재기동, supervisor,
  연결 안 됨, 최신 코드 반영 안 됨, MCP 응답 없음
next-skill-map:
  run.PASS: "sim run --regression"
  run.FAIL: "sim analyze"
  analyze.FOUND: "sim debug"
  analyze.INCONCLUSIVE: "sim analyze --signals"
  debug.PLAN_READY: "fix-plan 문서 승인 대기 → (승인 시) 구현 주체 선택(verilog-rtl-coder agent 또는 사람 직접 수정, ARCH면 fix-design 선행) → verilog-rtl-reviewer 정적 리뷰 + verilog-rtl-prover formal 증명 필수 게이트(fix-review, 문제/반례 시 재구현) → sim verify"
  verify.PASS: null
  verify.FAIL: "sim debug"
---
```

`debug.PLAN_READY` 값은 "verilog-rtl skill 연계"라는 막연한 표현이 아니라, 실제 구현을 맡는 `verilog-rtl-coder` agent(chip-design-skills 소유)를 명시한다 — `next-skill-map`은 본래 skill 간 라우팅용이지만 이 항목은 **문서 승인 게이트 + agent 위임**이라는 별도 종류의 전이라 `verilog-rtl`(skill)이 아니라 `verilog-rtl-coder`(agent)로 적는다. `verilog-rtl` skill 자체는 사라지지 않는다 — coder agent가 구현 시 내부적으로 로드한다(§10.2 참조). reviewer의 정적 리뷰와 prover의 formal 증명은 서로 다른 agent가 수행한다(§5.8 "4) fix-review" 참조).

### 4.5 Skill ↔ Backend 도구 매핑

| Skill subcommand | Backend compound | Backend 개별 tool (필요 시) |
|-----------------|-----------------|--------------------------|
| `run` (batch) | `run_and_check` | — |
| `run` (bridge) | — | connect_simulator, sim_run |
| `run` (regression) | `regression_summary` | — |
| `analyze` | `analyze_waveform` | — |
| `debug` (CSV) | `analyze_waveform` | — |
| `debug` (bridge) | — | connect, watch, get_signal_value |
| `debug` (RTL, 근본원인) | — | 로컬에 설치된 `verilog-rtl-debugger` agent 호출(mcp__xcelium-mcp__* 전 tool 접근 가능한 유일한 verilog-rtl-* agent), 필요 시 analyst/reviewer/prover로 추가 위임. `verilog-rtl-architect-advisor`는 이 목록에서 제외 — partitioning 비준·ADR 산출은 §5.8 `fix-design` phase에서만 이뤄진다(아래 행 참조). debugger가 조사 중 기존 아키텍처를 참고용으로 읽는 것 자체는 막지 않지만 그건 "위임"이 아니라 일반적인 코드 읽기다 |
| `debug` (RTL, 수정 구현) | — | 근본원인+approach 확보 후 **`fix-plan` 문서** 작성 → 사용자 승인 게이트(§4.2 debug 6단계) → 승인 시 **구현 주체 선택**: `verilog-rtl-coder` agent(chip-design-skills 소유, Task로 위임, fix-plan 경로 전달)가 구현하거나, **사람이 fix-plan.md 범위 안에서 직접 구현**(§4.2 debug 6단계 (b), §5.8 참조). coder는 자체 A0 model-diff gate를 가진 constrained implementer라, 변경이 architectural(새 FSM/module/instance/case-arm)이면 스스로 멈추고 `verilog-rtl-architect-advisor`로 escalate → **fix-design**(ADR) 산출 + 재승인 후 구현 재개(§5.8, 사람 구현 경로엔 이 자동 A0 게이트가 없음). LOCAL/IFACE면 fix-design 없이 fix-plan만으로 바로 구현 |
| `debug` (RTL, 구현 리뷰) | — | 구현 완료(AI든 사람이든) 직후 `verilog-rtl-reviewer` agent(chip-design-skills 소유, Task로 위임)가 실제 diff를 정적 관점에서 1차 리뷰하고, reviewer가 self-contained 로직/timing으로 분류한 항목은 이어서 `verilog-rtl-prover` agent(Task로 위임)가 그 자리에서 형식 증명까지 수행(§5.8 "4) fix-review"). STATIC-CONFIRMED 문제 또는 formal 반례 발견 시 fix-implement로 되돌아감 — 시뮬레이션(`/sim verify`) 재검증 전에 정적/formal로 잡을 수 있는 버그를 모두 먼저 차단하는 게 목적. **`fix_target=tb`면 이 행 대신 아래 TB 행이 적용됨** |
| `debug` (TB, 근본원인·수정 구현) | — | `verilog-rtl-debugger`가 근본원인을 `fix_target=tb`로 판정하면(§5.8 "Fix Target: RTL vs TB"), fix-plan 승인 후 구현 주체 선택에서 `verilog-tb-coder` agent(chip-design-skills 소유, Task로 위임, fix-plan 경로 전달)가 구현하거나 사람이 직접 구현(§5.8 "3) fix-implement" TB 표) — 완료 직후 `verilog-tb-reviewer` agent(chip-design-skills 소유, read-only, Task로 위임)가 spec 정당화(anti-tautology 최종 판단)·checker 완화·범위 준수를 독립 검토하는 fix-review 게이트를 거친다(§5.8 "4) fix-review" TB 경로 참조) — **이 경로엔 fix-design(ARCH escalation)만 없다**(TB용 구조적 escalation 기준 자체가 정의돼 있지 않음, §5.8 "정직한 한계" 참조). 게이트 통과 후 `/sim verify`(실제 시뮬레이션 재실행)로 최종 확인 |
| `verify` | 위 조합 (자동 체이닝) | — |
| `status` | — | mcp_config, ssh_run |

> **`verilog-rtl-debugger`/`verilog-rtl-coder`/`verilog-rtl-reviewer`/`verilog-rtl-prover`/`verilog-tb-coder`/`verilog-tb-reviewer`는 서로 다른 agent다**: `verilog-rtl-debugger`는 Write/Edit tool이 없는 조사 전용 agent(mcp__xcelium-mcp__* + Read/Glob/Grep/Task)로 실제 코드를 건드리지 못하고, `verilog-rtl-coder`/`verilog-tb-coder`만 각각 RTL/TB 파일을 Write/Edit할 수 있는 constrained implementer이며, `verilog-rtl-reviewer`/`verilog-rtl-prover`/`verilog-tb-reviewer`는 모두 다시 Read-only로 돌아가 코드를 고치지 않는다 — rtl-reviewer/tb-reviewer는 정적 리뷰 리포트를, prover는 형식 증명 결과(통과 또는 반례)를 산출할 뿐이다. `verilog-rtl-coder`와 `verilog-tb-coder`는 대상(RTL vs TB)이 다를 뿐 같은 "구현" 역할이지 서로의 대상을 겸하지 않는다. `verilog-tb-coder`가 spec 정당화를 self-check하는 것은 1차 필터일 뿐 권위 있는 판단이 아니다 — 그 최종 판단은 독립된 `verilog-tb-reviewer`가 내린다, RTL 쪽에서 coder의 anti-tautology(§5.8)가 Prover의 독립 검증으로 완결되는 것과 동일한 구조다. 따라서 "수정 제안"(debugger) → "실제 구현"(coder/tb-coder **또는 사람**) → "구현 검토"(reviewer, 필요 시 prover — `fix_target`에 따라 rtl-reviewer/prover 또는 tb-reviewer) 순서를 지키며, debugger·reviewer·prover가 스스로 코드를 고치는 경로는 존재하지 않는다.

---
## 5. 워크플로우 패턴 (bkit/compound-engineering 기반)

### 5.1 sim-state.json — 테스트별 상태 추적

**출처**: bkit `lib/pdca/status.js`

> **`{project}` 표기 규약**: 이 문서 전체에서 `{project}`는 **xcelium-mcp를 사용하는 RTL 검증 프로젝트**(예: `venezia-fpga`)를 가리키며, xcelium-mcp 저장소 자신을 가리키지 않는다. `sim-state.json`과 TB 분석서 캐시(`.ai/analysis/`)는 항상 호출 측 RTL 프로젝트 루트에 위치한다. 이 plan 문서가 xcelium-mcp repo로 이관된 이후에도(§Migration Note) 이 구분은 변하지 않는다.

**위치**: `{project}/.ai/sim-state.json` — **클라이언트(로컬) 머신 전용**, `{project}`는 Claude Code가 실제로 실행되는 RTL 프로젝트의 로컬 clone(예: venezia-fpga의 Windows 로컬 디렉터리)을 가리키며 원격 시뮬레이션 서버(xcelium-mcp 서버 프로세스와 실제 xrun/SimVision이 실행되는 호스트 — 프로젝트마다 다를 수 있으며 CLAUDE.md의 "Deployment" 절에서 `sim-server`로 지칭)가 아니다. Git 미추적.

> **동반 디렉터리 `{project}/.ai/sim-state/{test}/`**: `sim-state.json` 자신은 위처럼 Git 미추적 상태 캐시이지만, `debug`/`fix-plan`/`fix-design`/`fix-review` 4개 phase는 그 산출물(투자한 조사·승인 대상 계획·ADR·리뷰 findings)이 재활용·감사 가치가 있는 **prose 문서**라 Git-tracked 별도 파일로 남긴다. 이 문서들은 테스트별로 흩어진 최상위 디렉터리(`.ai/fixes/`, `.ai/debug/` 등)에 나누지 않고 **`.ai/sim-state/{test}/` 하나로 통일**한다 — `{test}` 하위에 `debug.md`/`fix-plan.md`/`fix-design.md`(조건부)/`fix-review.md`가 모이므로, 한 테스트의 조사~계획~설계~리뷰 산출물 전체를 한 폴더에서 바로 확인할 수 있고 이름에 `{slug}`를 붙일 필요도 없다(테스트당 phase별 활성 문서가 최대 1개뿐이라 슬러그로 구분할 대상이 애초에 없음). `sim-state.json`은 이 문서들을 **가리키는 포인터**(`path`)와 짧은 카운터(`iteration_count`/`revision_count`)만 들고, 본문 내용은 전부 이 디렉터리의 `.md` 파일이 정본이다.

> **저장 위치 결정 근거 — 두 상태는 물리적으로 다른 머신에 있다.** `registry.py`의 `_REGISTRY_PATH = Path.home() / ".xcelium_mcp" / "mcp_registry.json"`(`registry.py:13`)은 xcelium-mcp Python 서버 프로세스가 실행되는 **원격 시뮬레이션 서버의 `Path.home()`**에 쓰인다 — Claude Code(로컬 머신)는 `ssh {sim-server} xcelium-mcp`를 stdio로 띄우는 얇은 클라이언트일 뿐이고(`xcelium-mcp-server-process-lifecycle.plan.md` §1.2), 실제 서버·`BridgeManager`·`registry.py` 상태는 전부 그 원격 서버에서 산다. 반면 §6의 Hook(`sim-post-compound.js`/`sim-prompt-detect.js`, PostToolUse/UserPromptSubmit)은 **Claude Code가 실행되는 로컬 머신**에서 도는 plain shell/node 프로세스로, MCP 세션과 무관하게 파일시스템을 직접 읽는다. `sim-state.json`을 `registry.py`(원격 시뮬레이션 서버)에 합치면 Hook이 매 호출마다 원격 상태를 읽기 위한 네트워크 호출(MCP tool 또는 ssh)을 새로 만들어야 하며, 이는 §6.1이 명시한 "SessionStart 미사용(토큰/지연 낭비 회피)"과 같은 이유로 피해야 할 설계다. 이에 따라 `sim-state.json`은 `registry.py`를 확장하지 않고 독립 파일로 확정한다.
>
> **역할 분리** (병합이 아니라 독립적인 두 상태):
>
> | | `registry.py` (`environments[sim_dir]`) | `sim-state.json` |
> |---|---|---|
> | 위치 | 원격 시뮬레이션 서버, `Path.home()/.xcelium_mcp/` | 로컬(클라이언트) 머신, `{project}/.ai/` |
> | 소비 주체 | MCP tool(`sim_bridge_run`/`connect_simulator`) — 서버 프로세스 내부 | `/sim` Skill(L4) + Hook(L1) — 로컬 프로세스 |
> | 추적 대상 | bridge/connection 재접속에 필요한 **세션 식별 정보**(`current_test_name`, `current_tb_source`) | `/sim` **워크플로우 phase**(run→analyze→debug→fix-plan→[fix-design]→fix-implement→fix-review)와 그 산출물(`dump_path`/`csv_path`/`fail_type`/`origin_chain`) |
> | 갱신 시점 | 원격 프로세스 재기동/재접속 시 | compound tool 호출이 반환한 `CompoundResult`를 Skill이 로컬에 받아쓸 때 |
> | 접근 방식 | MCP tool 호출을 통해서만 | 로컬 `Read`/Hook의 plain 파일 I/O |
>
> 동기화는 추가 네트워크 호출 없이 이뤄진다 — `/sim` 워크플로우가 어차피 compound tool을 호출할 때 그 응답(`CompoundResult`)을 Skill이 로컬 `sim-state.json`에 기록할 뿐, `registry.py` 갱신을 위해 별도 왕복을 만들지 않는다. `sim_dir` 필드는 두 상태를 사람이 대조할 수 있도록 `sim-state.json`에도 참고용으로 남긴다(아래 스키마 유지).

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
        "debug": { "path": ".ai/sim-state/TOP015/debug.md", "iteration_count": 1, "updated_at": "2026-04-03T10:15:00" },
        "fix_plan": { "path": ".ai/sim-state/TOP015/fix-plan.md", "fix_target": "rtl", "status": "pending", "revision_count": 0, "approved_at": null },
        "fix_design": null,
        "fix_implement": { "implementer": null, "files_changed": [], "report": "", "revision_count": 0 },
        "fix_review": { "path": ".ai/sim-state/TOP015/fix-review.md", "status": "pending", "iteration_count": 0, "updated_at": null }
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

**`origin_chain` 필드 설명**:

- `fix_plan.fix_target`: `"rtl"` \| `"tb"` — `debug` 5단계에서 `verilog-rtl-debugger`가 판정하고 fix-plan.md에 spec 근거와 함께 기록(§5.8 "Fix Target: RTL vs TB" 참조, anti-tautology 원칙 — "RTL에 맞춰 TB를 고친다"는 근거는 반려 대상). `fix_target=tb`면 `fix_design`/`fix_review`는 이 phase들 자체가 대상이 아니므로 값이 항상 초기값(`null`/`"pending"`)으로 남고, `fix_implement.implementer`는 `verilog-tb-coder` 또는 `human`을 가리킨다.
- `fix_plan.status`/`revision_count`: `status`는 `"pending"`(승인 대기/보류 중) → `"approved"`(승인 완료, fix-implement 진행) 또는 `"superseded"`(`/sim run`으로 새로 시작해 폐기됨, §5.8 참조). `approved_at`은 `status`가 `"approved"`로 바뀌는 순간에만 채워지고, 그 외엔 `null`이다. `revision_count`는 "수정 요청" 루프를 돈 횟수 — 상세 변경 이력은 git-tracked인 fix-plan.md 자체의 git 이력이 정본이고, 이 필드는 `/sim status`에서 빠르게 훑어보기 위한 카운터일 뿐이다.
- `fix_implement.implementer`/`revision_count` + `fix_review`: `implementer`는 `"verilog-rtl-coder"` \| `"verilog-tb-coder"` \| `"human"` \| `null`(아직 구현 전). `report`는 구현 주체가 사람일 수도 있으므로 일반화된 이름(내용은 구현 완료 요약). `revision_count`는 fix-review에서 되돌아간 라운드 수. `fix_review`는 `fix_plan`/`fix_design`과 마찬가지로 `{"path", "status", "iteration_count", "updated_at"}` 포인터 — 실제 리뷰 내용은 git-tracked `fix-review.md`에 append-only로 누적한다. `status`는 `"pending"`(아직 리뷰 전) \| `"clean"`(RTL: 1차 정적+2차 formal 모두 통과 또는 formal 대상 항목 없음, TB: 정적 통과) \| `"issues_found"`(STATIC-CONFIRMED **또는** formal 반례 **또는** tb-reviewer의 issues_found, 어느 쪽이든 이 값 하나로 취급 — `note`에 어느 단계에서 나온 findings인지 프로즈로 남긴다) 3종.
- `debug`: `{"path": ".ai/sim-state/{test}/debug.md", "iteration_count": N, "updated_at": "..."}` — 조사 결과를 JSON 문자열 슬롯에 직접 넣지 않고 문서 포인터로 관리한다. 이유는 `run`(추가 dump 신호로 재실행)→`analyze`→`debug` 재진입마다 inline 슬롯이 덮어써지면 1차 조사에서 세운 가설·배제한 원인·확인한 신호가 2차 조사 시점엔 사라지기 때문이다 — `debug.md`에 append-only로 누적해 재조사 없이 이어서 참고할 수 있게 한다(전체 설계는 §5.8 "0) debug — 조사 노트" 참조).

**어느 phase가 "JSON=포인터, MD=git-tracked 문서" 패턴을 쓰는가**: `debug`/`fix_plan`/`fix_design`/`fix_review` 4개가 해당하고, `run`/`analyze`/`fix_implement`는 그대로 inline 필드로 남긴다 — 이유는 산출물의 성격이 다르기 때문이다.

- `run`/`analyze`는 이미 그 자체가 포인터다(`dump_path`/`csv_path`는 SHM/CSV 실물 파일 경로를 가리킴) — 게다가 같은 dump를 다시 `analyze`하면 같은 CSV가 재생성되는 **결정적(deterministic) 파생 데이터**라, 조사자의 판단이 축적되는 `debug`와 성격이 다르다. 문서로 감쌀 프로즈 자체가 없다.
- `fix_implement`는 실제 변경분이 이미 `db/design`의 RTL(또는 TB) 파일 자체에 git-tracked로 남는다(`files_changed` 목록이 가리키는 그 파일들의 git 이력이 정본) — `report`는 그 위에 얹는 한 줄 요약일 뿐이라 별도 문서가 필요 없다. `fix-review` 게이트로 인해 `fix_implement`도 여러 라운드를 돌 수 있지만, 라운드마다의 코드 diff는 어차피 `db/design`의 git 이력이 갖고 있고, 라운드마다의 "무엇이 문제였는지"는 아래 `fix_review`가 자신의 문서(`fix-review.md`)에 담당하므로, `fix_implement` 쪽에 또 별도 문서를 만들 필요가 없다 — `revision_count` 카운터 하나로 충분하다.
- `fix_review`는 `verilog-rtl-reviewer`(또는 tb-reviewer)가 매 라운드 산출하는 STATIC-CONFIRMED/SIM-RISK(또는 TB의 issues_found) 리뷰 내용이 사람의(agent의) 판단이 누적되는 prose이고, 다음 라운드 재리뷰 시 "이전에 뭘 지적했었는지" 참고할 재사용 가치가 있어 이 패턴에 포함된다 — `debug.md`가 재조사 방지에 쓰이는 것과 정확히 같은 논리다.
- 반대로 `debug`/`fix_plan`/`fix_design`/`fix_review`는 넷 다 **사람 또는 agent의 판단이 누적되는 prose**(가설/배제한 원인, structural delta 선언, ADR, 정적/formal 리뷰 findings)라 재사용·승인·감사 가치가 있고, 이게 이 패턴을 쓰는 공통 기준이다.

**Phase 전이**:

```
idle → run → analyze → debug → fix-plan ⇄ (수정 요청 루프, §5.8) → [fix-design, ARCH일 때만] → fix-implement ⇄ fix-review → (run 재진입)
                 │                  │                                                              │
                 │                  └─ 보류 → 세션 종료해도                                          └─ 문제 발견 → fix-implement로 복귀(§5.8 "4) fix-review")
                 │                     phase="fix-plan" 유지, 다음 세션에
                 │                     재개(debug) 또는 새로 시작(run, 기존 건 superseded)
                 │
                 └─ PASS → 완료 (debug 이하 전부 건너뜀)
```

**갱신 주체**: `/sim` Skill(L4)이 compound operation의 반환값(`CompoundResult`)을 받아 로컬에서 갱신 — Backend는 원격 프로세스라 이 파일에 직접 접근할 수 없다.

#### `sim_state.py` 핵심 함수 (인터페이스 정의)

`§3.2 CompoundResult`와 같은 수준(시그니처 + 한 줄 계약)으로 명시한다 — 실제 구현은 Phase 2 Design/Do에서 진행하고, 이 plan은 CRUD 계약만 고정한다.

```python
def append_debug_note(sim_dir: str, test: str, note: str, context: str) -> None:
    """`.ai/sim-state/{test}/debug.md`에 아래 형식의 섹션을 append(덮어쓰지 않음):

        ## Iteration {iteration_count+1} -- {context} ({updated_at})

        {note}

    `context`는 이번 조사가 왜 일어났는지 한 줄로 표현한 라벨이다. 호출부(Skill)가 상황에 맞게 채운다:
      - "최초 조사"                              — iteration_count==0에서 처음 debug 진입(§4.2 1~5단계)
      - "재개 조사(추가 정보 기반)"                — 0-b단계가 적용된 재진입(§4.2 참조)
      - "fix-plan 수정 요청 재조사(revision N)"    — §5.8 3-way "수정 요청"이 verilog-rtl-debugger에
                                                    재위임해 실제로 새 조사가 일어난 경우(§5.8 "0) debug —
                                                    조사 노트" 표의 "append 시점" 행 참조)
    각 섹션 헤더에 iteration 번호+context+시각이 함께 남으므로, 나중에 debug.md를 훑을 때
    "몇 번째 조사가 어떤 계기로 일어났고 무슨 내용인지"를 순서대로 바로 알 수 있다 — 이게
    이 헤더 포맷을 강제하는 이유다(그냥 텍스트를 이어 붙이기만 하면 나중에 구분이 안 됨).
    origin_chain.debug.iteration_count += 1, updated_at 갱신.
    debug phase 진입 시 이 함수를 호출하기 전에 기존 debug.md를 먼저 Read해서
    이미 결론 낸 추론(확정적으로 배제한 가설 등)을 처음부터 다시 도출하지 않도록 하는 것은
    Skill(§4.2 debug 단계)의 책임이다 — 단, 이건 "같은 결론을 다시 추론하지 말라"는 것이지
    "그 신호를 이번 조사·dump에서 빼라"는 뜻이 아니다(§4.2 debug 0-b단계 참조).
    이번 조사에 필요하면 이전에 확인한 신호라도 다시 포함될 수 있다."""

def write_fix_plan(sim_dir: str, test: str, content: str) -> None:
    """`.ai/sim-state/{test}/fix-plan.md` 작성/개정(덮어씀 — 개정 루프는 같은 파일을 고치는 것이지 새 파일을 만드는 게 아님).
    origin_chain.fix_plan.status="pending", revision_count 갱신(신규 작성 시 0, 개정 루프 라운드마다 +1)."""

def approve_fix_plan(sim_dir: str, test: str) -> None:
    """origin_chain.fix_plan.status: "pending" → "approved", approved_at 기록."""

def supersede_fix_plan(sim_dir: str, test: str) -> None:
    """origin_chain.fix_plan.status: "pending" → "superseded"(fix_design이 존재하면 그것도 함께 superseded).
    phase를 "run"으로 되돌린다. fix-plan.md/fix-design.md 파일 자체는 지우지 않는다 — 삭제해도 git 이력이 남지만,
    삭제 자체를 이 함수가 하지 않는 것이 더 단순하고, `/sim run` 0단계(§4.2)가 이 함수 호출 전에 사용자 확인을 이미 받았으므로
    이 함수 자신은 확인 없이 상태만 반영한다(호출부가 게이트, 함수는 순수 상태 전이).
    origin_chain.debug/debug.md는 건드리지 않는다 — 조사 내용은 fix-plan과 별개로 유효하며,
    새로 추가한 dump 신호로 다시 debug에 들어갔을 때 이어서 참고할 대상이기 때문이다."""

def hold_fix_plan(sim_dir: str, test: str) -> None:
    """상태 변경 없음 — phase="fix-plan" 그대로 두고 세션만 종료. 문서화 목적의 no-op(명시적으로 아무것도 안 함을 기록)."""

def record_fix_implement(sim_dir: str, test: str, implementer: str, files_changed: list, report: str) -> None:
    """origin_chain.fix_implement에 `implementer`("verilog-rtl-coder" | "verilog-tb-coder" | "human"), `files_changed`, `report` 기록.
    AI 경로에서는 coder/tb-coder Task가 반환되는 즉시 Skill이 호출(§4.2 debug 6단계 (a)),
    사람 경로에서는 `/sim run` 0-b단계(§4.2)가 git diff로 완료를 감지한 시점에 호출(§4.2 debug 6단계 (b)).
    phase는 아직 "run"으로 넘기지 않는다 — 다음으로 반드시 fix-review 게이트를 거쳐야 한다(§5.8)."""

def append_fix_review_note(sim_dir: str, test: str, note: str, verdict: str) -> None:
    """`.ai/sim-state/{test}/fix-review.md`에 `append_debug_note()`와 동일한 헤더 포맷으로 append:

        ## Review {iteration_count+1} -- {verdict} ({updated_at})

        {note}

    fix-review 게이트당 이 함수는 정확히 한 번만 호출된다 — RTL 경로에서는 1차(정적, verilog-rtl-reviewer)에서
    STATIC-CONFIRMED가 나오면 그 자리에서 호출하고 2차(formal)로 넘어가지 않으며, 1차가 clean이면
    2차(self-contained 항목에 한해 verilog-rtl-prover 형식 증명)까지 마친 뒤 그 결과로 한 번 호출한다.
    TB 경로에서는 verilog-tb-reviewer의 정적 판정 한 번으로 끝난다(2차 없음).
    `verdict`는 `"clean"`(모든 판정 통과, 또는 해당 없는 판정 단계는 자연히 생략) 또는
    `"issues_found"`(STATIC-CONFIRMED **또는** formal 반례 **또는** tb-reviewer issues_found — 어느 쪽이든 이 값 하나로 취급,
    `note`에 어느 단계에서 나온 findings인지 프로즈로 남긴다) — origin_chain.fix_review.status에도
    그대로 기록. `verdict == "issues_found"`이면 origin_chain.fix_implement.revision_count += 1,
    phase를 "fix-implement"로 되돌린다(구현 주체에게 note의 findings를 전달해 재구현하도록 하는 것은
    Skill의 책임, §4.2 debug 6단계 참조 — "기록=출력" 동일성 명시: 구현 주체가 "human"이면 이 함수를 호출하기
    **전에** Skill이 findings+개선 방향을 담은 "사람 전달용 요약"을 먼저 작성하고, 그 요약 텍스트 자체를 이
    함수의 `note` 인자로 넘긴다(agent 원문을 그대로 넘기는 AI 경로와 다름) — 함수가 반환되면 `fix-review.md`에는
    이미 그 요약이 append돼 있으므로, Skill은 **같은 텍스트를 그대로 채팅 응답에 출력**하기만 하면 된다. 이렇게
    작성 시점을 하나로 묶는 이유는, 채팅 출력과 문서 append를 별개로 작성하면 두 내용이 시간이 지나며
    어긋날 수 있고 "그때 실제로 뭐라고 전달됐는지"를 나중에 fix-review.md만으로 재확인할 수 없게 되기
    때문이다 — AI 경로는 다음 coder/tb-coder Task 프롬프트가 findings 전달 채널을 자동으로 제공하므로
    이 절차가 필요 없다. 상세는 §5.8 "4) fix-review" "구현 주체=사람일 때 findings 전달" 참조).
    `verdict == "clean"`이면 phase를 "run"으로 전환 — 이 함수가 fix-review 게이트의 유일한 출구다."""
```

> **`supersede_fix_plan`이 `debug`를 리셋하지 않는 이유가 이 두 함수 세트의 핵심 설계 결정이다**: "다시 run부터 시작"은 fix-plan(승인 대상 계획)을 폐기하는 것이지, 그 계획의 근거가 된 조사 자체를 폐기하는 게 아니다. 추가 dump 신호로 재실행 후 `debug`에 재진입하면, `append_debug_note`가 기존 `debug.md`에 이어서 쓰므로 1차 조사 내용을 다시 처음부터 재현할 필요가 없다 — 다음 `write_fix_plan` 호출 시점의 근본원인은 1차+2차 조사를 합친 더 완전한 근거 위에서 작성된다.

### 5.2 TB 분석서 YAML Frontmatter

**출처**: compound-engineering document-driven state

기존 `.ai/analysis/tb_*.analysis.md`에 YAML frontmatter를 추가한다. Skill이 파싱하여 compound operation 파라미터를 자동 구성한다.

**frontmatter는 `verilog-tb-analyst`(agent 미설치 시 Claude가 직접, `references/phase-0-discovery.md` §0A/0B에 서술된 실행 주체와 동일)가 TB 분석서를 작성·갱신하는 바로 그 시점에 함께 생성한다** — §0A/0B가 이미 뽑아내는 "필수 포함 항목"(판별 신호+기대값 → `pass_signals`/`fail_conditions`, 시뮬레이션 길이 → `sim_duration`)을 프로즈 작성과 동시에 아래 YAML 스키마로도 기록하는 것이며, `/sim` Skill(L4)은 그 결과물을 **그대로 파싱만** 한다 — 별도의 재파싱·재생성 단계는 없다. 이 스키마의 정본은 이 plan(Phase 2)이 정의하고, `phase-0-discovery.md` §0A/0B(Phase 1 소유 파일, 같은 저장소의 `skill-src/xcelium-sim/`)에 "이 형식으로 frontmatter도 함께 작성하라"는 지시로 Phase 2가 추가한다. `verilog-tb-analyst` agent 자체의 정의 문서는 chip-design-skills repo 소유라 이 plan 범위 밖이다.

`tb_source.combined_sha256` 체크섬(F-175로 이미 구현됨)을 `sim_batch_run`/`sim_regression`이 매번 자동 계산해 분석서 헤더에 기록하고, 재사용 전 이 값만 비교해 신선도를 판정한다 — 수동 날짜 갱신은 불필요하다(`references/phase-0-discovery.md` §0C 참조).

이미 작성되어 있는 `tb_TOP012~016`(frontmatter 없이 작성됨)은 이 컨벤션 적용 전 산출물이므로 1회성 backfill이 필요하지만, 그 이후 신규·갱신 TB 분석서부터는 작성 시점에 자동으로 포함된다.

`/sim run`의 "frontmatter 없음 → AI가 본문 읽어서 판단" fallback(§4.2)은 **정상 경로가 아니라** 이 backfill 대상 레거시 문서나 `verilog-tb-analyst` 미설치 환경에 대한 대비책이다.

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
| debug | fix-plan 문서 작성, `fix_target=rtl` | 사용자 승인 게이트 → (승인 시) 구현 주체 선택(`verilog-rtl-coder` agent 또는 사람 직접 수정, [ARCH면 fix-design 선행], §5.8) → `verilog-rtl-reviewer`+`verilog-rtl-prover` 필수 리뷰(fix-review) → `/sim verify {test}` (§4.2/§4.5/§5.8 참조) |
| debug | fix-plan 문서 작성, `fix_target=tb` | 사용자 승인 게이트 → (승인 시) 구현 주체 선택(`verilog-tb-coder` agent 또는 사람 직접 수정) → `verilog-tb-reviewer` 필수 정적 리뷰(fix-review) → `/sim verify {test}`(§4.5/§5.8 참조) |
| debug | bridge 필요 | `/sim debug {test} --bridge` |
| fix-review | 문제 발견(STATIC-CONFIRMED, formal 반례, 또는 tb-reviewer issues_found) | AI 구현이면 findings와 함께 coder/tb-coder에 재위임, 사람 구현이면 Skill이 findings+개선 방향 요약을 `append_fix_review_note()`로 `fix-review.md`에 기록한 뒤 동일 텍스트를 채팅에도 출력("기록=출력" 동일성, §5.8 "4) fix-review" 참조) → 재구현 → fix-review 재진입 |
| fix-review | clean | `/sim verify {test}` (phase가 `run`으로 복귀) |
| run | 사람 구현 대기 중, diff 없음 | 안내만 하고 대기 — 사용자가 직접 수정 후 `/sim run {test}` 재실행 |
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
[Run] TOP015           → in_progress
[Analyze] TOP015       → pending (blockedBy: Run)
[Debug] TOP015         → pending (blockedBy: Analyze, FAIL시만)
[Fix-Plan] TOP015      → pending (blockedBy: Debug, §5.8 — fix-plan 문서 작성+승인 대기)
[Fix-Design] TOP015    → pending (blockedBy: Fix-Plan, coder A0가 ARCH 판정 시만 생성되는 조건부 task)
[Fix-Implement] TOP015 → pending (blockedBy: Fix-Plan 또는 Fix-Design — ARCH면 후자, 아니면 전자)
[Fix-Review] TOP015    → pending (blockedBy: Fix-Implement, §5.8 — 필수 task. 문제 발견 시 Fix-Implement로 재귀 blockedBy)
[Verify] TOP015        → pending (blockedBy: Fix-Review clean — PASS 경로는 Analyze, FAIL 경로는 Fix-Review로 통일)
```

`[Fix-Design]`은 ARCH 판정일 때만 실제로 생성되는 조건부 task — LOCAL/IFACE면 `[Fix-Implement]`가 `[Fix-Plan]`에 바로 `blockedBy`. `[Fix-Review]`는 조건부가 아니라 **항상 생성되는 필수 task**이고, 유일하게 자기 자신의 선행 task로 되돌아갈 수 있는 task다 — STATIC-CONFIRMED 문제, formal 반례, 또는 TB의 issues_found가 발견되면 `[Fix-Implement]`가 `in_progress`로 재오픈되고 `[Fix-Review]`가 다시 그 뒤에 `blockedBy`로 붙는다(사람이 매번 개입하는 유한 루프, §11 참조).

### 5.6 Origin Linking

**출처**: compound-engineering Brainstorm→Plan 참조 체인

sim-state.json의 `origin_chain`에 각 단계 산출물을 기록한다. `fix_plan`은 `debug`의 근본원인을 참조하고, `fix_design`(있으면)은 `fix_plan`의 structural delta 선언을 참조하며, `fix_implement`는 `fix_plan`(및 있으면 `fix_design`)이 승인·비준한 범위를 참조하고, `fix_review`는 그 `fix_implement`가 만든 실제 diff를 참조한다. 즉 origin chain은 `run → analyze → debug → fix_plan → [fix_design] → fix_implement → fix_review` 전 구간에서 끊기지 않는다 — 나중에 "이 코드가 왜 이렇게 바뀌었는지"뿐 아니라 "구현 당시 정적/formal 리뷰에서 무엇을 지적했었는지"까지 `fix_review`에서 역방향으로 `fix_implement`/`fix_plan`/`debug`/`analyze`/`run`까지 전부 따라갈 수 있다.

### 5.7 Parallel FAIL Analysis

**출처**: compound-engineering parallel subagent

regression 복수 FAIL 시 Agent 병렬 분석:
- FAIL 2개 이상 → 각각 Agent("analyze {test}") 병렬 실행
- Agent는 텍스트만 반환, orchestrator가 취합
- 공통 패턴 도출 ("두 FAIL 모두 CHK_ADR 관련")
- 최대 3개 병렬, 나머지 순차
### 5.8 Fix Sub-cycle — Plan → Design(조건부) → Implement → Review

**출처**: bkit PDCA(Plan→Design→Do)를 이 프로젝트의 단일 버그-fix 스코프로 축소 이식 + `verilog-rtl-coder`(chip-design-skills)의 기존 PLAN-BEFORE-CODE 규율(A0 model-diff gate, A1..A7 산출물)을 그대로 재사용.

**왜 이런 구조인가**: "근본원인+수정 제안 → 사용자 승인(말로) → coder가 바로 구현"이라는 얕은 구조는 승인 대상이 구두 제안이라 검토 가능성이 낮고, coder가 A0/A1-A7을 자기 컨텍스트 안에서만 산출한 뒤 바로 코드를 쓰므로 "계획을 먼저 고정하고 그 계획만 구현"이 구조적으로 강제되지 않는다. `/sim` 워크플로우 레벨에 **문서화된 계획 → 승인 → (필요시 설계 비준) → 구현 → 필수 리뷰**라는 게이트를 두어 완성도를 높인다. bkit의 전체 PDCA(9-phase, `docs/01-plan/`~`docs/04-report/`, Task 시스템 연동)를 그대로 쓰지 않는 이유는 이건 새 feature가 아니라 **단일 테스트의 단일 버그 fix**라는 훨씬 작은 스코프라서다 — 무게가 안 맞는다.

#### Phase 상태 확장

```
                                                     ┌─ fix_target=rtl ─→ [fix-design, ARCH만] → fix-implement(coder/사람)   ⇄ fix-review(reviewer+prover)   → run(재진입)
idle → run → analyze → debug → fix-plan ─┤                                        │                                              │
                                                     └─ fix_target=tb  ──────────────────────→ fix-implement(tb-coder/사람) ⇄ fix-review(tb-reviewer) → run(재진입)
                                                                                                (fix-design 없음)
```

#### Fix Target: RTL vs TB

**출처**: 이 문서는 기본적으로 검증 flow라서 이미 testbench와 testcase가 작성된 것을 시뮬레이션하고 RTL을 검증하는 데 초점이 맞춰져 있었다. 그러나 bug를 수정하면서 testbench나 testcase를 수정해야 할 일도 생긴다는 문제 제기를 반영한 확장.

**왜 필요한가**: `verilog-rtl-debugger`(§4.2 debug 5단계)는 조사 과정에서 RTL과 TB를 함께 읽으므로, 결론이 "RTL이 spec대로 안 만들어짐"이 아니라 **"TB의 기대값·자극 생성 자체가 spec과 다르게 잘못 작성됨"**일 수 있다 — 이건 RTL을 고쳐서 해결되는 문제가 아니다. 이 두 근본원인을 구분하지 않으면 fix-plan이 항상 RTL 쪽만 고치도록 유도되고, 실제로는 TB가 틀린 케이스에서 존재하지도 않는 RTL 버그를 찾아 헤매거나(또는 억지로 RTL을 "고쳐서" 증상만 없앰 — 실제로는 새 버그 주입), 반대로 TB가 틀렸다고 성급히 판단해 checker를 완화하면 **진짜 RTL 버그를 숨기는** 결과가 될 수 있다(아래 "anti-tautology" 참조).

**확정**: `debug` 5단계에서 `verilog-rtl-debugger`가 근본원인을 도출할 때 **`fix_target: "rtl" | "tb"`도 함께 판정**하고, `fix-plan.md`(아래 "1) fix-plan")에 **"근본원인 소재 판정 근거"를 필수 항목으로 추가**한다 — 이 판정은 fix-plan의 3-way 승인 게이트(§4.2 debug 6단계)에서 사람이 함께 검토하므로, 별도의 새 승인 단계를 추가하지 않는다.

`fix_target`에 따라 이후 경로가 갈라진다:

| | `fix_target: "rtl"` (아래 "2)~4)") | `fix_target: "tb"` |
|---|---|---|
| fix-design(ARCH escalation) | 있음 — coder의 A0 model-diff gate가 ARCH 판정 시 진입 | **없음** — TB용 구조적 escalation 기준(자동 classifier)은 이 plan이 정의하지 않고, `verilog-tb-coder`/`verilog-tb-reviewer` 스스로의 판단(STOP-and-Ask, ARCH-SUSPECT-equivalent refer)에 맡김(아래 "정직한 한계" 참조) |
| fix-implement 구현 주체 | `verilog-rtl-coder` 또는 사람(아래 "3) fix-implement") | **`verilog-tb-coder`**(신규 agent, chip-design-skills 소유) 또는 사람 |
| fix-review(정적 게이트) | 있음 — `verilog-rtl-reviewer`/`verilog-rtl-prover`(아래 "4) fix-review") | **있음** — `verilog-tb-reviewer`(신규 agent, chip-design-skills 소유) — formal 단계 없이 정적 검토 1단만 |
| 시뮬레이션 재검증 | `/sim verify`(공통) | `/sim verify`(공통) |

**anti-tautology(가장 중요한 원칙, `verilog-rtl-coder`의 anti-tautology 원칙과 정확히 같은 이유)**: TB 수정은 반드시 **spec 대비** 정당화돼야 하고, "지금 RTL 동작에 맞춰 test를 통과시키기 위해서"가 이유가 되면 안 된다 — 그러면 진짜 RTL 버그를 checker가 못 잡게 완화하는 결과가 된다. fix-plan.md의 "근본원인 소재 판정 근거"는 spec(또는 `.ai/analysis/tb_*.analysis.md`의 판별 신호/기대값 정의) 대비 TB의 기대값·자극이 왜 틀렸는지를 근거로 들어야 하며, "RTL이 이렇게 동작하니 TB를 맞춘다"는 근거는 승인 게이트에서 반려 대상이다. 이 원칙은 "0) debug — 조사 노트"의 조사 내용(가설·배제한 원인)에도 반영되도록 `append_debug_note()` 호출 시 spec 근거를 함께 기록한다.

**anti-tautology의 최종 판단은 독립된 reviewer가 한다**: 이 재확인을 `verilog-tb-coder` 자신의 self-check에만 맡기면, RTL 쪽이 "coder는 자기 의도를 스스로 인증하지 않는다 — 정확성 property는 Prover가 독립적으로 저작한다"고 못박아둔 것과 정확히 같은 종류의 모순이 된다 — 코드를 쓴 당사자가 "이 코드가 spec에 맞다"를 스스로 판단하면 tautology가 된다. **확정**: `verilog-tb-coder`의 spec 정당화 확인은 **빠른 1차 필터**(명백히 틀린 경우 조기 발견)로 두고, **권위 있는 최종 판단은 독립된 `verilog-tb-reviewer`**(신규, read-only)가 fix-review 게이트에서 내린다 — RTL 쪽에서 coder의 A0 self-check(구조적 필터)와 reviewer/prover의 독립 검증(정확성 판단)이 나뉘어 있는 것과 동일한 2단 구조를 TB 쪽에도 적용한다.

**신규 agent — `verilog-tb-coder`(chip-design-skills 소유, 이 plan 범위 밖)**: `verilog-rtl-coder`는 이미 비준된 RTL micro-architecture 안에서만 구현하는 constrained implementer로 설계돼 있어 TB 작성까지 겸하면 그 agent의 정체성이 흐려진다 — TB 작성은 spec 이해·coverage 모델·프로토콜 준수라는 다른 판단이 필요하다. 이 agent의 정의(agent 파일·plan 문서)는 chip-design-skills repo 소유이며, 이 plan은 그 존재를 전제로 호출 인터페이스만 참조한다(cross-repo 계약, `verilog-tb-analyst`/`verilog-rtl-debugger`와 동일 패턴) — 상세 spec은 별도로 chip-design-skills에 `verilog-tb-coder.plan.md`로 작성한다.

**신규 agent — `verilog-tb-reviewer`(chip-design-skills 소유, 이 plan 범위 밖)**: `verilog-rtl-reviewer`와 대칭되는 TB 전용 read-only 검토자 — TB 코드를 직접 고치지 않고, spec 정당화 재확인(anti-tautology 최종 판단)·checker 완화 탐지·fix-plan 범위 준수·구조적 변경 의심(ARCH-SUSPECT-equivalent) 여부를 검토해 리포트만 남긴다. RTL의 T1-T9 taxonomy·`boundary-classifier.py`는 이식하지 않는다(카테고리 오류 방지, 아래 "정직한 한계" 참조) — TB 자체의 검토 항목 체계는 별도로 정의한다. 상세 spec은 chip-design-skills에 `verilog-tb-reviewer.plan.md`로 작성한다.

**RTL+TB 동시 수정이 필요한 경우**: 실제로 있을 수 있다 — 예를 들어 인터페이스 컨벤션이 spec 대비 애매해서 RTL과 TB 양쪽이 다 spec과 다르게 구현된 경우. 이 plan은 `fix_target`에 `"both"` 같은 3번째 값을 두지 않는다 — 혼합 대상을 하나의 fix-plan에 넣으면 구현 주체 2개(coder+tb-coder)와 리뷰 게이트 2개(reviewer/prover + tb-reviewer)가 한 상태 머신 안에서 뒤섞여 복잡도가 크게 늘어난다. **확정**: 근본원인이 RTL과 TB 양쪽에 걸치면 **두 개의 독립된 fix-plan(하나는 `fix_target=rtl`, 하나는 `fix_target=tb`)으로 분리**해 순차 처리한다 — 각 fix-plan.md의 "근본원인 요약"에 서로의 경로를 프로즈로 상호 참조만 남긴다(새 스키마 필드 추가 안 함, YAGNI). 원자성(둘이 항상 함께 반영됨을 보장)은 없어지지만, 이미 만든 단일-대상 Fix Sub-cycle 구조를 그대로 재사용할 수 있어 훨씬 단순하다 — 어느 쪽을 먼저 처리할지(보통 근본원인을 더 명확히 설명하는 쪽 우선)는 사용자 판단에 맡긴다.

**정직한 한계(YAGNI, 지금 해결하지 않음)**:
- TB 변경에도 "구조적 변경"(예: 새 시퀀스 클래스, 새 UVM agent/컴포넌트 추가) 개념이 있을 수 있으나, 이를 RTL의 ARCH/IFACE/LOCAL처럼 자동 분류하는 기준을 이 plan은 정의하지 않는다 — `verilog-tb-coder`/`verilog-tb-reviewer`가 스스로 판단해 STOP-and-Ask·ARCH-SUSPECT-equivalent refer로 사람에게 확인받는 것으로 충분하다고 보고, 별도 classifier 도구는 만들지 않는다(RTL의 `boundary-classifier.py` 같은 전용 도구는 미래 필요 시 검토).
- `fix_target=both`(RTL+TB 동시) 같은 혼합 대상 자동 처리는 만들지 않는다 — 위 "RTL+TB 동시 수정" 항목대로 두 개의 순차 fix-plan으로 분리하는 것이 이 plan의 확정 방침이다.

#### 0) `debug` — 조사 노트

`fix-plan`보다 앞선 `debug` phase 자체도 이 문서 컨벤션(git-tracked)을 따른다 — fix-plan/fix-design과 이유가 다르다는 점에 주의: fix-plan은 **승인 대상**이라 문서화하지만, `debug.md`는 승인 대상이 아니라 **재조사 방지용 누적 기록**이다.

| 항목 | 내용 |
|------|------|
| 산출물 | `{project}/.ai/sim-state/{test}/debug.md` — **git-tracked**, `fix-plan.md`/`fix-design.md`와 같은 디렉터리(§5.1 "동반 디렉터리" 참조) |
| 갱신 방식 | **append-only** — `sim_state.py`의 `append_debug_note()`(§5.1)가 매 debug iteration마다 이어서 씀. fix-plan/fix-design과 달리 승인 게이트가 없어 "덮어쓰기 vs 개정"을 구분할 필요가 없다 |
| 왜 필요한가 | `/sim run`으로 추가 dump 신호를 넣어 재실행 → `debug` 재진입 시나리오에서, 이전 iteration의 가설·배제한 원인·확인한 신호가 사라지면 매번 원점부터 재조사해야 한다. `origin_chain.debug`를 inline 텍스트가 아니라 이 문서를 가리키는 포인터로 바꿔 해결(§5.1 참조). **주의**: 여기서 "재조사하지 않는다"는 이미 결론 낸 추론을 되풀이하지 않는다는 뜻이지, 그 신호를 새 dump/조사 대상에서 빼라는 뜻이 아니다 — 필요하면 이전에 확인한 신호도 이번 조사에 다시 포함될 수 있다(§4.2 debug 0-b단계 참조) |
| `fix-plan`/`fix-design`과의 관계 | `fix-plan`이 `superseded`되어 `/sim run`으로 재시작해도(`supersede_fix_plan`, §5.1) `debug.md`는 건드리지 않는다 — 계획은 폐기돼도 조사 근거는 유효하기 때문 |
| append 시점 | **debug phase의 1~5단계**(§4.2, 최초/재개 조사)뿐 아니라 **fix-plan "수정 요청"이 재조사로 이어질 때**(§4.2 debug 6단계, 아래 표 "수정 요청" 행)도 append한다 — 근본원인이 승인 게이트를 통과한 뒤에도 재검토될 수 있고, 그 재검토 내용 역시 "재활용 가능한 조사 기록"이라는 이 문서의 목적에 포함되기 때문 |
| 기록 형식 | 매 append는 `## Iteration {N} -- {context} ({timestamp})` 헤더로 시작 — `context`는 이번 조사가 왜 일어났는지 한 줄 라벨(`append_debug_note()` §5.1 참조). 헤더로 iteration을 구분해두면 나중에 debug.md를 훑을 때 "몇 번째 조사가 어떤 계기로 일어났는지"가 바로 보인다. 예시: |

```markdown
## Iteration 1 -- 최초 조사 (2026-04-03T10:15:00)

가설: r_regAddr 레이스 컨디션. 확인 신호: r_regAddr, r_streamRwState.
배제한 원인: clock domain crossing(단일 클록 확인됨).

## Iteration 2 -- fix-plan 수정 요청 재조사(revision 1) (2026-04-03T11:40:00)

피드백: "SCL 타이밍도 의심된다" → 추가 확인 결과 SCL 관련 아님, 기존 결론(r_regAddr 레이스) 유지.
```

#### 1) `fix-plan`

| 항목 | 내용 |
|------|------|
| 산출물 | `{project}/.ai/sim-state/{test}/fix-plan.md`(테스트당 활성 fix-plan은 최대 1개뿐이라 `{slug}` 구분이 불필요, `debug.md`/`fix-design.md`와 한 디렉터리에 모아 테스트 하나의 조사~계획~설계 산출물을 한곳에서 보게 함, §5.1 참조) — **git-tracked**(sim-state.json과 달리 의사결정 기록이라 남길 가치 있음, `.ai/analysis/`와 동일한 컨벤션) |
| 필수 포함 항목 | **`fix_target: rtl \| tb` + 그 판정 근거**(spec 또는 `.ai/analysis/tb_*.analysis.md`의 기대값 정의 대비 — "RTL에 맞춰 TB를 고친다"는 근거는 반려 대상, 위 "Fix Target: RTL vs TB" anti-tautology 참조), 근본원인 요약(`/sim debug` 산출물 재사용), 영향 모듈/파일, **structural delta 선언**(`fix_target=rtl`일 때만 — coder A0가 요구하는 것과 동일 형식, "어떤 always/net/FSM/clock/instance가 바뀌는가". `fix_target=tb`면 대신 영향받는 테스트/시퀀스/checker 목록을 적는다), 수정 approach, 검증 대상(재실행할 `/sim verify {test}` 목록) |
| 작성 주체 | `verilog-rtl-debugger`가 이미 근본원인 조사를 마쳤으므로 초안은 debugger의 조사 결과를 `/sim` Skill이 문서로 저장 — RTL 코드가 아니라 계획 문서라 Write 권한 없는 debugger도(Skill이 대신 파일 기록) 문제 없음 |
| 승인 게이트 | AskUserQuestion "이 fix-plan대로 진행할까요?" — **3-way**: 승인 / 수정 요청 / 보류 |

##### 승인 게이트 3-way

| 선택 | 동작 |
|------|------|
| **승인** | `approve_fix_plan()`(§5.1)으로 status를 `approved`로 전환 후 **구현 주체 선택**(AI `verilog-rtl-coder`/`verilog-tb-coder` 위임 또는 사람이 직접 수정)을 거쳐 `fix-implement`로 진행 — 상세는 아래 "3) fix-implement" 참조 |
| **수정 요청** | 사용자 피드백 수집 → **가벼운 수정**(approach 조정, 누락 파일 추가 등)은 새 조사가 없으므로 `/sim` Skill이 `debug.md`에 아무것도 append하지 않고 직접 fix-plan.md만 Edit. **근본원인 재조사가 필요한 피드백**("다른 원인 아닐까?" 등)이면 `verilog-rtl-debugger`에 피드백과 함께 재위임 → 재조사 결과를 `append_debug_note(..., context="fix-plan 수정 요청 재조사(revision {N})")`(§5.1)로 `debug.md`에 먼저 append한 뒤, 그 내용을 반영해 `write_fix_plan()`(§5.1)으로 fix-plan.md를 개정하고 **이 승인 게이트를 다시 표시**(승인될 때까지 반복, phase는 계속 `fix-plan`). `origin_chain.fix_plan.revision_count`를 라운드마다 1씩 증가시켜 `/sim status`에서 빠르게 확인 가능하게 하고, 상세 diff는 git-tracked인 fix-plan.md 자체의 git 이력으로 추적 |
| **보류** | **종료가 아니다.** `hold_fix_plan()`(§5.1)이 상태를 바꾸지 않고 sim-state.json은 `phase: "fix-plan"`, `origin_chain.fix_plan`(fix-plan.md 경로 포함)을 그대로 유지한 채 이번 세션만 멈춘다. 다음 세션(또는 나중)에 두 경로로 재개 가능(§4.2 debug 0단계·run 0단계): (a) `/sim debug {test}` → 재조사 없이 기존 fix-plan.md로 바로 이 승인 게이트 재표시("이어서 결정"), (b) `/sim run {test}` → 확인 후 `supersede_fix_plan()`(§5.1)이 기존 fix-plan.md의 `status`만 `superseded`로 표시(파일은 유지, `debug.md`도 무관하게 유지)하고 완전히 새로 시작("포기하고 새로", 위 "0) debug — 조사 노트" 참조) |

#### 2) `fix-design` (조건부 — `fix_target=rtl`이고 coder의 A0가 ARCH일 때만)

**`fix_target=tb`면 이 phase 자체가 대상이 아니다** — ARCH/IFACE/LOCAL 분류(`boundary-classifier.py`)는 RTL 전용 도구라 TB 코드에 적용되지 않는다(위 "Fix Target: RTL vs TB" "정직한 한계" 참조).

| 항목 | 내용 |
|------|------|
| 트리거 | `verilog-rtl-coder`가 fix-plan을 입력받아 자체 A0 model-diff gate(`boundary-classifier.py`) 실행 → **ARCH**(새 FSM/module/instance/case-arm, clock/reset re-wire) 판정 |
| 산출물 | `{project}/.ai/sim-state/{test}/fix-design.md` — `verilog-rtl-architect-advisor`가 escalate 받아 산출하는 ADR(coder 자신의 `adr-template.md` 재사용 — 별도 template reference 불필요, `fix-review.md`가 reviewer/prover 자체 리포트 형식을 재사용하는 것과 같은 이유) |
| 승인 게이트 | 사용자가 ADR(partitioning 결정)을 재승인해야 fix-implement 재개 |
| LOCAL/IFACE인 경우 | 이 phase 자체를 **스킵** — fix-plan의 structural delta 선언만으로 충분, 불필요한 무게를 붙이지 않음(이 문서 전반의 YAGNI 원칙과 일치) |

#### 3) `fix-implement`

**출처**: "실제 verilog-rtl-coder를 사용하는 자동화가 최종목표이긴 하지만, 사람이 직접 고칠 수도 있다" / "spec을 가지고 testcase를 작성하는 agent는 따로 있어야 한다"는 요구를 반영한 구조.

승인된 fix-plan(+있으면 비준된 fix-design/ADR)을 실제 코드로 옮기는 단계. 구현 주체는 `fix_target`에 따라 다르지만, 둘 다 "AI 위임 또는 사람 직접 수정" 2-way 구조는 동일하다(§4.2 debug 6단계 "구현 주체 선택" 참조):

**`fix_target=rtl`인 경우**:

| 구현 주체 | 동작 |
|-----------|------|
| **(a) AI — `verilog-rtl-coder`** | Task로 호출하되, 프롬프트에 **fix-plan(+있으면 비준된 fix-design/ADR) 파일 경로를 명시적으로 전달**하고 "이 문서 범위 밖의 변경이 필요하면 멈추고 보고하라"는 제약을 건다 — coder 자신의 기존 "STOP-and-Ask" 원칙에 이미 있는 규율을 문서로 앵커링하는 것이지 새 규율을 발명하는 게 아니다. 완료 조건: coder 자체 Definition of Done(A0 post-implementation guardrail, lint/elaboration, fan-out audit, 분석서 갱신, taxonomy self-check) 통과. Task 반환 즉시 Skill이 `record_fix_implement(sim_dir, test, implementer="verilog-rtl-coder", files_changed=[...], report=coder의_요약)`(§5.1)를 호출하고 **같은 세션 안에서 바로** 아래 "4) fix-review"로 진행 |
| **(b) 사람 — 직접 수정** | Skill은 fix-plan.md 경로와 structural delta 선언을 안내만 하고 코드를 건드리지 않는다. 사람이 자신의 에디터에서 직접 수정하고 `/sim run {test}`(또는 `/sim verify {test}`)를 다시 실행하면, **run 0-b단계**(§4.2)가 fix-plan.md 선언 파일들의 git diff 유무로 완료를 감지 — diff가 있으면 `record_fix_implement(sim_dir, test, implementer="human", files_changed=git_diff_결과, report="")`(§5.1, `report`는 빈 문자열이어도 무방 — coder처럼 자체 리포트를 낼 의무가 없음)를 호출하고 아래 "4) fix-review"로 진행 |

**`fix_target=tb`인 경우**:

| 구현 주체 | 동작 |
|-----------|------|
| **(a) AI — `verilog-tb-coder`**(chip-design-skills 소유, 신규 agent — cross-repo 계약, 위 "Fix Target: RTL vs TB" 참조) | Task로 호출하되, fix-plan.md(영향 테스트/시퀀스/checker 목록 + 근본원인 소재 판정 근거) 경로를 전달하고 "spec 대비 정당화되지 않는 변경(단순히 현재 RTL 동작에 맞추는 변경)은 멈추고 보고하라"는 제약을 건다(anti-tautology 1차 필터 — **최종 판단은 아님**, 권위 있는 판단은 아래 "4) fix-review"의 `verilog-tb-reviewer`가 독립적으로 내림) — 이 agent 자체의 설계·STOP-and-Ask 규율은 chip-design-skills의 `verilog-tb-coder.plan.md`/`agents/verilog-tb-coder.md` 소관이며 이 plan은 재정의하지 않는다. 완료 시 `record_fix_implement(sim_dir, test, implementer="verilog-tb-coder", files_changed=[...], report=...)`(§5.1) 호출 → **이 세션 안에서 곧바로** 아래 "4) fix-review"로 진행 |
| **(b) 사람 — 직접 수정** | RTL (b) 경로와 완전히 동일한 메커니즘(run 0-b단계 git diff 감지) — 대상이 `db/design`이 아니라 TB 소스(`tb_tests/`, 공유 컴포넌트 등)라는 점만 다르다 |

`fix-plan.md`에는 두 경우 모두 "구현 완료" 표시 + 실제 변경 파일 목록을 추가한다 — 구현 주체가 사람이어도 fix-plan.md 자체의 갱신은 Skill이 대신 기록한다(fix-plan.md는 계획 문서이지 RTL이 아니므로 Skill이 직접 Edit해도 이 프로젝트의 "코드는 Skill이 건드리지 않는다"는 원칙과 충돌하지 않는다).

> **(a)와 (b)의 근본적 차이 — 동기 vs 비동기**: AI 경로는 Task 호출이 그 자리에서 완료·반환되므로 같은 `/sim debug` 호출 안에서 곧바로 리뷰까지 이어갈 수 있다. 사람 경로는 "언제 다 고칠지"를 Skill이 제어할 수 없으므로(에디터 밖에서 일어나는 일이라 실시간 감지 자체가 불가능), 완료 여부를 **다음 `/sim run` 호출 시점에 git diff로 사후 감지**하는 방식을 택했다 — 이는 §5.2가 이미 채택한 "사람이 수동으로 완료 신호를 주는 대신 자동으로 감지한다"(F-175의 `combined_sha256` 원칙)는 이 문서의 기존 설계 철학과 일관된다.
>
> **coder의 A0 self-check이 없다는 것의 의미**: (a)는 coder 스스로 ARCH 여부를 판정해 fix-design으로 escalate하는 자체 안전장치가 있지만, (b)는 그런 self-check이 없다 — 사람이 fix-plan의 범위를 벗어나 구조적으로 바꿔도 자동으로 잡아내는 장치가 없다는 뜻이다. 이게 바로 아래 "4) fix-review"가 **구현 주체와 무관하게 항상 필수**여야 하는 이유 중 하나다 — coder의 자체 검사가 없는 경로(사람)에게는 사실상 유일한 안전망이고, coder의 자체 검사가 있는 경로(AI)에게도 독립된 두 번째 검증(defense-in-depth)이 된다.

#### 4) `fix-review` (필수 — `fix_target` 무관하게 항상 실행)

**출처**: `verilog-rtl-coder`가 고친 것과 사람이 고친 것 모두, 시뮬레이션으로 검증하기 전에 reviewer를 활용해 버그를 미리 방지하는 것이 당연한 방어선이라는 판단. formal은 reviewer가 아니라 별도 agent(`verilog-rtl-prover`)가 정적 리뷰와 같은 게이트 안에서 수행한다. TB 쪽도 "testbench 역시 architecting이 가능하고 self-check만으로는 부족하다"는 문제 제기를 반영해 독립 reviewer를 둔다.

**왜 필요한가**: `/sim verify`의 다음 단계는 실제 시뮬레이션 재실행이다 — 이건 상대적으로 비싸다(빌드+실행+로그/CSV 분석, 초~분 단위). `verilog-rtl-reviewer`/`verilog-tb-reviewer`는 모두 chip-design-skills가 보유한 read-only 정적 리뷰 agent로, diff를 훑어 **STATIC-CONFIRMED**(정적 검토만으로 확정 가능한 문제 — 반드시 잡아야 함)와 그 외(RTL은 SIM-RISK, TB는 "시뮬레이션으로만 확인 가능한 나머지")로 분리해 리포트한다. STATIC-CONFIRMED급 문제는 정의상 **시뮬레이션 없이도 지금 당장 잡을 수 있는** 문제이므로, 비싼 시뮬레이션 사이클을 태우기 전에 이 값싼 게이트로 먼저 거르는 게 당연히 이득이다 — fix-plan 문서화가 "계획 단계에서 즉흥 코딩을 막는" 것과 같은 defense-in-depth 원리를, 이번엔 "구현 직후·시뮬레이션 직전"이라는 지점에 하나 더 세우는 것이다.

**formal도 같은 논리로 이 게이트에 포함된다(RTL 경로 한정)**: SIM-RISK 중 `verilog-rtl-reviewer`가 이미 "self-contained 로직/timing"(예: FSM corner deadlock, 포인터 wrap, off-by-one — 실제 클록 타이밍이나 외부 자극 순서와 무관하게 모듈 하나만으로 증명 가능한 주장)으로 분류해 `verilog-rtl-prover`에게 라우팅하는 항목은, formal 자체가 **시뮬레이션 없이 sby로 끝나는 값싼 검증**이라는 점에서 STATIC-CONFIRMED와 성격이 같다 — 정적 분석과 마찬가지로 "지금 당장, 시뮬레이션 없이 잡을 수 있는" 범주다. 그래서 이 부류는 reviewer의 라우팅을 "참고만 하고 끝"내지 않고, **fix-review 게이트가 그 자리에서 `verilog-rtl-prover`를 실제로 호출**해 증명까지 마친다 — 반례가 나오면 STATIC-CONFIRMED와 동일하게 차단한다. 반면 CDC 타이밍·protocol-relational처럼 **진짜로 동적 동작(실제 클록 관계, 외부 자극 순서)이 있어야만 확인되는** SIM-RISK는 formal로도 못 잡으므로, 여기서는 여전히 차단하지 않고 `/sim verify`(실제 시뮬레이션)로 넘긴다 — "self-contained면 formal로 지금 끝낸다, 아니면 시뮬레이션까지 미룬다"가 이 구분의 기준이다.

| 항목 | 내용 |
|------|------|
| 트리거 | `fix-implement` 완료(구현 주체가 AI든 사람이든 무관) — **조건부 아님**, `fix_target` 무관하게 매번 반드시 실행 |
| 산출물 | `{project}/.ai/sim-state/{test}/fix-review.md` — **git-tracked**, `debug.md`/`fix-plan.md`/`fix-design.md`와 같은 디렉터리(§5.1 참조) |
| 갱신 방식 | **append-only** — `sim_state.py`의 `append_fix_review_note()`(§5.1)가 매 리뷰 라운드마다 `## Review {N} -- {verdict} ({timestamp})` 헤더로 이어서 씀(`debug.md`의 기록 형식과 동일 원칙) |
| **RTL 경로**: 1차 판정(정적) | `verilog-rtl-reviewer` agent(Task로 위임, chip-design-skills 소유, read-only)가 fix-plan.md(선언된 범위)와 실제 diff(`fix_implement.files_changed`)를 함께 검토 — **STATIC-CONFIRMED 발견** → `verdict="issues_found"`, `fix_implement.revision_count` +1, `fix-implement`로 되돌아감(즉시 종료, 2차 판정으로 넘어가지 않음) / **없음** → 2차 판정으로 진행 |
| **RTL 경로**: 2차 판정(formal) | 1차 판정에서 reviewer가 SIM-RISK 중 **self-contained 로직/timing**으로 분류해 `verilog-rtl-prover`에 라우팅한 항목이 있으면, 그 자리에서 `verilog-rtl-prover`를 Task로 호출해 형식 증명 진행 — **반례 발견** → `verdict="issues_found"`(1차와 동일 취급), `fix_implement.revision_count` +1, `fix-implement`로 되돌아감(반례 corner를 findings에 포함해 재구현 시 참고하도록 함) / **증명 통과 또는 formal 대상 항목 자체가 없음** → `verdict="clean"`, phase가 `run`으로 전환, `/sim verify` 재진입 가능 |
| **RTL 경로**: 진짜 SIM-RISK 처리(CDC/protocol-relational) | self-contained가 아니라 실제 클록 타이밍·외부 자극 순서가 있어야 확인되는 항목은 formal로도 못 잡는다 — 이 게이트를 막지 않고 `fix-review.md`에 남겨 다음 `/sim run`의 dump 신호 선정 시 참고 자료로만 쓰인다(강제 아님) |
| **TB 경로**: 판정(정적) | `verilog-tb-reviewer` agent(Task로 위임, chip-design-skills 소유, read-only, 신규 — cross-repo 계약)가 fix-plan.md(근본원인 소재 판정 근거·영향 테스트/시퀀스/checker 목록)와 실제 diff를 함께 검토 — **spec 정당화 최종 판정**(anti-tautology의 권위 있는 확인, tb-coder의 1차 self-check을 독립적으로 재검증), checker 완화 탐지, fix-plan 범위 준수, 구조적 변경 의심(ARCH-SUSPECT-equivalent, 발견 시 사람에게 refer) — **문제 발견** → `verdict="issues_found"`, `fix_implement.revision_count` +1, `fix-implement`로 되돌아감 / **없음** → `verdict="clean"`, phase가 `run`으로 전환. **formal에 해당하는 2차 판정은 없음**(TB에는 자연스러운 formal 대응물이 없음, 정적 검토 1단으로 종결) |
| 무한 루프 방지 | 같은 fix에서 STATIC-CONFIRMED(또는 TB 경로의 issues_found)/formal 반례가 **2라운드 이상 연속** 발견되면 자동 재시도 대신 AskUserQuestion으로 "계속 자동 재시도할지 / 직접 개입할지" 확인(§11 리스크 참조) — "수정 요청" 개정 루프와 같은 "사람이 매 라운드 참여하는 유한 루프" 철학을 그대로 재사용 |
| **구현 주체=사람일 때 findings 전달("기록=출력" 동일성)** | `verdict="issues_found"`이고 `fix_implement.implementer=="human"`이면, Skill은 reviewer/prover(RTL 경로)/tb-reviewer(TB 경로)의 findings를 근거로 **사람에게 전달할 요약**(problem + 개선 제안)을 구성한다 — (1) 무엇이 발견됐는지(STATIC-CONFIRMED 목록 또는 formal 반례 corner, 또는 tb-reviewer의 issues_found 항목, agent 원문 근거를 왜곡하지 않고 요약), (2) agent가 findings에 함께 제시한 개선 방향이 있으면 그대로 포함(없으면 Skill이 임의로 지어내지 않고 "구체적 개선 방향 미제시"라고 명시). 이 요약은 **정확히 한 번만 작성**해 두 곳에 동일하게 반영한다: (a) 이 요약 텍스트 자체를 `note` 인자로 `append_fix_review_note()`(§5.1)에 넘겨 `fix-review.md`에 append(단순 raw agent 원문이 아니라 이 사람 전달용 요약이 그 라운드의 `note`가 됨), (b) 그 직후 **같은 텍스트를 이 응답(채팅) 안에 그대로 출력**한다 — append 따로, 채팅 출력 따로 별개로 작성하지 않는다(작성 시점이 갈리면 "기록된 내용"과 "사람이 실제로 본 내용"이 어긋날 수 있기 때문). 즉 `fix-review.md`는 findings 원본 저장소이자 동시에 **사람에게 실제로 전달된 메시지의 감사 기록**이 된다. AI 경로(구현 주체가 `verilog-rtl-coder`/`verilog-tb-coder`)는 이 단계가 **불필요** — 재위임 Task 프롬프트 자체에 findings를 실어 보내면 되므로 별도 전달 채널이 필요 없고, `append_fix_review_note()`의 `note`는 이 경우 agent 원문 그대로 둔다(사람 경로만 "전달용 요약"으로 대체) |

#### Run 복귀

`/sim verify {test}`가 Step 1(`/sim run`)부터 재진입 — fix-plan/fix-design/fix-implement/fix-review 산출물은 `origin_chain`에 남아 이후에도 추적 가능(§5.6 Origin Linking).

**보류 시엔 재진입하지 않는다**: 승인 게이트에서 "보류"를 선택하면 `run`으로 복귀하지 않고 `phase: "fix-plan"`에 머문다 — §4.2 debug 0단계/run 0단계가 설명하는 두 재개 경로(이어서 결정 vs 새로 시작) 중 하나를 사용자가 나중에 고를 때까지.

**fix-review에서 문제가 남아있어도 재진입하지 않는다**: `run`으로 복귀하는 유일한 경로는 fix-review가 `clean` 판정(RTL: 1차 정적 + 2차 formal 모두 통과 또는 formal 대상 항목 없음, TB: 정적 통과)을 내리는 것뿐이다 — `fix-implement`로 되돌아간 상태에서 `/sim verify`를 호출하면 §4.2 `/sim verify` 3단계가 "재진입하지 않는 경우"로 처리한다.

#### sim-state.json 스키마 확장

아래는 §5.1의 JSON 예시와 반드시 같은 스키마다.

```json
"origin_chain": {
  "run": { "dump_path": "...", "log": "..." },
  "analyze": { "csv_path": "...", "anomaly_time_ns": 8318143 },
  "debug": { "path": ".ai/sim-state/TOP015/debug.md", "iteration_count": 2, "updated_at": "..." },
  "fix_plan": { "path": ".ai/sim-state/TOP015/fix-plan.md", "fix_target": "rtl", "status": "pending", "revision_count": 0, "approved_at": null },
  "fix_design": { "path": ".ai/sim-state/TOP015/fix-design.md", "ratified_at": "..." },
  "fix_implement": { "implementer": "verilog-rtl-coder", "files_changed": ["db/design/.../ext_i2cSlave.v"], "report": "...", "revision_count": 0 },
  "fix_review": { "path": ".ai/sim-state/TOP015/fix-review.md", "status": "clean", "iteration_count": 1, "updated_at": "..." }
}
```

`fix_plan.fix_target`은 `"rtl"`/`"tb"`(위 "Fix Target: RTL vs TB" 참조) — `debug` 5단계에서 `verilog-rtl-debugger`가 판정하고 fix-plan.md에 근거와 함께 기록, 승인 게이트에서 사람이 함께 검토한다. `fix_target=tb`면 `fix_design`(ARCH escalation)과 `fix_review`(정적/formal 게이트)는 이 테스트에서 애초에 쓰이지 않는다(항상 `null`/`pending`으로 남음 — RTL 전용 도구라 적용 대상이 아님). `fix_design`은 LOCAL/IFACE 판정이면 `null` — 스키마에 필드는 있지만 값이 없는 게 정상이다. `fix_plan.status`는 `"pending"`/`"approved"`/`"superseded"`(§5.1 참조). `fix_plan.revision_count`는 "수정 요청" 루프를 돈 횟수 — 상세 diff/변경 이력은 git-tracked인 fix-plan.md 자체의 git 이력이 정본이고, 이 필드는 `/sim status`에서 빠르게 훑어보기 위한 카운터일 뿐이다. `debug.iteration_count`는 `debug.md`에 append한 횟수 — 동일한 역할의 카운터다. `fix_implement.implementer`는 `"verilog-rtl-coder"`/`"verilog-tb-coder"`/`"human"`/`null`, `revision_count`는 fix-review에서 되돌아온 라운드 수(`fix_target=tb`면 fix-review가 formal 없이 정적 판정만 하므로 revision_count 의미는 동일하게 적용된다). `fix_review.status`는 `"pending"`/`"clean"`/`"issues_found"`(`fix_target=tb`면 이 phase는 formal 없이 정적 판정만으로 결정된다).

#### 이 구조로 얻는 것

1. 승인 대상이 구두 제안이 아니라 **structural delta가 적힌 구체적 문서**로 고정되어 검토 가능성이 높아짐
2. coder의 즉흥 코딩을 `/sim` 워크플로우 레벨에서 한 번 더 차단(coder 자체 규율의 이중 강제)
3. `.ai/sim-state/{test}/*.md`가 git 이력에 남아 `solution-capture` skill로 자산화하기 쉬움(비자명 버그 fix 기록)
4. `/sim status`가 fix-plan 문서 존재 여부만으로 "이 fix가 계획/설계/구현 중 어느 단계인지" 판단 가능 — cross-session 복구가 더 정밀해짐
5. ARCH 판정 시에만 자동으로 fix-design이 붙어 과설계를 피함(LOCAL/IFACE는 가볍게)
6. 승인 게이트가 승인/종료 2택이 아니라 **승인/수정 요청(개정 루프)/보류(재개 가능)** 3택이라, 계획이 완벽하지 않아도 대화를 이어가며 다듬을 수 있고 — 보류하더라도 작업이 사라지지 않고 다음 세션에서 정확히 그 지점부터 이어가거나 깨끗하게 새로 시작할 수 있음
7. fix-plan이 `superseded`되어 `/sim run`부터 다시 시작해도 `debug.md`(조사 근거)는 보존되므로, 추가 dump 신호로 얻은 새 증거가 기존 조사 위에 **누적**된다 — 매번 원점에서 재조사할 필요가 없음
8. 구현 주체가 AI/사람 어느 쪽이든 동일한 워크플로우로 흡수된다 — `verilog-rtl-coder`/`verilog-tb-coder` 자동화가 최종 목표라도, 지금 당장 또는 앞으로도 사람이 직접 고치는 경우를 위한 별도 프로세스를 만들 필요가 없음
9. 정적 리뷰(`verilog-rtl-reviewer`/`verilog-tb-reviewer`)와 formal 증명(`verilog-rtl-prover`, self-contained 항목 한정)이 시뮬레이션 재검증 이전에 필수로 끼어들어, 비싼 시뮬레이션 사이클을 태우기 전에 정적·formal로 잡을 수 있는 버그를 먼저 걸러낸다 — coder의 자체 A0 검사가 없는 "사람 구현" 경로에서 특히 유일한 안전망이 되고, coder 경로에서도 독립된 두 번째 검증(defense-in-depth)이 됨
10. fix-review가 문제를 발견했을 때, 사람 구현 경로도 AI 구현 경로와 동등하게 "findings가 실제로 구현 주체에게 도달"한다 — AI는 Task 재위임으로, 사람은 Skill의 채팅 출력으로. 채팅에만 남기고 끝내지 않고 동일한 요약을 `fix-review.md`에도 함께 기록하므로("기록=출력" 동일성), 사람이 다음 세션까지 파일을 열어보지 않아 재수정 사이클이 조용히 멈추는 것을 방지하는 동시에, 그 요약이 대화 로그에만 남고 git 이력에는 없어 나중에 "그때 정확히 뭐가 전달됐는지" 재확인 못 하는 gap도 함께 막음

---
## 6. Hook 자동화 (Layer 1)

> **참고(Phase D 착수 시 재검토) — Hook 언어**: 아래는 hook을 JS(`node`)로 제안하지만, 이 사용자의 다른 환경(chip-design-skills)에서 확립된 hook 컨벤션은 전부 Python이다. Phase D는 후행이므로 지금 강제 수정하지는 않으나, 실제 착수 시 언어 일관성을 재검토할 것.
>
> **참고(Phase D 착수 시 재검토) — Fix Sub-cycle(§5.8)은 현재 Hook 설계로 감지되지 않는다**: §6.2의 `PostToolUse` matcher는 MCP compound tool 3개(`run_and_check`/`analyze_waveform`/`regression_summary`) 호출만 잡는다. §5.8 Fix Sub-cycle의 `fix-plan`/`fix-design`/`fix-implement` 전환은 MCP tool 호출이 아니라 `Task` tool로 agent(`verilog-rtl-coder`/`verilog-rtl-architect-advisor` 등)를 위임하는 방식이라, 같은 matcher 패턴으로는 감지되지 않는다(`Task` tool 자체를 matcher에 걸면 이 skill과 무관한 다른 모든 Task 호출까지 잡혀 오탐이 심해진다 — subagent_type 필터링이 필요하나 현재 PostToolUse matcher 문법이 이를 지원하는지 별도 확인 필요). 같은 이유로 `append_debug_note()` 호출(Skill이 `verilog-rtl-debugger` 조사 후 직접 호출, compound tool 아님)도 감지되지 않는다. Phase D 착수 시 이 gap을 반드시 재검토할 것.

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
동작: 키워드 매칭(§4.4 trigger 목록과 동일 어휘 — "시뮬레이션", "FAIL", "regression" 등. 프로젝트별 테스트 ID 네이밍 패턴은 포함하지 않음, §4.4 참조)
출력 (additionalContext):
  "시뮬레이션 관련 요청 감지. /sim skill 사용 권장."
```

### 6.4 Hook 파일 구조

```
skill-src/xcelium-sim/                    (git 정본, cp -r로 ~/.claude/skills/에 배포)
├── SKILL.md
├── scripts/
│   └── sim_state.py              ← (§3.4/§4.3) sim-state.json CRUD + phase 전이, 클라이언트 로컬
├── hooks/
│   ├── sim-post-compound.js     ← PostToolUse (compound tool 실행 후) — §6 참고 각주: 언어 일관성 재검토 대상
│   └── sim-prompt-detect.js     ← UserPromptSubmit (키워드 감지)
└── references/
    ├── phase-0-discovery.md         ← (§5.2) §0A/0B에 TB frontmatter 스키마 참조 지시 추가된 버전
    ├── phase-1-analysis.md
    ├── phase-2-simulation.md
    ├── phase-3-triage.md
    ├── phase-4-waveform.md
    ├── phase-5-fix-regression.md
    ├── tool-map.md
    ├── server-ops.md                ← Phase 1 산출물, Phase 2와 무관
    ├── backend-interface.md         ← Phase 2가 추가하는 신규 reference 1/2(§4.3 참조)
    └── fix-plan-template.md         ← (§5.8) Phase 2가 추가하는 신규 reference 2/2
```

---

## 7. CLI Commands (Layer 2)

### 7.1 설계 원칙

- Backend별 CLI — `xcelium-mcp-cli run`, `vcs-mcp-cli run` (향후)
- 공유 CompoundResult 출력 형식 — 어떤 backend든 동일한 `[TAG]` 형식
- `compound.py`에 로직 1번 작성, CLI와 MCP tool이 공유
- **독립 entry point — `server.py`의 sys.argv 분기가 아니다**: 이 저장소의 `pyproject.toml [project.scripts]`에는 `xcelium-mcp`(server:main) 외에 `xcelium-mcp-supervisor`(supervisor:main)/`xcelium-mcp-culler`(idle_culler:main)가 **각자 독립 console_script**로 등록돼 있고, `stdio_forward.py`/`sim_session_reaper.py`도 각자 독립 `-m` 모듈이다. supervisor 배포 이후 MCP 연결은 `WorkerHandler.handle()`이 `_xcelium_server.main()`을 **fork 후 직접 함수 호출**하는 방식이라(subprocess 재실행이 아님), 연결별로 다른 sys.argv가 애초에 전달될 방법이 없다. 따라서 CLI는 `server.py`를 전혀 건드리지 않고 `xcelium-mcp-cli = "xcelium_mcp.cli:main"`을 새 console_script로 추가하는 것으로 스코프를 정한다 — 이 저장소 자신이 이미 증명한 패턴("새 관심사 = 새 모듈 + 새 console_script")을 그대로 따르는 것이며, 부수적으로 "sys.argv 분기로 MCP 깨짐"이라는 리스크 자체가 존재하지 않는다(server.py 무변경이므로).

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
├── cli.py               ← argparse → compound.py 호출. pyproject.toml에 xcelium-mcp-cli로 등록되는 독립 entry point
└── tools/compound.py    ← MCP tool 3개 → compound.py 호출(server.py가 register()만 추가)
```

`server.py`는 이 다이어그램에 없다 — compound tool 3개를 `register()`하는 것 외에 CLI 관련 변경이 전혀 없기 때문이다(§7.1). `sim_state.py`도 이 다이어그램에 없다(§3.4 참조) — `xcelium-mcp-cli`는 `xcelium-mcp`와 마찬가지로 원격 시뮬레이션 서버에서 실행되므로(§8.2 E-2 "원격 host"), CLI로 직접 실행하는 경로는 클라이언트 로컬 `sim-state.json`을 애초에 다룰 수 없다 — CLI 사용은 `/sim` Skill을 우회하는 "AI 없이 직접 실행" 경로이므로 cross-session 상태 추적 대상이 아닌 게 자연스럽다.

---

## 8. 구현 계획

### 8.1 파일 변경 목록

| 파일 | 변경 | Layer |
|------|------|:-----:|
| `xcelium-mcp/src/xcelium_mcp/compound.py` | **신규**: CompoundResult + 3 compound 함수 | L3 |
| `xcelium-mcp/src/xcelium_mcp/cli.py` | **신규**: argparse CLI, `xcelium-mcp-cli` 독립 entry point(§7.1) | L2 |
| `xcelium-mcp/src/xcelium_mcp/tools/compound.py` | **신규**: MCP tool 3개 | L3 |
| `xcelium-mcp/pyproject.toml` | **수정**: `[project.scripts]`에 `xcelium-mcp-cli = "xcelium_mcp.cli:main"` 추가(기존 `xcelium-mcp`/`xcelium-mcp-supervisor`/`xcelium-mcp-culler`와 동일 패턴) | L2 |
| `xcelium-mcp/src/xcelium_mcp/server.py` | **수정**: compound tool 3개 `register()` 추가만 — CLI 관련 변경 없음 | L3 |
| `~/.claude/skills/xcelium-sim/SKILL.md` | **수정**(Phase 1에서 이미 생성됨, 신규 아님): 기존 `<!-- PHASE 2 확장점 -->` 마커 아래에 subcommand 라우팅만 추가 | L4 |
| `~/.claude/skills/xcelium-sim/scripts/sim_state.py` | **신규**: sim-state.json CRUD + phase 전이. 원격 서버 패키지가 아닌 클라이언트 로컬(§3.4/§5.1 참조)이 위치 근거 | L4 |
| `~/.claude/skills/xcelium-sim/references/backend-interface.md` | **신규**(나머지 phase-0~5/tool-map.md/server-ops.md 7개는 Phase 1에서 이미 완료, §4.3 참조) | L4 |
| `~/.claude/skills/xcelium-sim/references/fix-plan-template.md` | **신규**(§5.8 Fix Sub-cycle 도입): `fix-plan.md` 필수 항목(근본원인/영향 파일/structural delta 선언/검증 대상) 정의 | L4 |
| `~/.claude/skills/xcelium-sim/references/phase-0-discovery.md` | **수정**(§5.2 결정 반영): §0A/0B에 TB frontmatter YAML 스키마 참조 지시 추가 — `verilog-tb-analyst`가 분석서 작성 시점에 frontmatter를 함께 생성하도록. 이 8개 파일 중 Phase 2가 내용을 수정하는 유일한 기존 파일(§4.3 참조) | L4 |
| `~/.claude/skills/xcelium-sim/hooks/*.js` | **신규**: 2개 hook (PostToolUse, UserPromptSubmit) | L1 |
| `{project}/.ai/sim-state/{test}/fix-plan.md` | **신규**(§5.8): `fix-plan-template.md` 형식을 따르는 실제 fix 문서, git-tracked | — |
| `{project}/.ai/sim-state/{test}/fix-design.md` | **신규**(§5.8 "2) fix-design", ARCH 판정 시에만 생성): `verilog-rtl-architect-advisor`가 산출하는 ADR — **별도 template reference 불필요**, coder 자신의 기존 `adr-template.md`를 그대로 재사용(`fix-plan-template.md`를 따르는 것이 아님 — `fix-review.md`가 reviewer/prover 자체 리포트 형식을 재사용하는 것과 같은 이유), git-tracked | — |
| `{project}/.ai/sim-state/{test}/debug.md` | **신규**(§5.1/§5.8 "0) debug 조사 노트"): debug phase 조사 내용 append-only 누적 문서, git-tracked | — |
| `{project}/.ai/sim-state/{test}/fix-review.md` | **신규**(§5.1/§5.8 "4) fix-review"): `verilog-rtl-reviewer` 정적 리뷰 + `verilog-rtl-prover` formal 증명(self-contained 항목만, RTL 경로) 또는 `verilog-tb-reviewer` 정적 리뷰(TB 경로) 결과를 append-only 누적하는 문서, git-tracked. 별도 template reference 불필요 — 각 agent 자신의 기존 리포트 형식을 그대로 재사용 | — |
| `{project}/.ai/analysis/tb_TOP012~016.analysis.md` | **수정**: 새 frontmatter 컨벤션 적용 전 작성된 기존 5개 문서에 대한 **1회성 backfill만** — 이후 신규·갱신 문서는 `verilog-tb-analyst`가 작성 시점에 자동 포함(§5.2)하므로 별도 변경 대상 아님 | — |
| `venezia-fpga/CLAUDE.md` | **수정**: `/sim` skill 안내로 간소화 | — |

### 8.2 구현 순서

```
Phase A: Backend 공유 로직 (xcelium-mcp)
  A-1. CompoundResult dataclass
  A-2. run_and_check()      batch_run → log_grep → csv_extract → bisect, CompoundResult 반환까지만(sim-state.json은 여기서 갱신하지 않음, §5.1/§8.1 참조)
  A-3. analyze_waveform()   csv_extract → multi-condition bisect, 동일하게 CompoundResult 반환까지만
  A-4. regression_summary() batch_regression → per-test log → csv on fail, 동일

Phase B: CLI + MCP Compound Tools (xcelium-mcp)
  B-1. cli.py argparse (run/analyze/regression)
  B-2. pyproject.toml에 xcelium-mcp-cli console_script 등록(§7.1)
  B-3. tools/compound.py register() — 3 MCP tools
  B-4. server.py에 compound tool register() 호출 추가

Phase C: /sim Skill (run-guide/analyze-guide/debug-guide는 이미 phase-0~5.md로 완료돼 있어 별도 작성 불요, §4.3 참조)
  C-1. SKILL.md — 기존 `<!-- PHASE 2 확장점 -->` 마커 아래에 subcommand + next-skill-map + 트리거 추가
  C-2. scripts/sim_state.py — sim-state.json CRUD + phase 전이. Backend(Phase A)가 아니라 여기서 구현하는 이유는 §3.4/§8.1 참조 — 클라이언트 로컬 파일이라 원격 서버 패키지에 둘 수 없음
  C-3. references/backend-interface.md — Phase 2가 추가하는 신규 reference 문서 1/2(C-2는 코드라 별개, §4.3 참조)
  C-4. references/phase-0-discovery.md §0A/0B 수정 — TB frontmatter YAML 스키마(§5.2) 참조 지시 추가, `verilog-tb-analyst`가 분석서 작성 시점에 함께 생성하도록. chip-design-skills repo 소유의 agent 문서 자체 반영은 이 plan 범위 밖(§4.3 참조)
  C-5. 기존 TB 분석서(tb_TOP012~016) 1회성 backfill — C-4 스키마 형식으로 frontmatter 추가 (§5.2 형식 — last_verified 아닌 tb_source.combined_sha256). C-4 이후 작성되는 신규 문서는 이 단계 불필요
  C-6. references/fix-plan-template.md — Phase 2가 추가하는 신규 reference 문서 2/2(§5.8 Fix Sub-cycle). `/sim debug`가 이 템플릿으로 fix-plan.md를 작성하도록 SKILL.md 라우팅(C-1)에서 참조

Phase D: Hook 자동화
  D-1. hooks/sim-post-compound.js — phase 전환 제안
  D-2. hooks/sim-prompt-detect.js — 키워드 자동 트리거

Phase E: CLAUDE.md + 검증
  E-1. CLAUDE.md 시뮬레이션 섹션 간소화
  E-2. CLI: xcelium-mcp-cli run TOP015 (원격 host)
  E-3. MCP: run_and_check 호출 → Skill이 반환값으로 로컬 sim-state.json 갱신하는지 확인(갱신 주체는 Skill, §5.1 참조)
  E-4. Skill: /sim run → analyze → debug → verify e2e
  E-5. Skill: /sim verify --regression + parallel FAIL
  E-6. Hook: PostToolUse phase 전환 + UserPromptSubmit 트리거 확인
  E-7. 기존 25 tool 하위호환 확인
  E-8. Fix Sub-cycle e2e: LOCAL 판정 케이스(fix-plan→fix-implement 직행)와 ARCH 판정 케이스(fix-plan→fix-design/ADR→fix-implement) 둘 다 승인/거부 분기 포함 수동 검증(§5.8)
  E-9. `debug.md` 누적 e2e: `/sim debug {test}` 연속 2회 호출로 append+iteration_count 증가 확인, `/sim run {test}`로 fix-plan supersede 후 `/sim analyze`→`/sim debug {test}` 재진입 시 0-b단계가 기존 debug.md를 반영하는지 수동 검증(§4.2 debug 0-b단계·§5.8)
  E-10. `debug.md` "수정 요청 재조사" append e2e: fix-plan 승인 게이트에서 재조사 필요 피드백 → `verilog-rtl-debugger` 재위임 → `debug.md`에 `context="fix-plan 수정 요청 재조사(revision N)"` 헤더로 append되는지, 가벼운 수정 피드백일 때는 append가 일어나지 않는지 두 갈래 모두 수동 검증(§4.2 debug 6단계·§5.8)
  E-11. 구현 주체 2-way e2e: (a) AI(coder) 위임 경로가 Task 반환 즉시 같은 세션에서 fix-review로 이어지는지, (b) 사람 직접 수정 경로에서 세션 종료 후 실제로 파일을 고치고 `/sim run {test}`를 재호출했을 때 run 0-b단계가 git diff를 감지해 fix-review로 진입하는지, diff가 없을 때는 대기 안내만 하고 진행하지 않는지 수동 검증(§4.2 debug 6단계·run 0-b단계, §5.8 "3) fix-implement")
  E-12. fix-review 게이트 e2e: (a) 의도적으로 STATIC-CONFIRMED급 문제를 심어 `verilog-rtl-reviewer`가 잡아내고 fix-implement로 되돌리는지, (b) 정상 구현이 clean 판정을 받아 phase가 `run`으로 전환되는지, (c) 같은 fix에서 2라운드 연속 발견될 때 AskUserQuestion으로 계속 여부를 확인하는지, (d) 의도적으로 self-contained formal 반례 케이스(예: FSM corner deadlock)를 심어 2차 판정에서 `verilog-rtl-prover`가 호출되고 반례를 잡아 fix-implement로 되돌리는지, 증명이 통과하는 정상 케이스는 clean으로 진행하는지 수동 검증(§4.2 debug 6단계, §5.8 "4) fix-review")
  E-13. `fix_target` 분기 e2e: 의도적으로 TB(테스트케이스 기대값)가 spec과 다르게 잘못된 케이스를 만들어 (a) `verilog-rtl-debugger`가 `fix_target=tb`로 판정하고 fix-plan.md에 spec 근거를 남기는지, (b) 승인 후 `verilog-tb-coder`(또는 사람)만 호출되고 `verilog-rtl-coder`/fix-design은 전혀 개입하지 않는지, (c) tb-coder 완료 직후 `verilog-tb-reviewer`가 fix-review 게이트에서 호출되는지, (d) 의도적으로 순환 논리 근거("RTL에 맞춘다")를 담은 fix-plan.md로 tb-reviewer가 실제로 issues_found를 내고 fix-implement로 되돌리는지, 정상 spec 근거는 clean으로 진행하는지 — RTL 대상 케이스(E-4~E-12)와 완전히 분리된 경로로 동작하는지 수동 검증(§4.2 debug 5~6단계, §5.8 "Fix Target: RTL vs TB", "4) fix-review")
  E-14. RTL+TB 동시 수정 안내 확인: 근본원인이 RTL/TB 양쪽에 걸치는 시나리오에서 fix-plan이 `fix_target=both` 같은 단일 혼합 값 대신 **두 개의 독립 fix-plan(rtl 하나, tb 하나)**으로 분리 안내되는지, 각 fix-plan.md에 서로의 경로가 프로즈로 상호 참조되는지 수동 검증(§5.8 "Fix Target: RTL vs TB" "RTL+TB 동시 수정" 참조)
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
2. `/sim` Skill — 변경 없음 (backend interface만 사용)

`sim_state.py`는 새 backend가 신경 쓸 대상이 아니다 — 클라이언트 로컬(`skill-src/xcelium-sim/scripts/`, §3.4)에 backend와 무관하게 항상 고정 위치하므로, 새 backend가 재사용하거나 준비할 필요 자체가 없다.

### 10.2 추가 Skill·Agent 연계

| 기존 Skill/Agent | 연계 방식 |
|-----------|----------|
| `verilog-rtl` (skill) | `/sim`이 직접 참조하지 않음 — `verilog-rtl-coder` agent가 구현 시 내부적으로 로드(coder의 Mandatory Setup, chip-design-skills 소유) |
| `verilog-rtl-coder` (agent) | `/sim debug`가 작성한 **fix-plan 문서**가 **사용자 승인**을 받으면, `fix_target=rtl`이고 **구현 주체로 AI를 선택한 경우에 한해**(사람 직접 수정 선택 시엔 호출되지 않음, `fix_target=tb`면 애초에 호출 대상이 아님) Task로 위임해 구현(§4.2/§4.5/§5.8 Fix Sub-cycle) — ARCH 판정 시 architect-advisor 경유 |
| `verilog-tb-coder` (agent — chip-design-skills 소유, cross-repo 계약) | `fix_target=tb`이고 구현 주체로 AI를 선택한 경우에 한해 Task로 위임해 TB/testcase 구현(§4.2/§4.5/§5.8 "Fix Target: RTL vs TB", "3) fix-implement" 참조) — spec 대비 정당화(anti-tautology) 1차 필터를 자체적으로 수행하지만, 이건 빠른 필터일 뿐 권위 있는 판단이 아니다(최종 판단은 아래 `verilog-tb-reviewer`가 독립적으로 내림) |
| `verilog-tb-reviewer` (agent — chip-design-skills 소유, cross-repo 계약) | `fix_target=tb`의 fix-implement 완료 직후 **구현 주체와 무관하게 항상** Task로 호출(§5.8 "4) fix-review" TB 경로, 필수 게이트 — 조건부 아님). spec 정당화(anti-tautology)의 **최종·독립 판단**, checker 완화 탐지, fix-plan 범위 준수, 구조적 변경 의심 여부를 검토 — 문제 발견 시 fix-implement로 되돌림. RTL의 `verilog-rtl-reviewer`와 대칭이나 taxonomy(T1-T9)/formal 단계는 이식하지 않음(agent 정의 자체는 이 plan 범위 밖) |
| `verilog-rtl-architect-advisor` (agent) | Fix Sub-cycle의 `fix-design` phase에서만 호출(§5.8, `fix_target=rtl` 전용) — coder의 A0가 ARCH로 판정한 경우에 한해 escalate, ADR 산출. 사람 직접 수정 경로엔 이 자동 escalate가 없으므로, 필요하면 사용자가 직접 호출해야 함(§5.8 "3) fix-implement" 참조) |
| `verilog-rtl-reviewer` (agent) | `fix-implement` 완료 직후 `fix_target=rtl`이면 **구현 주체와 무관하게 항상** Task로 호출(§5.8 "4) fix-review", 필수 게이트 — 조건부 아님. `fix_target=tb`면 이 phase 자체가 없어 호출되지 않음). STATIC-CONFIRMED 문제 발견 시 fix-implement로 되돌림 |
| `verilog-rtl-prover` (agent) | fix-review 게이트 2차 판정에서만 호출(§5.8 "4) fix-review", `fix_target=rtl` 전용) — reviewer가 self-contained 로직/timing으로 분류한 항목에 한해, 같은 게이트 안에서 형식 증명. 반례 발견 시 STATIC-CONFIRMED와 동일하게 fix-implement로 되돌림 — CDC/protocol-relational처럼 self-contained가 아닌 진짜 SIM-RISK는 이 agent 대상이 아니다 |
| `chip-verification` (skill) | UVM 환경 구축 시 `/sim`과 연계 |
| `uvm-verification` (skill) | UVM sequence/agent 작성 후 `/sim run`으로 검증 |
| `lattice-fpga` (skill) | FPGA 합성 후 gate-level `/sim run --mode gate` |

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
- [ ] 로컬 `{project}/.ai/sim-state.json`(신규 독립 파일, §5.1 참조 — `registry.py` 비확장으로 확정)이 compound 실행마다 자동 갱신되고, `/sim status`로 cross-session 복구 가능 (§4.1, §5.1)
- [ ] TB frontmatter 없는 기존 `.ai/analysis/tb_*.analysis.md`도 fallback으로 정상 동작 (§5.2)
- [ ] `/sim debug`가 `fix-plan` 문서를 먼저 작성하고, **사용자 승인 없이는** `verilog-rtl-coder` agent를 호출하지 않음을 확인 — 승인/architect-advisor escalate(fix-design) 경로 수동 검증. 승인 후에도 **구현 주체로 "사람"을 선택하면 coder가 전혀 호출되지 않는지**도 함께 확인 (§4.2/§4.5/§5.8)
- [ ] ARCH 판정 테스트 케이스로 `fix-design`(ADR) 경로가, LOCAL/IFACE 판정 케이스로 `fix-design` 스킵 경로가 각각 정상 동작하는지 확인 (§5.8)
- [ ] 승인 게이트 3-way 전체 검증: **수정 요청**이 fix-plan.md를 개정하고 phase를 `fix-plan`에 유지한 채 게이트를 재표시하는지, **보류** 후 세션을 새로 시작해 `/sim debug {test}`가 재조사 없이 기존 fix-plan.md로 바로 게이트를 재표시하는지, `/sim run {test}`가 기존 fix-plan.md를 `superseded`로 표시하고 완전히 새로 시작하는지 (§4.2 debug 0단계/run 0단계, §5.8)
- [ ] `debug.md` 누적 검증: (1) `/sim debug {test}`를 두 번 이상 연속 실행 시 `debug.md`가 append되고 `iteration_count`가 증가하는지(덮어쓰지 않는지), (2) fix-plan `superseded` 후 `/sim run {test}` → `/sim analyze` → `/sim debug {test}` 재진입 시 0-b단계가 기존 `debug.md`를 먼저 Read하고 이전 iteration 내용을 반영해 조사를 이어가는지 (§4.2 debug 0-b단계, §5.1, §5.8 "0) debug — 조사 노트")
- [ ] `debug.md` "수정 요청 재조사" append 검증: fix-plan 승인 게이트에서 **수정 요청 → 근본원인 재조사 필요** 피드백을 줬을 때 (1) `verilog-rtl-debugger` 재위임 후 `debug.md`에 `context="fix-plan 수정 요청 재조사(revision N)"` 헤더로 새 iteration이 append되는지, (2) 반대로 **가벼운 수정**(재조사 없음) 피드백일 때는 `debug.md`에 아무것도 append되지 않고 fix-plan.md만 바뀌는지(두 갈래가 실제로 분기되는지) (§4.2 debug 6단계, §5.1 `append_debug_note`, §5.8 "0) debug — 조사 노트"·3-way "수정 요청" 행)
- [ ] 구현 주체 2-way 검증: 승인 후 "구현 주체 선택"이 실제로 AI/사람 두 옵션을 제시하는지, AI 선택 시 coder Task 완료 즉시 같은 세션에서 fix-review로 이어지는지, 사람 선택 시 phase가 `fix-implement`에 머무르고 세션이 종료되는지, 이후 `/sim run {test}` 재호출 시 run 0-b단계가 git diff 유무로 완료를 정확히 판정하는지(diff 있음 → fix-review 진입, 없음 → 대기 안내) (§4.2 debug 6단계·run 0-b단계, §5.8 "3) fix-implement")
- [ ] fix-review 필수 게이트 검증: fix-implement 완료 후(구현 주체 무관) `verilog-rtl-reviewer`가 자동으로 호출되는지(스킵 불가), STATIC-CONFIRMED 발견 시 `fix_implement.revision_count`가 증가하고 phase가 `fix-implement`로 되돌아가는지, **1차 정적 판정이 clean일 때만 2차로 `verilog-rtl-prover`가 self-contained 항목에 대해 호출되는지, formal 반례도 STATIC-CONFIRMED와 동일하게 fix-implement로 되돌리는지**, 정적+formal 모두 clean이어야만 phase가 `run`으로 전환돼 `/sim verify`가 시뮬레이션을 재실행하는지, `fix-review.md`가 라운드마다 append되는지 (§4.2 debug 6단계, §5.1 `append_fix_review_note`, §5.8 "4) fix-review")
- [ ] `fix_target` 분류 및 TB 경로 검증: (1) `verilog-rtl-debugger`가 debug 5단계에서 `fix_target`을 판정하고 fix-plan.md에 spec 근거를 남기는지, (2) `fix_target=tb`로 판정된 케이스에서 fix-design은 스킵되지만 **fix-review는 스킵되지 않고** fix-implement 완료 직후 `verilog-tb-reviewer`가 호출되는지, (3) 구현 주체 선택에서 `fix_target=tb`면 `verilog-tb-coder`(또는 사람)가, `fix_target=rtl`이면 `verilog-rtl-coder`(또는 사람)가 호출되는지 — 서로 뒤섞이지 않는지, (4) fix-plan.md의 "근본원인 소재 판정 근거"가 "RTL에 맞춘다"는 이유로 작성되면 승인 게이트뿐 아니라 `verilog-tb-reviewer`의 fix-review 게이트에서도 반려되는지(2중 방어 확인) (§4.2 debug 5~6단계, §5.8 "Fix Target: RTL vs TB", "3) fix-implement", "4) fix-review")
- [ ] RTL+TB 동시 수정 시 두 개의 독립 fix-plan으로 분리 안내되는지, `fix_target=both` 같은 혼합 값이 스키마에 존재하지 않는지 확인 (§5.8 "Fix Target: RTL vs TB" "RTL+TB 동시 수정")

### Quality Criteria

- [ ] `pytest tests/` 전체 스위트 회귀 없음 (도입 전 617 passed 기준선 유지)
- [ ] `ruff check src/` clean
- [ ] Phase D(Hook) 도입 전, Phase A-C만으로 e2e 수동 검증 통과 (§8.2 E-2~E-5)

---

## Impact Analysis

CLI는 `server.py`(기존 MCP 진입점)에 sys.argv 분기를 추가하는 방식이 아니라 `xcelium-mcp-cli`라는 독립 console_script다(§7.1) — `server.py`는 compound tool 3개 `register()` 추가만 받는다. 원격 배포 모델은 supervisor+fork 구조(`deploy/README.md`)를 따른다.

### Changed Resources

| Resource | Type | Change Description |
|----------|------|---------------------|
| `src/xcelium_mcp/cli.py` | Python module (신규) | argparse 기반 CLI, `xcelium-mcp-cli` 독립 entry point로 등록 — `server.py`와 무관 |
| `pyproject.toml` | `[project.scripts]` | `xcelium-mcp-cli = "xcelium_mcp.cli:main"` 신규 등록 (기존 `xcelium-mcp`/`xcelium-mcp-supervisor`/`xcelium-mcp-culler`와 동일 패턴) |
| `src/xcelium_mcp/server.py` | MCP entry point | compound tool 3개 `register()` 호출 추가만 — entry point 자체(`main()`)는 무변경 |
| 기존 25 tool | MCP tool | 변경 없음 — compound tool 3개만 신규 추가 |
| `src/xcelium_mcp/` 패키지 | Python module | `compound.py`, `cli.py` 신규 파일 추가(`sim_state.py`는 이 패키지가 아니라 `skill-src/xcelium-sim/scripts/`에 신규 추가, §3.4/§8.1 참조) |

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
| ~~sys.argv 분기로 MCP 깨짐~~ | ~~기존 MCP 불가~~ | **해당 없음**: CLI를 `xcelium-mcp-cli` 독립 console_script로 설계(§7.1)해 `server.py`를 아예 건드리지 않으므로 이 리스크 자체가 해당 없음 |
| sim-state.json 동시 접근 | 상태 충돌 | 단일 사용자, lock 불필요 |
| TB frontmatter 형식 불일치 | Skill 파싱 실패 | 없으면 AI 본문 읽기 fallback |
| 병렬 FAIL analysis 과부하 | Agent 과다 | 최대 3개 병렬, 나머지 순차 |
| Hook JS 유지보수 부담 | 추가 언어 | Hook 2개뿐, 로직 최소화 |
| Backend 추상화 과설계 | 현재 xcelium만 | interface만 정의, 구현은 xcelium만 |
| origin chain stale | 오래된 참조 | RTL 수정 후 verify 시 chain 초기화 |
| Fix Sub-cycle 오버헤드 — 간단한 버그도 문서 작성이 강제돼 느려짐 | 사소한 fix에 과도한 무게 | fix-design(ARCH)은 조건부로만 트리거, LOCAL/IFACE는 fix-plan 1개 문서로 끝(§5.8) — fix-plan 자체도 coder가 어차피 A0/A1-A7로 산출하는 정보를 문서화하는 것뿐이라 순수 추가 작업 아님 |
| fix-plan과 실제 구현이 괴리(coder가 문서 대충 따름) | 승인 게이트 무력화 | coder에 "문서 범위 밖 변경은 멈추고 보고" 명시 지시(§5.8) — coder 자신의 기존 STOP-and-Ask 원칙 재사용, 자체 DoD의 A0 post-implementation guardrail이 계획 없던 드리프트를 별도로 잡음 |
| 수정 요청 개정 루프가 수렴하지 않음(끝없이 다듬기만 함) | 진행 지연 | 사람이 매 라운드 직접 참여하는 대화형 루프라 자연히 사람이 멈추거나 보류 선택 — 자동 max-iteration 카운터 불필요(자동 루프가 아니므로) |
| `superseded` fix-plan.md가 `.ai/sim-state/{test}/`에 누적돼 어수선해짐 | 저장소 정리 부담 | git-tracked라 삭제해도 이력 보존 — `solution-capture`/정기 정리 시 `superseded` 상태 문서를 아카이브하는 건 이 plan 범위 밖의 운영 정책으로 남김 |
| `debug.md`가 iteration을 거듭할수록 무한정 길어짐 | 다음 debug 재진입 시 전체를 다시 읽는 비용 증가 | 이 plan 범위에서는 대응하지 않음(YAGNI) — 실제로 문제가 확인되면 오래된 iteration을 요약하는 후속 개선으로 미룸, 지금 선제 대응하지 않는다 |
| fix-review 루프가 수렴하지 않음(coder/사람이 같은 종류의 STATIC-CONFIRMED 문제를 반복 생산) | 시뮬레이션 진입 지연 | 2라운드 연속 발견 시 자동 재시도 대신 AskUserQuestion으로 계속 여부 확인(§5.8 "4) fix-review" "무한 루프 방지" 참조) — "수정 요청" 개정 루프와 동일한 "사람이 매 라운드 참여" 철학 재사용 |
| 사람 구현 완료 감지 실패 — fix-plan.md가 선언한 파일 목록 밖의 파일을 고쳤거나, git diff가 감지 불가능한 방식(예: 다른 브랜치·워크트리)으로 작업한 경우 | run 0-b단계가 구현을 영원히 "미완료"로 판단, 진행 정체 | 이 plan 범위에서는 "fix-plan.md 선언 범위 = 감지 대상"으로 단순화하고 별도 대응 없음(YAGNI) — 실제로 문제가 확인되면 사용자가 `/sim status`로 현재 phase를 확인하고 fix-plan.md 자체를 개정(수정 요청 루프 재사용)하는 것으로 우회 가능 |
| `verilog-rtl-reviewer`가 STATIC-CONFIRMED와 SIM-RISK 경계를 잘못 판정 — 실제로는 정적으로 잡혔어야 할 문제를 SIM-RISK로 넘겨 clean 판정 | 버그가 시뮬레이션 단계까지 새어나감 | 이 plan은 reviewer 자신의 기존 STATIC-CONFIRMED/SIM-RISK 분류 정책(chip-design-skills 소유)을 그대로 신뢰하고 재정의하지 않는다 — reviewer의 분류 정확도 자체는 이 plan의 범위 밖이고, `/sim verify`(시뮬레이션)가 그 뒤의 두 번째 방어선으로 여전히 남아있다(defense-in-depth) |
| `verilog-rtl-prover`의 형식 증명이 시간 초과(timeout)되거나 property 작성 자체가 어려운 케이스 | fix-review 게이트가 오래 걸리거나 결론을 못 냄 | 이 plan은 prover 자신의 기존 시간 제한·property 작성 정책(chip-design-skills 소유)을 재정의하지 않는다 — timeout 시 어떻게 처리할지는 prover 자신의 기존 동작을 따르고 이 plan이 새 규칙을 추가하지 않음(YAGNI) |
| fix-review가 STATIC-CONFIRMED와 formal 반례 2가지 종류의 실패를 모두 "issues_found"로 뭉뚱그려 `/sim status`에서 구분이 안 됨 | 재구현 시 어떤 종류의 문제였는지 빠르게 파악하기 어려움 | `fix-review.md`(append-only, §5.8 "4) fix-review")의 각 라운드 본문에 어느 판정 단계(1차 정적 vs 2차 formal)에서 나온 findings인지 적히므로, 상세 구분은 그 문서를 열어보면 확인 가능 — `fix_review.status`를 3종 이상으로 세분화하는 건 지금은 하지 않음(YAGNI, `/sim status` 요약용 필드는 가볍게 유지) |
| **`fix_target` 오판정 — 실제로는 RTL 버그인데 TB를 고쳐 checker를 완화(anti-tautology 위반)**: "TB가 틀렸다"는 판정이 실제로는 "현재 RTL 동작에 맞추기 위해" 내려지면, 진짜 RTL 버그를 숨기고 회귀가 조용히 통과하게 만든다 — RTL fix를 잘못 적용하는 것보다 훨씬 발견하기 어렵다(다음 실제 버그 재발 전까지 드러나지 않을 수 있음) | 중간(`verilog-tb-reviewer` 도입으로 완화됨) | 3중 방어: (1) fix-plan.md의 "근본원인 소재 판정 근거"를 spec 대비로 강제, (2) 승인 게이트에서 사람이 그 근거를 검토, (3) fix-implement 완료 후 `verilog-tb-reviewer`가 독립적으로 spec 정당화를 재확인하는 fix-review 게이트(§5.8 "4) fix-review" TB 경로) — RTL 쪽 STATIC-CONFIRMED/formal 게이트와 동급의 방어선이 TB 쪽에도 있음 |
| `verilog-tb-coder`의 1차 self-check과 `verilog-tb-reviewer`의 독립 검토가 중복되어 같은 fix에 두 번 검토 비용이 듦 | 사소한 TB fix에도 무게가 실림(위 "Fix Sub-cycle 오버헤드" 리스크와 유사 계열) | 의도된 설계다 — self-check은 "빠른 1차 필터"(명백한 순환 논리를 조기에 걸러 재작업 비용을 줄임)이고 reviewer는 "권위 있는 최종 판단"이라 역할이 다르다(RTL의 coder self-check + reviewer/prover 독립 검증과 동일 2단 구조, anti-tautology 참조) — 중복이 아니라 defense-in-depth |

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| 0.1 | 2026-04-03 | 초안: 3-Layer, `/debug` 단일 skill |
| 0.2 | 2026-04-03 | `/sim` 리네이밍, 3가지 목적 + verify, subcommand 구조 |
| 0.3 | 2026-04-03 | 워크플로우 패턴 7개 추가(bkit/compound-engineering 분석) |
| 1.0 | 2026-04-03 | 전면 재구성: 5-Layer 아키텍처, Backend Interface, Hook, 향후 확장 |
| 1.1 | 2026-04-03 | SessionStart hook 제거 → `/sim status` on-demand로 대체 |
| 1.2 | 2026-04-03 | context:fork 불채택 확정, `/sim status` 단일 패턴으로 cross-session 복구 통일 |
| 1.3 | 2026-07-02 | venezia-fpga → xcelium-mcp repo로 이관 |
| 1.4 | 2026-07-02 | bkit plan 템플릿 준수 리뷰 — tool 개수 정정, Context Anchor/Requirements/Success Criteria/Impact Analysis 섹션 신규 |
| 1.5 | 2026-07-02 | FR-09(tool-usage-guide) 추가 + Dependencies 섹션 신설 |
| 1.6 | 2026-07-02 | tool-usage-guide skill과 통합 결정(동일 디렉터리의 Phase 1/2) |
| 1.7 | 2026-07-03 | §4.5 debug(RTL) 매핑을 `verilog-rtl-debugger` agent 호출로 구체화 |
| 1.8 | 2026-07-03 | Design 문서는 tool-usage-guide와 별도 유지 결정(matchRate 왜곡 방지) |
| 1.9 | 2026-07-03 | 소스 재검증 패스(§3.4 재사용 대상 함수 존재 확인) — 변경 없음 |
| 1.10 | 2026-07-08 | Phase 1 완료 반영 — tool 수(24→25)·pytest 기준선(472→617) 갱신, §4.3을 실제 산출물(phase-0~5+tool-map+server-ops) 기준으로 전면 재작성 |
| 1.11 | 2026-07-08 | CLI를 `server.py` sys.argv 분기에서 독립 console_script(`xcelium-mcp-cli`)로 재설계 |
| 1.12 | 2026-07-08 | §5.1/§5.2 소스 재검증 — F-D(registry.py 세션 상태)·F-175(TB 체크섬) 중복 발견, 자동화된 기존 메커니즘으로 대체 |
| 1.13 | 2026-07-08 | 문서 전체 커버리지 재검증 완료(§10/Migration Note 포함) — clean |
| 1.14 | 2026-07-08 | §6.4 중복 파일구조 다이어그램 누락분 수정 + §8.2 Phase C 정합화 |
| 1.15 | 2026-07-09 | sim-state.json을 독립 파일로 확정(registry.py 비확장) — 원격/로컬 머신 분리가 근거, 역할 분리 표 신설 |
| 1.16 | 2026-07-09 | 원격 서버 호스트명(`cloud0`) 하드코딩을 "원격 시뮬레이션 서버"로 일반화 |
| 1.17 | 2026-07-09 | TB frontmatter 생성 주체·시점 확정 — `verilog-tb-analyst` 작성 시점에 함께 생성(재파싱 모델 폐기) |
| 1.18 | 2026-07-09 | SKILL trigger에서 프로젝트별 테스트 네이밍 컨벤션(`TOP0`) 제거, 실배포 trigger로 동기화 |
| 1.19 | 2026-07-09 | `sim_state.py` 소유권 자기모순 수정 — 9개 지점을 "클라이언트 로컬(`skill-src/`)" 모델로 통일 |
| 1.20 | 2026-07-09 | "verilog-rtl skill 연계"를 `verilog-rtl-coder` agent 위임 + 사용자 승인 게이트로 구체화 |
| 1.21 | 2026-07-09 | Fix Sub-cycle(Plan→Design→Implement) 도입 — 승인 대상을 구두 제안에서 fix-plan 문서로 격상 |
| 1.22 | 2026-07-09 | Fix Sub-cycle 도입 직후 자체 재검토 — 동기화 누락 6건 수정 |
| 1.23 | 2026-07-09 | 승인 게이트를 2택(승인/거부)에서 3택(승인/수정 요청/보류)+개정·재개 루프로 확장 |
| 1.24 | 2026-07-09 | v1.23 직후 자체 재검토 — `/sim debug` 절차 중복 버그 + 스키마 동기화 누락 2건 수정 |
| 1.25 | 2026-07-10 | `debug` phase를 inline 텍스트에서 git-tracked 문서 포인터로 전환 + `.ai/sim-state/{test}/` 디렉터리로 통일 |
| 1.26 | 2026-07-10 | v1.25 직후 자체 재검토 — 동기화 누락 3건 + 각주 보강 |
| 1.27 | 2026-07-10 | "fix-plan 수정 요청 재조사"도 `debug.md`에 append하도록 확정 + 기록 형식(Iteration 헤더) 구조화 |
| 1.28 | 2026-07-10 | "재조사 금지 ≠ 신호 배제" 표현 정정(3곳) |
| 1.29 | 2026-07-10 | fix-implement에 "사람이 직접 구현" 경로 추가 + `verilog-rtl-reviewer` 필수 리뷰 게이트(fix-review) 신규 도입 |
| 1.30 | 2026-07-10 | v1.29 직후 자체 재검토 — 중복 위치 3곳(승인 게이트 표·agent 연계 표·DoD) 동기화 누락 수정 |
| 1.31 | 2026-07-10 | fix-review 게이트 안으로 formal(`verilog-rtl-prover`) 편입 — 1차 정적+2차 formal 2단 판정 구조 확정 |
| 1.32 | 2026-07-10 | v1.31 직후 자체 재검토 — "formal은 reviewer가 한다"는 잔존 표현 10곳 정정 |
| 1.33 | 2026-07-10 | `fix_target`(RTL vs TB) 분류 도입 + `verilog-tb-coder` agent 신설(cross-repo 계약) |
| 1.34 | 2026-07-10 | `verilog-tb-reviewer` 신설(self-check 모순 해소) + RTL+TB 동시 수정 시 "두 개의 순차 fix-plan" 방침 확정 |
| 1.35 | 2026-07-21 | fix-review findings를 사람 구현자에게 "기록=출력" 동일성으로 전달하는 절차 신규(`fix-review.md` append + 동일 텍스트 채팅 출력) |
| 1.36 | 2026-07-21 | §8.1 파일 목록의 `fix-design.md` template 오기 수정 — `adr-template.md` 재사용으로 정정(fix-plan.md 행과 분리) |

---

## Migration Note

이 문서는 원래 `venezia-fpga/docs/01-plan/features/debug-workflow-v2.plan.md`에 있었으나, 2026-07-02 xcelium-mcp 저장소로 이관되었다. Predecessor 문서(`xcelium-mcp-debugging-workflow.plan.md`)와 구현 대상 파일(§8.1)이 모두 이 저장소 소속이라 계획 소유권을 이 프로젝트의 bkit PDCA 사이클로 옮기는 것이 맞다. venezia-fpga 쪽 원본은 삭제되었다(git 히스토리로 복구 가능).
