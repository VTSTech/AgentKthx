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

import os
from typing import Any

from .openai_compat import OpenAICompatibleBackend
from .base import BackendConfig
from ..core.types import ApiMode, BackendType, ToolSupportLevel
from ..core.models import Tool


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
          "non-empty and 8+ chars" (default: no extra validation).
        - ``_extra_auth_headers()`` — additional auth headers
          (e.g. OpenRouter's ``HTTP-Referer`` and ``X-Title``).
        - ``list_models()`` — if the provider has a discovery endpoint,
          override the catalog-only default.
        - ``_iter_sse_lines()`` — if the provider has special 429/400
          handling (e.g. ZAI's insufficient-credits fallback).
        - ``test_tool_support()`` — if the provider has known tool-call
          gaps (e.g. Gemini 1.5 Flash).

    This base implements:
      - ``__init__`` — resolves base_url, validates API key, sets
        ``_context_safe_max_tokens = None``, forces ``OPENAI``/``JEV``.
      - ``is_running()`` — True iff an API key is configured.
      - ``_get_auth_headers()`` — ``Bearer`` + ``Content-Type`` + extras.
      - ``_get_model_defaults(model)`` — catalog lookup + cap.
      - ``get_model_info(model)`` — catalog lookup.
      - ``test_tool_support()`` — returns ``NATIVE`` by default.
    """

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

        # ARCH-01: persist api_mode so is_openresponses_mode() in core/openresponses.py
        # works correctly. This mirrors what each cloud plugin used to do inline.
        os.environ["AGENTKTHX_API_MODE"] = forced_mode.value

        # API key — priority: explicit > env var > config module singleton
        env_value = os.environ.get(self._api_key_env_var, "")
        self._api_key = api_key or env_value

        # Validate presence (subclasses can extend via _validate_api_key)
        if not self._api_key or not self._api_key.strip():
            raise ValueError(
                f"{self._api_key_env_var} is required for the {self._provider_label} backend. "
                f"Set it via --api-key, {self._api_key_env_var} env var, or Config."
            )
        if len(self._api_key.strip()) < 8:
            raise ValueError(
                f"{self._api_key_env_var} appears invalid (too short: "
                f"{len(self._api_key.strip())} chars). Check your "
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
        raise NotImplementedError(
            f"{self.__class__.__name__} must override backend_type"
        )

    @property
    def api_key(self) -> str:
        """Return the API key."""
        return self._api_key

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
        (e.g. OpenRouter's ``HTTP-Referer`` and ``X-Title``).
        """
        return {}

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

        Strips provider prefix (e.g. ``zai/glm-4-flash`` → ``glm-4-flash``)
        before catalog lookup. Returns ``None`` if not found.
        """
        # Normalize: strip provider prefix if present (e.g., "zai/glm-4-plus")
        model_key = model.split("/")[-1] if "/" in model else model
        meta = self.MODELS.get(model_key, {})

        if not meta:
            return None

        return {
            "name": model_key,
            "size": 0,
            "details": {
                "family": self._catalog_family_name(),
                "backend": self._catalog_backend_name(),
                "context_length": meta.get("context_length", 128000),
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
        model_key = model.split("/")[-1] if "/" in model else model
        meta = self.MODELS.get(model_key, {})

        max_tokens = meta.get("default_max_tokens", 8192)
        context_length = meta.get("context_length", 128000)
        temperature = meta.get("default_temperature", 0.7)

        if os.environ.get("AGENTKTHX_DEBUG"):
            capped = min(max_tokens, context_length // 32)
            if capped < max_tokens:
                print(
                    f"  [{self._provider_label} Debug] Capped max_tokens "
                    f"{max_tokens} -> {capped} (context={context_length}, divisor=32)"
                )

        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=temperature
        )

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
        model_key = model.split("/")[-1] if "/" in model else model
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
        return 128000

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
            models.append({
                "name": name,
                "size": 0,
                "details": {
                    "family": self._catalog_family_name(),
                    "backend": self._catalog_backend_name(),
                    "context_length": meta.get("context_length", 128000),
                    # free_tier from catalog pricing so /models free works
                    # for catalog-driven backends too.
                    "free_tier": self._is_free_model(name),
                },
            })
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
        model_key = model.split("/")[-1] if "/" in model else model
        meta = self.MODELS.get(model_key)
        if not meta:
            return False
        pricing = meta.get("pricing", {})
        return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0
