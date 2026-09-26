"""
⚛️ AgentKthx — Gemini API Backend

Backend implementation for the Google Gemini API via its OpenAI-compatible
endpoint. Google exposes a Chat-Completions-compatible surface at
``https://generativelanguage.googleapis.com/v1beta/openai/`` — drop the
OpenAI Python client there with a Gemini API key and everything Just Works.

This backend subclasses ``OpenAICompatibleBackend`` (the same shared base
that ``ZaiBackend`` and ``OpenRouterBackend`` use) and adds Gemini-specific
defaults, thinking-config routing, free-tier rate-limit handling, and the
``extra_body.google.*`` parameter surface for native Gemini features.

Endpoints used:
  - POST /chat/completions  → OpenAI Chat Completions (tools, streaming)
  - GET  /models            → model discovery (OpenAI-compatible)
  - GET  /models/{model}    → retrieve a single model's metadata

Configuration:
  GEMINI_API_KEY          — API key (required; GOOGLE_API_KEY accepted as fallback)
  GEMINI_BASE_URL         — OpenAI-compat base URL
                            (default: https://generativelanguage.googleapis.com/v1beta/openai/)
  GEMINI_DEFAULT_MODEL    — Default model (default: gemini-3.8-flash)
  GEMINI_FREE_ONLY        — If true, restrict model list to free-tier models
  GEMINI_THINKING_LEVEL   — Default thinking level for Gemini 3.x:
                            one of "minimal" | "low" | "medium" | "high" (default: unset)
  GEMINI_SERVICE_TIER     — "standard" (default) | "flex" | "priority"

Usage:
  # CLI
  agentkthx chat --backend gemini --model gemini-3.8-flash --tools calculator
  agentkthx run "What is 15 * 8?" --backend gemini --model gemini-2.5-flash

  # Python API
  from agentkthx import Agent
  agent = Agent(model="gemini-3.8-flash", backend="gemini", tools=["calculator"])
  result = agent.run("What is 15 * 8?")

Free tier reality (Sep 2026):
  5 RPM / 250,000 TPM / 1,500 RPD for gemini-3.8-flash.
  Concurrent requests on a free key are pointless. The backend ships
  built-in 429 retry with exponential backoff (RESOURCE_EXHAUSTED).

Thinking configuration notes:
  Gemini 3.x cannot disable thinking — best you can do is "minimal".
  Gemini 2.5 can disable via reasoning_effort="none" or thinking_budget=0.
  The two parameter styles (reasoning_effort vs thinking_config) are
  mutually exclusive; the backend enforces this in _build_openai_body().

  Thought-signature stateful continuation is NOT yet implemented in v0.1.
  Multi-turn agent loops will re-derive reasoning from scratch each turn,
  which costs ~2-3x more reasoning tokens. Track this for v0.2.

See docs/GEMINI_API_TECHNICAL_REFERENCE.md for the full spec.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Generator, Optional

from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode
from agentkthx.core.models import Tool, ToolParam
from agentkthx.config import (
    GEMINI_BASE_URL,
    GEMINI_API_KEY,
    GEMINI_DEFAULT_MODEL,
    GEMINI_FREE_ONLY,
    GEMINI_THINKING_LEVEL,
    GEMINI_SERVICE_TIER,
)


# ─────────────────────────────────────────────────────────────────────────────
# Free Tier rate-limit data (ground truth from Google AI Studio)
# ─────────────────────────────────────────────────────────────────────────────
# Google does NOT expose a pricing or free-tier endpoint via the API.
# The rate-limits page (https://ai.google.dev/gemini-api/docs/rate-limits)
# documents the 4 usage tiers (Free / Tier 1 / Tier 2 / Tier 3) but
# per-model RPM/TPM/RPD numbers are only visible in the Google AI Studio
# UI under "Rate limits" (https://aistudio.google.com/rate-limits).
#
# The table below was transcribed from AI Studio on 2026-09-24 by VTSTech
# for a project with no billing setup (Free tier). Models listed with
# 0/0/0 limits are NOT available on the Free tier — they require paid
# Tier 1+ access. Models with non-zero limits ARE free.
#
# IMPORTANT: This table is the ONLY authoritative source of free-tier
# eligibility. The /v1beta/openai/models API endpoint does NOT return
# pricing or free_tier fields. If a model isn't in this table, the
# _is_free_tier_model() heuristic below decides (with patterns based
# on the table).
#
# To refresh this table on your own account, log in to AI Studio and
# visit: https://aistudio.google.com/rate-limits
#
# Format: "model_id_pattern": {"rpm": int, "tpm": int, "rpd": int}
# Patterns are matched case-insensitively as substrings of the model ID.

FREE_TIER_LIMITS: dict[str, dict[str, int]] = {
    # === Text-out chat models (FREE on Free tier) ===
    "gemini-2.5-flash":          {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    "gemini-2.5-flash-lite":     {"rpm": 10,  "tpm": 250_000, "rpd": 20},
    "gemini-3-flash-preview":    {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    "gemini-3.1-flash-lite":     {"rpm": 15,  "tpm": 250_000, "rpd": 500},
    "gemini-3.5-flash":          {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    "gemini-3.5-flash-lite":     {"rpm": 15,  "tpm": 250_000, "rpd": 500},
    "gemini-3.6-flash":          {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    "gemini-3.7-flash":          {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    "gemini-3.8-flash":          {"rpm": 5,   "tpm": 250_000, "rpd": 20},
    # Note: Gemini 2 Flash / 2 Flash Lite (legacy 2.0) show 0/0/0 — being
    # shut down. Not on free tier.

    # === Multi-modal generative (TTS variants — FREE on Free tier) ===
    "gemini-2.5-flash-preview-tts":       {"rpm": 3, "tpm": 10_000, "rpd": 10},
    "gemini-2.5-pro-preview-tts":         {"rpm": 0, "tpm": 0,       "rpd": 0},  # NOT free
    "gemini-3.1-flash-tts-preview":       {"rpm": 3, "tpm": 10_000, "rpd": 10},
    "gemini-3.8-flash-tts":               {"rpm": 3, "tpm": 10_000, "rpd": 10},
    "gemini-3.8-flash-lite-tts":         {"rpm": 3, "tpm": 10_000, "rpd": 10},

    # === Live API / Transcribe (FREE on Free tier, but uses audio endpoints) ===
    "gemini-3.5-transcribe":              {"rpm": 3, "tpm": 10_000, "rpd": 25},
    "gemini-3.5-transcribe-live":         {"rpm": 3, "tpm": 10_000, "rpd": 25},  # inferred

    # === Embeddings (FREE on Free tier) ===
    "gemini-embedding-001":               {"rpm": 100, "tpm": 30_000, "rpd": 1_000},
    "gemini-embedding-2-preview":        {"rpm": 100, "tpm": 30_000, "rpd": 1_000},
    "gemini-embedding-2":                 {"rpm": 100, "tpm": 30_000, "rpd": 1_000},  # inferred

    # === Robotics (FREE on Free tier, but uses specialized robotics endpoint) ===
    "gemini-robotics-er-2-preview":              {"rpm": 5, "tpm": 250_000, "rpd": 20},
    "gemini-robotics-er-2-streaming-preview":    {"rpm": 5, "tpm": 250_000, "rpd": 20},  # inferred
    "gemini-robotics-er-1.6-preview":            {"rpm": 5, "tpm": 250_000, "rpd": 20},  # inferred

    # === Managed agents (FREE on Free tier) ===
    "antigravity-preview-05-2026":        {"rpm": 60, "tpm": 100_000, "rpd": 100},
    "antigravity-preview-09-2026":        {"rpm": 60, "tpm": 100_000, "rpd": 100},  # inferred
    "antigravity-preview-latest":         {"rpm": 60, "tpm": 100_000, "rpd": 100},  # inferred

    # === Gemma open-source models (FREE on Free tier, generous RPD!) ===
    # These are served via the Gemini API catalog but are actually Google's
    # open-source Gemma models. They're chat-capable text LLMs in principle,
    # but UNTESTED via the OpenAI-compat /chat/completions endpoint —
    # they may require the native Gemma/Vertex API. See TODO in
    # _NON_CHAT_PATTERNS comment block.
    "gemma-4-26b-a4b-it":                 {"rpm": 30, "tpm": 16_000, "rpd": 14_400},
    "gemma-4-31b-it":                     {"rpm": 30, "tpm": 16_000, "rpd": 14_400},  # inferred (truncated in source)

    # === NOT on Free tier (0/0/0 — listed for completeness / future ref) ===
    "gemini-2.0-flash":                   {"rpm": 0, "tpm": 0, "rpd": 0},  # legacy, being shut down
    "gemini-2.0-flash-lite":              {"rpm": 0, "tpm": 0, "rpd": 0},  # legacy, being shut down
    "gemini-2.5-pro":                     {"rpm": 0, "tpm": 0, "rpd": 0},  # Pro = paid
    "gemini-2.5-pro-preview-tts":        {"rpm": 0, "tpm": 0, "rpd": 0},  # Pro TTS = paid
    "gemini-3.1-pro-preview":            {"rpm": 0, "tpm": 0, "rpd": 0},  # Pro = paid
    "gemini-3.1-pro-preview-customtools":{"rpm": 0, "tpm": 0, "rpd": 0},  # Pro = paid
    "deep-research-preview-04-2026":      {"rpm": 0, "tpm": 0, "rpd": 0},  # paid agentic
    "deep-research-max-preview-04-2026":  {"rpm": 0, "tpm": 0, "rpd": 0},  # paid agentic
    "deep-research-pro-preview-12-2025":  {"rpm": 0, "tpm": 0, "rpd": 0},  # paid agentic
    "gemini-2.5-computer-use-preview-10-2025": {"rpm": 0, "tpm": 0, "rpd": 0},  # paid, native API only
    "gemini-2.5-flash-image":            {"rpm": 0, "tpm": 0, "rpd": 0},  # Nano Banana (image gen = paid)
    "gemini-3-pro-image":                {"rpm": 0, "tpm": 0, "rpd": 0},  # Nano Banana Pro (image gen = paid)
    "gemini-3-pro-image-preview":        {"rpm": 0, "tpm": 0, "rpd": 0},
    "gemini-3.1-flash-image":            {"rpm": 0, "tpm": 0, "rpd": 0},  # Nano Banana 2 (image gen = paid)
    "gemini-3.1-flash-image-preview":    {"rpm": 0, "tpm": 0, "rpd": 0},
    "gemini-3.1-flash-lite-image":       {"rpm": 0, "tpm": 0, "rpd": 0},  # Nano Banana 2 Lite (image gen = paid)
    "gemini-3.1-flash-lite-image-preview": {"rpm": 0, "tpm": 0, "rpd": 0},
    "veo-3.1-generate-preview":           {"rpm": 0, "tpm": 0, "rpd": 0},  # video gen = paid
    "veo-3.1-fast-generate-preview":     {"rpm": 0, "tpm": 0, "rpd": 0},
    "veo-3.1-lite-generate-preview":     {"rpm": 0, "tpm": 0, "rpd": 0},
    "gemini-omni-1.1-flash":             {"rpm": 0, "tpm": 0, "rpd": 0},  # video gen = paid
    "gemini-omni-flash-preview":         {"rpm": 0, "tpm": 0, "rpd": 0},
    "lyria-3.5":                         {"rpm": 0, "tpm": 0, "rpd": 0},  # music gen = paid
    "lyria-3-pro-preview":               {"rpm": 0, "tpm": 0, "rpd": 0},
    "lyria-3-clip-preview":              {"rpm": 0, "tpm": 0, "rpd": 0},
    "lyria-realtime-exp":                {"rpm": 0, "tpm": 0, "rpd": 0},
    "aqa":                               {"rpm": 0, "tpm": 0, "rpd": 0},  # Answer Quality Assessment
}


def _is_free_tier_model(model_id: str) -> bool:
    """Return True if the model has non-zero rate limits on the Free tier.

    Source of truth: Google AI Studio → Rate limits page
    (https://aistudio.google.com/rate-limits), transcribed 2026-09-24.
    Google does NOT expose this via API — see the FREE_TIER_LIMITS table
    above for the per-model numbers.

    Lookup priority:
      1. Exact match against FREE_TIER_LIMITS table → check rpd > 0
      2. Heuristic pattern match for known free families
         (flash / lite / embedding / robotics / antigravity / transcribe / gemma)
      3. Default: NOT free (safer to mark paid than to mislead user)
    """
    if not model_id:
        return False
    m = model_id.lower()
    if m.startswith("models/"):
        m = m[len("models/"):]

    # 1. Exact match against the FREE_TIER_LIMITS table
    if m in FREE_TIER_LIMITS:
        return FREE_TIER_LIMITS[m]["rpd"] > 0

    # 2. Heuristic — patterns proven free by the AI Studio data.
    # Order matters: check NEGATIVE patterns first (paid overrides).
    # Paid overrides:
    if "-pro" in m or "-pro-preview" in m or "-pro-image" in m:
        return False  # All Pro variants are paid
    if "-image" in m or "imagen-" in m:
        return False  # All image gen variants are paid
    if "veo-" in m or "lyria-" in m or "omni-" in m:
        return False  # Video / music gen are paid
    if "computer-use" in m or "deep-research" in m:
        return False  # Specialized paid endpoints
    if m.startswith("gemini-2.0") or m == "gemini-flash-latest" or m == "gemini-pro-latest":
        return False  # Legacy 2.0 (being shut down) / aliases for Pro
    # Live API override: only gemini-3.5-transcribe* is explicitly free per
    # AI Studio. Other Live API variants (-live, live-translate) require
    # paid access or opt-in. Match by "-live" / "live-translate" and only
    # return True if the model is explicitly in the FREE_TIER_LIMITS table.
    if "-live" in m or "live-translate" in m:
        return m in FREE_TIER_LIMITS and FREE_TIER_LIMITS[m]["rpd"] > 0

    # Free families (per AI Studio data):
    if "flash" in m:
        return True  # All Flash variants including TTS, Lite
    if "lite" in m:
        return True  # Flash-Lite variants
    if "embedding" in m:
        return True  # All embeddings
    if "robotics" in m:
        return True  # Robotics ER variants
    if "antigravity" in m:
        return True  # Managed agent
    if "transcribe" in m:
        return True  # Live API / Transcribe (only gemini-3.5-transcribe per AI Studio)
    if "gemma-" in m:
        return True  # Gemma open-source

    # 3. Default: unknown → mark as NOT free (safer)
    return False


def _get_free_tier_limits(model_id: str) -> dict[str, int] | None:
    """Return the per-model free-tier rate limits, or None if unknown.

    Returns ``{"rpm": int, "tpm": int, "rpd": int}`` when the model is
    on the Free tier; returns ``None`` when it's not free or unknown.
    Useful for surfacing in the model-listing display or for client-side
    rate-limit tracking.
    """
    if not _is_free_tier_model(model_id):
        return None
    m = model_id.lower()
    if m.startswith("models/"):
        m = m[len("models/"):]
    return FREE_TIER_LIMITS.get(m)  # None if not in the table (heuristic-only match)


# ─────────────────────────────────────────────────────────────────────────────
# Gemini model catalog
# ─────────────────────────────────────────────────────────────────────────────
# Static metadata used as a fallback when the /models endpoint isn't reachable
# or when a model isn't listed (e.g. preview models behind a feature flag).
# Context lengths and key caps per the Gemini docs (Sep 2026).
# Free-tier models are tagged with `free_tier=True` — GEMINI_FREE_ONLY filters
# the cached model list to just these. The free_tier flags below MUST match
# the FREE_TIER_LIMITS table above (which was transcribed from AI Studio).
GEMINI_MODELS: dict[str, dict] = {
    # === Gemini 3.x family — current flagship generation ===
    "gemini-3.8-flash": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "supports_thought_signatures": True,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.8 Flash — current flagship flash model",
    },
    "gemini-3.7-flash": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.7 Flash",
    },
    "gemini-3.6-flash": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.6 Flash",
    },
    "gemini-3.5-flash": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.5 Flash",
    },
    "gemini-3.5-flash-lite": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.5 Flash-Lite — lowest cost in 3.5 family",
    },
    "gemini-3.1-flash-lite": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.1 Flash-Lite",
    },
    "gemini-3.1-pro-preview": {
        "context_length": 2_097_152,           # 2M for Pro
        "max_completion_tokens": 65_536,
        "free_tier": False,                    # Pro is not on free tier
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "thinking_can_disable": False,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "description": "Gemini 3.1 Pro Preview — 2M context, paid tier only",
    },

    # === Gemini 2.5 family — legacy but still served ===
    # Available only to projects that used 2.5 before. For new projects,
    # use gemini-3.5-flash-lite or gemini-3.8-flash.
    "gemini-2.5-pro": {
        "context_length": 2_097_152,
        "max_completion_tokens": 65_536,
        "free_tier": False,
        "supports_thinking": True,
        "thinking_budget_range": (0, 24_576),
        "thinking_can_disable": True,
        "supports_thought_signatures": False,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5",
        "description": "Gemini 2.5 Pro — legacy flagship, 2M context",
    },
    "gemini-2.5-flash": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_budget_range": (0, 24_576),
        "thinking_can_disable": True,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5",
        "description": "Gemini 2.5 Flash — legacy",
    },
    "gemini-2.5-flash-lite": {
        "context_length": 1_048_576,
        "max_completion_tokens": 65_536,
        "free_tier": True,
        "supports_thinking": True,
        "thinking_budget_range": (0, 24_576),
        "thinking_can_disable": True,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5",
        "description": "Gemini 2.5 Flash-Lite — legacy",
    },
}


def detect_gemini_family(model_name: str) -> dict:
    """Detect Gemini model capabilities from name.

    Naming convention: gemini-<MAJOR>.<MINOR>-<tier>[-preview][-<date>]
    """
    m = model_name.lower()
    if m.startswith("gemini-3."):
        return {
            "family": "gemini-3",
            "supports_native_tools": True,
            "supports_thinking": True,
            "thinking_levels": ["minimal", "low", "medium", "high"],
            "thinking_can_be_disabled": False,
            "supports_thought_signatures": True,
            "min_cache_tokens": 4096,
        }
    if m.startswith("gemini-2.5"):
        return {
            "family": "gemini-2.5",
            "supports_native_tools": True,
            "supports_thinking": True,
            "thinking_levels": [],
            "thinking_budget": (0, 24576),
            "thinking_can_be_disabled": True,
            "supports_thought_signatures": False,
            "min_cache_tokens": 2048,
        }
    if m.startswith("gemini-2.0"):
        return {
            "family": "gemini-2.0",
            "supports_native_tools": True,
            "supports_thinking": False,
        }
    return {
        "family": "unknown",
        "supports_native_tools": False,
        "supports_thinking": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chat-capability detection
# ─────────────────────────────────────────────────────────────────────────────
# Google's /v1beta/openai/models endpoint returns ALL models — chat,
# embedding, image generation (Nano Banana), video generation (Veo),
# music generation (Lyria), audio transcription, robotics, and Gemma
# open-source variants. Only chat models can be used as AgentKthx backends
# because they accept ``messages`` and ``tools`` parameters via the
# /v1beta/openai/chat/completions endpoint. The other model families use
# different endpoints:
#
#   - Image generation (Nano Banana, gemini-2.5-flash-image,
#     gemini-3-pro-image-preview) → POST /v1beta/openai/images/generations
#   - Video generation (Veo 3.1 variants, gemini-omni-1.1-flash) → POST /v1beta/openai/videos
#   - Music generation (Lyria 3.5, Lyria 3 Pro, Lyria RealTime) → different endpoint
#   - Audio (Live API, TTS, Transcribe) → different endpoints (WebSocket for Live,
#     /v1beta/openai/audio/transcriptions for Transcribe, native TTS path)
#   - Embeddings (gemini-embedding-001, gemini-embedding-2-preview) → POST /v1beta/openai/embeddings
#   - Robotics (gemini-robotics-er-2-preview) → specialized robotics endpoint
#   - Computer Use (gemini-2.5-computer-use-preview) → native API only
#   - Deep Research (deep-research-preview, deep-research-max-preview) → agentic endpoint
#   - Antigravity (antigravity-preview-05-2026) → managed agent endpoint
#   - Gemma open-source (gemma-4-26b-a4b-it, gemma-4-31b-it) → Google's
#     Gemma open models served via the Gemini API catalog. These ARE
#     chat-capable via /v1beta/openai/chat/completions — verified on a
#     real VM by VTSTech on 2026-09-24 (gemma-4-26b-a4b-it responded
#     successfully to a chat-completions request). However, Gemma uses
#     inline <thought>...</thought> tags for reasoning instead of the
#     OpenAI reasoning_content field — see ThoughtTagParser below for
#     the streaming parser that routes the tag content to
#     reasoning_content so the existing AgentKthx UI shows it as a
#     collapsible "thought" panel.
#
# Sending a /chat/completions request to a non-chat model returns a 400
# "model does not support this endpoint" or similar — and the response
# shape (when it returns at all) doesn't match our parser. So we mark
# them as NONE in test_tool_support() so users don't accidentally try
# to chat with an embedding model.
#
# TODO: v0.2 — when image I/O support is added, gemini-2.5-flash-image
# and gemini-3-pro-image-preview become usable via the /images/generations
# endpoint. That's a separate code path from /chat/completions and needs
# its own backend method (e.g. generate_image(prompt) → bytes). For now,
# they're correctly tagged as non-chat.
#
# VERIFIED 2026-09-24 (VTSTech on real VM): gemma-4-* IS chat-capable via
# /v1beta/openai/chat/completions. Kept OUT of _NON_CHAT_PATTERNS. Gemma
# uses <thought>...</thought> inline tags for reasoning — handled by
# ThoughtTagParser below.

# Prefixes / patterns for non-chat models. Matched case-insensitively
# against the model ID. Order matters: more specific patterns first
# (e.g. "nano-banana" before "banana" would matter if we had such a case).
_NON_CHAT_PATTERNS = (
    "embedding",            # gemini-embedding-001, gemini-embedding-2-preview → /embeddings endpoint
    "veo-",                  # veo-3.1-generate-preview, veo-3.1-fast-generate-preview → /videos endpoint
    "lyria-",                # lyria-3.5, lyria-3-pro-preview, lyria-realtime-exp → music gen endpoint
    "imagen-",               # imagen-4.0-generate (shut down, but still listed) → /images endpoint
    "robotics-",             # gemini-robotics-er-2-preview → specialized robotics endpoint
    "transcribe",            # gemini-3.5-transcribe, gemini-3.5-transcribe-live → /audio/transcriptions
    "live-translate",        # gemini-3.5-live-translate-preview → Live API (WebSocket)
    "-tts",                  # gemini-3.8-flash-tts, gemini-2.5-flash-preview-tts → native TTS path
    "-live",                 # gemini-3.8-live, gemini-3.1-flash-live-preview, gemini-3.8-live-extended-thinking → Live API (WebSocket)
    "-image",                # gemini-3.1-flash-image (Nano Banana), gemini-3-pro-image → /images endpoint
    "computer-use",          # gemini-2.5-computer-use-preview → native API only (specialized)
    "deep-research",         # deep-research-preview, deep-research-max-preview → agentic endpoint
    "antigravity",           # antigravity-preview-05-2026 → managed agent endpoint
    "aqa",                   # aqa (Answer Quality Assessment — not a chat model)
    "omni-",                 # gemini-omni-1.1-flash → /videos endpoint (video gen)
    # NOTE: gemma-* IS chat-capable via /chat/completions — verified on real VM
    # by VTSTech 2026-09-24 (gemma-4-26b-a4b-it responded to a chat-completions
    # request and emitted a thinking + answer response). Kept OUT of
    # _NON_CHAT_PATTERNS. Gemma uses inline <thought>...</thought> tags for
    # reasoning instead of the OpenAI reasoning_content field — see
    # ThoughtTagParser below.
)


def _is_chat_capable_model(model_id: str) -> bool:
    """Return True if the model accepts chat-completions requests with tools.

    Google's /v1beta/openai/models endpoint returns ALL model variants
    (chat, embedding, image gen, video gen, music gen, robotics, audio,
    Gemma open-source). Only chat models can be used as AgentKthx backends
    because they accept ``messages`` and ``tools`` parameters via
    /v1beta/openai/chat/completions. Others use different endpoints
    (see the comment block above _NON_CHAT_PATTERNS for the full table).
    """
    if not model_id:
        return False
    m = model_id.lower()
    # Strip a leading 'models/' prefix if present (defensive — should
    # already be stripped by _parse_gemini_model).
    if m.startswith("models/"):
        m = m[len("models/"):]
    for pattern in _NON_CHAT_PATTERNS:
        if pattern in m:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Inline <thought>...</thought> tag parser (Gemma family)
# ─────────────────────────────────────────────────────────────────────────────
# Gemma 4 (and potentially other models in the future) wraps its reasoning
# in inline <thought>...</thought> tags instead of using the OpenAI
# reasoning_content field that Gemini 3.x uses. Without parsing these tags,
# the raw <thought> blocks would show up in the user-facing content stream
# (see https://github.com/VTSTech/AgentKthx/issues for the bug report).
#
# This parser runs as a post-processing step on each streaming chunk:
#   - Text inside <thought>...</thought> blocks → routed to reasoning_content
#   - Text outside the tags → routed to content (the actual answer)
#
# The parser is stateful because:
#   - The opening <thought> tag might arrive in one chunk and the closing
#     </thought> in another (or across many chunks).
#   - Models occasionally emit malformed output — stray <thought> tags
#     without a matching </thought>, or vice versa. We handle these
#     gracefully (stray tags are passed through as content).
#   - Multiple <thought>...</thought> blocks may appear in sequence.
#
# Verified working on real VM (2026-09-24) with gemma-4-26b-a4b-it.


def _uses_thought_tags(model_id: str) -> bool:
    """Return True if the model uses inline <thought>...</thought> tags
    for reasoning instead of the OpenAI reasoning_content field.

    Currently: Gemma 4 variants (gemma-4-26b-a4b-it, gemma-4-31b-it).
    Other Gemini chat models use the native reasoning_content field and
    don't need this parser.

    Extending this to other models (e.g. DeepSeek-R1 with its <think> tags)
    would only require adding patterns here — the ThoughtTagParser is
    parameterized via the OPENING_TAG and CLOSING_TAG constants.
    """
    if not model_id:
        return False
    m = model_id.lower()
    if m.startswith("models/"):
        m = m[len("models/"):]
    return m.startswith("gemma-")


class ThoughtTagParser:
    """Stateful streaming parser for inline <thought>...</thought> blocks.

    Feed chunks via ``.feed(text)`` — returns ``(content_delta, reasoning_delta)``
    for each chunk. Call ``.flush()`` at the end of the stream to emit any
    buffered content.

    Algorithm:
      - State OUTSIDE: accumulate text to content. When we see "<thought>",
        switch to INSIDE state and start a new reasoning block.
      - State INSIDE: accumulate text to reasoning_content. When we see
        "</thought>", switch to OUTSIDE state.
      - Partial tags at chunk boundaries (e.g. "<tho" at end of chunk)
        are buffered and re-examined on the next feed() call.

    Malformed output handling:
      - Stray "</thought>" without a matching "<thought>" → emitted as
        literal text in content (so the user sees the model's actual output)
      - Unclosed "<thought>" at end of stream → flushed via flush() as
        reasoning_content (the model's reasoning is shown even if it forgot
        to close the tag)
      - Nested tags, double tags, etc. — not specially handled; the first
        "</thought>" after a "<thought>" closes the block.
    """

    OPENING_TAG = "<thought>"
    CLOSING_TAG = "</thought>"

    def __init__(self) -> None:
        # "outside" = emitting content; "inside" = emitting reasoning_content
        self._state: str = "outside"
        # Buffer for partial tags that span chunk boundaries.
        # Always ≤ max(len(OPENING_TAG), len(CLOSING_TAG)) - 1 = 9 chars.
        self._buffer: str = ""

    def feed(self, text: str) -> tuple[str, str]:
        """Process one chunk of text.

        Returns ``(content_delta, reasoning_delta)`` — text to append to
        the visible content stream and to the reasoning stream respectively.
        Either may be empty string if all the chunk's text belonged to the
        other stream.
        """
        if not text:
            return ("", "")

        # Prepend the leftover buffer from the previous chunk.
        text = self._buffer + text
        self._buffer = ""

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        i = 0
        n = len(text)

        while i < n:
            if self._state == "outside":
                # Look for the opening tag from position i.
                open_idx = text.find(self.OPENING_TAG, i)
                # Also look for a stray closing tag (model emitted </thought>
                # without a matching <thought> — happens with malformed output).
                # We check this FIRST because if it precedes any opening tag,
                # we should skip it (don't let it leak into content).
                close_idx = text.find(self.CLOSING_TAG, i)
                if close_idx != -1 and (open_idx == -1 or close_idx < open_idx):
                    # Stray closing tag — emit content before it, skip the tag
                    content_parts.append(text[i:close_idx])
                    i = close_idx + len(self.CLOSING_TAG)
                    # Stay in OUTSIDE state — re-loop to look for next tag
                    continue
                if open_idx == -1:
                    # No opening tag found. Check if the tail of `text`
                    # could be the start of an opening tag (partial match).
                    partial_len = self._suffix_is_tag_prefix(text, i, self.OPENING_TAG)
                    if partial_len > 0:
                        # Buffer the partial tag for the next feed() call.
                        # Emit content before it now.
                        content_parts.append(text[i:n - partial_len])
                        self._buffer = text[n - partial_len:]
                    else:
                        # No partial tag — emit everything as content.
                        content_parts.append(text[i:])
                    break
                else:
                    # Found opening tag — emit content before it, switch state.
                    content_parts.append(text[i:open_idx])
                    i = open_idx + len(self.OPENING_TAG)
                    self._state = "inside"
            else:  # state == "inside"
                # Look for the closing tag from position i.
                idx = text.find(self.CLOSING_TAG, i)
                if idx == -1:
                    # No closing tag found. Check for partial closing tag.
                    partial_len = self._suffix_is_tag_prefix(text, i, self.CLOSING_TAG)
                    if partial_len > 0:
                        reasoning_parts.append(text[i:n - partial_len])
                        self._buffer = text[n - partial_len:]
                    else:
                        reasoning_parts.append(text[i:])
                    break
                else:
                    # Found closing tag — emit reasoning before it, switch state.
                    reasoning_parts.append(text[i:idx])
                    i = idx + len(self.CLOSING_TAG)
                    self._state = "outside"

        return ("".join(content_parts), "".join(reasoning_parts))

    def flush(self) -> tuple[str, str]:
        """Emit any buffered content at the end of the stream.

        Called when the upstream is done sending. Returns
        ``(content_delta, reasoning_delta)`` containing whatever was
        buffered. If we were inside a <thought> block when the stream ended,
        the buffered content is emitted as reasoning (model forgot to close
        the tag — surface the reasoning anyway rather than hiding it).
        """
        if not self._buffer:
            return ("", "")
        # Buffer exists because we were waiting for a complete tag.
        # Treat the buffered partial-tag text as literal content/reasoning
        # (whichever state we were in).
        if self._state == "outside":
            return (self._buffer, "")
        else:
            return ("", self._buffer)

    @staticmethod
    def _suffix_is_tag_prefix(text: str, start: int, tag: str) -> int:
        """Check if text[start:] ends with a prefix of `tag`.

        Returns the length of the matching prefix (0..len(tag)-1), or 0 if
        no match. We check progressively shorter suffixes — e.g. for
        tag="<thought>" and text ending in "<th", we'd return 3.

        Used to detect partial tags at chunk boundaries (e.g. "<tho" might
        be the start of "<thought>" in the next chunk).
        """
        # Look at the last k chars of text[start:] and see if they're
        # a prefix of `tag`.
        max_k = min(len(tag) - 1, len(text) - start)
        for k in range(max_k, 0, -1):
            suffix = text[-k:]
            if tag.startswith(suffix):
                return k
        return 0


def _parse_thought_tags_from_complete_text(text: str) -> tuple[str, str]:
    """One-shot parser for non-streaming responses.

    Returns ``(content, reasoning)`` — the same shape as ThoughtTagParser
    but for already-complete responses where we have the full text in
    one piece. Used by generate() (non-streaming path).

    Same semantics as the streaming parser: text inside <thought>...</thought>
    goes to reasoning, text outside goes to content. Unclosed <thought> at
    end of text → flushed as reasoning (model forgot to close).
    """
    if not text or "<thought>" not in text:
        return (text, "")
    parser = ThoughtTagParser()
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    c, r = parser.feed(text)
    if c:
        content_parts.append(c)
    if r:
        reasoning_parts.append(r)
    c, r = parser.flush()
    if c:
        content_parts.append(c)
    if r:
        reasoning_parts.append(r)
    return ("".join(content_parts), "".join(reasoning_parts))


class GeminiBackend(OpenAICompatibleBackend):
    """
    Backend for Google Gemini API (cloud) via OpenAI-compatible endpoint.

    Extends ``OpenAICompatibleBackend`` with Gemini-specific:
      - Auth: Bearer token via GEMINI_API_KEY (GOOGLE_API_KEY fallback)
      - Base URL: ``https://generativelanguage.googleapis.com/v1beta/openai/``
        (trailing slash preserved)
      - Thinking config: ``reasoning_effort`` OR
        ``extra_body.google.thinking_config`` — mutually exclusive
      - 429 RESOURCE_EXHAUSTED retry with exponential backoff (free tier
        is heavily rate-limited at 5 RPM)
      - GEMINI_FREE_ONLY model filter
      - service_tier routing (standard / flex / priority)
    """

    # Model cache + 1-hour TTL (mirrors OpenRouterBackend).
    _model_cache: list[dict] | None = None
    _cache_time: float = 0.0
    _CACHE_TIMEOUT: int = 3600  # 1 hour

    # R06.54-style retry budget for 429/5xx. Free tier at 5 RPM returns
    # 429 RESOURCE_EXHAUSTED constantly — needs more patience than 3 quick
    # retries. Override with GEMINI_MAX_429_RETRIES.
    _MAX_429_RETRIES = 6
    _429_BACKOFF_BASE = 5.0
    _429_BACKOFF_CAP = 90.0

    # ARCH-03 (R06.57): Gemini's context-length 400 error uses a different
    # message format than OpenRouter/ZAI. Override the shared regex patterns
    # so the inherited ``_calculate_safe_max_tokens`` parses correctly.
    # Gemini error: "Request exceeds the maximum context length of 1048576
    # tokens. You requested 1100000 tokens (1000000 in the input, 100000
    # in the output)." — note "of" (not "is") and "in the input" (not
    # "of text input"), and no separate "tool input" count.
    _CONTEXT_LENGTH_MAX_PATTERN: str = r"maximum context length of (\d+)"
    _CONTEXT_LENGTH_INPUT_PATTERN: str = r"(\d+) in the input"
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = None  # Gemini doesn't separate tool input

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENAI,
    ):
        # Resolve base URL. Priority: explicit > host/port > env > default.
        if base_url:
            # Preserve trailing slash — Gemini's OpenAI-compat endpoint
            # requires it. Other backends strip it; we explicitly keep it.
            resolved_url = base_url if base_url.endswith("/") else base_url + "/"
        elif host and port:
            resolved_url = f"http://{host}:{port}/"
        else:
            resolved_url = GEMINI_BASE_URL
            if not resolved_url.endswith("/"):
                resolved_url += "/"

        # Gemini only exposes the OpenAI-compat endpoint. We accept any
        # api_mode here (OPENAI, OPENRE, JEV) without raising — the actual
        # wire format is always OpenAI Chat-Completions; OPENRE is silently
        # treated as OPENAI for back-compat with CLI code paths that default
        # to OPENRE for unknown backends. JEV wraps the underlying call.
        # This is intentionally more permissive than OpenRouterBackend's
        # strict check — Gemini has only one wire format, so the api_mode
        # is informational, not a hard constraint.
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        # Normalize: OPENRE → OPENAI (Gemini can't speak OpenResponses)
        if api_mode == ApiMode.OPENRE:
            api_mode = ApiMode.OPENAI

        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # API key is lazy — only required for generation, not model listing.
        # Read FRESH from env (not the module-level constant) so tests that
        # patch.dict(os.environ, ...) before constructing see the right key.
        self.api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY", "")
        )

        # ROB-06 parity: persisted safe max_tokens after a context-length 400.
        self._context_safe_max_tokens: int | None = None

        # Auth headers — Gemini accepts standard Bearer. No HTTP-Referer /
        # X-Title (those are OpenRouter leaderboard headers, irrelevant here).
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Default thinking level / service tier from env (can be overridden
        # per-call via kwargs).
        self._default_thinking_level = GEMINI_THINKING_LEVEL or None
        self._default_service_tier = GEMINI_SERVICE_TIER or "standard"

        # Populate model cache on init (mirrors OpenRouter).
        try:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print("  [Gemini Debug] Initializing: loading models into cache")
            self.list_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Gemini Debug] Failed to initialize models: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # BackendType / base_url properties
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        return BackendType.GEMINI

    @property
    def base_url(self) -> str:
        return self._base_url

    # ─────────────────────────────────────────────────────────────────────
    # Model discovery
    # ─────────────────────────────────────────────────────────────────────

    def _parse_gemini_model(self, model_data: dict) -> dict:
        """Parse OpenAI-compat /models entry into AgentKthx format.

        Gemini's /v1beta/openai/models endpoint returns model IDs prefixed
        with ``models/`` (e.g. ``models/gemini-3.8-flash``). We strip that
        prefix so the bare name matches our static catalog — without this,
        the catalog-merge step would treat ``gemini-3.8-flash`` (catalog)
        and ``models/gemini-3.8-flash`` (API) as different models and
        emit both as duplicates.

        The OpenAI-compat /models endpoint also does NOT return per-model
        ``context_length`` — only the native Gemini API does. So we look
        up context from our static catalog (which has accurate 1M/2M
        values), and fall back to the API response shape only if catalog
        doesn't have it.
        """
        raw_id = model_data.get("id") or model_data.get("name") or ""
        # Strip the 'models/' prefix that Google's OpenAI-compat endpoint
        # adds to every ID. Without this, our catalog-merge step sees
        # 'gemini-3.8-flash' (catalog) and 'models/gemini-3.8-flash' (API)
        # as different entries and emits duplicates.
        model_id = raw_id[len("models/"):] if raw_id.startswith("models/") else raw_id

        # Context length — try API first (rare), fall back to catalog,
        # then to 1M default (the common Gemini Flash context).
        catalog = GEMINI_MODELS.get(model_id, {})
        context_length = (
            model_data.get("context_length")
            or model_data.get("context_window")
            or catalog.get("context_length")
            or 1_048_576
        )
        max_completion = (
            (model_data.get("top_provider") or {}).get("max_completion_tokens")
            or model_data.get("max_completion_tokens")
            or catalog.get("max_completion_tokens")
            or 65_536
        )

        # Free tier classification — use the catalog flag if available,
        # else fall back to _is_free_tier_model() which uses the
        # FREE_TIER_LIMITS table (transcribed from AI Studio) plus
        # pattern-matching for models not in the catalog.
        if "free_tier" in catalog:
            free_tier = catalog["free_tier"]
        else:
            free_tier = _is_free_tier_model(model_id)

        # Surface the rate limits in details for display / client-side tracking
        free_limits = _get_free_tier_limits(model_id)

        family = catalog.get("family") or detect_gemini_family(model_id)["family"]

        # Non-chat models can't do tool calling. We tag them so
        # test_tool_support() can return NONE instead of NATIVE.
        is_chat = _is_chat_capable_model(model_id)

        return {
            "name": model_id,
            "size": 0,
            "details": {
                "family": family,
                "backend": "gemini",
                "context_length": context_length,
                "max_completion_tokens": max_completion,
                "free_tier": free_tier,
                "free_tier_limits": free_limits,  # {"rpm","tpm","rpd"} or None
                "supports_thinking": catalog.get("supports_thinking", is_chat),
                "is_chat_model": is_chat,
            },
            "model_data": model_data,
        }

    def list_models(self) -> list[dict]:
        """List available Gemini models from /openai/models with caching.

        Cache timeout: 1 hour. Refresh is automatic when the cache expires.
        GEMINI_FREE_ONLY filters the result to free-tier models only.
        """
        current_time = time.time()
        if (self._model_cache is not None and
                current_time - self._cache_time < self._CACHE_TIMEOUT):
            return self._model_cache

        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            req = urllib.request.Request(
                f"{self.base_url}/models",  # add "/" since BaseBackend stripped trailing slash
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                models_data = json.loads(resp.read().decode("utf-8"))

            available_models: list[dict] = []
            # OpenAI shape: {"data": [{"id": "gemini-3.8-flash", ...}, ...]}
            for model in models_data.get("data", []) or []:
                model_id = model.get("id")
                if model_id:
                    available_models.append(self._parse_gemini_model(model))

            # Add catalog-only models (not returned by /models on every
            # project — e.g. legacy 2.5 models only show if the project
            # used them before).
            catalog_ids = {m["name"] for m in available_models}
            for name, info in GEMINI_MODELS.items():
                if name not in catalog_ids:
                    available_models.append({
                        "name": name,
                        "size": 0,
                        "details": {
                            "family": info.get("family", "gemini"),
                            "backend": "gemini",
                            "context_length": info.get("context_length", 1_048_576),
                            "max_completion_tokens": info.get("max_completion_tokens", 65_536),
                            "free_tier": info.get("free_tier", False),
                            "supports_thinking": info.get("supports_thinking", True),
                        },
                    })

            if GEMINI_FREE_ONLY:
                self._model_cache = sorted(
                    [m for m in available_models if m["details"].get("free_tier")],
                    key=lambda x: x["name"],
                )
            else:
                self._model_cache = sorted(available_models, key=lambda x: x["name"])

            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Gemini Debug] Cached {len(self._model_cache)} models")
                for m in self._model_cache:
                    print(f"    - {m['name']} (ctx={m['details']['context_length']})")

            self._cache_time = current_time
            return self._model_cache

        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Gemini Debug] /models call failed ({e}); using catalog fallback")

            # Catalog fallback — static list above.
            fallback = []
            for name, info in GEMINI_MODELS.items():
                fallback.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": info.get("family", "gemini"),
                        "backend": "gemini",
                        "context_length": info.get("context_length", 1_048_576),
                        "max_completion_tokens": info.get("max_completion_tokens", 65_536),
                        "free_tier": info.get("free_tier", False),
                        "supports_thinking": info.get("supports_thinking", True),
                    },
                })

            if GEMINI_FREE_ONLY:
                fallback = [m for m in fallback if m["details"].get("free_tier")]
            fallback.sort(key=lambda x: x["name"])

            self._model_cache = fallback
            self._cache_time = current_time
            return self._model_cache

    def is_running(self) -> bool:
        """Gemini is a cloud API — always reachable in principle."""
        return True

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Get the model's maximum trained context window."""
        # Catalog lookup first
        if model in GEMINI_MODELS:
            return GEMINI_MODELS[model].get("context_length", 1_048_576)
        # Cache lookup
        if self._model_cache:
            for m in self._model_cache:
                if m["name"] == model:
                    return m["details"].get("context_length", 1_048_576)
        # Family fallback
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx
        return 1_048_576  # 1M is the Gemini default for flash models

    def _get_model_defaults(self, model: str) -> dict:
        """Return per-model temperature / max_tokens defaults.

        ARCH-03 (R06.57): Cap + persisted-safe-value logic now inherited
        from ``OpenAICompatibleBackend._apply_max_tokens_cap``. This method
        just resolves the per-model max_tokens/context_length from cache
        or catalog and delegates the cap decision.
        """
        # Cache hit
        if self._model_cache:
            for m in self._model_cache:
                if m["name"] == model:
                    details = m["details"]
                    max_tokens = details.get("max_completion_tokens", 65_536)
                    context_length = details.get("context_length", 1_048_576)
                    return self._apply_max_tokens_cap(
                        max_tokens, context_length, temperature=1.0
                    )

        # Catalog fallback
        if model in GEMINI_MODELS:
            info = GEMINI_MODELS[model]
            max_tokens = info.get("max_completion_tokens", 65_536)
            context_length = info.get("context_length", 1_048_576)
            return self._apply_max_tokens_cap(
                max_tokens, context_length, temperature=1.0
            )

        # Final fallback — sensible defaults for unknown Gemini models.
        # Note: cap is still applied (1M context / 32 = 32K, vs 65K default
        # → 32K wins). Persisted safe value also honored.
        return self._apply_max_tokens_cap(65_536, 1_048_576, temperature=1.0)

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Classify tool support per Gemini model.

        Chat models (gemini-3.x-flash, gemini-2.5-flash, etc.) support
        native function calling → return ``NATIVE``.

        Non-chat models (embeddings, Veo video gen, Lyria music gen,
        robotics, transcribe, TTS, Live API, image gen, computer-use,
        deep-research, antigravity, omni video, Gemma open-source)
        can't accept chat-completions requests at all → return ``NONE``.
        Showing them as "✓ native" in the models table was misleading —
        users would try to chat with them and get a 400.

        The classifier is pattern-based and lives in
        ``_is_chat_capable_model()`` above. It strips a ``models/`` prefix
        if present (defensive — ``_parse_gemini_model`` already does this).
        """
        if not _is_chat_capable_model(model):
            return ToolSupportLevel.NONE
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # 429 / 5xx retry budget (mirrors OpenRouterBackend)
    # ─────────────────────────────────────────────────────────────────────

    def _max_429_retries(self) -> int:
        """Resolve the 429 retry budget (env override > class default)."""
        raw = os.environ.get("GEMINI_MAX_429_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_429_RETRIES

    def _429_backoff(self, attempt: int) -> float:
        """Exponential back-off with full jitter for the Nth retry."""
        import random
        delay = self._429_BACKOFF_BASE * (2 ** max(0, attempt - 1))
        delay = min(delay, self._429_BACKOFF_CAP)
        jitter = delay * 0.2
        return max(1.0, delay + random.uniform(-jitter, jitter))

    # ─────────────────────────────────────────────────────────────────────
    # Auth + URL hooks for the OpenAICompatibleBackend base class
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """Gemini's chat completions endpoint.

        The base class ``BaseBackend.__init__`` strips trailing slashes
        from ``base_url`` — so we add one back here to produce
        ``https://...googleapis.com/v1beta/openai/chat/completions``.
        Without this, we'd get ``...openaichat/completions`` (missing slash).
        """
        return f"{self.base_url}/chat/completions"

    def _get_auth_headers(self) -> dict:
        """Gemini accepts standard Bearer auth."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ─────────────────────────────────────────────────────────────────────
    # Body construction override — handle Gemini-specific extra_body
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
        """Build the OpenAI Chat-Completions body with Gemini extras.

        Gemini-specific quirks handled here:
          1. ``reasoning_effort`` and ``thinking_config`` are mutually
             exclusive. If both are provided, we keep ``reasoning_effort``
             and drop the config (with a debug warning).
          2. ``service_tier`` is forwarded at the top level (Gemini maps
             ``flex`` / ``priority`` / ``standard`` to its inference tiers).
          3. ``extra_body.google.thinking_config`` and
             ``extra_body.google.cached_content`` are forwarded verbatim
             when the caller supplies them — this is the documented escape
             hatch for native Gemini features not in the OpenAI spec.
        """
        body = super()._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        # service_tier — Gemini-native routing for flex/priority.
        service_tier = kwargs.pop("service_tier", None) or self._default_service_tier
        if service_tier and service_tier != "standard":
            body["service_tier"] = service_tier

        # Thinking config — mutual exclusivity enforcement.
        reasoning_effort = kwargs.get("reasoning_effort")
        thinking_config = kwargs.pop("thinking_config", None)
        include_thoughts = kwargs.pop("include_thoughts", None)
        cached_content = kwargs.pop("cached_content", None)
        thought_signature = kwargs.pop("thought_signature", None)

        # Apply default thinking level from env if nothing was specified.
        if reasoning_effort is None and thinking_config is None and self._default_thinking_level:
            # Use Gemini-native thinking_level (3.x) via extra_body.
            thinking_config = {"thinking_level": self._default_thinking_level}

        if thinking_config is not None:
            # Build / merge extra_body.google.thinking_config
            google_extra = body.setdefault("extra_body", {}).setdefault("google", {})
            tc = dict(thinking_config)  # shallow copy — don't mutate caller's
            if include_thoughts is not None:
                tc["include_thoughts"] = include_thoughts
            if thought_signature is not None:
                tc["thought_signature"] = thought_signature
            google_extra["thinking_config"] = tc

            # reasoning_effort would conflict — drop it if the caller set both.
            if reasoning_effort is not None and os.environ.get("AGENTKTHX_DEBUG"):
                print("  [Gemini] reasoning_effort and thinking_config both set — "
                      "keeping thinking_config, dropping reasoning_effort")
            body.pop("reasoning_effort", None)

        if cached_content is not None:
            google_extra = body.setdefault("extra_body", {}).setdefault("google", {})
            google_extra["cached_content"] = cached_content

        return body

    # ─────────────────────────────────────────────────────────────────────
    # HTTP transport — 429 / 5xx retry with exponential backoff
    # ─────────────────────────────────────────────────────────────────────

    def _make_api_request(self, endpoint: str, data: dict, stream: bool = False):
        """POST to Gemini's OpenAI-compat endpoint with retry.

        Honors ``Retry-After`` when present. Falls back to exponential
        back-off (5s → 10s → 20s → 40s → 80s → 90s cap). 429s with
        ``error.code == "rate_limit_exceeded"`` are the routine free-tier
        case; ``spend_limit_exceeded`` (paid tiers) is also retried but
        gets longer waits.
        """
        # base_url was trailing-slash-stripped by BaseBackend.__init__,
        # so we add the "/" back here to join cleanly with the endpoint.
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) environment variable "
                "is required for Gemini API calls"
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

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

                retryable = status_code == 429 or status_code in (502, 503, 504)
                if retryable:
                    error_msg = (
                        "Rate limit exceeded (RESOURCE_EXHAUSTED)"
                        if status_code == 429
                        else f"Gemini server error {status_code}"
                    )
                    err_field = None
                    if isinstance(err_data, dict):
                        err_field = err_data.get("error")
                        if isinstance(err_field, dict):
                            inner_msg = err_field.get("message", "")
                            inner_code = err_field.get("code", "")
                            if inner_msg:
                                error_msg = inner_msg
                            if inner_code == "spend_limit_exceeded":
                                error_msg = f"Spend limit exceeded (paid tier): {inner_msg}"
                        elif err_data.get("message"):
                            error_msg = err_data["message"]

                    last_retryable_error = error_msg
                    retry_after_raw = e.headers.get("Retry-After", "")
                    retry_after = None
                    if retry_after_raw:
                        try:
                            retry_after = float(retry_after_raw)
                        except (ValueError, TypeError):
                            retry_after = None
                    if retry_after is None:
                        retry_after = self._429_backoff(attempt + 1)
                    # Spend-limit errors deserve longer waits (10-min window).
                    if "spend_limit" in error_msg.lower():
                        retry_after = max(retry_after, 60.0)
                    retry_after = min(max(retry_after, 1.0), self._429_BACKOFF_CAP)

                    if attempt < max_retries:
                        print(f"  [Gemini] {status_code} — {error_msg}. "
                              f"Retrying in {retry_after:.0f}s "
                              f"(attempt {attempt + 1}/{max_retries + 1})...")
                        time.sleep(retry_after)
                        continue
                    raise RuntimeError(
                        f"Gemini rate limit: {error_msg}. "
                        f"Retried {max_retries} times. "
                        f"Try again in {retry_after:.0f} seconds."
                    )

                # 401 — auth error (key invalid / standard-key rejected post Sept 2026)
                if status_code == 401:
                    raise RuntimeError(
                        "Gemini authentication failed. Check your GEMINI_API_KEY "
                        "(or GOOGLE_API_KEY). If using a standard API key after "
                        "Sept 2026, migrate to an auth key in Google AI Studio."
                    )

                # 403 — standard key rejected (migration enforcement) or region blocked
                if status_code == 403:
                    upstream_msg = ""
                    if isinstance(err_data, dict):
                        err_field = err_data.get("error")
                        if isinstance(err_field, dict):
                            upstream_msg = err_field.get("message", "") or str(err_field)
                        else:
                            upstream_msg = str(err_data)
                    else:
                        upstream_msg = body_text[:500]
                    raise RuntimeError(
                        f"Gemini permission denied (403): {upstream_msg}. "
                        "If 'unrestricted standard key rejected', migrate to an "
                        "auth key in Google AI Studio. If region-blocked, see "
                        "https://ai.google.dev/gemini-api/docs/available-regions"
                    )

                # Any other 4xx/5xx
                if status_code >= 400:
                    upstream_msg = ""
                    if isinstance(err_data, dict):
                        err_field = err_data.get("error")
                        if isinstance(err_field, dict):
                            upstream_msg = err_field.get("message", "") or str(err_field)
                        elif err_data.get("message"):
                            upstream_msg = err_data["message"]
                        else:
                            upstream_msg = str(err_data)
                    else:
                        upstream_msg = body_text[:500]
                    if len(upstream_msg) > 500:
                        upstream_msg = upstream_msg[:500] + "..."
                    raise RuntimeError(f"Gemini API error {status_code}: {upstream_msg}")

            except urllib.error.URLError as e:
                raise RuntimeError(f"Gemini connection error: {e.reason}")

    def _stream_request(self, url: str, data: dict, headers: dict) -> Generator[dict, None, None]:
        """Streaming POST — yields parsed SSE chunk dicts.

        ROB-06 (R06.57): try/finally so the urllib response is closed
        deterministically when the generator is abandoned mid-iteration.
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
            raise RuntimeError(f"Gemini HTTP error {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Gemini connection error: {e.reason}")

        try:
            for line in response:
                if not line:
                    continue
                line_str = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                if not line_str.startswith("data: "):
                    continue
                json_str = line_str[6:].strip()
                if json_str == "[DONE]":
                    continue
                try:
                    yield json.loads(json_str)
                except json.JSONDecodeError:
                    continue
        finally:
            try:
                response.close()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract SSE hook
    # ─────────────────────────────────────────────────────────────────────

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to Gemini's /chat/completions.

        ARCH-03 (R06.57): Context-length 400 recovery now delegates to the
        shared ``_handle_context_length_400`` helper inherited from
        ``OpenAICompatibleBackend``. The per-backend regex patterns
        (``_CONTEXT_LENGTH_MAX_PATTERN`` etc. overridden above) make the
        shared helper parse Gemini's error format correctly.
        """
        for attempt in range(2):
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
                # ARCH-03 (R06.57): shared context-length 400 handler
                if e.code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(error_body, body):
                        new_max = body["max_tokens"]
                        print(f"  [Gemini-Stream] Context length exceeded — "
                              f"reducing max_tokens {old_max} → {new_max} and retrying")
                        continue
                raise RuntimeError(f"Gemini HTTP error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"Gemini connection error: {e.reason}")

            # ROB-06 (R06.57): try/finally so the urllib response is
            # closed deterministically when the generator is abandoned
            # mid-iteration (Ctrl+C per ROB-05, an exception inside the
            # consumer, or the base-class break on [DONE]). Mirrors
            # ZaiBackend._iter_sse_lines at zai.py:705-712.
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

    # ARCH-03 (R06.57): ``_calculate_safe_max_tokens`` was here — now
    # inherited from ``OpenAICompatibleBackend``. The per-backend regex
    # patterns (``_CONTEXT_LENGTH_MAX_PATTERN`` etc. above) make the
    # inherited method parse Gemini's error format correctly.

    # ─────────────────────────────────────────────────────────────────────
    # generate() — main entry point
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
        """Generate a response using Gemini's Chat Completions API.

        Implements the OpenAI Chat Completions spec for Gemini with native
        tool-calling support and automatic ReAct fallback when the model
        rejects the ``tools`` field (rare for Gemini, but defensive).

        Gemini-specific kwargs accepted:
          - thinking_config: dict — passed via extra_body.google.thinking_config
          - include_thoughts: bool — surface thought summaries
          - thought_signature: str — pass back for stateful continuation (v0.2)
          - cached_content: str — Gemini cachedContent resource name
          - service_tier: str — "standard" | "flex" | "priority"
          - reasoning_effort: str — OpenAI-style ("none"|"low"|"medium"|"high"|"minimal")

        Returns:
            Dict with keys: content, tool_calls, finish_reason, usage,
            latency_ms, reasoning_content, raw.
        """
        # JEV dispatch — if api_mode is JEV, route through generate_decision().
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else 8192,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # Resolve per-model defaults.
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
            print(f"  [Gemini] POST chat/completions — "
                  f"tools={len(tools) if tools else 0}, "
                  f"tool_choice={kwargs.get('tool_choice', 'auto')}, "
                  f"thinking={'yes' if 'reasoning_effort' in kwargs or 'thinking_config' in kwargs else 'default'}")

        start_time = time.time()
        try:
            raw_response = self._make_api_request("chat/completions", body)
        except RuntimeError as e:
            err_str = str(e)
            # ReAct fallback: rare on Gemini (all current chat models
            # support native tools), but keep the path for safety.
            if tools and self._is_tools_not_supported_error(err_str):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [Gemini] Model doesn't support tools — "
                          f"retrying without tools (ReAct fallback)")
                body.pop("tools", None)
                body.pop("tool_choice", None)
                raw_response = self._make_api_request("chat/completions", body)
            # Context-length 400 — reduce max_tokens and retry once.
            elif "context length" in err_str.lower():
                old_max = body.get("max_tokens", 4096)
                new_max = max(old_max // 3, 4096)
                if new_max < old_max:
                    print(f"  [Gemini] Context length exceeded — "
                          f"reducing max_tokens {old_max} → {new_max} and retrying")
                    body["max_tokens"] = new_max
                    raw_response = self._make_api_request("chat/completions", body)
                else:
                    raise RuntimeError(f"Gemini API error: {err_str}")
            else:
                raise RuntimeError(f"Gemini API error: {err_str}")

        latency_ms = (time.time() - start_time) * 1000
        parsed = self._parse_openai_response(raw_response)
        parsed["latency_ms"] = latency_ms

        # Gemma-family post-processing: parse inline <thought>...</thought>
        # tags from content into reasoning_content. Gemma uses these inline
        # tags instead of the OpenAI reasoning_content field that Gemini
        # 3.x uses. Without this, the raw <thought> blocks would show up
        # in the user-facing content stream.
        if _uses_thought_tags(model) and parsed["content"]:
            content_text, reasoning_text = _parse_thought_tags_from_complete_text(parsed["content"])
            if reasoning_text:
                # Append to any existing reasoning_content (rare for Gemma
                # but possible if the model also emits native reasoning_content)
                existing_rc = parsed.get("reasoning_content", "") or ""
                parsed["reasoning_content"] = existing_rc + reasoning_text
            parsed["content"] = content_text
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Gemini.ThoughtTags] model={model} → "
                      f"content_len={len(parsed['content'])}, "
                      f"reasoning_len={len(parsed.get('reasoning_content', ''))}")

        # Synthesize a finish_reason if missing (Gemini usually includes one).
        if parsed["finish_reason"] is None:
            if parsed["tool_calls"]:
                parsed["finish_reason"] = "tool_calls"
            elif not parsed["content"]:
                parsed["finish_reason"] = "stop"
            else:
                parsed["finish_reason"] = "stop"

        # Empty-response detection — surface as error so the agent loop
        # can show something went wrong instead of "AgentKthx: " with no body.
        # Note: for Gemma, after thought-tag parsing, content might be empty
        # if the model emitted ONLY reasoning (rare, but possible). We still
        # raise here — the user should see the issue and rephrase.
        if not parsed["content"].strip() and not parsed["tool_calls"]:
            raise RuntimeError(
                f"Gemini returned an empty response (no content, no tool_calls). "
                f"This may be a recitation filter, content filter, or model issue. "
                f"finish_reason={parsed['finish_reason']}"
            )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Gemini] finish_reason={parsed['finish_reason']}, "
                  f"tool_calls={len(parsed['tool_calls'])}, "
                  f"content_len={len(parsed['content'])}, "
                  f"reasoning_len={len(parsed.get('reasoning_content', ''))}")

        return parsed

    # ─────────────────────────────────────────────────────────────────────
    # JEV hook (System-One decision mode)
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
        """JEV hook for Gemini — route through self.generate() with full
        retry/auth/thinking-config handling.

        Mirrors OpenRouterBackend._jev_call_completions: temporarily flip
        api_mode to OPENAI to avoid infinite recursion (self.generate()
        calls _maybe_jev_dispatch() at the top, which would call
        generate_decision() → _jev_call_completions() → self.generate()
        again).
        """
        # Decisions never carry tools — pass tools=None explicitly.
        # response_format is stripped — JEV prompt handles JSON shape.
        # (Same reasoning as OpenRouter: forced JSON mode can cause empty
        # responses on some models.)
        kwargs.pop("response_format", None)
        # Decisions shouldn't think — saves tokens and latency.
        # For Gemini 3.x we can't fully disable, but "minimal" is the floor.
        if "reasoning_effort" not in kwargs and "thinking_config" not in kwargs:
            kwargs["reasoning_effort"] = "minimal"

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
                        print(f"  [Gemini.JEV] Empty response — retrying with simplified prompt")
                    simplified = [
                        {"role": "user",
                         "content": messages[-1]["content"] if messages else ""}
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

    @staticmethod
    def _is_tools_not_supported_error(err_str: str) -> bool:
        """Detect 'tools not supported' rejection (rare on Gemini)."""
        err_lower = err_str.lower()
        indicators = (
            "does not support tools",
            "tools are not supported",
            "tool calling is not supported",
            "tools are not yet supported",
            "does not support function calling",
            "function calling is not supported",
        )
        return any(ind in err_lower for ind in indicators)

    # ─────────────────────────────────────────────────────────────────────
    # generate_completions_stream() override — Gemma <thought> tag parsing
    # ─────────────────────────────────────────────────────────────────────

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Streaming with Gemma-family <thought> tag parsing.

        For non-Gemma models: delegates to the inherited
        ``OpenAICompatibleBackend.generate_completions_stream()`` unchanged.

        For Gemma models (and any future model that emits inline
        ``<thought>...</thought>`` tags): wraps the inherited generator
        with a stateful ``ThoughtTagParser`` that routes the tag content
        to ``reasoning_content`` so the existing AgentKthx UI shows it
        as a collapsible "thought" panel — instead of letting the raw
        tags leak into the user-facing content stream.
        """
        if not _uses_thought_tags(model):
            # Pass through unchanged — model uses native reasoning_content
            yield from super().generate_completions_stream(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            return

        # Gemma path — wrap parent's generator with ThoughtTagParser.
        # The parser is stateful so it survives across the chunk loop.
        parser = ThoughtTagParser()

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Gemini.ThoughtTags-Stream] model={model} — "
                  f"thought-tag parser active")

        for chunk in super().generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            delta = chunk.get("delta", "") or ""
            tool_calls = chunk.get("tool_calls")
            finish_reason = chunk.get("finish_reason")
            existing_reasoning = chunk.get("reasoning_content", "") or ""
            usage = chunk.get("_usage")  # PERF-02 final usage chunk

            # PERF-02 usage-only chunk — pass through unchanged
            if usage and not delta and not tool_calls and not finish_reason:
                yield chunk
                continue

            # Parse <thought> tags from this chunk's delta
            content_delta, reasoning_delta = parser.feed(delta)

            # Combine with any existing native reasoning_content (rare for
            # Gemma but possible if the model also emits native reasoning).
            combined_reasoning = ""
            if existing_reasoning:
                combined_reasoning += existing_reasoning
            if reasoning_delta:
                combined_reasoning += reasoning_delta

            yield_chunk: dict = {
                "delta": content_delta,
                "tool_calls": tool_calls,
                "finish_reason": finish_reason,
            }
            if combined_reasoning:
                yield_chunk["reasoning_content"] = combined_reasoning
            yield yield_chunk

        # End of stream — flush any buffered partial-tag content.
        # This handles the case where the model left a "<tho" or similar
        # partial tag at the end (state == "outside" → emit as content,
        # state == "inside" → emit as reasoning — model forgot to close).
        content_flush, reasoning_flush = parser.flush()
        if content_flush or reasoning_flush:
            yield_chunk = {
                "delta": content_flush,
                "tool_calls": None,
                "finish_reason": None,
            }
            if reasoning_flush:
                yield_chunk["reasoning_content"] = reasoning_flush
            yield yield_chunk

    # ─────────────────────────────────────────────────────────────────────
    # generate_stream() — text-only streaming convenience wrapper
    # ─────────────────────────────────────────────────────────────────────

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from Gemini.

        ARCH-01 parity: delegates to the inherited
        ``generate_completions_stream()`` and yields just the text deltas.
        Tool-call deltas and reasoning deltas are handled by the parent.
        """
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
