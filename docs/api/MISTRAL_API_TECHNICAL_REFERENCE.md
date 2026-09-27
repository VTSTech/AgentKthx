# Mistral API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://docs.mistral.ai/api (OpenAPI spec: https://docs.mistral.ai/openapi.yaml)
> **Primary focus**: Native Chat Completions API (`https://api.mistral.ai/v1/`)
> **Last Updated**: 2026-09-26
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
10. [Sampling Parameters & Prompt Caching](#sampling-parameters--prompt-caching)
11. [Batch, Files & OCR Services](#batch-files--ocr-services)
12. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
13. [Troubleshooting Matrix](#troubleshooting-matrix)
14. [Appendix: OpenAI Wire-Format Deltas](#appendix-openai-wire-format-deltas)
15. [Appendix: Model Lifecycle & Deprecations](#appendix-model-lifecycle--deprecations)
16. [Appendix: Pricing Snapshot (Sep 2026)](#appendix-pricing-snapshot-sep-2026)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — La Plateforme public API (primary AgentKthx surface)
BASE_URL = "https://api.mistral.ai/v1"

# Codestral dedicated endpoint (FIM completion, separate key in some plans)
BASE_URL_CODESTRAL = "https://codestral.mistral.ai/v1"

# Regional inference — the API is served from EU data centers by default.
# Enterprise deployments may pin a region; the base URL does not change,
# routing is handled server-side (see docs.mistral.ai/inference/regional-inference).
```

Unlike Gemini, Mistral has **no separate OpenAI-compat endpoint** — the native
`/v1/chat/completions` path IS the OpenAI-style surface. The wire format is
close to OpenAI's but has deliberate deltas (see
[Appendix: OpenAI Wire-Format Deltas](#appendix-openai-wire-format-deltas)).

### API Endpoints (chat backend relevant)

```python
# Core chat — the endpoint AgentKthx uses as its primary surface
CHAT_COMPLETIONS = "/chat/completions"        # POST

# Agents wrapper around chat (server-side tool orchestration, Beta)
AGENTS_COMPLETIONS = "/agents/completions"    # POST

# Model discovery — used by AgentKthx for capability sniffing
MODELS_LIST     = "/models"                   # GET  (list all available models)
MODELS_RETRIEVE = "/models/{model_id}"        # GET  (single model card)
MODELS_DELETE   = "/models/{model_id}"        # DELETE (fine-tuned models only)

# Auxiliary (available, not required for the chat backend)
FIM_COMPLETIONS = "/fim/completions"          # POST  (Codestral fill-in-the-middle)
EMBEDDINGS      = "/embeddings"               # POST
FILES           = "/files"                    # POST/GET (upload, list)
FILE_CONTENT    = "/files/{file_id}/content"  # GET  (download)
BATCH_JOBS      = "/batch/jobs"               # POST/GET
BATCH_JOB_ID    = "/batch/jobs/{job_id}"      # GET/DELETE
BATCH_CANCEL    = "/batch/jobs/{job_id}/cancel"  # POST
OCR             = "/ocr"                      # POST (document understanding)
MODERATIONS     = "/moderations"              # POST (standalone classifier)
CHAT_MODERATIONS = "/chat/moderations"        # POST (moderation on chat-shaped input)
CLASSIFICATIONS = "/classifications"          # POST
AUDIO_TRANSCRIPTIONS = "/audio/transcriptions"  # POST
AUDIO_SPEECH    = "/audio/speech"             # POST (TTS)
CONVERSATIONS   = "/conversations"            # POST/GET (Beta — stateful agents)
AGENTS          = "/agents"                   # POST/GET (Beta — persisted agent defs)
LIBRARIES       = "/libraries"                # POST/GET (Beta — RAG document libraries)
```

The full machine-readable surface (including Beta admin/observability/workflow
paths) is published as an OpenAPI 3.1 spec at `docs.mistral.ai/openapi.yaml`.
AgentKthx only needs the subset above; everything else is listed for parity
awareness.

### Authentication Headers

Mistral uses a single bearer-token scheme — the simplest of all AgentKthx
cloud backends:

```python
headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",          # use "text/event-stream" for streaming
    "Authorization": "Bearer MISTRAL_API_KEY",
}
```

- There is exactly **one** auth style. No `x-api-key`, no query-param key, no
  OAuth. If `Authorization` is missing or malformed you get `401 Unauthorized`.
- API keys are created in **Studio → API keys**. The key is displayed **only
  once** at creation; losing it means generating a new one.
- `MISTRAL_API_KEY` is the canonical env var (matching the official Python and
  TypeScript SDKs). AgentKthx should support `MISTRAL_BASE_URL` override for
  self-hosted `mistral-inference` gateways that expose the same `/v1` surface.
- Codestral has a **separate dedicated endpoint** (`codestral.mistral.ai`) and
  in some plans a separate key — relevant only if AgentKthx later wires FIM.

### Request Format Requirements

- **Content-Type**: `application/json` (audio transcription uses
  `multipart/form-data`)
- **Character Encoding**: UTF-8
- **HTTPS only** — plaintext HTTP is not served
- **Timeout**: 120 s recommended for chat completions (`BackendConfig.timeout`);
  streaming connections are cut server-side after **10 minutes of inactivity**
- **Request size**: no published hard cap for the JSON body, but the effective
  ceiling is the model context window (256k tokens on current models) —
  requests exceeding it fail with `400` (see [Known Limitations](#error-codes--recovery))
- **Idempotency**: no idempotency-key header; retries must be implemented
  client-side with backoff (Mistral's official SDKs ship with built-in retry —
  mirror their policy: exponential backoff + jitter, max 5 attempts)

---

## Request/Response Structure

### Complete Request Schema (Chat Completions)

Field-for-field from the OpenAPI spec (`ChatCompletionRequest`). Required:
`model`, `messages`. Everything else is optional:

```json
{
    "model": "mistral-medium-latest",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "string|array<ContentChunk>"}
    ],
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 8192,
    "stream": false,
    "stop": ["\n\n###"],
    "n": 1,
    "random_seed": 42,
    "response_format": {"type": "text|json_object|json_schema"},
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "strict": false,
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        }
    ],
    "tool_choice": "auto|none|any|required|{\"type\":\"function\",\"function\":{\"name\":\"X\"}}",
    "parallel_tool_calls": true,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "safe_prompt": false,
    "prediction": {"type": "content", "content": "expected output string"},
    "prompt_cache_key": "conversation-uuid",
    "prompt_mode": "reasoning",
    "reasoning_effort": "none|minimal|low|medium|high|xhigh",
    "service_tier": "auto|standard_only",
    "metadata": {"any": "map<string, any> — free-form, echoed in usage reporting"},
    "guardrails": []
}
```

Field notes that matter for a correct backend implementation:

| Field | Type / Values | AgentKthx notes |
|-------|---------------|-----------------|
| `max_tokens` | integer | **Not** `max_completion_tokens`. Shared input+output budget against `max_context_length` |
| `random_seed` | integer | OpenAI's `seed` is renamed. Deterministic output across calls when set |
| `safe_prompt` | bool, default `false` | Mistral-specific: injects a safety system prompt before all conversations. Slightly changes output style and consumes a few tokens |
| `prompt_cache_key` | string | Prompt caching hint — reuse the same key for shared prefixes (multi-turn, fixed system prompt). Cached input tokens are billed at **10%** of the standard input price |
| `prompt_mode` | `"reasoning"` | Only relevant to reasoning models; high-level intent for the system prompt assembly |
| `reasoning_effort` | `none\|minimal\|low\|medium\|high\|xhigh` | Note `xhigh` — no other provider has it (see [Thinking & Reasoning](#thinking--reasoning-configuration)) |
| `service_tier` | `auto\|standard_only` | `"auto"` allows Priority Tier routing if the org has an entitlement; `"standard_only"` opts out |
| `prediction` | object | Expected-completion optimization (like OpenAI predicted outputs) — for tasks with known suffix, e.g. edits |
| `parallel_tool_calls` | bool, default `true` | `false` forces single tool call per turn |
| `stop` | string \| array[string] | Stop sequence(s); generation halts when one is detected |
| `n` | integer | Number of completions; input tokens billed once. Not supported by `mistral-large-2512` |
| `metadata` | map | Free-form key/value; useful to correlate requests in the Studio usage dashboard |

Conspicuously **absent** vs OpenAI: `seed`, `user`, `logprobs`, `top_k`,
`max_completion_tokens`, `reasoning` (as a request object). Don't forward those
from AgentKthx kwargs — they either break validation (`422`) or are ignored.

### Message Types

```json
// SystemMessage
{"role": "system", "content": "string"}

// UserMessage — content may be a plain string or an array of ContentChunk
// (text | image_url | document_url | input_audio | file | reference | thinking)
{"role": "user", "content": "What is in this image?"}

// AssistantMessage — as returned by the model, or user-authored for prefill
{
    "role": "assistant",
    "content": "string|null|array<ContentChunk>",
    "tool_calls": [
        {
            "id": "D681PevKs",
            "type": "function",
            "function": {"name": "get_weather", "arguments": "{\"location\": \"Toronto\"}"},
            "index": 0
        }
    ],
    "prefix": false
}

// ToolMessage — one per executed tool call
{"role": "tool", "content": "result string", "tool_call_id": "D681PevKs", "name": "get_weather"}
```

Mistral-specific message details:

- **`AssistantMessage.prefix`** — Mistral supports **prefill**: set `prefix:
  true` on the last assistant message to force the model to continue from that
  text. This is the cleanest way to implement "start the answer with ..." and
  AgentKthx should expose it via a backend kwarg rather than prompt hacks.
- **`ToolCall.id`** — short opaque strings (`D681PevKs`), **no `call_` prefix**.
  The schema default is the literal string `"null"` — treat empty/missing IDs
  as falsy, but always echo back exactly what the model sent.
- **`FunctionCall.arguments`** — the spec types it as `string | object`.
  Non-streaming responses normally return a JSON-encoded **string** (like
  OpenAI), but tolerate both when parsing. Never `json.loads()` unconditionally.
- **`ToolMessage.name`** — optional function-name echo. Harmless to include,
  and some server-side validations are happier with it present.
- **`ThinkChunk`** — reasoning models can return `content` as a chunk array
  containing `{type: "thinking", thinking: [...], signature: ...}` blocks
  (see [Thinking & Reasoning Configuration](#thinking--reasoning-configuration)).

### Response Schema (non-streaming)

```json
{
    "id": "cmpl-e5cc70bb28c444948073e77776eb30ef",
    "object": "chat.completion",
    "created": 1702256327,
    "model": "mistral-medium-latest",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "tool_calls": [
                    {
                        "id": "D681PevKs",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{\"location\": \"Toronto\"}"},
                        "index": 0
                    }
                ]
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 50,
        "total_tokens": 70,
        "prompt_audio_seconds": null,
        "service_tier": "standard"
    }
}
```

### `finish_reason` Semantics (Mistral-specific)

Exact enum from the OpenAPI spec — non-streaming:
`stop | length | model_length | error | tool_calls`

| `finish_reason` | Meaning | AgentKthx handling |
|-----------------|---------|--------------------|
| `stop` | Natural end of generation or `stop` sequence hit | Finalize assistant turn |
| `tool_calls` | Model emitted `tool_calls` | Execute tools, append `role: "tool"` messages, loop |
| `length` | `max_tokens` output cap reached | Surface truncation warning; do **not** retry blindly |
| `model_length` | **Context window exhausted** (input + output > `max_context_length`) | Mistral's distinct code for "your prompt is too big" — truncate/compact history, then retry. This is the value to match in the shared context-recovery path |
| `error` | Server-side generation error | Inspect the error payload; safe to retry with backoff |

Streaming responses use a *narrower* enum: `stop | length | error |
tool_calls | null` — `model_length` never appears mid-stream (the request
would have failed with `400` before the first token).

### Model Discovery (`/v1/models`)

`GET /v1/models` returns `{"object": "list", "data": [<BaseModelCard|FTModelCard>]}`.
The card is far richer than OpenAI's and should drive AgentKthx auto-config:

```json
{
    "id": "mistral-medium-latest",
    "object": "model",
    "created": 1710000000,
    "owned_by": "mistralai",
    "capabilities": {
        "completion_chat": true,
        "function_calling": true,
        "reasoning": true,
        "completion_fim": false,
        "fine_tuning": false,
        "vision": true,
        "ocr": false,
        "classification": false,
        "moderation": false,
        "audio": false,
        "audio_transcription": false,
        "audio_transcription_realtime": false
    },
    "name": "Mistral Medium 3.5",
    "description": "Frontier-class multimodal model optimized for agentic and coding use cases.",
    "max_context_length": 262144,
    "aliases": ["mistral-medium-latest", "mistral-medium-3"],
    "deprecation": null,
    "deprecation_replacement_model": null,
    "default_model_temperature": 0.7,
    "type": "base",
    "billing_model_name": "mistral-medium-2508"
}
```

AgentKthx should prefer `capabilities.function_calling`,
`capabilities.completion_chat`, and `max_context_length` from the card over
any hardcoded table, and fall back to the table only when the model endpoint
is unreachable (offline gateways, self-hosted).

---

## Model Family Specifications

### Current text/multimodal models (Sep 2026)

```python
MODEL_CONFIGS = {
    # === Mistral Medium family — frontier multimodal, agentic + coding ===
    "mistral-medium-latest": {
        "context_length": 262_144,             # 256k tokens
        "default_temperature": 0.7,
        "supports_function_calling": True,
        "supports_parallel_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_prefix_prefill": True,
        "license": "Modified MIT",             # commercial-friendly, not pure Apache
        "family": "mistral-medium",
        "version": "26.04",
        "snapshot_id": "mistral-medium-3-5",
    },
    # === Mistral Small family — efficient hybrid (instruct + reasoning + code) ===
    "mistral-small-latest": {
        "context_length": 262_144,
        "supports_function_calling": True,
        "supports_response_format_json_schema": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_reasoning": True,            # hybrid — single model, no effort ladder
        "license": "Apache 2.0",
        "family": "mistral-small",
        "version": "26.03",
        "snapshot_id": "mistral-small-4",
    },
    # === Mistral Large family — open-weight general-purpose multimodal ===
    "mistral-large-latest": {
        "context_length": 262_144,
        "supports_function_calling": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_reasoning": True,
        "supports_n_completions": False,       # mistral-large-2512: n > 1 rejected
        "license": "Apache 2.0",
        "family": "mistral-large",
        "version": "25.12",
        "snapshot_id": "mistral-large-3",
    },
    # === Ministral 3 — small on-device-class tier (text + vision) ===
    "ministral-14b-latest": {
        "context_length": 262_144, "supports_function_calling": True,
        "supports_streaming": True, "supports_vision": True,
        "license": "Apache 2.0", "family": "ministral", "version": "25.12",
    },
    "ministral-8b-latest": {   # mirror 14B
        "context_length": 262_144, "supports_function_calling": True,
        "supports_streaming": True, "supports_vision": True,
        "license": "Apache 2.0", "family": "ministral", "version": "25.12",
    },
    "ministral-3b-latest": {
        "context_length": 262_144, "supports_function_calling": True,
        "supports_streaming": True, "supports_vision": True,
        "license": "Apache 2.0", "family": "ministral", "version": "25.12",
    },
    # === Devstral 2 — SWE-agent coding specialist ===
    "devstral-latest": {
        "context_length": 262_144,
        "supports_function_calling": True,
        "supports_streaming": True,
        "license": "Apache 2.0",
        "family": "devstral", "version": "25.12+",
        "note": "Tool-calling-first tuning; best AgentKthx backend model per token",
    },
    # === Magistral — explicit reasoning ladder ===
    "magistral-medium-latest": {
        "context_length": 131_072,             # 128k — smaller than sibling Medium 3.5
        "supports_function_calling": True,
        "supports_streaming": True,
        "supports_reasoning": True,            # prompt_mode="reasoning", ThinkChunk output
        "family": "magistral", "version": "1.2 (25.09)",
    },
    # === Codestral — FIM + chat code completion ===
    "codestral-latest": {
        "context_length": 131_072,             # 128k
        "supports_function_calling": True,
        "supports_streaming": True,
        "supports_fim": True,                  # dedicated /fim/completions endpoint
        "family": "codestral", "version": "25.08",
    },
    # === Third-party hosted models (parity awareness) ===
    "zai-glm-5.3": {
        "context_length": 1_048_576,           # 1M — largest context on the platform
        "family": "third-party", "note": "Open-weight Z.ai model served by Mistral",
    },
}
```

### Model Naming Convention & Aliases

Per the model lifecycle policy, GA model identifiers follow
`model-name-major-minor` (e.g. `mistral-medium-3-5`). Older date-based IDs
(`mistral-medium-2508`) remain on legacy cards and as billing anchors
(`billing_model_name`). GA models resolve three alias forms:

| Alias form | Example | Resolves to |
|------------|---------|-------------|
| `model-name-latest` | `mistral-medium-latest` | Latest GA version **across generations** — moving alias |
| `model-name-major` | `mistral-medium-3` | Latest minor within that major version — moving alias |
| full snapshot | `mistral-medium-3-5` | Pinned exact version — for reproducibility |

Labs models carry a `labs-` prefix (`labs-mistral-small-creative`), are
**free of charge**, allow silent updates at any time, and don't support data
collection opt-out. AgentKthx should never pin a labs model as a default.

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect Mistral model capabilities from name.

    Naming convention: mistral-<tier>[-<desc>], plus specialist prefixes.
    Prefer GET /v1/models cards when available; this static table is the
    offline fallback.
    """
    m = model_name.lower()
    if m.startswith("mistral-medium"):
        return {
            "family": "mistral-medium",
            "context_length": 262_144,
            "supports_function_calling": True,
            "supports_vision": True,
            "supports_reasoning": True,
            "default_temperature": 0.7,
        }
    if m.startswith("mistral-large"):
        return {
            "family": "mistral-large",
            "context_length": 262_144,
            "supports_function_calling": True,
            "supports_vision": True,
            "supports_n_completions": False,
        }
    if m.startswith("mistral-small"):
        return {
            "family": "mistral-small",
            "context_length": 262_144,
            "supports_function_calling": True,
            "supports_reasoning": True,
        }
    if m.startswith("ministral"):
        return {"family": "ministral", "context_length": 262_144,
                "supports_function_calling": True, "supports_vision": True}
    if m.startswith(("devstral", "codestral")):
        return {"family": "code", "context_length": 131_072,
                "supports_function_calling": True, "supports_fim": True}
    if m.startswith("magistral"):
        return {"family": "magistral", "context_length": 131_072,
                "supports_function_calling": True, "supports_reasoning": True}
    if m.startswith(("voxtral", "mistral-ocr")):
        return {"family": "specialist", "is_chat_model": False}
    if m.startswith("labs-"):
        return {"family": "labs", "production_ready": False}
    return {"family": "unknown", "supports_function_calling": True}
```

