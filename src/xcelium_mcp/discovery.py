"""discovery.py — Simulation environment discovery and SDF analysis.

Extracted from sim_runner.py. Contains: run_full_discovery, resolve_sim_dir,
get_default_sim_dir, SDF annotation analysis, top module extraction,
and legacy script patching.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from datetime import datetime

from xcelium_mcp.registry import (
    _update_registry_from_config,
    load_registry,
    load_sim_config,
    save_sim_config,
)
from xcelium_mcp.runner_detection import (
    auto_detect_runner,
    detect_shell_and_env,
    extract_script_name,
    pick_default_mode,
    resolve_eda_tools,
    resolve_external_tools,
)
from xcelium_mcp.shell_utils import (
    UserInputRequired,
    shell_quote,
    ssh_run,
)
from xcelium_mcp.sim_env_detection import (
    analyze_tb_type,
    detect_bridge_port,
    detect_bridge_tcl,
    detect_run_dir,
    detect_setup_tcls,
    discover_sim_dir,
)
from xcelium_mcp.tcl_bridge import DEFAULT_BRIDGE_PORT

logger = logging.getLogger(__name__)


# ===================================================================
# sim_dir resolution
# ===================================================================


async def get_default_sim_dir() -> str:
    """Return the default simulation directory from mcp_registry.json."""
    registry = load_registry()
    projects = registry.get("projects", {})
    for proj_key, proj in projects.items():
        for env_key, env in proj.get("environments", {}).items():
            if env.get("is_default"):
                return env_key
    return ""


async def resolve_sim_dir(sim_dir: str = "") -> str:
    """Resolve sim_dir: use provided value or fall back to registry default.

    Raises ValueError if no sim_dir available.
    """
    resolved = sim_dir if sim_dir else await get_default_sim_dir()
    if not resolved:
        raise ValueError("No sim_dir. Run sim_discover first.")
    return resolved


# ===================================================================
# Top module extraction / SDF analysis
# ===================================================================


def _extract_top_module_from_content(content: str) -> str:
    """Parse xmsim/xrun/irun invocation in a script to find the top module name.

    Pure helper (no I/O). Handles: eval prefix, backslash line continuations.
    Returns: top module name, or "" if not found.
    """
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


async def _extract_top_module_from_script(sim_dir: str, runner: dict) -> str:
    """Read run_sim script via SSH and extract the top module name."""
    script_name = runner.get("script", "")
    if not script_name:
        return ""

    content = await ssh_run(
        f"cat {shell_quote(sim_dir + '/' + script_name)}", timeout=10
    )
    return _extract_top_module_from_content(content)


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

    Top module discovery: script -> parameter -> UserInputRequired -> default "top".

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
        f"grep -rl 'module\\s\\+{effective_top}\\b' {shell_quote(sim_dir)} "
        f"--include='*.v' --include='*.sv' | head -1",
        timeout=10,
    )
    if not top_v.strip():
        return {"has_sdf_annotate": False, "top_module": effective_top}

    # Step 3: search for $sdf_annotate in top module + includes/instances
    top_v_path = top_v.strip()
    content = await ssh_run(f"cat {shell_quote(top_v_path)}", timeout=10)
    sdf_source = top_v_path

    if "$sdf_annotate" not in content:
        # 3a. includes + 3b. instantiations (parallelized)
        includes, instances = await asyncio.gather(
            ssh_run(
                f"grep -oP '`include\\s+\"\\K[^\"]+' {shell_quote(top_v_path)}",
                timeout=10,
            ),
            ssh_run(
                f"grep -oP '^\\s*(\\w+)\\s+\\w+\\s*\\(' {shell_quote(top_v_path)}",
                timeout=10,
            ),
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
                    f"grep -rl 'module\\s\\+{inst_mod}\\b' {shell_quote(sim_dir)} "
                    f"--include='*.v' --include='*.sv' | head -1",
                    timeout=10,
                )
                if f.strip():
                    search_files.append(f.strip())

        # 3d. search collected files
        if search_files:
            files_arg = " ".join(shell_quote(f) for f in search_files)
            ctx = await ssh_run(
                f"grep -n -B10 -A2 '\\$sdf_annotate' {files_arg}",
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
# Legacy script patching (moved from bridge_lifecycle to break circular dep)
# ===================================================================

_SIMVISIONRC_MARKER = "# [xcelium-mcp] managed by sim_discover"


async def _patch_legacy_run_script(sim_dir: str, runner_info: dict) -> str:
    """Patch legacy run script to support MCP_INPUT_TCL env var override."""
    script_name = extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    _sp = shell_quote(script_path)

    exists = await ssh_run(f"test -f {_sp} && echo YES || echo NO", timeout=5)
    if "YES" not in exists:
        return "run script not found"

    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {_sp} || true")
    if r.strip() and r.strip() != "0":
        return "already patched"

    r = await ssh_run(f"grep -n 'xmsim.*-input' {_sp} || true")
    if not r.strip():
        return "no xmsim -input found — manual patch needed"

    match = re.search(r'-input\s+(\S+)', r.strip())
    if not match:
        return "could not parse -input argument — manual patch needed"

    original_tcl = match.group(1)
    escaped_original = re.escape(original_tcl)
    replacement = f'${{MCP_INPUT_TCL:-{original_tcl}}}'

    sed_pattern = f"s|-input {escaped_original}|-input {replacement}|"
    sed_cmd = f"sed -i -e {shell_quote(sed_pattern)} {_sp}"
    await ssh_run(sed_cmd, timeout=10)

    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {_sp} || true")
    if r.strip() and r.strip() != "0":
        return f"patched: -input {original_tcl} -> -input {replacement}"
    return "patch failed — manual edit needed"


async def _update_simvisionrc(bridge_tcl: str) -> str:
    """Update ~/.simvisionrc to source mcp_bridge.tcl from install path."""
    home = (await ssh_run("echo $HOME")).strip()
    rc_path = f"{home}/.simvisionrc"
    source_line = f"source {bridge_tcl}"

    content = await ssh_run(f"cat {rc_path} || true")

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
        b64 = base64.b64encode(new_content.encode()).decode()
        await ssh_run(f"echo {shell_quote(b64)} | base64 -d > {shell_quote(rc_path)}")
        return "updated (marker found)"

    if "mcp_bridge" in content:
        sed_pattern = f"/mcp_bridge/c\\{_SIMVISIONRC_MARKER}\\n{source_line}"
        await ssh_run(f"sed -i -e {shell_quote(sed_pattern)} {shell_quote(rc_path)}")
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
        envs = await discover_sim_dir()
        sim_dir = envs[0]["sim_dir"]

    # B-tilde fix: resolve ~ to absolute path before any shell_quote() calls.
    sim_dir = os.path.expanduser(sim_dir)

    if not force:
        existing = await load_sim_config(sim_dir)
        if existing and existing.get("version", 1) >= 2:
            return f"Registry already exists for {sim_dir}. Use force=True to re-detect."

    # Phase A: independent detection (parallelized)
    tb_type, runner_info, r_root, bridge_tcl = await asyncio.gather(
        analyze_tb_type(sim_dir),
        auto_detect_runner(sim_dir),
        ssh_run("git rev-parse --show-toplevel || echo ~"),
        detect_bridge_tcl(),
    )

    script_name = extract_script_name(runner_info.get("exec_cmd", ""))
    project_root = r_root.strip()
    shell_env = await detect_shell_and_env(sim_dir, script_name, project_root)

    # Phase B: dependent on shell_env / bridge_tcl (parallelized)
    # _patch_legacy_run_script uses sed -i (file write) — must run serially
    # to avoid race with _detect_run_dir which reads the same script.
    setup_tcls, eda_tools, external_tools, bridge_port, run_info = (
        await asyncio.gather(
            detect_setup_tcls(sim_dir),
            resolve_eda_tools(shell_env),
            resolve_external_tools(shell_env),
            detect_bridge_port(sim_dir, bridge_tcl),
            detect_run_dir(sim_dir, runner_info),
        )
    )
    patch_result = await _patch_legacy_run_script(sim_dir, runner_info)
    run_dir = run_info["run_dir"]
    script_has_cd = run_info["script_has_cd"]

    default_mode = pick_default_mode(setup_tcls)
    args_format = {default_mode: "-test {test_name} --"}
    if "gate" in setup_tcls:
        args_format["gate"] = "-test {test_name} -gate post --"
    if "ams_rtl" in setup_tcls:
        args_format["ams_rtl"] = "-test {test_name} -ams --"
    if "ams_gate" in setup_tcls:
        args_format["ams_gate"] = "-test {test_name} -amsf -gate post --"

    mode_defaults = {
        "common": {"timeout": 120, "probe_strategy": "all", "extra_args": "", "dump_depth": "all"},
        "rtl": {"timeout": 120, "probe_strategy": "all", "extra_args": "", "dump_depth": "all"},
    }
    if "gate" in setup_tcls:
        mode_defaults["gate"] = {"timeout": 1800, "probe_strategy": "selective", "extra_args": "", "dump_depth": "boundary"}
    if "ams_rtl" in setup_tcls:
        mode_defaults["ams_rtl"] = {"timeout": 3600, "probe_strategy": "selective", "extra_args": "", "dump_depth": "boundary"}
    if "ams_gate" in setup_tcls:
        mode_defaults["ams_gate"] = {"timeout": 3600, "probe_strategy": "selective", "extra_args": "", "dump_depth": "boundary"}

    _sd = shell_quote(sim_dir)
    if tb_type == "uvm":
        test_cmd = (
            f"(grep -rh 'extends uvm_test' {_sd} --include='*.sv' --include='*.svh' || true) "
            f"| grep -oE 'class \\w+' | sed 's/class //' | sort -u"
        )
    elif tb_type == "sv_directed":
        test_cmd = (
            f"(grep -rh '^\\s*program ' {_sd} --include='*.sv' || true) "
            f"| grep -oE 'program \\w+' | sed 's/program //' | sort -u"
        )
    else:
        test_cmd = f"(ls {_sd}/tb_tests/*.v || true) | xargs -I{{}} basename {{}} .v"

    cached_tests = []
    try:
        r = await ssh_run(f"cd {_sd} && {test_cmd}", timeout=30)
        cached_tests = [t.strip() for t in r.strip().splitlines() if t.strip()]
    except (RuntimeError, OSError, asyncio.TimeoutError) as e:
        logger.debug("test discovery failed (non-fatal): %s", e)

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
        sdf_info = await _analyze_sdf_annotate(sim_dir, config["runner"], top_module)
        config["sdf_info"] = sdf_info
    except UserInputRequired as e:
        # top module 자동 탐지 실패 -> sdf_info 없이 진행 (사용자가 재호출 시 top_module 제공)
        logger.warning("SDF analysis skipped — top module not found: %s", e)
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
        f"  bridge_port:    {bridge.get('port', DEFAULT_BRIDGE_PORT)}\n"
        f"  .simvisionrc:   {simvisionrc_result}\n"
        f"\nSaved to: ~/.xcelium_mcp/mcp_registry.json\n"
        f"          {sim_dir}/.mcp_sim_config.json"
    )
