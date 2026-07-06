"""TB source provenance — F-175.

Identifies which TB source file(s) a given test actually depends on (its own
file via test_discovery.cached_test_files, plus its direct `include`/import
dependencies via test_discovery.cached_dependency_files — both populated
once at discovery/cache-miss time, see discovery.py / test_resolution.py /
tools/sim_lifecycle.py::list_tests) and computes their sha256 on every call,
so a result or checkpoint can later be verified against the TB source that
actually produced it instead of trusting whatever local copy happens to be
lying around (the motivating incident: a stale local TB copy had different
test content than the one actually simulated on cloud0).

Design split — cheap on the hot path, not just at discovery:
  - WHERE a test's files live (cached_test_files/cached_dependency_files) is
    resolved via find/grep at discovery/cache-miss time, or — self-healing —
    lazily re-scanned the moment the test's own file content changes (see
    resolve_cached_dependency_files). It is never scanned unconditionally on
    the per-run hot path (sim_batch_run/sim_regression/sim_bridge_run).
  - WHAT those files currently contain (the sha256 hashes) is always read
    fresh, on every single call — content can change between runs and that
    must be caught every time.

Self-healing dependency cache: a test's own file changing is exactly when its
`include`/import lines might have changed too (added/removed a dependency).
cached_dependency_files therefore also records the primary file's sha256 at
scan time (scanned_primary_sha256). Every build_tb_provenance() call already
computes the CURRENT primary sha256 for its own purposes — comparing it
against scanned_primary_sha256 is then free. On a mismatch (or no cache entry
at all), the dependency scan re-runs once, right now, and the refreshed
result is written back — so the cost is paid only when the test file actually
changed, not on every run.

Scope note: dependency resolution covers the test's own file plus its
*direct* `include`/import references only (one level deep) — it does not
recursively expand what those dependencies themselves include. A test whose
behavior depends on a file two hops away (a shared component's own include)
will not be covered. This mirrors the same "best-effort, not exhaustive"
posture as the rest of test_discovery. See TODO.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from xcelium_mcp.registry import load_sim_config, resolve_sim_dir, save_sim_config
from xcelium_mcp.shell_utils import shell_quote, shell_run

_INCLUDE_RE = re.compile(r'`include\s+"([^"]+)"')
_IMPORT_RE = re.compile(r'\bimport\s+(\w+)\s*::')


# ---------------------------------------------------------------------------
# Content hashing — always fresh, never cached
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Dependency scan (find/grep) — only ever called at discovery/cache-miss
# time or by resolve_cached_dependency_files' self-healing refresh
# ---------------------------------------------------------------------------


async def _find_file_by_basename(sim_dir: str, name: str) -> str | None:
    """Best-effort: locate a file anywhere under sim_dir by its basename.

    Picks the first match if duplicates exist under different directories —
    same class of ambiguity as duplicate test-class names (see TODO.md).
    """
    r = await shell_run(
        f"find {shell_quote(sim_dir)} -name {shell_quote(name)} 2>/dev/null | head -1",
        timeout=15,
    )
    return r.strip() or None


async def _find_package_file(sim_dir: str, pkg_name: str) -> str | None:
    """Best-effort: locate the file that declares `package {pkg_name};`."""
    r = await shell_run(
        f"grep -rl {shell_quote('package ' + pkg_name)} {shell_quote(sim_dir)} "
        f"--include='*.sv' --include='*.svh' 2>/dev/null | head -1",
        timeout=15,
    )
    return r.strip() or None


async def find_dependency_files(test_file: str, sim_dir: str) -> list[str]:
    """Scan test_file for `include`/import references and resolve each to an
    actual path under sim_dir — one level deep (see module docstring).

    Best-effort: an unresolvable reference is silently skipped, never an
    error. Returns a de-duplicated list of resolved paths (test_file itself
    is not included in the result).
    """
    try:
        content = await asyncio.to_thread(
            Path(test_file).read_text, encoding="utf-8", errors="ignore"
        )
    except OSError:
        return []

    basenames = sorted({Path(m).name for m in _INCLUDE_RE.findall(content)})
    packages = sorted(set(_IMPORT_RE.findall(content)))

    resolved: list[str] = []
    seen: set[str] = set()
    for name in basenames:
        path = await _find_file_by_basename(sim_dir, name)
        if path and path not in seen:
            resolved.append(path)
            seen.add(path)
    for pkg in packages:
        path = await _find_package_file(sim_dir, pkg)
        if path and path not in seen:
            resolved.append(path)
            seen.add(path)
    return resolved


async def scan_test_dependencies(primary_path: str, sim_dir: str) -> dict:
    """Scan primary_path's dependencies and hash it, as one cache entry.

    Returns {"scanned_primary_sha256": str | None, "deps": list[str]} — the
    shape stored in test_discovery.cached_dependency_files[test_name].
    scanned_primary_sha256 is what resolve_cached_dependency_files compares
    against on later calls to decide whether this entry is still valid.
    """
    deps, checksum = await asyncio.gather(
        find_dependency_files(primary_path, sim_dir),
        compute_file_sha256(primary_path),
    )
    return {"scanned_primary_sha256": checksum, "deps": deps}


# ---------------------------------------------------------------------------
# Cache-backed resolvers
# ---------------------------------------------------------------------------


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


async def resolve_cached_dependency_files(
    full_test_name: str, primary_path: str, primary_sha256: str, sim_dir: str = "",
) -> list[str]:
    """Look up (or self-heal) the dependency file paths for full_test_name.

    Cache hit (scanned_primary_sha256 matches the CURRENT primary_sha256,
    which the caller already computed for its own hashing purposes): pure
    read, no find/grep.

    Cache miss/stale (no entry at all, or the test's own file has changed
    since the dependency list was last scanned — exactly when its
    `include`/import lines might have changed too): re-scans right now via
    find_dependency_files() (reusing the already-computed primary_sha256
    rather than hashing the file a second time), persists the refreshed
    entry back to config, and returns the fresh list. So the find/grep cost
    is paid only when the test file actually changed, not on every run —
    never on an unconditional per-run schedule.
    """
    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = sim_dir
    if not resolved_dir:
        return []
    cfg = await load_sim_config(resolved_dir)
    if not cfg:
        return []

    entry = cfg.get("test_discovery", {}).get("cached_dependency_files", {}).get(full_test_name)
    if entry is not None and entry.get("scanned_primary_sha256") == primary_sha256:
        return entry.get("deps", [])

    # Stale/missing — rescan deps only (the caller already computed
    # primary_sha256, so reuse it rather than hashing the file a second time).
    fresh_deps = await find_dependency_files(primary_path, resolved_dir)
    fresh_entry = {"scanned_primary_sha256": primary_sha256, "deps": fresh_deps}
    cfg.setdefault("test_discovery", {}).setdefault("cached_dependency_files", {})[full_test_name] = fresh_entry
    await save_sim_config(resolved_dir, cfg)
    return fresh_deps


async def build_tb_provenance(full_test_name: str, sim_dir: str = "") -> dict | None:
    """Resolve + hash the TB source file(s) for full_test_name.

    File *locations* come from cache, refreshed automatically when the
    primary file's content changes (see resolve_cached_dependency_files).
    File *contents* are always read fresh via compute_file_sha256.

    Returns:
        {
            "files": [{"path": str, "sha256": str}, ...],  # primary file first
            "combined_sha256": str,  # sha256 over all "path:sha256" pairs, sorted by path
        }
    or None if the primary test file can't be located or read. Dependency
    files that can't be read are silently omitted from "files" — best-effort,
    never surfaced as an error. Callers must treat None as "no provenance
    available" and skip it silently.
    """
    primary_path = await resolve_tb_source_file(full_test_name, sim_dir)
    if not primary_path:
        return None
    primary_checksum = await compute_file_sha256(primary_path)
    if primary_checksum is None:
        return None

    files = [{"path": primary_path, "sha256": primary_checksum}]
    seen_paths = {primary_path}
    dep_paths = await resolve_cached_dependency_files(
        full_test_name, primary_path, primary_checksum, sim_dir
    )
    for dep_path in dep_paths:
        if dep_path in seen_paths:
            continue
        checksum = await compute_file_sha256(dep_path)
        if checksum is not None:
            files.append({"path": dep_path, "sha256": checksum})
            seen_paths.add(dep_path)

    combined_sha256 = hashlib.sha256(
        "\n".join(
            f"{f['path']}:{f['sha256']}" for f in sorted(files, key=lambda f: f["path"])
        ).encode()
    ).hexdigest()

    return {"files": files, "combined_sha256": combined_sha256}


def format_tb_provenance(provenance: dict) -> str:
    """Render a build_tb_provenance() result as human-readable MCP tool output text."""
    lines = [f"  {f['path']} (sha256: {f['sha256']})" for f in provenance["files"]]
    return "tb_source:\n" + "\n".join(lines) + f"\n  combined_sha256: {provenance['combined_sha256']}"
