# xcelium-mcp v3 Improvements — Design Document

> **Summary**: xcelium-mcp v3 MCP tool 확장 — execute_tcl, batch sim, CSV 분석 인프라, save/restore 아키텍처, 사용자 디버깅 지원을 5-Phase로 단계 구현
>
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Author**: HSLEE
> **Date**: 2026-03-30
> **Status**: Draft
> **Planning Doc**: [xcelium-mcp-v3-improvements.plan.md](../01-plan/features/xcelium-mcp-v3-improvements.plan.md)

---

## 1. Overview

### 1.1 Design Goals

| Goal | Description |
|------|-------------|
| **즉시 안정화** | sim_restart 에러 제거, execute_tcl로 우회책 제거 |
| **오프라인 분석** | SHM dump → simvisdbutil CSV 추출 → in-memory 분석 |
| **단계적 구현** | Phase 1(Foundation) → Phase 5(UI) 순으로 완성도 보장 |
| **AI-Human 협업** | AI 분석 → SimVision 자동 세팅 → 사용자 직접 디버깅 |

### 1.2 Design Principles

- **Phase 독립성**: 각 Phase는 단독으로 배포·검증 가능한 완성도를 가진다 (이전 Phase 없이는 미동작하는 기능 금지 — 단, Phase 간 의존 명시 시 허용)
- **기존 API 호환성**: v2 MCP tool signature를 깨뜨리지 않는다 (파라미터 추가는 default값으로 backward-compatible)
- **SSH 단계 최소화**: cloud0에서 실행되는 xcelium-mcp server는 SSH stdio transport 사용 — 불필요한 SSH hop 추가 금지
- **오류 전파 차단**: 각 tool은 내부 오류를 catch하고 structured 오류 메시지로 반환 (unhandled exception 금지)

---

## 2. Architecture

### 2.1 전체 컴포넌트 구조

```
[Claude Code / AI Client]
         │  MCP protocol (stdio over SSH)
         ▼
[cloud0: xcelium-mcp server]                  ← 모든 MCP tool 실행 위치
    ├─ src/xcelium_mcp/server.py              ← @mcp.tool() 등록, 파라미터 라우팅
    ├─ src/xcelium_mcp/tcl_bridge.py          ← Python ↔ Tcl 통신 레이어
    ├─ tcl/mcp_bridge.tcl                     ← SimVision Tcl bridge (meta command 처리)
    ├─ src/xcelium_mcp/sim_runner.py          ← Script Discovery + Batch/Regression 실행
    ├─ src/xcelium_mcp/csv_cache.py           ← simvisdbutil CSV 추출 + in-memory 캐시
    ├─ src/xcelium_mcp/checkpoint_manager.py  ← L1/L2 checkpoint 저장·복원·정리
    └─ src/xcelium_mcp/debug_tools.py         ← generate_debug_tcl, export_debug_context

[cloud0: Xcelium Simulator]
    ├─ SHM dump files ({sim_dir}/dump/)
    └─ Checkpoints ({sim_dir}/checkpoints/)

[cloud0: SimVision]                           ← Phase 5에서만 연동
    └─ VNC session (open_debug_view)
```

### 2.2 실행 모드 (Hook 체계)

```
[A]  Batch full      : sim_batch_run(test) → L1+L2 저장 → SHM dump → 종료
[A'] Batch-restore   : sim_batch_run(from_checkpoint=L1/L2, probe_signals=[...])
                       → 새 SHM → 종료 (GUI 불필요)
[B]  Bridge interactive: connect_simulator → restore_checkpoint(L2)
                         → probe → sim_run → 정지 유지 → deposit_value 등
```

### 2.3 Phase 간 의존성

```
Phase 1 (Foundation)
  ├─ §9 execute_tcl          독립 (mcp_bridge 재사용)
  ├─ §7 Script Discovery      독립 (mcp_registry.json 생성)
  └─ §1 sim_restart 수정      독립 (mcp_bridge.tcl 수정)

Phase 2 (CSV Infrastructure)  requires: Phase 1 §7
  ├─ §5 simvisdbutil CSV      독립 모듈 (csv_cache.py)
  └─ §6 Batch sim tool        requires: §7 Script Discovery

Phase 3 (Advanced Analysis)   requires: Phase 2
  ├─ §2-B dump bisect         requires: §5 CSV 인프라
  ├─ §3 probe scope           requires: §6 Batch sim
  └─ §8-B generate_debug_tcl  독립 (파일 생성만)

Phase 4 (Bridge Enhancement)  requires: Phase 2
  └─ §4 save/restore 아키텍처  requires: §5 CSV 인프라

Phase 5 (UI/Visual)           requires: Phase 4
  └─ §8-A open_debug_view     requires: §4 save/restore
  └─ §8-D compare_waveforms   requires: §5 CSV 인프라
```

### 2.4 핵심 데이터 흐름

```
[Batch 실행]
sim_batch_run → _discover_sim_dir → _auto_detect_runner
             → Xcelium compile + run → SHM dump
             → save_checkpoint(L1) → save_checkpoint(L2_{test})
             → csv_cache 초기화

[Dump 분석]
bisect_signal_dump → csv_cache.extract(shm_path, signals)
                  → in-memory binary search
                  → 신호 없으면 → request_additional_signals → [A]/[A']/[B]

[Bridge 디버깅]
connect_simulator → restore_checkpoint(L2)
                 → probe_control → sim_run(watchpoint)
                 → execute_tcl("database -open...") → dump
                 → csv_cache.extract → 분석
```

---

## 3. Data Model

### 3.1 mcp_registry.json — 통합 레지스트리

위치: `~/.xcelium_mcp/mcp_registry.json`

**역할**: discovery 인덱스 + checkpoint 상태. runner 실행 설정은 저장하지 않는다.
- `.mcp_sim_config.json`이 있는 환경 → `config_file` 포인터만 기록
- `.mcp_sim_config.json`이 없는 환경 → (없음 — 반드시 config 파일 생성 후 포인터 기록)

`project_root` → `sim_dir` 계층으로 여러 TB 환경을 함께 관리.

```json
{
  "version": 1,
  "projects": {
    "/home/user/git.clone/venezia-t0": {
      "discovered_at": "2026-03-30T10:00:00Z",
      "environments": {
        "/home/user/git.clone/venezia-t0/design/top/sim/ncsim": {
          "tb_type": "ncsim_legacy",
          "is_default": true,
          "confidence": "high",
          "config_file": ".mcp_sim_config.json",
          "checkpoint_dir": "/home/user/git.clone/venezia-t0/design/top/sim/ncsim/checkpoints",
          "checkpoints": [
            {
              "name": "L1_common_init",
              "level": "L1",
              "test_name": null,
              "compile_hash": "a3f2b1c9",
              "created_at": "2026-03-30T11:00:00Z",
              "description": "Common init — before test-specific setup"
            },
            {
              "name": "L2_TOP015_setup",
              "level": "L2",
              "test_name": "TOP015",
              "compile_hash": "a3f2b1c9",
              "created_at": "2026-03-30T11:02:00Z",
              "description": "TOP015 test-specific setup complete"
            }
          ]
        },
        "/home/user/git.clone/venezia-t0/design/top/sim/uvm": {
          "tb_type": "uvm",
          "is_default": false,
          "confidence": "high",
          "config_file": ".mcp_sim_config.json",
          "checkpoint_dir": "/home/user/git.clone/venezia-t0/design/top/sim/uvm/checkpoints",
          "checkpoints": []
        }
      }
    }
  }
}
```

**계층 구조**:

| 계층 | key | 포함 데이터 |
|------|-----|------------|
| L1 | `projects[project_root]` | `discovered_at` |
| L2 | `environments[sim_dir]` | `tb_type`, `is_default`, `confidence`, `config_file` (포인터) |
| L3 | — | `checkpoint_dir` + `checkpoints[]` |

**`config_file`**: `sim_dir` 기준 상대 경로. 로드 순서:
1. registry에서 `config_file` 경로 읽기
2. `{sim_dir}/{config_file}` 로드 → runner 설정 전체 획득
3. `config_file` 없으면 → `_discover_sim_dir()` 실행 → 결과를 `.mcp_sim_config.json`으로 저장 → registry 업데이트

**`path` 필드 없음**: checkpoint 실제 경로 = `checkpoint_dir + "/" + name` 으로 유도.

### 3.2 CSV Cache — in-memory 구조

모듈: `csv_cache.py`

```python
# 캐시 키: (shm_path, signal_name)
# 캐시 값: DataFrame (time_ns, value)
_cache: dict[tuple[str, str], pd.DataFrame] = {}

# 추출 단위: 요청 신호 묶음 1회 simvisdbutil 호출 → 개별 캐시 저장
async def extract(shm_path: str, signals: list[str], start_ns=0, end_ns=None) -> dict[str, pd.DataFrame]
async def get_cached(shm_path: str, signal: str) -> pd.DataFrame | None
def clear_cache(shm_path: str = None)  # shm_path=None이면 전체 초기화
```

### 3.3 mcp_sim_config.json — runner 설정 파일 (단일 소스)

위치: `{sim_dir}/.mcp_sim_config.json`

**생성 방식**: `_discover_sim_dir()` 실행 시 auto-detection 결과를 이 파일로 저장.
사용자가 직접 편집하여 override 가능 (git 관리 권장).
파일이 이미 있으면 탐지 생략 — 파일 내용을 그대로 사용.

runner 실행 설정의 **single source of truth**. `mcp_registry.json`은 이 파일을 포인터로만 참조한다.

```json
{
  "version": 1,
  "runner": {
    "type": "shell",
    "script": "run_sim_mcp",
    "regression_script": "run_regression_mcp",
    "script_shell": "/bin/tcsh",
    "login_shell": "/bin/tcsh",
    "env_files": ["/home/user/.cadence_setup.csh"],
    "env_shell": "/bin/tcsh",
    "source_separately": true
  },
  "checkpoint_dir": "checkpoints",
  "dump_dir": "dump",
  "default_test": "TOP015"
}
```

> **`exec_cmd`는 파일에 저장하지 않는다.** 런타임에 `_resolve_exec_cmd(runner)`로 도출.
> `script`를 바꾸면 `exec_cmd`가 자동으로 일관되게 갱신된다.
> 복잡한 명령을 직접 지정하려면 `exec_cmd_override` 필드를 추가 (아래 참조).

**runner 필드 설명**:

| 필드 | 필수 | 설명 | 생성 방식 |
|------|:---:|------|---------|
| `type` | ✅ | `"shell"` \| `"makefile"` \| `"xrun"` \| `"python"` \| `"custom"` | `_auto_detect_runner()` |
| `script` | ✅ | 단일 테스트 실행 스크립트/빌드 파일 이름 | `_auto_detect_runner()` |
| `regression_script` | — | 회귀 실행 스크립트 (없으면 `script`로 반복 실행) | `_auto_detect_runner()` |
| `script_shell` | — | 스크립트 shebang interpreter (`null` = Makefile 등) | `_detect_shell_and_env()` |
| `login_shell` | ✅ | `$SHELL` 기반 사용자 login shell | `$SHELL` |
| `env_files` | — | EDA env 파일 목록 (없으면 `[]`) | `_detect_eda_env()` |
| `env_shell` | — | env 파일 source 시 사용할 shell | `_detect_env_shell()` |
| `source_separately` | ✅ | login shell 자동 로딩 실패 시 `true` | `_detect_eda_env()` |
| `exec_cmd_override` | — | 직접 지정 실행 명령 (있으면 도출 생략) | 사용자 직접 작성 |
| `regression_exec_cmd_override` | — | 직접 지정 회귀 명령 (있으면 도출 생략) | 사용자 직접 작성 |

