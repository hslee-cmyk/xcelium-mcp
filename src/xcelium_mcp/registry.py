"""Registry and config management for xcelium-mcp."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from xcelium_mcp import checkpoint_manager as _checkpoint_manager
from xcelium_mcp.tcl_bridge import DEFAULT_BRIDGE_PORT

_REGISTRY_PATH = Path.home() / ".xcelium_mcp" / "mcp_registry.json"

_MISSING = object()

_PROTECTED_KEYS = {
    "runner.script", "runner.login_shell", "runner.env_shell",
    "runner.exec_cmd_override", "runner.regression_exec_cmd_override",
    "eda_tools.simvisdbutil", "eda_tools.xmsim", "eda_tools.xrun",
    "external_tools.gs", "external_tools.convert", "external_tools.magick",
    "test_discovery.command", "test_discovery.tb_type",
}


def _load_registry_sync() -> dict:
    """Load mcp_registry.json synchronously. Returns empty structure if not found or corrupt."""
    if _REGISTRY_PATH.exists():
        try:
            return json.loads(_REGISTRY_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"version": 1, "projects": {}}


def load_registry() -> dict:
    """Load mcp_registry.json. Returns empty structure if not found or corrupt."""
    return _load_registry_sync()


def _save_registry_sync(registry: dict) -> None:
    """Save mcp_registry.json synchronously, creating parent directory as needed."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


def save_registry(registry: dict) -> None:
    """Save mcp_registry.json, creating parent directory as needed."""
    _save_registry_sync(registry)


async def get_default_sim_dir() -> str:
    """Return the default simulation directory from mcp_registry.json."""
    registry = load_registry()
    projects = registry.get("projects", {})
    for proj_key, proj in projects.items():
        for env_key, env in proj.get("environments", {}).items():
            if env.get("is_default"):
                return env_key
    return ""


async def resolve_sim_dir(sim_dir: str = "") -> str:
    """Resolve sim_dir: use provided value or fall back to registry default.

    Raises ValueError if no sim_dir available.
    """
    resolved = sim_dir if sim_dir else await get_default_sim_dir()
    if not resolved:
        raise ValueError("No sim_dir. Run sim_discover first.")
    return resolved


_MAX_CONFIG_CACHE = 8
_config_cache: dict[str, tuple[float, dict]] = {}  # sim_dir → (mtime, config)


async def load_sim_config(sim_dir: str, *, force: bool = False) -> dict | None:
    """Load .mcp_sim_config.json from sim_dir. Cached by file mtime.

    Args:
        sim_dir: Simulation directory path.
        force: Bypass cache (e.g. after sim_discover).
    """
    path = Path(sim_dir) / ".mcp_sim_config.json"
    if not await asyncio.to_thread(path.exists):
        return None
    stat = await asyncio.to_thread(path.stat)
    mtime = stat.st_mtime
    if not force and sim_dir in _config_cache:
        cached_mtime, cfg = _config_cache[sim_dir]
        if mtime == cached_mtime:
            return cfg
    try:
        raw = await asyncio.to_thread(path.read_text)
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # LRU eviction: remove oldest entry when cache is full
    if len(_config_cache) >= _MAX_CONFIG_CACHE and sim_dir not in _config_cache:
        oldest = next(iter(_config_cache))
        del _config_cache[oldest]
    _config_cache[sim_dir] = (mtime, config)
    return config


async def save_sim_config(sim_dir: str, config: dict) -> None:
    """Save .mcp_sim_config.json to sim_dir. Invalidates cache."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    text = json.dumps(config, indent=2)
    await asyncio.to_thread(path.write_text, text)
    _config_cache.pop(sim_dir, None)


def reset_caches() -> None:
    """Clear the module-level _config_cache. F-162: test isolation seam —
    tests exercising multiple sim_dirs/configs share this process-global
    cache; call this (e.g. in a pytest fixture teardown) to avoid one test's
    cached config leaking into another's assertions."""
    _config_cache.clear()




def _write_json_sync(path, data: dict) -> None:
    """Write JSON file synchronously. Works with both Path and str."""
    Path(str(path)).write_text(json.dumps(data, indent=2))


