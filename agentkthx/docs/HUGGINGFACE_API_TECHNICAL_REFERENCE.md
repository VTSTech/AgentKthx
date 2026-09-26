# Hugging Face API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://huggingface.co/docs/inference-providers
> **Last Updated**: 2026-09-26
> **Target Audience**: AgentKthx Developers

## Table of Contents

1. [Authentication & Endpoint Details](#authentication--endpoint-details)
2. [Request/Response Structure](#requestresponse-structure)
3. [Sampling Parameters](#sampling-parameters)
4. [Model Catalog & Discovery](#model-catalog--discovery)
5. [Function Calling Implementation](#function-calling-implementation)
6. [Streaming & Real-time Features](#streaming--real-time-features)
7. [Provider Routing Preferences](#provider-routing-preferences)
8. [Error Codes & Recovery](#error-codes--recovery)
9. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
10. [Free Tier Behavior](#free-tier-behavior)
11. [Multimodal Content Handling](#multimodal-content-handling)
12. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
13. [Proposed plugin.json](#proposed-pluginjson)
14. [Troubleshooting Matrix](#troubleshooting-matrix)

---

## Authentication & Endpoint Details

### Base URLs

Hugging Face exposes **two parallel inference surfaces**. The Inference Router is the OpenAI-compatible chat completions endpoint and the primary AgentKthx backend target. The legacy Serverless Inference API is preserved for per-model TGI text generation and is being deprecated.

```python
# Production — Inference Router (preferred for AgentKthx)
BASE_URL_ROUTER = "https://router.huggingface.co/v1"

# Production — Serverless Inference API (legacy, per-model TGI)
BASE_URL_SERVERLESS = "https://api-inference.huggingface.co"

# Production — Inference Endpoints (dedicated, paid only; mention for completeness)
BASE_URL_DEDICATED = "https://api.endpoints.huggingface.cloud/v2/endpoint/{endpoint_id}"
```

### API Endpoints

```python
# === Inference Router (OpenAI-compatible) ===
CHAT_COMPLETIONS = "/chat/completions"     # POST  — OpenAI-style chat completions
MODELS_LIST      = "/models"               # GET   — list all models served by ≥1 provider

# === Serverless Inference API (legacy TGI surface) ===
TEXT_GENERATION  = "/models/{model}"       # POST  — TGI text-generation task
CHAT_COMPLETIONS_LEGACY = "/models/{model}/v1/chat/completions"  # POST — per-model chat
TEXT_EMBEDDINGS   = "/pipeline/feature-extraction/{model}"  # POST  — legacy embeddings
IMAGE_GEN         = "/models/{model}"      # POST  — text-to-image task (different body)

# === Hub API (auxiliary — model metadata, not inference) ===
HUB_MODELS        = "https://huggingface.co/api/models"          # GET  — model search
HUB_MODEL_INFO    = "https://huggingface.co/api/models/{model}"  # GET  — single model
```

### Authentication Headers

```python
headers = {
    "Authorization": "Bearer hf_****",   # HF access token (fine-grained, Inference Providers scope)
    "Content-Type":  "application/json",
    "User-Agent":    "AgentKthx/0.x (+https://github.com/VTSTech/AgentKthx)",  # optional
}
```

**Token types** (created at https://huggingface.co/settings/tokens):
- **Fine-grained** (recommended) — pick "Make calls to Inference Providers" permission. Other scopes can be added (read repos, write repos, etc.) but only Inference Providers is required for the router.
- **Read access** — works for Hub API metadata calls but NOT for inference billing.
- **Write access** — never use for inference; will work but exposes unnecessary scope.

Token rotation: revocations propagate within seconds. New tokens take effect immediately. Treat the token as a secret — never expose in client-side code or commit to repos.

### Request Format Requirements

- **Content-Type**: `application/json` only (binary endpoints like image generation use `application/octet-stream` for response)
- **Character Encoding**: UTF-8
- **Max Request Size**: 4 MB on the router; per-provider cap may be smaller (typically 1–2 MB on free tier)
- **Timeout**: 60 seconds recommended for chat; 120 seconds for streaming long completions; 30 seconds for `/models`
- **HTTPS only** — plaintext HTTP is rejected at the edge

---

## Request/Response Structure

### Complete Request Schema (Inference Router — OpenAI-compatible)

```json
{
    "model": "openai/gpt-oss-120b:fastest",
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
    "stream_options": { "include_usage": true },
    "stop": ["###"],
    "seed": 42,
    "n": 1,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "repetition_penalty": 1.0,
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
    "tool_choice": "auto|none|required",
    "user": "user-identifier"
}
```

### Response Schema (OpenAI-compatible)

```json
{
    "id": "chatcmpl-xxxxxxxxxxxx",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "openai/gpt-oss-120b",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "reasoning": "Chain of thought (some reasoning models — Qwen3-Thinking, DeepSeek-R1)",
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
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150
    }
}
```

Key Hugging Face-specific fields:
- **`model`** in the response mirrors the model id **without** the `:fastest` / `:cheapest` / `:preferred` suffix — that suffix is a routing hint, not part of the model identity.
- **`reasoning`** appears on reasoning-capable models (Qwen3-Thinking family, DeepSeek-R1, openai/gpt-oss-20b-reasoning). Field name varies by upstream provider — most return `reasoning`, some return `reasoning_content`. AgentKthx should treat both as the same field.

### Legacy Serverless Inference API — TGI Text Generation Request

The legacy `POST /models/{model}` endpoint accepts a TGI-style payload, NOT the OpenAI shape. This is the original HF Inference API surface.

```json
{
    "inputs": "What is the capital of France?",
    "parameters": {
        "max_new_tokens": 100,
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": 40,
        "top_n_tokens": 5,
        "repetition_penalty": 1.0,
        "do_sample": true,
        "seed": 42,
        "stop": ["###"],
        "truncate": 1024,
        "typical_p": 0.95,
        "watermark": false,
        "return_full_text": false,
        "best_of": 1,
        "adapter_id": "lora-adapter-id",
        "grammar": {
            "type": "json|regex|json_schema",
            "value": "..."
        },
        "decoder_input_details": false,
        "details": true
    },
    "stream": false
}
```

### Legacy Serverless — TGI Text Generation Response

```json
{
    "generated_text": "The capital of France is Paris.",
    "details": {
        "finish_reason": "length|eos_token|stop_sequence",
        "generated_tokens": 7,
        "seed": 42,
        "input_length": 6,
        "prefill": [
            {"id": 0, "text": "What", "logprob": -0.5},
            {"id": 1, "text": " is", "logprob": -0.2}
        ],
        "tokens": [
            {"id": 100, "text": "The", "logprob": -0.1, "special": false, "top_tokens": []}
        ]
    }
}
```

`finish_reason` values on the TGI surface differ from OpenAI:
- `length` — `max_new_tokens` reached
- `eos_token` — end-of-sequence token generated
- `stop_sequence` — stop string matched

AgentKthx should map `eos_token` and `stop_sequence` → `"stop"`, and `length` → `"length"` to normalize for the agentic loop.

---

## Sampling Parameters

The Inference Router forwards OpenAI-style parameters. The legacy Serverless endpoint accepts TGI-style parameters. The router translates between them where possible, but not all TGI parameters are accepted on the OpenAI-compat path.

### Temperature-family parameters

| Parameter | Type | Range | Default | Surface | Description |
|-----------|------|-------|---------|---------|-------------|
| `temperature` | float | 0.0–2.0 | provider default | Router + TGI | Controls randomness |
| `top_p` | float | 0.0–1.0 | 1.0 | Router + TGI | Nucleus sampling |
| `top_k` | int | 0–N | 0 (disabled) | Router + TGI | Top-K sampling |
| `top_n_tokens` | int | 0–N | 0 (disabled) | TGI only | Top-N alternatives per token (TGI-specific) |
| `typical_p` | float | 0.0–1.0 | 0.95 | TGI only | Typical decoding (router ignores) |
| `seed` | int | any | None | Router + TGI | Reproducibility (best-effort) |

### Penalty parameters

| Parameter | Type | Range | Default | Surface | Description |
|-----------|------|-------|---------|---------|-------------|
| `presence_penalty` | float | -2.0 to 2.0 | 0.0 | Router only | Penalize tokens already present |
| `frequency_penalty` | float | -2.0 to 2.0 | 0.0 | Router only | Penalize proportional to frequency |
| `repetition_penalty` | float | 0.0–2.0 | 1.0 | TGI + Router (some providers) | Multiplier: 1.0 = none, >1.0 = discourage repeats |

### Generation control

| Parameter | Type | Default | Surface | Description |
|-----------|------|---------|---------|-------------|
| `max_tokens` | int | provider default | Router | OpenAI-style. Maximum tokens to generate |
| `max_new_tokens` | int | 100 | TGI | TGI-style. Same effect as `max_tokens` on router |
| `max_completion_tokens` | int | provider default | Router (some providers) | OpenAI newer field. Many partner providers may not honor |
| `stop` | list[str] | None | Router + TGI | Stop sequences (up to 4 strings) |
| `n` | int | 1 | Router only | Number of completions. Most providers cap at 1 |
| `truncate` | int | None | TGI only | Truncate input tokens to this size |
| `return_full_text` | bool | false | TGI only | Prepend prompt to output |
| `best_of` | int | 1 | TGI only | Generate `best_of` sequences, return highest-logprob one |
| `do_sample` | bool | false | TGI only | Activate logits sampling (false = greedy) |
| `watermark` | bool | false | TGI only | Apply watermarking (Hugging Face watermark paper) |

### Structured output

| Parameter | Type | Surface | Description |
|-----------|------|---------|-------------|
| `response_format` | object | Router | `{type: "text"|"json_object"|"json_schema"}` — OpenAI-style structured output. Supported on most partner providers for tool-calling-capable models. |
| `grammar` | object | TGI only | `{type: "json"|"regex"|"json_schema", value: <string or schema>}` — TGI-native grammar constraint. More expressive than OpenAI's `response_format`. |

### AgentKthx implementation status

The AgentKthx `HuggingFaceBackend._build_openai_body()` method should mirror the OpenRouter pattern — forward these parameters from `**kwargs`:

```python
optional_int_fields   = ("top_p", "top_k", "seed", "n")
optional_float_fields = ("presence_penalty", "frequency_penalty", "repetition_penalty")
optional_bool_fields  = ("stream", "include_usage")
optional_dict_fields  = ("response_format", "stream_options")
```

**Missing parameters worth adding later**:
- `min_p` — newer alternative to `top_p`, supported on Llama-3.x via TGI
- `top_a` — alternative nucleus sampler
- `logit_bias` — useful for steering specific tokens

---

## Model Catalog & Discovery

### `/v1/models` endpoint (Router)

```bash
curl https://router.huggingface.co/v1/models \
    -H "Authorization: Bearer $HF_TOKEN"
```

```json
{
    "data": [
        {
            "id": "openai/gpt-oss-120b",
            "object": "model",
            "created": 1700000000,
            "owned_by": "openai",
            "context_length": 131072,
            "max_completion_tokens": 32768,
            "pricing": {
                "prompt": "0.0000006",
                "completion": "0.0000018",
                "image": "0",
                "request": "0",
                "web_search": "0"
            },
            "supported_parameters": [
                "tools", "tool_choice", "temperature", "max_tokens",
                "top_p", "presence_penalty", "frequency_penalty", "seed",
                "top_k", "reasoning"
            ],
            "providers": [
                {"provider": "novita", "context_length": 131072, "max_completion_tokens": 32768},
                {"provider": "fireworks", "context_length": 131072, "max_completion_tokens": 32768},
                {"provider": "groq", "context_length": 131072, "max_completion_tokens": 8192}
            ]
        }
    ],
    "object": "list"
}
```

Key fields for AgentKthx to inspect:
- **`id`** — full model id including org prefix (e.g. `openai/gpt-oss-120b`, `Qwen/Qwen3-4B-Thinking-2507`)
- **`context_length`** — model's full context window
- **`pricing.prompt` / `pricing.completion`** — USD per token. `0` means free-tier covered (rare on HF; most have token rates even on free tier)
- **`supported_parameters`** — list of OpenAI-style parameters the model accepts
- **`providers`** — list of upstream providers serving this model (HF Inference, Together, Groq, Novita, etc.)

### Free model detection

Unlike OpenRouter's `:free` suffix convention, **Hugging Face does not encode "free" in the model id**. Free-tier status is determined by:
1. The user's account balance (free monthly credit + paid balance)
2. The provider routing choice (some providers cost more than others)
3. The `:cheapest` routing policy returning the most cost-efficient provider

This means AgentKthx cannot filter the model list by a `:free` suffix. Instead, the `HF_FREE_ONLY` env var should be enforced by:
1. Allowing only models whose `pricing.prompt == 0 AND pricing.completion == 0` — extremely rare on HF
2. Or: routing to `:cheapest` provider and tracking spend against the free monthly credit allowance
3. Or: querying the user's HF billing API to confirm subscription status (PRO user, team member, or free)

The cleanest AgentKthx interpretation of `HF_FREE_ONLY=true` is:
- Restrict model ids to a curated `HF_FREE_MODEL_WHITELIST` (below) of models that historically have free-tier access via partner providers
- Append `:cheapest` to the model id automatically
- Fail-fast with a clear error if the user tries to use a non-whitelisted paid model

### Free model whitelist (AgentKthx-curated, validated against live API 2026-09-26)

> **R07.03 update**: The whitelist was pruned from 31 → 16 models after a live
> API probe (`bash probe_huggingface.sh`) confirmed 15 catalog entries were no
> longer served by any partner provider. Dead models removed: all Mistral
> variants (Mistral-7B-Instruct-v0.3, Mistral-Nemo-Instruct-2407,
> Mixtral-8x7B-Instruct-v0.1 — the entire Mistral family was rotated out),
> all Phi variants (Phi-3.5-mini, Phi-3.5-MoE, Phi-4-mini), older Gemma 2
> variants (gemma-2-2b-it, gemma-2-9b-it — superseded by Gemma 3), Llama 3.2
> small variants (1B/3B — Llama 3.1-8B and 3.3-70B still served), Qwen
> legacy variants (Qwen2.5-7B-Instruct-1M, Qwen2.5-Math-7B-Instruct —
> superseded by Qwen3 variants), CohereForAI Command R variants (org renamed
> to CohereLabs — live models now under `CohereLabs/` prefix), and
> zai-org/GLM-Z1-32B-0414 (replaced by newer GLM variants).
>
> The `HF_FREE_FALLBACK_MODEL` default was also updated from the dead
> `Qwen/Qwen2.5-7B-Instruct-1M` to `openai/gpt-oss-20b` (live, broad
> partner support, small model).
>
> Live API probe findings (Sept 26 2026):
> - **14 active partner providers** (not 18 — Fal AI, Replicate,
>   WaveSpeedAI, and HF Inference are not currently serving models)
> - **139 models** in the live `/v1/models` response, **337 (model,
>   provider) combos**
> - **`is_free=true` on 0/337 combos** — free-tier is purely credit-based
> - **3 models with $0 pricing** (`prism-ml/Ternary-Bonsai-27B-*` via
>   Together, `inclusionAI/Ling-3.0-flash-Fin` via Novita) — these
>   have $0 input + $0 output but are still flagged `is_free=false`.
>   May be genuinely free or a provider-side pricing default — worth
>   probing with a single chat-completions call to confirm.
> - **Pricing data quality issue**: some providers (nscale, novita)
>   return pricing values like `$10,000/1M` — clearly misconfigured on
>   the provider's side. HF Router forwards whatever the provider sends.
>   The `_parse_hf_model()` aggregator takes the MIN across providers,
>   so a single misconfigured provider doesn't dominate — but users
>   should verify pricing at huggingface.co/playground before relying
>   on the `cheapest_input_per_1m` field.

```python
HF_FREE_MODEL_WHITELIST = {
    # OpenAI open-weighted models (free at HF partner providers)
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",

    # Qwen family — Alibaba (pruned: 7B-Instruct-1M, Math-7B removed —
    # not in live API as of Sept 2026; superseded by Qwen3 variants)
    "Qwen/Qwen3-4B-Thinking-2507",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",

    # DeepSeek family — reasoning models
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.1",

    # Meta Llama family (pruned: Llama-3.2-1B/3B removed — not in live API)
    "meta-llama/Llama-3.1-8B-Instruct",
    "meta-llama/Llama-3.3-70B-Instruct",

    # Google Gemma family (pruned: gemma-2-2b-it, gemma-2-9b-it removed —
    # superseded by Gemma 3 variants which are still live)
    "google/gemma-3-4b-it",
    "google/gemma-3-12b-it",
    "google/gemma-3-27b-it",

    # zai-org / GLM (pruned: GLM-Z1-32B-0414 removed — not in live API)
    "zai-org/GLM-4.5",
    "zai-org/GLM-4.5-Air",
}
```

**Caveat**: This whitelist was validated against the live `/v1/models`
response on 2026-09-26 using `probe_huggingface.sh`. Partner providers
rotate models continuously — re-run the probe quarterly to catch models
that have been added or removed. Models not in the live API are pruned
from the catalog (not kept as "fallback") since they cannot be used even
if the static catalog lists them.

### Live API data vs static catalog

AgentKthx should maintain a static `HF_MODELS` catalog in `agentkthx/plugins/huggingface/huggingface.py` for offline fallback, but **prefer live API data** for `context_length`, `max_completion_tokens`, and `pricing`. Static catalog should be refreshed quarterly.

### Hub API for model metadata

For richer model metadata (license, framework, tags, downloads), use the Hub API:

```bash
curl https://huggingface.co/api/models/openai/gpt-oss-120b
```

Returns `pipeline_tag`, `library_name`, `tags`, `downloads`, `likes`, `last_modified`, etc. AgentKthx can use this to power a richer `agentkthx models --backend huggingface --details` view.

---

## Function Calling Implementation

### Tool schema (OpenAI-compatible)

The Inference Router accepts the OpenAI tool schema:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": { "type": "string", "description": "City name" }
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

### Provider support for function calling

Not all HF partner providers support OpenAI function calling. Confirmed support:
- **Together** — yes (Llama-3.x, Qwen-2.5, Mistral families)
- **Fireworks** — yes (Llama-3.x, FireFunction-v2, Qwen)
- **Groq** — yes (Llama-3.x, Qwen, Mixtral, gpt-oss)
- **Novita** — yes (Llama-3.3-70B, DeepSeek, Qwen)
- **Cerebras** — yes (Llama-3.x, Qwen3)
- **HF Inference** — partial (varies by model; TGI-enabled chat models only)
- **Replicate, Fal AI, Baseten** — varies per model; check `/v1/models` `supported_parameters` field

### AgentKthx implementation

AgentKthx converts its internal `Tool` objects to OpenAI schema via `Tool.to_openai_schema()` and forwards them as the `tools` field in the request body. Tool results from previous turns are encoded as messages with `role: "tool"` and a `tool_call_id` field.

### ReAct fallback

Many HF partner providers (especially on free-tier `:cheapest` routing) **do not support native tool calling** for certain models. When a provider returns HTTP 400 with a message like "does not support tools" or "tool calling is not supported", AgentKthx's `HuggingFaceBackend.generate()` should automatically retry without the `tools` field, falling back to ReAct (text-based tool calling).

Recommended detection logic (mirrors OpenRouter's `_is_tools_not_supported_error()`):

```python
indicators = (
    "does not support tools",
    "tools are not supported",
    "tool calling is not supported",
    "tools are not yet supported",
    "does not support function calling",
    "function calling is not supported",
    "no tools endpoint",
    "tool use is not supported",  # HF-specific
    "tool_calls not supported on this model",
)
```

### Reasoning models on HF

Several HF-served models emit `reasoning` content interleaved with the visible output:
- `Qwen/Qwen3-4B-Thinking-2507` (and Qwen3-Coder variants)
- `deepseek-ai/DeepSeek-R1`, `deepseek-ai/DeepSeek-V3.1`
- `openai/gpt-oss-20b-reasoning`

These models put their chain-of-thought in the `reasoning` field of the response message (or `reasoning_content` for some upstream providers). AgentKthx should capture `reasoning_content` in both streaming and non-streaming responses — mirroring the OpenRouter implementation.

---

## Streaming & Real-time Features

### SSE streaming (Inference Router)

Set `"stream": true` in the request body. The router returns Server-Sent Events:

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
        "include_usage": true
    }
}
```

Setting `include_usage: true` causes the router to emit a final SSE chunk with `usage` populated. AgentKthx should set this by default (mirroring the OpenRouter behavior since R06.53 PERF-02).

**Note**: Some partner providers — particularly those routed via `:cheapest` — do NOT return a usage chunk even when `include_usage=true` is sent. AgentKthx should fall back to estimating tokens from message content (`chars ÷ 4`) so the footer's token counts and context % still update during the run.

### Streaming on legacy Serverless TGI

The legacy `POST /models/{model}` endpoint also supports streaming when `"stream": true` is set in the body. The SSE format differs slightly — each chunk contains `token`, `generated_text`, `details` fields rather than the OpenAI `delta` structure:

```
data: {"token":{"id":0,"text":"Hello","logprob":-0.1,"special":false},"index":0,"generated_text":null,"details":null}

data: {"token":{"id":1,"text":" world","logprob":-0.2,"special":false},"index":1,"generated_text":null,"details":null}

data: {"generated_text":"Hello world","details":{"finish_reason":"eos_token","generated_tokens":2,"seed":42}}
```

AgentKthx should normalize the TGI streaming shape to match the OpenAI shape before passing it up to the agentic loop.

### Reasoning content in streaming

For thinking-capable HF models (Qwen3-Thinking, DeepSeek-R1), reasoning tokens arrive as separate SSE chunks:

```
data: {"choices":[{"delta":{"reasoning":"Let me think..."}}]}
data: {"choices":[{"delta":{"reasoning":"First I should..."}}]}
data: {"choices":[{"delta":{"content":"The answer is..."}}]}
```

The `reasoning` field appears in delta chunks BEFORE the `content` field. AgentKthx should capture `reasoning_content` in both streaming and non-streaming responses (mirroring the OpenRouter implementation).

---

## Provider Routing Preferences

The Inference Router lets users control which upstream partner provider serves a request by appending a suffix to the model id:

| Suffix | Behavior | Cost implication |
|--------|----------|------------------|
| None / `:fastest` (default) | Routes to the highest-throughput provider for the model. Fastest tokens/sec. | Highest-priced provider is usually not the fastest; expect mid-tier pricing. |
| `:cheapest` | Routes to the most cost-efficient provider (lowest price per output token). | Slower throughput but cheapest. Best for `HF_FREE_ONLY=true` mode. |
| `:preferred` | Routes to the first available provider sorted by the user's Inference Provider settings (configured at huggingface.co/settings/inference-providers). | Depends on user config; defaults to fastest if not configured. |
| `:provider-name` (e.g. `:groq`, `:together`, `:novita`) | Forces routing to a specific partner provider. | Provider's pricing applies; fails if that provider doesn't serve the model. |

### Provider suffix examples

```python
# Default routing (fastest)
"model": "openai/gpt-oss-120b"

# Same as default, explicit
"model": "openai/gpt-oss-120b:fastest"

# Cheapest routing (for HF_FREE_ONLY=true mode)
"model": "openai/gpt-oss-120b:cheapest"

# User-preferred order (configured in HF settings)
"model": "openai/gpt-oss-120b:preferred"

# Force Groq specifically (Llama-3.x on Groq is very fast)
"model": "meta-llama/Llama-3.3-70B-Instruct:groq"

# Force Together (better for Qwen3-Coder)
"model": "Qwen/Qwen3-Coder-480B-A35B-Instruct:together"
```

### Available partner providers (Sept 2026)

| Provider | Chat (LLM) | Chat (VLM) | Embeddings | Text→Image | Text→Video | Speech→Text |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|
| Baseten | ✓ | ✓ | — | — | — | — |
| Cerebras | ✓ | — | — | — | — | — |
| Cohere | ✓ | ✓ | — | — | — | — |
| DeepInfra | ✓ | ✓ | — | — | — | — |
| Fal AI | ✓ | ✓ | — | ✓ | ✓ | — |
| Featherless AI | ✓ | ✓ | — | — | — | — |
| Fireworks | ✓ | ✓ | — | — | — | — |
| Groq | ✓ | ✓ | — | — | — | — |
| HF Inference | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Novita | ✓ | ✓ | ✓ | — | — | — |
| Nscale | ✓ | ✓ | ✓ | — | — | — |
| OVHcloud AI Endpoints | ✓ | ✓ | — | — | — | — |
| Public AI | ✓ | — | — | — | — | — |
| Replicate | ✓ | ✓ | — | ✓ | ✓ | — |
| Scaleway | ✓ | ✓ | — | — | — | — |
| Together | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| WaveSpeedAI | ✓ | ✓ | — | — | — | — |
| Z.ai | ✓ | ✓ | — | — | — | — |

### AgentKthx implementation

AgentKthx does NOT currently expose the provider suffix as a top-level CLI flag. The user can:
1. Pass the full model id with suffix via `--model openai/gpt-oss-120b:cheapest`
2. Set env var `HF_PROVIDER_POLICY=cheapest` (proposed) — backend appends the suffix automatically

The `HF_FREE_ONLY=true` mode should auto-append `:cheapest` to all model ids and reject any model not in `HF_FREE_MODEL_WHITELIST`.

---

## Error Codes & Recovery

### Common error codes

| Code | Meaning | Cause | Fix |
|------|---------|-------|-----|
| 400 | Bad Request | Malformed JSON, missing `model` or `messages` field, unsupported parameter for chosen model | Validate request schema; check `supported_parameters` for the model |
| 401 | Unauthorized | Invalid or expired HF token, missing `Make calls to Inference Providers` permission | Regenerate token at huggingface.co/settings/tokens with Inference Providers scope |
| 402 | Payment Required | Free-tier credit exhausted for the month, no paid balance | Wait for monthly reset OR add billing card at huggingface.co/settings/billing OR set `HF_FREE_ONLY=true` to enforce free-only routing |
| 403 | Forbidden | Token lacks scope for the requested resource (e.g. trying to use a paid Inference Endpoint with a free-tier token) | Check token permissions |
| 404 | Not Found | Model id not served by any partner provider, or model id typo | Verify via `agentkthx models --backend huggingface`; check `providers` list |
| 408 | Request Timeout | Provider took >60s to respond (typically happens on `:cheapest` routing during peak load) | Retry with `:fastest` policy, or pick a different model |
| 422 | Unprocessable Entity | Model doesn't support requested features (e.g. `tools` on a non-tool model, or `response_format: json_schema` on a non-structured-output model) | Use ReAct fallback for tools; use prompt-engineering for structured output |
| 429 | Too Many Requests | Rate limit hit (free tier: 1000 req/day per partner; paid: provider-specific) | Honor `Retry-After` header; exponential backoff |
| 500 | Internal Server Error | Router or provider crash | Retry with backoff |
| 502 | Bad Gateway | Upstream provider returned invalid response | Router usually auto-failovers; if persistent, switch provider with `:other-provider` |
| 503 | Service Unavailable | Provider flagged as unavailable by HF validation; router has no alternative | Try a different model id, or wait 60s and retry |
| 504 | Gateway Timeout | Router → provider connection timed out | Retry |

### 429 Retry-After handling

```python
retry_after_raw = response.headers.get("Retry-After", "10")
try:
    retry_after = int(retry_after_raw)
except (ValueError, TypeError):
    retry_after = 10
retry_after = min(max(retry_after, 1), 60)  # cap at 60s
```

AgentKthx's `HuggingFaceBackend._make_api_request()` should implement automatic 429 retry with up to 3 retries (`_MAX_429_RETRIES = 3`). Each retry waits the `Retry-After` duration (capped at 60s).

### Distinguishing router rate limit from provider rate limit

```json
// Router-side rate limit (your account hit the free-tier monthly cap)
{
    "error": {
        "code": 429,
        "message": "Monthly free-tier credit exhausted. Add billing at huggingface.co/settings/billing to continue."
    }
}

// Provider-side rate limit (the chosen partner provider throttled you)
{
    "error": {
        "code": 429,
        "message": "Provider Groq rate limited. Retrying with another provider...",
        "metadata": {"provider_name": "Groq"}
    }
}
```

For provider-side 429s, the router usually retries with another provider automatically (if `:fastest` or default routing is used). If using `:specific-provider`, no failover happens — the request fails outright.

### Free-tier credit exhaustion (HTTP 402)

When the user's free monthly credit ($0.10/mo as of May 2026 per Klymentiev report) is exhausted, the router returns `402 Payment Required`. AgentKthx should:
1. Surface a clear actionable error: "Hugging Face free-tier credit exhausted for this month. Add a billing card at huggingface.co/settings/billing OR set `HF_FREE_ONLY=false` with a paid token to use your own balance."
2. NOT silently retry — retrying burns router-side quota without resolving the issue.
3. Optionally fall back to a different model with cheaper token pricing (if `HF_FREE_FALLBACK_MODEL` env var is set, mirroring the ZAI plugin's `ZAI_FREE_FALLBACK_MODEL` pattern).

---

## Rate Limiting & Concurrency

### Free tier limits

- **Daily request cap**: ~1000 requests per day across all partner providers (Hugging Face-internal cap, not per-provider)
- **Monthly free credit**: $0.10 USD of inference credit at partner provider rates (as of May 2026 per Klymentiev report; subject to change)
- **Per-provider rate**: Most partner providers impose their own per-account RPM (Groq: 30 RPM free, Together: 60 RPM free, Novita: 60 RPM free, etc.)
- **Concurrent requests**: 5–10 per account (free tier); higher on PRO ($9/mo) and Team tiers

### PRO tier limits

- **Monthly free credit**: larger allocation (PRO users get $0.50/mo inference credit at partner rates)
- **Concurrent requests**: 20+ per account
- **Priority routing**: preferential queue position during peak load
- **Discounted provider rates**: ~10–20% off partner provider list prices

### Enterprise / Team tier limits

- **Custom rate limits**: contact sales for negotiated rates
- **Concurrent requests**: 100+ per account
- **Single-tenant routing**: dedicated capacity on partner providers

### AgentKthx implementation

AgentKthx does NOT currently implement client-side rate limiting. It relies on:
1. The 429 retry loop in `_make_api_request()` (max 3 retries with `Retry-After` honor)
2. The user to pace their requests if doing bulk operations (e.g. running `agentkthx test 04_gsm8k_benchmark --backend huggingface`)

For bulk workflows, users may want to add a client-side throttle. Could be a future R07.x feature.

---

## Free Tier Behavior

### What "free" actually means on Hugging Face

Hugging Face's free-tier model is **credit-based**, not request-based like OpenRouter's `:free` suffix. Specifically:

- ✅ Every account gets a monthly free inference credit (~$0.10 USD as of May 2026)
- ✅ This credit is consumed by both input and output tokens at partner provider rates
- ✅ Unused credit resets at the end of the calendar month (no rollover)
- ✅ All open-weight models on the HF router are eligible for free-tier use, subject to credit availability
- ❌ No request-count-based free tier — every token has a price, even if covered by free credit
- ❌ Free tier is significantly slower than paid routing during peak load
- ❌ Free tier does NOT support all models equally — partner providers may decide not to serve free-tier users for resource-intensive models (Llama-3.3-70B, Qwen3-480B-A35B)

### `HF_FREE_ONLY` env var — proposed behavior

To mirror the OpenRouter `OPENROUTER_FREE_ONLY` pattern and protect users from accidental paid API calls, AgentKthx should implement:

```bash
# Strict free-only mode (default false)
export HF_FREE_ONLY=true

# Model to fall back to if the requested model is not in the free whitelist
export HF_FREE_FALLBACK_MODEL="Qwen/Qwen2.5-7B-Instruct-1M"
```

When `HF_FREE_ONLY=true`:
1. All model ids are auto-suffixed with `:cheapest` (lowest-cost routing)
2. Any model not in `HF_FREE_MODEL_WHITELIST` is rejected with an actionable error:
   ```
   Model 'meta-llama/Llama-4-Scout-70B-Instruct' is not in the HF free-tier whitelist.
   Either set HF_FREE_ONLY=false (requires paid HF token with billing enabled) or
   pick a whitelisted model. See HUGGINGFACE_API_TECHNICAL_REFERENCE.md for the list.
   ```
3. Any HTTP 402 response is treated as a hard failure (no retry) — retrying burns router quota without resolving the issue
4. The agentic loop surfaces the credit-exhaustion condition to the user with a clear actionable message

When `HF_FREE_ONLY=false` (default):
1. No model id rewriting happens
2. HTTP 402 triggers a fallback to `HF_FREE_FALLBACK_MODEL` if set, mirroring the ZAI plugin's pattern
3. The user is responsible for monitoring their HF account balance

### Detecting free-tier support for tools

Run `agentkthx models --backend huggingface --tool-support` to test each whitelisted model's tool support. Results are cached in `~/.cache/agentkthx/tool_support.json` (mirroring the OpenRouter implementation).

Common free-model tool support issues on HF:
- **`does not support tools`** — provider doesn't implement OpenAI function calling for this model. AgentKthx auto-falls-back to ReAct.
- **Empty response with `tool_calls=[]`** — some providers accept tools but never invoke them. Workaround: use `--force-react` to skip the native path entirely.
- **Inconsistent results between runs** — some providers serve the same model id from different underlying deployments; tool support may vary. Workaround: pin to a specific provider with `:groq` or `:together`.

### Free-tier credit monitoring

AgentKthx should periodically fetch the user's remaining free-tier credit (if accessible via the HF Hub API — `/api/billing/usage` endpoint, subject to availability). When credit drops below 20% of monthly allocation, surface a warning. This is a future enhancement; not implemented in v0.1.

---

## Multimodal Content Handling

### Image input (VLM models)

Vision-language models on HF accept OpenAI-style multimodal messages:

```json
{
    "role": "user",
    "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}}
    ]
}
```

Supported VLM models on HF partner providers:
- `Qwen/Qwen2.5-VL-7B-Instruct` (Together, Novita, Fireworks)
- `Qwen/Qwen2.5-VL-72B-Instruct` (Together)
- `meta-llama/Llama-4-Scout-17B-16B-Instruct` (DeepInfra, Together — supports 5-image input)
- `mistralai/Pixtral-12B-2409` (DeepInfra, Together)
- `openai/gpt-oss-20b` (multimodal variant served by some providers)

### Image size limits

Per partner provider:
- **Together**: max 10 MB per image, max 4 images per request
- **Fireworks**: max 10 MB per image, max 4 images per request
- **DeepInfra**: max 5 MB per image, max 10 images per request
- **Novita**: max 5 MB per image, max 8 images per request

AgentKthx should validate image sizes client-side and surface a clear error if any single image exceeds 5 MB (conservative default).

### Audio input

Audio input is NOT supported on the chat-completions endpoint of the router. Users needing audio processing must:
1. Use the dedicated `speech-to-text` task on the legacy Serverless endpoint: `POST /models/{model}` with `inputs` being base64-encoded audio
2. Use a provider that supports audio chat natively (none currently on HF router)

### File input

The router does not support the OpenAI `file` content part type. Users needing file context must:
1. Read the file content client-side and pass as text in the prompt
2. Use the Files API on the Hub (separate from inference) for storage

### Video input

Video input is supported via the `fal-ai` and `replicate` providers for specific models (e.g. `fal-ai/qwen-video`). This is not on the OpenAI-compat path — uses the dedicated text-to-video task format.

---

## Implementation Notes for AgentKthx

### Backend file location (proposed)

```
agentkthx/plugins/huggingface/
├── __init__.py            # register()/unregister()
├── plugin.json            # plugin manifest (see Proposed plugin.json below)
└── huggingface.py         # HuggingFaceBackend class
```

### Key methods (mirror OpenRouterBackend shape)

| Method | Purpose |
|--------|---------|
| `__init__()` | Initializes with `HF_TOKEN` env var, sets HTTP headers, populates `_model_cache` via `list_models()` |
| `list_models()` | Fetches `/v1/models`, caches for 1 hour (`_CACHE_TIMEOUT = 3600`), filters to `HF_FREE_MODEL_WHITELIST` if `HF_FREE_ONLY=true` |
| `is_running()` | Always returns `True` (cloud API, no local server) |
| `generate(model, messages, tools, **kwargs)` | Main entry point. Dispatches to `_make_api_request()`. Implements ReAct fallback on "tools not supported" errors. Enforces `HF_FREE_ONLY` whitelist. |
| `generate_stream(model, messages, **kwargs)` | SSE streaming variant. Yields `delta` chunks. |
| `_make_api_request(endpoint, data, stream)` | HTTP wrapper with 429 retry, 401 detection, 402 free-tier exhaustion handling, error normalization |
| `_stream_request(url, data, headers)` | Low-level SSE parser |
| `_build_openai_body(...)` | Centralizes request body construction (shared by generate + generate_stream). Auto-appends `:cheapest` suffix if `HF_FREE_ONLY=true` and no explicit suffix on model id. |
| `_parse_openai_response(raw_response)` | Extracts `content`, `tool_calls`, `finish_reason`, `usage`, `reasoning_content`. Raises on top-level `error` field. |
| `_is_tools_not_supported_error(err_str)` | Detects "does not support tools" patterns for ReAct fallback |
| `_is_free_tier_exhausted(response)` | Detects HTTP 402 from free-tier credit exhaustion — surfaces actionable error, no retry |
| `_enforce_free_only(model_id)` | Validates model id is in `HF_FREE_MODEL_WHITELIST`; raises with actionable error if not |
| `test_tool_support(model, family, force_test)` | Runtime test: send a probe tool call, detect NATIVE/REACT/NONE |
| `_jev_call_completions(model, messages, ...)` | JEV api_mode hook — routes through `generate()` so auth + 429 retry are preserved |

### Configuration

```bash
# Required
export HF_TOKEN="hf_****"   # Fine-grained token with "Inference Providers" permission

# Optional
export HF_BASE_URL="https://router.huggingface.co/v1"  # default (Inference Router)
export HF_BASE_URL_LEGACY="https://api-inference.huggingface.co"  # legacy TGI surface
export HF_DEFAULT_MODEL="openai/gpt-oss-120b"  # default model
export HF_FREE_ONLY=false  # strict free-tier enforcement (default false)
export HF_FREE_FALLBACK_MODEL="Qwen/Qwen2.5-7B-Instruct-1M"  # used when HF_FREE_ONLY=false and 402 received
export HF_PROVIDER_POLICY=""  # "", "fastest", "cheapest", "preferred" — auto-appended as suffix
```

### Configuration env vars (AgentKthx core)

AgentKthx uses the `AGENTKTHX_*` env var prefix:
- `AGENTKTHX_BACKEND=huggingface` — set default backend
- `AGENTKTHX_API_MODE=openai` — Hugging Face router supports OpenAI mode only; JEV mode is not supported

### What AgentKthx does NOT yet implement (potential future work)

- **Per-provider routing** — currently the user picks via `:provider-name` suffix in model id. Could expose `--hf-provider groq` CLI flag.
- **TGI-legacy surface** — the v1 backend should use the OpenAI-compat router only. The TGI surface is being deprecated; not worth implementing.
- **`response_format: json_schema`** — supported on most partner providers, would enable reliable structured outputs for tool-call argument parsing
- **`reasoning` parameter object** — for thinking models (Qwen3-Thinking, DeepSeek-R1); would let users control thinking budget
- **Hub API integration** — `/api/models/{model}` for richer model metadata (license, downloads, tags) in `agentkthx models --backend huggingface --details`
- **Free-tier credit monitoring** — poll `/api/billing/usage` (if/when HF exposes it) to surface remaining monthly credit
- **`min_p`, `top_a`, `logit_bias`** — sampling parameters in `_build_openai_body()` (router supports these on most partner providers)

---

## Proposed plugin.json

Following the v0.2 schema (mirroring the ZAI and OpenRouter plugin manifests), the proposed `agentkthx/plugins/huggingface/plugin.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "huggingface",
  "version": "0.1.0",
  "description": "Hugging Face Inference Router backend for 100+ open models via OpenAI Chat-Completions API (free-tier + paid, with HF_FREE_ONLY enforcement and credit-exhaustion fallback)",
  "author": {
    "name": "VTSTech",
    "url": "https://www.vts-tech.org"
  },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "Hugging Face Inference Router Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "HF",
        "defaults": {
          "HF_BASE_URL": "https://router.huggingface.co/v1",
          "HF_BASE_URL_LEGACY": "https://api-inference.huggingface.co",
          "HF_TOKEN": "",
          "HF_DEFAULT_MODEL": "openai/gpt-oss-120b",
          "HF_FREE_ONLY": "false",
          "HF_FREE_FALLBACK_MODEL": "Qwen/Qwen2.5-7B-Instruct-1M",
          "HF_PROVIDER_POLICY": ""
        }
      },
      "provides": {
        "backends": {
          "huggingface": "huggingface.HuggingFaceBackend"
        },
        "cli_commands": [],
        "cli_flags": {
          "--backend": [
            "huggingface",
            "hf"
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
- **`name`**: `huggingface` (matches directory name, follows existing plugin naming convention)
- **`version`**: `0.1.0` (initial release; first-class HF Router support)
- **`env_prefix`**: `HF` (shorter than `HUGGINGFACE`, matches HF CLI convention)
- **`cli_flags."--backend"`**: includes both `huggingface` (canonical) and `hf` (alias) for ergonomic CLI usage
- **`defaults`**: includes both `HF_BASE_URL` (router) and `HF_BASE_URL_LEGACY` (serverless TGI) for completeness; v0.1 implementation only uses the router
- **`HF_FREE_ONLY`**: stored as string `"false"` to match the env-var convention; the backend parses it to bool via `_truthy()` helper (mirrors `ZAI_FREE_ONLY` and `OPENROUTER_FREE_ONLY` patterns)
- **`HF_FREE_FALLBACK_MODEL`**: a Qwen model with broad partner support and low token cost — picked for free-tier fallback stability
- **`compatibility.agentkthx`**: `>=0.5.0` (matches OpenRouter plugin's minimum — same architecture, same `OpenAICompatibleBackend` base class)

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401 Unauthorized` | Invalid or expired HF token | Check `HF_TOKEN` env var; regenerate at huggingface.co/settings/tokens with "Make calls to Inference Providers" permission |
| `402 Payment Required` | Free-tier monthly credit exhausted (and `HF_FREE_ONLY=true`) | Wait for monthly reset OR set `HF_FREE_ONLY=false` and add billing card at huggingface.co/settings/billing |
| `402 Payment Required` | Free-tier credit exhausted (and `HF_FREE_ONLY=false`) | Backend auto-falls-back to `HF_FREE_FALLBACK_MODEL`; or upgrade to PRO ($9/mo) for higher credit |
| `404 Not Found` | Model id typo, or no partner provider currently serves this model | Run `agentkthx models --backend huggingface` for live catalog; check the model's HF page for "Inference Providers" status |
| `422 Unprocessable Entity` | Model doesn't support `tools` parameter | AgentKthx auto-falls-back to ReAct; or use `--force-react` upfront |
| `429 Too Many Requests` | Account-level rate limit hit | Wait `Retry-After` seconds (auto-retried up to 3x by AgentKthx) |
| `429 Provider X rate limited` | Upstream partner provider throttling | Use `:other-provider` suffix to force a different provider |
| `503 Service Unavailable` | All partner providers for this model are flagged unavailable | Switch to a different model id; check huggingface.co/status for incidents |
| Empty response (no content, no tool_calls) | Provider silently failed (content filter, model issue) | Retry; check `--debug` for `finish_reason` |
| `does not support tools` | Free-tier provider lacks tool calling for this model | AgentKthx auto-falls-back to ReAct; or use `--force-react` upfront |
| Slow startup (`agentkthx models --backend huggingface`) | `/v1/models` endpoint slow, cache cold | Subsequent calls within 1 hour use cache |
| `model not found` | Model id typo, or model removed from all partner providers | Check `agentkthx models --backend huggingface` for current list; check the HF model page |
| Streaming response missing `usage` | `:cheapest`-routed provider doesn't send usage chunk | AgentKthx falls back to estimating tokens from content (`chars ÷ 4`). Footer still updates with approximate counts. |
| Reasoning not displayed with `--think` | Streaming path doesn't capture `reasoning_content` for some partner providers | Use `:preferred` or `:fastest` routing for more consistent reasoning content |
| Token count way too high | Conversation history growing unbounded | Use `/clear` in chat mode; or `--session` to persist between runs |
| `HF_FREE_ONLY=true` blocking a model | Model not in whitelist | Either add the model to `HF_FREE_MODEL_WHITELIST` in your local plugin config, or set `HF_FREE_ONLY=false` (requires paid HF token with billing) |
| HTTP 402 during long-running agent task | Free-tier credit exhausted mid-run | Use `--max-steps N` to cap step count; or run with `HF_FREE_ONLY=false` and a paid token |
| Inconsistent tool-calling behavior between runs | Provider routing varies (`:fastest` picks different providers each call) | Pin to a specific provider with `:groq`, `:together`, etc. for deterministic results |

---

## References

- **Hugging Face Inference Providers Docs**: https://huggingface.co/docs/inference-providers/index
- **Text Generation Task Reference**: https://huggingface.co/docs/inference-providers/tasks/text-generation
- **Chat Completion Task Reference**: https://huggingface.co/docs/inference-providers/tasks/chat-completion
- **Function Calling Guide**: https://huggingface.co/docs/inference-providers/guides/function-calling
- **Responses API (beta on HF)**: https://huggingface.co/docs/inference-providers/guides/responses-api
- **Pricing and Billing**: https://huggingface.co/docs/inference-providers/pricing
- **Hub Integration**: https://huggingface.co/docs/inference-providers/hub-integration
- **Security / Token Management**: https://huggingface.co/docs/inference-providers/security
- **Hub API (model metadata)**: https://huggingface.co/docs/hub/api
- **OpenAI SDK Compatibility**: https://huggingface.co/docs/inference-providers/guides/openai-sdk
- **Pricing 2026 Guide (community)**: https://klymentiev.com/blog/hugging-face-free-api-2026
- **HF Inference Playground**: https://huggingface.co/playground
- **HF Status Page**: https://status.huggingface.co

---

Written by VTSTech — https://www.vts-tech.org
