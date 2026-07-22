"""cli.py — xcelium-mcp-cli: AI-independent CLI entry point (Plan §7, Phase B).

Independent console_script (`xcelium-mcp-cli = "xcelium_mcp.cli:main"`) — NOT a
sys.argv branch inside server.py (Plan §7.1: since supervisor deployment,
WorkerHandler.handle() forks and calls server.main() directly per connection,
so there is no path for per-connection argv to reach it — a branch there would
be structurally unreachable for this use case regardless of style preference).

Runs on the same remote simulation server as `xcelium-mcp` itself, independent
of any MCP/Claude Code session. sim-state.json (client-local, Plan §5.1) is out
of scope here — this is the "AI 없이 직접 실행" escape hatch (Plan §7.3),
not a `/sim` Skill replacement.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from xcelium_mcp.compound import analyze_waveform, regression_summary, run_and_check
from xcelium_mcp.registry import load_sim_config, resolve_sim_dir
from xcelium_mcp.runner_detection import load_or_detect_runner
from xcelium_mcp.shell_utils import UserInputRequired
from xcelium_mcp.test_resolution import resolve_test_name, resolve_test_names_batch


async def _cmd_run(args: argparse.Namespace) -> int:
    try:
        sim_dir = await resolve_sim_dir(args.sim_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        test_name = await resolve_test_name(args.test_name, sim_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        runner = await load_or_detect_runner(sim_dir)
    except UserInputRequired as e:
        print(f"USER INPUT REQUIRED:\n{e.prompt}", file=sys.stderr)
        return 2

    result = await run_and_check(
        sim_dir=sim_dir,
        test_name=test_name,
        runner=runner,
        csv_signals=args.csv_signals or None,
        timeout=args.timeout,
    )
    print(f"[RUN] {test_name}")
    print(result.to_cli_output())
    return 0 if result.status == "PASS" else 1


async def _cmd_analyze(args: argparse.Namespace) -> int:
    result = await analyze_waveform(
        dump_path=args.dump_path,
        signals=args.signals,
        start_ns=args.start_ns,
        end_ns=args.end_ns,
    )
    print(f"[ANALYZE] {args.dump_path}")
    print(result.to_cli_output())
    return 0 if result.status == "PASS" else 1


async def _cmd_regression(args: argparse.Namespace) -> int:
    try:
        sim_dir = await resolve_sim_dir(args.sim_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        runner = await load_or_detect_runner(sim_dir)
    except UserInputRequired as e:
        print(f"USER INPUT REQUIRED:\n{e.prompt}", file=sys.stderr)
        return 2

    test_list = args.test_list
    if not test_list:
        cfg = await load_sim_config(sim_dir)
        test_list = cfg.get("test_list", []) if cfg else []
        if not test_list:
            print(
                "ERROR: no --test-list given and no test_list found in "
                f".mcp_sim_config.json at {sim_dir}. Provide --test-list explicitly.",
                file=sys.stderr,
            )
            return 2
    try:
        test_list = await resolve_test_names_batch(test_list, sim_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    result = await regression_summary(
        sim_dir=sim_dir,
        test_list=test_list,
        runner=runner,
        csv_on_fail=args.csv_on_fail,
        csv_signals=args.csv_signals or None,
    )
    print(f"[REGRESSION] {len(test_list)} test(s)")
    print(result.to_cli_output())
    return 0 if result.status == "PASS" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xcelium-mcp-cli",
        description="xcelium-mcp CLI (Layer 2) - AI 없이 compound operation 직접 실행",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --sim-dir is defined per-subparser (not on the top-level parser) so it
    # can be given in either position (`... run --sim-dir X TOP015` and
    # `... run TOP015 --sim-dir X` both work) — a shared top-level option would
    # only parse before the subcommand name, a common argparse footgun.
    p_run = sub.add_parser("run", help="Run a single test + optional CSV check")
    p_run.add_argument("test_name")
    p_run.add_argument("--sim-dir", default="", help="Default: registry default sim_dir")
    p_run.add_argument("--csv-signals", nargs="*", default=[])
    p_run.add_argument("--timeout", type=int, default=600)
    p_run.set_defaults(func=_cmd_run)

    p_analyze = sub.add_parser("analyze", help="Extract/search CSV from an existing dump")
    p_analyze.add_argument("dump_path")
    p_analyze.add_argument("--signals", nargs="+", required=True)
    p_analyze.add_argument("--start-ns", type=int, default=0)
    p_analyze.add_argument("--end-ns", type=int, default=0)
    p_analyze.set_defaults(func=_cmd_analyze)

    p_regr = sub.add_parser("regression", help="Run a regression + summary")
    p_regr.add_argument("--sim-dir", default="", help="Default: registry default sim_dir")
    p_regr.add_argument("--test-list", nargs="*", default=[],
                         help="Default: test_list from .mcp_sim_config.json")
    p_regr.add_argument("--csv-on-fail", action="store_true")
    p_regr.add_argument("--csv-signals", nargs="*", default=[])
    p_regr.set_defaults(func=_cmd_regression)

    return parser


def main() -> None:
    """Entry point for the `xcelium-mcp-cli` console_script."""
    parser = _build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(args.func(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
