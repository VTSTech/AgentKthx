"""
⚛️ AgentKthx — TokenHarbor API Backend
Backend implementation for the TokenHarbor gateway (OpenAI Chat-Completions
compatible).

Token Harbor (tokenharbor.ai) is an OpenRouter-style LLM gateway: one
`thk_live_` universal key, one USD wallet, every model wired into the
gateway via a single OpenAI-compatible endpoint. This backend inherits the
OpenAI Chat-Completions logic from CloudBackend (the R07.28-hardened shared
transport) and adds TokenHarbor-specific identity, key validation, catalog
handling, and error classification.

Endpoints used:
  - POST /v1/chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /v1/models           → model discovery (Bearer key REQUIRED —
                                docs-verified difference from OpenRouter)

Scope note: Token Harbor also exposes an Anthropic-compatible
`/v1/messages` surface. It is OUT OF SCOPE for this backend — AgentKthx
speaks the OpenAI wire only (no _wire_adapter).

Configuration:
  TOKENHARBOR_API_KEY       — thk_live_… universal key (required; created at
                              tokenharbor.ai/dashboard/api-keys, shown once,
                              no management API). An OpenAI/OpenRouter `sk-`
                              key "won't work here" (their connect CLI docs)
                              — rejected early with remediation.
  TOKENHARBOR_BASE_URL      — API base URL (default: https://tokenharbor.ai/v1)
  TOKENHARBOR_DEFAULT_MODEL — default model
                              (default: deepseek-v4.1-flash:free — the only
                              :free id Token Harbor's docs name in an
                              actionable context; perishable, re-verify at
                              probe time. :free routes never charge the
                              wallet.)
  TOKENHARBOR_FREE_ONLY     — when true, list_models() filters to :free ids
                              (default: false)

TokenHarbor-specific behavior (all pinned in
docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md):

  - Auth-gated catalog. GET /v1/models answers 401 without a valid key,
    so the cold-start catalog source is the seed JSON (model_seed.json,
    "tokenharbor" key — docs-only ids, marked perishable). The live
    refresh runs only when a key exists, and a 401 on the fetch degrades
    to the stale cache / seed without failing agent startup (the ROB-42
    narrowed-except + stale-first contract).

  - The spend gate. Any single call whose pessimistic estimate (full
    prompt, no cache discount, full max_tokens) exceeds HALF the current
    wallet balance is paused before it runs: HTTP 400, code
    `spend_confirmation_required`. AgentKthx NEVER auto-confirms — the
    gate exists so the USER decides whether a >half-balance call runs.
    The plugin surfaces the remediation text and stops (no retry, no
    `th_confirm_spend` injection). Override note: the 400 entry lives in
    _STATUS_REMEDIATIONS per the doc, but _raise_non_retryable_status
    checks the body for the code string first so a NON-spend 400 (a
    tools rejection, a param validation) keeps the generic message that
    the ReAct fallback detector pattern-matches.

  - Two 429 families. A plain 429 (free-account 60/min + 1,800/hour) is
    rate-class — the shared retry loop honors Retry-After. A 429 carrying
    `spend_limit_exceeded` is the per-key USD spend cap (rolling daily/
    weekly/monthly window) — quota-class: it only lifts when spend ages
    out of the window (potentially hours), so hammer-retrying inside the
    retry budget is pure waste; the MAINT-28 quota hook fast-fails it
    with the window-aging message. Balance-exhaustion phrasing on
    402/429 is classified quota-class too (the $0-balance-on-paid-model
    status code is undocumented — the wording match is a hedge, verify
    at probe time).

  - Key validation. Keys are `thk_live_` + base64url (docs show 64
    chars); `sk-…` keys (OpenAI, OpenRouter `sk-or-…`) are REJECTED with
    remediation — the connect CLI docs confirm they can't work. Other
    non-`thk_` prefixes draw a debug warning only (key formats may
    evolve; the 401 remediation covers actual rejections).

  - Pass-through guarantees (what the plugin must NOT do — doc §16
    invariants): system messages are forwarded byte-for-byte (the DDG/SD
    system-suppression convention does NOT apply); no OpenRouter
    attribution headers (no documented TH equivalent); no
    X-TH-Cache-Control header by default (documented escape hatch, not
    a default; no speculative env vars); no maker parsing from model ids
    (the vendor-list 403 is authoritative).

  - Dual usage-field family. The /v1/chat/completions usage field names
    are undocumented (OpenAI `prompt_tokens`/`completion_tokens` vs
    Anthropic-style `input_tokens`/`output_tokens`). _parse_openai_response
    accepts BOTH and surfaces `cache_creation_input_tokens` /
    `cache_read_input_tokens` when present (Token Harbor's prompt-cache
    accounting) — ignored when absent.

  - Tools. Aggregator assumption: every chat-capable model is treated as
    NATIVE (inherited CloudBackend default — no per-model probe). The
    runtime safety net: a 400 "does not support tools" rejection retries
    once without the tools field (ReAct fallback), same as the
    OpenRouter backend.

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
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.config import (
    TOKENHARBOR_BASE_URL,
    TOKENHARBOR_DEFAULT_MODEL,
    TOKENHARBOR_FREE_ONLY,
)
from agentkthx.core.types import BackendType
from agentkthx.model_cache import load_seed_catalog

# TokenHarbor model catalog (seed-backed offline fallback + metadata
# enrichment layer). Keys are the model ids EXACTLY as Token Harbor's docs
# use them — the id scheme is opaque (bare ids, `tokenharbor/`-prefixed ids,
# and `:free`-suffixed ids coexist; "Model id is whatever appears in
# /models"). No pricing or context_length is seeded: the /v1/models entry
# schema is undocumented and docs-only pricing would be fabrication —
# context is read tolerantly from the live payload when present (R07.35),
# else omitted so the CloudBackend family-knowledge layer resolves it
# (Claude 200K, GPT-6 400K, Gemini 1M, …) with the 128K generic fallback
# as the last resort.
TOKENHARBOR_MODELS: dict[str, dict] = load_seed_catalog("tokenharbor")
"""Static catalog for the tokenharbor backend — R07.31 seed (15 ids,
each named by Token Harbor's own docs: compare page, quickstart, and
Subscription named-model lists; docs-only, marked perishable via the
"_tokenharbor_note" metadata key in the seed JSON). Serves as the
initial defaults of the persistent model-catalog cache and the offline
fallback list. Update the seed JSON (or refresh the backend's cache
from the live API) instead of editing code here."""

# Default model when none specified. deepseek-v4.1-flash:free is the only
# :free id Token Harbor's own docs name in an actionable context (the
# quickstart free tier + the connect CLI's Qwen Code adapter). Perishable —
# re-verify against the live Free list at probe time.
TOKENHARBOR_DEFAULT_MODEL_STR = TOKENHARBOR_DEFAULT_MODEL or "deepseek-v4.1-flash:free"

#: Shape of a documented live key: `thk_live_` + 64 base64url chars
#: (docs observation, NOT a hard invariant — warn-only; the 401
#: remediation covers actual rejections, and key formats may evolve).
_THK_LIVE_KEY_RE = re.compile(r"^thk_live_[A-Za-z0-9_-]{64}$")


def _is_free_model(model_id: str) -> bool:
    """Check if a TokenHarbor model is a free-route (`:free`) model.

    The `:free` suffix is Token Harbor's canonical free marker — it
    routes to free promotional capacity and never charges the wallet
    (docs: "only the account limits above" apply). Unlike OpenRouter
    there is NO named free router id to whitelist; the suffix check is
    the whole rule.

    Token Harbor's ids are opaque strings, so the check is a plain
    suffix test with NO prefix stripping and NO maker parsing (doc §16
    invariant #4: the vendor-list 403 is authoritative; how the gateway
    derives a maker from an id is undocumented).
    """
    return model_id.endswith(":free")


def _is_tools_not_supported_error(err_str: str) -> bool:
    """Detect an upstream 'tools not supported' rejection (OpenRouter set).

    Same indicator list as the OpenRouter backend — Token Harbor is
    upstream-agnostic, so the rejection wording comes from whichever
    upstream served the request. The RuntimeError raised by the shared
    CloudBackend loop embeds the upstream message, so matching the
    raised text works.
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
    )
    return any(ind in err_lower for ind in indicators)