#### `.mcp_sim_config.json` 생성 흐름

```
_discover_sim_dir(hint) 실행:
  1. git root 결정 (실패 시 ~)
  2. 이름 패턴(sim*/test*/tb*/verif*/bench*/dv) maxdepth 3 탐색
  3. 상위-하위 중복 제거
  4. 각 후보에서 _analyze_tb_type() → unknown 제외
  5a. 탐지 성공 + 단일 환경  → _auto_detect_runner() → .mcp_sim_config.json 생성
  5b. 탐지 성공 + 복수 환경  → 사용자에게 목록 제시 + default 선택 요청
                              → 선택된 환경에 _auto_detect_runner() → .mcp_sim_config.json 생성
  6. 탐지 완전 실패          → 사용자에게 simulation root 폴더 직접 입력 요청:
       "Could not auto-detect simulation directory.
        Please enter the simulation root folder path:
          (e.g., ~/git.clone/myproject/sim)"
       → 입력 경로에서 _analyze_tb_type() + _auto_detect_runner() 실행
       → .mcp_sim_config.json 생성

_auto_detect_runner(sim_dir) 실행:
  후보 탐지: Makefile → shell script → xrun → python → (없음)
  1. 단일 고신뢰도 후보  → 사용자 확인 후 채택
  2. 복수 후보(ambiguous) → 사용자에게 선택 목록 + "직접 입력" 옵션 제시:
       "Multiple runners detected. Select one:
        1. [shell]  ./run_sim_mcp {test_name}
        2. [make]   make sim TEST={test_name}
        3. 직접 입력"
  3. 후보 없음(탐지 실패)  → 직접 입력 요청:
       "Could not auto-detect simulation runner in: {sim_dir}
        Please enter the run command (use {test_name} as placeholder):
          Example: ./run_sim -test {test_name}"
       → 입력값으로 cfg 구성 (confidence: "user_provided")

모든 경우에서 결과는 _save_sim_config(sim_dir, cfg) → .mcp_sim_config.json 저장
사용자가 직접 입력한 경우 → exec_cmd만 저장 (shell detection 필드 null)
```

#### Python 구현 — `_resolve_sim_runner` (진입점)

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
        await _save_sim_config(sim_dir, detected)
        return detected
    else:
        return await _ask_user_runner(sim_dir, detected["candidates"])
```

#### Python 구현 — `_auto_detect_runner` (Tier 2)

```python
async def _auto_detect_runner(sim_dir: str) -> dict:
    candidates = []

    # 1. Makefile + sim/test/run target 포함 여부
    r = await ssh_run(f"grep -lE 'sim:|test:|run:' {sim_dir}/Makefile 2>/dev/null")
    if r.strip():
        targets = await ssh_run(
            f"grep -oE '^(sim|test|run|simulate|regression)[^:]*:' {sim_dir}/Makefile | tr -d ':'"
        )
        best_target = targets.strip().splitlines()[0] if targets.strip() else "sim"
        candidates.append({"runner": "make",
                           "exec_cmd": f"make {best_target} TEST={{test_name}}",
                           "score": 3})

    # 2. 실행 권한 있는 shell script (이름 패턴 + shebang 확인)
    r = await ssh_run(
        f"find {sim_dir} -maxdepth 1 -perm /111 "
        r"\( -name 'run_sim*' -o -name 'run_test*' -o -name '*.sh' \) 2>/dev/null"
    )
    for script in r.strip().splitlines():
        shebang = await ssh_run(f"head -1 {script} 2>/dev/null")
        if shebang.strip().startswith("#!"):
            candidates.append({"runner": "shell",
                               "exec_cmd": f"{script} {{test_name}}",
                               "score": 2})

    # 3. *.f filelist + xrun/irun 가용 여부
    r = await ssh_run(f"ls {sim_dir}/*.f 2>/dev/null | head -1")
    if r.strip():
        tool = await ssh_run("which xrun 2>/dev/null || which irun 2>/dev/null | head -1")
        if tool.strip():
            tool_name = tool.strip().split("/")[-1]
            candidates.append({"runner": "xrun",
                               "exec_cmd": f"{tool_name} -f {r.strip()} +define+TEST={{test_name}} -run",
                               "score": 1})

    # 4. run_sim.py / sim.py
    r = await ssh_run(f"ls {sim_dir}/run_sim.py {sim_dir}/sim.py 2>/dev/null | head -1")
    if r.strip():
        py = await ssh_run("which python3 2>/dev/null || which python 2>/dev/null | head -1")
        py_cmd = py.strip().split("/")[-1] if py.strip() else "python3"
        candidates.append({"runner": "python",
                           "exec_cmd": f"{py_cmd} {r.strip()} --test {{test_name}}",
                           "score": 1})

    # 5. 모두 해당 없음
    if not candidates:
        return {"confidence": "none", "candidates": []}

    best = max(candidates, key=lambda x: x["score"])
    top_score = best["score"]
    top_candidates = [c for c in candidates if c["score"] == top_score]
    confidence = "high" if len(top_candidates) == 1 else "ambiguous"
    return {**best, "confidence": confidence, "candidates": candidates}
```

#### Python 구현 — `_ask_user_runner` (사용자 입력 fallback)

`ask_user()` 인라인 호출은 FastMCP stdio 환경에서 불가능하다.
대신 `UserInputRequired` exception을 raise하고, 호출 MCP tool이 `prompt`를 응답으로 반환하여 사용자에게 전달한다.

```python
async def _ask_user_runner(sim_dir: str, candidates: list) -> dict:
    """복수 후보(ambiguous) 또는 탐지 실패(none) 시 UserInputRequired를 raise.

    호출자(MCP tool)는 e.prompt를 응답으로 반환 → 사용자가 값을 입력 →
    MCP tool 재호출 시 .mcp_sim_config.json에 직접 작성하거나 sim_dir 명시.
    """
    if not candidates:
        raise UserInputRequired(
            f"Could not auto-detect simulation runner in:\n  {sim_dir}\n\n"
            "Please enter the run command (use {test_name} as placeholder):\n"
            "  Example: ./run_sim -test {test_name}\n"
            "  Example: make sim TEST={test_name}\n"
            "  Example: xrun -f sim.f +define+TEST={test_name} -run"
        )

    # ambiguous: 후보 목록 + 직접 입력 옵션 제시
    options = "\n".join(f"{i+1}. [{c['runner']}] {c['exec_cmd']}" for i, c in enumerate(candidates))
    raise UserInputRequired(
        f"Multiple runners detected in {sim_dir}. Select one:\n{options}\n"
        f"{len(candidates)+1}. Enter custom command"
    )
```

#### Python 구현 — `_discover_sim_dir` + `_analyze_tb_type`

```python
async def _discover_sim_dir(hint: str = "") -> list[dict]:
    """Discover all sim environments. Returns list of env dicts."""
    # 1. git root 결정
    if hint:
        project_root = hint
    else:
        r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
        project_root = r.strip()

    # 2. 이름 패턴 maxdepth 3 탐색
    patterns = r"-name 'sim*' -o -name 'test*' -o -name 'tb*' -o -name 'verif*' -o -name 'bench*' -o -name 'dv'"
    r = await ssh_run(
        f"find {project_root} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) 2>/dev/null | sort"
    )
    raw = r.strip().splitlines()

    # 3. 상위-하위 중복 제거
    raw = sorted(set(raw), key=len)
    deduped = []
    for path in raw:
        if not any(path.startswith(p + "/") for p in deduped):
            deduped.append(path)

    # 4. 직속 하위에서 _analyze_tb_type 실행 (unknown 제외)
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

    # 5. 탐지 실패 → UserInputRequired raise (호출 MCP tool이 prompt를 사용자에게 반환)
    if not envs:
        raise UserInputRequired(
            "Could not auto-detect simulation directory.\n"
            "Please enter the simulation root folder path:\n"
            "  (e.g., ~/git.clone/myproject/sim\n"
            "         ~/git.clone/myproject/test/ncsim)"
        )

    return envs


async def _analyze_tb_type(sim_dir: str) -> str:
    """Heuristic TB type detection from sim_dir contents."""
    # UVM 마커 확인
    r_uvm = await ssh_run(
        f"grep -rl 'uvm_component\\|uvm_test\\|UVM_TEST' {sim_dir} "
        f"--include='*.sv' --include='*.svh' 2>/dev/null | head -1"
    )
    has_uvm = bool(r_uvm.strip())

    # ncsim_legacy 마커 확인
    r_legacy = await ssh_run(f"ls {sim_dir}/run_sim {sim_dir}/*.f 2>/dev/null")
    has_legacy = bool(r_legacy.strip())

    # mixed: UVM + legacy 공존
    if has_uvm and has_legacy:
        return "mixed"
    if has_uvm:
        return "uvm"
    if has_legacy:
        return "ncsim_legacy"

    # sv_directed: non-UVM SystemVerilog
    r = await ssh_run(
        f"grep -rl 'interface\\|program ' {sim_dir} --include='*.sv' 2>/dev/null | head -1"
    )
    if r.strip():
        return "sv_directed"

    return "unknown"
```

**TB 타입 판별 기준:**

| TB 타입 | 판별 지표 |
|---------|----------|
| `ncsim_legacy` | `run_sim` 스크립트, `*.f` filelist, task 기반 stimulus |
| `uvm` | `uvm_pkg`, `uvm_test`, `uvm_component`, `UVM_TEST` plusarg |
| `sv_directed` | `interface`, `program` 블록, non-UVM SystemVerilog |
| `mixed` | uvm + legacy 공존 |
| `unknown` | 판별 불가 — 탐지 대상에서 제외 |

### 3.4 Shell / EDA Env 탐지 규칙

`_detect_shell_and_env(sim_dir)` 함수가 아래 우선순위로 탐지 후 **`.mcp_sim_config.json`에 저장**한다. (`mcp_registry.json`에는 `config_file` 포인터만 기록)

#### script_shell 탐지 (스크립트 interpreter)

```
1. 스크립트 shebang: head -1 {script}
   #!/bin/tcsh      → /bin/tcsh
   #!/bin/csh       → /bin/csh
   #!/bin/bash      → /bin/bash
   #!/usr/bin/env zsh → /usr/bin/env로 zsh 탐지
   #!/bin/ksh       → /bin/ksh

2. shebang 없음 → login_shell과 동일 (assumed)
```

#### login_shell 탐지

```
$SHELL 환경변수 → 없으면 /bin/sh (POSIX fallback)
```

#### env_shell 탐지 (env 파일 source 시 사용할 shell)

```
1. env 파일 shebang (가장 정확)
2. 파일 확장자:
   .tcsh            → /bin/tcsh
   .csh             → /bin/csh   (tcsh와 구분)
   .bash            → /bin/bash
   .sh              → /bin/sh
   .zsh             → /bin/zsh
   .ksh             → /bin/ksh
3. 내용 패턴 (확장자 없거나 .sh/.csh로 모호할 때):
   setenv VAR val   → csh 계열
   foreach / breaksw → tcsh 고유 → /bin/tcsh
   export VAR=val   → sh/bash 계열
   [[ ... ]]        → /bin/bash 또는 /bin/zsh
   typeset / autoload → /bin/zsh 또는 /bin/ksh
4. fallback → login_shell
```

#### EDA env 파일 탐지 흐름

```
Step 1: login shell 직접 테스트
  {login_shell} -lc "which xrun 2>/dev/null"
  → 찾음: source_separately=false, env_files=[]

