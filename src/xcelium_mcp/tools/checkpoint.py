"""Checkpoint management tools."""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.sim_runner import _get_default_sim_dir, _get_user_tmp_dir
import xcelium_mcp.checkpoint_manager as checkpoint_manager


async def _restore_checkpoint_impl(bridges: BridgeManager, name: str, sim_dir: str) -> str:
    """Shared restore logic — callable from other modules (e.g. debug.bisect_restore_and_debug)."""
    resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
    chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await _get_user_tmp_dir()}/checkpoints"

    # compile_hash verification
    if resolved_dir and name:
        valid, reason = checkpoint_manager.verify_checkpoint(resolved_dir, name)
        if not valid:
            stale = checkpoint_manager.invalidate_stale_checkpoints(
                resolved_dir, reason="hash mismatch on restore"
            )
            msg = (
                f"ERROR: {reason}\n"
                f"Stale checkpoints removed: {stale}\n"
                f"Re-run sim_batch_run to create new checkpoints."
            )
            return msg

    bridge = bridges.xmsim
    cmd = f"__RESTORE__ {name} {chk_base}" if name else f"__RESTORE__  {chk_base}"
    result = await bridge.execute(cmd, timeout=120.0)
    return result


def register(mcp: FastMCP, bridges: BridgeManager) -> None:

    @mcp.tool()
    async def save_checkpoint(
        name: str = "",
        sim_dir: str = "",
        saved_time_ns: int = 0,
    ) -> str:
        """Save a simulation checkpoint to persistent storage.

        Checkpoints are saved to {sim_dir}/checkpoints/ and registered in the
        manifest with a compile_hash for automatic invalidation on recompile.
        Use restore_checkpoint to return to this state without re-simulating.

        Args:
            name:          Checkpoint name (alphanumeric, e.g. "L1_common_init").
                           Auto-generated from timestamp if empty.
            sim_dir:       Simulation directory (auto-detected if empty).
            saved_time_ns: Current simulation time in ns for nearest-checkpoint lookup.
        """
        bridge = bridges.xmsim

        resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await _get_user_tmp_dir()}/checkpoints"

        cmd = f"__SAVE__ {name} {chk_base}" if name else f"__SAVE__  {chk_base}"
        result = await bridge.execute(cmd)

        # Register in manifest on success
        if "save failed" not in result and resolved_dir:
            # Extract actual name from response "saved:worklib.{name}:module|dir:..."
            actual_name = name
            if not actual_name and "saved:worklib." in result:
                try:
                    actual_name = result.split("saved:worklib.")[1].split(":module")[0]
                except IndexError:
                    pass
            if actual_name:
                checkpoint_manager.register_checkpoint(resolved_dir, actual_name, saved_time_ns)

        return result

    @mcp.tool()
    async def restore_checkpoint(
        name: str = "",
        sim_dir: str = "",
    ) -> str:
        """Restore simulation to a previously saved checkpoint.

        Verifies compile_hash before restore — rejects stale checkpoints created
        before the last RTL recompile.  Stale breakpoints are cleared automatically
        after restore to prevent spurious $finish.

        Args:
            name:    Checkpoint name to restore. Empty = last saved checkpoint.
            sim_dir: Simulation directory (auto-detected if empty).
        """
        return await _restore_checkpoint_impl(bridges, name, sim_dir)

    @mcp.tool()
    async def cleanup_checkpoints(
        sim_dir: str = "",
        mode: str = "stale",
        project_filter: str = "",
        dry_run: bool = True,
    ) -> str:
        """List or remove checkpoints from {sim_dir}/checkpoints/.

        mode:
          "list"    — list all checkpoints (no deletion)
          "stale"   — checkpoints whose compile_hash no longer matches (default)
          "project" — checkpoints whose path contains project_filter
          "all"     — every checkpoint

        dry_run=True (default): report candidates only, no deletion.
        Set dry_run=False to actually remove.

        Args:
            sim_dir:        Simulation directory (auto-detected if empty).
            mode:           Cleanup mode (list/stale/project/all).
            project_filter: Path substring for "project" mode.
            dry_run:        True = report only, False = delete.
        """
        resolved_dir = sim_dir if sim_dir else await _get_default_sim_dir()
        if not resolved_dir:
            return "ERROR: Could not determine sim_dir. Pass sim_dir explicitly."

        result = checkpoint_manager.cleanup_checkpoints(
            resolved_dir, mode=mode, project_filter=project_filter, dry_run=dry_run
        )

        lines = [
            f"sim_dir: {result['sim_dir']}",
            f"mode: {result['mode']}  dry_run: {result['dry_run']}",
            f"compile_hash (current): {result['current_hash']}",
            "",
        ]
        if result["removed"]:
            verb = "Would remove" if dry_run else "Removed"
            lines.append(f"{verb} ({len(result['removed'])}):")
            for n in result["removed"]:
                lines.append(f"  - {n}")
        else:
            lines.append("No checkpoints to remove.")
        if result["kept"]:
            lines.append(f"Kept ({len(result['kept'])}):")
            for n in result["kept"]:
                lines.append(f"  - {n}")
        return "\n".join(lines)
