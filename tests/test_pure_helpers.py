"""Unit tests for pure helper functions — no MCP, no I/O required."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.batch_runner import validate_extra_args
from xcelium_mcp.discovery import (
    _extract_top_module_from_content,
    _parse_ifdef_around_sdf,
)
from xcelium_mcp.shell_utils import (
    _parse_shm_path,
    _parse_time_ns,
    get_ssh_cmd_timeout,
    is_safe_tcl_string,
    sanitize_signal_name,
    shell_run_with_retry,
)

# ---------------------------------------------------------------------------
# _extract_top_module_from_content
# ---------------------------------------------------------------------------

class TestExtractTopModule:
    def test_simple_xmsim(self):
        content = "xmsim -input run.tcl top_module"
        assert _extract_top_module_from_content(content) == "top_module"

    def test_xrun_with_flags(self):
        content = "xrun -access +rwc -input setup.tcl my_top"
        assert _extract_top_module_from_content(content) == "my_top"

    def test_eval_prefix(self):
        content = "eval xmsim -input run.tcl top"
        assert _extract_top_module_from_content(content) == "top"

    def test_backslash_continuation(self):
        content = "xmsim \\\n  -input run.tcl \\\n  top"
        assert _extract_top_module_from_content(content) == "top"

    def test_empty_content(self):
        assert _extract_top_module_from_content("") == ""

    def test_no_match(self):
        assert _extract_top_module_from_content("echo hello") == ""


# ---------------------------------------------------------------------------
# _parse_ifdef_around_sdf
# ---------------------------------------------------------------------------

class TestParseIfdefAroundSdf:
    def test_no_sdf(self):
        result = _parse_ifdef_around_sdf("module top; endmodule")
        assert result["sdf_guard_define"] is None
        assert result["sdf_entries"] == []

    def test_simple_sdf(self):
        content = '$sdf_annotate("timing.sdf", dut);'
        result = _parse_ifdef_around_sdf(content)
        assert len(result["sdf_entries"]) == 1
        assert result["sdf_entries"][0]["scope"] == "dut"
        assert result["sdf_entries"][0]["file"] == "timing.sdf"

    def test_ifdef_guarded_sdf(self):
        content = """\
`ifdef SDF_GUARD
`else
  $sdf_annotate("gate.sdf", top.dut);
