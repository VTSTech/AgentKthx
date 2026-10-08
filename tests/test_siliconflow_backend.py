"""
SiliconFlow plugin (R07.29) — backend regression tests.

Verifies that the SiliconFlowBackend (the 11th cloud backend, scaffolded
from the R07.28-hardened CloudBackend base — the first scaffold to copy
the post-MAINT-28/ROB-42 patterns) correctly implements:

  - CloudBackend inheritance (issubclass checks + shared-transport
    identity pins — the MAINT-28 batch-2 lift means the request loops
    are INHERITED, not copied)
  - Class-attribute provider identity (_api_key_env_var, _provider_label,
    _default_base_url, _default_model, MODEL_CACHE_KEY, _error_brand)
  - __init__ base-URL resolution + API-key validation
  - ``_get_chat_completions_url()`` / ``_get_models_url()`` return the
    SiliconFlow endpoints (and honor the CN TLD override)
  - ``_get_auth_headers()`` includes Bearer + Content-Type
  - ``_validate_api_key`` warns (not errors) on non-sk- prefix
  - ``_catalog_model_key()`` keeps FULL prefixed IDs (NVIDIA contract)
  - ``_is_free_model()`` — per-model pricing (the only FREE_ONLY that
    actually filters among the four documented providers; currently
    False for EVERY model — no free tier, R07.29 billing probe)
  - ``_is_non_chat_model()`` blocklist (OCR, VL, Omni, GLM-*V, MT,
    embed, rerank, media)
  - ``_looks_like_balance_exhaustion()`` distinguishes transient TPM
    429s from permanent balance exhaustion on 429 AND on 402 (live
    R07.29 smoke evidence: 402 "Sorry, your account balance is
    insufficient"), with the transient indicators checked FIRST so
    "TPM limit reached" can never trip on the word "limit" — the
    Cloudflare "limit" drift lesson
  - ``test_tool_support()`` returns REACT for reasoning models (R1
    family, *-Thinking) and vision models (VL/Omni/GLM-*V), NATIVE for
    chat models — name-pattern only, no tool_cache interaction
  - ROB-42 contracts: failed fetch never persists the seed as
    source="api"; stale-first service; malformed shape raises; success
    still stores; the non-chat blocklist filters live results
  - ``_apply_free_only()`` filters the catalog to the free models
    (currently an EMPTY filter result — no free models exist)
  - ``generate()`` REJECTS under SILICONFLOW_FREE_ONLY before any
    billable request (a flag that promises "free only" must never
    emit a billable request — the R07.29 billing-probe lesson)
  - Quota-429 fast-fail through the shared classifier (transient
    TPM 429 retries; balance 429 raises the clear message)
  - 401/404 remediation texts through _STATUS_REMEDIATIONS
  - Plugin manifest loads via PluginManager (siliconflow + sf aliases)
  - Plugin manifest shape conforms to plugin spec v0.2
  - config.py exposes SILICONFLOW_* env vars with correct defaults
  - cli/auth.py registers the SiliconFlow picker rows
  - BackendType.SILICONFLOW exists and is distinct

Mirrors the structure of tests/test_nvidia_backend.py (the prior
cloud-backend regression suite — same inheritance / class-attribute /
manifest shape checks) plus the ROB-42 contract pins from
tests/test_r07_28_batch1_closures.py.
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.cloud_base import CloudBackend  # noqa: E402
from agentkthx.backends.openai_compat import OpenAICompatibleBackend  # noqa: E402
from agentkthx.core.types import BackendType, ToolSupportLevel  # noqa: E402

# ---------------------------------------------------------------------------
# Live-catalog snapshot (R07.29 probe, Oct 2026) — the 58 chat-kept model
# IDs from GET /v1/models after the non-chat blocklist. The seed catalog
# must ONLY carry models confirmed on this snapshot: a stale entry that
# would 404 at generate time fails test_seed_only_carries_live_models
# here instead. Re-sync after catalog drift via
# scripts/probe_siliconflow.sh Section 5 (seed drift).
# ---------------------------------------------------------------------------
_LIVE_KEPT_SNAPSHOT: frozenset[str] = frozenset(
    {
        "ByteDance-Seed/Seed-OSS-36B-Instruct",
        "FunAudioLLM/CosyVoice2-0.5B",
        "IndexTeam/IndexTTS-2",
        "Kev-4B",
        "MiniMaxAI/MiniMax-M3",
        "Qwen/Qwen-Image",
        "Qwen/Qwen-Image-Edit",
        "Qwen/Qwen2.5-7B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen3-14B",
        "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "Qwen/Qwen3-32B",
        "Qwen/Qwen3-8B",
        "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "Qwen/Qwen3.5-122B-A10B",
        "Qwen/Qwen3.5-27B",
        "Qwen/Qwen3.5-35B-A3B",
        "Qwen/Qwen3.5-9B",
        "Qwen/Qwen3.6-27B",
        "Qwen/Qwen3.6-35B-A3B",
        "Qwen/Qwen3.8-2.4T-A95B",
        "Qwen/Qwen3.8-27B",
        "Tongyi-MAI/Z-Image-Turbo",
        "Wan-AI/Wan2.2-I2V-A14B",
        "Wan-AI/Wan2.2-T2V-A14B",
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-V3.1",
        "deepseek-ai/DeepSeek-V3.1-Terminus",
        "deepseek-ai/DeepSeek-V3.2",
        "deepseek-ai/DeepSeek-V3.2-Exp",
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Pro-0813",
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "fishaudio/fish-speech-1.5",
        "google/gemma-4-12B-it",
        "google/gemma-4-26B-A4B-it",
        "google/gemma-4-31B-it",
        "inclusionAI/Ling-flash-2.0",
        "meituan-longcat/LongCat-2.0",
        "moonshotai/Kimi-K2.5",
        "moonshotai/Kimi-K2.6",
        "moonshotai/Kimi-K2.7-Code",
        "moonshotai/Kimi-K3",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "stepfun-ai/Step-3.5-Flash",
        "tencent/Hunyuan-A13B-Instruct",
        "tencent/Hy3",
        "tencent/Hy4-preview",
        "zai-org/GLM-4.5-Air",
        "zai-org/GLM-5",
        "zai-org/GLM-5.1",
        "zai-org/GLM-5.2",
        "zai-org/GLM-5.3",
        "zai-org/GLM-5.3-Flash",
    }
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_KEY = "sk-test-key-12345678901234567890"


@pytest.fixture
def siliconflow_env(monkeypatch):
    """Set up env vars for SiliconFlow tests."""
    monkeypatch.setenv("SILICONFLOW_API_KEY", VALID_KEY)
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1")
    monkeypatch.setenv("SILICONFLOW_DEFAULT_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "false")
    # Clear the cached module-level booleans by re-importing
    from agentkthx import config as _config

    monkeypatch.setattr(_config, "SILICONFLOW_API_KEY", VALID_KEY, raising=False)
    monkeypatch.setattr(
        _config, "SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1", raising=False
    )
    monkeypatch.setattr(_config, "SILICONFLOW_DEFAULT_MODEL", "Qwen/Qwen3-8B", raising=False)
    monkeypatch.setattr(_config, "SILICONFLOW_FREE_ONLY", False, raising=False)
    monkeypatch.setattr(_config, "SILICONFLOW_FREE_FALLBACK_MODEL", "Qwen/Qwen3-8B", raising=False)
    return _config


@pytest.fixture
def backend(monkeypatch):
    """A SiliconFlowBackend instance with a test API key configured."""
    from agentkthx import config as _config
    from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

    monkeypatch.setenv("SILICONFLOW_API_KEY", VALID_KEY)
    monkeypatch.setattr(_config, "SILICONFLOW_API_KEY", VALID_KEY, raising=False)
    return SiliconFlowBackend()


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent model_catalog.json cache at a temp file."""
    from agentkthx import model_cache as _mc

    monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")
    return _mc


