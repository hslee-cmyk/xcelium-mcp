"""Test name and simulation parameter resolution for xcelium-mcp.

Extracted from batch_runner.py (F-038 structural split).
Contains: resolve_test_name, resolve_sim_params.
"""
from __future__ import annotations

from datetime import datetime

from xcelium_mcp.registry import load_sim_config, save_sim_config
from xcelium_mcp.shell_utils import (
    shell_quote,
    shell_run,
)


def resolve_sim_params(
    runner: dict,
    sim_mode: str = "rtl",
    extra_args: str = "",
    timeout: int = 600,
    dump_depth: str | None = None,
) -> dict:
    """Resolve simulation parameters from registry schema — Single Point of Change.

    All tools (sim_bridge_run, sim_batch_run, sim_regression) call this.
    Schema changes → modify here only → all tools updated.

    Returns:
        {"test_args_format": str, "timeout": int,
         "probe_strategy": str, "extra_args": str, "dump_depth": str}
    """
    # 1. args_format: dict → mode선택, string → 전 mode 동일
    args_raw = runner.get("args_format", "-test {test_name} --")
    if isinstance(args_raw, dict):
        test_args_format = args_raw.get(sim_mode, args_raw.get("rtl", "-test {test_name} --"))
    else:
        test_args_format = args_raw

    # 2. mode_defaults: common + mode merge
    mode_defaults = runner.get("mode_defaults", {})
    common_cfg = mode_defaults.get("common", {})
    mode_cfg = mode_defaults.get(sim_mode, {})
    effective = {**common_cfg, **mode_cfg}

    # 3. extra_args: config + 1회성 합침
    cfg_extra = effective.get("extra_args", "")
    all_extra = f"{cfg_extra} {extra_args}".strip()

    # v4.3: extra_args combo warnings (warn only, never block)
    warnings: list[str] = []
    if extra_args:
        ea_lower = extra_args.lower()
        if sim_mode == "rtl" and any(k in ea_lower for k in ("-max", "-worst", "-best", "-min")):
            warnings.append("WARNING: corner options are typically for gate/ams mode, not rtl")
        if sim_mode == "gate" and "-ams" in ea_lower:
            warnings.append("WARNING: AMS option in gate mode — use sim_mode='ams_gate' instead")
        if not sim_mode and ("-gate" in ea_lower or "-gate post" in ea_lower):
            warnings.append("WARNING: use sim_mode='gate' instead of extra_args for gate mode")

    # v4.3: dump_depth 결정
    if dump_depth is not None:
        effective_dump_depth = dump_depth
    else:
        effective_dump_depth = effective.get("dump_depth", "all")

    return {
        "test_args_format": test_args_format,
        "timeout": effective.get("timeout", timeout),
        "probe_strategy": effective.get("probe_strategy", "all"),
        "extra_args": all_extra,
        "dump_depth": effective_dump_depth,
        "warnings": warnings,
    }


async def resolve_test_name(short_name: str, sim_dir: str = "") -> str:
    """Short name → full test name via cached_tests.

    "TOP015" → "VENEZIA_TOP015_i2c_8bit_offset_test"
    Exact match → return. 1 substring match → return. 0 → error. 2+ → candidates.
    Cache miss → triggers list_tests (mcp_config 경유 캐시 저장).
    """
    # Lazy import to avoid circular dependency (sim_runner → batch_runner → sim_runner)
    from xcelium_mcp.discovery import resolve_sim_dir
    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = sim_dir  # fallback: use as-is
    cfg = await load_sim_config(resolved_dir) if resolved_dir else None
    cached = cfg.get("test_discovery", {}).get("cached_tests", []) if cfg else []

    if not cached:
        # Cache miss — run test_discovery.command + cache via mcp_config
        # TRUST BOUNDARY: test_discovery.command comes from .mcp_sim_config.json
        # which is a user-controlled config file (written by sim_discover or
        # mcp_config set). The "test_discovery.command" key is in _PROTECTED_KEYS
        # (registry.py), so it cannot be overwritten via mcp_config set — only
        # sim_discover can populate it. We treat this as trusted user input.
        if cfg:
            discovery = cfg.get("test_discovery", {})
            cmd = discovery.get("command", "")
            if cmd:
                r = await shell_run(f"cd {shell_quote(resolved_dir)} && {cmd}", timeout=30)
                cached = [t.strip() for t in r.strip().splitlines() if t.strip()]
                if cached:
                    # Cache via config_action (write centralization)
                    cfg.setdefault("test_discovery", {})["cached_tests"] = cached
                    cfg["test_discovery"]["cached_at"] = datetime.now().isoformat()
                    await save_sim_config(resolved_dir, cfg)

    if not cached:
        return short_name  # No cache, no command → pass through

    # Exact match
    if short_name in cached:
        return short_name

    # Substring match
    matches = [t for t in cached if short_name in t]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) == 0:
        raise ValueError(f"No test matching '{short_name}'. Run list_tests() to see available.")
    else:
        raise ValueError(
            f"Multiple tests match '{short_name}':\n"
            + "\n".join(f"  {m}" for m in matches)
            + "\nSpecify more precisely."
        )
