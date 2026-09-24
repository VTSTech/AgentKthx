# Gemini API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://ai.google.dev/gemini-api/docs
> **Primary focus**: OpenAI-compatible endpoint (`/v1beta/openai/`)
> **Last Updated**: 2026-09-24
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Model Family Specifications](#model-family-specifications)
4. [Function Calling Implementation](#function-calling-implementation)
5. [Streaming & Real-time Features](#streaming--real-time-features)
6. [Error Codes & Recovery](#error-codes--recovery)
7. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
8. [Multimodal Content Handling](#multimodal-content-handling)
9. [Thinking & Reasoning Configuration](#thinking--reasoning-configuration)
10. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
11. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — Native Gemini API (REST)
BASE_URL_NATIVE = "https://generativelanguage.googleapis.com/v1beta"

# Production — OpenAI-compatible endpoint (preferred for AgentKthx)
BASE_URL_OPENAI = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Note: the OpenAI-compat path ends with a trailing slash.
# The OpenAI Python/JS SDK expects this trailing slash; urllib-based
# clients should not strip it.
```

### API Endpoints (OpenAI-compatible)

```python
# OpenAI Chat Completions — primary AgentKthx backend surface
CHAT_COMPLETIONS = "/chat/completions"        # POST

# Model discovery
MODELS_LIST    = "/models"                    # GET
MODELS_RETRIEVE = "/models/{model}"          # GET

# Auxiliary OpenAI-compatible endpoints (not required for chat backend
# but available if AgentKthx later supports embeddings / images / batch):
EMBEDDINGS = "/embeddings"                    # POST
IMAGES_GENERATE = "/images/generations"       # POST  (Nano Banana)
VIDEOS_CREATE = "/videos"                     # POST  (Veo 3.1, long-running)
VIDEOS_RETRIEVE = "/videos/{id}"              # GET   (poll video status)
BATCHES_CREATE = "/batches"                   # POST
BATCHS_RETRIEVE = "/batches/{id}"             # GET
```

### Native (non-OpenAI) Endpoints for Reference

```python
# Used by the official @google/genai SDK and the `interactions` API.
# AgentKthx should NOT use these directly — we list them for parity checks.
INTERACTIONS = "/interactions"                # POST (preferred by Google)
GENERATE_CONTENT = "/models/{model}:generateContent"
STREAM_GENERATE_CONTENT = "/models/{model}:streamGenerateContent"
COUNT_TOKENS = "/models/{model}:countTokens"
CACHED_CONTENTS = "/cachedContents"           # explicit context caching
```

### Authentication Headers

Gemini supports two header styles. The OpenAI-compat endpoint accepts either, but `Authorization: Bearer` is preferred for OpenAI SDK parity.

```python
# Preferred — OpenAI-compatible Bearer auth
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer GEMINI_API_KEY",
}

# Alternative — Google's native header (also accepted on /openai/ endpoints)
headers_native = {
    "Content-Type": "application/json",
    "x-goog-api-key": "GEMINI_API_KEY",
}
```

### API Key Types (Critical — Sept 2026 migration)

The Gemini API is migrating from **standard** API keys to **authorization** keys:

| Key type | Bound identity | Default scope | Status (Sep 2026) |
|----------|----------------|---------------|-------------------|
| Standard | Google Cloud project (no caller identity) | Unrestricted by default | Rejected if unrestricted; restricted ones still work. **Will be rejected entirely after Sept 2026.** |
| Authorization | Google Cloud service account | Restricted to Generative Language API | Default for all new keys created since May 28, 2026. |

**AgentKthx implementation guidance:**
- Document both `GEMINI_API_KEY` and `GOOGLE_API_KEY` as supported env vars. The official Google SDKs prefer `GOOGLE_API_KEY` if both are set.
- Detect which key type the user has by attempting a `GET /models` call on first use; if the response is `403 PERMISSION_DENIED` with "unrestricted standard key rejected" wording, surface a clear actionable error telling the user to migrate to an auth key.
- Do not silently retry on `403` from a standard-key rejection — the user needs to act, retrying will burn quota and confuse.

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: ~20 MB (uploads via Files API may be larger)
- **Timeout**: 120 seconds recommended for chat completions (configurable in `BackendConfig.timeout`); video polling should use 10 s intervals with no overall cap
- **HTTPS only** — plaintext HTTP is rejected at the edge

---

## Request/Response Structure

### Complete Request Schema (OpenAI-compatible)

```json
{
    "model": "gemini-3.8-flash",
    "messages": [
        {
            "role": "system|user|assistant|tool",
            "content": "string|array",
            "name": "string (optional, for tool-call identification)",
            "tool_calls": "array (for assistant messages)",
            "tool_call_id": "string (for tool messages)"
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_tokens": 2048,
    "max_completion_tokens": 2048,
    "stream": false,
    "stream_options": { "include_usage": true },
    "n": 1,
    "stop": ["###"],
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "seed": 42,
    "reasoning_effort": "low|medium|high|minimal|none",
    "service_tier": "standard|flex|priority",
    "response_format": { "type": "text|json_object|json_schema" },
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": { "type": "object", "properties": {}, "required": [] }
            }
        }
    ],
    "tool_choice": "auto|required|none",
    "user": "string",
    "extra_body": {
        "google": {
            "thinking_config": { "thinking_level": "low", "include_thoughts": true },
            "cached_content": "cachedContents/xxxx",
            "safety_settings": []
        }
    }
}
```

### Response Schema Details

```json
{
    "id": "chatcmpl-xxxxxxxxxxxx",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gemini-3.8-flash",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "reasoning": "Thought summary (when include_thoughts=true)",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "function_name",
                            "arguments": "{\"param1\": \"value1\"}"
                        }
                    }
                ]
            },
            "finish_reason": "stop|tool_calls|length|content_filter|model_context_window_exceeded",
            "logprobs": null
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 70,
        "prompt_tokens_details": {
            "cached_tokens": 0
        },
        "completion_tokens_details": {
            "reasoning_tokens": 0
        }
    },
    "service_tier": "standard",
    "model_version": "gemini-3.8-flash-001"
}
```

### `finish_reason` Semantics (Gemini-specific)

| `finish_reason` | Gemini internal cause | AgentKthx handling |
|-----------------|-----------------------|--------------------|
| `stop` | Natural end of generation | Finalize assistant turn |
| `tool_calls` | Model emitted one or more `tool_calls` | Invoke tools, append tool results, loop |
| `length` | `max_tokens` / `max_completion_tokens` reached | Surface truncation warning to user, do not retry |
| `content_filter` | Safety filter blocked output | Log the safety rating; do not retry with same prompt |
| `model_context_window_exceeded` | Combined input + output exceeded 1M (or 2M for Pro) | Truncate history and retry |
| `RECITATION` (Gemini-internal) | Output matched training data and was suppressed | Surface as `stop` with empty content; retry with rephrased prompt |

### `usage.completion_tokens_details.reasoning_tokens`

For thinking models (Gemini 2.5+, all of Gemini 3.x), the model's internal reasoning tokens are counted separately from `completion_tokens`. They are billed at the output-token rate but are NOT surfaced to the user as visible output unless `extra_body.google.thinking_config.include_thoughts` is `true`.

AgentKthx should track `reasoning_tokens` separately in any token-budget UI so the user understands why their token spend is higher than the visible output suggests.

---

## Model Family Specifications

### Current text/multimodal models (Sep 2026)

```python
MODEL_CONFIGS = {
    # === Gemini 3.x family — current flagship generation ===
    "gemini-3.8-flash": {
        "context_length": 1_048_576,           # 1M tokens
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "supports_thinking_budget": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,     # text, image, audio, video, PDF
        "supports_thought_signatures": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 4096,
        "temperature_default": 1.0,
        "top_p_default": 0.95,
        "top_k_default": 40,
        "family": "gemini-3",
        "tier": "flash",
        "free_tier_rpm": 5,
        "free_tier_tpm": 250_000,
        "free_tier_rpd": 1500,
    },
    "gemini-3.7-flash": {
        "context_length": 1_048_576,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal_input": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 4096,
        "family": "gemini-3",
        "tier": "flash",
    },
    "gemini-3.6-flash": {  # mirror 3.7
        "context_length": 1_048_576, "max_output_tokens": 65_536,
        "supports_thinking": True, "supports_function_calling": True,
        "supports_implicit_caching": True, "min_cache_tokens": 4096,
        "family": "gemini-3", "tier": "flash",
    },
    "gemini-3.5-flash": {  # mirror 3.7
        "context_length": 1_048_576, "max_output_tokens": 65_536,
        "supports_thinking": True, "supports_function_calling": True,
        "supports_implicit_caching": True, "min_cache_tokens": 4096,
        "family": "gemini-3", "tier": "flash",
    },
    "gemini-3.5-flash-lite": {  # lowest-cost in 3.5 family
        "context_length": 1_048_576, "max_output_tokens": 65_536,
        "supports_thinking": True, "supports_function_calling": True,
        "supports_implicit_caching": True, "min_cache_tokens": 4096,
        "family": "gemini-3", "tier": "flash-lite",
    },
    "gemini-3.1-flash-lite": {
        "context_length": 1_048_576, "max_output_tokens": 65_536,
        "supports_thinking": True, "supports_function_calling": True,
        "supports_implicit_caching": True, "min_cache_tokens": 4096,
        "family": "gemini-3", "tier": "flash-lite",
    },
    "gemini-3.1-pro-preview": {
        "context_length": 2_097_152,           # 2M tokens for Pro
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "thinking_levels": ["minimal", "low", "medium", "high"],
        "supports_function_calling": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 4096,
        "family": "gemini-3", "tier": "pro-preview",
    },

    # === Gemini 2.5 family — legacy but still served ===
    "gemini-2.5-pro": {
        "context_length": 2_097_152,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "thinking_budget_min": 0,             # thinking can be disabled with budget=0
        "thinking_budget_max": 24_576,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5", "tier": "pro",
    },
    "gemini-2.5-flash": {
        "context_length": 1_048_576,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "thinking_budget_min": 0,
        "thinking_budget_max": 24_576,
        "supports_function_calling": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5", "tier": "flash",
    },
    "gemini-2.5-flash-lite": {
        "context_length": 1_048_576,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_function_calling": True,
        "supports_implicit_caching": True,
        "min_cache_tokens": 2048,
        "family": "gemini-2.5", "tier": "flash-lite",
    },

    # === Audio / image / video models — listed for completeness; not
    #     recommended as AgentKthx chat backends ===
    "gemini-3.8-live": {"family": "live", "supports_streaming": True},
    "gemini-3.8-live-extended-thinking": {"family": "live", "supports_thinking": True},
    "gemini-3.8-flash-tts": {"family": "tts"},
    "gemini-3.8-flash-lite-tts": {"family": "tts"},
    "gemini-3.5-transcribe": {"family": "asr"},

    # === Embeddings (for AgentKthx memory/RAG features) ===
    "gemini-embedding-2-preview": {
        "dimensions": 1536, "supports_multimodal_input": True,
        "max_input_tokens": 2048,
    },
    "gemini-embedding-001": {
        "dimensions": 768, "supports_multimodal_input": False,
        "max_input_tokens": 2048,
    },
}
```

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect Gemini model capabilities from name.

    The Gemini naming convention is:
        gemini-<MAJOR>.<MINOR>-<tier>[-preview][-<date>]
    e.g. gemini-3.8-flash, gemini-2.5-pro-preview-tts,
         gemini-2.5-flash-native-audio-preview-12-2025
    """
    m = model_name.lower()
    if m.startswith("gemini-3."):
        return {
            "family": "gemini-3",
            "supports_native_tools": True,
            "supports_thinking": True,
            "thinking_levels": ["minimal", "low", "medium", "high"],
            "thinking_can_be_disabled": False,   # 3.x cannot disable thinking
            "supports_thought_signatures": True,
            "min_cache_tokens": 4096,
        }
    elif m.startswith("gemini-2.5"):
        return {
            "family": "gemini-2.5",
            "supports_native_tools": True,
            "supports_thinking": True,
            "thinking_levels": [],
            "thinking_budget": (0, 24576),       # use thinking_budget, not level
            "thinking_can_be_disabled": True,    # reasoning_effort="none" works
            "supports_thought_signatures": False,
            "min_cache_tokens": 2048,
        }
    elif m.startswith("gemini-2.0"):
        return {
            "family": "gemini-2.0",
            "supports_native_tools": True,
            "supports_thinking": False,
        }
    elif m.startswith("gemini-embedding"):
        return {"family": "embedding", "is_chat_model": False}
    elif m.startswith(("veo-", "lyria-", "imagen-")):
        return {"family": "media-generation", "is_chat_model": False}
    else:
        return {
            "family": "unknown",
            "supports_native_tools": False,
            "supports_thinking": False,
        }
```

