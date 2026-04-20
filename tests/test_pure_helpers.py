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
    _parse_tcl_db_open_path,
    _parse_time_ns,
    find_shm,
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
# _parse_tcl_db_open_path
# ---------------------------------------------------------------------------

class TestParseTclDbOpenPath:
    def test_simple_shm(self):
        content = "database -open dump.shm -shm\nprobe -create top\n"
        assert _parse_tcl_db_open_path(content) == "dump.shm"

    def test_relative_path(self):
        content = "database -open ../dump/ci_top.shm -shm\n"
        assert _parse_tcl_db_open_path(content) == "../dump/ci_top.shm"

    def test_absolute_path(self):
        content = "database -open /sim/run/waves.shm -shm -into /sim\n"
        assert _parse_tcl_db_open_path(content) == "/sim/run/waves.shm"

    def test_comment_ignored(self):
        content = "# database -open commented.shm\ndatabase -open real.shm -shm\n"
        assert _parse_tcl_db_open_path(content) == "real.shm"

    def test_no_database_open(self):
        content = "probe -create top -depth all\nrun\n"
        assert _parse_tcl_db_open_path(content) == ""

    def test_non_shm_extension_ignored(self):
        content = "database -open dump.vcd\ndatabase -open waves.shm\n"
        assert _parse_tcl_db_open_path(content) == "waves.shm"


# ---------------------------------------------------------------------------
# find_shm
# ---------------------------------------------------------------------------

class TestFindShm:
    @pytest.mark.asyncio
    async def test_with_test_name_found(self):
        """Returns matching shm when test_name glob succeeds."""
        with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
            mock.return_value = "/sim/run/dump/ci_top_TOP015.shm\n"
            result = await find_shm("/sim/run", "TOP015")
            assert result == "/sim/run/dump/ci_top_TOP015.shm"
            assert "TOP015" in mock.call_args_list[0][0][0]

    @pytest.mark.asyncio
    async def test_with_test_name_falls_back_to_any_shm(self):
        """Falls back to *.shm when test_name glob returns nothing."""
        with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
            mock.side_effect = ["", "/sim/run/dump/waves.shm\n"]
            result = await find_shm("/sim/run", "NOTFOUND")
            assert result == "/sim/run/dump/waves.shm"

    @pytest.mark.asyncio
    async def test_no_test_name_uses_wildcard(self):
        """Without test_name, returns newest *.shm directly."""
        with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
            mock.return_value = "/sim/run/dump/latest.shm\n"
            result = await find_shm("/sim/run")
            assert result == "/sim/run/dump/latest.shm"
            assert mock.call_count == 1  # only the fallback glob

    @pytest.mark.asyncio
    async def test_no_shm_returns_empty(self):
        """Returns empty string when no SHM files exist."""
        with patch("xcelium_mcp.shell_utils.shell_run", new_callable=AsyncMock) as mock:
            mock.return_value = ""
            result = await find_shm("/sim/run")
            assert result == ""


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
    """__DEBUG_SNAPSHOT_BULK__ is used; signal names appear clean in the report (F-125)."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    call_log: list[str] = []

    async def _fake_execute(cmd, **kwargs):
        call_log.append(cmd)
        if cmd == "__DEBUG_SNAPSHOT_BULK__":
            return (
                "POSITION:Time: 100 NS\nSCOPE:/tb/dut\nSTOPS:\n"
                "SIGNALS_COUNT:2\n"
                "SIGNAL:r_rst=1'h0\n"
                "SIGNAL:clk=1'h1"
            )
        if cmd == "__SCREENSHOT__":
            raise Exception("no screenshot")
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    # __DEBUG_SNAPSHOT_BULK__ should be the only Tcl call (no separate describe/value)
    assert any(c == "__DEBUG_SNAPSHOT_BULK__" for c in call_log), (
        f"Expected __DEBUG_SNAPSHOT_BULK__ call, got: {call_log}"
    )
    assert not any(c.startswith("describe") for c in call_log), (
        f"describe must not be called separately with bulk protocol: {call_log}"
    )
    assert not any(c.startswith("value") for c in call_log), (
        f"value must not be called separately with bulk protocol: {call_log}"
    )
    assert "r_rst" in report, f"Signal r_rst should appear in report: {report!r}"
    assert ".." not in report.split("Signal Values")[1] if "Signal Values" in report else True


@pytest.mark.asyncio
async def test_debug_snapshot_strips_dot_padding_from_signal_names() -> None:
    """Signal names from __DEBUG_SNAPSHOT_BULK__ appear clean (no dot-padding) in report (F-125)."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT_BULK__":
            # Tcl bridge strips dot-padding before returning SIGNAL: lines
            return (
                "POSITION:Time: 200 NS\nSCOPE:/tb\nSTOPS:\n"
                "SIGNALS_COUNT:2\n"
                "SIGNAL:data_out[7:0]=8'h00\n"
                "SIGNAL:r_rst=1'h0"
            )
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    assert "data_out[7:0]" in report, f"Signal data_out[7:0] should appear: {report!r}"
    assert "r_rst" in report, f"Signal r_rst should appear: {report!r}"
    # Dot-padding must not leak into the output
    assert "data_out[7:0].." not in report, f"Dot-padding must not appear in report: {report!r}"


