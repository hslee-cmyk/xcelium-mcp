"""Unit tests for sim_lifecycle tool behaviors — no real MCP or bridge needed."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _MockMCP:
    """Captures tools registered via @mcp.tool() so they can be called directly."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self):
        def decorator(f):
            self.tools[f.__name__] = f
            return f
        return decorator


# ---------------------------------------------------------------------------
# F-078: Surface RUN_ERROR from __RUN_AND_REPORT__ as ERROR response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_run_run_error_surfaces_error() -> None:
    """RUN_ERROR prefix from bridge should be returned as ERROR to caller."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(
        return_value="RUN_ERROR:bad duration\n(pos)"
    )

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert result.startswith("ERROR"), f"Expected ERROR prefix, got: {result!r}"
    assert "RUN_ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_success_returns_position() -> None:
    """Normal bridge response should return 'Simulation advanced' message."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert result.startswith("Simulation advanced"), f"Unexpected result: {result!r}"
    assert "100 NS" in result


# ---------------------------------------------------------------------------
# F-079: _DURATION_RE at module scope + duration.strip() before fullmatch
# ---------------------------------------------------------------------------


def test_duration_re_accessible_at_module_scope() -> None:
    """_DURATION_RE should be importable from module scope (not inside register())."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    assert isinstance(_DURATION_RE, re.Pattern)


def test_duration_re_matches_with_leading_trailing_space() -> None:
    """Stripped duration should match — strip() happens before fullmatch."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    # The regex itself matches clean values; strip is done in sim_run before calling fullmatch
    assert _DURATION_RE.fullmatch("100ns") is not None
    assert _DURATION_RE.fullmatch("  100ns  ") is None  # regex gets pre-stripped value


# ---------------------------------------------------------------------------
# F-146: _DURATION_RE must accept a decimal fraction without opening injection
# ---------------------------------------------------------------------------


def test_duration_re_accepts_decimal_duration() -> None:
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    assert _DURATION_RE.fullmatch("100.5ns") is not None
    assert _DURATION_RE.fullmatch("1.25ms") is not None


def test_duration_re_still_rejects_bare_integer() -> None:
    """Unit remains mandatory — decimal support must not loosen this rule."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    assert _DURATION_RE.fullmatch("100") is None
    assert _DURATION_RE.fullmatch("100.5") is None


def test_duration_re_still_rejects_injection_payloads() -> None:
    """Decimal support must not open a path for Tcl metacharacters."""
    from xcelium_mcp.tools.sim_lifecycle import _DURATION_RE
    assert _DURATION_RE.fullmatch("100ns; exec rm") is None
    assert _DURATION_RE.fullmatch("100.5ns; exec rm") is None
    assert _DURATION_RE.fullmatch("1.2.3ns") is None  # malformed decimal rejected


@pytest.mark.asyncio
async def test_sim_run_accepts_decimal_duration() -> None:
    """Duration with a decimal fraction should pass validation (F-146)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100.5ns")
    assert "Simulation advanced" in result


# ---------------------------------------------------------------------------
# F-081: sim_stop passes timeout to bridge.execute
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# F-080: Harden sim_run duration — length cap + ASCII-only digits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_run_rejects_too_long_duration() -> None:
    """Duration longer than 32 chars should be rejected immediately."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="ok")

    register(mock_mcp, mock_bridges)

    long_dur = "9" * 100 + "ns"
    result = await mock_mcp.tools["sim_run"](duration=long_dur)
    assert "ERROR" in result and "too long" in result


@pytest.mark.asyncio
async def test_sim_run_rejects_unicode_digits() -> None:
    """Unicode digits like '１００ns' should be rejected (ASCII-only check)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="ok")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="１００ns")
    assert "ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_accepts_normal_duration() -> None:
    """'100ns' should pass all validation."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert "ERROR" not in result or "RUN_ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_strips_duration_before_validation() -> None:
    """sim_run with leading/trailing space on duration should pass validation."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    # Duration with whitespace should not fail validation
    result = await mock_mcp.tools["sim_run"](duration="  100ns  ")
    assert "ERROR" not in result or "RUN_ERROR" in result, (
        f"Whitespace duration should not trigger validation error: {result!r}"
    )


