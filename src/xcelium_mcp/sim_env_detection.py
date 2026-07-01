"""Simulation environment / bridge / directory detection functions.

Split from env_detection.py — contains bridge port/TCL detection, VNC display,
run directory, setup TCL classification, sim directory discovery, and TB type
analysis.
"""
from __future__ import annotations

__all__ = [
    "analyze_tb_type",
    "detect_bridge_port",
    "detect_bridge_tcl",
    "detect_run_dir",
    "detect_setup_tcls",
    "detect_vnc_display",
    "discover_sim_dir",
    "_parse_describe_output",
    "_boundaries_from_tcl",
    "_boundaries_from_json",
]

import asyncio
import fnmatch
import json
import re
from pathlib import Path

from xcelium_mcp.shell_utils import (
    UserInputRequired,
    shell_quote,
    shell_run,
)

# Scope path whitelist: only word chars and dots (blocks TCL injection)
_SCOPE_PATH_RE = re.compile(r'^[\w.]+$')


def _parse_scope_item_local(item: str) -> str:
    """Parse a SimVision 'scope show' token to canonical signal path.

    Handles four forms produced by SimVision:
      {{path}[idx]}  → path[idx]
      {path}[idx]    → path[idx]
      {path}         → path
      plain          → plain
    """
    _DOUBLE = re.compile(r'^\{\{(.+?)\}(\[\d+(?::\d+)?\])\}$')
    _ARRAY = re.compile(r'^\{(.+?)\}(\[\d+(?::\d+)?\])$')
    _BRACED = re.compile(r'^\{(.+)\}$', re.DOTALL)
    m = _DOUBLE.match(item)
    if m:
        return m.group(1) + m.group(2)
    m = _ARRAY.match(item)
    if m:
        return m.group(1) + m.group(2)
    m = _BRACED.match(item)
    if m:
        return m.group(1)
    return item


# ---------------------------------------------------------------------------
# Phase 2: boundary auto-detection helpers
# ---------------------------------------------------------------------------

def _parse_describe_output(scope: str, output: str) -> list[str]:
    """Parse 'scope -describe -sort kind' output into port signal names.

    Extracts lines starting with 'input ', 'output ', 'inout ' and returns
    '{scope}.{port}' strings. Bit-range notation ([N:M]) is stripped.
    """
    signals: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        for prefix in ("input ", "output ", "inout "):
            if stripped.startswith(prefix):
                port = stripped[len(prefix):].strip()
                port = re.sub(r'\[.*?\]', '', port).strip()
                if port:
                    signals.append(f"{scope}.{port}")
                break
    return signals


async def _boundaries_from_tcl(
    bridge,
    top_scope: str,
    depth: int = 3,
    block_filter: list[str] | str | None = None,
) -> dict[str, list[str]]:
    """Discover block boundary signals via SimVision TCL bridge (Flow A).

    Uses 'scope -describe -sort kind' for port extraction and 'scope show'
    for child enumeration. Both commands are SimVision-specific — pass the
    SimVision bridge instance.

    Args:
        bridge:       TclBridge connected to SimVision.
        top_scope:    Root scope path to start from (e.g. "top").
        depth:        Maximum recursion depth (1 = only direct children of top).
        block_filter: fnmatch pattern(s) to include only matching scopes.
                      None = include all scopes.
    Returns:
        {scope_path: [port_signals]} mapping.
    """
    if isinstance(block_filter, str):
        block_filter = [block_filter]

    result: dict[str, list[str]] = {}

    async def _recurse(scope: str, remaining: int) -> None:
        if not _SCOPE_PATH_RE.fullmatch(scope):
            return

        # Extract ports for this scope
        try:
            desc = await bridge.execute(
                f"scope -describe -sort kind {scope}", timeout=10.0
            )
            ports = _parse_describe_output(scope, desc)
        except Exception:
            ports = []

        if ports:
            if block_filter is None or any(
                fnmatch.fnmatch(scope, pat) for pat in block_filter
            ):
                result[scope] = ports

        if remaining <= 0:
            return

        # List child scopes
        try:
            show = await bridge.execute(f"scope show {scope}", timeout=10.0)
        except Exception:
            return

        for token in show.split():
            child = _parse_scope_item_local(token)
            if not child:
                continue
            # Build absolute path: relative names lack dots
            child_path = child if '.' in child else f"{scope}.{child}"
            # Skip bit-select leaves (e.g. sig[0]) to avoid double bit-select recursion
            if re.search(r'\[\d+\]$', child_path):
                continue
            if _SCOPE_PATH_RE.fullmatch(child_path):
                await _recurse(child_path, remaining - 1)

    await _recurse(top_scope, depth)
    return result


