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
    """Build a command that runs in login shell environment."""
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
        await ssh_run(
            f"sed -i '/mcp_bridge/c\\{_SIMVISIONRC_MARKER}\\n{source_line}' {rc_path}"
        )
        return "replaced unmanaged entry"

    managed_block = f"{_SIMVISIONRC_MARKER}\n{source_line}"
    await ssh_run(f"echo '\\n{managed_block}' >> {rc_path}")
    if not content.strip():
        return "created"
    return "added"


# ===================================================================
# Discovery orchestrator
# ===================================================================


async def run_full_discovery(sim_dir: str = "", force: bool = False) -> str:
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
        "common": {"timeout": 120, "probe_strategy": "all", "extra_args": ""},
    }
    if "gate" in setup_tcls:
        mode_defaults["gate"] = {"timeout": 1800, "probe_strategy": "selective"}
    if "ams_rtl" in setup_tcls:
        mode_defaults["ams_rtl"] = {"timeout": 3600, "probe_strategy": "selective"}
    if "ams_gate" in setup_tcls:
        mode_defaults["ams_gate"] = {"timeout": 3600, "probe_strategy": "selective"}

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
        "test_discovery": test_discovery,
    }
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
        f"\nSaved to: ~/.xcelium_mcp/mcp_registry.json\n"
        f"          {sim_dir}/.mcp_sim_config.json"
    )


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
            extra_args=extra_args, bridges=bridges,
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
    params = resolve_sim_params(runner, sim_mode, extra_args=extra_args, timeout=timeout)
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
