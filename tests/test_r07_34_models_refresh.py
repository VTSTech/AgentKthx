"""
R07.34 — ``agentkthx models --refresh``.

``--refresh`` forces a live catalog re-fetch for the selected backend:

1. the backend's persistent JSON catalog entry is marked STALE
   (``model_cache.mark_stale`` — data kept on disk as the offline
   fallback, unlike ``--clear-cache`` which drops it);
2. the backend's in-process L1 class cache is reset;
3. the guaranteed cache-miss ``list_models()`` re-fetches live and
   ``store_models()`` replaces the stored catalog wholesale — entries
   the provider no longer lists are removed (persistent pins are
   re-attached by the cache manager);
4. if the live fetch fails (no store happened) the old catalog still
   serves and the command reports the failure instead of claiming a
   prune that never occurred.

The autouse fixture in tests/conftest.py points AGENTKTHX_MODEL_CACHE at
a per-test file, so none of these tests touch the real user cache.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agentkthx.model_cache as mc
from agentkthx.backends.ollama import OllamaBackend
from agentkthx.cli.parser import create_parser
from agentkthx.core.types import ToolSupportLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(model: str, ctx: int = 128000, **extra) -> dict:
    """Minimal AgentKthx-shaped model entry."""
    return {
        "name": model,
        "size": 0,
        "details": {"family": "test", "backend": "test", "context_length": ctx},
        **extra,
    }


def _seed_fresh(backend: str, names: list[str], persistent: str | None = None) -> None:
    """Store a fresh (source: api) catalog entry with the given names."""
    mc.store(backend, [_entry(n) for n in names], source="api")
    if persistent is not None:
        mc.set_persistent(backend, persistent, True)


def _cached_names(backend: str) -> set[str]:
    data = mc.get_stale_models(backend) or []
    return {m["name"] for m in data if isinstance(m, dict) and "name" in m}


class StubCacheCloudBackend:
    """Cloud stand-in that participates in the JSON catalog cache exactly
    like the real discovery backends: ``list_models()`` fetches "live"
    (the canned list) and stores the result via ``store_models`` — the
    store-on-success contract the refresh outcome report relies on.

    ``store_on_list=False`` simulates a FAILED live fetch that serves the
    stale catalog as fallback without re-stamping the cache (401 degrade,
    unreachable provider, ROB-42 stale-first).
    """

    MODEL_CACHE_KEY = "zai"
    is_cloud = True

    def __init__(self, live: list[dict], store_on_list: bool = True):
        self._live = live
        self._store_on_list = store_on_list
        self.fetches = 0
        self.base_url = "https://stub.cloud/v1"
        self.api_mode = None

    def is_running(self) -> bool:
        return True

    def list_models(self) -> list[dict]:
        if self._store_on_list:
            # Real backends short-circuit on a FRESH L2 hit (no API call);
            # a stale/absent entry goes live and stores on success.
            cached = mc.get_cached_models(self.MODEL_CACHE_KEY)
            if cached is not None:
                return [dict(m) for m in cached]
            self.fetches += 1
            mc.store_models(self.MODEL_CACHE_KEY, list(self._live))
        else:
            # Failure mode: went live, fetch failed, served the stale
            # catalog WITHOUT re-stamping the cache.
            self.fetches += 1
        return [dict(m) for m in self._live]

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768

    def test_tool_support(self, name, family=None, force_test=False):
        return ToolSupportLevel.NATIVE


class StubPlainCloudBackend(StubCacheCloudBackend):
    """Cloud stand-in WITHOUT a JSON catalog cache (static catalog)."""

    MODEL_CACHE_KEY = None  # type: ignore[assignment]
    is_cloud = True


class StubOllamaBackend(OllamaBackend):
    """OllamaBackend stand-in — no network, no super().__init__ side effects
    (same shape as the ROB-37 stub: cmd_models branches on isinstance)."""

    def __init__(self, entries):
        self._entries = entries
        self._base_url = "http://stub:11434"  # base_url is a read-only property
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return list(self._entries)

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768


def _stub_env(monkeypatch, backend_obj, backend_name: str) -> None:
    """Wire cmd_models' collaborators to the stub (same seams as the
    R07.20 flag tests + the ROB-37 table tests)."""
    import agentkthx.cli.commands.models as models_mod
    import agentkthx.config as config_mod

    monkeypatch.setattr(
        models_mod, "get_config", lambda: argparse.Namespace(backend=backend_name)
    )
    monkeypatch.setattr(
        models_mod, "get_backend", lambda name, api_mode=None: backend_obj
    )
    monkeypatch.setattr("agentkthx.cli._init_acp", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_tool_support",
        lambda model, api_mode="openre": None,
    )
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_thinking_support",
        lambda model: None,
    )
    # apply_free_only_filter reads these at call time — pin False so the
    # developer's ~/.agentkthx/.env cannot flip a test.
    monkeypatch.setattr(config_mod, "OPENROUTER_FREE_ONLY", False, raising=False)
    monkeypatch.setattr(config_mod, "ZAI_FREE_ONLY", False, raising=False)


def _run(argv: list[str], capsys, monkeypatch, backend_obj, backend_name: str):
    from agentkthx.cli.commands.models import cmd_models

    _stub_env(monkeypatch, backend_obj, backend_name)
    ns = create_parser().parse_args(argv)
    rc = cmd_models(ns)
    out = capsys.readouterr().out
    return rc, out


# ---------------------------------------------------------------------------
# model_cache.mark_stale — the new cache-manager primitive
# ---------------------------------------------------------------------------


class TestMarkStale:
    def test_keeps_data_and_marks_entry_stale(self):
        mc.store("zai", [_entry("a"), _entry("b")], source="api")
        assert mc.get_fresh("zai") is not None  # fresh before
        assert mc.mark_stale("zai") is True
        assert mc.get_fresh("zai") is None  # stale now — next read is a miss
        assert _cached_names("zai") == {"a", "b"}  # data kept as fallback
        assert mc.get_entry("zai")["updated_at"] == 0.0

    def test_unknown_backend_returns_false(self):
        assert mc.mark_stale("nope") is False

    def test_idempotent(self):
        mc.store("zai", [_entry("a")], source="api")
        assert mc.mark_stale("zai") is True
        assert mc.mark_stale("zai") is True
        assert _cached_names("zai") == {"a"}

    def test_store_after_mark_stale_replaces_wholesale(self):
        """The refresh contract end-to-end: mark stale → live fetch →
        store_models prunes dropped entries and re-attaches pins."""
        _seed_fresh("zai", ["a", "b", "c"], persistent="a")
        mc.mark_stale("zai")
        merged = mc.store_models("zai", [_entry("b"), _entry("c"), _entry("d")])
        assert _cached_names("zai") == {"a", "b", "c", "d"}  # a = re-attached pin
        assert mc.is_persistent("zai", "a")
        assert mc.cache_info("zai")["fresh"] is True  # fresh again after store
        assert mc.get_entry("zai")["source"] == "api"
        assert {m["name"] for m in merged} == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# agentkthx models --refresh — CLI behavior
# ---------------------------------------------------------------------------


class TestModelsRefreshFlag:
    def test_parser_accepts_refresh(self):
        ns = create_parser().parse_args(["models", "--refresh"])
        assert ns.refresh is True
        ns = create_parser().parse_args(["models"])
        assert ns.refresh is False

    def test_refresh_forces_live_fetch_on_fresh_cache(self, capsys, monkeypatch):
        """The point of the flag: a FRESH cache would normally serve from
        L2 with zero live fetches; --refresh must still hit the API."""
        _seed_fresh("zai", ["old-a", "old-b"])
        stub = StubCacheCloudBackend([_entry("old-a"), _entry("old-b")])
        rc, out = _run(["models"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        assert stub.fetches == 0  # fresh L2 short-circuit, no API call

        stub2 = StubCacheCloudBackend([_entry("old-a"), _entry("old-b")])
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub2, "zai")
        assert rc == 0
        assert stub2.fetches == 1  # forced live fetch
        assert "Refreshing catalog for zai" in out
        assert "no changes" in out  # same catalog → reported, not crashed

    def test_refresh_prunes_nonexistent_entries(self, capsys, monkeypatch):
        _seed_fresh("zai", ["th-alpha-dead", "th-beta", "th-gamma"])
        live = [_entry("th-beta"), _entry("th-gamma"), _entry("th-delta-new")]
        stub = StubCacheCloudBackend(live)
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        # Catalog replaced wholesale: dead entry gone, new entry stored.
        assert _cached_names("zai") == {"th-beta", "th-gamma", "th-delta-new"}
        assert "+1 new" in out and "-1 removed" in out
        assert "th-delta-new" in out  # added preview
        assert "th-alpha-dead" in out  # removed preview
        assert "Total: 3 models" in out  # the table shows the fresh catalog

    def test_refresh_pins_survive_provider_drop(self, capsys, monkeypatch):
        """A provider dropping a pinned model must not unlist it: the pin
        is re-attached by store_models and called out in the report — it
        is never counted as a removal."""
        _seed_fresh("zai", ["pin-keep", "plain-b"], persistent="pin-keep")
        live = [_entry("plain-b")]  # provider no longer lists pin-keep
        stub = StubCacheCloudBackend(live)
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        # Catalog still holds both: the pin was re-attached after the
        # wholesale replace.
        assert _cached_names("zai") == {"plain-b", "pin-keep"}
        assert mc.is_persistent("zai", "pin-keep")
        # Cache-level diff is empty (the pin stayed listed) → "no changes"
        # — which itself proves the pin was NOT counted as removed.
        assert "no changes" in out
        assert "persistent pin(s) re-attached" in out
        assert "1 persistent pin" in out
        assert "Live refresh failed" not in out

    def test_refresh_failure_keeps_catalog_and_warns(self, capsys, monkeypatch):
        """Failed live fetch (no store): old catalog intact, warning shown,
        nothing reported as removed."""
        _seed_fresh("zai", ["keep-a", "keep-b"])
        stale_copy = [_entry("keep-a"), _entry("keep-b")]
        stub = StubCacheCloudBackend(stale_copy, store_on_list=False)
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        assert "Live refresh failed" in out
        assert "kept as fallback" in out
        assert _cached_names("zai") == {"keep-a", "keep-b"}  # nothing lost
        assert mc.get_entry("zai")["updated_at"] == 0.0  # still stale-marked
        assert "removed" not in out.replace("nothing was removed", "")

    def test_refresh_first_contact_fetches_and_stores(self, capsys, monkeypatch):
        """No cache entry yet: refresh announces the live fetch and the
        store creates the entry (seed → live replace)."""
        assert mc.get_entry("zai") is None
        live = [_entry("fresh-x"), _entry("fresh-y")]
        stub = StubCacheCloudBackend(live)
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        assert "No cached catalog for zai" in out
        assert _cached_names("zai") == {"fresh-x", "fresh-y"}
        assert "+2 new" in out

    def test_refresh_static_backend_note(self, capsys, monkeypatch):
        """A cloud backend without a JSON catalog cache gets the static
        note and its (stub) listing still renders."""
        stub = StubPlainCloudBackend([_entry("static-1")])
        rc, out = _run(
            ["models", "--refresh"], capsys, monkeypatch, stub, "test-plugin"
        )
        assert rc == 0
        assert "no JSON catalog cache" in out
        assert "static-1" in out

    def test_refresh_ollama_lists_live_note(self, capsys, monkeypatch):
        """Ollama has no catalog cache at all — the note says so and the
        normal live listing proceeds."""
        stub = StubOllamaBackend(
            [
                {
                    "name": "qwen2.5:0.5b",
                    "size": int(0.4 * 1024**3),
                    "details": {"family": "qwen2"},
                }
            ]
        )
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "ollama")
        assert rc == 0
        assert "/api/tags" in out
        assert "nothing cached to refresh" in out
        assert mc.get_entry("ollama") is None  # no cache write happened

    def test_refresh_does_not_touch_other_backends(self, capsys, monkeypatch):
        _seed_fresh("zai", ["z-1"])
        _seed_fresh("openrouter", ["or-1"])
        stub = StubCacheCloudBackend([_entry("z-1"), _entry("z-2")])
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        assert _cached_names("openrouter") == {"or-1"}
        assert mc.get_entry("openrouter")["updated_at"] > 0.0  # still fresh

    def test_refresh_output_is_stable_across_repeat_runs(self, capsys, monkeypatch):
        """Second refresh over an identical catalog: still a live fetch,
        reported as no changes, cache stays consistent."""
        live = [_entry("same-1")]
        stub = StubCacheCloudBackend(live)
        _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        first_names = _cached_names("zai")
        rc, out = _run(["models", "--refresh"], capsys, monkeypatch, stub, "zai")
        assert rc == 0
        assert stub.fetches == 2
        assert "no changes" in out
        assert _cached_names("zai") == first_names
