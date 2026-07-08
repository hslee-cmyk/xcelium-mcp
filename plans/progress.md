
---

## 2026-07-08 - F-182: _filter_test_names의 fnmatch 대소문자 플랫폼 불일치 수정

### 배경
code-analyzer 리뷰(bug/security/performance) Minor #1. `fnmatch.fnmatch()`는 두 인자 모두 `os.path.normcase`를 거쳐 Windows에서는 대소문자 무시, 실제 배포 대상인 cloud0(Linux)에서는 대소문자 구분 — 같은 `_filter_test_names` 내 substring 폴백 분기(항상 대소문자 구분)와도 불일치.

### 구현
- `tools/sim_lifecycle.py::_filter_test_names()`의 `fnmatch.fnmatch(t, pattern)` → `fnmatch.fnmatchcase(t, pattern)`로 교체
- 신규 테스트: 소문자 pattern(`*top015*`)이 대문자 포함 테스트명(`VENEZIA_TOP015_test`)과 매칭 안 됨을 검증

### 결과
613 tests passed(612→613), ruff clean. 기존 F-177 테스트 전부(대소문자 일치 케이스만 사용) 회귀 없음.

---

## 2026-07-08 - F-181: sim_regression 스키마 마이그레이션 O(N^2) thundering herd 수정

### 배경
code-analyzer 리뷰(bug/security/performance 관점) Important #1. `sim_regression`이 test_list 전체를 `asyncio.gather(*(resolve_test_name(t, ...) for t in test_list))`로 동시 호출하는데, 마이그레이션이 실제로 필요한 상황(schema_version 구버전)이면 N개 호출 전부가 독립적으로 전체 마이그레이션(+각자 내부에서 또 N개 의존성 스캔)을 실행 — N x (1+N) ~= O(N^2) grep/find subprocess.

### 구현
- `test_resolution.py`: `resolve_test_name()`의 body를 `_load_cached_tests(sim_dir)`(마이그레이션+config 로드, I/O 있음) + `_match_short_name(short_name, cached)`(순수 매칭 로직, I/O 없음) 두 개로 분리
- 신규 `resolve_test_names_batch(short_names, sim_dir)`: `_load_cached_tests`를 **한 번만** 호출한 뒤, 각 이름을 로컬에서 `_match_short_name`으로 매칭(추가 I/O 없음)
- `tools/batch.py::sim_regression()`이 `asyncio.gather(*(resolve_test_name(t,...) for t in test_list))` 대신 `resolve_test_names_batch(test_list, resolved_sim_dir)` 호출로 교체
- `sim_batch_run()`의 단일 테스트 resolve(`resolve_test_name` 직접 호출)는 원래부터 1개 호출이라 영향 없음, 변경 없음
- 신규 테스트 2개: 15개 테스트명으로 마이그레이션이 필요한 상황을 시뮬레이션해 `analyze_tb_type`/`shell_run`이 정확히 1회만 호출됨을 검증(이전엔 15회 호출됐을 것) + 이미 마이그레이션된 경우 마이그레이션 호출 자체가 없음을 검증

### 결과
612 tests passed(610→612, +2), ruff clean, 순환 임포트 없음. 일회성 비용(스키마 업그레이드 직후 딱 한 번) 최적화라 기능적 회귀 리스크는 낮음.

---

## 2026-07-08 - F-180: stdio_forward.py 블로킹 read 버그 수정 (priority 1, ralph-loop --next 픽)

### 배경
venezia-fpga 세션에서 `~/.claude.json`을 `ssh cloud0 xcelium-mcp` 직접 실행 방식에서 `stdio_forward.py`(supervisor+socket 아키텍처) 방식으로 컷오버하던 중 발견. 컷오버 직후 Claude Code가 "MCP server xcelium-mcp connection timed out after 30000ms"로 재연결 실패. cloud0에서 직접 재현: 메시지 전송 후 프로세스를 즉시 EOF로 종료시키면 정상 응답이 왔지만(오탐), stdin을 열어둔 채(실제 클라이언트처럼) 대기하면 최초 메시지조차 전달 안 됨을 확인.

### Root Cause
`stdio_forward.py:49`의 `stdin.read(_CHUNK_SIZE)`(`io.BufferedReader.read(n)`)는 n바이트(64KB)가 다 모이거나 EOF가 올 때까지 블로킹. 실제 MCP JSON-RPC 메시지는 수백~수천 바이트라 64KB에 한참 못 미치고, 연결도 EOF 없이 계속 열려있어 영원히 안 넘어감.

### 구현
- `stdin.read(_CHUNK_SIZE)` → `stdin.read1(_CHUNK_SIZE)`로 교체 — 단일 raw read만 수행하고 가용한 만큼 즉시 반환(EOF/버퍼풀 때까지 안 기다림), `sock.recv()`와 동일한 non-blocking-relay 계약
- 신규 `tests/test_stdio_forward.py`(4개): `os.pipe()`(가짜 stdin) + `socket.socketpair()`(가짜 worker 소켓)로 실제 블로킹 동작을 재현 — EOF 없이 작은 청크 즉시 전달, 여러 청크 순서대로, 64KB 초과 큰 페이로드 무손실 전달, EOF 시 SHUT_WR 전파(기존 동작 유지) 검증
- **회귀 테스트 자체 검증**: 수정 전 코드(`read1`→`read`)로 임시 되돌려서 `test_single_small_chunk_forwarded_without_eof`가 실제로 타임아웃으로 실패하는 것 확인 후 원복 — 테스트가 진짜 버그를 잡아내는지 실측
- `docs/02-design/features/xcelium-mcp-server-process-lifecycle.design.md` T-8 갱신: "메시지 후 즉시 EOF" 검증 방식이 이 버그를 놓친 근본 원인이었음을 회고로 기록, "연결을 열어둔 채" 검증하도록 시나리오 자체를 수정

### 결과
610 tests passed(606→610, +4), ruff clean. 이 버그가 고쳐지기 전까지 서비스는 옛 방식(`ssh cloud0 xcelium-mcp` 직접 실행)으로 되돌려둔 상태였음 — F-A(server-process-lifecycle) 컷오버는 이 수정 반영 후 재시도 필요(cloud0 배포 자체는 이번 세션 범위 밖).

### Learnings
- "메시지 1개 보내고 프로세스 종료"와 "연결을 계속 열어두고 메시지 주고받기"는 완전히 다른 코드 경로를 탄다 — 특히 블로킹 I/O가 관여하는 릴레이/포워더류 코드는 반드시 "연결 유지" 시나리오로 검증해야 함. 원래 PDCA T-8 검증이 전자로만 이루어져 이 버그가 사이클을 통과했던 것.

---

## 2026-07-08 - F-178/F-179: code-analyzer 리뷰(architecture+중복코드) 기반 리팩터

### 배경
`/code-review` → `bkit:code-analyzer` agent가 F-175/F-177 커밋을 architecture+중복코드 관점에서 리뷰. Major #1(순환 임포트는 증상, 진짜 문제는 `parse_test_discovery_output`이 leaf 함수인데 orchestration 모듈(test_resolution.py)에 잘못 놓여있는 것), Major #2(`schema_migration.py`가 `discovery.py` Phase A를 거의 그대로 복제 + discovery.py가 만드는 config에 `schema_version`이 없어 매번 불필요한 재마이그레이션 발생), Minor #3(`list_tests()`/`resolve_test_name()`의 마이그레이션+저장 패턴 중복) 발견. F-178(=#1+#2 통합, 리뷰가 "하나의 근본 수정으로 함께 해소된다"고 명시)/F-179(=#3)로 prd.json 등록 후 구현.

### 구현
- **신규 모듈** `src/xcelium_mcp/test_discovery_scan.py`: `parse_test_discovery_output`(test_resolution.py에서 이동) + `build_test_discovery_cmd` + `build_test_discovery_dict(sim_dir, tb_type)`(discovery.py Phase A와 schema_migration 양쪽이 재사용하는 공용 flow) — shell_utils/tb_provenance 외 프로젝트 내부 모듈 의존 없는 순수 leaf 모듈
- `discovery.py`: Phase A가 `build_test_discovery_dict()` 호출로 교체, `schema_version=CURRENT_TEST_DISCOVERY_SCHEMA_VERSION` 명시적 스탬프 추가(drift 버그 수정) — `schema_migration.CURRENT_TEST_DISCOVERY_SCHEMA_VERSION`을 import(schema_migration은 더 이상 discovery/test_resolution에 의존 안 하므로 순환 아님)
- `schema_migration.py`: `_migrate_v1_add_tb_type_and_file_map`이 `build_test_discovery_dict()` 재사용하도록 축소, F-179용 `ensure_and_persist_test_discovery(resolved_dir, config)` 헬퍼 신규 추가
- `test_resolution.py`: `parse_test_discovery_output` 정의 제거, `schema_migration` 모듈 레벨 import로 전환(순환 해소, 로컬 import 워크어라운드 제거), `resolve_test_name()`이 `ensure_and_persist_test_discovery()` 사용
- `tools/sim_lifecycle.py`: `list_tests()`도 동일하게 `ensure_and_persist_test_discovery()` 사용
- 신규 테스트: `tests/test_discovery_scan.py`(7개, build_test_discovery_dict/cmd 단위 테스트 — 이전엔 이 flow가 discovery.py/schema_migration.py 양쪽에 중복 구현된 채 어느 쪽도 직접 단위테스트가 없었음)
- 기존 테스트 patch 대상 갱신: `test_tb_provenance.py`(import 경로), `test_schema_migration.py`/`test_resolve_test_name_cache_miss.py`(mock patch가 `xcelium_mcp.schema_migration.*` → `xcelium_mcp.test_discovery_scan.*`로 이동한 내부 함수 따라감)

### 결과
606 tests passed(599→606, +7), ruff clean, `python -c "import xcelium_mcp.server"` 순환 임포트 없음 확인. F-144/F-145는 사용자 지시로 skip:true 처리(다른 프로젝트에서 검증 예정).

