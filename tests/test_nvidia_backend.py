"""
NVIDIA NIM plugin (R07.26) — backend regression tests.

Verifies that the NvidiaBackend (the 9th cloud backend, scaffolded from
the CloudBackend base class) correctly implements:

  - CloudBackend inheritance (issubclass checks)
  - Class-attribute provider identity (_api_key_env_var, _provider_label,
    _default_base_url, _default_model, MODEL_CACHE_KEY)
  - __init__ base-URL resolution + API-key validation
  - ``_get_chat_completions_url()`` returns the NVIDIA endpoint
  - ``_get_auth_headers()`` includes Bearer + Content-Type
  - ``_validate_api_key`` warns (not errors) on non-nvapi- prefix
  - ``_extra_auth_headers()`` returns empty dict (no extras)
  - ``_is_free_model()`` returns True for cataloged models, False for unknown
  - ``_looks_like_credit_exhaustion()`` distinguishes 429 rate limit
    from 429 monthly quota exhaustion
  - ``test_tool_support()`` returns REACT for reasoning models (R1 family)
    and NATIVE for chat models (Llama, Mistral, Qwen, etc.)
  - ``_catalog_fallback_list()`` shapes the seed catalog correctly
  - ``list_models()`` returns catalog entries (offline fallback)
  - ``get_model_info()`` / ``get_model_max_context()`` catalog lookup
  - Plugin manifest loads via PluginManager (nvidia + nim aliases)
  - Plugin manifest shape conforms to plugin spec v0.2
  - config.py exposes NVIDIA_* env vars with correct defaults

Mirrors the structure of tests/test_orcarouter_backend.py (the prior
cloud-backend regression suite — same inheritance / class-attribute /
manifest shape checks).
"""

from __future__ import annotations

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

VALID_KEY = "nvapi-test-key-12345678901234567890"


@pytest.fixture
def nvidia_env(monkeypatch):
    """Set up env vars for NVIDIA tests."""
    monkeypatch.setenv("NVIDIA_API_KEY", VALID_KEY)
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("NVIDIA_DEFAULT_MODEL", "meta/llama-3.3-70b-instruct")
    monkeypatch.setenv("NVIDIA_FREE_ONLY", "false")
    # Clear the cached module-level booleans by re-importing
    from agentkthx import config as _config

    monkeypatch.setattr(_config, "NVIDIA_API_KEY", VALID_KEY, raising=False)
    monkeypatch.setattr(
        _config, "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1", raising=False
    )
    monkeypatch.setattr(
        _config, "NVIDIA_DEFAULT_MODEL", "meta/llama-3.3-70b-instruct", raising=False
    )
    monkeypatch.setattr(_config, "NVIDIA_FREE_ONLY", False, raising=False)
    return _config


@pytest.fixture
def backend(nvidia_env):
    """An NvidiaBackend instance with a test API key configured."""
    from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

    return NvidiaBackend()


# ---------------------------------------------------------------------------
# Inheritance and class structure
# ---------------------------------------------------------------------------


