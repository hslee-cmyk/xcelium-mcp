# xcelium-mcp-tool-usage-guide — Design-Implementation Gap Analysis

- **Plan**: `docs/01-plan/features/xcelium-mcp-tool-usage-guide.plan.md` (v0.7 → 본문은 v0.8까지 반영, FR-13 추가)
- **Design**: `docs/02-design/features/xcelium-mcp-tool-usage-guide.design.md` (v0.1)
- **분석일**: 2026-07-03 (gap-detector 정적 분석 + parent 수동 byte-diff 보완)
- **Runtime Verification (L1/L2/L3)**: N/A — 이 feature는 API/UI가 없는 Claude Code skill 산출물

## Addendum (2026-07-03, Report 이전 추가 반영)

Report 진행 직전, Plan 문서에 **FR-13**(`verilog-tb-analyst` agent 위임 — TB 분석서 Phase 0A/0B/1A의 위임 비대칭 해소)이 추가됨. 아래 본문 Match Rate(98%)는 FR-13 이전 기준이며, FR-13은 별도로 구현·검증 완료:

- `phase-0-discovery.md`(§0A/0B 위임 + fallback + 위임표), `phase-1-analysis.md`(§1A 위임 + fallback + 위임표 확장), `SKILL.md`(단계 5에 Phase 0 추가) 반영 완료, 배포본과 byte-diff 일치 확인.
- 구현 중 **잘못된 가정 발견·수정**: skill-src 및 관련 plan 문서 곳곳에 "agent(chip-design-skills)가/를 호출"이라는 부정확한 표현이 있었음 — chip-design-skills는 install.py로 agent를 user/project-level에 **배포**만 할 뿐 호출 주체가 아님(실제 호출은 로컬 세션의 Task 도구). 전수 검색해 skill-src 6개 파일 + `xcelium-mcp-debug-workflow-v2.plan.md` 본문 1곳 수정(Version History 등 과거 로그는 보존).
- `pdca-status.json` requirements에 FR-13 추가 반영.
- Match Rate 재계산 없음 — FR-13은 명확한 구현(Not Met 없음)이라 98%→변동 없거나 소폭 상승으로 판단, Report에서 통합 서술.

## Context Anchor

> Design 문서에서 복사.

| Key | Value |
|-----|-------|
| **WHY** | 24개 tool 사용법이 CLAUDE.md에만 있어 실제 RTL 프로젝트 세션엔 로드 안 됨 |
| **WHO** | xcelium-mcp를 쓰는 모든 RTL 프로젝트 AI (현재: venezia-fpga) |
| **RISK** | 트리거 과다 매칭, user-level 재배포 깜박 |
| **SUCCESS** | 키워드 감지 시 자동 로드, phase reference에 구체적 tool 예시, CLAUDE.md ≤15줄 |
| **SCOPE** | Phase 1(이 문서)만 — subcommand 라우팅은 Phase 2가 별도 진행 |

## 전략적 정합성 확인

- PRD 없음(§Related Documents) — PRD 대조 skip.
- Plan 핵심 문제("CLAUDE.md 프로즈만으론 RTL 프로젝트 세션에 안 로드됨")를 구현이 실제로 해결: user-level `~/.claude/skills/xcelium-sim/` 배포 완료, 트리거 키워드 명시.
- Design 핵심 결정(Architecture Option C — git 정본 `skill-src/` + 수동 배포) 그대로 구현됨. 이탈 없음.

## Overall Match Rate

| Category | Score | 근거 |
|----------|:-----:|------|
| Structural Match | **100%** | Design §11.1 File Structure 8개 파일 전부 존재 + `diff -r skill-src/xcelium-sim ~/.claude/skills/xcelium-sim` 완전 일치 확인(2026-07-03) |
| Functional Depth | 98% | tool-map.md 24/24 tool 커버(소스 실측 일치), God node 3개 상세화, v5.2 파라미터 반영, 6개 phase reference 전부 공통 포맷 준수, FR-12 agent 위임 전 phase 반영 |
| Contract 일치 | 100% | CLAUDE.md ↔ SKILL.md ↔ tool-map.md tool 개수(24) 3-way 일관, stale 섹션("Tool Groups 25", "v3 Improvement Plan") 제거 확인 |
| Intent Match | 100% | Definition of Done 5/5 Met, Quality Criteria 5/5 Met — 2026-07-03 사용자가 venezia-fpga 세션에서 DoD-2(라이브 트리거) 최종 확인 완료 |
| Behavioral Completeness | 90% | fallback 문구, 오탐 방지 기준, agent 자기완결성, 배포 체크리스트 전부 present |

