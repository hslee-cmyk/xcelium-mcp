"""Batch simulation tools."""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

import xcelium_mcp.csv_cache as _csv_cache
from xcelium_mcp.batch_runner import run_batch_regression, run_batch_single
from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.registry import load_sim_config, resolve_sim_dir
from xcelium_mcp.runner_detection import load_or_detect_runner
from xcelium_mcp.shell_utils import UserInputRequired, find_shm, validate_path
from xcelium_mcp.tb_provenance import (
    build_tb_provenance,
    format_tb_provenance,
    provenance_unavailable_reason,
)
from xcelium_mcp.tcl_bridge import TclError
from xcelium_mcp.test_resolution import resolve_test_name

# Type alias for the restore_checkpoint callable passed from server.py
RestoreCheckpointFn = Callable[..., Coroutine[Any, Any, str]]


_DUMP_SCOPES_KEY_RE = re.compile(r'^[\w.*]+$')
_VALID_DUMP_SCOPES_VALUES = {"all", "boundary", "skip"}


def _validate_run_params(
    dump_depth: str, sdf_file: str, sdf_corner: str, dump_scopes: dict | None
) -> str | None:
    """Validate dump_depth/sdf_corner/dump_scopes shared by sim_batch_run and sim_regression.

    Returns an error message string if invalid, or None if all params are valid.
    """
    if dump_depth and dump_depth not in ("boundary", "all"):
        return f"Invalid dump_depth='{dump_depth}'. Must be 'boundary', 'all', or '' (auto)."
    if sdf_file and sdf_corner not in ("min", "max", "typ"):
        return f"Invalid sdf_corner='{sdf_corner}'. Must be 'min', 'max', or 'typ'."
    if dump_scopes is not None:
        for k, v in dump_scopes.items():
            if not _DUMP_SCOPES_KEY_RE.fullmatch(k):
                return f"Invalid dump_scopes key: {k!r}. Only word chars, '.', '*' allowed."
            if v not in _VALID_DUMP_SCOPES_VALUES:
                return f"Invalid dump_scopes value: {v!r}. Must be 'all', 'boundary', or 'skip'."
    return None


def _build_dump_window(start_ms: int, end_ms: int) -> dict | None:
    """Validate and build dump_window dict from flat MCP params."""
    if start_ms < 0 or end_ms < 0:
        raise ValueError(f"dump_window values must be >= 0 (got start={start_ms}, end={end_ms})")
    if start_ms == 0 and end_ms == 0:
        return None
    if end_ms <= start_ms:
        raise ValueError(f"Invalid dump_window: end_ms ({end_ms}) must be > start_ms ({start_ms})")
    return {"start_ms": start_ms, "end_ms": end_ms}


