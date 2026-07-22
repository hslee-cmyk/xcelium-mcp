---
name: xcelium-sim
description: |
  xcelium-mcp MCP tool(25개)을 phase별로 언제·어떤 파라미터로 쓸지 안내. RTL 시뮬레이션 디버깅
  워크플로우(Phase 0 인프라 분석~Phase 5 수정+regression)를 단계별로 가이드.
  `/sim run|analyze|debug|verify|status` subcommand로 run→analyze→debug 자동 체이닝도 제공.
  트리거: xcelium, simvision, waveform, FAIL 분석, 시뮬레이션, debugging, 디버깅, CSV,
    checkpoint, bisect, regression, dump_scopes, dump_depth, 재기동, supervisor,
    연결 안 됨, 최신 코드 반영 안 됨, MCP 응답 없음.
argument-hint: "[run|analyze|debug|verify|status] [test_name] [--regression|--bridge]"
user-invocable: true
hooks:
  PostToolUse:
    - matcher: "mcp__xcelium-mcp__sim_run_and_check|mcp__xcelium-mcp__sim_analyze_waveform|mcp__xcelium-mcp__sim_regression_summary"
      hooks:
        - type: command
          command: "python3 ~/.claude/skills/xcelium-sim/hooks/sim_post_compound.py"
  UserPromptSubmit:
    - hooks:
        - type: command
          command: "python3 ~/.claude/skills/xcelium-sim/hooks/sim_prompt_detect.py"
---

# xcelium-sim

xcelium-mcp(Cadence Xcelium/SimVision MCP 서버)의 25개 tool을 RTL 디버깅 workflow의 phase별로 언제·어떻게 쓸지 안내한다. `~/.claude/skills/xcelium-sim/`(user-level)에 배포되어, xcelium-mcp를 사용하는 모든 RTL 프로젝트 세션(venezia-fpga 등)에서 동작한다.

> ⚠️ **세션을 끝내기 전에 항상 확인**: `connect_simulator`/`sim_bridge_run`(bridge 모드)으로 붙인
> xmsim/SimVision은 MCP worker의 자식 프로세스가 아니다 — 세션 종료 시 `sim_disconnect(action="shutdown", target="all")`을
> 명시적으로 호출하지 않으면 host에 프로세스가 무기한 방치되어 disk를 소진할 수 있다(실제 발생 이력 있음).
> **Phase 0~4 중 어디서 대화가 끝나든(Phase 5까지 못 가더라도) 이 확인은 생략하지 않는다.**
> 상세: `references/phase-5-fix-regression.md` §5F. batch/regression 모드(`sim_batch_run`/`sim_regression`)는
> 자체 정리 로직이 있어 이 확인 대상이 아니다.

## Phase 1 — Tool 사용법 가이드 (이 문서 소관)

이 skill은 6-phase 디버깅 workflow를 phase별 reference로 안내한다. 관련 키워드(위 트리거 목록)가 대화에 등장하면, 현재 상황(로그/dump 유무 등)을 보고 해당 phase reference를 로드한다.

| Phase | Reference | 내용 |
|-------|-----------|------|
| Phase 0 | `references/phase-0-discovery.md` | 검증 환경 인프라 분석(TB 캐시, 공유 컴포넌트) |
| Phase 1 | `references/phase-1-analysis.md` | 사전 분석(캐시 참조, RTL 분석서, dump scope — `dump_scopes` v5.2 포함) |
| Phase 2 | `references/phase-2-simulation.md` | 시뮬레이션 실행(Batch/Bridge, sentinel 중단) |
| Phase 3 | `references/phase-3-triage.md` | 1차 판별(로그 기반) |
| Phase 4 | `references/phase-4-waveform.md` | 2차 판별(waveform CSV, bisect, FSM 전이 대조) |
| Phase 5 | `references/phase-5-fix-regression.md` | 수정 + Regression + 문서 갱신 + **세션 종료 시뮬레이션 정리(5F, 필수)** |
| — | `references/tool-map.md` | 25개 tool 전체 결정 매트릭스 (모든 phase에서 참조) |
| Ops | `references/server-ops.md` | 원격 supervisor 코드 최신 반영 확인 + 재기동 (tool 응답 이상/재기동 필요 시 phase와 무관하게 참조) |

### 사용 절차

