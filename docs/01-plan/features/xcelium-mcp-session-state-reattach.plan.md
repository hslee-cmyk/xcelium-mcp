# xcelium-mcp-session-state-reattach Plan

> **Feature**: (1) 워커 재시작(SSH 끊김·idle-culler 등) 후 F-C로 같은 xmsim에 재연결해도
> `current_test_name`/`tb_source`(TB provenance)가 새 워커의 빈 메모리로 초기화되어, 재연결 후
> `checkpoint(action=save)`가 TB provenance를 빈 값으로 기록하는 문제(F-D). (2) idle-culler(F-B)가
> TCP 브릿지 없이 순수 로그 폴링만 하는 `sim_batch_run`/`sim_regression` 워커를 "idle"로 오판해
> 장시간 batch/regression 도중 죽일 수 있는 문제(F-E). Design §2.4 "향후 승격 경로 1단계"의 최소
> 구현 + F-B의 사각지대 보완.
>
> **Date**: 2026-07-07
> **Status**: Draft
> **Found in**: `xcelium-mcp-server-process-lifecycle` 완료 보고 직후, "SSH 끊김 중에도 시뮬레이션이
> 끝까지 도는가"라는 질문에 답하는 과정에서 (1) `verilog-rtl-debugger` agent(chip-design-skills
> 정본, `agents/verilog-rtl-debugger.md`)의 Phase 4E "AI 자율 디버깅 루프"가 F-D gap에 실제로
> 노출됨을 확인, (2) 이어서 "sim_batch_run/sim_regression은 어떤가"라는 후속 질문에 답하는 과정에서
> F-E(idle-culler 오판 위험)를 발견

---

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | **F-D**: `sim_lifecycle.py:325-326`의 `sim_bridge_run`이 `current_test_name`/`tb_source`(TB 소스 파일 경로+sha256)를 워커 프로세스의 **인메모리 `BridgeManager`에만** 기록한다. `checkpoint.py:142-143`은 bridge 모드 저장 시 이 값만으로 TB provenance(F-175)를 기록하는데, 워커가 재시작되면(SSH 끊김 등) 이 메모리가 사라진다. **F-E**: idle-culler(F-B)의 `has_established_tcp()`는 "TCP 브릿지가 없으면 idle"로 판단하는데, 순수 `sim_batch_run`(체크포인트 restore 없이)/`sim_regression`은 애초에 TCP 브릿지를 열지 않는다(순수 `shell_run` 로그 폴링) — 6시간(기본 임계값) 넘는 regression을 폴링 중인 워커가 실제로는 idle이 아닌데도 죽을 수 있다. |
| **Solution** | **F-D**: 이미 F-C가 만들어둔 `sim_dir` 키 레지스트리 엔트리(`registry.py`)에 `current_test_name`/`tb_source`를 같이 저장해두고, `connect_simulator(sim_dir=...)`가 레지스트리 direct-hit로 재연결에 성공할 때 그 값을 새 워커의 `BridgeManager`에 복원한다. **F-E**: idle_culler가 죽이기 전에 `/tmp/xcelium_mcp_{uid}/batch_job.json`/`regression_job.json`을 확인해, 기록된 PID가 살아있으면(=진행 중인 batch/regression이 있으면) 이번 라운드는 전체 워커를 건너뛴다. |
| **Function/UX Effect** | `verilog-rtl-debugger`의 Phase 4E 자율 루프 중 세션이 끊겼다 재연결돼도 체크포인트의 TB provenance가 정확히 유지된다(F-D). 장시간 batch/regression이 idle-culler에 의해 부당하게 중단되지 않는다(F-E). `checkpoint.py`는 무변경. |
| **Core Value** | `verilog-rtl-debugger.md` §2가 명시적으로 경고하는 "TB 소스 drift로 근본원인 서술이 틀어졌던 실사례"(2026-07-06)를 재연결 시나리오에서도 재발하지 않게 막고(F-D), F-B(idle-culler) 도입이 만든 새로운 사각지대(F-E)를 배포 전에 막는다. |

---

## 1. Problem Detail

### 1.1 정확한 코드 경로

```python
# sim_lifecycle.py:325-326 (sim_bridge_run 성공 직후)
bridges.current_test_name = test_name
bridges.current_tb_source = tb_source   # {"files":[{"path","sha256"},...], "combined_sha256"}
```

```python
# checkpoint.py:142-143 (checkpoint(action=save), bridge 모드)
test_name=bridges.current_test_name,
tb_source=bridges.current_tb_source,
```