---

## Function Calling Implementation

### Tool Schema Requirements

Gemini's OpenAI-compat endpoint accepts the standard OpenAI tool schema unchanged:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City and state, e.g. Toronto, ON"
                },
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "default": "celsius"
                }
            },
            "required": ["location"]
        }
    }
}
```

### Supported Tool Types

| `type` value | Native Gemini equivalent | Available on | Notes |
|--------------|--------------------------|--------------|-------|
| `function` | `functionDeclarations` | All chat models | Standard user-defined tools |
| `code_execution` | `codeExecution` | 2.5+ | Lets the model run Python in a sandbox; results returned as a tool call result |
| `google_search` | `googleSearch` | 3.x only | Grounding with Google Search; for OpenAI-compat use `tools=[{"google_search":{}}]` in `extra_body` |
| `url_context` | `urlContext` | 3.x only | Lets the model fetch a URL's content during generation |
| `computer_use` | `computerUse` | `gemini-2.5-computer-use-preview-10-2025` only | Specialized model — not part of the chat-completions backend |

### Tool Flow Implementation

```python
import json
from typing import Any

class GeminiToolHandler:
    """Convert AgentKthx tools to Gemini's OpenAI-compat schema and parse
    tool_calls from the response."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def convert_to_gemini_tools(self, agent_tools: list) -> list:
        """AgentKthx tools already use an OpenAI-compatible schema via
        `to_openai_schema()` — pass through unchanged."""
        gemini_tools = []
        for tool in agent_tools:
            if hasattr(tool, "to_openai_schema"):
                gemini_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema(),
                })
            else:
                gemini_tools.append(self._convert_custom_tool(tool))
        return gemini_tools

    def _convert_custom_tool(self, tool) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }

    def handle_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool_calls from a non-streaming response."""
        tool_calls = []
        choices = response.get("choices") or []
        if not choices:
            return tool_calls
        msg = choices[0].get("message") or {}
        for tc in msg.get("tool_calls") or []:
            args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}   # let the tool decide how to handle
            tool_calls.append({
                "id": tc["id"],
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": args,
                },
            })
        return tool_calls

    def build_tool_result_message(
        self, tool_call_id: str, result: Any, *, is_error: bool = False
    ) -> dict:
        """Build the role='tool' message to send back to Gemini."""
        body = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": body,
        }
