"""sim_runner.py — Backward-compatibility re-export stub.

All functions have moved to their actual modules:
- shell_utils: get_user_tmp_dir, _parse_shm_path, _parse_time_ns, ssh_run, sq, ...
- discovery: run_full_discovery, resolve_sim_dir, get_default_sim_dir, ...
- bridge_lifecycle: start_bridge_simulation, _start_bridge, ...

This file only re-exports names for backward compatibility.
"""
from __future__ import annotations

# ===================================================================
# Re-exports from batch_runner
# ===================================================================
from xcelium_mcp.batch_runner import (  # noqa: F401
    ExecInfo,
    _poll_batch_log,
    _resolve_exec_cmd,
    _run_batch_regression,
    _run_batch_single,
    resolve_sim_params,
    resolve_test_name,
    validate_extra_args,
)

# ===================================================================
# Re-exports from bridge_lifecycle
# ===================================================================
from xcelium_mcp.bridge_lifecycle import (  # noqa: F401
    _SIMVISIONRC_MARKER,
    _patch_legacy_run_script,
    _start_bridge,
    _update_simvisionrc,
    run_with_dump_window,
    start_bridge_simulation,
)

# ===================================================================
# Re-exports from discovery
# ===================================================================
from xcelium_mcp.discovery import (  # noqa: F401
    _analyze_sdf_annotate,
    _extract_top_module_from_content,
    _extract_top_module_from_script,
    _format_discovery_result,
    _parse_ifdef_around_sdf,
    get_default_sim_dir,
    resolve_sim_dir,
    run_full_discovery,
)

# ===================================================================
# Re-exports from env_detection
# ===================================================================
from xcelium_mcp.env_detection import (  # noqa: F401
    _analyze_tb_type,
    _ask_user_runner,
    _auto_detect_runner,
    _detect_bridge_port,
    _detect_bridge_tcl,
    _detect_eda_env,
    _detect_env_shell,
    _detect_run_dir,
    _detect_setup_tcls,
    _detect_shell_and_env,
    _detect_vnc_display,
    _discover_sim_dir,
    _extract_script_name,
    _load_or_detect_runner,
    _pick_default_mode,
    _resolve_eda_tools,
    _resolve_external_tools,
)

# ===================================================================
# Re-exports from registry
# ===================================================================
from xcelium_mcp.registry import (  # noqa: F401
    _update_registry_from_config,
    config_action,
    load_registry,
    load_sim_config,
    save_registry,
    save_sim_config,
)

# ===================================================================
# Re-exports from shell_utils
# ===================================================================
from xcelium_mcp.shell_utils import (  # noqa: F401
    UserInputRequired,
    _parse_shm_path,
    _parse_time_ns,
    build_redirect,
    get_user_tmp_dir,
    login_shell_cmd,
    ssh_run,
)
from xcelium_mcp.shell_utils import (  # noqa: F401
    shell_quote as sq,
)

# ===================================================================
# Re-exports from tcl_bridge
# ===================================================================
from xcelium_mcp.tcl_bridge import DEFAULT_BRIDGE_PORT  # noqa: F401
