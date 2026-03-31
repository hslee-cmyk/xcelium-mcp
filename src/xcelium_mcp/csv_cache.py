"""csv_cache.py — simvisdbutil CSV extraction wrapper for xcelium-mcp v3.

Wraps the simvisdbutil CLI to extract signal waveform data from SHM dump files.
In-memory cache: (shm_path, frozenset(signals), start_ns, end_ns) → CSV file path.

Architecture note:
  xcelium-mcp runs ON cloud0 — all Path operations target cloud0 local filesystem.
  ssh_run() is a local asyncio subprocess, not a remote SSH hop.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from xcelium_mcp.sim_runner import ssh_run


# ---------------------------------------------------------------------------
# simvisdbutil path resolution — MCP server runs in bash without EDA PATH
# ---------------------------------------------------------------------------

_simvisdbutil_path: str | None = None


async def _resolve_simvisdbutil() -> str:
    """Get simvisdbutil path from registry. Falls back to sim_discover.

    v4: Registry eda_tools is the single source. No direct which/glob detection.
    """
    global _simvisdbutil_path
    if _simvisdbutil_path:
        return _simvisdbutil_path

    # Try registry first
    from xcelium_mcp.sim_runner import _get_default_sim_dir, load_sim_config
    sim_dir = await _get_default_sim_dir()
    if sim_dir:
        cfg = await load_sim_config(sim_dir)
        if cfg and "eda_tools" in cfg:
            path = cfg["eda_tools"].get("simvisdbutil", "")
            if path:
                _simvisdbutil_path = path
                return path

    # Fallback: trigger sim_discover
    from xcelium_mcp.sim_runner import run_full_discovery
    await run_full_discovery(sim_dir or "")

    # Retry after discover
    sim_dir = await _get_default_sim_dir()
    if sim_dir:
        cfg = await load_sim_config(sim_dir)
        if cfg and "eda_tools" in cfg:
            path = cfg["eda_tools"].get("simvisdbutil", "")
            if path:
                _simvisdbutil_path = path
                return path

    raise RuntimeError("simvisdbutil not found even after sim_discover.")


# ---------------------------------------------------------------------------
# In-memory cache: (shm_path, frozenset(signals), start_ns, end_ns) → CSV path
# ---------------------------------------------------------------------------

_cache: dict[tuple, str] = {}


def _cache_key(
    shm_path: str, signals: list[str], start_ns: int, end_ns: int
) -> tuple:
    return (shm_path, frozenset(signals), start_ns, end_ns)


def _default_output_path(
    shm_path: str, signals: list[str], start_ns: int, end_ns: int
) -> str:
    """Generate a deterministic CSV output path based on inputs.

    Output is placed next to the SHM file:
      <shm_dir>/mcp_csv_<stem>_<sig_hash>[_<start>_<end>].csv
    """
    sig_hash = hashlib.md5(",".join(sorted(signals)).encode()).hexdigest()[:8]
    stem = Path(shm_path).stem  # e.g. "ci_top_TOP015"
    suffix = f"_{start_ns}_{end_ns}" if (start_ns or end_ns) else ""
    return str(Path(shm_path).parent / f"mcp_csv_{stem}_{sig_hash}{suffix}.csv")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract(
    shm_path: str,
    signals: list[str],
    start_ns: int = 0,
    end_ns: int = 0,
    output_path: str = "",
    missing_ok: bool = True,
) -> str:
    """Extract signal waveform data from SHM dump to CSV via simvisdbutil.

    Internally runs:
      simvisdbutil {shm_path} -csv -output {output_path} -overwrite
          [-range {start_ns}:{end_ns}ns]   # only when start_ns or end_ns != 0
          [-missing]                        # when missing_ok=True, ignore absent signals
          -sig {signal_1} -sig {signal_2} ...

    Returns: absolute path to generated CSV file.
    Raises RuntimeError if simvisdbutil fails (output file not created).
    Result is cached; subsequent calls with same args return cached path directly.
    """
    key = _cache_key(shm_path, signals, start_ns, end_ns)
    if key in _cache and Path(_cache[key]).exists():
        return _cache[key]

    if not output_path:
        output_path = _default_output_path(shm_path, signals, start_ns, end_ns)

    # --- Build simvisdbutil command with EDA env ---
    svdb = await _resolve_simvisdbutil()
    parts = [svdb, shm_path, "-csv", "-output", output_path, "-overwrite"]

    if start_ns or end_ns:
        parts += ["-range", f"{start_ns}:{end_ns}ns"]

    if missing_ok:
        parts.append("-missing")

    for sig in signals:
        parts += ["-sig", sig]

    svdb_cmd = " ".join(parts)

    # simvisdbutil is a wrapper script that needs EDA env (cds_root in PATH).
    # Source env from registry before execution.
    from xcelium_mcp.sim_runner import _get_default_sim_dir, load_sim_config, _login_shell_cmd
    sim_dir = await _get_default_sim_dir()
    cfg = await load_sim_config(sim_dir) if sim_dir else None
    if cfg:
        runner = cfg.get("runner", {})
        env_files = runner.get("env_files", [])
        if runner.get("source_separately") and env_files:
            env_shell = runner.get("env_shell", "/bin/csh")
            source_cmd = "; ".join(f"source {f}" for f in env_files)
            cmd = f"{env_shell} -c '{source_cmd}; {svdb_cmd}'"
        else:
            login_shell = runner.get("login_shell", "/bin/sh")
            cmd = _login_shell_cmd(login_shell, svdb_cmd)
    else:
        cmd = svdb_cmd  # fallback: try direct

    out = await ssh_run(cmd, timeout=120.0)

    # Validate output
    if not Path(output_path).exists():
        raise RuntimeError(
            f"simvisdbutil did not produce output file.\n"
            f"Command: {cmd}\n"
            f"Output: {out}"
        )

    _cache[key] = output_path
    return output_path


def bisect_csv(
    csv_path: str,
    signal: str,
    op: str,
    value: str,
    start_ns: int = 0,
    end_ns: int = 0,
    context_rows: int = 2,
) -> dict:
    """In-memory search of a CSV file for the first row matching a condition.

    Op values:
      "eq"     — signal == value
      "ne"     — signal != value
      "gt"     — signal > value  (numeric)
      "lt"     — signal < value  (numeric)
      "change" — any value change from the previous row

    Args:
        csv_path: Path to CSV file extracted by extract().
        signal:   Column name (full signal path as written in CSV header).
        op:       Comparison operator (see above).
        value:    Target value string (ignored for "change").
        start_ns: Start of search range (0 = from beginning).
        end_ns:   End of search range (0 = to end).
        context_rows: Number of rows before/after the match to include.

    Returns:
        dict with keys:
          found (bool), match_time_ns (int), match_value (str),
          context (list[dict]), and optionally error (str).
    """
    import csv as _csv

    rows: list[dict] = []
    with open(csv_path, newline="") as fh:
        reader = _csv.DictReader(fh)
        # simvisdbutil uses "SimTime" (in femtoseconds), not "time" (ns)
        for row in reader:
            raw_time = row.get("time") or row.get("SimTime") or "0"
            time_val = int(raw_time)
            # Detect femtosecond scale (SimTime > 1e9 for typical ns-range sims)
            ns = time_val // 1000 if time_val > 1_000_000_000 else time_val
            row["_ns"] = ns  # normalized time in ns
            if start_ns and ns < start_ns:
                continue
            if end_ns and ns > end_ns:
                break
            rows.append(row)

    if not rows:
        return {"found": False, "match_time_ns": 0, "match_value": "", "context": []}

    if signal not in rows[0]:
        available = [k for k in rows[0].keys() if k not in ("time", "SimTime", "_ns")]
        return {
            "found": False,
            "match_time_ns": 0,
            "match_value": "",
            "context": [],
            "error": f"Signal '{signal}' not in CSV. Available: {available[:10]}",
        }

    prev_value: str | None = None
    for i, row in enumerate(rows):
        cur_val = row[signal]
        if _eval_condition(cur_val, op, value, prev_value):
            ctx_start = max(0, i - context_rows)
            ctx_end = min(len(rows), i + context_rows + 1)
            return {
                "found": True,
                "match_time_ns": row["_ns"],
                "match_value": cur_val,
                "match_index": i,
                "context": rows[ctx_start:ctx_end],
                "match_row": i - ctx_start,  # index of match within context slice
            }
        prev_value = cur_val

    return {"found": False, "match_time_ns": 0, "match_value": "", "context": []}


def _eval_condition(
    cur_val: str, op: str, target: str, prev_val: str | None
) -> bool:
    """Evaluate a comparison condition between cur_val and target."""
    if op == "change":
        return prev_val is not None and cur_val != prev_val

    # Numeric comparison (hex/dec/oct literals supported)
    try:
        cur_n = int(cur_val, 0)
        tgt_n = int(target, 0)
        if op == "eq":
            return cur_n == tgt_n
        if op == "ne":
            return cur_n != tgt_n
        if op == "gt":
            return cur_n > tgt_n
        if op == "lt":
            return cur_n < tgt_n
    except (ValueError, TypeError):
        pass

    # String fallback
    if op == "eq":
        return cur_val == target
    if op == "ne":
        return cur_val != target
    return False


def clear_cache(shm_path: str | None = None) -> None:
    """Clear CSV cache entries.

    Args:
        shm_path: If given, clear only entries for this SHM path.
                  If None, clear entire cache.
    """
    global _cache
    if shm_path is None:
        _cache.clear()
    else:
        _cache = {k: v for k, v in _cache.items() if k[0] != shm_path}