**Note for AgentKthx**: `agentkthx/core/model_family_config.py` already lists
`mistral`/`mixtral` as *local* (Ollama-served) families for context defaults.
The cloud backend detection above is a separate namespace — prefix match on
the full cloud ID (`mistral-small-latest`, `devstral-2512`) and keep the local
path untouched. `agentkthx/backends/ollama_registry.py` continues to own the
local side.

---

## Function Calling Implementation

### Tool Schema Requirements

Mistral's tool schema matches OpenAI's with one addition — a `strict` flag on
the function object (default `false`):

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "strict": false,
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

AgentKthx tools that implement `to_openai_schema()` pass through unchanged —
wrap them in `{"type": "function", "function": <schema>}` if the schema helper
returns the bare function object.

### Server-Side Tool Types

Beyond user-defined `function` tools, `tools[]` accepts Mistral-hosted tools.
These are primarily exercised through the **Agents/Conversations** APIs, but
the type literals are valid on chat completions:

| Type literal | Behavior | AgentKthx relevance |
|--------------|----------|---------------------|
| `function` | Standard user-defined function | Primary path — fully supported |
| `web_search` / `web_search_premium` | Server-side web search (per-call billing) | Defer; AgentKthx does search client-side |
| `code_interpreter` | Server-side Python sandbox | Defer; AgentKthx has its own sandboxed REPL |
| `image_generation` | Server-side image generation | Out of scope for chat backend |
| `document_library` | RAG over Libraries (`library_ids` required) | Out of scope for chat backend |
| `custom_connector` | Connector/MCP tool surfaces | Out of scope for chat backend |

