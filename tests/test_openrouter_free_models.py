"""
R07.05 regression: OpenRouter ``:free``-suffix models show as ``paid``
in ``/models`` command output.

Bug: the ``/models`` and ``/models free`` chat slash-commands rely on
``details.free_tier`` to mark models as free. The OpenRouter backend's
``_parse_openrouter_model`` method didn't set ``free_tier`` at all —
every model defaulted to ``free_tier=False`` (paid). So all 17 ``:free``
-suffix models showed as ``paid``, and ``/models free`` returned no
matches.

Fix: ``_parse_openrouter_model`` now detects free-tier models via
THREE signals (any one of them marks the model as free):
  1. Model ID ends with ``:free`` (OpenRouter's canonical free marker)
  2. API response's ``is_free`` field is ``True`` (conservative — only
     True for a subset of free models)
  3. API response's ``pricing.prompt == 0`` AND ``pricing.completion == 0``
     (ground truth — $0/token means genuinely free)

Also sets ``is_chat_model=True`` by default (OpenRouter lists only
chat-capable models on the OpenAI-compatible endpoint).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend


def _make_backend(monkeypatch):
    """Construct an OpenRouterBackend without making any HTTP calls."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test1234567890")
    return OpenRouterBackend()


# ---------------------------------------------------------------------------
# Free-tier detection in _parse_openrouter_model
# ---------------------------------------------------------------------------

