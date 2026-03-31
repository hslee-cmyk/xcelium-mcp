"""sim_runner.py — Script Discovery, Environment Detection, and Sim Lifecycle for xcelium-mcp v4.

Architecture note:
  xcelium-mcp server runs ON cloud0 via SSH stdio transport.
  ssh_run() is a LOCAL asyncio subprocess on cloud0 — NOT a remote SSH hop.
  All file paths in this module refer to cloud0 local filesystem.

v4 changes:
  - ssh_run: log_file parameter + 2>&1 guard (tcsh safety)
  - _build_redirect: tcsh-safe redirect helper
  - run_full_discovery: unified environment detection (Single Source of Truth)
  - _update_registry_from_config: replaces v3 _update_registry_env
  - config_action: mcp_config dot-notation helper
  - start_simulation: bridge/batch sim start via registry
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# User-input exception (raised when auto-detection needs human decision)
# ---------------------------------------------------------------------------

class UserInputRequired(Exception):
    """Raised when auto-detection fails and user input is needed.

    Caller should surface `prompt` to the user via MCP tool response,
    then call the appropriate function again with the user-provided value.
    """

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        super().__init__(prompt)


# ---------------------------------------------------------------------------
# Local subprocess runner (cloud0-local commands)
# ---------------------------------------------------------------------------

def _build_redirect(log_path: str) -> str:
    """Build shell redirect suffix safe for both bash and tcsh.

    NEVER use '2>&1' — tcsh interprets '&1' as filename, creating file '1'.
    Use '>& file' which works in both bash and tcsh.
    """
    return f">& {log_path}"


async def ssh_run(cmd: str, timeout: float = 60.0, log_file: str = "") -> str:
    """Run a shell command as a local subprocess.

    Since xcelium-mcp runs on cloud0, this is a local asyncio subprocess —
    not an SSH call. Combined stdout+stderr is returned as a single string.

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


def _login_shell_cmd(login_shell: str, cmd: str) -> str:
    """Build a command that runs in login shell environment.

    tcsh 6.18 (CentOS 7) does not support '-l -c' combination.
    Workaround: source ~/.tcshrc (or ~/.cshrc) explicitly before the command.
    For bash: '-l -c' works fine.
    """
    if "tcsh" in login_shell or "csh" in login_shell:
        # tcsh/csh: source rc file explicitly
        return f"{login_shell} -c 'source ~/.tcshrc >& /dev/null; {cmd}'"
    # bash/sh/zsh: -l -c works
    return f"{login_shell} -l -c '{cmd}'"


# ---------------------------------------------------------------------------
# Registry and config file I/O
# ---------------------------------------------------------------------------

_REGISTRY_PATH = Path.home() / ".xcelium_mcp" / "mcp_registry.json"


def load_registry() -> dict:
    """Load mcp_registry.json. Returns empty structure if not found."""
    if _REGISTRY_PATH.exists():
        return json.loads(_REGISTRY_PATH.read_text())
    return {"version": 1, "projects": {}}


def save_registry(registry: dict) -> None:
    """Save mcp_registry.json, creating parent directory as needed."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


async def load_sim_config(sim_dir: str) -> dict | None:
    """Load .mcp_sim_config.json from sim_dir. Returns None if not found."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


