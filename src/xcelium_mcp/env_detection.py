"""Environment detection functions for xcelium-mcp.

Extracted from sim_runner.py (Phase 3, v4.2 refactoring).
Contains all environment/shell/runner/directory detection logic.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from xcelium_mcp.sim_runner import (
    ssh_run,
    _sq,
    _login_shell_cmd,
    UserInputRequired,
)
from xcelium_mcp.registry import load_sim_config


async def _detect_env_shell(env_file: str, login_shell: str) -> str:
    """Detect the appropriate shell for sourcing an env file.

    Priority: shebang → file extension → content patterns → login_shell fallback.
    """
    # 1. shebang
    shebang = await ssh_run(f"head -1 {_sq(env_file)} 2>/dev/null")
    if shebang.startswith("#!"):
        return shebang[2:].strip().split()[0]

    # 2. extension
    ext_map = {
        ".tcsh": "/bin/tcsh",
        ".csh": "/bin/csh",
        ".bash": "/bin/bash",
        ".sh": "/bin/sh",
        ".zsh": "/bin/zsh",
        ".ksh": "/bin/ksh",
    }
    for ext, shell in ext_map.items():
        if env_file.endswith(ext):
            return shell

    # 3. content patterns
    content = await ssh_run(f"head -30 {_sq(env_file)} 2>/dev/null")
    if "foreach" in content or "breaksw" in content:
        return "/bin/tcsh"
    if "setenv" in content:
        return "/bin/csh"
    if "[[ " in content:
        return "/bin/bash"
    if "typeset" in content or "autoload" in content:
        return "/bin/zsh"
    if "export " in content:
        return "/bin/bash"

    return login_shell


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
    r = await ssh_run(_login_shell_cmd(login_shell, "which xrun"), timeout=10)
    if r.strip() and "/" in r.strip():
        return {"env_files": [], "env_shell": login_shell, "source_separately": False}

    # Step 2: candidate search
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
        r = await ssh_run(f"find {_sq(search_dir)} -maxdepth 1 \\( -type f -o -type l \\) {pat} 2>/dev/null")
        for f in r.strip().splitlines():
            if not f:
                continue
            r2 = await ssh_run(f"grep -lE '{kw_grep}' {_sq(f)} 2>/dev/null")
            if r2.strip():
                candidates.append(f)

    # Step 3: validate
    for candidate in candidates:
        env_shell = await _detect_env_shell(candidate, login_shell)
        # No '2>/dev/null' inside csh/tcsh -c — causes Ambiguous redirect error
        r = await ssh_run(f"{env_shell} -c 'source {_sq(candidate)} && which xrun'")
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


async def _detect_shell_and_env(sim_dir: str, script: str, project_root: str) -> dict:
    """Detect script_shell, login_shell, and EDA env configuration.

    Returns dict with: login_shell, script_shell (or None), env_files, env_shell,
    source_separately.
    """
    # login_shell from $SHELL
    login_shell = (await ssh_run("echo $SHELL")).strip() or "/bin/sh"

    # script_shell from shebang
    script_path = f"{sim_dir}/{script}"
    shebang = await ssh_run(f"head -1 {_sq(script_path)} 2>/dev/null")
    script_shell: str | None = None
    if shebang.strip().startswith("#!"):
        script_shell = shebang.strip()[2:].split()[0]

    # EDA env detection — UserInputRequired propagates to caller
    eda = await _detect_eda_env(sim_dir, project_root, login_shell)

    return {
        "login_shell": login_shell,
        "script_shell": script_shell,
        **eda,
    }


# ---------------------------------------------------------------------------
# Runner auto-detection
# ---------------------------------------------------------------------------

async def _auto_detect_runner(sim_dir: str) -> dict:
    """Detect simulation runner from sim_dir contents.

    Priority: Makefile (score 3) > shell script (score 2) > xrun/irun (score 1) > python (score 1).
    Returns dict with: runner, exec_cmd, score, confidence, candidates.
    """
    candidates: list[dict] = []

    # 1. Makefile with sim/test/run target
    r = await ssh_run(f"grep -lE 'sim:|test:|run:' {_sq(sim_dir + '/Makefile')} 2>/dev/null")
    if r.strip():
        targets = await ssh_run(
            f"grep -oE '^(sim|test|run|simulate|regression)[^:]*:' {_sq(sim_dir + '/Makefile')} "
            f"| tr -d ':'"
        )
        best_target = targets.strip().splitlines()[0] if targets.strip() else "sim"
        candidates.append({
            "runner": "make",
            "exec_cmd": f"make {best_target} TEST={{test_name}}",
            "score": 3,
        })

    # 2. Executable shell scripts with recognized names
    r = await ssh_run(
        f"find {_sq(sim_dir)} -maxdepth 1 -perm /111 "
        r"\( -name 'run_sim*' -o -name 'run_test*' -o -name '*.sh' \) 2>/dev/null"
    )
    for script in r.strip().splitlines():
        if not script:
            continue
        shebang = await ssh_run(f"head -1 {_sq(script)} 2>/dev/null")
        if shebang.strip().startswith("#!"):
            candidates.append({
                "runner": "shell",
                "exec_cmd": f"{script} {{test_name}}",
                "score": 2,
            })

    # 3. *.f filelist + xrun/irun available
    r = await ssh_run(f"ls {_sq(sim_dir)}/*.f 2>/dev/null | head -1")
    if r.strip():
        tool = await ssh_run("which xrun 2>/dev/null || which irun 2>/dev/null | head -1")
        if tool.strip():
            tool_name = tool.strip().split("/")[-1]
            candidates.append({
                "runner": "xrun",
                "exec_cmd": f"{tool_name} -f {r.strip()} +define+TEST={{test_name}} -run",
                "score": 1,
            })

    # 4. Python runner
    r = await ssh_run(f"ls {_sq(sim_dir + '/run_sim.py')} {_sq(sim_dir + '/sim.py')} 2>/dev/null | head -1")
    if r.strip():
        py = await ssh_run("which python3 2>/dev/null || which python 2>/dev/null | head -1")
        py_cmd = py.strip().split("/")[-1] if py.strip() else "python3"
        candidates.append({
            "runner": "python",
            "exec_cmd": f"{py_cmd} {r.strip()} --test {{test_name}}",
            "score": 1,
        })

    if not candidates:
        return {"confidence": "none", "candidates": []}

    best = max(candidates, key=lambda x: x["score"])
    top_score = best["score"]
    top_candidates = [c for c in candidates if c["score"] == top_score]
    confidence = "high" if len(top_candidates) == 1 else "ambiguous"
    return {**best, "confidence": confidence, "candidates": candidates}


async def _ask_user_runner(sim_dir: str, candidates: list) -> dict:
    """Surface runner selection/input request when auto-detection is insufficient.

    Always raises UserInputRequired — caller captures and returns prompt to user.
    """
    if not candidates:
        raise UserInputRequired(
            f"Could not auto-detect simulation runner in:\n  {sim_dir}\n\n"
            "Please enter the run command (use {test_name} as placeholder):\n"
            "  Example: ./run_sim -test {test_name}\n"
            "  Example: make sim TEST={test_name}\n"
            "  Example: xrun -f sim.f +define+TEST={test_name} -run"
        )

    options = "\n".join(
        f"{i+1}. [{c['runner']}] {c['exec_cmd']}" for i, c in enumerate(candidates)
    )
    raise UserInputRequired(
        f"Multiple runners detected in {sim_dir}. Select one:\n{options}\n"
        f"{len(candidates)+1}. Enter custom command"
    )


# ---------------------------------------------------------------------------
# TB type analysis
# ---------------------------------------------------------------------------

async def _analyze_tb_type(sim_dir: str) -> str:
    """Heuristic testbench type detection from sim_dir file contents.

    Returns: "uvm" | "ncsim_legacy" | "sv_directed" | "mixed" | "unknown"
    """
    # UVM markers
    _sd = _sq(sim_dir)
    r_uvm = await ssh_run(
        f"grep -rl 'uvm_component\\|uvm_test\\|UVM_TEST' {_sd} "
        f"--include='*.sv' --include='*.svh' 2>/dev/null | head -1"
    )
    has_uvm = bool(r_uvm.strip())

    # ncsim_legacy markers: run_sim script + *.f filelist
    r_legacy = await ssh_run(f"ls {_sq(sim_dir + '/run_sim')} {_sq(sim_dir)}/*.f 2>/dev/null")
    has_legacy = bool(r_legacy.strip())

    if has_uvm and has_legacy:
        return "mixed"
    if has_uvm:
        return "uvm"
    if has_legacy:
        return "ncsim_legacy"

    # sv_directed: non-UVM SystemVerilog with interface/program
    r = await ssh_run(
        f"grep -rl 'interface\\|program ' {_sq(sim_dir)} --include='*.sv' 2>/dev/null | head -1"
    )
    if r.strip():
        return "sv_directed"

    return "unknown"


# ---------------------------------------------------------------------------
# Simulation directory discovery
# ---------------------------------------------------------------------------

async def _discover_sim_dir(hint: str = "") -> list[dict]:
    """Discover all simulation environments under project root.

    Args:
        hint: explicit project root path; empty → git root → home fallback
    Returns:
        List of env dicts: {sim_dir, tb_type, runner, exec_cmd, confidence, candidates}
    Raises:
        UserInputRequired: if no environments found (user must provide path)
    """
    # 1. determine project root
    if hint:
        project_root = hint
    else:
        r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
        project_root = r.strip()

    # 2. find candidate directories by name pattern, maxdepth 3
    patterns = (
        r"-name 'sim*' -o -name 'test*' -o -name 'tb*' "
        r"-o -name 'verif*' -o -name 'bench*' -o -name 'dv'"
    )
    r = await ssh_run(
        f"find {_sq(project_root)} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) 2>/dev/null | sort"
    )
    raw = r.strip().splitlines()

    # 3. deduplicate: remove paths that are children of already-included paths
    raw = sorted(set(raw), key=len)
    deduped: list[str] = []
    for path in raw:
        if not any(path.startswith(p + "/") for p in deduped):
            deduped.append(path)

    # 4. analyze each candidate
    envs: list[dict] = []
    for sim_root in deduped:
        r = await ssh_run(f"find {_sq(sim_root)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null")
        subdirs = [s for s in r.strip().splitlines() if s]
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

    # 5. no environments found → ask user
    if not envs:
        raise UserInputRequired(
            "Could not auto-detect simulation directory.\n"
            "Please enter the simulation root folder path:\n"
            "  (e.g., ~/git.clone/myproject/sim\n"
            "         ~/git.clone/myproject/test/ncsim)"
        )

    return envs


# ---------------------------------------------------------------------------
# Main entry point: load or detect runner config
# ---------------------------------------------------------------------------

async def _load_or_detect_runner(sim_dir: str) -> dict:
    """Return runner config for sim_dir.

    v4: Tier 2 self-detection removed. Config not found -> sim_discover delegation.

    Tier 1: load .mcp_sim_config.json (explicit config wins).
    Tier 2: (removed) -> sim_discover auto-call.
    """
    # Tier 1: explicit config
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    # v4: delegate to sim_discover instead of self-detecting
    # Lazy import to avoid circular dependency (sim_runner → env_detection → sim_runner)
    from xcelium_mcp.sim_runner import run_full_discovery
    await run_full_discovery(sim_dir)
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    raise RuntimeError(f"sim_discover failed for {sim_dir}")


def _extract_script_name(exec_cmd: str) -> str:
    """Extract bare script name from auto-detected exec_cmd string."""
    # exec_cmd examples:
    #   "make sim TEST={test_name}"     → "Makefile" is implied, return "sim"
    #   "/abs/path/run_sim_mcp {test_name}" → "run_sim_mcp"
    #   "xrun -f sim.f ..."             → "xrun"
    parts = exec_cmd.split()
    if not parts:
        return "unknown"
    name = parts[0].split("/")[-1]  # basename
    # strip common prefixes for make targets
    if name == "make" and len(parts) > 1:
        return parts[1].split("=")[0]  # "sim" from "sim TEST=..."
    return name


# ---------------------------------------------------------------------------
# Bridge / setup TCL detection
# ---------------------------------------------------------------------------

async def _detect_bridge_tcl() -> str:
    """Find mcp_bridge.tcl from xcelium-mcp package installation path.

    Search order:
      1. Python package path: xcelium_mcp.__file__ -> {parent}/tcl/mcp_bridge.tcl
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


