# Cloudflare Workers AI API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/ + https://developers.cloudflare.com/workers-ai/get-started/rest-api/ + https://developers.cloudflare.com/workers-ai/models/ (verified Oct 2026)
> **Free-tier verification**: 10,000 neurons per day on the free plan, no credit card required, UTC daily reset. Source: [ayautomate.com](https://www.ayautomate.com) + [bagrounds.org (Mar 2026)](https://bagrounds.org) + [Cloudflare Workers AI pricing docs](https://developers.cloudflare.com/workers-ai/platform/pricing/)
> **Live-behavior notes**: 2026-10-07 — Cloudflare offers TWO distinct API surfaces: (1) native `/accounts/{id}/ai/run/{model}` REST endpoint (Cloudflare-shaped response), (2) OpenAI-compatible `/accounts/{id}/ai/v1/chat/completions` endpoint (OpenAI-shaped response). AgentKthx should target the OpenAI-compatible surface to inherit the existing `openai_compat` plumbing. Auth model differs from other OpenAI-compat providers (account ID embedded in URL path, not just bearer token).
> **Primary focus**: OpenAI-compatible endpoint at `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1` for chat completions + embeddings. Native `/ai/run/{model}` endpoint is documented in Appendix A.
> **Last Updated**: 2026-10-07
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Model Catalog & Specifications](#model-catalog--specifications)
4. [Function Calling Implementation](#function-calling-implementation)
5. [Streaming & Real-time Features](#streaming--real-time-features)
6. [Error Codes & Recovery](#error-codes--recovery)
7. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
8. [Multimodal Content Handling](#multimodal-content-handling)
9. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
10. [Troubleshooting Matrix](#troubleshooting-matrix)
11. [Appendix: Neuron Economy & Free Access](#appendix-neuron-economy--free-access)
12. [Appendix: Native /ai/run/{model} Endpoint](#appendix-native-airunmodel-endpoint)
13. [Appendix: OpenAI Wire-Format Deltas](#appendix-openai-wire-format-deltas)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — OpenAI-compatible surface (PRIMARY for AgentKthx)
BASE_URL = "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1"

# Endpoints (OpenAI-compatible)
CHAT_COMPLETIONS = "/chat/completions"
EMBEDDINGS        = "/embeddings"
RESPONSES         = "/responses"  # GPT-OSS models only, stream:false only

# Native Cloudflare-shaped surface (legacy/alternative)
NATIVE_RUN_URL    = "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{model}"

# Account dash (where you obtain the account ID + API token)
DASH_URL          = "https://dash.cloudflare.com/?to=/:account/ai/workers-ai"
```

Unlike most OpenAI-compatible providers, Cloudflare embeds the **account ID in the URL path** rather than deriving it from the API key. This means:
- The base URL is per-account (`{ACCOUNT_ID}` is a 32-hex-char string)
- The same API token can be used across multiple accounts (if scoped to multiple)
- AgentKthx's `is_local_base_url` heuristic correctly classifies `api.cloudflare.com` as remote

### Authentication Headers

```python
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer {CLOUDFLARE_API_TOKEN}",  # NOT API_KEY — token must have Workers AI - Read AND Edit
}
```

### Key Types

Cloudflare Workers AI uses Cloudflare's standard API token system, not Workers-AI-specific keys:

| Key type | Where obtained | Permissions needed | What it can do |
|----------|----------------|--------------------|----------------|
| Workers AI API Token | Dashboard → Workers AI → Use REST API → Create Workers AI API Token | Prefilled: Workers AI - Read + Edit | Standard chat/embeddings/vision. **Recommended for AgentKthx.** |
| Custom API Token | Dashboard → My Profile → API Tokens → Create Token | Must include `Workers AI:Read` AND `Workers AI:Edit` | Same as above, but scoped to specific account(s) |
| Global API Key | Dashboard → My Profile → API Tokens | Account-level, all products | Not recommended — too broad. Use a scoped token instead. |

**AgentKthx guidance**: Use a Workers AI-scoped token (the prefilled "Create Workers AI API Token" button). Read the token from `CLOUDFLARE_API_KEY` env var, and the account ID from `CLOUDFLARE_ACCOUNT_ID`. Never embed either in prompt text, logs, or plugin manifests.

### Required Environment Variables

```bash
# Mandatory
export CLOUDFLARE_API_KEY="your-api-token-here"        # Bearer token value
export CLOUDFLARE_ACCOUNT_ID="your-32-hex-account-id"  # From dashboard

# Optional
export CLOUDFLARE_FREE_ONLY=1   # AgentKthx-style free-only enforcement
```

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 100MB (Cloudflare Workers limit)
- **Timeout**: 30 seconds default; configurable up to 5 minutes for streaming
- **HTTP method**: `POST` for inference, `GET` for none currently (no `/v1/models` on the OpenAI-compat path — use native endpoint)

---

## Request/Response Structure

### Complete Request Schema (OpenAI-compatible)

```json
{
    "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "messages": [
        {
            "role": "system|user|assistant|tool",
            "content": "string|array"
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "max_tokens": 2048,
    "stream": false,
    "stop": ["</s>"],
    "seed": 42,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        }
    ],
    "tool_choice": "auto",
    "response_format": {"type": "text|json_object"},
    "options": {
        "rejectIfBusy": false
    }
}
```

### Cloudflare-Specific Extension: `options.rejectIfBusy`

Cloudflare exposes a custom `options.rejectIfBusy` flag in the top-level request body. When `true`, the request fails immediately if Cloudflare's capacity is exhausted, rather than waiting in a queue. Useful for latency-sensitive agentic workflows.

```python
# Fail-fast for capacity issues
body["options"] = {"rejectIfBusy": True}
```

The OpenAI SDK passes through unknown fields, so this works with the standard OpenAI client. Other clients that strip unknown fields silently drop this option (no error, just no effect).

### Response Schema

```json
{
    "id": "chatcmpl-1234567890",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
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
            "finish_reason": "stop|tool_calls|length|content_filter"
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 70
    }
}
```

### Differences from vanilla OpenAI

- **`top_k`** is NOT supported (Cloudflare'sWorkers AI uses a fixed top-k internally)
- **`repetition_penalty`** is NOT supported on the OpenAI-compat path (it IS supported on the native `/ai/run/{model}` endpoint via `repetition_penalty`)
- **`seed`** is supported on most Llama/Qwen models — some older models ignore it silently
- **`response_format`**: `json_object` supported on most chat models; `json_schema` not yet supported via the OpenAI-compat path (use native endpoint with `response_format: {type: "json_schema", schema: {...}}` instead)
- **`tools`** supported on most chat models; see Function Calling section
- **No `/v1/models` endpoint on the OpenAI-compat path** — must use the native `/ai/models` endpoint to enumerate the catalog. AgentKthx should hardcode a seed list and refresh via probe (see Implementation Notes).

---

## Model Catalog & Specifications

### Model ID Format

Cloudflare Workers AI model IDs follow the `@cf/<author>/<model-name>` convention:

```
@cf/meta/llama-3.3-70b-instruct-fp8-fast
@cf/mistralai/mistral-7b-instruct-v0.3
@cf/qwen/qwen2.5-coder-32b-instruct
@cf/deepseek-ai/deepseek-r1-distill-qwen-32b
@cf/openai/gpt-oss-120b
@cf/meta/llama-3.2-11b-vision-instruct
@cf/baai/bge-large-en-v1.5  # embeddings
```

### Model Metadata Structure

```python
MODEL_CONFIGS = {
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": {
        "context_length": 131072,
        "max_output_tokens": 8192,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "supports_json_mode": True,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "llama-3",
        "neurons_per_request": "~500-2000 (varies by input length)",
        "tier": "flagship",
        "fp_quantization": "fp8"
    },
    "@cf/meta/llama-3.1-8b-instruct": {
        "context_length": 131072,
        "max_output_tokens": 4096,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "llama-3",
        "neurons_per_request": "~50-200",
        "tier": "standard"
    },
    "@cf/mistralai/mistral-7b-instruct-v0.3": {
        "context_length": 32768,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "mistral",
        "neurons_per_request": "~50-150",
        "tier": "standard"
    },
    "@cf/qwen/qwen2.5-coder-32b-instruct": {
        "context_length": 32768,
        "max_output_tokens": 8192,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "qwen-coder",
        "neurons_per_request": "~200-800",
        "tier": "flagship"
    },
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_thinking": True,
        "supports_streaming": True,
        "supports_function_calling": False,  # R1 distill models don't support tools
        "supports_multimodal": False,
        "temperature_default": 0.6,
        "top_p_default": 0.95,
        "family": "deepseek-r-distill",
        "neurons_per_request": "~200-2000 (varies with thinking length)",
        "tier": "reasoning"
    },
    "@cf/openai/gpt-oss-120b": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_streaming": False,  # Responses API only, stream:false only
        "supports_function_calling": True,
        "supports_multimodal": False,
        "supports_responses_api": True,  # unique to GPT-OSS on CF
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "gpt-oss",
        "tier": "flagship"
    },
    "@cf/meta/llama-3.2-11b-vision-instruct": {
        "context_length": 131072,
        "max_output_tokens": 4096,
        "supports_streaming": True,
        "supports_function_calling": False,  # vision models typically don't
        "supports_multimodal": True,  # text + image
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "llama-3-vision",
        "tier": "vision"
    }
}
```

### Catalog Discovery

There is **no `/v1/models` on the OpenAI-compat path**. To enumerate the catalog, use the native endpoint:

```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/models/search?per_page=100" \
  -H "Authorization: Bearer {CLOUDFLARE_API_TOKEN}" | jq '.result[] | select(.type=="text-generation") | .name'
```

Cloudflare's catalog page (https://developers.cloudflare.com/workers-ai/models/) lists ~69 models across text generation, embeddings, TTS, image generation, and classification. Of these, roughly 20–30 are chat-completion-capable LLMs.

### Planned Deprecations

Cloudflare periodically deprecates older models — verified May 2026: 18 older models deprecated (see [Cloudflare changelog](https://developers.cloudflare.com/workers-ai/platform/changelog/)). AgentKthx's `tool_support.json` cache handles model-not-found gracefully via the 404 path.

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect model capabilities from Cloudflare model ID"""
    name_lower = model_name.lower()
    if name_lower.startswith("@cf/meta/llama-3."):
        if "vision" in name_lower:
            return {"family": "llama-3-vision",
                    "supports_native_tools": False,
                    "supports_multimodal": True}
        return {"family": "llama-3",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("@cf/mistralai/mistral"):
        return {"family": "mistral",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("@cf/qwen/qwen"):
        return {"family": "qwen",
                "supports_native_tools": True,
                "supports_thinking": "r1" in name_lower or "thinking" in name_lower}
    elif "deepseek-r" in name_lower:
        return {"family": "deepseek-r-distill",
                "supports_native_tools": False,  # R1 distill models: no tools
                "supports_thinking": True}
    elif "deepseek-v" in name_lower:
        return {"family": "deepseek-v",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("@cf/openai/gpt-oss"):
        return {"family": "gpt-oss",
                "supports_native_tools": True,
                "supports_responses_api": True,
                "supports_streaming": False}  # Responses API only, non-streaming
    elif "gemma" in name_lower:
        return {"family": "gemma",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif "phi-" in name_lower or "phi3" in name_lower:
        return {"family": "phi",
                "supports_native_tools": True,
                "supports_multimodal": "vision" in name_lower}
    else:
        return {"family": "unknown",
                "supports_native_tools": False,
                "supports_thinking": False}
```

---

## Function Calling Implementation

### Tool Schema Requirements

Standard OpenAI tool-call schema:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "default": "celsius"}
            },
            "required": ["city"]
        }
    }
}
```

### Tool Support Caveats

- **Vision models** (`@cf/meta/llama-3.2-11b-vision-instruct`, `@cf/meta/llama-3.2-90b-vision-instruct`) typically **do not** support `tools` — 400 if attempted. Use ReAct fallback.
- **Reasoning models** (`@cf/deepseek-ai/deepseek-r1-distill-qwen-32b`) do NOT support `tools` — same 400 error. Use ReAct.
- **GPT-OSS models** (`@cf/openai/gpt-oss-120b`, `@cf/openai/gpt-oss-20b`) support `tools` via the **Responses API** only (`/responses` endpoint, not `/chat/completions`). They require a different request shape. AgentKthx should route GPT-OSS through a dedicated Responses-API path or fall back to chat-completions ReAct.
- **Tool-call streaming** is supported on chat-completion models that support tools. Standard OpenAI incremental `tool_calls[].function.arguments` chunking applies.

### Tool Flow Implementation

```python
class CloudflareToolHandler:
    """Tool handler for Cloudflare Workers AI — OpenAI-spec-compatible."""

    def __init__(self, api_key: str, account_id: str):
        self.api_key = api_key
        self.account_id = account_id
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"

    def convert_to_cf_tools(self, agent_tools: list) -> list:
        """Convert AgentKthx tools to Cloudflare (OpenAI) format"""
        cf_tools = []
        for tool in agent_tools:
            if hasattr(tool, 'to_openai_schema'):
                cf_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema()
                })
            else:
                cf_tools.append(self._convert_custom_tool(tool))
        return cf_tools

    def handle_tool_calls(self, response: dict) -> list:
        """Extract tool calls from response — OpenAI-spec shape"""
        tool_calls = []
        if 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            if 'message' in choice and 'tool_calls' in choice['message']:
                for tool_call in choice['message']['tool_calls']:
                    tool_calls.append({
                        'id': tool_call['id'],
                        'type': tool_call['type'],
                        'function': {
                            'name': tool_call['function']['name'],
                            'arguments': json.loads(tool_call['function']['arguments'])
                        }
                    })
        return tool_calls
