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
from datetime import datetime, timezone
from pathlib import Path

from xcelium_mcp.batch_polling import poll_batch_log, watch_pid_and_poll
from xcelium_mcp.registry import load_sim_config, save_sim_config
from xcelium_mcp.shell_utils import (
    build_eda_command,
    build_redirect,
    get_user_tmp_dir,
    shell_quote,
    shell_run,
    shell_run_fire_and_forget,
)
from xcelium_mcp.tcl_preprocessing import (
    _build_checkpoint_tcl,
    _handle_sdf_override,
    _parse_l1_time_ns,
    _preprocess_setup_tcl,
    _read_setup_tcl_sync,
    extract_setup_lines,
    get_dump_strategy,
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

    # 4. build full cmd (env sourcing) — F-158: use the shared build_eda_command
    # instead of an inline re-implementation (was drifting: '&&' vs '; ' join
    # separator, and didn't guard against source_separately=True with an
    # empty env_files list, which produced a malformed leading '&&').
    cmd = build_eda_command(runner, script_run)

    return ExecInfo(cmd=cmd, needs_test_name=needs_test_name)


async def _kill_stale_sim(pid: int, test_name: str = "") -> None:
    """Kill a stale simulation process — both wrapper PID and orphaned xmsim.

    nohup xmsim becomes an orphan when the wrapper exits. We must kill both:
    1. The wrapper PID (if alive)
    2. Any xmsim process matching the test_name (catches orphans)
    """
    if pid > 0:
        await shell_run(f"kill {pid} 2>/dev/null || true", timeout=5)
        await shell_run(f"pkill -P {pid} 2>/dev/null || true", timeout=5)
    if test_name:
        # Kill orphaned xmsim that contains this test_name in its command line
        await shell_run(f"pkill -f 'xmsim.*{shell_quote(test_name)}' 2>/dev/null || true", timeout=5)


async def _read_job_status(job_file: str) -> tuple[dict, bool] | None:
    """Read a batch/regression job file and check whether its PID is alive.

    F-156: this read+parse+PID-check prefix was duplicated between
    parse_existing_job() and run_batch_regression()'s inline resume block.
    Their resume DECISIONS (single-test result vs. regression completed_tests
    list) differ enough in return shape/semantics that unifying past this
    point would force an artificial common interface — this helper captures
    exactly the shared, mechanical part.

    Returns (job_dict, is_alive), or None if no valid job file exists /
    the file content isn't valid JSON.
    """
    existing_job = await shell_run(f"cat {job_file} || true")
    if not existing_job.strip():
        return None
    try:
        job = json.loads(existing_job)
    except (json.JSONDecodeError, KeyError):
        return None
    pid = job.get("pid", 0)
    # Guard: pid must be > 0 (kill -0 0 signals own process group → always ALIVE)
    if pid > 0:
        pid_alive = await shell_run(f"(kill -0 {pid}) && echo ALIVE || echo DEAD")
    else:
        pid_alive = "DEAD"
    return job, "ALIVE" in pid_alive


async def parse_existing_job(job_file: str, timeout: int, test_name: str = "") -> str | None:
    """Check for an existing batch job file and resume if the process is alive.

    If a valid job file exists and its PID is still alive:
      - Same test_name (or empty) → resume polling and return result
      - Different test_name → kill existing process, cleanup, return None (fresh start)
    If PID is dead or file is invalid, cleans up and returns None.

    Args:
        job_file: Path to the batch job JSON file.
        timeout: Timeout in seconds for log polling if resuming.
        test_name: Current test name. If different from saved, kill and restart.

    Returns:
        Result string if an alive job was resumed, None otherwise.
    """
    status = await _read_job_status(job_file)
    if status is None:
        return None
    try:
        job, is_alive = status
        pid = job.get("pid", 0)
        saved_test = job.get("test_name", "")
        saved_log = job.get("log_file", "")
        if is_alive:
            if not test_name or not saved_test or saved_test == test_name:
                # Same test, still running → resume polling
                result, _ = await poll_batch_log(
                    saved_log, timeout,
                    f"(Resumed monitoring existing batch PID {pid})\n"
                )
                await shell_run(f"rm -f {job_file}", timeout=5)
                return result
            # Different test → kill existing and start fresh
            await _kill_stale_sim(pid, saved_test)
        else:
            # PID dead — simulation finished while disconnected
            if not test_name or not saved_test or saved_test == test_name:
                # Same test, already finished → return existing log results
                if saved_log:
                    result = await shell_run(
                        f"(grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {saved_log} || true) | tail -30"
                    )
                    if result.strip():
                        await shell_run(f"rm -f {job_file}", timeout=5)
                        return f"(Completed while disconnected)\n{result}"
            else:
                # Different test → kill orphaned xmsim
                await _kill_stale_sim(0, saved_test)
        await shell_run(f"rm -f {job_file}", timeout=5)
    except (json.JSONDecodeError, KeyError):
        await shell_run(f"rm -f {job_file}", timeout=5)
    return None


_TEST_NAME_RE = _re.compile(r'^[A-Za-z0-9_.\-]+$')
# Matches "COMPLETE. Errors: N" verdict line produced by UVM test harness
_COMPLETE_RE = _re.compile(r'COMPLETE\.\s*Errors:\s*(\d+)')


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
    dump_scopes: dict | None = None,
    dump_strategy: dict | None = None,
) -> tuple[str, str, str | None, dict | None]:
    """Resolve params, build exec command, and preprocess setup tcl.

    Returns:
        (env_prefix, cmd, preprocessed_tcl, dump_summary) tuple where:
        - env_prefix: environment variable assignments for the shell command
        - cmd: the resolved simulation command string
        - preprocessed_tcl: path to preprocessed tcl file, or None
        - dump_summary: dump summary dict (hierarchical mode only), or None
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
    preprocessed_tcl, dump_summary = await _preprocess_setup_tcl(
        sim_dir, runner, test_name, sim_mode,
        dump_depth=effective_dump_depth, dump_signals=dump_signals,
        dump_window=dump_window,
        dump_scopes=dump_scopes,
        dump_strategy=dump_strategy,
    )
    if preprocessed_tcl:
        env_prefix += f"MCP_INPUT_TCL={shell_quote(preprocessed_tcl)} "

    return env_prefix, cmd, preprocessed_tcl, dump_summary


async def launch_nohup_job(
    sim_dir: str,
    run_cmd: str,
    log_file: str,
    test_name: str,
    job_file: str,
    extra_job_fields: dict | None = None,
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
        extra_job_fields: F-157 — additional/overriding fields merged into the
            job JSON (e.g. run_batch_regression adds type/current/current_log/
            completed/test_list and overrides "log_file" with the aggregate
            regression log, since this call's `log_file` param is the
            per-test log used for redirection).

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
        job_dict = {
            "pid": pid,
            "log_file": log_file,
            "test_name": test_name,
            "started_at": datetime.now().isoformat(),
        }
        if extra_job_fields:
            job_dict.update(extra_job_fields)
        job_info = json.dumps(job_dict)
        done_file = f"{log_file}.done"
        # Write job file (fast, sync)
        await shell_run(f"printf '%s' {shell_quote(job_info)} > {job_file}", timeout=15)
        # Launch PID watcher — fire and forget (Popen with DEVNULL, returns immediately)
        await shell_run_fire_and_forget(
            f"while kill -0 {pid} 2>/dev/null; do sleep 2; done; touch {shell_quote(done_file)}"
        )

    return pid


async def _lazy_discover_boundaries(
    sim_dir: str,
    dump_strategy: dict,
    sim_mode: str,
) -> dict | None:
    """Flow B: lazily discover block boundaries from a Yosys JSON netlist.

    Called when dump_depth="boundary" but block_boundaries is empty.
    Reads netlist_info.{mode}.boundary_json from config and parses it.
    If dump_strategy['write_discovered_boundaries'] is True, persists the
    result back to config.

    Returns discovered {scope: [signals]} dict, or None if unavailable.
    """
    from xcelium_mcp.sim_env_detection import _boundaries_from_json

    try:
        config = await load_sim_config(sim_dir) or {}
        base_mode = "gate" if "gate" in sim_mode else "rtl"
        json_rel = (
            config.get("netlist_info", {})
            .get(base_mode, {})
            .get("boundary_json", "")
        )
        if not json_rel:
            return None
        json_path = Path(sim_dir) / json_rel
        if not json_path.exists():
            return None

        block_filter = dump_strategy.get("block_filter")
        boundaries = _boundaries_from_json(
            json_path,
            config.get("top_module", "top"),
            depth=dump_strategy.get("boundary_depth", 3),
            block_filter=block_filter,
        )

        if boundaries and dump_strategy.get("write_discovered_boundaries"):
            config.setdefault("dump_strategy", {}).setdefault(base_mode, {})
            config["dump_strategy"][base_mode]["block_boundaries"] = boundaries
            await save_sim_config(sim_dir, config)

        return boundaries or None
    except Exception:
        return None


async def _update_dump_history(
    sim_dir: str,
    test_name: str,
    dump_summary: dict,
    dump_scopes: dict | None,
) -> None:
    """Persist dump_summary and effective dump_scopes into the sim config dump_history."""
    try:
        config = await load_sim_config(sim_dir, force=True) or {}
        history = config.setdefault("dump_history", {})
        history[test_name] = {
            "last_dump_summary": {
                k: v for k, v in dump_summary.items() if k != "scope_overrides"
            },
            "dump_scopes": dump_scopes or {},
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        await save_sim_config(sim_dir, config)
    except Exception:
        pass  # dump_history write failure is non-fatal


async def _history_scopes(sim_dir: str, test_name: str) -> dict | None:
    """Look up dump_scopes previously recorded for test_name in dump_history.

    Returns None if there's no history entry, or the config can't be read
    (config load failure here is non-fatal — caller falls back to auto mode).
    """
    try:
        config = await load_sim_config(sim_dir) or {}
        history = config.get("dump_history", {})
        return history.get(test_name, {}).get("dump_scopes") or None
    except Exception:
        return None


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
    force: bool = False,
    dump_scopes: dict | None = None,
    use_dump_history: bool = False,
) -> tuple[str, dict | None]:
    """Execute a single simulation test and return (log, dump_summary).

    Orchestrator that delegates to parse_existing_job, build_batch_cmd,
    launch_nohup_job, and watch_pid_and_poll.

    Strategy: nohup + PID watcher + adaptive log polling (P6-1/P6-2/P6-5).
    Returns:
        (result_log, dump_summary) where dump_summary is None unless
        dump_depth="boundary" with hierarchical scopes was used.
    """
    user_tmp = await get_user_tmp_dir()
    job_file = f"{user_tmp}/batch_job.json"

    # Load dump_scopes from history if use_dump_history and no explicit scopes given
    effective_dump_scopes = dump_scopes
    if use_dump_history and effective_dump_scopes is None:
        effective_dump_scopes = await _history_scopes(sim_dir, test_name)

    # Load dump_strategy from config for hierarchical mode
    dump_strategy: dict | None = None
    if effective_dump_scopes is not None or (dump_depth == "boundary"):
        try:
            config = await load_sim_config(sim_dir) or {}
            dump_strategy = get_dump_strategy(config, sim_mode)
        except Exception:
            pass

    # Flow B: lazy boundary discovery when block_boundaries is empty
    if (
        dump_depth == "boundary"
        and dump_strategy is not None
        and not dump_strategy.get("block_boundaries")
        and dump_strategy.get("default_block_policy")
    ):
        discovered = await _lazy_discover_boundaries(sim_dir, dump_strategy, sim_mode)
        if discovered:
            dump_strategy = dict(dump_strategy)
            dump_strategy["block_boundaries"] = discovered

    # Resume existing job if alive (skip if force=True)
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout, dump_depth=dump_depth)
    effective_timeout = params["timeout"]
    if force:
        # Force re-run: kill any existing process and cleanup
        existing_job = await shell_run(f"cat {job_file} || true")
        if existing_job.strip():
            try:
                job = json.loads(existing_job)
                await _kill_stale_sim(job.get("pid", 0), job.get("test_name", ""))
            except (json.JSONDecodeError, KeyError):
                pass
            await shell_run(f"rm -f {job_file}", timeout=5)
    else:
        resumed = await parse_existing_job(job_file, effective_timeout, test_name)
        if resumed is not None:
            return resumed, None

    # Build command
    env_prefix, cmd, preprocessed_tcl, dump_summary = await build_batch_cmd(
        runner, test_name, sim_mode, extra_args, timeout,
        dump_depth, dump_signals, dump_window, sdf_file, sdf_corner, sim_dir,
        dump_scopes=effective_dump_scopes,
        dump_strategy=dump_strategy,
    )

    # Launch
    log_file = f"{user_tmp}/batch_{int(_time.time())}.log"
    run_cmd = f"env {env_prefix}{cmd}"
    await launch_nohup_job(sim_dir, run_cmd, log_file, test_name, job_file)

    # Poll + cleanup
    result = await watch_pid_and_poll(log_file, job_file, effective_timeout)

    # Persist dump_summary to history if hierarchical mode was used
    if dump_summary is not None:
        await _update_dump_history(sim_dir, test_name, dump_summary, effective_dump_scopes)

    # Method 6-B fallback (deprecated — kept for backward compat)
    if rename_dump and not preprocessed_tcl:
        mv_cmd = (
            f"cd {shell_quote(sim_dir)} && "
            f"if [ -d dump/ci_top.shm ]; then "
            f"mv dump/ci_top.shm dump/ci_top_{shell_quote(test_name)}.shm; fi"
        )
        await shell_run(mv_cmd, timeout=30.0)

    return result, dump_summary


def _should_resume_regression(job: dict, test_list: list[str]) -> bool:
    """Return True if the existing regression job matches the requested test_list.

    An empty saved test_list (legacy job) is treated as a match to preserve
    backward-compatible resume behavior.
    """
    saved_list = job.get("test_list", [])
    return not saved_list or set(saved_list) == set(test_list)


def classify_regression_results(
    test_list: list[str],
    per_test_results: dict[str, list[str]],
    per_test_errors: dict[str, str],
    log_file: str,
) -> str:
    """5-way classify each test's collected log lines and build the summary log string.

    F-155: extracted from run_batch_regression's tail — pure function, no I/O,
    directly unit-testable without mocking shell_run/the regression pipeline.

    Classification (per test):
      (1) HAS_VERDICT: "COMPLETE. Errors: 0" -> pass
      (2) HAS_VERDICT: "COMPLETE. Errors: N>0", or "FAIL" without COMPLETE -> fail
      (3) NO_VERDICT: "$finish" reached, no error lines -> complete (waveform-only)
      (4) NO_VERDICT: "$finish" reached, with error lines -> error
      (5) NO_VERDICT: no "$finish" (timeout/crash) -> error
    """
    all_parts: list[str] = []
    pass_count = 0       # HAS_VERDICT: COMPLETE. Errors: 0
    fail_count = 0       # HAS_VERDICT: COMPLETE. Errors: N>0, or FAIL without COMPLETE
    complete_count = 0   # NO_VERDICT: $finish + no errors
    error_count = 0      # NO_VERDICT: $finish + errors, or no $finish (timeout/crash)
    check_pass = 0       # check-level substring count
    check_fail = 0       # check-level substring count
    for tn in test_list:
        t_raw = "\n".join(per_test_results[tn])
        all_parts.append(f"=== {tn} ===\n{t_raw}")
        # 5-way classification
        m = _COMPLETE_RE.search(t_raw)
        if m:
            # (1) HAS_VERDICT: COMPLETE. Errors: N
            if int(m.group(1)) == 0:
                pass_count += 1
            else:
                fail_count += 1
        elif "FAIL" in t_raw:
            # (2) HAS_VERDICT: crash/abort with FAIL but no COMPLETE
            fail_count += 1
        elif "$finish" in t_raw:
            # (3)/(4) NO_VERDICT: $finish reached
            if per_test_errors.get(tn):
                error_count += 1  # $finish + errors
            else:
                complete_count += 1  # $finish, no errors → waveform complete
        else:
            # (5) NO_VERDICT: no $finish → timeout or crash
            error_count += 1
        # Check-level counts (individual assertion lines)
        check_pass += t_raw.count("PASS")
        check_fail += t_raw.count("FAIL")

    total = len(test_list)
    raw = "\n".join(all_parts)
    verdict_total = pass_count + fail_count
    waveform_total = complete_count + error_count
    summary_lines: list[str] = []
    if verdict_total > 0:
        summary_lines.append(
            f"{pass_count}/{verdict_total} verdict tests PASS "
            f"({check_pass} checks passed, {check_fail} failed)"
        )
    if waveform_total > 0:
        summary_lines.append(f"{complete_count}/{waveform_total} waveform tests COMPLETE")
    if not summary_lines:
        summary_lines.append(f"0/{total} tests classified")
    summary = "\n".join(summary_lines)
    details = raw[:4000] if raw.strip() else "(no PASS/FAIL/$finish lines found in per-test logs)"
    return f"{summary}\n\nLog ({log_file}):\n{details}"


def aggregate_dump_stats(per_test_dump_summaries: dict[str, dict]) -> dict | None:
    """Aggregate per-test dump_summary dicts into a dump_stats report.

    F-155: extracted from run_batch_regression — pure function, no I/O.
    Returns None if no test produced a dump_summary (non-boundary mode).
    """
    if not per_test_dump_summaries:
        return None
    per_test_entry: dict[str, dict] = {}
    for tn, s in per_test_dump_summaries.items():
        per_test_entry[tn] = {
            "total": s.get("total_signals", 0),
            "top_boundary": s.get("top_boundary_count", 0),
            "block_count": sum(
                1 for c in s.get("block_boundaries", {}).values() if c > 0
            ),
        }
    totals = [v["total"] for v in per_test_entry.values()]
    avg = sum(totals) / len(totals) if totals else 0
    max_test = max(per_test_entry, key=lambda t: per_test_entry[t]["total"])
    min_test = min(per_test_entry, key=lambda t: per_test_entry[t]["total"])
    suggestions = [
        f"{t} total={per_test_entry[t]['total']} (max): dump_scopes로 heavy block skip 검토"
        for t in per_test_entry
        if per_test_entry[t]["total"] > avg * 2
    ]
    return {
        "per_test": per_test_entry,
        "max": {"test": max_test, "total": per_test_entry[max_test]["total"]},
        "min": {"test": min_test, "total": per_test_entry[min_test]["total"]},
        "suggestions": suggestions,
    }


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
    dump_scopes: dict | None = None,
    use_dump_history: bool = False,
) -> tuple[str, dict | None]:
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

    def _extract_ts_from_log(lf: str) -> int:
        """Extract timestamp from log filename: /tmp/.../regression_12345.log → 12345."""
        import re as _re2
        m = _re2.search(r'regression_(\d+)\.log', lf)
        return int(m.group(1)) if m else ts

    # v4.1: resolve sim_mode/extra_args for regression
    validate_extra_args(extra_args)
    effective_sim_mode = sim_mode or runner.get("default_mode", "rtl")
    params = resolve_sim_params(runner, effective_sim_mode, extra_args=extra_args, dump_depth=dump_depth)
    info = _resolve_exec_cmd(runner, regression=True)

    # v5.2: load dump_strategy for hierarchical dump mode
    dump_strategy: dict | None = None
    if dump_scopes is not None or dump_depth == "boundary":
        try:
            cfg = await load_sim_config(sim_dir) or {}
            dump_strategy = get_dump_strategy(cfg, effective_sim_mode)
        except Exception:
            pass

    # v5.2: per-test dump_summary accumulator for dump_stats
    per_test_dump_summaries: dict[str, dict] = {}

    # Phase 4: prepare checkpoint setup (read + strip run/exit once)
    chk_dir = f"{sim_dir}/checkpoints"
    setup_lines = ""
    if save_checkpoints:
        await shell_run(f"mkdir -p {shell_quote(chk_dir)}", timeout=5)
        raw_tcl = _read_setup_tcl_sync(runner, sim_dir)
        setup_lines = extract_setup_lines(raw_tcl)

    # Check for existing regression job (reconnection scenario)
    # F-156: read+parse+PID-check shared with parse_existing_job() via _read_job_status()
    completed_tests: list[str] = []
    status = await _read_job_status(job_file)
    if status is not None:
        try:
            job, is_alive = status
            if job.get("type") == "regression":
                pid = job.get("pid", 0)
                if is_alive:
                    if _should_resume_regression(job, test_list):
                        # Same test_list → resume polling
                        current = job.get("current", "")
                        completed_tests = job.get("completed", [])
                        log_file = job.get("log_file", log_file)
                        ts = _extract_ts_from_log(log_file)
                        current_log = job.get("current_log", "")
                        if current_log:
                            _, _ = await poll_batch_log(current_log, 600)
                            completed_tests.append(current)
                    else:
                        # Different test_list → kill existing and start fresh
                        current = job.get("current", "")
                        await _kill_stale_sim(pid, current)
                else:
                    # PID dead — sim finished while disconnected
                    current = job.get("current", "")
                    if _should_resume_regression(job, test_list):
                        # Same test_list → recover completed tests from job
                        completed_tests = job.get("completed", [])
                        log_file = job.get("log_file", log_file)
                        ts = _extract_ts_from_log(log_file)
                        # Check if current test also completed (check its log)
                        current_log = job.get("current_log", "")
                        if current and current_log:
                            log_check = await shell_run(
                                f"grep -cE 'PASS|FAIL|\\$finish|COMPLETE' {current_log} || echo 0"
                            )
                            if log_check.strip() != "0":
                                completed_tests.append(current)
                    elif current:
                        # Different test_list → kill orphaned xmsim
                        await _kill_stale_sim(0, current)
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
            test_log = f"{user_tmp}/regression_{ts}_{test_name}.log"
            env_prefix = f"TEST_NAME={shell_quote(test_name)} "

            # SHM naming + probe scope + dump window: preprocess setup_tcl.
            # Skip when save_checkpoints — _build_checkpoint_tcl handles SHM
            # replacement and sets its own MCP_INPUT_TCL.
            # NOTE: dump_depth/dump_window are ignored when save_checkpoints=True.
            # Checkpoints need full probe scope for later restore+debug.
            if not (save_checkpoints and setup_lines):
                # v5.2: resolve per-test dump_scopes (from history or param)
                effective_test_scopes = dump_scopes
                if use_dump_history and effective_test_scopes is None:
                    effective_test_scopes = await _history_scopes(sim_dir, test_name)

                preprocessed_tcl, test_dump_summary = await _preprocess_setup_tcl(
                    sim_dir, runner, test_name, effective_sim_mode,
                    dump_depth=params.get("dump_depth", "all"),
                    dump_signals=dump_signals,
                    dump_window=dump_window,
                    dump_scopes=effective_test_scopes,
                    dump_strategy=dump_strategy,
                )
                if preprocessed_tcl:
                    env_prefix += f"MCP_INPUT_TCL={shell_quote(preprocessed_tcl)} "
                if test_dump_summary is not None:
                    per_test_dump_summaries[test_name] = test_dump_summary
                    # v5.2: keep dump_history in sync on the regression path too
                    # (Plan §3.2 "항상 갱신" — was previously only updated by run_batch_single)
                    await _update_dump_history(
                        sim_dir, test_name, test_dump_summary, effective_test_scopes
                    )

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

            # F-157: reuse launch_nohup_job (was an inline re-implementation of
            # the same nohup+PID+jobfile+watcher sequence run_batch_single uses).
            run_cmd = f"env {env_prefix}{cmd}"
            test_pid = await launch_nohup_job(
                sim_dir, run_cmd, test_log, test_name, job_file,
                extra_job_fields={
                    "type": "regression",
                    "current": test_name,
                    "current_log": test_log,
                    "completed": completed_tests,
                    "test_list": test_list,
                    "log_file": log_file,  # overrides base "log_file" (=test_log) with the aggregate regression log
                },
            )

            # Per-test poll (P6-1/P6-2/P6-5 via poll_batch_log)
            _, timed_out = await poll_batch_log(test_log, 600)

            if timed_out:
                # Guard: kill stale xmsim/xmrm to prevent worklib lock on next test
                if test_pid:
                    await shell_run(
                        f"(kill -0 {test_pid}) && kill {test_pid}",
                        timeout=5,
                    )
                await shell_run("pkill -f xmrm || true", timeout=5)
                # Append TIMEOUT marker to per-test log
                await shell_run(
                    f"echo '[TIMEOUT] Test did not complete within 600s' >> {shell_quote(test_log)}",
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
                f"(grep -E 'PASS|FAIL|Errors:|COMPLETE|\\$finish' {shell_quote(test_log)} || true) >> {log_file}",
                timeout=10.0,
            )

    # Cleanup job file
    await shell_run(f"rm -f {job_file}", timeout=5)

    # Parse final results from per-test logs
    # For each test, find its log file — current ts first, then most recent
    # F-152: per-test log collection is independent (each test reads/greps its
    # own log file only) — run concurrently instead of N sequential round-trips.
    async def _collect_test_result(tn: str) -> tuple[str, list[str], str]:
        test_log_path = f"{user_tmp}/regression_{ts}_{tn}.log"
        log_exists = await shell_run(f"test -f {shell_quote(test_log_path)} && echo Y || echo N")
        if "Y" not in log_exists:
            # Find most recent log for this test (resume scenario — different ts)
            test_log_path = (await shell_run(
                f"ls -t {user_tmp}/regression_*_{shell_quote(tn)}.log 2>/dev/null | head -1"
            )).strip()
        if not test_log_path:
            return tn, [], ""
        result_lines = await shell_run(
            f"(grep -E 'PASS|FAIL|Errors:|COMPLETE|\\$finish' {shell_quote(test_log_path)} || true) | tail -30"
        )
        results = result_lines.strip().splitlines() if result_lines.strip() else []
        # Collect error lines for waveform-only tests (NO_VERDICT classification)
        err_lines = await shell_run(
            f"(grep -iE '\\*E |^Error:|fatal error|Segmentation' {shell_quote(test_log_path)} || true) | head -3"
        )
        return tn, results, err_lines.strip()

    per_test_results: dict[str, list[str]] = {tn: [] for tn in test_list}
    per_test_errors: dict[str, str] = {tn: "" for tn in test_list}
    for tn, results, err in await asyncio.gather(*(_collect_test_result(t) for t in test_list)):
        per_test_results[tn] = results
        per_test_errors[tn] = err

    # F-155: verdict classification + summary formatting extracted to a pure function
    log_str = classify_regression_results(test_list, per_test_results, per_test_errors, log_file)

    # v5.2: aggregate dump_stats across per-test summaries (F-155: extracted to a pure function)
    dump_stats = aggregate_dump_stats(per_test_dump_summaries)

    return log_str, dump_stats
