"""
⚛️ AgentNova — OpenRouter API Backend
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
  from agentnova import Agent
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
from agentnova.backends.base import BaseBackend, BackendConfig
from agentnova.backends.ollama import OllamaBackend
from agentnova.core.types import BackendType, ToolSupportLevel, ApiMode
from agentnova.core.models import Tool, ToolParam
from agentnova.config import OPENROUTER_BASE_URL, OPENROUTER_API_KEY, OPENROUTER_DEFAULT_MODEL, OPENROUTER_FREE_ONLY


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
            
        print(f"[DEBUG] OpenRouter using URL: {resolved_url}")

        # Set API mode (OpenRouter only supports OpenAI Chat-Completions)
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())
        if api_mode != ApiMode.OPENAI:
            raise ValueError("OpenRouter backend only supports OpenAI Chat-Completions API mode")

        # Call parent with shared state
        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # API key is lazy-loaded - only required for generation, not for listing models
        self.api_key = os.environ.get("OPENROUTER_API_KEY", "")

        # Set headers for OpenRouter
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentNova",
            "X-Title": "AgentNova"
        }

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OPENROUTER

    @property
    def base_url(self) -> str:
        return self._base_url
    
    @property
    def api_mode(self) -> ApiMode:
        return self._api_mode

    def list_models(self) -> list[dict]:
        """List available models from OpenRouter API with caching."""
        import time
        
        # Check cache first
        current_time = time.time()
        if (self._model_cache is not None and 
            current_time - self._cache_time < self._CACHE_TIMEOUT):
            return self._model_cache
        
        try:
            # Use proper headers for API call
            headers = {
                "HTTP-Referer": "https://github.com/VTSTech/AgentNova",
                "X-Title": "AgentNova"
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
            
            # Parse API response
            for model in models_data.get("data", []):
                model_id = model.get("id")
                if model_id:
                    # Get model info from catalog if available
                    model_info = OPENROUTER_MODELS.get(model_id, {})
                    available_models.append({
                        "name": model_id,
                        "size": 0,  # OpenRouter doesn't provide size info
                        "details": {
                            "family": model_info.get("provider", "unknown"),
                            "backend": "openrouter",
                            "context_length": model_info.get("context_length", 128000),
                        },
                        "model_data": model  # Store original model data
                    })
            
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
                free_models = [m for m in available_models 
                             if m["name"].endswith(":free") or m["name"].endswith("-free")]
                # Fallback to catalog free models if API doesn't return :free models
                if not free_models:
                    free_models = [m for m in available_models 
                                 if "free" in m["name"].lower() or 
                                 any(free in m["name"].lower() for free in ["flash", "mini", "haiku", "tiny"])]
                self._model_cache = sorted(free_models, key=lambda x: x["name"])
            else:
                self._model_cache = sorted(available_models, key=lambda x: x["name"])
            
            self._cache_time = current_time
            return self._model_cache
            
        except Exception as e:
            # Fallback to catalog if API fails
            catalog_models = []
            for name, model_info in OPENROUTER_MODELS.items():
                catalog_models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": model_info.get("provider", "unknown"),
                        "backend": "openrouter",
                        "context_length": model_info.get("context_length", 128000),
                    }
                })
            
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
        """Get model metadata from catalog or API."""
        # Check catalog first
        if model_name in OPENROUTER_MODELS:
            return OPENROUTER_MODELS[model_name]
        
        # Try to get from API (future enhancement)
        # For now, return None to let OllamaBackend handle defaults
        return None

    def _make_api_request(self, endpoint: str, data: dict, stream: bool = False) -> dict | Generator:
        """Make request to OpenRouter API."""
        url = f"{self.base_url}/{endpoint}"
        
        # Lazy API key check - only required for actual API calls
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable is required for API calls")
        
        # Update headers with API key if available
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/VTSTech/AgentNova",
            "X-Title": "AgentNova"
        }
        
        if stream:
            return self._stream_request(url, data, headers)
        else:
            response = requests.post(
                url,
                json=data,
                headers=headers,
                timeout=self.config.timeout
            )
            
            # Handle rate limiting
            if response.status_code == 429:
                error_msg = "Rate limit exceeded"
                retry_after = response.headers.get("Retry-After", "60")
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = error_data["error"]
                    elif "message" in error_data:
                        error_msg = error_data["message"]
                except:
                    pass
                raise RuntimeError(f"OpenRouter rate limit: {error_msg}. Try again in {retry_after} seconds.")
            
            # Handle authentication errors
            if response.status_code == 401:
                raise RuntimeError("OpenRouter authentication failed. Please check your OPENROUTER_API_KEY environment variable.")
            
            response.raise_for_status()
            return response.json()

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

    def test_tool_support(self, model_name: str) -> ToolSupportLevel:
        """Test tool support for a model via OpenRouter API."""
        # Most models in OpenRouter support native tool calling
        # Some models might need special handling
        unsupported_models = [
            "anthropic/claude-3-haiku",  # May have limited tool support
        ]
        
        if any(unsupported in model_name for unsupported in unsupported_models):
            return ToolSupportLevel.UNTESTED
        
        # Assume most models support native tool calling
        return ToolSupportLevel.NATIVE

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs
    ) -> dict:
        """
        Generate text using OpenRouter Chat Completions API.
        
        This method implements OpenAI Chat-Completions format compatible with
        OpenRouter's API specification.
        """
        print(f"[DEBUG] OpenRouter.generate called with model: {model}")
        print(f"[DEBUG] Messages: {messages}")
        
        # Get model info for defaults
        model_info = self._get_model_info(model)
        model_max_tokens = model_info.get("max_tokens", 4096) if model_info else 4096
        
        # Set default max tokens if not provided
        if max_tokens is None:
            max_tokens = model_max_tokens
        
        # Build request data in OpenAI format
        request_data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        
        print(f"[DEBUG] Request data: {json.dumps(request_data, indent=2)}")
        
        # Make API request
        try:
            raw_response = self._make_api_request("chat/completions", request_data)
            
            # Parse the OpenRouter response and format it for AgentNova
            if "choices" in raw_response and raw_response["choices"]:
                choice = raw_response["choices"][0]
                message = choice.get("message", {})
                content = message.get("content", "")
                
                # Return in the format that AgentNova expects
                return {
                    "content": content,
                    "tool_calls": [],
                    "usage": raw_response.get("usage", {}),
                    "raw": raw_response
                }
            else:
                # No choices in response
                return {
                    "content": "",
                    "tool_calls": [],
                    "usage": raw_response.get("usage", {}),
                    "raw": raw_response
                }
                
        except Exception as e:
            # Wrap error for consistent error handling
            raise RuntimeError(f"OpenRouter API error: {e}")

    # Generate_stream method is inherited from OllamaBackend