async def _detect_setup_tcls(sim_dir: str) -> dict[str, str]:
    """Find setup*.tcl files and classify by simulation mode.

    Classification rules:
      filename contains 'gate' + 'ams' -> 'ams_gate'
      filename contains 'ams' (no gate) -> 'ams_rtl'
      filename contains 'gate' (no ams) -> 'gate'
      otherwise -> 'rtl'

    Returns: {"rtl": "scripts/setup_rtl.tcl", "gate": "scripts/setup_gate.tcl", ...}
    """
    r = await ssh_run(
        f"find {_sq(sim_dir + '/scripts')} -maxdepth 1 -name 'setup*.tcl' 2>/dev/null | sort"
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


# ---------------------------------------------------------------------------
# Run directory detection
# ---------------------------------------------------------------------------

async def _detect_run_dir(sim_dir: str, runner_info: dict) -> dict:
    """Detect simulation run directory and whether runner script has internal cd.

    Returns: {"run_dir": str, "script_has_cd": bool}
    """
    candidates: list[str] = []
    script_has_cd = False
    _sd = _sq(sim_dir)

    # 1. run*/ directories with cds.lib or hdl.var
    r = await ssh_run(
        f"find {_sd} -maxdepth 1 -type d -name 'run*' 2>/dev/null"
    )
    for d in r.strip().splitlines():
        if not d.strip():
            continue
        has_cds = await ssh_run(
            f"test -f {_sq(d + '/cds.lib')} -o -L {_sq(d + '/cds.lib')} -o -f {_sq(d + '/hdl.var')} && echo YES || echo NO"
        )
        if "YES" in has_cds:
            candidates.append(d.split("/")[-1])

    # 2. Parse 'cd' from runner script → detect script_has_cd
    script_name = _extract_script_name(runner_info.get("exec_cmd", ""))
    script_path = f"{sim_dir}/{script_name}"
    cd_targets: list[str] = []
    r = await ssh_run(f"grep -E '^[[:space:]]*cd[[:space:]]+' {_sq(script_path)} 2>/dev/null | head -3")
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

    # 3. sim_dir itself — only if no cd targets found (script doesn't cd to subdirectory)
    if not script_has_cd:
        has_cds = await ssh_run(
            f"test -f {_sq(sim_dir + '/cds.lib')} -o -L {_sq(sim_dir + '/cds.lib')} && echo YES || echo NO"
        )
        if "YES" in has_cds and "." not in candidates:
            candidates.append(".")

    # 4. Single candidate
    if len(candidates) == 1:
        return {"run_dir": candidates[0], "script_has_cd": script_has_cd}

    # 5. Multiple → ask user
    if len(candidates) > 1:
        raise UserInputRequired(
            f"Multiple run directories found. Select one:\n"
            + "\n".join(f"  {i+1}. {c}" for i, c in enumerate(candidates))
        )

    # 6. None → ask user
    raise UserInputRequired(
        "Could not detect run directory.\n"
        "Enter the directory where xmsim/simvision should run:\n"
        f"  (relative to {sim_dir})\n"
        "  Example: run\n"
        "  Example: ."
    )


async def _detect_vnc_display() -> str:
    """Detect current user's VNC display.

    Search order:
      1. vncserver -list → parse display number
      2. ps -u $USER | grep Xvnc → extract :N
      3. $DISPLAY env var (skip :0 = physical)
    Returns: ":N" or "" if not found.
    """
    # 1. vncserver -list
    r = await ssh_run("vncserver -list 2>/dev/null | grep -E '^:'")
    if r.strip():
        display = r.strip().splitlines()[0].split()[0]
        return display

    # 2. Xvnc process
    r = await ssh_run("ps -u $(whoami) -o args 2>/dev/null | grep Xvnc | grep -v grep | grep -oE ':[0-9]+'")
    if r.strip():
        return r.strip().splitlines()[0]

    # 3. $DISPLAY fallback (skip :0)
    r = await ssh_run("echo $DISPLAY")
    if r.strip() and r.strip() != ":0":
        return r.strip()

    return ""
