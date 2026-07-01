"""tcl_preprocessing.py — Tcl setup file preprocessing for xcelium-mcp.

Extracted from batch_runner.py (v4.4 code review refactoring).
Contains: SHM stem replacement, probe line management, dump window injection,
SDF override, checkpoint Tcl generation, setup Tcl preprocessing.
v5.2: Hierarchical dump strategy (dump_scopes, block_boundaries, dump_summary).
"""
from __future__ import annotations

# Re-export for batch_runner backward compat
import asyncio
import base64 as _b64
import re as _re
from fnmatch import fnmatch
from pathlib import Path

from xcelium_mcp.shell_utils import shell_quote, shell_run, validate_path


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
        if stripped.startswith("run") or stripped.startswith("exit") or stripped.startswith("finish"):
            continue
        if "database" in stripped and "close" in stripped:
            continue
        if stripped.startswith("#"):
            words = stripped.lstrip("#").strip().split()
            if words and words[0] in ("run", "exit", "finish"):
                continue
        lines.append(line)
    return "\n".join(lines)


def _read_setup_tcl_sync(runner: dict, sim_dir: str) -> str:
    """Read the setup Tcl content synchronously for the current sim_mode.

    MCP server runs on cloud0 — uses direct Path I/O (no shell_run needed).
    Returns raw file content, or empty string if not found.
    """
    setup_tcls = runner.get("setup_tcls", {})
    mode = runner.get("default_mode", "rtl")
    tcl_rel = setup_tcls.get(mode, "scripts/setup_rtl.tcl")
    p = Path(f"{sim_dir}/{tcl_rel}")
    if p.exists():
        return p.read_text()
    return ""


def read_setup_tcl(runner: dict, sim_dir: str) -> str:
    """Read the setup Tcl content for the current sim_mode.

    Synchronous wrapper — use read_setup_tcl_async from async code.
    Returns raw file content, or empty string if not found.
    """
    return _read_setup_tcl_sync(runner, sim_dir)


async def read_setup_tcl_async(runner: dict, sim_dir: str) -> str:
    """Async version of read_setup_tcl — wraps file I/O in asyncio.to_thread."""
    return await asyncio.to_thread(_read_setup_tcl_sync, runner, sim_dir)


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
    pattern = r"database\s+-open\s+(?:\S*/)?(\S+)\.shm"
    matches = _re.findall(pattern, content)

    if not matches:
        return content

    replaced: set[str] = set()
    for stem in matches:
        if stem in replaced or test_name in stem:
            continue
        escaped = _re.escape(stem)
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
# v5.2: Hierarchical dump strategy helpers
# ---------------------------------------------------------------------------

def get_dump_strategy(config: dict, sim_mode: str) -> dict:
    """Return the dump_strategy sub-dict for the given sim_mode.

    Supports mode-keyed format (v5.2) and flat format (v5.1 fallback).
    AMS modes delegate to their base mode (ams_rtl→rtl, ams_gate→gate).
    """
    strategy = config.get("dump_strategy", {})
    base_mode = "gate" if "gate" in sim_mode else "rtl"
    if base_mode in strategy:
        return strategy[base_mode]
    # v5.1 flat format fallback
    if "top_boundary" in strategy or "block_boundaries" in strategy:
        return strategy
    return {}


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
    dump_scopes: dict[str, str] | None = None,
    dump_strategy: dict | None = None,
    sim_mode: str = "",
) -> tuple[str, dict | list | None, dict | None]:
    """Resolve final probe signal set based on dump_depth, dump_signals, and dump_scopes.

    v5.2: Extended to support hierarchical block-boundary dump strategy.
    dump_scopes values: "all" | "boundary" | "skip".
    fnmatch glob matching: * matches any character including dot.

    Returns (probe_type, probe_info, dump_summary):
        ("depth_all", None, None)              — probe -create top -depth all
        ("signals", [sig1, ...], None)         — v5.1 compat (no block_boundaries)
        ("hierarchical", {                     — v5.2 block-boundary mode
            "signals": [...],
            "scope_probes": [{"scope": ..., "depth": "all"}, ...]
        }, dump_summary)
    """
    if dump_depth == "all":
        return ("depth_all", None, None)

    strategy = dump_strategy or {}
    top_signals = strategy.get("top_boundary") or BOUNDARY_SIGNALS
    signals: set[str] = set(top_signals)
    block_bounds: dict[str, list[str]] = strategy.get("block_boundaries", {})
    default_policy = strategy.get("default_block_policy", "skip")
    included_scopes: set[str] = set()

    # opt-out model: include all block boundaries upfront
    if default_policy == "boundary":
        for scope, sigs in block_bounds.items():
            signals |= set(sigs)
        included_scopes = set(block_bounds.keys())

    # Process dump_scopes overrides
    scope_probes: list[dict] = []
    for pattern, strat in (dump_scopes or {}).items():
        matched = [s for s in block_bounds if fnmatch(s, pattern)]
        if strat == "all":
            # xmsim native glob — pass pattern directly to TCL
            scope_probes.append({"scope": pattern, "depth": "all"})
            # Remove boundary signals for matched scopes to avoid duplication
            for sc in matched:
                signals -= set(block_bounds[sc])
                included_scopes.discard(sc)
        elif strat == "skip":
            for sc in matched:
                signals -= set(block_bounds[sc])
                included_scopes.discard(sc)
        elif strat == "boundary":
            # opt-in: add matched block boundaries
            for sc in matched:
                signals |= set(block_bounds[sc])
                included_scopes.add(sc)
        else:
            raise ValueError(
                f"Invalid dump_scopes value {strat!r} for {pattern!r}. "
                "Must be 'all', 'boundary', or 'skip'."
            )

    if dump_signals:
        signals |= set(dump_signals)

    # Backward compat: no block_boundaries defined + no new features used → v5.1 behavior
    if (not block_bounds and not scope_probes
            and not any(v == "boundary" for v in (dump_scopes or {}).values())):
        return ("signals", sorted(signals), None)

    # Build dump_summary for dump_history recording
    block_counts = {
        sc: (len(sigs) if sc in included_scopes else 0)
        for sc, sigs in block_bounds.items()
    }
    dump_summary = {
        "dump_depth": "boundary",
        "sim_mode": sim_mode,
        "top_boundary_count": len(top_signals),
        "block_boundaries": block_counts,
        "scope_overrides": dump_scopes or {},
        "total_signals": len(signals),
    }

    return ("hierarchical", {
        "signals": sorted(signals),
        "scope_probes": scope_probes,
    }, dump_summary)


