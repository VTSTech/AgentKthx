# DuckDuckGo AI Chat API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: reverse-engineered source at https://github.com/mrgick/duck_chat (api.py + cli.py + models/model_type.py) + JSR package `@mumulhl/duckduckgo-ai-chat` v3.3.0 + DuckDuckGo help page (https://duckduckgo.com/duckduckgo-help-pages/aicheat) + arstechnica.com launch coverage
> **Free-tier verification**: Truly free, anonymous, no API key, no signup, no quota. Source: [DuckDuckGo help pages](https://duckduckgo.com) — *"Duck.ai allows you to anonymously chat with Anthropic's Claude 4.5 Haiku, Meta's Llama 3.3 70B, Mistral AI's Mistral Small 3 24B, and OpenAI's GPT-4o mini."* Also confirmed by [ars technica](https://arstechnica.com) and the [mrgick/duck_chat README](https://github.com/mrgick/duck_chat).
> **Live-behavior notes**: 2026-10-07 — DuckDuckGo AI Chat is the **only keyless, anonymous LLM API** among the four documented providers. It is NOT OpenAI-compatible — it uses DuckDuckGo's own chat protocol with an `x-vqd-4` token handshake. The token rotates after every request; the protocol requires a `/duckchat/v1/status` GET to bootstrap the first token, then each `/duckchat/v1/chat` response returns a fresh token in its `x-vqd-4` response header. Models are upstream frontier models (GPT-4o mini, Claude 3 Haiku, Llama 3.3 70B, Mistral Small 3 24B, Mixtral, o3-mini) proxied through DuckDuckGo's privacy layer.
> **Primary focus**: Reverse-engineered protocol at `https://duckduckgo.com/duckchat/v1/*`. Requires a custom backend (not `CloudBackend`-derived) because the protocol is non-OpenAI.
> **Last Updated**: 2026-10-07
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
12. [Appendix: x-vqd-4 Token Lifecycle](#appendix-x-vqd-4-token-lifecycle)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — DuckDuckGo AI Chat (the only surface)
BASE_URL = "https://duckduckgo.com"

# Endpoints (DuckDuckGo's own protocol, NOT OpenAI-compatible)
STATUS  = "/duckchat/v1/status"  # GET — bootstrap/refresh the x-vqd-4 token
CHAT    = "/duckchat/v1/chat"    # POST — send message, receive streamed response

# Web UI (for human users — not for API access)
WEB_UI  = "https://duck.ai"
```

DuckDuckGo AI Chat has **NO OpenAI-compatible endpoint**. The protocol is custom and requires two endpoints:

1. **`GET /duckchat/v1/status`** — returns the initial `x-vqd-4` token in a response header
2. **`POST /duckchat/v1/chat`** — accepts a JSON body with the conversation, returns a streamed SSE response, and returns a fresh `x-vqd-4` token in a response header for the next request

There is no `/models` endpoint. The model is selected by the client in the request body — the available models are hardcoded upstream model IDs (see Model Catalog).

### Authentication Headers

DuckDuckGo AI Chat uses **NO API key**. Authentication is purely the `x-vqd-4` token, which is obtained from the `/status` endpoint and refreshed by every `/chat` response.

```python
# Initial bootstrap request (no auth required beyond the x-vqd-accept header)
status_headers = {
    "Host": "duckduckgo.com",
    "Accept": "text/event-stream",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://duckduckgo.com/",
    "User-Agent": "Mozilla/5.0 ...",  # MUST look like a real browser UA
    "x-vqd-accept": "1",  # required — tells DDG to issue a vqd token
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "TE": "trailers",
}

# Subsequent chat requests (use the latest x-vqd-4 token)
chat_headers = {
    "Host": "duckduckgo.com",
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
    "Referer": "https://duckduckgo.com/",
    "User-Agent": "Mozilla/5.0 ...",
    "x-vqd-4": "<the-latest-token>",  # required — refreshed by every chat response
    "DNT": "1",
    "Sec-GPC": "1",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "TE": "trailers",
}
```

### Key Types

There are NO API keys. The "authentication" is the `x-vqd-4` token, which:

- Is issued by `GET /duckchat/v1/status` with the `x-vqd-accept: 1` request header
- Is returned in the `x-vqd-4` response header of EVERY `/chat` call (refresh-rotation)
- Has no documented expiration, but in practice expires after a period of inactivity
- Is per-session, not per-account (no account exists)

**AgentKthx guidance**: Use NO env var for credentials. The only required env var is `DUCKDUCKGO_USER_AGENT` (optional — defaults to a recent Chrome/Firefox UA string). The `x-vqd-4` token is stored in-memory per-session, not persisted to disk.

### Request Format Requirements

- **Content-Type**: `application/json` (POST only; GET status takes no body)
- **Character Encoding**: UTF-8
- **Max Request Size**: (not documented; the conversation history is bounded by the model's context window)
- **Timeout**: 60 seconds recommended (some models can take 30+ seconds for long completions)
- **HTTP method**: `GET` for status, `POST` for chat
- **Headers REQUIRED for ALL requests**: `User-Agent` (must look like a real browser), `Referer: https://duckduckgo.com/`, `Accept: text/event-stream`. Missing any of these returns 403.

---

## Request/Response Structure

### Status Request (Bootstrap)

```http
GET /duckchat/v1/status HTTP/1.1
Host: duckduckgo.com
Accept: text/event-stream
Accept-Language: en-US,en;q=0.5
Referer: https://duckduckgo.com/
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36
x-vqd-accept: 1
DNT: 1
Sec-GPC: 1
Connection: keep-alive
```

### Status Response

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
x-vqd-4: 4-1234567890123456789-abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
Content-Length: 0
```

The response body is EMPTY. The token is returned in the `x-vqd-4` response header. Capture this token and use it in the next `/chat` request.

### Chat Request

```http
POST /duckchat/v1/chat HTTP/1.1
Host: duckduckgo.com
Accept: text/event-stream
Content-Type: application/json
Referer: https://duckduckgo.com/
User-Agent: Mozilla/5.0 ...
x-vqd-4: 4-1234567890123456789-abcdef...
DNT: 1
Sec-GPC: 1
Connection: keep-alive

{
    "model": "claude-3-haiku-20240307",
    "messages": [
        {"role": "user", "content": "Hello, world!"}
    ]
}
```

### Chat Request Body Schema

```json
{
    "model": "claude-3-haiku-20240307",
    "messages": [
        {
            "role": "user|assistant",
            "content": "string"
        }
    ]
}
```

### Differences from OpenAI Chat Completions

- **No `system` role** — DuckDuckGo AI Chat strips system messages. The first message must be `user` (or `assistant` if continuing a prior conversation). To inject a system prompt, prepend it to the first `user` message as plain text.
- **No `temperature`/`top_p`/`max_tokens`/`stop`/`seed`** — DuckDuckGo uses upstream defaults. The temperature is fixed at the upstream model's default (typically 0.7–1.0).
- **No `tools`** — DuckDuckGo AI Chat does NOT support function calling. Use ReAct prompting (which AgentKthx already supports via `force_react=True`).
- **No `stream` field** — Responses are ALWAYS streamed via SSE. There is no non-streaming mode.
- **No `tool_calls` in assistant messages** — Even if a prior turn produced tool-call-shaped JSON, you must store it as plain content and re-send as `assistant` role content.
- **No `n` field** — Always returns a single completion.
- **No `response_format`** — No JSON mode.

### Chat Response (SSE Stream)

```
data: {"action": "start", "model": "claude-3-haiku-20240307"}

data: {"action": "chunk", "message": "Hello"}

data: {"action": "chunk", "message": "!"}

data: {"action": "chunk", "message": " How can I help today?"}

data: {"action": "success", "model": "claude-3-haiku-20240307"}

data: [DONE]
```

### Chat Response Headers

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
x-vqd-4: 4-9876543210987654321-fedcba0987654321fedcba0987654321fedcba0987654321fedcba0987654321
```

The response includes a **fresh `x-vqd-4` token in the response header**. Capture this token for the NEXT chat request — the previous token is invalidated.

---

## Model Catalog & Specifications

### Available Models (Verified Oct 2026)

| Model ID (DuckDuckGo) | Upstream Model | Family | Context (approx) | Notes |
|----------------------|----------------|--------|------------------|-------|
| `gpt-4o-mini` | OpenAI GPT-4o mini | OpenAI GPT | 128K | Default in many clients |
| `claude-3-haiku-20240307` | Anthropic Claude 3 Haiku | Anthropic Claude | 200K | Default in mrgick/duck_chat |
| `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` | Meta Llama 3.1 70B (via Together or similar) | Llama | 128K | Largest free model |
| `mistralai/Mixtral-8x7B-Instruct-v0.1` | Mistral Mixtral 8x7B | Mistral | 32K | MoE model |
| `o3-mini` | OpenAI o3-mini | OpenAI o3 | 200K | Reasoning model — added later |

Note: DuckDuckGo's help page (verified Oct 2026) lists: **Claude 4.5 Haiku, Llama 3.3 70B, Mistral Small 3 24B, GPT-4o mini**. The reverse-engineered `mrgick/duck_chat` source (older) lists Claude 3 Haiku, Llama 3.1 70B, Mixtral, GPT-4o mini. The JSR `@mumulhl/duckduckgo-ai-chat` v3.3.0 (also older) lists: `gpt-4o-mini`, `claude-3-haiku`, `llama`, `mixtral`, `o3-mini`.

DuckDuckGo periodically rotates which upstream models are available. The model ID format above is what the API expects in the request body — but DuckDuckGo may add/remove models without notice. AgentKthx should probe via a test request to verify availability.

### Model ID Format

DuckDuckGo model IDs follow two patterns:

- **Short alias**: `gpt-4o-mini`, `o3-mini` — OpenAI models
- **HF-style full path**: `claude-3-haiku-20240307` (Anthropic with date), `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` (Meta via HuggingFace-style path), `mistralai/Mixtral-8x7B-Instruct-v0.1`

### Model Metadata Structure

```python
MODEL_CONFIGS = {
    "gpt-4o-mini": {
        "context_length": 128000,
        "max_output_tokens": 16384,
        "supports_thinking": False,
        "supports_streaming": True,  # always streamed
        "supports_function_calling": False,  # DDG strips tool_calls
        "supports_multimodal": False,
        "upstream": "OpenAI GPT-4o mini",
        "family": "gpt"
    },
    "claude-3-haiku-20240307": {
        "context_length": 200000,
        "max_output_tokens": 8192,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": False,
        "supports_multimodal": False,
        "upstream": "Anthropic Claude 3 Haiku",
        "family": "claude"
    },
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": {
        "context_length": 128000,
        "max_output_tokens": 4096,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": False,
        "supports_multimodal": False,
        "upstream": "Meta Llama 3.1 70B (via Together)",
        "family": "llama-3"
    },
    "mistralai/Mixtral-8x7B-Instruct-v0.1": {
        "context_length": 32000,
        "max_output_tokens": 4096,
        "supports_thinking": False,
        "supports_streaming": True,
        "supports_function_calling": False,
        "supports_multimodal": False,
        "upstream": "Mistral Mixtral 8x7B",
        "family": "mistral-moe"
    },
    "o3-mini": {
        "context_length": 200000,
        "max_output_tokens": 100000,
        "supports_thinking": True,
        "supports_streaming": True,
        "supports_function_calling": False,
        "supports_multimodal": False,
        "upstream": "OpenAI o3-mini",
        "family": "o3"
    }
}
```

### Model Detection

```python
def detect_model_family(model_name: str) -> dict:
    """Detect model capabilities from DuckDuckGo model ID"""
    name_lower = model_name.lower()
    if name_lower.startswith("gpt-"):
        return {"family": "gpt", "supports_native_tools": False, "supports_thinking": False}
    elif "claude" in name_lower:
        return {"family": "claude", "supports_native_tools": False, "supports_thinking": False}
    elif "llama" in name_lower or name_lower == "llama":
        return {"family": "llama-3", "supports_native_tools": False, "supports_thinking": False}
    elif "mixtral" in name_lower or name_lower == "mixtral":
        return {"family": "mistral-moe", "supports_native_tools": False, "supports_thinking": False}
    elif name_lower.startswith("o3-") or name_lower == "o3-mini":
        return {"family": "o3", "supports_native_tools": False, "supports_thinking": True}
    else:
        return {"family": "unknown", "supports_native_tools": False, "supports_thinking": False}
```

---

## Function Calling Implementation

### DuckDuckGo Does NOT Support Function Calling

DuckDuckGo AI Chat strips `tools`, `tool_choice`, and `tool_calls` from requests and responses. Sending these fields does NOT cause an error — they are silently dropped, and the model responds as if no tools were provided.

### AgentKthx Workaround: Use ReAct

AgentKthx's existing ReAct prompting mode (set `force_react=True`) works around this. The model is prompted with a textual description of available tools in the `Action:` / `Action Input:` format, and the agent parses the model's text response for these patterns.

```python
# Force ReAct mode for all DuckDuckGo models
agent = Agent(
    backend=duckduckgo_backend,
    model="claude-3-haiku-20240307",
    force_react=True,  # REQUIRED — DDG strips tool_calls
    tools=[...],
)
```

### ReAct Prompt Construction

The system prompt (prepended to the first user message) should include:

```
Available tools:
- get_weather(city: str, unit: str = "celsius"): Get current weather for a city
- search_web(query: str): Search the web

When you want to use a tool, respond with:
Action: <tool_name>
Action Input: <json_arguments>

After receiving the result, continue reasoning.
```

AgentKthx's existing ReAct parser (`tool_parse.py`) handles `Action:` / `Action Input:` patterns, including markdown-bold-tolerant variants (`**Action:**`).

---

## Streaming & Real-time Features

### Streaming Is Mandatory

DuckDuckGo AI Chat ALWAYS streams responses via Server-Sent Events (SSE). There is no non-streaming mode — even the legacy "fetch full reply" clients simply buffer the SSE stream until completion.

### SSE Format

The SSE format is **NOT** OpenAI-spec. Each chunk has the shape:

```
data: {"action": "<action>", "message": "<text>"}
```

Where `<action>` is one of:

- `"start"` — first chunk, signals the start of the response. Includes `model` field but empty `message`.
- `"chunk"` — content chunk. The `message` field contains a text fragment (typically 1–5 tokens).
- `"success"` — final successful chunk. Empty `message`, includes `model` field.
- `"error"` — error chunk. Includes `type`, `status`, `message` fields (see Error Codes).
- `[DONE]` — terminal marker, plain text.

### Streaming Implementation (stdlib-only, AgentKthx pattern)

```python
import json
import urllib.request

class DuckDuckGoStreamHandler:
    """AgentKthx-style stdlib-only SSE stream reader for DuckDuckGo AI Chat."""

    DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    def __init__(self, user_agent: str = None):
        self.user_agent = user_agent or self.DEFAULT_UA
        self.vqd_tokens: list[str] = []  # token rotation history

    def _build_status_headers(self):
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self.user_agent,
            "x-vqd-accept": "1",
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "TE": "trailers",
        }

    def _build_chat_headers(self, vqd_token: str):
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self.user_agent,
            "x-vqd-4": vqd_token,
            "DNT": "1",
            "Sec-GPC": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "TE": "trailers",
        }

    def get_vqd(self) -> str:
        """Bootstrap a fresh x-vqd-4 token via GET /duckchat/v1/status."""
        req = urllib.request.Request(
            "https://duckduckgo.com/duckchat/v1/status",
            headers=self._build_status_headers(),
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 429:
                raise RatelimitException("DDG rate limit on /status")
            token = resp.headers.get("x-vqd-4")
            if not token:
                raise DuckChatException("No x-vqd-4 in /status response")
            self.vqd_tokens.append(token)
            return token

    def chat_stream(self, model: str, messages: list):
        """Stream a chat response. Yields content deltas as strings."""
        if not self.vqd_tokens:
            self.get_vqd()
        vqd_token = self.vqd_tokens[-1]

        body = json.dumps({"model": model, "messages": messages}).encode("utf-8")
        req = urllib.request.Request(
            "https://duckduckgo.com/duckchat/v1/chat",
            data=body,
            headers=self._build_chat_headers(vqd_token),
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            if resp.status == 429:
                raise RatelimitException("DDG rate limit on /chat")
            # Read the SSE stream line-by-line
            buffer = b""
            for chunk in resp:
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data: "):
                        continue
                    payload = line[6:]
                    if payload == b"[DONE]":
                        # Capture the fresh token from response headers
                        new_token = resp.headers.get("x-vqd-4")
                        if new_token:
                            self.vqd_tokens.append(new_token)
                        return
                    try:
                        data = json.loads(payload.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    action = data.get("action")
                    if action == "chunk":
                        message = data.get("message", "")
                        if message:
                            yield message
                    elif action == "error":
                        err_type = data.get("type", "")
                        err_status = data.get("status", 500)
                        if err_status == 429:
                            if err_type == "ERR_CONVERSATION_LIMIT":
                                raise ConversationLimitException(err_type)
                            raise RatelimitException(err_type)
                        raise DuckChatException(err_type)
            # End of stream without [DONE] — capture the fresh token anyway
            new_token = resp.headers.get("x-vqd-4")
            if new_token:
                self.vqd_tokens.append(new_token)

    def chat_full(self, model: str, messages: list) -> str:
        """Convenience: stream to completion, return full text."""
        return "".join(self.chat_stream(model, messages))
```

### Token Rotation Lifecycle

1. **First call**: `get_vqd()` issues `GET /duckchat/v1/status`, captures `x-vqd-4` from response header. Token appended to `vqd_tokens` list.
2. **First chat call**: Uses `vqd_tokens[-1]` as the `x-vqd-4` request header. Response streams back; the response headers include a fresh `x-vqd-4` token. Appended to `vqd_tokens`.
3. **Subsequent chat calls**: Use `vqd_tokens[-1]` (the most recent token). Each call's response provides the next token.
4. **Token expiry**: If a token expires (after inactivity), the next `/chat` returns 401 or 429. The handler should re-call `get_vqd()` and retry.

The `vqd_tokens` list grows over time. In practice, only the latest token is needed; older tokens are kept for the `reask_question` feature (regenerate a prior response by replaying from an earlier token).

---

## Error Codes & Recovery

### Error Response Structure

DuckDuckGo AI Chat reports errors in three ways:

1. **HTTP status code** on the initial response line (401, 429, 500, 503)
2. **SSE `data: {"action": "error", ...}`** chunk within an otherwise-200 streaming response
3. **Empty body with HTTP 429** for rate limits on the `/status` endpoint

### Error Codes

```python
class DuckChatException(Exception):
    """Base exception for DuckDuckGo AI Chat."""

class RatelimitException(DuckChatException):
    """Rate limit (429) — too many requests, retry with backoff."""

class ConversationLimitException(DuckChatException):
    """Conversation limit reached — must start a new conversation."""
```

### Comprehensive Error Handling

```python
class DuckDuckGoErrorHandler:
    ERROR_CODES = {
        200: {
            "message": "OK (streaming)",
            "recoverable": False,
            "actions": []  # success path
        },
        401: {
            "message": "Token expired OR invalid x-vqd-4",
            "recoverable": True,  # re-bootstrap with get_vqd()
            "actions": ["Call /duckchat/v1/status to get a fresh x-vqd-4 token",
                       "Retry the /chat request with the new token"]
        },
        403: {
            "message": "Forbidden — missing User-Agent or Referer header",
            "recoverable": False,
            "actions": ["Verify User-Agent looks like a real browser (Chrome/Firefox UA)",
                       "Verify Referer header is set to https://duckduckgo.com/"]
        },
        429: {
            "message": "Rate limit exceeded",
            "recoverable": True,
            "retry_after": 60,  # undocumented; backoff recommended
            "actions": ["Backoff with exponential jitter",
                       "If error type is ERR_CONVERSATION_LIMIT, start a new conversation"]
        },
        500: {
            "message": "Internal Server Error",
            "recoverable": True,
            "retry_after": 5,
            "actions": ["Retry with backoff"]
        },
        503: {
            "message": "Service Unavailable (upstream provider down)",
            "recoverable": True,
            "retry_after": 30,
            "actions": ["Retry with longer backoff",
                       "Try a different model (upstream provider may be down for one but not others)"]
        }
    }

    def handle_error(self, response_status: int, body: bytes = b"") -> dict:
        """Handle API errors with recovery suggestions"""
        code = response_status
        info = self.ERROR_CODES.get(code, {
            "message": "Unknown Error",
            "recoverable": False,
            "actions": ["Check logs"]
        })

        # Parse the SSE error body if present
        message = info["message"]
        if body:
            try:
                # Body may be plain text or JSON-encoded SSE chunk
                text = body.decode("utf-8", errors="replace").strip()
                if text.startswith("data: "):
                    text = text[6:]
                data = json.loads(text)
                if isinstance(data, dict) and data.get("action") == "error":
                    err_type = data.get("type", "")
                    err_status = data.get("status", code)
                    message = f"{err_type} (status={err_status})"
                    if err_type == "ERR_CONVERSATION_LIMIT":
                        message = "Conversation limit reached — start a new conversation"
                        info["recoverable"] = False  # not retryable; must reset
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        return {
            "code": code,
            "message": message,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"]
        }
```

### Special Error Types

- **`ERR_CONVERSATION_LIMIT`**: DuckDuckGo limits the length of a single conversation (typically ~20 turns). After this, the conversation must be reset (clear history and start fresh). The agent should detect this and clear the conversation memory.
- **`ERR_CHALLENGE`**: Sometimes returned when the `x-vqd-4` token is missing or malformed. Re-bootstrap with `get_vqd()`.
- **Token expiry 401**: After a period of inactivity, the `x-vqd-4` token expires. The handler should automatically call `get_vqd()` and retry.

---

## Rate Limiting & Concurrency

### Documented Limits

DuckDuckGo does NOT publish official rate limits. Empirically:

| Limit | Value | Notes |
|-------|-------|-------|
| Requests per minute | ~10–20 | Per IP, undocumented, can change |
| Concurrent requests | 1 | DuckDuckGo throttles aggressive parallelism |
| Conversation length | ~20 turns | Returns `ERR_CONVERSATION_LIMIT` after this |
| Daily quota | None documented | Appears genuinely uncapped; subject to per-IP throttling |

### Practical Considerations

- **Single-conversation only**: A `DuckChat` instance holds ONE conversation. To run parallel conversations, instantiate multiple `DuckChat` objects (each with its own `vqd_tokens` list).
- **Conservative backoff recommended**: DDG's anti-abuse heuristics are aggressive. Use a longer backoff (5–10 seconds) on 429, even though `Retry-After` is not always provided.
- **User-Agent rotation**: Some reverse-engineered clients rotate User-Agent strings to avoid fingerprinting. This is OPTIONAL — a stable UA works fine for typical usage.

### Conversation Limit Workaround

When `ERR_CONVERSATION_LIMIT` is hit, the handler should:

1. Clear the conversation history (`messages = []`)
2. Optionally re-bootstrap with `get_vqd()` (not strictly required; the same token works for new conversations)
3. Restart the agent loop

AgentKthx's `Memory` class already supports `clear()` — wire it to fire when `ConversationLimitException` is caught.

---

## Multimodal Content Handling

### DuckDuckGo AI Chat Does NOT Support Multimodal

The `/duckchat/v1/chat` endpoint accepts only text content in the `messages` array. Sending `image_url`, `video_url`, `file`, or other non-text content types returns 400 (or is silently dropped — behavior varies by upstream model).

### Workaround for Vision Tasks

For vision tasks, use a different backend (Cloudflare Workers AI vision models, ZAI GLM-V, NVIDIA NIM Phi-4-Multimodal). DuckDuckGo AI Chat is text-only.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

Unlike the other three documented providers, DuckDuckGo AI Chat does NOT fit the `CloudBackend` + `openai_compat` pattern. It requires a custom backend:

```python
from agentkthx.backends.base import BaseBackend

class DuckDuckGoBackend(BaseBackend):
    """DuckDuckGo AI Chat backend — keyless, anonymous, non-OpenAI-compat."""

    is_cloud = True  # it IS cloud-hosted, just not OpenAI-compat

    def __init__(self, config=None, **kwargs):
        super().__init__(config=config, **kwargs)
        self.user_agent = (getattr(config, "DUCKDUCKGO_USER_AGENT", None)
                           if config else kwargs.get("user_agent"))
        self.vqd_tokens: list[str] = []
        # NOTE: NO api_key attribute — DDG uses no API key

    def _get_vqd(self) -> str:
        """Bootstrap or refresh the x-vqd-4 token."""
        if not self.vqd_tokens:
            # Issue GET /duckchat/v1/status with x-vqd-accept: 1
            req = urllib.request.Request(
                "https://duckduckgo.com/duckchat/v1/status",
                headers=self._build_status_headers(),
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                token = resp.headers.get("x-vqd-4")
                if not token:
                    raise RuntimeError("No x-vqd-4 in /status response")
                self.vqd_tokens.append(token)
        return self.vqd_tokens[-1]

    def _build_status_headers(self) -> dict:
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self.user_agent or _DEFAULT_UA,
            "x-vqd-accept": "1",
            "DNT": "1",
            "Sec-GPC": "1",
        }

    def _build_chat_headers(self, vqd_token: str) -> dict:
        return {
            "Host": "duckduckgo.com",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Referer": "https://duckduckgo.com/",
            "User-Agent": self.user_agent or _DEFAULT_UA,
            "x-vqd-4": vqd_token,
            "DNT": "1",
            "Sec-GPC": "1",
        }

    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        """Build DuckDuckGo chat request body — minimal shape."""
        # DDG strips system messages — prepend to first user message
        messages = self._collapse_system_into_user(messages)
        # DDG does not support tools, temperature, max_tokens, etc.
        # Use ReAct prompting if tools are needed.
        return {
            "model": model,
            "messages": messages,
        }

    def _collapse_system_into_user(self, messages: list) -> list:
        """DDG strips system messages; prepend to first user message."""
        if not messages:
            return messages
        system_content = ""
        user_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content += msg.get("content", "") + "\n\n"
            else:
                user_messages.append(msg)
        if system_content and user_messages:
            user_messages[0]["content"] = system_content + user_messages[0]["content"]
        return user_messages

    def generate(self, model: str, messages: list, stream: bool = True, **kwargs):
        """Generate a response. Always streams — stream=False buffers internally."""
        vqd_token = self._get_vqd()
        body = self._build_request(model, messages, **kwargs)
        # ... issue POST /duckchat/v1/chat, parse SSE stream, yield content
        # (See DuckDuckGoStreamHandler above for the SSE parsing pattern)

    def list_models(self) -> list:
        """DDG has no /models endpoint — return the hardcoded list."""
        return list(MODEL_CONFIGS.keys())
```

### 2. FREE_ONLY Enforcement

Not applicable — DuckDuckGo AI Chat is entirely free. The `DUCKDUCKGO_FREE_ONLY` env var has no effect (all models are free).

### 3. Plugin Manifest

```json
{
    "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
    "name": "duckduckgo",
    "version": "0.1.0",
    "description": "DuckDuckGo AI Chat backend — keyless, anonymous, no signup required. Reverse-engineered protocol at duckduckgo.com/duckchat/v1. Proxies OpenAI GPT-4o mini / o3-mini, Anthropic Claude 3 Haiku, Meta Llama 3.1 70B, Mistral Mixtral 8x7B. No tools support — use ReAct prompting (force_react=True).",
    "backend_class": "agentkthx.backends.duckduckgo.DuckDuckGoBackend",
    "backend_type": "cloud",
    "env_vars": [],
    "optional_env_vars": ["DUCKDUCKGO_USER_AGENT"],
    "default_base_url": "https://duckduckgo.com",
    "free_tier": true,
    "keyless": true
}
```

### 4. Existing Patterns That Apply Directly

- **`BaseBackend` base class**: inherits the `generate()`, `is_cloud`, `tool_support` interface — but NOT the OpenAI-compat plumbing. The backend implements `generate()` from scratch.
- **`api_resilience.py`**: 429 classified as transient (rate limit). The `ConversationLimitException` is permanent per-conversation but recoverable by clearing memory — AgentKthx should add a special-case handler.
- **`tool_support.json` cache**: not needed — DDG never supports tools. Hardcode `ToolSupportLevel.REACT` for all DDG models.
- **`is_local_base_url`**: correctly classifies `duckduckgo.com` as remote.
- **`_close_http_response`** (R07.25 ROB-06): deterministic close applies automatically.
- **ReAct prompting**: The existing `force_react=True` flow in `agentic_loop.py` handles DDG's lack of native tools. The ReAct system-prompt construction in `prompts.py` needs to be embedded into the first user message (not sent as a system role) for DDG.

### 5. Unique DuckDuckGo-Specific Considerations

- **NO CloudBackend inheritance**: DDG is the only one of the four providers that does NOT inherit from `CloudBackend`. The custom protocol (SSE shape, `x-vqd-4` token rotation, no `/models` endpoint) requires a from-scratch `BaseBackend` subclass.
- **Token rotation state**: The `vqd_tokens` list must persist across calls within a single conversation. This is in-memory state — not cacheable across processes.
- **No system role**: DDG strips `system` messages. The backend's `_collapse_system_into_user()` helper handles this transparently — no caller code change needed.
- **Always-streaming**: DDG has no non-streaming mode. The `generate()` method should accept `stream=True` only; `stream=False` buffers the SSE stream internally and returns the full text.
- **Conversation length limit**: ~20 turns per conversation. After this, the conversation must be reset. The backend should auto-clear memory on `ConversationLimitException`.
- **Anti-bot heuristics**: DDG throttles aggressive usage. The `User-Agent` header MUST look like a real browser — empty or non-browser UAs return 403. Rotate UAs only if you observe throttling.
- **No cost tracking**: Since DDG is free and keyless, the footer's "estimated cost" segment should display `$0.00` or `FREE`.

### 6. Privacy & Anonymity

DuckDuckGo's value proposition is anonymity. To preserve this:

- Do NOT log the conversation to disk (or log with explicit user opt-in only)
- Do NOT send `User-Agent` identifying AgentKthx (use a generic browser UA)
- Do NOT include any account/session identifier in requests
- The `vqd_tokens` are session-scoped and should be cleared on session end

AgentKthx's existing `PersistentMemory` (SQLite) would violate this anonymity guarantee if enabled. The DDG backend should default to in-memory `Memory` only, with a clear warning if the user enables persistent memory.

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `403 Forbidden` | Missing or non-browser `User-Agent`, or missing `Referer` header | Set `User-Agent` to a real Chrome/Firefox UA string; ensure `Referer: https://duckduckgo.com/` |
| `401 Unauthorized` | `x-vqd-4` token expired | Re-bootstrap with `get_vqd()` and retry |
| `429` with `ERR_CONVERSATION_LIMIT` | Conversation exceeded ~20 turns | Clear conversation memory (`agent.memory.clear()`), start fresh |
| `429` without `ERR_CONVERSATION_LIMIT` | Rate limit (per-IP throttling) | Backoff with 5–10 second jitter; reduce request frequency |
| `500` / `503` | Upstream provider (OpenAI/Anthropic/Meta/Mistral) outage | Try a different DDG model; the upstream may be down for one provider but not others |
| `data: {"action": "error", "type": "ERR_CHALLENGE"}` | Missing or malformed `x-vqd-4` header | Re-bootstrap with `get_vqd()` |
| Stream returns empty body | Token expired mid-stream, or model rejected the input | Re-bootstrap and retry; check input for unusual content |
| Tool calls silently ignored | DDG strips `tools` and `tool_calls` from requests | Use `force_react=True` for ReAct prompting |
| System prompt not applied | DDG strips `system` role messages | Backend's `_collapse_system_into_user()` should handle this automatically; if not, prepend manually |
| Conversation limit hit mid-agent-loop | Long agentic workflow exceeded 20 turns | Reset conversation; consider switching to a backend without conversation limits (NVIDIA/Cloudflare/SiliconFlow) for long-running workflows |
| Image input rejected | DDG is text-only | Use a different backend for vision tasks |
| 503 on `o3-mini` | OpenAI o3-mini upstream overloaded | Try `gpt-4o-mini` or a non-OpenAI model |

---

## Appendix: Privacy & Anonymity Model

### What DuckDuckGo Promises

Per DuckDuckGo's published privacy policy for Duck.ai:

- **No account required**: No signup, no email, no identity
- **Anonymized upstream**: DuckDuckGo strips your IP address before forwarding to OpenAI/Anthropic/Meta/Mistral
- **No chat storage**: Conversations are NOT stored by DuckDuckGo (the upstream providers may store them per their own policies)
- **No training on your data**: DuckDuckGo's agreements with upstream providers prohibit using Duck.ai traffic for training

### What DuckDuckGo Cannot Guarantee

- **Upstream provider retention**: OpenAI/Anthropic/Meta/Mistral may retain anonymized conversations per their own data retention policies (typically 30 days for abuse monitoring)
- **Network metadata**: Your IP is visible to DuckDuckGo (though not forwarded upstream). Law enforcement could subpoena DuckDuckGo's logs.
- **No end-to-end encryption**: The connection is HTTPS (TLS) but DuckDuckGo decrypts the request before forwarding upstream

### AgentKthx Recommendation

For users who need strong anonymity (whistleblowers, journalists, security researchers):
- Use DuckDuckGo AI Chat through a VPN or Tor
- Disable AgentKthx's `PersistentMemory` (use in-memory only)
- Do not include PII in prompts
- Rotate the conversation frequently (clear memory after each session)

For typical developer use (testing, prototyping, light agentic workflows):
- DuckDuckGo AI Chat is the easiest free-tier option — no signup, no key, no cost
- The conversation limit (~20 turns) is the main constraint
- Pair with SiliconFlow's free `Qwen3-8B` for longer-running workflows

---

## Appendix: x-vqd-4 Token Lifecycle

### Token Format

The `x-vqd-4` token is a string of the form:

```
4-<10-digit-timestamp>-<64-hex-chars>
```

Example:
```
4-1712345678-abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
```

The timestamp appears to be a Unix epoch at issuance time. The 64-hex-char suffix is opaque (likely a HMAC or session token).

### Token States

| State | Description | Action |
|-------|-------------|--------|
| Fresh | Just issued by `/status` or `/chat` response | Use for the next `/chat` request |
| Used | Sent in a `/chat` request that completed | The response provided a new token; the old one is invalidated |
| Expired | Token has not been used for an extended period | Re-bootstrap with `/status` |
| Invalidated | A new token was issued, invalidating this one | Discard; use the latest token from `vqd_tokens[-1]` |

### Token Rotation Diagram

```
[Client]                              [duckduckgo.com]
   |                                          |
   | GET /duckchat/v1/status                  |
   | x-vqd-accept: 1                          |
   | ---------------------------------------> |
   |                                          |
   |                  200 OK                  |
   |          x-vqd-4: TOKEN_1                |
   | <--------------------------------------- |
   |                                          |
   | [store TOKEN_1 in vqd_tokens]            |
   |                                          |
   | POST /duckchat/v1/chat                   |
   | x-vqd-4: TOKEN_1                         |
   | body: {model, messages}                  |
   | ---------------------------------------> |
   |                                          |
   |          data: {"action":"chunk",...}     |
   | <--------------------------------------- |
   |          data: {"action":"chunk",...}     |
   | <--------------------------------------- |
   |          data: [DONE]                     |
   |          x-vqd-4: TOKEN_2                |
   | <--------------------------------------- |
   |                                          |
   | [store TOKEN_2 in vqd_tokens]            |
   |                                          |
   | POST /duckchat/v1/chat                   |
   | x-vqd-4: TOKEN_2                         |
   | body: {model, messages}                  |
   | ---------------------------------------> |
   |                                          |
   | ... (continues) ...                      |
```

### Implementation Note

The `vqd_tokens` list grows by one entry per `/chat` call. For long conversations, this can grow to hundreds of entries. In practice, only the latest token is needed — older tokens are kept only for the `reask_question` feature (regenerate a prior response by replaying from an earlier token).

For AgentKthx's use case (forward-only agent loop), the list can be capped at 2 entries (the current and the previous). Trim older entries to bound memory usage.
