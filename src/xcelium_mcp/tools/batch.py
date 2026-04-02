"""Batch simulation tools."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.bridge_manager import BridgeManager
from xcelium_mcp.sim_runner import (
    UserInputRequired,
    get_default_sim_dir,
    run_full_discovery,
)
from xcelium_mcp.registry import load_sim_config
from xcelium_mcp.env_detection import _load_or_detect_runner
from xcelium_mcp.batch_runner import _run_batch_single, _run_batch_regression, resolve_test_name
from xcelium_mcp.tools.debug import _prepare_dump_scope_internal
import xcelium_mcp.csv_cache as _csv_cache


# Type alias for the restore_checkpoint callable passed from server.py
RestoreCheckpointFn = Callable[..., Coroutine[Any, Any, str]]


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
            dump_signals: Additional signals to dump (prepare_dump_scope).
            timeout: SSH wait timeout in seconds.
        """
        if probe_signals is None:
            probe_signals = []
        if dump_signals is None:
            dump_signals = []
        # Resolve sim_dir
        try:
            resolved_sim_dir = sim_dir if sim_dir else await get_default_sim_dir()
            if not resolved_sim_dir:
                # v4: auto-discover instead of error
                await run_full_discovery(sim_dir)
                resolved_sim_dir = sim_dir if sim_dir else await get_default_sim_dir()
                if not resolved_sim_dir:
                    return "ERROR: sim_discover failed. Provide sim_dir explicitly."
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        # v4.1: resolve short test name → full name
        try:
            test_name = await resolve_test_name(test_name, resolved_sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        # Load runner config (v4: delegates to sim_discover if config missing)
        try:
            runner = await _load_or_detect_runner(resolved_sim_dir)
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
                except Exception as e:
                    return f"Restore succeeded but probe_add_signals failed: {e}"

        # dump_signals: extend probe scope via prepare_dump_scope
        if dump_signals:
            try:
                extended_tcl = await _prepare_dump_scope_internal(
                    resolved_sim_dir,
                    additional_signals=dump_signals,
                )
                runner = dict(runner)
                runner["_extended_tcl"] = extended_tcl
            except Exception as e:
                return f"ERROR in prepare_dump_scope: {e}"

        # Execute simulation
        try:
            # v4.1: sim_mode + extra_args
            effective_mode = sim_mode or runner.get("default_mode", "rtl")
            log = await _run_batch_single(
                sim_dir=resolved_sim_dir,
                test_name=test_name,
                runner=runner,
                rename_dump=rename_dump,
                run_duration=run_duration,
                timeout=timeout,
                sim_mode=effective_mode,
                extra_args=extra_args,
            )
        except Exception as e:
            return f"ERROR running simulation: {e}"

        # Invalidate CSV cache for this sim_dir so next bisect_csv reads fresh SHM
        _csv_cache.clear_cache()

        return f"sim_batch_run {test_name} completed.\n\n{log}"

    @mcp.tool()
    async def sim_batch_regression(
        test_list: list[str],
        sim_dir: str = "",
        from_checkpoint: str = "",
        dump_signals: list[str] | None = None,
        rename_dump: bool = False,
        sim_mode: str = "",
        extra_args: str = "",
    ) -> str:
        """Run regression over a list of tests.

        Normal run (from_checkpoint=""): nohup per-test execution with adaptive log polling.
        Restore run (from_checkpoint=name): not yet implemented (Phase 4).

        Returns: regression summary table (N/M PASS, failures: [...]).

        Args:
            test_list: List of test names. Empty → auto-detect from mcp_sim_config.json.
            sim_dir: Simulation directory. Empty → default from mcp_registry.json.
            from_checkpoint: Checkpoint for [A'] restore mode (not yet implemented).
            dump_signals: Additional dump signals.
            rename_dump: Enable Method 6-B SHM rename fallback.
        """
        if dump_signals is None:
            dump_signals = []

        # Resolve sim_dir
        try:
            resolved_sim_dir = sim_dir if sim_dir else await get_default_sim_dir()
            if not resolved_sim_dir:
                # v4: auto-discover instead of error
                await run_full_discovery(sim_dir)
                resolved_sim_dir = sim_dir if sim_dir else await get_default_sim_dir()
                if not resolved_sim_dir:
                    return "ERROR: sim_discover failed. Provide sim_dir explicitly."
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        # Load runner config (v4: delegates to sim_discover if config missing)
        try:
            runner = await _load_or_detect_runner(resolved_sim_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        # dump_signals: 1회만 prepare_dump_scope → 전 테스트 공유
        if dump_signals:
            try:
                shared_tcl = await _prepare_dump_scope_internal(
                    resolved_sim_dir,
                    additional_signals=dump_signals,
                )
                runner = dict(runner)
                runner["_extended_tcl"] = shared_tcl
            except Exception as e:
                return f"ERROR in prepare_dump_scope: {e}"

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

        # v4.1: resolve short test names → full names
        try:
            test_list = [await resolve_test_name(t, resolved_sim_dir) for t in test_list]
        except ValueError as e:
            return f"ERROR: {e}"

        # Execute regression
        try:
            summary = await _run_batch_regression(
                sim_dir=resolved_sim_dir,
                test_list=test_list,
                runner=runner,
                from_checkpoint=from_checkpoint,
                restore_fn=restore_checkpoint_fn if from_checkpoint else None,
                rename_dump=rename_dump,
                sim_mode=sim_mode,
                extra_args=extra_args,
            )
        except Exception as e:
            return f"ERROR running regression: {e}"

        return f"sim_batch_regression completed.\n\n{summary}"