Step 2: 후보 파일 탐색 (탐색 위치 우선순위)
  $HOME/           이름 패턴: .cshrc, .cadence, setup.{csh,sh}, sourceme.*, *eda*
  {project_root}/  이름 패턴: setup.*, sourceme.*, *eda*, *.env
  {sim_dir}/       이름 패턴: setup.*, sourceme.*, *eda*, *.env
  /etc/profile.d/  이름 패턴: cadence*, *eda*, xcelium*

  내용 패턴 (grep 확인):
  XCELIUM_HOME, CDS_LIC_FILE, xrun, irun, setenv.*LIC

Step 3: 후보별 유효성 검증
  {env_shell} -c "source {candidate} && which xrun 2>/dev/null"
  → xrun 발견: env_files=[candidate], source_separately=true

Step 4: 탐지 실패 → UserInputRequired raise
  raise UserInputRequired("EDA env file not found. Enter path (or Enter to skip):...")
  → 호출자(_detect_shell_and_env)로 propagate → 최종 MCP tool이 prompt 반환
```

#### exec_cmd 런타임 도출 — `_resolve_exec_cmd`

`exec_cmd`는 파일에 저장하지 않는다. 실행 시점에 runner 필드로부터 도출한다.

```python
from dataclasses import dataclass

@dataclass
class ExecInfo:
    cmd: str               # 실행 명령
    needs_test_name: bool  # True  → {test_name} 치환 후 실행 (단일: 1회, regression: 루프)
                           # False → 명령 완결, {test_name} 불필요 (regression_script 내장)

def _resolve_exec_cmd(runner: dict, regression: bool = False) -> ExecInfo:
    """Derive exec_cmd from runner fields at runtime. Never stored in config file."""
    # 1. override 필드가 있으면 그대로 사용
    override_key = "regression_exec_cmd_override" if regression else "exec_cmd_override"
    if override_key in runner:
        return ExecInfo(cmd=runner[override_key], needs_test_name=False)

    # 2. script 선택 + needs_test_name 결정
    #    regression_script 있음 → 스크립트가 전체 테스트 내장, {test_name} 불필요
    #    regression_script 없음 → 호출자가 {test_name} 치환 담당 (단일 실행 or 루프)
    if regression:
        if "regression_script" in runner:
            script = runner["regression_script"]
            needs_test_name = False
        else:
            script = runner["script"]
            needs_test_name = True
    else:
        script = runner["script"]
        needs_test_name = True

    # 3. script_run 구성 (shebang 유무)
    suffix = " {test_name}" if needs_test_name else ""
    if runner.get("script_shell"):          # shebang 있음 → OS가 interpreter 처리
        script_run = f"./{script}{suffix}"
    else:                                   # shebang 없음 → login_shell로 명시 실행
        script_run = f"{runner['login_shell']} ./{script}{suffix}"

    # 4. cmd 구성 (source_separately 분기)
    if runner.get("source_separately"):
        sources = " && ".join(f"source {f}" for f in runner.get("env_files", []))
        env_shell = runner.get("env_shell", runner["login_shell"])
        cmd = f"{env_shell} -c '{sources} && {script_run}'"
    else:
        cmd = f"{runner['login_shell']} -lc '{script_run}'"

    return ExecInfo(cmd=cmd, needs_test_name=needs_test_name)
```

**예시:**
```python
runner = {
    "script": "run_sim_mcp",
    "regression_script": "run_regression_mcp",
    "script_shell": "/bin/tcsh",
    "login_shell": "/bin/tcsh",
    "env_files": ["/home/user/.cadence_setup.csh"],
    "env_shell": "/bin/tcsh",
    "source_separately": True,
}

_resolve_exec_cmd(runner)
# → ExecInfo(cmd="tcsh -c 'source /home/user/.cadence_setup.csh && ./run_sim_mcp {test_name}'", needs_test_name=True)
# (단일 실행 → 호출자가 {test_name} 치환 후 1회 실행)

_resolve_exec_cmd(runner, regression=True)
# → ExecInfo(cmd="tcsh -c 'source /home/user/.cadence_setup.csh && ./run_regression_mcp'", needs_test_name=False)
# (regression_script 있음 → 명령 완결, 그대로 1회 실행)

# regression_script 없는 경우
runner_without_reg_script = {
    "script": "run_sim_mcp",
    # regression_script 없음
    "script_shell": "/bin/tcsh",
    "login_shell": "/bin/tcsh",
    "env_files": ["/home/user/.cadence_setup.csh"],
    "env_shell": "/bin/tcsh",
    "source_separately": True,
}
_resolve_exec_cmd(runner_without_reg_script, regression=True)
# → ExecInfo(cmd="tcsh -c 'source /home/user/.cadence_setup.csh && ./run_sim_mcp {test_name}'", needs_test_name=True)
# (regression_script 없음 → 호출자가 {test_name} 치환하며 test_list 순회)
```

**사용자 override 예시** (`.mcp_sim_config.json`에 직접 추가):
```json
"exec_cmd_override": "make sim TEST={test_name} EXTRA=+debug"
```

### 3.5 Save Point 전략

3가지 save point 전략을 지원한다. `sim_batch_run` 정상 실행 시 전략 1이 기본 동작하며, 전략 2·3은 옵션이다.

#### 전략 1: 계층형 L1/L2 (기본)

```
sim_batch_run("TOP015") 실행 흐름:
  compile → run → [L1_common_init 저장 시점] → test_specific init → [L2_TOP015_setup 저장] → run → SHM dump

L1 (common_init): 공통 초기화 완료 후 — 모든 테스트가 재사용
L2 (per-test):    테스트별 setup 완료 후 — 해당 테스트만 재사용

저장 시점 결정:
  L1: TB 코드의 initial begin 블록 종료 후 / `// MCP_L1_SAVE` 주석 인식
  L2: `$display("MCP_L2_SAVE")` 또는 시간 기반 (configurable)
```

#### 전략 2: 전이 기반 (Transition-based)

특정 신호 값 변화 시점에 자동 저장. `sim_batch_run`에 `save_on` 파라미터로 지정.

```python
# 예시: FSM이 S_ACTIVE로 전이하는 시점에 자동 저장
sim_batch_run("TOP015", save_on=[
    {"signal": "top.hw.u_fsm.r_state", "op": "eq", "value": "3", "name": "at_S_ACTIVE"}
])
# → checkpoints/at_S_ACTIVE/ 에 저장
```

#### 전략 3: Rolling Auto-save (N개 유지)

장기 실행 시뮬레이션에서 일정 주기로 자동 저장, 최근 N개만 유지.

```python
# sim_batch_run 내부 (향후 확장):
async def sim_run_with_autosave(interval_ns=1_000_000, keep_last_n=5):
    """
    매 interval_ns마다 snapshot 저장.
    keep_last_n 초과 시 가장 오래된 것부터 삭제.
    이름: auto_save_{time_ns}
    """
```

> **구현 범위**: 전략 1은 Phase 2(sim_batch_run)에서 구현. 전략 2·3은 Phase 4(checkpoint_manager) 확장으로 구현.

---

## 4. MCP Tool Interface

### Phase 1 신규/수정 Tool

| Tool | 변경 | 파일 |
|------|------|------|
| `execute_tcl(tcl_cmd)` | **신규** | server.py + mcp_bridge.tcl |
| `sim_restart()` | **수정** — `run -clean` fallback | mcp_bridge.tcl |

#### `execute_tcl` 시그니처

```python
@mcp.tool()
async def execute_tcl(
    tcl_cmd: str,                    # 실행할 Tcl 명령 (단일 또는 멀티라인)
    timeout: int = 30,               # 응답 대기 타임아웃 (초)
) -> str:
    """Execute arbitrary Tcl command in connected SimVision bridge session.

    Returns raw Tcl output. Raises if not connected or timeout.
    Use for commands not covered by other tools: database -open, probe -create, etc.

    WARNING: Commands that change simulator state (finish, exit, restart) can
    cause unintended termination — caller's responsibility.
    Prefer dedicated tools (sim_run, save_checkpoint) when they cover the need.
    """
    bridge = _get_bridge()
    return await bridge.execute(tcl_cmd)
```

#### Bridge 프로토콜 — execute_tcl 내부 동작

기존 `TclBridge.execute()`를 그대로 사용. bridge는 regular command를 `uplevel #0`으로 평가:

```
Request:  "database -open /tmp/foo.shm -shm -default\n"
Response: "OK 38\nCreated default SHM database /tmp/foo.shm\n<<<END>>>\n"

Request:  "invalid_cmd\n"
Response: "ERROR 25\ninvalid command name ...\n<<<END>>>\n"
```

**기존 우회책 대비 (disconnect → nc → reconnect):**

```python
# 이전 (v2 우회책):
disconnect_simulator()
ssh_run("printf 'database -open /tmp/foo.shm -shm -default\\n' | nc -w5 localhost 9876")
ssh_run("printf 'probe -create top...r_regAddr -database /tmp/foo.shm\\n' | nc -w5 localhost 9876")
connect_simulator()

# v3 (execute_tcl):
execute_tcl("database -open /tmp/foo.shm -shm -default")
execute_tcl("probe -create top...r_regAddr -database /tmp/foo.shm")
# disconnect/reconnect 불필요 — xcelium-mcp 연결 유지
```

**mcp_bridge.tcl — meta command 등록:**

```tcl
# meta command __EXECUTE_TCL__: "tcl_cmd"
proc ::mcp_bridge::do_execute_tcl {channel cmd_str} {
    if {[catch {uplevel #0 $cmd_str} result]} {
        ::mcp_bridge::send_error $channel "TclError: $result"
        return
    }
    ::mcp_bridge::send_ok $channel $result
}
```

### Phase 2 신규 Tool

| Tool | 변경 | 파일 |
|------|------|------|
| `sim_batch_run(...)` | **신규** | server.py + sim_runner.py |
| `sim_batch_regression(...)` | **신규** | server.py + sim_runner.py |
| `extract_csv(...)` | **신규** | server.py + csv_cache.py |

#### `sim_batch_run` 시그니처

```python
@mcp.tool()
async def sim_batch_run(
    test_name: str,                  # 테스트 이름 (예: "TOP015")
    sim_dir: str = "",               # "" → mcp_registry.json default_env 사용
    from_checkpoint: str = "",       # "" → [A] 전체 실행; L1/L2 이름 → [A'] restore
    probe_signals: list[str] = [],   # [A'] 모드에서 추가 probe할 신호 목록
    shm_path: str = "",              # [A'] 새 SHM 저장 경로 (default: dump/{test}_extra.shm)
    run_duration: str = "",          # 특정 시점까지만 실행 (e.g. "10ms")
    rename_dump: bool = False,       # SHM을 test_name 기반 이름으로 rename (방법 6-B fallback)
    dump_signals: list[str] = [],    # [A] 전체 실행 시 추가 dump 신호 (prepare_dump_scope 연동)
    timeout: int = 600,              # SSH 대기 타임아웃 (초)
) -> str:
    """Run simulation for single test.

    Normal run ([A]): from_checkpoint="" → compile → run → save L1+L2 → SHM dump
    Restore run ([A']): from_checkpoint=name → restore → probe_add → run → new SHM

    Returns: log summary (PASS/FAIL lines, error count, SHM dump path).
    """
```

#### `sim_batch_run` 내부 흐름 (상세)

