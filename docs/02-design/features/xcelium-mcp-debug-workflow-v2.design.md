# xcelium-mcp-debug-workflow-v2 Design Document

> **Summary**: `/sim` Skill(Layer 4) + Backend Compound Operations(Layer 3, `compound.py`) + 독립 CLI(Layer 2) + Hook 자동화(Layer 1, 후행)로 구성된 5-Layer 범용 HW 검증 워크플로우의 기술 설계. Plan §3/§5.1/§5.8이 이미 함수 시그니처·JSON 스키마·Fix Sub-cycle 상태 머신 수준까지 상세히 확정해 두었으므로, 이 문서의 역할은 (1) 남아있던 유일한 미결 아키텍처 축(Backend Interface 강제 수준)을 Checkpoint 3로 확정하고, (2) Plan의 산재된 기술 스펙을 Do 단계가 바로 착수할 수 있는 구현 가이드(파일 구조·순서·테스트 계획)로 재구성하는 것이다.
>
> **Project**: xcelium-mcp
> **Version**: 0.1
> **Author**: HSLEE
> **Date**: 2026-07-22
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-debug-workflow-v2.plan.md](../01-plan/features/xcelium-mcp-debug-workflow-v2.plan.md) (v1.36)

> **N/A 섹션 안내**: 이 feature는 웹앱이 아니라 (a) `xcelium-mcp` Python 패키지에 추가되는 백엔드 조합 계층 + (b) Claude Code user-level skill(`~/.claude/skills/xcelium-sim/`) 확장이다. 템플릿의 §3 Data Model(TS interface/SQL), §4 API Specification(REST), §5 UI/UX, §9 Clean Architecture(Presentation/Application/Domain/Infrastructure), §10 Coding Convention(TS/React)은 원본 그대로 적용 불가 — 각 섹션에서 이 프로젝트(Python + Skill/CLI + JSON 상태 파일)의 실제 산출물에 맞게 대체했다. 형식은 sibling 문서 `xcelium-mcp-tool-usage-guide.design.md`의 적용 패턴을 따른다.

---

## Context Anchor

> Plan 문서(v1.36)에서 그대로 복사.

| Key | Value |
|-----|-------|
| **WHY** | 시뮬레이터별 도구·절차가 제각각이라 표준화된 검증 워크플로우가 없고, 25개 개별 tool을 세션마다 개별 호출해야 해서 trigger가 과다함 |
| **WHO** | xcelium-mcp로 RTL 검증을 수행하는 AI 에이전트 및 엔지니어 (현재 소비 프로젝트: `venezia-fpga`) |
| **RISK** | `compound.py`가 기존 batch/CSV 로직을 재구현하면 이미 검증된 경로(617 tests)와 별개로 새 버그 표면이 생김(§3.4 참조) |
| **SUCCESS** | `/sim verify {test}` 1회 호출로 run→analyze→(debug) 자동 체이닝; 기존 25 tool 전량 하위호환 유지; tool trigger 세션당 60% 감소 |
| **SCOPE** | Phase A-C(Backend 조합 계층 + CLI + Skill) 우선 구현 → 검증 후 Phase D(Hook 자동화) 후행. Backend Interface는 두 번째 backend가 실제로 필요해지기 전까지 YAGNI 후보 — 이 문서 §2.0이 이 결정을 확정한다 |

---

## 1. Overview

### 1.1 Design Goals

1. Plan §3(Backend Interface)·§5.1(sim-state.json)·§5.8(Fix Sub-cycle)이 이미 확정한 함수 시그니처·JSON 스키마·상태 전이를 그대로 구현 대상으로 삼는다 — 이 Design 문서는 새 스펙을 발명하지 않는다.
2. 기존 검증된 경로(`batch_runner.py`/`csv_cache.py`, 617 tests)를 재구현하지 않고 조합(wrap)만 한다(RISK 대응).
3. 이미 완료된 `xcelium-mcp-tool-usage-guide` Phase 1 산출물(`references/phase-0~5.md`, `tool-map.md`, `server-ops.md` 8개 파일)에 subcommand 라우팅만 얹고, 기존 파일 내용은 건드리지 않는다(단, `phase-0-discovery.md` §0A/0B TB frontmatter 참조 지시 추가는 예외 — Plan §4.3).
4. Hook 자동화(Phase D)는 Phase A-C 검증 완료 후 별도 세션으로 미룬다(SCOPE) — 지금 설계는 Phase D의 최종 형태(§6)까지 포함하되 구현 순서에서만 후행시킨다.

