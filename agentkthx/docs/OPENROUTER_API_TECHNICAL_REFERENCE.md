# OpenRouter API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://openrouter.ai/docs
> **Last Updated**: 2026-09-20
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Sampling Parameters](#sampling-parameters)
4. [Model Catalog & Discovery](#model-catalog--discovery)
5. [Function Calling Implementation](#function-calling-implementation)
6. [Streaming & Real-time Features](#streaming--real-time-features)
7. [Provider Routing Preferences](#provider-routing-preferences)
8. [Transforms & Plugins](#transforms--plugins)
9. [Error Codes & Recovery](#error-codes--recovery)
10. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
11. [Free Tier Behavior](#free-tier-behavior)
12. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
13. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production
BASE_URL = "https://openrouter.ai/api/v1"

# API Endpoints
CHAT_COMPLETIONS = "/chat/completions"   # OpenAI-compatible
MODELS = "/models"                       # Model catalog (no auth required)
GENERATE = "/generate"                   # Legacy completion endpoint (rarely used)
KEYS = "/keys"                            # API key info (auth required)
CREDITS = "/credits"                      # Credit balance (auth required)
```

### Authentication Headers

```python
headers = {
    "Authorization": "Bearer sk-or-v1-YOUR_API_KEY",
    "Content-Type": "application/json",
    # Optional but recommended by OpenRouter for analytics / ranking:
    "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
    "X-Title": "AgentKthx",
}
```

The `HTTP-Referer` and `X-Title` headers are **optional** but OpenRouter uses them for app attribution in their leaderboards. Agents listed on openrouter.ai/community/apps get extra visibility.

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 8MB (varies by upstream provider)
- **Timeout**: 120 seconds default (AgentKthx uses 120s via `BackendConfig.timeout`)

---

## Request/Response Structure

### Complete Request Schema

```json
{
    "model": "anthropic/claude-3.5-sonnet",
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
    "max_tokens": 8192,
    "stream": false,
    "stream_options": {
        "include_usage": true
    },
    "stop": ["###"],
    "seed": 42,
    "n": 1,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "logit_bias": {},
    "logprobs": false,
    "top_logprobs": null,
    "user": "user-identifier",
    "response_format": {"type": "text|json_object|json_schema"},
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
    ],
    "tool_choice": "auto|none|required|{...}",
    "reasoning": {
        "effort": "low|medium|high",
        "max_tokens": 1024,
        "exclude": true
    },
    "transforms": ["middle-out"],
    "plugins": [{"id": "web", "max_results": 3}],
    "provider": {
        "order": ["Anthropic", "Together"],
        "allow_fallbacks": true,
        "require_parameters": false,
        "ignore": ["OpenAI"],
        "quantizations": ["fp8", "bf16"],
        "data_collection": "deny"
    }
}
```

### Response Schema

```json
{
    "id": "gen-1234567890",
    "provider": "Anthropic",
    "model": "anthropic/claude-3.5-sonnet",
    "object": "chat.completion",
    "created": 1700000000,
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "reasoning": "Chain of thought (some reasoning models)",
                "reasoning_content": "Alternative CoT field name",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "function_name",
                            "arguments": "{\"param1\": \"value1\"}"
                        }
                    }
                ]
            },
            "finish_reason": "stop|tool_calls|length|content_filter|model_context_window_exceeded"
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost": 0.0024
    }
}
```

Key differences from OpenAI's standard response:
- **`provider`**: Identifies which upstream provider served the request (Anthropic, Together, OpenAI, etc.)
- **`cost`**: Always present in `usage` (your actual cost in USD for the request)
- **`reasoning` / `reasoning_content`**: Some upstream models (GLM-5.x, o1, o3) emit chain-of-thought. Field name varies by provider.
- **No `web_search` field**: Web search results come back as tool calls when `plugins: [{id: "web"}]` is used.

---

## Sampling Parameters

OpenRouter supports a wide range of sampling parameters. Not all are honored by every upstream provider — OpenRouter silently forwards them and the provider decides.

### Temperature-family parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `temperature` | float | 0.0-2.0 | provider default | Controls randomness. Lower = focused/deterministic, higher = creative/diverse |
| `top_p` | float | 0.0-1.0 | 1.0 | Nucleus sampling: probability mass of tokens to consider |
| `top_k` | int | 0-N | 0 (disabled) | Top-K sampling: consider only the K most likely tokens |
| `top_a` | float | 0.0-1.0 | 0 (disabled) | Top-A sampling (alternative to top_p) — considers tokens where prob >= top_a * max_prob |
| `min_p` | float | 0.0-1.0 | 0 (disabled) | Min-P sampling (newer alternative to top_p) — keeps tokens with prob >= min_p * max_prob |
| `seed` | int | any | None | Reproducibility seed (best-effort; not all providers honor it) |

### Penalty parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `presence_penalty` | float | -2.0 to 2.0 | 0.0 | Positive: penalize tokens already present (encourages new topics) |
| `frequency_penalty` | float | -2.0 to 2.0 | 0.0 | Positive: penalize tokens proportional to frequency (discourages repetition) |
| `repetition_penalty` | float | 0.0-2.0 | 1.0 | Multiplier: 1.0 = no penalty, <1.0 = encourage repetition, >1.0 = discourage |
| `logit_bias` | dict | any | {} | Map token_id → bias (-100 to +100). Negative = avoid, positive = prefer |

### Generation control

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_tokens` | int | provider default | Maximum tokens to generate. **Use this field** (not `max_completion_tokens`) for max provider compatibility |
| `max_completion_tokens` | int | provider default | OpenAI's newer field name. Many free/3rd-party providers may not support |
| `stop` | list[str] | None | Stop sequences (up to 4 strings). Generation halts on match |
| `n` | int | 1 | Number of completions to generate. Returns `choices[]` array. Most providers cap at 1 for safety |
| `logprobs` | bool | false | Whether to return logprobs of output tokens |
| `top_logprobs` | int (0-20) | None | Number of top logprobs per token (requires `logprobs: true`) |

### AgentKthx implementation status

The AgentKthx `OpenRouterBackend._build_openai_body()` method (in `agentkthx/plugins/openrouter/openrouter.py`) currently forwards these parameters from `**kwargs`:

```python
optional_int_fields = ("top_p", "top_k", "seed", "n")
optional_float_fields = ("presence_penalty", "frequency_penalty")
```

**Missing parameters worth adding**:
- `min_p` — newer alternative to top_p, useful for small models
- `repetition_penalty` — different from frequency_penalty, supported by Llama-family
- `logit_bias` — useful for steering specific tokens
- `top_a` — alternative nucleus sampler

These can be added by extending `_build_openai_body()`'s optional fields list — the changes are minimal.

---

## Model Catalog & Discovery

### `/models` endpoint

Returns a list of all available models with metadata:

```bash
curl https://openrouter.ai/api/v1/models
```

```json
{
    "data": [
        {
            "id": "anthropic/claude-3.5-sonnet",
            "name": "Anthropic: Claude 3.5 Sonnet",
            "created": 1700000000,
            "description": "Claude 3.5 Sonnet...",
            "context_length": 200000,
            "architecture": {
                "modality": "text->text",
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
                "tokenizer": "Claude"
            },
            "pricing": {
                "prompt": "0.000003",
                "completion": "0.000015",
                "image": "0.004231",
                "request": "0",
                "web_search": "0.000005"
            },
            "top_provider": {
                "context_length": 200000,
                "max_completion_tokens": 8192,
                "is_moderated": true
            },
            "per_request_limits": null,
            "supported_parameters": [
                "tools", "tool_choice", "temperature", "max_tokens",
                "top_p", "presence_penalty", "frequency_penalty", "seed",
                "top_k", "reasoning"
            ]
        }
    ]
}
```

Key fields for AgentKthx to inspect:
- **`context_length`** — the model's full context window
- **`top_provider.max_completion_tokens`** — the actual max output tokens
- **`pricing.prompt` / `pricing.completion`** — USD per token (use for cost display)
- **`supported_parameters`** — list of parameters the model will accept (notable: `reasoning` for thinking models)
- **`architecture.modality`** — text→text, text+image→text, etc.

### Free model detection

Free models have `:free` suffix in their ID and zero pricing:

```python
def is_free_model(model_id: str) -> bool:
    return model_id.endswith(":free")

# Examples:
# "meta-llama/llama-3.2-3b-instruct:free"
# "poolside/laguna-xs-2.1:free"
# "google/gemini-flash-1.5:free"
```

When `OPENROUTER_FREE_ONLY=1` env var is set, AgentKthx filters the model list to only include free models.

### Live API data vs static catalog

AgentKthx maintains a static `OPENROUTER_MODELS` catalog in `agentkthx/plugins/openrouter/openrouter.py` for fallback when the API is unreachable, but **prefers live API data** for `context_length` and `max_completion_tokens`. The static catalog is updated periodically (last update: 2026-04-15).

---

## Function Calling Implementation

### Tool schema (OpenAI-compatible)

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name"
                }
            },
            "required": ["city"]
        }
    }
}
```

### Tool choice

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Model decides whether to call a tool or respond with text |
| `"none"` | Model MUST NOT call tools. Forces text response |
| `"required"` | Model MUST call at least one tool |
| `{"type": "function", "function": {"name": "X"}}` | Forces calling the specific function `X` |

### AgentKthx implementation

AgentKthx converts its internal `Tool` objects to OpenAI schema via `Tool.to_openai_schema()` and forwards them as the `tools` field in the request body. Tool results from previous turns are encoded as messages with `role: "tool"` and a `tool_call_id` field.

### ReAct fallback

Many free / 3rd-party OpenRouter providers **do not support native tool calling**. When a provider returns HTTP 400 with a message like "does not support tools" or "tool calling is not supported", AgentKthx's `OpenRouterBackend.generate()` automatically retries without the `tools` field, falling back to ReAct (text-based tool calling).

The detection logic is in `_is_tools_not_supported_error()`:

```python
indicators = (
    "does not support tools",
    "tools are not supported",
    "tool calling is not supported",
    "tools are not yet supported",
    "does not support function calling",
    "function calling is not supported",
    "no tools endpoint",
)
```

---

## Streaming & Real-time Features

### SSE streaming

Set `"stream": true` in the request body. OpenRouter returns Server-Sent Events:

```
data: {"id":"gen-123","choices":[{"delta":{"content":"Hello"}}]}

