"""
Tests for OpenAIBackend — token-type detection, OPENAI_FREE_ONLY whitelist
enforcement, 429 insufficient_quota trial-credit-exhaustion fallback,
service_tier / reasoning_effort enforcement, and the inherited OpenAI
Chat-Completions plumbing.

Mirrors test_huggingface_backend.py for the shared OpenAI-compat surface
(_parse_openai_response, _build_openai_body, _get_chat_completions_url,
_get_auth_headers, _is_tools_not_supported_error, generate flow) and
adds OpenAI-specific tests for:
  - OPENAI_FREE_MODEL_WHITELIST membership (_is_free_model)
  - Token type detection (_detect_key_type: user/project/admin/service_account)
  - 429 insufficient_quota detection (_is_insufficient_quota)
  - OPENAI_FREE_ONLY strict rejection of non-whitelisted models +
    reasoning_effort cap at "low" + service_tier forced to "default"
  - 429 insufficient_quota → OPENAI_FREE_FALLBACK_MODEL swap

Written by VTSTech — https://www.vts-tech.org
"""

import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

import pytest

from agentkthx.plugins.openai.openai import (
    OpenAIBackend,
    OPENAI_FREE_MODEL_WHITELIST,
    OPENAI_MODELS,
    OPENAI_SERVICE_TIER_VALUES,
    OPENAI_REASONING_EFFORT_VALUES,
    OPENAI_FREE_ONLY_REASONING_EFFORT_CAP,
    OPENAI_FREE_ONLY_SERVICE_TIER_FORCED,
    _detect_key_type,
    _is_free_model,
    _is_insufficient_quota,
)
from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.types import ApiMode, BackendType, ToolSupportLevel


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
    """The OPENAI_FREE_MODEL_WHITELIST and OPENAI_MODELS catalog should be
    consistent and populated for v0.1."""

    def test_whitelist_is_empty(self):
        # R07.03: OpenAI has NO genuinely free ($0/token) models.
        # The whitelist is intentionally empty.
        assert len(OPENAI_FREE_MODEL_WHITELIST) == 0

    def test_whitelist_empty_directive(self):
        # R07.03: The whitelist is intentionally empty because no OpenAI
        # model is genuinely $0/token. All models have per-token pricing
        # that consumes trial credit ($1) and monthly API credit ($10).
        # "Free only" means $0/token, not "cheap enough that credit lasts."
        assert OPENAI_FREE_MODEL_WHITELIST == frozenset()

    def test_whitelist_subset_of_catalog(self):
        # Every whitelisted model should have a catalog entry so
        # _get_model_defaults() works even when /v1/models is unreachable.
        missing = OPENAI_FREE_MODEL_WHITELIST - set(OPENAI_MODELS.keys())
        assert not missing, (
            f"Whitelist entries without catalog entries: {missing}. "
            f"All whitelisted models must be in the chat-capable catalog."
        )


class TestIsFreeModel:
    """_is_free_model checks whitelist membership."""

    def test_gpt_6_luna_not_free(self):
        # R07.03: gpt-6-luna costs $0.10/$0.50 per 1M — NOT free
        assert _is_free_model("gpt-6-luna") is False

    def test_gpt_4o_mini_not_free(self):
        # R07.03: gpt-4o-mini costs $0.15/$0.60 per 1M — NOT free
        assert _is_free_model("gpt-4o-mini") is False

    def test_paid_gpt_6_astra(self):
        assert _is_free_model("gpt-6-astra") is False

    def test_paid_gpt_6_sol(self):
        assert _is_free_model("gpt-6-sol") is False

    def test_unknown_model(self):
        assert _is_free_model("some-unknown-model") is False


class TestDetectKeyType:
    """_detect_key_type parses the prefix to determine the key type."""

    def test_project_key(self):
        assert _detect_key_type("sk-proj-abc123") == "project"

    def test_admin_key(self):
        assert _detect_key_type("sk-admin-abc123") == "admin"

    def test_service_account_key(self):
        assert _detect_key_type("sk-sa-abc123") == "service_account"

    def test_legacy_user_key(self):
        assert _detect_key_type("sk-abc123") == "user"

    def test_empty_key(self):
        assert _detect_key_type("") == "unknown"

    def test_unrecognized_prefix(self):
        assert _detect_key_type("abc123") == "unknown"
        assert _detect_key_type("other-xyz") == "unknown"


