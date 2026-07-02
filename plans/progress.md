
---

## 2026-07-02 - F-154: 버그 수정 — cleanup_stale_csv mtime 탐지 휴리스틱 오탐 수정

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Minor #9)에서 발견. `cleanup_stale_csv`가 파일명을 `_`로 split한 뒤 "9자리 이상 숫자인 첫 토큰"을 mtime으로 간주했는데, SHM stem(보통 테스트명) 자체에 9자리 이상 숫자가 포함되면(예: 테스트명에 타임스탬프가 임베드된 경우) 그 토큰이 실제 mtime보다 먼저 매치되어 **유효한 캐시 파일이 잘못 삭제**될 수 있는 휴리스틱이었음.

### 구현 내용
- `_default_output_path()`의 실제 파일명 구성 규칙(`mcp_csv_{stem}_{sig_hash}{mtime_part}{suffix}.csv`, `sig_hash`는 md5 hexdigest 8자)을 근거로, 파일명 **끝(tail)에 고정 앵커**된 정규식 `_STALE_CSV_MTIME_RE = re.compile(r'_[0-9A-Za-z]{8}_(\d{9,})(?:_\d+_\d+)?$')` 신설
- `cleanup_stale_csv()`가 `parts.isdigit()` 스캔 대신 이 정규식으로 `f.stem`의 끝에서부터 mtime을 추출 — stem 앞쪽에 우연히 낀 9자리+ 숫자는 더 이상 오매칭되지 않음

### 검증
`tests/test_f136_f137.py TestCleanupStaleCsv`에 2개 추가 — stem에 9자리+ 숫자가 임베드된 케이스에서 (1) 실제로 유효한(mtime 일치) 캐시 파일이 삭제되지 않는지, (2) 실제로 stale한(mtime 불일치) 파일은 여전히 정상 삭제되는지 확인. 기존 2개 테스트도 코드 수정 없이 그대로 통과(sig_hash 문자 클래스를 `[0-9A-Za-z]{8}`로 잡아 기존 테스트의 비-hex 합성 sig_hash 예시와도 호환).
`python -m pytest` 427 passed (425→427) / `python -m ruff check src/` all checks passed.