def _no_sleep(monkeypatch) -> None:
    """Neutralize backoff sleeps (shared handler + base helpers)."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    """Build a real HTTPError whose .read() yields ``body``.

    Passing fp=io.BytesIO(...) makes ``e.fp`` truthy so the shared
    handler's ``e.read() if e.fp else b""`` branch reads the body —
    the same contract as a real urllib 4xx response.
    """
    return urllib.error.HTTPError(
        url="https://unit.test",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body.encode("utf-8")),
    )


# ---------------------------------------------------------------------------
# Inheritance and class structure
# ---------------------------------------------------------------------------


class TestSiliconflowInheritance:
    """Verify SiliconFlowBackend's inheritance hierarchy."""

    def test_inherits_from_cloud_backend(self):
        """SiliconFlowBackend MUST inherit from CloudBackend (MAINT-02 pattern)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        assert issubclass(SiliconFlowBackend, CloudBackend)

    def test_inherits_from_openai_compatible_backend(self):
        """Transitively inherits from OpenAICompatibleBackend (existing
        isinstance checks continue to work)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        assert issubclass(SiliconFlowBackend, OpenAICompatibleBackend)

    def test_class_attributes_set(self):
        """CloudBackend class attributes are correctly overridden."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        assert SiliconFlowBackend._api_key_env_var == "SILICONFLOW_API_KEY"
        assert SiliconFlowBackend._provider_label == "SiliconFlow"
        assert SiliconFlowBackend._default_base_url == "https://api.siliconflow.com/v1"
        assert SiliconFlowBackend._default_model == "Qwen/Qwen3-8B"
        assert SiliconFlowBackend.MODEL_CACHE_KEY == "siliconflow"
        assert SiliconFlowBackend._error_brand == "SiliconFlow"

    def test_request_loops_are_inherited_not_copied(self):
        """MAINT-28 batch-2 contract: the first scaffold after the R07.28
        lift must NOT copy the request loops — _make_api_request /
        _iter_sse_lines / generate_stream / _jev_call_completions are
        the shared CloudBackend implementations."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        assert SiliconFlowBackend._make_api_request is CloudBackend._make_api_request
        assert SiliconFlowBackend._iter_sse_lines is CloudBackend._iter_sse_lines
        assert SiliconFlowBackend.generate_stream is CloudBackend.generate_stream
        assert SiliconFlowBackend._jev_call_completions is CloudBackend._jev_call_completions

    def test_catalog_model_key_hook_not_duplicated(self):
        """The catalog-key hook (not 4 per-method overrides) is the
        normalization surface (R07.28 batch-2 contract)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        assert SiliconFlowBackend.get_model_info is CloudBackend.get_model_info
        assert SiliconFlowBackend._get_model_defaults is CloudBackend._get_model_defaults
        assert SiliconFlowBackend.get_model_max_context is CloudBackend.get_model_max_context
        # ...but _catalog_model_key IS overridden (full prefixed IDs)
        assert SiliconFlowBackend._catalog_model_key is not CloudBackend._catalog_model_key


# ---------------------------------------------------------------------------
# Backend initialization + base URL resolution
# ---------------------------------------------------------------------------


class TestSiliconflowInit:
    """Verify __init__ correctly resolves base URL + validates API key."""

    def test_default_base_url(self, siliconflow_env):
        """Without explicit base_url, uses SILICONFLOW_BASE_URL env default."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend()
        assert b.base_url == "https://api.siliconflow.com/v1"

    def test_explicit_base_url(self, siliconflow_env):
        """Explicit base_url overrides env default."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend(base_url="https://custom.siliconflow.proxy/v1")
        assert b.base_url == "https://custom.siliconflow.proxy/v1"

    def test_explicit_base_url_strips_trailing_slash(self, siliconflow_env):
        """Trailing slash on base_url is stripped (CloudBackend convention)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend(base_url="https://custom.siliconflow.proxy/v1/")
        assert b.base_url == "https://custom.siliconflow.proxy/v1"

    def test_missing_api_key_raises(self, monkeypatch):
        """No API key anywhere → ValueError (CloudBackend validation)."""
        monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "SILICONFLOW_API_KEY", "", raising=False)
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        with pytest.raises(ValueError, match="SILICONFLOW_API_KEY is required"):
            SiliconFlowBackend()

    def test_short_api_key_raises(self, monkeypatch):
        """API key below _MIN_API_KEY_LEN (20) → ValueError."""
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-short")
        from agentkthx import config as _config

        monkeypatch.setattr(_config, "SILICONFLOW_API_KEY", "sk-short", raising=False)
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        with pytest.raises(ValueError, match="too short"):
            SiliconFlowBackend()

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        """Explicit api_key arg wins over SILICONFLOW_API_KEY env var."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-env-key-1234567890")
        b = SiliconFlowBackend(api_key="sk-explicit-key-1234567890")
        assert b.api_key == "sk-explicit-key-1234567890"

    def test_is_running_true_with_key(self, backend):
        """is_running() returns True when API key is configured."""
        assert backend.is_running() is True

    def test_api_key_setter_writes_through(self, siliconflow_env):
        """api_key setter writes through to _api_key (R07.21 pattern)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend()
        b.api_key = "sk-new-key-0987654321abcdef"
        assert b.api_key == "sk-new-key-0987654321abcdef"
        # Auth headers must reflect the new key
        assert b._get_auth_headers()["Authorization"] == "Bearer sk-new-key-0987654321abcdef"


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


