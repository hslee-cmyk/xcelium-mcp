"""Checkpoint management tools."""
from __future__ import annotations

import asyncio
import logging
import os

from mcp.server.fastmcp import FastMCP

import xcelium_mcp.checkpoint_manager as checkpoint_manager
from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.discovery import resolve_sim_dir
from xcelium_mcp.shell_utils import get_user_tmp_dir, shell_quote, shell_run

logger = logging.getLogger(__name__)


async def restore_checkpoint_impl(bridges: BridgeManager, name: str, sim_dir: str) -> str:
    """Shared restore logic — callable from other modules (e.g. debug.bisect_restore_and_debug).

    No compile_hash verification — user may intentionally restore from a
    previous compile (e.g. to compare behavior before/after RTL change).
    Use cleanup_checkpoints(mode="stale") to remove outdated checkpoints.
    """
    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = ""
    chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await get_user_tmp_dir()}/checkpoints"

    bridge = bridges.xmsim
    cmd = f"__RESTORE__ {name} {chk_base}" if name else f"__RESTORE__  {chk_base}"
    result = await bridge.execute(cmd, timeout=120.0)
    return result


def register(mcp: FastMCP, bridges: BridgeManager) -> None:

    @mcp.tool()
    async def checkpoint(
        action: str,
        name: str = "",
        sim_dir: str = "",
        saved_time_ns: int = 0,
        mode: str = "stale",
        filter_value: str = "",
        dry_run: bool = True,
        invert: bool = False,
    ) -> str:
        """Manage simulation checkpoints: save, restore, list, or cleanup.

        Args:
            action:        "save" — save a checkpoint.
                           "restore" — restore to a previously saved checkpoint.
                           "list" — list all checkpoints with details.
                           "cleanup" — remove checkpoints by filter criteria.
            name:          Checkpoint name (alphanumeric). Auto-generated if empty.
                           Used by save/restore actions.
            sim_dir:       Simulation directory (auto-detected if empty).
            saved_time_ns: Current simulation time in ns (save action only).
            mode:          Cleanup filter mode (cleanup action only):
                           "stale" (default), "hash", "origin", "pattern",
                           "before", "project", "all", "rebuild".
            filter_value:  Filter parameter for cleanup modes.
            dry_run:       True = report only (cleanup), False = actually delete.
            invert:        True = keep matching, remove rest (cleanup).
        """
        try:
            resolved_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        if action == "save":
            return await _save_impl(bridges, name, resolved_dir, saved_time_ns)
        elif action == "restore":
            return await restore_checkpoint_impl(bridges, name, resolved_dir or "")
        elif action == "list":
            return await _cleanup_impl(
                resolved_dir, mode="list", filter_value="", dry_run=True, invert=False,
            )
        elif action == "cleanup":
            return await _cleanup_impl(
                resolved_dir, mode=mode, filter_value=filter_value,
                dry_run=dry_run, invert=invert,
            )
        else:
            return f"ERROR: Unknown action '{action}'. Use 'save', 'restore', 'list', or 'cleanup'."

    async def _save_impl(
        bridges: BridgeManager,
        name: str,
        resolved_dir: str | None,
        saved_time_ns: int,
    ) -> str:
        bridge = bridges.xmsim

        chk_base = os.path.join(resolved_dir, "checkpoints") if resolved_dir else f"{await get_user_tmp_dir()}/checkpoints"

        cmd = f"__SAVE__ {name} {chk_base}" if name else f"__SAVE__  {chk_base}"
        result = await bridge.execute(cmd)

        # Register in manifest on success
        if "save failed" not in result and resolved_dir:
            actual_name = name
            if not actual_name and "saved:worklib." in result:
                try:
                    actual_name = result.split("saved:worklib.")[1].split(":module")[0]
                except IndexError:
                    pass
            if actual_name:
                await asyncio.to_thread(
                    checkpoint_manager.register_checkpoint,
                    resolved_dir, actual_name, saved_time_ns,
                    origin="bridge",
                )

        return result

    async def _cleanup_impl(
        resolved_dir: str | None,
        mode: str,
        filter_value: str,
        dry_run: bool,
        invert: bool,
    ) -> str:
        if not resolved_dir:
            return "ERROR: Could not determine sim_dir. Pass sim_dir explicitly."

        # Rebuild mode: scan worklib via xmls and recover missing manifest entries
        if mode == "rebuild":
            chk_dir = os.path.join(resolved_dir, "checkpoints", "worklib")
            if not os.path.isdir(chk_dir):
                return f"No checkpoints directory found at {chk_dir}"
            cfg = None
            try:
                from xcelium_mcp.registry import load_sim_config
                cfg = await load_sim_config(resolved_dir)
            except (RuntimeError, ValueError, OSError) as e:
                logger.debug("config load for checkpoint cleanup failed: %s", e)
            xmls_path = "xmls"
            if cfg and "eda_tools" in cfg:
                xrun = cfg["eda_tools"].get("xrun", "")
                if xrun:
                    xmls_path = xrun.replace("/xrun", "/xmls")
            user_tmp = await get_user_tmp_dir()
            cds_lib = f"{user_tmp}/rebuild_cds.lib"
            abs_worklib = os.path.expanduser(chk_dir)
            await shell_run(
                f"echo 'DEFINE worklib {abs_worklib}' > {shell_quote(cds_lib)}",
                timeout=5,
            )
            from xcelium_mcp.shell_utils import login_shell_cmd
            login_shell = "/usr/bin/tcsh"
            if cfg and "runner" in cfg:
                login_shell = cfg["runner"].get("login_shell", login_shell)
            run_dir = os.path.join(resolved_dir, cfg.get("runner", {}).get("run_dir", "run")) if cfg else resolved_dir
            xmls_cmd = f"cd {shell_quote(run_dir)} && {shell_quote(xmls_path)} -snapshot -all -cdslib {shell_quote(cds_lib)} -nolog -nocopyright"
            xmls_out = await shell_run(
                login_shell_cmd(login_shell, xmls_cmd),
                timeout=30,
            )
            result = await asyncio.to_thread(
                checkpoint_manager.rebuild_manifest, resolved_dir, xmls_out
            )
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

        result = await asyncio.to_thread(
            checkpoint_manager.cleanup_checkpoints,
            resolved_dir, mode=mode, filter_value=filter_value, dry_run=dry_run, invert=invert,
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

        # Remove snapshots from worklib via xmrm (only when actually deleting)
        if result["removed"] and not dry_run:
            cfg = None
            try:
                from xcelium_mcp.registry import load_sim_config
                cfg = await load_sim_config(resolved_dir)
            except (RuntimeError, ValueError, OSError) as e:
                logger.debug("config load for xmrm cleanup failed: %s", e)
            xmrm_path = "xmrm"
            login_shell = "/usr/bin/tcsh"
            if cfg and "eda_tools" in cfg:
                xrun = cfg["eda_tools"].get("xrun", "")
                if xrun:
                    xmrm_path = xrun.replace("/xrun", "/xmrm")
            if cfg and "runner" in cfg:
                login_shell = cfg["runner"].get("login_shell", login_shell)

            from xcelium_mcp.shell_utils import login_shell_cmd
            user_tmp = await get_user_tmp_dir()
            cds_lib = f"{user_tmp}/cleanup_cds.lib"
            chk_worklib = os.path.expanduser(os.path.join(resolved_dir, "checkpoints", "worklib"))
            await shell_run(
                f"echo 'DEFINE worklib {chk_worklib}' > {shell_quote(cds_lib)}",
                timeout=5,
            )
            run_dir = os.path.join(resolved_dir, cfg.get("runner", {}).get("run_dir", "run")) if cfg else resolved_dir
            xmrm_errors: list[str] = []
            for name in result["removed"]:
                xmrm_cmd = f"cd {shell_quote(run_dir)} && {shell_quote(xmrm_path)} -snapshot {shell_quote(f'worklib.{name}')} -cdslib {shell_quote(cds_lib)} -nolog -nocopyright -force"
                out = await shell_run(
                    login_shell_cmd(login_shell, xmrm_cmd),
                    timeout=15,
                )
                if "Removing" not in out and out.strip():
                    xmrm_errors.append(f"{name}: {out.strip()}")
            if xmrm_errors:
                lines.append(f"xmrm errors: {'; '.join(xmrm_errors)}")

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