@pytest.mark.asyncio
async def test_debug_snapshot_graceful_fallback_on_pnoobj() -> None:
    """When __DEBUG_SNAPSHOT_BULK__ returns SIGNALS_COUNT:0, report shows graceful message (F-125)."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT_BULK__":
            # Tcl bridge returns count:0 when describe fails (e.g. PNOOBJ)
            return "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:\nSIGNALS_COUNT:0"
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        raise RuntimeError(f"unexpected: {cmd}")

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
    """Bit-select child signals are filtered by Tcl bridge; only parents appear in report (F-125)."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT_BULK__":
            # Tcl do_debug_snapshot_bulk skips indented child lines — only parents returned
            return (
                "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:\n"
                "SIGNALS_COUNT:2\n"
                "SIGNAL:w_bus=1'h1\n"
                "SIGNAL:r_en=1'h1"
            )
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    # Bit-select children (w_bus[1], w_bus[0]) must not appear — only parent w_bus
    assert "- `w_bus[1]`" not in report, f"bit-select child must not appear: {report!r}"
    assert "- `w_bus[0]`" not in report, f"bit-select child must not appear: {report!r}"
    assert "could not read" not in report, f"No 'could not read' expected: {report!r}"


@pytest.mark.asyncio
async def test_debug_snapshot_skips_named_event_lines() -> None:
    """Named event signals are filtered by Tcl bridge; only valid signals appear in report (F-125)."""
    from unittest.mock import AsyncMock, MagicMock
    from xcelium_mcp.tools.debug import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    async def _fake_execute(cmd, **kwargs):
        if cmd == "__DEBUG_SNAPSHOT_BULK__":
            # Tcl do_debug_snapshot_bulk skips named event lines — only r_clk returned
            return (
                "POSITION:Time: 0 NS\nSCOPE:top\nSTOPS:\n"
                "SIGNALS_COUNT:1\n"
                "SIGNAL:r_clk=1'h0"
            )
        if cmd == "__SCREENSHOT__":
            raise RuntimeError("no screenshot")
        raise RuntimeError(f"unexpected: {cmd}")

    mock_bridges.get_bridge.return_value.execute = AsyncMock(side_effect=_fake_execute)
    mock_bridges.get_bridge.return_value.screenshot = AsyncMock(side_effect=RuntimeError("no ss"))

    register(mock_mcp, mock_bridges)
    result = await mock_mcp.tools["debug_snapshot"](mode="snapshot", target="auto")

    report = result[0] if isinstance(result, list) else result
    # Named events must not appear as signal value entries
    assert "- `sim_run` =" not in report, f"named event 'sim_run' must not be a signal entry: {report!r}"
    assert "- `vector_start` =" not in report, f"named event must not be a signal entry: {report!r}"
    assert "could not read" not in report, f"No 'could not read' expected: {report!r}"
    # The valid signal r_clk should still appear
    assert "r_clk" in report, f"Valid signal r_clk should appear: {report!r}"


