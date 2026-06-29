# Design: xcelium-mcp v4 — Simulation Lifecycle Management

> **Feature**: sim_discover + mcp_config + sim_start 기반 통합 시뮬레이션 라이프사이클
>
> **Date**: 2026-03-31
> **Version**: v1.1 (2026-03-31: 테스트 검증 11건 반영)
> **Plan**: `docs/01-plan/features/xcelium-mcp-v4-sim-lifecycle.plan.md`
> **Project**: xcelium-mcp (`Todoc/fpga/xcelium-mcp/`)

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | bridge mode에 MCP tool 없음, 환경 탐지 로직 분산, registry 미생성으로 sim_dir 의존 tool 실패 |
| **Solution** | `sim_discover`(탐지 일원화) + `mcp_config`(설정 편집) + `sim_start`(시뮬레이션 시작) 3개 tool, 환경 탐지 Single Source of Truth |
| **Function UX Effect** | sim_discover 1회 → sim_start로 자동 시작 → 모든 tool이 registry 자동 참조 |
| **Core Value** | AI self-contained 워크플로우, 탐지 로직 일원화로 유지보수성 확보 |

---

## 1. 파일 구조 및 변경 범위

```
src/xcelium_mcp/
├── server.py              # [수정] sim_discover, mcp_config, sim_start tool 추가
│                          #        connect_simulator에서 _auto_register_sim_dir 삭제
│                          #        sim_batch_run/regression 자체 탐지 → sim_discover 위임
├── sim_runner.py          # [수정] 환경 탐지 함수를 sim_discover 전용으로 리팩터링
│                          #        _build_redirect(), ssh_run log_file 파라미터 추가
│                          #        bridge 탐지/시작 로직 추가
├── csv_cache.py           # [수정] _resolve_simvisdbutil() → registry eda_tools 참조
├── checkpoint_manager.py  # 변경 없음
├── debug_tools.py         # 변경 없음
├── tcl_bridge.py          # 변경 없음
└── screenshot.py          # 변경 없음

tcl/
└── mcp_bridge.tcl         # [수정] MCP_SETUP_TCL 환경변수로 기존 setup tcl sourcing
```

---

## 2. `.mcp_sim_config.json` v2 스키마 상세

```json
{
  "version": 2,
  "runner": {
    "type": "shell",
    "script": "run_sim",
    "login_shell": "/bin/tcsh",
    "script_shell": "/bin/bash",
    "env_files": ["/users/hoseung.lee/git.clone/venezia-t0/design/top/sim/ncsim/eda.env"],
    "env_shell": "tcsh",
    "source_separately": true,
    "args_format": "-test {test_name} --",
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

> **`runner.args_format`** (선택 필드): `_start_bridge`에서 test_name을 스크립트에 전달하는 방식.
> 기본값 `"-test {test_name} --"` (ncsim 관례). `mcp_config set runner.args_format "{test_name}"` 으로 오버라이드 가능.

### 2.1 v1 → v2 마이그레이션

기존 v1 config에 `bridge`, `eda_tools`, `runner.setup_tcls` 필드가 없는 경우:
- `sim_discover(force=True)` 실행 시 자동 업그레이드
- `version` 필드로 구분: `1` → v1 (기존), `2` → v2 (신규)
- v1 config는 읽기 호환 유지 (누락 필드는 런타임 fallback)

### 2.2 필드별 접근 패턴

| 필드 | 쓰기 | 읽기 |
|------|------|------|
| `runner.*` | sim_discover, mcp_config | sim_start, sim_batch_run, sim_batch_regression |
| `bridge.*` | sim_discover, mcp_config | sim_start (bridge mode) |
| `eda_tools.*` | sim_discover, mcp_config | extract_csv, bisect_signal_dump, compare_waveforms |
| `runner.setup_tcls.*` | sim_discover, mcp_config | sim_start (sim_mode 선택) |

---

## 3. Phase 1: `sim_discover` 상세 구현

### 3.1 MCP Tool — server.py

```python
@mcp.tool()
async def sim_discover(
    sim_dir: str = "",
    force: bool = False,
) -> str:
    """Discover simulation environment and register in mcp_registry.

    Detects: sim_dir, TB type, runner, shell/EDA env, mcp_bridge.tcl,
    setup TCLs, EDA tool paths, bridge port.
    Also updates ~/.simvisionrc and patches legacy run scripts.

    Args:
        sim_dir: Explicit simulation directory. Empty = auto-discover.
        force:   Re-detect even if registry already exists.
    """
    from xcelium_mcp.sim_runner import run_full_discovery
    return await run_full_discovery(sim_dir, force)
