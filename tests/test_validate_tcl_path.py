"""Tests for validate_tcl_path (F-150) and its use in open_database/compare_simvision.

F-150: validate_path() only guards filesystem safety (null bytes, '..'
traversal) — it never blocked Tcl metacharacters ('[', ']', '$', etc.), yet it
was the sole guard for SHM paths reaching raw Tcl interpolation
(`bridge.execute(f"database open {shm_path}")`). validate_tcl_path() closes
that gap with a strict allowlist.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xcelium_mcp.shell_utils import validate_tcl_path


class TestValidateTclPath:
    @pytest.mark.parametrize("valid", [
        "/sim/run/dump/ci_top_TOP015.shm",
        "./dump/ci_top.shm",
        "dump.shm",
        "waves.shm",
        "/tmp/user/test-run_01.shm",
    ])
    def test_valid_paths_pass(self, valid):
        assert validate_tcl_path(valid) is None

    def test_dotdot_traversal_still_rejected_via_validate_path(self):
        """Pre-existing validate_path() traversal check ('..' component) still applies —
        not new to F-150, but confirms validate_tcl_path() doesn't loosen it."""
        assert validate_tcl_path("../dump/ci_top.shm") is not None

    @pytest.mark.parametrize("payload", [
        "/tmp/x[exec id].shm",
        "/tmp/x$env(HOME).shm",
        "/tmp/x; exec id",
        "/tmp/x.shm\ndatabase close",
        "/tmp/x.shm\rdatabase close",
        "/tmp/x{foo}.shm",
        "/tmp/x y.shm",  # embedded space
        "/tmp/x\"y.shm",
        "/tmp/x\\y.shm",
    ])
    def test_injection_payloads_rejected(self, payload):
        err = validate_tcl_path(payload)
        assert err is not None
        assert "ERROR" in err

    def test_delegates_null_byte_check_to_validate_path(self):
        err = validate_tcl_path("/tmp/x\x00.shm")
        assert err is not None
        assert "null bytes" in err

    def test_delegates_traversal_check_to_validate_path(self):
        err = validate_tcl_path("/tmp/../../etc/passwd")
        assert err is not None
        assert "traversal" in err


@pytest.mark.asyncio
async def test_open_database_rejects_injection_before_bridge_call() -> None:
    """F-150: open_database must not reach bridge.execute() with a malicious shm_path."""
    from xcelium_mcp.simvision_ops import open_database

    fake_bridges = MagicMock()
    fake_bridges.simvision_raw = None
    fake_bridges.xmsim = MagicMock()
    fake_bridges.xmsim.execute = AsyncMock(return_value="ok")

    result = await open_database(fake_bridges, "/tmp/x[exec id].shm")

    assert "ERROR" in result
    fake_bridges.xmsim.execute.assert_not_called()


@pytest.mark.asyncio
async def test_open_database_accepts_normal_path() -> None:
    from xcelium_mcp.simvision_ops import open_database

    fake_bridges = MagicMock()
    fake_bridges.simvision_raw = None
    fake_bridges.xmsim = MagicMock()
    fake_bridges.xmsim.execute = AsyncMock(return_value="ok")

    result = await open_database(fake_bridges, "/sim/run/dump/test.shm")

    assert "ERROR" not in result
    fake_bridges.xmsim.execute.assert_called_once()


@pytest.mark.asyncio
async def test_reload_waveform_rejects_injection_shm_path() -> None:
    """F-149: reload_waveform had NO validation at all on shm_path before this fix."""
    from xcelium_mcp.simvision_ops import reload_waveform

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    result = await reload_waveform(fake_bridges, "/tmp/x[exec id].shm")

    assert "ERROR" in result
    fake_bridge.execute.assert_not_called()


@pytest.mark.asyncio
async def test_reload_waveform_accepts_normal_path() -> None:
    from xcelium_mcp.simvision_ops import reload_waveform

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    result = await reload_waveform(fake_bridges, "/sim/run/dump/test.shm")

    assert "ERROR" not in result
    assert fake_bridge.execute.called


@pytest.mark.asyncio
async def test_reload_waveform_empty_shm_path_reloads_same_db() -> None:
    """Empty shm_path means 'reload the current SHM' — not subject to path
    validation (no path is interpolated in this branch)."""
    from xcelium_mcp.simvision_ops import reload_waveform

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    result = await reload_waveform(fake_bridges, "")

    assert "ERROR" not in result
    assert fake_bridge.execute.called