```

### Parallel Function Calling

Gemini 3.x natively supports parallel function calls — a single assistant message may contain multiple entries in `tool_calls[]`. AgentKthx's loop MUST:

1. Execute all tool calls concurrently (asyncio.gather or thread pool).
2. Append **one** `role: "tool"` message per tool call (NOT a single combined message).
3. Preserve `tool_call_id` ordering — Gemini matches tool results by ID, not by position.
4. Send the entire array of tool messages in the next `/chat/completions` request.

Failure to do (2) or (3) will produce `400 INVALID_ARGUMENT` with message `tool_call_id does not match any tool_call in the previous assistant message`.

### `tool_choice` Semantics

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Model decides whether to call tools |
| `"required"` | Model MUST call at least one tool |
| `"none"` | Model must NOT call tools (text output only) |
| `{"type": "function", "function": {"name": "X"}}` | Force a specific function |

Gemini maps `required` and forced-choice to its own `function_calling_config.mode` of `ANY`, and `auto` to `AUTO`. `none` is `NONE`.

---

## Streaming & Real-time Features

### Streaming Response Format (SSE)

```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gemini-3.8-flash","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gemini-3.8-flash","choices":[{"index":0,"delta":{"content":"Hello"}},"finish_reason":null]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gemini-3.8-flash","choices":[{"index":0,"delta":{"content":", how can I"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_xyz","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"location\":"}}]},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Toronto\"}"}}]},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":45,"completion_tokens":12,"total_tokens":57,"completion_tokens_details":{"reasoning_tokens":0}}}

data: [DONE]
```

### Thought-Signature Streaming Events

When `extra_body.google.thinking_config.include_thoughts=true`, Gemini emits additional delta chunks of type `thought_signature` interleaved with normal content deltas:

```
data: {"choices":[{"index":0,"delta":{"signature":"EpoGCpcGAXLI2nx/...","type":"thought_signature"},"event_type":"step.delta"}]}

data: {"choices":[{"index":0,"delta":{"summary":"Analyzing the user's request for weather data...","type":"thought_summary"},"event_type":"step.delta"}]}
```

AgentKthx should:
- Capture the most recent `signature` for each `index` and re-send it back in the next request's `extra_body.google.thinking_config.thought_signature` field when continuing a multi-turn conversation that needs reasoning state. Without this, the model will re-reason from scratch.
- Stream `summary` deltas to the UI as collapsible "thought" panels (do not stream them as the main assistant text — they are not the answer).

### Streaming Implementation

```python
import json
import urllib.request
from typing import Generator

