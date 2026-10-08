"""
\u269b\ufe0f AgentKthx \u2014 Cloudflare Workers AI Backend
Backend implementation for the Cloudflare Workers AI API (OpenAI Chat-Completions
compatible surface).

Cloudflare Workers AI provides cloud-hosted LLM inference via an OpenAI-
compatible API endpoint under ``api.cloudflare.com/client/v4/accounts/{id}/ai/v1``.
This backend inherits the OpenAI Chat-Completions logic from CloudBackend and
adds Cloudflare-specific authentication (Bearer token + account-id-in-URL),
default model selection, and the daily-neuron-quota free-tier model.

Endpoints used:
  - POST /chat/completions \u2192 OpenAI Chat Completions (tools, streaming)
  - GET  /models/search    \u2192 NATIVE catalog discovery (NOT on the OpenAI-compat
                           path \u2014 different response envelope: ``{result, success}``
                           instead of OpenAI's ``{data}``)

Configuration:
  CLOUDFLARE_API_KEY     \u2014 Bearer API token for authentication (required).
                          Create at dash.cloudflare.com \u2192 My Profile \u2192 API
                          Tokens \u2192 "Create Workers AI API Token" (prefilled
                          with Workers AI - Read + Edit permissions).
  CLOUDFLARE_ACCOUNT_ID  \u2014 32-hex-char account ID (required). Different from
                          the API key \u2014 Cloudflare embeds it in the URL path,
                          not derived from the token. Find at
                          dash.cloudflare.com \u2192 ?to=/:account/ai/workers-ai
  CLOUDFLARE_BASE_URL    \u2014 Override the full base URL (must include the
                          account ID in the path; default is constructed from
                          CLOUDFLARE_ACCOUNT_ID)
  CLOUDFLARE_DEFAULT_MODEL \u2014 Default model when none specified
                          (default: @cf/meta/llama-3.3-70b-instruct-fp8-fast)
  CLOUDFLARE_FREE_ONLY   \u2014 When true, the 429 daily-quota-exhausted error
                          surfaces a clearer "wait for UTC midnight reset"
                          hint (default: false). Does NOT filter the catalog
                          \u2014 the neuron budget is account-wide, not per-model.

Usage:
  # CLI
  agentkthx chat --backend cloudflare --model @cf/meta/llama-3.3-70b-instruct-fp8-fast
  agentkthx run "What is 15 * 8?" --backend cloudflare

  # Python API
  from agentkthx import Agent
  agent = Agent(model="@cf/meta/llama-3.1-8b-instruct", backend="cloudflare")
  result = agent.run("What is 15 * 8?")

Free tier (verified Oct 2026):
  - 10,000 neurons per day on the free plan, UTC daily reset
  - No credit card required
  - Quota is account-wide (shared across all models)
  - Paid Workers plan ($5/mo) does NOT raise the daily free quota \u2014 only
    enables paid overage at $0.011/1k neurons beyond the cap

Tool support:
  Most chat-completion models support OpenAI-compatible function calling.
  Three known families reject ``tools`` with 400:
    - Vision models (@cf/meta/llama-3.2-*-vision-instruct)
    - Reasoning distill models (@cf/deepseek-ai/deepseek-r1-distill-*)
    - GPT-OSS models (@cf/openai/gpt-oss-*) \u2014 require the Responses API,
      NOT /chat/completions
  AgentKthx's test_tool_support probe handles this dynamically per-model.
  The default implementation pre-classifies these families as REACT; all
  other models fall through to NATIVE.

Written by VTSTech \u2014 https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Generator

from agentkthx import model_cache
from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_BASE_URL,
    CLOUDFLARE_DEFAULT_MODEL,
)
from agentkthx.core.tool_cache import (
    cache_cloudflare_paid_only,
    is_cached_cloudflare_paid_only,
)
from agentkthx.core.types import BackendType, ToolSupportLevel
from agentkthx.model_cache import load_seed_catalog

# Cloudflare Workers AI model catalog with metadata for context sizing and
# defaults. Keys are the FULL prefixed Cloudflare model IDs
# (e.g. "@cf/meta/llama-3.3-70b-instruct-fp8-fast") because Cloudflare's API
# REQUIRES the full prefixed name in the request body. The
# cloud_base.get_model_info() / _get_model_defaults() strip the provider
# prefix before lookup by default \u2014 we override those methods here to
# skip the prefix-stripping so lookups match the catalog.
#
# Pricing: 0.0/0.0 for ALL entries because Cloudflare's daily-neuron-budget
# model means every model is "free" within the daily quota. This triggers
# _is_free_model()=True on every model, matching the
# CLOUDFLARE_API_TECHNICAL_REFERENCE.md guidance that CLOUDFLARE_FREE_ONLY
# does NOT filter the catalog (returns full catalog \u2014 quota is account-
# wide, not per-model).
CLOUDFLARE_MODELS: dict[str, dict] = load_seed_catalog("cloudflare")
"""Static catalog for the cloudflare backend \u2014 R07.20-pattern: the literal
dict lives in ``agentkthx/data/model_seed.json`` under the ``cloudflare`` key
and serves as the initial defaults of the persistent model-catalog cache
(and the offline fallback list). Update the seed JSON (or refresh a
backend's cache from the live API) instead of editing code here.
"""

# Default model when none specified. Llama-3.3-70B-Instruct-FP8-Fast is
# Cloudflare's flagship chat model \u2014 supports tools, streaming, JSON mode,
# 128K context, FP8 quantization for ~3x throughput vs the fp16 variant.
CLOUDFLARE_DEFAULT_MODEL_STR = (
    CLOUDFLARE_DEFAULT_MODEL or "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
)

# Default base URL template. Cloudflare embeds the account ID in the URL path
# (NOT just the Bearer token like other OpenAI-compat providers), so this is
# a format string that must be resolved with CLOUDFLARE_ACCOUNT_ID at
# construction time.
_DEFAULT_BASE_URL_TEMPLATE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"


def _is_free_model(model: str) -> bool:
    """Check if a Cloudflare Workers AI model is free.

    Cloudflare's daily-neuron-budget model means EVERY model is "free"
    within the daily quota \u2014 so this returns True for every catalog entry.
    The function exists for parity with the ZAI/OpenRouter pattern (which
    use per-model pricing to filter); for Cloudflare, the return value is
    uniform so FREE_ONLY doesn't filter the catalog.

    Returns False only for models NOT in the catalog (unknown models \u2014
    the live /ai/models/search endpoint may surface new models before the
    seed catalog is updated; those are conservatively treated as paid until
    added to the catalog).

    NOTE: The seed catalog keys on the FULL prefixed model ID (e.g.
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast") because Cloudflare's API
    requires the full prefixed name in the request body. This function
    does NOT strip the prefix \u2014 the model arg must match the catalog key
    exactly.
    """
    meta = CLOUDFLARE_MODELS.get(model)
    if not meta:
        return False
    pricing = meta.get("pricing", {})
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0


#: Name-pattern blocklist for non-chat models AgentKthx can't drive today.
#: If ANY of these substrings appears in the model name (lowercased), the
#: model is filtered out of the live /ai/models/search results.
#:
#: Categories covered:
#:   - Embeddings: embed, bge (BAAI's BGE family \u2014 embeddings only)
#:   - Image generation: diffusion, flux, sdxl, stable-diffusion
#:   - TTS / speech: tts, speech, whisper, asr, audio
#:   - Classification: classify, classification
#:   - Safety / guardrails: guard (llama-guard, nemoguard)
#:   - Translation: translate
#:   - Specialized non-chat: clef (Cloudflare's classifier family)
#:
#: Vision-language models (@cf/meta/llama-3.2-*-vision-instruct) are NOT
#: blocked \u2014 they're chat-capable text models with image input support
#: (currently ReAct-only because they reject `tools`, but the agent can
#: still drive them via text-only ReAct prompts today).
_NON_CHAT_PATTERNS: tuple[str, ...] = (
    "embed",  # @cf/baai/bge-base-en-v1.5, @cf/baai/bge-large-en-v1.5
    "bge",  # BAAI BGE family (embeddings)
    "diffusion",  # @cf/blackforest-labs/flux-* diffusion models
    "flux",  # @cf/blackforest-labs/flux-1-schnell
    "sdxl",  # stable-diffusion-xl
    "stable-diffusion",  # @cf/stabilityai/stable-diffusion-*
    "dreamshaper",  # @cf/lykon/dreamshaper-8
    "tts",  # text-to-speech
    "speech",  # speech models
    "whisper",  # @cf/openai/whisper-* (ASR)
    "asr",  # automatic speech recognition
    "audio",  # audio models generally
    "classify",  # classification models
    "classification",  # classification models (long form)
    "guard",  # @cf/meta/llama-guard-3-8b, *-nemoguard-* (safety classifier)
    "clef",  # @cf/cloudflare/clef, @cf/cloudflare/clef-flash (specialized)
    "translate",  # translation models
    "rerank",  # reranking models
)


def _is_non_chat_model(model_name: str) -> bool:
    """Check if a model name matches the non-chat blocklist.

    Returns True if the model should be filtered out (embeddings, image
    generation, TTS, classification, translation). Vision-language models
    are NOT blocked \u2014 they're chat-capable (text + image input), and
    AgentKthx drives them via ReAct since they reject `tools`.

    Conservative by design \u2014 only blocks patterns that CLEARLY indicate
    non-chat models. Legitimate chat models (even ones not in the seed
    catalog) pass through.
    """
    name_lower = model_name.lower()
    return any(pattern in name_lower for pattern in _NON_CHAT_PATTERNS)


def _looks_like_neuron_quota_exhaustion(status_code: int, body_text: str) -> bool:
    """Detect Cloudflare Workers AI's daily-neuron-quota 429.

    Cloudflare returns 429 for both transient rate limits (retryable with
    backoff) AND daily neuron quota exhaustion (NOT retryable \u2014 wait for
    UTC midnight reset). The two are distinguished by the error body:

      - Transient rate limit: message contains "rate limit" / "RPM" / "TPM"
        (per the Cloudflare API technical reference: "429 without quota
        language = transient rate limit")
      - Daily quota exhaustion: message contains "neuron" / "quota" /
        "daily" / "exhausted" (per the Cloudflare API technical reference:
        "429 with 'neuron' or 'quota' in message = daily quota exhausted")

    NOTE: "limit" alone is NOT an indicator \u2014 "rate limit exceeded"
    contains "limit" but is the canonical transient-rate-limit wording,
    so including "limit" would mis-classify every transient 429 as
    quota exhaustion. The four indicators above are the
    neuron-specific markers Cloudflare's docs call out; they don't
    overlap with plain rate-limit wording.

    This helper lets the retry loop classify the 429 correctly \u2014 transient
    ones back off and retry, quota-exhaustion surfaces immediately with a
    clear "daily quota exhausted, wait for UTC midnight reset" message.
    """
    if status_code != 429:
        return False
    if not body_text:
        return False
    body_lower = body_text.lower()
    indicators = ("neuron", "quota", "daily", "exhausted")
    return any(ind in body_lower for ind in indicators)


#: Cloudflare's "model not available on Workers Free plan" error code.
#: Surfaced in the ``errors[0].code`` field of HTTP 403 responses when a
#: free-tier account tries to invoke a paid-tier-only model (e.g.
#: @cf/zai-org/glm-5.3-flash). The accompanying message reads:
#:
#:   "AiError: Model @cf/zai-org/glm-5.3-flash is not available on the
#:    Workers Free plan: ... Upgrade to access this model: https://..."
#:
#: Distinct from a 403 caused by a token lacking Workers AI:Edit
#: permissions (which has no ``code`` field, just a plain message). The
#: two 403s need different user actions: code 5035 = upgrade the plan OR
#: switch to a free-tier model; token-permission 403 = recreate the API
#: token with the right scope.
_CF_PAID_ONLY_ERROR_CODE = 5035


def _extract_cloudflare_error_code(body_text: str) -> int | None:
    """Extract the Cloudflare error code from an error response body.

    Cloudflare's error envelope (NOT OpenAI-spec) looks like::

        {
          "result": {},
          "success": false,
          "errors": [
            {
              "code": 5035,
              "message": "AiError: Model ... is not available on the Workers Free plan: ..."
            }
          ],
          "messages": []
        }

    The first error's ``code`` field is the discriminator. Returns None
    when the body is empty, not valid JSON, doesn't carry the Cloudflare
    envelope shape, or has no numeric ``code`` field.

    Args:
        body_text: Raw HTTP response body (string).

    Returns:
        The integer error code (e.g. 5035), or None if not extractable.
    """
    if not body_text:
        return None
    try:
        data = json.loads(body_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    errors = data.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    code = first.get("code")
    if isinstance(code, bool):  # bool is a subclass of int \u2014 exclude it
        return None
    if isinstance(code, int):
        return code
    return None


class CloudflareBackend(CloudBackend):
    """Backend for Cloudflare Workers AI API (OpenAI Chat-Completions compatible).

    Inherits from ``CloudBackend`` (MAINT-02, R07.05) which provides:
      - ``__init__`` resolving base_url + API-key validation + OPENAI/JEV forcing
      - ``is_running()`` \u2014 True iff API key configured
      - ``_get_auth_headers()`` \u2014 standard Bearer + Content-Type
      - ``_get_model_defaults()`` \u2014 catalog lookup + max_tokens cap
      - ``get_model_info()`` / ``get_model_max_context()`` \u2014 catalog lookup
      - ``test_tool_support()`` \u2014 returns NATIVE by default
      - Shared retry helpers: ``_compute_retry_after``,
        ``_compute_network_backoff``, ``_is_retryable_http_status``
      - ``_close_http_response`` (ROB-06 R07.25 deterministic close)

    Cloudflare-specific overrides:
      - ``__init__`` \u2014 validates CLOUDFLARE_ACCOUNT_ID (required, used
        to construct the base URL with the account ID baked into the path)
      - ``MODELS`` \u2014 static catalog from model_seed.json
      - ``backend_type`` \u2014 ``BackendType.CLOUDFLARE``
      - ``_get_chat_completions_url()`` \u2014 ``{base}/chat/completions``
      - ``_get_models_url()`` \u2014 the NATIVE ``{base}/models/search``
        endpoint (NOT on the OpenAI-compat path)
      - ``list_models()`` \u2014 queries the native /ai/models/search
        endpoint (different response shape \u2014 Cloudflare envelope
        ``{result: [...], success: true}`` instead of OpenAI's ``{data: [...]}``)
        and merges with the static catalog (mirrors the NVIDIA pattern)
      - ``_iter_sse_lines()`` \u2014 streaming POST with 429/5xx retry +
        context-length 400 recovery + daily-neuron-quota detection
      - ``_make_api_request()`` \u2014 non-streaming POST with the same
        error recovery as the streaming path
      - ``test_tool_support()`` \u2014 pre-classifies vision / R1-distill /
        GPT-OSS models as REACT (they reject `tools` with 400); everything
        else falls through to NATIVE
      - ``generate()`` \u2014 entry point, JEV dispatch, builds body, calls
        ``_make_api_request(stream=False)``
      - ``generate_stream()`` \u2014 text-delta wrapper over the inherited
        ``generate_completions_stream`` (parity with NVIDIA/Mistral/OpenRouter)

    Usage:
        backend = get_backend("cloudflare")
        backend = CloudflareBackend(api_key="cf-token-...", account_id="abcdef...")
    """

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # CloudBackend class-attribute overrides
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    MODELS = CLOUDFLARE_MODELS
    _api_key_env_var = "CLOUDFLARE_API_KEY"
    _default_base_url = CLOUDFLARE_BASE_URL  # may be empty if no account_id \u2014 see __init__
    _default_model = CLOUDFLARE_DEFAULT_MODEL_STR
    _provider_label = "Cloudflare"

    # R07.20: key for the persistent JSON model-catalog cache
    # (agentkthx/model_cache.py). The cache lives at
    # ~/.cache/agentkthx/model_catalog.json under the "cloudflare" key.
    MODEL_CACHE_KEY = "cloudflare"

    # In-process mirror of the last resolved catalog (fresh cache hit,
    # live fetch, or offline fallback). The JSON cache remains the
    # cross-process source of truth.
    _model_cache: list[dict] | None = None

    # Cache the model list for 1 hour to avoid hitting /ai/models/search on
    # every agent.run() \u2014 the catalog rarely changes within a session.
    _MODEL_CACHE_TTL_SECONDS = 3600  # 1 hour

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # __init__ \u2014 Cloudflare-specific: account ID required, baked into URL
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config=None,
        api_mode=None,
        api_key: str | None = None,
        account_id: str | None = None,
    ):
        """Initialize the Cloudflare Workers AI backend.

        Unlike other OpenAI-compat cloud backends where the API key alone
        suffices, Cloudflare embeds the account ID in the URL path. Both
        ``CLOUDFLARE_API_KEY`` AND ``CLOUDFLARE_ACCOUNT_ID`` are required.

        Args:
            base_url: Override the full base URL (must include the account
                ID in the path already). If None, constructed from
                ``account_id`` (or ``CLOUDFLARE_ACCOUNT_ID`` env var) using
                the standard Cloudflare Workers AI URL template.
            host, port: Alternative URL form \u2014 not supported for Cloudflare
                (the account ID must be in the path). Ignored if base_url
                is provided.
            config: ``BackendConfig`` for timeout/max_retries.
            api_mode: ``OPENAI`` (default) or ``JEV``. ``OPENRE`` rejected
                (cloud backends don't expose the native /api/chat endpoint).
            api_key: Cloudflare API token. If None, falls back to
                ``CLOUDFLARE_API_KEY`` env var.
            account_id: Cloudflare account ID (32-hex-char string). If
                None, falls back to ``CLOUDFLARE_ACCOUNT_ID`` env var.

        Raises:
            ValueError: If account_id is missing/empty (after env fallback)
                or if the API key fails CloudBackend's validation.
        """
        # Resolve account ID \u2014 priority: explicit arg > env var > config module singleton
        resolved_account_id = (
            account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "") or CLOUDFLARE_ACCOUNT_ID
        )
        if not resolved_account_id or not resolved_account_id.strip():
            raise ValueError(
                "CLOUDFLARE_ACCOUNT_ID is required for the Cloudflare backend. "
                "Find your 32-hex-char account ID at "
                "https://dash.cloudflare.com/?to=/:account/ai/workers-ai "
                "(the ID is in the URL bar after clicking into the Workers AI "
                "section). Set it via the CLOUDFLARE_ACCOUNT_ID env var, the "
                "`agentkthx auth` picker, or pass account_id=... to the backend "
                "constructor."
            )

        # Resolve base URL \u2014 priority: explicit arg > env/config > template
        if base_url:
            resolved_url = base_url.rstrip("/")
        else:
            # CLOUDFLARE_BASE_URL may be set in env/config (with or without
            # the account ID already baked in). If it's the unmodified
            # template (contains "{account_id}" placeholder) or empty,
            # construct it from the resolved account ID.
            env_url = os.environ.get("CLOUDFLARE_BASE_URL", "") or CLOUDFLARE_BASE_URL
            if env_url and "{account_id}" not in env_url and "{ACCOUNT_ID}" not in env_url:
                # User-provided URL \u2014 trust it (account ID baked in already)
                resolved_url = env_url.rstrip("/")
            else:
                resolved_url = _DEFAULT_BASE_URL_TEMPLATE.format(account_id=resolved_account_id)

        # Stash the account ID for later use (list_models uses it for the
        # native /ai/models/search endpoint which is OUTSIDE the /ai/v1 base).
        self._account_id = resolved_account_id.strip()

        # Delegate to CloudBackend.__init__ for API-key validation, OPENAI/JEV
        # forcing, _context_safe_max_tokens init, etc.
        # We pass base_url explicitly here so the resolution logic above is
        # the single source of truth.
        super().__init__(
            base_url=resolved_url,
            host=host,
            port=port,
            config=config,
            api_mode=api_mode,
            api_key=api_key,
        )

        # Live model cache: list[dict] + timestamp. Populated by list_models().
        # MAINT-02 (R07.05): CloudBackend.__init__ already set
        # _context_safe_max_tokens; we only add the model-cache fields here.
        self._model_cache_ts: float = 0.0

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Provider identity
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @property
    def backend_type(self) -> BackendType:
        """Cloudflare's dedicated BackendType enum value."""
        return BackendType.CLOUDFLARE

    @property
    def account_id(self) -> str:
        """Return the Cloudflare account ID (baked into the URL path)."""
        return self._account_id

    def _validate_api_key(self, key: str) -> None:
        """Warn (not error) if the API token doesn't look like a Cloudflare token.

        Cloudflare API tokens are opaque base64-ish strings (40+ chars,
        typically with hyphens and underscores). Unlike NVIDIA's ``nvapi-``
        prefix, Cloudflare tokens don't carry a recognizable prefix \u2014 we
        only warn if the key starts with a known wrong-provider prefix
        (``sk-`` for OpenAI, ``hf_`` for HuggingFace, ``nvapi-`` for NVIDIA,
        ``pk_`` for Pollinations) so users can spot typos like accidentally
        pasting an OpenAI key.
        """
        wrong_prefixes = ("sk-", "hf_", "nvapi-", "pk_")
        if any(key.startswith(p) for p in wrong_prefixes):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [Cloudflare] Warning: API key starts with a known "
                    f"prefix from another provider ({key[:6]!r}...). "
                    f"Cloudflare API tokens have no recognizable prefix \u2014 "
                    f"if you see 401 errors, verify the token at "
                    f"dash.cloudflare.com \u2192 My Profile \u2192 API Tokens."
                )

    def _extra_auth_headers(self) -> dict:
        """Cloudflare uses the standard Bearer-token auth \u2014 no extras.

        Unlike OpenRouter (which adds HTTP-Referer + X-Title for attribution)
        or OrcaRouter (which adds X-OrcaRouter-Include-Cost), Cloudflare
        Workers AI uses only the standard ``Authorization: Bearer`` header.
        """
        return {}

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # OpenAICompatibleBackend abstract hooks
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _get_chat_completions_url(self) -> str:
        """Cloudflare Workers AI's OpenAI-compat endpoint.

        ``self._base_url`` already ends with ``/ai/v1`` (no trailing slash)
        \u2014 we append ``/chat/completions`` to form the full URL
        ``https://api.cloudflare.com/client/v4/accounts/{id}/ai/v1/chat/completions``.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the NATIVE catalog discovery endpoint.

        Unlike every other cloud backend, Cloudflare does NOT expose
        ``/v1/models`` on the OpenAI-compat path. The native endpoint is
        ``/ai/models/search`` (note: ``ai`` not ``ai/v1`` \u2014 it lives at
        the account-root level, not under the OpenAI-compat subpath).

        The response envelope is Cloudflare-shaped (``{result: [...],
        success: true, errors: [], messages: []}``) \u2014 NOT OpenAI's
        ``{object: "list", data: [...]}``. ``_fetch_live_models`` handles
        the parse.
        """
        # The models endpoint is at the /ai/ level (not /ai/v1/). We
        # rebuild from the account ID rather than from _base_url.
        return (
            f"https://api.cloudflare.com/client/v4/accounts/{self._account_id}/ai/models/search"
            "?per_page=100"
        )

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Catalog lookup overrides \u2014 Cloudflare keys on FULL prefixed IDs
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    #
    # The CloudBackend base class (cloud_base.py) strips the provider prefix
    # before catalog lookup (model.split("/")[-1]) because ZAI/OpenRouter
    # key their catalogs on bare post-slash segments. Cloudflare's API
    # REQUIRES the full prefixed name in the request body (e.g.
    # "@cf/meta/llama-3.3-70b-instruct-fp8-fast" \u2014 the @cf/ prefix is
    # part of the model ID, not a slashable provider marker), so our seed
    # catalog keys on full names. These overrides skip the prefix-stripping
    # so lookups match.

    def get_model_info(self, model: str) -> dict | None:
        """Look up model in the static catalog by FULL prefixed ID.

        Override of CloudBackend.get_model_info \u2014 does NOT strip the
        provider prefix because Cloudflare's catalog keys on the full name
        (e.g. "@cf/meta/llama-3.3-70b-instruct-fp8-fast").
        """
        meta = self.MODELS.get(model, {})
        if not meta:
            return None
        return {
            "name": model,
            "size": 0,
            "details": {
                "family": self._catalog_family_name(),
                "backend": self._catalog_backend_name(),
                "context_length": meta.get("context_length", self._DEFAULT_CONTEXT_FALLBACK),
                "free_tier": _is_free_model(model),
            },
        }

    def _get_model_defaults(self, model: str) -> dict:
        """Return {temperature, max_tokens} from the static catalog.

        Override of CloudBackend._get_model_defaults \u2014 does NOT strip
        the provider prefix. Falls back to safe defaults (max_tokens=8192,
        context_length=128000, temperature=0.7) when the model isn't in
        the catalog.
        """
        meta = self.MODELS.get(model, {})
        max_tokens = meta.get("default_max_tokens", 8192)
        context_length = meta.get("context_length", self._DEFAULT_CONTEXT_FALLBACK)
        temperature = meta.get("default_temperature", 0.7)
        return self._apply_max_tokens_cap(max_tokens, context_length, temperature=temperature)

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Return the model's maximum trained context window size.

        Override of CloudBackend.get_model_max_context \u2014 does NOT strip
        the provider prefix. Falls back to 128000 when not in catalog.
        """
        meta = self.MODELS.get(model, {})
        if meta:
            ctx = meta.get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                return ctx
        # Fall back to live model_info if catalog misses
        info = self.get_model_info(model)
        if info and "details" in info:
            ctx = info["details"].get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                return ctx
        return self._DEFAULT_CONTEXT_FALLBACK

    def _is_free_model(self, model: str) -> bool:
        """Override of CloudBackend._is_free_model \u2014 does NOT strip prefix."""
        return _is_free_model(model)

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # list_models \u2014 query native /ai/models/search, merge with static catalog
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    #
    # Mirrors the NVIDIA pattern (nvidia.py:_fetch_live_models): live
    # /ai/models/search results first, then catalog-only models (so
    # variants the API omits still appear). Persistent JSON cache (R07.20)
    # wraps the live fetch so the offline fallback (seed defaults) kicks in
    # when the network is unreachable.
    #
    # Key difference from NVIDIA: the /ai/models/search endpoint returns a
    # CLOUDFLARE-SHAPED envelope ({result: [...], success: true, errors: [],
    # messages: []}) instead of OpenAI's {object: "list", data: [...]}.

    def _fetch_live_models(self) -> list[dict]:
        """Query ``GET /ai/models/search`` and build the merged live catalog.

        Cloudflare's /ai/models/search returns a Cloudflare-shaped envelope
        (verified live Oct 2026 against a real account)::

            {
              "success": true,
              "result": [
                {
                  "id": "<uuid>",            # INTERNAL UUID \u2014 NOT the model ID
                  "name": "@cf/openai/gpt-oss-120b",   # THE model ID we send in requests
                  "description": "...",
                  "task": {
                    "id": "<uuid>",
                    "name": "Text Generation",        # Category (capitalized, with space)
                    "description": "..."
                  },
                  "properties": [
                    {"property_id": "context_window", "value": 128000},
                    {"property_id": "function_calling", "value": "true"},
                    {"property_id": "reasoning", "value": "true"},
                    {"property_id": "reasoning_effort", "value": {...}},
                    {"property_id": "price", "value": [...]}
                  ]
                },
                ...
              ],
              "errors": [],
              "messages": []
            }

        IMPORTANT: this shape differs from the docs in three ways that the
        original scaffold got wrong (causing the live fetch to silently
        return 0 models and fall back to the 15-model seed):

          1. ``id`` is an INTERNAL UUID, not the model ID. The model ID
             lives in ``name`` (e.g. ``@cf/openai/gpt-oss-120b``).
          2. There is no top-level ``type`` field. The category lives in
             ``task.name`` and uses the human-readable string
             ``"Text Generation"`` (capitalized, with space) \u2014 NOT
             the lowercase ``"text-generation"`` the docs claim.
          3. Capability metadata (context window, function calling,
             reasoning) lives in the ``properties[]`` array as
             ``{property_id, value}`` pairs, not as top-level fields.

        This implementation:
          - Filters to ``task.name == "Text Generation"`` (drops embeddings,
            image gen, TTS, classification, translation, ASR, image-to-text).
          - Applies the non-chat blocklist (drops llama-guard, clef, and
            any other ``Text Generation`` model whose name matches a
            non-chat pattern \u2014 e.g. ``embed``, ``guard``, ``classify``).
          - Parses ``properties[]`` to enrich each entry with the live
            ``context_window``, ``function_calling``, and ``reasoning``
            values \u2014 so the catalog no longer depends on the static
            seed for context_length on live-discovered models.
          - Merges catalog-only models (variants the API may omit) so the
            seed entries still appear even when Cloudflare's pagination
            or task filtering drops them.
        """
        url = self._get_models_url()
        headers = self._get_auth_headers()
        req = urllib.request.Request(url, headers=headers, method="GET")

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        # Cloudflare envelope: top-level `result` (list), `success` (bool),
        # `errors` (list), `messages` (list). NOT OpenAI's `data` field.
        if not result.get("success", True):
            raise RuntimeError(
                f"Cloudflare /ai/models/search returned success=false: "
                f"{result.get('errors', [])}"
            )

        api_models = result.get("result", []) or []

        # Build a {model_name: enriched_meta} dict from the live response.
        # We filter to "Text Generation" task (the chat-capable subset) and
        # parse the properties[] array for context_window + capabilities.
        api_model_meta: dict[str, dict] = {}
        for m in api_models:
            # Filter to text-generation models. Cloudflare's catalog uses
            # ``task.name`` (capitalized "Text Generation"), NOT a top-level
            # ``type`` field. The 9 task categories observed in production
            # (Oct 2026): Text Generation, Text Embeddings, Text-to-Image,
            # Text-to-Speech, Text Classification, Translation, Image
            # Classification, Image-to-Text, Automatic Speech Recognition,
            # Dumb Pipe. Only "Text Generation" is chat-capable for AgentKthx.
            task_name = (m.get("task") or {}).get("name", "")
            if task_name != "Text Generation":
                continue
            # The model ID lives in ``name`` (e.g. "@cf/openai/gpt-oss-120b"),
            # NOT in ``id`` (which is an internal UUID).
            name = m.get("name", "") or ""
            if not name:
                continue

            # Parse the properties[] array into a flat dict for easy lookup.
            # Each property is {"property_id": "...", "value": ...}.
            props: dict[str, object] = {}
            for p in m.get("properties", []) or []:
                pid = p.get("property_id", "")
                if pid:
                    props[pid] = p.get("value")

            api_model_meta[name] = props

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Cloudflare] API returned {len(api_model_meta)} " f"Text Generation models")

        # Build unified list: API-discovered models first (confirmed
        # available), then catalog-only models (variants the API may omit).
        seen: set[str] = set()
        models: list[dict] = []

        for name in sorted(api_model_meta.keys()):
            if name in seen:
                continue
            # Blocklist: skip non-chat models AgentKthx can't drive today.
            # The blocklist catches things like ``@cf/meta/llama-guard-3-8b``
            # that Cloudflare labels "Text Generation" but AgentKthx can't
            # drive (it's a safety classifier, not a chat model).
            if _is_non_chat_model(name):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [Cloudflare] Skipping {name} \u2014 matches non-chat "
                        f"blocklist pattern (embedding/guard/classify/image-gen/"
                        f"TTS/translation)"
                    )
                continue
            seen.add(name)

            # Live-enriched properties (from the API response).
            live_props = api_model_meta[name]
            live_ctx = live_props.get("context_window")
            live_fc = live_props.get("function_calling")  # "true" / None
            live_reasoning = live_props.get("reasoning")  # "true" / None

            # Static catalog metadata (for fallback when live doesn't carry
            # the field, e.g. context_window is missing on some LoRA models).
            meta = CLOUDFLARE_MODELS.get(name, {})
            seed_ctx = meta.get("context_length")

            # Resolve context_length: live wins, seed fallback, default 128K.
            if isinstance(live_ctx, int) and live_ctx > 0:
                ctx = live_ctx
            elif isinstance(seed_ctx, int) and seed_ctx > 0:
                ctx = seed_ctx
            else:
                ctx = self._DEFAULT_CONTEXT_FALLBACK

            models.append(
                {
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": meta.get("family", self._catalog_family_name()),
                        "backend": self._catalog_backend_name(),
                        "context_length": ctx,
                        "free_tier": _is_free_model(name) if meta else True,
                        "is_chat_model": True,
                        # Live capability flags (bonus enrichment from the
                        # API properties[] array \u2014 lets test_tool_support
                        # and the think column use the provider's own declaration
                        # rather than the static-catalog guess).
                        "function_calling": live_fc == "true",
                        "reasoning": live_reasoning == "true",
                        "pricing": meta.get("pricing", {}),
                    },
                }
            )

        # Catalog-only models \u2014 surfaced even if the API didn't list them
        # (variants that may be temporarily unavailable, or that
        # Cloudflare's /ai/models/search omits for pagination / task-filter
        # reasons). These come from the static seed catalog only.
        for catalog_name in sorted(CLOUDFLARE_MODELS.keys()):
            if catalog_name in seen:
                continue
            seen.add(catalog_name)
            meta = CLOUDFLARE_MODELS[catalog_name]
            models.append(
                {
                    "name": catalog_name,
                    "size": 0,
                    "details": {
                        "family": self._catalog_family_name(),
                        "backend": self._catalog_backend_name(),
                        "context_length": meta.get(
                            "context_length", self._DEFAULT_CONTEXT_FALLBACK
                        ),
                        "free_tier": _is_free_model(catalog_name),
                        "is_chat_model": True,
                        "pricing": meta.get("pricing", {}),
                    },
                }
            )

        return models

    def list_models(self) -> list[dict]:
        """Return the Cloudflare Workers AI model catalog (live + static, cached 1h).

        Cache layers (R07.20): L1 in-process cache (1 hour) + L2 persistent
        JSON cache (30-minute TTL, ``AGENTKTHX_MODEL_CACHE_TTL`` to override)
        shared across processes.

        ``CLOUDFLARE_FREE_ONLY=true`` filtering (R07.27 follow-up):
        Cloudflare's /ai/models/search lists ALL models, including paid-tier-
        only ones (e.g. @cf/zai-org/glm-5.3-flash returns code 5035 with
        "not available on the Workers Free plan" when invoked on a free
        account). When the user enables CLOUDFLARE_FREE_ONLY, list_models()
        filters out any model whose paid-only verdict is cached in
        ``~/.agentkthx/tool_support.json`` under the ``cf-paid:<model>`` key
        prefix (written by _make_api_request / _iter_sse_lines on the first
        403/5035 hit). Without CLOUDFLARE_FREE_ONLY, paid-only models remain
        in the catalog so users on paid plans can still see them.

        Without CLOUDFLARE_FREE_ONLY, the return value otherwise carries
        every cataloged Cloudflare model \u2014 the daily neuron quota is
        account-wide (not per-model), so per-model pricing doesn't filter
        the catalog the way it does for ZAI / OpenRouter.

        Returns:
            List of ``{"name": ..., "size": 0, "details": {...}}`` dicts
            in the shape expected by the CLI's ``agentkthx models`` command.
        """
        # L1 cache: return if fresh
        now = time.time()
        if (
            self._model_cache is not None
            and (now - self._model_cache_ts) < self._MODEL_CACHE_TTL_SECONDS
        ):
            return self._filter_paid_only(list(self._model_cache))

        # L2: persistent JSON cache \u2014 fresh entry replaces the live fetch
        cached = model_cache.get_cached_models(self.MODEL_CACHE_KEY)
        if cached is not None:
            self._model_cache = cached
            self._model_cache_ts = now
            return self._filter_paid_only(list(cached))

        # First JSON-cache contact: seed the static defaults (catalog
        # entries; stale-stamped so the live fetch below still runs).
        model_cache.ensure_seeded(self.MODEL_CACHE_KEY, self._catalog_fallback_list())

        # Live fetch \u2014 best-effort, falls back to catalog on any failure
        try:
            models = self._fetch_live_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Cloudflare] live /ai/models/search fetch failed ({e}); using catalog")
            models = self._catalog_fallback_list()

        # Persist to L2 cache (shared across processes)
        model_cache.store_models(self.MODEL_CACHE_KEY, models, source="api")

        # Update L1 cache
        self._model_cache = models
        self._model_cache_ts = now

        return self._filter_paid_only(list(models))

    def _filter_paid_only(self, models: list[dict]) -> list[dict]:
        """Filter out cached paid-only models when CLOUDFLARE_FREE_ONLY=true.

        Reads the ``cf-paid:<model>`` cache (populated by _make_api_request /
        _iter_sse_lines on the first 403/5035 hit) and drops any matching
        entries from ``models``. When CLOUDFLARE_FREE_ONLY is false (default),
        returns ``models`` unchanged so paid-plan users still see every
        Cloudflare model in the catalog.

        Best-effort: cache-read failures degrade to "show everything"
        (matches the catalog-only behavior \u2014 a broken cache must never
        silently hide models the user can actually access).
        """
        # Import the config flag locally to avoid a module-level import
        # cycle (config.py is imported before this module on every CLI
        # invocation, but the flag may flip mid-session via /auth).
        from agentkthx.config import CLOUDFLARE_FREE_ONLY

        if not CLOUDFLARE_FREE_ONLY:
            return models
        kept: list[dict] = []
        dropped = 0
        for m in models:
            name = m.get("name", "")
            try:
                if name and is_cached_cloudflare_paid_only(name):
                    dropped += 1
                    continue
            except Exception as e:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [Cloudflare] is_cached_cloudflare_paid_only "
                        f"failed for {name}: {e} (keeping model in catalog)"
                    )
            kept.append(m)
        if dropped and os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [Cloudflare] CLOUDFLARE_FREE_ONLY=true: filtered out "
                f"{dropped} cached paid-only model(s) from the catalog"
            )
        return kept

    def _catalog_fallback_list(self) -> list[dict]:
        """Shape the static (seed) catalog into ``list_models()`` entries.

        Used when the live /ai/models/search endpoint is unreachable AND the
        persistent JSON cache is empty/missing \u2014 the static seed catalog
        is the offline fallback.
        """
        models: list[dict] = []
        for name in sorted(CLOUDFLARE_MODELS.keys()):
            meta = CLOUDFLARE_MODELS[name]
            models.append(
                {
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": self._catalog_family_name(),
                        "backend": self._catalog_backend_name(),
                        "context_length": meta.get(
                            "context_length", self._DEFAULT_CONTEXT_FALLBACK
                        ),
                        "free_tier": _is_free_model(name),
                        "is_chat_model": True,
                        "pricing": meta.get("pricing", {}),
                    },
                }
            )
        return models

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # test_tool_support \u2014 override to detect vision / R1-distill / GPT-OSS models
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    #
    # CloudBackend's default returns NATIVE for all models. Cloudflare's
    # vision, R1-distill, and GPT-OSS families reject `tools` with 400 \u2014
    # those should cache REACT.
    #
    # The detection is conservative: only models whose name carries an
    # explicit "vision" / "r1" / "deepseek-r" / "gpt-oss" marker are
    # pre-classified as REACT (no live probe). Every other model falls
    # through to the inherited NATIVE default and is probed on first
    # use via the standard test_tool_support flow.

    #: Model name patterns (matched against the lowercased full ID).
    _REACT_NAME_PATTERNS: tuple[str, ...] = (
        r"vision",  # @cf/meta/llama-3.2-11b-vision-instruct, *-vision-instruct
        r"deepseek-r\d",  # @cf/deepseek-ai/deepseek-r1-distill-qwen-32b
        r"-r1",  # explicit R1 marker
        r"\bthinking\b",  # *-thinking-* (future Qwen3-thinking if it lands)
        r"gpt-oss",  # @cf/openai/gpt-oss-120b, @cf/openai/gpt-oss-20b (Responses API only)
    )

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Vision / R1-distill / GPT-OSS models default to REACT; everything else NATIVE.

        Cloudflare's vision models (@cf/meta/llama-3.2-*-vision-instruct)
        reject ``tools`` in the request body with a 400. Same for the
        DeepSeek-R1-distill family and GPT-OSS (which only supports tools
        via the Responses API, NOT /chat/completions). We pre-classify
        those as REACT so the agent loop uses the ReAct prompting path
        instead of native function calling.

        Every other model falls through to the inherited CloudBackend
        default (NATIVE) and is probed on first use via the standard
        ``test_tool_support`` flow (the probe is cached in
        ``~/.agentkthx/tool_support.json``).
        """
        model_lower = model.lower()
        for pattern in self._REACT_NAME_PATTERNS:
            if re.search(pattern, model_lower):
                return ToolSupportLevel.REACT
        # Default: NATIVE (inherited CloudBackend behavior \u2014 most chat
        # models in the Cloudflare catalog support OpenAI-spec function calling)
        return ToolSupportLevel.NATIVE

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # _make_api_request \u2014 non-streaming POST with retry (mirrors NVIDIA)
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _make_api_request(self, body: dict, *, stream: bool = False) -> dict:
        """POST to ``/chat/completions`` with 429/5xx retry.

        Honors ``Retry-After`` when present (429 rate-limit and 503
        service-unavailable). Falls back to exponential backoff with
        full jitter. Max retries determined by ``_max_retries()`` (default
        4, override via ``AGENTKTHX_MAX_API_RETRIES`` env var).

        Special case for Cloudflare: a 429 whose body indicates daily
        neuron quota exhaustion (contains "neuron" / "quota" / "daily"
        / "limit" / "exhausted") is NOT retryable \u2014 the daily quota has
        been hit and retrying won't help. Such 429s surface immediately
        as a clear RuntimeError so the user knows to wait for UTC midnight
        reset.

        On HTTP 200, parses the JSON body via ``_parse_openai_response``
        (inherited from OpenAICompatibleBackend) which handles the
        OpenAI-spec error envelope and tool-call shape.
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
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    return self._parse_openai_response(raw)

            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""

                # Parse OpenAI-spec error envelope
                err_data: dict | None = None
                try:
                    if body_text:
                        err_data = json.loads(body_text)
                except (json.JSONDecodeError, ValueError):
                    pass

                if isinstance(err_data, dict) and "error" in err_data:
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

                # Special case: Cloudflare daily-neuron-quota exhaustion 429
                # \u2014 NOT retryable. Surface immediately with a clear message
                # about the UTC midnight reset rather than burning through
                # retries that won't help.
                if (
                    status_code == 429
                    and _looks_like_neuron_quota_exhaustion(status_code, body_text)
                    and attempt == 0
                ):
                    raise RuntimeError(
                        f"Cloudflare Workers AI daily neuron quota exhausted. "
                        f"Quota resets at UTC midnight \u2014 wait for the reset, "
                        f"switch to another backend for the day, or upgrade to a "
                        f"paid Workers plan for paid overage at $0.011/1k neurons "
                        f"beyond the daily cap. Details: {err_msg}"
                    ) from e

                # ARCH-03: shared context-length 400 handler. Only on
                # the first attempt (don't loop forever on a 400).
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [Cloudflare] Context length exceeded \u2014 "
                                f"reducing max_tokens {old_max} \u2192 {new_max} and retrying"
                            )
                        continue

                # Retryable: 429 (rate limit, NOT quota exhaustion) +
                # 5xx (transient server errors). R07.24 (MAINT-23/ROB-29):
                # delegate to the shared _is_retryable_http_status helper.
                if self._is_retryable_http_status(status_code) and attempt < max_retries:
                    retry_after = self._compute_retry_after(e.headers, attempt)
                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        print(
                            f"  [Cloudflare] {status_code} \u2014 {err_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    time.sleep(retry_after)
                    continue

                # Non-retryable OR exhausted retries
                if status_code == 401:
                    raise RuntimeError(
                        "Cloudflare authentication failed. Check your "
                        "CLOUDFLARE_API_KEY environment variable (must be a "
                        "Workers AI-scoped API token with Read + Edit "
                        "permissions). Create one at dash.cloudflare.com \u2192 "
                        'My Profile \u2192 API Tokens \u2192 "Create Workers AI '
                        'API Token".'
                    ) from e
                if status_code == 403:
                    # Cloudflare error code 5035 = "model not available on
                    # Workers Free plan" (paid-tier-only model invoked on a
                    # free account). Different user action than a
                    # token-permission 403: 5035 = upgrade OR switch model,
                    # not "recreate your token". Cache the paid-only verdict
                    # so the next list_models() with CLOUDFLARE_FREE_ONLY=true
                    # filters this model out automatically.
                    err_code = _extract_cloudflare_error_code(body_text)
                    if err_code == _CF_PAID_ONLY_ERROR_CODE:
                        model_name = body.get("model", "?")
                        # Best-effort cache write - never let a cache
                        # failure mask the real error from the user.
                        try:
                            cache_cloudflare_paid_only(model_name, paid_only=True)
                        except Exception as cache_err:
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(
                                    f"  [Cloudflare] cache_cloudflare_paid_only "
                                    f"failed for {model_name}: {cache_err}"
                                )
                        raise RuntimeError(
                            f"Cloudflare model {model_name!r} is on the "
                            f"Workers Paid plan - your free plan doesn't "
                            f"include it. Pick a free-tier model with /model, "
                            f"or upgrade at "
                            f"https://dash.cloudflare.com/?to=/:account/"
                            f"workers/plans. (The paid-only verdict has been "
                            f"cached - subsequent `agentkthx models "
                            f"--backend cf` runs with CLOUDFLARE_FREE_ONLY=true "
                            f"will filter this model out automatically.) "
                            f"Details: {err_msg}"
                        ) from e
                    # Plain 403 (no code 5035) - token lacks
                    # Workers AI:Edit permission. Read alone returns 403;
                    # Edit is required even for inference.
                    raise RuntimeError(
                        "Cloudflare permission denied. The token lacks "
                        "Workers AI:Edit permission (Edit is required even "
                        "for inference \u2014 Read alone returns 403). Recreate "
                        "the token with both Workers AI:Read AND Workers AI:Edit."
                    ) from e
                if status_code == 404:
                    raise RuntimeError(
                        f"Cloudflare model not found (or wrong account ID): "
                        f"{err_msg}. Verify the model ID at "
                        f"https://developers.cloudflare.com/workers-ai/models/ "
                        f"(e.g. '@cf/meta/llama-3.3-70b-instruct-fp8-fast'). "
                        f"Also verify CLOUDFLARE_ACCOUNT_ID matches the token's "
                        f"account scope \u2014 a mismatched account ID returns "
                        f"404 even with a valid token."
                    ) from e
                if status_code == 422:
                    raise RuntimeError(
                        f"Cloudflare validation error: {err_msg}. Vision "
                        f"models (@cf/meta/llama-3.2-*-vision-instruct) and "
                        f"reasoning distill models (@cf/deepseek-ai/"
                        f"deepseek-r1-distill-*) don't support tools \u2014 use "
                        f"force_react=True. GPT-OSS models require the Responses "
                        f"API (/responses, not /chat/completions)."
                    ) from e

                raise RuntimeError(f"Cloudflare API error {status_code}: {err_msg}") from e

            except urllib.error.URLError as e:
                # Network-level error \u2014 retry once with backoff, then surface.
                # R07.24 (MAINT-23/ROB-29): delegate backoff to the shared
                # _compute_network_backoff helper on CloudBackend.
                if attempt < max_retries:
                    backoff = self._compute_network_backoff(attempt)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [Cloudflare] connection error ({e.reason}), "
                            f"retrying in {backoff:.0f}s"
                        )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"Cloudflare connection error: {e.reason}") from e

        # Should not reach here \u2014 the loop either returns or raises
        raise RuntimeError(f"Cloudflare retries exhausted. Last error: {last_error_msg}")

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # _iter_sse_lines \u2014 streaming POST with retry (mirrors NVIDIA)
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to Cloudflare's /chat/completions endpoint.

        Yields raw SSE line bytes for the inherited
        ``generate_completions_stream()`` to parse.

        Implements:
          - ARCH-03 context-length 400 recovery (delegates to the shared
            ``_handle_context_length_400`` helper inherited from
            ``OpenAICompatibleBackend``; Cloudflare uses the standard
            OpenAI error wording so no regex override is needed)
          - 429 / 5xx retry honoring ``Retry-After`` (R07.24 helpers)
          - Daily-neuron-quota 429 detection (NOT retryable \u2014 surfaces
            immediately with a clear daily-quota-exhausted message)
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
                # Thinking models can take 60-90+ seconds before the first
                # token. Use a longer timeout for them (300s vs default 120s)
                # so the connection doesn't timeout mid-reasoning.
                model_name = body.get("model", "")
                stream_timeout = (
                    300 if self._name_matches_thinking(model_name) else self.config.timeout
                )
                response = urllib.request.urlopen(req, timeout=stream_timeout)
            except urllib.error.HTTPError as e:
                status_code = e.code
                body_bytes = e.read() if e.fp else b""
                body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""

                # Special case: Cloudflare daily-neuron-quota exhaustion 429
                # \u2014 NOT retryable. Surface immediately with a clear message
                # about the UTC midnight reset rather than burning through
                # retries that won't help.
                if (
                    status_code == 429
                    and _looks_like_neuron_quota_exhaustion(status_code, body_text)
                    and attempt == 0
                ):
                    raise RuntimeError(
                        f"Cloudflare Workers AI daily neuron quota exhausted. "
                        f"Quota resets at UTC midnight \u2014 wait for the reset, "
                        f"switch to another backend for the day, or upgrade to a "
                        f"paid Workers plan for paid overage at $0.011/1k neurons "
                        f"beyond the daily cap. Details: {body_text[:300]}"
                    ) from e

                # ARCH-03: shared context-length 400 handler. Only on
                # the first attempt (don't loop forever on a 400).
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [Cloudflare-Stream] Context length exceeded \u2014 "
                                f"reducing max_tokens {old_max} \u2192 {new_max} and retrying"
                            )
                        continue

                # Parse error envelope
                err_msg = body_text[:500] or f"HTTP {status_code}"
                try:
                    err_data = json.loads(body_text) if body_text else None
                    if isinstance(err_data, dict):
                        if "error" in err_data:
                            inner = err_data["error"]
                            if isinstance(inner, dict):
                                err_msg = inner.get("message", str(inner))
                            else:
                                err_msg = str(inner)
                        elif "message" in err_data:
                            err_msg = err_data["message"]
                except (json.JSONDecodeError, ValueError):
                    pass

                last_error_msg = err_msg

                # Retryable: 429 + 5xx. R07.24 (MAINT-23/ROB-29): delegate
                # the retryable-classification + Retry-After + backoff
                # calculation to the shared CloudBackend helpers.
                if self._is_retryable_http_status(status_code) and attempt < max_retries:
                    retry_after = self._compute_retry_after(e.headers, attempt)
                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        print(
                            f"  [Cloudflare-Stream] {status_code} \u2014 {err_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    time.sleep(retry_after)
                    continue

                # Non-retryable OR exhausted retries
                if status_code == 401:
                    raise RuntimeError(
                        "Cloudflare authentication failed. Check your "
                        "CLOUDFLARE_API_KEY environment variable."
                    ) from e
                if status_code == 403:
                    # Same 5035 paid-plan-only special-case as the non-streaming
                    # path (_make_api_request). Caches the paid-only verdict
                    # so subsequent list_models() with CLOUDFLARE_FREE_ONLY=true
                    # filters this model out automatically.
                    err_code = _extract_cloudflare_error_code(body_text)
                    if err_code == _CF_PAID_ONLY_ERROR_CODE:
                        model_name = body.get("model", "?")
                        try:
                            cache_cloudflare_paid_only(model_name, paid_only=True)
                        except Exception as cache_err:
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(
                                    f"  [Cloudflare-Stream] cache_cloudflare_paid_only "
                                    f"failed for {model_name}: {cache_err}"
                                )
                        raise RuntimeError(
                            f"Cloudflare model {model_name!r} is on the "
                            f"Workers Paid plan - your free plan doesn't "
                            f"include it. Pick a free-tier model with /model, "
                            f"or upgrade at "
                            f"https://dash.cloudflare.com/?to=/:account/"
                            f"workers/plans. (The paid-only verdict has been "
                            f"cached - subsequent `agentkthx models "
                            f"--backend cf` runs with CLOUDFLARE_FREE_ONLY=true "
                            f"will filter this model out automatically.) "
                            f"Details: {err_msg}"
                        ) from e
                    raise RuntimeError(
                        "Cloudflare permission denied. The token lacks "
                        "Workers AI:Edit permission (Edit is required even "
                        "for inference - Read alone returns 403). Recreate "
                        "the token with both Workers AI:Read AND Workers AI:Edit."
                    ) from e
                raise RuntimeError(f"Cloudflare API error {status_code}: {err_msg}") from e

            except urllib.error.URLError as e:
                # Network-level error \u2014 retry once with backoff, then surface.
                # R07.24 (MAINT-23/ROB-29): delegate backoff to the shared
                # _compute_network_backoff helper on CloudBackend.
                if attempt < max_retries:
                    backoff = self._compute_network_backoff(attempt)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [Cloudflare-Stream] connection error ({e.reason}), "
                            f"retrying in {backoff:.0f}s"
                        )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"Cloudflare connection error: {e.reason}") from e

            # Success \u2014 yield raw SSE line bytes. The base class's
            # generate_completions_stream() handles the JSON parsing,
            # [DONE] detection, and delta/tool_call extraction.
            #
            # ROB-06 (R07.25 CLOSED): upgraded to use the deterministic
            # _close_http_response helper (fp.close() + release_conn() +
            # close()) so Windows doesn't leak the TCP connection.
            try:
                for line in response:
                    yield line
            finally:
                close_helper = getattr(self, "_close_http_response", None)
                if callable(close_helper):
                    close_helper(response)
                else:
                    try:
                        response.close()
                    except Exception:
                        pass
            return  # success \u2014 don't retry

        # Should not reach here \u2014 the loop either yields + returns, or raises
        raise RuntimeError(f"Cloudflare-Stream retries exhausted. Last error: {last_error_msg}")

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # generate \u2014 non-streaming entry point (mirrors NVIDIA)
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response from the Cloudflare Workers AI API.

        Always uses OpenAI Chat-Completions format. The ``think`` parameter
        is ignored (Cloudflare's Workers AI manages thinking internally via
        the model's own template \u2014 callers should use ``reasoning_effort``
        for models that support it, like DeepSeek-R1-distill).

        CLOUDFLARE_FREE_ONLY enforcement: since Cloudflare's neuron quota is
        account-wide (not per-model), FREE_ONLY doesn't filter the catalog.
        It only changes the error message on a quota-exhausted 429 to point
        users at the UTC midnight reset instead of generic rate-limit
        boilerplate.
        """
        # JEV dispatch \u2014 if api_mode is JEV, route through
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
                "  [Cloudflare] 'think' parameter ignored \u2014 "
                "use 'reasoning_effort' to control thinking depth"
            )

        # Build OpenAI-spec request body. The inherited _build_openai_body
        # handles tools, temperature, max_tokens, top_p, top_k, seed,
        # presence_penalty, frequency_penalty, stop, response_format,
        # tool_choice, reasoning_effort. Cloudflare accepts all of these
        # EXCEPT top_k and repetition_penalty (NOT supported on the
        # OpenAI-compat path \u2014 silently dropped by Cloudflare).
        #
        # Cloudflare-specific: ``options.rejectIfBusy`` is a custom field
        # that, when True, fails the request immediately if Cloudflare's
        # capacity is exhausted rather than waiting in a queue. We expose
        # it via the ``reject_if_busy`` kwarg.
        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

        # Cloudflare-specific extension: options.rejectIfBusy
        # (latency-sensitive agentic workflows). Default off \u2014 only on
        # when explicitly requested via ``reject_if_busy=True``.
        if kwargs.get("reject_if_busy"):
            body["options"] = {"rejectIfBusy": True}

        # top_k is not supported by Cloudflare on the OpenAI-compat path \u2014
        # drop it silently (the inherited _build_openai_body includes it
        # only when supplied, but Cloudflare 400s if it sees it).
        body.pop("top_k", None)

        return self._make_api_request(body, stream=False)

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # generate_stream \u2014 thin text-delta wrapper (parity with NVIDIA/Mistral/OpenRouter)
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    #
    # ARCH-01: delegates to the inherited ``generate_completions_stream``
    # (from ``OpenAICompatibleBackend``). Yields just the text content
    # deltas \u2014 the agent loop calls ``generate_completions_stream``
    # directly to get the full dict-shape (delta + tool_calls +
    # finish_reason + reasoning_content).

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from Cloudflare Workers AI.

        Thin wrapper over the inherited ``generate_completions_stream``
        (from ``OpenAICompatibleBackend``). Yields just the text content
        deltas \u2014 the agent loop calls ``generate_completions_stream``
        directly to get the full dict-shape (delta + tool_calls +
        finish_reason + reasoning_content).

        The actual HTTP transport + retry/recovery lives in
        ``_iter_sse_lines`` above.
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

    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # _jev_call_completions \u2014 JEV hook (mirrors NVIDIA)
    # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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
        """JEV hook for Cloudflare: route the decision call through
        Cloudflare's Bearer-authenticated ``/chat/completions`` endpoint.

        The response shape is normalized to match generate():
        ``{content, tool_calls, usage, latency_ms, raw}``.
        """
        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=None,  # decisions never call tools
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            response_format=response_format,
            **kwargs,
        )
        # top_k not supported on the OpenAI-compat path
        body.pop("top_k", None)
        return self._make_api_request(body, stream=False)