```

### 3.2 Core 함수 — sim_runner.py `run_full_discovery()`

```python
async def run_full_discovery(sim_dir: str = "", force: bool = False) -> str:
    """Main discovery orchestrator. Called by sim_discover MCP tool.

    Returns: human-readable discovery result summary.
    """
    # D-1: sim_dir 결정
    if not sim_dir:
        envs = await _discover_sim_dir()  # 기존 함수 재사용
        sim_dir = envs[0]["sim_dir"]

    # 기존 config 확인 (force=False 일 때)
    if not force:
        existing = await load_sim_config(sim_dir)
        if existing and existing.get("version", 1) >= 2:
            return f"Registry already exists for {sim_dir}. Use force=True to re-detect."

    # D-2: TB type
    tb_type = await _analyze_tb_type(sim_dir)

    # D-3: runner detection
    runner_info = await _auto_detect_runner(sim_dir)

    # D-4 + D-5: shell + EDA env
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
    project_root = r.strip()
    shell_env = await _detect_shell_and_env(sim_dir, script_name, project_root)

    # D-6: mcp_bridge.tcl (설치 원본에서 탐지)
    bridge_tcl = await _detect_bridge_tcl()

    # D-7: setup tcl 스크립트 탐지 + mode 분류
    setup_tcls = await _detect_setup_tcls(sim_dir)

    # D-8: EDA tool 경로 (D-5 EDA env source 후 which)
    eda_tools = await _resolve_eda_tools(shell_env)

    # D-9: bridge port
    bridge_port = await _detect_bridge_port(sim_dir, bridge_tcl)

    # D-10: legacy run script bridge 호환 패치
    patch_result = await _patch_legacy_run_script(sim_dir, runner_info)

    # Config v2 조립 + 저장
    config = {
        "version": 2,
        "runner": {
            "type": runner_info.get("runner", "shell"),
            "script": script_name,
            **shell_env,
            "setup_tcls": setup_tcls,
            "default_mode": _pick_default_mode(setup_tcls),
        },
        "bridge": {
            "tcl_path": bridge_tcl,
            "port": bridge_port,
        },
        "eda_tools": eda_tools,
    }
    await save_sim_config(sim_dir, config)

    # mcp_registry.json 등록
    _update_registry_from_config(sim_dir, tb_type, config)

    # D-6 부속: .simvisionrc 업데이트
    simvisionrc_result = await _update_simvisionrc(bridge_tcl)

    # 결과 문자열 조립
    return _format_discovery_result(sim_dir, tb_type, config, patch_result, simvisionrc_result)
```

### 3.2b `_detect_eda_env()` 구현 — EDA 환경 파일 탐지

```python
async def _detect_eda_env(sim_dir: str, project_root: str, login_shell: str) -> dict:
    """Detect EDA tool environment files.

    Step 1: Test if login shell already has xrun (no sourcing needed).
    Step 2: Search candidate env files by name pattern + EDA keyword grep.
    Step 3: Validate each candidate by sourcing and checking xrun.
    Step 4: If all fail, raise UserInputRequired.

    Returns: dict with env_files, env_shell, source_separately.
    """
    # Step 1: login shell direct test
    # Check for "/" to distinguish real path from "Command not found" stderr
    # (r.strip() and "/" in r.strip()) avoids false positive from error messages
    r = await ssh_run(_login_shell_cmd(login_shell, "which xrun"), timeout=10)
    if r.strip() and "/" in r.strip():
        return {"env_files": [], "env_shell": login_shell, "source_separately": False}

    # Step 2: candidate search
    # Use \( -type f -o -type l \) to include symlinks (env files are often symlinked)
    home = (await ssh_run("echo $HOME")).strip()
    search_specs = [
        (home,         r"\( -name '.cshrc' -o -name '.cadence' -o -name 'setup.csh' "
                       r"-o -name 'setup.sh' -o -name 'sourceme.*' -o -name '*eda*' \)"),
        (project_root, r"\( -name 'setup.*' -o -name 'sourceme.*' -o -name '*eda*' -o -name '*.env' \)"),
        (sim_dir,      r"\( -name 'setup.*' -o -name 'sourceme.*' -o -name '*eda*' -o -name '*.env' \)"),
        ("/etc/profile.d", r"\( -name 'cadence*' -o -name '*eda*' -o -name 'xcelium*' \)"),
    ]

    kw_grep = "XCELIUM_HOME|CDS_LIC_FILE|xrun|irun|setenv.*LIC"
    candidates: list[str] = []

    for search_dir, pat in search_specs:
        r = await ssh_run(
            f"find {search_dir} -maxdepth 1 \\( -type f -o -type l \\) {pat} 2>/dev/null"
        )
        for f in r.strip().splitlines():
            if not f:
                continue
            r2 = await ssh_run(f"grep -lE '{kw_grep}' {f} 2>/dev/null")
            if r2.strip():
                candidates.append(f)

    # Step 3: validate each candidate
    # No '2>/dev/null' inside csh/tcsh -c — causes "Ambiguous redirect" error in tcsh
    for candidate in candidates:
        env_shell = await _detect_env_shell(candidate, login_shell)
        r = await ssh_run(f"{env_shell} -c 'source {candidate} && which xrun'")
        if r.strip() and "/" in r.strip():
            return {
                "env_files": [candidate],
                "env_shell": env_shell,
                "source_separately": True,
            }

    # Step 4: not found
    raise UserInputRequired(
        "EDA env file not found. Enter path (or press Enter to skip):\n"
        "  Example: ~/.cadence_setup.csh\n"
        "  Example: /opt/cadence/etc/setup.csh"
    )
```

**Step 1 false-positive 방지**: `r.strip() and "/" in r.strip()` 조건으로 "Command not found" 같은 stderr 출력을 성공으로 오인하지 않음.

**Step 2 symlink 포함**: `-type f` 단독 대신 `\( -type f -o -type l \)` 사용 — EDA 환경 파일은 심볼릭 링크인 경우가 많음.

**Step 3 redirect 제거**: csh/tcsh 내부에서 `2>/dev/null`은 "Ambiguous redirect" 오류를 유발하므로 제거. `which xrun`의 stderr는 stdout과 합쳐져 반환되지만, `"/" in r.strip()` 검사로 오류 메시지를 필터링.

### 3.3 D-6: `_detect_bridge_tcl()` 구현

```python
async def _detect_bridge_tcl() -> str:
    """Find mcp_bridge.tcl from xcelium-mcp package installation path.

    Search order:
      1. Python package path: xcelium_mcp.__file__ → {parent}/tcl/mcp_bridge.tcl
      2. Standard install: /opt/xcelium-mcp/tcl/mcp_bridge.tcl
      3. pip show location fallback
    Raises RuntimeError if not found.
    """
    # 1. Package path (works for both regular and editable install)
    pkg_init = await ssh_run(
        "python3 -c \"import xcelium_mcp; print(xcelium_mcp.__file__)\" 2>/dev/null",
        timeout=10,
    )
    if pkg_init.strip():
        candidate = str(Path(pkg_init.strip()).parent.parent / "tcl" / "mcp_bridge.tcl")
        exists = await ssh_run(f"test -f {candidate} && echo YES || echo NO", timeout=5)
        if "YES" in exists:
            return candidate

    # 2. Standard path
    exists = await ssh_run("test -f /opt/xcelium-mcp/tcl/mcp_bridge.tcl && echo YES || echo NO", timeout=5)
    if "YES" in exists:
        return "/opt/xcelium-mcp/tcl/mcp_bridge.tcl"

    # 3. pip show fallback
    r = await ssh_run("pip3 show xcelium-mcp 2>/dev/null | grep Location", timeout=10)
    if r.strip():
        loc = r.strip().split(":", 1)[-1].strip()
        candidate = str(Path(loc).parent / "tcl" / "mcp_bridge.tcl")
        exists = await ssh_run(f"test -f {candidate} && echo YES || echo NO", timeout=5)
        if "YES" in exists:
            return candidate

    raise RuntimeError(
        "mcp_bridge.tcl not found. Verify xcelium-mcp is installed: pip show xcelium-mcp"
    )
