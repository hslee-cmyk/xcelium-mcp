"""Batch simulation execution for xcelium-mcp.

Extracted from sim_runner.py (v4.2 Phase 3 refactoring).
Contains batch execution functions: single-test batch, regression, polling,
parameter resolution, and test name resolution.
"""
from __future__ import annotations

import asyncio
import json
import re as _re
from dataclasses import dataclass

from xcelium_mcp.sim_runner import (
    ssh_run,
    sq,
    build_redirect,
    login_shell_cmd,
)
from xcelium_mcp.registry import load_sim_config, save_sim_config


@dataclass
class ExecInfo:
    cmd: str               # resolved execution command string
    needs_test_name: bool  # True  → {test_name} substitution needed before exec
                           # False → command complete as-is (regression_script builtin)


def validate_extra_args(s: str) -> str:
    """Validate extra_args: reject dangerous shell metacharacters.

    extra_args intentionally contains multiple shell tokens (e.g. "--flag val"),
    so we cannot quote it as a whole.  Instead we reject metacharacters that
    could chain/inject commands.
    """
    if _re.search(r'[;|&$`<>()\n\r]', s):
        raise ValueError(
            f"extra_args contains forbidden shell metacharacter: {s!r}  "
            "Only flags and values are allowed (no ;|&$`<>()\\n\\r characters)."
        )
    return s


def extract_setup_lines(tcl_content: str) -> str:
    """Extract probe/database setup lines from a setup Tcl, stripping run/exit/finish.

    Used by both _prepare_dump_scope_internal and _build_checkpoint_tcl
    to get the probe configuration without simulation control commands.
    """
    lines = []
    for line in tcl_content.splitlines():
        stripped = line.strip().lower()
        # Skip simulation control commands
        if stripped.startswith("run") or stripped.startswith("exit") or stripped.startswith("finish"):
            continue
        # Skip database close (we control this ourselves)
        if "database" in stripped and "close" in stripped:
            continue
        # Skip commented-out control commands
        if stripped.startswith("#") and any(kw in stripped for kw in ("run", "exit", "finish")):
            continue
        lines.append(line)
    return "\n".join(lines)


def read_setup_tcl(runner: dict, sim_dir: str) -> str:
    """Read the setup Tcl content for the current sim_mode.

    MCP server runs on cloud0 — uses direct Path I/O (no ssh_run needed).
    Returns raw file content, or empty string if not found.
    """
    setup_tcls = runner.get("setup_tcls", {})
    mode = runner.get("default_mode", "rtl")
    tcl_rel = setup_tcls.get(mode, "scripts/setup_rtl.tcl")
    p = Path(f"{sim_dir}/{tcl_rel}")
    if p.exists():
        return p.read_text()
    return ""


def _build_checkpoint_tcl(
    test_name: str, chk_dir: str, l1_time: str,
    setup_lines: str,
) -> str:
    """Generate a Tcl script with probe setup + L1/L2 checkpoint saves.

    Uses setup_lines (extracted by extract_setup_lines) — no run/exit included.
    Injected via MCP_INPUT_TCL env var.
    """
    if not l1_time:
        l1_time = "500us"

    l1_name = f"L1_{test_name}"
    l2_name = f"L2_{test_name}"

    return f"""\
# Auto-generated checkpoint Tcl (Phase 4)
# Probe setup from original + L1 at {l1_time} + L2 before $finish

# 1. Probe/database setup (extracted from setup Tcl, run/exit stripped)
{setup_lines}

# 2. Ensure checkpoint directory exists
file mkdir {chk_dir}

# 3. Run to L1 time (common init completion) + save L1
run {l1_time}
catch {{save -simulation worklib.{l1_name}:module -path {chk_dir} -overwrite}}

# 4. Set up L2 save — stop at $finish, save, then continue to exit
stop -create -condition {{\\$finish}} -name _L2_guard -silent
proc _mcp_l2_save {{}} {{
    catch {{save -simulation worklib.{l2_name}:module -path {chk_dir} -overwrite}}
    catch {{stop -delete _L2_guard}}
    run
}}
stop -create -command {{_mcp_l2_save}} -name _L2_trigger

# 5. Continue simulation to $finish
run

# 6. Clean exit
exit
"""


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
        cmd = login_shell_cmd(runner["login_shell"], script_run)

    return ExecInfo(cmd=cmd, needs_test_name=needs_test_name)


