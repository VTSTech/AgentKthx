"""
R07.20 — Persistent JSON model-catalog cache.

Covers ``agentkthx/model_cache.py`` (the cache manager), the seed file
that replaced the hardcoded static catalogs, the backend list_models()
integration (fresh-hit short-circuit, refresh-on-stale, offline
fallback, persistent-pin re-attachment), and the ``agentkthx models``
cache-management flags.

The autouse fixture in tests/conftest.py points AGENTKTHX_MODEL_CACHE at
a per-test file, so none of these tests touch the real user cache.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agentkthx.model_cache as mc
from agentkthx.cli.parser import create_parser

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


def _age_entry(backend: str, age_seconds: float) -> None:
    """Rewrite the cached entry's updated_at so it is ``age_seconds`` old."""
    cache = mc.load_cache()
    cache["backends"][backend]["updated_at"] = time.time() - age_seconds
    mc.save_cache(cache)


# ---------------------------------------------------------------------------
# Cache manager: storage + TTL
# ---------------------------------------------------------------------------


class TestStorageRoundtrip:
    def test_store_and_get_fresh(self):
        mc.store("zai", [_entry("glm-5.1")], source="api")
        data = mc.get_fresh("zai")
        assert data is not None
        assert [m["name"] for m in data] == ["glm-5.1"]

    def test_entry_shape_and_source(self):
        mc.store("zai", [_entry("glm-5.1")], source="api")
        entry = mc.get_entry("zai")
        assert entry is not None
        assert set(entry) >= {"updated_at", "source", "data"}
        assert entry["source"] == "api"
        assert abs(entry["updated_at"] - time.time()) < 5

    def test_missing_backend_returns_none(self):
        assert mc.get_fresh("nope") is None
        assert mc.get_stale("nope") is None
        assert mc.get_entry("nope") is None

    def test_atomic_write_leaves_no_temp_files(self):
        mc.store("zai", [_entry("glm-5.1")])
        leftovers = [
            p for p in mc.get_cache_path().parent.iterdir() if p.name.startswith(".model_catalog_")
        ]
        assert leftovers == []

    def test_corrupt_file_treated_as_empty(self):
        mc.store("zai", [_entry("glm-5.1")])
        mc.get_cache_path().write_text("{not json at all", encoding="utf-8")
        assert mc.load_cache()["backends"] == {}
        assert mc.get_fresh("zai") is None
        # and a store recovers the file
        mc.store("zai", [_entry("glm-5.2")])
        assert mc.get_fresh("zai") is not None

    def test_cache_file_lives_at_env_override(self):
        assert mc.get_cache_path().name == "model_catalog.json"


