"""
OrcaRouter plugin (R07.05) — backend regression tests.

Verifies that the OrcaRouterBackend (the 6th cloud backend, the first
scaffolded from scratch on top of the new CloudBackend base class from
MAINT-02) correctly implements:

  - CloudBackend inheritance (issubclass checks)
  - Class-attribute provider identity
  - __init__ base-URL resolution + API-key validation
  - ``_get_chat_completions_url()`` returns the OrcaRouter endpoint
  - ``_get_auth_headers()`` includes Bearer + X-OrcaRouter-Include-Cost
  - ``_validate_api_key`` warns (not errors) on non-sk-orca- prefix
  - ``_extra_auth_headers()`` includes cost header by default
  - ``ORCAROUTER_FREE_MODEL_WHITELIST`` covers the 4 genuinely-free models
  - ``_is_free_model()`` classifies free vs paid vs named routers correctly
  - ``_is_free_rate_limited()`` detects free-tier-specific error reasons
  - ``_parse_retry_after_seconds()`` extracts Retry-After header value
  - ``generate()`` applies FREE_ONLY gate (raises on paid model)
  - ``generate_stream()`` applies FREE_ONLY gate
  - ``generate()`` injects fallback chain via extra_body
  - Plugin manifest loads via PluginManager (orcarouter + orca aliases)
  - Plugin manifest shape conforms to plugin spec v0.2
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType, ToolSupportLevel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_KEY = "sk-orca-test-key-1234567890"


@pytest.fixture
def orca_env(monkeypatch):
    """Set up env vars for OrcaRouter tests."""
    monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
    monkeypatch.setenv("ORCAROUTER_FREE_ONLY", "false")
    monkeypatch.setenv("ORCAROUTER_FALLBACK_MODELS", "")
    monkeypatch.setenv("ORCAROUTER_INCLUDE_COST", "true")
    # Clear the cached module-level booleans by re-importing
    # (config.py reads env vars at import time — for tests that need
    # to flip ORCAROUTER_FREE_ONLY, we monkeypatch the module attr)
    from agentkthx import config as _config
    monkeypatch.setattr(_config, "ORCAROUTER_FREE_ONLY", False, raising=False)
    monkeypatch.setattr(_config, "ORCAROUTER_FALLBACK_MODELS", "", raising=False)
    monkeypatch.setattr(_config, "ORCAROUTER_INCLUDE_COST", True, raising=False)
    return _config


@pytest.fixture
def backend(orca_env):
    """An OrcaRouterBackend instance with a test API key configured."""
    from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
    return OrcaRouterBackend()


# ---------------------------------------------------------------------------
# Inheritance and class structure
# ---------------------------------------------------------------------------

class TestOrcaRouterInheritance:
    """Verify OrcaRouterBackend's inheritance hierarchy."""

    def test_inherits_from_cloud_backend(self):
        """OrcaRouterBackend MUST inherit from CloudBackend (MAINT-02 pattern)."""
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        assert issubclass(OrcaRouterBackend, CloudBackend)

    def test_inherits_from_openai_compatible_backend(self):
        """Transitively inherits from OpenAICompatibleBackend (existing
        isinstance checks continue to work)."""
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        assert issubclass(OrcaRouterBackend, OpenAICompatibleBackend)

    def test_class_attributes_set(self):
        """CloudBackend class attributes are correctly overridden."""
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        assert OrcaRouterBackend._api_key_env_var == "ORCAROUTER_API_KEY"
        assert OrcaRouterBackend._provider_label == "OrcaRouter"
        assert OrcaRouterBackend._default_model == "orcarouter/auto"


# ---------------------------------------------------------------------------
# __init__ — base URL and API key
# ---------------------------------------------------------------------------