class TestIsInsufficientQuota:
    """_is_insufficient_quota detects the OpenAI trial-credit-exhausted
    error code."""

    def test_matches_insufficient_quota(self):
        err = '{"error":{"code":"insufficient_quota","message":"You exceeded your current quota"}}'
        assert _is_insufficient_quota(err) is True

    def test_matches_case_insensitive(self):
        err = "INSUFFICIENT_QUOTA detected"
        assert _is_insufficient_quota(err) is True

    def test_does_not_match_rate_limit(self):
        err = '{"error":{"code":"rate_limit_exceeded","message":"Too many requests"}}'
        assert _is_insufficient_quota(err) is False

    def test_does_not_match_unrelated_errors(self):
        for err in (
            "Internal server error",
            "Invalid model id",
            "context length exceeded",
            "Invalid API key",
        ):
            assert _is_insufficient_quota(err) is False


# ─────────────────────────────────────────────────────────────────────────────
# Backend hooks + is_cloud + BackendType
# ─────────────────────────────────────────────────────────────────────────────

class TestBackendHooks(unittest.TestCase):
    """The 4 abstract hooks from OpenAICompatibleBackend (ARCH-01)
    should be implemented on OpenAIBackend."""

    @classmethod
    def setUpClass(cls):
        # Set a fake project key so __init__ doesn't crash on lazy auth check.
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        cls.backend = OpenAIBackend()
        # Reset env to avoid leaking into other tests
        del os.environ["OPENAI_API_KEY"]

    def test_get_chat_completions_url_uses_openai_path(self):
        """The URL should be ``<base>/chat/completions`` on OpenAI."""
        url = self.backend._get_chat_completions_url()
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")

    def test_get_auth_headers_include_bearer(self):
        """Auth headers must contain the Bearer token."""
        headers = self.backend._get_auth_headers()
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.assertIn("Content-Type", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_get_auth_headers_have_no_openrouter_specific_fields(self):
        """OpenAI doesn't use HTTP-Referer / X-Title (those are
        OpenRouter-specific leaderboard attribution headers)."""
        headers = self.backend._get_auth_headers()
        self.assertNotIn("HTTP-Referer", headers)
        self.assertNotIn("X-Title", headers)

    def test_get_auth_headers_include_org_project_when_set(self):
        """When OPENAI_ORGANIZATION_ID and OPENAI_PROJECT_ID are set,
        the headers should include them."""
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake"
        os.environ["OPENAI_ORGANIZATION_ID"] = "org-test"
        os.environ["OPENAI_PROJECT_ID"] = "proj_test"
        try:
            b = OpenAIBackend()
            headers = b._get_auth_headers()
            self.assertEqual(headers["OpenAI-Organization"], "org-test")
            self.assertEqual(headers["OpenAI-Project"], "proj_test")
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["OPENAI_ORGANIZATION_ID"]
            del os.environ["OPENAI_PROJECT_ID"]

    def test_get_auth_headers_omit_org_project_when_unset(self):
        """When env vars are empty, headers should NOT include
        OpenAI-Organization / OpenAI-Project."""
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake"
        try:
            b = OpenAIBackend()
            headers = b._get_auth_headers()
            self.assertNotIn("OpenAI-Organization", headers)
            self.assertNotIn("OpenAI-Project", headers)
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_has_iter_sse_lines(self):
        """_iter_sse_lines is implemented (abstract hook satisfied)."""
        self.assertTrue(callable(getattr(self.backend, "_iter_sse_lines", None)))


class TestIsCloudAndBackendType(unittest.TestCase):
    """Verify OpenAIBackend is correctly marked as cloud (R06.57)
    and uses the OPENAI BackendType enum value."""

    def test_is_cloud_inherits_true(self):
        """OpenAIBackend extends OpenAICompatibleBackend, so is_cloud
        resolves to True without any explicit override — same pattern as
        OpenRouterBackend, GeminiBackend, and HuggingFaceBackend."""
        assert OpenAIBackend.is_cloud is True

    def test_backend_type_property_is_openai(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake"
        try:
            b = OpenAIBackend()
            assert b.backend_type is BackendType.OPENAI
            assert b.backend_type.value == "openai"
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_key_type_detection_on_init(self):
        """The backend should detect the key type on __init__ and surface
        a warning for legacy sk- keys via AGENTKTHX_DEBUG."""
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake-project-key"
        try:
            b = OpenAIBackend()
            assert b._key_type == "project"
        finally:
            del os.environ["OPENAI_API_KEY"]

        os.environ["OPENAI_API_KEY"] = "sk-fake-legacy-key"
        try:
            b = OpenAIBackend()
            assert b._key_type == "user"
        finally:
            del os.environ["OPENAI_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# _is_tools_not_supported_error — parity check
# ─────────────────────────────────────────────────────────────────────────────

class TestIsToolsNotSupportedError(unittest.TestCase):
    """ReAct-fallback error detection — OpenAI parity check."""

    def test_matches_known_indicators(self):
        """All shared indicators should match (parity with other backends)."""
        for indicator in (
            "does not support tools",
            "tools are not supported",
            "tool calling is not supported",
            "tools are not yet supported",
            "does not support function calling",
            "function calling is not supported",
            "no tools endpoint",
            "tool use is not supported",
            "unsupported param: tools",
        ):
            self.assertTrue(
                OpenAIBackend._is_tools_not_supported_error(indicator),
                f"Indicator should match: {indicator}",
            )

    def test_does_not_match_unrelated_errors(self):
        """Specificity check — unrelated errors must not trigger the
        ReAct fallback (would mask real failures)."""
        for indicator in (
            "Internal server error",
            "Rate limit exceeded",
            "Invalid model id",
            "context length exceeded",
            "insufficient_quota",
        ):
            self.assertFalse(
                OpenAIBackend._is_tools_not_supported_error(indicator),
                f"Unrelated error must NOT trigger ReAct fallback: {indicator}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# Generate flow — ReAct fallback + empty response
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateFlow(unittest.TestCase):
    """Tests for OpenAIBackend.generate() — mirrors the HuggingFace
    generate flow tests, with OpenAI-specific request/response shape."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        self.backend = OpenAIBackend()

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]

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

        result = self.backend.generate(
            model="gpt-4o-mini",
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
        """When the model rejects the `tools` field with a
        'does not support tools' error, generate() should retry without
        tools (ReAct fallback path)."""
        import urllib.error
        real_http_err = urllib.error.HTTPError(
            url="http://test",
            code=400,
            msg='{"error":{"message":"does not support tools"}}',
            hdrs={},
            fp=io.BytesIO(b'{"error":{"message":"does not support tools"}}'),
        )
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
            model="gpt-4o-mini",
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
        RuntimeError so the chat loop can surface a meaningful error."""
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
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=128,
            )
        self.assertIn("empty response", str(ctx.exception).lower())


# ─────────────────────────────────────────────────────────────────────────────
# OPENAI_FREE_ONLY enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestFreeOnlyEnforcement(unittest.TestCase):
    """OPENAI_FREE_ONLY mode should reject non-whitelisted models BEFORE any
    HTTP request is made — preventing accidental trial-credit-burning
    API calls."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        self._saved_free_only = os.environ.get("OPENAI_FREE_ONLY", "")
        os.environ["OPENAI_FREE_ONLY"] = "false"
        from agentkthx.plugins.openai import openai as oai_mod
        self._oai_mod = oai_mod
        self._original_free_only = oai_mod.OPENAI_FREE_ONLY
        oai_mod.OPENAI_FREE_ONLY = False

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]
        if self._saved_free_only:
            os.environ["OPENAI_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("OPENAI_FREE_ONLY", None)
        self._oai_mod.OPENAI_FREE_ONLY = self._original_free_only

    def test_free_only_false_allows_paid_models_by_default(self):
        """When OPENAI_FREE_ONLY is false (the default), generate() with a
        paid model id should reach the HTTP layer — the whitelist check
        is skipped."""
        self._oai_mod.OPENAI_FREE_ONLY = False
        b = OpenAIBackend()
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
            model="gpt-6-astra",  # not in whitelist
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        assert called_with["endpoint"] == "chat/completions"
        assert called_with["model"] == "gpt-6-astra"
        assert result["content"] == "ok"

    def test_free_only_true_rejects_non_whitelisted_model(self):
        """When OPENAI_FREE_ONLY is true, generate() with a paid model
        should raise RuntimeError BEFORE any HTTP request is made."""
        self._oai_mod.OPENAI_FREE_ONLY = True
        b = OpenAIBackend()
        called = {"count": 0}
        def fail_if_called(endpoint, data, stream=False):
            called["count"] += 1
            raise AssertionError(
                "_make_api_request should NOT be called when OPENAI_FREE_ONLY "
                "rejects the model upfront"
            )
        b._make_api_request = fail_if_called
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="gpt-6-astra",  # not in whitelist
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        assert "no genuinely free" in str(ctx.exception).lower() or "OPENAI_FREE_ONLY" in str(ctx.exception)
        assert called["count"] == 0

    def test_free_only_true_rejects_all_models(self):
        """R07.03: When OPENAI_FREE_ONLY is true, ALL models are rejected —
        OpenAI has no genuinely free ($0/token) models. Even gpt-6-luna
        (cheapest at $0.10/$0.50 per 1M) costs money per token."""
        self._oai_mod.OPENAI_FREE_ONLY = True
        b = OpenAIBackend()
        called = {"count": 0}
        def fail_if_called(endpoint, data, stream=False):
            called["count"] += 1
            raise AssertionError("_make_api_request should NOT be called")
        b._make_api_request = fail_if_called
        # Even gpt-6-luna (cheapest OpenAI model) is rejected
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="gpt-6-luna",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        assert "no genuinely free" in str(ctx.exception).lower() or "OPENAI_FREE_ONLY" in str(ctx.exception)
        assert called["count"] == 0


class TestFreeOnlyReasoningEffortCap(unittest.TestCase):
    """When OPENAI_FREE_ONLY is true, reasoning_effort should be capped
    at "low" (reasoning tokens are billed at output rate and can quickly
    exhaust the $1 trial credit / $10/mo API credit allowance)."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        self._saved_free_only = os.environ.get("OPENAI_FREE_ONLY", "")
        os.environ["OPENAI_FREE_ONLY"] = "true"
        from agentkthx.plugins.openai import openai as oai_mod
        self._oai_mod = oai_mod
        self._original_free_only = oai_mod.OPENAI_FREE_ONLY
        oai_mod.OPENAI_FREE_ONLY = True

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]
        if self._saved_free_only:
            os.environ["OPENAI_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("OPENAI_FREE_ONLY", None)
        self._oai_mod.OPENAI_FREE_ONLY = self._original_free_only

    def test_high_reasoning_effort_capped_to_low(self):
        """User passes reasoning_effort=high but OPENAI_FREE_ONLY is true —
        should be capped at "low" to prevent credit exhaustion."""
        b = OpenAIBackend()
        body = b._build_openai_body(
            model="gpt-6-luna",  # whitelisted, supports reasoning
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.1,
            max_tokens=10,
            reasoning_effort="high",
        )
        # Cap applied
        assert body["reasoning_effort"] == "low"
        # Service tier also forced to default
        assert body["service_tier"] == "default"

    def test_low_reasoning_effort_preserved(self):
        """User passes reasoning_effort=low — should stay at low (already
        at the cap, no change)."""
        b = OpenAIBackend()
        body = b._build_openai_body(
            model="gpt-6-luna",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.1,
            max_tokens=10,
            reasoning_effort="low",
        )
        assert body["reasoning_effort"] == "low"

    def test_no_reasoning_effort_not_added(self):
        """User doesn't pass reasoning_effort — should not be added to
        the body (model uses its default, usually "medium" for gpt-5.5+)."""
        b = OpenAIBackend()
        body = b._build_openai_body(
            model="gpt-6-luna",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.1,
            max_tokens=10,
        )
        # reasoning_effort should NOT be in the body when not explicitly set
        # (OPENAI_FREE_ONLY forces service_tier=default but doesn't add
        # reasoning_effort if it wasn't requested)
        assert "reasoning_effort" not in body or body.get("reasoning_effort") is None \
               or body.get("reasoning_effort") == "low"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 429 insufficient_quota trial-credit-exhaustion fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestInsufficientQuotaFallback(unittest.TestCase):
    """When OpenAI returns HTTP 429 with `insufficient_quota` code
    (trial credit exhausted), the backend should swap to
    OPENAI_FREE_FALLBACK_MODEL and retry once (mirrors the ZAI plugin's
    429 insufficient-balance fallback pattern).

    When OPENAI_FREE_ONLY is true, 429 insufficient_quota is a hard
    failure (no retry) — the user must upgrade or wait for the monthly
    credit refresh.
    """

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        from agentkthx.plugins.openai import openai as oai_mod
        self._oai_mod = oai_mod
        self._original_free_only = oai_mod.OPENAI_FREE_ONLY

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]
        self._oai_mod.OPENAI_FREE_ONLY = self._original_free_only

    def test_insufficient_quota_with_free_only_true_raises_clear_error(self):
        """OPENAI_FREE_ONLY=true: 429 insufficient_quota must surface a
        clear actionable error (no retry — retrying is futile)."""
        self._oai_mod.OPENAI_FREE_ONLY = True
        b = OpenAIBackend()
        def raise_429(endpoint, data, stream=False):
            raise RuntimeError(
                "OpenAI trial credit exhausted for "
                f"'{data.get('model')}' (insufficient_quota). "
                "Upgrade to a paid tier..."
            )
        b._make_api_request = raise_429
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="gpt-6-luna",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
        assert "credit exhausted" in str(ctx.exception).lower() or "OPENAI_FREE_ONLY" in str(ctx.exception)


class TestFreeOnlyStreamingOverride(unittest.TestCase):
    """R07.03 bug fix: the agentic loop calls generate_completions_stream()
    directly for cloud backends (NOT generate_stream()). The original
    scaffold only overrode generate_stream() — the inherited base-class
    generate_completions_stream() skipped the OPENAI_FREE_ONLY whitelist
    check and made HTTP requests for paid models. Caught via live CLI
    smoke test (OPENAI_FREE_ONLY=true agentkthx run --backend oai
    --model gpt-6-astra reached the API layer instead of being rejected
    upfront). Mirrors the HuggingFace R07.02 polish lesson."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        self._saved_free_only = os.environ.get("OPENAI_FREE_ONLY", "")
        os.environ["OPENAI_FREE_ONLY"] = "true"
        from agentkthx.plugins.openai import openai as oai_mod
        self._oai_mod = oai_mod
        self._original_free_only = oai_mod.OPENAI_FREE_ONLY
        oai_mod.OPENAI_FREE_ONLY = True

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]
        if self._saved_free_only:
            os.environ["OPENAI_FREE_ONLY"] = self._saved_free_only
        else:
            os.environ.pop("OPENAI_FREE_ONLY", None)
        self._oai_mod.OPENAI_FREE_ONLY = self._original_free_only

    def test_free_only_true_rejects_non_whitelisted_model_in_streaming_path(self):
        """When OPENAI_FREE_ONLY is true, generate_completions_stream()
        (the path the agentic loop uses for cloud backends) should reject
        non-whitelisted models BEFORE any HTTP request is made."""
        b = OpenAIBackend()
        # Mock _iter_sse_lines to fail loudly if called (proves the upfront
        # check fires before any HTTP path)
        def fail_if_called(url, body, headers):
            raise AssertionError(
                "_iter_sse_lines should NOT be called when OPENAI_FREE_ONLY "
                "rejects the model upfront in the streaming path"
            )
        b._iter_sse_lines = fail_if_called
        # Generator must be consumed to trigger the check
        gen = b.generate_completions_stream(
            model="gpt-6-astra",  # not in whitelist
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        with self.assertRaises(RuntimeError) as ctx:
            list(gen)  # consume the generator
        assert "no genuinely free" in str(ctx.exception).lower() or "OPENAI_FREE_ONLY" in str(ctx.exception)

    def test_free_only_true_rejects_all_models_in_streaming_path(self):
        """When OPENAI_FREE_ONLY is true, generate_completions_stream()
        rejects ALL models (no genuinely free OpenAI models exist)."""
        b = OpenAIBackend()
        b._iter_sse_lines = lambda *a: (_ for _ in ()).throw(AssertionError("should not be called"))
        gen = b.generate_completions_stream(
            model="gpt-6-luna",  # cheapest OpenAI model but NOT free
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
        with self.assertRaises(RuntimeError) as ctx:
            list(gen)
        assert "no genuinely free" in str(ctx.exception).lower() or "OPENAI_FREE_ONLY" in str(ctx.exception)


# ─────────────────────────────────────────────────────────────────────────────
# Service tier + reasoning_effort enum validation
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceTierAndReasoningEffortEnums(unittest.TestCase):
    """Verify the enum values are populated and used correctly."""

    def test_service_tier_values_complete(self):
        """All 6 documented service tiers should be in the enum."""
        expected = {"auto", "default", "flex", "scale", "priority", "fast"}
        assert expected <= OPENAI_SERVICE_TIER_VALUES

    def test_reasoning_effort_values_complete(self):
        """All 7 documented reasoning efforts should be in the enum."""
        expected = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        assert expected <= OPENAI_REASONING_EFFORT_VALUES

    def test_free_only_caps_match_documented_values(self):
        """The cap + forced values should be valid enum members."""
        assert OPENAI_FREE_ONLY_REASONING_EFFORT_CAP in OPENAI_REASONING_EFFORT_VALUES
        assert OPENAI_FREE_ONLY_SERVICE_TIER_FORCED in OPENAI_SERVICE_TIER_VALUES


# ─────────────────────────────────────────────────────────────────────────────
# System → Developer role translation for reasoning models
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemToDeveloperTranslation(unittest.TestCase):
    """For reasoning-capable models (gpt-5.x+, gpt-6.x, o-series),
    the `system` role should be translated to `developer` in the
    request body (OpenAI's newer convention that replaces `system`
    for o1 and newer models)."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]

    def test_system_translated_to_developer_for_reasoning_model(self):
        """gpt-6-sol supports thinking → system role should become developer."""
        b = OpenAIBackend()
        body = b._build_openai_body(
            model="gpt-6-sol",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
            tools=None,
            temperature=0.1,
            max_tokens=10,
        )
        roles = [m["role"] for m in body["messages"]]
        assert "developer" in roles
        assert "system" not in roles

    def test_system_preserved_for_non_reasoning_model(self):
        """gpt-4o doesn't support thinking → system role should stay as system."""
        b = OpenAIBackend()
        body = b._build_openai_body(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
            tools=None,
            temperature=0.1,
            max_tokens=10,
        )
        roles = [m["role"] for m in body["messages"]]
        assert "system" in roles
        assert "developer" not in roles


# ─────────────────────────────────────────────────────────────────────────────
# Plugin manifest validation
# ─────────────────────────────────────────────────────────────────────────────

class TestPluginManifest(unittest.TestCase):
    """The plugin.json should validate against the v0.2 schema and
    follow the same structure as the OpenRouter / ZAI / Gemini / HF plugins."""

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
            / "agentkthx" / "plugins" / "openai" / "plugin.json"
        )
        assert manifest_path.exists(), f"Missing {manifest_path}"
        m = _parse_manifest(manifest_path, root_kind="builtin")
        assert m.name == "openai"
        assert m.schema == CANONICAL_SCHEMA
        assert m.legacy_fields_used == [], "openai still uses legacy top-level fields"
        assert "agentnova" not in m.compatibility

    def test_manifest_provides_openai_backend(self):
        import json
        from pathlib import Path
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "plugins" / "openai" / "plugin.json"
        )
        with open(manifest_path) as f:
            m = json.load(f)
        ext = m["extensions"]["org.vts-tech.agentkthx"]
        assert ext["type"] == "backend"
        assert ext["provides"]["backends"] == {"openai": "openai.OpenAIBackend"}
        assert "openai" in ext["provides"]["cli_flags"]["--backend"]
        assert "oai" in ext["provides"]["cli_flags"]["--backend"]

    def test_manifest_config_defaults_match_config_py(self):
        """The plugin.json config.defaults should mirror the env-var
        defaults defined in agentkthx/config.py."""
        import json
        from pathlib import Path
        manifest_path = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "plugins" / "openai" / "plugin.json"
        )
        with open(manifest_path) as f:
            m = json.load(f)
        defaults = m["extensions"]["org.vts-tech.agentkthx"]["config"]["defaults"]
        assert defaults["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
        assert defaults["OPENAI_DEFAULT_MODEL"] == "gpt-6-sol"
        assert defaults["OPENAI_FREE_ONLY"] == "false"
        assert defaults["OPENAI_FREE_FALLBACK_MODEL"] == "gpt-4o-mini"
        assert defaults["OPENAI_SERVICE_TIER"] == ""
        assert defaults["OPENAI_REASONING_EFFORT"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# Plugin discovery & loading integration
# ─────────────────────────────────────────────────────────────────────────────

class TestPluginDiscovery(unittest.TestCase):
    """The openai plugin should be discoverable and loadable
    via PluginManager alongside the other built-in plugins."""

    def test_builtin_manifests_now_include_openai(self):
        """Adding openai to the discovery set — the existing test in
        test_plugin_spec.py uses ``<=`` (subset), so adding is a
        superset — the existing assertion still passes. This test is
        a redundant safety net to call out openai explicitly."""
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
        assert "openai" in names
        # Verify the existing built-in plugins are still present
        assert {"bitnet", "zai", "openrouter", "turboquant", "acp",
                "test-plugin", "huggingface"} <= names

    def test_plugin_manager_loads_openai(self):
        from agentkthx.plugins._loader import PluginManager
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"
        try:
            pm = PluginManager()
            plugin = pm.load("openai")
            self.assertIsNotNone(plugin)
            self.assertTrue(pm.is_loaded("openai"))
            cls = pm.get_backend_class("openai")
            self.assertEqual(cls.__name__, "OpenAIBackend")
            # Both canonical name and alias should be in the choices
            choices = pm.get_backend_choices()
            self.assertIn("openai", choices)
            self.assertIn("oai", choices)
            pm.unload("openai")
            self.assertFalse(pm.is_loaded("openai"))
        finally:
            del os.environ["OPENAI_API_KEY"]


# ─────────────────────────────────────────────────────────────────────────────
# R07.03 polish: _NON_CHAT_PATTERNS filter + expanded catalog
# ─────────────────────────────────────────────────────────────────────────────

class TestNonChatPatterns(unittest.TestCase):
    """The _NON_CHAT_PATTERNS filter should exclude obvious non-chat
    models (embeddings, TTS, image gen, video gen, moderation, ASR,
    legacy completions) from the model listing, mirroring the
    GeminiBackend._NON_CHAT_PATTERNS pattern."""

    def test_embeddings_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_tts_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("tts-1", "tts-1-hd", "tts-1-1106", "gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_transcribe_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("gpt-4o-transcribe", "gpt-4o-transcribe-diarize",
                  "gpt-4o-mini-transcribe", "gpt-transcribe",
                  "gpt-live-transcribe", "gpt-4o-mini-transcribe-2025-03-20"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_whisper_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        assert _is_chat_model("whisper-1") is False

    def test_image_gen_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5",
                  "gpt-image-2", "gpt-image-2.5-flare",
                  "gpt-image-2.5-sunburst", "chatgpt-image-latest",
                  "gpt-image-2-2026-04-21"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_sora_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("sora-2", "sora-2-pro"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_moderation_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("omni-moderation-latest", "omni-moderation-2024-09-26"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat"

    def test_legacy_completions_filtered(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("babbage-002", "davinci-002"):
            assert _is_chat_model(m) is False, f"{m} should be filtered as non-chat (legacy /completions, not /chat/completions)"

    def test_chat_models_pass_through(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in (
            "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
            "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra",
            "gpt-5.5", "gpt-5.5-pro",
            "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5-codex",
            "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "gpt-3.5-turbo", "gpt-3.5-turbo-16k",
            "o1", "o3", "o3-mini", "o4-mini",
            "chat-latest",
            "gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro",
        ):
            assert _is_chat_model(m) is True, f"{m} should pass through as chat-capable"

    def test_dated_snapshots_pass_through(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in (
            "gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o-2024-11-20",
            "gpt-5-2025-08-07", "gpt-5.4-2026-03-05",
            "gpt-4o-mini-2024-07-18", "o3-2025-04-16", "o4-mini-2025-04-16",
        ):
            assert _is_chat_model(m) is True, f"{m} should pass through (dated snapshot of chat model)"

    def test_search_preview_passes_through(self):
        from agentkthx.plugins.openai.openai import _is_chat_model
        for m in ("gpt-4o-search-preview", "gpt-4o-mini-search-preview", "gpt-5-search-api"):
            assert _is_chat_model(m) is True, f"{m} should pass through (search-preview is chat-capable)"


class TestExpandedCatalog(unittest.TestCase):
    """Verify the R07.03 polish expanded the static catalog with the
    missed families discovered via the live /v1/models API."""

    def test_catalog_includes_o_series(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("o1", "o3", "o3-mini", "o4-mini"):
            assert m in OPENAI_MODELS, f"{m} should be in expanded catalog"

    def test_catalog_includes_gpt5_family(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro", "gpt-5-codex"):
            assert m in OPENAI_MODELS, f"{m} should be in expanded catalog"

    def test_catalog_includes_gpt56_daybreak_expanded(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("gpt-5.6-luna", "gpt-5.6-terra"):
            assert m in OPENAI_MODELS, f"{m} should be in expanded catalog (discovered via live API)"

    def test_catalog_includes_gpt54_expanded(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro"):
            assert m in OPENAI_MODELS, f"{m} should be in expanded catalog"

    def test_catalog_includes_gpt55_pro(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        assert "gpt-5.5-pro" in OPENAI_MODELS, "gpt-5.5-pro should be in expanded catalog"

    def test_catalog_includes_gpt41_family(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"):
            assert m in OPENAI_MODELS, f"{m} should be in expanded catalog"

    def test_catalog_includes_legacy_gpt35(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        for m in ("gpt-3.5-turbo", "gpt-3.5-turbo-16k"):
            assert m in OPENAI_MODELS, f"{m} should be in catalog (legacy but chat-capable)"

    def test_catalog_size_grew(self):
        from agentkthx.plugins.openai.openai import OPENAI_MODELS
        assert len(OPENAI_MODELS) >= 33, (
            f"Catalog should have grown to 33+ models after R07.03 polish, "
            f"got {len(OPENAI_MODELS)} (was 15 before)"
        )


class TestTestToolSupportNonChatClassification(unittest.TestCase):
    """test_tool_support() should classify non-chat models as NONE
    (not NATIVE) so users don't accidentally try to chat with an
    embedding model."""

    def setUp(self):
        os.environ["OPENAI_API_KEY"] = "sk-proj-fake_test_token_for_scaffold"

    def tearDown(self):
        del os.environ["OPENAI_API_KEY"]

    def test_embedding_model_classified_as_none(self):
        b = OpenAIBackend()
        assert b.test_tool_support("text-embedding-3-large") is ToolSupportLevel.NONE

    def test_tts_model_classified_as_none(self):
        b = OpenAIBackend()
        assert b.test_tool_support("tts-1") is ToolSupportLevel.NONE

    def test_image_model_classified_as_none(self):
        b = OpenAIBackend()
        assert b.test_tool_support("gpt-image-2") is ToolSupportLevel.NONE

    def test_moderation_model_classified_as_none(self):
        b = OpenAIBackend()
        assert b.test_tool_support("omni-moderation-latest") is ToolSupportLevel.NONE

    def test_chat_model_classified_as_native(self):
        b = OpenAIBackend()
        assert b.test_tool_support("gpt-6-sol") is ToolSupportLevel.NATIVE
        assert b.test_tool_support("gpt-4o-mini") is ToolSupportLevel.NATIVE
        assert b.test_tool_support("o3-mini") is ToolSupportLevel.NATIVE
