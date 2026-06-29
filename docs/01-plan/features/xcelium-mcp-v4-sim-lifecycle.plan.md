# Plan: xcelium-mcp v4 — Simulation Lifecycle Management

> **Feature**: sim_discover + sim_start 기반 통합 시뮬레이션 라이프사이클
>
> **Date**: 2026-03-31
> **Status**: Draft
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)
> **Predecessor**: xcelium-mcp v3 (100% complete, `docs/04-report/features/xcelium-mcp-v3-improvements-completion.report.md`)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | bridge mode 시뮬레이션 시작에 MCP tool이 없어 사용자가 수동으로 bash 스크립트를 실행해야 함. 환경(EDA PATH, shell, runner, bridge tcl) 탐지와 시뮬레이션 시작이 분리되어 있어 registry가 생성되지 않고, 이후 save_checkpoint 등 sim_dir 의존 tool이 모두 실패 |
| **Solution** | `sim_discover` (환경 탐지 + registry 등록) + `mcp_config` (registry 편집) + `sim_start` (registry 기반 bridge/batch 시뮬레이션 시작) 3개 tool 추가. 기존 `sim_batch_run`의 자체 탐지를 registry 기반으로 전환하여 환경 탐지 로직 통합 |
| **Function UX Effect** | `sim_discover` 1회 실행으로 환경 파악 완료 → `sim_start`로 bridge mode 자동 시작 → `connect_simulator` 후 모든 tool이 sim_dir 자동 참조. 수동 bash 명령 불필요 |
| **Core Value** | AI agent가 시뮬레이션 환경을 스스로 파악하고 시작할 수 있는 self-contained 워크플로우 구현. 사용자 개입 최소화, tool 간 환경 정보 공유 |

---

## 1. 배경: v3의 구조적 한계

### 1.1 현재 워크플로우의 문제

v3에서 bridge mode 시뮬레이션은 아래 과정을 거친다:

```
[사용자/AI가 수동으로]
  ssh_run("nohup bash run_sim_mcp > /tmp/log &")   ← 환경/경로를 직접 알아야 함
  ssh_run("... bridge ready 확인 ...")
  connect_simulator()                               ← sim_dir 정보 없음
  save_checkpoint()                                 ← sim_dir 없어서 실패!
```

**문제 1**: bridge mode 시작에 MCP tool이 없음 — AI가 bash 명령을 직접 구성해야 함
**문제 2**: `connect_simulator`는 순수 TCP 연결만 하므로 sim_dir, EDA env 정보를 모름
**문제 3**: `sim_batch_run`에만 환경 탐지 로직이 있고 bridge mode와 공유되지 않음
**문제 4**: registry (`mcp_registry.json`)가 batch 경로에서만 생성됨

### 1.2 batch mode와 bridge mode 비교

| 항목 | `sim_batch_run` (batch) | bridge mode (현재) |
|------|------------------------|-------------------|
| 환경 탐지 | `_load_or_detect_runner()` 자동 | 없음 (수동) |
| registry 생성 | `_update_registry_env()` 호출 | 없음 |
| 시뮬레이션 시작 | `_run_batch_single()` 내부 | 사용자 수동 bash |
| bridge 연결 | 불필요 (실행→종료) | `connect_simulator` 별도 |
| sim_dir 전파 | registry 경유 | 없음 → tool 실패 |

---

## 2. 목표

1. **`sim_discover` tool**: 시뮬레이션 환경을 탐지하고 registry에 등록
2. **`sim_start` tool**: registry 기반으로 bridge mode 또는 batch mode 시뮬레이션 시작
3. **Registry 통합**: bridge/batch 모두 동일한 registry를 사용하도록 환경 탐지 로직 일원화
4. **환경 탐지 일원화**: 기존 tool(`sim_batch_run` 등)의 동작은 그대로 유지하되, 기존에 각 tool에 분산 구현되었던 모든 환경 탐지 로직을 `sim_discover`를 활용하도록 수정하여, 향후 탐지 로직 수정 시 `sim_discover` 한 곳만 변경하면 전체에 일관되게 반영되도록 모듈화

---

## 3. 설계

### 3.1 `sim_discover` — 환경 탐지 + registry 등록

**MCP Tool Signature:**
```python
async def sim_discover(
    sim_dir: str = "",           # 명시적 sim_dir. 비어있으면 자동 탐색
    force: bool = False,         # True면 기존 registry 무시하고 재탐지
) -> str:
```

**탐지 항목 및 순서:**