class TestSiliconflowIdentity:
    """Verify backend_type + provider label."""

    def test_backend_type_returns_siliconflow(self, backend):
        assert backend.backend_type == BackendType.SILICONFLOW

    def test_backend_type_value_is_siliconflow_string(self, backend):
        """backend_type.value is the string the footer formatter reads."""
        assert backend.backend_type.value == "siliconflow"

    def test_provider_label(self, backend):
        assert backend._provider_label == "SiliconFlow"


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestSiliconflowUrls:
    """Verify URL builders return the SiliconFlow endpoints."""

    def test_chat_completions_url(self, backend):
        assert (
            backend._get_chat_completions_url() == "https://api.siliconflow.com/v1/chat/completions"
        )

    def test_models_url(self, backend):
        assert backend._get_models_url() == "https://api.siliconflow.com/v1/models"

    def test_chat_url_with_custom_base(self, siliconflow_env):
        """Custom base_url flows through to chat URL."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend(base_url="https://custom.proxy/v1")
        assert b._get_chat_completions_url() == "https://custom.proxy/v1/chat/completions"
        assert b._get_models_url() == "https://custom.proxy/v1/models"

    def test_cn_tld_base_url(self, siliconflow_env):
        """The China-domestic TLD override flows through cleanly (the
        documented low-latency path for users inside China)."""
        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        b = SiliconFlowBackend(base_url="https://api.siliconflow.cn/v1")
        assert b._get_chat_completions_url() == "https://api.siliconflow.cn/v1/chat/completions"
        assert b._get_models_url() == "https://api.siliconflow.cn/v1/models"


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------


class TestSiliconflowAuth:
    """Verify auth header construction."""

    def test_auth_headers_include_bearer(self, backend):
        h = backend._get_auth_headers()
        assert h["Authorization"] == f"Bearer {VALID_KEY}"
        assert h["Content-Type"] == "application/json"

    def test_extra_auth_headers_empty(self, backend):
        """SiliconFlow uses no extra headers (plain Bearer auth — unlike
        OpenRouter's HTTP-Referer/X-Title)."""
        assert backend._extra_auth_headers() == {}

    def test_validate_api_key_warns_on_wrong_prefix(self, siliconflow_env, capsys):
        """A non-sk- prefix surfaces a debug-mode warning (not an error)."""
        import os

        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            # Should NOT raise — just warn
            b = SiliconFlowBackend(api_key="wrong-prefix-key-1234567890")
            captured = capsys.readouterr()
            assert "sk-" in captured.out
            assert "SILICONFLOW_API_KEY" in captured.out
            # The warning truncates the key prefix to 8 chars for display
            assert "wrong-pr" in captured.out
            # Backend still works
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)

    def test_validate_api_key_silent_without_debug(self, siliconflow_env, capsys):
        """Without AGENTKTHX_DEBUG, the prefix mismatch is silent."""
        import os

        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        os.environ.pop("AGENTKTHX_DEBUG", None)
        b = SiliconFlowBackend(api_key="wrong-prefix-key-1234567890")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert b.is_running() is True

    def test_validate_api_key_accepts_sk_prefix(self, siliconflow_env, capsys):
        """A correctly-prefixed key produces no warning even in debug mode."""
        import os

        from agentkthx.plugins.siliconflow.siliconflow import SiliconFlowBackend

        os.environ["AGENTKTHX_DEBUG"] = "1"
        try:
            b = SiliconFlowBackend(api_key="sk-valid-prefix-1234567890")
            captured = capsys.readouterr()
            assert "Warning" not in captured.out
            assert b.is_running() is True
        finally:
            os.environ.pop("AGENTKTHX_DEBUG", None)


# ---------------------------------------------------------------------------
# Free-model + balance-exhaustion detection
# ---------------------------------------------------------------------------


