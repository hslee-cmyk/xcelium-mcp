# xcelium-mcp-tool-usage-guide Planning Document

> **Summary**: `~/.claude/skills/xcelium-sim/` (user-level, RTL 프로젝트에서 사용) skill의 **Phase 1** — 24개 raw tool을 디버깅 phase별로 언제·어떤 순서로 쓸지 가르치는 reference 세트. `compound.py`(Layer 3) 완성을 기다리지 않고 즉시 착수 가능한 부분만 먼저 구현한다.
>
> **Project**: xcelium-mcp (계획 문서 소속) — **skill 자체는 `~/.claude/skills/xcelium-sim/`에 배포되어 임의의 RTL 검증 프로젝트(예: venezia-fpga)에서 사용됨**
> **Version**: -
> **Author**: HSLEE
> **Date**: 2026-07-02
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | xcelium-mcp에 24개 tool이 있지만 사용법 가이드는 CLAUDE.md의 짧은 프로즈(6-phase 요약)뿐이라, AI가 phase별로 어떤 tool을 어떤 순서·파라미터로 써야 하는지 매번 재해석해야 한다. 게다가 이 가이드는 xcelium-mcp를 **실제로 사용하는 RTL 프로젝트**(venezia-fpga 등)에서 세션이 시작될 때 로드돼야 하는데, xcelium-mcp repo 안에만 있으면 그 프로젝트에서 절대 로드되지 않는다. |
| **Solution** | CLAUDE.md의 6-phase 방법론(Phase 0~5)을 **user-level** skill `~/.claude/skills/xcelium-sim/`로 이관하고, phase별 `references/*.md`로 쪼개 progressive disclosure 구조로 만든다. 이 skill은 `xcelium-mcp-debug-workflow-v2.plan.md`가 설계한 **동일한 `/sim` skill**이며, 이 문서는 그중 compound.py 없이 지금 바로 만들 수 있는 부분(references/tool-map)만 다룬다. |
| **Function/UX Effect** | "FAIL 분석", "waveform", "시뮬레이션" 등 관련 키워드가 **RTL 프로젝트 세션**(venezia-fpga 등)에서 등장하면 skill이 자동 로드되어, 현재 디버깅 phase에 맞는 tool 선택 매트릭스와 파라미터 예시를 즉시 제공한다. xcelium-mcp의 CLAUDE.md는 15줄 이하 포인터로 축소된다. |
| **Core Value** | Layer 3(compound.py) 완성과 무관하게 지금 바로, 그리고 **xcelium-mcp를 쓰는 모든 프로젝트에서** tool 활용도를 높인다. 나중에 `/sim` subcommand(run/analyze/debug/verify)가 추가돼도 같은 skill 디렉터리 안에 얹힐 뿐 이 Phase 1 산출물은 그대로 남는다. |

---

## Context Anchor

> Auto-generated from Executive Summary. Propagated to Design/Do documents for context continuity.

| Key | Value |
|-----|-------|
| **WHY** | 24개 tool 사용법이 xcelium-mcp repo의 CLAUDE.md 프로즈로만 존재해, 정작 이 tool을 쓰는 RTL 프로젝트 세션에서는 로드되지 않고 AI가 매번 재해석함 |
| **WHO** | xcelium-mcp를 사용해 RTL 검증을 수행하는 모든 프로젝트의 AI 에이전트 (현재: venezia-fpga) |
| **RISK** | 자동 키워드 트리거가 과도하게 넓으면 무관한 대화에서도 로드; user-level 배포라 xcelium-mcp git 변경사항이 자동으로 반영되지 않고 수동 재배포 필요 |
| **SUCCESS** | RTL 프로젝트 세션에서 키워드 감지 시 자동 로드; 각 phase reference가 구체적 tool+파라미터 예시 포함; xcelium-mcp CLAUDE.md는 15줄 이하로 축소 |
| **SCOPE** | `~/.claude/skills/xcelium-sim/`의 Phase 1(references/tool-map, 즉시 착수)만 이 문서 소관. Subcommand 라우팅(`/sim run` 등, Layer 3/4)은 `xcelium-mcp-debug-workflow-v2`가 같은 디렉터리에 이어서 추가 |