| # | 항목 | 탐지 방법 | 저장 위치 |
|---|------|----------|----------|
| D-1 | sim_dir | `_discover_sim_dir()` 또는 명시적 인자 | registry.projects.{root}.environments |
| D-2 | tb_type | `_analyze_tb_type()` — uvm/ncsim_legacy/sv_directed | .mcp_sim_config.json |
| D-3 | runner | `_auto_detect_runner()` — run_sim_mcp, Makefile 등 | .mcp_sim_config.json |
| D-4 | login_shell | `$SHELL` 확인 | .mcp_sim_config.json |
| D-5 | EDA env | `_detect_eda_env()` — eda.env, .cshrc 등 | .mcp_sim_config.json |
| D-6 | mcp_bridge.tcl 위치 | xcelium-mcp 설치 경로에서 탐지 (`{pkg_root}/tcl/mcp_bridge.tcl`). 프로젝트별 copy 금지 — 설치된 원본 1곳만 참조하여 업데이트 즉시 반영 | .mcp_sim_config.json (신규) |
| D-7 | setup tcl 스크립트 | `setup*.tcl` 패턴으로 시뮬레이션 설정 tcl 검색 (MCP 전용이 아닌 범용) | .mcp_sim_config.json (신규) |
| D-8 | EDA tool 경로 (simvisdbutil, xmsim 등) | D-5 EDA env source 후 `which`로 일괄 resolve — 동일 Xcelium 설치본 보장. 개별 탐지 없음 | .mcp_sim_config.json (신규) |
| D-9 | bridge port | setup tcl에서 `9876` 파싱 또는 기본값 | .mcp_sim_config.json (신규) |
| D-10 | legacy run script bridge 호환 | run script에서 xmsim `-input` 인자를 `MCP_INPUT_TCL` 환경변수로 override 가능하도록 1줄 수정 적용/안내 | run script (1줄 수정) |

**mcp_bridge.tcl 탐지 (D-6) 상세:**

**원칙**: mcp_bridge.tcl은 xcelium-mcp 패키지 설치 경로의 원본 1곳만 참조한다. 프로젝트별로 copy하지 않는다. 이를 통해 `git pull` 한 번으로 모든 프로젝트에 최신 Tcl이 반영된다.

```
검색 순서:
1. xcelium-mcp Python 패키지 경로에서 탐지:
   python -c "import xcelium_mcp; print(xcelium_mcp.__file__)"
   → {pkg_root}/tcl/mcp_bridge.tcl  (editable install이면 git clone의 tcl/)
2. /opt/xcelium-mcp/tcl/mcp_bridge.tcl  (표준 설치 경로)
3. pip show xcelium-mcp → Location 필드에서 경로 추출
```

**기존 프로젝트별 copy 문제**: 현재 `~/git.clone/venezia-t0/.../scripts/mcp_bridge.tcl`에 copy가 있어 xcelium-mcp 업데이트가 반영되지 않음. v4에서는 setup_tcl이 설치 원본을 source하도록 변경.

**setup tcl 탐지 (D-7) 상세:**

기존 프로젝트의 시뮬레이션 setup tcl을 있는 그대로 활용한다. MCP 전용 setup tcl을 별도로 만들지 않는다.

```
검색 순서:
1. {sim_dir}/scripts/setup*.tcl        (범용 — setup_rtl.tcl, setup_gate.tcl 등)
2. runner script 내부에서 -input 인자 파싱
3. 여러 개 발견 시 사용자 선택 (AskUserQuestion)
```

**mcp_bridge.tcl의 setup tcl sourcing 구조 (신규):**

기존 방식은 `xmsim -input setup_rtl_mcp_batch.tcl`처럼 MCP 전용 setup tcl을 만들어야 했다.
v4에서는 mcp_bridge.tcl이 기존 프로젝트의 setup tcl을 source하는 구조로 변경:

```tcl
# mcp_bridge.tcl (v4 구조)
#
# 1. bridge 초기화 (TCP 9876 리스닝)
::mcp_bridge::init

# 2. 프로젝트의 기존 setup tcl을 source (probe, 설정 등 기존 환경 반영)
#    sim_discover가 탐지한 setup_tcl 경로를 환경변수로 전달
if {[info exists ::env(MCP_SETUP_TCL)] && $::env(MCP_SETUP_TCL) ne ""} {
    source $::env(MCP_SETUP_TCL)
}

# 3. bridge 대기 (클라이언트 연결 수신)
::mcp_bridge::wait_for_client
```

**장점:**
- 기존 `setup_rtl.tcl` 등을 수정 없이 그대로 사용
- MCP 전용 setup tcl (setup_rtl_mcp_batch.tcl) 유지보수 불필요
- 새 프로젝트에서도 기존 setup tcl만 있으면 바로 bridge mode 사용 가능
- `sim_start`가 `MCP_SETUP_TCL` 환경변수를 설정하여 연결

**legacy run script bridge 호환 (D-10) 상세:**

기존 legacy run script에는 mcp_bridge.tcl이 없다. v4에서는 run script의 xmsim `-input` 인자를 환경변수로 override할 수 있도록 **1줄만 수정**하여 bridge mode를 지원한다.

