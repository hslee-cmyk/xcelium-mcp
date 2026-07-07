# Design: xcelium-mcp Session State Reattach (F-D) + Batch-Aware idle-culler (F-E)

> **Summary**: F-C 레지스트리에 `current_test_name`/`tb_source`를 추가해 재연결 시 TB provenance를
> 복원하고(F-D), idle-culler가 진행 중인 batch/regression job을 오판해 죽이지 않도록 job_file을
> 확인한다(F-E).
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: hoseung.lee
> **Date**: 2026-07-07
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-session-state-reattach.plan.md](../../01-plan/features/xcelium-mcp-session-state-reattach.plan.md)
> **Parent Feature**: [xcelium-mcp-server-process-lifecycle](xcelium-mcp-server-process-lifecycle.design.md) (안C+, F-A/B/C 완료)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | (F-D) `sim_bridge_run`이 설정하는 `bridges.current_test_name`/`current_tb_source`는 워커 프로세스의 인메모리 상태라 워커 재시작 시 사라진다 — F-C로 같은 xmsim에 재연결해도 `checkpoint(save)`가 TB provenance 없이 기록된다. (F-E) idle-culler(F-B)의 `has_established_tcp()`는 TCP 브릿지가 없으면 idle로 판단하는데, 순수 `sim_batch_run`/`sim_regression`은 TCP 브릿지를 열지 않아(순수 로그 폴링) 장시간 batch 작업이 부당하게 죽을 수 있다. |
| **Solution** | (F-D) F-C가 이미 만든 `registry.py`의 `environments[sim_dir]` 엔트리에 `current_test_name`/`tb_source` 필드를 추가, `sim_bridge_run`이 쓰고 `connect_simulator(sim_dir=...)`의 F-C direct-hit 경로가 읽어 복원. (F-E) idle_culler가 `_cull_if_idle()` 호출 전에 `batch_job.json`/`regression_job.json`을 읽어 살아있는 job이 있으면 이번 라운드 전체를 스킵 — mtime 기반 stale guard 포함(Checkpoint 3, 안C 채택). |
| **Function/UX Effect** | `verilog-rtl-debugger`의 Phase 4E 자율 루프가 재연결을 거쳐도 체크포인트 TB provenance가 정확하고, 장시간 regression이 idle-culler에 중단되지 않는다. |
| **Core Value** | 이미 검증된 F-C 인프라(레지스트리)와 batch_runner.py의 job_file 인프라를 각각 재사용해, 새 파일/새 계측 없이 두 gap을 모두 막는다 — `server.py`/`tools/*.py`의 다른 부분과 `batch_runner.py`는 무변경. |

---

## 1. Overview

### 1.1 Design Goals

1. `verilog-rtl-debugger`가 재연결 후 `checkpoint(save)`를 호출해도 TB provenance가 재연결 전과 동일하게 기록된다.
2. idle-culler가 살아있는 batch/regression job이 있는 동안에는 어떤 워커도 죽이지 않는다.
3. `batch_runner.py`, `checkpoint.py`, `server.py`, `tools/sim_lifecycle.py`의 기존 로직(F-D/F-E와 무관한 부분)은 변경하지 않는다.
4. 기존 pytest 스위트(548개, 부모 feature 포함)가 회귀 없이 통과한다.

### 1.2 Design Principles

- **기존 인프라 재사용**: F-D는 F-C가 이미 만든 레지스트리 엔트리에 필드만 추가(신규 스키마 없음). F-E는 batch_runner.py가 이미 만든 job_file을 읽기만 한다(신규 job 추적 메커니즘 없음).
- **무변경 우선 계승**: 부모 feature(안C+)의 "워커/수퍼바이저에 계측 코드를 추가하지 않는다" 원칙을 그대로 유지 — F-E는 idle_culler 쪽에서 기존 파일을 읽는 것으로 끝낸다.
- **보수적 안전 우선(F-E)**: 어느 워커가 특정 job을 폴링 중인지 정밀하게 알 수 없으므로, 정밀 attribution 대신 "살아있는 job이 있으면 이번 라운드는 전체 보류"를 택한다 — 그 비용(다른 진짜 idle 워커 정리가 최대 idle-culler 폴링 주기만큼 늦어짐)이 "진행 중인 regression을 잘못 죽이는" 비용보다 훨씬 낮다.

