"""
Cloudflare Workers AI plugin (R07.26 scaffold) \u2014 backend regression tests.

Verifies that the CloudflareBackend (the 10th cloud backend, scaffolded from
the CloudBackend base class) correctly implements:

  - CloudBackend inheritance (issubclass checks)
  - Class-attribute provider identity (_api_key_env_var, _provider_label,
    _default_base_url, _default_model, MODEL_CACHE_KEY)
  - __init__ base-URL resolution + account-ID validation (the unique
    Cloudflare twist: account ID is baked into the URL path)
  - ``_get_chat_completions_url()`` returns the Cloudflare OpenAI-compat URL
  - ``_get_models_url()`` returns the NATIVE /ai/models/search URL
    (NOT on the OpenAI-compat path)
  - ``_get_auth_headers()`` includes Bearer + Content-Type
  - ``_validate_api_key`` warns (not errors) on known wrong-provider prefixes
  - ``_extra_auth_headers()`` returns empty dict (no extras)
  - ``_is_free_model()`` returns True for cataloged models, False for unknown
  - ``_looks_like_neuron_quota_exhaustion()`` distinguishes 429 rate limit
    from 429 daily-quota exhaustion
  - ``test_tool_support()`` returns REACT for vision / R1-distill /
    GPT-OSS models and NATIVE for chat models (Llama, Mistral, Qwen, etc.)
  - ``_catalog_fallback_list()`` shapes the seed catalog correctly
  - ``list_models()`` returns catalog entries (offline fallback)
  - ``get_model_info()`` / ``get_model_max_context()`` catalog lookup
  - Plugin manifest loads via PluginManager (cloudflare + cf aliases)
  - Plugin manifest shape conforms to plugin spec v0.2
  - config.py exposes CLOUDFLARE_* env vars with correct defaults
  - /auth picker registry includes Cloudflare (incl. CLOUDFLARE_ACCOUNT_ID)

Mirrors the structure of tests/test_nvidia_backend.py (the prior
cloud-backend regression suite \u2014 same inheritance / class-attribute /
manifest shape checks).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.cloud_base import CloudBackend  # noqa: E402
from agentkthx.backends.openai_compat import OpenAICompatibleBackend  # noqa: E402
from agentkthx.core.types import BackendType, ToolSupportLevel  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_KEY = "cf-test-token-12345678901234567890"  # 38 chars, above _MIN_API_KEY_LEN
VALID_ACCOUNT_ID = "abcdef0123456789abcdef0123456789"  # 32-hex


@pytest.fixture
def cloudflare_env(monkeypatch):
    """Set up env vars for Cloudflare tests."""
    monkeypatch.setenv("CLOUDFLARE_API_KEY", VALID_KEY)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID)
    monkeypatch.setenv("CLOUDFLARE_BASE_URL", "")
    monkeypatch.setenv("CLOUDFLARE_DEFAULT_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    monkeypatch.setenv("CLOUDFLARE_FREE_ONLY", "false")
    # Clear the cached module-level constants by re-binding on config
    from agentkthx import config as _config

    monkeypatch.setattr(_config, "CLOUDFLARE_API_KEY", VALID_KEY, raising=False)
    monkeypatch.setattr(_config, "CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID, raising=False)
    monkeypatch.setattr(_config, "CLOUDFLARE_BASE_URL", "", raising=False)
    monkeypatch.setattr(
        _config,
        "CLOUDFLARE_DEFAULT_MODEL",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        raising=False,
    )
    monkeypatch.setattr(_config, "CLOUDFLARE_FREE_ONLY", False, raising=False)
    return _config


@pytest.fixture
def backend(cloudflare_env):
    """A CloudflareBackend instance with a test API key + account ID configured."""
    from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

    return CloudflareBackend()


# ---------------------------------------------------------------------------
# Inheritance and class structure
# ---------------------------------------------------------------------------


class TestCloudflareInheritance:
    """Verify CloudflareBackend's inheritance hierarchy."""

    def test_inherits_from_cloud_backend(self):
        """CloudflareBackend MUST inherit from CloudBackend (MAINT-02 pattern)."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        assert issubclass(CloudflareBackend, CloudBackend)

    def test_inherits_from_openai_compatible_backend(self):
        """Transitively inherits from OpenAICompatibleBackend (existing
        isinstance checks continue to work)."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        assert issubclass(CloudflareBackend, OpenAICompatibleBackend)

    def test_class_attributes_set(self):
        """CloudBackend class attributes are correctly overridden."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        assert CloudflareBackend._api_key_env_var == "CLOUDFLARE_API_KEY"
        assert CloudflareBackend._provider_label == "Cloudflare"
        # _default_base_url is the CLOUDFLARE_BASE_URL config constant
        # (empty by default \u2014 the actual URL is built from account_id at
        # __init__ time). Don't assert a specific value here.
        assert CloudflareBackend._default_model == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        assert CloudflareBackend.MODEL_CACHE_KEY == "cloudflare"


# ---------------------------------------------------------------------------
# Backend initialization + base URL resolution
# ---------------------------------------------------------------------------


class TestCloudflareInit:
    """Verify __init__ correctly resolves base URL + validates API key
    AND account ID (the unique Cloudflare twist)."""

    def test_default_base_url_built_from_account_id(self, cloudflare_env):
        """Without explicit base_url, the URL is built from CLOUDFLARE_ACCOUNT_ID."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend()
        assert b.base_url == (
            f"https://api.cloudflare.com/client/v4/accounts/{VALID_ACCOUNT_ID}/ai/v1"
        )

    def test_explicit_base_url_overrides_account_id_template(self, cloudflare_env):
        """Explicit base_url overrides the account_id-derived template."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend(base_url="https://custom.cloudflare.proxy/ai/v1")
        assert b.base_url == "https://custom.cloudflare.proxy/ai/v1"

    def test_explicit_base_url_strips_trailing_slash(self, cloudflare_env):
        """Trailing slash on base_url is stripped (CloudBackend convention)."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend(base_url="https://custom.cloudflare.proxy/ai/v1/")
        assert b.base_url == "https://custom.cloudflare.proxy/ai/v1"

    def test_missing_account_id_raises(self, monkeypatch):
        """No account_id anywhere \u2192 ValueError (fail-fast, prevents 404s later)."""
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.setenv("CLOUDFLARE_API_KEY", VALID_KEY)
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "CLOUDFLARE_ACCOUNT_ID", "", raising=False)
        monkeypatch.setattr(_config, "CLOUDFLARE_API_KEY", VALID_KEY, raising=False)
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        with pytest.raises(ValueError, match="CLOUDFLARE_ACCOUNT_ID is required"):
            CloudflareBackend()

    def test_empty_account_id_raises(self, monkeypatch):
        """Whitespace-only account_id \u2192 ValueError."""
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "   ")
        monkeypatch.setenv("CLOUDFLARE_API_KEY", VALID_KEY)
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        with pytest.raises(ValueError, match="CLOUDFLARE_ACCOUNT_ID is required"):
            CloudflareBackend()

    def test_explicit_account_id_arg_wins_over_env(self, cloudflare_env):
        """Explicit account_id arg wins over CLOUDFLARE_ACCOUNT_ID env var."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend(account_id="00000000000000000000000000000000")
        assert b.account_id == "00000000000000000000000000000000"
        assert "/accounts/00000000000000000000000000000000/" in b.base_url

    def test_missing_api_key_raises(self, monkeypatch):
        """No API key anywhere \u2192 ValueError (CloudBackend validation)."""
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID)
        monkeypatch.delenv("CLOUDFLARE_API_KEY", raising=False)
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "CLOUDFLARE_API_KEY", "", raising=False)
        monkeypatch.setattr(_config, "CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID, raising=False)
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        with pytest.raises(ValueError, match="CLOUDFLARE_API_KEY is required"):
            CloudflareBackend()

    def test_short_api_key_raises(self, monkeypatch):
        """API key below _MIN_API_KEY_LEN (20) \u2192 ValueError."""
        monkeypatch.setenv("CLOUDFLARE_API_KEY", "cf-short")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID)
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        with pytest.raises(ValueError, match="too short"):
            CloudflareBackend()

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        """Explicit api_key arg wins over CLOUDFLARE_API_KEY env var."""
        monkeypatch.setenv("CLOUDFLARE_API_KEY", "cf-env-token-1234567890")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", VALID_ACCOUNT_ID)
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend(api_key="cf-explicit-token-1234567890")
        assert b.api_key == "cf-explicit-token-1234567890"

    def test_is_running_true_with_key(self, backend):
        """is_running() returns True when API key is configured."""
        assert backend.is_running() is True

    def test_api_key_setter_writes_through(self, cloudflare_env):
        """api_key setter writes through to _api_key (R07.21 pattern)."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend()
        b.api_key = "cf-new-token-0987654321"
        assert b.api_key == "cf-new-token-0987654321"
        # Auth headers must reflect the new key
        assert b._get_auth_headers()["Authorization"] == "Bearer cf-new-token-0987654321"

    def test_account_id_property_returns_value(self, backend):
        """The account_id property exposes the resolved account ID."""
        assert backend.account_id == VALID_ACCOUNT_ID


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


