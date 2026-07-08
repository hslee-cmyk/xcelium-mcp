"""Test name and simulation parameter resolution for xcelium-mcp.

Extracted from batch_runner.py (F-038 structural split).
Contains: resolve_test_name, resolve_sim_params.

F-178: parse_test_discovery_output moved to test_discovery_scan.py — it was a
dependency-free leaf function misplaced in this (orchestration) module, which
forced schema_migration.py into a circular-import workaround.
"""
from __future__ import annotations

from xcelium_mcp.registry import load_sim_config, resolve_sim_dir
from xcelium_mcp.schema_migration import ensure_and_persist_test_discovery


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


def _match_short_name(short_name: str, cached: list[str]) -> str:
    """Match short_name against an already-resolved cached_tests list.

    "TOP015" → "VENEZIA_TOP015_i2c_8bit_offset_test"
    Exact match → return. 1 substring match → return. 0 → error. 2+ → candidates.
    No cache at all → pass short_name through unresolved (existing contract).

    F-181: split out of resolve_test_name() so a batch caller (resolve_test_names_batch)
    can migrate/load the config ONCE and then match each name locally, instead of every
    name re-running the full resolve (and, if a migration is pending, the full
    migration) independently.
    """
    if not cached:
        return short_name

    if short_name in cached:
        return short_name

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


async def _load_cached_tests(sim_dir: str) -> list[str]:
    """Resolve sim_dir, load config, and migrate/persist test_discovery ONCE.

    Shared by resolve_test_name() and resolve_test_names_batch() — the actual
    schema migration + cached_tests lookup, done exactly once per call site
    invocation regardless of how many short names need matching afterward.
    """
    try:
        resolved_dir = await resolve_sim_dir(sim_dir)
    except ValueError:
        resolved_dir = sim_dir  # fallback: use as-is
    cfg = await load_sim_config(resolved_dir) if resolved_dir else None
    if not cfg:
        return []

    # F-175 migration gap fix: schema_version-driven migration instead of
    # an "is cached_tests empty" check — a project whose cached_tests was
    # already populated before F-175 existed (tb_type/cached_test_files
    # missing) used to fall through this check forever, since this is the
    # actual hot-path entry point sim_batch_run/sim_regression call
    # before build_tb_provenance(). See schema_migration.py / the
    # xcelium-mcp-f175-provenance-migration-gap plan+design docs.
    #
    # TRUST BOUNDARY: test_discovery.command comes from .mcp_sim_config.json
    # which is a user-controlled config file (written by sim_discover or
    # mcp_config set). The "test_discovery.command" key is in _PROTECTED_KEYS
    # (registry.py), so it cannot be overwritten via mcp_config set — only
    # sim_discover can populate it. We treat this as trusted user input.
    return await ensure_and_persist_test_discovery(resolved_dir, cfg)


async def resolve_test_name(short_name: str, sim_dir: str = "") -> str:
    """Short name → full test name via cached_tests (single-test resolution,
    e.g. sim_batch_run). See resolve_test_names_batch() for the N-name path."""
    cached = await _load_cached_tests(sim_dir)
    return _match_short_name(short_name, cached)


async def resolve_test_names_batch(short_names: list[str], sim_dir: str = "") -> list[str]:
    """Resolve N short names against ONE migration/config load (F-181).

    sim_regression() used to asyncio.gather() resolve_test_name() per test —
    when a schema migration was actually pending, all N concurrent calls
    independently ran the full migration (each doing its own O(N) dependency
    scan), an O(N^2) thundering herd. Here the migrate-and-load happens once,
    then each name is matched locally (pure, no I/O) against the same
    cached_tests list.
    """
    cached = await _load_cached_tests(sim_dir)
    return [_match_short_name(name, cached) for name in short_names]