---

## 2. Architecture Options (Checkpoint 3 — 완료)

| 기준 | A: Minimal | B: Clean Architecture | **C: Pragmatic Balance(선택)** |
|---|---|---|---|
| F-D 데이터 위치 | registry.py 재사용 | 별도 `session_state.py` 모듈 신설 | registry.py 재사용(A/C 동일) |
| F-E 워커 attribution | job_file 직접 읽기, 전체 스킵 | `batch_runner.py`가 job_file에 호출 워커 pid 기록 → 정밀 보호 | job_file 직접 읽기, 전체 스킵(A와 동일) + stale guard |
| F-E robustness | 없음 — corrupt/stuck job_file이 idle-culling을 영구 차단할 위험 | 해당 없음(정밀 attribution이라 이 리스크 자체가 없음) | **mtime 기반 stale guard**로 이 리스크 차단 |
| `batch_runner.py` 변경 | 없음 | 있음(job_file 스키마 확장 + 여러 호출 지점에 pid 플러밍) | **없음** |
| 회귀 리스크 | 낮음(F-E에 corrupt-file 리스크 존재) | 중간(batch_runner.py는 이미 잘 동작하는 핵심 모듈 — 건드릴 이유를 최소화하고 싶음) | **낮음** |

**선택: C**. 근거: F-D는 A/B/C 모두 registry.py 재사용에 동의하므로 쟁점이 아니다. F-E의 진짜 트레이드오프는 "정밀함(B)" vs "단순함(A)"인데, B는 이미 안정적으로 잘 동작하는 `batch_runner.py`(job_file/PID watcher/adaptive polling 등 여러 P6 히스토리를 거쳐 다듬어진 모듈)를 건드려야 해서 이 작은 fix의 이득 대비 회귀 리스크가 과하다. C는 A의 단순함을 유지하면서, A의 유일한 약점(오래되거나 깨진 job_file이 idle-culling을 영구히 막을 수 있다는 것)을 mtime 체크 한 줄로 없앤다.

---

## 3. Component Architecture

### 3.1 Data Flow — F-D 저장·복원

```
sim_bridge_run(test_name, sim_dir=X) 성공
  → bridges.current_test_name = test_name; bridges.current_tb_source = tb_source   (기존, 무변경)
  → update_session_state(X, test_name, tb_source)   (신규) → registry.py의
    environments[X]에 current_test_name/tb_source 필드 기록

[워커 재시작 — SSH 끊김 등]

connect_simulator(sim_dir=X) 호출
  → get_bridge_port(X)로 F-C direct-hit 성공(§3.3, 부모 feature)
  → 그 포트로 connect 성공 직후, get_session_state(X)로 조회 (신규)
  → bridges.current_test_name / bridges.current_tb_source 복원 (신규)

checkpoint(action=save) 호출  (기존, 무변경)
  → bridges.current_test_name/current_tb_source를 그대로 읽음 → 복원된 값이 정확히 기록됨
```

### 3.2 Data Flow — F-E idle-culler batch job 인지

```
idle_culler.main() 실행(cron, 5분 간격 — 기존)
  → _has_live_batch_job() 확인 (신규)
      for jf in (batch_job.json, regression_job.json):
          read + mtime 확인(오래됐으면 무시) + pid 필드 확인(os.kill(pid,0))
          하나라도 살아있으면 True
  → True면: 이번 라운드 종료, 워커 순회/kill 없이 return (신규 분기)
  → False면: 기존 §4.2(부모 feature) idle 판정 로직 그대로 진행 (무변경)
```

