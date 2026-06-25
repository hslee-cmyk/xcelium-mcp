"""Tests for F-136 (checkpoint /tmp fallback removal) and F-137 (tmp cleanup)."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# F-136: restore_checkpoint_impl — no /tmp fallback on ValueError
# ---------------------------------------------------------------------------

class TestRestoreCheckpointImplF136:
    """restore_checkpoint_impl must return error (not /tmp fallback) when sim_dir unset."""

    @pytest.mark.asyncio
    async def test_valueerror_returns_error_not_tmp(self) -> None:
        """resolve_sim_dir raises ValueError → error string, not /tmp path."""
        from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl

        bridges = MagicMock()

        with patch(
            "xcelium_mcp.tools.checkpoint.resolve_sim_dir",
            side_effect=ValueError("No sim_dir registered"),
        ):
            result = await restore_checkpoint_impl(bridges, "chk1", "")

        assert result.startswith("ERROR:")
        assert "/tmp" not in result
        assert "sim_discover" in result.lower() or "project directory" in result.lower()

    @pytest.mark.asyncio
    async def test_valid_sim_dir_uses_project_path(self) -> None:
        """When sim_dir is valid, chk_base is under sim_dir/checkpoints (not /tmp)."""
        from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl

        bridges = MagicMock()
        mock_bridge = AsyncMock()
        mock_bridge.execute = AsyncMock(return_value="restore ok")
        bridges.xmsim = mock_bridge

        with (
            patch(
                "xcelium_mcp.tools.checkpoint.resolve_sim_dir",
                return_value="/project/sim",
            ),
            patch(
                "xcelium_mcp.tools.checkpoint.checkpoint_manager._read_manifest",
                return_value={"checkpoints": {}},
            ),
        ):
            result = await restore_checkpoint_impl(bridges, "", "/project/sim")

        # Should reach xmsim.execute with project-based path (normalize separators)
        call_args = mock_bridge.execute.call_args[0][0].replace("\\", "/")
        assert "/project/sim/checkpoints" in call_args
        assert "/tmp" not in call_args


# ---------------------------------------------------------------------------
# F-137: tmp_cleanup module
# ---------------------------------------------------------------------------

class TestCleanupOldLogs:
    """cleanup_old_logs deletes *.log and *.log.done older than TTL."""

    @pytest.mark.asyncio
    async def test_deletes_old_logs(self, tmp_path: Path) -> None:
        from xcelium_mcp.tmp_cleanup import cleanup_old_logs

        old_log = tmp_path / "sim_start_9876.log"
        old_log.write_text("old log")
        # Set mtime to 2 days ago
        old_time = time.time() - 2 * 86400
        os.utime(old_log, (old_time, old_time))

        deleted = await cleanup_old_logs(str(tmp_path), ttl_sec=86400)

        assert deleted == 1
        assert not old_log.exists()

    @pytest.mark.asyncio
    async def test_preserves_recent_logs(self, tmp_path: Path) -> None:
        from xcelium_mcp.tmp_cleanup import cleanup_old_logs

        new_log = tmp_path / "batch_test.log"
        new_log.write_text("recent log")
        # mtime is now (recent)

        deleted = await cleanup_old_logs(str(tmp_path), ttl_sec=86400)

        assert deleted == 0
        assert new_log.exists()

    @pytest.mark.asyncio
    async def test_deletes_log_done_files(self, tmp_path: Path) -> None:
        from xcelium_mcp.tmp_cleanup import cleanup_old_logs

        old_done = tmp_path / "regression_TOP015.log.done"
        old_done.write_text("done marker")
        old_time = time.time() - 2 * 86400
        os.utime(old_done, (old_time, old_time))

        deleted = await cleanup_old_logs(str(tmp_path), ttl_sec=86400)

        assert deleted == 1
        assert not old_done.exists()

    @pytest.mark.asyncio
    async def test_nonexistent_dir_returns_zero(self) -> None:
        from xcelium_mcp.tmp_cleanup import cleanup_old_logs

        deleted = await cleanup_old_logs("/tmp/xcelium_mcp_nonexistent_99999")
        assert deleted == 0


class TestCleanupSessionLogs:
    """cleanup_session_logs deletes all logs and PS files immediately."""

    @pytest.mark.asyncio
    async def test_deletes_all_logs_and_ps(self, tmp_path: Path) -> None:
        from xcelium_mcp.tmp_cleanup import cleanup_session_logs

        log1 = tmp_path / "sim_start_9876.log"
        log2 = tmp_path / "batch_TOP015.log.done"
        ps_file = tmp_path / "screenshot_abc.ps"
        other = tmp_path / "mcp_registry.json"  # must NOT be deleted

        for f in (log1, log2, ps_file, other):
            f.write_text("content")

        deleted = await cleanup_session_logs(str(tmp_path))

        assert deleted == 3
        assert not log1.exists()
        assert not log2.exists()
        assert not ps_file.exists()
        assert other.exists()  # permanent file preserved


# ---------------------------------------------------------------------------
# F-137: csv_cache disk cache and cleanup_stale_csv
# ---------------------------------------------------------------------------

class TestCsvCacheDiskcache:
    """csv_cache.extract() must use disk cache when mtime matches."""

    @pytest.mark.asyncio
    async def test_disk_cache_hit_skips_simvisdbutil(self, tmp_path: Path) -> None:
        """If CSV file exists and mtime matches, extract() returns it without running simvisdbutil."""
        import xcelium_mcp.csv_cache as csv_cache

        shm_path = str(tmp_path / "test.shm")
        # Create a fake SHM directory so getmtime works
        (tmp_path / "test.shm").mkdir()
        fake_mtime = int(os.path.getmtime(str(tmp_path / "test.shm")))

        # Create pre-existing CSV with mtime-keyed filename
        sig_hash = __import__("hashlib").md5("sig_a".encode()).hexdigest()[:8]
        csv_filename = f"mcp_csv_test_{sig_hash}_{fake_mtime}.csv"
        csv_file = tmp_path / csv_filename
        csv_file.write_text("SimTime,sig_a\n0,1\n")

        # Clear module-level cache to force fresh lookup
        csv_cache._cache.clear()

        async def _fake_get_tmp() -> str:
            return str(tmp_path)

        with (
            patch("xcelium_mcp.csv_cache.get_user_tmp_dir", _fake_get_tmp),
            patch("xcelium_mcp.csv_cache._resolve_simvisdbutil") as mock_svdb,
            patch("xcelium_mcp.csv_cache.shell_run") as mock_shell,
        ):
            result = await csv_cache.extract(
                shm_path=shm_path,
                signals=["sig_a"],
            )

        # simvisdbutil should NOT have been called
        mock_svdb.assert_not_called()
        mock_shell.assert_not_called()
        assert Path(result) == csv_file

    @pytest.mark.asyncio
    async def test_cache_key_includes_mtime(self) -> None:
        """_cache_key with different mtime values produces different keys."""
        from xcelium_mcp.csv_cache import _cache_key

        k1 = _cache_key("/a.shm", ["sig"], 0, 0, shm_mtime=1000)
        k2 = _cache_key("/a.shm", ["sig"], 0, 0, shm_mtime=2000)
        assert k1 != k2


class TestCleanupStaleCsv:
    """cleanup_stale_csv removes CSV files with non-matching mtime."""

    @pytest.mark.asyncio
    async def test_removes_stale_deletes_current_intact(self, tmp_path: Path) -> None:
        from xcelium_mcp.csv_cache import cleanup_stale_csv

        # Create a fake SHM directory with known mtime
        shm_dir = tmp_path / "test.shm"
        shm_dir.mkdir()
        current_mtime = int(os.path.getmtime(str(shm_dir)))
        stale_mtime = current_mtime - 9999

        # Current CSV (should survive)
        current_csv = tmp_path / f"mcp_csv_test_abc12345_{current_mtime}.csv"
        current_csv.write_text("data")

        # Stale CSV (should be deleted)
        stale_csv = tmp_path / f"mcp_csv_test_xyz99999_{stale_mtime}.csv"
        stale_csv.write_text("old data")

        # Non-CSV file (should not be touched)
        other_file = tmp_path / "mcp_registry.json"
        other_file.write_text("{}")

        deleted = await cleanup_stale_csv(str(tmp_path), str(shm_dir))

        assert deleted == 1
        assert not stale_csv.exists()
        assert current_csv.exists()
        assert other_file.exists()

    @pytest.mark.asyncio
    async def test_no_shm_dir_returns_zero(self, tmp_path: Path) -> None:
        from xcelium_mcp.csv_cache import cleanup_stale_csv

        # SHM path that doesn't exist
        result = await cleanup_stale_csv(str(tmp_path), "/nonexistent/test.shm")
        assert result == 0
