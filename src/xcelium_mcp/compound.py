"""compound.py — Layer 3 compound operations for the /sim workflow (Phase A).

Thin composition over already-tested functions (batch_runner.py, csv_cache.py) —
no new batch execution or CSV parsing logic is introduced here. See
docs/01-plan/features/xcelium-mcp-debug-workflow-v2.plan.md §3.4 ("조합
우선") and docs/02-design/features/xcelium-mcp-debug-workflow-v2.design.md §1.2.

The 3 compound operations (run_and_check / analyze_waveform / regression_summary)
match Plan §3.1's Backend Interface contract exactly — this module IS that
interface's implementation, but the interface itself stays a documented
convention (Design §2.0 Option C), not a Python Protocol/ABC.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import xcelium_mcp.csv_cache as csv_cache
from xcelium_mcp.batch_runner import _COMPLETE_RE, run_batch_regression, run_batch_single
from xcelium_mcp.shell_utils import find_shm

__all__ = ["CompoundResult", "run_and_check", "analyze_waveform", "regression_summary"]


@dataclass
class CompoundResult:
    """Shared result type for all 3 compound operations (Plan §3.2)."""

    status: str          # "PASS" | "FAIL" | "ERROR" | "PARTIAL"
    log_summary: str
    dump_path: str = ""
    csv_path: str = ""
    details: dict = field(default_factory=dict)

    def to_cli_output(self) -> str:
        """CLI [TAG] 형식 출력 (Plan §7.2 예시와 동일한 태그 구조).

        [RUN]/[REGRESSION] 태그는 여기 포함하지 않는다 — CompoundResult는
        test_name을 들고 있지 않으므로(§3.2 참조), 그 태그는 호출자(cli.py)가
        자신이 알고 있는 컨텍스트로 앞에 붙인다.
        """
        lines = []
        if self.log_summary:
            lines.append(f"[LOG] {self.log_summary.splitlines()[0]}")
        if self.dump_path:
            lines.append(f"[DUMP] {self.dump_path}")
        if self.csv_path:
            lines.append(f"[CSV] {self.csv_path}")
        lines.append(f"[RESULT] {self.status}")
        return "\n".join(lines)

    def to_mcp_output(self) -> str:
        """MCP tool용 상세 텍스트 출력."""
        parts = [f"status: {self.status}", "", self.log_summary]
        if self.dump_path:
            parts.append(f"\ndump_path: {self.dump_path}")
        if self.csv_path:
            parts.append(f"csv_path: {self.csv_path}")
        if self.details:
            parts.append(f"\ndetails:\n{json.dumps(self.details, indent=2, default=str)}")
        return "\n".join(parts)


def _classify_status(log: str) -> str:
    """Classify a single test's raw log into PASS/FAIL (Plan §3.4 "log_grep" step).

    Reuses the same COMPLETE-verdict marker batch_runner.classify_regression_results
    already relies on (_COMPLETE_RE) instead of inventing a new pattern.

    F-186: legacy directed tests (pre-dating the UVM harness convention) print
    their own per-check verdicts as lowercase "failed!!"/"passed!!" (e.g.
    `[REG BANK TEST] register 0x03: failed!!`) instead of "COMPLETE. Errors: N"
    or uppercase "FAIL". Before this fix, such a log fell through both of those
    checks and was classified PASS purely because it reached "$finish" —
    silently discarding every internal failure the TB itself reported. A
    case-insensitive "failed!!" search closes that gap without touching the
    UVM/uppercase-FAIL priority order above it.

    ERROR is NOT produced here — infrastructure failures (EDA env missing, SSH
    timeout, etc.) surface as exceptions from run_batch_single itself and are
    mapped to CompoundResult(status="ERROR", ...) by the caller (run_and_check),
    matching the existing sim_batch_run MCP tool's exception-handling convention
    (tools/batch.py). A log with no verdict and no "$finish" (timeout/crash while
    the process itself didn't raise) is treated as FAIL, not ERROR — the process
    ran to completion from run_batch_single's point of view, but produced no
    passing verdict.
    """
    m = _COMPLETE_RE.search(log)
    if m:
        return "PASS" if int(m.group(1)) == 0 else "FAIL"
    if "FAIL" in log:
        return "FAIL"
    if "failed!!" in log.lower():
        return "FAIL"
    if "$finish" in log:
        return "PASS"  # waveform-only completion, no assertion failures
    return "FAIL"  # no verdict, no $finish — timeout or crash


async def run_and_check(
    sim_dir: str,
    test_name: str,
    runner: dict,
    csv_signals: list[str] | None = None,
    csv_mode: str = "range",
    find_condition: dict | None = None,
    **run_kwargs: object,
) -> CompoundResult:
    """Run a single test, then optionally extract/search CSV in one call (Plan §3.1).

    Composes run_batch_single() (execution) + csv_cache.extract()/
    bisect_signal_dump() (analysis) — no new batch/CSV logic (Plan §3.4).

    Args:
        sim_dir: Simulation directory. Caller resolves it first (same
            convention as run_batch_single — this function does not call
            registry.resolve_sim_dir() itself, that's the MCP tool layer's job).
        test_name: Test name. Caller resolves short→full name first (same
            convention as test_resolution.resolve_test_name).
        runner: Runner config dict, as loaded by runner_detection.load_or_detect_runner.
        csv_signals: If given, extract/search CSV for these signals after the run.
        csv_mode: "range" (default) — plain csv_cache.extract() over the whole
            dump. "bisect" — csv_cache.bisect_signal_dump()-driven search using
            find_condition.
        find_condition: Required when csv_mode="bisect". Dict with keys
            {"signal", "op", "value", "start_ns", "end_ns", "context_signals"}
            (same shape as csv_cache.bisect_signal_dump's own params).
        **run_kwargs: Forwarded verbatim to run_batch_single (dump_depth,
            timeout, sim_mode, extra_args, sdf_file, ...) — not reinterpreted here.

    Returns:
        CompoundResult. status="ERROR" if run_batch_single itself raises;
        status="PASS"/"FAIL" from _classify_status() otherwise. A csv_signals
        extraction failure does NOT downgrade a PASS/FAIL run to ERROR — it is
        recorded in details["csv_error"] instead (the simulation result itself
        is still valid and more important than a follow-up CSV extraction).
    """
    try:
        log, dump_summary = await run_batch_single(
            sim_dir=sim_dir, test_name=test_name, runner=runner, **run_kwargs,
        )
    except (RuntimeError, ValueError, OSError, TimeoutError) as e:
        return CompoundResult(status="ERROR", log_summary=f"run_batch_single failed: {e}")

    status = _classify_status(log)
    dump_path = await find_shm(sim_dir, test_name)
    details: dict = {}
    if dump_summary is not None:
        details["dump_summary"] = dump_summary

    csv_path = ""
    if csv_signals:
        try:
            if csv_mode == "bisect":
                if not find_condition:
                    raise ValueError("find_condition is required when csv_mode='bisect'")
                details["bisect_result"] = await csv_cache.bisect_signal_dump(
                    shm_path=dump_path,
                    signal=find_condition["signal"],
                    op=find_condition["op"],
                    value=find_condition["value"],
                    start_ns=find_condition.get("start_ns", 0),
                    end_ns=find_condition.get("end_ns", 0),
                    context_signals=find_condition.get("context_signals"),
                )
            else:
                csv_path = await csv_cache.extract(shm_path=dump_path, signals=csv_signals)
        except (RuntimeError, ValueError) as e:
            details["csv_error"] = str(e)

    return CompoundResult(
        status=status, log_summary=log, dump_path=dump_path, csv_path=csv_path, details=details,
    )


async def analyze_waveform(
    dump_path: str,
    signals: list[str],
    find_conditions: list[dict] | None = None,
    start_ns: int = 0,
    end_ns: int = 0,
) -> CompoundResult:
    """Extract CSV from an existing dump and search multiple conditions (Plan §3.1).

    Composes csv_cache.extract() + repeated csv_cache.bisect_signal_dump() calls
    against an already-produced dump — no new CSV logic, and no simulation is run.

    Args:
        dump_path: SHM/VPD/FST dump path from a prior run.
        signals: Signals to extract into the CSV.
        find_conditions: Optional list of dicts, each shaped like
            {"signal", "op", "value", "start_ns", "end_ns", "context_signals"} —
            every condition is searched independently against the same dump.
        start_ns/end_ns: Extraction range for the base CSV (0/0 = whole dump).
            Per-condition start_ns/end_ns (if given) override this range for
            that condition's own bisect search only.

    Returns:
        CompoundResult. status="ERROR" if the initial extract() fails (nothing
        else can proceed without the CSV). status="PASS" otherwise — this
        reflects "the analysis operation itself succeeded", not a simulation
        verdict (there is no test run in this operation). details["conditions"]
        holds the per-condition bisect result strings, same order as
        find_conditions.
    """
    try:
        csv_path = await csv_cache.extract(
            shm_path=dump_path, signals=signals, start_ns=start_ns, end_ns=end_ns,
        )
    except RuntimeError as e:
        return CompoundResult(status="ERROR", log_summary=f"extract failed: {e}", dump_path=dump_path)

    details: dict = {}
    if find_conditions:
        details["conditions"] = [
            await csv_cache.bisect_signal_dump(
                shm_path=dump_path,
                signal=cond["signal"],
                op=cond["op"],
                value=cond["value"],
                start_ns=cond.get("start_ns", start_ns),
                end_ns=cond.get("end_ns", end_ns),
                context_signals=cond.get("context_signals"),
            )
            for cond in find_conditions
        ]

    return CompoundResult(
        status="PASS",
        log_summary=f"Extracted CSV for {len(signals)} signal(s)",
        dump_path=dump_path,
        csv_path=csv_path,
        details=details,
    )


_VERDICT_RE = re.compile(r'^(\d+)/(\d+) verdict tests PASS', re.MULTILINE)
_WAVEFORM_RE = re.compile(r'^(\d+)/(\d+) waveform tests COMPLETE', re.MULTILINE)


def _classify_regression_status(summary: str) -> str:
    """Classify overall regression status from run_batch_regression's summary text.

    Reads the "N/M verdict tests PASS" / "N/M waveform tests COMPLETE" header
    lines that batch_runner.classify_regression_results() already produces —
    does not re-derive per-test verdicts independently.
    """
    m = _VERDICT_RE.search(summary)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
    else:
        m = _WAVEFORM_RE.search(summary)
        passed, total = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if total == 0:
        return "ERROR"
    if passed == total:
        return "PASS"
    if passed == 0:
        return "FAIL"
    return "PARTIAL"


async def regression_summary(
    sim_dir: str,
    test_list: list[str],
    runner: dict,
    csv_on_fail: bool = False,
    csv_signals: list[str] | None = None,
    **run_kwargs: object,
) -> CompoundResult:
    """Run a regression, then optionally extract CSV for failing tests (Plan §3.1).

    Composes run_batch_regression() (execution) + csv_cache.extract() — no new
    regression/CSV logic.

    **Documented simplification (module-1 scope)**: run_batch_regression()
    returns an aggregate summary string, not a structured per-test PASS/FAIL
    dict — exposing that structure would require changing batch_runner.py
    itself, which Plan §3.4 explicitly avoids (risk of new bugs in an
    already-verified path). So when csv_on_fail=True and the overall status
    is not "PASS", this extracts CSV for every test in test_list rather than
    pinpointing exactly which ones failed. Precise per-test-failure CSV
    extraction is left as a future extension to run_batch_regression's return
    contract (YAGNI for Phase A).

    Args:
        sim_dir, test_list, runner: Forwarded to run_batch_regression as-is.
        csv_on_fail: If True and overall status != "PASS", extract CSV
            (csv_signals) for every test in test_list.
        csv_signals: Signals to extract when csv_on_fail triggers.
        **run_kwargs: Forwarded to run_batch_regression (dump_depth, sim_mode,
            save_checkpoints, ...).

    Returns:
        CompoundResult.status: "PASS" (all verdict/waveform tests passed),
        "FAIL" (none passed), "PARTIAL" (some passed, some didn't), "ERROR"
        (run_batch_regression raised, or produced no parseable verdict/
        waveform count at all). details = {"tb_provenance", "dump_stats"
        (if any), "csv_by_test" (if csv_on_fail triggered)}.
    """
    try:
        summary, dump_stats, tb_provenance = await run_batch_regression(
            sim_dir=sim_dir, test_list=test_list, runner=runner, **run_kwargs,
        )
    except (RuntimeError, ValueError, OSError, TimeoutError) as e:
        return CompoundResult(status="ERROR", log_summary=f"run_batch_regression failed: {e}")

    status = _classify_regression_status(summary)

    details: dict = {"tb_provenance": tb_provenance}
    if dump_stats is not None:
        details["dump_stats"] = dump_stats

    if csv_on_fail and status != "PASS" and csv_signals:
        csv_by_test: dict[str, str] = {}
        for tn in test_list:
            try:
                shm = await find_shm(sim_dir, tn)
                if shm:
                    csv_by_test[tn] = await csv_cache.extract(shm_path=shm, signals=csv_signals)
            except RuntimeError:
                continue
        if csv_by_test:
            details["csv_by_test"] = csv_by_test

    return CompoundResult(status=status, log_summary=summary, details=details)
