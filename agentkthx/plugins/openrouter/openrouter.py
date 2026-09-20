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
  agentnova chat --backend openrouter --model openai/gpt-4o
  agentnova run "What is 15 * 8?" --backend openrouter --model deepseek/deepseek-chat

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
from typing import Any, Generator, Optional

import requests
from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.backends.ollama import OllamaBackend
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


class OpenRouterBackend(OllamaBackend):
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

        # Set headers for OpenRouter
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
            "X-Title": "AgentKthx"
        }
        
        # Force model list to be loaded on initialization so cache is populated
        try:
            if os.environ.get("AGENTNOVA_DEBUG"):
                print("  [OpenRouter Debug] Initializing: loading models into cache")
            self.list_models()
        except Exception as e:
            if os.environ.get("AGENTNOVA_DEBUG"):
                print(f"  [OpenRouter Debug] Failed to initialize models: {e}")

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OPENROUTER

    @property
    def base_url(self) -> str:
        return self._base_url
    
    @property
    def api_mode(self) -> ApiMode:
        return self._api_mode

    def _parse_openrouter_model(self, model_data: dict) -> dict:
        """
        Parse OpenRouter API model data into AgentKthx format.
        
        Uses live API data for context length and max tokens instead of static catalog.
        """
        model_id = model_data["id"]
        
        # Get context length and max tokens from live API data
        context_length = model_data.get("context_length", 128000)
        max_completion_tokens = model_data.get("top_provider", {}).get("max_completion_tokens", 4096)
        
        # Determine family from provider or model name
        provider = model_data.get("top_provider", {}).get("provider", model_data.get("id", "/").split("/")[0])
        family = provider
        
        return {
            "name": model_id,
            "size": 0,  # OpenRouter doesn't provide size info
            "details": {
                "family": family,
                "backend": "openrouter",
                "context_length": context_length,
                "max_completion_tokens": max_completion_tokens,
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
            
            response = requests.get(
                f"{self.base_url}/models",
                headers=headers,
                timeout=10  # Shorter timeout for model listing
            )
            response.raise_for_status()
            
            models_data = response.json()
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
            
            if os.environ.get("AGENTNOVA_DEBUG"):
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
            if os.environ.get("AGENTNOVA_DEBUG"):
                print(f"  [OpenRouter Debug] Looking for model '{model}' in cache with {len(self._model_cache)} models")
                for cached_model in self._model_cache:
                    cached_name = cached_model["name"]
                    print(f"    Cache entry: '{cached_name}'")
            for cached_model in self._model_cache:
                cached_name = cached_model["name"]
                if cached_name == model:
                    if os.environ.get("AGENTNOVA_DEBUG"):
                        print(f"  [OpenRouter Debug] Found exact match: '{cached_name}'")
                    details = cached_model["details"]
                    max_tokens = details.get("max_completion_tokens", 4096)
                    if os.environ.get("AGENTNOVA_DEBUG"):
                        print(f"  [OpenRouter Debug] Using max_tokens: {max_tokens}")
                    return {
                        "temperature": 0.7,  # Default temperature
                        "max_tokens": max_tokens,
                        "context_length": details.get("context_length", 128000),
                    }
                elif model in cached_name or cached_name in model:
                    if os.environ.get("AGENTNOVA_DEBUG"):
                        print(f"  [OpenRouter Debug] Partial match: '{cached_name}' (searching for '{model}')")
        
        # Fallback to catalog if not in cache
        if os.environ.get("AGENTNOVA_DEBUG"):
            print(f"  [OpenRouter Debug] Model not found in cache, falling back to catalog")
        model_info = self._get_model_info(model)
        
        max_tokens = model_info.get("max_tokens", 4096) if model_info else 4096
        if os.environ.get("AGENTNOVA_DEBUG"):
            print(f"  [OpenRouter Debug] Catalog max_tokens: {max_tokens}")
        
        defaults = {
            "temperature": 0.7,  # Default temperature
            "max_tokens": max_tokens,
            "context_length": model_info.get("context_length", 128000) if model_info else 128000,
        }
        
        return defaults

    # Maximum retries for 429 rate-limit responses before giving up.
    _MAX_429_RETRIES = 3

    def _make_api_request(self, endpoint: str, data: dict, stream: bool = False) -> dict | Generator:
        """Make request to OpenRouter API with automatic 429 retry.

        On HTTP 429 (rate limit), reads the `Retry-After` header and waits
        the requested duration (capped at 60s) before retrying. Retries up
        to `_MAX_429_RETRIES` times. Other errors are normalized to
        RuntimeError carrying the upstream error message so callers can
        pattern-match on the text (e.g. to detect "does not support
        tools" for the ReAct fallback path).
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

        # Retry loop for 429 rate-limit responses.
        # OpenRouter sends 429 with a Retry-After header (seconds) when
        # the upstream provider is rate-limited. We honor it and retry
        # automatically so the user sees fewer "empty response" errors.
        last_429_error = None
        for attempt in range(self._MAX_429_RETRIES + 1):
            response = requests.post(
                url,
                json=data,
                headers=headers,
                timeout=self.config.timeout
            )

            # ---- 429 Rate Limit: wait and retry ----
            if response.status_code == 429:
                error_msg = "Rate limit exceeded"
                retry_after_raw = response.headers.get("Retry-After", "10")
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        inner = error_data["error"]
                        error_msg = (inner.get("message", inner)
                                     if isinstance(inner, dict) else str(inner))
                    elif "message" in error_data:
                        error_msg = error_data["message"]
                except Exception:
                    pass
                last_429_error = error_msg

                # Parse Retry-After (seconds). Cap at 60s so we don't hang forever.
                try:
                    retry_after = int(retry_after_raw)
                except (ValueError, TypeError):
                    retry_after = 10
                retry_after = min(max(retry_after, 1), 60)

                if attempt < self._MAX_429_RETRIES:
                    if os.environ.get("AGENTNOVA_DEBUG"):
                        print(f"  [OpenRouter] 429 rate limited "
                              f"(attempt {attempt + 1}/{self._MAX_429_RETRIES + 1}): "
                              f"{error_msg}. Retrying in {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                else:
                    # Exhausted retries — raise the error.
                    raise RuntimeError(
                        f"OpenRouter rate limit: {error_msg}. "
                        f"Retried {self._MAX_429_RETRIES} times. "
                        f"Try again in {retry_after} seconds."
                    )

            # ---- 401 Auth error ----
            if response.status_code == 401:
                raise RuntimeError(
                    "OpenRouter authentication failed. Please check your "
                    "OPENROUTER_API_KEY environment variable."
                )

            # ---- Any other 4xx/5xx error ----
            if response.status_code >= 400:
                upstream_msg = ""
                try:
                    err_data = response.json()
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
                        upstream_msg = str(err_data)
                except Exception:
                    upstream_msg = response.text[:500]

                if len(upstream_msg) > 500:
                    upstream_msg = upstream_msg[:500] + "..."

                raise RuntimeError(
                    f"OpenRouter API error {response.status_code}: {upstream_msg}"
                )

            # Success
            return response.json()

        # Should never reach here (loop exits via return or raise above).
        raise RuntimeError(
            f"OpenRouter rate limit: {last_429_error}. "
            f"Retried {self._MAX_429_RETRIES} times."
        )

    def _stream_request(self, url: str, data: dict, headers: dict) -> Generator[dict, None, None]:
        """Handle streaming requests."""
        response = requests.post(
            url,
            json=data,
            headers=headers,
            timeout=self.config.timeout,
            stream=True
        )
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    json_str = line[6:]
                    if json_str.strip() == '[DONE]':
                        continue
                    try:
                        chunk = json.loads(json_str)
                        yield chunk
                    except json.JSONDecodeError:
                        continue

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

    def _build_openai_body(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None,
        temperature: float,
        max_tokens: int,
        **kwargs,
    ) -> dict:
        """Build an OpenAI Chat-Completions request body for OpenRouter.

        Centralises request construction so generate() and generate_stream()
        stay in sync. All optional fields are only added when supplied.
        """
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            # OpenRouter accepts both `max_tokens` (legacy, universally
            # supported) and `max_completion_tokens` (newer OpenAI). We send
            # `max_tokens` to maximise compatibility with free / 3rd-party
            # providers that may not have adopted the new field yet.
            "max_tokens": max_tokens,
        }

        # Tools in OpenAI function-calling format.
        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]

        # Optional fields — only added when explicitly provided.
        optional_int_fields = ("top_p", "top_k", "seed", "n")
        optional_float_fields = ("presence_penalty", "frequency_penalty")
        for field in optional_int_fields + optional_float_fields:
            val = kwargs.get(field)
            if val is not None:
                body[field] = val

        stop = kwargs.get("stop")
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]

        response_format = kwargs.get("response_format")
        if response_format is not None:
            body["response_format"] = response_format

        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        return body

    @staticmethod
    def _parse_openai_response(raw_response: dict) -> dict:
        """Parse an OpenAI-format Chat Completions response.

        Returns a dict in the shape AgentKthx's agent loop expects:
        {
          "content": str,
          "tool_calls": [{"id", "name", "arguments": dict}, ...],
          "finish_reason": str | None,
          "usage": {...},
          "raw": <original response>,
        }

        Raises:
            RuntimeError: if the response body carries an OpenRouter
                provider-side `error` field (this happens on HTTP 200
                when an upstream provider is rate-limited or fails).
                Surfacing it here lets the chat loop print a meaningful
                message instead of silently showing an empty response.
        """
        # OpenRouter sometimes returns HTTP 200 with a top-level `error`
        # field (e.g. "Provider rate limited", "upstream error"). Detect
        # this and raise so the user sees a real message.
        err_field = raw_response.get("error")
        if err_field:
            if isinstance(err_field, dict):
                err_msg = err_field.get("message") or str(err_field)
                err_code = err_field.get("code")
            else:
                err_msg = str(err_field)
                err_code = None
            code_str = f" (code={err_code})" if err_code is not None else ""
            raise RuntimeError(f"OpenRouter provider error: {err_msg}{code_str}")

        choices = raw_response.get("choices", []) or []
        if not choices:
            # No choices and no error field — surface a clear message rather
            # than silently returning an empty response the user sees as blank.
            raise RuntimeError(
                "OpenRouter returned no choices in the response"
            )

        choice = choices[0]
        message = choice.get("message", {}) or {}

        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls") or []
        finish_reason = choice.get("finish_reason")
        # R05.8: Capture reasoning_content (chain-of-thought) emitted by
        # thinking-capable models routed through OpenRouter (e.g.
        # o-series, GLM-5.x, deepseek-r1). Surfaced on the response so
        # callers / CLI can display it via --think.
        reasoning_content = message.get("reasoning_content", "") or ""

        # Parse OpenAI tool_calls format:
        #   { "id": "...", "type": "function",
        #     "function": { "name": "...", "arguments": "<JSON string>" } }
        parsed_tool_calls: list[dict] = []
        for tc in raw_tool_calls:
            func = tc.get("function", {}) or {}
            args = func.get("arguments", "{}")
            # OpenAI returns arguments as a JSON STRING, not an object.
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    # Fall back to a raw wrapper so the agent loop can
                    # surface the bad payload rather than crashing.
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {"input": args}

            parsed_tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })

        usage = raw_response.get("usage", {}) or {}

        return {
            "content": content,
            "tool_calls": parsed_tool_calls,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "reasoning_content": reasoning_content,  # populated by thinking models
            "raw": raw_response,
        }

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

        if os.environ.get("AGENTNOVA_DEBUG"):
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
                if os.environ.get("AGENTNOVA_DEBUG"):
                    print(f"  [OpenRouter] Model doesn't support tools — "
                          f"retrying without tools (ReAct fallback)")
                body.pop("tools", None)
                body.pop("tool_choice", None)
                raw_response = self._make_api_request("chat/completions", body)
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

        if os.environ.get("AGENTNOVA_DEBUG"):
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
                    if os.environ.get("AGENTNOVA_DEBUG"):
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

    # Generate_stream method is inherited from OllamaBackend