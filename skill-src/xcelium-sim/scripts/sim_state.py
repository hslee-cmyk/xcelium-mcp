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

**Known gap (documented, not silently skipped)**: Plan §5.1 defines exactly
7 CRUD functions and explicitly scopes them as "CRUD 계약만 고정" — none of
them cover recording a `fix-design` ADR ratification (`origin_chain.
fix_design.ratified_at`, entering `phase="fix-design"`). That transition is
therefore out of this module's scope; the Skill must set those two fields
directly (see `_load_state`/`_save_state`, exported for that purpose) until
Plan is revised to add an 8th function.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_RELATIVE_PATH = ".ai/sim-state.json"
VALID_FIX_TARGETS = ("rtl", "tb")
VALID_IMPLEMENTERS = ("verilog-rtl-coder", "verilog-tb-coder", "human")
VALID_VERDICTS = ("clean", "issues_found")

__all__ = [
    "append_debug_note",
    "write_fix_plan",
    "approve_fix_plan",
    "supersede_fix_plan",
    "hold_fix_plan",
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


def _append_section(doc_path: Path, header: str, body: str) -> None:
    """Append `## {header}\\n\\n{body}\\n\\n` to doc_path (create if missing)."""
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not doc_path.exists() or not doc_path.read_text(encoding="utf-8").strip() else "\n"
    with doc_path.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}## {header}\n\n{body.rstrip()}\n")


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
    state["sim_dir"] = sim_dir
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
    state["sim_dir"] = sim_dir
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
    state["sim_dir"] = sim_dir
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
    state["sim_dir"] = sim_dir
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
    state["sim_dir"] = sim_dir
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
    state["sim_dir"] = sim_dir
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


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