# ---------------------------------------------------------------------------
# F-082: Catch asyncio.TimeoutError in sim_run with actionable message
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# F-083: Require explicit time unit in sim_run duration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sim_run_rejects_bare_integer_duration() -> None:
    """Duration without unit (e.g. '100') should be rejected."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="ok")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100")
    assert "ERROR" in result


@pytest.mark.asyncio
async def test_sim_run_accepts_duration_with_unit() -> None:
    """Duration with explicit unit should pass validation."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(return_value="Time: 100 NS")

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns")
    assert "Simulation advanced" in result


@pytest.mark.asyncio
async def test_sim_run_timeout_returns_actionable_error() -> None:
    """asyncio.TimeoutError from bridge should surface as ERROR with timeout guidance."""
    import asyncio as _asyncio

    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim.execute = AsyncMock(side_effect=_asyncio.TimeoutError())

    register(mock_mcp, mock_bridges)

    result = await mock_mcp.tools["sim_run"](duration="100ns", timeout=5.0)
    assert result.startswith("ERROR"), f"Expected ERROR prefix: {result!r}"
    assert "timeout" in result.lower() or "5.0" in result


# ---------------------------------------------------------------------------
# F-099: sim_disconnect shutdown target=all — independent per-bridge shutdown
# ---------------------------------------------------------------------------


def _make_connected_bridge(port: int = 9876) -> MagicMock:
    """Return a mock TclBridge that appears connected."""
    bridge = MagicMock()
    bridge.connected = True
    bridge.port = port
    resp = MagicMock()
    resp.body = "ok"
    bridge.execute_safe = AsyncMock(return_value=resp)
    return bridge