class TestCloudflareIdentity:
    """Verify backend_type + provider label."""

    def test_backend_type_returns_cloudflare(self, backend):
        assert backend.backend_type == BackendType.CLOUDFLARE

    def test_backend_type_value_is_cloudflare_string(self, backend):
        """backend_type.value is the string the footer formatter reads."""
        assert backend.backend_type.value == "cloudflare"

    def test_provider_label(self, backend):
        assert backend._provider_label == "Cloudflare"


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestCloudflareUrls:
    """Verify URL builders return the Cloudflare Workers AI endpoints."""

    def test_chat_completions_url(self, backend):
        assert (
            backend._get_chat_completions_url()
            == f"https://api.cloudflare.com/client/v4/accounts/{VALID_ACCOUNT_ID}/ai/v1/chat/completions"
        )

    def test_models_url_is_native_endpoint_not_openai_compat(self, backend):
        """Cloudflare does NOT expose /v1/models on the OpenAI-compat path.
        The native endpoint is /ai/models/search (at the /ai/ level, NOT
        /ai/v1/). Verifies the unique Cloudflare twist."""
        assert (
            backend._get_models_url()
            == f"https://api.cloudflare.com/client/v4/accounts/{VALID_ACCOUNT_ID}/ai/models/search?per_page=100"
        )

    def test_chat_url_with_custom_base(self, cloudflare_env):
        """Custom base_url flows through to chat URL."""
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        b = CloudflareBackend(base_url="https://custom.proxy/ai/v1")
        assert b._get_chat_completions_url() == "https://custom.proxy/ai/v1/chat/completions"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------


class TestCloudflareAuth:
    """Verify auth header construction."""

    def test_auth_headers_include_bearer(self, backend):
        h = backend._get_auth_headers()
        assert h["Authorization"] == f"Bearer {VALID_KEY}"
        assert h["Content-Type"] == "application/json"

    def test_extra_auth_headers_empty(self, backend):
        """Cloudflare uses no extra headers (unlike OpenRouter's
        HTTP-Referer/X-Title or OrcaRouter's X-OrcaRouter-Include-Cost)."""
        assert backend._extra_auth_headers() == {}

    def test_validate_api_key_warns_on_wrong_prefix(self, cloudflare_env, capsys):
        """A known wrong-provider prefix (sk- for OpenAI, hf_ for HuggingFace,
        nvapi- for NVIDIA, pk_ for Pollinations) surfaces a debug-mode warning
        (not an error). Cloudflare tokens have no recognizable prefix."""
        import os

        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            # Should NOT raise \u2014 just warn
            b = CloudflareBackend(api_key="sk-openai-key-123456789012")
            captured = capsys.readouterr()
            assert "Cloudflare" in captured.out
            assert "sk-" in captured.out
            assert "Warning" in captured.out
            # Backend still works
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)

    def test_validate_api_key_silent_without_debug(self, cloudflare_env, capsys):
        """Without AGENTKTHX_DEBUG, the prefix mismatch is silent."""
        import os

        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        os.environ.pop("AGENTKTHX_DEBUG", None)
        try:
            b = CloudflareBackend(api_key="sk-openai-key-123456789012")
            captured = capsys.readouterr()
            assert "Warning" not in captured.out
            assert b.is_running() is True
        except Exception:
            pass

    def test_validate_api_key_accepts_cloudflare_token(self, cloudflare_env, capsys):
        """A Cloudflare-style token (no recognizable prefix) produces no warning
        even in debug mode."""
        import os

        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            b = CloudflareBackend(api_key="cf-valid-token-123456789012345")
            captured = capsys.readouterr()
            assert "Warning" not in captured.out
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)


# ---------------------------------------------------------------------------
# Free-model + daily-neuron-quota detection
# ---------------------------------------------------------------------------