---

## 1. Overview

### 1.1 Purpose

xcelium-mcp의 24개 MCP tool을 디버깅 phase별(Phase 0 인프라 분석 ~ Phase 5 수정+regression)로 "지금 어떤 tool을 어떤 파라미터로 써야 하는가"를 즉시 답할 수 있는 **user-level** skill을 만들어, xcelium-mcp를 사용하는 모든 RTL 프로젝트(venezia-fpga 등)에서 재사용한다.

### 1.2 Background

CLAUDE.md의 "Debugging Workflow" 섹션(6-phase, Phase 0~5)이 이미 방법론을 정의하고 있지만 두 가지 문제가 있다:
1. 프로즈 형태라 AI가 매번 전체를 재해석해야 하고, 구체적 tool 시그니처·파라미터 예시가 없다.
2. **더 근본적으로, 이 문서는 xcelium-mcp repo 안에만 있어서 실제로 디버깅이 일어나는 RTL 프로젝트(venezia-fpga) 세션에는 전혀 로드되지 않는다.** CLAUDE.md는 해당 프로젝트를 열었을 때만 컨텍스트에 들어간다.

한편 `xcelium-mcp-debug-workflow-v2.plan.md`는 이미 이 문제를 정확히 인식하고 `~/.claude/skills/xcelium-sim/`(user-level)를 Layer 4로 설계해두었다. 다만 그 문서의 skill은 `compound.py`(Layer 3, 미구현) 위에서 동작하는 `/sim run|analyze|debug|verify` subcommand를 전제로 한다. 이 문서는 **같은 skill 디렉터리**에서, compound.py 없이도 지금 만들 수 있는 부분(24개 raw tool의 phase별 사용법 reference)만 먼저 구현한다 — 두 계획은 별개 skill이 아니라 **같은 skill의 순차적 구현 단계**다.

### 1.3 Related Documents

- Predecessor: `docs/01-plan/features/xcelium-mcp-debugging-workflow.plan.md` (Phase 0~5 상세, TB 캐시, 실전 히스토리)
- **동일 skill의 Phase 2**: `docs/01-plan/features/xcelium-mcp-debug-workflow-v2.plan.md` §4 (`/sim` skill, subcommand 라우팅 — 같은 `~/.claude/skills/xcelium-sim/` 디렉터리에 이어서 추가됨)
- 소스: xcelium-mcp `CLAUDE.md` "Debugging Workflow" 섹션 (이관 대상)

---

## 2. Scope

### 2.1 In Scope

