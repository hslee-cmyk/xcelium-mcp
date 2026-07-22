"""Compound operation MCP tools — Layer 3, Phase B (Plan §3.1/§8.2 B-3).

Thin MCP-tool wrappers over compound.py's 3 functions. Parameter resolution
(sim_dir/test_name/runner) mirrors tools/batch.py's sim_batch_run/sim_regression
exactly — these tools are meant to replace "sim_batch_run + bisect_signal/
extract_csv" (or "sim_regression + extract_csv per fail") called separately,
in one round-trip.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from xcelium_mcp.compound import analyze_waveform, regression_summary, run_and_check
from xcelium_mcp.registry import load_sim_config, resolve_sim_dir
from xcelium_mcp.runner_detection import load_or_detect_runner
from xcelium_mcp.shell_utils import UserInputRequired, validate_path
from xcelium_mcp.test_resolution import resolve_test_name, resolve_test_names_batch
from xcelium_mcp.tools.batch import _build_dump_window, _validate_run_params


def register(mcp: FastMCP) -> None:
    """Register the 3 compound-operation MCP tools.

    No BridgeManager dependency — these tools operate purely through
    batch_runner/csv_cache (SSH shell_run), never through TclBridge.
    """

    @mcp.tool()
    async def sim_run_and_check(
        test_name: str,
        sim_dir: str = "",
        csv_signals: list[str] | None = None,
        csv_mode: str = "range",
        find_condition: dict | None = None,
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
        """Run a single test, then optionally extract/search CSV — in one call.

        Compound operation (Plan §3.1): combines what sim_batch_run + a
        follow-up bisect_signal/extract_csv would otherwise take 2 round-trips
        for. Prefer this over calling them separately when you already know
        you'll want the CSV right after the run. Run-related args match
        sim_batch_run exactly (see its docstring for details).

        Args:
            csv_signals: If given, extract/search CSV for these signals after the run.
            csv_mode: "range" (default) — plain CSV extract over the whole run.
                      "bisect" — condition search via find_condition.
            find_condition: Required when csv_mode="bisect" — dict with keys
                {"signal","op","value","start_ns","end_ns","context_signals"}
                (same shape as bisect_signal's own params).
        """
        err = _validate_run_params(dump_depth, sdf_file, sdf_corner, dump_scopes)
        if err:
            return err

        try:
            resolved_sim_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"
        try:
            resolved_test_name = await resolve_test_name(test_name, resolved_sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"
        try:
            runner = await load_or_detect_runner(resolved_sim_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

        effective_mode = sim_mode or runner.get("default_mode", "rtl")
        effective_dump_depth = dump_depth if dump_depth else None
        try:
            dump_window = _build_dump_window(dump_window_start_ms, dump_window_end_ms)
        except ValueError as e:
            return str(e)

        result = await run_and_check(
            sim_dir=resolved_sim_dir,
            test_name=resolved_test_name,
            runner=runner,
            csv_signals=csv_signals if csv_signals else None,
            csv_mode=csv_mode,
            find_condition=find_condition,
            run_duration=run_duration,
            rename_dump=rename_dump,
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
        return result.to_mcp_output()

    @mcp.tool()
    async def sim_analyze_waveform(
        dump_path: str,
        signals: list[str],
        find_conditions: list[dict] | None = None,
        start_ns: int = 0,
        end_ns: int = 0,
    ) -> str:
        """Extract CSV from an existing dump and search multiple conditions — in one call.

        Compound operation (Plan §3.1): combines extract_csv + repeated
        bisect_signal calls against the same already-produced dump. No
        simulation is run — dump_path must point to an existing SHM/VPD/FST.

        Args:
            dump_path: SHM/VPD/FST dump path from a prior run.
            signals: Signals to extract into the CSV.
            find_conditions: Optional list of dicts, each shaped like
                {"signal","op","value","start_ns","end_ns","context_signals"} —
                every condition is searched independently against the same dump.
            start_ns/end_ns: Extraction range for the base CSV (0/0 = whole dump).
        """
        err = validate_path(dump_path, "dump_path")
        if err:
            return err

        result = await analyze_waveform(
            dump_path=dump_path,
            signals=signals,
            find_conditions=find_conditions,
            start_ns=start_ns,
            end_ns=end_ns,
        )
        return result.to_mcp_output()

    @mcp.tool()
    async def sim_regression_summary(
        test_list: list[str],
        sim_dir: str = "",
        csv_on_fail: bool = False,
        csv_signals: list[str] | None = None,
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
        """Run a regression, then optionally extract CSV for failing tests — in one call.

        Compound operation (Plan §3.1): combines what sim_regression + a
        follow-up extract_csv per failing test would otherwise take separate
        round-trips for.

        F-190: csv_on_fail now targets only the tests classify_regression_
        results() classified "fail"/"error" (compound.py::regression_summary's
        per_test_verdicts), not the whole test_list — the earlier module-1
        simplification (extract for everyone when the run isn't a full PASS)
        this replaces.

        Args:
            csv_on_fail: If True and the overall regression isn't a full PASS,
                extract CSV(csv_signals) for the tests that failed.
            csv_signals: Signals to extract when csv_on_fail triggers.
            (remaining args match sim_regression — see its docstring)
        """
        err = _validate_run_params(dump_depth, sdf_file, sdf_corner, dump_scopes)
        if err:
            return err

        try:
            resolved_sim_dir = await resolve_sim_dir(sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"
        try:
            runner = await load_or_detect_runner(resolved_sim_dir)
        except UserInputRequired as e:
            return f"USER INPUT REQUIRED:\n{e.prompt}"

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
        try:
            test_list = await resolve_test_names_batch(test_list, resolved_sim_dir)
        except ValueError as e:
            return f"ERROR: {e}"

        effective_dump_depth = dump_depth if dump_depth else None
        try:
            dump_window = _build_dump_window(dump_window_start_ms, dump_window_end_ms)
        except ValueError as e:
            return str(e)

        result = await regression_summary(
            sim_dir=resolved_sim_dir,
            test_list=test_list,
            runner=runner,
            csv_on_fail=csv_on_fail,
            csv_signals=csv_signals if csv_signals else None,
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
        return result.to_mcp_output()