```bash
# 변경 전 (legacy run script 내부):
xmsim -input scripts/setup_rtl.tcl -log logs/ncsim.log top

# 변경 후 (1줄 수정 — 환경변수 없으면 기존과 동일):
xmsim -input ${MCP_INPUT_TCL:-scripts/setup_rtl.tcl} -log logs/ncsim.log top
```

**사용:**
```bash
run_sim TOP015                          # legacy 그대로 (기본값 적용)
MCP_INPUT_TCL=/opt/xcelium-mcp/tcl/mcp_bridge.tcl run_sim TOP015   # bridge mode
```

**`sim_discover` D-10 처리:**

| 상황 | 동작 |
|------|------|
| run script에 이미 `MCP_INPUT_TCL` 있음 | bridge 호환 확인, skip |
| run script에 `xmsim -input` 하드코딩 | 수정 내용 안내 + 사용자 승인 후 자동 적용 (sed 1줄) |
| run script 구조 파악 불가 | 수동 수정 가이드 출력 |

**장점:**
- 기존 인자 파싱에 영향 없음 (환경변수 방식)
- 어떤 형태의 run script든 적용 가능
- 환경변수 미설정 시 legacy 동작 100% 호환
- `sim_start`가 환경변수만 설정하면 기존 run script의 컴파일/환경 설정 단계가 그대로 유지됨

**출력 예시:**
```
Simulation environment discovered:
  sim_dir:        /users/hoseung.lee/git.clone/venezia-t0/design/top/sim/ncsim
  tb_type:        ncsim_legacy
  runner:         run_sim (MCP_INPUT_TCL 호환 확인 ✅)
  login_shell:    /bin/tcsh
  EDA env:        eda.env (source_separately=true)
  bridge_tcl:     /opt/xcelium-mcp/tcl/mcp_bridge.tcl (설치 원본)
  setup_tcls:     rtl=setup_rtl.tcl, gate=setup_gate.tcl, ams_rtl=setup_ams_rtl.tcl
  default_mode:   rtl
  simvisdbutil:   /apps/eda/cdns/XCELIUM2209/tools/bin/simvisdbutil
  bridge_port:    9876
  .simvisionrc:   updated ✅

Saved to: ~/.xcelium_mcp/mcp_registry.json
          /users/.../ncsim/.mcp_sim_config.json
```

### 3.2 `mcp_config` — registry/config 범용 편집 tool

**MCP Tool Signature:**
```python
async def mcp_config(
    action: str = "show",    # "show" | "get" | "set" | "delete"
    file: str = "config",    # "config" (.mcp_sim_config.json) | "registry" (mcp_registry.json)
    key: str = "",           # dot-notation: "runner.default_mode", "bridge.port", "eda_tools.simvisdbutil"
    value: str = "",         # JSON 자동 파싱: "9876" → int, "true" → bool, "\"text\"" → str
) -> str:
```

**동작:**

| action | 설명 |
|--------|------|
| `show` | 대상 파일 전체 내용을 JSON으로 표시 |
| `get` | dot-notation key로 값 조회. 예: `runner.setup_tcls.gate` → `"scripts/setup_gate.tcl"` |
| `set` | dot-notation key에 값 설정. 중간 경로 없으면 자동 생성 |
| `delete` | dot-notation key 삭제 |

**`file` 대상 결정:**
- `file="config"`: registry의 default sim_dir에 해당하는 `.mcp_sim_config.json`
- `file="registry"`: `~/.xcelium_mcp/mcp_registry.json`

**사용 예시:**
```
mcp_config()                                                    → config 전체 표시
mcp_config(file="registry")                                     → registry 전체 표시
mcp_config(action="get", key="runner.default_mode")             → "rtl"
mcp_config(action="set", key="runner.default_mode", value="gate")
mcp_config(action="set", key="runner.setup_tcls.debug", value="scripts/setup_debug.tcl")
mcp_config(action="set", key="bridge.port", value="9877")
mcp_config(action="delete", key="runner.setup_tcls.debug")
```

**역할 분리:**

| tool | 역할 | 빈도 |
|------|------|------|
| `sim_discover` | 전체 환경 탐지 + registry/config 생성 | 1회 (또는 환경 변경 시) |
| `mcp_config` | registry/config 조회 + 개별 항목 수정 | 수시 |
| `sim_start` | registry 읽기 → 시뮬레이션 시작 | 매 세션 |

---

### 3.3 `sim_start` — registry 기반 시뮬레이션 시작

**MCP Tool Signature:**
```python
async def sim_start(
    test_name: str,              # 필수 — 시뮬레이션 실행의 최소 조건 (컴파일 타임에 적용)
    sim_dir: str = "",           # 비어있으면 registry default
    mode: str = "bridge",        # "bridge" | "batch"
    sim_mode: str = "",          # "rtl" | "gate" | "ams_rtl" | "ams_gate". 비어있으면 default_mode
    run_duration: str = "",      # batch mode: 실행 시간 제한
    timeout: int = 120,          # 시작 대기 시간
) -> str:
```