Do **not** pass hosted-tool literals from the standard chat backend — the
per-call billing and response shapes differ from `function` tool calls, and
AgentKthx's tool executor can't route them.

### Tool Flow Implementation

```python
import json
from typing import Any

class MistralToolHandler:
    """Convert AgentKthx tools to Mistral's schema and parse tool_calls."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def convert_to_mistral_tools(self, agent_tools: list) -> list:
        mistral_tools = []
        for tool in agent_tools:
            if hasattr(tool, "to_openai_schema"):
                mistral_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema(),
                })
            else:
                mistral_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                })
        return mistral_tools

    def handle_tool_calls(self, response: dict) -> list[dict]:
        """Extract tool_calls from a non-streaming response."""
        tool_calls = []
        choices = response.get("choices") or []
        if not choices:
            return tool_calls
        msg = choices[0].get("message") or {}
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            # Mistral types arguments as string | object — accept both.
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw) if args_raw.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
            else:
                args = args_raw or {}
            tool_calls.append({
                "id": tc.get("id") or f"mistral_tc_{i}",  # schema default id is "null"
                "type": tc.get("type", "function"),
                "function": {"name": fn.get("name", ""), "arguments": args},
            })
        return tool_calls

    def build_tool_result_message(
        self, tool_call_id: str, result: Any, *, name: str | None = None
    ) -> dict:
        body = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        msg = {"role": "tool", "tool_call_id": tool_call_id, "content": body}
        if name:
            msg["name"] = name
        return msg
```

### Parallel Function Calling

`parallel_tool_calls` defaults to `true`. When the model emits multiple calls
in one assistant message, AgentKthx's loop MUST:

1. Execute all tool calls (concurrently where safe).
2. Append **one** `role: "tool"` message per tool call — never merge.
3. Preserve `tool_call_id` pairing; Mistral may emit parallel calls in **any
   order** (documented limitation) — match by ID, not by position.
4. Send the full array of tool messages in the next `/chat/completions`.

Hard caps from the known-limitations page:

- **Maximum 128 tools per request.** More tools → `422` validation error.
- Tool descriptions are **counted against the context window** — long
  descriptions starve the message budget. Keep AgentKthx tool docs terse.

### `tool_choice` Semantics

| Value | Behavior |
|-------|----------|
| `"auto"` (default) | Model decides whether to call tools |
| `"any"` | **Mistral's forced-call value** — model MUST call a tool, but WHICH tool is not guaranteed |
| `"required"` | Accepted alias of `"any"` in the current spec |
| `"none"` | Model must NOT call tools (text output only) |
| `{"type": "function", "function": {"name": "X"}}` | Force a specific function |

Unlike OpenAI, `"any"` does not pin the function — only the explicit
`{"type": "function", ...}` object does. AgentKthx's `tool_choice="required"`
kwarg should map to `"any"` for maximum compatibility with older
self-hosted gateways that predate the alias.

---

## Streaming & Real-time Features

### Streaming Response Format (SSE)

Enable with `"stream": true`. The response is `text/event-stream`; events are
OpenAI-shaped chunk objects terminated by `data: [DONE]`:

```
data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{"content":"Hello"}},"finish_reason":null]}

data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{"content":", how can I help?"},"finish_reason":null}]}

data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"D681PevKs","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}

data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"location\": \"Toronto\"}"}}]},"finish_reason":null}]}

data: {"id":"cmpl-4e0f2a9b","object":"chat.completion.chunk","created":1700000000,"model":"mistral-medium-latest","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":45,"completion_tokens":12,"total_tokens":57}}

data: [DONE]
```

Streaming quirks that WILL bite an untested integration:

1. **Usage requires an explicit opt-in.** Send
   `"stream_options": {"include_usage": true}` or the stream never carries a
   `usage` object. (Mistral's docs call this out as mandatory; some gateways
   reject unknown `stream_options` — drop the key when `base_url` is
   overridden to a self-hosted gateway and usage is not needed.)