### Learnings
- code-analyzer 리뷰가 잡아낸 "discovery.py가 만드는 config엔 schema_version이 없다"는 발견은 실제로 신규 discovery 직후 매번 불필요한 전체 재마이그레이션(grep+의존성 스캔 재실행)을 유발하는 실질적 낭비였음 — 코드 두 곳이 "비슷해 보이는 로직"을 각자 구현하면 이런 drift가 리뷰 없이는 발견되기 어려움
- "prd에 기록만 해달라"는 요청과 "ralph-loop로 실행해달라"는 요청은 반드시 분리해서 처리 — 이번 세션에서 등록 직후 바로 구현에 들어갔다가 사용자에게 지적받음(관련 메모리 갱신 완료)

---

## 2026-07-08 - prd.json bookkeeping audit — 28개 pending 항목 중 27개가 이미 구현 완료 상태였음

### 배경
`/ralph-loop --next`가 F-144(priority 1)를 골랐는데, 코드를 확인해보니 이미 완전히 구현·테스트되어 있었음(`_parse_sim_time_ns`/`_to_number`/`_eval_condition`, `test_bisect.py` 32/32 통과). 우선순위 1 항목 F-145/F-148/F-174도 동일하게 이미 완료 상태임을 추가로 확인 — prd.json의 `passes:false` 기록이 코드 상태와 상당히 어긋나 있음을 발견. 사용자에게 보고 후, F-144/F-145는 "다른 프로젝트에서 검증 예정"이라 `false`로 유지하고 나머지는 직접 검증해 `true`로 정리하라는 지시를 받음.

### 방법
grep 카운트만으로는 신뢰 불가(F-165/F-176처럼 F-number를 코드/커밋 메시지에 인용하지 않고도 완료된 항목이 있었음 — 예: F-176은 skill 문서에 원칙이 다른 표현으로 이미 서술돼 있었음). 28개 pending 항목(F-144/F-145 제외) 전부를 개별적으로 acceptanceCriteria와 실제 소스 코드/테스트를 대조해 확인.

### 결과
- **27개 확인 완료** → `passes:true` + `completed_at` 갱신(대부분 "2026-07-08"로 기록했으나, F-173은 실제 작업일인 "2026-07-02"로 기록 — 아래 참조)
- **F-173** (registry.py `_dot_get`/`_dot_set`/`_dot_delete` 통합 검토): git log 확인 결과 **2026-07-02에 이미 검토·"리팩터 안 함" 결정이 notes에 기록된 커밋(d2690c8)이 존재**했으나, 그 커밋이 `notes`만 갱신하고 `passes:true`는 반영하지 않은 채 남아있었음 — acceptanceCriteria가 "리팩터 안 하기로 결정한 경우 notes 기록"을 유효한 완료 조건으로 명시하므로 `passes:true`로 정정(bookkeeping fix, 새 작업 아님)
- **F-144/F-145**만 최종적으로 `passes:false` 유지(사용자 지시 — 다른 프로젝트에서 검증 예정)
- 코드 변경 없음(순수 prd.json 기록 정정), 599 tests passed, ruff clean

### Learnings
- **F-number citation은 완료 여부의 신뢰할 수 있는 신호가 아니다** — grep으로 "0회 참조"였던 F-165/F-168/F-169/F-170/F-171/F-172/F-176도 전부 이미 완료 상태였음(코드 컨벤션상 F-number를 항상 주석에 남기는 건 아님). 반드시 acceptanceCriteria 각 항목을 실제 코드와 대조해야 함
- **git log가 prd.json 자체보다 더 정확한 기록**일 수 있다 — F-173처럼 실제 결정/작업은 있었는데 prd.json 필드 갱신만 누락된 케이스가 존재. 애매한 항목은 `git log --oneline -- plans/prd.json`으로 관련 커밋을 먼저 확인하는 게 grep보다 안전
- 이런 대규모 bookkeeping 드리프트가 왜 생겼는지는 불명(여러 세션/에이전트가 병렬로 prd.json을 건드린 정황 있음 — 이번 대화 중에도 사용자가 F-177을 대화 밖에서 직접 추가했고, F-146/147/152/153/174/175도 내가 손대지 않았는데 오늘 날짜로 이미 `passes:true`가 되어 있었음). 향후 ralph-loop 반복 전 최소한 `--next`가 고른 항목 하나는 코드 대조 검증을 먼저 하는 습관이 필요.

---

## 2026-07-08 - F-177: list_tests() pattern 필터 glob 지원

### 배경
venezia-fpga 세션에서 verilog-rtl-debugger agent가 F-175 schema migration gap(commit b6ad3c0) 검증 중 실전 재현 — `list_tests(pattern="*TOP01*")`가 실제로 매칭되는 테스트가 있는데도 "No tests found"를 반환. `tools/sim_lifecycle.py`의 `cached = [t for t in cached if pattern in t]`가 순수 substring 매칭이라 리터럴 `*`가 포함되지 않은 테스트명과는 매칭될 수 없었음.

### 구현
- `_filter_test_names(names, pattern)` 순수 헬퍼 신규 추가(모듈 레벨) — pattern에 glob 메타문자(`*`, `?`, `[`)가 있으면 `fnmatch.fnmatch`, 없으면 기존 substring 매칭으로 분기
- `list_tests()` 호출부를 이 헬퍼로 교체, docstring에 glob 지원 명시
- `fnmatch`는 whole-string 매칭이라 mid-string 검색엔 앞뒤 `*`가 필요함(테스트 작성 중 실수로 한번 놓쳤다가 수정 — 앞으로 유사 헬퍼 테스트 작성 시 유의)
- 테스트 6개 추가(`tests/test_sim_lifecycle.py`): glob 매칭, substring 회귀, `?`+`*` 혼합, `[...]` 브래킷, 빈 리스트, `list_tests()` 통합 레벨 1개
- prd.json F-177 `passes: true` 갱신

### 결과
pytest 599 passed(593→599), ruff clean.

### Learnings
- 이번 ralph-loop `--next` 실행 중, 우선순위 1인 F-144/F-145/F-148/F-174를 코드 대조해보니 **이미 전부 구현·테스트 완료된 상태인데 prd.json엔 `passes:false`로 남아있음**을 발견 — prd.json 북마킹이 상당히 stale함(코드는 고쳤지만 prd.json 갱신을 놓친 과거 세션들 추정). 사용자가 F-177(방금 막 추가된 신규 버그)을 먼저 지목해 그 이슈는 보류 중 — 다음 ralph-loop 반복 전에 prd.json 전체 재검증이 필요.

---

## 2026-07-06 - F-176 v3: self-healing 캐시 설명을 phase-0-discovery.md에 반영

### 배경
직전 self-healing 구현(의존 파일 목록이 primary sha256 변경 시 자동 재스캔)에 대해 사용자와 문답으로 정확한 동작(primary=test_name 정의 파일, dependency 파일과 혼동 없음)을 재확인한 뒤, F-176 문서(phase-0-discovery.md §0C)도 이 최신 동작에 맞춰 갱신 요청.

### 구현
`skill-src/xcelium-sim/references/phase-0-discovery.md` §0C "갱신 필요 판단"에 새 인용구 추가 — "의존 파일 '목록' 자체도 xcelium-mcp가 자동으로 최신 상태 유지 — Claude가 신경 쓸 필요 없음". 테스트 파일에 새 include/import를 추가·삭제해도 `sim_discover(force=True)`를 수동으로 다시 돌릴 필요가 없고, `tb_provenance`가 항상 최신 의존 파일 목록을 반영한다는 걸 명시 — Claude(문서 소비자) 입장에서 내부 캐싱 메커니즘을 몰라도 되지만, "새 include를 추가했으니 재discover 해야 하나?"라는 불필요한 혼란을 막기 위한 안내.

### 검증
문서만 수정, `python -m pytest` 526 passed(변화 없음), `python -m ruff check src/` all checks passed.

---

## 2026-07-06 - F-175 후속: 의존 파일 캐시에 self-healing 추가 — 테스트 파일 자체가 바뀌면(=include 목록이 바뀔 수 있는 시점) 자동 재스캔

