"""Tests for test_discovery_scan.py (F-178).

build_test_discovery_dict() is the shared flow that used to be duplicated
between discovery.py::run_full_discovery's Phase A and
schema_migration.py::_migrate_v1_add_tb_type_and_file_map. Those two call
sites' own tests (test_schema_migration.py, and discovery.py's existing
run_full_discovery tests, which never reach force=True's Phase A) don't
directly exercise this function in isolation — these tests do.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.test_discovery_scan import (
    build_test_discovery_cmd,
    build_test_discovery_dict,
)


class TestBuildTestDiscoveryCmd:
    def test_uvm(self) -> None:
        cmd = build_test_discovery_cmd("/sim", "uvm")
        assert "grep -rn 'extends uvm_test'" in cmd
        assert "/sim" in cmd

    def test_sv_directed(self) -> None:
        cmd = build_test_discovery_cmd("/sim", "sv_directed")
        assert "program " in cmd

    def test_other_falls_back_to_ls(self) -> None:
        cmd = build_test_discovery_cmd("/sim", "unknown")
        assert "ls /sim/tb_tests/*.v" in cmd


class TestBuildTestDiscoveryDict:
    @pytest.mark.asyncio
    async def test_happy_path_populates_all_fields(self) -> None:
        grep_output = (
            "/sim/tb/tests/top015_test.sv:42:class TOP015 extends uvm_test;\n"
            "/sim/tb/tests/top016_test.sv:10:class TOP016 extends uvm_test;\n"
        )
        with (
            patch("xcelium_mcp.test_discovery_scan.shell_run", new_callable=AsyncMock,
                  return_value=grep_output),
            patch("xcelium_mcp.test_discovery_scan.scan_test_dependencies", new_callable=AsyncMock,
                  return_value={"scanned_primary_sha256": "abc", "deps": []}),
        ):
            result = await build_test_discovery_dict("/sim", "uvm")

        assert result["tb_type"] == "uvm"
        assert result["cached_test_files"] == {
            "TOP015": "/sim/tb/tests/top015_test.sv",
            "TOP016": "/sim/tb/tests/top016_test.sv",
        }
        assert result["cached_tests"] == ["TOP015", "TOP016"]
        assert set(result["cached_dependency_files"]) == {"TOP015", "TOP016"}
        assert "schema_version" not in result  # callers own schema versioning
        assert result["cached_at"]

    @pytest.mark.asyncio
    async def test_primary_scan_failure_propagates(self) -> None:
        """Callers (discovery.py, schema_migration.py) each decide their own
        failure policy — this function must not swallow the exception."""
        with patch("xcelium_mcp.test_discovery_scan.shell_run", new_callable=AsyncMock,
                    side_effect=RuntimeError("ssh connection lost")):
            with pytest.raises(RuntimeError):
                await build_test_discovery_dict("/sim", "uvm")

    @pytest.mark.asyncio
    async def test_dependency_scan_failure_is_non_fatal(self) -> None:
        """A failed dependency scan must not lose the already-populated
        cached_test_files — only cached_dependency_files stays empty."""
        grep_output = "/sim/tb/tests/top015_test.sv:42:class TOP015 extends uvm_test;\n"
        with (
            patch("xcelium_mcp.test_discovery_scan.shell_run", new_callable=AsyncMock,
                  return_value=grep_output),
            patch("xcelium_mcp.test_discovery_scan.scan_test_dependencies", new_callable=AsyncMock,
                  side_effect=RuntimeError("scan failed")),
        ):
            result = await build_test_discovery_dict("/sim", "uvm")

        assert result["cached_test_files"] == {"TOP015": "/sim/tb/tests/top015_test.sv"}
        assert result["cached_dependency_files"] == {}

    @pytest.mark.asyncio
    async def test_no_tests_found_returns_empty_maps(self) -> None:
        with patch("xcelium_mcp.test_discovery_scan.shell_run", new_callable=AsyncMock,
                    return_value=""):
            result = await build_test_discovery_dict("/sim", "uvm")

        assert result["cached_tests"] == []
        assert result["cached_test_files"] == {}
        assert result["cached_dependency_files"] == {}
