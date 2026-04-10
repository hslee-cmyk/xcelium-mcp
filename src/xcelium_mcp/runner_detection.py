"""Runner / shell / EDA environment detection functions.

Split from env_detection.py — contains runner auto-detection, shell/EDA env
detection, and related helpers.
"""
from __future__ import annotations

__all__ = [
    "auto_detect_runner",
    "ask_user_runner",
    "detect_shell_and_env",
    "extract_script_name",
    "load_or_detect_runner",
    "pick_default_mode",
    "resolve_eda_tools",
    "resolve_external_tools",
]

import asyncio

from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.shell_utils import (
    UserInputRequired,
    login_shell_cmd,
    shell_quote,
    ssh_run,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _detect_env_shell(env_file: str, login_shell: str) -> str:
    """Detect the appropriate shell for sourcing an env file.

    Priority: shebang -> file extension -> content patterns -> login_shell fallback.
    """
    # 1. shebang
    shebang = await ssh_run(f"head -1 {shell_quote(env_file)} || true")
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
    content = await ssh_run(f"head -30 {shell_quote(env_file)} || true")
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
    r = await ssh_run(login_shell_cmd(login_shell, "which xrun"), timeout=10)
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

    # Batch find+grep into single SSH call per directory to reduce round-trips
    async def _find_and_grep(search_dir: str, pat: str) -> list[str]:
        """Find files matching pattern and grep for EDA keywords in one SSH call."""
        r = await ssh_run(
            f"find {shell_quote(search_dir)} -maxdepth 1 \\( -type f -o -type l \\) {pat} "
            f"-exec grep -lE '{kw_grep}' {{}} + || true"
        )
        return [f for f in r.strip().splitlines() if f]

    # Parallelize across search directories
    dir_results = await asyncio.gather(*[
        _find_and_grep(search_dir, pat) for search_dir, pat in search_specs
    ])
    for result_list in dir_results:
        candidates.extend(result_list)

    # Step 3: validate
    for candidate in candidates:
        env_shell = await _detect_env_shell(candidate, login_shell)
        # No '2>/dev/null' inside csh/tcsh -c -- causes Ambiguous redirect error
        r = await ssh_run(f"{env_shell} -c 'source {shell_quote(candidate)} && which xrun'")
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


# ---------------------------------------------------------------------------
# Shell and environment detection
# ---------------------------------------------------------------------------

async def detect_shell_and_env(sim_dir: str, script: str, project_root: str) -> dict:
    """Detect script_shell, login_shell, and EDA env configuration.

    Returns dict with: login_shell, script_shell (or None), env_files, env_shell,
    source_separately.
    """
    # login_shell from $SHELL
    login_shell = (await ssh_run("echo $SHELL")).strip() or "/bin/sh"

    # script_shell from shebang
    script_path = f"{sim_dir}/{script}"
    shebang = await ssh_run(f"head -1 {shell_quote(script_path)} || true")
    script_shell: str | None = None
    if shebang.strip().startswith("#!"):
        script_shell = shebang.strip()[2:].split()[0]

    # EDA env detection -- UserInputRequired propagates to caller
    eda = await _detect_eda_env(sim_dir, project_root, login_shell)

    return {
        "login_shell": login_shell,
        "script_shell": script_shell,
        **eda,
    }


# ---------------------------------------------------------------------------
# Runner auto-detection
# ---------------------------------------------------------------------------

async def auto_detect_runner(sim_dir: str) -> dict:
    """Detect simulation runner from sim_dir contents.

    Priority: Makefile (score 3) > shell script (score 2) > xrun/irun (score 1) > python (score 1).
    Returns dict with: runner, exec_cmd, score, confidence, candidates.
    """
    candidates: list[dict] = []

    # 1. Makefile with sim/test/run target
    r = await ssh_run(f"grep -lE 'sim:|test:|run:' {shell_quote(sim_dir + '/Makefile')} || true")
    if r.strip():
        targets = await ssh_run(
            f"grep -oE '^(sim|test|run|simulate|regression)[^:]*:' {shell_quote(sim_dir + '/Makefile')} "
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
        f"find {shell_quote(sim_dir)} -maxdepth 1 -perm /111 "
        r"\( -name 'run_sim*' -o -name 'run_test*' -o -name '*.sh' \) || true"
    )
    for script in r.strip().splitlines():
        if not script:
            continue
        shebang = await ssh_run(f"head -1 {shell_quote(script)} || true")
        if shebang.strip().startswith("#!"):
            candidates.append({
                "runner": "shell",
                "exec_cmd": f"{script} {{test_name}}",
                "score": 2,
            })

    # 3. *.f filelist + xrun/irun available
    r = await ssh_run(f"(ls {shell_quote(sim_dir)}/*.f || true) | head -1")
    if r.strip():
        tool = await ssh_run("(which xrun || which irun || true) | head -1")
        if tool.strip():
            tool_name = tool.strip().split("/")[-1]
            candidates.append({
                "runner": "xrun",
                "exec_cmd": f"{tool_name} -f {r.strip()} +define+TEST={{test_name}} -run",
                "score": 1,
            })

    # 4. Python runner
    r = await ssh_run(f"(ls {shell_quote(sim_dir + '/run_sim.py')} {shell_quote(sim_dir + '/sim.py')} || true) | head -1")
    if r.strip():
        py = await ssh_run("(which python3 || which python || true) | head -1")
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


async def ask_user_runner(sim_dir: str, candidates: list) -> dict:
    """Surface runner selection/input request when auto-detection is insufficient.

    Always raises UserInputRequired -- caller captures and returns prompt to user.
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
# Main entry point: load or detect runner config
# ---------------------------------------------------------------------------

async def load_or_detect_runner(sim_dir: str) -> dict:
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
    # Lazy import to avoid circular dependency (runner_detection -> discovery -> sim_env_detection -> runner_detection)
    from xcelium_mcp.discovery import run_full_discovery
    await run_full_discovery(sim_dir)
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    raise RuntimeError(f"sim_discover failed for {sim_dir}")


def extract_script_name(exec_cmd: str) -> str:
    """Extract bare script name from auto-detected exec_cmd string."""
    parts = exec_cmd.split()
    if not parts:
        return "unknown"
    name = parts[0].split("/")[-1]  # basename
    # strip common prefixes for make targets
    if name == "make" and len(parts) > 1:
        return parts[1].split("=")[0]  # "sim" from "sim TEST=..."
    return name


def pick_default_mode(setup_tcls: dict[str, str]) -> str:
    """Pick default sim mode. Priority: rtl > gate > ams_rtl > ams_gate."""
    for pref in ["rtl", "gate", "ams_rtl", "ams_gate"]:
        if pref in setup_tcls:
            return pref
    return next(iter(setup_tcls), "rtl")


async def resolve_eda_tools(shell_env: dict) -> dict[str, str]:
    """Resolve EDA tool absolute paths by sourcing detected EDA env.

    All tools come from the same Xcelium installation -- version consistency guaranteed.
    Queries each tool with a separate `which` call to avoid positional assignment errors.
    """
    tools = ["simvisdbutil", "xmsim", "xrun"]
    env_shell = shell_env.get("env_shell", shell_env.get("login_shell", "/bin/sh"))
    env_files = shell_env.get("env_files", [])
    login_shell = shell_env.get("login_shell", "/bin/sh")

    # Batch all which queries into a single subprocess call
    which_cmds = " && ".join(f"echo __TOOL_{t}__=$(which {t})" for t in tools)
    if shell_env.get("source_separately") and env_files:
        source_cmd = " && ".join(f"source {shell_quote(f)}" for f in env_files)
        r = await ssh_run(
            f"{env_shell} -c '{source_cmd} && {which_cmds}'",
            timeout=15,
        )
    else:
        r = await ssh_run(
            login_shell_cmd(login_shell, which_cmds),
            timeout=15,
        )

    result: dict[str, str] = {}
    for line in r.strip().splitlines():
        for tool in tools:
            marker = f"__TOOL_{tool}__="
            if line.startswith(marker):
                path = line[len(marker):].strip()
                if path and "/" in path:
                    result[tool] = path

    if "simvisdbutil" not in result:
        raise RuntimeError(
            "simvisdbutil not found after EDA env sourcing. "
            "Check eda.env or Xcelium installation."
        )

    return result


async def resolve_external_tools(shell_env: dict) -> dict[str, str]:
    """Resolve external utility paths (non-EDA tools).

    Discovers ghostscript (gs), ImageMagick (convert/magick), etc.
    These are optional -- missing tools are silently skipped.

    Note: Unlike _resolve_eda_tools, this does NOT use source_separately env sourcing.
    External tools like gs/convert are system-level utilities expected to be in the
    default PATH, not behind EDA-specific environment setup.
    """
    # Only Linux tools -- Windows variants (gswin64c/gswin32c) handled in screenshot.py local fallback
    tools = ["gs", "convert", "magick"]
    login_shell = shell_env.get("login_shell", "/bin/sh")

    # Batch all which queries into a single subprocess call
    which_cmds = " && ".join(f"echo __TOOL_{t}__=$(which {t})" for t in tools)
    r = await ssh_run(
        login_shell_cmd(login_shell, which_cmds),
        timeout=10,
    )

    result: dict[str, str] = {}
    for line in r.strip().splitlines():
        for tool in tools:
            marker = f"__TOOL_{tool}__="
            if line.startswith(marker):
                path = line[len(marker):].strip()
                if path and "/" in path:
                    result[tool] = path

    return result
