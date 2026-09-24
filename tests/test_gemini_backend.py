"""
Tests for GeminiBackend — OpenAI-compat response parsing, body construction
(with Gemini-specific thinking_config / extra_body / service_tier handling),
and GEMINI_FREE_ONLY model filtering.

Also tests the inherited _parse_openai_response() (Gemini uses the parent
class's parser unchanged — we cover it here for parity with the OpenRouter
test suite).

Live-API tests (anything requiring a real GEMINI_API_KEY) are skipped
unless the env var is set. They're here so a developer with a key can
run them with: GEMINI_API_KEY=... pytest tests/test_gemini_backend.py

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch, MagicMock

import pytest

from agentkthx.plugins.gemini.gemini import (
    GeminiBackend,
    GEMINI_MODELS,
    detect_gemini_family,
)
from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

# Live-API tests require an explicit opt-in env var AND a real-looking key.
# Setting GEMINI_API_KEY=test-key (e.g. for unit tests) does NOT opt in.
# To run the live tests: export GEMINI_API_KEY=<real_key> GEMINI_RUN_LIVE_TESTS=1
_LIVE_KEY = GEMINI_API_KEY and len(GEMINI_API_KEY) >= 20 and not GEMINI_API_KEY.startswith("test")
_LIVE_OPT_IN = os.environ.get("GEMINI_RUN_LIVE_TESTS", "").lower() in ("1", "true", "yes")
_RUN_LIVE = _LIVE_KEY and _LIVE_OPT_IN


def _make_tool() -> Tool:
    return Tool(
        name="shell",
        description="Run a shell command",
        params=[ToolParam(name="command", type="string", description="cmd")],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Static / unit tests — no network, no API key required
# ─────────────────────────────────────────────────────────────────────────────

class TestParseOpenAiResponse(unittest.TestCase):
    """Direct tests for the inherited _parse_openai_response helper."""

    def test_parses_native_tool_calls(self):
        raw = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        out = GeminiBackend._parse_openai_response(raw)
        self.assertEqual(out["content"], "")
        self.assertEqual(out["finish_reason"], "tool_calls")
        self.assertEqual(len(out["tool_calls"]), 1)
        tc = out["tool_calls"][0]
        self.assertEqual(tc["id"], "call_abc")
        self.assertEqual(tc["name"], "shell")
        self.assertEqual(tc["arguments"], {"command": "echo hi"})
        self.assertEqual(out["usage"]["total_tokens"], 15)

    def test_handles_arguments_as_object(self):
        """Some Gemini responses return arguments as object, not JSON string."""
        raw = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "calc",
                            "arguments": {"expression": "2+2"},
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        out = GeminiBackend._parse_openai_response(raw)
        self.assertEqual(out["tool_calls"][0]["arguments"], {"expression": "2+2"})

    def test_handles_malformed_arguments_gracefully(self):
        raw = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": "not-valid-json{",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        out = GeminiBackend._parse_openai_response(raw)
        self.assertEqual(out["tool_calls"][0]["arguments"], {"_raw_arguments": "not-valid-json{"})

    def test_no_choices_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            GeminiBackend._parse_openai_response({})
        self.assertIn("no choices", str(ctx.exception).lower())

    def test_provider_error_field_raises(self):
        """HTTP 200 + top-level `error` field raises with the provider message."""
        with self.assertRaises(RuntimeError) as ctx:
            GeminiBackend._parse_openai_response({
                "error": {"message": "Resource has been exhausted", "code": 429},
            })
        self.assertIn("Resource has been exhausted", str(ctx.exception))

    def test_text_only_response(self):
        raw = {
            "choices": [{
                "message": {"content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 4},
        }
        out = GeminiBackend._parse_openai_response(raw)
        self.assertEqual(out["content"], "Hello!")
        self.assertEqual(out["finish_reason"], "stop")

    def test_reasoning_content_extracted(self):
        """Gemini thinking models emit reasoning_content when include_thoughts=true."""
        raw = {
            "choices": [{
                "message": {
                    "content": "120",
                    "reasoning_content": "15 * 8 = 120",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 100, "completion_tokens": 50,
                      "completion_tokens_details": {"reasoning_tokens": 40}},
        }
        out = GeminiBackend._parse_openai_response(raw)
        self.assertEqual(out["content"], "120")
        self.assertEqual(out["reasoning_content"], "15 * 8 = 120")


class TestModelFamilyDetection(unittest.TestCase):
    """detect_gemini_family() pattern-matches model names."""

    def test_gemini_3_x_detected(self):
        for name in ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview"):
            info = detect_gemini_family(name)
            self.assertEqual(info["family"], "gemini-3")
            self.assertTrue(info["supports_thinking"])
            self.assertFalse(info["thinking_can_be_disabled"])
            self.assertTrue(info["supports_thought_signatures"])

    def test_gemini_2_5_detected(self):
        for name in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
            info = detect_gemini_family(name)
            self.assertEqual(info["family"], "gemini-2.5")
            self.assertTrue(info["supports_thinking"])
            self.assertTrue(info["thinking_can_be_disabled"])

    def test_gemini_2_0_detected(self):
        info = detect_gemini_family("gemini-2.0-flash")
        self.assertEqual(info["family"], "gemini-2.0")
        self.assertFalse(info["supports_thinking"])

    def test_unknown_model(self):
        info = detect_gemini_family("llama-3.1-70b")
        self.assertEqual(info["family"], "unknown")
        self.assertFalse(info["supports_thinking"])


class TestBackendInit(unittest.TestCase):
    """GeminiBackend construction — base URL trailing-slash, env vars."""

    def setUp(self):
        # Avoid the list_models() network call during __init__.
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    def test_backend_type_is_gemini(self):
        self.assertEqual(self.backend.backend_type, BackendType.GEMINI)

    def test_base_url_stored_without_trailing_slash(self):
        """BaseBackend strips trailing slashes — Gemini's URL construction
        methods (_get_chat_completions_url, list_models URL) add the
        slash back when joining paths."""
        self.assertFalse(self.backend.base_url.endswith("/"))
        # But the chat completions URL must still have the slash in the right place:
        url = self.backend._get_chat_completions_url()
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertNotIn("//chat", url)

    def test_base_url_appends_slash_when_missing(self):
        """When caller passes a URL without trailing slash, the base class
        strips it, but URL construction methods still produce the right URL."""
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                b = GeminiBackend(base_url="https://example.com/v1beta/openai")
        # Slash is stripped, but the constructed chat URL is correct.
        self.assertFalse(b.base_url.endswith("/"))
        self.assertTrue(b._get_chat_completions_url().endswith("/chat/completions"))

    def test_auth_headers_use_bearer(self):
        h = self.backend._get_auth_headers()
        self.assertEqual(h["Authorization"], "Bearer test-key")
        self.assertEqual(h["Content-Type"], "application/json")
        # No HTTP-Referer / X-Title — those are OpenRouter leaderboard headers.
        self.assertNotIn("HTTP-Referer", h)
        self.assertNotIn("X-Title", h)

    def test_chat_completions_url_joins_cleanly(self):
        """No double-slash: base ends with /, endpoint has no leading /."""
        url = self.backend._get_chat_completions_url()
        self.assertFalse("//chat" in url)
        self.assertTrue(url.endswith("/chat/completions"))

    def test_tool_support_returns_native(self):
        """All current Gemini chat models support native function calling."""
        result = self.backend.test_tool_support("gemini-3.8-flash")
        self.assertEqual(result, ToolSupportLevel.NATIVE)

    def test_openre_api_mode_normalized_to_openai(self):
        """Regression: cmd_models in the CLI hardcoded api_mode=ApiMode.OPENRE
        for any backend that wasn't 'openrouter' — Gemini would have crashed
        on `agentkthx models --backend gemini` before the fix.
        We now silently normalize OPENRE → OPENAI (Gemini has only one wire
        format anyway)."""
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                b = GeminiBackend(api_mode=ApiMode.OPENRE)
        # After normalization, the backend's api_mode is OPENAI
        self.assertEqual(b.api_mode, ApiMode.OPENAI)

    def test_string_api_mode_accepted(self):
        """String api_mode (e.g. 'openai' from CLI argparse) is coerced to enum."""
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                b = GeminiBackend(api_mode="openai")
        self.assertEqual(b.api_mode, ApiMode.OPENAI)

    def test_jev_api_mode_accepted(self):
        """JEV mode works — wraps underlying chat-completions call."""
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                b = GeminiBackend(api_mode=ApiMode.JEV)
        self.assertEqual(b.api_mode, ApiMode.JEV)


class TestBuildBody(unittest.TestCase):
    """_build_openai_body() Gemini-specific extras."""

    def setUp(self):
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    def _build(self, **kwargs):
        return self.backend._build_openai_body(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=2048,
            stream=False,
            **kwargs,
        )

    def test_basic_body_has_required_fields(self):
        body = self._build()
        self.assertEqual(body["model"], "gemini-3.8-flash")
        self.assertFalse(body["stream"])
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["max_tokens"], 2048)

    def test_service_tier_forwarded_when_non_standard(self):
        body = self._build(service_tier="flex")
        self.assertEqual(body["service_tier"], "flex")

    def test_service_tier_omitted_when_standard(self):
        """'standard' is the default — no need to send it on the wire."""
        body = self._build(service_tier="standard")
        self.assertNotIn("service_tier", body)

    def test_thinking_config_routed_via_extra_body(self):
        body = self._build(thinking_config={"thinking_level": "low", "include_thoughts": True})
        self.assertEqual(
            body["extra_body"]["google"]["thinking_config"],
            {"thinking_level": "low", "include_thoughts": True},
        )

    def test_reasoning_effort_and_thinking_config_are_mutually_exclusive(self):
        """When both are set, thinking_config wins and reasoning_effort is dropped."""
        body = self._build(
            reasoning_effort="low",
            thinking_config={"thinking_level": "high"},
        )
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(
            body["extra_body"]["google"]["thinking_config"]["thinking_level"],
            "high",
        )

    def test_cached_content_routed_via_extra_body(self):
        body = self._build(cached_content="cachedContents/abc123")
        self.assertEqual(
            body["extra_body"]["google"]["cached_content"],
            "cachedContents/abc123",
        )

    def test_thought_signature_routed_via_thinking_config(self):
        body = self._build(
            thinking_config={"thinking_level": "low"},
            thought_signature="EpoGCpcGAXLI2nx/...",
        )
        self.assertEqual(
            body["extra_body"]["google"]["thinking_config"]["thought_signature"],
            "EpoGCpcGAXLI2nx/...",
        )


class TestCalculateSafeMaxTokens(unittest.TestCase):
    """ROB-06 parity: context-length 400 → reduce max_tokens + retry."""

    def setUp(self):
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    def test_parses_gemini_error_format(self):
        error = (
            "Request exceeds the maximum context length of 1048576 tokens. "
            "You requested 1100000 tokens (1000000 in the input, 100000 in the output)."
        )
        body = {"max_tokens": 100000}
        safe = self.backend._calculate_safe_max_tokens(error, body)
        # 1048576 - 1000000 - 2048 = 46528
        self.assertIsNotNone(safe)
        self.assertEqual(safe, 46528)
        self.assertLess(safe, body["max_tokens"])

    def test_floors_at_1024(self):
        """When context is so small that even 1K output would barely fit,
        the floor at 1024 kicks in — BUT only if the floor is smaller than
        the original max_tokens. If old_max < 1024, returning None signals
        that reducing max_tokens further won't help."""
        # Scenario: tiny context, modest max_tokens request.
        error = (
            "maximum context length of 1100 tokens. "
            "You requested 1100 tokens (50 in the input, 1050 in the output)."
        )
        body = {"max_tokens": 1050}
        # safe = 1100 - 50 - 2048 = -998 → floor to 1024
        # 1024 < old_max (1050) → return 1024
        safe = self.backend._calculate_safe_max_tokens(error, body)
        self.assertEqual(safe, 1024)

    def test_returns_none_when_already_safe(self):
        error = (
            "maximum context length of 1048576 tokens. "
            "You requested 1000 tokens (500 in the input, 500 in the output)."
        )
        body = {"max_tokens": 100}
        # safe = 1048576 - 500 - 2048 = 1046028, which is > old_max (100).
        # Already safe → return None.
        safe = self.backend._calculate_safe_max_tokens(error, body)
        self.assertIsNone(safe)

    def test_falls_back_to_third_reduction_on_unparsable(self):
        body = {"max_tokens": 12000}
        safe = self.backend._calculate_safe_max_tokens("unparsable garbage", body)
        # 12000 // 3 = 4000, below 4096 floor → 4096
        self.assertEqual(safe, 4096)