두 필드의 소비처는 이 checkpoint 기록 한 곳뿐(grep으로 확인, 2026-07-07). 즉 이 값이 비어있으면
"bridge 모드로 저장한 체크포인트가 어떤 test/TB 소스로 만들어졌는지"를 알 방법이 없어진다.

### 1.2 재현 시나리오

1. `verilog-rtl-debugger`가 `sim_bridge_run(test_name="TOP015", sim_dir=X)` 호출 → 워커A의
   `bridges.current_test_name="TOP015"`, `current_tb_source={...}`.
2. Phase 4E 자율 디버깅 루프가 길게 도는 중(bisect 반복, waveform 분석 등) SSH가 순간 끊김 →
   `ServerAliveInterval`(xcelium-mcp-server-process-lifecycle 완료분) 만료로 워커A 종료.
3. Claude Code가 재연결 → 수퍼바이저가 새 워커B를 fork. 워커B의 `BridgeManager`는 완전히 새 인스턴스
   (`current_test_name=""`, `current_tb_source=None`).
4. `verilog-rtl-debugger`가 (같은 xmsim에 F-C로 재연결한 뒤) 진행 상황을 `checkpoint(action=save)`로
   저장 → **TB provenance 없이 기록됨** — F-175가 막으려던 "TB 소스가 뭔지 나중에 알 수 없는" 상황
   재발.

### 1.3 verilog-rtl-debugger.md와의 연관

`agents/verilog-rtl-debugger.md` §2 공통 규칙(2026-07-06 venezia-fpga 실사례 인용)이 이미 "로컬
사본과 실제 cloud0 TB 소스가 45줄 차이로 diverge"했던 사고를 명시적으로 경고하고 있다 — 이 agent
설계 자체가 TB provenance 정확성에 의존한다는 뜻이다. 이 gap을 막지 않으면, 이 agent가 장시간 자율
루프를 도는 동안(가장 SSH 끊김 확률이 높은 상황) 정확히 그 안전장치가 무력화된다.

### 1.4 F-E: idle-culler가 batch/regression 폴링 워커를 오판할 위험

`batch_runner.py`를 확인한 결과, `sim_batch_run`/`sim_regression`은 애초에 `sim_bridge_run`과
설계가 완전히 다르다 — 이미 "연결이 끊겼다 재연결되는 상황"을 전제로 만들어져 있다:

