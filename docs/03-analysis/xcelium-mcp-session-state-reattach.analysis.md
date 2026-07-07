# Analysis: xcelium-mcp Session State Reattach (F-D) + Batch-Aware idle-culler (F-E)

> **Summary**: Design v0.1(Option C) 대비 구현 검증. 전부 파일 기반 로직이라 pytest만으로 완결 검증.
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Planning Doc**: [xcelium-mcp-session-state-reattach.plan.md](../01-plan/features/xcelium-mcp-session-state-reattach.plan.md)
> **Design Doc**: [xcelium-mcp-session-state-reattach.design.md](../02-design/features/xcelium-mcp-session-state-reattach.design.md)

---

## 1. Strategic Alignment Check

| 질문 | 판정 | 근거 |
|---|---|---|
| Plan의 핵심 문제(F-D: TB provenance 유실, F-E: idle-culler 오판)를 해결했는가? | ✅ Met | §2 참조 |
| Design의 핵심 결정(Option C — batch_runner.py 무변경, mtime stale guard)이 그대로 따라졌는가? | ✅ Met | §3 참조 |
| `verilog-rtl-debugger`(F-D의 실제 동기)가 요구하는 수준까지 도달했는가? | ✅ Met | 재연결 → checkpoint(save) 엔드투엔드 테스트(T-3)로 실증 |

---

## 2. Plan Success Criteria (Test Plan T-1~T-9) 평가

| # | 기준 | 상태 | 근거 |
|---|---|:---:|---|
| T-1 | `sim_bridge_run` 성공 후 레지스트리에 세션 상태 기록 | ✅ Met | `test_update_session_state_then_get_session_state_roundtrip` |
| T-2 | 레지스트리에 세션 상태가 있는 상태에서 `connect_simulator(sim_dir=X)` → 복원 | ✅ Met | `test_connect_simulator_sim_dir_restores_session_state` |
| T-3 | 복원 후 `checkpoint(save)` → 정확한 provenance 기록(엔드투엔드) | ✅ Met | `test_checkpoint_save_after_reconnect_records_restored_tb_provenance` |
| T-4 | 세션 상태 없는 sim_dir → 기본값(`""`/`None`) 유지 | ✅ Met | `test_get_session_state_returns_defaults_when_no_entry` + `test_checkpoint_save_without_reconnect_records_empty_provenance_baseline` |
| T-5 | `batch_job.json`/`regression_job.json`에 살아있는 PID → idle-culler 전체 스킵 | ✅ Met | `test_alive_pid_in_batch_job_file`, `test_alive_pid_in_regression_job_file` |
| T-6 | job_file mtime이 stale → 무시(회귀 없음) | ✅ Met | `test_stale_job_file_ignored` |
| T-7 | job_file 없음/corrupt → False, 크래시 없음 | ✅ Met | `test_no_job_files_at_all`, `test_corrupt_json_ignored` |
| T-8 | `has_live_batch_job()=True` → `main()`이 워커 순회 없이 조기 반환 | ✅ Met | `test_main_returns_early_without_touching_supervisor` |
| T-9 | 회귀 | ✅ Met | pytest 563 passed(부모 feature 548 + 신규 15), ruff 전체 클린 |

**Met 9/9 — Partial/Not Met 없음.**

---

## 3. Structural Match — Design §8.1 vs 실제

| 파일 | Design 계획 | 구현 | 일치 |
|---|---|---|:---:|
| `registry.py` | `update_session_state`/`get_session_state` 추가 | 동일(§5.1 의사코드와 거의 동일한 실제 구현) | ✅ |
| `tools/sim_lifecycle.py` | `sim_bridge_run` 저장 훅 + `connect_simulator` 복원 훅 | 동일 — `f_c_direct_hit` 플래그로 F-C direct-hit 경로만 정확히 스코프 | ✅ |
| `idle_culler.py` | `has_live_batch_job()` + `main()` 조기 반환 | 동일 + **Design에 없던 부수 개선**: `_pid_alive()`가 `ProcessLookupError` 외의 `OSError`도 안전 측(not alive)으로 처리하도록 강화(Windows 테스트 중 발견 — 실제 Linux 배포 대상에서도 더 견고함) | ✅(개선 포함) |
| `batch_runner.py` | 무변경 | 무변경(git diff로 확인) | ✅ |
| `checkpoint.py` | 무변경 | 무변경(git diff로 확인) | ✅ |

**Structural Match: 5/5, 전부 계획대로 + 1건 견고성 개선.**

---

## 4. Functional Depth

- `registry.py`의 `update_session_state`/`get_session_state`: `_resolve_project_root` 재사용, F-C의 `bridge_port`와 형제 필드로 공존(sibling-field 보존 테스트로 확인) — placeholder 없음.
- `sim_lifecycle.py`: 저장/복원 모두 `try/except OSError: pass`로 best-effort 처리(TB provenance 자체의 기존 관례와 동일 — 실패해도 tool call을 막지 않음).
- `idle_culler.py`: `has_live_batch_job()`이 stale guard·corrupt-JSON·no-file 3가지 실패 모드를 전부 명시적으로 처리 — Design 의사코드보다 견고.

**Functional Depth: 결손 없음.**

---

## 5. Decision Record Verification

| 결정(Plan→Design) | 구현에서 지켜졌는가 |
|---|---|
| F-D는 registry.py 재사용(A/B/C 공통 합의) | ✅ |
| F-E Option C — job_file 직접 읽기 + 전체 스킵 + mtime stale guard | ✅ |
| `batch_runner.py` 무변경(Option B 기각 사유) | ✅ git diff로 확인 |
| checkpoint.py 무변경 | ✅ |
| idle_culler는 asyncio/shell_run 의존성 없이 순수 동기 유지 | ✅ `_default_user_tmp_dir()`도 `os.getuid()`만 사용, 신규 의존성 없음 |

**이탈 없음.**

---

## 6. Match Rate

이 feature는 파일 기반 로직만 다뤄 cloud0 실측이 불필요하다고 Design이 이미 명시했고(§7), 실제로 전부 pytest로 커버됐다.

| 축 | 점수 | 근거 |
|---|:---:|---|
| Structural | 100% | 5/5 파일 계획대로 |
| Functional | 100% | placeholder 없음, 부수 견고성 개선까지 포함 |
| Contract | 100% | 기존 MCP tool(`sim_bridge_run`/`connect_simulator`/`checkpoint`) 시그니처 무변경, 전부 하위호환 optional 동작 |
| Runtime(pytest) | 100% | T-1~T-9 전부 Met, 563 passed |

**Overall Match Rate ≈ 100%**

---

## 7. Checkpoint 5 — Review Decision 대상 이슈

없음 — Critical/Important 이슈 0건.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Design v0.1(Option C) 대비 구현 검증. Match Rate 100%(T-1~T-9 전부 Met). 부수 발견(Windows `os.kill` 플랫폼 차이로 인한 `_pid_alive()` 견고성 개선)까지 포함해 이탈 없음 확인. | hoseung.lee |
