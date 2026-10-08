"""
⚛️ AgentKthx — SiliconFlow API Backend
Backend implementation for the SiliconFlow cloud inference API.

SiliconFlow (siliconflow.com) is a China-hosted OpenAI-compatible
aggregator (79-model live catalog at the R07.29 probe: DeepSeek, Qwen,
GLM, Kimi, MiniMax, Hunyuan, Gemma, gpt-oss — plus media/embedding
families this backend blocklists). This backend inherits the
OpenAI Chat-Completions logic from CloudBackend and adds API-key
authentication and SiliconFlow-specific defaults.

Endpoints used:
  - POST /v1/chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /v1/models           → model discovery (live catalog probe)

Configuration:
  SILICONFLOW_BASE_URL    — API base URL (default: https://api.siliconflow.com/v1;
                            users inside China can set https://api.siliconflow.cn/v1
                            for lower latency — same API, domestic TLD)
  SILICONFLOW_API_KEY     — sk- API key for authentication (required)
  SILICONFLOW_DEFAULT_MODEL — default model when none specified
                            (default: Qwen/Qwen3-8B — the cheapest known
                            tool-capable chat model, input ≈ $0.06 per 1M
                            tokens)
  SILICONFLOW_FREE_ONLY   — when true, list_models() filters to the
                            permanently-free models and generate() REJECTS
                            before building any request (default: false) —
                            see the billing note below for why this
                            currently filters to an empty catalog
  SILICONFLOW_FREE_FALLBACK_MODEL — RESERVED for a future free tier
                            (default: Qwen/Qwen3-8B; unused today — no
                            free models exist, R07.29 billing probe)

Billing reality (R07.29 billing probe, Oct 2026):
  - NO free models on the SiliconFlow API. Qwen/Qwen3-8B — the
    cheapest known chat model — BILLS: a 235-input-token request cost
    $0.000014 on the billing console (meter
    qwen/qwen3-8b.online.input-tokens; ≈ $0.06 per 1M input tokens).
    The earlier "0.563K tokens → $0.0000" console row that seeded the
    free-tier claim was 4-decimal DISPLAY ROUNDING (real ≈ $0.0000338).
  - The formerly-documented free models are gone or billing:
    Qwen/Qwen3-8B bills; deepseek-ai/DeepSeek-R1-Distill-Qwen-7B and
    deepseek-ai/DeepSeek-OCR are absent from GET /v1/models.
  - Every model draws on the account balance — keep it topped up
    (https://cloud.siliconflow.com) for sustained runs.

Tool support:
  Most chat models support OpenAI-compatible function calling.
  Reasoning models (DeepSeek-R1 family, R1-distills, Kimi-K2-Thinking)
  and vision models (deepseek-vl2, Qwen*-VL*, GLM-*V) do NOT — sending
  `tools` in the request body returns 400. The name-pattern
  classification in test_tool_support pre-classifies those as REACT;
  the runtime 400→ReAct fallback is the safety net for anything the
  patterns miss.

Error shape note (SiliconFlow is heterogeneous):
  400/429/503 return JSON objects (``{"code", "message", "data"}`` for
  400/503, ``{"message", "data"}`` for 429); 401/404/504 return PLAIN
  STRINGS (``"Invalid token"``, ``"404 page not found"``). The shared
  ``_parse_error_envelope`` handles both shapes: it tries JSON first
  and falls back to the raw body text, so no SiliconFlow-specific
  envelope parser is needed.

Quota model:
  Unlike NVIDIA (account-wide monthly credits) and Cloudflare (daily
  neurons), SiliconFlow splits by STATUS + BODY wording: account
  balance exhaustion surfaces as HTTP 402 "Sorry, your account balance
  is insufficient" (live-observed R07.29, 2026-10-09 smoke run on a
  balance-emptied account) or as balance/quota wording on a 429 body —
  both NOT retryable (top up the balance; there is no free model to
  switch to). A 429 carrying "TPM limit reached" / "rate limiting" is
  a transient rate limit (retry with backoff).
  ``_looks_like_quota_exhaustion`` implements the split with the
  transient indicators checked FIRST so "TPM limit reached" can never
  trip the quota fast-fail on the word "limit" (the same
  wording-drift class the Cloudflare "limit" bug taught MAINT-28).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from agentkthx import model_cache
from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.config import (
    SILICONFLOW_BASE_URL,
    SILICONFLOW_DEFAULT_MODEL,
    SILICONFLOW_FREE_ONLY,
)
from agentkthx.core.types import BackendType, ToolSupportLevel
from agentkthx.model_cache import load_seed_catalog

# SiliconFlow model catalog with metadata for context sizing and defaults.
# Keys are FULL prefixed model IDs (the "<author>/<model>" convention that
# mirrors HuggingFace naming, e.g. "Qwen/Qwen3-8B",
# "deepseek-ai/DeepSeek-R1") because SiliconFlow's API REQUIRES the full
# prefixed name in the request body — the same contract as the NVIDIA
# seed (see _catalog_model_key below).
#
# Pricing: NO entry carries 0.0/0.0 — the SiliconFlow API has no free
# models (billing-verified R07.29: Qwen/Qwen3-8B billed $0.000014 for
# 235 input tokens, ≈$0.06/1M input; the earlier "$0.0000" console row
# was 4-decimal display rounding). Qwen/Qwen3-8B seeds its verified
# INPUT price only (output is unknown — omitted, never fabricated);
# every other model carries NO pricing key. _is_free_model() therefore
# returns False for everything (pricing.get("input", -1) != 0.0),
# driving free_tier + SILICONFLOW_FREE_ONLY.
SILICONFLOW_MODELS: dict[str, dict] = load_seed_catalog("siliconflow")
"""Static catalog for the siliconflow backend — R07.29 seed (30 chat
models, each confirmed on the live /v1/models probe; the 27 stale
entries that would 404 are pruned). Serves as the initial
defaults of the persistent model-catalog cache and the offline fallback
list. Update the seed JSON (or refresh the backend's cache from the
live API) instead of editing code here.
"""

# Default model when none specified. Qwen/Qwen3-8B is the cheapest
# known tool-capable chat model (32K context, input ≈$0.06/1M tokens —
# BILLS, not free) — the recommended default for cost-conscious agentic
# workflows. Override via SILICONFLOW_DEFAULT_MODEL env var.
SILICONFLOW_DEFAULT_MODEL_STR = SILICONFLOW_DEFAULT_MODEL or "Qwen/Qwen3-8B"

#: Verified-live permanently-free CHAT models — EMPTY as of the R07.29
#: billing probe (Oct 2026): the SiliconFlow API bills every model.
#: Qwen/Qwen3-8B — the former "sole verified-live free model" — cost
#: $0.000014 for 235 input tokens (the earlier $0.0000 console row was
#: 4-decimal display rounding), and the formerly-documented free
#: deepseek-ai/DeepSeek-R1-Distill-Qwen-7B / deepseek-ai/DeepSeek-OCR
#: are no longer served. Kept (empty) as the ground-truth extension
#: point: when SiliconFlow (re)introduces a genuinely-free model, add
#: its ID here + seed 0.0/0.0 pricing and the free_tier flag, the
#: FREE_ONLY filter, and the reserved fallback all reactivate.
_SILICONFLOW_FREE_CHAT_MODELS: frozenset[str] = frozenset()


def _is_free_model(model: str) -> bool:
    """Check if a SiliconFlow model is permanently free.

    Free = zero input AND output pricing in the seed catalog. As of
    the R07.29 billing probe (Oct 2026) this returns False for EVERY
    model: the SiliconFlow API has no free models. Qwen/Qwen3-8B —
    the former free-tier claim — bills ≈$0.06/1M input tokens
    ($0.000014 for a 235-token request), so its seed entry now carries
    input 0.06 instead of 0.0/0.0. The other seeded models omit the
    pricing key entirely (prices are not fabricated), which resolves
    to not-free via the ``pricing.get("input", -1) != 0.0`` contract.

    Returns False for models NOT in the catalog (unknown models the
    live /v1/models endpoint surfaces before the seed is updated —
    conservatively treated as paid; the verified-free fallback set
    is empty).

    NOTE: The seed catalog keys on the FULL prefixed model ID (e.g.
    "Qwen/Qwen3-8B") because SiliconFlow's API requires the full
    prefixed name in the request body. This function does NOT strip
    the prefix — the model arg must match the catalog key exactly.
    """
    meta = SILICONFLOW_MODELS.get(model)
    if not meta:
        # Not in the seed — fall back to the verified free set
        # (currently empty — every unknown model is conservatively
        # paid until a genuinely-free model is re-verified).
        return model in _SILICONFLOW_FREE_CHAT_MODELS
    pricing = meta.get("pricing", {})
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0


#: Name-pattern blocklist for non-chat models AgentKthx can't drive
#: today (regex, matched with re.search against the lowercased FULL
#: model ID). Conservative by design — only patterns that clearly
#: indicate non-chat or non-text models are included. Legitimate chat
#: models (even ones we haven't seeded) pass through automatically.
#:
#: Categories covered:
#:   - OCR: ocr (deepseek-ai/DeepSeek-OCR — image→text, not chat)
#:   - Vision / multimodal: -vl / vl- / vl2 (Qwen2.5-VL-*, deepseek-vl2),
#:     glm-<version>v (zai-org/GLM-4.5V / GLM-4.6V / GLM-5V-Turbo),
#:     omni (Qwen3-Omni-*), captioner, vision
#:   - Embeddings: embed (BGE family on the same key)
#:   - Rerankers: rerank (BGE reranker family)
#:   - Translation: \bmt\b (tencent/Hunyuan-MT-7B)
#:   - Media generation / audio: flux, stable-diffusion, tts, audio,
#:     whisper (SiliconFlow serves these families on the same key)
#:
#: When image I/O lands, remove the vision/multimodal patterns from
#: this blocklist and seed the corresponding models.
_NON_CHAT_PATTERNS: tuple[str, ...] = (
    r"ocr",  # deepseek-ai/DeepSeek-OCR (formerly free tier; image→text only)
    r"-vl",  # Qwen/Qwen2.5-VL-7B-Instruct, *-VL-*
    r"vl-",  # mid-name VL variants
    r"vl2",  # deepseek-ai/deepseek-vl2
    r"glm-\d[\d.]*v",  # zai-org/GLM-4.5V, GLM-4.6V, GLM-5V, GLM-5V-Turbo
    r"\bomni\b",  # Qwen/Qwen3-Omni-30B-A3B-{Instruct,Thinking,Captioner}
    r"captioner",  # Qwen/Qwen3-Omni-*-Captioner (double cover with omni)
    r"vision",  # explicit vision naming
    r"embed",  # BAAI/bge-* embeddings
    r"rerank",  # BAAI/bge-reranker-*
    r"bge",  # BAAI/bge-m3 + the bge-large/bge-small family (no chat model carries bge)
    r"\bmt\b",  # tencent/Hunyuan-MT-7B (translation vertical)
    r"flux",  # black-forest-labs/FLUX.* (image generation)
    r"stable-diffusion",  # image generation
    r"\btts\b",  # text-to-speech
    r"\baudio\b",  # audio models
    r"whisper",  # speech-to-text
)


def _is_non_chat_model(model_name: str) -> bool:
    """Check if a model name matches the non-chat blocklist.

    Returns True if the model should be filtered out of the live
    /v1/models results (OCR, vision/multimodal, embeddings, rerankers,
    translation, media-generation, audio models that AgentKthx can't
    drive today with chat-text I/O only).

    Conservative by design — only blocks patterns that CLEARLY indicate
    non-chat models. Legitimate chat models (even ones not in the seed
    catalog) pass through. When image I/O lands, remove the
    vision/multimodal patterns to un-block those models.
    """
    name_lower = model_name.lower()
    return any(re.search(pattern, name_lower) for pattern in _NON_CHAT_PATTERNS)


def _looks_like_balance_exhaustion(status_code: int, body_text: str) -> bool:
    """Detect SiliconFlow's paid-balance exhaustion (402 or 429).

    Live-observed (R07.29, 2026-10-09 smoke run on a balance-emptied
    account): balance exhaustion surfaces as HTTP 402 "Sorry, your
    account balance is insufficient" — 402 Payment Required is the
    primary balance-billing signal. 429 carries both transient rate
    limits ("TPM limit reached" — retryable) and balance-exhaustion
    wording (NOT retryable — top up the balance; no free model exists
    to switch to). Either way the two are distinguished by the error
    body:

      - Transient rate limit: message contains "rate limiting" / "TPM"
      - Balance exhaustion: message contains "balance" / "quota" /
        "insufficient" / "arrear"

    The transient indicators are checked FIRST so a "TPM limit reached"
    message can never trip the quota fast-fail on the word "limit" (the
    exact wording-drift class the Cloudflare "limit" bug taught MAINT-28
    — Cloudflare's classifier deliberately excludes "limit" for the
    same reason). SiliconFlow bills every model (no free tier, R07.29
    billing probe), so quota/balance wording is always an
    account-balance condition.

    This helper lets the shared retry loop classify the 402/429
    correctly — transient ones back off and retry, balance-exhaustion
    surfaces immediately with a clear top-up message.
    """
    if status_code not in (402, 429):
        return False
    if not body_text:
        return False
    body_lower = body_text.lower()
    # Transient indicators veto the quota classification FIRST.
    if "tpm" in body_lower or "rate limit" in body_lower:
        return False
    indicators = ("balance", "quota", "insufficient", "arrear")
    return any(ind in body_lower for ind in indicators)


class SiliconFlowBackend(CloudBackend):
    """Backend for the SiliconFlow API (OpenAI Chat-Completions compatible).

    Inherits from ``CloudBackend`` (MAINT-02, R07.05) which provides:
      - ``__init__`` resolving base_url + API-key validation + OPENAI/JEV forcing
      - ``is_running()`` — True iff API key configured
      - ``_get_auth_headers()`` — standard Bearer + Content-Type
      - ``_get_model_defaults()`` — catalog lookup + max_tokens cap
      - ``get_model_info()`` / ``get_model_max_context()`` — catalog lookup
      - ``_make_api_request()`` / ``_iter_sse_lines()`` — the R07.28
        shared retry/recovery loops (quota-429 fast-fail, context-length
        400 recovery, Retry-After backoff, deterministic close)
      - ``generate_stream()`` — the ARCH-01 text-delta wrapper
      - ``_jev_call_completions()`` — the shared JEV decision call

    SiliconFlow-specific overrides:
      - ``MODELS`` — static catalog from model_seed.json
      - ``backend_type`` — ``BackendType.SILICONFLOW``
      - ``_get_chat_completions_url()`` — ``{base}/chat/completions``
      - ``_catalog_model_key()`` — full prefixed IDs (NVIDIA contract)
      - ``list_models()`` — queries ``/v1/models`` and merges with the
        static catalog (ROB-42-hardened Mistral pattern: narrowed
        except, store-on-success, stale-first fallback)
      - ``_apply_free_only()`` — SILICONFLOW_FREE_ONLY catalog filter
        (the only FREE_ONLY that actually filters among the four
        documented providers — SiliconFlow prices per model; with no
        free models live, the filter currently empties the catalog)
      - ``test_tool_support()`` — reasoning + vision models → REACT
      - ``_looks_like_quota_exhaustion()`` — balance vs TPM split
      - ``generate()`` — entry point, FREE_ONLY reject (no free models
        — refuses before any billable request instead of silently
        billing), JEV dispatch, builds body, calls
        ``_make_api_request(stream=False)``

    Usage:
        backend = get_backend("siliconflow")
        backend = SiliconFlowBackend(api_key="sk-...")
    """

    # ─────────────────────────────────────────────────────────────────────
    # CloudBackend class-attribute overrides
    # ─────────────────────────────────────────────────────────────────────

    MODELS = SILICONFLOW_MODELS
    _api_key_env_var = "SILICONFLOW_API_KEY"
    _default_base_url = SILICONFLOW_BASE_URL
    _default_model = SILICONFLOW_DEFAULT_MODEL_STR
    _provider_label = "SiliconFlow"

    # R07.29: key for the persistent JSON model-catalog cache
    # (agentkthx/model_cache.py). The cache lives at
    # ~/.cache/agentkthx/model_catalog.json under the "siliconflow" key.
    MODEL_CACHE_KEY = "siliconflow"

    # In-process mirror of the last resolved catalog (fresh cache hit,
    # live fetch, or offline fallback). The JSON cache remains the
    # cross-process source of truth.
    _model_cache: list[dict] | None = None

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """SiliconFlow's dedicated BackendType enum value.

        Added to ``core/types.py`` in R07.29 alongside the plugin itself.
        The CLI's footer formatter reads ``backend_type.value`` to display
        the backend name in the status line — without this, the footer
        would show the first registered cloud backend's name even when
        ``--backend siliconflow`` is used.
        """
        return BackendType.SILICONFLOW

    def _validate_api_key(self, key: str) -> None:
        """Warn (not error) if the API key doesn't start with ``sk-``.

        SiliconFlow account keys are issued with the ``sk-`` prefix at
        https://cloud.siliconflow.com/account/ak (the same prefix OpenAI
        uses — a key from another provider will 401 rather than fail
        validation). A key without this prefix may still be valid (e.g.
        sub-account keys) but is unusual — surface a debug-mode warning
        so users can spot accidentally pasting an OpenAI key into
        SILICONFLOW_API_KEY.
        """
        if not key.startswith("sk-"):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [SiliconFlow] Warning: API key does not start with "
                    f"'sk-' (got prefix {key[:8]!r}...). This may be a "
                    f"key from another provider — if you see 401 errors, "
                    f"check SILICONFLOW_API_KEY."
                )

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """SiliconFlow's OpenAI-compat endpoint.

        ``SILICONFLOW_BASE_URL`` already ends with ``/v1`` (no trailing
        slash) — we append ``/chat/completions`` to form the full URL
        ``https://api.siliconflow.com/v1/chat/completions``. Users inside
        China can point ``SILICONFLOW_BASE_URL`` at
        ``https://api.siliconflow.cn/v1`` for lower latency.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the ``GET /models`` discovery endpoint."""
        return f"{self._base_url.rstrip('/')}/models"

    # ─────────────────────────────────────────────────────────────────────
    # Catalog lookup — SiliconFlow keys on FULL prefixed IDs
    # ─────────────────────────────────────────────────────────────────────
    #
    # The CloudBackend base class (cloud_base.py) normalizes catalog
    # lookups via _catalog_model_key (default: strip the provider prefix,
    # "zai/glm-4-flash" → "glm-4-flash") because ZAI/OpenRouter key their
    # catalogs on bare post-slash segments. SiliconFlow's API REQUIRES the
    # full prefixed name in the request body (e.g. "Qwen/Qwen3-8B" not
    # "Qwen3-8B"), so our seed catalog keys on full names — the same
    # NVIDIA contract (R07.28 batch 2): this single hook replaces the
    # four per-method overrides; those methods are inherited unchanged.

    def _catalog_model_key(self, model: str) -> str:
        """SiliconFlow catalog keys on FULL prefixed IDs — return as-is.

        Override of CloudBackend._catalog_model_key. The module-level
        ``_is_free_model`` helper and ``SILICONFLOW_MODELS`` both expect
        the exact catalog key (full prefixed name), so no normalization
        applies.
        """
        return model

    # ─────────────────────────────────────────────────────────────────────
    # list_models — query GET /v1/models, merge with static catalog
    # ─────────────────────────────────────────────────────────────────────
    #
    # Mirrors the Mistral pattern (the ROB-42-hardened shape both new
    # backends now follow): live /v1/models results first (blocklisted
    # against non-chat patterns), then catalog-only models (so seeded
    # variants still appear even if the API hasn't listed them yet).
    # Persistent JSON cache (R07.20) wraps the live fetch so the offline
    # fallback (seed defaults) kicks in when the network is unreachable.

    # Cache the model list for 1 hour to avoid hitting /v1/models on every
    # agent.run() — the catalog rarely changes within a session.
    _MODEL_CACHE_TTL_SECONDS = 3600  # 1 hour

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Live model cache: list[dict] + timestamp. Populated by list_models().
        # MAINT-02 (R07.05): CloudBackend.__init__ already set
        # _context_safe_max_tokens; we only add the model-cache fields here.
        self._model_cache_ts: float = 0.0

    def _fetch_live_models(self) -> list[dict]:
        """Query ``GET /v1/models`` and build the merged live catalog.

        SiliconFlow's /v1/models returns the standard OpenAI ``{object:
        list, data: [...]}`` shape. Each model entry has at minimum
        ``id`` (the full ``<author>/<model>`` ID — matches the seed
        catalog keys directly, no prefix stripping).

        Enriches API results with ``context_length`` from the static
        catalog (the API listing doesn't carry it). Sets ``free_tier``
        from the catalog pricing via ``_is_free_model()`` — currently
        False for every model (no free models on the API, R07.29
        billing probe).

        Non-chat models (OCR, vision, embeddings, rerankers, translation,
        media) are filtered by the ``_NON_CHAT_PATTERNS`` blocklist so
        the model picker only offers models AgentKthx can drive.
        """
        url = self._get_models_url()
        headers = self._get_auth_headers()
        req = urllib.request.Request(url, headers=headers, method="GET")

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        api_model_keys: set[str] = set()
        api_models = result.get("data", [])
        for m in api_models:
            name = m.get("id", "")
            if not name:
                continue
            api_model_keys.add(name)

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [SiliconFlow] API returned {len(api_model_keys)} models")

        # Build unified list: API-discovered models first (confirmed
        # available), then catalog-only models (variants the API may omit).
        seen: set[str] = set()
        models: list[dict] = []

        # Blocklist approach (not allowlist) — same reasoning as the
        # NVIDIA R07.26 follow-up #2: only clearly-non-chat patterns are
        # filtered; new chat models pass through automatically. The seed
        # catalog remains a metadata enrichment layer: models that ARE
        # in the seed get accurate context_length + free_tier; models
        # that aren't get defaults (128K context, free_tier=False —
        # unknown pricing is conservatively paid).
        for name in sorted(api_model_keys):
            if name in seen:
                continue
            # Catalog keys on the FULL prefixed ID (e.g. "Qwen/Qwen3-8B")
            # — no prefix stripping. The live /v1/models endpoint returns
            # full prefixed names that match the catalog keys directly.
            # Blocklist: skip non-chat models AgentKthx can't drive today
            if _is_non_chat_model(name):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [SiliconFlow] Skipping {name} — matches non-chat "
                        f"blocklist pattern (ocr/vision/embedding/rerank/"
                        f"translation/media)"
                    )
                continue
            seen.add(name)
            meta = SILICONFLOW_MODELS.get(name, {})
            models.append(
                {
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": meta.get("family", self._catalog_family_name()),
                        "backend": self._catalog_backend_name(),
                        "context_length": meta.get(
                            "context_length", self._DEFAULT_CONTEXT_FALLBACK
                        ),
                        "free_tier": _is_free_model(name) if meta else False,
                        "is_chat_model": True,
                        "pricing": meta.get("pricing", {}),
                    },
                }
            )

        # Catalog-only models — surfaced even if the API didn't list them
        # (temporarily unavailable variants, newly seeded entries).
        for catalog_name in sorted(SILICONFLOW_MODELS.keys()):
            if catalog_name in seen:
                continue
            seen.add(catalog_name)
            meta = SILICONFLOW_MODELS[catalog_name]
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

    def _apply_free_only(self, models: list[dict]) -> list[dict]:
        """Apply the SILICONFLOW_FREE_ONLY catalog filter.

        SiliconFlow is the only one of the four documented providers
        whose FREE_ONLY genuinely filters (per-model pricing, not an
        account-wide quota like NVIDIA/Cloudflare). With NO free
        models on the API (R07.29 billing probe — even Qwen/Qwen3-8B
        bills), the filter currently drops the catalog to EMPTY —
        honest, because there is nothing free to show. Mirrors the
        Mistral ``_apply_free_only`` shape (filters on the
        ``free_tier`` details flag); when a genuinely-free model
        returns, it lights up again automatically.
        """
        if SILICONFLOW_FREE_ONLY:
            kept = [m for m in models if m["details"].get("free_tier")]
            if not kept and models and os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    "  [SiliconFlow] FREE_ONLY filter left 0 models — the "
                    "SiliconFlow API has no free models (R07.29 billing "
                    "probe); unset SILICONFLOW_FREE_ONLY to see the full "
                    "catalog"
                )
            return kept
        return models

    def list_models(self) -> list[dict]:
        """Return the SiliconFlow model catalog (live + static, cached 1h).

        Cache layers (R07.20): L1 in-process cache (1 hour) + L2
        persistent JSON cache (30-minute TTL, ``AGENTKTHX_MODEL_CACHE_TTL``
        to override) shared across processes. When
        ``SILICONFLOW_FREE_ONLY=true``, the result is filtered to the
        permanently-free models — currently an EMPTY list (the API has
        no free models, R07.29 billing probe).

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
            return list(self._model_cache)

        # L2: persistent JSON cache — fresh entry replaces the live fetch
        cached = model_cache.get_cached_models(self.MODEL_CACHE_KEY)
        if cached is not None:
            self._model_cache = self._apply_free_only(cached)
            self._model_cache_ts = now
            return list(self._model_cache)

        # First JSON-cache contact: seed the static defaults (catalog
        # entries; stale-stamped so the live fetch below still runs).
        model_cache.ensure_seeded(self.MODEL_CACHE_KEY, self._catalog_fallback_list())

        # Live fetch — ROB-42 (R07.28): narrow the except to the
        # legitimate discovery-failure modes (the R07.24 ROB-28
        # catch-narrowing, Mistral pattern) + RuntimeError for the
        # success=false shape guard. Programming errors from a malformed
        # response shape (KeyError/AttributeError/TypeError) now
        # propagate as real bugs instead of being masked as "discovery
        # failed".
        try:
            models = self._fetch_live_models()
        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            # OSError: bare socket-level failures (read resets, DNS) can
            # escape urlopen unwrapped; URLError subclasses OSError so the
            # order is safe. OrcaRouter convention + the R07.20 cache-test
            # simulation idiom (bare `OSError("network down")`).
            OSError,
            json.JSONDecodeError,
            RuntimeError,
        ) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [SiliconFlow] live /v1/models fetch failed "
                    f"({type(e).__name__}: {e}); using stale/cache catalog"
                )
            models = None

        if models is not None:
            # ROB-42: persist ONLY on the success path — a failed fetch
            # must never overwrite the persistent cache with the static
            # seed under a fresh source="api" label (the R07.24
            # ROB-28/ROB-30 closure class).
            models = model_cache.store_models(self.MODEL_CACHE_KEY, models)
        else:
            # ROB-42: serve the stale last-known-good cache first (data
            # from a previous successful fetch — fresher than the seed),
            # then fall back to the static catalog. Mirrors Mistral's
            # get_stale_models() service.
            stale = model_cache.get_stale_models(self.MODEL_CACHE_KEY)
            models = stale if stale is not None else self._catalog_fallback_list()

        # Update L1 cache (post-FREE_ONLY filter, matching the return shape)
        models = self._apply_free_only(models)
        self._model_cache = models
        self._model_cache_ts = now

        return list(models)

    def _catalog_fallback_list(self) -> list[dict]:
        """Shape the static (seed) catalog into ``list_models()`` entries.

        Used when the live /v1/models endpoint is unreachable AND the
        persistent JSON cache is empty/missing — the static seed catalog
        (30 live-verified SiliconFlow chat models) is the offline
        fallback.
        """
        models: list[dict] = []
        for name in sorted(SILICONFLOW_MODELS.keys()):
            meta = SILICONFLOW_MODELS[name]
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

    # ─────────────────────────────────────────────────────────────────────
    # test_tool_support — override to detect reasoning + vision models
    # ─────────────────────────────────────────────────────────────────────
    #
    # CloudBackend's default returns NATIVE for all models. SiliconFlow's
    # reasoning models (DeepSeek-R1 family, R1-distills, Kimi-K2-Thinking)
    # and vision models (deepseek-vl2, Qwen*-VL*, GLM-*V) reject
    # `tools` with 400 — those should cache REACT.
    #
    # This is a NAME-PATTERN classification only: no live probe is
    # performed, and nothing is read from or written to
    # ``~/.agentkthx/tool_support.json`` — that cache belongs to the
    # local-backend auto-detection layer, and cloud backends bypass it
    # entirely. The safety net is runtime behavior: a model classified
    # NATIVE that actually rejects ``tools`` gets a 400 and the agent
    # loop falls back to the ReAct prompting path.

    #: Reasoning-model name patterns (matched against the model SEGMENT
    #: after the last "/", lowercased). Note: THUDM/GLM-Z1 is
    #: deliberately NOT here — SiliconFlow's own docs list
    #: THUDM/GLM-Z1-32B-0414 as "thinking + tools", so the conservative
    #: default (NATIVE) applies and the runtime 400 fallback covers any
    #: per-model drift.
    _REASONING_NAME_PATTERNS: tuple[str, ...] = (
        r"deepseek-r\d",  # deepseek-r1, r1-distill-qwen-7b/32b, deepseek-r1-distill-*
        r"-r1",  # explicit R1 marker
        r"\bthinking\b",  # *-thinking, ...-thinking-... (Kimi-K2-Thinking, Qwen3-*-Thinking-2507)
    )

    #: Vision/multimodal name patterns (matched against the model
    #: SEGMENT after the last "/", lowercased) — per SiliconFlow's docs,
    #: vision models "typically do NOT support tools — use ReAct".
    _VISION_NAME_PATTERNS: tuple[str, ...] = (
        r"-vl",  # qwen2.5-vl-7b-instruct, *-vl-*
        r"vl2",  # deepseek-vl2
        r"\bomni\b",  # qwen3-omni-* (omnimodal)
        r"vision",  # explicit vision naming
        r"glm-\d[\d.]*v",  # glm-4.5v, glm-4.6v, glm-5v-turbo
    )

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Reasoning + vision models default to REACT; everything else NATIVE.

        SiliconFlow's reasoning models (DeepSeek-R1 family + distills,
        Kimi-K2-Thinking) and vision models (deepseek-vl2, Qwen*-VL*,
        GLM-*V, Qwen3-Omni) reject ``tools`` in the request body with a
        400. We pre-classify those as REACT so the agent loop uses the
        ReAct prompting path instead of native function calling.

        Every other model returns NATIVE immediately from the pattern
        table. This is a NAME-PATTERN classification only: no live
        probe is performed, and nothing is read from or written to
        ``~/.agentkthx/tool_support.json`` — that cache belongs to the
        local-backend auto-detection layer, and cloud backends bypass
        it entirely. The runtime 400→ReAct fallback is the safety net
        for models the patterns miss.
        """
        model_segment = model.split("/")[-1] if "/" in model else model
        model_lower = model_segment.lower()
        for pattern in self._REASONING_NAME_PATTERNS:
            if re.search(pattern, model_lower):
                return ToolSupportLevel.REACT
        for pattern in self._VISION_NAME_PATTERNS:
            if re.search(pattern, model_lower):
                return ToolSupportLevel.REACT
        # Default: NATIVE (inherited CloudBackend behavior — most chat
        # models in the SiliconFlow catalog support OpenAI-spec function
        # calling)
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # MAINT-28 (R07.28): provider-specific retry-loop hooks
    # ─────────────────────────────────────────────────────────────────────
    #
    # The retry-loop skeleton (envelope parsing, quota-429 fast-fail,
    # 400 recovery, retryable backoff, 401/404/422 remediation) lives on
    # CloudBackend since MAINT-28 — ``_make_api_request`` and
    # ``_iter_sse_lines`` are the shared drivers; this backend supplies
    # only the SiliconFlow-specific pieces: the balance-vs-TPM 402/429
    # classifier and remediation message, and the per-status
    # remediation texts. No fixed-param 400 or request-body tweak is
    # needed (SiliconFlow accepts the OpenAI body as-is, including
    # top_k).

    #: Brand for the shared error strings ("SiliconFlow API error 429: …",
    #: "SiliconFlow connection error: …"). MAINT-28 (R07.28).
    _error_brand: str = "SiliconFlow"

    #: Per-status remediation texts (MAINT-28: one table serves both the
    #: streaming and non-streaming paths). 404 on SiliconFlow is
    #: "404 page not found" (wrong endpoint URL) per the live OpenAPI
    #: spec, but model-removal can also surface there — the text covers
    #: both. 400 remediation is NOT in this table: 400s flow through the
    #: context-length recovery and the fixed-param hook first, and a
    #: tools-rejection 400 is better served by the ReAct fallback.
    _STATUS_REMEDIATIONS: dict[int, str] = {
        401: (
            "SiliconFlow authentication failed. Check your "
            "SILICONFLOW_API_KEY environment variable (must start with "
            "'sk-'). Regenerate the key at "
            "https://cloud.siliconflow.com/account/ak."
        ),
        404: (
            "SiliconFlow model not found or wrong endpoint: {err_msg}. "
            "Verify the model ID uses the full author/model format "
            "(e.g. 'Qwen/Qwen3-8B', not 'Qwen3-8B') and that "
            "SILICONFLOW_BASE_URL is https://api.siliconflow.com/v1 "
            "(or https://api.siliconflow.cn/v1 in China; no trailing "
            "slash, no path beyond /v1)."
        ),
    }

    def _looks_like_quota_exhaustion(self, status_code: int, body_text: str) -> bool:
        """SiliconFlow quota hook: paid-balance exhaustion, 402 or 429 (MAINT-28)."""
        return _looks_like_balance_exhaustion(status_code, body_text)

    def _quota_exhaustion_message(self) -> str:
        """Balance-exhaustion remediation text (MAINT-28).

        No free-model remedy exists — the SiliconFlow API bills every
        model (R07.29 billing probe), so topping up the account balance
        is the only remedy.
        """
        return (
            "SiliconFlow account balance exhausted. Top up at "
            "https://cloud.siliconflow.com. (Note: the SiliconFlow API "
            "has no free models — every model, including Qwen/Qwen3-8B, "
            "bills against the balance.)"
        )

    # generate — non-streaming entry point (mirrors Mistral + NVIDIA)
    # ─────────────────────────────────────────────────────────────────────

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
        """Generate a response from the SiliconFlow API.

        Always uses OpenAI Chat-Completions format. The ``think``
        parameter is accepted but ignored (SiliconFlow's
        ``enable_thinking`` toggle for hybrid models is not wired at
        scaffold time — registered as a follow-up; reasoning models
        surface ``reasoning_content`` automatically, which the shared
        parser already extracts).

        SILICONFLOW_FREE_ONLY enforcement: when true, generate() RAISES
        before any request is built. The SiliconFlow API has no free
        models (billing-verified R07.29 — even Qwen/Qwen3-8B billed
        $0.000014 for 235 input tokens), so the Mistral-style
        paid→free swap would silently BILL under a flag that promises
        "free only". A flag that promises free must never emit a
        billable request — refusing loudly is the only honest
        behavior. ``SILICONFLOW_FREE_FALLBACK_MODEL`` stays reserved
        for a future free tier (re-seed 0.0/0.0 pricing to reactivate
        the swap path).
        """
        # SILICONFLOW_FREE_ONLY: refuse BEFORE any request — including
        # the JEV dispatch below, which is itself a billable LLM call.
        # R07.29 billing probe: the API has NO free models, so the
        # Mistral-style swap-to-fallback would silently bill under a
        # flag that promises "free only" — raise instead.
        if SILICONFLOW_FREE_ONLY and not self._is_free_model(model):
            raise RuntimeError(
                "SILICONFLOW_FREE_ONLY=1 but the SiliconFlow API has no "
                "free models (billing-verified Oct 2026 — even "
                "Qwen/Qwen3-8B billed $0.000014 for 235 input tokens, "
                "≈$0.06/1M input). Unset SILICONFLOW_FREE_ONLY to use the "
                "paid catalog (Qwen/Qwen3-8B is the cheapest known model) "
                "and keep the balance topped up at "
                "https://cloud.siliconflow.com."
            )

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
                "  [SiliconFlow] 'think' parameter ignored — thinking is "
                "managed by the model (enable_thinking passthrough is a "
                "registered follow-up)"
            )

        # Build OpenAI-spec request body. The inherited _build_openai_body
        # handles tools, temperature, max_tokens, top_p, top_k, seed, n,
        # presence_penalty, frequency_penalty, stop, response_format,
        # tool_choice, reasoning_effort. SiliconFlow accepts all of these
        # (vLLM-style passthrough). repetition_penalty /
        # enable_thinking are NOT forwarded at scaffold time (cloud
        # backends drop repeat_* by house convention — see the
        # follow-up finding for wiring them).
        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

        return self._make_api_request(body, stream=False)
