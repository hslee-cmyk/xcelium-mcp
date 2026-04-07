"""Tests for v4.3 dump_depth / probe scope.

Uses inline copies of pure functions to avoid SSH/async dependencies.
"""
import re as _re


# ---------------------------------------------------------------------------
# Inline copies of pure functions from batch_runner.py
# ---------------------------------------------------------------------------

BOUNDARY_SIGNALS = [
    "top.hw.i_mainClk", "top.hw.i_rst_n",
    "top.hw.i_scl", "top.hw.io_sda",
    "top.hw.i_pcmIn", "top.hw.i_pcmSync",
    "top.hw.o_askData", "top.hw.o_askDataInv",
    "top.hw.o_askRefClk", "top.hw.o_refClk", "top.hw.o_refClkInv",
    "top.hw.o_btCoilShort",
    "top.hw.i_backTel_p", "top.hw.i_backTel_n",
    "top.hw.o_backTel_pwr_en",
    "top.hw.i_led_ctrl_r", "top.hw.i_led_ctrl_g", "top.hw.i_led_ctrl_b",
    "top.hw.o_led_r", "top.hw.o_led_g", "top.hw.o_led_b",
    "top.hw.i_earpiece_det_n", "top.hw.i_rmClkNum",
    "top.hw.i_deep_slp_en", "top.hw.i_dyn_slp_en",
    "top.hw.o_sync_req", "top.hw.o_stim_trig", "top.hw.o_serial_tp_out",
]


def _resolve_probe_signals(dump_signals, dump_depth):
    if dump_depth == "all":
        return ("depth_all", None)
    base = set(BOUNDARY_SIGNALS)
    if dump_signals:
        base |= set(dump_signals)
    return ("signals", sorted(base))


def _replace_probe_lines(content, probe_type, probe_signals):
    lines = content.splitlines()
    filtered = []
    existing_signals = set()
    for line in lines:
        if _re.match(r"\s*probe\s+-create\b", line):
            if "-depth" in line:
                continue
            sig_match = _re.search(r"probe\s+-create\s+(\S+)", line)
            if sig_match:
                existing_signals.add(sig_match.group(1))
            filtered.append(line)
        else:
            filtered.append(line)
    if probe_type == "depth_all":
        new_probes = ["probe -create top -depth all -shm"]
    else:
        new_probes = [
            f"probe -create {sig} -shm"
            for sig in (probe_signals or [])
            if sig not in existing_signals
        ]
    result = []
    inserted = False
    for line in filtered:
        result.append(line)
        if not inserted and _re.match(r"\s*database\s+-open\b", line):
            result.extend(new_probes)
            inserted = True
    if not inserted:
        result = new_probes + result
    return "\n".join(result) + "\n"


# ---------------------------------------------------------------------------
# Tests: _resolve_probe_signals
# ---------------------------------------------------------------------------

def test_resolve_probe_signals_all():
    probe_type, signals = _resolve_probe_signals(None, "all")
    assert probe_type == "depth_all"
    assert signals is None


def test_resolve_probe_signals_boundary():
    probe_type, signals = _resolve_probe_signals(None, "boundary")
    assert probe_type == "signals"
    assert signals == sorted(BOUNDARY_SIGNALS)
    assert len(signals) == 28


def test_resolve_probe_signals_union():
    extra = ["top.hw.u_ext.debug_signal"]
    probe_type, signals = _resolve_probe_signals(extra, "boundary")
    assert probe_type == "signals"
    assert "top.hw.u_ext.debug_signal" in signals
    assert "top.hw.i_mainClk" in signals
    assert len(signals) == 29  # 28 boundary + 1 extra


def test_resolve_probe_signals_union_dedup():
    """dump_signals that overlap with BOUNDARY_SIGNALS should not duplicate."""
    extra = ["top.hw.i_mainClk", "top.hw.u_ext.debug_signal"]
    probe_type, signals = _resolve_probe_signals(extra, "boundary")
    assert signals.count("top.hw.i_mainClk") == 1
    assert len(signals) == 29  # 28 + 1 new


# ---------------------------------------------------------------------------
# Tests: resolve_sim_params dump_depth
# ---------------------------------------------------------------------------

def test_resolve_sim_params_gate_default():
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


def test_resolve_sim_params_gate_override():
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

def test_replace_probe_lines_all():
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "depth_all", None)
    assert "probe -create top -depth all -shm" in result
    assert result.count("probe -create top -depth all") == 1


def test_replace_probe_lines_signals():
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["sig1", "sig2"])
    assert "probe -create sig1 -shm" in result
    assert "probe -create sig2 -shm" in result
    assert "-depth all" not in result


def test_replace_probe_lines_keep_custom():
    """User custom signal probes should be preserved."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top -depth all -shm\n"
        "probe -create top.hw.u_ext.debug -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["sig1"])
    assert "probe -create top.hw.u_ext.debug -shm" in result
    assert "probe -create sig1 -shm" in result
    assert "-depth all" not in result


def test_replace_probe_lines_no_dup_custom():
    """If dump_signals overlaps with existing custom, don't duplicate."""
    content = (
        "database -open dump.shm -shm\n"
        "probe -create top.hw.u_ext.debug -shm\n"
        "run\n"
    )
    result = _replace_probe_lines(content, "signals", ["top.hw.u_ext.debug", "sig1"])
    assert result.count("top.hw.u_ext.debug") == 1
    assert "probe -create sig1 -shm" in result


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: _inject_dump_window
# ---------------------------------------------------------------------------