### 1.2 Design Principles

- **조합 우선(Composition over reimplementation)**: `compound.py`는 `batch_runner.py`/`csv_cache.py`의 기존 함수를 호출만 한다 — 새 batch 실행/CSV 파싱 로직을 작성하지 않는다.
- **원격/로컬 경계 엄수**: `sim-state.json`과 그 동반 `.md` 문서(`debug.md`/`fix-plan.md`/`fix-design.md`/`fix-review.md`)는 클라이언트(로컬) 파일이고, `compound.py`는 원격 시뮬레이션 서버 패키지다 — 이 경계를 넘는 코드(예: `sim_state.py`를 `src/xcelium_mcp/`에 두는 것)는 만들지 않는다(Plan §3.4/§5.1).
- **YAGNI 우선**: Backend Interface(§3.1)는 코드 수준 추상 클래스가 아니라 이 문서(§2.1 Component Diagram + §4 계약)가 정의하는 **컨벤션**으로 둔다 — 두 번째 backend(vcs-mcp 등)가 실제로 만들어질 때 Protocol/ABC로 승격 검토(Checkpoint 3 확정).
- **문서=정본, 코드=구현**: Fix Sub-cycle의 4개 git-tracked 문서(`debug.md`/`fix-plan.md`/`fix-design.md`/`fix-review.md`)는 append-only 프로즈가 정본이고, `sim-state.json`은 그 문서를 가리키는 포인터+카운터일 뿐이다(Plan §5.1) — 이 원칙을 어기고 JSON에 프로즈를 직접 넣는 구현은 하지 않는다.

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| **Approach** | Backend Interface 비공식(코드 타입 없음), sim_state.py 순수 함수, Hook 스캐폴딩도 안 함 | Backend Interface를 `typing.Protocol`로 강제, `sim_state.py`를 `SimState` 클래스로 캡슐화, Hook까지 한 번에 구현 | Backend Interface는 이 문서(§2.1/§4)의 문서화된 계약, `sim_state.py`는 Plan §5.1 그대로 순수 함수 7개, Hook은 Plan SCOPE대로 후행 |
| **New Files** | 6 (compound.py, cli.py, tools/compound.py, sim_state.py, backend-interface.md, fix-plan-template.md) | 9+ (위 6 + Protocol 모듈, hooks 2개, 배포 스크립트) | 7 (위 6 + hooks 2개는 파일만 만들고 라우팅은 Phase D에서 채움) |
| **Complexity** | Low | High | Medium |
| **Maintainability** | Medium(계약 미강제) | High(강제) | High(문서 강제, 코드 강제 아님) |
| **Effort** | Low | High | Medium |
| **Risk** | High — 2번째 backend가 실제로 생기면 계약 위반을 아무것도 감지 못함 | Low(기술적으로는) — 그러나 FR-07(YAGNI 후보)·SCOPE(Phase D 후행)이 이미 확정한 결정과 정면 충돌하는 **과설계** | Low — Plan의 기존 결정과 100% 정합 |
| **Recommendation** | 시뮬레이터가 영원히 xcelium-mcp 하나뿐이라면 고려 가능하나, 검증되지 않은 가정 | 2번째 backend(vcs-mcp 등) 착수가 실제로 결정된 시점에 재검토 | **선택됨(Checkpoint 3, 2026-07-22)** |

**Selected**: **Option C — Pragmatic Balance** — **Rationale**: Plan의 FR-07("YAGNI 후보, 두 번째 backend 착수 전까지 interface만 정의하고 범용화 자체는 보류 검토")과 SCOPE("Phase D 후행")이 이미 이 선택을 사실상 확정해 두었다. Option B는 기술적으로 더 견고하지만 이미 존재하지 않는 두 번째 backend를 위해 지금 추상화 비용을 지불하는 것이라 이 문서 전반의 YAGNI 원칙(§1.2)과 충돌한다. Option A는 문서 계약조차 없어 향후 vcs-mcp 착수 시 재작업 비용이 더 크다. Option C는 "지금 필요한 만큼만" 만들되, 계약을 코드가 아니라 **이 Design 문서 자체**로 못박아 두어 향후 승격 시 참조점이 되도록 한다.

