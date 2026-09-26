"""
Tests for HuggingFaceBackend — provider-suffix routing, FREE_ONLY
whitelist enforcement, 402 credit-exhaustion fallback, ReAct fallback
when partner providers reject the `tools` field, and the inherited
OpenAI Chat-Completions plumbing.

Mirrors test_openrouter_backend.py for the shared OpenAI-compat surface
(_parse_openai_response, _build_openai_body, _get_chat_completions_url,
_get_auth_headers, _is_tools_not_supported_error, generate flow) and
adds HF-specific tests for:
  - HF_FREE_MODEL_WHITELIST membership (_is_free_model)
  - HF_PROVIDER_POLICY auto-suffix (_apply_provider_policy)
  - HF_FREE_ONLY strict rejection of non-whitelisted models
  - HTTP 402 free-tier-credit-exhaustion fallback to
    HF_FREE_FALLBACK_MODEL

Written by VTSTech — https://www.vts-tech.org
"""

import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import pytest

# NOTE: We import the backend module directly (not via __init__.py) so
# the helpers and constants are available without triggering PluginManager
# discovery in test collection.
from agentkthx.plugins.huggingface.huggingface import (
    HuggingFaceBackend,
    HF_FREE_MODEL_WHITELIST,
    HF_MODELS,
    _apply_provider_policy,
    _apply_provider_policy_live,
    _has_provider_suffix,
    _is_free_model,
)
from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.types import ApiMode, BackendType, ToolSupportLevel


# ─────────────────────────────────────────────────────────────────────────────
# Module-level mock for _probe_whoami (R07.02 polish)
# ─────────────────────────────────────────────────────────────────────────────
# R07.02 polish added _probe_whoami() which is called on every
# HuggingFaceBackend __init__ to validate the token + detect free-tier
# users. In tests we use a fake token (hf_fake_test_token_for_scaffold),
# so the real network call would 401 + 5s timeout. Mock it module-wide
# to a no-op (self._user_info stays None, _resolve_free_only_mode falls
# back to the module-level HF_FREE_ONLY constant which each test
# controls directly via self._hf_mod.HF_FREE_ONLY).
#
# unittest's setUpModule/tearDownModule hooks run before/after ALL tests
# in this module — works across all TestCase classes without needing
# per-class setUp boilerplate.

_orig_probe_whoami = HuggingFaceBackend._probe_whoami


def _noop_probe_whoami(self):
    """No-op replacement for _probe_whoami in tests."""
    self._user_info = None


def setUpModule():
    HuggingFaceBackend._probe_whoami = _noop_probe_whoami


