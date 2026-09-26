"""
⚛️ AgentKthx — OrcaRouter API Backend

OrcaRouter is a zero-markup gateway to 11 upstream LLM providers
(OpenAI, Anthropic, Google, DeepSeek, Grok, Qwen, Kimi, MiniMax,
ZAI, Kling, BytePlus). 200+ models via OpenAI Chat-Completions API.

Key features:
  - **Free tier**: 4 genuinely $0/token models (confirmed via
    ``GET /api/free-package/public``). Enforced via ``ORCAROUTER_FREE_ONLY``.
  - **Named routers**: ``orcarouter/auto`` (cheapest live model) and
    ``orcarouter/free`` (routes across free models by difficulty, never
    escapes to paid).
  - **Fallback chains**: ``extra_body.models`` (up to 5 models) with
    ``route="fallback"`` for cross-provider resilience. Configurable via
    ``ORCAROUTER_FALLBACK_MODELS`` env var.
  - **Per-request cost reporting**: ``X-OrcaRouter-Include-Cost: true``
    header adds ``usage.cost_usd`` to the response. Enabled by default
    via ``ORCAROUTER_INCLUDE_COST=true``.
  - **Workspace-scoped rate limits**: 429s are workspace-level, not
    per-key. Free-tier 429s use fixed-window retry (wait exactly
    ``retry_after_seconds``, retry once) — NOT exponential backoff.

Endpoints used:
  - POST /chat/completions  → OpenAI Chat Completions (tools, streaming)
  - GET  /models           → model discovery (anonymous — no auth required)
  - GET  /api/free-package/public → free-tier package info (anonymous)

Configuration (env vars — see config.py):
  ORCAROUTER_API_KEY            — API key (required, prefix: sk-orca-)
  ORCAROUTER_BASE_URL           — API base URL (default: https://api.orcarouter.ai/v1)
  ORCAROUTER_DEFAULT_MODEL      — default model/router (default: orcarouter/auto)
  ORCAROUTER_FREE_ONLY          — strict $0/token whitelist (default: false)
  ORCAROUTER_FREE_FALLBACK_MODEL — fallback on 403/429 (default: orcarouter/free)
  ORCAROUTER_FALLBACK_MODELS    — comma-separated fallback chain (default: empty)
  ORCAROUTER_INCLUDE_COST       — per-request cost reporting (default: true)

Usage:
  # CLI
  agentkthx chat --backend orcarouter --model openai/gpt-4o-mini --tools calculator
  agentkthx chat --backend orca --model orcarouter/free
  agentkthx run "What is 15 * 8?" --backend orcarouter --model orcarouter/auto

  # Python API
  from agentkthx import Agent
  agent = Agent(model="openai/gpt-4o-mini", backend="orcarouter", tools=["calculator"])
  result = agent.run("What is 15 * 8?")

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Generator

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.base import BackendConfig
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode
from agentkthx.core.models import Tool, ToolParam
from agentkthx.config import (
    ORCAROUTER_BASE_URL,
    ORCAROUTER_API_KEY,
    ORCAROUTER_DEFAULT_MODEL,
    ORCAROUTER_FREE_ONLY,
    ORCAROUTER_FREE_FALLBACK_MODEL,
    ORCAROUTER_FALLBACK_MODELS,
    ORCAROUTER_INCLUDE_COST,
)


# ---------------------------------------------------------------------------
# Free-tier whitelist (R07.05)
# ---------------------------------------------------------------------------
#
# OrcaRouter's 4 genuinely-free models (verified via the anonymous
# GET /api/free-package/public endpoint). When ORCAROUTER_FREE_ONLY=true,
# only these model IDs (plus the orcarouter/free router) are accepted.
# Any other model raises a clear error directing the user to set
# ORCAROUTER_FREE_ONLY=false.
#
# Source: docs/ORCAROUTER_API_TECHNICAL_REFERENCE.md §Free Tier Behavior
# Last verified: 2026-09-26
ORCAROUTER_FREE_MODEL_WHITELIST: frozenset[str] = frozenset({
    "deepseek/deepseek-v4-flash-free",
    "orca/orcaverify-text1.0-free",
    "tencent/hy3-free",
    "z-ai/glm-5.3-flash-free",
    # The free router itself is always allowed under FREE_ONLY
    "orcarouter/free",
})


# Named routers — these are not models per se, they route to a model at
# request time. We list them here so the catalog includes them and so
# ORCAROUTER_FREE_ONLY=true accepts the free router.
_ORCA_NAMED_ROUTERS: frozenset[str] = frozenset({
    "orcarouter/auto",   # picks cheapest live chat model at request time
    "orcarouter/free",   # routes across free models by difficulty
})


# ---------------------------------------------------------------------------
# Free-tier error classification
# ---------------------------------------------------------------------------
#
# OrcaRouter's free-tier errors come in two flavors:
#
# **Retryable (rate-limited)** — wait ``Retry-After`` seconds and retry once.
# Fixed-window rate limiting, NOT exponential backoff.
#   - ``error.metadata.reason == "err_free_rate"`` (HTTP 429)
#   - ``error.code == "free_rate_limited"`` (HTTP 429)
# These mean: "your workspace hit the per-minute or per-UTC-day request cap.
# Wait for the window to reset, then retry."
#
# **Terminal (account-level)** — retrying the same free model is pointless.
# The free allowance is exhausted or the workspace isn't eligible.
#   - ``error.metadata.reason == "err_free_used"`` (HTTP 402)
#     Means: "free allowance used up — top up balance to keep going"
#     OR: "workspace not eligible for free tier (GitHub account not 'established')"
#   - ``error.metadata.reason == "err_free_access_denied"`` (HTTP 429)
#     Means: "workspace owner hasn't linked established GitHub account"
#   - ``error.code == "free_quota_exhausted"`` (HTTP 403)
#     Means: "orcarouter/free had no free model available (all saturated)"
#   - ``error.code == "err_free_prompt_cap"`` (HTTP 400)
#     Means: "per-request prompt-token cap exceeded (shorten prompt, not retryable)"
# For terminal errors, we should NOT swap to ``ORCAROUTER_FREE_FALLBACK_MODEL``
# and retry — if the user is already on ``orcarouter/free``, swapping to
# ``orcarouter/free`` is a no-op. And if they're on a specific free model
# like ``deepseek/deepseek-v4-flash-free``, the account-level gate applies
# to ALL free models, so swapping won't help. Surface the
# ``metadata.buy_credits_url`` clearly and terminate.
#
# Sources:
#   - docs/ORCAROUTER_API_TECHNICAL_REFERENCE.md §Free-tier error reasons
#   - https://docs.orcarouter.ai/routing/free-models (free-tier access requirements)
_FREE_RATE_RETRYABLE_REASONS = (
    "err_free_rate",  # per-minute or per-UTC-day rate window full
)
_FREE_RATE_RETRYABLE_CODES = (
    "free_rate_limited",  # HTTP 429 — same as err_free_rate
)
_FREE_RATE_TERMINAL_REASONS = (
    "err_free_used",            # free allowance used up OR workspace not eligible
    "err_free_access_denied",   # GitHub account not linked / not "established"
)
_FREE_RATE_TERMINAL_CODES = (
    "free_quota_exhausted",  # orcarouter/free had no free model available
    "err_free_prompt_cap",   # per-request prompt-token cap exceeded (not retryable)
)


def _is_free_rate_limited(err_str: str) -> bool:
    """Detect OrcaRouter free-tier errors (any flavor — retryable OR terminal).

    Returns True if the error string matches any free-tier-specific error
    reason or error code. Use this when you want to know "is this a
    free-tier error at all?" (e.g. for surfacing buy_credits_url).

    For the retry-vs-terminate decision, use ``_is_free_rate_retryable()``
    or ``_is_free_rate_terminal()`` instead.
    """
    err_lower = err_str.lower()
    for pat in (_FREE_RATE_RETRYABLE_REASONS + _FREE_RATE_TERMINAL_REASONS):
        if pat in err_lower:
            return True
    for pat in (_FREE_RATE_RETRYABLE_CODES + _FREE_RATE_TERMINAL_CODES):
        if pat in err_lower:
            return True
    return False


def _is_free_rate_retryable(err_str: str) -> bool:
    """Detect OrcaRouter free-tier RATE-LIMIT errors (retryable).

    These are HTTP 429s where the workspace hit the per-minute or
    per-UTC-day request cap. The fix: wait ``Retry-After`` seconds
    (fixed-window retry, NOT exponential) and retry once.

    Returns False for terminal free-tier errors (``err_free_used``,
    ``free_quota_exhausted``, ``err_free_access_denied``,
    ``err_free_prompt_cap``) — those don't benefit from retrying.
    """
    err_lower = err_str.lower()
    for pat in _FREE_RATE_RETRYABLE_REASONS:
        if pat in err_lower:
            return True
    for pat in _FREE_RATE_RETRYABLE_CODES:
        if pat in err_lower:
            return True
    return False


def _is_free_rate_terminal(err_str: str) -> bool:
    """Detect OrcaRouter free-tier TERMINAL errors (NOT retryable).

    These mean the free allowance is exhausted or the workspace isn't
    eligible for the free tier. Retrying the same free model (or swapping
    to ``orcarouter/free``) won't help — the account-level gate applies
    to ALL free models.

    The right action is to surface ``metadata.buy_credits_url`` and
    terminate the run with a clear error message.

    Returns False for retryable rate-limit errors (``err_free_rate``,
    ``free_rate_limited``).
    """
    err_lower = err_str.lower()
    for pat in _FREE_RATE_TERMINAL_REASONS:
        if pat in err_lower:
            return True
    for pat in _FREE_RATE_TERMINAL_CODES:
        if pat in err_lower:
            return True
    return False


def _extract_buy_credits_url(err_str: str) -> str | None:
    """Extract ``metadata.buy_credits_url`` from an OrcaRouter error response.

    OrcaRouter's free-tier rejection errors always include a
    ``buy_credits_url`` field pointing to the billing page. Surface it
    in the user-facing error message so the user knows where to top up.
    """
    import re
    m = re.search(r'"buy_credits_url"\s*:\s*"([^"]+)"', err_str)
    return m.group(1) if m else None


def _is_free_model(model_id: str) -> bool:
    """Return True if the model ID is in the OrcaRouter free-tier whitelist.

    Strips provider prefix if present, then checks against
    ``ORCAROUTER_FREE_MODEL_WHITELIST`` and the ``orcarouter/free`` router.
    """
    # The free router is always "free" (it never escapes to paid)
    if model_id == "orcarouter/free":
        return True
    # The auto router is NOT free (picks cheapest, which may be paid)
    if model_id == "orcarouter/auto":
        return False
    # Strip provider prefix if present (e.g. "deepseek/deepseek-v4-flash-free")
    # — the whitelist already has the prefixed form, but be tolerant.
    return model_id in ORCAROUTER_FREE_MODEL_WHITELIST


def _parse_retry_after_seconds(err_str: str, retry_after_header: str | None) -> float | None:
    """Extract the recommended wait time for a free-tier 429.

    OrcaRouter sends the ``Retry-After`` header on 429s. For free-tier
    429s with ``metadata.reason == err_free_rate``, the ``Retry-After``
    value is the seconds remaining in the rate window.

    Returns:
        Wait time in seconds (float), or None if no Retry-After header.
    """
    if not retry_after_header:
        return None
    try:
        return float(retry_after_header)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# OrcaRouterBackend
# ---------------------------------------------------------------------------

class OrcaRouterBackend(CloudBackend):
    """Backend for OrcaRouter API (OpenAI Chat-Completions compatible).

    MAINT-02 (R07.05): inherits the shared cloud-backend boilerplate
    (base-URL resolution, API-key validation, ``_context_safe_max_tokens``
    init, ``is_running()``, default ``_get_auth_headers``,
    catalog-driven ``_get_model_defaults``, default ``test_tool_support``,
    default ``_is_free_model``) from ``CloudBackend``.

    OrcaRouter-specific overrides:
      - ``_provider_label = "OrcaRouter"``
      - ``_default_base_url = "https://api.orcarouter.ai/v1"``
      - ``_api_key_env_var = "ORCAROUTER_API_KEY"``
      - ``_get_chat_completions_url()`` — ``{base}/chat/completions``
      - ``_extra_auth_headers()`` — adds ``X-OrcaRouter-Include-Cost``
        when ``ORCAROUTER_INCLUDE_COST=true``.
      - ``_validate_api_key()`` — warns if key doesn't start with ``sk-orca-``.
      - ``list_models()`` — queries the anonymous ``/v1/models`` endpoint
        and caches for 1 hour. Filters to whitelist when
        ``ORCAROUTER_FREE_ONLY=true``.
      - ``_iter_sse_lines()`` — OrcaRouter-specific 429 free-rate
        handling (fixed-window retry, NOT exponential).
      - ``generate()`` / ``generate_stream()`` — apply FREE_ONLY gate,
        inject ``extra_body.models`` fallback chain from env var.
      - ``test_tool_support()`` — returns NATIVE for chat models.

    Usage:
        backend = get_backend("orcarouter")
        backend = OrcaRouterBackend(api_key="sk-orca-...")
    """

    # ─────────────────────────────────────────────────────────────────────
    # CloudBackend class-attribute overrides
    # ─────────────────────────────────────────────────────────────────────

    # OrcaRouter has no static model catalog — discovery is via the
    # anonymous /v1/models endpoint (cached 1 hour). We leave MODELS
    # empty here; list_models() builds the list dynamically.
    MODELS: dict[str, dict] = {}

    _api_key_env_var = "ORCAROUTER_API_KEY"
    _default_base_url = ORCAROUTER_BASE_URL
    _default_model = ORCAROUTER_DEFAULT_MODEL
    _provider_label = "OrcaRouter"

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """OrcaRouter's dedicated BackendType enum value (R07.05).

        Added to ``core/types.py`` in R07.05 alongside the plugin itself.
        The CLI's footer formatter reads ``backend_type.value`` to display
        the backend name in the status line — without this, the footer
        would show ``🔌 zai`` even when ``--backend orcarouter`` is used.
        """
        return BackendType.ORCAROUTER

    def _validate_api_key(self, key: str) -> None:
        """Warn (not error) if the API key doesn't start with ``sk-orca-``.

        OrcaRouter keys are issued with the ``sk-orca-`` prefix. A key
        without this prefix may still work (e.g. legacy keys, BYOK
        tokens) but is unusual — surface a debug-mode warning so users
        can spot typos like accidentally pasting an OpenAI key.
        """
        if not key.startswith("sk-orca-"):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [OrcaRouter] Warning: API key does not start with "
                    f"'sk-orca-' (got prefix {key[:8]!r}...). This may be a "
                    f"legacy key or a key from another provider — if you "
                    f"see 401 errors, check ORCAROUTER_API_KEY."
                )

    def _extra_auth_headers(self) -> dict:
        """Add OrcaRouter-specific headers.

        - ``X-OrcaRouter-Include-Cost: true`` when ORCAROUTER_INCLUDE_COST
          is enabled (default: true) — adds ``usage.cost_usd`` to the
          response so the agent can surface per-request cost.
        """
        headers: dict[str, str] = {}
        if ORCAROUTER_INCLUDE_COST:
            headers["X-OrcaRouter-Include-Cost"] = "true"
        return headers

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """OrcaRouter's OpenAI-compat endpoint.

        ``ORCAROUTER_BASE_URL`` already ends with ``/v1`` (no trailing
        slash) — we append ``/chat/completions`` to form the full URL.
        """
        return f"{self.base_url}/chat/completions"

    # ─────────────────────────────────────────────────────────────────────
    # Model discovery — anonymous /v1/models endpoint, cached 1 hour
    # ─────────────────────────────────────────────────────────────────────

    # Cache the model list for 1 hour to avoid hitting /v1/models on every
    # agent.run() — the list rarely changes within a session.
    _MODEL_CACHE_TTL_SECONDS = 3600  # 1 hour

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Live model cache: list[dict] + timestamp. Populated by list_models().
        # MAINT-02 (R07.05): CloudBackend.__init__ already set
        # _context_safe_max_tokens; we only add the model-cache fields here.
        self._model_cache: list[dict] | None = None
        self._model_cache_ts: float = 0.0

    def list_models(self) -> list[dict]:
        """Fetch the model list from OrcaRouter's anonymous /v1/models.

        The endpoint returns the standard OpenAI ``{object: list, data: [...]}``
        shape. Each model entry has ``id``, ``owned_by``, and
        ``supported_endpoint_types`` (["openai"], ["anthropic", "openai"], etc.).

        We cache the result for 1 hour to avoid hitting the endpoint on
        every agent run. When ``ORCAROUTER_FREE_ONLY=true``, the list is
        filtered to only the 4 free-tier models + the orcarouter/free router.

        Returns:
            List of ``{"name": ..., "size": 0, "details": {...}}`` dicts
            in the shape expected by the CLI's ``--backend models`` command.
        """
        # Return cache if fresh
        now = time.time()
        if (
            self._model_cache is not None
            and (now - self._model_cache_ts) < self._MODEL_CACHE_TTL_SECONDS
        ):
            if ORCAROUTER_FREE_ONLY:
                return [m for m in self._model_cache if _is_free_model(m["name"])]
            return list(self._model_cache)

        # Fetch fresh
        import urllib.request
        import urllib.error

        models: list[dict] = []
        try:
            url = f"{self.base_url}/models"
            # Note: /v1/models is anonymous — no Authorization header needed.
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))

            api_models = result.get("data", [])
            for m in api_models:
                name = m.get("id", "")
                if not name:
                    continue
                owned_by = m.get("owned_by", "unknown")
                # Build the catalog-style entry. We don't have pricing info
                # from /v1/models — OrcaRouter's pricing is per-request
                # via X-OrcaRouter-Include-Cost. Default context_length to
                # 128K (most modern chat models support this).
                models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": owned_by,
                        "backend": "orcarouter",
                        "context_length": 128000,  # default; overridden by per-model catalog if any
                        "owned_by": owned_by,
                        "supported_endpoint_types": m.get("supported_endpoint_types", ["openai"]),
                    },
                })

            # Always include the named routers (orcarouter/auto, orcarouter/free)
            # even if the API didn't list them — they're always available.
            existing_names = {m["name"] for m in models}
            for router in _ORCA_NAMED_ROUTERS:
                if router not in existing_names:
                    models.append({
                        "name": router,
                        "size": 0,
                        "details": {
                            "family": "orcarouter",
                            "backend": "orcarouter",
                            "context_length": 128000,
                            "owned_by": "orcarouter",
                            "supported_endpoint_types": ["openai"],
                            "is_named_router": True,
                        },
                    })

            # Update cache
            self._model_cache = models
            self._model_cache_ts = now

            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OrcaRouter] /v1/models returned {len(models)} models "
                      f"(+ {len(_ORCA_NAMED_ROUTERS)} named routers)")

        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OrcaRouter] Model discovery failed ({e}), using fallback list")
            # Fallback: return just the named routers + free whitelist
            for name in sorted(_ORCA_NAMED_ROUTERS | ORCAROUTER_FREE_MODEL_WHITELIST):
                models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": name.split("/")[0] if "/" in name else "orcarouter",
                        "backend": "orcarouter",
                        "context_length": 128000,
                    },
                })

        # Apply FREE_ONLY filter
        if ORCAROUTER_FREE_ONLY:
            models = [m for m in models if _is_free_model(m["name"])]

        return models

    def get_model_info(self, model: str) -> dict | None:
        """Look up model info from the cached /v1/models list.

        Returns None if the model is not in the cache (caller may want
        to call list_models() first to populate the cache).
        """
        if self._model_cache is None:
            # Trigger a fresh fetch
            self.list_models()
        if self._model_cache is None:
            return None
        for entry in self._model_cache:
            if entry["name"] == model:
                return entry
        return None

    def _get_model_defaults(self, model: str) -> dict:
        """Return per-model defaults for OrcaRouter.

        OrcaRouter forwards all sampling params to the upstream provider,
        so we use conservative defaults that work across all 11 providers:
          - temperature: 0.7
          - max_tokens: capped to context // 32 (the CloudBackend default)
          - context_length: 128K (the /v1/models endpoint doesn't expose
            per-model context, so we use a safe default; the agent will
            hit context-length 400 if the actual model has less, and
            _handle_context_length_400 will recover)
        """
        # Try cache first — the live /v1/models response may include
        # a context_length hint for some models.
        info = self.get_model_info(model)
        if info and "details" in info:
            ctx = info["details"].get("context_length", 128000)
        else:
            ctx = 128000

        # MAINT-02 (R07.05): use the CloudBackend._apply_max_tokens_cap
        # to apply the num_ctx // 32 cap + honor _context_safe_max_tokens
        # (set by _handle_context_length_400 after a prior 400).
        return self._apply_max_tokens_cap(
            max_tokens=4096,  # conservative default; upstream provider may cap further
            context_length=ctx,
            temperature=0.7,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Generation — apply FREE_ONLY gate, inject fallback chain
    # ─────────────────────────────────────────────────────────────────────

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
        """Generate a response from OrcaRouter.

        Wraps the inherited ``OpenAICompatibleBackend.generate_completions``
        to:
          1. Apply the ORCAROUTER_FREE_ONLY gate (reject paid models)
          2. Inject the fallback chain via extra_body (if configured)
          3. Pass through to the inherited OpenAI Chat-Completions impl
        """
        # JEV dispatch — if api_mode is JEV, route through generate_decision()
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=max_tokens if max_tokens is not None else 4096,
            think=think,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # FREE_ONLY gate
        if ORCAROUTER_FREE_ONLY and not _is_free_model(model):
            raise ValueError(
                f"ORCAROUTER_FREE_ONLY=true but model {model!r} is not in the "
                f"free-tier whitelist. Set ORCAROUTER_FREE_ONLY=false to use paid "
                f"models, or use one of: {sorted(ORCAROUTER_FREE_MODEL_WHITELIST)}"
            )

        # Use model defaults if not specified
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        # Inject fallback chain if configured
        extra_body = kwargs.pop("extra_body", None) or {}
        if ORCAROUTER_FALLBACK_MODELS:
            fallback_list = [m.strip() for m in ORCAROUTER_FALLBACK_MODELS.split(",") if m.strip()]
            if fallback_list:
                # OrcaRouter caps fallback chains at 5 models
                extra_body["models"] = fallback_list[:5]
                extra_body["route"] = "fallback"
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [OrcaRouter] Fallback chain enabled: {fallback_list[:5]}")

        # Pass extra_body through to the inherited implementation
        if extra_body:
            kwargs["extra_body"] = extra_body

        # Delegate to the inherited OpenAI Chat-Completions implementation
        # (which calls _get_chat_completions_url, _get_auth_headers,
        # _iter_sse_lines — all overridden above).
        return self._generate_with_auth(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def _generate_with_auth(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> dict:
        """Make a non-streaming POST to OrcaRouter's /chat/completions.

        Implements OrcaRouter-specific error recovery:
          - 429 + free_rate_limited → fixed-window retry (wait Retry-After, retry once)
          - 403 free_quota_exhausted → swap to FREE_FALLBACK_MODEL, retry once
          - 400 context length → reduce max_tokens, persist, retry once
          - 400 "does not support tools" → retry without tools (ReAct fallback)
          - 401/403 access_denied → fatal (raise RuntimeError)
        """
        import urllib.request
        import urllib.error

        url = self._get_chat_completions_url()
        headers = self._get_auth_headers()

        # Build OpenAI-format body
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Optional params
        for opt_key in ("stop", "top_p", "presence_penalty", "frequency_penalty",
                        "response_format", "seed", "reasoning_effort"):
            val = kwargs.get(opt_key)
            if val is not None:
                body[opt_key] = val

        # Tools
        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]
        if kwargs.get("tool_choice") is not None:
            body["tool_choice"] = kwargs["tool_choice"]

        # extra_body (fallback chain, etc.)
        extra_body = kwargs.get("extra_body")
        if extra_body:
            body["extra_body"] = extra_body

        # stream_options for usage reporting
        body["stream_options"] = {"include_usage": True}

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OrcaRouter] Request: model={model}, tools={len(tools) if tools else 0}")

        start_time = time.time()

        def _do_request(req_body: dict):
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=self.config.timeout)

        # Retry budget: 1 retry on free-rate-429, 1 on free_quota_exhausted,
        # 1 on context-length-400, 1 on no-tools fallback. Each is a
        # one-shot (not cumulative).
        for attempt in range(4):
            try:
                response = _do_request(body)
                result = json.loads(response.read().decode("utf-8"))

                # Surface X-Orca-* response headers in debug mode
                if os.environ.get("AGENTKTHX_DEBUG"):
                    for h in ("X-Orca-Request-Id", "X-Orca-Fallback-Level",
                              "X-Orca-Fallback-Model", "X-Orca-Router",
                              "X-Orca-Resolved-Model"):
                        val = response.headers.get(h)
                        if val:
                            print(f"  [OrcaRouter] {h}: {val}")

                latency_ms = (time.time() - start_time) * 1000

                # Parse OpenAI-format response
                choices = result.get("choices", [])
                if not choices:
                    return {
                        "content": "",
                        "tool_calls": [],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "latency_ms": latency_ms,
                        "raw": result,
                    }

                choice = choices[0]
                message = choice.get("message", {})
                finish_reason = choice.get("finish_reason", "stop")
                content = message.get("content", "") or ""
                tool_calls = message.get("tool_calls", []) or []
                usage = result.get("usage", {})

                # Surface cost_usd if cost reporting is enabled
                cost_usd = usage.get("cost_usd")
                if cost_usd is not None and os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [OrcaRouter] Cost: ${cost_usd:.6f}")

                return {
                    "content": content,
                    "tool_calls": tool_calls,
                    "finish_reason": finish_reason,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "cost_usd": cost_usd,
                    },
                    "latency_ms": latency_ms,
                    "raw": result,
                }

            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""
                error_msg = error_body.lower() if error_body else ""

                # ARCH-03 (R06.57): context-length 400 — delegate to shared helper
                if e.code == 400 and attempt == 0:
                    if self._handle_context_length_400(error_body, body):
                        print(f"  [OrcaRouter] Context length exceeded — reducing max_tokens and retrying")
                        continue

                # 400 "does not support tools" — retry without tools (ReAct fallback)
                if "does not support tools" in error_msg and body.get("tools") and attempt == 0:
                    print(
                        f"\n  \033[33m[OrcaRouter] Model {model!r} does not support tools — "
                        f"retrying without tool definitions\033[0m",
                        file=sys.stderr,
                    )
                    body = {k: v for k, v in body.items() if k != "tools"}
                    body.pop("tool_choice", None)
                    continue

                # Free-tier error handling — classify as retryable vs terminal.
                # See the long comment block above _FREE_RATE_RETRYABLE_REASONS
                # for the full taxonomy.
                if _is_free_rate_limited(error_msg):
                    # TERMINAL: account-level free-tier rejection (err_free_used,
                    # free_quota_exhausted, err_free_access_denied, err_free_prompt_cap).
                    # Retrying the same model (or swapping to orcarouter/free) won't
                    # help — the gate applies to ALL free models. Surface the
                    # buy_credits_url and terminate.
                    if _is_free_rate_terminal(error_msg):
                        buy_url = _extract_buy_credits_url(error_body) or \
                            "https://www.orcarouter.ai/console/billing"
                        # err_free_access_denied and err_free_used are both
                        # account-level gates — but the remedy differs slightly.
                        # err_free_access_denied specifically means "GitHub not
                        # linked / not established". err_free_used means the
                        # workspace's free allowance is used up OR the workspace
                        # doesn't meet either free-tier access gate (no $20+
                        # lifetime purchases AND no established GitHub account).
                        if "err_free_access_denied" in error_msg:
                            remedy = (
                                "Either (a) link an established GitHub account "
                                "to your OrcaRouter workspace at "
                                "https://www.orcarouter.ai/console/settings "
                                "(new GitHub accounts become eligible after a "
                                "waiting period), OR (b) add credits at "
                                f"{buy_url} — any paid purchase lifts the "
                                "always-free access gate. After $20+ in "
                                "lifetime purchases, the workspace's daily "
                                "request cap also rises from 50 to 800."
                            )
                        else:
                            # err_free_used / free_quota_exhausted — the
                            # workspace either exhausted its free allowance
                            # or doesn't meet the free-tier access gates.
                            remedy = (
                                "Either (a) add credits at " + buy_url + " "
                                "and call a specific paid model with wallet "
                                "billing to keep going (any paid purchase "
                                "also lifts the always-free access gate, "
                                "and $20+ in lifetime purchases raises the "
                                "workspace's daily request cap from 50 to "
                                "800), OR (b) if you've already added credits, "
                                "link an established GitHub account at "
                                "https://www.orcarouter.ai/console/settings "
                                "(new GitHub accounts become eligible after "
                                "a waiting period)."
                            )
                        raise RuntimeError(
                            f"OrcaRouter free-tier access denied.\n"
                            f"  Reason: your workspace's free allowance is "
                            f"used up, or your workspace doesn't meet the "
                            f"free-tier access gates (no $20+ lifetime paid "
                            f"purchases AND no established GitHub account "
                            f"linked).\n"
                            f"  Remedy: {remedy}\n"
                            f"  Raw error: {error_body}"
                        )

                    # RETRYABLE: rate-limit (err_free_rate, free_rate_limited).
                    # Wait Retry-After seconds (fixed-window, NOT exponential),
                    # retry once. Only swap to the fallback model if we're
                    # not already on it — otherwise the swap is a no-op.
                    if attempt < 3:
                        fallback = ORCAROUTER_FREE_FALLBACK_MODEL
                        current_model = body.get("model", model)

                        if current_model == fallback:
                            # Already on the fallback — just wait and retry the
                            # same model (the rate window will reset).
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(f"  [OrcaRouter] Already on fallback {fallback!r}; "
                                      f"not swapping, just waiting for rate window.")
                        else:
                            # Swap to the free router for the retry
                            body["model"] = fallback

                        retry_after = _parse_retry_after_seconds(
                            error_body,
                            e.headers.get("Retry-After") if e.headers else None,
                        )
                        if retry_after:
                            print(
                                f"\n  \033[33m[OrcaRouter] Free-tier rate-limited — waiting "
                                f"{retry_after:.1f}s then retrying with {body['model']!r}\033[0m",
                                file=sys.stderr,
                            )
                            time.sleep(retry_after)
                        else:
                            # No Retry-After — wait a short fixed delay (10s)
                            # before the single retry. This is the "free channel
                            # timed out upstream" case from the doc.
                            print(
                                f"\n  \033[33m[OrcaRouter] Free-tier rate-limited (no "
                                f"Retry-After) — waiting 10s then retrying with "
                                f"{body['model']!r}\033[0m",
                                file=sys.stderr,
                            )
                            time.sleep(10)
                        continue

                # 401 / 403 access_denied — fatal
                if e.code in (401, 403) and "access_denied" in error_msg:
                    raise RuntimeError(
                        f"OrcaRouter access denied (code={e.code}). "
                        f"Check ORCAROUTER_API_KEY permissions or workspace spend limits. "
                        f"Error: {error_body}"
                    )

                # All other HTTP errors
                raise RuntimeError(f"OrcaRouter HTTP error {e.code}: {error_body}")

            except urllib.error.URLError as e:
                raise RuntimeError(f"OrcaRouter connection error: {e.reason}")

        # Exhausted retries
        raise RuntimeError(
            f"OrcaRouter: exhausted retries (4 attempts) for model {model!r}. "
            f"Last body: {json.dumps(body)[:500]}"
        )

    def _iter_sse_lines(self, url: str, body: dict, headers: dict) -> Generator[bytes, None, None]:
        """Make a streaming POST to OrcaRouter's /chat/completions.

        Mirrors the non-streaming ``_generate_with_auth`` error recovery
        but for the SSE streaming path. The 429 free-rate and 400
        context-length handling are shared with the non-streaming path.
        """
        import urllib.request
        import urllib.error

        def _do_request(req_body: dict):
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=self.config.timeout)

        # R06.57-style: max 4 attempts (original + 3 retries on various errors)
        for attempt in range(4):
            try:
                response = _do_request(body)
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""
                error_msg = error_body.lower() if error_body else ""

                # Context-length 400 — delegate to shared helper
                if e.code == 400 and attempt == 0:
                    if self._handle_context_length_400(error_body, body):
                        print(f"  [OrcaRouter-Stream] Context length exceeded — "
                              f"reducing max_tokens and retrying")
                        continue

                # "does not support tools" — retry without tools (ReAct fallback)
                if "does not support tools" in error_msg and body.get("tools") and attempt == 0:
                    print(
                        f"\n  \033[33m[OrcaRouter-Stream] Model {body.get('model')!r} does not "
                        f"support tools — retrying without tool definitions\033[0m",
                        file=sys.stderr,
                    )
                    body = {k: v for k, v in body.items() if k != "tools"}
                    body.pop("tool_choice", None)
                    continue

                # Free-tier error handling — classify as retryable vs terminal.
                # Mirrors the non-streaming path's logic (see comment block above).
                if _is_free_rate_limited(error_msg):
                    # TERMINAL: account-level rejection. Don't retry — surface
                    # the buy_credits_url and terminate.
                    if _is_free_rate_terminal(error_msg):
                        buy_url = _extract_buy_credits_url(error_body) or \
                            "https://www.orcarouter.ai/console/billing"
                        if "err_free_access_denied" in error_msg:
                            remedy = (
                                "Either (a) link an established GitHub account "
                                "to your OrcaRouter workspace at "
                                "https://www.orcarouter.ai/console/settings "
                                "(new GitHub accounts become eligible after a "
                                "waiting period), OR (b) add credits at "
                                f"{buy_url} — any paid purchase lifts the "
                                "always-free access gate. After $20+ in "
                                "lifetime purchases, the workspace's daily "
                                "request cap also rises from 50 to 800."
                            )
                        else:
                            remedy = (
                                "Either (a) add credits at " + buy_url + " "
                                "and call a specific paid model with wallet "
                                "billing to keep going (any paid purchase "
                                "also lifts the always-free access gate, "
                                "and $20+ in lifetime purchases raises the "
                                "workspace's daily request cap from 50 to "
                                "800), OR (b) if you've already added credits, "
                                "link an established GitHub account at "
                                "https://www.orcarouter.ai/console/settings "
                                "(new GitHub accounts become eligible after "
                                "a waiting period)."
                            )
                        raise RuntimeError(
                            f"OrcaRouter free-tier access denied.\n"
                            f"  Reason: your workspace's free allowance is "
                            f"used up, or your workspace doesn't meet the "
                            f"free-tier access gates (no $20+ lifetime paid "
                            f"purchases AND no established GitHub account "
                            f"linked).\n"
                            f"  Remedy: {remedy}\n"
                            f"  Raw error: {error_body}"
                        )

                    # RETRYABLE: rate-limit. Wait Retry-After, retry once.
                    # Skip the swap if already on the fallback (no-op).
                    if attempt < 3:
                        fallback = ORCAROUTER_FREE_FALLBACK_MODEL
                        current_model = body.get("model", "")

                        if current_model == fallback:
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(f"  [OrcaRouter-Stream] Already on fallback "
                                      f"{fallback!r}; not swapping, just waiting.")
                        else:
                            body["model"] = fallback

                        retry_after = _parse_retry_after_seconds(
                            error_body,
                            e.headers.get("Retry-After") if e.headers else None,
                        )
                        if retry_after:
                            print(
                                f"\n  \033[33m[OrcaRouter-Stream] Free-tier rate-limited — "
                                f"waiting {retry_after:.1f}s then retrying with "
                                f"{body['model']!r}\033[0m",
                                file=sys.stderr,
                            )
                            time.sleep(retry_after)
                        else:
                            print(
                                f"\n  \033[33m[OrcaRouter-Stream] Free-tier rate-limited (no "
                                f"Retry-After) — waiting 10s then retrying with "
                                f"{body['model']!r}\033[0m",
                                file=sys.stderr,
                            )
                            time.sleep(10)
                        continue

                # 401 / 403 access_denied — fatal
                if e.code in (401, 403) and "access_denied" in error_msg:
                    raise RuntimeError(
                        f"OrcaRouter access denied (code={e.code}). "
                        f"Check ORCAROUTER_API_KEY permissions. Error: {error_body}"
                    )

                raise RuntimeError(f"OrcaRouter HTTP error {e.code}: {error_body}")

            except urllib.error.URLError as e:
                raise RuntimeError(f"OrcaRouter connection error: {e.reason}")

            # Stream the response
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

    # ─────────────────────────────────────────────────────────────────────
    # Streaming variant — apply FREE_ONLY gate before delegating
    # ─────────────────────────────────────────────────────────────────────

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from OrcaRouter.

        Thin wrapper around ``generate_completions_stream`` (which does
        the actual FREE_ONLY gating + fallback-chain injection + delegation
        to the inherited OpenAI-compatible streaming machinery). Kept as
        a separate method because ``BaseBackend`` declares it abstract.
        """
        yield from self.generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from OrcaRouter.

        Applies the FREE_ONLY gate upfront, then delegates to the inherited
        ``OpenAICompatibleBackend.generate_completions_stream`` which uses
        ``_get_chat_completions_url()``, ``_get_auth_headers()``,
        ``_iter_sse_lines()``, and ``_build_openai_body(stream=True)``.
        """
        if ORCAROUTER_FREE_ONLY and not _is_free_model(model):
            raise ValueError(
                f"ORCAROUTER_FREE_ONLY=true but model {model!r} is not in the "
                f"free-tier whitelist. Set ORCAROUTER_FREE_ONLY=false to use paid "
                f"models, or use one of: {sorted(ORCAROUTER_FREE_MODEL_WHITELIST)}"
            )

        # Inject fallback chain if configured
        extra_body = kwargs.pop("extra_body", None) or {}
        if ORCAROUTER_FALLBACK_MODELS:
            fallback_list = [m.strip() for m in ORCAROUTER_FALLBACK_MODELS.split(",") if m.strip()]
            if fallback_list:
                extra_body["models"] = fallback_list[:5]
                extra_body["route"] = "fallback"
        if extra_body:
            kwargs["extra_body"] = extra_body

        yield from super().generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Tool support — OrcaRouter translates OpenAI tools to upstream shapes
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """OrcaRouter translates OpenAI tools to each upstream's native shape
        (Anthropic ``input_schema``, Gemini ``functionDeclarations``, etc.),
        so all chat-capable models report NATIVE tool support.

        Non-chat models (image gen, audio, video) would return NONE, but
        those aren't in the chat-completions surface anyway — we don't
        expose them via this backend.
        """
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # JEV (System-One decision) hook
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
        """JEV hook for OrcaRouter: route the decision call through
        OrcaRouter's Bearer-authenticated /chat/completions endpoint
        via _generate_with_auth().

        Keeps the FREE_ONLY gate + fallback chain active.
        """
        if ORCAROUTER_FREE_ONLY and not _is_free_model(model):
            fallback = ORCAROUTER_FREE_FALLBACK_MODEL
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OrcaRouter.JEV] FREE_ONLY mode — {model!r} not free, "
                      f"switching to {fallback!r}")
            model = fallback

        return self._generate_with_auth(
            model=model,
            messages=messages,
            tools=None,  # decisions never call tools
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            **kwargs,
        )