async def _run_batch_single(
    sim_dir: str,
    test_name: str,
    runner: dict,
    rename_dump: bool = False,
    run_duration: str = "",
    timeout: int = 600,
    sim_mode: str = "rtl",
    extra_args: str = "",
) -> str:
    """Execute a single simulation test and return combined log output.

    Strategy: nohup + PID watcher + adaptive log polling (P6-1/P6-2/P6-5).

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

    # v4.1: _resolve_sim_params for mode-aware params
    validate_extra_args(extra_args)
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout)
    effective_timeout = params["timeout"]

    # v4.1: use args_format from _resolve_sim_params
    info = _resolve_exec_cmd(runner, regression=False)
    # Format {test_name} placeholder with mode-specific args
    # e.g. {test_name} → "-test VENEZIA_TOP015 --" instead of bare "VENEZIA_TOP015"
    test_args = params["test_args_format"].format(test_name=sq(test_name))
    cmd = info.cmd.format(test_name=test_args) if info.needs_test_name else info.cmd
    if params["extra_args"]:
        cmd = f"{cmd} {params['extra_args']}"

    # Method 6-A: inject TEST_NAME for SHM file naming
    env_prefix = f"TEST_NAME={sq(test_name)} "

    # Always use nohup + stdbuf + polling (no direct ssh_run for batch)
    # Direct ssh_run returns entire compile+sim log (1MB+) — unusable.
    # nohup + polling returns grep summary only.

    # --- nohup + stdbuf + job resume ---
    from xcelium_mcp.sim_runner import get_user_tmp_dir
    user_tmp = await get_user_tmp_dir()
    job_file = f"{user_tmp}/batch_job.json"

    # Check for existing job (reconnection scenario)
    existing_job = await ssh_run(f"cat {job_file} 2>/dev/null")
    if existing_job.strip():
        try:
            job = json.loads(existing_job)
            pid = job.get("pid", 0)
            pid_alive = await ssh_run(f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD")
            if "ALIVE" in pid_alive:
                # Previous batch still running → resume polling
                result, _ = await _poll_batch_log(
                    job["log_file"], effective_timeout,
                    f"(Resumed monitoring existing batch PID {pid})\n"
                )
                await ssh_run(f"rm -f {job_file}", timeout=5)
                return result
            # PID dead → stale job file, remove and start fresh
            await ssh_run(f"rm -f {job_file}", timeout=5)
        except (json.JSONDecodeError, KeyError):
            await ssh_run(f"rm -f {job_file}", timeout=5)

    # Start new batch job
    ts = int(_time.time())
    log_file = f"{user_tmp}/batch_{ts}.log"

    # env VAR=val nohup ... — nohup treats first arg as command, so use env
    # Note: stdbuf removed — LD_PRELOAD incompatible with Xcelium binaries
    run_cmd = f"env {env_prefix}{cmd}"
    # B-0 fix: subshell wrapping to prevent PIPE fd inheritance.
    # Without subshell, nohup child inherits asyncio PIPE fds → communicate()
    # blocks until simulation ends → 15s timeout always fires.
    pid_file = f"{user_tmp}/batch_pid_{ts}"
    await ssh_run(
        f"cd {sq(sim_dir)} && "
        f"(nohup {run_cmd} {build_redirect(log_file)} < /dev/null & echo $! > {pid_file}) "
        f">& /dev/null",
        timeout=15.0,
    )

    # Read PID from file
    pid_str = await ssh_run(f"cat {pid_file} 2>/dev/null", timeout=5)
    pid_str = pid_str.strip()
    # Fallback — use pgrep if pid file didn't yield a number
    if not pid_str.isdigit():
        pid_str = await ssh_run(f"pgrep -f {sq(test_name)} 2>/dev/null | tail -1")
    pid = int(pid_str.strip()) if pid_str.strip().isdigit() else 0
    # Cleanup pid file
    await ssh_run(f"rm -f {pid_file}", timeout=5)

    if pid:
        from datetime import datetime
        job_info = json.dumps({
            "pid": pid,
            "log_file": log_file,
            "test_name": test_name,
            "started_at": datetime.now().isoformat(),
        })
        await ssh_run(f"cat > {job_file} << 'MCPEOF'\n{job_info}\nMCPEOF", timeout=5)
        # P6-5: background watcher — touch {log}.done when PID exits
        done_file = f"{log_file}.done"
        await ssh_run(
            f"(while kill -0 {pid} 2>/dev/null; do sleep 2; done; touch {done_file}) >& /dev/null &",
            timeout=5,
        )

    # Poll for completion
    result, _ = await _poll_batch_log(log_file, effective_timeout)

    # Cleanup job file
    await ssh_run(f"rm -f {job_file}", timeout=5)

    # Method 6-B fallback
    if rename_dump:
        mv_cmd = (
            f"cd {sq(sim_dir)} && "
            f"if [ -d dump/ci_top.shm ]; then "
            f"mv dump/ci_top.shm dump/ci_top_{sq(test_name)}.shm; fi"
        )
        await ssh_run(mv_cmd, timeout=30.0)

    prefix = (
        f"[Stale checkpoints removed: {stale_removed}]\n" if stale_removed else ""
    )
    return prefix + result


async def _run_batch_regression(
    sim_dir: str,
    test_list: list[str],
    runner: dict,
    rename_dump: bool = False,
    sim_mode: str = "",
    extra_args: str = "",
    save_checkpoints: bool = False,
    l1_time: str = "",
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
    import time as _time

    from xcelium_mcp.sim_runner import get_user_tmp_dir
    user_tmp = await get_user_tmp_dir()
    job_file = f"{user_tmp}/regression_job.json"
    ts = int(_time.time())
    log_file = f"{user_tmp}/regression_{ts}.log"

    # v4.1: resolve sim_mode/extra_args for regression
    validate_extra_args(extra_args)
    effective_sim_mode = sim_mode or runner.get("default_mode", "rtl")
    params = resolve_sim_params(runner, effective_sim_mode, extra_args=extra_args)
    info = _resolve_exec_cmd(runner, regression=True)

    # Phase 4: prepare checkpoint setup (read + strip run/exit once)
    chk_dir = f"{sim_dir}/checkpoints"
    setup_lines = ""
    if save_checkpoints:
        await ssh_run(f"mkdir -p {sq(chk_dir)}", timeout=5)
        raw_tcl = read_setup_tcl(runner, sim_dir)
        setup_lines = extract_setup_lines(raw_tcl)

    # Check for existing regression job (reconnection scenario)
    completed_tests: list[str] = []
    existing_job = await ssh_run(f"cat {job_file} 2>/dev/null")
    if existing_job.strip():
        try:
            job = json.loads(existing_job)
            if job.get("type") == "regression":
                pid = job.get("pid", 0)
                pid_alive = await ssh_run(
                    f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD"
                )
                if "ALIVE" in pid_alive:
                    # Current test still running → resume polling
                    current = job.get("current", "")
                    completed_tests = job.get("completed", [])
                    log_file = job.get("log_file", log_file)
                    current_log = job.get("current_log", "")
                    if current_log:
                        _, _ = await _poll_batch_log(current_log, 600)
                        completed_tests.append(current)
                else:
                    # PID dead — check what was completed
                    completed_tests = job.get("completed", [])
                    log_file = job.get("log_file", log_file)
        except (json.JSONDecodeError, KeyError):
            pass
        await ssh_run(f"rm -f {job_file}", timeout=5)

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
            await ssh_run(
                f"cd {sq(sim_dir)} && "
                f"(nohup {run_cmd} {build_redirect(log_file)} < /dev/null &) "
                f">& /dev/null",
                timeout=15.0,
            )
        # P6-1/P6-2: adaptive polling via _poll_batch_log
        _, _ = await _poll_batch_log(log_file, timeout=3600)

    else:
        # Per-test loop — skip completed tests (resume support)
        remaining = [t for t in test_list if t not in completed_tests]

        for test_name in remaining:
            test_log = f"{user_tmp}/regression_{ts}_{sq(test_name)}.log"
            env_prefix = f"TEST_NAME={sq(test_name)} "

            # Phase 4: inject checkpoint save commands into xmsim Tcl
            if save_checkpoints and setup_lines:
                chk_tcl = _build_checkpoint_tcl(
                    test_name, chk_dir, l1_time, setup_lines,
                )
                chk_tcl_path = f"{user_tmp}/chk_{sq(test_name)}.tcl"
                import base64 as _b64
                b64 = _b64.b64encode(chk_tcl.encode()).decode()
                await ssh_run(
                    f"echo {sq(b64)} | base64 -d > {sq(chk_tcl_path)}",
                    timeout=5,
                )
                env_prefix += f"MCP_INPUT_TCL={sq(chk_tcl_path)} "

            test_args = params["test_args_format"].format(test_name=sq(test_name))
            cmd = info.cmd.format(test_name=test_args) if info.needs_test_name else info.cmd
            if params["extra_args"]:
                cmd = f"{cmd} {params['extra_args']}"

            # Save job state (for resume on reconnection)
            from datetime import datetime
            job_info = json.dumps({
                "type": "regression",
                "pid": 0,  # updated after nohup
                "log_file": log_file,
                "current": test_name,
                "current_log": test_log,
                "completed": completed_tests,
                "started_at": datetime.now().isoformat(),
            })
            await ssh_run(f"cat > {job_file} << 'MCPEOF'\n{job_info}\nMCPEOF", timeout=5)

            # B-0 fix: subshell wrapping, stdbuf removed (Xcelium incompatible)
            # P6-5b: echo $! > pid_file inside subshell — aligns with _run_batch_single
            run_cmd = f"env {env_prefix}{cmd}"
            pid_file = f"{test_log}.pid"
            await ssh_run(
                f"cd {sq(sim_dir)} && "
                f"(nohup {run_cmd} {build_redirect(test_log)} < /dev/null & echo $! > {pid_file}) "
                f">& /dev/null",
                timeout=15.0,
            )

            # Read PID from pid_file (pgrep -f {test_name} fails: xmsim cmdline has no test name)
            pid_str = await ssh_run(f"cat {pid_file} 2>/dev/null; rm -f {pid_file}", timeout=5)
            if pid_str.strip().isdigit():
                test_pid = int(pid_str.strip())
                job_update = json.dumps({
                    "type": "regression", "pid": test_pid,
                    "log_file": log_file, "current": test_name,
                    "current_log": test_log, "completed": completed_tests,
                })
                await ssh_run(f"cat > {job_file} << 'MCPEOF'\n{job_update}\nMCPEOF", timeout=5)
                # P6-5: PID watcher for per-test done marker
                # >& /dev/null: B-0 fix — detach from asyncio PIPE fds
                test_done = f"{test_log}.done"
                await ssh_run(
                    f"(while kill -0 {test_pid} 2>/dev/null; do sleep 2; done; touch {test_done}) >& /dev/null &",
                    timeout=5,
                )

            # Per-test poll (P6-1/P6-2/P6-5 via _poll_batch_log)
            _, timed_out = await _poll_batch_log(test_log, 600)

            if timed_out:
                # Guard: kill stale xmsim/xmrm to prevent worklib lock on next test
                if pid_str.strip().isdigit():
                    await ssh_run(
                        f"kill -0 {test_pid} 2>/dev/null && kill {test_pid}",
                        timeout=5,
                    )
                await ssh_run("pkill -f xmrm 2>/dev/null", timeout=5)
                # Append TIMEOUT marker to per-test log
                await ssh_run(
                    f"echo '[TIMEOUT] Test did not complete within 600s' >> {test_log}",
                    timeout=5,
                )

            completed_tests.append(test_name)

            # Method 6-B fallback
            if rename_dump:
                mv_cmd = (
                    f"cd {sq(sim_dir)} && "
                    f"if [ -d dump/ci_top.shm ]; then "
                    f"mv dump/ci_top.shm dump/ci_top_{sq(test_name)}.shm; fi"
                )
                await ssh_run(mv_cmd, timeout=30.0)

            # Append per-test result to main log
            await ssh_run(
                f"echo {sq('=== ' + test_name + ' ===')} >> {log_file} && "
                f"grep -E 'PASS|FAIL|Errors:' {test_log} 2>/dev/null >> {log_file}",
                timeout=10.0,
            )

    # Cleanup job file
    await ssh_run(f"rm -f {job_file}", timeout=5)

    # Parse final results from per-test logs (reliable regardless of append timing)
    all_parts: list[str] = []
    pass_count = 0
    fail_count = 0
    for tn in test_list:
        t_log = f"{user_tmp}/regression_{ts}_{sq(tn)}.log"
        t_raw = await ssh_run(
            f"grep -E 'PASS|FAIL|Errors:|COMPLETE' {t_log} | tail -20",
            timeout=30.0,
        )
        all_parts.append(f"=== {tn} ===\n{t_raw}")
        pass_count += t_raw.count("PASS")
        fail_count += t_raw.count("FAIL")

    total = len(test_list)
    raw = "\n".join(all_parts)
    summary = f"{pass_count}/{total} tests PASS, {fail_count} FAIL"
    details = raw[:4000] if raw.strip() else "(no PASS/FAIL lines found in per-test logs)"
    return f"{summary}\n\nLog ({log_file}):\n{details}"


async def _poll_batch_log(log_file: str, timeout: float, prefix: str = "") -> str:
    """Poll a batch log file until completion keywords found or timeout.

    P6-1: Adaptive polling interval — 2s → 3s → 4.5s → 6.75s → 10s cap.
          Short gap catches fast tests; longer gap reduces SSH overhead for slow ones.
    P6-2: Single SSH call per poll — tail + done-file check in one round-trip.
    P6-5: .done marker file — reliable completion signal even when keywords scroll past tail.

    Returns: (result_str, timed_out) — timed_out=True when poll exhausted without completion.
    """
    import time as _time
    deadline = _time.time() + timeout
    interval = 2.0          # P6-1: start at 2s
    done_file = f"{log_file}.done"
    timed_out = True

    while _time.time() < deadline:
        # P6-2: single SSH call — tail for keyword scan + done-file sentinel
        out = await ssh_run(
            f"tail -10 {log_file} 2>/dev/null; "
            f"test -f {done_file} && echo __DONE__"
        )
        if "__DONE__" in out or any(
            kw in out for kw in ("$finish", "COMPLETE", "PASS", "FAIL", "Errors:")
        ):
            timed_out = False
            break
        # P6-1: adaptive backoff (×1.5, cap 10s)
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 10.0)

    result = await ssh_run(
        f"grep -E 'PASS|FAIL|Errors:|\\$finish|COMPLETE' {log_file} 2>/dev/null | tail -30"
    )
    await ssh_run(f"rm -f {done_file}", timeout=5)   # P6-5: cleanup marker
    return prefix + result, timed_out


# ===========================================================================
# v4.1 Phase 1b: resolve_sim_params + resolve_test_name
# ===========================================================================


def resolve_sim_params(
    runner: dict,
    sim_mode: str = "rtl",
    extra_args: str = "",
    timeout: int = 600,
) -> dict:
    """Resolve simulation parameters from registry schema — Single Point of Change.

    All tools (sim_start, sim_batch_run, sim_batch_regression) call this.
    Schema changes → modify here only → all tools updated.

    Returns:
        {"test_args_format": str, "timeout": int,
         "probe_strategy": str, "extra_args": str}
    """
    # 1. args_format: dict → mode선택, string → 전 mode 동일
    args_raw = runner.get("args_format", "-test {test_name} --")
    if isinstance(args_raw, dict):
        test_args_format = args_raw.get(sim_mode, args_raw.get("rtl", "-test {test_name} --"))
    else:
        test_args_format = args_raw

    # 2. mode_defaults: common + mode merge
    mode_defaults = runner.get("mode_defaults", {})
    common_cfg = mode_defaults.get("common", {})
    mode_cfg = mode_defaults.get(sim_mode, {})
    effective = {**common_cfg, **mode_cfg}

    # 3. extra_args: config + 1회성 합침
    cfg_extra = effective.get("extra_args", "")
    all_extra = f"{cfg_extra} {extra_args}".strip()

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
    }


async def resolve_test_name(short_name: str, sim_dir: str = "") -> str:
    """Short name → full test name via cached_tests.

    "TOP015" → "VENEZIA_TOP015_i2c_8bit_offset_test"
    Exact match → return. 1 substring match → return. 0 → error. 2+ → candidates.
    Cache miss → triggers list_tests (mcp_config 경유 캐시 저장).
    """
    # Lazy import to avoid circular dependency (sim_runner → batch_runner → sim_runner)
    from xcelium_mcp.sim_runner import get_default_sim_dir
    resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
    cfg = await load_sim_config(resolved_dir) if resolved_dir else None
    cached = cfg.get("test_discovery", {}).get("cached_tests", []) if cfg else []

    if not cached:
        # Cache miss — run test_discovery.command + cache via mcp_config
        if cfg:
            discovery = cfg.get("test_discovery", {})
            cmd = discovery.get("command", "")
            if cmd:
                r = await ssh_run(f"cd {sq(resolved_dir)} && {cmd}", timeout=30)
                cached = [t.strip() for t in r.strip().splitlines() if t.strip()]
                if cached:
                    # Cache via config_action (write centralization)
                    from datetime import datetime
                    cfg.setdefault("test_discovery", {})["cached_tests"] = cached
                    cfg["test_discovery"]["cached_at"] = datetime.now().isoformat()
                    await save_sim_config(resolved_dir, cfg)

    if not cached:
        return short_name  # No cache, no command → pass through

    # Exact match
    if short_name in cached:
        return short_name

    # Substring match
    matches = [t for t in cached if short_name in t]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) == 0:
        raise ValueError(f"No test matching '{short_name}'. Run list_tests() to see available.")
    else:
        raise ValueError(
            f"Multiple tests match '{short_name}':\n"
            + "\n".join(f"  {m}" for m in matches)
            + "\nSpecify more precisely."
        )


# Backward-compat aliases
_validate_extra_args = validate_extra_args
_resolve_sim_params = resolve_sim_params
_resolve_test_name = resolve_test_name
