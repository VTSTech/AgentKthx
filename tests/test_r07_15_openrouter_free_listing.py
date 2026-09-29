"""R07.15 regression: ``openrouter/free`` missing from ``agentkthx models
--backend openrouter`` under ``OPENROUTER_FREE_ONLY=true``.

Bug (user-reported, 2026-09-29): the listing showed 16 ``:free``-suffix
models but not ``openrouter/free`` — the named "Free Models Router" that
auto-routes to the cheapest free model and doubles as the plugin's
default model (``OPENROUTER_DEFAULT_MODEL``).

Root cause was TWO layers deep:

  1. CLI layer (the deterministic killer): ``cmd_models`` re-filtered at
     the command level with a bare ``m["name"].endswith(":free")`` check.
     ``"openrouter/free".endswith(":free")`` is False, so the router was
     stripped even though the backend's ``list_models()`` had correctly
     accepted it since R07.09. The ZAI branch right below used the
     backend's ``_is_free_model()`` helper — the OpenRouter branch never
     got the same treatment.
  2. Backend layer (latent): ``/v1/models`` does not reliably include
     the router (public listings show it today; the R07.10 note about
     router models returning null fields in this same endpoint shows its
     shape has always been flaky). If the API omits it, the backend list
     lacked it too.

Fix:
  - ``cmd_models`` now filters through the shared ``_is_free_model()``
    helper (mirrors the ZAI branch).
  - ``list_models()`` injects a synthetic ``_free_router_entry()`` when
    the API response omits the router — in both the live path and the
    catalog-fallback path (where FREE_ONLY would otherwise yield an
    empty list, since the static catalog contains no free models).
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.openrouter.openrouter import (
    OpenRouterBackend,
    _free_router_entry,
    _is_free_model,
)

ROUTER = "openrouter/free"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_class_cache():
    """MAINT-19 made the model cache CLASS-level — reset it around every
    test so one test's fetch never satisfies another test's assertions."""
    OpenRouterBackend._model_cache = None
    OpenRouterBackend._cache_time = 0
    yield
    OpenRouterBackend._model_cache = None
    OpenRouterBackend._cache_time = 0


@pytest.fixture
def backend(monkeypatch):
    """An OpenRouterBackend with a dummy key, as in test_openrouter_free_models.

    ``__init__`` eagerly calls ``list_models()`` to warm the MAINT-19
    class-level cache — a REAL network fetch that would poison every
    subsequent assertion (the cache TTL check short-circuits before the
    test's patched urlopen is ever reached). Construct with the network
    already cut, then reset both the class-level (MAINT-19) and any
    instance-level (failure-fallback) cache attributes so each test
    starts from a genuinely cold cache.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test1234567890")
    with unittest.mock.patch("urllib.request.urlopen", side_effect=OSError("no network during init")):
        instance = OpenRouterBackend()
    # Reset the class-level cache (MAINT-19) — and critically, POP any
    # instance-level attrs left by the init-time failure fallback rather
    # than assigning None: ``instance._model_cache = None`` would CREATE an
    # instance attribute that permanently shadows the class cache on every
    # later ``self._model_cache`` read (get_model_max_context would then
    # never see the refreshed listing).
    OpenRouterBackend._model_cache = None
    OpenRouterBackend._cache_time = 0
    instance.__dict__.pop("_model_cache", None)
    instance.__dict__.pop("_cache_time", None)
    return instance


def _api_model(model_id: str) -> dict:
    """Shape a /models entry the way the live API does (string pricing,
    null-able top_provider fields — see the R07.10 notes)."""
    is_free = model_id.endswith(":free") or model_id == ROUTER
    return {
        "id": model_id,
        "context_length": 256000,
        "modality": "text->text",
        "top_provider": {"max_completion_tokens": None if is_free else 8192},
        "pricing": {
            "prompt": "0" if is_free else "0.5",
            "completion": "0" if is_free else "1.5",
        },
    }


def _mock_urlopen(payload: dict):
    """Build a urlopen stand-in serving ``payload`` as the response body."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return MagicMock(return_value=resp)


def _free_only(monkeypatch: pytest.MonkeyPatch, value: bool) -> None:
    """Flip the plugin's FREE_ONLY flag.

    The plugin does ``from ...config import OPENROUTER_FREE_ONLY`` at module
    import time, so the env var alone would not be seen — patch the module
    global the code actually reads.
    """
    monkeypatch.setattr(
        "agentkthx.plugins.openrouter.openrouter.OPENROUTER_FREE_ONLY", value
    )


# ---------------------------------------------------------------------------
# _free_router_entry() / _is_free_model()
# ---------------------------------------------------------------------------

class TestFreeRouterEntry:
    def test_entry_shape(self):
        entry = _free_router_entry()
        assert entry["name"] == ROUTER
        details = entry["details"]
        assert details["free_tier"] is True
        assert details["is_chat_model"] is True
        assert details["context_length"] == 200000
        assert details["pricing"] == {"prompt": 0.0, "completion": 0.0}
        # model_data keeps a minimal original-response shape for consumers
        assert entry["model_data"]["id"] == ROUTER
        assert entry["model_data"]["top_provider"]["max_completion_tokens"] is None

    def test_router_passes_free_check(self):
        assert _is_free_model(ROUTER) is True
        # And the canonical :free marker still works
        assert _is_free_model("qwen/qwen3.8-27b:free") is True
        assert _is_free_model("openai/gpt-4o") is False


# ---------------------------------------------------------------------------
# list_models() — live path
# ---------------------------------------------------------------------------

class TestListModelsRouterInjection:
    def test_router_injected_when_api_omits_it(self, backend, monkeypatch):
        payload = {"data": [_api_model("qwen/qwen3.8-27b:free"),
                            _api_model("google/gemma-4-31b-it:free")]}
        with unittest.mock.patch("urllib.request.urlopen", _mock_urlopen(payload)):
            models = backend.list_models()
        names = [m["name"] for m in models]
        assert ROUTER in names
        assert names.count(ROUTER) == 1
        # Both API models survived the parse + catalog merge
        assert "qwen/qwen3.8-27b:free" in names
        assert "google/gemma-4-31b-it:free" in names

    def test_no_duplicate_when_api_includes_router(self, backend, monkeypatch):
        payload = {"data": [_api_model("qwen/qwen3.8-27b:free"),
                            _api_model(ROUTER)]}
        with unittest.mock.patch("urllib.request.urlopen", _mock_urlopen(payload)):
            models = backend.list_models()
        names = [m["name"] for m in models]
        assert names.count(ROUTER) == 1

    def test_free_only_filter_keeps_router(self, backend, monkeypatch):
        _free_only(monkeypatch, True)
        payload = {"data": [_api_model("qwen/qwen3.8-27b:free"),
                            _api_model("openai/gpt-4o")]}
        with unittest.mock.patch("urllib.request.urlopen", _mock_urlopen(payload)):
            models = backend.list_models()
        names = [m["name"] for m in models]
        # The paid model is filtered out; the router survives
        assert ROUTER in names
        assert "openai/gpt-4o" not in names
        assert len(names) == 2

    def test_router_context_resolves_via_cache(self, backend, monkeypatch):
        """The models command's Context column reads
        get_model_max_context() → the injected entry must be discoverable
        through the same cache list_models() populates."""
        payload = {"data": [_api_model("qwen/qwen3.8-27b:free")]}
        with unittest.mock.patch("urllib.request.urlopen", _mock_urlopen(payload)):
            backend.list_models()
        assert backend.get_model_max_context(ROUTER) == 200000


# ---------------------------------------------------------------------------
# list_models() — catalog fallback path (API unreachable)
# ---------------------------------------------------------------------------

class TestFallbackPathRouter:
    def test_fallback_includes_router_under_free_only(self, backend, monkeypatch):
        """The static catalog contains no free models — without the injected
        router a FREE_ONLY fallback list would be empty."""
        _free_only(monkeypatch, True)
        with unittest.mock.patch(
            "urllib.request.urlopen", side_effect=OSError("API unreachable")
        ):
            models = backend.list_models()
        names = [m["name"] for m in models]
        assert ROUTER in names
        # No paid catalog model leaks through the FREE_ONLY filter
        assert all(_is_free_model(n) for n in names)

    def test_fallback_includes_router_without_free_only(self, backend, monkeypatch):
        _free_only(monkeypatch, False)
        with unittest.mock.patch(
            "urllib.request.urlopen", side_effect=OSError("API unreachable")
        ):
            models = backend.list_models()
        names = [m["name"] for m in models]
        assert ROUTER in names
        assert len(names) > 1  # catalog models + the router


# ---------------------------------------------------------------------------
# CLI layer — cmd_models free-only filter
# ---------------------------------------------------------------------------

class TestCliFreeFilter:
    def test_models_command_no_longer_uses_bare_suffix_check(self):
        """Pin the exact regression: the OpenRouter branch must filter via
        the shared helper, not ``m["name"].endswith(":free")``."""
        from agentkthx.cli.commands import models as models_mod
        src = inspect.getsource(models_mod)
        assert 'm["name"].endswith(":free")' not in src
        assert "_is_free_model" in src

    def test_cmd_models_lists_router_under_free_only(self, monkeypatch, capsys):
        """End-to-end through cmd_models: a backend list that already
        contains the router (as the fixed backend guarantees) must render
        the router row and drop paid models under FREE_ONLY."""
        from agentkthx.cli.commands import models as models_mod
        import agentkthx.cli as cli_pkg

        monkeypatch.setattr("agentkthx.config.OPENROUTER_FREE_ONLY", True)
        # ACP is not under test — never initialize it
        monkeypatch.setattr(cli_pkg, "_init_acp", lambda *a, **k: (None, None))

        fake_models = [
            {"name": "qwen/qwen3.8-27b:free", "size": 0,
             "details": {"family": "qwen", "backend": "openrouter",
                         "context_length": 256000}},
            {"name": ROUTER, "size": 0,
             "details": {"family": "openrouter", "backend": "openrouter",
                         "context_length": 200000}},
            {"name": "openai/gpt-4o", "size": 0,
             "details": {"family": "openai", "backend": "openrouter",
                         "context_length": 128000}},
        ]

        class _StubBackend:
            is_cloud = True
            base_url = "https://openrouter.ai/api/v1"

            def is_running(self):
                return True

            def list_models(self):
                return list(fake_models)

            def get_model_runtime_context(self, name, family=None):
                return 8192

            def get_model_max_context(self, name, family=None):
                for m in fake_models:
                    if m["name"] == name:
                        return m["details"]["context_length"]
                return 128000

        monkeypatch.setattr(models_mod, "get_backend", lambda *a, **k: _StubBackend())

        args = argparse.Namespace(
            backend="openrouter",
            api_mode=None,
            tool_support=False,
            no_cache=False,
            acp=False,
        )
        rc = models_mod.cmd_models(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert ROUTER in out            # ← the user-visible regression
        assert "qwen3.8-27b:free" in out
        assert "gpt-4o" not in out      # paid model filtered at CLI level