data: {"id":"gen-123","choices":[{"delta":{"content":" world"}}]}

data: {"id":"gen-123","choices":[{"finish_reason":"stop"}]}

data: [DONE]
```

### Stream options

```json
{
    "stream": true,
    "stream_options": {
        "include_usage": true
    }
}
```

Setting `include_usage: true` causes OpenRouter to emit a final SSE chunk with `usage` populated. Without this, streaming responses have no token usage info. AgentKthx **does** set `stream_options.include_usage` (added R06.53, PERF-02) and captures the usage chunk in `_generate_stream()` for token tracking.

**Note:** Some `:free` models on OpenRouter do not return a usage chunk even when `include_usage=true` is sent. AgentKthx handles this with a fallback: if `total_tokens` is 0 after a step, it estimates tokens from message content (`chars ÷ 4`) so the footer's token counts and context % still update during the run.

### Reasoning content in streaming

For thinking-capable models (o1, o3, GLM-5.x), reasoning tokens arrive as separate SSE chunks:

```
data: {"choices":[{"delta":{"reasoning":"Let me think..."}}]}
data: {"choices":[{"delta":{"reasoning":"First I should..."}}]}
data: {"choices":[{"delta":{"content":"The answer is..."}}]}
```

The `reasoning` field appears in delta chunks BEFORE the `content` field. AgentKthx captures `reasoning_content` in both streaming and non-streaming responses (since R06.53). In streaming mode, reasoning deltas are displayed in a `reasoning:` panel above the `AgentKthx:` prompt (UX-01, R06.56).

---

## Provider Routing Preferences

OpenRouter can route requests to multiple upstream providers for the same model. Use the `provider` field to control this:

```json
{
    "model": "anthropic/claude-3.5-sonnet",
    "provider": {
        "order": ["Anthropic", "Together"],
        "allow_fallbacks": true,
        "require_parameters": false,
        "ignore": ["OpenAI"],
        "quantizations": ["fp8", "bf16"],
        "data_collection": "deny"
    }
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `order` | list[str] | Preferred provider order. Falls through if first is unavailable |
| `allow_fallbacks` | bool | If true (default), fall back to other providers when preferred is down |
| `require_parameters` | bool | If true, only use providers that support all parameters you sent |
| `ignore` | list[str] | Providers to never use for this request |
| `quantizations` | list[str] | Quantization preferences (e.g. `["fp8", "bf16", "auto"]`) |
| `data_collection` | `"allow" \| "deny"` | Whether to allow training on your data. Default: `"deny"` |

**AgentKthx does not currently send the `provider` field** — uses OpenRouter's default routing. Adding provider preferences would let users prioritize free providers, control quantization, or pin to a specific backend.

---

## Transforms & Plugins

### Transforms

Transforms modify the request before it reaches the model. Common use: auto-truncation when conversation exceeds context window.

```json
{
    "transforms": ["middle-out"]
}
```

Available transforms:
- **`middle-out`** — keeps the first and last messages, summarizes/omits middle. Useful for very long conversations.
- (OpenRouter occasionally adds new transforms; check their docs.)

### Plugins

Plugins add capabilities like web search:

```json
{
    "plugins": [
        {"id": "web", "max_results": 3}
    ]
}
```

When the `web` plugin is enabled, the model can autonomously trigger web searches and return results as tool calls. Results appear in the response as a `tool_calls` entry with name `web_search`.

**AgentKthx does not currently use transforms or plugins** — could be added as opt-in CLI flags (`--web-search`, `--auto-truncate`).

---

## Error Codes & Recovery

### Common error codes

| Code | Meaning | Cause | Fix |
|------|---------|-------|-----|
| 400 | Bad Request | Malformed JSON, missing required fields, unsupported parameter | Validate request schema |
| 401 | Unauthorized | Invalid/expired API key, malformed `Authorization` header | Verify `OPENROUTER_API_KEY` env var |
| 402 | Payment Required | Insufficient credits, trying paid model on free tier | Add credits at openrouter.ai/credits OR switch to `:free` model |
| 403 | Forbidden | API key lacks permission for this model, region-blocked | Check API key scopes |
| 408 | Request Timeout | Provider took >60s to respond | Retry, possibly with smaller context |
| 422 | Unprocessable Entity | Model doesn't support requested features (e.g. `tools` on a non-tool model) | Use `_is_tools_not_supported_error()` detection and fall back to ReAct |
| 429 | Too Many Requests | Rate limit hit (free tier: 20 req/min, paid: provider-specific) | Honor `Retry-After` header; exponential backoff |
| 503 | Service Unavailable | Upstream provider down | OpenRouter should auto-failover if `allow_fallbacks: true` |
| 504 | Gateway Timeout | OpenRouter → provider connection timed out | Retry |

### 429 Retry-After handling

OpenRouter sends a `Retry-After` header on 429 responses (seconds until you can retry):

```python
retry_after_raw = response.headers.get("Retry-After", "10")
try:
    retry_after = int(retry_after_raw)
except (ValueError, TypeError):
    retry_after = 10
retry_after = min(max(retry_after, 1), 60)  # cap at 60s
```

AgentKthx's `OpenRouterBackend._make_api_request()` implements automatic 429 retry with up to 3 retries (`_MAX_429_RETRIES = 3`). Each retry waits the `Retry-After` duration (capped at 60s).

### Distinguishing OpenRouter rate limit from provider rate limit

The error JSON shape differs:

```json
// OpenRouter-side rate limit (your account hit the limit)
{
    "error": {
        "code": 429,
        "message": "Rate limit exceeded. Please try again in 32 seconds."
    }
}

// Upstream provider rate limit (the model's provider rate-limited you)
{
    "error": {
        "code": 429,
        "message": "Provider Together rate limited. Retrying with another provider...",
        "metadata": {"provider_name": "Together"}
    }
}
```

For upstream provider 429s, OpenRouter usually retries with another provider automatically (if `allow_fallbacks: true`). If you see "Provider X rate limited", wait longer — the provider itself is throttling.

---

## Rate Limiting & Concurrency

### Free tier limits

- **20 requests/minute** per free model (per account)
- **~200 requests/day** per free model (varies by model popularity)
- **50 free-model requests/day** for unfunded accounts (deposit ≥$5 to lift)
- Concurrent requests: 5 (free tier), 20+ (paid tier)

### Paid tier limits

Paid tier limits are provider-specific. OpenRouter's documented general limits:
- 200 requests/minute default
- Higher limits available on request (contact support@openrouter.ai)
- No daily cap

### AgentKthx implementation

AgentKthx does **not** implement client-side rate limiting. It relies on:
1. The 429 retry loop in `_make_api_request()` (max 3 retries with `Retry-After` honor)
2. The user to pace their requests if doing bulk operations

For bulk workflows (e.g. running `agentkthx test 04_gsm8k_benchmark`), users may want to add a client-side throttle. Could be a future R07.x feature.

---

## Free Tier Behavior

### Model ID convention

Free models have the `:free` suffix:
- `meta-llama/llama-3.2-3b-instruct:free`
- `poolside/laguna-xs-2.1:free`
- `google/gemini-flash-1.5:free`
- `qwen/qwen-2.5-7b-instruct:free`

### What "free" actually means

- ✅ No token cost (prompt or completion)
- ✅ Subject to rate limits (20 req/min)
- ❌ May have fewer features (e.g. no tool calling on some free providers)
- ❌ May be lower priority (slower responses during peak)
- ❌ Daily cap of 50 requests for unfunded accounts
- ❌ Some free models add a "Free Models Router" intermediate (`openrouter/free`)

### Detecting free model support for tools

Run `agentkthx models --backend openrouter --tool-support` to test each free model's tool support. Results are cached in `~/.cache/agentkthx/tool_support.json`.

Common free-model tool support issues:
- **`does not support tools`** — Provider doesn't implement OpenAI function calling. AgentKthx auto-falls-back to ReAct.
- **`tools are not yet supported`** — Provider may add support later. Same ReAct fallback.
- **Empty response with tool_calls=[]** — Some providers accept tools but never invoke them. Workaround: use `--force-react` to skip the native path entirely.

---

## Implementation Notes for AgentKthx

### Backend file location

```
agentkthx/plugins/openrouter/
├── __init__.py           # register()/unregister()
├── plugin.json           # plugin manifest
└── openrouter.py         # OpenRouterBackend class
```

### Key methods

| Method | Purpose |
|--------|---------|
| `__init__()` | Initializes with `OPENROUTER_API_KEY` env var, sets HTTP headers, populates `_model_cache` via `list_models()` |
| `list_models()` | Fetches `/v1/models`, caches for 1 hour (`_CACHE_TIMEOUT = 3600`), filters `:free` models if `OPENROUTER_FREE_ONLY=1` |
| `is_running()` | Always returns `True` (cloud API, no local server) |
| `generate(model, messages, tools, **kwargs)` | Main entry point. Dispatches to `_make_api_request()`. Implements ReAct fallback on "tools not supported" errors. |
| `generate_stream(model, messages, **kwargs)` | SSE streaming variant. Yields `delta` chunks. |
| `_make_api_request(endpoint, data, stream)` | HTTP wrapper with 429 retry, 401 detection, error normalization |
| `_stream_request(url, data, headers)` | Low-level SSE parser |
| `_build_openai_body(...)` | Centralizes request body construction (shared by generate + generate_stream) |
| `_parse_openai_response(raw_response)` | Extracts `content`, `tool_calls`, `finish_reason`, `usage`, `reasoning_content`. Raises on top-level `error` field. |
| `_is_tools_not_supported_error(err_str)` | Detects "does not support tools" patterns for ReAct fallback |
| `test_tool_support(model, family, force_test)` | Runtime test: send a probe tool call, detect NATIVE/REACT/NONE |
| `_jev_call_completions(model, messages, ...)` | JEV api_mode hook — routes through `generate()` so auth + 429 retry are preserved |

### Configuration

```bash
# Required
export OPENROUTER_API_KEY="sk-or-v1-..."

# Optional
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"  # default
export OPENROUTER_DEFAULT_MODEL="anthropic/claude-3.5-sonnet"  # default
export OPENROUTER_FREE_ONLY=1  # filter to :free models only
```

### Configuration env vars

AgentKthx uses the `AGENTKTHX_*` env var prefix (renamed from `AGENTNOVA_*`
in R06.41 — no aliases retained):
- `AGENTKTHX_BACKEND=openrouter` — set default backend
- `AGENTKTHX_API_MODE=openai` — OpenRouter only supports OpenAI mode

### What AgentKthx does NOT yet implement (potential future work)

- **`reasoning` parameter object** — for thinking models (`{"effort": "high", "max_tokens": 1024, "exclude": false}`)
- **`provider` preferences** — would let users pin to specific providers
- **`transforms`** — would enable auto-truncation for long conversations
- **`plugins`** — would enable web search via OpenRouter's plugin system
- **`min_p`, `repetition_penalty`, `top_a`, `logit_bias`** — sampling parameters in `_build_openai_body()`
- **`/keys` and `/credits` endpoints** — could show remaining balance in CLI

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401 Unauthorized` | Invalid API key | Check `OPENROUTER_API_KEY` env var; regenerate key at openrouter.ai/keys |
| `402 Payment Required` | No credits + non-free model | Switch to `:free` model OR fund account at openrouter.ai/credits |
| `429 Too Many Requests` | Hit rate limit | Wait `Retry-After` seconds (auto-retried up to 3x by AgentKthx) |
| `429 Provider X rate limited` | Upstream provider throttling | Wait longer (provider-level, not account-level) |
| Empty response (no content, no tool_calls) | Provider silently failed (filter, model issue) | Retry; check `--debug` for finish_reason |
| `does not support tools` | Free model lacks tool calling | AgentKthx auto-falls-back to ReAct; or use `--force-react` upfront |
| Slow startup (`agentkthx models --backend openrouter`) | `/models` endpoint slow, cache cold | Subsequent calls within 1 hour use cache |
| `model not found` | Model ID typo or removed from OpenRouter | Check `agentkthx models --backend openrouter` for current list |
| Streaming response missing usage | `:free` model doesn't send usage chunk | AgentKthx falls back to estimating tokens from content (`chars ÷ 4`). Footer still updates with approximate counts. |
| Reasoning not displayed with `--think` | Streaming path doesn't capture `reasoning_content` | R06.53: streaming now captures and displays reasoning_content inline. This row is retained for historical reference. |
| Token count way too high | Conversation history growing unbounded | Use `/clear` in chat mode; or `--session` to persist between runs |

---

## References

- **OpenRouter Docs**: https://openrouter.ai/docs
- **API Reference**: https://openrouter.ai/docs/api-reference/overview
- **Models List**: https://openrouter.ai/models
- **Pricing**: https://openrouter.ai/pricing
- **Free Models**: https://openrouter.ai/openrouter/free
- **Provider Routing**: https://openrouter.ai/docs/features/provider-routing
- **Rate Limits**: https://openrouter.ai/docs/api-reference/limits
- **Error Codes**: https://openrouter.ai/docs/api-reference/errors

---

Written by VTSTech — https://www.vts-tech.org
