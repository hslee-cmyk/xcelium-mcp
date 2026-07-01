"""Tests for v5.2 hierarchical dump strategy.

Covers _resolve_probe_signals, get_dump_strategy, and _preprocess_setup_tcl
backward compat and new hierarchical behavior.
"""
from __future__ import annotations

import pytest

from xcelium_mcp.tcl_preprocessing import (
    BOUNDARY_SIGNALS,
    _resolve_probe_signals,
    get_dump_strategy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STRATEGY_SIMPLE = {
    "top_boundary": ["top.i_clk", "top.i_rst_n"],
    "default_block_policy": "skip",
    "block_boundaries": {
        "top.u_blk_a": ["top.u_blk_a.i_a", "top.u_blk_a.o_b"],
        "top.u_blk_b": ["top.u_blk_b.i_x", "top.u_blk_b.o_y", "top.u_blk_b.o_z"],
    },
}

STRATEGY_OPT_OUT = {
    "top_boundary": ["top.i_clk", "top.i_rst_n"],
    "default_block_policy": "boundary",
    "block_boundaries": {
        "top.u_blk_a": ["top.u_blk_a.i_a", "top.u_blk_a.o_b"],
        "top.u_blk_b": ["top.u_blk_b.i_x", "top.u_blk_b.o_y", "top.u_blk_b.o_z"],
    },
}

STRATEGY_GLOB = {
    "top_boundary": ["top.i_clk"],
    "default_block_policy": "skip",
    "block_boundaries": {
        "top.hw.u_ext.u_blk_a": ["top.hw.u_ext.u_blk_a.i_a"],
        "top.hw.u_ext.u_blk_b": ["top.hw.u_ext.u_blk_b.i_x"],
        "top.hw.u_core.u_blk_c": ["top.hw.u_core.u_blk_c.i_p"],
    },
}

# ---------------------------------------------------------------------------
# Test: get_dump_strategy
# ---------------------------------------------------------------------------

def test_get_dump_strategy_mode_keyed_rtl():
    config = {"dump_strategy": {"rtl": STRATEGY_SIMPLE, "gate": {}}}
    s = get_dump_strategy(config, "rtl")
    assert s is STRATEGY_SIMPLE

def test_get_dump_strategy_ams_gate_delegates_to_gate():
    gate_strat = {"top_boundary": ["top.x"], "block_boundaries": {}}
    config = {"dump_strategy": {"gate": gate_strat}}
    s = get_dump_strategy(config, "ams_gate")
    assert s is gate_strat

def test_get_dump_strategy_flat_fallback():
    config = {"dump_strategy": {"top_boundary": ["top.x"], "block_boundaries": {}}}
    s = get_dump_strategy(config, "rtl")
    assert "top_boundary" in s

def test_get_dump_strategy_empty():
    assert get_dump_strategy({}, "rtl") == {}

# ---------------------------------------------------------------------------
# Test: backward compat — dump_depth="all"
# ---------------------------------------------------------------------------

def test_depth_all_backward_compat():
    probe_type, probe_info, summary = _resolve_probe_signals([], "all")
    assert probe_type == "depth_all"
    assert probe_info is None
    assert summary is None

def test_depth_all_ignores_dump_scopes():
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "all", dump_scopes={"top.u_blk": "boundary"}, dump_strategy=STRATEGY_SIMPLE
    )
    assert probe_type == "depth_all"
    assert probe_info is None
    assert summary is None

# ---------------------------------------------------------------------------
# Test: backward compat — no block_boundaries
# ---------------------------------------------------------------------------

def test_boundary_no_block_boundaries():
    """v5.1 compat: no block_boundaries + no dump_scopes → signals type, summary=None."""
    probe_type, probe_info, summary = _resolve_probe_signals([], "boundary")
    assert probe_type == "signals"
    assert isinstance(probe_info, list)
    assert set(probe_info) == set(BOUNDARY_SIGNALS)
    assert summary is None

def test_boundary_no_block_boundaries_with_dump_signals():
    probe_type, probe_info, summary = _resolve_probe_signals(
        ["extra.sig"], "boundary"
    )
    assert probe_type == "signals"
    assert "extra.sig" in probe_info
    assert summary is None

# ---------------------------------------------------------------------------
# Test: opt-out model (default_policy="boundary")
# ---------------------------------------------------------------------------

def test_opt_out_all_blocks():
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary", dump_strategy=STRATEGY_OPT_OUT, sim_mode="rtl"
    )
    assert probe_type == "hierarchical"
    signals = set(probe_info["signals"])
    assert "top.i_clk" in signals
    assert "top.u_blk_a.i_a" in signals
    assert "top.u_blk_b.o_z" in signals
    assert probe_info["scope_probes"] == []
    assert summary["block_boundaries"]["top.u_blk_a"] == 2
    assert summary["block_boundaries"]["top.u_blk_b"] == 3
    assert summary["top_boundary_count"] == 2
    assert summary["sim_mode"] == "rtl"

# ---------------------------------------------------------------------------
# Test: dump_scopes overrides
# ---------------------------------------------------------------------------

