# NVIDIA NIM API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://docs.nvidia.com/nim/large-language-models/latest/api-reference.html + https://build.nvidia.com live catalog (verified Oct 2026)
> **Free-tier verification**: 1,000 inference credits on signup, **resets monthly**, up to 5,000 by request. 40 req/min rate limit. No credit card required. Source: [stevescargall.com (Apr 2026)](https://stevescargall.com/run-free-llms-at-scale-litellm-gateway-with-groq-nvidia-nim-and-cloudflare-workers-ai/) + [gopenai blog (Jun 2026)](https://blog.gopenai.com) + [stork.ai (May 2026)](https://www.stork.ai)
> **Live-behavior notes**: 2026-10-07 — credit renewal is monthly (not daily like Cloudflare); streaming + tool calling both supported; catalog is broad (Llama, Mistral, Qwen, Phi, NV Nemotron, DeepSeek, Granite, GLM)
> **Primary focus**: Cloud-hosted OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1` (the build.nvidia.com service). The same API surface also applies to self-hosted NIM containers, but AgentKthx would target the cloud endpoint.
> **Last Updated**: 2026-10-07
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
9. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
10. [Troubleshooting Matrix](#troubleshooting-matrix)
11. [Appendix: Credit Economy & Free Access](#appendix-credit-economy--free-access)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — cloud-hosted NIM inference
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Primary endpoints (OpenAI-compatible)
CHAT_COMPLETIONS = "/v1/chat/completions"
COMPLETIONS      = "/v1/completions"            # legacy single-turn
MODELS           = "/v1/models"                  # list models hosted on this deployment
EMBEDDINGS       = "/v1/embeddings"              # select embedding models only

# OpenAPI explorer (running container only — not available on cloud endpoint)
OPENAPI_DOCS     = "/docs"
```

Unlike Mistral (which exposes a separate OpenAI-compat path) or Gemini (separate compat endpoint), NVIDIA NIM's `/v1/chat/completions` IS the primary API surface — built on vLLM's OpenAI-compatible server. Point any OpenAI SDK at `https://integrate.api.nvidia.com/v1` and it works unchanged.

### Authentication Headers

```python
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer nvapi-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",  # build.nvidia.com API key
    "Accept": "application/json",
}
```

### Key Types

NVIDIA NIM distinguishes keys by **prefix** and **scope**:

| Key type | Prefix | Where it goes | What it can do |
|----------|--------|---------------|----------------|
| Cloud NIM key | `nvapi-` | Server only (env var, secrets manager) | Authenticates against `integrate.api.nvidia.com`. Issued at https://build.nvidia.com → Account → API Keys. One key per account; not scoped per-model. |
| Self-hosted NIM key | `nvapi-` (optional) | Server only | When NGC_API_KEY is set on the NIM container, the same key authorizes client requests. When unset, no auth required (LAN deployments). |
| NGC personal key | `nvapi-` | Server only | Used to pull NIM containers from NGC registry (`docker login nvcr.io`). Distinct from inference keys but same prefix. |

**AgentKthx guidance**: Use `nvapi-` keys read from the environment (`NVIDIA_API_KEY`). Never embed keys in prompt text, logs, or plugin manifests.

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 4MB (typical vLLM limit; cloud endpoint may be lower)
- **Timeout**: 120 seconds recommended (NIM supports long-running streaming requests)
- **HTTP method**: `POST` for inference endpoints, `GET` for `/v1/models`

---

## Request/Response Structure

### Complete Request Schema

```json
{
    "model": "meta/llama-3.3-70b-instruct",
    "messages": [
        {
            "role": "system|user|assistant|tool",
            "content": "string|array",
            "tool_calls": "array",
            "tool_call_id": "string"
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_tokens": 2048,
    "stream": false,
    "stop": ["</s>", "<|end|>"],
    "seed": 42,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "repetition_penalty": 1.0,
    "logprobs": false,
    "top_logprobs": 0,
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
    "response_format": {"type": "text|json_object|json_schema"},
    "n": 1,
    "user": "string"
}
```

### Response Schema

```json
{
    "id": "chatcmpl-1234567890",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "meta/llama-3.3-70b-instruct",
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
            "finish_reason": "stop|tool_calls|length|content_filter",
            "logprobs": null
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 70,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0}
    }
}
```

### Differences from vanilla OpenAI

- **`repetition_penalty`** is supported (vLLM extension beyond OpenAI spec — AgentKthx's existing `repeat_penalty` parameter maps cleanly onto this)
- **`top_k`** is supported as a top-level parameter (not just `top_p`)
- **`tool_choice`** supports `"auto"`, `"none"`, `"required"`, or `{"type": "function", "function": {"name": "..."}}`
- **`response_format`** supports `json_object` (constrained JSON mode on selected models) and `json_schema` (structured output mode on selected models — pass the full JSON schema)
- **`reasoning_tokens`** appears in `completion_tokens_details` for reasoning models (DeepSeek-R1, Qwen3-Thinking, etc.)

---

## Model Family Specifications

### Model Metadata Structure

```python
MODEL_CONFIGS = {
    "meta/llama-3.3-70b-instruct": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_thinking": False,
        "supports_reasoning_effort": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "supports_json_mode": True,
        "supports_json_schema": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "llama-3",
        "tier": "flagship"
    },
    "meta/llama-3.1-405b-instruct": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "llama-3",
        "tier": "flagship"
    },
    "deepseek-ai/deepseek-r1": {
        "context_length": 131072,
        "max_output_tokens": 32768,
        "supports_thinking": True,
        "supports_reasoning_effort": False,
        "supports_streaming": True,
        "supports_function_calling": False,  # R1 reasoning models don't support tools
        "supports_multimodal": False,
        "temperature_default": 0.6,
        "top_p_default": 0.95,
        "family": "deepseek-r",
        "tier": "reasoning"
    },
    "mistralai/mistral-nemo-12b-instruct": {
        "context_length": 131072,
        "max_output_tokens": 8192,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "mistral",
        "tier": "standard"
    },
    "qwen/qwen3-235b-a22b-instruct-2507": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_thinking": True,
        "supports_reasoning_effort": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "qwen3",
        "tier": "flagship"
    },
    "nvidia/llama-3.1-nemotron-70b-instruct": {
        "context_length": 131072,
        "max_output_tokens": 16384,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": False,
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "nemotron",
        "tier": "flagship"
    },
    "microsoft/phi-4-multimodal-instruct": {
        "context_length": 131072,
        "max_output_tokens": 8192,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_multimodal": True,  # text + image + audio
        "temperature_default": 0.7,
        "top_p_default": 0.95,
        "family": "phi",
        "tier": "vision"
    }
}
```

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect model capabilities from name"""
    name_lower = model_name.lower()
    if name_lower.startswith("meta/llama-3"):
        return {"family": "llama-3",
                "supports_native_tools": True,
                "supports_thinking": False,
                "supports_multimodal": False}
    elif name_lower.startswith("deepseek-ai/deepseek-r"):
        return {"family": "deepseek-r",
                "supports_native_tools": False,  # R1 family doesn't support tools
                "supports_thinking": True,
                "reasoning_levels": []}
    elif name_lower.startswith("deepseek-ai/deepseek-v"):
        return {"family": "deepseek-v",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("qwen/qwen3"):
        return {"family": "qwen3",
                "supports_native_tools": True,
                "supports_thinking": "thinking" in name_lower or "-thinking-" in name_lower}
    elif name_lower.startswith("mistralai/") or name_lower.startswith("mistral/"):
        return {"family": "mistral",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif "nemotron" in name_lower:
        return {"family": "nemotron",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif "phi-" in name_lower:
        return {"family": "phi",
                "supports_native_tools": True,
                "supports_multimodal": "multimodal" in name_lower}
    elif name_lower.startswith("ibm/granite"):
        return {"family": "granite",
                "supports_native_tools": True,
                "supports_thinking": False}
    else:
        return {"family": "unknown",
                "supports_native_tools": False,
                "supports_thinking": False}
```

### Catalog Discovery

AgentKthx can probe the available model list via:

```bash
curl -s https://integrate.api.nvidia.com/v1/models \
  -H "Authorization: Bearer $NVIDIA_API_KEY" | jq '.data[].id'
```

The catalog includes 80+ models. New models appear regularly; the local `tool_support.json` cache key scheme mirrors the existing CloudBackend pattern (one key per `<model>` + a separate `thinking:<model>` axis for thinking support).

**Live finding (verified Oct 2026)**: The `/v1/models` endpoint is **open** — it returns the full 108-model catalog even with an invalid `nvapi-` test key (the auth check applies only to inference endpoints, not catalog reads). This means `agentkthx models --backend nvidia` works without a real API key, which is useful for catalog exploration but does not grant inference access.

---

## Function Calling Implementation

### Tool Schema Requirements

NVIDIA NIM follows the **OpenAI tool-call schema verbatim**:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather information for a city",
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

- **Reasoning models (DeepSeek-R1, Qwen3-Thinking variants)** do NOT support tool calling. Sending `tools` in the request body causes a 400 from the vLLM backend. AgentKthx should disable native tools and fall back to ReAct for these models — same pattern as the existing ZAI/Ollama thinking-disabled handling.
- **Older small models** (Phi-3-mini, Llama-3.1-8B without instruct suffix) may emit malformed tool-call JSON. The existing 4-level ReAct fallback chain handles this — keep `force_react=False` as default for these models.
- **Streaming tool calls** are supported but the `tool_calls[].function.arguments` field is delivered incrementally across multiple chunks. AgentKthx's `StreamAccumulator` already buffers this — no code change required.

### Tool Flow Implementation

```python
class NvidiaToolHandler:
    """Tool handler for NVIDIA NIM — OpenAI-spec-compatible."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def convert_to_nvidia_tools(self, agent_tools: list) -> list:
        """Convert AgentKthx tools to NVIDIA NIM (OpenAI) format"""
        nvidia_tools = []
        for tool in agent_tools:
            if hasattr(tool, 'to_openai_schema'):
                nvidia_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema()
                })
            else:
                nvidia_tools.append(self._convert_custom_tool(tool))
        return nvidia_tools

    def handle_tool_calls(self, response: dict, tools: list) -> list:
        """Extract tool calls from response — straight OpenAI shape"""
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

    def execute_tool_call(self, tool_call: dict) -> dict:
        """Execute a single tool call — OpenAI-spec tool-result shape"""
        function_name = tool_call['function']['name']
        arguments = tool_call['function']['arguments']
        tool = self._find_tool_by_name(function_name)
        if not tool:
            return {
                "role": "tool",
                "content": f"Error: Tool '{function_name}' not found",
                "tool_call_id": tool_call['id']
            }
        try:
            result = tool.execute(**arguments)
            return {
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
                "tool_call_id": tool_call['id']
            }
        except Exception as e:
            return {
                "role": "tool",
                "content": json.dumps({"error": str(e)}, ensure_ascii=False),
                "tool_call_id": tool_call['id']
            }
