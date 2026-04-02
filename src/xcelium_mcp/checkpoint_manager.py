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

def register_checkpoint(sim_dir: str, name: str, saved_time_ns: int = 0) -> dict:
    """Register a saved checkpoint in the manifest.

    Called by server.py after bridge confirms save success.
    Returns the new checkpoint entry dict.
    """
    manifest = _read_manifest(sim_dir)
    compile_hash = compute_compile_hash(sim_dir)
    manifest["compile_hash"] = compile_hash

    entry: dict = {
        "name": name,
        "saved_at": time.time(),
        "saved_time_ns": saved_time_ns,
        "compile_hash": compile_hash,
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
    project_filter: str = "",
    dry_run: bool = True,
) -> dict:
    """List or remove checkpoints.

    mode:
      "list"    — list only, no deletion
      "stale"   — checkpoints with mismatched compile_hash
      "project" — checkpoints whose path contains project_filter
      "all"     — every checkpoint

    dry_run=True (default): report only, no filesystem changes.

    Returns: {"removed": [...], "kept": [...], "dry_run": bool, "mode": str}
    """
    manifest = _read_manifest(sim_dir)
    current_hash = compute_compile_hash(sim_dir)
    checkpoints = manifest.get("checkpoints", {})

    to_remove: list[str] = []
    to_keep: list[str] = []

    for name, info in checkpoints.items():
        if mode == "all":
            to_remove.append(name)
        elif mode == "stale" and info.get("compile_hash", "") != current_hash:
            to_remove.append(name)
        elif mode == "project" and project_filter and project_filter in info.get("path", ""):
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
        "dry_run": dry_run,
        "mode": mode,
        "sim_dir": sim_dir,
        "current_hash": current_hash,
    }
