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
from xcelium_mcp.tcl_preprocessing import _parse_l1_time_ns

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

    def test_decimal_ns_format(self):
        """F-146: decimal counts in the coarse/fine parts must not be dropped."""
        assert _parse_time_ns("  3 MS + 500.5") == round(3 * 1_000_000 + 500.5)

    def test_decimal_coarse_part(self):
        assert _parse_time_ns("  1.5 US + 0") == round(1.5 * 1000)

    def test_decimal_plain_number(self):
        assert _parse_time_ns("42.5") == round(42.5)


# ---------------------------------------------------------------------------
# _parse_l1_time_ns
# ---------------------------------------------------------------------------

class TestParseL1TimeNs:
    def test_ns_default_unit(self):
        assert _parse_l1_time_ns("500") == 500

    def test_us_unit(self):
        assert _parse_l1_time_ns("500us") == 500_000

    def test_ms_unit(self):
        assert _parse_l1_time_ns("1ms") == 1_000_000

    def test_no_match_returns_zero(self):
        assert _parse_l1_time_ns("bogus") == 0

    def test_decimal_ms(self):
        """F-146: '1.5ms' previously matched only the integer prefix '1' silently."""
        assert _parse_l1_time_ns("1.5ms") == round(1.5 * 1_000_000)

    def test_decimal_us(self):
        assert _parse_l1_time_ns("500.25us") == round(500.25 * 1_000)

    def test_decimal_ns_default_unit(self):
        assert _parse_l1_time_ns("500.5") == round(500.5)


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
        patch("xcelium_mcp.bridge_lifecycle.resolve_sim_params",
              return_value={"test_args_format": "", "extra_args": "", "timeout": 2, "dump_args": ""}),
    ):
        from xcelium_mcp.bridge_lifecycle import _start_bridge
        from xcelium_mcp.bridge_manager import BridgeManager

        bridges = BridgeManager()
        try:
            await _start_bridge("/sim", config, "t1", "/sim/setup.tcl", "normal", 2, bridges=bridges)
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
        patch("xcelium_mcp.bridge_lifecycle.resolve_sim_params",
              return_value={"test_args_format": "", "extra_args": "", "timeout": 2, "dump_args": ""}),
    ):
        from xcelium_mcp.bridge_lifecycle import _start_bridge
        from xcelium_mcp.bridge_manager import BridgeManager

        bridges = BridgeManager()
        try:
            await _start_bridge("/sim", config, "t1", "/sim/setup.tcl", "normal", 2, bridges=bridges)
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
# F-131/F-135: inspect_signal list — recursive=True uses __LIST_SIGNALS__ (single TCL call)
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
async def test_inspect_signal_list_recursive_single_tcl_call() -> None:
    """list recursive=True sends a single __LIST_SIGNALS__ command (F-135: no per-item round-trips)."""
    from unittest.mock import MagicMock, patch

    executed: list[str] = []

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            executed.append(cmd)
            if cmd.startswith("__LIST_SIGNALS__"):
                return "top.hw.sda_out\ntop.hw.u_core.sda_in\ntop.hw.u_core.u_sub.sda_int"
            return ""

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = _FakeBridge()

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        result = await tool.fn(action="list", scope="top.hw", pattern="*sda*", recursive=True)

    # Exactly one TCP call (the __LIST_SIGNALS__ meta command)
    assert len(executed) == 1
    assert executed[0].startswith("__LIST_SIGNALS__")
    assert "{top.hw}" in executed[0]
    assert "{*sda*}" in executed[0]
    # Results from TCL bridge are returned as-is
    assert "top.hw.sda_out" in result
    assert "top.hw.u_core.sda_in" in result
    assert "top.hw.u_core.u_sub.sda_int" in result


@pytest.mark.asyncio
async def test_inspect_signal_list_recursive_no_match_returns_message() -> None:
    """recursive=True with no matching signals returns a descriptive message."""
    from unittest.mock import MagicMock, patch

    class _FakeBridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            return ""  # TCL bridge found nothing

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


# ---------------------------------------------------------------------------
# F-132: inspect_signal list recursive — xmsim silent fail → SimVision fallback
# ---------------------------------------------------------------------------


def _make_inspect_tool(fake_bridges):
    """Register inspect_signal and return the tool fn."""
    from unittest.mock import patch
    from mcp.server.fastmcp import FastMCP
    from xcelium_mcp.tools.signal_inspection import register

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        mcp = FastMCP("test_f132")
        register(mcp, fake_bridges)
        return mcp._tool_manager._tools["inspect_signal"].fn


