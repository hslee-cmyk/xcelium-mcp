"""Signal inspection and manipulation tools."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.tcl_bridge import TclError


def register(mcp: FastMCP, bridges: BridgeManager) -> None:
    """Register signal inspection tools."""

    @mcp.tool()
    async def inspect_signal(
        action: str,
        signal: str = "",
        signals: list[str] | None = None,
        scope: str = "",
        pattern: str = "*",
        target: str = "auto",
        shm_path: str = "",
    ) -> str:
        """Read signal values, describe metadata, list signals, find drivers, or check SHM dump.

        Args:
            action:  "value" — read current values of one or more signals.
                     "describe" — detailed info (type, width, direction) for a signal.
                     "list" — list signals in a scope, filtered by pattern.
                     "drivers" — find all drivers of a signal (useful for X/Z debugging).
                     "check_dump" — check which signals exist in an SHM dump file.
            signal:  Full hierarchical signal path. Required for describe/drivers. Also usable for value (single).
            signals: List of signal paths for "value" or "check_dump" action.
            scope:   Hierarchical scope path (e.g. "top.hw.u_ext"). Required for "list".
            pattern: Glob pattern for "list" action (default "*").
            target:  "xmsim" | "simvision" | "auto" (default: auto). Used by "list".
            shm_path: SHM dump path for "check_dump". Empty = auto-detect latest.
        """
        if action == "value":
            if not signals:
                if signal:
                    signals = [signal]
                else:
                    return "ERROR: 'signals' or 'signal' is required for action='value'."
            bridge = bridges.xmsim
            results: list[str] = []
            for sig in signals:
                try:
                    val = await bridge.execute(f"value {sig}")
                    results.append(f"{sig} = {val}")
                except TclError as e:
                    results.append(f"{sig} = ERROR: {e}")
            return "\n".join(results)

        elif action == "describe":
            if not signal:
                return "ERROR: 'signal' is required for action='describe'."
            bridge = bridges.xmsim
            return await bridge.execute(f"describe {signal}")

        elif action == "list":
            if not scope:
                return "ERROR: 'scope' is required for action='list'."
            bridge = bridges.get_bridge(target)
            return await bridge.execute(f"describe {scope}.{pattern}")

        elif action == "drivers":
            if not signal:
                return "ERROR: 'signal' is required for action='drivers'."
            bridge = bridges.xmsim
            return await bridge.execute(f"drivers {signal}")

        elif action == "check_dump":
            if not signals:
                return "ERROR: 'signals' list is required for action='check_dump'."

            from xcelium_mcp.csv_cache import _resolve_simvisdbutil
            from xcelium_mcp.sim_runner import (
                ssh_run, sq, login_shell_cmd, resolve_sim_dir, get_user_tmp_dir,
            )
            from xcelium_mcp.registry import load_sim_config

            # 1. Resolve SHM path
            if not shm_path:
                try:
                    resolved_dir = await resolve_sim_dir()
                except ValueError as e:
                    return f"ERROR: {e}"
                dump_dir = f"{resolved_dir}/dump"
                r = await ssh_run(f"ls -td {dump_dir}/*.shm 2>/dev/null | head -1")
                shm_path = r.strip()
                if not shm_path:
                    return "ERROR: No SHM found in dump directory"

            # 2. Build simvisdbutil command (one call for all signals)
            try:
                svdb = await _resolve_simvisdbutil()
            except RuntimeError as e:
                return f"ERROR: {e}"

            sig_args = " ".join(f"-signal {sq(s)}" for s in signals)
            user_tmp = await get_user_tmp_dir()
            dummy_out = f"{user_tmp}/check_dump_dummy.csv"
            cmd = f"{svdb} -csv -missing -nolog -nocopyright {sig_args} -output {dummy_out} -overwrite {sq(shm_path)}"

            # Wrap in login shell for EDA env
            try:
                resolved_dir_cfg = await resolve_sim_dir()
                cfg = await load_sim_config(resolved_dir_cfg)
            except (ValueError, RuntimeError):
                cfg = None
            runner = cfg.get("runner", {}) if cfg else {}
            login_shell = runner.get("login_shell", "/bin/sh")
            shell_cmd = login_shell_cmd(login_shell, cmd)
            result = await ssh_run(shell_cmd, timeout=60)

            # 3. Parse "Ignoring missing" lines
            missing = set()
            for line in result.splitlines():
                if "Ignoring missing" in line:
                    # "Ignoring missing or misspelled signal: <name>"
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        missing.add(parts[1].strip())

            found = [s for s in signals if s not in missing]
            missing_list = [s for s in signals if s in missing]

            # 4. Cleanup dummy file
            await ssh_run(f"rm -f {dummy_out}", timeout=5)

            # 5. Format output
            lines = [
                f"SHM: {shm_path}",
                f"Total: {len(signals)} | Found: {len(found)} | Missing: {len(missing_list)}",
            ]
            if found:
                lines.append(f"\nFound ({len(found)}):")
                for s in found:
                    lines.append(f"  + {s}")
            if missing_list:
                lines.append(f"\nMissing ({len(missing_list)}):")
                for s in missing_list:
                    lines.append(f"  - {s}")
            return "\n".join(lines)

        else:
            return f"ERROR: Unknown action '{action}'. Use 'value', 'describe', 'list', 'drivers', or 'check_dump'."

    @mcp.tool()
    async def deposit_signal(
        signal: str,
        value: str = "",
        release: bool = False,
    ) -> str:
        """Force-deposit a value onto a signal, or release to restore driven value.

        Args:
            signal:  Full hierarchical signal path.
            value:   Value to deposit (e.g. "1'b1", "8'hFF"). Required unless release=True.
            release: True = release the signal instead of depositing.
        """
        bridge = bridges.xmsim
        if release:
            readback = await bridge.execute(f"__RELEASE_AND_VERIFY__ {signal}")
            return f"Released {signal}. Current value: {readback}"
        else:
            if not value:
                return "ERROR: 'value' is required for deposit (or set release=True)."
            readback = await bridge.execute(f"__DEPOSIT_AND_VERIFY__ {signal} {value}")
            return f"Deposited {value} on {signal}. Readback: {readback}"