```
1. sim_dir 결정:
   sim_dir != "" → 그대로 사용
   sim_dir == "" → mcp_registry.json default_env 조회 → _discover_sim_dir() fallback

2. runner 결정:
   _resolve_sim_runner(sim_dir) → .mcp_sim_config.json (Tier 1) 또는 _auto_detect_runner() (Tier 2)

3. dump_signals 있으면:
   actual_tcl = prepare_dump_scope(input_tcl=original_setup_tcl, additional_signals=dump_signals)
   → run_sim에서 -input actual_tcl 사용

4. from_checkpoint 지정 ([A'] restore 실행):
   a. checkpoint_manager.verify_hash(from_checkpoint) → 불일치 시 거부 + 에러
   b. input tcl 앞에 "restart {from_checkpoint} -path {checkpoint_dir}" 삽입
   c. shm_path 미지정 시 → dump/{test_name}_extra.shm
   d. run_duration 지정 시 → "run {duration}" 만 실행 (전체 run 대신)
   e. compile+elaborate 생략

5. SSH 경유 실행:
   단일 테스트: ssh_run(exec_cmd.format(test_name=test_name), timeout=timeout)
   장기 실행(timeout>120): screen 세션 + log polling

6. 완료 대기:
   poll: tail -3 /tmp/screen_{session}.log
   완료 조건: "COMPLETE" or "$finish" in log

7. 로그 파싱:
   grep -E "PASS|FAIL|Errors:|COMPLETE" {log_file} | tail -20

8. [A] 일반 실행 완료 후:
   save_checkpoint("L1_common_init")  ← L1 없는 경우에만
   save_checkpoint("L2_{test_name}_setup")  ← 항상 (or per-test 시점)

9. 결과 반환: "PASS/FAIL, Errors: N, SHM: {shm_path}"
```

**SHM Dump Overwrite 방지:**

```tcl
# 방법 6-A (권장): input tcl에서 $env(TEST_NAME) 사용
set test_name $env(TEST_NAME)
set shm_path "../dump/ci_top_${test_name}.shm"
database -open $shm_path -shm
probe -create top -unpacked 100 -database $shm_path -depth all -all -memories -dynamic
run 10000ms
```

```bash
# 방법 6-B fallback (rename_dump=True): 시뮬레이션 완료 후 mv
if [ -d dump/ci_top.shm ]; then
    mv dump/ci_top.shm dump/ci_top_${TEST_NAME}.shm
fi
```

`sim_batch_run` 구현: 방법 6-A 우선 (setup_rtl.tcl에 `$env(TEST_NAME)` 패턴 확인), 없으면 방법 6-B fallback (`rename_dump=True` 시).

#### `sim_batch_regression` 시그니처

```python
@mcp.tool()
async def sim_batch_regression(
    test_list: list[str],            # [] → 자동 탐지 (mcp_sim_config.json의 test_list)
    sim_dir: str = "",
    from_checkpoint: str = "",       # [A'] 모드: 이 checkpoint에서 restore 후 각 테스트 실행
                                     # 미지정(일반 실행): L1 없으면 첫 테스트에서 생성, 각 테스트 L2 자동 저장
    dump_signals: list[str] = [],    # regression 공통 추가 dump 신호
                                     # → 1회 prepare_dump_scope 후 전 테스트 공유 (효율)
    rename_dump: bool = False,       # 방법 6-B fallback (기본 False = 방법 6-A 사용)
    parallel: bool = False,          # screen 병렬 실행 (Phase 2에서 기본 False)
) -> str:
    """Run regression over test list. Each test: [A] or [A'] mode.

    Normal run (from_checkpoint=""): L1 없으면 첫 테스트에서 생성, 각 테스트별 L2 자동 저장.
    Restore run (from_checkpoint=name): 지정 checkpoint에서 시작, L1/L2 생성 생략.

    dump_signals 있으면: 1회 prepare_dump_scope → extended tcl → 전 테스트 공유.
    Returns: regression summary table (N/M PASS, failures: [...]).
    """
```

#### `sim_batch_regression` 내부 흐름

`regression_script` 존재 여부에 따라 두 경로로 분기한다.

```python
# screen 세션 기반 regression 실행
session = f"mcp_regression_{timestamp}"
runner = load_runner(sim_dir)  # .mcp_sim_config.json에서 로드

# dump_signals 있으면: 1회만 prepare_dump_scope
if dump_signals:
    shared_tcl = await prepare_dump_scope(
        input_tcl=original_setup_tcl,
        additional_signals=dump_signals,
    )  # → setup_rtl_debug.tcl 생성 (1회)
else:
    shared_tcl = original_setup_tcl

# screen 세션 생성 (tcsh login shell + EDA 환경 1회 설정)
await ssh_run(f"screen -dmS {session} -L -Logfile /tmp/screen_{session}.log tcsh -l")
await ssh_run(f"screen -S {session} -X stuff 'cd {sim_dir}\\n'")

info = _resolve_exec_cmd(runner, regression=True)
# info.needs_test_name=False → regression_script 내장, 명령 완결 → 1회 실행
# info.needs_test_name=True  → {test_name} 치환 필요 → test_list 순회

if not info.needs_test_name:
    # ── 경로 A: needs_test_name=False ───────────────────────────────
    # regression_script가 전체 테스트를 내부 관리 → 1회 실행
    # info.cmd = e.g. "tcsh -c 'source ~/.cadence_setup.csh && ./run_regression_mcp'"
    await ssh_run(f"screen -S {session} -X stuff '{info.cmd}\\n'")

    # 완료 대기: 로그에서 전체 완료 신호 확인
    while True:
        log = await ssh_run(f"tail -5 /tmp/screen_{session}.log")
        if "REGRESSION_COMPLETE" in log or "All tests done" in log:
            break
        await asyncio.sleep(10)

else:
    # ── 경로 B: needs_test_name=True ────────────────────────────────
    # {test_name} 치환 필요 → test_list 순회, 테스트별 실행
    # info.cmd = e.g. "tcsh -c 'source ~/.cadence_setup.csh && ./run_sim_mcp {test_name}'"

    for test_name in test_list:
        # 방법 6-A: $env(TEST_NAME)으로 SHM 파일명 자동 구분
        await ssh_run(f"screen -S {session} -X stuff 'setenv TEST_NAME {test_name}\\n'")
        cmd = info.cmd.format(test_name=test_name)
        await ssh_run(f"screen -S {session} -X stuff '{cmd}\\n'")

        # 테스트별 완료 대기
        while True:
            log = await ssh_run(f"tail -3 /tmp/screen_{session}.log")
            if "COMPLETE" in log or "$finish" in log:
                break
            await asyncio.sleep(10)

        # 방법 6-B fallback (rename_dump=True)
        if rename_dump:
            await ssh_run(f"screen -S {session} -X stuff "
                          f"'mv dump/ci_top.shm dump/ci_top_{test_name}.shm\\n'")

# 완료: screen 정리 + 결과 파싱
await ssh_run(f"screen -X -S {session} quit")
result = await ssh_run(f"grep -E 'PASS|FAIL' /tmp/screen_{session}.log")
# → "N/M tests PASS, failures: [TEST_A, TEST_B]"
```

#### `extract_csv` 시그니처

```python
@mcp.tool()
async def extract_csv(
    shm_path: str,                   # SHM 파일 경로 (예: "dump/ci_top_TOP015.shm/ci_top.trn")
    signals: list[str],              # 추출할 신호 목록
    start_ns: int = 0,               # 시작 시각 (0 = 처음부터)
    end_ns: int = 0,                 # 종료 시각 (0 = 끝까지)
    output_path: str = "",           # CSV 저장 경로 (빈칸이면 /tmp/mcp_csv_{hash}.csv)
    missing_ok: bool = True,         # 신호 미존재 시 무시 (True) vs 에러 (False)
) -> str:
    """Extract signal waveform data from SHM dump to CSV via simvisdbutil.

    Internally runs:
      simvisdbutil <shm_path> -csv -output <output_path> -overwrite
          [-range <start_ns>:<end_ns>ns]
          [-missing]
          -sig <signal_1> -sig <signal_2> ...

    Returns: path to generated CSV file.
    Caches result in csv_cache keyed by (shm_path, frozenset(signals), start_ns, end_ns).
    """
```

#### simvisdbutil CLI 명령 구조

```bash
# csv_cache.py 내부 — simvisdbutil 래퍼
simvisdbutil {shm_path} \
    -csv \
    -output {output_path} \
    -overwrite \
    [-range {start_ns}:{end_ns}ns] \
    [-missing] \           # missing_ok=True 시 — 신호 부재 무시
    -sig {signal_1} \
    -sig {signal_2} \
    ...
```

**CSV 파일 형식 예시:**

```csv
time,top.hw...r_regAddr,top.hw...r_loopState
8318040,33,2
8318143,33,3
8318245,16,5
8318345,16,6
```

**"前後 N행" bisect 결과 포맷:**

```
예: bisect 조건 "r_regAddr == 0x10", context_lines=2

  8318040ns | regAddr=0x21 | loopState=2(CHK_ADR)    ← 전: 아직 0x21
  8318143ns | regAddr=0x21 | loopState=3(INC_ADR)    ← 전: INC_ADR 진입
★ 8318245ns | regAddr=0x10 | loopState=5(DATA_READY) ← 매칭! 0x10으로 변경
  8318345ns | regAddr=0x10 | loopState=6(SCL_WT_HI)  ← 후: 변경 유지
  8318443ns | regAddr=0x10 | loopState=6(SCL_WT_HI)  ← 후: 변경 유지

→ CHK_ADR(2)→INC_ADR(3) 전이에서 regAddr 변경됨을 즉시 확인
```

### Phase 3 신규 Tool

| Tool | 변경 | 파일 |
|------|------|------|
| `bisect_signal_dump(...)` | **신규** | server.py + csv_cache.py |
| `probe_add_signals(...)` | **신규** | server.py + mcp_bridge.tcl |
| `prepare_dump_scope(...)` | **신규** | server.py |
| `generate_debug_tcl(...)` | **신규** | server.py + debug_tools.py |
| `request_additional_signals(...)` | **신규** | server.py |

> **Note**: Plan §3-C `suggest_regression_signals`는 MCP tool이 아닌 **Claude workflow** — AI가 분석서·디버깅 이력을 읽고 직접 신호 집합을 추론. tool 등록 불필요.

#### `probe_add_signals` 시그니처 (G1 — §3-A Bridge mode용)

```python
@mcp.tool()
async def probe_add_signals(
    signals: list[str],              # 추가할 신호 경로 목록
    shm_path: str = "",              # dump 파일 경로 (default: 현재 세션 SHM)
    depth: str = "all",              # probe depth: "all", "1", "2", ...
) -> str:
    """Dynamically add probe signals to running SimVision bridge session.

    Wraps: probe -create -shm {signals} -depth {depth}
    Requires active bridge connection. Used before sim_run to capture additional signals.
    """
```

#### `bisect_signal_dump` 시그니처

```python
@mcp.tool()
async def bisect_signal_dump(
    shm_path: str,
    signal: str,                     # 추적할 신호
    op: str,                         # 조건: "eq", "ne", "gt", "lt", "change"
    value: str,                      # 비교값 (change일 때는 무시)
    start_ns: int = 0,
    end_ns: int = 0,                 # 0 → 끝까지
    context_signals: list[str] = [], # 결과와 함께 출력할 연관 신호
) -> str:
    """Binary search in SHM dump CSV. No simulator connection required.

    Returns: first match time_ns + context signal values ± N rows.
    If signal not in SHM → calls request_additional_signals().
    """
```

#### `request_additional_signals` 시그니처 (G3 — 신호 부재 orchestration)