class TestSiliconflowFreeModel:
    """Verify the free-model classifier (per-model pricing)."""

    def test_is_free_model_false_for_qwen3_8b(self):
        """Qwen/Qwen3-8B is NOT free — billing-verified Oct 2026 (a
        235-input-token request cost $0.000014, ≈$0.06/1M input; the
        earlier "0.563K tokens → $0.0000" console row was 4-decimal
        display rounding of ≈$0.0000338). Its seed entry carries
        input 0.06, so the classifier must resolve False."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_free_model

        assert _is_free_model("Qwen/Qwen3-8B") is False

    def test_no_verified_free_models_on_intl_api(self):
        """The verified-free set is EMPTY — the SiliconFlow API bills
        every model (R07.29 billing probe). The constant stays as the
        ground-truth extension point for a future free tier."""
        from agentkthx.plugins.siliconflow.siliconflow import (
            _SILICONFLOW_FREE_CHAT_MODELS,
        )

        assert _SILICONFLOW_FREE_CHAT_MODELS == frozenset()

    def test_is_free_model_false_for_dead_free_model(self):
        """DeepSeek-R1-Distill-Qwen-7B (documented free, but no longer on
        /v1/models per the R07.29 probe) is pruned from BOTH the seed and
        the verified free set — it must resolve False, never silently
        resurrect as a FREE_ONLY swap target that would 404."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_free_model

        assert _is_free_model("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B") is False

    def test_is_free_model_false_for_paid(self):
        """Paid models (no pricing key — never fabricated) resolve False."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_free_model

        assert _is_free_model("deepseek-ai/DeepSeek-V3") is False
        assert _is_free_model("zai-org/GLM-5") is False
        assert _is_free_model("Qwen/Qwen3-14B") is False

    def test_is_free_model_false_for_unknown(self):
        """Uncataloged models return False (conservative — unknown
        pricing is paid until verified)."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_free_model

        assert _is_free_model("unknown-model-xyz") is False
        assert _is_free_model("Qwen/Qwen3-99B") is False

    def test_is_free_model_full_prefix_contract(self):
        """Catalog keys on FULL prefixed IDs — the model arg must match
        the catalog key exactly (no prefix stripping, NVIDIA contract)."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_free_model

        # Bare post-slash segments do NOT match (no prefix stripping)
        assert _is_free_model("Qwen3-8B") is False
        assert _is_free_model("DeepSeek-R1-Distill-Qwen-7B") is False

    def test_ocr_free_model_not_in_catalog(self):
        """DeepSeek-OCR is OCR, not chat — deliberately absent from the
        seed catalog (and no longer on the live endpoint, R07.29 probe)."""
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        assert "deepseek-ai/DeepSeek-OCR" not in SILICONFLOW_MODELS

    def test_seed_catalog_size(self):
        """The R07.29 seed carries the 30 live-verified chat models (the
        27 stale entries that would 404 are pruned)."""
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        assert len(SILICONFLOW_MODELS) == 30

    def test_seed_only_carries_live_models(self):
        """R07.29 probe contract: every seed entry is confirmed on the
        live /v1/models snapshot (58 chat-kept IDs). A stale entry
        re-landing in the seed fails here instead of 404-ing at
        generate time."""
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        stale = set(SILICONFLOW_MODELS) - _LIVE_KEPT_SNAPSHOT
        assert not stale, f"seed carries non-live models: {sorted(stale)}"


class TestSiliconflowBalanceExhaustion:
    """Verify the balance-vs-TPM 429 classifier."""

    def test_tpm_rate_limit_is_transient(self):
        """'TPM limit reached' / 'rate limiting' wording → NOT quota."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert (
            _looks_like_balance_exhaustion(
                429,
                "Request was rejected due to rate limiting. If you want more, "
                "please contact contact@siliconflow.com. Details:TPM limit reached.",
            )
            is False
        )
        assert _looks_like_balance_exhaustion(429, "rate limit exceeded") is False

    def test_balance_exhaustion_is_quota(self):
        """'balance' / 'quota' / 'insufficient' wording → quota (permanent)."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert _looks_like_balance_exhaustion(429, "Account balance is insufficient") is True
        assert _looks_like_balance_exhaustion(429, "insufficient quota") is True
        assert _looks_like_balance_exhaustion(429, "balance exhausted") is True
        assert _looks_like_balance_exhaustion(429, "account is in arrears") is True

    def test_unrelated_statuses_never_quota(self):
        """401 'Invalid token' etc. are not quota conditions (only
        402/429 can carry the balance-exhaustion classification)."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert _looks_like_balance_exhaustion(401, "Invalid token") is False
        assert _looks_like_balance_exhaustion(400, "insufficient balance in body") is False

    def test_402_balance_exhaustion_live_evidence(self):
        """R07.29 smoke run (2026-10-09, live): a balance-emptied account
        surfaces HTTP 402 'Sorry, your account balance is insufficient'
        — the classifier must catch 402, not just 429."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert (
            _looks_like_balance_exhaustion(402, "Sorry, your account balance is insufficient")
            is True
        )
        assert _looks_like_balance_exhaustion(402, "account is in arrears") is True

    def test_402_transient_wording_still_vetoes(self):
        """The TPM/rate-limit veto applies to 402 bodies too, and an
        empty 402 body cannot classify (wording-first, status-second)."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert _looks_like_balance_exhaustion(402, "rate limit exceeded") is False
        assert _looks_like_balance_exhaustion(402, "") is False

    def test_empty_body_is_transient(self):
        """No body → cannot classify → not quota (retry path)."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        assert _looks_like_balance_exhaustion(429, "") is False

    def test_transient_wording_beats_quota_wording(self):
        """A body carrying BOTH transient and quota wording is transient
        — the veto is checked FIRST (the Cloudflare 'limit' drift
        lesson: transient wording must never trip the quota fast-fail)."""
        from agentkthx.plugins.siliconflow.siliconflow import _looks_like_balance_exhaustion

        body = "Rate limiting in effect while your quota balance is checked"
        assert _looks_like_balance_exhaustion(429, body) is False

    def test_quota_exhaustion_message_content(self, backend):
        """The remediation text points at the top-up path — the only
        remedy, because no free models exist to switch to."""
        msg = backend._quota_exhaustion_message()
        assert "balance exhausted" in msg
        assert "cloud.siliconflow.com" in msg
        assert "no free models" in msg
        # The old "switch to the free model via SILICONFLOW_FREE_ONLY=1"
        # remedy is GONE — that flag now refuses instead of swapping.
        assert "SILICONFLOW_FREE_ONLY" not in msg


# ---------------------------------------------------------------------------
# Non-chat blocklist
# ---------------------------------------------------------------------------


class TestSiliconflowNonChatBlocklist:
    """Verify the live-catalog non-chat filter."""

    def test_ocr_filtered(self):
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("deepseek-ai/DeepSeek-OCR") is True

    def test_vision_filtered(self):
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("Qwen/Qwen2.5-VL-7B-Instruct") is True
        assert _is_non_chat_model("deepseek-ai/deepseek-vl2") is True
        assert _is_non_chat_model("zai-org/GLM-4.5V") is True
        assert _is_non_chat_model("zai-org/GLM-4.6V") is True
        assert _is_non_chat_model("zai-org/GLM-5V-Turbo") is True

    def test_omni_filtered(self):
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("Qwen/Qwen3-Omni-30B-A3B-Instruct") is True
        assert _is_non_chat_model("Qwen/Qwen3-Omni-30B-A3B-Captioner") is True

    def test_translation_filtered(self):
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("tencent/Hunyuan-MT-7B") is True

    def test_embeddings_and_rerankers_filtered(self):
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("BAAI/bge-m3") is True
        assert _is_non_chat_model("BAAI/bge-large-zh") is True
        assert _is_non_chat_model("BAAI/bge-reranker-v2-m3") is True

    def test_chat_models_pass(self):
        """Legitimate chat models pass the blocklist untouched — even
        unseeded ones (conservative-by-design)."""
        from agentkthx.plugins.siliconflow.siliconflow import _is_non_chat_model

        assert _is_non_chat_model("Qwen/Qwen3-8B") is False
        assert _is_non_chat_model("deepseek-ai/DeepSeek-V3.2") is False
        assert _is_non_chat_model("THUDM/GLM-Z1-32B-0414") is False
        assert _is_non_chat_model("zai-org/GLM-5.1") is False
        assert _is_non_chat_model("moonshotai/Kimi-K2.5") is False
        assert _is_non_chat_model("openai/gpt-oss-120b") is False
        # GLM-Z1 is thinking-family but NOT vision — the version-number V
        # pattern must not false-positive on "-0414" suffixes
        assert _is_non_chat_model("THUDM/GLM-4-32B-0414") is False


# ---------------------------------------------------------------------------
# Tool support
# ---------------------------------------------------------------------------


class TestSiliconflowToolSupport:
    """Verify test_tool_support's name-pattern classification."""

    def test_reasoning_models_react(self, backend):
        """DeepSeek-R1 family + *-Thinking models reject tools → REACT.
        (Name-pattern classification — some named models are no longer
        live per the R07.29 probe; the patterns stay valid for future
        re-listings and unlisted siblings.)"""
        assert backend.test_tool_support("deepseek-ai/DeepSeek-R1") == ToolSupportLevel.REACT
        assert (
            backend.test_tool_support("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B")
            == ToolSupportLevel.REACT
        )
        assert backend.test_tool_support("moonshotai/Kimi-K2-Thinking") == ToolSupportLevel.REACT
        assert (
            backend.test_tool_support("Qwen/Qwen3-235B-A22B-Thinking-2507")
            == ToolSupportLevel.REACT
        )

    def test_vision_models_react(self, backend):
        """Vision models 'typically do NOT support tools' → REACT."""
        assert backend.test_tool_support("Qwen/Qwen2.5-VL-7B-Instruct") == ToolSupportLevel.REACT
        assert backend.test_tool_support("deepseek-ai/deepseek-vl2") == ToolSupportLevel.REACT
        assert backend.test_tool_support("zai-org/GLM-4.5V") == ToolSupportLevel.REACT
        assert (
            backend.test_tool_support("Qwen/Qwen3-Omni-30B-A3B-Instruct") == ToolSupportLevel.REACT
        )

    def test_chat_models_native(self, backend):
        """Chat models return NATIVE (the inherited default)."""
        assert backend.test_tool_support("Qwen/Qwen3-8B") == ToolSupportLevel.NATIVE
        assert backend.test_tool_support("deepseek-ai/DeepSeek-V3") == ToolSupportLevel.NATIVE
        assert backend.test_tool_support("zai-org/GLM-5") == ToolSupportLevel.NATIVE
        assert backend.test_tool_support("tencent/Hunyuan-A13B-Instruct") == ToolSupportLevel.NATIVE

    def test_glm_z1_native_by_design(self, backend):
        """GLM-Z1 is deliberately NATIVE: SiliconFlow's own docs list
        THUDM/GLM-Z1-32B-0414 as 'thinking + tools' (the general caveat
        and the verified list contradict each other — the conservative
        default + runtime 400 fallback resolves the drift). Z1 is no
        longer live (R07.29 probe) — the pin stays for the classification
        contract."""
        assert backend.test_tool_support("THUDM/GLM-Z1-32B-0414") == ToolSupportLevel.NATIVE

    def test_patterns_match_model_segment_not_vendor(self, backend):
        """R07.19 #13 contract: patterns match the post-slash SEGMENT —
        a vendor name carrying a marker word must not bleed into the
        verdict."""
        # "thinking" appears in the ORG segment, not the model segment
        assert (
            backend.test_tool_support("thinkingmachines/some-chat-model") == ToolSupportLevel.NATIVE
        )

    def test_no_tool_cache_interaction(self, backend, monkeypatch):
        """Cloud backends bypass ~/.agentkthx/tool_support.json — the
        verdict must not read or write the cache (MAINT-29 honest
        docstring contract)."""
        monkeypatch.setattr(
            "agentkthx.core.tool_cache.get_cached_tool_support",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache read")),
        )
        monkeypatch.setattr(
            "agentkthx.core.tool_cache.cache_tool_support",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("cache write")),
        )
        verdict = backend.test_tool_support("Qwen/Qwen3-8B")
        assert verdict == ToolSupportLevel.NATIVE