```

### 3.4 D-7: `_detect_setup_tcls()` 구현

```python
async def _detect_setup_tcls(sim_dir: str) -> dict[str, str]:
    """Find setup*.tcl files and classify by simulation mode.

    Classification rules:
      - filename contains 'gate' + 'ams' → 'ams_gate'
      - filename contains 'ams' (no gate) → 'ams_rtl'
      - filename contains 'gate' (no ams) → 'gate'
      - otherwise → 'rtl'
    If multiple files map to same mode, first alphabetically wins.

    Returns: {"rtl": "scripts/setup_rtl.tcl", "gate": "scripts/setup_gate.tcl", ...}
    """
    r = await ssh_run(
        f"find {sim_dir}/scripts -maxdepth 1 -name 'setup*.tcl' 2>/dev/null | sort"
    )
    setup_tcls: dict[str, str] = {}
    for line in r.strip().splitlines():
        if not line.strip():
            continue
        fname = line.strip().split("/")[-1].lower()
        rel_path = f"scripts/{line.strip().split('/')[-1]}"

        if "ams" in fname and "gate" in fname:
            mode = "ams_gate"
        elif "ams" in fname:
            mode = "ams_rtl"
        elif "gate" in fname:
            mode = "gate"
        else:
            mode = "rtl"

        if mode not in setup_tcls:
            setup_tcls[mode] = rel_path

    return setup_tcls


def _pick_default_mode(setup_tcls: dict[str, str]) -> str:
    """Pick default sim mode. Priority: rtl > gate > ams_rtl > ams_gate."""
    for pref in ["rtl", "gate", "ams_rtl", "ams_gate"]:
        if pref in setup_tcls:
            return pref
    return next(iter(setup_tcls), "rtl")
```

### 3.5 D-8: `_resolve_eda_tools()` 구현

```python
async def _resolve_eda_tools(shell_env: dict) -> dict[str, str]:
    """Resolve EDA tool absolute paths by sourcing detected EDA env.

    All tools come from the same Xcelium installation — version consistency guaranteed.
    """
    tools = ["simvisdbutil", "xmsim", "xrun"]
    env_shell = shell_env.get("env_shell", shell_env.get("login_shell", "/bin/sh"))
    env_files = shell_env.get("env_files", [])

    if shell_env.get("source_separately") and env_files:
        source_cmd = " && ".join(f"source {f}" for f in env_files)
        which_cmd = " && ".join(f"which {t}" for t in tools)
        r = await ssh_run(
            f"{env_shell} -c '{source_cmd} && {which_cmd}' 2>/dev/null",
            timeout=15,
        )
    else:
        login_shell = shell_env.get("login_shell", "/bin/sh")
        # source_separately=False: EDA env comes via login shell rc files
        # Use _login_shell_cmd (not '-l -c') — tcsh 6.18 doesn't support '-l -c'
        which_cmd = " && ".join(f"which {t}" for t in tools)
        r = await ssh_run(_login_shell_cmd(login_shell, which_cmd), timeout=15)

    result: dict[str, str] = {}
    lines = [l.strip() for l in r.strip().splitlines() if l.strip() and "/" in l]
    for i, tool in enumerate(tools):
        if i < len(lines):
            result[tool] = lines[i]

    if "simvisdbutil" not in result:
        raise RuntimeError(
            "simvisdbutil not found after EDA env sourcing. "
            "Check eda.env or Xcelium installation."
        )

    return result
```

### 3.6 D-9: `_detect_bridge_port()` 구현

```python
async def _detect_bridge_port(sim_dir: str, bridge_tcl: str) -> int:
    """Parse bridge port from mcp_bridge.tcl. Default 9876."""
    r = await ssh_run(
        f"grep -oE 'variable port [0-9]+' {bridge_tcl} 2>/dev/null"
    )
    if r.strip():
        try:
            return int(r.strip().split()[-1])
        except ValueError:
            pass
    return 9876
```

### 3.7 D-10: `_patch_legacy_run_script()` 구현

```python
async def _patch_legacy_run_script(sim_dir: str, runner_info: dict) -> str:
    """Patch legacy run script to support MCP_INPUT_TCL env var override.

    Replaces: xmsim -input <hardcoded.tcl> ...
    With:     xmsim -input ${MCP_INPUT_TCL:-<hardcoded.tcl>} ...

    Returns: patch status string.
    """
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"

    # Check if file exists
    exists = await ssh_run(f"test -f {script_path} && echo YES || echo NO", timeout=5)
    if "YES" not in exists:
        return "run script not found"

    # Check if already patched
    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {script_path} 2>/dev/null")
    if r.strip() and r.strip() != "0":
        return "already patched"

    # Find xmsim -input line
    r = await ssh_run(f"grep -n 'xmsim.*-input' {script_path} 2>/dev/null")
    if not r.strip():
        return "no xmsim -input found — manual patch needed"

    # Extract the hardcoded tcl path
    # Pattern: xmsim ... -input <path.tcl> ...
    import re
    match = re.search(r'-input\s+(\S+)', r.strip())
    if not match:
        return "could not parse -input argument — manual patch needed"

    original_tcl = match.group(1)
    replacement = f'${{MCP_INPUT_TCL:-{original_tcl}}}'

    # Apply sed patch
    sed_cmd = (
        f"sed -i 's|-input {re.escape(original_tcl)}|-input {replacement}|' "
        f"{script_path}"
    )
    await ssh_run(sed_cmd, timeout=10)

    # Verify
    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {script_path} 2>/dev/null")
    if r.strip() and r.strip() != "0":
        return f"patched: -input {original_tcl} → -input {replacement}"
    return "patch failed — manual edit needed"
