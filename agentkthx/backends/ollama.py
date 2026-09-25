"""
⚛️ AgentKthx — Ollama Backend
Backend implementation for Ollama inference engine.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Generator, Optional

from .base import BaseBackend, BackendConfig
from .openai_compat import OpenAICompatibleBackend
from ..core.types import BackendType, ToolSupportLevel, ApiMode
from ..core.models import Tool, ToolParam
from ..config import OLLAMA_BASE_URL


class OllamaBackend(OpenAICompatibleBackend):
    """
    Backend for Ollama inference engine.

    Ollama is a popular local LLM server that supports
    many open-source models with native tool calling.

    ARCH-01: Extends ``OpenAICompatibleBackend`` (not ``BaseBackend``
    directly) so the shared JEV / body-construction / streaming-SSE
    logic is inherited from one place. Ollama-specific functionality
    (native ``/api/chat`` OPENRE mode, model management, logprobs)
    stays here.

    R06.57 (MAINT-05): ``is_cloud = False`` overrides the
    ``OpenAICompatibleBackend`` default. Ollama is a local server —
    no rate limits, no billing, supports native OPENRE mode. The CLI
    uses this to skip cloud-specific defaults (streaming-by-default,
    catalog-based num_ctx/num_predict, cloud column layout in
    cmd_models, OPENAI api_mode default in cmd_models).
    """

    #: R06.57 (MAINT-05): Local server — overrides OpenAICompatibleBackend's True.
    is_cloud: bool = False

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config: BackendConfig | None = None,
        api_mode: ApiMode | str = ApiMode.OPENRE,
    ):
        # Determine base URL - priority: base_url > host/port > env > default
        if base_url:
            resolved_url = base_url.rstrip("/")
        elif host and port:
            resolved_url = f"http://{host}:{port}"
        else:
            resolved_url = OLLAMA_BASE_URL.rstrip("/")

        # Set API mode (openre = OpenResponses, openai = Chat-Completions)
        if isinstance(api_mode, str):
            api_mode = ApiMode(api_mode.lower())

        # Call parent with shared state
        super().__init__(config=config, base_url=resolved_url, api_mode=api_mode)

        # Set environment variable so other components know the API mode
        os.environ["AGENTKTHX_API_MODE"] = api_mode.value

        # R06.57: Persisted safe max_tokens after a context-length 400 (if a
        # local llama.cpp fork ever returns one). Honored by _apply_max_tokens_cap
        # in _get_model_defaults. Mirrors the cloud backends' init.
        self._context_safe_max_tokens: int | None = None

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OLLAMA

    @property
    def base_url(self) -> str:
        return self._base_url

    # api_mode property/setter is inherited from OpenAICompatibleBackend (ARCH-01)

    def _convert_messages_to_ollama_format(self, messages: list[dict]) -> list[dict]:
        """
        Convert messages to Ollama native format.
        
        Key difference from OpenAI format:
        - OpenAI: tool_calls[].function.arguments is a JSON STRING
        - Ollama: tool_calls[].function.arguments should be an OBJECT
        
        This method parses the JSON string arguments back to objects for
        Ollama's native /api/chat endpoint.
        """
        converted = []
        for msg in messages:
            converted_msg = dict(msg)  # Copy the message
            
            # Convert tool_calls if present
            if "tool_calls" in msg and msg["tool_calls"]:
                ollama_tool_calls = []
                for tc in msg["tool_calls"]:
                    ollama_tc = dict(tc)
                    if "function" in tc:
                        func = tc["function"]
                        args = func.get("arguments", "{}")
                        
                        # Parse JSON string to object for Ollama
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        
                        ollama_tc["function"] = {
                            "name": func.get("name", ""),
                            "arguments": args
                        }
                    ollama_tool_calls.append(ollama_tc)
                converted_msg["tool_calls"] = ollama_tool_calls
            
            converted.append(converted_msg)
        
        return converted

    def generate(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        think: bool | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response from Ollama.
        
        Args:
            model: Model name
            messages: Chat messages
            tools: List of Tool objects for native tool calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            think: For thinking models (qwen3, deepseek-r1): None=auto, False=disable thinking
            **kwargs: Additional options passed to Ollama
        """
        # Dispatch based on api_mode
        if self._api_mode == ApiMode.OPENAI:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [Ollama] Dispatching to OpenAI-compatible API (mode={self._api_mode.value})")
            return self.generate_completions(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                think=think,
                **kwargs,
            )

        # JEV dispatch — shared by OllamaBackend, ZaiBackend, OpenRouterBackend.
        # Returns a generate()-shaped dict if JEV mode is active, else None.
        jev_response = self._maybe_jev_dispatch(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            **kwargs,
        )
        if jev_response is not None:
            return jev_response

        # Native Ollama /api/chat endpoint
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/chat"

        # Convert messages to Ollama native format
        # Key difference: Ollama expects tool_call arguments as OBJECT, not JSON string
        ollama_messages = self._convert_messages_to_ollama_format(messages)

        # Build request body
        body = {
            "model": model,
            "messages": ollama_messages,
            "stream": False,
            "keep_alive": "1m",  # Keep model loaded briefly but clear KV cache between requests
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        # Add tools if provided
        if tools:
            body["tools"] = [t.to_json_schema() for t in tools]

        # Add think parameter for thinking models (qwen3, deepseek-r1, etc.)
        if think is not None:
            body["think"] = think
        # Forward reasoning_effort for OpenAI o-series / GLM-5 thinking models.
        # Native /api/chat doesn't officially support this, but Ollama
        # generally forwards unknown top-level keys to the model template.
        reasoning_effort = kwargs.get("reasoning_effort")
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        # Add any extra options
        for key, value in kwargs.items():
            if key == "stop":
                body["options"]["stop"] = value if isinstance(value, list) else [value]
            elif key not in ("model", "messages", "tools", "stream", "think", "reasoning_effort"):
                body["options"][key] = value

        # Debug output for request
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Ollama] Request: tools={len(tools) if tools else 0}, think={think}")
            # Show messages being sent (respect truncation setting)
            truncation_disabled = kwargs.get("truncation") == "disabled"
            for i, msg in enumerate(body.get("messages", [])):
                role = msg.get("role", "?")
                if role == "system":
                    print(f"  [Ollama.Body] msg[{i}]: role={role}, content=<{len(msg.get('content', ''))} chars>")
                elif role == "tool":
                    content = msg.get('content', '')
                    if truncation_disabled:
                        print(f"  [Ollama.Body] msg[{i}]: role={role}, tool_call_id={msg.get('tool_call_id', 'MISSING')!r}, content={content}")
                    else:
                        print(f"  [Ollama.Body] msg[{i}]: role={role}, tool_call_id={msg.get('tool_call_id', 'MISSING')!r}, content={content[:50]!r}")
                elif "tool_calls" in msg:
                    tc = msg.get("tool_calls", [])
                    print(f"  [Ollama.Body] msg[{i}]: role={role}, tool_calls={tc}")
                else:
                    content = msg.get('content', '')
                    if truncation_disabled:
                        print(f"  [Ollama.Body] msg[{i}]: role={role}, content={content}")
                    else:
                        print(f"  [Ollama.Body] msg[{i}]: role={role}, content={content[:100]!r}")

        # Make request
        start_time = time.time()

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            error_msg = error_body.lower() if error_body else ""
            
            # Check if model doesn't support tools - fallback to no tools (ReAct mode)
            # R06.57: also match "unsupported param: tools" (llama-server 500)
            # which is how BitNet's llama.cpp fork rejects the tools param.
            if tools and ("does not support tools" in error_msg or "unsupported param: tools" in error_msg):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [Ollama] Model doesn't support tools, falling back to ReAct mode")
                # Retry without tools - let ReAct parsing handle tool calls
                body_fallback = {k: v for k, v in body.items() if k != "tools"}
                try:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(body_fallback).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as e2:
                    error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                    raise RuntimeError(f"Ollama HTTP error {e2.code}: {error_body2}")
            else:
                raise RuntimeError(f"Ollama HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama connection error: {e.reason}")

        latency_ms = (time.time() - start_time) * 1000

        # Parse response
        message = result.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls", [])
        # Capture thinking_content / reasoning_content if model emitted it.
        # Ollama uses "thinking" key for thinking-capable models (qwen3,
        # deepseek-r1, etc.). OpenAI-compatible responses use "reasoning_content".
        reasoning_content = (
            message.get("reasoning_content", "")
            or message.get("thinking", "")
            or ""
        )

        # Debug output
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [Ollama] Raw result keys: {list(result.keys())}")
            print(f"  [Ollama] Message keys: {list(message.keys())}")
            if kwargs.get("truncation") == "disabled":
                print(f"  [Ollama] Content: {content if content else '(empty)'}")
            else:
                print(f"  [Ollama] Content: {content[:1024] if content else '(empty)'}")
            print(f"  [Ollama] Tool calls: {tool_calls}")
            if reasoning_content:
                rc_preview = reasoning_content[:200] + "..." if len(reasoning_content) > 200 else reasoning_content
                print(f"  [Ollama] Reasoning: {rc_preview}")

        # Parse tool calls from Ollama format
        # IMPORTANT: Preserve the 'id' field - it's required for tool result messages
        parsed_tool_calls = []
        for tc in tool_calls:
            func = tc.get("function", {})
            parsed_tool_calls.append({
                "id": tc.get("id", ""),  # Preserve for tool result correlation
                "name": func.get("name", ""),
                "arguments": func.get("arguments", {}),
            })

        return {
            "content": content,
            "tool_calls": parsed_tool_calls,
            "usage": {
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "completion_tokens": result.get("eval_count", 0),
                "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0),
            },
            "latency_ms": latency_ms,
            "reasoning_content": reasoning_content,  # populated by thinking models
            "raw": result,
        }

    # ─────────────────────────────────────────────────────────────────────
    # System-One Decision Mode (ApiMode.JEV)
    # ─────────────────────────────────────────────────────────────────────
    # ARCH-01: JEV methods (generate_decision, _maybe_jev_dispatch,
    # _build_jev_messages, _parse_jev_response, _serialize_state,
    # _JEV_SYSTEM_PROMPT) are now inherited from OpenAICompatibleBackend.
    # Only _jev_call_completions stays here as the Ollama-specific hook
    # that delegates to self.generate_completions().

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
        Hook: underlying chat-completions call used by generate_decision().

        Default implementation delegates to OllamaBackend.generate_completions(),
        which hits the OpenAI-compatible /v1/chat/completions endpoint with
        no auth headers.

        Subclasses with custom auth or endpoint paths (ZaiBackend uses
        /api/paas/v4/chat/completions with Bearer token, OpenRouterBackend
        uses _make_api_request with 429 retry) override this method to
        route through their own auth-injected path while keeping the
        same response shape ({content, tool_calls, usage, latency_ms, raw}).
        """
        return self.generate_completions(
            model=model,
            messages=messages,
            tools=None,
            temperature=temperature,
            max_tokens=max_tokens,
            think=think,
            response_format=response_format,
            **kwargs,
        )

    # ─────────────────────────────────────────────────────────────────────
    # OpenAI Chat-Completions
    # ─────────────────────────────────────────────────────────────────────

    def generate_completions(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        think: bool | None = None,
        # OpenAI Chat Completions additional parameters
        stop: str | list[str] | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        response_format: dict | None = None,
        top_p: float | None = None,
        # OpenAI Chat Completions API spec completeness (v1.0)
        logprobs: bool | None = None,
        top_logprobs: int | None = None,
        n: int | None = None,
        user: str | None = None,
        # Tool choice control (OpenAI API spec)
        tool_choice: str | dict | None = None,
        **kwargs,
    ) -> dict:
        """Generate a response using OpenAI Chat-Completions compatible API.
        
        Uses Ollama's /v1/chat/completions endpoint which is compatible with
        OpenAI's Chat Completions API format.
        
        Args:
            model: Model name
            messages: Chat messages
            tools: List of Tool objects for native tool calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            think: For thinking models (qwen3, deepseek-r1): None=auto, False=disable thinking
            stop: Stop sequences (string or list of strings)
            presence_penalty: Presence penalty (-2.0 to 2.0)
            frequency_penalty: Frequency penalty (-2.0 to 2.0)
            response_format: Response format (e.g., {"type": "json_object"})
            top_p: Top-p sampling (0.0 to 1.0)
            logprobs: Whether to return log probabilities of output tokens
            top_logprobs: Number of most likely tokens to return at each position (requires logprobs=True)
            n: Number of completions to generate (default: 1)
            user: Unique identifier representing the end-user for abuse monitoring
            **kwargs: Additional options passed to Ollama
        """
        import urllib.request
        import urllib.error

        # OpenAI-compatible endpoint
        url = f"{self.base_url}/v1/chat/completions"

        # Build request body in OpenAI format
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # Add optional OpenAI parameters
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]
        if presence_penalty is not None:
            body["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            body["frequency_penalty"] = frequency_penalty
        if response_format is not None:
            body["response_format"] = response_format
        if top_p is not None:
            body["top_p"] = top_p
        
        # Add OpenAI API spec completeness parameters (v1.0)
        if logprobs is not None:
            body["logprobs"] = logprobs
        if top_logprobs is not None:
            body["top_logprobs"] = top_logprobs
        if n is not None:
            body["n"] = n
        if user is not None:
            body["user"] = user

        # Add tools in OpenAI format
        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]
        
        # Add tool_choice parameter (OpenAI API spec)
        # Supports: "auto", "none", "required", or {"type": "function", "function": {"name": "..."}}
        if tool_choice is not None:
            body["tool_choice"] = tool_choice

        # Add think parameter for thinking models (qwen3, deepseek-r1)
        # Ollama's OpenAI-compatible endpoint supports this in the request body
        if think is not None:
            body["think"] = think
        # Forward reasoning_effort for OpenAI o-series / GLM-5 thinking models.
        # When provided, instructs the model to use the requested effort level.
        # Silently ignored by models that don't recognize it.
        reasoning_effort = kwargs.get("reasoning_effort")
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        # Debug output for request
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenAI-Comp] Request: tools={len(tools) if tools else 0}, think={think}")

        # Make request
        start_time = time.time()

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            error_msg = error_body.lower() if error_body else ""
            
            # Check if model doesn't support tools - fallback to no tools (ReAct mode)
            # R06.57: also match "unsupported param: tools" (llama-server 500)
            # which is how BitNet's llama.cpp fork rejects the tools param.
            if tools and ("does not support tools" in error_msg or "unsupported param: tools" in error_msg):
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"  [OpenAI-Comp] Model doesn't support tools, falling back to ReAct mode")
                # Retry without tools - let ReAct parsing handle tool calls
                body_fallback = {k: v for k, v in body.items() if k != "tools"}
                try:
                    req = urllib.request.Request(
                        url,
                        data=json.dumps(body_fallback).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as e2:
                    error_body2 = e2.read().decode("utf-8") if e2.fp else ""
                    raise RuntimeError(f"Ollama HTTP error {e2.code}: {error_body2}")
            else:
                raise RuntimeError(f"Ollama HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama connection error: {e.reason}")

        latency_ms = (time.time() - start_time) * 1000

        # Parse OpenAI-format response
        # Response structure: {"choices": [{"message": {"content": ..., "tool_calls": [...]}}]}
        choices = result.get("choices", [])
        if not choices:
            return {
                "content": "",
                "tool_calls": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "latency_ms": latency_ms,
                "raw": result,
            }

        # Helper function to parse a single choice
        def _parse_choice(choice: dict) -> dict:
            """Parse a single choice from OpenAI format response."""
            message = choice.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])
            finish_reason = choice.get("finish_reason")
            # Capture reasoning_content if the model emitted one.
            # Thinking-capable models (GLM-4.5+, o-series, DeepSeek-R1, etc.)
            # populate this field with their chain-of-thought before the
            # final answer. We surface it on the response so callers /
            # CLI can display it via --think.
            reasoning_content = message.get("reasoning_content", "") or ""

            # Parse tool calls from OpenAI format
            # OpenAI format: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "{...}"}}
            parsed_tool_calls = []
            for tc in tool_calls:
                func = tc.get("function", {})
                args = func.get("arguments", "{}")
                # OpenAI returns arguments as JSON string, parse it
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

            # Extract logprobs if present
            choice_logprobs = None
            if logprobs:
                choice_logprobs = choice.get("logprobs")

            return {
                "content": content,
                "tool_calls": parsed_tool_calls,
                "finish_reason": finish_reason,
                "logprobs": choice_logprobs,
                "reasoning_content": reasoning_content,
            }

        usage = result.get("usage", {})

        # OpenAI Chat Completions API v1.0: Handle multiple completions (n > 1)
        # When n > 1, process ALL choices and return them under "choices" (plural) key
        # Maintain backward compatibility by also including first choice under existing keys
        num_choices = len(choices)
        parsed_choices = [_parse_choice(choice) for choice in choices]

        # First choice for backward-compatible keys
        first_choice = parsed_choices[0]
        content = first_choice["content"]
        parsed_tool_calls = first_choice["tool_calls"]
        finish_reason = first_choice["finish_reason"]
        logprobs_result = first_choice["logprobs"]
        reasoning_content = first_choice.get("reasoning_content", "") or ""

        # Debug output
        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenAI-Comp] Choices: {num_choices}")
            if kwargs.get("truncation") == "disabled":
                print(f"  [OpenAI-Comp] Content[0]: {content if content else '(empty)'}")
            else:
                print(f"  [OpenAI-Comp] Content[0]: {content[:1024] if content else '(empty)'}")
            print(f"  [OpenAI-Comp] Tool calls[0]: {parsed_tool_calls}")
            if reasoning_content:
                rc_preview = reasoning_content[:200] + "..." if len(reasoning_content) > 200 else reasoning_content
                print(f"  [OpenAI-Comp] Reasoning[0]: {rc_preview}")

        # Build response dict
        response = {
            "content": content,  # Backward compatible: first choice content
            "tool_calls": parsed_tool_calls,  # Backward compatible: first choice tool calls
            "finish_reason": finish_reason,  # OpenAI API spec: "stop", "length", "tool_calls", "content_filter"
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "latency_ms": latency_ms,
            "logprobs": logprobs_result,
            "reasoning_content": reasoning_content,  # populated by thinking models
            "raw": result,
        }

        # When n > 1, include all choices under "choices" key (OpenAI API spec compliance)
        if num_choices > 1:
            response["choices"] = parsed_choices

        return response

    def generate_completions_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        think: bool | None = None,
        stop: str | list[str] | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
        response_format: dict | None = None,
        top_p: float | None = None,
        # OpenAI Chat Completions API spec completeness (v1.0)
        logprobs: bool | None = None,
        top_logprobs: int | None = None,
        n: int | None = None,
        user: str | None = None,
        **kwargs,
    ) -> Generator[dict, None, None]:
        """Stream generated text using OpenAI Chat-Completions compatible API with SSE.
        
        Uses Server-Sent Events (SSE) to stream responses from Ollama's 
        /v1/chat/completions endpoint.
        
        Yields dict chunks with:
            - "delta": text delta from the model
            - "finish_reason": None or "stop" when complete
            - "tool_calls": incremental tool call data (if any)
            - "logprobs": log probability data (when logprobs=True)
        
        Args:
            model: Model name
            messages: Chat messages
            tools: List of Tool objects for native tool calling
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            think: For thinking models (qwen3, deepseek-r1): None=auto, False=disable thinking
            stop: Stop sequences (string or list of strings)
            presence_penalty: Presence penalty (-2.0 to 2.0)
            frequency_penalty: Frequency penalty (-2.0 to 2.0)
            response_format: Response format (e.g., {"type": "json_object"})
            top_p: Top-p sampling (0.0 to 1.0)
            logprobs: Whether to return log probabilities of output tokens
            top_logprobs: Number of most likely tokens to return at each position
            n: Number of completions to generate (default: 1)
            user: Unique identifier representing the end-user for abuse monitoring
            **kwargs: Additional options
            
        Yields:
            Dict with "delta", "finish_reason", and optionally "tool_calls" and "logprobs"
        """
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/v1/chat/completions"

        # Build request body
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # Add optional OpenAI parameters
        if stop is not None:
            body["stop"] = stop if isinstance(stop, list) else [stop]
        if presence_penalty is not None:
            body["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            body["frequency_penalty"] = frequency_penalty
        if response_format is not None:
            body["response_format"] = response_format
        if top_p is not None:
            body["top_p"] = top_p
        
        # Add OpenAI API spec completeness parameters (v1.0)
        if logprobs is not None:
            body["logprobs"] = logprobs
        if top_logprobs is not None:
            body["top_logprobs"] = top_logprobs
        if n is not None:
            body["n"] = n
        if user is not None:
            body["user"] = user

        if tools:
            body["tools"] = [t.to_openai_schema() for t in tools]

        # Add think parameter for thinking models (qwen3, deepseek-r1)
        if think is not None:
            body["think"] = think
        # Forward reasoning_effort (OpenAI o-series / GLM-5 thinking models)
        reasoning_effort = kwargs.get("reasoning_effort")
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        if os.environ.get("AGENTKTHX_DEBUG"):
            print(f"  [OpenAI-Comp-Stream] Request: tools={len(tools) if tools else 0}, think={think}")

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                buffer = ""
                last_finish_reason = None
                for line in response:
                    line = line.decode("utf-8")
                    
                    # SSE format: "data: {...}\n\n"
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        
                        # Check for end of stream
                        if data_str == "[DONE]":
                            # Yield final chunk with finish_reason if not already sent
                            if last_finish_reason is None:
                                yield {
                                    "delta": "",
                                    "finish_reason": "stop",  # Default to "stop" when [DONE] received
                                    "tool_calls": None,
                                }
                            break
                        
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        
                        delta = choices[0].get("delta", {})
                        finish_reason = choices[0].get("finish_reason")
                        
                        # Track last finish_reason for final chunk
                        if finish_reason:
                            last_finish_reason = finish_reason
                        
                        # Extract text content from delta
                        text_delta = delta.get("content", "")
                        
                        # Extract tool calls if present
                        tool_calls_delta = delta.get("tool_calls", None)
                        
                        # Extract logprobs if present (OpenAI API spec completeness)
                        # In streaming mode, logprobs are returned per-chunk in choices[0].logprobs
                        logprobs_data = choices[0].get("logprobs") if logprobs else None
                        
                        # Build yield dict with required fields
                        yield_chunk = {
                            "delta": text_delta,
                            "finish_reason": finish_reason,
                            "tool_calls": tool_calls_delta,
                        }
                        
                        # Include logprobs in yield when available
                        if logprobs_data is not None:
                            yield_chunk["logprobs"] = logprobs_data
                        
                        yield yield_chunk
                        
                        if finish_reason:
                            break

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"Ollama HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama connection error: {e.reason}")

    def generate_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs,
    ) -> Generator[str, None, None]:
        """Stream generated text from Ollama."""
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/chat"

        # Build request body
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        if tools:
            body["tools"] = [t.to_json_schema() for t in tools]

        for key, value in kwargs.items():
            if key not in ("model", "messages", "tools", "stream"):
                body["options"][key] = value

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
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
            raise RuntimeError(f"Ollama HTTP error {e.code}")

        except urllib.error.URLError as e:
            raise RuntimeError(f"Ollama connection error: {e.reason}")

    def get_model_info(self, model: str) -> dict | None:
        """
        Get detailed model information from Ollama (with per-instance caching).
        
        Uses /api/show endpoint which returns:
        - modelfile
        - parameters (including num_ctx)
        - template
        - details (family, parameter count, etc.)
        
        Results are cached for the lifetime of the backend instance.
        """
        # Check instance cache first
        if not hasattr(self, '_model_info_cache'):
            self._model_info_cache = {}
        if model in self._model_info_cache:
            return self._model_info_cache[model]

        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/show"

        body = {"name": model}

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                self._model_info_cache[model] = data
                return data

        except (urllib.error.HTTPError, urllib.error.URLError):
            return None

    # Known defaults by model family
    # Note: These are fallbacks. Actual context is read from model_info.<family>.context_length
    # ARCH-01: FAMILY_CONTEXT_DEFAULTS and get_context_by_family are now
    # inherited from OpenAICompatibleBackend. Only get_model_runtime_context
    # and get_model_max_context stay here as Ollama-specific implementations
    # (they query the Ollama /api/show endpoint for num_ctx and model_info).
    # R07.01: the zero-caller get_model_context_size override was removed
    # (dead repo-wide, along with the OpenAICompatibleBackend base method).

    def get_model_runtime_context(self, model: str) -> int:
        """
        Get the actual runtime context window size for a model.
        
        This is the num_ctx value from parameters, which Ollama defaults to 2048
        if not explicitly set in the Modelfile.
        
        Args:
            model: Model name
        
        Returns:
            Runtime context window size in tokens (defaults to 2048)
        """
        info = self.get_model_info(model)
        
        if not info:
            return 2048  # Ollama's default
        
        # Check parameters string for num_ctx
        parameters = info.get("parameters", "")
        if parameters:
            for line in parameters.split("\n"):
                line = line.strip()
                if line.startswith("num_ctx"):
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            return int(parts[1])
                        except ValueError:
                            pass
        
        # No explicit num_ctx - Ollama defaults to 2048
        return 2048

    def get_model_max_context(self, model: str, family: str | None = None) -> int:
        """
        Get the model's maximum trained context window size.
        
        Resolution order (most authoritative first):
        1. API /api/show → model_info.<family>.context_length  (exact model data)
        2. API /api/show → details.family → FAMILY_CONTEXT_DEFAULTS lookup
        3. Caller-provided family → FAMILY_CONTEXT_DEFAULTS lookup
        4. Hardcoded fallback 4096
        
        Args:
            model: Model name
            family: Optional family name (fallback if API doesn't report it)
        
        Returns:
            Maximum context window size in tokens
        """
        # Primary: ask the API for the model's actual context_length
        info = self.get_model_info(model)
        
        if info:
            model_info = info.get("model_info", {})
            # Key format: "<family>.context_length" (e.g., "gemma3.context_length")
            for key, value in model_info.items():
                if key.endswith(".context_length"):
                    try:
                        return int(value)
                    except (ValueError, TypeError):
                        pass
            
            # Fallback: bare "context_length" key (some Ollama versions)
            if "context_length" in model_info:
                try:
                    return int(model_info["context_length"])
                except (ValueError, TypeError):
                    pass
            
            # Try family from API details (more reliable than caller-provided)
            details = info.get("details", {})
            api_family = details.get("family", "")
            if api_family:
                ctx = self.get_context_by_family(api_family)
                if ctx:
                    return ctx
        
        # Fallback: use caller-provided family or hardcoded table
        if family:
            ctx = self.get_context_by_family(family)
            if ctx:
                return ctx
        
        return 4096  # Ultimate fallback

    def list_models(self) -> list[dict]:
        """List available models from Ollama."""
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/tags"

        try:
            req = urllib.request.Request(url, method="GET")

            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))

            return result.get("models", [])

        except (urllib.error.HTTPError, urllib.error.URLError):
            return []

    # No family-based assumptions - tool support depends on the model's template
    # Each model must be tested individually. Use --tool-support to test.

    def test_tool_support(self, model: str, family: str | None = None, force_test: bool = False) -> ToolSupportLevel:
        """
        Test model's tool support capability.

        Detection logic (from main branch):
        1. HTTP 400 "does not support tools" → none (Ollama's explicit rejection)
        2. Native tool_calls in API response, calling correct function → native
        3. No tool_calls but content has JSON tool call pattern → react (text-based)
        4. No tool_calls, no tool-like JSON, but API accepted tools → react

        Key insight: "native" requires ACTUAL native tool_calls structure in API response.
        Models that output JSON as text are "react", not "native".

        Results are automatically cached after each live test. If force_test is False,
        the cache is checked first before returning UNTESTED.

        Args:
            model: Model name
            family: Optional family hint (unused, kept for API compatibility)
            force_test: If True, make a test API call to determine support

        Returns:
            ToolSupportLevel (NATIVE, REACT, NONE, or UNTESTED if force_test=False)
        """
        from ..core.tool_cache import get_cached_tool_support, cache_tool_support

        # Determine API mode for cache namespacing
        api_mode = self._api_mode.value if hasattr(self, "_api_mode") else "openre"

        if not force_test:
            # Check cache first, even without force_test
            cached = get_cached_tool_support(model, api_mode=api_mode)
            if cached is not None:
                return cached
            return ToolSupportLevel.UNTESTED

        # Test tool: Weather (simple, commonly supported)
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
            # Send request with tools but NO system prompt override
            # Use model's default Modelfile system prompt
            response = self.generate(
                model=model,
                messages=[{
                    "role": "user",
                    "content": "What's the weather like in Tokyo?"
                }],
                tools=[test_tool],
                max_tokens=100,
            )

            message = response.get("message", response)
            tool_calls = message.get("tool_calls", [])
            content = message.get("content", "")

            # 2. Check for NATIVE tool_calls in API response structure
            # This is the ONLY path to "native" classification
            # Note: generate() already parses tool_calls into flat format: {"name": ..., "arguments": ...}
            if tool_calls:
                for tc in tool_calls:
                    # After parsing by generate(), structure is flat
                    func_name = tc.get("name", "")
                    args = tc.get("arguments", {})
                    
                    # Verify it's calling our tool (not hallucinating a different one)
                    if func_name == "get_weather":
                        # Check for reasonable arguments
                        if isinstance(args, dict) and ("location" in args or "city" in args or len(args) > 0):
                            result = ToolSupportLevel.NATIVE
                            break
                    # Native tool_calls exist but wrong function - still native capability
                    # (model might be confused but HAS native support)
                    elif func_name:  # Any function name = native structure
                        result = ToolSupportLevel.NATIVE
                        break
                else:
                    # tool_calls existed but no valid function found
                    result = ToolSupportLevel.REACT
            else:
                result = None  # No native tool calls

            if result is not None:
                cache_tool_support(model, result, family=family or "", api_mode=api_mode)
                return result

            # 3. Check for text-based tool calls in content
            # Models that output JSON like {"name": "get_weather", ...} as TEXT
            if self._contains_text_tool_call(content):
                cache_tool_support(model, ToolSupportLevel.REACT, family=family or "", api_mode=api_mode)
                return ToolSupportLevel.REACT

            # 4. API succeeded, no explicit rejection, no native tool_calls
            # Model accepted tools parameter but didn't use native calling
            # This is the "react" case - can still parse text-based tool calls
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "", api_mode=api_mode)
            return ToolSupportLevel.REACT

        except Exception as e:
            error_str = str(e)

            # 1. Check for explicit "does not support tools" rejection
            # R06.57: also match "unsupported param: tools" (llama-server 500,
            # used by BitNet's llama.cpp fork to reject the tools param).
            error_lower = error_str.lower()
            if "does not support tools" in error_lower or "unsupported param: tools" in error_lower:
                cache_tool_support(model, ToolSupportLevel.NONE, family=family or "",
                                   error=error_str[:100], api_mode=api_mode)
                return ToolSupportLevel.NONE

            # Other HTTP errors or connection issues
            cache_tool_support(model, ToolSupportLevel.REACT, family=family or "",
                               error=error_str[:100], api_mode=api_mode)
            return ToolSupportLevel.REACT

        finally:
            # Always unload the model after testing to free memory
            try:
                self.unload_model(model)
            except Exception:
                pass  # Ignore errors during cleanup

    def _contains_text_tool_call(self, content: str) -> bool:
        """
        Check if the response content contains a JSON tool call pattern.
        Models that output tool calls as TEXT (not native API) show this pattern.

        Examples:
          {"name": "get_weather", "arguments": {"location": "Tokyo"}}
          {"tool": "calculator", "arguments": {"expression": "2+2"}}
        """
        if not content:
            return False

        import re

        # Remove markdown code blocks if present
        cleaned = re.sub(r"```(?:json)?", "", content).strip().rstrip("`").strip()

        # Look for JSON object pattern
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1:
            return False

        try:
            import json
            obj = json.loads(cleaned[start:end + 1])

            # Check for tool call patterns
            if not isinstance(obj, dict):
                return False

            # Pattern 1: {"name": "...", "arguments": {...}}
            if "name" in obj and ("arguments" in obj or "parameters" in obj):
                return True

            # Pattern 2: {"tool": "...", "arguments": {...}}
            if "tool" in obj and "arguments" in obj:
                return True

            return False

        except json.JSONDecodeError:
            return False

    def unload_model(self, model: str) -> dict:
        """
        Unload a model from memory (stop keeping it warm).
        Equivalent to: ollama stop <model>

        Args:
            model: Model name to unload

        Returns:
            Response dict from server
        """
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/api/generate"
        body = {"model": model, "keep_alive": 0}

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError):
            return {"status": "not_running"}

    # ─────────────────────────────────────────────────────────────────────
    # OpenAICompatibleBackend abstract hooks (ARCH-01)
    # ─────────────────────────────────────────────────────────────────────

    def _get_chat_completions_url(self) -> str:
        """Ollama's OpenAI-compat endpoint."""
        return f"{self.base_url}/v1/chat/completions"

    def _get_auth_headers(self) -> dict:
        """Ollama needs no auth headers (local server)."""
        return {"Content-Type": "application/json"}

    def _iter_sse_lines(self, url: str, body: dict, headers: dict) -> Generator[bytes, None, None]:
        """Make a streaming POST to Ollama's /v1/chat/completions and yield raw SSE lines.

        R06.57: Now uses the shared ``_handle_context_length_400`` helper for
        context-length 400 recovery (same as OpenRouter/Gemini/ZAI). Local
        backends rarely 400 on context length (they truncate internally),
        but llama.cpp forks (TurboQuant, BitNet) might.
        """
        import urllib.request
        import urllib.error
        for attempt in range(2):  # max 2 attempts (original + 1 retry)
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout) as response:
                    for line in response:
                        yield line
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""
                # R06.57: shared context-length 400 handler
                if e.code == 400 and attempt == 0:
                    if self._handle_context_length_400(error_body, body):
                        continue  # retry with reduced max_tokens
                raise RuntimeError(f"Ollama HTTP error {e.code}: {error_body}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"Ollama connection error: {e.reason}")
            return  # success — don't retry

    def _get_model_defaults(self, model: str) -> dict:
        """Ollama per-model defaults (temperature, max_tokens, context_length).

        R06.57: Now uses the shared ``_apply_max_tokens_cap`` helper inherited
        from ``OpenAICompatibleBackend`` — same ``num_ctx/32`` cap + persisted
        ``_context_safe_max_tokens`` pattern as the cloud backends. This
        prevents local backends from requesting a huge ``max_tokens`` that
        leaves no room for input growth on long agentic runs.

        Local backends (Ollama, llama-server, BitNet) don't return HTTP 400
        context-length errors (they truncate internally), so the persisted
        safe value rarely fires — but if a llama.cpp fork DOES 400 (e.g.
        llama-cpp-turboquant with a tight context), the recovery path now
        works the same as cloud backends.
        """
        # Resolve context_length from the family-based lookup
        family = None
        if ":" in model:
            base = model.split(":")[0].lower()
        else:
            base = model.lower()
        if "qwen" in base:
            family = "qwen2"
        elif "llama" in base:
            family = "llama3"
        elif "mistral" in base:
            family = "mistral"
        elif "phi" in base:
            family = "phi3"
        elif "gemma" in base:
            family = "gemma"
        elif "deepseek" in base:
            family = "deepseek-r1"

        from ..core.model_family_config import get_family_defaults
        defaults = get_family_defaults(family)
        max_tokens = defaults.get("max_tokens", 2048)
        # R06.57: Use the RUNTIME context (what the server actually enforces),
        # NOT the model's theoretical max. For Ollama, this is the num_ctx from
        # the Modelfile (defaults to 2048). For llama-server/TurboQuant, this
        # is what was passed as --ctx-size when starting the server (defaults
        # to 4096, or TURBOQUANT_CTX for TurboQuant).
        #
        # Using the model's max (e.g. 262144 for qwen3.5) would cap max_tokens
        # to 262144//32 = 8192, which equals the entire runtime context if the
        # server was started with -c 8192 — leaving zero room for input.
        # Using the runtime context (e.g. 8192) correctly caps to 8192//32 = 256.
        try:
            # Try runtime context first (queries Ollama /api/show for num_ctx,
            # or checks NUM_CTX env var for llama-server)
            if hasattr(self, 'get_model_runtime_context'):
                context_length = self.get_model_runtime_context(model)
                # If runtime context is the Ollama default (2048), try the
                # max context as a fallback — the model might support more
                # and the user may have set num_ctx via --num-ctx CLI flag
                # (which doesn't show up in the Modelfile).
                if context_length <= 2048:
                    max_ctx = self.get_model_max_context(model, family=family)
                    if max_ctx and max_ctx > 2048:
                        # Use the smaller of: the CLI --num-ctx, or the model max
                        from ..config import NUM_CTX
                        if NUM_CTX and NUM_CTX > 0:
                            context_length = min(NUM_CTX, max_ctx)
                        else:
                            context_length = max_ctx
            else:
                context_length = self.get_model_max_context(model, family=family)
            if not context_length or context_length < 1024:
                context_length = 4096
        except Exception:
            context_length = 4096
        temperature = defaults.get("temperature", 0.7)

        # Use the shared cap helper (honors _context_safe_max_tokens if set)
        return self._apply_max_tokens_cap(
            max_tokens, context_length, temperature=temperature
        )

    def __repr__(self) -> str:
        return f"OllamaBackend(url={self.base_url})"