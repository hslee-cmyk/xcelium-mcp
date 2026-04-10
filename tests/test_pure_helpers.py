"""Unit tests for pure helper functions — no MCP, no I/O required."""
from __future__ import annotations

import pytest

from xcelium_mcp.discovery import (
    _extract_top_module_from_content,
    _parse_ifdef_around_sdf,
)
from xcelium_mcp.batch_runner import validate_extra_args
from xcelium_mcp.shell_utils import (
    _parse_shm_path,
    _parse_time_ns,
    is_safe_tcl_string,
    sanitize_signal_name,
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