2. **10-minute inactivity timeout.** A stream that stalls (long tool
   execution server-side, network idle) is cut after 10 minutes. Long agentic
   turns should rely on their own keepalive requests, not a single stream.
3. **Chunked transfer encoding.** Some HTTP client libraries buffer the
   whole response body. AgentKthx's `_iter_sse_lines` must read incrementally
   and split on `\n\n` event boundaries.
4. **Stream `finish_reason` enum is narrower** — `stop | length | error |
   tool_calls | null`. Never expect `model_length` mid-stream.
5. **Reasoning deltas.** For reasoning models, thinking content can arrive as
   `ContentChunk` deltas with `type: "thinking"` — render to a collapsible
   panel, never as the main answer text.

### Streaming Implementation

```python
import json
import urllib.request
from typing import Generator

class MistralStreamHandler:
    def __init__(self, api_key: str, base_url: str = "https://api.mistral.ai/v1"):
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

Tool calls arrive as deltas across chunks and must be accumulated by `index`:

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
            idx = tc_delta.get("index", 0)
            slot = accumulated.setdefault(
                idx,
                {"id": None, "type": "function",
                 "function": {"name": "", "arguments": ""}},
            )
            if tc_delta.get("id"):
                slot["id"] = tc_delta["id"]
            if tc_delta.get("type"):
                slot["type"] = tc_delta["type"]
            fn = tc_delta.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                # arguments may be a JSON string fragment OR an object
                if isinstance(fn["arguments"], str):
                    slot["function"]["arguments"] += fn["arguments"]
                else:
                    slot["function"]["arguments"] = json.dumps(fn["arguments"])
    return list(accumulated.values())
```

### Real-time APIs (parity awareness)

Mistral's real-time surface is **audio**: `voxtral-mini-transcribe-realtime`
for live transcription (WebSocket-based Sessions API:
`POST /v1/client/sessions`), plus `Events` streaming endpoints. These are not
chat backends and are out of scope for AgentKthx's core loop; revisit only if
voice I/O is added.

---

## Error Codes & Recovery

### Error Response Structure

All errors share a single envelope — note `object: "error"` (Mistral-specific,
vs OpenAI's bare `error` wrapper):

```json
{
    "object": "error",
    "message": "A human-readable description of the error.",
    "type": "invalid_request_error",
    "param": "model",
    "code": "unknown_model"
}
```

| Field | Description |
|-------|-------------|
| `message` | Human-readable description — safe to surface to users |
| `type` | Error category: `invalid_request_error`, `authentication_error`, `rate_limit_error`, `server_error` |
| `param` | The request parameter that caused the error, when applicable |
| `code` | Machine-readable code, when applicable (e.g. `unknown_model`) |

Parse defensively: `type`/`param`/`code` are conditional. Always read
`object == "error"` (or just the HTTP status) before sniffing fields.

### Full Error Code Table

| HTTP | `error.type` | Typical cause | Recoverable | Retry strategy |
|------|--------------|---------------|-------------|----------------|
| 400 | `invalid_request_error` | Malformed JSON, unknown field, context window exceeded | No | Fix request. If context overflow, truncate/compact history and retry |
| 401 | `authentication_error` | Missing/invalid/expired API key | No | Prompt user to re-export `MISTRAL_API_KEY` |
| 403 | *(varies)* | Key lacks access to the model, org suspended (spend limit), region restriction | Rarely | Surface actionable message; don't hammer retries |
| 404 | `invalid_request_error` | Unknown model ID (`code: "unknown_model"`), deleted fine-tuned model | No | Fall back to a known-good model (`mistral-small-latest`) |
| 422 | `invalid_request_error` | Validation error — wrong types, >128 tools, invalid `reasoning_effort` value | No | Fix request and retry manually |
| 429 | `rate_limit_error` | RPS or TPM exceeded (per organization) | Yes | Honor `Retry-After`; exponential backoff with jitter |
| 500 | `server_error` | Unexpected internal error | Yes | Retry with exponential backoff |
| 502 | `server_error` | Bad gateway (upstream blip) | Yes | Retry with backoff |
| 503 | `server_error` | Service unavailable / capacity | Yes | Retry with longer backoff (>=30 s) |
| 504 | `server_error` | Gateway timeout | Yes | Retry with backoff |

Context-overflow behavior worth restating: requests exceeding
`max_context_length` return **400** (documented), and token counts include
**input + output** — so `max_tokens` must leave headroom. This mirrors the
shared 400-recovery path in `OpenAICompatibleBackend` (ARCH-03), but Mistral's
message format differs from OpenRouter/ZAI — override the extraction regexes
if wiring automatic recovery (see Implementation Notes).

### Comprehensive Error Handler

```python
import json
import random
import time
import urllib.error

class MistralErrorHandler:
    ERROR_CODES = {
        400: {"recoverable": False, "actions": ["Validate request schema", "Check context size (input+output vs 256k)"]},
        401: {"recoverable": False, "actions": ["Verify API key", "Re-export MISTRAL_API_KEY"]},
        403: {"recoverable": False, "actions": ["Check model access for the key", "Check org spend limit / suspension"]},
        404: {"recoverable": False, "actions": ["Use a current model (mistral-small-latest, mistral-medium-latest)"]},
        422: {"recoverable": False, "actions": ["Fix validation error", "Check tools count <= 128"]},
        429: {"recoverable": True, "retry_after": "Retry-After header", "actions": ["Reduce request rate", "Implement client-side limiter"]},
        500: {"recoverable": True, "retry_after": 2, "actions": ["Retry with backoff"]},
        502: {"recoverable": True, "retry_after": 2, "actions": ["Retry with backoff"]},
        503: {"recoverable": True, "retry_after": 30, "actions": ["Retry with longer backoff", "Check status.mistral.ai"]},
        504: {"recoverable": True, "retry_after": 5, "actions": ["Retry with backoff"]},
    }

    def handle_error(self, response_or_exc) -> dict:
        if isinstance(response_or_exc, urllib.error.HTTPError):
            status = response_or_exc.code
            try:
                body = json.loads(response_or_exc.read().decode("utf-8"))
                err = body.get("error", body)  # envelope is the body itself
                msg = err.get("message", str(body))
                etype = err.get("type", "")
                code = err.get("code", "")
            except Exception:
                msg, etype, code = str(response_or_exc), "", ""
        elif isinstance(response_or_exc, Exception):
            return {"code": "NETWORK_ERROR", "message": str(response_or_exc),
                    "recoverable": True, "actions": ["Check connectivity", "Increase timeout"]}
        else:
            status = getattr(response_or_exc, "status", 500)
            msg, etype, code = str(response_or_exc), "", ""

        info = self.ERROR_CODES.get(status, {"recoverable": False, "actions": ["Check logs"]})
        return {
            "code": status,
            "message": msg,
            "error_type": etype,
            "error_code": code,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"],
        }

    def should_retry(self, error: dict, attempt: int, max_attempts: int) -> bool:
        if not error["recoverable"]:
            return False
        if attempt >= max_attempts:
            return False
        return error["code"] in (429, 500, 502, 503, 504, "NETWORK_ERROR")

    def calculate_delay(self, error: dict, attempt: int) -> float:
        """Exponential backoff with full jitter — mirrors the official SDK recipe."""
        base = error.get("retry_after") or 1
        if not isinstance(base, (int, float)) or base == "Retry-After header":
            base = 1
        return min(60, float(base) * (2 ** attempt)) + random.uniform(0, 1)
```

### Retry Logic Implementation

```python
class MistralRetryHandler:
    """Matches the official SDK behavior: exponential backoff, max 5 attempts."""

    def __init__(self, max_attempts: int = 5, base_delay: float = 1.0):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.error_handler = MistralErrorHandler()

    def execute_with_retry(self, request_fn, **kwargs):
        last_error = None
        for attempt in range(self.max_attempts):
            try:
                response = request_fn(**kwargs)
                if hasattr(response, "status") and response.status == 200:
                    return response
                if hasattr(response, "status"):
                    error = self.error_handler.handle_error(response)
                else:
                    return response
            except Exception as exc:
                error = self.error_handler.handle_error(exc)

            if not self.error_handler.should_retry(error, attempt, self.max_attempts):
                raise RuntimeError(f"Mistral call failed: {error['message']}")
            time.sleep(self.error_handler.calculate_delay(error, attempt))
            last_error = error

        raise RuntimeError(f"Mistral retries exhausted. Last error: {last_error}")
```

---

## Rate Limiting & Concurrency

### Limit Dimensions

Mistral enforces limits **per organization** (not per key) across two
independent axes:

| Dimension | Enforced | Notes |
|-----------|----------|-------|
| Requests per second (RPS) | Independently | Short-burst limiter — a parallel tool-execution burst can trip this alone |
| Tokens per minute (TPM) | Independently | Input + output tokens |
| Requests/tokens per day | Model- and tier-dependent | Visible in Admin Panel → API → Limits |
| Monthly spend limit | Org + workspace level | **API access can be suspended** when hit until limit raised or month rolls over |

Current published numbers are tier- and model-specific and are shown in the
Admin Panel (or `GET /v1/admin/rate-limit` with admin keys) — the docs
deliberately avoid a single static table. **Batch jobs do not count against
real-time rate limits**, which makes batch the right lane for evaluation
runs.

### Reading the Headers

```
X-RateLimit-Remaining: 42
Retry-After: 12
```

- `X-RateLimit-Remaining` is the documented way to monitor headroom *before*
  hitting the limit — AgentKthx should log it on every response.
- `Retry-After` appears on `429`s; prefer it over any local estimate.
- When neither is present (some self-hosted gateways), fall back to local
  rate-limit tracking based on observed 429s.

### Service Tiers & Priority Routing

Request-side `service_tier` accepts `"auto"` (default — allows Priority Tier
routing) or `"standard_only"`. The response `usage.service_tier` reports
`"standard" | "priority"` — what actually served the request.

| Tier | Queue | Latency | Availability |
|------|-------|---------|--------------|
| Batch | Async queue, processed over 24 h | Minutes–hours | Global; separate limits; -50% price |
| Standard | Best-effort | Seconds | Default for all orgs |
| Priority | Priority queue, falls back to Standard when exceeded | Seconds, predictable | Enterprise entitlement + per-model custom limits |

Priority Tier requires account setup with Mistral (custom per-model limits
configured by their team). AgentKthx should default to `"auto"` and expose
`service_tier` as a backend kwarg; when `usage.service_tier == "priority"` is
observed, latency-sensitive workflows are getting the entitlement.

### Client-Side Rate Limiter

```python
import threading
import time
from collections import deque

class MistralClientRateLimiter:
    """Sliding-window limiter respecting RPS and TPM simultaneously."""

    def __init__(self, rps: int = 1, tpm: int = 500_000):
        self.rps = rps
        self.tpm = tpm
        self._lock = threading.Lock()
        self._req_times = deque()      # timestamps within last 1 s
        self._token_times = deque()    # (ts, tokens) within last 60 s

    def acquire(self, input_tokens: int = 0) -> float:
        """Block until a request can be made. Returns wait time (s)."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.time()
                while self._req_times and now - self._req_times[0] > 1:
                    self._req_times.popleft()
                while self._token_times and now - self._token_times[0][0] > 60:
                    self._token_times.popleft()
                if (len(self._req_times) < self.rps
                        and sum(t for _, t in self._token_times) + input_tokens <= self.tpm):
                    self._req_times.append(now)
                    self._token_times.append((now, input_tokens))
                    return waited
                sleep_for = max(0.05, 1 - (now - self._req_times[0])) \
                    if len(self._req_times) >= self.rps else 1.0
            time.sleep(sleep_for)
            waited += sleep_for
```

Configure `rps`/`tpm` from the org's actual limits (Admin Panel → API →
Limits) rather than the placeholder defaults — they scale with tier.

