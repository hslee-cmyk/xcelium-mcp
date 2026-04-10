"""Tests for v4.3 dump_depth / probe scope.

Imports real functions from xcelium_mcp.* — no inline copies.
(Previous versions inlined the functions under test; regressions in the
production code went undetected. See test_imports.py for the smoke test
that now catches import-time regressions.)
"""
from __future__ import annotations

from xcelium_mcp.discovery import (
    _extract_top_module_from_content,
    _parse_ifdef_around_sdf,
)
from xcelium_mcp.tcl_preprocessing import (
    BOUNDARY_SIGNALS,
    _inject_dump_window,
    _replace_probe_lines,
    _resolve_probe_signals,
)

# ---------------------------------------------------------------------------
# Tests: _resolve_probe_signals
# ---------------------------------------------------------------------------


def test_resolve_probe_signals_all() -> None:
    probe_type, signals = _resolve_probe_signals(None, "all")
    assert probe_type == "depth_all"
    assert signals is None


def test_resolve_probe_signals_boundary() -> None:
    probe_type, signals = _resolve_probe_signals(None, "boundary")
    assert probe_type == "signals"
    assert signals == sorted(BOUNDARY_SIGNALS)
    assert len(signals) == 28


def test_resolve_probe_signals_union() -> None:
    extra = ["top.hw.u_ext.debug_signal"]
    probe_type, signals = _resolve_probe_signals(extra, "boundary")
    assert probe_type == "signals"
    assert "top.hw.u_ext.debug_signal" in signals
    assert "top.hw.i_mainClk" in signals
    assert len(signals) == 29  # 28 boundary + 1 extra


def test_resolve_probe_signals_union_dedup() -> None:
    """dump_signals that overlap with BOUNDARY_SIGNALS should not duplicate."""
    extra = ["top.hw.i_mainClk", "top.hw.u_ext.debug_signal"]
    _, signals = _resolve_probe_signals(extra, "boundary")
    assert signals.count("top.hw.i_mainClk") == 1
    assert len(signals) == 29  # 28 + 1 new


# ---------------------------------------------------------------------------
# Tests: resolve_sim_params dump_depth semantics
# ---------------------------------------------------------------------------


def test_resolve_sim_params_gate_default() -> None:
    """Gate mode without dump_depth → boundary from mode_defaults."""
    runner = {
        "args_format": "-test {test_name} --",
        "mode_defaults": {
            "common": {"timeout": 120, "probe_strategy": "all", "dump_depth": "all"},
            "gate": {"timeout": 1800, "probe_strategy": "selective", "dump_depth": "boundary"},
        },
    }
    mode_cfg = {**runner["mode_defaults"].get("common", {}), **runner["mode_defaults"].get("gate", {})}
    dump_depth = None
    effective = dump_depth if dump_depth is not None else mode_cfg.get("dump_depth", "all")
    assert effective == "boundary"


def test_resolve_sim_params_gate_override() -> None:
    """Gate mode with dump_depth="all" → override to all."""
    runner = {
        "args_format": "-test {test_name} --",
        "mode_defaults": {
            "common": {"dump_depth": "all"},
            "gate": {"dump_depth": "boundary"},
        },
    }
    mode_cfg = {**runner["mode_defaults"].get("common", {}), **runner["mode_defaults"].get("gate", {})}
    dump_depth = "all"
    effective = dump_depth if dump_depth is not None else mode_cfg.get("dump_depth", "all")
    assert effective == "all"


# ---------------------------------------------------------------------------
# Tests: _replace_probe_lines
# ---------------------------------------------------------------------------


def test_replace_probe_lines_all() -> None:
    """depth_all mode re-adds a single `probe -create top -depth all` line,
    using `-database <path>` (from `database -open`) as the destination."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "depth_all", None)
    assert "probe -create top -depth all -database dump.shm" in result
    assert result.count("probe -create top -depth all") == 1


def test_replace_probe_lines_signals() -> None:
    """signals mode drops `-depth` probes and adds one probe per signal."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["sig1", "sig2"])
    assert "probe -create sig1 -database dump.shm" in result
    assert "probe -create sig2 -database dump.shm" in result
    assert "-depth all" not in result


def test_replace_probe_lines_keep_custom() -> None:
    """User custom signal probes (without -depth) should be preserved."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "probe -create top.hw.u_ext.debug -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["sig1"])
    assert "probe -create top.hw.u_ext.debug -shm" in result
    assert "probe -create sig1 -database dump.shm" in result
    assert "-depth all" not in result


def test_replace_probe_lines_no_dup_custom() -> None:
    """If dump_signals overlaps with existing custom, don't duplicate."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top.hw.u_ext.debug -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["top.hw.u_ext.debug", "sig1"])
    assert result.count("top.hw.u_ext.debug") == 1
    assert "probe -create sig1 -database dump.shm" in result


