# Pollinations API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://gen.pollinations.ai/docs (API docs v0.3.0, OpenAPI 3.1.0) and https://github.com/pollinations/pollinations/blob/master/APIDOCS.md, verified against the live `/v1/models` catalog on 2026-09-27
> **Live-behavior corrections**: 2026-09-28 — catalog entitlement scoping, zero-cost pricing encoding, rolling health telemetry (marked ⚠️ throughout)
> **Primary focus**: Unified gateway `https://gen.pollinations.ai` (OpenAI-compatible) with legacy `text.pollinations.ai` / `image.pollinations.ai` surfaces
> **Last Updated**: 2026-09-28
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
9. [Thinking & Reasoning Configuration](#thinking--reasoning-configuration)
10. [Sampling Parameters & Prompt Caching](#sampling-parameters--prompt-caching)
11. [Media Generation Services (Image / Video / Audio / Embeddings / 3D)](#media-generation-services-image--video--audio--embeddings--3d)
12. [The Pollen Economy & Free Access](#the-pollen-economy--free-access)
13. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
14. [Troubleshooting Matrix](#troubleshooting-matrix)
15. [Appendix: Legacy Endpoints (text/image.pollinations.ai)](#appendix-legacy-endpoints-textimagepollinationsai)
16. [Appendix: OpenAI Wire-Format Deltas](#appendix-openai-wire-format-deltas)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — unified gateway (primary AgentKthx surface, OpenAI-compatible)
BASE_URL = "https://gen.pollinations.ai/v1"

# API docs / OpenAPI explorer
DOCS_URL = "https://gen.pollinations.ai/docs"

# Media storage — separate host for uploads referenced by other calls
MEDIA_BASE_URL = "https://media.pollinations.ai"

# OAuth 2.1 server metadata (RFC 8414) for BYOP flows
OAUTH_METADATA = "https://enter.pollinations.ai/.well-known/oauth-authorization-server"

# Legacy surfaces (still operational, see Appendix A)
LEGACY_TEXT_BASE = "https://text.pollinations.ai"
LEGACY_IMAGE_BASE = "https://image.pollinations.ai"
```

Unlike Mistral (native-only) or Gemini (separate compat endpoint), Pollinations'
primary gateway **IS** an OpenAI-compatible surface: Chat Completions,
Responses, Images, Audio, Embeddings, and Realtime all follow OpenAI wire
shapes. Point any OpenAI SDK at `https://gen.pollinations.ai/v1` and it works
unchanged.

### Key Types

Pollinations distinguishes keys by **prefix**, and the distinction matters for
where AgentKthx may safely store them:

| Key type | Prefix | Where it goes | What it can do |
|---|---|---|---|
| Secret key | `sk_` | Server only (env var, secrets manager) | Full account access. Can create child keys, list usage, run any model the account allows. **Never ship to a browser, mobile app, or repo.** |
| App key (BYOP) | `pk_` with redirect URIs | OAuth 2.1 `client_id` for web/mobile/CLI consent | Users authorize your app; you receive a scoped user `sk_`. Created at [enter.pollinations.ai/keys](https://enter.pollinations.ai/keys). |
| Raw publishable key | `pk_` without app binding | **Legacy only** | Existing integrations only. Rate-limited to 1 pollen per IP per hour. Do not mint new raw `pk_` keys. |

**AgentKthx guidance**: AgentKthx is a server-side agent runtime, so it always
uses `sk_` keys read from the environment. Never embed any key — including
`pk_` — in prompt text, logs, or plugin manifests.

### Key Transports

```python
# Preferred: Authorization header (all POST endpoints, server WebSocket)
AUTH_HEADER = {"Authorization": "Bearer <sk_...>"}

# GET endpoints and browser WebSockets (custom headers impossible):
#   https://gen.pollinations.ai/image/a%20cat?key=<key>
AUTH_QUERY_PARAM = "?key=<key>"
```

The header is preferred for everything except browser flows that cannot set
custom headers (image/audio `GET` endpoints and WebSocket realtime sessions).

### Endpoints That Require No Auth

| Endpoint group | Auth |
|---|---|
| `GET /{id}`, `GET /{id}/metadata`, `HEAD /{id}` | None — media URLs are public reads |
| `GET /models`, `GET /v1/models`, `GET /image/models`, `GET /text/models`, `GET /audio/models`, `GET /embeddings/models` | None — model catalog endpoints are public. ⚠️ **Keyed ≠ anonymous** (verified 2026-09-28): sending a Bearer key to `GET /v1/models` scopes the catalog to the key's entitlements — every `paid_only` model silently drops out (observed: 307 cards anonymous vs 134 cards with a test key). Fetch catalogs with NO Authorization header unless you intentionally want the entitlement view; the `?key=` query form scopes identically |
| Everything else | Bearer key required (unless the endpoint documents `?key=` support) |

### API Endpoints (AgentKthx-relevant)

```python
# ─── Text generation ─────────────────────────────────────────────
CHAT_COMPLETIONS = "/chat/completions"   # POST — full OpenAI compat: streaming,
                                         #        tools, vision, structured outputs
RESPONSES        = "/responses"          # POST — stateless Responses API subset
TEXT_MESSAGES    = "/text"               # POST — messages in, plain text out
TEXT_SIMPLE      = "/text/{prompt}"      # GET  — quick prototyping, plain text

# ─── Media generation (Pollinations' specialty) ──────────────────
IMAGE_GET        = "/image/{prompt}"     # GET  — JPEG/PNG/SVG
IMAGE_GENERATE   = "/images/generations" # POST — OpenAI Images compatible
IMAGE_EDIT       = "/images/edits"       # POST — OpenAI Images Edits compatible
VIDEO_GET        = "/video/{prompt}"     # GET  — MP4
AUDIO_SPEECH     = "/audio/speech"       # POST — OpenAI TTS compatible
AUDIO_TRANSCRIBE = "/audio/transcriptions"  # POST — Whisper compatible
AUDIO_SIMPLE     = "/audio/{text}"       # GET  — TTS via GET

# ─── Realtime & embeddings ───────────────────────────────────────
REALTIME_WS      = "/realtime"           # GET (WebSocket upgrade), OpenAI
REALTIME_WS_V1   = "/v1/realtime"        #      Realtime protocol events
EMBEDDINGS       = "/embeddings"         # POST — OpenAI compatible

# ─── Discovery & account ─────────────────────────────────────────
MODELS           = "/models"             # GET — bare catalog: RAW name-keyed JSON array
                                         #        (NOT the /v1 envelope) with a richer
                                         #        schema — the ONLY feed carrying
                                         #        paid_only (the tier boundary)
ACCOUNT_BALANCE  = "/account/balance"    # GET — pollen balance
ACCOUNT_QUESTS   = "/account/quests"     # GET — quest catalog + status
ACCOUNT_USAGE    = "/account/usage"      # GET — per-request history (JSON/CSV)

# ─── Auxiliary (available, not required for chat backend) ────────
VOICE_CHANGER    = "/audio/voice-changer"    # POST
VOICE_ISOLATOR   = "/audio/voice-isolator"   # POST
DECISIONS        = "/alpha/decisions"        # POST — typed decisions (jev)
MEDIA_UPLOAD     = "https://media.pollinations.ai/upload"  # POST multipart
```

### OAuth 2.1 / BYOP (Bring Your Own Pollen)

Third-party apps can obtain a key **on behalf of a Pollinations user** — the
OAuth 2.1 authorization-code flow with PKCE (S256) for web apps, or the device
flow (RFC 8628) for CLIs. The registered `pk_` App Key is the public
`client_id` (no secret); the issued access token is an opaque `sk_` key bound
to the budget, expiry, and scopes the user approved. Endpoints are discoverable
via RFC 8414 metadata — resolve them from there rather than hardcoding:

```
GET https://enter.pollinations.ai/.well-known/oauth-authorization-server
```

Full integration guide: `BRING_YOUR_OWN_POLLEN.md` in the Pollinations repo.
AgentKthx does not need this for personal use, but it is the correct path if
AgentKthx is ever distributed to end users who hold their own Pollinations
accounts.

---

## Request/Response Structure

### Chat Completions — `POST /v1/chat/completions`

Fully compatible with the OpenAI Chat Completions API. Supports streaming,
function calling, vision (image input), structured outputs, and
reasoning/thinking modes depending on the model.

Successful text JSON responses contain `usage`. Text streams contain a usage
chunk before `[DONE]`; **missing or malformed usage on a completed text
response fails the request** (Pollinations validates this server-side — unlike
OpenAI, which is lax).

```bash
curl https://gen.pollinations.ai/v1/chat/completions \
  -H "Authorization: Bearer $POLLINATIONS_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.4-nano",
    "messages": [{"role": "user", "content": "Summarise relativity in one sentence."}]
  }'
```

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1758960000,
  "model": "openai/gpt-5.4-nano",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "…"},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 42,
    "total_tokens": 61,
    "prompt_tokens_details": {"cached_tokens": 0},
    "completion_tokens_details": {}
  }
}
```

### Request Parameters (Chat Completions)

| Field | Type | Notes |
|---|---|---|
| `model` * | `string` | Provider-prefixed id (`openai/gpt-5.4-nano`) or alias (`openai`). See [Model Catalog](#model-catalog--specifications) |
| `messages` * | `object[]` | OpenAI message array; multimodal `content` arrays supported |
| `stream` | `boolean` | SSE streaming, OpenAI wire format |
| `stream_options` | `object` | `{include_usage: true}` — usage arrives on the final chunk |
| `temperature`, `top_p`, `top_k` | `number` | Honored only when the model card lists them in `supported_parameters`; ignored otherwise |
| `max_tokens`, `max_completion_tokens` | `integer` | Output token cap |
| `tools`, `tool_choice`, `parallel_tool_calls` | mixed | Function calling, see [Function Calling](#function-calling-implementation) |
| `response_format` | `object` | `json_object` or `json_schema` (model-dependent: Sonnet 4.6 supports `json_schema`, **not** `json_object`) |
| `reasoning_effort` | `string` | See [Thinking & Reasoning](#thinking--reasoning-configuration) |
| `seed` | `integer` | Reproducible results on models that support it |
| `safe` | `string \| boolean` | Safety filters: `privacy,secrets,sexual,violence,shield`; `true` = `privacy,secrets`; `nsfw` = `sexual,violence`. Also accepted as the `Pollinations-Safe` header. Defaults to **off** |
| `cache_control` | — | Per-content-block, see [Prompt Caching](#sampling-parameters--prompt-caching) |

`*` = required. Per-model support is advertised in the model card's
`supported_parameters` array — Pollinations omits unverified controls rather
than silently dropping them, and inclusion does not guarantee every value or
combination works (e.g. `openai/gpt-5.4` omits sampling controls entirely; on
`anthropic/claude-sonnet-4.6`, `temperature` and `top_p` are mutually
exclusive with `temperature` winning, and both are disabled when
`reasoning_effort` is set).

### Simple Text Generation — `GET /text/{prompt}`

A simplified alternative to the OpenAI-compatible endpoint — ideal for quick
prototyping, health checks, and one-shot queries. Returns `text/plain`.

```bash
curl "https://gen.pollinations.ai/text/Write%20a%20haiku%20about%20coding?model=openai%2Fgpt-5.4-nano" \
  -H "Authorization: Bearer $POLLINATIONS_KEY"
```

Query parameters mirror the JSON body: `model`, `seed`, `system` (system
prompt), `json` (force valid JSON output), `temperature`, `top_p`,
`presence_penalty`, `frequency_penalty`, `repetition_penalty`, `max_tokens`,
`max_completion_tokens`, `reasoning_effort`, `voice`, `stream`, `safe`.

`POST /text` is the messages-flavored variant: send an OpenAI-style
`messages` array, receive plain text back.

### Responses API — `POST /v1/responses`

A deliberately **stateless** subset of the OpenAI Responses API:

- `store` must be `false`; `previous_response_id`, `conversation`, and
  `prompt` must be null or omitted; `background` must be false or omitted;
  encrypted content and reusable item references are rejected.
- Streaming uses Responses event names and terminal usage events. Direct
  models preserve the provider's terminal marker; managed-agent streams add
  one `data: [DONE]`.
- For text models, missing or malformed usage on a completed/incomplete
  response fails the request. Failed responses may report null usage; those
  failed requests are not billed.

```bash
curl https://gen.pollinations.ai/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $POLLINATIONS_API_KEY" \
  -d '{"model": "openai", "input": "Explain why the sky is blue in two sentences.", "store": false}'
```

Use `supported_endpoints` from the model card to find which models advertise
`/v1/responses`. Community text models with an exact Responses URL accept both
public APIs (Responses natively, Chat Completions through a shared stateless
adapter); Chat-only registrations accept Chat Completions only.

---

## Model Catalog & Specifications

### Catalog Shape

`GET /v1/models` returns the OpenAI list envelope (**311 cards at the 2026-09-27
snapshot; live count drifts with real-time community churn — 307–308 observed
2026-09-28, with publisher paths renaming between requests**):

```json
{
  "object": "list",
  "data": [ /* model cards */ ]
}
```

⚠️ **Catalog entitlement scoping (verified 2026-09-28).** The envelope is
OpenAI-shaped, but the CONTENT is key-scoped: send `Authorization: Bearer`
and the gateway filters `data` down to the key's entitlements. Observed with
a test key: **307 cards anonymous vs 134 keyed** — the keyed feed is EXACTLY
the `paid_only != True` subset of the bare `GET /models` feed (the free
TIER: Quest-Pollen-eligible models — still metered pollen per token, but
runnable on the free grant). The `paid_only` boundary field itself appears
ONLY on the bare `GET /models` feed (name-keyed, richer schema: `paid_only`,
`pricing_default_label`, `pricing_variants`, `publisher`, `is_specialized`)
— `/v1/models` cards never carry it. Consequences:

- Catalog clients that want the full public view must send NO Authorization
  header even when they hold a key (AgentKthx: `POLLINATIONS_ANON_CATALOG=1`
  does exactly this — catalog GET anonymous, generation POST keyed).
- The keyed feed still carries every zero-cost model (verified: all 16
  `:free` community cards on 2026-09-28), so zero-cost model discovery
  works in either view.
- Community cards churn between requests (adds/removes/renames); treat ±1–2
  card count deltas between runs as normal live drift, not client bugs.

### Model Card Schema

Every card carries capability, pricing, and **health telemetry** — the health
block is unique to Pollinations and is directly useful for AgentKthx fallback
logic:

```json
{
  "id": "openai/gpt-5.4-nano",
  "object": "model",
  "created": 1759795200,
  "owned_by": "OpenAI",
  "aliases": ["gpt-5.4-nano", "openai"],
  "category": "text",
  "community": false,
  "title": "GPT-5.4 Nano",
  "description": "Fast, affordable all-rounder for everyday chat and image questions",
  "input_modalities": ["text", "image"],
  "output_modalities": ["text"],
  "supported_endpoints": ["/v1/chat/completions", "/text", "/text/{prompt}", "/v1/responses"],
  "pricing": {
    "currency": "pollen",
    "promptTextTokens": "0.00000015",
    "promptCachedTokens": "0.000000015",
    "completionTextTokens": "0.0000009375"
  },
  "capabilities": ["tool_calling", "reasoning"],
  "supported_parameters": ["max_tokens", "stream", "tools", "tool_choice",
    "response_format", "reasoning_effort", "parallel_tool_calls", "verbosity"],
  "tools": true,
  "reasoning": true,
  "context_length": 400000,
  "health": {"status": "healthy", "success_rate": 99.95, "requests": 75499}
}
```

| Field | AgentKthx use |
|---|---|
| `id` | Canonical `provider/model` id — always send this to the API |
| `aliases` | Shorthand (`openai`, `gpt-5.4-nano`); resolvable, but prefer the full id |
| `category` | `text`, `image`, `video`, `audio`, `embedding`, `3d` — filter for the chat backend |
| `community` | `true` = user-published model with `community/owner/model` id |
| `input_modalities` / `output_modalities` | Vision/audio/video support sniffing |
| `supported_endpoints` | Which API surfaces the model accepts |
| `pricing` | Pollen per **single token** (dashboard displays per 1M). ⚠️ Zero-cost models are encoded as a currency-only dict `{"currency": "pollen"}` with NO price fields — price fields are never zero-valued on the live feed |
| `capabilities` / `tools` / `reasoning` | Capability sniffing for `test_tool_support()` |
| `supported_parameters` | Per-model request-param allowlist |
| `context_length` | Feed the context-recovery divisor in the backend |
| `health.status` / `health.success_rate` | **Fallback ordering** — prefer healthy, high-success models. ⚠️ Telemetry is a ROLLING window (request counts observed DECREASING across runs: 64642 → 64511 → 64446 for the same card); weigh `success_rate` by `requests` volume — `sr=100` off 1 request is weak evidence |

Specialized catalogs expose the same cards through category endpoints:
`GET /text/models`, `GET /image/models`, `GET /video/models`,
`GET /audio/models`, `GET /embeddings/models`, `GET /3d/models`, plus
`GET /v1/models/{model}` (single card, OpenAI retrieve shape).

### Model Families (September 2026)

Model ids are `provider/model`. Representative official families:

| Provider prefix | Notable ids (Sep 2026) | Notes |
|---|---|---|
| `openai/` | `gpt-5.4-nano` (default text model), `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.5`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.3-codex`, `gpt-oss-20b`, `gpt-audio-mini`, `gpt-image-2` | Nano is the cheap workhorse; `openai` alias resolves to `gpt-5.4-nano` |
| `anthropic/` | `claude-sonnet-4.6`, `claude-haiku-4.5`, `claude-opus-5.x`, `claude-fable-5.1` | Prompt-caching capable |
| `google/` | `gemini-3.7-flash`, `gemini-3.8-flash`, `gemini-2.5-flash-lite`, `gemma-4-*`, `gemini-embedding-2` | Prompt-caching capable; `:search` variants with built-in tools |
| `z-ai/` | `glm-5.3`, `glm-5.3-flash`, `glm-5.3-flashx`, `glm-5.2` | `glm-5.3-flashx` = 1M-token context; same GLM family as the native ZAI backend |
| `deepseek/` | `deepseek-v4-flash`, `deepseek-v4.1-flash`, `deepseek-v4-pro` | Fast/cheap tier |
| `qwen/` | `qwen3.8-max`, `qwen3.8-flash`, `qwen3-vl-*`, `qwen3-coder-next`, `qwen-image-3` | VL variants take image/video input |
| `mistralai/` | `mistral-small-4`, `mistral-small-3.2`, `mistral-large-3` | Same models as the native Mistral backend |
| `moonshotai/` | `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3` | |
| `meta/` | `llama-4-maverick`, `llama-4-scout`, `llama-3.3-70b-instruct`, `muse-*` | |
| `cohere/` | `command-a-plus` | |
| `perplexity/` | `sonar` | Web-search-grounded |
| `inception/` | `mercury-2`, `mercury-2.5-preview` | Diffusion LLMs |
| `amazon/` | `nova-micro-v1`, `nova-2-lite-v1`, `nova-canvas-v1` | Prompt-caching capable |
| `nvidia/` | `nemotron-3-ultra`, `nemotron-3.5-lightning` | |
| `minimax/`, `tencent/`, `xiaomi/`, `stepfun/`, `meituan/` | `minimax-m3`, `hy3`/`hy4-preview`, `mimo-v2.6-*`, `step-3.7-flash`, `longcat-2.0` | |
| `typesafe/` | `jev-1.13` | Typed decisions via `/alpha/decisions` |
| `pollinations/` | `midijourney`, `midijourney-large` | In-house music models |
| `community/<owner>/` | user-published | Free or priced at owner's discretion |

**One key, many vendors.** Routing to the corresponding upstream provider
happens on Pollinations' side — AgentKthx does not need separate keys per
provider. Note that this makes Pollinations an aggregator in the same role as
OpenRouter, with a much simpler economics model (pollen credits instead of
per-vendor USD billing).

### Health & Status Endpoints

- `GET /account/balance` — pollen balance (see [Pollen Economy](#the-pollen-economy--free-access))
- `GET /account/usage` — per-request history (model, tokens, `cost_usd`,
  `response_time_ms`), last 30 days default, up to 90 days, JSON/CSV export,
  50k-row cap, cursor pagination via `before` + `before_event_id`
- `GET /account/usage/daily` — daily aggregates for dashboards
- `GET /account/key/usage` — key-scoped usage
- Model `health` block — rolling `success_rate` and request counts per card

---

## Function Calling Implementation

### Tool Declaration

Standard OpenAI `tools` array. Models advertise support via the card's
`tools: true` / `capabilities: ["tool_calling"]`.

```json
{
  "model": "openai/gpt-5.4-nano",
  "messages": [{"role": "user", "content": "Weather in Toronto?"}],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    }
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true
}
```

### Tool Call Response

The assistant message arrives in OpenAI shape with `finish_reason:
"tool_calls"`; loop back with `role: "tool"` messages exactly as with the
OpenAI backend. `OpenAICompatibleBackend` needs **no special-case code** for
the tool loop itself.

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"city\": \"Toronto\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

### Caveats

- `tool_choice` supports `"auto"`, `"none"`, `"required"`, and named-function
  objects (OpenAI semantics). Some upstream models restrict forced-tool
  choices — e.g. `anthropic/claude-fable-5.1` accepts only automatic or
  disabled tool choice; forcing any named tool returns a 400.
- `parallel_tool_calls: false` disables multi-tool fan-out where the upstream
  model supports the flag.
- **Gemini models do not cache when tools are present** — see
  [Prompt Caching](#sampling-parameters--prompt-caching). If a cached system
  prefix matters more than tools, send `"tools": []` or set a
  `response_format` instead.
- Structured outputs (`response_format.json_schema`) and tool calling are
  both gated by `supported_parameters` — check the card rather than assuming.

### Typed Decisions — `POST /alpha/decisions` (bonus surface)

`typesafe/jev-1.13` returns calibrated judgments instead of free text. Post
`state` plus a map of `questions` (each `choice`, `score`, or `noul`);
answers return per-question with `confidence` and `probabilities`. The same
model is reachable from `/v1/chat/completions` by putting the identical
request JSON in the last `user` message. Context limit 64k tokens total, 32k
for state + longest question. No streaming. Not needed by AgentKthx today,
but a clean primitive for classifier-style plugins.

---

## Streaming & Real-time Features

### Server-Sent Events (Chat Completions)

The wire format is **byte-for-byte the OpenAI streaming format** — any OpenAI
SDK that supports streaming works unchanged:

```bash
curl -N "https://gen.pollinations.ai/v1/chat/completions" \
  -H "Authorization: Bearer $POLLINATIONS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-5.4-nano","stream":true,"messages":[{"role":"user","content":"Count to five, one word per line."}]}'
```

- Each event is a `data: {…}` line terminated by `data: [DONE]`.
- `-N` (or the SDK equivalent) disables client-side buffering so deltas
  arrive as generated.
- Usage arrives on the final chunk; opt in explicitly with
  `stream_options: {include_usage: true}` if the SDK requires it.
- **Strictness**: for text models, missing or malformed usage on a completed
  stream fails the request. This is enforced server-side — AgentKthx should
  therefore treat an absent usage chunk on text streams as a protocol error,
  not a silent success.

`OpenAICompatibleBackend._iter_sse_lines()` handles this unchanged.

### Responses Streaming

`POST /v1/responses` with `stream: true` uses Responses event names and
terminal usage events. Direct models preserve the provider's terminal marker;
managed-agent streams add one `data: [DONE]` marker.

### Media Models via Chat Streaming

Image/video/audio/3D models invoked through chat completions emit events only
**after** generation finishes (no incremental deltas), return a Markdown
image/audio/video embed plus the plain public file URL, and carry
`usage: null` chunks — no final usage chunk at all. The URL is also in the
`Link` header of non-streaming responses.

### Realtime WebSocket

OpenAI-compatible Realtime WebSocket for voice, multimodal, and transcription
sessions:

| Endpoint | Use |
|---|---|
| `GET wss://gen.pollinations.ai/realtime` | Pollinations Realtime session |
| `GET wss://gen.pollinations.ai/v1/realtime` | OpenAI-style path |

```js
import WebSocket from "ws";

// Server: Bearer auth. Browser: append `&key=pk_...` instead.
const ws = new WebSocket(
    "wss://gen.pollinations.ai/v1/realtime?model=openai/gpt-realtime-2.1",
    { headers: { Authorization: `Bearer ${process.env.POLLINATIONS_API_KEY}` } },
);

ws.on("open", () => ws.send(JSON.stringify({
    type: "session.update",
    session: { type: "realtime", instructions: "Be concise." },
})));
ws.on("message", (m) => console.log(JSON.parse(m.toString())));
```

- Models: `openai/gpt-realtime-2.1`, `openai/gpt-realtime-2.1-mini`,
  `elevenlabs/scribe-v2-realtime` (auto-selects a transcription session),
  `openai/gpt-live-transcribe`.
- Events follow the OpenAI Realtime protocol (`session.update`,
  `input_audio_buffer.*`, `response.*`).
- **Billing**: requires a positive balance; one billing event settles when
  the socket closes.
- Browser audio gotcha: route model output through an `<audio>` element so
  echo cancellation works — otherwise the mic re-captures the model's voice
  and it replies to itself. WebRTC transport handles this automatically;
  WebSocket transport leaves it to the client.

---

## Error Codes & Recovery

All endpoints return errors in a **Pollinations-specific envelope** (different
from OpenAI's bare `{error: {...}}`):

```json
{
  "status": 400,
  "success": false,
  "error": {
    "code": "BAD_REQUEST",
    "message": "Description of what went wrong",
    "timestamp": "2026-01-01T00:00:00.000Z",
    "details": {"name": "ValidationError"},
    "requestId": "req_abc123"
  }
}
```

### Status Table

| Status | Code | Description | AgentKthx recovery |
|---|---|---|---|
| `400` | `BAD_REQUEST` | Invalid input. `details` includes `formErrors` and `fieldErrors` for validation failures. | Log `requestId`; fix payload. Do not retry. |
| `400` | `invalid_image_url` | Image URL is malformed, not HTTP(S), points at a private/credentialed host, **or redirects**. | Provide a direct public image URL; pre-resolve redirects client-side. |
| `400` | `failed_to_download_image` | Image host unreachable, DNS failure, non-2xx, or truncated body. `details.upstreamStatus` reports the host status. | Retry the *image source* first; surface `upstreamStatus`. |
| `400` | `image_too_large` | Per-image or per-request image size/count cap exceeded. | Downscale or split the request. |
| `400` | `unsupported_image_media_type` | Media type not recognized. | Convert to PNG/JPEG client-side. |
| `401` | `UNAUTHORIZED` | Missing or invalid API key. | Check `POLLINATIONS_API_KEY`; do not retry. |
| `402` | `PAYMENT_REQUIRED` | **Insufficient pollen balance or API-key budget exhausted.** Key authenticated fine. | Not retryable. See Pollen Economy — check `/account/balance`, top up, or fall back to another backend. |
| `403` | `FORBIDDEN` | Insufficient permissions, paid-model access denied, or unbudgeted key querying account usage. | Check key scopes; `paidOnly` models reject Quest Pollen. |
| `404` | `NOT_FOUND` | Resource/model not found. | Verify model id against `/v1/models` (community ids need the full `community/owner/model` form). |
| `405` | `METHOD_NOT_ALLOWED` | Wrong HTTP method on route. | Fix verb. |
| `409` | `CONFLICT` | Request conflicts with resource state (e.g. duplicate key name). | Only relevant for key-management calls. |
| `422` | `UNPROCESSABLE_ENTITY` | Well-formed but semantically invalid — model rejection or unsupported parameter combination. | Inspect `details`; often an unsupported param combination (e.g. forcing tools on a model that rejects them). |
| `422` | `content_policy_violation` | Prompt/input/output blocked by content moderation. | Adjust input; do not retry unchanged. |
| `429` | `RATE_LIMITED` | Too many requests. | **Honor `Retry-After` header**, then retry with backoff. |
| `500` | `INTERNAL_ERROR` | Server error. | Retry with exponential backoff; report `requestId`. |
| `502` | `BAD_GATEWAY` | Upstream provider returned an unexpected error (auth, billing on their side). | Usually transient — retry with backoff. |
| `503` | `SERVICE_UNAVAILABLE` | Temporarily unavailable — usually the safety/balance check service is degraded. | Retry with backoff; honor `Retry-After`. |

### Recovery Heuristics

1. **`402` is Pollinations' signature failure mode.** Other providers use 401
   (auth) or 429 (quota); Pollinations adds a third class — authenticated but
   out of currency. The backend should map 402 to "budget exhausted" and
   trigger model/provider fallback, *not* retry and *not* re-auth.
2. **Retry-idempotent generation.** Keep endpoint, body, query parameters,
   and `seed` unchanged on retry — the platform routes the retry to the
   generation already in progress, or returns the completed cached result
   instead of starting (and billing) a second generation.
3. **Watch `429` and `503`** — both carry `Retry-After`. `502` from
   Pollinations means upstream provider trouble.
4. **Moderation is an error class, not a filter flag** — content blocks
   surface as `422 content_policy_violation`, never as a sanitized response.

---

## Rate Limiting & Concurrency

Pollinations does not publish a fixed RPM/TPM matrix like Mistral's
priority tiers — limits are **pollen-budget-driven** plus abuse-driven:

| Surface | Limiting mechanism |
|---|---|
| Authenticated `sk_` keys | Pollen balance / per-key budget (`402` when exhausted) + `429 RATE_LIMITED` under burst |
| Budgeted API keys | Hard-capped by the key's budget — check `GET /account/balance` |
| Legacy raw `pk_` keys | 1 pollen per IP per hour (do not use for new integrations) |
| Legacy anonymous (no key) | IP-based rate limits on the legacy text/image surfaces (see Appendix A) |
| Realtime WebSocket | Requires positive balance at connect; settles once at close |

### Client-Side Limiter (suggested)

```python
import asyncio, time

class PollinationsLimiter:
    """Conservative shared limiter until real 429 telemetry says otherwise."""

    def __init__(self, max_concurrent: int = 4, min_interval: float = 0.25):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._last = 0.0
        self._min_interval = min_interval
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        await self._sem.__aenter__()
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        return self

    async def __aexit__(self, *exc):
        return await self._sem.__aexit__(*exc)
```

### Best Practices (from the official docs)

- **One key per app.** Child keys scope budget and permissions independently —
  easier to audit, easier to revoke without touching production. Give
  AgentKthx a dedicated child key with its own budget so a runaway agent loop
  cannot drain the whole account.
- Prefer `health.success_rate` on the model card when choosing between
  equivalent models — it reflects live upstream health, not a static tier.

---

## Multimodal Content Handling

### Vision (Image Input)

Models advertising `image` in `input_modalities` (e.g. `openai/gpt-5.4-nano`,
`anthropic/claude-sonnet-4.6`, `google/gemini-3.7-flash`) use the standard
OpenAI multimodal content shape:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What is in this image?"},
    {"type": "image_url", "image_url": {"url": "https://example.com/cat.jpg"}}
  ]
}
```

- `image_url.url` accepts a **public URL or a `data:image/...;base64,…` data
  URI** — no redirects, no private hosts (`400 invalid_image_url` otherwise).
- `detail: "high"` for fine-grained reasoning, `"low"` for quick takes.

### Audio & Video Input

Models listing `audio`/`video` in `input_modalities` accept `input_audio` or
`video_url` content parts. `video_url.url` takes a public `https://` URL or a
`data:video/...;base64,...` data URI; Gemini models additionally accept
YouTube and `gs://` URLs. Video usage is metered from the provider's reported
`video_tokens` detail.

### Media Models Inside Conversations

Image, video, audio, and 3D models that advertise chat endpoints accept a
text prompt directly through `/v1/chat/completions` — only the **last user
message** is used; history, instructions, and text-generation settings are
ignored. The response is assistant text containing a Markdown image embed
(images) or Markdown link (audio/video/3D), followed by the plain public file
URL; the URL is also in the `Link` header. `usage` is `null`/omitted.

```bash
curl https://gen.pollinations.ai/v1/chat/completions \
  -H "Authorization: Bearer $POLLINATIONS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"flux","messages":[{"role":"user","content":"A lighthouse at dawn"}]}'
```

Validation quirks: empty prompts, malformed Unicode, and prompts consisting
only of `.` or `..` return HTTP 400. Image parts in chat are the **source
images** for image models and the **start frame** for video models that list
`image` under `input_modalities` — any other attachment type returns 400.

### Safety Controls

`safe` (body param or query param) and the `Pollinations-Safe` header accept a
comma-separated list: `privacy`, `secrets`, `sexual`, `violence`, `shield`;
shorthands `true` = `privacy,secrets` and `nsfw` = `sexual,violence`.
**Defaults to off.** moderation blocks surface as
`422 content_policy_violation`.

---

## Thinking & Reasoning Configuration

Control reasoning via `reasoning_effort` on models advertising reasoning
support (card flag `reasoning: true` / capability `reasoning`):

```bash
curl https://gen.pollinations.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $POLLINATIONS_API_KEY" \
  -d '{
    "model": "openai",
    "reasoning_effort": "high",
    "messages": [{"role": "user", "content": "Prove there are infinitely many primes."}]
  }'
```

- Values follow the OpenAI convention (`minimal`/`low`/`medium`/`high` on
  models that expose the levels).
- Managed prompt agents accept `reasoning.effort` (Responses) and
  `reasoning_effort` (Chat Completions).
- **Reasoning summaries are not supported** — a non-null `reasoning.summary`
  returns HTTP 400.
- Interaction with sampling: on Claude models, `temperature`/`top_p` are
  disabled while `reasoning_effort` is active; newer Claude and Gemini models
  may omit sampling controls entirely (see the card's
  `supported_parameters`).
- Reasoning output is billed separately on models whose card lists
  `completionReasoningPrice` (visible in `/account/usage` as
  `output_reasoning_tokens`).

---

## Sampling Parameters & Prompt Caching

### Per-Model Parameter Allowlist

Pollinations publishes `supported_parameters` per model card and treats
unverified controls as **omitted, not silently dropped** — but "omitted"
means provider fallback routes may behave differently. Practical rules:

| Concern | Behavior |
|---|---|
| `openai/gpt-5.4` | Chat transform **removes** sampling controls — don't send them |
| `openai/gpt-oss-20b` | Forwards `temperature` and `top_p` |
| `anthropic/claude-sonnet-4.6` | `temperature` and `top_p` mutually exclusive (`temperature` wins); both disabled under `reasoning_effort` |
| `response_format` | Sonnet 4.6 supports `json_schema`, **not** `json_object` |
| Gemini `:search` variants | Built-in tools present — affects caching (below) |

### Prompt Caching (`cache_control`)

On **Gemini, Claude, and Nova** models, mark the end of a static prefix with
`cache_control` on the final content **block** (not the message):

```json
{
  "model": "google/gemini-2.5-flash-lite",
  "messages": [
    {
      "role": "system",
      "content": [
        {"type": "text", "text": "<large static prompt>",
         "cache_control": {"type": "ephemeral"}}
      ]
    },
    {"role": "user", "content": "<dynamic message>"}
  ]
}
```

Everything before the marker must be byte-identical across requests. First
request creates the cache (`usage.cache_creation_input_tokens`); repeats
report `usage.prompt_tokens_details.cached_tokens` at the discounted rate.

| Family | Minimum prefix | Cache TTL | Create cost | Hit cost | Notes |
|---|---|---|---|---|---|
| **Gemini** | ~2,048 tokens (~4,096 on Gemini 3) | 1 hour | Standard input + storage fee ($1/1M cached on Flash, $4.50 on Pro) | ~10% of input | **Not cached when tools are present** (built-in tools included — use `"tools": []` or a JSON `response_format`); `flash-lite` models cache by default |
| **Claude** | 512 tokens (`claude-fable-5`, `claude-opus-5`), 1,024 (`claude-sonnet-4.6`), higher on others | ~5 min, refreshed on hit | 1.25× input | 10% of input (2.5% on `claude-fable-5.1`) | Tool definitions are cacheable; fable-5.1 rejects forced tool choice |
| **Nova** | ~1,000 tokens (max 20K cacheable) | ~5 min | Free | 25% of input | `nova` and `nova-fast` only |

**Break-even guidance**: Gemini's storage fee means caching pays off only
with frequent reuse — roughly a dozen reuses/hour on the cheapest models.
AgentKthx's fixed persona/system prefix easily clears this on long sessions.

### Responses-API Cache Extension

Models advertising `/v1/responses` also accept OpenAI's cache controls: set
`prompt_cache_options.mode` to `explicit` and place
`prompt_cache_breakpoint: {"mode": "explicit"}` on the content block ending
each stable prefix (**up to four**). Chat requests adapted to Responses
preserve these markers; the existing `cache_control: {"type": "ephemeral"}`
marker is translated to the same explicit breakpoint. Managed prompt agents
apply an explicit request without caller markers to their configured static
prompt.

---

## Media Generation Services (Image / Video / Audio / Embeddings / 3D)

This is Pollinations' differentiator vs. every other backend in AgentKthx's
stable: text, images, video, speech, music, transcription, realtime voice,
and embeddings behind **one key and one SDK**.

### Images

| Endpoint | Shape | Default model |
|---|---|---|
| `GET /image/{prompt}` | Query params, returns JPEG/PNG/SVG bytes | `tongyi-mai/z-image-turbo` |
| `POST /v1/images/generations` | OpenAI Images compatible | `black-forest-labs/flux.1-schnell` |
| `POST /v1/images/edits` | OpenAI Images Edits compatible (JSON or multipart) | `black-forest-labs/flux.1-schnell` |

Key parameters (`GET` form): `model`, `width`/`height` (defaults 1024; some
models require multiples of 16/32 — see card), `seed` (`-1` = random, default
`0`), `quality` (`low|medium|high|hd`, GPT-image family), `transparent`
(GPT-image family only), `resolution` (`1k|2k|360p|…` for models advertising
`resolutions`), `image` (reference image URLs, `\|`-separated — edit or style
reference), `safe`.

OpenAI-compat responses default to `response_format: "b64_json"`; set
`"url"` to receive a stored `media.pollinations.ai` URL (fetching it never
triggers a new generation). `n` is currently capped at 1.

Representative models: `black-forest-labs/flux.1-schnell`, `flux.1.1-pro`,
`flux.2-pro/flex/max/klein-4b`, `flux.1-kontext-pro` (editing),
`google/gemini-3-pro-image`, `openai/gpt-image-2`,
`bytedance/seedream-4.0/5.0-pro`, `ideogram-ai/ideogram-v4-*`,
`qwen/qwen-image-3`, `recraft/recraft-v4.1-vector`, `amazon/nova-canvas-v1`.
Browse live cards at `GET /image/models`.

```bash
curl -X POST "https://gen.pollinations.ai/v1/images/generations" \
  -H "Authorization: Bearer $POLLINATIONS_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"a serene mountain landscape at sunset","model":"black-forest-labs/flux.1-schnell","size":"1024x1024"}'
```

### Video — `GET /video/{prompt}`

Returns MP4. `model` (default `google/veo-3.1-fast`), `duration` (1–120 s,
per-model ranges: veo 4/6/8 s, seedance-2.0 4–15 s, wan 2–15 s,
nova-reel 6–120 s in multiples of 6), `aspectRatio` (`16:9`/`9:16`,
some models 21:9/4:3/1:1/3:4), `audio` (model-dependent), `image[0]` start
frame / `image[1]` end frame, plus `reference_images` / `reference_videos` /
`reference_audios` for guidance on models with the capability. Seed supported
on a subset. Full capability flags live in `video_capabilities` on
`/image/models`.

### Audio

| Endpoint | Purpose | Compatibility |
|---|---|---|
| `POST /v1/audio/speech` | TTS, music, SFX, dialogue | OpenAI TTS compatible |
| `POST /v1/audio/speech/with-timestamps` | TTS + word timings | Pollinations extension |
| `POST /v1/audio/transcriptions` | STT | **Whisper compatible** |
| `POST /v1/audio/voice-changer` | Voice conversion | — |
| `POST /v1/audio/voice-isolator` | Speech isolation | — |
| `GET /audio/{text}` | TTS via GET | — |

Speech essentials: `model` (default OpenAI-voice set; music models
`elevenlabs/music-v2`/`v2.5`, `stability-ai/stable-audio-3*`,
`google/lyria-3-clip-preview` — Lyria returns a fixed 30 s MP3), `voice`
(default `alloy`; 100+ preset voices incl. multilingual `af_/am_/bf_/zf_`
families), `response_format` (`mp3` default; `opus`, `aac`, `flac`, `wav`,
`pcm`), `input` ≤ 10,000 chars. Multi-speaker dialogue: model
`elevenlabs/eleven-v3:dialogue` with one `<voice>: <text>` turn per line (≤10
voices, ≤2,000 chars).

Transcription: multipart/form-data (`file`, `model=openai/gpt-audio-mini`,
`response_format` in `json|verbose_json|text|srt|vtt`, `temperature`).
**Max file size 25 MB.**

### Embeddings — `POST /v1/embeddings`

OpenAI-compatible. Batch input ≤ 32 strings. Models:
`google/gemini-embedding-2` (text, image, audio, video input), 
`openai/text-embedding-3-small` (≤1536 dims), `openai/text-embedding-3-large`
(≤3072), `cohere/embed-v4.0` (text + one image per input; 256/512/1024/1536
dims), `qwen/qwen3-embedding-8b` (≤4096). Retrieval hints: `task_type` with
Gemini, `input_type` (`query`|`document`) with Cohere. Community embedding
models bill token-only at the owner-set `promptTextPrice`. ⚠️ Gemini GA
migration: `gemini-embedding-2` vectors from the preview era must be
re-embedded before comparing with new GA vectors.

### 3D & Media Storage

- `GET /3d/models` — 3D generation catalog; media models advertise chat
  endpoints that return Markdown links to generated assets.
- `POST https://media.pollinations.ai/upload` (multipart) — arbitrary media;
  returns a public `https://media.pollinations.ai/<id>` URL usable anywhere a
  remote image/audio/video URL is accepted. **30-day lifecycle** from upload
  or latest refresh; fetching the body refreshes the lifecycle only when the
  object is ≥15 days old; metadata/HEAD do not refresh. Optional `tags=`
  publish to public galleries (`GET /media?tag=...`).

---

## The Pollen Economy & Free Access

### What Pollen Is

**Pollen** is Pollinations' internal billing currency. Every generation
(text tokens, images, video seconds, audio characters/seconds) debits pollen
at the per-model rates published on each card (per-token internally,
per-1M on the dashboard). There are **two wallets**:

| Wallet | Internal name | Where it comes from |
|---|---|---|
| **Quest Pollen** | `tier` | Earned free by completing quests (account setup, linking GitHub, integrations, community participation). Claimed on the dashboard. |
| **Paid Pollen** | `pack` | Purchased. |

### Honest Answer on the "Free Tier"

Unlike Cerebras' expiring trial credits, **Quest Pollen is a renewable free
allowance** — but it is *quest-driven*, not an unlimited no-cost tier. What
is genuinely free:

1. **Legacy anonymous surface** (no key at all): `text.pollinations.ai`
   serves the anonymous-tier `openai-fast` model (GPT-OSS 20B, reasoning,
   tool-capable) and `image.pollinations.ai/prompt/...` serves basic image
   generation, IP-rate-limited. Fine for smoke tests, not for agent loops.
2. **Quest Pollen** — free credits earned through the quest catalog; enough
   for meaningful personal use of cheap text models (e.g. `gpt-5.4-nano` at
   0.15 pollen/1M prompt tokens is very cheap per request).
3. **Zero-cost community models** — community publishers can list models at
   zero cost (16 live on 2026-09-28). ⚠️ They are NOT marked with price `0`:
   the encoding is a currency-only `pricing` dict (`{"currency": "pollen"}`
   with NO price fields — the `:free`/`-free` id variants). Detect them on
   `GET /v1/models` with `set(pricing.keys()) <= {"currency"}` plus
   `community: true` — filtering for zero-valued price fields matches
   NOTHING on the live feed.
4. **Public model catalogs and media reads** — zero-cost metadata.

⚠️ Models can be marked `paidOnly`, which restricts them to Paid Pollen —
Quest Pollen is rejected even when its balance is positive. Community
publishers set this flag when upstream bills per use. The flag surfaces as
`paid_only` on the bare `GET /models` feed (`True` = paid tier;
absent/`False` = free TIER), and it also SCOPES the keyed `/v1/models`
catalog: a Bearer key's model list silently excludes every `paid_only`
model (see [Catalog entitlement scoping](#catalog-shape) above).

### Balance & Usage Inspection

```bash
curl https://gen.pollinations.ai/account/balance \
  -H "Authorization: Bearer $POLLINATIONS_KEY"
```

```json
{
  "balance": 412.75,
  "accountBalance": {
    "total": 412.75,
    "tier": 300.0,
    "paid": 112.75
  }
}
```

- `balance` — what *this caller* may spend: budgeted keys see their key
  budget; sessions and unbudgeted keys see the account total.
- `accountBalance` — included **only** when the caller has `account:usage`
  scope (or is a dashboard session). Unbudgeted keys without that scope get
  `403`; budgeted keys without it see only their own budget (the account
  wallet is deliberately not leaked).
- `accountBalance.tier` = Quest Pollen remaining, `paid` = Paid Pollen.
- `GET /account/quests` — quest catalog with `state`
  (`available|completed|coming_soon`), `rewardAmount`, and
  `balanceBucket` (`tier`|`pack`). Claiming is dashboard-only.
- `GET /account/usage` — per-request records with
  `meter_source: 'tier' | 'pack'`, token counts, `cost_usd`,
  `response_time_ms`.

### Cost Control for AgentKthx

1. Create a **child key with a fixed budget** for the agent runtime; a
   runaway loop hits `402` at the key budget instead of draining the account.
2. Default to cheap text models (`openai/gpt-5.4-nano`, `z-ai/glm-5.3-flash*`,
   `deepseek/deepseek-v4-flash`) and escalate only on demand.
3. Keep media generation behind explicit user actions — images and video are
   the expensive classes.
4. Prefer `promptCachedTokens`-friendly request shapes (stable system prefix)
   on Gemini/Claude/Nova — cached input is 10–25% of full price.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import BackendType

class PollinationsBackend(OpenAICompatibleBackend):
    """Pollinations unified gateway backend (OpenAI-compatible surface)."""

    BACKEND_TYPE = BackendType.CLOUD
    NAME = "pollinations"
    is_cloud = True

    DEFAULT_BASE_URL = "https://gen.pollinations.ai/v1"
    DEFAULT_MODEL = "openai/gpt-5.4-nano"      # platform default, cheapest tier
    FALLBACK_MODEL = "z-ai/glm-5.3-flash"      # cheap alternate family
    ENV_API_KEY = "POLLINATIONS_API_KEY"
    ENV_BASE_URL = "POLLINATIONS_BASE_URL"

    # Context-length 400 recovery (ARCH-03 shared path) — Pollinations'
    # 400 bodies have NOT been probed for context-wording yet. Leave the
    # regexes disabled (None) until probe_pollinations.py captures a real
    # body; a false positive here would truncate healthy conversations.
    _CONTEXT_LENGTH_MAX_PATTERN: str | None = None
    _CONTEXT_LENGTH_INPUT_PATTERN: str | None = None
    _CONTEXT_LENGTH_TOOL_PATTERN: str | None = None
    _MAX_TOKENS_CAP_DIVISOR: int = 32
    _CONTEXT_SAFETY_MARGIN: int = 2048
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
            "temperature": 0.7,
            "max_tokens": 8192,
            # Model cards expose context_length — prefer live value via
            # list_models() and cache it; this is the offline fallback.
            "context_length": 128_000,
        }
```

Abstract methods that work out of the box: `generate()`, `generate_stream()`,
`list_models()`, `test_tool_support()` — plus the four wire-format hooks
(`_get_chat_completions_url`, `_get_auth_headers`, `_iter_sse_lines`,
`_get_model_defaults`). The SSE path is byte-identical to OpenAI, so no
streaming overrides are needed.

### 2. Model ID Handling — Slash-Prefixed IDs

Pollinations uses `provider/model` ids — the **same pattern as OpenRouter**.
Reuse the OpenRouter plugin's model-id normalization:

```python
def normalize_model_id(self, model: str) -> str:
    """Pass through provider/model; resolve bare aliases conservatively."""
    if "/" in model or model in ("flux",):   # aliases mostly single-segment
        return model
    # 'openai' -> 'openai/gpt-5.4-nano' style resolution via /v1/models alias map
    return self._alias_map.get(model, model)
```

Build `_alias_map` once from `GET /v1/models` (`aliases[]` per card) so the
user can type `openai`, `glm`, or `mistral-small-4`. Add a `pollinations`
provider family to `core/model_family_config.py` keyed on the `provider/`
prefix so family detection does not misclassify these as local model names.

### 3. Health-Aware Fallback (Pollinations Exclusive)

No other provider gives AgentKthx live per-model success telemetry. Use it:

```python
async def healthy_fallbacks(self, limit: int = 3) -> list[str]:
    cards = await self.list_models()
    text = [c for c in cards
            if c.get("category") == "text"
            and not c.get("community", False)
            and c.get("tools", False)]
    text.sort(key=lambda c: (-c["health"]["success_rate"],
                             c["pricing"]["promptTextTokens"]))
    return [c["id"] for c in text[:limit]]
```

Order the fallback chain by `health.success_rate` descending, then price
ascending — this keeps the agent off unhealthy upstreams without any static
model list.

### 4. Error Mapping — 402 Is the Headline

```python
ERROR_MAP = {
    401: "auth_error",          # bad/missing key — stop, don't retry
    402: "budget_exhausted",    # pollen gone — fall back, don't retry
    403: "permission_error",    # scopes / paidOnly model
    422: "content_or_param",    # check error.code == content_policy_violation
    429: "rate_limited",        # honor Retry-After, then retry
    500: "server_error",        # backoff retry
    502: "upstream_error",      # backoff retry (transient)
    503: "degraded",            # backoff retry (safety/balance service)
}
```

Parse the Pollinations envelope, not OpenAI's: `body["error"]["code"]` carries
the machine-readable code; `body["error"]["requestId"]` is what support asks
for. **`402` must trigger provider fallback** — retrying a 402 just burns
latency.

### 5. Streaming Strictness

Unlike OpenAI, Pollinations fails requests whose completed text streams lack
a valid usage chunk. `OpenAICompatibleBackend` treats a stream as successful
once `[DONE]` arrives; consider adding a warning (not an error) when usage is
absent on this backend, since the platform already rejected malformed cases
server-side — absence after `[DONE]` implies a proxy mangled the stream.

### 6. Plugin Manifest

```json
{
  "name": "pollinations",
  "version": "0.1.0",
  "description": "Pollinations unified gateway — OpenAI-compatible text, vision, images, audio, embeddings",
  "type": "backend",
  "backend_class": "agentkthx.plugins.pollinations.backend.PollinationsBackend",
  "env": {
    "POLLINATIONS_API_KEY": {
      "required": false,
      "description": "sk_ secret key from enter.pollinations.ai/keys. Omit to use the legacy anonymous surface (text-only, rate-limited)."
    },
    "POLLINATIONS_BASE_URL": {
      "required": false,
      "default": "https://gen.pollinations.ai/v1"
    }
  },
  "capabilities": {
    "chat": true,
    "streaming": true,
    "tools": true,
    "vision": true,
    "json_mode": "model-dependent",
    "reasoning_effort": true,
    "prompt_caching": "gemini/claude/nova families",
    "image_generation": true,
    "video_generation": true,
    "tts": true,
    "transcription": true,
    "embeddings": true,
    "realtime": true
  },
  "model_family_hints": ["openai/", "anthropic/", "google/", "z-ai/",
    "deepseek/", "qwen/", "mistralai/", "meta/", "community/"],
  "free_tier": "legacy anonymous surface + Quest Pollen (quest-earned credits)",
  "homepage": "https://pollinations.ai",
  "docs": "https://gen.pollinations.ai/docs"
}
```

Note `POLLINATIONS_API_KEY` is **optional** — this is the only backend in
AgentKthx that can run with no key at all (legacy anonymous text surface,
Appendix A), which makes it the natural zero-config bootstrap backend and the
bottom rung of the fallback chain.

### 7. Test Plan Sketch

```bash
# 1. Catalog reachability (no auth needed)
curl -s https://gen.pollinations.ai/v1/models | python3 -c \
  "import json,sys; d=json.load(sys.stdin)['data']; print(len(d), 'models')"

# 2. Key validity + cheapest chat call
curl -s https://gen.pollinations.ai/v1/chat/completions \
  -H "Authorization: Bearer $POLLINATIONS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/gpt-5.4-nano","messages":[{"role":"user","content":"ping"}]}' \
  | python3 -m json.tool | grep -E '"content"|prompt_tokens'

# 3. Tool support probe — expect finish_reason: tool_calls
# 4. Stream probe — assert usage chunk arrives before [DONE]
# 5. 402 path — run against a zero-balance key; assert fallback fires
# 6. Balance probe
curl -s https://gen.pollinations.ai/account/balance \
  -H "Authorization: Bearer $POLLINATIONS_API_KEY"
```

### 8. Where Pollinations Fits in AgentKthx

| Role | Rationale |
|---|---|
| **Zero-config bootstrap** | Only provider that works with no key (legacy anonymous) — good first-run experience |
| **Aggregator fallback** | One key → dozens of vendor families; complements OpenRouter |
| **Media generation backend** | Only stable provider for images/video/TTS/embeddings under one credential |
| **Cheap workhorse tier** | `gpt-5.4-nano`/`glm-5.3-flash` pollen prices are far below typical USD-priced equivalents |
| **Telemetry source** | `health.success_rate` enables live-health fallback ordering no other backend offers |

---

## Troubleshooting Matrix

| Symptom | Likely cause | Diagnosis / Fix |
|---|---|---|
| `401 UNAUTHORIZED` on all calls | Key missing, malformed, or revoked | Verify `Authorization: Bearer sk_...` header; regenerate at enter.pollinations.ai/keys. GET endpoints also accept `?key=` |
| `402 PAYMENT_REQUIRED` with valid key | Pollen exhausted — key budget or both wallets | `GET /account/balance`; budgeted keys see only their own budget. Top up, claim quests (dashboard), or switch backend. **Never retry a 402** |
| `403` on `/account/balance` | Unbudgeted key without `account:usage` scope | Expected behavior — the endpoint deliberately hides the account wallet. Use a key with `account:usage` for wallet queries |
| `403` on a specific model | `paidOnly` model, caller holds Quest Pollen | Pick a non-`paidOnly` model or pay with Paid Pollen |
| `404` on model id | Typo, or community model missing `community/owner/` prefix | Re-fetch `/v1/models`; prefer full `provider/model` ids over aliases |
| `422 content_policy_violation` | Moderation blocked prompt or output | Rephrase; do not retry unchanged. `safe` param defaults off — this is upstream moderation, not the client flag |
| `429` without obvious overuse | Burst limits or exhausted per-IP allowance on legacy surface | Honor `Retry-After`; serialize via the client limiter; move to key auth if on anonymous surface |
| Stream ends without usage chunk | Proxy or SDK mangling SSE | Pollinations fails malformed streams server-side, so absence after `[DONE]` implies transport tampering — log and warn; retry with `stream_options.include_usage` |
| `400 invalid_image_url` on vision | Image URL redirects, private, or credentialed | Pollinations refuses redirects by design — pre-resolve to a direct public URL or inline a data URI |
| Media URL died after ~30 days | Media storage lifecycle expiry | Re-upload; note body fetches refresh the lifecycle only when ≥15 days old |
| `json_object` rejected on a Claude model | `response_format.json_object` unsupported | Sonnet 4.6 supports `json_schema` instead — consult the card's `supported_parameters` |
| Sampling controls ignored | Model's Chat transform omits them (`openai/gpt-5.4`) | Check `supported_parameters`; Pollinations omits unverified controls by contract |
| Cache never hits on Gemini | Tools present in request (even `"tools"` with search variants) | Send `"tools": []` or set `response_format`; or switch to `flash-lite` models which cache by default |
| Cache create cost higher than expected | Gemini storage fee | $1/1M cached tokens on Flash, $4.50 on Pro — only worth it with ~dozen+ reuses/hour |
| `reasoning.summary` → 400 | Summaries unsupported platform-wide | Omit the field; reasoning arrives as tokens, not summary text |
| WebSocket realtime rejects | Zero balance, or browser tried header auth | Keep balance positive; browsers must use `?key=pk_...` query auth |
| Model replies to itself over realtime audio | Mic re-capturing model output | Route model audio through an `<audio>` element as the echo-cancellation reference |
| Retry of image gen billed twice | Retry changed seed/params | Keep endpoint, body, query params, and seed identical — the platform then dedupes against the in-flight/cached generation |
| Backend works but every model errors `502` | Upstream provider outage at Pollinations | Wait/backoff; check model `health.status` and pick an alternate family |

---

## Appendix: Legacy Endpoints (text/image.pollinations.ai)

The pre-gateway surfaces remain operational and are what most third-party
tutorials still reference. AgentKthx should treat them as the **anonymous /
bootstrap tier only** — the unified gateway is canonical for everything else.

### `https://text.pollinations.ai`

| Endpoint | Behavior |
|---|---|
| `GET /{prompt}` | Plain-text completion; query params mirror the gateway (`model`, `system`, `seed`, `json`, `temperature`, `stream`, `safe`) |
| `POST /` (and `/openai`) | OpenAI Chat Completions request/response |
| `GET /models` | Cards with a `tier` field — e.g. the anonymous tier currently serves `openai-fast` (GPT-OSS 20B; `reasoning: true`, `tools: true`, aliases `openai`, `gpt-oss`, `gpt-oss-20b`) |

Anonymous-tier cards look like:

```json
{
  "name": "openai-fast",
  "description": "GPT-OSS 20B Reasoning LLM (OVH)",
  "reasoning": true,
  "tier": "anonymous",
  "community": false,
  "input_modalities": ["text"],
  "output_modalities": ["text"],
  "tools": true,
  "aliases": ["openai", "gpt-oss", "gpt-oss-20b", "ovh-reasoning"],
  "vision": false,
  "audio": false
}
```

### `https://image.pollinations.ai`

| Endpoint | Behavior |
|---|---|
| `GET /prompt/{prompt}` | Image generation via GET; classic `width`/`height`/`seed`/`model`/`nologo`/`safe` query params |
| `GET /models` | Live image model list (e.g. `sana`) |

### Migration Notes

- Legacy ids are **short names** (`openai-fast`, `flux`), not
  `provider/model` — a backend supporting both surfaces needs id mapping.
- No pollen accounting on the anonymous tier; limits are IP-based and
  undocumented in detail (deliberately).
- Raw `pk_` keys — the historical browser-auth mechanism — are legacy and
  capped at 1 pollen/IP/hour; do not mint new ones. Browser apps should use
  BYOP (OAuth 2.1) instead.
- Feature parity is not guaranteed: new capabilities (Responses API,
  realtime, community models) are gateway-only.

---

## Appendix: OpenAI Wire-Format Deltas

What an OpenAI-hardened client will notice on Pollinations:

| Area | OpenAI | Pollinations |
|---|---|---|
| Model ids | `gpt-5.4-nano` | `provider/model` (`openai/gpt-5.4-nano`); aliases resolvable |
| Error envelope | `{"error": {"message", "type", "code"}}` | `{"status", "success": false, "error": {"code", "message", "timestamp", "details", "requestId"}}` |
| Payment failure | `429`/`insufficient_quota` | **`402 PAYMENT_REQUIRED`** |
| Stream usage strictness | Usage optional | Completed text streams **must** carry valid usage or fail; media-model streams carry `usage: null` |
| Responses API | Persistent (`store`, `previous_response_id`) | **Stateless only** — `store:false` required, continuation fields rejected |
| Reasoning summaries | Supported on o-series | **Not supported** — `reasoning.summary: non-null` → 400 |
| Extra endpoints | — | `GET /text/{prompt}`, `POST /text`, `GET /image/{prompt}`, `GET /video/{prompt}`, `GET /audio/{text}`, `POST /alpha/decisions` |
| Extra auth | Bearer header | Bearer header **or `?key=` query param** on GET endpoints/browser WS |
| Safety flag | Moderation endpoint (separate) | Inline `safe` param / `Pollinations-Safe` header; blocks = `422 content_policy_violation` |
| Caching controls | Automatic | Explicit `cache_control` blocks (Gemini/Claude/Nova) + `prompt_cache_breakpoint` (Responses path) |
| Pricing | USD per 1M tokens | **Pollen** per token on cards (per-1M on dashboard); `cost_usd` reported in account usage |
| Media in chat | — | Image/video/audio/3D models callable via chat completions; Markdown embed + `Link` header response, `usage: null` |
| Model telemetry | — | `health` block (`status`, `success_rate`, `requests`) per card |
| Parallel tool calls | `parallel_tool_calls` | Supported where the card lists it; some upstreams (e.g. fable-5.1) reject forced tool choice |
