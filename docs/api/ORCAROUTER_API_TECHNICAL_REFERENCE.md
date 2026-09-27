# OrcaRouter API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://docs.orcarouter.ai
> **Last Updated**: 2026-09-26
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Sampling Parameters](#sampling-parameters)
4. [Model Catalog & Discovery](#model-catalog--discovery)
5. [Function Calling Implementation](#function-calling-implementation)
6. [Streaming & Real-time Features](#streaming--real-time-features)
7. [Fallback Chains & Named Routers](#fallback-chains--named-routers)
8. [Error Codes & Recovery](#error-codes--recovery)
9. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
10. [Free Tier Behavior](#free-tier-behavior)
11. [Per-Request Cost Reporting](#per-request-cost-reporting)
12. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
13. [Proposed plugin.json](#proposed-pluginjson)
14. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — primary API surface (OpenAI-compatible)
BASE_URL = "https://api.orcarouter.ai/v1"

# Free-tier package info (anonymous, no auth required)
FREE_PACKAGE_URL = "https://api.orcarouter.ai/api/free-package/public"
```

OrcaRouter is an OpenAI-compatible API gateway that routes requests to 11 upstream providers at **provider cost price** — zero per-token markup. The gateway's revenue comes from optional paid subscription plans, not from inflating token costs. Every request and response uses the OpenAI JSON shape; the gateway translates to each upstream provider's native protocol as needed.

### API Endpoints

```python
# === Chat Completions API (OpenAI-compatible — primary AgentKthx surface) ===
CHAT_COMPLETIONS = "/chat/completions"             # POST

# === Responses API (OpenAI-compatible, newer stateful) ===
RESPONSES_CREATE = "/responses"                     # POST

# === Anthropic Messages (Anthropic-native surface) ===
MESSAGES = "/messages"                               # POST (at /v1/messages)

# === Gemini Native (passthrough) ===
GEMINI_NATIVE = "/v1beta/models/{model}:{action}"   # POST

# === Model discovery ===
MODELS_LIST    = "/models"                           # GET (anonymous — no auth required)
MODELS_RETRIEVE = "/models/{model}"                  # GET

# === Free-tier info (anonymous) ===
FREE_PACKAGE   = "https://api.orcarouter.ai/api/free-package/public"  # GET

# === Images / Audio / Video (not chat backends — listed for completeness) ===
IMAGES_GENERATE = "/images/generations"              # POST
AUDIO_SPEECH    = "/audio/speech"                    # POST
VIDEO_GENERATE  = "/video/generations"               # POST (async — Kling, Seedance, MiniMax)
```

### Authentication Headers

```python
headers = {
    "Authorization": "Bearer sk-orca-...",
    "Content-Type":  "application/json",
}
```

**API key types**: All keys start with `sk-orca-`. Keys are issued from the OrcaRouter Dashboard at `https://www.orcarouter.ai/console`. Per-key options at creation:
- **Name** — label for the key
- **Credit limit** — cap on total spend in USD (leave blank for unlimited)
- **Expiration** — fixed lifetime (1 hour up to 1 year, or never)
- **Allowed models** — restrict which models the key can call (includes router aliases as units)

Rate limits are applied at the **workspace** level, not per-key. All keys belonging to the same workspace draw from the same bucket.

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: standard OpenAI-compatible limits
- **Timeout**: 120 seconds recommended (configurable in `BackendConfig.timeout`)
- **HTTPS only**

### Response Headers (OrcaRouter-specific)

Every response includes these headers (in addition to standard OpenAI headers):

| Header | Description |
|-------|-------------|
| `X-Orca-Request-Id` | Unique request ID for troubleshooting |
| `X-Orca-Fallback-Level` | Which fallback level served the request (0 = primary, 1+ = fallback) |
| `X-Orca-Fallback-Model` | The model that actually served the request (may differ from requested if fallback fired) |
| `X-Orca-Router` | Which named router resolved the request (if any) |
| `X-Orca-Resolved-Model` | The final resolved model ID after router/fallback resolution |

---

## Request/Response Structure

### Complete Request Schema (Chat Completions — OpenAI-compatible)

```json
{
    "model": "openai/gpt-4o-mini",
    "messages": [
        {
            "role": "system|user|assistant|tool|function",
            "content": "string|array",
            "name": "string (optional)",
            "tool_calls": "array (assistant only)",
            "tool_call_id": "string (tool only)"
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "max_tokens": 2048,
    "max_completion_tokens": 2048,
    "stream": false,
    "stream_options": {"include_usage": true},
    "n": 1,
    "stop": ["###"],
    "seed": 42,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "logit_bias": {},
    "logprobs": false,
    "top_logprobs": null,
    "response_format": {"type": "text|json_object|json_schema"},
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": {"type": "object", "properties": {}, "required": []},
                "strict": false
            }
        }
    ],
    "tool_choice": "auto|none|required|{...}",
    "parallel_tool_calls": true,
    "reasoning_effort": "low|medium|high",
    "web_search_options": {"search_context_size": "low|medium|high"},
    "user": "user-identifier",
    "extra_body": {
        "models": ["openai/gpt-4o", "anthropic/claude-haiku-4.5", "google/gemini-2.5-flash"],
        "route": "fallback"
    }
}
```

### Response Schema

```json
{
    "id": "chatcmpl-xxx",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "openai/gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "function_name",
                            "arguments": "{\"param1\": \"value1\"}"
                        }
                    }
                ]
            },
            "finish_reason": "stop|length|tool_calls|content_filter|function_call"
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150
    }
}
```

### `extra_body` — OrcaRouter-Specific Extensions

The `extra_body` object is OrcaRouter's extension point for gateway-specific features:

```json
{
    "extra_body": {
        "models": [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-flash"
        ],
        "route": "fallback"
    }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `models` | `list[str]` | Ordered fallback chain. If the primary `model` fails, OrcaRouter tries each model in sequence. Maximum 5 models. |
| `route` | `"fallback"` | Set to `"fallback"` to enable the `models` chain. |

### `finish_reason` Semantics

| `finish_reason` | Cause | AgentKthx handling |
|-----------------|-------|--------------------|
| `stop` | Natural end or stop sequence | Finalize assistant turn |
| `length` | `max_tokens` reached | Surface truncation warning, do not retry |
| `tool_calls` | Model emitted tool calls | Invoke tools, append results, loop |
| `content_filter` | Safety filter blocked output | Log, do not retry with same prompt |
| `function_call` | Legacy function call (deprecated) | Migrate to `tools` API |

---

## Sampling Parameters

OrcaRouter forwards all OpenAI-standard sampling parameters to the upstream provider. The gateway translates between providers as needed (e.g. OpenAI `tools` → Anthropic `input_schema`, OpenAI `tools` → Gemini `functionDeclarations`).

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `temperature` | float | 0.0–2.0 | provider default | Controls randomness |
| `top_p` | float | 0.0–1.0 | 1.0 | Nucleus sampling |
| `max_tokens` | int | 1+ | provider default | Max output tokens (legacy field) |
| `max_completion_tokens` | int | 1+ | provider default | Preferred for reasoning models |
| `n` | int | 1+ | 1 | Number of completions |
| `stop` | str / list[str] | — | None | Stop sequences (up to 4) |
| `seed` | int | any | None | Deterministic sampling |
| `presence_penalty` | float | -2.0 to 2.0 | 0.0 | Penalize tokens already present |
| `frequency_penalty` | float | -2.0 to 2.0 | 0.0 | Penalize proportional to frequency |
| `logit_bias` | dict | any | {} | Token ID → bias (-100 to +100) |
| `logprobs` | bool | — | false | Return logprobs |
| `top_logprobs` | int | 0–20 | None | Top logprobs per token |
| `reasoning_effort` | str | low/medium/high | — | For OpenAI reasoning models (o1, o3, o4, gpt-5*-pro) |
| `response_format` | object | — | text | Structured output: text / json_object / json_schema |
| `parallel_tool_calls` | bool | — | true | Allow multiple tool calls in one response |

---

## Model Catalog & Discovery

### `/v1/models` endpoint (anonymous)

```bash
curl https://api.orcarouter.ai/v1/models
# Returns HTTP 200 without auth — model listing is public
```

```json
{
    "object": "list",
    "data": [
        {
            "id": "openai/gpt-4o",
            "object": "model",
            "created": 1626777600,
            "owned_by": "openai",
            "supported_endpoint_types": ["openai"]
        },
        {
            "id": "anthropic/claude-sonnet-4.6",
            "object": "model",
            "owned_by": "anthropic",
            "supported_endpoint_types": ["anthropic", "openai"]
        }
    ]
}
```

Key fields:
- **`id`** — provider-prefixed model ID (e.g. `openai/gpt-4o-mini`, `anthropic/claude-sonnet-4.6`)
- **`owned_by`** — the upstream provider
- **`supported_endpoint_types`** — which API surfaces the model supports (`openai`, `anthropic`, `gemini`)

### Provider Prefixes (11 upstream providers)

| Prefix | Provider | Examples |
|--------|----------|---------|
| `openai/` | OpenAI | `openai/gpt-4o-mini`, `openai/gpt-5`, `openai/o3-mini` |
| `anthropic/` | Anthropic | `anthropic/claude-sonnet-4.6`, `anthropic/claude-opus-4.7` |
| `google/` | Google Gemini | `google/gemini-2.5-flash`, `google/gemini-3-pro-preview` |
| `deepseek/` | DeepSeek | `deepseek/deepseek-chat`, `deepseek/deepseek-reasoner` |
| `grok/` | xAI Grok | `grok/grok-4-fast-reasoning` |
| `qwen/` | Alibaba Qwen | `qwen/qwen3.6-plus`, `qwen/qwen3-max` |
| `kimi/` | Moonshot Kimi | `kimi/kimi-k2.5`, `kimi/kimi-k2.6` |
| `minimax/` | MiniMax | `minimax/minimax-m2.7`, `minimax/minimax-h3` |
| `z-ai/` | Z.ai (GLM) | `z-ai/glm-5.1`, `z-ai/glm-4.5` |
| `kling/` | Kling (video) | `kling/kling-v3-omni` |
| `byteplus/` | ByteDance Seedance (video) | `byteplus/dreamina-seedance-2-0-260128` |

### Naming conventions

Model IDs are **provider-prefixed** by default. The bare upstream name (e.g. `gpt-4o-mini`) may also work if an alias is configured, but the default listing always uses the prefixed form. Named routers (`orcarouter/auto`, `orcarouter/free`) resolve to a model at request time.

---

## Function Calling Implementation

### Tool schema (OpenAI-compatible)

OrcaRouter accepts the standard OpenAI tool schema and translates it to each upstream's native shape:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"]
        },
        "strict": false
    }
}
```

### Cross-provider tool translation

| Upstream | Translation |
|----------|-------------|
| OpenAI / Grok / DeepSeek | Native — `tools` keeps OpenAI shape |
| Anthropic | OpenAI `tools` → Anthropic `tools` with `input_schema` |
| Google Gemini | OpenAI `tools` → Gemini `functionDeclarations` |

### Gemini reserved function names

On Gemini targets, three reserved `function.name` values map to Gemini's built-in tools:

| Reserved name | Maps to |
|---------------|---------|
| `googleSearch` | Gemini Google Search grounding |
| `codeExecution` | Gemini built-in code execution |
| `urlContext` | Gemini built-in URL-context tool |

### Tool choice

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Model decides |
| `"none"` | Force text response |
| `"required"` | Must call at least one tool |
| `{"type": "function", "function": {"name": "X"}}` | Force specific function |

---

## Streaming & Real-time Features

### SSE streaming (OpenAI-compatible)

Set `"stream": true` for Server-Sent Events:

```
data: {"id":"...","choices":[{"delta":{"content":"Hello"}}]}

data: {"id":"...","choices":[{"delta":{"content":" world"}}]}

data: {"id":"...","choices":[{"finish_reason":"stop"}]}

data: [DONE]
```

Pass `stream_options: {"include_usage": true}` to get the final `usage` object in the chunk before `[DONE]`.

### Anthropic Messages streaming

Anthropic uses **named SSE events** on the `/v1/messages` endpoint:

```
event: message_start
data: {...}

event: content_block_delta
data: {...}

event: message_stop
data: {...}
```

### Errors during a stream

Errors emitted mid-stream cannot use HTTP status codes (the status was sent when the stream opened). OrcaRouter sends an error SSE chunk with the same JSON envelope as a normal error response, then closes the stream.

---

## Fallback Chains & Named Routers

### Fallback chains (`extra_body.models`)

```json
{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello"}],
    "extra_body": {
        "models": [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
            "google/gemini-2.5-flash"
        ],
        "route": "fallback"
    }
}
```

If the primary `model` fails, OrcaRouter tries each model in the `models` array in order until one succeeds or the list is exhausted. Maximum 5 models in the chain. Response headers (`X-Orca-Fallback-Level`, `X-Orca-Fallback-Model`) indicate which model actually served the request.

### Named routers

| Router | Behavior |
|--------|----------|
| `orcarouter/auto` | Picks the cheapest live chat model at request time (seeded on signup for every account) |
| `orcarouter/free` | Routes across the workspace's free models by difficulty (never touches paid capacity) |

### `orcarouter/free` — Free models router

The free router routes each request by difficulty across the workspace's free models. It **never escapes to paid capacity** — when free models are saturated, it returns `free_quota_exhausted` instead of falling back to a paid model. Use `extra_body.models` with a paid base model to escape the free tier.

---

## Error Codes & Recovery

### Error envelope

```json
{
    "error": {
        "message": "Descriptive error message",
        "type": "orcarouter_api_error",
        "code": "model_not_found"
    }
}
```

`type` is a broad category; `code` is a specific identifier. Some errors add `error.metadata` with structured detail.

### HTTP status codes

| Status | Meaning | Typical cause |
|--------|---------|---------------|
| `400` | Bad request | Invalid parameters, missing fields, schema violation, guardrail block |
| `401` | Unauthorized | Missing or invalid API key |
| `403` | Forbidden | Several causes — check `error.code` (cycle spend limit, insufficient quota, access denied, free_quota_exhausted) |
| `404` | Not found | Model or endpoint doesn't exist |
| `425` | Too early | Model announced but not live yet (`metadata.expected_window`, `metadata.closest_live_alternative`) |
| `429` | Too many requests | Rate limit — carries `Retry-After` header. Free-tier rejections say why in `error.metadata.reason` |
| `500` | Internal error | OrcaRouter-side bug |
| `502` | Upstream error | All upstream providers failed (including fallback chain) |
| `503` | Service unavailable | Model temporarily unavailable, or BYOK key couldn't be used |

### Error codes

| `error.code` | Status | Description |
|--------------|--------|-------------|
| `insufficient_user_quota` | 402/403 | Workspace balance exhausted |
| `access_denied` | 403 | Key not permitted (cycle spend limit, IP allowlist) |
| `free_quota_exhausted` | 403 | `orcarouter/free` had no free model available |
| `model_not_found` | 503 | Model not available for your account |
| `model_not_yet_available` | 425 | Model announced but not live yet |
| `model_price_error` | 400 | Pricing not configured for this model |
| `api_not_implemented` | 400 | Endpoint not implemented for this model |
| `free_rate_limited` | 429/400 | Free-tier rejection — branch on `metadata.reason` |
| `byok:key_unavailable` | 503 | BYOK key could not be used |

---

## Rate Limiting & Concurrency

### Workspace-scoped limits

OrcaRouter rate-limits at the **workspace** level, not per API key. All keys in the same workspace share the same bucket. The gateway does not expose `X-RateLimit-Remaining` / `X-RateLimit-Reset` headers — treat 429 as the signal.

### 429 handling

```python
retry_after = response.headers.get("Retry-After", "10")
# Wait that many seconds, then retry
# For ordinary 429s: exponential backoff (1x → 2x → ... up to 60s)
# For free-tier 429s: wait exactly retry_after_seconds, retry once (NOT exponential — fixed windows)
```

### No per-model rate limits

OrcaRouter does not expose per-model rate limits to callers. Internal throttling toward upstream providers happens transparently. The gateway behaves like a single logical provider from the application's view.

---

## Free Tier Behavior

### What "free" means on OrcaRouter

OrcaRouter has genuinely free models — `$0/token`, no provider cost charged to the user. These are separate from the "zero markup" pricing model (where you pay the provider's per-token rate with $0 added by OrcaRouter). The free models are genuinely $0/token.

### Free models (confirmed via `GET /api/free-package/public`)

```json
{
    "data": {
        "active": true,
        "models": [
            {
                "free_alias": "deepseek/deepseek-v4-flash-free",
                "base_model": "deepseek/deepseek-v4-flash",
                "is_free_tier": true
            },
            {
                "free_alias": "orca/orcaverify-text1.0-free",
                "base_model": "orca/orcaverify-text1.0",
                "is_free_tier": true,
                "override_rpm": 5,
                "override_rpd": 50
            },
            {
                "free_alias": "tencent/hy3-free",
                "base_model": "tencent/hy3",
                "is_free_tier": true
            },
            {
                "free_alias": "z-ai/glm-5.3-flash-free",
                "base_model": "z-ai/glm-5.3-flash",
                "is_free_tier": true
            }
        ]
    }
}
```

| Free alias | Base model | Rate limit | Notes |
|-----------|------------|------------|-------|
| `deepseek/deepseek-v4-flash-free` | `deepseek/deepseek-v4-flash` | per-minute + per-UTC-day fixed windows | DeepSeek V4 Flash |
| `orca/orcaverify-text1.0-free` | `orca/orcaverify-text1.0` | 5 RPM / 50 RPD | OrcaRouter's verification model |
| `tencent/hy3-free` | `tencent/hy3` | per-minute + per-UTC-day fixed windows | Tencent Hy3 |
| `z-ai/glm-5.3-flash-free` | `z-ai/glm-5.3-flash` | per-minute + per-UTC-day fixed windows | ZAI GLM-5.3 Flash |

### `orcarouter/free` named router

Routes by difficulty across the workspace's free models. **Never touches paid capacity** — when free models are saturated, returns `free_quota_exhausted` (403) or `free_rate_limited` (429/400) instead of falling back to a paid model. Use `extra_body.models` with a paid base model to escape the free tier.

### Free-tier rate limiting

Free traffic runs on **fixed windows** — a per-minute bucket and a per-UTC-day bucket (not sliding window or token bucket). When a window trips, `Retry-After` gives the seconds left. **Do not use exponential backoff for free-tier 429s** — the window refills entirely at its boundary, so backing off further just wastes time. Wait exactly `retry_after_seconds` and retry once.

### Free-tier error reasons (`metadata.reason`)

| `metadata.reason` | Status | Cause | Fix |
|-------------------|--------|-------|-----|
| `err_free_rate` with `retry_after_seconds` | 429 | Rate window full (per-minute or per-day bucket) | Wait `retry_after_seconds`, retry once |
| `err_free_rate` without `retry_after_seconds` | 429 | Free channel timed out upstream — capacity saturated | Retry after short pause, or call paid base model |
| `err_free_prompt_cap` | 400 | Per-request prompt-token cap exceeded | Shorten prompt (not retryable) |
| `err_free_access_denied` | 429 | Workspace owner hasn't linked established GitHub account | Link GitHub account or add credits |

### Free-tier access requirements

Free-tier access requires the workspace owner to have linked an established GitHub account. Workspaces that have paid enough are exempt from this requirement. The `metadata.buy_credits_url` field is always present on free-tier rejections.

---

## Per-Request Cost Reporting

### `X-OrcaRouter-Include-Cost` header

```bash
curl https://api.orcarouter.ai/v1/chat/completions \
    -H "Authorization: Bearer sk-orca-..." \
    -H "X-OrcaRouter-Include-Cost: true" \
    -d '{"model": "openai/gpt-4o-mini", "messages": [...]}'
```

When set to `true` (or `1`, case-insensitive), the billed cost is added to the response's `usage` object:

```json
{
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost_usd": 0.000075
    }
}
```

On streams, the cost rides the frame carrying final usage (send `stream_options: {"include_usage": true}`).

For non-OpenAI formats: `usageMetadata.costUsd` on the Gemini surface.

---

## Implementation Notes for AgentKthx

### Backend file location (proposed)

```
agentkthx/plugins/orcarouter/
├── __init__.py            # register()/unregister()
├── plugin.json            # plugin manifest (see Proposed plugin.json below)
└── orcarouter.py          # OrcaRouterBackend class
```

### Key methods (mirror OpenRouterBackend / HuggingFaceBackend shape)

| Method | Purpose |
|--------|---------|
| `__init__()` | Initializes with `ORCAROUTER_API_KEY` env var (prefix `sk-orca-`), sets HTTP headers, populates `_model_cache` via `list_models()` |
| `list_models()` | Fetches `/v1/models` (anonymous — no auth required), caches for 1 hour, filters to `ORCAROUTER_FREE_MODEL_WHITELIST` if `ORCAROUTER_FREE_ONLY=true` |
| `is_running()` | Always returns `True` (cloud API) |
| `generate(model, messages, tools, **kwargs)` | Main entry point. Dispatches to `_make_api_request()`. Implements ReAct fallback. Enforces `ORCAROUTER_FREE_ONLY` whitelist. Forwards `extra_body` for fallback chains. |
| `generate_stream(model, messages, **kwargs)` | SSE streaming variant |
| `_make_api_request(endpoint, data, stream)` | HTTP wrapper with 429 retry (exponential for paid, fixed-wait for free-tier), 401 detection, 402/403 `insufficient_user_quota` / `free_quota_exhausted` handling |
| `_build_openai_body(...)` | Body construction. Adds `extra_body.models` + `extra_body.route: "fallback"` when `ORCAROUTER_FALLBACK_MODELS` env var is set. Adds `X-OrcaRouter-Include-Cost: true` header for per-request cost reporting. |
| `_parse_openai_response(raw_response)` | Extracts content, tool_calls, finish_reason, usage, `cost_usd` (if present) |
| `_is_free_rate_limited(err_str)` | Detects `free_rate_limited` / `err_free_rate` for free-tier-specific handling (fixed-window retry, NOT exponential) |
| `test_tool_support(model, family, force_test)` | Returns NATIVE for chat-capable models, NONE for non-chat (video, image gen, TTS) |
| `_jev_call_completions(model, messages, ...)` | JEV api_mode hook |

### Configuration

```bash
# Required
export ORCAROUTER_API_KEY="sk-orca-..."

# Optional
export ORCAROUTER_BASE_URL="https://api.orcarouter.ai/v1"  # default
export ORCAROUTER_DEFAULT_MODEL="orcarouter/auto"          # default — cheapest live model
export ORCAROUTER_FREE_ONLY="true"                          # strict $0/token whitelist (4 models)
export ORCAROUTER_FREE_FALLBACK_MODEL="orcarouter/free"     # free router on 403/429
export ORCAROUTER_FALLBACK_MODELS="openai/gpt-4o-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash"
export ORCAROUTER_INCLUDE_COST="true"                       # per-request cost reporting
```

### What AgentKthx should implement

- **ORCAROUTER_FREE_MODEL_WHITELIST** — 4 genuinely free models: `deepseek/deepseek-v4-flash-free`, `orca/orcaverify-text1.0-free`, `tencent/hy3-free`, `z-ai/glm-5.3-flash-free`
- **`orcarouter/free` named router** — use as the `ORCAROUTER_FREE_FALLBACK_MODEL` (routes across free models by difficulty, never escapes to paid)
- **Free-tier rate-limit handling** — `free_rate_limited` / `err_free_rate` with `metadata.reason` → fixed-window retry (wait exactly `retry_after_seconds`, retry once — NOT exponential backoff)
- **`extra_body.models` fallback chains** — env-var-driven `ORCAROUTER_FALLBACK_MODELS` for cross-provider resilience
- **Per-request cost reporting** — `X-OrcaRouter-Include-Cost: true` header → `usage.cost_usd` in response (surface in token tracking UI)
- **Response headers** — capture `X-Orca-Fallback-Model` / `X-Orca-Resolved-Model` for debug output (which model actually served the request after router/fallback resolution)
- **`GET /api/free-package/public`** — probe on init to validate free-tier availability + show free-tier rate limits in `--debug` output (anonymous, no auth required)

### What AgentKthx should NOT implement (potential future work)

- **Anthropic Messages surface** (`/v1/messages`) — OrcaRouter supports it natively, but AgentKthx's `OpenAICompatibleBackend` only speaks the OpenAI shape
- **Gemini Native surface** (`/v1beta/...`) — same reasoning
- **Responses API** (`/v1/responses`) — newer stateful endpoint, deferred to v0.2
- **Image/Audio/Video generation** — not chat backends
- **BYOK (Bring Your Own Key)** — OrcaRouter supports per-workspace provider keys, but AgentKthx uses OrcaRouter's key only
- **Web search** — `web_search_options` / `tools: [{"type": "web_search"}]` for search-augmented responses

---

## Proposed plugin.json

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "orcarouter",
  "version": "0.1.0",
  "description": "OrcaRouter API backend for 200+ models via 11 upstream providers at zero markup (free tier with 4 genuinely $0/token models + paid, with ORCAROUTER_FREE_ONLY enforcement and fallback-chain routing)",
  "author": {
    "name": "VTSTech",
    "url": "https://www.vts-tech.org"
  },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "OrcaRouter Cloud Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "ORCAROUTER",
        "defaults": {
          "ORCAROUTER_BASE_URL": "https://api.orcarouter.ai/v1",
          "ORCAROUTER_API_KEY": "",
          "ORCAROUTER_DEFAULT_MODEL": "orcarouter/auto",
          "ORCAROUTER_FREE_ONLY": "false",
          "ORCAROUTER_FREE_FALLBACK_MODEL": "orcarouter/free",
          "ORCAROUTER_FALLBACK_MODELS": "",
          "ORCAROUTER_INCLUDE_COST": "true"
        }
      },
      "provides": {
        "backends": {
          "orcarouter": "orcarouter.OrcaRouterBackend"
        },
        "cli_commands": [],
        "cli_flags": {
          "--backend": [
            "orcarouter",
            "orca"
          ]
        }
      },
      "compatibility": {
        "agentkthx": ">=0.5.0"
      }
    }
  }
}
```

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401 Unauthorized` | Invalid/expired API key | Check `ORCAROUTER_API_KEY`; regenerate at orcarouter.ai/console |
| `403 insufficient_user_quota` | Workspace balance exhausted | Add credits at orcarouter.ai/console |
| `403 access_denied` (cycle spend limit) | API key hit its recurring spend limit | Wait for UTC reset or raise the key's cycle limit |
| `403 free_quota_exhausted` | `orcarouter/free` had no free model available | Call a paid base model — free router never escapes to paid |
| `429 err_free_rate` with `retry_after_seconds` | Free-tier rate window full (per-minute or per-day) | Wait exactly `retry_after_seconds`, retry once (NOT exponential) |
| `429 err_free_rate` without `retry_after_seconds` | Free channel timed out upstream | Retry after short pause, or call paid base model |
| `400 err_free_prompt_cap` | Free-tier per-request prompt-token cap exceeded | Shorten prompt (not retryable) |
| `429 err_free_access_denied` | Workspace owner hasn't linked established GitHub account | Link GitHub account or add credits |
| `425 model_not_yet_available` | Model announced but not live | Use `metadata.closest_live_alternative`; same ID routes automatically at launch |
| `502 Upstream error` | All providers failed (including fallback chain) | Check `X-Orca-Fallback-Level` to see how far the chain got; add more fallback models |
| `503 model_not_found` | Model not available for your account | Check `agentkthx models --backend orcarouter` for current list |
| Empty response | Provider silently failed | Check `X-Orca-Resolved-Model` for which model actually served; retry with different model |
| `free_rate_limited` during free-tier usage | Free models saturated | Use `ORCAROUTER_FREE_ONLY=false` to use paid models, or wait for rate window reset |

---

## References

- **OrcaRouter Docs**: https://docs.orcarouter.ai
- **API Reference (Chat)**: https://docs.orcarouter.ai/api-reference/chat/create-a-chat-completion
- **Models**: https://docs.orcarouter.ai/getting-started/models
- **Quickstart**: https://docs.orcarouter.ai/getting-started/quickstart
- **Streaming**: https://docs.orcarouter.ai/advanced/streaming
- **Tool Calling**: https://docs.orcarouter.ai/advanced/tool-calling
- **Rate Limits**: https://docs.orcarouter.ai/operations/rate-limits
- **Errors**: https://docs.orcarouter.ai/operations/errors
- **Free Models**: https://docs.orcarouter.ai/routing/free-models
- **Model Fallbacks**: https://docs.orcarouter.ai/routing/model-fallbacks
- **Pricing**: https://www.orcarouter.ai/pricing
- **Models Page**: https://www.orcarouter.ai/models
- **Free Package API**: https://api.orcarouter.ai/api/free-package/public (anonymous GET)
- **llms.txt**: https://docs.orcarouter.ai/llms.txt (full documentation index)

---

Written by VTSTech — https://www.vts-tech.org
