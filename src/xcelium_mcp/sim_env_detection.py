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
]

import asyncio
from pathlib import Path

from xcelium_mcp.shell_utils import (
    UserInputRequired,
    shell_quote,
    shell_run,
)

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
        f"--include='*.sv' --include='*.svh' || true) | head -1"
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
        f"(grep -rl 'interface\\|program ' {shell_quote(sim_dir)} --include='*.sv' || true) | head -1"
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
        r = await shell_run("git rev-parse --show-toplevel || echo ~")
        project_root = r.strip()

    # 2. find candidate directories by name pattern, maxdepth 3
    patterns = (
        r"-name 'sim*' -o -name 'test*' -o -name 'tb*' "
        r"-o -name 'verif*' -o -name 'bench*' -o -name 'dv'"
    )
    r = await shell_run(
        f"(find {shell_quote(project_root)} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) || true) | sort"
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

async def detect_run_dir(sim_dir: str, runner_info: dict) -> dict:
    """Detect simulation run directory and whether runner script has internal cd.

    Returns: {"run_dir": str, "script_has_cd": bool}
    """
    from xcelium_mcp.runner_detection import extract_script_name

    candidates: list[str] = []
    script_has_cd = False
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
            candidates.append(d.split("/")[-1])

    # 2. Parse 'cd' from runner script -> detect script_has_cd
    script_name = extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    cd_targets: list[str] = []
    r = await shell_run(f"(grep -E '^[[:space:]]*cd[[:space:]]+' {shell_quote(script_path)} || true) | head -3")
    for line in r.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and '$' not in parts[1]:
            cd_target = parts[1].strip("'\"").rstrip("/")
            # Skip navigation-only targets (cd .., cd /, cd ~)
            if cd_target and cd_target not in ("..", "/", "~"):
                cd_targets.append(cd_target)
                if cd_target not in candidates:
                    candidates.append(cd_target)

    script_has_cd = len(cd_targets) > 0

    # 3. sim_dir itself -- only if no cd targets found (script doesn't cd to subdirectory)
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
        raise UserInputRequired(
            "Multiple run directories found. Select one:\n"
            + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
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