### 3.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `tools/sim_lifecycle.py`(수정) | `registry.py`(확장) | `sim_bridge_run`이 세션 상태 write, `connect_simulator`가 read+복원 |
| `registry.py`(확장) | 기존 `_resolve_project_root`(F-C) | 세션 상태 저장/조회의 project_root 정규화 재사용 |
| `idle_culler.py`(수정) | `shell_utils.get_user_tmp_dir()`와 동일 경로 패턴(`/tmp/xcelium_mcp_{uid}/`) | `batch_job.json`/`regression_job.json` 위치 — 단, idle_culler는 순수 동기 스크립트라 `get_user_tmp_dir()`(async) 자체를 import하지 않고 동일 패턴을 직접 구성(`os.getuid()` 사용) |

---

## 4. Data Model

### 4.1 `mcp_registry.json` — F-D 필드 추가 (기존 엔트리 확장)

F-C가 만든 `environments[sim_dir]`에 필드 2개만 추가한다(신규 최상위 키 없음):

```python
envs[sim_dir] = {
    "tb_type": ...,           # 기존
    "is_default": ...,        # 기존
    "config_version": ...,    # 기존
    "bridge_port": ...,       # F-C
    "current_test_name": "",           # F-D 신규
    "current_tb_source": None,         # F-D 신규 — build_tb_provenance()의 {"files":[...], "combined_sha256":...} 그대로
}
```

### 4.2 idle_culler의 batch job 판단 근거 — 신규 파일 없음

`batch_runner.py`가 이미 쓰고 있는 두 파일을 **읽기만** 한다(§1.4 부모 feature 원칙과 동일 정신 —
새로 뭘 만들지 않고 커널/기존 애플리케이션이 이미 관리하는 정보만 본다):

```
/tmp/xcelium_mcp_{uid}/batch_job.json        # launch_nohup_job이 씀 — {"pid": N, ...}
/tmp/xcelium_mcp_{uid}/regression_job.json   # 동일 스키마 + type="regression"/completed 목록
```

- `STALE_JOB_FILE_SEC`(예: 4시간 — batch/regression의 개별 타임아웃 상한(3600s)보다 넉넉히 크게):
  이보다 mtime이 오래된 job_file은 "이미 끝났는데 정리가 안 된" 것으로 간주해 무시(Checkpoint 3의
  robustness 요구사항).
- `pid` 필드가 없거나 `os.kill(pid, 0)`가 `ProcessLookupError`를 던지면 죽은 job으로 간주.

---

## 5. Component Detail

### 5.1 `src/xcelium_mcp/registry.py` (기존, 확장 — F-D)

```python
async def update_session_state(sim_dir: str, test_name: str, tb_source: dict | None) -> None:
    """F-D: sim_bridge_run 성공 시 세션 상태를 sim_dir 키 레지스트리에 write-back.
    update_bridge_port()와 동일한 project_root 정규화(_resolve_project_root) 재사용."""
    project_root = await _resolve_project_root(sim_dir)
    resolved_sim_dir = str(Path(sim_dir).resolve())
    registry = await asyncio.to_thread(_load_registry_sync)
    envs = registry.setdefault("projects", {}).setdefault(project_root, {"environments": {}}).setdefault("environments", {})
    env = envs.setdefault(resolved_sim_dir, {})
    env["current_test_name"] = test_name
    env["current_tb_source"] = tb_source
    await asyncio.to_thread(_save_registry_sync, registry)


async def get_session_state(sim_dir: str) -> tuple[str, dict | None]:
    """F-D: connect_simulator의 F-C direct-hit 경로에서 복원용 조회.
    엔트리가 없으면 ("", None) — 하위호환 기본값과 동일."""
    project_root = await _resolve_project_root(sim_dir)
    resolved_sim_dir = str(Path(sim_dir).resolve())
    registry = await asyncio.to_thread(_load_registry_sync)
    env = registry.get("projects", {}).get(project_root, {}).get("environments", {}).get(resolved_sim_dir, {})
    return env.get("current_test_name", ""), env.get("current_tb_source")
```

