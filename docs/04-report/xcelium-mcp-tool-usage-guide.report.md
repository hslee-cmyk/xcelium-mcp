# xcelium-mcp-tool-usage-guide Completion Report

> **Status**: Complete
>
> **Project**: xcelium-mcp
> **Version**: Phase 1 (tool usage guide, compound.py-independent)
> **Author**: HSLEE
> **Completion Date**: 2026-07-03
> **PDCA Cycle**: #1 (Feature Inception)

---

## Executive Summary

### 1.1 Project Overview

| Item | Content |
|------|---------|
| Feature | xcelium-mcp-tool-usage-guide — 24개 MCP tool의 phase별 사용법을 user-level skill로 제공 |
| Start Date | 2026-07-02 (Plan v0.1 초안) |
| End Date | 2026-07-03 (Design v0.1 → Do → Check 완료) |
| Duration | 2 days (계획 → 설계 → 구현 → 감사) |
| Scope | Phase 1(즉시 착수): user-level skill `~/.claude/skills/xcelium-sim/` 배포, 6개 phase reference + tool-map, CLAUDE.md 축소 |

### 1.2 Results Summary

```
┌──────────────────────────────────────────────────┐
│  Completion Rate: 100%                           │
├──────────────────────────────────────────────────┤
│  ✅ Complete:     13 FR + 10 Success Criteria    │
│  ⏳ Next Cycle:   compound.py 기다리는 Phase 2  │
│  ❌ Cancelled:    0 items                        │
└──────────────────────────────────────────────────┘
```

### 1.3 Value Delivered

| Perspective | Content |
|-------------|---------|
| **Problem** | 24개 xcelium-mcp MCP tool의 사용법이 xcelium-mcp repo의 CLAUDE.md 프로즈로만 존재해, 정작 이 tool을 쓰는 RTL 프로젝트(venezia-fpga 등) 세션에는 로드되지 않음. AI가 매번 처음부터 재해석해야 함. |
| **Solution** | 6-phase 디버깅 workflow(Phase 0~5)를 user-level skill `~/.claude/skills/xcelium-sim/`으로 이관. Phase별 reference 분리, tool-map.md 결정 매트릭스 제공. CLAUDE.md는 15줄 이하 포인터로 축소. |
| **Function/UX Effect** | **"FAIL 분석", "waveform", "checkpoint" 등 키워드가 venezia-fpga 세션에서 등장 → skill 자동 로드** → 현재 디버깅 phase를 판단 → 해당 phase reference 로드 → 구체적 tool+파라미터 예시 즉시 제공. 2026-07-03 사용자가 실제 세션에서 트리거 동작 확인 완료. |
| **Core Value** | Layer 3(compound.py) 완성과 무관하게 **지금 바로**, xcelium-mcp를 사용하는 **모든 RTL 프로젝트에서** tool 선택/파라미터 활용도를 높임. 나중에 `/sim` subcommand가 추가돼도 같은 skill 디렉터리의 Phase 2 마커 아래에만 얹혀 이 산출물은 그대로 유지됨. |

### 1.4 Success Criteria Final Status

#### Definition of Done (§4.1, 5/5 항목)

| # | 항목 | 상태 | 근거 |
|---|------|:----:|------|
| 1 | SKILL.md + references 7개 파일 작성 완료 | ✅ Met | skill-src/xcelium-sim/ 8개 파일 모두 배포(SKILL.md + phase-0~5 + tool-map) |
| 2 | venezia-fpga 세션 자동 트리거 수동 확인 | ✅ Met | 2026-07-03 사용자가 실제 세션에서 트리거 키워드("FAIL 분석" 등) 동작 확인 |
| 3 | CLAUDE.md Debugging Workflow ≤15줄 축소 | ✅ Met | 현재 "Debugging Workflow" 섹션 ~9줄(3문장) — 정본 skill 포인터만 유지 |
| 4 | 기존 6-phase 내용 유실 없이 이관 | ✅ Met | phase-0~5 reference에 캐시 규칙, tier, sentinel 중단, bisect 2-mode, 수정-회귀 전체 이관 확인 |
| 5 | 정본↔배포본 동기화 절차 문서화(FR-10) | ✅ Met | skill-src/README.md + Design §11.2에 `cp -r` 명령 + 재배포 체크리스트 문서화 |