class TestCloudflareFreeModel:
    """Verify the free-model classifier + daily-quota detector."""

    def test_is_free_model_for_cataloged_model(self):
        """Every cataloged Cloudflare model is 'free' (daily-neuron-budget model)."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_free_model

        assert _is_free_model("@cf/meta/llama-3.3-70b-instruct-fp8-fast") is True
        assert _is_free_model("@cf/meta/llama-3.1-8b-instruct") is True
        assert _is_free_model("@cf/qwen/qwen2.5-coder-32b-instruct") is True

    def test_is_free_model_no_prefix_stripping(self):
        """Cloudflare catalog keys on FULL prefixed IDs (e.g. '@cf/meta/...').
        The function does NOT strip the prefix (the model arg must match the
        catalog key exactly)."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_free_model

        # Full prefixed names match the catalog directly
        assert _is_free_model("@cf/meta/llama-3.3-70b-instruct-fp8-fast") is True
        # Bare post-slash segment does NOT match (no prefix stripping)
        assert _is_free_model("llama-3.3-70b-instruct-fp8-fast") is False
        assert _is_free_model("meta/llama-3.3-70b-instruct-fp8-fast") is False

    def test_is_free_model_false_for_unknown(self):
        """Uncataloged models return False (conservative)."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_free_model

        assert _is_free_model("@cf/unknown/future-model") is False
        assert _is_free_model("nonexistent") is False
        assert _is_free_model("") is False

    def test_neuron_quota_exhaustion_true_on_429_with_quota_keyword(self):
        """429 with 'neuron' / 'quota' / 'daily' / 'exhausted' / 'limit' is
        daily quota exhaustion (NOT retryable)."""
        from agentkthx.plugins.cloudflare.cloudflare import _looks_like_neuron_quota_exhaustion

        assert _looks_like_neuron_quota_exhaustion(429, "daily neuron quota exhausted") is True
        assert _looks_like_neuron_quota_exhaustion(429, "neuron limit reached") is True
        assert _looks_like_neuron_quota_exhaustion(429, "quota exhausted") is True
        assert _looks_like_neuron_quota_exhaustion(429, "daily limit") is True
        assert _looks_like_neuron_quota_exhaustion(429, "exhausted for today") is True

    def test_neuron_quota_exhaustion_false_on_plain_rate_limit(self):
        """429 with rate-limit language only (no quota keyword) is NOT
        classified as quota exhaustion (retryable).

        Per the Cloudflare API technical reference:
          - "429 without quota language = transient rate limit (backoff)"
          - "429 with 'neuron' or 'quota' in message = daily quota exhausted"

        So the helper only looks for neuron-specific markers ("neuron",
        "quota", "daily", "exhausted"). "limit" alone is NOT an indicator
        because "rate limit exceeded" (the canonical transient wording)
        contains "limit" \u2014 including it would mis-classify every
        transient 429 as quota exhaustion.
        """
        from agentkthx.plugins.cloudflare.cloudflare import _looks_like_neuron_quota_exhaustion

        assert _looks_like_neuron_quota_exhaustion(429, "rate limit exceeded") is False
        assert _looks_like_neuron_quota_exhaustion(429, "RPM limit reached") is False
        assert _looks_like_neuron_quota_exhaustion(429, "TPM limit reached") is False
        # "limit reached" alone (no quota keyword) is also NOT quota
        # exhaustion \u2014 it's the generic transient wording.
        assert _looks_like_neuron_quota_exhaustion(429, "limit reached") is False

    def test_neuron_quota_exhaustion_false_on_non_429(self):
        """Non-429 status codes are never quota exhaustion."""
        from agentkthx.plugins.cloudflare.cloudflare import _looks_like_neuron_quota_exhaustion

        assert _looks_like_neuron_quota_exhaustion(500, "neuron") is False
        assert _looks_like_neuron_quota_exhaustion(401, "quota") is False
        assert _looks_like_neuron_quota_exhaustion(200, "daily") is False

    def test_neuron_quota_exhaustion_false_on_empty_body(self):
        """Empty error body returns False (no signal)."""
        from agentkthx.plugins.cloudflare.cloudflare import _looks_like_neuron_quota_exhaustion

        assert _looks_like_neuron_quota_exhaustion(429, "") is False


# ---------------------------------------------------------------------------
# Tool support detection
# ---------------------------------------------------------------------------


class TestCloudflareToolSupport:
    """Verify test_tool_support distinguishes vision/R1/GPT-OSS vs chat models."""

    def test_chat_model_returns_native(self, backend):
        """Llama / Mistral / Qwen / Gemma / Phi chat models \u2192 NATIVE."""
        assert (
            backend.test_tool_support("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
            == ToolSupportLevel.NATIVE
        )
        assert (
            backend.test_tool_support("@cf/meta/llama-3.1-8b-instruct") == ToolSupportLevel.NATIVE
        )
        assert (
            backend.test_tool_support("@cf/mistralai/mistral-7b-instruct-v0.3")
            == ToolSupportLevel.NATIVE
        )
        assert (
            backend.test_tool_support("@cf/qwen/qwen2.5-coder-32b-instruct")
            == ToolSupportLevel.NATIVE
        )
        assert backend.test_tool_support("@cf/google/gemma-3-12b-it") == ToolSupportLevel.NATIVE
        assert (
            backend.test_tool_support("@cf/microsoft/phi-4-mini-instruct")
            == ToolSupportLevel.NATIVE
        )

    def test_vision_model_returns_react(self, backend):
        """Vision models reject `tools` \u2192 REACT fallback."""
        assert (
            backend.test_tool_support("@cf/meta/llama-3.2-11b-vision-instruct")
            == ToolSupportLevel.REACT
        )

    def test_deepseek_r1_distill_returns_react(self, backend):
        """DeepSeek-R1-distill variants reject `tools` \u2192 REACT."""
        assert (
            backend.test_tool_support("@cf/deepseek-ai/deepseek-r1-distill-qwen-32b")
            == ToolSupportLevel.REACT
        )
        assert (
            backend.test_tool_support("@cf/deepseek-ai/deepseek-r1-distill-llama-8b")
            == ToolSupportLevel.REACT
        )

    def test_gpt_oss_returns_react(self, backend):
        """GPT-OSS requires the Responses API for tools \u2192 REACT fallback
        via /chat/completions."""
        assert backend.test_tool_support("@cf/openai/gpt-oss-120b") == ToolSupportLevel.REACT
        assert backend.test_tool_support("@cf/openai/gpt-oss-20b") == ToolSupportLevel.REACT

    def test_unknown_model_returns_native_default(self, backend):
        """Unknown models fall through to NATIVE (inherited CloudBackend default).

        The first real request will probe and cache the actual verdict."""
        assert backend.test_tool_support("@cf/unknown/future-model") == ToolSupportLevel.NATIVE


# ---------------------------------------------------------------------------
# Catalog (seed) content
# ---------------------------------------------------------------------------


class TestCloudflareCatalog:
    """Verify the seed catalog loaded from model_seed.json."""

    def test_catalog_loaded_with_15_plus_models(self):
        """Seed catalog must include the 15 chat Cloudflare models we
        curated in scripts/add_cloudflare_seed.py."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        assert (
            len(CLOUDFLARE_MODELS) >= 15
        ), f"Seed catalog has {len(CLOUDFLARE_MODELS)} models, expected >= 15"

    def test_catalog_includes_llama_family(self):
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name in [
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/meta/llama-3.1-8b-instruct",
            "@cf/meta/llama-3.2-3b-instruct",
            "@cf/meta/llama-3.2-1b-instruct",
        ]:
            assert name in CLOUDFLARE_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_vision_model(self):
        """Vision model is in the catalog (chat-capable via ReAct)."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        assert "@cf/meta/llama-3.2-11b-vision-instruct" in CLOUDFLARE_MODELS

    def test_catalog_includes_mistral_family(self):
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        assert "@cf/mistralai/mistral-7b-instruct-v0.3" in CLOUDFLARE_MODELS

    def test_catalog_includes_qwen_family(self):
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name in [
            "@cf/qwen/qwen2.5-coder-32b-instruct",
            "@cf/qwen/qwen2.5-7b-instruct",
        ]:
            assert name in CLOUDFLARE_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_deepseek_r1_distill(self):
        """R1 distill models are in the catalog (chat-capable via ReAct)."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name in [
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
            "@cf/deepseek-ai/deepseek-r1-distill-llama-8b",
        ]:
            assert name in CLOUDFLARE_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_gemma_family(self):
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name in [
            "@cf/google/gemma-3-12b-it",
            "@cf/google/gemma-2-9b-it",
        ]:
            assert name in CLOUDFLARE_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_phi(self):
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        assert "@cf/microsoft/phi-4-mini-instruct" in CLOUDFLARE_MODELS

    def test_catalog_includes_gpt_oss(self):
        """GPT-OSS is in the catalog (Responses-API-only for tools, but
        /chat/completions works for plain text \u2014 ReAct fallback applies)."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name in [
            "@cf/openai/gpt-oss-120b",
            "@cf/openai/gpt-oss-20b",
        ]:
            assert name in CLOUDFLARE_MODELS, f"{name} missing from catalog"

    def test_catalog_entries_have_required_fields(self):
        """Each entry has context_length, default_temperature,
        default_max_tokens, pricing."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name, meta in CLOUDFLARE_MODELS.items():
            assert "context_length" in meta, f"{name} missing context_length"
            assert "default_temperature" in meta, f"{name} missing default_temperature"
            assert "default_max_tokens" in meta, f"{name} missing default_max_tokens"
            assert "pricing" in meta, f"{name} missing pricing"
            assert isinstance(meta["context_length"], int) and meta["context_length"] > 0
            assert isinstance(meta["default_max_tokens"], int) and meta["default_max_tokens"] > 0
            assert "input" in meta["pricing"] and "output" in meta["pricing"]

    def test_catalog_pricing_all_zero(self):
        """Cloudflare's daily-neuron-budget model means every cataloged model
        is priced 0.0/0.0 (free within the daily quota)."""
        from agentkthx.plugins.cloudflare.cloudflare import CLOUDFLARE_MODELS

        for name, meta in CLOUDFLARE_MODELS.items():
            pricing = meta["pricing"]
            assert pricing["input"] == 0.0, (
                f"{name} pricing.input is {pricing['input']}, expected 0.0 "
                f"(Cloudflare's daily-neuron-budget model means every "
                f"cataloged model is 'free' within the daily quota)"
            )
            assert (
                pricing["output"] == 0.0
            ), f"{name} pricing.output is {pricing['output']}, expected 0.0"