- [ ] **User-level** skill: `~/.claude/skills/xcelium-sim/SKILL.md` (xcelium-mcp repo 밖, `~/.claude/skills/`에 직접 배포 — chip-design-skills의 install.py 관리 체계는 따르지 않음, 독립 배포)
- [ ] 키워드 자동 트리거 — `description:` 블록 안에 `트리거:` 줄을 명시하는 형식(이 사용자의 다른 프로젝트 skill들과 동일한 컨벤션, 예: `verilog-rtl/SKILL.md`)으로 작성, 별도 `trigger:` frontmatter 필드가 아님
- [ ] `references/phase-0-discovery.md` ~ `references/phase-5-fix-regression.md` (6개, xcelium-mcp CLAUDE.md 6-phase에 대응)
- [ ] `references/tool-map.md` — 24개 tool을 phase/목적별로 분류한 결정 매트릭스
- [ ] xcelium-mcp `CLAUDE.md` "Debugging Workflow" 섹션을 skill 포인터로 간소화
- [ ] **CLAUDE.md 전체를 실제 소스(`src/xcelium_mcp/tools/*.py`, `server.py` 등록부)와 대조해 오래된 detail 전수 수정** — "Tool Groups (25 tools)" 표, "Repository Structure"의 tool 개수/위치 서술 등. §3.1 FR-11 참조
- [ ] **`references/phase-1-analysis.md`·`phase-4-waveform.md`·`phase-5-fix-regression.md`에 `verilog-rtl-debugger` agent(chip-design-skills, 신설 예정) 위임 지점 명시** — `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조(v2.4)와 1:1 대응. §3.1 FR-12 참조

### 2.2 Out of Scope

- `compound.py`/`sim_state.json`/`/sim` subcommand(`run`/`analyze`/`debug`/`verify`/`status`) 구현 — 같은 skill 디렉터리에 **후속으로** 추가됨 (→ `xcelium-mcp-debug-workflow-v2`)
- Hook 자동화 (PostToolUse/UserPromptSubmit JS) — debug-workflow-v2 Phase D 소관
- v5.1 Runner Abstraction (`RunnerInterface`, wrapper script 자동생성) — 별도 트랙, 무관
- chip-design-skills로의 편입 — 검토했으나 기각(§7 Dependencies). 방법론/도메인 스코프가 아니라 단일 MCP 서버 스코프라 그 repo의 기존 컨벤션과 맞지 않고, 독립 배포로 결정됨
- **`verilog-rtl-debugger` agent 자체의 구현(정의 파일 작성)** — chip-design-skills repo 소관, 별도 PDCA 사이클. 이 문서(skill)는 "언제 이 agent를 호출하는가"만 phase reference에 명시하며, agent가 아직 없을 때는 Claude가 직접 수행하도록 fallback 문구를 둔다(§3.1 FR-12)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `~/.claude/skills/xcelium-sim/SKILL.md` — `description:` 블록에 `트리거:` 키워드 목록 명시 (xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, debugging, 디버깅, CSV, checkpoint, bisect, regression 등) | High | Pending |
| FR-02 | `references/phase-0-discovery.md` — 검증 환경 인프라 분석(공유 컴포넌트/테스트케이스 캐시, `.ai/analysis/tb_*.analysis.md`) | High | Pending |
| FR-03 | `references/phase-1-analysis.md` — 사전 분석(캐시 참조 + RTL 분석서 + dump scope 확인) | High | Pending |
| FR-04 | `references/phase-2-simulation.md` — 시뮬레이션 실행(batch 우선 vs bridge, `sim_batch_run`/`sim_bridge_run`/`sim_regression` 선택 기준) | High | Pending |
| FR-05 | `references/phase-3-triage.md` — 1차 판별(로그: PASS/FAIL/Errors/UVM_ERROR) | High | Pending |
| FR-06 | `references/phase-4-waveform.md` — 2차 판별(waveform CSV + FSM 전이 대조, `checkpoint`/`bisect_signal`) | High | Pending |
| FR-07 | `references/phase-5-fix-regression.md` — 수정 + Regression + 문서 갱신 | Medium | Pending |
| FR-08 | `references/tool-map.md` — 24개 tool 전체를 phase/목적별로 분류한 결정 매트릭스 (God node급 tool인 `sim_batch_run`/`checkpoint`/`bisect_signal` 우선 상세화). **[2026-07-03 추가]** `xcelium-mcp-debugging-workflow.plan.md` §1D-5(v5.2 `dump_scopes`/`use_dump_history`/`auto_boundaries`/`boundary_depth`)를 반드시 포함 — 이 감사에서 발견된 최근 파라미터라 누락 위험 높음 | High | Pending |
| FR-09 | xcelium-mcp `CLAUDE.md`의 "Debugging Workflow" 섹션을 skill 포인터로 축소 (기존 v3/v2 문서들의 "15줄 패턴"과 동일) | Medium | Pending |
| FR-10 | 배포 방식: xcelium-mcp repo 안에 소스(`docs/`나 별도 폴더)를 두고 수동/스크립트로 `~/.claude/skills/xcelium-sim/`에 복사하는 절차 정의 — chip-design-skills의 `install.py` 패턴은 따르지 않되, "정본 repo → user-level 배포" 구조 자체는 동일하게 유지 | Medium | Pending |
| FR-12 | **`verilog-rtl-debugger` agent 위임 명시**: `references/phase-1-analysis.md`(분석서 부재/stale → `verilog-rtl-analyst` 위임), `references/phase-4-waveform.md`(FSM 전이 대조·AI 자율 디버깅 루프를 `verilog-rtl-debugger`가 직접 수행), `references/phase-5-fix-regression.md`(수정 코드 작성 → `verilog-rtl-coder`, 커밋 전 리뷰 → `verilog-rtl-reviewer`, 아키텍처 경계 시 → `verilog-rtl-architect-advisor`)에 각각 위임 지점을 `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조와 동일하게 명시. Agent가 아직 chip-design-skills에 없는 동안은 "Agent를 찾을 수 없으면 Claude가 직접 수행" fallback 문구 포함 | High | Pending |
| FR-11 | **CLAUDE.md 소스-오브-트루스 감사**: CLAUDE.md 전체를 `src/xcelium_mcp/tools/*.py`(batch/checkpoint/debug/signal_inspection/sim_lifecycle/simvision/waveform 7개 모듈) + `server.py` 등록부와 1:1 대조해 detail 오류 전수 수정. 확인된 항목만 예시: (1) "Tool Groups (25 tools)" 표의 tool 이름이 전부 v4.2 이전 명세(`get_signal_value`/`waveform_add_signals`/`save_checkpoint` 등) — 현재 코드엔 존재하지 않음, 실제 24개 tool(`checkpoint`/`waveform`/`watch`/`probe`/`simvision` 등 action-파라미터 통합형 포함)로 전면 재작성 (2) "Repository Structure"의 "server.py, 18 tool definitions" — 실제는 `tools/` 서브모듈 7개 파일에 분산, 18이 아님 (3) **[2026-07-03 추가 확인]** "## v3 Improvement Plan" 섹션 전체가 "7개 개선 항목 계획됨"으로 서술돼 있으나 실제로는 2026-03-30에 100% match rate로 완료됨(`xcelium-mcp-v3-improvements.plan.md`/`.analysis.md`/`.report.md` 확인) — 이 섹션 전체가 CLAUDE.md 갱신(FR-09) 대상. **tool-map.md(FR-08)와 CLAUDE.md 갱신 둘 다 기존 CLAUDE.md 프로즈가 아니라 이 감사 결과를 원본으로 작성** | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|---------------------|
| Discoverability | RTL 프로젝트(venezia-fpga 등) 세션에서 관련 키워드 등장 시 수동 `/command` 없이 skill 자동 로드 | venezia-fpga 세션에서 수동 시나리오 테스트 |
| Token Efficiency | 무관한 대화에서 오탐 로드 없음; 로드 시에도 현재 phase에 필요한 reference만 로드 | 트리거 키워드 리뷰 + 세션 관찰 |
| Content Fidelity | CLAUDE.md 6-phase 방법론 내용이 이관 과정에서 유실 없음 | 이관 전/후 diff 리뷰 |
| Portability | xcelium-mcp를 사용하는 어떤 RTL 프로젝트에서도(프로젝트별 경로 하드코딩 없이) 동작 | reference 내 경로가 모두 `{project}` 변수화돼 있는지 리뷰 |
| Accuracy | tool-map.md/CLAUDE.md의 모든 tool 이름·시그니처·개수가 현재 소스와 100% 일치(FR-11), CLAUDE.md·`xcelium-mcp-debugging-workflow.plan.md`·`xcelium-mcp-debug-workflow-v2.plan.md`·`xcelium-mcp-v3-improvements.plan.md` 등 관련 문서 간 tool 개수/이름 서술 불일치 없음 | 소스 대조 체크리스트(FR-11) + 문서 간 grep 대조표 |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `~/.claude/skills/xcelium-sim/SKILL.md` + `references/*.md` 7개 파일 작성 완료 (user-level)
- [ ] venezia-fpga(또는 다른 RTL 프로젝트) 세션에서 관련 키워드 대화 시 skill이 자동 트리거됨을 수동 확인
- [ ] xcelium-mcp `CLAUDE.md` Debugging Workflow 섹션이 15줄 이하 포인터로 축소
- [ ] 기존 6-phase 내용이 유실 없이 references로 이관됨
- [ ] xcelium-mcp repo 안의 소스(정본)와 `~/.claude/skills/xcelium-sim/`(배포본) 간 동기화 절차가 문서화됨(FR-10)

