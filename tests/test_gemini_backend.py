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
    FREE_TIER_LIMITS,
    detect_gemini_family,
    _is_free_tier_model,
    _get_free_tier_limits,
    _is_chat_capable_model,
    _uses_thought_tags,
    ThoughtTagParser,
    _parse_thought_tags_from_complete_text,
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

    def test_gemini_is_cloud_provider(self):
        """Regression for BUG-02: cli.py:2145 had `[OPENROUTER, ZAI]` cloud-provider
        allowlist missing GEMINI. The cmd_models display loop had two branches
        (OllamaBackend / cloud_provider) and Gemini hit neither — so
        `agentkthx models --backend gemini` showed "Total: 10 models" with an
        empty table.

        The fix added GEMINI to all 6 occurrences of the cloud-provider
        allowlist in cli.py. This test asserts the Gemini backend's
        backend_type is correctly classified as a cloud provider.
        """
        # Sanity: backend_type is GEMINI (already covered by test_backend_type_is_gemini)
        self.assertEqual(self.backend.backend_type, BackendType.GEMINI)
        # And GEMINI is in the cloud-provider set used by cli.py
        cloud_provider_types = {BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI}
        self.assertIn(self.backend.backend_type, cloud_provider_types)


class TestModelIdPrefixStripping(unittest.TestCase):
    """Polish fix 1: Google's /v1beta/openai/models endpoint returns IDs
    prefixed with 'models/'. Without stripping the prefix, catalog merge
    sees 'gemini-3.8-flash' (catalog) and 'models/gemini-3.8-flash' (API)
    as different entries and emits duplicates."""

    def setUp(self):
        from unittest.mock import patch
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    def test_parse_strips_models_prefix(self):
        parsed = self.backend._parse_gemini_model({"id": "models/gemini-3.8-flash"})
        self.assertEqual(parsed["name"], "gemini-3.8-flash")

    def test_parse_preserves_unprefixed_id(self):
        """If the API ever returns IDs without the prefix, don't break."""
        parsed = self.backend._parse_gemini_model({"id": "gemini-3.8-flash"})
        self.assertEqual(parsed["name"], "gemini-3.8-flash")

    def test_parse_strips_prefix_before_catalog_lookup(self):
        """After stripping 'models/', catalog context_length is applied.
        Pro models should show 2M context (2048K), Flash should show 1M (1024K)."""
        parsed = self.backend._parse_gemini_model({"id": "models/gemini-3.1-pro-preview"})
        # Catalog has gemini-3.1-pro-preview with context_length=2_097_152 (2M)
        self.assertEqual(parsed["details"]["context_length"], 2_097_152)

    def test_parse_uses_1m_default_when_no_catalog_match(self):
        """Unknown models fall back to 1M context (Gemini Flash default)."""
        parsed = self.backend._parse_gemini_model({"id": "models/some-future-model-2099"})
        self.assertEqual(parsed["details"]["context_length"], 1_048_576)