class TokenHarborBackend(CloudBackend):
    """Backend for the TokenHarbor gateway (OpenAI Chat-Completions wire).

    Inherits from ``CloudBackend`` (MAINT-02, R07.05) which provides:
      - ``__init__`` resolving base_url + API-key validation + OPENAI/JEV forcing
      - ``is_running()`` — True iff API key configured
      - ``_get_auth_headers()`` — standard Bearer + Content-Type (NO
        attribution extras — Token Harbor has no documented equivalent of
        OpenRouter's app-attribution headers, and unknown-header handling
        is undocumented, so none are sent)
      - ``_get_model_defaults()`` — catalog lookup + max_tokens cap
      - ``get_model_info()`` / ``get_model_max_context()`` — catalog lookup
      - ``_make_api_request()`` / ``_iter_sse_lines()`` — the R07.28
        shared retry/recovery loops (quota fast-fail via the hooks below,
        context-length 400 recovery, Retry-After backoff, deterministic
        close)
      - ``generate_stream()`` — the ARCH-01 text-delta wrapper
      - ``_jev_call_completions()`` — the shared JEV decision call
      - ``test_tool_support()`` — unconditional NATIVE (the aggregator
        assumption: a gateway only exposes models that already support
        native function calling upstream; the runtime 400→ReAct fallback
        in generate() is the safety net)

    TokenHarbor-specific overrides:
      - ``MODELS`` — static seed catalog (docs-only ids)
      - ``backend_type`` — ``BackendType.TOKENHARBOR``
      - ``_validate_api_key()`` — sk- rejection + thk_live_ shape hints
      - ``_get_chat_completions_url()`` / ``_get_models_url()``
      - ``_catalog_model_key()`` — ids are opaque; returned unchanged
      - ``list_models()`` — auth-gated live fetch (seed-first cold start,
        ROB-42 store-on-success + stale-first, FREE_ONLY at return time)
      - ``_apply_free_only()`` — TOKENHARBOR_FREE_ONLY (:free suffix)
      - ``_STATUS_REMEDIATIONS`` — 401 / 403 (+ 400 spend-gate text,
        delivered via the ``_raise_non_retryable_status`` override so a
        non-spend 400 keeps the generic, ReAct-detectable message)
      - ``_looks_like_quota_exhaustion()`` — 429 spend_limit_exceeded
        (documented per-key USD spend cap) + balance-exhaustion wording
        → quota-class; plain rate-limit 429 stays retryable
      - ``_quota_exhaustion_message()`` — the window-aging remediation
      - ``generate()`` — entry point with the ReAct fallback
      - ``_parse_openai_response()`` — dual usage-field family + cache
        token surfacing

    Usage:
        backend = get_backend("tokenharbor")
        backend = TokenHarborBackend(api_key="thk_live_…")
    """

    # ─────────────────────────────────────────────────────────────────────
    # CloudBackend class-attribute overrides
    # ─────────────────────────────────────────────────────────────────────

    MODELS = TOKENHARBOR_MODELS
    _api_key_env_var = "TOKENHARBOR_API_KEY"
    _default_base_url = TOKENHARBOR_BASE_URL
    _default_model = TOKENHARBOR_DEFAULT_MODEL_STR
    _provider_label = "TokenHarbor"

    # MAINT-28 (R07.28): brand for the shared error strings
    # ("Token Harbor API error 429: …", "Token Harbor connection error: …").
    _error_brand = "Token Harbor"

    # R07.31: key for the persistent JSON model-catalog cache
    # (agentkthx/model_cache.py). The cache lives at
    # ~/.cache/agentkthx/model_catalog.json under the "tokenharbor" key.
    MODEL_CACHE_KEY = "tokenharbor"

    # In-process mirror of the last resolved catalog (fresh cache hit,
    # live fetch, or offline fallback). The JSON cache remains the
    # cross-process source of truth.
    _model_cache: list[dict] | None = None

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """TokenHarbor's dedicated BackendType enum value.

        Added to ``core/types.py`` in R07.31 alongside the plugin (the
        14th backend). The CLI's footer formatter reads
        ``backend_type.value`` to display the backend name in the
        status line.
        """
        return BackendType.TOKENHARBOR

    def _validate_api_key(self, key: str) -> None:
        """Reject `sk-` keys early; warn on unexpected prefixes.

        Token Harbor's universal keys start with ``thk_live_`` (shown
        once at creation in the dashboard; there is no management API
        to re-read a lost key). Their own connect CLI docs confirm an
        OpenAI ``sk-`` key "won't work here" — and OpenRouter keys also
        start ``sk-`` (``sk-or-…``) — so an ``sk-``-prefixed value in
        TOKENHARBOR_API_KEY is rejected immediately with remediation
        instead of surfacing later as a confusing 401 on every call.

        Any other non-``thk_`` prefix draws a debug-mode warning only
        (key formats may evolve; the 401 remediation text is the
        authority for actual rejections). A ``thk_live_`` key whose
        remainder doesn't match the documented 64-char base64url shape
        also warns (docs observation, not a hard invariant — never
        reject on shape alone).
        """
        if key.startswith("sk-"):
            raise ValueError(
                "TOKENHARBOR_API_KEY looks like an OpenAI/OpenRouter key "
                f"({key[:6]}…). Token Harbor keys start with 'thk_live_' — "
                "create one at tokenharbor.ai/dashboard/api-keys (shown "
                "once at creation; the connect CLI docs confirm an OpenAI "
                "sk- key won't work here)."
            )
        if not key.startswith("thk_"):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [TokenHarbor] Warning: API key does not start with "
                    f"'thk_' (got prefix {key[:8]!r}...). If you see 401 "
                    f"errors, verify TOKENHARBOR_API_KEY — keys are "
                    f"thk_live_…, shown once at creation."
                )
            return
        if key.startswith("thk_live_") and not _THK_LIVE_KEY_RE.fullmatch(key):
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [TokenHarbor] Warning: key matches thk_live_ but not "
                    f"the documented shape (thk_live_ + 64 base64url chars, "
                    f"got {len(key)} total). Check for a truncated or "
                    f"trailing-whitespace paste — keys are shown once, so "
                    f"re-create if lost (or use the account-level Rotate)."
                )

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """TokenHarbor's OpenAI-compat endpoint.

        ``TOKENHARBOR_BASE_URL`` already ends with ``/v1`` (no trailing
        slash) — we append ``/chat/completions`` to form the full URL
        ``https://tokenharbor.ai/v1/chat/completions``.
        """
        return f"{self._base_url.rstrip('/')}/chat/completions"

    def _get_models_url(self) -> str:
        """Full URL for the ``GET /models`` discovery endpoint.

        Token Harbor's /v1/models REQUIRES AUTH (docs-verified: opened
        without a key it answers 401) — callers must send the Bearer
        header (``_get_auth_headers`` does).
        """
        return f"{self._base_url.rstrip('/')}/models"

    def _catalog_model_key(self, model: str) -> str:
        """TokenHarbor ids are opaque — return unchanged.

        Override of CloudBackend._catalog_model_key (which strips a
        provider prefix). Token Harbor has NO documented vendor/model
        convention: bare ids (``glm-5.3``), ``tokenharbor/``-prefixed
        ids (``tokenharbor/qwen3-max``), and ``:free``-suffixed ids all
        coexist, and the seed keys on the full documented id — so no
        normalization applies. (Doc §16 invariant #4: never parse a
        maker from the id either — the vendor-list 403 is authoritative.)
        """
        return model

    # ─────────────────────────────────────────────────────────────────────
    # list_models — auth-gated discovery (seed-first cold start)
    # ─────────────────────────────────────────────────────────────────────
    #
    # The load-bearing difference from OpenRouter: GET /v1/models needs a
    # valid Bearer key (401 without one), so the catalog ladder is:
    #
    #   no key          → seed catalog immediately (no network call)
    #   key + fresh L1  → in-process cache (1h TTL)
    #   key + fresh L2  → persistent JSON cache (R07.20, cross-process)
    #   key + stale/absent → live fetch → store-on-success (ROB-42)
    #   fetch fails (401/network/shape) → stale cache → seed fallback
    #
    # A 401 on /v1/models is a discovery failure, NOT a startup failure:
    # the narrowed except degrades to stale/seed so `agentkthx models`
    # and the agent's catalog probe never crash on a bad/rotated key.

    # Cache the model list for 1 hour to avoid hitting /v1/models on every
    # agent.run() — the catalog rarely changes within a session, and each
    # fetch burns one request against the free-tier rate limits.
    _MODEL_CACHE_TTL_SECONDS = 3600  # 1 hour

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Live model cache: list[dict] + timestamp. Populated by list_models().
        # MAINT-02 (R07.05): CloudBackend.__init__ already set
        # _context_safe_max_tokens; we only add the model-cache fields here.
        self._model_cache_ts: float = 0.0

    def _fetch_live_models(self) -> list[dict]:
        """Query ``GET /v1/models`` and build the merged live catalog.

        The /v1/models entry schema is UNDOCUMENTED (docs say only
        "returns the callable model ids as JSON") — assume the OpenAI
        ``{"data": [...]}`` envelope. ``data[].id`` is the only IDENTITY
        field. R07.35: context-window fields are read TOLERANTLY when
        present (owner-directed metadata detection — the models table
        showed every model at the 128K fallback because only ``id`` was
        read); see ``_extract_live_context``. Anything else (pricing,
        modality, …) stays unwired until the probe pins the schema (doc
        §16 probe checklist #1).

        Catalog enrichment ladder per entry: live payload context →
        seed ``context_length`` → (omitted). A context field is only
        written when REAL data exists — no default stamping, so the
        CloudBackend family-knowledge layer (``get_model_max_context``
        step 3) decides for unknown ids.
        """
        url = self._get_models_url()
        headers = self._get_auth_headers()
        req = urllib.request.Request(url, headers=headers, method="GET")

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        # id → raw entry dict (kept for the tolerant context extraction;
        # non-dict data items are tolerated junk with no metadata).
        api_entries: dict[str, dict] = {}
        for m in result.get("data", []) or []:
            name = m.get("id") if isinstance(m, dict) else None
            # Only string ids are catalog entries — a non-string id from a
            # malformed payload is skipped (tolerated junk), not a crash.
            if isinstance(name, str) and name:
                api_entries[name] = m if isinstance(m, dict) else {}

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [TokenHarbor] API returned {len(api_entries)} models")

        # Live-discovered first (confirmed callable), then catalog-only
        # ids (seeded variants the live response may omit — e.g. the
        # :free default during a docs-only period).
        seen: set[str] = set()
        models: list[dict] = []

        for name in sorted(api_entries):
            if name in seen:
                continue
            seen.add(name)
            meta = TOKENHARBOR_MODELS.get(name, {})
            live_ctx = self._extract_live_context(api_entries[name])
            models.append(self._catalog_entry(name, meta, live_ctx=live_ctx))

        for catalog_name in sorted(TOKENHARBOR_MODELS.keys()):
            if catalog_name in seen:
                continue
            seen.add(catalog_name)
            meta = TOKENHARBOR_MODELS[catalog_name]
            models.append(self._catalog_entry(catalog_name, meta))

        return models

    #: Live-entry fields that may carry a context window. The /v1/models
    #: schema is undocumented, so the extractor is defensive: known
    #: gateway conventions only (OpenRouter ``context_length`` top-level
    #: and nested under ``top_provider``, generic ``context_size`` /
    #: ``max_context_tokens`` / ``max_input_tokens``). Anything absent or
    #: junk yields None — never a fabricated default.
    _LIVE_CONTEXT_FIELDS: tuple[str, ...] = (
        "context_length",
        "context_size",
        "max_context_tokens",
        "max_input_tokens",
    )

    @staticmethod
    def _extract_live_context(entry: dict) -> int | None:
        """Tolerantly read a context window from a live /v1/models entry.

        R07.35: returns the first positive integer found among the known
        context field conventions (``_LIVE_CONTEXT_FIELDS``, plus the
        OpenRouter-style nested ``top_provider.context_length``); None
        when the entry carries none. Absent ≠ 128K — the omit-and-fall-
        through contract keeps fabricated defaults out of the catalog.
        """
        if not isinstance(entry, dict):
            return None
        for field in TokenHarborBackend._LIVE_CONTEXT_FIELDS:
            value = entry.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        top_provider = entry.get("top_provider")
        if isinstance(top_provider, dict):
            value = top_provider.get("context_length")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        return None

    def _catalog_entry(
        self, name: str, meta: dict, live_ctx: int | None = None
    ) -> dict:
        """Shape one catalog entry (shared by live + fallback builders).

        R07.35: ``details.context_length`` is written ONLY from real
        data — the live payload's context field (``live_ctx``) or the
        seed meta's ``context_length``. When neither exists the key is
        OMITTED (was: stamped with the 128000 CloudBackend default,
        which made every model in ``agentkthx models`` render 125K and
        blocked the family-knowledge layer in
        ``CloudBackend.get_model_max_context``).
        """
        details: dict = {
            "family": meta.get("family", self._catalog_family_name()),
            "backend": self._catalog_backend_name(),
            "free_tier": _is_free_model(name),
            "is_chat_model": True,
            "pricing": meta.get("pricing", {}),
        }
        ctx = live_ctx if isinstance(live_ctx, int) and live_ctx > 0 else None
        if ctx is None:
            seed_ctx = meta.get("context_length")
            if isinstance(seed_ctx, int) and not isinstance(seed_ctx, bool) and seed_ctx > 0:
                ctx = seed_ctx
        if ctx is not None:
            details["context_length"] = ctx
        return {"name": name, "size": 0, "details": details}

    def _apply_free_only(self, models: list[dict]) -> list[dict]:
        """Apply the TOKENHARBOR_FREE_ONLY filter (:free suffix only).

        Applied at RETURN time, never at cache-store time (the
        OPENROUTER_FREE_ONLY precedent): the persistent cache keeps the
        UNFILTERED catalog so toggling the env var takes effect without
        waiting out the cache TTL.
        """
        if TOKENHARBOR_FREE_ONLY:
            kept = [m for m in models if _is_free_model(m["name"])]
            if not kept and models and os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    "  [TokenHarbor] FREE_ONLY filter left 0 models — no "
                    "':free' ids in this catalog view; unset "
                    "TOKENHARBOR_FREE_ONLY to see the full catalog"
                )
            return kept
        return models

    def list_models(self) -> list[dict]:
        """Return the TokenHarbor catalog (seed-first, auth-gated live refresh).

        Cache layers (R07.20): L1 in-process cache (1 hour) + L2
        persistent JSON cache (30-minute TTL, ``AGENTKTHX_MODEL_CACHE_TTL``
        to override) shared across processes. The live fetch runs ONLY
        when a key exists (the endpoint answers 401 without one); a
        failed fetch degrades to the stale cache, then the seed catalog,
        and never raises (agent startup must not depend on the gateway
        being reachable or the key being valid — the first real call
        surfaces 401 with remediation).

        Returns:
            List of ``{"name": ..., "size": 0, "details": {...}}`` dicts
            in the shape expected by the CLI's ``agentkthx models`` command.
        """
        # L1 cache: return if fresh. The cache stores the UNFILTERED
        # catalog — FREE_ONLY applies to the RETURN value at every layer
        # (doc §16: "applied at RETURN time, not cache-store time"), so a
        # mid-session TOKENHARBOR_FREE_ONLY toggle takes effect on the
        # very next call instead of waiting out the TTL.
        now = time.time()
        if (
            self._model_cache is not None
            and (now - self._model_cache_ts) < self._MODEL_CACHE_TTL_SECONDS
        ):
            return list(self._apply_free_only(self._model_cache))

        # L2: persistent JSON cache — a fresh entry replaces the live
        # fetch entirely (models listed by an earlier process are served
        # without touching the API or burning a rate-limited request).
        cached = model_cache.get_cached_models(self.MODEL_CACHE_KEY)
        if cached is not None:
            self._model_cache = list(cached)
            self._model_cache_ts = now
            return list(self._apply_free_only(self._model_cache))

        # First JSON-cache contact: seed the static defaults (catalog
        # entries; stale-stamped so the keyed live fetch below still runs).
        model_cache.ensure_seeded(self.MODEL_CACHE_KEY, self._catalog_fallback_list())

        # Auth gate (doc §16 invariant #6): the endpoint REQUIRES auth —
        # no key means no live refresh, period. Serve the seed catalog
        # (cold start = seed catalog; live refresh only when a key
        # exists). Defensive: CloudBackend.__init__ enforces a non-empty
        # key, but test harnesses and future construction paths may not.
        if not self._api_key:
            models = self._catalog_fallback_list()
            self._model_cache = models
            self._model_cache_ts = now
            return list(self._apply_free_only(models))

        # Live fetch — ROB-42 (R07.28): narrow the except to the
        # legitimate discovery-failure modes (the R07.24 ROB-28
        # catch-narrowing, Mistral pattern). HTTPError covers the 401
        # (missing/invalid/rotated key) — a discovery failure that
        # degrades to stale/seed, never a startup crash. Programming
        # errors from a malformed response shape (KeyError/AttributeError/
        # TypeError) propagate as real bugs.
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
        ) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [TokenHarbor] live /v1/models fetch failed "
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

        # Store UNFILTERED in L1; filter the RETURN value only.
        self._model_cache = list(models)
        self._model_cache_ts = now

        return list(self._apply_free_only(models))

    def _catalog_fallback_list(self) -> list[dict]:
        """Shape the static (seed) catalog into ``list_models()`` entries.

        Used when there is no key (cold start), the live /v1/models
        endpoint is unreachable AND the persistent JSON cache is
        empty/missing. The seed carries only the ids Token Harbor's own
        docs name (docs-only, perishable — see the "_tokenharbor_note"
        metadata key in the seed JSON).

        R07.35: shares ``_catalog_entry`` with the live builder —
        ``details.context_length`` only when the seed meta actually has
        one (currently none do), never the 128000 default stamp.
        """
        models: list[dict] = []
        for name in sorted(TOKENHARBOR_MODELS.keys()):
            models.append(self._catalog_entry(name, TOKENHARBOR_MODELS[name]))
        return models

    # ─────────────────────────────────────────────────────────────────────
    # MAINT-28 (R07.28): provider-specific retry-loop hooks
    # ─────────────────────────────────────────────────────────────────────
    #
    # The retry-loop skeleton (envelope parsing, quota fast-fail, 400
    # recovery, retryable backoff, remediation raise) lives on
    # CloudBackend — ``_make_api_request`` and ``_iter_sse_lines`` are
    # the shared drivers; this backend supplies only the
    # TokenHarbor-specific pieces:
    #
    #   - the 401/403 remediation texts (doc §11 verbatim),
    #   - the spend-gate 400 (delivered via the
    #     ``_raise_non_retryable_status`` override — see below for why
    #     the 400 entry is NOT consulted for every 400),
    #   - the 429 classifier that separates the per-key USD spend cap
    #     (429 + spend_limit_exceeded, quota-class) from the free-tier
    #     rate limit (plain 429, rate-class retry).
    #
    # No fixed-param 400 hook and no ``_tweak_request_body`` quirk is
    # needed: Token Harbor forwards the OpenAI body byte-for-byte (doc
    # §4), and X-TH-Cache-Control stays unused by default (doc §16
    # invariant #5 — documented escape hatch, not a default).

    _STATUS_REMEDIATIONS: dict[int, str] = {
        # 400 carries the spend-gate text (doc §10/§11 verbatim), but it
        # is delivered selectively: _raise_non_retryable_status raises it
        # ONLY when the body names spend_confirmation_required. Every
        # other 400 (upstream tools rejection, param validation, the
        # verbatim context_length_exceeded forward) keeps the GENERIC
        # message so the ReAct fallback detector and the context-length
        # recovery keep working.
        400: (
            "spend_confirmation_required — the estimate for this single call "
            "exceeds half your Token Harbor balance. Confirm deliberately "
            "(body th_confirm_spend=true or header x-th-confirm-spend=true), "
            "lower max_tokens, or top up. AgentKthx never auto-confirms."
        ),
        401: (
            "Key rejected. Verify TOKENHARBOR_API_KEY (thk_live_…, pasted in "
            "full). If every integration on the account started failing at "
            "once, the account-wide Rotate was used — create a new key at "
            "tokenharbor.ai/dashboard/api-keys."
        ),
        403: (
            "vendor_not_allowed — this key's vendor allow/block list excludes "
            "the model's maker. Use a model from an allowed maker or another "
            "key (the dashboard has no UI to edit the lists — documented)."
        ),
    }

    def _raise_non_retryable_status(
        self,
        exc: Exception,
        status_code: int,
        body_text: str,
        err_msg: str,
        body: dict,
    ) -> None:
        """Route the spend-gate 400; keep other 400s generic (MAINT-28).

        The doc's planned ``_STATUS_REMEDIATIONS[400]`` text is the
        spend-gate remediation, but a blanket 400 table entry would
        mislabel EVERY non-retryable 400 as a spend gate — a tools
        rejection ("does not support tools") must keep its upstream
        message so the ReAct fallback in generate() can detect it, and
        the verbatim ``context_length_exceeded`` forward belongs to the
        context-length recovery story. So:

          400 + spend_confirmation_required in body → the §10/§11
              remediation text, raised FATAL (no retry, no
              th_confirm_spend injection — doc §10 invariant).
          400 + anything else → the generic "{brand} API error 400:
              {err_msg}" raise.
          everything else → the inherited table/generic flow (401/403).

        The spend-gate code string is matched against the raw body
        (not just the parsed envelope message) because the documented
        signal is the CODE string ``spend_confirmation_required`` and
        the envelope shape is undocumented.
        """
        if status_code == 400 and "spend_confirmation_required" in body_text:
            raise RuntimeError(self._STATUS_REMEDIATIONS[400].format(err_msg=err_msg)) from exc
        if status_code == 400:
            # Non-spend 400 — the generic message carries the upstream
            # text (envelope-parsed) for the ReAct detector / user.
            raise RuntimeError(f"{self._error_brand} API error {status_code}: {err_msg}") from exc
        super()._raise_non_retryable_status(exc, status_code, body_text, err_msg, body)

    def _looks_like_quota_exhaustion(self, status_code: int, body_text: str) -> bool:
        """TokenHarbor quota hook: spend cap or balance exhaustion.

        Doc §11 mapping (MAINT-28 hook contract):

          429 + ``spend_limit_exceeded`` (the documented per-key USD
            spend-cap code, rolling daily/weekly/monthly window) →
            quota-class. It only lifts when spend ages out of the
            window — potentially hours — so fast-fail with the
            window-aging message instead of burning the retry budget.

          402/429 + balance-exhaustion phrasing → quota-class hedge.
            The $0-balance-on-paid-model status code is UNDOCUMENTED
            (doc §11 expects 400- or 402-class with a balance message;
            probe checklist #3). Matching the wording now is the
            honest hedge — the "Details:" line carries the upstream
            text either way.

          plain 429 (free-account 60/min + 1,800/hr rate limit) →
            rate-class: returns False so the shared loop retries with
            Retry-After backoff.

        Order matters (the Cloudflare "limit" drift lesson, MAINT-28):
        the documented exact code string is checked FIRST — it contains
        the substring "limit" and must never be vetoed by the
        transient-wording check below it.
        """
        if status_code not in (402, 429):
            return False
        if not body_text:
            return False
        body_lower = body_text.lower()
        # 1. Documented per-key spend-cap code — always quota-class.
        #    (Checked before the transient veto: "spend_limit_exceeded"
        #    contains "limit".)
        if "spend_limit_exceeded" in body_lower:
            return True
        # 2. Transient rate-limit wording vetoes the quota classification
        #    (a "rate limit"/"TPM" message is the free-tier rate limit —
        #    retryable with Retry-After).
        if "rate limit" in body_lower or "rate-limit" in body_lower or "tpm" in body_lower:
            return False
        # 3. Balance-exhaustion phrasing (undocumented shape — hedge;
        #    verify at probe time per doc §16 checklist #3).
        balance_indicators = ("balance", "insufficient", "top up", "top-up")
        return any(ind in body_lower for ind in balance_indicators)

    def _quota_exhaustion_message(self) -> str:
        """Spend-cap / balance remediation text (doc §11 verbatim)."""
        return (
            "Token Harbor key spend cap reached — the rolling USD window must "
            "age out, or raise the cap / switch keys at "
            "tokenharbor.ai/dashboard/api-keys."
        )

    # ─────────────────────────────────────────────────────────────────────
    # generate — non-streaming entry point (mirrors SiliconFlow + OpenRouter)
    # ─────────────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response using Token Harbor's Chat Completions API.

        Implements the OpenAI Chat Completions spec via the shared
        CloudBackend transport, with the doc-pinned pass-through
        guarantees: system messages and the messages array are forwarded
        byte-for-byte (NO DDG/SD-style system suppression — the ReAct/
        native tool scaffolding must reach the model, doc §4), no
        attribution headers, and no cache-control headers by default.

        Fallback behavior:
        - HTTP 400 "does not support tools" (an upstream rejection —
          Token Harbor is upstream-agnostic) → retried ONCE without the
          ``tools`` field so the model can emit text-format (ReAct) tool
          calls that the Agent's ToolParser handles.

        The spend gate is NOT handled here beyond surfacing: a 400
        ``spend_confirmation_required`` raises the remediation text
        fatally (see _raise_non_retryable_status) — AgentKthx never
        auto-confirms (doc §10 invariant #1).

        Args:
            model: TokenHarbor model id (opaque string from the catalog)
            messages: Chat messages in OpenAI format
            tools: Optional list of Tool objects for native function calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Optional OpenAI params — top_p, stop, seed, n,
                presence_penalty, frequency_penalty, response_format,
                tool_choice, reasoning_effort, etc. (forwarded by the
                shared _build_openai_body)

        Returns:
            Dict with keys: content, tool_calls, finish_reason, usage,
            reasoning_content, raw.
        """
        # JEV dispatch — if api_mode is JEV, route through
        # generate_decision() which wraps the underlying LLM call with
        # a constrained decision prompt.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=max_tokens if max_tokens is not None else 8192,
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

        # Build the OpenAI-spec request body via the shared builder —
        # system messages pass through untouched (doc §4 pass-through
        # guarantee; invariant #3: never strip or fold system messages).
        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            **kwargs,
        )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [TokenHarbor] POST chat/completions — "
                f"model={model}, tools={len(tools) if tools else 0}, "
                f"tool_choice={kwargs.get('tool_choice', 'auto')}"
            )

        try:
            return self._make_api_request(body, stream=False)
        except RuntimeError as e:
            err_str = str(e)
            # ReAct fallback: an upstream that rejects the `tools` field
            # (HTTP 400 with a "does not support tools" message). Retry
            # ONCE without it so text-format tool calls can still flow
            # through the Agent's ToolParser. The spend-gate 400 never
            # matches this detector (its message names
            # spend_confirmation_required) and stays fatal.
            if tools and _is_tools_not_supported_error(err_str):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        "  [TokenHarbor] Model doesn't support tools — "
                        "retrying without tools (ReAct fallback)"
                    )
                body.pop("tools", None)
                body.pop("tool_choice", None)
                return self._make_api_request(body, stream=False)
            raise

    # ─────────────────────────────────────────────────────────────────────
    # Usage-field dual-family parsing (doc §4 unverified-shape hedge)
    # ─────────────────────────────────────────────────────────────────────
    #
    # The /v1/chat/completions usage field names are undocumented
    # (probe checklist #2). Token Harbor fronts Anthropic-class models
    # (its own docs describe prompt-cache accounting with
    # cache_creation_input_tokens / cache_read_input_tokens — Anthropic
    # field names), so the response may carry EITHER the OpenAI family
    # (prompt_tokens / completion_tokens) OR the Anthropic family
    # (input_tokens / output_tokens). This override normalizes both
    # into the OpenAI-shaped usage dict the house parser emits, and
    # surfaces the cache-token fields when present (ignored when
    # absent). The streaming path's usage handling is inherited.

    @staticmethod
    def _parse_openai_response(raw_response: dict) -> dict:
        """Parse the OpenAI-shape response with TH usage normalization.

        Normalizes the Anthropic-family usage names into the OpenAI
        family BEFORE delegating to the inherited parser, then copies
        the prompt-cache token fields onto the parsed usage dict. The
        raw response is mutated in place (usage is a nested dict of the
        caller's raw object — same ownership convention as the other
        normalization sites).
        """
        usage = raw_response.get("usage")
        if isinstance(usage, dict):
            # Anthropic family → OpenAI family (only when the OpenAI
            # names are absent — never overwrite a real field).
            if "prompt_tokens" not in usage and "input_tokens" in usage:
                usage["prompt_tokens"] = usage.get("input_tokens") or 0
            if "completion_tokens" not in usage and "output_tokens" in usage:
                usage["completion_tokens"] = usage.get("output_tokens") or 0
            if "total_tokens" not in usage:
                prompt = usage.get("prompt_tokens")
                completion = usage.get("completion_tokens")
                if isinstance(prompt, int) and isinstance(completion, int):
                    usage["total_tokens"] = prompt + completion

        result = OpenAICompatibleBackend._parse_openai_response(raw_response)

        # Surface the prompt-cache accounting fields when present
        # (Token Harbor's auto-marked prompt cache: 1.25x write /
        # 0.1x read — Claude-family models). Ignored when absent.
        if isinstance(usage, dict):
            for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                if usage.get(field) is not None:
                    result["usage"][field] = usage[field]

        return result
