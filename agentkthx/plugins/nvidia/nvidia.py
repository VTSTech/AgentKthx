"""
⚛️ AgentKthx — NVIDIA NIM API Backend
Backend implementation for the NVIDIA NIM cloud inference API.

NVIDIA NIM (build.nvidia.com) provides cloud-hosted LLM inference via an
OpenAI-compatible API endpoint backed by vLLM. This backend inherits the
OpenAI Chat-Completions logic from CloudBackend and adds API-key
authentication and NVIDIA-specific defaults.

Endpoints used:
  - POST /v1/chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /v1/models           → model discovery (live catalog probe)

Configuration:
  NVIDIA_BASE_URL    — API base URL (default: https://integrate.api.nvidia.com/v1)
  NVIDIA_API_KEY     — nvapi- API key for authentication (required)
  NVIDIA_DEFAULT_MODEL — default model when none specified
                       (default: meta/llama-3.3-70b-instruct)
  NVIDIA_FREE_ONLY   — when true, 429-with-credit-exhausted errors get a
                       clearer "monthly quota exhausted" message (default: false)

Free tier (verified Oct 2026):
  - 1,000 inference credits on signup, resets MONTHLY (not daily)
  - Up to 5,000 credits by request (one-time)
  - 40 requests/minute hard rate limit
  - No credit card required
  - All models in the catalog are accessible within the credit budget
    (quota is account-wide, not per-model)

Catalog (28 entries seeded in agentkthx/data/model_seed.json):
  - Meta Llama family (3.1, 3.2, 3.3 — instruct + vision variants)
  - Mistral family (Nemo, Small, Mixtral 8x7B/8x22B)
  - Qwen family (2.5, 2.5-coder, 2.5-vl)
  - DeepSeek family (R1 reasoning, R1-distill, V3 chat)
  - NVIDIA Nemotron (70B, Super 49B, Nano 9B)
  - Microsoft Phi (4-mini, 4-multimodal)
  - IBM Granite (3.3-8B, vision 3.3-2B)
  - Google Gemma (2-27B, 2-9B)

Tool support:
  Most chat models support OpenAI-compatible function calling. Reasoning
  models (DeepSeek-R1, R1-distill, Qwen3-Thinking) do NOT support tools —
  sending `tools` in the request body returns 400. AgentKthx's test_tool_support
  probe handles this dynamically per-model. The default CloudBackend
  implementation returns NATIVE for all models, which is correct for the
  chat-capable subset of the catalog. Reasoning models that fail the probe
  cache REACT and fall back to ReAct prompting.

Written by VTSTech — https://www.vts-tech.org
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
    NVIDIA_BASE_URL,
    NVIDIA_DEFAULT_MODEL,
)
from agentkthx.core.types import BackendType, ToolSupportLevel
from agentkthx.model_cache import load_seed_catalog

# NVIDIA NIM model catalog with metadata for context sizing and defaults.
# Keys are model identifiers SEGMENT after the last "/" in the API model ID
# (e.g. "meta/llama-3.3-70b-instruct" → "llama-3.3-70b-instruct"). The
# cloud_base.get_model_info() / _get_model_defaults() strip the provider
# prefix before lookup, mirroring the ZAI pattern.
#
# Pricing: 0.0/0.0 for ALL entries because NVIDIA's credit-budget model
# means every model is "free" within the monthly quota. This triggers
# _is_free_model()=True on every model, matching the
# NVIDIA_API_TECHNICAL_REFERENCE.md guidance that NVIDIA_FREE_ONLY
# does NOT filter the catalog (returns full catalog — quota is
# account-wide, not per-model).
NVIDIA_MODELS: dict[str, dict] = load_seed_catalog("nvidia")
"""Static catalog for the nvidia backend — R07.20 moved the literal
dict out of Python into ``agentkthx/data/model_seed.json``, where it
serves as the initial defaults of the persistent model-catalog cache
(and the offline fallback list). Update the seed JSON (or refresh a
backend's cache from the live API) instead of editing code here.
"""

# Default model when none specified. NVIDIA's Llama-3.1-Nemotron-70B-Instruct
# is their flagship chat model based on Llama 3.1 70B — supports tools,
# streaming, JSON mode, 128K context. NOTE: NVIDIA's cloud endpoint does NOT
# serve bare "meta/llama-3.3-70b-instruct" — only their Nemotron-tuned
# variant. Override via NVIDIA_DEFAULT_MODEL env var.
NVIDIA_DEFAULT_MODEL_STR = NVIDIA_DEFAULT_MODEL or "nvidia/llama-3.1-nemotron-70b-instruct"


def _is_free_model(model: str) -> bool:
    """Check if a NVIDIA NIM model is free.

    NVIDIA's credit-budget model means EVERY model is "free" within the
    monthly quota — so this returns True for every catalog entry. The
    function exists for parity with the ZAI/OpenRouter pattern (which use
    per-model pricing to filter); for NVIDIA, the return value is uniform
    so FREE_ONLY doesn't filter the catalog.

    Returns False only for models NOT in the catalog (unknown models —
    the live /v1/models endpoint may surface new models before the seed
    catalog is updated; those are conservatively treated as paid until
    added to the catalog).

    NOTE: The seed catalog keys on the FULL prefixed model ID (e.g.
    "meta/llama-3.3-70b-instruct") because NVIDIA's API requires the full
    prefixed name in the request body. This function does NOT strip the
    prefix — the model arg must match the catalog key exactly.
    """
    meta = NVIDIA_MODELS.get(model)
    if not meta:
        return False
    pricing = meta.get("pricing", {})
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0


#: Name-pattern blocklist for non-chat models AgentKthx can't drive today.
#: If ANY of these substrings appears in the model name (lowercased), the
#: model is filtered out of the live /v1/models results. Conservative by
#: design — only patterns that clearly indicate non-chat models are included.
#: Legitimate chat models (even ones we haven't seeded, like kimi-k3) pass
#: through automatically.
#:
#: Categories covered:
#:   - Embeddings: embed, embedqa, nv-embed, arctic-embed
#:   - Reward / ranking: reward
#:   - Safety / guardrails: safety, guard, nemoguard
#:   - Translation: translate, riva-translate
#:   - Vision / multimodal: vision, vl-, vlm, multimodal, vila, neva,
#:     nvclip, deplot, kosmos, omni
#:   - Document parsing: parse (nemotron-parse, nemotron-parse-2.0)
#:   - Video analysis: video-detector, video-
#:   - Specialized tools: ising-calibration, muse-glimmer, diffusion
#:   - Writer.com verticals: palmyra (creative/financial/medical — not
#:     general-purpose chat)
#:   - Poolside: laguna (coding-specific SaaS, not general chat)
#:   - Snowflake: arctic (excluding arctic-embed which is caught above)
#:
#: When image I/O lands, remove the vision/multimodal patterns from this
#: blocklist and add the 5 dropped seed entries back.
_NON_CHAT_PATTERNS: tuple[str, ...] = (
    "embed",  # embed-qa-4, embedqa, nemotron-3-embed, arctic-embed, etc.
    "reward",  # nemotron-4-340b-reward
    "safety",  # content-safety, nemotron-safety-guard, nemotron-3.5-content-safety
    "guard",  # nemoguard, llama-guard, llama-3.1-nemoguard-*
    "translate",  # riva-translate-4b-instruct, riva-translate-4b-instruct-v2
    "vision",  # *-vision-instruct, llama-3.2-11b-vision-instruct
    "vl-",  # qwen2.5-vl-32b-instruct, *-vl-*
    "vlm",  # nemoretriever-1b-vlm-embed-v1
    "multimodal",  # phi-4-multimodal-instruct
    "vila",  # nvidia/vila (vision-language)
    "neva",  # nvidia/neva-22b (vision)
    "nvclip",  # nvidia/nvclip (vision-language)
    "deplot",  # google/deplot (chart→text, not general chat)
    "kosmos",  # microsoft/kosmos-2 (vision-language)
    "omni",  # nemotron-3-nano-omni-* (omnimodal: text+image+audio)
    "parse",  # nemotron-parse, nemotron-parse-2.0
    "video",  # ai-synthetic-video-detector
    "ising-calibration",  # nvidia/ising-calibration-1.5-31b
    "muse-glimmer",  # meta/muse-glimmer-30b (image gen)
    "diffusion",  # google/diffusiongemma-* (image gen)
    "palmyra",  # writer/palmyra-* (vertical: creative/financial/medical)
    "laguna",  # poolside/laguna-xs-2.1 (vertical: coding SaaS)
    "arctic-embed",  # snowflake/arctic-embed-l (caught by "embed" too, but explicit)
)


def _is_non_chat_model(model_name: str) -> bool:
    """Check if a model name matches the non-chat blocklist.

    Returns True if the model should be filtered out (embeddings, reward,
    safety, vision, translation, or specialized models that AgentKthx
    can't drive today with chat-text I/O only).

    Conservative by design — only blocks patterns that CLEARLY indicate
    non-chat models. Legitimate chat models (even ones not in the seed
    catalog) pass through. When image I/O lands, remove the vision/
    multimodal patterns to un-block those models.
    """
    name_lower = model_name.lower()
    return any(pattern in name_lower for pattern in _NON_CHAT_PATTERNS)


def _looks_like_credit_exhaustion(status_code: int, body_text: str) -> bool:
    """Detect NVIDIA NIM's monthly-credit-exhausted 429.

    NVIDIA NIM returns 429 for both transient rate limits (TPM exceeded —
    retryable) AND monthly credit quota exhaustion (NOT retryable — wait
    for next month's reset). The two are distinguished by the error body:

      - Transient rate limit: message contains "rate limit" / "TPM" / "RPM"
      - Credit exhaustion: message contains "credit" / "quota" / "balance"

    This helper lets the retry loop classify the 429 correctly — transient
    ones back off and retry, credit-exhaustion surfaces immediately with
    a clear "monthly quota exhausted" message.
    """
    if status_code != 429:
        return False
    if not body_text:
        return False
    body_lower = body_text.lower()
    indicators = ("credit", "quota", "balance", "exhausted", "monthly")
    return any(ind in body_lower for ind in indicators)


def _extract_fixed_param(body_text: str) -> tuple[str, str] | None:
    """Detect NVIDIA NIM's "param is fixed at X" 400 error.

    NVIDIA NIM returns 400 for models that have fixed parameter values.
    Example (kimi-k3): ``Validation: `top_p` is fixed at 0.95 for Kimi K3;
    overriding it is not supported (got 0.9)``

    This helper extracts (param_name, fixed_value) from the error body so
    the retry loop can drop the offending param (or set it to the fixed
    value) and retry.

    Returns:
        (param_name, fixed_value) tuple, or None if no fixed-param
        pattern is found.
    """
    if not body_text:
        return None
    import re

    # Match: `param` is fixed at VALUE
    # NVIDIA's error format: "Validation: `top_p` is fixed at 0.95 for ..."
    match = re.search(r"`(\w+)`[^`]*?fixed at\s+([\d.]+)", body_text, re.IGNORECASE)
    if match:
        return (match.group(1), match.group(2))
    return None


class NvidiaBackend(CloudBackend):
    """Backend for NVIDIA NIM API (OpenAI Chat-Completions compatible).

    Inherits from ``CloudBackend`` (MAINT-02, R07.05) which provides:
      - ``__init__`` resolving base_url + API-key validation + OPENAI/JEV forcing
      - ``is_running()`` — True iff API key configured
      - ``_get_auth_headers()`` — standard Bearer + Content-Type
      - ``_get_model_defaults()`` — catalog lookup + max_tokens cap
      - ``get_model_info()`` / ``get_model_max_context()`` — catalog lookup
      - ``test_tool_support()`` — returns NATIVE by default
      - Shared retry helpers: ``_compute_retry_after``,
        ``_compute_network_backoff``, ``_is_retryable_http_status``
      - ``_close_http_response`` (ROB-06 R07.25 deterministic close)

    NVIDIA-specific overrides:
      - ``MODELS`` — static catalog from model_seed.json
      - ``backend_type`` — ``BackendType.NVIDIA``
      - ``_get_chat_completions_url()`` — ``{base}/chat/completions``
      - ``list_models()`` — queries ``/v1/models`` discovery endpoint
        and merges with the static catalog (mirrors Mistral's pattern)
      - ``_iter_sse_lines()`` — streaming POST with 429/5xx retry +
        context-length 400 recovery + credit-exhaustion detection
      - ``_make_api_request()`` — non-streaming POST with the same
        error recovery as the streaming path
      - ``generate()`` — entry point, JEV dispatch, builds body, calls
        ``_make_api_request(stream=False)``
      - ``generate_stream()`` — text-delta wrapper over the inherited
        ``generate_completions_stream`` (parity with Mistral/OpenRouter)

    Usage:
        backend = get_backend("nvidia")
        backend = NvidiaBackend(api_key="nvapi-...")
    """

    # ─────────────────────────────────────────────────────────────────────
    # CloudBackend class-attribute overrides
    # ─────────────────────────────────────────────────────────────────────

    MODELS = NVIDIA_MODELS
    _api_key_env_var = "NVIDIA_API_KEY"
    _default_base_url = NVIDIA_BASE_URL
    _default_model = NVIDIA_DEFAULT_MODEL_STR
    _provider_label = "NVIDIA"

    # R07.20: key for the persistent JSON model-catalog cache
    # (agentkthx/model_cache.py). The cache lives at
    # ~/.cache/agentkthx/model_catalog.json under the "nvidia" key.
    MODEL_CACHE_KEY = "nvidia"

    # In-process mirror of the last resolved catalog (fresh cache hit,
    # live fetch, or offline fallback). The JSON cache remains the
    # cross-process source of truth.
    _model_cache: list[dict] | None = None

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """NVIDIA's dedicated BackendType enum value.

        Added to ``core/types.py`` in R07.26 alongside the plugin itself.
        The CLI's footer formatter reads ``backend_type.value`` to display
        the backend name in the status line — without this, the footer
        would show ``🔌 zai`` (or whatever the first registered cloud
        backend happens to be) even when ``--backend nvidia`` is used.
        """
        return BackendType.NVIDIA

    def _validate_api_key(self, key: str) -> None:
        """Warn (not error) if the API key doesn't start with ``nvapi-``.

        NVIDIA NIM cloud keys are issued with the ``nvapi-`` prefix at
        https://build.nvidia.com → Account → API Keys. A key without this
        prefix may still be valid (e.g. legacy keys, NGC personal keys
        used in self-hosted NIM containers) but is unusual — surface a
        debug-mode warning so users can spot typos like accidentally
        pasting an OpenAI key.
        """
        if not key.startswith("nvapi-"):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [NVIDIA] Warning: API key does not start with "
                    f"'nvapi-' (got prefix {key[:8]!r}...). This may be a "
                    f"key from another provider — if you see 401 errors, "
                    f"check NVIDIA_API_KEY."
                )

    def _extra_auth_headers(self) -> dict:
        """NVIDIA NIM uses the standard Bearer-token auth — no extras.

        Unlike OpenRouter (which adds HTTP-Referer + X-Title for
        attribution) or OrcaRouter (which adds X-OrcaRouter-Include-Cost),
        NVIDIA NIM uses only the standard ``Authorization: Bearer`` header.
        """
        return {}

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """NVIDIA NIM's OpenAI-compat endpoint.

        ``NVIDIA_BASE_URL`` already ends with ``/v1`` (no trailing slash)
        — we append ``/chat/completions`` to form the full URL
        ``https://integrate.api.nvidia.com/v1/chat/completions``.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the ``GET /models`` discovery endpoint."""
        return f"{self._base_url.rstrip('/')}/models"

    # ─────────────────────────────────────────────────────────────────────
    # Catalog lookup overrides — NVIDIA keys on FULL prefixed IDs
    # ─────────────────────────────────────────────────────────────────────
    #
    # The CloudBackend base class (cloud_base.py) strips the provider prefix
    # before catalog lookup (model.split("/")[-1]) because ZAI/OpenRouter
    # key their catalogs on bare post-slash segments. NVIDIA's API REQUIRES
    # the full prefixed name in the request body (e.g. "meta/llama-3.3-70b-instruct"
    # not "llama-3.3-70b-instruct"), so our seed catalog keys on full names.
    # These overrides skip the prefix-stripping so lookups match.

    def get_model_info(self, model: str) -> dict | None:
        """Look up model in the static catalog by FULL prefixed ID.

        Override of CloudBackend.get_model_info — does NOT strip the
        provider prefix because NVIDIA's catalog keys on the full name
        (e.g. "meta/llama-3.3-70b-instruct").
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

        Override of CloudBackend._get_model_defaults — does NOT strip
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

        Override of CloudBackend.get_model_max_context — does NOT strip
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
        """Override of CloudBackend._is_free_model — does NOT strip prefix."""
        return _is_free_model(model)

    # ─────────────────────────────────────────────────────────────────────
    # list_models — query GET /v1/models, merge with static catalog
    # ─────────────────────────────────────────────────────────────────────
    #
    # Mirrors the Mistral pattern (mistral.py:347-475): live /v1/models
    # results first, then catalog-only models (so flash variants and
    # newly-deployed models still appear even if the API hasn't listed
    # them yet). Persistent JSON cache (R07.20) wraps the live fetch
    # so the offline fallback (seed defaults) kicks in when the network
    # is unreachable.

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

        NVIDIA NIM's /v1/models returns the standard OpenAI ``{object: list,
        data: [...]}`` shape. Each model entry has at minimum ``id`` and
        ``owned_by``; some entries include ``context_length`` but most don't
        (the static catalog is the source of truth for context_length).

        Enriches API results with ``context_length`` from the static
        catalog. Sets ``free_tier`` from the catalog pricing via
        ``_is_free_model()`` — every cataloged NVIDIA model is "free"
        within the monthly credit budget.
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
            print(f"  [NVIDIA] API returned {len(api_model_keys)} models")

        # Build unified list: API-discovered models first (confirmed
        # available), then catalog-only models (variants the API may omit).
        seen: set[str] = set()
        models: list[dict] = []

        # R07.26 follow-up #2: BLOCKLIST approach (not allowlist).
        #
        # The prior allowlist (only seed-catalog entries) was too aggressive —
        # it dropped legitimate chat models like moonshotai/kimi-k3 that
        # NVIDIA serves but we hadn't seeded. Now we use a blocklist of name
        # patterns that clearly indicate non-chat models (embeddings, reward,
        # safety, vision, translation, etc.). Everything else passes through
        # — new chat models get included automatically.
        #
        # The seed catalog remains a metadata enrichment layer: models that
        # ARE in the seed get accurate context_length + pricing; models that
        # aren't get defaults (128K context, free_tier=True).
        for name in sorted(api_model_keys):
            if name in seen:
                continue
            # R07.26 follow-up #3: catalog keys on the FULL prefixed ID
            # (e.g. "meta/llama-3.3-70b-instruct") — no prefix stripping.
            # The live /v1/models endpoint returns full prefixed names
            # that match the catalog keys directly.
            # Blocklist: skip non-chat models AgentKthx can't drive today
            if _is_non_chat_model(name):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [NVIDIA] Skipping {name} — matches non-chat "
                        f"blocklist pattern (embedding/reward/safety/"
                        f"vision/translation/specialized)"
                    )
                continue
            seen.add(name)
            meta = NVIDIA_MODELS.get(name, {})
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
                        "free_tier": _is_free_model(name) if meta else True,
                        "is_chat_model": True,
                        "pricing": meta.get("pricing", {}),
                    },
                }
            )

        # Catalog-only models — surfaced even if the API didn't list them
        # (flash variants, models that may be temporarily unavailable).
        for catalog_name in sorted(NVIDIA_MODELS.keys()):
            if catalog_name in seen:
                continue
            seen.add(catalog_name)
            meta = NVIDIA_MODELS[catalog_name]
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
        """Return the NVIDIA NIM model catalog (live + static, cached 1h).

        Cache layers (R07.20): L1 in-process cache (1 hour) + L2
        persistent JSON cache (30-minute TTL, ``AGENTKTHX_MODEL_CACHE_TTL``
        to override) shared across processes. When ``NVIDIA_FREE_ONLY=true``,
        the return value is unchanged (every cataloged NVIDIA model is
        "free" within the monthly quota — no filtering applies), but the
        429-with-credit-exhaustion error path surfaces a clearer message.

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
            self._model_cache = cached
            self._model_cache_ts = now
            return list(cached)

        # First JSON-cache contact: seed the static defaults (catalog
        # entries; stale-stamped so the live fetch below still runs).
        model_cache.ensure_seeded(self.MODEL_CACHE_KEY, self._catalog_fallback_list())

        # Live fetch — best-effort, falls back to catalog on any failure
        try:
            models = self._fetch_live_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [NVIDIA] live /v1/models fetch failed ({e}); using catalog")
            models = self._catalog_fallback_list()

        # Persist to L2 cache (shared across processes)
        model_cache.store_models(self.MODEL_CACHE_KEY, models, source="api")

        # Update L1 cache
        self._model_cache = models
        self._model_cache_ts = now

        return list(models)

    def _catalog_fallback_list(self) -> list[dict]:
        """Shape the static (seed) catalog into ``list_models()`` entries.

        Used when the live /v1/models endpoint is unreachable AND the
        persistent JSON cache is empty/missing — the static seed catalog
        (28 NVIDIA models) is the offline fallback.
        """
        models: list[dict] = []
        for name in sorted(NVIDIA_MODELS.keys()):
            meta = NVIDIA_MODELS[name]
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
    # test_tool_support — override to detect reasoning models
    # ─────────────────────────────────────────────────────────────────────
    #
    # CloudBackend's default returns NATIVE for all models. NVIDIA NIM's
    # reasoning models (DeepSeek-R1, R1-distill, Qwen3-Thinking) reject
    # `tools` with 400 — those should cache REACT.
    #
    # The reasoning-model detection is conservative: only models whose
    # name carries an explicit "r1" / "deepseek-r" / "-thinking" marker
    # are pre-classified as REACT (no live probe). Every other model
    # falls through to the inherited NATIVE default and is probed on
    # first use via the standard test_tool_support flow.

    #: Reasoning-model name patterns (matched against the post-slash segment).
    _REASONING_NAME_PATTERNS: tuple[str, ...] = (
        r"deepseek-r\d",  # deepseek-r1, r1-distill-llama-8b, deepseek-r1-distill-qwen-32b
        r"-r1",  # explicit R1 marker (e.g. llama-3.1-nemotron-r1)
        r"\bthinking\b",  # qwen3-...-thinking, ...-thinking-...
    )

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Reasoning models default to REACT (no tools); everything else NATIVE.

        NVIDIA NIM's reasoning models (DeepSeek-R1, R1-distill variants,
        Qwen3-Thinking) reject ``tools`` in the request body with a 400.
        We pre-classify those as REACT so the agent loop uses the ReAct
        prompting path instead of native function calling.

        Every other model falls through to the inherited CloudBackend
        default (NATIVE) and is probed on first use via the standard
        ``test_tool_support`` flow (the probe is cached in
        ``~/.agentkthx/tool_support.json``).
        """
        model_segment = model.split("/")[-1] if "/" in model else model
        model_lower = model_segment.lower()
        for pattern in self._REASONING_NAME_PATTERNS:
            if re.search(pattern, model_lower):
                return ToolSupportLevel.REACT
        # Default: NATIVE (inherited CloudBackend behavior — most chat
        # models in the NVIDIA catalog support OpenAI-spec function calling)
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # _make_api_request — non-streaming POST with retry (mirrors Mistral)
    # ─────────────────────────────────────────────────────────────────────

    def _make_api_request(self, body: dict, *, stream: bool = False) -> dict:
        """POST to ``/chat/completions`` with 429/5xx retry.

        Honors ``Retry-After`` when present (429 rate-limit and 503
        service-unavailable). Falls back to exponential backoff with
        full jitter. Max retries determined by ``_max_retries()`` (default
        4, override via ``AGENTKTHX_MAX_API_RETRIES`` env var).

        Special case for NVIDIA NIM: a 429 whose body indicates credit
        exhaustion (contains "credit" / "quota" / "balance") is NOT
        retryable — the monthly quota has been hit and retrying won't help.
        Such 429s surface immediately as a clear RuntimeError so the user
        knows to wait for the next monthly reset.

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

                # Special case: NVIDIA credit-exhaustion 429 — NOT retryable.
                # Surface immediately with a clear message about the monthly
                # reset rather than burning through retries that won't help.
                if (
                    status_code == 429
                    and _looks_like_credit_exhaustion(status_code, body_text)
                    and attempt == 0
                ):
                    raise RuntimeError(
                        f"NVIDIA NIM monthly credit quota exhausted. "
                        f"Credits reset monthly — wait for the next reset "
                        f"or request additional credits at the NVIDIA "
                        f"developer forums. Details: {err_msg}"
                    ) from e

                # Fixed-param 400: some NVIDIA NIM models have parameters
                # fixed at specific values (e.g. kimi-k3 fixes top_p=0.95).
                # Detect "fixed at X" in the error, drop the offending param
                # (or set it to the fixed value), and retry.
                if status_code == 400 and attempt == 0:
                    fixed = _extract_fixed_param(body_text)
                    if fixed:
                        param_name, fixed_value = fixed
                        if param_name in body:
                            old_val = body[param_name]
                            # Set to the fixed value (safer than dropping —
                            # some models reject requests that omit the param
                            # entirely, others accept omission. Setting to
                            # the fixed value works universally.)
                            try:
                                body[param_name] = type(body[param_name])(fixed_value)
                            except (ValueError, TypeError):
                                body[param_name] = fixed_value
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(
                                    f"  [NVIDIA] {param_name} is fixed at "
                                    f"{fixed_value} for this model — "
                                    f"overriding {old_val} → {body[param_name]} "
                                    f"and retrying"
                                )
                            continue

                # ARCH-03: shared context-length 400 handler. Only on
                # the first attempt (don't loop forever on a 400).
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [NVIDIA] Context length exceeded — "
                                f"reducing max_tokens {old_max} → {new_max} and retrying"
                            )
                        continue

                # Retryable: 429 (rate limit, NOT credit exhaustion) +
                # 5xx (transient server errors). R07.24 (MAINT-23/ROB-29):
                # delegate to the shared _is_retryable_http_status helper.
                if self._is_retryable_http_status(status_code) and attempt < max_retries:
                    retry_after = self._compute_retry_after(e.headers, attempt)
                    if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                        print(
                            f"  [NVIDIA] {status_code} — {err_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    time.sleep(retry_after)
                    continue

                # Non-retryable OR exhausted retries
                if status_code == 401:
                    raise RuntimeError(
                        "NVIDIA NIM authentication failed. Check your "
                        "NVIDIA_API_KEY environment variable (must start "
                        "with 'nvapi-'). Get a key at "
                        "https://build.nvidia.com → Account → API Keys."
                    ) from e
                if status_code == 404:
                    raise RuntimeError(
                        f"NVIDIA NIM model not found: {err_msg}. "
                        f"Verify the model ID at https://build.nvidia.com "
                        f"(e.g. 'meta/llama-3.3-70b-instruct' not "
                        f"'llama-3.3-70b-instruct')."
                    ) from e
                if status_code == 422:
                    raise RuntimeError(
                        f"NVIDIA NIM validation error: {err_msg}. "
                        f"Reasoning models (DeepSeek-R1, R1-distill, "
                        f"Qwen3-Thinking) don't support tools — use "
                        f"force_react=True."
                    ) from e

                raise RuntimeError(f"NVIDIA NIM API error {status_code}: {err_msg}") from e

            except urllib.error.URLError as e:
                # Network-level error — retry once with backoff, then surface.
                # R07.24 (MAINT-23/ROB-29): delegate backoff to the shared
                # _compute_network_backoff helper on CloudBackend.
                if attempt < max_retries:
                    backoff = self._compute_network_backoff(attempt)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [NVIDIA] connection error ({e.reason}), "
                            f"retrying in {backoff:.0f}s"
                        )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"NVIDIA NIM connection error: {e.reason}") from e

        # Should not reach here — the loop either returns or raises
        raise RuntimeError(f"NVIDIA NIM retries exhausted. Last error: {last_error_msg}")

    # ─────────────────────────────────────────────────────────────────────
    # _iter_sse_lines — streaming POST with retry (mirrors Mistral)
    # ─────────────────────────────────────────────────────────────────────

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to NVIDIA NIM's /chat/completions endpoint.

        Yields raw SSE line bytes for the inherited
        ``generate_completions_stream()`` to parse.

        Implements:
          - ARCH-03 context-length 400 recovery (delegates to the shared
            ``_handle_context_length_400`` helper inherited from
            ``OpenAICompatibleBackend``; NVIDIA NIM uses the standard
            OpenAI error wording so no regex override is needed)
          - 429 / 5xx retry honoring ``Retry-After`` (R07.24 helpers)
          - Credit-exhaustion 429 detection (NOT retryable — surfaces
            immediately with a clear monthly-quota-exhausted message)
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

                # Special case: NVIDIA credit-exhaustion 429 — NOT retryable.
                if (
                    status_code == 429
                    and _looks_like_credit_exhaustion(status_code, body_text)
                    and attempt == 0
                ):
                    raise RuntimeError(
                        f"NVIDIA NIM monthly credit quota exhausted. "
                        f"Credits reset monthly — wait for the next reset "
                        f"or request additional credits at the NVIDIA "
                        f"developer forums. Details: {body_text[:300]}"
                    ) from e

                # Fixed-param 400: some NVIDIA NIM models have parameters
                # fixed at specific values (e.g. kimi-k3 fixes top_p=0.95).
                # Detect "fixed at X" in the error, set the param to the
                # fixed value, and retry.
                if status_code == 400 and attempt == 0:
                    fixed = _extract_fixed_param(body_text)
                    if fixed:
                        param_name, fixed_value = fixed
                        if param_name in body:
                            old_val = body[param_name]
                            try:
                                body[param_name] = type(body[param_name])(fixed_value)
                            except (ValueError, TypeError):
                                body[param_name] = fixed_value
                            if os.environ.get("AGENTKTHX_DEBUG"):
                                print(
                                    f"  [NVIDIA-Stream] {param_name} is fixed "
                                    f"at {fixed_value} for this model — "
                                    f"overriding {old_val} → {body[param_name]} "
                                    f"and retrying"
                                )
                            continue

                # ARCH-03: shared context-length 400 handler. Only on
                # the first attempt (don't loop forever on a 400).
                if status_code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(body_text, body):
                        new_max = body["max_tokens"]
                        if os.environ.get("AGENTKTHX_DEBUG"):
                            print(
                                f"  [NVIDIA-Stream] Context length exceeded — "
                                f"reducing max_tokens {old_max} → {new_max} and retrying"
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
                            f"  [NVIDIA-Stream] {status_code} — {err_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                    time.sleep(retry_after)
                    continue

                # Non-retryable OR exhausted retries
                if status_code == 401:
                    raise RuntimeError(
                        "NVIDIA NIM authentication failed. Check your "
                        "NVIDIA_API_KEY environment variable."
                    ) from e
                raise RuntimeError(f"NVIDIA NIM API error {status_code}: {err_msg}") from e

            except urllib.error.URLError as e:
                # Network-level error — retry once with backoff, then surface.
                # R07.24 (MAINT-23/ROB-29): delegate backoff to the shared
                # _compute_network_backoff helper on CloudBackend.
                if attempt < max_retries:
                    backoff = self._compute_network_backoff(attempt)
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(
                            f"  [NVIDIA-Stream] connection error ({e.reason}), "
                            f"retrying in {backoff:.0f}s"
                        )
                    time.sleep(backoff)
                    continue
                raise RuntimeError(f"NVIDIA NIM connection error: {e.reason}") from e

            # Success — yield raw SSE line bytes. The base class's
            # generate_completions_stream() handles the JSON parsing,
            # [DONE] detection, and delta/tool_call extraction.
            #
            # Note: the chat.py spinner handles the "thinking..." progress
            # indicator. Thinking models (GLM-5.3-flash, DeepSeek-V4.1-flash)
            # can take 60-90+ seconds before the first token — the spinner
            # runs until agent.run() returns, so it covers that gap.
            # ROB-06: try/finally so the urllib response is closed
            # deterministically when the generator is abandoned
            # mid-iteration (Ctrl+C, consumer exception, or the base
            # class's break on [DONE]).
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
            return  # success — don't retry

        # Should not reach here — the loop either yields + returns, or raises
        raise RuntimeError(f"NVIDIA-Stream retries exhausted. Last error: {last_error_msg}")

    # ─────────────────────────────────────────────────────────────────────
    # generate — non-streaming entry point (mirrors Mistral)
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
        """Generate a response from the NVIDIA NIM API.

        Always uses OpenAI Chat-Completions format. The ``think`` parameter
        is ignored (NVIDIA NIM manages thinking internally via the model's
        own template — callers should use ``reasoning_effort`` for models
        that support it, like DeepSeek-R1).

        NVIDIA_FREE_ONLY enforcement: since NVIDIA's quota is account-wide
        (not per-model), FREE_ONLY doesn't filter the catalog. It only
        changes the error message on a credit-exhausted 429 to point
        users at the monthly reset instead of generic rate-limit boilerplate.
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
                "  [NVIDIA] 'think' parameter ignored — "
                "use 'reasoning_effort' to control thinking depth"
            )

        # Build OpenAI-spec request body. The inherited _build_openai_body
        # handles tools, temperature, max_tokens, top_p, top_k, seed,
        # presence_penalty, frequency_penalty, stop, response_format,
        # tool_choice, reasoning_effort. NVIDIA NIM (via vLLM) accepts all
        # of these.
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

    # ─────────────────────────────────────────────────────────────────────
    # generate_stream — thin text-delta wrapper (parity with Mistral/OpenRouter)
    # ─────────────────────────────────────────────────────────────────────
    #
    # ARCH-01: delegates to the inherited ``generate_completions_stream``
    # (from ``OpenAICompatibleBackend``). Yields just the text content
    # deltas — the agent loop calls ``generate_completions_stream``
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
        """Stream generated text from NVIDIA NIM.

        Thin wrapper over the inherited ``generate_completions_stream``
        (from ``OpenAICompatibleBackend``). Yields just the text content
        deltas — the agent loop calls ``generate_completions_stream``
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

    # ─────────────────────────────────────────────────────────────────────
    # _jev_call_completions — JEV hook (mirrors Mistral)
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
        """JEV hook for NVIDIA NIM: route the decision call through
        NVIDIA's Bearer-authenticated ``/chat/completions`` endpoint.

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
        return self._make_api_request(body, stream=False)