### 4.2 Quality Criteria

- [ ] `tool-map.md`가 24개 tool 전체를 빠짐없이 커버 (누락 시 Gap)
- [ ] 각 phase reference에 최소 1개 이상 구체적 tool 호출 예시(파라미터 포함) 포함
- [ ] 다른 project/user-level skill(verilog-rtl, chip-verification 등)과 트리거 키워드 중복 최소화
- [ ] CLAUDE.md의 tool 이름·개수·구조 서술이 소스(`tools/*.py`, `server.py`)와 100% 일치(FR-11), 관련 plan 문서들과도 tool 개수 서술이 일관됨
- [ ] `references/phase-2~4*.md`가 **Claude(skill 내부 사용)와 `verilog-rtl-debugger` agent(외부 Read 소비) 양쪽에서 모두 자기완결적으로 이해 가능** — skill 세션의 다른 컨텍스트(예: SKILL.md 라우팅 문구)에 의존하는 표현 금지

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 키워드 트리거가 과도하게 넓어 무관한 대화에서도 로드 | Medium | Medium | 트리거 키워드를 xcelium/시뮬레이션 특화 용어로 한정, 범용어(예: "테스트") 단독으로는 미트리거 |
| Phase reference를 과도하게 쪼개 탐색 비용 증가 | Low | Medium | 6개(Phase 0~5) + tool-map 1개로 상한, CLAUDE.md 6-phase 구조와 1:1 대응 유지 |
| `xcelium-mcp-debug-workflow-v2`가 같은 skill 디렉터리에 subcommand를 추가할 때 이 문서의 산출물(SKILL.md 라우팅 부분)과 충돌 | Medium | Medium | SKILL.md의 "Phase 1 전용" 섹션과 "subcommand 라우팅" 섹션을 명확히 분리해 작성 — Design 단계에서 두 문서의 SKILL.md 구조를 합쳐서 확정 |
| user-level 배포라 xcelium-mcp git의 변경이 자동 반영 안 됨 (정본↔배포본 drift) | Medium | High | FR-10 배포 절차 문서화, 변경 시 재배포 체크리스트를 skill README 또는 xcelium-mcp CLAUDE.md에 명시 |
| CLAUDE.md 이관 과정에서 6-phase 내용 유실 | High | Low | 이관 전/후 diff 리뷰를 Done 조건에 포함(§4.1) |
| CLAUDE.md의 "Tool Groups (25 tools)"·"Repository Structure" 등 기존 detail이 오래돼 실제 소스와 불일치(사용자 확인, 2026-07-03) — 이걸 그대로 skill/tool-map으로 이관하면 오류가 그대로 전파됨 | High | Confirmed | FR-11로 소스 대조 감사를 신규 요구사항화, tool-map.md/CLAUDE.md 갱신 모두 감사 결과를 원본으로 작성(기존 CLAUDE.md 프로즈를 그대로 베끼지 않음) |
| `verilog-rtl-debugger` agent가 chip-design-skills에 아직 구현되지 않은 상태로 이 skill의 reference가 그 agent 호출을 전제로 작성됨 — cross-repo 의존성 | Medium | High | FR-12에 "agent 없으면 Claude가 직접 수행" fallback 문구를 필수 포함, agent 구현 완료 후 reference 문구를 "직접 수행"에서 "agent 호출"로 갱신하는 체크리스트를 §8 Next Steps에 추가 |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|---------------------|
| `~/.claude/skills/xcelium-sim/` | 신규 디렉토리 (user-level, xcelium-mcp git 추적 밖) | SKILL.md + references/ 7개 파일 신규 생성 |
| xcelium-mcp `CLAUDE.md` "Debugging Workflow" 섹션 | 문서 | 6-phase 상세 프로즈 → skill 포인터로 축소 (정보는 삭제가 아니라 이관) |
| xcelium-mcp repo 내 skill 소스(신규, 위치는 Design 단계에서 결정) | 신규 파일 | user-level 배포본의 정본 — git으로 버전관리 |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| xcelium-mcp `CLAUDE.md` | READ | xcelium-mcp 프로젝트 세션 시작 시 컨텍스트로 로드 | None — 정보가 skill로 이관될 뿐 삭제되지 않음 |
| `~/.claude/skills/` 디렉터리 | READ (SessionStart) | **모든** Claude Code 프로젝트 세션(venezia-fpga 포함, xcelium-mcp가 아닌 다른 프로젝트도 전부) | Needs verification — user-level이므로 xcelium-mcp/venezia-fpga 외의 무관한 프로젝트 세션에서도 트리거 키워드 매칭 대상이 됨 (§5 Risk 1과 동일 맥락, 범위가 이번엔 전역이라 더 중요) |
| 24개 기존 tool | CALL | Claude Code MCP `tool_use` | None — 이 skill은 문서/가이드만 추가, tool 자체는 변경 없음 |