def _boundaries_from_json(
    json_path,
    top_module: str,
    depth: int = 3,
    block_filter: list[str] | str | None = None,
) -> dict[str, list[str]]:
    """Discover block boundary signals from a Yosys JSON netlist (Flow B).

    Parses the modules/cells hierarchy starting from top_module and returns
    port signals for each discovered sub-module instance.

    Args:
        json_path:    Path to the Yosys JSON netlist file.
        top_module:   Top-level module name in the JSON (used as scope prefix).
        depth:        Maximum recursion depth (1 = direct children of top).
        block_filter: fnmatch pattern(s) to include only matching scope paths.
                      None = include all.
    Returns:
        {instance_path: [port_signals]} mapping.
    """
    if isinstance(block_filter, str):
        block_filter = [block_filter]

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    modules = data.get("modules", {})
    result: dict[str, list[str]] = {}

    def _recurse(module_name: str, instance_path: str, remaining: int) -> None:
        mod = modules.get(module_name)
        if mod is None:
            return

        # Collect signals for this instance (skip the root itself)
        if instance_path != top_module:
            signals: list[str] = []
            for port_name, port_info in mod.get("ports", {}).items():
                if port_info.get("direction") in ("input", "output", "inout"):
                    signals.append(f"{instance_path}.{port_name}")
            if signals:
                if block_filter is None or any(
                    fnmatch.fnmatch(instance_path, pat) for pat in block_filter
                ):
                    result[instance_path] = signals

        if remaining <= 0:
            return

        for cell_name, cell_info in mod.get("cells", {}).items():
            cell_type = cell_info.get("type", "")
            if cell_type in modules:
                _recurse(cell_type, f"{instance_path}.{cell_name}", remaining - 1)

    _recurse(top_module, top_module, depth)
    return result

# ---------------------------------------------------------------------------
# TB type analysis
# ---------------------------------------------------------------------------

async def analyze_tb_type(sim_dir: str) -> str:
    """Heuristic testbench type detection from sim_dir file contents.

    Returns: "uvm" | "ncsim_legacy" | "sv_directed" | "mixed" | "unknown"
    """
    # UVM markers
    _sd = shell_quote(sim_dir)
    r_uvm = await shell_run(
        f"(grep -rl 'uvm_component\\|uvm_test\\|UVM_TEST' {_sd} "
        f"--include='*.sv' --include='*.svh' 2>/dev/null || true) | head -1"
    )
    has_uvm = bool(r_uvm.strip())

    # ncsim_legacy markers: run_sim script + *.f filelist
    r_legacy = await shell_run(f"ls {shell_quote(sim_dir + '/run_sim')} {shell_quote(sim_dir)}/*.f || true")
    has_legacy = bool(r_legacy.strip())

    if has_uvm and has_legacy:
        return "mixed"
    if has_uvm:
        return "uvm"
    if has_legacy:
        return "ncsim_legacy"

    # sv_directed: non-UVM SystemVerilog with interface/program
    r = await shell_run(
        f"(grep -rl 'interface\\|program ' {shell_quote(sim_dir)} --include='*.sv' 2>/dev/null || true) | head -1"
    )
    if r.strip():
        return "sv_directed"

    return "unknown"


# ---------------------------------------------------------------------------
# Simulation directory discovery
# ---------------------------------------------------------------------------