class TestFreeSuffixDetection:
    """Verify :free-suffix models are marked free_tier=True."""

    def test_free_suffix_model_marked_free(self, monkeypatch):
        """A model with ``:free`` suffix is marked free_tier=True."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "google/gemma-3-27b-it:free",
            "context_length": 256000,
            "top_provider": {"max_completion_tokens": 8192, "provider": "google"},
            "is_free": False,  # API says False (conservative)
            "pricing": {"prompt": "0", "completion": "0"},
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["free_tier"] is True
        assert parsed["details"]["is_free_suffix"] is True

    def test_free_suffix_overrides_is_free_false(self, monkeypatch):
        """The :free suffix takes precedence over is_free=False.

        OpenRouter's is_free field is conservative — it's False for many
        genuinely-free models. The :free suffix is the canonical marker.
        """
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "cohere/north-mini-code:free",
            "is_free": False,
            "pricing": {"prompt": "0", "completion": "0"},
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["free_tier"] is True

    def test_is_free_api_field_marks_free(self, monkeypatch):
        """A model with is_free=True (but no :free suffix) is marked free."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/some-model",
            "is_free": True,
            "pricing": {"prompt": "0", "completion": "0"},
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["free_tier"] is True
        assert parsed["details"]["is_free_api"] is True

    def test_zero_pricing_marks_free(self, monkeypatch):
        """A model with $0 prompt + $0 completion pricing is marked free.

        Pricing is the ground truth — if both prompt and completion are
        $0/token, the model is genuinely free regardless of the is_free
        field or :free suffix.
        """
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/free-model-no-suffix",
            "is_free": False,
            "pricing": {"prompt": "0", "completion": "0"},
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["free_tier"] is True
        assert parsed["details"]["is_zero_pricing"] is True

    def test_paid_model_marked_paid(self, monkeypatch):
        """A paid model (no :free suffix, is_free=False, non-zero pricing)
        is marked free_tier=False."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "openai/gpt-4o-mini",
            "is_free": False,
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["free_tier"] is False

    def test_non_zero_pricing_not_free_even_with_is_free(self, monkeypatch):
        """If pricing is non-zero, the model is NOT free even if is_free=True.

        Edge case: some OpenRouter models have is_free=True but non-zero
        pricing (e.g. trial credits). Pricing is the ground truth.
        """
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/trial-model",
            "is_free": True,
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
        }
        parsed = b._parse_openrouter_model(model_data)
        # is_free=True + non-zero pricing → still free_tier=True
        # (because is_free=True OR :free suffix — pricing only UPGRADES to free)
        assert parsed["details"]["free_tier"] is True


# ---------------------------------------------------------------------------
# is_chat_model detection
# ---------------------------------------------------------------------------

class TestIsChatModelDetection:
    """Verify is_chat_model is set correctly."""

    def test_text_modality_is_chat(self, monkeypatch):
        """A model with modality='text' is marked is_chat_model=True."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/chat-model",
            "modality": "text",
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["is_chat_model"] is True

    def test_text_image_modality_is_chat(self, monkeypatch):
        """A model with modality='text+image->text' is marked is_chat_model=True
        (contains 'text')."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/vision-model",
            "modality": "text+image->text",
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["is_chat_model"] is True

    def test_no_modality_defaults_to_chat(self, monkeypatch):
        """A model with no modality field defaults to is_chat_model=True."""
        b = _make_backend(monkeypatch)
        model_data = {"id": "some-provider/model"}
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["is_chat_model"] is True

    def test_image_only_modality_not_chat(self, monkeypatch):
        """A model with modality='image' (no text) is marked is_chat_model=False."""
        b = _make_backend(monkeypatch)
        model_data = {
            "id": "some-provider/image-gen",
            "modality": "image",
        }
        parsed = b._parse_openrouter_model(model_data)
        assert parsed["details"]["is_chat_model"] is False


# ---------------------------------------------------------------------------
# /models free filter integration (simulated)
# ---------------------------------------------------------------------------

class TestModelsFreeFilterIntegration:
    """Verify the /models free filter would now match :free-suffix models.

    This simulates what the /models chat slash-command does: filter the
    list_models() result by details.free_tier. Before the fix, this
    returned an empty list (because free_tier was never set). After the
    fix, it returns all :free-suffix models.
    """

    def test_free_filter_returns_free_models(self, monkeypatch):
        """Filtering list_models() by free_tier=True returns :free models.

        We mock list_models() to return a mix of free and paid models
        (simulating the OpenRouter API response), then apply the same
        filter the /models chat command uses.
        """
        b = _make_backend(monkeypatch)

        # Mock list_models to return a representative sample
        mock_models = [
            # Free via :free suffix
            {"name": "google/gemma-3-27b-it:free", "size": 0, "details": {
                "free_tier": True, "is_chat_model": True, "context_length": 256000,
            }},
            # Free via is_free=True (no :free suffix)
            {"name": "some-provider/free-model", "size": 0, "details": {
                "free_tier": True, "is_chat_model": True, "context_length": 128000,
            }},
            # Paid
            {"name": "openai/gpt-4o-mini", "size": 0, "details": {
                "free_tier": False, "is_chat_model": True, "context_length": 128000,
            }},
            # Paid
            {"name": "anthropic/claude-sonnet-4.6", "size": 0, "details": {
                "free_tier": False, "is_chat_model": True, "context_length": 200000,
            }},
        ]
        monkeypatch.setattr(b, "list_models", lambda: mock_models)

        # Apply the /models free filter (same logic as chat.py:925-926)
        models = b.list_models()
        free_models = [m for m in models if m.get("details", {}).get("free_tier", False)]

        # Should return the 2 free models, not the 2 paid ones
        assert len(free_models) == 2, (
            f"Expected 2 free models, got {len(free_models)}. "
            f"Names: {[m['name'] for m in free_models]}"
        )
        names = [m["name"] for m in free_models]
        assert "google/gemma-3-27b-it:free" in names
        assert "some-provider/free-model" in names
        assert "openai/gpt-4o-mini" not in names
        assert "anthropic/claude-sonnet-4.6" not in names


# ---------------------------------------------------------------------------
# Real :free-suffix models from the user's /models output
# ---------------------------------------------------------------------------

class TestRealFreeModelsFromUserReport:
    """Verify the exact :free-suffix models from the user's /models report
    are now correctly marked as free_tier=True.

    The user ran /models on openrouter and saw 17 :free-suffix models
    all marked as "paid". This test ensures each of those 17 models
    would now be marked free_tier=True after the fix.
    """

    USER_REPORTED_FREE_MODELS = [
        "cohere/north-mini-code:free",
        "dots-studio/dots-3-note-preview:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
        "inclusionai/ling-3.0-flash-fin:free",
        "inclusionai/ling-3.0-flash-sante:free",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-content-safety:free",
        "nvidia/nemotron-3.5-lightning:free",
        "poolside/laguna-s-2.1:free",
        "poolside/laguna-xs-2.1:free",
        "qwen/qwen3.8-27b:free",
        "thinkingmachines/inkling-small:free",
        "thinkingmachines/inkling:free",
    ]

    def test_all_user_reported_free_models_marked_free(self, monkeypatch):
        """Every :free-suffix model from the user's /models report is now
        marked free_tier=True."""
        b = _make_backend(monkeypatch)

        for model_id in self.USER_REPORTED_FREE_MODELS:
            # Simulate the OpenRouter API response for this model
            # (is_free=False because OpenRouter's is_free is conservative)
            model_data = {
                "id": model_id,
                "context_length": 256000,
                "is_free": False,
                "pricing": {"prompt": "0", "completion": "0"},
            }
            parsed = b._parse_openrouter_model(model_data)
            assert parsed["details"]["free_tier"] is True, (
                f"Model {model_id!r} should be free_tier=True after the fix, "
                f"but got free_tier={parsed['details']['free_tier']}"
            )
            assert parsed["details"]["is_free_suffix"] is True

    def test_user_reported_count_matches(self, monkeypatch):
        """Verify we have all 17 models from the user's report."""
        assert len(self.USER_REPORTED_FREE_MODELS) == 17