class TestTTL:
    def test_stale_after_ttl(self):
        mc.store("zai", [_entry("glm-5.1")])
        assert mc.get_fresh("zai") is not None
        _age_entry("zai", mc.get_ttl() + 1)
        assert mc.get_fresh("zai") is None
        # but the stale payload is still there for the offline fallback
        assert mc.get_stale("zai") is not None

    def test_default_ttl_is_30_minutes(self):
        assert mc.get_ttl() == 1800

    def test_ttl_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MODEL_CACHE_TTL", "60")
        assert mc.get_ttl() == 60
        mc.store("zai", [_entry("glm-5.1")])
        _age_entry("zai", 61)
        assert mc.get_fresh("zai") is None

    def test_ttl_zero_disables_caching(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MODEL_CACHE_TTL", "0")
        mc.store("zai", [_entry("glm-5.1")])
        assert mc.get_fresh("zai") is None
        assert mc.get_stale("zai") is not None  # data is still on disk

    def test_invalid_ttl_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MODEL_CACHE_TTL", "-5")
        assert mc.get_ttl() == 1800
        monkeypatch.setenv("AGENTKTHX_MODEL_CACHE_TTL", "abc")
        assert mc.get_ttl() == 1800

    def test_ensure_seeded_entry_is_stale_from_birth(self):
        mc.ensure_seeded("zai", [_entry("glm-5.1")])
        entry = mc.get_entry("zai")
        assert entry["updated_at"] == 0.0
        assert entry["source"] == "seed"
        assert mc.get_fresh("zai") is None  # stale -> next list_models goes live

    def test_ensure_seeded_never_overwrites(self):
        mc.store("zai", [_entry("glm-5.1")], source="api")
        assert mc.ensure_seeded("zai", [_entry("seed-model")]) is False
        assert [m["name"] for m in mc.get_stale("zai")] == ["glm-5.1"]

    def test_ensure_seeded_reports_first_write(self):
        assert mc.ensure_seeded("zai", [_entry("glm-5.1")]) is True


# ---------------------------------------------------------------------------
# Persistent pin semantics
# ---------------------------------------------------------------------------


class TestPersistentPin:
    def test_set_and_check_pin(self):
        mc.store("zai", [_entry("glm-5.1"), _entry("glm-5.2")])
        assert mc.set_persistent("zai", "glm-5.1", True) is True
        assert mc.is_persistent("zai", "glm-5.1") is True
        assert mc.is_persistent("zai", "glm-5.2") is False
        assert mc.persistent_names("zai") == {"glm-5.1"}

    def test_pin_unknown_model_creates_stub(self):
        assert mc.set_persistent("zai", "brand/new-model", True) is True
        models = mc.get_stale("zai")
        stub = next(m for m in models if m["name"] == "brand/new-model")
        assert stub["persistent"] is True
        assert stub["source"] == "manual"

    def test_unpin(self):
        mc.store("zai", [_entry("glm-5.1")])
        mc.set_persistent("zai", "glm-5.1", True)
        assert mc.set_persistent("zai", "glm-5.1", False) is True
        assert mc.is_persistent("zai", "glm-5.1") is False

    def test_unpin_unknown_model_is_a_noop_true(self):
        assert mc.set_persistent("zai", "ghost", False) is False

    def test_pinned_flag_survives_refresh(self):
        mc.store("zai", [_entry("glm-5.1"), _entry("glm-5.2")])
        mc.set_persistent("zai", "glm-5.1", True)
        # Live refresh still carries glm-5.1 — the flag must survive.
        merged = mc.store_models("zai", [_entry("glm-5.1"), _entry("glm-5.3")])
        names = {m["name"]: m for m in merged}
        assert names["glm-5.1"]["persistent"] is True
        assert "persistent" not in names["glm-5.3"]

    def test_refresh_re_adds_dropped_persistent_model(self):
        mc.store("zai", [_entry("glm-5.1"), _entry("legacy-model")])
        mc.set_persistent("zai", "legacy-model", True)
        # The API no longer lists legacy-model — the pin keeps it alive.
        merged = mc.store_models("zai", [_entry("glm-5.1"), _entry("glm-5.9")])
        names = [m["name"] for m in merged]
        assert "legacy-model" in names
        kept = next(m for m in merged if m["name"] == "legacy-model")
        assert kept["persistent"] is True

    def test_non_persistent_models_are_replaced_by_refresh(self):
        mc.store("zai", [_entry("glm-old")])
        merged = mc.store_models("zai", [_entry("glm-new")])
        assert [m["name"] for m in merged] == ["glm-new"]

    def test_clear_backend_keeps_persistent_and_marks_stale(self):
        mc.store("zai", [_entry("glm-5.1"), _entry("glm-5.2")])
        mc.set_persistent("zai", "glm-5.1", True)
        removed = mc.clear_backend("zai")
        assert removed == 1  # only the non-persistent one went away
        models = mc.get_stale("zai")
        assert [m["name"] for m in models] == ["glm-5.1"]
        assert mc.get_entry("zai")["updated_at"] == 0.0  # stale -> next call refreshes

    def test_clear_backend_force_removes_everything(self):
        mc.store("zai", [_entry("glm-5.1")])
        mc.set_persistent("zai", "glm-5.1", True)
        assert mc.clear_backend("zai", force=True) == 1
        assert mc.get_entry("zai") is None

    def test_clear_all_spans_backends(self):
        mc.store("zai", [_entry("glm-5.1")])
        mc.store("openai", [_entry("gpt-x")])
        assert mc.clear_all() == 2
        # non-forced clear leaves stale empty husks (persistent pins would
        # survive there); the next list_models refreshes from the API
        assert mc.get_stale("zai") == []
        assert mc.get_stale("openai") == []
        assert mc.get_entry("zai")["updated_at"] == 0.0
        # forced clear removes the entries entirely
        assert mc.clear_all(force=True) == 0
        mc.store("zai", [_entry("glm-5.1")])
        assert mc.clear_all(force=True) == 1
        assert mc.get_entry("zai") is None

    def test_empty_model_name_rejected(self):
        assert mc.set_persistent("zai", "", True) is False


# ---------------------------------------------------------------------------
# Seed file (the former hardcoded static catalogs)
# ---------------------------------------------------------------------------


class TestSeedCatalog:
    def test_seed_file_exists_and_parses(self):
        catalogs = mc.load_seed_catalogs()
        assert set(catalogs) >= {
            "zai",
            "openai",
            "openrouter",
            "gemini",
            "mistral",
            "pollinations",
            "huggingface",
        }

    def test_seed_entries_carry_catalog_metadata(self):
        zai = mc.load_seed_catalog("zai")
        assert "glm-5.1" in zai
        assert zai["glm-5.1"]["context_length"] > 0
        assert "pricing" in zai["glm-5.1"]

    def test_plugin_catalogs_load_from_seed_not_python(self):
        """R07.20: the plugin module dicts are JSON-backed (the literal
        dicts are gone from the Python sources)."""
        from agentkthx.plugins.huggingface.huggingface import HF_MODELS
        from agentkthx.plugins.mistral.mistral import MISTRAL_MODELS
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        from agentkthx.plugins.zai.zai import ZAI_MODELS

        assert ZAI_MODELS == mc.load_seed_catalog("zai")
        assert OPENAI_MODELS == mc.load_seed_catalog("openai")
        assert MISTRAL_MODELS == mc.load_seed_catalog("mistral")
        assert HF_MODELS == mc.load_seed_catalog("huggingface")

    def test_no_catalog_literals_remain_in_python_sources(self):
        """The static catalogs are DATA now — grep-guard the sources."""
        plugins = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins"
        offenders = []
        for path in plugins.rglob("*.py"):
            if path.name in (
                "zai.py",
                "openai.py",
                "openrouter.py",
                "gemini.py",
                "mistral.py",
                "pollinations.py",
                "huggingface.py",
            ):
                text = path.read_text(encoding="utf-8")
                for attr in (
                    "ZAI_MODELS",
                    "OPENAI_MODELS",
                    "OPENROUTER_MODELS",
                    "GEMINI_MODELS",
                    "MISTRAL_MODELS",
                    "POLLINATIONS_MODELS",
                    "HF_MODELS",
                ):
                    if (
                        f"{attr}: dict[str, dict] = {{}}" in text
                        or f"{attr}: dict[str, dict] = {{\n" in text
                    ):
                        offenders.append(f"{path.name}:{attr}")
        assert offenders == []

    def test_seed_env_override(self, tmp_path, monkeypatch):
        seed = tmp_path / "seed.json"
        seed.write_text(
            json.dumps({"custom": {"my-model": {"context_length": 1000}}}), encoding="utf-8"
        )
        monkeypatch.setenv("AGENTKTHX_MODEL_SEED", str(seed))
        assert mc.load_seed_catalog("custom") == {"my-model": {"context_length": 1000}}
        assert mc.load_seed_catalog("zai") == {}

    def test_missing_seed_file_degrades_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENTKTHX_MODEL_SEED", str(tmp_path / "absent.json"))
        assert mc.load_seed_catalogs() == {}


# ---------------------------------------------------------------------------
# Backend integration
# ---------------------------------------------------------------------------


def _make_zai(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "sk-test-key-1234567890abcdef")
    from agentkthx.plugins.zai.zai import ZaiBackend

    return ZaiBackend()


def _mock_urlopen(payload, calls=None):
    """Patch urllib.request.urlopen to serve ``payload`` (a dict) once per call."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(req, timeout=10):
        if calls is not None:
            calls.append(getattr(req, "full_url", ""))
        return _Resp()

    return fake_urlopen


class TestZaiCacheIntegration:
    def test_fresh_cache_short_circuits_api(self, monkeypatch):
        b = _make_zai(monkeypatch)
        mc.store("zai", [_entry("cached-only-model")], source="api")
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({}, calls=[]))
        models = b.list_models()
        assert [m["name"] for m in models] == ["cached-only-model"]

    def test_stale_cache_triggers_refresh_and_store(self, monkeypatch):
        b = _make_zai(monkeypatch)
        mc.ensure_seeded("zai", b._catalog_fallback_list())
        _age_entry("zai", mc.get_ttl() + 10)
        calls = []
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _mock_urlopen({"data": [{"id": "glm-5.1"}, {"id": "brand-new"}]}, calls=calls),
        )
        models = b.list_models()
        assert calls, "stale cache must trigger a live refresh"
        names = [m["name"] for m in models]
        assert "brand-new" in names and "glm-5.1" in names
        info = mc.cache_info("zai")
        assert info["fresh"] is True and info["source"] == "api"
        # The refreshed list is now served from cache without an API call.
        calls.clear()
        b2 = _make_zai(monkeypatch)
        models2 = b2.list_models()
        assert not calls
        assert [m["name"] for m in models2] == [m["name"] for m in models]

    def test_offline_serves_stale_cache(self, monkeypatch):
        b = _make_zai(monkeypatch)
        mc.store("zai", [_entry("cached-model")], source="api")
        _age_entry("zai", mc.get_ttl() + 10)

        def failing(req, timeout=10):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", failing)
        models = b.list_models()
        assert [m["name"] for m in models] == ["cached-model"]

    def test_offline_first_call_serves_seed_defaults(self, monkeypatch):
        b = _make_zai(monkeypatch)

        def failing(req, timeout=10):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", failing)
        models = b.list_models()
        seed_names = {m["name"] for m in b._catalog_fallback_list()}
        assert {m["name"] for m in models} == seed_names
        # the seed is now in the cache as the initial defaults
        assert mc.get_entry("zai")["source"] == "seed"

    def test_persistent_model_survives_refresh_that_drops_it(self, monkeypatch):
        b = _make_zai(monkeypatch)
        mc.store("zai", [_entry("glm-5.1"), _entry("my-pinned")])
        mc.set_persistent("zai", "my-pinned", True)
        _age_entry("zai", mc.get_ttl() + 10)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"data": [{"id": "glm-5.1"}]}))
        models = b.list_models()
        names = [m["name"] for m in models]
        assert "my-pinned" in names
        stored = {m["name"]: m for m in mc.get_stale("zai")}
        assert stored["my-pinned"]["persistent"] is True


class TestOpenRouterCacheIntegration:
    def _make_backend(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test1234567890")
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend

        OpenRouterBackend._model_cache = None
        OpenRouterBackend._cache_time = 0
        return OpenRouterBackend()

    def test_free_only_does_not_bake_into_the_json_cache(self, monkeypatch):
        """The JSON cache stores the UNFILTERED catalog; FREE_ONLY only
        filters what list_models returns (so toggling the env var works
        without waiting out the TTL)."""
        # The plugin reads OPENROUTER_FREE_ONLY at import time — patch the
        # module binding (same convention as test_r07_15_openrouter_free_listing).
        import agentkthx.plugins.openrouter.openrouter as or_mod

        monkeypatch.setattr(or_mod, "OPENROUTER_FREE_ONLY", True)
        calls = []
        payload = {
            "data": [
                {"id": "google/gemma-3-27b-it:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "openai/gpt-5.5", "pricing": {"prompt": "0.1", "completion": "0.2"}},
            ]
        }
        # Patch BEFORE construction — __init__ calls list_models().
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen(payload, calls=calls))
        b = self._make_backend(monkeypatch)
        models = b.list_models()
        names = [m["name"] for m in models]
        assert "openai/gpt-5.5" not in names  # filtered out of the RETURN value
        assert "google/gemma-3-27b-it:free" in names
        # ...but the JSON cache holds the paid model too
        cached_names = {m["name"] for m in mc.get_stale("openrouter")}
        assert "openai/gpt-5.5" in cached_names

        # Toggle FREE_ONLY off — the paid model comes back from cache,
        # without a re-fetch.
        monkeypatch.setattr(or_mod, "OPENROUTER_FREE_ONLY", False)
        b2 = self._make_backend(monkeypatch)
        calls.clear()
        models2 = b2.list_models()
        assert not calls, "fresh JSON cache must serve without a re-fetch"
        assert "openai/gpt-5.5" in [m["name"] for m in models2]

    def test_offline_serves_stale_before_catalog(self, monkeypatch):
        calls = []

        def failing(req, timeout=10):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", failing)
        self._make_backend(monkeypatch)  # construction fails -> seed fallback
        assert calls == []
        mc.store("openrouter", [_entry("stale-live-model")], source="api")
        _age_entry("openrouter", mc.get_ttl() + 10)
        # fresh instance (empty L1) -> stale L2 served before the catalog
        b2 = self._make_backend(monkeypatch)
        models = b2.list_models()
        assert [m["name"] for m in models] == ["stale-live-model"]


class TestPollinationsCacheIntegration:
    def _make_backend(self, monkeypatch):
        from agentkthx.plugins.pollinations.pollinations import PollinationsBackend

        return PollinationsBackend()  # keyless — the anonymous surface

    def test_fresh_hit_rehydrates_model_cards_without_network(self, monkeypatch):
        b = self._make_backend(monkeypatch)
        card = {
            "id": "openai/gpt-5.4-nano",
            "category": "text",
            "context_length": 400000,
            "health": True,
        }
        entry = b._card_to_entry("openai/gpt-5.4-nano", card)
        mc.store("pollinations", [entry], source="api")
        mc.store("pollinations:cards", {"openai/gpt-5.4-nano": card}, source="api")

        calls = []
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"data": []}, calls=calls))
        models = b.list_models()
        assert not calls, "fresh cache must not hit the gateway"
        assert [m["name"] for m in models] == ["openai/gpt-5.4-nano"]
        # raw cards side-cache rehydrated the instance state
        assert b._model_cards.get("openai/gpt-5.4-nano") == card

    def test_offline_serves_seed_defaults(self, monkeypatch):
        b = self._make_backend(monkeypatch)

        def failing(req, timeout=10):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", failing)
        models = b.list_models()
        assert {m["name"] for m in models} == set(b.MODELS.keys())
        assert mc.get_entry("pollinations")["source"] == "seed"


class TestOrcaRouterCacheIntegration:
    def _make_backend(self, monkeypatch):
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test-key-1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend

        return OrcaRouterBackend()

    def test_seed_contains_routers_and_free_whitelist(self, monkeypatch):
        from agentkthx.plugins.orcarouter.orcarouter import (
            _ORCA_NAMED_ROUTERS,
            ORCAROUTER_FREE_MODEL_WHITELIST,
        )

        b = self._make_backend(monkeypatch)
        seed_names = {m["name"] for m in b._catalog_fallback_list()}
        assert seed_names == set(_ORCA_NAMED_ROUTERS) | set(ORCAROUTER_FREE_MODEL_WHITELIST)

    def test_fresh_cache_short_circuits_api(self, monkeypatch):
        b = self._make_backend(monkeypatch)
        mc.store("orcarouter", [_entry("openai/gpt-4o-mini")], source="api")
        calls = []
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"data": []}, calls=calls))
        models = b.list_models()
        assert not calls
        assert [m["name"] for m in models] == ["openai/gpt-4o-mini"]

    def test_offline_serves_stale(self, monkeypatch):
        b = self._make_backend(monkeypatch)
        mc.store("orcarouter", [_entry("openai/gpt-4o-mini")], source="api")
        _age_entry("orcarouter", mc.get_ttl() + 10)

        def failing(req, timeout=10):
            raise OSError("network down")

        monkeypatch.setattr("urllib.request.urlopen", failing)
        models = b.list_models()
        assert [m["name"] for m in models] == ["openai/gpt-4o-mini"]


# ---------------------------------------------------------------------------
# cache_info + CLI flags
# ---------------------------------------------------------------------------


class TestCacheInfo:
    def test_info_shape(self):
        mc.store("zai", [_entry("glm-5.1"), _entry("glm-5.2")])
        mc.set_persistent("zai", "glm-5.1", True)
        info = mc.cache_info("zai")
        assert info["backend"] == "zai"
        assert info["model_count"] == 2
        assert info["persistent_count"] == 1
        assert info["fresh"] is True
        assert info["source"] == "api"
        assert 0 <= info["age_seconds"] < 30

    def test_info_none_for_unknown_backend(self):
        assert mc.cache_info("nope") is None


class TestModelsCommandFlags:
    """`agentkthx models --persist/--unpersist/--clear-cache/--cache-status`."""

    @staticmethod
    def _stub_config(monkeypatch, backend="zai"):
        """Stub get_config() in the models command module — the parser's
        --backend choices only cover entry-point backends, while zai is a
        plugin resolved at main() time."""
        import agentkthx.cli.commands.models as models_mod

        class _Cfg:
            pass

        cfg = _Cfg()
        cfg.backend = backend
        monkeypatch.setattr(models_mod, "get_config", lambda: cfg)

    def _run(self, argv, capsys, monkeypatch, backend="zai"):
        from agentkthx.cli.commands.models import cmd_models

        self._stub_config(monkeypatch, backend)
        ns = create_parser().parse_args(argv)
        rc = cmd_models(ns)
        out = capsys.readouterr().out
        return rc, out

    def test_parser_accepts_new_flags(self):
        ns = create_parser().parse_args(["models", "--persist", "glm-5.1"])
        assert ns.persist == "glm-5.1"
        ns = create_parser().parse_args(["models", "--clear-cache"])
        assert ns.clear_cache is True
        ns = create_parser().parse_args(["models", "--cache-status"])
        assert ns.cache_status is True
        ns = create_parser().parse_args(["models", "--unpersist", "glm-5.1"])
        assert ns.unpersist == "glm-5.1"

    def test_persist_roundtrip(self, capsys, monkeypatch):
        rc, out = self._run(["models", "--persist", "glm-5.1"], capsys, monkeypatch)
        assert rc == 0
        assert "persistent" in out
        assert mc.is_persistent("zai", "glm-5.1")

        rc, out = self._run(["models", "--unpersist", "glm-5.1"], capsys, monkeypatch)
        assert rc == 0
        assert not mc.is_persistent("zai", "glm-5.1")

    def test_clear_cache_keeps_persistent(self, capsys, monkeypatch):
        mc.store("zai", [_entry("glm-5.1"), _entry("glm-5.2")])
        mc.set_persistent("zai", "glm-5.1", True)
        rc, out = self._run(["models", "--clear-cache"], capsys, monkeypatch)
        assert rc == 0
        assert "1" in out  # 1 cleared, 1 kept
        names = {m["name"] for m in mc.get_stale("zai")}
        assert names == {"glm-5.1"}

    def test_cache_status_lists_backends(self, capsys, monkeypatch):
        mc.store("zai", [_entry("glm-5.1")], source="api")
        mc.set_persistent("zai", "glm-5.1", True)
        rc, out = self._run(["models", "--cache-status"], capsys, monkeypatch)
        assert rc == 0
        assert "model_catalog.json" in out
        assert "1800s" in out
        assert "zai" in out and "1 persistent" in out

    def test_persist_stub_when_backend_absent(self, capsys, monkeypatch):
        rc, out = self._run(
            ["models", "--persist", "brand/new"], capsys, monkeypatch, backend="gemini"
        )
        assert rc == 0
        assert mc.is_persistent("gemini", "brand/new")