async def discover_sim_dir(hint: str = "") -> list[dict]:
    """Discover all simulation environments under project root.

    Args:
        hint: explicit project root path; empty -> git root -> home fallback
    Returns:
        List of env dicts: {sim_dir, tb_type, runner, exec_cmd, confidence, candidates}
    Raises:
        UserInputRequired: if no environments found (user must provide path)
    """
    # Import here to avoid circular dependency
    from xcelium_mcp.runner_detection import auto_detect_runner

    # 1. determine project root
    if hint:
        project_root = hint
    else:
        r = await shell_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
        project_root = r.strip()

    # 2. find candidate directories by name pattern, maxdepth 3
    patterns = (
        r"-name 'sim*' -o -name 'test*' -o -name 'tb*' "
        r"-o -name 'verif*' -o -name 'bench*' -o -name 'dv'"
    )
    r = await shell_run(
        f"(find {shell_quote(project_root)} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) 2>/dev/null || true) | sort"
    )
    raw = r.strip().splitlines()

    # 3. deduplicate: remove paths that are children of already-included paths
    raw = sorted(set(raw), key=len)
    deduped: list[str] = []
    for path in raw:
        if not any(path.startswith(p + "/") for p in deduped):
            deduped.append(path)

    # 4. analyze each candidate -- parallelize across sim_roots
    async def _analyze_sim_root(sim_root: str) -> list[dict]:
        """Analyze a single sim_root and return list of env dicts."""
        results: list[dict] = []
        r = await shell_run(f"find {shell_quote(sim_root)} -maxdepth 1 -mindepth 1 -type d || true")
        subdirs = [s for s in r.strip().splitlines() if s]

        # Parallelize subdirectory analysis
        async def _analyze_subdir(sub: str) -> dict | None:
            tb_type = await analyze_tb_type(sub)
            if tb_type != "unknown":
                runner_cfg = await auto_detect_runner(sub)
                return {"sim_dir": sub, "tb_type": tb_type, **runner_cfg}
            return None

        if subdirs:
            sub_results = await asyncio.gather(*[_analyze_subdir(sub) for sub in subdirs])
            found_in_sub = False
            for res in sub_results:
                if res is not None:
                    results.append(res)
                    found_in_sub = True
            if not found_in_sub:
                tb_type = await analyze_tb_type(sim_root)
                if tb_type != "unknown":
                    runner_cfg = await auto_detect_runner(sim_root)
                    results.append({"sim_dir": sim_root, "tb_type": tb_type, **runner_cfg})
        else:
            tb_type = await analyze_tb_type(sim_root)
            if tb_type != "unknown":
                runner_cfg = await auto_detect_runner(sim_root)
                results.append({"sim_dir": sim_root, "tb_type": tb_type, **runner_cfg})
        return results

    all_results = await asyncio.gather(*[_analyze_sim_root(sr) for sr in deduped])
    envs: list[dict] = []
    for result_list in all_results:
        envs.extend(result_list)

    # 5. no environments found -> ask user
    if not envs:
        raise UserInputRequired(
            "Could not auto-detect simulation directory.\n"
            "Please enter the simulation root folder path:\n"
            "  (e.g., ~/git.clone/myproject/sim\n"
            "         ~/git.clone/myproject/test/ncsim)"
        )

    return envs


# ---------------------------------------------------------------------------
# Bridge / setup TCL detection
# ---------------------------------------------------------------------------

async def detect_bridge_tcl() -> str:
    """Find mcp_bridge.tcl from xcelium-mcp package installation path.

    Search order:
      1. Python package path: xcelium_mcp.__file__ -> {parent}/tcl/mcp_bridge.tcl
      2. Standard install: /opt/xcelium-mcp/tcl/mcp_bridge.tcl
      3. pip show location fallback
    Raises RuntimeError if not found.
    """
    # 1. Package path (works for both regular and editable install)
    pkg_init = await shell_run(
        "python3 -c \"import xcelium_mcp; print(xcelium_mcp.__file__)\" || true",
        timeout=10,
    )
    if pkg_init.strip():
        candidate = str(Path(pkg_init.strip()).parent.parent / "tcl" / "mcp_bridge.tcl")
        exists = await shell_run(f"test -f {candidate} && echo YES || echo NO", timeout=5)
        if "YES" in exists:
            return candidate

    # 2. Standard path
    exists = await shell_run("test -f /opt/xcelium-mcp/tcl/mcp_bridge.tcl && echo YES || echo NO", timeout=5)
    if "YES" in exists:
        return "/opt/xcelium-mcp/tcl/mcp_bridge.tcl"

    # 3. pip show fallback
    r = await shell_run("(pip3 show xcelium-mcp || true) | grep Location", timeout=10)
    if r.strip():
        loc = r.strip().split(":", 1)[-1].strip()
        candidate = str(Path(loc).parent / "tcl" / "mcp_bridge.tcl")
        exists = await shell_run(f"test -f {candidate} && echo YES || echo NO", timeout=5)
        if "YES" in exists:
            return candidate

    raise RuntimeError(
        "mcp_bridge.tcl not found. Verify xcelium-mcp is installed: pip show xcelium-mcp"
    )


