"""sim_runner.py — Core simulation lifecycle for xcelium-mcp v4.2.

v4.2: Functions split into env_detection.py, registry.py, batch_runner.py.
This file retains: ssh_run, shell helpers, start_simulation, _start_bridge,
run_full_discovery orchestrator, and legacy script patching.

Re-exports from new modules are provided for backward compatibility with
tools/*.py imports that reference sim_runner.
"""
from __future__ import annotations

import asyncio
import base64
import os
import re
import shlex

# ===================================================================
# Core utilities (used by ALL modules)
# ===================================================================


def sq(s: str) -> str:
    """Shell-quote a user-supplied string to prevent injection."""
    return shlex.quote(s)


# Backward-compat alias
_sq = sq


def build_redirect(log_path: str) -> str:
    """Build shell redirect suffix safe for both bash and tcsh.

    NEVER use '2>&1' — tcsh interprets '&1' as filename, creating file '1'.
    Use '>& file' which works in both bash and tcsh.
    """
    return f">& {log_path}"


# Backward-compat alias
_build_redirect = build_redirect


class UserInputRequired(Exception):
    """Raised when user input is needed to continue."""
    def __init__(self, prompt: str):
        self.prompt = prompt
        super().__init__(prompt)


async def ssh_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Since xcelium-mcp runs on cloud0, this is a local asyncio subprocess —
    not an SSH call. Combined stdout+stderr is returned as a single string.
    """
    if "2>&1" in cmd:
        raise ValueError(
            "Do not use '2>&1' — tcsh interprets '&1' as filename. "
            "Use log_file parameter or build_redirect() instead."
        )
    if log_file:
        cmd = f"{cmd} {build_redirect(log_file)}"

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


def login_shell_cmd(login_shell: str, cmd: str) -> str:
    """Build a command that runs in login shell environment.

    WARNING: Do not include '2>/dev/null' or '2>&1' in cmd.
    tcsh has no stderr-only redirect syntax — '2>' causes 'Ambiguous redirect'.
    If stderr suppression is needed, either:
      - Filter results in Python (e.g. check '/' in path for 'which' output)
      - Add ssh_run(stderr_mode="drop") parameter (implement when needed)
    """
    # Escape single quotes in cmd to prevent shell injection:
    # replace ' with '\'' (end quote, literal apostrophe, reopen quote)
    safe_cmd = cmd.replace("'", "'\\''")
    if "tcsh" in login_shell or "csh" in login_shell:
        return (
            f"{login_shell} -c '"
            f"if (-f ~/.tcshrc) source ~/.tcshrc >& /dev/null; "
            f"if (-f ~/.cshrc) source ~/.cshrc >& /dev/null; "
            f"{safe_cmd}'"
        )
    return f"{login_shell} -l -c '{safe_cmd}'"


# Backward-compat alias
_login_shell_cmd = login_shell_cmd


# ===================================================================
# Re-exports from new modules (backward compatibility)
# tools/*.py import these names from sim_runner
# ===================================================================
from xcelium_mcp.registry import (  # noqa: E402, F401
    load_registry,
    save_registry,
    load_sim_config,
    save_sim_config,
    _update_registry_from_config,
    config_action,
)

from xcelium_mcp.batch_runner import (  # noqa: E402, F401
    ExecInfo,
    validate_extra_args,
    _validate_extra_args,  # backward compat
    _resolve_exec_cmd,
    _run_batch_single,
    _run_batch_regression,
    _poll_batch_log,
    resolve_sim_params,
    _resolve_sim_params,  # backward compat
    resolve_test_name,
    _resolve_test_name,  # backward compat
)

from xcelium_mcp.env_detection import (  # noqa: E402, F401
    _detect_env_shell,
    _detect_eda_env,
    _detect_shell_and_env,
    _auto_detect_runner,
    _ask_user_runner,
    _analyze_tb_type,
    _discover_sim_dir,
    _load_or_detect_runner,
    _extract_script_name,
    _detect_bridge_tcl,
    _detect_setup_tcls,
    _pick_default_mode,
    _resolve_eda_tools,
    _resolve_external_tools,
    _detect_bridge_port,
    _detect_run_dir,
    _detect_vnc_display,
)


# ===================================================================
# Utility functions (used by tools)
# ===================================================================


_USER_TMP: str = ""  # cached after first call


async def get_user_tmp_dir() -> str:
    """Get per-user temp directory. Creates on first call.

    Returns /tmp/xcelium_mcp_{uid}/ — unique per Unix user.
    Python and Tcl must use the same path pattern for ready file sync.
    """
    global _USER_TMP
    if _USER_TMP:
        return _USER_TMP
    r = await ssh_run("id -u", timeout=5)
    uid = r.strip()
    _USER_TMP = f"/tmp/xcelium_mcp_{uid}"
    await ssh_run(f"mkdir -p {_USER_TMP}", timeout=5)
    return _USER_TMP


# Backward-compat alias
_get_user_tmp_dir = get_user_tmp_dir


def _parse_shm_path(db_list_output: str) -> str:
    """Parse SHM path from xmsim 'database -list' output."""
    for line in db_list_output.strip().splitlines():
        line = line.strip().strip("'\"")
        if ".shm" in line:
            idx = line.index(".shm") + 4
            return line[:idx]
    return ""


def _parse_time_ns(where_output: str) -> int:
    """Parse simulation time from xmsim 'where' output into nanoseconds."""
    m = re.search(r'(\d+)\s+MS\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1_000_000 + int(m.group(2))
    m = re.search(r'(\d+)\s+US\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 1000 + int(m.group(2))
    m = re.search(r'(\d+)\s+NS\s*\+\s*(\d+)', where_output, re.IGNORECASE)
    if m:
        return int(m.group(1)) + int(m.group(2))
    m = re.search(r'(\d+)', where_output)
    if m:
        return int(m.group(1))
    return 0


async def get_default_sim_dir() -> str:
    """Return the default simulation directory from mcp_registry.json."""
    registry = load_registry()
    projects = registry.get("projects", {})
    for proj_key, proj in projects.items():
        for env_key, env in proj.get("environments", {}).items():
            if env.get("is_default"):
                return env_key
    return ""


# Backward-compat alias
_get_default_sim_dir = get_default_sim_dir


# ===================================================================
# Legacy script patching
# ===================================================================

_SIMVISIONRC_MARKER = "# [xcelium-mcp] managed by sim_discover"


async def _patch_legacy_run_script(sim_dir: str, runner_info: dict) -> str:
    """Patch legacy run script to support MCP_INPUT_TCL env var override."""
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    _sp = sq(script_path)

    exists = await ssh_run(f"test -f {_sp} && echo YES || echo NO", timeout=5)
    if "YES" not in exists:
        return "run script not found"

    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {_sp} 2>/dev/null")
    if r.strip() and r.strip() != "0":
        return "already patched"

    r = await ssh_run(f"grep -n 'xmsim.*-input' {_sp} 2>/dev/null")
    if not r.strip():
        return "no xmsim -input found — manual patch needed"

    match = re.search(r'-input\s+(\S+)', r.strip())
    if not match:
        return "could not parse -input argument — manual patch needed"

    original_tcl = match.group(1)
    escaped_original = re.escape(original_tcl)
    replacement = f'${{MCP_INPUT_TCL:-{original_tcl}}}'

    # sq() the sed pattern to prevent shell injection from original_tcl/replacement
    sed_pattern = f"s|-input {escaped_original}|-input {replacement}|"
    sed_cmd = f"sed -i -e {sq(sed_pattern)} {_sp}"
    await ssh_run(sed_cmd, timeout=10)

    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {_sp} 2>/dev/null")
    if r.strip() and r.strip() != "0":
        return f"patched: -input {original_tcl} -> -input {replacement}"
    return "patch failed — manual edit needed"


async def _update_simvisionrc(bridge_tcl: str) -> str:
    """Update ~/.simvisionrc to source mcp_bridge.tcl from install path."""
    home = (await ssh_run("echo $HOME")).strip()
    rc_path = f"{home}/.simvisionrc"
    source_line = f"source {bridge_tcl}"

    content = await ssh_run(f"cat {rc_path} 2>/dev/null")

    if _SIMVISIONRC_MARKER in content:
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
                continue
            skip_next = False
            new_lines.append(line)
        new_content = "\n".join(new_lines)
        # base64-encode to avoid heredoc delimiter injection
        b64 = base64.b64encode(new_content.encode()).decode()
        await ssh_run(f"echo {sq(b64)} | base64 -d > {sq(rc_path)}")
        return "updated (marker found)"

    if "mcp_bridge" in content:
        sed_pattern = f"/mcp_bridge/c\\{_SIMVISIONRC_MARKER}\\n{source_line}"
        await ssh_run(f"sed -i -e {sq(sed_pattern)} {sq(rc_path)}")
        return "replaced unmanaged entry"

    managed_block = f"{_SIMVISIONRC_MARKER}\n{source_line}"
    await ssh_run(f"echo '\\n{managed_block}' >> {rc_path}")
    if not content.strip():
        return "created"
    return "added"


# ===================================================================
# Discovery orchestrator
# ===================================================================


async def run_full_discovery(
    sim_dir: str = "", force: bool = False, top_module: str = "",
) -> str:
    """Main discovery orchestrator. Called by sim_discover MCP tool."""
    if not sim_dir:
        envs = await _discover_sim_dir()
        sim_dir = envs[0]["sim_dir"]

    # B-tilde fix: resolve ~ to absolute path before any sq() calls.
    sim_dir = os.path.expanduser(sim_dir)

    if not force:
        existing = await load_sim_config(sim_dir)
        if existing and existing.get("version", 1) >= 2:
            return f"Registry already exists for {sim_dir}. Use force=True to re-detect."

    tb_type = await _analyze_tb_type(sim_dir)
    runner_info = await _auto_detect_runner(sim_dir)

    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
    project_root = r.strip()
    shell_env = await _detect_shell_and_env(sim_dir, script_name, project_root)

    bridge_tcl = await _detect_bridge_tcl()
    setup_tcls = await _detect_setup_tcls(sim_dir)
    eda_tools = await _resolve_eda_tools(shell_env)
    external_tools = await _resolve_external_tools(shell_env)
    bridge_port = await _detect_bridge_port(sim_dir, bridge_tcl)
    patch_result = await _patch_legacy_run_script(sim_dir, runner_info)
    run_info = await _detect_run_dir(sim_dir, runner_info)
    run_dir = run_info["run_dir"]
    script_has_cd = run_info["script_has_cd"]

    default_mode = _pick_default_mode(setup_tcls)
    args_format = {default_mode: "-test {test_name} --"}
    if "gate" in setup_tcls:
        args_format["gate"] = "-test {test_name} -gate post --"
    if "ams_rtl" in setup_tcls:
        args_format["ams_rtl"] = "-test {test_name} -ams --"
    if "ams_gate" in setup_tcls:
        args_format["ams_gate"] = "-test {test_name} -amsf -gate post --"

    mode_defaults = {
        "common": {"timeout": 120, "probe_strategy": "all", "extra_args": "", "dump_depth": "all"},
    }
    if "gate" in setup_tcls:
        mode_defaults["gate"] = {"timeout": 1800, "probe_strategy": "selective", "dump_depth": "boundary"}
    if "ams_rtl" in setup_tcls:
        mode_defaults["ams_rtl"] = {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"}
    if "ams_gate" in setup_tcls:
        mode_defaults["ams_gate"] = {"timeout": 3600, "probe_strategy": "selective", "dump_depth": "boundary"}

    _sd = sq(sim_dir)
    if tb_type == "uvm":
        test_cmd = (
            f"grep -rh 'extends uvm_test' {_sd} --include='*.sv' --include='*.svh' 2>/dev/null "
            f"| grep -oE 'class \\w+' | sed 's/class //' | sort -u"
        )
    elif tb_type == "sv_directed":
        test_cmd = (
            f"grep -rh '^\\s*program ' {_sd} --include='*.sv' 2>/dev/null "
            f"| grep -oE 'program \\w+' | sed 's/program //' | sort -u"
        )
    else:
        test_cmd = f"ls {_sd}/tb_tests/*.v 2>/dev/null | xargs -I{{}} basename {{}} .v"

    cached_tests = []
    try:
        r = await ssh_run(f"cd {_sd} && {test_cmd}", timeout=30)
        cached_tests = [t.strip() for t in r.strip().splitlines() if t.strip()]
    except Exception:
        pass

    from datetime import datetime
    test_discovery = {
        "command": test_cmd,
        "cached_tests": cached_tests,
        "cached_at": datetime.now().isoformat(),
    }

    config = {
        "version": 2,
        "runner": {
            "type": runner_info.get("runner", "shell"),
            "script": script_name,
            "run_dir": run_dir,
            "script_has_cd": script_has_cd,
            **shell_env,
            "args_format": args_format,
            "mode_defaults": mode_defaults,
            "setup_tcls": setup_tcls,
            "default_mode": default_mode,
        },
        "bridge": {
            "tcl_path": bridge_tcl,
            "port": bridge_port,
        },
        "eda_tools": eda_tools,
        "external_tools": external_tools,
        "test_discovery": test_discovery,
    }

    # v4.3: $sdf_annotate analysis
    try:
        sdf_info = await _analyze_sdf_annotate(sim_dir, runner_info, top_module)
        config["sdf_info"] = sdf_info
    except UserInputRequired:
        # top module 자동 탐지 실패 → sdf_info 없이 진행 (사용자가 재호출 시 top_module 제공)
        config["sdf_info"] = {"has_sdf_annotate": False}

    await save_sim_config(sim_dir, config)
    await _update_registry_from_config(sim_dir, tb_type, config)

    simvisionrc_result = await _update_simvisionrc(bridge_tcl)

    return _format_discovery_result(sim_dir, tb_type, config, patch_result, simvisionrc_result)


def _format_discovery_result(
    sim_dir: str, tb_type: str, config: dict,
    patch_result: str, simvisionrc_result: str,
) -> str:
    """Format human-readable discovery result."""
    runner = config["runner"]
    bridge = config["bridge"]
    eda = config.get("eda_tools", {})
    ext = config.get("external_tools", {})
    setup_modes = ", ".join(f"{k}={v}" for k, v in runner.get("setup_tcls", {}).items())
    ext_summary = ", ".join(f"{k}={v}" for k, v in ext.items()) if ext else "(none)"

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
        f"  external_tools: {ext_summary}\n"
        f"  bridge_port:    {bridge.get('port', 9876)}\n"
        f"  .simvisionrc:   {simvisionrc_result}\n"
        f"\nSaved to: ~/.xcelium_mcp/mcp_registry.json\n"
        f"          {sim_dir}/.mcp_sim_config.json"
    )


# ===================================================================
# v4.3: Bridge mode dump_window
# ===================================================================


async def run_with_dump_window(bridges, dump_window: dict, timeout: float = 600):
    """Bridge mode dump_window: probe on/off sequencing.

    Assumes setup tcl started with probe -disable.
    Bridge turnaround: 5 commands (settling run + enable + window run + disable + final run).

    Args:
        bridges: BridgeManager instance (must be connected).
        dump_window: {"start_ms": int, "end_ms": int}
        timeout: max seconds for each sim_run command.
    """
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms

    bridge = bridges.xmsim

    # settling — probe already off (setup tcl: probe -disable)
    if start_ms > 0:
        await bridge.execute(f"run {start_ms}ms", timeout=timeout)

    # window — probe on
    await bridge.execute("probe -enable", timeout=30)
    await bridge.execute(f"run {duration_ms}ms", timeout=timeout)

    # remainder — probe off
    await bridge.execute("probe -disable", timeout=30)
    await bridge.execute("run", timeout=timeout)


# ===================================================================
# v4.3: SDF annotation analysis
# ===================================================================


async def _extract_top_module_from_script(sim_dir: str, runner: dict) -> str:
    """Extract top module name from run_sim script.

    Parses xmsim/xrun/irun invocation to find the last non-option argument.
    Handles: eval prefix, backslash line continuations.

    Returns: top module name, or "" if not found.
    """
    script_name = runner.get("script", "")
    if not script_name:
        return ""

    content = await ssh_run(
        f"cat {sq(sim_dir + '/' + script_name)} 2>/dev/null", timeout=10
    )
    if not content:
        return ""

    # Join backslash-continued lines
    joined = re.sub(r"\\\s*\n\s*", " ", content)

    match = re.search(
        r"(?:eval\s+)?(?:xmsim|xrun|irun)\s+(.+)",
        joined, re.MULTILINE,
    )
    if match:
        tokens = match.group(1).strip().split()
        for token in reversed(tokens):
            if (not token.startswith("-")
                    and not token.startswith("$")
                    and re.fullmatch(r"\w+", token)):
                return token

    return ""


def _parse_ifdef_around_sdf(content: str) -> dict:
    """Parse ifdef structure around $sdf_annotate — no hardcoded define names.

    Builds structured sdf_entries: each $sdf_annotate call with its scope,
    conditions (ifdef stack at that point), and SDF file path.

    Returns:
        {
            "sdf_guard_define": str | None,
            "sdf_entries": list[dict],
        }
    """
    sdf_guard_define = None
    sdf_entries: list[dict] = []
    ifdef_stack: list[dict] = []

    for line in content.splitlines():
        stripped = line.strip()

        # ifdef/ifndef tracking
        m = re.match(r"`(ifdef|ifndef)\s+(\w+)", stripped)
        if m:
            ifdef_stack.append({
                "define": m.group(2), "type": m.group(1), "branch": "if",
            })
        elif stripped.startswith("`else"):
            if ifdef_stack:
                ifdef_stack[-1]["branch"] = "else"
        elif stripped.startswith("`endif"):
            if ifdef_stack:
                ifdef_stack.pop()

        # $sdf_annotate (skip comments)
        if "$sdf_annotate" not in line or stripped.startswith("//"):
            continue

        # Guard detection
        if sdf_guard_define is None:
            for frame in reversed(ifdef_stack):
                if frame["branch"] == "else" and frame["type"] == "ifdef":
                    sdf_guard_define = frame["define"]
                    break
                elif frame["branch"] == "if" and frame["type"] == "ifndef":
                    sdf_guard_define = frame["define"]
                    break

        # Build conditions from current stack
        conditions: dict[str, bool] = {}
        for frame in ifdef_stack:
            if frame["define"] == sdf_guard_define:
                continue
            if frame["type"] == "ifdef":
                conditions[frame["define"]] = (frame["branch"] == "if")
            elif frame["type"] == "ifndef":
                conditions[frame["define"]] = (frame["branch"] == "else")

        # Extract $sdf_annotate arguments: ("file", scope)
        sdf_match = re.search(
            r'\$sdf_annotate\s*\(\s*"([^"]+)"\s*,\s*([^,)\s]+)', line,
        )
        if sdf_match:
            sdf_entries.append({
                "scope": sdf_match.group(2),
                "conditions": conditions,
                "file": sdf_match.group(1),
            })

    return {"sdf_guard_define": sdf_guard_define, "sdf_entries": sdf_entries}


async def _analyze_sdf_annotate(
    sim_dir: str, runner: dict, top_module: str = "",
) -> dict:
    """Analyze $sdf_annotate in TB RTL and surrounding ifdef guards.

    Top module discovery: script → parameter → UserInputRequired → default "top".

    Returns dict with: has_sdf_annotate, top_module, sdf_source_file,
    sdf_guard_define, sdf_entries.
    """
    # Step 1: top module name
    effective_top = top_module
    if not effective_top:
        effective_top = await _extract_top_module_from_script(sim_dir, runner)
    if not effective_top:
        raise UserInputRequired(
            "Top module 이름을 자동으로 찾지 못했습니다.\n"
            "시뮬레이션의 top module 이름을 입력해주세요.\n"
            "  (예: top, tb_top, testbench)\n"
            "  입력하지 않으면 기본값 'top'을 사용합니다."
        )

    # Step 2: find file defining top module
    top_v = await ssh_run(
        f"grep -rl 'module\\s\\+{effective_top}\\b' {sq(sim_dir)} "
        f"--include='*.v' --include='*.sv' 2>/dev/null | head -1",
        timeout=10,
    )
    if not top_v.strip():
        return {"has_sdf_annotate": False, "top_module": effective_top}

    # Step 3: search for $sdf_annotate in top module + includes/instances
    top_v_path = top_v.strip()
    content = await ssh_run(f"cat {sq(top_v_path)}", timeout=10)
    sdf_source = top_v_path

    if "$sdf_annotate" not in content:
        # 3a. includes
        includes = await ssh_run(
            f"grep -oP '`include\\s+\"\\K[^\"]+' {sq(top_v_path)} 2>/dev/null",
            timeout=10,
        )
        # 3b. instantiations
        instances = await ssh_run(
            f"grep -oP '^\\s*(\\w+)\\s+\\w+\\s*\\(' {sq(top_v_path)} 2>/dev/null",
            timeout=10,
        )
        # 3c. collect files
        search_files: list[str] = []
        for inc in includes.strip().splitlines():
            if inc:
                search_files.append(f"{sim_dir}/*/{inc}")
        for line in instances.strip().splitlines():
            inst_mod = line.strip().split()[0] if line.strip() else ""
            if inst_mod:
                f = await ssh_run(
                    f"grep -rl 'module\\s\\+{inst_mod}\\b' {sq(sim_dir)} "
                    f"--include='*.v' --include='*.sv' 2>/dev/null | head -1",
                    timeout=10,
                )
                if f.strip():
                    search_files.append(f.strip())

        # 3d. search collected files
        if search_files:
            files_arg = " ".join(sq(f) for f in search_files)
            ctx = await ssh_run(
                f"grep -n -B10 -A2 '\\$sdf_annotate' {files_arg} 2>/dev/null",
                timeout=10,
            )
            if ctx.strip():
                content = ctx
                # extract source file from grep output
                first_line = ctx.strip().splitlines()[0]
                if ":" in first_line:
                    sdf_source = first_line.split(":")[0]
            else:
                return {"has_sdf_annotate": False, "top_module": effective_top}
        else:
            return {"has_sdf_annotate": False, "top_module": effective_top}

    if "$sdf_annotate" not in content:
        return {"has_sdf_annotate": False, "top_module": effective_top}

    # Step 4: parse ifdef guards + sdf_entries
    result: dict = {
        "has_sdf_annotate": True,
        "top_module": effective_top,
        "sdf_source_file": sdf_source,
    }
    result.update(_parse_ifdef_around_sdf(content))
    return result


# ===================================================================
# Simulation start
# ===================================================================


async def start_simulation(
    test_name: str,
    sim_dir: str = "",
    mode: str = "bridge",
    sim_mode: str = "",
    run_duration: str = "",
    timeout: int = 120,
    extra_args: str = "",
    bridges=None,
    dump_depth: str = "",
) -> str:
    """Start simulation. Registry없으면 sim_discover 자동 호출."""
    validate_extra_args(extra_args)

    resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
    if not resolved_dir:
        await run_full_discovery(sim_dir)
        resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
        if not resolved_dir:
            raise RuntimeError("sim_discover failed to create registry.")

    config = await load_sim_config(resolved_dir)
    if config is None:
        await run_full_discovery(resolved_dir)
        config = await load_sim_config(resolved_dir)
        if config is None:
            raise RuntimeError(f"sim_discover failed for {resolved_dir}")

    runner = config.get("runner", {})

    effective_mode = sim_mode or runner.get("default_mode", "rtl")
    setup_tcls = runner.get("setup_tcls", {})
    if effective_mode not in setup_tcls:
        available = ", ".join(setup_tcls.keys())
        raise RuntimeError(f"sim_mode '{effective_mode}' not found. Available: {available}")

    setup_tcl = f"{resolved_dir}/{setup_tcls[effective_mode]}"

    if mode == "bridge":
        return await _start_bridge(
            resolved_dir, config, test_name, setup_tcl, effective_mode, timeout,
            extra_args=extra_args, bridges=bridges, dump_depth=dump_depth,
        )
    elif mode == "batch":
        return await _start_batch(
            resolved_dir, config, test_name, setup_tcl, run_duration
        )
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'bridge' or 'batch'.")


async def _start_bridge(
    sim_dir: str,
    config: dict,
    test_name: str,
    setup_tcl: str,
    sim_mode: str,
    timeout: int,
    extra_args: str = "",
    bridges=None,
    dump_depth: str = "",
) -> str:
    """Start simulation in bridge mode via legacy run script + env vars."""
    runner = config["runner"]
    bridge = config["bridge"]
    port = bridge.get("port", 9876)
    bridge_tcl = bridge.get("tcl_path", "")
    script = runner.get("script", "run_sim")

    ps = await ssh_run("pgrep -la xmsim 2>/dev/null", timeout=5)
    if ps.strip():
        return (
            f"ERROR: xmsim already running:\n{ps.strip()}\n"
            f"Use shutdown_simulator or 'pkill -f xmsim' first."
        )

    # P4: per-user temp directory
    user_tmp = await get_user_tmp_dir()
    await ssh_run(f"rm -f {user_tmp}/bridge_ready_*", timeout=5)

    script_shell = runner.get("script_shell", runner.get("env_shell", "/bin/sh"))
    params = resolve_sim_params(runner, sim_mode, extra_args=extra_args, timeout=timeout,
                               dump_depth=dump_depth if dump_depth else None)
    test_args = params["test_args_format"].format(test_name=sq(test_name))
    if params["extra_args"]:
        test_args = f"{test_args} {params['extra_args']}"
    effective_timeout = params["timeout"]
    log_file = f"{user_tmp}/sim_start_{port}.log"

    filtered_tcl = f"{user_tmp}/setup_filtered_{port}.tcl"
    await ssh_run(
        f"sed '"
        f"/^[[:space:]]*run[[:space:]]*$/d; "
        f"/^[[:space:]]*run[[:space:]]/d; "
        f"/^[[:space:]]*exit[[:space:]]*$/d; "
        f"/^[[:space:]]*exit[[:space:]]/d; "
        f"/^[[:space:]]*finish[[:space:]]*$/d; "
        f"/^[[:space:]]*finish[[:space:]]/d; "
        f"/^[[:space:]]*database[[:space:]]*-close/d"
        f"' {setup_tcl} > {filtered_tcl}",
        timeout=10,
    )

    env_files = runner.get("env_files", [])
    env_shell = runner.get("env_shell", script_shell)
    login_shell = runner.get("login_shell", "/bin/sh")
    inner_parts = [
        f"setenv MCP_INPUT_TCL {bridge_tcl}",
        f"setenv MCP_SETUP_TCL {filtered_tcl}",
    ]
    if runner.get("source_separately") and env_files:
        for ef in env_files:
            inner_parts.append(f"source {ef}")
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = f"{env_shell} -c '{inner_cmd}'"
    else:
        inner_parts.append(f"./{script} {test_args}")
        inner_cmd = "; ".join(inner_parts)
        shell_cmd = login_shell_cmd(login_shell, inner_cmd)

    run_dir = runner.get("run_dir", "run")
    if runner.get("script_has_cd", False):
        cwd = sim_dir
    else:
        cwd = f"{sim_dir}/{run_dir}"

    cmd = (
        f"cd {sq(cwd)} && "
        f"(nohup {shell_cmd} "
        f"{build_redirect(log_file)} < /dev/null &)"
    )
    await ssh_run(cmd, timeout=15)

    from xcelium_mcp.tcl_bridge import TclBridge as _TB

    if bridges is not None and bridges.xmsim_raw and bridges.xmsim_raw.connected:
        await bridges.xmsim_raw.disconnect()
        bridges.set_xmsim(None)

    for i in range(effective_timeout // 2):
        await asyncio.sleep(2)
        r = await ssh_run(f"cat {user_tmp}/bridge_ready_* 2>/dev/null")
        for line in r.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "xmsim":
                actual_port = int(parts[0])
                new_bridge = _TB(host="localhost", port=actual_port)
                try:
                    ping = await new_bridge.connect()
                    if bridges is not None:
                        bridges.set_xmsim(new_bridge)
                    return (
                        f"Simulation started and connected (bridge mode, {sim_mode}).\n"
                        f"  test: {test_name}\n"
                        f"  setup_tcl: {setup_tcl}\n"
                        f"  port: {actual_port}\n"
                        f"  ping: {ping}\n"
                        f"  log: {log_file}\n\n"
                        f"Ready. sim_run, get_signal_value etc. available immediately."
                    )
                except Exception:
                    continue

    log_tail = await ssh_run(f"tail -20 {log_file} 2>/dev/null", timeout=5)
    return f"ERROR: bridge not ready after {timeout}s.\nLog tail:\n{log_tail}"


async def _start_batch(
    sim_dir: str,
    config: dict,
    test_name: str,
    setup_tcl: str,
    run_duration: str,
) -> str:
    """Start simulation in batch mode. Delegates to batch_runner."""
    runner = config.get("runner", {})
    return await _run_batch_single(
        sim_dir=sim_dir,
        test_name=test_name,
        runner=runner,
        run_duration=run_duration,
        timeout=600,
    )
