# Backend Interface — Compound Operations (Layer 3)

## 목적

`/sim` Skill(Layer 4)이 시뮬레이터 backend(현재: xcelium-mcp)를 호출할 때 쓰는 3개 compound operation의 계약을 정의한다. 이 문서가 계약의 정본이다 — 코드 수준 추상 클래스(Python Protocol/ABC)는 없다(Design Checkpoint 3, Option C — 두 번째 backend가 실제로 만들어지기 전까지 YAGNI). 새 backend(예: vcs-mcp)를 추가할 때는 이 문서에 정의된 3개 MCP tool을 같은 이름·같은 반환 형식으로 구현하면 된다.

## 3개 Compound Operation

| Operation (MCP tool) | 언제 쓰는가 | 대응 raw tool 조합 |
|---|---|---|
| `sim_run_and_check` | 테스트 실행 + (선택) 실행 직후 CSV 추출/조건 검색을 한 번에 | `sim_batch_run` + `bisect_signal`/`extract_csv` |
| `sim_analyze_waveform` | 이미 있는 dump에서 CSV 추출 + 여러 조건 검색 | `bisect_signal` 여러 번 |
| `sim_regression_summary` | Regression 실행 + (선택) 실패 테스트 CSV 추출 | `sim_regression` + `extract_csv` 여러 번 |

## 반환 형식 — CompoundResult

세 tool 모두 아래 형식의 텍스트를 반환한다(`CompoundResult.to_mcp_output()`, `src/xcelium_mcp/compound.py`):

```
status: PASS | FAIL | ERROR | PARTIAL
{log_summary}

dump_path: ...        (있으면)
csv_path: ...          (있으면)

details:
{JSON}                 (있으면 — dump_summary/bisect_result/conditions/tb_provenance/per_test_verdicts/csv_by_test 등)
```

| status | 의미 |
|---|---|
| `PASS` | 정상 통과(단일 테스트) 또는 분석 자체 성공(analyze는 시뮬레이션 검증이 아니므로 "성공"의 의미) |
| `FAIL` | 시뮬레이션 실패(assertion/mismatch) 또는 verdict 없이 timeout/crash |
| `ERROR` | 실행 자체 실패(EDA 환경변수 미설정, SSH 끊김 등) — 예외에서 옴, 로그 내용에서 추론하지 않음 |
| `PARTIAL` | Regression 중 일부만 실패(`sim_regression_summary` 전용) |

## `sim_run_and_check`

`test_name`, `sim_dir`(빈 값=registry 기본값), 그리고 `sim_batch_run`과 동일한 실행 파라미터(`dump_depth`/`timeout`/`sim_mode`/`extra_args`/`dump_window_*`/`sdf_*`/`force`/`dump_scopes`/`use_dump_history`) + 아래 CSV 파라미터.

| 파라미터 | 설명 |
|---|---|
| `csv_signals` | 실행 직후 CSV로 추출할 신호 목록. 생략하면 CSV 단계 자체를 건너뜀 |
| `csv_mode` | `"range"`(기본, 전체 구간 추출) \| `"bisect"`(`find_condition` 기반 조건 검색) |
| `find_condition` | `csv_mode="bisect"`일 때 필수 — `{"signal","op","value","start_ns","end_ns","context_signals"}` |

CSV 추출이 실패해도(예: simvisdbutil 오류) 시뮬레이션 자체의 PASS/FAIL 판정은 낮춰지지 않는다 — `details.csv_error`에만 기록된다.

## `sim_analyze_waveform`

`dump_path`(필수, 기존 dump), `signals`(추출 대상), `find_conditions`(선택 — 여러 조건 리스트, 각각 독립적으로 같은 dump에 대해 검색), `start_ns`/`end_ns`(추출 구간). 시뮬레이션을 새로 실행하지 않는다 — `/sim analyze`가 이전 `run` 결과의 dump를 재분석할 때 쓴다.

## `sim_regression_summary`

`test_list`(빈 값=`.mcp_sim_config.json`의 `test_list`), `sim_dir`, 그리고 `sim_regression`과 동일한 실행 파라미터 + 아래.

| 파라미터 | 설명 |
|---|---|
| `csv_on_fail` | `True`면 전체 status가 PASS가 아닐 때 CSV 추출 시도 |
| `csv_signals` | `csv_on_fail=True`일 때 추출할 신호 목록 |

`run_batch_regression()`은 `classify_regression_results()`가 이미 계산하는 테스트별 분류를
`per_test_verdicts`(`{test_name: "pass"|"fail"|"complete"|"error"}`)로도 반환한다(F-190).
`csv_on_fail=True`일 때 `sim_regression_summary`는 이 dict를 이용해 **실제로 "fail"/"error"로
분류된 테스트에 대해서만** CSV를 추출한다 — `test_list` 전체를 훑던 이전 동작(과거엔 "알려진
단순화"로 문서화돼 있었음, `TODO.md`에서 제거됨)을 대체. `per_test_verdicts`는 응답의
`details`에도 그대로 노출되므로, Skill이 규명 없이 실패 테스트를 바로 알 수 있다.

## 두 번째 Backend를 추가하려면

1. 위 3개 tool 이름·파라미터·반환 형식을 동일하게 구현한다(`CompoundResult.to_mcp_output()` 형식 재사용).
2. Skill(SKILL.md Phase 2 라우팅)은 backend 이름을 알 필요 없이 이 3개 tool만 호출하므로, Skill 쪽 변경은 없다.
3. 이 시점이 되면 코드 수준 `Protocol`/ABC로 승격하는 걸 재검토한다(Design Checkpoint 3 Option B — 지금은 과설계로 보류된 안).
