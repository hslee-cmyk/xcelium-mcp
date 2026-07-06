"""TB source provenance — F-175.

Identifies which TB source file(s) a given test actually depends on (via
test_discovery.cached_test_files, populated by discovery.py /
test_resolution.py's parse_test_discovery_output, plus a one-level scan of
the test file's own `include`/import references) and computes their sha256,
so a result or checkpoint can later be verified against the TB source that
actually produced it instead of trusting whatever local copy happens to be
lying around (the motivating incident: a stale local TB copy had different
test content than the one actually simulated on cloud0).

Scope note: dependency resolution covers the test's own file plus its
*direct* `include`/import references only (one level deep) — it does not
recursively expand what those dependencies themselves include. A test whose
behavior depends on a file two hops away (a shared component's own include)
will not be covered. This mirrors the same "best-effort, not exhaustive"
posture as the rest of test_discovery.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from xcelium_mcp.registry import load_sim_config, resolve_sim_dir
from xcelium_mcp.shell_utils import shell_quote, shell_run

_INCLUDE_RE = re.compile(r'`include\s+"([^"]+)"')
_IMPORT_RE = re.compile(r'\bimport\s+(\w+)\s*::')


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


async def build_tb_provenance(full_test_name: str, sim_dir: str = "") -> dict | None:
    """Resolve + hash the TB source file(s) for full_test_name.

    Returns:
        {
            "files": [{"path": str, "sha256": str}, ...],  # primary file first
            "combined_sha256": str,  # sha256 over all "path:sha256" pairs, sorted by path
        }
    or None if the primary test file can't be located or read. Dependency
    files that can't be resolved/read are silently omitted from "files" —
    best-effort, never surfaced as an error. Callers must treat None as "no
    provenance available" and skip it silently.
    """
    primary_path = await resolve_tb_source_file(full_test_name, sim_dir)
    if not primary_path:
        return None
    primary_checksum = await compute_file_sha256(primary_path)
    if primary_checksum is None:
        return None

    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = sim_dir or ""

    files = [{"path": primary_path, "sha256": primary_checksum}]
    seen_paths = {primary_path}
    if resolved_dir:
        for dep_path in await find_dependency_files(primary_path, resolved_dir):
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