### 배경
직전 커밋(discovery 시점 캐싱)에 대해 사용자가 "test file이 변경되면 hash가 바뀌는데, 그 시점에 include 파일이 추가/삭제될 수 있으니 재확인이 필요하지 않나" 지적. 정확한 지적 — discovery 시점에만 `cached_dependency_files`를 채우고 이후 갱신 메커니즘이 없으면, 테스트 자신의 파일을 수정해서 새로운 `` `include``를 추가/삭제해도 캐시된 의존 파일 목록이 영원히 stale 상태로 남음(재discover 전까지).

### 구현 — self-healing 캐시
- `cached_dependency_files`의 각 엔트리를 `[path, ...]` 단순 리스트에서 `{"scanned_primary_sha256": <스캔 당시 테스트 자신 파일의 sha256>, "deps": [path, ...]}`로 확장.
- `build_tb_provenance()`가 이미 매번 계산하는 "테스트 자신 파일의 현재 sha256"(`primary_checksum`)을 그대로 재사용해 `scanned_primary_sha256`과 비교 — 별도 계산 비용 없음(공짜 비교).
  - 일치(테스트 파일이 스캔 이후 안 바뀜) → 캐시된 `deps` 그대로 반환, find/grep 없음.
  - 불일치(테스트 파일이 바뀜 → include/import 줄도 바뀌었을 수 있음) 또는 캐시 자체가 없음 → `find_dependency_files()`를 지금 한 번 다시 실행하고, 결과를(새 sha256과 함께) config에 즉시 write-back.
- `tb_provenance.py`에 `scan_test_dependencies(primary_path, sim_dir) -> {"scanned_primary_sha256", "deps"}` 신규 — deps 스캔 + 파일 해시를 한 쌍으로 묶어 discovery 시점(3곳)에서 재사용. self-heal 경로(`resolve_cached_dependency_files`)는 호출자가 이미 계산해둔 sha256을 그대로 쓰고 `find_dependency_files()`만 다시 불러 중복 해시 계산을 피함.
- `discovery.py`/`test_resolution.py::resolve_test_name`/`tools/sim_lifecycle.py::list_tests` 3곳 모두 `find_dependency_files()` 직접 호출 대신 `scan_test_dependencies()`로 교체(같은 자리, 스키마만 변경).

### 검증
`tests/test_tb_provenance.py`: `TestResolveCachedDependencyFiles`를 self-healing 동작에 맞춰 재작성(3 tests) — 캐시 히트 시 shell_run 미호출(AssertionError로 회귀 방지), stale entry 시 정확히 1회 재스캔 후 새 sha256으로 write-back, 캐시 엔트리 자체가 없을 때도 정상 스캔. `TestBuildTbProvenance`의 기존 테스트들도 신규 스키마(`scanned_primary_sha256`)로 갱신 — 그중 "의존 파일만 바뀌고 테스트 자신은 안 바뀌는" 시나리오 테스트에 `shell_run` 호출 시 AssertionError를 추가해 "테스트 파일 불변 시 재스캔이 일어나지 않는다"를 명시적으로 검증. `test_resolve_test_name_cache_miss.py`도 `scan_test_dependencies` mock으로 갱신.

부수 발견: 초안에서 self-heal 경로가 호출자로부터 받은 `primary_sha256`을 쓰지 않고 `scan_test_dependencies()`로 다시 해시를 계산해 테스트가 실패 — 같은 파일을 굳이 두 번 해시하는 비효율이기도 해서, self-heal 경로만 `find_dependency_files()` 직접 호출 + 이미 받은 `primary_sha256` 재사용으로 수정.

`python -m pytest` 526 passed(525→526) / `python -m ruff check src/` all checks passed / `python -c 'import xcelium_mcp.server'` 순환 임포트 없음 확인.

TODO.md 갱신 — "의존 스캔 미캐싱"에 이어 "테스트 파일 변경 시 재스캔 안 됨"도 해결됨으로 표시. 남은 건 1단계 스코프 한계와 include 파일 basename 중복 두 가지만.

---

## 2026-07-06 - F-175 후속: 의존 파일 위치 탐색을 discovery 시점으로 캐싱 — 매 실행마다 find/grep 반복 제거

### 배경
직전 커밋(의존 파일 스코프 확장)에서 `find_dependency_files()`가 `build_tb_provenance()` 호출마다(즉 매 `sim_batch_run`/`sim_bridge_run`, `sim_regression` 테스트마다) `find`/`grep -rl`을 새로 실행하고 있다는 걸 사용자가 지적. 사용자 제안: `cached_test_files`와 같은 시점(discovery/cache-miss)에 "어디 있나"(경로)를 캐싱하고, 시뮬레이션마다 갱신해야 하는 "내용이 뭔가"(해시)는 지금처럼 매번 새로 계산 — 이 둘의 성격이 다르다는 원칙을 그대로 적용.

### 구현
- `test_discovery`에 `cached_dependency_files: {test_name: [dependency_path, ...]}` 신규 필드 — `cached_test_files`와 나란히, **정확히 같은 3곳**(`discovery.py` 최초 discovery, `test_resolution.py::resolve_test_name` cache-miss, `tools/sim_lifecycle.py::list_tests` cache-miss)에서 `cached_test_files`를 만든 직후 각 테스트에 대해 `find_dependency_files()`를 한 번씩 실행(`asyncio.gather`로 병렬화)해서 채움. `find`/`grep -rl`은 이제 이 3곳에서만 실행됨.
- `tb_provenance.py`: `resolve_cached_dependency_files(full_test_name, sim_dir)` 신규 — `resolve_tb_source_file()`과 대칭 구조로, `cached_dependency_files`를 순수 캐시 조회만 함(find/grep 없음). `build_tb_provenance()`가 `find_dependency_files()` 직접 호출을 이걸로 교체 — `resolved_dir` 재계산 코드도 불필요해져 함께 제거(단순화).
- 하위 호환: `cached_dependency_files` 필드가 없는(이 캐싱 이전) config는 빈 리스트로 안전하게 처리 — `tb_type` 없을 때와 동일 패턴, `sim_discover(force=True)` 재실행 시 채워짐.
- `find_dependency_files()` 자체(실제 스캔 로직)는 그대로 유지 — 호출 위치만 매 실행 hot path에서 discovery-time 3곳으로 이동.

### 검증
- `tests/test_tb_provenance.py`: `TestResolveCachedDependencyFiles` 신규(3 tests, `resolve_tb_source_file` 테스트들과 대칭). 기존 `TestBuildTbProvenance`의 의존파일 관련 테스트 2개를 `shell_run` mock 방식에서 `cached_dependency_files`를 config에 직접 주입하는 방식으로 갱신 — 그중 하나는 `shell_run` 호출 시 AssertionError를 던지도록 만들어 "build_tb_provenance가 절대 find/grep을 직접 호출하지 않는다"는 걸 명시적으로 회귀 방지.
- `tests/test_resolve_test_name_cache_miss.py`: `test_cache_miss_populates_cached_dependency_files` 신규 — cache-miss 시 `find_dependency_files`가 호출되고 그 결과가 `cached_dependency_files`에 정확히 저장되는지 확인(find_dependency_files 자체의 스캔 정확성은 이미 별도 테스트로 커버되므로 여기선 배선만 검증).

`python -m pytest` 525 passed(521→525, 신규 4개) / `python -m ruff check src/` all checks passed / `python -c 'import xcelium_mcp.server'` 순환 임포트 없음 확인(tb_provenance.py를 이제 discovery.py/test_resolution.py 양쪽에서 import하지만 tb_provenance.py는 registry.py/shell_utils.py 외 의존성이 없는 leaf 모듈이라 순환 없음).

TODO.md 갱신 — "의존 스캔 미캐싱" 항목을 해결됨으로 표시, 남은 건 1단계 스코프 한계와 include 파일 basename 중복 두 가지만.

---

## 2026-07-06 - F-175 후속: 해시가 테스트 파일 1개만 커버하던 스코프 gap 수정 — 직접 의존 파일(include/import)까지 확장

### 배경
사용자가 "hash 업데이트하는 부분에 추가로 고려할 사항"을 물어서 재검토하다 발견. F-175 prd 항목은 원래 "TB 소스 파일(들)"이라고 복수로 적혀 있었는데, 실제 구현은 `class Name extends uvm_test` 선언이 있는 파일 **하나만** 해싱하고 있었음. 실제 UVM 테스트는 보통 0A(공유 컴포넌트) — `` `include``/`import`되는 인터페이스·시퀀스·스코어보드 등 — 에 의존하므로, **테스트 자신의 파일은 안 바뀌고 그 테스트가 쓰는 공유 컴포넌트만 수정되면 이전 구현은 이 변경을 전혀 감지하지 못했음**.

### 구현
- `tb_provenance.py`에 `find_dependency_files(test_file, sim_dir)` 신규 — 테스트 파일 내용에서 `` `include "..."``/`import pkg::*;` 참조를 정규식으로 스캔한 뒤, `find {sim_dir} -name {basename}`(include) / `grep -rl 'package {pkg}'`(import)로 실제 경로를 best-effort 해석. **1단계만**(의존 파일이 또 무엇을 include하는지는 재귀적으로 안 펼침 — TODO.md에 스코프 명시).
- `build_tb_provenance()` 반환 형태 변경: `{"path": str, "sha256": str}` (단일) → `{"files": [{"path", "sha256"}, ...], "combined_sha256": str}`(테스트 자신의 파일이 첫 번째, 그 뒤로 해석된 의존 파일들 — `combined_sha256`은 전체 파일 목록을 path 정렬 후 이어붙여 재해시한 값, 하나라도 바뀌면 같이 바뀜).
- 신규 `format_tb_provenance()` 헬퍼 — MCP tool 출력 텍스트 포맷팅을 한 곳에 모아 `sim_batch_run`/`sim_bridge_run` 양쪽에서 재사용(기존엔 각자 f-string으로 중복 포맷).
- `tools/batch.py`/`tools/sim_lifecycle.py`의 `tb_source['path']`/`tb_source['sha256']` 직접 접근을 `format_tb_provenance()` 호출로 교체.
- `phase-0-discovery.md` 0C 절 갱신 — 비교 대상이 `tb_source.sha256`(단일)에서 `tb_source.combined_sha256`(집계)으로 바뀌었음을 반영, 1단계 스코프 한계 명시.

### 검증
`tests/test_tb_provenance.py`에 신규 클래스 2개 추가:
- `TestFindDependencyFiles`(5 tests) — include 없음/`` `include`` basename 해석/`import` package 해석/미해석 참조 스킵/파일 못 읽음
- `TestBuildTbProvenance`에 2개 추가 — 의존 파일이 `files`에 포함되는지, **테스트 자신의 파일은 그대로인데 의존 파일만 바뀌어도 `combined_sha256`이 바뀌는지**(이번에 고친 gap의 핵심 회귀 방지 assertion)
- `TestFormatTbProvenance`(2 tests) 신규
- 기존 provenance 관련 테스트들의 `path`/`sha256` 단일 필드 assertion을 `files`/`combined_sha256`로 갱신

`python -m pytest` 521 passed(512→521, 신규 9개) / `python -m ruff check src/` all checks passed / `python -c 'import xcelium_mcp.server'` 순환 임포트 없음 확인.

### 남은 작업
TODO.md에 이어서 기록: (1) 의존 스캔이 1단계까지만이라 2단계 이상 떨어진 의존성은 미검출, (2) include 파일 basename 중복 시 첫 매치 채택(기존 클래스명 중복과 동일 계열 리스크), (3) 의존 스캔 자체가 캐싱 안 돼서 매 호출마다 find/grep 재실행(성능, 실측된 문제는 아직 아님) — 전부 낮은 우선순위로 보류.

---

## 2026-07-06 - F-175 후속: sim_regression의 tb_provenance 계산 타이밍 버그 수정

### 배경
사용자가 "checksum이 만들어지는 시점"을 캐물으면서 발견. `sim_regression`은 `run_batch_regression` 완료 후 **한 번에 test_list 전체**의 provenance를 `asyncio.gather`로 계산하고 있었음(F-175 최초 구현 당시 `tools/batch.py` 쪽 코드). 즉 test A, B가 같은 공유 TB 파일을 쓸 때, A가 끝나고 B가 실행되는 도중 그 파일이 수정되면 — A의 provenance로 기록되는 sha256이 실제 A 컴파일 시점이 아니라 **regression 전체가 끝난 시점(=B 실행 후 최종 상태)**을 반영하는 부정확성이 있었음. `sim_batch_run`/`sim_bridge_run`은 테스트 1개만 다루므로 이 문제가 없었지만 `sim_regression`만 해당.