### Concurrency Management

RPS is the binding constraint, not parallelism: a single burst of 8 parallel
tool-result follow-ups will trip a 1 RPS free-tier limit instantly. Serialize
chat-loop requests per backend instance; allow higher concurrency only on
tiers where the org has verified headroom.

```python
import asyncio

class MistralConcurrencyManager:
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
```

---

## Multimodal Content Handling

Multimodal input is expressed as `ContentChunk` arrays in the `content` field
of user messages — **not** OpenAI's `image_url`-in-object shape, though the
chunk type literal is the same string.

### Supported Content Chunk Types

| `type` | Chunk fields | Notes |
|--------|--------------|-------|
| `text` | `text` | Plain text segment |
| `image_url` | `url`, `detail` (`auto\|high\|low`) | Public URL or `data:` base64 URI. Max **20 MB**/image; PNG, JPG, JPEG, GIF, WEBP |
| `document_url` | `document_url`, `document_name` | PDF or image URL for in-chat document understanding |
| `file` | `file_id` (UUID) | Reference to a previously uploaded `/v1/files` object |
| `input_audio` | `input_audio` | Base64 audio for audio-input models (Voxtral Small) |
| `reference` | `reference_ids` | Library/connector references (Agents ecosystem) |
| `thinking` | `thinking[]`, `signature` | **Output-only** — reasoning models echo thought blocks in assistant content |

### Vision Example

```json
{
    "model": "mistral-medium-latest",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "https://example.com/cat.png", "detail": "low"}}
            ]
        }
    ]
}
```

- Vision availability is per-model (`capabilities.vision` on the model card).
  Mistral Medium 3.5, Small 4, Large 3, and the Ministral 3 family support it;
  Codestral/Devstral do not.
- Images are resized internally; very small images lose detail. Prefer
  `detail: "low"` for thumbnails to conserve tokens.
- Any image handling in AgentKthx should go through the chunk-array form —
  passing OpenAI's `{"type": "image_url", "image_url": "https://..."}` string
  shorthand (URL directly instead of object) is **not** valid Mistral.

### Document Understanding In-Chat

