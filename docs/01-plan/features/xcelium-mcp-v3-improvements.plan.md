# Plan: xcelium-mcp v3 Improvements

> **Feature**: xcelium-mcp 안정성 및 dump 기반 bisect 확장
>
> **Date**: 2026-03-26
> **Status**: Completed (2026-03-30, 100% match rate — 45/45 항목 PASS, `docs/03-analysis/xcelium-mcp-v3-improvements.analysis.md` 참조) — *2026-07-03 정정: 이 헤더가 "Draft"로 오래 방치돼 있었음. 아래 본문(요구사항 등)은 계획 당시 원문 그대로 보존*
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | sim_restart 에러, bisect SVNOCL 취약, dump 오프라인 분석 불가, batch tool 부재, 스크립트 매번 재생성, AI 분석 → 사용자 디버깅 연결 부재 |
| **Solution** | sim_restart 수정, bisect 2-mode, simvisdbutil CSV 통합, save/restore 아키텍처 변경, batch sim tool, script discovery, 사용자 디버깅 지원 tool |
| **Function UX Effect** | restart 성공 보장, dump bisect, batch regression 1-command, 스크립트 자동 재사용, AI 분석→SimVision 자동 세팅으로 사용자 디버깅 즉시 시작 |
| **Core Value** | 디버깅 시간 단축, save/restore 안정화, 오프라인 분석, 반복 자동화, AI-Human 협업 디버깅 |

---

## 1. sim_restart Snapshot Name 에러 수정

### 현재 문제

```python
# server.py:107-112
async def sim_restart() -> str:
    bridge = _get_bridge()
    await bridge.execute("restart")  # ← 단순 restart, snapshot name 없음
```

Xcelium의 `restart` 명령은 **snapshot name**이 필요하다:
```
restart <snapshot_name> [-path <dir>]
```

단순 `restart`는 "no snapshot name" 에러를 발생시킨다.

### 수정 방안

**Option A (권장): `run -clean` 사용**
```tcl
# time 0으로 돌아가는 가장 안전한 방법
run -clean
```

**Option B: Initial snapshot 자동 저장 + 활용**
```tcl
# bridge 시작 시 초기 snapshot 저장 (/tmp/mcp_init 은 restart 전용 임시 snapshot)
# ※ L1/L2 계층적 checkpoint와 별개 — 이 snapshot은 세션 내 restart 용도만이며
#    4-C의 persistent {sim_dir}/checkpoints/ 정책에서 제외
proc ::mcp_bridge::init_snapshot {} {
    variable _init_snapshot_dir "/tmp/mcp_init"
    file mkdir $_init_snapshot_dir
    catch {save -simulation mcp_init -path $_init_snapshot_dir -overwrite}
}

# restart 시 초기 snapshot 복원
proc ::mcp_bridge::do_restart {channel} {
    variable _init_snapshot_dir
    if {[catch {restart worklib.mcp_init:module -path $_init_snapshot_dir} err]} {
        # fallback: run -clean
        if {[catch {run -clean} err2]} {
            ::mcp_bridge::send_error $channel "restart failed: $err; run -clean also failed: $err2"
            return
        }
    }
    ::mcp_bridge::send_ok $channel "restarted to time 0"
}
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 1-1 | `restart` → meta command `__RESTART__`로 변환 | server.py, mcp_bridge.tcl |
| 1-2 | Xcelium `run -clean` 또는 snapshot 기반 restart 구현 | mcp_bridge.tcl |
| 1-3 | bridge 초기화 시 initial snapshot 자동 저장 | mcp_bridge.tcl (init) |
| 1-4 | 에러 시 fallback: `run -clean` 시도 | mcp_bridge.tcl |

---

## 2. bisect 2-mode 구현 (Checkpoint / Dump)

### 현재 문제

bisect_signal은 save/restore 기반 binary search만 지원:
- `save -simulation` → SVNOCL 에러 빈발
- restore 후 `$finish`로 점프하는 문제
- interactive session에서만 사용 가능

### Mode A: Checkpoint 기반 (Bridge mode 전용, §4 아키텍처 변경 적용)

기존 `bisect_signal`의 내부 동작을 §4의 "Restore → Watchpoint → Dump → CSV" 패턴으로 변경:
- 기존: restore → run + watchpoint → get_signal_value → restore → ... (N회 반복)
- 변경: restore → watchpoint → run 1회 → dump → CSV → in-memory binary search
- Bridge mode에서만 사용 (시뮬레이터 연결 필요 — save/restore 때문)

**Mode A 사용 케이스:**

| 케이스 | 이유 | Mode B 대체 |
|--------|------|:-----------:|
| **deposit_value로 수정 가설 검증** | RTL 수정 없이 신호에 값 강제 주입 → 하류 동작 확인 | **불가** |
| 사용자 실시간 디버깅 (§8) | SimVision GUI로 AI+사용자 협업 | 불가 |
| 블록 레벨 짧은 시뮬레이션 | compile→run이 수 초로 빠름, checkpoint 오버헤드 미미 | 가능하지만 Mode A가 더 간결 |
| find_drivers 설계 탐색 | 라이브 시뮬레이터에서 driver 추적 | 정적 코드 분석으로 대체 가능 |

**가장 핵심적인 Mode A 고유 기능: `deposit_value`로 수정 가설 사전 검증.**
RTL 수정 → 컴파일 → 재실행 사이클(수십 분)을 건너뛰고 즉시 확인할 수 있다:
```python
restore_checkpoint(name="before_bug")
sim_run(duration="0.1ms")        # CHK_ADR 직전까지
deposit_value(signal="top.hw...r_regAddr", value="8'h10")  # 강제 주입
sim_run(duration="5ms")          # → write 정상이면 regAddr 미설정이 근본 원인 확정
```

**참고**: c_ 조합 신호는 SHM dump에 포함됨 (`probe -depth all -all` 옵션). Mode A에서만 접근 가능하다는 것은 오해 — **검증 완료 (2026-03-27).**

**그 외 대부분의 디버깅은 Mode B로 충분.**

### Mode B: Dump 기반 (신규)

**원리**: 시뮬레이션은 이미 완료되어 SHM dump 파일이 존재한다. SHM에는 대부분의 신호가 기록되어 있으며, simvisdbutil로 **분석에 필요한 신호만** CSV로 선별 추출한 뒤 in-memory binary search를 수행한다. 시뮬레이터 연결 불필요.

```
[사용자/AI]
    │
    ├─ bisect_signal_dump(shm_path, signal, op, value, start_ns, end_ns, context_signals=[])
    │       │
    │       ├─ 1. simvisdbutil로 CSV 1회 추출 (관심 범위 + 판별 신호)
    │       ├─ 2. CSV → Python in-memory 로드
    │       ├─ 3. in-memory binary search → 조건 매칭 시점 특정
    │       ├─ 4. 결과 반환: 첫 매칭 시각 + 전후 N행 (값 변화 원인 파악용)
    │       │
    │       ├─ 5. (SHM에 신호 있음) 같은 범위에서 추가 신호 CSV 재추출 → 조인
    │       │
    │       └─ 6. (SHM에 신호 없음) ★ 사용자 선택 Hook (아래 참조)
    │
    └─ 시뮬레이터 연결 불필요 — Batch dump 완료 후 오프라인 분석
