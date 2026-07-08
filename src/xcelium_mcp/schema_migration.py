"""test_discovery config schema migration — F-175 migration gap fix.

test_discovery accumulated fields over time (cached_tests -> tb_type +
cached_test_files -> cached_dependency_files, see discovery.py's Phase A and
tb_provenance.py's module docstring) without an explicit schema version.
Every backfill check added so far only fires on its own narrow trigger —
discovery.py's Phase A re-runs only on force=True, and
tools/sim_lifecycle.py::list_tests()'s cache-miss backfill only fires when
cached_tests is empty. A project sitting between those two states
(cached_tests already populated from an old discovery, tb_type/
cached_test_files never populated because F-175 didn't exist yet) falls
through both triggers and stays on the pre-F-175 shape forever — see
docs/01-plan/features/xcelium-mcp-f175-provenance-migration-gap.plan.md for
the incident this was found from.

This module replaces "does field X exist" checks with an explicit
schema_version + a registered migration function per version step, mirroring
standard DB migration tools (Alembic/Flyway): a read site calls
ensure_test_discovery_current() once, and a future schema change only needs a
new entry in TEST_DISCOVERY_MIGRATIONS — no call-site changes required.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from xcelium_mcp.shell_utils import shell_quote, shell_run
from xcelium_mcp.sim_env_detection import analyze_tb_type
from xcelium_mcp.tb_provenance import scan_test_dependencies
from xcelium_mcp.test_resolution import parse_test_discovery_output

logger = logging.getLogger(__name__)


class _MigrationIncomplete(Exception):
    """Raised internally when a migration step's own re-scan fails.

    ensure_test_discovery_current catches this and leaves schema_version at
    its pre-attempt value instead of stamping the next version — so the next
    call retries the same migration step, rather than permanently recording
    an empty cached_test_files as if it were a legitimately-empty project.
    """


def _build_test_discovery_cmd(sim_dir: str, tb_type: str) -> str:
    """Same command shape as discovery.py::run_full_discovery's Phase A
    (the -n/filename:lineno format parse_test_discovery_output expects) —
    kept here too because migration must rebuild `command` itself rather
    than trust whatever pre-F-175 (name-only) command is already on file.
    """
    quoted_dir = shell_quote(sim_dir)
    if tb_type == "uvm":
        return f"grep -rn 'extends uvm_test' {quoted_dir} --include='*.sv' --include='*.svh' || true"
    if tb_type == "sv_directed":
        return f"grep -rn '^\\s*program ' {quoted_dir} --include='*.sv' || true"
    return f"ls {quoted_dir}/tb_tests/*.v || true"


async def _migrate_v1_add_tb_type_and_file_map(discovery: dict, sim_dir: str) -> dict:
    """v1 (cached_tests name-list only, pre-F-175) -> v2 (tb_type +
    cached_test_files + cached_dependency_files).

    Re-detects tb_type and rebuilds `command` in the new -n format rather
    than trusting the stored (pre-F-175, name-only) command — the old output
    shape can't be parsed by parse_test_discovery_output. The primary
    discovery re-scan raises _MigrationIncomplete on failure (caught by
    ensure_test_discovery_current, which then does NOT advance schema_version
    — so the next call retries instead of permanently recording an empty
    cached_test_files as if this were a legitimately test-less project).
    A failed *dependency* scan is lower-stakes (cached_test_files is already
    populated by that point) and stays best-effort: it leaves
    cached_dependency_files empty rather than raising.
    """
    tb_type = discovery.get("tb_type") or await analyze_tb_type(sim_dir)
    test_cmd = _build_test_discovery_cmd(sim_dir, tb_type)

    try:
        output = await shell_run(f"cd {shell_quote(sim_dir)} && {test_cmd}", timeout=30)
        cached_test_files = parse_test_discovery_output(output, tb_type)
    except (RuntimeError, OSError, asyncio.TimeoutError) as e:
        raise _MigrationIncomplete(f"test discovery re-scan failed: {e}") from e

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
            logger.debug("schema migration: dependency scan failed (non-fatal): %s", e)

    migrated = dict(discovery)
    migrated["command"] = test_cmd
    migrated["tb_type"] = tb_type
    migrated["cached_test_files"] = cached_test_files
    migrated["cached_tests"] = sorted(cached_test_files.keys())
    migrated["cached_dependency_files"] = cached_dependency_files
    migrated["cached_at"] = datetime.now().isoformat()
    return migrated


# version -> migration fn applying CURRENT version -> next version. Add new
# entries here (never mutate an existing one) when test_discovery's shape
# changes again; CURRENT_TEST_DISCOVERY_SCHEMA_VERSION follows automatically.
TEST_DISCOVERY_MIGRATIONS: dict[int, Callable[[dict, str], Awaitable[dict]]] = {
    1: _migrate_v1_add_tb_type_and_file_map,
}

CURRENT_TEST_DISCOVERY_SCHEMA_VERSION = max(TEST_DISCOVERY_MIGRATIONS) + 1


async def ensure_test_discovery_current(discovery: dict, sim_dir: str) -> dict:
    """Bring a test_discovery dict up to CURRENT_TEST_DISCOVERY_SCHEMA_VERSION.

    schema_version absent is treated as 1 (the pre-F-175 shape). A
    schema_version already at or beyond CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
    is left untouched — no migration is defined for versions ahead of what
    this codebase knows about.

    Returns a new dict; never mutates the input (even in the no-op case) and
    does not persist anything — callers (e.g. list_tests()) decide whether/
    how to save via save_sim_config, and can compare the result against the
    input to decide whether a save is even needed.

    If a migration step's own re-scan fails (_MigrationIncomplete), that
    step's version is NOT stamped — the returned dict is left at whatever
    version was successfully reached, so the next call retries the failed
    step instead of permanently recording an empty result as "migrated".
    """
    discovery = dict(discovery)
    version = discovery.get("schema_version", 1)
    while version in TEST_DISCOVERY_MIGRATIONS:
        try:
            discovery = await TEST_DISCOVERY_MIGRATIONS[version](discovery, sim_dir)
        except _MigrationIncomplete as e:
            logger.debug(
                "schema migration: version %d step incomplete, will retry next call: %s",
                version, e,
            )
            discovery["schema_version"] = version
            return discovery
        version += 1
    discovery["schema_version"] = version
    return discovery