```python
@mcp.tool()
async def request_additional_signals(
    missing_signals: list[str],            # SHM에 없는 신호 목록
    shm_path: str,                          # 분석 중인 SHM 경로
    bug_time_ns: int = 0,                   # 버그 시각 (checkpoint 탐색 기준, 0=auto)
    available_checkpoints: list[str] = [],  # 빈 리스트 → checkpoint_manager에서 자동 조회
) -> str:
    """Signal absence handler — presents capture mode options to user.

    When called by bisect_signal_dump (signal not in SHM), presents 3 options:
    [A]  Full re-run: sim_batch_run with expanded probe scope (run_sim_mcp 재실행)
    [A'] Restore run: _find_nearest_checkpoint(bug_time_ns) → restore + probe_add_signals
    [B]  Bridge live: connect_simulator + probe_add_signals + sim_run

    Auto-finds nearest checkpoint via _find_nearest_checkpoint(bug_time_ns).
    Executes chosen mode after user selection. Returns new shm_path on completion.
    """
```

### Phase 4 수정 Tool

| Tool | 변경 | 파일 |
|------|------|------|
| `save_checkpoint(name, checkpoint_dir)` | **수정** — persistent dir, registry | server.py + checkpoint_manager.py |
| `restore_checkpoint(name)` | **수정** — compile_hash 검증, stale breakpoint 정리 | server.py + checkpoint_manager.py |
| `cleanup_checkpoints(mode, dry_run)` | **신규** | server.py + checkpoint_manager.py |
| `bisect_restore_and_debug(...)` | **신규** | server.py |
| `bisect_signal(...)` | **수정** — Mode A: "Restore→probe_add_signals→run→dump→CSV" 패턴 적용 | server.py |

> **`bisect_signal` 변경 방침 (G4)**: 기존 `bisect_signal`은 Mode A 패턴으로 내부 변경. v2 API 시그니처 유지 (backward-compatible). 신규 `bisect_restore_and_debug`는 "restore 후 watchpoint 정지 유지" 전용 (keep_alive=True 포함).

#### `bisect_signal` Mode A 변경 — Tcl 구현 (do_bisect)

기존: restore → run + watchpoint → check → restore → run → ... (N회)
변경: restore → watchpoint → run (1회) → dump → CSV → in-memory binary search

```tcl
# mcp_bridge.tcl — Mode A bisect (Phase 4 변경)
proc ::mcp_bridge::do_bisect {channel args_str} {
    variable _checkpoint_dir

    # args_str 파싱: start_snapshot, signal, op, value, start_ns, end_ns
    # (실제 구현: JSON dict 또는 공백 구분 파싱)

    # 1. restore to start checkpoint
    if {[catch {restart $start_snapshot -path $_checkpoint_dir} err]} {
        ::mcp_bridge::send_error $channel "bisect restore failed: $err"
        return
    }

    # 2. stale breakpoint 정리 ($finish 방지)
    catch {stop -delete -all}

    # 3. watchpoint 설정 (end_ns 또는 조건)
    stop -create -condition "\[value $signal\] $op {$value}" -silent

    # 4. probe enable + run → watchpoint에서 stop (dump = start ~ watchpoint만)
    probe -enable *
    run [expr {$end_ns - $start_ns}]ns

    # 5. watchpoint 제거
    catch {stop -delete -all}

    # 6. simvisdbutil로 CSV 추출 (작은 dump → 빠른 추출)
    # → Python 쪽에서 extract_csv 호출 후 in-memory binary search
    ::mcp_bridge::send_ok $channel "bisect_dump_ready: $shm_path"
}
```

**Python 쪽 bisect_signal 변경 (server.py):**

```python
@mcp.tool()
async def bisect_signal(
    signal: str,
    op: str,         # "eq", "ne", "gt", "lt", "change"
    value: str,
    start_checkpoint: str,
    start_ns: int = 0,
    end_ns: int = 0,
    context_signals: list[str] = [],
) -> str:
    """Mode A bisect: restore → watchpoint → 1 run → dump → CSV → in-memory search.

    v2 API backward-compatible. Internally uses dump+CSV instead of N-iteration restore.
    """
    bridge = _get_bridge()

    # 1. restore + watchpoint + run (1회)
    await bridge.execute(f"__BISECT__ {start_checkpoint} {signal} {op} {value} {start_ns} {end_ns}")

    # 2. CSV 추출 + in-memory binary search
    shm = _get_current_shm()
    df = await csv_cache.extract(shm, [signal] + context_signals, start_ns, end_ns)

    # 3. binary search → 첫 매칭 시점
    match_idx = _binary_search(df, signal, op, value)
    context = df.iloc[max(0, match_idx-2) : match_idx+3]
    return _format_bisect_result(match_idx, context)
```

#### `sim_restart` 수정 — mcp_bridge.tcl Tcl 프로시저

```tcl
# mcp_bridge.tcl — init 시 초기 snapshot 저장 (restart 전용)
proc ::mcp_bridge::init_snapshot {} {
    variable _init_snapshot_dir "/tmp/mcp_init"
    file mkdir $_init_snapshot_dir
    catch {save -simulation mcp_init -path $_init_snapshot_dir -overwrite}
    # ※ 이 snapshot은 세션 내 restart 용도만. L1/L2와 별개.
    # bridge 종료 시 on_shutdown에서 정리.
}

# mcp_bridge.tcl — __RESTART__ meta command 핸들러
proc ::mcp_bridge::do_restart {channel} {
    variable _init_snapshot_dir

    # Option A: run -clean (권장)
    if {![catch {run -clean} err]} {
        ::mcp_bridge::send_ok $channel "restarted to time 0 (run -clean)"
        return
    }

    # Option B: snapshot restore fallback
    if {[info exists _init_snapshot_dir] && [file exists $_init_snapshot_dir]} {
        if {![catch {restart worklib.mcp_init:module -path $_init_snapshot_dir} err2]} {
            catch {stop -delete -all}   ;# stale breakpoint 정리
            ::mcp_bridge::send_ok $channel "restarted to time 0 (snapshot)"
            return
        }
    }

    ::mcp_bridge::send_error $channel "restart failed: $err; snapshot also failed: $err2"
}

# bridge 초기화 시 on_init에서 호출
proc ::mcp_bridge::on_init {} {
    # /tmp 임시 파일만 정리 (legacy 경로, bisect 잔여물)
    # L1/L2가 있는 {sim_dir}/checkpoints/ 는 건드리지 않는다
    foreach dir {/tmp/mcp_init} {
        if {[file exists $dir]} { file delete -force $dir }
    }
    ::mcp_bridge::init_snapshot
}
```

**server.py — sim_restart 변환:**

```python
@mcp.tool()
async def sim_restart() -> str:
    """Restart simulation to time 0.

    Sends __RESTART__ meta command to bridge.
    Bridge: run -clean first, snapshot restore fallback.
    """
    bridge = _get_bridge()
    return await bridge.send_meta("__RESTART__")
```

#### `bisect_restore_and_debug` 시그니처 (G2)

```python
@mcp.tool()
async def bisect_restore_and_debug(
    checkpoint_name: str,                  # restore 기준 checkpoint 이름
    probe_signals: list[str] = [],         # restore 후 동적으로 추가할 신호 목록
    watch_signal_path: str = "",           # watchpoint 신호 경로
    watch_op: str = "change",             # watchpoint 조건: "change" | "eq" | "ne" | "gt" | "lt"
    watch_value: str = "",                 # watchpoint 비교값 (change 시 무시)
    run_duration: str = "",               # watchpoint 대신 시간 기준 실행 (e.g. "1ms")
    shm_path: str = "",                    # dump 저장 경로 (default: dump/{checkpoint}_extra.shm)
    keep_alive: bool = True,              # True: watchpoint 정지 유지 (인터랙티브), False: disconnect
) -> str:
    """Restore checkpoint → add probes → run to watchpoint → stay for interactive debug.

    Workflow (G2 — probe_add_signals 연동):
    1. restore_checkpoint(checkpoint_name)
    2. probe_add_signals(probe_signals) if probe_signals  ← §3-A 동적 추가
    3. sim_run(until=watchpoint or run_duration) → SHM dump
    4. csv_cache → binary search → 결과 반환
    5. keep_alive=True:  watchpoint 시점 정지 유지 → deposit_value/sim_run 인터랙티브 가능
       keep_alive=False: disconnect_simulator()
    """
```

#### `cleanup_checkpoints` 시그니처

```python
@mcp.tool()
async def cleanup_checkpoints(
    mode: str = "list",       # "list" | "stale" | "project" | "all"
    project_root: str = "",   # "project" 모드 대상 (default: 현재 project_root)
    dry_run: bool = True,     # True: 목록만 출력 (기본), False: 실제 삭제 수행
) -> str:
    """Manage checkpoint lifecycle in mcp_registry.json and on-disk.

    Modes:
    - list:    전체 checkpoint 목록 출력 (name, level, compile_hash, size_mb, age)
    - stale:   compile_hash 불일치 checkpoint 삭제 대상 표시/삭제
    - project: 특정 project_root의 모든 checkpoint 삭제
    - all:     전체 checkpoint 삭제 (dry_run=False 필요, 확인 요청)

    dry_run=True (기본): 삭제 대상 목록만 반환, 파일/레지스트리 변경 없음.
    dry_run=False: 파일 삭제 + mcp_registry.json에서 항목 제거.
    """
```

### Phase 5 신규 Tool

| Tool | 변경 | 파일 |
|------|------|------|
| `attach_to_simvision(...)` | **신규** | server.py + debug_tools.py |
| `open_debug_view(...)` | **신규** | server.py + debug_tools.py |
| `compare_waveforms(...)` | **신규** | server.py + debug_tools.py |
| `export_debug_context(...)` | **신규** | server.py + debug_tools.py |

#### `attach_to_simvision` 시그니처 (M8 — 실행 중인 SimVision attach)

```python
@mcp.tool()
async def attach_to_simvision(
    port: int = 9876,         # TCP bridge 포트 (기본 9876)
    timeout: int = 10,        # 연결 대기 타임아웃 (초)
) -> str:
    """Attach to an already-running SimVision session via TCP bridge.

    Precondition: ~/.simvisionrc에 'source /path/to/mcp_bridge.tcl' 설정 필요.
    1회 설정: echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc
    어떤 방법으로든 SimVision을 시작하면 bridge가 자동으로 TCP 9876에서 리슨.

    open_debug_view와 차이:
    - open_debug_view: SimVision 새로 실행 (VNC 세션 기동)
    - attach_to_simvision: 이미 실행 중인 SimVision에 연결 (재시작 없이 attach)

    성공 시 기존 bridge 명령(probe_add_signals, deposit_value 등) 모두 사용 가능.
    실패 시 → "SimVision이 실행 중이 아니거나 .simvisionrc가 설정되지 않았습니다." 안내.
    """
    # 단순 TCP 연결 시도
    result = await ssh_run(f"nc -z localhost {port} && echo OK || echo FAIL")
    if "OK" in result:
        return await connect_simulator(host="localhost", port=port)
    else:
        return (
            f"SimVision bridge not found on port {port}.\n"
            "Setup: echo 'source /path/to/mcp_bridge.tcl' >> ~/.simvisionrc\n"
            "Then restart SimVision."
        )
```

#### `open_debug_view` 시그니처 (M8 — VNC + SimVision 자동 세팅)