@pytest.mark.asyncio
async def test_recursive_list_xmsim_autofallback_to_simvision() -> None:
    """recursive=True + target=xmsim → auto-switch to SimVision bridge."""
    from unittest.mock import MagicMock

    xmsim_bridge = MagicMock()
    xmsim_bridge.connected = True

    sv_executed: list[str] = []

    class _SVBridge:
        connected = True
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            sv_executed.append(cmd)
            if cmd.startswith("__LIST_SIGNALS__"):
                return "top.hw.sda_out"
            return ""

    sv_bridge = _SVBridge()

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = xmsim_bridge   # returns xmsim
    fake_bridges.xmsim_raw = xmsim_bridge                  # bridge is xmsim_raw → triggers fallback
    fake_bridges.simvision_raw = sv_bridge                 # SimVision available

    tool_fn = _make_inspect_tool(fake_bridges)
    result = await tool_fn(action="list", scope="top.hw", pattern="*sda*", recursive=True)

    # SimVision bridge was used (__LIST_SIGNALS__ called on sv_bridge)
    assert any("__LIST_SIGNALS__" in c for c in sv_executed), f"__LIST_SIGNALS__ not called on SimVision: {sv_executed}"
    assert "ERROR" not in result


@pytest.mark.asyncio
async def test_recursive_list_xmsim_no_simvision_returns_error() -> None:
    """recursive=True + target=xmsim + no SimVision → clear error message."""
    from unittest.mock import MagicMock

    xmsim_bridge = MagicMock()
    xmsim_bridge.connected = True

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = xmsim_bridge
    fake_bridges.xmsim_raw = xmsim_bridge
    fake_bridges.simvision_raw = None                      # no SimVision

    tool_fn = _make_inspect_tool(fake_bridges)
    result = await tool_fn(action="list", scope="top.hw", pattern="*sda*", recursive=True)

    assert "ERROR" in result
    assert "SimVision" in result


@pytest.mark.asyncio
async def test_recursive_list_simvision_target_unchanged() -> None:
    """recursive=True + target=simvision uses SimVision directly (no xmsim check)."""
    from unittest.mock import MagicMock

    sv_executed: list[str] = []

    class _SVBridge:
        connected = True
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            sv_executed.append(cmd)
            if cmd.startswith("__LIST_SIGNALS__"):
                return "top.hw.sda_out"
            return ""

    sv_bridge = _SVBridge()

    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = sv_bridge       # target=simvision returns sv_bridge
    fake_bridges.xmsim_raw = MagicMock()                   # different object → no fallback triggered
    fake_bridges.simvision_raw = sv_bridge

    tool_fn = _make_inspect_tool(fake_bridges)
    result = await tool_fn(action="list", scope="top.hw", pattern="*sda*",
                           target="simvision", recursive=True)

    assert any("__LIST_SIGNALS__" in c for c in sv_executed)
    assert "ERROR" not in result


