# xcelium-mcp-tool-usage-guide Design Document

> **Summary**: `~/.claude/skills/xcelium-sim/`(user-level) skill Phase 1 산출물의 구조·소스 관리·배포 절차를 설계한다. `compound.py` 없이 지금 만들 수 있는 부분(24개 tool의 phase별 사용법 reference)만 다루며, Phase 2(`xcelium-mcp-debug-workflow-v2`)가 나중에 얹을 subcommand 라우팅 확장점을 SKILL.md에 명시적으로 남긴다.
>
> **Project**: xcelium-mcp
> **Version**: 0.1
> **Author**: HSLEE
> **Date**: 2026-07-03
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-tool-usage-guide.plan.md](../01-plan/features/xcelium-mcp-tool-usage-guide.plan.md) (v0.7)

> **N/A 섹션 안내**: 이 feature는 웹앱이 아니라 Claude Code skill(markdown/YAML 산출물)이다. 템플릿의 §3 Data Model, §4 API Specification, §5 UI/UX, §9 Clean Architecture(Next.js 레이어), §10 Coding Convention(TS/React)은 원본 그대로 적용 불가 — 각 섹션에서 skill 저작 맥락에 맞게 대체하거나 N/A로 표기했다.

---

## Context Anchor

> Plan 문서(v0.7)에서 그대로 복사.

| Key | Value |
|-----|-------|
| **WHY** | 24개 tool 사용법이 xcelium-mcp repo의 CLAUDE.md 프로즈로만 존재해, 정작 이 tool을 쓰는 RTL 프로젝트 세션에서는 로드되지 않고 AI가 매번 재해석함 |
| **WHO** | xcelium-mcp를 사용해 RTL 검증을 수행하는 모든 프로젝트의 AI 에이전트 (현재: venezia-fpga) |
| **RISK** | 자동 키워드 트리거가 과도하게 넓으면 무관한 대화에서도 로드; user-level 배포라 xcelium-mcp git 변경사항이 자동으로 반영되지 않고 수동 재배포 필요 |
| **SUCCESS** | RTL 프로젝트 세션에서 키워드 감지 시 자동 로드; 각 phase reference가 구체적 tool+파라미터 예시 포함; xcelium-mcp CLAUDE.md는 15줄 이하로 축소 |
| **SCOPE** | `~/.claude/skills/xcelium-sim/`의 Phase 1(references/tool-map, 즉시 착수)만 이 문서 소관. Subcommand 라우팅(`/sim run` 등, Layer 3/4)은 `xcelium-mcp-debug-workflow-v2`가 같은 디렉터리에 이어서 추가 |

---

## 1. Overview

### 1.1 Design Goals

1. `references/*.md` 7개 파일이 CLAUDE.md의 6-phase 방법론(Phase 0~5) + `xcelium-mcp-debugging-workflow.plan.md`(v2.6, §1D-5 v5.2 dump_scopes 포함)의 detail을 유실 없이 이관한다.
2. SKILL.md는 Phase 1(이 문서)만으로 완결되게 동작하되, Phase 2(`xcelium-mcp-debug-workflow-v2`)가 나중에 subcommand 라우팅을 추가해도 구조 충돌이 없도록 명시적 확장점을 둔다.
3. 소스(`skill-src/xcelium-sim/`)와 배포본(`~/.claude/skills/xcelium-sim/`)의 관계를 코드 없이도 명확한 절차로 관리한다(FR-10, Option C).
4. `verilog-rtl-debugger` agent(chip-design-skills, 신설 예정)가 소비할 수 있도록 phase reference를 자기완결적으로 작성한다(tool-usage-guide.plan.md §4.2 Quality Criteria).

### 1.2 Design Principles