### 구현
- `batch_runner.py::run_batch_regression()`: 반환 타입을 `tuple[str, dict|None]` → `tuple[str, dict|None, dict[str, dict]]`로 확장. per-test 루프 안, `completed_tests.append(test_name)` 직후(해당 테스트 자신의 poll 완료 직후, 다음 테스트로 넘어가기 전)에 `build_tb_provenance()`를 호출해 `per_test_tb_provenance[test_name]`에 즉시 기록 — 이전엔 이 호출이 `save_checkpoints=True`일 때만 체크포인트 등록 블록 안에서 이뤄졌는데, 이제 무조건 한 번만 계산해서 체크포인트 등록에도 재사용(중복 계산 제거).
- `tools/batch.py::sim_regression`: `run_batch_regression`이 돌려주는 `tb_provenance`를 그대로 사용하도록 변경 — 기존에 있던, regression 완료 후 `test_list` 전체를 다시 `asyncio.gather`로 훑는 별도 계산 블록 삭제.
- 기존 테스트 2곳(`test_dump_history_stats.py`, `test_regression_result_collection.py`)의 3-tuple unpacking 업데이트.

### 검증
`tests/test_regression_tb_provenance_timing.py` 신규 작성(2 tests):
- provenance 계산이 T1 poll 완료 직후 곧바로 일어나고(T2가 시작되기도 전에), T2도 마찬가지 순서로 일어나는지 call-order로 직접 검증
- T1·T2가 같은 TB 파일을 공유하고 그 사이 파일이 바뀌는 상황을 mock으로 재현 — T1의 provenance가 T2 실행 후의 최신 값이 아니라 T1 자신이 실행됐을 때의 값(OLD_HASH)으로 정확히 기록되는지 확인(수정 전이었다면 이 assertion이 실패했을 것)

`python -m pytest` 512 passed(510→512, 신규 2개) / `python -m ruff check src/` all checks passed / `python -c 'import xcelium_mcp.server'` 순환 임포트 없음 확인.

---

## 2026-07-06 - F-176 v2: 체크섬의 실제 용도 재정정 — "로컬 사본 검증"이 아니라 "캐시된 분석서 staleness 판단"

### 배경
F-176 v1(아래 섹션)에서 "로컬 사본이 있으면 체크섬으로 검증 후 써도 된다"고 적었는데, 사용자가 두 차례 질문으로 이 전제 자체를 짚어냈다: (1) "로컬 사본이 왜 필요하지?" — F-175 notes를 다시 보면 애초에 사건 이후 "로컬엔 사본을 두지 않기로" 이미 결정돼 있었다. 즉 "로컬 사본을 신뢰해도 되는 조건"을 문서화하는 것 자체가 잘못된 전제(로컬 사본의 존재를 용인)였다. (2) "체크섬은 현재 analysis 문서를 업데이트할 필요가 있는지 판단하기 위한 것 아닌가?" — 정확한 지적. 0C(캐시 관리 규칙)의 "갱신 시점: 해당 파일이 수정되었을 때만"이라는 규칙엔 애초에 "어떻게 아는지" 메커니즘이 없었는데, 체크섬이 정확히 그 자리에 맞는 도구였다.

### 수정
- **0-Prep-2 단순화**: "체크섬 비교 후 로컬 사본 조건부 사용" 절차를 완전히 삭제. "로컬 프로젝트 저장소 경로는 조건 없이 읽지 않는다 — 항상 ssh-mcp로 sim_discover가 resolve한 sim_dir을 직접 읽는다"는 무조건 규칙으로 교체.
- **0C(캐시 관리 규칙)에 체크섬의 진짜 용도 추가**: 분석서 작성 시점의 `tb_source.sha256`을 분석서 헤더에 기록해두고, 다음 재사용 시 현재 실행의 sha256과 비교 — 일치하면 캐시 재사용, 다르면(또는 기록이 없으면) 재작성. "지금 실행이 만든 결과가 캐시된 분석서 작성 시점의 TB 소스와 같은가"를 매번 전체 재분석 없이 싸게 판단하는 게 목적이라는 점을 명시.

### 검증
`python -m pytest` 510 passed(변화 없음, 문서만 수정), `python -m ruff check src/` all checks passed.

---

## 2026-07-06 - F-176: 문서 — TB 분석 시 로컬 저장소 사본 대신 MCP-resolve 경로를 우선하는 원칙 명시 (review only)

### 배경
F-175와 짝을 이루는 문서화 태스크. venezia-fpga 세션에서 로컬 stale TB 사본을 근거로 TB 분석서를 작성했다가 실제 cloud0 TB와 내용이 달라 근본원인 서술이 틀어졌던 사례(2026-07-06) — F-175가 코드 측(체크섬 provenance)을 추가했다면, F-176은 그 provenance를 실제로 어떻게 활용해야 하는지 방법론 문서에 명시하는 짝.

### 구현
`skill-src/xcelium-sim/references/phase-0-discovery.md`에 "0-Prep-2. TB 소스 읽기 원칙 — 로컬 사본 신뢰 금지" 신규 절 추가(0-Prep과 0A 사이):
- 원칙: 로컬 프로젝트 저장소의 동일 파일명 TB 사본을 신뢰하지 말고, `sim_discover`/`mcp_config`가 resolve한 실제 sim_dir을 ssh-mcp(`file_read`/`file_grep`)로 직접 읽는 것을 기본 경로로 함
- 적용 순서: F-175의 `tb_source`/`tb_provenance`(경로+sha256)가 있으면 로컬 사본의 체크섬과 먼저 비교 → 일치할 때만 로컬 사본 사용. 체크섬 비교 불가 시(F-175 이전 config 등) 무조건 MCP-resolve 경로를 직접 읽음
- `verilog-tb-analyst`/`verilog-rtl-debugger` agent(chip-design-skills repo 소유)도 같은 원칙을 따르려면 해당 문서에 별도 반영 필요함을 명시(이 태스크는 xcelium-mcp의 skill-src만 대상, chip-design-skills 쪽은 범위 밖)

코드 변경 없음(review only, acceptance criteria대로).

### 검증
`python -m pytest` 510 passed(변화 없음, 문서만 수정), `python -m ruff check src/` all checks passed.

이것으로 F-174~176(2026-07-02 code-analyzer 리뷰 이후 발견된 실제 버그/기능 3건) 전부 처리 완료. `prd.json`은 프로젝트 규칙상 `passes:false`로 유지 — 사용자 확인 대기.

---

## 2026-07-06 - F-175: 기능 — sim_batch_run/sim_regression/sim_bridge_run TB 소스 provenance(경로+sha256) 기록

### 배경
venezia-fpga 세션에서 verilog-rtl-debugger agent를 T-002(TOP014 V-13 FIFO overflow 커버리지 갭) 실사례로 테스트하던 중 발견(2026-07-06). 로컬 venezia-fpga repo에 xcelium-mcp가 실제로 시뮬레이션에 쓰는 TB 소스(venezia-t0/cloud0)와 같은 이름의 stale 사본이 있었고, 내용이 달라(로컬은 drain 로직 포함, cloud0는 미포함) TB 분석의 근본원인 서술이 틀어졌었다. "지금 분석 중인 TB 소스가 실제로 그 결과를 만든 파일과 같은지" 사후 검증할 방법이 전혀 없었던 게 근본 문제 — 이번 F-175가 그 검증 수단(경로+sha256 체크섬)을 추가한다.

### 설계 결정 — 별도 필드 대신 test_discovery 확장
초기 설계안은 "특정 test_name을 정의한 파일을 찾는 별도 함수(grep -l 재실행)"였으나, 사용자가 "이미 test_discovery가 test 목록을 만들 때 grep으로 소스를 훑는데 왜 새 매커니즘을 또 만드냐"고 지적 — 코드 확인 결과 정확히 맞는 지적이었다. `discovery.py`가 test 이름을 뽑아낼 때 이미 `grep -rh 'extends uvm_test'|'^\s*program '` (uvm/sv_directed) 또는 `ls tb_tests/*.v`(legacy)로 TB 소스를 스캔하고 있었는데, `-h`(파일명 숨김) 플래그 때문에 클래스/프로그램 **이름만** 남기고 정의 파일 경로는 버리고 있었다. `-h`→`-n`(파일명:라인번호)으로 바꾸기만 하면 같은 grep 결과에서 파일 경로까지 얻을 수 있어, 별도 grep을 추가하지 않고 기존 test_discovery 메커니즘을 확장하는 방향으로 설계를 변경했다.

### 구현
- **`test_resolution.py`**: `parse_test_discovery_output(raw, tb_type) -> dict[name, file]` 신규 — uvm(`{file}:{line}:...class {Name} extends uvm_test`)/sv_directed(`{file}:{line}:program {Name}`)/legacy(bare path, name=stem) 3가지 포맷 파싱을 한 곳에 통합. `resolve_test_name()`의 cache-miss 재실행 경로가 이 파서를 사용해 `cached_tests`뿐 아니라 신규 `cached_test_files`도 채우도록 변경.
- **`discovery.py`**: test_cmd 생성을 `-h`+`sort -u`/`-o` 파이프라인 → `-n`(raw) 명령으로 단순화(파싱은 이제 Python 쪽 `parse_test_discovery_output`이 전담). `test_discovery` config에 `tb_type`(재파싱 시 포맷 선택용) + `cached_test_files` 필드 신규 추가.
- **`tools/sim_lifecycle.py list_tests`**: 동일하게 cache-miss 경로에서 `cached_test_files`도 갱신하도록 변경 — discovery.py/resolve_test_name/list_tests 3곳 모두 같은 파서를 재사용(로직 중복 없음).
- **`registry.py`**: `_PROTECTED_KEYS`에 `test_discovery.tb_type` 추가(command와 동일하게 sim_discover만 갱신 가능 — 사용자가 mcp_config로 바꾸면 파싱 포맷이 깨짐).
- **`tb_provenance.py` 신규 모듈**: `resolve_tb_source_file()`(cached_test_files 조회) + `compute_file_sha256()`(이 코드베이스 최초의 content-hash — 기존 `compute_compile_hash`는 mtime만 해시) + `build_tb_provenance()`(둘을 묶어 `{"path", "sha256"}` 반환, 실패 시 None — best-effort, 절대 에러로 표면화하지 않음).
- **`tools/batch.py`**: `sim_batch_run`은 반환 텍스트에 `tb_source: {path} (sha256: ...)` 블록 추가. `sim_regression`은 이미 resolve된 `test_list` 전체에 대해 병렬로 provenance를 모아 `tb_provenance: {test_name: {path, sha256}, ...}` JSON 블록 추가(테스트별 개별 기록, 공통 TB 파일 공유 테스트는 자연히 동일 체크섬).
- **`tools/sim_lifecycle.py sim_bridge_run`**: 동일하게 반환 텍스트에 `tb_source` 추가. 추가로 `bridges.current_test_name`/`bridges.current_tb_source`에 저장(bridge 모드는 test_name 파라미터가 없는 `checkpoint(action=save)`가 나중에 참조할 수 있도록).
- **`bridge_manager.py`**: `BridgeManager`에 `current_test_name`/`current_tb_source` 필드 신규, `set_xmsim(None)`(disconnect) 시 초기화.
- **`checkpoint_manager.py register_checkpoint()`**: `tb_source: dict | None = None` 파라미터 추가, 제공 시 manifest entry에 저장(None이면 키 자체를 안 만들어 기존 리더와 하위호환). `compile_hash`(재컴파일 감지)를 대체하지 않고 보완.
- **`batch_runner.py`**: regression의 L1 자동 체크포인트 등록(`save_checkpoints=True`, test_name 이미 알고 있는 유일한 checkpoint 등록 지점)에서 `build_tb_provenance()` 호출 후 `tb_source`로 전달.
- **`tools/checkpoint.py` `_save_impl`**: bridge 모드 `checkpoint(action=save)`가 `bridges.current_test_name`/`current_tb_source`를 `register_checkpoint()`에 전달.