### 6.3 Verification

- [ ] xcelium-mcp/venezia-fpga와 무관한 다른 프로젝트 세션에서 트리거 키워드가 오탐하지 않는지 확인 (user-level 배포의 전역 영향 범위 특성상 필수)
- [ ] CLAUDE.md 이관 후에도 6-phase 원칙(batch+CSV 우선, TB 캐시 재사용 등)이 어디선가(skill) 여전히 명시돼 있는지 확인
- [ ] 기존 skill(verilog-rtl, chip-verification 등)의 트리거 키워드와 충돌 없는지 확인

---

## 7. Dependencies

### `xcelium-mcp-debug-workflow-v2.plan.md`와의 관계 — **동일 skill의 순차 구현 단계 (병합 결정, v0.2)**

- 최초 초안(v0.1)에서는 "별도 skill, 나중에 `/sim`이 흡수"로 설계했으나, 재검토 결과 **`~/.claude/skills/xcelium-sim/`이라는 같은 skill 디렉터리를 목표로 하는 하나의 skill**로 통합하기로 결정했다(2026-07-02).
- 이 문서(tool-usage-guide)는 **Phase 1**: `compound.py` 없이 지금 만들 수 있는 부분 — `references/phase-0~5.md`, `references/tool-map.md`, SKILL.md의 키워드 트리거·라우팅 스켈레톤.
- `xcelium-mcp-debug-workflow-v2`는 **Phase 2**: `compound.py`(Layer 3)가 생긴 뒤 같은 SKILL.md에 `/sim run|analyze|debug|verify|status` subcommand 라우팅을 추가.
- **결정(2026-07-03)**: Design 문서는 **별도 유지**한다(병합하지 않음). 이유: bkit의 matchRate는 feature별로 Design vs 구현을 비교하는데, 합치면 Phase 2(`xcelium-mcp-debug-workflow-v2`, compound.py 대기 중이라 아직 구현 불가)의 Design 요소가 이 feature의 Do에서 구현되지 않아 matchRate가 부당하게 낮아진다. 이 문서(Phase 1)는 지금 바로 Design→Do 진행 가능, Phase 2는 compound.py가 생긴 뒤 별도 Design→Do로 진행. SKILL.md 구조 충돌 방지를 위해 이 문서의 Design에서 Phase 2용 확장점(subcommand 라우팅 자리)을 명시적으로 비워두고 문서화한다.
- compound operation(`run_and_check` 등)이 생기면, `tool-map.md`의 개별 tool 나열 대신 compound operation을 우선 추천하도록 갱신 필요.

