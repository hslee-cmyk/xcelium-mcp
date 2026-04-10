"""Batch simulation execution for xcelium-mcp.

Extracted from sim_runner.py (v4.2 Phase 3 refactoring).
v4.4: Tcl preprocessing extracted to tcl_preprocessing.py.
      Shell utilities imported from shell_utils.py.
F-038: resolve_test_name/resolve_sim_params → test_resolution.py.
       poll_batch_log/watch_pid_and_poll → batch_polling.py.

Contains batch execution functions: single-test batch, regression,
job parsing, command building, and nohup launch.
"""
from __future__ import annotations

import asyncio
import base64 as _b64
import json
import re as _re
import time as _time
from dataclasses import dataclass
from datetime import datetime

from xcelium_mcp.batch_polling import poll_batch_log, watch_pid_and_poll
from xcelium_mcp.shell_utils import (
    build_redirect,
    get_user_tmp_dir,
    login_shell_cmd,
    shell_quote,
    shell_run,
    shell_run_with_retry,
)
from xcelium_mcp.tcl_preprocessing import (
    _build_checkpoint_tcl,
    _handle_sdf_override,
    _parse_l1_time_ns,
    _preprocess_setup_tcl,
    extract_setup_lines,
    read_setup_tcl,
)
from xcelium_mcp.test_resolution import resolve_sim_params, resolve_test_name

# Re-export for backward compatibility
__all__ = [
    "ExecInfo",
    "validate_extra_args",
    "_resolve_exec_cmd",
    "parse_existing_job",
    "build_batch_cmd",
    "launch_nohup_job",
    "watch_pid_and_poll",
    "run_batch_single",
    "run_batch_regression",
    "poll_batch_log",
    "resolve_sim_params",
    "resolve_test_name",
]


@dataclass
class ExecInfo:
    cmd: str               # resolved execution command string
    needs_test_name: bool  # True  → {test_name} substitution needed before exec
                           # False → command complete as-is (regression_script builtin)


def validate_extra_args(s: str) -> str:
    """Validate extra_args: reject dangerous shell metacharacters.

    extra_args intentionally contains multiple shell tokens (e.g. "--flag val"),
    so we cannot quote it as a whole.  Instead we reject metacharacters that
    could chain/inject commands.  S-4 fix: also reject single quotes to prevent
    csh -c '...' breakout.
    """
    if _re.search(r"[;|&$`<>()\n\r']", s):
        raise ValueError(
            f"extra_args contains forbidden shell metacharacter: {s!r}  "
            "Only flags and values are allowed (no ;|&$`<>()\\n\\r' characters)."
        )
    return s


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
        sources = " && ".join(f"source {shell_quote(f)}" for f in runner.get("env_files", []))
        env_shell = runner.get("env_shell", runner["login_shell"])
        cmd = f"{env_shell} -c '{sources} && {script_run}'"
    else:
        cmd = login_shell_cmd(runner["login_shell"], script_run)

    return ExecInfo(cmd=cmd, needs_test_name=needs_test_name)


async def parse_existing_job(job_file: str, timeout: int) -> str | None:
    """Check for an existing batch job file and resume if the process is alive.

    If a valid job file exists and its PID is still alive, resumes polling
    and returns the result string. If PID is dead or file is invalid,
    cleans up the stale file and returns None.

    Args:
        job_file: Path to the batch job JSON file.
        timeout: Timeout in seconds for log polling if resuming.

    Returns:
        Result string if an alive job was resumed, None otherwise.
    """
    existing_job = await shell_run(f"cat {job_file} || true")
    if not existing_job.strip():
        return None
    try:
        job = json.loads(existing_job)
        pid = job.get("pid", 0)
        # Guard: pid must be > 0 (kill -0 0 signals own process group → always ALIVE)
        if pid > 0:
            pid_alive = await shell_run(f"(kill -0 {pid}) && echo ALIVE || echo DEAD")
        else:
            pid_alive = "DEAD"
        if "ALIVE" in pid_alive:
            # Previous batch still running → resume polling
            result, _ = await poll_batch_log(
                job["log_file"], timeout,
                f"(Resumed monitoring existing batch PID {pid})\n"
            )
            await shell_run(f"rm -f {job_file}", timeout=5)
            return result
        # PID dead → stale job file, remove and start fresh
        await shell_run(f"rm -f {job_file}", timeout=5)
    except (json.JSONDecodeError, KeyError):
        await shell_run(f"rm -f {job_file}", timeout=5)
    return None


