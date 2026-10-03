# OpenRouter API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://openrouter.ai/docs (App Attribution, API Reference, Usage Accounting, OpenAPI spec)
> **Last Updated**: 2026-10-03 (R07.21 — App Attribution headers + Client/Harness Metrics section)
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [App Attribution — Marketplace Headers](#app-attribution--marketplace-headers)
3. [App Directory Entry — Category & Description](#app-directory-entry--category--description)
4. [Request/Response Structure](#requestresponse-structure)
5. [Sampling Parameters](#sampling-parameters)
6. [Model Catalog & Discovery](#model-catalog--discovery)
7. [Function Calling Implementation](#function-calling-implementation)
8. [Streaming & Real-time Features](#streaming--real-time-features)
9. [Provider Routing Preferences](#provider-routing-preferences)
10. [Transforms & Plugins](#transforms--plugins)
11. [Error Codes & Recovery](#error-codes--recovery)
12. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
13. [Free Tier Behavior](#free-tier-behavior)
14. [Client/Harness Metrics & Reporting](#clientharness-metrics--reporting)
15. [Generation Inspection & Audit Trail](#generation-inspection--audit-trail)
16. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
17. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production
BASE_URL = "https://openrouter.ai/api/v1"

# API Endpoints
CHAT_COMPLETIONS = "/chat/completions"   # OpenAI-compatible (primary surface)
MODELS = "/models"                       # Model catalog (no auth required for read)
GENERATE = "/generate"                   # Legacy completion endpoint (rarely used)
KEY = "/key"                              # Current API key info (auth required)
KEYS = "/keys"                            # List/create API keys (management key)
CREDITS = "/credits"                      # Credit balance (management key)
GENERATION = "/generation"                # Per-generation usage metadata (auth required)
ACTIVITY = "/activity"                    # User activity grouped by endpoint
```

The full OpenAPI 3.1 specification is published at `https://openrouter.ai/openapi.json` (111 paths) and `https://openrouter.ai/openapi.yaml`. AgentKthx's surface area is the Chat-Completions endpoint + the read-only catalog `/models`; the management/analytics endpoints (`/key`, `/credits`, `/generation`, `/activity`, `/datasets/*`) are not currently called by the plugin but are documented in the [Client/Harness Metrics & Reporting](#clientharness-metrics--reporting) section below.

### Authentication Headers

```python
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    # App Attribution headers (R07.21) — see the dedicated section below:
    "HTTP-Referer": "https://github.com/VTSTech/AgentKthx",
    "X-OpenRouter-Title": "AgentKthx",
    "X-Title": "AgentKthx",  # legacy alias — kept for backwards compat
    "X-OpenRouter-Categories": "cli-agent",
}
```

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 8MB (varies by upstream provider)
- **Timeout**: 120 seconds default (AgentKthx uses 120s via `BackendConfig.timeout`)

---

## App Attribution — Marketplace Headers

OpenRouter's [App Attribution spec](https://openrouter.ai/docs/app-attribution) lets a harness self-identify so its API traffic is associated with the harness's app entry on `openrouter.ai/apps`. Attribution is set entirely via HTTP headers on every request — there is no separate registration API. The headers are:

| Header | Required | Purpose |
|--------|----------|---------|
| `HTTP-Referer` | **Yes** | Primary URL identifier. OpenRouter uses this as the unique key for the app entry. Without this header, no app page is created and traffic is not attributed. |
| `X-OpenRouter-Title` | No (recommended) | Display name in rankings. Replaces the older `X-Title` header (which is still accepted for backwards compat). |
| `X-Title` | No (legacy) | Pre-R07.21 form of the title. Still accepted; superseded by `X-OpenRouter-Title`. |
| `X-OpenRouter-Categories` | No (recommended) | Comma-separated marketplace categories. Max **2 per request**, max **10 per app**. Unrecognized values are silently dropped. See [Category groups](#category-groups). |
| `X-OpenRouter-App-Visibility` | No | `hidden` creates the app hidden from public rankings (attribution still works, analytics still flow). Applies only when the request creates a brand-new app. |

### AgentKthx implementation (R07.21)

AgentKthx sends the four attribution headers on every OpenRouter request — both the `/chat/completions` calls and the `/models` catalog reads. The headers are centralized in a single helper, `_build_openrouter_attribution_headers()`, so the four header-construction sites (`__init__`, `list_models`, `_make_api_request`, `_get_auth_headers`) cannot drift out of sync:

```python
# agentkthx/plugins/openrouter/openrouter.py (module-level constants)
_APP_REFERER_URL = "https://github.com/VTSTech/AgentKthx"
_APP_TITLE = "AgentKthx"
_APP_CATEGORIES = "cli-agent"

def _build_openrouter_attribution_headers() -> dict:
    return {
        "HTTP-Referer": _APP_REFERER_URL,
        "X-OpenRouter-Title": _APP_TITLE,
        "X-Title": _APP_TITLE,  # legacy alias
        "X-OpenRouter-Categories": _APP_CATEGORIES,
    }
```

The values are **hardcoded constants, not user-overridable env vars**. The category describes what AgentKthx *is* to OpenRouter's marketplace — letting a user flip `cli-agent` to `creative-writing` would misclassify the harness in the rankings. If a fork needs a different identity, change the constants directly so the audit catches it (the `test_categories_are_hardcoded_not_env_overridable` regression test pins this contract).

### Why both `X-OpenRouter-Title` AND `X-Title`?

OpenRouter's docs state `X-Title` is "still supported for backwards compatibility". AgentKthx sends both forms so rankings that haven't been re-indexed to look for the new header still attribute traffic correctly. The two headers carry the same value (`AgentKthx`); there is no semantic difference. When OpenRouter formally deprecates `X-Title`, the legacy header can be dropped.

### Where the attribution appears

Once any request lands at OpenRouter carrying the headers, the app entry is created at `https://openrouter.ai/apps?url=<HTTP-Referer-value>`. For AgentKthx that resolves to:

- **App page**: https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FVTSTech%2FAgentKthx
- **Coding group**: https://openrouter.ai/apps/category/coding
- **CLI Agent subcategory**: https://openrouter.ai/apps/category/coding/cli-agent

The app entry includes:
- **Title**: `AgentKthx`
- **Origin URL**: `https://github.com/VTSTech/AgentKthx`
- **Categories**: `["cli-agent"]` (merged into the existing set on each request)
- **Total tokens**: cumulative token usage across all attributed requests
- **Models used**: count of distinct models with attributed traffic
- **Rank**: position within the category rankings (null until the app qualifies)

---

## App Directory Entry — Category & Description

### Categories

OpenRouter's marketplace organizes apps into **category groups**, each containing **leaf categories**. An app is listed on BOTH its group landing page AND each leaf-category subpage. Claiming a single leaf is enough to appear on the group landing — AgentKthx claims only `cli-agent` and automatically appears on `/apps/category/coding` as well as `/apps/category/coding/cli-agent`.

#### Category groups

**Coding** (tools for software development):

| Leaf | Description |
|------|-------------|
| `cli-agent` | Terminal-based coding assistants |
| `ide-extension` | Editor/IDE integrations |
| `cloud-agent` | Cloud-hosted coding agents |
| `programming-app` | Programming apps |
| `native-app-builder` | Mobile and desktop app builders |

**Creative** (creative apps):

| Leaf | Description |
|------|-------------|
| `creative-writing` | Creative writing tools |
| `video-gen` | Video generation apps |
| `image-gen` | Image generation apps |
| `audio-gen` | Audio generation apps |

**Productivity** (writing and productivity tools):

| Leaf | Description |
|------|-------------|
| `writing-assistant` | AI-powered writing tools |
| `general-chat` | General chat apps |
| `personal-agent` | Personal AI agents |
| `legal` | Legal tools and assistants |

**Entertainment** (entertainment apps):

| Leaf | Description |
|------|-------------|
| `roleplay` | Roleplay apps and other character-based chat apps |
| `game` | Gaming and interactive entertainment apps |

Rules (from OpenRouter's App Attribution doc):
- Max **2 categories per request** (the header is comma-separated)
- Max **10 categories per app** (OpenRouter merges across requests)
- Categories must be lowercase, hyphen-separated, max 30 chars each
- Only the recognized values above are accepted; unrecognized values are silently dropped (no error)
- Categories are merged with any existing ones — they don't replace

AgentKthx sends a single category, `cli-agent`, on every request. This is the most accurate fit: the harness is a terminal-based CLI (`agentkthx chat`, `agentkthx run`, `agentkthx turbo start`) that uses LLMs to drive an agentic loop. Adding `cloud-agent` would mis-describe AgentKthx as cloud-hosted (it's a stdlib-only local install); adding `programming-app` would dilute the more specific `cli-agent` signal.

### Description

OpenRouter's app directory has a `description` field on each app entry, but **it cannot be set via HTTP headers** — only via the web UI at `openrouter.ai/apps/url/<your-referer-url>`. OpenRouter's app data model includes `description` as a nullable string that the app owner populates through the dashboard.

The first time traffic with attribution headers lands at OpenRouter, the app entry is created with `description: null`. The owner (anyone who can prove control of the referer URL, or who holds the API key that created the entry) then visits `https://openrouter.ai/apps/url/<referer>` while logged in and uses the dashboard form to set the description.

AgentKthx's directory entry currently shows:
```json
{
  "app": {
    "categories": ["cli-agent"],
    "created_at": "2026-09-20T14:56:39.444Z",
    "description": null,       // <-- set this via the web dashboard
    "favicon_url": null,
    "icon_class_name": null,
    "id": 5072126,
    "main_url": null,
    "origin_url": "https://github.com/VTSTech/AgentKthx",
    "related_apps": [],
    "slug": null,
    "source_code_url": null,
    "title": "AgentKthx"
  },
  "totalTokens": 32995314,
  "rank": null,
  "modelsUsed": 14
}
```

The fields `description`, `main_url`, `slug`, `source_code_url`, `favicon_url`, `icon_class_name`, and `related_apps` are all set via the dashboard, not headers. Recommended values for AgentKthx:

| Field | Recommended value |
|-------|-------------------|
| `description` | `A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, souls, and skills.` |
| `main_url` | `https://github.com/VTSTech/AgentKthx` (same as `origin_url`) |
| `source_code_url` | `https://github.com/VTSTech/AgentKthx` |
| `slug` | `agentkthx` (lowercase, hyphen-separated) |
| `favicon_url` | (path to a square logo once one is published) |
| `icon_class_name` | (leave null — used for FontAwesome/CSS class icons) |

These dashboard-only fields are intentionally outside the plugin's control surface — they're presentation metadata, not runtime behavior, and OpenRouter gates them through web authentication to prevent arbitrary harnesses from spoofing identity.

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
    "repetition_penalty": 1.0,
    "min_p": 0.0,
    "top_a": 0.0,
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
        "cost": 0.0024,
        "cost_details": {
            "upstream_inference_cost": 0.0019
        },
        "prompt_tokens_details": {
            "cached_tokens": 0,
            "cache_write_tokens": 100,
            "audio_tokens": 0
        },
        "completion_tokens_details": {
            "reasoning_tokens": 0
        }
    }
}
```

Key differences from OpenAI's standard response:
- **`provider`**: Identifies which upstream provider served the request (Anthropic, Together, OpenAI, etc.)
- **`usage.cost`**: Always present — your actual cost in USD for the request
- **`usage.cost_details.upstream_inference_cost`**: What OpenRouter paid the upstream provider (BYOK-only on the `/generation` endpoint; populated for direct `/chat/completions` responses)
- **`usage.prompt_tokens_details.cached_tokens`**: Tokens read from prompt cache (lower cost)
- **`usage.prompt_tokens_details.cache_write_tokens`**: Tokens written to prompt cache (only returned for models with explicit caching)
- **`usage.completion_tokens_details.reasoning_tokens`**: Tokens spent on chain-of-thought (thinking models)
- **`reasoning` / `reasoning_content`**: Some upstream models (GLM-5.x, o1, o3) emit chain-of-thought. Field name varies by provider.
- **No `web_search` field**: Web search results come back as tool calls when `plugins: [{id: "web"}]` is used.

**Deprecated parameters (no-op)**: `usage: { include: true }` and `stream_options: { include_usage: true }` are deprecated — full usage details are now always included in every response. AgentKthx still sets `stream_options.include_usage` (harmless; the streaming path's `_parse_openai_response` reads usage from the final SSE chunk).

---

## Sampling Parameters

OpenRouter supports a wide range of sampling parameters. The `/models` endpoint's `supported_parameters` array on each model entry lists which ones a given model will accept — OpenRouter silently forwards unsupported ones and the provider decides.

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

### Reasoning parameter (thinking models)

```json
{
    "reasoning": {
        "effort": "low|medium|high",
        "max_tokens": 1024,
        "exclude": true
    }
}
```

- `effort`: How hard the model should think before answering (low/medium/high)
- `max_tokens`: Cap on reasoning tokens
- `exclude`: If true, reasoning tokens are not returned in the response (still counted in `usage.completion_tokens_details.reasoning_tokens`)

### AgentKthx implementation status

The AgentKthx `OpenRouterBackend._build_openai_body()` method (inherited from `OpenAICompatibleBackend`) currently forwards these parameters from `**kwargs`:

```python
optional_int_fields = ("top_p", "top_k", "seed", "n")
optional_float_fields = ("presence_penalty", "frequency_penalty")
```

Plus: `stop`, `response_format`, `tool_choice`, `reasoning_effort` (forwarded as a top-level field, NOT inside a `reasoning` object — this is a known limitation).

**Missing parameters worth adding** (tracked as potential future work):
- `min_p` — newer alternative to top_p, useful for small models
- `repetition_penalty` — different from frequency_penalty, supported by Llama-family
- `logit_bias` — useful for steering specific tokens
- `top_a` — alternative nucleus sampler
- `reasoning` object (the structured form) — current code only forwards `reasoning_effort` as a top-level field

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
                "top_k", "reasoning", "include_reasoning", "repetition_penalty",
                "logprobs", "top_logprobs"
            ],
            "default_parameters": {},
            "reasoning": {
                "mandatory": false,
                "default_enabled": false
            },
            "knowledge_cutoff": null,
            "expiration_date": null,
            "links": {
                "details": "/api/v1/models/anthropic/claude-3.5-sonnet/endpoints"
            }
        }
    ]
}
```

Key fields for AgentKthx to inspect (validated against the live API R07.21):
- **`context_length`** — the model's full context window
- **`top_provider.max_completion_tokens`** — the actual max output tokens (may be `null` for router models like `openrouter/free`; AgentKthx coerces null → 4096)
- **`pricing.prompt` / `pricing.completion`** — USD per token (use for cost display)
- **`supported_parameters`** — list of parameters the model will accept (notable: `reasoning`, `include_reasoning` for thinking models)
- **`default_parameters`** — model-defined defaults AgentKthx could honor
- **`architecture.modality`** — `text->text`, `text+image->text`, etc.
- **`reasoning`** — `{mandatory, default_enabled}` flags for thinking models
- **`links.details`** — relative URL to the per-model endpoint list

### Free model detection

Free models have `:free` suffix in their ID and zero pricing:

```python
def is_free_model(model_id: str) -> bool:
    return model_id == "openrouter/free" or model_id.endswith(":free")

# Examples:
# "meta-llama/llama-3.2-3b-instruct:free"
# "google/gemini-flash-1.5:free"
# "openrouter/free"  (named Free Models Router — always free)
```

When `OPENROUTER_FREE_ONLY=1` env var is set, AgentKthx filters the model list to only include free models. The filter applies at return time, not at cache-store time, so toggling the env var doesn't require waiting out the cache TTL.

### Live API data vs static catalog

AgentKthx maintains a static `OPENROUTER_MODELS` catalog (loaded from `agentkthx/data/model_seed.json` since R07.20) for fallback when the API is unreachable, but **prefers live API data** for `context_length` and `max_completion_tokens`. The static catalog is updated periodically.

Two-layer cache (R07.20):
- **L1** — in-process class cache (1-hour TTL)
- **L2** — persistent JSON cache at `~/.cache/agentkthx/model_catalog.json` (30-min TTL, `AGENTKTHX_MODEL_CACHE_TTL` override)

When the live API is unreachable, AgentKthx serves the stale JSON cache (any age) so the CLI still works offline. The first successful fetch stores the unfiltered determined catalog (the FREE_ONLY filter applies at return time only).

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

### Tool-support verdict caching

OpenRouter's `test_tool_support()` returns `ToolSupportLevel.NATIVE` for every model without probing — OpenRouter's `/v1/models` endpoint surfaces only chat-capable models that already support native function calling on their underlying provider. The defensive ReAct fallback at runtime (HTTP 400 → retry without `tools`) is the safety net for the rare case where a specific free/fine-tuned model rejects the `tools` field.

> **Note (ROB-39, OPEN)**: the unconditional `NATIVE` verdict overstates tool support for non-chat slugs (image/audio/moderation models) that happen to appear in the catalog. Runtime is safe (the 400 → ReAct fallback catches them) but the `agentkthx models` table shows `tools ✓ native` for them.

---

## Streaming & Real-time Features

### SSE streaming

Set `"stream": true` in the request body. OpenRouter returns Server-Sent Events:

```
data: {"id":"gen-123","choices":[{"delta":{"content":"Hello"}}]}

data: {"id":"gen-123","choices":[{"delta":{"content":" world"}}]}

data: {"id":"gen-123","choices":[{"finish_reason":"stop"}],"usage":{...}}

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

Setting `include_usage: true` causes OpenRouter to emit a final SSE chunk with `usage` populated. **Deprecated (no-op)**: per the Usage Accounting doc, OpenRouter now always includes usage in the final chunk regardless of this flag. AgentKthx still sets it for backwards compat with older OpenRouter deployments — harmless.

**Note:** Some `:free` models on OpenRouter do not return a usage chunk even when `include_usage=true` is sent. AgentKthx handles this with a fallback: if `total_tokens` is 0 after a step, it estimates tokens from message content (`chars ÷ 4`) so the footer's token counts and context % still update during the run.

### Reasoning content in streaming

For thinking-capable models (o1, o3, GLM-5.x), reasoning tokens arrive as separate SSE chunks:

```
data: {"choices":[{"delta":{"reasoning":"Let me think..."}}]}
data: {"choices":[{"delta":{"reasoning":"First I should..."}}]}
data: {"choices":[{"delta":{"content":"The answer is..."}}]}
```

The `reasoning` field appears in delta chunks BEFORE the `content` field. AgentKthx captures `reasoning_content` in both streaming and non-streaming responses (since R06.53). In streaming mode, reasoning deltas are displayed in a `reasoning:` panel above the `AgentKthx:` prompt.

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

**AgentKthx does not currently use transforms or plugins** — could be added as opt-in CLI flags (`--web_search`, `--auto-truncate`).

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
| 524 | Edge Network Timeout | Cloudflare-level timeout | Retry |
| 529 | Provider Overloaded | Upstream provider is overloaded | Retry with backoff |

### 429 Retry-After handling

OpenRouter sends a `Retry-After` header on 429 responses (seconds until you can retry):

```python
retry_after_raw = response.headers.get("Retry-After", "")
try:
    retry_after = float(retry_after_raw)
except (ValueError, TypeError):
    retry_after = None
if retry_after is None:
    retry_after = self._429_backoff(attempt + 1)  # exponential: 5s → 10s → 20s → 40s → 80s → 90s cap
retry_after = min(max(retry_after, 1.0), 90.0)  # cap at 90s
```

AgentKthx's `OpenRouterBackend._make_api_request()` implements automatic 429 retry with up to **6 retries** (`_MAX_429_RETRIES = 6`, R06.54 — was 3, raised because free-tier models return 429 constantly and 3 retries was never enough for an agentic run). Override with `OPENROUTER_MAX_429_RETRIES` env var. Each retry waits the `Retry-After` duration (capped at 90s), or an exponential backoff (5s base, 90s cap, ±20% jitter) if no header is present.

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

### Per-key limits (visible via `/key`)

The `/key` endpoint returns the per-key limit envelope:

```json
{
  "data": {
    "limit": 100,
    "limit_remaining": 74.5,
    "limit_reset": "monthly",
    "is_free_tier": false,
    "free_model_daily_requests": {
      "limit": 50,
      "remaining": 38,
      "used": 12
    }
  }
}
```

See [Client/Harness Metrics & Reporting](#clientharness-metrics--reporting) for the full schema.

### AgentKthx implementation

AgentKthx does **not** implement client-side rate limiting. It relies on:
1. The 429 retry loop in `_make_api_request()` (max 6 retries with `Retry-After` honor + exponential backoff)
2. The user to pace their requests if doing bulk operations

For bulk workflows (e.g. running `agentkthx test 04_gsm8k_benchmark`), users may want to add a client-side throttle. Could be a future R07.x feature.

---

## Free Tier Behavior

### Model ID convention

Free models have the `:free` suffix:
- `meta-llama/llama-3.2-3b-instruct:free`
- `google/gemini-flash-1.5:free`
- `qwen/qwen-2.5-7b-instruct:free`

Plus the named Free Models Router:
- `openrouter/free` — auto-routes to the cheapest available free model at request time. Always free. Documented at https://openrouter.ai/openrouter/free. AgentKthx's `OPENROUTER_DEFAULT_MODEL` is `openrouter/free`.

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

## Client/Harness Metrics & Reporting

OpenRouter exposes a rich analytics and accounting surface that AgentKthx does not currently consume but is documented here so future work can wire it up. All endpoints require authentication (Bearer token); the management endpoints (`/credits`, `/keys` POST/PATCH/DELETE) require a **management key** rather than a provisioning key.

### 1. Per-key usage envelope — `GET /key`

Returns information about the API key used for the current request — the most useful single endpoint for showing the user their remaining budget inline.

```bash
curl https://openrouter.ai/api/v1/key \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

```json
{
  "data": {
    "label": "sk-or-v1-au7...890",
    "is_free_tier": false,
    "is_management_key": false,
    "is_provisioning_key": false,
    "limit": 100,
    "limit_remaining": 74.5,
    "limit_reset": "monthly",
    "usage": 25.5,
    "usage_daily": 25.5,
    "usage_weekly": 25.5,
    "usage_monthly": 25.5,
    "byok_usage": 17.38,
    "byok_usage_daily": 17.38,
    "byok_usage_weekly": 17.38,
    "byok_usage_monthly": 17.38,
    "include_byok_in_limit": false,
    "free_model_daily_requests": {
      "limit": 50,
      "remaining": 38,
      "used": 12
    },
    "rate_limit": {
      "interval": "1h",
      "note": "This field is deprecated and safe to ignore.",
      "requests": 1000
    },
    "allowed_data_regions": ["global", "europe", "us"],
    "creator_user_id": "user_2dHFtVWx2n56w6Hk...",
    "workspace_id": "0df9e665-d932-5740-b2c7-b52af166bc11",
    "organization_id": null,
    "expires_at": "2027-12-31T23:59:59Z",
    "created_at": "2025-08-24T10:30:00Z"
  }
}
```

Fields most useful for an AgentKthx CLI display:
- `limit_remaining` — credits left in the current limit window
- `limit_reset` — when the limit window resets (`"monthly"`, `"daily"`, etc.)
- `free_model_daily_requests.remaining` — how many free-model calls are left today (50 cap for unfunded accounts)
- `usage_daily` / `usage_weekly` / `usage_monthly` — spend in each window
- `byok_usage_*` — usage that went through a Bring-Your-Own-Key (not charged by OpenRouter)
- `is_free_tier` — whether this key is on the unfunded-account tier

### 2. Total credit balance — `GET /credits`

```bash
curl https://openrouter.ai/api/v1/credits \
  -H "Authorization: Bearer $OPENROUTER_MANAGEMENT_KEY"
```

```json
{
  "data": {
    "total_credits": 100.5,
    "total_usage": 25.75
  }
}
```

Returns the lifetime credits purchased and the lifetime usage. Requires a **management key** (a provisioning key gets 403). Useful for a `agentkthx status` banner.

### 3. Per-generation usage metadata — `GET /generation?id=<gen-id>`

Returns the full usage breakdown for a single generation. Use this to audit a specific request after the fact — e.g. to find out which provider served it, what the upstream cost was, or whether tokens were cached.

```bash
curl "https://openrouter.ai/api/v1/generation?id=gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

```json
{
  "data": {
    "id": "gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG",
    "model": "sao10k/l3-stheno-8b",
    "provider_name": "Infermatic",
    "router": "openrouter/auto",
    "api_type": "completions",
    "app_id": 12345,
    "http_referer": "https://openrouter.ai/",
    "origin": "https://openrouter.ai/",
    "user_agent": "Mozilla/5.0",
    "external_user": "user-123",
    "request_id": "req-1727282430-aBcDeFgHiJkLmNoPqRsT",
    "upstream_id": "chatcmpl-791bcf62-080e-4568-87d0-94c72e3b4946",
    "preset_id": null,
    "session_id": null,
    "service_tier": "priority",
    "data_region": "global",
    "is_byok": false,
    "streamed": true,
    "cancelled": false,
    "created_at": "2024-07-15T23:33:19.433273+00:00",
    "generation_time": 1200,
    "latency": 1250,
    "moderation_latency": 50,
    "num_fetches": 0,
    "num_search_results": 5,
    "num_media_prompt": 1,
    "num_media_completion": 0,
    "num_input_audio_prompt": 0,
    "web_search_engine": "exa",
    "finish_reason": "stop",
    "native_finish_reason": "stop",
    "native_tokens_prompt": 10,
    "native_tokens_completion": 25,
    "native_tokens_reasoning": 5,
    "native_tokens_cached": 3,
    "native_tokens_completion_images": 0,
    "tokens_prompt": 10,
    "tokens_completion": 25,
    "total_cost": 0.0015,
    "upstream_inference_cost": 0.0012,
    "usage": 0.0015,
    "cache_discount": null,
    "provider_responses": null,
    "moderation_latency": 50
  }
}
```

Key fields for harness reporting:
- `total_cost` / `upstream_inference_cost` — what you paid vs. what OpenRouter paid the upstream
- `native_tokens_cached` — tokens read from prompt cache (cost-discounted)
- `native_tokens_reasoning` — tokens spent on chain-of-thought
- `latency` — total round-trip ms
- `moderation_latency` — time spent in OpenRouter's moderation layer
- `provider_name` — which upstream provider served the request
- `app_id` — the attribution app this generation was attributed to (matches the `id` field from the app directory entry)
- `http_referer` — the `HTTP-Referer` header value sent with the request
- `is_byok` — whether the request went through a Bring-Your-Own-Key

The `id` field is the value of `response.id` from the original `/chat/completions` call. AgentKthx's `_parse_openai_response` already preserves this in the `raw` field of the parsed response — wiring it up to a deferred `/generation` lookup would let the CLI show exact cost + provider attribution after the fact, even for streaming responses where the final usage chunk was missing.

> **Note**: `upstream_inference_cost` is only populated for BYOK requests on the `/generation` endpoint. For direct `/chat/completions` responses, it's available inline in `usage.cost_details.upstream_inference_cost`.

### 4. Activity endpoint — `GET /activity`

Returns per-day usage breakdowns grouped by model, app, endpoint, or workspace. Useful for a `agentkthx report` command.

```bash
curl "https://openrouter.ai/api/v1/activity?date=2026-10-02" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

Query parameters:
- `date` — single UTC date in the last 30 days (YYYY-MM-DD)
- `api_key_hash` — filter by API key hash (SHA-256 hex, as returned by `/keys`)
- `user_id` — filter by org member user ID (org accounts only)
- `workspace_id` — filter by workspace ID (UUID)
- `group_by` — `workspace` to split rows per workspace

### 5. App rankings dataset — `GET /datasets/app-rankings`

Returns the top public apps ranked by token usage in a date window — the same data that powers `openrouter.ai/apps`.

```bash
curl "https://openrouter.ai/api/v1/datasets/app-rankings?category=coding&subcategory=cli-agent&limit=50" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

```json
{
  "data": [
    {
      "app_id": 12345,
      "app_name": "Cline",
      "rank": 1,
      "total_requests": 4321,
      "total_tokens": "12345678"
    },
    {
      "app_id": 67890,
      "app_name": "Roo Code",
      "rank": 2,
      "total_requests": 2109,
      "total_tokens": "9876543"
    }
  ],
  "meta": {
    "as_of": "2026-05-12T02:00:00Z",
    "start_date": "2026-04-12",
    "end_date": "2026-05-11",
    "version": "v1"
  }
}
```

Query parameters:
- `category` — marketplace category group (e.g. `coding`)
- `subcategory` — marketplace subcategory (e.g. `cli-agent`) — takes precedence over `category`
- `sort` — `popular` (total token volume) or `trending` (recent growth)
- `start_date` / `end_date` — UTC date window (defaults to last 30 days)
- `limit` — 1-100, default 50
- `offset` — pagination

This is the dataset that powers `https://openrouter.ai/apps/category/coding/cli-agent`. AgentKthx's attribution headers land it on this ranking once traffic flows.

### 6. Session cost dataset — `GET /datasets/session-cost`

Returns weekly-refreshed, aggregated cost-per-session cells for published harnesses. This is the dataset OpenRouter uses to compare harness efficiency — useful for benchmarking AgentKthx against Cline, Roo Code, etc.

```bash
curl "https://openrouter.ai/api/v1/datasets/session-cost?app_slug=agentkthx" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

```json
{
  "data": [
    {
      "app_name": "AgentKthx",
      "app_slug": "agentkthx",
      "median_session_cost_usd": 0.027,
      "model_permaslug": "anthropic/claude-3.5-sonnet",
      "turn_range": "10-49-turns"
    }
  ],
  "meta": {
    "as_of": "2026-05-12T02:00:00.000Z",
    "version": "v1",
    "window_days": 30,
    "window_end_date": "2026-05-11"
  }
}
```

Sessions are never pooled across apps. Medians are of per-session USD spend. Privacy-preserving — never exposes `clerk_user_id` values or per-session rows.

Query parameters:
- `app_slug` — filter to one published harness slug
- `model` — exact model permaslug filter (works across all harness apps)
- `turn_range` — filter by inclusive number of turns in a session
- `limit` — 1-500, default 100
- `offset` — 0-5000

### 7. Custom analytics queries — `POST /analytics/query`

For arbitrary grouping/slicing of your own traffic. Discover available metrics and dimensions via `GET /analytics/meta`.

```bash
curl -X POST https://openrouter.ai/api/v1/analytics/query \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "dimensions": ["model"],
    "metrics": ["request_count"],
    "granularity": "day",
    "limit": 100,
    "time_range": {
      "start": "2025-01-01T00:00:00Z",
      "end": "2025-01-08T00:00:00Z"
    }
  }'
```

### What AgentKthx currently collects (vs. what's available)

| Metric | OpenRouter exposes | AgentKthx currently captures | Gap |
|--------|---------------------|------------------------------|-----|
| Per-response token count | `usage.prompt_tokens` / `completion_tokens` / `total_tokens` | Captured in `_parse_openai_response` → footer ⚡ TPS + token counters | ✅ Wired |
| Per-response cost | `usage.cost` | Parsed but not displayed | ⚠️ Could surface in footer |
| Upstream inference cost | `usage.cost_details.upstream_inference_cost` | Not parsed | ⚠️ Future transparency feature |
| Cached tokens | `usage.prompt_tokens_details.cached_tokens` | Not parsed | ⚠️ Could surface as `⚡ cached` |
| Reasoning tokens | `usage.completion_tokens_details.reasoning_tokens` | Not parsed | ⚠️ Could surface alongside `think` column |
| Per-key limit envelope | `GET /key` → `limit_remaining`, `free_model_daily_requests.remaining` | Not called | ⚠️ Future `agentkthx status` banner |
| Credit balance | `GET /credits` → `total_credits`, `total_usage` | Not called (requires management key) | ⚠️ Future `agentkthx status` banner |
| Per-generation audit | `GET /generation?id=<gen-id>` | Not called | ⚠️ Future `--audit-last` flag |
| App rankings | `GET /datasets/app-rankings` | Not called | ⚠️ Future `agentkthx openrouter rank` command |
| Session cost benchmark | `GET /datasets/session-cost` | Not called | ⚠️ Future benchmarking feature |
| Custom analytics | `POST /analytics/query` | Not called | ⚠️ Future report generator |

The footer currently shows: version, model, prompt-size, ctx + max-tokens, temp, 🔧 batch (when set), 🧊 quant (when detected), ⚡ per-response TPS, tok counters, ctx %, debug. Cost- and limit-based metrics are the most natural additions for a future release.

---

## Generation Inspection & Audit Trail

OpenRouter assigns every generation a unique `id` (e.g. `gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG`) returned in the response body. This ID is the key for the `/generation` lookup endpoint, which returns the full audit trail for that single request — provider, cost breakdown, token counts by category, latency, moderation latency, cache hits, and more.

The `id` is also visible in the SSE stream's first chunk (`data: {"id":"gen-...", ...}`). AgentKthx's `StreamAccumulator` already captures the generation ID for the OpenResponses event stream; surfacing it in the CLI footer or a `/last-gen` slash command would let users audit any individual response after the fact.

### Using the generation ID

```bash
# After any /chat/completions call, look up the full audit trail:
curl "https://openrouter.ai/api/v1/generation?id=gen-3bhGkxlo4XFrqiabUM7NDtwDzWwG" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

The response includes:
- `total_cost` — what you paid
- `upstream_inference_cost` — what OpenRouter paid the upstream (BYOK-only on this endpoint)
- `native_tokens_cached` — tokens read from prompt cache
- `native_tokens_reasoning` — tokens spent on chain-of-thought
- `provider_name` — which upstream provider served the request
- `app_id` — the attribution app this generation was attributed to
- `latency` / `moderation_latency` — timing breakdown
- `cancelled` — whether the request was cancelled mid-stream

### Attribution verification

The `app_id` and `http_referer` fields in the `/generation` response confirm that attribution worked. If you see `app_id: null` or `http_referer: null`, the request did not carry the attribution headers — verify the `_build_openrouter_attribution_headers()` helper is being called from every header-construction site (the regression tests in `tests/test_r07_21_openrouter_attribution.py` pin this).

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
| `__init__()` | Initializes with `OPENROUTER_API_KEY` env var, sets HTTP headers (via `_build_openrouter_attribution_headers()`), populates `_model_cache` via `list_models()` |
| `list_models()` | Fetches `/v1/models`, caches L1 (1 hour in-process) + L2 (30-min persistent JSON), filters `:free` models if `OPENROUTER_FREE_ONLY=1` |
| `is_running()` | Always returns `True` (cloud API, no local server) |
| `generate(model, messages, tools, **kwargs)` | Main entry point. Dispatches to `_make_api_request()`. Implements ReAct fallback on "tools not supported" errors. |
| `generate_stream(model, messages, **kwargs)` | SSE streaming variant. Yields `delta` chunks. |
| `_make_api_request(endpoint, data, stream)` | HTTP wrapper with 429/5xx retry, 401 detection, error normalization. Headers via `_build_openrouter_attribution_headers()` + Bearer token |
| `_stream_request(url, data, headers)` | Low-level SSE parser |
| `_build_openai_body(...)` | Centralizes request body construction (inherited from `OpenAICompatibleBackend` — shared by generate + generate_stream) |
| `_parse_openai_response(raw_response)` | Extracts `content`, `tool_calls`, `finish_reason`, `usage`, `reasoning_content`. Raises on top-level `error` field. (Inherited) |
| `_is_tools_not_supported_error(err_str)` | Detects "does not support tools" patterns for ReAct fallback |
| `test_tool_support(model, family, force_test)` | Returns `ToolSupportLevel.NATIVE` for every model without probing (ROB-39 OPEN — non-chat slugs overstate) |
| `_get_auth_headers()` | Returns Bearer + Content-Type + attribution headers (via `_build_openrouter_attribution_headers()`). Used by the streaming path. |
| `_build_openrouter_attribution_headers()` (module-level) | Returns the four attribution headers. Hardcoded constants — NOT env-overridable. |
| `_jev_call_completions(model, messages, ...)` | JEV api_mode hook — routes through `generate()` so auth + 429 retry are preserved |

### Configuration

```bash
# Required
export OPENROUTER_API_KEY="sk-or-v1-..."

# Optional
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"  # default
export OPENROUTER_DEFAULT_MODEL="openrouter/free"          # default
export OPENROUTER_FREE_ONLY=1                              # filter to :free models only
export OPENROUTER_MAX_429_RETRIES=6                        # 429 retry budget
```

### Configuration env vars

AgentKthx uses the `AGENTKTHX_*` env var prefix (renamed from `AGENTNOVA_*` in R06.41 — no aliases retained):
- `AGENTKTHX_BACKEND=openrouter` — set default backend
- `AGENTKTHX_API_MODE=openai` — OpenRouter only supports OpenAI mode (and JEV, which routes through OpenAI underneath)

> **Note**: The `X-OpenRouter-Categories` header value (`cli-agent`) is **NOT** configurable via env var. The harness category describes what AgentKthx *is* to OpenRouter's marketplace — it's a hardcoded constant. See [App Attribution — Marketplace Headers](#app-attribution--marketplace-headers) for the rationale.

### What AgentKthx does NOT yet implement (potential future work)

- **`reasoning` parameter object** — for thinking models (`{"effort": "high", "max_tokens": 1024, "exclude": false}`)
- **`provider` preferences** — would let users pin to specific providers
- **`transforms`** — would enable auto-truncation for long conversations
- **`plugins`** — would enable web search via OpenRouter's plugin system
- **`min_p`, `repetition_penalty`, `top_a`, `logit_bias`** — sampling parameters in `_build_openai_body()`
- **`/key` endpoint** — could show remaining credits/limits in CLI footer
- **`/credits` endpoint** — could show total balance in `agentkthx status`
- **`/generation` endpoint** — could power a `--audit-last` flag for per-response audit trail
- **`/datasets/app-rankings` + `/datasets/session-cost`** — could power a `agentkthx openrouter rank` command
- **`/analytics/query`** — could power a report generator

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401 Unauthorized` | Invalid API key | Check `OPENROUTER_API_KEY` env var; regenerate key at openrouter.ai/keys |
| `402 Payment Required` | No credits + non-free model | Switch to `:free` model OR fund account at openrouter.ai/credits |
| `403 Forbidden` on `/credits` | Provisioning key used instead of management key | Use a management key for `/credits` and `/keys` write operations |
| `429 Too Many Requests` | Hit rate limit | Wait `Retry-After` seconds (auto-retried up to 6x by AgentKthx) |
| `429 Provider X rate limited` | Upstream provider throttling | Wait longer (provider-level, not account-level) |
| Empty response (no content, no tool_calls) | Provider silently failed (filter, model issue) | Retry; check `--debug` for finish_reason |
| `does not support tools` | Free model lacks tool calling | AgentKthx auto-falls-back to ReAct; or use `--force-react` upfront |
| Slow startup (`agentkthx models --backend openrouter`) | `/models` endpoint slow, cache cold | Subsequent calls within 30 min use the persistent JSON cache |
| `model not found` | Model ID typo or removed from OpenRouter | Check `agentkthx models --backend openrouter` for current list |
| Streaming response missing usage | `:free` model doesn't send usage chunk | AgentKthx falls back to estimating tokens from content (`chars ÷ 4`). Footer still updates with approximate counts. |
| Reasoning not displayed with `--think` | Streaming path doesn't capture `reasoning_content` | R06.53: streaming now captures and displays reasoning_content inline. This row is retained for historical reference. |
| Token count way too high | Conversation history growing unbounded | Use `/clear` in chat mode; or `--session` to persist between runs |
| App not appearing in OpenRouter rankings | Missing `HTTP-Referer` header | Verify `_build_openrouter_attribution_headers()` is called from all header sites (R07.21 regression tests pin this) |
| App category missing from directory | Missing or unrecognized `X-OpenRouter-Categories` | Default is `cli-agent` (hardcoded). Verify via `/generation?id=<gen-id>` → `http_referer` field |
| App description is null | Dashboard field — not settable via headers | Visit `https://openrouter.ai/apps/url/<referer>` while logged in to set via the web UI |
| `OverflowError` on `--num-ctx infk` | `_parse_token_size` accepts `inf` (ROB-36 OPEN) | Use a finite value: `--num-ctx 128k` |

---

## References

- **OpenRouter Docs (root)**: https://openrouter.ai/docs
- **App Attribution**: https://openrouter.ai/docs/app-attribution
- **Usage Accounting**: https://openrouter.ai/docs/cookbook/administration/usage-accounting
- **API Reference (overview)**: https://openrouter.ai/docs/api-reference/overview
- **OpenAPI spec (JSON)**: https://openrouter.ai/openapi.json
- **OpenAPI spec (YAML)**: https://openrouter.ai/openapi.yaml
- **Models List**: https://openrouter.ai/models
- **Pricing**: https://openrouter.ai/pricing
- **Free Models**: https://openrouter.ai/openrouter/free
- **Provider Routing**: https://openrouter.ai/docs/features/provider-routing
- **Rate Limits**: https://openrouter.ai/docs/api-reference/limits
- **Error Codes**: https://openrouter.ai/docs/api-reference/errors
- **Apps Marketplace**: https://openrouter.ai/apps
- **Coding Category**: https://openrouter.ai/apps/category/coding
- **CLI Agent Subcategory**: https://openrouter.ai/apps/category/coding/cli-agent
- **AgentKthx App Entry**: https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FVTSTech%2FAgentKthx
- **Public Rankings**: https://openrouter.ai/rankings

---

Written by VTSTech — https://www.vts-tech.org