> 상세 설계는 아래 선택된 Option C를 기준으로 한다.

### 2.1 Component Diagram

> Plan §2 5-Layer 아키텍처를 그대로 구현 대상 구조로 채택.

```
Layer 4: /sim Skill (~/.claude/skills/xcelium-sim/, user-level)
    │  run / analyze / debug / verify / status subcommands
    │  TB frontmatter 파싱, sim-state.json R/W, next-skill 제안, FAIL 분류
    │  Fix Sub-cycle 오케스트레이션(§5.8) — AskUserQuestion 게이트, Task 위임
    │
Layer 3: xcelium-mcp Backend (src/xcelium_mcp/, 원격 시뮬레이션 서버)
    │  compound.py: CompoundResult + run_and_check/analyze_waveform/regression_summary
    │  → batch_runner.py/csv_cache.py 기존 함수 호출(신규 로직 없음)
    │
Layer 2: CLI (xcelium-mcp-cli, 독립 console_script)
    │  argparse run/analyze/regression → compound.py 직접 호출
    │
Layer 1: Hook (Claude Code plugin, Phase D 후행)
    │  PostToolUse(sim-post-compound.js) / UserPromptSubmit(sim-prompt-detect.js)
    │
Layer 0: 기존 25개 MCP tool (변경 없음, 하위호환 유지)
```

### 2.2 Data Flow

```
사용자: "/sim verify TOP015"
  → Skill(L4)이 sim-state.json 읽어 현재 phase 확인
  → compound tool(L3, MCP 경유) 호출 → run_and_check() 실행
      → batch_runner.run_batch_single() → csv_cache.extract()
      → CompoundResult{status, log_summary, dump_path, csv_path, details} 반환
  → Skill이 CompoundResult를 로컬 sim-state.json에 기록(원격 Backend는 파일에 관여하지 않음)
  → FAIL이면 debug 단계 진입 → verilog-rtl-debugger 위임 → Fix Sub-cycle(§5.8, Plan 참조)
  → clean 판정 후 Step 1(run)부터 재진입
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `tools/compound.py` (MCP tool) | `compound.py` | MCP 경유 호출 시 compound 로직 재사용 |
| `compound.py` | `batch_runner.py`, `csv_cache.py` (기존, 변경 없음) | 조합만 — 재구현 금지(§1.2) |
| `cli.py` | `compound.py` | MCP 경유 없이 직접 호출(AI 불필요 경로) |
| `~/.claude/skills/xcelium-sim/scripts/sim_state.py` | 없음(순수 파일 I/O) | sim-state.json + 동반 `.md` 문서 CRUD |
| SKILL.md subcommand 라우팅 | `references/phase-0~5.md`(Phase 1 완료), `sim_state.py`, MCP compound tool 3개 | 워크플로우 오케스트레이션 |
| Fix Sub-cycle(§5.8, Plan 참조) | `verilog-rtl-debugger`/`verilog-rtl-coder`/`verilog-rtl-reviewer`/`verilog-rtl-prover`/`verilog-tb-coder`/`verilog-tb-reviewer`/`verilog-rtl-architect-advisor`(전부 chip-design-skills 소유, cross-repo) | 근본원인 조사~구현~리뷰 |

---

## 3. 데이터 모델 (§3 Data Model 대체)

> TypeScript interface/SQL 대신, 이 프로젝트의 실제 데이터 산출물(Python dataclass + JSON 상태 파일 + git-tracked MD 문서)을 정의한다. 전체 스키마는 Plan §3.2/§5.1이 정본이며, 여기서는 구현 시 참조할 요약만 둔다.

### 3.1 `CompoundResult` (Plan §3.2 원본)

```python
@dataclass
class CompoundResult:
    status: str          # "PASS" | "FAIL" | "ERROR" | "PARTIAL"
    log_summary: str
    dump_path: str
    csv_path: str
    details: dict
    def to_cli_output(self) -> str: ...
    def to_mcp_output(self) -> str: ...