- job 상태가 메모리가 아니라 **파일**에 있다: `/tmp/xcelium_mcp_{uid}/batch_job.json`(단일 batch),
  `.../regression_job.json`(regression, `completed` 테스트 목록 포함)에 nohup'd 시뮬레이션의
  PID·진행 상황을 저장(`launch_nohup_job` 독스트링: "saves job state **for resume**"). 호출될
  때마다 이 파일부터 확인해서, PID가 죽어있으면 `"(Completed while disconnected)"`로 즉시 결과
  반환, 살아있으면 이어서 폴링, regression이면 완료된 테스트는 건너뛰고 남은 것만 이어서 실행한다
  (`run_batch_regression` 독스트링: "Job resume: on reconnection, resumes from last completed
  test").
- TB provenance도 `bridges` 메모리가 아니라 **그 tool 호출 자체의 파라미터**(`test_name`,
  `resolved_sim_dir`)로 매번 새로 계산한다(`tools/batch.py:222`, regression은 테스트별로 완료
  직후 즉시 계산 — "공유 TB 소스가 regression 도중 수정돼도 이전 테스트에 잘못 귀속되지 않도록"
  의도적으로 설계됨). **잃을 상태 자체가 없다** — F-D가 고치는 문제가 batch/regression엔 원래
  없다.

**그런데 이번에 추가한 idle-culler(F-B)가 새 위험을 만들었다**: `idle_culler.py`의
`has_established_tcp(pid)`는 "TCP 브릿지 연결이 없으면 idle"로 판단하는데, `tools/batch.py`에서
`bridges.xmsim`이 쓰이는 곳은 `from_checkpoint`+`probe_signals` 조합 한 곳뿐이고, 순수 batch
실행/`batch_polling.py`의 폴링 루프는 **TCP 브릿지를 전혀 열지 않는다**(순수 `shell_run`으로 로그
파일을 grep). 즉 6시간(idle-culler 기본 임계값) 넘게 걸리는 regression을 폴링 중인 워커는
`has_established_tcp()`가 `False`를 반환해, 실제로는 전혀 idle이 아닌데도 age 임계값에 걸려 죽을
수 있다. 데이터는 유실되지 않지만(위 job_file resume 덕분), 클라이언트의 그 tool call은 연결이
끊겨 에러로 실패하고 재호출이 필요해진다 — F-B를 추가하기 전에는 없던 리스크다.

---

## 2. Fix Items

### F-D: `sim_dir` 키 레지스트리에 세션 상태(test_name/tb_source) 저장·복원

- **저장**: `sim_bridge_run`이 `bridges.current_test_name`/`current_tb_source`를 설정하는 바로 그
  지점에서, 이미 F-C가 쓰고 있는 `registry.py`의 `environments[sim_dir]` 엔트리에도 같은 값을 기록
  (신규 헬퍼, 예: `update_session_state(sim_dir, test_name, tb_source)`).
- **복원**: `connect_simulator(sim_dir=...)`가 F-C의 direct-hit 경로(`get_bridge_port(sim_dir)`가
  값을 반환해 그 포트로 바로 connect하는 경우)로 성공하면, 같은 레지스트리 엔트리에서
  `current_test_name`/`tb_source`를 읽어 새 `bridges`에 복원(신규 헬퍼, 예:
  `get_session_state(sim_dir)`).
- `checkpoint.py`는 **무변경** — 이미 `bridges.current_test_name`/`current_tb_source`를 읽고 있어서,
  값이 정확히 채워지기만 하면 자동으로 올바르게 동작한다.

**명시적 범위 밖(Design §2.4의 더 뒷단계로 남김)**:
- `connect_simulator(target="auto")`(sim_dir 미지정) 경로 — sim_dir을 모르니 레지스트리 조회 자체가
  불가능. `verilog-rtl-debugger`는 Phase 0에서 이미 sim_dir을 resolve해 알고 호출하므로 실사용에
  영향 없음.
- 워커 죽음을 자동 감지해 알아서 재연결하는 완전 자동화(에이전트/클라이언트가 여전히 재연결을
  명시적으로 트리거해야 함) — Checkpoint에서 사용자가 명시적으로 범위 밖으로 확정.
- 재연결 시점의 신뢰성 검증(예: 레지스트리에 기록된 test_name이 실제로 그 포트에서 지금 도는 것과
  일치하는지 재확인) — 기존 `bridge_port` 필드도 같은 종류의 staleness 리스크를 이미 안고 있고
  (`checkpoint_manager`의 `cleanup_checkpoints(mode="stale")` 같은 기존 패턴으로 다뤄지는 성격),
  이번 feature가 새로 만드는 리스크가 아니므로 별도 조치 없이 동일 수준으로 둔다.

### F-E: idle-culler에 batch/regression job 인지 추가

- idle_culler.py의 `_cull_if_idle(pid)` 호출 전에, `$HOME` 기반이 아니라 `/tmp/xcelium_mcp_{uid}/`
  (batch job이 실제로 쓰는 경로 — `shell_utils.get_user_tmp_dir()`와 동일 패턴)에서
  `batch_job.json`/`regression_job.json`을 읽어, 그 안의 `pid` 필드가 `os.kill(pid, 0)`로 살아있음이
  확인되면 **이번 idle-culler 실행 라운드는 워커를 하나도 죽이지 않고 종료**.
- 어느 MCP 워커가 그 job을 폴링 중인지 job_file만으로는 알 수 없으므로(nohup'd 시뮬레이션 PID와
  MCP 워커 PID는 부모-자식 관계가 아님, F-A와 동일한 이유로 이미 분리돼 있음), 워커 단위로 정밀하게
  가려내지 않고 **사용자 단위로 보수적으로 전체 스킵**한다 — 이 프로젝트 규모(1인~소수 사용자)에서는
  "그 라운드에 다른 진짜 idle 워커 정리가 5분 늦어짐" 정도의 비용이 "진행 중인 regression을 잘못
  죽임"보다 훨씬 싸다.
- 워커 프로세스/수퍼바이저에는 아무 계측도 추가하지 않는다(Design §1.2 "무변경 우선" 원칙 유지) —
  idle_culler가 이미 존재하는 job_file을 읽기만 한다.

**우선순위**: F-D(verilog-rtl-debugger 직접 요구사항) ≈ F-E(이미 배포된 F-B의 회귀 위험 차단) — 둘 다
Critical은 아니지만 F-E는 "이미 배포된 기능이 새로 만든 위험"이라 더 빨리 막는 게 안전.

---

## 3. Scope

| 파일 | 변경 내용 |
|------|----------|
| `src/xcelium_mcp/registry.py` | F-D: 신규 `update_session_state(sim_dir, test_name, tb_source)` / `get_session_state(sim_dir)` 헬퍼 추가(F-C의 `_resolve_project_root` 재사용) |
| `src/xcelium_mcp/tools/sim_lifecycle.py` | F-D: `sim_bridge_run`: `bridges.current_test_name`/`current_tb_source` 설정 직후 `update_session_state()` 호출 추가. `connect_simulator`: F-C direct-hit 성공 시 `get_session_state()`로 복원 |
| `src/xcelium_mcp/tools/checkpoint.py` | 무변경 |
| `src/xcelium_mcp/idle_culler.py` | F-E: `batch_job.json`/`regression_job.json` PID 생존 확인 후 전체 스킵 로직 추가 |

---

## 4. Test Plan

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | `sim_bridge_run(test_name=..., sim_dir=X)` 성공 후 레지스트리 조회 | `environments[X].current_test_name`/`tb_source`가 정확히 기록됨(pytest, OS 무관) |
| T-2 | 레지스트리에 세션 상태가 있는 상태에서 `connect_simulator(sim_dir=X)` 호출(F-C direct-hit) | 새 `bridges.current_test_name`/`current_tb_source`가 레지스트리 값으로 복원됨(pytest, OS 무관) |
| T-3 | 재연결 후 `checkpoint(action=save)` 호출(엔드투엔드) | 기록되는 매니페스트의 `test_name`/`tb_source`가 재연결 전과 동일 — F-175 TB provenance가 재연결을 거쳐도 유지됨을 증명 |
| T-4 | 레지스트리에 세션 상태가 없는 sim_dir로 `connect_simulator(sim_dir=X)` 호출 | 에러 없이 `bridges.current_test_name=""`/`current_tb_source=None` 기본값 유지(하위호환) |
| T-5 | `batch_job.json`/`regression_job.json`에 살아있는 PID가 기록된 상태에서 idle_culler 실행 | 어떤 워커도 죽지 않고 그대로 return(pytest — PID를 현재 프로세스 자신의 PID로 모킹해 "항상 살아있음" 재현 가능, OS 무관) |
| T-6 | job_file이 아예 없거나 PID가 죽어있는 상태에서 idle_culler 실행 | 기존 idle 판정 로직(§4.2) 그대로 동작(회귀 없음) |
| T-7 | 회귀 | 기존 전체 pytest 스위트(F-C/F-B 테스트 포함) 그대로 통과 |

---

## 5. Related Documents

- [xcelium-mcp-server-process-lifecycle.plan.md](xcelium-mcp-server-process-lifecycle.plan.md) §2.4 "향후 승격 경로 1단계" — F-D가 구현하는 항목을 이미 예고해둔 문서.
- [xcelium-mcp-server-process-lifecycle.design.md](../../02-design/features/xcelium-mcp-server-process-lifecycle.design.md) §4.1(레지스트리 스키마, F-D), §4.2/§5.3(idle_culler, F-E).
- `chip-design-skills/agents/verilog-rtl-debugger.md` — F-D gap에 실제로 노출되는 consumer. §2 공통 규칙의 TB drift 실사례가 이 Plan의 동기.
- [xcelium-mcp-server-process-lifecycle.report.md](../../04-report/xcelium-mcp-server-process-lifecycle.report.md) §8.2 Next PDCA Cycle — 후속 사이클로 이미 예정돼 있던 항목.
- `src/xcelium_mcp/batch_runner.py`, `src/xcelium_mcp/batch_polling.py` — F-E 근거(job_file 기반 resume 설계, TCP 브릿지 미사용 확인).

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-07-07 | 초안 — `verilog-rtl-debugger` agent 검토 중 TB provenance 재연결 gap을 확인, Design §2.4 1단계를 이 agent가 필요로 하는 최소 범위(F-D: sim_bridge_run 저장 + connect_simulator 복원, checkpoint.py 무변경)로 스코프. 사용자 확인: FR-04(재연결 투명성 메시지)는 이번 스코프에서 제외. |
| 0.2 | 2026-07-07 | **F-E 추가** — "sim_batch_run/sim_regression은 어떤가"라는 후속 질문에 답하는 과정에서 `batch_runner.py`를 조사, batch/regression은 job_file 기반 resume + 파라미터 기반 TB provenance로 F-D 문제가 원래 없음을 확인. 대신 이번에 배포한 idle-culler(F-B)가 TCP 브릿지 없는 batch 폴링 워커를 오판해 죽일 수 있는 새 위험을 발견해 F-E로 추가(job_file PID 생존 확인 후 전체 스킵). 사용자 확인: F-E를 별도 Plan이 아니라 이 Plan에 통합. |