```

---

## Streaming & Real-time Features

### Streaming Response Format

NVIDIA NIM uses **standard OpenAI Server-Sent Events** format:

```
data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"meta/llama-3.3-70b-instruct","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"meta/llama-3.3-70b-instruct","choices":[{"index":0,"delta":{"content":", how can I help"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"meta/llama-3.3-70b-instruct","choices":[{"index":0,"delta":{"content":""},"finish_reason":"stop"}]}

data: [DONE]
```

### Streaming Implementation (stdlib-only, AgentKthx pattern)

```python
import json
import urllib.request

class NvidiaStreamHandler:
    """AgentKthx-style stdlib-only SSE stream reader for NVIDIA NIM."""

    def __init__(self, api_key: str, base_url: str = "https://integrate.api.nvidia.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def create_streaming_request(self, messages, **kwargs):
        """Build streaming request with proper headers"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        data = json.dumps({
            "model": kwargs.get("model", "meta/llama-3.3-70b-instruct"),
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.95),
            "max_tokens": kwargs.get("max_tokens", 2048),
        }).encode("utf-8")
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

### Streaming Tool Calls

When the model emits a tool call while streaming, the `delta` field carries the **incremental** tool-call arguments:

```json
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"ci"}}]},"finish_reason":null}]}
{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ty\":\"Paris\"}"}}]},"finish_reason":null}]}
{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}
```

The `index` field disambiguates multiple parallel tool calls. AgentKthx's existing `StreamAccumulator` already handles this pattern (mirrors the OpenAI/OpenRouter/Mistral behavior).

---

## Error Codes & Recovery

### Error Response Structure

```json
{
    "error": {
        "message": "Invalid request format",
        "type": "invalid_request_error",
        "param": "messages",
        "code": "invalid_request"
    }
}
```

For HTTP errors, NIM (vLLM) returns the OpenAI-spec error envelope. For rate-limit and credit-exhaustion, see below.

### Comprehensive Error Handling

```python
class NvidiaErrorHandler:
    ERROR_CODES = {
        400: {
            "message": "Bad Request",
            "recoverable": False,
            "actions": ["Check request format", "Validate parameters",
                       "Verify model supports requested features (tools, JSON mode)"]
        },
        401: {
            "message": "Invalid API Key",
            "recoverable": False,
            "actions": ["Verify nvapi- key at build.nvidia.com",
                       "Check key status (revoked, expired)"]
        },
        403: {
            "message": "Permission Denied",
            "recoverable": False,
            "actions": ["Check account status",
                       "Verify you have NIM access (NGC org membership)"]
        },
        404: {
            "message": "Model Not Found",
            "recoverable": False,
            "actions": ["Check model ID at build.nvidia.com",
                       "Verify the model is currently deployed"]
        },
        422: {
            "message": "Unprocessable Entity",
            "recoverable": False,
            "actions": ["Model received request but couldn't process it",
                       "Verify message format / tool schema"]
        },
        429: {
            "message": "Rate Limit Exceeded OR Credit Exhaustion",
            "recoverable": True,  # rate limit; NOT credit exhaustion
            "retry_after": "Retry-After header",
            "actions": ["Implement exponential backoff (rate limit case)",
                       "Distinguish: 429 with 'credits exhausted' message = permanent until next month"]
        },
        500: {
            "message": "Internal Server Error",
            "recoverable": True,
            "retry_after": 5,
            "actions": ["Retry with backoff", "Check NVIDIA status page"]
        },
        503: {
            "message": "Service Unavailable (model loading / capacity)",
            "recoverable": True,
            "retry_after": 30,
            "actions": ["Retry with longer backoff", "Try a different model variant"]
        },
        504: {
            "message": "Gateway Timeout",
            "recoverable": True,
            "retry_after": 10,
            "actions": ["Retry", "Reduce max_tokens for faster response"]
        }
    }

    def handle_error(self, response) -> dict:
        """Handle API errors with recovery suggestions"""
        try:
            error_data = response.json()
            code = response.status_code
            message = error_data.get("error", {}).get("message", "")
        except Exception:
            code = response.status_code
            message = response.text

        info = self.ERROR_CODES.get(code, {
            "message": "Unknown Error",
            "recoverable": False,
            "actions": ["Check logs", "Contact NVIDIA support"]
        })

        # Special case: distinguish credit exhaustion from rate limit
        if code == 429 and "credit" in message.lower():
            info = {
                "message": "Monthly credit quota exhausted",
                "recoverable": False,  # NOT retryable — wait for next month
                "actions": ["Wait for next monthly reset (~30 days)",
                           "Request additional credits at NVIDIA developer forums",
                           "Switch to alternative backend"]
            }

        return {
            "code": code,
            "message": info["message"],
            "raw_message": message,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"],
            "status_code": code
        }
```

### Retry Logic Implementation

The retry pattern matches AgentKthx's existing `api_resilience.py` — 3 retries with exponential backoff + jitter for transient errors (429 rate limit / 5xx), no retry for permanent errors (400/401/403/404). The credit-exhaustion special case is the only divergence: it returns 429 but is NOT retryable.

---

## Rate Limiting & Concurrency

### Cloud Endpoint Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Requests per minute | 40 RPM | Per API key, all models combined |
| Daily request cap | (none documented) | Bounded by credit budget instead |
| Concurrent requests | (none documented) | vLLM backend queues internally |
| Streaming duration | 5 min hard timeout | For long completions, use non-streaming + chunked prompts |

### Self-Hosted NIM Limits

Self-hosted NIM containers have no rate limit — bounded by GPU memory and the vLLM continuous-batching engine. Default `--max-num-seqs` is 256; AgentKthx doesn't need to set this.

### Rate-Limit Headers

```python
RATE_LIMIT_HEADERS = {
    'x-ratelimit-limit-requests': '40',
    'x-ratelimit-remaining-requests': '38',
    'x-ratelimit-reset-requests': '1.5s',
    'retry-after': '1.5',  # only present on 429
}
```

### Concurrency Management

AgentKthx's existing 4-worker parallel-tool-batch pool is well under the 40 RPM limit — even a max-steps agentic loop firing 4 tool calls per step would hit 4 RPM, leaving ample headroom.

---

## Multimodal Content Handling

### Content Types

```python
class NvidiaMultimodalHandler:
    CONTENT_TYPE_VALIDATION = {
        "text": {"max_length": 131072, "allowed_types": [str]},
        "image_url": {
            "max_size_mb": 5,
            "max_pixels": 8000 * 8000,
            "allowed_formats": ["jpg", "jpeg", "png", "webp", "gif"],
            "max_images": 1,  # most NIM vision models accept 1 image per turn
        },
        "input_audio": {  # Phi-4-Multimodal only
            "max_size_mb": 25,
            "allowed_formats": ["wav", "mp3", "flac"],
            "max_audios": 1
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
# Llama-3.2-90B-Vision-Instruct on NVIDIA NIM
content = [
    {"type": "text", "text": "Describe this image"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
]
```

NVIDIA NIM supports **both** URL-referenced images and base64-encoded images inline (matching the OpenAI vision API shape). For cloud-hosted NIM, URL images are fetched server-side — AgentKthx's `is_safe_url` SSRF protection applies before forwarding.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatMixin

class NvidiaBackend(OpenAICompatMixin, CloudBackend):
    """NVIDIA NIM cloud backend."""

    is_cloud = True

    def __init__(self, config=None, base_url="https://integrate.api.nvidia.com/v1", **kwargs):
        super().__init__(config=config, base_url=base_url, **kwargs)
        self.api_key = config.NVIDIA_API_KEY if config else kwargs.get("api_key")

    @property
    def auth_header(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        """Build NVIDIA NIM (OpenAI-spec) request"""
        request = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "max_tokens": kwargs.get("num_predict"),
            "stream": kwargs.get("stream", False),
        }
        # vLLM-specific sampling params pass through cleanly
        if kwargs.get("top_k") is not None:
            request["top_k"] = kwargs["top_k"]
        if kwargs.get("repeat_penalty") is not None:
            request["repetition_penalty"] = kwargs["repeat_penalty"]
        if kwargs.get("seed") is not None:
            request["seed"] = kwargs["seed"]
        if "tools" in kwargs:
            request["tools"] = kwargs["tools"]
            request["tool_choice"] = kwargs.get("tool_choice", "auto")
        return request

    def list_models(self) -> list:
        """List models hosted on the cloud NIM endpoint"""
        req = urllib.request.Request(
            f"{self.base_url}/models",
            headers=self.auth_header,
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return [m["id"] for m in data.get("data", [])]
```

### 2. FREE_ONLY Enforcement

Mirror the existing ZAI/OpenRouter pattern:

```python
class NvidiaBackend(OpenAICompatMixin, CloudBackend):
    # ... (continued)

    def list_free_models(self) -> list:
        """All cloud-NIM models are free within the monthly credit quota."""
        # NVIDIA's credit model is account-wide, not per-model — every model
        # in the catalog is "free" until the monthly quota is exhausted, then
        # every model returns 429. So FREE_ONLY returns the full catalog.
        if not getattr(self, "_free_only", False):
            return self.list_models()
        return self.list_models()  # same list — quota is shared
```

Set `NVIDIA_FREE_ONLY=1` env var to enable quota-aware behavior. Since NIM's quota is account-wide (not per-model), `FREE_ONLY` doesn't filter the catalog — it just changes the error message on 429 to point users at the credit-exhaustion scenario.

### 3. Model Auto-detection

```python
def auto_detect_model_config(model_name: str) -> dict:
    config = {
        "max_tokens": 4096,
        "temperature": 0.7,
        "top_p": 0.95,
        "supports_streaming": True,
        "supports_tools": True,
        "supports_thinking": False
    }
    family = detect_model_family(model_name)
    if family["family"] == "llama-3":
        config.update({"max_tokens": 16384, "supports_tools": True})
    elif family["family"] == "deepseek-r":
        config.update({"max_tokens": 32768, "supports_tools": False,
                       "supports_thinking": True})
    elif family["family"] == "qwen3":
        config.update({"max_tokens": 16384, "supports_thinking": family["supports_thinking"]})
    return config
```

### 4. Plugin Manifest

```json
{
    "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
    "name": "nvidia",
    "version": "0.1.0",
    "description": "NVIDIA NIM API backend for 80+ models (Llama, Mistral, Qwen, Phi, DeepSeek, Nemotron, Granite, GLM) via OpenAI Chat-Completions API at integrate.api.nvidia.com — free tier with monthly-recurring 1,000 inference credits, no credit card required",
    "backend_class": "agentkthx.backends.nvidia.NvidiaBackend",
    "backend_type": "cloud",
    "env_vars": ["NVIDIA_API_KEY"],
    "optional_env_vars": ["NVIDIA_FREE_ONLY", "NVIDIA_BASE_URL"],
    "default_base_url": "https://integrate.api.nvidia.com/v1",
    "free_tier": true
}
```

### 5. Existing Patterns That Apply Directly

- **`CloudBackend` base class** (R07.05 MAINT-02): `NvidiaBackend` inherits the retry helpers, SSE streaming, JSON-endpoint layout for free — no new code required.
- **`api_resilience.py`**: `is_transient_api_error` correctly classifies 429 as transient (rate limit) — but AgentKthx needs to special-case the "credit exhausted" 429 message as permanent. Add a helper that checks the error body for "credit" substring.
- **`tool_support.json` cache**: same `<model>` plain-key namespace. `test_tool_support` probe hits `/v1/chat/completions` with `tools=[{...}]` and a minimal user message; 200 → NATIVE, 400 → REACT, transient errors (429/5xx) → UNTESTED uncached.
- **`update_check.py`**: not affected.
- **`_close_http_response`** (R07.25 ROB-06): deterministic close applies automatically.

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `401 Unauthorized` | Invalid `nvapi-` key or expired key | Regenerate key at https://build.nvidia.com → Account → API Keys |
| `404 model not found` | Model ID removed or renamed | Query `/v1/models`, use exact ID (e.g. `meta/llama-3.3-70b-instruct` not `llama-3.3-70b`) |
| `429` with `rate limit` in message | RPM exceeded | Back off; 40 RPM is the hard limit |
| `429` with `credit` in message | Monthly credit quota exhausted | Wait for next monthly reset; request additional at NVIDIA developer forums; switch backend |
| `422` with `tool calls not supported` | Reasoning model (DeepSeek-R1, Qwen3-Thinking) with `tools` in body | Disable native tools, use ReAct mode |
| `400` with `context length` | Input + max_tokens exceeds model context | Reduce max_tokens to `ctx // 32` (AgentKthx default) |
| Streaming stalls after first chunk | Long generation hit 5-min timeout | Use non-streaming + chunked prompts, or reduce max_tokens |
| `tool_calls` empty despite model support | `tool_choice: "none"` set, or tool schema malformed | Verify `tool_choice` is `"auto"` and tool schema is valid JSON Schema |
| Memory growth across requests | vLLM continuous batching holds context — not applicable to cloud endpoint (stateless HTTP) | Local self-hosted only; cloud endpoint is request-scoped |
| Image request returns `400` | Image format not supported, or image exceeds size limit | Re-encode as PNG/JPEG <5MB, <8000x8000px |

---

## Appendix: Credit Economy & Free Access

### Free Tier Details

- **Sign-up bonus**: 1,000 inference credits issued immediately on account creation
- **Monthly renewal**: Credits reset every month (verified by [stevescargall.com (Apr 2026)](https://stevescargall.com/run-free-llms-at-scale-litellm-gateway-with-groq-nvidia-nim-and-cloudflare-workers-ai/))
- **Extension**: Up to 5,000 credits by request (one-time, via NVIDIA developer forum)
- **No credit card**: Required only for paid NVIDIA NGC org membership; the build.nvidia.com free tier needs only an NVIDIA developer account
- **Credit consumption**: Each request consumes credits proportional to (input_tokens + output_tokens). A 1,000-token request on a 70B model typically consumes ~1 credit.

### What "Free" Means in Practice

- **Genuinely recurring**: Monthly credit reset is the recurring free tier — not a one-time trial bonus
- **All models included**: Every model in the catalog is accessible within the credit budget (no per-model free/paid split like OpenRouter)
- **No automatic upgrade**: Hitting the credit cap does NOT auto-charge; it returns 429 until the next reset
- **No watermarking or output modification**: NIM returns raw model output, identical to a paid deployment

### Comparison with Other Free Tiers

| Provider | Reset cadence | Quota | No card | OpenAI-compat |
|----------|--------------|-------|---------|---------------|
| NVIDIA NIM | Monthly | 1,000 credits (renewable to 5,000 by request) | ✅ | ✅ |
| Cloudflare Workers AI | Daily (UTC) | 10,000 neurons | ✅ | ✅ (via `/ai/v1`) |
| SiliconFlow | Unlimited (free models only) | 3 free models, no quota | ✅ | ✅ |
| Pollinations | Daily (anonymous) / Pollen (keyed) | Variable | ✅ (anonymous) | ✅ |

### AgentKthx Recommendation

For an AgentKthx user with a typical agentic workflow (10–50 requests/session, 2k tokens/request ≈ 2–10 credits/session), NVIDIA NIM's monthly 1,000 credits supports roughly **100–500 agentic sessions per month** — comfortably covering a developer's daily use without paying. Combined with the broad model catalog (Llama/Mistral/Qwen/DeepSeek/Phi/Nemotron/Granite/GLM) and the OpenAI-compatible API, this is the strongest free-tier candidate among cloud inference providers as of October 2026.
