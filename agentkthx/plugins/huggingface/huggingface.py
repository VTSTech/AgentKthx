"""
⚛️ AgentKthx — Hugging Face Inference Router API Backend

Backend implementation for the Hugging Face Inference Router
(OpenAI Chat-Completions compatible).

Hugging Face's Inference Router (https://router.huggingface.co/v1) is a
unified proxy that exposes 100+ open-weight models (Llama, Qwen,
DeepSeek, Mistral, Gemma, GLM, Phi, Command-R, gpt-oss) served by ~18
partner providers (Together, Groq, Novita, DeepInfra, Fireworks,
Cerebras, Replicate, Fal AI, etc.) through a single OpenAI-compatible
chat completions endpoint.

Provider routing is controlled by a suffix on the model id:
    "openai/gpt-oss-120b"            — router default (:fastest)
    "openai/gpt-oss-120b:cheapest"   — lowest price per output token
    "openai/gpt-oss-120b:preferred"  — user's HF settings preference
    "openai/gpt-oss-120b:groq"       — pin to a specific partner provider

This backend inherits the OpenAI Chat-Completions logic from
OpenAICompatibleBackend and adds:
  - HF_TOKEN Bearer authentication (with HUGGING_FACE_HUB_TOKEN fallback)
  - HF_FREE_ONLY enforcement: model whitelist + :cheapest auto-suffix +
    HTTP 402 hard-fail (vs. fallback when HF_FREE_ONLY=false)
  - HF_FREE_FALLBACK_MODEL: swap model on HTTP 402 (free-tier exhausted)
  - HF_PROVIDER_POLICY: env-var-driven default routing suffix
  - 429 retry loop with Retry-After honor + exponential back-off
  - Reasoning-content capture for thinking-capable HF models
    (Qwen3-Thinking, DeepSeek-R1, prism-ml/Ternary-Bonsai-27B-gguf-reasoning)
  - ReAct fallback when a partner provider rejects the `tools` field
    (some providers serve the same model id from different underlying
    deployments — tool support may vary)

Endpoints used:
  - POST /chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /models           → model discovery (OpenAI-compatible, returns
                              per-provider pricing and supported_parameters)

Configuration:
  HF_TOKEN                — Hugging Face access token (required for inference)
  HF_BASE_URL             — Router base URL
                            (default: https://router.huggingface.co/v1)
  HF_DEFAULT_MODEL        — Default model id
                            (default: openai/gpt-oss-120b)
  HF_FREE_ONLY            — Strict free-tier enforcement (default: false)
  HF_FREE_FALLBACK_MODEL  — Model to swap to on HTTP 402 when
                            HF_FREE_ONLY=false
                            (default: prism-ml/Ternary-Bonsai-27B-gguf)
  HF_PROVIDER_POLICY      — Auto-suffix for routing (default: "")
                            One of: "", "fastest", "cheapest",
                            "preferred", or a partner name like "groq".

Usage:
  # CLI
  agentkthx chat --backend huggingface --model openai/gpt-oss-120b
  agentkthx run "What is 15 * 8?" --backend hf --model Qwen/Qwen3-4B-Thinking-2507

  # Python API
  from agentkthx import Agent
  agent = Agent(
      model="deepseek-ai/DeepSeek-R1",
      backend="huggingface",
      tools=["calculator"],
  )
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
from typing import Generator, Iterable

from agentkthx.backends.base import BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.config import (
    HF_BASE_URL,
    HF_DEFAULT_MODEL,
    HF_FREE_FALLBACK_MODEL,
    HF_FREE_ONLY,
    HF_PROVIDER_POLICY,
    HF_TOKEN,
)
from agentkthx.core.models import Tool
from agentkthx.core.types import ApiMode, BackendType, ToolSupportLevel


# ─────────────────────────────────────────────────────────────────────────────
# Static model catalog — fallback when /v1/models is unreachable
# ─────────────────────────────────────────────────────────────────────────────
# Curated set of models historically served by HF partner providers
# with free or near-free routing. The /v1/models endpoint is the source
# of truth for live context_length, max_completion_tokens, and pricing;
# this catalog is a fallback when the API is unreachable.
#
# Updated: 2026-09-26 (matches HUGGINGFACE_API_TECHNICAL_REFERENCE.md §Free
# model whitelist)
HF_MODELS: dict[str, dict] = {
    # OpenAI open-weighted models (free at HF partner providers)
    "prism-ml/Ternary-Bonsai-27B-gguf": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "openai",
        "description": "gpt-oss-20b — open-weight conversational model",
    },
    "openai/gpt-oss-120b": {
        "context_length": 131_072,
        "max_completion_tokens": 32_768,
        "provider": "openai",
        "description": "gpt-oss-120b — flagship open-weight model with tool calling",
    },

    # Qwen family — Alibaba
    "Qwen/Qwen3-4B-Thinking-2507": {
        "context_length": 32_768,
        "max_completion_tokens": 8_192,
        "provider": "qwen",
        "description": "Qwen3-4B Thinking — small reasoning model",
        "supports_thinking": True,
    },
    "Qwen/Qwen3-Coder-480B-A35B-Instruct": {
        "context_length": 262_144,
        "max_completion_tokens": 32_768,
        "provider": "qwen",
        "description": "Qwen3-Coder 480B — MoE coding model",
    },
    "Qwen/Qwen2.5-Coder-32B-Instruct": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "qwen",
        "description": "Qwen2.5-Coder 32B — coding model",
    },
    "Qwen/Qwen2.5-72B-Instruct": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "qwen",
        "description": "Qwen2.5-72B Instruct",
    },

    # DeepSeek family — reasoning models
    "deepseek-ai/DeepSeek-R1": {
        "context_length": 65_536,
        "max_completion_tokens": 32_768,
        "provider": "deepseek",
        "description": "DeepSeek-R1 — open reasoning model",
        "supports_thinking": True,
    },
    "deepseek-ai/DeepSeek-V3": {
        "context_length": 65_536,
        "max_completion_tokens": 8_192,
        "provider": "deepseek",
        "description": "DeepSeek-V3 chat model",
    },
    "deepseek-ai/DeepSeek-V3.1": {
        "context_length": 131_072,
        "max_completion_tokens": 32_768,
        "provider": "deepseek",
        "description": "DeepSeek-V3.1 — improved chat + tool calling",
    },

    # Meta Llama family
    "meta-llama/Llama-3.3-70B-Instruct": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "meta",
        "description": "Llama 3.3 70B Instruct",
    },
    "meta-llama/Llama-3.1-8B-Instruct": {
        "context_length": 131_072,
        "max_completion_tokens": 4_096,
        "provider": "meta",
        "description": "Llama 3.1 8B Instruct",
    },

    # Google Gemma family
    "google/gemma-3-4b-it": {
        "context_length": 32_768,
        "max_completion_tokens": 4_096,
        "provider": "google",
        "description": "Gemma 3 4B instruct",
    },
    "google/gemma-3-12b-it": {
        "context_length": 32_768,
        "max_completion_tokens": 4_096,
        "provider": "google",
        "description": "Gemma 3 12B instruct",
    },
    "google/gemma-3-27b-it": {
        "context_length": 32_768,
        "max_completion_tokens": 4_096,
        "provider": "google",
        "description": "Gemma 3 27B instruct",
    },

    # Mistral family

    # zai-org / GLM (also accessible via HF router)
    "zai-org/GLM-4.5": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "zai",
        "description": "GLM-4.5 — powerful text generation model",
    },
    "zai-org/GLM-4.5-Air": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "zai",
        "description": "GLM-4.5-Air — lighter variant",
    },

    # Phi family — Microsoft

    # Cohere Command R family

    # === Genuinely $0/token models (verified via live API probe 2026-09-26) ===
    # These 3 models have pricing: {input: 0, output: 0} on at least one
    # partner provider. is_free=false for all 3 (HF's flag is conservative)
    # but the pricing data confirms $0/token — truly free, zero cost.
    "inclusionAI/Ling-3.0-flash-Fin": {
        "context_length": 131_072,
        "max_completion_tokens": 8_192,
        "provider": "inclusionai",
        "description": "Ling-3.0-flash-Fin — financial domain, $0/token via Novita",
    },
    "prism-ml/Ternary-Bonsai-27B-gguf": {
        "context_length": 131_072,
        "max_completion_tokens": 4_096,
        "provider": "prism-ml",
        "description": "Ternary-Bonsai-27B GGUF — 1.58-bit ternary, $0/token via Together",
    },
    "prism-ml/Ternary-Bonsai-27B-AWQ-4bit": {
        "context_length": 131_072,
        "max_completion_tokens": 4_096,
        "provider": "prism-ml",
        "description": "Ternary-Bonsai-27B AWQ 4-bit, $0/token via Together",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# HF_FREE_MODEL_WHITELIST — models eligible for HF_FREE_ONLY=true mode
# ─────────────────────────────────────────────────────────────────────────────
# HF doesn't use OpenRouter's :free suffix convention. Free-tier status is
# determined by the user's account balance (free monthly credit + paid
# balance) and the provider routing choice. This whitelist is a curated
# set of models that historically have free-tier access via partner
# providers.
#
# See HUGGINGFACE_API_TECHNICAL_REFERENCE.md §Free Tier Behavior for the
# full rationale.
# R07.03: Whitelist contains ONLY models with genuinely $0/token
# pricing (verified via live API probe on 2026-09-26). These 3 models
# have pricing: {input: 0, output: 0} on at least one partner provider.
# The is_free flag is false for all 3 (HF's flag is conservative) but
# the pricing data confirms $0/token — truly free, zero cost.
#
# Previously this whitelist had 16 "low-cost" models (gpt-oss-120b,
# DeepSeek-R1, etc.) — but those all have per-token pricing ($0.42-$30K
# per 1M). "Low cost" is not "free" — the $0.10/mo free-tier credit
# covers some usage but eventually runs out. FREE_ONLY means $0/token.
#
# Re-validate quarterly with: bash scripts/probe_huggingface.sh
HF_FREE_MODEL_WHITELIST: frozenset[str] = frozenset({
    "inclusionAI/Ling-3.0-flash-Fin",        # $0 via Novita
    "prism-ml/Ternary-Bonsai-27B-gguf",       # $0 via Together
    "prism-ml/Ternary-Bonsai-27B-AWQ-4bit",   # $0 via Together
})


# ─────────────────────────────────────────────────────────────────────────────
# Provider-suffix validation — supported partner names (Sept 2026)
# ─────────────────────────────────────────────────────────────────────────────
HF_KNOWN_PROVIDERS: frozenset[str] = frozenset({
    "baseten", "cerebras", "cohere", "deepinfra", "fal-ai", "featherless-ai",
    "fireworks", "groq", "hf-inference", "novita", "nscale",
    "ovhcloud", "public-ai", "replicate", "scaleway", "together",
    "wavespeedai", "z.ai",
})

HF_KNOWN_POLICIES: frozenset[str] = frozenset({
    "fastest", "cheapest", "preferred",
})


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (module-level so tests can import them directly)
# ─────────────────────────────────────────────────────────────────────────────

def _is_free_model(model_id: str) -> bool:
    """Check whether a model id is in the HF_FREE_MODEL_WHITELIST.

    Strips any provider-suffix (``:fastest``/``:cheapest``/``:preferred``/
    ``:provider-name``) before checking — the suffix is a routing hint,
    not part of the model identity.
    """
    base = model_id.split(":", 1)[0]
    return base in HF_FREE_MODEL_WHITELIST


def _has_provider_suffix(model_id: str) -> bool:
    """Return True if model_id already has a ``:suffix`` routing policy."""
    return ":" in model_id


def _apply_provider_policy(model_id: str, force_cheapest: bool = False) -> str:
    """Append the HF_PROVIDER_POLICY suffix if model_id has no explicit suffix.

    HF_PROVIDER_POLICY is read from the env var at module import time
    (see config.py). When empty, no suffix is appended — the router
    defaults to :fastest.

    When ``force_cheapest=True`` is passed (or when ``HF_FREE_ONLY`` is
    true at module-import time), the policy is forced to ``:cheapest``
    regardless of HF_PROVIDER_POLICY (free-tier routing for cost
    minimization).

    R07.02 polish: callers that respect auto-detected free-tier mode
    (via ``self._free_only_effective``) should pass ``force_cheapest``
    explicitly; module-level callers that just want the env-var
    behavior can omit it (defaults to ``False``, falls back to the
    ``HF_FREE_ONLY`` module constant for backward compat with tests).
    """
    if _has_provider_suffix(model_id):
        return model_id  # user's explicit suffix wins

    if force_cheapest or HF_FREE_ONLY:
        return f"{model_id}:cheapest"

    policy = (HF_PROVIDER_POLICY or "").strip()
    if not policy:
        return model_id  # router default (:fastest) applies

    return f"{model_id}:{policy}"


def _apply_provider_policy_live(backend: "HuggingFaceBackend", model_id: str) -> str:
    """Instance-aware version of :func:`_apply_provider_policy`.

    Forwards to the module-level helper with
    ``force_cheapest=backend._free_only_effective`` so the auto-detected
    free-tier mode (from ``_probe_whoami``) is honored even when the
    user did NOT set the ``HF_FREE_ONLY`` env var explicitly.
    """
    return _apply_provider_policy(
        model_id,
        force_cheapest=getattr(backend, "_free_only_effective", False),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Backend class
# ─────────────────────────────────────────────────────────────────────────────

class HuggingFaceBackend(OpenAICompatibleBackend):
    """Backend for the Hugging Face Inference Router API.

    The Inference Router (``router.huggingface.co/v1``) is an OpenAI-
    compatible chat completions endpoint that proxies to ~18 partner
    providers (Together, Groq, Novita, DeepInfra, Fireworks, etc.) for
    100+ open-weight models.

    Inherits the shared OpenAI Chat-Completions logic from
    OpenAICompatibleBackend (body construction, response parsing,
    SSE streaming, JEV dispatch, context-length 400 recovery) and
    adds:
      - HF_TOKEN Bearer auth (with HUGGING_FACE_HUB_TOKEN fallback)
      - HF_FREE_ONLY whitelist enforcement
      - HF_FREE_FALLBACK_MODEL swap on HTTP 402 (free-tier exhausted)
      - HF_PROVIDER_POLICY auto-suffix on model id
      - 429 retry loop with Retry-After honor + exponential back-off
      - ReAct fallback when a partner provider rejects the `tools` field
    """

    # Model cache with 1-hour timeout (mirrors OpenRouterBackend).
    _model_cache: list[dict] | None = None
    _cache_time: float = 0.0
    _CACHE_TIMEOUT: int = 3600  # 1 hour in seconds

    # R07.02 polish: process-scoped flag so the free-tier warning
    # prints at most once per process. The CLI may instantiate
    # HuggingFaceBackend twice in one command (once for feature
    # discovery via _probe_backend, once for the actual cmd_models
    # call), and we don't want the user to see the same nudge twice.
    # Class-level (not instance-level) so subsequent instances in the
    # same process skip the print. Reset in tests via direct attribute
    # write — see TestResolveFreeOnlyMode.setUp.
    _free_tier_warning_emitted: bool = False

    # R06.54: maximum retries for rate-limit (429) and transient server
    # (502/503/504) responses before giving up. Partner providers
    # (especially on :cheapest routing) return 429 frequently during
    # peak load — match OpenRouter's patience budget.
    _MAX_429_RETRIES: int = 6

    # Exponential back-off schedule (seconds) when no usable Retry-After
    # header is present. Capped so a broken provider can't hang the agent.
    _429_BACKOFF_BASE: float = 5.0
    _429_BACKOFF_CAP: float = 90.0

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENAI,
    ):
        # Resolve base URL — priority: explicit > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        else:
            resolved_url = HF_BASE_URL.rstrip("/")

        # Validate api_mode. HF Router only exposes the OpenAI
        # Chat-Completions endpoint, but JEV mode is accepted because it
        # uses the same wire format under the hood (JEV wraps the chat
        # call with a decision prompt + JSON parsing).
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode == ApiMode.JEV:
            pass  # accepted — _jev_call_completions routes through generate()
        elif api_mode == ApiMode.OPENAI:
            pass
        else:
            raise ValueError(
                "Hugging Face backend only supports OpenAI Chat-Completions "
                "or JEV (System-One) API modes"
            )

        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # Token is lazy — only required for generation, not for /models
        # listing (which is anonymous on HF Router).
        # Read directly from env at __init__ time (NOT from the
        # module-level HF_TOKEN constant) so tests can set the env var
        # after config.py has already been imported. Mirrors the
        # OpenRouterBackend pattern at openrouter.py:302.
        self.api_key: str = (
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            or os.environ.get("HF_API_KEY")
            or HF_TOKEN  # fall back to module-level (in case it was set at startup)
        )

        # ROB-06: Persisted safe max_tokens after a context-length 400.
        # When set, _get_model_defaults() returns this instead of the
        # model's reported max_completion_tokens. Mirrors OpenRouterBackend.
        self._context_safe_max_tokens: int | None = None

        # Headers for HF Router. No HTTP-Referer/X-Title attribution —
        # HF doesn't have OpenRouter's leaderboard system. The User-Agent
        # is set for diagnostic purposes only.
        self.headers: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AgentKthx/0.x (+https://github.com/VTSTech/AgentKthx)",
        }

        # R07.02 polish: probe /api/whoami-v2 to (a) validate the token
        # before the first chat call (catches typos / expired tokens
        # early with a clear actionable error instead of a confusing
        # 401 mid-conversation), and (b) detect free-tier users
        # (canPay=False → auto-enable HF_FREE_ONLY-style whitelist
        # protection even when the user did NOT set the env var).
        # Failures here are non-fatal — the backend still works without
        # a valid token for /v1/models (which is anonymous), but paid
        # inference calls will fail with 401 at request time.
        self._user_info: dict | None = None  # populated by _probe_whoami()
        self._probe_whoami()  # best-effort, never raises

        # Resolve effective HF_FREE_ONLY behavior:
        # - User explicitly set HF_FREE_ONLY=true  → strict (no auto-detect)
        # - User explicitly set HF_FREE_ONLY=false → permissive (opt-out)
        # - User did NOT set HF_FREE_ONLY          → auto-detect via whoami:
        #     canPay=False (free tier) → strict + one-time warning
        #     canPay=True  (paid tier) → permissive
        #     whoami unreachable        → fall back to module constant
        self._free_only_effective: bool = self._resolve_free_only_mode()

        # Force model list to be loaded on initialization so the cache
        # is populated (mirrors OpenRouterBackend). Failures are silent
        # — the static HF_MODELS catalog is the fallback.
        try:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print("  [HF Debug] Initializing: loading models into cache")
            self.list_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [HF Debug] Failed to initialize models: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # whoami-v2 probe + free-tier auto-detection (R07.02 polish)
    # ─────────────────────────────────────────────────────────────────────

    def _probe_whoami(self) -> None:
        """Probe ``https://huggingface.co/api/whoami-v2`` to validate the
        token and detect free-tier users.

        Stores the response (or ``None`` on failure) on
        ``self._user_info``. Never raises — failures here are
        non-fatal. The CLI surfaces the result in ``--debug`` output
        and ``_resolve_free_only_mode()`` uses ``canPay=False`` to
        auto-enable free-tier protection when ``HF_FREE_ONLY`` is
        unset.

        Token-role check: a ``read``-role token (the default fine-
        grained token) is enough for ``whoami-v2`` — the endpoint
        returns the user's name, email, billing mode, and free-tier
        period end. Higher-role endpoints (``/api/inference-providers``,
        ``/api/billing/usage``) require ``write`` or ``admin`` role
        and return 401 for ``read``-role tokens — we don't need them
        for the AgentKthx use case.
        """
        if not self.api_key:
            # Nothing to probe — ensure _user_info is None so
            # _resolve_free_only_mode falls back cleanly.
            self._user_info = None
            return
        try:
            req = urllib.request.Request(
                "https://huggingface.co/api/whoami-v2",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "AgentKthx/0.x (+https://github.com/VTSTech/AgentKthx)",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._user_info = data
            if os.environ.get("AGENTKTHX_DEBUG"):
                name = data.get("name", "<unknown>")
                role = (
                    data.get("auth", {})
                    .get("accessToken", {})
                    .get("role", "?")
                )
                can_pay = data.get("canPay", True)
                period_end = data.get("periodEnd")
                tier = "free" if not can_pay else "paid"
                period_str = ""
                if period_end:
                    import datetime as _dt
                    period_dt = _dt.datetime.fromtimestamp(
                        period_end, tz=_dt.timezone.utc
                    )
                    period_str = (
                        f", period ends {period_dt.strftime('%Y-%m-%d')}"
                    )
                print(
                    f"  [HF Debug] Token valid for user '{name}' "
                    f"(role={role}, {tier} tier{period_str})"
                )
        except Exception as e:
            # whoami-v2 unreachable (network down, DNS fail, 401 with
            # bad token, 5xx, etc.). Non-fatal — the backend still
            # works for /v1/models (anonymous) and paid inference
            # (will surface its own 401 at request time).
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [HF Debug] /api/whoami-v2 unreachable: {e}")
            self._user_info = None

    def _resolve_free_only_mode(self) -> bool:
        """Resolve effective ``HF_FREE_ONLY`` behavior.

        Returns ``True`` if free-tier whitelist enforcement should be
        active. Resolution order:

        1. **User explicitly set ``HF_FREE_ONLY=true``** → strict
           (no auto-detect; respect explicit opt-in)
        2. **User explicitly set ``HF_FREE_ONLY=false``** → permissive
           (opt-out; user accepts paid API calls)
        3. **User did NOT set ``HF_FREE_ONLY``** → auto-detect via
           whoami-v2 response:
              - ``canPay=False`` (free tier) → strict + one-time
                warning to stderr (so the user knows why their paid
                model request was rejected)
              - ``canPay=True`` (paid tier) → permissive
              - whoami unreachable → fall back to module-level
                ``HF_FREE_ONLY`` constant (parsed from env at
                config.py import time, defaults to False)

        The env-var check is done by reading ``os.environ`` directly
        (not the parsed bool constant) so we can distinguish "user
        set the var" from "default false" — critical for the auto-
        detect path to work correctly.
        """
        env_val = os.environ.get("HF_FREE_ONLY", "").strip().lower()
        if env_val in ("1", "true", "yes"):
            return True  # explicit strict
        if env_val in ("0", "false", "no"):
            return False  # explicit permissive (opt-out)

        # User didn't set HF_FREE_ONLY → auto-detect from whoami
        if self._user_info is not None:
            can_pay = self._user_info.get("canPay", True)
            if not can_pay:
                # Free-tier user — auto-enable free-tier protection
                # and emit a one-time warning so the user understands
                # why their non-whitelisted model request will be
                # rejected (and how to override).
                #
                # R07.02 polish: the warning is process-scoped via the
                # class-level _free_tier_warning_emitted flag — the
                # CLI may instantiate HuggingFaceBackend twice in one
                # command (once for _probe_backend discovery, once for
                # the actual cmd_models call), and we don't want the
                # user to see the same nudge twice. Subsequent instances
                # in the same process skip the print but still get the
                # enforcement (return True).
                if not HuggingFaceBackend._free_tier_warning_emitted:
                    HuggingFaceBackend._free_tier_warning_emitted = True
                    import sys
                    period_end = self._user_info.get("periodEnd")
                    period_str = ""
                    if period_end:
                        import datetime as _dt
                        period_dt = _dt.datetime.fromtimestamp(
                            period_end, tz=_dt.timezone.utc
                        )
                        period_str = (
                            f" Credit refreshes "
                            f"{period_dt.strftime('%Y-%m-%d')}."
                        )
                    print(
                        f"\n  \033[33m[HF] Detected free-tier account "
                        f"(no billing card on file, $0.10/mo credit at "
                        f"partner provider rates.{period_str})\n"
                        f"  Auto-enabling HF_FREE_ONLY whitelist — only "
                        f"models in HF_FREE_MODEL_WHITELIST are accepted "
                        f"to prevent accidental paid API calls.\n"
                        f"  Set HF_FREE_ONLY=false to override (requires "
                        f"a paid HF token with billing enabled).\033[0m\n",
                        file=sys.stderr,
                    )
                return True
        # Fall back: HF_FREE_ONLY unset AND (whoami unreachable OR
        # paid-tier user). Use module-level constant (parsed from env
        # at config.py import time, defaults to False).
        return HF_FREE_ONLY

    # ─────────────────────────────────────────────────────────────────────
    # Required BaseBackend properties
    # ─────────────────────────────────────────────────────────────────────

    @property
    def backend_type(self) -> BackendType:
        return BackendType.HUGGINGFACE

    @property
    def base_url(self) -> str:
        return self._base_url

    # api_mode property/setter is inherited from OpenAICompatibleBackend

    # ─────────────────────────────────────────────────────────────────────
    # Model listing & discovery
    # ─────────────────────────────────────────────────────────────────────

    def _parse_hf_model(self, model_data: dict) -> dict:
        """Parse HF Router /v1/models response into AgentKthx format.

        Live API response shape (verified Sept 2026 via direct probe of
        https://router.huggingface.co/v1/models)::

            {
              "id": "Qwen/Qwen3.8-27B",
              "object": "model",
              "created": 1785918179,
              "owned_by": "Qwen",
              "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"]
              },
              "providers": [
                {
                  "provider": "novita",
                  "status": "live",                          # "live" / "down" / "loading"
                  "context_length": 1000000,
                  "pricing": {"input": 0.42, "output": 3.0}, # USD per 1M tokens
                  "is_free": false,                          # ← free-tier flag
                  "supports_tools": true,
                  "supports_structured_output": false,
                  "first_token_latency_ms": 762.6,
                  "throughput": 39.32,                        # tokens/sec
                  "is_model_author": false
                },
                ...
              ]
            }

        The full per-provider list is preserved under the ``providers``
        key so callers (e.g. ``_is_free_model_live()``) can inspect
        per-provider fields. Top-level ``context_length`` and
        ``max_completion_tokens`` are aggregated as the max across
        providers (best-case budget — the actual budget per request
        depends on which provider the router picks, which is non-
        deterministic under :fastest routing).

        As of 2026-09-26: ``is_free`` is ``false`` for all 336 combos
        even with auth — the free-tier model is purely credit-based
        ($0.10/mo credit, see HUGGINGFACE_API_TECHNICAL_REFERENCE.md).
        The ``is_free`` flag is captured anyway so HF can flip any
        combo free tomorrow (sponsored/promo window) and AgentKthx
        auto-picks it up with zero code change via
        ``_is_free_model_live()``.
        """
        model_id = model_data.get("id", "")
        if not model_id:
            return {}

        raw_providers = model_data.get("providers") or []

        # Aggregate per-provider context_length (take max across providers
        # — gives the agent the best-case budget for routing decisions)
        context_lengths = [
            p.get("context_length")
            for p in raw_providers
            if isinstance(p.get("context_length"), int)
        ]
        if context_lengths:
            context_length = max(context_lengths)
        else:
            # Fall back to top-level field (some legacy models may
            # expose it there). Conservative default if neither.
            context_length = (
                model_data.get("context_length")
                or 128_000
            )

        # max_completion_tokens is not consistently exposed per-provider
        # in the current /v1/models response. Take the max across
        # providers when available; conservative default 8192 otherwise.
        max_completion_tokens_list = [
            p.get("max_completion_tokens")
            for p in raw_providers
            if isinstance(p.get("max_completion_tokens"), int)
        ]
        if max_completion_tokens_list:
            max_completion_tokens = max(max_completion_tokens_list)
        else:
            max_completion_tokens = (
                model_data.get("max_completion_tokens")
                or model_data.get("top_provider", {}).get("max_completion_tokens")
                or 8192
            )

        # Cheapest provider rates (USD per 1M tokens) — for surfacing
        # in the model listing later (Change #4, deferred). Captured
        # now for forward-compat.
        input_rates = [
            p.get("pricing", {}).get("input")
            for p in raw_providers
            if isinstance(p.get("pricing", {}).get("input"), (int, float))
        ]
        output_rates = [
            p.get("pricing", {}).get("output")
            for p in raw_providers
            if isinstance(p.get("pricing", {}).get("output"), (int, float))
        ]
        cheapest_input = min(input_rates) if input_rates else None
        cheapest_output = min(output_rates) if output_rates else None

        # Free-tier signals aggregated across providers
        any_free_provider = any(
            bool(p.get("is_free", False)) for p in raw_providers
        )
        # Tool-support signals (any provider supports tools → model is
        # tool-callable via that provider)
        any_supports_tools = any(
            bool(p.get("supports_tools", False)) for p in raw_providers
        )
        all_support_tools = bool(raw_providers) and all(
            bool(p.get("supports_tools", False)) for p in raw_providers
        )
        any_supports_structured = any(
            bool(p.get("supports_structured_output", False)) for p in raw_providers
        )

        # Family detection: the org prefix in the model id (e.g.
        # "openai/" in "openai/gpt-oss-120b") is a good proxy for family.
        family = model_id.split("/", 1)[0] if "/" in model_id else "unknown"

        return {
            "name": model_id,
            "size": 0,  # HF Router doesn't expose size info
            "details": {
                "family": family,
                "backend": "huggingface",
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
                # New per-provider aggregated fields (R07.02 polish):
                "cheapest_input_per_1m": cheapest_input,
                "cheapest_output_per_1m": cheapest_output,
                "any_free_provider": any_free_provider,
                "any_supports_tools": any_supports_tools,
                "all_support_tools": all_support_tools,
                "any_supports_structured_output": any_supports_structured,
                "owned_by": model_data.get("owned_by", ""),
                "provider_count": len(raw_providers),
            },
            "model_data": model_data,  # full raw response preserved (debug, future-proofing)
            "providers": raw_providers,  # shortcut for _is_free_model_live() and friends
        }

    # ─────────────────────────────────────────────────────────────────────
    # Live-API-supplemented free-model check (R07.02 polish)
    # ─────────────────────────────────────────────────────────────────────

    def _is_free_model_live(self, model_id: str) -> bool:
        """Live-API-supplemented free-model check.

        Returns True if EITHER:
        (a) The model is in the static ``HF_FREE_MODEL_WHITELIST`` (the
            ground truth when /v1/models is unreachable), OR
        (b) The live /v1/models response has any provider with
            ``is_free=true`` for this model.

        As of 2026-09-26: ``is_free`` is ``false`` for all 336 combos
        even with auth — the free-tier model is purely credit-based.
        But HF can flip this true at any time for sponsored/promo
        windows, and this method auto-picks that up with zero code
        change. The static whitelist stays as the documented ground
        truth fallback.

        Strips the provider suffix before lookup — the suffix is a
        routing hint, not part of the model identity.
        """
        # Fast path: static whitelist (no instance state needed)
        base = model_id.split(":", 1)[0]
        if base in HF_FREE_MODEL_WHITELIST:
            return True

        # Slow path: consult live API cache for any provider flagged
        # is_free=true. The cache is populated by list_models() on
        # init; if it's empty (API unreachable, falling back to
        # static catalog), there's nothing live to consult.
        if not self._model_cache:
            return False

        for cached in self._model_cache:
            if cached.get("name") != base:
                continue
            # Use the shortcut `providers` array set by _parse_hf_model.
            # Fall back to model_data.providers for older cache entries
            # that don't have the shortcut (defensive).
            providers = cached.get("providers") or []
            if not providers:
                providers = (
                    cached.get("model_data", {}).get("providers", [])
                )
            return any(bool(p.get("is_free", False)) for p in providers)

        # Model not in cache (not in static whitelist, not in live API)
        return False

    def list_models(self) -> list[dict]:
        """List available models from HF Router /v1/models with caching.

        Cache timeout: 1 hour (3600 seconds).
        Refresh endpoint: GET /v1/models (anonymous — no auth required).

        When ``HF_FREE_ONLY`` is true, the cache is filtered to only
        include models in ``HF_FREE_MODEL_WHITELIST``.
        """
        current_time = time.time()
        if (self._model_cache is not None
                and current_time - self._cache_time < self._CACHE_TIMEOUT):
            return self._model_cache

        try:
            # /v1/models is anonymous on HF Router — no Authorization
            # header required. We still set User-Agent for diagnostic
            # purposes and Authorization if available (some endpoints
            # may rate-limit anonymous callers).
            headers = {"User-Agent": "AgentKthx/0.x"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                models_data = json.loads(resp.read().decode("utf-8"))

            available_models: list[dict] = []
            for model in models_data.get("data", []):
                model_id = model.get("id")
                if model_id:
                    available_models.append(self._parse_hf_model(model))

            # Add catalog-only models (not returned by API but still in
            # our static HF_MODELS catalog) — fallback safety net.
            cached_names = {m["name"] for m in available_models}
            for name, info in HF_MODELS.items():
                if name not in cached_names:
                    available_models.append({
                        "name": name,
                        "size": 0,
                        "details": {
                            "family": info.get("provider", "unknown"),
                            "backend": "huggingface",
                            "context_length": info.get("context_length", 128_000),
                            "max_completion_tokens": info.get("max_completion_tokens", 4096),
                        },
                    })

            # HF_FREE_ONLY: filter to whitelist only (R07.02 polish: use
            # self._free_only_effective so auto-detected free-tier mode
            # also filters — and consult both the static whitelist AND
            # the live is_free flag from freshly-parsed providers).
            if self._free_only_effective:
                available_models = [
                    m for m in available_models
                    if _is_free_model(m["name"])
                    or any(
                        bool(p.get("is_free", False))
                        for p in (m.get("providers") or [])
                    )
                ]

            self._model_cache = sorted(available_models, key=lambda x: x["name"])
            self._cache_time = current_time

            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [HF Debug] Stored {len(self._model_cache)} models in cache")
                for m in self._model_cache[:10]:
                    print(f"    - {m['name']}")
                if len(self._model_cache) > 10:
                    print(f"    ... and {len(self._model_cache) - 10} more")

            return self._model_cache

        except Exception as e:
            # Fallback to static catalog if API fails
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [HF Debug] /v1/models unreachable, using static catalog: {e}")

            catalog_models: list[dict] = []
            for name, info in HF_MODELS.items():
                catalog_models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": info.get("provider", "unknown"),
                        "backend": "huggingface",
                        "context_length": info.get("context_length", 128_000),
                        "max_completion_tokens": info.get("max_completion_tokens", 4096),
                    },
                })

            if self._free_only_effective:
                catalog_models = [m for m in catalog_models if _is_free_model(m["name"])]

            self._model_cache = sorted(catalog_models, key=lambda x: x["name"])
            self._cache_time = current_time
            return self._model_cache

    def is_running(self) -> bool:
        """HF Router is a cloud API, so it's always 'running'."""
        return True

    def _get_model_info(self, model_name: str) -> dict | None:
        """Get model metadata from catalog, cache, or API."""
        # Strip routing suffix before lookup — suffix isn't part of identity
        base_name = model_name.split(":", 1)[0]

        # Check static catalog first
        if base_name in HF_MODELS:
            return HF_MODELS[base_name]

        # Check cache if available
        if self._model_cache:
            for cached in self._model_cache:
                if cached["name"] == base_name or cached["name"] == model_name:
                    details = cached["details"]
                    return {
                        "max_tokens": details.get("max_completion_tokens", 4096),
                        "context_length": details.get("context_length", 128_000),
                    }

        return None

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """Get the model's maximum trained context window size.

        Uses live HF Router /v1/models data for accurate context lengths.
        """
        base_name = model.split(":", 1)[0]

        # Check cache first
        if self._model_cache:
            for cached in self._model_cache:
                if cached["name"] == base_name:
                    return cached["details"].get("context_length", 128_000)

        # Fallback to catalog
        info = self._get_model_info(model)
        if info and "context_length" in info:
            return info["context_length"]

        # Fallback to family-based defaults from OpenAICompatibleBackend
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx

        return 128_000

    def _get_model_defaults(self, model: str) -> dict:
        """Return per-model defaults: temperature, max_tokens, context_length.

        ARCH-03 (R06.57): cap + persisted-safe-value logic inherited
        from OpenAICompatibleBackend._apply_max_tokens_cap. Many HF
        partner providers report max_completion_tokens close to the
        full context_length (e.g. 131072), leaving no room for input
        on long agentic runs. The num_ctx // 32 cap (4096 on a 131072
        context) gives 97% of context to input — proven safe in
        OpenRouter R06.55 testing.
        """
        base_name = model.split(":", 1)[0]

        # Check cache first
        if self._model_cache:
            for cached in self._model_cache:
                if cached["name"] == base_name:
                    details = cached["details"]
                    max_tokens = details.get("max_completion_tokens", 4096)
                    context_length = details.get("context_length", 128_000)
                    return self._apply_max_tokens_cap(
                        max_tokens, context_length, temperature=0.7
                    )

        # Fallback to catalog
        info = self._get_model_info(model)
        max_tokens = info.get("max_tokens", 4096) if info else 4096
        context_length = info.get("context_length", 128_000) if info else 128_000
        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=0.7
        )

    # ─────────────────────────────────────────────────────────────────────
    # 429 / 5xx retry helpers (mirrors OpenRouterBackend R06.54)
    # ─────────────────────────────────────────────────────────────────────

    def _max_429_retries(self) -> int:
        """Resolve the 429 retry budget (env override > class default)."""
        raw = os.environ.get("HF_MAX_429_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_429_RETRIES

    def _429_backoff(self, attempt: int) -> float:
        """Back-off wait for the Nth (1-based) rate-limit retry.

        Schedule: 5s → 10s → 20s → 40s → 80s → 90s cap, with ±20% jitter.
        """
        delay = self._429_BACKOFF_BASE * (2 ** max(0, attempt - 1))
        delay = min(delay, self._429_BACKOFF_CAP)
        jitter = delay * 0.2
        return max(1.0, delay + random.uniform(-jitter, jitter))

    # ─────────────────────────────────────────────────────────────────────
    # HTTP request wrapper — 429/5xx retry, 401 detection, 402 fallback
    # ─────────────────────────────────────────────────────────────────────

    def _make_api_request(
        self,
        endpoint: str,
        data: dict,
        stream: bool = False,
    ) -> dict | Iterable[bytes]:
        """Make a request to HF Router with automatic 429/5xx retry and
        402 free-tier-exhaustion fallback.

        On HTTP 429 (rate limit) or transient 5xx errors, waits —
        honoring the ``Retry-After`` header when present, otherwise an
        exponential back-off schedule — and retries up to
        ``_max_429_retries()`` times (default 6).

        On HTTP 402 (Payment Required, free-tier credit exhausted):
          - If ``HF_FREE_ONLY`` is true, raises a clear actionable error
            (no retry — retrying burns router quota without resolving).
          - Otherwise, swaps the model to ``HF_FREE_FALLBACK_MODEL``
            and retries once (mirrors the ZAI plugin's 429 insufficient
            balance fallback at zai.py:706-714).

        Other errors are normalized to RuntimeError carrying the
        upstream error message so callers can pattern-match on the
        text (e.g. to detect "does not support tools" for the ReAct
        fallback path).
        """
        url = f"{self.base_url}/{endpoint}"

        if not self.api_key:
            raise ValueError(
                "HF_TOKEN environment variable is required for Hugging Face "
                "inference calls. Generate a fine-grained token at "
                "https://huggingface.co/settings/tokens with 'Make calls to "
                "Inference Providers' permission."
            )

        headers = dict(self.headers)
        headers["Authorization"] = f"Bearer {self.api_key}"

        if stream:
            return self._stream_request(url, data, headers)

        # Pre-emptive: ensure the model id carries the right routing suffix
        # (no-op if user already appended one explicitly).
        # R07.02 polish: use _apply_provider_policy_live so the auto-detected
        # free-tier mode (via _probe_whoami + _resolve_free_only_mode) is
        # honored, not just the explicit HF_FREE_ONLY env var.
        if "model" in data:
            data = {**data, "model": _apply_provider_policy_live(self, data["model"])}

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

                # ---- 402 Payment Required: free-tier credit exhausted ----
                if status_code == 402:
                    current_model = data.get("model", "")
                    if self._free_only_effective:
                        # Strict mode: no retry, no fallback — surface
                        # actionable error.
                        raise RuntimeError(
                            f"Hugging Face free-tier credit exhausted for "
                            f"'{current_model}'. Set HF_FREE_ONLY=false "
                            f"and add a billing card at "
                            f"https://huggingface.co/settings/billing, "
                            f"or wait for the monthly credit reset."
                        )
                    # Non-strict: swap to HF_FREE_FALLBACK_MODEL, retry once
                    fallback = HF_FREE_FALLBACK_MODEL
                    if self._is_free_model_live(current_model):
                        # Already a free model and still 402 — credit is
                        # truly exhausted. Don't retry; surface the error.
                        raise RuntimeError(
                            f"Hugging Face free-tier credit exhausted and "
                            f"fallback '{fallback}' is also a free model — "
                            f"no point retrying. Add billing at "
                            f"https://huggingface.co/settings/billing."
                        )
                    if not _has_provider_suffix(fallback):
                        fallback = _apply_provider_policy_live(self, fallback)
                    import sys
                    print(
                        f"\n  \033[33m[HF] Free-tier credit exhausted for "
                        f"'{current_model}' — falling back to "
                        f"'{fallback}'\033[0m",
                        file=sys.stderr,
                    )
                    fallback_data = {**data, "model": fallback}
                    fallback_req = urllib.request.Request(
                        url,
                        data=json.dumps(fallback_data).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(
                            fallback_req, timeout=self.config.timeout
                        ) as resp2:
                            return json.loads(resp2.read().decode("utf-8"))
                    except urllib.error.HTTPError as e2:
                        error_body2 = (
                            e2.read().decode("utf-8") if e2.fp else ""
                        )
                        raise RuntimeError(
                            f"Hugging Face: paid model '{current_model}' "
                            f"failed (HTTP 402, free-tier exhausted) and "
                            f"free fallback '{fallback}' also failed: "
                            f"HTTP {e2.code}: {error_body2}"
                        )

                # ---- 429 Rate Limit / transient 5xx: wait and retry ----
                retryable = (
                    status_code == 429
                    or status_code in (502, 503, 504)
                )
                if retryable:
                    error_msg = (
                        "Rate limit exceeded"
                        if status_code == 429
                        else f"Provider error {status_code}"
                    )
                    retry_after_raw = e.headers.get("Retry-After", "")
                    if isinstance(err_data, dict):
                        if "error" in err_data:
                            inner = err_data["error"]
                            error_msg = (
                                inner.get("message", inner)
                                if isinstance(inner, dict) else str(inner)
                            )
                        elif "message" in err_data:
                            error_msg = err_data["message"]
                    last_retryable_error = error_msg

                    retry_after = None
                    if retry_after_raw:
                        try:
                            retry_after = float(retry_after_raw)
                        except (ValueError, TypeError):
                            retry_after = None
                    if retry_after is None:
                        retry_after = self._429_backoff(attempt + 1)
                    retry_after = min(max(retry_after, 1.0), self._429_BACKOFF_CAP)

                    if attempt < max_retries:
                        print(
                            f"  [HF] {status_code} — {error_msg}. "
                            f"Retrying in {retry_after:.0f}s "
                            f"(attempt {attempt + 1}/{max_retries + 1})..."
                        )
                        time.sleep(retry_after)
                        continue
                    else:
                        raise RuntimeError(
                            f"Hugging Face rate limit: {error_msg}. "
                            f"Retried {max_retries} times. "
                            f"Try again in {retry_after:.0f} seconds."
                        )

                # ---- 401 Auth error ----
                if status_code == 401:
                    raise RuntimeError(
                        "Hugging Face authentication failed. Please check "
                        "your HF_TOKEN environment variable. Generate a "
                        "fine-grained token at "
                        "https://huggingface.co/settings/tokens with 'Make "
                        "calls to Inference Providers' permission."
                    )

                # ---- Any other 4xx/5xx error ----
                if status_code >= 400:
                    upstream_msg = ""
                    if isinstance(err_data, dict):
                        err_field = err_data.get("error")
                        if isinstance(err_field, dict):
                            upstream_msg = err_field.get("message", "") or str(err_field)
                        elif isinstance(err_field, str):
                            upstream_msg = err_field
                        elif err_data.get("message"):
                            upstream_msg = err_data["message"]
                        else:
                            upstream_msg = str(err_data)
                    else:
                        upstream_msg = body_text[:500]

                    if len(upstream_msg) > 500:
                        upstream_msg = upstream_msg[:500] + "..."

                    raise RuntimeError(
                        f"Hugging Face API error {status_code}: {upstream_msg}"
                    )

            except urllib.error.URLError as e:
                raise RuntimeError(f"Hugging Face connection error: {e.reason}")

        # Should not reach here — the loop either returns or raises.
        raise RuntimeError(
            f"Hugging Face API request exhausted retries: {last_retryable_error}"
        )

    def _stream_request(
        self,
        url: str,
        data: dict,
        headers: dict,
    ) -> Generator[bytes, None, None]:
        """Low-level SSE stream — yields raw bytes lines from the SSE stream.

        ROB-04 (R06.56): stdlib ``urllib.request`` (not ``requests``) to
        preserve the zero-dependency claim. ROB-06 (R06.57): try/finally
        so the urllib response is closed deterministically when the
        generator is abandoned mid-iteration.

        Note: streaming requests do NOT retry on 429 — the base class
        ``generate_completions_stream()`` parses these lines and surfaces
        errors via the streaming contract. Retries would re-send the
        same oversized request body.
        """
        # Apply provider policy to streaming requests too
        if "model" in data:
            data = {**data, "model": _apply_provider_policy_live(self, data["model"])}

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
            # Surface 402 / 429 / 5xx as RuntimeError so callers can
            # pattern-match (same as _make_api_request above).
            if e.code == 402 and not self._free_only_effective:
                # Try the free fallback on streaming 402 too
                current = data.get("model", "")
                fallback = _apply_provider_policy_live(self, HF_FREE_FALLBACK_MODEL)
                if self._is_free_model_live(current):
                    raise RuntimeError(
                        f"Hugging Face free-tier credit exhausted (stream) "
                        f"and fallback '{fallback}' is also a free model."
                    )
                import sys
                print(
                    f"\n  \033[33m[HF-Stream] Free-tier credit exhausted for "
                    f"'{current}' — falling back to '{fallback}'\033[0m",
                    file=sys.stderr,
                )
                fb_data = {**data, "model": fallback}
                fb_req = urllib.request.Request(
                    url,
                    data=json.dumps(fb_data).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    response = urllib.request.urlopen(
                        fb_req, timeout=self.config.timeout
                    )
                except urllib.error.HTTPError as e2:
                    error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                    raise RuntimeError(
                        f"Hugging Face HTTP error {e2.code} (stream fallback): "
                        f"{error_body2}"
                    )
            else:
                raise RuntimeError(
                    f"Hugging Face HTTP error {e.code} (stream): {error_body}"
                )
        except urllib.error.URLError as e:
            raise RuntimeError(f"Hugging Face connection error: {e.reason}")

        try:
            for line in response:
                yield line
        finally:
            try:
                response.close()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Tool-support testing
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Test tool support for a model via HF Router.

        Hugging Face is a multi-provider aggregator. Most partner
        providers (Together, Fireworks, Groq, Novita, Cerebras) support
        OpenAI function calling for chat-capable models, but the actual
        support depends on the partner that ends up serving the request
        (which varies with :fastest routing).

        We assume NATIVE for every chat-capable model without probing —
        no live API call is made. The actual generate() path keeps a
        defensive ReAct fallback for the case where a specific partner
        rejects the `tools` field at runtime (HTTP 400), so text-format
        tool calls can still flow through the Agent's ToolParser.
        """
        return ToolSupportLevel.NATIVE

    # ─────────────────────────────────────────────────────────────────────
    # Generate (non-streaming)
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
        """Generate a response using HF Router's Chat Completions API.

        Implements the OpenAI Chat Completions spec for Hugging Face,
        with native tool-calling support, automatic ReAct fallback when
        a partner provider rejects the `tools` field, and context-length
        400 recovery (shared with all OpenAI-compatible backends via
        ARCH-03).

        Args:
            model: HF model id (e.g. "openai/gpt-oss-120b"). A routing
                suffix (":fastest", ":cheapest", ":preferred",
                ":<provider>") may be appended; if absent, the
                HF_PROVIDER_POLICY env var is auto-appended (or
                ":cheapest" when HF_FREE_ONLY is true).
            messages: Chat messages in OpenAI format.
            tools: Optional list of Tool objects for native function
                calling.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.
            **kwargs: Optional OpenAI params — top_p, stop,
                presence_penalty, frequency_penalty, response_format,
                tool_choice, reasoning_effort, etc.

        Returns:
            Dict with keys: content, tool_calls, finish_reason, usage,
            latency_ms, raw.
        """
        # JEV dispatch — if api_mode is JEV, route through
        # generate_decision() which wraps the underlying LLM call with
        # a decision prompt.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else 8192,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # HF_FREE_ONLY: reject non-whitelisted models upfront (before any
        # HTTP request is made — prevents accidental paid API calls).
        if self._free_only_effective and not self._is_free_model_live(model):
            raise RuntimeError(
                f"Model '{model}' is not in the Hugging Face free-tier "
                f"whitelist (HF_FREE_MODEL_WHITELIST in "
                f"agentkthx/plugins/huggingface/huggingface.py). Either "
                f"set HF_FREE_ONLY=false (requires paid HF token with "
                f"billing enabled) or pick a whitelisted model. See "
                f"HUGGINGFACE_API_TECHNICAL_REFERENCE.md §Free Tier "
                f"Behavior for the list."
            )

        # Apply provider policy (no-op if user already appended a suffix)
        routed_model = _apply_provider_policy_live(self, model)

        # Use model defaults from catalog/cache if not specified
        defaults = self._get_model_defaults(routed_model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        body = self._build_openai_body(
            model=routed_model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [HF] POST chat/completions — model={routed_model}, "
                f"tools={len(tools) if tools else 0}, "
                f"tool_choice={kwargs.get('tool_choice', 'auto')}"
            )

        start_time = time.time()
        try:
            raw_response = self._make_api_request("chat/completions", body)
        except RuntimeError as e:
            err_str = str(e)
            # ReAct fallback: some partner providers reject the `tools`
            # field for models that nominally support function calling.
            # Retry without it so the model can emit text-format tool
            # calls that the ToolParser handles.
            if tools and self._is_tools_not_supported_error(err_str):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(
                        f"  [HF] Partner provider rejected tools — retrying "
                        f"without tools (ReAct fallback)"
                    )
                body.pop("tools", None)
                body.pop("tool_choice", None)
                raw_response = self._make_api_request("chat/completions", body)
            # Context-length 400: shared handler from OpenAICompatibleBackend.
            # Reduce max_tokens and retry once. The handler persists the
            # safe value on self._context_safe_max_tokens so future
            # _get_model_defaults() calls reuse it.
            elif "context length" in err_str.lower() or "maximum context" in err_str.lower():
                old_max = body.get("max_tokens", 4096)
                # Inline 1/3 reduction as a fallback if the shared
                # regex-based handler can't parse the error body. The
                # shared handler is the preferred path; this is the
                # belt-and-suspenders fallback.
                new_max = max(old_max // 3, 4096)
                if new_max < old_max:
                    print(
                        f"  [HF] Context length exceeded — reducing "
                        f"max_tokens {old_max} -> {new_max} and retrying"
                    )
                    body["max_tokens"] = new_max
                    self._context_safe_max_tokens = new_max
                    raw_response = self._make_api_request("chat/completions", body)
                else:
                    raise RuntimeError(f"Hugging Face API error: {err_str}")
            else:
                raise RuntimeError(f"Hugging Face API error: {err_str}")

        latency_ms = (time.time() - start_time) * 1000
        parsed = self._parse_openai_response(raw_response)
        parsed["latency_ms"] = latency_ms

        # Synthesize finish_reason if the API omitted one
        if parsed["finish_reason"] is None:
            if parsed["tool_calls"]:
                parsed["finish_reason"] = "tool_calls"
            else:
                parsed["finish_reason"] = "stop"

        # Detect empty responses — partner provider silently failed
        # (filter, model issue). Surface as an error so the chat loop
        # can show the user something went wrong.
        if not parsed["content"].strip() and not parsed["tool_calls"]:
            raise RuntimeError(
                f"Hugging Face returned an empty response (no content, no "
                f"tool_calls) for model '{routed_model}'. This may be a "
                f"rate limit, content filter, or partner-provider issue. "
                f"finish_reason={parsed['finish_reason']}"
            )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(
                f"  [HF] finish_reason={parsed['finish_reason']}, "
                f"tool_calls={len(parsed['tool_calls'])}, "
                f"content_len={len(parsed['content'])}"
            )

        return parsed

    # ─────────────────────────────────────────────────────────────────────
    # System-One Decision Mode (ApiMode.JEV)
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
        """JEV hook for HF: route the decision call through HF Router's
        /chat/completions endpoint with full auth, 429 retry, and
        HF_FREE_ONLY handling — all of which live in self.generate().

        The response shape returned by self.generate() already matches
        what generate_decision() expects:
            {content, tool_calls, usage, latency_ms, raw, finish_reason}

        Decisions never carry tools — pass tools=None explicitly so
        the ReAct fallback path in self.generate() doesn't trigger.

        NOTE: We intentionally do NOT pass response_format to HF Router
        here. Some partner providers silently return empty content when
        response_format={"type":"json_object"} is forced (they don't
        support JSON mode). The JEV System-One prompt already instructs
        the model to output JSON-only, so response_format is redundant.
        """
        kwargs.pop("response_format", None)

        # HF_FREE_ONLY is enforced inside self.generate(); we don't
        # silently swap models here — the user picked the model.

        # CRITICAL: Temporarily flip api_mode to OPENAI to avoid
        # infinite recursion. self.generate() calls _maybe_jev_dispatch()
        # at the top, which would call generate_decision() →
        # _jev_call_completions() → self.generate() again.
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
                        print(
                            f"  [HF.JEV] Empty response — retrying with "
                            f"simplified prompt"
                        )
                    simplified = [
                        {"role": "user", "content": messages[-1]["content"] if messages else ""}
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

    # ─────────────────────────────────────────────────────────────────────
    # ReAct-fallback error detection (mirrors OpenRouterBackend)
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_tools_not_supported_error(err_str: str) -> bool:
        """Detect HF Router / upstream partner 'tools not supported' rejection.

        HF-specific patterns added beyond the OpenRouter set:
            - "tool use is not supported"
            - "tool_calls not supported on this model"
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
            "tool use is not supported",       # HF-specific
            "tool_calls not supported on this model",  # HF-specific
            "unsupported param: tools",        # TGI / llama-server
        )
        return any(ind in err_lower for ind in indicators)

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks (ARCH-01)
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """HF Router's chat completions endpoint."""
        return f"{self.base_url}/chat/completions"

    def _get_auth_headers(self) -> dict:
        """HF Router requires Bearer token. No HTTP-Referer/X-Title
        (HF doesn't have OpenRouter's leaderboard system)."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AgentKthx/0.x (+https://github.com/VTSTech/AgentKthx)",
        }

    def _iter_sse_lines(
        self,
        url: str,
        body: dict,
        headers: dict,
    ) -> Iterable[bytes]:
        """Make a streaming POST to HF Router's /chat/completions.

        ROB-04 (R06.56): uses stdlib ``urllib.request.urlopen`` (not
        ``requests``) to preserve the zero-dependency claim. The base
        class ``generate_completions_stream()`` parses these lines.

        ARCH-03 (R06.57): Context-length 400 recovery delegates to the
        shared ``_handle_context_length_400`` helper inherited from
        ``OpenAICompatibleBackend``. HF Router's error format matches
        the base class defaults (``"maximum context length is N
        tokens"`` + ``"N of text input"`` + ``"N of tool input"``),
        so no regex override is needed.
        """
        # Apply provider policy for streaming requests too
        if "model" in body:
            body = {**body, "model": _apply_provider_policy_live(self, body["model"])}

        for attempt in range(2):  # max 2 attempts (original + 1 retry)
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

                # ARCH-03 (R06.57): Shared context-length 400 handler
                if e.code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(error_body, body):
                        new_max = body["max_tokens"]
                        print(
                            f"  [HF-Stream] Context length exceeded — "
                            f"reducing max_tokens {old_max} → {new_max} "
                            f"and retrying"
                        )
                        continue

                # 402 streaming fallback (mirror _stream_request above)
                if e.code == 402 and not self._free_only_effective:
                    current = body.get("model", "")
                    fallback = _apply_provider_policy_live(self, HF_FREE_FALLBACK_MODEL)
                    if not self._is_free_model_live(current):
                        import sys
                        print(
                            f"\n  \033[33m[HF-Stream] Free-tier credit "
                            f"exhausted for '{current}' — falling back "
                            f"to '{fallback}'\033[0m",
                            file=sys.stderr,
                        )
                        fb_body = {**body, "model": fallback}
                        fb_req = urllib.request.Request(
                            url,
                            data=json.dumps(fb_body).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        try:
                            response = urllib.request.urlopen(
                                fb_req, timeout=self.config.timeout
                            )
                        except urllib.error.HTTPError as e2:
                            error_body2 = (
                                e2.read().decode("utf-8") if e2.fp else ""
                            )
                            raise RuntimeError(
                                f"Hugging Face HTTP error {e2.code} "
                                f"(stream 402 fallback): {error_body2}"
                            )
                    else:
                        raise RuntimeError(
                            f"Hugging Face free-tier credit exhausted "
                            f"(stream) and fallback is also a free model."
                        )
                    # If we got here via fallback path, fall through to
                    # the yield loop below with the new response object.

                # ReAct fallback on streaming 400
                if "does not support tools" in error_body.lower() and body.get("tools"):
                    import sys
                    print(
                        f"\n  \033[33m[HF-Stream] Model "
                        f"'{body.get('model')}' does not support tools "
                        f"— retrying without tool definitions\033[0m",
                        file=sys.stderr,
                    )
                    body_fb = {k: v for k, v in body.items() if k != "tools"}
                    body_fb.pop("tool_choice", None)
                    fb_req = urllib.request.Request(
                        url,
                        data=json.dumps(body_fb).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    try:
                        response = urllib.request.urlopen(
                            fb_req, timeout=self.config.timeout
                        )
                    except urllib.error.HTTPError as e2:
                        error_body2 = (
                            e2.read().decode("utf-8") if e2.fp else ""
                        )
                        raise RuntimeError(
                            f"Hugging Face HTTP error {e2.code} "
                            f"(stream no-tools fallback): {error_body2}"
                        )
                else:
                    raise RuntimeError(
                        f"Hugging Face HTTP error {e.code} (stream): {error_body}"
                    )
            except urllib.error.URLError as e:
                raise RuntimeError(f"Hugging Face connection error: {e.reason}")

            # ROB-06 (R06.57): try/finally so the urllib response is
            # closed deterministically when the generator is abandoned
            # mid-iteration.
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

    # _get_model_defaults() already exists above (uses _model_cache)
    # generate_completions_stream() is overridden below to enforce
    # HF_FREE_ONLY upfront (the agentic loop calls generate_completions_stream()
    # directly for cloud backends, bypassing the generate_stream() override).

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from HF Router.

        ARCH-01: Thin override that handles HF_FREE_ONLY upfront, then
        delegates to super().generate_completions_stream() (from
        OpenAICompatibleBackend) which uses _get_chat_completions_url(),
        _get_auth_headers(), _iter_sse_lines(), and
        _build_openai_body(stream=True).

        This override is necessary because the agentic loop calls
        generate_completions_stream() directly for cloud backends (not
        generate_stream()). Without this override, the inherited base-
        class version would skip the HF_FREE_ONLY whitelist check and
        make HTTP requests for non-whitelisted models — defeating the
        purpose of the env var (which is to prevent accidental paid
        API calls).
        """
        # HF_FREE_ONLY: reject non-whitelisted models upfront
        if self._free_only_effective and not self._is_free_model_live(model):
            raise RuntimeError(
                f"Model '{model}' is not in the Hugging Face free-tier "
                f"whitelist. Set HF_FREE_ONLY=false or pick a whitelisted "
                f"model. See HUGGINGFACE_API_TECHNICAL_REFERENCE.md."
            )

        # Apply provider policy (no-op if user already appended a suffix)
        routed_model = _apply_provider_policy_live(self, model)

        yield from super().generate_completions_stream(
            model=routed_model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from HF Router.

        ARCH-01: Thin override that handles HF_FREE_ONLY upfront, then
        delegates to the inherited ``generate_completions_stream()``
        (from OpenAICompatibleBackend) which uses _get_chat_completions_url(),
        _get_auth_headers(), _iter_sse_lines(), and _build_openai_body(stream=True).
        """
        # HF_FREE_ONLY: reject non-whitelisted models upfront
        if self._free_only_effective and not self._is_free_model_live(model):
            raise RuntimeError(
                f"Model '{model}' is not in the Hugging Face free-tier "
                f"whitelist. Set HF_FREE_ONLY=false or pick a whitelisted "
                f"model. See HUGGINGFACE_API_TECHNICAL_REFERENCE.md."
            )

        # Apply provider policy (no-op if user already appended a suffix)
        routed_model = _apply_provider_policy_live(self, model)

        for chunk in self.generate_completions_stream(
            model=routed_model,
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
    # Runtime context (mirrors OpenRouterBackend)
    # ─────────────────────────────────────────────────────────────────────

    def get_model_runtime_context(self, model: str) -> int:
        """Get the runtime context window size for a model.

        For cloud providers (HF Router), there's no separate "runtime"
        context — the context window is fixed by the model. This
        delegates to ``get_model_max_context()``.
        """
        return self.get_model_max_context(model)

    def __repr__(self) -> str:
        return (
            f"HuggingFaceBackend(base_url={self._base_url!r}, "
            f"api_mode={self._api_mode}, free_only={HF_FREE_ONLY})"
        )


# Module-level alias used inside list_models()'s fallback path. Defined
# here at the bottom (after HF_FREE_MODEL_WHITELIST is available) to keep
# the helper close to its sole caller.
def _is_free(model_id: str) -> bool:
    """Backwards-compat wrapper for the catalog fallback path."""
    return _is_free_model(model_id)
