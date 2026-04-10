"""Unit tests for pure helper functions — no MCP, no I/O required."""
from __future__ import annotations

import pytest

from xcelium_mcp.discovery import (
    _extract_top_module_from_content,
    _parse_ifdef_around_sdf,
)
from xcelium_mcp.shell_utils import _parse_shm_path, _parse_time_ns


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
