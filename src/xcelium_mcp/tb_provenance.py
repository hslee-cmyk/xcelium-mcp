"""TB source provenance — F-175.

Identifies which TB source file defines a given test (via
test_discovery.cached_test_files, populated by discovery.py /
test_resolution.py's parse_test_discovery_output) and computes its sha256,
so a result or checkpoint can later be verified against the TB source that
actually produced it instead of trusting whatever local copy happens to be
lying around (the motivating incident: a stale local TB copy had different
test content than the one actually simulated on cloud0).
"""
from __future__ import annotations

import asyncio
import hashlib

from xcelium_mcp.registry import load_sim_config, resolve_sim_dir


async def resolve_tb_source_file(full_test_name: str, sim_dir: str = "") -> str | None:
    """Look up the TB source file that defines full_test_name.

    Must be called with an already-resolved (full) test name — callers
    resolve short names via resolve_test_name() first. Returns None when
    unavailable (e.g. a config discovered before this feature existed, or no
    grep match) — provenance is best-effort, never a hard requirement.
    """
    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = sim_dir
    if not resolved_dir:
        return None
    cfg = await load_sim_config(resolved_dir)
    if not cfg:
        return None
    return cfg.get("test_discovery", {}).get("cached_test_files", {}).get(full_test_name)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def compute_file_sha256(path: str) -> str | None:
    """SHA256 hex digest of a local file's contents, or None if unreadable."""
    try:
        return await asyncio.to_thread(_sha256_file, path)
    except OSError:
        return None


async def build_tb_provenance(full_test_name: str, sim_dir: str = "") -> dict | None:
    """Resolve + hash the TB source file for full_test_name.

    Returns {"path": str, "sha256": str}, or None if the file can't be
    located or read. Best-effort: callers must treat None as "no provenance
    available" and skip it silently, never surface it as an error.
    """
    file_path = await resolve_tb_source_file(full_test_name, sim_dir)
    if not file_path:
        return None
    checksum = await compute_file_sha256(file_path)
    if checksum is None:
        return None
    return {"path": file_path, "sha256": checksum}