**Overall Match Rate: 98%** (2026-07-03 최종 갱신 — 사용자가 venezia-fpga 세션에서 트리거 실동작을 직접 확인, 마지막 Not Met 항목 해소)

## Plan §4.1 Definition of Done

| # | 항목 | 판정 | 근거 |
|---|------|:----:|------|
| 1 | SKILL.md + references 7개 작성 완료 | ✅ Met | skill-src/deployed 8파일 전부 존재·완결 |
| 2 | venezia-fpga 세션 자동 트리거 수동 확인 | ✅ Met | 2026-07-03 사용자가 venezia-fpga 세션에서 실제 트리거 확인 완료 |
| 3 | CLAUDE.md Debugging Workflow ≤15줄 축소 | ✅ Met | 실질 콘텐츠 ~9줄 |
| 4 | 6-phase 내용 유실 없이 이관 | ✅ Met | phase-0~5에 캐시규칙/tier/sentinel/bisect 등 상세 이관 확인 |
| 5 | 정본↔배포본 동기화 절차 문서화(FR-10) | ✅ Met | `skill-src/README.md` + Design §11.2 |

## Plan §4.2 Quality Criteria

| # | 항목 | 판정 | 근거 |
|---|------|:----:|------|
| 1 | tool-map.md 24개 tool 전체 커버 | ✅ Met | 소스 24개 함수명 전부 매핑 |
| 2 | 각 phase reference ≥1 구체적 tool 예시 | ✅ Met | phase-0~5 모두 "Tool 예시" 코드블록 보유 |
| 3 | 타 skill과 트리거 키워드 중복 최소화 | ✅ Met | 2026-07-03 cross-grep 완료(7개 skill 대조): "regression" 1건이 `chip-verification`과 중복(나머지 12개는 중복 없음). 둘 다 RTL regression과 실제 관련 있어 심각도 낮음, 수정 불필요로 판단·확정 |
| 4 | CLAUDE.md tool 이름·개수·구조 100% 소스 일치(FR-11) | ✅ Met | 24개 일치, stale 섹션 전부 제거 |
| 5 | phase-2~4 자기완결적(외부 agent 소비 가능) | ✅ Met | 세션종속 표현 없음, agent 위임 지점 명시 |

## Gap 목록

### 전부 해결됨 — Gap 없음
| 항목 | 해결 내역 |
|------|----------|
| venezia-fpga 세션 자동 트리거 수동 확인 | ✅ 2026-07-03 사용자가 실제 세션에서 트리거 확인 완료 |
| 타 skill 트리거 키워드 중복 최소화 검증 | ✅ 2026-07-03 cross-grep 완료, "regression" 1건 중복(chip-verification) 발견, 심각도 낮아 수정 불필요로 확정 |

### Minor — 해결됨
| 항목 | 상태 |
|------|:----:|
| 배포본 8파일 전체 byte-diff | ✅ 2026-07-03 `diff -r`로 완전 일치 확인, Structural 100%로 갱신 |

### Changed
없음 — Design 스펙과 구현 간 의미적 이탈 없음.

## 결론

90% 게이트 통과(98%, Gap 없음). Checkpoint 5(2026-07-03)에서 "지금 모두 수정" 선택 후 즉시 처리 가능한 항목(배포본 byte-diff, 타 skill 키워드 대조)을 이 세션에서 해결했고, 유일하게 남았던 DoD-2(venezia-fpga 라이브 세션 트리거 확인)도 사용자가 직접 확인 완료. Report 진행 가능.