### 5.2 `src/xcelium_mcp/tools/sim_lifecycle.py` (기존, 수정 — F-D)

- `sim_bridge_run`: 기존 `bridges.current_test_name = test_name` / `bridges.current_tb_source = tb_source` 바로 다음 줄에 `await update_session_state(resolved_dir, test_name, tb_source)` 추가.
- `connect_simulator`: F-C direct-hit 분기(`registry_port = await get_bridge_port(sim_dir)`가 성공하는 경로) 안에서, bridge connect 성공 직후 `bridges.current_test_name, bridges.current_tb_source = await get_session_state(sim_dir)` 추가.

### 5.3 `src/xcelium_mcp/idle_culler.py` (기존, 수정 — F-E)

```python
STALE_JOB_FILE_SEC = 4 * 3600

def _user_tmp_dir() -> Path:
    # get_user_tmp_dir()(shell_utils, async)와 동일 패턴을 동기적으로 재구성 —
    # idle_culler는 asyncio/shell_run 의존성을 들이지 않는다(§1.2 무변경 우선 계승).
    return Path(f"/tmp/xcelium_mcp_{os.getuid()}")

def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 다른 uid가 그 pid를 재사용 중 == 살아있음(보수적으로 True 처리)

def has_live_batch_job() -> bool:
    """F-E: 진행 중인 batch/regression job이 있으면 True — idle-culler가 이번 라운드를 전체 스킵하는 근거."""
    user_tmp = _user_tmp_dir()
    now = time.time()
    for name in ("batch_job.json", "regression_job.json"):
        path = user_tmp / name
        try:
            stat = path.stat()
            if now - stat.st_mtime > STALE_JOB_FILE_SEC:
                continue  # stale guard(Checkpoint 3)
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if _pid_alive(data.get("pid", 0)):
            return True
    return False

def main() -> int:
    ...
    if has_live_batch_job():
        return 0  # F-E: 이번 라운드는 아무 워커도 건드리지 않음
    supervisor_pid = find_supervisor_pid()
    ...  # 기존 로직 그대로
```

---

## 6. Error Handling

| 상황 | 처리 |
|---|---|
| `registry.py` 조회 시 sim_dir 엔트리 없음(F-D) | `("", None)` 기본값 반환 — 기존 `bridges.current_test_name=""` 초기값과 동일, 에러 아님 |
| `batch_job.json`/`regression_job.json`이 없거나 JSON 파싱 실패(F-E) | 해당 파일 무시하고 다음 파일 확인 — 크래시 없음 |
| job_file은 있으나 mtime이 `STALE_JOB_FILE_SEC` 초과(F-E) | stale로 간주해 무시 — idle-culling이 영구 차단되지 않음(Checkpoint 3 robustness) |
| job_file의 pid가 다른 uid 소유 프로세스로 재사용됨(극히 드묾, PID 재사용) | `os.kill(pid, 0)`가 `PermissionError`를 던지면 "살아있음"으로 안전 측 처리(보수적 원칙과 일관) |

---

## 7. Test Plan

