#!/usr/bin/env python3
"""sim_state.py — sim-state.json CRUD + phase transitions (Plan §5.1, Phase C).

Runs entirely on the CLIENT LOCAL machine (never on the remote simulation
server) — see docs/01-plan/features/xcelium-mcp-debug-workflow-v2.plan.md §3.4/
§5.1 for why this cannot live in src/xcelium_mcp/ (that package runs on the
remote sim-server and has no filesystem access to the client's project files).

No third-party dependencies (stdlib only) — this script must run standalone
via `python3 sim_state.py <command> ...` from the Skill (Bash tool) or a Hook,
neither of which can assume the xcelium-mcp pip package is installed locally.

**Implementation note on `sim_dir` vs. file location (resolves a Plan §5.1
ambiguity)**: Plan §5.1's pseudocode signatures all take `sim_dir` as the
first argument, and also say the state file lives at `{project}/.ai/
sim-state.json` where `{project}` is the local RTL project root. These are
NOT the same path — `sim_dir` is the *remote* simulation directory (resolved
by registry.py, which only exists on the sim-server) and could never be used
to locate a *local* file even if this script had that value. So here,
`sim_dir` is treated purely as metadata to persist (the JSON's own top-level
`"sim_dir"` field) — the state file's own location is resolved from
`project_root` (default: current working directory), matching how the Skill
is actually invoked (Claude Code's Bash tool runs in the RTL project's cwd).

**History**: Plan §5.1 defined exactly 7 CRUD functions ("CRUD 계약만 고정")
covering only the Fix Sub-cycle proper (debug/fix-plan/fix-design mentioned
in schema but not wired/fix-implement/fix-review). Driving `/sim run` and
`/sim analyze` end-to-end (2026-07-22) found two more gaps the Plan's schema
already implied but never gave a function for: `record_run`/`record_analyze`
(basic run/analyze result recording) and `write_fix_design`/
`ratify_fix_design` (the `origin_chain.fix_design` ADR transition, previously
left as a documented-but-unfixed gap here). All four now exist below —
`origin_chain`'s full 7 fields (run/analyze/debug/fix_plan/fix_design/
fix_implement/fix_review) each have a writer function.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_RELATIVE_PATH = ".ai/sim-state.json"
VALID_FIX_TARGETS = ("rtl", "tb")
VALID_IMPLEMENTERS = ("verilog-rtl-coder", "verilog-tb-coder", "human")
VALID_VERDICTS = ("clean", "issues_found")

# Structural defense against a real incident (2026-07-22): on Windows,
# Git-Bash/MSYS auto-translates any `/`-leading argv value into a Windows
# path before the argv ever reaches this script — e.g. the remote path
# "/usrdata/hoseung.lee/..." silently became "C:/Program Files/Git/usrdata/
# hoseung.lee/...". This corrupted sim_dir/dump_path is never legitimate:
# this project's remote sim-server is always Linux, so a drive-letter
# prefix on a value that's supposed to be a remote path can only mean the
# shell mangled it. Fail loudly here instead of silently persisting
# corrupted state — this check is what makes the fix structural rather
# than "remember to set MSYS_NO_PATHCONV=1 every time" (still documented in
# SKILL.md as the actual fix; this is the safety net for when someone
# doesn't, or a different shell has the same problem).
_WINDOWS_DRIVE_RE = re.compile(r'^[A-Za-z]:[\\/]')


def _reject_if_msys_mangled(label: str, value: str) -> None:
    if value and _WINDOWS_DRIVE_RE.match(value):
        raise ValueError(
            f"{label}={value!r} looks like a Windows-drive-letter path, but remote "
            "sim_dir/dump_path must be Unix paths on the (Linux) sim-server. This is "
            "almost certainly Git-Bash/MSYS auto-converting a '/'-leading argument — "
            "re-run with MSYS_NO_PATHCONV=1 set (see SKILL.md)."
        )

__all__ = [
    "record_run",
    "record_analyze",
    "record_regression",
    "append_debug_note",
    "write_fix_plan",
    "approve_fix_plan",
    "supersede_fix_plan",
    "hold_fix_plan",
    "write_fix_design",
    "ratify_fix_design",
    "record_fix_implement",
    "append_fix_review_note",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _state_path(project_root: str) -> Path:
    return Path(project_root) / STATE_RELATIVE_PATH


def _load_state(project_root: str) -> dict:
    path = _state_path(project_root)
    if not path.exists():
        return {"version": "1.0", "backend": "xcelium-mcp", "sim_dir": "", "tests": {}, "regression": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(project_root: str, state: dict) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _empty_origin_chain(test: str) -> dict:
    base = f".ai/sim-state/{test}"
    return {
        "run": {},
        "analyze": {},
        "debug": {"path": f"{base}/debug.md", "iteration_count": 0, "updated_at": None},
        "fix_plan": {"path": f"{base}/fix-plan.md", "fix_target": None, "status": "pending",
                     "revision_count": 0, "approved_at": None},
        "fix_design": None,
        "fix_implement": {"implementer": None, "files_changed": [], "report": "", "revision_count": 0},
        "fix_review": {"path": f"{base}/fix-review.md", "status": "pending",
                        "iteration_count": 0, "updated_at": None},
    }


def _get_test_entry(state: dict, test: str) -> dict:
    tests = state.setdefault("tests", {})
    entry = tests.setdefault(test, {"phase": "idle", "result": None, "origin_chain": _empty_origin_chain(test)})
    entry.setdefault("origin_chain", _empty_origin_chain(test))
    return entry


def _companion_path(project_root: str, rel_path: str) -> Path:
    return Path(project_root) / rel_path


def _set_sim_dir(state: dict, sim_dir: str) -> None:
    """Validate + persist `sim_dir` metadata — the single choke point every
    public function goes through, so the MSYS-mangling guard only needs to
    live in one place (see `_reject_if_msys_mangled` above)."""
    _reject_if_msys_mangled("sim_dir", sim_dir)
    state["sim_dir"] = sim_dir


def _append_section(doc_path: Path, header: str, body: str) -> None:
    """Append `## {header}\\n\\n{body}\\n\\n` to doc_path (create if missing)."""
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not doc_path.exists() or not doc_path.read_text(encoding="utf-8").strip() else "\n"
    with doc_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}## {header}\n\n{body.rstrip()}\n")


# ---------------------------------------------------------------------------
# run — Plan §5.1 schema has top-level phase/result/dump_path/log_summary +
# origin_chain.run, but Plan's own 7-function CRUD list (§5.1/§5.8) never
# actually defined how those fields get written — it only covers the Fix
# Sub-cycle (debug/fix-plan/fix-design/fix-implement/fix-review), which all
# come AFTER a run+analyze already happened. Found this gap by actually
# driving `/sim run TOP015` end-to-end (2026-07-22) — SKILL.md's own "run"
# step 3 ("Skill이 compound 반환값으로 sim-state.json 갱신") had nothing to
# call. Added as the 8th function, same CRUD-only spirit as the other 7.
# ---------------------------------------------------------------------------

def record_run(sim_dir: str, test: str, status: str, log_summary: str,
                dump_path: str = "", project_root: str = ".") -> None:
    """Record a `/sim run` (or `--regression`) result's CompoundResult.

    Sets the top-level "current state" fields (`phase="run"`, `result`,
    `dump_path`, `log_summary`) AND `origin_chain.run` (the immutable record
    of what this run produced, referenced later by `/sim analyze`/`/sim
    debug` regardless of how many times `phase`/`result` get overwritten by
    later runs). `run`/`analyze` are deterministic derived data (re-running
    the same dump reproduces the same CSV), not accumulated prose — so unlike
    debug/fix-plan/fix-review, this has no companion git-tracked `.md` file
    and simply overwrites (Plan §5.1 "어느 phase가 JSON=포인터, MD=문서 패턴을
    쓰는가").
    """
    _reject_if_msys_mangled("dump_path", dump_path)
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    entry["phase"] = "run"
    entry["result"] = status
    entry["dump_path"] = dump_path
    entry["log_summary"] = log_summary
    entry["origin_chain"]["run"] = {"dump_path": dump_path, "log": log_summary}
    _save_state(project_root, state)


def record_analyze(sim_dir: str, test: str, csv_path: str,
                    anomaly_time_ns: int | None = None,
                    fail_signals: list[str] | None = None,
                    fail_type: str | None = None,
                    project_root: str = ".") -> None:
    """Record a `/sim analyze` result — same gap/rationale as `record_run`
    (found while actually driving `/sim analyze TOP015`, 2026-07-22).

    Sets `phase="analyze"`, `csv_path`, and optionally `fail_signals`/
    `fail_type` (Plan §5.4 FAIL 유형 자동 분류) at the top level, plus
    `origin_chain.analyze={csv_path, anomaly_time_ns}`. Like `record_run`,
    overwrites rather than accumulates (deterministic derived data, not
    prose) and does not touch `result`/`dump_path` (those stay whatever the
    prior `record_run` call set — analyze doesn't re-run the simulation).
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    entry["phase"] = "analyze"
    entry["csv_path"] = csv_path
    if fail_signals is not None:
        entry["fail_signals"] = fail_signals
    if fail_type is not None:
        entry["fail_type"] = fail_type
    entry["origin_chain"]["analyze"] = {"csv_path": csv_path, "anomaly_time_ns": anomaly_time_ns}
    _save_state(project_root, state)


_REGRESSION_RATIO_RE = re.compile(r'(\d+/\d+) (?:verdict tests PASS|waveform tests COMPLETE)')


def record_regression(sim_dir: str, test_list: list[str], log_summary: str,
                       fail_tests: list[str] | None = None,
                       per_test_verdicts: dict[str, str] | None = None,
                       project_root: str = ".") -> None:
    """Record a `/sim run --regression` (or `/sim verify --regression`) result
    into the top-level `regression` field (Plan §5.1: {"last_run", "pass_rate",
    "fail_tests"}) — same gap/rationale as `record_run`/`record_analyze`,
    found while actually driving a live 2-test regression (2026-07-22).

    This is a project-wide summary, NOT a per-test phase transition — it does
    not touch any individual test's `origin_chain`/`phase` (those still need
    their own `record_run`/`record_analyze`/... calls if per-test tracking is
    wanted; `test_list` here is recorded only as context for what was run).

    `pass_rate` is parsed from `log_summary`'s own "N/M verdict tests PASS" /
    "N/M waveform tests COMPLETE" line — the same text `compound.py`'s
    `_classify_regression_status()` already parses to decide PASS/FAIL/
    PARTIAL, kept here as a display string, not re-classified.

    `fail_tests`: pass explicitly if the caller already knows which tests
    failed some other way (e.g. per-test `/sim analyze` follow-ups) — this
    always wins over `per_test_verdicts` when both are given.

    `per_test_verdicts` (xcelium-mcp F-190): `sim_regression_summary`'s
    CompoundResult now exposes `details["per_test_verdicts"]`
    (`{test_name: "pass"|"fail"|"complete"|"error"}`) — pass that dict
    straight through here and `fail_tests` is auto-derived as every test
    verdict-marked "fail"/"error", instead of being left empty. Before F-190,
    `sim_regression_summary` didn't expose which specific tests failed at
    all, so `fail_tests` here defaulted to empty unless the caller guessed
    some other way.

    If neither is given, `fail_tests` defaults to empty (unknown), same as
    before F-190.
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    m = _REGRESSION_RATIO_RE.search(log_summary)
    if fail_tests is not None:
        resolved_fail_tests = fail_tests
    elif per_test_verdicts:
        resolved_fail_tests = [
            tn for tn in test_list if per_test_verdicts.get(tn) in ("fail", "error")
        ]
    else:
        resolved_fail_tests = []
    state["regression"] = {
        "last_run": _now_iso(),
        "pass_rate": m.group(1) if m else None,
        "test_list": test_list,
        "fail_tests": resolved_fail_tests,
    }
    _save_state(project_root, state)


# ---------------------------------------------------------------------------
# 0) debug — 조사 노트 (Plan §5.8 "0)")
# ---------------------------------------------------------------------------

def append_debug_note(sim_dir: str, test: str, note: str, context: str,
                       project_root: str = ".") -> None:
    """Append an investigation note to `.ai/sim-state/{test}/debug.md`.

    Header format: `## Iteration {N} -- {context} ({timestamp})`. Never
    overwrites — debug.md is a pure append-only investigation log (Plan §5.8
    "0) debug"). Does not change `phase`.
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    debug = entry["origin_chain"]["debug"]
    debug["iteration_count"] += 1
    now = _now_iso()
    debug["updated_at"] = now
    header = f"Iteration {debug['iteration_count']} -- {context} ({now})"
    _append_section(_companion_path(project_root, debug["path"]), header, note)
    _save_state(project_root, state)


# ---------------------------------------------------------------------------
# 1) fix-plan (Plan §5.8 "1)")
# ---------------------------------------------------------------------------

_FIX_TARGET_LINE_PREFIX = "fix_target:"


def _extract_fix_target(content: str) -> str | None:
    """Parse a `fix_target: rtl|tb` line from fix-plan.md content (§5.8 "1)"
    필수 포함 항목 — the document itself is the source of truth; this JSON
    field is derived from it, not supplied as a separate parameter)."""
    for line in content.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(_FIX_TARGET_LINE_PREFIX):
            value = stripped[len(_FIX_TARGET_LINE_PREFIX):].strip()
            if value in VALID_FIX_TARGETS:
                return value
    return None


def write_fix_plan(sim_dir: str, test: str, content: str, project_root: str = ".") -> None:
    """Write/revise `.ai/sim-state/{test}/fix-plan.md` (overwrites — a revision
    loop edits the same file, it does not create a new one).

    `fix_target` is parsed out of `content` itself (a `fix_target: rtl|tb`
    line, per fix-plan-template.md) rather than taken as a separate argument.
    `status` resets to "pending" (fresh write or re-submission after a
    revision request both need re-approval). `revision_count` starts at 0 on
    the first write and increments on every subsequent write to the same test.
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    fix_plan = entry["origin_chain"]["fix_plan"]
    is_first_write = fix_plan.get("status") == "pending" and fix_plan.get("revision_count", 0) == 0 \
        and not _companion_path(project_root, fix_plan["path"]).exists()
    fix_plan["revision_count"] = 0 if is_first_write else fix_plan.get("revision_count", 0) + 1
    fix_plan["status"] = "pending"
    fix_plan["approved_at"] = None
    fix_target = _extract_fix_target(content)
    if fix_target is not None:
        fix_plan["fix_target"] = fix_target
    entry["phase"] = "fix-plan"
    doc_path = _companion_path(project_root, fix_plan["path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    _save_state(project_root, state)


def approve_fix_plan(sim_dir: str, test: str, project_root: str = ".") -> None:
    """origin_chain.fix_plan.status: "pending" -> "approved", approved_at set.

    Advances phase to "fix-implement" — the LOCAL/IFACE (no fix-design)
    direct path. If the coder's A0 gate later escalates to ARCH, the Skill
    sets phase="fix-design" and origin_chain.fix_design directly (see module
    docstring "Known gap" — no dedicated function for that transition).
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    fix_plan = entry["origin_chain"]["fix_plan"]
    fix_plan["status"] = "approved"
    fix_plan["approved_at"] = _now_iso()
    entry["phase"] = "fix-implement"
    _save_state(project_root, state)


def supersede_fix_plan(sim_dir: str, test: str, project_root: str = ".") -> None:
    """origin_chain.fix_plan.status -> "superseded", phase back to "run".

    Does not touch origin_chain.debug/debug.md (investigation notes stay
    valid across a superseded plan — Plan §5.1 "debug가 문서 포인터인 이유").
    Does not delete fix-plan.md/fix-design.md — only the status flips; git
    history of the file itself is the record.
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    entry["origin_chain"]["fix_plan"]["status"] = "superseded"
    entry["phase"] = "run"
    _save_state(project_root, state)


def hold_fix_plan(sim_dir: str, test: str, project_root: str = ".") -> None:
    """No-op — explicitly documents "nothing changes" (phase stays "fix-plan").

    Intentionally does not read or write sim-state.json at all: "hold" means
    the session ends with state exactly as it already is.
    """
    return None


# ---------------------------------------------------------------------------
# 2) fix-design (Plan §5.8 "2)") — conditional, ARCH-classification only.
# Previously the one origin_chain field with no writer (module docstring
# "History"). LOCAL/IFACE-classified fixes skip this phase entirely and go
# straight from fix-plan to fix-implement — never call these two.
# ---------------------------------------------------------------------------

def write_fix_design(sim_dir: str, test: str, content: str, project_root: str = ".") -> None:
    """Write `.ai/sim-state/{test}/fix-design.md` — the ADR `verilog-rtl-
    architect-advisor` produces after the coder's A0 gate escalates a
    fix-plan to ARCH (new FSM/module/instance/case-arm, clock/reset re-wire).

    Sets `phase="fix-design"` and `origin_chain.fix_design={"path", ...,
    "ratified_at": None}` — pending the user's re-approval (`ratify_fix_
    design`) before `fix-implement` can resume. Overwrites on revision, same
    as `write_fix_plan` (the ADR is a single evolving document, not an
    append-only log).
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    path = f".ai/sim-state/{test}/fix-design.md"
    entry["origin_chain"]["fix_design"] = {"path": path, "ratified_at": None}
    entry["phase"] = "fix-design"
    doc_path = _companion_path(project_root, path)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    _save_state(project_root, state)


def ratify_fix_design(sim_dir: str, test: str, project_root: str = ".") -> None:
    """User re-approves the ADR (`origin_chain.fix_design.ratified_at` set).

    Resumes `phase="fix-implement"` so the coder can continue implementing
    against the now-ratified partitioning decision. Raises `ValueError` if
    `write_fix_design` was never called for this test (nothing to ratify) —
    unlike `approve_fix_plan`, which always has a fix-plan to act on by the
    time it's reachable, fix-design is conditional and may genuinely be
    skipped (LOCAL/IFACE), so a missing entry here is a caller bug worth
    catching rather than silently no-op'ing.
    """
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    fix_design = entry["origin_chain"].get("fix_design")
    if fix_design is None:
        raise ValueError(
            f"no fix-design.md recorded for test {test!r} — call write_fix_design() first "
            "(this phase is ARCH-only; skip it entirely for LOCAL/IFACE fixes)"
        )
    fix_design["ratified_at"] = _now_iso()
    entry["phase"] = "fix-implement"
    _save_state(project_root, state)


# ---------------------------------------------------------------------------
# 3) fix-implement (Plan §5.8 "3)")
# ---------------------------------------------------------------------------

def record_fix_implement(sim_dir: str, test: str, implementer: str,
                          files_changed: list[str], report: str,
                          project_root: str = ".") -> None:
    """Record who implemented the fix and what changed.

    `implementer`: "verilog-rtl-coder" | "verilog-tb-coder" | "human".
    Does not advance phase to "run" — fix-review must run next regardless of
    implementer (Plan §5.8 "4) fix-review").
    """
    if implementer not in VALID_IMPLEMENTERS:
        raise ValueError(f"implementer must be one of {VALID_IMPLEMENTERS}, got {implementer!r}")
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    fix_impl = entry["origin_chain"]["fix_implement"]
    fix_impl["implementer"] = implementer
    fix_impl["files_changed"] = files_changed
    fix_impl["report"] = report
    entry["phase"] = "fix-implement"
    _save_state(project_root, state)


# ---------------------------------------------------------------------------
# 4) fix-review (Plan §5.8 "4)")
# ---------------------------------------------------------------------------

def append_fix_review_note(sim_dir: str, test: str, note: str, verdict: str,
                            project_root: str = ".") -> None:
    """Append a review round to `.ai/sim-state/{test}/fix-review.md`.

    `verdict`: "clean" (all applicable checks passed) or "issues_found"
    (STATIC-CONFIRMED / formal counterexample / tb-reviewer issues_found —
    all three collapse to this one value, `note` records which).

    For a human implementer, `note` MUST already be the human-facing summary
    (findings + suggested improvement) the Skill is about to echo verbatim in
    chat — this function's append and that chat output must be the exact same
    text ("기록=출력" 동일성, Plan §5.8 "4) fix-review" "구현 주체=사람일 때
    findings 전달"). This function does not compose that summary itself —
    the caller (Skill) does, then passes the final text here.

    `verdict == "issues_found"` -> fix_implement.revision_count += 1, phase
    back to "fix-implement". `verdict == "clean"` -> phase to "run" (the only
    exit from this gate).
    """
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")
    state = _load_state(project_root)
    _set_sim_dir(state, sim_dir)
    entry = _get_test_entry(state, test)
    fix_review = entry["origin_chain"]["fix_review"]
    fix_review["iteration_count"] += 1
    now = _now_iso()
    fix_review["updated_at"] = now
    fix_review["status"] = verdict
    header = f"Review {fix_review['iteration_count']} -- {verdict} ({now})"
    _append_section(_companion_path(project_root, fix_review["path"]), header, note)

    if verdict == "issues_found":
        entry["origin_chain"]["fix_implement"]["revision_count"] += 1
        entry["phase"] = "fix-implement"
    else:
        entry["phase"] = "run"
    _save_state(project_root, state)


# ---------------------------------------------------------------------------
# CLI dispatch — invoked by the Skill via the Bash tool, e.g.:
#   echo "note text" | python3 sim_state.py append_debug_note \
#       --sim-dir /remote/sim --test TOP015 --context "최초 조사"
# Free-text arguments (note/content/report) are read from stdin so the Skill
# never has to shell-escape multi-line Markdown through argv.
# ---------------------------------------------------------------------------

def _read_stdin_text() -> str:
    return sys.stdin.read()


def _cli_record_run(args: argparse.Namespace) -> None:
    log_summary = args.log_summary if args.log_summary is not None else _read_stdin_text()
    record_run(args.sim_dir, args.test, args.status, log_summary,
               dump_path=args.dump_path, project_root=args.project_root)


def _cli_record_analyze(args: argparse.Namespace) -> None:
    record_analyze(args.sim_dir, args.test, args.csv_path,
                   anomaly_time_ns=args.anomaly_time_ns,
                   fail_signals=args.fail_signals or None,
                   fail_type=args.fail_type or None,
                   project_root=args.project_root)


def _cli_record_regression(args: argparse.Namespace) -> None:
    log_summary = args.log_summary if args.log_summary is not None else _read_stdin_text()
    per_test_verdicts = json.loads(args.per_test_verdicts) if args.per_test_verdicts else None
    record_regression(args.sim_dir, args.test_list, log_summary,
                       fail_tests=args.fail_tests or None,
                       per_test_verdicts=per_test_verdicts, project_root=args.project_root)


def _cli_append_debug_note(args: argparse.Namespace) -> None:
    append_debug_note(args.sim_dir, args.test, _read_stdin_text(), args.context,
                       project_root=args.project_root)


def _cli_write_fix_plan(args: argparse.Namespace) -> None:
    write_fix_plan(args.sim_dir, args.test, _read_stdin_text(), project_root=args.project_root)


def _cli_approve_fix_plan(args: argparse.Namespace) -> None:
    approve_fix_plan(args.sim_dir, args.test, project_root=args.project_root)


def _cli_supersede_fix_plan(args: argparse.Namespace) -> None:
    supersede_fix_plan(args.sim_dir, args.test, project_root=args.project_root)


def _cli_hold_fix_plan(args: argparse.Namespace) -> None:
    hold_fix_plan(args.sim_dir, args.test, project_root=args.project_root)


def _cli_write_fix_design(args: argparse.Namespace) -> None:
    write_fix_design(args.sim_dir, args.test, _read_stdin_text(), project_root=args.project_root)


def _cli_ratify_fix_design(args: argparse.Namespace) -> None:
    ratify_fix_design(args.sim_dir, args.test, project_root=args.project_root)


def _cli_record_fix_implement(args: argparse.Namespace) -> None:
    report = args.report if args.report is not None else _read_stdin_text()
    record_fix_implement(args.sim_dir, args.test, args.implementer,
                          args.files_changed or [], report, project_root=args.project_root)


def _cli_append_fix_review_note(args: argparse.Namespace) -> None:
    append_fix_review_note(args.sim_dir, args.test, _read_stdin_text(), args.verdict,
                            project_root=args.project_root)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sim_state.py",
        description="sim-state.json CRUD (Plan §5.1) - client-local, stdlib only",
    )
    parser.add_argument("--project-root", default=".", help="RTL project root (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("record_run")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--status", required=True, help='e.g. "PASS"/"FAIL"/"ERROR"/"PARTIAL"')
    p.add_argument("--dump-path", default="")
    p.add_argument("--log-summary", default=None, help="Omit to read from stdin")
    p.set_defaults(func=_cli_record_run)

    p = sub.add_parser("record_analyze")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--csv-path", required=True)
    p.add_argument("--anomaly-time-ns", type=int, default=None)
    p.add_argument("--fail-signals", nargs="*", default=None)
    p.add_argument("--fail-type", default=None,
                    help='e.g. "data_mismatch"/"timeout"/"assertion"/"protocol"')
    p.set_defaults(func=_cli_record_analyze)

    p = sub.add_parser("record_regression")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test-list", nargs="+", required=True)
    p.add_argument("--fail-tests", nargs="*", default=None)
    p.add_argument("--per-test-verdicts", default=None,
                    help='JSON dict, e.g. \'{"T1":"pass","T2":"fail"}\' -- from '
                         'sim_regression_summary\'s details.per_test_verdicts (F-190). '
                         "Ignored if --fail-tests is also given.")
    p.add_argument("--log-summary", default=None, help="Omit to read from stdin")
    p.set_defaults(func=_cli_record_regression)

    p = sub.add_parser("append_debug_note")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--context", required=True)
    p.set_defaults(func=_cli_append_debug_note)

    p = sub.add_parser("write_fix_plan")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_write_fix_plan)

    p = sub.add_parser("approve_fix_plan")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_approve_fix_plan)

    p = sub.add_parser("supersede_fix_plan")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_supersede_fix_plan)

    p = sub.add_parser("hold_fix_plan")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_hold_fix_plan)

    p = sub.add_parser("write_fix_design")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_write_fix_design)

    p = sub.add_parser("ratify_fix_design")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.set_defaults(func=_cli_ratify_fix_design)

    p = sub.add_parser("record_fix_implement")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--implementer", required=True, choices=VALID_IMPLEMENTERS)
    p.add_argument("--files-changed", nargs="*", default=[])
    p.add_argument("--report", default=None, help="Omit to read from stdin")
    p.set_defaults(func=_cli_record_fix_implement)

    p = sub.add_parser("append_fix_review_note")
    p.add_argument("--sim-dir", required=True)
    p.add_argument("--test", required=True)
    p.add_argument("--verdict", required=True, choices=VALID_VERDICTS)
    p.set_defaults(func=_cli_append_fix_review_note)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