1. 트리거 키워드 감지 시 이 SKILL.md 로드
2. 대화 맥락(로그 존재 여부, dump 존재 여부, 이미 알려진 판별 신호 등)으로 현재 phase 판단
3. 해당 `references/phase-N-*.md` 로드 → 절차 확인
4. 구체적 tool 호출/파라미터가 필요하면 `references/tool-map.md` 참조
5. Phase 0/1/4/5에서는 `verilog-tb-analyst`/`verilog-rtl-debugger` agent(chip-design-skills가 install.py로 user/project-level에 배포) 위임 여부를 각 reference의 "agent 위임" 절에서 확인 — 로컬에 설치돼 있으면 Task로 호출, 없으면 Claude가 직접 수행(각 reference의 fallback 문구 참조)

### 트리거 판단 기준 (오탐 방지)

- xcelium-mcp/venezia-fpga와 무관한 프로젝트의 일상 대화에서는 로드하지 않는다 — "테스트" 같은 범용어 단독으로는 트리거하지 않고, xcelium/시뮬레이션 특화 키워드(위 목록)가 명시적으로 나와야 한다.
- 다른 skill(verilog-rtl, chip-verification 등)과 동시 활성화될 수 있다 — 이 skill은 tool 사용법에, 그쪽은 RTL 설계/검증 방법론에 집중한다.

## Phase 2 — Subcommand 라우팅

`/sim` subcommand는 위 Phase 1의 phase reference 판단 로직 위에, "지금 어떤 phase reference를 자동으로 골라 로드할지"를 결정하는 라우팅 계층이다 — Phase 1 reference 8개는 그대로 재사용하고, 새로 추가되는 산출물은 `scripts/sim_state.py`(상태 CRUD) + `references/backend-interface.md`(compound tool 계약) + `references/fix-plan-template.md`(수정 계획 문서 양식) 3개뿐이다.

**`sim_state.py` 호출 방법**: 아래 절차의 `sim_state.py {command} ...`는 Bash tool로 실제 배포된 절대경로를 호출하는 축약 표기다. **Windows/Git-Bash 환경에서는 반드시 `MSYS_NO_PATHCONV=1`을 앞에 붙인다** — 이게 없으면 `/usrdata/...`처럼 `/`로 시작하는 `--sim-dir`/`--dump-path` 값을 MSYS가 자기 마음대로 `C:/Program Files/Git/usrdata/...`로 바꿔버려서 sim-state.json에 오염된 값이 기록된다(2026-07-22 실제 발생·확인). 표준 호출 형태:

```bash
MSYS_NO_PATHCONV=1 python3 ~/.claude/skills/xcelium-sim/scripts/sim_state.py \
  append_debug_note --sim-dir {sim_dir} --test {test} --context "..." <<'EOF'
{note 내용}
EOF
```

(note/content/report 같은 자유 텍스트는 `<<'EOF'` heredoc으로 stdin에 넘긴다 — argv 이스케이프 문제 회피). `--project-root`는 생략하면 현재 작업 디렉터리(=RTL 프로젝트 루트, Bash tool의 기본 cwd)를 쓴다 — 다른 위치에서 실행해야 하면 명시적으로 지정한다.