`document_url` chunks process PDFs directly in chat completions on
OCR-capable models. For full structured extraction (bounding boxes, block
labels, confidence scores), use the dedicated `POST /v1/ocr` endpoint instead
(see [Batch, Files & OCR Services](#batch-files--ocr-services)).

### Audio

- **Transcription**: `POST /v1/audio/transcriptions` (Voxtral Mini Transcribe
  2). WAV, MP3, FLAC, OGG, WEBM; max **60 minutes** / **500 MB** per file.
- **Speech**: `POST /v1/audio/speech` (Voxtral TTS) — text-to-speech with
  zero-shot voice cloning; `/v1/audio/voices` manages voice inventory.
- **In-chat audio input**: `input_audio` chunk on Voxtral Small
  (`capabilities.audio`).

---

## Thinking & Reasoning Configuration

Mistral's reasoning story has three layers. The native-reasoning cookbook
parameters have been **deprecated** in favor of the unified controls below.

### 1. `reasoning_effort` — the control knob

```python
request["reasoning_effort"] = "none"   # disable thinking where the model allows
# values: "none" | "minimal" | "low" | "medium" | "high" | "xhigh"
```

| Value | Behavior | Use case |
|-------|----------|----------|
| `none` | Thinking off | Simple chat, cheapest path on hybrid models |
| `minimal` | Bare-minimum reasoning tokens | Classification, extraction |
| `low` | Light reasoning | Basic tool use, short answers |
| `medium` | Balanced (sensible default) | Multi-step reasoning, code generation |
| `high` | Deep reasoning | Math, planning, agent loops |
| `xhigh` | **Mistral-only** maximum-effort ladder | Hard proofs, long-horizon planning — very high token cost |

Notes for AgentKthx:

- Map OpenAI-style `reasoning_effort` kwargs straight through — the enum is a
  superset of OpenAI's (`xhigh` is extra). Never send both `reasoning_effort`
  and `prompt_mode` together; `prompt_mode` is for the Conversations/Agents
  stack.
- Which models reason: `capabilities.reasoning == true` on the model card
  (Mistral Medium 3.5, Small 4, Large 3, Magistral family). Non-reasoning
  models ignore or reject the parameter — gate it on the card.
- Reasoning tokens are **billed as output tokens** and count against
  `max_tokens`.

### 2. `ThinkChunk` — reading thoughts out of responses

Reasoning models may return `content` as a chunk array. Thinking blocks carry
a `signature` — a server-issued integrity token:

```json
{
    "role": "assistant",
    "content": [
        {
            "type": "thinking",
            "thinking": [
                {"type": "text", "text": "The user asked for weather in Toronto..."}
            ],
            "signature": "EQAAACUKBB8GEpk..."
        },
        {"type": "text", "text": "It's currently 18°C in Toronto."}
    ]
}
```

AgentKthx handling rules:

- Extract `text` chunks as the user-visible answer; route `thinking` chunks to
  a collapsible "thought" panel in agent-mode UI.
- Preserve the full assistant message (including `thinking` chunks and
  signatures) when continuing a multi-turn reasoning conversation — stripping
  them forces the model to re-reason and inflates cost.
- A `content` field that is an array (not a string) is normal for reasoning
  models. The response parser must accept `str | list` and normalize.

### 3. Hybrid vs dedicated reasoning models

- **Mistral Small 4** is hybrid: instruct + reasoning unified in one model;
  `reasoning_effort` modulates depth without a separate model switch.
- **Magistral Medium 1.2** is the dedicated reasoning specialist (128k
  context). Reach for it when `xhigh`-class reasoning is needed on a budget
  of tokens-per-dollar that Medium 3.5 can't hit.

---

## Sampling Parameters & Prompt Caching

### Temperature-family parameters

Mistral recommends `temperature` between **0.0 and 0.7**; the default is
model-specific and published on the model card
(`default_model_temperature` — e.g. `0.7` for Mistral Medium 3.5). Alter
temperature **or** `top_p`, not both — the docs are explicit about this.

| Parameter | Range | Guidance |
|-----------|-------|----------|
| `temperature` | 0.0–1.0+ | 0.2 → focused/deterministic; 0.7 → random/creative |
| `top_p` | 0.0–1.0 | Nucleus sampling; 0.1 = top 10% probability mass only |
| `presence_penalty` | -2.0–2.0 | Discourages repetition of already-used tokens |
| `frequency_penalty` | -2.0–2.0 | Scales penalty with token frequency in output |

### `random_seed`

```python
request["random_seed"] = 42   # deterministic output across identical calls
```

OpenAI calls this `seed`. Use it in AgentKthx's evaluation harness
(`agentkthx/examples/`) to make benchmark runs reproducible — same seed +
same inputs + same model snapshot gives (near-)deterministic outputs.

### `n` completions

`n > 1` returns multiple candidate completions; **input tokens are billed
once** regardless of `n`. Caveats: outputs require `temperature > 0` to
diverge, and `mistral-large-2512` does **not** support `n` completions at
all. Gate `n` on family detection.

### `safe_prompt`

```python
request["safe_prompt"] = False   # default
```

Injects Mistral's safety system prompt before all conversations. It changes
output tone slightly and consumes a few prompt tokens. AgentKthx should leave
this `False` by default (the agent's soul/system prompt owns behavior) and
expose it as a config flag for safety-sensitive deployments.

### `prediction` (expected outputs)

```python
request["prediction"] = {
    "type": "content",
    "content": "the expected completion text"
}
```

For edit/rewrite-style tasks where much of the output is known, supplying the
expected completion can reduce latency. Adopt later if AgentKthx adds a
diff/rewrite tool; harmless to omit.

### Prompt Caching (`prompt_cache_key`)

```python
request["prompt_cache_key"] = f"agentkthx-{session_id}"
```

- Reuse the same key for requests with shared prefixes (fixed system prompt,
  multi-turn history) to raise cache hit rate.
- **Cached input tokens are billed at 10%** of the standard input token
  price — the single biggest cost lever for long-running agent sessions.
- Combine with `X-RateLimit-Remaining` monitoring: cache hits also reduce
  TPM pressure since fewer tokens are processed.

---

## Batch, Files & OCR Services

### Files API

```python
# Upload (multipart/form-data)
POST /v1/files        # purpose="ocr" or "batch"; max 512 MB
GET  /v1/files        # list
GET  /v1/files/{id}/content   # download
DELETE /v1/files/{id}
```

- Files are retained **30 days** unless deleted earlier.
- OCR-supported formats: PDF, PNG, JPG, JPEG, TIFF, BMP, GIF, WEBP.
- `GET /v1/files/{file_id}/url` returns a signed URL usable in
  `document_url`/`file_id` chunks.

### Batch API

```python
POST   /v1/batch/jobs          # submit JSONL input file (chat-completion-shaped lines)
GET    /v1/batch/jobs          # list jobs
GET    /v1/batch/jobs/{job_id} # poll status
POST   /v1/batch/jobs/{job_id}/cancel
DELETE /v1/batch/jobs/{job_id}
```

- Max **100,000 requests** per batch; input file max **512 MB**.
- Processed asynchronously over a **24-hour window**; results downloadable
  for **24 hours after completion** — poll, then fetch promptly.
- **-50% pricing** vs synchronous and **does not count against real-time
  rate limits**. This is the correct lane for AgentKthx's benchmark suites
  (`agentkthx/examples/*.py`) once they outgrow interactive pacing.

### OCR API

```python
POST /v1/ocr
{
    "model": "mistral-ocr-latest",
    "document": {
        "type": "document_url",
        "document_url": "https://example.com/report.pdf",
        "document_name": "report.pdf"
    },
    "pages": [0, 1, 2],                 # or "0-5" / "0,2-4"
    "include_image_base64": false,
    "image_limit": 0,
    "document_annotation_format": {...}  # optional structured extraction
}
```

- OCR 4.1 (Premier) is current: paragraph-level bounding boxes, structural
  block labels, block-level confidence scores.
- Billing is **per 1,000 pages**, not per token.
- Relevant to a future `ocr` AgentKthx tool; the chat backend itself only
  needs `document_url` chunks for lightweight PDF Q&A.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType

class MistralBackend(OpenAICompatibleBackend):
    """Mistral La Plateforme backend (native /v1 chat-completions surface)."""

    BACKEND_TYPE = BackendType.CLOUD
    NAME = "mistral"
    is_cloud = True

    DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
    DEFAULT_MODEL = "mistral-small-latest"     # cost-efficient default
    FALLBACK_MODEL = "mistral-medium-latest"   # when Small is rate-limited/absent
    ENV_API_KEY = "MISTRAL_API_KEY"
    ENV_BASE_URL = "MISTRAL_BASE_URL"

    # Context-length 400 recovery (ARCH-03 shared path) — Mistral's message
    # wording differs from OpenRouter/ZAI. Tune these regexes against a real
    # 400 body before enabling automatic recovery:
    _CONTEXT_LENGTH_MAX_PATTERN: str = r"context length (?:of |is )?(\d+)"
    _CONTEXT_LENGTH_INPUT_PATTERN: str = r"(\d+) tokens? (?:in|of) (?:the )?(?:text |)input"
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = None   # not separately reported
    # Mistral counts input+output against max_context_length — keep the shared
    # divisor but raise the safety margin to cover the output budget too.
    _MAX_TOKENS_CAP_DIVISOR: int = 32
    _CONTEXT_SAFETY_MARGIN: int = 4096
    _CONTEXT_SAFE_FLOOR: int = 1024

    def _get_chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def _get_auth_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _get_model_defaults(self, model: str) -> dict:
        return {
            "temperature": 0.7,          # card default for Medium 3.5
            "max_tokens": 8192,
            "context_length": 262_144,   # overridden by model card when reachable
        }
```

Abstract methods that must work out of the box with the payloads above:
`generate()`, `generate_stream()`, `list_models()`, `test_tool_support()`
(inherited from `BaseBackend`), plus the four wire-format hooks
(`_get_chat_completions_url`, `_get_auth_headers`, `_iter_sse_lines`,
`_get_model_defaults`) required by `OpenAICompatibleBackend`. `list_models()`
should map `GET /v1/models` cards into AgentKthx's model inventory, carrying
over `max_context_length` and `capabilities`.

### 2. Request Builder — Mistral Deltas

```python
def _build_request(self, model: str, messages: list, **kwargs) -> dict:
    request = {
        "model": model,
        "messages": messages,
        "temperature": kwargs.get("temperature"),
        "top_p": kwargs.get("top_p"),
        "max_tokens": kwargs.get("max_tokens"),
        "stream": kwargs.get("stream", False),
        "random_seed": kwargs.get("seed") or kwargs.get("random_seed"),
        "safe_prompt": kwargs.get("safe_prompt", False),
        "service_tier": kwargs.get("service_tier", "auto"),
    }
    # Strip None values to keep payload minimal
    request = {k: v for k, v in request.items() if v is not None}

    if "tools" in kwargs:
        request["tools"] = kwargs["tools"]
        request["tool_choice"] = kwargs.get("tool_choice", "auto")
        if kwargs.get("parallel_tool_calls") is False:
            request["parallel_tool_calls"] = False

    # OpenAI 'seed' -> Mistral 'random_seed' handled above.
    # OpenAI 'reasoning_effort' passes through (superset enum, xhigh extra).
    if "reasoning_effort" in kwargs and kwargs["reasoning_effort"]:
        request["reasoning_effort"] = kwargs["reasoning_effort"]

    # Prompt caching — key on session id when the caller provides one.
    if kwargs.get("session_id"):
        request["prompt_cache_key"] = f"agentkthx-{kwargs['session_id']}"

    return request
```

Never forward OpenAI-only kwargs (`user`, `logprobs`, `top_k`,
`max_completion_tokens`, `service_tier: "flex"`/`"priority"`) — they trigger
`422` validation errors or silent breakage. Map `service_tier="flex"` /
`"priority"` requests to `"auto"` and log the downgrade.

### 3. Plugin Registration

`agentkthx/plugins/mistral/plugin.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "mistral",
  "version": "0.1.0",
  "description": "Mistral AI La Plateforme backend via native /v1 Chat-Completions API",
  "author": {
    "name": "VTSTech",
    "url": "https://www.vts-tech.org"
  },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "Mistral Cloud Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "MISTRAL",
        "defaults": {
          "MISTRAL_API_KEY": "",
          "MISTRAL_BASE_URL": "https://api.mistral.ai/v1",
          "MISTRAL_DEFAULT_MODEL": "mistral-small-latest"
        }
      },
      "provides": {
        "backends": {
          "mistral": "mistral.MistralBackend"
        },
        "cli_commands": [],
        "cli_flags": {
          "--backend": ["mistral"]
        }
      },
      "compatibility": {
        "agentkthx": ">=0.5.0"
      }
    }
  }
}
```

### 4. Tool Schema Compatibility

AgentKthx's `to_openai_schema()` output works unchanged. Two edge cases:

- **Enum-typed parameters**: Mistral respects `enum` in the tool schema but
  is not guaranteed to honor it under load — validate argument values
  against the schema before dispatching to the tool (same guidance as
  Gemini).
- **Arguments as objects**: non-streaming usually returns a JSON string, but
  the spec allows an object. The shared `tool_parse.py` path must accept
  both (the `MistralToolHandler` above does).

### 5. Context Management

```python
class MistralContextManager:
    """Trim history to fit Mistral's 256k window (128k for Magistral/Codestral)."""

    def __init__(self, model_name: str):
        cfg = detect_model_family(model_name)
        self.max_context = cfg.get("context_length", 262_144)
        self.window: list[dict] = []

    def add_message(self, role: str, content):
        self.window.append({"role": role, "content": content})
        # Keep 10% headroom: Mistral counts INPUT + OUTPUT against the window
        while self._total_tokens() > self.max_context * 0.9 and len(self.window) > 2:
            self.window.pop(0)

    def _total_tokens(self) -> int:
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
                        total += 500   # conservative estimate per image
                    elif part.get("type") == "document_url":
                        total += 1_500  # ~1 page of PDF text
        return total