# ---------------------------------------------------------------------------
# Catalog lookup
# ---------------------------------------------------------------------------


class TestSiliconflowCatalog:
    """Verify catalog lookups via the _catalog_model_key hook."""

    def test_catalog_model_key_identity(self, backend):
        """Full prefixed IDs pass through unchanged (NVIDIA contract)."""
        assert backend._catalog_model_key("Qwen/Qwen3-8B") == "Qwen/Qwen3-8B"

    def test_get_model_info(self, backend):
        info = backend.get_model_info("Qwen/Qwen3-8B")
        assert info is not None
        assert info["name"] == "Qwen/Qwen3-8B"
        assert info["details"]["context_length"] == 32768
        assert info["details"]["free_tier"] is False

    def test_get_model_info_unknown(self, backend):
        assert backend.get_model_info("not/a-real-model") is None

    def test_get_model_max_context_seeded_model(self, backend):
        """The seeded default model carries its documented 32K ctx; the
        dead formerly-free model has no seed entry left → 128K
        fallback."""
        assert backend.get_model_max_context("Qwen/Qwen3-8B") == 32768
        assert backend.get_model_max_context("deepseek-ai/DeepSeek-R1-Distill-Qwen-7B") == 128000

    def test_get_model_max_context_fallback(self, backend):
        """Uncataloged/paid-without-ctx models fall back to 128K (the
        class default) — never a fabricated number."""
        assert backend.get_model_max_context("deepseek-ai/DeepSeek-V3") == 128000
        assert backend.get_model_max_context("not/a-real-model") == 128000

    def test_get_model_defaults(self, backend):
        defaults = backend._get_model_defaults("Qwen/Qwen3-8B")
        assert defaults["temperature"] == 0.7
        # max_tokens capped to ctx // 32 (house convention)
        assert defaults["max_tokens"] == 1024

    def test_get_model_defaults_unknown(self, backend):
        """Uncataloged models get safe defaults — max_tokens capped to
        the 128K fallback ctx // 32 (house convention)."""
        defaults = backend._get_model_defaults("not/a-real-model")
        assert defaults["temperature"] == 0.7
        assert defaults["max_tokens"] == 4000

    def test_shared_catalog_helpers(self, backend):
        """Catalog naming helpers use the provider label."""
        assert backend._catalog_family_name() == "siliconflow"
        assert backend._catalog_backend_name() == "siliconflow"


# ---------------------------------------------------------------------------
# list_models — ROB-42 contracts + blocklist + FREE_ONLY
# ---------------------------------------------------------------------------


