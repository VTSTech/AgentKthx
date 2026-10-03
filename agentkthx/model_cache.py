"""
⚛️ AgentKthx — Persistent Model Catalog Cache (R07.20)

A single JSON file on disk caches the LIVE model catalog every cloud
backend determines at runtime (ZAI, OpenRouter, OpenAI, Gemini,
HuggingFace, Mistral, Pollinations, OrcaRouter). Purpose: model
discovery used to hit each provider's ``/models``-style endpoint on
every call — and because the CLI is a fresh process per invocation, the
per-process in-memory caches (1 hour, OpenRouter/OpenAI/Gemini/HF/
OrcaRouter only) never survived between runs. ZAI, Mistral and
Pollinations had no cache at all. The persistent cache survives process
restarts, so ``agentkthx models`` + ``/models`` + agent_factory probe
constructs stop hammering the provider APIs.

Design
------
Storage: one JSON file, by default ``~/.cache/agentkthx/model_catalog.json``
(``%LOCALAPPDATA%\\agentkthx\\cache`` on Windows — the same location
``core/tool_cache.py`` uses for the tool-support verdicts). Override the
location with ``AGENTKTHX_MODEL_CACHE`` (full path to the JSON file).

Shape (version 1)::

    {
      "version": 1,
      "backends": {
        "<backend-key>": {
          "updated_at": 1759500000.0,     # epoch seconds of last refresh
          "data": [ ...model entries... ] # shape defined by the backend
        }
      }
    }

TTL: entries older than ``AGENTKTHX_MODEL_CACHE_TTL`` seconds (default
**1800 — 30 minutes**) are stale; callers refresh from the live API and
``store_models()`` writes the fresh list. TTL ``0`` disables caching
(every call is a cache miss).

Persistent pin: any model entry may carry ``"persistent": true``.
Persistent models NEVER expire and are NEVER cleared:

  - they are returned as part of the cached list regardless of freshness;
  - when a refresh drops them (the API stopped listing the model), they
    are re-attached to the stored list;
  - when a refresh keeps them, the flag is carried onto the new entry;
  - ``clear_backend()`` preserves them (``force=True`` removes everything);
  - they survive cache-file rewrites and process restarts like any other
    cached entry.

Set the flag via the CLI (``agentkthx models --persist <model>``) or by
editing the JSON directly (``"persistent": true`` on the model entry).

Seeding: the packaged seed file ``agentkthx/data/model_seed.json``
holds the former hardcoded per-plugin catalogs (R07.20 moved them out of
the Python sources). ``ensure_seeded()`` writes a backend's catalog into
the cache as its INITIAL DEFAULTS — stamped ``updated_at = 0.0`` so the
very next ``list_models()`` still goes live once and replaces the seed
with the real catalog; if the provider is unreachable, the stale seed
serves as the offline fallback. Plugins load their static catalog
metadata (context lengths, pricing, defaults) through
``load_seed_catalog()`` so module-level names like ``ZAI_MODELS`` keep
working against JSON-backed data.

Thread safety: a module lock serializes load-modify-save cycles.
Corruption resilience: an unreadable/invalid file is treated as empty
(the next successful refresh rewrites it) — cache failures never take
down model listing.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "get_cache_path",
    "get_ttl",
    "load_cache",
    "save_cache",
    "get_entry",
    "get_fresh",
    "get_stale",
    "get_cached_models",
    "get_stale_models",
    "store",
    "store_models",
    "ensure_seeded",
    "set_persistent",
    "is_persistent",
    "persistent_names",
    "clear_backend",
    "clear_all",
    "cache_info",
    "load_seed_catalogs",
    "load_seed_catalog",
]

# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

DEFAULT_TTL_SECONDS = 1800
"""Default cache TTL: 30 minutes (override with AGENTKTHX_MODEL_CACHE_TTL)."""

_CACHE_VERSION = 1

_LOCK = threading.RLock()

# ─────────────────────────────────────────────────────────────────────────
# Path + TTL resolution (env-overridable, read per call for testability)
# ─────────────────────────────────────────────────────────────────────────


def get_cache_path() -> Path:
    """Resolve the model-catalog JSON path.

    Priority: ``AGENTKTHX_MODEL_CACHE`` (full file path) > the shared
    AgentKthx cache dir from :func:`agentkthx.core.tool_cache.get_cache_dir`
    (``~/.cache/agentkthx`` on Unix, ``%LOCALAPPDATA%\\agentkthx\\cache``
    on Windows).
    """
    env_path = os.environ.get("AGENTKTHX_MODEL_CACHE", "")
    if env_path.strip():
        return Path(env_path.strip()).expanduser()
    # Lazy import keeps this module importable in isolation (and out of
    # any import cycle): tool_cache pulls in core.types via core/__init__.
    from .core.tool_cache import get_cache_dir

    return get_cache_dir() / "model_catalog.json"


def get_ttl() -> int:
    """Return the cache TTL in seconds (default 1800 = 30 minutes).

    ``AGENTKTHX_MODEL_CACHE_TTL`` overrides; ``0`` disables caching
    (every read is a miss, every ``list_models`` goes live). Negative or
    non-integer values fall back to the default.
    """
    raw = os.environ.get("AGENTKTHX_MODEL_CACHE_TTL", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_TTL_SECONDS
        return value if value >= 0 else DEFAULT_TTL_SECONDS
    return DEFAULT_TTL_SECONDS


# ─────────────────────────────────────────────────────────────────────────
# Low-level load / save
# ─────────────────────────────────────────────────────────────────────────


def _empty_cache() -> dict:
    return {"version": _CACHE_VERSION, "backends": {}}


def load_cache() -> dict:
    """Load the cache file, returning an empty skeleton on any problem.

    Never raises: a missing, unreadable, corrupt, or wrong-shaped file
    is treated as an empty cache (the next successful refresh rewrites
    it). A debug line is printed under ``AGENTKTHX_DEBUG``.
    """
    path = get_cache_path()
    if not path.exists():
        return _empty_cache()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"[ModelCache] Warning: cache file has invalid JSON: {e}")
        return _empty_cache()
    except OSError as e:
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"[ModelCache] Warning: could not read cache file: {e}")
        return _empty_cache()
    if not isinstance(data, dict) or not isinstance(data.get("backends"), dict):
        if os.environ.get("AGENTKTHX_DEBUG"):
            print("[ModelCache] Warning: cache file corrupted (bad shape), ignoring")
        return _empty_cache()
    return data


def save_cache(cache: dict) -> None:
    """Persist the cache atomically (temp file + ``os.replace``).

    Best-effort: I/O failures are reported to stderr but never raised —
    the cache is an optimization, and losing a write must not take down
    model listing.
    """
    path = get_cache_path()
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                dir=str(path.parent), prefix=".model_catalog_", suffix=".json.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, str(path))
            except Exception:
                if os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                raise
        except OSError as e:
            import sys

            print(f"[ModelCache] Warning: could not save model cache: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────
# Entry access
# ─────────────────────────────────────────────────────────────────────────


def get_entry(backend: str) -> dict | None:
    """Return the raw cache entry for ``backend`` (``{"updated_at", "data"}``)
    or ``None`` when absent/invalid."""
    cache = load_cache()
    entry = cache.get("backends", {}).get(backend)
    if not isinstance(entry, dict) or "updated_at" not in entry:
        return None
    return entry


def get_fresh(backend: str, max_age: int | None = None) -> Any | None:
    """Return the cached payload for ``backend`` if it is fresh, else ``None``.

    Fresh means ``age < max_age`` (default: the configured TTL). A TTL of
    ``0`` disables caching — always ``None``.
    """
    ttl = get_ttl() if max_age is None else max_age
    if ttl <= 0:
        return None
    entry = get_entry(backend)
    if entry is None:
        return None
    try:
        updated_at = float(entry.get("updated_at", 0.0))
    except (TypeError, ValueError):
        return None
    if (time.time() - updated_at) >= ttl:
        return None
    return entry.get("data")


def get_stale(backend: str) -> Any | None:
    """Return the cached payload for ``backend`` regardless of age.

    Used as the offline fallback when the live refresh fails — the cache
    may hold a stale live catalog or the seeded defaults. ``None`` when
    the backend has no entry at all.
    """
    entry = get_entry(backend)
    if entry is None:
        return None
    return entry.get("data")


# ─────────────────────────────────────────────────────────────────────────
# Model-list operations (persistent-pin aware)
# ─────────────────────────────────────────────────────────────────────────


def _model_name(model: Any) -> str | None:
    if isinstance(model, dict):
        name = model.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def get_cached_models(backend: str, max_age: int | None = None) -> Optional[list[dict]]:
    """Fresh model list for ``backend`` (includes persistent entries), or None."""
    data = get_fresh(backend, max_age)
    if isinstance(data, list):
        return data
    return None


def get_stale_models(backend: str) -> Optional[list[dict]]:
    """Cached model list for ``backend`` at any age (offline fallback), or None."""
    data = get_stale(backend)
    if isinstance(data, list):
        return data
    return None


def store(backend: str, data: Any, source: str = "api", updated_at: float | None = None) -> None:
    """Store an arbitrary JSON-serializable payload for ``backend``.

    Low-level: no persistent-pin merging (that is ``store_models``).
    ``updated_at`` defaults to now; seeders pass ``0.0`` to mark the
    entry stale-from-birth.
    """
    with _LOCK:
        cache = load_cache()
        cache["version"] = _CACHE_VERSION
        cache.setdefault("backends", {})[backend] = {
            "updated_at": time.time() if updated_at is None else updated_at,
            "source": source,
            "data": data,
        }
        save_cache(cache)


def store_models(backend: str, models: list[dict], source: str = "api") -> list[dict]:
    """Store a freshly-fetched model list, honoring persistent pins.

    Merge rules (the persistent contract):

    1. A model that was pinned in the cache and is still present in the
       new list keeps ``"persistent": true`` on its (fresh) entry.
    2. A pinned model the live list no longer contains is RE-ADDED from
       the cache — it is never cleared by a refresh.
    3. Everything else is replaced wholesale by the fresh data.

    Returns the merged list that was stored (so callers return exactly
    what the cache now holds).
    """
    with _LOCK:
        cache = load_cache()
        backends = cache.setdefault("backends", {})
        prev_entry = backends.get(backend)
        prev_models = prev_entry.get("data") if isinstance(prev_entry, dict) else None
        prev_by_name: dict[str, dict] = {}
        if isinstance(prev_models, list):
            for m in prev_models:
                name = _model_name(m)
                if name is not None and isinstance(m, dict) and m.get("persistent"):
                    prev_by_name[name] = m

        merged: list[dict] = []
        seen: set[str] = set()
        for m in models:
            name = _model_name(m)
            if name is None:
                continue
            entry = dict(m)
            if name in prev_by_name:
                entry["persistent"] = True
            seen.add(name)
            merged.append(entry)

        # Rule 2: re-attach persistent models the live list dropped.
        for name, prev in prev_by_name.items():
            if name not in seen:
                merged.append(dict(prev))

        backends[backend] = {
            "updated_at": time.time(),
            "source": source,
            "data": merged,
        }
        cache["version"] = _CACHE_VERSION
        save_cache(cache)
        return merged


def ensure_seeded(backend: str, seed_models: list[dict]) -> bool:
    """Seed ``backend``'s cache entry with the initial defaults.

    Writes ``seed_models`` (typically the backend's static catalog shaped
    like ``list_models()`` output) as the backend's cache entry IF the
    backend has no entry yet. The entry is stamped ``updated_at = 0.0``
    (stale-from-birth, ``source: "seed"``) so the next ``list_models()``
    still refreshes from the live API — the seed only serves as the
    offline fallback until the first successful refresh.

    Returns True when a seed was written, False when an entry (seed or
    live) already existed.
    """
    with _LOCK:
        cache = load_cache()
        backends = cache.setdefault("backends", {})
        existing = backends.get(backend)
        if isinstance(existing, dict) and "updated_at" in existing:
            return False
        models = [m for m in seed_models if _model_name(m) is not None]
        backends[backend] = {"updated_at": 0.0, "source": "seed", "data": models}
        cache["version"] = _CACHE_VERSION
        save_cache(cache)
        return True


# ─────────────────────────────────────────────────────────────────────────
# Persistent pin management
# ─────────────────────────────────────────────────────────────────────────


def set_persistent(backend: str, model: str, persistent: bool = True) -> bool:
    """Set (or clear) the ``persistent`` pin on ``model`` for ``backend``.

    If the model is not cached yet, a minimal stub entry is created so
    the pin exists before the model ever shows up in a live catalog —
    on the next refresh the stub is either upgraded to the full live
    entry (with the pin carried over) or serves as the pin placeholder
    that gets re-attached as-is. Returns True on success.
    """
    if not model:
        return False
    with _LOCK:
        cache = load_cache()
        backends = cache.setdefault("backends", {})
        entry = backends.get(backend)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), list):
            # No entry for this backend yet — create a stub holding just
            # the pinned model so the flag survives until the first refresh.
            entry = {
                "updated_at": 0.0,
                "source": "manual",
                "data": [],
            }
            backends[backend] = entry
        models = entry["data"]
        for m in models:
            if _model_name(m) == model:
                if persistent:
                    m["persistent"] = True
                else:
                    m.pop("persistent", None)
                entry["source"] = entry.get("source", "api")
                cache["version"] = _CACHE_VERSION
                save_cache(cache)
                return True
        if persistent:
            models.append(
                {
                    "name": model,
                    "size": 0,
                    "details": {},
                    "persistent": True,
                    "source": "manual",
                }
            )
            cache["version"] = _CACHE_VERSION
            save_cache(cache)
            return True
        return False


def is_persistent(backend: str, model: str) -> bool:
    """True when ``model`` carries the persistent pin for ``backend``."""
    models = get_stale_models(backend)
    if not models:
        return False
    for m in models:
        if _model_name(m) == model and isinstance(m, dict) and m.get("persistent"):
            return True
    return False


def persistent_names(backend: str) -> set[str]:
    """Names of all pinned models for ``backend``."""
    models = get_stale_models(backend) or []
    return {
        name
        for m in models
        if (name := _model_name(m)) is not None and isinstance(m, dict) and m.get("persistent")
    }


def clear_backend(backend: str, force: bool = False) -> int:
    """Drop ``backend``'s cached model list; return how many entries went away.

    Persistent models are NEVER cleared unless ``force=True``. After a
    non-forced clear the entry stays behind (holding only pinned models,
    if any) marked stale (``updated_at = 0.0``) so the next
    ``list_models()`` refreshes from the live API.
    """
    with _LOCK:
        cache = load_cache()
        backends = cache.setdefault("backends", {})
        entry = backends.get(backend)
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), list):
            return 0
        models = entry["data"]
        if force:
            removed = len(models)
            backends.pop(backend, None)
        else:
            kept = [m for m in models if isinstance(m, dict) and m.get("persistent")]
            removed = len(models) - len(kept)
            if removed == 0:
                return 0
            entry["data"] = kept
            entry["updated_at"] = 0.0
            entry["source"] = "seed" if not kept else entry.get("source", "api")
        cache["version"] = _CACHE_VERSION
        save_cache(cache)
        return removed


def clear_all(force: bool = False) -> int:
    """Clear every backend's entry (persistent pins preserved unless forced)."""
    with _LOCK:
        cache = load_cache()
        backends = cache.get("backends", {})
        total = 0
        for backend in list(backends.keys()):
            total += clear_backend(backend, force=force)
        return total


