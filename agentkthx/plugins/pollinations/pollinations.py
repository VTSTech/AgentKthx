"""
⚛️ AgentKthx — Pollinations Unified Gateway Backend

Backend implementation for the Pollinations API (OpenAI Chat-Completions
compatible, with deliberate wire-format deltas).

Pollinations serves its unified gateway at
``https://gen.pollinations.ai/v1`` — the primary AgentKthx surface. The
gateway IS an OpenAI-compatible endpoint: Chat Completions, Responses,
Images, Audio, Embeddings, and Realtime all follow OpenAI wire shapes.
Deltas vs the OpenAI spec this backend handles:

  - Model ids are ``provider/model`` (``openai/gpt-5.4-nano``) — the
    same pattern as OpenRouter. Bare aliases (``openai``,
    ``gpt-5.4-nano``) resolve server-side, but we resolve locally via
    the ``/v1/models`` alias map when available.
  - The API key is OPTIONAL — the only AgentKthx backend that runs
    with no key at all (legacy anonymous text surface, IP-rate-limited,
    serves the ``openai-fast`` GPT-OSS-20B tier). This makes Pollinations
    the natural zero-config bootstrap backend.
  - Payment failure is **402 PAYMENT_REQUIRED** (pollen budget
    exhausted), not OpenAI's 429/insufficient_quota — must trigger
    provider fallback, never retry and never re-auth.
  - Error envelope is Pollinations-specific:
    ``{"status", "success": false, "error": {"code", "message",
    "timestamp", "details", "requestId"}}`` — the ``requestId`` is what
    support asks for, so it is surfaced in every raised error.
  - Model cards carry live **health telemetry** (``health.success_rate``,
    ``health.requests``) unique to Pollinations — used for health-aware
    fallback ordering (``healthy_fallbacks()``), no other backend offers
    this.
  - ``safe`` body param / ``Pollinations-Safe`` header accepts a
    comma-separated filter list (``privacy,secrets,sexual,violence,
    shield``) with shorthands ``true`` = ``privacy,secrets`` and
    ``nsfw`` = ``sexual,violence``. Defaults OFF. Moderation blocks
    surface as ``422 content_policy_violation``.
  - Completed text streams MUST carry a valid usage chunk — the platform
    rejects malformed streams server-side, so absence after ``[DONE]``
    implies transport tampering (we warn, not error).
  - ``seed`` passes through natively (no ``random_seed`` aliasing).
  - ``reasoning_effort`` passes through (minimal/low/medium/high);
    reasoning summaries (``reasoning.summary``) are unsupported
    platform-wide and never sent.

This backend inherits the shared cloud-backend boilerplate from
``CloudBackend`` (base URL resolution, auth headers, catalog-driven
model defaults, ``is_running()``, ``test_tool_support()``), with ONE
deliberate deviation: ``__init__`` does NOT hard-require an API key
(the anonymous surface works keyless), so it bypasses
``CloudBackend.__init__`` and calls ``OpenAICompatibleBackend.__init__``
directly. Pollinations-specific overrides remain here:

  - ``POLLINATIONS_MODELS`` catalog (from
    docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md §Model Families)
  - ``_get_chat_completions_url()`` — ``/chat/completions``
  - ``list_models()`` — queries public ``GET /v1/models`` (311 cards,
    no auth required), filters ``category == "text"``, caches live
    cards + builds the alias map; merges static catalog as fallback
  - ``normalize_model_id()`` — provider/model passthrough + alias
    resolution
  - ``healthy_fallbacks()`` — health.success_rate descending, price
    ascending (Pollinations-exclusive capability)
  - ``_make_api_request()`` / ``_iter_sse_lines()`` — Pollinations
    error envelope parsing, 402 budget-exhausted mapping (non-retryable),
    Retry-After-capped 429/5xx backoff, ARCH-03 context-length recovery
  - ``get_balance()`` — pollen balance probe (GET /account/balance)

Endpoints used:
  - POST /chat/completions  → OpenAI Chat Completions (tools, streaming)
  - GET  /models            → model discovery (public, rich cards + health)
  - GET  /account/balance   → pollen balance inspection

Configuration:
  POLLINATIONS_API_KEY       — API key (OPTIONAL; empty = anonymous tier)
  POLLINATIONS_BASE_URL      — API base URL (default: https://gen.pollinations.ai/v1)
  POLLINATIONS_DEFAULT_MODEL — Default model (default: openai/gpt-5.4-nano)
  POLLINATIONS_FALLBACK_MODEL — Offline fallback chain anchor (z-ai/glm-5.3-flash)
  POLLINATIONS_SAFE          — Safety filters ("" | true | nsfw | comma list)
  POLLINATIONS_FREE_ONLY     — Only zero-priced models (default: false)
  POLLINATIONS_MAX_RETRIES   — Retry budget override (default: 5)

Usage:
  # CLI — keyless smoke test (anonymous tier)
  agentkthx run "hello" --backend poll

  # CLI — keyed
  agentkthx chat --backend pollinations --model openai/gpt-5.4-nano
  agentkthx chat --backend poll --model z-ai/glm-5.3-flashx

  # Python API
  from agentkthx import Agent
  agent = Agent(model="openai/gpt-5.4-nano", backend="pollinations",
                tools=["calculator"])
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
from typing import Any, Generator

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.backends.base import BackendConfig
from agentkthx.core.types import BackendType, ApiMode
from agentkthx.core.models import Tool
from agentkthx.config import (
    POLLINATIONS_BASE_URL,
    POLLINATIONS_DEFAULT_MODEL,
    POLLINATIONS_FALLBACK_MODEL,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pollinations model catalog (offline fallback)
# ─────────────────────────────────────────────────────────────────────────────
# Sourced from docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md §Model
# Families (September 2026). Keys are FULL provider/model ids — Pollinations'
# canonical form (same pattern as OpenRouter). The live /v1/models catalog
# (311 cards, public, no auth) is the source of truth at runtime; this static
# catalog is the offline fallback when the gateway is unreachable, and the
# anchor for _get_model_defaults() context lengths.
#
# AgentKthx catalog schema (must match CloudBackend's expectation):
#   context_length, default_max_tokens, default_temperature, pricing
#
# Pricing is pollen per 1M tokens (cards publish per-single-token —
# 0.00000015 pollen/token == 0.15 pollen/1M). Context lengths are
# card-verified where the reference documents them (gpt-5.4-nano 400K,
# glm-5.3-flashx 1M); others use the conservative 128K default and are
# overridden by live cards via list_models().
POLLINATIONS_MODELS: dict[str, dict] = {
    # ── OpenAI — nano is the cheap workhorse / platform default ─────────
    "openai/gpt-5.4-nano": {
        "context_length": 400_000,           # card-verified (Sep 2026)
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.15, "output": 0.9375},
        "family": "openai",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "Platform default; alias 'openai' resolves here",
    },
    "openai/gpt-5.4-mini": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.75, "output": 3.0},
        "family": "openai",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
    "openai/gpt-5.4": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 1.875, "output": 7.5},
        "family": "openai",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "Chat transform REMOVES sampling controls — don't rely on temperature",
    },
    "openai/gpt-oss-20b": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.05, "output": 0.3},
        "family": "openai",
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "Same weights as the legacy anonymous 'openai-fast' tier; forwards temperature/top_p",
    },
    # ── Z-AI — GLM family, cheap alternates (fallback chain anchor) ─────
    "z-ai/glm-5.3-flash": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.1, "output": 0.4},
        "family": "z-ai",
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "Cheap alternate family — POLLINATIONS_FALLBACK_MODEL",
    },
    "z-ai/glm-5.3-flashx": {
        "context_length": 1_000_000,         # 1M-token context
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.15, "output": 0.6},
        "family": "z-ai",
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "1M-token context — long-session / codebase-audit tier",
    },
    # ── DeepSeek — fast/cheap tier ──────────────────────────────────────
    "deepseek/deepseek-v4-flash": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.1, "output": 0.4},
        "family": "deepseek",
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
    # ── Anthropic — prompt-caching capable (512/1024-token min prefix) ──
    "anthropic/claude-sonnet-4.6": {
        "context_length": 200_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 1.875, "output": 9.375},
        "family": "anthropic",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "temperature/top_p mutually exclusive (temperature wins); supports json_schema NOT json_object",
    },
    "anthropic/claude-haiku-4.5": {
        "context_length": 200_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.625, "output": 3.125},
        "family": "anthropic",
        "supports_vision": True,
        "supports_function_calling": True,
    },
    # ── Google — flash tier; ':search' variants ship built-in tools ─────
    "google/gemini-3.7-flash": {
        "context_length": 1_000_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.1, "output": 0.4},
        "family": "google",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
        "note": "Prompt caching — but NOT cached when tools are present",
    },
    # ── Mistral — same models as the native Mistral backend ─────────────
    "mistralai/mistral-small-4": {
        "context_length": 262_144,           # 256K (matches native catalog)
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.2, "output": 0.5},
        "family": "mistralai",
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
    # ── Meta — Llama 4 ──────────────────────────────────────────────────
    "meta/llama-4-scout": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.075, "output": 0.3},
        "family": "meta",
        "supports_vision": True,
        "supports_function_calling": True,
    },
    # ── Qwen — flash tier, VL variants take image/video input ───────────
    "qwen/qwen3.8-flash": {
        "context_length": 128_000,
        "default_temperature": 0.7,
        "default_max_tokens": 8192,
        "pricing": {"input": 0.1, "output": 0.4},
        "family": "qwen",
        "supports_reasoning": True,
        "supports_function_calling": True,
    },
}

# Default model when none is specified. Resolved from env var → catalog.
POLLINATIONS_DEFAULT_MODEL_FALLBACK = "openai/gpt-5.4-nano"

# Doc-verified bare aliases → full provider/model ids. The live /v1/models
# catalog's per-card ``aliases[]`` arrays extend this at runtime (see
# list_models()); these seeds cover the keyless bootstrap path where the
# user types a bare alias before any catalog fetch has happened.
_STATIC_ALIASES: dict[str, str] = {
    "openai": "openai/gpt-5.4-nano",        # doc: "openai alias resolves to gpt-5.4-nano"
    "gpt-5.4-nano": "openai/gpt-5.4-nano",
    "gpt-oss": "openai/gpt-oss-20b",        # legacy anonymous-tier aliases
    "gpt-oss-20b": "openai/gpt-oss-20b",
}

# ``safe`` flag shorthands per the technical reference §Safety Controls.
_SAFE_SHORTHANDS = {
    "true": "privacy,secrets",
    "nsfw": "sexual,violence",
}
_SAFE_TOKENS = frozenset({"privacy", "secrets", "sexual", "violence", "shield"})


def _expand_safe_flag(raw: str) -> str | None:
    """Expand the POLLINATIONS_SAFE env value into a valid ``safe`` param.

    Accepts a shorthand (``true`` → ``privacy,secrets``, ``nsfw`` →
    ``sexual,violence``) or an explicit comma-separated list. Unknown
    tokens are dropped; if nothing survives, returns ``None`` (send no
    ``safe`` param — platform default is OFF).
    """
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _SAFE_SHORTHANDS:
        return _SAFE_SHORTHANDS[value]
    tokens = [t.strip() for t in value.split(",") if t.strip()]
    kept = [t for t in tokens if t in _SAFE_TOKENS]
    return ",".join(kept) if kept else None


def _pollen_to_per_million(per_token: Any) -> float | None:
    """Convert a card's per-single-token pollen price to per-1M for display.

    Card ``pricing`` fields are decimal strings per token (e.g.
    ``"0.00000015"``); the dashboard displays per-1M (0.15). Returns
    ``None`` for missing/unparseable values.
    """
    try:
        return round(float(per_token) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _card_is_free(card_or_meta: dict) -> bool:
    """True iff pricing is genuinely zero (free community models).

    Works on both live card shape (``pricing.promptTextTokens`` /
    ``completionTextTokens`` per-token strings) and static catalog shape
    (``pricing.input`` / ``pricing.output`` per-1M floats).
    """
    pricing = card_or_meta.get("pricing") or {}
    if "promptTextTokens" in pricing or "completionTextTokens" in pricing:
        prompt = _pollen_to_per_million(pricing.get("promptTextTokens"))
        completion = _pollen_to_per_million(pricing.get("completionTextTokens"))
        return bool(
            prompt is not None
            and completion is not None
            and prompt == 0.0
            and completion == 0.0
        )
    return (
        pricing.get("input", -1) == 0.0
        and pricing.get("output", -1) == 0.0
    )


def _env_flag(name: str) -> bool:
    """Read a boolean env flag live (runtime changes take effect)."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