_TEST_NAME_RE = _re.compile(r'^[A-Za-z0-9_.\-]+$')


async def build_batch_cmd(
    runner: dict,
    test_name: str,
    sim_mode: str,
    extra_args: str,
    timeout: int,
    dump_depth: str | None,
    dump_signals: list[str] | None,
    dump_window: dict | None,
    sdf_file: str,
    sdf_corner: str,
    sim_dir: str,
) -> tuple[str, str, str | None]:
    """Resolve params, build exec command, and preprocess setup tcl.

    Returns:
        (env_prefix, cmd, preprocessed_tcl) tuple where:
        - env_prefix: environment variable assignments for the shell command
        - cmd: the resolved simulation command string
        - preprocessed_tcl: path to preprocessed tcl file, or None
    """
    # Validate test_name at entry point — same regex as tcl_preprocessing
    if not _TEST_NAME_RE.fullmatch(test_name):
        raise ValueError(
            f"Invalid test_name: {test_name!r}. "
            "Only alphanumeric, underscore, dot, and hyphen characters are allowed."
        )
    validate_extra_args(extra_args)
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout, dump_depth=dump_depth)
    effective_dump_depth = params["dump_depth"]

    # Resolve exec command and format test args
    info = _resolve_exec_cmd(runner, regression=False)
    test_args = params["test_args_format"].format(test_name=shell_quote(test_name))
    cmd = info.cmd.format(test_name=test_args) if info.needs_test_name else info.cmd
    if params["extra_args"]:
        cmd = f"{cmd} {params['extra_args']}"

    # SDF override
    if sdf_file:
        sdf_extra = await _handle_sdf_override(sim_dir, runner, sdf_file, sdf_corner)
        if sdf_extra:
            cmd = f"{cmd} {sdf_extra}"

    # SHM naming + probe scope + dump window: preprocess setup_tcl
    env_prefix = f"TEST_NAME={shell_quote(test_name)} "
    preprocessed_tcl = await _preprocess_setup_tcl(
        sim_dir, runner, test_name, sim_mode,
        dump_depth=effective_dump_depth, dump_signals=dump_signals,
        dump_window=dump_window,
    )
    if preprocessed_tcl:
        env_prefix += f"MCP_INPUT_TCL={shell_quote(preprocessed_tcl)} "

    return env_prefix, cmd, preprocessed_tcl


async def launch_nohup_job(
    sim_dir: str,
    run_cmd: str,
    log_file: str,
    test_name: str,
    job_file: str,
) -> int:
    """Launch a nohup batch job and save job state for resume.

    Starts the simulation via nohup in a subshell, reads the PID,
    saves a job file for reconnection, and starts a PID watcher.

    Args:
        sim_dir: Simulation working directory.
        run_cmd: Full command string (with env prefix) to execute.
        log_file: Path to the log file for output redirection.
        test_name: Test name (for pgrep fallback).
        job_file: Path to save job state JSON.

    Returns:
        PID of the launched process (0 if unknown).
    """
    ts = int(_time.time())
    user_tmp = await get_user_tmp_dir()

    # B-0 fix: subshell wrapping to prevent PIPE fd inheritance
    pid_file = f"{user_tmp}/batch_pid_{ts}"
    await shell_run(
        f"cd {shell_quote(sim_dir)} && "
        f"(nohup {run_cmd} {build_redirect(log_file)} < /dev/null & echo $! > {pid_file}) "
        f">& /dev/null",
        timeout=15.0,
    )

    # Read PID from file + cleanup in single SSH call
    pid_str = await shell_run(f"(cat {pid_file} || true); rm -f {pid_file}", timeout=5)
    pid_str = pid_str.strip()
    # Fallback — use pgrep if pid file didn't yield a number
    if not pid_str.isdigit():
        pid_str = await shell_run(f"(pgrep -f {shell_quote(test_name)} || true) | tail -1")
    pid = int(pid_str.strip()) if pid_str.strip().isdigit() else 0

    if pid:
        job_info = json.dumps({
            "pid": pid,
            "log_file": log_file,
            "test_name": test_name,
            "started_at": datetime.now().isoformat(),
        })
        done_file = f"{log_file}.done"
        # Write job file (sync, fast)
        await shell_run_with_retry(
            f"printf '%s' {shell_quote(job_info)} > {job_file}",
            timeout=15,
        )
        # Launch PID watcher independently using nohup pattern (fully detached from SSH session)
        await shell_run(
            f"(nohup bash -c 'while kill -0 {pid} 2>/dev/null; do sleep 2; done; touch {shell_quote(done_file)}' "
            f"< /dev/null >& /dev/null &)",
            timeout=10,
        )

    return pid