- **Progressive disclosure**: SKILL.md는 라우팅/트리거만, 상세 내용은 references/*.md로 분리 — 무관한 대화에서 불필요한 토큰 로드 방지.
- **Phase 1/Phase 2 경계 명시**: SKILL.md 안에 "Phase 1 전용" 섹션과 "Phase 2 확장점"을 물리적으로 분리된 마커로 구분.
- **소스 = 정본, 배포본 = 사본**: `skill-src/`가 git 추적되는 유일한 정본, `~/.claude/skills/xcelium-sim/`은 매번 덮어써도 되는 배포 산출물(수동 편집 금지).
- **감사 결과 우선**: FR-11(CLAUDE.md 감사)·§1D-5(v5.2 dump_scopes) 등 이번 세션에서 확정된 최신 정보를 원본으로 삼고, 낡은 CLAUDE.md 프로즈를 그대로 베끼지 않는다.

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| **Approach** | `~/.claude/skills/xcelium-sim/`에 직접 저작, repo 사본 없음 | git 정본 + 자동 배포 스크립트(hash 기반 drift 감지) | git 정본(`skill-src/`) + 문서화된 수동 배포 명령 |
| **New Files** | 8 (skill 디렉터리 자체) | 8 + 1(deploy script) | 8 |
| **Modified Files** | 0 (repo 밖) | 1 (README/CLAUDE.md 배포 절차) | 1 (README/CLAUDE.md 배포 절차) |
| **Complexity** | Low | High | Medium |
| **Maintainability** | Low (PR 리뷰·git blame 불가) | High (자동 안전장치) | High (git 이력 보존) |
| **Effort** | Low | High | Medium |
| **Risk** | High (drift 감지 수단 전무, FR-10 요구사항 자체가 무의미해짐) | Low (자동 감지) but 구축 비용 과잉 | Medium (재배포 깜박 위험, 수동 관리로 완화) |
| **Recommendation** | — | 향후 skill이 여러 개로 늘어나면 재검토 | **선택됨** |

**Selected**: **Option C — Pragmatic Balance** — **Rationale**: (Checkpoint 3, 2026-07-03) skill 1개만 존재하는 현재 규모에서 자동 배포 스크립트(Option B)는 과설계다. git 정본을 두되(Option A의 "PR 리뷰·git blame 불가" 문제 해결) 배포는 문서화된 수동 명령으로 충분 — drift 위험은 재배포 체크리스트(§11.2)로 완화한다. Skill이 여러 개로 늘어나거나 drift가 실제 문제로 발생하면 Option B(자동화)로 재검토.

> 상세 설계는 아래 선택된 Option C를 기준으로 한다.

### 2.1 Component Diagram

```
xcelium-mcp repo (git 정본)                    ~/.claude/ (user-level, 배포본)
┌─────────────────────────────┐                ┌──────────────────────────────┐
│ skill-src/xcelium-sim/       │   cp -r (수동)  │ skills/xcelium-sim/           │
│ ├── SKILL.md                 │ ─────────────▶ │ ├── SKILL.md                  │
│ └── references/              │                │ └── references/               │
│     ├── phase-0-discovery.md │                │     (동일 구조, 배포 산출물)   │
│     ├── phase-1-analysis.md  │                │                               │
│     ├── phase-2-simulation.md│                └──────────────────────────────┘
│     ├── phase-3-triage.md    │                         ▲
│     ├── phase-4-waveform.md  │                         │ Read (런타임)
│     ├── phase-5-fix-regression.md                      │
│     └── tool-map.md          │                ┌────────┴──────────────────────┐
└─────────────────────────────┘                │ verilog-rtl-debugger agent      │
                                                 │ (chip-design-skills, 별도 repo) │
CLAUDE.md (xcelium-mcp)                         └─────────────────────────────────┘
└── "Debugging Workflow" 섹션
    → skill 포인터로 15줄 이하 축소 (FR-09)
```

### 2.2 Data Flow (세션 트리거 흐름)

```
RTL 프로젝트 세션(venezia-fpga 등) 사용자 메시지
    → 키워드 매칭("FAIL 분석", "waveform", "시뮬레이션" 등, SKILL.md description의 트리거 목록)
    → SKILL.md 로드
    → 현재 디버깅 phase 판단(로그/dump 유무 등, Claude 판단)
    → 해당 references/phase-N-*.md 로드
    → tool-map.md에서 구체적 tool+파라미터 확인
    → xcelium-mcp MCP tool 호출 (또는 Phase 1B/5A류 판단은 향후 verilog-rtl-debugger agent에 위임 — Phase 2 확장점)
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `SKILL.md` | `references/*.md` 7개 | 트리거 후 상세 내용 progressive disclosure |
| `references/phase-2~4*.md` | `xcelium-mcp-debugging-workflow.plan.md` §Phase 2~4, §1D-5 | 방법론 원본(이관 대상) |
| `references/phase-2~4*.md` | (역방향) `verilog-rtl-debugger` agent | agent가 런타임에 이 파일을 Read (tool-usage-guide.plan.md §7 Dependencies, 2026-07-03 결정) |
| `tool-map.md` | `src/xcelium_mcp/tools/*.py` 소스 감사 결과(FR-11 방법론과 동일하게 소스 직접 대조) | 24개 tool 정확한 인벤토리 |
| xcelium-mcp `CLAUDE.md` | `references/*.md` (요약 대상) | 15줄 이하 포인터로 축소 |

---

## 3. Skill 산출물 구조 (§3 Data Model 대체)

### 3.1 SKILL.md Frontmatter

```yaml
---
name: xcelium-sim
description: |
  xcelium-mcp MCP tool(24개)을 phase별로 언제·어떤 파라미터로 쓸지 안내. RTL 시뮬레이션 디버깅
  워크플로우(Phase 0 인프라 분석~Phase 5 수정+regression)를 단계별로 가이드.
  트리거: xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, debugging, 디버깅, CSV,
    checkpoint, bisect, regression, dump_scopes, dump_depth.
argument-hint: ""   # Phase 1은 subcommand 없음 — Phase 2가 "[action] [test]" 추가 예정(§3.2 확장점)
user-invocable: false   # Phase 1은 키워드 트리거만, 슬래시 커맨드는 Phase 2(/sim)에서 활성화
---
```

> **주의**: 이 사용자의 다른 project skill(`verilog-rtl/SKILL.md` 등)과 동일하게 `description:` 블록 안에 `트리거:` 줄을 두는 컨벤션을 따른다 — 별도 `trigger:` 필드가 아니다(Plan FR-01).

### 3.2 SKILL.md 본문 구조 — Phase 1/Phase 2 확장점 분리

```markdown
# xcelium-sim

## Phase 1 — Tool 사용법 가이드 (이 문서 소관)

이 문서는 24개 xcelium-mcp tool을 디버깅 phase별로 어떻게 쓰는지 안내한다.

| Phase | Reference | 내용 |
|-------|-----------|------|
| Phase 0 | references/phase-0-discovery.md | 검증 환경 인프라 분석 |
| Phase 1 | references/phase-1-analysis.md | 사전 분석 |
| Phase 2 | references/phase-2-simulation.md | 시뮬레이션 실행 |
| Phase 3 | references/phase-3-triage.md | 1차 판별(로그) |
| Phase 4 | references/phase-4-waveform.md | 2차 판별(waveform CSV) |
| Phase 5 | references/phase-5-fix-regression.md | 수정+regression |
| — | references/tool-map.md | 24개 tool 결정 매트릭스 |

<!-- ============================================================
     PHASE 2 확장점 (xcelium-mcp-debug-workflow-v2가 추가 예정)
     이 마커 아래에 subcommand 라우팅(/sim run|analyze|debug|verify|status)이
     compound.py(Layer 3) 완성 후 삽입된다. Phase 1 구현 시점에는 빈 상태로 둔다.
     ============================================================ -->

## Phase 2 — Subcommand 라우팅 (Pending, xcelium-mcp-debug-workflow-v2 소관)

*(compound.py 완성 후 채워짐 — 현재는 Phase 1의 phase reference만으로 동작)*
```

이 마커 구조가 §2.0에서 확정한 "Phase 1/Phase 2 경계 명시" 원칙의 구체적 구현이다 — `xcelium-mcp-debug-workflow-v2`의 Design 단계는 이 마커 아래에만 내용을 추가하면 되고, 위쪽(Phase 1 산출물)은 건드리지 않는다.

### 3.3 references/*.md 공통 포맷

각 phase reference는 다음을 반드시 포함한다(Plan §4.2 Quality Criteria):

```markdown
# Phase N — {제목}

## 목적
{1-2문장}

## 절차
{xcelium-mcp-debugging-workflow.plan.md의 해당 Phase 내용을 압축 이관}

## Tool 예시
```python
{구체적 tool 호출, 파라미터 포함}
```

## verilog-rtl-debugger agent 위임 (해당 시)
{§Agent 위임 구조의 매핑표에서 이 phase에 해당하는 행 — Phase 1/4/5만 해당}
```

> **agent 자기완결성 요구사항**: 위 포맷은 Claude(skill 세션 내)와 `verilog-rtl-debugger` agent(별도 context, SKILL.md 라우팅 문구를 모름) 양쪽에서 독립적으로 이해 가능해야 한다 — SKILL.md의 "위에서 설명했듯이" 같은 세션-종속 표현 금지.

---

## 4. Tool-map.md 결정 매트릭스 설계 (§4 API Specification 대체)

### 4.1 매트릭스 축

| 축 | 값 |
|----|-----|
| Phase | 0/1/2A/2B/3/4A~4E/5A~5E |
| Tool | 24개 전체(FR-11 소스 감사 결과 기준 — CLAUDE.md 프로즈 아님) |
| God node 우선 상세화 | `sim_batch_run`(19개 파라미터, `dump_scopes`/`use_dump_history` 포함), `checkpoint`(action 4종), `bisect_signal`(Mode A/B) |

### 4.2 신규 반영 필수 항목 (2026-07-03 감사에서 발견, 누락 방지)

| 항목 | 출처 | 반영 위치 |
|------|------|----------|
| `dump_scopes`/`use_dump_history` (sim_batch_run/sim_regression) | debugging-workflow.plan.md §1D-5 (v5.2) | tool-map.md, phase-1-analysis.md, phase-4-waveform.md |
| `auto_boundaries`(sim_bridge_run) / `boundary_depth`(sim_discover) | 〃 | tool-map.md, phase-0-discovery.md |
| `ssh_run(kill -s INT ...)`가 네이티브 tool이 아님 | debugging-workflow.plan.md v2.6 수정 노트 | tool-map.md에 "24개 네이티브 + ssh-mcp 헬퍼 1건 별도" 명시 |

---

## 5. N/A — UI/UX Design

이 feature는 UI가 없다(markdown 산출물). §5 전체 N/A.

---

## 6. Error Handling (skill 로딩 실패 대응으로 대체)

| 상황 | 원인 | 대응 |
|------|------|------|
| 키워드 트리거 후 skill이 안 로드됨 | user-level 배포 누락(FR-10 절차 미실행) | §11.2 배포 체크리스트 확인 |
| `verilog-rtl-debugger` agent가 reference를 못 찾음 | skill 배포 전에 agent가 먼저 실행됨(순서 위반) | tool-usage-guide.plan.md §7 Dependencies에 명시된 build 순서(skill 먼저) 재확인 |
| Phase reference 내용이 CLAUDE.md와 불일치 | 이관 시 누락 또는 이후 소스 코드 변경 미반영 | FR-11 방식(소스 직접 대조)으로 재감사 |

---

## 7. Security Considerations

- [ ] `references/*.md`에 프로젝트별 민감 정보(실제 서버 IP, 사용자명 등) 하드코딩 금지 — `{project}` 변수화 원칙(Plan NFR Portability) 재확인
- [ ] user-level 배포이므로 다른 프로젝트 세션에서도 로드됨 — 트리거 키워드가 무관한 프로젝트의 민감한 맥락과 섞이지 않는지 검토(Plan §6.3 Verification)

---

## 8. Test Plan

> 이 feature에 API/UI가 없으므로 L1/L2/L3(웹앱 표준)를 다음과 같이 재정의한다.

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1: 내용 정확성 | tool-map.md의 24개 tool 이름·파라미터 | 소스 직접 대조(grep/Read) | Do |
| L2: 트리거 동작 | SKILL.md 키워드 매칭 | venezia-fpga 세션 수동 시나리오 | Do/Check |
| L3: E2E 시나리오 | phase reference를 따라 실제 디버깅 1건 완주 | venezia-fpga 세션 수동 실행 | Check |

### 8.2 L1: 내용 정확성 시나리오

| # | 대상 | 검증 방법 | 기대 결과 |
|---|------|----------|----------|
| 1 | tool-map.md 24개 tool 전체 | `grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py`와 개수 대조 | 정확히 일치 |
| 2 | 각 tool의 action 값 | 소스 docstring과 대조 | 100% 일치 |
| 3 | `dump_scopes`/`use_dump_history`/`auto_boundaries` 포함 여부 | tool-map.md grep | 존재 |
| 4 | CLAUDE.md 축소본 | FR-09 15줄 이하 확인 | 라인 수 ≤15 |

### 8.3 L2: 트리거 동작 시나리오

| # | 시나리오 | 액션 | 기대 결과 |
|---|---------|------|----------|
| 1 | venezia-fpga 세션에서 "FAIL 분석해줘" 입력 | 관찰 | xcelium-sim skill 자동 로드 |
| 2 | xcelium-mcp/venezia-fpga와 무관한 다른 프로젝트에서 일상 대화 | 관찰 | skill 오탐 로드 없음(Plan §6.3) |
| 3 | verilog-rtl 등 다른 skill과 동시 활성화 가능 여부 | 관찰 | 트리거 키워드 충돌 없음 |

### 8.4 L3: E2E 시나리오

| # | 시나리오 | 단계 | 성공 기준 |
|---|---------|------|----------|
| 1 | venezia-fpga 실제 FAIL 테스트 1건 디버깅 | Phase 0→5 reference를 따라 수동 진행 | 근본 원인까지 도달, tool 예시가 실제로 동작 |

### 8.5 Seed Data Requirements

N/A — DB 없음. 대신 venezia-fpga의 실제 실패 테스트케이스 1건(예: TOP015류) 존재 필요.

---

## 9. N/A — Clean Architecture (Next.js 레이어 구조)

이 feature는 markdown 산출물이라 Presentation/Application/Domain/Infrastructure 레이어 구분이 적용되지 않는다. 대신 §3.2의 Phase 1/Phase 2 SKILL.md 구조 분리가 이 역할을 한다.

---

## 10. N/A — Coding Convention (TS/React)

대신 §3.3의 references/*.md 공통 포맷이 이 feature의 "코딩 컨벤션"에 해당한다.

---

## 11. Implementation Guide

### 11.1 File Structure

```
xcelium-mcp/
└── skill-src/
    └── xcelium-sim/
        ├── SKILL.md
        └── references/
            ├── phase-0-discovery.md
            ├── phase-1-analysis.md
            ├── phase-2-simulation.md
            ├── phase-3-triage.md
            ├── phase-4-waveform.md
            ├── phase-5-fix-regression.md
            └── tool-map.md
```

### 11.2 배포 절차 (FR-10, Option C)

```bash
# 1. skill-src/xcelium-sim/ 수정 완료 후
cp -r skill-src/xcelium-sim ~/.claude/skills/
# 2. 재배포 체크리스트 (CLAUDE.md 또는 skill-src/README.md에 명시)
#    - [ ] skill-src/ 변경 시 매번 위 명령 재실행
#    - [ ] ~/.claude/skills/xcelium-sim/를 직접 편집하지 말 것(다음 배포 시 덮어써짐)
#    - [ ] venezia-fpga 등 소비 프로젝트 세션에서 트리거 재확인
```

### 11.3 Implementation Order

1. [ ] `tool-map.md` 작성 (FR-08, §4의 신규 반영 필수 항목 포함) — 다른 reference가 이걸 참조하므로 최우선
2. [ ] `references/phase-0-discovery.md` ~ `phase-5-fix-regression.md` 6개 작성 (FR-02~FR-07, debugging-workflow.plan.md 이관 + §1D-5 반영)
3. [ ] Phase 1/4/5 reference에 `verilog-rtl-debugger` agent 위임 지점 삽입 (FR-12)
4. [ ] `SKILL.md` 작성 (§3.1 frontmatter + §3.2 Phase 1/Phase 2 마커 구조)
5. [ ] xcelium-mcp `CLAUDE.md` 축소 (FR-09, FR-11 감사 결과 기준 — "Tool Groups"/"v3 Improvement Plan" 등 stale 섹션 전부 정리)
6. [ ] `skill-src/README.md`에 배포 절차(§11.2) 문서화
7. [ ] `cp -r`로 최초 배포 + venezia-fpga 세션 수동 검증(§8.3, §8.4)

### 11.4 Session Guide

#### Module Map

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| tool-map + phase references | `module-1` | Implementation Order 1~3 | 40-50 |
| SKILL.md + CLAUDE.md 축소 | `module-2` | Implementation Order 4~5 | 20-25 |
| 배포 + 검증 | `module-3` | Implementation Order 6~7 | 15-20 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 | Plan + Design | 전체 (완료) | - |
| Session 2 | Do | `--scope module-1` | 40-50 |
| Session 3 | Do | `--scope module-2,module-3` | 35-45 |
| Session 4 | Check + Report | 전체 | 20-30 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-03 | 초안 — Plan v0.7 기반. Checkpoint 3에서 Option C(Pragmatic Balance: git 정본 `skill-src/` + 수동 배포) 선택. SKILL.md의 Phase 1/Phase 2 확장점 마커 구조 확정, FR-11/§1D-5 감사 결과를 tool-map.md 필수 반영 항목으로 명시 | HSLEE |