def register(
    mcp: FastMCP,
    bridges: BridgeManager,
    restore_checkpoint_fn: RestoreCheckpointFn,
) -> None:
    """Register batch simulation tools.

    Args:
        mcp: FastMCP server instance.
        bridges: BridgeManager for simulator bridge access.
        restore_checkpoint_fn: Reference to the restore_checkpoint tool function
            defined in server.py (avoids circular import).
    """

    @mcp.tool()
    async def sim_batch_run(
        test_name: str,
        sim_dir: str = "",
        from_checkpoint: str = "",
        probe_signals: list[str] | None = None,
        shm_path: str = "",
        run_duration: str = "",
        rename_dump: bool = False,
        dump_signals: list[str] | None = None,
        timeout: int = 600,
        sim_mode: str = "",
        extra_args: str = "",
        dump_depth: str = "",
        dump_window_start_ms: int = 0,
        dump_window_end_ms: int = 0,
        sdf_file: str = "",
        sdf_corner: str = "max",
        force: bool = False,
        dump_scopes: dict | None = None,
        use_dump_history: bool = False,
    ) -> str:
        """Run simulation for a single test.

        Normal run ([A]): from_checkpoint="" → compile + run → SHM dump.
        Restore run ([A']): from_checkpoint=name → restore_checkpoint → probe_add → run → new SHM.

        SHM overwrite prevention:
          Method 6-A (default): injects TEST_NAME env var; setup.tcl uses $env(TEST_NAME).
          Method 6-B (rename_dump=True): renames dump/ci_top.shm after simulation.

        Returns: log summary (PASS/FAIL lines, error count, SHM dump path).

        Args:
            test_name: Test name (e.g. "TOP015").
            sim_dir: Simulation directory. Empty → use default from mcp_registry.json.
            from_checkpoint: Checkpoint name for [A'] restore mode.
            probe_signals: Additional signals to probe in [A'] mode.
            shm_path: New SHM path for [A'] mode (default: dump/{test_name}_extra.shm).
            run_duration: Run only up to this time (e.g. "10ms"). Empty = run to end.
            rename_dump: Enable Method 6-B SHM rename fallback.
            dump_signals: Additional signals for v4.3 dump_depth probe (merged with BOUNDARY_SIGNALS).
            timeout: SSH wait timeout in seconds.
            force: Force re-run even if a completed job exists. Ignores previous results.
        """
        if probe_signals is None:
            probe_signals = []
        if dump_signals is None:
            dump_signals = []
        # Security: path traversal validation
        for p, label in [(sim_dir, "sim_dir"), (shm_path, "shm_path")]:
            if p:
                err = validate_path(p, label)
                if err:
                    return err
        # v4.3/v5.2: dump_depth/sdf_corner/dump_scopes enum validation
        err = _validate_run_params(dump_depth, sdf_file, sdf_corner, dump_scopes)
        if err:
            return err
        # Cleanup stale logs (TTL 24h) before starting a new batch run
        from xcelium_mcp.shell_utils import get_user_tmp_dir
        from xcelium_mcp.tmp_cleanup import cleanup_old_logs
        try:
            _user_tmp = await get_user_tmp_dir()
            await cleanup_old_logs(_user_tmp)
        except Exception:
            pass

        # Resolve sim_dir
        try:
            resolved_sim_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        # v4.1: resolve short test name → full name
        try:
            test_name = await resolve_test_name(test_name, resolved_sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        # Load runner config (v4: delegates to sim_discover if config missing)
        try:
            runner = await load_or_detect_runner(resolved_sim_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        # [A'] restore mode: restore checkpoint → add probe signals before run
        if from_checkpoint:
            restore_result = await restore_checkpoint_fn(from_checkpoint, resolved_sim_dir)
            if "ERROR" in restore_result or "restore failed" in restore_result:
                return f"ERROR in [A'] restore: {restore_result}"
            if probe_signals:
                try:
                    bridge = bridges.xmsim
                    sig_str = " ".join(probe_signals)
                    await bridge.execute(
                        f"probe -create {{{sig_str}}} -shm -depth all", timeout=30.0
                    )
                except (ConnectionError, TclError, TimeoutError) as e:
                    return f"Restore succeeded but probe_add_signals failed: {e}"

        # Execute simulation
        # dump_signals flows to run_batch_single → _preprocess_setup_tcl → _resolve_probe_signals
        try:
            # v4.1: sim_mode + extra_args
            effective_mode = sim_mode or runner.get("default_mode", "rtl")
            # v4.3: dump_depth + dump_window
            effective_dump_depth = dump_depth if dump_depth else None
            try:
                dump_window = _build_dump_window(dump_window_start_ms, dump_window_end_ms)
            except ValueError as e:
                return str(e)
            log, dump_summary = await run_batch_single(
                sim_dir=resolved_sim_dir,
                test_name=test_name,
                runner=runner,
                rename_dump=rename_dump,
                run_duration=run_duration,
                timeout=timeout,
                sim_mode=effective_mode,
                extra_args=extra_args,
                dump_depth=effective_dump_depth,
                dump_signals=dump_signals if dump_signals else None,
                dump_window=dump_window,
                sdf_file=sdf_file,
                sdf_corner=sdf_corner,
                force=force,
                dump_scopes=dump_scopes,
                use_dump_history=use_dump_history,
            )
        except (RuntimeError, ValueError, OSError, TimeoutError) as e:
            return f"ERROR running simulation: {e}"

        # Invalidate CSV cache for this sim_dir so next bisect_csv reads fresh SHM
        _csv_cache.clear_cache()

        # Resolve SHM path for downstream tools (bisect_signal, extract_csv)
        # find_shm: *test_name* glob removes project-specific prefix (ci_top) hardcoding
        shm_path = await find_shm(resolved_sim_dir, test_name)

        # Remove stale bisect CSV files (mtime differs from new SHM)
        if shm_path:
            try:
                await _csv_cache.cleanup_stale_csv(_user_tmp, shm_path)
            except Exception:
                pass

        # F-175: TB source provenance — best-effort, never fails the run.
        tb_source = await build_tb_provenance(test_name, resolved_sim_dir)

        parts = [
            f"sim_batch_run {test_name} completed.\n\n"
            f"shm_path: {shm_path or '(not found in dump/)'}\n\n"
            f"{log}"
        ]
        if dump_summary is not None and dump_depth == "boundary":
            import json as _json
            parts.append(f"\ndump_summary:\n{_json.dumps(dump_summary, indent=2)}")
        if tb_source is not None:
            parts.append(f"\n{format_tb_provenance(tb_source)}")
        else:
            # F-2: distinguish "project never migrated to F-175 schema" from
            # "this one test isn't in the map" instead of staying silent.
            reason = await provenance_unavailable_reason(test_name, resolved_sim_dir)
            if reason:
                parts.append(f"\ntb_provenance: unavailable ({reason})")
        return "".join(parts)

    @mcp.tool()
    async def sim_regression(
        test_list: list[str],
        sim_dir: str = "",
        dump_signals: list[str] | None = None,
        rename_dump: bool = False,
        sim_mode: str = "",
        extra_args: str = "",
        save_checkpoints: bool = False,
        l1_time: str = "",
        dump_depth: str = "",
        dump_window_start_ms: int = 0,
        dump_window_end_ms: int = 0,
        sdf_file: str = "",
        sdf_corner: str = "max",
        dump_scopes: dict | None = None,
        use_dump_history: bool = False,
    ) -> str:
        """Run regression over a list of tests.

        Each test is compiled and run independently via nohup batch.
        When save_checkpoints=True, L1 checkpoint is auto-saved per test:
          L1_{test}: at l1_time (common init completion, default 500us)
        Use sim_batch_run(from_checkpoint="L1_{test}") later for fast debugging.

        Returns: regression summary table (N/M PASS, failures: [...]).

        Args:
            test_list: List of test names. Empty → auto-detect from mcp_sim_config.json.
            sim_dir: Simulation directory. Empty → default from mcp_registry.json.
            dump_signals: Additional dump signals.
            rename_dump: Enable Method 6-B SHM rename fallback.
            save_checkpoints: Save L1/L2 checkpoints per test for later debugging.
            l1_time: Time for L1 checkpoint (default "500us"). e.g. "1ms".
        """
        # v4.3/v5.2: dump_depth/sdf_corner/dump_scopes enum validation
        err = _validate_run_params(dump_depth, sdf_file, sdf_corner, dump_scopes)
        if err:
            return err
        if dump_signals is None:
            dump_signals = []

        # Resolve sim_dir
        try:
            resolved_sim_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        # Load runner config (v4: delegates to sim_discover if config missing)
        try:
            runner = await load_or_detect_runner(resolved_sim_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        # dump_signals flows to run_batch_regression → _preprocess_setup_tcl → _resolve_probe_signals

        # Auto-detect test_list from sim config if empty
        if not test_list:
            cfg = await load_sim_config(resolved_sim_dir)
            if cfg:
                test_list = cfg.get("test_list", [])
            if not test_list:
                return (
                    "ERROR: test_list is empty and no test_list found in "
                    f".mcp_sim_config.json at {resolved_sim_dir}. "
                    "Provide test_list explicitly."
                )

        # v4.1: resolve short test names → full names (parallel resolution)
        try:
            test_list = list(await asyncio.gather(
                *(resolve_test_name(t, resolved_sim_dir) for t in test_list)
            ))
        except ValueError as e:
            return f"ERROR: {e}"

        # Execute regression
        try:
            effective_dump_depth = dump_depth if dump_depth else None
            try:
                dump_window = _build_dump_window(dump_window_start_ms, dump_window_end_ms)
            except ValueError as e:
                return str(e)
            summary, dump_stats, tb_provenance = await run_batch_regression(
                sim_dir=resolved_sim_dir,
                test_list=test_list,
                runner=runner,
                rename_dump=rename_dump,
                sim_mode=sim_mode,
                extra_args=extra_args,
                save_checkpoints=save_checkpoints,
                l1_time=l1_time,
                dump_depth=effective_dump_depth,
                dump_signals=dump_signals if dump_signals else None,
                dump_window=dump_window,
                sdf_file=sdf_file,
                sdf_corner=sdf_corner,
                dump_scopes=dump_scopes,
                use_dump_history=use_dump_history,
            )
        except (RuntimeError, ValueError, OSError, TimeoutError) as e:
            return f"ERROR running regression: {e}"

        # F-175: tb_provenance is captured per-test inside run_batch_regression,
        # right after each test's own run — not recomputed here at the end,
        # so a shared TB file edited mid-regression can't get attributed to
        # an earlier test that already finished (see run_batch_regression docstring).
        parts = [f"sim_regression completed.\n\n{summary}"]
        if dump_stats is not None:
            import json as _json
            parts.append(f"\ndump_stats:\n{_json.dumps(dump_stats, indent=2)}")
        if tb_provenance:
            import json as _json
            parts.append(f"\ntb_provenance:\n{_json.dumps(tb_provenance, indent=2)}")
        # F-2/F-3: diagnose per-test gaps instead of staying silent — same
        # reasoning as sim_batch_run above, applied to run_batch_regression's
        # per-test tb_provenance dict (F-3: confirmed it calls
        # build_tb_provenance() per test, see batch_runner.py).
        missing = [t for t in test_list if t not in tb_provenance]
        if missing:
            reasons = await asyncio.gather(
                *(provenance_unavailable_reason(t, resolved_sim_dir) for t in missing)
            )
            reason_lines = [
                f"  {t}: {r}" for t, r in zip(missing, reasons) if r
            ]
            if reason_lines:
                parts.append("\ntb_provenance unavailable:\n" + "\n".join(reason_lines))
        return "".join(parts)
