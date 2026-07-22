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


class TestMsysMangledPathGuard:
    """Structural defense against the 2026-07-22 incident: Git-Bash/MSYS
    silently rewrites '/'-leading argv values into Windows paths."""

    def test_record_run_rejects_mangled_sim_dir(self, tmp_path):
        with pytest.raises(ValueError, match="MSYS_NO_PATHCONV"):
            sim_state.record_run(
                "C:/Program Files/Git/usrdata/hoseung.lee/sim", "TOP015", "PASS", "log",
                project_root=str(tmp_path),
            )
        assert not (tmp_path / sim_state.STATE_RELATIVE_PATH).exists()

    def test_record_run_rejects_mangled_dump_path(self, tmp_path):
        with pytest.raises(ValueError, match="MSYS_NO_PATHCONV"):
            sim_state.record_run(
                "/remote/sim", "TOP015", "PASS", "log",
                dump_path="C:/Program Files/Git/usrdata/dump/ci_top.shm",
                project_root=str(tmp_path),
            )

    def test_normal_unix_path_is_accepted(self, tmp_path):
        # Should not raise.
        sim_state.record_run("/usrdata/hoseung.lee/sim", "TOP015", "PASS", "log",
                              dump_path="/usrdata/hoseung.lee/sim/dump/ci_top.shm",
                              project_root=str(tmp_path))

    def test_guard_applies_to_every_public_function_via_set_sim_dir(self, tmp_path):
        """Spot-check a non-run function to confirm the guard is centralized,
        not just wired into record_run."""
        with pytest.raises(ValueError, match="MSYS_NO_PATHCONV"):
            sim_state.append_debug_note("C:/Program Files/Git/remote/sim", "TOP015", "note",
                                         "최초 조사", project_root=str(tmp_path))

    def test_cli_prints_clean_error_and_exits_1(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "sim_state.py", "--project-root", str(tmp_path),
            "record_run", "--sim-dir", "C:/Program Files/Git/remote/sim",
            "--test", "TOP015", "--status", "PASS", "--log-summary", "log",
        ])
        exit_code = sim_state.main()
        assert exit_code == 1
        assert "MSYS_NO_PATHCONV" in capsys.readouterr().err


class TestRecordRun:
    def test_pass_sets_phase_and_top_level_fields(self, tmp_path):
        sim_state.record_run("/remote/sim", "TOP015", "PASS", "Simulation complete via $finish",
                              dump_path="/dump/ci_top.shm", project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "run"
        assert entry["result"] == "PASS"
        assert entry["dump_path"] == "/dump/ci_top.shm"
        assert entry["log_summary"] == "Simulation complete via $finish"
        assert entry["origin_chain"]["run"] == {
            "dump_path": "/dump/ci_top.shm", "log": "Simulation complete via $finish",
        }

    def test_second_run_overwrites_not_appends(self, tmp_path):
        """run/analyze are deterministic derived data, not accumulated prose — unlike debug.md."""
        sim_state.record_run("/remote/sim", "TOP015", "FAIL", "first attempt log",
                              dump_path="/dump/v1.shm", project_root=str(tmp_path))
        sim_state.record_run("/remote/sim", "TOP015", "PASS", "second attempt log",
                              dump_path="/dump/v2.shm", project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["result"] == "PASS"
        assert entry["dump_path"] == "/dump/v2.shm"
        assert "first attempt" not in json.dumps(state)

    def test_via_cli_with_log_summary_flag(self, tmp_path):
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "record_run", "--sim-dir", "/remote/sim", "--test", "TOP015",
            "--status", "PASS", "--dump-path", "/dump/ci_top.shm",
            "--log-summary", "done via --log-summary flag",
        ])
        args.func(args)

        state = _state_json(tmp_path)
        assert state["tests"]["TOP015"]["log_summary"] == "done via --log-summary flag"


class TestRecordAnalyze:
    def test_sets_phase_and_csv_path(self, tmp_path):
        sim_state.record_run("/remote/sim", "TOP015", "PASS", "log", dump_path="/dump/ci_top.shm",
                              project_root=str(tmp_path))
        sim_state.record_analyze("/remote/sim", "TOP015", "/tmp/mcp_csv_ci_top.csv",
                                  project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "analyze"
        assert entry["csv_path"] == "/tmp/mcp_csv_ci_top.csv"
        assert entry["origin_chain"]["analyze"] == {
            "csv_path": "/tmp/mcp_csv_ci_top.csv", "anomaly_time_ns": None,
        }
        # analyze must not clobber the prior run's own fields
        assert entry["dump_path"] == "/dump/ci_top.shm"
        assert entry["result"] == "PASS"

    def test_fail_signals_and_fail_type_recorded_when_given(self, tmp_path):
        sim_state.record_analyze("/remote/sim", "TOP015", "/tmp/out.csv",
                                  anomaly_time_ns=8318143,
                                  fail_signals=["top.hw.u_ext.r_regAddr"],
                                  fail_type="data_mismatch",
                                  project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["fail_signals"] == ["top.hw.u_ext.r_regAddr"]
        assert entry["fail_type"] == "data_mismatch"
        assert entry["origin_chain"]["analyze"]["anomaly_time_ns"] == 8318143

    def test_omitted_fail_fields_not_added(self, tmp_path):
        sim_state.record_analyze("/remote/sim", "TOP015", "/tmp/out.csv", project_root=str(tmp_path))

        entry = _state_json(tmp_path)["tests"]["TOP015"]
        assert "fail_signals" not in entry
        assert "fail_type" not in entry

    def test_via_cli(self, tmp_path):
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "record_analyze", "--sim-dir", "/remote/sim", "--test", "TOP015",
            "--csv-path", "/tmp/out.csv", "--fail-type", "assertion",
            "--fail-signals", "sig.a", "sig.b",
        ])
        args.func(args)

        entry = _state_json(tmp_path)["tests"]["TOP015"]
        assert entry["phase"] == "analyze"
        assert entry["fail_type"] == "assertion"
        assert entry["fail_signals"] == ["sig.a", "sig.b"]