class TestSiliconflowListModels:
    """Verify list_models() cache layers + failure paths."""

    def test_offline_fallback_serves_seed(self, backend, monkeypatch, isolated_cache):
        """Network down + empty cache → the seed catalog serves."""

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = backend.list_models()
        assert len(models) == 30
        assert all(m.get("name") for m in models)
        names = {m["name"] for m in models}
        assert "Qwen/Qwen3-8B" in names
        assert "deepseek-ai/DeepSeek-OCR" not in names

    def test_failed_fetch_does_not_persist_seed_as_api(self, backend, monkeypatch, isolated_cache):
        """ROB-42: a URLError on the live fetch must NOT call
        store_models — the seed must never land in the persistent cache
        labeled source='api'."""
        from agentkthx import model_cache as _mc

        store_calls = []
        real_store = _mc.store_models

        def spy_store(*args, **kwargs):
            store_calls.append(args)
            return real_store(*args, **kwargs)

        monkeypatch.setattr(_mc, "store_models", spy_store)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = backend.list_models()

        assert store_calls == []
        assert len(models) == 30

    def test_failed_fetch_serves_stale_first(self, backend, monkeypatch, isolated_cache):
        """ROB-42: on discovery failure, the stale last-known-good cache
        is served BEFORE the static seed."""
        from agentkthx import model_cache as _mc

        # Pre-store a stale (TTL-expired) live entry
        stale_entry = [
            {
                "name": "vendor/new-model-live",
                "size": 0,
                "details": {
                    "family": "siliconflow",
                    "backend": "siliconflow",
                    "context_length": 65536,
                    "free_tier": False,
                    "is_chat_model": True,
                },
            }
        ]
        _mc.store_models("siliconflow", stale_entry, source="api")
        # Force TTL expiry
        monkeypatch.setattr(_mc, "get_ttl", lambda: 0)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = backend.list_models()
        names = {m["name"] for m in models}
        # Stale live data beats the seed
        assert "vendor/new-model-live" in names
        assert len(models) < 30

    def test_malformed_shape_raises(self, backend, monkeypatch, isolated_cache):
        """ROB-42 catch-narrowing: a malformed response shape (JSON list
        where a dict was expected) propagates as a real bug instead of
        being masked as 'discovery failed'."""
        from agentkthx import model_cache as _mc

        # Empty the cache so ensure_seeded doesn't shortcut
        monkeypatch.setattr(_mc, "get_cached_models", lambda *a, **k: None)
        monkeypatch.setattr(_mc, "get_stale_models", lambda *a, **k: None)

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(["not", "a", "dict"]).encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResponse())

        with pytest.raises((AttributeError, TypeError)):
            backend.list_models()

    def test_success_path_stores_with_api_provenance(self, backend, monkeypatch, isolated_cache):
        """The success path still persists with source='api'."""

        live_payload = {
            "object": "list",
            "data": [
                {"id": "Qwen/Qwen3-8B"},
                {"id": "deepseek-ai/DeepSeek-OCR"},  # blocklisted
                {"id": "vendor/brand-new-chat-model"},
            ],
        }

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(live_payload).encode("utf-8")

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResponse())

        models = backend.list_models()
        names = {m["name"] for m in models}
        # API results first...
        assert "vendor/brand-new-chat-model" in names
        assert "Qwen/Qwen3-8B" in names
        # ...blocklist applied to live results...
        assert "deepseek-ai/DeepSeek-OCR" not in names
        # ...catalog-only models merged in (seed is 30 live-verified + 1 new = 31)
        assert len(models) == 31

        # Provenance: stored under the backend key, source=api
        cache_path = isolated_cache.get_cache_path()
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert data["backends"]["siliconflow"]["source"] == "api"

    def test_free_only_filters_catalog(self, backend, monkeypatch, isolated_cache):
        """SILICONFLOW_FREE_ONLY filters to the free models — with NO
        free models on the API (R07.29 billing probe), the honest
        result is an EMPTY list, never a silently-billing 'cheap'
        subset."""
        import agentkthx.plugins.siliconflow.siliconflow as sf_module

        monkeypatch.setattr(sf_module, "SILICONFLOW_FREE_ONLY", True)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = backend.list_models()
        names = {m["name"] for m in models}
        # No free models exist — the filter empties the catalog (the
        # formerly-free Qwen3-8B bills ≈$0.06/1M input, so it is NOT a
        # FREE_ONLY result)
        assert names == set()

    def test_l1_cache_avoids_refetch(self, backend, monkeypatch, isolated_cache):
        """A fresh L1 entry short-circuits the live fetch entirely."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        first = backend.list_models()
        assert calls  # live fetch attempted
        calls.clear()
        second = backend.list_models()
        assert calls == []  # L1 served
        assert len(second) == len(first)


# ---------------------------------------------------------------------------
# FREE_ONLY generate() rejection (no free models — R07.29 billing probe)
# ---------------------------------------------------------------------------


class TestSiliconflowFreeOnlyGenerate:
    """Verify generate() REFUSES under FREE_ONLY (no free models)."""

    def test_generate_rejects_under_free_only(self, backend, monkeypatch):
        """SILICONFLOW_FREE_ONLY + any model → RuntimeError BEFORE any
        request is built (a flag that promises 'free only' must never
        emit a billable request — even Qwen/Qwen3-8B bills ≈$0.06/1M
        input, R07.29 billing probe; the Mistral-style swap would have
        silently billed)."""
        import agentkthx.plugins.siliconflow.siliconflow as sf_module

        monkeypatch.setattr(sf_module, "SILICONFLOW_FREE_ONLY", True)

        called = []

        def fake_make_api_request(body, *, stream=False):
            called.append(body)
            return {"content": "ok", "tool_calls": [], "finish_reason": "stop"}

        def fake_jev_dispatch(**kwargs):
            called.append("jev")
            return None

        monkeypatch.setattr(backend, "_make_api_request", fake_make_api_request)
        monkeypatch.setattr(backend, "_maybe_jev_dispatch", fake_jev_dispatch)

        with pytest.raises(RuntimeError, match="no free models"):
            backend.generate("deepseek-ai/DeepSeek-V3", [{"role": "user", "content": "hi"}])
        # Not even the JEV decision call fired — the guard sits BEFORE
        # the dispatch (which is itself a billable LLM call).
        assert called == []

    def test_generate_free_only_message_carries_remedy(self, backend, monkeypatch):
        """The rejection text names the flag, the billing evidence, and
        the remedy (unset the flag / top up) — the maintainer-facing
        contract from the 2026-10 billing incident."""
        import agentkthx.plugins.siliconflow.siliconflow as sf_module

        monkeypatch.setattr(sf_module, "SILICONFLOW_FREE_ONLY", True)

        with pytest.raises(RuntimeError) as exc_info:
            backend.generate("Qwen/Qwen3-8B", [{"role": "user", "content": "hi"}])

        message = str(exc_info.value)
        assert "SILICONFLOW_FREE_ONLY" in message
        assert "no free models" in message
        assert "Qwen/Qwen3-8B" in message
        assert "cloud.siliconflow.com" in message

    def test_generate_without_free_only_keeps_paid_model(self, backend, monkeypatch):
        """FREE_ONLY off → paid models pass through (paid usage is
        opt-in, not the default)."""
        import agentkthx.plugins.siliconflow.siliconflow as sf_module

        monkeypatch.setattr(sf_module, "SILICONFLOW_FREE_ONLY", False)

        captured = {}

        def fake_make_api_request(body, *, stream=False):
            captured.update(body)
            return {"content": "ok", "tool_calls": [], "finish_reason": "stop"}

        monkeypatch.setattr(backend, "_make_api_request", fake_make_api_request)

        backend.generate("deepseek-ai/DeepSeek-V3", [{"role": "user", "content": "hi"}])
        assert captured["model"] == "deepseek-ai/DeepSeek-V3"

    def test_generate_builds_openai_body(self, backend, monkeypatch):
        """The request body is the standard OpenAI shape (model +
        messages + temperature + max_tokens) — SiliconFlow accepts it
        as-is, no provider tweaks needed."""
        captured = {}

        def fake_make_api_request(body, *, stream=False):
            captured.update(body)
            return {"content": "ok", "tool_calls": [], "finish_reason": "stop"}

        monkeypatch.setattr(backend, "_make_api_request", fake_make_api_request)

        backend.generate(
            "Qwen/Qwen3-8B",
            [{"role": "user", "content": "hi"}],
            temperature=0.3,
            max_tokens=512,
        )
        assert captured["model"] == "Qwen/Qwen3-8B"
        assert captured["temperature"] == 0.3
        assert captured["max_tokens"] == 512
        assert captured["stream"] is False


# ---------------------------------------------------------------------------
# Retry-loop behavior through the shared MAINT-28 classifier
# ---------------------------------------------------------------------------


class TestSiliconflowRetryDelegation:
    """Quota fast-fail, transient retry, and remediation texts all
    route through the shared CloudBackend skeleton."""

    def test_quota_429_fast_fails(self, backend, monkeypatch):
        """A balance-exhausted 429 raises the clear quota message
        immediately (no budget burn)."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(429, '{"error":{"message":"Account balance is insufficient"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="balance exhausted"):
            backend._make_api_request({"model": "m", "messages": []}, stream=False)

    def test_402_balance_exhaustion_raises_quota_message(self, backend, monkeypatch):
        """Live R07.29 smoke shape: 402 'account balance is insufficient'
        raises the top-up remediation immediately — no retry budget
        burned (the smoke runs failed in ~150-250ms, single attempt)."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(402, '{"message": "Sorry, your account balance is insufficient"}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="balance exhausted"):
            backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert len(calls) == 1  # fast-fail — the 402 never retried

    def test_transient_429_retries_then_succeeds(self, backend, monkeypatch):
        """A TPM rate-limit 429 backs off and retries (transient)."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(
                    429,
                    '{"message": "Request was rejected due to rate limiting. '
                    'Details:TPM limit reached."}',
                )
            return io.BytesIO(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert result is not None
        assert len(calls) == 2

    def test_401_remediation_text(self, backend, monkeypatch):
        """The 401 remediation carries the SiliconFlow key guidance."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(401, '"Invalid token"')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError) as exc_info:
            backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert "SILICONFLOW_API_KEY" in str(exc_info.value)
        assert "cloud.siliconflow.com/account/ak" in str(exc_info.value)

    def test_404_remediation_text(self, backend, monkeypatch):
        """The 404 remediation carries both documented causes (wrong
        endpoint URL + wrong model ID format)."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(404, '"404 page not found"')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError) as exc_info:
            backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert "author/model" in str(exc_info.value)
        assert "api.siliconflow.com/v1" in str(exc_info.value)

    def test_status_remediations_shape(self, backend):
        """The remediation table covers 401 + 404 with format templates."""
        table = backend._STATUS_REMEDIATIONS
        assert "sk-" in table[401]
        assert "{err_msg}" in table[404]

    def test_urlerror_exhaustion_message(self, backend, monkeypatch):
        """URLError exhaustion surfaces with the SiliconFlow brand."""
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "1")

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="SiliconFlow"):
            backend._make_api_request({"model": "m", "messages": []}, stream=False)

    def test_plain_string_error_body_parsed(self, backend, monkeypatch):
        """SiliconFlow's heterogeneous envelopes: 401 is a PLAIN STRING
        (not JSON) — the shared _parse_error_envelope falls back to the
        raw body text."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(401, '"Invalid token"')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        # Just verify the message surfaces (envelope parsed, no crash)
        with pytest.raises(RuntimeError):
            backend._make_api_request({"model": "m", "messages": []}, stream=False)

    def test_streaming_quota_429_fast_fails(self, backend, monkeypatch):
        """The STREAMING path shares the same classifier (the
        [SiliconFlow-Stream] prefix)."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(429, '{"error":{"message":"insufficient quota balance"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        gen = backend._iter_sse_lines(
            "https://api.siliconflow.com/v1/chat/completions",
            {"model": "m", "messages": [], "stream": True},
            {"Authorization": f"Bearer {VALID_KEY}"},
        )
        with pytest.raises(RuntimeError, match="balance exhausted"):
            list(gen)


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------


