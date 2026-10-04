"""
\u269b\ufe0f AgentKthx \u2014 TTL cache for MCP search results

A stdlib-only, JSON-backed TTL cache for ``mcp search`` results. Lives
at ``~/.agentkthx/mcp_cache.json`` (mode 0o600) so repeated searches
within the TTL window don't re-hit npm/GitHub.

Design
------
* **JSON file, not SQLite** \u2014 cache size is small (a few hundred KB at
  most), and JSON is consistent with how ``mcp.json`` is handled. SQLite
  would be overkill for a key\u2192value\u2192expiry store.
* **Per-entry TTL** \u2014 each entry has its own ``expires_at`` timestamp,
  so a search for "filesystem" and a search for "git" can coexist with
  independent freshness windows.
* **Lazy eviction** \u2014 expired entries are not actively removed; they
  are skipped on read and overwritten on write. A ``clear()`` function
  is provided for explicit cleanup.
* **Concurrency** \u2014 file access is not locked. Two concurrent
  ``mcp search`` calls could race on the write. The worst case is a
  lost cache update (last writer wins), not corruption \u2014 JSON is
  written atomically via ``tmp + rename``.
* **Configurable** \u2014 TTL defaults to 600 seconds (10 minutes) per
  the maintainer's spec. Override with ``AGENTKTHX_MCP_CACHE_TTL``
  env var (seconds). Set to ``0`` to disable caching entirely.

Public API
----------
.. autofunction:: get_cached
.. autofunction:: set_cached
.. autofunction:: clear_cache
.. autofunction:: cache_path
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL = 600  # 10 minutes
DEFAULT_TTL_ENV = "AGENTKTHX_MCP_CACHE_TTL"


def cache_path() -> Path:
    """Return the default cache file path: ``~/.agentkthx/mcp_cache.json``."""
    return Path.home() / ".agentkthx" / "mcp_cache.json"


def _effective_ttl(ttl: int | None) -> int:
    """Resolve the TTL: explicit arg > env var > default."""
    if ttl is not None:
        return max(0, int(ttl))
    env = os.environ.get(DEFAULT_TTL_ENV, "").strip()
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            return DEFAULT_TTL
    return DEFAULT_TTL


def _read_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Read the cache file, returning an empty dict on any error."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache(path: Path, data: dict[str, dict[str, Any]]) -> None:
    """Write the cache file atomically (tmp + rename). Mode 0o600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def get_cached(
    key: str, *, ttl: int | None = None, path: str | os.PathLike | None = None
) -> tuple[bool, Any]:
    """Look up ``key`` in the cache.

    Args:
        key: Cache key (e.g. ``"npm_search:filesystem:25"``).
        ttl: Override the TTL check. If None, uses
            :data:`DEFAULT_TTL` (or ``AGENTKTHX_MCP_CACHE_TTL`` env var).
            Pass ``0`` to always treat the entry as expired.
        path: Override the cache file path. Defaults to
            :func:`cache_path`.

    Returns:
        ``(hit, value)`` \u2014 ``(True, value)`` if the entry exists and
        is fresh; ``(False, None)`` if missing or expired. Expired
        entries are NOT evicted here (lazy eviction); they are
        overwritten on the next ``set_cached`` for the same key.
    """
    if _effective_ttl(ttl) == 0:
        return False, None

    cache_file = Path(path) if path else cache_path()
    data = _read_cache(cache_file)
    entry = data.get(key)
    if entry is None:
        return False, None
    if not isinstance(entry, dict):
        return False, None
    expires_at = entry.get("expires_at", 0)
    if not isinstance(expires_at, (int, float)):
        return False, None
    if time.time() > expires_at:
        return False, None
    return True, entry.get("value")


def set_cached(
    key: str,
    value: Any,
    *,
    ttl: int | None = None,
    path: str | os.PathLike | None = None,
) -> None:
    """Set ``key`` in the cache with a TTL.

    Args:
        key: Cache key.
        value: JSON-serializable value.
        ttl: TTL in seconds. If None, uses :data:`DEFAULT_TTL` (or
            ``AGENTKTHX_MCP_CACHE_TTL`` env var). Pass ``0`` to skip
            caching entirely (the entry is removed if it exists).
        path: Override the cache file path.
    """
    effective_ttl = _effective_ttl(ttl)
    cache_file = Path(path) if path else cache_path()
    data = _read_cache(cache_file)

    if effective_ttl == 0:
        # TTL of 0 means "don't cache" \u2014 remove the entry if it exists
        if key in data:
            del data[key]
            _write_cache(cache_file, data)
        return

    data[key] = {
        "value": value,
        "expires_at": time.time() + effective_ttl,
    }
    _write_cache(cache_file, data)


def clear_cache(path: str | os.PathLike | None = None) -> int:
    """Clear all cache entries. Returns the number of entries removed."""
    cache_file = Path(path) if path else cache_path()
    data = _read_cache(cache_file)
    count = len(data)
    if count:
        _write_cache(cache_file, {})
    return count


__all__ = [
    "DEFAULT_TTL",
    "cache_path",
    "get_cached",
    "set_cached",
    "clear_cache",
]
