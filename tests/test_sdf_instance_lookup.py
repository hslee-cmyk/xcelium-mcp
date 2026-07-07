"""Tests for _analyze_sdf_annotate's instance-module file lookup (F-153).

F-153: the per-instance `grep -rl 'module ...'` lookup in Step 3b ran
sequentially (N+1 shell_run calls for N instances). Instance lookups are
independent, so this now runs concurrently via asyncio.gather. The main
correctness risk is order/mapping: search_files must end up in the same
order as the sequential version, and each instance's grep result must not
leak into a sibling instance's slot.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from xcelium_mcp.discovery import _analyze_sdf_annotate


@pytest.mark.asyncio
async def test_instance_lookups_preserve_order_and_mapping() -> None:
    """3 instances, only 2 resolve to a file; search_files/sdf_source must
    reflect the correct (ordered) mapping even though lookups run concurrently."""

    async def fake_shell(cmd: str, **kwargs) -> str:
        if "module\\s\\+tb_top\\b" in cmd:
            return "/sim/tb_top.sv"
        if cmd.startswith("cat "):
            return "module tb_top;\n  mod_a inst_a (\n  mod_b inst_b (\n  mod_c inst_c (\nendmodule\n"
        if "`include" in cmd:
            return ""  # no `include lines
        if "^\\s*(\\w+)\\s+\\w+\\s*\\(" in cmd:
            return "mod_a inst_a (\nmod_b inst_b (\nmod_c inst_c (\n"
        if "module\\s\\+mod_a\\b" in cmd:
            return "/sim/mod_a.sv"
        if "module\\s\\+mod_b\\b" in cmd:
            return "/sim/mod_b.sv"
        if "module\\s\\+mod_c\\b" in cmd:
            return ""  # mod_c's defining file not found
        if cmd.startswith("grep -n -B10 -A2"):
            # $sdf_annotate found in the second collected file (mod_b)
            assert "/sim/mod_a.sv" in cmd
            assert "/sim/mod_b.sv" in cmd
            assert "/sim/mod_c.sv" not in cmd  # mod_c never resolved to a file
            return '/sim/mod_b.sv:5:    $sdf_annotate ("d.sdf", top);'
        return ""

    with patch("xcelium_mcp.discovery.shell_run", side_effect=fake_shell):
        result = await _analyze_sdf_annotate("/sim", runner={}, top_module="tb_top")

    assert result["has_sdf_annotate"] is True
    assert result["sdf_source_file"] == "/sim/mod_b.sv"


@pytest.mark.asyncio
async def test_no_instances_resolve_returns_no_sdf_annotate() -> None:
    """If no instance grep resolves to a file, search proceeds with includes
    only (or reports has_sdf_annotate=False if nothing is found)."""

    async def fake_shell(cmd: str, **kwargs) -> str:
        if "module\\s\\+tb_top\\b" in cmd:
            return "/sim/tb_top.sv"
        if cmd.startswith("cat "):
            return "module tb_top;\n  mod_x inst_x (\nendmodule\n"
        if "include" in cmd:
            return ""
        if "^\\s*(\\w+)\\s+\\w+\\s*\\(" in cmd:
            return "mod_x inst_x (\n"
        if "module\\s\\+mod_x\\b" in cmd:
            return ""  # not found
        return ""

    with patch("xcelium_mcp.discovery.shell_run", side_effect=fake_shell):
        result = await _analyze_sdf_annotate("/sim", runner={}, top_module="tb_top")

    assert result["has_sdf_annotate"] is False
