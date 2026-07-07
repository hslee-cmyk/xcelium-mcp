"""Simulation lifecycle management tools."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_lifecycle import _get_pid_for_port, start_bridge_simulation
from xcelium_mcp.bridge_manager import BridgeManager, scan_ready_files
from xcelium_mcp.discovery import run_full_discovery
from xcelium_mcp.registry import (
    config_action,
    get_bridge_port,
    get_session_state,
    load_sim_config,
    resolve_sim_dir,
    save_sim_config,
    update_session_state,
)
from xcelium_mcp.shell_utils import UserInputRequired, get_user_tmp_dir, shell_run
from xcelium_mcp.tb_provenance import build_tb_provenance, format_tb_provenance, scan_test_dependencies
from xcelium_mcp.tcl_bridge import BRIDGE_ERRORS, TclBridge, TclError
from xcelium_mcp.test_resolution import parse_test_discovery_output, resolve_test_name

# ---------------------------------------------------------------------------
# Module-level helpers (don't need bridges closure)
# ---------------------------------------------------------------------------

async def _find_ready_file(target: str) -> tuple[int, str]:
    """Find ready file matching target type."""
    entries = await scan_ready_files(target=target)
    if entries:
        return entries[0]
    return 0, target


async def _read_bridge_type(port: int) -> str:
    """Read bridge type from ready file for given port."""
    user_tmp = await get_user_tmp_dir()
    r = await shell_run(f"cat {user_tmp}/bridge_ready_{port} || true")
    parts = r.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return "xmsim"


# ---------------------------------------------------------------------------
# Helper that needs bridges parameter explicitly
# ---------------------------------------------------------------------------

async def _auto_connect_all(bridges: BridgeManager, host: str, timeout: float) -> str:
    """Scan all ready files, connect to each, assign to appropriate slot.

    F-C (attach 모호성 해소): if more than one ready file exists for the same
    bridge type (e.g. two xmsim instances from two concurrently-debugged
    sim_dirs), connecting to both would silently overwrite bridges.xmsim with
    whichever one connects last. Flag that type as ambiguous instead and ask
    the caller to pass sim_dir (or an explicit port) — see connect_simulator.
    """
    entries = await scan_ready_files()
    if not entries:
        return "No bridges found. Run sim_bridge_run or simvision_start first."

    by_type: dict[str, list[int]] = {}
    for p, btype in entries:
        by_type.setdefault(btype, []).append(p)

    results = []
    for btype, ports in by_type.items():
        if len(ports) > 1:
            results.append(
                f"  {btype}: AMBIGUOUS — {len(ports)} bridges detected on ports {ports}. "
                f"Specify sim_dir (or an explicit port) to disambiguate."
            )
            continue
        p = ports[0]
        bridge = TclBridge(host=host, port=p, timeout=timeout)
        try:
            ping = await bridge.connect()
            if btype == "simvision":
                bridges.set_simvision(bridge)
            else:
                bridges.set_xmsim(bridge)
                bridges.xmsim_pid = await _get_pid_for_port(p)
            results.append(f"  {btype}:{p} (ping={ping})")
        except BRIDGE_ERRORS as e:
            results.append(f"  {btype}:{p} FAILED ({e})")

    if not results:
        return "No bridges found."
    return "Connected:\n" + "\n".join(results)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# fullmatch required — changing to match/search opens Tcl injection
# re.ASCII ensures [0-9] blocks Unicode digits (e.g. '１００ns')
# Unit is mandatory — bare integers are ambiguous (Xcelium uses default timescale)
# Optional decimal fraction (F-146) — _duration_to_ns() already float()-converts,
# only this gate rejected "100.5ns" before it ever got there.
_DURATION_RE = re.compile(r'^[0-9]+(?:\.[0-9]+)?\s*(ns|us|ms|s|ps|fs)$', re.IGNORECASE | re.ASCII)
_DURATION_MAX_LEN = 32
_UNIT_TO_NS: dict[str, float] = {
    "fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9,
}


def _duration_to_ns(duration: str) -> int:
    """Convert a validated duration string (e.g. '10ms', '100us') to integer nanoseconds."""
    d = duration.strip().lower()
    for unit in sorted(_UNIT_TO_NS, key=len, reverse=True):
        if d.endswith(unit):
            return int(float(d[: -len(unit)]) * _UNIT_TO_NS[unit])
    raise ValueError(f"Cannot parse duration: {duration!r}")


def _parse_chunked_run_report(raw: str) -> str:
    """Convert CHUNKED_RUN_REPORT text from Tcl into a human-readable string."""
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line and not line.startswith("CHUNKED_RUN_REPORT"):
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()
    status = fields.get("status", "completed")
    sim_time = fields.get("sim_time", "(unknown)")
    requested = fields.get("requested", "")
    error = fields.get("error", "")
    if status == "stopped":
        return (
            f"Simulation stopped by user. "
            f"Current position: {sim_time} (requested: {requested})"
        )
    if status == "error":
        return f"ERROR: sim_run failed: {error}\nPosition: {sim_time}"
    return f"Simulation advanced. Current position: {sim_time}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register(mcp: FastMCP, bridges: BridgeManager) -> dict:
    """Register simulation lifecycle tools (12 tools)."""

    @mcp.tool()
    async def list_tests(sim_dir: str = "", pattern: str = "") -> str:
        """List available test names using test_discovery.command from registry.

        Args:
            sim_dir: Simulation directory. Empty = registry default.
            pattern: Filter pattern. Empty = all tests.
        """
        try:
            resolved_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        config = await load_sim_config(resolved_dir)
        if not config:
            return "ERROR: No config. Run sim_discover first."

        discovery = config.get("test_discovery", {})
        cached = discovery.get("cached_tests", [])

        if not cached:
            cmd = discovery.get("command", "")
            if not cmd:
                return "ERROR: test_discovery.command not configured.\nSet via: mcp_config set test_discovery.command '<command>'"
            r = await shell_run(f"cd {resolved_dir} && {cmd}", timeout=30)
            tb_type = discovery.get("tb_type", "")
            if tb_type:
                cached_test_files = parse_test_discovery_output(r, tb_type)
                cached = sorted(cached_test_files.keys())
            else:
                # Pre-F-175 config: no tb_type means `cmd` is the OLD
                # name-only pipeline, not the new -n (file:lineno) format —
                # see resolve_test_name() for the full explanation. Fall
                # back to a plain name split; no file mapping until
                # sim_discover re-runs and populates tb_type.
                cached = sorted({t.strip() for t in r.strip().splitlines() if t.strip()})
                cached_test_files = {}
            # F-175: resolve dependency FILE LOCATIONS (+ primary sha256 for
            # staleness checks) here, once, at cache-miss time — same
            # rationale as discovery.py's initial-discovery pass.
            cached_dependency_files: dict[str, dict] = {}
            if cached_test_files:
                names = list(cached_test_files.keys())
                scan_results = await asyncio.gather(
                    *(scan_test_dependencies(cached_test_files[n], resolved_dir) for n in names)
                )
                for n, entry in zip(names, scan_results):
                    cached_dependency_files[n] = entry
            if cached:
                # Cache via config_action (write centralization)
                await config_action("set", "config", "test_discovery.cached_tests",
                                    json.dumps(cached))
                await config_action("set", "config", "test_discovery.cached_test_files",
                                    json.dumps(cached_test_files))
                await config_action("set", "config", "test_discovery.cached_dependency_files",
                                    json.dumps(cached_dependency_files))
                await config_action("set", "config", "test_discovery.cached_at",
                                    datetime.now().isoformat())

        if pattern:
            cached = [t for t in cached if pattern in t]

        if not cached:
            return f"No tests found{f' (pattern={pattern})' if pattern else ''}."

        return f"Tests ({len(cached)} found):\n" + "\n".join(f"  {t}" for t in sorted(cached))

    @mcp.tool()
    async def sim_discover(
        sim_dir: str = "",
        force: bool = False,
        top_module: str = "",
        run_dir: str = "",
        boundary_depth: int = 3,
    ) -> str:
        """Discover simulation environment and register in mcp_registry.

        Detects: sim_dir, TB type, runner, shell/EDA env, mcp_bridge.tcl,
        setup TCLs, EDA tool paths, bridge port, $sdf_annotate guards (v4.3).

        Args:
            sim_dir:        Explicit simulation directory. Empty = auto-discover.
            force:          Re-detect even if registry already exists.
            top_module:     Top module name for SDF analysis. Empty = auto-detect from script.
            run_dir:        Run directory override. Use when multiple candidates found
                            and USER INPUT REQUIRED prompted you to re-call with run_dir=.
            boundary_depth: Max hierarchy depth for block boundary auto-detection (default 3).
                            Saved to dump_strategy.{mode}.boundary_depth for Flow A/B use.
        """
        try:
            result = await run_full_discovery(sim_dir, force, top_module=top_module, run_dir=run_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        if not result.startswith(("ERROR", "USER INPUT REQUIRED")):
            try:
                resolved_dir = await resolve_sim_dir(sim_dir)
                config = await load_sim_config(resolved_dir) or {}
                for mode in ("rtl", "gate"):
                    config.setdefault("dump_strategy", {}).setdefault(mode, {})
                    config["dump_strategy"][mode]["boundary_depth"] = boundary_depth
                await save_sim_config(resolved_dir, config)
                if boundary_depth != 3:
                    result += f"\nboundary_depth={boundary_depth} saved to dump_strategy (rtl/gate)"
            except Exception:
                pass

        return result

    @mcp.tool()
    async def mcp_config(
        action: str = "show",
        file: str = "config",
        key: str = "",
        value: str = "",
    ) -> str:
        """View or modify xcelium-mcp registry/config via dot-notation keys.

        Args:
            action: "show" (full dump), "get" (read key), "set" (write key), "delete" (remove key).
            file:   "config" (.mcp_sim_config.json), "registry" (mcp_registry.json),
                    or "checkpoint" (checkpoints/manifest.json).
            key:    Dot-notation path (e.g. "runner.default_mode", "checkpoints.L1_TOP015").
            value:  Value for 'set' action. Auto-parsed: "9876" to int, "true" to bool.
        """
        try:
            return await config_action(action, file, key, value)
        except RuntimeError as e:
            return f"ERROR: {e}"

    @mcp.tool()
    async def sim_bridge_run(
        test_name: str,
        sim_dir: str = "",
        sim_mode: str = "",
        timeout: int = 120,
        extra_args: str = "",
        dump_depth: str = "",
        auto_boundaries: bool = False,
    ) -> str:
        """Start simulation in bridge (interactive) mode. Compile + launch + connect bridge.

        After this tool returns, use sim_run/get_signal_value/bisect_signal for debugging.
        For batch (non-interactive) runs, use sim_batch_run instead.

        Args:
            test_name:        Required — test to run. Short name OK (e.g. "TOP015").
            sim_dir:          Simulation dir. Empty = registry default.
            sim_mode:         "rtl"|"gate"|"ams_rtl"|"ams_gate". Empty = default_mode.
            timeout:          Max seconds to wait for bridge ready.
            extra_args:       1-shot extra simulation arguments (not saved to registry).
            dump_depth:       "boundary"|"all"|"" (auto from mode_defaults). v4.3.
            auto_boundaries:  If True and SimVision bridge is connected, auto-discover
                              block boundaries via 'scope -describe' and save to config.
                              Flow A of v5.2 hierarchical dump strategy.
        """
        try:
            test_name = await resolve_test_name(test_name, sim_dir)
            result = await start_bridge_simulation(
                test_name, sim_dir, sim_mode, timeout,
                extra_args=extra_args, bridges=bridges, dump_depth=dump_depth,
            )
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"
        except RuntimeError as e:
            return f"ERROR: {e}"

        # F-175: TB source provenance — best-effort, never fails the tool call.
        # Also stashed on bridges so checkpoint(action=save) in bridge mode
        # (which has no test_name param of its own) can record the same info.
        resolved_dir = sim_dir
        tb_source = None
        try:
            resolved_dir = await resolve_sim_dir(sim_dir)
            tb_source = await build_tb_provenance(test_name, resolved_dir)
        except ValueError:
            pass
        bridges.current_test_name = test_name
        bridges.current_tb_source = tb_source
        # F-D (session-state-reattach): also persist to the sim_dir-keyed registry
        # so a worker that reconnects after a restart (SSH drop, idle-culler) can
        # restore these onto its own fresh BridgeManager — see connect_simulator's
        # F-C direct-hit branch below. Best-effort like TB provenance itself above.
        try:
            await update_session_state(resolved_dir, test_name, tb_source)
        except OSError:
            pass
        if tb_source:
            result += f"\n{format_tb_provenance(tb_source)}"

        if auto_boundaries and bridges.simvision_raw and bridges.simvision_raw.connected:
            try:
                from xcelium_mcp.sim_env_detection import _boundaries_from_tcl
                from xcelium_mcp.tcl_preprocessing import get_dump_strategy
                config = await load_sim_config(resolved_dir) or {}
                effective_mode = sim_mode or config.get("runner", {}).get("default_mode", "rtl")
                base_mode = "gate" if "gate" in effective_mode else "rtl"
                strategy = get_dump_strategy(config, effective_mode)
                depth = strategy.get("boundary_depth", 3)
                top = config.get("top_module", "top")
                block_filter = strategy.get("block_filter")
                if isinstance(block_filter, str):
                    block_filter = [block_filter]
                boundaries = await _boundaries_from_tcl(
                    bridges.simvision_raw, top, depth=depth, block_filter=block_filter
                )
                if boundaries:
                    await config_action(
                        "set", "config",
                        f"dump_strategy.{base_mode}.block_boundaries",
                        json.dumps(boundaries),
                    )
                    result += f"\nauto_boundaries: discovered {len(boundaries)} block(s) via SimVision"
                else:
                    result += "\nauto_boundaries: no boundary signals found (check top_module in config)"
            except Exception as _e:
                result += f"\nauto_boundaries: skipped ({_e})"

        return result

    @mcp.tool()
    async def connect_simulator(
        sim_dir: str = "",
        host: str = "localhost",
        port: int = 0,
        target: str = "auto",
        timeout: float = 30.0,
    ) -> str:
        """Connect to simulator bridge(s).

        v4.1: Multi-bridge support. Reads ready file for port + type auto-detection.
        F-C: sim_dir lets a caller with multiple concurrent sim_dirs pick the
        right bridge deterministically instead of relying on ready-file scans.

        Args:
            sim_dir: Simulation directory. If a registry bridge_port entry exists
                     for it (written by sim_bridge_run — see registry.update_bridge_port),
                     connect directly to that port instead of scanning ready files.
                     Avoids the ambiguity when multiple bridges are live at once.
            host:    Bridge host (default localhost).
            port:    Bridge port. 0 = auto-detect from ready files (or from sim_dir above).
            target:  "xmsim" | "simvision" | "auto". auto = ready file type.
                     port=0 + target=auto → scan all ready files, connect each to slot.
            timeout: Connection timeout in seconds.
        """
        f_c_direct_hit = False
        if sim_dir and port == 0:
            registry_port = await get_bridge_port(sim_dir)
            if registry_port is not None:
                port = registry_port
                f_c_direct_hit = True
                if target == "auto":
                    target = "xmsim"  # registry only tracks the xmsim bridge port (F-C)

        if port == 0 and target == "auto":
            return await _auto_connect_all(bridges, host, timeout)

        if port == 0:
            port, detected_type = await _find_ready_file(target)
            if port == 0:
                return f"ERROR: No {target} bridge found in ready files."
            target = detected_type

        if target == "auto":
            target = await _read_bridge_type(port)

        bridge = TclBridge(host=host, port=port, timeout=timeout)
        try:
            ping = await bridge.connect()
        except BRIDGE_ERRORS as e:
            return f"ERROR: Connection failed: {type(e).__name__}: {e}"

        if target == "simvision":
            bridges.set_simvision(bridge)
        else:
            bridges.set_xmsim(bridge)
            bridges.xmsim_pid = await _get_pid_for_port(port)
            if f_c_direct_hit:
                # F-D: reattaching to an already-running xmsim via F-C's sim_dir
                # registry lookup — restore whatever sim_bridge_run last recorded
                # for this sim_dir onto this (fresh) BridgeManager, so a
                # subsequent checkpoint(action=save) still records correct TB
                # provenance even though this worker never called sim_bridge_run
                # itself. Best-effort like TB provenance recording elsewhere.
                try:
                    bridges.current_test_name, bridges.current_tb_source = (
                        await get_session_state(sim_dir)
                    )
                except OSError:
                    pass

        try:
            where = await bridge.execute("where")
        except TclError:
            where = "(unknown)"

        if target != "simvision" and bridges.xmsim_pid:
            return (
                f"Connected to {target} at {host}:{port} (ping={ping})\n"
                f"  xmsim_pid: {bridges.xmsim_pid}\n"
                f"Current position: {where}"
            )
        return f"Connected to {target} at {host}:{port} (ping={ping})\nCurrent position: {where}"

    @mcp.tool()
    async def sim_disconnect(
        action: str = "bridge",
        target: str = "all",
    ) -> str:
        """Disconnect or shutdown simulator.

        Args:
            action: "bridge" — disconnect bridge connection only (sim keeps running).
                    "shutdown" — safely shutdown simulator, preserving SHM data.
                    WARNING: Always use "shutdown" when ending a debug session.
                    Plain disconnect or pkill will lose SHM data.
            target: "xmsim" | "simvision" | "all" (default: all for bridge, xmsim for shutdown).
        """
        if action == "bridge":
            results = []
            if target in ("xmsim", "all") and bridges.xmsim_raw and bridges.xmsim_raw.connected:
                await bridges.xmsim_raw.disconnect()
                bridges.set_xmsim(None)
                results.append("xmsim: disconnected")
            if target in ("simvision", "all") and bridges.simvision_raw and bridges.simvision_raw.connected:
                await bridges.simvision_raw.disconnect()
                bridges.set_simvision(None)
                results.append("simvision: disconnected")
            return "\n".join(results) if results else f"No {target} bridge connected."

        elif action == "shutdown":
            user_tmp = await get_user_tmp_dir()

            # Clean up session logs and orphaned PS files on shutdown
            from xcelium_mcp.tmp_cleanup import cleanup_session_logs
            try:
                await cleanup_session_logs(user_tmp)
            except Exception:
                pass

            if target == "all":
                # Each bridge is checked independently; only error if both disconnected.
                results = []
                for btype, raw, set_fn in (
                    ("xmsim", bridges.xmsim_raw, bridges.set_xmsim),
                    ("simvision", bridges.simvision_raw, bridges.set_simvision),
                ):
                    if raw is None or not raw.connected:
                        results.append(f"{btype}: not connected (skipped)")
                        continue
                    port = raw.port if hasattr(raw, 'port') else 0
                    status = f"{btype}: shutdown ok (connection closed)"
                    try:
                        resp = await raw.execute_safe("__SHUTDOWN__")
                        status = f"{btype}: shutdown ok ({resp.body.strip()})"
                    except (ConnectionError, asyncio.TimeoutError):
                        pass
                    finally:
                        set_fn(None)
                        if port:
                            await shell_run(f"rm -f {user_tmp}/bridge_ready_{port}")
                    results.append(status)
                if all("(skipped)" in r for r in results):
                    return "ERROR: No simulator connected."
                return "\n".join(results)

            elif target == "simvision":
                bridge = bridges.simvision
                port = bridge.port if hasattr(bridge, 'port') else 0
                try:
                    resp = await bridge.execute_safe("__SHUTDOWN__")
                    return f"SimVision shutdown: {resp.body}"
                except (ConnectionError, asyncio.TimeoutError):
                    return "SimVision shutdown completed (connection closed)."
                finally:
                    bridges.set_simvision(None)
                    if port:
                        await shell_run(f"rm -f {user_tmp}/bridge_ready_{port}")

            else:
                bridge = bridges.xmsim
                port = bridge.port if hasattr(bridge, 'port') else 0
                try:
                    resp = await bridge.execute_safe("__SHUTDOWN__")
                    return f"Simulator shutdown: {resp.body}"
                except (ConnectionError, asyncio.TimeoutError):
                    return "Simulator shutdown completed (connection closed)."
                finally:
                    bridges.set_xmsim(None)
                    if port:
                        await shell_run(f"rm -f {user_tmp}/bridge_ready_{port}")

        else:
            return f"ERROR: Unknown action '{action}'. Use 'bridge' or 'shutdown'."

    @mcp.tool()
    async def sim_run(
        duration: str = "",
        timeout: float = 600.0,
        chunk: int = 100000,
    ) -> str:
        """Run the simulation, optionally for a specified duration.

        Args:
            duration: Simulation time to run with explicit unit (ns/us/ms/s/ps/fs),
                e.g. "100ns", "1us", "500ms". Empty = run until breakpoint or end.
            timeout: MCP response timeout in seconds (default 600s for gate-level sim support).
            chunk: Chunk size in ns for interruptible runs (default 100000 = 100µs).
                Set to 0 for legacy 1-shot mode.
                Smaller values improve stop responsiveness but add overhead.

        To interrupt a running sim_run, create the sentinel file from an external
        shell or ssh session (MCP tool calls are serialized — sim_stop cannot run
        in parallel with sim_run on the same server):
            touch /tmp/xcelium_mcp_{uid}/stop_{port}
        e.g. via ssh_bg_run: "touch /tmp/xcelium_mcp_1001/stop_9876"
        sim_run will stop at the next chunk boundary and return status='stopped'.
        """
        duration = duration.strip()
        if duration and len(duration) > _DURATION_MAX_LEN:
            return "ERROR: duration too long"
        if duration and not _DURATION_RE.fullmatch(duration):
            return (
                f"ERROR: Invalid duration {duration!r}. "
                "Expected format like '100ns', '1us', '500ps'."
            )
        bridge = bridges.xmsim
        # Convert duration to ns for chunked path; empty duration uses legacy path.
        # chunk=0 → legacy 1-shot (backward compat).
        effective_chunk = max(0, int(chunk))
        if not duration or effective_chunk == 0:
            # Empty duration or chunk=0 → legacy 1-shot path in Tcl
            payload = f"__RUN_AND_REPORT__ {duration} 0"
        else:
            # Chunked path: Tcl needs integer ns for arithmetic (incr remaining -$step)
            duration_ns = _duration_to_ns(duration)
            payload = f"__RUN_AND_REPORT__ {duration_ns} {effective_chunk}"
        try:
            result = await bridge.execute(payload, timeout=timeout)
        except asyncio.TimeoutError:
            return (
                f"ERROR: sim_run exceeded {timeout}s. "
                "Pass larger timeout= argument or split the run duration."
            )
        if result.startswith("CHUNKED_RUN_REPORT"):
            return _parse_chunked_run_report(result)
        if "RUN_ERROR:" in result:
            return f"ERROR: {result}"
        return f"Simulation advanced. Current position: {result}"

    @mcp.tool()
    async def sim_restart() -> str:
        """Restart the simulation from time 0.

        Restores the init snapshot (saved at sim_bridge_run time).
        If an SHM database is open, backs it up to waves_backup_{timestamp}.shm
        and re-initializes a fresh SHM so Mode A bisect works from time 0.

        Returns method used and backup_shm path when available.
        """
        bridge = bridges.xmsim
        result = await bridge.execute("__RESTART__")
        # F-116: parse backup_shm path from result
        backup_shm = ""
        for part in result.split("|"):
            if part.startswith("backup_shm:"):
                backup_shm = part[len("backup_shm:"):]
                break
        if backup_shm:
            return (
                f"Simulation restarted to time 0. ({result})\n"
                f"Previous SHM backed up to: {backup_shm}\n"
                f"Use bisect_signal(shm_path='{backup_shm}') or compare_waveforms to analyse the previous run."
            )
        return f"Simulation restarted to time 0. ({result})"

    @mcp.tool()
    async def execute_tcl(
        tcl_cmd: str,
        timeout: int = 30,
        target: str = "auto",
    ) -> str:
        """Execute arbitrary Tcl command in the connected SimVision bridge session.

        Returns raw Tcl output. Raises if not connected or command times out.
        Use for commands not covered by dedicated tools: database -open, probe -create, etc.

        WARNING: State-changing commands (finish, exit, restart) can cause unintended
        termination — caller's responsibility. Prefer dedicated tools when available.

        Args:
            tcl_cmd: Tcl command to execute (single or multi-line).
            timeout: Response timeout in seconds.
            target:  "xmsim" | "simvision" | "auto" (default: auto).
        """
        # Security: block dangerous Tcl commands that could execute arbitrary OS commands
        _TCL_DENYLIST = ["exec", "open", "file delete", "file rename", "exit", "source",
                         "eval", "interp", "package", "load", "uplevel", "after"]
        # Normalize whitespace: collapse tabs/multiple spaces to single space
        cmd_normalized = re.sub(r'[ \t]+', ' ', tcl_cmd).strip().lower()
        # Split on ; and newline to check each segment independently
        for segment in re.split(r'[;\n]', cmd_normalized):
            first_token = segment.strip().split(' ')[0] if segment.strip() else ""
            for denied in _TCL_DENYLIST:
                denied_parts = denied.split()
                # Single-word denylist entry: match first token exactly
                if len(denied_parts) == 1:
                    if first_token == denied_parts[0]:
                        return f"ERROR: Tcl command '{denied}' is blocked for security. Use dedicated MCP tools instead."
                # Multi-word denylist entry: match first N tokens
                else:
                    seg_tokens = segment.strip().split()
                    if len(seg_tokens) >= len(denied_parts) and seg_tokens[:len(denied_parts)] == denied_parts:
                        return f"ERROR: Tcl command '{denied}' is blocked for security. Use dedicated MCP tools instead."
        # S-6 fix: also block embedded [exec ...] and [open ...] in Tcl substitution brackets
        from xcelium_mcp.shell_utils import is_safe_tcl_string
        if not is_safe_tcl_string(tcl_cmd):
            return "ERROR: Tcl command contains embedded [exec] or [open] — blocked for security."

        bridge = bridges.get_bridge(target)
        return await bridge.execute(tcl_cmd, timeout=float(timeout))

    @mcp.tool()
    async def sim_status(target: str = "auto") -> str:
        """Get current simulation status (time, scope, state).

        Args:
            target: "xmsim" | "simvision" | "auto" (default: auto).
        """
        bridge = bridges.get_bridge(target)
        # Single round-trip: where + scope combined in Tcl
        return await bridge.execute("__STATUS__")

    return {"connect_simulator": connect_simulator}
