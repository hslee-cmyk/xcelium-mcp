"""test_discovery scanning — parsing + the shared discovery/migration flow.

F-178: parse_test_discovery_output() used to live in test_resolution.py (a
high-level orchestration module), which forced schema_migration.py (which
needs the parser) into a circular-import workaround when test_resolution.py
in turn needed schema_migration.ensure_test_discovery_current(). This module
is the dependency-free home for that leaf logic — it only depends on
shell_utils and tb_provenance.scan_test_dependencies, never on discovery.py,
schema_migration.py, or test_resolution.py.

build_test_discovery_dict() also replaces what used to be two near-identical
copies of the same command-build + run + parse + dependency-scan flow: one in
discovery.py::run_full_discovery's Phase A, one in
schema_migration.py::_migrate_v1_add_tb_type_and_file_map. Both now call this
one function.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from xcelium_mcp.shell_utils import shell_quote, shell_run
from xcelium_mcp.tb_provenance import scan_test_dependencies

logger = logging.getLogger(__name__)

# F-175: test_discovery's grep command (built below) emits `-n`
# (filename:lineno:content) instead of `-h` (content only), so the file that
# defines each test can be captured instead of discarded — needed to locate a
# test's TB source file for provenance (build_tb_provenance).
_UVM_TEST_LINE_RE = re.compile(r"^(?P<file>[^:]+):\d+:.*\bclass\s+(?P<name>\w+)\b")
_SV_PROGRAM_LINE_RE = re.compile(r"^(?P<file>[^:]+):\d+:\s*program\s+(?P<name>\w+)\b")


def parse_test_discovery_output(raw: str, tb_type: str) -> dict[str, str]:
    """Parse test_discovery.command output into {test_name: defining_file_path}.

    tb_type selects the line format the command (built by
    build_test_discovery_cmd) emits:
      "uvm"         — "{file}:{lineno}:...class {Name} extends uvm_test..."
      "sv_directed" — "{file}:{lineno}:program {Name}..."
      other         — bare file paths, one per line (tb_tests/*.v listing;
                       name = path stem)
    Unmatched/malformed lines are silently skipped — test discovery (and the
    TB-source lookup built on it) is best-effort, not a hard requirement.
    """
    if tb_type == "uvm":
        pattern = _UVM_TEST_LINE_RE
    elif tb_type == "sv_directed":
        pattern = _SV_PROGRAM_LINE_RE
    else:
        pattern = None

    names_to_files: dict[str, str] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if pattern is None:
            names_to_files.setdefault(Path(line).stem, line)
            continue
        m = pattern.match(line)
        if m:
            names_to_files.setdefault(m.group("name"), m.group("file"))
    return names_to_files


def build_test_discovery_cmd(sim_dir: str, tb_type: str) -> str:
    """Test-discovery shell command for tb_type, in the -n (filename:lineno)
    format parse_test_discovery_output expects.
    """
    quoted_dir = shell_quote(sim_dir)
    if tb_type == "uvm":
        return f"grep -rn 'extends uvm_test' {quoted_dir} --include='*.sv' --include='*.svh' || true"
    if tb_type == "sv_directed":
        return f"grep -rn '^\\s*program ' {quoted_dir} --include='*.sv' || true"
    return f"ls {quoted_dir}/tb_tests/*.v || true"


async def build_test_discovery_dict(sim_dir: str, tb_type: str) -> dict:
    """Run test discovery for tb_type and return a fresh test_discovery dict
    (command, cached_tests, cached_test_files, cached_dependency_files,
    cached_at — schema_version is NOT set here, callers own schema
    versioning: discovery.py stamps CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
    directly, schema_migration.py stamps it via ensure_test_discovery_current).

    The primary scan's failure (shell_run raising RuntimeError/OSError/
    asyncio.TimeoutError) propagates to the caller — discovery.py's
    fresh-discovery path treats that as non-fatal (empty result),
    schema_migration.py's migration path treats it as retry-later
    (_MigrationIncomplete). Only the dependency scan sub-step stays
    best-effort/non-fatal regardless of caller, since cached_test_files is
    already populated by that point.
    """
    test_cmd = build_test_discovery_cmd(sim_dir, tb_type)
    output = await shell_run(f"cd {shell_quote(sim_dir)} && {test_cmd}", timeout=30)
    cached_test_files = parse_test_discovery_output(output, tb_type)
    cached_tests = sorted(cached_test_files.keys())

    # F-175: resolve each test's `include`/import dependency FILE LOCATIONS
    # (+ the primary file's sha256 at this scan, for later staleness checks)
    # here, once, at discovery time — never unconditionally on the per-run
    # hot path (sim_batch_run/sim_regression/sim_bridge_run only re-scan if
    # the primary file's content changed since — see tb_provenance.py).
    cached_dependency_files: dict[str, dict] = {}
    if cached_test_files:
        names = list(cached_test_files.keys())
        try:
            scan_results = await asyncio.gather(
                *(scan_test_dependencies(cached_test_files[n], sim_dir) for n in names)
            )
            for name, entry in zip(names, scan_results):
                cached_dependency_files[name] = entry
        except (RuntimeError, OSError, asyncio.TimeoutError) as e:
            logger.debug("dependency file discovery failed (non-fatal): %s", e)

    return {
        "command": test_cmd,
        "tb_type": tb_type,
        "cached_tests": cached_tests,
        "cached_test_files": cached_test_files,
        "cached_dependency_files": cached_dependency_files,
        "cached_at": datetime.now().isoformat(),
    }