```

---

## Streaming & Real-time Features

### Streaming Response Format

Cloudflare's OpenAI-compatible endpoint uses standard OpenAI SSE format:

```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"@cf/meta/llama-3.3-70b-instruct-fp8-fast","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"@cf/meta/llama-3.3-70b-instruct-fp8-fast","choices":[{"index":0,"delta":{"content":", world"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"@cf/meta/llama-3.3-70b-instruct-fp8-fast","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Streaming Implementation (stdlib-only, AgentKthx pattern)

```python
import json
import urllib.request

class CloudflareStreamHandler:
    """AgentKthx-style stdlib-only SSE stream reader for Cloudflare Workers AI."""

    def __init__(self, api_key: str, account_id: str):
        self.api_key = api_key
        self.account_id = account_id
        self.base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"

    def create_streaming_request(self, messages, **kwargs):
        """Build streaming request with proper headers"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        body = {
            "model": kwargs.get("model", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.95),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }
        # Cloudflare-specific capacity fail-fast
        if kwargs.get("reject_if_busy"):
            body["options"] = {"rejectIfBusy": True}
        if "tools" in kwargs:
            body["tools"] = kwargs["tools"]
            body["tool_choice"] = kwargs.get("tool_choice", "auto")

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers=headers,
            method="POST"
        )
        return urllib.request.urlopen(req, timeout=120)

    def iter_stream_chunks(self, response):
        """Yield (content_delta, tool_call_delta, finish_reason) tuples."""
        buffer = b""
        for chunk in response:
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data: "):
                    continue
                payload = line[6:]
                if payload == b"[DONE]":
                    return
                try:
                    data = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if not data.get("choices"):
                    continue
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                yield (
                    delta.get("content", ""),
                    delta.get("tool_calls", []),
                    choice.get("finish_reason"),
                )
```