class TestModelDefaults(unittest.TestCase):
    """_get_model_defaults() uses catalog when cache is empty."""

    def setUp(self):
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()
                self.backend._model_cache = None  # force catalog path

    def test_known_model_uses_catalog(self):
        d = self.backend._get_model_defaults("gemini-3.8-flash")
        # Catalog: 1M context, 65_536 max_tokens → capped at 1M/32 = 32768
        self.assertEqual(d["context_length"], 1_048_576)
        self.assertLessEqual(d["max_tokens"], 65_536)
        self.assertGreater(d["max_tokens"], 0)

    def test_pro_model_uses_2m_context(self):
        d = self.backend._get_model_defaults("gemini-3.1-pro-preview")
        self.assertEqual(d["context_length"], 2_097_152)

    def test_context_safe_max_tokens_overrides(self):
        """Once persisted, _context_safe_max_tokens wins over catalog."""
        self.backend._context_safe_max_tokens = 4096
        d = self.backend._get_model_defaults("gemini-3.8-flash")
        self.assertEqual(d["max_tokens"], 4096)

    def test_unknown_model_gets_sensible_defaults(self):
        d = self.backend._get_model_defaults("gemini-9.9-flash-future")
        self.assertEqual(d["context_length"], 1_048_576)
        self.assertGreater(d["max_tokens"], 0)