### 하위 호환
`cached_test_files`가 없는 기존(pre-F-175) config는 `resolve_tb_source_file()`이 `None`을 반환 → `build_tb_provenance()`도 `None` → 3개 tool 모두 `tb_source`/`tb_provenance` 섹션을 조용히 생략(에러 없음). `register_checkpoint(tb_source=None)`도 manifest에 키를 추가하지 않아 기존 스키마 소비자에 영향 없음.

### 검증
- `tests/test_tb_provenance.py` 신규 22 tests — `parse_test_discovery_output` 3개 포맷(uvm/sv_directed/legacy) + 잘못된 라인 스킵, `compute_file_sha256`(hashlib 기준값 일치/동일 내용 동일 해시/수정 후 해시 변경/파일 없음 시 None), `resolve_tb_source_file`/`build_tb_provenance`(캐시 히트/미스/config 없음, 공유 TB 파일 두 테스트 동일 체크섬 — F-175 acceptance criterion, 파일 수정 후 재실행 체크섬 변경 — F-175 acceptance criterion), `register_checkpoint(tb_source=...)` 저장/생략, `BridgeManager` 기본값/disconnect 시 초기화.
- `tests/test_resolve_test_name_cache_miss.py` 신규 2 tests — cache-miss 시 `cached_test_files` 채워짐, cache-hit 시 재실행 없음(회귀 없음).
- `sim_batch_run`/`sim_regression`/`sim_bridge_run` 3개 MCP tool 자체는 `register()` 클로저 내부라 직접 통합테스트 대신, 세 tool 모두가 호출하는 공유 함수 `build_tb_provenance()`를 단위 테스트로 철저히 검증하는 방식으로 동등한 보증 확보(체크섬 안정성/변경감지 로직은 3곳 모두 동일 함수를 통하므로 중복 검증 불필요).
- `python -m pytest` 509 passed (485→509, 신규 24개) / `python -m ruff check src/` all checks passed / `python -c 'import xcelium_mcp.server'` 순환 임포트 없음 확인.

### 남은 작업
F-176(TB 분석 시 MCP-resolve 경로 우선 원칙 문서화, review only, 코드 변경 없음) — 미착수.

---

## 2026-07-06 - F-174: 버그 수정 — poll_batch_log 완료 판별 키워드 오탐(조기 반환) 수정

### 배경
venezia-fpga 세션에서 TOP015(`VENEZIA_TOP015_i2c_8bit_offset_test`) `sim_batch_run` 실행 중 실전 재현(2026-07-03). `sim_batch_run`이 xmsim이 실제로 종료되기 전(수 초 만에) "completed"를 반환했으나, SSH로 직접 확인한 결과 xmsim이 95.9% CPU로 계속 실행 중이었고 실제로는 2분 30초 후에야 `Errors: 0`으로 정상 종료. Root cause: `batch_polling.py::poll_batch_log()`의 완료 판별이 `"$finish"`/`"PASS"`/`"FAIL"` **단순 substring** 매칭이었는데, venezia-t0의 `scripts/setup_rtl_batch.tcl` 1번째 줄 주석("... run to $finish (no MCP bridge)")이 xmsim `-input` 처리 시 로그 최상단에 그대로 echo되어 첫 poll(P6-1 interval 2.0s 시작) 시점에 `tail -10`이 이 주석 줄만 포함하고 있어도 즉시 완료로 오판됨. 개별 assertion 로그(`[V-18] PASS: ...`)도 동일 계열의 잠재적 오탐 지점(별도 위험, plan §1.2(B)). 상세: `docs/01-plan/features/xcelium-mcp-batch-poll-false-completion.plan.md`.

### 구현 (F-1, 필수 항목만 — F-2 2차 확인 로직은 plan에서 "검토 후 결정" 항목으로 남겨두고 이번엔 미적용)
- `batch_polling.py`에 `_COMPLETION_MARKERS = ("Simulation complete via $finish", "COMPLETE", "Errors:")` 모듈 상수 추가
- 완료 판별 조건을 `kw in out for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")` → `kw in out for kw in _COMPLETION_MARKERS`로 교체 — `$finish` 단독 substring 제거(xmsim이 실제 종료 시에만 출력하는 `"Simulation complete via $finish"` 고정 문구로 앵커링), `PASS`/`FAIL` 단독 substring은 완전히 제거(개별 assertion 로그와 구분 불가능 — `done_file` + `COMPLETE`/`Errors:`에 위임)
- L44-45(최종 결과 추출 grep)는 plan F-3 판단대로 변경 없음 — 조기 종료 버그(F-1)가 해소되면 이 grep이 조기 반환된 스크립트 주석 한 줄 대신 정상적으로 전체 PASS/FAIL 요약을 받게 되므로 별도 수정 불필요

### 검증
`tests/test_poll_batch_log.py` 신규 작성(4 tests, `poll_batch_log`를 직접 단위 테스트하는 최초 파일 — 기존엔 모든 caller가 이 함수 자체를 mock만 해왔음):
- TCL 주석에 `$finish`만 포함된 로그로 2회 poll해도 조기 종료하지 않고 timeout까지 진행하는지
- 개별 `[V-18] PASS: ...` 라인만 있고 `COMPLETE`/`Errors:`가 없을 때 조기 종료하지 않는지
- `"Simulation complete via $finish(1) at time ..."` 포함 시 정상적으로 완료 판정되는지(회귀 방지)
- `done_file`(`__DONE__`) 존재 시 키워드 매칭과 무관하게 즉시 완료 판정되는 기존 동작이 유지되는지

`python -m pytest` 485 passed (481→485, 신규 4개) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-175(TB 소스 provenance 체크섬 기록), F-176(TB 분석 시 MCP-resolve 경로 우선 원칙 문서화, review only) — 미착수. plan의 F-2(키워드 fast-path 2차 확인)는 리스크/비용 대비 낮은 우선순위로 판단해 이번엔 스킵 — 필요 시 별도 태스크로 분리 검토.

---

## 2026-07-02 - Verbosity 코드 리뷰 → F-163~173 (11건) 백로그 추가 + F-163 구현

code-analyzer를 "코드가 불필요하게 길게/장황하게 작성되었는가"에만 집중하는 렌즈로 실행(23개 파일, High 2 / Medium 4 / Low 5). High-Impact #2(batch.py 검증 중복), Low #7(`_write_json` 죽은 코드), Low #9(중복 `import re`), Medium #4(`read_setup_tcl` pass-through 래퍼)는 Claude가 직접 코드를 읽어 재확인 완료. 사용자가 "모두 prd로 추가하고 고치자" 지시 → F-163~173 전부 `passes:false`로 추가, 순차적으로 `/ralph-loop --next`로 구현 시작.

### F-163: test_pure_helpers.py의 debug_snapshot 테스트 5개 보일러플레이트 → fixture 통합

`tests/test_pure_helpers.py:531-713`의 `debug_snapshot` 관련 테스트 5개(에이전트 보고는 6개라 했으나 실제로는 5개)가 각각 mock_mcp/mock_bridges 생성 + `_fake_execute` 정의 + `register()` 호출 + 결과 언랩까지 거의 동일한 ~20줄 보일러플레이트를 반복하고 있었음.

**구현**: `run_snapshot` pytest fixture 추가 — `responses: dict[cmd, str|Exception]`를 받아 `_MockMCP`+`MagicMock` 브릿지를 구성하고 `debug_snapshot` 툴을 등록·호출해 report 문자열을 반환. `call_log` 옵션으로 호출된 Tcl 커맨드 순서도 필요 시 캡처 가능(첫 테스트가 `describe`/`value` 별도 호출이 없음을 검증하는 데 필요). 5개 테스트 모두 fixture를 사용하도록 축소 — 각 테스트는 이제 입력(`responses`)과 기대 assert만 남음.

**검증**: `pytest tests/test_pure_helpers.py -k debug_snapshot` 5 passed. 전체 `pytest` 461 passed(테스트 개수 불변, 순수 리팩터), `ruff check src/` clean.

### F-164: tools/batch.py — sim_batch_run/sim_regression의 dump_depth/sdf_corner/dump_scopes 검증 중복 제거

Claude가 직접 코드 확인 완료: `sim_batch_run`(93-117라인)과 `sim_regression`(251-267라인)이 검증 로직을 글자 그대로 중복하고 있었고, 변수명(`_re_ds`/`_re_ds2`, `_valid_ds_values`/`_valid_ds_values2`, `_key_re`/`_key_re2`)까지 접미사로만 구분한 상태 — 두 곳이 따로 진화하며 검증이 어긋날 sync-drift 위험이 실질적으로 있었음.

