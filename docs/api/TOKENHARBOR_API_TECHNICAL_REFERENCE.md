# TokenHarbor API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://tokenharbor.ai/docs (Chat APIs, Models, Prompt caching on Claude, How we measure speed, Rate limits, Universal Key, Quickstart, Credits & top-ups, Rewards, Subscription, Connect CLI) + https://tokenharbor.ai/docs/compare/openrouter
> **Last Updated**: 2026-10-12 (pre-implementation — planned backend #14; **no plugin code exists yet**)
> **Target Audience**: AgentKthx Developers
> **Verification status**: **Docs-only.** This reference was drafted entirely from TokenHarbor's public documentation on 2026-10-12. No endpoint was probed and no key was used. Wire shapes the docs do not explicitly show are marked **⚠️ UNVERIFIED** — do not write code against them without a live probe (`scripts/probe_tokenharbor.sh`, to be created).

## Table of Contents

1. [Provider Overview & Positioning](#provider-overview--positioning)
2. [Authentication & Endpoint Details](#authentication--endpoint-details)
3. [Universal Key Lifecycle & Per-Key Limits](#universal-key-lifecycle--per-key-limits)
4. [Request/Response Structure](#requestresponse-structure)
5. [Sampling Parameters](#sampling-parameters)
6. [Model Catalog & Discovery](#model-catalog--discovery)
7. [Function Calling Implementation](#function-calling-implementation)
8. [Streaming & Real-time Features](#streaming--real-time-features)
9. [Caching Behavior — Exact Cache & Prompt Cache](#caching-behavior--exact-cache--prompt-cache)
10. [Billing, Spend Confirmation & Usage Visibility](#billing-spend-confirmation--usage-visibility)
11. [Error Codes & Recovery](#error-codes--recovery)
12. [Rate Limiting & Concurrency](#rate-limiting--concurrency)
13. [Free Tier Behavior — `:free` Rules](#free-tier-behavior--free-rules)
14. [Attribution & Usage-Metrics Gaps vs OpenRouter](#attribution--usage-metrics-gaps-vs-openrouter)
15. [Using Token Harbor Today (Pre-Scaffold)](#using-token-harbor-today-pre-scaffold)
16. [Implementation Notes for AgentKthx (Backend #14, Scaffolded)](#implementation-notes-for-agentkthx-backend-14-scaffolded)
17. [OpenRouter → TokenHarbor Migration Map](#openrouter--tokenharbor-migration-map)
18. [Troubleshooting Matrix](#troubleshooting-matrix)
19. [References](#references)

---

## Provider Overview & Positioning

Token Harbor (tokenharbor.ai) is an LLM gateway/aggregator: one account, one API key, and one USD balance that reach models from many upstream providers (OpenAI, Anthropic, Google, DeepSeek, Qwen, and more). The positioning is deliberately close to OpenRouter's — the site's own comparison page (https://tokenharbor.ai/docs/compare/openrouter) frames it as "if you already understand OpenRouter, you already understand Token Harbor." Both gateways expose an OpenAI-compatible endpoint, both bill pay-as-you-go per token, and both fail a request over to another upstream provider serving the same model when one is unhealthy.

### Side-by-side (as documented by Token Harbor, 2026-10-12)

| Capability | Token Harbor | OpenRouter |
|------------|--------------|------------|
| One key for many providers | Yes | Yes |
| OpenAI-compatible endpoint | Yes, `…/v1` | Yes |
| Native Anthropic `/v1/messages` endpoint | Yes | "Varies" (their wording) |
| One-command coding-agent setup (CLI) | Yes (`tokenharbor` CLI, `/connect`) | — |
| Free tier on select models | Yes (standing `:free` routes) | "Varies" |
| Pay-as-you-go without a subscription | Yes | Yes |
| Per-token pricing on public pages | Yes | Yes |
| Fallback to another provider for the same model | Yes | Yes |

(Token Harbor's page deliberately writes "Varies" rather than speaking for OpenRouter's current feature set. The `Yes` rows for Token Harbor are things their docs claim are verifiable on their site today.)

### Where Token Harbor differs (their four claims)

1. **Native Anthropic API too, not just OpenAI-shaped.** Token Harbor exposes both `https://tokenharbor.ai/v1` (OpenAI `/v1/chat/completions`) and `https://tokenharbor.ai` (Anthropic `/v1/messages`), so Anthropic-wire tools — including Claude Code — work without a shim. **This reference scopes to the OpenAI wire only**; the Anthropic endpoint is documented here only where it changes behavior visible from the OpenAI side (auth, error type names, per-key limit coverage).
2. **One-command agent setup** via the `tokenharbor` CLI (`curl -fsSL https://tokenharbor.ai/connect.sh | sh`), which wires Claude Code, Codex, Cursor, Cline, opencode, pi and others to the gateway, backs up configs, and can restore them.
3. **A standing free tier**: model IDs ending in `:free` never charge the balance. See [Free Tier Behavior](#free-tier-behavior--free-rules).
4. **Transparent per-token pricing** on every model card, with automatic fallback to another provider serving the same model.

### Relation to AgentKthx

Token Harbor is a candidate **cloud backend #14** in the `CloudBackend` family (R07.05 base, hardened R07.28). It is the closest structural sibling to `OpenRouterBackend` in the codebase: same OpenAI Chat-Completions grammar, same aggregator-with-failover model, same "assume NATIVE tools + runtime ReAct fallback" posture. The two meaningful protocol differences from OpenRouter are documented in depth below:

- **`GET /v1/models` requires auth** (OpenRouter's is a public read) — this changes the `list_models()` fallback value and the offline story (see [Model Catalog & Discovery](#model-catalog--discovery)).
- **Two server-side caches sit in front of the model** — an exact-match response cache (billed $0) and vendor prompt-cache auto-marking. Neither exists on OpenRouter, and both change what a "retry" costs and what the usage numbers mean (see [Caching Behavior](#caching-behavior--exact-cache--prompt-cache)).

Token Harbor's `tokenharbor` connect CLI does **not** list AgentKthx in its supported-agent matrix. The CLI's adapter taxonomy — **Auto** (edits the agent's own config file after copying it aside; `disconnect` restores), **Env** (sets the shared `OPENAI_*`/`ANTHROPIC_*` variables, writes no file), **Guided** (prints exact values to paste, touches nothing) — currently covers Claude Code, Codex CLI, opencode, OpenClaw, Qwen Code and pi as Auto; Aider and Hermes as Env; Cursor, Cline/Roo Code, Zed, Cherry Studio, Goose, Crush, n8n, Dify, OpenHands, Flowise, Langflow, ChatBox/LobeChat as Guided; Windsurf as Unsupported. AgentKthx integration follows the **Env** pattern: export `TOKENHARBOR_API_KEY` (or `OPENAI_API_KEY` — see section 15) and point the base URL at `https://tokenharbor.ai/v1`. See [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx-backend-14-scaffolded).

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production
BASE_URL = "https://tokenharbor.ai/v1"          # OpenAI-compatible surface (this reference's scope)

# Anthropic-compatible surface — OUT OF SCOPE for the plugin, listed for completeness
ANTHROPIC_BASE_URL = "https://tokenharbor.ai"    # POST {base}/v1/messages

# API Endpoints (OpenAI wire)
CHAT_COMPLETIONS = "/chat/completions"   # POST — primary surface
MODELS = "/models"                       # GET  — model catalog. REQUIRES AUTH (unlike OpenRouter)
```

Token Harbor's rate-limit page also names two additional OpenAI-family endpoints — `/v1/responses` (OpenAI Responses API shape) and `/v1/images/generations` (image generation) — and states they share the same rate-limit pool as `/v1/chat/completions`. **AgentKthx does not plan to call either**; they are listed here because the per-key limit coverage rules (below) treat them differently, and because the images endpoint's 10/min free-tier limit matters if a user points a shared key at it.

There is no published OpenAPI specification for Token Harbor (OpenRouter publishes `openapi.json`; Token Harbor's docs make no mention of one).

### Authentication Headers

```python
headers = {
    "Authorization": f"Bearer {api_key}",   # thk_live_… — see Universal Key section
    "Content-Type": "application/json",
}
```

- The key is sent as a standard OpenAI-style `Authorization: Bearer` header on every `/v1` call, including `GET /v1/models`. Token Harbor's docs are explicit that opening `/v1/models` in a browser without a key answers `401`.
- On the Anthropic-compatible endpoint (out of scope), auth accepts **either** `x-api-key: thk_live_…` (Anthropic default) or `Authorization: Bearer thk_live_…`.
- Transport: HTTPS only. The Universal Key doc states plaintext `Authorization` headers are "rejected at the network edge before reaching our servers" (i.e., no plain-HTTP downgrade path exists).
- **No attribution/identification headers are documented** — see [Attribution & Usage-Metrics Gaps vs OpenRouter](#attribution--usage-metrics-gaps-vs-openrouter). AgentKthx should send no Token Harbor-specific headers beyond `Authorization` and the two cache-control headers documented in section 9.

### Request Format Requirements

- **Content-Type**: `application/json`
- **Character Encoding**: UTF-8
- **Max Request Size**: not documented ⚠️ UNVERIFIED (OpenRouter documents 8MB; assume provider-dependent until probed)
- **Timeout**: AgentKthx's `BackendConfig.timeout` default (120s) applies. Token Harbor's own gateway timeout is not documented ⚠️ UNVERIFIED. CPU-diffusion-style long generations are not a concern on this backend (text chat only), but long thinking-model outputs can run minutes — keep the 120s default and rely on the shared retry loop for transient aborts.
- **Windows shell caveat (from Token Harbor's own docs)**: in PowerShell, the aliased `curl` is `Invoke-WebRequest`, and even `curl.exe` mangles JSON quoting — the usual cause of a `bad range specification` error, or an `invalid_api_key` when a long key picks up a stray space or line-break. Always paste the key in full, with no spaces. Relevant to anyone hand-testing the gateway from Windows.

---

## Universal Key Lifecycle & Per-Key Limits

Token Harbor calls its API keys **Universal Keys** — one key authenticates every call to `/v1/chat/completions` (and `/v1/messages`), reaching every model and provider in the catalog. There is no per-vendor key to manage.

### Key format and storage

| Property | Value (documented) |
|----------|--------------------|
| Format | `thk_live_{64-char base64url random}` |
| Prefix purpose | Tag for debugging tools and log scanners ("the `thk_live_` prefix is a tag for debugging tools and log scanners; the rest is the secret") |
| Plaintext storage | Never stored server-side — only a salted SHA-256 hash |
| Reveal | Shown **once**, at creation. Re-reveal is impossible; a lost key can only be replaced |
| Log masking | `thk_live_…………{last4}` everywhere in their logs |
| First key | Created automatically on first sign-in |
| Additional keys | Created at `/dashboard/api-keys` — one per project, environment, or agent |
| Multiple keys | Yes — several active keys may be used simultaneously; all bill to the same wallet and reach the same catalog. Revoking one leaves the others untouched |

### Rotation model (differs from OpenRouter)

- **Per-key revoke** is the normal path: revoke the leaked/lost key at `/dashboard/api-keys`, create a replacement, re-point that one integration. Everything else keeps working.
- **Rotate is account-wide** — the dashboard's Universal Key card has a Rotate button that revokes **every** active key on the account (other projects, other machines, any signed-in CLI device) and issues a single new one, revealed once. Everything the user runs will `401` until it has the new key. It is explicitly the panic switch for "I don't know which key leaked."

> **AgentKthx note**: a user who hits `Rotate` will see every `agentkthx` session on every machine start failing with 401 at once. The troubleshooting matrix (section 18) lists this as a first-check item before suspecting the plugin.

### Programmatic management: there is none

> "There is no key-management API you can drive with a Universal Key — no `/v1/keys/...` endpoints. Creating, rotating and revoking keys is done while signed in to the dashboard."

This is a hard difference from OpenRouter (which exposes `/keys` create/list under a management key). AgentKthx cannot offer key lifecycle commands against Token Harbor; the plugin's job starts and ends at *using* a key.

### Per-key spend caps

Each key can carry three rolling spend caps in USD — **Daily** (last 24h), **Weekly** (last 7d), **Monthly** (last 30d). Windows roll continuously; they are not calendar boundaries. Left on `Unlimited` there is no cap.

- Once a key's spend inside a window reaches its cap, calls with **that key** are refused with HTTP `429` and error code **`spend_limit_exceeded`**, with a `Retry-After` header, until enough spend ages out of the window or the cap is raised. Other keys on the account are unaffected.
- The rate-limit doc is explicit: **"Key limits are spending caps … They stop a key once it has spent that much"** — not request-rate limits.
- The leaked-key rationale: paid accounts have no request-rate cap, so the ceiling on a leaked key is the spending limit set on it.

### Per-key vendor allow/block lists

A key can carry a list of model **makers** it may use (e.g. `anthropic`, `openai`) or a list it may not. A call to a model from a maker outside the list is refused with HTTP `403` and error code **`vendor_not_allowed`**.

- Coverage (documented): the spend caps and vendor lists are checked on **`/v1/chat/completions` and `/v1/responses`**.
- Not covered (documented): image generation (`/v1/images/generations`) and *some* Claude requests on `/v1/messages`. For AgentKthx's OpenAI-wire-only scope this means: **every call the plugin makes IS checked** — the gap does not apply to us.
- The dashboard currently has no control for editing the vendor lists, so in practice they apply only to keys that already carry one (set via support, presumably — the docs don't say how a list gets attached).

### What a 429 spend cap vs a rate-limit 429 means for the retry loop

Both arrive as HTTP 429 with `Retry-After`. The distinguishing signal is the error code string in the body:

- `spend_limit_exceeded` → **do not hammer-retry**: the window rolls, so retrying sooner than `Retry-After` is pointless and the cap won't lift until spend ages out (or the user raises the cap / switches keys). The planned CloudBackend mapping treats this as quota-class, not rate-class (see [Error Codes & Recovery](#error-codes--recovery)).
- Any other 429 → the free-tier account rate limit; normal `Retry-After`-honoring retry applies.

---

## Request/Response Structure

### Complete Request Schema

Token Harbor's docs show a minimal OpenAI Chat-Completions body (`model`, `messages`, `stream`) and never publish a full field reference — the gateway is positioned as a *pass-through*: "Your `system` and `messages` are forwarded byte-for-byte to the model you name; we never add or rewrite anything." The schema below is the OpenAI-standard field set AgentKthx may send, annotated with what Token Harbor's docs explicitly confirm vs. what is inherited-from-OpenAI and unverified:

```jsonc
{
    // ── documented by Token Harbor ──────────────────────────────────────
    "model": "claude-opus-5.5",              // any id from /v1/models (see §6 for the id scheme)
    "messages": [                             // forwarded byte-for-byte; system role IS transmitted
        {"role": "system|user|assistant|tool", "content": "string|array"}
    ],
    "stream": false,                          // true → SSE (see §8)
    "tools": [ /* OpenAI tool schema, §7 */ ],
    "cache_control": { "type": "ephemeral" }, // Anthropic-style mark — FORWARDED on content blocks,
                                              // not an OpenAI field (see §9 "Placing your own mark")
    "th_confirm_spend": true,                 // Token Harbor extension — confirms a >½-balance call
                                              // (see §10 "Confirming a large call")

    // ── OpenAI-standard, NOT named in Token Harbor's docs ⚠️ UNVERIFIED ──
    "temperature": 0.7,
    "top_p": 0.95,
    "max_tokens": 8192,
    "stop": ["###"],
    "seed": 42,
    "n": 1,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "response_format": {"type": "text|json_object|json_schema"},
    "tool_choice": "auto|none|required|{...}",
    "stream_options": {"include_usage": true}
}
```

Notes on specific fields:

- **`model`** — the only routing input. There is no documented `provider` preference object, no `transforms`, no `plugins` (all OpenRouter features with no documented Token Harbor counterpart). Routing to "the cheapest healthy upstream" happens internally "regardless of which shape you call."
- **`system` role is transmitted.** This is the opposite of the DDG/SD convention (R07.30/31 system-prompt suppression) and matches the OpenRouter convention. The plugin must NOT strip or fold system messages — the ReAct/native tool scaffolding reaches the model.
- **`max_tokens`** — on the OpenAI wire this is the standard optional output cap. (On the Anthropic-compatible `/v1/messages` it is *required* by the schema — mentioned here only so nobody copies an Anthropic example onto the OpenAI wire and worries about the asymmetry.)
- **`th_confirm_spend`** — a Token Harbor-specific boolean body field. Sending it on every request disables the >½-balance spend-confirmation gate for that client. See section 10.
- **`cache_control`** — Token Harbor forwards this Anthropic-owned field on the OpenAI wire "rather than dropping it" (documented verbatim). One mark anywhere — on a tool, a system block, or a message block — claims the request from the auto-marker. See section 9.

### Response Schema

The docs' examples read `response.json()["choices"][0]["message"]["content"]` — the standard OpenAI Chat-Completions envelope. The docs never print a full response body, so the field-by-field envelope below is **⚠️ UNVERIFIED** beyond the `choices[0].message.content` path and the `usage` block:

```jsonc
{
    "id": "⚠️ UNVERIFIED — id format not documented",
    "model": "⚠️ UNVERIFIED — whether the gateway echoes the requested id or the routed upstream",
    "object": "chat.completion  ⚠️ UNVERIFIED",
    "created": 0,                              // ⚠️ UNVERIFIED
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response"   // ← documented read path
                // "reasoning"/"reasoning_content": NOT documented for the OpenAI wire ⚠️
            },
            "finish_reason": "stop|tool_calls|length|…"   // ⚠️ UNVERIFIED enumeration
        }
    ],
    "usage": {
        // Anthropic-side names, documented in the prompt-caching section:
        "input_tokens": 812,
        "cache_creation_input_tokens": 11702,   // written this turn, billed 1.25×
        "cache_read_input_tokens": 0            // read from cache, billed 0.1×
        // ⚠️ UNVERIFIED whether the OpenAI wire uses these Anthropic-style names,
        //    OpenAI-style names (prompt_tokens/completion_tokens), or both.
        //    AgentKthx's parser should accept BOTH families — see §16 test plan.
    }
}
```

**The usage-field naming question is the single most important unverified shape on this wire.** Token Harbor's prompt-caching doc shows an Anthropic-style usage block (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) without stating which endpoint it belongs to. The billing doc says "Token counts come from the serving model's own usage report on the final request body," which implies vendor-native counters are surfaced as-is. Plan for tolerance: parse `prompt_tokens`/`completion_tokens` (OpenAI) **and** `input_tokens`/`output_tokens` (Anthropic-style) — the same dual-family handling `core/streaming.py` already applies for reasoning-content field names across cloud providers.

Other documented response-adjacent facts:

- **Cache-served responses** add the response header `X-TH-Cache-Layer: exact` (documented) so the client can tell the reply came from the exact cache rather than the model.
- **Errors** — envelope shape not documented ⚠️ UNVERIFIED; known codes in section 11.
- **No `usage.cost`**: unlike OpenRouter, no inline per-request cost field is documented. Cost visibility is the `/dashboard/usage` page (last 100 requests, tokens-in/out, cost-paid, cache layer, CSV export).

### Prompt handling: pass-through, no transforms

Direct quote (Chat APIs → Prompt handling): "Pure pass-through. Your `system` and `messages` are forwarded byte-for-byte to the model you name; we never add or rewrite anything." Consequences for AgentKthx:

- What you send is what you're billed for — "the gateway adds nothing, so you are billed for exactly the tokens you sent plus the tokens the model returned."
- There is **no middle-out truncation** and no request rewriting. Combined with the context-limits policy ("We never truncate your conversation … if the model refuses with `context_length_exceeded`, that error is forwarded verbatim"), Token Harbor behaves exactly like AgentKthx's local backends: oversized context is the caller's problem, surfaced as the upstream's error.
- The byte-for-byte guarantee also means AgentKthx's `sanitize_tool_output`-wrapped history and ReAct scaffolding land at the model unmolested — same trust model as OpenRouter.

---

## Sampling Parameters

Token Harbor's documentation does not enumerate sampling parameters. The gateway's stated posture — byte-for-byte pass-through of everything except the documented extensions — implies the OpenAI-standard sampling set is forwarded to whatever upstream serves the request, and the upstream decides what it supports. Everything in this section beyond the two documented facts is **⚠️ UNVERIFIED**.

### Documented facts

1. **Sampling settings participate in exact-cache identity.** The exact cache serves a response only for "Same model + same messages + same sampling within 5 minutes" (Models → How we bill). So sampling fields are part of the cache key — changing `temperature` between retries defeats the cache.
2. **Requests carrying `tools` always skip the exact cache** (documented twice: Models and Credits pages). Sampling therefore never causes a cached tool-call reply to be replayed.

### Expected (unverified) parameter set

Assume the OpenAI Chat-Completions family forwards: `temperature`, `top_p`, `max_tokens`, `stop`, `seed`, `n`, `presence_penalty`, `frequency_penalty`, `response_format`, `tool_choice`. Whether router-only extras (`min_p`, `top_k`, `repetition_penalty`, `logit_bias`, OpenRouter's `reasoning` object) survive the hop to every upstream is unknown — Token Harbor is upstream-agnostic, so support is per-served-provider, not per-gateway.

### AgentKthx implementation plan

`TokenHarborBackend` inherits `_build_openai_body()` from `OpenAICompatibleBackend`/`CloudBackend` and forwards the same field set OpenRouter's plugin does (R07.05 era):

```python
# inherited shape — optional fields forwarded from **kwargs when not None
optional_int_fields   = ("top_p", "top_k", "seed", "n")
optional_float_fields = ("presence_penalty", "frequency_penalty")
# plus: stop, response_format, tool_choice, reasoning_effort (top-level — same
# known limitation as the OpenRouter plugin: no structured `reasoning` object)
```

Cloud backends drop AgentKthx's local-only knobs (`num_batch`, `repeat_penalty`, `repeat_last_n`) before the wire — that behavior is the shared `CloudBackend` default and must not be "fixed" for Token Harbor. No Token Harbor-specific sampling extension is planned; the `th_confirm_spend` field is the only documented extension and belongs in the spend-gate handling, not the sampler.

---

## Model Catalog & Discovery

### Two surfaces: web page and API

| Surface | Auth | Contents (documented) |
|---------|------|----------------------|
| `/models` (web page) | none | Every model wired into the gateway. Each row shows **modalities, Intelligence Index, and per-million input/output price**. A **Free** section lists the current `:free` set. |
| `GET https://tokenharbor.ai/v1/models` | **Bearer key required** | "returns the callable model ids as JSON for SDKs that pre-fetch the catalog." Opened in a browser without a key it answers `401`. |

The **auth requirement is the load-bearing difference from OpenRouter.** OpenRouter's `/api/v1/models` is a public read, which is why `OpenRouterBackend.list_models()` works cold, offline-adjacent, and why the seed catalog matters less. Token Harbor's catalog read burns a request against the free-tier rate limits and fails entirely without a valid key — so AgentKthx's offline fallback story leans harder on the seed catalog.

The JSON shape of `/v1/models` is **not documented** ⚠️ UNVERIFIED. The phrase "returns the callable model ids as JSON" plus OpenAI-compat positioning suggests an OpenAI-style `{"data": [...]}` envelope, but the per-entry fields (context length? pricing? modality?) are unknown. **Do not assume OpenRouter's rich per-entry schema** (`architecture`, `pricing`, `top_provider`, `supported_parameters`) exists here — probe before wiring any field other than the id.

### Model ID scheme

Token Harbor's docs use several id shapes, and the docs' own rule is: **"Model id is whatever appears in `/models`."**

| Shape | Documented examples |
|-------|---------------------|
| `tokenharbor/`-prefixed | `tokenharbor/qwen3-max` (used in every Chat APIs example) |
| Bare ids | `claude-opus-5.5`, `deepseek-v4.1-flash`, `glm-5.3` (compare page + quickstart) |
| `:free`-suffixed | `deepseek-v4.1-flash:free` (quickstart free tier; the connect CLI's Qwen Code adapter selects it) |
| Sub-versioned | `gpt-5.6-luna`, `gpt-5.6-terra`, `mimo-v2.5-pro`, `claude-sonnet-5.5`, `qwen3.8-max`, `grok-4.7`, `muse-spark-1-3` (Subscription page named-model lists) |

There is **no documented `vendor/model` convention** like OpenRouter's `anthropic/claude-3.5-sonnet`. Treat ids as opaque strings from the catalog; the only structural markers AgentKthx may rely on are the `:free` suffix (section 13) and nothing else. Per-key vendor lists match on *maker*, not on id prefix — the docs never define how a maker is derived from an id, so the plugin must not try to parse one.

### Pricing formula (documented)

```
cost = (input_tokens × price_in_per_1m + output_tokens × price_out_per_1m) / 1,000,000
       × (1 + markup_pct / 100)
```

- `price_*_per_1m` — the model's per-token input/output price as listed on its `/models` card.
- `markup_pct` — Token Harbor's per-model markup. "Default is 0% today."
- "There is no volume discount and no per-turn fee: the per-token price on the model's card is the whole price."
- `input_tokens` = tokens from the serving model's own usage report on the final request body (gateway adds nothing in direct model calls).
- Two caching layers reduce the real billed amount (section 9).

### Context limits

Documented policy: "We never truncate your conversation. Each request goes to the model as you sent it; if the model refuses with `context_length_exceeded`, that error is forwarded verbatim." Whether `/v1/models` entries carry a `context_length` field is ⚠️ UNVERIFIED. Until probed, the plugin's context-derivation ladder (`_get_catalog_defaults`) should treat Token Harbor like a catalog of unknown context sizes: honor an explicit `--num-ctx`, fall back to the seed catalog, and let the persisted model-cache store whatever the live entries eventually prove to carry.

### AgentKthx catalog plan

- Seed key: `"tokenharbor"` in `agentkthx/data/model_seed.json` (backend keys must not start with `_` — reserved for `_meta`).
- Seed entries: only the **documented** ids (`claude-opus-5.5`, `deepseek-v4.1-flash`, `deepseek-v4.1-flash:free`, `glm-5.3`, `tokenharbor/qwen3-max`, the Subscription named models) with pricing left `null`/absent — docs-only pricing would be fabrication. A comment in the seed JSON marks the key "docs-only, unverified — refresh from live /v1/models at scaffold time."
- Cache contract: the ROB-42 shape is mandatory — `list_models()` = narrowed-except, store-on-success, `get_stale_models()` stale-first. Because the endpoint needs auth, the *unauthenticated cold-start* path is: seed catalog immediately, live refresh only when a key exists.

---

## Function Calling Implementation

### Tool schema (OpenAI-compatible)

AgentKthx converts its internal `Tool` objects to OpenAI schema via `Tool.to_openai_schema()` and forwards them as the `tools` field:

```json
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"]
        }
    }
}
```

Token Harbor's docs never show a `tools` example, but they reference tools repeatedly and normatively: the exact cache "Requests that carry `tools` always skip it", and the auto-marker fires when "the request carries tools". Tools on the OpenAI wire are therefore a first-class, expected field ⚠️ (envelope behavior — `tool_calls` deltas on streaming, `finish_reason: "tool_calls"` — UNVERIFIED but OpenAI-standard).

### Tool choice

OpenAI-standard values expected (⚠️ UNVERIFIED — not named in Token Harbor's docs): `"auto"` (default), `"none"`, `"required"`, and the force-specific-function object form. Same handling as the OpenRouter plugin.

### What the gateway itself does around tools (documented)

1. **Tools always bypass the exact cache** — a tool-bearing request is never served a cached reply, so every tool round-trip genuinely reaches a model. This matters for AgentKthx's agentic loop: repeated identical tool-batch turns inside the 5-minute window still cost money and still exercise the model.
2. **Tools trigger prompt-cache auto-marking** — a tool-bearing request with a long prefix gets a cache mark injected unless the client placed one (section 9). Agents benefit structurally: the tools + system prompt + growing history prefix is exactly the "reuse is near certain" case.
3. **Per-key vendor lists are enforced on tool-bearing chat calls** — they're checked on `/v1/chat/completions` regardless of tools (documented coverage).

### Tool-support verdict plan (aggregator assumption)

Follow the OpenRouter decision (R07.05, reviewed R07.19 — Active Decisions table in `audit/brief.md`):

- `test_tool_support()` returns `ToolSupportLevel.NATIVE` **unconditionally**, without probing. Rationale: no per-model probe across a multi-provider catalog; the catalog is gated behind auth anyway, making a probe sweep expensive against the free-tier rate budget.
- The safety net is the runtime fallback: HTTP 400 with a "does not support tools"-class message → retry without the `tools` field → ReAct. `CloudBackend`/`OpenAICompatibleBackend` already carry `_is_tools_not_supported_error()` with the standard indicator list (`does not support tools`, `tools are not supported`, `tool calling is not supported`, `tools are not yet supported`, `does not support function calling`, `function calling is not supported`, `no tools endpoint`).
- ROB-39's caveat transfers verbatim: the unconditional NATIVE verdict overstates support for non-chat slugs (image/audio/moderation models) that appear in the catalog. Runtime stays safe (400 → ReAct); the `agentkthx models` table may show `tools ✓ native` for a non-chat row. Token Harbor's catalog explicitly includes modalities and an Intelligence Index on the web page, but the API entries' fields are unverified — until probed, expect ROB-39-class noise, don't fix it locally.
- Cache the verdict via the standard `tool_support.json` mechanism (ONE plain-key namespace + `thinking:<model>` keys). Mind landmine #2 (pre-#13 stale thinking caches) when adding `thinking:` entries for a new vendor's ids.

### ReAct path and the pass-through guarantee

On the ReAct path, AgentKthx folds tool descriptions into the system prompt and tool results into user turns — all of which Token Harbor forwards byte-for-byte (documented). Unlike DDG/SD, **nothing about the ReAct scaffolding gets dropped at the wire**, so the ROB-51 tool-silently-cannot-engage class does not exist here.

---

## Streaming & Real-time Features

### SSE streaming

Set `"stream": true`; the server "emits Server-Sent Events so tokens arrive as they're generated" (documented). Token Harbor's Python example reads raw lines:

```python
with requests.post(url="https://tokenharbor.ai/v1/chat/completions",
                   headers={"Authorization": "Bearer thk_live_…",
                            "Content-Type": "application/json"},
                   json={"model": "tokenharbor/qwen3-max",
                         "messages": [{"role": "user", "content": "Write a haiku about the sea."}],
                         "stream": True},
                   stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode("utf-8"))
```

The chunk grammar is **⚠️ UNVERIFIED** — the docs never print an SSE frame. Assume the OpenAI delta grammar (`data: {"choices":[{"delta":{"content":"…"}}]}`, terminal `data: [DONE]`) that every OpenAI-compatible aggregator emits; AgentKthx's shared `_iter_sse_lines()` + `StreamAccumulator` path needs no adaptation for it. Whether the final chunk carries `usage` is unverified; keep the standard posture (set `stream_options.include_usage`, tolerate its absence, fall back to the chars÷4 estimator so the footer's ctx% still moves).

On the Anthropic-compatible endpoint (out of scope), "Our adapter emits the same events so SDK handlers keep working" — typed events `message_start`, `content_block_delta`, `message_stop`. Noted for completeness only.

### What streams are NOT: no reasoning-delta contract

No `reasoning`/`reasoning_content` delta behavior is documented for the OpenAI wire ⚠️ UNVERIFIED. Thinking models are in the catalog (the speed doc counts "hidden reasoning tokens on models that think before answering" as output), but whether their chain-of-thought surfaces as deltas on `/v1/chat/completions` is unknown. The streaming path's existing dual-field reasoning capture costs nothing here; treat reasoning display as a live-probe checklist item, not a documented feature.

### Speed metrics — what the usage page reports (documented)

Token Harbor measures two speeds per request, both from timestamps on the connection that served the request:

**Headline — tokens per second (writing speed):**

```
tok/s = output tokens ÷ generation time
generation time = total response time − time-to-first-token
```

- Output tokens include hidden reasoning tokens (both are billed as output, both are counted).
- A 12.0s reply whose first token arrived at 4.0s = 8.0s generation time; 1,000 output tokens → 125 tok/s.

**Second line — prefill (reading speed), shown only for prompts ≥16,000 tokens:**

```
prefill = prompt tokens ÷ time-to-first-token
```

- Prompt tokens include any served from cache. A 77,000-token prompt read in 4.9s ≈ 15.6k tok/s prefill.
- Below 16k tokens the same fact is shown as a wait ("4.3s to first") because TTFT there measures round-trip and queue, not reading. Documented median prefill by prompt size: <200 → 30 tok/s; 200–1k → 147; 1k–4k → 616; 4k–16k → 2,887; 16k–64k → 9,536; >64k → 21,720. The 16k switch covered 71% of requests in their 24h measurement window.
- **The two are never added together** — their docs are emphatic that combining them produces "a real number for a question nobody asked."

**The dash rule:** if the generation phase measures under 200 ms, the row prints `—` instead of a rate. Some providers buffer a whole reply and flush in one frame; dividing by a collapsed generation phase would print nonsense like "22,000 tok/s". A dash also appears when the request failed before a model answered or produced no output tokens. Affects ~3% of requests, almost all short ones.

**Clock boundaries:** both clocks run from gateway→provider connection open to provider response end. Inside: provider queueing + inference. Outside: the client's network round trip, Token Harbor's routing/auth/billing work, and the time the client takes to consume the stream.

**Why this matters to AgentKthx:** the footer's ⚡ TPS is *client-measured* (`_generate_with_retry`'s TPS trio) and includes network + gateway time, so it will systematically read lower than the usage page's tok/s for the same request. That's a definitional difference, not a bug — don't reconcile them, and don't "fix" the footer to match. The usage page (last 100 requests, per-row speeds, hover on the output column breaks out the reasoning split) is the audit surface; the footer is the live surface.

---

## Caching Behavior — Exact Cache & Prompt Cache

Two independent caching layers sit in front of the model. Both are documented, both are Token Harbor-specific (OpenRouter has neither), and both change what a retry costs and what the usage counters mean.

### Layer overview (documented)

| Layer | Triggered when | What it saves |
|-------|----------------|---------------|
| **Prompt cache** (model maker's side) | Long repeated prefixes (≥1024 tokens), on models that support prompt caching | ~90% off the cached input portion |
| **Exact cache** (Token Harbor's) | Same model + same messages + same sampling within 5 minutes, on `/v1/chat/completions` | 100% — billed $0, the model is not called |

Cache savings show as a green dollar line on `/dashboard/usage` under Today's Usage.

### Exact cache rules (documented)

- Endpoint: **`/v1/chat/completions` only.** The Anthropic endpoint and web chat are not served from it.
- Identity: same model + same messages + same sampling, within 5 minutes.
- **Requests that carry `tools` always skip it** — never cached, never served from it.
- A cached hit adds response header **`X-TH-Cache-Layer: exact`**.
- Per-request control header, **`X-TH-Cache-Control`**:

| Value | Behaviour |
|-------|-----------|
| `bypass` | Skip lookup. The response is still written into the cache, so the *next* identical request can hit. |
| `force-refresh` | Skip lookup AND skip write. Pure passthrough — for testing / fresh-roll scenarios. |

- When served from the exact cache, "billed $0, the model is not called."

**AgentKthx implications:**

- The agentic loop mutates history every turn, so natural traffic mostly misses the exact cache. The two realistic hit patterns are (a) a user re-sending the same one-shot prompt, and (b) **the shared retry loop** — a `_make_api_request` retry after a timeout re-sends an identical body, and if the first attempt actually completed server-side, the retry is free. That's a cost win, but it also means a retry's usage row shows $0/token-less numbers; don't misread a $0 row as a billing bug.
- `/model` switching then re-asking the same question *can* hit if model + messages + sampling all match — switching model defeats it by design.
- Deterministic samplers (temperature 0) maximize accidental hits. If a benchmark or eval harness re-runs the identical prompt within 5 minutes expecting fresh inference, it will get cached replies — send `X-TH-Cache-Control: force-refresh` for that case. The plugin should NOT send any cache-control header by default; expose nothing until a real need appears (no new env vars for speculative features).

### Prompt cache and auto-marking (documented — Token Harbor's differentiator)

Claude models bill a reused prompt at a tenth (a twenty-fifth on "Claude Fable 5.1", as their docs name it) — but only the part before an explicit `cache_control` mark. Most OpenAI-shaped clients never place the mark because the field is Anthropic's. Token Harbor's gateway marks it for you:

- **When:** only if your request carries **no cache marks of its own**. One mark anywhere — tool, system block, or message block — and "the request is yours to control"; the gateway never touches or adds to it.
- **Where:** on **both** endpoints (`/v1/chat/completions` and `/v1/messages`) and the web chat.
- **The heuristic** (they only mark "where reuse is near certain"): the request carries tools, **or** the conversation is already ≥3 messages deep, **and** the reusable prefix is over ~1,200 tokens. Rationale: a cache *write* costs 1.25×, so marking a one-shot request would make it more expensive, not less. "When in doubt we leave your request alone."
- **Economics:** write = 1.25×; read = 0.1× (0.025× on Fable 5.1). Cached entries live 5 minutes, refreshed on each hit.
- **Coverage is a floor, not a promise:** for Claude, tools and system prompt are always covered; conversation history is covered "on some connections and not others" — a Claude request may be served over more than one connection, they don't all cache the same amount, and which one serves a request can change without notice. Certainty requires placing your own mark.
- **Anthropic's limits** (quoted): at most **four marks per request** across tools + system + messages; a cache lookup walks back at most **20 content blocks**. A single turn emitting more than twenty blocks — "an agent making many tool calls" — can step past its own previous mark; the fix is a second mark part-way through.
- **Non-Claude models:** "Nothing to do — they cache automatically or not at all." The auto-marker's value is Claude-specific.

**The standard growing-conversation pattern** (their doc): one mark on the last content block of each request; keep it there as the conversation grows; don't remove earlier ones; don't move it by hand.

```json
{
    "model": "claude-fable-5.1",
    "messages": [
        { "role": "user", "content": "…" },
        { "role": "assistant", "content": "…" },
        {
            "role": "user",
            "content": [
                { "type": "text", "text": "…", "cache_control": { "type": "ephemeral" } }
            ]
        }
    ]
}
```

**Reading whether it worked** (their usage block):

```json
"usage": {
    "input_tokens": 812,
    "cache_creation_input_tokens": 11702,
    "cache_read_input_tokens": 0
}
```

- `cache_creation_input_tokens` — written this turn, billed 1.25×.
- `cache_read_input_tokens` — read from cache, billed 0.1×.
- On turn 2, `cache_read` should climb while `input_tokens` stays small. If `cache_read` is stuck at the same number while input grows, only the static prefix is being cached — the history isn't covered; the fix is the mark above.

**AgentKthx implications:**

- The native-tools path always sends `tools` → the auto-marker's "carries tools" branch applies on every agentic turn with a >1,200-token prefix. For Claude-family models on Token Harbor this is a real cost lever for long agent sessions — with zero client changes.
- The ReAct path folds tools into the user turn, but the "conversation ≥3 messages deep" branch still catches it once history grows past three messages.
- Should AgentKthx place its own marks? **Not in v1.** The auto-marker covers the dominant case; client-side marking interacts with the 20-block walk (AgentKthx's long tool outputs can exceed it — that's exactly the multi-block case their doc warns about) and would need its own audit surface. Register it as potential future work: an opt-in `--th-prompt-cache` flag that appends `cache_control` to the last content block, behind a per-request inspection of `cache_read_input_tokens` growth.
- **A note on comparing gateways** (their doc, worth keeping in mind for benchmarking): cache ratios between providers are only meaningful when the request is the same. Anthropic-native clients place their own marks and any pass-through gateway reports high ratios; the same client in OpenAI-compatible mode sends none, and what you compare then is each gateway's own behavior.

---

## Billing, Spend Confirmation & Usage Visibility

### One wallet

Token Harbor has one balance in USD; web chat, the OpenAI-compat API, and the Anthropic-compat API all draw from the same wallet. New accounts start at **$0** — no sign-up or welcome credit — but selected `:free` models are usable immediately (section 13).

- **Top-up packages** (documented): Starter $10, Community $50, Harbor $100. Card (Stripe) or PayPal. **Credits never expire.**
- **Refunds:** unused wallet balance refundable within 30 days of the original top-up (email billing@tokenharbor.ai). Already-consumed balance, promotional credits, and reward credits are not refundable.
- **Promotional/reward credit** spends on any model but is not withdrawable. Rewards include the first top-up match (100% up to $100, available 14 days after registration, credited locked — unlocks $1 per $1 spent from paid balance) and spend milestones ($10→$2, $50→$10, $200→$50 lifetime, each once, claim needs verified email). All rewards/promo credit count toward a **$500 lifetime cap** per account.
- **Passes** (subscription): Agent $0.99 first month then $1.99/mo — $10 four-week allowance + 5% PAYG discount; Office $9.99/mo — $35 + 10%; Frontier $99/mo — $180 + 15%. The allowance is *included usage value*, not wallet credit — consumed automatically on eligible paid models, shared across all covered models, divided into four 7-day windows per four-week cycle with **no carryover**. **Model Boost** stretches the allowance on selected models (e.g. a 2× Boost makes $10 of allowance cover $20 of usage value). After exhaustion, pay-as-you-go continuation at the Pass's discount can draw on the wallet (toggle: Dashboard → Billing → "Keep working after my Pass allowance runs out"). Model coverage is by price tier plus a named-model list (Agent: ≤$0.20/M in and ≤$1/M out, plus `gpt-5.6-luna`, `mimo-v2.5-pro`; Office adds ≤$1/M in and ≤$4/M out plus `muse-spark-1-3`, `claude-sonnet-5.5`, `claude-sonnet-5`, `gpt-5.6-terra`, `deepseek-v4-pro`, `qwen3.8-max`, `glm-5.3`, `grok-4.7`, `grok-4.6`; Frontier adds everything else). Free `:free` access remains independent of Passes.

### Spend formula

Per-token at the model's listed price, plus optional per-model markup (0% on every model today) — see the formula in section 6. No volume discount, no per-turn fee.

### Confirming a large call — the spend gate (documented)

> "If a single API call could cost **more than half of your current balance**, it is **paused before it runs** and you're asked to confirm."

- Protects a small wallet from one oversized request (their example: a whole repository pasted into one prompt).
- The estimate is **deliberately pessimistic**: full prompt with no cache discount and the full `max_tokens` output cap. Most calls cost much less than the estimate.
- Calls paid from a Pass allowance or the free allowance are **never paused** (they don't draw on the balance).
- The pause surfaces as **HTTP 400, code `spend_confirmation_required`** (`invalid_request_error` on `/v1/messages`). The message shows the estimate. **SDKs don't retry it on their own.**
- To run the call anyway, send either:
  - the body field `"th_confirm_spend": true`, or
  - the header `x-th-confirm-spend: true` (for tools that can't add body fields).
- Sending the header on **every** request turns the check off for that client. Or keep the check and lower the estimate: smaller `max_tokens`, or top up so half the balance covers the call.

**AgentKthx policy: never auto-confirm.** `spend_confirmation_required` must surface to the user as a fatal, remediation-bearing error (CloudBackend `_STATUS_REMEDIATIONS[400]` entry), NOT be retried, and NOT be auto-confirmed by the plugin. The whole point of the gate is that the *user* decides whether a >½-balance call runs. An `AGENTKTHX`-side escape hatch (e.g. honoring a user-set `x-th-confirm-spend` header for scripts) can be future work behind an explicit env var — and it must print the estimate first. This posture matches the audit register's philosophy on silent spend (cf. ROB-31's entitlement-blindness lesson on Pollinations).

### Seeing your balance and usage

- **Balance**: the dashboard's top-bar pill updates live. There is **no documented API for balance** (contrast OpenRouter `GET /credits`) — see section 14.
- **Per-request detail**: `/dashboard/usage` shows the **last 100 requests** with exact tokens-in / tokens-out / cost-paid for each, plus cache layer and speed figures; CSV export from the same page.
- **Ledger**: Dashboard → Billing → Top-ups filter → Export CSV → `tokenharbor-ledger-<date>.csv`. Each row: Date (ISO UTC), Type (e.g. `Recharge`), Source pool (`Paid pool` for top-ups), Amount USD, Running balance USD, Status, Order ID (PayPal order or Stripe `cs_…` session), PayPal capture ID (or Stripe `pi_…` payment). Append-only; reconcile against PayPal/Stripe receipts by capture ID + amount.

---

## Error Codes & Recovery

Token Harbor's docs name fewer error codes than OpenRouter's error-catalog page, and never print an error envelope. The codes below are **documented** (each with its source page); the envelope shape is **⚠️ UNVERIFIED** — assume the OpenAI `{"error": {"code": ..., "message": ...}}` shape that CloudBackend's `_parse_error_envelope()` already tolerates, and verify at probe time.

### Documented codes

| Code | Code string / signal | Meaning (documented) | Source | Fix |
|------|----------------------|----------------------|--------|-----|
| 400 | `spend_confirmation_required` | Single-call estimate exceeds half the current wallet balance; the call was paused before it ran. (`invalid_request_error` type on `/v1/messages`; on the OpenAI wire the code string is the documented signal.) | Credits & top-ups | Human decision: re-send with `th_confirm_spend: true` or `x-th-confirm-spend: true`; or lower `max_tokens`; or top up. **AgentKthx: never auto-confirm — surface remediation text and stop.** |
| 400 | `context_length_exceeded` (forwarded verbatim) | The upstream model refused the request — Token Harbor never truncates and forwards the model's own error unchanged. | Models → Context limits | Shrink context: `/clear`, fewer/shorter history, smaller `--num-ctx` expectations. CloudBackend's existing context-length 400 handling (`_context_safe_max_tokens`) applies. |
| 401 | — (browser probe documented: `/v1/models` without a key answers `401`) | Missing/invalid/malformed key. Also the signature of an account-wide **Rotate** (every key revoked). | Models; Universal Key | Verify `TOKENHARBOR_API_KEY` starts `thk_` and was pasted in full (no spaces — see the PowerShell note, §2). If other integrations also just started failing, the user hit Rotate: create a new key. |
| 403 | `vendor_not_allowed` | The key's vendor allow/block list excludes this model's maker. Per-key, other keys unaffected. | Universal Key → Per-key limits | Use a model from an allowed maker, or a different key. Note: the dashboard has no UI to edit these lists (documented). |
| 429 | — (generic) | Free-account rate limit: 60 req/min + 1,800 req/hour per account, or 100/min + 3,000/hour per IP. | Rate limits | Honor `Retry-After` (documented: "Wait that many seconds and send the request again"). Shared retry loop handles it. |
| 429 | `spend_limit_exceeded` | Per-key **spend cap** (daily/weekly/monthly rolling USD window) reached. Includes `Retry-After`. | Universal Key → Per-key limits | Wait for spend to age out of the rolling window, raise the cap, or switch keys. **Quota-class, not rate-class** — see mapping below. |

### Undocumented / unverified codes

- **402**: OpenRouter uses 402 for insufficient credits; Token Harbor's docs describe wallet exhaustion only through the spend-confirmation gate and `spend_limit_exceeded`. What a chat call with a $0 balance on a paid model returns is **not documented** ⚠️ UNVERIFIED. Probe it; expect 400- or 402-class with a balance message. CloudBackend's `_looks_like_quota_exhaustion` hook should match balance-exhaustion phrasing ("balance", "insufficient", "top up") when confirmed.
- **5xx family**: no gateway timeout / upstream-down taxonomy is documented. The shared `_is_retryable_http_status()` treatment (5xx transient, backoff with jitter) applies by default. Automatic provider failover ("fallback to another provider serving the same model") is internal and happens *before* an error reaches the client, so surviving 5xx should be rarer than on single-provider backends.
- **SSE error frames**: not documented. CloudBackend's SSE iterator treats malformed/terminal frames defensively; verify against a live stream.

### 429 `Retry-After` handling (shared machinery)

Both 429 variants carry `Retry-After`. The inherited behavior needs no backend-specific code:

```python
# CloudBackend._compute_retry_after(headers, attempt) — inherited
# 1. Retry-After header (float seconds) — honored as-is
# 2. Fallback: exponential backoff with ±jitter, capped
# Retry budget: _MAX_RETRIES class attr (subclass-tunable)
```

The distinguishing logic belongs in the **quota hook**, not the retry loop:

```python
# Planned TokenHarborBackend overrides (hooks-only convention, MAINT-28):

_STATUS_REMEDIATIONS = {
    400: ("spend_confirmation_required — the estimate for this single call exceeds "
          "half your Token Harbor balance. Confirm deliberately (body "
          "th_confirm_spend=true or header x-th-confirm-spend=true), lower "
          "max_tokens, or top up. AgentKthx never auto-confirms."),
    401: ("Key rejected. Verify TOKENHARBOR_API_KEY (thk_live_…, pasted in full). "
          "If every integration on the account started failing at once, the "
          "account-wide Rotate was used — create a new key at /dashboard/api-keys."),
    403: ("vendor_not_allowed — this key's vendor allow/block list excludes the "
          "model's maker. Use an allowed maker or another key."),
}

def _looks_like_quota_exhaustion(self, status_code: int, body_text: str) -> bool:
    """429 spend_limit_exceeded (or balance-exhaustion phrasing) = quota-class
    fast-fail; everything else stays rate-class (retryable)."""

def _quota_exhaustion_message(self) -> str:
    return ("Token Harbor key spend cap reached — the rolling USD window must "
            "age out, or raise the cap / switch keys at /dashboard/api-keys.")
```

Rationale for treating `spend_limit_exceeded` as quota-class: the MAINT-28 hook contract distinguishes *fatal-ish quota* (NVIDIA monthly credits) from *retryable rate limits* because they share HTTP 429. A spend cap does lift on its own (rolling window), but only after spend ages out — potentially hours. Hammer-retrying inside the retry budget is pure waste; failing fast with remediation matches the NVIDIA precedent.

---

## Rate Limiting & Concurrency

### The limit table (documented)

| Account state | Request rate | Notes |
|---------------|--------------|-------|
| **Paid** (active Pass, **or** any topped-up wallet) | **No requests-per-minute or requests-per-hour limit** | "An account with an active Pass, or any account that has topped up its wallet, has no requests-per-minute or requests-per-hour limit." |
| **Free account** — per **account** | 60 requests/min + 1,800 requests/hour | Shared across **all** the account's API keys and all models |
| **Free account** — per **IP address** | 100 requests/min + 3,000 requests/hour | Second dimension; a shared NAT/office IP can trip it independently |
| **Image generation** (`/v1/images/generations`) | 10 requests/min + 150 requests/hour | Out of AgentKthx scope; listed for completeness |
| **Free promotional (`:free`) models** | No extra per-model limit | "only the account limits above" — a real difference from OpenRouter, whose free tier is per-model 20 rpm |
| **Concurrency** (any plan) | **No limit on concurrent requests** | Documented verbatim |

Applies to: `/v1/chat/completions`, `/v1/messages`, `/v1/responses`, `/v1/images/generations`. (Web chat has its own separate limits — `docs/web-chat/limits` — irrelevant to the plugin.)

### What counts as a request

"Each API call is one request. In agent tools — Claude Code, Codex, Cursor, opencode and the rest — every tool-call round trip is its own call, so a single task can use several requests." AgentKthx's agentic loop maps 1:1: every `_generate_with_retry` call that reaches the gateway is one request against the free-tier budget (retries after a *failed* request presumably don't count as separate completed requests — undocumented ⚠️, but the 429-plus-retry flow is designed around it).

### When you go over

HTTP `429` with a `Retry-After` header: "Wait that many seconds and send the request again; the agent tools above do this for you." AgentKthx is in that category — the shared `_make_api_request` retry loop (Retry-After honored, capped backoff otherwise) is exactly the documented expectation.

### Key limits are spending caps

Restated from section 3 because it's the most commonly misread row: the limits you set on an API key in the dashboard are **caps in USD, not request rates**. They stop a key once it has spent that much (`429` + `spend_limit_exceeded` until the rolling window ages out).

### AgentKthx posture

- **No client-side rate limiting** (same posture as the OpenRouter backend). The free-tier budget (60/min per account) is already generous relative to a single interactive agent loop; the per-hour ceiling (1,800/h) only matters for bulk workflows (`agentkthx test` benchmarks, batch runs), and the 429 loop + `Retry-After` is the correct mechanism there.
- **Paid-account reality check**: since *any* top-up removes all rate limits, the practical free-vs-paid boundary is "$0 balance" vs "has ever topped up". A user with $2 left is rate-limit-free but spend-gated — the two failure modes (429 rate vs 429 `spend_limit_exceeded` vs 400 `spend_confirmation_required`) must stay distinct in remediation text.
- **Headroom for the catalog probe**: `GET /v1/models` burns a request. At 60/min that's negligible per session, but `agentkthx models` sweeps across backends should stay behind the existing L1/L2 cache TTLs rather than re-fetching per invocation.

---

## Free Tier Behavior — `:free` Rules

> This section was flagged as a must-include. It is the section to re-read whenever the question is "what can a $0-balance account actually do on Token Harbor?"

### Model ID convention

- Free routes are **explicit model IDs ending in `:free`** — e.g. `deepseek-v4.1-flash:free` (the quickstart's example; also what Token Harbor's own connect CLI selects for the Qwen Code adapter).
- "Free routes never charge your balance; **the base model IDs remain paid routes**." I.e. `deepseek-v4.1-flash:free` is free while `deepseek-v4.1-flash` bills — the suffix is the route selector, not a model property.
- The current set "is listed under **Free** on the Models page and changes as models are added or retired." There is no named free-router id (contrast OpenRouter's `openrouter/free` meta-model).

### What "free" actually means on Token Harbor

| Aspect | Documented behavior |
|--------|---------------------|
| Token cost | $0 — "their `:free` model IDs never charge your balance" |
| Allowance | A **personal rolling 7×24-hour period**, started by your first free request. Not a calendar week, not midnight UTC |
| Allowance size | Measured by the **list-price value of the work**, not a fixed request count. The dashboard shows a **0–100% progress bar** |
| Rate limits | Free *promotional* models have **no extra per-model limit** — only the account limits (60/min + 1,800/hr per account; 100/min + 3,000/hr per IP) |
| Concurrency | Unlimited (same as everything else) |
| Campaigns | Limited-time free routes with an end date. A campaign either carries **its own separate allowance** or **draws on the free-tier allowance**; the dashboard's progress bar states which, plus the next refresh time. A campaign sharing the free-tier bar shows the same percentage "because it is the same allowance" |
| Privacy | "Free-route prompts and responses may be retained by Token Harbor **only after you enable free models**. See Privacy for details." (Paid routes: "not kept for training and are not stored beyond a five-minute exact-match cache.") |
| Spend gate | Calls paid from the free allowance are **never paused** by the >½-balance confirmation gate (they don't draw on the balance) |
| Passes | Free access remains active alongside any Pass; subscribing doesn't reset or enlarge it |

The allowance is a *value* ceiling in disguise: at list prices, a $X-equivalent of free work per rolling week. The docs don't publish the number — the progress bar is the only meter. **The practical cap for an agent run is therefore (allowance remaining) × (model's list price)⁻¹**, unknown until the bar is observed live.

### Contrast with OpenRouter's free tier

| | Token Harbor `:free` | OpenRouter `:free` (documented in the OpenRouter reference) |
|--|----------------------|--------------------------------------------------------------|
| Billing | $0, never touches balance | $0 |
| Rate shape | Account-level 60/min + 1,800/hr (shared across ALL keys and models) | Per-model 20 req/min + ~200/day, **50 requests/day cap for unfunded accounts** |
| Allowance metering | List-price value, rolling 7×24h, progress bar | Request counts, per model |
| Named router | none | `openrouter/free` (routes to cheapest free model) |
| Tool calling | Undocumented per-model; same NATIVE-assumption + ReAct fallback posture | Varies by free provider; 400→ReAct fallback |

### Detection and AgentKthx wiring

```python
def _is_free_model(self, model: str) -> bool:      # CloudBackend hook — inherit as-is
    return model.endswith(":free")
```

- The inherited `_is_free_model()` (suffix check) is exactly right for Token Harbor — no whitelist beyond the suffix is needed, and none is possible docs-only (the live set changes).
- Planned env var: **`TOKENHARBOR_FREE_ONLY=1`** — filter `list_models()` output to `:free` ids only, mirroring `OPENROUTER_FREE_ONLY`. Filter at *return* time, not cache-store time (the ROB-42 cache contract keeps the unfiltered catalog; toggling the flag must not wait out a TTL).
- Default model (shipped): **`mimo-v2.6-flash:free`** — the scaffold default was the docs-named `deepseek-v4.1-flash:free` (the only `:free` id Token Harbor's docs name in an actionable context), but the maintainer's live smoke found it returning 402 "We can't serve this model on this account" on a free account, so the default moved to `mimo-v2.6-flash:free` (live-verified 5/5: models listing, thinking, shell tools both modes). Either id is perishable — re-verify against the live Free list at probe time; the seed catalog's `:free` entries must be marked perishable.
- **FEAT-08 interplay** (free-TIER mode, OPEN in the audit register): Token Harbor is the cleanest FEAT-08 target yet — a standing documented free tier with an account-level (not per-model) limit and an explicit `:free` selector. The register's FEAT-08 design should treat `tokenharbor` as a reference implementation candidate when it lands.
- **Privacy note for users**: enabling free models opts the account into free-route retention (per their docs). This is the user's dashboard choice, not AgentKthx's — but it belongs in the plugin's README row of `docs/SUPPORT.md` once scaffolded, so users make it knowingly.

---

## Attribution & Usage-Metrics Gaps vs OpenRouter

AgentKthx's R07.21 work built OpenRouter-specific attribution (marketplace headers) and documented a large usage-analytics surface (`/key`, `/credits`, `/generation`, `/activity`, `/datasets/*`, `/analytics/query`). **None of that surface exists on Token Harbor's documented API.** This section records the gaps so nobody hunts for endpoints that aren't there.

| OpenRouter capability (R07.21 reference) | Token Harbor documented equivalent | Gap disposition |
|------------------------------------------|------------------------------------|-----------------|
| App Attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`, `X-OpenRouter-Categories`, …) | **None documented.** No header, no marketplace, no app directory. | Do **not** send the OpenRouter headers to Token Harbor — they'd be inert (handling of unknown headers is undocumented ⚠️). Keep the attribution helper OpenRouter-scoped. Revisit if Token Harbor publishes an attribution spec. |
| `GET /key` — per-key limit envelope (`limit_remaining`, `free_model_daily_requests`, …) | None. Per-key caps are dashboard-only. | The CLI can't show remaining budget from the API. Future option: parse the `Retry-After`/error bodies (reactive only). |
| `GET /credits` — wallet balance | None. Balance is the dashboard pill. | Same. |
| `GET /generation?id=` — per-generation audit (cost, provider, cache tokens, latency) | Partially covered **inline**: the usage row appears on `/dashboard/usage` (last 100 requests, tokens/cost/cache layer/speeds, CSV export). No documented per-request API lookup. | For audit: direct users to `/dashboard/usage`. The response `id` (unverified shape) is still worth preserving in the raw response for support requests. |
| `GET /activity`, `/datasets/*`, `/analytics/query` | None documented. Ledger CSV (`tokenharbor-ledger-<date>.csv`) covers payment reconciliation, not API analytics. | Out of scope until Token Harbor ships something. |
| App marketplace / rankings | None. | The `cli-agent`-category strategy has no Token Harbor counterpart. |
| `provider` routing object, `transforms`, `plugins` (web search) | None documented; routing is automatic ("cheapest healthy upstream"). | Nothing to send; nothing to map. |

**Net effect for the plugin:** Token Harbor is *thinner* than OpenRouter on client-visible metadata but *thicker* on inline economics (usage rows with cache layer + speed per request; the exact-cache header; prompt-cache usage fields). The natural AgentKthx wiring is: consume what the chat response itself carries (`usage`, `X-TH-Cache-Layer`), and leave everything dashboard-side to the dashboard.

---

## Using Token Harbor Today (Pre-Scaffold)

A native `tokenharbor` backend doesn't exist yet — but the existing `openai` backend already supports arbitrary OpenAI-compatible base URLs (`OPENAI_BASE_URL` is honored at `OpenAIBackend.__init__`, and both `list_models()` and `generate()` resolve against `{base_url}/…`). Token Harbor is therefore usable **today**, before any plugin code is written:

```bash
# Stopgap configuration — the OpenAI backend pointed at Token Harbor
export OPENAI_BASE_URL="https://tokenharbor.ai/v1"
export OPENAI_API_KEY="thk_live_…"                        # the Universal Key
export OPENAI_DEFAULT_MODEL="deepseek-v4.1-flash:free"    # override the gpt-6-sol default!

agentkthx chat --backend openai
agentkthx models --backend openai      # GET /v1/models — works, needs the key (docs-verified)
```

What works through the stopgap, per Token Harbor's documented behavior:

- **Chat + streaming** — `/v1/chat/completions` is the same endpoint the native plugin will call. Native tool calling works or falls back to ReAct exactly as with real OpenAI (`_is_tools_not_supported_error()` needs no change).
- **Catalog** — `agentkthx models` fetches Token Harbor's live catalog because `OpenAIBackend.list_models()` hits `{base}/models`. Without a key it degrades per the OpenAI backend's own narrowed-except handling.
- **`:free` routing** — pass a `:free` id and the wallet is never charged (documented). `--model` on the CLI selects it per-run; `OPENAI_DEFAULT_MODEL` pins it.

What the stopgap does NOT give you — and why the native backend (section 16) is still worth building:

| Gap in the stopgap | Why it matters |
|--------------------|----------------|
| `OPENAI_FREE_ONLY` is OpenAI-curated (a hardcoded 6-model whitelist; real OpenAI has no $0 models) | **Do not enable it against Token Harbor** — Token Harbor's `:free` ids won't match the whitelist, so the filter returns 0 models. The native backend's suffix-based `_is_free_model` + `TOKENHARBOR_FREE_ONLY` is the correct mechanism |
| OpenAI's `insufficient_quota` 429 classifier won't match Token Harbor's balance semantics | `spend_confirmation_required` (400) and `spend_limit_exceeded` (429) get generic remediation, not the §16 quota-class handling |
| Usage-row fields may use Anthropic-style names (§4, unverified) | The OpenAI backend's parser reads OpenAI names; token counters/ctx% in the footer may read 0 until the dual-family parser lands |
| No seed-catalog key | `agentkthx models` is live-only through the stopgap; offline fallback stays empty for Token Harbor ids |
| OpenAI-specific defaults leak through | `reasoning_effort` forwarding, the `service_tier` enum validation, and OpenAI's max-context catalog lookups all assume real-OpenAI semantics — harmless on Token Harbor (forwarded/ignored), but not TH-aware |

The stopgap is also the cheapest live-probe vehicle for the §16 open-questions checklist: with `AGENTKTHX_DEBUG=1`, a handful of manual runs answer the usage-field family, SSE grammar, `finish_reason` enumeration, and error-envelope questions without writing any plugin code. Probe results then land in this doc's ⚠️ UNVERIFIED markers before scaffolding starts.

---

## Implementation Notes for AgentKthx (Backend #14, Scaffolded)

> **Status: SCAFFOLDED (R07.31).** `agentkthx/plugins/tokenharbor/` exists (`__init__.py` + `plugin.json` + `tokenharbor.py`, `TokenHarborBackend(CloudBackend)` hooks-only); `BackendType.TOKENHARBOR` is the 14th member; the mocked regression suite lives at `tests/test_tokenharbor_backend.py` (78 tests, all green; the full suite stays green). The companion changes below are all landed: the `config.py` block, the seed-catalog `"tokenharbor"` key (docs-only ids, `_tokenharbor_note` metadata marker), the `agentkthx config` env-reference rows + auth rows, the `agentkthx auth` picker row (with a shown-once key prompt hint), the `docs/SUPPORT.md` **Limited Support** row, `scripts/probe_tokenharbor.sh`, and the inert `smoke_test.sh` wiring. Two deliberate deviations from the plan text below, both noted in code: (1) the 400 spend-gate text in `_STATUS_REMEDIATIONS` is delivered via a `_raise_non_retryable_status` override that checks the body for `spend_confirmation_required` first — a blanket 400 entry would mislabel every tools-rejection/context 400 as a spend gate and break the ReAct fallback detector; (2) the manifest's `compatibility` floor is `>=0.7.28` (the R07.28-hardened shared transport this backend inherits), not `>=0.5.0`. Everything else in this section is implemented as written; the ⚠️ UNVERIFIED wire-shape questions remain open until `scripts/probe_tokenharbor.sh` runs against the live gateway.

### Where it slots in

```
agentkthx/plugins/tokenharbor/
├── __init__.py           # register()/unregister() — plugin spec v0.2
├── plugin.json           # manifest (below)
└── tokenharbor.py        # TokenHarborBackend(CloudBackend)
```

Companion changes, in dependency order:

1. `core/types.py` — add `TOKENHARBOR = "tokenharbor"` to `BackendType` (14th member; update the enum comment — and note MAINT-33: the DDG enum comment went stale once, keep the new one current).
2. `config.py` — env-var block (below), following the SiliconFlow block's comment convention (points at this doc).
3. `agentkthx/data/model_seed.json` — `"tokenharbor"` key (docs-only entries, marked perishable).
4. `cli/commands/config.py` — env reference rows (the table that documents `SD_BASE_URL`, `DUCKDUCKGO_*` etc.).
5. `docs/SUPPORT.md` — add to **Limited Support** (new scaffold; stays Limited until the maintainer's 5/5 smoke passes — the SiliconFlow/Cloudflare promotion path).
6. `scripts/probe_tokenharbor.sh` — sibling of `probe_openrouter.sh` (key check → `/v1/models` → one non-streaming call → one streaming call).

### plugin.json manifest

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "tokenharbor",
  "version": "0.1.0",
  "description": "TokenHarbor API backend — one key, every model, via OpenAI Chat-Completions",
  "author": { "name": "VTSTech", "url": "https://www.vts-tech.org" },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "TokenHarbor Cloud Backend",
      "type": "backend",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "TOKENHARBOR",
        "defaults": {
          "TOKENHARBOR_API_KEY": "",
          "TOKENHARBOR_BASE_URL": "https://tokenharbor.ai/v1",
          "TOKENHARBOR_DEFAULT_MODEL": "mimo-v2.6-flash:free",
          "TOKENHARBOR_FREE_ONLY": "false"
        }
      },
      "provides": {
        "backends": { "tokenharbor": "tokenharbor.TokenHarborBackend" },
        "cli_commands": [],
        "cli_flags": { "--backend": ["tokenharbor"] }
      },
      "compatibility": { "agentkthx": ">=0.7.28" }
    }
  }
}
```

### config.py block (planned)

```python
# docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md — TokenHarbor cloud backend (planned #14).
# OpenAI-compatible surface at https://tokenharbor.ai/v1 (the Anthropic /v1/messages
# surface is out of scope). GET /v1/models REQUIRES AUTH (docs-verified) — the seed
# catalog is the only cold-start catalog source.
TOKENHARBOR_BASE_URL = os.environ.get("TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1")
TOKENHARBOR_API_KEY = os.environ.get("TOKENHARBOR_API_KEY", "")
# Default model: live-verified in the maintainer's 5/5 :free smoke pass.
# Was the docs-named deepseek-v4.1-flash:free until it started returning
# live 402 "We can't serve this model on this account". Perishable —
# re-verify against the live Free list at probe time. :free routes never
# charge the wallet.
TOKENHARBOR_DEFAULT_MODEL = os.environ.get("TOKENHARBOR_DEFAULT_MODEL", "mimo-v2.6-flash:free")
# Filter list_models() to :free ids only. Applied at RETURN time, not cache-store
# time (OPENROUTER_FREE_ONLY precedent — toggling must not wait out the cache TTL).
TOKENHARBOR_FREE_ONLY = os.environ.get("TOKENHARBOR_FREE_ONLY", "").lower() in ("1", "true", "yes")
```

`agentkthx auth` gains a `tokenharbor` row (persists to `~/.agentkthx/.env`) — key acquisition is dashboard-only, so the auth picker's help text should say so ("create at tokenharbor.ai/dashboard/api-keys; shown once").

### Class skeleton (hooks-only, per the MAINT-28/R07.29 contract)

```python
class TokenHarborBackend(CloudBackend):
    """TokenHarbor gateway — one key, every model (OpenAI wire only).

    Wire grammar: OpenAI Chat-Completions, inherited unchanged (no _wire_adapter).
    Key: thk_live_… bearer. /v1/models requires auth (docs-verified difference
    from OpenRouter). Two server-side caches (exact + prompt) documented in
    docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md §9.
    """

    _default_base_url = TOKENHARBOR_BASE_URL
    _api_key_env_var = "TOKENHARBOR_API_KEY"
    _provider_label = "tokenharbor"
    _error_brand = "Token Harbor"

    _STATUS_REMEDIATIONS = {  # see §11 for the full text
        400: "...spend_confirmation_required remediation...",
        401: "...key rejected / account-wide Rotate...",
        403: "...vendor_not_allowed remediation...",
    }

    def backend_type(self) -> BackendType:
        return BackendType.TOKENHARBOR

    def _looks_like_quota_exhaustion(self, status_code: int, body_text: str) -> bool:
        # 429 + spend_limit_exceeded, or balance-exhaustion phrasing → quota-class
        # (fast-fail with remediation). Everything else stays rate-class.
        ...

    def _quota_exhaustion_message(self) -> str:
        return ("Token Harbor key spend cap reached — the rolling USD window must "
                "age out, or raise the cap / switch keys at /dashboard/api-keys.")

    def _validate_api_key(self, key: str) -> None:
        # shape check: thk_live_ + 64 base64url chars (reject sk-or-/sk- early —
        # the connect CLI docs confirm an OpenAI sk- key "won't work here")
        ...
```

Everything else is inherited: `_make_api_request` (retry loop, Retry-After, network backoff), `_iter_sse_lines` (SSE), `_build_openai_body` (sampling fields), `_parse_openai_response` (extended to accept Anthropic-style usage field names — see the test plan), `test_tool_support` (override to unconditional NATIVE, OpenRouter aggregator assumption), `_is_free_model` (suffix check, inherited as-is), `get_stale_models` (ROB-42 shape).

### What the plugin must NOT do (doc-pinned invariants)

1. **Never auto-confirm a spend gate.** `400 spend_confirmation_required` is user-business, not plugin-business (§10). No env var may silently inject `th_confirm_spend`/`x-th-confirm-spend` in v1.
2. **Never send the OpenRouter attribution headers** — no documented equivalent (§14); unknown-header handling is undocumented.
3. **Never strip system messages** — pass-through is documented; the DDG/SD suppression convention does NOT apply (§4).
4. **Never parse maker from model id** for vendor-list prediction — the maker derivation is server-side and undocumented; the 403 is authoritative.
5. **No cache-control headers by default.** `X-TH-Cache-Control` is a documented escape hatch, not a default; speculative env vars for it are forbidden by the house no-speculative-features rule.
6. **Catalog ladder respects the auth-gated catalog**: cold start = seed catalog; live refresh only when a key exists; never let a 401 on `/v1/models` fail agent startup (narrowed except + stale-first, the ROB-42 contract).

### Probe-stack discipline (ROB-48 family)

`_build_agent`'s remote path probes the catalog; Token Harbor adds an auth-gated wrinkle. The SD landmine (three stacked 10s probes on a hung tunnel ≈ 30s startup stall) applies if the scaffold adds probes carelessly. Rules: the backend's `list_models()` is the ONLY network call at startup; `_get_model_defaults` reads the cache; no dedicated preflight "is the key valid" ping (the first real call surfaces 401 with remediation).

### Test plan (per-release regression convention)

New file `tests/test_tokenharbor_backend.py`, all mocked (TEST-01: no integration tier), covering at minimum:

1. **Manifest**: loads through `_loader.py`, registers `tokenharbor` backend id, env defaults correct.
2. **Key validation**: rejects `sk-…`/`sk-or-…` with remediation text; accepts `thk_live_` + 64 base64url; empty key → the standard `TOKENHARBOR_API_KEY is required` ValueError.
3. **Usage-field dual-family parsing** (the §4 unverified-shape hedge): `_parse_openai_response` accepts `{prompt_tokens, completion_tokens}` AND `{input_tokens, output_tokens}`; `cache_creation_input_tokens`/`cache_read_input_tokens` surface when present, ignored when absent.
4. **Spend gate**: 400 + `spend_confirmation_required` → fatal error with `_STATUS_REMEDIATIONS[400]` text; **no retry**, no confirm injection (assert the retry loop is not entered and the body never gains `th_confirm_spend`).
5. **Quota classification**: 429 + `spend_limit_exceeded` → quota-class fast-fail with the window-aging message; plain 429 → rate-class retry honoring `Retry-After`.
6. **Free tier**: `_is_free_model` suffix behavior; `TOKENHARBOR_FREE_ONLY=1` filters at return time (toggle does not wait out TTL); default model resolves from seed.
7. **Catalog**: auth-gated cold start (no key → seed catalog, no exception); ROB-42 store-on-success + stale-first; 401 on `/v1/models` degrades to seed (never fails startup).
8. **Aggregator tool assumption**: `test_tool_support` → NATIVE unconditionally; 400 "does not support tools" → ReAct fallback via the inherited detector.
9. **Pass-through invariants**: system messages forwarded (no `_strip_system_messages`); no attribution headers; no cache-control headers in the default body.

Runner expectation: all green under `python -m pytest tests/test_tokenharbor_backend.py -q` offline (mocked); smoke via `./scripts/probe_tokenharbor.sh` then `./scripts/smoke_test.sh --backend tokenharbor` once a funded test key exists (maintainer gate for promotion out of Limited Support).

### Open questions that block coding (probe checklist)

| # | Question | Blocking what |
|---|----------|---------------|
| 1 | `/v1/models` entry schema (id field name? context_length? pricing? modality?) | Seed refresh + models-table columns |
| 2 | Usage field family on the OpenAI wire (OpenAI names, Anthropic names, or both) | `_parse_openai_response` + footer token counters |
| 3 | Error envelope shape + the $0-balance-on-paid-model status code | Error mapping + quota hook phrases |
| 4 | SSE frame grammar + whether a usage-bearing final chunk exists | Streaming path, chars÷4 fallback trigger |
| 5 | `finish_reason` enumeration (esp. tool_calls + length) | Agentic loop finish handling |
| 6 | Reasoning deltas presence on thinking models | `reasoning:` panel wiring |
| 7 | Live `:free` set + allowance bar behavior | Default model + FEAT-08 |
| 8 | Exact-cache `X-TH-Cache-Layer: exact` observability through urllib streaming | Usage-row provenance display |

---

## OpenRouter → TokenHarbor Migration Map

The two-line swap from Token Harbor's compare page, plus what changes for an existing AgentKthx user moving from the `openrouter` backend to the planned `tokenharbor` one:

### The two-line swap (any OpenAI-compatible client)

```diff
- base_url = "https://openrouter.ai/api/v1"
- api_key  = "sk-or-..."
+ base_url = "https://tokenharbor.ai/v1"
+ api_key  = "thk_..."
```

### AgentKthx env-var mapping

| OpenRouter | Token Harbor (planned) | Notes |
|------------|------------------------|-------|
| `OPENROUTER_API_KEY` | `TOKENHARBOR_API_KEY` | Prefix differs (`sk-or-v1-…` vs `thk_live_…`); key is reveal-once on both, but Token Harbor has no management API at all |
| `OPENROUTER_BASE_URL` | `TOKENHARBOR_BASE_URL` | `https://tokenharbor.ai/v1` |
| `OPENROUTER_DEFAULT_MODEL` | `TOKENHARBOR_DEFAULT_MODEL` | Default differs: `openrouter/free` (meta-router) vs `mimo-v2.6-flash:free` (no documented TH router equivalent) |
| `OPENROUTER_FREE_ONLY` | `TOKENHARBOR_FREE_ONLY` | Same return-time filter semantics |
| `OPENROUTER_MAX_429_RETRIES` | (inherited `_MAX_RETRIES`) | No TH-specific override planned |

### Model IDs

- OpenRouter: `vendor/model[:free]` — vendor prefix is structural and parseable.
- Token Harbor: opaque ids ("whatever appears in `/models`") — documented examples mix `tokenharbor/qwen3-max`, `claude-opus-5.5`, `deepseek-v4.1-flash`. The only structural marker is `:free`. Anything that parses the vendor segment (e.g. the openrouter thinking-name heuristics in `openai_compat.py`) must not run against Token Harbor ids.

### Carries over unchanged

OpenAI body grammar; tools schema + `tool_choice`; SSE streaming posture; `stream_options.include_usage` tolerance; 400→ReAct tool fallback; `_is_free_model` suffix rule; `FREE_ONLY` return-time filter; CloudBackend retry/quota hook architecture; `agentkthx auth` flow (new row); seed-catalog + L1/L2 caching.

### Does NOT carry over

| OpenRouter feature | Token Harbor status |
|--------------------|---------------------|
| App Attribution headers + marketplace/rankings | No equivalent documented (§14) — send nothing |
| `/key`, `/credits`, `/generation`, `/activity`, `/datasets/*`, `/analytics/query` | No equivalents documented; usage visibility is `/dashboard/usage` (last-100 + CSV) |
| `provider` routing preferences | Routing is automatic; no client control documented |
| `transforms: ["middle-out"]` | Absent by policy — TH never truncates; `context_length_exceeded` forwards verbatim |
| `plugins: [{id: "web"}]` | No plugin system documented |
| 402 Payment Required semantics | Undocumented; balance semantics arrive as 400 `spend_confirmation_required` / 429 `spend_limit_exceeded` instead |
| 20 rpm per free model + 50/day unfunded cap | Replaced by account-level 60/min + 1,800/hr (free) and a value-based rolling `:free` allowance |

### New behaviors an OpenRouter user will notice

1. **Retries can be free** — an identical re-send within 5 minutes hits the exact cache (billed $0, `X-TH-Cache-Layer: exact`). OpenRouter never does this.
2. **A long agent session on Claude models gets cheaper turn-over-turn** — the auto prompt-cache marking (§9) with no client changes.
3. **One oversized call can pause with a 400** — the >½-balance spend gate (§10), which OpenRouter has no analog for.
4. **No rate limits at all once topped up** (§12) — but per-key USD caps then become the only ceiling, so set one on any key an agent uses.
5. **The catalog needs a key** (§6) — `agentkthx models --backend tokenharbor` without a key serves the seed catalog, not a live list.

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `401` on every call | Key not set, malformed, or pasted with a stray space/line-break (the PowerShell mangling class from TH's docs) | Verify `TOKENHARBOR_API_KEY` starts `thk_`, pasted in full. An OpenAI `sk-…` key won't work — create one at tokenharbor.ai/dashboard/api-keys |
| `401` that started suddenly, across machines/integrations at once | The account-wide **Rotate** was used — it revoked every active key | Create a new key at `/dashboard/api-keys`, update `TOKENHARBOR_API_KEY` (per-key revoke is the normal path; Rotate is the panic switch) |
| `401` on `agentkthx models` but chat works | Not possible per docs — `/v1/models` requires the same Bearer key; if seen, treat as a bug | Report with `--debug`; check the seed-catalog fallback didn't mask a 401 |
| `403 vendor_not_allowed` | The key's vendor allow/block list excludes the model's maker | Use a model from an allowed maker or a different key. No dashboard UI exists to edit the lists (documented) |
| `400 spend_confirmation_required` | Single-call estimate > half the wallet balance (pessimistic: no cache discount, full `max_tokens`) | Deliberate choice: re-send with `th_confirm_spend: true` / header `x-th-confirm-spend: true`; or lower `max_tokens`; or top up. AgentKthx never auto-confirms |
| `429` repeatedly, message mentions a spend cap | Per-key USD cap (daily/weekly/monthly rolling) reached | Wait for spend to age out of the window, raise the cap, or switch keys — retrying sooner is wasted budget (quota-class in the plugin: fast-fail) |
| `429` on a free account, no code string | Account 60/min + 1,800/hr or IP 100/min + 3,000/hr limit | Honor `Retry-After` (auto-retried by the shared loop). For bulk runs: a paid wallet (any top-up) removes rate limits entirely |
| `context_length_exceeded` | TH never truncates; the upstream model refused the full body | `/clear`, shorten history, or reduce per-turn prompt size. No gateway-side rescue exists (no middle-out) |
| Model not found / id typo | Ids are opaque and change; `:free` suffix selects the free route | Pick ids from `/models` (web) or `agentkthx models --backend tokenharbor` (seed + live). Remember: base id and `:free` id are different routes with different billing |
| Response arrived instantly and cost $0 | Exact-cache hit (`X-TH-Cache-Layer: exact` header) — same model+messages+sampling within 5 min | Expected behavior, not a billing bug. For fresh inference: change sampling or send `X-TH-Cache-Control: force-refresh` |
| `cache_read_input_tokens` stuck while input grows (Claude models) | Only the static prefix is being cached; history not covered (the documented "floor not promise" case) | Place your own `cache_control` mark on the last content block (§9 pattern); AgentKthx v1 doesn't place marks — do it via a custom request or wait for `--th-prompt-cache` |
| Footer TPS much lower than the usage page tok/s | Definitional: footer is client-measured (includes network + gateway + consumption); usage page measures gateway→provider only (§8) | Not a bug — don't reconcile. Use the usage page for model-speed questions |
| `agentkthx models` shows `tools ✓ native` for an obviously non-chat model | The OpenRouter-class ROB-39 caveat — unconditional NATIVE verdict | Runtime stays safe (400 → ReAct). Cosmetic; not worth a local blacklist |
| PowerShell manual test fails with `bad range specification` / odd `invalid_api_key` | `curl` is `Invoke-WebRequest`; `curl.exe` mangles JSON quoting | Use the `Invoke-RestMethod` form; paste the key in full with no spaces (TH's own docs call this out) |
| Startup stall when the gateway is unreachable | Remote-catalog probe family (landmine #7 / ROB-48) | The scaffold's probe-stack discipline (§16) keeps one network call at startup; if a stall appears, it's a bug — file it with the ROB-48 reference |
| No way to check remaining balance from the CLI | No balance/limits API documented (§14) | Check the dashboard (live pill) or `/dashboard/usage`; the plugin surfaces `spend_limit_exceeded`/`spend_confirmation_required` reactively |

---

## References

- **Token Harbor docs (root)**: https://tokenharbor.ai/docs
- **Quickstart**: https://tokenharbor.ai/docs/getting-started/quickstart
- **Universal Key**: https://tokenharbor.ai/docs/getting-started/universal-key
- **Chat APIs**: https://tokenharbor.ai/docs/api/curl
- **Models & billing**: https://tokenharbor.ai/docs/api/models
- **Prompt caching on Claude**: https://tokenharbor.ai/docs/api/prompt-caching
- **How we measure speed**: https://tokenharbor.ai/docs/api/speed
- **Rate limits**: https://tokenharbor.ai/docs/api/rate-limits
- **Credits & top-ups**: https://tokenharbor.ai/docs/billing/credits
- **Rewards**: https://tokenharbor.ai/docs/billing/cashback
- **Subscription (Passes)**: https://tokenharbor.ai/docs/billing/subscription
- **Referrals**: https://tokenharbor.ai/docs/billing/referrals
- **Connect CLI**: https://tokenharbor.ai/docs/connect
- **Token Harbor vs OpenRouter**: https://tokenharbor.ai/docs/compare/openrouter
- **Model catalog (web)**: https://tokenharbor.ai/models
- **Dashboard — API keys**: https://tokenharbor.ai/dashboard/api-keys
- **Dashboard — usage**: https://tokenharbor.ai/dashboard/usage
- **Privacy**: https://tokenharbor.ai/privacy
- **Terms**: https://tokenharbor.ai/terms
- **Related AgentKthx docs**: `docs/api/OPENROUTER_API_TECHNICAL_REFERENCE.md` (the sibling this doc mirrors) · `docs/SUPPORT.md` (tier assignment at scaffold time) · `audit/brief.md` (register, R07.31 tree) · `agentkthx/skills/codebase-audit/SKILL.md` (re-audit workflow when scaffolding lands)

---

Written by VTSTech — https://www.vts-tech.org
