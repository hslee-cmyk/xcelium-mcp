# fix-plan.md Template

`/sim debug`의 6단계(Fix Sub-cycle)가 `.ai/sim-state/{test}/fix-plan.md`를 이 템플릿으로 작성한다(`sim_state.py`의 `write_fix_plan()` 호출). 사용자 승인 게이트(승인/수정 요청/보류)의 대상 문서다 — 구두 제안이 아니라 이 문서 자체를 승인받는다.

## 필수 섹션

```markdown
---
fix_target: rtl
---

# Fix Plan — {test_name}

## 근본원인

{`/sim debug` 1~5단계에서 도출한 근본원인 요약 — debug.md의 최종 결론을 옮겨 적는다, 재작성하지 않는다}

## 판정 근거 (fix_target: rtl | tb)

{RTL이 spec대로 안 만들어졌다는 근거, 또는 TB의 기대값/자극 생성이 spec과 다르게 잘못됐다는 근거.
"RTL이 이렇게 동작하니 TB를 맞춘다"는 근거는 반려 대상(anti-tautology) — 반드시 spec 또는
`.ai/analysis/tb_*.analysis.md`의 판별 신호/기대값 정의 대비로 서술한다}

## 영향 모듈/파일

- {module/file 1}
- {module/file 2}

## Structural Delta (fix_target=rtl 전용)

{어떤 always 블록/net/FSM/clock/instance가 바뀌는가 — `verilog-rtl-coder`의 A0 model-diff gate가
요구하는 것과 동일한 형식. fix_target=tb면 이 섹션 대신 아래 "영향 테스트/시퀀스" 사용}

## 영향 테스트/시퀀스/checker (fix_target=tb 전용)

{어떤 테스트/시퀀스/checker가 바뀌는가}

## 수정 Approach

{무엇을 어떻게 고칠지 — coder/tb-coder 또는 사람 구현자가 이 설명만으로 구현 범위를 알 수 있어야 함}

## 검증 대상

- `/sim verify {test_name}`
- (있으면 추가로 재실행할 관련 테스트)
```

## `fix_target:` 필드 파싱 규칙

`sim_state.py::write_fix_plan()`이 문서 본문에서 `fix_target: rtl` 또는 `fix_target: tb` 줄(대소문자 무관)을 찾아 `origin_chain.fix_plan.fix_target`에 반영한다 — **문서가 정본**, JSON은 그 값에서 파생된 포인터일 뿐이다(Plan §5.1 "JSON=포인터, MD=정본" 원칙). 위 예시처럼 YAML frontmatter 블록(`---\nfix_target: rtl\n---`)에 두는 게 권장 형식이지만, 파서는 그 줄이 frontmatter 안에 있는지는 보지 않고 문서 전체에서 첫 매치만 찾는다 — frontmatter 밖에 평문으로 적어도 동작한다.

## 개정 루프에서의 갱신

"수정 요청" 피드백을 반영해 이 문서를 다시 쓸 때도 `write_fix_plan()`을 그대로 재호출한다(새 파일이 아니라 같은 파일을 덮어씀, `revision_count` 자동 증가) — 섹션 구조는 그대로 유지하고 내용만 갱신한다.

## ARCH 판정 시(fix_target=rtl 전용)

coder의 A0 gate가 ARCH로 판정하면 이 문서는 그대로 두고 `fix-design.md`(ADR, `verilog-rtl-architect-advisor`가 산출 — 별도 템플릿 없음, coder 자신의 `adr-template.md` 재사용)가 추가로 생성된다. LOCAL/IFACE면 이 문서만으로 충분하다.
