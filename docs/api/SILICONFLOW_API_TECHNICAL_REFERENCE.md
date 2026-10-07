# SiliconFlow API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions.md + https://docs.siliconflow.com/en/userguide/guides/function-calling.md + https://docs.siliconflow.com/llms.txt (verified Oct 2026)
> **Free-tier verification**: 3 permanently-free models (Qwen3-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-OCR), no credit card required, no usage limits on the free tier. Source: [pricepertoken.com](https://pricepertoken.com) + [therouter.ai](https://therouter.ai) — *"Three models are completely free: Qwen3-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-OCR. No credit card required, no usage limits on the free tier."*
> **Live-behavior notes**: 2026-10-07 — SiliconFlow is a China-hosted OpenAI-compatible aggregator offering 200+ models. Free tier is the narrowest of the four documented providers (3 models, no quota) but the free models have NO daily/monthly cap — useful for high-volume agentic workloads on Qwen3-8B or DeepSeek-R1-Distill.
> **Primary focus**: OpenAI-compatible Chat Completions endpoint at `https://api.siliconflow.com/v1` (also accessible via the `.cn` TLD at `https://api.siliconflow.cn/v1` for China-domestic traffic).
> **Last Updated**: 2026-10-07
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
10. [Implementation Notes for AgentKthx](#implementation-notes-for-agentkthx)
11. [Troubleshooting Matrix](#troubleshooting-matrix)
12. [Appendix: Free-Model Catalog](#appendix-free-model-catalog)
13. [Appendix: OpenAI Wire-Format Deltas](#appendix-openai-wire-format-deltas)

---

## Authentication & Endpoint Details

### Base URLs

```python
# Production — global TLD (lower latency from outside China)
BASE_URL = "https://api.siliconflow.com/v1"

# Production — China-domestic TLD (lower latency from inside China)
BASE_URL_CN = "https://api.siliconflow.cn/v1"

# API endpoints (OpenAI-compatible)
CHAT_COMPLETIONS  = "/chat/completions"      # primary chat
COMPLETIONS       = "/completions"            # legacy single-turn
MODELS            = "/models"                  # list models hosted on this deployment
EMBEDDINGS        = "/embeddings"              # BGE family
RERANK            = "/rerank"                  # BGE reranker family
MESSAGES          = "/messages"               # Anthropic-compat (separate endpoint)

# Console (where you obtain API keys)
CONSOLE_URL       = "https://cloud.siliconflow.com/account/ak"
```

SiliconFlow exposes both an OpenAI-compatible `/chat/completions` AND an Anthropic-compatible `/messages` endpoint — the same backend, different wire shapes. AgentKthx targets the OpenAI-compat path to inherit the existing `openai_compat` plumbing.

### Authentication Headers

```python
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-XXXXXXXXXXXXXXXXXXXXXXXX",  # SiliconFlow API key
    "Accept": "application/json",
}
```

### Key Types

SiliconFlow issues a single key type per account, scoped by the account's available balance:

| Key type | Prefix | Where obtained | What it can do |
|----------|--------|----------------|----------------|
| Account API key | `sk-` | https://cloud.siliconflow.com/account/ak | Full account access — all models the account can access (free + paid). One key per account. |
| Sub-account key | `sk-` | Console → Sub-accounts | Same scope but tied to a sub-account; useful for team isolation |

**AgentKthx guidance**: Use a single `sk-` key read from `SILICONFLOW_API_KEY` env var. Free models (`Qwen/Qwen3-8B`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`, `deepseek-ai/DeepSeek-OCR`) require no payment setup; paid models require balance top-up.

### Request Format Requirements

- **Content-Type**: `application/json` only
- **Character Encoding**: UTF-8
- **Max Request Size**: 4MB (documented OpenAI-spec)
- **Timeout**: 120 seconds recommended (DeepSeek-R1 reasoning can take 60+ seconds)
- **HTTP method**: `POST` for inference, `GET` for `/models`

---

## Request/Response Structure

### Complete Request Schema

```json
{
    "model": "Qwen/Qwen3-32B",
    "messages": [
        {
            "role": "system|user|assistant|tool",
            "content": "string|array",
            "tool_calls": "array",
            "tool_call_id": "string",
            "reasoning_content": "string"
        }
    ],
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_tokens": 4096,
    "stream": false,
    "stop": ["</s>", "<|im-end|>"],
    "seed": 42,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "repetition_penalty": 1.0,
    "enable_thinking": true,
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "string",
                "description": "string",
                "parameters": {"type": "object", "properties": {}, "required": []}
            }
        }
    ],
    "tool_choice": "auto",
    "response_format": {"type": "text|json_object"},
    "n": 1,
    "user": "string"
}
```

### Response Schema

```json
{
    "id": "<string>",
    "object": "chat.completion",
    "created": 123,
    "model": "Qwen/Qwen3-32B",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Generated text response",
                "reasoning_content": "Chain of thought reasoning (R1/thinking models only)",
                "tool_calls": [
                    {
                        "id": "<string>",
                        "type": "function",
                        "function": {
                            "name": "<string>",
                            "arguments": "<string>"
                        }
                    }
                ]
            },
            "finish_reason": "stop|tool_calls|length|content_filter",
            "logprobs": null
        }
    ],
    "usage": {
        "prompt_tokens": 123,
        "completion_tokens": 123,
        "total_tokens": 123
    }
}
```

### Differences from vanilla OpenAI

- **`reasoning_content`** field in BOTH the response `message` and streaming `delta` — SiliconFlow surfaces chain-of-thought reasoning from DeepSeek-R1, Qwen3-Thinking, GLM-Z1 thinking models. This is the same field name OpenAI uses for o1/o3 reasoning traces.
- **`enable_thinking`** boolean (top-level request field) — switches between thinking and non-thinking modes on hybrid models like Qwen3. Default `true` for thinking-capable models.
- **`repetition_penalty`** is supported as a top-level parameter (SiliconFlow passes vLLM-style sampling params through)
- **`top_k`** is supported
- **`response_format: json_schema`** — supported on select models (DeepSeek-V3.1, Qwen3-Coder) with structured output enforcement
- **`tool_calls[].function.arguments`** is a STRING (JSON-encoded), matching OpenAI spec — not a parsed dict

---

## Model Family Specifications

### Model ID Format

SiliconFlow model IDs follow the `<author>/<model-name>` convention (mirrors HuggingFace naming):

```
Qwen/Qwen3-32B
Qwen/Qwen3-Coder-480B-A35B-Instruct
deepseek-ai/DeepSeek-R1
deepseek-ai/DeepSeek-V3.2
THUDM/GLM-4-32B-0414
zai-org/GLM-5
meta-llama/Meta-Llama-3.1-8B-Instruct
moonshotai/Kimi-K2-Instruct
MiniMaxAI/MiniMax-M2.5
```

### Verified Catalog (from /chat/completions schema enum, Oct 2026)

The full enum from the live OpenAPI spec includes (truncated for brevity — full list in source):

```python
CATALOG_ENUM = [
    # DeepSeek
    "deepseek-ai/DeepSeek-R1",
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V3.1-Terminus",
    "deepseek-ai/DeepSeek-V3.2-Exp",
    "deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/deepseek-vl2",
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V4-Pro",
    "nex-agi/DeepSeek-V3.1-Nex-N1",

    # Baidu
    "baidu/ERNIE-4.5-300B-A47B",

    # GLM (THUDM = Tsinghua)
    "THUDM/GLM-4-32B-0414",
    "THUDM/GLM-4-9B-0414",
    "THUDM/GLM-Z1-32B-0414",
    "THUDM/GLM-Z1-9B-0414",

    # ZAI (Zhipu)
    "zai-org/GLM-4.5",
    "zai-org/GLM-4.5-Air",
    "zai-org/GLM-4.5V",
    "zai-org/GLM-5",
    "zai-org/GLM-5.1",
    "zai-org/GLM-4.7",
    "zai-org/GLM-4.6",
    "zai-org/GLM-4.6V",
    "zai-org/GLM-5V-Turbo",

    # Tencent
    "tencent/Hunyuan-A13B-Instruct",
    "tencent/Hunyuan-MT-7B",
    "tencent/Hy3-preview",

    # Moonshot
    "moonshotai/Kimi-K2.5",
    "moonshotai/Kimi-K2.6",
    "moonshotai/Kimi-K2-Instruct",
    "moonshotai/Kimi-K2-Instruct-0905",
    "moonshotai/Kimi-K2-Thinking",

    # Inclusion AI
    "inclusionAI/Ling-flash-2.0",
    "inclusionAI/Ling-mini-2.0",
    "inclusionAI/Ring-flash-2.0",

    # Meta
    "meta-llama/Meta-Llama-3.1-8B-Instruct",

    # MiniMax
    "MiniMaxAI/MiniMax-M2.5",
    "MiniMaxAI/MiniMax-M2.1",

    # Qwen (large set)
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-14B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-72B-Instruct-128K",
    "Qwen/Qwen2.5-VL-7B-Instruct",
    "Qwen/Qwen3-8B",                    # FREE TIER
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-235B-A22B",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "Qwen/Qwen3-235B-A22B-Thinking-2507",
    "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "Qwen/Qwen3-30B-A3B-Thinking-2507",
    "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct",
    "Qwen/Qwen3-Next-80B-A3B-Instruct",
    "Qwen/Qwen3-Next-80B-A3B-Thinking",
    "Qwen/Qwen3-Omni-30B-A3B-Captioner",
    "Qwen/Qwen3-Omni-30B-A3B-Instruct",
    "Qwen/Qwen3-Omni-30B-A3B-Thinking",
    "Qwen/Qwen3.5-9B",
    "Qwen/Qwen3.5-27B",
    "Qwen/Qwen3.5-35B-A3B",
    "Qwen/Qwen3.5-122B-A10B",
    "Qwen/Qwen3.5-397B-A17B",
    "Qwen/Qwen3.6-27B",
    "Qwen/Qwen3.6-35B-A3B",

    # Bytedance
    "ByteDance-Seed/Seed-OSS-36B-Instruct",

    # Google
    "google/gemma-4-26B-A4B-it",
    "google/gemma-4-31B-it",

    # OpenAI (open weights only)
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]
```

### Free Tier Models (verified Oct 2026)

| Model ID | Family | Context | Notes |
|----------|--------|---------|-------|
| `Qwen/Qwen3-8B` | Qwen3 | 32K | General chat + light reasoning. No usage limits. |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | DeepSeek-R1 distill | 32K | Reasoning model (distilled). No tools support. |
| `deepseek-ai/DeepSeek-OCR` | DeepSeek OCR | 4K | OCR model (image → text), not a chat model. Listed for completeness. |

The free catalog is small but the two chat models cover both the "general chat" and "reasoning" use cases. **No daily or monthly quota** on these free models — useful for sustained agentic workloads.

### Model Detection & Auto-configuration

```python
def detect_model_family(model_name: str) -> dict:
    """Detect model capabilities from SiliconFlow model ID"""
    name_lower = model_name.lower()
    if name_lower.startswith("qwen/qwen3"):
        thinking = "thinking" in name_lower or "-thinking-" in name_lower
        if "coder" in name_lower:
            return {"family": "qwen-coder",
                    "supports_native_tools": True,
                    "supports_thinking": thinking}
        if "vl" in name_lower:
            return {"family": "qwen-vl",
                    "supports_native_tools": True,
                    "supports_multimodal": True}
        if "omni" in name_lower:
            return {"family": "qwen-omni",
                    "supports_native_tools": True,
                    "supports_multimodal": True,
                    "supports_thinking": "thinking" in name_lower}
        return {"family": "qwen3",
                "supports_native_tools": True,
                "supports_thinking": thinking}
    elif name_lower.startswith("deepseek-ai/deepseek-r"):
        return {"family": "deepseek-r",
                "supports_native_tools": False,  # R1 family: no tools
                "supports_thinking": True,
                "reasoning_levels": []}
    elif name_lower.startswith("deepseek-ai/deepseek-v"):
        return {"family": "deepseek-v",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("deepseek-ai/deepseek-vl"):
        return {"family": "deepseek-vl",
                "supports_native_tools": False,
                "supports_multimodal": True}
    elif name_lower.startswith("zai-org/glm"):
        return {"family": "glm-zai",
                "supports_native_tools": True,
                "supports_thinking": "v" not in name_lower.split("/")[-1].lower() or name_lower.endswith("-turbo")}
    elif name_lower.startswith("thudm/glm-z"):
        return {"family": "glm-thinking",
                "supports_native_tools": True,
                "supports_thinking": True}
    elif name_lower.startswith("thudm/glm-4"):
        return {"family": "glm",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("meta-llama/"):
        return {"family": "llama",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif name_lower.startswith("moonshotai/kimi-k2-thinking"):
        return {"family": "kimi-thinking",
                "supports_native_tools": False,
                "supports_thinking": True}
    elif name_lower.startswith("moonshotai/"):
        return {"family": "kimi",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif "nemotron" in name_lower:
        return {"family": "nemotron",
                "supports_native_tools": True,
                "supports_thinking": False}
    elif "phi-" in name_lower or "phi3" in name_lower:
        return {"family": "phi",
                "supports_native_tools": True,
                "supports_multimodal": "multimodal" in name_lower}
    else:
        return {"family": "unknown",
                "supports_native_tools": False,
                "supports_thinking": False}
```

### Catalog Discovery

```bash
curl -s https://api.siliconflow.com/v1/models \
  -H "Authorization: Bearer $SILICONFLOW_API_KEY" \
  -H "Content-Type: application/json" | jq '.data[].id'
```

---

## Function Calling Implementation

### Tool Schema Requirements

SiliconFlow follows the OpenAI tool-call schema verbatim:

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
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"], "default": "celsius"}
            },
            "required": ["city"]
        }
    }
}
```

### Tool Support Caveats

- **Reasoning models** (DeepSeek-R1, R1-Distill, GLM-Z1, Kimi-K2-Thinking) do NOT support `tools` — sending `tools` returns 400. Use ReAct fallback.
- **Vision models** (`deepseek-ai/deepseek-vl2`, `Qwen/Qwen2.5-VL-7B-Instruct`) typically do NOT support `tools` — use ReAct.
- **Tool-call streaming** supported on Qwen3, GLM-4, Llama-3, and DeepSeek-V families. Standard OpenAI incremental `tool_calls[].function.arguments` chunking applies.

### Verified Function-Calling Models (from SiliconFlow docs)

SiliconFlow documents function-calling support on these specific models (Oct 2026):

- `deepseek-ai/DeepSeek-R1` (R1 — actually NO tools; doc is wrong)
- `deepseek-ai/DeepSeek-V3` (V3 — supports tools)
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` (distill — NO tools, R1 family)
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` (distill — NO tools)
- `deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B` (distill — NO tools)
- `Qwen/Qwen2.5-72B-Instruct`, `Qwen/Qwen2.5-32B-Instruct`, `Qwen/Qwen2.5-14B-Instruct`, `Qwen/Qwen2.5-7B-Instruct`
- `THUDM/GLM-Z1-32B-0414` (thinking + tools)
- `THUDM/GLM-4-32B-0414`
- `THUDM/GLM-4-9B-0414`

The list is continuously updated — refer to the live `/features/function_calling` page for the current set. AgentKthx's `test_tool_support` probe handles this dynamically per-model.

### Tool Flow Implementation

```python
class SiliconFlowToolHandler:
    """Tool handler for SiliconFlow — OpenAI-spec-compatible."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def convert_to_sf_tools(self, agent_tools: list) -> list:
        """Convert AgentKthx tools to SiliconFlow (OpenAI) format"""
        sf_tools = []
        for tool in agent_tools:
            if hasattr(tool, 'to_openai_schema'):
                sf_tools.append({
                    "type": "function",
                    "function": tool.to_openai_schema()
                })
            else:
                sf_tools.append(self._convert_custom_tool(tool))
        return sf_tools

    def handle_tool_calls(self, response: dict) -> list:
        """Extract tool calls from response — OpenAI-spec shape"""
        tool_calls = []
        if 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            if 'message' in choice and 'tool_calls' in choice['message']:
                for tool_call in choice['message']['tool_calls']:
                    tool_calls.append({
                        'id': tool_call['id'],
                        'type': tool_call['type'],
                        'function': {
                            'name': tool_call['function']['name'],
                            'arguments': json.loads(tool_call['function']['arguments'])
                        }
                    })
        return tool_calls

    def extract_reasoning(self, response: dict) -> str:
        """Extract chain-of-thought reasoning_content if present."""
        if 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            if 'message' in choice:
                return choice['message'].get('reasoning_content', "")
        return ""
```

---

## Streaming & Real-time Features

### Streaming Response Format

SiliconFlow uses standard OpenAI SSE format, with the `reasoning_content` field added for thinking models:

```
data: {"id":"abc","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-ai/DeepSeek-R1","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"Let me think..."},"finish_reason":null}]}

data: {"id":"abc","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-ai/DeepSeek-R1","choices":[{"index":0,"delta":{"reasoning_content":"The answer is 42 because..."},"finish_reason":null}]}

data: {"id":"abc","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-ai/DeepSeek-R1","choices":[{"index":0,"delta":{"content":"The answer is 42."},"finish_reason":null}]}

data: {"id":"abc","object":"chat.completion.chunk","created":1700000000,"model":"deepseek-ai/DeepSeek-R1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### Streaming Implementation (stdlib-only, AgentKthx pattern)

```python
import json
import urllib.request

class SiliconFlowStreamHandler:
    """AgentKthx-style stdlib-only SSE stream reader for SiliconFlow."""

    def __init__(self, api_key: str, base_url: str = "https://api.siliconflow.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def create_streaming_request(self, messages, **kwargs):
        """Build streaming request with proper headers"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream",
        }
        body = {
            "model": kwargs.get("model", "Qwen/Qwen3-32B"),
            "messages": messages,
            "stream": True,
            "temperature": kwargs.get("temperature", 0.7),
            "top_p": kwargs.get("top_p", 0.95),
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        if "enable_thinking" in kwargs:
            body["enable_thinking"] = kwargs["enable_thinking"]
        if "tools" in kwargs:
            body["tools"] = kwargs["tools"]
            body["tool_choice"] = kwargs.get("tool_choice", "auto")
        if "stop" in kwargs:
            body["stop"] = kwargs["stop"]
        if "seed" in kwargs:
            body["seed"] = kwargs["seed"]
        if "repetition_penalty" in kwargs:
            body["repetition_penalty"] = kwargs["repetition_penalty"]

        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers=headers,
            method="POST"
        )
        return urllib.request.urlopen(req, timeout=180)

    def iter_stream_chunks(self, response):
        """Yield (content_delta, reasoning_delta, tool_call_delta, finish_reason) tuples.

        Note the 4-tuple — reasoning_content is a separate stream that
        precedes the content stream on thinking models.
        """
        buffer = b""
        for chunk in response:
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data: "):
                    continue
                payload = line[6:]
                if payload == b"[DONE]":
                    return
                try:
                    data = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                if not data.get("choices"):
                    continue
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                yield (
                    delta.get("content", ""),
                    delta.get("reasoning_content", ""),
                    delta.get("tool_calls", []),
                    choice.get("finish_reason"),
                )
```

### Streaming Reasoning Content

The `reasoning_content` field streams BEFORE the `content` field on thinking models. The stream sequence is:

1. Multiple chunks with only `reasoning_content` populated
2. A chunk with empty `reasoning_content` and the first `content` token
3. Multiple chunks with only `content` populated
4. Final chunk with `finish_reason: "stop"`
5. `data: [DONE]`

AgentKthx's `StreamAccumulator` should be extended to surface `reasoning_content` separately from `content` — display in the footer (like the existing `think ✓ yes` indicator) but not in the main agent transcript.

---

## Error Codes & Recovery

### Error Response Structure

SiliconFlow's documented error responses (verified from the live OpenAPI spec):

```python
ERROR_RESPONSES = {
    400: {
        "code": 20012,
        "message": "<string>",
        "data": "<string>"
    },
    401: '"Invalid token"',  # plain string, not JSON
    404: '"404 page not found"',  # plain string
    429: {
        "message": "Request was rejected due to rate limiting. If you want more, please contact contact@siliconflow.com. Details:TPM limit reached.",
        "data": "<string>"
    },
    503: {
        "code": 50505,
        "message": "Model service overloaded. Please try again later.",
        "data": "<string>"
    },
    504: "<string>"  # plain string
}
```

### Important: Heterogeneous Error Envelopes

SiliconFlow does NOT consistently wrap errors in a single JSON envelope. The shapes vary by error class:

- **400**: JSON object with `code`, `message`, `data` fields
- **401**: Plain string `"Invalid token"` (NOT valid JSON)
- **404**: Plain string `"404 page not found"`
- **429**: JSON object with `message`, `data` fields (no `code`)
- **503**: JSON object with `code: 50505`, `message`, `data`
- **504**: Plain string

AgentKthx's error parser must try JSON parsing first, fall back to plain-text body.

### Comprehensive Error Handling

```python
class SiliconFlowErrorHandler:
    ERROR_CODES = {
        400: {
            "message": "Bad Request",
            "recoverable": False,
            "actions": ["Check request format",
                       "Verify model supports requested features",
                       "Check max_tokens vs context window"]
        },
        401: {
            "message": "Invalid token",
            "recoverable": False,
            "actions": ["Regenerate API key at https://cloud.siliconflow.com/account/ak",
                       "Verify key starts with 'sk-'"]
        },
        403: {
            "message": "Permission Denied",
            "recoverable": False,
            "actions": ["Account suspended or under review",
                       "Contact SiliconFlow support"]
        },
        404: {
            "message": "Model not found",
            "recoverable": False,
            "actions": ["Check model ID at https://cloud.siliconflow.com/models",
                       "Model may have been removed from catalog"]
        },
        429: {
            "message": "Rate Limit (TPM) OR Free Tier Quota Exceeded",
            "recoverable": True,  # rate limit; NOT quota exhaustion on paid models
            "retry_after": "Retry-After header (when present)",
            "actions": ["Distinguish: 'TPM limit reached' = transient rate limit (backoff)",
                       "Free-tier exhaustion: free models have NO quota, so 429 is always transient",
                       "Paid model exhaustion: top up balance at cloud.siliconflow.com"]
        },
        500: {
            "message": "Internal Server Error",
            "recoverable": True,
            "retry_after": 5,
            "actions": ["Retry with backoff"]
        },
        503: {
            "message": "Model service overloaded (code 50505)",
            "recoverable": True,
            "retry_after": 30,
            "actions": ["Retry with longer backoff",
                       "Try alternative model in same family"]
        },
        504: {
            "message": "Gateway Timeout",
            "recoverable": True,
            "retry_after": 10,
            "actions": ["Retry", "Reduce max_tokens",
                       "For R1: enable_thinking=false to skip reasoning"]
        }
    }

    def handle_error(self, response) -> dict:
        """Handle API errors with recovery suggestions"""
        code = response.status_code
        # SiliconFlow returns heterogeneous error bodies — try JSON first
        try:
            error_data = response.json()
            if isinstance(error_data, dict):
                message = error_data.get("message", "")
                if not message:
                    message = error_data.get("data", "")
            else:
                message = str(error_data)
        except Exception:
            try:
                message = response.text.strip().strip('"')
            except Exception:
                message = "Unknown error"

        info = self.ERROR_CODES.get(code, {
            "message": "Unknown Error",
            "recoverable": False,
            "actions": ["Check logs", "Contact SiliconFlow support"]
        })

        # Special case: 429 with "TPM" in message is rate limit (transient)
        # 429 with "balance" or "quota" is permanent until top-up
        if code == 429:
            if "tpm" in message.lower() or "rate" in message.lower():
                info = {**info, "recoverable": True, "message": "TPM rate limit"}
            elif "balance" in message.lower() or "quota" in message.lower():
                info = {**info, "recoverable": False,
                        "message": "Account balance exhausted (paid models)",
                        "actions": ["Top up balance at cloud.siliconflow.com",
                                   "Switch to free-tier models (Qwen3-8B, R1-Distill-Qwen-7B)"]}

        return {
            "code": code,
            "message": info["message"],
            "raw_message": message,
            "recoverable": info["recoverable"],
            "retry_after": info.get("retry_after", 0),
            "actions": info["actions"]
        }
```

---

## Rate Limiting & Concurrency

### Documented Limits

SiliconFlow's documented limits (from the 429 error message):

| Limit | Value | Notes |
|-------|-------|-------|
| Tokens per minute (TPM) | Varies by model + account tier | Documented in 429 message: "TPM limit reached" |
| Requests per minute | (not documented separately) | Bounded by TPM |
| Concurrent requests | (not documented) | Server queues internally |
| Streaming duration | (not documented) | Standard 5-min HTTP timeout recommended |

### Free Model Specifics

Free-tier models (`Qwen/Qwen3-8B`, `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`, `deepseek-ai/DeepSeek-OCR`) have:
- **No daily quota** (unlimited requests, subject to TPM)
- **No monthly quota**
- **No credit card required**

This makes SiliconFlow unique among the four documented providers — the only one with a truly uncapped free tier (subject to per-request rate limits).

### Rate-Limit Headers

SiliconFlow does NOT document specific rate-limit headers in the OpenAPI spec. The 429 response includes a `Retry-After` header in some cases — AgentKthx's existing `api_resilience.py` reads this when present.

---

## Multimodal Content Handling

### Content Types

```python
class SiliconFlowMultimodalHandler:
    CONTENT_TYPE_VALIDATION = {
        "text": {"max_length": 131072, "allowed_types": [str]},
        "image_url": {
            "max_size_mb": 5,
            "allowed_formats": ["jpg", "jpeg", "png", "webp"],
            "max_images": 1  # most SF vision models accept 1 image per turn
        }
    }
```

### Vision Models on SiliconFlow

- `deepseek-ai/deepseek-vl2` — DeepSeek Vision-Language v2
- `Qwen/Qwen2.5-VL-7B-Instruct` — Qwen2.5 VL
- `zai-org/GLM-4.5V` — GLM-4.5 Vision
- `zai-org/GLM-4.6V` — GLM-4.6 Vision
- `zai-org/GLM-5V-Turbo` — GLM-5 Vision Turbo

All accept OpenAI-spec `image_url` content items, both URL-referenced and base64-encoded.

---

## Thinking & Reasoning Configuration

### `enable_thinking` Field

For hybrid models that support both thinking and non-thinking modes (Qwen3, GLM-Z1), SiliconFlow exposes a top-level `enable_thinking` boolean:

```python
# Force thinking mode (default for thinking-capable models)
body["enable_thinking"] = True

# Force non-thinking mode (faster, no reasoning trace)
body["enable_thinking"] = False
```

### Reasoning Content Surfacing

When thinking is enabled, the response includes a `reasoning_content` field on the assistant message:

```json
{
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "The answer is 42.",
            "reasoning_content": "Let me think about this step by step. The user asked..."
        }
    }]
}
```

In streaming mode, `reasoning_content` chunks arrive BEFORE `content` chunks — see Streaming section.

### Thinking Models (Verified)

| Model ID | Thinking Support | Tool Support | Notes |
|----------|-------------------|--------------|-------|
| `deepseek-ai/DeepSeek-R1` | ✅ Always on | ❌ | Pure reasoning model |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | ✅ Always on | ❌ | FREE TIER |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` | ✅ Always on | ❌ | |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-32B` | ✅ Always on | ❌ | |
| `THUDM/GLM-Z1-32B-0414` | ✅ Always on | ✅ | Hybrid: thinking + tools |
| `THUDM/GLM-Z1-9B-0414` | ✅ Always on | ✅ | Hybrid |
| `Qwen/Qwen3-235B-A22B-Thinking-2507` | ✅ Always on | ❌ | Thinking variant |
| `Qwen/Qwen3-30B-A3B-Thinking-2507` | ✅ Always on | ❌ | Thinking variant |
| `Qwen/Qwen3-Next-80B-A3B-Thinking` | ✅ Always on | ❌ | Thinking variant |
| `Qwen/Qwen3-Omni-30B-A3B-Thinking` | ✅ Always on | ✅ | Hybrid: thinking + tools + multimodal |
| `moonshotai/Kimi-K2-Thinking` | ✅ Always on | ❌ | Kimi K2 thinking variant |

For thinking + tools combo, the only options on SiliconFlow are `THUDM/GLM-Z1-32B-0414`, `THUDM/GLM-Z1-9B-0414`, and `Qwen/Qwen3-Omni-30B-A3B-Thinking`. AgentKthx should probe per-model via `test_tool_support`.

---

## Implementation Notes for AgentKthx

### 1. Backend Integration Points

```python
from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatMixin

class SiliconFlowBackend(OpenAICompatMixin, CloudBackend):
    """SiliconFlow cloud backend (OpenAI-compatible surface)."""

    is_cloud = True

    def __init__(self, config=None, base_url="https://api.siliconflow.com/v1", **kwargs):
        super().__init__(config=config, base_url=base_url, **kwargs)
        self.api_key = config.SILICONFLOW_API_KEY if config else kwargs.get("api_key")

    @property
    def auth_header(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def _build_request(self, model: str, messages: list, **kwargs) -> dict:
        """Build SiliconFlow (OpenAI-spec) request"""
        request = {
            "model": model,
            "messages": messages,
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "max_tokens": kwargs.get("num_predict", 4096),
            "stream": kwargs.get("stream", False),
        }
        # SiliconFlow passes through vLLM-style sampling params
        if kwargs.get("top_k") is not None:
            request["top_k"] = kwargs["top_k"]
        if kwargs.get("repeat_penalty") is not None:
            request["repetition_penalty"] = kwargs["repeat_penalty"]
        if kwargs.get("seed") is not None:
            request["seed"] = kwargs["seed"]
        if "stop" in kwargs:
            request["stop"] = kwargs["stop"]
        # Thinking control (hybrid models only)
        if "enable_thinking" in kwargs:
            request["enable_thinking"] = kwargs["enable_thinking"]
        if "tools" in kwargs:
            request["tools"] = kwargs["tools"]
            request["tool_choice"] = kwargs.get("tool_choice", "auto")
        return request

    def list_models(self) -> list:
        """List models via the OpenAI-compat /models endpoint"""
        req = urllib.request.Request(
            f"{self.base_url}/models",
            headers=self.auth_header,
            method="GET"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return [m["id"] for m in data.get("data", [])]

    def list_free_models(self) -> list:
        """SiliconFlow's free-tier model list (verified Oct 2026)."""
        FREE_MODELS = [
            "Qwen/Qwen3-8B",                            # general chat
            "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",  # reasoning
            # deepseek-ai/DeepSeek-OCR is image-OCR, not chat — exclude
        ]
        if not getattr(self, "_free_only", False):
            return self.list_models()
        # Filter the live catalog against the known-free set
        catalog = self.list_models()
        return [m for m in catalog if m in FREE_MODELS]
```

### 2. FREE_ONLY Enforcement

```python
# Set SILICONFLOW_FREE_ONLY=1 env var
# Filters catalog to the 3 free models — useful for cost-conscious agentic workflows
```

Unlike NVIDIA/Cloudflare where FREE_ONLY doesn't filter (because quota is account-wide), SiliconFlow's FREE_ONLY actually filters the catalog to the 3 known-free models. This is the most useful FREE_ONLY implementation among the four providers.

### 3. Plugin Manifest

```json
{
    "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
    "name": "siliconflow",
    "version": "0.1.0",
    "description": "SiliconFlow API backend for 200+ models (DeepSeek, Qwen, GLM, Llama, Kimi, MiniMax, ERNIE, Hunyuan, Phi, Gemma) via OpenAI Chat-Completions API at api.siliconflow.com — free tier with 3 permanently-free models (Qwen3-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-OCR), no quota, no credit card required",
    "backend_class": "agentkthx.backends.siliconflow.SiliconFlowBackend",
    "backend_type": "cloud",
    "env_vars": ["SILICONFLOW_API_KEY"],
    "optional_env_vars": ["SILICONFLOW_FREE_ONLY", "SILICONFLOW_BASE_URL"],
    "default_base_url": "https://api.siliconflow.com/v1",
    "free_tier": true
}
```

### 4. Existing Patterns That Apply Directly

- **`CloudBackend` base class** (R07.05 MAINT-02): inherits retry helpers, SSE streaming, JSON-endpoint layout for free.
- **`api_resilience.py`**: 429 classified as transient (TPM rate limit). For paid-model quota exhaustion (message contains "balance"), AgentKthx should mark as permanent — the same special-case pattern used for NVIDIA credit exhaustion.
- **`tool_support.json` cache**: same `<model>` plain-key namespace. Model IDs include the `<author>/` prefix — keep them intact.
- **`is_local_base_url`**: correctly classifies `api.siliconflow.com` and `api.siliconflow.cn` as remote.
- **`_close_http_response`** (R07.25 ROB-06): deterministic close applies automatically.
- **`reasoning_content` field**: existing AgentKthx already surfaces thinking content from the ZAI/OpenRouter implementations that include DeepSeek-R1 — same parser path applies.

### 5. Unique SiliconFlow-Specific Considerations

- **`enable_thinking` toggle**: Only the second provider (after ZAI) to expose a thinking on/off switch. AgentKthx's existing `thinking: {type: "enabled"|"disabled"}` config maps onto `enable_thinking: true|false`.
- **Heterogeneous error envelopes**: 401 and 404 return plain strings, not JSON. The error parser must handle both. This is a divergence from the OpenAI spec — handled in the `SiliconFlowErrorHandler` above.
- **CN TLD alternative**: For users inside China, `api.siliconflow.cn` offers lower latency than `api.siliconflow.com`. The base URL is configurable via `SILICONFLOW_BASE_URL` env var.
- **DeepSeek-OCR free model is NOT a chat model**: It's an OCR endpoint (image → text). AgentKthx should exclude it from the chat-eligible catalog even when `SILICONFLOW_FREE_ONLY=1` is set — the `list_free_models()` implementation above filters it out.

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `401 "Invalid token"` | Wrong API key, or key revoked | Regenerate at https://cloud.siliconflow.com/account/ak |
| `404 "404 page not found"` | Wrong endpoint URL | Verify base URL is `https://api.siliconflow.com/v1` (no trailing slash, no path beyond `/v1`) |
| `400` with `code: 20012` | Bad request — usually model-specific issue | Check `message` field for details; verify model supports requested features |
| `429` with "TPM limit reached" | Tokens-per-minute rate limit | Backoff with Retry-After; reduce max_tokens |
| `429` with "balance" or "quota" | Account balance exhausted (paid models) | Top up balance OR switch to free-tier models |
| `503` with `code: 50505` | Model service overloaded | Retry with longer backoff; try alternative model in same family |
| `504` | Gateway timeout (typically DeepSeek-R1 with long thinking) | Reduce max_tokens, set `enable_thinking: false`, or use non-streaming |
| `400` with "tool calls not supported" | Reasoning model (R1, GLM-Z1-thinking, Kimi-K2-Thinking) with `tools` in body | Disable native tools, use ReAct mode |
| Empty `content` but `reasoning_content` populated | Thinking model returned reasoning only | This is normal for R1 — set `enable_thinking: false` to skip reasoning |
| Streaming stalls > 60s | DeepSeek-R1 with long thinking trace | Use non-streaming, increase timeout, or disable thinking |
| `tool_calls` empty despite model support | `tool_choice: "none"` set, or model not in the supported list | Verify model supports tools via `test_tool_support` probe |
| Image request returns `400` | Model doesn't support vision, or image too large | Use a vision-capable model (deepseek-vl2, Qwen2.5-VL, GLM-V); re-encode image <5MB |

---

## Appendix: Free-Model Catalog

### Permanently-Free Models (Verified Oct 2026)

| Model ID | Family | Type | Context | Notes |
|----------|--------|------|---------|-------|
| `Qwen/Qwen3-8B` | Qwen3 | Chat + thinking | 32K | Best general-purpose free model on SiliconFlow |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | DeepSeek-R1 distill | Reasoning | 32K | Free reasoning model; no tools support |
| `deepseek-ai/DeepSeek-OCR` | DeepSeek OCR | OCR (image→text) | 4K | Not a chat model — exclude from chat catalog |

### Free-Model Recommendations for AgentKthx

- **For tool-using agentic workflows**: `Qwen/Qwen3-8B` is the only free model that supports `tools` (via the chat-completions API). At 8B params, it's capable for general chat + light tool use.
- **For pure reasoning tasks** (no tools needed): `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` provides DeepSeek-R1 quality reasoning at zero cost. The 7B distill is competitive with the 32B distill on most benchmarks.
- **For long-running agentic sessions**: SiliconFlow's no-quota free tier is the strongest of the four documented providers — no daily/monthly caps to exhaust during a long agentic run.

### Free-Model Caveats

- **TPM rate limits still apply**: Even on free models, the TPM (tokens-per-minute) cap can throttle sustained high-volume workflows. Backoff via the existing `api_resilience.py` handles this.
- **Free models can change**: SiliconFlow reserves the right to change the free-tier model set. Verify via the live `/models` endpoint before relying on a specific model for production.
- **Free models do NOT include multimodal**: All three free models are text-only. For vision, must use paid `deepseek-vl2`, `Qwen2.5-VL`, or GLM-V variants.

---

## Appendix: OpenAI Wire-Format Deltas

For a developer familiar with vanilla OpenAI Chat Completions, here's the diff for SiliconFlow:

| Field | OpenAI | SiliconFlow |
|-------|--------|-------------|
| Base URL | `https://api.openai.com/v1` | `https://api.siliconflow.com/v1` (or `.cn` for China) |
| Auth | Bearer API key (`sk-`) | Bearer API key (`sk-`) — same prefix |
| Model ID | `gpt-4o-mini` | `Qwen/Qwen3-32B` (`<author>/<model>` format) |
| `top_k` | Supported | Supported |
| `repetition_penalty` | NOT supported | Supported (vLLM-style) |
| `seed` | Supported | Supported |
| `enable_thinking` | N/A (use `reasoning.effort` on o1) | Supported (boolean, hybrid models only) |
| `reasoning_content` | N/A (on o1 via `reasoning` field) | Supported (DeepSeek-R1, GLM-Z1, Qwen3-Thinking) |
| `response_format: json_schema` | Supported | Supported (select models) |
| `tools` | Standard | Standard (excluding reasoning models) |
| `/v1/models` endpoint | Yes | Yes |
| Error envelope | OpenAI-spec `{error: {message, type, code}}` | Heterogeneous — JSON for 400/429/503, plain string for 401/404/504 |
| Streaming SSE | Standard | Standard (with `reasoning_content` extension) |