async def save_sim_config(sim_dir: str, config: dict) -> None:
    """Save .mcp_sim_config.json to sim_dir."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    path.write_text(json.dumps(config, indent=2))


def _write_json(path, data: dict) -> None:
    """Write JSON file. Works with both Path and str."""
    Path(str(path)).write_text(json.dumps(data, indent=2))


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


# ---------------------------------------------------------------------------
# ExecInfo dataclass + _resolve_exec_cmd
# ---------------------------------------------------------------------------

@dataclass
class ExecInfo:
    cmd: str               # resolved execution command string
    needs_test_name: bool  # True  → {test_name} substitution needed before exec
                           # False → command complete as-is (regression_script builtin)


def _resolve_exec_cmd(runner: dict, regression: bool = False) -> ExecInfo:
    """Derive exec_cmd from runner fields at runtime.

    exec_cmd is never stored in .mcp_sim_config.json — always derived here
    so that changing `script` automatically updates the command.

    Args:
        runner: Runner sub-dict from .mcp_sim_config.json
        regression: True → derive regression command
    Returns:
        ExecInfo with resolved cmd and needs_test_name flag
    """
    # 1. override field takes precedence
    override_key = "regression_exec_cmd_override" if regression else "exec_cmd_override"
    if override_key in runner:
        return ExecInfo(cmd=runner[override_key], needs_test_name=False)

    # 2. select script + determine needs_test_name
    if regression:
        if "regression_script" in runner:
            # regression_script handles all tests internally → run once
            script = runner["regression_script"]
            needs_test_name = False
        else:
            # no regression_script → loop over test_list with single-test script
            script = runner["script"]
            needs_test_name = True
    else:
        script = runner["script"]
        needs_test_name = True

    # 3. build script_run (shebang-aware)
    suffix = " {test_name}" if needs_test_name else ""
    if runner.get("script_shell"):          # shebang present → OS handles interpreter
        script_run = f"./{script}{suffix}"
    else:                                   # no shebang → invoke via login_shell
        script_run = f"{runner['login_shell']} ./{script}{suffix}"

    # 4. build full cmd (env sourcing)
    if runner.get("source_separately"):
        sources = " && ".join(f"source {f}" for f in runner.get("env_files", []))
        env_shell = runner.get("env_shell", runner["login_shell"])
        cmd = f"{env_shell} -c '{sources} && {script_run}'"
    else:
        cmd = _login_shell_cmd(runner["login_shell"], script_run)

    return ExecInfo(cmd=cmd, needs_test_name=needs_test_name)


# ---------------------------------------------------------------------------
# Shell and EDA environment detection
# ---------------------------------------------------------------------------

async def _detect_env_shell(env_file: str, login_shell: str) -> str:
    """Detect the appropriate shell for sourcing an env file.

    Priority: shebang → file extension → content patterns → login_shell fallback.
    """
    # 1. shebang
    shebang = await ssh_run(f"head -1 {env_file} 2>/dev/null")
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
    content = await ssh_run(f"head -30 {env_file} 2>/dev/null")
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
        r = await ssh_run(f"find {search_dir} -maxdepth 1 \\( -type f -o -type l \\) {pat} 2>/dev/null")
        for f in r.strip().splitlines():
            if not f:
                continue
            r2 = await ssh_run(f"grep -lE '{kw_grep}' {f} 2>/dev/null")
            if r2.strip():
                candidates.append(f)

    # Step 3: validate
    for candidate in candidates:
        env_shell = await _detect_env_shell(candidate, login_shell)
        r = await ssh_run(f"{env_shell} -c 'source {candidate} && which xrun 2>/dev/null'")
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
    shebang = await ssh_run(f"head -1 {script_path} 2>/dev/null")
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
    r = await ssh_run(f"grep -lE 'sim:|test:|run:' {sim_dir}/Makefile 2>/dev/null")
    if r.strip():
        targets = await ssh_run(
            f"grep -oE '^(sim|test|run|simulate|regression)[^:]*:' {sim_dir}/Makefile "
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
        f"find {sim_dir} -maxdepth 1 -perm /111 "
        r"\( -name 'run_sim*' -o -name 'run_test*' -o -name '*.sh' \) 2>/dev/null"
    )
    for script in r.strip().splitlines():
        if not script:
            continue
        shebang = await ssh_run(f"head -1 {script} 2>/dev/null")
        if shebang.strip().startswith("#!"):
            candidates.append({
                "runner": "shell",
                "exec_cmd": f"{script} {{test_name}}",
                "score": 2,
            })

    # 3. *.f filelist + xrun/irun available
    r = await ssh_run(f"ls {sim_dir}/*.f 2>/dev/null | head -1")
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
    r = await ssh_run(f"ls {sim_dir}/run_sim.py {sim_dir}/sim.py 2>/dev/null | head -1")
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
    r_uvm = await ssh_run(
        f"grep -rl 'uvm_component\\|uvm_test\\|UVM_TEST' {sim_dir} "
        f"--include='*.sv' --include='*.svh' 2>/dev/null | head -1"
    )
    has_uvm = bool(r_uvm.strip())

    # ncsim_legacy markers: run_sim script + *.f filelist
    r_legacy = await ssh_run(f"ls {sim_dir}/run_sim {sim_dir}/*.f 2>/dev/null")
    has_legacy = bool(r_legacy.strip())

    if has_uvm and has_legacy:
        return "mixed"
    if has_uvm:
        return "uvm"
    if has_legacy:
        return "ncsim_legacy"

    # sv_directed: non-UVM SystemVerilog with interface/program
    r = await ssh_run(
        f"grep -rl 'interface\\|program ' {sim_dir} --include='*.sv' 2>/dev/null | head -1"
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
        f"find {project_root} -maxdepth 3 -mindepth 1 -type d \\( {patterns} \\) 2>/dev/null | sort"
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
        r = await ssh_run(f"find {sim_root} -maxdepth 1 -mindepth 1 -type d 2>/dev/null")
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
# Batch / Regression execution helpers (Phase 2)
# ---------------------------------------------------------------------------

async def _get_default_sim_dir() -> str:
    """Return the default simulation directory from mcp_registry.json.

    v4: Falls back to run_full_discovery() (not _discover_sim_dir directly)
    to enforce single entry point for all environment detection.
    Returns "" if nothing found.
    """
    registry = load_registry()
    for project in registry.get("projects", {}).values():
        for sim_dir, env in project.get("environments", {}).items():
            if env.get("is_default"):
                return sim_dir

    # v4: delegate to run_full_discovery (single entry point for detection)
    try:
        await run_full_discovery()
    except (UserInputRequired, RuntimeError):
        return ""

    # Re-read registry after discovery
    registry = load_registry()
    for project in registry.get("projects", {}).values():
        for sim_dir, env in project.get("environments", {}).items():
            if env.get("is_default"):
                return sim_dir
    return ""


async def _run_batch_single(
    sim_dir: str,
    test_name: str,
    runner: dict,
    rename_dump: bool = False,
    run_duration: str = "",
    timeout: int = 600,
) -> str:
    """Execute a single simulation test and return combined log output.

    Strategy (P2-8 SSH screen hybrid):
      timeout <= 120 → direct ssh_run (synchronous, result returned immediately)
      timeout  > 120 → screen session + log polling

    SHM overwrite prevention:
      Method 6-A (default): injects TEST_NAME env var so setup tcl uses
          $env(TEST_NAME) to name the SHM file.
      Method 6-B (rename_dump=True): moves dump/ci_top.shm to
          dump/ci_top_{test_name}.shm after simulation completes.
    """
    import time as _time
    from xcelium_mcp import checkpoint_manager as _ckpt_mgr

    # P4-4: Recompile detection — invalidate stale checkpoints before run
    stale_removed = _ckpt_mgr.invalidate_stale_checkpoints(
        sim_dir, reason=f"pre-run recompile check for {test_name}"
    )

    info = _resolve_exec_cmd(runner, regression=False)
    cmd = info.cmd.format(test_name=test_name) if info.needs_test_name else info.cmd

    # Method 6-A: inject TEST_NAME for SHM file naming
    env_prefix = f"TEST_NAME={test_name} "

    if timeout <= 120:
        # --- Direct ssh_run ---
        full_cmd = f"cd {sim_dir} && {env_prefix}{cmd}"
        result = await ssh_run(full_cmd, timeout=float(timeout))

        if rename_dump:
            # Method 6-B fallback
            mv_cmd = (
                f"cd {sim_dir} && "
                f"if [ -d dump/ci_top.shm ]; then "
                f"mv dump/ci_top.shm dump/ci_top_{test_name}.shm; fi"
            )
            await ssh_run(mv_cmd, timeout=30.0)

        prefix = (
            f"[Stale checkpoints removed: {stale_removed}]\n" if stale_removed else ""
        )
        return prefix + result

    # --- Screen session + log polling ---
    ts = int(_time.time())
    session = f"mcp_single_{ts}"
    log_file = f"/tmp/screen_{session}.log"
    login_shell = runner.get("login_shell", "/bin/sh")

    # Start screen session with log capture
    await ssh_run(
        f"screen -dmS {session} -L -Logfile {log_file} {login_shell} -l",
        timeout=15.0,
    )
    await ssh_run(f"screen -S {session} -X stuff 'cd {sim_dir}\\n'", timeout=10.0)
    await ssh_run(
        f"screen -S {session} -X stuff 'setenv TEST_NAME {test_name}\\n'",
        timeout=10.0,
    )
    await ssh_run(f"screen -S {session} -X stuff '{cmd}\\n'", timeout=10.0)

    # Poll for completion
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        log = await ssh_run(f"tail -5 {log_file} 2>/dev/null")
        if any(kw in log for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")):
            break
        await asyncio.sleep(10)

    # Method 6-B fallback
    if rename_dump:
        await ssh_run(
            f"screen -S {session} -X stuff "
            f"'if [ -d {sim_dir}/dump/ci_top.shm ]; then "
            f"mv {sim_dir}/dump/ci_top.shm {sim_dir}/dump/ci_top_{test_name}.shm; fi\\n'",
            timeout=10.0,
        )

    # Cleanup + collect results
    await ssh_run(f"screen -X -S {session} quit 2>/dev/null", timeout=10.0)
    result = await ssh_run(
        f"grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {log_file} 2>/dev/null | tail -30"
    )
    return result


async def _run_batch_regression(
    sim_dir: str,
    test_list: list[str],
    runner: dict,
    from_checkpoint: str = "",
    rename_dump: bool = False,
) -> str:
    """Execute regression tests via screen session with per-test polling.

    Always uses screen (P2-8: regression always detaches).

    needs_test_name=False → regression_script handles all tests → 1 cmd, poll REGRESSION_COMPLETE
    needs_test_name=True  → iterate test_list, per-test poll, method 6-A/B for SHM naming
    """
    import time as _time

    ts = int(_time.time())
    session = f"mcp_regression_{ts}"
    log_file = f"/tmp/screen_{session}.log"
    login_shell = runner.get("login_shell", "/bin/sh")

    # Check for existing mcp_regression screen sessions
    existing = await ssh_run("screen -ls 2>/dev/null | grep mcp_regression || echo ''")
    if existing.strip():
        # Kill stale sessions
        await ssh_run(
            "screen -ls 2>/dev/null | grep mcp_regression "
            "| awk '{print $1}' | xargs -I{} screen -X -S {} quit 2>/dev/null || true"
        )

    # Start screen session with log capture
    await ssh_run(
        f"screen -dmS {session} -L -Logfile {log_file} {login_shell} -l",
        timeout=15.0,
    )
    await ssh_run(f"screen -S {session} -X stuff 'cd {sim_dir}\\n'", timeout=10.0)

    info = _resolve_exec_cmd(runner, regression=True)

    if not info.needs_test_name:
        # regression_script handles all tests internally → 1 cmd
        await ssh_run(
            f"screen -S {session} -X stuff '{info.cmd}\\n'", timeout=10.0
        )
        # Poll for overall completion
        for _ in range(360):  # max ~1 hour
            log = await ssh_run(f"tail -5 {log_file} 2>/dev/null")
            if "REGRESSION_COMPLETE" in log or "All tests done" in log:
                break
            await asyncio.sleep(10)

    else:
        # Per-test loop — needs {test_name} substitution
        for test_name in test_list:
            # Method 6-A: set TEST_NAME env var for SHM file naming
            await ssh_run(
                f"screen -S {session} -X stuff 'setenv TEST_NAME {test_name}\\n'",
                timeout=10.0,
            )
            cmd = info.cmd.format(test_name=test_name)
            await ssh_run(
                f"screen -S {session} -X stuff '{cmd}\\n'", timeout=10.0
            )

            # Per-test completion poll (max 10 min each)
            for _ in range(60):
                log = await ssh_run(f"tail -3 {log_file} 2>/dev/null")
                if any(kw in log for kw in ("$finish", "COMPLETE", "PASS", "FAIL")):
                    break
                await asyncio.sleep(10)

            # Method 6-B fallback
            if rename_dump:
                await ssh_run(
                    f"screen -S {session} -X stuff "
                    f"'if [ -d {sim_dir}/dump/ci_top.shm ]; then "
                    f"mv {sim_dir}/dump/ci_top.shm "
                    f"{sim_dir}/dump/ci_top_{test_name}.shm; fi\\n'",
                    timeout=10.0,
                )

    # Cleanup screen session
    await ssh_run(f"screen -X -S {session} quit 2>/dev/null", timeout=10.0)

    # Parse final results
    raw = await ssh_run(
        f"grep -E 'PASS|FAIL' {log_file} 2>/dev/null | tail -100"
    )
    pass_count = raw.count("PASS")
    fail_count = raw.count("FAIL")
    total = len(test_list)

    summary = f"{pass_count}/{total} tests PASS, {fail_count} FAIL"
    details = raw[:2000] if raw.strip() else "(no PASS/FAIL lines found in log)"
    return f"{summary}\n\nLog ({log_file}):\n{details}"


# ===========================================================================
# v4: Unified Environment Detection Functions
# ===========================================================================


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
# v4: Legacy run script patching (D-10)
# ---------------------------------------------------------------------------


async def _patch_legacy_run_script(sim_dir: str, runner_info: dict) -> str:
    """Patch legacy run script to support MCP_INPUT_TCL env var override.

    Replaces: xmsim -input <hardcoded.tcl> ...
    With:     xmsim -input ${MCP_INPUT_TCL:-<hardcoded.tcl>} ...

    Returns: patch status string.
    """
    import re

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
    match = re.search(r'-input\s+(\S+)', r.strip())
    if not match:
        return "could not parse -input argument — manual patch needed"

    original_tcl = match.group(1)
    escaped_original = re.escape(original_tcl)
    replacement = f'${{MCP_INPUT_TCL:-{original_tcl}}}'

    # Apply sed patch
    sed_cmd = f"sed -i 's|-input {escaped_original}|-input {replacement}|' {script_path}"
    await ssh_run(sed_cmd, timeout=10)

    # Verify
    r = await ssh_run(f"grep -c 'MCP_INPUT_TCL' {script_path} 2>/dev/null")
    if r.strip() and r.strip() != "0":
        return f"patched: -input {original_tcl} -> -input {replacement}"
    return "patch failed — manual edit needed"


# ---------------------------------------------------------------------------
# v4: .simvisionrc management (P1-9)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# v4: Unified Discovery Orchestrator (P1-7)
# ---------------------------------------------------------------------------


async def run_full_discovery(sim_dir: str = "", force: bool = False) -> str:
    """Main discovery orchestrator. Called by sim_discover MCP tool.

    Detects all environment aspects and saves to registry + config.
    Returns human-readable discovery result summary.
    """
    # D-1: sim_dir
    if not sim_dir:
        envs = await _discover_sim_dir()
        sim_dir = envs[0]["sim_dir"]

    # Check existing (skip if force=False and v2 config exists)
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

    # D-6: mcp_bridge.tcl (install origin)
    bridge_tcl = await _detect_bridge_tcl()

    # D-7: setup tcl scripts + mode classification
    setup_tcls = await _detect_setup_tcls(sim_dir)

    # D-8: EDA tool paths (from D-5 env)
    eda_tools = await _resolve_eda_tools(shell_env)

    # D-9: bridge port
    bridge_port = await _detect_bridge_port(sim_dir, bridge_tcl)

    # D-10: legacy run script bridge patch
    patch_result = await _patch_legacy_run_script(sim_dir, runner_info)

    # Assemble config v2
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

    # Registry registration
    _update_registry_from_config(sim_dir, tb_type, config)

    # P1-9: .simvisionrc update
    simvisionrc_result = await _update_simvisionrc(bridge_tcl)

    # Format result
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
        f"\nSaved to: {_REGISTRY_PATH}\n"
        f"          {sim_dir}/.mcp_sim_config.json"
    )


# ===========================================================================
# v4 Phase 2: mcp_config — dot-notation config editor
# ===========================================================================

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

    "9876" -> 9876 (int)
    "true"/"false" -> True/False (bool)
    "3.14" -> 3.14 (float)
    Everything else -> str
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


# ===========================================================================
# v4 Phase 2: sim_start — registry-based simulation lifecycle
# ===========================================================================


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
        await run_full_discovery(sim_dir)
        resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_dir:
            raise RuntimeError("sim_discover failed to create registry.")

    config = await load_sim_config(resolved_dir)
    if config is None:
        await run_full_discovery(resolved_dir)
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
    log_file = f"/tmp/sim_start_{port}.log"
    cmd = (
        f"nohup env "
        f"MCP_INPUT_TCL={bridge_tcl} "
        f"MCP_SETUP_TCL={setup_tcl} "
        f"bash {sim_dir}/{script} {test_name} "
        f"{_build_redirect(log_file)} &"
    )
    await ssh_run(cmd, timeout=10)

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