async def run_batch_single(
    sim_dir: str,
    test_name: str,
    runner: dict,
    rename_dump: bool = False,
    run_duration: str = "",
    timeout: int = 600,
    sim_mode: str = "rtl",
    extra_args: str = "",
    dump_depth: str | None = None,
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    sdf_file: str = "",
    sdf_corner: str = "max",
) -> str:
    """Execute a single simulation test and return combined log output.

    Orchestrator that delegates to parse_existing_job, build_batch_cmd,
    launch_nohup_job, and watch_pid_and_poll.

    Strategy: nohup + PID watcher + adaptive log polling (P6-1/P6-2/P6-5).
    """
    user_tmp = await get_user_tmp_dir()
    job_file = f"{user_tmp}/batch_job.json"

    # Resume existing job if alive
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout, dump_depth=dump_depth)
    effective_timeout = params["timeout"]
    resumed = await parse_existing_job(job_file, effective_timeout)
    if resumed is not None:
        return resumed

    # Build command
    env_prefix, cmd, preprocessed_tcl = await build_batch_cmd(
        runner, test_name, sim_mode, extra_args, timeout,
        dump_depth, dump_signals, dump_window, sdf_file, sdf_corner, sim_dir,
    )

    # Launch
    log_file = f"{user_tmp}/batch_{int(_time.time())}.log"
    run_cmd = f"env {env_prefix}{cmd}"
    pid = await launch_nohup_job(sim_dir, run_cmd, log_file, test_name, job_file)

    # Poll + cleanup
    result = await watch_pid_and_poll(pid, log_file, job_file, effective_timeout)

    # Method 6-B fallback (deprecated — kept for backward compat)
    if rename_dump and not preprocessed_tcl:
        mv_cmd = (
            f"cd {shell_quote(sim_dir)} && "
            f"if [ -d dump/ci_top.shm ]; then "
            f"mv dump/ci_top.shm dump/ci_top_{shell_quote(test_name)}.shm; fi"
        )
        await shell_run(mv_cmd, timeout=30.0)

    return result


def _should_resume_regression(job: dict, test_list: list[str]) -> bool:
    """Return True if the existing regression job matches the requested test_list.

    An empty saved test_list (legacy job) is treated as a match to preserve
    backward-compatible resume behavior.
    """
    saved_list = job.get("test_list", [])
    return not saved_list or set(saved_list) == set(test_list)