---

## Error Codes & Recovery

### Error Response Structure

Cloudflare returns OpenAI-spec error envelopes on the OpenAI-compat path:

```json
{
    "error": {
        "message": "Model not found",
        "type": "invalid_request_error",
        "param": "model",
        "code": "model_not_found"
    }
}
```

On the native `/ai/run/{model}` path, Cloudflare uses its own envelope:

```json
{
    "result": null,
    "success": false,
    "errors": [{"code": 7003, "message": "Could not route to /accounts/.../ai/run/..."}],
    "messages": []
}
```

### Comprehensive Error Handling

```python
class CloudflareErrorHandler:
    ERROR_CODES = {
        400: {
            "message": "Bad Request",
            "recoverable": False,
            "actions": ["Check request format",
                       "Verify model supports requested features",
                       "For vision models: tools not supported"]
        },
        401: {
            "message": "Invalid API Token",
            "recoverable": False,
            "actions": ["Verify token at dash.cloudflare.com → My Profile → API Tokens",
                       "Token must have Workers AI:Read AND Workers AI:Edit"]
        },
        403: {
            "message": "Permission Denied",
            "recoverable": False,
            "actions": ["Token lacks Workers AI - Edit permission",
                       "Account ID does not match token scope"]
        },
        404: {
            "message": "Model Not Found OR Account ID wrong",
            "recoverable": False,
            "actions": ["Verify model ID at developers.cloudflare.com/workers-ai/models/",
                       "Verify ACCOUNT_ID matches the token's account scope",
                       "Model may have been deprecated — check changelog"]
        },
        429: {
            "message": "Rate Limit Exceeded OR Daily Neuron Quota Exhausted",
            "recoverable": True,  # rate limit; NOT quota exhaustion
            "retry_after": "Retry-After header",
            "actions": ["Distinguish: 429 with 'neuron' or 'quota' in message = daily quota exhausted (wait UTC midnight)",
                       "429 without quota language = transient rate limit (backoff)"]
        },
        500: {
            "message": "Internal Server Error",
            "recoverable": True,
            "retry_after": 5,
            "actions": ["Retry with backoff"]
        },
        503: {
            "message": "Service Unavailable — capacity exhausted (if rejectIfBusy=true)",
            "recoverable": True,
            "retry_after": 30,
            "actions": ["Retry with longer backoff",
                       "If using rejectIfBusy, consider disabling for non-interactive workloads"]
        }
    }

    def handle_error(self, response) -> dict:
        try:
            error_data = response.json()
            code = response.status_code
            # OpenAI-compat path
            if "error" in error_data:
                message = error_data["error"].get("message", "")
            # Native path
            elif "errors" in error_data:
                message = error_data["errors"][0].get("message", "")
            else:
                message = str(error_data)
        except Exception:
            code = response.status_code
            message = response.text

        info = self.ERROR_CODES.get(code, {
            "message": "Unknown Error",
            "recoverable": False,
            "actions": ["Check logs", "Contact Cloudflare support"]
        })

        # Special case: daily neuron quota exhausted
        if code == 429 and ("neuron" in message.lower() or "quota" in message.lower() or "limit" in message.lower()):
            info = {
                "message": "Daily neuron quota exhausted",
                "recoverable": False,  # NOT retryable — wait UTC midnight
                "actions": ["Wait for UTC midnight reset",
                           "Switch to alternative backend for the day",
                           "Upgrade to paid Workers plan for higher daily limits"]
            }

        return {
            "code": code,
            "message": info["message"],
            "raw_message": message,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"]
        }
```

