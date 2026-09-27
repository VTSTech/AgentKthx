"""
⚛️ AgentKthx — Mistral La Plateforme Backend

Backend implementation for the Mistral API (OpenAI Chat-Completions
compatible, with deliberate wire-format deltas).

Mistral serves its public API at ``https://api.mistral.ai/v1`` — there
is no separate OpenAI-compat endpoint, the native ``/v1/chat/completions``
surface IS the OpenAI-style wire format. Deltas vs the OpenAI spec:

  - ``random_seed`` instead of ``seed``
  - ``finish_reason: "model_length"`` for context-window overflow
    (distinct from ``"length"`` which means ``max_tokens`` was hit)
  - ``reasoning_effort`` enum is a superset: ``none | minimal | low |
    medium | high | xhigh`` (the ``xhigh`` rung is Mistral-only)
  - Optional ``safe_prompt: bool`` injects a safety system prompt
  - AssistantMessage supports ``prefix: true`` for prefill
  - Tool-call IDs are short opaque strings (``D681PevKs``) with no
    ``call_`` prefix — the schema default is the literal ``"null"``
  - Function-call ``arguments`` field may arrive as a JSON string OR
    a parsed object (spec allows both; parser must tolerate both)
  - Error envelope is ``{"object": "error", "message": ..., "type":
    ..., "param": ..., "code": ...}`` — note the ``object == "error"``
    marker, distinct from OpenAI's bare ``{"error": {...}}`` wrapper
  - ``response_format: {"type": "json_schema", "schema": {...}}`` is
    GA; ``json_object`` requires "JSON" mentioned in the prompt
  - ``prompt_cache_key`` enables cached-prefix billing at 10% of input

This backend inherits the shared cloud-backend boilerplate from
``CloudBackend`` (base URL resolution, API-key validation, auth
headers, catalog-driven model defaults, ``is_running()``,
``test_tool_support()``). Mistral-specific overrides remain here:

  - ``MODELS`` catalog (from docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md)
  - ``_get_chat_completions_url()`` — ``/chat/completions``
  - ``list_models()`` — queries ``GET /v1/models`` (richer card than
    OpenAI's: includes ``capabilities``, ``max_context_length``,
    ``default_model_temperature``, ``aliases``, ``deprecation``)
  - ``_iter_sse_lines()`` — Mistral-specific SSE parsing with
    ``data: [DONE]`` terminator and 429/5xx retry honoring ``Retry-After``
  - ``_build_request()`` — strips OpenAI-only kwargs (``seed``,
    ``logprobs``, ``top_k``, ``user``, ``max_completion_tokens``)
    and aliases ``seed`` → ``random_seed``, ``tool_choice="required"``
    → ``"any"``
  - ``_parse_mistral_response()`` — Mistral's error envelope is
    ``{"object": "error", ...}``, distinct from OpenAI's
    ``{"error": {...}}`` wrapper
  - ``_make_api_request()`` — non-streaming POST with retry

Endpoints used:
  - POST /chat/completions  → OpenAI Chat Completions (tools, streaming)
  - GET  /models            → model discovery (Mistral-shaped cards)

Configuration:
  MISTRAL_API_KEY       — API key for authentication (required)
  MISTRAL_BASE_URL      — API base URL (default: https://api.mistral.ai/v1)
  MISTRAL_DEFAULT_MODEL — Default model (default: mistral-small-latest)
  MISTRAL_SAFE_PROMPT   — Inject Mistral's safety system prompt (default: false)
  MISTRAL_SERVICE_TIER  — "auto" | "standard_only" | "" (don't send)

Usage:
  # CLI
  agentkthx chat --backend mistral --model mistral-medium-latest
  agentkthx run "What is 15 * 8?" --backend mst --model mistral-small-latest

  # Python API
  from agentkthx import Agent
  agent = Agent(model="mistral-small-latest", backend="mistral",
                tools=["calculator"])
  result = agent.run("What is 15 * 8?")

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from typing import Any, Generator

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.base import BackendConfig
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode
from agentkthx.core.models import Tool
from agentkthx.config import (
    MISTRAL_BASE_URL,
    MISTRAL_API_KEY,
    MISTRAL_DEFAULT_MODEL,
    MISTRAL_FREE_ONLY,
    MISTRAL_FREE_FALLBACK_MODEL,
    MISTRAL_SAFE_PROMPT,
    MISTRAL_SERVICE_TIER,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mistral model catalog
# ─────────────────────────────────────────────────────────────────────────────
# Sourced from docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md §Model Family
# Specifications. Context lengths are per the model cards. Pricing is per
# 1M input / 1M output tokens (USD). Free entries are Labs models —
# ``labs-`` prefix, free of charge, silent updates, non-production.
#
# AgentKthx catalog schema (must match CloudBackend's expectation):
#   context_length, default_max_tokens, default_temperature, pricing
#
# Default temperature comes from each model card's ``default_model_temperature``
# (e.g. 0.7 for Mistral Medium 3.5). Max output for Mistral Large 3 / Medium
# 3.5 / Small 4 is 256K context window — but ``default_max_tokens`` is the
# conservative 8K output cap (matches the AgentKthx shared pattern). Override
# at runtime via ``max_tokens=``.
MISTRAL_MODELS: dict[str, dict] = {
    # ── Mistral Medium — frontier multimodal, agentic + coding ──────────
    "mistral-medium-latest": {
        "context_length": 262_144,            # 256K
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 2.00, "output": 6.00},
        "family": "mistral-medium",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_prefix_prefill": True,
        "license": "Modified MIT",
    },
    "mistral-medium-3-5": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 2.00, "output": 6.00},
        "family": "mistral-medium",
    },
    "mistral-medium-3": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 2.00, "output": 6.00},
        "family": "mistral-medium",
    },
    # ── Mistral Small — efficient hybrid (instruct + reasoning + code) ──
    "mistral-small-latest": {
        "context_length": 262_144,            # 256K
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.20, "output": 0.50},
        "family": "mistral-small",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "license": "Apache 2.0",
    },
    "mistral-small-4": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.20, "output": 0.50},
        "family": "mistral-small",
    },
    # ── Mistral Large — open-weight general-purpose multimodal ──────────
    "mistral-large-latest": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.50, "output": 1.50},
        "family": "mistral-large",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "supports_n_completions": False,      # mistral-large-2512 rejects n > 1
        "license": "Apache 2.0",
    },
    "mistral-large-3": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.50, "output": 1.50},
        "family": "mistral-large",
    },
    # ── Ministral — small on-device-class tier (text + vision) ──────────
    "ministral-14b-latest": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.20, "output": 0.50},
        "family": "ministral",
        "supports_vision": True,
        "supports_function_calling": True,
        "license": "Apache 2.0",
    },
    "ministral-8b-latest": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.10, "output": 0.30},
        "family": "ministral",
        "supports_vision": True,
        "supports_function_calling": True,
        "license": "Apache 2.0",
    },
    "ministral-3b-latest": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.04, "output": 0.04},
        "family": "ministral",
        "supports_vision": True,
        "supports_function_calling": True,
        "license": "Apache 2.0",
    },
    # ── Devstral — SWE-agent coding specialist ───────────────────────────
    "devstral-latest": {
        "context_length": 262_144,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.20, "output": 0.50},
        "family": "devstral",
        "supports_function_calling": True,
        "license": "Apache 2.0",
        "note": "Tool-calling-first tuning; best AgentKthx backend model per token",
    },
    # ── Magistral — dedicated reasoning ladder (128K context) ───────────
    "magistral-medium-latest": {
        "context_length": 131_072,            # 128K — smaller than sibling Medium
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 2.00, "output": 6.00},
        "family": "magistral",
        "supports_reasoning": True,           # prompt_mode="reasoning", ThinkChunk output
        "supports_function_calling": True,
    },
    "magistral-small-latest": {
        "context_length": 131_072,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.50, "output": 1.50},
        "family": "magistral",
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
    # ── Codestral — FIM + chat code completion (128K context) ────────────
    "codestral-latest": {
        "context_length": 131_072,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.20, "output": 0.60},
        "family": "codestral",
        "supports_function_calling": True,
        "supports_fim": True,                # dedicated /fim/completions endpoint
    },
    # ── Labs — free of charge, silent updates, NOT production-grade ─────
    # Pinned in the catalog so MISTRAL_FREE_ONLY mode has something to
    # fall back to without requiring a live API discovery call.
    "labs-mistral-small-creative": {
        "context_length": 262_144,
        "default_temperature": 0.9,          # creative bias
        "default_max_tokens": 8192,
        "pricing": {"input": 0.0, "output": 0.0},  # Labs = free
        "family": "labs",
        "supports_function_calling": True,
        "note": "Labs model — silent updates, no data opt-out, not for production",
    },
}

# Default model when none is specified. Resolved from env var → catalog.
MISTRAL_DEFAULT_MODEL_FALLBACK = "mistral-small-latest"


def _is_free_model(model: str) -> bool:
    """Check if a Mistral model is free (zero pricing OR Labs prefix)."""
    model_key = model.split("/")[-1] if "/" in model else model
    if model_key.startswith("labs-"):
        return True
    meta = MISTRAL_MODELS.get(model_key)
    if not meta:
        return False
    pricing = meta.get("pricing", {})
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# MistralBackend
# ─────────────────────────────────────────────────────────────────────────────


class MistralBackend(CloudBackend):
    """
    Backend for the Mistral La Plateforme API.

    Inherits the shared cloud-backend patterns from ``CloudBackend``
    (base URL resolution, API-key validation, auth headers,
    catalog-driven ``_get_model_defaults``, ``is_running()``,
    ``test_tool_support()``). Mistral-specific overrides remain here.

    Mistral wire-format deltas vs the OpenAI Chat-Completions spec
    that this backend handles:

      1. ``seed`` kwarg → ``random_seed`` request field
      2. ``tool_choice="required"`` → ``"any"`` (Mistral's forced-call
         value; ``"required"`` is accepted as an alias on the live API
         but ``"any"`` is safer for self-hosted gateways that predate
         the alias)
      3. ``reasoning_effort`` passes through unchanged (superset enum;
         ``xhigh`` is Mistral-only — sent only when caller explicitly
         requests it)
      4. ``safe_prompt`` is added when ``MISTRAL_SAFE_PROMPT=true``
      5. ``service_tier`` is added when ``MISTRAL_SERVICE_TIER`` is set
      6. ``prompt_cache_key`` is added when ``session_id`` kwarg is
         present (cached input tokens billed at 10% of standard price)
      7. Strips OpenAI-only kwargs (``logprobs``, ``top_k``, ``user``,
         ``max_completion_tokens``) that would 422 on Mistral
      8. Error envelope is ``{"object": "error", ...}`` — distinct
         from OpenAI's ``{"error": {...}}`` wrapper; the parser handles
         both forms

    Usage:
        backend = get_backend("mistral")
        backend = MistralBackend(api_key="...")
    """

    # CloudBackend required overrides — provider identity as class attrs
    MODELS = MISTRAL_MODELS
    _api_key_env_var = "MISTRAL_API_KEY"
    _default_base_url = MISTRAL_BASE_URL
    _default_model = MISTRAL_DEFAULT_MODEL or MISTRAL_DEFAULT_MODEL_FALLBACK
    _provider_label = "Mistral"

    # Mistral's API keys are 32-char hex strings by default. CloudBackend
    # enforces _MIN_API_KEY_LEN=20 (R07.07 ROB-21), which is satisfied.
    # Override only if a future key format diverges.
    _MIN_API_KEY_LEN: int = 20

    # Mistral's context-length 400 message format differs from the
    # OpenRouter/ZAI default. Per the technical reference, the message
    # reads like "maximum context length of 262144 tokens" (with "of"
    # not "is"). Override the inherited OpenAICompatibleBackend patterns.
    #
    # The regexes below are intentionally permissive — they accept
    # "is X tokens", "of X tokens", and bare "X tokens" forms so the
    # shared _handle_context_length_400 recovery path (ARCH-03) kicks
    # in regardless of which phrasing Mistral returns on a given 400.
    _CONTEXT_LENGTH_MAX_PATTERN: str = r"maximum context length (?:is |of )?(\d+)"
    _CONTEXT_LENGTH_INPUT_PATTERN: str = r"(\d+) tokens? (?:in|of) (?:the )?(?:text |)input"
    # Mistral does not separately report tool-input tokens in the 400
    # body — set to None so the shared recovery path treats tool input
    # as 0 (matching the Gemini override pattern).
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = None
    # Mistral counts input + output against max_context_length — raise
    # the safety margin so the calculated safe max_tokens leaves room
    # for both halves of the budget.
    _CONTEXT_SAFETY_MARGIN: int = 4096

    # Retry policy — mirrors Mistral's official SDK: exponential backoff
    # with full jitter, max 5 attempts. Override with MISTRAL_MAX_RETRIES.
    _MAX_RETRIES: int = 5
    _BACKOFF_BASE: float = 1.0
    _BACKOFF_CAP: float = 60.0

    # Set of OpenAI-only request fields that would 422 on Mistral. Never
    # forward these to the API — strip them in _build_request().
    _OPENAI_ONLY_FIELDS = frozenset({
        "logprobs", "top_logprobs", "top_k", "user",
        "max_completion_tokens", "seed",  # seed → random_seed (mapped)
        "metadata_internal",
    })

    # ───────────────────────────────────────────────────────────────────
    # __init__ — delegate to CloudBackend
    # ───────────────────────────────────────────────────────────────────

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str | None = None,
        api_key: str | None = None,
    ):
        # CloudBackend.__init__ resolves base_url/host/port, validates
        # the API key, forces OPENAI/JEV api_mode, and initializes
        # _context_safe_max_tokens. ~30 lines of boilerplate collapsed
        # to one super().__init__ call.
        super().__init__(
            base_url=base_url,
            host=host,
            port=port,
            config=config,
            api_mode=api_mode,
            api_key=api_key,
        )

    @property
    def backend_type(self) -> BackendType:
        return BackendType.MISTRAL

    # ``base_url`` and ``api_key`` properties are inherited from
    # CloudBackend — no override needed.

    # ``is_running()`` is inherited — cloud service is "running" iff an
    # API key is configured.

    # Mistral catalog uses the model family name (e.g. "mistral-medium")
    # as the family label, not "mistral". Override the CloudBackend
    # defaults so /models output matches Mistral's own grouping.
    def _catalog_family_name(self) -> str:
        return "mistral"

    def _catalog_backend_name(self) -> str:
        return "mistral"

    def _validate_api_key(self, key: str) -> None:
        """Hook for provider-specific API key validation.

        Mistral keys are opaque bearer tokens — typically 32-char hex
        strings, but the platform accepts longer tokens for some key
        types (admin / service-account). We don't enforce a format
        prefix (unlike OpenAI's ``sk-`` prefix check) because Mistral
        doesn't document one. The CloudBackend base class already
        enforces the 20-char minimum length.
        """
        return

    def _extra_auth_headers(self) -> dict:
        """Mistral recommends ``Accept: application/json`` for non-streaming
        and ``Accept: text/event-stream`` for streaming. We don't know
        which mode at header-construction time, so we set the JSON form
        here — the streaming path overrides it locally.
        """
        return {"Accept": "application/json"}

    # ───────────────────────────────────────────────────────────────────
    # URL construction
    # ───────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """Full URL for the ``/chat/completions`` endpoint.

        Mistral's API base already includes ``/v1`` — appending
        ``/chat/completions`` produces the canonical
        ``https://api.mistral.ai/v1/chat/completions``.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the ``GET /models`` discovery endpoint."""
        return f"{self._base_url.rstrip('/')}/models"

    # ───────────────────────────────────────────────────────────────────
    # list_models — query GET /v1/models, merge with static catalog
    # ───────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """List available Mistral models.

        Queries ``GET /v1/models`` dynamically and merges with the
        static ``MISTRAL_MODELS`` catalog. The API returns richer model
        cards than OpenAI's (``capabilities``, ``max_context_length``,
        ``default_model_temperature``, ``aliases``, ``deprecation``) —
        we surface the capabilities and context length in the AgentKthx
        shape.

        Enriches API results with ``context_length`` from the static
        catalog (the API may omit it for some models). Sets
        ``free_tier`` from the catalog pricing via ``_is_free_model()``
        (Mistral's cards don't expose pricing — Labs models are the
        only genuinely-free tier).

        Falls back to the static catalog when the API is unreachable
        (offline gateways, self-hosted ``mistral-inference``).
        """
        api_model_keys: set[str] = set()

        try:
            url = self._get_models_url()
            headers = self._get_auth_headers()
            req = urllib.request.Request(url, headers=headers, method="GET")

            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))

            api_models = result.get("data", [])
            if api_models:
                for m in api_models:
                    name = m.get("id", "")
                    if not name:
                        continue
                    model_key = name.split("/")[-1] if "/" in name else name
                    api_model_keys.add(model_key)

                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [Mistral] API returned {len(api_model_keys)} models")

        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Mistral] Model discovery failed ({e}), using static catalog")
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Mistral] Model discovery error ({e}), using static catalog")

        # Build unified list: API-discovered models first (confirmed
        # available), then catalog-only models (flash variants, Labs).
        seen: set[str] = set()
        models: list[dict] = []

        for model_key in sorted(api_model_keys):
            if model_key in seen:
                continue
            seen.add(model_key)
            meta = MISTRAL_MODELS.get(model_key, {})
            models.append({
                "name": model_key,
                "size": 0,
                "details": {
                    "family": meta.get("family", self._catalog_family_name()),
                    "backend": self._catalog_backend_name(),
                    "context_length": meta.get(
                        "context_length", self._DEFAULT_CONTEXT_FALLBACK
                    ),
                    "free_tier": _is_free_model(model_key),
                    "is_chat_model": True,
                    "pricing": meta.get("pricing", {}),
                },
            })

        for name in sorted(MISTRAL_MODELS.keys()):
            if name in seen:
                continue
            seen.add(name)
            meta = MISTRAL_MODELS[name]
            models.append({
                "name": name,
                "size": 0,
                "details": {
                    "family": meta.get("family", self._catalog_family_name()),
                    "backend": self._catalog_backend_name(),
                    "context_length": meta.get(
                        "context_length", self._DEFAULT_CONTEXT_FALLBACK
                    ),
                    "free_tier": _is_free_model(name),
                    "is_chat_model": True,
                    "pricing": meta.get("pricing", {}),
                },
            })

        if MISTRAL_FREE_ONLY:
            models = [m for m in models if m["details"].get("free_tier")]

        if os.environ.get("AGENTKTHX_DEBUG"):
            catalog_only = len(models) - len(api_model_keys)
            print(
                f"  [Mistral] Total: {len(models)} models "
                f"({len(api_model_keys)} API + {catalog_only} catalog)"
            )

        return models

    def get_model_info(self, model: str) -> dict | None:
        """Get model information from the Mistral catalog.

        CloudBackend's parent returns ``None`` for models not in the
        catalog. Mistral accepts any valid model ID (including date-
        pinned snapshots like ``mistral-medium-2508`` not in our
        catalog), so this override returns a default 256K-context entry
        for unknown models instead of ``None`` — matching the ZAI
        pattern.
        """
        info = super().get_model_info(model)
        if info is not None:
            info["details"]["is_chat_model"] = True
            model_key = model.split("/")[-1] if "/" in model else model
            info["details"]["pricing"] = MISTRAL_MODELS.get(
                model_key, {}
            ).get("pricing", {})
            return info
        # Unknown model — Mistral accepts it; return a safe default.
        model_key = model.split("/")[-1] if "/" in model else model
        return {
            "name": model_key,
            "size": 0,
            "details": {
                "family": self._catalog_family_name(),
                "backend": self._catalog_backend_name(),
                "context_length": self._DEFAULT_CONTEXT_FALLBACK,
                "free_tier": _is_free_model(model_key),
                "is_chat_model": True,
            },
        }

    # ``_get_model_defaults`` is inherited from CloudBackend — the
    # catalog lookup + ``_apply_max_tokens_cap`` logic is identical
    # for Mistral. ARCH-03 (R06.57): the overridden regex patterns
    # above ensure Mistral's 400 context-length error format is
    # recognized.

    # ───────────────────────────────────────────────────────────────────
    # Request construction — Mistral wire-format deltas
    # ───────────────────────────────────────────────────────────────────

    def _build_mistral_body(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """Build a Mistral Chat-Completions request body.

        Applies all documented Mistral deltas over the OpenAI shape:
          - ``seed`` → ``random_seed``
          - ``tool_choice="required"`` → ``"any"``
          - Adds ``safe_prompt`` when ``MISTRAL_SAFE_PROMPT=true``
          - Adds ``service_tier`` when ``MISTRAL_SERVICE_TIER`` is set
          - Adds ``prompt_cache_key`` when ``session_id`` kwarg present
          - Adds ``reasoning_effort`` when caller passes it
            (superset enum — pass through ``xhigh`` verbatim)
          - Strips OpenAI-only kwargs that would 422

        When ``stream=True``, also sets ``stream_options.include_usage``
        so the SSE stream carries a final usage chunk (PERF-02).
        """
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if stream:
            body["stream_options"] = {"include_usage": True}

        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]
            # Mistral's forced-call value is "any"; "required" is an
            # accepted alias on the live API but older self-hosted
            # gateways reject it. Map for safety.
            tc = kwargs.get("tool_choice")
            if tc == "required":
                body["tool_choice"] = "any"
            elif tc is not None:
                body["tool_choice"] = tc
            elif kwargs.get("parallel_tool_calls") is False:
                body["parallel_tool_calls"] = False

        # Mistral-specific: seed → random_seed
        seed = kwargs.get("seed")
        random_seed = kwargs.get("random_seed")
        if random_seed is not None:
            body["random_seed"] = random_seed
        elif seed is not None:
            body["random_seed"] = seed

        # Optional sampling fields (don't forward top_k — not in the
        # Mistral spec and would 422)
        for field in ("top_p", "n", "presence_penalty", "frequency_penalty"):
            val = kwargs.get(field)
            if val is not None:
                body[field] = val

        stop = kwargs.get("stop")
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]

        response_format = kwargs.get("response_format")
        if response_format is not None:
            body["response_format"] = response_format

        # Reasoning effort — superset enum; pass through unchanged.
        # The Mistral docs note: never send reasoning_effort AND
        # prompt_mode together. prompt_mode is for the Conversations/
        # Agents stack — we never send it here.
        reasoning_effort = kwargs.get("reasoning_effort")
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        # safe_prompt — Mistral-specific. Default false (controlled by
        # MISTRAL_SAFE_PROMPT env var); the agent's soul/system prompt
        # owns behavior. Read live from os.environ so runtime changes
        # (e.g. agentkthx param set) take effect without a process
        # restart — the module-level constant in agentkthx.config is
        # bound at import time and won't pick up env-var changes.
        if os.environ.get("MISTRAL_SAFE_PROMPT", "").lower() in ("1", "true", "yes"):
            body["safe_prompt"] = True

        # Service tier — "auto" allows Priority routing if the org has
        # the entitlement; "standard_only" opts out. Empty (default)
        # means don't send the parameter (server default is "auto").
        # Same live-read rationale as safe_prompt above.
        service_tier = os.environ.get("MISTRAL_SERVICE_TIER", "")
        if service_tier:
            body["service_tier"] = service_tier

        # Prompt caching — key on session_id when caller provides one.
        # Cached input tokens billed at 10% of standard input price.
        session_id = kwargs.get("session_id")
        if session_id:
            body["prompt_cache_key"] = f"agentkthx-{session_id}"

        # Forward any other kwargs the caller explicitly passes, EXCEPT
        # the OpenAI-only fields that would 422 on Mistral.
        for key, value in kwargs.items():
            if key in self._OPENAI_ONLY_FIELDS:
                continue
            if key in (
                "model", "messages", "tools", "stream", "temperature",
                "max_tokens", "tool_choice", "parallel_tool_calls",
                "top_p", "n", "presence_penalty", "frequency_penalty",
                "stop", "response_format", "reasoning_effort",
                "seed", "random_seed", "session_id",
            ):
                continue
            body[key] = value

        return body

    @staticmethod
    def _parse_mistral_response(raw_response: dict) -> dict:
        """Parse a Mistral Chat-Completions response.

        Returns a dict in the shape AgentKthx's agent loop expects:
        ``{content, tool_calls, finish_reason, usage, reasoning_content,
        raw}``.

        Handles Mistral-specific error envelope
        (``{"object": "error", "message": ..., ...}``) by surfacing it
        as a ``RuntimeError`` — distinct from OpenAI's
        ``{"error": {...}}`` wrapper which the shared
        ``_parse_openai_response`` already handles.

        Tool-call ``arguments`` may arrive as a JSON string OR a parsed
        object (Mistral spec allows both). We tolerate both and surface
        a ``_raw_arguments`` fallback for malformed JSON.
        """
        # Mistral error envelope — {"object": "error", "message": ...}
        if raw_response.get("object") == "error" or "message" in raw_response and not raw_response.get("choices"):
            msg = raw_response.get("message", str(raw_response))
            etype = raw_response.get("type", "")
            code = raw_response.get("code", "")
            raise RuntimeError(
                f"Mistral API error: {msg} "
                f"(type={etype}, code={code})"
            )

        # OpenAI-style {"error": {...}} envelope (some gateways wrap)
        err_field = raw_response.get("error")
        if err_field:
            if isinstance(err_field, dict):
                err_msg = err_field.get("message") or str(err_field)
            else:
                err_msg = str(err_field)
            raise RuntimeError(f"Mistral API error: {err_msg}")

        choices = raw_response.get("choices")
        if not choices:
            raise RuntimeError(
                f"Mistral API returned no choices in response: {raw_response}"
            )

        choice = choices[0]
        msg = choice.get("message", {}) or {}

        # content may be str OR list of ContentChunk (reasoning models).
        # Normalize to a string for AgentKthx's loop.
        raw_content = msg.get("content")
        reasoning_content = ""
        content_str = ""
        if isinstance(raw_content, str):
            content_str = raw_content
        elif isinstance(raw_content, list):
            text_parts: list[str] = []
            for chunk in raw_content:
                if not isinstance(chunk, dict):
                    continue
                chunk_type = chunk.get("type")
                if chunk_type == "text":
                    text_parts.append(chunk.get("text", ""))
                elif chunk_type == "thinking":
                    # Extract thinking text into a separate field —
                    # AgentKthx renders this in a collapsible panel,
                    # not as the main answer.
                    for piece in chunk.get("thinking", []):
                        if isinstance(piece, dict) and piece.get("type") == "text":
                            reasoning_content += piece.get("text", "")
            content_str = "".join(text_parts)

        # Tool calls — accumulate by index (Mistral emits one entry
        # per call; arguments may be str or object).
        tool_calls_out: list[dict] = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            args_raw = fn.get("arguments", "{}")
            if isinstance(args_raw, str):
                if args_raw.strip():
                    try:
                        args = json.loads(args_raw)
                    except json.JSONDecodeError:
                        args = {"_raw_arguments": args_raw}
                else:
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {"_raw_arguments": str(args_raw)}

            # Mistral's schema default for tool_call.id is the literal
            # string "null" — synthesize a fallback when the model
            # emits nothing usable.
            tc_id = tc.get("id") or f"mistral_tc_{i}"
            tool_calls_out.append({
                "id": tc_id,
                "type": tc.get("type", "function"),
                "name": fn.get("name", ""),
                "arguments": args,
            })

        usage = raw_response.get("usage", {}) or {}

        return {
            "content": content_str,
            "tool_calls": tool_calls_out,
            "finish_reason": choice.get("finish_reason"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "reasoning_content": reasoning_content,
            "raw": raw_response,
        }

    # ───────────────────────────────────────────────────────────────────
    # Generation — non-streaming POST with retry
    # ───────────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response from the Mistral API.

        Always uses OpenAI Chat-Completions format. The ``think``
        parameter is ignored (Mistral manages thinking internally via
        ``reasoning_effort`` — callers should pass ``reasoning_effort``
        directly to control thinking depth).

        Injects Bearer token auth + Mistral-specific request shaping
        (random_seed aliasing, safe_prompt injection, tool_choice="any"
        mapping, OpenAI-only kwarg stripping) into every request.
        """
        # JEV dispatch — if api_mode is JEV, route through
        # generate_decision() which wraps the underlying LLM call with
        # a constrained decision prompt.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=max_tokens if max_tokens is not None else 8192,
            think=think,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # Model defaults from catalog
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        if think is not None and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                "  [Mistral] 'think' parameter ignored — "
                "use 'reasoning_effort' to control thinking depth"
            )

        # MISTRAL_FREE_ONLY: reject paid models upfront
        if MISTRAL_FREE_ONLY and not _is_free_model(model):
            fallback = MISTRAL_FREE_FALLBACK_MODEL
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Mistral] FREE_ONLY mode — '{model}' is a paid "
                    f"model, switching to '{fallback}'"
                )
            model = fallback

        body = self._build_mistral_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

        return self._make_api_request(body, stream=False)

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
        """JEV hook for Mistral: route the decision call through
        Mistral's Bearer-authenticated ``/chat/completions`` endpoint.

        Keeps Mistral's auth + MISTRAL_FREE_ONLY + fallback logic
        active when running decisions through free Mistral models.

        The response shape is normalized to match generate():
        ``{content, tool_calls, usage, latency_ms, raw}``.
        """
        if MISTRAL_FREE_ONLY and not _is_free_model(model):
            fallback = MISTRAL_FREE_FALLBACK_MODEL
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Mistral.JEV] FREE_ONLY mode — '{model}' is a "
                    f"paid model, switching to '{fallback}'"
                )
            model = fallback

        body = self._build_mistral_body(
            model=model,
            messages=messages,
            tools=None,  # decisions never call tools
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            response_format=response_format,
            **kwargs,
        )
        return self._make_api_request(body, stream=False)

    # ───────────────────────────────────────────────────────────────────
    # Streaming — SSE with data: [DONE] terminator + 429/5xx retry
    # ───────────────────────────────────────────────────────────────────

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from the Mistral API.

        Yields content deltas (text fragments) as they arrive in SSE
        ``data: {...}`` chunks. Handles Mistral's streaming quirks:

          - SSE chunks are JSON objects terminated by ``data: [DONE]``
          - ``stream_options.include_usage=True`` is set so the final
            chunk carries token usage stats (PERF-02)
          - 10-minute inactivity timeout: server-side, no client
            mitigation beyond ``BackendConfig.timeout``
          - 429 / 5xx responses: honored with Retry-After + backoff
            (matches non-streaming path)

        Tool-call deltas arrive as ``tool_calls[].function.arguments``
        fragments across multiple chunks — we accumulate them by index
        but yield only text content (the agent loop reconstructs the
        final tool_calls from the accumulated state).
        """
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        if MISTRAL_FREE_ONLY and not _is_free_model(model):
            fallback = MISTRAL_FREE_FALLBACK_MODEL
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Mistral.stream] FREE_ONLY mode — '{model}' is "
                    f"a paid model, switching to '{fallback}'"
                )
            model = fallback

        body = self._build_mistral_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **kwargs,
        )

        # Streaming uses Accept: text/event-stream (not JSON).
        headers = dict(self._get_auth_headers())
        headers["Accept"] = "text/event-stream"

        url = self._get_chat_completions_url()
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        # Mistral streams via chunked transfer encoding — read
        # incrementally and split on ``\n\n`` event boundaries.
        try:
            response = urllib.request.urlopen(
                req, timeout=self.config.timeout
            )
        except urllib.error.HTTPError as e:
            # Surface HTTP errors with the upstream body so the agent
            # loop's retry / context-length recovery can pattern-match.
            body_bytes = e.read() if e.fp else b""
            body_text = body_bytes.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Mistral API error {e.code}: {body_text[:500]}"
            ) from e

        try:
            buf = b""
            for chunk in iter(lambda: response.read(1024), b""):
                buf += chunk
                while b"\n\n" in buf:
                    event_bytes, buf = buf.split(b"\n\n", 1)
                    for line in event_bytes.decode(
                        "utf-8", errors="replace"
                    ).splitlines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            return
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        # Extract content delta
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield content
        finally:
            # ROB-06: deterministic close when the generator is
            # abandoned mid-iteration.
            response.close()

    # ───────────────────────────────────────────────────────────────────
    # _make_api_request — non-streaming POST with retry
    # ───────────────────────────────────────────────────────────────────

    def _make_api_request(self, body: dict, *, stream: bool = False) -> dict:
        """POST to ``/chat/completions`` with 429/5xx retry.

        Honors ``Retry-After`` when present (429 rate-limit and 503
        service-unavailable). Falls back to exponential backoff with
        full jitter (mirrors Mistral's official SDK recipe). Max 5
        attempts before surfacing the final error as ``RuntimeError``
        — the agent loop's resilience classifier
        (``core.api_resilience.is_transient_api_error``) treats
        RuntimeError-wrapped upstream messages as permanent by default,
        so a retried-and-exhausted error becomes a terminal failure
        rather than an infinite retry loop.

        On HTTP 200, parses the JSON body via
        ``_parse_mistral_response`` which handles Mistral's error
        envelope and tool-call shape.
        """
        url = self._get_chat_completions_url()
        headers = self._get_auth_headers()
        if stream:
            headers["Accept"] = "text/event-stream"

        max_retries = self._max_retries()
        last_error_msg = ""

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    req, timeout=self.config.timeout
                ) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    return self._parse_mistral_response(raw)

            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = (
                    body_bytes.decode("utf-8", errors="replace")
                    if body_bytes else ""
                )

                # Parse Mistral error envelope
                err_data: dict | None = None
                try:
                    if body_text:
                        err_data = json.loads(body_text)
                except (json.JSONDecodeError, ValueError):
                    pass

                # Mistral envelope: {"object": "error", "message": ...}
                if isinstance(err_data, dict) and err_data.get("object") == "error":
                    err_msg = err_data.get("message", body_text)
                elif isinstance(err_data, dict) and "error" in err_data:
                    inner = err_data["error"]
                    if isinstance(inner, dict):
                        err_msg = inner.get("message", str(inner))
                    else:
                        err_msg = str(inner)
                elif isinstance(err_data, dict) and "message" in err_data:
                    err_msg = err_data["message"]
                else:
                    err_msg = body_text[:500] or f"HTTP {status_code}"

                last_error_msg = err_msg

                # Retryable: 429 (rate limit) and 502/503/504 (transient
                # server errors). 500 is also retryable per Mistral docs.
                retryable = (
                    status_code == 429
                    or status_code in (500, 502, 503, 504)
                )

                if retryable and attempt < max_retries:
                    # Honor Retry-After when parseable; otherwise
                    # exponential backoff with full jitter.
                    retry_after_raw = e.headers.get("Retry-After", "")
                    retry_after: float | None = None
                    if retry_after_raw:
                        try:
                            retry_after = float(retry_after_raw)
                        except (ValueError, TypeError):
                            retry_after = None

                    if retry_after is None:
                        base = self._BACKOFF_BASE * (2 ** attempt)
                        retry_after = min(base, self._BACKOFF_CAP)
                        retry_after += random.uniform(0, retry_after * 0.2)

                    retry_after = min(max(retry_after, 1.0), self._BACKOFF_CAP)

                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        # Always surface the first 2 retries — the user
                        # must see the harness is patiently waiting.
                        print(
                            f"  [Mistral] {status_code} — {err_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )

                    time.sleep(retry_after)
                    continue

                # Non-retryable OR exhausted retries
                if status_code == 401:
                    raise RuntimeError(
                        "Mistral authentication failed. Check your "
                        "MISTRAL_API_KEY environment variable."
                    ) from e
                if status_code == 404:
                    raise RuntimeError(
                        f"Mistral model not found: {err_msg}. "
                        f"Use a current model (mistral-small-latest, "
                        f"mistral-medium-latest)."
                    ) from e
                if status_code == 422:
                    raise RuntimeError(
                        f"Mistral validation error: {err_msg}. "
                        f"Check tools count (max 128), reasoning_effort "
                        f"value, or unknown OpenAI-only fields."
                    ) from e

                raise RuntimeError(
                    f"Mistral API error {status_code}: {err_msg}"
                ) from e

            except urllib.error.URLError as e:
                # Network-level error — retry once with backoff, then
                # surface as RuntimeError so the agent loop sees it.
                if attempt < max_retries:
                    backoff = self._BACKOFF_BASE * (2 ** attempt)
                    backoff = min(backoff, self._BACKOFF_CAP)
                    backoff += random.uniform(0, backoff * 0.2)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [Mistral] connection error ({e.reason}), "
                            f"retrying in {backoff:.0f}s"
                        )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(
                    f"Mistral connection error: {e.reason}"
                ) from e

        # Should not reach here — the loop either returns or raises.
        raise RuntimeError(
            f"Mistral API retries exhausted. Last error: {last_error_msg}"
        )

    def _max_retries(self) -> int:
        """Resolve the retry budget (env override > class default)."""
        raw = os.environ.get("MISTRAL_MAX_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_RETRIES