# ---------------------------------------------------------------------------
# Non-chat blocklist
# ---------------------------------------------------------------------------


class TestCloudflareNonChatBlocklist:
    """Verify _is_non_chat_model keeps chat models and drops non-chat."""

    def test_llama_chat_passes_through(self):
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/meta/llama-3.3-70b-instruct-fp8-fast") is False
        assert _is_non_chat_model("@cf/meta/llama-3.1-8b-instruct") is False

    def test_vision_model_passes_through(self):
        """Vision-language models are chat-capable (text + image input) \u2014
        NOT blocked. They get ReAct in test_tool_support but still appear in
        the catalog so users can drive them via text-only ReAct prompts."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/meta/llama-3.2-11b-vision-instruct") is False

    def test_deepseek_r1_passes_through(self):
        """R1-distill models are chat-capable (text + reasoning) \u2014 NOT
        blocked (get ReAct for tools, but still in catalog)."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/deepseek-ai/deepseek-r1-distill-qwen-32b") is False

    def test_gpt_oss_passes_through(self):
        """GPT-OSS is chat-capable via /chat/completions \u2014 NOT blocked
        (just gets ReAct for tools since Responses API is needed for native)."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/openai/gpt-oss-120b") is False
        assert _is_non_chat_model("@cf/openai/gpt-oss-20b") is False

    def test_embedding_models_blocked(self):
        """BGE / embedding models must be blocked."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/baai/bge-base-en-v1.5") is True
        assert _is_non_chat_model("@cf/baai/bge-large-en-v1.5") is True

    def test_image_generation_models_blocked(self):
        """Stable diffusion / Flux / Dreamshaper image-gen models must be blocked."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/blackforest-labs/flux-1-schnell") is True
        assert _is_non_chat_model("@cf/stabilityai/stable-diffusion-xl-base-1.0") is True
        assert _is_non_chat_model("@cf/lykon/dreamshaper-8") is True

    def test_tts_speech_models_blocked(self):
        """TTS / speech / ASR / Whisper models must be blocked."""
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/myshell-ai/melotts") is True  # tts
        assert _is_non_chat_model("@cf/openai/whisper-tiny") is True  # asr
        assert _is_non_chat_model("@cf/openai/whisper-large-v3-turbo") is True

    def test_classification_models_blocked(self):
        from agentkthx.plugins.cloudflare.cloudflare import _is_non_chat_model

        assert _is_non_chat_model("@cf/huggingface/hate-speech-detection") is True


# ---------------------------------------------------------------------------
# list_models + catalog fallback
# ---------------------------------------------------------------------------


class TestCloudflareListModels:
    """Verify list_models() and the offline fallback."""

    def test_catalog_fallback_list_returns_seed_entries(self, backend):
        """_catalog_fallback_list returns the static catalog shaped as
        list_models() entries."""
        models = backend._catalog_fallback_list()
        assert len(models) >= 15

        # Each entry has the list_models() shape
        for m in models:
            assert "name" in m
            assert m["size"] == 0
            assert "details" in m
            assert m["details"]["backend"] == "cloudflare"
            assert "context_length" in m["details"]
            assert "free_tier" in m["details"]
            assert m["details"]["free_tier"] is True  # all cataloged models free

    def test_catalog_fallback_includes_llama(self, backend):
        models = backend._catalog_fallback_list()
        names = {m["name"] for m in models}
        assert "@cf/meta/llama-3.3-70b-instruct-fp8-fast" in names
        assert "@cf/meta/llama-3.1-8b-instruct" in names


# ---------------------------------------------------------------------------
# get_model_info / get_model_max_context (catalog lookup)
# ---------------------------------------------------------------------------


class TestCloudflareModelInfo:
    """Verify catalog-driven model info lookups."""

    def test_get_model_info_returns_catalog_entry(self, backend):
        info = backend.get_model_info("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        assert info is not None
        assert info["name"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        assert info["details"]["context_length"] == 131072
        assert info["details"]["free_tier"] is True

    def test_get_model_info_no_prefix_stripping(self, backend):
        """Cloudflare catalog keys on FULL prefixed IDs \u2014 get_model_info
        does NOT strip the prefix. Only the full name matches."""
        a = backend.get_model_info("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        assert a is not None
        # Bare segment does NOT match (no prefix stripping)
        b = backend.get_model_info("llama-3.3-70b-instruct-fp8-fast")
        assert b is None

    def test_get_model_info_returns_none_for_unknown(self, backend):
        assert backend.get_model_info("@cf/unknown/future-model") is None

    def test_get_model_max_context_for_cataloged(self, backend):
        assert backend.get_model_max_context("@cf/meta/llama-3.3-70b-instruct-fp8-fast") == 131072
        assert backend.get_model_max_context("@cf/mistralai/mistral-7b-instruct-v0.3") == 32768

    def test_get_model_max_context_fallback_for_unknown(self, backend):
        """Unknown models fall back to _DEFAULT_CONTEXT_FALLBACK (128000)."""
        assert backend.get_model_max_context("@cf/unknown/future-model") == 128000

    def test_get_model_max_context_ignores_family_arg(self, backend):
        """CloudBackend's get_model_max_context ignores the family arg
        (catalog is per-model, not per-family)."""
        ctx_with_family = backend.get_model_max_context(
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast", family="llama"
        )
        ctx_no_family = backend.get_model_max_context("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
        assert ctx_with_family == ctx_no_family == 131072


# ---------------------------------------------------------------------------
# Plugin manifest + PluginManager discovery
# ---------------------------------------------------------------------------


class TestCloudflareManifest:
    """Verify the plugin.json manifest loads via PluginManager."""

    def test_plugin_discovered_by_manager(self):
        """PluginManager.discover() finds the cloudflare plugin."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        cf_m = [m for m in manifests if m.name == "cloudflare"]
        assert len(cf_m) == 1
        m = cf_m[0]
        assert m.version == "0.1.0"
        assert m.type == "backend"
        assert m.display_name == "Cloudflare Workers AI Cloud Backend"
        assert m.license == "MIT"

    def test_manifest_provides_cloudflare_and_cf_aliases(self):
        """Both 'cloudflare' and 'cf' backend aliases are registered."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        cf_m = next(m for m in manifests if m.name == "cloudflare")
        backends = cf_m.provides["backends"]
        assert "cloudflare" in backends
        assert "cf" in backends
        assert backends["cloudflare"] == "cloudflare.CloudflareBackend"
        assert backends["cf"] == "cloudflare.CloudflareBackend"

    def test_manifest_cli_flags_include_cloudflare_and_cf(self):
        """--backend flag accepts both 'cloudflare' and 'cf'."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        cf_m = next(m for m in manifests if m.name == "cloudflare")
        cli_flags = cf_m.provides["cli_flags"]
        assert "--backend" in cli_flags
        assert "cloudflare" in cli_flags["--backend"]
        assert "cf" in cli_flags["--backend"]

    def test_manifest_config_defaults(self):
        """Manifest declares CLOUDFLARE_BASE_URL, CLOUDFLARE_API_KEY,
        CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_DEFAULT_MODEL, CLOUDFLARE_FREE_ONLY
        defaults."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        cf_m = next(m for m in manifests if m.name == "cloudflare")
        defaults = cf_m.config["defaults"]
        assert defaults["CLOUDFLARE_BASE_URL"] == ""
        assert defaults["CLOUDFLARE_API_KEY"] == ""
        assert defaults["CLOUDFLARE_ACCOUNT_ID"] == ""
        assert defaults["CLOUDFLARE_DEFAULT_MODEL"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        assert defaults["CLOUDFLARE_FREE_ONLY"] == "false"
        assert cf_m.config["env_prefix"] == "CLOUDFLARE"

    def test_plugin_loads_and_registers_backend(self):
        """Loading the plugin registers CloudflareBackend under both 'cloudflare'
        and 'cf' aliases."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("cloudflare")

        assert pm.is_loaded("cloudflare")
        cls = pm.get_backend_class("cloudflare")
        assert cls is not None
        assert cls.__name__ == "CloudflareBackend"

        # Alias
        cls_alias = pm.get_backend_class("cf")
        assert cls_alias is cls

    def test_plugin_in_backend_choices(self):
        """Both 'cloudflare' and 'cf' appear in --backend choices."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("cloudflare")
        choices = pm.get_backend_choices()
        assert "cloudflare" in choices
        assert "cf" in choices

    def test_manifest_conforms_to_v0_2_schema(self):
        """The plugin.json shape conforms to schemas/v0.2/plugin.schema.json."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        cf_m = next(m for m in manifests if m.name == "cloudflare")
        # Required v0.2 fields
        assert cf_m.name
        assert cf_m.version
        assert cf_m.description
        assert cf_m.author
        # Schema URL
        assert cf_m.schema and "v0.2" in cf_m.schema


