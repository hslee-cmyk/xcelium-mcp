"""Tests for sim_state.py (Plan §5.1 CRUD contract, Phase C).

Standalone from the xcelium-mcp pip package's own tests/ suite — this script
is a separately-deployed Claude Code skill asset (skill-src/xcelium-sim/),
not part of src/xcelium_mcp/. Run directly:

    python3 -m pytest skill-src/xcelium-sim/scripts/test_sim_state.py -v

(Not picked up by the package's own `pytest tests/ -v` per CLAUDE.md, which
is scoped to tests/ — intentional, this asset has its own deployment/test
lifecycle, see skill-src/README.md.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import sim_state  # noqa: E402


def _state_json(project_root: Path) -> dict:
    return json.loads((project_root / sim_state.STATE_RELATIVE_PATH).read_text(encoding="utf-8"))


class TestAppendDebugNote:
    def test_first_call_creates_debug_md_and_iteration_1(self, tmp_path):
        sim_state.append_debug_note("/remote/sim", "TOP015", "hypothesis A", "최초 조사",
                                     project_root=str(tmp_path))

        state = _state_json(tmp_path)
        debug = state["tests"]["TOP015"]["origin_chain"]["debug"]
        assert debug["iteration_count"] == 1
        assert debug["updated_at"] is not None

        doc = (tmp_path / debug["path"]).read_text(encoding="utf-8")
        assert "## Iteration 1 -- 최초 조사" in doc
        assert "hypothesis A" in doc

    def test_second_call_appends_without_overwriting(self, tmp_path):
        sim_state.append_debug_note("/remote/sim", "TOP015", "hypothesis A", "최초 조사",
                                     project_root=str(tmp_path))
        sim_state.append_debug_note("/remote/sim", "TOP015", "hypothesis B", "재개 조사(추가 정보 기반)",
                                     project_root=str(tmp_path))

        state = _state_json(tmp_path)
        debug = state["tests"]["TOP015"]["origin_chain"]["debug"]
        assert debug["iteration_count"] == 2

        doc = (tmp_path / debug["path"]).read_text(encoding="utf-8")
        assert "## Iteration 1 -- 최초 조사" in doc
        assert "## Iteration 2 -- 재개 조사" in doc
        assert "hypothesis A" in doc and "hypothesis B" in doc

    def test_does_not_change_phase(self, tmp_path):
        sim_state.append_debug_note("/remote/sim", "TOP015", "note", "최초 조사", project_root=str(tmp_path))
        assert _state_json(tmp_path)["tests"]["TOP015"]["phase"] == "idle"


class TestFixPlanLifecycle:
    def test_write_fix_plan_extracts_fix_target_and_sets_phase(self, tmp_path):
        content = "fix_target: rtl\n\n근본원인: r_regAddr race condition.\n"
        sim_state.write_fix_plan("/remote/sim", "TOP015", content, project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "fix-plan"
        fix_plan = entry["origin_chain"]["fix_plan"]
        assert fix_plan["fix_target"] == "rtl"
        assert fix_plan["status"] == "pending"
        assert fix_plan["revision_count"] == 0

        doc = (tmp_path / fix_plan["path"]).read_text(encoding="utf-8")
        assert "근본원인" in doc

    def test_revision_increments_count_and_resets_status(self, tmp_path):
        sim_state.write_fix_plan("/remote/sim", "TOP015", "fix_target: rtl\ndraft 1", project_root=str(tmp_path))
        sim_state.approve_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))
        # user requests a revision -> write_fix_plan called again on the same test
        sim_state.write_fix_plan("/remote/sim", "TOP015", "fix_target: rtl\ndraft 2 (revised)",
                                  project_root=str(tmp_path))

        state = _state_json(tmp_path)
        fix_plan = state["tests"]["TOP015"]["origin_chain"]["fix_plan"]
        assert fix_plan["revision_count"] == 1
        assert fix_plan["status"] == "pending"  # re-approval required after revision
        assert fix_plan["approved_at"] is None

    def test_approve_sets_status_and_advances_phase(self, tmp_path):
        sim_state.write_fix_plan("/remote/sim", "TOP015", "fix_target: tb\ndraft", project_root=str(tmp_path))
        sim_state.approve_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "fix-implement"
        assert entry["origin_chain"]["fix_plan"]["status"] == "approved"
        assert entry["origin_chain"]["fix_plan"]["approved_at"] is not None

    def test_supersede_resets_phase_but_preserves_debug(self, tmp_path):
        sim_state.append_debug_note("/remote/sim", "TOP015", "hypothesis", "최초 조사", project_root=str(tmp_path))
        sim_state.write_fix_plan("/remote/sim", "TOP015", "fix_target: rtl\ndraft", project_root=str(tmp_path))
        sim_state.supersede_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "run"
        assert entry["origin_chain"]["fix_plan"]["status"] == "superseded"
        assert entry["origin_chain"]["debug"]["iteration_count"] == 1  # untouched

    def test_hold_fix_plan_is_a_true_noop(self, tmp_path):
        """hold_fix_plan must not even create the state file."""
        sim_state.hold_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))
        assert not (tmp_path / sim_state.STATE_RELATIVE_PATH).exists()


class TestFixImplementAndReview:
    def _approved_fixture(self, tmp_path, fix_target="rtl"):
        sim_state.write_fix_plan("/remote/sim", "TOP015", f"fix_target: {fix_target}\ndraft",
                                  project_root=str(tmp_path))
        sim_state.approve_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))

    def test_record_fix_implement_human(self, tmp_path):
        self._approved_fixture(tmp_path)
        sim_state.record_fix_implement("/remote/sim", "TOP015", "human",
                                        ["db/design/ext_i2cSlave.v"], "",
                                        project_root=str(tmp_path))

        state = _state_json(tmp_path)
        fix_impl = state["tests"]["TOP015"]["origin_chain"]["fix_implement"]
        assert fix_impl["implementer"] == "human"
        assert fix_impl["files_changed"] == ["db/design/ext_i2cSlave.v"]

    def test_record_fix_implement_rejects_invalid_implementer(self, tmp_path):
        self._approved_fixture(tmp_path)
        with pytest.raises(ValueError, match="implementer"):
            sim_state.record_fix_implement("/remote/sim", "TOP015", "chatgpt", [], "",
                                            project_root=str(tmp_path))

    def test_fix_review_issues_found_returns_to_fix_implement(self, tmp_path):
        self._approved_fixture(tmp_path)
        sim_state.record_fix_implement("/remote/sim", "TOP015", "verilog-rtl-coder",
                                        ["a.v"], "done", project_root=str(tmp_path))
        sim_state.append_fix_review_note("/remote/sim", "TOP015",
                                          "STATIC-CONFIRMED: off-by-one in FSM", "issues_found",
                                          project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "fix-implement"
        assert entry["origin_chain"]["fix_implement"]["revision_count"] == 1
        assert entry["origin_chain"]["fix_review"]["status"] == "issues_found"

        review_doc = (tmp_path / entry["origin_chain"]["fix_review"]["path"]).read_text(encoding="utf-8")
        assert "## Review 1 -- issues_found" in review_doc
        assert "off-by-one" in review_doc

    def test_fix_review_clean_advances_to_run(self, tmp_path):
        self._approved_fixture(tmp_path)
        sim_state.record_fix_implement("/remote/sim", "TOP015", "verilog-rtl-coder",
                                        ["a.v"], "done", project_root=str(tmp_path))
        sim_state.append_fix_review_note("/remote/sim", "TOP015", "no issues found", "clean",
                                          project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "run"
        assert entry["origin_chain"]["fix_review"]["status"] == "clean"
        # revision_count must NOT increment on a clean verdict
        assert entry["origin_chain"]["fix_implement"]["revision_count"] == 0

    def test_second_review_round_appends_not_overwrites(self, tmp_path):
        self._approved_fixture(tmp_path)
        sim_state.record_fix_implement("/remote/sim", "TOP015", "human", [], "", project_root=str(tmp_path))
        sim_state.append_fix_review_note("/remote/sim", "TOP015", "first round issue", "issues_found",
                                          project_root=str(tmp_path))
        sim_state.record_fix_implement("/remote/sim", "TOP015", "human", ["fixed.v"], "",
                                        project_root=str(tmp_path))
        sim_state.append_fix_review_note("/remote/sim", "TOP015", "clean now", "clean",
                                          project_root=str(tmp_path))

        state = _state_json(tmp_path)
        fix_review = state["tests"]["TOP015"]["origin_chain"]["fix_review"]
        assert fix_review["iteration_count"] == 2
        doc = (tmp_path / fix_review["path"]).read_text(encoding="utf-8")
        assert "## Review 1 -- issues_found" in doc
        assert "## Review 2 -- clean" in doc
        assert "first round issue" in doc and "clean now" in doc


class TestCliDispatch:
    def test_append_debug_note_via_cli(self, tmp_path, monkeypatch, capsys):
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO("note from stdin"))
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "append_debug_note", "--sim-dir", "/remote/sim", "--test", "TOP015", "--context", "최초 조사",
        ])
        args.func(args)

        state = _state_json(tmp_path)
        assert state["tests"]["TOP015"]["origin_chain"]["debug"]["iteration_count"] == 1
        doc_path = tmp_path / state["tests"]["TOP015"]["origin_chain"]["debug"]["path"]
        assert "note from stdin" in doc_path.read_text(encoding="utf-8")

    def test_record_fix_implement_report_flag_skips_stdin(self, tmp_path):
        sim_state.write_fix_plan("/remote/sim", "TOP015", "fix_target: rtl\ndraft", project_root=str(tmp_path))
        sim_state.approve_fix_plan("/remote/sim", "TOP015", project_root=str(tmp_path))
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "record_fix_implement", "--sim-dir", "/remote/sim", "--test", "TOP015",
            "--implementer", "verilog-rtl-coder", "--files-changed", "a.v", "b.v",
            "--report", "done via --report flag",
        ])
        args.func(args)

        state = _state_json(tmp_path)
        fix_impl = state["tests"]["TOP015"]["origin_chain"]["fix_implement"]
        assert fix_impl["report"] == "done via --report flag"
        assert fix_impl["files_changed"] == ["a.v", "b.v"]
