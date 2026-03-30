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

    # --- Build simvisdbutil command ---
    parts = ["simvisdbutil", shm_path, "-csv", "-output", output_path, "-overwrite"]

    if start_ns or end_ns:
        parts += ["-range", f"{start_ns}:{end_ns}ns"]

    if missing_ok:
        parts.append("-missing")

    for sig in signals:
        parts += ["-sig", sig]

    cmd = " ".join(parts)
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