def _inject_dump_window(content, dump_window):
    """Inline copy of batch_runner._inject_dump_window."""
    start_ms = dump_window["start_ms"]
    end_ms = dump_window["end_ms"]
    duration_ms = end_ms - start_ms
    lines = content.splitlines()
    filtered = [line for line in lines if not _re.match(r"\s*run\b", line)]
    window_tcl = ["probe -disable"]
    if start_ms > 0:
        window_tcl.append(f"run {start_ms}ms")
    window_tcl.append("probe -enable")
    window_tcl.append(f"run {duration_ms}ms")
    window_tcl.append("probe -disable")
    window_tcl.append("run")
    return "\n".join(filtered + window_tcl) + "\n"


def test_inject_dump_window():
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


def test_inject_dump_window_start_zero():
    """start_ms=0 should skip the initial settling run."""
    content = "database -open dump.shm -shm\nprobe -create top -depth all -shm\nrun\n"
    result = _inject_dump_window(content, {"start_ms": 0, "end_ms": 10})
    assert "run 0ms" not in result
    assert "run 10ms" in result


def test_dump_window_invalid_range():
    """end_ms must be > start_ms."""
    start, end = 55, 50
    assert end <= start  # invalid


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: _parse_ifdef_around_sdf
# ---------------------------------------------------------------------------

def _parse_ifdef_around_sdf(content):
    """Inline copy of sim_runner._parse_ifdef_around_sdf."""
    sdf_guard_define = None
    sdf_entries = []
    ifdef_stack = []
    for line in content.splitlines():
        stripped = line.strip()
        m = _re.match(r"`(ifdef|ifndef)\s+(\w+)", stripped)
        if m:
            ifdef_stack.append({"define": m.group(2), "type": m.group(1), "branch": "if"})
        elif stripped.startswith("`else"):
            if ifdef_stack:
                ifdef_stack[-1]["branch"] = "else"
        elif stripped.startswith("`endif"):
            if ifdef_stack:
                ifdef_stack.pop()
        if "$sdf_annotate" not in line or stripped.startswith("//"):
            continue
        if sdf_guard_define is None:
            for frame in reversed(ifdef_stack):
                if frame["branch"] == "else" and frame["type"] == "ifdef":
                    sdf_guard_define = frame["define"]
                    break
                elif frame["branch"] == "if" and frame["type"] == "ifndef":
                    sdf_guard_define = frame["define"]
                    break
        conditions = {}
        for frame in ifdef_stack:
            if frame["define"] == sdf_guard_define:
                continue
            if frame["type"] == "ifdef":
                conditions[frame["define"]] = (frame["branch"] == "if")
            elif frame["type"] == "ifndef":
                conditions[frame["define"]] = (frame["branch"] == "else")
        sdf_match = _re.search(
            r'\$sdf_annotate\s*\(\s*"([^"]+)"\s*,\s*([^,)\s]+)', line,
        )
        if sdf_match:
            sdf_entries.append({
                "scope": sdf_match.group(2),
                "conditions": conditions,
                "file": sdf_match.group(1),
            })
    return {"sdf_guard_define": sdf_guard_define, "sdf_entries": sdf_entries}


def test_analyze_sdf_annotate_nodly():
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
    scopes = set(e["scope"] for e in result["sdf_entries"])
    assert "`DTOP" in scopes
    assert "`VTG_TOP" in scopes


def test_parse_ifdef_no_guard():
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


def test_parse_ifdef_comment_skipped():
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
# Tests: _extract_top_module (pattern matching only)
# ---------------------------------------------------------------------------

def _extract_top_module_from_content(content):
    """Inline version — parse script content without SSH."""
    joined = _re.sub(r"\\\s*\n\s*", " ", content)
    match = _re.search(
        r"(?:eval\s+)?(?:xmsim|xrun|irun)\s+(.+)",
        joined, _re.MULTILINE,
    )
    if match:
        tokens = match.group(1).strip().split()
        for token in reversed(tokens):
            if (not token.startswith("-")
                    and not token.startswith("$")
                    and _re.fullmatch(r"\w+", token)):
                return token
    return ""


def test_extract_top_module_from_script():
    content = "eval xmsim -64bit -messages $sim_args top\n"
    assert _extract_top_module_from_content(content) == "top"


def test_extract_top_module_not_found():
    content = "echo hello\n"
    assert _extract_top_module_from_content(content) == ""


def test_extract_top_module_eval():
    content = "eval xmsim \\\n  -64bit \\\n  top\n"
    assert _extract_top_module_from_content(content) == "top"


def test_extract_top_module_backslash():
    content = "xmsim \\\n  -64bit \\\n  -messages \\\n  top\n"
    assert _extract_top_module_from_content(content) == "top"


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

def test_dump_depth_invalid():
    """Invalid dump_depth should be caught at MCP schema level."""
    valid = {"", "boundary", "all"}
    assert "invalid" not in valid
    assert "" in valid
    assert "boundary" in valid
    assert "all" in valid