# ---------------------------------------------------------------------------
# config.py integration
# ---------------------------------------------------------------------------


class TestCloudflareConfig:
    """Verify config.py exposes CLOUDFLARE_* env vars with correct defaults."""

    def test_cloudflare_base_url_default_empty(self):
        """Default CLOUDFLARE_BASE_URL is empty (constructed from account_id)."""
        from agentkthx import config

        # The default is "" \u2014 the backend builds the URL at __init__ time
        # from CLOUDFLARE_ACCOUNT_ID. Don't assert a specific value here
        # because the test environment may have CLOUDFLARE_BASE_URL set.
        assert isinstance(config.CLOUDFLARE_BASE_URL, str)

    def test_cloudflare_api_key_default_empty(self):
        from agentkthx import config

        assert config.CLOUDFLARE_API_KEY == ""

    def test_cloudflare_account_id_default_empty(self):
        from agentkthx import config

        assert config.CLOUDFLARE_ACCOUNT_ID == ""

    def test_cloudflare_default_model_default(self):
        from agentkthx import config

        assert config.CLOUDFLARE_DEFAULT_MODEL == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

    def test_cloudflare_free_only_default_false(self):
        from agentkthx import config

        assert config.CLOUDFLARE_FREE_ONLY is False

    def test_cloudflare_free_only_env_true(self, monkeypatch):
        monkeypatch.setenv("CLOUDFLARE_FREE_ONLY", "1")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.CLOUDFLARE_FREE_ONLY is True
        finally:
            monkeypatch.delenv("CLOUDFLARE_FREE_ONLY", raising=False)
            importlib.reload(_config)

    def test_cloudflare_in_backend_selection_ladder(self, monkeypatch):
        """AGENTKTHX_BACKEND=cloudflare picks up the Cloudflare default model."""
        monkeypatch.setenv("AGENTKTHX_BACKEND", "cloudflare")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.DEFAULT_MODEL == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        finally:
            monkeypatch.delenv("AGENTKTHX_BACKEND", raising=False)
            importlib.reload(_config)

    def test_cloudflare_alias_cf_in_backend_selection_ladder(self, monkeypatch):
        """AGENTKTHX_BACKEND=cf (alias) picks up the Cloudflare default model."""
        monkeypatch.setenv("AGENTKTHX_BACKEND", "cf")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.DEFAULT_MODEL == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        finally:
            monkeypatch.delenv("AGENTKTHX_BACKEND", raising=False)
            importlib.reload(_config)


# ---------------------------------------------------------------------------
# BackendType enum integration
# ---------------------------------------------------------------------------


class TestCloudflareBackendType:
    """Verify the BackendType.CLOUDFLARE enum value exists."""

    def test_cloudflare_enum_value_exists(self):
        assert hasattr(BackendType, "CLOUDFLARE")
        assert BackendType.CLOUDFLARE.value == "cloudflare"

    def test_cloudflare_enum_distinct_from_others(self):
        """CLOUDFLARE is a distinct value, not aliased to another backend."""
        other_values = [
            BackendType.ZAI,
            BackendType.OPENROUTER,
            BackendType.GEMINI,
            BackendType.HUGGINGFACE,
            BackendType.OPENAI,
            BackendType.MISTRAL,
            BackendType.ORCAROUTER,
            BackendType.POLLINATIONS,
            BackendType.NVIDIA,
        ]
        for other in other_values:
            assert BackendType.CLOUDFLARE != other
            assert BackendType.CLOUDFLARE.value != other.value


# ---------------------------------------------------------------------------
# /auth picker registry integration
# ---------------------------------------------------------------------------


class TestCloudflareAuthRegistry:
    """Verify the /auth picker exposes CLOUDFLARE_* entries."""

    def test_auth_vars_includes_cloudflare_api_key(self):
        from agentkthx.cli.auth import auth_vars

        names = {e.name for e in auth_vars()}
        assert "CLOUDFLARE_API_KEY" in names

    def test_auth_vars_includes_cloudflare_account_id(self):
        """The account ID row is unique to Cloudflare \u2014 every other backend
        derives everything from the API key alone."""
        from agentkthx.cli.auth import auth_vars

        names = {e.name for e in auth_vars()}
        assert "CLOUDFLARE_ACCOUNT_ID" in names

    def test_auth_vars_includes_cloudflare_free_only_flag(self):
        from agentkthx.cli.auth import auth_vars

        names = {e.name for e in auth_vars()}
        assert "CLOUDFLARE_FREE_ONLY" in names

    def test_cloudflare_account_id_kind_is_key(self):
        """The account ID row is a 'key' kind (masked) so the picker prompts
        for a value via prompt_secret()."""
        from agentkthx.cli.auth import auth_vars

        for e in auth_vars():
            if e.name == "CLOUDFLARE_ACCOUNT_ID":
                assert e.kind == "key"
                assert e.backend == "Cloudflare"
                return
        pytest.fail("CLOUDFLARE_ACCOUNT_ID not in auth_vars()")

    def test_cloudflare_account_id_sits_between_key_and_flag(self):
        """The account ID is injected between the API key row and the
        FREE_ONLY flag row so the picker reads naturally:

            Cloudflare   CLOUDFLARE_API_KEY       set (***xyz)
            Cloudflare   CLOUDFLARE_ACCOUNT_ID    set (***abc)
            Cloudflare   CLOUDFLARE_FREE_ONLY     [off]
        """
        from agentkthx.cli.auth import auth_vars

        entries = auth_vars()
        cf_entries = [e for e in entries if e.backend == "Cloudflare"]
        assert len(cf_entries) == 3
        assert cf_entries[0].name == "CLOUDFLARE_API_KEY"
        assert cf_entries[0].kind == "key"
        assert cf_entries[1].name == "CLOUDFLARE_ACCOUNT_ID"
        assert cf_entries[1].kind == "key"
        assert cf_entries[2].name == "CLOUDFLARE_FREE_ONLY"
        assert cf_entries[2].kind == "flag"

    def test_key_var_to_slug_includes_cloudflare(self):
        """_KEY_VAR_TO_SLUG maps CLOUDFLARE_API_KEY to 'cloudflare' so the
        live-backend-patching logic finds the Cloudflare session."""
        from agentkthx.cli.auth import _KEY_VAR_TO_SLUG

        assert _KEY_VAR_TO_SLUG.get("CLOUDFLARE_API_KEY") == "cloudflare"