@pytest.mark.asyncio
async def test_shutdown_all_only_simvision_connected() -> None:
    """target=all, xmsim not connected, simvision connected → simvision shutdown, no error."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    sv_bridge = _make_connected_bridge(port=9877)
    mock_bridges = MagicMock()
    mock_bridges.xmsim_raw = None          # xmsim not connected
    mock_bridges.simvision_raw = sv_bridge

    with patch("xcelium_mcp.tools.sim_lifecycle.get_user_tmp_dir",
               new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
         patch("xcelium_mcp.tools.sim_lifecycle.shell_run",
               new_callable=AsyncMock, return_value=""):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["sim_disconnect"](action="shutdown", target="all")

    assert "ERROR" not in result, f"Should not error when simvision is connected: {result!r}"
    assert "simvision: shutdown ok" in result
    assert "xmsim: not connected (skipped)" in result
    mock_bridges.set_simvision.assert_called_once_with(None)
    mock_bridges.set_xmsim.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_all_only_xmsim_connected() -> None:
    """target=all, xmsim connected, simvision not connected → xmsim shutdown only."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    xm_bridge = _make_connected_bridge(port=9876)
    mock_bridges = MagicMock()
    mock_bridges.xmsim_raw = xm_bridge
    mock_bridges.simvision_raw = None      # simvision not connected

    with patch("xcelium_mcp.tools.sim_lifecycle.get_user_tmp_dir",
               new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
         patch("xcelium_mcp.tools.sim_lifecycle.shell_run",
               new_callable=AsyncMock, return_value=""):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["sim_disconnect"](action="shutdown", target="all")

    assert "ERROR" not in result, f"Should not error when xmsim is connected: {result!r}"
    assert "xmsim: shutdown ok" in result
    assert "simvision: not connected (skipped)" in result
    mock_bridges.set_xmsim.assert_called_once_with(None)
    mock_bridges.set_simvision.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_all_both_disconnected_returns_error() -> None:
    """target=all, both not connected → ERROR."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim_raw = None
    mock_bridges.simvision_raw = None

    with patch("xcelium_mcp.tools.sim_lifecycle.get_user_tmp_dir",
               new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
         patch("xcelium_mcp.tools.sim_lifecycle.shell_run",
               new_callable=AsyncMock, return_value=""):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["sim_disconnect"](action="shutdown", target="all")

    assert result.startswith("ERROR"), f"Expected ERROR when both disconnected: {result!r}"


@pytest.mark.asyncio
async def test_shutdown_all_both_connected() -> None:
    """target=all, both connected → both shutdown, no error."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    xm_bridge = _make_connected_bridge(port=9876)
    sv_bridge = _make_connected_bridge(port=9877)
    mock_bridges = MagicMock()
    mock_bridges.xmsim_raw = xm_bridge
    mock_bridges.simvision_raw = sv_bridge

    with patch("xcelium_mcp.tools.sim_lifecycle.get_user_tmp_dir",
               new_callable=AsyncMock, return_value="/tmp/mcp_test"), \
         patch("xcelium_mcp.tools.sim_lifecycle.shell_run",
               new_callable=AsyncMock, return_value=""):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["sim_disconnect"](action="shutdown", target="all")

    assert "ERROR" not in result, f"Should not error when both connected: {result!r}"
    assert "xmsim: shutdown ok" in result
    assert "simvision: shutdown ok" in result
    mock_bridges.set_xmsim.assert_called_once_with(None)
    mock_bridges.set_simvision.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# F-100: connect_simulator exposes xmsim_pid in result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_simulator_xmsim_result_contains_pid() -> None:
    """connect_simulator result includes xmsim_pid line when xmsim is connected."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim_pid = 12345

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")
    mock_bridge_inst.execute = AsyncMock(return_value="Time: 0 NS")

    with patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=12345):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["connect_simulator"](
            host="localhost", port=9876, target="xmsim"
        )

    assert "xmsim_pid: 12345" in result, f"Expected xmsim_pid in result: {result!r}"


# ---------------------------------------------------------------------------
# F-107: _duration_to_ns + _parse_chunked_run_report
# ---------------------------------------------------------------------------


class TestDurationToNs:
    def test_ns(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("100ns") == 100

    def test_us(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("1us") == 1_000

    def test_ms(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("10ms") == 10_000_000

    def test_s(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("1s") == 1_000_000_000

    def test_ps(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("500ps") == 0  # 0.5ns truncated to int

    def test_case_insensitive(self):
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("10MS") == 10_000_000

    def test_decimal_ns(self):
        """F-146: _duration_to_ns already float()-converted; the gate (_DURATION_RE)
        was the only piece rejecting decimals before this string ever got here."""
        from xcelium_mcp.tools.sim_lifecycle import _duration_to_ns
        assert _duration_to_ns("100.5ns") == 100  # int(100.5 * 1.0) truncates


class TestParseChunkedRunReport:
    def test_completed(self):
        from xcelium_mcp.tools.sim_lifecycle import _parse_chunked_run_report
        raw = "CHUNKED_RUN_REPORT\nsim_time:100ns\nrequested:10000000ns\nstatus:completed\n"
        result = _parse_chunked_run_report(raw)
        assert "100ns" in result
        assert "stopped" not in result

    def test_stopped(self):
        from xcelium_mcp.tools.sim_lifecycle import _parse_chunked_run_report
        raw = "CHUNKED_RUN_REPORT\nsim_time:500us\nrequested:10000000ns\nstatus:stopped\nreason:user_stop\n"
        result = _parse_chunked_run_report(raw)
        assert "stopped" in result.lower()
        assert "500us" in result

    def test_error(self):
        from xcelium_mcp.tools.sim_lifecycle import _parse_chunked_run_report
        raw = "CHUNKED_RUN_REPORT\nsim_time:0ns\nrequested:10000000ns\nstatus:error\nerror:some error\n"
        result = _parse_chunked_run_report(raw)
        assert "ERROR" in result


# ---------------------------------------------------------------------------
# F-C: attach 모호성 해소 — connect_simulator(sim_dir=...) + _auto_connect_all
# ambiguity fail-loud (design.md §5.6/§5.3, plan.md T-4/T-5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_simulator_sim_dir_uses_registry_port() -> None:
    """sim_dir with a known registry bridge_port connects directly to that port
    — must not fall through to the ambiguous ready-file scan."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.xmsim_pid = None

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")
    mock_bridge_inst.execute = AsyncMock(return_value="Time: 0 NS")

    with patch("xcelium_mcp.tools.sim_lifecycle.get_bridge_port",
               new_callable=AsyncMock, return_value=9881) as mock_get_port, \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge",
               return_value=mock_bridge_inst) as mock_tcl_bridge, \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=555), \
         patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock) as mock_scan:
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["connect_simulator"](sim_dir="/projects/A/sim")

    mock_get_port.assert_awaited_once_with("/projects/A/sim")
    mock_tcl_bridge.assert_called_once()
    assert mock_tcl_bridge.call_args.kwargs["port"] == 9881
    mock_scan.assert_not_called()  # no ambiguous glob scan when sim_dir resolves directly
    assert "Connected to xmsim at localhost:9881" in result


