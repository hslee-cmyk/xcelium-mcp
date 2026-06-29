# xcelium-mcp-ssh-timeout-fix Plan

## Executive Summary

| Perspective | Description |
|-------------|-------------|
| **Problem** | PID watcher SSH 명령이 background로 분리되지 않아 ssh_run이 15초 timeout 에러를 반복 발생. SSH 명령 timeout이 하드코딩(5s/15s)되어 mcp_sim_config에서 설정 불가. 결과적으로 sim_regression 완료 전 에러 보고. |
| **Solution** | PID watcher를 독립 SSH 호출로 분리하고, ssh_run에 retry + exponential backoff 추가. mcp_sim_config에 `ssh_command_timeout` 설정 항목 추가. |
| **Function UX Effect** | sim_regression 실행 시 SSH timeout 에러 없이 정상 완료. 느린 네트워크/서버 환경에서도 안정적으로 동작. |
| **Core Value** | regression이 에러 없이 완료되어 PASS/FAIL 결과를 신뢰할 수 있음. 서버 환경 차이에 무관한 안정적 인프라. |

---

## 1. Problem Detail

### 1.1 현상

```
Error executing tool sim_regression: ssh_run timeout (15s):
  echo {b64} | base64 -d > regression_job.json &&
  (while kill -0 {pid}; do sleep 2; done; touch done_file)
  < /dev/null >& /dev/null &
```

- `< /dev/null` 추가 후에도 동일 에러 반복
- 시뮬레이션 자체는 정상 실행됨 (로그 파일 생성 확인)
- TOP015/TOP016 PASS이지만 MCP tool은 에러 반환

### 1.2 Root Cause 분석

**두 가지 독립적인 문제가 복합 발생:**

**문제 A: PID watcher SSH session 분리 실패**

```bash
# 현재 코드 (F-027 merged command)
echo {b64} | base64 -d > job_file &&
(while kill -0 {pid}; do sleep 2; done; touch done_file) < /dev/null >& /dev/null &
```

- `echo | base64 -d > file` 은 foreground 실행
- `&&`로 연결된 PID watcher는 `&`로 background 처리
- 그러나 **ssh-mcp의 ssh_run**은 subprocess를 spawn하고 communicate()를 기다림
- bash에서 `cmd1 && cmd2 &` 의 `&`가 SSH session context에서 예상대로 분리되지 않을 수 있음
- 결과: ssh_run이 PID watcher가 종료될 때까지 대기 → 시뮬레이션 끝날 때까지 블로킹

**문제 B: SSH 명령 timeout 하드코딩**

| 코드 위치 | 현재 timeout | 적절성 |
|-----------|:------------:|:------:|
| `rm -f job_file` | 5s | 적절 (파일 삭제는 빠름) |
| `cat pid_file` | 5s | 적절 |
| `echo b64 | base64 -d > job_file` | 15s | 부적절 (SSH session 문제 시 15s 고정) |
| PID watcher 시작 | 15s (merged) | 부적절 |
| `id -u` (get_user_tmp_dir) | 5s | 적절 |
| `mkdir -p` | 5s | 적절 |

- mcp_sim_config의 `timeout`(600s)은 `poll_batch_log`에만 사용
- SSH 명령 timeout은 config와 완전히 분리, 하드코딩
- SSH 명령 실패 시 retry 없이 즉시 에러

### 1.3 Gap Analysis 결과

```
xcelium-mcp-sim-run-timeout-fix.plan.md → TCP bridge timeout (sim_run)
현재 plan → SSH 명령 timeout (인프라 레이어)
```

기존 plan은 TCP bridge의 Tcl 명령 timeout을 다룸. SSH 인프라 timeout은 별도 plan 없음.

---

## 2. Fix Items

### F-1: PID watcher 분리 (핵심 수정)

**문제**: F-027의 merged SSH 명령이 SSH session을 블로킹함

**해결**: job 파일 쓰기와 PID watcher를 별도 SSH 호출로 분리

```python
# Before (F-027 merged - 블로킹 문제 있음)
await ssh_run(
    f"echo {b64} | base64 -d > {job_file} && "
    f"(while kill -0 {pid}; do sleep 2; done; touch {done_file}) < /dev/null >& /dev/null &",
    timeout=15,
)

# After (분리된 두 호출)
# Step 1: job 파일 쓰기 (동기, 빠름)
await ssh_run(f"printf '%s' {shell_quote(job_info)} > {job_file}", timeout=ssh_cmd_timeout)

# Step 2: PID watcher 독립 실행 (nohup 패턴 동일 적용)
await ssh_run(
    f"(nohup bash -c 'while kill -0 {pid} 2>/dev/null; do sleep 2; done; touch {shell_quote(done_file)}' "
    f"< /dev/null >& /dev/null &)",
    timeout=ssh_cmd_timeout,
)
```

