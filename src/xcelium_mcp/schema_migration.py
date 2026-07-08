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
from typing import Awaitable, Callable

from xcelium_mcp.registry import save_sim_config
from xcelium_mcp.sim_env_detection import analyze_tb_type
from xcelium_mcp.test_discovery_scan import build_test_discovery_dict

logger = logging.getLogger(__name__)


class _MigrationIncomplete(Exception):
    """Raised internally when a migration step's own re-scan fails.

    ensure_test_discovery_current catches this and leaves schema_version at
    its pre-attempt value instead of stamping the next version — so the next
    call retries the same migration step, rather than permanently recording
    an empty cached_test_files as if it were a legitimately-empty project.
    """


async def _migrate_v1_add_tb_type_and_file_map(discovery: dict, sim_dir: str) -> dict:
    """v1 (cached_tests name-list only, pre-F-175) -> v2 (tb_type +
    cached_test_files + cached_dependency_files).

    Re-detects tb_type (unless already known) and rebuilds `command` in the
    new -n format rather than trusting the stored (pre-F-175, name-only)
    command — the old output shape can't be parsed by
    parse_test_discovery_output. build_test_discovery_dict()'s primary scan
    failure (RuntimeError/OSError/asyncio.TimeoutError) is converted to
    _MigrationIncomplete here (caught by ensure_test_discovery_current, which
    then does NOT advance schema_version — so the next call retries instead
    of permanently recording an empty cached_test_files as if this were a
    legitimately test-less project). A failed *dependency* scan stays
    best-effort inside build_test_discovery_dict (cached_test_files is
    already populated by that point).
    """
    tb_type = discovery.get("tb_type") or await analyze_tb_type(sim_dir)
    try:
        fresh = await build_test_discovery_dict(sim_dir, tb_type)
    except (RuntimeError, OSError, asyncio.TimeoutError) as e:
        raise _MigrationIncomplete(f"test discovery re-scan failed: {e}") from e

    migrated = dict(discovery)
    migrated.update(fresh)
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


async def ensure_and_persist_test_discovery(resolved_dir: str, config: dict) -> list[str]:
    """Migrate config["test_discovery"] to current and persist iff changed.

    F-179: list_tests() and resolve_test_name() both used to inline this same
    three-step transaction (call ensure_test_discovery_current, compare
    against the original, conditionally save_sim_config) — this is the single
    shared implementation. Mutates config["test_discovery"] in place when a
    migration actually ran; returns the resulting cached_tests either way.
    """
    discovery = config.get("test_discovery", {})
    migrated = await ensure_test_discovery_current(discovery, resolved_dir)
    if migrated != discovery:
        config["test_discovery"] = migrated
        await save_sim_config(resolved_dir, config)
    return migrated.get("cached_tests", [])