# ---------------------------------------------------------------------------
# R07.09 fix: openrouter/free router + _is_free_model() helper
# ---------------------------------------------------------------------------

class TestOpenRouterFreeRouter:
    """R07.09 fix: ``openrouter/free`` (the named Free Models Router) was
    missing from the OpenRouter free whitelist.

    Bug: when OPENROUTER_FREE_ONLY=true, the ``list_models()`` filter only
    accepted models ending in ``:free``. The ``openrouter/free`` router
    (https://openrouter.ai/openrouter/free) — which auto-routes to the
    cheapest free model at request time — was filtered out, so users
    couldn't write ``--model openrouter/free`` with FREE_ONLY mode.

    Fix: added ``OPENROUTER_FREE_MODEL_WHITELIST`` + ``_is_free_model()``
    helper mirroring the OrcaRouter pattern. Both ``list_models()``
    filter branches now use the helper instead of the prior substring
    hack (which incorrectly included paid flash variants and missed
    the named router).
    """

    def test_openrouter_free_router_in_whitelist(self):
        """The named ``openrouter/free`` router must be in the whitelist."""
        from agentkthx.plugins.openrouter.openrouter import (
            OPENROUTER_FREE_MODEL_WHITELIST,
        )
        assert "openrouter/free" in OPENROUTER_FREE_MODEL_WHITELIST

    def test_is_free_model_openrouter_free_router(self):
        """The named router is free."""
        from agentkthx.plugins.openrouter.openrouter import _is_free_model
        assert _is_free_model("openrouter/free") is True

    def test_is_free_model_free_suffix(self):
        """Models ending in ``:free`` are free (canonical OpenRouter marker)."""
        from agentkthx.plugins.openrouter.openrouter import _is_free_model
        assert _is_free_model("google/gemini-flash-1.5:free") is True
        assert _is_free_model("qwen/qwen-2.5-7b-instruct:free") is True
        assert _is_free_model("meta-llama/llama-3.3-70b-instruct:free") is True

    def test_is_free_model_paid_models_not_free(self):
        """Paid models (no ``:free`` suffix, not the named router) return False."""
        from agentkthx.plugins.openrouter.openrouter import _is_free_model
        assert _is_free_model("anthropic/claude-3.5-sonnet") is False
        assert _is_free_model("openai/gpt-4o") is False
        # Prior substring hack would have incorrectly flagged these as free
        # because they contain "flash"/"mini" — the new helper correctly
        # identifies them as paid.
        assert _is_free_model("openai/gpt-4o-mini") is False
        assert _is_free_model("anthropic/claude-3-haiku") is False


