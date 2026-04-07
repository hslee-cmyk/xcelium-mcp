"""Batch simulation execution for xcelium-mcp.

Extracted from sim_runner.py (v4.2 Phase 3 refactoring).
Contains batch execution functions: single-test batch, regression, polling,
parameter resolution, and test name resolution.
"""
from __future__ import annotations

import asyncio
import base64 as _b64
import json
from pathlib import Path
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


def _parse_l1_time_ns(l1_time: str) -> int:
    """Convert l1_time string (e.g. "500us", "1ms") to nanoseconds."""
    m = _re.match(r'(\d+)\s*(us|ms|ns)?', l1_time.strip())
    if not m:
        return 0
    val = int(m.group(1))
    unit = m.group(2) or "ns"
    if unit == "ms":
        return val * 1_000_000
    if unit == "us":
        return val * 1_000
    return val


def extract_setup_lines(tcl_content: str) -> str:
    """Extract probe/database setup lines from a setup Tcl, stripping run/exit/finish.

    Used by _build_checkpoint_tcl and _preprocess_setup_tcl
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
        # Skip commented-out control commands (# run, #exit, #finish as first word)
        if stripped.startswith("#"):
            words = stripped.lstrip("#").strip().split()
            if words and words[0] in ("run", "exit", "finish"):
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


def _replace_shm_stems(content: str, test_name: str) -> str:
    """Replace <stem>.shm with <stem>_{test_name}.shm in Tcl SHM references.

    Targets all Tcl lines that reference .shm paths:
      - ``database -open <path>/<stem>.shm``
      - ``database -close <path>/<stem>.shm``
      - ``probe ... -database <path>/<stem>.shm``

    Stem discovery uses ``database -open`` lines only. If there is no
    ``database -open`` line (e.g. probe-only content), no replacement is made.
    Generic: works with any SHM name. Skips if stem already contains test_name.
    """
    # Step 1: find SHM stems from database -open lines
    pattern = r"database\s+-open\s+(?:\S*/)?(\S+)\.shm"
    matches = _re.findall(pattern, content)

    if not matches:
        return content

    # Step 2: for each unique stem, replace in all Tcl command contexts
    replaced: set[str] = set()
    for stem in matches:
        if stem in replaced or test_name in stem:
            continue
        escaped = _re.escape(stem)
        # Replace in: database -open, database -close, probe ... -database
        content = _re.sub(
            r"((?:database\s+(?:-open|-close)|probe\s+.*?-database)\s+(?:\S*/)?)"
            + escaped + r"\.shm",
            rf"\1{stem}_{test_name}.shm",
            content,
        )
        replaced.add(stem)

    return content


# ---------------------------------------------------------------------------
# v4.3: Dump depth — boundary signals + probe line management
# ---------------------------------------------------------------------------

BOUNDARY_SIGNALS = [
    "top.hw.i_mainClk", "top.hw.i_rst_n",
    "top.hw.i_scl", "top.hw.io_sda",
    "top.hw.i_pcmIn", "top.hw.i_pcmSync",
    "top.hw.o_askData", "top.hw.o_askDataInv",
    "top.hw.o_askRefClk", "top.hw.o_refClk", "top.hw.o_refClkInv",
    "top.hw.o_btCoilShort",
    "top.hw.i_backTel_p", "top.hw.i_backTel_n",
    "top.hw.o_backTel_pwr_en",
    "top.hw.i_led_ctrl_r", "top.hw.i_led_ctrl_g", "top.hw.i_led_ctrl_b",
    "top.hw.o_led_r", "top.hw.o_led_g", "top.hw.o_led_b",
    "top.hw.i_earpiece_det_n", "top.hw.i_rmClkNum",
    "top.hw.i_deep_slp_en", "top.hw.i_dyn_slp_en",
    "top.hw.o_sync_req", "top.hw.o_stim_trig", "top.hw.o_serial_tp_out",
]


def _resolve_probe_signals(
    dump_signals: list[str] | None,
    dump_depth: str,
) -> tuple[str, list[str] | None]:
    """Resolve final probe signal set based on dump_depth and dump_signals.

    dump_depth="all" → probe -create top -depth all (dump_signals ignored).
    dump_depth="boundary" → BOUNDARY_SIGNALS union dump_signals (deduped).

    Returns:
        ("depth_all", None)              — probe -create top -depth all
        ("signals", [sig1, sig2, ...])   — probe -create {each} individually
    """
    if dump_depth == "all":
        return ("depth_all", None)

    base = set(BOUNDARY_SIGNALS)
    if dump_signals:
        base |= set(dump_signals)

    return ("signals", sorted(base))


def _generate_probe_reset_tcl(probe_type: str, probe_signals: list[str] | None) -> str:
    """Generate Tcl commands to reset probe configuration after checkpoint restore.

    Sequence: disable existing probes → add new probes → enable.
    Used when from_checkpoint + dump_depth is specified.
    """
    lines = []
    lines.append("probe -disable")

    if probe_type == "depth_all":
        lines.append("probe -create top -depth all -shm")
    elif probe_signals:
        for sig in probe_signals:
            lines.append(f"probe -create {sig} -shm")

    lines.append("probe -enable")
    return "\n".join(lines) + "\n"


def _replace_probe_lines(
    content: str, probe_type: str, probe_signals: list[str] | None,
) -> str:
    """Adjust probe lines in setup tcl based on dump_depth.

    - Scope probes (-depth option) → removed
    - Specific signal probes (user custom) → kept
    - New probes added based on dump_depth (deduped against existing)
    """
    lines = content.splitlines()

    filtered = []
    existing_signals: set[str] = set()
    for line in lines:
        if _re.match(r"\s*probe\s+-create\b", line):
            if "-depth" in line:
                continue  # scope probe → remove
            sig_match = _re.search(r"probe\s+-create\s+(\S+)", line)
            if sig_match:
                existing_signals.add(sig_match.group(1))
            filtered.append(line)
        else:
            filtered.append(line)

    if probe_type == "depth_all":
        new_probes = ["probe -create top -depth all -shm"]
    else:
        new_probes = [
            f"probe -create {sig} -shm"
            for sig in (probe_signals or [])
            if sig not in existing_signals
        ]

    # Insert after database -open, or at start if not found
    result: list[str] = []
    inserted = False
    for line in filtered:
        result.append(line)
        if not inserted and _re.match(r"\s*database\s+-open\b", line):
            result.extend(new_probes)
            inserted = True

    if not inserted:
        result = new_probes + result

    return "\n".join(result) + "\n"


def _inject_dump_window(content: str, dump_window: dict) -> str:
    """Inject probe on/off + run sequence for dump_window (Batch mode only).

    Replaces existing 'run' command with windowed probe on/off + run sequence.
    Setup tcl에 직접 주입되므로 bridge 통신 불필요.

    Args:
        content: setup tcl content
        dump_window: {"start_ms": int, "end_ms": int}
    """
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms

    # 기존 run 명령 제거
    lines = content.splitlines()
    filtered = [line for line in lines if not _re.match(r"\s*run(\s|$)", line)]

    window_tcl = ["probe -disable"]
    if start_ms > 0:
        window_tcl.append(f"run {start_ms}ms")
    window_tcl.append("probe -enable")
    window_tcl.append(f"run {duration_ms}ms")
    window_tcl.append("probe -disable")
    window_tcl.append("run")  # $finish까지

    return "\n".join(filtered + window_tcl) + "\n"


# ---------------------------------------------------------------------------
# v4.3: SDF override
# ---------------------------------------------------------------------------

async def _handle_sdf_override(
    sim_dir: str, runner: dict, sdf_file: str, sdf_corner: str,
) -> str:
    """Handle SDF override: disable TB $sdf_annotate + generate tfile.

    Returns extra_args string to append (e.g. "-define NODLY -tfile ...").
    """
    # Validate sdf_file path (prevent command injection)
    if not _re.fullmatch(r"[\w./\-]+", sdf_file):
        raise ValueError(f"Invalid sdf_file path: {sdf_file!r}")

    from xcelium_mcp.sim_runner import get_user_tmp_dir

    config = await load_sim_config(sim_dir)
    sdf_info = (config or {}).get("sdf_info", {})
    extra_defines: list[str] = []

    # Step 1: disable TB $sdf_annotate
    if sdf_info.get("has_sdf_annotate"):
        guard = sdf_info.get("sdf_guard_define")
        if guard:
            extra_defines.append(f"-define {guard}")
        else:
            await _patch_tb_sdf_guard(sim_dir, sdf_info)
            extra_defines.append("-define MCP_SDF_OVERRIDE")

    # Step 2: generate tfile with scope-aware entries
    corner_map = {"min": "MINIMUM", "max": "MAXIMUM", "typ": "TYPICAL"}
    sdf_corner_upper = corner_map.get(sdf_corner, "MAXIMUM")

    user_tmp = await get_user_tmp_dir()
    tfile_path = f"{user_tmp}/mcp_sdf_tfile"

    sdf_entries = sdf_info.get("sdf_entries", [])
    scopes = sorted(set(e["scope"] for e in sdf_entries)) if sdf_entries else ["top"]

    tfile_lines: list[str] = []
    for scope in scopes:
        tfile_lines.append(f'COMPILED_SDF_FILE "{sdf_file}"')
        tfile_lines.append(f"  SCOPE {scope}")
        tfile_lines.append(f"  {sdf_corner_upper}")
        tfile_lines.append(";")
    tfile_content = "\n".join(tfile_lines) + "\n"

    # Write tfile via base64 (avoid heredoc delimiter injection)
    b64 = _b64.b64encode(tfile_content.encode()).decode()
    await ssh_run(
        f"echo {sq(b64)} | base64 -d > {sq(tfile_path)}",
        timeout=10,
    )

    # Step 3: build elab extra args
    elab_extra = f"-delay_mode path -sdf_verbose -timescale 1ns/1fs -tfile {tfile_path}"
    return " ".join(extra_defines + [elab_extra])


async def _patch_tb_sdf_guard(sim_dir: str, sdf_info: dict) -> None:
    """Patch TB RTL: add `ifndef MCP_SDF_OVERRIDE guard around $sdf_annotate.

    Only called when sdf_guard_define is None (no existing guard).
    Creates backup before patching.
    """
    from xcelium_mcp.sim_runner import get_user_tmp_dir

    top_v = sdf_info.get("sdf_source_file", "")
    if not top_v:
        return

    # Backup
    user_tmp = await get_user_tmp_dir()
    filename = top_v.split("/")[-1]
    await ssh_run(f"cp {sq(top_v)} {user_tmp}/{filename}.bak.mcp_sdf", timeout=5)

    # Patch: wrap $sdf_annotate block with `ifndef MCP_SDF_OVERRIDE
    content = await ssh_run(f"cat {sq(top_v)}", timeout=10)

    patched = _re.sub(
        r"(\s*initial\s+begin\s*\n)(.*?\$sdf_annotate.*?\n)(.*?\s*end)",
        r"\1`ifndef MCP_SDF_OVERRIDE\n\2`endif\n\3",
        content,
        flags=_re.DOTALL,
    )

    if patched != content:
        # Write via base64 (avoid heredoc delimiter injection)
        b64 = _b64.b64encode(patched.encode()).decode()
        await ssh_run(
            f"echo {sq(b64)} | base64 -d > {sq(top_v)}",
            timeout=10,
        )