class TestSiliconflowManifest:
    """Verify the plugin.json manifest loads via PluginManager."""

    def test_manifest_registers_backend_and_alias(self):
        """Both 'siliconflow' and 'sf' backend aliases are registered."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        sf_m = next(m for m in manifests if m.name == "siliconflow")
        backends = sf_m.provides["backends"]
        assert "siliconflow" in backends
        assert "sf" in backends
        assert backends["siliconflow"] == "siliconflow.SiliconFlowBackend"
        assert backends["sf"] == "siliconflow.SiliconFlowBackend"

    def test_manifest_cli_flags_include_siliconflow_and_sf(self):
        """--backend flag accepts both 'siliconflow' and 'sf'."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        sf_m = next(m for m in manifests if m.name == "siliconflow")
        cli_flags = sf_m.provides["cli_flags"]
        assert "--backend" in cli_flags
        assert "siliconflow" in cli_flags["--backend"]
        assert "sf" in cli_flags["--backend"]

    def test_manifest_config_defaults(self):
        """Manifest declares the SILICONFLOW_* defaults."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        sf_m = next(m for m in manifests if m.name == "siliconflow")
        defaults = sf_m.config["defaults"]
        assert defaults["SILICONFLOW_BASE_URL"] == "https://api.siliconflow.com/v1"
        assert defaults["SILICONFLOW_API_KEY"] == ""
        assert defaults["SILICONFLOW_DEFAULT_MODEL"] == "Qwen/Qwen3-8B"
        assert defaults["SILICONFLOW_FREE_ONLY"] == "false"
        assert sf_m.config["env_prefix"] == "SILICONFLOW"

    def test_plugin_loads_and_registers_backend(self):
        """Loading the plugin registers SiliconFlowBackend under both
        'siliconflow' and 'sf' aliases."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("siliconflow")

        assert pm.is_loaded("siliconflow")
        cls = pm.get_backend_class("siliconflow")
        assert cls is not None
        assert cls.__name__ == "SiliconFlowBackend"

        # Alias
        cls_alias = pm.get_backend_class("sf")
        assert cls_alias is cls

    def test_plugin_in_backend_choices(self):
        """Both 'siliconflow' and 'sf' appear in --backend choices."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("siliconflow")
        choices = pm.get_backend_choices()
        assert "siliconflow" in choices
        assert "sf" in choices

    def test_manifest_conforms_to_v0_2_schema(self):
        """The plugin.json shape conforms to schemas/v0.2/plugin.schema.json."""
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        sf_m = next(m for m in manifests if m.name == "siliconflow")
        # Required v0.2 fields
        assert sf_m.name
        assert sf_m.version
        assert sf_m.description
        assert sf_m.author
        # Schema URL
        assert sf_m.schema and "v0.2" in sf_m.schema


# ---------------------------------------------------------------------------
# config.py integration
# ---------------------------------------------------------------------------


class TestSiliconflowConfig:
    """Verify config.py exposes SILICONFLOW_* env vars with correct defaults."""

    def test_siliconflow_base_url_default(self):
        from agentkthx import config

        assert config.SILICONFLOW_BASE_URL == "https://api.siliconflow.com/v1"

    def test_siliconflow_api_key_default_empty(self):
        from agentkthx import config

        assert config.SILICONFLOW_API_KEY == ""

    def test_siliconflow_default_model_default(self):
        from agentkthx import config

        assert config.SILICONFLOW_DEFAULT_MODEL == "Qwen/Qwen3-8B"

    def test_siliconflow_free_only_default_false(self):
        from agentkthx import config

        assert config.SILICONFLOW_FREE_ONLY is False

    def test_siliconflow_free_fallback_default(self):
        """Reserved-for-future-free-tier var; defaults to the cheapest
        known model (currently unused — no free models exist)."""
        from agentkthx import config

        assert config.SILICONFLOW_FREE_FALLBACK_MODEL == "Qwen/Qwen3-8B"

    def test_siliconflow_base_url_env_override(self, monkeypatch):
        monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
        # Force reload of config module
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.SILICONFLOW_BASE_URL == "https://api.siliconflow.cn/v1"
        finally:
            # Restore
            monkeypatch.delenv("SILICONFLOW_BASE_URL", raising=False)
            importlib.reload(_config)

    def test_siliconflow_free_only_env_true(self, monkeypatch):
        monkeypatch.setenv("SILICONFLOW_FREE_ONLY", "1")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.SILICONFLOW_FREE_ONLY is True
        finally:
            monkeypatch.delenv("SILICONFLOW_FREE_ONLY", raising=False)
            importlib.reload(_config)

    def test_siliconflow_in_backend_selection_ladder(self, monkeypatch):
        """AGENTKTHX_BACKEND=siliconflow picks up the cheapest default
        model (the SILICONFLOW_DEFAULT_MODEL)."""
        monkeypatch.setenv("AGENTKTHX_BACKEND", "siliconflow")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.DEFAULT_MODEL == "Qwen/Qwen3-8B"
        finally:
            monkeypatch.delenv("AGENTKTHX_BACKEND", raising=False)
            importlib.reload(_config)

    def test_sf_alias_in_backend_selection_ladder(self, monkeypatch):
        """AGENTKTHX_BACKEND=sf (alias) picks up the same default."""
        monkeypatch.setenv("AGENTKTHX_BACKEND", "sf")
        import importlib

        import agentkthx.config as _config

        importlib.reload(_config)
        try:
            assert _config.DEFAULT_MODEL == "Qwen/Qwen3-8B"
        finally:
            monkeypatch.delenv("AGENTKTHX_BACKEND", raising=False)
            importlib.reload(_config)


# ---------------------------------------------------------------------------
# BackendType enum integration
# ---------------------------------------------------------------------------


class TestSiliconflowBackendType:
    """Verify the BackendType.SILICONFLOW enum value exists."""

    def test_siliconflow_enum_value_exists(self):
        assert hasattr(BackendType, "SILICONFLOW")
        assert BackendType.SILICONFLOW.value == "siliconflow"

    def test_siliconflow_enum_distinct_from_others(self):
        """SILICONFLOW is a distinct value, not aliased to another backend."""
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
            assert BackendType.SILICONFLOW != other


# ---------------------------------------------------------------------------
# CLI registry integration
# ---------------------------------------------------------------------------


class TestSiliconflowCliRegistries:
    """Verify the auth picker + config command surface."""

    def test_auth_backend_registry_contains_siliconflow(self):
        """The /auth picker lists the SiliconFlow key + FREE_ONLY rows."""
        from agentkthx.cli.auth import _KEY_VAR_TO_SLUG, AUTH_BACKENDS

        labels = {row[0] for row in AUTH_BACKENDS}
        assert "SiliconFlow" in labels
        sf_rows = [row for row in AUTH_BACKENDS if row[0] == "SiliconFlow"]
        assert sf_rows[0][1] == "SILICONFLOW_API_KEY"
        assert sf_rows[0][2] == "SILICONFLOW_FREE_ONLY"

        assert _KEY_VAR_TO_SLUG["SILICONFLOW_API_KEY"] == "siliconflow"

    def test_config_display_names_contain_siliconflow(self):
        """The config command maps both slug + alias to a display name."""
        from agentkthx.cli.commands.config import _BACKEND_SLUG_TO_LABEL

        assert _BACKEND_SLUG_TO_LABEL["siliconflow"] == "SiliconFlow"
        assert _BACKEND_SLUG_TO_LABEL["sf"] == "SiliconFlow"

    def test_env_reference_lists_siliconflow_vars(self):
        """`agentkthx config --env` documents all five SILICONFLOW_* vars."""
        from agentkthx.cli.commands.config import _env_reference_entries

        entries = _env_reference_entries()
        names = {name for name, _ in entries}
        assert "SILICONFLOW_BASE_URL" in names
        assert "SILICONFLOW_API_KEY" in names
        assert "SILICONFLOW_DEFAULT_MODEL" in names
        assert "SILICONFLOW_FREE_ONLY" in names
        assert "SILICONFLOW_FREE_FALLBACK_MODEL" in names


# ---------------------------------------------------------------------------
# Seed catalog integrity
# ---------------------------------------------------------------------------


class TestSiliconflowSeedIntegrity:
    """Verify the model_seed.json siliconflow section's shape."""

    def test_qwen3_8b_carries_billing_derived_price(self):
        """Qwen/Qwen3-8B is the ONLY priced entry — the billing-derived
        input rate (≈$0.06/1M tokens; a 235-input-token request billed
        $0.000014, Oct 2026). Output is unknown → omitted, never
        fabricated. NO entry carries 0.0/0.0 — the API has no free
        models (R07.29 billing probe)."""
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        priced = {name for name, meta in SILICONFLOW_MODELS.items() if "pricing" in meta}
        assert priced == {"Qwen/Qwen3-8B"}
        pricing = SILICONFLOW_MODELS["Qwen/Qwen3-8B"]["pricing"]
        assert pricing["input"] == 0.06
        assert "output" not in pricing  # unverified — never fabricated
        # No zero-zero pricing anywhere → _is_free_model is False for all
        for meta in SILICONFLOW_MODELS.values():
            p = meta.get("pricing", {})
            assert not (p.get("input") == 0.0 and p.get("output") == 0.0)

    def test_paid_models_omit_pricing(self):
        """Paid models never carry fabricated prices."""
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        for name, meta in SILICONFLOW_MODELS.items():
            if name == "Qwen/Qwen3-8B":
                continue
            assert "pricing" not in meta, name

    def test_all_entries_have_temperature_and_max_tokens(self):
        from agentkthx.plugins.siliconflow.siliconflow import SILICONFLOW_MODELS

        for name, meta in SILICONFLOW_MODELS.items():
            assert "default_temperature" in meta, name
            assert "default_max_tokens" in meta, name

    def test_no_vision_or_ocr_models_seeded(self):
        """The seed is chat-only (blocklist patterns hold for the seed)."""
        from agentkthx.plugins.siliconflow.siliconflow import (
            SILICONFLOW_MODELS,
            _is_non_chat_model,
        )

        for name in SILICONFLOW_MODELS:
            assert not _is_non_chat_model(name), name