```

### 3.8 P1-9: `_update_simvisionrc()` 구현

```python
_SIMVISIONRC_MARKER = "# [xcelium-mcp] managed by sim_discover"

async def _update_simvisionrc(bridge_tcl: str) -> str:
    """Update ~/.simvisionrc to source mcp_bridge.tcl from install path.

    Returns status string.
    """
    home = (await ssh_run("echo $HOME")).strip()
    rc_path = f"{home}/.simvisionrc"
    source_line = f"source {bridge_tcl}"

    # Read existing
    content = await ssh_run(f"cat {rc_path} 2>/dev/null")

    if _SIMVISIONRC_MARKER in content:
        # Update existing managed block — replace the source line after marker
        lines = content.splitlines()
        new_lines = []
        skip_next = False
        for line in lines:
            if _SIMVISIONRC_MARKER in line:
                new_lines.append(_SIMVISIONRC_MARKER)
                new_lines.append(source_line)
                skip_next = True
                continue
            if skip_next and line.strip().startswith("source") and "mcp_bridge" in line:
                skip_next = False
                continue  # replaced by new source_line above
            skip_next = False
            new_lines.append(line)
        new_content = "\n".join(new_lines)
        # Write back using heredoc
        await ssh_run(f"cat > {rc_path} << 'SIMVISIONRC_EOF'\n{new_content}\nSIMVISIONRC_EOF")
        return "updated (marker found)"

    if "mcp_bridge" in content:
        # Replace existing unmanaged source line
        await ssh_run(
            f"sed -i '/mcp_bridge/c\\{_SIMVISIONRC_MARKER}\\n{source_line}' {rc_path}"
        )
        return "replaced unmanaged entry"

    # Append new
    managed_block = f"{_SIMVISIONRC_MARKER}\n{source_line}"
    await ssh_run(f"echo '\\n{managed_block}' >> {rc_path}")
    if not content.strip():
        return "created"
    return "added"
```

### 3.9 `_update_registry_from_config()` — registry 쓰기 (sim_discover 전용)

```python
def _update_registry_from_config(sim_dir: str, tb_type: str, config: dict) -> None:
    """Register sim environment in mcp_registry.json.

    This is the ONLY function that writes to mcp_registry.json
    (besides mcp_config tool). Replaces v3's _update_registry_env().
    """
    import subprocess
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=sim_dir
    )
    project_root = r.stdout.strip() if r.returncode == 0 else str(Path.home())

    registry = load_registry()
    projects = registry.setdefault("projects", {})
    project = projects.setdefault(project_root, {"environments": {}})
    envs = project.setdefault("environments", {})

    envs[sim_dir] = {
        "tb_type": tb_type,
        "is_default": len(envs) == 0 or envs.get(sim_dir, {}).get("is_default", False),
        "config_version": config.get("version", 2),
        "bridge_port": config.get("bridge", {}).get("port", 9876),
    }

    save_registry(registry)
```

### 3.10 `_format_discovery_result()` — 출력 포맷

```python
def _format_discovery_result(
    sim_dir: str, tb_type: str, config: dict,
    patch_result: str, simvisionrc_result: str,
) -> str:
    runner = config["runner"]
    bridge = config["bridge"]
    eda = config.get("eda_tools", {})
    setup_modes = ", ".join(f"{k}={v}" for k, v in runner.get("setup_tcls", {}).items())

    return (
        f"Simulation environment discovered:\n"
        f"  sim_dir:        {sim_dir}\n"
        f"  tb_type:        {tb_type}\n"
        f"  runner:         {runner.get('script', '?')} (MCP_INPUT_TCL {patch_result})\n"
        f"  login_shell:    {runner.get('login_shell', '?')}\n"
        f"  EDA env:        {', '.join(runner.get('env_files', []))}\n"
        f"  bridge_tcl:     {bridge.get('tcl_path', '?')} (install origin)\n"
        f"  setup_tcls:     {setup_modes}\n"
        f"  default_mode:   {runner.get('default_mode', 'rtl')}\n"
        f"  simvisdbutil:   {eda.get('simvisdbutil', '?')}\n"
        f"  bridge_port:    {bridge.get('port', 9876)}\n"
        f"  .simvisionrc:   {simvisionrc_result}\n"
        f"\nSaved to: {_REGISTRY_PATH}\n"
        f"          {sim_dir}/.mcp_sim_config.json"
    )
```

---

## 4. Phase 2: `mcp_config` 상세 구현

### 4.1 MCP Tool — server.py

```python
@mcp.tool()
async def mcp_config(
    action: str = "show",
    file: str = "config",
    key: str = "",
    value: str = "",
) -> str:
    """View or modify xcelium-mcp registry/config via dot-notation keys.

    Args:
        action: "show" (full dump), "get" (read key), "set" (write key), "delete" (remove key).
        file:   "config" (.mcp_sim_config.json of default sim_dir) or "registry" (mcp_registry.json).
        key:    Dot-notation path (e.g. "runner.default_mode", "bridge.port").
        value:  Value for 'set' action. Auto-parsed: "9876"→int, "true"→bool, quoted→str.
    """
    from xcelium_mcp.sim_runner import config_action
    return await config_action(action, file, key, value)
