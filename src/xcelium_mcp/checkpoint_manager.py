"""checkpoint_manager.py — Persistent checkpoint management for xcelium-mcp v3.

Architecture:
  Checkpoints are saved to {sim_dir}/checkpoints/{name}/
  Manifest file: {sim_dir}/checkpoints/manifest.json
  compile_hash: MD5 of inca/ directory object file mtimes (detects recompile)

Phase 3 unblock:
  find_nearest_checkpoint() resolves P3-3 (_find_nearest_checkpoint dependency
  in request_additional_signals).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path


_MANIFEST_FILE = "manifest.json"


# ---------------------------------------------------------------------------
# compile_hash — detects RTL recompile
# ---------------------------------------------------------------------------

def compute_compile_hash(sim_dir: str) -> str:
    """Return MD5 of inca/ object file mtimes.

    inca/ is Xcelium's compiled design database.  Any recompile changes
    mtime of at least one object file, causing the hash to change.
    Falls back to sim_dir itself when inca/ is absent.
    """
    inca_dir = os.path.join(sim_dir, "inca")
    root_dir = inca_dir if os.path.isdir(inca_dir) else sim_dir
    mtimes: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            try:
                mtimes.append(f"{fpath}:{os.path.getmtime(fpath):.3f}")
            except OSError:
                pass
    return hashlib.md5("\n".join(mtimes).encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _checkpoint_base_dir(sim_dir: str) -> str:
    return str(Path(sim_dir) / "checkpoints")


def _manifest_path(sim_dir: str) -> str:
    return str(Path(sim_dir) / "checkpoints" / _MANIFEST_FILE)


def _read_manifest(sim_dir: str) -> dict:
    path = _manifest_path(sim_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _write_manifest(sim_dir: str, data: dict) -> None:
    base = _checkpoint_base_dir(sim_dir)
    Path(base).mkdir(parents=True, exist_ok=True)
    with open(_manifest_path(sim_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_checkpoint(
    sim_dir: str,
    name: str,
    saved_time_ns: int = 0,
    origin: str = "bridge",
    test_name: str = "",
) -> dict:
    """Register a saved checkpoint in the manifest.

    Args:
        sim_dir: Simulation directory.
        name: Checkpoint name (e.g. "L1_TOP015").
        saved_time_ns: Simulation time at checkpoint in nanoseconds.
        origin: How the checkpoint was created — "regression", "bridge", or "single".
        test_name: Associated test name (for pattern-based cleanup).

    Returns the new checkpoint entry dict.
    """
    from datetime import datetime

    manifest = _read_manifest(sim_dir)
    compile_hash = compute_compile_hash(sim_dir)
    manifest["compile_hash"] = compile_hash

    entry: dict = {
        "saved_at": datetime.now().isoformat(),
        "saved_time_ns": saved_time_ns,
        "compile_hash": compile_hash,
        "origin": origin,
        "test_name": test_name,
        "path": str(Path(_checkpoint_base_dir(sim_dir)) / name),
    }
    manifest.setdefault("checkpoints", {})[name] = entry
    _write_manifest(sim_dir, manifest)
    return entry


def verify_checkpoint(sim_dir: str, name: str) -> tuple[bool, str]:
    """Check if a checkpoint is valid (compile hash matches current).

    Returns: (is_valid, reason_string)
    """
    manifest = _read_manifest(sim_dir)
    checkpoints = manifest.get("checkpoints", {})

    if name not in checkpoints:
        return False, f"Checkpoint '{name}' not found in manifest"

    current_hash = compute_compile_hash(sim_dir)
    saved_hash = checkpoints[name].get("compile_hash", "")
    if saved_hash != current_hash:
        return False, (
            f"Compile hash mismatch (saved={saved_hash}, current={current_hash}). "
            f"RTL was recompiled after this checkpoint was saved."
        )
    return True, "ok"


def invalidate_stale_checkpoints(sim_dir: str, reason: str = "") -> list[str]:
    """Delete all checkpoints whose compile_hash differs from current.

    Returns list of deleted checkpoint names.
    Called by sim_batch_run when recompile is detected.
    """
    manifest = _read_manifest(sim_dir)
    current_hash = compute_compile_hash(sim_dir)
    checkpoints = manifest.get("checkpoints", {})

    removed: list[str] = []
    for name in list(checkpoints):
        if checkpoints[name].get("compile_hash", "") != current_hash:
            chk_path = Path(_checkpoint_base_dir(sim_dir)) / name
            shutil.rmtree(chk_path, ignore_errors=True)
            del checkpoints[name]
            removed.append(name)

    if removed:
        manifest["compile_hash"] = current_hash
        manifest.setdefault("last_invalidation", {}).update(
            {"reason": reason, "removed": removed, "timestamp": time.time()}
        )
        _write_manifest(sim_dir, manifest)
    return removed


def list_checkpoints(sim_dir: str) -> list[dict]:
    """Return all checkpoint entries from the manifest."""
    manifest = _read_manifest(sim_dir)
    return list(manifest.get("checkpoints", {}).values())


def find_nearest_checkpoint(sim_dir: str, bug_time_ns: int) -> list[dict]:
    """Find checkpoints saved before bug_time_ns, sorted by proximity.

    Resolves P3-3 (_find_nearest_checkpoint dependency).
    Only returns checkpoints whose compile_hash matches current.

    Returns list sorted by (bug_time_ns - saved_time_ns) ascending,
    i.e. closest-before-bug first.
    """
    manifest = _read_manifest(sim_dir)
    current_hash = compute_compile_hash(sim_dir)
    checkpoints = manifest.get("checkpoints", {})

    candidates: list[dict] = []
    for info in checkpoints.values():
        t = info.get("saved_time_ns", 0)
        if t < bug_time_ns and info.get("compile_hash", "") == current_hash:
            candidates.append({**info, "_distance_ns": bug_time_ns - t})

    candidates.sort(key=lambda x: x["_distance_ns"])
    return candidates


# ---------------------------------------------------------------------------
# TB analysis cache — P4-8
# ---------------------------------------------------------------------------

def update_tb_analysis_cache(
    sim_dir: str,
    test_name: str,
    analysis_path: str,
    checkpoint_name: str = "",
) -> dict:
    """Record that a TB analysis was performed for test_name.

    Stores the analysis file path and the checkpoint that was active (if any)
    when the analysis was done.  This lets find_nearest_checkpoint() callers
    cross-reference which checkpoint corresponds to a cached TB analysis.

    manifest["tb_analysis_cache"][test_name] = {
        "analysis_path": <str>,
        "checkpoint":    <str>,   # "" when no checkpoint was active
        "updated_at":    <float>, # time.time()
    }

    Returns the new cache entry dict.
    """
    manifest = _read_manifest(sim_dir)
    entry: dict = {
        "analysis_path": analysis_path,
        "checkpoint": checkpoint_name,
        "updated_at": time.time(),
    }
    manifest.setdefault("tb_analysis_cache", {})[test_name] = entry
    _write_manifest(sim_dir, manifest)
    return entry


def get_tb_analysis_cache(sim_dir: str, test_name: str) -> dict:
    """Return the TB analysis cache entry for test_name.

    Returns a dict with keys:
      analysis_path — absolute path to the .analysis.md file
      checkpoint    — checkpoint name active when the analysis was cached
      updated_at    — timestamp (float) of last update

    Returns an empty dict when no cache entry exists for test_name.
    """
    manifest = _read_manifest(sim_dir)
    return manifest.get("tb_analysis_cache", {}).get(test_name, {})


def cleanup_checkpoints(
    sim_dir: str,
    mode: str = "stale",
    filter_value: str = "",
    dry_run: bool = True,
) -> dict:
    """List or remove checkpoints.

    mode:
      "list"    — list all with details (no deletion)
      "stale"   — compile_hash differs from current
      "hash"    — compile_hash == filter_value (remove a specific build's checkpoints)
      "origin"  — origin == filter_value ("regression", "bridge", "single")
      "pattern" — test_name or name contains filter_value (glob-like substring)
      "before"  — saved_at < filter_value (ISO date, e.g. "2026-04-01")
      "project" — path contains filter_value
      "all"     — every checkpoint

    dry_run=True (default): report only, no filesystem changes.

    Returns: {"removed": [...], "kept": [...], "details": [...], ...}
    """
    manifest = _read_manifest(sim_dir)
    current_hash = compute_compile_hash(sim_dir)
    checkpoints = manifest.get("checkpoints", {})

    to_remove: list[str] = []
    to_keep: list[str] = []
    details: list[dict] = []

    for name, info in checkpoints.items():
        # Build detail entry for list mode
        detail = {
            "name": name,
            "compile_hash": info.get("compile_hash", "?"),
            "origin": info.get("origin", "?"),
            "test_name": info.get("test_name", ""),
            "saved_at": info.get("saved_at", "?"),
            "saved_time_ns": info.get("saved_time_ns", 0),
        }
        details.append(detail)

        # Determine remove/keep
        remove = False
        if mode == "all":
            remove = True
        elif mode == "stale":
            remove = info.get("compile_hash", "") != current_hash
        elif mode == "hash" and filter_value:
            remove = info.get("compile_hash", "") == filter_value
        elif mode == "origin" and filter_value:
            remove = info.get("origin", "") == filter_value
        elif mode == "pattern" and filter_value:
            remove = (filter_value in name or filter_value in info.get("test_name", ""))
        elif mode == "before" and filter_value:
            saved = info.get("saved_at", "")
            remove = bool(saved and saved < filter_value)
        elif mode == "project" and filter_value:
            remove = filter_value in info.get("path", "")

        if remove:
            to_remove.append(name)
        else:
            to_keep.append(name)

    if not dry_run and mode != "list":
        for name in to_remove:
            info = checkpoints[name]
            chk_path = Path(info.get("path", str(Path(_checkpoint_base_dir(sim_dir)) / name)))
            shutil.rmtree(chk_path, ignore_errors=True)
            del checkpoints[name]
        if to_remove:
            manifest["compile_hash"] = current_hash
            _write_manifest(sim_dir, manifest)

    return {
        "removed": to_remove,
        "kept": to_keep,
        "details": details,
        "dry_run": dry_run,
        "mode": mode,
        "filter_value": filter_value,
        "sim_dir": sim_dir,
        "current_hash": current_hash,
    }