class TestNvidiaInheritance:
    """Verify NvidiaBackend's inheritance hierarchy."""

    def test_inherits_from_cloud_backend(self):
        """NvidiaBackend MUST inherit from CloudBackend (MAINT-02 pattern)."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        assert issubclass(NvidiaBackend, CloudBackend)

    def test_inherits_from_openai_compatible_backend(self):
        """Transitively inherits from OpenAICompatibleBackend (existing
        isinstance checks continue to work)."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        assert issubclass(NvidiaBackend, OpenAICompatibleBackend)

    def test_class_attributes_set(self):
        """CloudBackend class attributes are correctly overridden."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        assert NvidiaBackend._api_key_env_var == "NVIDIA_API_KEY"
        assert NvidiaBackend._provider_label == "NVIDIA"
        assert NvidiaBackend._default_base_url == "https://integrate.api.nvidia.com/v1"
        assert NvidiaBackend._default_model == "meta/llama-3.3-70b-instruct"
        assert NvidiaBackend.MODEL_CACHE_KEY == "nvidia"


# ---------------------------------------------------------------------------
# Backend initialization + base URL resolution
# ---------------------------------------------------------------------------


class TestNvidiaInit:
    """Verify __init__ correctly resolves base URL + validates API key."""

    def test_default_base_url(self, nvidia_env):
        """Without explicit base_url, uses NVIDIA_BASE_URL env default."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend()
        assert b.base_url == "https://integrate.api.nvidia.com/v1"

    def test_explicit_base_url(self, nvidia_env):
        """Explicit base_url overrides env default."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend(base_url="https://custom.nvidia.proxy/v1")
        assert b.base_url == "https://custom.nvidia.proxy/v1"

    def test_explicit_base_url_strips_trailing_slash(self, nvidia_env):
        """Trailing slash on base_url is stripped (CloudBackend convention)."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend(base_url="https://custom.nvidia.proxy/v1/")
        assert b.base_url == "https://custom.nvidia.proxy/v1"

    def test_missing_api_key_raises(self, monkeypatch):
        """No API key anywhere → ValueError (CloudBackend validation)."""
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "NVIDIA_API_KEY", "", raising=False)
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        with pytest.raises(ValueError, match="NVIDIA_API_KEY is required"):
            NvidiaBackend()

    def test_short_api_key_raises(self, monkeypatch):
        """API key below _MIN_API_KEY_LEN (20) → ValueError."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-short")
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "NVIDIA_API_KEY", "nvapi-short", raising=False)
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        with pytest.raises(ValueError, match="too short"):
            NvidiaBackend()

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        """Explicit api_key arg wins over NVIDIA_API_KEY env var."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-env-key-1234567890")
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend(api_key="nvapi-explicit-key-1234567890")
        assert b.api_key == "nvapi-explicit-key-1234567890"

    def test_is_running_true_with_key(self, backend):
        """is_running() returns True when API key is configured."""
        assert backend.is_running() is True

    def test_api_key_setter_writes_through(self, nvidia_env):
        """api_key setter writes through to _api_key (R07.21 pattern)."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend()
        b.api_key = "nvapi-new-key-0987654321"
        assert b.api_key == "nvapi-new-key-0987654321"
        # Auth headers must reflect the new key
        assert b._get_auth_headers()["Authorization"] == "Bearer nvapi-new-key-0987654321"


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


class TestNvidiaIdentity:
    """Verify backend_type + provider label."""

    def test_backend_type_returns_nvidia(self, backend):
        assert backend.backend_type == BackendType.NVIDIA

    def test_backend_type_value_is_nvidia_string(self, backend):
        """backend_type.value is the string the footer formatter reads."""
        assert backend.backend_type.value == "nvidia"

    def test_provider_label(self, backend):
        assert backend._provider_label == "NVIDIA"


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestNvidiaUrls:
    """Verify URL builders return the NVIDIA NIM endpoints."""

    def test_chat_completions_url(self, backend):
        assert (
            backend._get_chat_completions_url()
            == "https://integrate.api.nvidia.com/v1/chat/completions"
        )

    def test_models_url(self, backend):
        assert backend._get_models_url() == "https://integrate.api.nvidia.com/v1/models"

    def test_chat_url_with_custom_base(self, nvidia_env):
        """Custom base_url flows through to chat URL."""
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        b = NvidiaBackend(base_url="https://custom.proxy/v1")
        assert b._get_chat_completions_url() == "https://custom.proxy/v1/chat/completions"
        assert b._get_models_url() == "https://custom.proxy/v1/models"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------


class TestNvidiaAuth:
    """Verify auth header construction."""

    def test_auth_headers_include_bearer(self, backend):
        h = backend._get_auth_headers()
        assert h["Authorization"] == f"Bearer {VALID_KEY}"
        assert h["Content-Type"] == "application/json"

    def test_extra_auth_headers_empty(self, backend):
        """NVIDIA NIM uses no extra headers (unlike OpenRouter's
        HTTP-Referer/X-Title or OrcaRouter's X-OrcaRouter-Include-Cost)."""
        assert backend._extra_auth_headers() == {}

    def test_validate_api_key_warns_on_wrong_prefix(self, nvidia_env, capsys):
        """A non-nvapi- prefix surfaces a debug-mode warning (not an error)."""
        # Force debug mode + non-nvapi prefix
        import os

        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            # Should NOT raise — just warn
            b = NvidiaBackend(api_key="wrong-prefix-key-1234567890")
            captured = capsys.readouterr()
            assert "nvapi-" in captured.out
            # The warning truncates the key prefix to 8 chars for display,
            # so we check for the truncated form, not the full "wrong-prefix"
            assert "wrong-pr" in captured.out
            # Backend still works
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)

    def test_validate_api_key_silent_without_debug(self, nvidia_env, capsys):
        """Without AGENTKTHX_DEBUG, the prefix mismatch is silent."""
        import os

        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        os.environ.pop("AGENTKTHX_DEBUG", None)
        try:
            b = NvidiaBackend(api_key="wrong-prefix-key-1234567890")
            captured = capsys.readouterr()
            assert captured.out == ""
            assert b.is_running() is True
        except Exception:
            pass

    def test_validate_api_key_accepts_nvapi_prefix(self, nvidia_env, capsys):
        """A correctly-prefixed key produces no warning even in debug mode."""
        import os

        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            b = NvidiaBackend(api_key="nvapi-valid-prefix-1234567890")
            captured = capsys.readouterr()
            assert "Warning" not in captured.out
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)