```

### 6. Integration Test Plan

Tests to add under `tests/test_mistral_backend.py`:

```python
import os, pytest
from agentkthx import Agent

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
pytestmark = pytest.mark.skipif(not MISTRAL_API_KEY, reason="no Mistral key")

def test_basic_chat():
    agent = Agent(model="mistral-small-latest", backend="mistral")
    r = agent.run("What is 15 * 8?")
    assert "120" in r

def test_function_calling():
    agent = Agent(model="mistral-small-latest", backend="mistral",
                  tools=["calculator"])
    r = agent.run("Compute 23 * 17")
    assert r.tool_calls or r.text

def test_tool_choice_any_forces_call():
    agent = Agent(model="mistral-small-latest", backend="mistral",
                  tools=["calculator"], tool_choice="any")
    r = agent.run("Hello")
    # 'any' forces a tool call even on a greeting
    assert r.tool_calls

def test_streaming():
    agent = Agent(model="mistral-small-latest", backend="mistral")
    chunks = list(agent.stream("Count from 1 to 5"))
    assert len(chunks) >= 2

def test_reasoning_effort_passthrough():
    agent = Agent(model="mistral-small-latest", backend="mistral",
                  reasoning_effort="low")
    r = agent.run("Hi")
    assert r.text  # must not 422 on the effort value

def test_random_seed_alias():
    # OpenAI-style 'seed' kwarg must be mapped to 'random_seed'
    agent = Agent(model="mistral-small-latest", backend="mistral", seed=42)
    a = agent.run("Say a number between 1 and 100")
    b = agent.run("Say a number between 1 and 100")
    assert a.text and b.text

def test_models_list_capability_mapping():
    backend = Agent(model="mistral-small-latest", backend="mistral").backend
    models = backend.list_models()
    assert any("mistral" in m.id for m in models)

def test_429_backoff():
    # Rapid-fire requests should trip per-org RPS on low tiers; the backend
    # must recover via Retry-After-aware backoff rather than crash.
    agent = Agent(model="mistral-small-latest", backend="mistral")
    for i in range(10):
        r = agent.run(f"Say the number {i}")
        assert r.text
```

---

## Troubleshooting Matrix

### Issue Detection & Solutions

| Symptom | Possible Cause | Solution | Priority |
|---------|----------------|----------|----------|
| `401 authentication_error` | Missing/revoked key; key shown only once at creation | Re-create key in Studio → API keys; re-export `MISTRAL_API_KEY` | Critical |
| `422` "unknown field" after adding an OpenAI-only param (`user`, `logprobs`, `seed`) | OpenAI kwargs forwarded verbatim | Use the delta mapper in `_build_request`; strip OpenAI-only fields | High |
| `400` on a request that worked yesterday | Moving alias (`-latest`) rolled to a newer generation with different behavior | Pin a snapshot ID (`mistral-medium-3-5`) for reproducibility | High |
| `404` with `code: "unknown_model"` | Retired model still referenced in config | Check the deprecation table; switch to `deprecation_replacement_model` | High |
| `429 rate_limit_error` bursts during parallel tool fan-out | RPS enforced independently of TPM; org-level | Serialize chat-loop requests; honor `Retry-After`; log `X-RateLimit-Remaining` | High |
| `400` context error at < 256k tokens | Input **+ output** counted against window; reasoning tokens inflate output | Lower `max_tokens`, compact history, or use `reasoning_effort: "low"` | Medium |
| Infinite whitespace stream in JSON mode | `response_format: json_object` without "JSON" mentioned in the prompt | Always include "JSON" in the system/user prompt; prefer `json_schema` | Medium |
| `finish_reason: "model_length"` | Context window exhausted (distinct from `length`) | Trigger compaction, then retry — do not surface as simple truncation | Medium |
| Empty `tool_calls[].id` or literal `"null"` | Schema default leaking through | Synthesize a fallback ID; echo back exactly what was sent otherwise | Medium |
| Stream ends without usage stats | `stream_options.include_usage` not set | Send the opt-in; drop it for self-hosted gateways that reject it | Low |
| Higher token usage than visible output | Reasoning tokens (hybrid/`xhigh` models) billed as output | Show reasoning overhead in the token-budget UI | Low |
| `403` with suspended org | Monthly spend limit hit | Raise limit in Admin Panel → Subscriptions → Billing | Critical |

### Debug Mode Implementation

```python
class MistralDebugHandler:
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
            "tool_choice": request.get("tool_choice"),
            "reasoning_effort": request.get("reasoning_effort"),
            "safe_prompt": request.get("safe_prompt"),
            "service_tier": request.get("service_tier"),
            "prompt_cache_key": request.get("prompt_cache_key"),
            "random_seed": request.get("random_seed"),
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
            "service_tier": usage.get("service_tier"),
            "finish_reason": (response.get("choices") or [{}])[0].get("finish_reason"),
        })

    def summary(self) -> dict:
        requests = [e for e in self.debug_log if e["type"] == "request"]
        responses = [e for e in self.debug_log if e["type"] == "response"]
        return {
            "total_requests": len(requests),
            "total_responses": len(responses),
            "success_rate": len(responses) / max(len(requests), 1),
            "avg_completion_tokens": sum(r.get("completion_tokens", 0) for r in responses) / max(len(responses), 1),
            "tier_mix": {t: sum(1 for r in responses if r.get("service_tier") == t)
                         for t in ("standard", "priority")},
            "recent": self.debug_log[-10:],
        }