**mode="bridge" 실행 순서:**

| # | 단계 | 구현 |
|---|------|------|
| S-1 | registry에서 환경 정보 로드 | `load_sim_config(sim_dir)` |
| S-2 | 기존 xmsim 프로세스 확인 | `pgrep -la xmsim` |
| S-3 | 기존 bridge ready 파일 정리 | `rm -f /tmp/mcp_bridge_ready_{port}` |
| S-4 | 기존 run script 경유 시작 | `MCP_INPUT_TCL` + `MCP_SETUP_TCL` 환경변수 설정 → 기존 run script 호출. run script의 컴파일/환경 단계 그대로 유지 |
| S-5 | bridge ready 대기 | `/tmp/mcp_bridge_ready_{port}` polling (timeout) |
| S-6 | 상태 확인 | 실행 로그 확인, registry는 읽기만 (쓰기는 sim_discover/mcp_config만) |
| S-7 | 결과 반환 | "Ready. Use connect_simulator(port={port}) to connect." |

**S-4 시작 명령 구성 — 기존 run script 경유:**

```bash
# sim_start(sim_mode="gate") 예시:
# 1. registry에서 setup_tcl 선택: runner.setup_tcls.gate → "scripts/setup_gate.tcl"
# 2. MCP_INPUT_TCL = mcp_bridge.tcl (설치 원본) → run script의 xmsim -input을 override
# 3. MCP_SETUP_TCL = setup_gate.tcl → mcp_bridge.tcl이 내부에서 source
# 4. 기존 run script를 그대로 호출 → 컴파일/환경 설정 단계 유지

nohup env \
  MCP_INPUT_TCL={bridge.tcl_path} \
  MCP_SETUP_TCL={sim_dir}/scripts/setup_gate.tcl \
  bash {sim_dir}/{runner_script} {test_name} \
  >& /tmp/sim_start_{port}.log &
```

**실행 흐름:**
```
sim_start(sim_mode="gate")
  → registry에서 runner_script, setup_tcls.gate, bridge.tcl_path 조회
  → 환경변수 설정:
      MCP_INPUT_TCL  = /opt/xcelium-mcp/tcl/mcp_bridge.tcl
      MCP_SETUP_TCL  = scripts/setup_gate.tcl
  → 기존 run_sim 호출 (컴파일, 환경 설정 등 그대로 실행)
      → run_sim 내부: xmsim -input ${MCP_INPUT_TCL:-setup_rtl.tcl} ...
      → mcp_bridge.tcl 로드됨
      → mcp_bridge.tcl 내부: source $env(MCP_SETUP_TCL) → setup_gate.tcl 반영
      → bridge TCP 9876 리스닝
```

**mode="batch" 실행:**
기존 `_run_batch_single()` 위임. registry에서 환경 로드 후 전달.

**구현 참조**: `sim_start`의 bridge/batch 모드 구현 시 기존 `sim_batch_run` + `_run_batch_single()`의 EDA env sourcing, screen 하이브리드 전략, 로그 polling 패턴을 참조할 것. 특히 SSH screen timeout 분기 (≤120s 직접 실행 / >120s screen+polling)는 `sim_start`에도 동일하게 적용.

**에러 처리:**

| 상황 | 대응 |
|------|------|
| registry 없음 | `sim_discover(sim_dir)` 자동 호출 → registry 생성 후 계속 진행 |
| 기존 xmsim 실행 중 | 포트 충돌 안내 + shutdown 제안 |
| bridge ready timeout | 로그 파일 tail 반환 |
| EDA env source 실패 | 에러 + sim_discover 재실행 안내 |

**shell redirect 제약 (필수):**

cloud0 login shell이 tcsh이므로, shell 명령 조립 시 아래 규칙을 반드시 준수:

| 금지 | 대체 | 이유 |
|------|------|------|
| `2>&1` | `>&` | tcsh가 `&1`을 파일명으로 해석 → 파일 `1` 생성 |
| `2>/dev/null` | 사용 불가 | tcsh `Ambiguous output redirect` 에러 |

**코드 수준 보호 (3층 방어):**

**1층 — `ssh_run()` 런타임 guard**: `2>&1` 패턴 감지 시 즉시 에러 발생
```python
async def ssh_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Args:
        log_file: redirect stdout+stderr to this file using '>& file' (tcsh-safe).
    """
    if "2>&1" in cmd:
        raise ValueError(
            "Do not use '2>&1' — tcsh interprets '&1' as filename. "
            "Use log_file parameter or _build_redirect() instead."
        )
    if log_file:
        cmd = f"{cmd} >& {log_file}"
    # ... 기존 로직
```