```python
@mcp.tool()
async def open_debug_view(
    shm_path: str,                  # SHM 파일 경로
    signals: list[str],             # 추가할 신호 목록 (AI가 분석에서 식별)
    center_time_ns: int,            # 줌 중심 시각 (버그 시점)
    zoom_range_ns: int = 10000,     # 줌 범위 (±ns)
    cursor_time_ns: int = 0,        # 커서 위치 (0이면 center_time_ns)
    markers: list[dict] = [],       # 마커: [{"time_ns": T, "label": "bug here"}, ...]
    group_name: str = "AI_Debug",   # AI 추가 신호 전용 그룹 이름
    context_note: str = "",         # AI 분석 요약 (SimVision 콘솔에 출력)
    display: str = ":1",            # VNC DISPLAY 환경변수
) -> str:
    """Launch SimVision on VNC display with pre-configured debug view.

    Flow:
    1. VNC 실행 확인: ssh_run("vncserver -list 2>/dev/null | grep :1")
       없으면 → generate_debug_tcl fallback (§8-B)
    2. SimVision 실행: ssh_run("DISPLAY=:1 simvision {shm_path} &")
    3. Bridge ready 대기 (TCP 9876):
       ssh_run("for i in $(seq 1 15); do sleep 2; nc -z localhost 9876 && break; done")
    4. connect_simulator(host="localhost", port=9876)
    5. waveform_add_signals(signals, group_name) — 중복 skip
    6. waveform_zoom(start=center-range, end=center+range)
    7. cursor_set(time=center_time_ns)
    8. 마커: cursor set -time {T}ns -name "{label}"
    9. execute_tcl("puts {=== AI Debug Context: {context_note} ===}")
    Returns: "VNC viewer: localhost:5901 접속하면 SimVision GUI 확인 가능"
    """
```

**사용 예시:**

```python
await open_debug_view(
    shm_path="dump/ci_top_TOP015.shm",
    signals=[
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_regAddr",
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.r_startStopDetState",
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_loopState",
        "top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_rxData",
    ],
    center_time_ns=8318143,
    zoom_range_ns=50000,
    markers=[
        {"time_ns": 8300000, "label": "offset byte start"},
        {"time_ns": 8318143, "label": "★ BUG: CHK_ADR + NULL_DET"},
        {"time_ns": 8418633, "label": "regAddr should be 0x10"},
    ],
    context_note="STREAM_REG offset capture fails: startStopDetState=NULL_DET at CHK_ADR. regAddr=0x21 instead of 0x10.",
)
```

#### `generate_debug_tcl` 시그니처 + 생성 파일 예시

```python
@mcp.tool()
async def generate_debug_tcl(
    shm_path: str,
    signals: list[str],
    center_time_ns: int,
    zoom_range_ns: int = 10000,     # ±ns
    markers: list[dict] = [],       # [{"time_ns": T, "label": "..."}]
    context_note: str = "",
    output_path: str = "",          # 빈칸이면 {sim_dir}/scripts/debug_{timestamp}.tcl
) -> str:
    """Generate a SimVision Tcl script for offline debugging.

    User runs: simvision -input {output_path} {shm_path}
    Returns: path to generated Tcl script.
    """
```

**생성되는 Tcl 스크립트 예시:**

```tcl
# === Auto-generated debug script ===
# Bug: STREAM_REG offset capture fails at 8318143ns
# Generated by xcelium-mcp AI analysis

# Open waveform
database -open ../dump/ci_top_TOP015.shm -shm

# Add debug signals in AI_Debug group
waveform add -signals {
    top.hw.u_ext.u_ext_d_main.u_ext_i2cSlave.u_ext_i2cSerialInterface.r_regAddr
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
simvision -input debug_TOP015.tcl dump/ci_top_TOP015.shm
```

#### `export_debug_context` 시그니처 + 생성 문서 예시

```python
@mcp.tool()
async def export_debug_context(
    test_name: str,
    bug_description: str,           # 1줄 요약
    root_cause: str,                # 근본 원인 추정
    evidence: list[dict],           # CSV 증거: [{"time_ns": T, "signal": S, "value": V, "expected": E, "meaning": M}]
    related_code: list[dict],       # 관련 코드: [{"file": F, "line": L, "snippet": S}]
    signals_to_check: list[str],    # 사용자가 확인할 신호 목록
    suggested_fix: str = "",        # 수정 제안
    output_path: str = "",          # 빈칸이면 debug_{test_name}.md
) -> str:
    """Export a human-readable debug context document (Markdown).

    Returns: path to generated .md file.
    """
```

**생성되는 문서 예시 (`debug_TOP015.md`):**

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

#### `compare_waveforms` 시그니처 + 출력 예시

```python
@mcp.tool()
async def compare_waveforms(
    shm_before: str,                # 수정 전 (또는 이상) SHM
    shm_after: str,                 # 수정 후 (또는 정상) SHM
    signals: list[str],             # 비교할 신호 목록
    time_range_ns: tuple = (0, 0),  # 비교 시간 범위 (0,0 = 전체)
    output_mode: str = "csv_diff",  # "simvision" (GUI) | "csv_diff" (텍스트)
) -> str:
    """Compare two SHM dumps.

    csv_diff mode:
    1. extract_csv(shm_before, signals, ...) → df_before
    2. extract_csv(shm_after, signals, ...) → df_after
    3. 동일 시각의 값 비교 → diff_table 생성
    4. 변경된 신호 목록 + 첫 변화 시점 반환

    simvision mode:
    - VNC에서 두 SHM 동시 오픈 + 신호 나란히 배치
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

Result: 1 signal changed, 1 signal unchanged.
First change: 8318245ns at r_regAddr.
```

---

## 5. Phase 구현 계획

### Phase 1: Foundation (즉시 안정화)

**목표**: 현재 매 세션마다 발생하는 에러 제거, Script Discovery 기반 마련

**구현 항목**:

| # | 항목 | 파일 | Plan § |
|---|------|------|--------|
| P1-1 | `execute_tcl` tool 신규 (`__EXECUTE_TCL__` meta command) | server.py, mcp_bridge.tcl | §9 |
| P1-2 | `sim_restart` → `__RESTART__` meta command 변환 | server.py, mcp_bridge.tcl | §1 |
| P1-3 | `do_restart`: `run -clean` 시도 → 실패 시 init_snapshot restore fallback | mcp_bridge.tcl | §1 |
| P1-4 | bridge 초기화 시 `/tmp/mcp_init` snapshot 자동 저장 | mcp_bridge.tcl (init) | §1 |
| P1-5 | `_discover_sim_dir`: 이름 패턴 + content 검증 2단계 탐지 | sim_runner.py | §7 |
| P1-6 | `_analyze_tb_type`: uvm / ncsim_legacy / mixed / unknown 판별 | sim_runner.py | §7 |
| P1-7 | `_auto_detect_runner`: Makefile→shell→xrun→python→user 5단계 탐지 | sim_runner.py | §7 |
| P1-8 | `_ask_user_runner`: candidates=[] 또는 ambiguous 시 직접 입력 요청 | sim_runner.py | §7 |
| P1-9 | `mcp_registry.json` 저장·로드: `config_file` 포인터 + `checkpoints[]`만 관리 (runner 저장 안 함) | sim_runner.py | §7 |
| P1-10 | `.mcp_sim_config.json` 존재하면 탐지 생략 후 로드. 없으면 탐지 후 `.mcp_sim_config.json` 생성 → registry에 포인터 기록 | sim_runner.py | §7 |
| P1-11 | `_detect_shell_and_env`: login_shell($SHELL) + script_shell(shebang) 탐지 | sim_runner.py | §3.5 |
| P1-12 | `_detect_eda_env`: login shell 직접 테스트 → env 파일 탐색·검증 → 사용자 입력 fallback | sim_runner.py | §3.5 |
| P1-13 | `_detect_env_shell`: env 파일 shebang → 확장자 → 내용 패턴 → login_shell fallback | sim_runner.py | §3.5 |
| P1-14 | `_resolve_exec_cmd`: runtime에 script + shell 필드로 exec_cmd 도출 (파일에 저장 안 함) | sim_runner.py | §3.4 |

**Entry Criteria**: xcelium-mcp v2 소스 코드 준비됨

**Exit Criteria**:
- `execute_tcl("database -open ...")` 성공 — 기존 우회책(disconnect→nc→reconnect) 불필요
- `sim_restart()` 에러 없이 time 0 복귀
- cloud0 `~/git.clone/venezia-t0/` 에서 `_discover_sim_dir()` → ncsim/uvm 환경 자동 탐지
- `.mcp_sim_config.json` 생성 확인 (`exec_cmd` 포함), `mcp_registry.json`에 `config_file` 포인터만 기록 확인
- `which xrun` 실패 환경에서 env 파일 탐지 → `source_separately=true` + `exec_cmd` 정상 생성

**검증 방법**:

```bash
# cloud0에서
cd ~/git.clone/venezia-t0
python3 -c "
from sim_runner import _discover_sim_dir
envs = _discover_sim_dir('.')
for e in envs:
    print(e['sim_dir'], e['runner']['exec_cmd'])
"
# → .../ncsim  tcsh -c 'source ~/.cadence_setup.csh && ./run_sim_mcp {test_name}'
# → .../uvm    tcsh -lc 'make sim TEST={test_name}'
```

---

### Phase 2: CSV Infrastructure

**목표**: SHM → CSV 추출 파이프라인 구축, Batch sim 1-command 실행

**구현 항목**:

