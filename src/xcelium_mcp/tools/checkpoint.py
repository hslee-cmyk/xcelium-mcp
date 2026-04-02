"""Checkpoint management tools."""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.sim_runner import get_default_sim_dir, get_user_tmp_dir, ssh_run, sq
import xcelium_mcp.checkpoint_manager as checkpoint_manager


async def restore_checkpoint_impl(bridges: BridgeManager, name: str, sim_dir: str) -> str:
    """Shared restore logic — callable from other modules (e.g. debug.bisect_restore_and_debug).

    No compile_hash verification — user may intentionally restore from a
    previous compile (e.g. to compare behavior before/after RTL change).
    Use cleanup_checkpoints(mode="stale") to remove outdated checkpoints.
    """
    resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
    chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await get_user_tmp_dir()}/checkpoints"

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

        resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
        chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await get_user_tmp_dir()}/checkpoints"

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
                checkpoint_manager.register_checkpoint(
                    resolved_dir, actual_name, saved_time_ns,
                    origin="bridge",
                )

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
        return await restore_checkpoint_impl(bridges, name, sim_dir)

    @mcp.tool()
    async def cleanup_checkpoints(
        sim_dir: str = "",
        mode: str = "stale",
        filter_value: str = "",
        dry_run: bool = True,
    ) -> str:
        """List or remove checkpoints from {sim_dir}/checkpoints/.

        mode:
          "list"    — list all checkpoints with details (no deletion)
          "rebuild" — scan worklib .pak via xmls and rebuild manifest for missing entries
          "stale"   — checkpoints whose compile_hash no longer matches (default)
          "hash"    — checkpoints with compile_hash == filter_value
          "origin"  — checkpoints with origin == filter_value ("regression"/"bridge"/"single")
          "pattern" — checkpoints whose name or test_name contains filter_value
          "before"  — checkpoints saved before filter_value (ISO date, e.g. "2026-04-01")
          "project" — checkpoints whose path contains filter_value
          "all"     — every checkpoint

        dry_run=True (default): report candidates only, no deletion.
        Set dry_run=False to actually remove.

        Args:
            sim_dir:      Simulation directory (auto-detected if empty).
            mode:         Cleanup mode.
            filter_value: Filter parameter for hash/origin/pattern/before/project modes.
            dry_run:      True = report only, False = delete.
        """
        resolved_dir = sim_dir if sim_dir else await get_default_sim_dir()
        if not resolved_dir:
            return "ERROR: Could not determine sim_dir. Pass sim_dir explicitly."

        # Rebuild mode: scan worklib via xmls and recover missing manifest entries
        if mode == "rebuild":
            chk_dir = os.path.join(resolved_dir, "checkpoints", "worklib")
            if not os.path.isdir(chk_dir):
                return f"No checkpoints directory found at {chk_dir}"
            # Resolve xmls path from registry
            cfg = None
            try:
                from xcelium_mcp.registry import load_sim_config
                cfg = await load_sim_config(resolved_dir)
            except Exception:
                pass
            xmls_path = "xmls"
            if cfg and "eda_tools" in cfg:
                # xmls is in the same bin dir as xrun
                xrun = cfg["eda_tools"].get("xrun", "")
                if xrun:
                    xmls_path = xrun.replace("/xrun", "/xmls")
            # Create temp cds.lib pointing to checkpoints worklib
            user_tmp = await get_user_tmp_dir()
            cds_lib = f"{user_tmp}/rebuild_cds.lib"
            abs_worklib = os.path.abspath(chk_dir)
            await ssh_run(
                f"echo 'DEFINE worklib {abs_worklib}' > {sq(cds_lib)}",
                timeout=5,
            )
            # Run xmls
            xmls_out = await ssh_run(
                f"{sq(xmls_path)} -snapshot -all -cdslib {sq(cds_lib)} -nolog -nocopyright",
                timeout=30,
            )
            result = checkpoint_manager.rebuild_manifest(resolved_dir, xmls_out)
            lines = [
                f"sim_dir: {resolved_dir}",
                f"Scanned worklib: {abs_worklib}",
                f"Total snapshots found: {result['total']}",
            ]
            if result["added"]:
                lines.append(f"\nRecovered ({len(result['added'])}):")
                for n in result["added"]:
                    lines.append(f"  + {n}")
            if result["existing"]:
                lines.append(f"\nAlready in manifest ({len(result['existing'])}):")
                for n in result["existing"]:
                    lines.append(f"  = {n}")
            if not result["added"] and not result["existing"]:
                lines.append("No snapshots found in worklib.")
            return "\n".join(lines)

        result = checkpoint_manager.cleanup_checkpoints(
            resolved_dir, mode=mode, filter_value=filter_value, dry_run=dry_run
        )

        lines = [
            f"sim_dir: {result['sim_dir']}",
            f"mode: {result['mode']}  dry_run: {result['dry_run']}",
            f"compile_hash (current): {result['current_hash']}",
        ]
        if result["filter_value"]:
            lines.append(f"filter: {result['filter_value']}")
        lines.append("")

        # Show details in list mode
        if mode == "list" and result["details"]:
            for d in result["details"]:
                lines.append(
                    f"  {d['name']}\n"
                    f"    hash: {d['compile_hash']}  origin: {d['origin']}  "
                    f"saved_at: {d['saved_at']}  sim_time: {d['saved_time_ns']}ns"
                )
            lines.append("")

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


# Backward-compat alias
_restore_checkpoint_impl = restore_checkpoint_impl