**2층 — `_build_redirect()` 헬퍼**: nohup 등 `ssh_run()` 외부에서 명령 조립 시 사용
```python
def _build_redirect(log_path: str) -> str:
    """Build shell redirect suffix safe for both bash and tcsh.

    NEVER use '2>&1' — tcsh interprets '&1' as filename, creating file '1'.
    Use '>& file' which works in both bash and tcsh.
    """
    return f">& {log_path}"
```

**3층 — SC-11 검증**: 구현 완료 후 전체 소스 grep으로 `2>&1` 0건 확인

**사용 가이드:**

| 상황 | 방법 |
|------|------|
| `ssh_run()` 호출 + redirect 필요 | `ssh_run(cmd, log_file="/tmp/out.log")` |
| nohup 등 복합 명령 조립 | `f"nohup {cmd} {_build_redirect(log)} &"` |
| redirect 불필요 | `ssh_run(cmd)` (그대로) |
| `2>&1` 직접 사용 | **금지** — ssh_run이 런타임 에러 발생 |

### 3.3 Registry 확장 스키마

**`.mcp_sim_config.json` v2:**

```json
{
  "version": 2,
  "runner": {
    "type": "shell",
    "script": "run_sim",
    "login_shell": "/bin/tcsh",
    "script_shell": "/bin/bash",
    "env_files": ["/users/.../ncsim/eda.env"],
    "env_shell": "tcsh",
    "source_separately": true,
    "setup_tcls": {
      "rtl": "scripts/setup_rtl.tcl",
      "gate": "scripts/setup_gate.tcl",
      "ams_rtl": "scripts/setup_ams_rtl.tcl",
      "ams_gate": "scripts/setup_ams_gate.tcl"
    },
    "default_mode": "rtl"
  },
  "bridge": {
    "tcl_path": "/opt/xcelium-mcp/tcl/mcp_bridge.tcl",
    "port": 9876
  },
  "eda_tools": {
    "simvisdbutil": "/apps/eda/cdns/XCELIUM2209/tools/bin/simvisdbutil",
    "xmsim": "/apps/eda/cdns/XCELIUM2209/tools/bin/xmsim",
    "xrun": "/apps/eda/cdns/XCELIUM2209/tools/bin/xrun"
  }
}
```

**스키마 설계 원칙:**
- **runner.setup_tcls**: 시뮬레이션 설정 tcl은 runner의 일부 — bridge/batch 모두 사용. mode별 분류하여 `sim_start(sim_mode="gate")`로 선택
- **bridge**: mcp_bridge.tcl 위치 + port만. setup tcl은 bridge가 아닌 runner 소관
- **bridge.tcl_path**: xcelium-mcp 설치 원본 경로. 프로젝트별 copy 없음

**setup_tcls mode 자동 분류 규칙 (D-7):**
- 파일명에 `gate` 포함 → `gate`
- 파일명에 `ams` + `gate` → `ams_gate`
- 파일명에 `ams` (gate 없음) → `ams_rtl`
- 그 외 → `rtl` (기본)
- 여러 개가 같은 mode에 매핑되면 사용자 선택

**`mcp_registry.json` — 변경 없음** (기존 스키마 유지, .mcp_sim_config.json이 상세 정보 담당)

### 3.3.1 `.simvisionrc` 자동 관리

SimVision GUI 모드에서도 mcp_bridge.tcl이 자동 로드되려면 `~/.simvisionrc`에 source 행이 필요하다.

**현재 문제**:
1. 수동으로 `echo 'source ...' >> ~/.simvisionrc` 실행 필요 (`.ai/knowledge/simvision-bridge-setup.md` 참조)
2. 프로젝트별 copy 경로가 하드코딩됨
3. xcelium-mcp 업데이트가 반영되지 않음

```tcl
# 기존 (~/.simvisionrc) — 수동 설정, 프로젝트별 copy 경로
source /users/hoseung.lee/git.clone/venezia-t0/.../scripts/mcp_bridge.tcl
```

**v4 변경**: `sim_discover`가 `.simvisionrc`도 자동 관리

```tcl
# v4 (~/.simvisionrc) — sim_discover가 자동 생성/업데이트, 설치 원본 참조
# [xcelium-mcp] managed by sim_discover — do not edit manually
source /opt/xcelium-mcp/tcl/mcp_bridge.tcl
```

**`sim_discover` D-6 단계에서 `.simvisionrc` 처리 (P1-9):**

| 상황 | 동작 |
|------|------|
| `~/.simvisionrc` 없음 | 생성 + source 행 추가 |
| 기존 `source .../mcp_bridge.tcl` 행 있음 | 설치 원본 경로로 교체 |
| `[xcelium-mcp] managed` 마커 있음 | 경로만 업데이트 |
| mcp_bridge 관련 행 없음 | 파일 끝에 source 행 추가 |

