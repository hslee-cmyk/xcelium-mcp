"""Tests for sim_post_compound.py / sim_prompt_detect.py (Plan §6, Phase D).

Standalone from the xcelium-mcp pip package's tests/ suite, same reasoning as
scripts/test_sim_state.py (separately-deployed skill asset). Run directly:

    python3 -m pytest skill-src/xcelium-sim/hooks/test_hooks.py -v
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sim_post_compound  # noqa: E402
import sim_prompt_detect  # noqa: E402


def _run_hook(module, payload: dict, monkeypatch, capsys) -> tuple[int, dict | None]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    exit_code = module.main()
    out = capsys.readouterr().out.strip()
    return exit_code, (json.loads(out) if out else None)


class TestSimPostCompound:
    def test_pass_status_suggests_regression(self, monkeypatch, capsys):
        payload = {
            "tool_name": "mcp__xcelium-mcp__sim_run_and_check",
            "tool_output": {"type": "text", "text": "status: PASS\n\nCOMPLETE. Errors: 0"},
        }
        code, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert code == 0
        assert result is not None
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "sim_run_and_check" in ctx
        assert "regression" in ctx

    def test_fail_status_suggests_analyze(self, monkeypatch, capsys):
        payload = {
            "tool_name": "mcp__xcelium-mcp__sim_run_and_check",
            "tool_output": {"type": "text", "text": "status: FAIL\n\nCOMPLETE. Errors: 3"},
        }
        _, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert "/sim analyze" in result["hookSpecificOutput"]["additionalContext"]

    def test_regression_partial_status(self, monkeypatch, capsys):
        payload = {
            "tool_name": "mcp__xcelium-mcp__sim_regression_summary",
            "tool_output": {"type": "text", "text": "status: PARTIAL\n\n1/2 verdict tests PASS"},
        }
        _, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert "실패 테스트" in result["hookSpecificOutput"]["additionalContext"]

    def test_unrelated_tool_name_produces_no_output(self, monkeypatch, capsys):
        payload = {
            "tool_name": "Bash",
            "tool_output": {"type": "text", "text": "status: PASS"},
        }
        code, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert code == 0
        assert result is None

    def test_other_mcp_tool_produces_no_output(self, monkeypatch, capsys):
        payload = {
            "tool_name": "mcp__xcelium-mcp__sim_batch_run",
            "tool_output": {"type": "text", "text": "status: PASS"},
        }
        _, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert result is None

    def test_missing_status_line_produces_no_output(self, monkeypatch, capsys):
        payload = {
            "tool_name": "mcp__xcelium-mcp__sim_run_and_check",
            "tool_output": {"type": "text", "text": "no status line here"},
        }
        _, result = _run_hook(sim_post_compound, payload, monkeypatch, capsys)
        assert result is None

    def test_malformed_stdin_fails_open(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        assert sim_post_compound.main() == 0
        assert capsys.readouterr().out == ""


class TestSimPromptDetect:
    def test_no_keyword_produces_no_output(self, tmp_path, monkeypatch, capsys):
        payload = {"user_input": "let's refactor the login page", "cwd": str(tmp_path)}
        code, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)
        assert code == 0
        assert result is None

    def test_keyword_but_no_state_file_produces_no_output(self, tmp_path, monkeypatch, capsys):
        payload = {"user_input": "let's check the waveform", "cwd": str(tmp_path)}
        _, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)
        assert result is None

    def test_keyword_with_pending_fix_plan_surfaces_context(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / ".ai"
        state_dir.mkdir()
        (state_dir / "sim-state.json").write_text(json.dumps({
            "tests": {"TOP015": {"phase": "fix-plan"}, "TOP020": {"phase": "idle"}}
        }), encoding="utf-8")

        payload = {"user_input": "let's debug the simulation", "cwd": str(tmp_path)}
        _, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)

        assert result is not None
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "TOP015: fix-plan" in ctx
        assert "TOP020" not in ctx  # idle tests are not "pending"

    def test_all_idle_produces_no_output(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / ".ai"
        state_dir.mkdir()
        (state_dir / "sim-state.json").write_text(json.dumps({
            "tests": {"TOP015": {"phase": "idle"}}
        }), encoding="utf-8")

        payload = {"user_input": "regression status?", "cwd": str(tmp_path)}
        _, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)
        assert result is None

    def test_malformed_state_file_fails_open(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / ".ai"
        state_dir.mkdir()
        (state_dir / "sim-state.json").write_text("not json", encoding="utf-8")

        payload = {"user_input": "xcelium debugging", "cwd": str(tmp_path)}
        code, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)
        assert code == 0
        assert result is None

    def test_slash_sim_command_is_a_trigger(self, tmp_path, monkeypatch, capsys):
        state_dir = tmp_path / ".ai"
        state_dir.mkdir()
        (state_dir / "sim-state.json").write_text(json.dumps({
            "tests": {"TOP015": {"phase": "fix-review"}}
        }), encoding="utf-8")

        payload = {"user_input": "/sim run TOP015", "cwd": str(tmp_path)}
        _, result = _run_hook(sim_prompt_detect, payload, monkeypatch, capsys)
        assert result is not None