```

### 신호 부족 시 사용자 선택 Hook

Mode B에서 분석 중 추가 신호가 필요한데 **SHM dump에 해당 신호가 없는 경우**, 시뮬레이션을 다시 실행해야 한다. 이때 3가지 경로가 있으며, 상황에 따라 최적 선택이 다르므로 **사용자에게 선택을 요청**한다.

```
bisect_signal_dump 분석 중
    │
    ├─ 추가 신호 필요 → SHM에서 확인
    │       │
    │       ├─ SHM에 있음 → CSV 재추출 (수 초, 선택 불필요)
    │       │
    │       └─ SHM에 없음 → ★ HOOK: 사용자 선택 요청 (3가지)
    │               │
    │               ├─ [A] Batch full 재실행
    │               │     dump scope 확장 (prepare_dump_scope)
    │               │     → 전체 시뮬레이션 재실행 (time 0~)
    │               │     → 새 SHM으로 CSV 분석 재개
    │               │
    │               ├─ [A'] Batch-restore (권장, L1 checkpoint 있을 때)
    │               │     L1 checkpoint에서 restore (GUI 불필요)
    │               │     → probe 신호 추가 → run → 새 SHM → CSV 분석
    │               │     → 추가 신호 필요 시 [A'] 반복 가능
    │               │
    │               └─ [B] Bridge interactive (keep_alive)
    │                     L1 checkpoint에서 restore
    │                     → probe 신호 추가
    │                     → watchpoint까지 실행
    │                     → 부분 dump → CSV 분석
    │                     → 시뮬레이터 watchpoint 시점에서 정지 유지
    │                         ├─ deposit_value()   : 수정 가설 즉시 검증
    │                         ├─ get_signal_value(): 현재 시점 신호 확인
    │                         ├─ sim_run()         : watchpoint 이후 계속 실행
    │                         ├─ execute_tcl()     : 임의 Tcl 실행
    │                         └─ shutdown_simulator() : 완료 후 수동 종료
    │
    └─ 선택 기준 안내:
         [A]  추천: L1 checkpoint 없음, 전체 dump 필요
         [A'] 추천: L1 checkpoint 있음 (기본 경로)
         [B]  추천: deposit_value 등 실시간 조작이 반드시 필요한 경우만
```

**Hook 구현:**

```python
@mcp.tool()
async def request_additional_signals(
    missing_signals: list[str],     # SHM에 없는 신호 목록
    shm_path: str,                  # 현재 SHM 경로
    bug_time_ns: int = 0,           # 이상 시점 (알려진 경우)
    available_checkpoints: list[str] = [],  # 사용 가능한 checkpoint 목록
) -> str:
    """Signal not found in SHM dump. Ask user to choose re-run strategy.

    Returns user's choice: 'batch_full', 'batch_restore', or 'bridge_interactive'.
      batch_full        : [A]  Batch full — dump scope 확장 후 전체 재시뮬레이션 (time 0~)
      batch_restore     : [A'] Batch-restore — L1 restore → probe_add → run → 새 SHM (GUI 불필요)
      bridge_interactive: [B]  Bridge interactive — restore → probe → dump → CSV → keep_alive
    """
    # 선택지 구성
    options = []
    options.append("[A] Batch full: prepare_dump_scope로 scope 확장 → 전체 재실행")

    if available_checkpoints:
        nearest = _find_nearest_checkpoint(available_checkpoints, bug_time_ns)
        options.append(f"[A'] Batch-restore (권장): {nearest}에서 restore → probe 추가 → run → 새 SHM (GUI 불필요)")
        options.append(f"[B] Bridge interactive: {nearest}에서 restore → probe 추가 → 부분 dump → CSV"
                       f" → watchpoint 시점 정지 유지 (deposit_value / sim_run 등 직접 조작 가능)")
    else:
        options.append("[A'] Batch-restore: L1 checkpoint 없음 — [A] 사용 권장")
        options.append("[B] Bridge interactive: sim 처음부터 실행 후 직접 조작 필요 시")

    recommended = "[A']" if available_checkpoints else "[A]"

    # 사용자에게 선택 요청 (AskUserQuestion 또는 MCP prompt)
    return await ask_user(
        f"Missing signals: {missing_signals}\n"
        f"Options:\n" + "\n".join(options) +
        f"\nRecommended: {recommended}"
    )
```

**선택 후 자동 실행:**

```python
# 사용자 선택에 따라 자동으로 적절한 흐름 실행
if choice == "batch_full":
    # [A] §3-B: dump scope 확장 tcl 생성 → §6: batch 전체 재실행
    new_tcl = await prepare_dump_scope(
        input_tcl=original_tcl,
        additional_signals=missing_signals,
    )
    await sim_batch_run(test_name=test, ...)

elif choice == "batch_restore":
    # [A'] Batch-restore: L1 restore → probe 추가 → run → 새 SHM (GUI 불필요)
    await sim_batch_run(
        test_name=test,
        from_checkpoint=nearest_checkpoint,
        probe_signals=missing_signals,
        shm_path=f"dump/{test}_extra.shm",
    )

elif choice == "bridge_interactive":
    # [B] Bridge interactive: restore → probe → dump → CSV → watchpoint 정지 유지
    await bisect_restore_and_debug(
        checkpoint_name=nearest_checkpoint,
        probe_signals=missing_signals,
        watch_signal_path=...,
        watch_op=..., watch_value=...,
        keep_alive=True,    # [B]: watchpoint 시점에서 시뮬레이터 유지
    )
    # → 반환 후 사용자가 deposit_value / get_signal_value / sim_run 등 직접 실행
    # → 완료 후 수동으로 shutdown_simulator() 호출
```

### [B] Option — Bridge interactive (keep_alive)

**목적**: restore → probe → dump → CSV 완료 후 시뮬레이터를 종료하지 않고, watchpoint가 걸린 시점(버그 발생 직전/직후)에 그대로 정지 상태로 유지하여 추가 디버깅을 수행한다. `bisect_restore_and_debug(keep_alive=True)`로 진입.

> **참고**: `keep_alive=False`는 Bridge가 이미 실행 중일 때 dump-only 용도로 여전히 사용 가능하나, **신규 세션에서는 [A'] Batch-restore를 사용할 것** (GUI 불필요, 더 안정적).

| | [A'] Batch-restore | [B] Bridge interactive |
|--|---------------------|------------------------|
| GUI 필요 | 불필요 (batch) | 필요 (SimVision/xmsim 연결) |
| 완료 후 | 새 SHM + CSV 반환 | 시뮬레이터 watchpoint 정지 유지 |
| 추가 조작 | 불가 (batch 완료) | deposit_value / get_signal_value / sim_run / execute_tcl 가능 |
| 사용 시점 | 기본 경로 (CSV만 필요) | 수정 가설 검증, 실시간 probing 필요 시만 |

```python
@mcp.tool()
async def bisect_restore_and_debug(
    checkpoint_name: str,           # 복원할 checkpoint
    probe_signals: list[str],       # 추가할 probe 신호
    watch_signal_path: str,         # watchpoint 신호
    watch_op: str,                  # "==", "!=", ">", "<"
    watch_value: str,               # watchpoint 조건 값
    run_duration: str = "10ms",     # watchpoint 도달까지 최대 실행 시간
    shm_path: str = "/tmp/debug.shm",
    keep_alive: bool = True,        # True=[B] 정지 유지 / False=dump 후 shutdown (보조)
) -> str:
    """Restore checkpoint, add probes, run to watchpoint, dump CSV.

    keep_alive=True  [B]: dump + CSV 후 watchpoint 시점에서 시뮬레이터 정지 유지.
                          가장 일반적인 인터랙티브 디버깅 진입점:
                            - deposit_value()    : 수정 가설 즉시 검증
                            - get_signal_value() : 현재 시점 신호 확인
                            - sim_run()          : watchpoint 이후 계속 실행
                            - execute_tcl()      : 임의 Tcl 명령 실행
                            - bisect_signal()    : Mode A로 추가 이진 탐색
    keep_alive=False    : dump + CSV 후 shutdown_simulator(). Bridge 이미 실행 중일 때만.
                          신규 세션에서 CSV만 필요하면 [A'] Batch-restore 사용 권장.
    """
    bridge = _get_bridge()

    # 1. restore
    await restore_checkpoint(name=checkpoint_name)

    # 2. 새 SHM + probe 추가
    await execute_tcl(f"database -open {shm_path} -shm -default")
    await probe_add_signals(signals=probe_signals)

    # 3. watchpoint 설정 후 실행
    await watch_signal(signal=watch_signal_path, op=watch_op, value=watch_value)
    result = await sim_run(duration=run_duration)

    # 4. dump → CSV
    await execute_tcl(f"database -close {shm_path}")
    csv = await extract_waveform_csv(shm_path=shm_path, signals=probe_signals)

    if keep_alive:
        # watchpoint 시점에서 정지 중 — 추가 디버깅 가능 상태
        pos = await sim_status()
        return (
            f"[keep_alive] Simulator paused at: {pos}\n"
            f"CSV extracted: {csv}\n\n"
            f"Available next actions:\n"
            f"  deposit_value(signal=..., value=...)  # 수정 가설 검증\n"
            f"  get_signal_value(signal=...)          # 현재 시점 신호 확인\n"
            f"  sim_run(duration=...)                 # 계속 실행\n"
            f"  execute_tcl(command=...)              # 임의 Tcl\n"
            f"  bisect_signal(...)                    # Mode A 추가 탐색\n"
            f"  shutdown_simulator()                  # 완료 후 종료"
        )
    else:
        await shutdown_simulator()
        return f"Done. CSV: {csv}"
```

**bisect_restore_and_debug 실행 흐름 (keep_alive 분기):**

```
bisect_restore_and_debug(checkpoint_name, probe_signals, watch_*, keep_alive=True)
    │
    ├─ restore_checkpoint("before_bug")
    │       ↓
    ├─ execute_tcl("database -open /tmp/debug.shm -shm -default")
    ├─ probe_add_signals(["top.hw...r_regAddr", "top.hw...r_state"])
    │       ↓
    ├─ watch_signal(signal="top.hw...r_regAddr", op="==", value="8'h10")
    ├─ sim_run("10ms")   ← watchpoint 조건 충족 시 자동 정지
    │       ↓
    ├─ execute_tcl("database -close /tmp/debug.shm")
    ├─ extract_waveform_csv(...)   ← CSV 반환
    │       ↓
    │   keep_alive=False (보조)        keep_alive=True [B]
    │       │                               │
    │   shutdown_simulator()           시뮬레이터 watchpoint 정지 유지
    │   (Bridge 이미 실행 중일 때만)        │
    │   신규 세션: [A'] Batch-restore   ├─ deposit_value(signal, value)  ← 수정 가설 즉시 검증
    │   사용 권장                       ├─ get_signal_value(signal)      ← 현재 시점 신호 확인
    │                                  ├─ sim_run("1ms")                ← watchpoint 이후 실행
    │                                  ├─ execute_tcl("puts [scope]")   ← 임의 Tcl
    │                                  ├─ bisect_signal(...)            ← Mode A 추가 탐색
    │                                  └─ shutdown_simulator()          ← 수동 종료
    │
    └─ 반환: CSV 내용 + (keep_alive=True 시) 현재 시각 + 가능한 다음 명령 목록
```

### 파라미터 설계

```python
@mcp.tool()
async def bisect_signal_dump(
    shm_path: str,          # SHM 파일 경로 (예: "dump/ci_top.shm/ci_top.trn")
    signal: str,            # 신호 경로 (예: "top.hw.u_ext...r_regAddr")
    op: str,                # 비교 연산자: "==", "!=", ">", "<"
    value: str,             # 목표 값 (예: "16", "0xFF")
    start_ns: int,          # 검색 시작 시각
    end_ns: int,            # 검색 종료 시각
    context_signals: list[str] = [],  # 함께 추출할 추가 신호
    precision_ns: int = 100,          # 최종 정밀도
) -> str:
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 2-1 | `bisect_signal` 내부를 §4 "Restore → Watchpoint → Dump → CSV" 패턴으로 변경 (Mode A) | server.py, mcp_bridge.tcl |
| 2-2 | `bisect_signal_dump` 신규 tool 구현 | server.py |
| 2-3 | simvisdbutil CSV 추출 wrapper 함수 | server.py 또는 별도 모듈 |
| 2-4 | CSV in-memory 파싱 + binary search 로직 | csv_cache.py |
| 2-5 | CSV 캐시 활용: §5 csv_cache와 연동, 재추출 없이 재검색 | server.py |
| 2-6 | SHM 신호 존재 확인: simvisdbutil로 신호 유무 검증 | server.py |
| 2-7 | `request_additional_signals` hook: 신호 미존재 시 3가지 선택 요청 — [A] Batch full / [A'] Batch-restore / [B] Bridge interactive | server.py |
| 2-8 | 선택 후 자동 실행: [A] prepare_dump_scope → batch, [A'] sim_batch_run(from_checkpoint=...), [B] bisect_restore_and_debug(keep_alive=True) | server.py |
| 2-9 | `bisect_restore_and_debug` tool: `keep_alive=True`([B] watchpoint 정지 유지 → deposit/get_signal/sim_run/execute_tcl) / `keep_alive=False`(보조: Bridge 이미 실행 중일 때만) | server.py |

---

## 3. Dump Signal Scope 사전 분석 및 조정

### 현재 문제

테스트벤치가 `$shm_open`, `$shm_probe` 등으로 dump scope를 결정하므로, 디버깅에 필요한 내부 신호가 포함되지 않을 수 있다.

### 수정 방안

**3-A: probe_control 확장 — 신호 추가 기능**

```python
@mcp.tool()
async def probe_add_signals(
    signals: list[str],       # 추가할 신호 목록
    database: str = "",       # 대상 SHM database (빈칸이면 활성 DB)
) -> str:
```

Tcl 구현:
```tcl
# SimVision에서 실행 중인 시뮬레이션에 신호 추가
foreach sig $signal_list {
    probe -create -shm $sig
}
```

**3-B: Input Tcl 분석 + 신호 추가 Tcl 생성 (batch mode용)**

batch mode에서는 mcp_bridge가 없으므로 3-A를 사용할 수 없다. 대신 시뮬레이션 실행 전에 기존 input tcl을 읽어 probe scope를 확인하고, 추가 신호가 필요하면 확장된 tcl을 생성하여 사용한다.

```python
@mcp.tool()
async def prepare_dump_scope(
    input_tcl: str = "",            # 기존 input tcl 경로 (예: "scripts/setup_rtl.tcl")
                                    # 빈칸이면 §7 script discovery로 자동 탐지
    additional_signals: list[str],  # 추가할 개별 신호 목록
    additional_scopes: list[str] = [],  # 추가할 계층 scope (예: "top.hw.u_ext.u_ext_d_main")
    output_tcl: str = "",           # 출력 tcl 경로 (빈칸이면 자동 생성: <input>_debug.tcl)
    sim_dir: str = "",              # 시뮬레이션 디렉토리 (input_tcl 탐지에 사용)
) -> str:
    """Read existing input tcl, analyze probe scope, add signals if needed.

    Args:
        input_tcl: Path to existing input tcl. If empty, auto-discovers
                   setup_rtl.tcl / setup_gate.tcl in sim_dir/scripts/.
        additional_signals: Individual signal paths to ensure in dump.
        additional_scopes: Hierarchy scopes to add (probe -create -depth all).
        output_tcl: Where to write the extended tcl. If empty, appends
                    '_debug' to input filename (e.g. setup_rtl_debug.tcl).
        sim_dir: Simulation directory for auto-discovery.

    Returns: Path to the tcl to use (original if no changes needed, new if extended).
    """
```

**내부 흐름:**

```
0. input_tcl 결정:
   - 인자로 주어지면 → 그대로 사용
   - 빈칸이면 → sim_dir/scripts/ 에서 탐색:
     setup_rtl.tcl → setup_gate.tcl → setup_ams_rtl.tcl 순서
1. 기존 input tcl 읽기 (SSH file_read)
2. probe 명령 파싱:
   - "database -open <path> -shm" → SHM 경로 추출
   - "probe -create <scope> -depth all" → 기존 probe scope 확인
3. 추가 신호 coverage 판단:
   - 기존 scope가 "top -depth all"이면 → 모든 신호 포함 → 수정 불필요
   - 특정 scope만 있으면 → 추가 신호가 해당 scope에 포함되는지 확인
4. 수정 필요 시:
   - 원본 tcl 내용 복사
   - "run" 명령 앞에 추가 probe 명령 삽입
   - 신규 tcl 파일 생성 (예: scripts/setup_rtl_debug.tcl)
5. 수정 불필요 시:
   - 원본 tcl 경로 그대로 반환
```

**생성되는 tcl 예시:**

```tcl
# === Original setup_rtl.tcl content ===
set test_name $env(TEST_NAME)
set shm_path "../dump/ci_top_${test_name}.shm"
database -open $shm_path -shm
probe -create top -unpacked 100 -database $shm_path -depth all -all -memories -dynamic

# === Additional debug probes (auto-generated) ===
probe -create top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_regAddr -database $shm_path
probe -create top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_startStopDetState -database $shm_path

run 10000ms
```

**기존 scope가 충분한 경우의 판단 로직:**

| 기존 probe | 추가 신호 | 판단 |
|-----------|----------|------|
| `probe -create top -depth all` | `top.hw.u_ext...r_regAddr` | ✓ 포함 — 수정 불필요 |
| `probe -create top.hw -depth 2` | `top.hw.u_ext.u_ext_d_main...r_regAddr` | ✗ depth 부족 — 추가 필요 |
| `probe -create top.hw.u_ext -depth all` | `top.sw.test_id` | ✗ scope 외 — 추가 필요 |

**3-C: 테스트케이스 분석 → dump 신호 리스트 생성 (AI 워크플로우)**

이것은 tool이 아닌 **워크플로우** 차원의 개선:

1. 테스트케이스 파일을 읽어 검증 포인트 식별 (PASS/FAIL 판별 신호)
2. 관련 RTL 모듈의 분석서에서 내부 신호 목록 도출
3. **Bridge mode**: `probe_add_signals`(3-A)로 실행 중 추가
4. **Batch mode**: `prepare_dump_scope`(3-B)로 실행 전 tcl 생성
5. 시뮬레이션 실행

**sim_batch_run / sim_batch_regression 연동:**

`prepare_dump_scope`는 `sim_batch_run`과 `sim_batch_regression` 양쪽에서 모두 호출된다. `sim_batch_regression`은 `sim_batch_run`을 반복 호출하는 구조이므로, regression 레벨에서 `dump_signals`를 받아 각 테스트의 `sim_batch_run`으로 전달한다.

```python
# sim_batch_run 내부 — 단일 테스트
if dump_signals:
    # 3-B: 기존 tcl 분석 → 필요 시 확장 tcl 생성
    actual_tcl = await prepare_dump_scope(
        input_tcl=original_setup_tcl,
        additional_signals=dump_signals,
    )
    # run_sim에서 -input 옵션으로 actual_tcl 사용

# [기본] sim_batch_regression 내부 — dump_signals를 각 sim_batch_run에 전달
# (sim_batch_run이 매번 prepare_dump_scope를 호출 — 신호 목록이 같으면 tcl 재생성 없음)
for test_name in test_names:
    await sim_batch_run(
        test_name=test_name,
        dump_signals=dump_signals,   # → sim_batch_run 내부에서 prepare_dump_scope 호출
        ...
    )
```

**중요**: `prepare_dump_scope`는 tcl을 **한 번만 생성**하면 모든 테스트에 재사용 가능하다. `sim_batch_regression` 내부에서 첫 번째 테스트 실행 전에 1회 생성 후 전 테스트 공유하는 방식이 더 효율적이다:

```python
# [최적화] sim_batch_regression — prepare_dump_scope 1회 실행 후 shared_tcl 공유
if dump_signals:
    shared_tcl = await prepare_dump_scope(  # 1회만 호출
        input_tcl=original_setup_tcl,
        additional_signals=dump_signals,
    )
else:
    shared_tcl = original_setup_tcl

for i, test_name in enumerate(test_names):
    await sim_batch_run(
        test_name=test_name,
        input_tcl=shared_tcl,
        from_checkpoint=from_checkpoint,    # 일반 실행: L1 공유(없으면 첫 실행에서 생성) + 각 테스트 L2 저장
                                            # [A'] restore 실행: 지정 checkpoint에서 시작, L1/L2 생성 생략
        ...
    )
```

**regression용 dump_signals 설계 원칙 — 포괄적 신호 집합**

`sim_batch_run`의 `dump_signals`는 "이 테스트에서 필요한 신호"지만, `sim_batch_regression`의 `dump_signals`는 의미가 다르다: **regression 내 모든 테스트에서 공유되는 단일 tcl로 확장**되므로, 특정 테스트의 실패 신호만 넣으면 다른 테스트의 dump에 그 신호가 없어 나중에 재실행이 필요하다.

```
나쁜 예 (특정 테스트 실패 신호만):
    dump_signals = ["top.hw.u_ext...r_regAddr"]   ← TOP013 실패 시 추가한 신호
    → TOP015, TOP016 dump에도 r_regAddr만 추가됨
    → TOP015가 r_state 신호 필요 시 regression 재실행 필요

좋은 예 (regression 전체 커버 포괄 집합):
    dump_signals = [
        "top.hw.u_ext...r_regAddr",       # 주소 레지스터
        "top.hw.u_ext...r_state",         # 상태 머신
        "top.hw.u_ext...r_dataState",     # 데이터 상태
        "top.hw.u_ext...r_byteCount",     # 전송 카운터
        ...                               # 검증 대상 모듈의 핵심 내부 신호 전체
    ]
    → 어느 테스트가 실패해도 재실행 없이 CSV 분석 가능
```

**포괄 신호 집합 결정 방법:**

| 소스 | 방법 |
|------|------|
| 모듈 분석서 | `.ai/analysis/{module}.analysis.md`의 FSM 전이표 + 주요 레지스터 목록 |
| 기존 디버깅 이력 | 과거 버그에서 확인이 필요했던 신호 누적 |
| AI 워크플로우 (§3-C) | 테스트케이스 분석 → 검증 포인트 → 관련 내부 신호 도출 |
| 실용 기준 | 검증 대상 모듈의 FSM state + 주요 레지스터 + 버스 인터페이스 신호 |

**도구 지원**: `sim_batch_regression` 호출 전 AI(Claude)가 포괄 집합을 직접 구성한다.

> **아키텍처 결정**: `suggest_regression_signals`는 MCP tool로 구현하지 않는다.
>
> xcelium-mcp Python server는 **cloud0에서 실행** (SSH stdio transport)되므로,
> 로컬 분석서 파일(`.ai/analysis/*.md`)에 접근할 수 없다.
> 핵심 작업인 "분석서를 읽고 신호를 추론하는 것"은 Python 코드가 아닌 AI 추론이다.
> Claude는 로컬 파일(`Read` tool)과 cloud0 파일(`ssh-mcp`)에 모두 직접 접근할 수 있으므로,
> Claude 워크플로우로 처리하는 것이 자연스럽다.

**Claude 워크플로우 (MCP tool 없이):**

```
# regression 실행 전 Claude가 직접 수행:

1. Read(".ai/analysis/ext_i2cSlave.analysis.md")
   → FSM 전이표, 주요 레지스터 목록 추출

2. mcp__ssh__ssh_run("cat ~/git.clone/venezia-t0/design/top/sim/ncsim/tests/TOP013.sv")
   mcp__ssh__ssh_run("cat .../TOP015.sv")
   mcp__ssh__ssh_run("cat .../TOP016.sv")
   → 각 테스트의 검증 포인트(PASS/FAIL 판별 신호) 추출

3. AI 추론: 1 + 2의 합집합으로 포괄 신호 목록 구성

4. sim_batch_regression(test_names=[...], dump_signals=[...포괄 집합...])
```

```python
# 최종 호출 예
await sim_batch_regression(
    test_names=["TOP013", "TOP015", "TOP016"],
    dump_signals=[
        "top.hw.u_ext...r_regAddr",    # ← Claude가 분석서·테스트케이스 읽고 결정
        "top.hw.u_ext...r_state",
        "top.hw.u_ext...r_dataState",
        "top.hw.u_ext...r_byteCount",
    ],
)
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 3-1 | `probe_add_signals` tool (bridge mode용) | server.py, mcp_bridge.tcl |
| 3-2 | `probe -create -shm` Tcl 명령 래핑 | mcp_bridge.tcl |
| 3-3 | `prepare_dump_scope` tool (batch mode용) | server.py |
| 3-4 | Input tcl 파싱: probe scope/database 추출 | server.py 또는 utils.py |
| 3-5 | 신호 coverage 판단 로직 (scope+depth 매칭) | server.py |
| 3-6 | 확장 tcl 생성 (원본 + 추가 probe, run 앞 삽입) | server.py |
| 3-7 | `sim_batch_run` + `sim_batch_regression`과 `prepare_dump_scope` 연동: `dump_signals` 파라미터로 tcl 확장, regression은 1회 생성 후 전 테스트 공유 | server.py |
| 3-8 | regression 포괄 신호 집합 구성: MCP tool 없음 — Claude가 `Read`(로컬 분석서) + `ssh-mcp`(cloud0 테스트케이스)로 직접 추론 후 `sim_batch_regression(dump_signals=...)` 호출 | Claude workflow (server.py 수정 불필요) |

---

## 4. save/restore 아키텍처 변경 — "Restore → Dump → CSV" 패턴

### 검증 결과 (2026-03-27)

**"restore → probe_add → run → dump → CSV" 아키텍처 검증 완료.**

테스트 조건: `setup_rtl_mcp_batch.tcl`에서 `database`/`probe` 주석 처리 (기본 dump 없음).

| 단계 | Tcl 명령 | 결과 |
|------|----------|------|
| 1. run 5ms + checkpoint save | `save_checkpoint("no_dump_test")` | OK — `{sim_dir}/checkpoints/` 저장 (v3 이전 검증 시 `/tmp/mcp_checkpoints/`, v3에서 위치 변경) |
| 2. restore | `restore_checkpoint("no_dump_test")` | OK — 5ms로 복원 |
| 3. 새 SHM 열기 | `database -open /tmp/test_no_dump.shm -shm -default` | OK — "Created default SHM database" |
| 4. 신호 probe 추가 | `probe -create top...r_regAddr -database /tmp/test_no_dump.shm` | OK — "Created probe 1" |
| 5. 3ms 더 실행 | `run 3ms` | OK — 5ms → 8ms |
| 6. database close | `database -close /tmp/test_no_dump.shm` | OK |
| 7. simvisdbutil CSV 추출 | `-CSV -SIGNAL top...r_regAddr` | OK — `5ms=0x20, 6.3ms=0x21` |

**결론**: 기본 dump 없이 시작해도, checkpoint restore 후 원하는 신호만 선택적으로 probe → dump → CSV 추출 가능. §5-B "Bridge" 경로 전체 동작 확인.

---

### 현재 문제

1. **SVNOCL 에러**: `save -simulation` 실행 시 라이선스 에러
2. **restore 후 $finish 점프**: checkpoint 복원 후 `run` 시 즉시 종료
3. **반복 probing 비효율**: restore → run → get_signal_value를 N회 반복

### 아키텍처 변경: 반복 probing → 1회 dump + CSV 분석

**기존 (v2) — 반복 interactive probing:**
```
save(10ms) → restore → run(5ms) → get_signal_value → restore → run(5ms) → get_signal_value → ...
                                    ↑ N회 반복, 매번 시뮬레이션 실행
```

**변경 (v3) — Restore → Watchpoint → Dump → CSV:**
```
save(10ms)
    ↓
restore → watch_signal(조건) → probe enable → run → watchpoint에서 stop
    ↓
SHM dump = checkpoint ~ watchpoint 구간만 (최소 크기)
    ↓
simvisdbutil → CSV 추출 → in-memory 분석
    ↓
추가 신호 필요? → SHM에 있음? → CSV 재추출 (자동, 수 초)
    ↓                  │
    │                  └─ SHM에 없음 → ★ 사용자 선택 Hook
    │                         ├─ [A] Batch full: prepare_dump_scope → 전체 재실행(time 0~) → 새 SHM → CSV
    │                         ├─ [A'] Batch-restore (권장): sim_batch_run(from_checkpoint=L1 또는 L2, probe_signals=[...])
    │                         │         → TCL: restore → probe_add → run → 새 SHM (GUI 불필요)
    │                         │         → L1: 첫 실행 또는 다른 테스트. L2: 같은 테스트 반복 probe 시 설정 절약
    │                         │         → 추가 신호 필요 시 [A'] 반복 가능
    │                         └─ [B] Bridge interactive: restore → probe_add → watchpoint → run → dump → CSV
    │                                                     → 시뮬레이터 watchpoint 정지 유지 (keep_alive)
    │                                                         ├─ deposit_value()   : 수정 가설 즉시 검증
    │                                                         ├─ get_signal_value(): 현재 시점 신호 확인
    │                                                         ├─ sim_run()         : 계속 실행
    │                                                         └─ shutdown_simulator() : 완료 후 수동 종료
    │                                                     (실시간 조작 필요 시만)
    ↓
watchpoint 변경? → restore → 새 watchpoint → run → 새 dump → CSV
    ↓
완료
```

**핵심 장점:**

| 항목 | 기존 (반복 probing) | 변경 (Restore → Watchpoint → Dump → CSV) |
|------|--------------------|-----------------------------------------|
| 시뮬레이션 실행 횟수 | N회 (bisect 반복) | **1~2회** (dump + 신호 추가 시 1회 더) |
| dump 범위 | 전체 시뮬레이션 | **checkpoint ~ watchpoint만** (최소 크기) |
| 신호 추가 | 매 restore마다 probe 롤백 → 재추가 루프 없음; bisect tree 재시작 강제 | **자유** (restore 후 probe 추가, 같은 watchpoint까지 재실행) |
| watchpoint 변경 | watchpoint = bisect 탐색 조건 자체 → 변경 = bisect 전체 재시작 (dump 개념 없음) | restore → 새 watchpoint → run → **새 dump 필요** |
| 분석 속도 | 매 반복 시뮬레이션 대기 | **즉시** (CSV in-memory) |
| 안정성 | save/restore 반복으로 SVNOCL 빈발 | save 1회, restore 1~2회로 최소화 |

### 구현: "Restore → Dump → CSV" 흐름 (Batch-restore 기본, Bridge interactive 선택)

> **참고**: `probe_control` — xcelium-mcp v2 기존 tool (계속 사용 가능). `mode="enable"`로 SHM 기록 활성화, `mode="disable"`로 중단. `probe_add_signals` (§3-A)로 신호를 추가한 후 enable 순서로 사용. **아래 코드는 v2 수동 조합 예시**이며, v3에서는 이 전체 시퀀스를 `bisect_restore_and_debug` 한 번의 호출로 대체한다 (제거되는 것이 아니라 내부에 wrapping됨).

```python
# 1. 정상 구간까지 실행 후 checkpoint 저장
sim_run(duration="10ms")
save_checkpoint(name="before_bug")

# 2. restore → watchpoint 설정 → 관심 구간만 dump
restore_checkpoint(name="before_bug")
watch_signal(signal="top.hw...r_regAddr", op="==", value="8'h10")  # 버그 조건
probe_control(mode="enable")                    # SHM 기록 시작 (v2 기존 tool — probe_add 후 enable)
sim_run(duration="5ms")                          # watchpoint에서 자동 stop → dump = checkpoint~watchpoint만
watch_clear()

# 3. CSV 추출 + in-memory 분석 (작은 dump → 빠른 추출)
extract_waveform_csv(shm_path="dump/ci_top.shm", signals=[...])
bisect_signal_dump(shm_path="dump/ci_top.shm", signal="...", op="==", value="...")

# 4. 추가 신호 필요 시 → SHM 존재 확인
#    SHM에 있음 → CSV 재추출 (자동)
extract_waveform_csv(shm_path="dump/ci_top.shm", signals=[...추가...])

#    SHM에 없음 → ★ 사용자 선택 Hook
choice = request_additional_signals(
    missing_signals=["top.hw...c_regAddr"],
    shm_path="dump/ci_top.shm",
    available_checkpoints=["before_bug"],
)

# [A] Batch full 선택 시 (checkpoint 없음 / 전체 dump 필요):
#    shutdown → prepare_dump_scope → sim_batch_run → 새 SHM → CSV
shutdown_simulator()
new_tcl = prepare_dump_scope(input_tcl=..., additional_signals=[...])
sim_batch_run(test_name=..., ...)
extract_waveform_csv(shm_path="dump/ci_top_NEW.shm", signals=[...전체...])

# [A'] Batch-restore 선택 시 (권장, checkpoint 있음):
#    restore → probe 추가 → run → 새 SHM → CSV (GUI 불필요)
sim_batch_run(
    test_name=...,
    from_checkpoint="L1_common_init",        # [A']: L1 또는 L2에서 재시작 (L2는 같은 테스트 반복 시 설정 절약)
    probe_signals=["top.hw...c_regAddr", "top.hw...r_dataState"],
    shm_path="dump/ci_top_extra.shm",
    run_duration="5ms",
)
extract_waveform_csv(shm_path="dump/ci_top_extra.shm", signals=[...전체...])
# → 추가 신호 필요 시 [A'] 반복

# [B] Bridge interactive (실시간 조작 필요 시만):
#    restore → probe 추가 → watchpoint → run → dump → CSV + keep_alive
bisect_restore_and_debug(
    checkpoint_name="before_bug",
    probe_signals=["top.hw...c_regAddr", "top.hw...r_dataState"],
    watch_signal_path="top.hw...r_regAddr",
    watch_op="==", watch_value="8'h10",
    run_duration="5ms",
    shm_path="/tmp/debug_extra.shm",
    keep_alive=True,   # [B]: watchpoint 시점에서 정지 유지
)
# → 반환: "Simulator paused at TIME: 8.3ms\nCSV: ...\nAvailable: deposit_value / ..."

# [B] 이후 — watchpoint 시점에서 직접 디버깅
deposit_value(signal="top.hw...r_regAddr", value="8'h20")   # 수정 가설 검증
get_signal_value(signal="top.hw...r_state")                   # 현재 상태 확인
sim_run(duration="1ms")                                       # 조금 더 실행

# 완료 후 수동 종료
shutdown_simulator()
```

### 기존 안정성 개선 (save/restore 자체)

save/restore 호출 횟수가 대폭 감소하지만, 여전히 안정성 개선은 필요:

**4-A: save 전 환경 검증**
```tcl
proc ::mcp_bridge::do_save {channel name} {
    variable _checkpoint_dir    ;# 4-C에서 설정된 {sim_dir}/checkpoints/

    # 1. 시뮬레이션 상태 확인 (stopped인지)
    set st [status]
    if {![string match "*stopped*" $st]} { catch {stop} }

    # 2. save 실행
    if {[catch {save -simulation $name -path $_checkpoint_dir -overwrite} err]} {
        ::mcp_bridge::send_error $channel "save failed: $err"
        return
    }

    # 3. 성공 응답
    ::mcp_bridge::send_ok $channel "saved: $name at $_checkpoint_dir"
}
```

**4-B: restore 후 $finish 방지 + stale breakpoint 정리**
```tcl
proc ::mcp_bridge::do_restore {channel name} {
    variable _checkpoint_dir    ;# 4-C에서 설정된 {sim_dir}/checkpoints/

    # 1. restore 실행
    if {[catch {restart $name -path $_checkpoint_dir} err]} {
        ::mcp_bridge::send_error $channel "restore failed: $err"
        return
    }

    # 2. stale breakpoint 정리 ($finish 방지)
    catch {stop -delete -all}

    # 3. 성공 응답
    ::mcp_bridge::send_ok $channel "restored: $name"
}
```

**4-C: checkpoint 저장 위치 — persistent location**

L1/L2 checkpoint는 `/tmp`가 아닌 시뮬레이션 디렉토리 내 persistent 위치에 저장:

```
{sim_dir}/checkpoints/
├── L1_common_init/          ← Xcelium snapshot 파일
├── L2_TOP015_setup/
└── .checkpoint_manifest     ← 컴파일 해시 + 생성 시각 기록
```

`.checkpoint_manifest` 형식:
```json
{
  "compile_hash": "abc123",
  "project": "venezia-t0",
  "feature": "sync-xfr-extension",
  "sim_dir": "/home/hoseung.lee/git.clone/venezia-t0/.../ncsim",
  "checkpoints": {
    "L1_common_init": {"time_ns": 2100000, "size_mb": 45, "created": "2026-03-27", "last_used": "2026-03-27"},
    "L2_TOP015_setup": {"time_ns": 5300000, "size_mb": 47, "created": "2026-03-27", "last_used": "2026-03-27"}
  }
}
```

**자동 무효화 정책 (compile hash 기반)**

| 트리거 | 조건 | 동작 |
|--------|------|------|
| `sim_batch_run` / `sim_batch_regression` 실행 | 현재 compile_hash ≠ manifest compile_hash | 해당 프로젝트 stale checkpoint **자동 삭제** + 로그 출력 |
| `restore_checkpoint` 호출 | hash 불일치 | restore **거부** + stale checkpoint **자동 삭제** + 재생성 안내 |

두 경우 모두 주관적 판단이 불필요한 객관적 조건이므로 `dry_run` 없이 자동 삭제한다. stale checkpoint를 restore하면 시뮬레이터 크래시 또는 잘못된 결과가 나오므로 자동 삭제가 오히려 더 안전하다.

```python
# sim_batch_run 내부 — recompile 감지 시 stale checkpoint 자동 삭제
current_hash = _compute_compile_hash(sim_dir)
manifest = _read_manifest(checkpoint_dir)
if manifest and manifest["compile_hash"] != current_hash:
    _auto_cleanup_stale(checkpoint_dir, reason="recompile detected")
    # 로그: "Stale checkpoints removed (compile hash changed: abc123 → def456)"

# restore_checkpoint 내부 — hash 불일치 시 거부 + 자동 삭제
if manifest["compile_hash"] != current_hash:
    _auto_cleanup_stale(checkpoint_dir, reason="hash mismatch on restore")
    return ERROR("Checkpoint invalid after recompile. Stale checkpoints removed. "
                 "Re-run simulation to create new checkpoints.")
```

**compile_hash 계산**: `inca/` 디렉토리 내 오브젝트 파일들의 최신 수정 시각(mtime) 기반 MD5.

bridge 초기화 시 **L1/L2는 절대 자동 삭제하지 않는다**. 검증 기간 전체에 걸쳐 유지되어야 한다.

```tcl
proc ::mcp_bridge::on_init {} {
    # /tmp 임시 파일만 정리 (legacy 경로, bisect 잔여물)
    # L1/L2가 있는 {sim_dir}/checkpoints/ 는 건드리지 않는다
    foreach dir {/tmp/mcp_init} {
        if {[file exists $dir]} { file delete -force $dir }
    }
}
```

**bisect 임시 checkpoint 불필요**: v3 in-memory binary search로 대체되므로 `/tmp/mcp_bisect/` 디렉토리 자체가 불필요하다.

`save_checkpoint` tool의 기본 저장 경로:
```python
@mcp.tool()
async def save_checkpoint(
    name: str,
    checkpoint_dir: str = "",   # 빈칸 = 자동 결정: {sim_dir}/checkpoints/
) -> str:
    # checkpoint_dir이 비어 있으면 sim_dir에서 자동 결정
    # manifest 갱신 포함
```

**4-D: save 실패 시 graceful fallback**
```
"save failed (SVNOCL). Falling back to batch dump mode."
```

**4-E: Checkpoint 수동 정리 (`cleanup_checkpoints` tool)**

검증 기간이 끝나거나 디스크 공간이 부족할 때 사용자가 명시적으로 checkpoint를 정리한다. **자동 삭제는 절대 없으며**, 반드시 사용자가 직접 호출한다.

```python
@mcp.tool()
async def cleanup_checkpoints(
    mode: str = "list",           # "list" | "project" | "stale" | "all"
    project_path: str = "",       # 특정 sim_dir (mode="project" 시 필수)
    older_than_days: int = 0,     # N일 이상 last_used인 것만 (0=전체)
    dry_run: bool = True,         # True=삭제 없이 목록만 / False=실제 삭제
) -> str:
    """
    mode="list"    : 모든 프로젝트의 checkpoint 목록 + 크기 + 날짜 출력
    mode="project" : project_path 지정 프로젝트의 checkpoint 삭제
    mode="stale"   : compile_hash 불일치 (구버전) checkpoint만 삭제
    mode="all"     : 모든 프로젝트의 모든 checkpoint 삭제

    dry_run=True (기본): 삭제 없이 "삭제 예정" 목록만 반환.
                          사용자가 확인 후 dry_run=False로 재호출.
    """
```

**Central Registry** (`~/.xcelium_mcp/checkpoint_registry.json`):

`save_checkpoint` 호출 시 자동으로 이 registry에 등록된다. `cleanup_checkpoints(mode="list")`는 registry를 읽어 모든 프로젝트의 checkpoint를 한 눈에 보여준다.

```json
{
  "checkpoints": [
    {
      "project": "venezia-t0",
      "feature": "sync-xfr-extension",
      "checkpoint_dir": "/home/.../ncsim/run/checkpoints",
      "total_size_mb": 92,
      "compile_hash": "abc123",
      "last_used": "2026-03-27",
      "names": ["L1_common_init", "L2_TOP015_setup", "L2_TOP016_setup"],
      "notes": "L1: [A]/[A']/[B] 공통. L2_*: [A'] Batch-restore + [B] Bridge interactive 공통 (반복 실행 시 테스트별 설정 구간 절약)"
    },
    {
      "project": "other-project",
      "feature": "feature-x",
      "checkpoint_dir": "/home/.../other/checkpoints",
      "total_size_mb": 210,
      "last_used": "2026-02-10"
    }
  ]
}
```

**사용자 주도 cleanup 워크플로우 (Claude 보조):**

```
# Step 1: 목록 확인 (항상 dry_run=True로 시작)
cleanup_checkpoints(mode="list")
→ 출력 예:
   venezia-t0 / sync-xfr-extension   92MB   last_used: 2026-03-27  [L1, L2_TOP015, L2_TOP016]
   other-project / feature-x        210MB   last_used: 2026-02-10  [L1, L2_feature_x_main]
   ─────────────────────────────────────────────────────────────────
   총 302MB

# Step 2: 사용자가 선택 ("other-project는 완료됐으니 지워줘")
cleanup_checkpoints(mode="project", project_path=".../other/checkpoints", dry_run=True)
→ 삭제 예정:
   /home/.../other/checkpoints/L1_common_init/    (105MB)
   /home/.../other/checkpoints/L2_feature_x_main/ (105MB)
   총 210MB 삭제 예정. dry_run=False로 재호출하면 실제 삭제됩니다.

# Step 3: 사용자 확인 후 실제 삭제
cleanup_checkpoints(mode="project", project_path=".../other/checkpoints", dry_run=False)
→ Deleted: 210MB freed. Registry updated.
```

**stale checkpoint 자동 식별:**
```
cleanup_checkpoints(mode="stale", dry_run=True)
→ compile_hash 불일치 checkpoint (RTL/TB 수정 후 남은 구버전):
   venezia-t0 L1_common_init  hash: abc123 (현재: def456)  45MB  → 삭제 예정
```

**모든 삭제 후 registry 자동 업데이트** (dangling entry 제거).

### Save Point 전략 — 최적의 checkpoint 시점 결정

checkpoint는 restore 후 시뮬레이션을 다시 실행하는 시작점이므로, **정상 동작이 끝나고 버그 구간이 시작되는 직전**에 저장하는 것이 이상적이다. 이렇게 하면 restore 후 최소 시간만 재실행하면 된다.

#### 전략 1: 계층적 Save Point (공통 구간 + 테스트별 구간)

> **구체적 구현 계획**: `tb-common-init-alignment.plan.md` — TOP000~TOP016 초기화 분석, 5그룹 분류, `common_init.inc` 추출, 테스트별 수정 계획

```
시뮬레이션 시작                    테스트 고유 구간 시작              버그 발생
    │                                  │                              │
    ├── 공통 초기화 구간 ──────────────┤                              │
    │   (클럭 안정, PLL lock,          │── 테스트 고유 설정 ──────────┤
    │    리셋 해제, 프리앰블)          │   (addressing mode enable,   │── 관심 구간
    │                                  │    레지스터 초기값 설정)      │
    │                                  │                              │
    └─ ★ L1 Save Point               (L2 Save Point: [A']+[B] 공통) └─ watchpoint
       ([A]/[A']/[B] 공통)              ([A'] 반복 probe 시 설정 절약   (dump 종료)
                                         [B] 반복 디버깅 표준 흐름)
```

**L1 Save Point (공통)**:
- 여러 테스트케이스에서 공통으로 수행되는 초기화 구간의 끝
- 예: 클럭 안정 + 리셋 해제 + `i2c_xfr_enable()` + `pcm_preamble()` 완료 시점
- **TB 분석 캐시(Phase 0)에서 자동 식별**: 모든 테스트의 `run_test` 앞부분을 비교하여 공통 시퀀스 끝 시점 결정
- 한 번 저장하면 모든 테스트에서 재사용 → 초기화 시간(수 ms) 절약

**L1 Save Point 무효화 조건 — 재생성 필요:**
- RTL 코드(설계) 변경 또는 추가
- 테스트벤치 코드 변경 (공유 모델, include 파일 등)
- 컴파일 옵션/define 변경

위 경우 반드시 **재컴파일 → 시뮬레이션 재실행 → 공통 구간 도달 → L1 재저장**해야 한다. 이전 L1 checkpoint는 컴파일 결과가 달라져 restore 시 불일치/크래시 위험.

**L1 유효성 관리:**
```
1. L1 저장 시: 컴파일 hash (소스 파일 목록의 md5) 함께 기록
2. L1 restore 시: 현재 컴파일 hash와 비교
3. 불일치 → "L1 checkpoint outdated. Recompile + re-save required." 경고
4. 일치 → 정상 restore
```

**L2 Save Point (테스트별) — [A'] Batch-restore + [B] Bridge interactive 공통**:
- 각 테스트 고유의 설정 구간 끝 (버그 관심 구간 직전)
- 예: TOP015의 경우 `i2c_addr_mode_enable()` + `pcm_reg_write(sync_xfr_en)` 완료 시점
- **L2 생성 시점**: `sim_batch_run`(일반 실행) 및 `sim_batch_regression` 실행 시 테스트별로 **자동 저장**. 반복 디버깅/probe 추가 시 이미 존재.
- **[A'] Batch-restore**: L2 이미 존재 → `from_checkpoint=L2_<test>`로 바로 사용. L2 없으면 해당 테스트만 재생성.
- **[B] Bridge interactive**: L2 이미 존재 → restore 후 진입. L2 없으면 해당 테스트만 재생성.
- **무효화 조건**: L1과 동일 (RTL/TB 변경 시 재생성 필요). L1이 무효화되면 L2도 자동 무효화

```python
# sim_batch_run / sim_batch_regression 표준 흐름 — L1 + L2 자동 생성
# (일반 실행, from_checkpoint 미지정)

# (1) 공통 초기화 → L1 저장 (전체 공통, 1회)
sim_run(duration="2ms")
save_checkpoint(name="L1_common_init")       # L1: [A]/[A']/[B] 공통

# (2) 테스트별 설정 → L2 저장 (각 테스트마다, 매 실행 시 자동 저장)
sim_run(duration="3ms")                      # 테스트 고유 설정 구간
save_checkpoint(name="L2_TOP015_setup")      # L2: sim_batch_run/regression에서 표준 저장

# (3) 테스트 본체 실행 + dump
sim_run(duration="5ms")

# 반복 디버깅 [A']: L2 이미 존재 → 바로 사용 (L2 없으면 해당 테스트만 재생성)
sim_batch_run(test_name="TOP015", from_checkpoint="L2_TOP015_setup", probe_signals=[...])

# 반복 디버깅 [B]: L2 restore → 진입 (L2 없으면 해당 테스트만 재생성)
restore_checkpoint(name="L2_TOP015_setup")
watch_signal(...)
sim_run(duration="5ms")
```

#### 전략 2: 이상 전이 시점 기반 Save Point

버그 시점을 모를 때, 정상→이상 전이 시점을 찾아 그 직전에 save한다.

**방법 A (기본): Batch dump → CSV에서 전이 시점 특정**

```
1. Batch mode로 전체 시뮬레이션 실행 → full dump
2. CSV 추출: 관심 신호의 값 변화 추적
3. 정상값 → 이상값 전이 시점 T_bug 특정
4. CSV in-memory 분석으로 근본 원인 특정
```

대부분의 디버깅은 여기서 완료된다. Batch dump + CSV만으로 충분.

**방법 B (신호 부족 시): ★ 사용자 선택 Hook → Batch 재실행 / Batch-restore / Bridge interactive**

Batch dump의 기존 신호만으로 원인 특정이 안 되고, **SHM에 해당 신호가 없는 경우** 사용자에게 선택을 요청한다.

```
1. 방법 A에서 전이 시점 T_bug 특정 (CSV 분석)
2. 추가 신호 필요 → SHM에서 확인
3. SHM에 있음 → CSV 재추출 (자동, 사용자 선택 불필요)
4. SHM에 없음 → ★ 사용자 선택 Hook:
   ├─ [A] Batch full 재실행:
   │     prepare_dump_scope로 dump scope 확장 → 전체 재실행(time 0~) → 새 SHM → CSV
   │     추천: checkpoint 없음, 또는 전체 dump가 필요한 경우
   │
   ├─ [A'] Batch-restore (권장, checkpoint 있을 때):
   │     sim_batch_run(from_checkpoint=L1 또는 L2, probe_signals=[...추가...])
   │     → TCL: restore → probe_add → run → 새 SHM → CSV (GUI 불필요)
   │     → 추가 신호 필요 시 [A'] 반복
   │     추천: L1(첫 실행·다른 테스트) 또는 L2(같은 테스트 반복 probe 시 설정 절약), 관심 구간만 재실행하면 충분
   │
   └─ [B] Bridge interactive (keep_alive):
         L1/L2 checkpoint → restore → probe 추가 → watchpoint → dump → CSV
         → 시뮬레이터 watchpoint 정지 유지 → deposit_value / sim_run 등 직접 조작
         추천: 수정 가설 검증 또는 실시간 probing이 반드시 필요한 경우만
5. 선택 후 자동 실행 → CSV 분석 재개
```

**기존 dump 신호로 분석 가능하면 사용자 선택 불필요** — SHM에서 CSV 재추출로 완결.

**방법 C: 주기적 auto-save (긴 시뮬레이션용)**

시뮬레이션이 길어 Batch 전체 dump가 비현실적일 때. **Batch mode와 Bridge mode 모두 사용 가능.**

**Batch mode — input tcl에 save 명령 삽입:**

```tcl
# scripts/setup_rtl_autosave.tcl (자동 생성 또는 prepare_dump_scope에서 생성)
set test_name $env(TEST_NAME)
set shm_path "../dump/ci_top_${test_name}.shm"
set chk_dir "[file dirname [info script]]/../checkpoints"
file mkdir $chk_dir

database -open $shm_path -shm
probe -create top -unpacked 100 -database $shm_path -depth all -all -memories -dynamic

# 주기적 auto-save (매 10ms, rolling 2개)
set interval_ms 10
set total_ms 200
for {set t 0} {$t < $total_ms} {incr t $interval_ms} {
    run ${interval_ms}ms
    save -simulation auto_${t}ms -path $chk_dir -overwrite
}
```

시뮬레이션 완료 후 checkpoint 목록이 `{sim_dir}/checkpoints/`에 남아 있으므로, 이후 **Batch 또는 Bridge mode**에서 원하는 시점으로 restore → probe 추가 → dump 가능. (재부팅·OS cleanup에도 유지됨)

**Batch mode에서의 restore + probe 추가** (기본 경로):

```tcl
# scripts/setup_rtl_from_checkpoint.tcl (auto-generated)
restart auto_10ms -path $chk_dir          ;# 가장 가까운 checkpoint로 복원
database -open ../dump/ci_top_extra.shm -shm -default
probe -create top.hw...new_signal -database ../dump/ci_top_extra.shm
run 20ms                                  ;# 관심 구간만 재실행
database -close ../dump/ci_top_extra.shm
# [선택] save -simulation auto_10ms_v2 -path $chk_dir -overwrite  ;# 필요 시 별도로
```

**Bridge mode — Python API (실시간 조작 필요 시만):**

```python
async def sim_run_with_autosave(total_duration_ms, interval_ms=10):
    for t in range(0, total_duration_ms, interval_ms):
        sim_run(duration=f"{interval_ms}ms")
        save_checkpoint(name=f"auto_{t}ms")
    # 이상 발견 시 → 가장 가까운 auto checkpoint에서 restore
```

**핵심: probe 추가 시에도 Batch에서 restore + probe_add가 기본 ([A'] Batch-restore). Bridge는 deposit_value / get_signal_value 등 실시간 조작이 필요할 때만. auto-save(방법 C)는 긴 시뮬레이션에서 주기적 nearest checkpoint를 만드는 별개 개념.**

#### 전략 3: TB 분석 캐시 기반 자동 Save Point 제안

Phase 0의 TB 분석 캐시에 save point 정보를 포함시킨다. **Batch mode와 Bridge mode 모두 적용 가능.**

```markdown
## Save Points (tb_TOP015_i2c_8bit_offset_test.analysis.md)

| Level | 시점 | 조건 | 공유 여부 |
|-------|------|------|----------|
| L1 | ~0.15ms | pcm_preamble + 10 nop 완료 | 전체 공통 |
| L2 | ~8.0ms | addr_mode_enable + sync_xfr_en 설정 완료 | TOP015 전용 |
| L2b | ~8.3ms | V-18 첫 i2c_fpga_write 시작 직전 | V-18 전용 |
```

**Batch mode 적용 — input tcl 자동 생성:**

캐시의 save point 정보를 읽어 input tcl에 해당 시점의 `save` 명령을 자동 삽입한다.

```tcl
# setup_rtl_with_savepoints.tcl (캐시 기반 자동 생성 — Batch용)
set test_name $env(TEST_NAME)
set shm_path "../dump/ci_top_${test_name}.shm"
set chk_dir "[file dirname [info script]]/../checkpoints"
file mkdir $chk_dir

database -open $shm_path -shm
probe -create top -unpacked 100 -database $shm_path -depth all -all -memories -dynamic

# L1 save point (캐시: ~0.15ms) — 전체 공통, 1회 저장
run 0.15ms
save -simulation L1_common_init -path $chk_dir -overwrite

# 테스트별 설정 구간 실행
run 7.85ms
# L2 save point (캐시: ~8.0ms) — 테스트별, sim_batch_run/regression 표준 저장
save -simulation L2_${test_name}_setup -path $chk_dir -overwrite

# 테스트 본체 실행
run 10000ms
# ※ from_checkpoint 지정 시([A'] restore 실행): 이 구간 건너뜀 — restore → probe_add → run만 수행
```

`prepare_dump_scope` 또는 `sim_batch_run`이 캐시를 참조하여 이 tcl을 자동 생성.

**[A'] Batch-restore 적용**: `sim_batch_run(from_checkpoint=L2_<test>, probe_signals=[...])`로 캐시에서 checkpoint 경로를 자동 조회 → restore → probe_add → run. L2는 sim_batch_run/regression 일반 실행 시 자동 저장되므로 반복 probe 시 이미 존재. L2 없으면 해당 테스트만 재생성.

**[B] Bridge interactive 적용**: AI agent가 캐시에서 L2 checkpoint를 직접 restore하여 진입. L2는 sim_batch_run/regression에서 이미 저장됨. 없으면 `sim_run` + `save_checkpoint`로 해당 테스트만 재생성.

**핵심: sim_batch_run/regression 일반 실행 시 L1+L2 자동 저장. [A']+[B] 반복 시 L2 우선 사용 — 없으면 해당 테스트만 재생성.**

#### Save Point 전략 선택 가이드

| 상황 | 전략 | Bridge 필요? | 이유 |
|------|------|:----------:|------|
| 처음 디버깅, 버그 시점 모름 | **2-A**: Batch dump → CSV 분석 | **불필요** | Batch + CSV만으로 완결 |
| 기존 신호로 원인 특정 안됨 | **2-B**: [A'] restore → probe 추가 → dump | **불필요** | L2에서 restore, probe 추가 후 Batch 완결 |
| 테스트별 반복 실행·디버깅 (단일/복수) | **1**: L1+L2 계층 — sim_batch_run/regression 자동 저장 | 조건부 | [A'] probe 추가 / [B] 실시간 조작(deposit_value 등) 시만 |
| 긴 시뮬레이션 (100ms+) | **2-C**: 주기적 auto-save | 조건부 | 구간 dump + probe 추가 |
| TB 분석 캐시 있음 | **3**: 캐시에서 자동 제안 | 조건부 | 캐시 기반 판단 |

**핵심 원칙: sim_batch_run/regression 일반 실행 시 L1+L2 자동 저장. [A'] probe 추가도 Batch(restore)로 완결 — Bridge는 deposit_value 등 실시간 조작이 반드시 필요할 때만.**

### bisect_signal (Mode A) 변경

기존 bisect_signal도 "Restore → Dump → CSV" 패턴으로 변경:

**기존**: restore → run + watchpoint → check → restore → run + watchpoint → ... (N회)
**변경**: restore → watchpoint → run (watchpoint까지만) → dump → CSV → in-memory binary search (1회)

```tcl
proc ::mcp_bridge::do_bisect {channel args_str} {
    # 1. restore to start checkpoint
    if {[catch {restart $start_snapshot -path $chk_dir} err]} {
        ::mcp_bridge::send_error $channel "bisect restore failed: $err"
        return
    }

    # 2. watchpoint 설정 (end_ns 또는 조건)
    stop -create -condition {[value $signal] $op "$value"} -silent

    # 3. probe enable + run → watchpoint에서 stop (dump = start ~ watchpoint만)
    probe -enable *
    run ${end_ns - start_ns}ns

    # 4. simvisdbutil로 CSV 추출 (작은 dump → 빠른 추출)
    # 5. CSV in-memory binary search → 결과 반환 (JSON)
    ::mcp_bridge::send_ok $channel $result_json
}
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 4-1 | save 전 상태 검증 | mcp_bridge.tcl |
| 4-2 | restore 후 stale breakpoint 정리 + $finish 방지 | mcp_bridge.tcl |
| 4-3 | bridge 초기화 정책 변경: L1/L2(`{sim_dir}/checkpoints/`) 자동 삭제 금지. `/tmp/mcp_init` 잔여물만 정리. `/tmp/mcp_bisect` 디렉토리 불필요(v3 in-memory search로 대체) | mcp_bridge.tcl |
| 4-4 | save 실패 시 batch dump fallback 안내 | mcp_bridge.tcl |
| 4-5 | bisect_signal을 "Restore → Watchpoint → Dump → CSV" 패턴으로 변경 | mcp_bridge.tcl |
| 4-6 | restore 후 `probe_add_signals` → watchpoint → `run` → dump 생성 흐름 | server.py |
| 4-7 | `save_checkpoint` 저장 위치 변경: `/tmp/mcp_checkpoints` → `{sim_dir}/checkpoints/`. `checkpoint_dir` 파라미터 추가. manifest(`.checkpoint_manifest`) 생성/갱신 | server.py, mcp_bridge.tcl |
| 4-8 | Central registry(`~/.xcelium_mcp/checkpoint_registry.json`) 관리: save 시 자동 등록, cleanup 시 자동 제거 | server.py |
| 4-9 | `cleanup_checkpoints` tool: list/project/stale/all 모드, dry_run=True 기본, registry 조회·삭제 | server.py |
| 4-10 | compile_hash 기반 자동 무효화: `sim_batch_run` / `sim_batch_regression` 실행 시 hash 변경 감지 → stale checkpoint 자동 삭제. `restore_checkpoint` 시 hash 불일치 → restore 거부 + 자동 삭제 + 재생성 안내 | server.py |
| 4-11 | dump 생성 후 `extract_waveform_csv` + `bisect_signal_dump` 자동 연계 | server.py |
| 4-12 | L1/L2 계층적 save point 관리 (공통 + 테스트별) | server.py |
| 4-13 | `sim_run_with_autosave`: 주기적 rolling checkpoint (최근 N개 유지), 저장 위치 `{sim_dir}/checkpoints/` | server.py |
| 4-14 | TB 분석 캐시(Phase 0)에 save point 시점 정보 포함 | 분석서 형식 확장 |
| 4-15 | Batch dump → 전이 시점 자동 탐지 → save point 제안 | server.py + csv_cache.py |
| 4-16 | `sim_batch_run` + `sim_batch_regression`에 `from_checkpoint` 파라미터 추가. **일반 실행**(from_checkpoint 미지정): L1 없으면 첫 실행에서 자동 생성, 테스트별 L2 자동 저장. **[A'] restore 실행**(from_checkpoint 지정): checkpoint restore → probe_add → run (L1/L2 생성 생략). `sim_batch_regression`: from_checkpoint 미지정이면 각 테스트가 L1 공유 → L2 각자 저장 | server.py |

---

## 5. simvisdbutil CSV 추출 + in-memory 분석 인프라

### 전제: SHM dump와 CSV 추출의 관계

```
SHM dump (수 GB)                         CSV (수 MB)
┌──────────────────────────┐              ┌────────────────────┐
│ 수천~수만 개 신호가       │  simvisdbutil │ 분석에 필요한       │
│ 전체 시뮬레이션 시간에    │ ───────────→ │ 특정 신호만,         │
│ 걸쳐 기록됨              │  -sig, -range │ 특정 시간 범위만     │
│ (probe -depth all 등)    │              │ 선별 추출            │
└──────────────────────────┘              └────────────────────┘
```

**핵심 전제**: 시뮬레이션 실행 시 `probe -create top -depth all` 등으로 **대부분의 신호가 SHM에 이미 기록**되어 있다. SHM에는 수천 개 신호 × 전체 시간이 들어 있으므로 파일이 크다.

CSV 추출(`simvisdbutil`)은 이 SHM에서 **분석에 필요한 신호만 골라서** 읽기 쉬운 형태로 뽑아내는 것이다. 시뮬레이션을 다시 실행하는 것이 아니다.

따라서 "추가 신호 재추출"이란:
- ✗ 시뮬레이션을 다시 돌려서 새 신호를 dump하는 것이 **아님**
- ✓ 이미 SHM에 있는 다른 신호를 CSV로 **추가 추출**하는 것 (simvisdbutil 1회 재호출, 수 초)

**시뮬레이션 재실행이 필요한 경우는 단 하나**: SHM dump scope에 해당 신호가 아예 포함되지 않은 경우 (§3의 `prepare_dump_scope`로 사전 방지).

### 현재 문제

simvisdbutil 연동이 없어 매번 수동으로 CLI 명령을 조합해야 한다. CSV 추출 결과도 캐시되지 않아 동일 분석을 반복한다.

### 수정 방안

**simvisdbutil wrapper tool 구현:**

```python
@mcp.tool()
async def extract_waveform_csv(
    shm_path: str,              # SHM 파일 경로
    signals: list[str],         # 추출할 신호 목록
    start_ns: int = 0,          # 시작 시각
    end_ns: int = 0,            # 종료 시각 (0 = 전체)
    output_path: str = "",      # 출력 CSV 경로 (빈칸이면 /tmp/auto)
    missing_ok: bool = True,    # 신호 미존재 시 무시
) -> str:
```

내부 구현 (SSH 경유):
```bash
simvisdbutil <shm_path> \
    -csv \
    -output <output_path> \
    -overwrite \
    -range <start_ns>:<end_ns>ns \
    -missing \           # (missing_ok=True일 때)
    -sig <signal_1> \
    -sig <signal_2> \
    ...
```

**bisect_signal_dump 내부 흐름:**

```
1. CSV 추출: simvisdbutil로 전체 범위 (start~end) 1회 추출
    ↓
2. 메모리 파싱: CSV를 Python list/dict로 로드 (time, signal_values)
    ↓
3. In-memory 검색: 조건 매칭 시점을 binary search로 특정
    ↓
4. 결과 반환: 첫 매칭 시각 + 전후 N행
    ↓
5. (필요 시만) 추가 신호 재추출: 같은 범위에서 context_signals 추가
```

**"전후 N행" 설명**: 매칭 행만 보면 "값이 바뀌었다"만 알 수 있지만, 직전/직후 행을 함께 보면 **어떤 FSM 전이에서 값이 바뀌었는지** 즉시 파악할 수 있다.

```
예: bisect 조건 "r_regAddr == 0x10", context_lines=2

  8318040ns | regAddr=0x21 | loopState=2(CHK_ADR)   ← 전: 아직 0x21
  8318143ns | regAddr=0x21 | loopState=3(INC_ADR)   ← 전: INC_ADR 진입
★ 8318245ns | regAddr=0x10 | loopState=5(DATA_READY) ← 매칭! 0x10으로 변경
  8318345ns | regAddr=0x10 | loopState=6(SCL_WT_HI)  ← 후: 변경 유지
  8318443ns | regAddr=0x10 | loopState=6(SCL_WT_HI)  ← 후: 변경 유지

→ CHK_ADR(2)→INC_ADR(3) 전이에서 regAddr 변경됨을 즉시 확인
```

**핵심 원칙: CSV는 1회 추출 → 메모리에서 반복 검색. simvisdbutil 재호출 최소화.**

재추출이 필요한 경우는 **다른 신호를 CSV로 추가 추출**할 때뿐 (SHM에는 이미 있지만 1차 CSV에 포함하지 않았던 신호):
- 1차 CSV 신호로 이상 시점 특정 → 그 시점의 다른 신호가 필요 → SHM에서 같은 범위로 추가 신호만 CSV 재추출 (수 초)
- 시간 범위를 좁혀서 재추출하는 것은 불필요 (이미 메모리에 전체 데이터가 있으므로)
- 시뮬레이션 재실행은 불필요 (SHM에 신호가 이미 기록되어 있으므로)

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 5-1 | `extract_waveform_csv` tool 신규 구현 | server.py |
| 5-2 | simvisdbutil CLI 래핑 (ssh_run 경유) | server.py 또는 별도 모듈 |
| 5-3 | CSV 파싱 + in-memory 캐시 (pandas 미사용, 순수 Python) | 별도 모듈 (csv_cache.py) |
| 5-4 | bisect_signal_dump: 1회 추출 → in-memory binary search | server.py |
| 5-5 | CSV 캐시 재사용: 같은 SHM+범위+신호면 재추출 skip | csv_cache.py |

---

## 6. Batch Mode Simulation Tool

### 현재 문제

시뮬레이션을 batch mode로 실행하려면 매번 ssh-mcp로 수동 명령을 조합해야 한다:
```
echo 'source ~/.cshrc; cd ~/path/ncsim; ./run_sim -test TEST_NAME --' | tcsh
```

이것은 MCP tool이 아니라 AI agent가 직접 조합하는 것이므로, 실행 경로·환경·dump rename 등을 매번 다시 파악해야 한다.

### 수정 방안

**`sim_batch_run` tool 구현:**

```python
@mcp.tool()
async def sim_batch_run(
    test_name: str,                 # 테스트 이름 (예: "VENEZIA_TOP015_i2c_8bit_offset_test")
    sim_dir: str = "",              # 시뮬레이션 디렉토리 (빈칸이면 자동 탐지)
    run_script: str = "",           # 실행 스크립트 (빈칸이면 §7 규칙으로 탐지/생성)
    dump_signals: list[str] = [],   # 추가 dump 신호 (probe_add 역할 통합)
    from_checkpoint: str = "",      # checkpoint 이름: 지정 시 time 0 대신 restore에서 시작
    shm_path: str = "",             # SHM 경로 지정 (from_checkpoint 사용 시 기본: {sim_dir}/dump/{test_name}_extra.shm)
    run_duration: str = "",         # 실행 시간 지정 (from_checkpoint 사용 시 권장)
    rename_dump: bool = False,      # 완료 후 SHM mv rename. 기본 False(방법 6-A TCL로 처리). True=방법 6-B fallback
    timeout: int = 600,             # 타임아웃 (초)
) -> str:
    """Run a simulation in batch mode (no MCP bridge).

    Executes compile + elaborate + simulate using the existing run_sim script.
    After completion, optionally renames the SHM dump file for archival.
    Returns: log summary (PASS/FAIL lines, error count, dump path).
    """
```

**내부 흐름:**

```
1. sim_dir 탐지: 기존 run_sim 스크립트 위치에서 sim 디렉토리 결정
2. §7 규칙: run_script 존재 확인 → 없으면 생성
3. dump_signals 있으면 → §3-B prepare_dump_scope로 input tcl 확장
4. from_checkpoint 지정 시:
   a. checkpoint 유효성 확인 (compile_hash 일치 여부)
   b. input tcl 앞에 "restart <name> -path <checkpoint_dir>" 삽입
   c. run_duration 지정 시 "run <duration>" 사용 (전체 run 대신)
   d. shm_path를 {sim_dir}/dump/{test_name}_extra.shm으로 자동 결정 (미지정 시)
   e. compile+elaborate 생략 — restore 후 바로 시뮬레이션
5. SHM dump overwrite 방지 (아래 6-A/6-B 중 택 1)
6. SSH 경유 batch 실행:
   echo 'source ~/.cshrc; cd <sim_dir>; ./<run_script> -test <test_name> --' | tcsh
7. 완료 대기 (ssh_bg_run + poll)
8. 로그 파싱: PASS/FAIL/Errors/COMPLETE 추출
9. 결과 반환 (dump 경로 포함)
```

### SHM Dump Overwrite 방지

**현재 문제**: `setup_rtl.tcl`의 SHM 파일명이 `ci_top.shm`으로 고정:
```tcl
# scripts/setup_rtl.tcl (현재)
database -open ../dump/ci_top.shm -shm
probe -create top -unpacked 100 -database ../dump/ci_top.shm -depth all -all -memories -dynamic
run 10000ms
```

다중 테스트 순차 실행 시 매번 같은 파일이 덮어써져서 마지막 테스트의 dump만 남는다.

**방법 6-A (권장): Input Tcl에서 TEST_NAME으로 SHM 파일명 지정**

`run_sim`이 이미 `setenv TEST_NAME $2`로 환경변수를 설정하므로, Tcl에서 `$env(TEST_NAME)`으로 접근 가능:

```tcl
# scripts/setup_rtl.tcl (수정안)
set test_name $env(TEST_NAME)
set shm_path "../dump/ci_top_${test_name}.shm"
database -open $shm_path -shm
probe -create top -unpacked 100 -database $shm_path -depth all -all -memories -dynamic
run 10000ms
```

- 장점: SHM이 처음부터 테스트명으로 생성됨, rename 불필요, 중간 실패해도 dump 보존
- 단점: 기존 setup_rtl.tcl 수정 필요 (§7 script discovery에서 처리)

**방법 6-B: 시뮬레이션 완료 후 SHM rename**

```bash
# 시뮬레이션 완료 후
if [ -d dump/ci_top.shm ]; then
    mv dump/ci_top.shm dump/ci_top_${TEST_NAME}.shm
fi
```

- 장점: setup_rtl.tcl 수정 불필요
- 단점: 시뮬레이션 중간 실패 시 rename 미실행, 다음 테스트가 이전 dump 덮어씀

**sim_batch_run 구현 시**: 방법 6-A 우선 시도 (setup_rtl.tcl에 `$env(TEST_NAME)` 패턴 확인), 없으면 방법 6-B fallback.

**sim_batch_regression 구현 시**: 방법 6-A가 적용되지 않은 환경이면 각 테스트 완료 후 반드시 rename 수행.

**Batch Regression Tool (다중 테스트):**

```python
@mcp.tool()
async def sim_batch_regression(
    test_names: list[str],          # 테스트 목록
    sim_dir: str = "",
    run_script: str = "",
    rename_dump: bool = False,
    dump_signals: list[str] = [],   # §3-B: 추가 dump 신호 — 1회 prepare_dump_scope 후 전 테스트 공유
    from_checkpoint: str = "",      # [A'] restore 실행 시만 지정: 지정 checkpoint에서 시작, L1/L2 생성 생략
                                    # 미지정(일반 실행): 각 테스트가 time 0부터 실행 → L1+L2 자동 저장
) -> str:
    """Run multiple simulations sequentially in batch mode.

    For each test: run → rename dump → save log → next test.
    일반 실행(from_checkpoint 미지정): L1 없으면 첫 테스트에서 생성, 각 테스트별 L2도 자동 저장.
    from_checkpoint 지정([A'] restore): 지정 checkpoint에서 시작 → L1/L2 생성 생략.
    Returns: regression summary table.

    dump_signals: If provided, calls prepare_dump_scope once before the first
    test to extend the input tcl with additional probe signals. The extended
    tcl is reused for all tests in the regression (same signal list).
    """
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 6-1 | `sim_batch_run` tool 구현 | server.py |
| 6-2 | `sim_batch_regression` tool 구현 | server.py |
| 6-3 | SSH 실행 래핑: batch/regression은 screen, 그 외는 ssh_bg_run | server.py 또는 별도 모듈 |
| 6-4 | 로그 파싱 유틸리티 (PASS/FAIL/Errors 추출) | 별도 모듈 |
| 6-5 | SHM dump overwrite 방지: setup_rtl.tcl 내 `$env(TEST_NAME)` 패턴 적용 (방법 6-A) | templates/ + server.py |
| 6-6 | SHM rename fallback (방법 6-B): 기존 tcl 미수정 환경 대응 | server.py |
| 6-7 | timeout + 프로세스 상태 모니터링 | server.py |
| 6-8 | screen 세션 관리: 생성/재사용/종료/로그 읽기 유틸리티 | server.py 또는 ssh_screen.py |

### SSH 실행 전략: screen 하이브리드

**현재 방식의 pain point:**

| 문제 | 빈도 | 영향 |
|------|:----:|:----:|
| `echo 'source ~/.cshrc; ...' \| tcsh` 매번 래핑 | 매 명령 | 코드 복잡도 |
| EDA 환경(PATH, LICENSE) 매번 재설정 | 매 명령 | 수행 시간 |
| SSH 끊기면 장시간 시뮬레이션 종료 위험 | 가끔 | **데이터 소실** |
| 작업 디렉토리/변수 상태 공유 안 됨 | 매번 | 반복 작업 |

**하이브리드 전략: 단기 명령은 ssh_run, 장기 시뮬레이션은 screen**

```
┌──────────────────────────────────────────────────────┐
│ 명령 종류              │ 실행 방법          │ 이유    │
├────────────────────────┼────────────────────┼─────────┤
│ 파일 읽기/쓰기, grep   │ ssh_run (현재 유지)│ 단순/빠름│
│ 컴파일 (xmvlog/xmelab) │ ssh_run (현재 유지)│ 수 분 이내│
│ Batch 시뮬레이션       │ ★ screen 세션     │ gate sim 수 주 │
│ regression (다중 테스트)│ ★ screen 세션     │ 수십 분~수 일 │
│ simvisdbutil CSV 추출  │ ssh_run (현재 유지)│ 수 초    │
│ Bridge mode (xmsim)    │ ssh_bg_run (현재)  │ TCP bridge 별도│
│ SimVision GUI          │ VNC (§8)          │ VNC 디스플레이 │
└──────────────────────────────────────────────────────┘
```

screen은 **Batch 시뮬레이션/regression 전용**. gate-level sim처럼 수 주 걸리는 경우 SSH 끊김 보호가 필수.
Bridge mode는 TCP bridge, SimVision GUI는 VNC로 처리.

**screen 세션 관리 흐름:**

```
1. sim_batch_run 호출 시:
   ↓
2. screen 세션 존재 확인:
   ssh_run("screen -ls mcp_sim 2>/dev/null | grep mcp_sim")
   ↓
3-a. 없으면 → 생성 (tcsh login shell + EDA 환경 1회 설정):
   ssh_run("screen -dmS mcp_sim tcsh -l")
   ssh_run("screen -S mcp_sim -X stuff 'cd <sim_dir>\n'")
   ↓
3-b. 있으면 → 재사용 (환경 이미 설정됨)
   ↓
4. 명령 전송:
   ssh_run("screen -S mcp_sim -X stuff './run_sim -test <test_name> --\n'")
   ↓
5. 완료 대기 (screen log 또는 프로세스 확인):
   ssh_run("screen -S mcp_sim -X hardcopy /tmp/screen_out.txt")
   또는
   ssh_run("tail -5 /tmp/screen_mcp_sim.log")  # screen -L 로그
   ↓
6. 결과 파싱 → 반환
```

**screen 세션의 장점:**

| 항목 | ssh_bg_run (현재) | screen 세션 |
|------|:-----------------:|:-----------:|
| 환경 설정 | 매번 `source ~/.cshrc` | **1회** (세션 생성 시) |
| 작업 디렉토리 | 매번 cd | **유지** |
| SSH 끊김 보호 | nohup (부분적) | **완전** (screen detach) |
| 출력 접근 | temp file → 270K+ 문자 전체 | screen log 또는 `hardcopy` (마지막 N줄) |
| 다중 테스트 | 매번 새 bg job | **같은 세션에서 순차 실행** |
| xmsim bridge | 별도 ssh_bg_run | TCP bridge 사용 |

**sim_batch_run 내부 구현 (screen 적용):**

```python
async def sim_batch_run(self, test_name, sim_dir, ...):
    session = "mcp_sim"

    # 1. screen 세션 확인/생성
    exists = await ssh_run(f"screen -ls {session} 2>/dev/null | grep -q {session} && echo YES || echo NO")
    if "NO" in exists:
        await ssh_run(f"screen -dmS {session} -L -Logfile /tmp/screen_{session}.log tcsh -l")
        await ssh_run(f"screen -S {session} -X stuff 'cd {sim_dir}\\n'")

    # 2. 시뮬레이션 실행 (tcsh 래핑 불필요 — 이미 tcsh 세션)
    await ssh_run(f"screen -S {session} -X stuff './run_sim -test {test_name} --\\n'")

    # 3. 완료 대기 (로그 polling)
    while True:
        log = await ssh_run(f"tail -3 /tmp/screen_{session}.log")
        if "COMPLETE" in log or "$finish" in log:
            break
        await asyncio.sleep(10)

    # 4. 결과 파싱
    result = await ssh_run(f"grep -E 'PASS|FAIL|Errors:|COMPLETE' /tmp/screen_{session}.log | tail -20")
    return result
```

**sim_batch_regression에서의 효과:**

```python
# 현재: 매 테스트마다 새 SSH + tcsh 래핑
for test in tests:
    ssh_bg_run(f"echo 'source ~/.cshrc; cd ...; ./run_sim -test {test} --' | tcsh")

# screen: 같은 세션에서 순차 실행 (환경 1회, cd 1회)
for test in tests:
    ssh_run(f"screen -S mcp_sim -X stuff './run_sim -test {test} --\\n'")
    # 완료 대기...

    # SHM rename 처리:
    # - 방법 6-A (기본): input TCL에서 $env(TEST_NAME)으로 SHM 파일명 지정
    #   → SHM이 처음부터 ci_top_{test}.shm으로 생성됨, mv 불필요
    # - 방법 6-B (rename_dump=True): 기존 TCL 수정 불가 등 특수 상황
    #   → ci_top.shm → ci_top_{test}.shm mv
    if rename_dump:  # 방법 6-B fallback
        ssh_run(f"screen -S mcp_sim -X stuff 'mv dump/ci_top.shm dump/ci_top_{test}.shm\\n'")
```

**`rename_dump` 파라미터 정책**:
- **기본값 `False`**: 방법 6-A (input TCL `$env(TEST_NAME)`) 적용 시 mv 불필요
- **`rename_dump=True`**: 기존 TCL 수정이 불가하거나 외부 스크립트가 SHM 파일명을 고정으로 생성하는 특수 환경

---

## 7. Script 재사용 정책 (Batch/Bridge 공통)

### 현재 문제

batch mode(`run_sim`)와 bridge mode(`run_sim_mcp`) 실행 시, AI agent가 기존 스크립트 존재 여부를 모르고 매번 새로 생성하거나 잘못된 경로를 사용한다.

### 수정 방안: 2-tier Sim Runner Discovery

**원칙**: 실행 메커니즘(shell/make/xrun/python)에 무관하게 동작. 명시적 config 우선, 자동 탐지 fallback.

#### Tier 1: `.mcp_sim_config.json` (명시적, 최우선)

`sim_dir/.mcp_sim_config.json` 존재 시 바로 사용. 사용자가 1회 정의하거나 자동 탐지 결과를 확인 후 저장.

```json
{
  "runner":           "make",
  "batch_cmd":        "make sim TEST={test_name}",
  "bridge_cmd":       "make sim_mcp TEST={test_name}",
  "compile_cmd":      "make compile",
  "shm_pattern":      "dump/ci_top_{test_name}.shm",
  "log_pattern":      "logs/ncsim_{test_name}.log"
}
```

| runner 타입 | batch_cmd 예시 |
|------------|----------------|
| `shell`  | `./run_sim -test {test_name}` |
| `make`   | `make sim TEST={test_name}` |
| `xrun`   | `xrun -f sim.f +define+TEST={test_name} -run` |
| `python` | `python run_sim.py --test {test_name}` |

#### Tier 2: 자동 탐지 (fallback)

`.mcp_sim_config.json` 없을 때 `sim_dir` 내 파일 패턴으로 runner 타입 추론:

```
탐지 우선순위:
1. Makefile + sim/test/run target 포함 여부
   → grep 'sim:\|test:\|run:' Makefile → make 기반
2. 실행 권한 있는 shell script (run_sim*, run_test*, *.sh)
   → 이름 패턴 + shebang(#!/bin/csh, #!/bin/bash 등) 확인
3. *.f filelist + xrun/irun 실행 가능 여부
   → direct EDA tool 기반
4. run_sim.py / sim.py
   → Python runner 기반
5. 모두 해당 없음 → 사용자에게 직접 입력 요청
```

추론 결과는 사용자에게 제안 후 확인:

```
탐지 결과: "Makefile에서 'sim:' target 발견"
제안: runner="make", batch_cmd="make sim TEST={test_name}"
확인 후 .mcp_sim_config.json 자동 저장 (이후 재탐지 불필요)
```

#### Python 구현

```python
async def _resolve_sim_runner(sim_dir: str) -> dict:
    """Resolve sim runner config. Config file first, auto-detect fallback."""

    # Tier 1: explicit config
    cfg_path = f"{sim_dir}/.mcp_sim_config.json"
    result = await ssh_run(f"test -f {cfg_path} && cat {cfg_path} || echo MISSING")
    if "MISSING" not in result:
        return json.loads(result)   # trust explicit config

    # Tier 2: auto-detect
    detected = await _auto_detect_runner(sim_dir)

    if detected["confidence"] == "high":
        # 단일 후보, 사용자 확인 후 저장
        await _save_sim_config(sim_dir, detected)
        return detected
    else:
        # 복수 후보 또는 불명확 → 사용자 선택 요청
        return await _ask_user_runner(sim_dir, detected["candidates"])

async def _auto_detect_runner(sim_dir: str) -> dict:
    candidates = []

    # 1. Makefile + sim/test/run target 포함 여부
    r = await ssh_run(f"grep -lE 'sim:|test:|run:' {sim_dir}/Makefile 2>/dev/null")
    if r.strip():
        targets = await ssh_run(f"grep -oE '^(sim|test|run|simulate|regression)[^:]*:' {sim_dir}/Makefile | tr -d ':'")
        best_target = targets.strip().splitlines()[0] if targets.strip() else "sim"
        candidates.append({"runner": "make", "batch_cmd": f"make {best_target} TEST={{test_name}}", "score": 3})

    # 2. 실행 권한 있는 shell script + shebang 확인
    r = await ssh_run(
        f"find {sim_dir} -maxdepth 1 -perm /111 \\( -name 'run_sim*' -o -name 'run_test*' -o -name '*.sh' \\) 2>/dev/null"
    )
    for script in r.strip().splitlines():
        shebang = await ssh_run(f"head -1 {script} 2>/dev/null")
        if shebang.strip().startswith("#!"):          # #!/bin/csh, #!/bin/bash 등
            candidates.append({"runner": "shell", "batch_cmd": f"{script} -test {{test_name}}", "score": 2})

    # 3. *.f filelist + xrun/irun 실행 가능 여부
    r = await ssh_run(f"ls {sim_dir}/*.f 2>/dev/null | head -1")
    if r.strip():
        tool = await ssh_run("which xrun 2>/dev/null || which irun 2>/dev/null | head -1")
        if tool.strip():
            tool_name = tool.strip().split("/")[-1]   # xrun 또는 irun
            candidates.append({"runner": "xrun", "batch_cmd": f"{tool_name} -f {r.strip()} +define+TEST={{test_name}} -run", "score": 1})

    # 4. run_sim.py / sim.py
    r = await ssh_run(f"ls {sim_dir}/run_sim.py {sim_dir}/sim.py 2>/dev/null | head -1")
    if r.strip():
        py = await ssh_run("which python3 2>/dev/null || which python 2>/dev/null | head -1")
        py_cmd = py.strip().split("/")[-1] if py.strip() else "python3"
        candidates.append({"runner": "python", "batch_cmd": f"{py_cmd} {r.strip()} --test {{test_name}}", "score": 1})

    # 5. 모두 해당 없음
    if not candidates:
        return {"confidence": "none", "candidates": []}

    best = max(candidates, key=lambda x: x["score"])
    top_score = best["score"]
    top_candidates = [c for c in candidates if c["score"] == top_score]
    confidence = "high" if len(top_candidates) == 1 else "ambiguous"
    return {**best, "confidence": confidence, "candidates": candidates}


async def _ask_user_runner(sim_dir: str, candidates: list) -> dict:
    """복수 후보(ambiguous) 또는 탐지 실패(none) 시 사용자 선택/입력 요청."""
    if not candidates:
        # 5번 케이스: 탐지 완전 실패 → 직접 입력 요청
        user_cmd = await ask_user(
            f"Could not auto-detect simulation runner in:\n  {sim_dir}\n\n"
            "Please enter the run command (use {test_name} as placeholder):\n"
            "  Example: ./run_sim -test {test_name}\n"
            "  Example: make sim TEST={test_name}\n"
            "  Example: xrun -f sim.f +define+TEST={test_name} -run"
        )
        cfg = {"runner": "custom", "batch_cmd": user_cmd, "confidence": "user_defined"}
        await _save_sim_config(sim_dir, cfg)
        return cfg

    # ambiguous: 후보 목록 + 직접 입력 옵션 제시
    options = [f"{i+1}. [{c['runner']}] {c['batch_cmd']}" for i, c in enumerate(candidates)]
    options.append(f"{len(candidates)+1}. 직접 입력")
    choice = await ask_user(
        f"Multiple runners detected in {sim_dir}. Select one:\n" + "\n".join(options)
    )
    idx = int(choice.strip()) - 1
    if idx == len(candidates):           # 직접 입력 선택
        return await _ask_user_runner(sim_dir, [])
    cfg = {**candidates[idx], "confidence": "user_selected"}
    await _save_sim_config(sim_dir, cfg)
    return cfg
```

**sim_dir 자동 탐지 및 TB 환경 분석:**

프로젝트에 따라 검증 환경 디렉토리 이름(`sim/`, `test/`, `tb/`, `verif/` 등)과 구조(단일 환경 또는 `ncsim/`, `uvm/`, `sv/` 등 하위 분리)가 다를 수 있다. 첫 실행 시 이름 패턴 + 내용 기반으로 모든 TB 환경을 탐지하여 캐시에 저장하고, 이후에는 캐시를 기준으로 `sim_dir`를 특정한다. 탐지 실패 시 사용자에게 simulation root 폴더를 직접 입력받아 처리한다.

**캐시 구조** (`~/.xcelium_mcp/sim_registry.json`):

```json
{
  "sim_envs": [
    {
      "sim_dir": "~/git.clone/venezia-t0/design/top/sim/ncsim/",
      "tb_type": "ncsim_legacy",
      "runner": "shell",
      "batch_cmd": "./run_sim -test {test_name}",
      "description": "Legacy ncsim — i2c/pcm direct task API"
    },
    {
      "sim_dir": "~/git.clone/venezia-t0/design/top/sim/uvm/",
      "tb_type": "uvm",
      "runner": "make",
      "batch_cmd": "make sim UVM_TEST={test_name}",
      "description": "UVM testbench"
    }
  ],
  "default_env": "ncsim_legacy"
}
```

**TB 타입 판별 기준:**

| TB 타입 | 판별 지표 |
|---------|----------|
| `ncsim_legacy` | `run_sim` 스크립트, `*.f` filelist, `tb_top.v` (non-UVM), task 기반 stimulus |
| `uvm` | `uvm/` 디렉토리, `uvm_pkg`, `uvm_test`, `uvm_component`, `UVM_TEST` plusarg |
| `sv_directed` | `*.sv` testbench, `interface`, `program` 블록, non-UVM |
| `mixed` | uvm + legacy 공존 |

**탐지 흐름:**

```
첫 sim_dir 결정 시:
    │
    ├─ 1. sim_registry.json 있음 → default_env의 sim_dir 사용 (캐시 적중)
    │
    └─ 2. 없음 → 자동 탐지:
           a. project_root 결정: git rev-parse --show-toplevel → 실패 시 ~
           b. 이름 패턴으로 후보 탐색 (maxdepth 3):
              sim*, test*, tb*, verif*, bench*, dv
           c. 상위-하위 중복 제거 (짧은 경로 우선)
           d. 각 후보 직속 하위(maxdepth 1)에서 _analyze_tb_type() 실행
              → valid(uvm/ncsim_legacy/sv_directed)만 등록
              → 하위에 없으면 후보 자체 판별
           e. 탐지 성공 → 복수 환경이면 사용자 목록 제시 + default 선택
           f. 탐지 실패 → 사용자에게 simulation root 폴더 직접 입력 요청
              → 입력 경로에서 _analyze_tb_type() + _auto_detect_runner() 실행
           g. 결과 → sim_registry.json 저장 (이후 재탐지 불필요)
```

```python
async def _discover_sim_dir(hint: str = "") -> dict:
    """Discover sim_dir and TB type. Cache result for future use."""

    # 1. cached registry
    registry = await _load_sim_registry()
    if registry:
        return registry["sim_envs"][registry["default_env"]]

    # 2. determine project root (git root → fallback ~)
    if hint:
        project_root = hint
    else:
        r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
        project_root = r.strip()

    # 3. find candidates: name pattern at depth 1-3
    patterns = "-name 'sim*' -o -name 'test*' -o -name 'tb*' -o -name 'verif*' -o -name 'bench*' -o -name 'dv'"
    r = await ssh_run(
        f"find {project_root} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) 2>/dev/null | sort"
    )
    raw = r.strip().splitlines()

    # 4. deduplicate: remove sub-paths of already-found shorter paths
    raw = sorted(set(raw), key=len)
    deduped = []
    for path in raw:
        if not any(path.startswith(p + "/") for p in deduped):
            deduped.append(path)

    # 5. validate by content: _analyze_tb_type wins, not directory name
    envs = []
    for sim_root in deduped:
        r = await ssh_run(f"find {sim_root} -maxdepth 1 -mindepth 1 -type d 2>/dev/null")
        subdirs = r.strip().splitlines()
        found_in_sub = False
        for sub in subdirs:
            tb_type = await _analyze_tb_type(sub)
            if tb_type != "unknown":
                runner_cfg = await _auto_detect_runner(sub)
                envs.append({"sim_dir": sub, "tb_type": tb_type, **runner_cfg})
                found_in_sub = True
        if not found_in_sub:
            tb_type = await _analyze_tb_type(sim_root)
            if tb_type != "unknown":
                runner_cfg = await _auto_detect_runner(sim_root)
                envs.append({"sim_dir": sim_root, "tb_type": tb_type, **runner_cfg})

    # 6. auto-detect failed → ask user for simulation root folder
    if not envs:
        sim_root = await ask_user(
            "Could not auto-detect simulation directory.\n"
            "Please enter the simulation root folder path:\n"
            "  (e.g., ~/git.clone/myproject/sim\n"
            "         ~/git.clone/myproject/test/ncsim)"
        )
        tb_type = await _analyze_tb_type(sim_root)
        runner_cfg = await _auto_detect_runner(sim_root)
        envs = [{"sim_dir": sim_root, "tb_type": tb_type, **runner_cfg}]

    # 7. present to user, get default selection → save registry
    default = await _ask_user_select_env(envs)
    await _save_sim_registry(envs, default)
    return envs[default]

async def _analyze_tb_type(sim_dir: str) -> str:
    """Heuristic TB type detection from sim_dir contents."""
    # UVM 마커 확인 (-rl: recursive + list filename only, -l 중복 제거)
    r_uvm = await ssh_run(
        f"grep -rl 'uvm_component\\|uvm_test\\|UVM_TEST' {sim_dir} --include='*.sv' --include='*.svh' 2>/dev/null | head -1"
    )
    has_uvm = bool(r_uvm.strip())

    # ncsim_legacy 마커 확인 (run_sim 스크립트 또는 *.f filelist)
    r_legacy = await ssh_run(f"ls {sim_dir}/run_sim {sim_dir}/*.f 2>/dev/null")
    has_legacy = bool(r_legacy.strip())

    # mixed: UVM + legacy 공존
    if has_uvm and has_legacy:
        return "mixed"
    if has_uvm:
        return "uvm"
    if has_legacy:
        return "ncsim_legacy"

    # sv_directed: non-UVM SystemVerilog (interface/program 블록)
    r = await ssh_run(
        f"grep -rl 'interface\\|program ' {sim_dir} --include='*.sv' 2>/dev/null | head -1"
    )
    if r.strip():
        return "sv_directed"

    return "unknown"
```

### Bridge Mode 연동

```
connect_simulator(test_name="TOP015")
    │
    ├─ 1. sim_dir 탐지
    ├─ 2. _resolve_sim_runner(sim_dir) → bridge_cmd 결정
    ├─ 3. SSH: {bridge_cmd} 실행 (background)
    ├─ 4. MCP bridge 연결 대기
    └─ 5. 연결 완료 반환
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 7-1 | `_resolve_sim_runner()`: Tier 1 config 우선, Tier 2 자동 탐지 fallback | server.py 또는 utils.py |
| 7-2 | `_auto_detect_runner()`: Makefile/shell/xrun/python 패턴 탐지 + score-based ranking | server.py |
| 7-3 | `.mcp_sim_config.json` 스키마 정의 + 자동 저장 (단일 sim_dir 전용) | server.py |
| 7-4 | `sim_batch_run` + `connect_simulator`에 `_resolve_sim_runner` 통합 | server.py |
| 7-5 | sim_dir 파라미터 비어 있을 때 `_discover_sim_dir()` 호출로 연결 | server.py |
| 7-6 | 불명확 시 사용자 선택 요청 + 결과 저장 (`_ask_user_runner`, `_ask_user_select_env`) | server.py |
| 7-7 | `sim_registry.json` 스키마 정의 + `_load_sim_registry()` / `_save_sim_registry()` 구현 (`~/.xcelium_mcp/sim_registry.json`) | server.py |
| 7-8 | `_analyze_tb_type(sim_dir)`: uvm/ncsim_legacy/sv_directed/**mixed**/unknown 탐지. UVM 마커(grep `uvm_component`/`UVM_TEST`) + legacy 마커(`run_sim`/`*.f`) 각각 확인 → 둘 다 있으면 `mixed` → 단독이면 해당 타입 → sv_directed(interface/program) → unknown | server.py |
| 7-9 | `_discover_sim_dir(hint)`: (1) git root 결정(실패 시 ~), (2) 이름 패턴(`sim*`/`test*`/`tb*`/`verif*`/`bench*`/`dv`) maxdepth 3 탐색, (3) 상위-하위 중복 제거, (4) 직속 하위 maxdepth 1에서 `_analyze_tb_type` 내용 기반 검증(unknown 제외), (5) 탐지 실패 시 **사용자에게 simulation root 폴더 직접 입력 요청**, (6) 복수 환경 목록 제시 + `default_env` 선택 → `sim_registry.json` 저장 | server.py |

---

## 8. 사용자 디버깅 지원 Tool (Human-in-the-Loop)

### 목적

AI가 CSV 분석으로 관련 신호와 이상 시점을 특정한 뒤, **사용자가 SimVision에서 시각적으로 확인**할 수 있도록 환경을 자동 세팅해주는 tool. AI가 모든 것을 자동 해결하지 않고, 사람의 판단이 필요한 지점에서 최적의 디버깅 환경을 준비한다.

### 8-A: `open_debug_view` — VNC + SimVision + bridge 자동 세팅

AI가 VNC 디스플레이에서 SimVision을 실행하고 bridge를 주입한 뒤, 분석 결과에 따라 신호/줌/커서를 세팅한다. 사용자는 VNC viewer로 접속하여 SimVision GUI를 실시간 확인.

**아키텍처:**

```
┌─────────────┐                           ┌──────────────────────────┐
│ AI (Claude)  │──── TCP 9876 ──────────→ │ SimVision (DISPLAY=:1)   │
│              │                           │  + mcp_bridge.tcl        │
└─────────────┘                           │  (cloud0 VNC desktop)    │
                                           └──────────────────────────┘
┌─────────────┐                                       ↑
│ 사용자       │──── VNC viewer (localhost:5901) ─────┘
│ (MobaXterm)  │     (SimVision GUI 실시간 확인)
└─────────────┘
```

AI는 TCP bridge로 제어하고, 사용자는 VNC로 GUI를 본다. 같은 SimVision 프로세스를 **두 경로로 접근**한다.

```python
@mcp.tool()
async def open_debug_view(
    shm_path: str,                  # SHM 파일 경로
    signals: list[str],             # 추가할 신호 목록 (AI가 분석에서 식별한 것)
    center_time_ns: int,            # 줌 중심 시각 (버그 시점)
    zoom_range_ns: int = 10000,     # 줌 범위 (±)
    cursor_time_ns: int = 0,        # 커서 위치 (0이면 center_time_ns)
    markers: list[dict] = [],       # 마커 목록: [{"time_ns": T, "label": "bug here"}, ...]
    group_name: str = "AI_Debug",   # AI 추가 신호 전용 그룹 이름. 없으면 자동 생성.
                                    # 중복 확인은 전체 waveform(사용자 그룹 포함) 기준 — 어디에 있든 이미 있으면 skip
    context_note: str = "",         # AI 분석 요약 (SimVision 콘솔에 출력)
    display: str = ":1",            # DISPLAY 환경변수 (VNC :1)
) -> str:
    """Launch SimVision on VNC display with pre-configured debug view.

    Sets DISPLAY → launches SimVision (bridge auto-loaded via .simvisionrc)
    → connects via TCP → adds signals/zoom/cursor/markers.
    User connects via VNC viewer to see SimVision GUI.
    """
```

**DISPLAY 전략:**

SimVision GUI를 사용자가 보려면 DISPLAY가 필요하다. 접근 방법에 따라 전략이 다르다:

| 방법 | DISPLAY 설정 | 사용자 접근 | SSH 끊김 시 | 권장 |
|------|-------------|-----------|:-----------:|:----:|
| **VNC** (권장) | `:1` (vncserver) | VNC viewer (localhost:5901) | **GUI 유지** ✓ | ★ |
| X11 forwarding | `localhost:10.0` (SSH 종속) | MobaXterm 직접 | GUI 사망 ✗ | 임시 |
| Headless (GUI 없음) | 미설정 | generate_debug_tcl로 대체 | N/A | 항상 가능 |

**VNC 1회 설정 (cloud0):**
```bash
vncserver :1 -geometry 1920x1080 -depth 24
# + ~/.ssh/config에 LocalForward 5901 localhost:5901 추가
```

**내부 흐름:**

```
1. VNC 실행 중 확인:
   ssh_run("vncserver -list 2>/dev/null | grep :1")
   - 있으면 → DISPLAY=":1"
   - 없으면 → GUI 불가 → generate_debug_tcl fallback (§8-B)

2. SimVision 실행 (.simvisionrc가 bridge 자동 로드):
   ssh_run("DISPLAY=:1 simvision <shm_path> &")

3. Bridge ready 대기 (TCP 9876):
   ssh_run("for i in $(seq 1 15); do sleep 2; nc -z localhost 9876 && break; done")

4. TCP bridge 연결:
   connect_simulator(host="localhost", port=9876)

5. 신호 추가 + 줌 + 커서 + 마커:
   waveform_add_signals(signals=[...], group_name="AI_Debug")
   → 전체 waveform 신호 목록 조회 (사용자 그룹 내부 포함)
   → 어디에 있든 이미 있는 신호는 건너뜀 (중복 방지)
   → 없는 신호만 AI_Debug 그룹에 추가 (그룹 없으면 자동 생성)
   waveform_zoom(start=center-range, end=center+range)
   cursor_set(time=center_time_ns)

6. 분석 요약 출력 (SimVision 콘솔):
   execute("puts {=== AI Debug Context ===}")

7. 사용자에게 안내:
   "VNC viewer로 localhost:5901 접속하면 SimVision GUI를 볼 수 있습니다."
```

### 사용자가 이미 SimVision을 열고 있는 경우: `.simvisionrc` 자동 bridge

**검증 완료 (2026-03-27, cloud0 Xcelium 22.09):** `.simvisionrc`에 bridge source를 1행 추가하면, SimVision을 어떻게 열든 bridge가 자동 로드된다.

**1회 설정 (cloud0):**
```bash
echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc
```

**검증 결과:**

| 테스트 | 결과 |
|--------|:----:|
| `.simvisionrc` 자동 bridge 로드 | ✅ TCP 9876 자동 오픈 |
| `connect_simulator()` 접속 | ✅ ping=pong |
| `waveform_add_signals` (3개) | ✅ SimVision GUI에 즉시 표시 |
| `waveform_zoom` (8.2~8.5ms) | ✅ |
| `cursor_set` (8318143ns) | ✅ |

**`waveform_add_signals` 중복 건너뜀 요구사항:**

신호를 추가하기 전 waveform에 이미 있는 신호(그룹 내부 포함)를 조회하여 중복을 건너뜀:

```tcl
# mcp_bridge.tcl — do_waveform_add
proc ::mcp_bridge::do_waveform_add {channel args_str} {
    # 1. 현재 waveform 신호 목록 조회 (그룹 내부 포함)
    set existing_raw [waveform list -signals]
    set existing [split $existing_raw "\n"]

    # 2. 요청 신호 중 없는 것만 필터
    set to_add {}
    foreach sig $requested_signals {
        if {[lsearch -exact $existing $sig] < 0} {
            lappend to_add $sig
        }
    }

    # 3. 없는 신호만 AI 전용 그룹에 추가
    if {[llength $to_add] == 0} {
        ::mcp_bridge::send_ok $channel "all signals already present, skipped"
        return
    }

    # 4. AI 전용 그룹 없으면 자동 생성, 있으면 재사용
    set groups [waveform list -groups]
    if {[lsearch -exact $groups $group_name] < 0} {
        waveform group -add $group_name
    }
    waveform add -signals $to_add -into $group_name
    ::mcp_bridge::send_ok $channel "added [llength $to_add] to '$group_name', skipped [expr {[llength $requested_signals] - [llength $to_add]}] duplicates"
}
```

**`attach_to_simvision` 구현이 단순해졌다:**

```python
@mcp.tool()
async def attach_to_simvision(
    port: int = 9876,
) -> str:
    """Connect to a SimVision that has bridge auto-loaded via .simvisionrc.

    Simply attempts TCP connection. If .simvisionrc is configured,
    any running SimVision already has the bridge listening.
    """
```

**내부 흐름:**

```
1. TCP 9876 연결 시도 → 성공 → 완료 (.simvisionrc가 bridge 자동 로드)
2. 실패 → SimVision 미실행 또는 .simvisionrc 미설정
   → 사용자에게 안내:
     "SimVision이 실행 중이 아니거나 .simvisionrc가 설정되지 않았습니다."
     "echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc 후 SimVision을 재시작해주세요."
```

**외부에서 실행 중인 SimVision에 Tcl 주입은 불가 (검증됨):**
- `/proc/PID/fd/0` write → pty에 echo만 되고 Tcl 인터프리터에 전달 안 됨
- SimVision에 `-submittcl` 같은 외부 주입 옵션 없음
- **`.simvisionrc` 자동 로드가 유일하게 확실한 방법**

**사용 시나리오:**

```python
# AI가 CSV 분석으로 버그 원인을 좁힌 후:
await open_debug_view(
    shm_path="dump/ci_top_TOP015.shm",
    signals=[
        "top.hw...r_regAddr",
        "top.hw...r_streamRwState",
        "top.hw...r_startStopDetState",
        "top.hw...r_loopState",
        "top.hw...r_rxData",
    ],
    center_time_ns=8318143,          # CHK_ADR에서 NULL_DET인 시점
    zoom_range_ns=50000,             # ±50us
    markers=[
        {"time_ns": 8300000, "label": "offset byte start"},
        {"time_ns": 8318143, "label": "★ BUG: CHK_ADR + NULL_DET"},
        {"time_ns": 8418633, "label": "regAddr should be 0x10"},
    ],
    context_note="STREAM_REG offset capture fails because startStopDetState=NULL_DET at CHK_ADR. regAddr stays 0x21 instead of 0x10.",
)
# → 사용자의 SimVision에 자동으로 디버깅 환경이 구성됨
```

### 8-B: `generate_debug_tcl` — 오프라인 디버깅 Tcl 스크립트 생성

X11 접속이 어렵거나 SimVision을 나중에 열 경우, AI 분석 결과를 Tcl 스크립트로 저장하여 사용자가 직접 실행.

```python
@mcp.tool()
async def generate_debug_tcl(
    shm_path: str,
    signals: list[str],
    center_time_ns: int,
    zoom_range_ns: int = 10000,
    markers: list[dict] = [],
    context_note: str = "",
    output_path: str = "",          # Tcl 스크립트 저장 경로
) -> str:
    """Generate a SimVision Tcl script for offline debugging.

    User can later run: simvision -input <output_path> <shm_path>
    """
```

**생성되는 Tcl 스크립트 예시:**

```tcl
# === Auto-generated debug script ===
# Bug: STREAM_REG offset capture fails at 8318143ns
# Generated by xcelium-mcp AI analysis

# Open waveform
database -open ../dump/ci_top_TOP015.shm -shm

# Add debug signals in group
waveform add -signals {
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_regAddr
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_streamRwState
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_startStopDetState
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_loopState
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_rxData
}

# Zoom to bug region
waveform zoom -range 8268143:8368143ns

# Set cursor at bug time
cursor set -time 8318143ns

# Markers
cursor set -time 8300000ns -name "offset byte start"
cursor set -time 8318143ns -name "BUG: CHK_ADR + NULL_DET"
cursor set -time 8418633ns -name "regAddr should be 0x10"

# AI analysis context (console output)
puts "============================================"
puts "AI Debug Context:"
puts "  STREAM_REG offset capture fails because"
puts "  startStopDetState=NULL_DET at CHK_ADR."
puts "  regAddr stays 0x21 instead of 0x10."
puts "============================================"
```

**사용자 실행:**
```bash
simvision -input debug_TOP015_v18.tcl dump/ci_top_TOP015.shm
```

### 8-C: `export_debug_context` — AI 분석 결과를 사람이 읽을 수 있는 형태로 정리

CSV 분석 결과, 근본 원인 추정, 관련 코드 위치 등을 요약 문서로 생성.

```python
@mcp.tool()
async def export_debug_context(
    test_name: str,
    bug_description: str,           # 1줄 요약
    root_cause: str,                # 근본 원인 추정
    evidence: list[dict],           # CSV에서 발견한 증거: [{"time_ns": T, "signal": S, "value": V, "expected": E}, ...]
    related_code: list[dict],       # 관련 코드: [{"file": F, "line": L, "snippet": S}, ...]
    signals_to_check: list[str],    # 사용자가 확인할 신호 목록
    suggested_fix: str = "",        # 수정 제안 (있으면)
    output_path: str = "",
) -> str:
    """Export a human-readable debug context document.

    Summarizes AI findings for the user to review before making changes.
    """
```

**생성되는 문서 예시:**

```markdown
# Debug Context: TOP015 V-18 CONFIG_DUR read-back = 0x00

## Bug Summary
CONFIG_DUR(0x10) write 후 read-back이 0x00 반환. 6 errors.

## Root Cause (AI 추정)
CHK_ADR의 STREAM_REG offset 캡처가 START_DET 게이트 내부에만 있음.
Byte 1에서 clearStartStopDet 후 NULL_DET → STREAM_REG case 미진입 → regAddr 미설정.

## Evidence (CSV 분석)
| 시각 (ns) | 신호 | 실측값 | 기대값 | 의미 |
|-----------|------|--------|--------|------|
| 8318143 | startStopDetState | 0 (NULL_DET) | 1 (START_DET) | CHK_ADR 진입 시 START_DET이어야 함 |
| 8318245 | regAddr | 0x21 (불변) | 0x10 | rxData=0x10이 regAddr에 캡처되지 않음 |
| 8418731 | streamRwState | 3 (WRITE) | 3 (WRITE) | 전이 자체는 정상이나 regAddr이 잘못됨 |

## Related Code
- ext_i2cSerialInterface.v:289 — `if (... && startStopDetState == START_DET)` ← 이 조건이 문제
- ext_i2cSerialInterface.v:343 — `c_regAddr[7:0] = r_rxData[7:0]` ← 이 줄이 실행 안 됨

## Signals to Check in SimVision
1. r_regAddr — offset 캡처 시점 확인
2. r_startStopDetState — NULL_DET/START_DET 전이
3. r_loopState — CHK_ADR(2) → INC_ADR(3) 전이
4. r_rxData — 수신 데이터 0x10 확인

## Suggested Fix
CHK_ADR에 else-if 분기 추가: STREAM_REG + !START_DET에서도 regAddr 캡처.
```

### 8-D: `compare_waveforms` — 정상/이상 실행 비교 뷰

같은 테스트의 수정 전/후 또는 정상/이상 구간을 SimVision에서 나란히 비교.

```python
@mcp.tool()
async def compare_waveforms(
    shm_before: str,                # 수정 전 (또는 이상) SHM
    shm_after: str,                 # 수정 후 (또는 정상) SHM
    signals: list[str],             # 비교할 신호 목록
    time_range_ns: tuple = (0, 0),  # 비교 시간 범위
    output_mode: str = "simvision", # "simvision" (GUI) 또는 "csv_diff" (텍스트)
) -> str:
    """Compare two SHM dumps side by side.

    In simvision mode: opens both dumps and aligns signals for visual comparison.
    In csv_diff mode: extracts CSV from both and generates diff report.
    """
```

**csv_diff mode 출력 예시:**

```
=== Waveform Comparison: before_fix vs after_fix ===
Signal: r_regAddr
  Time 8318245ns: BEFORE=0x21 | AFTER=0x10  ← CHANGED (fix applied)
  Time 8418633ns: BEFORE=0x21 | AFTER=0x10  ← CHANGED

Signal: r_streamRwState
  Time 8318245ns: BEFORE=3    | AFTER=3     (same)

Result: 1 signal changed, 1 signal unchanged
```

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 8-1 | `open_debug_view`: VNC DISPLAY에 SimVision 실행 + 신호/줌/커서/마커 자동 세팅 (.simvisionrc로 bridge 자동) | server.py |
| 8-2 | `attach_to_simvision`: TCP 9876 연결 시도 (.simvisionrc 전제) + 미설정 시 안내 | server.py |
| 8-3 | `generate_debug_tcl`: 오프라인 디버깅 Tcl 스크립트 생성 | server.py |
| 8-4 | `export_debug_context`: AI 분석 결과 요약 문서 생성 | server.py |
| 8-5 | `compare_waveforms`: 정상/이상 SHM 비교 (SimVision GUI 또는 CSV diff) | server.py |
| 8-6 | VNC DISPLAY 확인 + fallback (VNC 없으면 generate_debug_tcl) | server.py |
| 8-7 | 마커 Tcl 생성 로직 (cursor set -name) | server.py |
| 8-8 | `waveform_add_signals` 중복 건너뜀 + AI 전용 그룹: (1) 전체 waveform 신호 조회(사용자 그룹 내부 포함) → 어디에 있든 이미 있으면 skip, (2) 없는 신호는 `group_name`(기본 `"AI_Debug"`) 그룹에 추가 — 그룹 없으면 자동 생성, 있으면 재사용. 사용자 그룹은 건드리지 않음 | mcp_bridge.tcl |

---

## 9. execute_tcl — 범용 Tcl 실행 Tool

### 문제 배경 (2026-03-27 세션에서 발견)

`restore → probe_add → run → dump → CSV` 아키텍처 검증 중, bridge 연결 상태에서 아래 Tcl 명령이 필요했다:

```tcl
database -open /tmp/test_no_dump.shm -shm -default
probe -create top...r_regAddr -database /tmp/test_no_dump.shm
```

이 명령들은 xcelium-mcp의 25개 tool 어디에도 없다. 불가피하게 다음 우회책을 사용했다:

```
disconnect_simulator   ← xcelium-mcp 연결 해제
nc -w5 localhost 9876  ← raw TCP로 Tcl 직접 전송 (bridge 단독 점유)
connect_simulator      ← 재연결
```

**근본 원인**: bridge는 `uplevel #0`으로 임의 Tcl을 이미 지원하는데, Python server 쪽에 이를 노출하는 MCP tool이 없다.

### 해결: `execute_tcl` tool

```python
@mcp.tool()
async def execute_tcl(command: str) -> str:
    """Execute arbitrary Tcl command in xmsim/SimVision context.

    Sends the command directly to the bridge via uplevel #0.
    Use for low-level operations not covered by other tools:
      - database -open / -close
      - probe -create / -delete
      - set / puts / after (Tcl built-ins)
      - any custom Tcl procedure

    Args:
        command: Tcl command string.
    Returns:
        Bridge response (OK body) or raises TclError on failure.
    """
    bridge = _get_bridge()
    return await bridge.execute(command)
```

### Bridge 프로토콜 — 추가 구현 없음

기존 `TclBridge.execute()`가 그대로 사용된다. bridge는 이미 regular command를 `uplevel #0`으로 평가한다 (메타 명령 `__PING__` 등과 구분됨). **server.py에 `@mcp.tool()` 래퍼만 추가하면 된다.**

```
Request:  "database -open /tmp/foo.shm -shm -default\n"
Response: "OK 38\nCreated default SHM database /tmp/foo.shm\n<<<END>>>\n"
```

### 사용 예 — 이번 세션 우회책 대비

**기존 우회책 (disconnect → nc → reconnect):**
```python
disconnect_simulator()
ssh_run("printf 'database -open /tmp/foo.shm -shm -default\\n' | nc -w5 localhost 9876")
ssh_run("printf 'probe -create top...r_regAddr -database /tmp/foo.shm\\n' | nc -w5 localhost 9876")
connect_simulator()
```

**execute_tcl 적용 후:**
```python
execute_tcl("database -open /tmp/foo.shm -shm -default")
execute_tcl("probe -create top...r_regAddr -database /tmp/foo.shm")
# disconnect/reconnect 불필요, xcelium-mcp 연결 유지
```

### 주의사항

- Tcl 에러(잘못된 명령, 존재하지 않는 path 등)는 `TclError`로 전파됨
- 시뮬레이터 상태를 바꾸는 명령(`finish`, `exit`, `restart`)은 의도치 않은 종료를 유발할 수 있음 — 사용자 책임
- 기존 tool(sim_run, save_checkpoint 등)이 있는 작업은 해당 tool 우선 사용

### 구현 항목

| # | 항목 | 파일 |
|---|------|------|
| 9-1 | `execute_tcl` tool: `@mcp.tool()` + `bridge.execute(command)` | server.py |
| 9-2 | CLAUDE.md tool 목록에 execute_tcl 추가 (Group: Debug) | CLAUDE.md (xcelium-mcp) |
| 9-3 | 테스트: MockTclServer 기반 unit test | tests/test_bridge.py |

---

## 구현 우선순위

| 순서 | 항목 | 난이도 | 영향도 | 근거 |
|:----:|------|:------:|:------:|------|
| 1 | §9 `execute_tcl` | **Very Low** | **Critical** | server.py 1함수 추가, 기존 bridge 그대로 사용. 즉각적인 우회책 제거 |
| 2 | §7 Script 재사용 정책 | Low | High | 모든 실행 경로의 기반, §6/§3-B가 의존 |
| 3 | §1 sim_restart 수정 | Low | High | 매번 에러 발생, 1-2시간 작업 |
| 4 | §5 simvisdbutil CSV 추출 + in-memory 인프라 | Medium | Critical | §2-B, §4 bisect, 전체 CSV 분석의 기반 |
| 5 | §6 Batch mode simulation tool | Medium | Critical | dump 생성 진입점, §3-B/§5와 연동 |
| 6 | §2-B dump 기반 bisect (Mode B) | Medium | Critical | §5 인프라 위에서 구현 |
| 7 | §3 probe signal scope (3-A Bridge + 3-B Batch) | Medium | High | dump 품질 + §4 신호 추가 흐름의 기반 |
| 8 | §8 사용자 디버깅 지원 (8-3 generate_debug_tcl 우선) | Low | High | 즉시 사용 가능, VNC 불필요 |
| 9 | §4 save/restore 아키텍처 변경 | High | High | Bridge mode 고도화 |
| 10 | §4 save point 전략 (L1/L2/auto-save) | Medium | Medium | §4 안정화 후 확장 |
| 11 | §8 나머지 (8-1 open_debug_view, 8-4 compare_waveforms) | Medium | Medium | X11/GUI 의존, 환경 제약 |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-03-26 | Initial draft — 5 improvement items |
| 0.2 | 2026-03-26 | §6 batch sim tool, §7 script 재사용 정책 추가, 우선순위 재조정 |
| 0.3 | 2026-03-26 | §6 SHM dump overwrite 방지 상세화: 방법 6-A (Tcl $env TEST_NAME) + 방법 6-B (rename fallback) |
| 0.4 | 2026-03-26 | §3-B `prepare_dump_scope` tool 추가: 기존 input tcl 분석 → probe scope 확인 → 부족 시 확장 tcl 생성, sim_batch_run 연동 |
| 0.5 | 2026-03-26 | 디버깅 워크플로우 문서 분리: `xcelium-mcp-debugging-workflow.plan.md` |
| 0.6 | 2026-03-26 | CSV 1회 추출 + in-memory 캐시 원칙, csv_cache.py 모듈 추가 |
| 0.7 | 2026-03-26 | §4 아키텍처 변경: "Restore → Watchpoint → Dump → CSV" 패턴, save point 전략 3종, L1/L2 유효성 관리 |
| 0.8 | 2026-03-27 | 전체 일관성 검증 7건 수정, 구현 우선순위 8단계 재조정 |
| 0.9 | 2026-03-27 | §6 SSH screen 하이브리드 전략, 구현항목 6-8 |
| 1.0 | 2026-03-27 | §8 사용자 디버깅 지원 4 tool, 구현 우선순위 10단계 |
| 1.1 | 2026-03-27 | §8 `.simvisionrc` 검증, attach_to_simvision 단순화, /proc/fd/0 불가, knowledge 작성 |
| 1.2 | 2026-03-27 | §2 Mode A 사용 케이스 명시: deposit_value(수정 가설 검증)가 핵심 고유 기능. c_ 조합 신호는 SHM dump에 포함됨(검증 완료) — Mode A 고유가 아님. "screen 불필요" 문구 전체 제거. 일관성 검증 5건 수정 |
| 1.3 | 2026-03-27 | §4 검증 결과 추가: restore→probe_add→run→dump→CSV 아키텍처 cloud0에서 완전 동작 확인 |
| 1.4 | 2026-03-27 | §9 `execute_tcl` 신규 tool 추가. 배경: bridge 연결 유지 중 raw Tcl(database -open, probe -create) 실행 불가 → disconnect→nc→reconnect 우회책 사용. 해결: server.py에 `@mcp.tool()` 래퍼 1개 추가로 우회책 제거. 구현 우선순위 1위(Very Low 난이도, Critical 영향도) |
| 1.5 | 2026-03-27 | §2 `keep_alive` 옵션 추가 및 `bisect_restore_and_debug` tool 설계. restore→dump→CSV 완료 후 시뮬레이터를 종료하지 않고 watchpoint 시점에서 정지 유지 → deposit_value/get_signal_value/sim_run/execute_tcl 인터랙티브 디버깅 가능. keep_alive=True가 초기 기본값 (v1.7에서 [B]/[C] 분리 시 False로 변경됨). §4 [A]/[B] 코드 코멘트 표기 통일, probe_control 미정의 주석 보완 |
| 1.6 | 2026-03-27 | 일관성 검증 7건 수정: (1) 다이어그램 context_signals 누락 추가, (2) keep_alive 전방참조 코드 bisect_restore_and_debug 호출로 교체, (3) §4 [A]/[B] 표기 통일(5-A/5-B → Hook [A]/[B]), (4) probe_control 미정의 → 주석으로 v2 기존 tool임 명시, (5) probe_control v3 통합 안내 추가 |
| 1.7 | 2026-03-27 | [C] Bridge interactive를 별도 Option으로 분리. 기존 [B]/[C] 혼재 → [A] Batch / [B] Bridge dump-only(keep_alive=False, 자동 shutdown) / [C] Bridge interactive(keep_alive=True, watchpoint 정지 유지)로 3-way 명시. Hook 다이어그램 3분기로 확장, request_additional_signals 반환값 'bridge_interactive' 추가, bisect_restore_and_debug 기본값 keep_alive=False로 변경, 흐름 다이어그램 [B]/[C] 분기 명시, §4 [B]/[C] 코드 예제 분리 |
| 1.8 | 2026-03-27 | §3-B regression 연동 추가: sim_batch_regression에 dump_signals 파라미터 추가, 1회 prepare_dump_scope 생성 후 전 테스트 공유. §3-B 연동 섹션을 "sim_batch_run / sim_batch_regression 연동"으로 확장, 구현 항목 3-7 업데이트 |
| 1.9 | 2026-03-27 | regression용 dump_signals 포괄 신호 집합 원칙 추가: 특정 테스트 실패 신호만 아닌 regression 전체 커버 집합 필요(나쁜 예/좋은 예 명시). 포괄 집합 결정 방법(분석서·디버깅 이력·§3-C AI 워크플로우). suggest_regression_signals tool 설계(구현 항목 3-8) |
| **2.0** | **2026-03-27** | **일관성 검증 5건 수정: (1) probe_control "통합" 표현 → "wrapping" 명확화, (2) rename_dumps → rename_dump 통일(singular), (3) §3-B 두 코드 블록 [기본]/[최적화] 레이블 추가, (4) suggest_regression_signals @mcp.tool() 시그니처 정의 추가, (5) v1.5 keep_alive 기본값 변경 이력 명시(→ v1.7에서 False로 변경)** |
| 2.1 | 2026-03-27 | MCP server 위치 정정: xcelium-mcp Python server는 로컬이 아닌 cloud0에서 실행(SSH stdio transport: `ssh cloud0 /opt/mcp-env/bin/xcelium-mcp`). §3-C suggest_regression_signals 아키텍처 결정 이유 수정(로컬 분석서 접근 불가가 진짜 이유). `mcp-operations-guide.md` 아키텍처 다이어그램 + 포트 포워딩 섹션 업데이트 |
| 2.2 | 2026-03-27 | §4 checkpoint 저장 위치·수명·정리 정책 전면 개편: (1) L1/L2 저장 위치 `/tmp` → `{sim_dir}/checkpoints/` persistent, (2) bisect 임시 checkpoint 불필요(v3 in-memory search 대체), (3) bridge init 자동 삭제에서 L1/L2 제외, (4) `save_checkpoint`에 `checkpoint_dir` 파라미터 + manifest 추가, (5) Central registry(`~/.xcelium_mcp/checkpoint_registry.json`) 설계, (6) `cleanup_checkpoints` tool 신규(list/project/stale/all 모드, dry_run=True 기본, 사용자 주도 삭제 워크플로우), (7) compile_hash 기반 자동 무효화: sim_batch_run 시 recompile 감지 → 자동 삭제, restore_checkpoint 시 hash 불일치 → restore 거부 + 자동 삭제 |
| 2.3 | 2026-03-27 | 일관성 검증 수정: (1) §4 구현 항목 번호 중복 해소(4-7~4-10 신규 추가분과 기존 4-7~4-13 충돌 → 기존분을 4-11~4-15로 재번호), (2) §4 검증 결과 표 `/tmp/mcp_checkpoints/` → `{sim_dir}/checkpoints/` 반영, (3) §1 Option B init_snapshot에 `/tmp/mcp_init` 용도 구분 주석 추가(restart 전용, L1/L2와 별개) |
| 2.4 | 2026-03-27 | §4 diagram 2곳에 [C] Bridge interactive 누락 추가: (1) "아키텍처 변경" Hook diagram, (2) 전략 2-B 다이어그램. 각 [C] 항목에 keep_alive 동작 및 deposit_value/sim_run 추가 조작 명시 |
| 2.5 | 2026-03-27 | §4 Tcl 코드 버그 수정: do_save/do_restore/do_restart/do_bisect의 `channel` 미사용, 미정의 변수(`$dir`, `$snapshot`) 수정. send_ok/send_error 일관 적용 |
| 2.6 | 2026-03-27 | §4 Hook 구조 재정립: [B] Bridge dump-only 제거 → [A'] Batch-restore 신설(sim_batch_run(from_checkpoint=..., probe_signals=...)). Bridge는 실시간 조작(deposit_value 등) 필요 시만([B] keep_alive=True). autosave는 [A']와 무관한 별개 개념(방법 C)으로 분리. sim_batch_run에 from_checkpoint/shm_path/run_duration 파라미터 추가(4-16) |
| 2.7 | 2026-03-27 | §4 L1/L2 역할 재정립: L2는 Batch에서 불필요(L1 restore 후 재실행이 기본). L2는 Bridge interactive 반복 디버깅 시만. 전략 1 다이어그램·TCL 코드·Python 코드 일관성 수정. [A'] from_checkpoint=L1. 방법 C TCL 코드 save 줄 [선택]으로 변경 |
| **2.8** | **2026-03-27** | **§2 전체 일관성 재정립: Hook 3-way [A]/[B]/[C] → [A]/[A']/[B] 체계로 통일. bisect_restore_and_debug keep_alive 기본값 False→True. rename_dump 기본값 True→False. 4-16: sim_batch_regression에도 from_checkpoint 추가. §7 Script Discovery 전면 개편: 하드코딩(run_sim) → 2-tier 설계(Tier 1: .mcp_sim_config.json, Tier 2: Makefile/shell/xrun/python 자동 탐지). sim_batch_regression screen 루프 mv 조건부 처리** |
| 2.9 | 2026-03-27 | §7 sim_dir 자동 탐지 전면 개편: ncsim/uvm/sv_directed 등 복수 검증 환경 공존 지원. `_discover_sim_dir()`: `sim/` 하위 전체 탐색 → `_analyze_tb_type()` 휴리스틱 탐지(uvm_component/run_sim/interface 패턴) → 복수 환경 사용자 제시 + default_env 선택. 결과 `~/.xcelium_mcp/sim_registry.json` 캐시 — 이후 sim 실행부터 재탐지 불필요. 탐지 흐름 다이어그램, TB 타입 판별 기준 표, sim_registry.json 구조, _analyze_tb_type() 코드 추가. 구현 항목 7-7~7-9 추가 |
| 3.0 | 2026-03-30 | §4 L2 생성 정책 재정립: sim_batch_run/regression 일반 실행 시 L1+L2 자동 저장(표준). [A']+[B] 반복 시 L2 우선 사용, 없으면 해당 테스트만 재생성. 영향 범위: L2 Save Point 설명·Python 코드 블록·전략 3 TCL(L2 save 추가)·[A'][B] 적용 텍스트·전략 선택 가이드 표(2행→1행 병합)·핵심 원칙·sim_batch_regression 파라미터 주석·구현 항목 4-16·regression 코드 주석·§2 흐름도·방법 B hook diagram 일관 수정. bisect 비교 표 2개 항목(신호 추가/watchpoint 변경) 수정 포함 |
| 3.1 | 2026-03-30 | §7 `_auto_detect_runner` 버그 수정 5건: (1) `grep -l` ERE 플래그 누락(`-E` 추가), (2) `find -o` 우선순위 오류 → `\( \)` 그룹핑으로 `-perm /111` 전체 적용, (3) shebang 확인 추가(`head -1 | startswith("#!")`), (4) xrun/irun `which` 가용성 확인 추가, (5) `python3/python` 확인 추가. confidence 로직 `len==1` → 최고 score 단독 후보 기준으로 수정. `_ask_user_runner` 신규 추가 — candidates=[]일 때 직접 입력 요청(5번 케이스), ambiguous일 때 후보 선택 + 직접 입력 옵션 제공 |
| 3.2 | 2026-03-30 | §7 `_discover_sim_dir` 전면 재설계: 이름 패턴(`sim*`/`test*`/`tb*`/`verif*`/`bench*`/`dv`) + 내용 기반 검증 2단계로 교체. git root 결정 → maxdepth 3 탐색 → 상위-하위 중복 제거 → 직속 하위 `_analyze_tb_type` 검증(unknown 제외) → 탐지 실패 시 사용자에게 simulation root 직접 입력 요청 → `sim_registry.json` 저장. 7-9 구현 항목 설명 갱신 |
| 3.3 | 2026-03-30 | §7 `_analyze_tb_type` 수정: `mixed` 타입(UVM+legacy 공존) 판별 추가 — UVM 마커와 legacy 마커를 각각 독립 확인 후 둘 다 있으면 `mixed` 반환. 기존 early-return 구조는 UVM 단독 판별로 legacy 정보 소실 문제. `grep -rl ... -l` 중복 `-l` 제거. 7-8 구현 항목에 `mixed` 반영 |
| 3.4 | 2026-03-30 | §8 `waveform_add_signals` AI 전용 그룹 + 중복 건너뜀 정책 확정(8-8): AI 추가 신호는 항상 `group_name`(기본 `"AI_Debug"`) 전용 그룹에 추가 — 없으면 자동 생성. 중복 확인은 전체 waveform(사용자 그룹 내부 포함) 기준 → 어디에 있든 이미 있으면 skip. `open_debug_view` 파라미터 기본값 `"AI_Debug"`. step 5 흐름 + TCL `do_waveform_add` 반영 |