**효과:**
- `sim_discover` 1회 실행으로 batch xmsim + SimVision GUI 모두 동일한 원본 mcp_bridge.tcl 사용
- xcelium-mcp `git pull` 후 별도 `.simvisionrc` 수정 불필요
- 기존 수동 설정 가이드 (`.ai/knowledge/simvision-bridge-setup.md`) → `sim_discover` 자동화로 대체

### 3.4 기존 tool 개선 — `sim_discover` 일원화 원칙

**핵심 원칙**: 모든 환경 탐지 로직은 `sim_discover`에 집중한다. 기존 tool들은 자체 탐지를 제거하고, registry가 없으면 `sim_discover`를 호출하여 생성한다. 환경 탐지 수정 시 `sim_discover`만 변경하면 전체에 반영된다.

```
[sim_discover]  ← 환경 탐지 + registry 쓰기의 단일 진입점 (Single Source of Truth)
[mcp_config]    ← registry 개별 항목 수정 (유일한 다른 쓰기 경로)
    │
    ├─ mcp_registry.json          (프로젝트 레벨)  ← 쓰기: sim_discover, mcp_config만
    └─ .mcp_sim_config.json       (sim_dir 레벨)   ← 쓰기: sim_discover, mcp_config만
           │
           │  ── 독립 실행 가능 (없으면 sim_discover 자동 호출) ──
           ├─ sim_start              ← 없으면 sim_discover 호출
           ├─ sim_batch_run          ← 없으면 sim_discover 호출
           ├─ sim_batch_regression   ← 없으면 sim_discover 호출
           ├─ cleanup_checkpoints    ← 없으면 sim_discover 호출 (파일 관리, bridge 불필요)
           ├─ prepare_dump_scope     ← 없으면 sim_discover 호출 (Tcl 생성, bridge 불필요)
           ├─ request_additional_signals ← 없으면 sim_discover 호출 (옵션 안내, bridge 불필요)
           ├─ extract_csv            ← 없으면 sim_discover 호출 (SHM→CSV, bridge 불필요)
           ├─ bisect_signal_dump     ← 없으면 sim_discover 호출 (CSV 분석, bridge 불필요)
           ├─ compare_waveforms      ← 없으면 sim_discover 호출 (SHM 비교, bridge 불필요)
           ├─ generate_debug_tcl     ← 없으면 sim_discover 호출 (Tcl 생성, bridge 불필요)
           ├─ export_debug_context   ← 없으면 sim_discover 호출 (MD 생성, bridge 불필요)
           │
           │  ── bridge 필수 (sim_start 경유 → registry 이미 존재) ──
           ├─ save_checkpoint        ← 읽기만 (sim_start가 registry 보장)
           ├─ restore_checkpoint     ← 읽기만
           ├─ bisect_signal          ← 읽기만 (restore 기반)
           ├─ bisect_restore_and_debug ← 읽기만 (restore + watchpoint)
           │
           │  ── registry 접근 없음 ──
           └─ connect_simulator      ← 순수 TCP 연결만

※ v3의 _update_registry_env(), _load_or_detect_runner() Tier 2 자체 탐지 →
   sim_discover 내부로 흡수. 외부에서 직접 호출 제거.
```

| # | tool | 변경 내용 |
|---|------|----------|
| E-1 | `sim_batch_run` | 자체 `_load_or_detect_runner()` 제거. registry 확인 → 없으면 `sim_discover(sim_dir)` 자동 호출 → registry로 환경 로드. 탐지 로직 중복 완전 제거 |
| E-1b | `sim_batch_regression` | E-1과 동일 — 자체 환경 탐지 제거, registry 기반으로 전환 |
| E-2 | `connect_simulator` | `_auto_register_sim_dir()` 삭제. 환경 탐지는 `sim_discover`의 책임. registry 없이 수동 시작한 경우 `sim_discover` 실행을 안내 |
| E-3 | `extract_csv` / `bisect_signal_dump` | `simvisdbutil` 경로를 registry `eda_tools`에서 로드. 없으면 `sim_discover`로 registry 생성 후 재시도 |
| | **독립 실행 가능 — 없으면 sim_discover 자동 호출** | |
| E-4a | `cleanup_checkpoints` | registry 없으면 `sim_discover` 호출 (파일 관리, bridge 불필요) |
| E-4b | `prepare_dump_scope` | registry 없으면 `sim_discover` 호출 (Tcl 생성, bridge 불필요) |
| E-4c | `request_additional_signals` | registry 없으면 `sim_discover` 호출 (옵션 안내, bridge 불필요) |
| E-4d | `extract_csv` / `bisect_signal_dump` / `compare_waveforms` | registry 없으면 `sim_discover` 호출 (SHM 오프라인 분석, bridge 불필요) |
| E-4e | `generate_debug_tcl` / `export_debug_context` | registry 없으면 `sim_discover` 호출 (파일 생성, bridge 불필요) |
| | **bridge 필수 — sim_start가 registry 보장** | |
| E-4f | `save_checkpoint` / `restore_checkpoint` | sim_dir를 registry에서 읽기만 (sim_start 경유 → registry 존재 보장) |
| E-4g | `bisect_signal` / `bisect_restore_and_debug` | E-4f와 동일 (restore 기반, bridge 필수) |
| E-5 | `_load_or_detect_runner()` | Tier 2 자체 탐지 로직 제거. Tier 1 (config 파일) → 없으면 `sim_discover` 호출로 변경 |