```

### 3.2 `sim-state.json` 스키마

Plan §5.1의 JSON 예시와 `origin_chain.{run,analyze,debug,fix_plan,fix_design,fix_implement,fix_review}` 필드 전체를 그대로 구현 대상으로 삼는다 — 이 문서에서 재정의하지 않는다(Plan §5.1 참조, 중복 방지 원칙).

### 3.3 git-tracked 동반 문서 (Plan §5.8 참조)

| 문서 | 위치 | 갱신 방식 | 템플릿 |
|------|------|-----------|--------|
| `debug.md` | `.ai/sim-state/{test}/` | append-only | 없음(자유 프로즈, `## Iteration N` 헤더) |
| `fix-plan.md` | 〃 | 개정(덮어씀) | **신규** `references/fix-plan-template.md` |
| `fix-design.md` | 〃 (ARCH 판정 시만) | 개정 | 기존 `adr-template.md` 재사용(신규 템플릿 없음) |
| `fix-review.md` | 〃 | append-only | 없음(reviewer/prover 자체 리포트 형식 재사용) |

### 3.4 `SimState` 관계도

```
sim-state.json (JSON, 상태+포인터)
  └─ tests.{test}.origin_chain.{debug,fix_plan,fix_design,fix_review}.path
        └──▶ .ai/sim-state/{test}/{debug,fix-plan,fix-design,fix-review}.md (git-tracked, 정본)
```

---

## 4. Backend/Skill 계약 (§4 API Specification 대체)

> REST 엔드포인트 대신, Layer 3(compound operations)·Layer 4(Skill subcommand)·`sim_state.py` 함수 계약을 정의한다. Option C(§2.0)에 따라 이 계약은 **문서 수준**이며 Python 타입으로 강제하지 않는다 — 이 표 자체가 미래 두 번째 backend가 지켜야 할 "Backend Interface"다.

### 4.1 Compound Operations (Layer 3, Plan §3.1)

| Operation | 입력 | 출력 | 내부 구현 |
|-----------|------|------|-----------|
| `run_and_check` | test_name, csv_signals, csv_mode, find_condition | `CompoundResult` | `batch_runner.run_batch_single` → log 확인 → (선택)`csv_cache.extract` |
| `analyze_waveform` | dump_path, signals, find_conditions, range | `CompoundResult` | `csv_cache.extract` → `csv_cache.bisect_signal_dump` |
| `regression_summary` | test_list, csv_on_fail, csv_signals | `CompoundResult` | `batch_runner.run_batch_regression` → per-test 로그 → FAIL 시 CSV |

### 4.2 `sim_state.py` 함수 계약 (Plan §5.1 원본, 7개)

`append_debug_note` / `write_fix_plan` / `approve_fix_plan` / `supersede_fix_plan` / `hold_fix_plan` / `record_fix_implement` / `append_fix_review_note` — 시그니처와 각 함수의 상태 전이 규칙은 Plan §5.1에 정의된 docstring이 정본이다(중복 방지, 여기서 재작성하지 않음).

### 4.3 `/sim` Skill Subcommand (Layer 4, Plan §4.1/§4.2)

| Subcommand | 트리거 | 체이닝 |
|------------|--------|--------|
| `/sim run {test}` | 단일 실행 | PASS→analyze 제안 / FAIL→analyze |
| `/sim analyze {test}` | 결과 분석 | 원인 특정→debug / 불확정→추가 signal 분석 |
| `/sim debug {test}` | FAIL 원인 추적 + Fix Sub-cycle 전체(§5.8) | fix-review clean→run 재진입 |
| `/sim verify {test}` | run→analyze→(FAIL시)debug 자동 체이닝 | Fix Sub-cycle 완료까지 |
| `/sim status` | 현재 phase/origin_chain on-demand 조회 | — |

---

## 5. CLI/Skill UX (§5 UI/UX 대체)

> 화면 UI가 없으므로, 이 절은 CLI 출력 형식과 Skill 응답 패턴을 정의한다.