@pytest.mark.asyncio
async def test_connect_simulator_sim_dir_miss_falls_back_to_scan() -> None:
    """sim_dir with no registry entry falls back to the existing auto-scan path
    (single candidate — behavior unchanged from before F-C)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")

    with patch("xcelium_mcp.tools.sim_lifecycle.get_bridge_port",
               new_callable=AsyncMock, return_value=None), \
         patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock, return_value=[(9876, "xmsim")]), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=123):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["connect_simulator"](sim_dir="/projects/unknown/sim")

    assert "Connected:" in result
    assert "xmsim:9876" in result


@pytest.mark.asyncio
async def test_auto_connect_all_single_candidate_per_type_unchanged() -> None:
    """Regression: exactly one ready file per type still connects normally
    (this is the common single-session case covered before F-C)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")

    with patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock, return_value=[(9876, "xmsim"), (9877, "simvision")]), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=123):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["connect_simulator"](port=0, target="auto")

    assert "AMBIGUOUS" not in result
    assert "xmsim:9876" in result
    assert "simvision:9877" in result
    mock_bridges.set_xmsim.assert_called_once()
    mock_bridges.set_simvision.assert_called_once()


@pytest.mark.asyncio
async def test_auto_connect_all_ambiguous_type_fails_loud_without_overwriting() -> None:
    """Two live xmsim bridges (two concurrently-debugged sim_dirs) must not be
    silently connected-and-overwritten — F-C requires an explicit ambiguity
    error instead (plan.md T-5)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")

    with patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock, return_value=[(9876, "xmsim"), (9877, "xmsim")]), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["connect_simulator"](port=0, target="auto")

    assert "AMBIGUOUS" in result
    assert "9876" in result and "9877" in result
    assert "sim_dir" in result
    # Must not silently connect to either candidate and clobber bridges.xmsim.
    mock_bridges.set_xmsim.assert_not_called()


# ---------------------------------------------------------------------------
# F-D (session-state-reattach): connect_simulator restores current_test_name/
# current_tb_source onto a fresh BridgeManager via the F-C direct-hit path.
# Design ref: docs/02-design/features/xcelium-mcp-session-state-reattach.design.md §5.2
# Plan SC: T-2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_simulator_sim_dir_restores_session_state() -> None:
    """T-2: F-C direct-hit reconnect restores test_name/tb_source from the
    registry onto the (fresh) BridgeManager — this is what lets a subsequent
    checkpoint(action=save) record correct TB provenance after a worker
    restart."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.current_test_name = ""
    mock_bridges.current_tb_source = None

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")
    mock_bridge_inst.execute = AsyncMock(return_value="Time: 0 NS")

    tb_source = {"files": [{"path": "/proj/tb/top015.sv", "sha256": "abc"}], "combined_sha256": "def"}

    with patch("xcelium_mcp.tools.sim_lifecycle.get_bridge_port",
               new_callable=AsyncMock, return_value=9881), \
         patch("xcelium_mcp.tools.sim_lifecycle.get_session_state",
               new_callable=AsyncMock, return_value=("TOP015", tb_source)) as mock_get_state, \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=555):
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["connect_simulator"](sim_dir="/proj/sim")

    mock_get_state.assert_awaited_once_with("/proj/sim")
    assert mock_bridges.current_test_name == "TOP015"
    assert mock_bridges.current_tb_source == tb_source