```

### 4.2 Core 함수 — sim_runner.py

```python
async def config_action(action: str, file: str, key: str, value: str) -> str:
    """Execute mcp_config action."""
    # Load target file
    if file == "registry":
        data = load_registry()
        path = _REGISTRY_PATH
    else:
        sim_dir = await _get_default_sim_dir()
        if not sim_dir:
            raise RuntimeError("No default sim_dir. Run sim_discover first.")
        cfg = await load_sim_config(sim_dir)
        if cfg is None:
            raise RuntimeError(f"No .mcp_sim_config.json in {sim_dir}. Run sim_discover first.")
        data = cfg
        path = Path(sim_dir) / ".mcp_sim_config.json"

    if action == "show":
        return json.dumps(data, indent=2)

    if action == "get":
        val = _dot_get(data, key)
        if val is _MISSING:
            return f"Key '{key}' not found"
        return json.dumps(val, indent=2) if isinstance(val, (dict, list)) else str(val)

    if action == "set":
        parsed = _parse_json_value(value)
        _dot_set(data, key, parsed)
        _write_json(path, data)
        return f"Set {key} = {json.dumps(parsed)}"

    if action == "delete":
        if _dot_delete(data, key):
            _write_json(path, data)
            return f"Deleted {key}"
        return f"Key '{key}' not found"

    return f"Unknown action: {action}"


# --- Dot-notation helpers ---

_MISSING = object()

def _dot_get(data: dict, key: str):
    """Traverse dict by dot-separated key. Returns _MISSING if not found."""
    parts = key.split(".")
    cur = data
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return _MISSING
    return cur


def _dot_set(data: dict, key: str, value) -> None:
    """Set value at dot-separated key, creating intermediate dicts as needed."""
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _dot_delete(data: dict, key: str) -> bool:
    """Delete key at dot-separated path. Returns True if deleted."""
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False
    if parts[-1] in cur:
        del cur[parts[-1]]
        return True
    return False


def _parse_json_value(value: str):
    """Parse value string to appropriate Python type.

    "9876" → 9876 (int)
    "true"/"false" → True/False (bool)
    "3.14" → 3.14 (float)
    Everything else → str
    """
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _write_json(path, data: dict) -> None:
    """Write JSON file. Works with both Path and str."""
    Path(str(path)).write_text(json.dumps(data, indent=2))
```

---

## 5. Phase 2: `sim_start` 상세 구현

### 5.1 MCP Tool — server.py

```python
@mcp.tool()
async def sim_start(
    test_name: str,
    sim_dir: str = "",
    mode: str = "bridge",
    sim_mode: str = "",
    run_duration: str = "",
    timeout: int = 120,
) -> str:
    """Start simulation using registry configuration.

    Args:
        test_name:    Required — test to run (applied at compile time).
        sim_dir:      Simulation dir. Empty = registry default.
        mode:         "bridge" (interactive, waits for connect_simulator) or "batch" (run to end).
        sim_mode:     "rtl"|"gate"|"ams_rtl"|"ams_gate". Empty = default_mode from config.
        run_duration: Batch mode only — limit sim time (e.g. "10ms").
        timeout:      Bridge mode — max seconds to wait for bridge ready.
    """
    from xcelium_mcp.sim_runner import start_simulation
    return await start_simulation(test_name, sim_dir, mode, sim_mode, run_duration, timeout)