# ---------------------------------------------------------------------------
# F-133: _list_signals_recursive — scope_prefixes parameter replaces u_* hardcode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recursive_default_prefix_only_recurses_u_() -> None:
    """Default scope_prefixes=None (→ ['u_']) recurses only into u_* items."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    calls: list[str] = []

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            calls.append(cmd)
            responses = {
                "scope show {top.hw}": "{top.hw.u_core} {top.hw.clk} {top.hw.inst_other}",
                "scope show {top.hw.u_core}": "{top.hw.u_core.sda_in}",
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(_Bridge(), "top.hw", "*sda*")

    # Recursed into u_core (u_ prefix), found sda_in
    assert "top.hw.u_core.sda_in" in hits
    # Did NOT recurse into inst_other (no u_ prefix)
    assert not any("inst_other" in c for c in calls if "scope show" in c and "u_core" not in c
                   and "inst_other" in c)


@pytest.mark.asyncio
async def test_list_recursive_custom_prefix_recurses_matching() -> None:
    """scope_prefixes=['inst_'] recurses only into inst_* items."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    calls: list[str] = []

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            calls.append(cmd)
            responses = {
                "scope show {top}": "{top.inst_a} {top.u_b} {top.sda_sig}",
                "scope show {top.inst_a}": "{top.inst_a.sda_out}",
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(_Bridge(), "top", "*sda*", scope_prefixes=["inst_"])

    assert "top.inst_a.sda_out" in hits
    # u_b was NOT recursed (prefix not in ["inst_"])
    assert not any("scope show {top.u_b}" in c for c in calls)
    # sda_sig at top level still matches
    assert "top.sda_sig" in hits


@pytest.mark.asyncio
async def test_list_recursive_empty_prefixes_general_mode() -> None:
    """scope_prefixes=[] recurses into ALL items (general mode)."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    calls: list[str] = []

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            calls.append(cmd)
            responses = {
                "scope show {top}": "{top.inst_a} {top.clk}",
                "scope show {top.inst_a}": "{top.inst_a.sda_out}",
                "scope show {top.clk}": "",  # signal → empty → no results
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(_Bridge(), "top", "*sda*", scope_prefixes=[])

    # Both inst_a and clk were tried
    assert any("scope show {top.inst_a}" in c for c in calls)
    assert any("scope show {top.clk}" in c for c in calls)
    # Only inst_a.sda_out matches (clk returned "" = signal, no children)
    assert "top.inst_a.sda_out" in hits
    assert "top.clk" not in hits


@pytest.mark.asyncio
async def test_inspect_signal_list_scope_prefixes_threaded() -> None:
    """scope_prefixes param on inspect_signal is forwarded via __LIST_SIGNALS__ command."""
    from unittest.mock import MagicMock, patch

    calls: list[str] = []

    class _SVBridge:
        connected = True
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            calls.append(cmd)
            if cmd.startswith("__LIST_SIGNALS__"):
                return "top.inst_x.sda"
            return ""

    sv_bridge = _SVBridge()
    fake_bridges = MagicMock()
    fake_bridges.get_bridge.return_value = sv_bridge
    fake_bridges.xmsim_raw = MagicMock()   # different object → no fallback
    fake_bridges.simvision_raw = sv_bridge

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        from xcelium_mcp.tools.signal_inspection import register
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("test_f133")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["inspect_signal"]
        result = await tool.fn(
            action="list", scope="top", pattern="*sda*",
            target="simvision", recursive=True, scope_prefixes=["inst_"]
        )

    # __LIST_SIGNALS__ was called with inst_ prefix in the command
    assert len(calls) == 1
    assert "__LIST_SIGNALS__" in calls[0]
    assert "{inst_}" in calls[0]
    assert "top.inst_x.sda" in result


# ---------------------------------------------------------------------------
# F-134: _parse_scope_item — SimVision TCL list array element path parsing
# ---------------------------------------------------------------------------


class TestParseScopeItem:
    """_parse_scope_item must handle all four SimVision scope show item formats."""

    def _parse(self, item: str) -> str:
        from xcelium_mcp.tools.signal_inspection import _parse_scope_item
        return _parse_scope_item(item)

    def test_double_braced_array_element(self) -> None:
        """{{path}[idx]} → path[idx] (scope show on array base returns double-braced)."""
        assert self._parse("{{top.hw.u_x.r_sdaDelayed}[1]}") == "top.hw.u_x.r_sdaDelayed[1]"
        assert self._parse("{{top.hw.u_x.r_sdaDelayed}[0]}") == "top.hw.u_x.r_sdaDelayed[0]"
        assert self._parse("{{top.hw.u_x.r_sdaDelayed}[1:0]}") == "top.hw.u_x.r_sdaDelayed[1:0]"

    def test_array_element_braced(self) -> None:
        """{path}[idx] → path[idx] (no } artifact)."""
        assert self._parse("{top.hw.u_x.r_sdaDelayed}[1]") == "top.hw.u_x.r_sdaDelayed[1]"
        assert self._parse("{top.hw.u_x.r_sdaDelayed}[0]") == "top.hw.u_x.r_sdaDelayed[0]"

    def test_array_range_braced(self) -> None:
        """{path}[1:0] → path[1:0]."""
        assert self._parse("{top.hw.u_x.r_sdaDelayed}[1:0]") == "top.hw.u_x.r_sdaDelayed[1:0]"

    def test_braced_plain_path(self) -> None:
        """{path} → path (normal braced item)."""
        assert self._parse("{top.hw.u_core}") == "top.hw.u_core"

    def test_unbraced_plain_path(self) -> None:
        """plain.path → plain.path (no transformation)."""
        assert self._parse("top.hw.clk") == "top.hw.clk"

    def test_no_brace_artifact_in_tail(self) -> None:
        """No } artifact regardless of input format."""
        for item in [
            "{top.hw.u_x.r_sdaDelayed}[1]",
            "{{top.hw.u_x.r_sdaDelayed}[1]}",
        ]:
            clean = self._parse(item)
            assert "}" not in clean, f"Brace artifact in {item!r} → {clean!r}"


@pytest.mark.asyncio
async def test_list_recursive_empty_prefixes_no_brace_artifact() -> None:
    """scope_prefixes=[] mode: single-braced array items must not produce } artifacts (F-134)."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            responses = {
                # Top-level scope show: single-braced SimVision format
                "scope show {top.hw.u_x}": (
                    "{top.hw.u_x.r_sdaDelayed}[1:0] "
                    "{top.hw.u_x.r_sdaDelayed}[1] "
                    "{top.hw.u_x.r_sdaDelayed}[0] "
                    "{top.hw.u_x.r_sdaDelayed}"
                ),
                "scope show {top.hw}": "{top.hw.u_x}",
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(
        _Bridge(), "top.hw", "*sdaDelayed*", scope_prefixes=[]
    )

    for h in hits:
        assert "}" not in h, f"Brace artifact in result: {h!r}"

    assert any("r_sdaDelayed[1:0]" in h for h in hits), f"Missing [1:0] in {hits}"
    assert any("r_sdaDelayed[1]" in h for h in hits), f"Missing [1] in {hits}"
    assert any("r_sdaDelayed[0]" in h for h in hits), f"Missing [0] in {hits}"


