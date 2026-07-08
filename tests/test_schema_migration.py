"""Tests for the F-175 test_discovery schema migration gap fix.

Covers schema_migration.ensure_test_discovery_current — the module-1 scope of
docs/02-design/features/xcelium-mcp-f175-provenance-migration-gap.design.md
(Design §8.2 scenarios 1, 3, 4) — plus tb_provenance.provenance_unavailable_reason
(F-2 diagnostic helper).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.schema_migration import (
    CURRENT_TEST_DISCOVERY_SCHEMA_VERSION,
    ensure_test_discovery_current,
)
from xcelium_mcp.tb_provenance import provenance_unavailable_reason

# ---------------------------------------------------------------------------
# ensure_test_discovery_current
# ---------------------------------------------------------------------------


class TestEnsureTestDiscoveryCurrent:
    @pytest.mark.asyncio
    async def test_v1_config_gets_migrated(self) -> None:
        """Design §8.2 scenario 1: schema_version absent + cached_tests-only
        (the pre-F-175 shape) gets tb_type/cached_test_files/
        cached_dependency_files backfilled and schema_version stamped."""
        discovery = {
            "command": "ls /sim/tb_tests/*.v || true",
            "cached_tests": ["TOP015", "TOP016"],
            "cached_at": "2026-04-16T15:38:04",
        }
        grep_output = (
            "/sim/tb/tests/top015_test.sv:42:class TOP015 extends uvm_test;\n"
            "/sim/tb/tests/top016_test.sv:10:class TOP016 extends uvm_test;\n"
        )
        with (
            patch(
                "xcelium_mcp.schema_migration.analyze_tb_type",
                new_callable=AsyncMock,
                return_value="uvm",
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.shell_run",
                new_callable=AsyncMock,
                return_value=grep_output,
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.scan_test_dependencies",
                new_callable=AsyncMock,
                return_value={"scanned_primary_sha256": "deadbeef", "deps": []},
            ),
        ):
            result = await ensure_test_discovery_current(discovery, "/sim")

        assert result["schema_version"] == CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
        assert result["tb_type"] == "uvm"
        assert result["cached_test_files"] == {
            "TOP015": "/sim/tb/tests/top015_test.sv",
            "TOP016": "/sim/tb/tests/top016_test.sv",
        }
        assert set(result["cached_dependency_files"]) == {"TOP015", "TOP016"}
        assert result["cached_tests"] == ["TOP015", "TOP016"]

    @pytest.mark.asyncio
    async def test_already_current_config_is_noop(self) -> None:
        """Design §8.2 scenario 3: a config already at
        CURRENT_TEST_DISCOVERY_SCHEMA_VERSION triggers no re-scan (no
        performance regression for the common case)."""
        discovery = {
            "command": "grep -rn 'extends uvm_test' /sim || true",
            "tb_type": "uvm",
            "cached_tests": ["TOP015"],
            "cached_test_files": {"TOP015": "/sim/tb/tests/top015_test.sv"},
            "cached_dependency_files": {},
            "schema_version": CURRENT_TEST_DISCOVERY_SCHEMA_VERSION,
            "cached_at": "2026-07-08T00:00:00",
        }
        with (
            patch("xcelium_mcp.schema_migration.analyze_tb_type", new_callable=AsyncMock) as m_tb_type,
            patch("xcelium_mcp.test_discovery_scan.shell_run", new_callable=AsyncMock) as m_shell,
        ):
            result = await ensure_test_discovery_current(discovery, "/sim")

        m_tb_type.assert_not_awaited()
        m_shell.assert_not_awaited()
        assert result == discovery

    @pytest.mark.asyncio
    async def test_migration_is_pure_does_not_mutate_input(self) -> None:
        """ensure_test_discovery_current returns a new dict — callers decide
        whether/how to persist (list_tests() does, in module-2 scope)."""
        discovery = {"cached_tests": ["TOP015"], "cached_at": "2026-04-16T15:38:04"}
        original = dict(discovery)
        with (
            patch(
                "xcelium_mcp.schema_migration.analyze_tb_type",
                new_callable=AsyncMock,
                return_value="uvm",
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.shell_run",
                new_callable=AsyncMock,
                return_value="",
            ),
        ):
            await ensure_test_discovery_current(discovery, "/sim")

        assert discovery == original

    @pytest.mark.asyncio
    async def test_migration_scan_failure_does_not_advance_schema_version(self) -> None:
        """A failed re-scan (e.g. remote shell error) must NOT stamp
        schema_version forward — otherwise the project is permanently
        recorded as "migrated" with an empty cached_test_files, indistinguishable
        from a legitimately test-less project, and only a manual
        sim_discover(force=True) could ever recover it. Leaving schema_version
        at its pre-attempt value means the next call retries the migration."""
        discovery = {"cached_tests": ["TOP015"], "cached_at": "2026-04-16T15:38:04"}
        with (
            patch(
                "xcelium_mcp.schema_migration.analyze_tb_type",
                new_callable=AsyncMock,
                return_value="uvm",
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.shell_run",
                new_callable=AsyncMock,
                side_effect=RuntimeError("ssh connection lost"),
            ),
        ):
            result = await ensure_test_discovery_current(discovery, "/sim")

        assert result["schema_version"] == 1
        assert "cached_test_files" not in result
        assert result["cached_tests"] == ["TOP015"]

    @pytest.mark.asyncio
    async def test_migration_retries_successfully_after_a_prior_failure(self) -> None:
        """A config left at version 1 by a previous failed attempt (see above)
        must successfully migrate on the next call once the transient failure
        is gone — proving the retry path actually works, not just that it's
        attempted."""
        discovery = {"cached_tests": ["TOP015"], "cached_at": "2026-04-16T15:38:04"}
        grep_output = "/sim/tb/tests/top015_test.sv:42:class TOP015 extends uvm_test;\n"
        with (
            patch(
                "xcelium_mcp.schema_migration.analyze_tb_type",
                new_callable=AsyncMock,
                return_value="uvm",
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.shell_run",
                new_callable=AsyncMock,
                return_value=grep_output,
            ),
            patch(
                "xcelium_mcp.test_discovery_scan.scan_test_dependencies",
                new_callable=AsyncMock,
                return_value={"scanned_primary_sha256": "abc", "deps": []},
            ),
        ):
            result = await ensure_test_discovery_current(discovery, "/sim")

        assert result["schema_version"] == CURRENT_TEST_DISCOVERY_SCHEMA_VERSION
        assert result["cached_test_files"] == {"TOP015": "/sim/tb/tests/top015_test.sv"}


# ---------------------------------------------------------------------------
# provenance_unavailable_reason (F-2)
# ---------------------------------------------------------------------------


class TestProvenanceUnavailableReason:
    @pytest.mark.asyncio
    async def test_unmigrated_project_reports_schema_gap(self) -> None:
        """Design §8.2 scenario 4: a project missing cached_test_files
        entirely (never migrated) gets a distinct diagnosis from a single
        missing test name."""
        config = {"test_discovery": {"cached_tests": ["TOP015"]}}
        with (
            patch(
                "xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"
            ),
            patch(
                "xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock, return_value=config
            ),
        ):
            reason = await provenance_unavailable_reason("TOP015", "/sim")

        assert reason is not None
        assert "not yet migrated" in reason

    @pytest.mark.asyncio
    async def test_migrated_project_missing_single_test_reports_per_test_gap(self) -> None:
        """Design §8.2 scenario 4: a migrated project (has cached_test_files)
        but missing this one test name gets the per-test diagnosis, not the
        schema-migration one."""
        config = {
            "test_discovery": {
                "cached_test_files": {"TOP016": "/sim/tb/tests/top016_test.sv"},
            }
        }
        with (
            patch(
                "xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"
            ),
            patch(
                "xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock, return_value=config
            ),
        ):
            reason = await provenance_unavailable_reason("TOP015", "/sim")

        assert reason is not None
        assert "not found in cached_test_files" in reason

    @pytest.mark.asyncio
    async def test_test_present_returns_none(self) -> None:
        config = {
            "test_discovery": {
                "cached_test_files": {"TOP015": "/sim/tb/tests/top015_test.sv"},
            }
        }
        with (
            patch(
                "xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"
            ),
            patch(
                "xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock, return_value=config
            ),
        ):
            reason = await provenance_unavailable_reason("TOP015", "/sim")

        assert reason is None

    @pytest.mark.asyncio
    async def test_no_config_returns_none(self) -> None:
        with (
            patch(
                "xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock, return_value="/sim"
            ),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock, return_value=None),
        ):
            reason = await provenance_unavailable_reason("TOP015", "/sim")

        assert reason is None