def _generate_probe_reset_tcl(
    probe_type: str,
    probe_info: dict | list | None,
) -> str:
    """Generate Tcl commands to reset probe configuration after checkpoint restore."""
    lines = ["probe -disable"]

    if probe_type == "depth_all":
        lines.append("probe -create top -depth all -shm")
    elif probe_type == "hierarchical" and probe_info:
        for sig in probe_info["signals"]:  # type: ignore[index]
            lines.append(f"probe -create {sig} -shm")
        for sp in probe_info["scope_probes"]:  # type: ignore[index]
            lines.append(f"probe -create {sp['scope']} -depth {sp['depth']} -shm")
    elif probe_info:
        # "signals" type — probe_info is a list
        for sig in probe_info:
            lines.append(f"probe -create {sig} -shm")

    lines.append("probe -enable")
    return "\n".join(lines) + "\n"


def _replace_probe_lines(
    content: str,
    probe_type: str,
    probe_info: dict | list | None,
) -> str:
    """Adjust probe lines in setup tcl based on dump_depth.

    v5.2: probe_info accepts dict (hierarchical) or list (signals) or None.
    """
    lines = content.splitlines()

    filtered = []
    existing_signals: set[str] = set()
    for line in lines:
        if _re.match(r"\s*probe\s+-create\b", line):
            if "-depth" in line:
                continue
            sig_match = _re.search(r"probe\s+-create\s+(\S+)", line)
            if sig_match:
                existing_signals.add(sig_match.group(1))
            filtered.append(line)
        else:
            filtered.append(line)

    db_path = ""
    for line in filtered:
        m = _re.match(r"\s*database\s+-open\s+(\S+)", line)
        if m:
            db_path = m.group(1)
            break
    db_opt = f" -database {db_path}" if db_path else " -shm"

    if probe_type == "depth_all":
        new_probes = [f"probe -create top -depth all{db_opt}"]
    elif probe_type == "hierarchical":
        new_probes = []
        for sig in probe_info["signals"]:  # type: ignore[index]
            if sig not in existing_signals:
                new_probes.append(f"probe -create {sig}{db_opt}")
        for sp in probe_info["scope_probes"]:  # type: ignore[index]
            new_probes.append(f"probe -create {sp['scope']} -depth {sp['depth']}{db_opt}")
    else:
        # "signals" type (v5.1 compat) — probe_info is a list
        new_probes = [
            f"probe -create {sig}{db_opt}"
            for sig in (probe_info or [])
            if sig not in existing_signals
        ]

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
    """Inject probe on/off + run sequence for dump_window (Batch mode only)."""
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms

    lines = content.splitlines()
    filtered = [line for line in lines if not _re.match(r"\s*run(\s|$)", line)]

    window_tcl = ["probe -disable"]
    if start_ms > 0:
        window_tcl.append(f"run {start_ms}ms")
    window_tcl.append("probe -enable")
    window_tcl.append(f"run {duration_ms}ms")
    window_tcl.append("probe -disable")
    window_tcl.append("run")

    return "\n".join(filtered + window_tcl) + "\n"