### 5.1 CLI 출력 (Layer 2)

```
$ xcelium-mcp-cli run TOP015
[RUN] TOP015 -- PASS (0 errors, 1.2s)
  dump: ~/.../TOP015.shm
```

`CompoundResult.to_cli_output()`이 이 `[TAG] {test} -- {status} ({detail})` 형식을 담당한다(Plan §3.2).

### 5.2 Skill 응답 패턴 (Layer 4)

- **정상 진행**: 매 subcommand 호출 후 결과 요약 + next-skill 제안(§4.4 next-skill-map, Plan 참조).
- **Fix Sub-cycle 승인 게이트**: AskUserQuestion 3-way(승인/수정 요청/보류) — Plan §5.8 "1) fix-plan" 참조.
- **구현 주체=사람일 때 findings 전달**: fix-review에서 문제 발견 시, Skill이 요약을 채팅에 직접 출력하는 동시에 `fix-review.md`에 append("기록=출력" 동일성, Plan §5.8 "4) fix-review" 참조) — UX 관점에서는 이게 이 Skill의 유일한 "알림" 성격 출력이다.

### 5.3 Page UI Checklist 대체 — Subcommand 출력 체크리스트

- [ ] `/sim run`: PASS/FAIL 배지 + dump_path + log 요약 1줄
- [ ] `/sim analyze`: FAIL 유형 분류(§5.4, Plan 참조) + CSV 경로 + 이상 신호 목록
- [ ] `/sim debug`: 근본원인 요약 + fix-plan.md 경로 + 승인 게이트 질문
- [ ] `/sim status`: phase 배지 + origin_chain 요약 표(Plan §5.1 JSON 예시 형태)

---

## 6. Error Handling

### 6.1 Error Code Definition (CompoundResult.status)

| Status | 의미 | 처리 |
|--------|------|------|
| `PASS` | 정상 통과 | next-skill: regression 제안 |
| `FAIL` | 시뮬레이션 실패(assertion/mismatch) | `/sim analyze`로 체이닝 |
| `ERROR` | 실행 자체 실패(EDA 환경변수 미설정 등) | 재시도 안내(Plan §11 리스크 "CLI EDA 환경변수 미설정" 참조) |
| `PARTIAL` | 부분 실패(regression 중 일부 tool 실패) | 실패 단계 명시 + 부분 결과 반환(Plan §11 참조) |

### 6.2 예외 전파

- `TclBridge.execute()`가 던지는 `TclError`는 `compound.py`가 catch해 `CompoundResult(status="ERROR", ...)`로 변환 — 원시 예외를 Skill/CLI까지 올리지 않는다(기존 25 tool의 `execute_safe()` 관례와 일치, CLAUDE.md 참조).
- `run 0-b단계`의 git diff 감지 실패(사람이 fix-plan.md 선언 범위 밖 파일을 고친 경우) — Plan §11 리스크 표 "사람 구현 완료 감지 실패" 참조, 이 Design에서 별도 예외 처리 코드를 추가하지 않는다(YAGNI, 확정 사항).

---

## 7. Security Considerations

- 이 feature는 로컬 개발자 도구(Claude Code + 원격 시뮬레이션 서버 SSH stdio)이며 외부 사용자 입력이나 인증이 필요한 웹 API가 아니다 — 템플릿의 XSS/SQL Injection/Rate Limiting 항목은 **N/A**.
- 유일하게 실질적인 항목: `sim-state.json`/git-tracked 문서(`fix-plan.md` 등)에 시크릿이 기록되지 않도록 — 이미 CLAUDE.md 전역 규칙("커밋 전 시크릿 확인")이 커버, 이 feature 전용 추가 조치 없음.

---

## 8. Test Plan