```

### Performance Monitoring

```python
class MistralPerformanceMonitor:
    def __init__(self):
        self.metrics = {
            "request_count": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_time": 0.0,
            "error_count": 0,
            "rate_limit_hits": 0,
            "cache_hits": 0,          # prompt_cache_key reuse with lower prompt cost
            "start_time": time.time(),
        }

    def record(self, *, prompt_tokens: int, completion_tokens: int,
               duration: float, success: bool = True,
               rate_limited: bool = False, cache_hit: bool = False):
        self.metrics["request_count"] += 1
        self.metrics["total_prompt_tokens"] += prompt_tokens
        self.metrics["total_completion_tokens"] += completion_tokens
        self.metrics["total_time"] += duration
        if not success:
            self.metrics["error_count"] += 1
        if rate_limited:
            self.metrics["rate_limit_hits"] += 1
        if cache_hit:
            self.metrics["cache_hits"] += 1

    def stats(self) -> dict:
        uptime = time.time() - self.metrics["start_time"]
        rc = max(self.metrics["request_count"], 1)
        return {
            "requests_per_minute": self.metrics["request_count"] / max(uptime / 60, 1),
            "avg_prompt_tokens": self.metrics["total_prompt_tokens"] / rc,
            "avg_completion_tokens": self.metrics["total_completion_tokens"] / rc,
            "error_rate": self.metrics["error_count"] / rc,
            "rate_limit_rate": self.metrics["rate_limit_hits"] / rc,
            "cache_hit_rate": self.metrics["cache_hits"] / rc,
            "uptime_seconds": uptime,
        }
```

---

## Appendix: OpenAI Wire-Format Deltas

The fastest mental model: Mistral is "OpenAI Chat Completions with renamed
and trimmed fields." The table is the complete delta list an integrator must
handle:

| Concept | OpenAI | Mistral | AgentKthx action |
|---------|--------|---------|------------------|
| Determinism seed | `seed` | `random_seed` | Map in request builder |
| Output cap | `max_tokens` / `max_completion_tokens` | `max_tokens` only | Send `max_tokens` |
| Context overflow | `finish_reason: "context_length_exceeded"` (varies) | `finish_reason: "model_length"` + 400 on input | Handle both values in finish-reason switch |
| Forced tool call | `tool_choice: "required"` | `"any"` (alias now accepted) | Send `"any"` for legacy-gateway safety |
| Tool call IDs | `call_`-prefixed | Short opaque strings; schema default `"null"` | Don't assume prefix; synthesize fallback IDs |
| Tool args type | JSON string | JSON string **or** object | Accept both in parser |
| Sampling control | `seed`, `logprobs`, `top_k` | none of these (has `random_seed`) | Don't forward; log a downgrade |
| Safe mode | — | `safe_prompt` (default false) | Expose as config flag |
| Prefill | — | `AssistantMessage.prefix = true` | Map to agent "continue from" feature |
| Expected output | `prediction` (beta) | `prediction` (GA shape `{"type": "content", ...}`) | Pass through when provided |
| Cache control | automatic | `prompt_cache_key` (cached tokens = 10% price) | Key per session |
| Service tier | `standard\|flex\|priority` | `auto\|standard_only` (request); `standard\|priority` (usage echo) | Map flex→standard_only semantics |
| Effort ladder | `minimal\|low\|medium\|high` (+`none` on some) | `none\|minimal\|low\|medium\|high\|xhigh` | Superset — pass through, allow xhigh |
| Error envelope | `{"error": {...}}` | `{"object": "error", ...}` at top level | Parse both shapes in error handler |
| Model metadata | bare objects | Full model cards (capabilities, deprecation, default temp) | Prefer cards over static tables |
| Multiple choices | `n` (widely supported) | `n` (input billed once; not on `mistral-large-2512`) | Gate on family |

## Appendix: Model Lifecycle & Deprecations

Lifecycle stages: **Labs** (`labs-` prefix, free, silent updates,
non-production) → **Public Preview** (near-final, silent updates, GA pricing)
→ **General Availability** (no silent updates, aliases, supported until
retirement) → **Deprecated** (retirement announced) → **Retired**.

Highlights of the deprecation ledger relevant to AgentKthx defaults
(check the live table at `docs.mistral.ai/getting-started/models` before
relying on this snapshot):

| Model | Snapshot ID | API deprecated | Retired | Replacement |
|-------|-------------|----------------|---------|-------------|
| Mistral Small 3.2 | `mistral-small-2506` | 2026-04-30 | 2026-07-31 | Mistral Small 4 |
| Mistral Medium 3.1 | `mistral-medium-2508` | 2026-05-22 | 2026-08-31 | Mistral Medium 3.5 |
| Devstral 2 | `devstral-2512` | 2026-05-22 | 2026-07-31 | Mistral Medium 3.5 |
| Magistral Medium 1.2 | `magistral-medium-2509` | 2026-05-22 | 2026-07-31 | Mistral Medium 3.5 |
| Magistral Small 1.2 | `magistral-small-2509` | 2026-04-30 | 2026-07-31 | Mistral Small 4 |
| Ministral 3B/8B (24.1) | `ministral-3b-2410` / `ministral-8b-2410` | 2025-12-02 | 2025-12-31 | Ministral 3 3B/8B |

Operational rules for AgentKthx:

- Default model IDs should always be `-latest` aliases; pinned snapshot IDs
  belong in tests and reproducible benchmark configs only.
- When a model card shows a non-null `deprecation` date, surface a one-time
  warning in agent mode and suggest `deprecation_replacement_model`.
- Silent updates never hit GA models — a pinned GA snapshot is safe for
  regression testing across the whole support window.

## Appendix: Pricing Snapshot (Sep 2026)

Pricing is per-model and per-million-tokens; the authoritative table lives in
the docs. Structural facts that survive model churn:

| Item | Value | Notes |
|------|-------|-------|
| Chat models | Per 1M input / per 1M output | e.g. Mistral Large: $0.50 in / $1.50 out (FAQ reference point) |
| Cached input tokens | 10% of input price (up to -90%) | Opt-in via `prompt_cache_key` |
| Batch API | -50% on chat pricing | Async, 24 h processing window |
| OCR | Per 1,000 pages | Not token-billed |
| Audio (transcription/TTS) | Per minute | Voxtral family |
| Hosted tool calls (web search, code interpreter, connectors) | Per call | Agents/Conversations stack |
| Labs models | Free | `labs-` prefix; silent updates; no data opt-out |

Cost-optimization order of operations for AgentKthx sessions:
1. Reuse `prompt_cache_key` per session (up to 90% off repeated prefixes).
2. Prefer `mistral-small-latest` where capability allows (Apache 2.0 tier).
3. Route offline benchmark suites through the Batch API (-50%, off rate
   limits).
4. Bound reasoning spend with `reasoning_effort` — `xhigh` is a premium
   ladder, not a default.

---

This technical reference provides the implementation details needed to add a
Mistral backend to AgentKthx. The native `/v1` surface is the primary
integration path — it is OpenAI-shaped enough for `OpenAICompatibleBackend`
to carry most of the load, with the delta mapper, finish-reason switch, and
model-card-driven configuration doing the remaining work. Agents,
Conversations, Libraries, Workflows, and the observability stack are listed
for parity awareness and should only be wired in when AgentKthx needs
capabilities the plain chat-completions loop cannot express.
