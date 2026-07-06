"""Tests for TB source provenance (F-175).

Covers the two new units the feature is built on:
  - test_resolution.parse_test_discovery_output — turns test_discovery's
    grep -n output into {test_name: defining_file_path}, for uvm/sv_directed/
    legacy tb_type formats.
  - tb_provenance.{resolve_tb_source_file, compute_file_sha256,
    build_tb_provenance} — looks up a resolved test's file and hashes it.

sim_batch_run/sim_regression/sim_bridge_run all call build_tb_provenance()
directly, so checksum-stability/change-detection is proven once here rather
than duplicated across three MCP tool integration tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp import checkpoint_manager
from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tb_provenance import (
    build_tb_provenance,
    compute_file_sha256,
    find_dependency_files,
    format_tb_provenance,
    resolve_tb_source_file,
)
from xcelium_mcp.test_resolution import parse_test_discovery_output

# ---------------------------------------------------------------------------
# parse_test_discovery_output
# ---------------------------------------------------------------------------


class TestParseTestDiscoveryOutput:
    def test_uvm_single_test(self) -> None:
        raw = (
            "/sim/tb/tests/top015_test.sv:42:class VENEZIA_TOP015_i2c_8bit_offset_test extends uvm_test;"
        )
        result = parse_test_discovery_output(raw, "uvm")
        assert result == {
            "VENEZIA_TOP015_i2c_8bit_offset_test": "/sim/tb/tests/top015_test.sv",
        }

    def test_uvm_multiple_tests_different_files(self) -> None:
        raw = (
            "/sim/tb/tests/top014_test.sv:10:class VENEZIA_TOP014_test extends uvm_test;\n"
            "/sim/tb/tests/top015_test.sv:42:class VENEZIA_TOP015_i2c_8bit_offset_test extends uvm_test;\n"
        )
        result = parse_test_discovery_output(raw, "uvm")
        assert result == {
            "VENEZIA_TOP014_test": "/sim/tb/tests/top014_test.sv",
            "VENEZIA_TOP015_i2c_8bit_offset_test": "/sim/tb/tests/top015_test.sv",
        }

    def test_uvm_two_tests_same_file(self) -> None:
        """Shared TB file among tests → both names map to the same path
        (so their provenance checksums will naturally match)."""
        raw = (
            "/sim/tb/tests/shared.sv:10:class TEST_A extends uvm_test;\n"
            "/sim/tb/tests/shared.sv:55:class TEST_B extends uvm_test;\n"
        )
        result = parse_test_discovery_output(raw, "uvm")
        assert result == {
            "TEST_A": "/sim/tb/tests/shared.sv",
            "TEST_B": "/sim/tb/tests/shared.sv",
        }

    def test_sv_directed_program(self) -> None:
        raw = "/sim/tb/directed/prog1.sv:5:program TOP014_DIRECTED_TEST;"
        result = parse_test_discovery_output(raw, "sv_directed")
        assert result == {"TOP014_DIRECTED_TEST": "/sim/tb/directed/prog1.sv"}

    def test_legacy_v_file_listing(self) -> None:
        raw = "/sim/tb_tests/TOP015.v\n/sim/tb_tests/TOP016.v\n"
        result = parse_test_discovery_output(raw, "unknown")
        assert result == {
            "TOP015": "/sim/tb_tests/TOP015.v",
            "TOP016": "/sim/tb_tests/TOP016.v",
        }

    def test_malformed_lines_skipped(self) -> None:
        """Lines that don't match the expected grep -n shape are dropped,
        not raised as errors — discovery is best-effort."""
        raw = (
            "not a valid grep line at all\n"
            "/sim/tb/tests/top015_test.sv:42:class VENEZIA_TOP015_test extends uvm_test;\n"
        )
        result = parse_test_discovery_output(raw, "uvm")
        assert result == {"VENEZIA_TOP015_test": "/sim/tb/tests/top015_test.sv"}

    def test_empty_input(self) -> None:
        assert parse_test_discovery_output("", "uvm") == {}
        assert parse_test_discovery_output("   \n  \n", "sv_directed") == {}


# ---------------------------------------------------------------------------
# compute_file_sha256
# ---------------------------------------------------------------------------


class TestComputeFileSha256:
    @pytest.mark.asyncio
    async def test_hash_matches_hashlib_reference(self, tmp_path) -> None:
        import hashlib

        f = tmp_path / "tb_test.sv"
        content = b"class VENEZIA_TOP015_test extends uvm_test;\nendclass\n"
        f.write_bytes(content)

        result = await compute_file_sha256(str(f))
        assert result == hashlib.sha256(content).hexdigest()

    @pytest.mark.asyncio
    async def test_identical_content_same_checksum(self, tmp_path) -> None:
        """Two independent files with identical content hash identically —
        proves the checksum is content-based, not path-based."""
        content = b"identical tb source content\n"
        f1 = tmp_path / "a.sv"
        f2 = tmp_path / "b.sv"
        f1.write_bytes(content)
        f2.write_bytes(content)

        h1 = await compute_file_sha256(str(f1))
        h2 = await compute_file_sha256(str(f2))
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_modified_content_changes_checksum(self, tmp_path) -> None:
        """Modifying the TB source after a first hash must change the
        checksum — this is the core provenance-verification guarantee."""
        f = tmp_path / "tb_test.sv"
        f.write_text("class TEST extends uvm_test;\nendclass\n")
        h_before = await compute_file_sha256(str(f))

        f.write_text("class TEST extends uvm_test;\n  // modified\nendclass\n")
        h_after = await compute_file_sha256(str(f))

        assert h_before != h_after

    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self, tmp_path) -> None:
        missing = tmp_path / "does_not_exist.sv"
        result = await compute_file_sha256(str(missing))
        assert result is None


# ---------------------------------------------------------------------------
# resolve_tb_source_file / build_tb_provenance
# ---------------------------------------------------------------------------


class TestResolveTbSourceFile:
    @pytest.mark.asyncio
    async def test_found_in_cache(self, tmp_path) -> None:
        cfg = {
            "test_discovery": {
                "cached_test_files": {
                    "VENEZIA_TOP015_test": str(tmp_path / "top015.sv"),
                }
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            result = await resolve_tb_source_file("VENEZIA_TOP015_test", str(tmp_path))
        assert result == str(tmp_path / "top015.sv")

    @pytest.mark.asyncio
    async def test_not_in_cache_returns_none(self, tmp_path) -> None:
        """Backward compat: a config discovered before F-175 (or one where
        grep found no match) has no cached_test_files entry — must return
        None, not raise."""
        cfg = {"test_discovery": {"cached_tests": ["VENEZIA_TOP015_test"]}}
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            result = await resolve_tb_source_file("VENEZIA_TOP015_test", str(tmp_path))
        assert result is None

    @pytest.mark.asyncio
    async def test_no_config_returns_none(self, tmp_path) -> None:
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=None),
        ):
            result = await resolve_tb_source_file("VENEZIA_TOP015_test", str(tmp_path))
        assert result is None


class TestFindDependencyFiles:
    """find_dependency_files scans a TB file for `include`/import references
    and resolves each to an actual path — one level deep (F-175 gap fix,
    2026-07-06): a test's own file may be unchanged while a shared component
    it `includes` changes, and that must still be detectable."""

    @pytest.mark.asyncio
    async def test_no_references_returns_empty(self, tmp_path) -> None:
        f = tmp_path / "top015.sv"
        f.write_text("class VENEZIA_TOP015_test extends uvm_test;\nendclass\n")
        result = await find_dependency_files(str(f), str(tmp_path))
        assert result == []

    @pytest.mark.asyncio
    async def test_backtick_include_resolved_by_basename(self, tmp_path) -> None:
        f = tmp_path / "top015.sv"
        f.write_text('`include "i2c_sequence.svh"\nclass TEST extends uvm_test;\nendclass\n')
        seq_file = tmp_path / "shared" / "i2c_sequence.svh"
        seq_file.parent.mkdir()
        seq_file.write_text("// sequence body\n")

        with patch("xcelium_mcp.tb_provenance.shell_run", new_callable=AsyncMock,
                    return_value=str(seq_file)):
            result = await find_dependency_files(str(f), str(tmp_path))

        assert result == [str(seq_file)]

    @pytest.mark.asyncio
    async def test_import_package_resolved_via_grep(self, tmp_path) -> None:
        f = tmp_path / "top015.sv"
        f.write_text("import i2c_pkg::*;\nclass TEST extends uvm_test;\nendclass\n")
        pkg_file = tmp_path / "i2c_pkg.sv"
        pkg_file.write_text("package i2c_pkg;\nendpackage\n")

        with patch("xcelium_mcp.tb_provenance.shell_run", new_callable=AsyncMock,
                    return_value=str(pkg_file)):
            result = await find_dependency_files(str(f), str(tmp_path))

        assert result == [str(pkg_file)]

    @pytest.mark.asyncio
    async def test_unresolvable_reference_silently_skipped(self, tmp_path) -> None:
        f = tmp_path / "top015.sv"
        f.write_text('`include "missing_file.svh"\nclass TEST extends uvm_test;\nendclass\n')

        with patch("xcelium_mcp.tb_provenance.shell_run", new_callable=AsyncMock,
                    return_value=""):
            result = await find_dependency_files(str(f), str(tmp_path))

        assert result == []

    @pytest.mark.asyncio
    async def test_unreadable_test_file_returns_empty(self, tmp_path) -> None:
        missing = tmp_path / "does_not_exist.sv"
        result = await find_dependency_files(str(missing), str(tmp_path))
        assert result == []


class TestBuildTbProvenance:
    @pytest.mark.asyncio
    async def test_returns_files_and_combined_sha256(self, tmp_path) -> None:
        tb_file = tmp_path / "top015.sv"
        tb_file.write_text("class VENEZIA_TOP015_test extends uvm_test;\nendclass\n")
        cfg = {
            "test_discovery": {
                "cached_test_files": {"VENEZIA_TOP015_test": str(tb_file)}
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            result = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))

        assert result is not None
        assert result["files"] == [
            {"path": str(tb_file), "sha256": await compute_file_sha256(str(tb_file))}
        ]
        assert len(result["combined_sha256"]) == 64  # hex-encoded sha256

    @pytest.mark.asyncio
    async def test_includes_direct_dependency_file(self, tmp_path) -> None:
        """The test's own file `includes` a shared sequence file — both
        must appear in "files", and the dependency's own content
        contributes to combined_sha256."""
        seq_file = tmp_path / "i2c_sequence.svh"
        seq_file.write_text("task send_start; endtask\n")
        tb_file = tmp_path / "top015.sv"
        tb_file.write_text('`include "i2c_sequence.svh"\nclass VENEZIA_TOP015_test extends uvm_test;\nendclass\n')
        cfg = {
            "test_discovery": {
                "cached_test_files": {"VENEZIA_TOP015_test": str(tb_file)}
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
            patch("xcelium_mcp.tb_provenance.shell_run", new_callable=AsyncMock,
                  return_value=str(seq_file)),
        ):
            result = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))

        paths = {f["path"] for f in result["files"]}
        assert paths == {str(tb_file), str(seq_file)}
        # primary file listed first
        assert result["files"][0]["path"] == str(tb_file)

    @pytest.mark.asyncio
    async def test_dependency_only_change_alters_combined_checksum(self, tmp_path) -> None:
        """The test's OWN file is unchanged — only the shared file it
        `include`s changes — combined_sha256 must still change (this is
        exactly the gap single-file hashing missed)."""
        seq_file = tmp_path / "i2c_sequence.svh"
        seq_file.write_text("task send_start; endtask\n")
        tb_file = tmp_path / "top015.sv"
        tb_file.write_text('`include "i2c_sequence.svh"\nclass VENEZIA_TOP015_test extends uvm_test;\nendclass\n')
        cfg = {
            "test_discovery": {
                "cached_test_files": {"VENEZIA_TOP015_test": str(tb_file)}
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
            patch("xcelium_mcp.tb_provenance.shell_run", new_callable=AsyncMock,
                  return_value=str(seq_file)),
        ):
            prov_run1 = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))
            seq_file.write_text("task send_start; endtask\n// added a second time\ntask drain; endtask\n")
            prov_run2 = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))

        assert prov_run1["combined_sha256"] != prov_run2["combined_sha256"]
        # the test's own file hash is unchanged — proves the *dependency's*
        # content is what moved the combined checksum, not the primary file
        primary1 = next(f for f in prov_run1["files"] if f["path"] == str(tb_file))
        primary2 = next(f for f in prov_run2["files"] if f["path"] == str(tb_file))
        assert primary1["sha256"] == primary2["sha256"]

    @pytest.mark.asyncio
    async def test_shared_tb_file_two_tests_same_checksum(self, tmp_path) -> None:
        """Two tests defined in the same TB file (F-175 acceptance criterion:
        '공통 TB 소스를 쓰는 테스트끼리는 체크섬이 같아야 함') must produce
        identical provenance checksums."""
        shared_file = tmp_path / "shared.sv"
        shared_file.write_text(
            "class TEST_A extends uvm_test;\nendclass\n"
            "class TEST_B extends uvm_test;\nendclass\n"
        )
        cfg = {
            "test_discovery": {
                "cached_test_files": {
                    "TEST_A": str(shared_file),
                    "TEST_B": str(shared_file),
                }
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            prov_a = await build_tb_provenance("TEST_A", str(tmp_path))
            prov_b = await build_tb_provenance("TEST_B", str(tmp_path))

        assert prov_a["combined_sha256"] == prov_b["combined_sha256"]
        assert prov_a["files"] == prov_b["files"]

    @pytest.mark.asyncio
    async def test_file_modified_between_runs_changes_checksum(self, tmp_path) -> None:
        """Same test re-run after its TB source changed → checksum differs
        (F-175 acceptance criterion: 'TB 소스를 수정 후 재실행 시 체크섬 변경')."""
        tb_file = tmp_path / "top015.sv"
        tb_file.write_text("class VENEZIA_TOP015_test extends uvm_test;\n  // v1\nendclass\n")
        cfg = {
            "test_discovery": {
                "cached_test_files": {"VENEZIA_TOP015_test": str(tb_file)}
            }
        }
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            prov_run1 = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))
            tb_file.write_text("class VENEZIA_TOP015_test extends uvm_test;\n  // v2\nendclass\n")
            prov_run2 = await build_tb_provenance("VENEZIA_TOP015_test", str(tmp_path))

        assert prov_run1["combined_sha256"] != prov_run2["combined_sha256"]
        assert prov_run1["files"][0]["path"] == prov_run2["files"][0]["path"]

    @pytest.mark.asyncio
    async def test_no_file_found_returns_none(self, tmp_path) -> None:
        cfg = {"test_discovery": {"cached_test_files": {}}}
        with (
            patch("xcelium_mcp.tb_provenance.resolve_sim_dir", new_callable=AsyncMock,
                  return_value=str(tmp_path)),
            patch("xcelium_mcp.tb_provenance.load_sim_config", new_callable=AsyncMock,
                  return_value=cfg),
        ):
            result = await build_tb_provenance("UNKNOWN_TEST", str(tmp_path))
        assert result is None


class TestFormatTbProvenance:
    def test_single_file(self) -> None:
        provenance = {
            "files": [{"path": "/sim/tb/top015.sv", "sha256": "a" * 64}],
            "combined_sha256": "b" * 64,
        }
        text = format_tb_provenance(provenance)
        assert "/sim/tb/top015.sv" in text
        assert "a" * 64 in text
        assert "combined_sha256: " + "b" * 64 in text

    def test_multiple_files_all_listed(self) -> None:
        provenance = {
            "files": [
                {"path": "/sim/tb/top015.sv", "sha256": "a" * 64},
                {"path": "/sim/tb/i2c_sequence.svh", "sha256": "c" * 64},
            ],
            "combined_sha256": "b" * 64,
        }
        text = format_tb_provenance(provenance)
        assert "/sim/tb/top015.sv" in text
        assert "/sim/tb/i2c_sequence.svh" in text


# ---------------------------------------------------------------------------
# checkpoint_manager.register_checkpoint(tb_source=...) — F-175 manifest field
# ---------------------------------------------------------------------------


class TestRegisterCheckpointTbSource:
    def test_tb_source_stored_when_provided(self, tmp_path) -> None:
        sim_dir = str(tmp_path)
        tb_source = {"path": "/sim/tb/top015.sv", "sha256": "a" * 64}

        entry = checkpoint_manager.register_checkpoint(
            sim_dir, "L1_TOP015", 1000, origin="regression",
            test_name="VENEZIA_TOP015_test", tb_source=tb_source,
        )

        assert entry["tb_source"] == tb_source
        manifest = checkpoint_manager._read_manifest(sim_dir)
        assert manifest["checkpoints"]["L1_TOP015"]["tb_source"] == tb_source

    def test_tb_source_omitted_when_none(self, tmp_path) -> None:
        """Backward compat: when provenance couldn't be resolved (None),
        the manifest entry must not gain a bogus 'tb_source': null key —
        existing manifest readers shouldn't need to handle it."""
        sim_dir = str(tmp_path)

        entry = checkpoint_manager.register_checkpoint(
            sim_dir, "L1_TOP014", 500, origin="bridge",
        )

        assert "tb_source" not in entry
        manifest = checkpoint_manager._read_manifest(sim_dir)
        assert "tb_source" not in manifest["checkpoints"]["L1_TOP014"]


# ---------------------------------------------------------------------------
# BridgeManager.current_test_name / current_tb_source — F-175 bridge-mode
# provenance handoff to checkpoint(action=save)
# ---------------------------------------------------------------------------


class TestBridgeManagerProvenanceState:
    def test_defaults_empty(self) -> None:
        bm = BridgeManager()
        assert bm.current_test_name == ""
        assert bm.current_tb_source is None

    def test_cleared_on_disconnect(self) -> None:
        bm = BridgeManager()
        bm.current_test_name = "VENEZIA_TOP015_test"
        bm.current_tb_source = {"path": "/sim/tb/top015.sv", "sha256": "a" * 64}

        bm.set_xmsim(None)

        assert bm.current_test_name == ""
        assert bm.current_tb_source is None