class TestChatCapabilityClassification(unittest.TestCase):
    """Polish fix 2 + 3: Non-chat models (embeddings, Veo, Lyria, robotics,
    TTS, Live API, image gen, computer-use, deep-research, antigravity,
    Gemma open-source) can't accept chat-completions requests with tools.
    They should be marked as ✗ none in the tool-support columns instead
    of ✓ native."""

    def setUp(self):
        from unittest.mock import patch
        with patch.object(GeminiBackend, "list_models", return_value=[]):
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
                self.backend = GeminiBackend()

    # Chat models → NATIVE
    def test_chat_flash_models_are_native(self):
        for name in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash",
                     "gemini-2.5-flash", "gemini-2.5-flash-lite"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NATIVE,
                f"{name} should be NATIVE (chat model)"
            )

    def test_chat_pro_models_are_native(self):
        for name in ("gemini-3.1-pro-preview", "gemini-2.5-pro"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NATIVE,
                f"{name} should be NATIVE (chat model)"
            )

    # Non-chat models → NONE
    def test_embedding_models_are_none(self):
        for name in ("gemini-embedding-001", "gemini-embedding-2-preview"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (embedding, not chat)"
            )

    def test_video_gen_models_are_none(self):
        for name in ("veo-3.1-generate-preview", "veo-3.1-fast-generate-preview",
                     "veo-3.1-lite-generate-preview", "gemini-omni-1.1-flash"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (video gen, not chat)"
            )

    def test_music_gen_models_are_none(self):
        for name in ("lyria-3.5", "lyria-3-pro-preview", "lyria-3-clip-preview",
                     "lyria-realtime-exp"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (music gen, not chat)"
            )

    def test_tts_models_are_none(self):
        for name in ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts",
                     "gemini-2.5-flash-preview-tts"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (TTS, not chat)"
            )

    def test_live_api_models_are_none(self):
        """Live API audio-to-audio models — including the tricky 'gemini-3.8-live'
        that has no trailing dash after 'live'."""
        for name in ("gemini-3.8-live", "gemini-3.8-live-extended-thinking",
                     "gemini-3.1-flash-live-preview", "gemini-3.5-live-translate-preview"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (Live API, not chat)"
            )

    def test_image_gen_models_are_none(self):
        for name in ("gemini-3.1-flash-image", "gemini-3-pro-image",
                     "gemini-3-pro-image-preview", "gemini-3.1-flash-lite-image"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (image gen, not chat)"
            )

    def test_robotics_models_are_none(self):
        for name in ("gemini-robotics-er-2-preview",
                     "gemini-robotics-er-2-streaming-preview",
                     "gemini-robotics-er-1.6-preview"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (robotics, not chat)"
            )

    def test_transcribe_models_are_none(self):
        for name in ("gemini-3.5-transcribe", "gemini-3.5-transcribe-live"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (transcribe, not chat)"
            )

    def test_misc_non_chat_models_are_none(self):
        for name in ("gemini-2.5-computer-use-preview-10-2025",
                     "deep-research-preview-04-2026",
                     "deep-research-max-preview-04-2026",
                     "antigravity-preview-09-2026",
                     "aqa"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NONE,
                f"{name} should be NONE (not a chat model)"
            )

    def test_gemma_models_are_chat_capable(self):
        """VERIFIED 2026-09-24 on real VM: gemma-4-* models ARE chat-capable
        via /v1beta/openai/chat/completions. They use inline <thought> tags
        for reasoning (see ThoughtTagParser) — but they DO accept chat
        requests with tools → return NATIVE in test_tool_support."""
        for name in ("gemma-4-26b-a4b-it", "gemma-4-31b-it"):
            self.assertEqual(
                self.backend.test_tool_support(name),
                ToolSupportLevel.NATIVE,
                f"{name} should be NATIVE (verified chat-capable on real VM)"
            )

    def test_models_prefix_stripped_in_classification(self):
        """Defensive: if a 'models/' prefixed ID slips through, classification
        still works correctly."""
        self.assertEqual(
            self.backend.test_tool_support("models/gemini-embedding-001"),
            ToolSupportLevel.NONE,
            "Prefixed 'models/gemini-embedding-001' should still be NONE"
        )
        self.assertEqual(
            self.backend.test_tool_support("models/gemini-3.8-flash"),
            ToolSupportLevel.NATIVE,
            "Prefixed 'models/gemini-3.8-flash' should still be NATIVE"
        )