---

## 4. 구현 항목 요약

### Phase 1: sim_discover (환경 탐지 tool)

| # | 항목 | 파일 |
|---|------|------|
| P1-1 | `sim_discover` MCP tool 등록 | server.py |
| P1-2 | mcp_bridge.tcl 탐지 로직 | sim_runner.py |
| P1-3 | setup tcl 스크립트 탐지 로직 | sim_runner.py |
| P1-4 | EDA tool 경로 일괄 resolve — D-5 EDA env source 후 `which simvisdbutil xmsim xrun` (기존 csv_cache.py 개별 탐지 제거) | sim_runner.py |
| P1-5 | bridge port 파싱 (setup tcl에서) | sim_runner.py |
| P1-6 | `.mcp_sim_config.json` v2 스키마 (bridge, eda_tools 섹션 추가) | sim_runner.py |
| P1-7 | 기존 `_load_or_detect_runner()` + `_update_registry_env()` → `sim_discover` 내부로 흡수. registry 쓰기는 `sim_discover`와 `mcp_config`에서만 수행 | sim_runner.py |
| P1-8 | setup_tcls mode 자동 분류 (rtl/gate/ams_rtl/ams_gate) | sim_runner.py |
| P1-9 | `~/.simvisionrc` 관리 — mcp_bridge.tcl source 경로를 설치 원본으로 업데이트 | sim_runner.py |
| P1-10 | legacy run script bridge 호환 — `MCP_INPUT_TCL` 환경변수 적용 (자동 sed 또는 가이드) | sim_runner.py |

### Phase 2: mcp_config + sim_start

| # | 항목 | 파일 |
|---|------|------|
| P2-1 | `mcp_config` MCP tool 등록 — show/get/set/delete | server.py |
| P2-2 | dot-notation key 파싱 + JSON value 자동 변환 | sim_runner.py |
| P2-3 | `sim_start` MCP tool 등록 | server.py |
| P2-4 | bridge mode 시작 — EDA env source + xmsim 실행 | sim_runner.py |
| P2-5 | bridge ready polling 로직 | sim_runner.py |
| P2-6 | 기존 xmsim 프로세스 확인/충돌 처리 | sim_runner.py |
| P2-7 | batch mode — 기존 `_run_batch_single()` 위임 | sim_runner.py |
| P2-8 | 로그 파일 경로 반환 (에러 시 디버깅용) | sim_runner.py |
| P2-9 | `_build_redirect()` 헬퍼 — `2>&1` 금지, `>&` 강제. 모든 shell 명령 조립에서 사용 | sim_runner.py |

### Phase 3: 기존 tool 일원화 (sim_discover 집중)

| # | 항목 | 파일 |
|---|------|------|
| P3-1 | `csv_cache.py` — `_resolve_simvisdbutil()` → registry `eda_tools` 참조, 없으면 `sim_discover` 호출 | csv_cache.py |
| P3-2 | `sim_batch_run` — 자체 `_load_or_detect_runner()` Tier 2 제거 → registry 없으면 `sim_discover` 호출 | server.py, sim_runner.py |
| P3-2b | `sim_batch_regression` — P3-2와 동일, 자체 환경 탐지 제거 → registry 기반 | server.py, sim_runner.py |
| P3-3 | `_load_or_detect_runner()` — Tier 2 자체 탐지 제거, config 없으면 `sim_discover` 위임 | sim_runner.py |
| P3-4 | `connect_simulator` — `_auto_register_sim_dir()` 삭제. 순수 TCP 연결만 수행 | server.py |

---

## 5. 전체 워크플로우

### 5.1 첫 사용 (환경 미파악)

```
AI: sim_discover(sim_dir="/users/.../ncsim")
    → runner, shell, EDA, bridge tcl, simvisdbutil 전부 탐지
    → mcp_registry.json + .mcp_sim_config.json 생성

AI: sim_start(mode="bridge")
    → registry에서 환경 로드 → xmsim 시작 → bridge ready 확인
    → "Ready. Use connect_simulator(port=9876)"

AI: connect_simulator()
AI: sim_run(duration="5ms")
AI: save_checkpoint()              ← sim_dir 자동 (registry)
AI: extract_csv(shm_path="...")    ← simvisdbutil 경로 자동 (registry)
AI: shutdown_simulator()
```