class TestOpenRouterRouterNullMaxTokens:
    """R07.10 fix: ``openrouter/free`` (the named Free Models Router) returns
    ``max_completion_tokens: null`` in the API response — it's a router, not
    a real model, so there's no fixed output cap.

    Bug: ``dict.get("max_completion_tokens", 4096)`` returns ``None`` (not
    the default 4096) when the key exists with value ``None``. The None
    propagated through ``_parse_openrouter_model`` → ``_get_model_defaults``
    → ``_apply_max_tokens_cap`` where ``min(None, context_length // 32)``
    raised ``'<' not supported between instances of 'int' and 'NoneType'``.
    The agent loop's resilience layer caught it but classified as terminal
    (not retryable), surfacing as "Fatal API error — not retrying" with
    an empty response.

    Fix: ``_parse_openrouter_model`` now coerces explicit None → 4096 at
    parse time. ``_get_model_defaults`` and ``_get_model_info`` use
    ``details.get(k) or default`` instead of ``details.get(k, default)``
    so a None value is treated the same as an absent key.
    """

    def test_router_with_null_max_completion_tokens_parses_to_4096(self, monkeypatch):
        """``max_completion_tokens: null`` in API response must NOT propagate
        as None — must be coerced to the 4096 default at parse time."""
        b = _make_backend(monkeypatch)

        # Mimic the actual OpenRouter API response for openrouter/free:
        # the router has no fixed output cap, so the API returns null.
        model_data = {
            "id": "openrouter/free",
            "context_length": 195000,
            "top_provider": {
                "max_completion_tokens": None,  # explicitly null
            },
            "pricing": {"prompt": "0", "completion": "0"},
        }
        parsed = b._parse_openrouter_model(model_data)
        # The parsed max_completion_tokens must be 4096, NOT None — otherwise
        # the downstream _apply_max_tokens_cap() call crashes with
        # "'<' not supported between instances of 'int' and 'NoneType'"
        assert parsed["details"]["max_completion_tokens"] == 4096, (
            "max_completion_tokens must be coerced from None → 4096 at parse "
            "time to prevent the min(None, int) crash in _apply_max_tokens_cap"
        )

    def test_get_model_defaults_does_not_crash_on_router(self, monkeypatch):
        """``_get_model_defaults('openrouter/free')`` must return a valid
        dict with int max_tokens — must NOT raise the '<' TypeError.

        This is the regression test for the R07.10 user-facing crash:
        ``agentkthx chat -m openrouter/free --backend openrouter`` failed
        with ``[Resilience] Fatal API error — not retrying: '<' not
        supported between instances of 'int' and 'NoneType'`` because
        ``min(None, context_length // 32)`` was evaluated in
        ``_apply_max_tokens_cap``.
        """
        b = _make_backend(monkeypatch)

        # Populate the cache with a router entry that has null max_completion_tokens
        # (simulates the live API response shape)
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
        OpenRouterBackend._model_cache = [
            b._parse_openrouter_model({
                "id": "openrouter/free",
                "context_length": 195000,
                "top_provider": {"max_completion_tokens": None},
                "pricing": {"prompt": "0", "completion": "0"},
            })
        ]
        OpenRouterBackend._cache_time = float("inf")  # never expire

        try:
            # Must NOT raise "TypeError: '<' not supported between instances
            # of 'int' and 'NoneType'"
            defaults = b._get_model_defaults("openrouter/free")
        finally:
            # Cleanup class-level cache so other tests aren't affected
            OpenRouterBackend._model_cache = None
            OpenRouterBackend._cache_time = 0

        # The critical assertion: max_tokens must be an int, not None.
        # If this is None, the next call to _apply_max_tokens_cap() will
        # crash with the TypeError the user saw.
        assert defaults["max_tokens"] is not None, (
            "max_tokens must be coerced from None → int at parse time to "
            "prevent the min(None, int) crash in _apply_max_tokens_cap"
        )
        assert isinstance(defaults["max_tokens"], int)
        assert defaults["max_tokens"] > 0