| # | 항목 | 파일 | Plan § |
|---|------|------|--------|
| P2-1 | `csv_cache.py` 모듈: simvisdbutil wrapper + pandas DataFrame 캐시 | csv_cache.py | §5 |
| P2-2 | `extract_csv` tool: 신호 목록 → CSV 파일 경로 반환 | server.py | §5 |
| P2-3 | simvisdbutil 명령 빌더: 신호 필터, 시간 범위, 출력 경로 | csv_cache.py | §5 |
| P2-4 | `sim_batch_run` tool: [A] 전체 실행 + L1+L2 자동 저장 | server.py, sim_runner.py | §6 |
| P2-5 | `sim_batch_run` [A'] 모드: from_checkpoint + probe_signals | server.py, sim_runner.py | §6 |
| P2-6 | SHM dump overwrite 방지: `$env(TEST_NAME)` Tcl 변수 주입 | sim_runner.py | §6 |
| P2-7 | `sim_batch_regression` tool: test_list 순차 실행 + per-test L2 저장 (Plan의 `test_names` → Design에서 `test_list`로 통일) | server.py, sim_runner.py | §6 |
| P2-8 | SSH screen 하이브리드 전략: 단일 test → ssh_run(timeout=120), regression → screen detach + log polling | sim_runner.py | §6 |
| P2-9 | regression 진행 상황 polling (`grep PASS/FAIL /tmp/regression_*.log`) | sim_runner.py | §6 |

**SSH screen 하이브리드 전략 (P2-8 상세)**:

```
단일 테스트 (sim_batch_run):
  ssh_run(exec_cmd, timeout=120)  ← 동기 대기, 결과 즉시 반환

회귀 테스트 (sim_batch_regression):
  1. screen -dmS mcp_regression_{timestamp} bash -c "{exec_cmd_all_tests} > /tmp/regression_{ts}.log 2>&1"
     → 즉시 반환 (detach)
  2. polling loop: ssh_run("grep -c 'PASS\|FAIL' /tmp/regression_{ts}.log")
     → 완료 조건: PASS+FAIL 수 == test_list 길이
  3. 완료 시 ssh_run("screen -X -S mcp_regression_{ts} quit")  ← screen 정리
  4. 최종 결과 파싱: "N/M tests PASS, failures: [...]"

screen 세션 재사용 검사:
  ssh_run("screen -ls | grep mcp_regression") → 기존 세션 있으면 재attach 또는 kill 선택
```

**Entry Criteria**: Phase 1 완료 (mcp_registry.json 생성 가능)

**Exit Criteria**:
- `sim_batch_run(test_name="TOP015")` → SHM 생성 + L1/L2 checkpoint 저장 확인
- `extract_csv(shm_path, signals=["top.hw.u_ext...r_state"])` → CSV 반환
- `sim_batch_regression(test_list=["TOP015", "TOP016"])` → 2개 테스트 순차 실행

**검증 방법**:

```python
# MCP client에서
result = await sim_batch_run("TOP015")
assert "L1_common_init saved" in result
assert "L2_TOP015_setup saved" in result

csv_data = await extract_csv(shm_path="dump/TOP015.shm", signals=["top.hw...r_state"])
assert len(csv_data) > 0
```

---

### Phase 3: Advanced Analysis

**목표**: dump 기반 bisect, probe scope 자동 조정, 사용자 디버깅 TCL 생성

**구현 항목**:

| # | 항목 | 파일 | Plan § |
|---|------|------|--------|
| P3-1 | `bisect_signal_dump` tool: in-memory binary search + context 반환 | server.py, csv_cache.py | §2-B |
| P3-2 | 신호 부재 시 `request_additional_signals` Hook: [A]/[A']/[B] 선택 + 자동 실행 orchestration | server.py | §2-B, §2-8 |
| P3-3 | `_find_nearest_checkpoint`: bug_time_ns 기준 최근접 checkpoint 탐색 | checkpoint_manager.py | §2-B |
| P3-4 | `probe_add_signals` tool: Bridge mode 신호 동적 추가 (`probe -create -shm`) | server.py, mcp_bridge.tcl | §3-A |
| P3-5 | `prepare_dump_scope` (3-B Batch): input Tcl 분석 → scope 확장 Tcl 생성 | server.py | §3-B |
| P3-6 | `sim_batch_run`/`regression`에 dump_signals 연동 | sim_runner.py | §3 |
| P3-7 | `generate_debug_tcl` tool: 오프라인 SimVision 디버깅 Tcl 스크립트 생성 | server.py, debug_tools.py | §8-B |

**Entry Criteria**: Phase 2 완료 (SHM + L1/L2 생성 가능)

**Exit Criteria**:
- `bisect_signal_dump(shm_path, "top...r_state", "eq", "3", 0, 5000000)` → 첫 매칭 시각 반환
- 신호 부재 시 `request_additional_signals` 호출 → 선택 후 [A]/[A']/[B] 자동 실행 확인
- `probe_add_signals(["top.hw...r_state"])` → bridge 세션에서 probe 추가 성공
- `generate_debug_tcl(bug_time_ns=1234567)` → 실행 가능한 `.tcl` 파일 생성 확인

---

### Phase 4: Bridge Enhancement

**목표**: save/restore 안정화, checkpoint 영속화·무효화·정리 자동화

**구현 항목**:

| # | 항목 | 파일 | Plan § |
|---|------|------|--------|
| P4-1 | `save_checkpoint` 수정: `{sim_dir}/checkpoints/` persistent 저장 + registry 등록 (project_root/sim_dir 계층) | server.py, checkpoint_manager.py | §4 |
| P4-2 | `restore_checkpoint` 수정: compile_hash 검증 → 불일치 시 거부 + 자동 삭제 + restore 후 stale breakpoint 정리 + `$finish` 방지 | server.py, checkpoint_manager.py | §4 |
| P4-3 | compile_hash 계산: RTL 소스 파일 mtime hash | checkpoint_manager.py | §4 |
| P4-4 | `sim_batch_run` recompile 감지 → 기존 checkpoint 자동 무효화. bridge init: L1/L2 자동 삭제 금지, `/tmp/mcp_init`은 세션 종료 시 정리 | sim_runner.py, mcp_bridge.tcl | §4 |
| P4-5 | `cleanup_checkpoints` tool: list/project/stale/all 모드, dry_run=True 기본 | server.py, checkpoint_manager.py | §4 |
| P4-6 | `bisect_restore_and_debug` tool: restore → `probe_add_signals` → sim_run → watchpoint 정지 (keep_alive 분기) | server.py | §4, §2-A |
| P4-7 | `bisect_signal` 수정: 내부를 "restore → `probe_add_signals` → run 1회 → dump → CSV → in-memory search" 패턴으로 변경. v2 API 시그니처 유지 | server.py | §4-5, §2-1 |
| P4-8 | TB 분석 캐시에 save point 시점 정보 추가 | checkpoint_manager.py | §4 |
| P4-9 | mcp_bridge.tcl Tcl 버그 수정: do_save/do_restore의 `$dir`/`$snapshot` 미정의 | mcp_bridge.tcl | §4 |
| P4-10 | send_ok/send_error 일관 적용 (channel 파라미터 통일) | mcp_bridge.tcl | §4 |

**P4-9 Tcl 버그 수정 상세 — do_save / do_restore 구현:**

```tcl
# mcp_bridge.tcl — do_save (4-A: save 전 환경 검증)
proc ::mcp_bridge::do_save {channel name} {
    variable _checkpoint_dir    ;# {sim_dir}/checkpoints/ (4-C에서 설정)

    # 1. 시뮬레이션 상태 확인 (stopped인지)
    set st [status]
    if {![string match "*stopped*" $st]} { catch {stop} }

    # 2. checkpoint 디렉토리 생성
    file mkdir $_checkpoint_dir

    # 3. save 실행
    if {[catch {save -simulation $name -path $_checkpoint_dir -overwrite} err]} {
        ::mcp_bridge::send_error $channel "save failed: $err"
        return
    }

    # 4. 성공 응답
    ::mcp_bridge::send_ok $channel "saved: $name at $_checkpoint_dir"
}

# mcp_bridge.tcl — do_restore (4-B: restore 후 $finish 방지 + stale breakpoint 정리)
proc ::mcp_bridge::do_restore {channel name} {
    variable _checkpoint_dir    ;# {sim_dir}/checkpoints/ (4-C에서 설정)

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

**compile_hash 계산 (checkpoint_manager.py):**

```python
import hashlib, os

def _compute_compile_hash(sim_dir: str) -> str:
    """inca/ 디렉토리 오브젝트 파일들의 최신 mtime 기반 MD5."""
    inca_dir = os.path.join(sim_dir, "inca")
    mtimes = []
    for root, dirs, files in os.walk(inca_dir):
        for f in sorted(files):
            path = os.path.join(root, f)
            try:
                mtimes.append(f"{path}:{os.path.getmtime(path)}")
            except OSError:
                pass
    content = "\n".join(sorted(mtimes))
    return hashlib.md5(content.encode()).hexdigest()[:8]
```

**자동 무효화 정책 구현 (server.py / checkpoint_manager.py):**

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
    return (
        f"ERROR: Checkpoint invalid after recompile. "
        f"Stale checkpoints removed (was: {manifest['compile_hash']}, now: {current_hash}). "
        f"Re-run sim_batch_run to create new checkpoints."
    )
```

**Entry Criteria**: Phase 2 완료

**Exit Criteria**:
- `save_checkpoint("L1_common_init")` → `{sim_dir}/checkpoints/` 저장 + mcp_registry.json에 project_root/sim_dir 계층으로 등록 확인
- `restore_checkpoint("L1_common_init")` → compile_hash 일치 시 성공, stale breakpoint 정리 확인
- RTL 수정 후 `sim_batch_run` → 기존 L1/L2 자동 무효화 확인 (L1/L2는 삭제 안 됨, bridge init 시도 방지)
- `cleanup_checkpoints(mode="stale", dry_run=True)` → 삭제 대상 목록만 출력
- `bisect_signal(...)` → Mode A 패턴(dump→CSV) 내부 동작, v2 시그니처 호환 확인

---

### Phase 5: UI / Visual

**목표**: SimVision VNC 자동 세팅, AI 분석 → 사용자 디버깅 원클릭 전환

**구현 항목**:

| # | 항목 | 파일 | Plan § |
|---|------|------|--------|
| P5-1 | `open_debug_view` tool: VNC 세션 감지 → SimVision 실행 → bridge 자동 세팅 | server.py, debug_tools.py | §8-A |
| P5-2 | waveform_add_signals: AI_Debug 그룹 자동 생성 + 전체 waveform 중복 skip | mcp_bridge.tcl | §8-A |
| P5-3 | `attach_to_simvision` tool: `.simvisionrc` 탐색 → 실행 중인 SimVision bridge 연결 | server.py, debug_tools.py | §8-A |
| P5-4 | `compare_waveforms` tool: 정상/이상 SHM → CSV 추출 → 차이점 요약 | server.py, debug_tools.py | §8-D |
| P5-5 | `export_debug_context` tool: AI 분석 결과 → Markdown 리포트 생성 | server.py, debug_tools.py | §8-C |

**Entry Criteria**: Phase 4 완료 (checkpoint 안정화)

**Exit Criteria**:
- VNC 세션에서 `open_debug_view(test_name="TOP015")` → SimVision 열림 + AI_Debug 그룹에 신호 추가 확인
- `compare_waveforms(normal_shm, fail_shm, signals=[...])` → 차이점 시각 목록 반환
- `export_debug_context(...)` → `debug_report.md` 생성 확인

---

## 6. 파일 구조

구현 위치: `Todoc/fpga/xcelium-mcp/`

기존 구조(`src/xcelium_mcp/` 패키지, `tcl/`, `tests/`)를 유지하고 신규 모듈을 추가한다.

```
xcelium-mcp/
├─ pyproject.toml                        ← 기존 (dependencies 추가 가능)
├─ CLAUDE.md                             ← 기존
├─ README.md                             ← 기존
├─ doc/
│   └─ simvision-integration-test.md    ← 기존
│
├─ src/
│   └─ xcelium_mcp/
│       ├─ __init__.py                   ← 기존
│       ├─ server.py                     ← 기존 (변경: execute_tcl, sim_batch_*, probe_add_signals 추가)
│       ├─ tcl_bridge.py                 ← 기존 (변경: execute_tcl meta command 라우팅)
│       ├─ screenshot.py                 ← 기존 (변경 없음)
│       ├─ sim_runner.py                 ← 신규: Script Discovery + Batch/Regression 실행 (§6, §7)
│       ├─ csv_cache.py                  ← 신규: simvisdbutil wrapper + in-memory 캐시 (§5)
│       ├─ checkpoint_manager.py         ← 신규: save/restore, registry, compile_hash, cleanup (§4)
│       └─ debug_tools.py               ← 신규: generate_debug_tcl, export_debug_context, open_debug_view (§8)
│
├─ tcl/
│   └─ mcp_bridge.tcl                   ← 기존 (변경: do_restart, do_execute_tcl, do_probe_add, do_waveform_add 수정)
│
└─ tests/
    ├─ test_bridge.py                    ← 기존
    ├─ test_phase1.py                    ← 신규: execute_tcl, sim_restart, Script Discovery 검증
    ├─ test_phase2.py                    ← 신규: csv_cache, sim_batch_run/regression 검증
    ├─ test_phase3.py                    ← 신규: bisect_signal_dump, probe_add_signals 검증
    ├─ test_phase4.py                    ← 신규: checkpoint save/restore, compile_hash 무효화 검증
    └─ test_phase5.py                    ← 신규: open_debug_view, waveform_add 검증
```

**런타임 상태 파일** (코드 외, cloud0 서버에 생성):

```
~/.xcelium_mcp/
└─ mcp_registry.json          ← 통합 레지스트리: project_root/sim_dir별 runner + checkpoints

{sim_dir}/
├─ .mcp_sim_config.json       ← Tier 1 고정 설정 (사용자 작성, 옵션)
├─ checkpoints/               ← L1/L2 checkpoint 영속 저장
│   ├─ L1_common_init/
│   └─ L2_{test_name}_setup/
└─ dump/                      ← SHM dump 파일
    ├─ {test_name}.shm
    └─ {test_name}_extra.shm  ← [A'] 추가 dump
```

**모듈별 책임**:

| 모듈 | 책임 | Phase |
|------|------|-------|
| `server.py` | `@mcp.tool()` 등록, 파라미터 라우팅 | 전 Phase |
| `tcl_bridge.py` | Python ↔ SimVision Tcl 통신 | 기존 + Phase 1 |
| `tcl/mcp_bridge.tcl` | Tcl meta command 처리 (do_restart, do_execute_tcl 등) | 기존 + Phase 1, 3 |
| `sim_runner.py` | Script Discovery, exec_cmd 생성, Batch/Regression 실행 | Phase 1, 2 |
| `csv_cache.py` | simvisdbutil 호출, CSV → DataFrame, in-memory 캐시 | Phase 2, 3 |
| `checkpoint_manager.py` | L1/L2 저장·복원, compile_hash, registry, cleanup | Phase 4 |
| `debug_tools.py` | Tcl 스크립트 생성, 분석 리포트, VNC 연동 | Phase 3, 5 |

---

## 7. Error Handling

| 에러 상황 | 처리 방법 | Tool |
|----------|----------|------|
| sim_restart 실패 | `run -clean` 시도 → 실패 시 structured error 반환 | sim_restart |
| checkpoint 없음 | "Checkpoint not found. Run sim_batch_run first." 반환 | restore_checkpoint |
| compile_hash 불일치 | checkpoint 자동 삭제 + "Recompile detected. Re-run needed." | restore_checkpoint |
| SHM에 신호 없음 | `request_additional_signals` 호출 → 사용자 선택 | bisect_signal_dump |
| sim_dir 탐지 실패 | 사용자에게 경로 직접 입력 요청 | _discover_sim_dir |
| runner 탐지 실패/모호 | 후보 목록 또는 직접 입력 요청 | _auto_detect_runner |
| EDA env 파일 탐지 실패 | 사용자에게 직접 입력 요청 (skip 허용, 경고 포함) | _detect_eda_env |
| env_shell 결정 불가 | login_shell fallback 사용 + 경고 로그 | _detect_env_shell |
| SSH timeout | nohup + polling 전환 | sim_batch_run (장기 실행) |
| VNC 없음 | "VNC session not found. Start VNC first." 반환 | open_debug_view |

---

## 8. Test Plan

### 8.1 Phase별 검증 범위

| Phase | 검증 방법 | 환경 |
|-------|---------|------|
| Phase 1 | Python unit test (mock SSH) + cloud0 실제 실행 | cloud0 ncsim |
| Phase 2 | sim_batch_run 실제 실행 + L1/L2 파일 존재 확인 | cloud0 ncsim |
| Phase 3 | 기존 SHM dump에서 bisect 실행 + CSV 검증 | cloud0 SHM |
| Phase 4 | compile hash 변경 시 checkpoint 무효화 확인 | cloud0 ncsim |
| Phase 5 | VNC 세션 수동 확인 + waveform 그룹 생성 확인 | cloud0 SimVision |

### 8.2 회귀 방지 체크리스트

- [ ] v2 기존 tool (get_signal_value, bisect_signal, deposit_value)이 여전히 동작
- [ ] Phase 1 tool이 Phase 2 미완료 상태에서도 독립 동작
- [ ] `sim_batch_run(from_checkpoint="")` → [A] 전체 실행 (default 동작)
- [ ] `sim_batch_run(from_checkpoint="L1_common_init")` → [A'] 모드
- [ ] mcp_registry.json 손상 시 graceful fallback (파일 삭제 후 재생성)

---

## 9. Implementation Order Summary

```
Phase 1 (Foundation)     → 배포 가능 단위
  P1-1 execute_tcl       ← 1순위, 즉각 효과
  P1-2~P1-4 sim_restart  ← 2순위
  P1-5~P1-14 script disc ← 3순위 (Phase 2 전제, shell/env 탐지 포함)

Phase 2 (CSV Infra)      → 배포 가능 단위
  P2-1~P2-3 csv_cache    ← Phase 3/4 공통 기반
  P2-4~P2-9 batch sim    ← Phase 3 전제

Phase 3 (Analysis)       → 배포 가능 단위
  P3-1~P3-3 bisect_dump  ← Phase 2 이후
  P3-4 probe_add_signals  ← Bridge mode 신호 동적 추가 (§3-A)
  P3-5~P3-6 probe scope (batch)
  P3-7 debug_tcl gen

Phase 4 (Bridge Enh)     → 배포 가능 단위
  P4-9~P4-10 Tcl 버그 수정  ← 최우선 (독립)
  P4-1~P4-5 checkpoint
  P4-6 bisect_restore_and_debug (probe_add_signals 의존)
  P4-7 bisect_signal Mode A 변경

Phase 5 (UI/Visual)      → 배포 가능 단위 (VNC 환경 필요)
  P5-2 waveform_add       ← VNC 없어도 가능
  P5-1 open_debug_view
  P5-4~P5-5 compare/export
```

---

## 10. Design Deviations from Plan

Plan 대비 의도적으로 변경한 항목 (개선 사항):

| Plan 명칭 | Design 명칭 | 변경 이유 |
|-----------|-------------|---------|
| `sim_registry.json` + `checkpoint_registry.json` (별도) | `mcp_registry.json` (통합) | 동일 계층(project_root/sim_dir)이므로 단일 파일로 관리 |
| `batch_cmd` | `exec_cmd` | runner 타입 무관하게 통일된 최종 실행 명령 |
| `test_names` (regression param) | `test_list` | Python 관례 list 명칭 통일 |
| `dump_signals` (sim_batch_run param) | `probe_signals` | [A'] 모드의 의미(probe 추가)를 정확히 반영 |
| `extract_waveform_csv` | `extract_csv` | csv_cache 모듈 내 함수명과 통일 |
| `op: "=="`, `"!="` (bisect) | `op: "eq"`, `"ne"`, `"change"` | string enum으로 통일 + "change" 모드 추가 |
| pandas 미사용 (순수 Python) | `pd.DataFrame` 사용 | 시간 범위 슬라이싱·binary search에 pandas가 명확히 유리 |
| `sim_registry.json`이 runner 저장 | `mcp_registry.json`은 `config_file` 포인터만 저장 | runner 설정은 `.mcp_sim_config.json`이 single source of truth — auto-generated + 사용자 편집 가능 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-30 | Initial draft — 5-Phase design, tool signatures, data models | HSLEE |
| 0.5 | 2026-03-30 | Phase별 Entry/Exit Criteria 추가, implementation order 정리 | HSLEE |
| 1.1 | 2026-03-30 | registry 통합(mcp_registry.json), shell/EDA env 탐지, 파일 구조 보강, Gap G1~G5 수정 | HSLEE |
| 1.2 | 2026-03-30 | Gap 수정: C1(request_additional_signals 시그니처), C2(cleanup_checkpoints 시그니처), C3(bisect_restore_and_debug 정식 시그니처), M5(SSH screen 상세), M7(Save Point 전략 3종), M8(attach_to_simvision), Design Deviations 표 추가 | HSLEE |
| 1.3 | 2026-03-30 | registry/config 중복 제거: `.mcp_sim_config.json`이 runner 설정 single source of truth (auto-generated), `mcp_registry.json`은 `config_file` 포인터 + checkpoints[]만 보유 | HSLEE |
| 1.1 | 2026-03-30 | §3 통합: sim_registry.json + checkpoint_registry.json → mcp_registry.json 단일 파일. project_root/sim_dir 계층에 runner + checkpoint_dir + checkpoints[] 통합. 전체 참조 일괄 교체. §3.x 재번호(3.3→3.2, 3.4→3.3, 3.5→3.4) | HSLEE |
| 1.0 | 2026-03-30 | §3.1 (구 sim_registry) project_root 계층 구조 도입. §3.2 (구 checkpoint_registry) project_root/sim_dir 계층 도입. 두 파일 구조 통일 | HSLEE |
| 0.9 | 2026-03-30 | §6 파일 구조: 실제 xcelium-mcp 폴더 구조 반영 — src/xcelium_mcp/ 패키지, tcl/, tests/ 기존 유지. 신규 모듈 4개(sim_runner, csv_cache, checkpoint_manager, debug_tools) src/xcelium_mcp/에 추가. 런타임 상태 파일(~/.xcelium_mcp/, {sim_dir}/checkpoints/, dump/) 구조 명시. §2.1 컴포넌트 경로 실제 경로로 수정 | HSLEE |
| 0.8 | 2026-03-30 | Gap 분석 반영: (G1) probe_add_signals tool 신규 추가(Phase 3, §3-A), (G2) bisect_restore_and_debug에서 probe_add_signals 호출 명시, (G3) request_additional_signals orchestration 명확화, (G4) bisect_signal Mode A 내부 변경 + v2 호환 명시(P4-7), (G5) suggest_regression_signals → Claude workflow로 재분류(tool 아님). Phase 4 번호 재조정(P4-7~P4-10) | HSLEE |
| 0.7 | 2026-03-30 | mcp_registry.json 구조 개편: flat list → project_root 키 + environments[sim_dir] 계층. path 필드 제거(checkpoint_dir+name으로 유도). mcp_registry.json과 계층 구조 일치 | HSLEE |
| 0.6 | 2026-03-30 | Shell/EDA env 탐지 설계 추가: script_shell/login_shell/env_shell 분리, batch_cmd 제거, exec_cmd 단일화. env 파일 탐지 4단계(login shell 테스트→파일 탐색→검증→사용자 입력). env_shell 탐지 우선순위(shebang→확장자→내용패턴→fallback). source_separately에 따른 login shell 필요 여부 분기. P1-11~P1-14 추가. §3.4 mcp_sim_config.json exec_cmd 기반으로 업데이트 | HSLEE |
| **2.0** | **2026-03-30** | **Plan 대비 구체성 강화 (Iteration 1): (1) §9 execute_tcl — bridge 프로토콜 request/response 형식, do_execute_tcl Tcl proc, before/after 비교 코드 추가. (2) §1 sim_restart — init_snapshot/do_restart/on_init Tcl proc 전문 + server.py 변환 코드 추가. (3) §6 sim_batch_run — 내부 흐름 9단계 상세화, dump_signals/timeout 파라미터 추가, SHM overwrite 방지 Tcl/bash 코드 추가. (4) §6 sim_batch_regression — 내부 screen 세션 Python 코드, extract_csv 시그니처 + simvisdbutil CLI 구조 + CSV 포맷 예시 + "前後 N행" bisect 포맷 추가. (5) §4 bisect_signal Mode A — do_bisect Tcl proc + server.py 변경 코드 추가. (6) §7 Script Discovery — _resolve_sim_runner/_auto_detect_runner/_ask_user_runner/_discover_sim_dir/_analyze_tb_type Python 구현 전문 추가. (7) §8 — open_debug_view/generate_debug_tcl/export_debug_context/compare_waveforms 시그니처 + 생성 파일 전체 예시 + attach_to_simvision 단순화(port만) 추가** | HSLEE |