---

## Rate Limiting & Concurrency

### Free Plan Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Neurons per day | 10,000 | UTC midnight reset; shared across all models |
| Requests per minute | (none documented) | Bounded by neuron consumption |
| Concurrent requests | (none documented) | Cloudflare queues internally |
| Streaming duration | 5 min hard timeout | Standard Cloudflare Workers limit |

### Neuron Costs (Approximate)

Neuron costs vary by model and request size. Verified approximate costs:

| Model | Neurons per 1k input tokens | Neurons per 1k output tokens |
|-------|-----------------------------|------------------------------|
| `@cf/meta/llama-3.1-8b-instruct` | ~1 | ~1 |
| `@cf/meta/llama-3.3-70b-instruct-fp8-fast` | ~5 | ~10 |
| `@cf/mistralai/mistral-7b-instruct-v0.3` | ~1 | ~1 |
| `@cf/qwen/qwen2.5-coder-32b-instruct` | ~3 | ~6 |
| `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | ~3 | ~10 (with thinking) |
| `@cf/openai/gpt-oss-120b` | ~5 | ~15 |

With 10,000 neurons/day, a developer running a typical agentic workflow (5k input + 2k output tokens per request, ~50 requests/day) on llama-3.3-70b-instruct would consume ~50 × (25 + 20) = 2,250 neurons — well within the daily budget. A heavier workload on gpt-oss-120b might exhaust the budget in ~30 requests.

### Rate-Limit Headers

```python
RATE_LIMIT_HEADERS = {
    # Cloudflare does not document specific rate-limit headers on the OpenAI-compat path
    # The 429 response includes Retry-After for transient rate limits
    'retry-after': '60'
}
```

---

## Multimodal Content Handling

### Content Types

```python
class CloudflareMultimodalHandler:
    CONTENT_TYPE_VALIDATION = {
        "text": {"max_length": 131072, "allowed_types": [str]},
        "image_url": {
            "max_size_mb": 5,
            "max_pixels": 8000 * 8000,
            "allowed_formats": ["jpg", "jpeg", "png", "webp"],
            "max_images": 1  # most CF vision models accept 1 image per turn
        }
    }

    def validate_content(self, content: list) -> dict:
        """Validate multimodal content structure"""
        errors = []
        for i, item in enumerate(content):
            if not isinstance(item, dict):
                errors.append(f"Item {i}: Must be a dictionary")
                continue
            content_type = item.get("type")
            if content_type not in self.CONTENT_TYPE_VALIDATION:
                errors.append(f"Item {i}: Invalid content type '{content_type}'")
        return {"valid": len(errors) == 0, "errors": errors}