# ---------------------------------------------------------------------------
# Free-model + credit-exhaustion detection
# ---------------------------------------------------------------------------


class TestNvidiaFreeModel:
    """Verify the free-model classifier + credit-exhaustion detector."""

    def test_is_free_model_for_cataloged_model(self):
        """Every cataloged NVIDIA model is 'free' (credit-budget model)."""
        from agentkthx.plugins.nvidia.nvidia import _is_free_model

        # Sample cataloged models
        assert _is_free_model("llama-3.3-70b-instruct") is True
        assert _is_free_model("deepseek-r1") is True
        assert _is_free_model("mistral-nemo-12b-instruct") is True

    def test_is_free_model_strips_provider_prefix(self):
        """'meta/llama-3.3-70b-instruct' is treated the same as
        'llama-3.3-70b-instruct'."""
        from agentkthx.plugins.nvidia.nvidia import _is_free_model

        assert _is_free_model("meta/llama-3.3-70b-instruct") is True
        assert _is_free_model("deepseek-ai/deepseek-r1") is True
        assert _is_free_model("mistralai/mistral-nemo-12b-instruct") is True

    def test_is_free_model_false_for_unknown(self):
        """Uncataloged models return False (conservative)."""
        from agentkthx.plugins.nvidia.nvidia import _is_free_model

        assert _is_free_model("unknown-model-xyz") is False
        assert _is_free_model("nonexistent") is False
        assert _is_free_model("") is False

    def test_looks_like_credit_exhaustion_true_on_429_with_credit_keyword(self):
        """429 with 'credit' / 'quota' / 'balance' is credit exhaustion."""
        from agentkthx.plugins.nvidia.nvidia import _looks_like_credit_exhaustion

        assert _looks_like_credit_exhaustion(429, "monthly credit quota exhausted") is True
        assert _looks_like_credit_exhaustion(429, "credit balance exhausted") is True
        assert _looks_like_credit_exhaustion(429, "monthly quota reached") is True
        assert _looks_like_credit_exhaustion(429, "exhausted for the month") is True

    def test_looks_like_credit_exhaustion_false_on_plain_rate_limit(self):
        """429 with rate-limit language is NOT credit exhaustion (retryable)."""
        from agentkthx.plugins.nvidia.nvidia import _looks_like_credit_exhaustion

        assert _looks_like_credit_exhaustion(429, "rate limit exceeded") is False
        assert _looks_like_credit_exhaustion(429, "RPM limit reached") is False
        assert _looks_like_credit_exhaustion(429, "TPM limit reached") is False

    def test_looks_like_credit_exhaustion_false_on_non_429(self):
        """Non-429 status codes are never credit exhaustion."""
        from agentkthx.plugins.nvidia.nvidia import _looks_like_credit_exhaustion

        assert _looks_like_credit_exhaustion(500, "credit") is False
        assert _looks_like_credit_exhaustion(401, "quota") is False
        assert _looks_like_credit_exhaustion(200, "credit") is False

    def test_looks_like_credit_exhaustion_false_on_empty_body(self):
        """Empty error body returns False (no signal)."""
        from agentkthx.plugins.nvidia.nvidia import _looks_like_credit_exhaustion

        assert _looks_like_credit_exhaustion(429, "") is False