```

### 5.2 Core 함수 — sim_runner.py `start_simulation()`

```python
async def start_simulation(
    test_name: str,
    sim_dir: str = "",
    mode: str = "bridge",
    sim_mode: str = "",
    run_duration: str = "",
    timeout: int = 120,
) -> str:
    """Start simulation. Registry없으면 sim_discover 자동 호출."""

    # S-1: registry 로드 (없으면 sim_discover 자동 호출)
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_dir:
        # Auto-discover
        result = await run_full_discovery(sim_dir)
        resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_dir:
            raise RuntimeError("sim_discover failed to create registry.")

    config = await load_sim_config(resolved_dir)
    if config is None:
        result = await run_full_discovery(resolved_dir)
        config = await load_sim_config(resolved_dir)
        if config is None:
            raise RuntimeError(f"sim_discover failed for {resolved_dir}")

    runner = config.get("runner", {})
    bridge = config.get("bridge", {})

    # sim_mode 결정
    effective_mode = sim_mode or runner.get("default_mode", "rtl")
    setup_tcls = runner.get("setup_tcls", {})
    if effective_mode not in setup_tcls:
        available = ", ".join(setup_tcls.keys())
        raise RuntimeError(f"sim_mode '{effective_mode}' not found. Available: {available}")

    setup_tcl = f"{resolved_dir}/{setup_tcls[effective_mode]}"

    if mode == "bridge":
        return await _start_bridge(
            resolved_dir, config, test_name, setup_tcl, effective_mode, timeout
        )
    elif mode == "batch":
        return await _start_batch(
            resolved_dir, config, test_name, setup_tcl, run_duration
        )
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'bridge' or 'batch'.")
```

### 5.3 `_start_bridge()` 구현

```python
async def _start_bridge(
    sim_dir: str,
    config: dict,
    test_name: str,
    setup_tcl: str,
    sim_mode: str,
    timeout: int,
) -> str:
    """Start simulation in bridge mode via legacy run script + env vars."""
    runner = config["runner"]
    bridge = config["bridge"]
    port = bridge.get("port", 9876)
    bridge_tcl = bridge.get("tcl_path", "")
    script = runner.get("script", "run_sim")

    # S-2: Check existing xmsim
    ps = await ssh_run("pgrep -la xmsim 2>/dev/null", timeout=5)
    if ps.strip():
        return (
            f"ERROR: xmsim already running:\n{ps.strip()}\n"
            f"Use shutdown_simulator or 'pkill -f xmsim' first."
        )

    # S-3: Clean stale ready file
    ready_file = f"/tmp/mcp_bridge_ready_{port}"
    await ssh_run(f"rm -f {ready_file}", timeout=5)

    # S-4: Start via run script with env vars
    # script_shell: use runner.script_shell (shebang-detected) or env_shell fallback
    # Never hardcode 'bash' — legacy scripts may be tcsh/csh
    script_shell = runner.get("script_shell", runner.get("env_shell", "/bin/sh"))
    # args_format: how test_name is passed to the script (default: ncsim convention)
    # Override via: mcp_config set runner.args_format "{test_name}"
    args_format = runner.get("args_format", "-test {test_name} --")
    test_args = args_format.format(test_name=test_name)
    log_file = f"/tmp/sim_start_{port}.log"

    # Pre-filter setup TCL to remove run/exit/finish/database-close for bridge mode
    # Uses POSIX sed with [[:space:]] character class (no \b — avoids GNU sed dependency)
    filtered_tcl = f"/tmp/mcp_setup_filtered_{port}.tcl"
    await ssh_run(
        f"sed '"
        f"/^[[:space:]]*run[[:space:]]*$/d; "       # 'run' alone
        f"/^[[:space:]]*run[[:space:]]/d; "          # 'run ...' with args
        f"/^[[:space:]]*exit[[:space:]]*$/d; "       # 'exit' alone
        f"/^[[:space:]]*exit[[:space:]]/d; "         # 'exit ...'
        f"/^[[:space:]]*finish[[:space:]]*$/d; "     # 'finish' alone
        f"/^[[:space:]]*finish[[:space:]]/d; "       # 'finish ...'
        f"/^[[:space:]]*database[[:space:]]*-close/d"  # 'database -close ...'
        f"' {setup_tcl} > {filtered_tcl}",
        timeout=10,
    )

    # Source EDA env before running script (xmvlog/xmsim need PATH)
    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", script_shell)
    login_shell = runner.get("login_shell", "/bin/sh")

    # Build inner command: setenv MCP vars → source EDA env → run script
    inner_parts = [
        f"setenv MCP_INPUT_TCL {bridge_tcl}",
        f"setenv MCP_SETUP_TCL {filtered_tcl}",
    ]
    if runner.get("source_separately") and env_files:
        # source_separately=True: EDA env from explicit env files
        for ef in env_files:
            inner_parts.append(f"source {ef}")
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = f"{env_shell} -c '{inner_cmd}'"
    else:
        # source_separately=False: EDA env comes via login shell rc files
        # Use _login_shell_cmd to source ~/.tcshrc + ~/.cshrc before running
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = _login_shell_cmd(login_shell, inner_cmd)

    # Wrap in subshell + nohup + < /dev/null to fully detach from asyncio PIPE
    # Subshell `(...)` ensures nohup job is reparented to init, not asyncio event loop
    cmd = (
        f"cd {sim_dir} && "
        f"(nohup {shell_cmd} "
        f"{_build_redirect(log_file)} < /dev/null &)"
    )
    await ssh_run(cmd, timeout=15)

    # S-5: Poll for bridge ready
    for i in range(timeout // 2):
        await asyncio.sleep(2)
        r = await ssh_run(f"test -f {ready_file} && echo READY || echo WAITING", timeout=5)
        if "READY" in r:
            return (
                f"Simulation started (bridge mode, {sim_mode}).\n"
                f"  test: {test_name}\n"
                f"  setup_tcl: {setup_tcl}\n"
                f"  port: {port}\n"
                f"  log: {log_file}\n\n"
                f"Ready. Use connect_simulator(port={port}) to connect."
            )

    # Timeout — return log tail
    log_tail = await ssh_run(f"tail -20 {log_file} 2>/dev/null", timeout=5)
    return f"ERROR: bridge not ready after {timeout}s.\nLog tail:\n{log_tail}"
```

**항목별 변경 요약:**

- **`script_shell`**: `bash` 하드코딩 제거 → `runner.script_shell` (shebang 탐지값) 또는 `env_shell` fallback. 레거시 스크립트가 tcsh인 경우를 처리.
- **`args_format`**: `runner.args_format` 필드로 test_name 전달 방식 추상화 (기본값 `"-test {test_name} --"`). `mcp_config set`으로 오버라이드 가능.
- **sed POSIX 패턴**: `\b` word boundary 제거 → `[[:space:]]` 패턴 사용. GNU sed 의존성 없음, BusyBox/macOS sed 호환.
- **setup TCL 필터링**: Tcl 파일 I/O 대신 Python(asyncio) 레벨에서 `sed`로 사전 필터링. filtered_tcl (`/tmp/mcp_setup_filtered_{port}.tcl`)을 `MCP_SETUP_TCL`에 전달.
- **`source_separately=False` 분기**: login shell wrapper(`_login_shell_cmd`)로 rc 파일 소스 후 스크립트 실행.
- **nohup detach**: `(nohup ... < /dev/null &)` subshell 래핑으로 asyncio PIPE에서 완전 분리. `< /dev/null` 없으면 xmsim이 stdin EOF를 받아 즉시 종료될 수 있음.

### 5.4 `_start_batch()` 구현

```python
async def _start_batch(
    sim_dir: str,
    config: dict,
    test_name: str,
    setup_tcl: str,
    run_duration: str,
) -> str:
    """Start simulation in batch mode. Delegates to existing _run_batch_single()."""
    runner = config.get("runner", {})
    return await _run_batch_single(
        sim_dir=sim_dir,
        test_name=test_name,
        runner=runner,
        run_duration=run_duration,
        timeout=600,
    )
```

---

## 6. Phase 2: Shell redirect 보호 및 login shell wrapper

### 6.0 `_login_shell_cmd()` — login shell 래핑

```python
def _login_shell_cmd(login_shell: str, cmd: str) -> str:
    """Build a command that runs in login shell environment.

    tcsh 6.18 (CentOS 7) does not support '-l -c' combination.
    Workaround: source rc files explicitly before the command.
    tcsh reads ~/.tcshrc first; if absent, reads ~/.cshrc.
    Both are sourced unconditionally so either file works.
    For bash: '-l -c' works fine.
    """
    if "tcsh" in login_shell or "csh" in login_shell:
        # tcsh/csh: source rc files in tcsh's native order
        # source both — 'if (-f ...)' guard prevents error when absent
        return (
            f"{login_shell} -c '"
            f"if (-f ~/.tcshrc) source ~/.tcshrc >& /dev/null; "
            f"if (-f ~/.cshrc) source ~/.cshrc >& /dev/null; "
            f"{cmd}'"
        )
    # bash/sh/zsh: -l -c works
    return f"{login_shell} -l -c '{cmd}'"
```

**변경 이유**: tcsh 6.18 (CentOS 7)은 `-l -c` 조합을 지원하지 않아 `tcsh: Bad flag combination` 오류 발생.
이전 구현은 `~/.tcshrc`만 소스했으나, 일부 환경에서는 EDA 환경이 `~/.cshrc`에만 설정되므로
두 파일을 모두 시도하도록 변경. `if (-f ...)` 가드로 파일 부재 시 에러 없이 넘어감.

### 6.1 `ssh_run()` 확장 — sim_runner.py

```python
async def ssh_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Args:
        cmd:      Shell command string.
        timeout:  Execution timeout in seconds.
        log_file: If set, append '>& {log_file}' to cmd (tcsh-safe redirect).

    Raises:
        ValueError: if cmd contains '2>&1' (tcsh-unsafe).
    """
    if "2>&1" in cmd:
        raise ValueError(
            "Do not use '2>&1' — tcsh interprets '&1' as filename. "
            "Use log_file parameter or _build_redirect() instead."
        )
    if log_file:
        cmd = f"{cmd} {_build_redirect(log_file)}"

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise asyncio.TimeoutError(f"ssh_run timeout ({timeout}s): {cmd}")
    return (stdout + stderr).decode("utf-8", errors="replace").strip()


def _build_redirect(log_path: str) -> str:
    """Build shell redirect suffix safe for both bash and tcsh.

    NEVER use '2>&1' — tcsh interprets '&1' as filename, creating file '1'.
    Use '>& file' which works in both bash and tcsh.
    """
    return f">& {log_path}"
```

---

## 7. Phase 3: 기존 tool 일원화

### 7.1 P3-1: csv_cache.py — registry 기반 simvisdbutil 경로

```python
# csv_cache.py — 변경 부분

async def _resolve_simvisdbutil() -> str:
    """Get simvisdbutil path from registry. Falls back to sim_discover."""
    global _simvisdbutil_path
    if _simvisdbutil_path:
        return _simvisdbutil_path

    # Try registry first
    from xcelium_mcp.sim_runner import _get_default_sim_dir, load_sim_config
    sim_dir = await _get_default_sim_dir()
    if sim_dir:
        cfg = await load_sim_config(sim_dir)
        if cfg and "eda_tools" in cfg:
            path = cfg["eda_tools"].get("simvisdbutil", "")
            if path:
                _simvisdbutil_path = path
                return path

    # Fallback: trigger sim_discover
    from xcelium_mcp.sim_runner import run_full_discovery
    await run_full_discovery(sim_dir or "")

    # Retry after discover
    sim_dir = await _get_default_sim_dir()
    if sim_dir:
        cfg = await load_sim_config(sim_dir)
        if cfg and "eda_tools" in cfg:
            path = cfg["eda_tools"].get("simvisdbutil", "")
            if path:
                _simvisdbutil_path = path
                return path

    raise RuntimeError("simvisdbutil not found even after sim_discover.")
```

### 7.2 P3-2/P3-2b: sim_batch_run / sim_batch_regression — 자체 탐지 제거

```python
# server.py — sim_batch_run 변경 부분

async def sim_batch_run(test_name: str, sim_dir: str = "", ...):
    # 기존: runner = await _load_or_detect_runner(resolved_sim_dir)
    # 변경:
    resolved_sim_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    if not resolved_sim_dir:
        await run_full_discovery(sim_dir)  # sim_discover 자동 호출
        resolved_sim_dir = await _get_default_sim_dir()

    config = await load_sim_config(resolved_sim_dir)
    if config is None:
        await run_full_discovery(resolved_sim_dir)
        config = await load_sim_config(resolved_sim_dir)
    runner = config.get("runner", config)
    # ... 이하 기존 로직
```

동일 패턴을 `sim_batch_regression`에도 적용.

### 7.3 P3-3: `_load_or_detect_runner()` 리팩터링

```python
async def _load_or_detect_runner(sim_dir: str) -> dict:
    """Return runner config for sim_dir.

    v4: Tier 2 자체 탐지 제거. config 없으면 sim_discover 위임.

    Tier 1: load .mcp_sim_config.json
    Tier 2: (삭제됨 — sim_discover로 대체)
    """
    # Tier 1: explicit config
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    # sim_discover 자동 호출
    await run_full_discovery(sim_dir)
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    raise RuntimeError(f"sim_discover failed for {sim_dir}")
```

### 7.4 P3-4: connect_simulator — `_auto_register_sim_dir()` 삭제

```python
# server.py — connect_simulator

@mcp.tool()
async def connect_simulator(
    host: str = "localhost",
    port: int = 9876,
    timeout: float = 30.0,
) -> str:
    """Connect to a SimVision instance running mcp_bridge.tcl."""
    global _bridge

    if _bridge and _bridge.connected:
        await _bridge.disconnect()

    _bridge = TclBridge(host=host, port=port, timeout=timeout)
    ping = await _bridge.connect()

    try:
        where = await _bridge.execute("where")
    except TclError:
        where = "(unknown)"

    # v4: _auto_register_sim_dir() 삭제. 순수 TCP 연결만.
    return f"Connected to SimVision at {host}:{port} (ping={ping})\nCurrent position: {where}"


# _auto_register_sim_dir() 함수 자체를 삭제
```

---

## 8. mcp_bridge.tcl 변경

### 8.1 MCP_SETUP_TCL sourcing 추가

```tcl
# mcp_bridge.tcl — ::mcp_bridge::init 내부, on_init 완료 직후에 위치

# --- v4: Source project setup TCL via MCP_SETUP_TCL env var ---
# When sim_start sets MCP_SETUP_TCL, this sources the project's original
# setup.tcl (probe settings, dump scope, etc.) after bridge initialization.
# IMPORTANT: MCP_SETUP_TCL is pre-filtered by _start_bridge (Python side)
# to remove run/exit/finish/database-close lines.
# Only probe/database-open setup remains — safe to source in bridge mode.
if {[info exists ::env(MCP_SETUP_TCL)] && $::env(MCP_SETUP_TCL) ne ""} {
    if {[file exists $::env(MCP_SETUP_TCL)]} {
        puts "MCP Bridge: sourcing setup TCL: $::env(MCP_SETUP_TCL)"
        source $::env(MCP_SETUP_TCL)
        puts "MCP Bridge: setup TCL loaded"
    } else {
        puts "MCP Bridge: WARNING — MCP_SETUP_TCL not found: $::env(MCP_SETUP_TCL)"
    }
}
```

**삽입 위치**: `::mcp_bridge::init` proc 내부, `::mcp_bridge::on_init` 완료 직후.

**필터링 책임 분리**: `run/exit/finish` 명령 인터셉트는 Tcl 레벨이 아닌 Python `_start_bridge`의 `sed` 사전 필터링으로 처리. mcp_bridge.tcl은 단순 `source`만 수행.

### 8.2 `vwait` 추가

```tcl
# mcp_bridge.tcl 파일 끝 (::mcp_bridge::init 호출 이후)

# Start the bridge
::mcp_bridge::init

# When run via nohup (stdin=/dev/null), xmsim exits after -input script
# instead of entering interactive mode. vwait keeps the process alive
# and processes fileevent (socket) callbacks.
# Note: vwait in stopped state does NOT advance simulation.
puts "MCP Bridge: ready (waiting for client)"
if {![info exists ::mcp_bridge::_shutdown_flag]} {
    set ::mcp_bridge::_shutdown_flag 0
}
vwait ::mcp_bridge::_shutdown_flag
```

**추가 이유**: `nohup ... < /dev/null` 조합으로 실행할 때 xmsim은 stdin이 EOF이므로 `-input` 스크립트 실행 후 즉시 종료. `vwait`로 event loop를 유지해야 socket fileevent 콜백이 동작함.
`_shutdown_flag` 변수는 `__SHUTDOWN__` 명령 처리 시 `finish` 직전에 1로 설정되어 vwait를 해제.

---

## 9. v3 삭제 대상 코드

| 파일 | 삭제 대상 | 이유 |
|------|----------|------|
| server.py | `_auto_register_sim_dir()` 함수 전체 | P3-4: connect_simulator 환경 탐지 제거 |
| server.py | `from ... import _update_registry_env` | sim_discover 내부로 흡수 |
| sim_runner.py | `_update_registry_env()` 함수 | `_update_registry_from_config()`로 대체 |
| sim_runner.py | `_load_or_detect_runner()` Tier 2 블록 (line 530-548) | sim_discover 위임으로 대체 |
| csv_cache.py | `_resolve_simvisdbutil()` 내 login shell / glob 탐지 | registry `eda_tools` 참조로 대체 |

---

## 10. 구현 순서 의존관계

```
Phase 1 (sim_discover):
  P1-6 (schema) → P1-2,3,4,5,8 (탐지 로직) → P1-7 (리팩터링) → P1-1 (tool 등록)
  P1-9 (.simvisionrc) — 독립
  P1-10 (legacy patch) — 독립

Phase 2 (mcp_config + sim_start):
  P2-2 (dot-notation) → P2-1 (mcp_config tool)
  P2-9 (_build_redirect) → P2-4,5,6 (bridge 시작) → P2-3 (sim_start tool)
  P2-7 (batch mode) — P2-3 이후
  P2-8 (로그) — P2-4 이후

Phase 3 (일원화):
  Phase 1 완료 후 착수
  P3-3 (_load_or_detect_runner) → P3-2,P3-2b (batch tools) → P3-1 (csv_cache) → P3-4 (connect)

mcp_bridge.tcl 변경 (§8): Phase 1과 병렬 가능
```

---

## 11. 검증 항목 (SC 매핑)

| SC | 검증 내용 | Phase |
|:--:|----------|:-----:|
| SC-1 | sim_discover 환경 자동 탐지 + registry 생성 | P1 |
| SC-2 | sim_start bridge mode → connect_simulator 성공 | P2 |
| SC-3 | save_checkpoint sim_dir 자동 로드 | P2 |
| SC-4 | extract_csv simvisdbutil registry 로드 | P3 |
| SC-5 | sim_batch_run 호환 | P3 |
| SC-6 | sim_start → sim_discover 자동 호출 | P2 |
| SC-7 | mcp_config show/get/set/delete | P2 |
| SC-8 | registry 쓰기 일원화 grep | P3 |
| SC-9 | 환경 탐지 일원화 grep | P3 |
| SC-10 | connect_simulator 탐지 코드 제거 | P3 |
| SC-11 | `2>&1` 전체 코드 0건 | P2 |
