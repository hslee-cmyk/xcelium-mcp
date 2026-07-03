# skill-src/ — xcelium-sim skill 소스

`~/.claude/skills/xcelium-sim/`(user-level, RTL 프로젝트 세션에서 사용)로 배포되는 skill의 **git 정본**이다. 이 디렉터리가 정본이고 `~/.claude/skills/xcelium-sim/`는 배포 산출물(사본)이다 — 배포본을 직접 편집하지 말 것(다음 배포 시 덮어써짐).

배경/설계: `docs/01-plan/features/xcelium-mcp-tool-usage-guide.plan.md`, `docs/02-design/features/xcelium-mcp-tool-usage-guide.design.md`(Architecture Option C — Pragmatic Balance).

## 구조

```
skill-src/xcelium-sim/
├── SKILL.md                          # 트리거 + Phase 1/Phase 2 라우팅 스켈레톤
└── references/
    ├── phase-0-discovery.md          # 검증 환경 인프라 분석
    ├── phase-1-analysis.md           # 사전 분석 (dump scope, verilog-rtl-analyst 위임)
    ├── phase-2-simulation.md         # 시뮬레이션 실행 (Batch/Bridge)
    ├── phase-3-triage.md             # 1차 판별 (로그)
    ├── phase-4-waveform.md           # 2차 판별 (waveform CSV, verilog-rtl-debugger 소유)
    ├── phase-5-fix-regression.md     # 수정+regression (coder/reviewer/architect-advisor 위임)
    └── tool-map.md                   # 24개 tool 결정 매트릭스
```

## 배포 절차

```bash
cp -r skill-src/xcelium-sim ~/.claude/skills/
```

### 재배포 체크리스트

- [ ] `skill-src/` 변경 시 **매번** 위 명령 재실행 — 자동 배포 없음(Option C, drift는 수동 관리)
- [ ] `~/.claude/skills/xcelium-sim/`를 직접 편집하지 말 것 — 다음 배포 시 덮어써짐
- [ ] 재배포 후 venezia-fpga 등 소비 프로젝트 세션에서 트리거 동작 재확인

## 이 skill을 다른 repo가 소비하는 방법

`verilog-rtl-debugger` agent(신설 예정, chip-design-skills가 install.py로 user/project-level에 배포 — chip-design-skills 자체가 실행하는 게 아니라 로컬에 설치된 agent가 그 세션에서 동작)가 `references/phase-2~4*.md`를 런타임에 Read해서 Phase 2~4 방법론을 따른다 — 이 skill이 그 agent보다 먼저(최소 병행) 배포되어 있어야 한다. 상세: `xcelium-mcp-debugging-workflow.plan.md` §Agent 위임 구조, `xcelium-mcp-tool-usage-guide.plan.md` §7 Dependencies.

## 검증

- **L1 (내용 정확성)**: `grep -c "@mcp.tool()" src/xcelium_mcp/tools/*.py`가 24와 일치하는지, `tool-map.md`가 이를 전부 커버하는지 주기적으로 재확인 (소스가 바뀌면 tool-map.md도 갱신 필요)
- **L2/L3 (트리거·E2E)**: venezia-fpga 등 실제 소비 프로젝트 세션에서 수동 시나리오로 확인. 상세 시나리오는 Design 문서 §8.3/§8.4.
