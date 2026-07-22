"""Tests for sim_post_compound.py / sim_prompt_detect.py (Plan §6, Phase D).

Standalone from the xcelium-mcp pip package's tests/ suite, same reasoning as
scripts/test_sim_state.py (separately-deployed skill asset). Run directly:

    python3 -m pytest skill-src/xcelium-sim/hooks/test_hooks.py -v
"""
from __future__ import annotations

import io
import json
import re
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


class TestRegisteredHookCommand:
    """F-188: SKILL.md's `hooks:` frontmatter registers the actual shell command
    Claude Code invokes. The tests above call sim_post_compound.main()/
    sim_prompt_detect.main() directly in-process, so they never exercise that
    command string — a bare `python3 ...` resolves to a broken Windows App
    Execution Alias stub on some machines (this dev machine included) and the
    hook silently never runs. These tests guard the registered string itself.
    """

    def _hook_commands(self) -> list[str]:
        skill_md = Path(__file__).parent.parent / "SKILL.md"
        content = skill_md.read_text(encoding="utf-8")
        frontmatter = content.split("---", 2)[1]
        return re.findall(r'command:\s*"([^"]+)"', frontmatter)

    def test_both_hooks_registered(self):
        commands = self._hook_commands()
        assert len(commands) == 2
        assert any("sim_post_compound.py" in c for c in commands)
        assert any("sim_prompt_detect.py" in c for c in commands)

    def test_each_command_has_a_python_fallback(self):
        """A bare 'python3 <script>' with no fallback is the F-188 regression --
        require a '|| python <script>' (or better) fallback in every registered
        hook command."""
        for cmd in self._hook_commands():
            assert cmd.count("python3") >= 1, f"missing python3 in: {cmd}"
            assert "||" in cmd and re.search(r"\|\|\s*python\b(?!3)", cmd), (
                f"hook command has no python3->python fallback (F-188 regression): {cmd}"
            )