**구현**: `tools/batch.py` 모듈 레벨에 `_validate_run_params(dump_depth, sdf_file, sdf_corner, dump_scopes) -> str | None` 추출(에러 메시지 문구는 기존과 동일하게 유지). 두 툴 함수 모두 `err = _validate_run_params(...); if err: return err` 패턴으로 교체, 로컬 `import re as _re_dsN`도 제거하고 모듈 상단 `import re`로 통일. `tests/test_validate_run_params.py` 신규 작성(6 tests) — 유효/무효 dump_depth·sdf_corner(sdf_file 설정 시에만 검사되는 조건 포함)·dump_scopes key/value 케이스.

**검증**: `pytest` 467 passed(461→467), `ruff check src/` clean, `python -c 'import xcelium_mcp.server'` 성공.

### F-165: simvision_ops.py compare_simvision — try/except BRIDGE_ERRORS 3개 블록 통합

`compare_simvision` 내 `database open`/`__WAVEFORM_ADD__ BEFORE`/`__WAVEFORM_ADD__ AFTER` 3곳이 `try/except BRIDGE_ERRORS as e: return f"...: {e}"` 패턴을 그대로 반복하고 있었음.

**구현**: 로컬 async 클로저 `_try(cmd, errmsg) -> str | None` 추가(성공 시 None, 실패 시 `f"{errmsg}: {e}"` 반환). 3개 호출부 모두 `err = await _try(cmd, errmsg); if err: return err` 패턴으로 축소. 에러 메시지 문구는 기존과 동일하게 유지.

**검증**: `pytest` 467 passed(변화 없음, 순수 리팩터라 신규 테스트 불필요 — 기존 compare_simvision 관련 테스트가 이미 이 경로를 커버), `ruff check src/` clean.

### F-166: tcl_preprocessing.py의 read_setup_tcl 불필요한 pass-through 래퍼 제거

`read_setup_tcl()`은 `_read_setup_tcl_sync()`를 그대로 감싸기만 하는 3중 레이어 중 하나였음(죽은 코드는 아니고 `batch_runner.py:729`가 유일 호출부).

**구현**: `tcl_preprocessing.py`에서 `read_setup_tcl()` 삭제, `read_setup_tcl_async()`는 유지(asyncio.to_thread로 감싸는 실질적 역할이 있음). `batch_runner.py`의 import를 `read_setup_tcl` → `_read_setup_tcl_sync`로 변경, 호출부도 함께 수정.

**검증**: `pytest` 467 passed(변화 없음), `ruff check src/` clean, `python -c 'import xcelium_mcp.server'` 성공, grep으로 다른 참조 없음 재확인.

### F-167: batch_runner.py — dump-history scope 로딩 로직이 run_batch_single/run_batch_regression 사이 중복

`run_batch_single`(469-475라인)과 `run_batch_regression`의 per-test 루프(811-822라인)가 "use_dump_history이고 명시적 dump_scopes가 없으면 config의 dump_history에서 조회" 로직을 거의 그대로 중복.

**구현**: `_update_dump_history` 바로 아래에 `_history_scopes(sim_dir, test_name) -> dict | None` 추출(설정 로드 실패 시 None 반환하는 기존 non-fatal 동작 유지, 빈 dict `{}` → `or None`으로 falsy 처리하는 동작도 동일하게 유지). 두 호출부 모두 `effective_*_scopes = await _history_scopes(sim_dir, test_name)`로 축소.

**검증**: `tests/test_batch_helpers.py`에 `TestHistoryScopes` 5 tests 추가(기록된 scopes 반환/기록 없음/빈 dict/config 로드 실패/None config). `pytest` 472 passed(467→472), `ruff check src/` clean.

### F-168: 테스트 헬퍼 _make_inspect_tool 미재사용 4곳 + shell_run_with_retry mock-wiring 중복 2곳

`_make_inspect_tool` 헬퍼(F-132 섹션에서 정의)가 있었는데, 그보다 앞선 F-131/F-135 섹션의 테스트 4개와 뒤쪽 F-133 섹션의 테스트 1개(`test_inspect_signal_list_scope_prefixes_threaded`)가 이를 재사용하지 않고 register+patch 인라인 패턴을 반복하고 있었음. 별도로 `shell_run_with_retry` 테스트 2개(`retries_on_timeout`, `raises_after_max_retries`)가 `patch("xcelium_mcp.shell_utils.asyncio")` 6줄짜리 mock-wiring 블록을 반복.

**구현**:
- `_make_inspect_tool`을 F-131/F-135 섹션 최상단(첫 사용 지점 바로 위)으로 이동, F-132 섹션의 중복 정의 삭제. 5개 테스트(4개 앞선 섹션 + scope_prefixes_threaded) 모두 헬퍼를 사용하도록 축소.
- `shell_run_with_retry` 섹션에 `_patched_asyncio_passthrough()` 컨텍스트매니저(`@contextmanager`) 추가 — 2개 테스트 모두 사용하도록 축소.

**검증**: `pytest` 472 passed(변화 없음, 순수 리팩터), `ruff check src/` clean. 개별로 `-k "inspect_signal or recursive_list"` 8 tests 재확인.

### F-169: registry.py의 _write_json 죽은 코드 제거

grep으로 재확인 결과 자기 정의 외 호출부 전혀 없음(`_write_json_sync`만 실사용).

**구현**: `_write_json()` 삭제.

**검증**: `pytest` 472 passed(변화 없음), `ruff check src/` clean, `python -c 'import xcelium_mcp.server'` 성공, grep 재확인.

### F-170: batch_polling.py watch_pid_and_poll의 미사용 pid 파라미터 + 과도한 docstring 정리

`pid` 파라미터는 docstring 스스로 "unused but kept for future use"라고 명시할 정도로 실제 미사용이었음(호출부 `run_batch_single`도 `pid` 값을 로깅 등에 쓰지 않고 오직 이 호출에만 넘김). 2줄짜리 함수 본문 대비 Args 섹션까지 갖춘 과도한 docstring도 함께 정리.

**구현**: `watch_pid_and_poll(pid, log_file, job_file, timeout)` → `watch_pid_and_poll(log_file, job_file, timeout)`로 시그니처 축소, docstring을 한 줄로 축약. 호출부(`run_batch_single`)에서 `pid = await launch_nohup_job(...)` 대입도 불필요해져 `await launch_nohup_job(...)`로 변경(반환값이 다른 곳에서 쓰이지 않음을 확인). `tests/test_batch_helpers.py`의 `test_watch_pid_and_poll_returns_result`에서 `pid=42` 인자 제거.

**검증**: `pytest` 472 passed(변화 없음), `ruff check src/` clean, `python -c 'import xcelium_mcp.server'` 성공.

### F-171: batch_runner.py의 중복 'import re as _re2' 제거

모듈이 이미 17라인에서 `import re as _re`를 하는데, `_extract_ts_from_log` 내부(구 711라인)에서 로컬로 `import re as _re2`를 또 하고 있었음.

**구현**: 로컬 import 삭제, `_re2.search(...)` → `_re.search(...)`로 변경(모듈 상단 `_re` 재사용).

**검증**: `pytest` 472 passed(변화 없음), `ruff check src/` clean.

### F-172: debug_tools.py — 코드를 그대로 재서술하는 주석들이 CLAUDE.md 컨벤션 위반

`generate_debug_tcl_content()`의 `# Validate shm_path`, `# Sanitize signals`, `# Open SHM waveform`, `# Add debug signals`, `# Zoom to bug region`, `# Main cursor at bug time`, `# Named markers`, `# AI context note` 등이 바로 다음 코드가 이미 하는 일을 그대로 재서술하고 있었음 — CLAUDE.md의 "WHAT을 설명하는 주석 금지" 컨벤션 위반. 주의: `lines.append("# Add debug signals")` 같은 문자열 리터럴은 **생성되는 Tcl 파일에 실제로 출력될 내용**(사용자가 나중에 SimVision에서 보는 주석)이라 이건 건드리지 않음 — 삭제 대상은 Python 소스 레벨 `#` 주석만.

**구현**: 위 파이썬 소스 주석 8곳 삭제(생성 콘텐츠 문자열은 전혀 변경 없음). PRD 원 범위는 76-98라인이었으나, 같은 함수 내 101/110 근방의 동일 패턴(재서술 주석)도 일관성을 위해 함께 정리.

**검증**: `pytest` 472 passed(변화 없음, 생성 Tcl 콘텐츠 문자열은 그대로라 출력 동일), `ruff check src/` clean.

### F-173: registry.py `_dot_get`/`_dot_set`/`_dot_delete` — 검토 후 리팩터 안 함으로 결정

acceptanceCriteria가 "추출하거나, 추출이 오히려 복잡도를 높인다면 현행 유지 사유를 notes에 기록"으로 양쪽 다 허용하는 형태라 코드를 직접 분석.

**분석**: 세 함수가 표면적으로는 "dot-path 순회"를 공유하는 것처럼 보이지만, 실제로는 (1) 순회 범위(parts 전체 vs `parts[:-1]`), (2) 중간 노드 부재 시 동작(auto-vivify 생성 vs 실패), (3) 실패 시 반환값(`_MISSING` sentinel vs `bool`)이 모두 다름. 공용 헬퍼로 묶으려면 이 3축을 전부 파라미터화해야 하고, 그러면 호출부마다 플래그 조합을 시뮬레이션해야 이해 가능한 코드가 되어 현재의 독립된 7줄 내외 함수 3개보다 오히려 가독성이 떨어짐.

**결정**: 리팩터하지 않음. `prd.json` F-173의 `notes`에 판단 근거 전문 기록.

**검증**: 코드 변경 없음(문서만), `pytest` 472 passed(불변), `ruff check src/` clean.

이것으로 verbosity 코드 리뷰(F-163~173, 11건) 전부 처리 완료 — 10건 구현 + 1건(F-173) 검토 후 현행 유지 결정. 전체 리팩터를 거치며 `pytest`는 461(리뷰 시작 시점) → 472로 증가(순수 리팩터가 대부분이었고, F-164/F-167에서만 신규 테스트 추가), `ruff check src/`는 매 커밋마다 clean 유지.