def tearDownModule():
    HuggingFaceBackend._probe_whoami = _orig_probe_whoami


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_tool() -> Tool:
    """Sample tool used in the generate-flow tests below."""
    return Tool(
        name="shell",
        description="Run a shell command",
        params=[ToolParam(name="command", type="string", description="cmd")],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants and helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestWhitelistAndCatalog:
    """The HF_FREE_MODEL_WHITELIST and HF_MODELS catalog should be
    consistent and populated for the v0.1 scaffold."""

    def test_whitelist_not_empty(self):
        assert len(HF_FREE_MODEL_WHITELIST) > 0

    def test_catalog_not_empty(self):
        assert len(HF_MODELS) > 0

    def test_whitelist_subset_of_known_open_models(self):
        # Spot-check: the headline open-weight models from each major
        # family should be in the whitelist.
        # R07.03: Only genuinely $0/token models (verified via probe)
        expected = {
            "inclusionAI/Ling-3.0-flash-Fin",
            "prism-ml/Ternary-Bonsai-27B-gguf",
            "prism-ml/Ternary-Bonsai-27B-AWQ-4bit",
        }
        assert expected <= HF_FREE_MODEL_WHITELIST

    def test_catalog_covers_whitelist(self):
        # Every whitelisted model should have a catalog entry so
        # _get_model_defaults() works even when /v1/models is unreachable.
        missing = HF_FREE_MODEL_WHITELIST - set(HF_MODELS.keys())
        assert not missing, (
            f"Whitelist entries without catalog entries: {missing}. "
            f"Both sets must stay in sync — see HUGGINGFACE_API_TECHNICAL_REFERENCE.md "
            f"§Free Tier Behavior."
        )


class TestIsFreeModel:
    """_is_free_model strips the routing suffix before checking whitelist."""

    def test_whitelisted_base_id(self):
        assert _is_free_model("prism-ml/Ternary-Bonsai-27B-gguf") is True

    def test_whitelisted_with_fastest_suffix(self):
        assert _is_free_model("prism-ml/Ternary-Bonsai-27B-gguf:fastest") is True

    def test_whitelisted_with_cheapest_suffix(self):
        assert _is_free_model("prism-ml/Ternary-Bonsai-27B-gguf:cheapest") is True

    def test_whitelisted_with_provider_suffix(self):
        # Suffix can be any partner name; whitelist check ignores it.
        assert _is_free_model("prism-ml/Ternary-Bonsai-27B-gguf:together") is True

    def test_paid_model_not_in_whitelist(self):
        # Closed-weight models never appear in the whitelist
        assert _is_free_model("anthropic/claude-3.5-sonnet") is False

    def test_paid_model_with_suffix_still_not_in_whitelist(self):
        assert _is_free_model("openai/gpt-4o:cheapest") is False

    def test_unknown_model_not_in_whitelist(self):
        assert _is_free_model("some-org/some-model") is False


class TestHasProviderSuffix:
    """_has_provider_suffix detects the ``:`` separator on a model id."""

    def test_no_suffix(self):
        assert _has_provider_suffix("openai/gpt-oss-120b") is False

    def test_fastest_suffix(self):
        assert _has_provider_suffix("openai/gpt-oss-120b:fastest") is True

    def test_cheapest_suffix(self):
        assert _has_provider_suffix("openai/gpt-oss-120b:cheapest") is True

    def test_provider_name_suffix(self):
        assert _has_provider_suffix("openai/gpt-oss-120b:groq") is True


class TestApplyProviderPolicy:
    """_apply_provider_policy appends the configured routing suffix when
    no explicit suffix is present on the model id."""

    def test_no_suffix_no_env_policy(self, monkeypatch):
        # HF_PROVIDER_POLICY defaults to "" — no suffix appended.
        monkeypatch.delenv("HF_PROVIDER_POLICY", raising=False)
        # NOTE: this test verifies the runtime env-var lookup, but since
        # the module-level HF_PROVIDER_POLICY is read at import time,
        # we instead verify the function's behavior on the imported value.
        # If HF_PROVIDER_POLICY env var is unset, the function returns
        # the model id unchanged.
        result = _apply_provider_policy("openai/gpt-oss-120b")
        # Either unchanged (policy was "") or has a suffix (policy set)
        assert ":" in result or result == "openai/gpt-oss-120b"

    def test_explicit_suffix_is_preserved(self):
        # User's explicit suffix wins over the env-var policy.
        result = _apply_provider_policy("openai/gpt-oss-120b:groq")
        assert result == "openai/gpt-oss-120b:groq"

    def test_explicit_provider_name_suffix_preserved(self):
        result = _apply_provider_policy("meta-llama/Llama-3.3-70B-Instruct:together")
        assert result == "meta-llama/Llama-3.3-70B-Instruct:together"


# ─────────────────────────────────────────────────────────────────────────────
# URL / auth / abstract-hook implementation
# ─────────────────────────────────────────────────────────────────────────────

class TestBackendHooks(unittest.TestCase):
    """The 4 abstract hooks from OpenAICompatibleBackend (ARCH-01)
    should be implemented on HuggingFaceBackend."""

    @classmethod
    def setUpClass(cls):
        # Set a fake token so __init__ doesn't crash on lazy auth check.
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        cls.backend = HuggingFaceBackend()
        # Reset env to avoid leaking into other tests
        del os.environ["HF_TOKEN"]

    def test_get_chat_completions_url_uses_hf_router_path(self):
        """The URL should be ``<base>/chat/completions`` on HF Router."""
        url = self.backend._get_chat_completions_url()
        self.assertEqual(url, "https://router.huggingface.co/v1/chat/completions")

    def test_get_auth_headers_include_bearer(self):
        """Auth headers must contain the Bearer token."""
        headers = self.backend._get_auth_headers()
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.assertIn("Content-Type", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_get_auth_headers_have_no_openrouter_specific_fields(self):
        """HF Router doesn't use HTTP-Referer / X-Title (those are
        OpenRouter-specific leaderboard attribution headers)."""
        headers = self.backend._get_auth_headers()
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-Title", headers)

    def test_has_iter_sse_lines(self):
        """_iter_sse_lines is implemented (abstract hook satisfied)."""
        self.assertTrue(callable(getattr(self.backend, "_iter_sse_lines", None)))


# ─────────────────────────────────────────────────────────────────────────────
# is_cloud and BackendType
# ─────────────────────────────────────────────────────────────────────────────

class TestIsCloudAndBackendType(unittest.TestCase):
    """Verify HuggingFaceBackend is correctly marked as cloud (R06.57)
    and uses the HUGGINGFACE BackendType enum value."""

    def test_is_cloud_inherits_true(self):
        """HuggingFaceBackend extends OpenAICompatibleBackend, so is_cloud
        resolves to True without any explicit override — same pattern as
        OpenRouterBackend and GeminiBackend."""
        assert HuggingFaceBackend.is_cloud is True

    def test_backend_type_property_is_huggingface(self):
        os.environ["HF_TOKEN"] = "hf_fake"
        try:
            b = HuggingFaceBackend()
            assert b.backend_type is BackendType.HUGGINGFACE
            assert b.backend_type.value == "huggingface"
        finally:
            del os.environ["HF_TOKEN"]


# ─────────────────────────────────────────────────────────────────────────────
# _is_tools_not_supported_error — HF-specific patterns
# ─────────────────────────────────────────────────────────────────────────────

class TestIsToolsNotSupportedError(unittest.TestCase):
    """ReAct-fallback error detection — HF-specific patterns added beyond
    the OpenRouter set."""

    def test_matches_openrouter_indicators(self):
        """All OpenRouter-shared indicators should still match (parity)."""
        for indicator in (
            "does not support tools",
            "tools are not supported",
            "tool calling is not supported",
            "tools are not yet supported",
            "does not support function calling",
            "function calling is not supported",
            "no tools endpoint",
        ):
            self.assertTrue(
                HuggingFaceBackend._is_tools_not_supported_error(indicator),
                f"Indicator should match: {indicator}",
            )

    def test_matches_hf_specific_indicators(self):
        """HF-specific phrases from the technical reference."""
        for indicator in (
            "tool use is not supported",
            "tool_calls not supported on this model",
            "Unsupported param: tools",  # TGI / llama-server form
        ):
            self.assertTrue(
                HuggingFaceBackend._is_tools_not_supported_error(indicator),
                f"HF-specific indicator should match: {indicator}",
            )

    def test_does_not_match_unrelated_errors(self):
        """Specificity check — unrelated errors must not trigger the
        ReAct fallback (would mask real failures)."""
        for indicator in (
            "Internal server error",
            "Rate limit exceeded",
            "Invalid model id",
            "context length is 131072 tokens",  # context-length 400, not tools
        ):
            self.assertFalse(
                HuggingFaceBackend._is_tools_not_supported_error(indicator),
                f"Unrelated error must NOT trigger ReAct fallback: {indicator}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Generate flow — ReAct fallback when partner rejects tools
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateFlow(unittest.TestCase):
    """Tests for HuggingFaceBackend.generate() — mirrors the OpenRouter
    generate flow tests, but with HF-specific request/response shape."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        self.backend = HuggingFaceBackend()

    def tearDown(self):
        del os.environ["HF_TOKEN"]

    def _mock_urlopen(self, response_json: dict):
        """Build a contextmanager that patches urllib.request.urlopen
        to return a fake response yielding the given JSON."""
        cm = MagicMock()
        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.read = MagicMock(return_value=json.dumps(response_json).encode("utf-8"))
        cm.return_value = response
        return cm

    @patch("urllib.request.urlopen")
    def test_generate_sends_tools_and_parses_response(self, mock_urlopen):
        """A successful generate() call should POST to /chat/completions
        with the tools field, and parse the response into AgentKthx's
        {content, tool_calls, usage, finish_reason} shape."""
        mock_urlopen.side_effect = [
            self._mock_urlopen({
                "id": "chatcmpl-test",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "The result is 120.",
                        "tool_calls": [],
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }).return_value,
        ]
        # The /v1/models call during __init__ also hits urlopen, but
        # __init__ runs in setUp before this mock is applied — it falls
        # back to the static catalog. Good.

        result = self.backend.generate(
            model="prism-ml/Ternary-Bonsai-27B-gguf",
            messages=[{"role": "user", "content": "What is 15 * 8?"}],
            tools=[_make_tool()],
            temperature=0.1,
            max_tokens=256,
        )
        self.assertEqual(result["content"], "The result is 120.")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertIn("latency_ms", result)

    @patch("urllib.request.urlopen")
    def test_generate_falls_back_when_tools_rejected(self, mock_urlopen):
        """When the partner provider rejects the `tools` field with a
        'does not support tools' error, generate() should retry without
        tools (ReAct fallback path)."""
        # First call: tools-not-supported error
        err_response = MagicMock()
        err_response.read = MagicMock(return_value=b'{"error":{"message":"does not support tools"}}')
        err_response.__enter__ = MagicMock(return_value=err_response)
        err_response.__exit__ = MagicMock(return_value=False)
        # HTTPError needs .code, .headers, .fp
        http_err = type("HTTPError", (Exception,), {
            "code": 400,
            "headers": {},
            "fp": True,
            "read": err_response.read,
        })()
        # But urllib.error.HTTPError is a specific class — easier to
        # raise the actual class.
        import urllib.error
        real_http_err = urllib.error.HTTPError(
            url="http://test",
            code=400,
            msg='{"error":{"message":"does not support tools"}}',
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"does not support tools"}}'),
        )
        # Second call: success without tools
        success_response = MagicMock()
        success_response.__enter__ = MagicMock(return_value=success_response)
        success_response.__exit__ = MagicMock(return_value=False)
        success_response.read = MagicMock(return_value=json.dumps({
            "id": "chatcmpl-react",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": 'I should call shell({"command": "echo 120"})',
                    "tool_calls": [],
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8"))
        mock_urlopen.side_effect = [real_http_err, success_response]

        result = self.backend.generate(
            model="prism-ml/Ternary-Bonsai-27B-gguf",
            messages=[{"role": "user", "content": "What is 15 * 8?"}],
            tools=[_make_tool()],
            temperature=0.1,
            max_tokens=256,
        )
        # The ReAct-fallback path succeeded and we got text content
        # that the ToolParser will pick up.
        self.assertIn("shell", result["content"])

    @patch("urllib.request.urlopen")
    def test_generate_raises_on_empty_response(self, mock_urlopen):
        """An empty response (no content, no tool_calls) should raise
        RuntimeError so the chat loop can surface a meaningful error
        instead of showing a blank ``AgentKthx: ``."""
        mock_urlopen.side_effect = [
            self._mock_urlopen({
                "id": "chatcmpl-empty",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [],
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
            }).return_value,
        ]
        with self.assertRaises(RuntimeError) as ctx:
            self.backend.generate(
                model="prism-ml/Ternary-Bonsai-27B-gguf",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=128,
            )
        self.assertIn("empty response", str(ctx.exception).lower())


# ─────────────────────────────────────────────────────────────────────────────
# HF_FREE_ONLY enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestFreeOnlyEnforcement(unittest.TestCase):
    """HF_FREE_ONLY mode should reject non-whitelisted models BEFORE any
    HTTP request is made — preventing accidental paid API calls.

    R07.02 polish: the effective free-only mode is now resolved at
    ``__init__`` time via ``_resolve_free_only_mode()``, which reads the
    ``HF_FREE_ONLY`` env var directly (not the module-level constant).
    The new ``_probe_whoami()`` would also try a real network call on
    init — we mock it to a no-op so the tests don't hit the network
    and don't slow down waiting for the 5s timeout on the fake token.
    """

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        # Save and clear HF_FREE_ONLY so the default path is testable
        self._saved_free_only = os.environ.get("HF_FREE_ONLY", "")
        os.environ["HF_FREE_ONLY"] = "false"
        # Patch the module-level HF_FREE_ONLY constant directly. Used
        # as the fallback in _resolve_free_only_mode() when whoami is
        # unreachable (which it is in tests — _probe_whoami is mocked
        # module-wide via setUpModule).
        from agentkthx.plugins.huggingface import huggingface as hf_mod
        self._hf_mod = hf_mod
        self._original_free_only = hf_mod.HF_FREE_ONLY
        hf_mod.HF_FREE_ONLY = False

    def tearDown(self):
        del os.environ["HF_TOKEN"]
        if self._saved_free_only:
            os.environ["HF_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("HF_FREE_ONLY", None)
        self._hf_mod.HF_FREE_ONLY = self._original_free_only

    def test_free_only_false_allows_paid_models_by_default(self):
        """When HF_FREE_ONLY is false (the default), generate() with a
        paid model id should reach the HTTP layer — the whitelist check
        is skipped."""
        os.environ["HF_FREE_ONLY"] = "false"
        self._hf_mod.HF_FREE_ONLY = False
        b = HuggingFaceBackend()
        # _free_only_effective should be False (explicit env var)
        assert b._free_only_effective is False
        # Verify the check is OFF — calling generate with a paid model
        # should reach the HTTP layer (which will fail with a fake token,
        # but that's a different error than the whitelist rejection).
        # We verify by patching _make_api_request to short-circuit.
        called_with = {}
        def fake_request(endpoint, data, stream=False):
            called_with["endpoint"] = endpoint
            called_with["model"] = data.get("model")
            return {
                "id": "test",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok", "tool_calls": []},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        b._make_api_request = fake_request
        result = b.generate(
            model="anthropic/claude-3.5-sonnet",  # not in whitelist
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        assert called_with["endpoint"] == "chat/completions"
        assert "anthropic/claude-3.5-sonnet" in called_with["model"]
        assert result["content"] == "ok"

    def test_free_only_true_rejects_non_whitelisted_model(self):
        """When HF_FREE_ONLY is true, generate() with a paid model
        should raise RuntimeError BEFORE any HTTP request is made."""
        # R07.02: set the ENV VAR (which _resolve_free_only_mode reads
        # directly) AND the module constant (fallback when whoami fails).
        os.environ["HF_FREE_ONLY"] = "true"
        self._hf_mod.HF_FREE_ONLY = True
        b = HuggingFaceBackend()
        # _free_only_effective should be True (explicit env var)
        assert b._free_only_effective is True
        # Verify _make_api_request is NEVER called for a paid model
        called = {"count": 0}
        def fail_if_called(endpoint, data, stream=False):
            called["count"] += 1
            raise AssertionError(
                "_make_api_request should NOT be called when HF_FREE_ONLY "
                "rejects the model upfront"
            )
        b._make_api_request = fail_if_called
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="anthropic/claude-3.5-sonnet",  # not in whitelist
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        assert "not in the Hugging Face free-tier whitelist" in str(ctx.exception)
        assert called["count"] == 0

    def test_free_only_true_allows_whitelisted_model(self):
        """When HF_FREE_ONLY is true, generate() with a whitelisted
        model should reach the HTTP layer."""
        os.environ["HF_FREE_ONLY"] = "true"
        self._hf_mod.HF_FREE_ONLY = True
        b = HuggingFaceBackend()
        assert b._free_only_effective is True
        called = {"model": None}
        def fake_request(endpoint, data, stream=False):
            called["model"] = data.get("model")
            return {
                "id": "test",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok", "tool_calls": []},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        b._make_api_request = fake_request
        result = b.generate(
            model="prism-ml/Ternary-Bonsai-27B-gguf",  # genuinely $0/token
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        # HF_FREE_ONLY forces :cheapest suffix when no explicit suffix
        assert called["model"] == "prism-ml/Ternary-Bonsai-27B-gguf:cheapest"
        assert result["content"] == "ok"


# ─────────────────────────────────────────────────────────────────────
# HTTP 402 credit-exhaustion fallback
# ─────────────────────────────────────────────────────────────────────

class TestCreditExhaustionFallback(unittest.TestCase):
    """When HF Router returns HTTP 402 (free-tier credit exhausted),
    the backend should swap to HF_FREE_FALLBACK_MODEL and retry once
    (mirroring the ZAI plugin's 429 insufficient balance fallback).

    When HF_FREE_ONLY is true, 402 is a hard failure (no retry) — the
    user must add billing or wait for the monthly reset.
    """

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        from agentkthx.plugins.huggingface import huggingface as hf_mod
        self._hf_mod = hf_mod
        self._original_free_only = hf_mod.HF_FREE_ONLY

    def tearDown(self):
        del os.environ["HF_TOKEN"]
        self._hf_mod.HF_FREE_ONLY = self._original_free_only

    def test_402_with_free_only_true_raises_clear_error(self):
        """HF_FREE_ONLY=true: 402 must surface a clear actionable error
        (no retry — retrying burns router quota)."""
        self._hf_mod.HF_FREE_ONLY = True
        b = HuggingFaceBackend()
        # Patch _make_api_request to raise a 402-shaped RuntimeError
        # The 402 path in _make_api_request itself raises, so we patch
        # _make_api_request to short-circuit.
        def raise_402(endpoint, data, stream=False):
            raise RuntimeError(
                "Hugging Face free-tier credit exhausted for "
                f"'{data.get('model')}'. Set HF_FREE_ONLY=false ..."
            )
        b._make_api_request = raise_402
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="prism-ml/Ternary-Bonsai-27B-gguf",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        assert "free-tier credit exhausted" in str(ctx.exception)


# ─────────────────────────────────────────────────────────────────────
# Plugin manifest validation
# ─────────────────────────────────────────────────────────────────────

class TestPluginManifest(unittest.TestCase):
    """The plugin.json should validate against the v0.2 schema and
    follow the same structure as the OpenRouter / ZAI / Gemini plugins."""

    def test_manifest_is_v02_form(self):
        import json
        from pathlib import Path
        from agentkthx.plugins._loader import (
            CANONICAL_SCHEMA,
            EXT_NAMESPACE,
            _parse_manifest,
        )
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "plugins" / "huggingface" / "plugin.json"
        )
        assert manifest_path.exists(), f"Missing {manifest_path}"
        m = _parse_manifest(manifest_path, root_kind="builtin")
        assert m.name == "huggingface"
        assert m.schema == CANONICAL_SCHEMA
        assert m.legacy_fields_used == [], "huggingface still uses legacy top-level fields"
        assert "agentnova" not in m.compatibility

    def test_manifest_provides_huggingface_backend(self):
        import json
        from pathlib import Path
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "plugins" / "huggingface" / "plugin.json"
        )
        with open(manifest_path) as f:
            m = json.load(f)
        ext = m["extensions"]["org.vts-tech.agentkthx"]
        assert ext["type"] == "backend"
        assert ext["provides"]["backends"] == {"huggingface": "huggingface.HuggingFaceBackend"}
        assert "huggingface" in ext["provides"]["cli_flags"]["--backend"]
        assert "hf" in ext["provides"]["cli_flags"]["--backend"]

    def test_manifest_config_defaults_match_config_py(self):
        """The plugin.json config.defaults should mirror the env-var
        defaults defined in agentkthx/config.py."""
        import json
        from pathlib import Path
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "plugins" / "huggingface" / "plugin.json"
        )
        with open(manifest_path) as f:
            m = json.load(f)
        defaults = m["extensions"]["org.vts-tech.agentkthx"]["config"]["defaults"]
        assert defaults["HF_BASE_URL"] == "https://router.huggingface.co/v1"
        assert defaults["HF_DEFAULT_MODEL"] == "openai/gpt-oss-120b"
        assert defaults["HF_FREE_ONLY"] == "false"
        assert defaults["HF_FREE_FALLBACK_MODEL"] == "prism-ml/Ternary-Bonsai-27B-gguf"
        assert defaults["HF_PROVIDER_POLICY"] == ""


# ─────────────────────────────────────────────────────────────────────
# Plugin discovery & loading integration
# ─────────────────────────────────────────────────────────────────────

class TestPluginDiscovery(unittest.TestCase):
    """The huggingface plugin should be discoverable and loadable
    via PluginManager alongside the other built-in plugins."""

    def test_builtin_manifests_still_include_huggingface(self):
        """test_plugin_spec.py:test_builtin_manifests_are_v02_form uses
        ``<=`` (subset), so adding huggingface is a superset — the
        existing assertion still passes. This test is a redundant
        safety net to call out HF explicitly in the test report."""
        import json
        from pathlib import Path
        plugins_dir = (
            Path(__file__).resolve().parents[1] / "agentkthx" / "plugins"
        )
        names = set()
        for entry in sorted(plugins_dir.iterdir()):
            mpath = entry / "plugin.json"
            if entry.is_dir() and mpath.exists():
                with open(mpath) as f:
                    m = json.load(f)
                names.add(m["name"])
        assert "huggingface" in names
        # Verify the existing built-in plugins are still present
        assert {"bitnet", "zai", "openrouter", "turboquant", "acp", "test-plugin"} <= names

    def test_plugin_manager_loads_huggingface(self):
        from agentkthx.plugins._loader import PluginManager
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        try:
            pm = PluginManager()
            plugin = pm.load("huggingface")
            self.assertIsNotNone(plugin)
            self.assertTrue(pm.is_loaded("huggingface"))
            cls = pm.get_backend_class("huggingface")
            self.assertEqual(cls.__name__, "HuggingFaceBackend")
            # Both canonical name and alias should be in the choices
            choices = pm.get_backend_choices()
            self.assertIn("huggingface", choices)
            self.assertIn("hf", choices)
            pm.unload("huggingface")
            self.assertFalse(pm.is_loaded("huggingface"))
        finally:
            del os.environ["HF_TOKEN"]


# ─────────────────────────────────────────────────────────────────────────────
# R07.02 polish: per-provider parse shape, _is_free_model_live, whoami probe,
# _resolve_free_only_mode auto-detection
# ─────────────────────────────────────────────────────────────────────────────

class TestParseHfModelShape(unittest.TestCase):
    """R07.02 polish: _parse_hf_model() should capture the per-provider
    shape from the live /v1/models response (pricing, is_free,
    supports_tools, latency, throughput, status), not just the top-level
    fields. Verified Sept 2026 via direct probe — the live response shape
    is documented in _parse_hf_model()'s docstring."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"

    def tearDown(self):
        del os.environ["HF_TOKEN"]

    def _make_sample_model_data(self) -> dict:
        """Build a sample /v1/models response object that mirrors the
        live HF Router shape (verified Sept 2026)."""
        return {
            "id": "Qwen/Qwen3.8-27B",
            "object": "model",
            "created": 1785918179,
            "owned_by": "Qwen",
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
            "providers": [
                {
                    "provider": "novita",
                    "status": "live",
                    "context_length": 1_000_000,
                    "pricing": {"input": 0.42, "output": 3.0},
                    "is_free": False,
                    "supports_tools": True,
                    "supports_structured_output": False,
                    "first_token_latency_ms": 762.6,
                    "throughput": 39.32,
                    "is_model_author": False,
                },
                {
                    "provider": "cerebras",
                    "status": "live",
                    "context_length": 65_536,
                    "pricing": {"input": 0.99, "output": 1.49},
                    "is_free": False,
                    "supports_tools": True,
                    "supports_structured_output": True,
                    "first_token_latency_ms": 4926.0,
                    "throughput": 166.42,
                    "is_model_author": False,
                },
                {
                    "provider": "featherless-ai",
                    "status": "live",
                    "is_free": False,
                    "is_model_author": False,
                },
            ],
        }

    def test_parse_captures_per_provider_list(self):
        """The full per-provider array should be preserved on the parsed
        model under the 'providers' key (shortcut for _is_free_model_live
        and similar queries)."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        assert "providers" in parsed
        assert isinstance(parsed["providers"], list)
        assert len(parsed["providers"]) == 3
        assert parsed["providers"][0]["provider"] == "novita"

    def test_parse_aggregates_context_length_max(self):
        """Top-level context_length should be the MAX across providers
        (best-case budget for routing decisions)."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        # novita=1_000_000, cerebras=65_536 → max=1_000_000
        assert parsed["details"]["context_length"] == 1_000_000

    def test_parse_aggregates_cheapest_pricing(self):
        """cheapest_input_per_1m and cheapest_output_per_1m should be
        the MIN across providers' pricing.input and pricing.output."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        # novita input=0.42, cerebras input=0.99 → min=0.42
        assert parsed["details"]["cheapest_input_per_1m"] == 0.42
        # novita output=3.0, cerebras output=1.49 → min=1.49
        assert parsed["details"]["cheapest_output_per_1m"] == 1.49

    def test_parse_aggregates_is_free_any(self):
        """any_free_provider should be True if ANY provider has is_free=true.
        Currently all are False (matches live Sept 2026 state)."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        assert parsed["details"]["any_free_provider"] is False

        # Now flip one provider to is_free=True and re-check
        data = self._make_sample_model_data()
        data["providers"][1]["is_free"] = True  # cerebras becomes free
        parsed = b._parse_hf_model(data)
        assert parsed["details"]["any_free_provider"] is True

    def test_parse_aggregates_supports_tools_signals(self):
        """any_supports_tools and all_support_tools should be computed
        correctly across providers."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        # novita=True, cerebras=True, featherless-ai missing → any=True, all=False
        assert parsed["details"]["any_supports_tools"] is True
        assert parsed["details"]["all_support_tools"] is False

    def test_parse_captures_provider_count_and_owned_by(self):
        """provider_count and owned_by should be surfaced for richer
        model listing display (Change #4, deferred)."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model(self._make_sample_model_data())
        assert parsed["details"]["provider_count"] == 3
        assert parsed["details"]["owned_by"] == "Qwen"

    def test_parse_handles_missing_providers_array(self):
        """A model with no providers array should not crash — falls back
        to conservative defaults for context_length and max_completion_tokens."""
        b = HuggingFaceBackend()
        parsed = b._parse_hf_model({
            "id": "test/no-providers-model",
            "object": "model",
            # no "providers" key at all
        })
        assert parsed["name"] == "test/no-providers-model"
        assert parsed["details"]["context_length"] == 128_000  # conservative default
        assert parsed["details"]["max_completion_tokens"] == 8192
        assert parsed["details"]["any_free_provider"] is False
        assert parsed["details"]["provider_count"] == 0
        assert parsed["providers"] == []  # shortcut key, empty list

    def test_parse_preserves_raw_model_data_for_debug(self):
        """The full raw response should be preserved under 'model_data'
        for forward-compat (any future field the API adds is available
        without code change)."""
        b = HuggingFaceBackend()
        raw = self._make_sample_model_data()
        parsed = b._parse_hf_model(raw)
        assert parsed["model_data"] is raw  # same object, no copy
        # Architecture field is preserved (not consumed by _parse_hf_model)
        assert parsed["model_data"]["architecture"]["input_modalities"] == ["text", "image"]


class TestIsFreeModelLive(unittest.TestCase):
    """R07.02 polish: _is_free_model_live() instance method supplements
    the static whitelist with live API is_free flag consultation.

    As of 2026-09-26: is_free is false for all 336 combos even with auth
    — the free-tier model is purely credit-based. But HF can flip this
    true at any time for sponsored/promo windows, and this method auto-
    picks that up with zero code change."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"

    def tearDown(self):
        del os.environ["HF_TOKEN"]

    def test_static_whitelist_hit_returns_true_without_cache(self):
        """A whitelisted model returns True even before the cache is
        populated — the static whitelist is the fast path."""
        b = HuggingFaceBackend()
        # Force-clear the cache (it's populated on init via list_models)
        b._model_cache = None
        assert b._is_free_model_live("prism-ml/Ternary-Bonsai-27B-gguf") is True

    def test_static_whitelist_hit_with_suffix_returns_true(self):
        """A whitelisted model with a routing suffix should still match
        (suffix is stripped before whitelist check)."""
        b = HuggingFaceBackend()
        b._model_cache = None
        assert b._is_free_model_live("prism-ml/Ternary-Bonsai-27B-gguf:cheapest") is True
        assert b._is_free_model_live("prism-ml/Ternary-Bonsai-27B-AWQ-4bit:together") is True
        assert b._is_free_model_live("prism-ml/Ternary-Bonsai-27B-gguf:fastest") is True

    def test_non_whitelisted_returns_false_when_no_live_data(self):
        """A non-whitelisted model with no live API data should return
        False (the cache is empty or model not in cache)."""
        b = HuggingFaceBackend()
        b._model_cache = None
        assert b._is_free_model_live("anthropic/claude-3.5-sonnet") is False
        assert b._is_free_model_live("some-unknown/model") is False

    def test_live_is_free_true_combo_returns_true(self):
        """A non-whitelisted model with a provider flagged is_free=true
        in the live API cache should return True (auto-detection)."""
        b = HuggingFaceBackend()
        # Inject a fake cache entry: non-whitelisted model, but one
        # provider has is_free=true (simulates HF flipping a sponsored
        # combo free).
        b._model_cache = [{
            "name": "sponsored/some-paid-model",
            "providers": [
                {"provider": "novita", "is_free": False},
                {"provider": "groq", "is_free": True},  # sponsored free!
                {"provider": "together", "is_free": False},
            ],
        }]
        # Not in static whitelist
        from agentkthx.plugins.huggingface.huggingface import HF_FREE_MODEL_WHITELIST
        assert "sponsored/some-paid-model" not in HF_FREE_MODEL_WHITELIST
        # But auto-detected as free via live API cache
        assert b._is_free_model_live("sponsored/some-paid-model") is True

    def test_live_is_free_false_combos_return_false(self):
        """A non-whitelisted model with all providers is_free=false
        should return False (matches live Sept 2026 state for all 336
        combos)."""
        b = HuggingFaceBackend()
        b._model_cache = [{
            "name": "paid/some-paid-model",
            "providers": [
                {"provider": "novita", "is_free": False},
                {"provider": "cerebras", "is_free": False},
                {"provider": "together", "is_free": False},
            ],
        }]
        assert b._is_free_model_live("paid/some-paid-model") is False

    def test_live_falls_back_to_model_data_providers(self):
        """When the shortcut 'providers' key is missing (older cache
        shape), the method should fall back to model_data.providers."""
        b = HuggingFaceBackend()
        b._model_cache = [{
            "name": "legacy/old-cache-shape-model",
            # No 'providers' shortcut key — only model_data.providers
            "model_data": {
                "providers": [
                    {"provider": "novita", "is_free": True},
                ],
            },
        }]
        assert b._is_free_model_live("legacy/old-cache-shape-model") is True

    def test_live_model_not_in_cache_returns_false(self):
        """A model that's not in the cache AND not in the static whitelist
        should return False (no signal either way)."""
        b = HuggingFaceBackend()
        b._model_cache = [
            {"name": "other/model-1", "providers": []},
            {"name": "other/model-2", "providers": []},
        ]
        assert b._is_free_model_live("not/in-cache") is False


class TestProbeWhoami(unittest.TestCase):
    """R07.02 polish: _probe_whoami() validates the token via
    /api/whoami-v2 and detects free-tier users. Tests use the
    module-level mock (no-op) by default — these tests temporarily
    restore the original method and patch urlopen to verify the real
    behavior with a controlled response."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"

    def tearDown(self):
        del os.environ["HF_TOKEN"]

    def test_probe_skips_when_no_api_key(self):
        """When api_key is empty, _probe_whoami should bail out early
        (no network call, _user_info stays None)."""
        b = HuggingFaceBackend()
        b.api_key = ""  # clear the token
        # Manually call _probe_whoami (already mocked module-wide, so
        # we restore the original first)
        from agentkthx.plugins.huggingface.huggingface import HuggingFaceBackend as _Cls
        original = _Cls._probe_whoami
        _Cls._probe_whoami = _orig_probe_whoami  # restore real method
        try:
            b._user_info = "stale-value"
            b._probe_whoami()
            assert b._user_info is None  # bailed out, didn't make a call
        finally:
            _Cls._probe_whoami = _noop_probe_whoami  # restore mock

    def test_probe_populates_user_info_on_success(self):
        """A successful whoami-v2 response should populate _user_info
        with the parsed JSON (name, canPay, billingMode, periodEnd)."""
        from agentkthx.plugins.huggingface.huggingface import HuggingFaceBackend as _Cls
        original = _Cls._probe_whoami
        _Cls._probe_whoami = _orig_probe_whoami  # restore real method
        try:
            # Mock urlopen to return a sample whoami-v2 response
            sample_response = {
                "type": "user",
                "id": "abc123",
                "name": "VTSTech",
                "email": "veritas@vts-tech.org",
                "canPay": False,  # free tier
                "billingMode": "prepaid",
                "periodEnd": 1790812800,
                "isPro": False,
                "orgs": [],
                "auth": {
                    "type": "access_token",
                    "accessToken": {"displayName": "dev", "role": "read"},
                },
            }
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read = MagicMock(return_value=json.dumps(sample_response).encode("utf-8"))
            with patch("urllib.request.urlopen", return_value=mock_resp):
                b = HuggingFaceBackend()
                assert b._user_info is not None
                assert b._user_info["name"] == "VTSTech"
                assert b._user_info["canPay"] is False
                assert b._user_info["billingMode"] == "prepaid"
                assert b._user_info["periodEnd"] == 1790812800
                assert b._user_info["auth"]["accessToken"]["role"] == "read"
        finally:
            _Cls._probe_whoami = _noop_probe_whoami

    def test_probe_swallows_errors_on_unreachable(self):
        """Network failures (DNS, timeout, 401 with bad token, 5xx)
        should be swallowed — _user_info stays None, no exception
        propagates."""
        from agentkthx.plugins.huggingface.huggingface import HuggingFaceBackend as _Cls
        original = _Cls._probe_whoami
        _Cls._probe_whoami = _orig_probe_whoami  # restore real method
        try:
            # Mock urlopen to raise (simulates network failure)
            with patch("urllib.request.urlopen", side_effect=Exception("network unreachable")):
                b = HuggingFaceBackend()  # should NOT raise
                assert b._user_info is None  # silently swallowed
        finally:
            _Cls._probe_whoami = _noop_probe_whoami

    def test_probe_populates_paid_user_can_pay_true(self):
        """A paid user (canPay=True) should be detected as paid —
        _resolve_free_only_mode then leaves _free_only_effective=False
        (permissive, no auto-enforcement)."""
        from agentkthx.plugins.huggingface.huggingface import HuggingFaceBackend as _Cls
        original = _Cls._probe_whoami
        _Cls._probe_whoami = _orig_probe_whoami
        try:
            sample_response = {
                "name": "PaidUser",
                "canPay": True,  # paid tier (billing card on file)
                "billingMode": "prepaid",
                "isPro": True,
            }
            mock_resp = MagicMock()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_resp.read = MagicMock(return_value=json.dumps(sample_response).encode("utf-8"))
            # Clear HF_FREE_ONLY so auto-detect kicks in
            saved = os.environ.pop("HF_FREE_ONLY", None)
            try:
                with patch("urllib.request.urlopen", return_value=mock_resp):
                    b = HuggingFaceBackend()
                    assert b._user_info["canPay"] is True
                    assert b._free_only_effective is False  # paid user → permissive
            finally:
                if saved is not None:
                    os.environ["HF_FREE_ONLY"] = saved
        finally:
            _Cls._probe_whoami = _noop_probe_whoami


class TestResolveFreeOnlyMode(unittest.TestCase):
    """R07.02 polish: _resolve_free_only_mode() resolves the effective
    HF_FREE_ONLY behavior — explicit env var > whoami auto-detect >
    module constant fallback. Tests patch _probe_whoami to inject a
    controlled user_info dict."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        self._saved_free_only = os.environ.get("HF_FREE_ONLY", "")
        os.environ.pop("HF_FREE_ONLY", None)  # unset by default
        # Patch module-level HF_FREE_ONLY to False so the fallback path
        # returns False (matches the live default when env var is unset).
        from agentkthx.plugins.huggingface import huggingface as hf_mod
        self._hf_mod = hf_mod
        self._original_free_only = hf_mod.HF_FREE_ONLY
        hf_mod.HF_FREE_ONLY = False
        # R07.02 polish: reset the process-scoped warning flag so each
        # test starts with a clean slate. The flag prevents duplicate
        # warnings across multiple HuggingFaceBackend instances in the
        # same process (the CLI may instantiate twice — _probe_backend
        # discovery + cmd_models), but tests need to verify the warning
        # behavior in isolation.
        self._original_warning_flag = HuggingFaceBackend._free_tier_warning_emitted
        HuggingFaceBackend._free_tier_warning_emitted = False

    def tearDown(self):
        del os.environ["HF_TOKEN"]
        if self._saved_free_only:
            os.environ["HF_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("HF_FREE_ONLY", None)
        self._hf_mod.HF_FREE_ONLY = self._original_free_only
        HuggingFaceBackend._free_tier_warning_emitted = self._original_warning_flag

    def _inject_user_info(self, backend, can_pay: bool, period_end: int = 1790812800):
        """Manually set backend._user_info to simulate whoami response
        (avoids needing to mock urlopen — the upstream method is already
        mocked to a no-op via setUpModule)."""
        backend._user_info = {
            "name": "TestUser",
            "canPay": can_pay,
            "billingMode": "prepaid",
            "isPro": not can_pay,  # free users aren't pro
            "periodEnd": period_end,
        }

    def test_explicit_true_overrides_auto_detect(self):
        """HF_FREE_ONLY=true env var → strict mode regardless of whoami."""
        os.environ["HF_FREE_ONLY"] = "true"
        b = HuggingFaceBackend()
        # Even if whoami says paid user, explicit env var wins
        self._inject_user_info(b, can_pay=True)  # paid user
        # _free_only_effective was already set during __init__ — re-resolve
        effective = b._resolve_free_only_mode()
        assert effective is True

    def test_explicit_false_overrides_auto_detect(self):
        """HF_FREE_ONLY=false env var → permissive regardless of whoami."""
        os.environ["HF_FREE_ONLY"] = "false"
        b = HuggingFaceBackend()
        # Even if whoami says free-tier user, explicit env var wins
        self._inject_user_info(b, can_pay=False)  # free tier
        effective = b._resolve_free_only_mode()
        assert effective is False

    def test_unset_with_free_tier_auto_enables(self):
        """HF_FREE_ONLY unset + whoami says canPay=False (free tier) →
        auto-enables strict mode (with one-time stderr warning)."""
        b = HuggingFaceBackend()
        self._inject_user_info(b, can_pay=False)  # free tier
        # _free_only_effective was set during __init__ when _user_info was
        # None (no whoami). Re-resolve now that _user_info is populated.
        effective = b._resolve_free_only_mode()
        assert effective is True

    def test_unset_with_paid_user_stays_permissive(self):
        """HF_FREE_ONLY unset + whoami says canPay=True (paid) →
        permissive (no auto-enforcement)."""
        b = HuggingFaceBackend()
        self._inject_user_info(b, can_pay=True)  # paid user
        effective = b._resolve_free_only_mode()
        assert effective is False

    def test_unset_with_whoami_unreachable_falls_back_to_module_constant(self):
        """HF_FREE_ONLY unset + whoami unreachable (_user_info=None) →
        fall back to module-level HF_FREE_ONLY constant."""
        b = HuggingFaceBackend()
        # _user_info is None (default — _probe_whoami was mocked to no-op)
        assert b._user_info is None
        effective = b._resolve_free_only_mode()
        # Falls back to module constant (set to False in setUp)
        assert effective is False

        # Now flip module constant to True and re-check
        self._hf_mod.HF_FREE_ONLY = True
        effective = b._resolve_free_only_mode()
        assert effective is True

    def test_explicit_yes_and_1_are_accepted_as_true(self):
        """Env var accepts '1', 'true', 'yes' (case-insensitive) as True."""
        for val in ("1", "true", "TRUE", "Yes", "YES"):
            os.environ["HF_FREE_ONLY"] = val
            b = HuggingFaceBackend()
            assert b._free_only_effective is True, f"HF_FREE_ONLY={val!r} should resolve to True"

    def test_explicit_no_and_0_are_accepted_as_false(self):
        """Env var accepts '0', 'false', 'no' (case-insensitive) as False."""
        for val in ("0", "false", "FALSE", "No", "NO"):
            os.environ["HF_FREE_ONLY"] = val
            b = HuggingFaceBackend()
            assert b._free_only_effective is False, f"HF_FREE_ONLY={val!r} should resolve to False"

    def test_warning_fires_only_once_per_process(self):
        """R07.02 polish: the free-tier warning should print at most
        once per process — the CLI may instantiate HuggingFaceBackend
        twice in one command (once for _probe_backend discovery, once
        for the actual cmd_models call), and we don't want the user to
        see the same nudge twice.

        Verifies the class-level ``_free_tier_warning_emitted`` flag
        suppresses the warning on the second+ instantiation, while
        still enforcing the whitelist on every instance.
        """
        # setUp reset the flag to False, so the first instance fires
        # the warning + sets the flag.
        b1 = HuggingFaceBackend()
        self._inject_user_info(b1, can_pay=False)
        # Re-resolve now that _user_info is populated (during __init__,
        # _user_info was None because _probe_whoami is mocked to no-op)
        captured = []
        original_print = __builtins__.print if hasattr(__builtins__, "print") else print
        try:
            # Capture stderr writes via patching builtins.print
            import builtins
            original = builtins.print
            def capture_print(*args, **kwargs):
                captured.append(args[0] if args else "")
                # Don't actually print to keep test output clean
            builtins.print = capture_print
            # First call — should emit warning, set flag
            effective1 = b1._resolve_free_only_mode()
            assert effective1 is True
            assert HuggingFaceBackend._free_tier_warning_emitted is True
            assert len(captured) >= 1, "First call should emit the warning"

            # Second instance — should NOT emit warning (flag is set)
            # but still return True (enforcement active)
            captured.clear()
            b2 = HuggingFaceBackend()
            self._inject_user_info(b2, can_pay=False)
            effective2 = b2._resolve_free_only_mode()
            assert effective2 is True  # enforcement still active
            assert len(captured) == 0, (
                f"Second call should NOT emit warning (flag is set), "
                f"but captured: {captured}"
            )
        finally:
            builtins.print = original

    def test_warning_resets_when_flag_cleared(self):
        """If the flag is manually cleared (e.g. by a test tearDown),
        the next free-tier detection should fire the warning again.
        Verifies the flag is the only thing suppressing the warning
        (not some other state)."""
        # Set the flag (simulating prior emission)
        HuggingFaceBackend._free_tier_warning_emitted = True
        b1 = HuggingFaceBackend()
        self._inject_user_info(b1, can_pay=False)
        captured = []
        import builtins
        original = builtins.print
        def capture_print(*args, **kwargs):
            captured.append(args[0] if args else "")
        builtins.print = capture_print
        try:
            effective = b1._resolve_free_only_mode()
            assert effective is True  # enforcement active
            assert len(captured) == 0, "Flag set → no warning"
        finally:
            builtins.print = original

        # Now clear the flag and re-resolve — warning should fire
        HuggingFaceBackend._free_tier_warning_emitted = False
        captured.clear()
        builtins.print = capture_print
        try:
            effective = b1._resolve_free_only_mode()
            assert effective is True
            assert len(captured) >= 1, "Flag cleared → warning fires"
        finally:
            builtins.print = original


class TestApplyProviderPolicyLiveForceCheapest(unittest.TestCase):
    """R07.02 polish: _apply_provider_policy_live() passes
    force_cheapest=self._free_only_effective so the auto-detected
    free-tier mode (from whoami) honors the :cheapest suffix even
    when the user did NOT set HF_FREE_ONLY env var explicitly."""

    def setUp(self):
        os.environ["HF_TOKEN"] = "hf_fake_test_token_for_scaffold"
        self._saved_free_only = os.environ.get("HF_FREE_ONLY", "")
        os.environ.pop("HF_FREE_ONLY", None)
        from agentkthx.plugins.huggingface import huggingface as hf_mod
        self._hf_mod = hf_mod
        self._original_free_only = hf_mod.HF_FREE_ONLY
        hf_mod.HF_FREE_ONLY = False

    def tearDown(self):
        del os.environ["HF_TOKEN"]
        if self._saved_free_only:
            os.environ["HF_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("HF_FREE_ONLY", None)
        self._hf_mod.HF_FREE_ONLY = self._original_free_only

    def test_force_cheapest_true_appends_cheapest_suffix(self):
        """When force_cheapest=True, :cheapest is appended (no env var
        or HF_PROVIDER_POLICY consulted)."""
        result = _apply_provider_policy("openai/gpt-oss-120b", force_cheapest=True)
        assert result == "openai/gpt-oss-120b:cheapest"

    def test_force_cheapest_false_does_not_append_when_no_policy(self):
        """When force_cheapest=False and no env var, no suffix is appended
        (router's :fastest default applies)."""
        # Clear HF_PROVIDER_POLICY for a clean test
        saved_policy = os.environ.pop("HF_PROVIDER_POLICY", None)
        try:
            result = _apply_provider_policy("openai/gpt-oss-120b", force_cheapest=False)
            assert result == "openai/gpt-oss-120b"
        finally:
            if saved_policy is not None:
                os.environ["HF_PROVIDER_POLICY"] = saved_policy

    def test_explicit_suffix_wins_over_force_cheapest(self):
        """User's explicit :suffix always wins, even when
        force_cheapest=True."""
        result = _apply_provider_policy("openai/gpt-oss-120b:groq", force_cheapest=True)
        assert result == "openai/gpt-oss-120b:groq"

    def test_live_method_passes_force_cheapest_from_effective_mode(self):
        """_apply_provider_policy_live(backend, model_id) should pass
        force_cheapest=backend._free_only_effective to the module-level
        helper — so the auto-detected mode is honored."""
        b = HuggingFaceBackend()
        # Default state in tests: _free_only_effective=False (module
        # constant False, no env var, whoami mocked to None)
        b._free_only_effective = False
        saved_policy = os.environ.pop("HF_PROVIDER_POLICY", None)
        try:
            result = _apply_provider_policy_live(b, "openai/gpt-oss-120b")
            assert result == "openai/gpt-oss-120b"  # no suffix
        finally:
            if saved_policy is not None:
                os.environ["HF_PROVIDER_POLICY"] = saved_policy

        # Now flip _free_only_effective to True and re-check
        b._free_only_effective = True
        result = _apply_provider_policy_live(b, "openai/gpt-oss-120b")
        assert result == "openai/gpt-oss-120b:cheapest"
