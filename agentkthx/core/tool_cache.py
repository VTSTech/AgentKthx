"""
⚛️ AgentKthx — Tool Support Cache

Persistent cache for model tool support detection results.
Eliminates the need to re-test models on every run.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .types import ThinkingSupport, ToolSupportLevel


def get_cache_dir() -> Path:
    """Get the cache directory for AgentKthx."""
    if os.name == "nt":
        # Windows: %LOCALAPPDATA%\agentkthx\cache
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        cache_dir = Path(base) / "agentkthx" / "cache"
    else:
        # Unix: ~/.cache/agentkthx
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        cache_dir = Path(base) / "agentkthx"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_file() -> Path:
    """Get the tool support cache file path."""
    return get_cache_dir() / "tool_support.json"


def load_tool_cache() -> dict:
    """
    Load cached tool support results.

    Returns:
        Dict mapping model names to their cached tool support info:
        {
            "qwen2.5:0.5b": {
                "support": "native",
                "tested_at": 1234567890.0,
                "family": "qwen2"
            },
            ...
        }
    """
    cache_file = get_cache_file()
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                # Corrupted - not a dict
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print("[ToolCache] Warning: Cache file corrupted (not a dict), ignoring")
                return {}
        except json.JSONDecodeError as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"[ToolCache] Warning: Cache file has invalid JSON: {e}")
            try:
                cache_file.unlink()
            except Exception:
                pass
            return {}
        except IOError as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"[ToolCache] Warning: Could not read cache file: {e}")
    return {}


def save_tool_cache(cache: dict) -> None:
    """
    Save tool support results to cache using atomic writes.

    Args:
        cache: Dict mapping model names to their tool support info
    """
    import tempfile

    cache_dir = get_cache_dir()
    cache_file = get_cache_file()

    try:
        # Write to a temp file first, then rename for atomicity
        fd, temp_path = tempfile.mkstemp(
            dir=str(cache_dir), prefix=".tool_support_", suffix=".json.tmp"
        )

        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename (on POSIX systems)
            os.replace(temp_path, str(cache_file))
        except Exception:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    except IOError as e:
        # Log the error but don't fail - cache is optional
        print(f"[ToolCache] Warning: Could not save tool cache: {e}", file=__import__("sys").stderr)


def _cache_key(model: str, api_mode: str = "openre") -> str:
    """
    Build a cache key that namespaces by API mode.

    Different API modes (openre vs openai) can produce different tool
    support results for the same model, so the cache key must include
    the API mode.  The default "openre" is omitted for backward
    compatibility with existing cache entries.
    """
    if api_mode and api_mode != "openre":
        return f"{model}:{api_mode}"
    return model


def get_cached_tool_support(model: str, api_mode: str = "openre") -> Optional[ToolSupportLevel]:
    """
    Get cached tool support level for a model.

    Args:
        model: Model name (e.g., "qwen2.5:0.5b")
        api_mode: API mode used during testing (default: "openre")

    Returns:
        ToolSupportLevel if cached, None if not in cache
    """
    cache = load_tool_cache()
    key = _cache_key(model, api_mode)
    cached = cache.get(key)
    if cached:
        support_str = cached.get("support", "")
        try:
            return ToolSupportLevel(support_str)
        except ValueError:
            pass
    # Fallback: check legacy key without API mode suffix
    # (supports caches written before this change)
    if key != model:
        cached = cache.get(model)
        if cached:
            support_str = cached.get("support", "")
            try:
                return ToolSupportLevel(support_str)
            except ValueError:
                pass
    return None


def cache_tool_support(
    model: str,
    support: ToolSupportLevel,
    family: str = "",
    error: str = "",
    api_mode: str = "openre",
) -> None:
    """
    Cache tool support result for a model.

    R07.19 (follow-up #10): there is now a SINGLE authoritative tool-support
    check per model (the capabilities verdict is API-mode-independent), so
    internal callers no longer namespace by api_mode — the default "openre"
    key is the one canonical entry. The api_mode parameter is retained for
    backward compatibility with third-party callers and old cache shapes;
    reads of legacy "model:openai" entries still work via the fallback in
    get_cached_tool_support().

    Args:
        model: Model name
        support: Detected tool support level
        family: Model family (for reference)
        error: Error message if detection failed
        api_mode: API mode used during testing (default: "openre")
    """
    cache = load_tool_cache()
    key = _cache_key(model, api_mode)
    cache[key] = {
        "support": support.value,
        "tested_at": time.time(),
        "family": family,
    }
    if api_mode:
        cache[key]["api_mode"] = api_mode
    if error:
        cache[key]["error"] = error[:100]
    save_tool_cache(cache)


# ─────────────────────────────────────────────────────────────────────────
# Thinking / reasoning support cache (R07.19 follow-up #11)
#
# Stored in the SAME tool_support.json file under a ``thinking:<model>``
# key prefix, so one file answers both "can this model call tools?" and
# "does this model think?". A separate prefix (instead of a field inside
# the tool entry) keeps the two verdicts independent — re-testing tools
# never clobbers a thinking verdict and vice versa.
# ─────────────────────────────────────────────────────────────────────────


def _thinking_key(model: str) -> str:
    """Cache key for a model's thinking-support verdict."""
    return f"thinking:{model}"


def get_cached_thinking_support(model: str) -> Optional[ThinkingSupport]:
    """
    Get cached thinking/reasoning support level for a model.

    Args:
        model: Model name (e.g., "deepseek-r1:8b")

    Returns:
        ThinkingSupport if cached, None if not in cache
    """
    cache = load_tool_cache()
    cached = cache.get(_thinking_key(model))
    if cached:
        support_str = cached.get("support", "")
        try:
            return ThinkingSupport(support_str)
        except ValueError:
            pass
    return None


def cache_thinking_support(model: str, support: ThinkingSupport, family: str = "") -> None:
    """
    Cache thinking/reasoning support verdict for a model.

    Args:
        model: Model name
        support: Detected ThinkingSupport level
        family: Model family (for reference)
    """
    cache = load_tool_cache()
    cache[_thinking_key(model)] = {
        "support": support.value,
        "tested_at": time.time(),
        "family": family,
    }
    save_tool_cache(cache)