#### Quality Criteria (§4.2, 5/5 항목)

| # | 항목 | 상태 | 근거 |
|---|------|:----:|------|
| 1 | tool-map.md 24개 tool 전체 커버 | ✅ Met | src/xcelium_mcp/tools/*.py 소스 7개 모듈의 24개 함수명과 byte-for-byte 매핑 확인 |
| 2 | 각 phase reference ≥1 구체적 tool 호출 예시(파라미터 포함) | ✅ Met | phase-0~5 모두 "Tool 예시" 섹션에 코드블록 보유(예: `sim_batch_run(test="TOP015", dump_scopes=[...])` 등) |
| 3 | 타 skill 트리거 키워드 중복 최소화 | ✅ Met | 2026-07-03 cross-grep 완료(7개 skill 대조): "regression" 1건이 chip-verification과 중복, 나머지 12개 키워드는 중복 없음. "regression"은 둘 다 RTL regression과 실제 관련 있어 심각도 낮음(수정 불필요로 확정) |
| 4 | CLAUDE.md tool 개수·이름·구조 100% 소스 일치(FR-11) | ✅ Met | "Tool Groups (25 tools)" 오류 정정(실제 24개), "Repository Structure" 서술 갱신, stale "v3 Improvement Plan" 섹션 제거 |
| 5 | phase-2~4 reference가 외부 agent(verilog-rtl-debugger) 소비 가능하도록 자기완결적 | ✅ Met | 세션 종속 표현("위에서 설명했듯이") 없음, agent 위임 지점 명시, 각 phase의 목적·절차·tool 예시 독립적으로 이해 가능 |

**Success Rate**: 10/10 criteria met (100%)

### 1.5 Decision Record Summary

| 출처 | 의사결정 | 따랐는가? | 결과 |
|------|---------|:--------:|------|
| [Plan §1.2] | 24개 tool을 CLAUDE.md 프로즈에서 user-level skill로 이관 | ✅ | skill-src/xcelium-sim/ 배포, 실제 세션에서 키워드 트리거 동작 확인 |
| [Design §2.0] | Architecture Option C (git 정본 `skill-src/` + 수동 배포) 선택 | ✅ | skill-src/ 소스 유지, README.md 배포 절차 문서화, 타 skill이 늘어나면 Option B(자동) 재검토 |
| [Plan FR-11] | CLAUDE.md 소스-오브-트루스 감사 (tool 개수/이름/stale 섹션) | ✅ | 25→24 정정, "Tool Groups"/"Repository Structure"/"v3 Improvement Plan" 전부 갱신, 이후 tool-map.md와 CLAUDSE.md 모두 감사 결과를 원본으로 작성 |
| [Plan FR-12] | `verilog-rtl-debugger` agent 위임 지점 명시 (Phase 1/4/5 reference) | ✅ | phase-1-analysis.md §1B (분석서 부재/stale), phase-4-waveform.md §4A (FSM 대조·AI 루프), phase-5-fix-regression.md §5A (수정 코드) — agent 미구현 시 fallback 문구 포함 |
| [Plan FR-13] | `verilog-tb-analyst` agent 위임 지점 명시 (Phase 0/1 reference) | ✅ | phase-0-discovery.md §0A/0B (TB 공유 컴포넌트·테스트케이스 분석), phase-1-analysis.md §1A (캐시 미스) — agent 미구현 시 fallback 문구 포함 |

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [xcelium-mcp-tool-usage-guide.plan.md](../01-plan/features/xcelium-mcp-tool-usage-guide.plan.md) (v0.8) | ✅ Finalized |
| Design | [xcelium-mcp-tool-usage-guide.design.md](../02-design/features/xcelium-mcp-tool-usage-guide.design.md) (v0.1) | ✅ Finalized |
| Check | [xcelium-mcp-tool-usage-guide.analysis.md](../03-analysis/xcelium-mcp-tool-usage-guide.analysis.md) | ✅ Complete (98% match rate) |
| Act | Current document | 🔄 Writing |

---

## 3. Completed Items

### 3.1 Functional Requirements (13개 모두 완료)

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| FR-01 | `SKILL.md` frontmatter 트리거 키워드 명시 (`description:` 블록) | ✅ Complete | 12개 키워드: xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, debugging, 디버깅, CSV, checkpoint, bisect, regression, dump_scopes, dump_depth |
| FR-02 | `references/phase-0-discovery.md` (TB 캐시·공유 컴포넌트) | ✅ Complete | verilog-tb-analyst 위임 지점(§0A/0B) + fallback 포함 |
| FR-03 | `references/phase-1-analysis.md` (캐시/RTL 분석서/dump scope) | ✅ Complete | dump_scopes v5.2 포함, verilog-rtl-analyst/verilog-tb-analyst 위임 지점(§1A/1B) + fallback |
| FR-04 | `references/phase-2-simulation.md` (Batch vs Bridge, sentinel) | ✅ Complete | sim_batch_run/sim_regression/sim_bridge_run 선택 기준 제시 |
| FR-05 | `references/phase-3-triage.md` (로그 기반 1차 판별) | ✅ Complete | PASS/FAIL/Errors/UVM_ERROR 판별 절차 |
| FR-06 | `references/phase-4-waveform.md` (CSV·FSM·bisect) | ✅ Complete | checkpoint/bisect_signal 2-mode, verilog-rtl-debugger 위임 지점(§4A) + fallback |
| FR-07 | `references/phase-5-fix-regression.md` (수정·회귀·문서) | ✅ Complete | verilog-rtl-coder/reviewer/architect 위임 지점 + fallback |
| FR-08 | `references/tool-map.md` (24개 tool 매트릭스) | ✅ Complete | v5.2 dump_scopes/use_dump_history + auto_boundaries/boundary_depth 포함, God node 3개(sim_batch_run 19개 파라미터, checkpoint 4종, bisect 2-mode) 상세화 |
| FR-09 | CLAUDE.md "Debugging Workflow" 섹션 ≤15줄 축소 | ✅ Complete | 현재 ~9줄, skill 정본 포인터만 유지 |
| FR-10 | 배포 절차(정본↔배포본) 문서화 | ✅ Complete | `skill-src/README.md` + Design §11.2: `cp -r skill-src/xcelium-sim ~/.claude/skills/` + 체크리스트 |
| FR-11 | CLAUDE.md 소스-오브-트루스 감사 | ✅ Complete | 25→24 정정, stale 섹션 제거, 관련 plan 4개 문서 간 tool 개수 일관성 확인 |
| FR-12 | `verilog-rtl-debugger` agent 위임 명시 | ✅ Complete | Phase 1/4/5 reference 3개, agent 미구현 시 fallback, chip-design-skills 별도 PDCA |
| FR-13 | `verilog-tb-analyst` agent 위임 명시 (신규) | ✅ Complete | Phase 0/1 reference 2개, 위임 비대칭 해소, agent 미구현 시 fallback |

### 3.2 Non-Functional Requirements

| Category | Criteria | Achieved | Status |
|----------|----------|----------|--------|
| Discoverability | RTL 프로젝트 세션 키워드 트리거 | 자동 로드 확인(venezia-fpga 실증) | ✅ |
| Token Efficiency | 무관한 대화 오탐 없음 | 범용어 단독 제외, xcelium 특화 키워드만 | ✅ |
| Content Fidelity | 6-phase 내용 유실 | 0건 (전체 이관 완료) | ✅ |
| Portability | 프로젝트별 경로 변수화 | {project} 사용, 하드코딩 없음 | ✅ |
| Accuracy | tool 이름·개수 소스 100% 일치 | 24/24 매핑, 3-way 검증 | ✅ |

### 3.3 Deliverables

| 산출물 | 위치 | 상태 |
|--------|------|------|
| SKILL.md (정본) | `skill-src/xcelium-sim/SKILL.md` | ✅ 8KB |
| phase-0-discovery.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| phase-1-analysis.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| phase-2-simulation.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| phase-3-triage.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| phase-4-waveform.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| phase-5-fix-regression.md | `skill-src/xcelium-sim/references/` | ✅ 완성 |
| tool-map.md | `skill-src/xcelium-sim/references/tool-map.md` | ✅ 1056줄, 24개 tool 커버 |
| 배포본 (사본) | `~/.claude/skills/xcelium-sim/` | ✅ byte-diff 일치 |
| README.md (배포 절차) | `skill-src/README.md` | ✅ 문서화 |
| CLAUDE.md (축소) | `docs/CLAUDE.md` "Debugging Workflow" 섹션 | ✅ ~9줄 |

---

## 4. Incomplete Items

### 4.1 Carried Over to Next Cycle (Phase 2)

| Item | Owner | Priority | Expected Start |
|------|-------|----------|-----------------|
| `/sim` subcommand 라우팅 (`run`, `analyze`, `debug`, `verify`, `status`) | xcelium-mcp-debug-workflow-v2 | High | compound.py 완성 후 (2026-Q3 예정) |
| `compound.py` (Layer 3, compound operation 관리) | xcelium-mcp v5.2+ | High | 별도 계획 |

### 4.2 Cross-Repository Dependencies (별도 PDCA 사이클)

| Agent | Status | Consumer | Impact |
|-------|--------|----------|--------|
| `verilog-rtl-debugger` | 미구현(chip-design-skills 예정) | phase-1/4/5 reference (RT Read) | Phase 1~5의 agent 지점에 fallback 문구 포함, agent 완성 후 갱신 필요 |
| `verilog-tb-analyst` | 미구현(chip-design-skills 예정) | phase-0/1 reference (RT Read) | Phase 0~1의 agent 지점에 fallback 문구 포함, agent 완성 후 갱신 필요 |

---

## 5. Quality Metrics

### 5.1 Design Match Rate (갭 분석 결과)

| 차원 | 목표 | 최종 | 변화 | 상태 |
|------|------|------|------|------|
| Structural Match (파일 구조) | 90% | 100% | +10% | ✅ skill-src 8개 파일 전부 존재, byte-diff 완전 일치 |
| Functional Depth (콘텐츠 완결성) | 90% | 98% | +8% | ✅ 24개 tool 전부, v5.2 파라미터, 6개 phase reference 공통 포맷, agent 위임 명시 |
| Contract 일치 (cross-document consistency) | 90% | 100% | +10% | ✅ CLAUDE.md ↔ SKILL.md ↔ tool-map.md tool 개수 24개 3-way 일관 |
| Intent Match (Plan vs Do) | 90% | 100% | +10% | ✅ 13개 FR + 10개 Success Criteria 모두 Met |
| **Overall Match Rate** | **90%** | **98%** | **+8%** | ✅ Gate Pass |

### 5.2 Resolved Issues (이 사이클에서 발견·수정)

| Issue | Root Cause | Resolution | Result |
|-------|-----------|-----------|--------|
| CLAUDE.md "Tool Groups (25 tools)" 오류 | 낡은 프로즈(v4.2 기준, 현재 코드 미반영) | 24개로 정정, source truth 갱신 | ✅ 3-way 검증 일치 |
| CLAUDE.md "v3 Improvement Plan" 섹션 stale | v3 완료(2026-03-30) 이후 미갱신 | 섹션 전체 제거, 축약 내용으로 대체 | ✅ 감사 결과 기반 재작성 |
| CLAUDE.md "Repository Structure" 부정확 | "server.py, 18 tool definitions" → 실제 `tools/*.py` 7개 모듈에 24개 분산 | 서술 갱신 | ✅ 소스와 일치 |
| 타 skill과 트리거 키워드 중복 | "regression" → chip-verification도 사용 | cross-grep 확인, 실제 관련 있어 수정 불필요로 확정 | ✅ 심각도 낮음 |
| 배포본 byte-diff 검증 | skill-src 완성 후 배포본 동기화 | `diff -r skill-src/xcelium-sim ~/.claude/skills/xcelium-sim` 완전 일치 확인 | ✅ 100% 일치 |

---

## 6. Lessons Learned & Retrospective

### 6.1 What Went Well (Keep)

1. **source truth 감사가 조기에 발견한 문제들** — Plan FR-11에서 "CLAUDE.md를 실제 소스와 대조"하도록 강제한 것이, 낡은 detail들(25→24, stale v3 섹션)을 Do 단계 전에 찾아내 tool-map.md와 CLAUDE.md를 정확히 작성하는 기초가 됨. 프로즈 기반 이관이 아닌 감사 기반 재작성이 결과물 품질을 크게 높임.

2. **Progressive disclosure 구조가 대규모 콘텐츠 관리를 효율화** — 24개 tool을 한 곳(CLAUDE.md)에 쓰지 않고, phase reference(6개) + tool-map(1개)으로 쪼갠 덕분에:
   - skill이 로드될 때 필요한 reference만 메모리에 진입 → 토큰 효율
   - 단일 정본(tool-map.md)에서 관리하고, reference들이 이를 참조 → 일관성 유지
   - 나중에 `/sim` subcommand 추가 시 SKILL.md 상단(Phase 1 부분)은 건드리지 않음 → 구조 안정성

3. **Cross-repo 의존성을 "배포"와 "호출"로 명확히 분리하기** — 초기에 "agent가 호출한다"는 부정확한 표현이 여러 문서에 퍼져 있었는데, 이를 "agent는 install.py로 배포되고, 로컬 세션의 Task가 호출한다"로 정정하면서 다중 reader(skill 내부 Claude + 외부 agent + 설계 문서들)가 일관된 정신 모델을 유지할 수 있게 됨.

### 6.2 What Needs Improvement (Problem)

1. **"현재 상태" 문서는 주기적 대조 감사 필요** — CLAUDE.md는 프로젝트가 시간이 지나며 소스와 drift하는 대표적 사례. tool 이름/개수 같은 hard fact도 계속 갱신 안 되면 여러 산출물(design, skill, agent)에 영향을 미쳐 cross-document consistency가 깨짐. 향후 "감사 주기"를 정책화하면 좋을 것 같음(예: 매 v.release 시마다 또는 3개월마다).

2. **agent 미구현 상태에서의 fallback 텍스트 유지비용** — phase reference마다 "verilog-rtl-debugger를 찾을 수 없으면 Claude가 직접 수행"이라는 fallback 문구를 넣었는데, agent가 완성되면 이 문구들을 다시 찾아서 "agent 호출"로 바꿔야 함. 이 주기를 어떻게 추적할지가 과제.

### 6.3 What to Try Next (Try)

1. **"API 문서" 원칙으로 외부 consumer 자기완결성 보장** — 이번에 phase-2~4 reference가 agent의 runtime Read 대상이 되면서, 세션 종속 표현("위에서 설명했듯이")을 제거하고 각 reference가 독립적으로 이해 가능하도록 설계한 것이 아주 효과적이었음. 향후 다른 공유 문서들도 이 원칙을 적용하면 다중 reader 환경에서 robust할 것 같음.

2. **SKILL.md 내 Phase 1/Phase 2 경계를 명시적으로 유지** — Design에서 "Phase 1 전용 섹션"과 "Phase 2 확장점(HTML 주석)" 마커를 넣은 덕분에, `xcelium-mcp-debug-workflow-v2`가 나중에 들어올 때 상단(Phase 1)을 건드리지 않고 아래(Phase 2 마커)에만 추가할 수 있도록 구조화됨. 이런 "명시적 경계"가 다중 feature 협업을 한결 수월하게 함.

---

## 7. Process Improvement Suggestions

### 7.1 PDCA Process개선

| Phase | 개선 제안 | Expected Benefit |
|-------|---------|------------------|
| Plan | source truth 감사(FR-11) 방법론화 — "이관/재작성 대상 문서는 원본(소스/기존 프로즈 아님)을 1:1 대조해서 새로 작성" | drift 방지, cross-document consistency |
| Design | cross-feature 경계 명시하기(Phase 1/2 마커처럼) — multi-phase design일 때 각 phase 구현 순서 명확화 | 병렬 작업, 의존성 구조화 |
| Do | agent/capability 미구현 상태 fallback 사용법 표준화 | 긴 의존성 체인에서 부분 완성 가능 |
| Check | 외부 reader 자기완결성(API 문서 원칙) 검증 추가 — "이 문서가 원본 세션 없이 읽혀도 이해 가능한가?" | cross-repo 참조 시 robust |

### 7.2 Documentation

| Area | 개선 제안 | Timing |
|------|---------|--------|
| source truth 감사 정책 | 12-month 또는 v.release 주기로 formalize | 향후 관리 계획 수립 |
| cross-repo 배포/호출 용어 정립 | glossary 또는 style guide 문서화 | chip-design-skills와 공동 |

---

## 8. Next Steps

### 8.1 Immediate (이 feature 완료 후 바로)

- [ ] skill-src/README.md에 배포 절차 최종 확인(Checkpoint 6)
- [ ] venezia-fpga 등 다른 RTL 프로젝트 2~3개에서 트리거 테스트
- [ ] 사용자 피드백: skill 콘텐츠 정확성/유용성 검증(§8.3 L3 E2E)

### 8.2 Next PDCA Cycle (별도 features)

| 항목 | 계획 | 소유권 | 예정 시작 |
|------|------|--------|----------|
| **xcelium-mcp-debug-workflow-v2** (Phase 2) | `/sim run`, `/sim analyze`, `/sim debug`, `/sim verify`, `/sim status` subcommand 라우팅 | xcelium-mcp | compound.py 완성 후(2026-Q3) |
| compound.py (Layer 3) | `run_and_check()`, `extract_coverage()` 등 compound operation | xcelium-mcp | 별도 계획 |
| **verilog-rtl-debugger.plan.md** (chip-design-skills) | Phase 2~5 방법론 구현, xcelium-sim phase reference runtime Read | chip-design-skills | 우선순위 TBD |
| **verilog-tb-analyst.plan.md** (chip-design-skills) | Phase 0~1 방법론 구현, xcelium-sim phase reference runtime Read | chip-design-skills | 우선순위 TBD |

### 8.3 Fallback 문구 갱신 (agent 완성 시)

다음 두 agent가 chip-design-skills에 구현되면:
```
1. verilog-rtl-debugger 완성
   → phase-1-analysis.md §1B, phase-4-waveform.md §4A, phase-5-fix-regression.md §5A
     의 fallback 문구를 "Claude가 직접 수행" → "verilog-rtl-debugger agent 호출" 로 갱신

2. verilog-tb-analyst 완성
   → phase-0-discovery.md §0A/0B, phase-1-analysis.md §1A
     의 fallback 문구를 "Claude가 직접 수행" → "verilog-tb-analyst agent 호출" 로 갱신
```

---

## 9. Appendix

### 9.1 도구 정합성 검증 결과 (FR-11)

**2026-07-03 전수 감사 완료**:

```bash
# Source (xcelium-mcp)
$ grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py
→ 24개 함수 확인

# Deliverables
$ wc -l skill-src/xcelium-sim/references/tool-map.md
→ 1056줄, 24개 tool 매핑

# CLAUDE.md
→ "Tool Groups (25 tools)" 오류 수정 → 24개
→ "Repository Structure"의 "server.py, 18 tool definitions" 수정 → "tools/*.py 7 modules, 24 tools distributed"
```

**Cross-document consistency (3-way)**:
- CLAUDE.md: 24개 명시 ✅
- SKILL.md: 트리거 목록 + phase reference 7개 ✅
- tool-map.md: 24개 매핑 ✅

### 9.2 트리거 키워드 검증 (타 skill과 충돌)

2026-07-03 cross-grep 대조:
```
xcelium-sim:
  xcelium ←(unique)
  simvision ←(unique)
  waveform ←(chip-verification과 겹침 없음)
  FAIL 분석 ←(unique)
  checkpoint ←(unique)
  bisect ←(unique)
  regression ←(chip-verification 1건 중복, 그러나 RTL regression 의미로 실제 관련)
  dump_scopes ←(unique)
```

심각도: **Low** (둘 다 actual RTL regression 대상) — 수정 불필요 결정.

---

## 10. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-07-03 | 완료 보고서 초판 — Plan v0.8 + Design v0.1 + Do + Check(98% match rate) 통합 보고. 13개 FR + 10개 Success Criteria 모두 Met. 배포본 검증, agent 위임 지점 명시, 소스 감사 결과 반영. Next Cycle(compound.py/Phase 2, agent 구현) 명확화 | HSLEE |
