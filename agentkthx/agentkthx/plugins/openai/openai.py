"""
⚛️ AgentKthx — OpenAI API Backend

Backend implementation for the OpenAI Chat-Completions API.

OpenAI's API surface at ``https://api.openai.com/v1`` is the canonical
OpenAI Chat-Completions wire format — every other OpenAI-compatible
backend in the framework (ZAI, OpenRouter, Hugging Face, Gemini's
OpenAI-compat path) speaks this same wire format, which is why they
all inherit from ``OpenAICompatibleBackend``. This backend is the
"first-party OpenAI direct" surface, adding OpenAI-specific auth
(project-key scoping), service-tier selection, reasoning_effort
parameter for thinking models, and the curated free-tier whitelist.

v0.1 targets the Chat Completions API only. The newer stateful
Responses API (``/v1/responses``) is deferred to v0.2 — it has its
own request/response shape, built-in tools (web_search, file_search,
code_interpreter, computer_use), and event-driven streaming protocol.

Key features:
  - OPENAI_API_KEY Bearer auth with token-type detection (sk- legacy,
    sk-proj- project, sk-admin- admin, sk-sa- service account) +
    optional OpenAI-Organization / OpenAI-Project headers for
    multi-org / project-scoped billing
  - OPENAI_FREE_ONLY whitelist enforcement (6 very-low-cost models:
    gpt-6-luna, gpt-4o-mini, gpt-4.1-mini, gpt-realtime-2.1-mini,
    gpt-4o-mini-transcribe, gpt-transcribe). When true, also forces
    service_tier=default and caps reasoning_effort at "low" (reasoning
    tokens are billed at output rate and can quickly exhaust the
    $1 trial credit / $10/mo API credit allowance)
  - OPENAI_FREE_FALLBACK_MODEL swap on HTTP 429 insufficient_quota
    (mirrors ZAI's 429 insufficient-balance fallback pattern)
  - OPENAI_SERVICE_TIER env var (auto/default/flex/scale/priority/fast)
    controls pricing/latency tradeoff
  - OPENAI_REASONING_EFFORT env var (none/minimal/low/medium/high/
    xhigh/max) for reasoning models (gpt-5.x+, gpt-6.x, o-series)
  - 429 retry with Retry-After honor + exponential back-off
  - ReAct fallback when a model rejects the `tools` field (rare on
    OpenAI but kept for parity with other backends)
  - Reasoning-content capture for thinking-capable models
  - Context-length 400 recovery via the shared ARCH-03 helper
  - JEV dispatch via _jev_call_completions() routing through
    self.generate() so auth + 429 retry + OPENAI_FREE_ONLY are
    preserved for decision-mode calls

Endpoints used:
  - POST /chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /models          → model discovery (OpenAI-compatible)

Configuration:
  OPENAI_API_KEY             — API key for auth (required, sk-proj- recommended)
  OPENAI_BASE_URL            — API base URL (default: https://api.openai.com/v1)
  OPENAI_ORGANIZATION_ID     — Optional, multi-org scoping (org-xxx)
  OPENAI_PROJECT_ID          — Optional, project-scoped billing (proj_xxx)
  OPENAI_DEFAULT_MODEL       — Default model (default: gpt-6-sol)
  OPENAI_FREE_ONLY           — Strict free-tier enforcement (default: false)
  OPENAI_FREE_FALLBACK_MODEL — Model to swap to on HTTP 429 insufficient_quota
                               when OPENAI_FREE_ONLY=false (default: gpt-4o-mini)
  OPENAI_SERVICE_TIER        — Service tier (auto/default/flex/scale/priority/fast)
  OPENAI_REASONING_EFFORT    — Reasoning effort for thinking models
                               (none/minimal/low/medium/high/xhigh/max)

Usage:
  # CLI
  agentkthx chat --backend openai --model gpt-6-sol
  agentkthx run "What is 15 * 8?" --backend oai --model gpt-6-luna
  agentkthx chat --backend oai --model gpt-6-astra --think

  # Python API
  from agentkthx import Agent
  agent = Agent(
      model="gpt-6-astra",
      backend="openai",
      tools=["calculator"],
  )
  result = agent.run("What is 15 * 8?")

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Generator, Iterable

from agentkthx.backends.base import BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.config import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_DEFAULT_MODEL,
    OPENAI_FREE_FALLBACK_MODEL,
    OPENAI_FREE_ONLY,
    OPENAI_ORGANIZATION_ID,
    OPENAI_PROJECT_ID,
    OPENAI_REASONING_EFFORT,
    OPENAI_SERVICE_TIER,
)
from agentkthx.core.models import Tool
from agentkthx.core.types import ApiMode, BackendType, ToolSupportLevel


# ─────────────────────────────────────────────────────────────────────────────
# Static model catalog — fallback when /v1/models is unreachable
# ─────────────────────────────────────────────────────────────────────────────
# Sept 2026 OpenAI lineup verified via https://platform.openai.com/docs/pricing
# (captured Sept 26 2026 — see docs/OPENAI_API_TECHNICAL_REFERENCE.md §Model
# Family Specifications for the full pricing snapshot).
#
# Pricing is USD per 1M tokens, "standard" service tier (long-context
# pricing differs; see OpenAI pricing page).
OPENAI_MODELS: dict[str, dict] = {
    # === GPT-6 family — current flagship generation ===
    "gpt-6-astra": {
        "context_length": 400_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,        # standard + pro
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,     # text, image, audio, file
        "supports_multimodal_output": True,    # text, audio
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": True,
        "family": "gpt-6",
        "tier": "flagship",
        "standard_input_per_1m": 10.00,
        "standard_output_per_1m": 50.00,
        "cached_input_per_1m": 1.00,
    },
    "gpt-6-sol": {
        "context_length": 400_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": True,
        "family": "gpt-6",
        "tier": "standard",
        "standard_input_per_1m": 2.00,
        "standard_output_per_1m": 10.00,
    },
    "gpt-6-luna": {
        "context_length": 400_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": False,    # not supported on Luna
        "family": "gpt-6",
        "tier": "lite",
        "standard_input_per_1m": 0.10,
        "standard_output_per_1m": 0.50,
        "free_tier_eligible": True,             # covered by monthly credit
    },

    # === GPT-5.6 family — Daybreak program ===
    "gpt-5.6-sol": {  # alias: gpt-daybreak-blue-latest
        "context_length": 200_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-5.6",
        "tier": "daybreak",
        "standard_input_per_1m": 4.00,
        "standard_output_per_1m": 20.00,
    },

    # === GPT-5.x family — legacy but still served ===
    "gpt-5.5": {
        "context_length": 200_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.4": {
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": True,
        "supports_interleaved_thinking": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.3-codex": {
        "context_length": 200_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_code_interpreter_tool": True,
        "family": "gpt-5",
        "tier": "codex",
        "standard_input_per_1m": 1.75,
        "standard_output_per_1m": 14.00,
    },

    # === GPT-4o family — legacy chat models (no reasoning) ===
    "gpt-4o": {
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": False,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,        # text, image, audio
        "supports_multimodal_output": True,       # text, audio
        "supports_web_search_tool": True,
        "family": "gpt-4o",
        "tier": "standard",
    },
    "gpt-4o-mini": {
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": False,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_web_search_tool": True,
        "family": "gpt-4o",
        "tier": "mini",
        "free_tier_eligible": True,
    },
    "gpt-4.1-mini": {
        "context_length": 1_000_000,             # 1M tokens
        "max_completion_tokens": 32_768,
        "supports_thinking": False,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_web_search_tool": True,
        "family": "gpt-4.1",
        "tier": "mini",
        "free_tier_eligible": True,
    },

    # === Specialized models ===
    "chat-latest": {  # ChatGPT backend model
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": False,
        "supports_function_calling": True,
        "family": "chatgpt",
        "tier": "chat-latest",
        "standard_input_per_1m": 5.00,
        "standard_output_per_1m": 30.00,
    },

    # === Realtime / audio / image models (not chat backends — listed for completeness) ===
    "gpt-realtime-2.1": {
        "context_length": 128_000,
        "family": "realtime",
        "supports_streaming": True,
        "supports_multimodal_input": True,        # text, audio, image
        "supports_multimodal_output": True,       # text, audio
        "tier": "standard",
    },
    "gpt-realtime-2.1-mini": {
        "context_length": 128_000,
        "family": "realtime",
        "supports_streaming": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "tier": "mini",
        "free_tier_eligible": True,
    },

    # === o-series — reasoning models (added R07.03 polish, discovered via live API) ===
    "o1": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "o-series",
        "tier": "flagship",
    },
    "o3": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "o-series",
        "tier": "flagship",
    },
    "o3-mini": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "o-series",
        "tier": "mini",
    },
    "o4-mini": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "o-series",
        "tier": "mini",
    },

    # === GPT-5.0 family — original gpt-5 release (discovered via live API) ===
    "gpt-5": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "gpt-5",
        "tier": "flagship",
    },
    "gpt-5-mini": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "mini",
    },
    "gpt-5-nano": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "nano",
    },
    "gpt-5-pro": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "family": "gpt-5",
        "tier": "pro",
    },
    "gpt-5-codex": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "supports_code_interpreter_tool": True,
        "family": "gpt-5",
        "tier": "codex",
    },

    # === GPT-5.1 family (discovered via live API) ===
    "gpt-5.1": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.1-codex": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "codex",
    },

    # === GPT-5.2 family (discovered via live API) ===
    "gpt-5.2": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.2-pro": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "pro",
    },

    # === GPT-5.4 expanded family (discovered via live API — had only base 5.4) ===
    "gpt-5.4-mini": {
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "mini",
    },
    "gpt-5.4-nano": {
        "context_length": 128_000,
        "max_completion_tokens": 16_384,
        "supports_thinking": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "nano",
    },
    "gpt-5.4-pro": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "pro",
    },

    # === GPT-5.5 pro variant (discovered via live API — had only base 5.5) ===
    "gpt-5.5-pro": {
        "context_length": 200_000,
        "max_completion_tokens": 100_000,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "pro",
    },

    # === GPT-5.6 Daybreak expanded (discovered via live API — had only sol+cyber) ===
    "gpt-5.6-luna": {
        "context_length": 200_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5.6",
        "tier": "daybreak",
    },
    "gpt-5.6-terra": {
        "context_length": 200_000,
        "max_completion_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_function_calling": True,
        "family": "gpt-5.6",
        "tier": "daybreak",
    },

    # === Legacy GPT-3.5 (still served, chat-capable via /chat/completions) ===
    "gpt-3.5-turbo": {
        "context_length": 16_384,
        "max_completion_tokens": 4_096,
        "supports_thinking": False,
        "supports_function_calling": True,
        "family": "gpt-3.5",
        "tier": "legacy",
    },
    "gpt-3.5-turbo-16k": {
        "context_length": 16_384,
        "max_completion_tokens": 4_096,
        "supports_thinking": False,
        "supports_function_calling": True,
        "family": "gpt-3.5",
        "tier": "legacy",
    },

    # === GPT-4.1 family (discovered via live API — had only gpt-4.1-mini) ===
    "gpt-4.1": {
        "context_length": 1_047_576,           # ~1M tokens
        "max_completion_tokens": 32_768,
        "supports_thinking": False,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-4.1",
        "tier": "standard",
    },
    "gpt-4.1-nano": {
        "context_length": 1_047_576,
        "max_completion_tokens": 32_768,
        "supports_thinking": False,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-4.1",
        "tier": "nano",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# OPENAI_FREE_MODEL_WHITELIST — models eligible for OPENAI_FREE_ONLY=true mode
# ─────────────────────────────────────────────────────────────────────────────
# 6 very-low-cost models covered by OpenAI's $1 trial credit / $10/mo API
# credit allowance. As of Sept 2026:
#   - gpt-6-luna: $0.10/$0.50 per 1M tokens (cheapest flagship)
#   - gpt-4o-mini, gpt-4.1-mini: legacy but very cheap
#   - gpt-realtime-2.1-mini: realtime/voice mini
#   - gpt-4o-mini-transcribe, gpt-transcribe: transcription
# See docs/OPENAI_API_TECHNICAL_REFERENCE.md §Free Tier & Trial Credits.
# R07.03: OPENAI_FREE_MODEL_WHITELIST is EMPTY.
# OpenAI has NO genuinely free ($0/token) models — every model has
# per-token pricing that consumes the $1 trial credit and $10/mo API
# credit. The whitelist was previously populated with "very low cost"
# models (gpt-6-luna at $0.10/$0.50 per 1M), but "low cost" is not
# "free" — the user's trial credit was exhausted by gpt-4o-mini despite
# being whitelisted. FREE_ONLY means $0/token, not "cheap enough that
# the credit lasts a while".
#
# When OPENAI_FREE_ONLY=true, all model requests are rejected with a
# clear message directing the user to set OPENAI_FREE_ONLY=false (which
# requires acknowledging that paid API calls will consume credit) or
# use the Hugging Face backend (HF_FREE_ONLY=true) for genuinely free
# models served by partner providers.
OPENAI_FREE_MODEL_WHITELIST: frozenset[str] = frozenset()


# ─────────────────────────────────────────────────────────────────────────────
# Service tier + reasoning_effort enums (for validation in _enforce_free_only)
# ─────────────────────────────────────────────────────────────────────────────
# Service tier values accepted by OpenAI's /v1/chat/completions endpoint
# (verified Sept 2026 via platform.openai.com/docs/api-reference/chat).
OPENAI_SERVICE_TIER_VALUES: frozenset[str] = frozenset({
    "auto",       # default — uses project's configured tier
    "default",    # standard pricing and performance
    "flex",       # 50% off, best-effort async
    "scale",      # custom — reserved capacity
    "priority",   # 2x premium — fastest latency (renamed from "fast" July 30 2026)
    "fast",       # alias for priority
})

# Reasoning effort values accepted by OpenAI for thinking-capable models
# (gpt-5.x+, gpt-6.x, o-series).
OPENAI_REASONING_EFFORT_VALUES: frozenset[str] = frozenset({
    "none",       # disable reasoning entirely (gpt-5.x only; gpt-6 rejects)
    "minimal",    # brief reasoning, minimal tokens
    "low",        # light reasoning (latency-sensitive)
    "medium",     # balanced — default for gpt-5.5+, gpt-6.sol/luna
    "high",       # deeper reasoning (complex planning, agentic tasks)
    "xhigh",      # maximum reasoning (hard reasoning, deep planning)
    "max",        # absolute maximum — use sparingly
})

# When OPENAI_FREE_ONLY is true, reasoning_effort is capped at this value
# (reasoning tokens are billed at output rate; higher effort = more tokens =
# faster credit exhaustion). "low" is the sweet spot — enough for tool use
# and basic planning without burning the trial credit on a single call.
OPENAI_FREE_ONLY_REASONING_EFFORT_CAP = "low"

# When OPENAI_FREE_ONLY is true, service_tier is forced to this value
# (never priority/fast/scale — those carry 2x pricing premium).
OPENAI_FREE_ONLY_SERVICE_TIER_FORCED = "default"


# ─────────────────────────────────────────────────────────────────────────────
# Non-chat model filter (mirrors GeminiBackend._NON_CHAT_PATTERNS pattern)
# ─────────────────────────────────────────────────────────────────────────────
# OpenAI's /v1/models endpoint returns 128+ models, but many aren't
# chat-capable (embeddings, TTS, image gen, video gen, moderation, ASR,
# legacy completions). These models don't work with /chat/completions —
# they use separate endpoints (/embeddings, /audio/speech, /images/
# generations, /moderations, etc.). Filter them out of the default
# listing so users see only chat-capable models.
#
# Matched case-insensitively against the model ID via substring match.
# To verify a pattern doesn't accidentally exclude a chat model, run:
#   agentkthx models --backend openai 2>&1 | grep -i "<pattern>"
# and confirm every result is genuinely non-chat.
_NON_CHAT_PATTERNS: tuple[str, ...] = (
    "embedding",        # text-embedding-3-large, text-embedding-3-small, text-embedding-ada-002
    "tts",              # tts-1, tts-1-hd, gpt-4o-mini-tts → /audio/speech
    "transcribe",       # gpt-4o-transcribe, gpt-4o-mini-transcribe, gpt-transcribe, gpt-live-transcribe → /audio/transcriptions
    "whisper",          # whisper-1 → legacy /audio/transcriptions
    "image",            # gpt-image-1, gpt-image-2, gpt-image-2.5-flare, gpt-image-2.5-sunburst, chatgpt-image-latest → /images/generations
    "sora",             # sora-2, sora-2-pro → video generation
    "moderation",       # omni-moderation-latest, omni-moderation-2024-09-26 → /moderations
    "babbage",          # babbage-002 → legacy /completions (not /chat/completions)
    "davinci",          # davinci-002 → legacy /completions (not /chat/completions)
)


def _is_chat_model(model_id: str) -> bool:
    """Return True if the model id looks like a chat-capable model.

    Checks against ``_NON_CHAT_PATTERNS`` — if any pattern is a
    case-insensitive substring of the model id, the model is classified
    as non-chat (embeddings, TTS, image gen, etc.) and excluded from the
    default listing. Chat-capable models (gpt-*, o-*, chat-*) pass through.
    """
    if not model_id:
        return False
    m = model_id.lower()
    for pattern in _NON_CHAT_PATTERNS:
        if pattern in m:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (module-level so tests can import them directly)
# ─────────────────────────────────────────────────────────────────────────────

def _is_free_model(model_id: str) -> bool:
    """Check whether a model id is in the OPENAI_FREE_MODEL_WHITELIST.

    OpenAI doesn't support routing suffixes (unlike HF Router's
    :fastest/:cheapest/:preferred) — OpenAI is the sole provider — so
    no suffix-stripping is needed here.
    """
    return model_id in OPENAI_FREE_MODEL_WHITELIST


def _detect_key_type(api_key: str) -> str:
    """Detect the OpenAI API key type from its prefix.

    Returns one of:
      - ``"user"``           — legacy ``sk-...`` user key (deprecated)
      - ``"project"``        — ``sk-proj-...`` project key (recommended)
      - ``"admin"``          — ``sk-admin-...`` admin key (admin endpoints only)
      - ``"service_account"`` — ``sk-sa-...`` service account key
      - ``"unknown"``        — empty or unrecognized prefix
    """
    if not api_key:
        return "unknown"
    if api_key.startswith("sk-proj-"):
        return "project"
    if api_key.startswith("sk-admin-"):
        return "admin"
    if api_key.startswith("sk-sa-"):
        return "service_account"
    if api_key.startswith("sk-"):
        return "user"  # legacy
    return "unknown"


def _is_insufficient_quota(err_str: str) -> bool:
    """Detect OpenAI HTTP 429 with `insufficient_quota` code (trial credit
    exhausted). Retrying is futile — the user needs to either upgrade or
    wait for the monthly credit refresh.
    """
    err_lower = err_str.lower()
    return "insufficient_quota" in err_lower


# ─────────────────────────────────────────────────────────────────────────────
# Backend class
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIBackend(OpenAICompatibleBackend):
    """Backend for the OpenAI Chat-Completions API.

    Connects to ``https://api.openai.com/v1/chat/completions`` — the
    canonical OpenAI wire format that every other OpenAI-compatible
    backend in the framework (ZAI, OpenRouter, Hugging Face, Gemini
    OpenAI-compat path) also speaks. This backend is the "first-party
    OpenAI direct" surface, adding OpenAI-specific auth (project-key
    scoping), service-tier selection, reasoning_effort parameter for
    thinking models, and the curated free-tier whitelist.

    Inherits the shared OpenAI Chat-Completions logic from
    OpenAICompatibleBackend (body construction, response parsing,
    SSE streaming, JEV dispatch, context-length 400 recovery) and
    adds:
      - OPENAI_API_KEY Bearer auth with token-type detection
        (sk- / sk-proj- / sk-admin- / sk-sa-) + optional
        OpenAI-Organization / OpenAI-Project headers
      - OPENAI_FREE_ONLY whitelist enforcement (caps
        reasoning_effort at "low", forces service_tier="default",
        treats HTTP 429 insufficient_quota as hard failure)
      - OPENAI_FREE_FALLBACK_MODEL swap on 429 insufficient_quota
      - OPENAI_SERVICE_TIER env var (auto/default/flex/scale/priority/fast)
      - OPENAI_REASONING_EFFORT env var (none→max) for reasoning models
      - 429 retry loop with Retry-After honor + exponential back-off
      - ReAct fallback when a model rejects the `tools` field (rare
        on OpenAI but kept for parity)
      - Reasoning-content capture for thinking-capable models
        (gpt-5.x+, gpt-6.x, o-series) — surfaced via the `reasoning_content`
        field on the response message
    """

    # Model cache with 1-hour timeout (mirrors OpenRouterBackend / HuggingFaceBackend).
    _model_cache: list[dict] | None = None
    _cache_time: float = 0.0
    _CACHE_TIMEOUT: int = 3600  # 1 hour in seconds

    # R06.54: maximum retries for rate-limit (429) and transient server
    # (502/503/504) responses before giving up. OpenAI's rate limits are
    # tier-based (free: 100 RPM/90k TPM; paid: tier-dependent) — match
    # OpenRouter's patience budget since 429s are routine on free tier.
    _MAX_429_RETRIES: int = 6

    # Exponential back-off schedule (seconds) when no usable Retry-After
    # header is present. Capped so a broken endpoint can't hang the agent.
    _429_BACKOFF_BASE: float = 5.0
    _429_BACKOFF_CAP: float = 90.0

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENAI,
    ):
        # Resolve base URL — priority: explicit > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        else:
            resolved_url = OPENAI_BASE_URL.rstrip("/")

        # Validate api_mode. OpenAI v0.1 only supports the Chat
        # Completions API (OPENAI mode). The Responses API is a
        # separate wire format — deferred to v0.2 of this plugin.
        # JEV mode is accepted because it uses the same wire format
        # under the hood (JEV wraps the chat call with a decision
        # prompt + JSON parsing).
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode == ApiMode.JEV:
            pass  # accepted — _jev_call_completions routes through generate()
        elif api_mode == ApiMode.OPENAI:
            pass
        else:
            raise ValueError(
                "OpenAI backend only supports OpenAI Chat-Completions "
                "or JEV (System-One) API modes. OPENRE mode (OpenResponses "
                "API) is not supported — OpenAI doesn't expose that wire "
                "format. The Responses API (/v1/responses) is a separate "
                "format deferred to v0.2 of this plugin."
            )

        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # Token is lazy — only required for generation, not for /models
        # listing (which OpenAI also requires auth for, unlike HF Router).
        # Read directly from env at __init__ time (NOT from the module-
        # level OPENAI_API_KEY constant) so tests can set the env var
        # after config.py has already been imported. Mirrors the
        # OpenRouterBackend pattern at openrouter.py:302.
        self.api_key: str = (
            os.environ.get("OPENAI_API_KEY")
            or OPENAI_API_KEY  # fall back to module-level
        )

        # Detect token type and surface a warning for legacy sk- keys
        # (they lack project-scoping and are discouraged for production).
        self._key_type: str = _detect_key_type(self.api_key)
        if self._key_type == "user" and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [OpenAI Debug] Legacy user API key detected (sk-...). "
                f"Project API keys (sk-proj-...) are recommended for "
                f"production — they support project-scoped billing and "
                f"tighter permissions. See "
                f"https://platform.openai.com/api-keys"
            )
        if self._key_type == "admin" and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [OpenAI Debug] Admin API key detected (sk-admin-...). "
                f"Admin keys are for administration endpoints only (users, "
                f"audit logs, projects) — they're not for inference calls. "
                f"Use a project key (sk-proj-...) for chat completions."
            )

        # Optional org/project headers — only set if env vars are populated
        # (relevant for sk-proj- and legacy sk- keys with multi-org accounts).
        self._organization_id: str = (
            os.environ.get("OPENAI_ORGANIZATION_ID")
            or OPENAI_ORGANIZATION_ID
        )
        self._project_id: str = (
            os.environ.get("OPENAI_PROJECT_ID")
            or OPENAI_PROJECT_ID
        )

        # ROB-06: Persisted safe max_tokens after a context-length 400.
        # When set, _get_model_defaults() returns this instead of the
        # model's reported max_completion_tokens. Mirrors OpenRouterBackend.
        self._context_safe_max_tokens: int | None = None

        # Headers for OpenAI — Authorization + Content-Type + optional
        # OpenAI-Organization + OpenAI-Project. User-Agent is set for
        # diagnostic purposes (OpenAI uses it for analytics, no leaderboard).
        self.headers: dict[str, str] = self._build_auth_headers()

        # Force model list to be loaded on initialization so the cache
        # is populated (mirrors OpenRouterBackend / HuggingFaceBackend).
        # Failures are silent — the static OPENAI_MODELS catalog is
        # the fallback.
        try:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print("  [OpenAI Debug] Initializing: loading models into cache")
            self.list_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenAI Debug] Failed to initialize models: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Required BaseBackend properties
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OPENAI

    @property
    def base_url(self) -> str:
        return self._base_url

    # api_mode property/setter is inherited from OpenAICompatibleBackend

    # ─────────────────────────────────────────────────────────────────────
    # Auth header construction (handles org/project scoping)
    # ─────────────────────────────────────────────────────────────────────

    def _build_auth_headers(self) -> dict[str, str]:
        """Build the auth header dict for OpenAI API calls.

        Includes Authorization (always), Content-Type (always), and
        OpenAI-Organization / OpenAI-Project headers only when env
        vars are populated. The User-Agent is set for diagnostic
        purposes (OpenAI uses it for analytics, no leaderboard system
        like OpenRouter's HTTP-Referer / X-Title).
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AgentKthx/0.x (+https://github.com/VTSTech/AgentKthx)",
        }
        if self._organization_id:
            headers["OpenAI-Organization"] = self._organization_id
        if self._project_id:
            headers["OpenAI-Project"] = self._project_id
        return headers

    # ─────────────────────────────────────────────────────────────────────
    # Model listing & discovery
    # ─────────────────────────────────────────────────────────────────────

    def _parse_openai_model(self, model_data: dict) -> dict:
        """Parse OpenAI /v1/models response into AgentKthx format.

        OpenAI's /v1/models endpoint returns a flat list with minimal
        per-model metadata (id, object, created, owned_by). Context
        length and pricing data are NOT exposed by the API — the
        static OPENAI_MODELS catalog is the source of truth for those.

        Live API data is merged with the static catalog: if a model id
        appears in both, the static catalog's richer metadata (context
        length, pricing, capabilities) wins.
        """
        model_id = model_data.get("id", "")
        if not model_id:
            return {}

        # Cross-reference with static catalog for richer metadata
        static_info = OPENAI_MODELS.get(model_id, {})

        return {
            "name": model_id,
            "size": 0,  # OpenAI doesn't expose size info
            "details": {
                "family": static_info.get("family", "unknown"),
                "backend": "openai",
                "context_length": static_info.get("context_length", 128_000),
                "max_completion_tokens": static_info.get("max_completion_tokens", 16_384),
                "supports_thinking": static_info.get("supports_thinking", False),
                "supports_function_calling": static_info.get("supports_function_calling", True),
                "supports_multimodal_input": static_info.get("supports_multimodal_input", False),
                "free_tier_eligible": static_info.get("free_tier_eligible", False),
                "owned_by": model_data.get("owned_by", "openai"),
                "standard_input_per_1m": static_info.get("standard_input_per_1m"),
                "standard_output_per_1m": static_info.get("standard_output_per_1m"),
                "tier": static_info.get("tier", "unknown"),
                # R07.03: capture shutdown_date if exposed by the API
                # (OpenAI now returns this for models being deprecated —
                # surfaced in the model listing as a deprecation warning).
                "shutdown_date": model_data.get("shutdown_date"),
            },
            "model_data": model_data,  # full raw response preserved
        }

    def list_models(self) -> list[dict]:
        """List available models from OpenAI /v1/models with caching.

        Cache timeout: 1 hour (3600 seconds).
        Refresh endpoint: GET /v1/models (auth required — unlike HF
        Router which is anonymous).

        When ``OPENAI_FREE_ONLY`` is true, the cache is filtered to only
        include models in ``OPENAI_FREE_MODEL_WHITELIST``.
        """
        current_time = time.time()
        if (self._model_cache is not None
                and current_time - self._cache_time < self._CACHE_TIMEOUT):
            return self._model_cache

        try:
            # OpenAI requires auth for /v1/models — fail fast if no API key
            if not self.api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY environment variable is required for "
                    "model discovery. Generate a project API key at "
                    "https://platform.openai.com/api-keys (sk-proj- prefix "
                    "is recommended for production)."
                )

            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers=self.headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                models_data = json.loads(resp.read().decode("utf-8"))

            available_models: list[dict] = []
            for model in models_data.get("data", []):
                model_id = model.get("id")
                if model_id and _is_chat_model(model_id):
                    available_models.append(self._parse_openai_model(model))

            # Add catalog-only models (not returned by API but still in
            # our static OPENAI_MODELS catalog) — fallback safety net.
            cached_names = {m["name"] for m in available_models}
            for name, info in OPENAI_MODELS.items():
                if name not in cached_names:
                    available_models.append({
                        "name": name,
                        "size": 0,
                        "details": {
                            "family": info.get("family", "unknown"),
                            "backend": "openai",
                            "context_length": info.get("context_length", 128_000),
                            "max_completion_tokens": info.get("max_completion_tokens", 16_384),
                            "supports_thinking": info.get("supports_thinking", False),
                            "supports_function_calling": info.get("supports_function_calling", True),
                            "supports_multimodal_input": info.get("supports_multimodal_input", False),
                            "free_tier_eligible": info.get("free_tier_eligible", False),
                            "owned_by": "openai",
                            "standard_input_per_1m": info.get("standard_input_per_1m"),
                            "standard_output_per_1m": info.get("standard_output_per_1m"),
                            "tier": info.get("tier", "unknown"),
                        },
                    })

            # OPENAI_FREE_ONLY: filter to whitelist only
            if OPENAI_FREE_ONLY:
                available_models = [
                    m for m in available_models
                    if _is_free_model(m["name"])
                ]

            self._model_cache = sorted(available_models, key=lambda x: x["name"])
            self._cache_time = current_time

            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenAI Debug] Stored {len(self._model_cache)} models in cache")
                for m in self._model_cache[:10]:
                    print(f"    - {m['name']}")
                if len(self._model_cache) > 10:
                    print(f"    ... and {len(self._model_cache) - 10} more")

            return self._model_cache

        except Exception as e:
            # Fallback to static catalog if API fails
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenAI Debug] /v1/models unreachable, using static catalog: {e}")

            catalog_models: list[dict] = []
            for name, info in OPENAI_MODELS.items():
                catalog_models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": info.get("family", "unknown"),
                        "backend": "openai",
                        "context_length": info.get("context_length", 128_000),
                        "max_completion_tokens": info.get("max_completion_tokens", 16_384),
                        "supports_thinking": info.get("supports_thinking", False),
                        "supports_function_calling": info.get("supports_function_calling", True),
                        "supports_multimodal_input": info.get("supports_multimodal_input", False),
                        "free_tier_eligible": info.get("free_tier_eligible", False),
                        "owned_by": "openai",
                        "standard_input_per_1m": info.get("standard_input_per_1m"),
                        "standard_output_per_1m": info.get("standard_output_per_1m"),
                        "tier": info.get("tier", "unknown"),
                    },
                })

            if OPENAI_FREE_ONLY:
                catalog_models = [m for m in catalog_models if _is_free_model(m["name"])]

            self._model_cache = sorted(catalog_models, key=lambda x: x["name"])
            self._cache_time = current_time
            return self._model_cache

    def is_running(self) -> bool:
        """OpenAI API is a cloud API, so it's always 'running'."""
        return True

    def _get_model_info(self, model_name: str) -> dict | None:
        """Get model metadata from the static catalog."""
        return OPENAI_MODELS.get(model_name)

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Get the model's maximum trained context window size."""
        info = self._get_model_info(model)
        if info and "context_length" in info:
            return info["context_length"]

        # Check cache if available
        if self._model_cache:
            for cached in self._model_cache:
                if cached["name"] == model:
                    return cached["details"].get("context_length", 128_000)

        # Fallback to family-based defaults
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx

        return 128_000

    def _get_model_defaults(self, model: str) -> dict:
        """Return per-model defaults: temperature, max_tokens, context_length.

        ARCH-03 (R06.57): cap + persisted-safe-value logic inherited
        from OpenAICompatibleBackend._apply_max_tokens_cap. OpenAI
        models report max_completion_tokens close to the full context
        length (e.g. 65536 on a 400000 context model) — the num_ctx // 32
        cap (8192 on a 256000 context model) gives 97% of context to
        input on long agentic runs.
        """
        info = self._get_model_info(model)

        # Check cache first for models not in static catalog
        if not info and self._model_cache:
            for cached in self._model_cache:
                if cached["name"] == model:
                    details = cached["details"]
                    max_tokens = details.get("max_completion_tokens", 4096)
                    context_length = details.get("context_length", 128_000)
                    return self._apply_max_tokens_cap(
                        max_tokens, context_length, temperature=0.7
                    )

        max_tokens = info.get("max_completion_tokens", 4096) if info else 4096
        context_length = info.get("context_length", 128_000) if info else 128_000
        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=0.7
        )

    # ─────────────────────────────────────────────────────────────────────
    # 429 / 5xx retry helpers (mirrors OpenRouterBackend R06.54 / HuggingFaceBackend)
    # ─────────────────────────────────────────────────────────────────────

    def _max_429_retries(self) -> int:
        """Resolve the 429 retry budget (env override > class default)."""
        raw = os.environ.get("OPENAI_MAX_429_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_429_RETRIES

    def _429_backoff(self, attempt: int) -> float:
        """Back-off wait for the Nth (1-based) rate-limit retry.

        Schedule: 5s → 10s → 20s → 40s → 80s → 90s cap, with ±20% jitter.
        """
        delay = self._429_BACKOFF_BASE * (2 ** max(0, attempt - 1))
        delay = min(delay, self._429_BACKOFF_CAP)
        jitter = delay * 0.2
        return max(1.0, delay + random.uniform(-jitter, jitter))

    # ─────────────────────────────────────────────────────────────────────
    # HTTP request wrapper — 429/5xx retry, 401 detection, 429 insufficient_quota fallback
    # ─────────────────────────────────────────────────────────────────────

    def _make_api_request(
        self,
        endpoint: str,
        data: dict,
        stream: bool = False,
    ) -> dict | Iterable[bytes]:
        """Make a request to OpenAI with automatic 429/5xx retry and
        429 insufficient_quota trial-credit-exhaustion fallback.

        On HTTP 429 with `insufficient_quota` code (trial credit
        exhausted):
          - If ``OPENAI_FREE_ONLY`` is true, raises a clear actionable
            error (no retry — retrying is futile, user must upgrade
            or wait for monthly credit refresh).
          - Otherwise, swaps the model to ``OPENAI_FREE_FALLBACK_MODEL``
            and retries once (mirrors ZAI's 429 insufficient-balance
            fallback pattern).

        On HTTP 429 (other rate limit) or transient 5xx errors, waits —
        honoring the ``Retry-After`` header when present, otherwise an
        exponential back-off schedule — and retries up to
        ``_max_429_retries()`` times (default 6).

        Other errors are normalized to RuntimeError carrying the
        upstream error message so callers can pattern-match on the
        text (e.g. to detect "does not support tools" for the ReAct
        fallback path).
        """
        url = f"{self.base_url}/{endpoint}"

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is required for OpenAI "
                "API calls. Generate a project API key at "
                "https://platform.openai.com/api-keys (sk-proj- prefix is "
                "recommended for production)."
            )

        headers = dict(self.headers)
        # Refresh the API key in case it was changed after __init__
        # (e.g. tests may patch self.api_key after construction).
        headers["Authorization"] = f"Bearer {self.api_key}"

        if stream:
            return self._stream_request(url, data, headers)

        max_retries = self._max_429_retries()
        last_retryable_error: str | None = None
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
                err_data = None
                try:
                    if body_text:
                        err_data = json.loads(body_text)
                except (json.JSONDecodeError, ValueError):
                    pass

                # ---- 429 insufficient_quota: trial credit exhausted ----
                if status_code == 429 and _is_insufficient_quota(body_text):
                    current_model = data.get("model", "")
                    if OPENAI_FREE_ONLY:
                        # Strict mode: no retry, no fallback — surface
                        # actionable error.
                        raise RuntimeError(
                            f"OpenAI trial credit exhausted for "
                            f"'{current_model}' (insufficient_quota). "
                            f"Upgrade to a paid tier at "
                            f"https://platform.openai.com/settings/billing "
                            f"OR wait for the monthly credit refresh. "
                            f"Set OPENAI_FREE_ONLY=false to disable strict "
                            f"enforcement (still requires a paid key with "
                            f"billing enabled)."
                        )
                    # Non-strict: swap to OPENAI_FREE_FALLBACK_MODEL, retry once
                    fallback = OPENAI_FREE_FALLBACK_MODEL
                    if _is_free_model(current_model):
                        # Already a free model and still 429 — credit is
                        # truly exhausted. Don't retry; surface the error.
                        raise RuntimeError(
                            f"OpenAI trial credit exhausted and fallback "
                            f"'{fallback}' is also a free model — no point "
                            f"retrying. Add billing at "
                            f"https://platform.openai.com/settings/billing."
                        )
                    import sys
                    print(
                        f"\n  \033[33m[OpenAI] Trial credit exhausted for "
                        f"'{current_model}' — falling back to "
                        f"'{fallback}'\033[0m",
                        file=sys.stderr,
                    )
                    fallback_data = {**data, "model": fallback}
                    fallback_req = urllib.request.Request(
                        url,
                        data=json.dumps(fallback_data).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(
                            fallback_req, timeout=self.config.timeout
                        ) as resp2:
                            return json.loads(resp2.read().decode("utf-8"))
                    except urllib.error.HTTPError as e2:
                        error_body2 = (
                            e2.read().decode("utf-8") if e2.fp else ""
                        )
                        raise RuntimeError(
                            f"OpenAI: paid model '{current_model}' "
                            f"failed (insufficient_quota, trial credit "
                            f"exhausted) and free fallback '{fallback}' "
                            f"also failed: HTTP {e2.code}: {error_body2}"
                        )

                # ---- 429 Rate Limit / transient 5xx: wait and retry ----
                retryable = (
                    status_code == 429
                    or status_code in (502, 503, 504)
                )
                if retryable:
                    error_msg = (
                        "Rate limit exceeded"
                        if status_code == 429
                        else f"Provider error {status_code}"
                    )
                    retry_after_raw = e.headers.get("Retry-After", "")
                    if isinstance(err_data, dict):
                        if "error" in err_data:
                            inner = err_data["error"]
                            error_msg = (
                                inner.get("message", inner)
                                if isinstance(inner, dict) else str(inner)
                            )
                        elif "message" in err_data:
                            error_msg = err_data["message"]
                    last_retryable_error = error_msg

                    retry_after = None
                    if retry_after_raw:
                        try:
                            retry_after = float(retry_after_raw)
                        except (ValueError, TypeError):
                            retry_after = None
                    if retry_after is None:
                        retry_after = self._429_backoff(attempt + 1)
                    retry_after = min(max(retry_after, 1.0), self._429_BACKOFF_CAP)

                    if attempt < max_retries:
                        print(
                            f"  [OpenAI] {status_code} — {error_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                        time.sleep(retry_after)
                        continue
                    else:
                        raise RuntimeError(
                            f"OpenAI rate limit: {error_msg}. "
                            f"Retried {max_retries} times. "
                            f"Try again in {retry_after:.0f} seconds."
                        )

                # ---- 401 Auth error ----
                if status_code == 401:
                    raise RuntimeError(
                        "OpenAI authentication failed. Please check "
                        "your OPENAI_API_KEY environment variable. "
                        "Generate a project API key at "
                        "https://platform.openai.com/api-keys (sk-proj- "
                        "prefix is recommended for production)."
                    )

                # ---- Any other 4xx/5xx error ----
                if status_code >= 400:
                    upstream_msg = ""
                    if isinstance(err_data, dict):
                        err_field = err_data.get("error")
                        if isinstance(err_field, dict):
                            upstream_msg = err_field.get("message", "") or str(err_field)
                        elif isinstance(err_field, str):
                            upstream_msg = err_field
                        elif err_data.get("message"):
                            upstream_msg = err_data["message"]
                        else:
                            upstream_msg = str(err_data)
                    else:
                        upstream_msg = body_text[:500]

                    if len(upstream_msg) > 500:
                        upstream_msg = upstream_msg[:500] + "..."

                    raise RuntimeError(
                        f"OpenAI API error {status_code}: {upstream_msg}"
                    )

            except urllib.error.URLError as e:
                raise RuntimeError(f"OpenAI connection error: {e.reason}")

        # Should not reach here — the loop either returns or raises.
        raise RuntimeError(
            f"OpenAI API request exhausted retries: {last_retryable_error}"
        )

    def _stream_request(
        self,
        url: str,
        data: dict,
        headers: dict,
    ) -> Generator[bytes, None, None]:
        """Low-level SSE stream — yields raw bytes lines from the SSE stream.

        ROB-04 (R06.56): stdlib ``urllib.request`` (not ``requests``) to
        preserve the zero-dependency claim. ROB-06 (R06.57): try/finally
        so the urllib response is closed deterministically when the
        generator is abandoned mid-iteration.
        """
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            response = urllib.request.urlopen(req, timeout=self.config.timeout)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            # 429 insufficient_quota on streaming path — surface as
            # RuntimeError (don't fallback on stream, the body is already
            # half-sent)
            if e.code == 429 and _is_insufficient_quota(error_body):
                raise RuntimeError(
                    f"OpenAI trial credit exhausted (stream, "
                    f"insufficient_quota). Upgrade at "
                    f"https://platform.openai.com/settings/billing."
                )
            raise RuntimeError(
                f"OpenAI HTTP error {e.code} (stream): {error_body}"
            )
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenAI connection error: {e.reason}")

        try:
            for line in response:
                yield line
        finally:
            try:
                response.close()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Tool-support testing
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Test tool support for a model via OpenAI API.

        OpenAI is the canonical implementation of OpenAI function calling
        — every chat-capable model supports native tool calling (with
        rare exceptions for transcription-only models like gpt-transcribe
        which aren't chat backends anyway). We assume NATIVE for every
        chat-capable model without probing — no live API call is made.

        Non-chat models (embeddings, TTS, image gen, etc. — detected
        via ``_NON_CHAT_PATTERNS``) return ``NONE`` so users don't
        accidentally try to chat with an embedding model.
        """
        # Non-chat models (embeddings, TTS, image gen, moderation, etc.)
        # don't support /chat/completions — classify as NONE.
        if not _is_chat_model(model):
            return ToolSupportLevel.NONE
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # Body construction override — adds service_tier + reasoning_effort
    # ─────────────────────────────────────────────────────────────────────

    def _build_openai_body(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """Build an OpenAI Chat-Completions request body.

        Extends the inherited OpenAICompatibleBackend._build_openai_body
        with OpenAI-specific parameters:
          - service_tier (from OPENAI_SERVICE_TIER env var or kwargs)
          - reasoning_effort (from OPENAI_REASONING_EFFORT env var or kwargs)
          - system → developer role translation for o-series / gpt-5.x+

        When OPENAI_FREE_ONLY is true:
          - service_tier is forced to "default" (never priority/fast/scale)
          - reasoning_effort is capped at "low" (reasoning tokens are
            billed at output rate, can quickly exhaust trial credit)
        """
        # Inherit the base body construction (handles stream_options,
        # tools, tool_choice, response_format, etc.)
        body = super()._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        # OPENAI_FREE_ONLY: enforce restrictions on service_tier and
        # reasoning_effort to prevent accidental credit exhaustion
        if OPENAI_FREE_ONLY:
            body["service_tier"] = OPENAI_FREE_ONLY_SERVICE_TIER_FORCED

            # Cap reasoning_effort at "low" if it's set higher
            reasoning_effort = body.get("reasoning_effort")
            if reasoning_effort and reasoning_effort not in (
                "none", "minimal", "low"
            ):
                body["reasoning_effort"] = OPENAI_FREE_ONLY_REASONING_EFFORT_CAP
        else:
            # Add service_tier if set via env var or kwargs
            service_tier = kwargs.get("service_tier") or OPENAI_SERVICE_TIER
            if service_tier and service_tier in OPENAI_SERVICE_TIER_VALUES:
                body["service_tier"] = service_tier

            # Add reasoning_effort if set via env var or kwargs
            reasoning_effort = (
                kwargs.get("reasoning_effort")
                or OPENAI_REASONING_EFFORT
            )
            if reasoning_effort and reasoning_effort in OPENAI_REASONING_EFFORT_VALUES:
                body["reasoning_effort"] = reasoning_effort

        # System → Developer role translation for reasoning models
        # (o1 and newer: gpt-5.x+, gpt-6.x). The developer role replaces
        # the system role for these models — OpenAI's wire format accepts
        # both but surfaces a deprecation warning for `system` on
        # reasoning models.
        family_info = OPENAI_MODELS.get(model, {})
        if family_info.get("supports_thinking", False):
            for msg in body.get("messages", []):
                if msg.get("role") == "system":
                    msg["role"] = "developer"

        return body

    # ─────────────────────────────────────────────────────────────────────
    # Generate (non-streaming)
    # ─────────────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response using OpenAI's Chat Completions API.

        Implements the OpenAI Chat Completions spec with native tool-
        calling support, automatic ReAct fallback when a model rejects
        the `tools` field (rare on OpenAI but kept for parity), and
        context-length 400 recovery (shared with all OpenAI-compatible
        backends via ARCH-03).

        Args:
            model: OpenAI model id (e.g. "gpt-6-sol", "gpt-4o-mini").
            messages: Chat messages in OpenAI format.
            tools: Optional list of Tool objects for native function
                calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Optional OpenAI params — top_p, stop,
                presence_penalty, frequency_penalty, response_format,
                tool_choice, reasoning_effort, service_tier, etc.

        Returns:
            Dict with keys: content, tool_calls, finish_reason, usage,
            latency_ms, raw.
        """
        # JEV dispatch — if api_mode is JEV, route through
        # generate_decision() which wraps the underlying LLM call with
        # a decision prompt.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else 8192,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # OPENAI_FREE_ONLY: reject non-whitelisted models upfront (before
        # any HTTP request is made — prevents accidental paid API calls
        # that burn trial credit).
        if OPENAI_FREE_ONLY:
            raise RuntimeError(
                f"OPENAI_FREE_ONLY=true but OpenAI has no genuinely free "
                f"($0/token) models — every model has per-token pricing "
                f"that consumes your trial credit ($1) and monthly API "
                f"credit ($10). Model '{model}' costs money per token. "
                f"Set OPENAI_FREE_ONLY=false to use paid models with your "
                f"API credit, or use --backend hf (Hugging Face) with "
                f"HF_FREE_ONLY=true for genuinely free models served by "
                f"partner providers at $0/token."
            )

        # Use model defaults from catalog/cache if not specified
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [OpenAI] POST chat/completions — model={model}, "
                f"tools={len(tools) if tools else 0}, "
                f"service_tier={body.get('service_tier', 'auto')}, "
                f"reasoning_effort={body.get('reasoning_effort', 'default')}"
            )

        start_time = time.time()
        try:
            raw_response = self._make_api_request("chat/completions", body)
        except RuntimeError as e:
            err_str = str(e)
            # ReAct fallback: rare on OpenAI but kept for parity with
            # other OpenAI-compatible backends. Retries without the
            # `tools` field if the model rejects it.
            if tools and self._is_tools_not_supported_error(err_str):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [OpenAI] Model rejected tools — retrying "
                        f"without tools (ReAct fallback)"
                    )
                body.pop("tools", None)
                body.pop("tool_choice", None)
                raw_response = self._make_api_request("chat/completions", body)
            # Context-length 400: shared handler from OpenAICompatibleBackend.
            elif "context length" in err_str.lower() or "maximum context" in err_str.lower():
                old_max = body.get("max_tokens", 4096)
                new_max = max(old_max // 3, 4096)
                if new_max < old_max:
                    print(
                        f"  [OpenAI] Context length exceeded — reducing "
                        f"max_tokens {old_max} -> {new_max} and retrying"
                    )
                    body["max_tokens"] = new_max
                    self._context_safe_max_tokens = new_max
                    raw_response = self._make_api_request("chat/completions", body)
                else:
                    raise RuntimeError(f"OpenAI API error: {err_str}")
            else:
                raise RuntimeError(f"OpenAI API error: {err_str}")

        latency_ms = (time.time() - start_time) * 1000
        parsed = self._parse_openai_response(raw_response)
        parsed["latency_ms"] = latency_ms

        # Synthesize finish_reason if the API omitted one
        if parsed["finish_reason"] is None:
            if parsed["tool_calls"]:
                parsed["finish_reason"] = "tool_calls"
            else:
                parsed["finish_reason"] = "stop"

        # Detect empty responses — surface as an error so the chat loop
        # can show the user something went wrong.
        if not parsed["content"].strip() and not parsed["tool_calls"]:
            raise RuntimeError(
                f"OpenAI returned an empty response (no content, no "
                f"tool_calls) for model '{model}'. This may be a rate "
                f"limit, content filter, or model issue. "
                f"finish_reason={parsed['finish_reason']}"
            )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [OpenAI] finish_reason={parsed['finish_reason']}, "
                f"tool_calls={len(parsed['tool_calls'])}, "
                f"content_len={len(parsed['content'])}"
            )

        return parsed

    # ─────────────────────────────────────────────────────────────────────
    # System-One Decision Mode (ApiMode.JEV)
    # ─────────────────────────────────────────────────────────────────────

    def _jev_call_completions(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 512,
        think: bool | None = None,
        response_format: dict | None = None,
        **kwargs,
    ) -> dict:
        """JEV hook for OpenAI: route the decision call through OpenAI's
        /chat/completions endpoint with full auth, 429 retry, and
        OPENAI_FREE_ONLY handling — all of which live in self.generate().

        The response shape returned by self.generate() already matches
        what generate_decision() expects:
            {content, tool_calls, usage, latency_ms, raw, finish_reason}

        Decisions never carry tools — pass tools=None explicitly so
        the ReAct fallback path in self.generate() doesn't trigger.

        NOTE: We intentionally do NOT pass response_format to OpenAI
        here. Some models (notably o-series with high reasoning_effort)
        silently return empty content when response_format={"type":
        "json_object"} is forced. The JEV System-One prompt already
        instructs the model to output JSON-only, so response_format is
        redundant.
        """
        kwargs.pop("response_format", None)

        # OPENAI_FREE_ONLY is enforced inside self.generate(); we don't
        # silently swap models here — the user picked the model.

        # CRITICAL: Temporarily flip api_mode to OPENAI to avoid
        # infinite recursion. self.generate() calls _maybe_jev_dispatch()
        # at the top, which would call generate_decision() →
        # _jev_call_completions() → self.generate() again.
        original_api_mode = self._api_mode
        self._api_mode = ApiMode.OPENAI
        try:
            try:
                return self.generate(
                    model=model,
                    messages=messages,
                    tools=None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
            except RuntimeError as e:
                err_lower = str(e).lower()
                if "empty response" in err_lower or "no content" in err_lower:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [OpenAI.JEV] Empty response — retrying "
                            f"with simplified prompt"
                        )
                    simplified = [
                        {"role": "user", "content": messages[-1]["content"] if messages else ""}
                    ]
                    return self.generate(
                        model=model,
                        messages=simplified,
                        tools=None,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                raise
        finally:
            self._api_mode = original_api_mode

    # ─────────────────────────────────────────────────────────────────────
    # ReAct-fallback error detection (mirrors OpenRouterBackend / HuggingFaceBackend)
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_tools_not_supported_error(err_str: str) -> bool:
        """Detect OpenAI 'tools not supported' rejection.

        Rare on OpenAI (every chat-capable model supports native function
        calling) but kept for parity with the other OpenAI-compatible
        backends. Some legacy models or restricted-access models (e.g.
        gpt-rosalind-research) may reject the `tools` field at runtime.
        """
        err_lower = err_str.lower()
        indicators = (
            "does not support tools",
            "tools are not supported",
            "tool calling is not supported",
            "tools are not yet supported",
            "does not support function calling",
            "function calling is not supported",
            "no tools endpoint",
            "tool use is not supported",
            "unsupported param: tools",
        )
        return any(ind in err_lower for ind in indicators)

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks (ARCH-01)
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """OpenAI's chat completions endpoint."""
        return f"{self.base_url}/chat/completions"

    def _get_auth_headers(self) -> dict:
        """OpenAI requires Bearer token + optional OpenAI-Organization /
        OpenAI-Project headers for multi-org / project-scoped billing."""
        return self._build_auth_headers()

    def _iter_sse_lines(
        self,
        url: str,
        body: dict,
        headers: dict,
    ) -> Iterable[bytes]:
        """Make a streaming POST to OpenAI's /chat/completions.

        ROB-04 (R06.56): uses stdlib ``urllib.request.urlopen`` (not
        ``requests``) to preserve the zero-dependency claim. The base
        class ``generate_completions_stream()`` parses these lines.

        ARCH-03 (R06.57): Context-length 400 recovery delegates to the
        shared ``_handle_context_length_400`` helper inherited from
        ``OpenAICompatibleBackend``. OpenAI's error format matches the
        base class defaults, so no regex override is needed.
        """
        for attempt in range(2):  # max 2 attempts (original + 1 retry)
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                response = urllib.request.urlopen(req, timeout=self.config.timeout)
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""

                # ARCH-03 (R06.57): Shared context-length 400 handler
                if e.code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(error_body, body):
                        new_max = body["max_tokens"]
                        print(
                            f"  [OpenAI-Stream] Context length exceeded — "
                            f"reducing max_tokens {old_max} → {new_max} "
                            f"and retrying"
                        )
                        continue

                # 429 insufficient_quota on streaming path
                if e.code == 429 and _is_insufficient_quota(error_body):
                    raise RuntimeError(
                        f"OpenAI trial credit exhausted (stream, "
                        f"insufficient_quota). Upgrade at "
                        f"https://platform.openai.com/settings/billing."
                    )

                # ReAct fallback on streaming 400 (rare on OpenAI)
                if "does not support tools" in error_body.lower() and body.get("tools"):
                    import sys
                    print(
                        f"\n  \033[33m[OpenAI-Stream] Model "
                        f"'{body.get('model')}' does not support tools "
                        f"— retrying without tool definitions\033[0m",
                        file=sys.stderr,
                    )
                    body_fb = {k: v for k, v in body.items() if k != "tools"}
                    body_fb.pop("tool_choice", None)
                    fb_req = urllib.request.Request(
                        url,
                        data=json.dumps(body_fb).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    try:
                        response = urllib.request.urlopen(
                            fb_req, timeout=self.config.timeout
                        )
                    except urllib.error.HTTPError as e2:
                        error_body2 = (
                            e2.read().decode("utf-8") if e2.fp else ""
                        )
                        raise RuntimeError(
                            f"OpenAI HTTP error {e2.code} "
                            f"(stream no-tools fallback): {error_body2}"
                        )
                else:
                    raise RuntimeError(
                        f"OpenAI HTTP error {e.code} (stream): {error_body}"
                    )
            except urllib.error.URLError as e:
                raise RuntimeError(f"OpenAI connection error: {e.reason}")

            # ROB-06 (R06.57): try/finally so the urllib response is
            # closed deterministically when the generator is abandoned
            # mid-iteration.
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

    # _get_model_defaults() already exists above (uses static OPENAI_MODELS)
    # generate_completions_stream() is overridden below to enforce
    # OPENAI_FREE_ONLY upfront (the agentic loop calls generate_completions_stream()
    # directly for cloud backends, bypassing the generate_stream() override).

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from OpenAI.

        ARCH-01: Thin override that handles OPENAI_FREE_ONLY upfront, then
        delegates to super().generate_completions_stream() (from
        OpenAICompatibleBackend) which uses _get_chat_completions_url(),
        _get_auth_headers(), _iter_sse_lines(), and
        _build_openai_body(stream=True).

        This override is necessary because the agentic loop calls
        generate_completions_stream() directly for cloud backends (not
        generate_stream()). Without this override, the inherited base-
        class version would skip the OPENAI_FREE_ONLY whitelist check and
        make HTTP requests for non-whitelisted models — defeating the
        purpose of OPENAI_FREE_ONLY (which is to prevent accidental
        trial-credit-burning API calls). Same lesson learned on the HF
        plugin (R07.02 polish) — see HuggingFaceBackend override.
        """
        # OPENAI_FREE_ONLY: reject non-whitelisted models upfront
        if OPENAI_FREE_ONLY:
            raise RuntimeError(
                f"OPENAI_FREE_ONLY=true but OpenAI has no genuinely free "
                f"models. Model '{model}' costs money per token. Set "
                f"OPENAI_FREE_ONLY=false or use --backend hf."
            )

        yield from super().generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from OpenAI.

        ARCH-01: Thin override that handles OPENAI_FREE_ONLY upfront, then
        delegates to the inherited ``generate_completions_stream()``
        (from OpenAICompatibleBackend).
        """
        # OPENAI_FREE_ONLY: reject non-whitelisted models upfront
        if OPENAI_FREE_ONLY:
            raise RuntimeError(
                f"OPENAI_FREE_ONLY=true but OpenAI has no genuinely free "
                f"models. Model '{model}' costs money per token. Set "
                f"OPENAI_FREE_ONLY=false or use --backend hf."
            )

        for chunk in self.generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            delta = chunk.get("delta", "")
            if delta:
                yield delta

    # ─────────────────────────────────────────────────────────────────────
    # Runtime context (mirrors OpenRouterBackend / HuggingFaceBackend)
    # ─────────────────────────────────────────────────────────────────────

    def get_model_runtime_context(self, model: str) -> int:
        """Get the runtime context window size for a model."""
        return self.get_model_max_context(model)

    def __repr__(self) -> str:
        return (
            f"OpenAIBackend(base_url={self._base_url!r}, "
            f"api_mode={self._api_mode}, free_only={OPENAI_FREE_ONLY}, "
            f"key_type={self._key_type})"
        )