# ─────────────────────────────────────────────────────────────────────────────
# PollinationsBackend
# ─────────────────────────────────────────────────────────────────────────────


class PollinationsBackend(CloudBackend):
    """
    Backend for the Pollinations unified gateway API.

    Inherits the shared cloud-backend patterns from ``CloudBackend``
    (base URL resolution, auth headers, catalog-driven
    ``_get_model_defaults``, ``test_tool_support()``) with ONE deliberate
    deviation: the API key is OPTIONAL, so ``__init__`` bypasses
    ``CloudBackend.__init__``'s hard key requirement (Pollinations'
    legacy anonymous text surface works with no key — the only AgentKthx
    backend that can run zero-config).

    Pollinations wire-format deltas vs the OpenAI Chat-Completions spec
    that this backend handles:

      1. ``provider/model`` ids with local alias resolution
         (``normalize_model_id``)
      2. 402 PAYMENT_REQUIRED = pollen budget exhausted — mapped to a
         distinct non-retryable RuntimeError so the resilience
         classifier triggers provider fallback instead of burning
         latency on retries
      3. Pollinations error envelope
         (``{"status", "success": false, "error": {...}}``) parsed for
         ``code`` + ``requestId``; OpenAI's bare ``{"error": {...}}``
         wrapper tolerated too
      4. ``Retry-After`` honored on 429/503 — and CAPPED at 60s
         (ROB-16 lesson: an uncapped ``Retry-After: 3600`` once hung a
         sibling backend for an hour)
      5. ``safe`` safety-filter param injected from POLLINATIONS_SAFE
      6. ``seed`` passes through natively (no random_seed aliasing)
      7. Health-telemetry fallback ordering (``healthy_fallbacks()``) —
         sorts live text cards by ``health.success_rate`` descending,
         price ascending

    Usage:
        backend = get_backend("pollinations")
        backend = PollinationsBackend()               # keyless (anonymous)
        backend = PollinationsBackend(api_key="sk_...")
    """

    # CloudBackend required overrides — provider identity as class attrs
    MODELS = POLLINATIONS_MODELS
    _api_key_env_var = "POLLINATIONS_API_KEY"
    _default_base_url = POLLINATIONS_BASE_URL
    _default_model = POLLINATIONS_DEFAULT_MODEL or POLLINATIONS_DEFAULT_MODEL_FALLBACK
    _provider_label = "Pollinations"

    # Pollinations keys are sk_ (secret, canonical) or pk_ (app/legacy
    # publishable). Length is not documented precisely, so the floor is
    # permissive (8) — the prefix check catches the obvious junk that
    # ROB-21 was about, without rejecting legitimate short keys. The
    # KEYLESS path (empty) is always valid — that's the whole point.
    _MIN_API_KEY_LEN: int = 8
    _KEY_PREFIXES = ("sk_", "pk_")

    # Pollinations' context-length 400 bodies have NOT been probed yet
    # (probe_pollinations.sh is the next step). The reference sketch
    # suggested disabling the regexes (None) until a real body is
    # captured — but the shared _calculate_safe_max_tokens() calls
    # re.search() on the MAX/INPUT patterns unguarded, so None would
    # raise TypeError on a genuine context-length 400. Instead we KEEP
    # the inherited OpenAI-compatible defaults: the shared
    # _handle_context_length_400() guard ("context length" must appear
    # in the body) plus the regex-miss fallback (1/3 reduction) gives
    # graceful degradation with no false-positive truncation risk beyond
    # what every other backend already tolerates.
    # _CONTEXT_LENGTH_MAX_PATTERN / _INPUT_PATTERN: inherited defaults.
    # Pollinations doesn't separately report tool-input tokens (unknown
    # until probed) — treat as 0, matching the Gemini/Mistral pattern.
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = None

    # Retry policy — exponential backoff with full jitter, max 5 attempts.
    # Override with POLLINATIONS_MAX_RETRIES.
    _MAX_RETRIES: int = 5
    _BACKOFF_BASE: float = 1.0
    # ROB-16 (R07.07) lesson from OrcaRouter: cap honored Retry-After
    # values — an uncapped "Retry-After: 3600" would hang the agent for
    # an hour. Pollinations sends Retry-After on 429 AND 503.
    _BACKOFF_CAP: float = 60.0

    # ───────────────────────────────────────────────────────────────────
    # __init__ — CloudBackend boilerplate, MINUS the hard key requirement
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
        """Initialize a Pollinations backend (key OR keyless).

        Args:
            base_url: Override the default API base URL. If None, uses
                ``POLLINATIONS_BASE_URL`` env var, then
                ``https://gen.pollinations.ai/v1``.
            host, port: Alternative URL form — constructs
                ``https://{host}:{port}``. Used by some test setups.
            config: ``BackendConfig`` for timeout/max_retries.
            api_mode: ``OPENAI`` (default for cloud) or ``JEV``.
                ``OPENRE`` is rejected (forced to OPENAI).
            api_key: API key. If None, falls back to the
                ``POLLINATIONS_API_KEY`` env var. Empty/missing is VALID
                — Pollinations' legacy anonymous text surface needs no
                key (IP-rate-limited; the zero-config bootstrap path).
        """
        # Resolve base URL — priority: explicit arg > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"https://{host}:{port}"
        else:
            resolved_url = (
                os.environ.get("POLLINATIONS_BASE_URL")
                or self._default_base_url
            ).rstrip("/")

        # Cloud backends only support OPENAI / JEV. Reject OPENRE.
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode is None:
            forced_mode = ApiMode.OPENAI
        elif api_mode in (ApiMode.JEV, ApiMode.OPENAI):
            forced_mode = api_mode
        else:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [{self._provider_label}] API mode '{api_mode}' not supported — "
                    f"cloud backends only support OpenAI / JEV, forcing OPENAI"
                )
            forced_mode = ApiMode.OPENAI

        # ⚠️ Deliberate deviation from CloudBackend: skip its __init__
        # (which hard-raises ValueError when the API key is missing) and
        # call the grandparent directly. Pollinations is the ONE backend
        # that runs keyless on the legacy anonymous surface.
        OpenAICompatibleBackend.__init__(
            self,
            base_url=resolved_url,
            config=config,
            api_mode=forced_mode,
        )

        # SEC-15 pattern (R07.08): first-instance-wins env var — never
        # stomp AGENTKTHX_API_MODE if another backend already set it.
        if "AGENTKTHX_API_MODE" not in os.environ:
            os.environ["AGENTKTHX_API_MODE"] = forced_mode.value

        # API key — priority: explicit arg > env var. OPTIONAL: an empty
        # key selects the legacy anonymous text surface (IP-rate-limited,
        # openai-fast GPT-OSS-20B tier) rather than raising.
        env_value = os.environ.get(self._api_key_env_var, "")
        self._api_key = api_key or env_value

        if self._api_key and self._api_key.strip():
            key = self._api_key.strip()
            if len(key) < self._MIN_API_KEY_LEN or not key.startswith(self._KEY_PREFIXES):
                raise ValueError(
                    f"{self._api_key_env_var} appears invalid — expected an "
                    f"'sk_' secret key (or legacy 'pk_' key) of at least "
                    f"{self._MIN_API_KEY_LEN} chars, got {len(key)} chars "
                    f"starting with {key[:3]!r}. Fix your "
                    f"{self._api_key_env_var} environment variable, or unset "
                    f"it entirely to use the keyless anonymous tier."
                )
            self._api_key = key
        else:
            self._api_key = ""
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    "  [Pollinations] No API key — using the legacy anonymous "
                    "text surface (IP-rate-limited, openai-fast tier). Set "
                    "POLLINATIONS_API_KEY for keyed access."
                )

        # R06.57: Persisted safe max_tokens after a context-length 400.
        self._context_safe_max_tokens: int | None = None

        # Live catalog caches — populated by list_models() (public
        # GET /v1/models). Empty until the first catalog fetch.
        self._model_cards: dict[str, dict] = {}
        self._alias_map: dict[str, str] = dict(_STATIC_ALIASES)

    # ───────────────────────────────────────────────────────────────────
    # Identity
    # ───────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        return BackendType.POLLINATIONS

    def _catalog_family_name(self) -> str:
        return "pollinations"

    def _catalog_backend_name(self) -> str:
        return "pollinations"

    def is_running(self) -> bool:
        """Always True — the anonymous surface needs no key.

        Unlike every other cloud backend (key == available), Pollinations
        works with zero configuration: the legacy anonymous text surface
        serves the openai-fast tier with no credential at all. With a key
        the full gateway (311 models + media generation) opens up either
        way, actual availability is discovered on the first request.
        """
        return True

    # _get_auth_headers() is inherited from CloudBackend — it only adds
    # the Authorization header when self._api_key is truthy, which is
    # exactly the keyless behavior we need.

    # ───────────────────────────────────────────────────────────────────
    # URL construction
    # ───────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """Full URL for the ``/chat/completions`` endpoint.

        The base URL already includes ``/v1`` — appending
        ``/chat/completions`` produces the canonical
        ``https://gen.pollinations.ai/v1/chat/completions``.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the public ``GET /models`` discovery endpoint."""
        return f"{self._base_url.rstrip('/')}/models"

    def _get_balance_url(self) -> str:
        """Full URL for ``GET /account/balance`` (pollen balance)."""
        return f"{self._base_url.rstrip('/')}/account/balance"

    # ───────────────────────────────────────────────────────────────────
    # Model-id handling — provider/model ids + alias resolution
    # ───────────────────────────────────────────────────────────────────

    def normalize_model_id(self, model: str) -> str:
        """Normalize a user-supplied model id to the canonical form.

        Pollinations ids are ``provider/model`` (same pattern as
        OpenRouter). Rules:

        - Already contains ``/`` → pass through unchanged (canonical
          form, including ``community/owner/model``).
        - Bare alias (``openai``, ``gpt-5.4-nano``, ``gpt-oss``) →
          resolve through the alias map (live ``/v1/models`` aliases[]
          merged over doc-verified static seeds).
        - Unknown bare name → pass through unchanged; the server
          resolves its own aliases (e.g. ``openai`` → gpt-5.4-nano) and
          404s cleanly if truly unknown.
        """
        if not model:
            return model
        if "/" in model:
            return model
        resolved = self._alias_map.get(model)
        if resolved and resolved != model:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Pollinations] alias '{model}' → '{resolved}'"
                )
            return resolved
        return model

    def _catalog_lookup(self, model: str) -> dict | None:
        """Look up a model in the static catalog.

        Tries the full ``provider/model`` key first, then the
        provider-stripped short name (reverse-matched against every
        catalog id). Returns ``None`` when not found.
        """
        meta = self.MODELS.get(model)
        if meta is not None:
            return meta
        stripped = model.split("/")[-1] if "/" in model else model
        if stripped == model:
            # bare name — direct key already missed above
            return None
        for full_id, m in self.MODELS.items():
            if full_id.split("/")[-1] == stripped:
                return m
        return None

    # ───────────────────────────────────────────────────────────────────
    # list_models — public GET /v1/models merged over static catalog
    # ───────────────────────────────────────────────────────────────────

    def _fetch_model_cards(self) -> dict[str, dict]:
        """Fetch live model cards from the public ``GET /v1/models``.

        The catalog endpoint requires NO auth (sending a bearer key
        returns the same data, some endpoints add per-account fields) —
        so this works in keyless anonymous mode too. Returns a dict of
        ``{id: card}``; empty dict on any failure (caller falls back to
        the static catalog).
        """
        url = self._get_models_url()
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Pollinations] Model discovery failed ({e}), using static catalog")
            return {}
        except Exception as e:  # malformed body, decode errors, ...
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Pollinations] Model discovery error ({e}), using static catalog")
            return {}

        cards: dict[str, dict] = {}
        for m in result.get("data", []) or []:
            mid = m.get("id", "")
            if mid:
                cards[mid] = m
        return cards

    def list_models(self) -> list[dict]:
        """List available Pollinations models.

        Queries the public ``GET /v1/models`` catalog (311 cards,
        verified 2026-09-27) and surfaces the ``category == "text"``
        cards — image/video/audio/embedding/3d cards are not chat
        backends. Falls back to the static ``POLLINATIONS_MODELS``
        catalog when the gateway is unreachable.

        Side effects (cached for normalize_model_id / _get_model_defaults
        / healthy_fallbacks):
          - ``self._model_cards`` — ``{id: card}`` live card cache
          - ``self._alias_map`` — bare alias → full id (live aliases[]
            merged over doc-verified static seeds)

        Each entry is shaped for AgentKthx's discovery UI:
        ``{"name", "size", "details": {family, backend, context_length,
        free_tier, is_chat_model, pricing (per-1M), health}}``.
        """
        live_cards = self._fetch_model_cards()
        if live_cards:
            self._model_cards = live_cards
            # alias map: live aliases[] over static seeds; catalog
            # short-names as a third layer.
            merged = dict(_STATIC_ALIASES)
            for cid, card in live_cards.items():
                for alias in card.get("aliases", []) or []:
                    if alias and "/" not in alias:
                        merged[alias] = cid
            for full_id in self.MODELS:
                short = full_id.split("/")[-1]
                merged.setdefault(short, full_id)
            self._alias_map = merged
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Pollinations] API returned {len(live_cards)} cards, "
                    f"{len(self._alias_map)} aliases resolvable"
                )

        models: list[dict] = []
        seen: set[str] = set()

        # Live cards first (confirmed available, fresh context lengths +
        # health telemetry) — text category only for the chat backend.
        for cid in sorted(self._model_cards):
            card = self._model_cards[cid]
            if card.get("category") not in (None, "text"):
                continue
            seen.add(cid)
            models.append(self._card_to_entry(cid, card))

        # Static catalog entries not covered by the live catalog
        # (offline fallback keeps the backend usable).
        for name in sorted(self.MODELS.keys()):
            if name in seen:
                continue
            seen.add(name)
            meta = self.MODELS[name]
            models.append({
                "name": name,
                "size": 0,
                "details": {
                    "family": meta.get("family", name.split("/")[0]),
                    "backend": self._catalog_backend_name(),
                    "context_length": meta.get(
                        "context_length", self._DEFAULT_CONTEXT_FALLBACK
                    ),
                    "free_tier": _card_is_free(meta),
                    "is_chat_model": True,
                    "pricing": meta.get("pricing", {}),
                },
            })

        if _env_flag("POLLINATIONS_FREE_ONLY"):
            models = [m for m in models if m["details"].get("free_tier")]

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Pollinations] Total: {len(models)} chat models listed")

        return models

    def _card_to_entry(self, cid: str, card: dict) -> dict:
        """Shape a live Pollinations card into AgentKthx's model dict."""
        pricing_raw = card.get("pricing", {}) or {}
        pricing = {}
        prompt_per_m = _pollen_to_per_million(pricing_raw.get("promptTextTokens"))
        completion_per_m = _pollen_to_per_million(pricing_raw.get("completionTextTokens"))
        if prompt_per_m is not None:
            pricing["input"] = prompt_per_m
        if completion_per_m is not None:
            pricing["output"] = completion_per_m
        ctx = card.get("context_length")
        if not isinstance(ctx, int) or ctx <= 0:
            meta = self._catalog_lookup(cid)
            ctx = (meta or {}).get("context_length", self._DEFAULT_CONTEXT_FALLBACK)
        return {
            "name": cid,
            "size": 0,
            "details": {
                "family": cid.split("/")[0],
                "backend": self._catalog_backend_name(),
                "context_length": ctx,
                "free_tier": _card_is_free(card),
                "is_chat_model": True,
                "pricing": pricing,
                "community": bool(card.get("community", False)),
                "health": card.get("health"),
                "input_modalities": card.get("input_modalities", ["text"]),
            },
        }

    def get_model_info(self, model: str) -> dict | None:
        """Get model information — live card > static catalog > safe default.

        CloudBackend's parent returns ``None`` for unknown models after
        STRIPPING the provider prefix (its lookup splits on "/"), which
        breaks provider-prefixed ids. This override tries the full id
        against the live card cache and the static catalog first, then
        falls back to a safe default entry (matching the ZAI/Mistral
        pattern) because Pollinations accepts any valid catalog id —
        including ones our static catalog has never seen.
        """
        model = self.normalize_model_id(model)

        # Live card cache first (freshest context + health)
        card = self._model_cards.get(model)
        if card is not None:
            return self._card_to_entry(model, card)

        # Static catalog
        meta = self._catalog_lookup(model)
        if meta is not None:
            return {
                "name": model,
                "size": 0,
                "details": {
                    "family": meta.get("family", model.split("/")[0]),
                    "backend": self._catalog_backend_name(),
                    "context_length": meta.get(
                        "context_length", self._DEFAULT_CONTEXT_FALLBACK
                    ),
                    "free_tier": _card_is_free(meta),
                    "is_chat_model": True,
                    "pricing": meta.get("pricing", {}),
                },
            }

        # Unknown model — Pollinations accepts it; return a safe default.
        return {
            "name": model,
            "size": 0,
            "details": {
                "family": model.split("/")[0],
                "backend": self._catalog_backend_name(),
                "context_length": self._DEFAULT_CONTEXT_FALLBACK,
                "free_tier": False,
                "is_chat_model": True,
            },
        }

    def _get_model_defaults(self, model: str) -> dict:
        """Return ``{temperature, max_tokens, context_length}`` for a model.

        Context-length priority: live card (``context_length`` field,
        card-accurate) → static catalog → 128K fallback. The inherited
        ``_apply_max_tokens_cap`` then applies the ``context // 32`` cap
        (ARCH-03) — with a 400K-context nano model that yields a 12K
        output ceiling, plenty for agentic loops while leaving 97% of
        context to input.
        """
        model = self.normalize_model_id(model)

        ctx: int | None = None
        card = self._model_cards.get(model)
        if card is not None and isinstance(card.get("context_length"), int):
            raw_ctx = card["context_length"]
            if raw_ctx > 0:
                ctx = raw_ctx
        meta = self._catalog_lookup(model)
        if ctx is None and meta:
            ctx = meta.get("context_length")
        if not ctx or not isinstance(ctx, int) or ctx <= 0:
            ctx = self._DEFAULT_CONTEXT_FALLBACK

        max_tokens = (meta or {}).get("default_max_tokens", 8192)
        temperature = (meta or {}).get("default_temperature", 0.7)

        return self._apply_max_tokens_cap(
            max_tokens, ctx, temperature=temperature
        )

    # ───────────────────────────────────────────────────────────────────
    # Health-aware fallback (Pollinations-exclusive capability)
    # ───────────────────────────────────────────────────────────────────

    def healthy_fallbacks(self, limit: int = 3, *, refresh: bool = False) -> list[str]:
        """Return up to ``limit`` model ids ordered by live upstream health.

        Sorts live text-category cards by ``health.success_rate``
        descending, then ``pricing.promptTextTokens`` ascending — the
        fallback chain prefers healthy, cheap upstreams with tool
        support, with zero static model list to drift out of date.

        Without live cards (offline / catalog unreachable) falls back to
        the static default → fallback pair.

        Args:
            limit: Chain length (default 3).
            refresh: Force a catalog re-fetch even if cards are cached.
        """
        if refresh or not self._model_cards:
            live_cards = self._fetch_model_cards()
            if live_cards:
                self._model_cards = live_cards
                # keep alias map in sync with the fresh cards
                self.list_models()

        if not self._model_cards:
            chain = [self._default_model, POLLINATIONS_FALLBACK_MODEL]
            return chain[:limit]

        def sort_key(cid: str):
            card = self._model_cards.get(cid, {})
            health = card.get("health") or {}
            rate = health.get("success_rate")
            rate = float(rate) if isinstance(rate, (int, float)) else 0.0
            pricing = card.get("pricing") or {}
            price = _pollen_to_per_million(pricing.get("promptTextTokens"))
            price = price if price is not None else 1_000_000.0
            return (-rate, price)

        candidates = [
            cid
            for cid, card in self._model_cards.items()
            if card.get("category") == "text"
            and not card.get("community", False)
            and card.get("tools", False)
        ]
        candidates.sort(key=sort_key)
        chain = candidates[:limit]
        if not chain:
            chain = [self._default_model]
        return chain

    # ───────────────────────────────────────────────────────────────────
    # Request construction — Pollinations wire-format deltas
    # ───────────────────────────────────────────────────────────────────

    def _build_pollinations_body(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        **kwargs,
    ) -> dict:
        """Build a Pollinations Chat-Completions request body.

        Starts from the shared ``_build_openai_body`` (identical wire
        format — tools, stream_options.include_usage, optional sampling
        fields, reasoning_effort pass-through) then applies the
        Pollinations deltas:

          - model id normalized to ``provider/model`` form
          - ``safe`` param injected when POLLINATIONS_SAFE expands to a
            non-empty filter list (live env read — runtime changes via
            ``agentkthx param set`` take effect without a restart)
          - ``seed`` passes through natively (NO random_seed aliasing —
            that's the Mistral delta, not this one)
          - ``tool_choice`` passes through with OpenAI semantics
            (``auto`` / ``none`` / ``required`` / named-function object);
            some upstreams reject forced choices with 400 — surfaced
            verbatim rather than pre-mapped
        """
        body = self._build_openai_body(
            model=self.normalize_model_id(model),
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            **kwargs,
        )

        # Safety filters — defaults OFF; only send when the env var
        # expands to a non-empty list.
        safe = _expand_safe_flag(os.environ.get("POLLINATIONS_SAFE", ""))
        if safe:
            body["safe"] = safe

        return body

    # ───────────────────────────────────────────────────────────────────
    # Response parsing — Pollinations error envelope + OpenAI shape
    # ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_error_envelope(body_text: str, status_code: int) -> tuple[str, str | None, str | None]:
        """Parse a Pollinations error body into ``(message, code, request_id)``.

        Primary shape (Pollinations envelope):
            ``{"status": 400, "success": false, "error": {"code": ...,
            "message": ..., "timestamp": ..., "details": {...},
            "requestId": "req_..."}}``

        Tolerated fallbacks: OpenAI's bare ``{"error": {...}}`` wrapper,
        a flat ``{"message": ...}`` dict, and non-JSON bodies (truncated
        to 500 chars). ``requestId`` is surfaced because that's exactly
        what Pollinations support asks for.
        """
        fallback = body_text[:500] if body_text else f"HTTP {status_code}"
        if not body_text:
            return fallback, None, None
        try:
            err_data = json.loads(body_text)
        except (json.JSONDecodeError, ValueError):
            return fallback, None, None
        if not isinstance(err_data, dict):
            return fallback, None, None

        err = err_data.get("error")
        if isinstance(err, dict):
            message = err.get("message") or str(err)
            code = err.get("code")
            request_id = err.get("requestId")
            return message, str(code) if code is not None else None, request_id
        if err is not None:
            return str(err), None, None
        if "message" in err_data:
            return str(err_data["message"]), None, None
        return fallback, None, None

    # Error classification per the technical reference §Error Codes —
    # drives retry-vs-fail decisions and the messages the agent loop
    # surfaces. 402 is the headline: pollen budget exhausted.
    _ERROR_CLASSES = {
        400: "bad_request",
        401: "auth_error",
        402: "budget_exhausted",
        403: "permission_error",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "content_or_param",
        429: "rate_limited",
        500: "server_error",
        502: "upstream_error",
        503: "degraded",
        504: "upstream_timeout",
    }

    def _classify_error(self, status_code: int) -> str:
        return self._ERROR_CLASSES.get(status_code, f"http_{status_code}")

    @staticmethod
    def _parse_pollinations_response(raw_response: dict) -> dict:
        """Parse a Pollinations Chat-Completions response.

        Returns a dict in the shape AgentKthx's agent loop expects:
        ``{content, tool_calls, finish_reason, usage, reasoning_content,
        raw}``.

        Pollinations' success shape is byte-for-byte OpenAI, so this
        mirrors the shared ``_parse_openai_response`` — with one extra
        guard: media models invoked through chat completions return a
        Markdown embed + public URL with ``usage: null`` (never a final
        usage chunk), which we normalize to zeroed usage rather than
        crashing the token tracker.
        """
        # Provider-side error on HTTP 200 — some gateways wrap upstream
        # failures this way. Handles both the Pollinations envelope and
        # OpenAI's bare wrapper.
        err_field = raw_response.get("error")
        if err_field:
            if isinstance(err_field, dict):
                err_msg = err_field.get("message") or str(err_field)
                err_code = err_field.get("code")
            else:
                err_msg = str(err_field)
                err_code = None
            code_str = f" (code={err_code})" if err_code is not None else ""
            raise RuntimeError(f"Provider error: {err_msg}{code_str}")

        choices = raw_response.get("choices", []) or []
        if not choices:
            raise RuntimeError(
                f"Pollinations API returned no choices in response: "
                f"{str(raw_response)[:300]}"
            )

        choice = choices[0]
        message = choice.get("message", {}) or {}

        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content", "") or ""
        raw_tool_calls = message.get("tool_calls") or []
        finish_reason = choice.get("finish_reason")

        # Parse OpenAI tool_calls format — arguments arrive as a JSON
        # string; tolerate malformed JSON via _raw_arguments fallback.
        parsed_tool_calls: list[dict] = []
        for i, tc in enumerate(raw_tool_calls):
            func = tc.get("function", {}) or {}
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                if args.strip():
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw_arguments": args}
                else:
                    args = {}
            elif not isinstance(args, dict):
                args = {"_raw_arguments": str(args)}
            parsed_tool_calls.append({
                "id": tc.get("id") or f"pollinations_tc_{i}",
                "name": func.get("name", ""),
                "arguments": args,
            })

        # usage: null on media-model chat responses — normalize to zeros.
        usage = raw_response.get("usage") or {}

        return {
            "content": content,
            "tool_calls": parsed_tool_calls,
            "finish_reason": finish_reason,
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
        """Generate a response from the Pollinations API.

        Always uses OpenAI Chat-Completions format. The ``think``
        parameter is ignored (Pollinations manages reasoning via
        ``reasoning_effort`` — callers should pass ``reasoning_effort``
        directly: minimal / low / medium / high).
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

        model = self.normalize_model_id(model)

        # Model defaults from card/catalog
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        if think is not None and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                "  [Pollinations] 'think' parameter ignored — "
                "use 'reasoning_effort' to control thinking depth"
            )

        # FREE_ONLY: reject priced models upfront (live flag read)
        if _env_flag("POLLINATIONS_FREE_ONLY"):
            card = self._model_cards.get(model)
            meta = self._catalog_lookup(model)
            is_free = _card_is_free(card) if card else (_card_is_free(meta) if meta else False)
            if not is_free:
                fallback = POLLINATIONS_FALLBACK_MODEL
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [Pollinations] FREE_ONLY mode — '{model}' is a "
                        f"priced model, switching to '{fallback}'"
                    )
                model = fallback

        body = self._build_pollinations_body(
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
        """JEV hook for Pollinations: route the decision call through
        the gateway's Bearer-authenticated ``/chat/completions`` endpoint.

        Keeps Pollinations' alias normalization + FREE_ONLY logic active
        when running decisions. The response shape is normalized to match
        generate(): ``{content, tool_calls, usage, raw}``.
        """
        model = self.normalize_model_id(model)
        body = self._build_pollinations_body(
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
    # Shared retry helpers
    # ───────────────────────────────────────────────────────────────────

    def _max_retries(self) -> int:
        """Resolve the retry budget (env override > class default)."""
        raw = os.environ.get("POLLINATIONS_MAX_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_RETRIES

    def _sleep_for_retry(self, attempt: int, retry_after_raw: str | None) -> float:
        """Compute and sleep the backoff for attempt ``attempt``.

        Honors ``Retry-After`` when parseable — **capped at
        ``_BACKOFF_CAP`` (60s)** per the ROB-16 lesson (an uncapped
        ``Retry-After: 3600`` once hung a sibling backend for an hour).
        Falls back to exponential backoff with full jitter. Returns the
        slept seconds (for tests).
        """
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
        # ROB-16: cap honored Retry-After — never sleep more than 60s
        # no matter what the header claims.
        retry_after = min(max(retry_after, 1.0), self._BACKOFF_CAP)
        time.sleep(retry_after)
        return retry_after

    def _raise_for_status(
        self,
        status_code: int,
        err_msg: str,
        code: str | None,
        request_id: str | None,
        cause: Exception | None = None,
    ) -> None:
        """Surface a non-retryable HTTP error as a RuntimeError.

        Error-class-specific messages (per the technical reference
        §Error Codes & Recovery) so the agent loop and the resilience
        classifier can act on them. ``requestId`` is included in every
        message — that's what Pollinations support asks for.
        """
        rid = f" [requestId={request_id}]" if request_id else ""
        klass = self._classify_error(status_code)

        if status_code == 401:
            raise RuntimeError(
                f"Pollinations authentication failed ({klass}){rid}: {err_msg}. "
                f"Check your POLLINATIONS_API_KEY, or unset it to use the "
                f"keyless anonymous tier."
            ) from cause
        if status_code == 402:
            # The headline failure mode: authenticated but out of
            # currency. NOT retryable — the resilience classifier should
            # treat this as permanent so provider fallback fires instead
            # of burning latency on retries that can never succeed.
            raise RuntimeError(
                f"Pollinations pollen budget exhausted ({klass}){rid}: {err_msg}. "
                f"Check GET /account/balance, top up at "
                f"https://pollinations.ai, or fall back to another backend. "
                f"Retrying a 402 cannot succeed."
            ) from cause
        if status_code == 403:
            raise RuntimeError(
                f"Pollinations permission denied ({klass}){rid}: {err_msg}. "
                f"Likely a paidOnly model on Quest Pollen or a key missing "
                f"the required scope."
            ) from cause
        if status_code == 404:
            raise RuntimeError(
                f"Pollinations model not found ({klass}){rid}: {err_msg}. "
                f"Verify the id against GET /v1/models — community models "
                f"need the full community/owner/model form; prefer full "
                f"provider/model ids over aliases."
            ) from cause
        if status_code == 422:
            detail = (
                "content_policy_violation — rephrase the prompt and do not "
                "retry unchanged"
                if code == "content_policy_violation"
                else "unsupported parameter combination — check the model "
                     "card's supported_parameters"
            )
            raise RuntimeError(
                f"Pollinations validation error ({klass}){rid}: {err_msg}. "
                f"Likely {detail}."
            ) from cause

        raise RuntimeError(
            f"Pollinations API error {status_code} ({klass}){rid}: {err_msg}"
        ) from cause

    # ───────────────────────────────────────────────────────────────────
    # _make_api_request — non-streaming POST with retry
    # ───────────────────────────────────────────────────────────────────

    def _make_api_request(self, body: dict, *, stream: bool = False) -> dict:
        """POST to ``/chat/completions`` with 429/5xx retry.

        Honors ``Retry-After`` on 429 AND 503 (capped at 60s — ROB-16).
        500/502/504 get exponential backoff with full jitter. 402 is
        NEVER retried (budget exhausted). ARCH-03 context-length 400
        recovery delegates to the shared ``_handle_context_length_400``.

        On HTTP 200, parses the JSON body via
        ``_parse_pollinations_response``.
        """
        url = self._get_chat_completions_url()
        headers = self._get_auth_headers()

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
                    return self._parse_pollinations_response(raw)

            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = (
                    body_bytes.decode("utf-8", errors="replace")
                    if body_bytes else ""
                )
                err_msg, err_code, request_id = self._parse_error_envelope(
                    body_text, status_code
                )
                last_error_msg = err_msg

                # ARCH-03: shared context-length 400 handler. Only on
                # the first attempt (don't loop forever on a 400).
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [Pollinations] Context length exceeded — "
                                f"reducing max_tokens {old_max} → {new_max} and retrying"
                            )
                        continue

                # Retryable: 429 (rate limit) + 500/502/503/504
                # (server/upstream/transient). 402 NEVER — pollen budget
                # exhaustion cannot be slept away.
                retryable = (
                    status_code == 429
                    or status_code in (500, 502, 503, 504)
                )
                if retryable and attempt < max_retries:
                    slept = self._sleep_for_retry(
                        attempt, e.headers.get("Retry-After", "")
                    )
                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        # Always surface the first 2 retries — the user
                        # must see the harness is patiently waiting.
                        print(
                            f"  [Pollinations] {status_code} — {err_msg}. "
                            f"Retrying in {slept:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    continue

                self._raise_for_status(
                    status_code, err_msg, err_code, request_id, cause=e
                )

            except urllib.error.URLError as e:
                # Network-level error — retry with backoff, then surface
                if attempt < max_retries:
                    slept = self._sleep_for_retry(attempt, None)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [Pollinations] connection error ({e.reason}), "
                            f"retrying in {slept:.0f}s"
                        )
                    continue
                raise RuntimeError(
                    f"Pollinations connection error: {e.reason}"
                ) from e

        # Should not reach here — the loop either returns or raises.
        raise RuntimeError(
            f"Pollinations API retries exhausted. Last error: {last_error_msg}"
        )

    # ───────────────────────────────────────────────────────────────────
    # Streaming — SSE with data: [DONE] terminator + 429/5xx retry
    # ───────────────────────────────────────────────────────────────────
    # _iter_sse_lines — abstract hook required by OpenAICompatibleBackend
    # ───────────────────────────────────────────────────────────────────
    #
    # The agent loop's streaming path goes:
    #
    #   agent._generate_stream() (streaming.py)
    #     → backend.generate_completions_stream()  (inherited from
    #       OpenAICompatibleBackend — parses SSE JSON chunks uniformly)
    #       → self._iter_sse_lines(url, body, headers)  (this method —
    #         concrete backend owns the HTTP transport + retry/recovery)
    #
    # The SSE wire format is byte-for-byte the OpenAI streaming format
    # (verified in the technical reference §Streaming), so no parsing
    # overrides are needed — we just feed the base class raw bytes.

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to the gateway's /chat/completions.

        Yields raw SSE line bytes for the inherited
        ``generate_completions_stream()`` to parse.

        Implements:
          - ARCH-03 context-length 400 recovery (shared helper, first
            attempt only)
          - 429 / 5xx retry honoring ``Retry-After`` — capped at 60s
            (ROB-16)
          - 402 budget-exhausted surfaced immediately (never retried)
          - ROB-06 deterministic response close on generator abandonment
        """
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
                response = urllib.request.urlopen(
                    req, timeout=self.config.timeout
                )
            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = (
                    body_bytes.decode("utf-8", errors="replace")
                    if body_bytes else ""
                )
                err_msg, err_code, request_id = self._parse_error_envelope(
                    body_text, status_code
                )
                last_error_msg = err_msg

                # ARCH-03: shared context-length 400 handler.
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [Pollinations-Stream] Context length exceeded — "
                                f"reducing max_tokens {old_max} → {new_max} and retrying"
                            )
                        continue

                # Retryable: 429 + 5xx (Retry-After honored, capped).
                retryable = (
                    status_code == 429
                    or status_code in (500, 502, 503, 504)
                )
                if retryable and attempt < max_retries:
                    slept = self._sleep_for_retry(
                        attempt, e.headers.get("Retry-After", "")
                    )
                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        print(
                            f"  [Pollinations-Stream] {status_code} — {err_msg}. "
                            f"Retrying in {slept:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    continue

                self._raise_for_status(
                    status_code, err_msg, err_code, request_id, cause=e
                )

            except urllib.error.URLError as e:
                if attempt < max_retries:
                    slept = self._sleep_for_retry(attempt, None)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [Pollinations-Stream] connection error "
                            f"({e.reason}), retrying in {slept:.0f}s"
                        )
                    continue
                raise RuntimeError(
                    f"Pollinations connection error: {e.reason}"
                ) from e

            # Success — yield raw SSE line bytes. The base class's
            # generate_completions_stream() handles JSON parsing,
            # [DONE] detection, and delta/tool_call extraction.
            # ROB-06: try/finally so the urllib response is closed
            # deterministically when the generator is abandoned
            # mid-iteration (Ctrl+C, consumer exception, or the base
            # class's break on [DONE]).
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

        # Should not reach here — the loop either yields + returns, or raises
        raise RuntimeError(
            f"Pollinations-Stream retries exhausted. Last error: {last_error_msg}"
        )

    # ───────────────────────────────────────────────────────────────────
    # generate_stream — thin text-delta wrapper (parity with siblings)
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
        """Stream generated text from Pollinations.

        Thin wrapper over the inherited ``generate_completions_stream``
        (from ``OpenAICompatibleBackend``). Yields just the text content
        deltas — the agent loop calls ``generate_completions_stream``
        directly to get the full dict-shape (delta + tool_calls +
        finish_reason + reasoning_content).
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

    def generate_completions_stream(self, *args, **kwargs):
        """Stream with Pollinations' usage-strictness warning attached.

        Unlike OpenAI, Pollinations fails completed text streams that
        lack a valid usage chunk SERVER-SIDE — so if a stream completes
        without any usage chunk reaching us, a proxy mangled the stream
        in transit. Per the technical reference §Implementation Notes we
        WARN (not error) in debug mode: the platform already rejected
        the malformed cases, so absence after ``[DONE]`` implies
        transport tampering, not a silent success.
        """
        saw_usage = False
        for chunk in super().generate_completions_stream(*args, **kwargs):
            if chunk.get("_usage"):
                saw_usage = True
            yield chunk
        if not saw_usage and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                "  [Pollinations-Stream] Stream completed without a usage "
                "chunk — Pollinations rejects malformed streams server-side, "
                "so this implies transport tampering (proxy mangling). "
                "Token counts for this turn may be under-reported."
            )

    # ───────────────────────────────────────────────────────────────────
    # Balance inspection — pollen economy
    # ───────────────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        """Fetch the pollen balance (``GET /account/balance``).

        Returns the parsed response — ``{"balance": float, ...}`` with
        ``accountBalance.{total, tier, paid}`` included only when the
        key has the ``account:usage`` scope.

        Raises:
            ValueError: keyless mode (anonymous tier has no balance) or
                a response that isn't JSON.
            RuntimeError: HTTP failures with the class-appropriate
                message (403 here is EXPECTED for unbudgeted keys
                without account:usage scope — the endpoint deliberately
                hides the account wallet).
        """
        if not self._api_key:
            raise ValueError(
                "get_balance() requires a key — the anonymous tier has "
                "no pollen balance. Set POLLINATIONS_API_KEY."
            )
        url = self._get_balance_url()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            err_msg, err_code, request_id = self._parse_error_envelope(
                body_text, e.code
            )
            self._raise_for_status(e.code, err_msg, err_code, request_id, cause=e)
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Pollinations connection error: {e.reason}"
            ) from e
        return {}  # unreachable — _raise_for_status always raises