# ---------------------------------------------------------------------------
# v4.3: SDF override
# ---------------------------------------------------------------------------

async def _handle_sdf_override(
    sim_dir: str, runner: dict, sdf_file: str, sdf_corner: str,
) -> str:
    """Handle SDF override: disable TB $sdf_annotate + generate tfile."""
    err = validate_path(sdf_file, "sdf_file")
    if err:
        raise ValueError(err)
    if not _re.fullmatch(r"[\w./\-]+", sdf_file):
        raise ValueError(f"Invalid sdf_file path: {sdf_file!r}")

    from xcelium_mcp.registry import load_sim_config
    from xcelium_mcp.shell_utils import get_user_tmp_dir

    config = await load_sim_config(sim_dir)
    sdf_info = (config or {}).get("sdf_info", {})
    extra_defines: list[str] = []

    if sdf_info.get("has_sdf_annotate"):
        guard = sdf_info.get("sdf_guard_define")
        if guard:
            extra_defines.append(f"-define {guard}")
        else:
            await _patch_tb_sdf_guard(sim_dir, sdf_info)
            extra_defines.append("-define MCP_SDF_OVERRIDE")

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

    b64 = _b64.b64encode(tfile_content.encode()).decode()
    await shell_run(
        f"echo {shell_quote(b64)} | base64 -d > {shell_quote(tfile_path)}",
        timeout=10,
    )

    elab_extra = f"-delay_mode path -sdf_verbose -timescale 1ns/1fs -tfile {tfile_path}"
    return " ".join(extra_defines + [elab_extra])


async def _patch_tb_sdf_guard(sim_dir: str, sdf_info: dict) -> None:
    """Patch TB RTL: add `ifndef MCP_SDF_OVERRIDE guard around $sdf_annotate."""
    from xcelium_mcp.shell_utils import get_user_tmp_dir

    top_v = sdf_info.get("sdf_source_file", "")
    if not top_v:
        return

    user_tmp = await get_user_tmp_dir()
    filename = top_v.split("/")[-1]
    await shell_run(f"cp {shell_quote(top_v)} {user_tmp}/{filename}.bak.mcp_sdf", timeout=5)

    content = await shell_run(f"cat {shell_quote(top_v)}", timeout=10)

    patched = _re.sub(
        r"(\s*initial\s+begin\s*\n)(.*?\$sdf_annotate.*?\n)(.*?\s*end)",
        r"\1`ifndef MCP_SDF_OVERRIDE\n\2`endif\n\3",
        content,
        flags=_re.DOTALL,
    )

    if patched != content:
        b64 = _b64.b64encode(patched.encode()).decode()
        await shell_run(
            f"echo {shell_quote(b64)} | base64 -d > {shell_quote(top_v)}",
            timeout=10,
        )


async def _preprocess_setup_tcl(
    sim_dir: str, runner: dict, test_name: str, sim_mode: str = "",
    dump_depth: str = "all",
    dump_signals: list[str] | None = None,
    dump_window: dict | None = None,
    dump_scopes: dict[str, str] | None = None,
    dump_strategy: dict | None = None,
) -> tuple[str, dict | None]:
    """Preprocess setup_tcl: SHM naming + probe scope + dump window.

    v5.2: Added dump_scopes and dump_strategy for hierarchical block-boundary mode.

    Returns:
        (out_path, dump_summary) — out_path is "" if no changes made.
        dump_summary is non-None only when probe_type == "hierarchical".
    """
    if not _re.fullmatch(r"[A-Za-z0-9_\-]+", test_name):
        return "", None

    content = await read_setup_tcl_async(runner, sim_dir)
    if not content:
        return "", None

    changed = False

    if "$env(TEST_NAME)" not in content:
        new_content = _replace_shm_stems(content, test_name)
        if new_content != content:
            content = new_content
            changed = True

    probe_type, probe_info, dump_summary = _resolve_probe_signals(
        dump_signals, dump_depth,
        dump_scopes=dump_scopes,
        dump_strategy=dump_strategy,
        sim_mode=sim_mode,
    )
    new_content = _replace_probe_lines(content, probe_type, probe_info)
    if new_content != content:
        content = new_content
        changed = True

    if dump_window:
        new_content = _inject_dump_window(content, dump_window)
        if new_content != content:
            content = new_content
            changed = True

    if not changed:
        return "", None

    from xcelium_mcp.shell_utils import get_user_tmp_dir
    user_tmp = await get_user_tmp_dir()
    out_path = f"{user_tmp}/setup_batch_{test_name}.tcl"
    await asyncio.to_thread(Path(out_path).write_text, content)

    return out_path, dump_summary


def _build_checkpoint_tcl(
    test_name: str, chk_dir: str, l1_time: str,
    setup_lines: str,
) -> str:
    """Generate a Tcl script with probe setup + L1 checkpoint save."""
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
