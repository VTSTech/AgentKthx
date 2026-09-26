"""
⚛️ AgentKthx — ZAI API Backend
Backend implementation for the ZAI API (OpenAI Chat-Completions compatible).

ZAI provides cloud-hosted LLM inference via an OpenAI-compatible API endpoint.
This backend inherits the OpenAI Chat-Completions logic from OllamaBackend
and adds API key authentication and ZAI-specific defaults.

Endpoints used:
  - POST /api/paas/v4/chat/completions → OpenAI Chat Completions (tools, streaming)
  - GET  /api/paas/v4/models          → model discovery (if supported)

Configuration:
  ZAI_BASE_URL   — API base URL (default: https://api.z.ai)
  ZAI_API_KEY    — API key for authentication (required)

Usage:
  # CLI
  agentkthx chat --backend zai --model glm-5.1 --tools calculator
  agentkthx run "What is 15 * 8?" --backend zai --model glm-4-flash

  # Python API
  from agentkthx import Agent
  agent = Agent(model="glm-4-plus", backend="zai", tools=["calculator"])
  result = agent.run("What is 15 * 8?")

Max Tokens Information (from ZAI API docs):
  Note: max_tokens limits the length of generated content (output), not including input.
  Context window (input + output) confirmed for each model below.
  
  Model Code          Default max_tokens    Maximum max_tokens    Context Length (Official)
  glm-5.3             65536               131072               128K → 125K display
  glm-5.3-flash       65536               131072               128K → 125K display  
  glm-5.2             65536               131072               128K → 125K display
  glm-5.1             65536               131072               128K → 125K display
  glm-5               65536               131072               128K → 125K display
  glm-4.7             65536               131072               200K → 195K display * Official
  glm-4.7-flash       65536               131072               200K → 195K display * Official
  glm-4.6             65536               131072               200K → 195K display * Official
  glm-4.6v            16384               32768                varies
  glm-4.6v-flash      16384               32768                varies
  glm-4.6v-flashx     16384               32768                varies
  glm-4.5             65536               98304                128K → 125K display → 132K display * Updated
  glm-4.5-air         65536               98304                128K → 125K display
  glm-4.5-x           65536               98304                128K → 125K display
  glm-4.5-airx        65536               98304                128K → 125K display
  glm-4.5-flash       65536               98304                128K → 125K display → 132K display * Updated
  glm-4.5v           16384               16384                varies
  glm-4-32b-0414-128k 16384               16384                128K → 125K display

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Generator

from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.backends.base import BackendConfig
from agentkthx.core.types import BackendType, ToolSupportLevel, ApiMode
from agentkthx.core.models import Tool, ToolParam
from agentkthx.config import ZAI_BASE_URL, ZAI_API_KEY, ZAI_FREE_ONLY, ZAI_FREE_FALLBACK_MODEL


# ZAI model catalog with metadata for context sizing and defaults.
# Keys are model identifiers accepted by the ZAI API.
# Context lengths and pricing sourced from https://docs.z.ai.
# The /api/paas/v4/models endpoint may not return all models —
# this catalog ensures flash variants and other models are always available.
# Updated: 2026-04-15
ZAI_MODELS: dict[str, dict] = {
    # ── GLM 5.x ──────────────────────────────────────────────────────
    "glm-5.1": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 1.4, "output": 4.4},
    },
    "glm-5.2": {
        "context_length": 1048576,  # 1M for display (1024 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.6, "output": 2.2},
    },
    "glm-5": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 1.0, "output": 3.2},
    },
    "glm-5-turbo": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 1.2, "output": 4.0},
    },
    # ── GLM 5.3 ─────────────────────────────────────────────────────
    "glm-5.3": {
        "context_length": 1048576,  # 1M for display (1024 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.6, "output": 2.2},
    },
    "glm-5.3-flash": {
        "context_length": 1048576,  # 1M for display (1024 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.0, "output": 0.0},
    },
    "glm-5.3-flashx": {
        "context_length": 1048576,  # 1M for display (1024 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.07, "output": 0.4},
    },
    # ── GLM 4.7 ─────────────────────────────────────────────────────
    "glm-4.7": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.6, "output": 2.2},
    },
    "glm-4.7-flash": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.0, "output": 0.0},  # Free
    },
    # ── GLM 4.6 ─────────────────────────────────────────────────────
    "glm-4.6": {
        "context_length": 204800,  # 200K for display (200 * 1024)
        "default_temperature": 0.7,
        "default_max_tokens": 131072,  # 128K maximum output
        "pricing": {"input": 0.6, "output": 2.2},
    },
    # ── GLM 4.5 ─────────────────────────────────────────────────────
    "glm-4.5": {
        "context_length": 132000,  # 128K rounded for display
        "default_temperature": 0.7,
        "default_max_tokens": 98304,  # Model-specific maximum (96K)
        "pricing": {"input": 0.6, "output": 2.2},
    },
    "glm-4.5-flash": {
        "context_length": 132000,  # 128K rounded for display
        "default_temperature": 0.7,
        "default_max_tokens": 98304,  # Model-specific maximum (96K)
        "pricing": {"input": 0.0, "output": 0.0},  # Free
    },
    "glm-4.5-air": {
        "context_length": 132000,  # 128K rounded for display
        "default_temperature": 0.7,
        "default_max_tokens": 98304,  # Model-specific maximum (96K)
        "pricing": {"input": 0.2, "output": 1.1},
    },
    # ── GLM 4.x variants ─────────────────────────────────────────────
}

# Default model when none specified.
ZAI_DEFAULT_MODEL = "glm-5.1"


def _is_free_model(model: str) -> bool:
    """Check if a ZAI model is free (zero pricing)."""
    model_key = model.split("/")[-1] if "/" in model else model
    meta = ZAI_MODELS.get(model_key)
    if not meta:
        return False
    pricing = meta.get("pricing", {})
    return pricing.get("input", -1) == 0.0 and pricing.get("output", -1) == 0.0


class ZaiBackend(OpenAICompatibleBackend):
    """
    Backend for ZAI API (OpenAI Chat-Completions compatible).

    Inherits the full OpenAI Chat Completions implementation from OllamaBackend
    (generate_completions, generate_completions_stream, tool calling) and
    customizes server management for ZAI's cloud endpoint.

    Key differences from Ollama:
    - Always uses OPENAI API mode (no native /api/chat)
    - Requires API key authentication via Bearer token
    - Cloud endpoint (no local server management)
    - Model catalog is static (no /api/show, /api/tags)
    - No is_running() health check (always available)

    Usage:
        backend = get_backend("zai")
        backend = ZaiBackend(api_key="sk-...")
    """

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str | None = None,
        api_key: str | None = None,
    ):
        # Determine base URL — priority: base_url > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"https://{host}:{port}"
        else:
            resolved_url = ZAI_BASE_URL.rstrip("/")

        # ZAI is OpenAI-compatible. Both OPENAI and JEV modes use the
        # same /v1/chat/completions endpoint underneath. JEV adds a
        # decision-prompt wrapper on top, but the wire format is the
        # same. Reject OPENRE (native /api/chat) since ZAI doesn't
        # expose it.
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
                print(f"  [ZAI] API mode '{api_mode}' not supported — ZAI only supports OpenAI / JEV, forcing OPENAI")
            forced_mode = ApiMode.OPENAI

        # Call parent (OpenAICompatibleBackend → BaseBackend) with resolved values.
        super().__init__(
            base_url=resolved_url,
            config=config,
            api_mode=forced_mode,
        )

        # ARCH-01: OllamaBackend.__init__ used to set this env var; since
        # ZAI no longer inherits from OllamaBackend, set it here so
        # is_openresponses_mode() in core/openresponses.py works correctly.
        os.environ["AGENTKTHX_API_MODE"] = forced_mode.value

        # API key — priority: explicit > env var > config module
        self._api_key = api_key or os.environ.get("ZAI_API_KEY", "") or ZAI_API_KEY
        if not self._api_key or not self._api_key.strip():
            raise ValueError(
                "ZAI_API_KEY is required for the ZAI backend. "
                "Set it via --api-key, ZAI_API_KEY env var, or Config.zai_api_key."
            )
        if len(self._api_key.strip()) < 8:
            raise ValueError(
                f"ZAI_API_KEY appears invalid (too short: {len(self._api_key.strip())} chars). "
                "Check your ZAI_API_KEY environment variable."
            )

        # R06.57: Persisted safe max_tokens after a context-length 400.
        # Set by _iter_sse_lines() when a streaming call 400s with
        # "context length" in the error message. Honored by
        # _get_model_defaults() so future agentic-loop steps don't
        # re-trigger the same 400. Mirrors OpenRouterBackend (R06.55)
        # and GeminiBackend (R06.56).
        self._context_safe_max_tokens: int | None = None

    @property
    def backend_type(self) -> BackendType:
        return BackendType.ZAI

    @property
    def base_url(self) -> str:
        """Return the ZAI API base URL."""
        return self._base_url

    @property
    def api_key(self) -> str:
        """Return the API key."""
        return self._api_key

    # ─────────────────────────────────────────────────────────────────────
    # Server Management — ZAI is a cloud service
    # ─────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """
        Check if ZAI API is reachable.

        Unlike local backends, ZAI is a cloud service — we check if we have
        an API key configured rather than probing a health endpoint.
        """
        if not self._api_key:
            return False
        return True

    def list_models(self) -> list[dict]:
        """
        List available ZAI models.

        Queries the ZAI API /api/paas/v4/models endpoint dynamically, then
        merges with the static catalog. The API may not return all models
        (e.g., flash variants), so the catalog fills in the gaps.

        Enriches API results with context_length from the static catalog.
        """
        import urllib.request
        import urllib.error

        # Track which models the API knows about
        api_model_keys: set[str] = set()

        # Try dynamic discovery from the API
        try:
            url = f"{self._base_url}/api/paas/v4/models"

            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            req = urllib.request.Request(url, headers=headers, method="GET")

            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))

            api_models = result.get("data", [])
            if api_models:
                for m in api_models:
                    name = m.get("id", "")
                    if not name:
                        continue
                    # Strip provider prefix if present (e.g., "zai/glm-4-flash")
                    model_key = name.split("/")[-1] if "/" in name else name
                    api_model_keys.add(model_key)

                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [ZAI] API returned {len(api_model_keys)} models")

        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] Model discovery failed ({e}), using static catalog")
        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] Model discovery error ({e}), using static catalog")

        # Build unified list: start with full static catalog
        # API-discovered models get the same treatment (catalog enriches them)
        seen: set[str] = set()
        models = []

        # Add API models first (they're confirmed available)
        for model_key in sorted(api_model_keys):
            if model_key in seen:
                continue
            seen.add(model_key)
            meta = ZAI_MODELS.get(model_key, {})
            models.append({
                "name": model_key,
                "size": 0,
                "details": {
                    "family": "glm",
                    "backend": "zai",
                    "context_length": meta.get("context_length", 128000),
                },
            })

        # Add catalog-only models (not returned by API, e.g. flash variants)
        for name in sorted(ZAI_MODELS.keys()):
            if name not in seen:
                seen.add(name)
                meta = ZAI_MODELS[name]
                models.append({
                    "name": name,
                    "size": 0,
                    "details": {
                        "family": "glm",
                        "backend": "zai",
                        "context_length": meta.get("context_length", 128000),
                    },
                })

        if os.environ.get("AGENTKTHX_DEBUG"):
            catalog_only = len(models) - len(api_model_keys)
            print(f"  [ZAI] Total: {len(models)} models ({len(api_model_keys)} API + {catalog_only} catalog)")

        return models

    def get_model_info(self, model: str) -> dict | None:
        """
        Get model information.

        Checks the static catalog first for context_length metadata,
        then queries the API to verify the model exists.
        Always returns info for any model name (ZAI accepts any valid model ID).
        """
        # Normalize: strip provider prefix if present (e.g., "zai/glm-4-plus")
        model_key = model.split("/")[-1] if "/" in model else model
        meta = ZAI_MODELS.get(model_key, {})

        # Return catalog info if available
        if meta:
            return {
                "name": model_key,
                "size": 0,
                "details": {
                    "family": "glm",
                    "backend": "zai",
                    "context_length": meta.get("context_length", 128000),
                },
            }

        # Model not in static catalog — still valid if ZAI knows it
        return {
            "name": model_key,
            "size": 0,
            "details": {
                "family": "glm",
                "backend": "zai",
                "context_length": 128000,
            },
        }

    def _get_model_defaults(self, model: str) -> dict:
        """
        Get model-specific defaults from catalog.

        ARCH-03 (R06.57): Cap + persisted-safe-value logic now inherited
        from ``OpenAICompatibleBackend._apply_max_tokens_cap``. ZAI's
        error format matches the base class defaults (``"maximum context
        length is N tokens"`` + ``"N of text input"`` + ``"N of tool
        input"``), so no regex override is needed.

        Returns:
            dict: temperature, max_tokens, and other model defaults
        """
        model_key = model.split("/")[-1] if "/" in model else model
        meta = ZAI_MODELS.get(model_key, {})

        max_tokens = meta.get("default_max_tokens", 8192)
        context_length = meta.get("context_length", 128000)
        temperature = meta.get("default_temperature", 0.7)

        if os.environ.get("AGENTKTHX_DEBUG"):
            capped = min(max_tokens, context_length // 32)
            if capped < max_tokens:
                print(f"  [ZAI Debug] Capped max_tokens {max_tokens} -> "
                      f"{capped} (context={context_length}, divisor=32)")

        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=temperature
        )

    # ─────────────────────────────────────────────────────────────────────
    # Generation — always OpenAI Chat-Completions
    # ─────────────────────────────────────────────────────────────────────

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """
        Generate a response from ZAI API.

        Always uses OpenAI Chat-Completions format. The `think` parameter
        is ignored (ZAI handles thinking internally if applicable).

        Injects Bearer token authentication into every request.
        Supports ZAI_FREE_ONLY mode and auto-fallback on insufficient credits.
        """
        # JEV dispatch — if api_mode is JEV, route through generate_decision()
        # which wraps the underlying LLM call with a decision prompt.
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

        # Use model defaults from catalog if not specified
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]
            
        if think is not None and os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [ZAI] 'think' parameter ignored — ZAI manages thinking internally")

        # ZAI_FREE_ONLY: reject paid models upfront
        if ZAI_FREE_ONLY and not _is_free_model(model):
            fallback = ZAI_FREE_FALLBACK_MODEL
            print(f"  [ZAI] FREE_ONLY mode — '{model}' is a paid model, switching to '{fallback}'")
            model = fallback

        # Delegate to the inherited OpenAI Chat-Completions implementation
        # but inject our API key into the request headers
        return self._generate_with_auth(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

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
        JEV hook for ZAI: route the decision call through ZAI's
        Bearer-authenticated /api/paas/v4/chat/completions endpoint
        via _generate_with_auth(), instead of the OllamaBackend's
        default /v1/chat/completions.

        This keeps ZAI's auth + ZAI_FREE_ONLY + fallback logic active
        when running decisions through ZAI free models like glm-4.5-flash.

        The response shape is normalized to match generate_completions():
            {content, tool_calls, usage, latency_ms, raw}
        """
        # Apply ZAI_FREE_ONLY upfront — decisions should also respect it
        if ZAI_FREE_ONLY and not _is_free_model(model):
            fallback = ZAI_FREE_FALLBACK_MODEL
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI.JEV] FREE_ONLY mode — '{model}' is a paid model, switching to '{fallback}'")
            model = fallback

        # _generate_with_auth() returns the same {content, tool_calls, usage, ...} shape
        # that generate_completions() returns — it's a drop-in replacement.
        return self._generate_with_auth(
            model=model,
            messages=messages,
            tools=None,  # decisions never call tools
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            **kwargs,
        )

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        """
        Stream generated text from ZAI API.

        Always uses OpenAI Chat-Completions SSE streaming.
        """
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/paas/v4/chat/completions"

        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]

        for key, value in kwargs.items():
            if key not in ("model", "messages", "tools", "stream"):
                body[key] = value

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                for line in response:
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"ZAI connection error: {e.reason}")

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks (ARCH-01)
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """ZAI's OpenAI-compat endpoint."""
        return f"{self.base_url}/api/paas/v4/chat/completions"

    def _get_auth_headers(self) -> dict:
        """ZAI requires Bearer token authentication."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _iter_sse_lines(self, url: str, body: dict, headers: dict) -> Generator[bytes, None, None]:
        """Make a streaming POST to ZAI's /api/paas/v4/chat/completions.

        Includes ZAI-specific error recovery:
        - 429 insufficient credits -> retry with free fallback model
        - 400 "does not support tools" -> retry without tools (ReAct fallback)
        - 400 "context length" -> reduce max_tokens, persist, retry once (R06.57)
        """
        import urllib.request
        import urllib.error

        def _do_request(req_body: dict):
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=self.config.timeout)

        # R06.57: max 2 attempts — original + 1 retry on context-length 400.
        # The 429/credits and 400/no-tools paths are handled inline below
        # (they retry with a different body, not a reduced max_tokens).
        for attempt in range(2):
            try:
                response = _do_request(body)
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""
                error_msg = error_body.lower() if error_body else ""

                # ARCH-03 (R06.57): Context-length 400 — delegate to the shared
                # _handle_context_length_400 helper inherited from
                # OpenAICompatibleBackend. ZAI's error format matches the base
                # class defaults, so no regex override is needed.
                if e.code == 400 and attempt == 0:
                    old_max = body.get("max_tokens", 4096)
                    if self._handle_context_length_400(error_body, body):
                        new_max = body["max_tokens"]
                        print(f"  [ZAI-Stream] Context length exceeded — "
                              f"reducing max_tokens {old_max} -> {new_max} and retrying")
                        continue

                # Insufficient credits -- auto-fallback to free model
                if e.code == 429 and ("insufficient balance" in error_msg or "insufficient" in error_msg or "no resource package" in error_msg):
                    fallback = ZAI_FREE_FALLBACK_MODEL
                    if not _is_free_model(body.get("model", "")):
                        import sys
                        print(
                            f"\n  \033[33m[ZAI-Stream] Insufficient credits for '{body.get('model')}' -- "
                            f"falling back to free model '{fallback}'\033[0m",
                            file=sys.stderr,
                        )
                        body_fallback = {**body, "model": fallback}
                        try:
                            response = _do_request(body_fallback)
                        except urllib.error.HTTPError as e2:
                            error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                            raise RuntimeError(f"ZAI HTTP error {e2.code}: {error_body2}")
                    else:
                        raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")
                # Model doesn't support tools -- retry without tools (ReAct fallback)
                elif "does not support tools" in error_msg and body.get("tools"):
                    import sys
                    print(
                        f"\n  \033[33m[ZAI-Stream] Model '{body.get('model')}' does not support tools -- "
                        f"retrying without tool definitions\033[0m",
                        file=sys.stderr,
                    )
                    body_fallback = {k: v for k, v in body.items() if k != "tools"}
                    body_fallback.pop("tool_choice", None)
                    try:
                        response = _do_request(body_fallback)
                    except urllib.error.HTTPError as e2:
                        error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                        raise RuntimeError(f"ZAI HTTP error {e2.code}: {error_body2}")
                else:
                    raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"ZAI connection error: {e.reason}")

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
    # inherited from ``OpenAICompatibleBackend``. ZAI's error format matches
    # the base class default regex patterns, so no override needed.

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream OpenAI Chat-Completions chunks from ZAI.

        ARCH-01: Thin override that handles ZAI_FREE_ONLY upfront, then
        delegates to super().generate_completions_stream() which uses
        _get_chat_completions_url(), _get_auth_headers(), _iter_sse_lines(),
        and _build_openai_body(stream=True).
        """
        # ZAI_FREE_ONLY: reject paid models upfront
        if ZAI_FREE_ONLY and not _is_free_model(model):
            fallback = ZAI_FREE_FALLBACK_MODEL
            print(f"  [ZAI] FREE_ONLY mode -- '{model}' is a paid model, switching to '{fallback}'")
            model = fallback

        yield from super().generate_completions_stream(
            model=model,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def _generate_with_auth(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> dict:
        """
        Generate via ZAI /api/paas/v4/chat/completions with Bearer auth.

        This is a standalone implementation rather than calling
        super().generate_completions() because we need to inject
        the Authorization header, which the parent method doesn't support.
        """
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/paas/v4/chat/completions"

        # Use model defaults from catalog if not specified
        defaults = self._get_model_defaults(model)
        if temperature is None:
            temperature = defaults["temperature"]
        if max_tokens is None:
            max_tokens = defaults["max_tokens"]
        
        # Build request body in OpenAI format
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Optional OpenAI-compatible parameters
        stop = kwargs.get("stop")
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]
        if kwargs.get("presence_penalty") is not None:
            body["presence_penalty"] = kwargs["presence_penalty"]
        if kwargs.get("frequency_penalty") is not None:
            body["frequency_penalty"] = kwargs["frequency_penalty"]
        if kwargs.get("response_format") is not None:
            body["response_format"] = kwargs["response_format"]
        if kwargs.get("top_p") is not None:
            body["top_p"] = kwargs["top_p"]

        # Add tools in OpenAI format
        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]

        # Add tool_choice parameter
        if kwargs.get("tool_choice") is not None:
            body["tool_choice"] = kwargs["tool_choice"]

        # Inject Bearer token
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [ZAI] Request: model={model}, tools={len(tools) if tools else 0}")

        start_time = time.time()

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            error_msg = error_body.lower() if error_body else ""

            # Check for insufficient credits — auto-fallback to free model
            if e.code == 429 and ("insufficient balance" in error_msg or "insufficient" in error_msg or "no resource package" in error_msg):
                fallback = ZAI_FREE_FALLBACK_MODEL
                if not _is_free_model(model):
                    import sys
                    print(
                        f"\n  \033[33m[ZAI] Insufficient credits for '{model}' — "
                        f"falling back to free model '{fallback}'\033[0m",
                        file=sys.stderr,
                    )
                    if os.environ.get("AGENTKTHX_DEBUG"):
                        print(f"  [ZAI] Insufficient credits for '{model}', falling back to '{fallback}'")
                    body_fallback = {**body, "model": fallback}
                    try:
                        req = urllib.request.Request(
                            url,
                            data=json.dumps(body_fallback).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                            result = json.loads(response.read().decode("utf-8"))
                        print(f"  [ZAI] Fallback to '{fallback}' succeeded")
                        # Continue to normal response parsing below
                    except Exception as e2:
                        raise RuntimeError(f"ZAI: paid model '{model}' failed (insufficient credits) and free fallback '{fallback}' also failed: {e2}")
                else:
                    raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")
            # Check if model doesn't support tools — fallback to no tools
            elif "does not support tools" in error_msg and tools:
                import sys
                print(
                    f"\n  \033[33m[ZAI] Model '{model}' does not support tools — "
                    f"retrying without tool definitions\033[0m",
                    file=sys.stderr,
                )
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [ZAI] Model doesn't support tools, falling back to ReAct mode")
                body_fallback = {k: v for k, v in body.items() if k != "tools"}
                try:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(body_fallback).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as e2:
                    error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                    raise RuntimeError(f"ZAI HTTP error {e2.code}: {error_body2}")
            # ARCH-03 (R06.57): Context-length 400 — delegate to the shared
            # _handle_context_length_400 helper inherited from
            # OpenAICompatibleBackend. Mirrors OpenRouterBackend.generate()
            # at the same error site.
            elif e.code == 400:
                old_max = body.get("max_tokens", 4096)
                if self._handle_context_length_400(error_body, body):
                    new_max = body["max_tokens"]
                    print(f"  [ZAI] Context length exceeded — "
                          f"reducing max_tokens {old_max} -> {new_max} and retrying")
                    try:
                        req = urllib.request.Request(
                            url,
                            data=json.dumps(body).encode("utf-8"),
                            headers=headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                            result = json.loads(response.read().decode("utf-8"))
                    except urllib.error.HTTPError as e2:
                        error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                        raise RuntimeError(f"ZAI HTTP error {e2.code}: {error_body2}")
                else:
                    raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")
            else:
                raise RuntimeError(f"ZAI HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"ZAI connection error: {e.reason}")

        latency_ms = (time.time() - start_time) * 1000

        # Parse OpenAI-format response
        choices = result.get("choices", [])
        if not choices:
            return {
                "content": "",
                "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "latency_ms": latency_ms,
                "raw": result,
            }

        message = choices[0].get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])
        finish_reason = choices[0].get("finish_reason")
        # R05.8: Capture reasoning_content (chain-of-thought) emitted by
        # thinking-capable ZAI models (GLM-4.5+, GLM-5.x). Surfaced on the
        # response so callers / CLI can display it via --think.
        reasoning_content = message.get("reasoning_content", "") or ""

        # Parse tool calls from OpenAI format
        parsed_tool_calls = []
        for tc in tool_calls:
            func = tc.get("function", {})
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            parsed_tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [ZAI] Content: {content[:1024] if content else '(empty)'}")
            print(f"  [ZAI] Tool calls: {parsed_tool_calls}")
            if reasoning_content:
                rc_preview = reasoning_content[:200] + "..." if len(reasoning_content) > 200 else reasoning_content
                print(f"  [ZAI] Reasoning: {rc_preview}")

        usage = result.get("usage", {})

        return {
            "content": content,
            "tool_calls": parsed_tool_calls,
            "finish_reason": finish_reason,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "latency_ms": latency_ms,
            "reasoning_content": reasoning_content,  # populated by thinking models
            "raw": result,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Tool Support — ZAI models support native function calling
    # ─────────────────────────────────────────────────────────────────────

    def test_tool_support(self, model: str, family: str | None = None, force_test: bool = False) -> ToolSupportLevel:
        """
        Test model's tool support capability.

        ZAI's GLM models support native function calling via the standard
        OpenAI tools format. Returns NATIVE for known GLM models.

        When force_test=True, makes a live API call to verify.
        """
        from agentkthx.core.tool_cache import get_cached_tool_support, cache_tool_support

        api_mode = "openai"

        if not force_test:
            cached = get_cached_tool_support(model, api_mode=api_mode)
            if cached is not None:
                return cached
            return ToolSupportLevel.UNTESTED

        # Check API key before making a test call
        if not self._api_key:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] No API key configured — cannot test tool support")
            return ToolSupportLevel.UNTESTED

        # Test tool: Weather
        test_tool = Tool(
            name="get_weather",
            description="Get the current weather for a location",
            params=[ToolParam(
                name="location",
                type="string",
                description="The city and country, e.g., 'Paris, France'"
            )],
        )

        try:
            import urllib.request
            import urllib.error

            url = f"{self.base_url}/api/paas/v4/chat/completions"

            body = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "What's the weather like in Tokyo?"
                }],
                "tools": [test_tool.to_openai_schema()],
                "stream": False,
                "temperature": 0.0,
                "max_tokens": 200,
            }

            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))

            choices = result.get("choices", [])
            if not choices:
                support = ToolSupportLevel.REACT
                cache_tool_support(model, support, family=family or "glm", api_mode=api_mode)
                return support

            message = choices[0].get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            # Native tool calls in response → NATIVE support
            if tool_calls:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [ZAI] Tool support: NATIVE (tool_calls={len(tool_calls)})")
                cache_tool_support(model, ToolSupportLevel.NATIVE, family=family or "glm", api_mode=api_mode)
                return ToolSupportLevel.NATIVE

            # Check for ReAct-style text patterns
            if content and any(kw in content.lower() for kw in ["action:", "action input:", "final answer:"]):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [ZAI] Tool support: REACT (text-based tool pattern)")
                cache_tool_support(model, ToolSupportLevel.REACT, family=family or "glm", api_mode=api_mode)
                return ToolSupportLevel.REACT

            # API accepted tools but model didn't use them — REACT-capable
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] Tool support: REACT (tools accepted, no tool calls)")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "glm", api_mode=api_mode)
            return ToolSupportLevel.REACT

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            error_msg = error_body.lower()

            if "does not support" in error_msg or "invalid" in error_msg:
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [ZAI] Tool support: REACT (server rejected tools param)")
                cache_tool_support(model, ToolSupportLevel.REACT, family=family or "glm",
                                   error=str(e), api_mode=api_mode)
                return ToolSupportLevel.REACT

            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] Tool support: REACT (HTTP {e.code})")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "glm",
                               error=str(e), api_mode=api_mode)
            return ToolSupportLevel.REACT

        except Exception as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [ZAI] Tool support test failed: {e}")
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "glm",
                               error=str(e), api_mode=api_mode)
            return ToolSupportLevel.REACT

    # ─────────────────────────────────────────────────────────────────────
    # Context Size — from static model catalog
    # ─────────────────────────────────────────────────────────────────────

    def get_model_runtime_context(self, model: str) -> int:
        """
        Get the runtime context window size.

        ZAI doesn't expose per-request context settings, so we return
        the model's maximum trained context from our catalog.
        """
        model_key = model.split("/")[-1] if "/" in model else model
        meta = ZAI_MODELS.get(model_key)
        if meta:
            return meta.get("context_length", 128000)

        # Check if user set num_ctx via env var
        from agentkthx.config import NUM_CTX
        if NUM_CTX and NUM_CTX > 0:
            return NUM_CTX

        return 128000  # ZAI default

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """
        Get the model's maximum trained context window size.

        Resolution order:
        1. Static catalog (ZAI_MODELS)
        2. Caller-provided family → OllamaBackend family table
        3. Fallback 128000
        """
        model_key = model.split("/")[-1] if "/" in model else model
        meta = ZAI_MODELS.get(model_key)
        if meta:
            return meta.get("context_length", 128000)

        # Try family heuristics
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx

        return 128000

    def __repr__(self) -> str:
        key_status = "configured" if self._api_key else "NO KEY"
        return f"ZaiBackend(url={self._base_url}, key={key_status})"