### `xcelium-mcp-v5.1-runner-abstraction.plan.md`와의 관계 — 무관

- 이 skill은 tool 선택/사용법 가이드이며, 시뮬레이터 실행 메커니즘(Runner 추상화)과는 레이어가 다르다. v5.1 진행 여부와 무관하게 작성 가능.

### chip-design-skills와의 관계 — 검토 후 기각

- chip-design-skills(`Todoc/fpga/chip-design-skills`)는 `verilog-rtl`/`uvm-verification`/`chip-verification` 등 **방법론/도메인 스코프** skill의 정본이며 `install.py`로 `~/.claude/skills/`에 배포한다.
- 이 skill은 **단일 MCP 서버(xcelium-mcp) 스코프**라 기존 컨벤션(도메인 스코프)과 맞지 않아 편입하지 않기로 결정. xcelium-mcp repo 자체가 정본, 독립 배포 절차(FR-10)를 별도로 정의한다.

### `verilog-rtl-debugger` agent(chip-design-skills, 신설 예정)와의 관계 — **cross-repo 의존, 신규(2026-07-03)**

- 이 skill과는 별개로 chip-design-skills repo에 신설되는 agent(§FR-12)이며, **이 skill의 정본과는 다른 배포 경로**(install.py, 기존 verilog-rtl-* agent와 동일)를 사용한다 — skill 자체의 독립 배포(FR-10)와 헷갈리지 말 것.
- 이 문서는 agent 구현을 기다리지 않고 "어디서 호출하는가"만 먼저 확정한다(FR-12) — agent 부재 시 fallback 문구로 즉시 착수 가능.
- 상세: `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조(v2.4~v2.5)가 정본.
- **역방향 의존 신규 확정(2026-07-03, `verilog-rtl-debugger.plan.md` v0.2)**: agent는 Phase 2~4 방법론을 자체 내장하지 않고 이 skill의 `references/phase-2~4*.md`를 **런타임에 Read**한다. 즉 이 skill이 agent보다 먼저(또는 최소 병행) 완성돼야 agent가 정상 동작한다 — **build 순서: skill reference 초안 → agent Do 단계**. 이로 인해 `references/phase-2~4*.md`는 Claude가 skill 안에서 읽는 내부 문서에 그치지 않고, **외부 agent가 소비하는 자기완결적 API 문서**가 되어야 한다(§4.2 Quality Criteria 참조).

---

## 8. Next Steps

1. [ ] `/pdca design xcelium-mcp-tool-usage-guide` — 별도 Design 문서로 진행(2026-07-03 결정, §7 참조). SKILL.md에 Phase 2(subcommand 라우팅)용 확장점을 명시적으로 남겨 `xcelium-mcp-debug-workflow-v2`의 향후 Design과 구조 충돌 방지
2. [ ] **소스-오브-트루스 감사(FR-11) — 다른 작업보다 선행**: `tools/*.py` 7개 모듈 + `server.py` 등록부를 1:1 스캔해 tool 인벤토리(이름/시그니처/action 파라미터) 확정, CLAUDE.md·`xcelium-mcp-debugging-workflow.plan.md`·`xcelium-mcp-debug-workflow-v2.plan.md`·`xcelium-mcp-v3-improvements.plan.md`의 tool 관련 서술과 대조해 불일치 목록 작성
3. [ ] Phase 0~5 reference 작성 (CLAUDE.md 6-phase 내용 이관 + tool 예시 보강, FR-11 감사 결과로 tool 예시 검증)
4. [ ] `tool-map.md` 작성 (FR-11 감사 결과를 원본으로, 24개 tool 전체 커버)
5. [ ] xcelium-mcp `CLAUDE.md` 간소화 + FR-11 감사 결과로 "Tool Groups"/"Repository Structure" 등 detail 전수 수정
6. [ ] user-level 배포 절차(FR-10) 정의 + venezia-fpga 세션에서 트리거 동작 수동 검증
7. [ ] `verilog-rtl-debugger` agent가 chip-design-skills에 구현되면, phase reference의 fallback 문구("Claude가 직접 수행")를 "agent 호출"로 갱신(FR-12 관련 체크리스트)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-02 | 초안 — debug-workflow-v2 리뷰 중 FR-09로 식별된 "tool 사용법 skill 분리" 필요성을 별도 feature로 승격. Project-level(`xcelium-mcp/.claude/skills/`) 배포로 계획 | HSLEE |
| 0.2 | 2026-07-02 | **User-level(`~/.claude/skills/xcelium-sim/`)로 배포 위치 변경** — 실제 디버깅은 venezia-fpga 등 별도 RTL 프로젝트에서 일어나므로 project-level(xcelium-mcp repo 안)로는 로드조차 안 됨. chip-design-skills 편입 검토 후 기각(도메인 스코프 컨벤션과 불일치). **`xcelium-mcp-debug-workflow-v2`의 `/sim` skill과 통합 결정** — 같은 skill 디렉터리의 Phase 1(이 문서)/Phase 2(debug-workflow-v2)로 재정의. FR-01 트리거 형식을 `description:`+`트리거:` 컨벤션으로 수정, FR-10(배포 절차) 신규 추가 | HSLEE |
| 0.3 | 2026-07-03 | **FR-11(CLAUDE.md 소스-오브-트루스 감사) 신규 추가** — 사용자가 CLAUDE.md의 MCP 사용법 detail이 오래됐다고 지적, 실제 검증 결과 "Tool Groups (25 tools)" 표의 tool 이름 전부가 v4.2 이전 명세(현재 코드에 미존재)이고 "Repository Structure"의 "server.py, 18 tool definitions"도 부정확(실제는 `tools/*.py` 7개 서브모듈에 24개 분산)함을 확인. tool-map.md(FR-08)와 CLAUDE.md 갱신(FR-09) 모두 기존 CLAUDE.md 프로즈가 아니라 이 감사 결과를 원본으로 작성하도록 명시, 관련 plan 문서(debugging-workflow/debug-workflow-v2/v3-improvements) 간 tool 개수 서술 일관성도 Success Criteria에 추가. Next Steps에 감사를 최우선 단계로 삽입 | HSLEE |
| 0.4 | 2026-07-03 | **FR-12(`verilog-rtl-debugger` agent 위임 명시) 신규 추가** — verilog/verilog-a RTL 분석 시 chip-design-skills의 reviewer급 agent를 사용해 결과물 완성도를 높이자는 요청에서, 기존 5개 agent 전부가 MCP tool 접근이 없어 라이브 디버깅(Phase 2~4)을 수행할 agent가 없다는 공백을 확인 → 신규 `verilog-rtl-debugger` agent(chip-design-skills 신설, 기존 agent와 동일 배포 경로) 도입으로 결정. `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조(v2.4)를 정본으로 phase reference 3개(1/4/5)에 위임 지점 매핑, cross-repo 의존 Risk + Dependencies 신규 섹션 추가, Out of Scope에 agent 구현 자체는 제외 명시 | HSLEE |
| 0.5 | 2026-07-03 | **방법론 중복 방지 — 역방향 의존 확정**: `verilog-rtl-debugger` agent가 Phase 2~4 방법론을 자체 내장하지 않고 이 skill의 `references/phase-2~4*.md`를 런타임에 Read하도록 chip-design-skills 쪽과 합의(`verilog-rtl-debugger.plan.md` v0.2) → 이 skill이 agent보다 먼저 완성돼야 하는 build 순서 의존이 생김, `references/phase-2~4*.md`가 skill 내부 문서에서 외부 agent가 소비하는 "API 문서"로 격상됨을 §7 Dependencies·§4.2 Quality Criteria에 반영 | HSLEE |
| 0.6 | 2026-07-03 | **Design 문서 분리 결정** — `xcelium-mcp-debug-workflow-v2`와 Design을 병합할지 여부를 확정: **별도 유지**. bkit matchRate가 feature별 Design-vs-구현 비교라, 병합 시 아직 구현 불가한 Phase 2 Design 요소가 matchRate를 왜곡하는 문제를 근거로 결정. §7 Dependencies·§8 Next Steps 갱신 | HSLEE |
| 0.7 | 2026-07-03 | **소스 재검증 전수 감사** — Design 착수 전 이 문서 + 참조 문서(`xcelium-mcp-debugging-workflow.plan.md`, CLAUDE.md, `xcelium-mcp-v3-improvements.plan.md`, `xcelium-mcp-v5.1-runner-abstraction.plan.md`, chip-design-skills `verilog-rtl-debugger.plan.md`) 전체를 `src/xcelium_mcp/tools/*.py` 실제 소스와 대조. 24개 tool 개수·이름·action 파라미터는 이 문서/debug-workflow-v2 기준 전부 정확함을 확인(수정 불필요). 발견 사항: FR-11에 CLAUDE.md "v3 Improvement Plan" 섹션 stale 사례 추가, FR-08에 v5.2 dump_scopes 누락 방지 문구 추가. v5.1-runner-abstraction은 재확인 결과 여전히 미구현(RunnerInterface/sim_start 코드 없음) — Draft 상태 정확, 수정 없음 | HSLEE |