`endif
"""
        result = _parse_ifdef_around_sdf(content)
        assert result["sdf_guard_define"] == "SDF_GUARD"
        assert len(result["sdf_entries"]) == 1

    def test_commented_sdf_ignored(self):
        content = '// $sdf_annotate("skip.sdf", dut);'
        result = _parse_ifdef_around_sdf(content)
        assert result["sdf_entries"] == []


# ---------------------------------------------------------------------------
# _parse_shm_path
# ---------------------------------------------------------------------------

class TestParseShm:
    def test_simple_shm(self):
        output = "/path/to/dump/test.shm"
        assert _parse_shm_path(output) == "/path/to/dump/test.shm"

    def test_quoted_shm(self):
        output = "'/path/to/dump/test.shm'"
        assert _parse_shm_path(output) == "/path/to/dump/test.shm"

    def test_no_shm(self):
        assert _parse_shm_path("no database") == ""

    def test_multiline(self):
        output = "database1\n/run/dump/top.shm\nother"
        assert _parse_shm_path(output) == "/run/dump/top.shm"


# ---------------------------------------------------------------------------
# _parse_time_ns
# ---------------------------------------------------------------------------

class TestParseTimeNs:
    def test_ns_format(self):
        assert _parse_time_ns("  100 NS + 500") == 100 + 500

    def test_us_format(self):
        assert _parse_time_ns("  5 US + 200") == 5 * 1000 + 200

    def test_ms_format(self):
        assert _parse_time_ns("  3 MS + 1000") == 3 * 1_000_000 + 1000

    def test_plain_number(self):
        assert _parse_time_ns("42") == 42

    def test_no_match(self):
        assert _parse_time_ns("no time here") == 0


# ---------------------------------------------------------------------------
# validate_extra_args (F-062)
# ---------------------------------------------------------------------------

class TestValidateExtraArgs:
    """Test validate_extra_args rejects forbidden chars and passes clean strings."""

    @pytest.mark.parametrize("bad_input", [
        "arg1; rm -rf /",
        "arg1 | grep",
        "arg1 & bg",
        "arg1 $HOME",
        "arg1 `whoami`",
        "arg1 < /etc/passwd",
        "arg1 > /tmp/out",
        "arg1 (subshell)",
        "arg1\narg2",
        "arg1\rarg2",
        "arg1'breakout",
    ])
    def test_forbidden_chars_rejected(self, bad_input):
        with pytest.raises(ValueError, match="forbidden shell metacharacter"):
            validate_extra_args(bad_input)

    @pytest.mark.parametrize("good_input", [
        "",
        "--flag value",
        "-max -define GATE_SIM",
        "+define+TEST_NAME --timeout 300",
        "-f sim.f -top top_module",
    ])
    def test_clean_args_pass(self, good_input):
        assert validate_extra_args(good_input) == good_input


# ---------------------------------------------------------------------------
# sanitize_signal_name (F-062)
# ---------------------------------------------------------------------------

class TestSanitizeSignalName:
    """Test sanitize_signal_name allows valid signals, rejects injection."""

    @pytest.mark.parametrize("valid", [
        "top.hw.clk",
        "top.hw.data[7:0]",
        "top.hw.data[31]",
        r"top.hw.i_rst_n",
        "top.hw.bus[0]",
    ])
    def test_valid_signals_pass(self, valid):
        assert sanitize_signal_name(valid) == valid.strip()

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_signal_name("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_signal_name("   ")

    @pytest.mark.parametrize("injection", [
        "top.hw.[exec id]",
        "top.hw.$env(HOME)",
        "top.hw.clk; exec id",
        "top.[open /etc/passwd]",
    ])
    def test_injection_rejected(self, injection):
        with pytest.raises(ValueError):
            sanitize_signal_name(injection)

    def test_bracket_with_non_digit_rejected(self):
        with pytest.raises(ValueError, match="Tcl injection"):
            sanitize_signal_name("sig[exec rm]")


# ---------------------------------------------------------------------------
# is_safe_tcl_string (F-062)
# ---------------------------------------------------------------------------

class TestIsSafeTclString:
    """Test is_safe_tcl_string denylist and allow normal Tcl."""

    @pytest.mark.parametrize("dangerous", [
        "[exec id]",
        "[open /etc/passwd r]",
        "[socket localhost 4444]",
        "[file delete /tmp/foo]",
        "[file rename a b]",
        "[interp eval child {exec id}]",
        "[interp create]",
        "[load /tmp/evil.so]",
        "run 100ns; [exec whoami]",
    ])
    def test_dangerous_rejected(self, dangerous):
        assert is_safe_tcl_string(dangerous) is False

    @pytest.mark.parametrize("safe", [
        "run 100ns",
        "value /top/clk",
        "probe -create top -depth all",
        "database -open foo.shm",
        "waveform add -signals clk",
    ])
    def test_safe_passes(self, safe):
        assert is_safe_tcl_string(safe) is True


# ---------------------------------------------------------------------------
# get_ssh_cmd_timeout
# ---------------------------------------------------------------------------

def test_get_ssh_cmd_timeout_from_config() -> None:
    """Reads ssh_command_timeout from runner config."""
    runner = {"ssh_command_timeout": 60}
    assert get_ssh_cmd_timeout(runner) == 60.0


def test_get_ssh_cmd_timeout_default() -> None:
    """Returns default 30s when key missing."""
    assert get_ssh_cmd_timeout({}) == 30.0


def test_get_ssh_cmd_timeout_coerced_to_float() -> None:
    """Integer config value is returned as float."""
    runner = {"ssh_command_timeout": 45}
    result = get_ssh_cmd_timeout(runner)
    assert result == 45.0
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# shell_run_with_retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_run_with_retry_success_first_attempt() -> None:
    """Succeeds on first attempt — no retry needed."""
    with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
        mock.return_value = "output"
        result = await shell_run_with_retry("echo hello", timeout=5.0)
        assert result == "output"
        assert mock.call_count == 1


@pytest.mark.asyncio
async def test_shell_run_with_retry_retries_on_timeout() -> None:
    """Retries on TimeoutError, succeeds on second attempt."""
    with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
        mock.side_effect = [asyncio.TimeoutError("timeout"), "ok"]
        with patch("xcelium_mcp.shell_utils.asyncio") as mock_asyncio:
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.sleep = AsyncMock()
            mock_asyncio.create_subprocess_shell = asyncio.create_subprocess_shell
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            result = await shell_run_with_retry("cmd", timeout=5.0, max_retries=2, backoff_base=1.0)
            assert result == "ok"
            assert mock.call_count == 2


@pytest.mark.asyncio
async def test_shell_run_with_retry_raises_after_max_retries() -> None:
    """Raises TimeoutError after exhausting all retries."""
    with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
        mock.side_effect = asyncio.TimeoutError("timeout")
        with patch("xcelium_mcp.shell_utils.asyncio") as mock_asyncio:
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.sleep = AsyncMock()
            mock_asyncio.create_subprocess_shell = asyncio.create_subprocess_shell
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            with pytest.raises(asyncio.TimeoutError):
                await shell_run_with_retry("cmd", timeout=5.0, max_retries=1, backoff_base=1.0)
            assert mock.call_count == 2  # original + 1 retry


@pytest.mark.asyncio
async def test_shell_run_with_retry_no_retry_on_non_timeout() -> None:
    """Non-timeout errors propagate immediately without retry."""
    with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
        mock.side_effect = ValueError("bad command")
        with pytest.raises(ValueError):
            await shell_run_with_retry("bad cmd", timeout=5.0, max_retries=2)


# ---------------------------------------------------------------------------
# F-090: resolve_eda_tools uses backtick syntax for csh/tcsh
# ---------------------------------------------------------------------------

class TestResolveEdaToolsShellSyntax:
    """Verify which_cmds uses correct substitution syntax per env_shell type."""

    @pytest.mark.asyncio
    async def test_csh_uses_backtick_syntax(self) -> None:
        """When env_shell is /bin/csh, which_cmds must use backtick syntax."""
        captured: list[str] = []

        async def _fake_shell_run(cmd: str, **kwargs) -> str:
            captured.append(cmd)
            return "__TOOL_simvisdbutil__=/usr/bin/simvisdbutil\n__TOOL_xmsim__=/usr/bin/xmsim\n__TOOL_xrun__=/usr/bin/xrun"

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell_run):
            from xcelium_mcp.runner_detection import resolve_eda_tools
            result = await resolve_eda_tools({"env_shell": "/bin/csh", "login_shell": "/bin/csh"})

        assert result["simvisdbutil"] == "/usr/bin/simvisdbutil"
        assert len(captured) == 1
        # Must use backtick syntax, not $()
        assert "`which simvisdbutil`" in captured[0]
        assert "$(which simvisdbutil)" not in captured[0]

    @pytest.mark.asyncio
    async def test_tcsh_uses_backtick_syntax(self) -> None:
        """When env_shell is /bin/tcsh, which_cmds must use backtick syntax."""
        captured: list[str] = []

        async def _fake_shell_run(cmd: str, **kwargs) -> str:
            captured.append(cmd)
            return "__TOOL_simvisdbutil__=/opt/eda/simvisdbutil\n__TOOL_xmsim__=/opt/eda/xmsim\n__TOOL_xrun__=/opt/eda/xrun"

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell_run):
            from xcelium_mcp.runner_detection import resolve_eda_tools
            result = await resolve_eda_tools({"env_shell": "/bin/tcsh", "login_shell": "/bin/tcsh"})

        assert result["simvisdbutil"] == "/opt/eda/simvisdbutil"
        assert "`which simvisdbutil`" in captured[0]
        assert "$(which simvisdbutil)" not in captured[0]

    @pytest.mark.asyncio
    async def test_bash_uses_dollar_paren_syntax(self) -> None:
        """When env_shell is /bin/bash, which_cmds must use $() syntax."""
        captured: list[str] = []

        async def _fake_shell_run(cmd: str, **kwargs) -> str:
            captured.append(cmd)
            return "__TOOL_simvisdbutil__=/usr/bin/simvisdbutil\n__TOOL_xmsim__=/usr/bin/xmsim\n__TOOL_xrun__=/usr/bin/xrun"

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell_run):
            from xcelium_mcp.runner_detection import resolve_eda_tools
            result = await resolve_eda_tools({"env_shell": "/bin/bash", "login_shell": "/bin/bash"})

        assert result["simvisdbutil"] == "/usr/bin/simvisdbutil"
        assert "$(which simvisdbutil)" in captured[0]
        assert "`which simvisdbutil`" not in captured[0]

    @pytest.mark.asyncio
    async def test_sh_uses_dollar_paren_syntax(self) -> None:
        """When env_shell is /bin/sh, which_cmds must use $() syntax."""
        captured: list[str] = []

        async def _fake_shell_run(cmd: str, **kwargs) -> str:
            captured.append(cmd)
            return "__TOOL_simvisdbutil__=/usr/bin/simvisdbutil\n__TOOL_xmsim__=/usr/bin/xmsim\n__TOOL_xrun__=/usr/bin/xrun"

        with patch("xcelium_mcp.runner_detection.shell_run", side_effect=_fake_shell_run):
            from xcelium_mcp.runner_detection import resolve_eda_tools
            result = await resolve_eda_tools({"env_shell": "/bin/sh", "login_shell": "/bin/bash"})

        assert "$(which simvisdbutil)" in captured[0]


# ---------------------------------------------------------------------------
# F-102: debug_snapshot snapshot mode — absolute describe + dot-padding parse
# ---------------------------------------------------------------------------

class _MockMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


@pytest.mark.asyncio
async def test_debug_snapshot_uses_absolute_describe_path() -> None:
    """describe command uses scope absolute path, not bare 'describe *'."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    describe_calls: list[str] = []
    value_calls: list[str] = []

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT__":
            return "POSITION:Time: 100 NS\nSCOPE:/tb/dut\nSTOPS:"
        if cmd.startswith("describe"):
            describe_calls.append(cmd)
            # dot-padded relative names as xmsim returns for 'describe *'
            return "r_rst..........variable reg = 1'h0\nclk............variable reg = 1'h1"
        if cmd.startswith("value"):
            value_calls.append(cmd)
            return "1'h0"
        if cmd == "__SCREENSHOT__":
            raise Exception("no screenshot")
        return ""

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    # Verify absolute-path describe was used
    assert describe_calls, "No describe command was issued"
    assert describe_calls[0] == "describe *", (
        f"Expected 'describe *', got: {describe_calls[0]!r}"
    )

    # Verify parsed signal names are clean (no dot-padding)
    assert any("r_rst" in c for c in value_calls), f"value calls: {value_calls}"
    for c in value_calls:
        assert ".." not in c, f"Dot-padding leaked into value call: {c!r}"


@pytest.mark.asyncio
async def test_debug_snapshot_strips_dot_padding_from_signal_names() -> None:
    """Signal names are parsed by split('..')[0], stripping xmsim dot-padding."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    value_calls: list[str] = []

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT__":
            return "POSITION:Time: 200 NS\nSCOPE:/tb\nSTOPS:"
        if cmd.startswith("describe"):
            # dot-padded relative names as returned by real xmsim 'describe *'
            return (
                "data_out[7:0]..variable reg = 8'h00\n"
                "  data_out[7]....bit      = 1'h0\n"
                "r_rst..........variable reg = 1'h0"
            )
        if cmd.startswith("value"):
            value_calls.append(cmd)
            return "0"
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        return ""

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    for c in value_calls:
        assert ".." not in c, f"Dot-padding must be stripped from value command: {c!r}"

    assert any("data_out[7:0]" in c for c in value_calls)
    assert any("r_rst" in c for c in value_calls)


@pytest.mark.asyncio
async def test_debug_snapshot_graceful_fallback_on_pnoobj() -> None:
    """TclError from describe * (e.g. PNOOBJ for empty scope) shows graceful message."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tcl_bridge import TclError
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT__":
            return "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:"
        if cmd.startswith("describe"):
            raise TclError("PNOOBJ: No such object")
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        return ""

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    assert "no signals in current scope" in report.lower(), (
        f"Expected graceful PNOOBJ message, got: {report!r}"
    )


# ---------------------------------------------------------------------------
# F-103: skip bit-select expansion lines and named event lines in describe output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_snapshot_skips_bit_select_expansion_lines() -> None:
    """Lines starting with a space (bit-select children) are skipped — parent was already read."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    call_log: list[str] = []

    async def _fake_execute(cmd, **kwargs):
        call_log.append(cmd)
        if cmd == "__DEBUG_SNAPSHOT__":
            return "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:"
        if cmd.startswith("describe"):
            # parent line (dot-padded) + bit-select child (leading space, no dots)
            return (
                "w_bus..........net logic [1:0]\n"
                "   w_bus[1] (wire/tri) = St0\n"
                "   w_bus[0] (wire/tri) = St0\n"
                "r_en...........variable reg = 1'h1"
            )
        if cmd.startswith("value"):
            return "1'h1"
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    # Child lines should not produce value calls and must not appear as signals
    value_calls = [c for c in call_log if c.startswith("value")]
    assert all("w_bus[" not in c for c in value_calls), (
        f"bit-select child lines must not trigger value calls: {value_calls}"
    )
    assert "could not read" not in report, f"No 'could not read' expected: {report!r}"


@pytest.mark.asyncio
async def test_debug_snapshot_skips_named_event_lines() -> None:
    """Lines containing 'named event' are skipped — value command unsupported for this type."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT__":
            return "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:"
        if cmd.startswith("describe"):
            return (
                "sim_run........................ named event\n"
                "vector_start................... named event\n"
                "r_clk..................variable reg = 1'h0"
            )
        if cmd.startswith("value"):
            return "1'h0"
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    # Named events must not appear as signal value entries (format: "- `name` = `val`").
    # Note: `sim_run` also appears in the hardcoded "Suggested Next Steps" prose — use "= " to
    # distinguish signal entries from prose references.
    assert "- `sim_run` =" not in report, f"named event 'sim_run' must not be a signal entry: {report!r}"
    assert "- `vector_start` =" not in report, f"named event must not be a signal entry: {report!r}"
    assert "could not read" not in report, f"No 'could not read' expected: {report!r}"
    # The valid signal r_clk should still appear
    assert "r_clk" in report, f"Valid signal r_clk should appear: {report!r}"