class GeminiStreamHandler:
    def __init__(self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def stream_chat(self, payload: dict, *, timeout: int = 120) -> Generator[dict, None, None]:
        """Yield parsed SSE chunk dicts from /chat/completions with stream=True."""
        payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            buf = b""
            for chunk in iter(lambda: resp.read(1024), b""):
                buf += chunk
                while b"\n\n" in buf:
                    event_bytes, buf = buf.split(b"\n\n", 1)
                    for line in event_bytes.decode("utf-8", errors="replace").splitlines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            return
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
```

### Streaming Tool Call Accumulation

When streaming with tools, the assistant's tool calls arrive as deltas across multiple chunks. They must be accumulated by `index`:

```python
def accumulate_tool_call_deltas(chunks: list[dict]) -> list[dict]:
    """Merge streaming delta tool_calls into final tool_call objects."""
    accumulated: dict[int, dict] = {}
    for chunk in chunks:
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta["index"]
            slot = accumulated.setdefault(idx, {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
            if tc_delta.get("id"):
                slot["id"] = tc_delta["id"]
            if tc_delta.get("type"):
                slot["type"] = tc_delta["type"]
            fn = tc_delta.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
    return list(accumulated.values())
```

---

## Error Codes & Recovery

### Error Response Structure (OpenAI-compat format)

```json
{
    "error": {
        "message": "Resource has been exhausted (e.g. rate limit).",
        "type": "rate_limit_exceeded",
        "param": null,
        "code": "rate_limit_exceeded",
        "status": 429
    }
}
```

### Native (gRPC-style) Error Codes

Gemini's underlying errors come from a Google RPC layer and surface in the `error.code` field as canonical gRPC status strings. AgentKthx should map these to recovery actions:

| HTTP | gRPC code | Gemini internal | Recoverable | Retry strategy |
|------|-----------|-----------------|-------------|----------------|
| 400 | `INVALID_ARGUMENT` | Bad request schema | No | Fix request and retry manually |
| 401 | `UNAUTHENTICATED` | Invalid or expired key | No | Prompt user to refresh key |
| 403 | `PERMISSION_DENIED` | Standard key rejected, model not enabled for project, region blocked | No | Surface actionable migration message |
| 404 | `NOT_FOUND` | Unknown model or endpoint | No | Fall back to a known-good model (e.g. `gemini-3.8-flash`) |
| 408 | `DEADLINE_EXCEEDED` | Server-side timeout | Yes | Retry with backoff |
| 409 | `ABORTED` | Concurrent transaction conflict | Yes | Retry once |
| 429 | `RESOURCE_EXHAUSTED` | RPM/TPM/RPD/spend limit hit | Yes | Honor `Retry-After`; backoff with jitter |
| 500 | `INTERNAL` | Unexpected server error | Yes | Retry with exponential backoff |
| 503 | `UNAVAILABLE` | Service overloaded | Yes | Retry with longer backoff (≥30 s) |
| 504 | `DEADLINE_EXCEEDED` (gateway) | Upstream timeout | Yes | Retry with backoff |

### Comprehensive Error Handler

```python
import random
import time
import urllib.error
import json

class GeminiErrorHandler:
    ERROR_CODES = {
        400: {"recoverable": False, "actions": ["Validate request schema", "Check model name spelling"]},
        401: {"recoverable": False, "actions": ["Verify API key", "Re-export GEMINI_API_KEY"]},
        403: {"recoverable": False, "actions": ["Check if standard key needs migration to auth key", "Verify Generative Language API is enabled for the project"]},
        404: {"recoverable": False, "actions": ["Use a current model (gemini-3.8-flash, gemini-2.5-flash)"]},
        408: {"recoverable": True, "retry_after": 1, "actions": ["Retry with backoff"]},
        429: {"recoverable": True, "retry_after": "Retry-After header", "actions": ["Reduce RPM", "Implement client-side rate limiter"]},
        500: {"recoverable": True, "retry_after": 5, "actions": ["Retry with backoff"]},
        503: {"recoverable": True, "retry_after": 30, "actions": ["Retry with longer backoff", "Check status.ai.google.dev"]},
        504: {"recoverable": True, "retry_after": 5, "actions": ["Retry with backoff"]},
    }

    def handle_error(self, response_or_exc) -> dict:
        """Handle either an HTTPResponse, urllib HTTPError, or generic Exception."""
        if isinstance(response_or_exc, urllib.error.HTTPError):
            status = response_or_exc.code
            try:
                body = json.loads(response_or_exc.read().decode("utf-8"))
                msg = body.get("error", {}).get("message", str(body))
            except Exception:
                msg = str(response_or_exc)
        elif isinstance(response_or_exc, Exception):
            return {"code": "NETWORK_ERROR", "message": str(response_or_exc),
                    "recoverable": True, "actions": ["Check connectivity", "Increase timeout"]}
        else:
            status = getattr(response_or_exc, "status", 500)
            msg = str(response_or_exc)

        info = self.ERROR_CODES.get(status, {"recoverable": False, "actions": ["Check logs"]})
        return {
            "code": status,
            "message": msg,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"],
        }

    def should_retry(self, error: dict, attempt: int, max_attempts: int) -> bool:
        if not error["recoverable"]:
            return False
        if attempt >= max_attempts:
            return False
        return error["code"] in (408, 429, 500, 503, 504, "NETWORK_ERROR")

    def calculate_delay(self, error: dict, attempt: int) -> float:
        """Exponential backoff with full jitter."""
        base = error.get("retry_after") or 1
        if base == "Retry-After header":
            base = 1
        elif not isinstance(base, (int, float)):
            base = 1
        # Cap exponential growth at 60 seconds
        return min(60, float(base) * (2 ** attempt)) + random.uniform(0, 1)
```

### Retry Logic Implementation

```python
class GeminiRetryHandler:
    def __init__(self, max_attempts: int = 4, base_delay: float = 1.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.error_handler = GeminiErrorHandler()

    def execute_with_retry(self, request_fn, **kwargs):
        """Execute an HTTP request with Google-recommended exponential backoff.

        Google's own SDK uses: 4 retries, ~1 s initial delay, 60 s max delay.
        """
        last_error = None
        for attempt in range(self.max_attempts):
            try:
                response = request_fn(**kwargs)
                # Inspect status — urllib raises on 4xx/5xx but our request_fn
                # may also return a response object directly.
                if hasattr(response, "status") and response.status == 200:
                    return response
                if hasattr(response, "status"):
                    error = self.error_handler.handle_error(response)
                else:
                    return response
            except Exception as exc:
                error = self.error_handler.handle_error(exc)

            if not self.error_handler.should_retry(error, attempt, self.max_attempts):
                raise RuntimeError(f"Gemini call failed: {error['message']}")

            delay = self.error_handler.calculate_delay(error, attempt)
            time.sleep(delay)
            last_error = error

        raise RuntimeError(f"Gemini retries exhausted. Last error: {last_error}")
```

---

## Rate Limiting & Concurrency

### Three Rate Limit Dimensions

Gemini enforces limits across three orthogonal axes:

| Dimension | Abbrev | Typical Free-tier value | Reset cadence |
|-----------|--------|-------------------------|---------------|
| Requests per minute | RPM | 5 | Rolling 60 s |
| Tokens per minute (input) | TPM | 250,000 | Rolling 60 s |
| Requests per day | RPD | 1,500 | Midnight Pacific time |
| Spend rate limit (paid tiers) | $/10min | $10 (Tier 1) | Rolling 10 min |

**Important**: limits are per-project, not per-API-key. All keys in the same Google Cloud project share the same budget.

### Free Tier Reality Check (Sep 2026)

The Gemini free tier is genuinely free (no credit card, no $5 trial) but heavily rate-limited. Empirically observed limits for the most common chat models:

| Model | Free RPM | Free TPM | Free RPD | Notes |
|-------|----------|----------|----------|-------|
| `gemini-3.8-flash` | 5 | 250,000 | 1,500 | Best default for AgentKthx |
| `gemini-3.5-flash` | 5 | 250,000 | 1,500 | Stable fallback |
| `gemini-2.5-flash` | 10 | 250,000 | 500 | Legacy — only available if project used it before |
| `gemini-2.5-pro` | 5 | 250,000 | 100 | Limited RPD on Pro |

### Spend-Based Rate Limits (Paid Tiers Only)

| Tier | Spend cap per 10 min | Cumulative cap | Qualification |
|------|----------------------|----------------|---------------|
| Free | N/A | N/A | Active project or free trial |
| Tier 1 | $10 | $250 | Set up linked billing account |
| Tier 2 | $50 | $2,000 | $100 paid + 3 days from first payment |
| Tier 3 | $200 | $20,000 – $100,000+ | $1,000 paid + 30 days from first payment |

Hitting the spend limit returns `429 RESOURCE_EXHAUSTED` with `error.type = "spend_limit_exceeded"`. Distinguish from regular rate-limit errors and surface a UI message like "Daily spending cap reached — wait 10 minutes or upgrade to Tier 2".

### Rate Limit Headers

Gemini does not always return explicit `Retry-After` or `X-RateLimit-*` headers on the OpenAI-compat endpoint. When present:

```
Retry-After: 30
X-RateLimit-Limit-Requests: 5
X-RateLimit-Remaining-Requests: 3
X-RateLimit-Reset-Requests: 12s
```

When absent, fall back to local rate-limit tracking based on observed 429s.

### Client-Side Rate Limiter

```python
import threading
import time
from collections import deque

class GeminiClientRateLimiter:
    """Token-bucket-ish limiter that respects RPM, TPM, and RPD simultaneously."""

    def __init__(self, rpm: int = 5, tpm: int = 250_000, rpd: int = 1500):
        self.rpm = rpm
        self.tpm = tpm
        self.rpd = rpd
        self._lock = threading.Lock()
        self._req_times = deque()      # timestamps within last 60s
        self._token_times = deque()    # (ts, tokens) within last 60s
        self._day_count = 0
        self._day_reset_at = self._next_midnight_pt()

    @staticmethod
    def _next_midnight_pt() -> float:
        """Return Unix timestamp of next midnight Pacific."""
        import datetime as dt, zoneinfo
        pt = zoneinfo.ZoneInfo("America/Los_Angeles")
        now = dt.datetime.now(pt)
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.timestamp()

    def acquire(self, input_tokens: int = 0) -> float:
        """Block until a request can be made. Returns wait time (s)."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.time()
                # Drop entries older than 60s
                while self._req_times and now - self._req_times[0] > 60:
                    self._req_times.popleft()
                while self._token_times and now - self._token_times[0][0] > 60:
                    self._token_times.popleft()
                # Roll over daily counter
                if now >= self._day_reset_at:
                    self._day_count = 0
                    self._day_reset_at = self._next_midnight_pt()

                if (len(self._req_times) < self.rpm
                        and sum(t for _, t in self._token_times) + input_tokens <= self.tpm
                        and self._day_count < self.rpd):
                    self._req_times.append(now)
                    self._token_times.append((now, input_tokens))
                    self._day_count += 1
                    return waited

                # Compute sleep time
                sleep_candidates = []
                if len(self._req_times) >= self.rpm:
                    sleep_candidates.append(60 - (now - self._req_times[0]))
                if self._day_count >= self.rpd:
                    sleep_candidates.append(self._day_reset_at - now)
                sleep_for = max(0.0, min(sleep_candidates) if sleep_candidates else 0.5)
            time.sleep(sleep_for)
            waited += sleep_for
```

### Concurrency Management

For the Free tier, you should rarely run more than 1 concurrent request — 5 RPM is exhausted instantly otherwise. On paid tiers:

```python
import asyncio

class GeminiConcurrencyManager:
    def __init__(self, max_concurrent: int = 1):
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.active = 0

    async def __aenter__(self):
        await self.semaphore.acquire()
        self.active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.active -= 1
        self.semaphore.release()

    def status(self) -> dict:
        return {
            "active": self.active,
            "max": self.semaphore._value,
            "available": max(0, self.semaphore._value - self.active),
        }
```

---

## Multimodal Content Handling

Gemini 3.x and 2.5 are natively multimodal. The OpenAI-compat endpoint accepts the standard `content: array` shape with these part types:

### Supported Content Part Types

| `type` | Purpose | Fields | Supported on |
|--------|---------|--------|--------------|
| `text` | Plain text | `text: string` | All chat models |
| `image_url` | Image input (URL or base64 data URI) | `image_url.url: string` | 2.5+, 3.x |
| `input_audio` | Audio input (base64) | `input_audio.data: string`, `input_audio.format: "wav"\|"mp3"\|"flac"\|"ogg"` | 2.5+ |
| `input_text` | Aliased to `text` in some SDKs | — | — |
| `file` | File reference (PDF, video) uploaded via Files API | `file.file_id: string` | 2.5+ |

### Image Content Validation

```python
class GeminiMultimodalHandler:
    CONTENT_TYPE_VALIDATION = {
        "text": {"max_length": 1_048_576, "allowed_types": [str]},
        "image_url": {
            "max_size_mb": 20,                    # for base64 inline; larger via Files API
            "max_pixels": 4096 * 4096,             # ~16MP
            "allowed_formats": ["png", "jpeg", "jpg", "webp", "gif", "heic"],
        },
        "input_audio": {
            "max_size_mb": 20,
            "allowed_formats": ["wav", "mp3", "flac", "ogg"],
            "max_duration_seconds": 9_000,         # ~2.5 hours
        },
        "file": {
            "max_size_mb": 2_000,                  # 2 GB via Files API
            "max_pages_pdf": 1000,
            "allowed_formats": ["pdf", "txt", "md", "csv", "json", "jsonl",
                                "docx", "xlsx", "pptx", "mp4", "mov", "avi", "mkv", "webm"],
        },
    }

    def validate_content(self, content: list) -> dict:
        errors = []
        for i, item in enumerate(content):
            if not isinstance(item, dict):
                errors.append(f"Item {i}: must be a dict")
                continue
            ct = item.get("type")
            if ct not in self.CONTENT_TYPE_VALIDATION:
                errors.append(f"Item {i}: unknown type '{ct}'")
                continue
            # type-specific checks elided for brevity — full implementation
            # should mirror ZaiMultimodalHandler in zai.py
        return {"valid": not errors, "errors": errors}

    def build_vision_request(self, image_paths: list[str], prompt: str, model: str = "gemini-3.8-flash") -> dict:
        import base64, mimetypes
        content = []
        for path in image_paths:
            mime = mimetypes.guess_type(path)[0] or "image/jpeg"
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        content.append({"type": "text", "text": prompt})
        return {"model": model, "messages": [{"role": "user", "content": content}]}

    def build_pdf_request(self, file_uri: str, prompt: str, model: str = "gemini-3.8-flash") -> dict:
        """Use Files API first to upload the PDF, then reference by file_id."""
        return {
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "file", "file": {"file_id": file_uri}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
```

### Files API (Large File Upload)

Files > 20 MB must be uploaded via the Files API first; the returned `file_id` is then referenced in the message content. The OpenAI-compat layer does NOT expose `/files` — you must use the native `POST /upload/v1beta/files` endpoint:

```python
def upload_file(path: str, mime: str = "application/pdf") -> str:
    """Upload to Gemini's Files API. Returns file_id like 'files/abc123'."""
    import urllib.request, mimetypes, os, json
    size = os.path.getsize(path)
    boundary = "----agentkthx" + str(int(time.time()))
    with open(path, "rb") as f:
        file_bytes = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url="https://generativelanguage.googleapis.com/upload/v1beta/files",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['GEMINI_API_KEY']}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["file"]["name"]   # e.g. "files/abc-123-xyz"
```

---

## Thinking & Reasoning Configuration

This is Gemini's distinctive capability vs. ZAI/OpenRouter — every Gemini 2.5+ model produces internal reasoning tokens. AgentKthx should expose this.

### Parameter Mapping

| `reasoning_effort` (OpenAI-style) | `thinking_level` (Gemini 3.x) | `thinking_budget` (Gemini 2.5) |
|-----------------------------------|-------------------------------|-------------------------------|
| `minimal` | `minimal` | `1024` |
| `low` | `low` | `1024` |
| `medium` | `medium` | `8192` |
| `high` | `high` | `24576` |
| `none` | *(not supported on 3.x)* | `0` |

`reasoning_effort` and `thinking_level`/`thinking_budget` **cannot** be set simultaneously — the request will return `400 INVALID_ARGUMENT`.

### Sending Thinking Config via `extra_body`

For full Gemini-native control, pass `thinking_config` inside `extra_body.google`:

```python
def build_thinking_request(model: str, messages: list, level: str = "low",
                           include_thoughts: bool = True) -> dict:
    """Build a request with native Gemini thinking config."""
    request = {
        "model": model,
        "messages": messages,
        "extra_body": {
            "google": {
                "thinking_config": {
                    "thinking_level": level,        # for 3.x
                    # OR for 2.5: "thinking_budget": 8192,
                    "include_thoughts": include_thoughts,
                }
            }
        },
    }
    return request
```

### Disabling Thinking

| Model family | How to disable |
|--------------|----------------|
| Gemini 2.5 | Set `reasoning_effort: "none"` (top-level) OR `thinking_budget: 0` |
| Gemini 3.x | **Cannot be fully disabled.** Best you can do is `reasoning_effort: "minimal"` / `thinking_level: "minimal"` |

### Thought Summaries

When `include_thoughts=true`, the response includes:

- **Non-streaming**: `choices[0].message.reasoning` contains the full thought summary as a string.
- **Streaming**: Interleaved delta chunks with `delta.type = "thought_summary"` (text) and `delta.type = "thought_signature"` (opaque token to pass back for stateful continuation).

### Thought Signatures (Stateful Continuation)

To continue a multi-turn conversation without the model re-reasoning from scratch:

```python
def continue_with_signature(messages: list, signature: str, level: str = "low") -> dict:
    return {
        "model": "gemini-3.8-flash",
        "messages": messages,
        "extra_body": {
            "google": {
                "thinking_config": {
                    "thinking_level": level,
                    "include_thoughts": True,
                    "thought_signature": signature,   # pass back the latest
                }
            }
        },
    }
```

Without `thought_signature`, each turn re-derives reasoning from scratch — roughly 2-3× more expensive in token usage for multi-turn agent loops.

### `reasoning_tokens` in Usage

```
"usage": {
    "prompt_tokens": 1500,
    "completion_tokens": 800,
    "total_tokens": 2300,
    "completion_tokens_details": {
        "reasoning_tokens": 650
    }
}
```

`reasoning_tokens` is **part of** `completion_tokens`, not in addition to it. Billed at the output-token rate. AgentKthx should surface this in any token-budget UI so users understand why their visible output (150 tokens) doesn't match their billed output (800 tokens).

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType, ToolSupportLevel

class GeminiBackend(OpenAICompatibleBackend):
    """Gemini API backend via OpenAI-compatible endpoint."""

    BACKEND_TYPE = BackendType.CLOUD
    NAME = "gemini"

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    DEFAULT_MODEL = "gemini-3.8-flash"
    FALLBACK_MODEL = "gemini-3.5-flash"        # if 3.8 is rate-limited
    ENV_API_KEY = "GEMINI_API_KEY"
    ENV_BASE_URL = "GEMINI_BASE_URL"
    ENV_FREE_ONLY = "GEMINI_FREE_ONLY"         # if true, restrict to free-tier models

    FREE_TIER_MODELS = {
        "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
        "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
        "gemini-2.5-flash", "gemini-2.5-flash-lite",
    }

    def __init__(self, config, base_url: str | None = None):
        super().__init__(config=config, base_url=base_url or self.DEFAULT_BASE_URL)
        self.api_key = os.environ.get(self.ENV_API_KEY, "")
        if not self.api_key:
            # also accept GOOGLE_API_KEY as a fallback, mirroring Google's SDKs
            self.api_key = os.environ.get("GOOGLE_API_KEY", "")
        self.rate_limiter = GeminiClientRateLimiter()
        self.error_handler = GeminiErrorHandler()
        self.retry_handler = GeminiRetryHandler()

    def _auth_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        request = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "max_tokens": kwargs.get("max_tokens") or kwargs.get("num_predict"),
            "stream": kwargs.get("stream", False),
            "seed": kwargs.get("seed"),
            "service_tier": kwargs.get("service_tier", "standard"),
        }
        # Strip None values to keep payload minimal
        request = {k: v for k, v in request.items() if v is not None}

        if "tools" in kwargs:
            request["tools"] = kwargs["tools"]
            request["tool_choice"] = kwargs.get("tool_choice", "auto")

        # Thinking — must use either reasoning_effort OR thinking_config,
        # never both.
        if "reasoning_effort" in kwargs:
            request["reasoning_effort"] = kwargs["reasoning_effort"]
        elif "thinking_config" in kwargs:
            request["extra_body"] = {
                "google": {"thinking_config": kwargs["thinking_config"]}
            }

        if kwargs.get("include_thoughts"):
            request.setdefault("extra_body", {}).setdefault("google", {}) \
                   .setdefault("thinking_config", {})["include_thoughts"] = True

        if kwargs.get("cached_content"):
            request.setdefault("extra_body", {}).setdefault("google", {}) \
                   ["cached_content"] = kwargs["cached_content"]

        return request
```

### 2. Model Auto-detection

```python
def auto_detect_gemini_config(model_name: str) -> dict:
    config = {
        "max_tokens": 65_536,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 40,
        "supports_streaming": True,
        "supports_tools": True,
        "supports_thinking": False,
        "supports_multimodal_input": True,
        "min_cache_tokens": 4096,
    }
    if model_name.startswith("gemini-3."):
        config.update({
            "supports_thinking": True,
            "thinking_levels": ["minimal", "low", "medium", "high"],
            "thinking_can_be_disabled": False,
            "supports_thought_signatures": True,
        })
    elif model_name.startswith("gemini-2.5"):
        config.update({
            "supports_thinking": True,
            "thinking_budget_range": (0, 24576),
            "thinking_can_be_disabled": True,
            "supports_thought_signatures": False,
            "min_cache_tokens": 2048,
        })
    elif model_name.startswith("gemini-2.0"):
        config.update({"supports_thinking": False, "supports_multimodal_input": True})
    return config
```

### 3. Tool Schema Compatibility

AgentKthx tools that already implement `to_openai_schema()` work unchanged with Gemini. The only edge case is **enum-typed parameters** — Gemini is stricter than OpenAI and will return `400 INVALID_ARGUMENT` if a tool call argument value is not in the schema's `enum` list. Validate before dispatching to the tool.

### 4. Context Management

```python
class GeminiContextManager:
    """Trim message history to fit within the 1M-token window (2M for Pro)."""

    def __init__(self, model_name: str):
        self.config = auto_detect_gemini_config(model_name)
        # Pro models get 2M, others get 1M
        self.max_context = 2_097_152 if "pro" in model_name else 1_048_576
        self.window: list[dict] = []

    def add_message(self, role: str, content):
        msg = {"role": role, "content": content}
        self.window.append(msg)
        # Drop oldest user/assistant pairs until we're under budget
        while self._total_tokens() > self.max_context * 0.9 and len(self.window) > 2:
            self.window.pop(0)

    def _total_tokens(self) -> int:
        # Rough approximation: ~4 chars per token
        total = 0
        for msg in self.window:
            c = msg.get("content")
            if isinstance(c, str):
                total += len(c) // 4
            elif isinstance(c, list):
                for part in c:
                    if part.get("type") == "text":
                        total += len(part.get("text", "")) // 4
                    elif part.get("type") == "image_url":
                        total += 258   # Gemini's per-image token overhead
        return total
```

### 5. Integration Test Plan

Tests to add under `tests/test_gemini_backend.py`:

```python
import os, pytest
from agentkthx import Agent

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
pytestmark = pytest.mark.skipif(not GEMINI_API_KEY, reason="no Gemini key")

def test_basic_chat():
    agent = Agent(model="gemini-3.8-flash", backend="gemini")
    r = agent.run("What is 15 * 8?")
    assert "120" in r

def test_function_calling():
    agent = Agent(model="gemini-3.8-flash", backend="gemini", tools=["calculator"])
    r = agent.run("Compute 23 * 17")
    # Gemini reliably calls the calculator tool
    assert r.tool_calls or r.text

def test_streaming():
    agent = Agent(model="gemini-3.8-flash", backend="gemini")
    chunks = list(agent.stream("Count from 1 to 5"))
    assert len(chunks) >= 2

def test_thinking_disabled_on_2_5():
    agent = Agent(model="gemini-2.5-flash", backend="gemini",
                  reasoning_effort="none")
    r = agent.run("Hi")
    assert r.usage.completion_tokens_details.reasoning_tokens == 0

def test_thinking_cannot_be_disabled_on_3_x():
    agent = Agent(model="gemini-3.8-flash", backend="gemini",
                  reasoning_effort="none")
    with pytest.raises(Exception, match="thinking"):
        agent.run("Hi")

def test_multimodal_vision(tmp_path):
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 100)   # minimal PNG header
    agent = Agent(model="gemini-3.8-flash", backend="gemini")
    r = agent.run(["What's in this image?", {"type": "image_url", "image_url": {"url": f"file://{img}"}}])
    # Should NOT crash with 400 — Gemini accepts the image
    assert r.text

def test_429_backoff():
    # With a free-tier key at 5 RPM, sending 10 rapid requests should
    # trigger at least one 429. The backend should recover via backoff.
    agent = Agent(model="gemini-3.8-flash", backend="gemini")
    for i in range(10):
        r = agent.run(f"Say the number {i}")
        assert r.text
```

---

## Troubleshooting Matrix

### Issue Detection & Solutions

| Symptom | Possible Cause | Solution | Priority |
|---------|----------------|----------|----------|
| `403 PERMISSION_DENIED` "unrestricted standard key rejected" | Standard key without restrictions | Tell user to migrate to auth key (or apply request-origin restrictions) | Critical |
| `404 NOT_FOUND` on `gemini-2.5-flash` | Project never used 2.5 before | Fall back to `gemini-3.8-flash` | High |
| `429 RESOURCE_EXHAUSTED` `rate_limit_exceeded` | RPM/TPM/RPD hit | Honor `Retry-After`; implement client-side limiter | High |
| `429` with `spend_limit_exceeded` | Tier 1/2/3 spend cap reached | Surface "wait 10 min" message; do NOT auto-retry rapidly | High |
| `400 INVALID_ARGUMENT` "thinking_level and reasoning_effort cannot both be set" | Conflicting thinking params | Pick one — recommend `reasoning_effort` for OpenAI parity | Medium |
| `400 INVALID_ARGUMENT` "tool_call_id does not match" | Tool results sent out of order or merged | Send one `role: "tool"` message per call, with correct `tool_call_id` | High |
| Empty `content` with `finish_reason: "stop"` | Recitation filter triggered | Rephrase the prompt; do NOT retry with the same prompt | Medium |
| Higher token usage than expected | `reasoning_tokens` not visible in UI | Surface `completion_tokens_details.reasoning_tokens` separately in the UI | Low |
| Slow first token (TTFT > 5s) | Thinking model warming up | Use `thinking_level: "minimal"` for low-latency use cases | Low |
| Context overflow errors at <1M tokens | Multimodal parts inflate token count (images = 258 tokens each, PDFs = ~1500/page) | Reduce image count or trim history | Medium |
| Streaming stalls after tool_calls | Thought-signature accumulation bug | Ensure handler captures every `step.delta` event, not just content deltas | High |

### Debug Mode Implementation

```python
class GeminiDebugHandler:
    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode
        self.debug_log: list[dict] = []

    def log_request(self, request: dict):
        if not self.debug_mode:
            return
        self.debug_log.append({
            "timestamp": time.time(),
            "type": "request",
            "model": request.get("model"),
            "messages_count": len(request.get("messages", [])),
            "tools_count": len(request.get("tools", [])),
            "reasoning_effort": request.get("reasoning_effort"),
            "thinking_config": (request.get("extra_body") or {}).get("google", {}).get("thinking_config"),
            "service_tier": request.get("service_tier"),
        })

    def log_response(self, response: dict):
        if not self.debug_mode:
            return
        usage = response.get("usage", {})
        self.debug_log.append({
            "timestamp": time.time(),
            "type": "response",
            "id": response.get("id"),
            "model": response.get("model"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "finish_reason": (response.get("choices") or [{}])[0].get("finish_reason"),
            "service_tier": response.get("service_tier"),
        })

    def summary(self) -> dict:
        requests = [e for e in self.debug_log if e["type"] == "request"]
        responses = [e for e in self.debug_log if e["type"] == "response"]
        return {
            "total_requests": len(requests),
            "total_responses": len(responses),
            "success_rate": len(responses) / max(len(requests), 1),
            "avg_completion_tokens": sum(r.get("completion_tokens", 0) for r in responses) / max(len(responses), 1),
            "avg_reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in responses) / max(len(responses), 1),
            "cache_hit_rate": sum(r.get("cached_tokens", 0) > 0 for r in responses) / max(len(responses), 1),
            "recent": self.debug_log[-10:],
        }
```

### Performance Monitoring

```python
class GeminiPerformanceMonitor:
    def __init__(self):
        self.metrics = {
            "request_count": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_time": 0.0,
            "error_count": 0,
            "rate_limit_hits": 0,
            "start_time": time.time(),
        }

    def record(self, *, prompt_tokens: int, completion_tokens: int,
               reasoning_tokens: int = 0, cached_tokens: int = 0,
               duration: float, success: bool = True,
               rate_limited: bool = False):
        self.metrics["request_count"] += 1
        self.metrics["total_prompt_tokens"] += prompt_tokens
        self.metrics["total_completion_tokens"] += completion_tokens
        self.metrics["total_reasoning_tokens"] += reasoning_tokens
        self.metrics["cached_tokens"] += cached_tokens
        self.metrics["total_time"] += duration
        if not success:
            self.metrics["error_count"] += 1
        if rate_limited:
            self.metrics["rate_limit_hits"] += 1

    def stats(self) -> dict:
        uptime = time.time() - self.metrics["start_time"]
        rc = max(self.metrics["request_count"], 1)
        return {
            "requests_per_minute": self.metrics["request_count"] / max(uptime / 60, 1),
            "tokens_per_second": (self.metrics["total_prompt_tokens"]
                                   + self.metrics["total_completion_tokens"]) / max(uptime, 1),
            "avg_prompt_tokens": self.metrics["total_prompt_tokens"] / rc,
            "avg_completion_tokens": self.metrics["total_completion_tokens"] / rc,
            "avg_reasoning_tokens": self.metrics["total_reasoning_tokens"] / rc,
            "reasoning_overhead_pct": (
                self.metrics["total_reasoning_tokens"]
                / max(self.metrics["total_completion_tokens"], 1) * 100
            ),
            "cache_hit_rate": self.metrics["cached_tokens"] / max(self.metrics["total_prompt_tokens"], 1),
            "error_rate": self.metrics["error_count"] / rc,
            "rate_limit_rate": self.metrics["rate_limit_hits"] / rc,
            "uptime_seconds": uptime,
        }
```

---

## Appendix: OpenAI-Compat Limitations (as of Sep 2026)

Per Google's docs, the OpenAI-compat layer is still in **beta** and has these gaps:

| Feature | Status | Workaround |
|---------|--------|------------|
| `extra_body.google.thinking_config` | ✅ Supported | — |
| `extra_body.google.cached_content` | ✅ Supported | — |
| `service_tier: "flex"\|"priority"` | ✅ Supported | — |
| Batch API file upload/download via OpenAI SDK | ❌ Not supported | Use `@google/genai` SDK for files; use OpenAI SDK only for batch creation/retrieval |
| Native `interactions` endpoint | ❌ Not exposed via OpenAI-compat | Use `@google/genai` directly |
| Live API (real-time audio) | ❌ Not in OpenAI-compat | Use WebSocket client with native API |
| Computer Use model | ❌ Not in OpenAI-compat | Use native API |
| `logprobs` | ❌ Not returned by Gemini | Returned as `null` in response |
| `n > 1` (multiple candidates) | ⚠️ Partial — `n: 1` only via OpenAI-compat | Use native API for `n: 1-8` |

When AgentKthx needs any of the unsupported features, the backend should switch to a separate native-API code path rather than attempting to wrap them in OpenAI-compat calls.

---

## Appendix: Region Availability

Gemini API is available in 200+ countries and territories. Notable absences (users in these regions will get `403` regardless of key type):

- China (mainland)
- Hong Kong (varies — try Gemini Enterprise on Vertex AI)
- Russia
- Belarus
- Iran
- North Korea
- Cuba
- Syria
- Crimea / Donetsk / Luhansk regions (Ukraine)

A full current list is maintained at <https://ai.google.dev/gemini-api/docs/available-regions>.

---

## Appendix: Reasoning Effort → Token Cost Reference

Approximate token-cost multipliers vs. a non-thinking baseline:

| Effort | Reasoning tokens per turn | Latency | Use case |
|--------|---------------------------|---------|----------|
| `minimal` | 0–500 | Fastest | Classification, extraction, simple Q&A |
| `low` | 500–2,000 | Fast | Chat, basic tool use |
| `medium` | 2,000–8,000 | Medium | Multi-step reasoning, code generation |
| `high` | 8,000–24,576 | Slow | Math, complex planning, agent loops |
| (default — no param) | Model-dependent | Varies | Recommended for first-time users |

---

This technical reference provides the implementation details needed to add a Gemini backend to AgentKthx. The OpenAI-compat endpoint is the primary integration surface; native API features (Files, Live, Computer Use, Interactions) are listed for parity awareness but should only be wired in if and when AgentKthx needs capabilities the OpenAI-compat layer cannot express.
