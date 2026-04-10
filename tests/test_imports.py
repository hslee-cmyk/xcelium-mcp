"""Import smoke tests.

These guarantee every module in the xcelium_mcp package (and every MCP tool
submodule) imports cleanly. They auto-prevent regressions like the one in
commit 7cffcf8 → 81e7025 where `tools/batch.py` was broken by an accidental
re-export deletion in `sim_runner.py`, yet pytest still passed because no
existing test ever touched `tools.batch`.

Rule: if a module is in `src/xcelium_mcp/`, there should be an `importlib`
line in this file for it. Catching a broken `import` here is the cheapest
possible unit test — but historically the one we did not have.
"""
from __future__ import annotations

import importlib

import pytest

PACKAGE_MODULES = [
    "xcelium_mcp",
    "xcelium_mcp.batch_polling",
    "xcelium_mcp.batch_runner",
    "xcelium_mcp.bridge_manager",
    "xcelium_mcp.checkpoint_manager",
    "xcelium_mcp.csv_cache",
    "xcelium_mcp.debug_tools",
    "xcelium_mcp.runner_detection",
    "xcelium_mcp.sim_env_detection",
    "xcelium_mcp.registry",
    "xcelium_mcp.screenshot",
    "xcelium_mcp.server",
    "xcelium_mcp.shell_utils",
    "xcelium_mcp.simvision_ops",
    "xcelium_mcp.discovery",
    "xcelium_mcp.bridge_lifecycle",
    "xcelium_mcp.tcl_bridge",
    "xcelium_mcp.tcl_preprocessing",
    "xcelium_mcp.test_resolution",
]

TOOL_MODULES = [
    "xcelium_mcp.tools",
    "xcelium_mcp.tools.batch",
    "xcelium_mcp.tools.checkpoint",
    "xcelium_mcp.tools.debug",
    "xcelium_mcp.tools.signal_inspection",
    "xcelium_mcp.tools.sim_lifecycle",
    "xcelium_mcp.tools.simvision",
    "xcelium_mcp.tools.waveform",
]


@pytest.mark.parametrize("module_name", PACKAGE_MODULES + TOOL_MODULES)
def test_module_imports(module_name: str) -> None:
    """Every module must import without error."""
    importlib.import_module(module_name)


def test_tool_modules_expose_register() -> None:
    """Every MCP tool submodule must expose a callable `register`."""
    for name in TOOL_MODULES:
        if name == "xcelium_mcp.tools":
            continue  # package init, no register
        mod = importlib.import_module(name)
        register = getattr(mod, "register", None)
        assert callable(register), f"{name}.register is not callable"


def test_server_main_is_callable() -> None:
    """`xcelium_mcp.server.main` must be importable and callable."""
    from xcelium_mcp.server import main
    assert callable(main)