async def run_batch_regression(
    sim_dir: str,
    test_list: list[str],
    runner: dict,
    rename_dump: bool = False,
    sim_mode: str = "",
    extra_args: str = "",
    save_checkpoints: bool = False,
    l1_time: str = "",
    dump_depth: str | None = None,
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    sdf_file: str = "",
    sdf_corner: str = "max",
) -> str:
    """Execute regression tests via nohup batch with job resume.

    nohup + PID watcher + adaptive log polling (P6-1/P6-2/P6-5).
    Job resume: on reconnection, resumes from last completed test.

    needs_test_name=False → regression_script handles all tests → 1 cmd
    needs_test_name=True  → iterate test_list, per-test nohup + poll

    Phase 4 — save_checkpoints:
      When True, injects Tcl save commands into each test's xmsim input script.
      L1_{test}: saved at l1_time (common init completion).
      L2_{test}: saved just before $finish (test completion).
      These checkpoints are used later by sim_batch_run(from_checkpoint=...)
      for faster debugging (skip compile+init).
    """
    user_tmp = await get_user_tmp_dir()
    job_file = f"{user_tmp}/regression_job.json"
    ts = int(_time.time())
    log_file = f"{user_tmp}/regression_{ts}.log"

    # v4.1: resolve sim_mode/extra_args for regression
    validate_extra_args(extra_args)
    effective_sim_mode = sim_mode or runner.get("default_mode", "rtl")
    params = resolve_sim_params(runner, effective_sim_mode, extra_args=extra_args, dump_depth=dump_depth)
    info = _resolve_exec_cmd(runner, regression=True)

    # Phase 4: prepare checkpoint setup (read + strip run/exit once)
    chk_dir = f"{sim_dir}/checkpoints"
    setup_lines = ""
    if save_checkpoints:
        await shell_run(f"mkdir -p {shell_quote(chk_dir)}", timeout=5)
        raw_tcl = read_setup_tcl(runner, sim_dir)
        setup_lines = extract_setup_lines(raw_tcl)

    # Check for existing regression job (reconnection scenario)
    completed_tests: list[str] = []
    existing_job = await shell_run(f"cat {job_file} || true")
    if existing_job.strip():
        try:
            job = json.loads(existing_job)
            if job.get("type") == "regression":
                pid = job.get("pid", 0)
                # Guard: pid must be > 0 (kill -0 0 signals own process group → always ALIVE)
                if pid > 0:
                    pid_alive = await shell_run(
                        f"(kill -0 {pid}) && echo ALIVE || echo DEAD"
                    )
                else:
                    pid_alive = "DEAD"
                if "ALIVE" in pid_alive and _should_resume_regression(job, test_list):
                    # Current test still running → resume polling
                    current = job.get("current", "")
                    completed_tests = job.get("completed", [])
                    log_file = job.get("log_file", log_file)
                    current_log = job.get("current_log", "")
                    if current_log:
                        _, _ = await poll_batch_log(current_log, 600)
                        completed_tests.append(current)
                # else: PID dead OR different test_list → discard and start fresh
        except (json.JSONDecodeError, KeyError):
            pass
        await shell_run(f"rm -f {job_file}", timeout=5)

    if not info.needs_test_name:
        # regression_script handles all tests internally → 1 cmd
        if not completed_tests:  # only start if not resuming
            cmd_with_extra = (
                f"{info.cmd} {params['extra_args']}".strip()
                if params["extra_args"]
                else info.cmd
            )
            # B-0 fix: subshell wrapping, stdbuf removed (Xcelium incompatible)
            run_cmd = cmd_with_extra
            await shell_run(
                f"cd {shell_quote(sim_dir)} && "
                f"(nohup {run_cmd} {build_redirect(log_file)} < /dev/null &) "
                f">& /dev/null",
                timeout=15.0,
            )
        # P6-1/P6-2: adaptive polling via poll_batch_log
        _, _ = await poll_batch_log(log_file, timeout=3600)

    else:
        # Per-test loop — skip completed tests (resume support)
        remaining = [t for t in test_list if t not in completed_tests]

        for test_name in remaining:
            test_log = f"{user_tmp}/regression_{ts}_{shell_quote(test_name)}.log"
            env_prefix = f"TEST_NAME={shell_quote(test_name)} "

            # SHM naming + probe scope + dump window: preprocess setup_tcl.
            # Skip when save_checkpoints — _build_checkpoint_tcl handles SHM
            # replacement and sets its own MCP_INPUT_TCL.
            # NOTE: dump_depth/dump_window are ignored when save_checkpoints=True.
            # Checkpoints need full probe scope for later restore+debug.
            if not (save_checkpoints and setup_lines):
                preprocessed_tcl = await _preprocess_setup_tcl(
                    sim_dir, runner, test_name, effective_sim_mode,
                    dump_depth=params.get("dump_depth", "all"),
                    dump_signals=dump_signals,
                    dump_window=dump_window,
                )
                if preprocessed_tcl:
                    env_prefix += f"MCP_INPUT_TCL={shell_quote(preprocessed_tcl)} "

            # Phase 4: inject checkpoint save commands into xmsim Tcl
            if save_checkpoints and setup_lines:
                chk_tcl = _build_checkpoint_tcl(
                    test_name, chk_dir, l1_time, setup_lines,
                )
                chk_tcl_path = f"{user_tmp}/chk_{shell_quote(test_name)}.tcl"
                b64 = _b64.b64encode(chk_tcl.encode()).decode()
                await shell_run(
                    f"echo {shell_quote(b64)} | base64 -d > {shell_quote(chk_tcl_path)}",
                    timeout=5,
                )
                env_prefix += f"MCP_INPUT_TCL={shell_quote(chk_tcl_path)} "

            test_args = params["test_args_format"].format(test_name=shell_quote(test_name))
            cmd = info.cmd.format(test_name=test_args) if info.needs_test_name else info.cmd
            if params["extra_args"]:
                cmd = f"{cmd} {params['extra_args']}"

            # B-0 fix: subshell wrapping, stdbuf removed (Xcelium incompatible)
            # P6-5b: echo $! > pid_file inside subshell — aligns with run_batch_single
            run_cmd = f"env {env_prefix}{cmd}"
            pid_file = f"{test_log}.pid"
            await shell_run(
                f"cd {shell_quote(sim_dir)} && "
                f"(nohup {run_cmd} {build_redirect(test_log)} < /dev/null & echo $! > {pid_file}) "
                f">& /dev/null",
                timeout=15.0,
            )

            # Read PID + save job state in single cycle (F-020: was 2 base64 writes)
            pid_str = await shell_run(f"(cat {pid_file} || true); rm -f {pid_file}", timeout=5)
            test_pid = int(pid_str.strip()) if pid_str.strip().isdigit() else 0
            job_info = json.dumps({
                "type": "regression",
                "pid": test_pid,
                "log_file": log_file,
                "current": test_name,
                "current_log": test_log,
                "completed": completed_tests,
                "test_list": test_list,
                "started_at": datetime.now().isoformat(),
            })
            # Write job file (sync, fast) — no base64, use printf directly
            await shell_run_with_retry(
                f"printf '%s' {shell_quote(job_info)} > {job_file}",
                timeout=15,
            )
            # Launch PID watcher independently using nohup pattern (fully detached from SSH session)
            if test_pid:
                test_done = f"{test_log}.done"
                await shell_run(
                    f"(nohup bash -c 'while kill -0 {test_pid} 2>/dev/null; do sleep 2; done; touch {shell_quote(test_done)}' "
                    f"< /dev/null >& /dev/null &)",
                    timeout=10,
                )

            # Per-test poll (P6-1/P6-2/P6-5 via poll_batch_log)
            _, timed_out = await poll_batch_log(test_log, 600)

            if timed_out:
                # Guard: kill stale xmsim/xmrm to prevent worklib lock on next test
                if pid_str.strip().isdigit():
                    await shell_run(
                        f"(kill -0 {test_pid}) && kill {test_pid}",
                        timeout=5,
                    )
                await shell_run("pkill -f xmrm || true", timeout=5)
                # Append TIMEOUT marker to per-test log
                await shell_run(
                    f"echo '[TIMEOUT] Test did not complete within 600s' >> {test_log}",
                    timeout=5,
                )

            completed_tests.append(test_name)

            # Phase 4: register L1/L2 in checkpoint manifest
            if save_checkpoints and not timed_out:
                from xcelium_mcp import checkpoint_manager as _ckpt
                l1_ns = _parse_l1_time_ns(l1_time)
                await asyncio.to_thread(
                    _ckpt.register_checkpoint,
                    sim_dir, f"L1_{test_name}", l1_ns,
                    origin="regression", test_name=test_name,
                )

            # Method 6-B fallback
            if rename_dump:
                mv_cmd = (
                    f"cd {shell_quote(sim_dir)} && "
                    f"if [ -d dump/ci_top.shm ]; then "
                    f"mv dump/ci_top.shm dump/ci_top_{shell_quote(test_name)}.shm; fi"
                )
                await shell_run(mv_cmd, timeout=30.0)

            # Append per-test result to main log
            await shell_run(
                f"echo {shell_quote('=== ' + test_name + ' ===')} >> {log_file} && "
                f"(grep -E 'PASS|FAIL|Errors:' {test_log} || true) >> {log_file}",
                timeout=10.0,
            )

    # Cleanup job file
    await shell_run(f"rm -f {job_file}", timeout=5)

    # Parse final results from per-test logs (F-020: single grep instead of N)
    # Build one command that greps all test logs and prefixes each with filename
    log_pattern = f"{user_tmp}/regression_{ts}_*.log"
    batch_grep = await shell_run(
        f"(grep -H -E 'PASS|FAIL|Errors:|COMPLETE' {log_pattern} || true) | tail -200",
        timeout=30.0,
    )
    # Parse grep -H output: "filename:matched_line"
    per_test_results: dict[str, list[str]] = {tn: [] for tn in test_list}
    for line in batch_grep.strip().splitlines():
        for tn in test_list:
            if f"_{shell_quote(tn)}.log:" in line:
                per_test_results[tn].append(line.split(":", 1)[1] if ":" in line else line)
                break

    all_parts: list[str] = []
    pass_count = 0
    fail_count = 0
    for tn in test_list:
        t_raw = "\n".join(per_test_results[tn])
        all_parts.append(f"=== {tn} ===\n{t_raw}")
        pass_count += t_raw.count("PASS")
        fail_count += t_raw.count("FAIL")

    total = len(test_list)
    raw = "\n".join(all_parts)
    summary = f"{pass_count}/{total} tests PASS, {fail_count} FAIL"
    details = raw[:4000] if raw.strip() else "(no PASS/FAIL lines found in per-test logs)"
    return f"{summary}\n\nLog ({log_file}):\n{details}"