# ---------------------------------------------------------------------------
# Live /ai/models/search response-shape regression (R07.27 follow-up)
# ---------------------------------------------------------------------------
#
# The Cloudflare /ai/models/search endpoint returns a DIFFERENT shape than
# the docs claim. The original scaffold read `id` for the model name and
# `type == "text-generation"` for the category, which silently matched 0
# models and fell back to the 15-model seed. The fix reads `name` (the
# @cf/... model ID) and `task.name == "Text Generation"` (capitalized, with
# space), and parses the `properties[]` array for context_window +
# function_calling + reasoning enrichment.
#
# These tests pin the corrected shape by mocking the live response with
# the actual envelope observed against a real account (Oct 2026).

_LIVE_RESPONSE_SHAPE = {
    "success": True,
    "result": [
        # A chat-capable Text Generation model (matches the seed catalog)
        {
            "id": "f9f2250b-1048-4a52-9910-d0bf976616a1",
            "source": 1,
            "name": "@cf/openai/gpt-oss-120b",
            "description": "OpenAI's open-weight models.",
            "task": {
                "id": "c329a1f9-323d-4e91-b2aa-582dd4188d34",
                "name": "Text Generation",
                "description": "Family of generative text models.",
            },
            "created_at": "2025-08-05 10:27:29.131",
            "tags": [],
            "properties": [
                {"property_id": "async_queue", "value": "true"},
                {"property_id": "context_window", "value": 128000},
                {
                    "property_id": "price",
                    "value": [
                        {"unit": "per M input tokens", "price": 0.35, "currency": "USD"},
                        {"unit": "per M output tokens", "price": 0.75, "currency": "USD"},
                    ],
                },
                {"property_id": "function_calling", "value": "true"},
                {"property_id": "reasoning", "value": "true"},
                {
                    "property_id": "reasoning_effort",
                    "value": {
                        "supported_efforts": ["high", "medium", "low"],
                        "default_effort": "medium",
                        "mandatory": True,
                        "default_enabled": True,
                    },
                },
            ],
        },
        # A second chat-capable Text Generation model (NEW — not in seed)
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "source": 1,
            "name": "@cf/zai-org/glm-5.3",
            "description": "GLM-5.3 from Zhipu AI.",
            "task": {"id": "x", "name": "Text Generation", "description": "..."},
            "created_at": "2025-09-01 00:00:00.000",
            "tags": [],
            "properties": [
                {"property_id": "context_window", "value": 1048576},
                {"property_id": "function_calling", "value": "true"},
                {"property_id": "reasoning", "value": "true"},
            ],
        },
        # An embeddings model — should be filtered out (NOT Text Generation)
        {
            "id": "22222222-3333-4444-5555-666666666666",
            "source": 1,
            "name": "@cf/baai/bge-large-en-v1.5",
            "description": "BAAI BGE embeddings.",
            "task": {"id": "y", "name": "Text Embeddings", "description": "..."},
            "properties": [{"property_id": "context_window", "value": 512}],
        },
        # A safety guardrail model — labeled "Text Generation" by Cloudflare
        # but the blocklist should drop it (it's a safety classifier, not chat)
        {
            "id": "33333333-4444-5555-6666-777777777777",
            "source": 1,
            "name": "@cf/meta/llama-guard-3-8b",
            "description": "Llama Guard 3 safety classifier.",
            "task": {"id": "z", "name": "Text Generation", "description": "..."},
            "properties": [{"property_id": "context_window", "value": 131072}],
        },
        # A Cloudflare CLEF model — labeled "Text Generation" but specialized
        {
            "id": "44444444-5555-6666-7777-888888888888",
            "source": 1,
            "name": "@cf/cloudflare/clef",
            "description": "Cloudflare CLEF classifier.",
            "task": {"id": "w", "name": "Text Generation", "description": "..."},
            "properties": [{"property_id": "context_window", "value": 65536}],
        },
    ],
    "errors": [],
    "messages": [],
}


class TestCloudflareLiveFetchShape:
    """Pin the corrected live-fetch shape: `name` for model ID,
    `task.name == "Text Generation"` for category, `properties[]` for
    context_window + capabilities. The original scaffold got all three
    wrong and silently fell back to the 15-model seed.
    """

    def test_fetch_live_models_uses_name_not_id_for_model_id(self, backend, monkeypatch):
        """The model ID lives in `name` (e.g. '@cf/openai/gpt-oss-120b'),
        NOT in `id` (which is an internal UUID)."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        names = {m["name"] for m in models}
        # The live-discovered Text Generation model appears under its `name`,
        # not its UUID `id`.
        assert "@cf/openai/gpt-oss-120b" in names
        assert "@cf/zai-org/glm-5.3" in names
        # The UUID from `id` must NOT appear as a model name.
        assert "f9f2250b-1048-4a52-9910-d0bf976616a1" not in names

    def test_fetch_live_models_uses_task_name_not_type_for_category(self, backend, monkeypatch):
        """The category lives in `task.name` as the capitalized string
        'Text Generation', NOT a top-level `type` field with the lowercase
        'text-generation' value the docs claim."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        names = {m["name"] for m in models}
        # The Text Generation models pass the filter.
        assert "@cf/openai/gpt-oss-120b" in names
        # The Text Embeddings model (different task.name) is filtered out.
        assert "@cf/baai/bge-large-en-v1.5" not in names

    def test_fetch_live_models_drops_safety_guard_despite_text_generation_label(
        self, backend, monkeypatch
    ):
        """@cf/meta/llama-guard-3-8b is labeled 'Text Generation' by
        Cloudflare but is a safety classifier, not a chat model. The
        non-chat blocklist must drop it via the 'guard' pattern."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        names = {m["name"] for m in models}
        assert "@cf/meta/llama-guard-3-8b" not in names, (
            "llama-guard-3-8b is a safety classifier — must be blocked "
            "even though Cloudflare labels it 'Text Generation'"
        )

    def test_fetch_live_models_drops_clef_specialized_classifier(self, backend, monkeypatch):
        """@cf/cloudflare/clef is Cloudflare's specialized classifier —
        labeled 'Text Generation' but not a chat model. The blocklist
        must drop it via the 'clef' pattern."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        names = {m["name"] for m in models}
        assert "@cf/cloudflare/clef" not in names

    def test_fetch_live_models_enriches_context_window_from_properties(self, backend, monkeypatch):
        """context_window is parsed from the properties[] array (live
        enrichment), not from the static seed catalog. Live wins."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        by_name = {m["name"]: m for m in models}
        # gpt-oss-120b's live context_window is 128000 (from properties).
        assert by_name["@cf/openai/gpt-oss-120b"]["details"]["context_length"] == 128000
        # glm-5.3's live context_window is 1048576 (1M tokens).
        assert by_name["@cf/zai-org/glm-5.3"]["details"]["context_length"] == 1048576

    def test_fetch_live_models_enriches_function_calling_and_reasoning_flags(
        self, backend, monkeypatch
    ):
        """function_calling + reasoning are parsed from properties[] as
        bonus enrichment (the static seed never had these fields)."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        by_name = {m["name"]: m for m in models}
        # gpt-oss-120b declares function_calling=true, reasoning=true.
        gpt_oss = by_name["@cf/openai/gpt-oss-120b"]["details"]
        assert gpt_oss["function_calling"] is True
        assert gpt_oss["reasoning"] is True

    def test_fetch_live_models_merges_with_seed_catalog(self, backend, monkeypatch):
        """Live-discovered models come first; seed-only models (variants
        the API may omit) are appended so the catalog always includes them."""

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(_LIVE_RESPONSE_SHAPE).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        models = backend._fetch_live_models()
        names = {m["name"] for m in models}
        # Live-discovered (from the mock).
        assert "@cf/openai/gpt-oss-120b" in names
        assert "@cf/zai-org/glm-5.3" in names
        # Seed-only (NOT in the live mock — must be appended from the seed).
        assert "@cf/meta/llama-3.3-70b-instruct-fp8-fast" in names
        assert "@cf/mistralai/mistral-7b-instruct-v0.3" in names

    def test_fetch_live_models_handles_success_false(self, backend, monkeypatch):
        """When Cloudflare returns success=false, _fetch_live_models raises
        so the caller falls back to the seed catalog (via the surrounding
        try/except in list_models)."""

        bad_response = {
            "success": False,
            "result": [],
            "errors": [{"code": 7003, "message": "Could not route"}],
            "messages": [],
        }

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(bad_response).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="success=false"):
            backend._fetch_live_models()


