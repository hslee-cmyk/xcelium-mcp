"""Tests for deposit_signal / _DEPOSIT_VALUE_RE (F-147).

F-147: _DEPOSIT_VALUE_RE only allowed digital Verilog literals (1'b1, 8'hFF),
rejecting real/wreal (AMS analog) values like "3.3" before they ever reached
the Tcl bridge. This is the write-path counterpart to F-144's bisect
(read-path) decimal bug. No test coverage existed for deposit_signal at all
before this change.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xcelium_mcp.tools.signal_inspection import _DEPOSIT_VALUE_RE


class TestDepositValueRegex:
    """Regex-level tests — the actual injection guard for deposit_signal's value param."""

    # --- existing digital literals must keep working ---

    def test_binary_literal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("1'b1") is not None

    def test_hex_literal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("8'hFF") is not None

    def test_decimal_literal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("32'd100") is not None

    def test_tristate_literal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("16'bxxxx") is not None

    def test_plain_digits(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("42") is not None

    # --- F-147: real/wreal (AMS analog) decimal values must now be accepted ---

    def test_plain_decimal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("3.3") is not None

    def test_negative_decimal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("-1.5") is not None

    def test_scientific_notation_negative_exponent(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("1.2e-05") is not None

    def test_scientific_notation_positive_exponent(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("1.2e+05") is not None

    def test_scientific_notation_uppercase_e(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("1.2E-05") is not None

    def test_zero_decimal(self):
        assert _DEPOSIT_VALUE_RE.fullmatch("0.0") is not None

    # --- injection payloads must still be rejected after the charset expansion ---

    @pytest.mark.parametrize("payload", [
        "1'b1; exec rm",
        "3.3; exec rm",
        "3.3 [exec rm]",
        "3.3\nexec rm",
        "3.3$foo",
        "3.3{foo}",
        "3.3\"",
        "3.3'; rm -rf /",
        "3.3\\",
        "3.3 ",
    ])
    def test_injection_payloads_rejected(self, payload):
        assert _DEPOSIT_VALUE_RE.fullmatch(payload) is None


class _FakeBridge:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, cmd: str, timeout: float = 30) -> str:
        self.calls.append(cmd)
        return "readback_ok"


@pytest.mark.asyncio
async def test_deposit_signal_accepts_decimal_value() -> None:
    """End-to-end: deposit_signal(value='3.3') on a real/wreal net must pass validation."""
    from mcp.server.fastmcp import FastMCP
    from xcelium_mcp.tools.signal_inspection import register

    fake_bridge = _FakeBridge()
    fake_bridges = MagicMock()
    fake_bridges.xmsim = fake_bridge

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["deposit_signal"]
        result = await tool.fn(signal="top.hw.v_out", value="3.3")

    assert "Deposited 3.3" in result
    assert any("__DEPOSIT_AND_VERIFY__" in c and "3.3" in c for c in fake_bridge.calls)


@pytest.mark.asyncio
async def test_deposit_signal_rejects_injection_payload() -> None:
    """value with a Tcl metacharacter must be rejected before reaching the bridge."""
    from mcp.server.fastmcp import FastMCP
    from xcelium_mcp.tools.signal_inspection import register

    fake_bridge = _FakeBridge()
    fake_bridges = MagicMock()
    fake_bridges.xmsim = fake_bridge

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["deposit_signal"]
        result = await tool.fn(signal="top.hw.v_out", value="3.3; exec rm")

    assert "ERROR" in result
    assert not fake_bridge.calls, "bridge must not be called when value validation fails"


@pytest.mark.asyncio
async def test_deposit_signal_still_accepts_digital_literal() -> None:
    """Regression: existing digital-literal deposits must keep working."""
    from mcp.server.fastmcp import FastMCP
    from xcelium_mcp.tools.signal_inspection import register

    fake_bridge = _FakeBridge()
    fake_bridges = MagicMock()
    fake_bridges.xmsim = fake_bridge

    with patch("xcelium_mcp.tools.signal_inspection.sanitize_signal_name", side_effect=lambda s: s):
        mcp = FastMCP("test")
        register(mcp, fake_bridges)
        tool = mcp._tool_manager._tools["deposit_signal"]
        result = await tool.fn(signal="top.hw.r_en", value="1'b1")

    assert "Deposited 1'b1" in result
