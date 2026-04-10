"""bridge_lifecycle.py — Bridge simulation lifecycle management.

Extracted from sim_runner.py. Contains: start_bridge_simulation, _start_bridge,
_patch_legacy_run_script, _update_simvisionrc, run_with_dump_window.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re

from xcelium_mcp.batch_runner import resolve_sim_params, validate_extra_args
from xcelium_mcp.discovery import resolve_sim_dir, run_full_discovery
from xcelium_mcp.env_detection import _extract_script_name
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.shell_utils import (
    build_redirect,
    get_user_tmp_dir,
    login_shell_cmd,
    ssh_run,
)
from xcelium_mcp.shell_utils import (
    shell_quote as sq,
)
from xcelium_mcp.tcl_bridge import DEFAULT_BRIDGE_PORT

logger = logging.getLogger(__name__)


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

    # sq() the sed pattern to prevent shell injection from original_tcl/replacement
    sed_pattern = f"s|-input {escaped_original}|-input {replacement}|"
    sed_cmd = f"sed -i -e {sq(sed_pattern)} {_sp}"
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
# Bridge mode dump_window
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
# Simulation start
# ===================================================================


async def start_bridge_simulation(
    test_name: str,
    sim_dir: str = "",
    sim_mode: str = "",
    timeout: int = 120,
    extra_args: str = "",
    bridges=None,
    dump_depth: str = "",
) -> str:
    """Start simulation in bridge (interactive) mode. Registry없으면 sim_discover 자동 호출."""
    validate_extra_args(extra_args)

    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError as e:
        raise RuntimeError(str(e))

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

    return await _start_bridge(
        resolved_dir, config, test_name, setup_tcl, effective_mode, timeout,
        extra_args=extra_args, bridges=bridges, dump_depth=dump_depth,
    )


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
    port = bridge.get("port", DEFAULT_BRIDGE_PORT)
    bridge_tcl = bridge.get("tcl_path", "")
    script = runner.get("script", "run_sim")

    ps = await ssh_run("pgrep -la xmsim || true", timeout=5)
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
            inner_parts.append(f"source {sq(ef)}")
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

    from xcelium_mcp.tcl_bridge import BRIDGE_ERRORS as _BRIDGE_ERRORS
    from xcelium_mcp.tcl_bridge import TclBridge as _TB

    if bridges is not None and bridges.xmsim_raw and bridges.xmsim_raw.connected:
        await bridges.xmsim_raw.disconnect()
        bridges.set_xmsim(None)

    # F-021: TCP connect retry — try direct port connection (0 subprocess spawns)
    # Falls back to scan_ready_files if TCP probe fails after half the timeout
    last_exc: Exception | None = None
    tcp_deadline = effective_timeout // 2  # first half: pure TCP probe
    for i in range(tcp_deadline // 2):
        await asyncio.sleep(2)
        new_bridge = _TB(host="localhost", port=port)
        try:
            ping = await new_bridge.connect()
            if bridges is not None:
                bridges.set_xmsim(new_bridge)
            return (
                f"Simulation started and connected (bridge mode, {sim_mode}).\n"
                f"  test: {test_name}\n"
                f"  setup_tcl: {setup_tcl}\n"
                f"  port: {port}\n"
                f"  ping: {ping}\n"
                f"  log: {log_file}\n\n"
                f"Ready. sim_run, get_signal_value etc. available immediately."
            )
        except _BRIDGE_ERRORS as e:
            last_exc = e
            logger.debug("TCP connect attempt %d failed: %s", i, e)

    # Fallback: scan ready files (handles port mismatch / dynamic port)
    from xcelium_mcp.bridge_manager import scan_ready_files

    for i in range(tcp_deadline // 2):
        await asyncio.sleep(2)
        for actual_port, _btype in await scan_ready_files(target="xmsim"):
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
            except _BRIDGE_ERRORS as e:
                last_exc = e
                logger.debug("bridge connect attempt failed: %s", e)
                continue

    # P-1 fix: kill orphaned xmsim process on timeout
    ps = await ssh_run("pgrep -la xmsim || true", timeout=5)
    if ps.strip():
        logger.warning("Bridge timeout — killing orphaned xmsim: %s", ps.strip())
        await ssh_run("pkill -f xmsim || true", timeout=5)

    log_tail = await ssh_run(f"tail -20 {log_file} || true", timeout=5)
    exc_info = f"\nLast error: {last_exc}" if last_exc else ""
    return f"ERROR: bridge not ready after {timeout}s.{exc_info}\nLog tail:\n{log_tail}"