def cache_info(backend: str) -> dict | None:
    """Status snapshot for ``backend`` (used by ``models --cache-status``)."""
    entry = get_entry(backend)
    if entry is None:
        return None
    data = entry.get("data")
    models = data if isinstance(data, list) else []
    try:
        updated_at = float(entry.get("updated_at", 0.0))
    except (TypeError, ValueError):
        updated_at = 0.0
    age = max(0.0, time.time() - updated_at) if updated_at > 0 else None
    ttl = get_ttl()
    return {
        "backend": backend,
        "model_count": len(models),
        "persistent_count": len(persistent_names(backend)),
        "updated_at": updated_at,
        "age_seconds": age,
        "fresh": age is not None and age < ttl and ttl > 0,
        "ttl_seconds": ttl,
        "source": entry.get("source", ""),
    }


# ─────────────────────────────────────────────────────────────────────────
# Seed catalog loading (the former hardcoded static catalogs)
# ─────────────────────────────────────────────────────────────────────────


def _seed_path() -> Path:
    env_path = os.environ.get("AGENTKTHX_MODEL_SEED", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return Path(__file__).resolve().parent / "data" / "model_seed.json"


def load_seed_catalogs() -> dict[str, dict]:
    """Load the packaged seed file (backend key → ``{model: metadata}``).

    Returns ``{}`` when the file is missing or corrupt — plugins then run
    with empty static catalogs (live discovery still works). The path can
    be overridden with ``AGENTKTHX_MODEL_SEED`` (tests use this).
    """
    path = _seed_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"[ModelCache] Warning: seed file has invalid JSON: {e}")
        return {}
    except OSError:
        return {}
    if not isinstance(data, dict):
        return {}
    # Only backend-shaped dicts are returned; metadata keys (reserved
    # "_" prefix) are dropped here and re-read via load_seed_meta().
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def load_seed_catalog(backend: str) -> dict[str, dict]:
    """Static catalog for one backend from the seed file ({} when absent).

    This is what plugin modules bind at import time (e.g.
    ``ZAI_MODELS = load_seed_catalog("zai")``) — the R07.20 replacement
    for the hardcoded per-plugin catalog literals.
    """
    return load_seed_catalogs().get(backend, {})