async def _preprocess_setup_tcl(
    sim_dir: str, runner: dict, test_name: str, sim_mode: str = "",
    dump_depth: str = "all",
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
) -> str:
    """Preprocess setup_tcl: SHM naming + probe scope + dump window (v4.3).

    1. SHM stem replacement (existing): ``<stem>.shm`` → ``<stem>_{test_name}.shm``
    2. Probe scope adjustment (v4.3): remove scope probes, add dump_depth-based probes
    3. Dump window (v4.3): replace 'run' with probe on/off + windowed run sequence

    Returns temp file path for MCP_INPUT_TCL injection, or empty string
    if no replacement needed.
    """
    # Validate test_name for filesystem/Tcl safety
    if not _re.fullmatch(r"[A-Za-z0-9_\-]+", test_name):
        return ""

    content = read_setup_tcl(runner, sim_dir)
    if not content:
        return ""

    changed = False

    # Step 1: SHM stem replacement (existing)
    if "$env(TEST_NAME)" not in content:
        new_content = _replace_shm_stems(content, test_name)
        if new_content != content:
            content = new_content
            changed = True

    # Step 2: probe scope adjustment (v4.3)
    probe_type, probe_signals = _resolve_probe_signals(dump_signals, dump_depth)
    new_content = _replace_probe_lines(content, probe_type, probe_signals)
    if new_content != content:
        content = new_content
        changed = True

    # Step 3: dump window — replace 'run' with probe on/off sequence (v4.3)
    if dump_window:
        new_content = _inject_dump_window(content, dump_window)
        if new_content != content:
            content = new_content
            changed = True

    if not changed:
        return ""

    # Write preprocessed tcl to temp location
    from xcelium_mcp.sim_runner import get_user_tmp_dir
    user_tmp = await get_user_tmp_dir()
    out_path = f"{user_tmp}/setup_batch_{test_name}.tcl"
    Path(out_path).write_text(content)

    return out_path