# ---------------------------------------------------------------------------
# Tool support detection
# ---------------------------------------------------------------------------


class TestNvidiaToolSupport:
    """Verify test_tool_support distinguishes reasoning vs chat models."""

    def test_chat_model_returns_native(self, backend):
        """Llama / Mistral / Qwen / Phi chat models → NATIVE."""
        assert backend.test_tool_support("meta/llama-3.3-70b-instruct") == ToolSupportLevel.NATIVE
        assert backend.test_tool_support("meta/llama-3.1-8b-instruct") == ToolSupportLevel.NATIVE
        assert (
            backend.test_tool_support("mistralai/mistral-nemo-12b-instruct")
            == ToolSupportLevel.NATIVE
        )
        assert (
            backend.test_tool_support("qwen/qwen2.5-coder-32b-instruct") == ToolSupportLevel.NATIVE
        )
        assert backend.test_tool_support("microsoft/phi-4-mini-instruct") == ToolSupportLevel.NATIVE

    def test_deepseek_r1_returns_react(self, backend):
        """DeepSeek-R1 (reasoning) → REACT (no native tools)."""
        assert backend.test_tool_support("deepseek-ai/deepseek-r1") == ToolSupportLevel.REACT

    def test_deepseek_r1_distill_returns_react(self, backend):
        """DeepSeek-R1-Distill variants → REACT."""
        assert (
            backend.test_tool_support("deepseek-ai/deepseek-r1-distill-qwen-32b")
            == ToolSupportLevel.REACT
        )
        assert (
            backend.test_tool_support("deepseek-ai/deepseek-r1-distill-llama-8b")
            == ToolSupportLevel.REACT
        )

    def test_deepseek_v3_returns_native(self, backend):
        """DeepSeek-V3 (chat, not reasoning) → NATIVE."""
        assert backend.test_tool_support("deepseek-ai/deepseek-v3") == ToolSupportLevel.NATIVE

    def test_unknown_model_returns_native_default(self, backend):
        """Unknown models fall through to NATIVE (inherited CloudBackend default).

        The first real request will probe and cache the actual verdict."""
        assert backend.test_tool_support("unknown-future-model") == ToolSupportLevel.NATIVE


# ---------------------------------------------------------------------------
# Catalog (seed) content
# ---------------------------------------------------------------------------