async def _resolve_project_root(sim_dir: str) -> str:
    """Resolve the git project root for sim_dir (falls back to $HOME if not a git repo).

    Shared by _update_registry_from_config/update_bridge_port/get_bridge_port so all
    three agree on the same project_root key for a given sim_dir.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=sim_dir,
    )
    stdout, _ = await proc.communicate()
    raw_root = stdout.decode().strip() if proc.returncode == 0 else str(Path.home())
    return str(Path(raw_root).resolve())


async def _update_registry_from_config(sim_dir: str, tb_type: str, config: dict) -> None:
    """Register sim environment in mcp_registry.json.

    This is the ONLY function that writes to mcp_registry.json
    (besides mcp_config tool). Replaces v3's _update_registry_env().
    """
    project_root = await _resolve_project_root(sim_dir)
    sim_dir = str(Path(sim_dir).resolve())

    registry = await asyncio.to_thread(_load_registry_sync)
    projects = registry.setdefault("projects", {})
    project = projects.setdefault(project_root, {"environments": {}})
    envs = project.setdefault("environments", {})

    envs[sim_dir] = {
        "tb_type": tb_type,
        "is_default": len(envs) == 0 or envs.get(sim_dir, {}).get("is_default", False),
        "config_version": config.get("version", 2),
        "bridge_port": config.get("bridge", {}).get("port", DEFAULT_BRIDGE_PORT),
    }

    await asyncio.to_thread(_save_registry_sync, registry)


async def update_bridge_port(sim_dir: str, port: int) -> None:
    """Write-back the actual runtime bridge port for sim_dir into mcp_registry.json.

    F-C (attach 모호성 해소, design.md §4.1): the "bridge_port" field written by
    _update_registry_from_config() is only the *configured* value from
    .mcp_sim_config.json at sim_discover time — mcp_bridge.tcl's P1-2 auto-range
    may bind a different port at runtime. Call this right after a successful
    connect so later connect_simulator(sim_dir=...) calls can look up the real port
    instead of guessing via bridge_ready_* glob scans.
    """
    project_root = await _resolve_project_root(sim_dir)
    resolved_sim_dir = str(Path(sim_dir).resolve())

    registry = await asyncio.to_thread(_load_registry_sync)
    projects = registry.setdefault("projects", {})
    project = projects.setdefault(project_root, {"environments": {}})
    envs = project.setdefault("environments", {})
    env = envs.setdefault(resolved_sim_dir, {})
    env["bridge_port"] = port

    await asyncio.to_thread(_save_registry_sync, registry)


async def get_bridge_port(sim_dir: str) -> int | None:
    """Look up the last known bridge port for sim_dir from mcp_registry.json.

    Returns None if sim_dir has no registry entry (e.g. sim_discover/sim_bridge_run
    never ran for it) rather than falling back to DEFAULT_BRIDGE_PORT — callers
    decide whether to fall back to scan_ready_files() on a miss.
    """
    project_root = await _resolve_project_root(sim_dir)
    resolved_sim_dir = str(Path(sim_dir).resolve())

    registry = await asyncio.to_thread(_load_registry_sync)
    env = registry.get("projects", {}).get(project_root, {}).get("environments", {}).get(resolved_sim_dir)
    if env is None:
        return None
    return env.get("bridge_port")


# ---------------------------------------------------------------------------
# Dot-notation helpers for config_action
# ---------------------------------------------------------------------------


def _dot_get(data: dict, key: str) -> Any:
    """Traverse dict by dot-separated key. Returns _MISSING if not found."""
    parts = key.split(".")
    cur = data
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return _MISSING
    return cur


def _dot_set(data: dict, key: str, value: Any) -> None:
    """Set value at dot-separated key, creating intermediate dicts as needed."""
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _dot_delete(data: dict, key: str) -> bool:
    """Delete key at dot-separated path. Returns True if deleted."""
    parts = key.split(".")
    cur = data
    for p in parts[:-1]:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False
    if parts[-1] in cur:
        del cur[parts[-1]]
        return True
    return False


def _parse_json_value(value: str) -> int | float | bool | str:
    """Parse value string to appropriate Python type.

    "9876" -> 9876 (int)
    "true"/"false" -> True/False (bool)
    "3.14" -> 3.14 (float)
    Everything else -> str
    """
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


async def config_action(action: str, file: str, key: str, value: str) -> str:
    """Execute mcp_config action."""
    # Load target file. Each branch also defines _write(d), an async closure
    # that persists the (possibly mutated) data dict through the correct
    # owner for that file.
    if file == "registry":
        data = await asyncio.to_thread(_load_registry_sync)

        async def _write(d: dict) -> None:
            await asyncio.to_thread(_write_json_sync, _REGISTRY_PATH, d)
    elif file == "checkpoint":
        try:
            sim_dir = await resolve_sim_dir()
        except ValueError as e:
            raise RuntimeError(str(e))
        # F-159: delegate to checkpoint_manager's read/write so the manifest
        # schema (compile_hash, checkpoints, tb_analysis_cache) has a single
        # owner — this generic dot-path editor no longer reads/writes the
        # file directly, only via checkpoint_manager's own I/O functions.
        data = await asyncio.to_thread(_checkpoint_manager._read_manifest, sim_dir)

        async def _write(d: dict) -> None:
            await asyncio.to_thread(_checkpoint_manager._write_manifest, sim_dir, d)
    else:
        try:
            sim_dir = await resolve_sim_dir()
        except ValueError as e:
            raise RuntimeError(str(e))
        cfg = await load_sim_config(sim_dir)
        if cfg is None:
            raise RuntimeError(f"No .mcp_sim_config.json in {sim_dir}. Run sim_discover first.")
        data = cfg

        # F-162: write through save_sim_config() (not a raw _write_json_sync)
        # so cache invalidation is explicit (_config_cache.pop()) rather than
        # depending on the next read's mtime differing from the cached mtime
        # — a same-second write/read pair could otherwise see a stale cache hit.
        async def _write(d: dict) -> None:
            await save_sim_config(sim_dir, d)

    if action == "show":
        return json.dumps(data, indent=2)

    if action == "get":
        val = _dot_get(data, key)
        if val is _MISSING:
            return f"Key '{key}' not found"
        return json.dumps(val, indent=2) if isinstance(val, (dict, list)) else str(val)

    if action == "set":
        # Security: block overwriting critical keys that could lead to command injection
        if key in _PROTECTED_KEYS:
            return f"ERROR: Key '{key}' is protected. Re-run sim_discover to update it."

        parsed = _parse_json_value(value)
        _dot_set(data, key, parsed)
        await _write(data)
        return f"Set {key} = {json.dumps(parsed)}"

    if action == "delete":
        if _dot_delete(data, key):
            await _write(data)
            return f"Deleted {key}"
        return f"Key '{key}' not found"

    return f"Unknown action: {action}"