class TestRetryHelpers(unittest.TestCase):
    """429 retry budget + exponential backoff math."""

    def setUp(self):
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    def test_default_max_429_retries(self):
        self.assertEqual(self.backend._max_429_retries(), 6)

    def test_env_override_for_max_429_retries(self):
        with patch.dict(os.environ, {"GEMINI_MAX_429_RETRIES": "10"}):
            self.assertEqual(self.backend._max_429_retries(), 10)

    def test_backoff_schedule(self):
        """5 → 10 → 20 → 40 → 80 → 90 cap."""
        # First attempt: ~5 (with ±20% jitter → 4..6)
        d1 = self.backend._429_backoff(1)
        self.assertGreaterEqual(d1, 4.0)
        self.assertLessEqual(d1, 6.0)
        # Sixth attempt: capped at 90
        d6 = self.backend._429_backoff(6)
        self.assertLessEqual(d6, 90.0 + 18.0)  # cap + jitter


class TestToolsNotSupportedError(unittest.TestCase):
    """ReAct-fallback detector — rare on Gemini but defensive."""

    def test_detects_canonical_message(self):
        self.assertTrue(GeminiBackend._is_tools_not_supported_error(
            "This model does not support tools."
        ))

    def test_detects_function_calling_message(self):
        self.assertTrue(GeminiBackend._is_tools_not_supported_error(
            "Function calling is not supported on gemini-1.0-flash"
        ))

    def test_ignores_unrelated_errors(self):
        self.assertFalse(GeminiBackend._is_tools_not_supported_error(
            "Rate limit exceeded"
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Live API tests — skipped unless GEMINI_API_KEY is set
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _RUN_LIVE, reason="live-API tests need GEMINI_API_KEY=<real_key> + GEMINI_RUN_LIVE_TESTS=1")
class TestLiveGeminiAPI:
    """Live integration tests against the real Gemini endpoint.

    Run with: GEMINI_API_KEY=... pytest tests/test_gemini_backend.py::TestLiveGeminiAPI
    """

    def setup_method(self, _):
        self.backend = GeminiBackend()

    def test_list_models_returns_known_entries(self):
        models = self.backend.list_models()
        names = [m["name"] for m in models]
        assert "gemini-3.8-flash" in names or any("gemini-3" in n for n in names)

    def test_basic_chat(self):
        result = self.backend.generate(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "Say exactly: hello"}],
            max_tokens=20,
        )
        assert result["content"]
        assert result["finish_reason"] == "stop"

    def test_function_calling(self):
        from agentkthx.core.models import Tool, ToolParam
        tool = Tool(
            name="echo",
            description="Echo back the input string",
            params=[ToolParam(name="text", type="string", description="text to echo")],
        )
        result = self.backend.generate(
            model="gemini-3.8-flash",
            messages=[{"role": "user", "content": "Use the echo tool to echo 'hi'"}],
            tools=[tool],
            max_tokens=200,
        )
        # Either model called the tool, or returned text — both are acceptable.
        assert result["content"] or result["tool_calls"]


if __name__ == "__main__":
    unittest.main()