| # | 테스트 | 대상 | 환경 |
|---|---|---|---|
| T-1 | `sim_bridge_run` 성공 후 레지스트리에 `current_test_name`/`tb_source` 기록 확인 | `registry.py` | pytest(OS 무관) |
| T-2 | 레지스트리에 세션 상태가 있는 sim_dir로 `connect_simulator(sim_dir=X)` 호출 → `bridges` 필드 복원 확인 | `tools/sim_lifecycle.py` | pytest(OS 무관) |
| T-3 | 복원 후 `checkpoint(action=save)` 호출 → 매니페스트에 정확한 test_name/tb_source 기록(엔드투엔드) | `checkpoint.py`(무변경) + 위 두 개 통합 | pytest(OS 무관) |
| T-4 | 세션 상태가 없는 sim_dir로 `connect_simulator(sim_dir=X)` 호출 → 에러 없이 기본값(`""`/`None`) 유지 | `tools/sim_lifecycle.py` | pytest(OS 무관) |
| T-5 | `batch_job.json`에 현재 프로세스 자신의 pid(항상 살아있음)를 넣고 `has_live_batch_job()` 호출 → `True` | `idle_culler.py` | pytest(순수 함수, OS 무관 — `os.getpid()`로 모킹) |
| T-6 | job_file mtime을 `STALE_JOB_FILE_SEC` 이전으로 조작 → `has_live_batch_job()`이 `False`(stale guard) | `idle_culler.py` | pytest(OS 무관) |
| T-7 | job_file 없음/corrupt JSON → `has_live_batch_job()`이 `False`, 크래시 없음 | `idle_culler.py` | pytest(OS 무관) |
| T-8 | `has_live_batch_job()`이 `True`일 때 `main()`이 워커를 하나도 건드리지 않고 조기 반환 | `idle_culler.py` | pytest(`find_supervisor_pid` 등을 mock, OS 무관) |
| T-9 | 회귀 | 전체 pytest 스위트(부모 feature 548개 포함) | 로컬(Windows) — 이 feature는 전부 순수 로직/pytest라 cloud0 실측 불필요 |

> 부모 feature와 달리 이번 변경은 `/proc` 프로세스 트리(F-A)나 fork(F-B 원본)를 건드리지 않고
> job_file/registry라는 **파일 기반** 로직만 다루므로, 전부 로컬(Windows) pytest로 완결 가능하다 —
> cloud0 실배포·스모크 테스트가 필요 없다.

---

## 8. Implementation Guide

### 8.1 File Structure

```
src/xcelium_mcp/
├── registry.py            (수정) — update_session_state/get_session_state 추가
├── tools/sim_lifecycle.py (수정) — sim_bridge_run/connect_simulator에 훅 추가
└── idle_culler.py          (수정) — has_live_batch_job() + main() 조기 반환 분기
```

### 8.2 Implementation Order

1. [ ] `registry.py`: `update_session_state`/`get_session_state` 추가 + 단위 테스트(T-1)
2. [ ] `tools/sim_lifecycle.py`: `sim_bridge_run`에 저장 훅 추가(T-1 검증)
3. [ ] `tools/sim_lifecycle.py`: `connect_simulator`에 복원 훅 추가(T-2)
4. [ ] `checkpoint.py` 관련 통합 테스트(T-3, 코드 변경 없음)
5. [ ] `idle_culler.py`: `has_live_batch_job()` 추가 + 단위 테스트(T-5~T-7)
6. [ ] `idle_culler.py`: `main()`에 조기 반환 분기 추가(T-8)
7. [ ] 회귀: 전체 pytest 스위트 통과 확인(T-9)

### 8.3 Session Guide

이 feature는 전부 pytest로 검증 가능하고 파일 수도 3개뿐이라, 단일 세션으로 완결 권장(모듈 분할 불필요).

| Session | Phase | Scope | 비고 |
|---------|-------|-------|------|
| Session 1 | Do + Check | 전체(F-D + F-E) | 로컬(Windows)에서 전부 완결 가능, cloud0 불필요 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-07-07 | 초안 — Plan v0.2(F-D+F-E) 기반. Checkpoint 3: F-D는 registry.py 재사용(쟁점 없음), F-E는 Option C(job_file 직접 읽기 + 전체 스킵 + mtime stale guard) 채택 — `batch_runner.py` 무변경 유지가 핵심 근거. 전체 테스트가 pytest로 완결 가능해 cloud0 실배포 불필요함을 명시. | hoseung.lee |