### F-144~F-154 전체 마무리 — bisect 소수점 버그 + code-analyzer 리뷰 전체 항목 완료
2026-07-02 하루 동안 사용자 버그 리포트(bisect CSV 소수점 미지원)에서 시작해 F-144~F-147(같은 버그 클래스 광범위 조사), code-analyzer 리뷰(F-148~F-154, Critical 2 + Major 2 + Minor 3) 전부 구현·테스트·커밋 완료. pytest 325(F-140 시작 시점) → **427 passed**. execute_tcl(Major #5)과 `_list_signals_recursive` 삭제(Minor #8)만 사용자 지시로 범위 밖 유지. `plans/prd.json`은 규칙상 전부 `passes:false`로 남아있음 — 사용자 확인 후 일괄 반영 필요.

---

## 2026-07-02 - F-153: 성능 개선 — discovery.py SDF 분석 인스턴스별 N+1 grep 병렬화

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Minor #7)에서 발견. `_analyze_sdf_annotate`의 Step 3b(인스턴스별 모듈 정의 파일 탐색)가 인스턴스마다 순차 `grep -rl` 호출 — top module 산하 인스턴스가 많을수록 `sim_discover` 지연.

### 구현 내용
- 인스턴스 모듈명 리스트를 먼저 추출한 뒤, 각각의 grep 호출을 `asyncio.gather`로 동시 실행 — `gather()`가 입력 순서를 보존하므로 `search_files` 최종 순서는 기존 순차 버전과 동일
- Step 3a(includes/instances 추출)가 이미 같은 파일 내 `asyncio.gather` 패턴을 쓰고 있어 스타일 일관성 유지

### 검증
`tests/test_sdf_instance_lookup.py` 신규 작성 (2 tests, 이전엔 이 함수의 Step 3 로직 테스트가 전무했음) — 3개 인스턴스 중 2개만 파일로 resolve되는 케이스에서 `search_files`/`sdf_source_file` 매핑이 정확한 순서로 유지되는지(병렬화의 핵심 리스크), 전부 미발견 시 `has_sdf_annotate=False`로 정상 처리되는지 확인.
`python -m pytest` 425 passed (423→425) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-154(cleanup_stale_csv mtime 휴리스틱, priority 3) — 유일하게 남은 항목.

---

## 2026-07-02 - F-152: 성능 개선 — batch_runner.py regression 결과 파싱 N+1 병렬화

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Minor #6)에서 발견. `run_batch_regression`의 "Parse final results" 루프가 테스트당 4회(`test -f`, `ls -t` fallback, grep 2회) 순차 `shell_run` subprocess를 실행 — 대규모 regression에서 `4·N`회 순차 round-trip.

### 구현 내용
- 루프 본문을 `_collect_test_result(tn) -> (tn, results, err)` 코루틴으로 추출
- `for tn, results, err in await asyncio.gather(*(_collect_test_result(t) for t in test_list))`로 전체 테스트를 동시 실행 — 각 코루틴은 자기 자신의 로컬 변수만 다루고 튜플을 반환, dict 쓰기는 gather 완료 후 메인 코루틴에서 순차 수행하므로 race condition 없음
- shell_run 호출 내용/순서는 테스트별로 완전히 동일 — 병렬화만 적용, 로직 변경 없음

### 검증
`tests/test_regression_result_collection.py` 신규 작성 (2 tests) — 동시 실행 시 결과가 테스트끼리 뒤섞이지 않는지(각 테스트의 PASS/FAIL이 자기 이름에 정확히 매핑되는지), 한 테스트에 로그가 없어도 다른 테스트에 영향 없는지 확인. 이런 검증이 병렬화의 핵심 리스크(cross-test 데이터 오염)를 직접 겨냥함.
기존 `tests/test_dump_history_stats.py`의 회귀 테스트(`shell_run` 전부 `""` 반환)도 그대로 통과 — 동작 동일성 확인.
`python -m pytest` 423 passed (421→423) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-153(discovery.py SDF 분석 N+1), F-154(cleanup_stale_csv mtime 휴리스틱) — priority 2/3, 아직 미착수.

---

## 2026-07-02 - F-151: 보안 수정 — compare_simvision의 signals sanitize 누락 수정

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Major #4)에서 발견, Claude가 직접 검증. `compare_waveforms`의 `signals` 파라미터는 default(`csv_diff`) 모드에서 이미 `csv_cache.extract()` → `sanitize_signal_name`을 거치는데, `simvision` 모드(`compare_simvision`)만 이 검증 없이 `" ".join(signals)`를 그대로 bridge에 넘기고 있었음 — 다른 신호명 관련 tool들과의 비일관.

### 구현 내용
- `simvision_ops.py compare_simvision()`: `shm_after` 검증(F-150) 직후, BEFORE/AFTER 그룹에 추가하기 전에 `signals = [sanitize_signal_name(s) for s in signals]` 추가 — 실패 시 `ERROR` 반환
- import에 `sanitize_signal_name` 추가

### 검증
`tests/test_validate_tcl_path.py`에 2개 추가 — injection 신호명 거부(WAVEFORM_ADD 호출이 발생하지 않음 확인), 정상 신호명이 BEFORE/AFTER 그룹에 올바르게 추가되는지(AFTER는 `cmp_after.` 접두사 확인) 검증.
`python -m pytest` 421 passed (419→421) / `python -m ruff check src/` all checks passed.

### F-148~F-151 전체 마무리 — code-analyzer 리뷰 Critical/Major 보안 항목 완료
- F-148: `sanitize_signal_name` 개행 미차단 (Tcl 명령 스머글링)
- F-149: `reload_waveform` shm_path 무검증
- F-150: `validate_tcl_path` 공용 헬퍼 + `open_database`/`compare_simvision`(경로)
- F-151: `compare_simvision`의 signals sanitize 누락

리뷰에서 발견된 Critical 2건 + Major 2건 전부 수정 완료(F-152/153/154는 성능/저위험 항목으로 미착수). execute_tcl(Major #5)과 `_list_signals_recursive` 삭제(Minor #8)는 사용자 지시로 수정 대상에서 제외됨.

---

## 2026-07-02 - F-149: 보안 수정 — reload_waveform shm_path 무검증 수정

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Critical #2)에서 발견, Claude가 직접 검증. `simvision(action='reload')` → `reload_waveform`이 `shm_path`를 `validate_path`조차 거치지 않고 바로 `f"database open {shm_path}"`에 삽입 — F-148/F-150과 같은 계열이지만 검증이 아예 없었던 가장 심각한 지점.

### 구현 내용
- `simvision_ops.py reload_waveform()` 최상단에 `if shm_path: err = validate_tcl_path(shm_path, "shm_path")` 추가 — F-150에서 만든 공용 헬퍼 재사용. 빈 `shm_path`(= 현재 SHM 그대로 reload, 경로 보간 없음)는 검증 대상에서 제외.

### 검증
`tests/test_validate_tcl_path.py`에 3개 추가 — injection 거부, 정상 경로 통과, 빈 shm_path(같은 DB reload) 케이스.
`python -m pytest` 419 passed (416→419) / `python -m ruff check src/` all checks passed.

### F-148~F-150 정리 — Tcl injection 3형제 마무리
- F-148: `sanitize_signal_name` 개행 미차단 → Tcl 명령 스머글링
- F-150: `validate_tcl_path` 공용 헬퍼 + `open_database`/`compare_simvision`
- F-149: `reload_waveform` (F-150 헬퍼 재사용)

남은 건 **F-151**(`compare_simvision`의 `signals` sanitize 누락) 하나. Minor 성능 항목(F-152/F-153)과 F-154도 미착수.

---

## 2026-07-02 - F-150: 보안 수정 — Tcl-safe path validator 신설 (open_database/compare_simvision)

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Major #3)에서 발견. `validate_path`는 파일시스템 안전성(null byte, `..` traversal)만 보장하고 Tcl 메타문자(`[`, `]`, `$` 등)는 걸러내지 않는데, 이 함수가 Tcl로 넘어가는 SHM 경로(`open_database`, `compare_simvision`)의 유일한 검증이었음. F-149(reload_waveform)가 재사용할 공용 헬퍼가 필요해 순서를 바꿔 F-150을 먼저 구현.

### 구현 내용
- `shell_utils.py`에 `validate_tcl_path(path, label)` 신설 — `validate_path()`를 먼저 호출(null byte/traversal 위임)한 뒤, allowlist 정규식(`^[\w./-]+$`)으로 Tcl 메타문자·공백·개행·따옴표·백슬래시를 전부 거부
- `simvision_ops.py open_database()`: `validate_path` → `validate_tcl_path`로 교체 (함수 진입부 1곳 — `name_opt`/`xmsim fallback` 등 하위 사용처는 같은 `shm_path` 변수를 그대로 쓰므로 자동으로 커버됨)
- `simvision_ops.py compare_simvision()`: `shm_after`가 Tcl `database open`에 도달하기 직전에 `validate_tcl_path` 신규 추가 (기존엔 검증이 전혀 없었음 — 호출부 `compare_waveforms`의 `validate_path`는 Tcl 메타문자를 못 거름)

### 검증
`tests/test_validate_tcl_path.py` 신규 작성 (20 tests) — `validate_tcl_path` 단위 테스트(정상 경로, injection payload 9종, validate_path 위임 확인), `open_database`/`compare_simvision` end-to-end 통합 테스트(injection 시 bridge.execute 미호출 확인). `compare_simvision`은 VNC/launch/bridge-ready 폴링 전체를 mock(asyncio.sleep 포함)해 무거운 셋업 없이 검증.
`python -m pytest` 416 passed (396→416) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-149(reload_waveform — 이 헬퍼 재사용 예정), F-151(compare_simvision의 signals sanitize 누락) — priority 1, 아직 미착수.

---

## 2026-07-02 - F-148: 보안 수정 — sanitize_signal_name 개행문자 미차단 (Tcl 명령 스머글링)

### 배경
code-analyzer 에이전트의 광범위 코드 리뷰(Critical #1)에서 발견, Claude가 직접 프로토콜 코드를 추적해 재현 가능함을 검증. `sanitize_signal_name`이 `[`, `$`, `;`만 거부하고 개행은 체크하지 않음 — `tcl_bridge.py:129`가 명령을 `command + "\n"`으로 1개 개행 프레이밍하고, `tcl/mcp_bridge.tcl:162-186`의 `on_readable`이 `gets $channel line`으로 한 줄씩 읽어 즉시 dispatch. 신호명에 개행이 포함되면 두 개의 독립된 Tcl 명령으로 스플릿되어 두 번째 명령이 Python 클라이언트 모르게 실행됨.

### 구현 내용
- `shell_utils.py sanitize_signal_name()`: 빈 문자열 체크 직후 `'\n' in stripped or '\r' in stripped` 검사 추가 — 개행/CR 포함 시 `ValueError`
- docstring에 개행/CR 거부 근거(line-framed 프로토콜) 명시

### 영향도 (사전 검증 완료, 회귀 없음)
`sanitize_signal_name` 호출부 14곳(`csv_cache.py`, `debug_tools.py`, `tools/debug.py`, `tools/signal_inspection.py`, `tools/waveform.py`) 전부 개별 신호명/scope 문자열만 넘겨 개행을 포함할 정당한 사용 사례 없음. `.strip()`은 선행/후행 개행만 제거하므로 (`test_leading_trailing_newline_still_stripped`로 확인) 중간에 삽입된 개행만 새로 거부됨.

### 검증
`tests/test_pure_helpers.py TestSanitizeSignalName`에 4개 injection 케이스 + strip 경계 테스트 1개 추가, `tests/test_deposit_signal.py`에 `deposit_signal` end-to-end 통합 테스트 1개 추가(개행 포함 signal 전달 시 bridge 미호출 확인).
`python -m pytest` 396 passed (390→396) / `python -m ruff check src/` all checks passed.

### 남은 작업
같은 리뷰에서 발견된 F-149(reload_waveform shm_path 무검증), F-150(Tcl-safe path validator), F-151(compare_simvision signals sanitize 누락) — 전부 priority 1, 아직 미착수. execute_tcl(5번)과 dead code 제거(8번)는 사용자 지시로 수정 대상에서 제외.

---

## 2026-07-02 - F-147: 버그 수정 — deposit_signal 값 검증 정규식 소수점(real/wreal) 미지원

### 배경
F-144 광범위 조사로 분리된 마지막 항목. bisect(읽기 경로)의 소수점 버그와 짝을 이루는 쓰기 경로 — `deposit_signal`의 `_DEPOSIT_VALUE_RE`가 digital Verilog literal만 허용해 real/wreal(AMS analog) 값("3.3")을 Tcl에 도달하기도 전에 Python 레이어에서 거부.

### 구현 내용
- `signal_inspection.py _DEPOSIT_VALUE_RE`: `^[\d'bhBHdDoOxXzZ_]+$` → `^[\da-fA-F'bhBHdDoOxXzZ_.eE+-]+$` — 소수점(`.`), 부호(`-`/`+`), 지수표기(`e`/`E`) 추가. injection 방지 목적(F-012)은 그대로 유지 — Tcl 메타문자(`;`,`[`,`]`,`$`,`{`,`}`, 공백, 따옴표, 백슬래시)는 여전히 전부 거부됨(char class에 없음).
- **부수 발견**: 테스트를 작성하다가 `8'hFF`(함수 자체 docstring의 예시)가 기존 정규식에서 애초에 매치되지 않던 사전 버그를 발견 — hex 자릿값 문자 `a-f`/`A-F`가 char class에 전혀 없었음(라디칼 지정자 `h`/`H`만 있고 실제 hex 숫자는 없었음). `deposit_signal`에 대한 테스트가 이전까지 전무해 아무도 잡지 못했던 것 — 같은 정규식을 손대는 김에 `a-fA-F`도 함께 추가해 실제로 동작하도록 수정.
- docstring/에러 메시지에 real/wreal 예시(`3.3`, `-1.5`, `1.2e-05`) 추가

### 검증
`tests/test_deposit_signal.py` 신규 작성 (24 tests, 이전엔 deposit_signal에 대한 테스트가 전무했음) — 기존 digital literal 회귀 없음(hex 포함), 신규 decimal/음수/과학적 표기법 허용, injection payload 10종 여전히 거부, end-to-end `deposit_signal` tool 호출(정상/injection/digital 각 1개) 확인.
`python -m pytest` 390 passed (366→390) / `python -m ruff check src/` all checks passed.

### F-144~F-147 전체 마무리
사용자 버그 리포트("bisect CSV 소수점 미지원")에서 출발한 광범위 조사 4개 항목 모두 구현·테스트 완료:
- F-144: bisect CSV (읽기 경로, SimTime + 값 비교)
- F-145: compare_waveforms (동일 버그 독립 재구현 지점)
- F-146: 시간 문자열 파싱 3곳 (bridge where / checkpoint L1 / sim_run duration)
- F-147: deposit_signal (쓰기 경로, + 부수적으로 발견한 hex 버그)
전체 pytest 325(F-140 시작 시점) → 390 passed, 신규 테스트 65개 추가. `plans/prd.json`은 프로젝트 규칙상 `passes:false`로 유지 — 사용자 확인 대기.

---

## 2026-07-02 - F-146: 버그 수정 — 시간 문자열 파싱 3곳 소수점 미지원

### 배경
F-144 광범위 조사로 분리된 항목. "N + 단위" 형태의 시간 문자열을 파싱하는 3곳이 전부 `\d+`-only 정규식이라 소수점을 거부: bridge `where` 응답 파싱(`shell_utils.py`), checkpoint L1 저장 시각(`tcl_preprocessing.py`), `sim_run` duration 검증(`sim_lifecycle.py`).

### 구현 내용
- `shell_utils.py _parse_time_ns()`: 4개 정규식의 `(\d+)` → `(\d+(?:\.\d+)?)`, `int()` → `float()` + 최종 `round()`로 교체 — "N MS + M" 형태의 두 파트(coarse/fine) 모두 소수점 지원
- `tcl_preprocessing.py _parse_l1_time_ns()`: 동일 패턴 — `l1_time` 파라미터(예: "1.5ms")가 정수부만 조용히 파싱되던 문제 수정
- `tools/sim_lifecycle.py _DURATION_RE`: `^[0-9]+\s*(unit)$` → `^[0-9]+(?:\.[0-9]+)?\s*(unit)$`로 확장. `_duration_to_ns()`는 이미 `float()` 변환이라 게이트(정규식)만 손대면 충분 — F-013(Tcl injection 방지) 특성(fullmatch, ASCII-only, unit 필수)은 전부 그대로 유지, 추가된 문자는 `.`뿐.
- 세 곳 모두 waveform.py의 `_TIME_RE`(`^\d+(\.\d+)?...`) 패턴을 참조해 스타일 통일

### 검증
`tests/test_pure_helpers.py`에 `TestParseTimeNs`(+3) / `TestParseL1TimeNs`(신규, 7 tests) 추가, `tests/test_sim_lifecycle.py`에 `_DURATION_RE`/`_duration_to_ns`/`sim_run` 소수점 케이스 5개 추가 — injection payload가 소수점 허용 후에도 여전히 거부되는지 회귀 테스트 포함.
`python -m pytest` 366 passed (351→366) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-147(deposit_signal 값 검증) — priority 2, 아직 미착수.

---

## 2026-07-02 - F-145: 버그 수정 — compare_waveforms/compare_csv_diff SimTime 소수점 크래시

### 배경
F-144 조사 중 발견한, 독립적으로 재구현된 동일 버그. `simvision_ops.py`의 `_load_rows()`가 `csv_cache.py`의 `bisect_csv()`와 완전히 별도로 SimTime 파싱을 구현하면서 똑같이 `int(raw_time)`을 무방비로 호출 — `compare_waveforms` MCP tool(`compare_csv_diff` 경유)이 소수점 SimTime CSV에서 크래시.

### 구현 내용
- `simvision_ops.py`가 `csv_cache.py`의 `_parse_sim_time_ns()` 헬퍼를 import해 재사용 (F-144에서 만든 공용 헬퍼) — 코드 중복 없이 동일한 반올림/예외 정책 적용
- `_load_rows()`의 `rows[int(raw_time)] = row` → `_parse_sim_time_ns()` + try/except로 교체, 파싱 실패 row는 skip
- dict 키 타입은 원래도 int였고 지금도 int(반올림)라 `compare_csv_diff()`의 `set(rows_b) | set(rows_a)` 비교는 자연히 그대로 정합

### 검증
`tests/test_compare_csv_diff.py` 신규 작성 (4 tests) — `_load_rows` 소수점 SimTime 크래시 방지, 정수/소수점 CSV 간 키 타입 일치, `compare_csv_diff()` end-to-end(fake csv_cache로 extract mock) 크래시 방지 확인.
`python -m pytest` 351 passed (347→351) / `python -m ruff check src/` all checks passed. `python -c "import xcelium_mcp.server"` 순환 임포트 없음 확인.

### 남은 작업
F-146(시간 문자열 정규식 3곳), F-147(deposit_signal 값 검증) — priority 2, 아직 미착수.

---

## 2026-07-02 - F-144: 버그 수정 — bisect CSV 소수점 값 미지원

### 배경
사용자 버그 리포트: "bisect를 csv 파일로 처리할 때 소수점 자리가 지원이 안 되는 문제". Explore 에이전트 조사 + 직접 코드 확인으로 `csv_cache.py`에서 원인 2곳 확정.

### 구현 내용
- `csv_cache.py`에 `_parse_sim_time_ns(raw: str) -> int` 헬퍼 신규 추가 — `int(raw)` 우선 시도, 실패 시 `float(raw)` 후 `round()`로 반올림. 완전히 파싱 불가하면 `ValueError` 전파.
- `bisect_csv()`의 SimTime 파싱 2곳(메인 루프, suffix read-ahead 루프)을 `int(raw_time)` → `_parse_sim_time_ns()` + try/except로 교체 — 파싱 실패 row는 크래시 대신 skip.
- `_to_number(s: str) -> int | float | None` 헬퍼 신규 추가 — `int(s, 0)`(hex/oct/dec literal) 우선 시도, 실패 시 `float(s)`(소수점/과학적 표기법 실수값) 폴백. 둘 다 실패하면 `None` 반환.
- `_eval_condition()`의 숫자 비교를 `int(cur_val, 0)`/`int(target, 0)` → `_to_number()` 기반으로 교체 — `eq`/`ne`/`gt`/`lt` 4개 op 전부 소수점 값에서 정상 동작. 기존 tristate(`x`/`z`) → 문자열 fallback 동작은 그대로 유지.

### 검증
`tests/test_bisect.py`에 21개 신규 테스트 추가 (`TestBisectCsvGtLt`, `TestBisectCsvDecimalValue`, `TestBisectCsvDecimalSimTime`, `TestParseSimTimeNs`, `TestToNumber`) — gt/lt op은 이번 수정 전까지 정수값에 대해서도 테스트가 전무했음을 확인하고 함께 보강.
`python -m pytest` 347 passed (326→347) / `python -m ruff check src/` all checks passed.

### 남은 작업 (별도 태스크로 분리, 2026-07-02 광범위 조사)
- F-145: `simvision_ops.py _load_rows()`(compare_waveforms/compare_csv_diff)에 동일한 `int(raw_time)` 크래시가 독립적으로 존재 — F-144의 `_parse_sim_time_ns` 재사용 권장
- F-146: 시간 문자열 파싱 3곳(`shell_utils.py _parse_time_ns`, `tcl_preprocessing.py _parse_l1_time_ns`, `sim_lifecycle.py _DURATION_RE`)이 `\d+`-only 정규식이라 소수점 미지원
- F-147: `deposit_signal`의 `_DEPOSIT_VALUE_RE`가 digital literal만 허용해 real/wreal(analog) 값 미지원 — bisect(읽기) 버그의 쓰기 경로 짝

---

## 2026-07-02 - F-143: v5.2 gap-fix (Minor M4) — _update_dump_history load_sim_config force=True

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` (2026-07-02 재분석) Minor M4.
`_update_dump_history`가 `load_sim_config(sim_dir)`을 `force=True` 없이 호출 — design.md §7 pseudocode는 `force=True`를 사용. 기존엔 `save_sim_config`의 캐시 무효화에 의존해 순차 regression 루프 안에서는 문제없었지만, 다른 프로세스/세션이 동시에 config를 갱신한 경우 stale 캐시를 읽을 이론적 가능성이 있었음.

### 구현 내용
- `batch_runner.py` `_update_dump_history()`: `load_sim_config(sim_dir)` → `load_sim_config(sim_dir, force=True)` — design.md §7 스펙과 일치
- `tests/test_dump_history_stats.py`: `test_update_dump_history_loads_config_with_force` 신규 추가 — `load_sim_config`가 `force=True`로 호출되는지 mock assertion으로 검증

### 검증
`python -m pytest` 326 passed (325→326, 신규 1개), 0 warnings / `python -m ruff check src/` all checks passed.
`run_batch_regression` per-test 루프에서의 반복 호출도 순차 실행이라 회귀 없음 — `test_regression_updates_dump_history_and_dump_stats_shape`가 `_update_dump_history` 자체를 mock하므로 이번 변경의 영향을 받지 않고 그대로 통과.

---

## 2026-07-02 - F-142: v5.2 gap-fix (Minor M2) — datetime.utcnow() deprecation 해소

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` (2026-07-02 재분석) Minor M2.
`_update_dump_history`가 Python 3.12+에서 deprecated된 `datetime.utcnow()`를 사용 — pytest 스위트에서 DeprecationWarning 2건 발생 중이었음(3.14 환경).

### 구현 내용
- `batch_runner.py` import: `from datetime import datetime` → `from datetime import datetime, timezone`
- `_update_dump_history()`: `datetime.utcnow().isoformat(timespec="seconds")` → `datetime.now(timezone.utc).isoformat(timespec="seconds")`
- 코드베이스 내 `datetime.utcnow()` 사용처는 이 한 곳뿐이었음 (grep 확인)

### 검증
`python -m pytest` 325 passed, **0 warnings** (기존 2건의 DeprecationWarning 해소 확인) / `python -m ruff check src/` all checks passed.
`tests/test_dump_history_stats.py`의 `datetime.fromisoformat(entry["updated_at"])` 파싱 검증도 그대로 통과 (offset-aware ISO 문자열도 `fromisoformat`으로 정상 파싱됨).

---

## 2026-07-02 - F-141: v5.2 async wiring 단위 테스트 추가

### 구현 내용
`tests/test_dump_history_stats.py` 신규 작성 (7 tests) — F-140에서 손댄 async wiring 영역을 직접 커버:
- `_update_dump_history`: `load_sim_config`/`save_sim_config` mock — `last_dump_summary`/`updated_at`/`scope_overrides` strip/`dump_scopes` 기본값 검증
- `_lazy_discover_boundaries`: `tmp_path`에 실제 Yosys JSON netlist 작성 + config mock 4 케이스 (정상 파싱, `write_discovered_boundaries=True` 시 영속, `netlist_info` 없음, JSON 파일 미존재)
- `run_batch_regression` 3-test 케이스 (`T1=10, T2=5, T3=50` signal count) — `shell_run`/`poll_batch_log`/`_preprocess_setup_tcl`/`_update_dump_history`를 전부 mock하여 실제 함수를 end-to-end 구동:
  - 3개 테스트 모두에서 `_update_dump_history`가 호출되는지 확인 (Gap #3 회귀 방지)
  - `dump_stats`가 design.md §8 스펙(`max`/`min` = `{test,total}` dict, `per_test` = `total`/`top_boundary`/`block_count`, per-test named suggestion)과 정확히 일치하는지 확인 (Gap #2 회귀 방지)

### 패턴
`run_batch_regression`은 900줄 orchestrator라 실제 실행 경로를 태우려면 `shell_run` 호출 순서에 의존하지 않는 catch-all mock(`AsyncMock(return_value="")`)이 필요 — 정확한 call sequence를 하드코딩하는 대신 각 단계가 빈 문자열/성공 응답에도 안전하게 진행되도록 설계된 점을 활용함 (test_batch_helpers.py의 `_fake_shell` catch-all 패턴과 동일 계열, index 기반 대신 default-return 사용).

### 검증
`python -m pytest` 325 passed (318 → 325, 신규 7개) / `python -m ruff check src/` all checks passed.

---

## 2026-07-02 - F-140: v5.2 gap-fix — dump_history/dump_stats 스키마 정합

### 배경
`docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md` Gap #1-#3 (Important, Match Rate 85%의 API Contract 78%를 끌어내린 원인).

### 구현 내용
- `batch_runner.py` `_update_dump_history()`: 저장 키 `dump_summary` → `last_dump_summary`로 변경, `scope_overrides` strip, `updated_at`(UTC ISO, seconds) 추가 — design.md §7 스펙과 일치
- `run_batch_regression()` per-test 루프: `test_dump_summary`가 나올 때마다 `_update_dump_history` 호출 추가 — 이전에는 `run_batch_single` 경로에서만 갱신되어 regression 실행 후 `use_dump_history=True` 재사용이 불가능했음 (Plan §3.2 "항상 갱신" 정책 위반)
- `dump_stats` 집계: `max_total_signals`/`min_total_signals` bare int → `{"test":…, "total":…}` dict로 변경, `per_test` 엔트리를 `total`/`top_boundary`/`block_count`로 재구성, generic variance>0.5 메시지 대신 per-test `total > avg×2` named suggestion으로 복원 — design.md §8 pseudocode와 일치

### 하위 호환
`run_batch_single`의 read path(`history.get(test_name, {}).get("dump_scopes")`)는 `dump_scopes` 키만 사용 — `dump_summary`→`last_dump_summary` 리네임의 영향 없음. 확인됨.

### 검증
`python -m pytest` 318 passed / `python -m ruff check src/` all checks passed.

### 남은 작업
F-141: 이 async wiring(`_lazy_discover_boundaries`/`_update_dump_history`/`dump_stats`)에 대한 단위 테스트가 여전히 없음 — 분석서 "Coverage gap" 섹션이 정확히 지적한 사각지대. 순수 함수만 테스트되어 있어 이번 Gap #1-#3도 여기서 발생했었음.

---

## 2026-07-01 - F-138: v5.2 Phase 2 Auto-detection

### 구현 내용
- `sim_env_detection.py`: Phase 2 함수 3개 추가
  - `_parse_describe_output(scope, output)` — TCL `scope -describe -sort kind` 파싱, 비트범위 제거
  - `_boundaries_from_tcl(bridge, top_scope, depth, block_filter)` async — SimVision bridge 재귀 scope describe+show, `_SCOPE_PATH_RE` whitelist로 TCL injection 방지
  - `_boundaries_from_json(json_path, top_module, depth, block_filter)` — Yosys JSON modules/cells 계층 파싱, fnmatch block_filter 지원 (str도 자동 리스트 변환)
- `batch_runner.py`: `_lazy_discover_boundaries(sim_dir, dump_strategy, sim_mode)` 추가
  - `netlist_info.{mode}.boundary_json` → `_boundaries_from_json` Flow B 자동 호출
  - `run_batch_single`: `block_boundaries` 비어있고 `default_block_policy` 설정 시 자동 발동
- `tools/sim_lifecycle.py`:
  - `sim_bridge_run(auto_boundaries=False)`: SimVision 연결 후 `_boundaries_from_tcl` 호출 (Flow A)
  - `sim_discover(boundary_depth=3)`: config의 `dump_strategy.{rtl,gate}.boundary_depth` 저장

### 테스트
- `tests/test_hierarchical_dump.py`: 10개 신규 (Phase 1: 16 + Phase 2: 10 = 26개)
- **318/318 PASS**, ruff clean

### 학습
- `_parse_scope_item`은 `tools/signal_inspection.py`에 정의됨 — 순환 임포트 방지를 위해 `sim_env_detection.py`에 local copy `_parse_scope_item_local` 작성
- block_filter는 str 또는 list 모두 허용 (str → [str] 자동 변환)
- `_boundaries_from_json`에서 top_module 자체는 result에서 제외 (sub-block만 수집)
- `sim_discover` boundary_depth 저장: discovery 성공 후 update (result가 "ERROR"/"USER INPUT" 미시작 시)

---

## 2026-04-22 - F-136 + F-137

### F-136: checkpoint.py restore_checkpoint_impl /tmp fallback 제거
- `restore_checkpoint_impl()`: `except ValueError: resolved_dir = ""` → `return "ERROR: Project directory not configured. Run sim_discover first..."`
- BUG-A (예외 무시) + BUG-B (/tmp fallback) + BUG-C (manifest 검증 스킵) 동시 해결
- chk_base 분기 제거: `os.path.join(resolved_dir, "checkpoints")` 단일 경로
- 두 호출부(checkpoint tool, batch.py)는 이미 resolve 완료 상태로 호출 → 추가 수정 불필요

### F-137: Temp 파일 Cleanup 메커니즘
- `tmp_cleanup.py` 신규: `cleanup_old_logs(ttl=86400)`, `cleanup_session_logs()`
- `csv_cache.py`: SHM mtime 파일명 포함 영속 디스크 캐시; `_get_shm_mtime()`, `_cache_key(shm_mtime)`, `_default_output_path(shm_mtime)`, `extract()` disk hit 로직, `cleanup_stale_csv()`
- `-overwrite` 플래그 제거 (mtime-keyed 파일명이 unique)
- `bridge_lifecycle.py`: `start_bridge_simulation()` 시작 시 `cleanup_old_logs()` 호출
- `tools/batch.py`: `sim_batch_run` 시작 시 `cleanup_old_logs()`, 완료 후 `cleanup_stale_csv(shm_path)`
- `tools/sim_lifecycle.py`: `sim_disconnect(shutdown)` 시 `cleanup_session_logs()`
- `tools/waveform.py`, `debug.py`, `simvision_ops.py`: `ps_to_png()` 후 `os.unlink(ps_path)` (try/finally)
- 11 tests in test_f136_f137.py — 292/292 pass, ruff clean
cmd (F-110)
- SIGN-015: Wrong Tcl flag for operation variant (F-109)
- SIGN-016: Using stale ID lists instead of fresh parse (F-115)

**All tasks:** 125/125 passes=true. 258 pytest, ruff clean.

- Explicit display= still takes precedence; no VNC found returns clear error

**Learnings:**
- TCL `stop -create` supports `-object <sig>` (change on any value) vs `-condition <expr>` (comparison). These are mutually exclusive forms.
- `detect_vnc_display()` already existed in sim_env_detection.py — just needed to be called in compare_simvision (was only wired in start_simvision)
- MCP tool modules under `src/xcelium_mcp/tools/`
- BridgeManager DI, 7 tool modules (since v4.2)
- Dev deps: pytest, pytest-asyncio, ruff

## Key Files

- `src/xcelium_mcp/server.py` — MCP server entry point
- `src/xcelium_mcp/tools/` — tool implementations
- `tests/` — pytest test suite
- `pyproject.toml` — project metadata + ruff config

## Verification Command

```
python -m pytest && python -m ruff check src/
```

(Overridden by `verifyCommand` in plans/prd.json.)

---

### Task: Ralph loop — F-005, F-006 (structural refactor)

**Completed:**
- **F-005** — Split sim_runner.py (842→106 lines stub). New: discovery.py (446), bridge_lifecycle.py (343). Moved utils to shell_utils.py. Updated 13 files.
- **F-006** — Extracted 4 helpers from _run_batch_single: parse_existing_job, build_batch_cmd, launch_nohup_job, watch_pid_and_poll. Added 20 unit tests. Total: 81 tests.

**All 21 prd.json tasks complete.**

---

### Task: Ralph loop — F-020, F-021 (performance optimization)

**Completed:**
- **F-020** — Consolidated batch regression SSH calls: merged 2 base64 writes → 1, per-test grep → single glob grep
- **F-021** — TCP connect retry for bridge: first half uses direct TCP (0 subprocess), fallback to scan_ready_files

---

### Task: Ralph loop — F-019 (simvision extraction)

**Completed:**
- **F-019** — Extracted 7 module-level async functions from register() closures
  - open_database, start_simvision, setup_waveform, live_start, reload_waveform, compare_csv_diff, compare_simvision
  - Closure captures → explicit parameters
  - register() wrappers now thin dispatchers (8-19 lines each)
  - _load_rows helper also extracted to module level

---

### Task: Ralph loop — F-011 through F-018 (security + dedup batch)

**Completed (8 tasks):**

1. **F-011** — Tcl denylist bypass via semicolons/tabs: normalize whitespace, split on `;`/`\n`
2. **F-012** — deposit_signal value regex validation: `_DEPOSIT_VALUE_RE`
3. **F-013** — sim_run duration regex validation: `_DURATION_RE`
4. **F-014** — Replace all 67 `2>/dev/null` with `|| true` for tcsh compat
5. **F-015** — csv_cache CSV output moved to per-user tmp dir
6. **F-016** — `scan_ready_files()` helper extracted, 5 duplicates removed
7. **F-017** — `build_eda_command()` helper for EDA env sourcing
8. **F-018** — `DEFAULT_BRIDGE_PORT`, `BRIDGE_ERRORS`, `_PROTECTED_KEYS` consolidated

**Skipped:** F-019 (simvision extraction), F-020 (SSH consolidation), F-021 (inotifywait) — large refactors needing remote testing

---

### Task: Ralph loop — F-002 through F-010 (security + cleanup batch)

**Completed (6 tasks in single iteration):**

1. **F-002** — `compare_waveforms` injection + fd leak + validate_path
   - Added `_DISPLAY_RE` regex for display param validation
   - Switched simvision launch from bare `&` to `(nohup env ... &)` + `build_redirect`
   - Added `validate_path()` for shm_before/shm_after

2. **F-003** — `screenshot.py` temp file leak
   - Wrapped conversion + read in try/finally for guaranteed cleanup

3. **F-004** — `csv_cache.clear_cache` OrderedDict downgrade
   - Replaced dict comprehension with in-place key deletion

4. **F-008** — `debug_tools.generate_debug_tcl_content` Tcl escaping
   - Added `_tcl_escape()` helper for `"`, `[`, `$`, `\`
   - Signals sanitized, shm_path validated, context_note/labels escaped

5. **F-009** — Narrowed bare `except Exception` in connect/retry loops
   - 4 files: tcl_bridge.py, simvision.py, sim_lifecycle.py, sim_runner.py
   - All now catch `(ConnectionError, asyncio.TimeoutError, OSError, TclError)`
   - Last exception included in timeout error message

6. **F-010** — Dead code cleanup in register() return dicts
   - Removed `generate_debug_tcl_fn` param (unused in simvision)
   - Replaced lambda with `functools.partial` in waveform
   - debug.register returns None

**Remaining:** F-005, F-006 skipped (large structural refactors)

---

## 2026-04-10 - Session Notes

### Task: Ralph loop installation

**What was implemented:**
- Added ruff to dev deps in pyproject.toml
- Installed Ralph same-session mode scaffolding (hooks, commands, plans)

**Files changed:**
- pyproject.toml (ruff dev dep + [tool.ruff] config)
- .claude/hooks/stop-hook.sh (new)
- .claude/settings.json (new)
- .claude/commands/ralph-loop.md (new)
- .claude/commands/ralph-cancel.md (new)
- plans/progress.md (this file)
- plans/guardrails.md (seed signs)
- plans/prd.json (empty backlog)
- .gitignore (Ralph state files)

**Learnings:**
- `python -m pytest` / `python -m ruff` keeps verify command PATH-independent on Windows
- stop-hook.sh uses `eval` for VERIFY_COMMAND to allow `&&` chaining

---
