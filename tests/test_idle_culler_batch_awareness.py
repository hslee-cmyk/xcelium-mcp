"""F-E (session-state-reattach): idle_culler must not cull workers while a
batch/regression job is still running (they hold no TCP bridge — see
plan.md §1.4 for why has_established_tcp() alone misjudges them as idle).

Design ref: docs/02-design/features/xcelium-mcp-session-state-reattach.design.md §5.3
Uses real tmp_path fixtures (not /proc) — has_live_batch_job() takes an
explicit user_tmp_dir so this is fully platform-independent (Windows included).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from xcelium_mcp.idle_culler import STALE_JOB_FILE_SEC, has_live_batch_job, main


def _write_job_file(dir_: Path, name: str, pid: int, mtime: float | None = None) -> Path:
    path = dir_ / name
    path.write_text(json.dumps({"pid": pid, "test_name": "TOP015"}))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestHasLiveBatchJob:
    def test_alive_pid_in_batch_job_file(self, tmp_path: Path) -> None:
        """T-5: a live PID (this test process itself) in batch_job.json → True."""
        _write_job_file(tmp_path, "batch_job.json", pid=os.getpid())
        assert has_live_batch_job(tmp_path) is True

    def test_alive_pid_in_regression_job_file(self, tmp_path: Path) -> None:
        """T-5 variant: regression_job.json is checked too."""
        _write_job_file(tmp_path, "regression_job.json", pid=os.getpid())
        assert has_live_batch_job(tmp_path) is True

    def test_stale_job_file_ignored(self, tmp_path: Path) -> None:
        """T-6: mtime far older than STALE_JOB_FILE_SEC → treated as
        abandoned/corrupt, must not block idle-culling forever even though
        the pid field happens to still be alive."""
        old_mtime = time.time() - STALE_JOB_FILE_SEC - 3600
        _write_job_file(tmp_path, "batch_job.json", pid=os.getpid(), mtime=old_mtime)
        assert has_live_batch_job(tmp_path) is False

    def test_no_job_files_at_all(self, tmp_path: Path) -> None:
        """T-7: nothing present → False, no crash."""
        assert has_live_batch_job(tmp_path) is False

    def test_corrupt_json_ignored(self, tmp_path: Path) -> None:
        """T-7: corrupt job file → False, no crash (caller shouldn't have to
        care whether batch_runner.py's write was interrupted mid-write)."""
        (tmp_path / "batch_job.json").write_text("{not valid json")
        assert has_live_batch_job(tmp_path) is False

    def test_dead_pid_in_job_file(self, tmp_path: Path) -> None:
        """A job file whose simulation already exited → False (this is the
        'completed while disconnected' case from batch_runner.py — nothing
        left to protect)."""
        # pid 1 belongs to init and this test doesn't own it — but to keep
        # the test hermetic (no assumption about pid 1's actual owner), use
        # a pid guaranteed not to exist instead: the largest possible pid + 1
        # is not portable, so just pick something exceedingly unlikely to be
        # alive alongside a permission-safe expectation via os.kill semantics
        # is out of scope here — has_live_batch_job()'s _pid_alive() already
        # has dedicated coverage via ProcessLookupError in idle_culler's own
        # ProcessLookupError branch; this test only needs *some* dead pid.
        dead_pid = 999999
        _write_job_file(tmp_path, "batch_job.json", pid=dead_pid)
        assert has_live_batch_job(tmp_path) is False


class TestMainSkipsWhenBatchJobLive:
    def test_main_returns_early_without_touching_supervisor(self) -> None:
        """T-8: when a live batch job exists, main() must not even look for
        the supervisor/workers — the whole round is skipped conservatively."""
        with patch("xcelium_mcp.idle_culler.sys") as mock_sys, \
             patch("xcelium_mcp.idle_culler.has_live_batch_job", return_value=True), \
             patch("xcelium_mcp.idle_culler.find_supervisor_pid") as mock_find_supervisor:
            mock_sys.platform = "linux"
            result = main()

        assert result == 0
        mock_find_supervisor.assert_not_called()

    def test_main_proceeds_normally_when_no_batch_job(self) -> None:
        """Regression: existing idle-culling logic (§4.2, parent feature)
        still runs unchanged when there's no live batch job."""
        with patch("xcelium_mcp.idle_culler.sys") as mock_sys, \
             patch("xcelium_mcp.idle_culler.has_live_batch_job", return_value=False), \
             patch("xcelium_mcp.idle_culler.find_supervisor_pid", return_value=None) as mock_find_supervisor:
            mock_sys.platform = "linux"
            result = main()

        assert result == 0
        mock_find_supervisor.assert_called_once()
