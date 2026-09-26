"""
⚛️ AgentKthx — OpenRouter API Backend
Backend implementation for the OpenRouter API (OpenAI Chat-Completions compatible).

OpenRouter provides access to 500+ models from various providers (Anthropic, OpenAI, 
Google, Cohere, local models, etc.) via an OpenAI-compatible API endpoint.
This backend inherits the OpenAI Chat-Completions logic from OllamaBackend
and adds API key authentication and OpenRouter-specific defaults.

Endpoints used:
  - POST /chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /models          → model discovery (OpenAI-compatible)

Configuration:
  OPENROUTER_API_KEY    — API key for authentication (required)
  OPENROUTER_BASE_URL   — API base URL (default: https://openrouter.ai/api/v1)
  OPENROUTER_DEFAULT_MODEL — Default model (default: anthropic/claude-3.5-sonnet)

Usage:
  # CLI
  agentkthx chat --backend openrouter --model openai/gpt-4o
  agentkthx run "What is 15 * 8?" --backend openrouter --model deepseek/deepseek-chat

  # Python API
  from agentkthx import Agent
  agent = Agent(model="anthropic/claude-3.5-sonnet", backend="openrouter", tools=["calculator"])
  result = agent.run("What is 15 * 8?")

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Generator, Optional

from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode
from agentkthx.core.models import Tool, ToolParam
from agentkthx.config import OPENROUTER_BASE_URL, OPENROUTER_API_KEY, OPENROUTER_DEFAULT_MODEL, OPENROUTER_FREE_ONLY


# OpenRouter model catalog with metadata for context sizing and defaults.
# Keys are model identifiers accepted by the OpenRouter API.
# Context lengths sourced from https://openrouter.ai/docs#models
# The /models endpoint returns dynamic data, but this catalog ensures
# common models are always available with proper defaults.
# Updated: 2026-04-15
OPENROUTER_MODELS: dict[str, dict] = {
    # Anthropic
    "anthropic/claude-3.5-sonnet": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 15.00,  # $ per 1M tokens
            "completion": 75.00
        },
        "context_length": 131072,
        "provider": "anthropic",
        "description": "Claude 3.5 Sonnet - Fast, intelligent, and accurate"
    },
    "anthropic/claude-3.5-haiku": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 1.00,
            "completion": 5.00
        },
        "context_length": 131072,
        "provider": "anthropic",
        "description": "Claude 3.5 Haiku - Fast and cost-effective"
    },
    "anthropic/claude-3-opus": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 15.00,
            "completion": 75.00
        },
        "context_length": 131072,
        "provider": "anthropic",
        "description": "Claude 3 Opus - Most powerful model"
    },
    "anthropic/claude-3-haiku": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 0.25,
            "completion": 1.25
        },
        "context_length": 131072,
        "provider": "anthropic",
        "description": "Claude 3 Haiku - Fast and lightweight"
    },
    
    # OpenAI
    "openai/gpt-4o": {
        "max_tokens": 128000,
        "pricing": {
            "prompt": 2.50,
            "completion": 10.00
        },
        "context_length": 128000,
        "provider": "openai",
        "description": "GPT-4o - multimodal model"
    },
    "openai/gpt-4o-mini": {
        "max_tokens": 128000,
        "pricing": {
            "prompt": 0.15,
            "completion": 0.60
        },
        "context_length": 128000,
        "provider": "openai",
        "description": "GPT-4o mini - fast and affordable"
    },
    "openai/gpt-4-turbo": {
        "max_tokens": 128000,
        "pricing": {
            "prompt": 10.00,
            "completion": 30.00
        },
        "context_length": 128000,
        "provider": "openai",
        "description": "GPT-4 Turbo - previous flagship"
    },
    "openai/gpt-4": {
        "max_tokens": 8192,
        "pricing": {
            "prompt": 30.00,
            "completion": 60.00
        },
        "context_length": 8192,
        "provider": "openai",
        "description": "GPT-4 - legacy model"
    },
    
    # DeepSeek
    "deepseek/deepseek-chat": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 1.00,
            "completion": 2.00
        },
        "context_length": 131072,
        "provider": "deepseek",
        "description": "DeepSeek Chat - open-source model"
    },
    "deepseek/deepseek-coder": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 1.00,
            "completion": 2.00
        },
        "context_length": 131072,
        "provider": "deepseek",
        "description": "DeepSeek Coder - programming model"
    },
    
    # Google
    "google/gemini-2.0-flash-exp": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 0.15,
            "completion": 0.60
        },
        "context_length": 131072,
        "provider": "google",
        "description": "Gemini 2.0 Flash Experimental"
    },
    "google/gemini-1.5-flash": {
        "max_tokens": 2097152,
        "pricing": {
            "prompt": 0.075,
            "completion": 0.30
        },
        "context_length": 2097152,
        "provider": "google",
        "description": "Gemini 1.5 Flash - long context"
    },
    "google/gemini-1.5-pro": {
        "max_tokens": 2097152,
        "pricing": {
            "prompt": 12.50,
            "completion": 50.00
        },
        "context_length": 2097152,
        "provider": "google",
        "description": "Gemini 1.5 Pro - flagship model"
    },
    
    # Cohere
    "cohere/command-r-plus": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 3.00,
            "completion": 15.00
        },
        "context_length": 131072,
        "provider": "cohere",
        "description": "Command R Plus - powerful assistant"
    },
    "cohere/command-r": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 0.50,
            "completion": 1.50
        },
        "context_length": 131072,
        "provider": "cohere",
        "description": "Command R - balanced performance"
    },
    
    # Local models (via OpenRouter)
    "meta-llama/llama-3.1-70b-instruct": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 0.88,
            "completion": 0.88
        },
        "context_length": 131072,
        "provider": "meta",
        "description": "Llama 3.1 70B Instruct"
    },
    "mistralai/mixtral-8x7b-instruct": {
        "max_tokens": 32768,
        "pricing": {
            "prompt": 0.50,
            "completion": 0.50
        },
        "context_length": 32768,
        "provider": "mistral",
        "description": "Mixtral 8x7B Instruct"
    },
    "qwen/qwen-2.5-72b-instruct": {
        "max_tokens": 131072,
        "pricing": {
            "prompt": 0.50,
            "completion": 0.50
        },
        "context_length": 131072,
        "provider": "qwen",
        "description": "Qwen 2.5 72B Instruct"
    },
    

}


class OpenRouterBackend(OpenAICompatibleBackend):
    """
    Backend for OpenRouter cloud API.
    
    OpenRouter provides access to 500+ models via an OpenAI-compatible API.
    This backend extends OllamaBackend's OpenAI Chat-Completions support
    with OpenRouter-specific authentication and model handling.
    """
    
    # Model cache with 1-hour timeout
    _model_cache = None
    _cache_time = 0
    _CACHE_TIMEOUT = 3600  # 1 hour in seconds

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENAI,
    ):
        # Determine base URL - priority: base_url > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        else:
            resolved_url = OPENROUTER_BASE_URL.rstrip("/")

        # Set API mode. OpenRouter only exposes the OpenAI Chat-Completions
        # endpoint, but JEV mode is accepted because it uses the same wire
        # format under the hood (JEV is a wrapper that calls OpenAI
        # chat-completions underneath).
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode == ApiMode.JEV:
            # accepted — _jev_call_completions routes through generate()
            pass
        elif api_mode == ApiMode.OPENAI:
            pass
        else:
            raise ValueError(
                "OpenRouter backend only supports OpenAI Chat-Completions "
                "or JEV (System-One) API modes"
            )

        # Call parent with shared state
        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # API key is lazy-loaded - only required for generation, not for listing models
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")

        # ROB-06: Persisted safe max_tokens after a context-length 400.
        # When set, _get_model_defaults() returns this instead of the
        # model's reported max_completion_tokens. Set to None initially.
        self._context_safe_max_tokens: int | None = None

        # Set headers for OpenRouter
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
            "X-Title": "AgentKthx"
        }
        
        # Force model list to be loaded on initialization so cache is populated
        try:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print("  [OpenRouter Debug] Initializing: loading models into cache")
            self.list_models()
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenRouter Debug] Failed to initialize models: {e}")

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OPENROUTER

    @property
    def base_url(self) -> str:
        return self._base_url

    # api_mode property/setter is inherited from OpenAICompatibleBackend (ARCH-01)

    def _parse_openrouter_model(self, model_data: dict) -> dict:
        """
        Parse OpenRouter API model data into AgentKthx format.
        
        Uses live API data for context length and max tokens instead of static catalog.

        R07.05: sets ``free_tier=True`` when the model ID ends with ``:free``
        OR when the API response's ``is_free`` field is ``True``. The
        ``:free`` suffix is OpenRouter's canonical marker for genuinely-free
        models (the upstream's per-token rate is $0). The ``is_free`` API
        field is conservative (only True for some models), so we OR the two
        signals — if EITHER says free, mark it free. This fixes the
        ``/models free`` filter showing ``:free``-suffix models as ``paid``.
        Also sets ``is_chat_model=True`` by default (OpenRouter lists only
        chat-capable models on the OpenAI-compatible endpoint).
        """
        model_id = model_data["id"]
        
        # Get context length and max tokens from live API data
        context_length = model_data.get("context_length", 128000)
        max_completion_tokens = model_data.get("top_provider", {}).get("max_completion_tokens", 4096)
        
        # Determine family from provider or model name
        provider = model_data.get("top_provider", {}).get("provider", model_data.get("id", "/").split("/")[0])
        family = provider

        # R07.05: detect free-tier models. OpenRouter marks genuinely-free
        # models with the ``:free`` suffix on the model ID (e.g.
        # ``google/gemma-3-27b-it:free``). The API response also has an
        # ``is_free`` boolean field, but it's conservative — only True for
        # a subset of free models. We OR the two signals so ``/models free``
        # shows ALL genuinely-free models, not just the ones OpenRouter
        # flags with is_free=True.
        is_free_suffix = model_id.endswith(":free")
        is_free_api = bool(model_data.get("is_free", False))
        free_tier = is_free_suffix or is_free_api

        # R07.05: OpenRouter's /v1/models endpoint lists only chat-capable
        # models on the OpenAI-compatible surface. Non-chat models
        # (embeddings, image gen, audio) are not returned here. Default
        # is_chat_model=True; override via the ``modality`` field if present.
        modality = model_data.get("modality", "text")
        is_chat_model = "text" in modality if isinstance(modality, str) else True

        # Capture pricing for display/debug (OpenRouter returns per-token USD)
        pricing = model_data.get("pricing", {})
        prompt_price = float(pricing.get("prompt", 0) or 0)
        completion_price = float(pricing.get("completion", 0) or 0)
        # A model is genuinely free if both prompt and completion are $0
        is_zero_pricing = (prompt_price == 0.0 and completion_price == 0.0)
        if is_zero_pricing:
            free_tier = True  # pricing is the ground truth — overrides is_free
        
        return {
            "name": model_id,
            "size": 0,  # OpenRouter doesn't provide size info
            "details": {
                "family": family,
                "backend": "openrouter",
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
                "free_tier": free_tier,
                "is_chat_model": is_chat_model,
                "is_free_suffix": is_free_suffix,
                "is_free_api": is_free_api,
                "is_zero_pricing": is_zero_pricing,
                "pricing": {
                    "prompt": prompt_price,
                    "completion": completion_price,
                },
            },
            "model_data": model_data  # Store original data for future reference
        }
    
    def list_models(self) -> list[dict]:
        """List available models from OpenRouter API with caching.
        
        Cache timeout: 1 hour (3600 seconds)
        Refresh endpoint: GET /v1/models (automatic refresh when cache expires)
        """
        import time
        
        # Check cache first
        current_time = time.time()
        if (self._model_cache is not None and 
            current_time - self._cache_time < self._CACHE_TIMEOUT):
            return self._model_cache
        
        try:
            # Use proper headers for API call
            headers = {
                "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
                "X-Title": "AgentKthx"
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers=headers,
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                models_data = json.loads(resp.read().decode("utf-8"))
            available_models = []
            
            # Parse API response using live data
            for model in models_data.get("data", []):
                model_id = model.get("id")
                if model_id:
                    # Use live API data instead of static catalog
                    parsed_model = self._parse_openrouter_model(model)
                    available_models.append(parsed_model)
            
            # Add catalog-only models (not returned by API)
            catalog_models = list(OPENROUTER_MODELS.keys())
            for name in catalog_models:
                if not any(m["name"] == name for m in available_models):
                    model_info = OPENROUTER_MODELS[name]
                    available_models.append({
                        "name": name,
                        "size": 0,
                        "details": {
                            "family": model_info.get("provider", "unknown"),
                            "backend": "openrouter",
                            "context_length": model_info.get("context_length", 128000),
                        }
                    })
            
            # Filter models if OPENROUTER_FREE_ONLY is enabled
            if OPENROUTER_FREE_ONLY:
                # OpenRouter free models have :free suffix at the end
                free_models = [m for m in available_models if m["name"].endswith(":free")]
                self._model_cache = sorted(free_models, key=lambda x: x["name"])
            else:
                self._model_cache = sorted(available_models, key=lambda x: x["name"])
            
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenRouter Debug] Stored {len(self._model_cache)} models in cache:")
                for model in self._model_cache:
                    print(f"    - {model['name']}")
            
            self._cache_time = current_time
            return self._model_cache
            
        except Exception as e:
            # Fallback to catalog if API fails
            catalog_models = []
            for name, model_info in OPENROUTER_MODELS.items():
                # Create mock model data for fallback
                mock_model_data = {
                    "id": name,
                    "context_length": model_info.get("context_length", 128000),
                    "top_provider": {
                        "max_completion_tokens": model_info.get("max_tokens", 4096)
                    }
                }
                parsed_model = self._parse_openrouter_model(mock_model_data)
                catalog_models.append(parsed_model)
            
            if OPENROUTER_FREE_ONLY:
                free_models = [m for m in catalog_models 
                             if "free" in m["name"].lower() or 
                             any(free in m["name"].lower() for free in ["flash", "mini", "haiku", "tiny"])]
                self._model_cache = sorted(free_models, key=lambda x: x["name"])
            else:
                self._model_cache = sorted(catalog_models, key=lambda x: x["name"])
            
            self._cache_time = current_time
            return self._model_cache

    def is_running(self) -> bool:
        """OpenRouter is a cloud API, so it's always 'running'."""
        return True
    
    def _get_model_info(self, model_name: str) -> dict | None:
        """Get model metadata from catalog, cache, or API."""
        # Check catalog first
        if model_name in OPENROUTER_MODELS:
            return OPENROUTER_MODELS[model_name]
        
        # Check cache if available
        if self._model_cache:
            for cached_model in self._model_cache:
                if cached_model["name"] == model_name:
                    # Return a dict compatible with the catalog format
                    details = cached_model["details"]
                    return {
                        "max_tokens": details.get("max_completion_tokens", 4096),
                        "context_length": details.get("context_length", 128000),
                    }
        
        # Try to get from API (future enhancement)
        # For now, return None to let OllamaBackend handle defaults
        return None

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """
        Get the model's maximum trained context window size.
        
        Uses live OpenRouter API data for accurate context lengths.
        """
        # Try to get model from cache first
        if self._model_cache:
            for cached_model in self._model_cache:
                if cached_model["name"] == model:
                    return cached_model["details"].get("context_length", 128000)
        
        # Fallback to catalog if not in cache
        model_info = self._get_model_info(model)
        if model_info and "context_length" in model_info:
            return model_info["context_length"]
        
        # Fallback to family-based defaults from OllamaBackend
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx
        
        # Default fallback
        return 128000

    def _get_model_defaults(self, model: str) -> dict:
        """
        Get model-specific defaults from live API data.
        
        Returns:
            dict: temperature, max_tokens, and other model defaults
        """
        # Try to get model from cache first
        if self._model_cache:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [OpenRouter Debug] Looking for model '{model}' in cache with {len(self._model_cache)} models")
                for cached_model in self._model_cache:
                    cached_name = cached_model["name"]
                    print(f"    Cache entry: '{cached_name}'")
            for cached_model in self._model_cache:
                cached_name = cached_model["name"]
                if cached_name == model:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(f"  [OpenRouter Debug] Found exact match: '{cached_name}'")
                    details = cached_model["details"]
                    max_tokens = details.get("max_completion_tokens", 4096)
                    context_length = details.get("context_length", 128000)
                    # ARCH-03 (R06.57): cap + persisted-safe-value logic now
                    # inherited from OpenAICompatibleBackend._apply_max_tokens_cap
                    return self._apply_max_tokens_cap(
                        max_tokens, context_length, temperature=0.7
                    )
                elif model in cached_name or cached_name in model:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(f"  [OpenRouter Debug] Partial match: '{cached_name}' (searching for '{model}')")

        # Fallback to catalog if not in cache
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenRouter Debug] Model not found in cache, falling back to catalog")
        model_info = self._get_model_info(model)

        max_tokens = model_info.get("max_tokens", 4096) if model_info else 4096
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenRouter Debug] Catalog max_tokens: {max_tokens}")

        context_length = model_info.get("context_length", 128000) if model_info else 128000
        # ARCH-03 (R06.57): cap + persisted-safe-value logic via shared helper.
        # Note: catalog fallback also gets the cap — previously it returned the
        # raw max_tokens without capping, which was inconsistent with the cache
        # hit path. Now both paths cap consistently.
        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=0.7
        )

    # R06.54: maximum retries for rate-limit (429) and transient server
    # (502/503/504) responses before giving up. Free-tier models on
    # OpenRouter return 429 "Provider returned error" constantly — 3 quick
    # retries (~30 s of patience) were never enough for an agentic run, so
    # the run died mid-audit. Override with OPENROUTER_MAX_429_RETRIES.
    _MAX_429_RETRIES = 6

    # Exponential back-off schedule (seconds) when no usable Retry-After
    # header is present. Capped so a broken provider can't hang the agent.
    _429_BACKOFF_BASE = 5.0
    _429_BACKOFF_CAP = 90.0

    def _max_429_retries(self) -> int:
        """Resolve the 429 retry budget (env override > class default)."""
        raw = os.environ.get("OPENROUTER_MAX_429_RETRIES", "")
        try:
            val = int(raw)
            if val >= 0:
                return val
        except (ValueError, TypeError):
            pass
        return self._MAX_429_RETRIES

    def _429_backoff(self, attempt: int) -> float:
        """Back-off wait for the Nth (1-based) rate-limit retry."""
        import random
        delay = self._429_BACKOFF_BASE * (2 ** max(0, attempt - 1))
        delay = min(delay, self._429_BACKOFF_CAP)
        jitter = delay * 0.2
        return max(1.0, delay + random.uniform(-jitter, jitter))

    def _make_api_request(self, endpoint: str, data: dict, stream: bool = False) -> dict | Generator:
        """Make request to OpenRouter API with automatic 429/5xx retry.

        On HTTP 429 (rate limit) or transient server errors (502/503/504),
        waits — honoring the `Retry-After` header when present, otherwise
        an exponential back-off schedule — and retries up to
        `_max_429_retries()` times (R06.54: default 6, was 3). Other errors
        are normalized to RuntimeError carrying the upstream error message
        so callers can pattern-match on the text (e.g. to detect "does not
        support tools" for the ReAct fallback path).
        """
        url = f"{self.base_url}/{endpoint}"

        # Lazy API key check - only required for actual API calls
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required for API calls")

        # Update headers with API key if available
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
            "X-Title": "AgentKthx"
        }

        if stream:
            return self._stream_request(url, data, headers)

        # Retry loop for 429 rate-limit and transient 5xx responses.
        # OpenRouter sends 429 (with an optional Retry-After header) when the
        # upstream provider is rate-limited, and 502/503/504 when the provider
        # itself errors out — both are routine on :free models and both are
        # worth waiting out (R06.54). We honor Retry-After and fall back to an
        # exponential schedule so the agent outlives provider hiccups instead
        # of dying mid-run.
        #
        # ROB-04 (R06.56): rewritten from `requests` to stdlib
        # `urllib.request` to preserve the zero-dependency claim. urllib
        # raises HTTPError on non-2xx status, so we catch it and extract
        # status / headers / body the same way requests gave us
        # .status_code / .headers / .json() / .text.
        max_retries = self._max_429_retries()
        last_retryable_error = None
        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                    # Success — read and parse the JSON body
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                status_code = e.code
                # Read body once — e.fp can only be consumed once
                body_bytes = e.read() if e.fp else b""
                body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
                # Parse JSON body if possible (for error messages)
                err_data = None
                try:
                    if body_text:
                        err_data = json.loads(body_text)
                except (json.JSONDecodeError, ValueError):
                    pass

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
                            error_msg = (inner.get("message", inner)
                                         if isinstance(inner, dict) else str(inner))
                        elif "message" in err_data:
                            error_msg = err_data["message"]
                    last_retryable_error = error_msg

                    # Honor Retry-After when parseable; otherwise back off
                    # exponentially (5s → 10s → 20s → 40s → 80s → 90s cap).
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
                        # R06.54: always visible — the user must SEE that the
                        # harness is patiently waiting instead of silently dying.
                        print(f"  [OpenRouter] {status_code} — {error_msg}. "
                              f"Retrying in {retry_after:.0f}s "
                              f"(attempt {attempt + 1}/{max_retries + 1})...")
                        time.sleep(retry_after)
                        continue
                    else:
                        # Exhausted retries — raise the error.
                        raise RuntimeError(
                            f"OpenRouter rate limit: {error_msg}. "
                            f"Retried {max_retries} times. "
                            f"Try again in {retry_after:.0f} seconds."
                        )

                # ---- 401 Auth error ----
                if status_code == 401:
                    raise RuntimeError(
                        "OpenRouter authentication failed. Please check your "
                        "OPENROUTER_API_KEY environment variable."
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
                        f"OpenRouter API error {status_code}: {upstream_msg}"
                    )

            except urllib.error.URLError as e:
                # Network-level error (DNS, connection refused, timeout)
                raise RuntimeError(f"OpenRouter connection error: {e.reason}")

    def _stream_request(self, url: str, data: dict, headers: dict) -> Generator[dict, None, None]:
        """Handle streaming requests (ROB-04: stdlib urllib, not requests).

        ROB-06 (R06.57): try/finally so the urllib response is closed
        deterministically when the generator is abandoned mid-iteration.
        """
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
            raise RuntimeError(f"OpenRouter HTTP error {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenRouter connection error: {e.reason}")

        try:
            for line in response:
                if line:
                    line_str = line.decode("utf-8")
                    if line_str.startswith("data: "):
                        json_str = line_str[6:]
                        if json_str.strip() == "[DONE]":
                            continue
                        try:
                            chunk = json.loads(json_str)
                            yield chunk
                        except json.JSONDecodeError:
                            continue
        finally:
            try:
                response.close()
            except Exception:
                pass

    def test_tool_support(
        self,
        model: str,
        family: str | None = None,
        force_test: bool = False,
    ) -> ToolSupportLevel:
        """Test tool support for a model via OpenRouter API.

        OpenRouter is a cloud aggregator that only exposes models which
        already support native function calling on their underlying
        provider. We therefore assume NATIVE for every model without
        probing — no live API call is made.

        The actual generate() path keeps a defensive ReAct fallback for
        the rare case where a specific free / fine-tuned model rejects
        the `tools` field at runtime (HTTP 400), so text-format tool
        calls can still flow through the Agent's ToolParser.

        Args:
            model: OpenRouter model id (e.g. "openai/gpt-4o")
            family: Optional family hint (unused, kept for API compat)
            force_test: Ignored — kept for API compatibility with other backends

        Returns:
            ToolSupportLevel.NATIVE for every model.
        """
        return ToolSupportLevel.NATIVE

    # _build_openai_body() is inherited from OpenAICompatibleBackend (ARCH-01)

    # _parse_openai_response() is inherited from OpenAICompatibleBackend (ARCH-01)

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response using OpenRouter's Chat Completions API.

        Implements the OpenAI Chat Completions spec for OpenRouter, with
        native tool-calling support and automatic ReAct fallback when the
        provider rejects the `tools` field (many free models do).

        Fallback behaviour:
        - If OpenRouter returns HTTP 400 with a "does not support tools"
          message, the request is retried once WITHOUT the `tools` field,
          so the model can fall back to text-based (ReAct) tool calls that
          the Agent's ToolParser can still parse from `content`.

        Args:
            model: OpenRouter model id (e.g. "openai/gpt-4o")
            messages: Chat messages in OpenAI format
            tools: Optional list of Tool objects for native function calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Optional OpenAI params — top_p, stop,
                presence_penalty, frequency_penalty, response_format,
                tool_choice, etc.

        Returns:
            Dict with keys: content, tool_calls, finish_reason, usage,
            latency_ms, raw.
        """
        # JEV dispatch — if api_mode is JEV, route through generate_decision()
        # which wraps the underlying LLM call with a decision prompt.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else 8192,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # Use model defaults from catalog if not specified
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]

        body = self._build_openai_body(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenRouter] POST chat/completions — "
                  f"tools={len(tools) if tools else 0}, "
                  f"tool_choice={kwargs.get('tool_choice', 'auto')}")

        start_time = time.time()
        try:
            raw_response = self._make_api_request("chat/completions", body)
        except RuntimeError as e:
            err_str = str(e)
            # ReAct fallback: many :free / fine-tuned models on OpenRouter
            # reject the `tools` field. Retry without it so the model can
            # emit text-format tool calls that the ToolParser handles.
            if tools and self._is_tools_not_supported_error(err_str):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [OpenRouter] Model doesn't support tools — "
                          f"retrying without tools (ReAct fallback)")
                body.pop("tools", None)
                body.pop("tool_choice", None)
                raw_response = self._make_api_request("chat/completions", body)
            # ROB-06: Context-length 400 — the model's reported
            # max_completion_tokens was too large for the actual input.
            # Dynamically reduce max_tokens and retry once. The error
            # message usually contains "maximum context length" and the
            # token breakdown.
            elif "maximum context length" in err_str.lower() or "context length" in err_str.lower():
                # Estimate a safe max_tokens: use 1/3 of the current value
                # or 4096, whichever is larger. This is conservative but
                # prevents the death-spiral where the agent keeps re-sending
                # the same oversized request.
                old_max = body.get("max_tokens", 4096)
                new_max = max(old_max // 3, 4096)
                if new_max < old_max:
                    print(f"  [OpenRouter] Context length exceeded — "
                          f"reducing max_tokens {old_max} → {new_max} and retrying")
                    body["max_tokens"] = new_max
                    raw_response = self._make_api_request("chat/completions", body)
                else:
                    raise RuntimeError(f"OpenRouter API error: {err_str}")
            else:
                raise RuntimeError(f"OpenRouter API error: {err_str}")

        latency_ms = (time.time() - start_time) * 1000
        parsed = self._parse_openai_response(raw_response)
        parsed["latency_ms"] = latency_ms

        # Synthesize a finish_reason if the API omitted one (some providers do)
        if parsed["finish_reason"] is None:
            if parsed["tool_calls"]:
                parsed["finish_reason"] = "tool_calls"
            elif not parsed["content"]:
                parsed["finish_reason"] = "stop"
            else:
                parsed["finish_reason"] = "stop"

        # Detect empty responses — model returned no content AND no
        # tool_calls. This usually means the provider silently failed
        # (rate limit, content filter, or the model just returned
        # whitespace). Surface it as an error so the chat loop can show
        # the user something went wrong instead of a blank "AgentKthx: ".
        if not parsed["content"].strip() and not parsed["tool_calls"]:
            raise RuntimeError(
                "OpenRouter returned an empty response (no content, no tool_calls). "
                "This may be a rate limit, content filter, or model issue. "
                f"finish_reason={parsed['finish_reason']}"
            )

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenRouter] finish_reason={parsed['finish_reason']}, "
                  f"tool_calls={len(parsed['tool_calls'])}, "
                  f"content_len={len(parsed['content'])}")

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
        """
        JEV hook for OpenRouter: route the decision call through
        OpenRouter's /chat/completions endpoint with full auth,
        429 retry, and OPENROUTER_FREE_ONLY handling — all of which
        live in self.generate().

        ZAI / Ollama / llama-server backends override this same hook to
        route through their own auth-injected path. The parent
        OllamaBackend.generate_decision() handles the JEV wrapper
        (prompt building, JSON parsing, alternatives, etc.).

        The response shape returned by self.generate() already matches
        what generate_decision() expects:
            {content, tool_calls, usage, latency_ms, raw, finish_reason}
        """
        # Decisions never carry tools — pass tools=None explicitly so
        # the ReAct fallback path in self.generate() doesn't trigger.
        # NOTE: We intentionally do NOT pass response_format to OpenRouter
        # here. Many free models (e.g. poolside/laguna-xs-2.1:free) silently
        # return empty content when response_format={"type":"json_object"}
        # is forced — they don't support JSON mode and OpenRouter doesn't
        # error, just returns finish_reason=stop with no content.
        # The JEV System-One prompt already instructs the model to output
        # JSON-only, so response_format is redundant. If the first attempt
        # returns empty, we retry without it (belt-and-suspenders).
        kwargs.pop("response_format", None)  # strip it — prompt handles JSON

        # OPENROUTER_FREE_ONLY is handled inside list_models() (the model
        # cache is pre-filtered to :free models). If the user passes a
        # paid model name, self.generate() will still attempt the call;
        # OpenRouter will respond with a 429 or paid-tier error.
        # We don't silently swap models here — the user picked the model.

        # CRITICAL: Temporarily flip api_mode to OPENAI to avoid infinite
        # recursion. self.generate() calls _maybe_jev_dispatch() at the top,
        # which would call generate_decision() → _jev_call_completions() →
        # self.generate() again. By flipping to OPENAI, _maybe_jev_dispatch()
        # returns None and we proceed to the actual API call.
        original_api_mode = self._api_mode
        from agentkthx.core.types import ApiMode
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
                # If we get an empty response, it might be because the model
                # doesn't support response_format (even though we stripped it
                # above, some models still struggle). Retry with a simpler
                # prompt — just the last user message as state, no system prompt.
                err_lower = str(e).lower()
                if "empty response" in err_lower or "no content" in err_lower:
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(f"  [OpenRouter.JEV] Empty response — retrying with simplified prompt")
                    # Simplify: strip the JEV system prompt, just send raw
                    simplified_messages = [
                        {"role": "user", "content": messages[-1]["content"] if messages else ""}
                    ]
                    return self.generate(
                        model=model,
                        messages=simplified_messages,
                        tools=None,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                raise
        finally:
            # Restore original api_mode (JEV) so subsequent generate() calls
            # from the agent loop still dispatch to JEV mode.
            self._api_mode = original_api_mode

    @staticmethod
    def _is_tools_not_supported_error(err_str: str) -> bool:
        """Detect OpenRouter / upstream 'tools not supported' rejection."""
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

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks (ARCH-01)
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """OpenRouter's chat completions endpoint."""
        return f"{self.base_url}/chat/completions"

    def _get_auth_headers(self) -> dict:
        """OpenRouter requires Bearer token + HTTP-Referer + X-Title headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
            "X-Title": "AgentKthx",
        }

    def _iter_sse_lines(self, url: str, body: dict, headers: dict):
        """Make a streaming POST to OpenRouter's /chat/completions.

        ROB-04 (R06.56): uses stdlib `urllib.request.urlopen` (not `requests`)
        to preserve the zero-dependency claim. The base class
        `generate_completions_stream()` parses these lines.

        ARCH-03 (R06.57): Context-length 400 recovery now delegates to the
        shared ``_handle_context_length_400`` helper inherited from
        ``OpenAICompatibleBackend``. OpenRouter's error format matches the
        base class defaults (``"maximum context length is N tokens"`` +
        ``"N of text input"`` + ``"N of tool input"``), so no regex
        override is needed.
        """
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
                # ARCH-03 (R06.57): shared context-length 400 handler
                if e.code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(error_body, body):
                        new_max = body["max_tokens"]
                        print(f"  [OpenRouter-Stream] Context length exceeded — "
                              f"reducing max_tokens {old_max} → {new_max} and retrying")
                        continue
                raise RuntimeError(f"OpenRouter HTTP error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"OpenRouter connection error: {e.reason}")

            # ROB-06 (R06.57): try/finally so the urllib response is
            # closed deterministically when the generator is abandoned
            # mid-iteration (Ctrl+C per ROB-05, an exception inside the
            # consumer, or the base-class break on [DONE]). Mirrors
            # ZaiBackend._iter_sse_lines at zai.py:705-712.
            try:
                for line in response:
                    yield line
            finally:
                try:
                    response.close()
                except Exception:
                    pass
            return  # success — don't retry

    # ARCH-03 (R06.57): ``_calculate_safe_max_tokens`` was here — now
    # inherited from ``OpenAICompatibleBackend``. OpenRouter's error format
    # matches the base class default regex patterns, so no override needed.

    # _get_model_defaults() already exists on OpenRouterBackend (uses _model_cache)
    # generate_completions_stream() is inherited from OpenAICompatibleBackend

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from OpenRouter.

        ARCH-01: Previously inherited from OllamaBackend. Now delegates
        to the inherited ``generate_completions_stream()`` (from
        OpenAICompatibleBackend) and yields just the text deltas.
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