**`MSYS_NO_PATHCONV=1`을 깜빡해도 안전하다**: `sim_state.py` 자체가 `sim_dir`/`dump_path`에 Windows 드라이브 문자 접두(`C:\` 등)가 들어오면 이 오염 패턴으로 판단해 즉시 에러로 거부한다(`_reject_if_msys_mangled`, `scripts/test_sim_state.py::TestMsysMangledPathGuard`) — 이 문서를 안 읽고 호출해도 조용히 오염된 상태가 저장되는 대신 명확한 에러 메시지로 실패한다.

### Subcommand 목록

| Subcommand | 설명 |
|---|---|
| `/sim run {test}` | 실행(batch, 기본) |
| `/sim run {test} --bridge` | 실행(bridge, interactive) |
| `/sim run --regression` | 전체 regression |
| `/sim analyze {test}` | 이전 run 결과 분석(로그+CSV+coverage) |
| `/sim analyze --regression` | regression 전체 결과 분석 |
| `/sim debug {test}` | FAIL 원인 추적 + Fix Sub-cycle 전체 |
| `/sim debug {test} --bridge` | Interactive debugging |
| `/sim verify {test}` | run→analyze→(FAIL시)debug 자동 체이닝 |
| `/sim verify --regression` | regression→분석→FAIL 디버깅 |
| `/sim status` | 현재 상태 조회(cross-session 복구용) — **새 세션에서 가장 먼저 실행** |

### `/sim status` — 세션 시작 시 항상 먼저

`sim-state.json`(`.ai/sim-state.json`, project 로컬)을 읽어 이전 세션 상태를 현재 context로 복구한다. `phase: "fix-plan"`인 테스트가 있으면 "fix-plan 단계에서 보류 중 — `/sim debug {test}`로 이어서 결정하거나 `/sim run {test}`로 새로 시작 가능"을 안내(아래 run 0단계/debug 0단계와 동일한 두 재개 경로).

### `/sim run`

```
0. phase=="fix-plan"(승인 보류 중)이면 "보류 중인 fix-plan이 있는데 새로 시작할까요?" 확인
   → 승인 시 `sim_state.py supersede_fix_plan --sim-dir {sim_dir} --test {test}` 호출 후 1단계 진행
   → 거부 시 `/sim debug {test}`로 유도
0-b. phase=="fix-implement" 이고 fix_implement.implementer=="human" 이고 아직 review 전이면:
   fix-plan.md가 선언한 영향 파일들의 git diff 유무 확인
   → diff 있음: `sim_state.py record_fix_implement --sim-dir ... --test ... --implementer human --files-changed {diff 파일들}`
     (report는 stdin 빈 문자열) → 바로 fix-review 게이트로 진입(아래 debug 6단계 fix-review 참조, 1단계 이하로 내려가지 않음)
   → diff 없음: "아직 변경이 감지되지 않았습니다. 직접 수정 후 다시 실행해 주세요" 안내, phase 유지, 여기서 종료
1. TB 분석서 캐시 확인 → .ai/analysis/tb_{env}_{test}.analysis.md
   frontmatter 있음 → pass_signals/fail_conditions 자동 추출 (phase-0-discovery.md §0B-YAML 참조)
   frontmatter 없음 → 본문 읽어서 판단(fallback) / 분석서 자체 없음 → Phase 0 절차로 먼저 작성
2. backend-interface.md 참조해 compound tool 호출:
   --bridge → connect_simulator(개별 tool) / --regression → sim_regression_summary / 기본 → sim_run_and_check
3. 반환된 CompoundResult로 sim-state.json 갱신(Backend는 파일에 관여 안 함 — Skill이 직접 기록):
   `sim_state.py record_run --sim-dir {sim_dir} --test {test} --status {status} --dump-path {dump_path}`
   (log_summary는 stdin)
4. 결과 보고 + 아래 "Next-Skill 자동 제안" 표대로 다음 단계 제안
```

### `/sim analyze`

```
1. sim-state.json에서 이전 run의 dump_path/log_summary 참조 — 없으면 "먼저 /sim run 필요"
2. 로그 판별: PASS → "regression?" 제안 / FAIL·불확정 → 3단계
3. sim_analyze_waveform 호출(신호=frontmatter pass_signals, 조건=frontmatter fail_conditions)
4. FAIL 유형 자동 분류(아래 표) + sim-state 갱신 + next-skill 제안:
   `sim_state.py record_analyze --sim-dir {sim_dir} --test {test} --csv-path {csv_path}
   [--fail-signals {신호들} --fail-type {유형}]`
```

**FAIL 유형 자동 분류**:

| FAIL 유형 | 판별 기준 | 전략 |
|---|---|---|
| `data_mismatch` | `FAIL: read-back != expected` | CSV 값 비교 (`sim_analyze_waveform`) |
| `timeout` | 로그에 PASS/FAIL 없음 | Bridge mode 전환 |
| `assertion` | `UVM_ERROR`, `$error` | 해당 시점 CSV (`sim_analyze_waveform`) |
| `protocol` | `protocol error`, checker 문구 | 프로토콜 신호 분석 |

### `/sim debug`

```
0. 이미 phase=="fix-plan"인 기존 fix-plan.md가 있으면 1~5단계(재조사) 건너뛰고 바로 6단계 승인 게이트로
0-b. 0단계 아니고 debug.iteration_count > 0이면, 1~5단계 전에 .ai/sim-state/{test}/debug.md를 먼저 Read
     (이미 배제한 가설을 재도출하는 헛수고 방지 — 신호를 이번 조사 대상에서 빼라는 뜻 아님)
1. sim-state.json에서 analyze 결과(FAIL 유형, 이상 시점, 관련 신호) 참조
2. .ai/analysis/{module}.analysis.md(RTL 분석서) 참조 — FSM 전이표, 신호 의존성
3. FAIL 유형별 전략 선택(위 표) — 필요 시 Interactive probing(bridge)
4. 근본원인+approach 도출 — 로컬에 설치돼 있으면 `verilog-rtl-debugger` agent(Task)에 위임,
   결론에 fix_target: rtl|tb 판정도 포함(spec 근거 필수, "RTL에 맞춰 TB를 고친다"는 근거는 반려 대상)
5. `sim_state.py append_debug_note --sim-dir ... --test ... --context {"최초 조사"|"재개 조사(추가 정보 기반)"}`
   (note는 stdin) 로 debug.md에 append
6. Fix Sub-cycle 진입(fix-plan-template.md 참조):
   a. fix-plan.md 작성 → `sim_state.py write_fix_plan --sim-dir ... --test ...`(content는 stdin, fix_target 포함)
   b. AskUserQuestion "이 fix-plan대로 진행할까요?" — 승인/수정 요청/보류 3택
      승인 → `sim_state.py approve_fix_plan ...` → 구현 주체 선택(AskUserQuestion): AI 위임 / 사람이 직접 수정
        AI 위임(fix_target=rtl→verilog-rtl-coder, tb→verilog-tb-coder, Task로 위임 + fix-plan 경로 전달 +
          "범위 밖 변경은 멈추고 보고" 지시) → 완료 시 `sim_state.py record_fix_implement --implementer {agent명}
          --files-changed {...}`(report는 stdin) → 같은 세션에서 바로 fix-review로 진행
        사람이 직접 수정 → 안내만 하고 `sim_state.py record_fix_implement --implementer human --files-changed`
          (빈 목록, report 빈 문자열)만 호출, phase는 fix-implement에 머무름, 세션 종료(비동기 —
          완료 감지는 다음 `/sim run`의 0-b단계가 담당)
      수정 요청 → 가벼운 수정: append_debug_note 없이 write_fix_plan만 재호출 /
                근본원인 재조사 필요: verilog-rtl-debugger 재위임 → append_debug_note(context="fix-plan 수정
                요청 재조사(revision N)") 먼저 → write_fix_plan 재호출 → b 재진입
      보류 → `sim_state.py hold_fix_plan ...`(진짜 no-op) — 세션 종료, 다음 세션에서 이어서 결정 또는 새로 시작
   c. coder의 A0가 ARCH 판정 → verilog-rtl-architect-advisor escalate → ADR(fix-design.md, adr-template.md
      재사용) 산출 → `sim_state.py write_fix_design --sim-dir ... --test ...`(ADR 본문은 stdin, phase가
      fix-design으로 전환) → AskUserQuestion으로 재승인 → 승인되면 `sim_state.py ratify_fix_design --sim-dir
      ... --test ...` 호출(phase가 fix-implement로 복귀) → fix-implement 재개. 사람 구현 경로엔 이 자동
      escalate 없음(필요시 수동 호출)
   d. fix-implement 완료(주체 무관) → fix-review 게이트(필수, 조건부 아님):
      fix_target=rtl → verilog-rtl-reviewer 정적 리뷰 → self-contained 항목은 verilog-rtl-prover formal 증명
      fix_target=tb → verilog-tb-reviewer 정적 리뷰(formal 대응 없음)
      문제 발견 → AI 구현이면 findings와 함께 재위임 / 사람 구현이면 Skill이 findings+개선방향 요약을 먼저
        작성 → `sim_state.py append_fix_review_note --verdict issues_found`(그 요약을 stdin으로) →
        **동일 텍스트를 이 응답에도 그대로 출력**("기록=출력" 동일성 — 별도로 다시 쓰지 않음) →
        fix-implement로 복귀(2라운드 연속이면 AskUserQuestion으로 계속 여부 확인)
      clean → `sim_state.py append_fix_review_note --verdict clean` → phase가 run으로 전환
7. sim-state 갱신 + next-skill 제안 — fix-review clean 통과해야만 phase가 run으로 복귀
```

### `/sim verify`

```
1. /sim run {test}  (0-b단계 포함 — fix-implement 대기 중이면 여기서 diff 감지해 fix-review로 바로 진입 가능)
2. /sim analyze {test}
3. FAIL이면 /sim debug {test}(Fix Sub-cycle 전체) → fix-review clean이면 1단계 재진입
   재진입 안 하는 경우: 수정 요청 중 / 보류 / fix-design ADR 대기 중 / fix-review에서 문제 발견돼 되돌아간 경우
   → 넷 다 이번 verify 호출은 여기서 끝, 나중에 /sim debug(이어서) 또는 /sim run(새로 시작/사람 구현 감지)로 재개
4. PASS면 단일 테스트→"regression?" 제안 / --regression→요약 보고
```

### Next-Skill 자동 제안

| 완료 | 결과 | 제안 |
|---|---|---|
| run | PASS | `/sim run --regression` |
| run | FAIL | `/sim analyze {test}` |
| analyze | 원인 특정 | `/sim debug {test}` |
| analyze | 불확정 | `/sim analyze --signals ...` |
| debug | fix-plan 작성, fix_target=rtl | 승인 게이트 → 구현 주체 선택 → reviewer+prover 필수 리뷰 → `/sim verify {test}` |
| debug | fix-plan 작성, fix_target=tb | 승인 게이트 → 구현 주체 선택 → tb-reviewer 필수 리뷰 → `/sim verify {test}` |
| debug | bridge 필요 | `/sim debug {test} --bridge` |
| fix-review | 문제 발견 | (위 debug 6-d 참조) → 재구현 → fix-review 재진입 |
| fix-review | clean | `/sim verify {test}` (phase가 run으로 복귀) |
| run | 사람 구현 대기 중, diff 없음 | 안내만 하고 대기 |
| verify | PASS | 완료 |
| verify | FAIL | `/sim debug {test}` |

### agent 위임 요약

| 상황 | 위임 대상(로컬 설치 시) |
|---|---|
| debug 4단계 근본원인 조사 | `verilog-rtl-debugger` |
| fix-implement, fix_target=rtl, AI 위임 | `verilog-rtl-coder` |
| fix-implement, fix_target=tb, AI 위임 | `verilog-tb-coder` |
| fix-design(ARCH 판정 시) | `verilog-rtl-architect-advisor` |
| fix-review, fix_target=rtl 정적 리뷰 | `verilog-rtl-reviewer` |
| fix-review, fix_target=rtl formal 증명 | `verilog-rtl-prover`(self-contained 항목만) |
| fix-review, fix_target=tb 정적 리뷰 | `verilog-tb-reviewer` |

이 agent들은 모두 chip-design-skills 소유(cross-repo) — 로컬에 설치돼 있으면 Task로 위임하고, 없으면 Claude가 이 SKILL.md 절차를 직접 따라 수행한다.

### Hook 자동화 (Phase D, 후행)

위 frontmatter의 `hooks:` 블록이 실제 등록이다 — Skill 프론트매터에 직접 선언해 별도 `settings.json` 편집 없이 배포(`cp -r skill-src/xcelium-sim ~/.claude/skills/`)만으로 활성화된다.

| Hook | 역할 |
|---|---|
| `PostToolUse`(`hooks/sim_post_compound.py`) | compound tool 3개 호출 감지 → 반환된 `status:` 값을 읽어 다음 `/sim` 단계를 `additionalContext`로 제안 |
| `UserPromptSubmit`(`hooks/sim_prompt_detect.py`) | 트리거 키워드(위 description과 동일 목록) 감지 시 `.ai/sim-state.json`을 읽어 idle이 아닌(진행 중인 Fix Sub-cycle 등) 테스트가 있으면 `additionalContext`로 요약 — SessionStart를 안 쓰는 것과 같은 이유(§6.1, 토큰 낭비 회피)로 **키워드 매치 전엔 파일도 읽지 않는다** |

**Node.js → Python 전환(Phase D 재검토, Plan §6.2가 스스로 남긴 flag)**: 애초 스케치는 bkit 자체 hook 컨벤션(Node.js — 배포용 plugin이라 런타임 이식성이 필요해서)을 따랐으나, 이 skill은 단일 사용자 개인 배포물이고 이 사용자의 실제 hook(`~/.claude/hooks/guard-*.py`)은 전부 Python이라 그에 맞췄다.

**경로 가정**: hook `command`는 `~` 홈 디렉터리 확장에 의존한다(Claude Code가 `${CLAUDE_SKILL_DIR}` 같은 skill 전용 경로 변수를 제공하지 않고, skill frontmatter hook의 상대경로는 skill 배포 위치가 아니라 **호출 시점 cwd** 기준으로 풀리므로 상대경로를 쓸 수 없음 — 확인 완료). `~` 확장이 동작하지 않는 환경이면 대신 이 두 스크립트를 소비 프로젝트의 `.claude/hooks/`에 복사하고 `${CLAUDE_PROJECT_DIR}/.claude/hooks/...`로 프로젝트 `settings.json`에 등록하는 대안을 쓴다.
