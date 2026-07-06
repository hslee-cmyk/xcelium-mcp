"""Tests for v5.2 async wiring: _update_dump_history, _lazy_discover_boundaries,
and dump_stats aggregation in run_batch_regression.

F-141: these functions do config/SSH I/O and were previously untested — the
"Coverage gap" section of docs/03-analysis/xcelium-mcp-v5.2-hierarchical-dump.analysis.md
notes this is exactly where Gap #1-#3 (fixed by F-140) escaped the pure-function
test suite in tests/test_hierarchical_dump.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.batch_runner import (
    _lazy_discover_boundaries,
    _update_dump_history,
    run_batch_regression,
)


def _make_runner(**overrides) -> dict:
    """Minimal runner config dict (mirrors tests/test_batch_helpers.py)."""
    base = {
        "script": "run_sim.sh",
        "login_shell": "/bin/tcsh",
        "script_shell": True,
        "args_format": "-test {test_name} --",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: _update_dump_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_dump_history_writes_last_dump_summary_schema() -> None:
    """Persisted schema uses last_dump_summary/updated_at, strips scope_overrides (F-140)."""
    saved_config: dict = {}

    async def fake_save(sim_dir: str, config: dict) -> None:
        saved_config.update(config)

    dump_summary = {
        "dump_depth": "boundary",
        "sim_mode": "rtl",
        "total_signals": 28,
        "scope_overrides": {"top.u_blk_a": "boundary"},
    }

    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value={}),
        patch("xcelium_mcp.batch_runner.save_sim_config", side_effect=fake_save),
    ):
        await _update_dump_history("/sim", "T1", dump_summary, {"top.u_blk_a": "boundary"})

    entry = saved_config["dump_history"]["T1"]
    assert "dump_summary" not in entry, "old key name must not be written"
    assert entry["last_dump_summary"] == {
        "dump_depth": "boundary",
        "sim_mode": "rtl",
        "total_signals": 28,
    }
    assert "scope_overrides" not in entry["last_dump_summary"]
    assert entry["dump_scopes"] == {"top.u_blk_a": "boundary"}
    assert "updated_at" in entry
    # ISO timestamp, seconds precision — must be parseable
    from datetime import datetime
    datetime.fromisoformat(entry["updated_at"])


@pytest.mark.asyncio
async def test_update_dump_history_loads_config_with_force() -> None:
    """load_sim_config is called with force=True (F-143) — bypass stale cache before writing history."""
    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value={}) as mock_load,
        patch("xcelium_mcp.batch_runner.save_sim_config", new_callable=AsyncMock),
    ):
        await _update_dump_history("/sim", "T1", {"total_signals": 5}, None)

    mock_load.assert_called_once_with("/sim", force=True)


@pytest.mark.asyncio
async def test_update_dump_history_defaults_dump_scopes_to_empty_dict() -> None:
    """dump_scopes=None persists as {} (existing read path expects a dict)."""
    saved_config: dict = {}

    async def fake_save(sim_dir: str, config: dict) -> None:
        saved_config.update(config)

    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value={}),
        patch("xcelium_mcp.batch_runner.save_sim_config", side_effect=fake_save),
    ):
        await _update_dump_history("/sim", "T1", {"total_signals": 5}, None)

    assert saved_config["dump_history"]["T1"]["dump_scopes"] == {}


# ---------------------------------------------------------------------------
# Tests: _lazy_discover_boundaries
# ---------------------------------------------------------------------------


def _write_netlist(tmp_path, modules: dict, rel_name: str = "netlist.json"):
    p = tmp_path / rel_name
    p.write_text(json.dumps({"modules": modules}), encoding="utf-8")
    return rel_name


@pytest.mark.asyncio
async def test_lazy_discover_boundaries_returns_parsed_boundaries(tmp_path) -> None:
    rel = _write_netlist(tmp_path, {
        "top": {"ports": {}, "cells": {"u_blk_a": {"type": "blk_a"}}},
        "blk_a": {"ports": {"i_a": {"direction": "input"}}, "cells": {}},
    })
    config = {"netlist_info": {"rtl": {"boundary_json": rel}}, "top_module": "top"}

    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value=config),
        patch("xcelium_mcp.batch_runner.save_sim_config", new_callable=AsyncMock) as mock_save,
    ):
        result = await _lazy_discover_boundaries(str(tmp_path), {"boundary_depth": 3}, "rtl")

    assert result is not None
    assert "top.u_blk_a" in result
    assert "top.u_blk_a.i_a" in result["top.u_blk_a"]
    mock_save.assert_not_called()  # write_discovered_boundaries not set


@pytest.mark.asyncio
async def test_lazy_discover_boundaries_persists_when_flagged(tmp_path) -> None:
    rel = _write_netlist(tmp_path, {
        "top": {"ports": {}, "cells": {"u_blk_a": {"type": "blk_a"}}},
        "blk_a": {"ports": {"i_a": {"direction": "input"}}, "cells": {}},
    })
    config = {"netlist_info": {"rtl": {"boundary_json": rel}}, "top_module": "top"}
    saved: dict = {}

    async def fake_save(sim_dir: str, cfg: dict) -> None:
        saved.update(cfg)

    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value=config),
        patch("xcelium_mcp.batch_runner.save_sim_config", side_effect=fake_save),
    ):
        result = await _lazy_discover_boundaries(
            str(tmp_path), {"boundary_depth": 3, "write_discovered_boundaries": True}, "rtl"
        )

    assert result is not None
    assert saved["dump_strategy"]["rtl"]["block_boundaries"] == result


@pytest.mark.asyncio
async def test_lazy_discover_boundaries_returns_none_without_netlist_info(tmp_path) -> None:
    """No netlist_info.{mode}.boundary_json in config → None, no exception."""
    with (
        patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value={}),
        patch("xcelium_mcp.batch_runner.save_sim_config", new_callable=AsyncMock) as mock_save,
    ):
        result = await _lazy_discover_boundaries(str(tmp_path), {}, "rtl")

    assert result is None
    mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_discover_boundaries_missing_json_file_returns_none(tmp_path) -> None:
    """boundary_json path configured but file does not exist on disk → None."""
    config = {"netlist_info": {"rtl": {"boundary_json": "does_not_exist.json"}}, "top_module": "top"}
    with patch("xcelium_mcp.batch_runner.load_sim_config", new_callable=AsyncMock, return_value=config):
        result = await _lazy_discover_boundaries(str(tmp_path), {}, "rtl")

    assert result is None


# ---------------------------------------------------------------------------
# Tests: run_batch_regression — dump_history sync + dump_stats shape (F-140/F-141)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regression_updates_dump_history_and_dump_stats_shape() -> None:
    """3-test regression: dump_history written per test, dump_stats matches design.md §8.

    Per-test totals T1=10, T2=5, T3=50 — avg=21.67, avg*2=43.33, so only T3
    triggers a named suggestion; max/min must be {test,total} dicts (not bare ints).
    """
    runner = _make_runner()
    totals = {"T1": 10, "T2": 5, "T3": 50}

    async def fake_preprocess(sim_dir, runner_arg, test_name, sim_mode, **kwargs):
        summary = {
            "dump_depth": "boundary",
            "sim_mode": sim_mode,
            "top_boundary_count": 2,
            "block_boundaries": {"top.u_blk_a": 2},
            "scope_overrides": {},
            "total_signals": totals[test_name],
        }
        return None, summary

    history_calls: list[tuple] = []

    async def fake_update_history(sim_dir, test_name, dump_summary, dump_scopes):
        history_calls.append((sim_dir, test_name, dump_summary, dump_scopes))

    with (
        patch("xcelium_mcp.batch_runner.get_user_tmp_dir", new_callable=AsyncMock,
              return_value="/tmp/mcp_test"),
        patch("xcelium_mcp.batch_runner.shell_run", new_callable=AsyncMock, return_value=""),
        patch("xcelium_mcp.batch_runner.shell_run_fire_and_forget", new_callable=AsyncMock),
        patch("xcelium_mcp.batch_runner.poll_batch_log", new_callable=AsyncMock,
              return_value=("", False)),
        patch("xcelium_mcp.batch_runner._preprocess_setup_tcl", side_effect=fake_preprocess),
        patch("xcelium_mcp.batch_runner._update_dump_history", side_effect=fake_update_history),
    ):
        log_str, dump_stats, _tb_provenance = await run_batch_regression(
            sim_dir="/sim",
            test_list=["T1", "T2", "T3"],
            runner=runner,
        )

    # dump_history must be synced for every test in the regression loop (Gap #3)
    assert [c[1] for c in history_calls] == ["T1", "T2", "T3"]
    assert history_calls[2][2]["total_signals"] == 50

    # dump_stats shape must match design.md §8 (Gap #2)
    assert dump_stats is not None
    assert dump_stats["per_test"]["T1"] == {"total": 10, "top_boundary": 2, "block_count": 1}
    assert dump_stats["per_test"]["T2"] == {"total": 5, "top_boundary": 2, "block_count": 1}
    assert dump_stats["per_test"]["T3"] == {"total": 50, "top_boundary": 2, "block_count": 1}
    assert dump_stats["max"] == {"test": "T3", "total": 50}
    assert dump_stats["min"] == {"test": "T2", "total": 5}
    assert "max_total_signals" not in dump_stats  # old bare-int key must be gone
    assert "min_total_signals" not in dump_stats
    assert len(dump_stats["suggestions"]) == 1
    assert "T3" in dump_stats["suggestions"][0]
