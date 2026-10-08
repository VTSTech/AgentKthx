"""
⚛️ AgentKthx — Cloud Backend Base (MAINT-02, R07.05)

Consolidates the structurally-duplicated logic from the 5 cloud backend
plugins (``zai``, ``openrouter``, ``gemini``, ``openai``, ``huggingface``)
into a single shared base class.

Each of those plugins previously implemented its own version of:

  - ``__init__`` resolving ``base_url`` from arg/env/default, validating
    the API key is present + non-trivial, setting
    ``_context_safe_max_tokens = None`` for the R06.57 context-length-400
    recovery, and forcing ``api_mode`` to ``OPENAI`` or ``JEV``.
  - ``is_running()`` — for a cloud service, this is "do we have an API
    key configured?" rather than "is the local server alive?".
  - ``_get_model_defaults(model)`` — catalog lookup (``temperature``,
    ``max_tokens``, ``context_length``) feeding the shared
    ``_apply_max_tokens_cap`` helper from ``OpenAICompatibleBackend``.
  - ``_get_auth_headers()`` — the common ``{"Authorization": "Bearer X"}``
    form (with ``Content-Type: application/json``).
  - ``_iter_sse_lines`` error handling for the 400 context-length case —
    already consolidated into ``_handle_context_length_400`` in R06.57.
  - ``test_tool_support()`` — cloud models with native function calling
    return ``NATIVE`` (with provider-specific exceptions like Gemini 1.5).

This class is a *thin* consolidation: per-plugin code stays in the
plugin modules (catalogs, auth quirks, free-tier logic, JEV dispatch).
``CloudBackend`` only owns the shape that every cloud backend shares.

The 5 cloud plugins inherit from ``OpenAICompatibleBackend`` directly
today. After MAINT-02 they should inherit from ``CloudBackend``, which
itself inherits from ``OpenAICompatibleBackend`` — so existing code paths
that ``isinstance(b, OpenAICompatibleBackend)`` continue to work.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
from typing import Generator

from ..core.types import ApiMode, BackendType, ToolSupportLevel
from .base import BackendConfig
from .openai_compat import OpenAICompatibleBackend


class WireAdapter:
    """Protocol for non-OpenAI cloud wire-format adapters.

    ARCH-06 closure (R07.13): documents the seam where non-OpenAI
    clouds (e.g. Anthropic Messages API) plug in a custom wire format
    without forking :class:`OpenAICompatibleBackend`. The default
    OpenAI shape (used by ZAI, OpenRouter, HuggingFace, Pollinations,
    Mistral) needs no adapter — they set ``_wire_adapter = None`` and
    inherit the OpenAI body builder / response parser / SSE iterator
    unchanged.

    A future Anthropic Messages API backend would subclass
    :class:`CloudBackend`, set ``_wire_adapter = AnthropicWireAdapter()``,
    and override the three hook methods below to translate between
    AgentKthx's internal OpenAI-shape messages and Anthropic's native
    ``system`` + ``messages`` split + ``content_block_*`` SSE events.

    This class is a protocol — concrete adapters subclass it and
    override the methods they need. Methods that return ``None``
    mean "fall back to the inherited OpenAI-shape implementation".
    """

    def build_request_body(self, messages: list, **kwargs) -> dict | None:
        """Translate AgentKthx messages → provider-native request body.

        Return ``None`` to use the inherited OpenAI-shape body builder
        (``_build_openai_body``). Override to translate to a non-OpenAI
        wire shape (e.g. Anthropic's ``system`` + ``messages`` split).

        Args:
            messages: AgentKthx-internal message list (OpenAI shape).
            **kwargs: Generation parameters (temperature, max_tokens, etc.)

        Returns:
            Provider-native request body dict, or ``None`` to fall back
            to the OpenAI-shape builder.
        """
        return None

    def parse_response(self, raw_response: dict) -> dict | None:
        """Translate provider-native response → AgentKthx-internal shape.

        Return ``None`` to use the inherited OpenAI-shape parser
        (``_parse_openai_response``). Override to translate from a
        non-OpenAI response shape (e.g. Anthropic's ``content_block_*``
        structure) back to AgentKthx's internal ``{content, tool_calls,
        finish_reason, usage}`` shape.

        Args:
            raw_response: The raw JSON response dict from the provider.

        Returns:
            AgentKthx-internal response dict, or ``None`` to fall back
            to the OpenAI-shape parser.
        """
        return None

    def iter_sse_events(self, response, url: str, body: dict, headers: dict):
        """Iterate provider-native SSE events → AgentKthx-internal chunks.

        Return ``None`` to use the inherited OpenAI-shape SSE iterator
        (``_iter_sse_lines``). Override to translate non-OpenAI SSE
        event shapes (e.g. Anthropic's ``content_block_delta`` events)
        into AgentKthx's internal chunk shape ``{"delta": str}`` /
        ``{"content": str}`` / ``{"finish_reason": str}``.

        Args:
            response: The urllib response object.
            url: The request URL.
            body: The request body dict.
            headers: The request headers dict.

        Yields:
            AgentKthx-internal chunk dicts. Return ``None`` (without
            yielding) to fall back to the OpenAI-shape iterator.
        """
        return None


class CloudBackend(OpenAICompatibleBackend):
    """Shared base class for cloud-hosted OpenAI-compatible backends.

    MAINT-02 (R07.05): consolidates the common cloud-backend patterns
    from the 5 cloud plugins. Concrete plugins override:

      Required overrides (data + provider identity):
        - ``MODELS: dict`` — static catalog of supported models with
          ``context_length``, ``default_max_tokens``,
          ``default_temperature``, ``pricing`` fields.
        - ``backend_type`` — return the appropriate ``BackendType`` enum
          value (e.g. ``BackendType.ZAI``).
        - ``_get_chat_completions_url()`` — provider-specific URL.
        - ``_api_key_env_var`` — env var name (e.g. ``"ZAI_API_KEY"``).
        - ``_default_base_url`` — fallback URL when no env/arg is set.
        - ``_default_model`` — model used when none specified.
        - ``_provider_label`` — short label for debug messages
          (e.g. ``"ZAI"``, ``"OpenRouter"``).

      Optional overrides (behavior):
        - ``_validate_api_key(key)`` — extra validation beyond
          "non-empty and ``_MIN_API_KEY_LEN``+ chars" (default: no extra validation).
        - ``_extra_auth_headers()`` — additional auth headers
          (e.g. OpenRouter's ``HTTP-Referer`` and ``X-Title``).
        - ``list_models()`` — if the provider has a discovery endpoint,
          override the catalog-only default.
        - ``_iter_sse_lines()`` — if the provider has special 429/400
          handling (e.g. ZAI's insufficient-credits fallback).
        - ``test_tool_support()`` — if the provider has known tool-call
          gaps (e.g. Gemini 1.5 Flash).

      Optional override (wire format — ARCH-06 closure R07.13):
        - ``_wire_adapter`` — set to a :class:`WireAdapter` instance to
          customize the request body builder + response parser. Default
          ``None`` means "use the inherited OpenAI Chat Completions wire
          shape" (the historical behavior). A non-OpenAI cloud (e.g.
          Anthropic Messages API) would set this to a custom adapter
          that translates ``messages`` → Anthropic's ``messages`` +
          ``system`` split, and parses Anthropic's ``content_block_*``
          SSE events back into the AgentKthx response shape. The
          adapter protocol is documented on :class:`WireAdapter` below.

    This base implements:
      - ``__init__`` — resolves base_url, validates API key, sets
        ``_context_safe_max_tokens = None``, forces ``OPENAI``/``JEV``.
      - ``is_running()`` — True iff an API key is configured.
      - ``_get_auth_headers()`` — ``Bearer`` + ``Content-Type`` + extras.
      - ``_get_model_defaults(model)`` — catalog lookup + cap.
      - ``get_model_info(model)`` — catalog lookup.
      - ``test_tool_support()`` — returns ``NATIVE`` by default.

    R07.28 (MAINT-28 batch 2) additionally implements the full shared
    HTTP transport so backends scaffolded from the Mistral pattern no
    longer re-copy it:
      - ``_make_api_request(body, *, stream)`` — non-streaming POST with
        the MAINT-28 retry skeleton (``_handle_http_error_for_retry`` /
        ``_handle_url_error_for_retry``) + a ``_tweak_request_body``
        hook fired once per request before the first send.
      - ``_iter_sse_lines(url, body, headers)`` — streaming POST with
        the same skeleton, thinking-model timeout bump, and the ROB-06
        deterministic ``_close_http_response`` teardown.
      - ``generate_stream(...)`` — the thin text-delta wrapper over
        ``generate_completions_stream`` (ARCH-01 parity).
      - ``_jev_call_completions(...)`` — JEV decision call routed
        through ``_make_api_request`` (no tools, ``response_format``
        passthrough).
      - ``_catalog_model_key(model)`` — the catalog-lookup normalization
        hook that replaces the "does NOT strip the provider prefix"
        override family (get_model_info / _get_model_defaults /
        get_model_max_context / _is_free_model).

    ARCH-06 (R07.13): the OpenAI wire-shape coupling is now explicit
    rather than implicit. ``CloudBackend`` still inherits from
    ``OpenAICompatibleBackend`` (preserving all existing behavior), but
    the ``_wire_adapter`` attribute documents the seam where non-OpenAI
    clouds would plug in a custom wire format. The 4 existing cloud
    backends (ZAI, OpenRouter, HuggingFace, Pollinations, Mistral) all
    use the default OpenAI shape — they don't set ``_wire_adapter``.
    A future Anthropic Messages API backend would set it and override
    ``_build_openai_body`` / ``_parse_openai_response`` / ``_iter_sse_lines``
    via the adapter, without needing to fork the entire
    ``OpenAICompatibleBackend`` class.
    """

    # ARCH-06: Wire format adapter. None = use inherited OpenAI shape.
    # Set to a WireAdapter instance in subclasses for non-OpenAI clouds.
    _wire_adapter: "WireAdapter | None" = None

    # ─────────────────────────────────────────────────────────────────────
    # Class attributes — concrete backends MUST override these
    # ─────────────────────────────────────────────────────────────────────

    MODELS: dict[str, dict] = {}
    """Static catalog of supported models. Each entry has at minimum:
    ``context_length``, ``default_max_tokens``, ``default_temperature``,
    ``pricing``. Concrete backends define this as a class attribute."""

    _api_key_env_var: str = ""
    """Name of the env var that holds the API key (e.g. ``ZAI_API_KEY``)."""

    _default_base_url: str = ""
    """Fallback base URL when no env var or explicit arg is set."""

    _default_model: str = ""
    """Model used when none is specified."""

    _provider_label: str = "Cloud"
    """Short label for debug messages (e.g. ``"ZAI"``)."""

    # ROB-21 (R07.07): minimum API key length enforced by ``__init__``.
    # 8 chars (the prior default) only catches the most egregious typos;
    # real cloud API keys are 30+ chars (OpenAI ``sk-...`` is 51 chars,
    # ZAI is similar). 20 is a conservative floor that catches obvious
    # mistakes without breaking legitimate test setups. Subclasses can
    # override (e.g. ``_MIN_API_KEY_LEN = 4`` for a dev sandbox).
    _MIN_API_KEY_LEN: int = 20

    # MAINT-12 (R07.07): default context-length fallback used when neither
    # the static catalog nor the live model cache has an entry. Was a
    # hardcoded ``128000`` literal repeated at 4 sites in this file; now a
    # single class attribute so backends with smaller models (e.g. a
    # hypothetical cloud serving Llama-2-7B at 4K context) can override.
    _DEFAULT_CONTEXT_FALLBACK: int = 128000

    # ─────────────────────────────────────────────────────────────────────
    # __init__ — shared cloud-backend initialization
    # ─────────────────────────────────────────────────────────────────────

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str | None = None,
        api_key: str | None = None,
    ):
        """Initialize a cloud backend.

        Args:
            base_url: Override the default API base URL. If None, uses
                ``self._default_base_url``.
            host, port: Alternative URL form — constructs
                ``https://{host}:{port}``. Used by some test setups.
            config: ``BackendConfig`` for timeout/max_retries.
            api_mode: ``OPENAI`` (default for cloud) or ``JEV``.
                ``OPENRE`` is rejected (cloud backends don't expose the
                native ``/api/chat`` endpoint).
            api_key: API key. If None, falls back to the env var named
                by ``self._api_key_env_var``, then to the config module
                singleton (which itself reads the env var).
        """
        # Resolve base URL — priority: explicit arg > host/port > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"https://{host}:{port}"
        else:
            resolved_url = self._default_base_url.rstrip("/")

        # Cloud backends only support OPENAI / JEV. Reject OPENRE.
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode is None:
            forced_mode = ApiMode.OPENAI
        elif api_mode == ApiMode.JEV:
            forced_mode = ApiMode.JEV  # accepted — generate_decision() handles the wrapper
        elif api_mode == ApiMode.OPENAI:
            forced_mode = ApiMode.OPENAI
        else:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  [{self._provider_label}] API mode '{api_mode}' not supported — "
                    f"cloud backends only support OpenAI / JEV, forcing OPENAI"
                )
            forced_mode = ApiMode.OPENAI

        # Call parent (OpenAICompatibleBackend → BaseBackend) with resolved values.
        super().__init__(
            base_url=resolved_url,
            config=config,
            api_mode=forced_mode,
        )

        # SEC-15 (R07.08): previously this unconditionally overwrote
        # os.environ["AGENTKTHX_API_MODE"] = forced_mode.value, which meant
        # the LAST CloudBackend instance to be constructed won — creating a
        # second backend with a different api_mode silently changed the
        # first backend's debug-output behavior (process-global side effect).
        # Fix: only set the env var if it's not already set (first-instance-
        # wins). The env var is read by _should_show_openresponses_debug()
        # in core/openresponses.py to gate OpenResponses debug output. A
        # proper fix would pass api_mode through the Response objects, but
        # that's a larger refactor (ARCH-01/ARCH-06 territory).
        if "AGENTKTHX_API_MODE" not in os.environ:
            os.environ["AGENTKTHX_API_MODE"] = forced_mode.value

        # API key — priority: explicit > env var > config module singleton
        env_value = os.environ.get(self._api_key_env_var, "")
        self._api_key = api_key or env_value

        # Validate presence (subclasses can extend via _validate_api_key)
        if not self._api_key or not self._api_key.strip():
            raise ValueError(
                f"{self._api_key_env_var} is required for the {self._provider_label} backend. "
                f"Run `agentkthx auth` to set it interactively (persists to "
                f"~/.agentkthx/.env), or export {self._api_key_env_var} in "
                f"your shell."
            )
        if len(self._api_key.strip()) < self._MIN_API_KEY_LEN:
            raise ValueError(
                f"{self._api_key_env_var} appears invalid (too short: "
                f"{len(self._api_key.strip())} chars, need at least "
                f"{self._MIN_API_KEY_LEN}). Check your "
                f"{self._api_key_env_var} environment variable."
            )

        # Hook for provider-specific key validation (override in subclass)
        self._validate_api_key(self._api_key)

        # R06.57: Persisted safe max_tokens after a context-length 400.
        # Set by _iter_sse_lines() when a streaming call 400s with
        # "context length" in the error message. Honored by
        # _get_model_defaults() so future agentic-loop steps don't
        # re-trigger the same 400.
        self._context_safe_max_tokens: int | None = None

    # ─────────────────────────────────────────────────────────────────────
    # Provider identity hooks (override in subclass)
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        """Subclasses MUST override to return their specific BackendType."""
        raise NotImplementedError(f"{self.__class__.__name__} must override backend_type")

    @property
    def api_key(self) -> str:
        """Return the API key."""
        return self._api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        """Set the API key (writes through to ``_api_key``).

        R07.21: the ``/auth`` picker's ``_patch_live_backend`` calls
        ``setattr(backend, "api_key", new_value)`` to patch a key onto the
        running session's backend. Pre-R07.21 this raised
        ``AttributeError: property 'api_key' of 'ZaiBackend' object has no
        setter`` because the property was read-only. The setter writes
        through to ``_api_key`` so every consumer (header builders, the
        ``test_tool_support`` probe, etc.) sees the new value on the next
        call. Same pattern as ``OpenAICompatibleBackend.base_url``.
        """
        self._api_key = value

    @property
    def base_url(self) -> str:
        """Return the cloud API base URL."""
        return self._base_url

    def _validate_api_key(self, key: str) -> None:
        """Hook for provider-specific API key validation.

        Default: no-op. Override to enforce provider-specific key format
        (e.g. OpenAI's ``sk-`` prefix, HuggingFace's ``hf_`` prefix).
        Raise ``ValueError`` on invalid keys.
        """
        return

    def _extra_auth_headers(self) -> dict:
        """Hook for additional auth headers beyond the standard Bearer token.

        Default: empty dict. Override to add provider-specific headers
        (e.g. OpenRouter's ``HTTP-Referer`` and ``X-Title``). Backends
        that use plain Bearer auth (NVIDIA NIM, Cloudflare Workers AI,
        ZAI, Mistral, Pollinations) inherit this unchanged — R07.28
        deleted the docstring-only ``return {}`` copies.
        """
        return {}

    def _catalog_model_key(self, model: str) -> str:
        """Normalize a caller-supplied model name to its catalog key.

        MAINT-28 batch 2 (R07.28): replaces the 4-method "does NOT strip
        the provider prefix" override family (``get_model_info`` /
        ``_get_model_defaults`` / ``get_model_max_context`` /
        ``_is_free_model``) that nvidia.py + cloudflare.py each carried.

        Default: strip the provider prefix — ``"zai/glm-4-flash"`` →
        ``"glm-4-flash"`` — because ZAI/OpenRouter-style catalogs key on
        the bare post-slash segment.

        Override to return the name unchanged when the provider's API
        requires the FULL prefixed ID in the request body and the seed
        catalog keys on that full name (NVIDIA ``meta/llama-3.3-70b-``
        ``instruct``, Cloudflare ``@cf/meta/llama-3.3-70b-instruct-fp8-``
        ``fast`` — the ``@cf/`` prefix is part of the model ID, not a
        slashable provider marker).
        """
        return model.split("/")[-1] if "/" in model else model

    def _tweak_request_body(self, body: dict) -> None:
        """Per-backend request-body adjustments before the first send.

        MAINT-28 batch 2 (R07.28): single choke point fired by
        ``_make_api_request`` (so both the ``generate()`` and JEV paths
        get it). Default: no-op. Override for provider quirks — e.g.
        Cloudflare pops ``top_k`` (not supported on its OpenAI-compat
        path; the endpoint 400s if it sees it).

        Mutates ``body`` in place. Called ONCE per request (before the
        retry loop), not per attempt — param-fixing retries that need
        per-attempt mutation belong in ``_handle_fixed_param_400``.
        """
        del body
        return

    # ─────────────────────────────────────────────────────────────────────
    # R07.24 (MAINT-23/ROB-29): shared HTTP retry helpers
    # ─────────────────────────────────────────────────────────────────────
    #
    # The retry-loop skeleton was previously copy-pasted between Mistral's
    # _iter_sse_lines (streaming) and _make_api_request (non-streaming),
    # and again in Pollinations, OpenRouter, and OrcaRouter (the latter
    # two having their own slight variants). This block lifts the truly
    # shared pieces — Retry-After parsing + backoff calculation + 429/5xx
    # retryable classification — so concrete backends can call the helpers
    # instead of inlining the same ~30 LOC four times.
    #
    # R07.24 deliberately left the 4xx-specific handlers (401/404/422 +
    # 400-context-length recovery) in each backend's caller because they
    # differed in error-message wording and recovery strategy. MAINT-28
    # (R07.28) reversed that for the post-R07.24 backends after nvidia.py
    # + cloudflare.py grew four near-identical copies (~100 LOC × 4) of
    # exactly that skeleton: the block below now carries the shared
    # _handle_http_error_for_retry / _handle_url_error_for_retry template
    # with per-backend hooks (remediation table, quota classifier,
    # fixed-param 400). The older hand-rolled loops (Mistral,
    # Pollinations, OpenRouter, OrcaRouter, ...) keep their inline
    # variants — migrating them is optional follow-up, not required.

    # Class-level backoff defaults — concrete backends override these
    # to tune their own retry behavior. ROB-16 (R07.07): the cap matters;
    # an uncapped ``Retry-After: 3600`` once hung a sibling backend for
    # an hour. _BACKOFF_CAP clamps both the parsed Retry-After AND the
    # computed exponential-backoff value.
    _BACKOFF_BASE: float = 1.0
    _BACKOFF_CAP: float = 60.0
    _MAX_RETRIES: int = 4

    def _max_retries(self) -> int:
        """Resolve the retry budget — env override > class default.

        Concrete backends can override to read their own env var
        (e.g. Mistral reads ``MISTRAL_MAX_RETRIES``). The default
        implementation reads ``AGENTKTHX_MAX_API_RETRIES`` (the
        cross-backend override the agent loop's resilience layer
        also reads); falls back to ``_MAX_RETRIES`` class attribute.
        """
        raw = os.environ.get("AGENTKTHX_MAX_API_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_RETRIES

    def _compute_retry_after(self, headers, attempt: int) -> float:
        """Parse Retry-After header or compute exponential backoff with jitter.

        Honors ``Retry-After`` when present (429/503), capped at
        ``_BACKOFF_CAP`` per the ROB-16 lesson. Falls back to
        exponential backoff with full jitter (mirrors the official
        cloud SDK recipe): ``base = _BACKOFF_BASE * 2**attempt``, clamped
        to ``_BACKOFF_CAP``, plus 0–20% jitter on top to avoid
        thundering-herd retries. The result is always ≥1s and ≤_BACKOFF_CAP.

        Args:
            headers: The HTTPError's ``.headers`` object (or None if
                not available — e.g. a URLError has no headers).
            attempt: Zero-indexed attempt number for the backoff
                calculation (attempt 0 → ~1s base, attempt 1 → ~2s, etc.).

        Returns:
            Seconds to sleep before the next retry.
        """
        retry_after_raw = ""
        if headers is not None:
            try:
                retry_after_raw = headers.get("Retry-After", "") or ""
            except Exception:
                retry_after_raw = ""

        if retry_after_raw:
            try:
                ra = float(retry_after_raw)
                # ROB-16: cap honored Retry-After — never sleep more than
                # _BACKOFF_CAP, even if the server says "Retry-After: 3600".
                return min(max(ra, 1.0), self._BACKOFF_CAP)
            except (ValueError, TypeError):
                pass  # malformed Retry-After — fall through to backoff

        base = self._BACKOFF_BASE * (2**attempt)
        backoff = min(base, self._BACKOFF_CAP)
        # Full jitter: add 0–20% on top of the computed base. This
        # de-correlates concurrent retries from the same client (e.g.
        # multi-agent orchestrator retrying 4 backends in parallel after
        # a network blip) so they don't all hammer the same server at
        # the same instant.
        backoff += random.uniform(0, backoff * 0.2)
        return min(max(backoff, 1.0), self._BACKOFF_CAP)

    def _is_retryable_http_status(self, status_code: int) -> bool:
        """429 (rate limit) + 5xx (server errors) are retryable.

        Per the convention established across Mistral, OpenRouter,
        Pollinations, and OrcaRouter: 429 is always retryable (rate
        limit, may clear), 500/502/503/504 are retryable (transient
        server-side), 4xx (except 429) are not retryable (client error
        — the request is malformed or unauthorized, retrying won't
        help). 400-with-context-length-recovery is a special case
        handled separately by ``_handle_context_length_400``.
        """
        return status_code == 429 or status_code in (500, 502, 503, 504)

    def _compute_network_backoff(self, attempt: int) -> float:
        """Compute exponential backoff for URLError (network-level) retries.

        Distinct from ``_compute_retry_after`` because URLErrors carry
        no headers — there's no Retry-After to honor. Just the
        exponential-backoff-with-jitter path. Same clamping rules.

        Args:
            attempt: Zero-indexed attempt number.

        Returns:
            Seconds to sleep before the next retry.
        """
        base = self._BACKOFF_BASE * (2**attempt)
        backoff = min(base, self._BACKOFF_CAP)
        backoff += random.uniform(0, backoff * 0.2)
        return min(max(backoff, 1.0), self._BACKOFF_CAP)

    # ─────────────────────────────────────────────────────────────────────
    # MAINT-28 (R07.28): shared retry-loop skeleton
    # ─────────────────────────────────────────────────────────────────────
    #
    # R07.26/R07.27 scaffolded nvidia.py + cloudflare.py from the Mistral
    # pattern AFTER the R07.24 split, and each copy pasted the remaining
    # skeleton between their own two methods AND between the two files:
    # OpenAI-spec envelope parsing (~15 LOC), the quota-exhaustion 429
    # fast-fail (~15 LOC), the 400 recovery handlers (~10 LOC), the
    # retryable-check + user-facing print block (~12 LOC), and the
    # 401/404/422 remediation texts (~30 LOC) — roughly 100 LOC × 4
    # copies, differing only in the "[NVIDIA]" / "[NVIDIA-Stream]" /
    # "[Cloudflare]" / "[Cloudflare-Stream]" log prefixes. The drift that
    # duplication invites had ALREADY fired once inside R07.27's own
    # development: cloudflare's _make_api_request docstring listed
    # "limit" among the quota indicators while
    # _looks_like_neuron_quota_exhaustion deliberately excludes it (the
    # function was fixed, the docstring copy wasn't).
    #
    # This block collapses the shared skeleton into CloudBackend template
    # methods (the MAINT-23 precedent, taken one level up). Subclasses
    # supply the genuinely provider-specific pieces via hooks:
    #
    #   _error_brand                 — brand for "{brand} API error {code}" strings
    #   _STATUS_REMEDIATIONS         — per-status remediation text table
    #   _looks_like_quota_exhaustion — quota-429 classifier hook
    #   _quota_exhaustion_message    — quota-429 user-facing remediation text
    #   _handle_fixed_param_400      — fixed-param 400 recovery hook (NVIDIA)
    #   _raise_non_retryable_status  — override for side-effectful statuses
    #                                  (Cloudflare's 403/5035 paid-plan cache write)
    #
    # ROB-43 (R07.28 batch 3): the ``attempt == 0`` gate that used to sit
    # on the quota branch is GONE — a quota-exhaustion 429 now fast-fails
    # at ANY attempt. The gate was borrowed from the 400-recovery handlers
    # (where it prevents an infinite loop on a persistent 400 and is still
    # in place), but the quota branch RAISES, so it can never loop — the
    # gate only delayed the clear quota message until the retry budget
    # burned down. Removal pinned by tests/test_r07_28_batch3_quick_wins.py
    # (the batch-1 pin test was flipped in the same diff).

    #: Brand used in the generic error strings ("{brand} API error 429:
    #: ...", "{brand} connection error: ..."). Subclasses override
    #: ("NVIDIA NIM", "Cloudflare", ...).
    _error_brand: str = "cloud backend"

    #: Per-status remediation texts for non-retryable HTTP errors
    #: (MAINT-28). Keys are status codes (401/404/422 ...); values are
    #: str.format templates where ``{err_msg}`` is substituted with the
    #: parsed error-envelope message. Previously duplicated ×4 across
    #: nvidia.py + cloudflare.py. Subclasses with side-effectful statuses
    #: (Cloudflare's 403/5035) override ``_raise_non_retryable_status``
    #: instead of adding them here.
    _STATUS_REMEDIATIONS: dict[int, str] = {}

    @staticmethod
    def _parse_error_envelope(body_text: str, status_code: int) -> str:
        """Extract a human-readable message from an OpenAI-spec error body.

        MAINT-28 (R07.28): was copy-pasted into all four retry loops of
        nvidia.py + cloudflare.py (and inline-again in each streaming
        copy). Handles the three shapes seen in the wild:

          - ``{"error": {"message": ...}}`` — the OpenAI spec envelope
          - ``{"error": "<string>"}``        — some gateways
          - ``{"message": ...}``             — plain envelope

        Falls back to the first 500 characters of the raw body (or
        ``"HTTP <code>"`` when the body is empty). Malformed JSON is not
        an error — the fallback text is returned, exactly like the
        pre-dedup copies.
        """
        fallback = body_text[:500] or f"HTTP {status_code}"
        if not body_text:
            return fallback
        try:
            err_data = json.loads(body_text)
        except (json.JSONDecodeError, ValueError):
            return fallback
        if not isinstance(err_data, dict):
            return fallback
        if "error" in err_data:
            inner = err_data["error"]
            if isinstance(inner, dict):
                return inner.get("message", str(inner))
            return str(inner)
        if "message" in err_data:
            return err_data["message"]
        return fallback

    def _looks_like_quota_exhaustion(self, status_code: int, body_text: str) -> bool:
        """Backend quota-exhaustion classifier hook (MAINT-28).

        Distinguishes a fatal quota 429 (NVIDIA monthly credits,
        Cloudflare daily neurons — NOT retryable) from a transient
        rate-limit 429 (retryable). Default: never matches (a backend
        without a quota model treats every 429 as retryable).
        """
        del status_code, body_text
        return False

    def _quota_exhaustion_message(self) -> str:
        """User-facing remediation text for a quota-exhausted 429 (MAINT-28).

        The full message ("...quota exhausted. Resets ...") whose whole
        point is telling the user when the quota resets. ``Details:
        {err_msg}`` is appended by ``_check_quota_429``.
        """
        return f"{self._error_brand} quota exhausted."

    def _handle_fixed_param_400(self, body_text: str, body: dict, log_prefix: str) -> bool:
        """Fixed-param 400 recovery hook (MAINT-28). Default: no recovery.

        Some providers pin certain parameters per model (e.g. NVIDIA NIM's
        kimi-k3 fixes ``top_p=0.95``) and reject the request with a 400
        naming the fixed value. Override to detect the error, mutate
        ``body``, and return True so the caller retries. The
        ``attempt == 0`` gate lives in ``_handle_http_error_for_retry``
        (a persistent 400 must not loop forever).
        """
        del body_text, body, log_prefix
        return False

    def _check_quota_429(
        self,
        status_code: int,
        body_text: str,
        attempt: int,
        err_msg: str,
        exc: Exception,
    ) -> None:
        """Fast-fail a quota-exhaustion 429 (MAINT-28 shared skeleton).

        Raises ``RuntimeError`` with the backend's
        ``_quota_exhaustion_message()`` when the body matches the
        backend's ``_looks_like_quota_exhaustion()`` classifier; returns
        otherwise so the retry loop classifies the 429 normally
        (transient rate limits retry with backoff).

        ROB-43 (R07.28 batch 3): the former ``attempt == 0`` gate is
        removed — a quota 429 arriving after ≥1 transient retry now
        raises the clear quota message IMMEDIATELY instead of burning
        the remaining retry budget against a non-retryable condition.
        ``attempt`` stays in the signature (callers pass it; subclass
        overrides may still consult it), but the base gate no longer
        uses it.
        """
        del attempt  # ROB-43: the attempt == 0 gate is gone (R07.28 batch 3)
        if status_code == 429 and self._looks_like_quota_exhaustion(status_code, body_text):
            raise RuntimeError(f"{self._quota_exhaustion_message()} Details: {err_msg}") from exc

    def _raise_non_retryable_status(
        self,
        exc: Exception,
        status_code: int,
        body_text: str,
        err_msg: str,
        body: dict,
    ) -> None:
        """Raise the user-facing error for a non-retryable status (MAINT-28).

        Consults ``_STATUS_REMEDIATIONS`` first (the 401/404/422 texts
        that were duplicated ×4), then falls back to the generic
        ``"{brand} API error {code}: {err_msg}"``. Subclasses with
        side-effectful statuses override and call ``super()`` on misses
        — e.g. Cloudflare's 403 handler writes the paid-only verdict to
        the tool_cache before raising (code 5035 = paid-plan-only model
        vs token-permission 403 need different user actions).

        Always raises; the return is unreachable but keeps the signature
        honest for callers.
        """
        del body_text, body
        template = self._STATUS_REMEDIATIONS.get(status_code)
        if template is not None:
            raise RuntimeError(template.format(err_msg=err_msg)) from exc
        raise RuntimeError(f"{self._error_brand} API error {status_code}: {err_msg}") from exc

    def _handle_http_error_for_retry(
        self,
        e: urllib.error.HTTPError,
        body: dict,
        attempt: int,
        max_retries: int,
        log_prefix: str,
    ) -> str:
        """Shared HTTPError classifier for the cloud retry loops (MAINT-28).

        Consolidates the skeleton that was duplicated ×4 across
        nvidia.py + cloudflare.py (``_make_api_request`` /
        ``_iter_sse_lines`` each): envelope parsing, quota-exhaustion
        429 fast-fail, fixed-param + context-length 400 recovery,
        retryable-status backoff (R07.24 helpers), and the
        401/404/422 remediation texts. Callers shrink to their
        genuinely provider-specific logic.

        Flow (identical to the pre-dedup loops; the streaming copies
        now also parse the envelope BEFORE the quota check, so the
        quota "Details:" line carries the parsed message instead of a
        raw body slice):

          1. Read the error body + parse the OpenAI-spec envelope
          2. Quota-exhaustion 429 → raise immediately (never retryable,
             at ANY attempt — ROB-43 closed R07.28 batch 3)
          3. Fixed-param 400 (backend hook) → maybe retry
          4. Context-length 400 (ARCH-03 shared handler) → maybe retry
          5. Retryable status (R07.24 helpers) → sleep, return "retry"
          6. Non-retryable → ``_raise_non_retryable_status``

        Args:
            e: The caught ``urllib.error.HTTPError``.
            body: The request body dict (400-recovery handlers mutate it).
            attempt: Zero-indexed attempt number.
            max_retries: Total retry budget (from ``_max_retries()``).
            log_prefix: Backend log tag — "[NVIDIA]", "[Cloudflare-Stream]", ...

        Returns:
            "retry" — the backoff sleep is already done; the caller just
            continues its loop. Any non-retryable outcome raises instead.
        """
        status_code = e.code
        body_bytes = e.read() if e.fp else b""
        body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""

        # OpenAI-spec error envelope → human-readable message.
        err_msg = self._parse_error_envelope(body_text, status_code)

        # Quota-exhaustion 429 — NOT retryable. Surface the backend's
        # remediation message immediately instead of burning retries.
        self._check_quota_429(status_code, body_text, attempt, err_msg, e)

        # Fixed-param 400 (backend hook, e.g. kimi-k3's fixed top_p) —
        # first attempt only so a persistent 400 can't loop forever.
        if (
            status_code == 400
            and attempt == 0
            and self._handle_fixed_param_400(body_text, body, log_prefix)
        ):
            return "retry"

        # ARCH-03: shared context-length 400 handler. Only on the first
        # attempt (don't loop forever on a 400).
        if status_code == 400 and attempt == 0:
            old_max = body.get("max_tokens", 4096)
            if self._handle_context_length_400(body_text, body):
                new_max = body["max_tokens"]
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  {log_prefix} Context length exceeded — "
                        f"reducing max_tokens {old_max} → {new_max} and retrying"
                    )
                return "retry"

        # Retryable: 429 (rate limit, NOT quota exhaustion) + 5xx
        # (transient server errors). R07.24 (MAINT-23/ROB-29): delegate
        # to the shared _is_retryable_http_status / _compute_retry_after
        # helpers.
        if self._is_retryable_http_status(status_code) and attempt < max_retries:
            retry_after = self._compute_retry_after(e.headers, attempt)
            if os.environ.get("AGENTKTHX_DEBUG") or attempt < 2:
                print(
                    f"  {log_prefix} {status_code} — {err_msg}. "
                    f"Retrying in {retry_after:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries + 1})..."
                )
            time.sleep(retry_after)
            return "retry"

        # Non-retryable OR exhausted retries → remediation table /
        # backend override / generic raise.
        self._raise_non_retryable_status(e, status_code, body_text, err_msg, body)

    def _handle_url_error_for_retry(
        self,
        e: urllib.error.URLError,
        attempt: int,
        max_retries: int,
        log_prefix: str,
    ) -> None:
        """Shared URLError classifier for the cloud retry loops (MAINT-28).

        Network-level errors (DNS failure, connection refused, socket
        timeout) retry with the R07.24 ``_compute_network_backoff``
        helper (no Retry-After header to honor), then surface as
        ``"{brand} connection error"`` when the budget is exhausted.
        Was duplicated ×4 across nvidia.py + cloudflare.py.
        """
        if attempt < max_retries:
            backoff = self._compute_network_backoff(attempt)
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(
                    f"  {log_prefix} connection error ({e.reason}), " f"retrying in {backoff:.0f}s"
                )
            time.sleep(backoff)
            return
        raise RuntimeError(f"{self._error_brand} connection error: {e.reason}") from e

    # ─────────────────────────────────────────────────────────────────────
    # MAINT-28 batch 2 (R07.28): shared HTTP transport
    # ─────────────────────────────────────────────────────────────────────
    #
    # Batch 1 extracted the ERROR-CLASSIFICATION half of the retry loops
    # (``_handle_http_error_for_retry`` / ``_handle_url_error_for_retry``)
    # but deliberately left the request loops themselves per-backend.
    # The post-lift nvidia.py + cloudflare.py loops turned out to be
    # line-for-line identical — the only per-backend residue was the
    # ``[NVIDIA]`` / ``[Cloudflare]`` log prefix (already available as
    # ``_provider_label``) and the brand in the "retries exhausted"
    # RuntimeError (already available as ``_error_brand``). This block
    # moves the loops themselves onto CloudBackend:
    #
    #   _make_api_request      — non-streaming POST + retry loop
    #   _iter_sse_lines        — streaming POST + retry loop (+ ROB-06
    #                            deterministic close, thinking-model
    #                            300s timeout bump)
    #   generate_stream        — thin text-delta wrapper (ARCH-01)
    #   _jev_call_completions  — JEV decision call via _make_api_request
    #   _tweak_request_body    — per-backend body quirks (single choke
    #                            point, fired once per request)
    #
    # Backends with genuinely different request paths (Mistral's
    # ``_parse_mistral_response``, Pollinations' card pipeline, ZAI's
    # own loop, OrcaRouter's endpoint_types filtering, and the four
    # OpenAICompatibleBackend-direct siblings) keep their overrides —
    # migrating them is the MAINT-31 follow-up, not this pass.

    def _make_api_request(self, body: dict, *, stream: bool = False) -> dict:
        """POST to ``/chat/completions`` with the shared 429/5xx retry loop.

        R07.28 (MAINT-28 batch 2): the shared implementation. The loop
        fires ``_tweak_request_body`` once (per-backend body quirks),
        then per attempt:

          1. ``urlopen`` → ``_parse_openai_response`` on HTTP 200
          2. ``HTTPError`` → ``_handle_http_error_for_retry`` (the
             MAINT-28 shared classifier: envelope parse → quota-429
             fast-fail → fixed-param 400 hook → context-length 400 →
             retryable backoff → remediation raise). The quota
             classifier + remediation texts live on the backend hooks —
             they are the single source of truth for what a fatal 429
             looks like (Cloudflare deliberately does NOT treat "limit" as a
             quota indicator).
          3. ``URLError`` → ``_handle_url_error_for_retry`` (network
             backoff, then ``"{brand} connection error"``).

        Honors ``Retry-After`` when present; falls back to exponential
        backoff with full jitter (R07.24 helpers). Retry budget from
        ``_max_retries()`` (default 4, ``AGENTKTHX_MAX_API_RETRIES``
        override). Log prefix is ``[{self._provider_label}]``.

        Returns:
            The parsed response dict (``_parse_openai_response`` shape).

        Raises:
            RuntimeError: on non-retryable statuses (via the remediation
                table / backend override) or when retries are exhausted
                (``"{brand} retries exhausted"`` — unreachable in
                practice because the classifier raises first).
        """
        self._tweak_request_body(body)

        url = self._get_chat_completions_url()
        headers = self._get_auth_headers()
        if stream:
            headers["Accept"] = "text/event-stream"

        max_retries = self._max_retries()
        log_prefix = f"[{self._provider_label}]"

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
                # Shared MAINT-28 classifier — sleeps + returns "retry"
                # for a retryable status, raises the remediation
                # RuntimeError otherwise. Either way the loop advances
                # only via this handler's decision.
                self._handle_http_error_for_retry(e, body, attempt, max_retries, log_prefix)

            except urllib.error.URLError as e:
                # Network-level error — retry with backoff, then surface.
                self._handle_url_error_for_retry(e, attempt, max_retries, log_prefix)

        # Should not reach here — every iteration returns, retries
        # (loop continues), or raises.
        raise RuntimeError(f"{self._error_brand} retries exhausted")

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST and yield raw SSE line bytes.

        R07.28 (MAINT-28 batch 2): the shared streaming implementation.
        Same shared classifier as ``_make_api_request`` via
        ``_handle_http_error_for_retry`` / ``_handle_url_error_for_retry``
        (with the ``[{label}-Stream]`` prefix), plus two transport
        details every streaming backend shared:

          - Thinking models can take 60-90+ seconds before the first
            token — the urlopen timeout bumps to 300s when
            ``_name_matches_thinking`` matches the body's model (vs the
            configured timeout otherwise). The chat.py spinner covers
            the user-facing progress gap.
          - ROB-06 (R07.25 CLOSED): the response is closed
            deterministically via ``_close_http_response`` in a
            ``finally`` so Windows doesn't leak the TCP connection when
            the generator is abandoned mid-iteration (Ctrl+C, consumer
            exception, or the base class's break on [DONE]).

        Yields:
            Raw SSE line bytes for ``generate_completions_stream`` to
            parse (JSON parsing, [DONE] detection, delta/tool_call
            extraction all live there).

        Raises:
            RuntimeError: non-retryable statuses or exhausted retries
                (``"{brand}-Stream retries exhausted"``).
        """
        max_retries = self._max_retries()
        log_prefix = f"[{self._provider_label}-Stream]"

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
                # Shared MAINT-28 classifier — sleeps + returns "retry"
                # for a retryable status, raises the remediation
                # RuntimeError otherwise. The continue only runs on the
                # "retry" return; a raise skips it. (Batch-2 fix: the
                # per-backend copies this loop replaces fell through to
                # ``for line in response`` with ``response`` unbound — an
                # UnboundLocalError masking the retry — because they
                # omitted the continue; pinned by
                # test_streaming_uses_log_prefix_via_provider_label.)
                self._handle_http_error_for_retry(e, body, attempt, max_retries, log_prefix)
                continue
            except urllib.error.URLError as e:
                # Network-level error — retry with backoff, then surface.
                self._handle_url_error_for_retry(e, attempt, max_retries, log_prefix)
                continue

            # Success — yield raw SSE line bytes. The base class's
            # generate_completions_stream() handles the JSON parsing,
            # [DONE] detection, and delta/tool_call extraction.
            try:
                for line in response:
                    yield line
            finally:
                self._close_http_response(response)
            return  # success — don't retry

        # Should not reach here — every iteration yields + returns,
        # retries (loop continues), or raises.
        raise RuntimeError(f"{self._error_brand}-Stream retries exhausted")

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text as text deltas (shared thin wrapper).

        R07.28 (MAINT-28 batch 2): ARCH-01 parity wrapper — delegates to
        the inherited ``generate_completions_stream`` and yields just the
        text ``delta`` strings. The agent loop calls
        ``generate_completions_stream`` directly when it needs the full
        dict shape (delta + tool_calls + finish_reason +
        reasoning_content). The HTTP transport + retry/recovery lives in
        ``_iter_sse_lines``.
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
        """JEV decision call routed through the shared request path.

        R07.28 (MAINT-28 batch 2): shared implementation for
        OpenAI-compat cloud backends — builds the body with
        ``_build_openai_body`` (no tools — decisions never call tools,
        ``response_format`` passthrough for constrained decision
        prompts), applies ``_tweak_request_body`` via
        ``_make_api_request``, and returns the normalized response
        (``{content, tool_calls, usage, latency_ms, raw}``).

        The ``think`` parameter is accepted (signature parity with
        ``generate_decision``'s dispatch) but not forwarded — thinking
        is controlled via ``reasoning_effort`` in ``**kwargs`` where the
        provider supports it.
        """
        del think
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

    # ─────────────────────────────────────────────────────────────────────
    # HTTP response cleanup (ROB-06 R07.25)
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _close_http_response(response) -> None:
        """Deterministically close an urllib HTTP response.

        ROB-06 (R07.25 CLOSED): on Windows, ``urllib.request.urlopen``
        returns an ``http.client.HTTPResponse`` whose ``.close()`` may
        not immediately close the underlying TCP connection — it
        relies on GC. On long sessions with many Ctrl+C interrupts,
        this can exhaust the connection pool. The fix: explicitly close
        the underlying ``fp`` (the buffered reader) AND release the
        connection (``release_conn`` on keep-alive-aware responses),
        catching ``AttributeError`` for older Python versions where
        these attributes don't exist.

        Best-effort: any error is swallowed. The caller's
        KeyboardInterrupt handler is in a try/except already; we don't
        want to add a second raise here that would propagate to the
        caller's except block and mask the cancellation.

        Args:
            response: an ``http.client.HTTPResponse`` (or any object
                with a ``.close()`` method + optionally ``.fp`` +
                ``release_conn``). Pass None to no-op.
        """
        if response is None:
            return
        # 1. flush + close the buffered reader (fp). On Windows this
        # is what actually releases the socket buffer; without it the
        # underlying TCP connection stays in CLOSE_WAIT until GC.
        try:
            fp = getattr(response, "fp", None)
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    pass
        except Exception:
            pass
        # 2. release the connection (keep-alive-aware responses). The
        # attribute is only present on http.client.HTTPResponse; older
        # Python versions or alternate response objects may not have it.
        try:
            release = getattr(response, "release_conn", None)
            if callable(release):
                release()
        except Exception:
            pass
        # 3. finally, close the response itself (idempotent — close()
        # is documented safe to call multiple times).
        try:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Shared implementations
    # ─────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Cloud service is "running" iff an API key is configured.

        Unlike local backends (Ollama, LlamaServer, BitNet), cloud
        backends don't need a health-check probe — if the user provided
        an API key, the service is available from our perspective.
        Actual availability is discovered on the first request.
        """
        return bool(self._api_key)

    def _get_auth_headers(self) -> dict:
        """Standard Bearer-token auth header for cloud backends.

        Combines ``Content-Type: application/json`` + ``Authorization:
        Bearer <key>`` + any provider-specific extras from
        ``_extra_auth_headers()``.
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # Merge provider-specific extras (OpenRouter adds HTTP-Referer + X-Title)
        headers.update(self._extra_auth_headers())
        return headers

    def get_model_info(self, model: str) -> dict | None:
        """Look up model in the static catalog.

        Normalizes the model name via ``_catalog_model_key`` (default:
        strip provider prefix, e.g. ``zai/glm-4-flash`` → ``glm-4-flash``;
        NVIDIA/Cloudflare return the full prefixed ID unchanged).
        Returns ``None`` if not found.
        """
        model_key = self._catalog_model_key(model)
        meta = self.MODELS.get(model_key, {})

        if not meta:
            return None

        return {
            "name": model_key,
            "size": 0,
            "details": {
                "family": self._catalog_family_name(),
                "backend": self._catalog_backend_name(),
                "context_length": meta.get("context_length", self._DEFAULT_CONTEXT_FALLBACK),
                # free_tier from catalog pricing so /models labels match
                # the catalog (default False = paid when pricing unknown).
                "free_tier": self._is_free_model(model_key),
            },
        }

    def _catalog_family_name(self) -> str:
        """Family name for catalog entries. Override for non-default."""
        return self._provider_label.lower()

    def _catalog_backend_name(self) -> str:
        """Backend identifier for catalog entries. Override for non-default."""
        return self._provider_label.lower()

    def _get_model_defaults(self, model: str) -> dict:
        """Return ``{temperature, max_tokens}`` from the static catalog.

        Uses ``MODELS[model].default_max_tokens`` (capped to
        ``context_length // 32`` by the inherited
        ``_apply_max_tokens_cap``) and ``default_temperature``. If the
        model is not in the catalog, falls back to safe defaults
        (``max_tokens=8192``, ``context_length=128000``, ``temperature=0.7``).

        ARCH-03 (R06.57): the cap + persisted-safe-value logic is
        inherited from ``OpenAICompatibleBackend._apply_max_tokens_cap``.
        """
        model_key = self._catalog_model_key(model)
        meta = self.MODELS.get(model_key, {})

        max_tokens = meta.get("default_max_tokens", 8192)
        context_length = meta.get("context_length", self._DEFAULT_CONTEXT_FALLBACK)
        temperature = meta.get("default_temperature", 0.7)

        if os.environ.get("AGENTKTHX_DEBUG"):
            capped = min(max_tokens, context_length // 32)
            if capped < max_tokens:
                print(
                    f"  [{self._provider_label} Debug] Capped max_tokens "
                    f"{max_tokens} -> {capped} (context={context_length}, divisor=32)"
                )

        return self._apply_max_tokens_cap(max_tokens, context_length, temperature=temperature)

    # ─────────────────────────────────────────────────────────────────────
    # Context-window reporting (R07.05 — fixes ``agentkthx models`` crash)
    # ─────────────────────────────────────────────────────────────────────
    #
    # The ``agentkthx models`` CLI command (agentkthx/cli/commands/models.py:138-139)
    # calls ``backend.get_model_runtime_context(name)`` and
    # ``backend.get_model_max_context(name, family=family)`` on every model
    # in the list. ``OpenAICompatibleBackend.get_model_runtime_context``
    # delegates to ``self.get_model_max_context(model)``, but that method
    # was only defined on ``OllamaBackend`` (which uses Ollama's ``/api/show``
    # endpoint). Cloud backends (ZAI, OpenRouter, OpenAI, HuggingFace,
    # OrcaRouter) crashed with ``AttributeError: ... has no attribute
    # 'get_model_max_context'`` when ``agentkthx models --backend <cloud>``
    # was invoked.
    #
    # The fix: ``CloudBackend`` provides a catalog-based implementation.
    # For cloud backends, the "runtime" context equals the model's max
    # trained context (no separate runtime context like Ollama's
    # Modelfile ``num_ctx``). The catalog lookup falls back to 128K
    # (a safe default for modern cloud chat models).

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Return the model's maximum trained context window size.

        For cloud backends, this is the ``context_length`` field from the
        static ``MODELS`` catalog. The ``family`` argument is ignored for
        cloud backends (the catalog is authoritative per-model, not
        per-family). Falls back to 128000 (128K) if the model is not in
        the catalog — a safe default for modern cloud chat models.

        Cloud backends don't have Ollama's ``/api/show`` endpoint, so we
        can't probe the model's actual context window at runtime. The
        catalog is the source of truth.

        Args:
            model: Model name (provider prefix stripped automatically).
            family: Ignored for cloud backends (catalog is per-model).

        Returns:
            Maximum context window size in tokens (default: 128000).
        """
        # Try the static catalog first (CloudBackend.MODELS)
        model_key = self._catalog_model_key(model)
        meta = self.MODELS.get(model_key, {})
        if meta:
            ctx = meta.get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                return ctx

        # Try the live model cache (populated by list_models() for
        # backends like OrcaRouter that query /v1/models at runtime)
        info = self.get_model_info(model)
        if info and "details" in info:
            ctx = info["details"].get("context_length")
            if ctx and isinstance(ctx, int) and ctx > 0:
                return ctx

        # Safe fallback — 128K is the minimum for modern cloud chat models
        # (GPT-4o-mini, Claude Haiku, Gemini Flash, GLM-4-Flash all support
        # at least 128K). Older models that support less will trigger the
        # _handle_context_length_400 recovery on first request.
        # MAINT-12 (R07.07): use class attribute so subclasses can override.
        return self._DEFAULT_CONTEXT_FALLBACK

    def get_model_runtime_context(self, model: str) -> int:
        """Return the runtime context window size for a model.

        For cloud backends, the runtime context equals the max context —
        there's no separate "runtime" context like Ollama's Modelfile
        ``num_ctx`` (which can be set below the model's max for memory
        savings). Cloud backends always use the model's full context.

        This override replaces the broken default on
        ``OpenAICompatibleBackend`` (which called
        ``self.get_model_max_context(model)`` — the method we define
        just above — but only ``OllamaBackend`` had previously defined
        it, so cloud backends crashed with ``AttributeError``).
        """
        return self.get_model_max_context(model)

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Cloud models default to NATIVE tool support.

        Most cloud providers (ZAI, OpenRouter, OpenAI, HuggingFace)
        support OpenAI-compatible function calling natively. Gemini has
        known gaps for some 1.5 Flash variants — override in the
        Gemini backend if needed.

        Args:
            model: Model identifier.
            family: Optional model family hint (unused in default impl).
            force_test: If True, perform a live test instead of returning
                the static default. The default implementation ignores
                this (always returns NATIVE) — override to implement
                live testing.

        Returns:
            ``ToolSupportLevel.NATIVE`` by default.
        """
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # list_models — default catalog-only implementation
    # ─────────────────────────────────────────────────────────────────────

    def list_models(self) -> list[dict]:
        """Return the static catalog as a list of model dicts.

        Default implementation: enumerate ``self.MODELS`` and shape each
        entry as ``{"name": ..., "size": 0, "details": {...}}``.

        Override in subclasses that have a discovery endpoint (e.g.
        ``ZaiBackend`` queries ``/api/paas/v4/models`` and merges with
        the catalog; ``OpenRouterBackend`` queries
        ``/api/v1/models`` and caches the result).
        """
        models = []
        for name in sorted(self.MODELS.keys()):
            meta = self.MODELS[name]
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
                        # free_tier from catalog pricing so /models free works
                        # for catalog-driven backends too.
                        "free_tier": self._is_free_model(name),
                    },
                }
            )
        return models

    # ─────────────────────────────────────────────────────────────────────
    # FREE_ONLY filtering hook (override in subclasses that have free tiers)
    # ─────────────────────────────────────────────────────────────────────

    def _is_free_model(self, model: str) -> bool:
        """Return True if ``model`` is free (zero pricing) per the catalog.

        Default: looks up the model in ``self.MODELS`` and checks
        ``pricing.input == 0 and pricing.output == 0``. Override in
        subclasses with provider-specific free-tier logic (e.g. Gemini
        uses a separate ``FREE_TIER_LIMITS`` table; OpenAI uses a
        hard-coded whitelist).
        """
        model_key = self._catalog_model_key(model)
        meta = self.MODELS.get(model_key)
        if not meta:
            return False
        pricing = meta.get("pricing", {})
        return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0