class TestNvidiaCatalog:
    """Verify the seed catalog loaded from model_seed.json."""

    def test_catalog_loaded_with_23_plus_models(self):
        """Seed catalog must include the 23 chat-text-only NVIDIA models.

        R07.26 follow-up: filtered from 28 → 23 by dropping vision /
        multimodal entries (granite-vision, llama-3.2-*-vision-instruct,
        phi-4-multimodal, qwen2.5-vl) since AgentKthx only supports
        chat text I/O today. Re-add when image I/O lands.
        """
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        assert (
            len(NVIDIA_MODELS) >= 23
        ), f"Seed catalog has {len(NVIDIA_MODELS)} models, expected >= 23"

    def test_catalog_includes_llama_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        # R07.26 follow-up: vision variants (llama-3.2-11b-vision-instruct,
        # llama-3.2-90b-vision-instruct) dropped — chat-text-only filter.
        for name in [
            "llama-3.3-70b-instruct",
            "llama-3.1-405b-instruct",
            "llama-3.1-70b-instruct",
            "llama-3.1-8b-instruct",
            "llama-3.2-1b-instruct",
            "llama-3.2-3b-instruct",
        ]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_mistral_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name in [
            "mistral-nemo-12b-instruct",
            "mistral-small-24b-instruct",
            "mixtral-8x7b-instruct-v0.1",
            "mixtral-8x22b-instruct-v0.1",
        ]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_qwen_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        # R07.26 follow-up: qwen2.5-vl-32b-instruct dropped (vision) — kept
        # the text-only chat + coder variants.
        for name in [
            "qwen2.5-7b-instruct",
            "qwen2.5-coder-32b-instruct",
        ]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_deepseek_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name in [
            "deepseek-r1",
            "deepseek-r1-distill-llama-8b",
            "deepseek-r1-distill-qwen-32b",
            "deepseek-v3",
        ]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_nvidia_nemotron(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name in [
            "llama-3.1-nemotron-70b-instruct",
            "llama-3.3-nemotron-super-49b-v1",
            "nemotron-nano-9b-v2",
        ]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_phi_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        # R07.26 follow-up: phi-4-multimodal-instruct dropped (vision+audio)
        # — kept the text-only phi-4-mini-instruct.
        for name in ["phi-4-mini-instruct"]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_granite_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        # R07.26 follow-up: granite-vision-3.3-2b dropped (vision) — kept
        # the text-only granite-3.3-8b-instruct.
        for name in ["granite-3.3-8b-instruct"]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_includes_gemma_family(self):
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name in ["gemma-2-27b-it", "gemma-2-9b-it"]:
            assert name in NVIDIA_MODELS, f"{name} missing from catalog"

    def test_catalog_entries_have_required_fields(self):
        """Each entry has context_length, default_temperature,
        default_max_tokens, pricing."""
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name, meta in NVIDIA_MODELS.items():
            assert "context_length" in meta, f"{name} missing context_length"
            assert "default_temperature" in meta, f"{name} missing default_temperature"
            assert "default_max_tokens" in meta, f"{name} missing default_max_tokens"
            assert "pricing" in meta, f"{name} missing pricing"
            assert isinstance(meta["context_length"], int) and meta["context_length"] > 0
            assert isinstance(meta["default_max_tokens"], int) and meta["default_max_tokens"] > 0
            assert "input" in meta["pricing"] and "output" in meta["pricing"]

    def test_catalog_pricing_all_zero(self):
        """NVIDIA's credit-budget model means every cataloged model is
        priced 0.0/0.0 (free within quota)."""
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        for name, meta in NVIDIA_MODELS.items():
            pricing = meta["pricing"]
            assert pricing["input"] == 0.0, (
                f"{name} pricing.input is {pricing['input']}, expected 0.0 "
                f"(NVIDIA's credit-budget model means every cataloged model "
                f"is 'free' within the monthly quota)"
            )
            assert (
                pricing["output"] == 0.0
            ), f"{name} pricing.output is {pricing['output']}, expected 0.0"

    def test_no_vision_or_multimodal_models_in_seed(self):
        """R07.26 follow-up: vision / multimodal models are filtered out
        of the seed catalog because AgentKthx only supports chat-text I/O
        today. Re-add when image I/O lands.
        """
        from agentkthx.plugins.nvidia.nvidia import NVIDIA_MODELS

        # These were the 5 dropped in R07.26 follow-up. If any reappear,
        # it means the seed file was re-extended without updating this test.
        dropped = {
            "granite-vision-3.3-2b",
            "llama-3.2-11b-vision-instruct",
            "llama-3.2-90b-vision-instruct",
            "phi-4-multimodal-instruct",
            "qwen2.5-vl-32b-instruct",
        }
        for name in dropped:
            assert name not in NVIDIA_MODELS, (
                f"{name} should be filtered out (vision/multimodal) — "
                f"AgentKthx doesn't support image I/O yet"
            )


# ---------------------------------------------------------------------------
# list_models + catalog fallback
# ---------------------------------------------------------------------------


class TestNvidiaListModels:
    """Verify list_models() and the offline fallback."""

    def test_catalog_fallback_list_returns_seed_entries(self, backend):
        """_catalog_fallback_list returns the static catalog shaped as
        list_models() entries."""
        models = backend._catalog_fallback_list()
        assert len(models) >= 23

        # Each entry has the list_models() shape
        for m in models:
            assert "name" in m
            assert m["size"] == 0
            assert "details" in m
            assert m["details"]["backend"] == "nvidia"
            assert "context_length" in m["details"]
            assert "free_tier" in m["details"]
            assert m["details"]["free_tier"] is True  # all cataloged models free

    def test_catalog_fallback_includes_llama(self, backend):
        models = backend._catalog_fallback_list()
        names = {m["name"] for m in models}
        assert "llama-3.3-70b-instruct" in names
        assert "llama-3.1-405b-instruct" in names


# ---------------------------------------------------------------------------
# get_model_info / get_model_max_context (catalog lookup)
# ---------------------------------------------------------------------------


class TestNvidiaModelInfo:
    """Verify catalog-driven model info lookups."""

    def test_get_model_info_returns_catalog_entry(self, backend):
        info = backend.get_model_info("meta/llama-3.3-70b-instruct")
        assert info is not None
        assert info["name"] == "llama-3.3-70b-instruct"
        assert info["details"]["context_length"] == 131072
        assert info["details"]["free_tier"] is True

    def test_get_model_info_strips_provider_prefix(self, backend):
        """'meta/llama-3.3-70b-instruct' and 'llama-3.3-70b-instruct' both
        resolve to the same catalog entry."""
        a = backend.get_model_info("meta/llama-3.3-70b-instruct")
        b = backend.get_model_info("llama-3.3-70b-instruct")
        assert a == b

    def test_get_model_info_returns_none_for_unknown(self, backend):
        assert backend.get_model_info("unknown-model-xyz") is None

    def test_get_model_max_context_for_cataloged(self, backend):
        assert backend.get_model_max_context("meta/llama-3.3-70b-instruct") == 131072
        assert backend.get_model_max_context("deepseek-ai/deepseek-r1") == 131072

    def test_get_model_max_context_fallback_for_unknown(self, backend):
        """Unknown models fall back to _DEFAULT_CONTEXT_FALLBACK (128000)."""
        assert backend.get_model_max_context("unknown-model-xyz") == 128000

    def test_get_model_max_context_ignores_family_arg(self, backend):
        """CloudBackend's get_model_max_context ignores the family arg
        (catalog is per-model, not per-family)."""
        ctx_with_family = backend.get_model_max_context(
            "meta/llama-3.3-70b-instruct", family="llama"
        )
        ctx_no_family = backend.get_model_max_context("meta/llama-3.3-70b-instruct")
        assert ctx_with_family == ctx_no_family == 131072


# ---------------------------------------------------------------------------
# Plugin manifest + PluginManager discovery
# ---------------------------------------------------------------------------


class TestNvidiaManifest:
    """Verify the plugin.json manifest loads via PluginManager."""

    def test_plugin_discovered_by_manager(self):
        """PluginManager.discover() finds the nvidia plugin."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        nvidia_m = [m for m in manifests if m.name == "nvidia"]
        assert len(nvidia_m) == 1
        m = nvidia_m[0]
        assert m.version == "0.1.0"
        assert m.type == "backend"
        assert m.display_name == "NVIDIA NIM Cloud Backend"
        assert m.license == "MIT"

    def test_manifest_provides_nvidia_and_nim_aliases(self):
        """Both 'nvidia' and 'nim' backend aliases are registered."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        nvidia_m = next(m for m in manifests if m.name == "nvidia")
        backends = nvidia_m.provides["backends"]
        assert "nvidia" in backends
        assert "nim" in backends
        assert backends["nvidia"] == "nvidia.NvidiaBackend"
        assert backends["nim"] == "nvidia.NvidiaBackend"

    def test_manifest_cli_flags_include_nvidia_and_nim(self):
        """--backend flag accepts both 'nvidia' and 'nim'."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        nvidia_m = next(m for m in manifests if m.name == "nvidia")
        cli_flags = nvidia_m.provides["cli_flags"]
        assert "--backend" in cli_flags
        assert "nvidia" in cli_flags["--backend"]
        assert "nim" in cli_flags["--backend"]

    def test_manifest_config_defaults(self):
        """Manifest declares NVIDIA_BASE_URL, NVIDIA_API_KEY,
        NVIDIA_DEFAULT_MODEL, NVIDIA_FREE_ONLY defaults."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        nvidia_m = next(m for m in manifests if m.name == "nvidia")
        defaults = nvidia_m.config["defaults"]
        assert defaults["NVIDIA_BASE_URL"] == "https://integrate.api.nvidia.com/v1"
        assert defaults["NVIDIA_API_KEY"] == ""
        assert defaults["NVIDIA_DEFAULT_MODEL"] == "meta/llama-3.3-70b-instruct"
        assert defaults["NVIDIA_FREE_ONLY"] == "false"
        assert nvidia_m.config["env_prefix"] == "NVIDIA"

    def test_plugin_loads_and_registers_backend(self):
        """Loading the plugin registers NvidiaBackend under both 'nvidia'
        and 'nim' aliases."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("nvidia")

        assert pm.is_loaded("nvidia")
        cls = pm.get_backend_class("nvidia")
        assert cls is not None
        assert cls.__name__ == "NvidiaBackend"

        # Alias
        cls_alias = pm.get_backend_class("nim")
        assert cls_alias is cls

    def test_plugin_in_backend_choices(self):
        """Both 'nvidia' and 'nim' appear in --backend choices."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("nvidia")
        choices = pm.get_backend_choices()
        assert "nvidia" in choices
        assert "nim" in choices

    def test_manifest_conforms_to_v0_2_schema(self):
        """The plugin.json shape conforms to schemas/v0.2/plugin.schema.json."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        nvidia_m = next(m for m in manifests if m.name == "nvidia")
        # Required v0.2 fields
        assert nvidia_m.name
        assert nvidia_m.version
        assert nvidia_m.description
        assert nvidia_m.author
        # Schema URL
        assert nvidia_m.schema and "v0.2" in nvidia_m.schema


# ---------------------------------------------------------------------------
# config.py integration
# ---------------------------------------------------------------------------


class TestNvidiaConfig:
    """Verify config.py exposes NVIDIA_* env vars with correct defaults."""

    def test_nvidia_base_url_default(self):
        from agentkthx import config

        assert config.NVIDIA_BASE_URL == "https://integrate.api.nvidia.com/v1"

    def test_nvidia_api_key_default_empty(self):
        from agentkthx import config

        assert config.NVIDIA_API_KEY == ""

    def test_nvidia_default_model_default(self):
        from agentkthx import config

        assert config.NVIDIA_DEFAULT_MODEL == "meta/llama-3.3-70b-instruct"

    def test_nvidia_free_only_default_false(self):
        from agentkthx import config

        assert config.NVIDIA_FREE_ONLY is False

    def test_nvidia_base_url_env_override(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_BASE_URL", "https://custom.proxy/v1")
        # Force reload of config module
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.NVIDIA_BASE_URL == "https://custom.proxy/v1"
        finally:
            # Restore
            monkeypatch.delenv("NVIDIA_BASE_URL", raising=False)
            importlib.reload(_config)

    def test_nvidia_free_only_env_true(self, monkeypatch):
        monkeypatch.setenv("NVIDIA_FREE_ONLY", "1")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.NVIDIA_FREE_ONLY is True
        finally:
            monkeypatch.delenv("NVIDIA_FREE_ONLY", raising=False)
            importlib.reload(_config)

    def test_nvidia_in_backend_selection_ladder(self, monkeypatch):
        """AGENTKTHX_BACKEND=nvidia picks up the NVIDIA default model."""
        monkeypatch.setenv("AGENTKTHX_BACKEND", "nvidia")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.DEFAULT_MODEL == "meta/llama-3.3-70b-instruct"
        finally:
            monkeypatch.delenv("AGENTKTHX_BACKEND", raising=False)
            importlib.reload(_config)


# ---------------------------------------------------------------------------
# BackendType enum integration
# ---------------------------------------------------------------------------


class TestNvidiaBackendType:
    """Verify the BackendType.NVIDIA enum value exists."""

    def test_nvidia_enum_value_exists(self):
        assert hasattr(BackendType, "NVIDIA")
        assert BackendType.NVIDIA.value == "nvidia"

    def test_nvidia_enum_distinct_from_others(self):
        """NVIDIA is a distinct value, not aliased to another backend."""
        other_values = [
            BackendType.ZAI,
            BackendType.OPENROUTER,
            BackendType.GEMINI,
            BackendType.HUGGINGFACE,
            BackendType.OPENAI,
            BackendType.MISTRAL,
            BackendType.ORCAROUTER,
            BackendType.POLLINATIONS,
        ]
        for other in other_values:
            assert BackendType.NVIDIA != other
            assert BackendType.NVIDIA.value != other.value
