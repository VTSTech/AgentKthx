# OpenAI API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://platform.openai.com/docs/api-reference
> **Primary focus**: Chat Completions API (`/v1/chat/completions`) and Responses API (`/v1/responses`)
> **Last Updated**: 2026-09-26
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Sampling Parameters](#sampling-parameters)
4. [Service Tiers & Pricing](#service-tiers--pricing)
5. [Model Family Specifications](#model-family-specifications)
6. [Function Calling Implementation](#function-calling-implementation)
7. [Streaming & Real-time Features](#streaming--real-time-features)
8. [Reasoning Configuration](#reasoning-configuration)
9. [Error Codes & Recovery](#error-codes--recovery)
10. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
11. [Free Tier & Trial Credits](#free-tier--trial-credits)
12. [Multimodal Content Handling](#multimodal-content-handling)
13. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
14. [Proposed plugin.json](#proposed-pluginjson)
15. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — primary API surface
BASE_URL = "https://api.openai.com/v1"

# Production — Azure OpenAI (separate surface, different auth)
BASE_URL_AZURE = "https://{resource}.openai.azure.com/openai/deployments/{deployment}"

# Production — Bedrock (separate surface, AWS SigV4 auth)
BASE_URL_BEDROCK = "https://bedrock-runtime.{region}.amazonaws.com"
```

AgentKthx v0.1 should target the OpenAI-direct surface (`api.openai.com/v1`). Azure and Bedrock support is a future enhancement — both require different auth schemes (Azure Active Directory / API key, AWS SigV4) and use different URL shapes per deployment.

### API Endpoints

```python
# === Chat Completions API (OpenAI-compat) ===
CHAT_COMPLETIONS_CREATE  = "/chat/completions"                      # POST
CHAT_COMPLETIONS_LIST    = "/chat/completions"                     # GET
CHAT_COMPLETIONS_GET     = "/chat/completions/{completion_id}"     # GET
CHAT_COMPLETIONS_DELETE  = "/chat/completions/{completion_id}"     # DELETE

# === Responses API (newer, stateful) ===
RESPONSES_CREATE         = "/responses"                             # POST
RESPONSES_RETRIEVE      = "/responses/{response_id}"               # GET
RESPONSES_DELETE         = "/responses/{response_id}"               # DELETE
RESPONSES_LIST_INPUTS   = "/responses/{response_id}/input_items"  # GET
RESPONSES_COUNT_TOKENS  = "/responses/count_tokens"                # POST
RESPONSES_CANCEL        = "/responses/{response_id}/cancel"        # POST
RESPONSES_COMPACT       = "/responses/{response_id}/compact"       # POST

# === Conversations (stateful, layered on Responses) ===
CONVERSATIONS_CREATE     = "/conversations"                         # POST
CONVERSATIONS_RETRIEVE   = "/conversations/{conversation_id}"      # GET
CONVERSATIONS_UPDATE     = "/conversations/{conversation_id}"      # POST
CONVERSATIONS_DELETE     = "/conversations/{conversation_id}"      # DELETE

# === Auxiliary — model discovery and misc ===
MODELS_LIST              = "/models"                                # GET
MODELS_RETRIEVE          = "/models/{model}"                        # GET
EMBEDDINGS_CREATE        = "/embeddings"                             # POST
IMAGES_GENERATE          = "/images/generations"                     # POST
IMAGES_EDIT              = "/images/edits"                          # POST
AUDIO_SPEECH             = "/audio/speech"                           # POST
AUDIO_TRANSCRIPTION      = "/audio/transcriptions"                   # POST
AUDIO_TRANSLATION        = "/audio/translations"                    # POST
MODERATIONS_CREATE       = "/moderations"                           # POST
BATCHES_CREATE           = "/batches"                                # POST
FILES_LIST               = "/files"                                 # GET
FILES_CREATE             = "/files"                                 # POST

# === Realtime API (WebSocket, NOT chat backend — listed for parity) ===
REALTIME_WS              = "wss://api.openai.com/v1/realtime"        # WebSocket

# === Assistants API (legacy, being deprecated) ===
ASSISTANTS_LIST          = "/assistants"                            # GET
THREADS_CREATE           = "/threads"                              # POST
RUNS_CREATE              = "/threads/{thread_id}/runs"              # POST
```

### Authentication Headers

```python
headers = {
    "Content-Type":  "application/json",
    "Authorization": "Bearer sk-...YOUR_API_KEY",
    # Optional — when account has multiple orgs or projects:
    "OpenAI-Organization": "org-xxx",
    "OpenAI-Project":     "proj_xxx",
    # Optional — request ID for support troubleshooting:
    "X-Client-Request-Id": str(uuid.uuid4()),
}
```

### API key types

| Key type | Format | Use case | Status (Sept 2026) |
|----------|--------|----------|---------------------|
| User API key | `sk-...` (legacy) | Single-user scripts, personal projects | Discouraged for production; can't scope to projects |
| Project API key | `sk-proj-...` | Production apps, org/project-scoped | **Recommended for AgentKthx backends** |
| Admin API key | `sk-admin-...` | Administration endpoints only (users, audit logs) | Never use for inference |
| Service account key | `sk-sa-...` | Long-running service workloads, no human | For org-level automation only |
| Workload identity token | short-lived JWT | Ephemeral access via OIDC federation | Best for Kubernetes / cloud-native deployments |

**AgentKthx implementation guidance:**
- Document `OPENAI_API_KEY` as the primary env var.
- Detect key type on first use: if prefix is `sk-proj-`, also look for `OPENAI_ORGANIZATION_ID` and `OPENAI_PROJECT_ID` to send the corresponding headers.
- Surface a clear warning if a legacy `sk-` user key is used in production: "Project API keys (`sk-proj-`) are recommended for production. User keys lack project-scoping and are discouraged for service deployments."

### Request Format Requirements

- **Content-Type**: `application/json` only (binary endpoints like `/audio/transcriptions` use `multipart/form-data`)
- **Character Encoding**: UTF-8
- **Max Request Size**: 64 KiB for ALL headers combined (including Authorization); individual header ≤ 60 KiB. JSON body typically caps at ~8 MB; partner providers (Azure, Bedrock) may differ.
- **Timeout**: 120 seconds recommended for chat completions (AgentKthx uses `BackendConfig.timeout = 120`); 600 seconds for long-running Responses API with reasoning models
- **HTTPS only** — plaintext HTTP is rejected at the edge
- **`User-Agent`**: not required but OpenAI uses it for analytics

### Request ID tracking

Every API response includes an `x-request-id` header. OpenAI strongly recommends logging this in production deployments for support troubleshooting. AgentKthx should log it via the existing `--debug` flag.

For client-side correlation, the user may supply `X-Client-Request-Id` (UUID or trace ID, ASCII-only, ≤512 chars). OpenAI logs this internally for chat/completions, embeddings, responses endpoints.

---

## Request/Response Structure

### Chat Completions — Complete Request Schema

```json
{
    "model": "gpt-6-sol",
    "messages": [
        {
            "role": "system|developer|user|assistant|tool|function",
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
    "stream_options": {
        "include_usage": true,
        "include_obfuscation": false
    },
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
    "reasoning_effort": "minimal|low|medium|high",
    "service_tier": "auto|default|flex|scale|priority|fast",
    "user": "user-identifier",
    "metadata": {},
    "modalities": ["text"],
    "audio": {
        "voice": "alloy|echo|fable|onyx|nova|shimmer|cor|sage",
        "format": "wav|mp3|flac|opus|pcm16"
    },
    "web_search_options": {"search_context_size": "low|medium|high"},
    "store": true,
    "prompt_cache_options": {"ttl": "default|extended"},
    "prediction": {"type": "content", "content": "..."},
    "function_call": "deprecated",
    "functions": "deprecated"
}
```

### Chat Completions — Response Schema

```json
{
    "id": "chatcmpl-xxxxxxxxxxxx",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-6-sol",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "refusal": null,
                "annotations": [
                    {
                        "type": "url_citation",
                        "url_citation": {
                            "end_index": 42,
                            "start_index": 30,
                            "title": "Example Source",
                            "url": "https://example.com"
                        }
                    }
                ],
                "audio": null,
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
            "finish_reason": "stop|length|tool_calls|content_filter|function_call",
            "logprobs": null
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "prompt_tokens_details": {
            "cached_tokens": 0,
            "image_tokens": 0,
            "text_tokens": 0
        },
        "completion_tokens_details": {
            "reasoning_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
            "audio_tokens": 0,
            "text_tokens": 0
        }
    },
    "service_tier": "default",
    "system_fingerprint": "fp_xxx",
    "metadata": null,
    "moderation": null
}
```

Key OpenAI-specific fields:
- **`refusal`** — populated when the model refuses the request (content policy, safety). AgentKthx should surface this distinctly from `content` to show users why generation halted.
- **`annotations`** — when using the `web_search` tool, citations appear here as `url_citation` entries with `start_index` / `end_index` into `content`.
- **`audio`** — when `modalities: ["audio"]` is requested, contains `id`, `data` (base64), `expires_at`, `transcript`.
- **`service_tier`** — always echoes the actual processing mode used (may differ from request `service_tier` if `auto` was set).
- **`system_fingerprint`** — backend config fingerprint; useful with `seed` to detect determinism-breaking changes.
- **`completion_tokens_details.reasoning_tokens`** — counts internal reasoning tokens separately from visible output. Billed at output-token rate but NOT visible unless `reasoning.include_thoughts` is true (Responses API only).

### `finish_reason` Semantics

| `finish_reason` | OpenAI internal cause | AgentKthx handling |
|-----------------|------------------------|--------------------|
| `stop` | Natural end of generation or stop sequence match | Finalize assistant turn |
| `length` | `max_tokens` / `max_completion_tokens` reached | Surface truncation warning to user, do not retry |
| `tool_calls` | Model emitted one or more `tool_calls` | Invoke tools, append tool results, loop |
| `content_filter` | Safety filter blocked output | Log the moderation result; do not retry with same prompt |
| `function_call` (deprecated) | Model called a legacy `function` (not `tools`) | Migrate to `tools` API; surface deprecation warning |

### Responses API — Complete Request Schema (different shape)

The Responses API is **stateful** and uses an `input` field instead of `messages`:

```json
{
    "model": "gpt-6-astra",
    "input": [
        {
            "type": "message",
            "role": "developer|user|assistant|system",
            "content": "string|array"
        },
        {
            "type": "function_call",
            "name": "function_name",
            "arguments": "...",
            "call_id": "call_xxx",
            "id": "fc_xxx"
        },
        {
            "type": "function_call_output",
            "call_id": "call_xxx",
            "output": "..."
        }
    ],
    "instructions": "string (developer system prompt)",
    "reasoning": {
        "effort": "none|minimal|low|medium|high|xhigh|max",
        "mode": "standard|pro",
        "summary": "auto|concise|detailed",
        "context": "auto|include",
        "include_thoughts": true
    },
    "tools": [
        {"type": "function", "function": {...}},
        {"type": "web_search", "search_context_size": "low|medium|high"},
        {"type": "file_search", "vector_store_ids": ["vs_xxx"]},
        {"type": "code_interpreter", "container_id": "ctr_xxx"},
        {"type": "computer_use", "display_width": 1024, "environment": "mac"}
    ],
    "tool_choice": "auto|none|required|{...}",
    "parallel_tool_calls": true,
    "max_output_tokens": 65536,
    "previous_response_id": "resp_xxx",
    "store": true,
    "stream": false,
    "service_tier": "auto|default|flex|scale|priority|fast",
    "prompt_cache_options": {"ttl": "default|extended"},
    "user": "user-identifier",
    "metadata": {}
}
```

### Responses API — Response Schema (event-driven)

The Responses API response is a `Response` object containing `output` (an array of items):

```json
{
    "id": "resp_xxx",
    "object": "response",
    "created_at": 1700000000,
    "model": "gpt-6-astra",
    "status": "completed|in_progress|failed|cancelled",
    "output": [
        {
            "type": "reasoning",
            "id": "rs_xxx",
            "summary": [{"type": "summary_text", "text": "..."}],
            "content": []
        },
        {
            "type": "message",
            "id": "msg_xxx",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "Generated response", "annotations": []}
            ]
        },
        {
            "type": "function_call",
            "id": "fc_xxx",
            "call_id": "call_xxx",
            "name": "function_name",
            "arguments": "..."
        }
    ],
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 1024}
    },
    "service_tier": "default",
    "reasoning": {"effort": "medium", "mode": "standard"},
    "previous_response_id": null
}
```

Key differences from Chat Completions:
- **`input` instead of `messages`** — supports richer item types (function calls, function outputs, reasoning, file inputs)
- **`instructions` field** — replaces the system message for o-series / gpt-5.x+ models
- **`previous_response_id`** — links to a prior response for multi-turn stateful conversations (server stores conversation state)
- **`store: true` (default)** — OpenAI stores the response on their servers, addressable by `id` for retrieval/cancellation
- **`output` array** — can contain reasoning items, messages, function calls in order
- **Built-in tools** — `web_search`, `file_search`, `code_interpreter`, `computer_use` are first-class tool types (not user-defined functions)
- **`reasoning` object** — supersedes `reasoning_effort` with structured effort/mode/summary control

---

## Sampling Parameters

### Temperature-family parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `temperature` | float | 0.0–2.0 | 1.0 | Controls randomness. Lower = focused/deterministic, higher = creative/diverse |
| `top_p` | float | 0.0–1.0 | 1.0 | Nucleus sampling: probability mass of tokens to consider |
| `top_k` | int | 0-N | -1 (disabled) | Top-K sampling (not all models honor) |
| `seed` | int | any | None | Reproducibility seed. Best-effort; use `system_fingerprint` to detect backend changes |

**Note**: OpenAI recommends using `temperature` OR `top_p`, not both. Setting both may produce unexpected behavior.

### Penalty parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `presence_penalty` | float | -2.0 to 2.0 | 0.0 | Positive: penalize tokens already present (encourages new topics) |
| `frequency_penalty` | float | -2.0 to 2.0 | 0.0 | Positive: penalize tokens proportional to frequency (discourages repetition) |
| `logit_bias` | dict | any | {} | Map token_id → bias (-100 to +100). Negative = avoid, positive = prefer. Works only on chat models; ignored for reasoning models |

### Generation control

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `max_tokens` | int | model default | Legacy field for max output tokens. **Use `max_completion_tokens` for reasoning models.** Some reasoning models (gpt-5.x+) reject `max_tokens` |
| `max_completion_tokens` | int | model default | OpenAI's preferred field. Total token budget INCLUDING reasoning tokens |
| `max_output_tokens` | int | model default | Responses API field. Same semantics as `max_completion_tokens` |
| `stop` | list[str] | None | Stop sequences (up to 4 strings). Generation halts on match. Not supported on reasoning models (o-series, gpt-5.x+) |
| `n` | int | 1 | Number of completions to generate. Returns `choices[]` array. **Ignored for reasoning models** — they only support `n=1`. |
| `logprobs` | bool | false | Whether to return logprobs of output tokens. Not supported on reasoning models |
| `top_logprobs` | int (0-20) | None | Number of top logprobs per token (requires `logprobs: true`) |

### Structured output

| Parameter | Type | Description |
|-----------|------|-------------|
| `response_format` | object | `{type: "text"|"json_object"|"json_schema"}` — supported on most modern chat models. `json_schema` requires `strict: true` schema |
| `response_format.json_schema` | object | `{name: "...", schema: <json_schema>, strict: true}` — strict structured output. Supported on gpt-4o, gpt-5.x, gpt-6.x. Recommended over prompt-engineered JSON |

### Audio output

| Parameter | Type | Description |
|-----------|------|-------------|
| `modalities` | list[str] | `["text"]` (default), `["text", "audio"]`, `["audio"]`. Adding `"audio"` enables spoken output |
| `audio.voice` | string | `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`, `cor`, `sage` — pick voice for audio output |
| `audio.format` | string | `wav`, `mp3`, `flac`, `opus`, `pcm16` — audio encoding format |

### Web search

| Parameter | Type | Description |
|-----------|------|-------------|
| `web_search_options` | object | `{search_context_size: "low"|"medium"|"high"}` — enables built-in web search tool on supported models. Billed at $10/1k calls + search content tokens at model rates |
| `tools: [{type: "web_search"}]` | object | Responses API equivalent — adds web search as a built-in tool |

### AgentKthx implementation status

The AgentKthx `OpenAIBackend._build_openai_body()` method should mirror the OpenRouter pattern and forward these parameters from `**kwargs`:

```python
optional_int_fields   = ("top_p", "seed", "n", "top_logprobs", "max_completion_tokens")
optional_float_fields = ("presence_penalty", "frequency_penalty", "temperature")
optional_bool_fields  = ("stream", "logprobs", "parallel_tool_calls", "store")
optional_dict_fields  = ("response_format", "stream_options", "tool_choice",
                          "reasoning", "prompt_cache_options", "prediction",
                          "audio", "web_search_options", "metadata")
optional_list_fields  = ("tools", "stop", "modalities")
optional_str_fields   = ("service_tier", "user", "reasoning_effort")
```

---

## Service Tiers & Pricing

OpenAI's pricing varies by **service tier** — selecting a tier affects both cost and latency. All tiers share the same model and output quality; only the processing queue and discount differ.

### Service tier values

| Service tier | Pricing | Latency | Use case |
|--------------|---------|---------|----------|
| `auto` (default) | Project default | Standard | Default — uses Project tier settings (defaults to `default`) |
| `default` | Standard (1x) | Standard | Standard pricing and performance |
| `flex` | 0.5x (50% off) | Best-effort, async | Background workloads where latency is not critical |
| `scale` | Custom | Reserved capacity | Pre-provisioned capacity for predictable workloads |
| `priority` | 2x (premium) | Lowest | Renamed from `fast` on July 30, 2026 — fastest latency |
| `fast` | 2x (premium) | Lowest | Alias for `priority` |

When `service_tier` is set, the response body's `service_tier` field reflects the **actual** mode used (may differ from request value when `auto` resolves).

### Pricing snapshot (Sept 2026)

All prices USD per 1M tokens, **Standard** tier (long-context pricing differs; see OpenAI pricing page).

#### Flagship models (GPT-6 family)

| Model | Input | Cached input | Output |
|-------|-------|--------------|--------|
| gpt-6-astra | $10.00 | $1.00 | $50.00 |
| gpt-6-sol | $2.00 | $0.20 | $10.00 |
| gpt-6-luna | $0.10 | $0.01 | $0.50 |

#### Cyber models (Daybreak program — GPT-5.6 family)

| Model | Input | Cached input | Output |
|-------|-------|--------------|--------|
| gpt-5.6-sol | $4.00 | $0.40 | $20.00 |
| gpt-5.6-cyber | $12.50 | $1.25 | $75.00 |

#### Realtime / audio

| Model | Modality | Input | Cached | Output |
|-------|----------|-------|--------|--------|
| gpt-realtime-2.1 | Audio | $32.00 | $0.40 | $64.00 |
| gpt-realtime-2.1 | Text | $4.00 | $0.40 | $24.00 |
| gpt-realtime-2.1 | Image | $5.00 | $0.50 | — |
| gpt-realtime-2.1-mini | Audio | $10.00 | $0.30 | $20.00 |
| gpt-realtime-2.1-mini | Text | $0.60 | $0.06 | $2.40 |
| gpt-realtime-2.1-mini | Image | $0.80 | $0.08 | — |

#### Live voice sessions

| Model | Price |
|-------|-------|
| gpt-live-1 | $0.05 / minute (billed per second, no rounding) |

#### Image generation

| Model | Modality | Input | Cached | Output |
|-------|----------|-------|--------|--------|
| gpt-image-2.5-sunburst | Image | $8.00 | $2.00 | $30.00 |
| gpt-image-2.5-sunburst | Text | $5.00 | $1.25 | — |
| gpt-image-2.5-flare | Image | $8.00 | $2.00 | $30.00 |
| gpt-image-2 | Image | $4.00 | $1.00 | $15.00 |
| gpt-image-2 | Text | $2.50 | $0.625 | — |

#### Transcription

| Model | Use case | Input | Output | Estimated cost |
|-------|----------|------|--------|----------------|
| gpt-realtime-translate | Live translation | — | — | $0.034 / minute |
| gpt-live-transcribe | Live transcription | — | — | $0.017 / minute |
| gpt-realtime-whisper | Live transcription | — | — | $0.017 / minute |
| gpt-transcribe | Transcription | — | — | $0.0045 / minute |
| gpt-4o-transcribe | Transcription | $2.50 | $10.00 | $0.006 / minute |
| gpt-4o-mini-transcribe | Transcription | $1.25 | $5.00 | $0.003 / minute |

#### Specialized models

| Model | Category | Input | Cached | Output |
|-------|----------|-------|--------|--------|
| chat-latest | ChatGPT | $5.00 | $0.50 | $30.00 |
| gpt-5.3-codex | Codex | $1.75 | $0.175 | $14.00 |
| gpt-rosalind-research | Life Sciences | $5.00 | $0.50 | $25.00 |

#### Tools (additional billing)

| Tool | Pricing |
|------|---------|
| Web search (all models) | $10.00 / 1k calls + search content tokens at model rates |
| Web search preview (reasoning models, including gpt-5, o-series) | $10.00 / 1k calls + content tokens at model rates |
| Web search preview (non-reasoning models) | $25.00 / 1k calls + content tokens are free |
| File search tool call | $2.50 / 1k calls |
| File search storage | $0.10 / GB per day (1 GB free per account) |
| Containers (Hosted Shell + Code Interpreter) | 1 GB $0.03, 4 GB $0.12, 16 GB $0.48, 64 GB $1.92 per 20-min session |
| Agent Kit / ChatKit file and image storage | $0.10 / GB-day after 1 GB free per account per month |

### Batch API discount

The `/v1/batches` endpoint gives 50% off all token rates for async (≤24h SLA) processing. Useful for offline eval runs:

```bash
# Submit a batch of chat completions (50% discount)
curl -X POST https://api.openai.com/v1/batches \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -d '{"input_file_id": "file-abc", "endpoint": "/v1/chat/completions", "completion_window": "24h"}'
```

### AgentKthx implementation

AgentKthx should expose `service_tier` as an env var and CLI flag:

```bash
# Default: standard
export OPENAI_SERVICE_TIER=default

# For batch eval runs (cheaper, async)
export OPENAI_SERVICE_TIER=flex

# For latency-sensitive agentic workflows (premium, 2x cost)
export OPENAI_SERVICE_TIER=priority
```

For `OPENAI_FREE_ONLY=true` mode, the backend should:
1. Restrict `service_tier` to `default` or `flex` (never `priority`/`fast`/`scale`)
2. Restrict models to `OPENAI_FREE_MODEL_WHITELIST` (below)
3. Surface clear errors if the user attempts to use a paid-only feature

---

## Model Family Specifications

> **R07.03 update**: The live `/v1/models` API (queried with a real
> `OPENAI_API_KEY`) returns 128 models — much more than the 15-model
> catalog documented below. The static `OPENAI_MODELS` catalog in
> `agentkthx/plugins/openai/openai.py` has been expanded from 15 → 38
> models to include the o-series (o1/o3/o3-mini/o4-mini), the full
> GPT-5.0 family (gpt-5/mini/nano/pro/codex), GPT-5.1/5.2 variants,
> GPT-5.4 mini/nano/pro, GPT-5.5-pro, GPT-5.6-luna/terra (the missing
> Daybreak variants), GPT-4.1/nano, and legacy GPT-3.5-turbo. A
> `_NON_CHAT_PATTERNS` filter excludes ~34 non-chat models (embeddings,
> TTS, transcribe, whisper, image gen, sora, moderation, babbage,
> davinci) from the listing. The catalog below documents the
> originally-identified models with full pricing metadata — the expanded
> entries use the same schema but omit pricing (not yet documented).

### Current text/multimodal models (Sept 2026)

```python
MODEL_CONFIGS = {
    # === GPT-6 family — current flagship generation ===
    "gpt-6-astra": {
        "context_length": 400_000,             # 400K tokens
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,        # standard + pro
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,      # text, image, audio, file
        "supports_multimodal_output": True,     # text, audio
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": True,
        "temperature_default": 1.0,
        "top_p_default": 0.95,
        "family": "gpt-6",
        "tier": "flagship",
        "standard_input_per_1m": 10.00,
        "standard_output_per_1m": 50.00,
        "cached_input_per_1m": 1.00,
    },
    "gpt-6-sol": {
        "context_length": 400_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": True,
        "family": "gpt-6",
        "tier": "standard",
        "standard_input_per_1m": 2.00,
        "standard_output_per_1m": 10.00,
    },
    "gpt-6-luna": {
        "context_length": 400_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "supports_web_search_tool": True,
        "supports_file_search_tool": True,
        "supports_code_interpreter_tool": True,
        "supports_computer_use_tool": False,    # not supported on Luna
        "family": "gpt-6",
        "tier": "lite",
        "standard_input_per_1m": 0.10,
        "standard_output_per_1m": 0.50,
        "free_tier_eligible": True,             # covered by monthly credit
    },

    # === GPT-5.6 family — Daybreak program ===
    "gpt-5.6-sol": {  # alias: gpt-daybreak-blue-latest
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-5.6",
        "tier": "daybreak",
        "standard_input_per_1m": 4.00,
        "standard_output_per_1m": 20.00,
    },
    "gpt-5.6-cyber": {  # alias: gpt-daybreak-red-latest
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-5.6",
        "tier": "daybreak",
        "standard_input_per_1m": 12.50,
        "standard_output_per_1m": 75.00,
    },
    "gpt-5.6": {  # base alias
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_reasoning_mode": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "family": "gpt-5.6",
        "tier": "standard",
    },

    # === GPT-5.x family — legacy, still served ===
    "gpt-5.5": {
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,        # defaults to medium
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.4": {
        "context_length": 128_000,
        "max_output_tokens": 16_384,
        "supports_thinking": True,
        "supports_interleaved_thinking": True,    # thinks between tool calls
        "supports_streaming": True,
        "supports_function_calling": True,
        "family": "gpt-5",
        "tier": "standard",
    },
    "gpt-5.3-codex": {
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_computer_use_tool": False,
        "supports_code_interpreter_tool": True,
        "family": "gpt-5",
        "tier": "codex",
        "standard_input_per_1m": 1.75,
        "standard_output_per_1m": 14.00,
    },

    # === GPT-4o family — legacy chat models (no reasoning) ===
    "gpt-4o": {
        "context_length": 128_000,
        "max_output_tokens": 16_384,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,        # text, image, audio
        "supports_multimodal_output": True,       # text, audio
        "supports_web_search_tool": True,
        "family": "gpt-4o",
        "tier": "standard",
    },
    "gpt-4o-mini": {
        "context_length": 128_000,
        "max_output_tokens": 16_384,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_web_search_tool": True,
        "family": "gpt-4o",
        "tier": "mini",
        "free_tier_eligible": True,               # very low cost, covered by trial credit
    },
    "gpt-4.1-mini": {
        "context_length": 1_000_000,             # 1M tokens
        "max_output_tokens": 32_768,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_multimodal_input": True,
        "supports_web_search_tool": True,
        "family": "gpt-4.1",
        "tier": "mini",
        "free_tier_eligible": True,
    },

    # === Specialized models ===
    "chat-latest": {  # ChatGPT backend model
        "context_length": 128_000,
        "max_output_tokens": 16_384,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": True,
        "family": "chatgpt",
        "tier": "chat-latest",
        "standard_input_per_1m": 5.00,
        "standard_output_per_1m": 30.00,
    },
    "gpt-rosalind-research": {  # Life sciences (restricted access)
        "context_length": 200_000,
        "max_output_tokens": 65_536,
        "supports_thinking": True,
        "supports_function_calling": True,
        "family": "rosalind",
        "tier": "specialized",
        "standard_input_per_1m": 5.00,
        "standard_output_per_1m": 25.00,
        "access_restricted": True,                # trusted-access program only
    },

    # === Realtime / audio / image models (not chat backends — listed for completeness) ===
    "gpt-realtime-2.1": {
        "context_length": 128_000,
        "family": "realtime",
        "supports_streaming": True,
        "supports_multimodal_input": True,        # text, audio, image
        "supports_multimodal_output": True,       # text, audio
        "tier": "standard",
    },
    "gpt-realtime-2.1-mini": {
        "context_length": 128_000,
        "family": "realtime",
        "supports_streaming": True,
        "supports_multimodal_input": True,
        "supports_multimodal_output": True,
        "tier": "mini",
        "free_tier_eligible": True,
    },
    "gpt-live-1": {  # Voice sessions, billed per minute
        "family": "live",
        "supports_streaming": True,
        "billing_model": "per_minute",
        "price_per_minute": 0.05,
    },
    "gpt-image-2": {
        "family": "image",
        "supports_multimodal_output": True,       # image output
        "tier": "standard",
    },
    "gpt-image-2.5-sunburst": {
        "family": "image",
        "supports_multimodal_output": True,
        "tier": "flagship",
    },
    "gpt-image-2.5-flare": {
        "family": "image",
        "supports_multimodal_output": True,
        "tier": "flagship",
    },

    # === Transcription models ===
    "gpt-transcribe": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.0045,
    },
    "gpt-4o-transcribe": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.006,
    },
    "gpt-4o-mini-transcribe": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.003,
        "free_tier_eligible": True,
    },
    "gpt-realtime-whisper": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.017,
    },
    "gpt-realtime-translate": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.034,
    },
    "gpt-live-transcribe": {
        "family": "asr",
        "supports_streaming": True,
        "price_per_minute": 0.017,
    },
}
```

### Model detection & auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect OpenAI model capabilities from name.

    The OpenAI naming convention is:
        gpt-<MAJOR>.<MINOR>[-<tier>][-<date>]
        e.g. gpt-6-astra, gpt-5.6-sol, gpt-4o-mini, gpt-5.3-codex
    """
    m = model_name.lower()
    if m.startswith("gpt-6"):
        return {
            "family": "gpt-6",
            "supports_native_tools": True,
            "supports_thinking": True,
            "supports_reasoning_effort": True,
            "supports_reasoning_mode": True,        # standard + pro
            "supports_developer_role": True,        # use developer messages instead of system
            "default_reasoning_effort": "medium",
        }
    elif m.startswith("gpt-5.6"):
        return {
            "family": "gpt-5.6",
            "supports_native_tools": True,
            "supports_thinking": True,
            "supports_reasoning_effort": True,
            "supports_reasoning_mode": True,
            "supports_developer_role": True,
            "default_reasoning_effort": "medium",
        }
    elif m.startswith("gpt-5."):
        return {
            "family": "gpt-5",
            "supports_native_tools": True,
            "supports_thinking": True,
            "supports_reasoning_effort": True,
            "supports_developer_role": True,
            "default_reasoning_effort": "medium",   # GPT-5.5 default
        }
    elif m.startswith("gpt-4o") or m.startswith("gpt-4.1"):
        return {
            "family": "gpt-4o",
            "supports_native_tools": True,
            "supports_thinking": False,
            "supports_reasoning_effort": False,
            "supports_developer_role": False,      # use system messages
        }
    elif m.startswith("chat-"):
        return {
            "family": "chatgpt",
            "supports_native_tools": True,
            "supports_thinking": False,
            "supports_developer_role": False,
        }
    elif m.startswith("gpt-realtime"):
        return {
            "family": "realtime",
            "supports_native_tools": True,
            "supports_thinking": False,
            "supports_audio_modalities": True,
        }
    elif m.startswith("gpt-image"):
        return {
            "family": "image",
            "supports_native_tools": False,
            "supports_image_output": True,
        }
    elif m.startswith("gpt-") and "transcribe" in m:
        return {
            "family": "asr",
            "supports_native_tools": False,
            "supports_audio_input": True,
        }
    else:
        return {
            "family": "unknown",
            "supports_native_tools": False,
            "supports_thinking": False,
        }
```

### `OPENAI_FREE_MODEL_WHITELIST` (AgentKthx-curated)

Models that are eligible for `OPENAI_FREE_ONLY=true` mode — either very low token cost (covered by monthly credit) or explicitly `free_tier_eligible`:

```python
OPENAI_FREE_MODEL_WHITELIST = {
    # GPT-6 Luna — cheapest flagship ($0.10/$0.50 per 1M tokens)
    "gpt-6-luna",

    # GPT-4o family — legacy but very cheap
    "gpt-4o-mini",
    "gpt-4.1-mini",

    # Realtime mini
    "gpt-realtime-2.1-mini",

    # Transcription
    "gpt-4o-mini-transcribe",
    "gpt-transcribe",
}
```

When `OPENAI_FREE_ONLY=true`:
1. Any model not in this list is rejected with an actionable error pointing the user to whitelist the model with a paid API key
2. `service_tier` is forced to `default` (never `priority`/`fast`/`scale`)
3. The user's trial credit balance is monitored; HTTP 429 with `insufficient_quota` triggers a clear "trial credit exhausted" message

---

## Function Calling Implementation

### Tool schema (Chat Completions API)

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
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["city"],
            "additionalProperties": false
        },
        "strict": false
    }
}
```

Setting `strict: true` enables OpenAI's **structured outputs** guarantee — the model will only emit valid JSON conforming to the schema. Requires:
- All fields in `properties` must be in `required`
- `additionalProperties: false` at every level
- No `default` values (use union types instead)

### Tool choice

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Model decides whether to call a tool or respond with text |
| `"none"` | Model MUST NOT call tools. Forces text response |
| `"required"` | Model MUST call at least one tool |
| `{"type": "function", "function": {"name": "X"}}` | Forces calling the specific function `X` |
| `{"type": "allowed_tools", "allowed_tools": {"mode": "auto\|required", "tools": [...]}}` | Constrain the model to a pre-defined subset of tools |

### Parallel tool calls

Set `parallel_tool_calls: true` (default for GPT-4o+) to allow the model to emit multiple `tool_calls` in a single response. AgentKthx should:
1. Execute all tool calls in parallel (the framework already does this)
2. Append all results to the next request as `tool` messages with matching `tool_call_id`
3. Order matters — `tool` messages must appear in the same order as the `tool_calls` they respond to

For reasoning models (gpt-5.x+), parallel tool calls are interleaved with reasoning tokens — the model thinks between calls, then emits the next call.

### Responses API — built-in tools

The Responses API supports built-in tools as first-class tool types:

```json
{
    "tools": [
        {"type": "function", "function": {...}},
        {"type": "web_search", "search_context_size": "medium"},
        {"type": "file_search", "vector_store_ids": ["vs_xxx"], "max_num_results": 10},
        {"type": "code_interpreter", "container_id": "ctr_xxx"},
        {"type": "computer_use", "display_width": 1024, "environment": "mac"},
        {"type": "image_generation", "model": "gpt-image-2.5-sunburst", "size": "1024x1024"},
        {"type": "mcp", "server_label": "github", "server_url": "https://...", "headers": {}}
    ]
}
```

Built-in tools emit tool-call items in the response `output` array. AgentKthx v0.1 should support only the `function` type — built-in tools (`web_search`, `file_search`, etc.) require additional integrations and are out of scope for the initial OpenAI plugin.

### AgentKthx implementation

AgentKthx converts its internal `Tool` objects to OpenAI schema via `Tool.to_openai_schema()` and forwards them as the `tools` field in the request body. Tool results from previous turns are encoded as messages with `role: "tool"` and a `tool_call_id` field.

---

## Streaming & Real-time Features

### SSE streaming (Chat Completions API)

Set `"stream": true` in the request body. OpenAI returns Server-Sent Events:

```
data: {"id":"chatcmpl-xxx","choices":[{"delta":{"role":"assistant","content":"Hello"}}]}