@pytest.mark.asyncio
async def test_compare_simvision_rejects_injection_shm_after() -> None:
    """F-150: compare_simvision must reject a malicious shm_after before the
    Tcl `database open` call, even though the caller's validate_path() (in
    compare_waveforms) doesn't block Tcl metacharacters."""
    from xcelium_mcp.simvision_ops import compare_simvision

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    with (
        patch("xcelium_mcp.simvision_ops.detect_vnc_display", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.shell_run", AsyncMock(return_value=":1 (active)")),
        patch("xcelium_mcp.simvision_ops.resolve_sim_dir", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.load_sim_config", AsyncMock(return_value=None)),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.simvision_ops.scan_ready_files", AsyncMock(return_value=[(9877, "simvision")])),
        patch("asyncio.sleep", AsyncMock(return_value=None)),
    ):
        connect_fn = AsyncMock(return_value="connected")
        result = await compare_simvision(
            fake_bridges, connect_fn,
            shm_before="/sim/before.shm",
            shm_after="/tmp/x[exec id].shm",
            signals=["top.hw.clk"],
            display=":1",
        )

    assert "ERROR" in result
    fake_bridge.execute.assert_not_called()


@pytest.mark.asyncio
async def test_compare_simvision_rejects_injection_in_signals() -> None:
    """F-151: compare_simvision must sanitize `signals` too — this was the one
    bridge-facing tool that skipped sanitize_signal_name (unlike csv_diff mode,
    which sanitizes the identical list via csv_cache.extract())."""
    from xcelium_mcp.simvision_ops import compare_simvision

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    with (
        patch("xcelium_mcp.simvision_ops.detect_vnc_display", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.shell_run", AsyncMock(return_value=":1 (active)")),
        patch("xcelium_mcp.simvision_ops.resolve_sim_dir", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.load_sim_config", AsyncMock(return_value=None)),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.simvision_ops.scan_ready_files", AsyncMock(return_value=[(9877, "simvision")])),
        patch("asyncio.sleep", AsyncMock(return_value=None)),
    ):
        connect_fn = AsyncMock(return_value="connected")
        result = await compare_simvision(
            fake_bridges, connect_fn,
            shm_before="/sim/before.shm",
            shm_after="/sim/after.shm",  # valid — must pass the shm_after gate
            signals=["top.a[exec id]"],
            display=":1",
        )

    assert "ERROR" in result
    # database open (shm_after) is allowed to have happened; the WAVEFORM_ADD
    # calls carrying the malicious signal must not.
    assert not any("WAVEFORM_ADD" in str(c) for c in fake_bridge.execute.call_args_list)


@pytest.mark.asyncio
async def test_compare_simvision_accepts_normal_signals() -> None:
    from xcelium_mcp.simvision_ops import compare_simvision

    fake_bridge = MagicMock()
    fake_bridge.execute = AsyncMock(return_value="ok")
    fake_bridges = MagicMock()
    fake_bridges.simvision = fake_bridge

    with (
        patch("xcelium_mcp.simvision_ops.detect_vnc_display", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.shell_run", AsyncMock(return_value=":1 (active)")),
        patch("xcelium_mcp.simvision_ops.resolve_sim_dir", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.load_sim_config", AsyncMock(return_value=None)),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.simvision_ops.scan_ready_files", AsyncMock(return_value=[(9877, "simvision")])),
        patch("asyncio.sleep", AsyncMock(return_value=None)),
    ):
        connect_fn = AsyncMock(return_value="connected")
        result = await compare_simvision(
            fake_bridges, connect_fn,
            shm_before="/sim/before.shm",
            shm_after="/sim/after.shm",
            signals=["top.hw.clk", "top.hw.data[7:0]"],
            display=":1",
        )

    assert "ERROR" not in result
    before_calls = [c for c in fake_bridge.execute.call_args_list if "BEFORE" in str(c)]
    after_calls = [c for c in fake_bridge.execute.call_args_list if "AFTER" in str(c)]
    assert before_calls and "top.hw.clk" in str(before_calls[0])
    assert after_calls and "cmp_after.top.hw.clk" in str(after_calls[0])