**핵심 변경사항:**
- `printf '%s' 'json'` — base64 인코딩 불필요, 단순 파일 쓰기
- `nohup bash -c '...'` — nohup을 명시적으로 사용, SSH session에서 완전 분리
- `< /dev/null >& /dev/null &` — nohup launch 패턴과 동일하게 적용

### F-2: ssh_command_timeout 설정 추가

**mcp_sim_config에 SSH 명령 timeout 설정 항목 추가:**

```json
{
  "runner": { ... },
  "ssh_command_timeout": 30
}
```

`resolve_sim_params()`에서 읽어 인프라 SSH 명령에 적용:

```python
def get_ssh_cmd_timeout(runner: dict, default: int = 30) -> int:
    """SSH infrastructure command timeout from config or default."""
    return runner.get("ssh_command_timeout", default)
```

적용 위치:
- `launch_nohup_job()` — job 파일 쓰기, PID watcher 시작
- `run_batch_regression()` — job 파일 cleanup, per-test SSH 명령
- `get_user_tmp_dir()` — id -u, mkdir -p

### F-3: SSH 명령 retry with exponential backoff

단순 timeout 에러 시 retry 없이 즉시 실패하는 문제 해결:

```python
async def ssh_run_with_retry(
    cmd: str,
    timeout: float = 30.0,
    max_retries: int = 2,
    backoff_base: float = 2.0,
) -> str:
    """ssh_run with exponential backoff retry for transient failures.

    retry 조건: asyncio.TimeoutError (일시적 SSH 지연)
    non-retry: 명령 실행 에러 (CalledProcessError, 명시적 실패)
    """
    for attempt in range(max_retries + 1):
        try:
            return await ssh_run(cmd, timeout=timeout)
        except asyncio.TimeoutError:
            if attempt == max_retries:
                raise
            wait = backoff_base ** attempt  # 1s, 2s
            await asyncio.sleep(wait)
```

적용 위치: job 파일 쓰기, PID watcher 시작 (파일 cleanup은 best-effort, retry 불필요)

### F-4: 진단 로그 추가

현재: timeout 발생해도 SSH 명령만 에러로 출력, 시뮬레이션 상태 불명확

추가:
```python
# timeout 발생 시 시뮬레이션 상태 보고
if timed_out:
    pgrep_result = await ssh_run(f"pgrep -la xmsim || true", timeout=10)
    logger.warning(f"SSH timeout occurred. Running simulations: {pgrep_result}")
```

---

## 3. 구현 범위

| 파일 | 변경 내용 |
|------|----------|
| `src/xcelium_mcp/batch_runner.py` | F-1: PID watcher 분리, F-2: ssh_command_timeout 적용, F-3: retry 적용 |
| `src/xcelium_mcp/shell_utils.py` | F-2: `get_ssh_cmd_timeout()` helper 추가, F-3: `ssh_run_with_retry()` 추가 |
| `src/xcelium_mcp/test_resolution.py` | F-2: `ssh_command_timeout` 파라미터를 `resolve_sim_params()` 반환값에 포함 |
| `tests/test_batch_helpers.py` | F-1 테스트: PID watcher 독립 호출 확인, F-3 테스트: retry 동작 확인 |

변경 없음: `mcp_bridge.tcl`, `tcl_bridge.py`, `registry.py`, tool modules

---

## 4. Test Plan

| # | 테스트 | 검증 |
|---|--------|------|
| T-1 | `launch_nohup_job` — PID watcher SSH 명령이 별도 호출인지 확인 | `nohup bash -c` 포함, `&&` 없이 독립 실행 |
| T-2 | `ssh_run_with_retry` — TimeoutError 시 retry 동작 | 1회 실패 → 2s 대기 → 2회 성공 |
| T-3 | `ssh_run_with_retry` — max_retries 초과 시 에러 전파 | 3회 실패 → TimeoutError |
| T-4 | `ssh_run_with_retry` — 비timeout 에러는 retry 없음 | CalledProcessError → 즉시 전파 |
| T-5 | `get_ssh_cmd_timeout` — config에서 읽기 | runner["ssh_command_timeout"]=60 → 60 반환 |
| T-6 | `get_ssh_cmd_timeout` — 기본값 | 키 없음 → 30 반환 |
| T-7 | regression 실제 실행 — SSH timeout 에러 없음 | TOP015/TOP016 정상 완료 |

---

## 5. 비교: 이전 vs 개선

| 항목 | 이전 | 개선 |
|------|------|------|
| PID watcher | job 파일과 merged 1 SSH call | 별도 2 SSH call |
| SSH session 분리 | `< /dev/null` (불완전) | `nohup bash -c` (nohup 패턴) |
| SSH 명령 timeout | 하드코딩 5s/15s | config `ssh_command_timeout` (기본 30s) |
| SSH 명령 실패 시 | 즉시 에러 | retry with backoff (2회) |
| SSH 호출 수 | N+0 (merged) | N+1 (분리, F-027 최적화 일부 revert) |

SSH 호출 1회 증가는 안정성 확보를 위한 의도적 tradeoff.