data: {"id":"chatcmpl-xxx","choices":[{"delta":{"content":" world"}}]}

data: {"id":"chatcmpl-xxx","choices":[{"finish_reason":"stop"}]}

data: [DONE]
```

### Stream options

```json
{
    "stream": true,
    "stream_options": {
        "include_usage": true,
        "include_obfuscation": false
    }
}
```

- **`include_usage: true`** — emits a final SSE chunk with `usage` populated. Recommended for AgentKthx so token tracking updates during the run.
- **`include_obfuscation: false`** — disables the random-character obfuscation field on streaming deltas. Obfuscation is enabled by default to mitigate side-channel attacks. Set to `false` to optimize bandwidth IF you trust the network links (e.g. on-prem to OpenAI over private link).

### Reasoning content in streaming

For reasoning models (gpt-5.x+, gpt-6.x), reasoning tokens arrive as separate SSE chunks BEFORE the visible content:

```
data: {"choices":[{"delta":{"reasoning":"Let me think..."}}]}
data: {"choices":[{"delta":{"reasoning":"First I should consider..."}}]}
data: {"choices":[{"delta":{"content":"The answer is 42"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
```

The `reasoning` field (sometimes `reasoning_content`) appears in delta chunks BEFORE the `content` field. AgentKthx should capture reasoning_content in both streaming and non-streaming responses, displaying it in a `reasoning:` panel above the `AgentKthx:` prompt (mirroring the OpenRouter implementation UX-01, R06.56).

### Responses API — streaming events

The Responses API uses a richer event-driven streaming protocol with named events:

```
event: response.created
data: {"id":"resp_xxx","status":"in_progress"}

event: response.output_item.added
data: {"item":{"type":"reasoning","id":"rs_xxx"}}

event: response.reasoning.delta
data: {"item_id":"rs_xxx","delta":"Let me think..."}

event: response.output_item.added
data: {"item":{"type":"message","id":"msg_xxx"}}

event: response.output_text.delta
data: {"item_id":"msg_xxx","delta":"The answer is"}

event: response.completed
data: {"id":"resp_xxx","status":"completed","usage":{"input_tokens":100,"output_tokens":50}}
```

Event types include:
- `response.created` — response object created
- `response.in_progress` — model started generating
- `response.output_item.added` — new item added to output array
- `response.reasoning.delta` — reasoning text delta
- `response.output_text.delta` — visible text delta
- `response.function_call_arguments.delta` — tool call argument delta
- `response.completed` — final response with usage
- `response.failed`, `response.cancelled` — terminal failure states

AgentKthx v0.1 should support Chat Completions streaming only. Responses API streaming is a future enhancement.

---

## Reasoning Configuration

### `reasoning_effort` parameter (Chat Completions API)

For reasoning-capable models (o-series, gpt-5.x+, gpt-6.x) on Chat Completions:

| Effort | Description | Use case |
|--------|-------------|----------|
| `none` | Disable reasoning entirely. **Only GPT-5.x supports this**; gpt-6 rejects. | Latency-critical tasks that don't benefit from multi-step reasoning |
| `minimal` | Brief reasoning, minimal tokens | Fast + cheap, basic tool use |
| `low` | Light reasoning | Latency-sensitive tasks needing some planning |
| `medium` (default for gpt-5.5+) | Balanced | Default starting point for most workloads |
| `high` | Deeper reasoning, more tokens | Complex planning, multi-step agentic tasks |
| `xhigh` | Maximum reasoning, large token spend | Hard reasoning, complex debugging, deep planning |
| `max` | Absolute maximum — use sparingly | Most complex tasks where intelligence matters most |

### `reasoning` object (Responses API — supersedes `reasoning_effort`)

```json
{
    "reasoning": {
        "effort": "medium",                  // same enum as reasoning_effort
        "mode": "standard|pro",              // standard (default) or pro (deeper, slower)
        "summary": "auto|concise|detailed",  // summary of reasoning visible in response
        "context": "auto|include",           // whether to carry reasoning from prior turns
        "include_thoughts": true             // surface raw reasoning tokens in output
    }
}
```

**GPT-5.6 and GPT-6 only**: `mode: "pro"` enables a deeper reasoning mode for difficult tasks. Mode and effort are independent — mode picks standard vs pro execution, effort controls how much reasoning within that mode.

### Reasoning tokens billing

Reasoning tokens are billed at the **output-token rate** but are NOT visible in the response unless `include_thoughts: true` (Responses API) is set. The `usage.completion_tokens_details.reasoning_tokens` field reports the count separately.

AgentKthx should:
1. Track `reasoning_tokens` separately in any token-budget UI so users understand why spend exceeds visible output
2. Default `include_thoughts: false` for Chat Completions (cannot control this — reasoning is never surfaced as content in Chat Completions)
3. Surface reasoning on Responses API when `--think` CLI flag is set

### Adaptive reasoning

OpenAI models reason adaptively across effort levels — using fewer tokens for simpler tasks even at `high` effort. Don't expect deterministic token counts for the same prompt; do expect quality to scale with effort.

---

## Error Codes & Recovery

### Common error codes

| Code | Meaning | Cause | Fix |
|------|---------|-------|-----|
| 400 | Bad Request | Malformed JSON, missing `model` or `messages`, unsupported parameter for model (e.g. `tools` on gpt-3.5, `stop` on reasoning models) | Validate request schema; check model capabilities in `MODEL_CONFIGS` |
| 401 | Unauthorized | Invalid/expired API key, malformed `Authorization` header, key lacks project access | Verify `OPENAI_API_KEY` env var; regenerate at platform.openai.com/api-keys; check project scoping |
| 403 | Forbidden | API key lacks permission for this model (e.g. `gpt-rosalind-research` requires trusted-access program) | Check API key scopes and project model permissions |
| 404 | Not Found | Model id typo, deprecated model removed from API | Verify model exists at platform.openai.com/docs/models; some legacy models (`text-davinci-003`, `gpt-3.5-turbo-0301`) are deprecated |
| 408 | Request Timeout | Provider took >60s to respond (typically with `service_tier: flex` on overloaded capacity) | Retry; consider switching to `service_tier: default` |
| 409 | Conflict | Concurrent modification of stored Responses (e.g. trying to `cancel` an already-completed response) | Refetch the current state; check `status` field |
| 413 | Payload Too Large | Request body or headers exceed 64 KiB combined | Reduce message count or move large content to Files API |
| 422 | Unprocessable Entity | Model doesn't support requested features (e.g. `tools` on a non-tool model, `response_format: json_schema` with non-strict schema) | Use ReAct fallback for tools; ensure strict schema for structured outputs |
| 429 | Too Many Requests | Rate limit hit (free tier: 100 RPM/90k TPM; paid: tier-dependent) | Honor `Retry-After` header; exponential backoff; consider `service_tier: flex` for queuing |
| 429 `insufficient_quota` | Trial credit exhausted | Free tier trial credit spent | Upgrade to paid tier OR wait for monthly credit refresh |
| 500 | Internal Server Error | OpenAI server crash | Retry with backoff (rare) |
| 502 | Bad Gateway | Edge proxy issue | Retry; check status.openai.com |
| 503 | Service Unavailable | Overloaded; capacity exhausted | Retry with backoff; consider `service_tier: flex` |
| 504 | Gateway Timeout | Request exceeded server-side timeout | Reduce `max_tokens`; simplify prompt; retry |

### 429 Retry-After handling

```python
retry_after_raw = response.headers.get("Retry-After", "10")
try:
    retry_after = int(retry_after_raw)
except (ValueError, TypeError):
    retry_after = 10
retry_after = min(max(retry_after, 1), 60)  # cap at 60s
```

AgentKthx's `OpenAIBackend._make_api_request()` should implement automatic 429 retry with up to 3 retries (`_MAX_429_RETRIES = 3`). Each retry waits the `Retry-After` duration (capped at 60s).

### Distinguishing rate limit types

OpenAI returns distinct error shapes for different rate limits:

```json
// Token-based TPM rate limit (your account hit tokens-per-minute cap)
{
    "error": {
        "code": "rate_limit_exceeded",
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "type": "rate_limit_error"
    }
}

// Request-based RPM rate limit (your account hit requests-per-minute cap)
{
    "error": {
        "code": "rate_limit_exceeded",
        "message": "Request too large. Reading the system message token budget.",
        "type": "rate_limit_error"
    }
}

// Trial credit exhausted (free tier hard limit)
{
    "error": {
        "code": "insufficient_quota",
        "message": "You exceeded your current quota, please check your plan and billing details."
    }
}
```

For `insufficient_quota`, retrying is futile — the user needs to either upgrade or wait for the monthly refresh. AgentKthx should detect this and surface a clear actionable error.

### Rate-limit response headers

OpenAI sends informative rate-limit headers on every response:

| Header | Description |
|--------|-------------|
| `x-ratelimit-limit-requests` | Max RPM for this account tier |
| `x-ratelimit-limit-tokens` | Max TPM for this account tier |
| `x-ratelimit-remaining-requests` | Remaining RPM |
| `x-ratelimit-remaining-tokens` | Remaining TPM |
| `x-ratelimit-reset-requests` | Time until RPM window resets (e.g. `1s`, `1m`, `1h`) |
| `x-ratelimit-reset-tokens` | Time until TPM window resets |
| `x-ratelimit-limit-project-tokens` | Project-scoped TPM limit (if project key) |
| `x-ratelimit-remaining-project-tokens` | Remaining project TPM |
| `x-ratelimit-reset-project-tokens` | Time until project TPM window resets |

AgentKthx should parse these headers and surface them in `--debug` mode, and could show remaining budget in the chat footer.

---

## Rate Limiting & Concurrency

### Free tier limits

- **100 requests per minute** (RPM) across all model families
- **90,000 tokens per minute** (TPM) input + output combined
- **$1 trial credit** for new accounts (one-time grant; consumed by any model)
- **$10/month in API credits** on the Free user-product tier (refreshes monthly)
- **Concurrent requests**: 5 (free tier)

### Paid tier limits

Paid tier limits scale with monthly spend:
| Monthly spend | RPM | TPM | Concurrent |
|---------------|-----|-----|------------|
| < $50 | 500 | 30k-90k | 10 |
| $50-$250 | 5,000 | 250k-1M | 50 |
| $250-$1k | 5,000 | 1M-3M | 100 |
| > $1k | 10,000 | 30M+ | 1,000+ |

Higher limits available on request (contact sales).

### Trial credit details

- **New account grant**: $1 USD credit, one-time, no expiry (verified Sept 2026 per omidsaffari.com report)
- **Free user-product tier**: $10/month API credits, refreshes on the 1st of each month, no rollover
- **Plus tier ($20/mo)**: $15/month API credits
- **Pro tier ($200/mo)**: $30/month API credits + access to higher rate limits
- **Trial credits can be spent on any model** including gpt-6-astra — there is no model-level restriction on the free tier

### AgentKthx implementation

AgentKthx does NOT currently implement client-side rate limiting. It relies on:
1. The 429 retry loop in `_make_api_request()` (max 3 retries with `Retry-After` honor)
2. The user to pace their requests if doing bulk operations (e.g. running `agentkthx test 04_gsm8k_benchmark --backend openai`)

For bulk workflows, users may want to add a client-side throttle or use the **Batch API** (50% discount on token rates, async ≤24h SLA). The Batch API is mentioned in `/v1/batches` endpoint and could be a future R07.x feature.

---

## Free Tier & Trial Credits

### What "free" actually means on OpenAI

OpenAI's free-tier model is **credit-based** (similar to Hugging Face, different from OpenRouter's `:free` suffix):

- ✅ New accounts receive $1 in trial credits (one-time grant)
- ✅ Free user-product tier accounts receive $10/month in API credits (refreshes monthly, no rollover)
- ✅ All models are accessible via the trial credit (no model-level restriction)
- ✅ Standard rate limits apply (100 RPM, 90k TPM) — same for free and paid tiers
- ❌ Trial credits do not roll over month-to-month
- ❌ Free tier has lower concurrency limits (5 concurrent requests vs 10+ on paid)
- ❌ Free tier is subject to the same rate limits as paid — there's no "free queue"

### `OPENAI_FREE_ONLY` env var — proposed behavior

To mirror the OpenRouter `OPENROUTER_FREE_ONLY` pattern and protect users from accidental paid API calls, AgentKthx should implement:

```bash
# Strict free-only mode (default false)
export OPENAI_FREE_ONLY=true

# Model to fall back to if the requested model is not in the free whitelist
export OPENAI_FREE_FALLBACK_MODEL="gpt-4o-mini"
```

When `OPENAI_FREE_ONLY=true`:
1. Any model not in `OPENAI_FREE_MODEL_WHITELIST` is rejected with an actionable error:
   ```
   Model 'gpt-6-astra' is not in the OpenAI free-tier whitelist.
   Either set OPENAI_FREE_ONLY=false (requires paid OpenAI API key with billing enabled) or
   pick a whitelisted model. See OPENAI_API_TECHNICAL_REFERENCE.md for the list.
   ```
2. `service_tier` is forced to `default` (never `priority`/`fast`/`scale`)
3. The `reasoning_effort` parameter is capped at `low` (reasoning tokens are billed at output rate and can quickly exhaust the monthly credit)
4. HTTP 429 with `insufficient_quota` is treated as a hard failure (no retry) — retrying is futile
5. The agentic loop surfaces the credit-exhaustion condition to the user with a clear actionable message

When `OPENAI_FREE_ONLY=false` (default):
1. No model filtering happens
2. HTTP 429 with `insufficient_quota` triggers a fallback to `OPENAI_FREE_FALLBACK_MODEL` if set, mirroring the ZAI plugin's pattern
3. The user is responsible for monitoring their OpenAI account balance at platform.openai.com/usage

### Detecting free-tier support for tools

Run `agentkthx models --backend openai --tool-support` to test each whitelisted model's tool support. Results are cached in `~/.cache/agentkthx/tool_support.json` (mirroring the OpenRouter implementation).

All OpenAI models in the whitelist support native function calling — there is no ReAct fallback needed for OpenAI (unlike Hugging Face where some free providers lack tool support). However:
- `parallel_tool_calls` may behave differently on gpt-4o-mini vs gpt-6-astra — the smaller model may serialize calls even when `parallel_tool_calls: true` is set
- `response_format: json_schema` with `strict: true` is supported on all whitelisted models

### Free-tier credit monitoring

AgentKthx should periodically fetch the user's remaining trial credit via the OpenAI Admin API (`/v1/organization/costs` endpoint — requires admin key). When credit drops below 20% of monthly allocation, surface a warning. This is a future enhancement; not implemented in v0.1.

For simpler usage monitoring, AgentKthx can show the trial credit balance at session start using a one-shot call to the Admin API's `/v1/organization/usage` endpoint.

---

## Multimodal Content Handling

### Image input (vision models)

OpenAI accepts OpenAI-style multimodal messages on gpt-4o, gpt-5.x, gpt-6.x families:

```json
{
    "role": "user",
    "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg", "detail": "auto"}}
    ]
}
```

**`detail` values**:
- `"auto"` (default) — model picks resolution based on image size
- `"low"` — fixed 512x512 thumbnail, fast/cheap
- `"high"` — full detail, multiple tiles for large images, slower/costlier

### Image size limits

- **URL input**: must be HTTPS; max ~20 MB per image; OpenAI fetches the URL on their server
- **Base64 data URL input**: `data:image/jpeg;base64,...` format, max ~4 MB per image (smaller than URL due to encoding overhead)
- **Max images per request**: 50 for gpt-4o, 100 for gpt-6 (combined with text and audio)
- **Supported formats**: PNG, JPEG, WEBP, GIF (non-animated); JFIF not supported

### Audio input

Audio input via Chat Completions API:

```json
{
    "role": "user",
    "content": [
        {"type": "text", "text": "Transcribe this audio"},
        {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav|mp3"}}
    ]
}
```

Supported on gpt-4o-audio-preview, gpt-5.x, gpt-6.x. Audio must be base64-encoded WAV or MP3.

### Audio output

Audio output via Chat Completions API (gpt-4o-audio-preview and later):

```json
{
    "modalities": ["text", "audio"],
    "audio": {"voice": "alloy", "format": "mp3"}
}
```

Response includes `audio` field in the assistant message with `id`, `data` (base64), `expires_at` (12 hours), `transcript` (text). Audio output is billed at the audio-token rate.

### File input (Responses API only)

The Responses API supports file content parts:

```json
{
    "type": "file",
    "file": {
        "file_id": "file-xxx",
        "filename": "report.pdf"
    }
}
```

Files must be uploaded first via the Files API (`/v1/files`). Supported for PDF, plain text, CSV, JSON, markdown on gpt-4o, gpt-5.x, gpt-6.x. Files up to 32 MB each, up to 50 files per request.

### Video input

Video input is NOT supported on the Chat Completions or Responses APIs (Sept 2026). Users needing video understanding must extract frames client-side and pass as image sequence.

### PDF input

PDF input is supported via:
1. The Files API + Responses API `file` content part (recommended for large PDFs)
2. URL image input with `data:application/pdf;base64,...` (small PDFs only, ≤4 MB)
3. Base64-encoded PDF via image_url (same size constraint)

---

## Implementation Notes for AgentKthx

> **R07.03 update**: The OpenAI plugin has been scaffolded and shipped as
> `agentkthx/plugins/openai/`. The live `/v1/models` API returns 128
> models; the static `OPENAI_MODELS` catalog (expanded from 15 → 38
> models after discovering the full Sept 2026 lineup) provides richer
> metadata (context_length, pricing, capability flags) for the models
> documented here. A `_NON_CHAT_PATTERNS` filter (mirroring
> GeminiBackend's pattern) excludes ~34 non-chat models (embeddings,
> TTS, image gen, video gen, moderation, ASR, legacy completions) from
> the listing so users see only chat-capable models (~94 after
> filtering). The `OPENAI_FREE_MODEL_WHITELIST` (6 very-low-cost
> models) filters further when `OPENAI_FREE_ONLY=true`. See the
> `test_tool_support()` method — non-chat models return
> `ToolSupportLevel.NONE` so users don't accidentally try to chat
> with an embedding model.

### Backend file location

```
agentkthx/plugins/openai/
├── __init__.py            # register()/unregister() with alias_of="openai" for `oai`
├── plugin.json            # plugin manifest (v0.2 schema, shipped in R07.03)
└── openai.py              # OpenAIBackend class (1967 lines, shipped in R07.03)
```

### Key methods (mirror OpenRouterBackend shape)

| Method | Purpose |
|--------|---------|
| `__init__()` | Initializes with `OPENAI_API_KEY` env var, detects key type (legacy `sk-`, `sk-proj-`, `sk-sa-`), sets HTTP headers including `OpenAI-Organization` / `OpenAI-Project` if env vars present, populates `_model_cache` via `list_models()` |
| `list_models()` | Fetches `/v1/models` (auth required — unlike HF Router which is anonymous), caches for 1 hour (`_CACHE_TIMEOUT = 3600`). Filters via `_NON_CHAT_PATTERNS` (excludes embeddings, TTS, image gen, video gen, moderation, ASR, legacy completions — ~34 of 128 live models excluded). Filters to `OPENAI_FREE_MODEL_WHITELIST` if `OPENAI_FREE_ONLY=true`. Static `OPENAI_MODELS` catalog (38 chat-capable models, expanded R07.03) provides richer metadata (context_length, pricing, capability flags) for models documented here — falls back to conservative defaults (128K context, 16K max_tokens) for models only in the live API. |
| `is_running()` | Always returns `True` (cloud API, no local server) |
| `generate(model, messages, tools, **kwargs)` | Main entry point. Dispatches to `_make_api_request()`. Implements ReAct fallback (rarely needed for OpenAI but kept for parity). Enforces `OPENAI_FREE_ONLY` whitelist. Caps `reasoning_effort` at `low` if free-only. |
| `generate_stream(model, messages, **kwargs)` | SSE streaming variant. Yields `delta` chunks. Captures `reasoning` content for thinking models. |
| `_make_api_request(endpoint, data, stream)` | HTTP wrapper with 429 retry, 401 detection, 429 `insufficient_quota` hard-fail handling, error normalization |
| `_stream_request(url, data, headers)` | Low-level SSE parser |
| `_build_openai_body(...)` | Centralizes request body construction (shared by generate + generate_stream). Auto-translates `system` role to `developer` for reasoning models. Forwards `reasoning_effort` and `service_tier` from env vars. |
| `_parse_openai_response(raw_response)` | Extracts `content`, `tool_calls`, `finish_reason`, `usage`, `reasoning_content`, `annotations`, `refusal`. Raises on top-level `error` field. |
| `_is_tools_not_supported_error(err_str)` | Detects "does not support tools" patterns (rare for OpenAI but kept for parity) |
| `_is_insufficient_quota(response)` | Detects HTTP 429 with `insufficient_quota` code — surfaces actionable error, no retry |
| `_enforce_free_only(model_id, kwargs)` | Validates model id is in `OPENAI_FREE_MODEL_WHITELIST`; caps `reasoning_effort` at `low`; forces `service_tier` to `default` |
| `_detect_key_type(api_key)` | Parses key prefix to determine type (`user`, `project`, `admin`, `service_account`); surfaces warnings for legacy keys |
| `test_tool_support(model, family, force_test)` | Runtime test: send a probe tool call, detect NATIVE/REACT/NONE. OpenAI models should always return NATIVE. |
| `_jev_call_completions(model, messages, ...)` | JEV api_mode hook — routes through `generate()` so auth + 429 retry are preserved |

### Configuration

```bash
# Required
export OPENAI_API_KEY="sk-proj-..."   # Project API key recommended

# Optional
export OPENAI_BASE_URL="https://api.openai.com/v1"  # default
export OPENAI_DEFAULT_MODEL="gpt-6-sol"              # default model
export OPENAI_ORGANIZATION_ID="org-xxx"              # if account has multiple orgs
export OPENAI_PROJECT_ID="proj_xxx"                  # for project-scoped billing
export OPENAI_FREE_ONLY=false                        # strict free-tier enforcement
export OPENAI_FREE_FALLBACK_MODEL="gpt-4o-mini"      # used when OPENAI_FREE_ONLY=false and 429 insufficient_quota received
export OPENAI_SERVICE_TIER=""                        # "", "default", "flex", "priority"
export OPENAI_REASONING_EFFORT=""                     # "", "minimal", "low", "medium", "high"
```

### Configuration env vars (AgentKthx core)

AgentKthx uses the `AGENTKTHX_*` env var prefix:
- `AGENTKTHX_BACKEND=openai` — set default backend
- `AGENTKTHX_API_MODE=openai` — OpenAI only supports OpenAI mode (Responses API mode is a future enhancement)

### What AgentKthx does NOT yet implement (potential future work)

- **Responses API** — only Chat Completions is targeted in v0.1. Responses API offers better reasoning model support, built-in tools (web_search, file_search, code_interpreter), and stateful conversations.
- **Built-in tools** — `web_search`, `file_search`, `code_interpreter`, `computer_use` (Responses API only)
- **Batch API** — `/v1/batches` endpoint for 50% discount on async workloads (≤24h SLA)
- **Realtime API** — `wss://api.openai.com/v1/realtime` for voice/audio sessions (not chat backend)
- **Audio modality** — input and output audio in chat completions (`modalities: ["audio"]`)
- **Image generation** — `/v1/images/generations` endpoint with gpt-image-2 / gpt-image-2.5
- **Embeddings** — `/v1/embeddings` endpoint for vector search / RAG
- **Files API** — `/v1/files` for upload and use in Responses API
- **Moderations** — `/v1/moderations` for content safety checks
- **Assistants API (legacy, being deprecated)** — `/v1/assistants`, `/v1/threads`, `/v1/runs`. Migrate to Responses API.
- **Conversations API** — stateful multi-turn conversations layered on Responses
- **Webhooks** — `/v1/webhooks` for server-to-server event delivery

---

## Proposed plugin.json

Following the v0.2 schema (mirroring the ZAI and OpenRouter plugin manifests), the proposed `agentkthx/plugins/openai/plugin.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "openai",
  "version": "0.1.0",
  "description": "OpenAI API backend for GPT-6/GPT-5.6/GPT-4o families via OpenAI Chat-Completions API (free tier + paid, with OPENAI_FREE_ONLY enforcement and trial-credit-exhaustion fallback)",
  "author": {
    "name": "VTSTech",
    "url": "https://www.vts-tech.org"
  },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "OpenAI Cloud Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "OPENAI",
        "defaults": {
          "OPENAI_BASE_URL": "https://api.openai.com/v1",
          "OPENAI_API_KEY": "",
          "OPENAI_ORGANIZATION_ID": "",
          "OPENAI_PROJECT_ID": "",
          "OPENAI_DEFAULT_MODEL": "gpt-6-sol",
          "OPENAI_FREE_ONLY": "false",
          "OPENAI_FREE_FALLBACK_MODEL": "gpt-4o-mini",
          "OPENAI_SERVICE_TIER": "",
          "OPENAI_REASONING_EFFORT": ""
        }
      },
      "provides": {
        "backends": {
          "openai": "openai.OpenAIBackend"
        },
        "cli_commands": [],
        "cli_flags": {
          "--backend": [
            "openai",
            "oai"
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

Notes on the manifest:
- **`name`**: `openai` (matches directory name, follows existing plugin naming convention)
- **`version`**: `0.1.0` (initial release; Chat Completions API only in v0.1, Responses API planned for v0.2)
- **`env_prefix`**: `OPENAI` (matches OpenAI SDK convention)
- **`cli_flags."--backend"`**: includes both `openai` (canonical) and `oai` (alias) for ergonomic CLI usage
- **`defaults`**: includes all relevant env vars including org/project IDs, service tier, reasoning effort
- **`OPENAI_FREE_ONLY`**: stored as string `"false"` to match the env-var convention; the backend parses it to bool via `_truthy()` helper (mirrors `ZAI_FREE_ONLY`, `OPENROUTER_FREE_ONLY`, and `HF_FREE_ONLY` patterns)
- **`OPENAI_FREE_FALLBACK_MODEL`**: `gpt-4o-mini` — broad availability, low token cost, supports all key features (function calling, structured output, multimodal)
- **`compatibility.agentkthx`**: `>=0.5.0` (matches OpenRouter plugin's minimum — same architecture, same `OpenAICompatibleBackend` base class)

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401 Unauthorized` | Invalid or expired API key | Check `OPENAI_API_KEY` env var; regenerate at platform.openai.com/api-keys; ensure key has project access |
| `403 Forbidden` | Project key lacks access to restricted model (e.g. `gpt-rosalind-research`) | Check project model permissions at platform.openai.com/settings/project/.../limits |
| `404 Not Found` | Model id typo or model deprecated | Check `agentkthx models --backend openai` for current list; some legacy models (`gpt-3.5-turbo-0301`, `text-davinci-003`) are removed |
| `422 Unprocessable Entity` | Model doesn't support requested features (e.g. `stop` on reasoning models, `n>1` on gpt-5.x) | Check `MODEL_CONFIGS` for model capabilities; use `reasoning_effort` instead of `stop` for reasoning models |
| `429 Too Many Requests` | RPM or TPM rate limit hit | Wait `Retry-After` seconds (auto-retried up to 3x by AgentKthx); reduce `max_tokens`; consider `service_tier: flex` for async |
| `429 insufficient_quota` | Trial credit exhausted (free tier) | Upgrade to paid tier at platform.openai.com/settings/billing OR wait for monthly credit refresh |
| `429 insufficient_quota` (with `OPENAI_FREE_ONLY=false`) | Trial credit exhausted mid-run | AgentKthx auto-falls-back to `OPENAI_FREE_FALLBACK_MODEL`; or upgrade to paid tier |
| Empty response (no content, no tool_calls) | Model refused (content filter) or hit content_filter finish_reason | Check `--debug` for `finish_reason` and `refusal` field; rephrase prompt |
| Slow first-token latency | High `reasoning_effort` on gpt-6-astra can take 5-30s before first visible token | Use `reasoning_effort: low` for latency-sensitive workloads; switch to gpt-6-luna for fastest reasoning |
| Reasoning not displayed with `--think` | Streaming path doesn't capture `reasoning_content` | Make sure `stream_options.include_usage: true` is set; reasoning delta chunks need explicit capture |
| Token count way too high | Reasoning tokens counted as output tokens | Check `usage.completion_tokens_details.reasoning_tokens` separately from `completion_tokens`; surface this in token UI |
| `parallel_tool_calls: true` not parallel | Smaller models (gpt-4o-mini) serialize calls even when parallel flag is set | Switch to gpt-6-sol or gpt-5.5+ for true parallel tool calling |
| `response_format: json_schema` rejected | Non-strict schema (missing `additionalProperties: false`, optional fields not in `required`, etc.) | Ensure schema follows OpenAI strict-mode rules |
| Streaming response missing `usage` | `stream_options.include_usage` not set | AgentKthx sets this by default; verify in `--debug` that request body includes `stream_options: {"include_usage": true}` |
| `OPENAI_FREE_ONLY=true` blocking a model | Model not in whitelist | Either add the model to `OPENAI_FREE_MODEL_WHITELIST` in your local plugin config, or set `OPENAI_FREE_ONLY=false` (requires paid OpenAI API key with billing) |
| `model not found` for new GPT-6 model | Legacy OpenAI SDK caches older `/v1/models` response | Force-refresh: `agentkthx models --backend openai --refresh` |
| Long-running agentic task hits context window | Reasoning tokens + multi-turn history exceeding model context | Use `/clear` between turns; use Responses API `previous_response_id` for stateful conversations (future feature); enable compaction |
| Image URL rejected | URL not HTTPS, or URL not publicly reachable | Convert to base64 data URL, or host on a public HTTPS endpoint |
| `image_url` size limit exceeded | Base64 image > 4 MB | Compress or resize image client-side; use URL input for larger images (up to 20 MB) |
| Trial credit monitoring doesn't show | Admin API key required (`sk-admin-...`) | Either use Admin API key, or accept that trial credit tracking is unavailable; rely on `429 insufficient_quota` as the exhaustion signal |

---

## References

- **OpenAI API Reference**: https://platform.openai.com/docs/api-reference
- **Chat Completions Reference**: https://platform.openai.com/docs/api-reference/chat
- **Responses API Reference**: https://platform.openai.com/docs/api-reference/responses
- **Models Overview**: https://platform.openai.com/docs/models
- **Pricing**: https://platform.openai.com/docs/pricing
- **Reasoning Models Guide**: https://platform.openai.com/docs/guides/reasoning
- **Text Generation Guide**: https://platform.openai.com/docs/guides/text-generation
- **Function Calling Guide**: https://platform.openai.com/docs/guides/function-calling
- **Structured Outputs Guide**: https://platform.openai.com/docs/guides/structured-outputs
- **Streaming Guide**: https://platform.openai.com/docs/api-reference/streaming
- **Rate Limits**: https://platform.openai.com/docs/guides/rate-limits
- **Error Codes**: https://platform.openai.com/docs/guides/error-codes
- **API Keys (creating)**: https://platform.openai.com/api-keys
- **Usage Dashboard**: https://platform.openai.com/usage
- **Billing Settings**: https://platform.openai.com/settings/billing
- **Status Page**: https://status.openai.com
- **Realtime API (WebSocket)**: https://platform.openai.com/docs/api-reference-realtime
- **Batch API**: https://platform.openai.com/docs/api-reference-batch
- **Files API**: https://platform.openai.com/docs/api-reference/files
- **Assistants API (legacy)**: https://platform.openai.com/docs/api-reference-assistants
- **Migration guide (Assistants → Responses)**: https://platform.openai.com/docs/guides/migrating-to-responses
- **LLMs.txt (full doc index)**: https://platform.openai.com/llms.txt
- **Backwards Compatibility**: https://platform.openai.com/docs/api-reference/backwards-compatibility

---

Written by VTSTech — https://www.vts-tech.org