def test_dump_scopes_all_override():
    """dump_scopes={X: "all"} → scope_probe added, X boundary signals removed."""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk_a": "all"},
        dump_strategy=STRATEGY_SIMPLE,
    )
    assert probe_type == "hierarchical"
    signals = set(probe_info["signals"])
    # u_blk_a boundary signals removed (replaced by scope probe)
    assert "top.u_blk_a.i_a" not in signals
    assert "top.u_blk_a.o_b" not in signals
    assert probe_info["scope_probes"] == [{"scope": "top.u_blk_a", "depth": "all"}]
    # top_boundary still present
    assert "top.i_clk" in signals

def test_dump_scopes_skip():
    """dump_scopes={X: "skip"} in opt-out → removes X from signal set, count=0."""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk_a": "skip"},
        dump_strategy=STRATEGY_OPT_OUT,
    )
    assert probe_type == "hierarchical"
    signals = set(probe_info["signals"])
    assert "top.u_blk_a.i_a" not in signals
    assert summary["block_boundaries"]["top.u_blk_a"] == 0
    # u_blk_b still included
    assert "top.u_blk_b.i_x" in signals
    assert summary["block_boundaries"]["top.u_blk_b"] == 3

def test_dump_scopes_boundary_optin():
    """dump_scopes={X: "boundary"} in opt-in → adds X signals."""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk_a": "boundary"},
        dump_strategy=STRATEGY_SIMPLE,
    )
    assert probe_type == "hierarchical"
    signals = set(probe_info["signals"])
    assert "top.u_blk_a.i_a" in signals
    assert "top.u_blk_a.o_b" in signals
    # u_blk_b NOT included (opt-in, not requested)
    assert "top.u_blk_b.i_x" not in signals
    assert summary["block_boundaries"]["top.u_blk_b"] == 0

# ---------------------------------------------------------------------------
# Test: glob patterns
# ---------------------------------------------------------------------------

def test_glob_subtree_all():
    """glob "top.hw.u_ext.*": "all" → pattern sent to TCL as-is."""
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.hw.u_ext.*": "all"},
        dump_strategy=STRATEGY_GLOB,
    )
    assert probe_type == "hierarchical"
    # scope_probe contains the glob pattern directly
    assert {"scope": "top.hw.u_ext.*", "depth": "all"} in probe_info["scope_probes"]
    # matched block boundary signals removed from flat list
    signals = set(probe_info["signals"])
    assert "top.hw.u_ext.u_blk_a.i_a" not in signals
    assert "top.hw.u_ext.u_blk_b.i_x" not in signals
    # non-matched block untouched (still skip policy, so absent)
    assert "top.hw.u_core.u_blk_c.i_p" not in signals

def test_glob_subtree_skip():
    """glob "top.hw.u_ext.*": "skip" in opt-out → removes matched block signals."""
    strat = {
        "top_boundary": ["top.i_clk"],
        "default_block_policy": "boundary",
        "block_boundaries": {
            "top.hw.u_ext.u_blk_a": ["top.hw.u_ext.u_blk_a.i_a"],
            "top.hw.u_ext.u_blk_b": ["top.hw.u_ext.u_blk_b.i_x"],
            "top.hw.u_core.u_blk_c": ["top.hw.u_core.u_blk_c.i_p"],
        },
    }
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.hw.u_ext.*": "skip"},
        dump_strategy=strat,
    )
    assert probe_type == "hierarchical"
    signals = set(probe_info["signals"])
    assert "top.hw.u_ext.u_blk_a.i_a" not in signals
    assert "top.hw.u_ext.u_blk_b.i_x" not in signals
    # u_core.u_blk_c unaffected → still included
    assert "top.hw.u_core.u_blk_c.i_p" in signals
    assert summary["block_boundaries"]["top.hw.u_ext.u_blk_a"] == 0
    assert summary["block_boundaries"]["top.hw.u_core.u_blk_c"] == 1

# ---------------------------------------------------------------------------
# Test: validation
# ---------------------------------------------------------------------------

def test_invalid_dump_scopes_value():
    with pytest.raises(ValueError, match="Invalid dump_scopes value"):
        _resolve_probe_signals(
            [], "boundary",
            dump_scopes={"top.u_blk_a": "invalid"},
            dump_strategy=STRATEGY_SIMPLE,
        )

# ---------------------------------------------------------------------------
# Test: dump_summary structure
# ---------------------------------------------------------------------------

def test_dump_summary_structure():
    probe_type, probe_info, summary = _resolve_probe_signals(
        [], "boundary",
        dump_scopes={"top.u_blk_a": "boundary"},
        dump_strategy=STRATEGY_SIMPLE,
        sim_mode="gate",
    )
    assert probe_type == "hierarchical"
    assert summary["dump_depth"] == "boundary"
    assert summary["sim_mode"] == "gate"
    assert "top_boundary_count" in summary
    assert "block_boundaries" in summary
    assert "scope_overrides" in summary
    assert "total_signals" in summary
    assert summary["scope_overrides"] == {"top.u_blk_a": "boundary"}
    assert summary["total_signals"] == len(probe_info["signals"])