> 이 프로젝트는 Playwright/curl이 아니라 **pytest + MockTclServer**(CLAUDE.md 참조)로 테스트한다 — 템플릿의 L1(API)/L2(UI)/L3(E2E) 명칭은 유지하되 도구를 이 프로젝트 컨벤션으로 교체했다. Plan §8.2의 `E-1`~`E-14`는 실제 SimVision/원격 서버가 있어야 확인 가능한 **수동 검증 시나리오**이므로 별도 절(§8.4)로 분리했다.

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1: Compound 함수 단위 테스트 | `compound.py`의 3개 함수 | pytest + MockTclServer | Do (Phase A) |
| L2: CLI 테스트 | `cli.py` argparse 진입점 | pytest(subprocess 또는 직접 함수 호출) | Do (Phase B) |
| L3: `sim_state.py` CRUD 테스트 | 7개 함수 + JSON/MD 파일 I/O | pytest(`tmp_path` fixture) | Do (Phase C) |
| 수동 E2E | Fix Sub-cycle·Hook·Skill 전체 흐름 | 실제 SimVision + Claude Code 세션 | §8.4 참조 |

### 8.2 L1: Compound 함수 테스트 시나리오

| # | 함수 | 시나리오 | 기대 결과 |
|---|------|----------|-----------|
| 1 | `run_and_check` | PASS 케이스(mock batch 성공) | `CompoundResult.status == "PASS"`, `dump_path` 채워짐 |
| 2 | `run_and_check` | FAIL 케이스(mock 로그에 error) | `status == "FAIL"`, `log_summary`에 error count 반영 |
| 3 | `run_and_check` | EDA 환경변수 미설정(mock 실행 실패) | `status == "ERROR"`, 원시 예외 노출 없음 |
| 4 | `analyze_waveform` | 다중 조건 검색 | `details`에 각 조건별 매칭 결과 |
| 5 | `regression_summary` | 일부 테스트 FAIL | `status == "PARTIAL"`, `details.fail_tests` 목록 정확 |
| 6 | 전체 | `batch_runner`/`csv_cache` 함수가 실제로 호출되는지(mock.assert_called) | 재구현이 아니라 조합임을 회귀로 고정(RISK 대응) |

### 8.3 L2/L3 테스트 시나리오

| # | 대상 | 시나리오 | 기대 결과 |
|---|------|----------|-----------|
| 1 | `cli.py` | `xcelium-mcp-cli run TOP015` | exit code 0(PASS)/1(FAIL), stdout이 `to_cli_output()` 형식 |
| 2 | `cli.py` | `server.py` import 없이 독립 실행되는지 | sys.argv 분기 코드 자체가 없음을 정적 확인(§7.1 결정 회귀) |
| 3 | `sim_state.py` | `append_debug_note` 연속 2회 호출 | `debug.md`에 `## Iteration 1`/`## Iteration 2` 헤더로 append, 덮어쓰지 않음 |
| 4 | `sim_state.py` | `write_fix_plan` → `approve_fix_plan` | `origin_chain.fix_plan.status`: `pending`→`approved`, `approved_at` 채워짐 |
| 5 | `sim_state.py` | `record_fix_implement(implementer="human")` → `append_fix_review_note(verdict="issues_found")` | phase가 `fix-implement`로 복귀, `revision_count` +1 |
| 6 | `sim_state.py` | `supersede_fix_plan` | `fix_plan.status`만 `superseded`, `debug.md`는 건드리지 않음(회귀 고정, Plan §5.1 핵심 설계) |

### 8.4 수동 E2E 검증 (Plan §8.2 E-1~E-14, 실제 sim-server 필요)

Fix Sub-cycle 전체(승인 게이트, AI/사람 구현 2-way, fix-review RTL 2단/TB 1단 판정, `fix_target` 분기, RTL+TB 동시 수정 분리), Hook 자동화, 기존 25 tool 하위호환 — 전체 목록과 검증 절차는 Plan §8.2를 그대로 따른다(중복 방지, 여기서 재작성하지 않음). 이 시나리오들은 자동화된 pytest 스위트가 아니라 실제 SimVision 세션에서 수동으로 확인한다.

### 8.5 Seed / Fixture 요구사항

`MockTclServer`(기존, `tests/` 하위)를 그대로 재사용 — 이 feature가 새 mock 인프라를 추가하지 않는다. `sim_state.py` 테스트는 `tmp_path`에 매 테스트마다 독립된 `sim-state.json` + `.ai/sim-state/{test}/` 디렉터리를 생성해 격리한다.