class TestOrcaRouterInit:
    """Verify __init__ resolves base URL and validates API key."""

    def test_default_base_url(self, orca_env):
        """Default base URL is https://api.orcarouter.ai/v1."""
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()
        assert b.base_url == "https://api.orcarouter.ai/v1"

    def test_explicit_base_url_overrides(self, orca_env):
        """Explicit base_url arg overrides the default."""
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend(base_url="https://staging.orcarouter.ai/v1")
        assert b.base_url == "https://staging.orcarouter.ai/v1"

    def test_missing_api_key_raises(self, monkeypatch):
        """Missing API key raises ValueError with a helpful message."""
        monkeypatch.delenv("ORCAROUTER_API_KEY", raising=False)
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        with pytest.raises(ValueError, match=r"ORCAROUTER_API_KEY is required"):
            OrcaRouterBackend()

    def test_too_short_api_key_raises(self, monkeypatch):
        """API key < 8 chars raises ValueError."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "short")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        with pytest.raises(ValueError, match=r"appears invalid"):
            OrcaRouterBackend()

    def test_explicit_api_key_wins(self, monkeypatch):
        """Explicit api_key arg takes priority over env var."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "env-key-1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend(api_key="explicit-orca-key-1234567890")
        assert b.api_key == "explicit-orca-key-1234567890"

    def test_non_sk_orca_prefix_warns_not_errors(self, monkeypatch, capsys):
        """API key without sk-orca- prefix emits a debug warning but doesn't raise."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-openai-1234567890")  # wrong prefix
        monkeypatch.setenv("AGENTKTHX_DEBUG", "1")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        # Should NOT raise — just warn in debug mode
        b = OrcaRouterBackend()
        assert b.api_key == "sk-openai-1234567890"
        captured = capsys.readouterr()
        assert "sk-orca-" in captured.out

    def test_default_api_mode_openai(self, backend):
        """Default API mode is OPENAI (cloud backends reject OPENRE)."""
        from agentkthx.core.types import ApiMode
        assert backend.api_mode == ApiMode.OPENAI

    def test_is_running_true_with_key(self, backend):
        """A backend with an API key reports is_running=True."""
        assert backend.is_running() is True


# ---------------------------------------------------------------------------
# URL and auth headers
# ---------------------------------------------------------------------------

class TestOrcaRouterUrlAndAuth:
    """Verify URL construction and auth headers."""

    def test_chat_completions_url(self, backend):
        """_get_chat_completions_url returns {base}/chat/completions."""
        assert backend._get_chat_completions_url() == \
            "https://api.orcarouter.ai/v1/chat/completions"

    def test_auth_headers_contain_bearer(self, backend):
        """Auth headers include Authorization: Bearer <key>."""
        headers = backend._get_auth_headers()
        assert headers["Authorization"] == f"Bearer {VALID_KEY}"

    def test_auth_headers_contain_content_type(self, backend):
        """Auth headers include Content-Type: application/json."""
        headers = backend._get_auth_headers()
        assert headers["Content-Type"] == "application/json"

    def test_auth_headers_include_cost_by_default(self, backend):
        """X-OrcaRouter-Include-Cost: true is added by default
        (ORCAROUTER_INCLUDE_COST defaults to true)."""
        headers = backend._get_auth_headers()
        assert headers.get("X-OrcaRouter-Include-Cost") == "true"

    def test_cost_header_suppressed_when_disabled(self, monkeypatch):
        """X-OrcaRouter-Include-Cost is NOT added when ORCAROUTER_INCLUDE_COST=false."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_INCLUDE_COST", "false")
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_INCLUDE_COST", False, raising=False)
        # Also patch the orcarouter module's local reference (it imports
        # ORCAROUTER_INCLUDE_COST from config at module-load time, so we
        # need to update both)
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_INCLUDE_COST", False, raising=False)
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()
        headers = b._get_auth_headers()
        assert "X-OrcaRouter-Include-Cost" not in headers


# ---------------------------------------------------------------------------
# Free-tier whitelist and free-model detection
# ---------------------------------------------------------------------------