**남은 작업**: 없음(11건 전부 처리). F-163~172(10건)는 `passes:false`로 사용자 확인 대기, F-173은 리팩터 없이 결론만 기록했으므로 별도 코드 검증 불필요.

### F-160 보류 (사용자 결정)
code-analyzer 아키텍처 리뷰 Major #6(`BOUNDARY_SIGNALS` 프로젝트별 하드코딩)은 F-155~159와 달리 **순수 리팩터가 아니라 기본 동작을 바꾸는 breaking change**임을 재확인 — `_resolve_probe_signals`의 `dump_strategy.top_boundary` 미설정 시 폴백이 v5.1 backward-compat의 핵심 경로이자 venezia 프로젝트의 실사용 경로(`tests/test_dump_strategy.py`, `tests/test_hierarchical_dump.py`가 이 폴백을 명시적으로 테스트). 원안대로 "빈 리스트+에러"로 바꾸면 venezia의 `.mcp_sim_config.json`에 `top_boundary`가 먼저 마이그레이션돼 있지 않은 한 기존 `dump_depth="boundary"` 호출이 전부 깨짐. 사용자에게 확인 결과 **보류** 결정 — `plans/prd.json`에 `skip:true` + 결정 근거 기록, ralph-loop 자동 진행 대상에서 제외. 재개 시 마이그레이션 전략부터 별도 논의 필요.

### F-162: 아키텍처 리팩터 — registry._config_cache 격리 + config_action 캐시 무효화

code-analyzer 아키텍처 리뷰 Minor #8. `registry._config_cache`(모듈 레벨 mutable 캐시, `sim_dir → (mtime, config)`)에 리셋 훅이 없어 테스트 격리가 암묵적이었고, `config_action`의 project-config(`file != "registry"/"checkpoint"`) 분기가 `save_sim_config()`를 거치지 않고 raw `_write_json_sync`로 직접 쓰던 것(F-159에서 만든 `_write` 클로저 구조의 "else" 분기)이 캐시 무효화를 mtime 우연 일치에 의존하게 만들었음.

**구현 내용**:
- `registry.py`에 `reset_caches()` 추가 — `_config_cache.clear()`, 테스트 fixture에서 사용
- `config_action`의 "else" 분기 `_write(d)` 클로저를 `save_sim_config(sim_dir, d)` 경유로 변경 — `_config_cache.pop()`이 mtime 무관하게 명시적으로 실행됨

**검증**: `tests/test_config_cache_invalidation.py` 신규 작성(3 tests) — 캐시에 stale 엔트리를 강제로 심어둔 뒤(실제 mtime 충돌을 기다리지 않고 버그 시나리오를 결정론적으로 재현) `config_action(set/delete)` 후 캐시가 실제로 무효화되고 다음 `load_sim_config`이 최신 값을 반환하는지 확인, `reset_caches()` 동작 확인.
`python -m pytest` 461 passed(458→461) / `python -m ruff check src/` all checks passed.

### 아키텍처 리뷰 백로그 마무리
F-155~159, F-162 전부 완료. F-160만 breaking change라 사용자 결정으로 보류. F-161(선행)까지 포함해 이번 세션에서 아키텍처 리뷰 8건 중 7건 처리 완료.

---

## 2026-07-02 - F-159: 아키텍처 리팩터 — checkpoints/manifest.json 단일 writer화

