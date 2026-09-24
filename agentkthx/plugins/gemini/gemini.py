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
# Gemini model catalog
# ─────────────────────────────────────────────────────────────────────────────
# Static metadata used as a fallback when the /models endpoint isn't reachable
# or when a model isn't listed (e.g. preview models behind a feature flag).
# Context lengths and key caps per the Gemini docs (Sep 2026).
# Free-tier models are tagged with `free_tier=True` — GEMINI_FREE_ONLY filters
# the cached model list to just these.
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

        # Gemini only exposes the OpenAI-compat endpoint. JEV mode works
        # because it wraps the underlying chat-completions call.
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode == ApiMode.JEV:
            pass  # accepted — _jev_call_completions routes through generate()
        elif api_mode == ApiMode.OPENAI:
            pass
        else:
            raise ValueError(
                "Gemini backend only supports OpenAI Chat-Completions "
                "or JEV (System-One) API modes"
            )

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
        """Parse OpenAI-compat /models entry into AgentKthx format."""
        model_id = model_data.get("id") or model_data.get("name") or ""
        # /openai/models returns context_length and max_completion_tokens
        # at the top level (OpenAI shape). Fall back to catalog if missing.
        context_length = model_data.get("context_length") or \
            model_data.get("context_window") or 1_048_576
        max_completion = (model_data.get("top_provider") or {}).get("max_completion_tokens") \
            or model_data.get("max_completion_tokens") or 65_536

        # Free tier classification — use catalog if available, else heuristic.
        catalog = GEMINI_MODELS.get(model_id, {})
        free_tier = catalog.get("free_tier", "flash" in model_id or "lite" in model_id)

        family = catalog.get("family") or detect_gemini_family(model_id)["family"]

        return {
            "name": model_id,
            "size": 0,
            "details": {
                "family": family,
                "backend": "gemini",
                "context_length": context_length,
                "max_completion_tokens": max_completion,
                "free_tier": free_tier,
                "supports_thinking": catalog.get("supports_thinking", True),
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
        """Return per-model temperature / max_tokens defaults."""
        # Cache hit
        if self._model_cache:
            for m in self._model_cache:
                if m["name"] == model:
                    details = m["details"]
                    max_tokens = details.get("max_completion_tokens", 65_536)
                    context_length = details.get("context_length", 1_048_576)

                    # ROB-06 parity: persisted safe max_tokens wins.
                    if self._context_safe_max_tokens is not None:
                        return {
                            "temperature": 1.0,
                            "max_tokens": self._context_safe_max_tokens,
                            "context_length": context_length,
                        }

                    # Cap to leave room for input growth (mirrors OpenRouter
                    # R06.55 empirical finding on long agentic runs).
                    capped = min(max_tokens, context_length // 32)
                    return {
                        "temperature": 1.0,  # Gemini default
                        "max_tokens": capped,
                        "context_length": context_length,
                    }

        # Catalog fallback
        if model in GEMINI_MODELS:
            info = GEMINI_MODELS[model]
            max_tokens = info.get("max_completion_tokens", 65_536)
            context_length = info.get("context_length", 1_048_576)
            # ROB-06 parity: persisted safe max_tokens wins.
            if self._context_safe_max_tokens is not None:
                return {
                    "temperature": 1.0,
                    "max_tokens": self._context_safe_max_tokens,
                    "context_length": context_length,
                }
            return {
                "temperature": 1.0,
                "max_tokens": min(max_tokens, context_length // 32),
                "context_length": context_length,
            }

        # Final fallback — sensible defaults for unknown Gemini models.
        return {
            "temperature": 1.0,
            "max_tokens": 65_536,
            "context_length": 1_048_576,
        }

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """All current Gemini chat models support native function calling."""
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
        """Streaming POST — yields parsed SSE chunk dicts."""
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

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract SSE hook
    # ─────────────────────────────────────────────────────────────────────

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to Gemini's /chat/completions.

        Mirrors OpenRouterBackend's _iter_sse_lines with context-length 400
        recovery (ROB-06) — parses actual token counts from the error
        message, calculates a safe max_tokens, persists it, and retries once.
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
                # ROB-06 parity: context-length 400 → reduce max_tokens + retry
                if (e.code == 400 and attempt == 0
                        and "context length" in error_body.lower()):
                    new_max = self._calculate_safe_max_tokens(error_body, body)
                    if new_max is not None:
                        old_max = body.get("max_tokens", 4096)
                        print(f"  [Gemini-Stream] Context length exceeded — "
                              f"reducing max_tokens {old_max} → {new_max} and retrying")
                        body["max_tokens"] = new_max
                        self._context_safe_max_tokens = new_max
                        continue
                raise RuntimeError(f"Gemini HTTP error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"Gemini connection error: {e.reason}")

            for line in response:
                yield line
            return  # success — don't retry

    def _calculate_safe_max_tokens(self, error_body: str, body: dict) -> int | None:
        """Parse token counts from a context-length 400 and compute safe max.

        Gemini's error message looks like:
            "Request exceeds the maximum context length of 1048576 tokens.
             You requested 1100000 tokens (1000000 in the input, 100000 in
             the output)."
        """
        import re
        text = error_body.lower()
        max_match = re.search(r"maximum context length of (\d+)", text)
        input_match = re.search(r"(\d+) in the input", text)
        output_match = re.search(r"(\d+) in the output", text)

        if not max_match or not input_match:
            old_max = body.get("max_tokens", 4096)
            new_max = max(old_max // 3, 4096)
            return new_max if new_max < old_max else None

        max_context = int(max_match.group(1))
        input_tokens = int(input_match.group(1))
        if output_match:
            requested_output = int(output_match.group(1))
        else:
            requested_output = body.get("max_tokens", 4096)

        safety_margin = 2048
        safe_max = max_context - input_tokens - safety_margin
        if safe_max < 1024:
            safe_max = 1024
        old_max = body.get("max_tokens", 4096)
        if safe_max >= old_max:
            return None
        return safe_max

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