def _build_checkpoint_tcl(
    test_name: str, chk_dir: str, l1_time: str,
    setup_lines: str,
) -> str:
    """Generate a Tcl script with probe setup + L1 checkpoint save.

    Saves L1 at l1_time (common init completion), then continues to $finish.
    L2 ($finish) is not saved — SHM dump is sufficient for post-mortem analysis.

    Uses setup_lines (extracted by extract_setup_lines) — no run/exit included.
    Injected via MCP_INPUT_TCL env var.
    """
    # Replace SHM paths with test-specific names
    setup_lines = _replace_shm_stems(setup_lines, test_name)

    l1_ns = _parse_l1_time_ns(l1_time) if l1_time else 500000
    l1_name = f"L1_{test_name}"

    return f"""\
# Auto-generated checkpoint Tcl (Phase 4)
# Probe setup + L1 at {l1_ns}ns

# 1. Probe/database setup (extracted from setup Tcl, run/exit stripped)
{setup_lines}

# 2. Ensure checkpoint directory exists
file mkdir {chk_dir}

# 3. Run to L1 time (common init completion) + save L1
run {l1_ns}ns
catch {{save -simulation worklib.{l1_name}:module -path {chk_dir} -overwrite}}

# 4. Continue simulation to $finish
run

# 5. Clean exit
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
        sources = " && ".join(f"source {sq(f)}" for f in runner.get("env_files", []))
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
    dump_depth: str | None = None,
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    sdf_file: str = "",
    sdf_corner: str = "max",
) -> str:
    """Execute a single simulation test and return combined log output.

    Strategy: nohup + PID watcher + adaptive log polling (P6-1/P6-2/P6-5).

    SHM naming: Preprocesses setup_tcl to replace hardcoded SHM paths
    (e.g. ``dump/ci_top.shm``) with test-specific names
    (``dump/ci_top_{TEST_NAME}.shm``) before simulation runs.
    Injected via MCP_INPUT_TCL env var so run_sim sources the modified tcl.

    v4.3: dump_depth/dump_signals control probe scope in setup tcl.
           dump_window injects probe on/off + windowed run sequence.
           sdf_file/sdf_corner for SDF override.
    """
    import time as _time

    # v4.1: _resolve_sim_params for mode-aware params
    # v4.3: dump_depth forwarded to resolve_sim_params
    validate_extra_args(extra_args)
    params = resolve_sim_params(runner, sim_mode, extra_args, timeout, dump_depth=dump_depth)
    effective_timeout = params["timeout"]
    effective_dump_depth = params["dump_depth"]

    # v4.1: use args_format from _resolve_sim_params
    info = _resolve_exec_cmd(runner, regression=False)
    # Format {test_name} placeholder with mode-specific args
    # e.g. {test_name} → "-test VENEZIA_TOP015 --" instead of bare "VENEZIA_TOP015"
    test_args = params["test_args_format"].format(test_name=sq(test_name))
    cmd = info.cmd.format(test_name=test_args) if info.needs_test_name else info.cmd
    if params["extra_args"]:
        cmd = f"{cmd} {params['extra_args']}"

    # v4.3: SDF override — disable TB $sdf_annotate + generate tfile
    if sdf_file:
        sdf_extra = await _handle_sdf_override(sim_dir, runner, sdf_file, sdf_corner)
        if sdf_extra:
            cmd = f"{cmd} {sdf_extra}"

    # SHM naming + probe scope + dump window: preprocess setup_tcl.
    env_prefix = f"TEST_NAME={sq(test_name)} "
    preprocessed_tcl = await _preprocess_setup_tcl(
        sim_dir, runner, test_name, sim_mode,
        dump_depth=effective_dump_depth, dump_signals=dump_signals,
        dump_window=dump_window,
    )
    if preprocessed_tcl:
        env_prefix += f"MCP_INPUT_TCL={sq(preprocessed_tcl)} "

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
        import base64 as _b64
        job_info = json.dumps({
            "pid": pid,
            "log_file": log_file,
            "test_name": test_name,
            "started_at": datetime.now().isoformat(),
        })
        b64 = _b64.b64encode(job_info.encode()).decode()
        await ssh_run(f"echo {b64} | base64 -d > {job_file}", timeout=5)
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

    # Method 6-B fallback (deprecated — kept for backward compat)
    # Prefer preprocessed_tcl approach above which names SHM correctly from the start.
    if rename_dump and not preprocessed_tcl:
        mv_cmd = (
            f"cd {sq(sim_dir)} && "
            f"if [ -d dump/ci_top.shm ]; then "
            f"mv dump/ci_top.shm dump/ci_top_{sq(test_name)}.shm; fi"
        )
        await ssh_run(mv_cmd, timeout=30.0)

    return result


async def _run_batch_regression(
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
    import time as _time

    from xcelium_mcp.sim_runner import get_user_tmp_dir
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
                    env_prefix += f"MCP_INPUT_TCL={sq(preprocessed_tcl)} "

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
            import base64 as _b64
            job_info = json.dumps({
                "type": "regression",
                "pid": 0,  # updated after nohup
                "log_file": log_file,
                "current": test_name,
                "current_log": test_log,
                "completed": completed_tests,
                "started_at": datetime.now().isoformat(),
            })
            b64 = _b64.b64encode(job_info.encode()).decode()
            await ssh_run(f"echo {b64} | base64 -d > {job_file}", timeout=5)

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
                b64_upd = _b64.b64encode(job_update.encode()).decode()
                await ssh_run(f"echo {b64_upd} | base64 -d > {job_file}", timeout=5)
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

            # Phase 4: register L1/L2 in checkpoint manifest
            if save_checkpoints and not timed_out:
                from xcelium_mcp import checkpoint_manager as _ckpt
                l1_ns = _parse_l1_time_ns(l1_time)
                _ckpt.register_checkpoint(
                    sim_dir, f"L1_{test_name}", l1_ns,
                    origin="regression", test_name=test_name,
                )

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


async def _poll_batch_log(log_file: str, timeout: float, prefix: str = "") -> tuple[str, bool]:
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
    dump_depth: str | None = None,
) -> dict:
    """Resolve simulation parameters from registry schema — Single Point of Change.

    All tools (sim_bridge_run, sim_batch_run, sim_regression) call this.
    Schema changes → modify here only → all tools updated.

    Returns:
        {"test_args_format": str, "timeout": int,
         "probe_strategy": str, "extra_args": str, "dump_depth": str}
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

    # v4.3: extra_args combo warnings (warn only, never block)
    warnings: list[str] = []
    if extra_args:
        ea_lower = extra_args.lower()
        if sim_mode == "rtl" and any(k in ea_lower for k in ("-max", "-worst", "-best", "-min")):
            warnings.append("WARNING: corner options are typically for gate/ams mode, not rtl")
        if sim_mode == "gate" and "-ams" in ea_lower:
            warnings.append("WARNING: AMS option in gate mode — use sim_mode='ams_gate' instead")
        if not sim_mode and ("-gate" in ea_lower or "-gate post" in ea_lower):
            warnings.append("WARNING: use sim_mode='gate' instead of extra_args for gate mode")

    # v4.3: dump_depth 결정
    if dump_depth is not None:
        effective_dump_depth = dump_depth
    else:
        effective_dump_depth = effective.get("dump_depth", "all")

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
        "dump_depth": effective_dump_depth,
        "warnings": warnings,
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