### 배경
code-analyzer 아키텍처 리뷰(2026-07-02, Major #5), Claude가 직접 코드 확인. `checkpoint_manager.py`가 `_read_manifest`/`_write_manifest`로 manifest 스키마(`compile_hash`, `checkpoints`, `tb_analysis_cache`)를 관리하는데, `registry.config_action`의 `file=="checkpoint"` 분기가 완전히 별개 경로로 같은 파일을 raw `Path.read_text`/`_write_json_sync`로 직접 읽고 썼음 — `mcp_config` tool을 통해 AI가 스키마 지식 없이 manifest를 손상시킬 수 있는 실질적 데이터 무결성 리스크였음.

### 구현 내용
- `registry.py`에 `checkpoint_manager` 모듈 최상단 import 추가(순환참조 없음 확인 — checkpoint_manager는 xcelium_mcp 내부 의존성이 전혀 없는 leaf 모듈)
- `config_action()`을 각 분기(`registry`/`checkpoint`/그 외 project config)마다 `_write(d)` async 클로저를 정의하는 구조로 재구성 — `checkpoint` 분기의 read는 `checkpoint_manager._read_manifest(sim_dir)`, write는 `checkpoint_manager._write_manifest(sim_dir, d)`를 경유
- dot-path 데이터 접근(`_dot_get`/`_dot_set`/`_dot_delete`)은 그대로 유지 — I/O 경로만 교체

### 부수 발견 — 버그 수정
`_write_manifest`는 `checkpoints/` 디렉터리가 없으면 `mkdir(parents=True)`로 생성하는데, 기존 raw `_write_json_sync`는 디렉터리 생성을 안 해서 **한 번도 checkpoint를 저장한 적 없는 sim_dir에서 `mcp_config(file='checkpoint', action='set', ...)`을 먼저 호출하면 `FileNotFoundError`로 죽었을 것** — 통합하면서 이 엣지케이스도 함께 해소됨.

### 검증
`tests/test_config_action_checkpoint.py` 신규 작성(4 tests, 이전엔 `config_action`에 대한 테스트가 전무했음) — 디렉터리 미존재 시 정상 생성, `checkpoint_manager.register_checkpoint()`로 쓴 데이터가 `config_action(get)`으로 보이는지(단일 writer 증명), `config_action(set)`이 기존 `compile_hash`/`checkpoints` 스키마를 훼손하지 않는지(read-modify-write 증명), `config_action(delete)`가 실제 manifest에 반영되는지 확인.
`python -m pytest` 458 passed(454→458) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-160(`BOUNDARY_SIGNALS` 이전), F-162(캐시 격리) — 미착수.

---

## 2026-07-02 - F-158: 아키텍처 리팩터 — build_eda_command 일원화 (batch_runner + simvision_ops)

### 배경
code-analyzer 아키텍처 리뷰(2026-07-02, Major #4). `shell_utils.build_eda_command`가 이미 있는데 `batch_runner._resolve_exec_cmd`, `simvision_ops.start_simvision`, `simvision_ops.compare_simvision` 3곳이 `source_separately` 분기를 각자 인라인 재구현 — F-017(2026-04-10, "5곳 중복 제거" 완료 처리)이 있었음에도 이후 코드에서 재발한 사례.

### 구현 내용 — batch_runner.py
- `_resolve_exec_cmd()`의 인라인 `source_separately` 분기(join 구분자 `' && '`)를 `build_eda_command()` 호출로 교체
- 부수적으로 버그 1건 수정: 기존 인라인 코드는 `source_separately=True` + `env_files=[]`(빈 리스트)일 때 `sources=""`가 되어 `"{env_shell} -c ' && {script_run}'"` 같은 **선두 `&&`가 남는 malformed 명령**을 만들었음 — `build_eda_command`는 `source_separately and env_files` 둘 다 확인해 이 경우 정상적으로 login_shell_cmd 경로로 폴백

### 구현 내용 — simvision_ops.py
- `_launch_simvision(runner, display, inner_cmd_parts, log_name, launch_timeout)` 신설 — DISPLAY 설정 + `build_eda_command()` 래핑 + nohup 실행 + log_file 경로 반환을 캡슐화
- `start_simvision()`/`compare_simvision()` 둘 다 신설 헬퍼 사용하도록 교체 (각각 ~20줄의 거의 동일한 인라인 블록 제거)
- `start_simvision()`은 반환된 `log_file`을 에러 메시지(로그 tail)에 계속 사용 — 헬퍼가 값을 반환하도록 설계해 이 요구를 충족

### 의도적 동작 변화 (검토 후 안전하다고 판단, 문서화)
`build_eda_command`는 `{sources}; {inner_cmd}` 순서로 조립 — 기존 `simvision_ops`의 수동 조립은 `setenv DISPLAY`를 sourcing보다 먼저 실행했으나, 통합 후에는 sourcing이 먼저, `setenv DISPLAY`가 그 다음. X11 앱은 자기 실행 시점에 DISPLAY를 읽고, EDA env 스크립트는 DISPLAY에 의존하지 않으므로 실질적으로 무해하다고 판단 — `_launch_simvision` docstring에 명시.

### 검증
- `tests/test_batch_helpers.py`에 `_resolve_exec_cmd`의 `source_separately` 분기 테스트 3개 추가(기존엔 이 분기 테스트가 전무 — join 구분자 drift와 empty-env_files 버그가 발견 안 된 이유). 빈 env_files 케이스가 malformed 명령을 만들지 않는지 명시적으로 검증.
- `tests/test_launch_simvision.py` 신규 작성(3 tests) — `_launch_simvision`이 source_separately 분기/일반 분기 모두 올바른 명령을 만드는지, DISPLAY가 sourcing 뒤에 오는지, 지정한 timeout이 전달되는지 검증.
- `python -m pytest` 454 passed(448→454) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-159(manifest 단일 writer), F-160(`BOUNDARY_SIGNALS` 이전), F-162(캐시 격리) — 미착수.

---

## 2026-07-02 - F-157: 아키텍처 리팩터 — launch_nohup_job을 run_batch_regression 경로에서 재사용

### 배경
code-analyzer 아키텍처 리뷰(2026-07-02, Major #3). `run_batch_single`은 `launch_nohup_job()` 헬퍼를 쓰는데, `run_batch_regression`의 per-test 루프는 nohup-subshell + `echo $! > pid_file` + PID 읽기 + job-file 쓰기 + fire-and-forget watcher 시퀀스를 인라인으로 재구현하고 있었음(주석에 "P6-5b", "F-020" 등 서로 다른 커밋 태그가 붙어 있어 이미 따로 진화 중이었음을 시사).

### 구현 내용
- `launch_nohup_job()`에 `extra_job_fields: dict | None = None` 파라미터 추가 — job JSON dict를 base 필드(`pid`/`log_file`/`test_name`/`started_at`) 생성 후 `.update()`로 병합. regression 쪽은 `type`/`current`/`current_log`/`completed`/`test_list`를 추가하고, `log_file`은 base(=`test_log`, 인자로 넘긴 per-test 로그)를 **의도적으로 override**해 실제로 필요한 aggregate regression log 값을 유지
- `run_batch_regression`의 인라인 nohup 블록(약 30줄)을 `launch_nohup_job()` 호출 1개로 교체

### 의도적 동작 변화 2건 (검토 후 안전하다고 판단, 회귀 아님)
1. **pgrep fallback 추가**: `launch_nohup_job`은 pid-file 읽기 실패 시 `pgrep -f {test_name}`로 재시도하는데, 기존 regression 인라인 코드엔 이 fallback이 없었음 — PID 캡처 실패 시 더 견고해지는 방향의 개선(`run_batch_single`이 이미 누리던 안전장치를 regression도 갖게 됨)
2. **PID=0일 때 job-file 미생성**: `launch_nohup_job`은 `if pid:` 블록 안에서만 job-file을 쓰는데, 기존 regression 코드는 PID=0이어도 무조건 job-file을 썼음(watcher만 조건부). 다만 `_read_job_status`/`_should_resume_regression`이 이미 `pid<=0`을 "dead"로 취급하므로 resume 판단 결과는 동일 — 오히려 무의미한 pid=0 job-file을 안 남기는 쪽이 더 깔끔함
3. timeout guard의 `if pid_str.strip().isdigit():` → `if test_pid:`로 단순화 — PID=0("0".isdigit()이 True라 원래는 `kill -0 0`을 시도할 뻔했던 엣지케이스)을 안전하게 걸러냄, 코드베이스 전반의 "pid must be > 0" 컨벤션과 일치

### 검증
`tests/test_batch_helpers.py`에 `test_launch_nohup_extra_job_fields_merged_and_override_base` 추가 — 성공 케이스(pid>0)에서 실제 작성되는 job JSON을 파싱해 병합된 필드(type/current/current_log/completed/test_list)와 override된 log_file이 정확한지 직접 검증(이 시나리오는 기존 blanket-mock 테스트들이 pid=0 조기 반환 경로만 태워서 커버 못 하던 부분).
`python -m pytest` 448 passed(447→448) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-158(`build_eda_command` 일원화), F-159(manifest 단일 writer), F-160(`BOUNDARY_SIGNALS` 이전), F-162(캐시 격리) — 미착수. 이걸로 batch_runner.py의 job-lifecycle 관심사(F-155/156/157) 통합은 마무리.

---

## 2026-07-02 - F-156: 아키텍처 리팩터 — job-resume 로직 중복(parse_existing_job vs run_batch_regression) 통합

### 배경
code-analyzer 아키텍처 리뷰(2026-07-02, Major #2). `parse_existing_job()`(단일 테스트 resume)과 `run_batch_regression()`의 인라인 resume 블록이 `cat job_file → json.loads → kill -0 → ALIVE/DEAD` 시퀀스를 각자 따로 구현하고 있었음.

### 설계 결정 — 부분 통합(원안에서 스코프 조정)
당초 "match predicate를 파라미터화해 완전 통합"을 acceptance criteria로 썼으나, 실제 코드를 보니 두 함수의 **반환 형태 자체가 다름**: `parse_existing_job`은 "이 테스트 하나의 최종 결과 문자열"을 반환하고, regression 쪽은 "이미 완료된 테스트 목록 + log_file/ts 조정값"을 반환 — resume 판단 후의 처리(단일 결과 반환 vs 리스트 갱신)가 근본적으로 다른 반환 계약이라 억지로 하나의 함수로 합치면 오히려 가독성이 떨어짐. 대신 **진짜 중복된 부분만** — read+parse+PID-alive-check — `_read_job_status(job_file) -> tuple[dict, bool] | None`로 추출하고, 그 이후의 resume 판단/처리 로직은 각 caller에 그대로 유지. `_kill_stale_sim`은 애초에 이미 공유되고 있었음(변경 없음).

### 구현 내용
- `_read_job_status()` 신설 — `parse_existing_job` 직전에 배치
- `parse_existing_job()`과 `run_batch_regression()`의 인라인 resume 블록 둘 다 `_read_job_status()` 호출로 교체, 이후 판단 로직은 그대로 유지
- shell_run 호출 시퀀스/횟수는 기존과 완전히 동일 — 순수 리팩터

### 검증
`tests/test_batch_helpers.py`에 `_read_job_status` 직접 테스트 5개 추가(파일 없음/invalid JSON/alive/dead/PID=0가 kill -0 호출 없이 dead 처리되는지). 기존 `parse_existing_job` 테스트 전부(9개) 회귀 없이 통과.
`python -m pytest` 447 passed(442→447) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-157(`launch_nohup_job` regression 경로 재사용), F-158~160, F-162 — 미착수.

---

## 2026-07-02 - F-155: 아키텍처 리팩터 — run_batch_regression에서 순수 로직(verdict 분류/dump_stats 집계) 추출

### 배경
code-analyzer 아키텍처 리뷰(2026-07-02, Critical #1). `run_batch_regression`(921줄 파일 중 400줄)이 job-resume, per-test 실행, nohup/PID 플러밍, checkpoint 주입, 결과 수집과 함께 **순수 로직**(5-way verdict 분류, dump_stats 집계)까지 섞여 있어, 이 로직만 테스트하려 해도 매번 전체 regression 파이프라인을 mock해야 했음(F-140~152에서 반복된 패턴).

### 구현 내용
- `classify_regression_results(test_list, per_test_results, per_test_errors, log_file) -> str` 신설 — 기존 5-way verdict 분류 + summary 문자열 생성 로직(약 85줄) 그대로 이전, 순수 함수
- `aggregate_dump_stats(per_test_dump_summaries) -> dict | None` 신설 — 기존 dump_stats 집계 로직 그대로 이전, 순수 함수
- `run_batch_regression()`의 해당 블록을 두 함수 호출로 교체 — 로직 완전히 동일, 반환값(`log_str`, `dump_stats`) 형식 불변
- 두 함수 모두 `_should_resume_regression` 근처(`run_batch_regression` 바로 앞)에 배치해 "regression 헬퍼" 그룹 유지

### 검증
`tests/test_regression_classification.py` 신규 작성(15 tests) — **mock 전혀 없이** 순수 함수 직접 호출로 5-way 분류(pass/fail-via-complete/fail-via-FAIL/complete/error-via-finish/error-via-timeout) 전부 + dump_stats 집계(outlier suggestion, block_count 필터링, 빈 입력) 검증. 테스트 작성 중 두 가지를 발견/정정: (1) "no $finish" 케이스도 waveform_total에 집계됨(에러 버킷이 waveform_total에 포함), (2) "0/N tests classified" fallback은 test_list가 완전히 비어있을 때만 도달 가능(개별 테스트가 빈 결과여도 `=== tn ===` 헤더가 남아 raw가 비지 않음) — 둘 다 기존 동작 그대로, 리팩터로 인한 변경 아님.
`python -m pytest` 442 passed(427→442) / `python -m ruff check src/` all checks passed. 기존 `tests/test_regression_summary.py`, `tests/test_dump_history_stats.py`, `tests/test_regression_result_collection.py` 전부 회귀 없이 통과 — 이 함수들을 경유하는 end-to-end 테스트라 리팩터가 동작을 안 바꿨음을 재확인.

### 남은 작업
F-156~160(batch_runner.py job-resume/launch_nohup_job 통합, build_eda_command 일원화, manifest 단일 writer, BOUNDARY_SIGNALS 이전), F-162(캐시 격리) — 전부 미착수.

---

## 2026-07-02 - F-161: 아키텍처 리팩터 — resolve_sim_dir/get_default_sim_dir을 discovery.py → registry.py로 이동

### 배경
code-analyzer 에이전트의 아키텍처 리뷰(2026-07-02, Minor #7)에서 발견. `resolve_sim_dir`/`get_default_sim_dir`이 `load_registry()`만 쓰는데 무거운 `discovery.py`(runner_detection, sim_env_detection 등 의존)에 있어서, 여러 모듈이 순환참조를 피하려고 함수 내부에서 lazy import 중이었음. 아키텍처 리뷰가 권장한 선행 작업(F-155~160 리팩터 전에 먼저) — priority 3이지만 다른 태스크의 전제조건이라 먼저 진행.

### 구현 내용
- `registry.py`에 `get_default_sim_dir()`/`resolve_sim_dir()` 이동 (registry는 tcl_bridge 외 의존성 없는 leaf 모듈이라 어디서 import해도 순환 위험 없음)
- `discovery.py`에서 두 함수 제거, 내부 사용처는 `from xcelium_mcp.registry import get_default_sim_dir`로 재import
- 실제로는 review가 집계한 4곳보다 많은 **lazy import 6곳** 발견 및 정리: `registry.py`(config_action 자기 자신 호출이라 import 자체 제거), `test_resolution.py`, `csv_cache.py`(2곳), `tools/signal_inspection.py`, `tools/waveform.py` — 전부 module-level import로 전환
- module-level import였던 `bridge_lifecycle.py`, `simvision_ops.py`, `tools/batch.py`, `tools/checkpoint.py`, `tools/debug.py`, `tools/sim_lifecycle.py`도 import 출처를 `discovery` → `registry`로 갱신
- 부수 정리: `csv_cache.py`의 인접 lazy import(`load_sim_config`, `build_eda_command`)도 같은 줄을 만지는 김에 module-level로 정리(F-161 범위는 아니지만 동일 패턴이라 함께 처리)
- `test_resolution.py`의 stale 주석("sim_runner → batch_runner → sim_runner" — F-005로 이미 사라진 모듈 언급) 제거

### 검증
`python -c "import xcelium_mcp.server"` 순환 임포트 없음 확인. `tests/test_regression_summary.py`가 `patch("xcelium_mcp.discovery.get_default_sim_dir", ...)`로 패치하는 기존 테스트 2개가 여전히 통과함을 확인 — `discovery.py`가 재import로 해당 이름을 자기 네임스페이스에 유지하고 있어 patch 경로가 그대로 유효.
동작 변경 없는 순수 리팩터라 `python -m pytest` 427 passed(테스트 수 불변) / `python -m ruff check src/` all checks passed.

### 남은 작업
F-155~160(아키텍처 리뷰 나머지 6건, 이번 F-161이 전제조건이었음), F-162(캐시 격리) — 전부 미착수.

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