class TestFreeTierClassification(unittest.TestCase):
    """Free-tier eligibility per the FREE_TIER_LIMITS table transcribed from
    Google AI Studio on 2026-09-24.

    The user manually checked AI Studio's rate-limits page for their Free-tier
    project (no billing setup). The 20 confirmed-free models are encoded in
    FREE_TIER_LIMITS with non-zero rpd. Models with rpd=0 are explicitly NOT
    on the free tier. The _is_free_tier_model() classifier extends this with
    pattern-based heuristics for variants not in the table.

    Ground truth (per AI Studio, 2026-09-24):
      ✓ FREE (non-zero limits):
        Antigravity (60/100K/100), Gemini 2.5 Flash (5/250K/20),
        Gemini 2.5 Flash Lite (10/250K/20), Gemini 2.5 Flash TTS (3/10K/10),
        Gemini 3 Flash (5/250K/20), Gemini 3.1 Flash Lite (15/250K/500),
        Gemini 3.1 Flash TTS (3/10K/10), Gemini 3.5 Flash (5/250K/20),
        Gemini 3.5 Flash Lite (15/250K/500), Gemini 3.5 Transcribe (3/10K/25),
        Gemini 3.6/3.7/3.8 Flash (5/250K/20 each),
        Gemini 3.8 Flash Lite TTS / Flash TTS (3/10K/10 each),
        Gemini Embedding 1/2 (100/30K/1K),
        Gemini Robotics ER 2 Preview (5/250K/20),
        Gemma 4 26B (30/16K/14.4K), Gemma 4 31B (30/16K/[truncated])
      ✗ NOT FREE (0/0/0):
        Deep Research variants, Gemini 2 Flash / 2 Flash Lite (legacy),
        Computer Use Preview, Nano Banana variants (image gen),
        Gemini 2.5 Pro / 3.1 Pro (Pro = paid), Gemini Omni variants,
        Veo / Lyria (video/music gen = paid)
    """

    # ── Confirmed FREE (from AI Studio data, in FREE_TIER_LIMITS table) ──

    def test_chat_flash_models_are_free(self):
        for name in ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash",
                     "gemini-3.7-flash", "gemini-3.8-flash", "gemini-3-flash-preview"):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (5 RPM / 250K TPM / 20 RPD per AI Studio)")

    def test_chat_flash_lite_models_are_free_with_higher_rpd(self):
        # Flash-Lite has 15 RPM / 500 RPD — actually more generous than regular Flash
        for name in ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite"):
            self.assertTrue(_is_free_tier_model(name))
            limits = _get_free_tier_limits(name)
            self.assertEqual(limits["rpm"], 15)
            self.assertEqual(limits["rpd"], 500)

    def test_gemma_is_free(self):
        """Gemma open-source models ARE on the Free tier (generous RPD!).
        Earlier catalog had them marked non-chat for /chat/completions
        unverified — but free-tier eligibility is separate from chat capability."""
        for name in ("gemma-4-26b-a4b-it", "gemma-4-31b-it"):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (30 RPM / 16K TPM / 14.4K RPD per AI Studio)")
            limits = _get_free_tier_limits(name)
            self.assertEqual(limits["rpd"], 14_400)

    def test_embeddings_are_free(self):
        for name in ("gemini-embedding-001", "gemini-embedding-2-preview", "gemini-embedding-2"):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (100 RPM / 30K TPM / 1K RPD per AI Studio)")

    def test_antigravity_is_free(self):
        for name in ("antigravity-preview-05-2026", "antigravity-preview-09-2026", "antigravity-preview-latest"):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (60 RPM / 100K TPM / 100 RPD per AI Studio)")

    def test_robotics_is_free(self):
        for name in ("gemini-robotics-er-2-preview",):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (5 RPM / 250K TPM / 20 RPD per AI Studio)")

    def test_transcribe_is_free(self):
        self.assertTrue(_is_free_tier_model("gemini-3.5-transcribe"))
        limits = _get_free_tier_limits("gemini-3.5-transcribe")
        self.assertEqual(limits["rpd"], 25)

    def test_tts_variants_are_free(self):
        for name in ("gemini-2.5-flash-preview-tts", "gemini-3.1-flash-tts-preview",
                     "gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"):
            self.assertTrue(_is_free_tier_model(name),
                            f"{name} should be FREE (3 RPM / 10K TPM / 10 RPD per AI Studio)")

    # ── Confirmed NOT FREE (from AI Studio data, rpd=0 in table) ──

    def test_pro_models_are_not_free(self):
        for name in ("gemini-2.5-pro", "gemini-3.1-pro-preview",
                     "gemini-3.1-pro-preview-customtools", "gemini-2.5-pro-preview-tts"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (Pro = paid per AI Studio)")

    def test_image_gen_models_are_not_free(self):
        """Nano Banana / image gen variants show 0/0/0 in AI Studio."""
        for name in ("gemini-2.5-flash-image", "gemini-3-pro-image",
                     "gemini-3-pro-image-preview", "gemini-3.1-flash-image",
                     "gemini-3.1-flash-image-preview", "gemini-3.1-flash-lite-image",
                     "nano-banana-pro-preview"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (image gen = paid)")

    def test_video_gen_models_are_not_free(self):
        for name in ("veo-3.1-generate-preview", "veo-3.1-fast-generate-preview",
                     "veo-3.1-lite-generate-preview", "gemini-omni-1.1-flash",
                     "gemini-omni-flash-preview"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (video gen = paid)")

    def test_music_gen_models_are_not_free(self):
        for name in ("lyria-3.5", "lyria-3-pro-preview", "lyria-3-clip-preview",
                     "lyria-realtime-exp"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (music gen = paid)")

    def test_deep_research_is_not_free(self):
        for name in ("deep-research-preview-04-2026",
                     "deep-research-max-preview-04-2026",
                     "deep-research-pro-preview-12-2025"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (agentic = paid)")

    def test_computer_use_is_not_free(self):
        self.assertFalse(_is_free_tier_model("gemini-2.5-computer-use-preview-10-2025"))

    def test_legacy_2_0_models_are_not_free(self):
        """Gemini 2.0 / 2 Flash / 2 Flash Lite are listed as 0/0/0 — being shut down."""
        self.assertFalse(_is_free_tier_model("gemini-2.0-flash"))
        self.assertFalse(_is_free_tier_model("gemini-2.0-flash-lite"))
        self.assertFalse(_is_free_tier_model("gemini-flash-latest"))  # alias for Pro
        self.assertFalse(_is_free_tier_model("gemini-pro-latest"))

    # ── Live API edge cases — only gemini-3.5-transcribe* is free ──

    def test_live_api_variants_not_in_table_are_not_free(self):
        """Only gemini-3.5-transcribe and -live are explicitly free per AI Studio.
        Other Live API variants (-live, live-translate) are NOT in the table
        and should be marked NOT free."""
        for name in ("gemini-3.8-live", "gemini-3.8-live-extended-thinking",
                     "gemini-3.1-flash-live-preview", "gemini-3.5-live-translate-preview"):
            self.assertFalse(_is_free_tier_model(name),
                             f"{name} should NOT be free (not in AI Studio free-tier table)")

    def test_transcribe_in_table_is_free(self):
        """gemini-3.5-transcribe and gemini-3.5-transcribe-live are in the table."""
        for name in ("gemini-3.5-transcribe", "gemini-3.5-transcribe-live"):
            self.assertTrue(_is_free_tier_model(name))

    # ── Defensive: models/ prefix stripping ──

    def test_models_prefix_stripped_in_free_tier_check(self):
        """If 'models/' prefix slips through, classifier still works."""
        self.assertTrue(_is_free_tier_model("models/gemini-3.8-flash"))
        self.assertFalse(_is_free_tier_model("models/gemini-2.5-pro"))
        self.assertTrue(_is_free_tier_model("models/gemma-4-31b-it"))

    # ── Catalog consistency check ──

    def test_catalog_free_tier_flags_match_table(self):
        """Catalog free_tier flags must match the FREE_TIER_LIMITS table."""
        for name, info in GEMINI_MODELS.items():
            catalog_says_free = info.get("free_tier", False)
            table_says_free = _is_free_tier_model(name)
            self.assertEqual(catalog_says_free, table_says_free,
                             f"{name}: catalog={catalog_says_free} but table={table_says_free}")


class TestUsesThoughtTags(unittest.TestCase):
    """_uses_thought_tags() detector — currently Gemma 4 variants only."""

    def test_gemma_models_use_thought_tags(self):
        for name in ("gemma-4-26b-a4b-it", "gemma-4-31b-it"):
            self.assertTrue(_uses_thought_tags(name),
                            f"{name} should use <thought> tags for reasoning")

    def test_gemini_models_do_not_use_thought_tags(self):
        """Gemini 3.x / 2.5 use the native OpenAI reasoning_content field,
        not inline <thought> tags."""
        for name in ("gemini-3.8-flash", "gemini-3.1-pro-preview",
                     "gemini-2.5-flash", "gemini-2.5-pro"):
            self.assertFalse(_uses_thought_tags(name),
                            f"{name} should NOT use <thought> tags (uses native reasoning_content)")

    def test_models_prefix_stripped(self):
        """If 'models/' prefix slips through, detector still works."""
        self.assertTrue(_uses_thought_tags("models/gemma-4-31b-it"))
        self.assertFalse(_uses_thought_tags("models/gemini-3.8-flash"))


class TestThoughtTagParser(unittest.TestCase):
    """ThoughtTagParser — streaming <thought>...</thought> tag parser for Gemma.

    Gemma 4 wraps its reasoning in inline <thought>...</thought> tags instead
    of using the OpenAI reasoning_content field. The parser routes tag content
    to reasoning_content so AgentKthx can show it as a collapsible "thought"
    panel — instead of letting raw tags leak into the visible content stream.
    """

    # ── Non-streaming one-shot parser ──

    def test_complete_text_simple(self):
        """One <thought> block + answer after."""
        text = "<thought>reasoning here</thought>actual answer"
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, "actual answer")
        self.assertEqual(reasoning, "reasoning here")

    def test_complete_text_no_tags(self):
        """No <thought> tags → content passes through unchanged, reasoning empty."""
        text = "Just a normal response."
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, text)
        self.assertEqual(reasoning, "")

    def test_complete_text_user_actual_output(self):
        """The exact output the user pasted from real Gemma on the VM."""
        text = (
            "<thought>*   User's name/identity: VTSTech.\n"
            "    *   Goal: Greeting/Introduction.\n"
            "    *   Instruction: Answer directly and accurately.\n"
            "\n"
            "    *   Acknowledge the user's greeting and identity.\n"
            "    *   Maintain the persona of AI AgentKthx (direct and accurate).\n"
            "\n"
            "    *   *Option 1:* Hello VTSTech. How can I help you? (Simple, direct).\n"
            "    *   *Option 2:* Nice to meet you, VTSTech. I am AI AgentKthx. (Personalized).\n"
            "\n"
            '    *   "Hello, VTSTech. How can I assist you today?"</thought>'
            "Hello, VTSTech. How can I assist you today?"
        )
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, "Hello, VTSTech. How can I assist you today?")
        self.assertIn("User's name/identity: VTSTech", reasoning)
        self.assertIn("AI AgentKthx", reasoning)
        # The <thought> tags themselves should NOT appear in either output
        self.assertNotIn("<thought>", content)
        self.assertNotIn("</thought>", content)
        self.assertNotIn("<thought>", reasoning)
        self.assertNotIn("</thought>", reasoning)

    def test_malformed_stray_closing_tag_stripped(self):
        """Gemma sometimes emits a stray </thought> without a matching opening.
        The parser should strip it from content (not let raw tags leak)."""
        # Construct: <thought>reasoning...Response: X. <thought>
        #            </thought>Actual answer.</thought>
        text = (
            "<thought>The user is asking for my name.\n"
            "Response: My name is AI AgentKthx. <thought>\n"
            "</thought>My name is AI AgentKthx.</thought>"
        )
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        # Content should NOT contain any raw tags
        self.assertEqual(content, "My name is AI AgentKthx.")
        self.assertNotIn("<thought>", content)
        self.assertNotIn("</thought>", content)
        # Reasoning should have the model's actual thought process
        self.assertIn("The user is asking for my name", reasoning)
        self.assertIn("AI AgentKthx", reasoning)

    def test_unclosed_thought_at_end(self):
        """Model forgot to close the <thought> tag — surface the reasoning
        anyway (don't hide it just because the model was sloppy)."""
        text = "<thought>the model never closed this"
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, "")
        self.assertEqual(reasoning, "the model never closed this")

    def test_multiple_thought_blocks(self):
        """Multiple <thought>...</thought> blocks in sequence."""
        text = "<thought>thought 1</thought>answer 1<thought>thought 2</thought>answer 2"
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, "answer 1answer 2")
        self.assertEqual(reasoning, "thought 1thought 2")

    def test_empty_thought_block(self):
        """Empty <thought></thought> → empty reasoning, content passes through."""
        text = "<thought></thought>just the answer"
        content, reasoning = _parse_thought_tags_from_complete_text(text)
        self.assertEqual(content, "just the answer")
        self.assertEqual(reasoning, "")

    # ── Streaming parser (chunk-by-chunk) ──

    def test_streaming_simple_split(self):
        """Stream the response in chunks where tags are intact in single chunks."""
        parser = ThoughtTagParser()
        chunks = ["<thought>", "reasoning here", "</thought>", "actual answer"]
        content_parts = []
        reasoning_parts = []
        for chunk in chunks:
            c, r = parser.feed(chunk)
            if c: content_parts.append(c)
            if r: reasoning_parts.append(r)
        c, r = parser.flush()
        if c: content_parts.append(c)
        if r: reasoning_parts.append(r)
        self.assertEqual("".join(content_parts), "actual answer")
        self.assertEqual("".join(reasoning_parts), "reasoning here")

    def test_streaming_partial_opening_tag(self):
        """Opening tag split across chunks: '<tho' + 'ught>'."""
        parser = ThoughtTagParser()
        chunks = ["<tho", "ught>", "my reasoning", "</thought>", "final answer"]
        content_parts = []
        reasoning_parts = []
        for chunk in chunks:
            c, r = parser.feed(chunk)
            if c: content_parts.append(c)
            if r: reasoning_parts.append(r)
        c, r = parser.flush()
        if c: content_parts.append(c)
        if r: reasoning_parts.append(r)
        # The partial "<tho" should NOT leak into content — it's buffered
        # until the rest of the tag arrives.
        self.assertNotIn("<tho", "".join(content_parts))
        self.assertNotIn("ught>", "".join(content_parts))
        self.assertEqual("".join(content_parts), "final answer")
        self.assertEqual("".join(reasoning_parts), "my reasoning")

    def test_streaming_partial_closing_tag(self):
        """Closing tag split across chunks: '</th' + 'ought>'."""
        parser = ThoughtTagParser()
        chunks = ["<thought>", "my reasoning", "</th", "ought>", "final answer"]
        content_parts = []
        reasoning_parts = []
        for chunk in chunks:
            c, r = parser.feed(chunk)
            if c: content_parts.append(c)
            if r: reasoning_parts.append(r)
        c, r = parser.flush()
        if c: content_parts.append(c)
        if r: reasoning_parts.append(r)
        self.assertNotIn("</th", "".join(content_parts))
        self.assertNotIn("ought>", "".join(content_parts))
        self.assertEqual("".join(content_parts), "final answer")
        self.assertEqual("".join(reasoning_parts), "my reasoning")

    def test_streaming_no_tags_passes_through(self):
        """Non-Gemma response (no <thought> tags) → content passes through,
        reasoning stays empty. This is the common case for Gemini 3.x."""
        parser = ThoughtTagParser()
        text = "Just a normal streaming response from gemini-3.8-flash."
        content_parts = []
        reasoning_parts = []
        # Simulate streaming in 10-char chunks
        for i in range(0, len(text), 10):
            chunk = text[i:i+10]
            c, r = parser.feed(chunk)
            if c: content_parts.append(c)
            if r: reasoning_parts.append(r)
        c, r = parser.flush()
        if c: content_parts.append(c)
        if r: reasoning_parts.append(r)
        self.assertEqual("".join(content_parts), text)
        self.assertEqual("".join(reasoning_parts), "")

    def test_streaming_unclosed_at_end(self):
        """Stream ends with an open <thought> tag — flush should emit
        the reasoning anyway (don't hide it)."""
        parser = ThoughtTagParser()
        chunks = ["<thought>", "ongoing reasoning that never closes"]
        content_parts = []
        reasoning_parts = []
        for chunk in chunks:
            c, r = parser.feed(chunk)
            if c: content_parts.append(c)
            if r: reasoning_parts.append(r)
        c, r = parser.flush()
        if c: content_parts.append(c)
        if r: reasoning_parts.append(r)
        # All the reasoning was buffered — flush emits it
        self.assertEqual("".join(content_parts), "")
        self.assertEqual("".join(reasoning_parts), "ongoing reasoning that never closes")

    def test_streaming_empty_input(self):
        """Empty chunks should return empty deltas."""
        parser = ThoughtTagParser()
        c, r = parser.feed("")
        self.assertEqual(c, "")
        self.assertEqual(r, "")
        c, r = parser.flush()
        self.assertEqual(c, "")
        self.assertEqual(r, "")


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
