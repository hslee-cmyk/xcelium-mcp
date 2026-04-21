"""Signal inspection and manipulation tools."""
from __future__ import annotations

import asyncio
import fnmatch
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.shell_utils import find_shm, sanitize_signal_name
from xcelium_mcp.tcl_bridge import TclError

# Verilog/SystemVerilog value literals: 1'b0, 8'hFF, 32'd100, 16'bxxxx, plain digits
_DEPOSIT_VALUE_RE = re.compile(r"^[\d'bhBHdDoOxXzZ_]+$")

# SimVision 'scope show' returns TCL list items in four forms:
#   {{full.path}[idx]}  — scope show on array base returns double-braced elements
#                         (outer {} = TCL list quoting, inner {path}[idx] = SimVision format)
#   {full.path}[idx]    — array element at top-level scope show output
#   {full.path}         — path with special chars, braced
#   full.path           — plain unbraced path
_DOUBLE_BRACED_ARRAY_RE = re.compile(r"^\{\{(.+?)\}(\[\d+(?::\d+)?\])\}$")
_ARRAY_ELEM_RE = re.compile(r"^\{(.+?)\}(\[\d+(?::\d+)?\])$")
_BRACED_PATH_RE = re.compile(r"^\{(.+?)\}$")


def _parse_scope_item(item: str) -> str:
    m = _DOUBLE_BRACED_ARRAY_RE.match(item)
    if m:
        return m.group(1) + m.group(2)
    m = _ARRAY_ELEM_RE.match(item)
    if m:
        return m.group(1) + m.group(2)
    m = _BRACED_PATH_RE.match(item)
    if m:
        return m.group(1)
    return item


async def _list_signals_recursive(
    bridge: Any,
    scope: str,
    pattern: str,
    scope_prefixes: list[str] | None = None,
    depth: int = 0,
    max_depth: int = 5,
) -> list[str]:
    """Walk scope hierarchy via 'scope show' and collect signal names matching pattern.

    scope_prefixes controls which items are recursed into:
      None / ["u_"] — only items whose last component starts with a prefix (fast, project-specific)
      []            — try scope show on every item; non-empty result means it is a sub-scope (general)
    Stops at max_depth to bound the number of bridge round-trips.
    """
    if scope_prefixes is None:
        scope_prefixes = ["u_"]
    try:
        raw = await bridge.execute(f"scope show {{{scope}}}")
    except (TclError, ConnectionError, asyncio.TimeoutError, OSError):
        return []
    results: list[str] = []
    for item in raw.split():
        clean = _parse_scope_item(item)
        if not clean:
            continue
        tail = clean.split(".")[-1].split("[")[0]
        if fnmatch.fnmatch(tail, pattern):
            results.append(clean)
        should_recurse = (
            not scope_prefixes  # [] = general: try all (scope show on signal returns "" → safe)
            or any(tail.startswith(p) for p in scope_prefixes)
        )
        if should_recurse and depth < max_depth:
            sub = await _list_signals_recursive(
                bridge, clean, pattern, scope_prefixes, depth + 1, max_depth
            )
            results.extend(sub)
    return results


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
        recursive: bool = False,
        scope_prefixes: list[str] | None = None,
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
            recursive: If True, "list" walks the sub-hierarchy via scope show. Default False.
            scope_prefixes: Controls which items are recursed into during recursive list.
                     None / ["u_"] — only recurse into items with a matching prefix (fast).
                     []            — try scope show on every item (general, works with any naming).
        """
        # S-1 fix: sanitize all signal/scope inputs to prevent Tcl injection
        try:
            if signal:
                signal = sanitize_signal_name(signal)
            if signals:
                signals = [sanitize_signal_name(s) for s in signals]
            if scope:
                scope = sanitize_signal_name(scope)
        except ValueError as e:
            return f"ERROR: {e}"

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
            if recursive:
                # scope show is a SimVision-only Tcl command; auto-switch if needed
                if bridge is bridges.xmsim_raw:
                    if bridges.simvision_raw and bridges.simvision_raw.connected:
                        bridge = bridges.simvision_raw
                    else:
                        return (
                            "ERROR: recursive list requires SimVision (scope show is unavailable "
                            "in xmsim). Start SimVision first."
                        )
                hits = await _list_signals_recursive(bridge, scope, pattern, scope_prefixes)
                if not hits:
                    return f"No signals matching {pattern!r} found under {scope} (recursive search)"
                return "\n".join(hits)
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
            from xcelium_mcp.discovery import resolve_sim_dir
            from xcelium_mcp.registry import load_sim_config
            from xcelium_mcp.shell_utils import (
                get_user_tmp_dir,
                login_shell_cmd,
                shell_quote,
                shell_run,
            )

            # 1. Resolve SHM path
            if not shm_path:
                try:
                    resolved_dir = await resolve_sim_dir()
                except ValueError as e:
                    return f"ERROR: {e}"
                shm_path = await find_shm(resolved_dir)
                if not shm_path:
                    return "ERROR: No SHM found in dump directory"

            # 2. Build simvisdbutil command (one call for all signals)
            try:
                svdb = await _resolve_simvisdbutil()
            except RuntimeError as e:
                return f"ERROR: {e}"

            sig_args = " ".join(f"-signal {shell_quote(s)}" for s in signals)
            user_tmp = await get_user_tmp_dir()
            dummy_out = f"{user_tmp}/check_dump_dummy.csv"
            cmd = f"{svdb} -csv -missing -nolog -nocopyright {sig_args} -output {dummy_out} -overwrite {shell_quote(shm_path)}"

            # Wrap in login shell for EDA env
            try:
                resolved_dir_cfg = await resolve_sim_dir()
                cfg = await load_sim_config(resolved_dir_cfg)
            except (ValueError, RuntimeError):
                cfg = None
            runner = cfg.get("runner", {}) if cfg else {}
            login_shell = runner.get("login_shell", "/bin/sh")
            shell_cmd = login_shell_cmd(login_shell, cmd)
            result = await shell_run(shell_cmd, timeout=60)

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
            await shell_run(f"rm -f {dummy_out}", timeout=5)

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
        # S-1 fix: sanitize signal name
        try:
            signal = sanitize_signal_name(signal)
        except ValueError as e:
            return f"ERROR: {e}"

        bridge = bridges.xmsim
        if release:
            readback = await bridge.execute(f"__RELEASE_AND_VERIFY__ {signal}")
            return f"Released {signal}. Current value: {readback}"
        else:
            if not value:
                return "ERROR: 'value' is required for deposit (or set release=True)."
            if not _DEPOSIT_VALUE_RE.fullmatch(value):
                return (
                    f"ERROR: Invalid deposit value {value!r}. "
                    "Only Verilog literals allowed (e.g. 1'b1, 8'hFF, 32'd100)."
                )
            readback = await bridge.execute(f"__DEPOSIT_AND_VERIFY__ {signal} {value}")
            return f"Deposited {value} on {signal}. Readback: {readback}"
