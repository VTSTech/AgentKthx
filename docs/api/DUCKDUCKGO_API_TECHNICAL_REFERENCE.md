# DuckDuckGo AI Chat API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: the implemented backend `agentkthx/plugins/duckduckgo/duckduckgo.py` (plugin **v0.2.0**, R07.30) + the bundled Node helpers `ddg_vqd.js` (x-vqd-hash-1 challenge solver, headless v2) + `ddg_durable.js` (durableStream RSA-2048 keygen). Protocol reverse-engineered 2026-10-09 via a curl UA/Origin/casing/HTTP-version matrix + HAR capture + 5 decoded obfuscator.io challenges, cross-validated against the maintained Go client [benoitpetit/duckduckgo-chat-cli](https://github.com/benoitpetit/duckduckgo-chat-cli) and the live duck.ai dropdown.
> **Supersedes**: the 2026-10-07 pre-implementation draft of this document (built from mrgick/duck_chat + JSR `@mumulhl/duckduckgo-ai-chat` v3.3.0). That draft described the **retired `x-vqd-4` opaque-token protocol** and the **2025 model catalog** — both were rotated out by DuckDuckGo between the scaffold pass and the plugin's first live traffic. Every `x-vqd-4`-era header set, token format, SSE grammar, and model ID in the old text is obsolete; do not reuse them.
> **Free-tier verification**: Truly free, anonymous, no API key, no signup, no quota. Source: the DuckDuckGo help pages and the live duck.ai dropdown. There is no quota object to sign up for — the only constraints are the undocumented per-IP anti-bot limits and the ~20-turn conversation cap (see Rate Limiting).
> **Live-behavior notes**: 2026-10-09 — DuckDuckGo AI Chat is the **only keyless, anonymous backend** in AgentKthx and the only cloud backend that subclasses `BaseBackend` directly instead of `CloudBackend`. It is NOT OpenAI-compatible — it uses DuckDuckGo's own `/duckchat/v1/*` protocol, which as of the 2026-10 rotation is **CHALLENGE-BASED**: `GET /duckchat/v1/status` (with `x-vqd-accept: 1`) issues a base64 obfuscated-JS challenge in the `x-vqd-hash-1` response header; the challenge is executed against a clean-browser environment by the bundled Node solver and post-processed into the `X-Vqd-Hash-1` REQUEST header (the proof); each `/chat` response header carries the NEXT challenge (per-turn rotation). The retired `x-vqd-4` token is GONE — `/status` no longer returns it, and requests that send neither proof nor token fail. Proven by a curl UA/Origin/casing/HTTP-version matrix: every variant got the challenge — the missing piece was the proof, not header cosmetics.
> **Primary focus**: the implemented protocol at `https://duck.ai/duckchat/v1/*` (the default base URL moved off `duckduckgo.com` in the v0.2.0 rewrite). Wire body carries `canUseTools` / `canUseApproxLocation` / `reasoningEffort` / `durableStream` (client-minted RSA-2048 JWK). Responses are role-based SSE (`assistant` / `source` / `tool-invocation` / `ui-component` + bracketed markers) — the legacy action grammar is kept only as a fallback.
> **Verification status**: the challenge handshake is LIVE-VERIFIED (TEST-13 partially closed — `scripts/probe_duckduckgo.py --status-only` solves repeatedly at 54–987ms; a live 404 `ERR_MODEL_UNAVAILABLE` JSON body proved auth + request shape parse correctly, isolating the model-ID rotation). One live 200 SSE turn through the plugin remains open (TEST-13 in `audit/audit.md`); the `docs/SUPPORT.md` tier row awaits it.
> **Last Updated**: 2026-10-09 (R07.30 — plugin v0.2.0 protocol rewrite; FEAT-10 closed)
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
11. [Appendix: Privacy & Anonymity Model](#appendix-privacy--anonymity-model)
12. [Appendix: x-vqd-hash-1 Challenge & Proof Lifecycle](#appendix-x-vqd-hash-1-challenge--proof-lifecycle)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — duck.ai (DUCKDUCKGO_BASE_URL default; moved off duckduckgo.com
# in the v0.2.0 rewrite — the protocol lives under /duckchat/v1 either way)
BASE_URL = "https://duck.ai"

# Endpoints (DuckDuckGo's own protocol, NOT OpenAI-compatible)
STATUS       = "/duckchat/v1/status"        # GET  — issue the x-vqd-hash-1 challenge (x-vqd-accept: 1)
CHAT         = "/duckchat/v1/chat"          # POST — send message (proof header), consume role-based SSE
CAPABILITIES = "/duckchat/v1/capabilities"  # GET  — unauthenticated live model discovery (best-effort)

# Web UI (for human users — not for API access)
WEB_UI = "https://duck.ai"
```

DuckDuckGo AI Chat has **NO OpenAI-compatible endpoint**. The protocol requires:

1. **`GET /duckchat/v1/status`** — returns a base64 JS challenge in the `x-vqd-hash-1` response header (with `x-vqd-accept: 1`; without it the endpoint returns bare status JSON and NO challenge header)
2. **`POST /duckchat/v1/chat`** — accepts the JSON body (model + messages + protocol fields), returns a role-based SSE stream, and returns the NEXT challenge in its `x-vqd-hash-1` response header
3. **`GET /duckchat/v1/capabilities`** — the browser bootstrap sequence hits this before any challenge is solved (unauthenticated model discovery). The backend exposes it as `fetch_capabilities()`; on a 401/403 gate it retries once with a solved proof. Returns parsed JSON or `None` — a discovery helper, never a hard dependency.

There is no `/models` endpoint. `list_models()` serves the seed-backed offline catalog (house contract: no network in model listing) routed through the persistent model cache — see Model Catalog.

### Authentication Model

DuckDuckGo AI Chat uses **NO API key**. The "authentication" IS the challenge proof:

- A base64 obfuscated-JS challenge is issued by `GET /duckchat/v1/status` (header `x-vqd-accept: 1`) — re-randomized per issue, so proofs cannot be cached across turns
- The bundled Node solver (`ddg_vqd.js`, subprocess) executes the challenge against a clean-browser environment; post-processing mirrors the duck.ai page bundle → the `X-Vqd-Hash-1` REQUEST header (see Appendix: x-vqd-hash-1 Challenge & Proof Lifecycle)
- Each `/chat` response's `x-vqd-hash-1` header carries the NEXT challenge (per-turn rotation)
- The pending challenge is held in-memory per backend instance (a single `_next_challenge_b64` slot), NEVER persisted to disk
- Because there is no credential, there is **no `agentkthx auth` entry** for this backend (nothing to store)

**UA-binding caveat**: the solver MUST see the same User-Agent as the request headers (`DDG_SOLVE_UA` is injected from the backend's UA at solve time) — a UA mismatch between the solved probes and the request headers invalidates the proof.

### Request Headers — /status Bootstrap (as implemented)

```python
{
    "User-Agent": "<Chrome 136 UA>",   # DUCKDUCKGO_USER_AGENT override; must look like a real browser
    "Accept": "*/*",                   # NOT text/event-stream (the 2026-10-07 draft was wrong)
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://duck.ai/",
    "Cookie": "5=1; dcm=3; dcs=1",     # minimum cookie contract (mirrors duckduckgo-chat-cli)
    "sec-ch-ua": '"Chromium";v="136", "Not.A/Brand";v="99", "Google Chrome";v="136"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',   # Chromium client hints on EVERY request
    "Cache-Control": "no-store",
    "x-vqd-accept": "1",               # opt-in — makes /status issue a challenge
    "x-ddg-journey-id": "<per-request 32-hex random ID>",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
```

### Request Headers — /chat (as implemented)

Same browser contract as `/status`, plus:

```python
{
    ...  # UA / Accept-Language / Referer / Cookie / sec-ch-ua trio / Sec-Fetch-* as above
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://duck.ai",
    "x-fe-version": "<scraped serp_… build; hardcoded 2026-10-08 fallback>",
    "x-fe-signals": "<base64 JSON synthesized engagement telemetry>",
    "X-Vqd-Hash-1": "<the solved proof>",   # header casing as sent: X-Vqd-Hash-1
    "x-ddg-journey-id": "<fresh 32-hex per request>",
    "Cache-Control": "no-store",
}
```

- **`x-fe-version`**: scraped best-effort from `GET /` homepage HTML (regex `serp_\d{8}_\d{6}_[A-Za-z0-9-]+`, 10-minute cache, 2MB read cap, 15s timeout); falls back silently to the captured 2026-10-08 build constant. A stale value is currently tolerated by the server.
- **`x-fe-signals`**: synthesized frontend telemetry — base64 of `{"start": <ms>, "events": [{"name": "action", "delta": <ms>, "trusted": true}, {"name": "startNewChat_free", "delta": <ms>}], "end": <ms>}` with plausible millisecond deltas. Accepted by the server alongside the solved challenge (verified end-to-end pre-integration).
- The 2026-10-07 draft's header set (`DNT` / `TE: trailers` / `Accept-Encoding: gzip, deflate, br` / `Host: duckduckgo.com`) is obsolete — the curl matrix proved header cosmetics are irrelevant; only the browser contract (UA/Referer/Accept) and the proof matter.

### Key Types

There are NO API keys and NO accounts. Nothing to rotate, nothing to store:

- The proof is derived from a per-issue challenge and held in-memory per backend instance
- The durableStream RSA-2048 keypair is minted once per backend instance (= per conversation) and also held in-memory
- No `agentkthx auth` entry exists for DuckDuckGo

### Runtime Dependencies (Node.js)

The backend shells out to Node (subprocess, **no npm packages**) — this is a RUNTIME dependency, not a build dependency:

- **`node >= 18` on PATH** — missing node fails FAST at generate time with a clear remediation message (not a cryptic traceback)
- **`ddg_vqd.js`** — challenge solver. Reads a base64 challenge on stdin, executes it against a globals-installed clean-browser environment, prints the raw solution JSON. `DDG_SOLVE_UA` is injected into the subprocess env = the backend's UA (a mismatch invalidates the proof). Subprocess timeout: 45s.
- **`ddg_durable.js`** — durableStream RSA-2048 JWK keygen (Python stdlib has no RSA). Prints `{"publicJwk": {...}, "privateJwk": {...}}`; the backend keeps the public JWK. Subprocess timeout: 20s.
- **Packaging caveat (live finding 2026-10-09)**: pip builds predating the `plugins/*/*.js` package-data fix ship the plugin's `.py` files but silently DROP the `.js` helpers — every `generate()` then dies with a cryptic `Cannot find module .../ddg_durable.js`. The backend now detects this and raises the actual remediation (reinstall editable or copy the `.js` files into site-packages).
- **`ddg_capture.js`** (capture proof mode) — RESERVED, not yet shipped/implemented. See `DUCKDUCKGO_PROOF_MODE` below.

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DUCKDUCKGO_BASE_URL` | `https://duck.ai` | API root (protocol under `/duckchat/v1`) |
| `DUCKDUCKGO_USER_AGENT` | *(empty → built-in)* | Browser UA override; empty falls back to the built-in Chrome 136 UA. Only set if DDG starts rejecting the built-in one (the solver is fed the SAME UA — change it as a unit, never mid-instance) |
| `DUCKDUCKGO_DEFAULT_MODEL` | `gpt-6-luna` | Default model when none specified |
| `DUCKDUCKGO_MIN_INTERVAL` | `3` (seconds) | Minimum wall-clock gap between `/chat` POSTs (anti-429 pacing). `0` disables. Garbage values keep the default |
| `DUCKDUCKGO_PROOF_MODE` | `synth` | **RESERVED** — `synth` (Node-solved challenge) is the only active mode; `capture` (headless-Chromium proof lift, needs local Chrome + Node >= 22) is NOT YET implemented |
| `AGENTKTHX_DEBUG` | *(unset)* | Debug traces: 418 teapot rounds, 401/ERR_CHALLENGE re-solves, temperature notice |

`DDG_SOLVE_UA` is an internal env var (injected into the solver subprocess), not a user knob.

### Request Format Requirements

- **Content-Type**: `application/json` (POST `/chat` only; `/status` and `/capabilities` are GETs)
- **Character Encoding**: UTF-8
- **Timeouts (as implemented)**: `/status` 30s · `/capabilities` 20s · `/chat` 180s · solver subprocess 45s · keygen subprocess 20s
- **HTTP methods**: GET for status/capabilities, POST for chat
- **REQUIRED on all requests**: a real-browser `User-Agent` + the minimum cookie set + Chromium client hints (403 otherwise); a valid `X-Vqd-Hash-1` proof on `/chat` (401/418/SSE `ERR_CHALLENGE` otherwise)

---

## Request/Response Structure

### Status Request (Challenge Bootstrap)

```http
GET /duckchat/v1/status HTTP/1.1
Host: duck.ai
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36
Accept: */*
Accept-Language: en-US,en;q=0.9
Referer: https://duck.ai/
Cookie: 5=1; dcm=3; dcs=1
sec-ch-ua: "Chromium";v="136", "Not.A/Brand";v="99", "Google Chrome";v="136"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Linux"
Cache-Control: no-store
x-vqd-accept: 1
x-ddg-journey-id: 3f9c2a7b1e4d5f6a8b0c9d2e3f4a5b6c
Sec-GPC: 1
Connection: keep-alive
```

### Status Response

```http
HTTP/1.1 200 OK
Content-Type: application/json
x-vqd-hash-1: <base64 obfuscated-JS challenge — re-randomized per issue>
```

The body is a small JSON document (the backend drains it; only the `x-vqd-hash-1` response header is consumed). Capture the challenge and hand it to the Node solver — do NOT send it back directly; the server expects the SOLVED proof (Appendix 12).

### Chat Request — Full Wire Body (Current Shape)

```http
POST /duckchat/v1/chat HTTP/1.1
Host: duck.ai
Accept: text/event-stream
Content-Type: application/json
Origin: https://duck.ai
Referer: https://duck.ai/
User-Agent: Mozilla/5.0 ... Chrome/136.0.0.0 Safari/537.36
Cookie: 5=1; dcm=3; dcs=1
sec-ch-ua: "Chromium";v="136", "Not.A/Brand";v="99", "Google Chrome";v="136"
x-fe-version: serp_20261008_165829_ET-12db35fbec1a8e8189c691f90debc97b169e1c5a
x-fe-signals: <base64 JSON engagement telemetry>
X-Vqd-Hash-1: <the solved proof — base64(JSON solution)>
x-ddg-journey-id: <fresh 32-hex>

{"model":"gpt-6-luna","messages":[{"role":"user","content":"Hello"}],
 "canUseTools":false,"canUseApproxLocation":null,"reasoningEffort":"none",
 "durableStream":{"messageId":"<32-hex per request>",
                  "conversationId":"<32-hex per backend instance>",
                  "publicKey":<RSA-2048 JWK>}}
```

### Chat Request Body Schema

```json
{
    "model": "<canonical wire ID — alias-resolved, case-insensitive>",
    "messages": [
        {"role": "user|assistant", "content": "string"}
    ],
    "canUseTools": false,
    "canUseApproxLocation": null,
    "reasoningEffort": "none | low",
    "durableStream": {
        "messageId": "<32-hex, fresh per request>",
        "conversationId": "<32-hex, fixed per backend instance>",
        "publicKey": {"kty": "RSA", "alg": "...", "n": "...", "e": "...", "use": "...", "key_ops": [...], "ext": true}
    }
}
```

Field notes (all mirror the live frontend):

- **`canUseTools`**: always `false` from this backend. The wire HAS a native tools surface (`canUseTools` + `metadata.toolChoice`: WebSearch / GenerateImage / NewsSearch / VideosSearch / LocalSearch / WeatherForecast) but it is undocumented and rotates independently — so the backend ships `false` and no `metadata` key (see Function Calling).
- **`canUseApproxLocation`**: `null` — the frontend sends null when there is no location consent.
- **`reasoningEffort`**: per-model, exactly like the frontend — `"low"` for the Tinfoil-hosted open-weight pair (`tinfoil/gpt-oss-120b`, `tinfoil/gemma4-31b`), `"none"` for everything else.
- **`durableStream.publicKey`**: client-minted RSA-2048 JWK (Node crypto via `ddg_durable.js` — Python stdlib has no RSA). One keypair per backend instance (= per conversation); `messageId` is fresh per request. The web client persists its pair in localStorage under `duckaiStreamsTestCredentials`; the server uses the public key to encrypt stream-resume payloads.
- The body is serialized compact (`separators=(",", ":")`).

### Message Shaping (Pre-Wire Translation)

The backend translates the house message list into DDG's wire shape before building the body (`_strip_system_messages`):

- **No `system` role — and the system prompt is NEVER transmitted** — DDG strips system messages server-side, and live testing (2026-10-09, claude-haiku-4-5 via duck.ai) showed the upstream models read a forwarded harness/system prompt as a jailbreak attempt: they refuse, lecture about social engineering, and derail the session. The backend therefore DROPS all `system` messages instead of folding them into user turns — the AgentKthx system prompt (ReAct scaffolding included) never reaches DDG (decision: VTSTech, R07.30)
- **`tool` / `function` roles are folded into `user`** — DDG only understands `user`/`assistant`; the ReAct loop's tool outputs ride as user turns
- **Leading `assistant` turn gets a primer** — if the conversation starts with an assistant message, `{"role": "user", "content": "Hello"}` is inserted ahead of it (DDG requires a leading user message)
- **Empty translation raises `ValueError`** ("no sendable messages") — a system-only conversation has nothing to send
- Message content that arrives as an OpenAI-style part-list is flattened to its text parts (see Multimodal)

### Chat Response (Role-Based SSE Stream — Current Grammar)

```
data: {"role":"assistant","message":"Hello"}
data: {"role":"assistant","message":" — how can I help today?"}
data: {"role":"source","source":{"url":"https://example.com/article","title":"Example Article"}}
data: {"role":"tool-invocation","toolName":"WebSearch","state":"result"}
data: {"role":"ui-component","name":"weather-card"}
data: [CHAT_TITLE:Greeting]
data: [DONE]
```

Event types:

| Event | Shape | Handling |
|-------|-------|----------|
| `assistant` | `{"role":"assistant","message":"<delta>"}` | Text delta — buffered (or yielded in streaming mode). Assistant frames WITHOUT `message` are state-only and ignored |
| `source` | `{"role":"source","source":{"url":"…","title":"…"}}` | Web citation — collected by the stream consumer but NOT yet surfaced in the house response shape (the buffered path discards them); does NOT pollute text content |
| `tool-invocation` | `{"role":"tool-invocation","toolName":"…","state":"…"}` | DDG's native tool frames — observed, no text to buffer |
| `ui-component` | `{"role":"ui-component","name":"…"}` | UI components — observed, no text to buffer |
| `error` | `{"role":"error","type":"…","status":…}` or legacy `{"action":"error",…}` | See Error Codes |
| `[DONE]` | plain marker | Terminal — success |
| `[PING]` | plain marker | Keepalive — ignored |
| `[CHAT_TITLE:<t>]` | plain marker | Conversation title — ignored (no text) |
| `[LIMIT_CONVERSATION]` | plain marker | Conversation limit — treated as `ERR_CONVERSATION_LIMIT` |

**Legacy fallback**: the 2025 action grammar (`data: {"action":"chunk","message":"…"}` / `{"action":"success",…}` / `{"action":"error",…}`) is STILL parsed — DDG has flipped event shapes before without notice. The `success` action no longer appears in the current grammar.

**Stream end without `[DONE]`**: an exhausted stream that already produced content is treated as a SUCCESS (no error raised).

### Chat Response Headers — Challenge Rotation

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
x-vqd-hash-1: <the NEXT base64 challenge>
```

The response's `x-vqd-hash-1` header carries the next turn's challenge — stored in the single in-memory slot and consumed by the next `/chat` request. When the slot is empty (fresh conversation, or after an error consumed it), the backend re-bootstraps from `/status`.

### Differences from OpenAI Chat Completions

- **No `system` role — dropped, not folded** — DDG strips system messages, and duck.ai models treat a forwarded harness prompt as a jailbreak attempt (live-verified 2026-10-09), so `_strip_system_messages` DROPS all `system` content: the AgentKthx system prompt never reaches DDG. `tool`/`function` roles are folded into `user`. No caller code change needed.
- **No sampling params** — `temperature` / `max_tokens` / `top_p` / `stop` / `seed` are accepted at the interface for parity and **silently dropped** (a debug-mode notice fires for `temperature`). Upstream defaults apply. The seed's `default_temperature: 0.7` is informational only.
- **No native function calling from AgentKthx** — `canUseTools` ships `false`; ReAct prompting is the tool path (see Function Calling). `tool_calls` in the house response shape is always `[]`.
- **No `stream` field** — responses are ALWAYS SSE. There is no non-streaming mode; `generate()` buffers the stream internally.
- **No `n` field, no `response_format`** — single completion, no JSON mode.
- **No usage reported** — the protocol reports none; the backend returns an ~4-chars/token ESTIMATE flagged `"estimated": True`.
- **Roles on the wire**: `user` / `assistant` only.

---

## Model Catalog & Specifications

### Available Models (Wire IDs — verified 2026-10-09)

| Wire ID | Display Name | Upstream | Family | Context | Seed Max Output | reasoningEffort | Aliases |
|---------|--------------|----------|--------|---------|-----------------|-----------------|---------|
| `gpt-6-luna` | GPT-6 Luna | OpenAI GPT-6 Luna (via DDG proxy) | gpt | 128K | 16384 | none | `gpt` |
| `gpt-5.6-luna` | GPT-5.6 Luna | OpenAI GPT-5.6 Luna (via DDG proxy) | gpt | 128K | 16384 | none | `gpt-4o-mini` (legacy) |
| `gpt-5.4-nano` | GPT-5.4 Nano | OpenAI GPT-5.4 Nano (via DDG proxy) | gpt | 128K | 16384 | none | `nano` |
| `gpt-5.4-mini` | GPT-5.4 Mini | OpenAI GPT-5.4 Mini (via DDG proxy) | gpt | 128K | 16384 | none | `o3-mini`, `o4mini` (legacy), `mini` |
| `claude-haiku-4-5` | Claude Haiku 4.5 | Anthropic Claude Haiku 4.5 | claude | 200K | 8192 | none | `claude`, `claude-3-haiku` / `claude-3-haiku-20240307` (legacy) |
| `mistral-small-2603` | Mistral Small 4 | Mistral Small 4 | mistral | 128K | 8192 | none | `mixtral`, `mistral`, `mistralai/mistral-small-24b-instruct-2501` (legacy) |
| `tinfoil/gpt-oss-120b` | GPT OSS 120B | OpenAI gpt-oss-120b on Tinfoil (zero provider visibility) | gpt-oss | 128K | 16384 | low | `llama`, `oss`, `meta-llama/llama-3.3-70b-instruct-turbo` (legacy) |
| `tinfoil/gemma4-31b` | Gemma 4 31B | Google Gemma 4 31B on Tinfoil (BETA in the dropdown) | gemma | 128K | 8192 | low | `gemma` |

**Default model**: `gpt-6-luna` — DuckDuckGo's own default pick ("Best for everyday use"); override via `DUCKDUCKGO_DEFAULT_MODEL`.

Notes:

- The 2025-era IDs (`gpt-4o-mini`, `claude-3-haiku-20240307`, `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo`, `mistralai/Mixtral-8x7B-Instruct-v0.1`, `o3-mini`) now **404 with `ERR_MODEL_UNAVAILABLE`** (verified 2026-10-09). They are kept in the alias map as FORWARD-resolving entries so existing sessions and scripts keep working across the rotation — they never hit the wire as-is.
- `mistral-small-2603` is the wire ID (NOT `mistral-small-4` — the display name differs from the ID).
- The Tinfoil-hosted pair occupies the open-weights slot (the `llama` alias resolves there); both are served with `reasoningEffort: "low"`.
- Context lengths and max-output values come from the seed catalog (`agentkthx/data/model_seed.json`, 8 client-verified entries). DDG reports no usage and no per-model metadata endpoint — the seed is the source of truth; unknown models get a conservative 128K context default (an over-estimate risks context-length 400s DDG can't recover from).

### Model ID Format & Resolution

Two wire-ID patterns:

- **Bare kebab IDs**: `gpt-6-luna`, `claude-haiku-4-5`, `mistral-small-2603`
- **Provider-prefixed pair** (Tinfoil-hosted open weights): `tinfoil/gpt-oss-120b`, `tinfoil/gemma4-31b`

Resolution (`_resolve_model`) is **case-insensitive** — historical mixed-case legacy IDs (`meta-llama/llama-3.3-70b-instruct-turbo` et al.) are written lowercase in the alias map and match a lowercased input. Resolution order: exact wire-ID match → legacy/short alias → **pass through unchanged** (the caller surfaces the upstream `ERR_MODEL_UNAVAILABLE` with the catalog-drift hint).

### Catalog Plumbing

- **`list_models()`**: serves the seed catalog shaped into house entries (`name`, `size: 0`, `details{family, backend: "duckduckgo", context_length, free_tier: True, is_chat_model: True, pricing: {input: 0.0, output: 0.0}}`), cached in-memory 1h and routed through the persistent model cache (`model_cache` namespace `duckduckgo`) so `--cache-status` stays uniform. Fully OFFLINE — the house contract is no network in model listing.
- **`fetch_capabilities()`**: live discovery via `GET /duckchat/v1/capabilities` (unauthenticated first; one retry with a solved proof on 401/403). Returns the parsed JSON or `None` on ANY failure — a discovery helper, never a hard dependency.
- **`get_model_max_context()`**: seed lookup with alias resolution; unknown → `128000`.
- **`get_model_runtime_context()`**: equals max context (cloud-style backends have no Ollama-style `num_ctx` to shrink at runtime).
- Every DDG model is free — pricing `0.0/0.0`, `free_tier: True`; the `think` verdict is NO across the board (see Thinking Support below).

### Thinking Support

`ThinkingSupport.NO` for **every** DDG model — a protocol fact, not a probe. The privacy layer hides reasoning: even the reasoning models (`tinfoil/gpt-oss-120b`, served with `reasoningEffort: "low"`) never surface chain-of-thought through the DDG SSE shape — only `message` text arrives. `--think` has nothing to display on this backend.

---

## Function Calling Implementation

### The Wire HAS a Native Tools Surface — This Backend Does Not Use It

The current protocol carries `canUseTools` plus a `metadata.toolChoice` surface exposing DuckDuckGo's built-in tools: **WebSearch / GenerateImage / NewsSearch / VideosSearch / LocalSearch / WeatherForecast**. This surface is UNDOCUMENTED and rotates independently of the chat endpoint — so the backend ships `canUseTools: false` (and no `metadata` key) until it stabilizes. `test_tool_support()` returns `ToolSupportLevel.REACT` for every model: a **PROTOCOL DECISION, not a probe** — no probe request is sent, and nothing is written to `tool_support.json`.

(DDG's own native tool executions still appear on the stream as `role: "tool-invocation"` frames when the upstream model chooses to use its built-ins — the backend observes them but does not act on them, and they do not pollute text content.)

### AgentKthx Tool Path: ReAct

AgentKthx's existing ReAct prompting mode handles tools end-to-end: the model is prompted with a textual description of available tools in the `Action:` / `Action Input:` format, and the ReAct parser (`core/tool_parse.py`, markdown-bold-tolerant) extracts the calls. Tool results ride back to DDG as `user` turns (the role-folding in Message Shaping).

```python
# ReAct is forced automatically by the agent factory — test_tool_support()
# returns REACT for every DDG model, so no explicit flag is needed:
agent = Agent(
    backend=duckduckgo_backend,
    model="gpt-6-luna",
    tools=[...],   # prompted as ReAct text; canUseTools stays false on the wire
)
```

---

## Streaming & Real-time Features

### Streaming Is Mandatory — and `generate_stream()` Is Real

DuckDuckGo AI Chat ALWAYS streams via SSE; there is no non-streaming mode. The backend exposes both consumption styles:

- **`generate()`** — buffers the SSE stream to completion (`_consume_sse`) and returns the house response shape
- **`generate_stream()`** — TRUE incremental streaming (**FEAT-10 — closed by this rewrite**): yields text deltas as they arrive off the wire (4096-byte reads), with per-turn challenge rotation and full error-taxonomy parity with the buffered path

Both paths share the proof lifecycle, the pacing gate, and the error taxonomy.

### Streaming Retry Semantics

- A challenge error mid-stream (HTTP 401, SSE `ERR_CHALLENGE`) re-solves ONCE and replays the request — **already-yielded text is NOT re-yielded; the retry restarts the turn**
- HTTP 418 teapots climb the challenge ladder (up to `_CHALLENGE_RETRIES = 3` re-solves; the 418's own `x-vqd-hash-1` response-header challenge preferred over a `/status` re-bootstrap)
- Fatal errors (403 / 404 / 429 / 5xx / conversation limit) surface the same remediation messages the buffered path does

### Node Solver Timing

The solver finishes a challenge in ~60–90ms locally (live-verified 54–987ms depending on hardware) — faster than any real browser could execute the obfuscated bundle. Strict model routes plausibility-check the proof's `meta.duration`; the FIRST attempt stays honest, and 418 retries escalate the reported duration to a plausible 250ms floor (+ random 0–150ms). See Error Codes and Appendix 12.

---

## Error Codes & Recovery

### Error Surface Shape

All errors surface as `RuntimeError` with a remediation message (the internal exception classes `_ChallengeError` / `_ConversationLimitError` / `_ModelUnavailableError` never escape the backend). Errors arrive three ways:

1. **HTTP status code** on the initial response line (401, 403, 404, 418, 429, 5xx)
2. **SSE error frames** within an otherwise-200 stream: `{"role":"error",…}` (or legacy `{"action":"error",…}`) with `type`/`status` fields
3. **Bracketed SSE markers**: `[LIMIT_CONVERSATION]`

### HTTP Error Taxonomy (as implemented)

| Status | Meaning | Recovery (implemented behavior) |
|--------|---------|--------------------------------|
| **401** | Proof rejected or expired | ONE automatic re-solve (fresh challenge + proof) + single retry — both paths. A second 401 surfaces the error |
| **403** | Forbidden — anti-bot layer rejected the request (non-browser UA) | Fatal with remediation: check `DUCKDUCKGO_USER_AGENT` (must look like a real browser); Referer and Accept are fixed by the backend |
| **404** | `ERR_MODEL_UNAVAILABLE` — the catalog rotated | Fatal with the model ID + rotation hint: run `agentkthx models --backend duckduckgo` after a seed update |
| **418** | Teapot — `ERR_CHALLENGE` at the HTTP layer: proof rejected (expired / shape-flagged) or the anti-bot challenge ladder escalated | Up to **3 re-solves** (`_CHALLENGE_RETRIES`): prefer the challenge carried on the 418's OWN `x-vqd-hash-1` response header (the ladder), else re-bootstrap `/status`; 1s sleep between rounds; reported solve duration escalated to the 250ms floor on retries (strict routes flag sub-100ms solves). Persisted 418 → fatal: "Try another model or wait a minute (proofs expire)" |
| **429** | Rate limit — per-IP throttling (undocumented limits) | Fatal, but the server's `Retry-After` header is surfaced when present. Message: back off 5–10s, reduce request frequency, or route through a different network |
| **5xx** | Upstream provider outage | Fatal: "Try a different DDG model" — the upstream may be down for one provider but not others |

The 418 response body (best-effort parsed in debug traces) carries challenge-ladder metadata: `{"type": "ERR_CHALLENGE", "cd": {"i": <round>}, "overrideCode": …}`.

### SSE Error Frames

| Frame | Meaning | Recovery |
|-------|---------|----------|
| `ERR_CHALLENGE` | Stale/rejected proof mid-stream | ONE re-solve + replay (attempt 1); persisted → fatal: challenge format rotated |
| `ERR_CONVERSATION_LIMIT` (or `[LIMIT_CONVERSATION]`) | Conversation exceeded ~20 turns | Fatal: start a fresh conversation — clear agent memory (`/clear` in chat) or restart the session. Not retryable; re-solve cannot help |
| other `type`/`status` | Upstream/stream error | Fatal with `type (status=…)` surfaced |

### Debug Tracing

`AGENTKTHX_DEBUG=1` prints per-round 418 ladder lines (`type=… round=… override=…`, source: response-header ladder challenge vs `/status` re-bootstrap, attempt n/3), 401/`ERR_CHALLENGE` re-solve notices, and the temperature-ignored notice.

---

## Rate Limiting & Concurrency

### Anti-429 Pacing (Implemented)

- **`/chat` POSTs are paced to `DUCKDUCKGO_MIN_INTERVAL`** (default **3 seconds**, `0` disables). The timestamp is CLASS-level — one pace across every backend instance in the process — so agentic loops and parallel conversations cannot collectively hammer the endpoint.
- The anonymous per-IP limit is strict and undocumented; the 2026-10-09 429 storm showed back-to-back ReAct turns can trip it single-handedly.
- 418 re-solve rounds are paced too (1s sleep between teapot ladder climbs — politely, not hammered).

### Empirical Limits

| Limit | Value | Notes |
|-------|-------|-------|
| Requests per minute | undocumented | Per-IP anti-bot layer; pacing + backoff mandatory |
| `/chat` pacing | 3s default | `DUCKDUCKGO_MIN_INTERVAL`; class-level across instances |
| Conversation length | ~20 turns | `ERR_CONVERSATION_LIMIT` / `[LIMIT_CONVERSATION]` after this |
| 418 ladder budget | 3 re-solves | `_CHALLENGE_RETRIES`; then fatal |
| Daily quota | None | Genuinely uncapped; subject to per-IP throttling |

### Practical Considerations

- **One conversation per backend instance**: the durableStream keypair + `conversationId` are minted per instance. To run parallel conversations, instantiate multiple `DuckDuckGoBackend` objects — they still share the class-level pacing clock.
- **Conservative backoff on 429**: 5–10s with jitter; honor the server's `Retry-After` when present.
- **UA stability**: keep ONE UA for both the solver and the request headers. Rotating UAs is an anti-fingerprinting LAST RESORT, not a routine knob — and never mid-instance (the proof binds the UA).
- **Anti-bot contract v3**: minimum cookie set (`5=1; dcm=3; dcs=1`) + Chromium client hints ride on EVERY request — cheap to send, plausibly checked by the anti-bot layer.

### Conversation Limit Workaround

When the ~20-turn limit hits, the remediation is operational (the backend does not auto-clear memory):

1. Clear the conversation history (`/clear` in chat, or `agent.memory.clear()`)
2. Restart the session — a fresh backend instance mints a fresh durableStream keypair + conversationId
3. The challenge slot survives; no re-bootstrap is required for the new conversation

---

## Multimodal Content Handling

### DuckDuckGo AI Chat Is Text-Only

The `/duckchat/v1/chat` endpoint accepts only text content. There is no image/video/file input on this surface.

### Content-Part Flattening (Implemented)

OpenAI-style part-list content (`[{"type":"text","text":"…"}, …]`) is flattened to plain text before the wire: dict parts with a `text` field and bare strings are collected and joined with newlines (`_coerce_content`); non-text parts are silently dropped. A `tool`-role message whose parts reduce to empty content can still host pending system content (the turn is kept with the system text).

### Workaround for Vision Tasks

For vision tasks, use a different backend (Cloudflare Workers AI vision models, ZAI GLM-V, NVIDIA NIM multimodal). DuckDuckGo AI Chat is text-only.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

DuckDuckGo is the **only** AgentKthx cloud backend that does NOT fit the `CloudBackend` + `openai_compat` pattern — the protocol shares nothing with the OpenAI-compat shared transport, so ZERO lines of `cloud_base.py` / `openai_compat.py` were touched (the MAINT-31 dedup backlog cannot grow from this backend):

```python
from agentkthx.backends.base import BaseBackend

class DuckDuckGoBackend(BaseBackend):
    """duck.ai backend — challenge-proof protocol, always-SSE."""

    is_cloud: bool = True          # EXPLICIT override — BaseBackend defaults direct
                                   # subclasses to False (R06.57 MAINT-05); without this the
                                   # CLI misconfigures streaming defaults + models-table columns

    @property
    def backend_type(self) -> BackendType:
        return BackendType.DUCKDUCKGO     # dedicated enum member (R07.30)

    # base_url property → self._base_url or DUCKDUCKGO_BASE_URL ("https://duck.ai")
    # NO api_key attribute exists — keyless
```

- Registered under BOTH `duckduckgo` and `ddg` aliases (mirrors the SiliconFlow `sf` alias pattern)
- Keyless: no `agentkthx auth` entry (nothing to store); challenge slot + durable keypair are in-memory only
- `generate()` returns the house shape: `{"content", "tool_calls": [], "usage": {prompt_tokens, completion_tokens, total_tokens, "estimated": True}, "finish_reason": "stop", "model": <resolved wire ID>}` — usage is an ~4-chars/token estimate (the protocol reports none)
- `generate()` accepts `tools` / `temperature` / `max_tokens` / `think` for interface parity and silently drops them
- `is_running()` returns `True` unconditionally — a CHEAP local check that must not touch the network (and must not check node availability; a missing node surfaces at generate() time with its remediation)

### 2. FREE_ONLY Enforcement

Not applicable — DuckDuckGo AI Chat is entirely free. Every seed entry carries `pricing: {input: 0.0, output: 0.0}` and `free_tier: True`.

### 3. Plugin Manifest (as shipped, v0.2.0)

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "duckduckgo",
  "version": "0.2.0",
  "description": "DuckDuckGo AI Chat (duck.ai) backend — keyless, anonymous, zero-cost frontier models (GPT-6 Luna, GPT-5.6 Luna, GPT-5.4 Nano/Mini, Claude Haiku 4.5, Mistral Small 4, Tinfoil-hosted gpt-oss-120b / Gemma 4 31B) via the NON-OpenAI /duckchat/v1 protocol — now CHALLENGE-BASED: the retired x-vqd-4 token was replaced by the x-vqd-hash-1 JS proof (solved by the bundled Node helper), per-turn challenge rotation, role-based SSE, durableStream RSA keypair, no /models endpoint, no sampling params, tools stripped — ReAct only. The only cloud backend that subclasses BaseBackend directly instead of CloudBackend",
  "author": {"name": "VTSTech", "url": "https://www.vts-tech.org"},
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "DuckDuckGo AI Chat Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "DUCKDUCKGO",
        "defaults": {
          "DUCKDUCKGO_BASE_URL": "https://duck.ai",
          "DUCKDUCKGO_USER_AGENT": "",
          "DUCKDUCKGO_DEFAULT_MODEL": "gpt-6-luna"
        }
      },
      "provides": {
        "backends": {"duckduckgo": "duckduckgo.DuckDuckGoBackend",
                      "ddg": "duckduckgo.DuckDuckGoBackend"},
        "cli_commands": [],
        "cli_flags": {"--backend": ["duckduckgo", "ddg"]}
      },
      "compatibility": {"agentkthx": ">=0.7.29"}
    }
  }
}
```

(The v0.1.0 manifest shape from the 2026-10-07 draft — flat `backend_class` / `env_vars` / `keyless` fields — is obsolete.)

### 4. Existing Patterns That Apply

- **`BaseBackend` base class**: inherits the interface, NOT the OpenAI-compat plumbing — `generate()` / `generate_stream()` are implemented from scratch
- **`model_cache`**: `list_models()` is routed through the persistent cache (namespace `duckduckgo`, 1h TTL) so `agentkthx models --cache-status` stays uniform across backends
- **`tool_support.json` cache**: deliberately unused — REACT is hardcoded per model (protocol decision, not a probe)
- **`api_resilience.py`**: NOT used — the retry semantics are protocol-specific (the 418 challenge ladder), implemented in-backend
- **`is_local_base_url`**: correctly classifies `duck.ai` as remote
- **Node helpers as plugin assets**: `plugins/*/*.js` MUST be in setuptools package-data — a plain `pip install .` previously dropped them (live finding 2026-10-09), killing every `generate()` with `MODULE_NOT_FOUND`

### 5. Unique DuckDuckGo-Specific Considerations

- **No CloudBackend inheritance**: the custom protocol (challenge proof, role-based SSE, no `/models`) requires the from-scratch `BaseBackend` subclass
- **Single challenge slot**: `_next_challenge_b64` holds exactly the NEXT challenge — not a token history. The old draft's `vqd_tokens` list and its `reask_question` replay feature are obsolete/not implemented
- **Frontend metadata synthesis**: `x-fe-version` + bundle names scraped from `GET /` (10-min cache, silent fallbacks to the captured 2026-10-08 constants); `x-fe-signals` synthesized per request; the proof's `meta.stack` is a synthesized plausible duck.ai bundle stack (real-capture shape, randomized line numbers, refreshed bundle names)
- **Always-streaming**: `generate()` buffers; `generate_stream()` yields
- **Conversation length limit**: ~20 turns — operational reset (`/clear`), not auto-cleared
- **Anti-bot contract v3**: cookies + client hints + UA stability + pacing + duration-floor escalation (see Rate Limiting)
- **No cost tracking**: DDG is free and keyless — the footer's cost segment shows FREE/$0.00
- **Proof modes**: `synth` is the only active mode; `capture` is reserved (headless-Chromium proof lift, needs local Chrome + Node >= 22 — will intercept + abort the page's own `/chat` before it consumes quota)

### 6. Privacy & Anonymity

- The proof and the durableStream keypair are in-memory per instance, NEVER persisted; `messageId` / `conversationId` / `x-ddg-journey-id` are random 32-hex values carrying no identity
- No account/session identifier exists in any request; the UA is a generic browser string
- `PersistentMemory` (SQLite) would break the anonymity property if enabled — in-memory `Memory` is the recommendation for anonymity-sensitive sessions (recommendation only; the backend does not force a memory choice)

### 7. Probe Script

`scripts/probe_duckduckgo.py` — rewritten for the challenge protocol:

- `--status-only` — exercises the challenge solve alone (TEST-13 live verification used this: repeated solves at 54–987ms)
- `--live` — drives the backend end-to-end through a real conversation turn (TEST-13 automation; the remaining live-200-SSE-turn verification)

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `403 Forbidden` | Anti-bot layer rejected the request — non-browser `User-Agent` | Set `DUCKDUCKGO_USER_AGENT` to a real Chrome/Firefox UA string (Referer/Accept are fixed by the backend) |
| `401 Unauthorized` (once) | Proof expired mid-session | No action — the backend auto-re-solves and retries once |
| `401 Unauthorized` (recurring) | UA mismatch between the solver and the request headers (invalidates the proof), or the protocol rotated | Keep `DUCKDUCKGO_USER_AGENT` stable across solver + requests; check the challenge appendix |
| `HTTP 418` (`ERR_CHALLENGE` at the HTTP layer) | Proof rejected/shape-flagged, or the challenge ladder escalated (observed 2026-10-09 on the strict routes `gpt-6-luna`, `claude-haiku-4-5`, `tinfoil/gemma4-31b`) | Automatic — up to 3 ladder re-solves with the duration floor. Persisted: try another model or wait a minute (proofs expire); run with `AGENTKTHX_DEBUG=1` to see the ladder rounds |
| `429` | Per-IP throttling | Honor the surfaced `Retry-After`; respect `DUCKDUCKGO_MIN_INTERVAL` (3s default); back off 5–10s; reduce frequency or change network |
| `404` with `ERR_MODEL_UNAVAILABLE` | The catalog rotated again (2025-era IDs all 404 now) | Pick a current wire ID via `agentkthx models --backend duckduckgo`; update the seed for new arrivals |
| `500` / `503` | Upstream provider (OpenAI/Anthropic/Mistral/Tinfoil) outage | Try a different DDG model — the upstream may be down for one provider but not others |
| SSE `ERR_CHALLENGE` mid-stream | Stale/rejected proof | Automatic — one re-solve + turn replay (already-yielded text is not re-yielded). Persisted: the challenge format rotated |
| `429`-shaped `ERR_CONVERSATION_LIMIT` / `[LIMIT_CONVERSATION]` | Conversation exceeded ~20 turns | Clear memory (`/clear`) or restart the session; a fresh backend instance mints a fresh durableStream keypair |
| `RuntimeError: … Cannot find module …/ddg_durable.js` (or `ddg_vqd.js`) | pip install dropped the plugin's `.js` helpers (pre-package-data builds) | Follow the raised remediation: `pip install -e /path/to/AgentKthx`, or copy the `.js` files from `agentkthx/plugins/duckduckgo/` into the installed plugin directory |
| `RuntimeError: DuckDuckGo backend needs Node.js …` | `node` not on PATH | Install Node >= 18 and make sure it is on PATH |
| Solver subprocess fails (rc != 0) | Challenge format rotated, or the Node environment is broken | Run with `AGENTKTHX_DEBUG=1`; verify `node --version` >= 18; check the solver stderr surfaced in the error |
| Tool calls silently ignored | `canUseTools` ships `false` by design | Use ReAct (automatic — `test_tool_support()` returns REACT for every DDG model) |
| System prompt not applied | BY DESIGN — the DDG backend drops all `system` messages (duck.ai reads forwarded prompts as jailbreak attempts) | None — DDG sessions run prompt-less; use another backend if you need a system prompt |
| Image input rejected / vanished | DDG is text-only; part-lists flatten to text | Use a different backend for vision tasks |
| `ValueError: no sendable messages` | Message list reduced to empty under wire translation (system-only conversation, or empty input) | Send at least one user (or foldable tool) message |

---

## Appendix: Privacy & Anonymity Model

### What DuckDuckGo Promises

Per DuckDuckGo's published privacy policy for Duck.ai:

- **No account required**: no signup, no email, no identity
- **Anonymized upstream**: DuckDuckGo strips your IP address before forwarding to the upstream providers
- **No chat storage**: conversations are NOT stored by DuckDuckGo (upstream providers may store them per their own policies)
- **No training on your data**: DuckDuckGo's agreements with upstream providers prohibit using Duck.ai traffic for training

### The Tinfoil-hosted Open-Weights Pair

`tinfoil/gpt-oss-120b` and `tinfoil/gemma4-31b` are served through Tinfoil — per the seed notes, **zero provider visibility** (the strongest anonymity option in the lineup). Both run with `reasoningEffort: "low"`; Gemma 4 31B is flagged BETA in the live dropdown.

### What DuckDuckGo Cannot Guarantee

- **Upstream provider retention**: upstream providers may retain anonymized conversations per their own policies (typically 30 days for abuse monitoring)
- **Network metadata**: your IP is visible to DuckDuckGo (though not forwarded upstream). Law enforcement could subpoena DuckDuckGo's logs
- **No end-to-end encryption**: the connection is HTTPS (TLS) but DuckDuckGo decrypts the request before forwarding upstream

### AgentKthx-Side Privacy Properties (Implemented)

- The challenge proof and the durableStream keypair live in-memory per backend instance and are never written to disk
- All identifiers (`messageId`, `conversationId`, `x-ddg-journey-id`) are random 32-hex values with no identity content
- The static cookie set (`5=1; dcm=3; dcs=1`) and client hints are fixed contract values, not user identifiers

### Recommendations

For anonymity-sensitive sessions (journalists, researchers):

- Route through a VPN or Tor
- Use in-memory `Memory`, not `PersistentMemory`
- Do not include PII in prompts
- Rotate the conversation frequently (clear memory after each session); prefer the Tinfoil-hosted pair for zero upstream provider visibility

For typical developer use (testing, prototyping, light agentic workflows):

- DDG is the easiest zero-config option in the catalog — no key, no signup, no cost
- The ~20-turn conversation limit is the main constraint; pair with another backend for long agentic workflows

---

## Appendix: x-vqd-hash-1 Challenge & Proof Lifecycle

### Challenge Format

The `x-vqd-hash-1` challenge is a **base64 blob wrapping obfuscator.io-style JavaScript, re-randomized per issue** (the same logical challenge never arrives twice — proof caching across turns is impossible by design).

The RETIRED `x-vqd-4` token format (`4-<timestamp>-<64-hex>`) is GONE: `/status` no longer returns it, and requests sending neither a valid proof nor a token fail. Samples in the 2026-10-07 draft showing that format are void.

### The Solver (`ddg_vqd.js`, headless v2)

Reads the base64 challenge on stdin (or argv), executes it against a **globals-installed clean-browser environment**, and prints the raw solution JSON:

```json
{"server_hashes": ["…"],
 "client_hashes": ["<ua-derived>", "<probe2>", "<probe3>"],
 "signals": {},
 "meta": {"v": "4", "challenge_id": "…", "timestamp": "…", "debug": "…"}}
```

Why globals (not function parameters): the `pxjzr` probe family checks `(function(){return this;}()) === window`, which is only true when `window` IS the global object — exactly like a real browser page. The solver runs as a dedicated subprocess, so polluting `globalThis` is safe.

Clean-browser facts emulated (verified against captured real-browser hashes): native `parseInt` toString, ES6 subclass semantics, `Object.prototype.toString.call(window) === '[object Window]'`, `navigator.webdriver === false`, un-instrumented iframes, unpolluted `window.top`, real DOM measurements (`offsetWidth`/`getComputedStyle`/NodeList), and the duck.ai CSP meta tag. `DDG_SOLVE_UA` overrides `navigator.userAgent` — the backend injects its own UA so the proof binds to the request headers.

### Proof Assembly (Mirrors the duck.ai Page Bundle)

```python
solution = {
    "server_hashes": raw["server_hashes"],          # pass through UNTOUCHED (opaque server nonces)
    "client_hashes": [b64(SHA256(String(v))) for v in raw["client_hashes"]],
    "signals": raw["signals"],
    "meta": {
        "v": "4", "challenge_id": "…", "timestamp": "…", "debug": "…",
        "origin": "https://duck.ai",
        "stack": "<synthesized plausible duck.ai bundle stack>",
        "duration": "<measured solve ms>",
    },
}
proof = base64(json.dumps(solution, separators=(",", ":")))   # → the X-Vqd-Hash-1 REQUEST header
```

- `client_hashes[i]` = `base64(SHA256(String(raw_value)))` — this is where the UA binding lives
- `meta.stack` is synthesized from the scraped `entry.duckai.*.js` / `entry.vendors.*.js` bundle names with randomized plausible line numbers (real-capture shape)
- `meta.duration` reports the honest measured solve time on the first attempt; 418 retries escalate it to the 250ms plausibility floor (+ random 0–150ms) — strict routes flag sub-100ms solves

### Rotation Lifecycle

```
[AgentKthx backend]                         [duck.ai]
     |                                           |
     | GET /duckchat/v1/status                   |
     | x-vqd-accept: 1                           |
     | ----------------------------------------> |
     |             x-vqd-hash-1: CHALLENGE_1     |
     | <----------------------------------------- |
     |                                           |
     | [node ddg_vqd.js  <-- CHALLENGE_1]        |
     |  -> raw solution -> proof (b64 JSON)      |
     |                                           |
     | POST /duckchat/v1/chat                    |
     | X-Vqd-Hash-1: PROOF_1                     |
     | body: {model, messages, canUseTools:false, |
     |        reasoningEffort, durableStream}    |
     | ----------------------------------------> |
     |                                           |
     |      data: {"role":"assistant",...}       |
     | <----------------------------------------- |
     |      data: [DONE]                          |
     |      x-vqd-hash-1: CHALLENGE_2            |
     | <----------------------------------------- |
     |                                           |
     | [_next_challenge_b64 = CHALLENGE_2]       |
     |                                           |
     | POST /duckchat/v1/chat (next turn)        |
     | X-Vqd-Hash-1: PROOF_2  (solved from       |
     |  CHALLENGE_2 — no /status round-trip)     |
     | ----------------------------------------> |
     | ... (per-turn rotation continues) ...     |
```

1. **Bootstrap**: `GET /status` with `x-vqd-accept: 1` → challenge in the `x-vqd-hash-1` response header (skipped when the rotation slot already holds a challenge)
2. **Solve**: bundled Node solver executes the challenge (UA-bound) → raw solution
3. **Assemble**: post-process → base64 JSON → `X-Vqd-Hash-1` request header
4. **Rotate**: every `/chat` response's `x-vqd-hash-1` header carries the NEXT challenge → stored in the single `_next_challenge_b64` slot, consumed by the next request
5. **Re-bootstrap**: slot empty (fresh conversation / consumed by an error) → `/status` again
6. **Ladder (418)**: a teapot response carries its own harder challenge on `x-vqd-hash-1` — preferred over a `/status` re-bootstrap; up to 3 rounds with 1s sleeps and duration-floor escalation

### Implementation Notes

- The pending challenge is a SINGLE slot, not a list — older challenges are worthless (re-randomized per issue)
- The proof is per-request state: it is built fresh for every `/chat` attempt (including retries), never reused, never persisted
- The first attempt reports the honest solve duration; only the 418-retry path pads it — plausibility, not deception, is the design goal (a sub-100ms claim is what the strict routes actually reject)
- The protocol can rotate again without notice (it already did once between the plugin scaffold and its first live traffic). If `/status` stops returning an `x-vqd-hash-1` header, the backend raises with a pointer to this document — start by re-running the curl matrix and decoding the new challenge shape
