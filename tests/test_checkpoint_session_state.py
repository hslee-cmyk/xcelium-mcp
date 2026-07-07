"""T-3 (session-state-reattach): checkpoint(action=save) records the TB
provenance restored onto bridges by F-D's connect_simulator reattach path —
end-to-end proof that a worker restart doesn't lose provenance anymore.

Design ref: docs/02-design/features/xcelium-mcp-session-state-reattach.design.md §7
checkpoint.py itself is unmodified (§5) — this test only proves it reads the
now-correctly-populated bridges.current_test_name/current_tb_source fields.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _MockMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


@pytest.mark.asyncio
async def test_checkpoint_save_after_reconnect_records_restored_tb_provenance() -> None:
    """Simulates: sim_bridge_run on workerA set test_name/tb_source → workerA
    died → workerB reconnected via F-C+F-D (test_connect_simulator_sim_dir_
    restores_session_state already proves this restore step) → checkpoint(save)
    on workerB must record the *restored* test_name/tb_source, not empty ones."""
    from xcelium_mcp.tools.checkpoint import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    # Simulates the state left behind by connect_simulator's F-D restore path.
    mock_bridges.current_test_name = "TOP015"
    mock_bridges.current_tb_source = {
        "files": [{"path": "/proj/tb/top015.sv", "sha256": "abc"}],
        "combined_sha256": "def",
    }
    mock_bridges.xmsim.execute = AsyncMock(
        return_value="saved:worklib.TOP015_snap1:module:top ok"
    )

    with patch("xcelium_mcp.tools.checkpoint.resolve_sim_dir",
               new_callable=AsyncMock, return_value="/proj/sim"), \
         patch("xcelium_mcp.checkpoint_manager.register_checkpoint") as mock_register:
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["checkpoint"](action="save", name="", sim_dir="/proj/sim")

    assert mock_register.called, "register_checkpoint was never called"
    _, kwargs = mock_register.call_args
    assert kwargs["test_name"] == "TOP015"
    assert kwargs["tb_source"] == mock_bridges.current_tb_source


@pytest.mark.asyncio
async def test_checkpoint_save_without_reconnect_records_empty_provenance_baseline() -> None:
    """Baseline/regression guard: a fresh BridgeManager that never had F-D
    restore applied (e.g. sim_dir with no registry entry, T-4) still behaves
    exactly as before F-D — empty test_name, None tb_source, no crash."""
    from xcelium_mcp.tools.checkpoint import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.current_test_name = ""
    mock_bridges.current_tb_source = None
    mock_bridges.xmsim.execute = AsyncMock(
        return_value="saved:worklib.TOP015_snap1:module:top ok"
    )

    with patch("xcelium_mcp.tools.checkpoint.resolve_sim_dir",
               new_callable=AsyncMock, return_value="/proj/sim"), \
         patch("xcelium_mcp.checkpoint_manager.register_checkpoint") as mock_register:
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["checkpoint"](action="save", name="", sim_dir="/proj/sim")

    _, kwargs = mock_register.call_args
    assert kwargs["test_name"] == ""
    assert kwargs["tb_source"] is None