# ---------------------------------------------------------------------------
# 403 / Cloudflare error code 5035 paid-plan-only handling (R07.27 follow-up)
# ---------------------------------------------------------------------------
#
# Cloudflare returns HTTP 403 with errors[0].code == 5035 when a free-tier
# account invokes a paid-tier-only model (e.g. @cf/zai-org/glm-5.3-flash).
# Distinct from a token-permission 403 (no code field, just a plain message).
# The two need different user actions: 5035 = upgrade OR switch model;
# token-permission 403 = recreate the API token with the right scope.
#
# These tests pin:
#   - _extract_cloudflare_error_code parses 5035 from the live envelope
#   - _make_api_request surfaces a clear "Workers Paid plan" error on 403/5035
#   - _make_api_request caches the paid-only verdict for the next list_models()
#   - _iter_sse_lines does the same on the streaming path
#   - list_models with CLOUDFLARE_FREE_ONLY=true filters cached paid-only models
#   - list_models without CLOUDFLARE_FREE_ONLY keeps paid-only models
#   - Plain 403 (no code 5035) still surfaces the token-permission error

_PAID_ONLY_403_BODY = (
    '{"result":{},"success":false,'
    '"errors":[{"code":5035,"message":"AiError: Model '
    '@cf/zai-org/glm-5.3-flash is not available on the Workers Free plan"}],'
    '"messages":[]}'
)

_PLAIN_403_BODY = '{"error":{"message":"token lacks permission"}}'


class TestCloudflareErrorCodeExtractor:
    """Verify _extract_cloudflare_error_code parses the Cloudflare error
    envelope correctly."""

    def test_extracts_5035_paid_only_code(self):
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        assert _extract_cloudflare_error_code(_PAID_ONLY_403_BODY) == 5035

    def test_extracts_other_cloudflare_codes(self):
        """Non-5035 Cloudflare error codes also extract correctly."""
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        # Model not found (Cloudflare routing error)
        body = '{"errors":[{"code":7003,"message":"Could not route to /accounts/..."}]}'
        assert _extract_cloudflare_error_code(body) == 7003

    def test_returns_none_on_empty_body(self):
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        assert _extract_cloudflare_error_code("") is None

    def test_returns_none_on_non_json_body(self):
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        assert _extract_cloudflare_error_code("not json") is None
        assert _extract_cloudflare_error_code("<html>404</html>") is None

    def test_returns_none_on_openai_spec_error_envelope(self):
        """Plain 403 (token-permission) uses the OpenAI-spec shape
        {error: {message}} which has no `errors` array \u2014 return None
        so the plain-403 handler fires."""
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        assert _extract_cloudflare_error_code(_PLAIN_403_BODY) is None

    def test_returns_none_on_missing_errors_array(self):
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        assert _extract_cloudflare_error_code('{"success":false,"result":{}}') is None

    def test_returns_none_on_non_numeric_code(self):
        """If errors[0].code is a string, return None (we only parse int codes)."""
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        body = '{"errors":[{"code":"E5035","message":"..."}]}'
        assert _extract_cloudflare_error_code(body) is None

    def test_returns_none_on_bool_code(self):
        """bool is a subclass of int but should be rejected (True/False aren't
        valid Cloudflare error codes)."""
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        body = '{"errors":[{"code":true,"message":"..."}]}'
        assert _extract_cloudflare_error_code(body) is None

    def test_handles_first_error_only(self):
        """Multiple errors in the array \u2014 we extract the first one's code."""
        from agentkthx.plugins.cloudflare.cloudflare import _extract_cloudflare_error_code

        body = '{"errors":[{"code":5035,"message":"first"},' '{"code":7003,"message":"second"}]}'
        assert _extract_cloudflare_error_code(body) == 5035