### 5.2 이후 사용 (환경 이미 파악)

```
AI: sim_start(mode="bridge")       ← registry에서 즉시 로드
    → xmsim 시작 → bridge ready
AI: connect_simulator()
AI: ...디버깅...
AI: shutdown_simulator()
```

### 5.3 batch mode

```
AI: sim_start(test_name="TOP015", mode="batch")
    → registry에서 환경 로드 → 시뮬레이션 실행→종료→SHM
AI: extract_csv(shm_path="...")
AI: bisect_signal_dump(...)
```

---

## 6. v3 hotfix와의 관계

v3 테스트 중 발견된 버그 3건은 이미 hotfix 커밋으로 수정 완료:

| # | 버그 | 커밋 | v4와의 관계 |
|---|------|------|-----------|
| 1 | `list_signals` SCMULT error | `96108f6` | 독립 (v4 무관) |
| 2 | `save_checkpoint` NOSNPL error | `96108f6` | 독립 (v4 무관) |
| 3 | `simvisdbutil` PATH 미설정 | `35adb0f` | P3-1에서 registry 기반으로 개선 |
| 4 | `connect_simulator` registry 자동 등록 | `0455d69` | P3-3에서 유지 (fallback) |

---

## 7. 성공 기준

| # | 기준 | 검증 방법 |
|---|------|----------|
| SC-1 | `sim_discover`로 ncsim 환경 자동 탐지 + registry 생성 | registry 없는 상태에서 실행 → 모든 항목 탐지 확인 |
| SC-2 | `sim_start(mode="bridge")`로 xmsim 자동 시작 + bridge ready | sim_start → connect_simulator 성공 |
| SC-3 | `save_checkpoint(sim_dir="")` 정상 동작 | registry에서 sim_dir 자동 로드 |
| SC-4 | `extract_csv` simvisdbutil 경로 registry에서 로드 | CSV 정상 생성 |
| SC-5 | 기존 `sim_batch_run` 호환 유지 | TOP015 batch 실행 성공 |
| SC-6 | registry 없는 상태에서 `sim_start` → `sim_discover` 자동 호출 → registry 생성 후 시뮬레이션 시작 | 빈 registry 상태에서 sim_start 1회 호출로 전체 워크플로우 완료 확인 |
| SC-7 | `mcp_config`로 설정 조회/수정 | show/get/set/delete 동작 확인 |
| **SC-8** | **탐지/수정 일원화 검증 — registry 쓰기 경로가 sim_discover + mcp_config만** | 전체 소스 grep: `save_registry\|write_text.*mcp_sim_config\|_update_registry_env\|write.*registry` → `sim_discover` 내부와 `mcp_config` 내부에서만 호출. 다른 tool(sim_start, sim_batch_run, connect_simulator, save_checkpoint, extract_csv 등)에서 registry 직접 쓰기 없음 확인 |
| **SC-9** | **환경 탐지 로직 일원화 검증 — 자체 탐지 코드가 sim_discover에만 존재** | 전체 소스 grep: `_detect_eda_env\|_detect_shell_and_env\|_auto_detect_runner\|_discover_sim_dir\|_analyze_tb_type` → `sim_discover` 구현 내부에서만 호출. 다른 tool에서 직접 호출 없음 확인 |
| **SC-10** | **connect_simulator에서 환경 탐지 코드 완전 제거** | `_auto_register_sim_dir` 함수 삭제 확인. connect_simulator가 순수 TCP 연결만 수행 |
| **SC-11** | **shell redirect 안전성 — `2>&1` 패턴 전체 코드에서 없음** | 전체 소스 grep: `2>&1` → 0건. 모든 redirect가 `_build_redirect()` 또는 `>&` 사용 확인 |

---

## 8. 파일 구조 (변경 예상)

```
src/xcelium_mcp/
├── server.py            # sim_discover, sim_start MCP tool 추가
├── sim_runner.py         # bridge 탐지 로직, sim_start_bridge() 추가
├── csv_cache.py          # _resolve_simvisdbutil() → registry 참조 우선
├── checkpoint_manager.py # 변경 없음
├── debug_tools.py        # 변경 없음
├── tcl_bridge.py         # 변경 없음
└── screenshot.py         # 변경 없음

tcl/
└── mcp_bridge.tcl        # 변경 없음
```

---

## 9. 추정 일정

| Phase | 내용 | 항목 수 |
|-------|------|:------:|
| Phase 1 | sim_discover | 10 |
| Phase 2 | mcp_config + sim_start | 9 |
| Phase 3 | 기존 tool 일원화 (sim_discover 집중) | 4 |
| **합계** | | **23** |