class TestOrcaRouterFreeWhitelist:
    """Verify the free-tier whitelist and _is_free_model classification."""

    def test_whitelist_contains_4_genuinely_free_models(self):
        """The whitelist contains exactly the 4 documented free-tier models."""
        from agentkthx.plugins.orcarouter.orcarouter import ORCAROUTER_FREE_MODEL_WHITELIST
        assert "deepseek/deepseek-v4-flash-free" in ORCAROUTER_FREE_MODEL_WHITELIST
        assert "orca/orcaverify-text1.0-free" in ORCAROUTER_FREE_MODEL_WHITELIST
        assert "tencent/hy3-free" in ORCAROUTER_FREE_MODEL_WHITELIST
        assert "z-ai/glm-5.3-flash-free" in ORCAROUTER_FREE_MODEL_WHITELIST
        # Plus the free router itself
        assert "orcarouter/free" in ORCAROUTER_FREE_MODEL_WHITELIST
        # 4 free models + 1 free router = 5 entries
        assert len(ORCAROUTER_FREE_MODEL_WHITELIST) == 5

    def test_is_free_model_for_free_aliases(self):
        """The 4 documented free-tier models are classified as free."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model
        assert _is_free_model("deepseek/deepseek-v4-flash-free") is True
        assert _is_free_model("orca/orcaverify-text1.0-free") is True
        assert _is_free_model("tencent/hy3-free") is True
        assert _is_free_model("z-ai/glm-5.3-flash-free") is True

    def test_is_free_model_for_free_router(self):
        """orcarouter/free (the named router) is classified as free
        (it never escapes to paid capacity)."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model
        assert _is_free_model("orcarouter/free") is True

    def test_is_free_model_for_auto_router_is_false(self):
        """orcarouter/auto is NOT free (picks cheapest, which may be paid)."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model
        assert _is_free_model("orcarouter/auto") is False

    def test_is_free_model_for_paid_models_is_false(self):
        """Paid models are classified as not free."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model
        assert _is_free_model("openai/gpt-4o-mini") is False
        assert _is_free_model("anthropic/claude-sonnet-4.6") is False
        assert _is_free_model("google/gemini-2.5-flash") is False

    def test_is_free_model_for_unknown_is_false(self):
        """Unknown models default to not-free (safer — don't assume free)."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model
        assert _is_free_model("nonexistent/model") is False


# ---------------------------------------------------------------------------
# Free-tier rate-limit detection
# ---------------------------------------------------------------------------

class TestOrcaRouterRateLimitDetection:
    """Verify _is_free_rate_limited detects the right error patterns."""

    def test_detects_err_free_rate_reason(self):
        """err_free_rate in metadata.reason triggers free-rate detection."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        err = '{"error":{"code":"free_rate_limited","metadata":{"reason":"err_free_rate"}}}'
        assert _is_free_rate_limited(err) is True

    def test_detects_err_free_access_denied_reason(self):
        """err_free_access_denied triggers free-rate detection."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        err = '{"error":{"metadata":{"reason":"err_free_access_denied"}}}'
        assert _is_free_rate_limited(err) is True

    def test_detects_free_quota_exhausted_code(self):
        """free_quota_exhausted error code triggers free-rate detection."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        err = '{"error":{"code":"free_quota_exhausted"}}'
        assert _is_free_rate_limited(err) is True

    def test_detects_free_rate_limited_code(self):
        """free_rate_limited error code triggers free-rate detection."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        err = '{"error":{"code":"free_rate_limited"}}'
        assert _is_free_rate_limited(err) is True

    def test_does_not_match_generic_429(self):
        """Generic 429 'rate limit exceeded' does NOT trigger free-rate
        detection (it's a paid 429 — uses exponential backoff)."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        assert _is_free_rate_limited("rate limit exceeded") is False
        assert _is_free_rate_limited("Too many requests") is False

    def test_does_not_match_other_errors(self):
        """Other errors don't trigger free-rate detection."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        assert _is_free_rate_limited("Internal server error") is False
        assert _is_free_rate_limited("Invalid API key") is False
        assert _is_free_rate_limited("") is False


# ---------------------------------------------------------------------------
# Free-tier error classification: retryable vs terminal (R07.05 follow-up)
# ---------------------------------------------------------------------------

class TestOrcaRouterFreeTierClassification:
    """Verify the retryable-vs-terminal free-tier error classification.

    OrcaRouter's free-tier errors come in two flavors:
      - Retryable (rate-limited): err_free_rate, free_rate_limited → wait + retry
      - Terminal (account-level): err_free_used, free_quota_exhausted,
        err_free_access_denied, err_free_prompt_cap → surface buy_credits_url + terminate

    The bug this prevents: if the user runs `agentkthx chat --backend orcarouter
    --model orcarouter/free` and gets a terminal err_free_used, the old code
    swapped to ORCAROUTER_FREE_FALLBACK_MODEL (also orcarouter/free) and retried
    3 times — wasting time and producing confusing "falling back to X" messages
    when the fallback IS X.
    """

    def test_err_free_rate_is_retryable(self):
        """err_free_rate (per-minute/per-day rate window full) is retryable."""
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"metadata":{"reason":"err_free_rate"}}}'
        assert _is_free_rate_retryable(err) is True
        assert _is_free_rate_terminal(err) is False

    def test_free_rate_limited_code_is_retryable(self):
        """error.code == free_rate_limited is retryable."""
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"code":"free_rate_limited"}}'
        assert _is_free_rate_retryable(err) is True
        assert _is_free_rate_terminal(err) is False

    def test_err_free_used_is_terminal(self):
        """err_free_used (allowance used up / account not eligible) is TERMINAL.

        This is the error from the user's live test — a brand-new API key
        whose workspace isn't eligible for the free tier (GitHub account
        not "established" per OrcaRouter's requirement).
        """
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"metadata":{"reason":"err_free_used"}}}'
        assert _is_free_rate_retryable(err) is False
        assert _is_free_rate_terminal(err) is True

    def test_free_quota_exhausted_is_terminal(self):
        """free_quota_exhausted (no free model available) is TERMINAL."""
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"code":"free_quota_exhausted"}}'
        assert _is_free_rate_retryable(err) is False
        assert _is_free_rate_terminal(err) is True

    def test_err_free_access_denied_is_terminal(self):
        """err_free_access_denied (GitHub not linked) is TERMINAL."""
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"metadata":{"reason":"err_free_access_denied"}}}'
        assert _is_free_rate_retryable(err) is False
        assert _is_free_rate_terminal(err) is True

    def test_err_free_prompt_cap_is_terminal(self):
        """err_free_prompt_cap (per-request prompt-token cap exceeded) is TERMINAL.

        Not retryable — the user must shorten the prompt.
        """
        from agentkthx.plugins.orcarouter.orcarouter import (
            _is_free_rate_retryable, _is_free_rate_terminal
        )
        err = '{"error":{"code":"err_free_prompt_cap"}}'
        assert _is_free_rate_retryable(err) is False
        assert _is_free_rate_terminal(err) is True

    def test_is_free_rate_limited_returns_true_for_both_classes(self):
        """_is_free_rate_limited (the original API) returns True for any
        free-tier error — both retryable and terminal. This preserves
        backward compat with the existing check in _iter_sse_lines."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_rate_limited
        # Retryable
        assert _is_free_rate_limited('{"reason":"err_free_rate"}') is True
        # Terminal
        assert _is_free_rate_limited('{"reason":"err_free_used"}') is True
        assert _is_free_rate_limited('{"code":"free_quota_exhausted"}') is True


class TestOrcaRouterBuyCreditsUrlExtraction:
    """Verify _extract_buy_credits_url pulls the billing URL from errors."""

    def test_extracts_url_from_err_free_used(self):
        """The buy_credits_url field is extracted from an err_free_used error."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        err = (
            '{"error":{"metadata":{'
            '"buy_credits_url":"https://www.orcarouter.ai/console/billing?ref=err_free_used#add-credits",'
            '"reason":"err_free_used"}}}'
        )
        url = _extract_buy_credits_url(err)
        assert url is not None
        assert "orcarouter.ai/console/billing" in url
        assert "err_free_used" in url

    def test_returns_none_when_no_url(self):
        """None is returned when the error has no buy_credits_url field."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        err = '{"error":{"message":"some other error"}}'
        assert _extract_buy_credits_url(err) is None

    def test_extracts_url_from_real_402_payload(self):
        """Verify extraction from the actual 402 payload seen in the live test."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        # This is the exact payload from the user's live test
        err = (
            '{"error":{"code":"free_quota_exhausted",'
            '"message":"your orcarouter/free allowance is used up",'
            '"metadata":{"base_model":"",'
            '"buy_credits_url":"https://www.orcarouter.ai/console/billing?ref=err_free_used#add-credits",'
            '"free_model":"orcarouter/free","reason":"err_free_used"},'
            '"type":"insufficient_quota"}}'
        )
        url = _extract_buy_credits_url(err)
        assert url == "https://www.orcarouter.ai/console/billing?ref=err_free_used#add-credits"


# ---------------------------------------------------------------------------
# Regression: terminal free-tier errors don't retry the same model
# ---------------------------------------------------------------------------

class TestOrcaRouterTerminalNoRetry:
    """Verify terminal free-tier errors raise immediately instead of retrying.

    The bug: before this fix, an err_free_used error on orcarouter/free
    would swap to ORCAROUTER_FREE_FALLBACK_MODEL (also orcarouter/free)
    and retry 3 times — producing 3 confusing "falling back to
    orcarouter/free" messages before finally surfacing the error.
    """

    def test_err_free_used_raises_immediately(self, monkeypatch):
        """A terminal err_free_used error raises RuntimeError immediately,
        without retrying the same model 3 times."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        # Disable fallback chain (don't want it interfering)
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FALLBACK_MODELS", "", raising=False)

        b = OrcaRouterBackend()

        # Mock urllib.request.urlopen to raise an HTTPError with err_free_used
        import urllib.error
        import urllib.request

        terminal_err_body = (
            '{"error":{"code":"free_quota_exhausted",'
            '"message":"your orcarouter/free allowance is used up",'
            '"metadata":{"buy_credits_url":"https://www.orcarouter.ai/console/billing#add-credits",'
            '"reason":"err_free_used"},"type":"insufficient_quota"}}'
        )

        call_count = {"chat_calls": 0, "models_calls": 0}

        class _FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                # Minimal HTTPError init — we only need .code, .fp, .headers.
                # .fp must be truthy so the production code path
                # ``error_body = e.read().decode("utf-8") if e.fp else ""``
                # actually reads the body. We set .fp to a sentinel object
                # and patch .read() to return the error body.
                self.code = 402
                self.fp = True  # truthy sentinel — triggers the read() path
                self.headers = {}

        def _fake_urlopen(req, timeout=None):
            # Count chat-completions calls separately from /v1/models calls.
            # The /v1/models call happens once during _get_model_defaults
            # (to populate the model cache) — that's expected and not a retry.
            url = str(req.full_url) if hasattr(req, "full_url") else str(req)
            if "/chat/completions" in url:
                call_count["chat_calls"] += 1
            elif "/models" in url:
                call_count["models_calls"] += 1
            err = _FakeHTTPError()
            # Patch .read() to return the error body as bytes
            err.read = lambda: terminal_err_body.encode("utf-8")
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        # The generate() call should raise RuntimeError with the buy_credits_url
        with pytest.raises(RuntimeError) as exc_info:
            b.generate(model="orcarouter/free",
                       messages=[{"role": "user", "content": "hi"}])

        # Verify the error message surfaces the remedy + buy_credits_url
        err_msg = str(exc_info.value)
        assert "free-tier access denied" in err_msg.lower(), (
            f"Error message should mention free-tier access denied, got: {err_msg!r}"
        )
        assert "orcarouter.ai/console/billing" in err_msg
        assert "err_free_used" in err_msg or "free_quota_exhausted" in err_msg

        # CRITICAL: the /chat/completions endpoint should have been called
        # exactly ONCE — not retried 3 times like the old code did.
        # (The /v1/models call is a separate concern — it populates the
        # model cache during _get_model_defaults and is not a retry.)
        assert call_count["chat_calls"] == 1, (
            f"Terminal free-tier error should NOT retry the /chat/completions "
            f"endpoint — expected 1 call, got {call_count['chat_calls']}. "
            f"The old behavior would have made 4 calls (1 original + 3 retries "
            f"of the same model)."
        )

    def test_err_free_used_error_message_mentions_20_threshold(self, monkeypatch):
        """The terminal error message surfaces the $20 lifetime-purchase
        threshold and the 50→800 daily-cap lift — so the user knows what
        they're buying when they add credits.

        Per OrcaRouter's free-tier access requirements: any paid purchase
        lifts the always-free access gate, and $20+ in lifetime purchases
        raises the workspace's daily request cap from 50 to 800.
        """
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FALLBACK_MODELS", "", raising=False)

        b = OrcaRouterBackend()

        import urllib.error
        import urllib.request

        terminal_err_body = (
            '{"error":{"code":"free_quota_exhausted",'
            '"message":"your orcarouter/free allowance is used up",'
            '"metadata":{"buy_credits_url":"https://www.orcarouter.ai/console/billing?ref=err_free_used#add-credits",'
            '"reason":"err_free_used"},"type":"insufficient_quota"}}'
        )

        class _FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                self.code = 402
                self.fp = True
                self.headers = {}

        def _fake_urlopen(req, timeout=None):
            err = _FakeHTTPError()
            err.read = lambda: terminal_err_body.encode("utf-8")
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

        with pytest.raises(RuntimeError) as exc_info:
            b.generate(model="orcarouter/free",
                       messages=[{"role": "user", "content": "hi"}])

        err_msg = str(exc_info.value).lower()

        # Verify the remedy mentions the $20 threshold + the 50→800 daily cap lift
        assert "$20" in err_msg, (
            f"Error message should mention the $20 lifetime-purchase threshold, "
            f"got: {exc_info.value}"
        )
        assert "800" in err_msg, (
            f"Error message should mention the 800 daily-cap lift, got: {exc_info.value}"
        )
        # Verify both remedy paths (add credits OR link GitHub) are surfaced
        assert "github" in err_msg, (
            f"Error message should mention the GitHub-account alternative, "
            f"got: {exc_info.value}"
        )
        # Verify the actual buy_credits_url from the API response is preserved
        assert "orcarouter.ai/console/billing?ref=err_free_used#add-credits" in err_msg


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------

class TestOrcaRouterRetryAfterParsing:
    """Verify _parse_retry_after_seconds extracts the wait time."""

    def test_parses_numeric_value(self):
        """A numeric Retry-After header value is parsed as float seconds."""
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds
        assert _parse_retry_after_seconds("error", "30") == 30.0
        assert _parse_retry_after_seconds("error", "0.5") == 0.5

    def test_returns_none_when_no_header(self):
        """None is returned when there's no Retry-After header."""
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds
        assert _parse_retry_after_seconds("error", None) is None

    def test_returns_none_for_invalid_value(self):
        """None is returned for non-numeric Retry-After values."""
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds
        assert _parse_retry_after_seconds("error", "invalid") is None
        assert _parse_retry_after_seconds("error", "") is None


# ---------------------------------------------------------------------------
# generate() — FREE_ONLY gate
# ---------------------------------------------------------------------------

class TestOrcaRouterGenerateFreeOnlyGate:
    """Verify generate() and generate_stream() apply the FREE_ONLY gate."""

    def test_generate_rejects_paid_model_when_free_only(self, monkeypatch):
        """generate() raises ValueError when FREE_ONLY=true and model is paid."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_FREE_ONLY", "true")
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_FREE_ONLY", True, raising=False)
        # Patch the orcarouter module's imported reference too
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FREE_ONLY", True, raising=False)
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend

        b = OrcaRouterBackend()
        with pytest.raises(ValueError, match=r"ORCAROUTER_FREE_ONLY=true"):
            b.generate(
                model="openai/gpt-4o-mini",  # paid
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_generate_accepts_free_model_when_free_only(self, monkeypatch):
        """generate() proceeds (does not raise FREE_ONLY error) when model is free."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_FREE_ONLY", "true")
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_FREE_ONLY", True, raising=False)
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FREE_ONLY", True, raising=False)
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend

        b = OrcaRouterBackend()
        # Use a free model — should pass the gate (will fail later on network,
        # but the FREE_ONLY check itself should not raise)
        try:
            b.generate(
                model="deepseek/deepseek-v4-flash-free",
                messages=[{"role": "user", "content": "hi"}],
            )
        except ValueError as e:
            # If we get a ValueError, it must NOT be the FREE_ONLY error
            assert "ORCAROUTER_FREE_ONLY" not in str(e)
        except Exception:
            # Any other exception (network, HTTP, etc.) is fine — the test
            # only verifies the FREE_ONLY gate passed.
            pass

    def test_generate_stream_rejects_paid_model_when_free_only(self, monkeypatch):
        """generate_stream() raises ValueError when FREE_ONLY=true and model is paid."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_FREE_ONLY", "true")
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_FREE_ONLY", True, raising=False)
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FREE_ONLY", True, raising=False)
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend

        b = OrcaRouterBackend()
        with pytest.raises(ValueError, match=r"ORCAROUTER_FREE_ONLY=true"):
            # Consume the generator to trigger the validation
            list(b.generate_stream(
                model="openai/gpt-4o-mini",  # paid
                messages=[{"role": "user", "content": "hi"}],
            ))


# ---------------------------------------------------------------------------
# generate() — fallback chain injection
# ---------------------------------------------------------------------------

class TestOrcaRouterFallbackChain:
    """Verify generate() injects the fallback chain via extra_body."""

    def test_fallback_chain_injected_when_env_set(self, monkeypatch):
        """When ORCAROUTER_FALLBACK_MODELS is set, extra_body.models is populated."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_FALLBACK_MODELS",
                           "openai/gpt-4o-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash")
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_FALLBACK_MODELS",
                            "openai/gpt-4o-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash",
                            raising=False)
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FALLBACK_MODELS",
                            "openai/gpt-4o-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash",
                            raising=False)

        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()

        # Capture the body by patching _generate_with_auth
        captured_body = {}

        def _capture(model, messages, **kwargs):
            captured_body.update(kwargs)
            return {"content": "", "tool_calls": [], "usage": {}}

        monkeypatch.setattr(b, "_generate_with_auth", _capture)
        b.generate(model="openai/gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])

        assert "extra_body" in captured_body
        assert captured_body["extra_body"]["route"] == "fallback"
        assert captured_body["extra_body"]["models"] == [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-flash",
        ]

    def test_fallback_chain_capped_at_5_models(self, monkeypatch):
        """OrcaRouter caps fallback chains at 5 models — extras are dropped."""
        six_models = ",".join([
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-flash",
            "deepseek/deepseek-chat",
            "grok/grok-4-fast-reasoning",
            "qwen/qwen3-max",  # this one should be dropped
        ])
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        monkeypatch.setenv("ORCAROUTER_FALLBACK_MODELS", six_models)
        from agentkthx import config as _config
        monkeypatch.setattr(_config, "ORCAROUTER_FALLBACK_MODELS", six_models, raising=False)
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod
        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FALLBACK_MODELS", six_models, raising=False)

        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()

        captured_body = {}

        def _capture(model, messages, **kwargs):
            captured_body.update(kwargs)
            return {"content": "", "tool_calls": [], "usage": {}}

        monkeypatch.setattr(b, "_generate_with_auth", _capture)
        b.generate(model="openai/gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])

        assert len(captured_body["extra_body"]["models"]) == 5
        assert "qwen/qwen3-max" not in captured_body["extra_body"]["models"]

    def test_no_fallback_chain_when_env_empty(self, backend):
        """When ORCAROUTER_FALLBACK_MODELS is empty, extra_body is not set."""
        # Default fixture has empty ORCAROUTER_FALLBACK_MODELS
        captured_body = {}

        original = backend._generate_with_auth

        def _capture(model, messages, **kwargs):
            captured_body.update(kwargs)
            return {"content": "", "tool_calls": [], "usage": {}}

        backend._generate_with_auth = _capture
        try:
            backend.generate(model="openai/gpt-4o-mini",
                             messages=[{"role": "user", "content": "hi"}])
        finally:
            backend._generate_with_auth = original

        # extra_body should not be present (or empty) when no fallback configured
        assert not captured_body.get("extra_body")


# ---------------------------------------------------------------------------
# Tool support
# ---------------------------------------------------------------------------

class TestOrcaRouterToolSupport:
    """Verify test_tool_support returns NATIVE for chat models."""

    def test_returns_native_for_chat_model(self, backend):
        """OrcaRouter translates OpenAI tools to upstream shapes, so all
        chat-capable models report NATIVE tool support."""
        result = backend.test_tool_support("openai/gpt-4o-mini")
        assert result == ToolSupportLevel.NATIVE

    def test_returns_native_for_anthropic_model(self, backend):
        """Anthropic models get OpenAI tools → input_schema translation."""
        result = backend.test_tool_support("anthropic/claude-sonnet-4.6")
        assert result == ToolSupportLevel.NATIVE

    def test_returns_native_for_gemini_model(self, backend):
        """Gemini models get OpenAI tools → functionDeclarations translation."""
        result = backend.test_tool_support("google/gemini-2.5-flash")
        assert result == ToolSupportLevel.NATIVE


# ---------------------------------------------------------------------------
# Backend type
# ---------------------------------------------------------------------------

class TestOrcaRouterBackendType:
    """Verify backend_type returns BackendType.ORCAROUTER (R07.05).

    The dedicated enum value was added to core/types.py alongside the
    plugin itself — without it, the footer would show ``🔌 zai`` even
    when ``--backend orcarouter`` is used.
    """

    def test_backend_type_returns_orcarouter(self, backend):
        """backend_type returns BackendType.ORCAROUTER (not ZAI placeholder)."""
        assert backend.backend_type == BackendType.ORCAROUTER

    def test_backend_type_value_is_orcarouter_string(self, backend):
        """backend_type.value is the lowercase string 'orcarouter' (used by
        the CLI footer formatter to display the backend name)."""
        assert backend.backend_type.value == "orcarouter"


# ---------------------------------------------------------------------------
# Plugin manifest and PluginManager integration
# ---------------------------------------------------------------------------

class TestOrcaRouterPluginManifest:
    """Verify the plugin.json manifest loads and conforms to plugin spec v0.2."""

    def test_plugin_json_exists(self):
        """plugin.json exists at the expected path."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        assert path.exists(), f"plugin.json not found at {path}"

    def test_plugin_json_valid_json(self):
        """plugin.json is valid JSON."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_plugin_json_has_required_fields(self):
        """plugin.json has the required top-level fields per plugin spec v0.2."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        assert data["name"] == "orcarouter"
        assert data["version"] == "0.1.0"
        assert data["license"] == "MIT"
        assert data["author"]["name"] == "VTSTech"

    def test_plugin_json_extensions_block(self):
        """plugin.json has the extensions.org.vts-tech.agentkthx block."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        ext = data["extensions"]["org.vts-tech.agentkthx"]
        assert ext["type"] == "backend"
        assert ext["entrypoint"] == "__init__"
        assert ext["display_name"] == "OrcaRouter Cloud Backend"

    def test_plugin_json_provides_backends(self):
        """plugin.json declares the orcarouter backend."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        ext = data["extensions"]["org.vts-tech.agentkthx"]
        assert "orcarouter" in ext["provides"]["backends"]
        assert ext["provides"]["backends"]["orcarouter"] == "orcarouter.OrcaRouterBackend"

    def test_plugin_json_cli_aliases(self):
        """plugin.json registers both 'orcarouter' and 'orca' as --backend aliases."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        ext = data["extensions"]["org.vts-tech.agentkthx"]
        backend_aliases = ext["provides"]["cli_flags"]["--backend"]
        assert "orcarouter" in backend_aliases
        assert "orca" in backend_aliases

    def test_plugin_json_config_defaults(self):
        """plugin.json declares all 7 ORCAROUTER_* env vars with defaults."""
        path = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter" / "plugin.json"
        data = json.loads(path.read_text())
        ext = data["extensions"]["org.vts-tech.agentkthx"]
        defaults = ext["config"]["defaults"]
        assert defaults["ORCAROUTER_BASE_URL"] == "https://api.orcarouter.ai/v1"
        assert defaults["ORCAROUTER_API_KEY"] == ""
        assert defaults["ORCAROUTER_DEFAULT_MODEL"] == "orcarouter/auto"
        assert defaults["ORCAROUTER_FREE_ONLY"] == "false"
        assert defaults["ORCAROUTER_FREE_FALLBACK_MODEL"] == "orcarouter/free"
        assert defaults["ORCAROUTER_FALLBACK_MODELS"] == ""
        assert defaults["ORCAROUTER_INCLUDE_COST"] == "true"


class TestOrcaRouterPluginManagerIntegration:
    """Verify the plugin loads via PluginManager and registers its backend."""

    def test_plugin_loads_via_plugin_manager(self, monkeypatch):
        """PluginManager discovers and loads the orcarouter plugin."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        from agentkthx.plugins import get_plugin_manager
        pm = get_plugin_manager()
        pm.load_all()
        plugins = pm.list_plugins()
        assert "orcarouter" in plugins

    def test_backend_registered_as_orcarouter(self, monkeypatch):
        """The 'orcarouter' backend name is registered after plugin load."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        from agentkthx.plugins import get_plugin_manager
        pm = get_plugin_manager()
        pm.load_all()
        choices = pm.get_backend_choices()
        assert "orcarouter" in choices

    def test_backend_alias_orca_registered(self, monkeypatch):
        """The 'orca' alias is also registered as a backend choice."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        from agentkthx.plugins import get_plugin_manager
        pm = get_plugin_manager()
        pm.load_all()
        choices = pm.get_backend_choices()
        assert "orca" in choices

    def test_get_backend_orcarouter_returns_instance(self, monkeypatch):
        """get_backend('orcarouter') returns an OrcaRouterBackend instance."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        from agentkthx import get_backend
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = get_backend("orcarouter")
        assert isinstance(b, OrcaRouterBackend)

    def test_get_backend_orca_alias_returns_instance(self, monkeypatch):
        """get_backend('orca') returns an OrcaRouterBackend instance (alias)."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", VALID_KEY)
        from agentkthx import get_backend
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = get_backend("orca")
        assert isinstance(b, OrcaRouterBackend)


# ---------------------------------------------------------------------------
# BOM check (R07.01 MAINT-06 regression — ensures no BOM in plugin .py files)
# ---------------------------------------------------------------------------

class TestOrcaRouterNoBom:
    """Verify the plugin's Python files don't carry a UTF-8 BOM (MAINT-06)."""

    def test_no_bom_in_plugin_files(self):
        """No .py file under plugins/orcarouter/ starts with a UTF-8 BOM."""
        plugin_dir = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "orcarouter"
        for py_file in plugin_dir.glob("*.py"):
            with open(py_file, "rb") as f:
                first3 = f.read(3)
            assert first3 != b"\xef\xbb\xbf", f"BOM found at start of {py_file}"