---

## 9. 계층 구조 (§9 Clean Architecture 대체)

> 이 프로젝트는 Presentation/Application/Domain/Infrastructure(웹앱 4계층)가 아니라 Plan §2가 정의한 **5-Layer 검증 프레임워크**를 이미 자체 계층 모델로 갖고 있다 — 여기서는 그 모델을 이 feature의 실제 컴포넌트에 매핑한다.

### 9.1 Layer ↔ 파일 매핑

| Layer | 책임 | 위치 |
|-------|------|------|
| L4 Skill | 워크플로우 오케스트레이션, Fix Sub-cycle 게이트 | `~/.claude/skills/xcelium-sim/SKILL.md`, `scripts/sim_state.py` |
| L3 Backend | Compound operation 실행 | `xcelium-mcp/src/xcelium_mcp/compound.py`, `tools/compound.py` |
| L2 CLI | AI 없는 직접 실행 | `xcelium-mcp/src/xcelium_mcp/cli.py` |
| L1 Hook | 자동 phase 전환/트리거(후행) | `~/.claude/skills/xcelium-sim/hooks/*.js` |
| L0 개별 tool | 세밀 제어(bridge mode 등) | 기존 25개 `@mcp.tool()` (변경 없음) |

### 9.2 의존 방향

```
L4(Skill) → L3(Backend, MCP 경유) ; L4(Skill) → L2 없음(CLI는 L4와 독립적인 별도 진입점)
L2(CLI) → L3(Backend, 직접 import)
L1(Hook) → L4 상태 파일(sim-state.json)만 읽음, L3/L2를 직접 호출하지 않음
규칙: L3는 L4/L1을 모른다(순수 조합 계층) — L4가 L3를 소비하는 방향만 존재, 역방향 없음
```

### 9.3 Import 규칙

| From | Can Import | Cannot Import |
|------|-----------|----------------|
| `compound.py` (L3) | `batch_runner.py`, `csv_cache.py` | Skill/Hook 관련 모듈(로컬 파일이라 애초에 import 불가) |
| `cli.py` (L2) | `compound.py` | `server.py`(sys.argv 분기 없음, §7.1 확정) |
| `sim_state.py` (L4, 클라이언트 로컬) | 표준 라이브러리(json/pathlib)만 | `xcelium_mcp.*`(원격 패키지, 물리적으로 import 불가) |

---

## 10. Coding Convention Reference

> CLAUDE.md의 기존 Python 컨벤션을 그대로 따른다 — 이 feature가 새 컨벤션을 발명하지 않는다.

### 10.1 Naming

| 대상 | 규칙 | 예 |
|------|------|-----|
| 함수/모듈 | snake_case | `run_and_check`, `append_debug_note`, `compound.py` |
| 클래스/dataclass | PascalCase | `CompoundResult` |
| MCP tool 함수 | `async def` + `@mcp.tool()` 데코레이터 | 기존 25개 tool과 동일 컨벤션(CLAUDE.md 참조) |
| 상수 | UPPER_SNAKE_CASE | (해당 시) |

### 10.2 예외/에러 처리 컨벤션

- `TclBridge.execute()`는 `TclError` 발생, `execute_safe()`는 `TclResponse` 반환 — `compound.py`는 기존 tool처럼 `execute_safe()` 계열을 우선 사용해 원시 예외가 Skill/CLI로 새지 않게 한다(CLAUDE.md 참조).
- Docstring은 Plan §5.1이 이미 보여준 형식(한 줄 계약 + 세부 규칙 prose)을 따른다 — Google/NumPy 스타일 강제 없음, 기존 저장소 관례 유지.

### 10.3 이 Feature 전용 컨벤션

| 항목 | 적용 |
|------|------|
| Backend Interface 강제 방식 | 코드 타입 아님 — 이 문서 §4가 계약(Option C, Checkpoint 3) |
| `sim_state.py` 구조 | 클래스 아님 — Plan §5.1 그대로 순수 함수 7개(Option C) |
| 파일 위치 원칙 | 원격 실행 코드(`compound.py`)는 `src/xcelium_mcp/`, 클라이언트 로컬 코드(`sim_state.py`)는 `~/.claude/skills/xcelium-sim/scripts/`(§9.3 Import 규칙 참조) |