async def detect_setup_tcls(sim_dir: str) -> dict[str, str]:
    """Find setup*.tcl files and classify by simulation mode.

    Classification rules:
      filename contains 'gate' + 'ams' -> 'ams_gate'
      filename contains 'ams' (no gate) -> 'ams_rtl'
      filename contains 'gate' (no ams) -> 'gate'
      otherwise -> 'rtl'

    Returns: {"rtl": "scripts/setup_rtl.tcl", "gate": "scripts/setup_gate.tcl", ...}
    """
    r = await shell_run(
        f"(find {shell_quote(sim_dir + '/scripts')} -maxdepth 1 -name 'setup*.tcl' || true) | sort"
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


async def detect_bridge_port(sim_dir: str, bridge_tcl: str) -> int:
    """Parse bridge port from mcp_bridge.tcl. Default 9876."""
    r = await shell_run(
        f"grep -oE 'variable port [0-9]+' {bridge_tcl} || true"
    )
    if r.strip():
        try:
            return int(r.strip().split()[-1])
        except ValueError:
            pass
    from xcelium_mcp.tcl_bridge import DEFAULT_BRIDGE_PORT
    return DEFAULT_BRIDGE_PORT


# ---------------------------------------------------------------------------
# Run directory detection
# ---------------------------------------------------------------------------

async def detect_run_dir(sim_dir: str, runner_info: dict, run_dir: str = "") -> dict:
    """Detect simulation run directory and whether runner script has internal cd.

    Args:
        run_dir: If provided, skip detection and use this value directly.
    Returns: {"run_dir": str, "script_has_cd": bool}
    """
    from xcelium_mcp.runner_detection import extract_script_name

    # Detect script_has_cd first — needed regardless of whether run_dir hint is provided.
    script_name = extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    cd_targets: list[str] = []
    r = await shell_run(f"(grep -E '^[[:space:]]*cd[[:space:]]+' {shell_quote(script_path)} 2>/dev/null || true) | head -3")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == 'cd' and '$' not in parts[1]:
            cd_target = parts[1].strip("'\"").rstrip("/")
            # Skip navigation-only targets (cd .., cd /, cd ~)
            if cd_target and cd_target not in ("..", "/", "~"):
                cd_targets.append(cd_target)
    script_has_cd = len(cd_targets) > 0

    # Hint path: run_dir explicitly provided — skip directory scanning, return with detected script_has_cd.
    if run_dir:
        return {"run_dir": run_dir, "script_has_cd": script_has_cd}

    candidates: list[str] = list(cd_targets)
    _sd = shell_quote(sim_dir)

    # 1. run*/ directories with cds.lib or hdl.var
    r = await shell_run(
        f"find {_sd} -maxdepth 1 -type d -name 'run*' || true"
    )
    for d in r.strip().splitlines():
        if not d.strip():
            continue
        has_cds = await shell_run(
            f"test -f {shell_quote(d + '/cds.lib')} -o -L {shell_quote(d + '/cds.lib')} -o -f {shell_quote(d + '/hdl.var')} && echo YES || echo NO"
        )
        if "YES" in has_cds:
            name = d.split("/")[-1]
            if name not in candidates:
                candidates.append(name)

    # 2. sim_dir itself -- only if no cd targets found (script doesn't cd to subdirectory)
    if not script_has_cd:
        has_cds = await shell_run(
            f"test -f {shell_quote(sim_dir + '/cds.lib')} -o -L {shell_quote(sim_dir + '/cds.lib')} && echo YES || echo NO"
        )
        if "YES" in has_cds and "." not in candidates:
            candidates.append(".")

    # 4. Single candidate
    if len(candidates) == 1:
        return {"run_dir": candidates[0], "script_has_cd": script_has_cd}

    # 5. Multiple -> ask user
    if len(candidates) > 1:
        choices = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
        raise UserInputRequired(
            "Multiple run directories found. Select one:\n"
            + choices
            + "\nRe-call sim_discover with run_dir=<choice>, e.g. run_dir='"
            + candidates[0] + "'"
        )

    # 6. None -> ask user
    raise UserInputRequired(
        "Could not detect run directory.\n"
        "Enter the directory where xmsim/simvision should run:\n"
        f"  (relative to {sim_dir})\n"
        "  Example: run\n"
        "  Example: ."
    )


async def detect_vnc_display() -> str:
    """Detect current user's VNC display.

    Search order:
      1. vncserver -list -> parse display number
      2. ps -u $USER | grep Xvnc -> extract :N
      3. $DISPLAY env var (skip :0 = physical)
    Returns: ":N" or "" if not found.
    """
    # 1. vncserver -list
    r = await shell_run("(vncserver -list || true) | grep -E '^:'")
    if r.strip():
        display = r.strip().splitlines()[0].split()[0]
        return display

    # 2. Xvnc process
    r = await shell_run("(ps -u $(whoami) -o args || true) | grep Xvnc | grep -v grep | grep -oE ':[0-9]+'")
    if r.strip():
        return r.strip().splitlines()[0]

    # 3. $DISPLAY fallback (skip :0)
    r = await shell_run("echo $DISPLAY")
    if r.strip() and r.strip() != ":0":
        return r.strip()

    return ""