class TestRecordRegression:
    def test_parses_verdict_pass_rate(self, tmp_path):
        sim_state.record_regression(
            "/remote/sim", ["T1", "T2"], "1/2 verdict tests PASS (5 checks passed, 1 failed)",
            project_root=str(tmp_path),
        )
        regression = _state_json(tmp_path)["regression"]
        assert regression["pass_rate"] == "1/2"
        assert regression["test_list"] == ["T1", "T2"]
        assert regression["fail_tests"] == []
        assert regression["last_run"] is not None

    def test_parses_waveform_complete_pass_rate(self, tmp_path):
        """The fallback form (no explicit verdict), same regex used by
        compound.py's own _classify_regression_status()."""
        sim_state.record_regression(
            "/remote/sim", ["T1", "T2"], "2/2 waveform tests COMPLETE",
            project_root=str(tmp_path),
        )
        assert _state_json(tmp_path)["regression"]["pass_rate"] == "2/2"

    def test_no_parseable_ratio_yields_none(self, tmp_path):
        sim_state.record_regression("/remote/sim", ["T1"], "some unrelated log text",
                                     project_root=str(tmp_path))
        assert _state_json(tmp_path)["regression"]["pass_rate"] is None

    def test_fail_tests_recorded_when_given(self, tmp_path):
        sim_state.record_regression("/remote/sim", ["T1", "T2"], "1/2 verdict tests PASS",
                                     fail_tests=["T2"], project_root=str(tmp_path))
        assert _state_json(tmp_path)["regression"]["fail_tests"] == ["T2"]

    def test_does_not_touch_per_test_entries(self, tmp_path):
        """regression is a project-wide summary — it must not create/modify tests[]."""
        sim_state.record_run("/remote/sim", "T1", "PASS", "log", project_root=str(tmp_path))
        sim_state.record_regression("/remote/sim", ["T1", "T2"], "2/2 waveform tests COMPLETE",
                                     project_root=str(tmp_path))

        state = _state_json(tmp_path)
        assert list(state["tests"]) == ["T1"]  # T2 never got its own entry
        assert state["tests"]["T1"]["phase"] == "run"  # untouched by the regression call

    def test_via_cli(self, tmp_path):
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "record_regression", "--sim-dir", "/remote/sim",
            "--test-list", "T1", "T2", "--log-summary", "2/2 waveform tests COMPLETE",
        ])
        args.func(args)

        regression = _state_json(tmp_path)["regression"]
        assert regression["pass_rate"] == "2/2"
        assert regression["test_list"] == ["T1", "T2"]


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


class TestFixDesignLifecycle:
    """origin_chain.fix_design — the one field with no writer until 2026-07-22."""

    def test_write_fix_design_sets_phase_and_pending_ratification(self, tmp_path):
        sim_state.write_fix_design("/remote/sim", "TOP015", "# ADR\n\nNew FSM state needed.",
                                    project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "fix-design"
        fix_design = entry["origin_chain"]["fix_design"]
        assert fix_design["ratified_at"] is None
        doc = (tmp_path / fix_design["path"]).read_text(encoding="utf-8")
        assert "New FSM state needed" in doc

    def test_ratify_sets_timestamp_and_resumes_fix_implement(self, tmp_path):
        sim_state.write_fix_design("/remote/sim", "TOP015", "# ADR", project_root=str(tmp_path))
        sim_state.ratify_fix_design("/remote/sim", "TOP015", project_root=str(tmp_path))

        state = _state_json(tmp_path)
        entry = state["tests"]["TOP015"]
        assert entry["phase"] == "fix-implement"
        assert entry["origin_chain"]["fix_design"]["ratified_at"] is not None

    def test_ratify_without_prior_write_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no fix-design.md recorded"):
            sim_state.ratify_fix_design("/remote/sim", "TOP015", project_root=str(tmp_path))

    def test_revision_overwrites_not_appends(self, tmp_path):
        sim_state.write_fix_design("/remote/sim", "TOP015", "draft 1", project_root=str(tmp_path))
        sim_state.write_fix_design("/remote/sim", "TOP015", "draft 2 (revised)", project_root=str(tmp_path))

        fix_design = _state_json(tmp_path)["tests"]["TOP015"]["origin_chain"]["fix_design"]
        doc = (tmp_path / fix_design["path"]).read_text(encoding="utf-8")
        assert doc.strip() == "draft 2 (revised)"
        # re-writing resets ratification — the revised ADR needs re-approval
        assert fix_design["ratified_at"] is None

    def test_via_cli(self, tmp_path, monkeypatch):
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO("# ADR via CLI"))
        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "write_fix_design", "--sim-dir", "/remote/sim", "--test", "TOP015",
        ])
        args.func(args)

        parser = sim_state._build_parser()
        args = parser.parse_args([
            "--project-root", str(tmp_path),
            "ratify_fix_design", "--sim-dir", "/remote/sim", "--test", "TOP015",
        ])
        args.func(args)

        entry = _state_json(tmp_path)["tests"]["TOP015"]
        assert entry["phase"] == "fix-implement"


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
