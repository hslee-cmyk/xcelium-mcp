"""Tests for registry cache invalidation / reset_caches (F-162).

F-162: config_action's project-config ("else") branch used to write via a
raw _write_json_sync(path, d), bypassing save_sim_config()'s explicit
_config_cache.pop(sim_dir). load_sim_config() caches by (mtime, config) —
a write that lands within the same mtime tick as the cached entry would
have been invisible to the next load_sim_config() call (stale read). Now
the write path goes through save_sim_config(), which pops the cache
unconditionally regardless of mtime granularity.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from xcelium_mcp.registry import _config_cache, config_action, load_sim_config, reset_caches


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_caches()
    yield
    reset_caches()


@pytest.mark.asyncio
async def test_config_action_set_invalidates_cache_even_with_stale_cache_entry(tmp_path) -> None:
    """Simulates the exact bug scenario: a stale cache entry exists (as if
    a previous read happened to share the file's post-write mtime). Without
    F-162's fix, config_action's set would leave this stale entry in place
    and the next load_sim_config() would return outdated data."""
    sim_dir = str(tmp_path)
    cfg_path = tmp_path / ".mcp_sim_config.json"
    cfg_path.write_text(json.dumps({"foo": "bar"}))

    # Prime the cache with a stale/fake entry — deliberately using a mtime
    # value the real load below will keep matching, to prove invalidation
    # doesn't depend on mtime changing.
    stat = cfg_path.stat()
    _config_cache[sim_dir] = (stat.st_mtime, {"foo": "STALE_CACHED_VALUE"})

    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        await config_action("set", "config", "foo", "baz")

    # Cache entry for this sim_dir must be gone after the write.
    assert sim_dir not in _config_cache

    # A fresh load must see the new value, not the stale cached one — even
    # though the file's mtime may not have advanced past what was cached.
    cfg = await load_sim_config(sim_dir)
    assert cfg["foo"] == "baz"


@pytest.mark.asyncio
async def test_config_action_delete_invalidates_cache(tmp_path) -> None:
    sim_dir = str(tmp_path)
    cfg_path = tmp_path / ".mcp_sim_config.json"
    cfg_path.write_text(json.dumps({"foo": "bar", "extra": 1}))

    stat = cfg_path.stat()
    _config_cache[sim_dir] = (stat.st_mtime, {"foo": "bar", "extra": 1})

    with patch("xcelium_mcp.registry.resolve_sim_dir", new_callable=AsyncMock, return_value=sim_dir):
        await config_action("delete", "config", "extra", "")

    assert sim_dir not in _config_cache
    cfg = await load_sim_config(sim_dir)
    assert "extra" not in cfg


def test_reset_caches_clears_config_cache() -> None:
    _config_cache["/some/sim/dir"] = (123.0, {"a": 1})
    assert _config_cache  # populated

    reset_caches()

    assert _config_cache == {}