```

### Image Request Example

```python
# Llama-3.2-11B-Vision-Instruct on Cloudflare Workers AI
content = [
    {"type": "text", "text": "What's in this image?"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
]
```

Cloudflare accepts both URL-referenced and base64-encoded images (matching the OpenAI vision API shape). For URL images, Cloudflare fetches server-side — AgentKthx's `is_safe_url` SSRF protection applies before forwarding.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatMixin

class CloudflareBackend(OpenAICompatMixin, CloudBackend):
    """Cloudflare Workers AI backend (OpenAI-compatible surface)."""

    is_cloud = True

    def __init__(self, config=None, base_url=None, **kwargs):
        # Cloudflare base URL requires account ID at construction time
        account_id = (config.CLOUDFLARE_ACCOUNT_ID if config
                      else kwargs.get("account_id"))
        if not account_id:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID is required for Cloudflare backend")
        base_url = base_url or f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        super().__init__(config=config, base_url=base_url, **kwargs)
        self.api_key = config.CLOUDFLARE_API_KEY if config else kwargs.get("api_key")
        self.account_id = account_id

    @property
    def auth_header(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        """Build Cloudflare Workers AI (OpenAI-spec) request"""
        request = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "max_tokens": kwargs.get("num_predict"),
            "stream": kwargs.get("stream", False),
        }
        # Cloudflare does NOT support top_k or repetition_penalty on the
        # OpenAI-compat path — silently drop them (unlike NVIDIA NIM where
        # they pass through).
        if kwargs.get("seed") is not None:
            request["seed"] = kwargs["seed"]
        if "tools" in kwargs:
            request["tools"] = kwargs["tools"]
            request["tool_choice"] = kwargs.get("tool_choice", "auto")
        # Cloudflare-specific capacity fail-fast
        if kwargs.get("reject_if_busy"):
            request["options"] = {"rejectIfBusy": True}
        return request

    def list_models(self) -> list:
        """List models via the native endpoint (not on OpenAI-compat path)"""
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/models/search?per_page=100",
            headers=self.auth_header,
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return [m["name"] for m in data.get("result", [])
                    if m.get("type") == "text-generation"]
```

### 2. FREE_ONLY Enforcement

```python
class CloudflareBackend(OpenAICompatMixin, CloudBackend):
    # ... (continued)

    def list_free_models(self) -> list:
        """All Workers AI models are free within the daily neuron quota."""
        # Cloudflare's neuron quota is account-wide, not per-model — every
        # model is "free" until the daily quota is exhausted, then every
        # model returns 429. So FREE_ONLY returns the full catalog.
        if not getattr(self, "_free_only", False):
            return self.list_models()
        return self.list_models()  # same list — quota is shared
```

Set `CLOUDFLARE_FREE_ONLY=1` env var to enable quota-aware behavior. Same pattern as NVIDIA — `FREE_ONLY` doesn't filter the catalog, it just changes the 429 error message to mention daily quota.

### 3. Plugin Manifest

```json
{
    "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
    "name": "cloudflare",
    "version": "0.1.0",
    "description": "Cloudflare Workers AI backend for 20+ open models (Llama, Mistral, Qwen, DeepSeek, Phi, Gemma, GPT-OSS) via OpenAI Chat-Completions API at api.cloudflare.com — free tier with 10,000 neurons per day (UTC reset), no credit card required",
    "backend_class": "agentkthx.backends.cloudflare.CloudflareBackend",
    "backend_type": "cloud",
    "env_vars": ["CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"],
    "optional_env_vars": ["CLOUDFLARE_FREE_ONLY", "CLOUDFLARE_BASE_URL"],
    "default_base_url": "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1",
    "free_tier": true
}
```

### 4. Existing Patterns That Apply Directly

- **`CloudBackend` base class** (R07.05 MAINT-02): inherits retry helpers, SSE streaming, JSON-endpoint layout for free.
- **`api_resilience.py`**: 429 is correctly classified as transient (rate limit), but AgentKthx needs to special-case the "neuron quota" 429 message as permanent-daily (not retryable). Add a helper that checks the error body for "neuron" / "quota".
- **`tool_support.json` cache**: same `<model>` plain-key namespace. Model IDs include the `@cf/` prefix — keep them intact.
- **`is_local_base_url`**: correctly classifies `api.cloudflare.com` as remote — no false "local" detection.
- **`_close_http_response`** (R07.25 ROB-06): deterministic close applies automatically.

### 5. Unique Cloudflare-Specific Considerations

- **Account ID required at construction**: Unlike other OpenAI-compat backends where the API key alone suffices, Cloudflare requires both `CLOUDFLARE_API_KEY` and `CLOUDFLARE_ACCOUNT_ID`. The `__init__` raises `ValueError` if account ID is missing — fail-fast prevents confusing 404s later.
- **No `/v1/models` on OpenAI-compat path**: Use the native `/ai/models/search` endpoint (different response shape — `{result: [...], success: true}` envelope). The `list_models()` method above handles this.
- **`options.rejectIfBusy`**: Optional Cloudflare extension. Recommended for interactive REPL sessions (fail fast on capacity); not recommended for background agentic workflows (better to queue).
- **GPT-OSS Responses API**: `@cf/openai/gpt-oss-120b` and `@cf/openai/gpt-oss-20b` support the OpenAI Responses API (`/responses` endpoint, NOT `/chat/completions`) and only with `stream: false`. If AgentKthx targets GPT-OSS, route through the Responses-API path (the existing `openresponses.py` module provides the parser).

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `401 Unauthorized` | Invalid API token, or token lacks Workers AI - Edit permission | Recreate token with Workers AI - Read AND Edit permissions |
| `404 model not found` | Wrong model ID, or model deprecated, or wrong ACCOUNT_ID | Verify model ID at https://developers.cloudflare.com/workers-ai/models/; verify ACCOUNT_ID matches token scope |
| `403 Forbidden` | Token has Read but not Edit permission for Workers AI | Edit permissions are required even for inference — counterintuitive but documented |
| `429` with "neuron" or "quota" in message | Daily neuron quota exhausted | Wait for UTC midnight reset; switch backend; upgrade to paid Workers plan |
| `429` without quota language | Transient rate limit | Backoff with Retry-After header |
| `503` with `rejectIfBusy=true` | Capacity exhausted | Disable rejectIfBusy for non-interactive workloads, or retry with backoff |
| `400` with "tool calls not supported" | Vision model or R1 distill model with `tools` in body | Disable native tools, use ReAct mode |
| `400` with "stream not supported" | GPT-OSS model via `/chat/completions` | Use `/responses` endpoint instead, with `stream: false` |
| Streaming stalls after 5 minutes | Workers AI hard timeout | Reduce max_tokens; use non-streaming for long completions |
| `tool_calls` empty despite model support | `tool_choice: "none"` set, or tool schema malformed | Verify `tool_choice` is `"auto"` and tool schema is valid JSON Schema |
| Image request returns `400` | Image exceeds size limit, or model doesn't support vision | Re-encode as PNG/JPEG <5MB, <8000x8000px; verify model ID includes "vision" |

---

## Appendix: Neuron Economy & Free Access

### Free Tier Details

- **Daily quota**: 10,000 neurons per day, refreshed at UTC midnight
- **No credit card**: Required only for paid Workers plans (which raise the daily limit)
- **Shared across models**: Neuron budget is account-wide — using llama-3.1-8b for 100 requests and gpt-oss-120b for 5 requests both draw from the same daily 10k pool
- **No automatic upgrade**: Hitting the daily cap returns 429 until UTC midnight; no auto-charge to a credit card

### Paid Tier Comparison

| Plan | Daily neurons | Cost |
|------|---------------|------|
| Free | 10,000 | $0 |
| Workers Paid | 10,000 (same) + paid overage at $0.011/1k neurons | $5/month base |
| Enterprise | Custom | Contact sales |

Paid Workers plan does NOT raise the daily free quota — it just enables paid overage beyond the cap. For sustained free use, stay on the free plan.

---

## Appendix: Native /ai/run/{model} Endpoint

Cloudflare also exposes a native REST endpoint that predates the OpenAI-compat surface. Some features are native-only:

### Endpoint

```bash
curl https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct \
  -H 'Authorization: Bearer {API_TOKEN}' \
  -d '{"prompt": "Where did the phrase Hello World come from"}'
```

### Response Shape (NOT OpenAI-spec)

```json
{
    "result": {
        "response": "Hello, World first appeared in 1974..."
    },
    "success": true,
    "errors": [],
    "messages": []
}
```

### Native-Specific Features

- **`repetition_penalty`** is supported (not available on OpenAI-compat path)
- **`response_format: json_schema`** with full schema enforcement
- **`stream: true`** uses a different SSE shape (`data: {"response": "..."}` per chunk)
- **`loras`** field for LoRA adapter selection (fine-tuned models)

### AgentKthx Recommendation

Stick with the OpenAI-compat path for consistency with the existing `openai_compat` plumbing. Use the native endpoint only if you need `repetition_penalty`, structured output JSON schema, or LoRA adapters.

---

## Appendix: OpenAI Wire-Format Deltas

For a developer familiar with vanilla OpenAI Chat Completions, here's the diff for Cloudflare Workers AI:

| Field | OpenAI | Cloudflare Workers AI |
|-------|--------|----------------------|
| Base URL | `https://api.openai.com/v1` | `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1` |
| Auth | Bearer API key | Bearer API token (Cloudflare token, not OpenAI key) |
| Model ID | `gpt-4o-mini` | `@cf/meta/llama-3.3-70b-instruct-fp8-fast` (longer, namespaced) |
| `top_k` | Supported | NOT supported on OpenAI-compat path |
| `repetition_penalty` | NOT supported | NOT supported on OpenAI-compat (supported on native) |
| `seed` | Supported | Supported (some older models ignore silently) |
| `response_format: json_schema` | Supported | NOT supported on OpenAI-compat (supported on native) |
| `tools` | Standard | Standard (vision/reasoning models reject) |
| `/v1/models` endpoint | Yes | NO (use native `/ai/models/search`) |
| Custom `options.rejectIfBusy` | N/A | Optional Cloudflare extension |
| Error envelope | OpenAI-spec `{error: {message, type, code}}` | Same OpenAI-spec shape on compat path |