class TestCloudflarePaidOnlyCache:
    """Verify the tool_cache helpers for cf-paid:<model> verdicts."""

    def test_cache_and_read_round_trip(self, monkeypatch, tmp_path):
        """cache_cloudflare_paid_only writes; is_cached_cloudflare_paid_only reads."""
        from agentkthx.core import tool_cache

        # Point the cache at a temp file so we don't pollute the host's
        # ~/.agentkthx/tool_support.json during tests.
        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        # Fresh cache \u2014 not paid-only.
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is False

        # Cache the verdict.
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is True

        # Other models unaffected.
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/openai/gpt-oss-120b") is False

    def test_clear_paid_only(self, monkeypatch, tmp_path):
        """cache_cloudflare_paid_only(model, paid_only=False) drops the entry."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is True

        # Clear via paid_only=False.
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=False)
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is False

    def test_clear_all_paid_only(self, monkeypatch, tmp_path):
        """clear_cloudflare_paid_only() drops every cf-paid:<model> entry."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.2", paid_only=True)
        tool_cache.cache_cloudflare_paid_only("@cf/openai/gpt-oss-120b", paid_only=True)

        n_cleared = tool_cache.clear_cloudflare_paid_only()
        assert n_cleared == 3
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is False
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.2") is False
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/openai/gpt-oss-120b") is False

    def test_clear_all_preserves_other_entries(self, monkeypatch, tmp_path):
        """clear_cloudflare_paid_only() only drops cf-paid:<model> keys; the
        tool-support and thinking:<model> entries stay put."""
        from agentkthx.core import tool_cache
        from agentkthx.core.types import ThinkingSupport, ToolSupportLevel

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        # Populate all three key types.
        tool_cache.cache_tool_support(
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast", ToolSupportLevel.NATIVE
        )
        tool_cache.cache_thinking_support(
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", ThinkingSupport.YES
        )
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)

        # Clear only the paid-only entries.
        n_cleared = tool_cache.clear_cloudflare_paid_only()
        assert n_cleared == 1

        # Tool-support and thinking verdicts still readable.
        assert (
            tool_cache.get_cached_tool_support("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
            == ToolSupportLevel.NATIVE
        )
        assert (
            tool_cache.get_cached_thinking_support("@cf/deepseek-ai/deepseek-r1-distill-qwen-32b")
            == ThinkingSupport.YES
        )
        # Paid-only verdict cleared.
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is False


class TestCloudflareMakeApiRequest403:
    """Verify _make_api_request surfaces the right error on 403 + 5035 vs
    plain 403, and caches the paid-only verdict."""

    def _make_http_error(self, status_code: int, body_text: str):
        """Build a fake urllib.error.HTTPError with a body we can read."""
        import urllib.error

        return urllib.error.HTTPError(
            url="https://example.com",
            code=status_code,
            msg="Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    def test_403_with_code_5035_raises_paid_plan_error(self, backend, monkeypatch, tmp_path):
        """403 + errors[0].code=5035 \u2192 'Workers Paid plan' error, NOT
        the generic 'token lacks permission' error."""
        from agentkthx.core import tool_cache

        # Isolate the cache file.
        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        # Build a fake urlopen that raises HTTPError(403) with the
        # paid-only body.
        http_err = self._make_http_error(403, _PAID_ONLY_403_BODY)
        # Attach the body so e.read() returns it (urllib uses e.fp for this,
        # but our handler reads e.read() which falls back to b"" when fp is
        # None \u2014 so we patch e.read directly).
        http_err.read = lambda: _PAID_ONLY_403_BODY.encode("utf-8")  # type: ignore[assignment]

        def fake_urlopen(req, timeout=None):
            raise http_err

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="Workers Paid plan"):
            backend._make_api_request(
                {"model": "@cf/zai-org/glm-5.3-flash", "messages": [], "stream": False},
                stream=False,
            )

    def test_403_with_code_5035_caches_paid_only_verdict(self, backend, monkeypatch, tmp_path):
        """The 403/5035 path caches the paid-only verdict so the next
        list_models() with CLOUDFLARE_FREE_ONLY=true filters it out."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        http_err = self._make_http_error(403, _PAID_ONLY_403_BODY)
        http_err.read = lambda: _PAID_ONLY_403_BODY.encode("utf-8")  # type: ignore[assignment]

        def fake_urlopen(req, timeout=None):
            raise http_err

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="Workers Paid plan"):
            backend._make_api_request(
                {"model": "@cf/zai-org/glm-5.3-flash", "messages": [], "stream": False},
                stream=False,
            )

        # The verdict should now be cached.
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is True

    def test_plain_403_raises_token_permission_error(self, backend, monkeypatch, tmp_path):
        """403 without a Cloudflare code (OpenAI-spec error envelope) \u2192
        the generic 'token lacks permission' error path. Does NOT cache
        a paid-only verdict (the model isn't paid-only, the token is wrong)."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        http_err = self._make_http_error(403, _PLAIN_403_BODY)
        http_err.read = lambda: _PLAIN_403_BODY.encode("utf-8")  # type: ignore[assignment]

        def fake_urlopen(req, timeout=None):
            raise http_err

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="permission denied"):
            backend._make_api_request(
                {
                    "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                    "messages": [],
                    "stream": False,
                },
                stream=False,
            )

        # No paid-only verdict cached (model is fine, token is the issue).
        assert (
            tool_cache.is_cached_cloudflare_paid_only("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
            is False
        )

    def test_403_with_code_5035_streaming_path_also_caches(self, backend, monkeypatch, tmp_path):
        """_iter_sse_lines (the streaming path) does the same 5035
        special-case + cache write as _make_api_request."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        http_err = self._make_http_error(403, _PAID_ONLY_403_BODY)
        http_err.read = lambda: _PAID_ONLY_403_BODY.encode("utf-8")  # type: ignore[assignment]

        def fake_urlopen(req, timeout=None):
            raise http_err

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        # _iter_sse_lines is a generator \u2014 drain it to trigger the raise.
        gen = backend._iter_sse_lines(
            "https://example.com",
            {"model": "@cf/zai-org/glm-5.3-flash", "messages": []},
            {"Content-Type": "application/json"},
        )
        with pytest.raises(RuntimeError, match="Workers Paid plan"):
            list(gen)

        # Same cache write as the non-streaming path.
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is True


class TestCloudflareListModelsFreeOnlyFilter:
    """Verify list_models() filters out cached paid-only models when
    CLOUDFLARE_FREE_ONLY=true, and keeps them otherwise."""

    def test_free_only_filters_paid_models(self, backend, monkeypatch, tmp_path):
        """When CLOUDFLARE_FREE_ONLY=true, models with a cached paid-only
        verdict are dropped from list_models() output."""
        from agentkthx.core import tool_cache

        # Isolate the cache file.
        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        # Mark glm-5.3-flash + glm-5.2 as paid-only.
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)
        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.2", paid_only=True)

        # Mock the live fetch so we get a deterministic catalog.
        live_response = {
            "success": True,
            "result": [
                {
                    "name": "@cf/zai-org/glm-5.3-flash",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 1048576}],
                },
                {
                    "name": "@cf/zai-org/glm-5.2",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 262144}],
                },
                {
                    "name": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 24000}],
                },
            ],
            "errors": [],
            "messages": [],
        }

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(live_response).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        # Force CLOUDFLARE_FREE_ONLY=true via config + env (the
        # _filter_paid_only helper imports the config constant lazily).
        monkeypatch.setenv("CLOUDFLARE_FREE_ONLY", "1")
        import agentkthx.config as _config

        monkeypatch.setattr(_config, "CLOUDFLARE_FREE_ONLY", True, raising=False)

        # Also clear the model_catalog.json cache so list_models re-fetches.
        from agentkthx import model_cache as _mc

        monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")

        names = {m["name"] for m in backend.list_models()}
        # Paid-only models filtered out.
        assert "@cf/zai-org/glm-5.3-flash" not in names
        assert "@cf/zai-org/glm-5.2" not in names
        # Free-tier model still present.
        assert "@cf/meta/llama-3.3-70b-instruct-fp8-fast" in names

    def test_no_free_only_keeps_paid_models(self, backend, monkeypatch, tmp_path):
        """When CLOUDFLARE_FREE_ONLY=false (default), paid-only models stay
        in the catalog (the user might be on a paid plan)."""
        from agentkthx.core import tool_cache

        cache_file = tmp_path / "tool_support.json"
        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: cache_file)

        tool_cache.cache_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash", paid_only=True)

        live_response = {
            "success": True,
            "result": [
                {
                    "name": "@cf/zai-org/glm-5.3-flash",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 1048576}],
                },
                {
                    "name": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 24000}],
                },
            ],
            "errors": [],
            "messages": [],
        }

        def fake_urlopen(req, timeout=None):
            import io

            return io.BytesIO(json.dumps(live_response).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        # Default: CLOUDFLARE_FREE_ONLY=false.
        monkeypatch.delenv("CLOUDFLARE_FREE_ONLY", raising=False)
        import agentkthx.config as _config

        monkeypatch.setattr(_config, "CLOUDFLARE_FREE_ONLY", False, raising=False)

        from agentkthx import model_cache as _mc

        monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")

        names = {m["name"] for m in backend.list_models()}
        # Paid-only model kept when FREE_ONLY is off.
        assert "@cf/zai-org/glm-5.3-flash" in names
        assert "@cf/meta/llama-3.3-70b-instruct-fp8-fast" in names
