"""csv_cache.py — simvisdbutil CSV extraction wrapper for xcelium-mcp v3.

Wraps the simvisdbutil CLI to extract signal waveform data from SHM dump files.
In-memory cache: (shm_path, frozenset(signals), start_ns, end_ns) → CSV file path.

Architecture note:
  xcelium-mcp runs ON cloud0 — all Path operations target cloud0 local filesystem.
  ssh_run() is a local asyncio subprocess, not a remote SSH hop.
"""

from __future__ import annotations

import hashlib
import shlex
from collections import OrderedDict, deque
from pathlib import Path

from xcelium_mcp.shell_utils import ssh_run

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
    from xcelium_mcp.sim_runner import load_sim_config, resolve_sim_dir
    try:
        sim_dir = await resolve_sim_dir()
    except ValueError as e:
        raise RuntimeError(str(e))
    cfg = await load_sim_config(sim_dir)
    if cfg and "eda_tools" in cfg:
        path = cfg["eda_tools"].get("simvisdbutil", "")
        if path:
            _simvisdbutil_path = path
            return path

    raise RuntimeError("simvisdbutil not found. Check eda_tools in sim config.")


# ---------------------------------------------------------------------------
# In-memory cache: (shm_path, frozenset(signals), start_ns, end_ns) → CSV path
# LRU-bounded to prevent unbounded memory growth in long sessions.
# ---------------------------------------------------------------------------

_MAX_CACHE_SIZE = 32
_cache: OrderedDict[tuple, str] = OrderedDict()


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
    # Extract .shm directory stem (not inner .trn filename)
    shm_p = Path(shm_path)
    stem = shm_p.parent.stem if shm_p.parent.suffix == ".shm" else shm_p.stem
    suffix = f"_{start_ns}_{end_ns}" if (start_ns or end_ns) else ""
    return str(shm_p.parent / f"mcp_csv_{stem}_{sig_hash}{suffix}.csv")


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
        _cache.move_to_end(key)  # LRU: mark as recently used
        return _cache[key]

    if not output_path:
        output_path = _default_output_path(shm_path, signals, start_ns, end_ns)

    # --- Build simvisdbutil command with EDA env ---
    svdb = await _resolve_simvisdbutil()
    # -timeunits ns: force nanosecond output regardless of SHM time resolution
    parts = [svdb, shm_path, "-csv", "-output", output_path, "-overwrite", "-timeunits", "ns"]

    if start_ns or end_ns:
        parts += ["-range", f"{start_ns}:{end_ns}ns"]

    if missing_ok:
        parts.append("-missing")

    for sig in signals:
        parts += ["-sig", sig]

    svdb_cmd = " ".join(parts)

    # simvisdbutil is a wrapper script that needs EDA env (cds_root in PATH).
    # Source env from registry before execution.
    from xcelium_mcp.sim_runner import load_sim_config, login_shell_cmd, resolve_sim_dir
    try:
        sim_dir = await resolve_sim_dir()
    except ValueError as e:
        raise RuntimeError(str(e))
    cfg = await load_sim_config(sim_dir)
    if cfg:
        runner = cfg.get("runner", {})
        env_files = runner.get("env_files", [])
        if runner.get("source_separately") and env_files:
            env_shell = runner.get("env_shell", "/bin/csh")
            source_cmd = "; ".join(f"source {shlex.quote(f)}" for f in env_files)
            cmd = f"{env_shell} -c '{source_cmd}; {svdb_cmd}'"
        else:
            login_shell = runner.get("login_shell", "/bin/sh")
            cmd = login_shell_cmd(login_shell, svdb_cmd)
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

    # LRU eviction: remove least-recently-used entry when cache is full
    if len(_cache) >= _MAX_CACHE_SIZE:
        _cache.popitem(last=False)  # remove oldest (least recently used)
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

    # Use a sliding window deque(maxlen=context_rows) for prefix rows to avoid
    # loading the entire CSV into memory. Post-match suffix is read-ahead inline.
    prefix: deque[dict] = deque(maxlen=context_rows)
    first_row: dict | None = None
    prev_value: str | None = None
    abs_index = 0  # row index within filtered range

    with open(csv_path, newline="") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            # simvisdbutil uses "SimTime" column. With -timeunits ns, values are in ns.
            raw_time = row.get("SimTime") or row.get("time") or "0"
            ns = int(raw_time)
            row["_ns"] = ns

            if start_ns and ns < start_ns:
                continue
            if end_ns and ns > end_ns:
                break

            if first_row is None:
                first_row = row

            cur_val = row.get(signal, "")
            if _eval_condition(cur_val, op, value, prev_value):
                # Build context: prefix rows + match row + read-ahead suffix
                ctx: list[dict] = list(prefix) + [row]
                match_row_idx = len(ctx) - 1  # index of match row in ctx
                # Read-ahead up to context_rows more rows for suffix
                suffix_needed = context_rows
                for suffix_row in reader:
                    raw_t = suffix_row.get("SimTime") or suffix_row.get("time") or "0"
                    s_ns = int(raw_t)
                    suffix_row["_ns"] = s_ns
                    if end_ns and s_ns > end_ns:
                        break
                    ctx.append(suffix_row)
                    suffix_needed -= 1
                    if suffix_needed <= 0:
                        break
                return {
                    "found": True,
                    "match_time_ns": row["_ns"],
                    "match_value": cur_val,
                    "match_index": abs_index,
                    "context": ctx,
                    "match_row": match_row_idx,
                }
            prev_value = cur_val
            prefix.append(row)
            abs_index += 1

    if first_row is None:
        return {"found": False, "match_time_ns": 0, "match_value": "", "context": []}

    if signal not in first_row:
        available = [k for k in first_row.keys() if k not in ("time", "SimTime", "_ns")]
        return {
            "found": False,
            "match_time_ns": 0,
            "match_value": "",
            "context": [],
            "error": f"Signal '{signal}' not in CSV. Available: {available[:10]}",
        }

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

    Call this after sim_batch_run completes so that the next analysis
    always re-extracts from the freshly written SHM file.

    Args:
        shm_path: If given, clear only entries for this SHM path.
                  If None, clear entire cache.

    Usage:
        # After sim_batch_run produces a new SHM:
        import xcelium_mcp.csv_cache as csv_cache
        csv_cache.clear_cache(shm_path)   # invalidate stale entries for this SHM
        # or
        csv_cache.clear_cache()            # clear all
    """
    global _cache
    if shm_path is None:
        _cache.clear()
    else:
        _cache = {k: v for k, v in _cache.items() if k[0] != shm_path}