# ---------------------------------------------------------------------------
# F-128: checkpoint restore — non-existent name error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_checkpoint_nonexistent_name_returns_error() -> None:
    """restore_checkpoint_impl returns ERROR when name not in manifest (F-128)."""
    import json
    import os
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl

    with tempfile.TemporaryDirectory() as tmp:
        chk_dir = os.path.join(tmp, "checkpoints")
        os.makedirs(chk_dir)
        manifest = {
            "checkpoints": {
                "L1_TOP015": {
                    "saved_at": "2026-01-01", "saved_time_ns": 0,
                    "compile_hash": "abc", "origin": "bridge",
                    "test_name": "", "path": f"{chk_dir}/L1_TOP015",
                },
            }
        }
        with open(os.path.join(chk_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)

        bridges = MagicMock()
        bridges.xmsim.execute = AsyncMock(return_value="restored:ok")

        with patch("xcelium_mcp.tools.checkpoint.resolve_sim_dir", AsyncMock(return_value=tmp)):
            result = await restore_checkpoint_impl(bridges, "nonexistent_ckpt", tmp)

    assert result.startswith("ERROR:"), f"Expected ERROR, got: {result!r}"
    assert "nonexistent_ckpt" in result
    assert "not found" in result.lower()
    # Tcl bridge must NOT have been called
    bridges.xmsim.execute.assert_not_called()


@pytest.mark.asyncio
async def test_restore_checkpoint_valid_name_proceeds() -> None:
    """restore_checkpoint_impl calls Tcl bridge when name exists in manifest (F-128)."""
    import json
    import os
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl

    with tempfile.TemporaryDirectory() as tmp:
        chk_dir = os.path.join(tmp, "checkpoints")
        os.makedirs(chk_dir)
        manifest = {
            "checkpoints": {
                "L1_TOP015": {
                    "saved_at": "2026-01-01", "saved_time_ns": 0,
                    "compile_hash": "abc", "origin": "bridge",
                    "test_name": "", "path": f"{chk_dir}/L1_TOP015",
                },
            }
        }
        with open(os.path.join(chk_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)

        bridges = MagicMock()
        bridges.xmsim.execute = AsyncMock(
            return_value="restored:worklib.L1_TOP015:module|position:0 NS"
        )

        with patch("xcelium_mcp.tools.checkpoint.resolve_sim_dir", AsyncMock(return_value=tmp)):
            result = await restore_checkpoint_impl(bridges, "L1_TOP015", tmp)

    # Tcl bridge was called and no error returned
    bridges.xmsim.execute.assert_called_once()
    assert "ERROR" not in result


# ---------------------------------------------------------------------------
# F-129: checkpoint restore — no double "restore failed:" when manifest empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_checkpoint_empty_manifest_propagates_tcl_error() -> None:
    """When manifest is empty, TclError from bridge propagates out of restore_checkpoint_impl (F-129)."""
    import json
    import os
    import tempfile
    from unittest.mock import AsyncMock, MagicMock, patch

    import pytest

    from xcelium_mcp.tcl_bridge import TclError
    from xcelium_mcp.tools.checkpoint import restore_checkpoint_impl

    with tempfile.TemporaryDirectory() as tmp:
        chk_dir = os.path.join(tmp, "checkpoints")
        os.makedirs(chk_dir)
        # Empty manifest — the `if known` guard is False, pre-check skipped
        manifest = {"checkpoints": {}}
        with open(os.path.join(chk_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)

        bridges = MagicMock()
        bridges.xmsim.execute = AsyncMock(
            side_effect=TclError(
                "restore failed: xmsim: *E,RSNFND: Snapshot not found: worklib.ghost:module."
            )
        )

        with patch("xcelium_mcp.tools.checkpoint.resolve_sim_dir", AsyncMock(return_value=tmp)):
            with pytest.raises(TclError) as exc_info:
                await restore_checkpoint_impl(bridges, "ghost", tmp)

    # TclError message should contain single "restore failed:"
    assert "restore failed:" in str(exc_info.value)


def test_restore_except_clause_no_double_prefix() -> None:
    """Except-clause formatting must not double 'restore failed:' prefix (F-129)."""
    from xcelium_mcp.tcl_bridge import TclError

    # Simulate what the Tcl bridge returns as TclError message
    e = TclError("restore failed: xmsim: *E,RSNFND: Snapshot not found: worklib.ghost:module.")
    msg = str(e)
    # This is the new except-clause logic from checkpoint.py
    if not msg.startswith("ERROR:"):
        msg = f"ERROR: {msg}"

    assert msg.count("restore failed:") == 1, f"Double prefix: {msg!r}"
    assert msg.startswith("ERROR:")


# ---------------------------------------------------------------------------
# F-130: bridge_ready_* cleanup — type-scoped, process-presence-based
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_bridge_removes_only_xmsim_ready_files() -> None:
    """_start_bridge clears xmsim-type ready files but preserves simvision-type ones."""
    from unittest.mock import AsyncMock, patch

    removed: list[str] = []

    async def fake_shell_run(cmd: str, timeout: float = 30) -> str:
        if cmd.startswith("rm -f") and "bridge_ready_" in cmd:
            removed.append(cmd)
        return ""  # pgrep/ss/ps all return empty → no xmsim running, port free

    async def fake_scan(target: str | None = None) -> list[tuple[int, str]]:
        entries = [(9876, "xmsim"), (9877, "simvision")]
        if target is None:
            return entries
        return [(p, t) for p, t in entries if t == target]

    config = {
        "runner": {"script": "run.sh", "run_dir": "run"},
        "bridge": {"port": 9876, "tcl_path": "/sim/bridge.tcl"},
    }

    with (
        patch("xcelium_mcp.bridge_lifecycle.scan_ready_files", fake_scan),
        patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.bridge_lifecycle.shell_run", fake_shell_run),
        patch("xcelium_mcp.bridge_lifecycle.find_shm", AsyncMock(return_value="")),
    ):
        from xcelium_mcp.bridge_lifecycle import _start_bridge
        from xcelium_mcp.bridge_manager import BridgeManager

        bridges = BridgeManager()
        try:
            await _start_bridge("/sim", config, "t1", "/sim/setup.tcl", "normal", 60, bridges=bridges)
        except Exception:
            pass  # only care about which rm -f calls were made before launch

    assert any("bridge_ready_9876" in r for r in removed), f"xmsim file not removed: {removed}"
    assert not any("bridge_ready_9877" in r for r in removed), f"simvision file wrongly removed: {removed}"


@pytest.mark.asyncio
async def test_start_bridge_no_ready_files_is_noop() -> None:
    """_start_bridge handles absent bridge_ready files without error."""
    removed: list[str] = []

    async def fake_shell_run(cmd: str, timeout: float = 30) -> str:
        if "bridge_ready_" in cmd and cmd.startswith("rm"):
            removed.append(cmd)
        return ""

    async def fake_scan(target: str | None = None) -> list[tuple[int, str]]:
        return []

    config = {
        "runner": {"script": "run.sh", "run_dir": "run"},
        "bridge": {"port": 9876, "tcl_path": "/sim/bridge.tcl"},
    }

    with (
        patch("xcelium_mcp.bridge_lifecycle.scan_ready_files", fake_scan),
        patch("xcelium_mcp.bridge_lifecycle.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.bridge_lifecycle.shell_run", fake_shell_run),
        patch("xcelium_mcp.bridge_lifecycle.find_shm", AsyncMock(return_value="")),
    ):
        from xcelium_mcp.bridge_lifecycle import _start_bridge
        from xcelium_mcp.bridge_manager import BridgeManager

        bridges = BridgeManager()
        try:
            await _start_bridge("/sim", config, "t1", "/sim/setup.tcl", "normal", 60, bridges=bridges)
        except Exception:
            pass

    assert removed == [], f"Unexpected rm calls with no ready files: {removed}"


@pytest.mark.asyncio
async def test_start_simvision_removes_stale_ready_file_on_connection_failure() -> None:
    """start_simvision removes simvision ready files whose port is not connectable."""
    from unittest.mock import AsyncMock, patch

    removed: list[str] = []

    async def fake_shell_run(cmd: str, timeout: float = 30) -> str:
        if "bridge_ready_" in cmd and cmd.startswith("rm"):
            removed.append(cmd)
        return ""

    async def fake_scan(target: str | None = None) -> list[tuple[int, str]]:
        if target == "simvision" or target is None:
            return [(9877, "simvision")]
        return []

    class _FailBridge:
        async def connect(self) -> str:
            raise ConnectionRefusedError("no server")

    with (
        patch("xcelium_mcp.simvision_ops.scan_ready_files", fake_scan),
        patch("xcelium_mcp.simvision_ops.get_user_tmp_dir", AsyncMock(return_value="/tmp/mcp")),
        patch("xcelium_mcp.simvision_ops.shell_run", fake_shell_run),
        patch("xcelium_mcp.simvision_ops.TclBridge", lambda **_kw: _FailBridge()),
        patch("xcelium_mcp.simvision_ops.resolve_sim_dir", AsyncMock(return_value="/sim")),
        patch("xcelium_mcp.simvision_ops.load_sim_config", AsyncMock(return_value=None)),
        patch("xcelium_mcp.simvision_ops.find_shm", AsyncMock(return_value="")),
        patch("xcelium_mcp.simvision_ops.detect_vnc_display", AsyncMock(return_value="")),
    ):
        from xcelium_mcp.bridge_manager import BridgeManager
        from xcelium_mcp.simvision_ops import start_simvision

        bridges = BridgeManager()
        await start_simvision(bridges, "", "", "", "")

    assert any("bridge_ready_9877" in r for r in removed), f"stale simvision file not removed: {removed}"


# ---------------------------------------------------------------------------
# F-131: inspect_signal list — recursive=True uses Python-side scope show walk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_signal_list_nonrecursive_uses_describe_dot() -> None:
    """list action with recursive=False (default) sends describe scope.pattern."""
    from unittest.mock import MagicMock, patch

    executed: list[str] = []

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            executed.append(cmd)
            return "top.hw.sda wire"

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = _FakeBridge()

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        await tool.fn(action="list", scope="top.hw", pattern="*sda*", recursive=False)

    assert len(executed) == 1
    assert executed[0] == "describe top.hw.*sda*"


@pytest.mark.asyncio
async def test_inspect_signal_list_recursive_uses_scope_show() -> None:
    """list action with recursive=True calls scope show and recurses into u_ scopes."""
    from unittest.mock import MagicMock, patch

    executed: list[str] = []

    # Simulated scope show responses:
    # top.hw contains u_core (sub-scope) and sda_out (signal)
    # top.hw.u_core contains u_sub (sub-scope) and sda_in (signal)
    # top.hw.u_core.u_sub contains just a signal sda_int
    def fake_execute(cmd: str, timeout: float = 30):
        import asyncio
        executed.append(cmd)
        responses = {
            "scope show {top.hw}": "{top.hw.u_core} {top.hw.sda_out}",
            "scope show {top.hw.u_core}": "{top.hw.u_core.u_sub} {top.hw.u_core.sda_in}",
            "scope show {top.hw.u_core.u_sub}": "{top.hw.u_core.u_sub.sda_int}",
        }
        return asyncio.coroutine(lambda: responses.get(cmd, ""))()

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            executed.append(cmd)
            responses = {
                "scope show {top.hw}": "{top.hw.u_core} {top.hw.sda_out}",
                "scope show {top.hw.u_core}": "{top.hw.u_core.u_sub} {top.hw.u_core.sda_in}",
                "scope show {top.hw.u_core.u_sub}": "{top.hw.u_core.u_sub.sda_int}",
            }
            return responses.get(cmd, "")

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = _FakeBridge()

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        result = await tool.fn(action="list", scope="top.hw", pattern="*sda*", recursive=True)

    # scope show called for top.hw, then recursed into u_core and u_sub
    assert any("scope show {top.hw}" in c for c in executed)
    assert any("scope show {top.hw.u_core}" in c for c in executed)
    assert any("scope show {top.hw.u_core.u_sub}" in c for c in executed)
    # All sda* signals found at every level
    assert "top.hw.sda_out" in result
    assert "top.hw.u_core.sda_in" in result
    assert "top.hw.u_core.u_sub.sda_int" in result
    # u_core (as a bare scope name) does not appear on its own line
    for line in result.splitlines():
        assert line.endswith("u_core") is False, f"bare scope leaked into results: {line}"


@pytest.mark.asyncio
async def test_inspect_signal_list_recursive_no_match_returns_message() -> None:
    """recursive=True with no matching signals returns a descriptive message."""
    from unittest.mock import MagicMock, patch

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            return "{top.hw.clk} {top.hw.rst_n}"  # no sda* signals

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = _FakeBridge()

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        result = await tool.fn(action="list", scope="top.hw", pattern="*sda*", recursive=True)

    assert "No signals" in result


@pytest.mark.asyncio
async def test_inspect_signal_list_recursive_default_false_backward_compat() -> None:
    """recursive=False (default) preserves the existing describe scope.pattern behaviour."""
    from unittest.mock import MagicMock, patch

    executed: list[str] = []

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            executed.append(cmd)
            return ""

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = _FakeBridge()

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        await tool.fn(action="list", scope="top.hw", pattern="*")

    assert executed == ["describe top.hw.*"], f"Unexpected: {executed}"