@pytest.mark.asyncio
async def test_connect_simulator_auto_path_does_not_restore_session_state() -> None:
    """Reattachment via the legacy auto-scan path (no sim_dir) has no sim_dir
    to look up session state with — must not attempt to restore anything
    (out of scope per plan.md §2 F-D, verified here as a negative test)."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")

    with patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock, return_value=[(9876, "xmsim")]), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=123), \
         patch("xcelium_mcp.tools.sim_lifecycle.get_session_state",
               new_callable=AsyncMock) as mock_get_state:
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["connect_simulator"](port=0, target="auto")

    mock_get_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# F-2 (sim-session-reaper): bridge instances learn their sim_dir so
# TclBridge.execute_safe() can record activity for the reaper's TTL tracking.
# Design ref: docs/02-design/features/xcelium-mcp-sim-session-reaper.design.md §8
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_simulator_sim_dir_sets_bridge_sim_dir() -> None:
    """F-C direct-hit reconnect must stamp the resolved sim_dir onto the bridge
    instance so activity tracking (registry.touch_activity) has something to
    key off of."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    mock_bridges.current_test_name = ""
    mock_bridges.current_tb_source = None

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")
    mock_bridge_inst.execute = AsyncMock(return_value="Time: 0 NS")

    with patch("xcelium_mcp.tools.sim_lifecycle.get_bridge_port",
               new_callable=AsyncMock, return_value=9881), \
         patch("xcelium_mcp.tools.sim_lifecycle.get_session_state",
               new_callable=AsyncMock, return_value=("", None)), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=555):
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["connect_simulator"](sim_dir="/proj/sim")

    assert mock_bridge_inst.sim_dir == "/proj/sim"


@pytest.mark.asyncio
async def test_connect_simulator_auto_path_does_not_set_bridge_sim_dir() -> None:
    """No sim_dir known (legacy auto-scan) — must not stamp a sim_dir attribute
    that would make activity-tracking key off garbage."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()

    mock_bridge_inst = MagicMock()
    mock_bridge_inst.connect = AsyncMock(return_value="pong")

    with patch("xcelium_mcp.tools.sim_lifecycle.scan_ready_files",
               new_callable=AsyncMock, return_value=[(9876, "xmsim")]), \
         patch("xcelium_mcp.tools.sim_lifecycle.TclBridge", return_value=mock_bridge_inst), \
         patch("xcelium_mcp.tools.sim_lifecycle._get_pid_for_port",
               new_callable=AsyncMock, return_value=123):
        register(mock_mcp, mock_bridges)
        await mock_mcp.tools["connect_simulator"](port=0, target="auto")

    # MagicMock auto-creates attributes on access, so assert it was never
    # explicitly assigned rather than checking for AttributeError.
    assert not hasattr(mock_bridge_inst, "sim_dir") or isinstance(mock_bridge_inst.sim_dir, MagicMock)


# ---------------------------------------------------------------------------
# F-3 (sim-session-reaper): list_active_sessions
# Design ref: docs/02-design/features/xcelium-mcp-sim-session-reaper.design.md §4.3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_sessions_reports_ttl_remaining() -> None:
    """T-9: a fresh bridge session reports TTL remaining, not exceeded."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    now = 100_000.0
    registry = {
        "projects": {
            "/proj": {
                "environments": {
                    "/proj/sim": {
                        "bridge_port": 9876,
                        "current_test_name": "TOP015",
                        "last_activity": now - 10,
                    }
                }
            }
        }
    }

    with patch("xcelium_mcp.tools.sim_lifecycle.load_registry", return_value=registry), \
         patch("xcelium_mcp.sim_session_reaper.time.time", return_value=now), \
         patch("xcelium_mcp.tools.sim_lifecycle._time.time", return_value=now):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["list_active_sessions"]()

    assert "/proj/sim" in result
    assert "TTL remaining" in result
    assert "TOP015" in result


@pytest.mark.asyncio
async def test_list_active_sessions_flags_ttl_exceeded() -> None:
    """T-9: a session past TTL is flagged as pending auto-shutdown."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    now = 100_000.0
    registry = {
        "projects": {
            "/proj": {
                "environments": {
                    "/proj/sim": {"bridge_port": 9876, "last_activity": now - 999_999_999}
                }
            }
        }
    }

    with patch("xcelium_mcp.tools.sim_lifecycle.load_registry", return_value=registry), \
         patch("xcelium_mcp.tools.sim_lifecycle._time.time", return_value=now):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["list_active_sessions"]()

    assert "auto-shutdown" in result


@pytest.mark.asyncio
async def test_list_active_sessions_skips_batch_only_entries() -> None:
    """batch/regression-only registry entries (no bridge_port) must not appear."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    registry = {
        "projects": {
            "/proj": {"environments": {"/proj/sim": {"current_test_name": "TOP015"}}}
        }
    }

    with patch("xcelium_mcp.tools.sim_lifecycle.load_registry", return_value=registry):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["list_active_sessions"]()

    assert "No active bridge sessions" in result