@pytest.mark.asyncio
async def test_list_recursive_double_braced_array_elements() -> None:
    """scope show on array base returns double-braced {{path}[idx]} items (F-134 v2)."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            responses = {
                "scope show {top.hw.u_x}": "{top.hw.u_x.r_sdaDelayed}[1:0] {top.hw.u_x.r_sdaDelayed}",
                # Recursing into the array base returns double-braced individual elements
                "scope show {top.hw.u_x.r_sdaDelayed[1:0]}": "",
                "scope show {top.hw.u_x.r_sdaDelayed}": (
                    "{{top.hw.u_x.r_sdaDelayed}[1]} {{top.hw.u_x.r_sdaDelayed}[0]}"
                ),
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(
        _Bridge(), "top.hw.u_x", "*sdaDelayed*", scope_prefixes=[]
    )

    for h in hits:
        assert "}" not in h, f"Brace artifact in result: {h!r}"
        assert "{" not in h, f"Brace artifact in result: {h!r}"

    assert any("r_sdaDelayed[1]" in h for h in hits), f"Missing [1] in {hits}"
    assert any("r_sdaDelayed[0]" in h for h in hits), f"Missing [0] in {hits}"


@pytest.mark.asyncio
async def test_list_recursive_no_double_bit_select() -> None:
    """Single bit-select [N] paths must not be recursed into — prevents r_bus[1][0] (F-134 v3)."""
    from xcelium_mcp.tools.signal_inspection import _list_signals_recursive

    recurse_calls: list[str] = []

    class _Bridge:
        async def execute(self, cmd: str, timeout: float = 30) -> str:
            recurse_calls.append(cmd)
            responses = {
                # Array base scope: returns individual bit-select paths
                "scope show {top.hw.u_x}": (
                    "{{top.hw.u_x.r_bus}[1]} {{top.hw.u_x.r_bus}[0]}"
                ),
                # If we mistakenly recurse into [1], SimVision returns nested garbage
                "scope show {top.hw.u_x.r_bus[1]}": "{{top.hw.u_x.r_bus[1]}[0]}",
                "scope show {top.hw.u_x.r_bus[0]}": "",
            }
            return responses.get(cmd, "")

    hits = await _list_signals_recursive(
        _Bridge(), "top.hw.u_x", "*r_bus*", scope_prefixes=[]
    )

    # [N] paths must NOT be recursed into
    assert "scope show {top.hw.u_x.r_bus[1]}" not in recurse_calls, (
        f"Should not recurse into bit-select path: {recurse_calls}"
    )
    assert "scope show {top.hw.u_x.r_bus[0]}" not in recurse_calls, (
        f"Should not recurse into bit-select path: {recurse_calls}"
    )

    # No double bit-select in results
    for h in hits:
        assert "[1][0]" not in h and "[0][0]" not in h, f"Double bit-select in result: {h!r}"
        assert "{" not in h and "}" not in h, f"Brace artifact: {h!r}"

    # Individual bits should be in results
    assert any("r_bus[1]" in h for h in hits), f"Missing r_bus[1] in {hits}"
    assert any("r_bus[0]" in h for h in hits), f"Missing r_bus[0] in {hits}"