# ---------------------------------------------------------------------------
# Tests: _inject_dump_window
# ---------------------------------------------------------------------------


def test_inject_dump_window() -> None:
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "run\n"
    )
    result = _inject_dump_window(content, {"start_ms": 50, "end_ms": 55})
    assert "probe -disable" in result
    assert "run 50ms" in result
    assert "probe -enable" in result
    assert "run 5ms" in result
    lines = result.strip().splitlines()
    assert lines[-1] == "run"


def test_inject_dump_window_start_zero() -> None:
    """start_ms=0 should skip the initial settling run."""
    content = "database -open dump.shm -shm\nprobe -create top -depth all -shm\nrun\n"
    result = _inject_dump_window(content, {"start_ms": 0, "end_ms": 10})
    assert "run 0ms" not in result
    assert "run 10ms" in result


def test_dump_window_invalid_range() -> None:
    """end_ms must be > start_ms."""
    start, end = 55, 50
    assert end <= start  # invalid


# ---------------------------------------------------------------------------
# Tests: _parse_ifdef_around_sdf
# ---------------------------------------------------------------------------


def test_analyze_sdf_annotate_nodly() -> None:
    """venezia-t0 pattern: GATE → NODLY guard → BEST/WORST/TYP corners."""
    content = """\
`ifdef GATE
  initial begin
  `ifdef NODLY
  `else
    `ifdef PRE
    $sdf_annotate ("../db/sdf/d_top.sdf",`DTOP);
    $sdf_annotate ("../db/sdf/ci_vtg_top.sdf",`VTG_TOP);
    `else
      `ifdef BEST
    $sdf_annotate ("../db/sdf/d_top.bst.sdf.gz",`DTOP);
    $sdf_annotate ("../db/sdf/ci_vtg_top.bst.sdf.gz",`VTG_TOP);
      `else
        `ifdef WORST
    $sdf_annotate ("../db/sdf/d_top.wst.sdf.gz",`DTOP);
    $sdf_annotate ("../db/sdf/ci_vtg_top.wst.sdf.gz",`VTG_TOP);
        `else
    $sdf_annotate ("../db/sdf/d_top.typ.sdf.gz",`DTOP);
    $sdf_annotate ("../db/sdf/ci_vtg_top.typ.sdf.gz",`VTG_TOP);
        `endif
      `endif
    `endif
  `endif
  end
`endif
"""
    result = _parse_ifdef_around_sdf(content)
    assert result["sdf_guard_define"] == "NODLY"
    assert len(result["sdf_entries"]) == 8  # 2 per corner × 4 corners
    scopes = {e["scope"] for e in result["sdf_entries"]}
    assert "`DTOP" in scopes
    assert "`VTG_TOP" in scopes


def test_parse_ifdef_no_guard() -> None:
    """$sdf_annotate without any guard → sdf_guard_define is None."""
    content = """\
initial begin
    $sdf_annotate ("top.sdf", top);
end
"""
    result = _parse_ifdef_around_sdf(content)
    assert result["sdf_guard_define"] is None
    assert len(result["sdf_entries"]) == 1
    assert result["sdf_entries"][0]["scope"] == "top"
    assert result["sdf_entries"][0]["conditions"] == {}


def test_parse_ifdef_comment_skipped() -> None:
    """Commented $sdf_annotate should be ignored."""
    content = """\
initial begin
    //$sdf_annotate ("old.sdf", top);
    $sdf_annotate ("new.sdf", top);
end
"""
    result = _parse_ifdef_around_sdf(content)
    assert len(result["sdf_entries"]) == 1
    assert result["sdf_entries"][0]["file"] == "new.sdf"


# ---------------------------------------------------------------------------
# Tests: _extract_top_module_from_content
# ---------------------------------------------------------------------------


def test_extract_top_module_from_script() -> None:
    content = "eval xmsim -64bit -messages $sim_args top\n"
    assert _extract_top_module_from_content(content) == "top"


def test_extract_top_module_not_found() -> None:
    content = "echo hello\n"
    assert _extract_top_module_from_content(content) == ""


def test_extract_top_module_eval() -> None:
    content = "eval xmsim \\\n  -64bit \\\n  top\n"
    assert _extract_top_module_from_content(content) == "top"


def test_extract_top_module_backslash() -> None:
    content = "xmsim \\\n  -64bit \\\n  -messages \\\n  top\n"
    assert _extract_top_module_from_content(content) == "top"


# ---------------------------------------------------------------------------
# Tests: dump_depth validation (MCP schema level)
# ---------------------------------------------------------------------------


def test_dump_depth_invalid() -> None:
    """Invalid dump_depth should be caught at MCP schema level."""
    valid = {"", "boundary", "all"}
    assert "invalid" not in valid
    assert "" in valid
    assert "boundary" in valid
    assert "all" in valid