# ---------------------------------------------------------------------------
# F-177: list_tests() pattern filter — glob wildcards were treated as literal
# substrings, so a caller-provided pattern like "*TOP01*" silently matched
# nothing even when matching tests existed (real-world repro: venezia-fpga
# verilog-rtl-debugger agent, 2026-07-08).
# ---------------------------------------------------------------------------


class TestFilterTestNames:
    def test_glob_wildcard_pattern_matches(self) -> None:
        from xcelium_mcp.tools.sim_lifecycle import _filter_test_names

        names = ["VENEZIA_TOP015_i2c_8bit_offset_test", "VENEZIA_TOP016_test", "OTHER_test"]
        assert _filter_test_names(names, "*TOP01*") == [
            "VENEZIA_TOP015_i2c_8bit_offset_test",
            "VENEZIA_TOP016_test",
        ]

    def test_plain_substring_pattern_still_matches(self) -> None:
        """No glob metacharacter — falls back to the original substring match
        (regression: existing callers passing a literal fragment)."""
        from xcelium_mcp.tools.sim_lifecycle import _filter_test_names

        names = ["VENEZIA_TOP015_test", "VENEZIA_TOP016_test", "OTHER_test"]
        assert _filter_test_names(names, "TOP015") == ["VENEZIA_TOP015_test"]

    def test_mixed_question_and_star_glob(self) -> None:
        from xcelium_mcp.tools.sim_lifecycle import _filter_test_names

        names = [
            "VENEZIA_TOP015_i2c_8bit_offset_test",
            "VENEZIA_TOP016_i2c_8bit_offset_test",
            "VENEZIA_TOP015_spi_test",
        ]
        # fnmatch matches the whole string, so a mid-string pattern needs
        # leading/trailing '*' to act as a substring search.
        assert _filter_test_names(names, "*TOP01?_i2c*") == [
            "VENEZIA_TOP015_i2c_8bit_offset_test",
            "VENEZIA_TOP016_i2c_8bit_offset_test",
        ]

    def test_bracket_glob_metacharacter_triggers_fnmatch(self) -> None:
        from xcelium_mcp.tools.sim_lifecycle import _filter_test_names

        names = ["TOP1_test", "TOP2_test", "TOP3_test"]
        assert _filter_test_names(names, "TOP[12]_test") == ["TOP1_test", "TOP2_test"]

    def test_empty_names_returns_empty(self) -> None:
        from xcelium_mcp.tools.sim_lifecycle import _filter_test_names

        assert _filter_test_names([], "*TOP01*") == []


@pytest.mark.asyncio
async def test_list_tests_glob_pattern_matches_cached_tests() -> None:
    """Integration-level repro of the venezia-fpga incident: list_tests()
    with a glob pattern must return matching tests, not silently empty."""
    from xcelium_mcp.tools.sim_lifecycle import register

    mock_mcp = _MockMCP()
    mock_bridges = MagicMock()
    config = {
        "test_discovery": {
            "cached_tests": ["VENEZIA_TOP015_i2c_8bit_offset_test", "VENEZIA_TOP016_test"],
            "cached_test_files": {},
            "cached_dependency_files": {},
            "tb_type": "uvm",
            "schema_version": 2,
        }
    }

    with (
        patch("xcelium_mcp.tools.sim_lifecycle.resolve_sim_dir", new_callable=AsyncMock,
              return_value="/sim"),
        patch("xcelium_mcp.tools.sim_lifecycle.load_sim_config", new_callable=AsyncMock,
              return_value=config),
    ):
        register(mock_mcp, mock_bridges)
        result = await mock_mcp.tools["list_tests"](pattern="*TOP01*")

    assert "VENEZIA_TOP015_i2c_8bit_offset_test" in result
    assert "VENEZIA_TOP016_test" in result
    assert "No tests found" not in result
