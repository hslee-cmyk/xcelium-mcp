"""Registry and config management for xcelium-mcp."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

_REGISTRY_PATH = Path.home() / ".xcelium_mcp" / "mcp_registry.json"

_MISSING = object()


def load_registry() -> dict:
    """Load mcp_registry.json. Returns empty structure if not found or corrupt."""
    if _REGISTRY_PATH.exists():
        try:
            return json.loads(_REGISTRY_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"version": 1, "projects": {}}


def save_registry(registry: dict) -> None:
    """Save mcp_registry.json, creating parent directory as needed."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2))


async def load_sim_config(sim_dir: str) -> dict | None:
    """Load .mcp_sim_config.json from sim_dir. Returns None if not found."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


async def save_sim_config(sim_dir: str, config: dict) -> None:
    """Save .mcp_sim_config.json to sim_dir."""
    path = Path(sim_dir) / ".mcp_sim_config.json"
    path.write_text(json.dumps(config, indent=2))


def _write_json(path, data: dict) -> None:
    """Write JSON file. Works with both Path and str."""
    Path(str(path)).write_text(json.dumps(data, indent=2))


async def _update_registry_from_config(sim_dir: str, tb_type: str, config: dict) -> None:
    """Register sim environment in mcp_registry.json.

    This is the ONLY function that writes to mcp_registry.json
    (besides mcp_config tool). Replaces v3's _update_registry_env().
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=sim_dir,
    )
    stdout, _ = await proc.communicate()
    project_root = stdout.decode().strip() if proc.returncode == 0 else str(Path.home())

    registry = load_registry()
    projects = registry.setdefault("projects", {})
    project = projects.setdefault(project_root, {"environments": {}})
    envs = project.setdefault("environments", {})

    envs[sim_dir] = {
        "tb_type": tb_type,
        "is_default": len(envs) == 0 or envs.get(sim_dir, {}).get("is_default", False),
        "config_version": config.get("version", 2),
        "bridge_port": config.get("bridge", {}).get("port", 9876),
    }

    save_registry(registry)


# ---------------------------------------------------------------------------
# Dot-notation helpers for config_action
# ---------------------------------------------------------------------------


def _dot_get(data: dict, key: str):
    """Traverse dict by dot-separated key. Returns _MISSING if not found."""
    parts = key.split(".")
    cur = data
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return _MISSING
    return cur


def _dot_set(data: dict, key: str, value) -> None:
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


def _parse_json_value(value: str):
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
    # Lazy import to avoid circular dependency (sim_runner imports from registry)
    from xcelium_mcp.sim_runner import get_default_sim_dir

    # Load target file
    if file == "registry":
        data = load_registry()
        path = _REGISTRY_PATH
    elif file == "checkpoint":
        sim_dir = await get_default_sim_dir()
        if not sim_dir:
            raise RuntimeError("No default sim_dir. Run sim_discover first.")
        path = Path(sim_dir) / "checkpoints" / "manifest.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}
    else:
        sim_dir = await get_default_sim_dir()
        if not sim_dir:
            raise RuntimeError("No default sim_dir. Run sim_discover first.")
        cfg = await load_sim_config(sim_dir)
        if cfg is None:
            raise RuntimeError(f"No .mcp_sim_config.json in {sim_dir}. Run sim_discover first.")
        data = cfg
        path = Path(sim_dir) / ".mcp_sim_config.json"

    if action == "show":
        return json.dumps(data, indent=2)

    if action == "get":
        val = _dot_get(data, key)
        if val is _MISSING:
            return f"Key '{key}' not found"
        return json.dumps(val, indent=2) if isinstance(val, (dict, list)) else str(val)

    if action == "set":
        parsed = _parse_json_value(value)
        _dot_set(data, key, parsed)
        _write_json(path, data)
        return f"Set {key} = {json.dumps(parsed)}"

    if action == "delete":
        if _dot_delete(data, key):
            _write_json(path, data)
            return f"Deleted {key}"
        return f"Key '{key}' not found"

    return f"Unknown action: {action}"
