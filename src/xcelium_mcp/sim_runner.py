"""sim_runner.py — Script Discovery and Batch/Regression execution for xcelium-mcp v3.

Architecture note:
  xcelium-mcp server runs ON cloud0 via SSH stdio transport.
  ssh_run() is a LOCAL asyncio subprocess on cloud0 — NOT a remote SSH hop.
  All file paths in this module refer to cloud0 local filesystem.
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

async def ssh_run(cmd: str, timeout: float = 60.0) -> str:
    """Run a shell command as a local subprocess.

    Since xcelium-mcp runs on cloud0, this is a local asyncio subprocess —
    not an SSH call. Combined stdout+stderr is returned as a single string.
    """
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


def _update_registry_env(sim_dir: str, tb_type: str, config_file: str = ".mcp_sim_config.json") -> None:
    """Register a sim environment in mcp_registry.json."""
    import subprocess
    # project_root = git root of sim_dir
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=sim_dir
    )
    project_root = r.stdout.strip() if r.returncode == 0 else str(Path.home())

    registry = load_registry()
    projects = registry.setdefault("projects", {})
    project = projects.setdefault(project_root, {"environments": {}})
    envs = project.setdefault("environments", {})

    if sim_dir not in envs:
        envs[sim_dir] = {
            "tb_type": tb_type,
            "is_default": len(envs) == 0,
            "confidence": "auto",
            "config_file": config_file,
            "checkpoint_dir": str(Path(sim_dir) / "checkpoints"),
            "checkpoints": [],
        }
    else:
        envs[sim_dir]["config_file"] = config_file

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
        cmd = f"{runner['login_shell']} -lc '{script_run}'"

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
    r = await ssh_run(f"{login_shell} -lc 'which xrun 2>/dev/null'")
    if r.strip():
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
        r = await ssh_run(f"find {search_dir} -maxdepth 1 -type f {pat} 2>/dev/null")
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
        if r.strip():
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

    Tier 1: load .mcp_sim_config.json (explicit config wins).
    Tier 2: auto-detect runner, save if high-confidence.
    Tier 3: raise UserInputRequired for ambiguous/none.

    Returns: runner sub-dict from config.
    """
    # Tier 1: explicit config
    cfg = await load_sim_config(sim_dir)
    if cfg is not None:
        return cfg.get("runner", cfg)

    # Tier 2: auto-detect runner + shell/EDA env
    detected = await _auto_detect_runner(sim_dir)
    if detected["confidence"] == "high":
        script = _extract_script_name(detected["exec_cmd"])

        # git root for EDA env search scope
        r = await ssh_run("git rev-parse --show-toplevel 2>/dev/null || echo ~")
        project_root = r.strip()

        # detect shell + EDA env (UserInputRequired propagates if env not found)
        shell_env = await _detect_shell_and_env(sim_dir, script, project_root)

        runner: dict = {
            "type": detected["runner"],
            "script": script,
            **shell_env,
        }
        cfg = {"version": 1, "runner": runner}
        await save_sim_config(sim_dir, cfg)
        return runner

    # Tier 3: ambiguous or none
    await _ask_user_runner(sim_dir, detected.get("candidates", []))
    # _ask_user_runner always raises; this line is unreachable
    raise RuntimeError("unreachable")


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

    Falls back to _discover_sim_dir() if registry is empty.
    Returns "" if nothing found.
    """
    registry = load_registry()
    for project in registry.get("projects", {}).values():
        for sim_dir, env in project.get("environments", {}).items():
            if env.get("is_default"):
                return sim_dir

    # Fallback: auto-discover (may raise UserInputRequired)
    envs = await _discover_sim_dir()
    return envs[0]["sim_dir"] if envs else ""


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

        return result

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