---

## 11. Implementation Guide

### 11.1 File Structure

> Plan §8.1 파일 변경 목록과 동일 — 중복 방지를 위해 여기서는 트리 형태로만 재구성한다(상세 설명은 Plan §8.1 참조).

```
xcelium-mcp/
├── src/xcelium_mcp/
│   ├── compound.py                 [신규] CompoundResult + 3 compound 함수
│   ├── cli.py                      [신규] 독립 console_script
│   ├── tools/compound.py           [신규] MCP tool 3개
│   ├── server.py                   [수정] register() 추가만
│   └── pyproject.toml              [수정] xcelium-mcp-cli script 등록
└── (venezia-fpga 등 소비 프로젝트)/
    └── CLAUDE.md                   [수정] /sim skill 안내로 간소화

~/.claude/skills/xcelium-sim/       (user-level, Phase 1에서 이미 생성됨)
├── SKILL.md                        [수정] Phase 2 확장점에 subcommand 라우팅 추가
├── scripts/sim_state.py            [신규] sim-state.json CRUD + phase 전이
├── references/
│   ├── backend-interface.md        [신규]
│   ├── fix-plan-template.md        [신규]
│   └── phase-0-discovery.md        [수정] §0A/0B TB frontmatter 참조 지시 추가
└── hooks/*.js                      [신규, Phase D 후행] PostToolUse/UserPromptSubmit

{project}/.ai/sim-state/{test}/     (RTL 프로젝트 로컬, git-tracked)
├── debug.md / fix-plan.md / fix-design.md(조건부) / fix-review.md
```

### 11.2 Implementation Order

> Plan §8.2 Phase A~E를 그대로 채택(중복 방지) — 아래는 체크리스트 형태 요약.

1. [ ] **Phase A** — `CompoundResult` + `run_and_check`/`analyze_waveform`/`regression_summary` (기존 함수 조합만)
2. [ ] **Phase B** — `cli.py` + `pyproject.toml` script 등록 + `tools/compound.py` + `server.py` register()
3. [ ] **Phase C** — SKILL.md subcommand 라우팅 + `sim_state.py` + `backend-interface.md`/`fix-plan-template.md` + `phase-0-discovery.md` 수정 + TB 분석서 backfill
4. [ ] **Phase D**(후행) — Hook 2개(`sim-post-compound.js`/`sim-prompt-detect.js`)
5. [ ] **Phase E** — CLAUDE.md 간소화 + E-1~E-14 수동 검증(§8.4)

### 11.3 Session Guide

#### Module Map

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|--------------|:---------------:|
| Compound Backend | `module-1` | Phase A: CompoundResult + 3 함수 + pytest L1 | 30-40 |
| CLI + MCP Tool | `module-2` | Phase B: cli.py + tools/compound.py + server.py + pytest L2 | 25-35 |
| Skill + sim_state.py | `module-3` | Phase C: SKILL.md 라우팅 + sim_state.py + reference 2종 + pytest L3 | 35-45 |
| Hook 자동화 | `module-4` (후행) | Phase D: Hook 2개 — Phase A-C 검증 완료 후 별도 세션 | 15-20 |
| CLAUDE.md + 수동 검증 | `module-5` | Phase E: CLAUDE.md 간소화 + E-1~E-14 수동 e2e | 20-30 |

#### Recommended Session Plan

| Session | Phase | Scope | Turns |
|---------|-------|-------|:-----:|
| Session 1 | Plan + Design | 전체(완료) | — |
| Session 2 | Do | `--scope module-1` | 30-40 |
| Session 3 | Do | `--scope module-2` | 25-35 |
| Session 4 | Do | `--scope module-3` | 35-45 |
| Session 5 | Do + Check | `--scope module-5`(module-4는 Check 통과 후 별도) | 30-40 |
| Session 6 | Do(후행) + Report | `--scope module-4` + 완료 보고 | 20-30 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-22 | 초안 — Checkpoint 3(Option C 선택) 반영, Plan v1.36 기술 스펙을 구현 가이드로 재구성 | HSLEE |
