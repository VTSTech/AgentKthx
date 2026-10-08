# SiliconFlow API Technical Reference for AgentKthx Implementation

> **Technical Implementation Guide**
> **Generated from**: https://docs.siliconflow.com/en/api-reference/chat-completions/chat-completions.md + https://docs.siliconflow.com/en/userguide/guides/function-calling.md + https://docs.siliconflow.com/llms.txt (verified Oct 2026)
> **Free-tier verification (SUPERSEDED — billing-corrected 2026-10-09)**: the third-party free-tier claim ("3 permanently-free models: Qwen3-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-OCR — no usage limits", via [pricepertoken.com](https://pricepertoken.com) + [therouter.ai](https://therouter.ai)) is **WRONG for the current API**. SiliconFlow's own billing console shows `Qwen/Qwen3-8B` **BILLS**: meter `qwen/qwen3-8b.online.input-tokens`, 0.235K input tokens → **$0.000014** (≈ **$0.06 per 1M input tokens**). A "0.563K tokens → $0.0000" console row is **4-decimal display rounding** (real ≈ $0.0000338) — never read a $0.0000 row as free. **There is no free tier: every model bills against the account balance.**
> **R07.29 live-probe update (2026-10-09)**: `GET /v1/models` lists **79 models**; two of the three formerly-documented free models (`deepseek-ai/DeepSeek-R1-Distill-Qwen-7B`, `deepseek-ai/DeepSeek-OCR`) are **no longer served**. `Qwen/Qwen3-8B` survives as the **cheapest known** chat model (input ≈$0.06/1M tokens — BILLS, not free; see the billing correction above). Also probe-verified: `/v1/models` cards carry only `{id, object, created, owned_by}` (no pricing / context / capabilities fields), `GET /v1/user/info` → **410 deprecated**, `GET /v1/user/balance` → **404**, and there is **no `/v1/pricing` endpoint** (pricing lives on the web console). The AgentKthx seed catalog was pruned to the 30 confirmed-live chat models.
> **R07.29 smoke-run update (2026-10-09)**: the maintainer's end-to-end smoke attempt on a drained account verified auth + the 79 → 58 catalog + one `--think` generation, then surfaced balance exhaustion as **HTTP 402 "Sorry, your account balance is insufficient"** on both tool-call steps. **402 Payment Required is the live balance-exhaustion signal** (429 carries the transient TPM wording); the AgentKthx quota classifier now fast-fails 402 balance bodies with the top-up message. With no free path, the backend rests at Limited Support until a topped-up 5/5 smoke.
> **Live-behavior notes**: 2026-10-07 (updated 2026-10-09) — SiliconFlow is a China-hosted OpenAI-compatible aggregator. No free tier at all: every model bills the account balance — `Qwen/Qwen3-8B` at ≈$0.06/1M input is the cheapest known option for high-volume agentic workloads.
> **Primary focus**: OpenAI-compatible Chat Completions endpoint at `https://api.siliconflow.com/v1` (also accessible via the `.cn` TLD at `https://api.siliconflow.cn/v1` for China-domestic traffic).
> **Last Updated**: 2026-10-09 (R07.29 live-probe reconciliation + billing correction + smoke run — no free tier; 402 balance-exhaustion signal)
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
12. [Appendix: Model Pricing Reality](#appendix-model-pricing-reality)
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
| Account API key | `sk-` | https://cloud.siliconflow.com/account/ak | Full account access — every model the account balance covers (no free tier — all models bill). One key per account. |
| Sub-account key | `sk-` | Console → Sub-accounts | Same scope but tied to a sub-account; useful for team isolation |

**AgentKthx guidance**: Use a single `sk-` key read from `SILICONFLOW_API_KEY` env var. Every model requires account balance (no free tier — keep it topped up at https://cloud.siliconflow.com; `Qwen/Qwen3-8B` at input ≈$0.06/1M tokens is the cheapest known model).

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
    "Qwen/Qwen3-8B",                    # cheapest known (input ≈$0.06/1M — BILLS)
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

### Pricing & Free-Tier Reality (billing-verified Oct 2026)

| Model ID | Family | Context | Notes |
|----------|--------|---------|-------|
| `Qwen/Qwen3-8B` | Qwen3 | 32K | Cheapest known chat model. **BILLS ≈$0.06/1M input tokens** (billing-console evidence: 235 input tokens → $0.000014, meter `qwen/qwen3-8b.online.input-tokens`). |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | DeepSeek-R1 distill | 32K | ~~Formerly free reasoning model (no tools).~~ **REMOVED from live catalog (R07.29 probe).** |
| `deepseek-ai/DeepSeek-OCR` | DeepSeek OCR | 4K | ~~Formerly free OCR model (image → text).~~ **REMOVED from live catalog (R07.29 probe).** |

There is **no free tier on the SiliconFlow API** — the third-party "3 permanently-free models" claim is stale, and even the cheapest model bills. Budget accordingly: a 1M-token input workload on Qwen3-8B costs ≈$0.06 (output pricing unverified — not on the SSR pricing page; the billing console is the source of truth). And beware the console's 4-decimal display rounding: a $0.0000 row can still be a real charge (0.563K tokens → $0.0000338 renders as $0.0000).

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
        402: {
            "message": "Account Balance Exhausted (Payment Required — live-verified R07.29)",
            "recoverable": False,
            "actions": ["Top up at cloud.siliconflow.com",
                       "No free tier exists — every model bills the account balance"]
        },
        429: {
            "message": "Rate Limit (TPM) OR Account Balance Exhausted",
            "recoverable": True,  # rate limit; balance exhaustion is NOT retryable
            "retry_after": "Retry-After header (when present)",
            "actions": ["Distinguish: 'TPM limit reached' = transient rate limit (backoff)",
                       "No free tier exists — every model bills the account balance",
                       "Balance exhaustion ('balance'/'quota'/'insufficient'): top up at cloud.siliconflow.com"]
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
        # 402 (Payment Required) IS balance exhaustion — live-verified R07.29
        # ("Sorry, your account balance is insufficient")
        if code in (402, 429):
            if "tpm" in message.lower() or "rate" in message.lower():
                info = {**info, "recoverable": True, "message": "TPM rate limit"}
            elif "balance" in message.lower() or "quota" in message.lower():
                info = {**info, "recoverable": False,
                        "message": "Account balance exhausted",
                        "actions": ["Top up balance at cloud.siliconflow.com",
                                   "No free model exists to switch to (every model bills)"]}

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

### Qwen3-8B Specifics (cheapest known model — BILLS)

`Qwen/Qwen3-8B` (the former "free tier" claim, billing-corrected R07.29) has:
- **No daily or monthly request quota** (subject to TPM)
- **No free pricing**: input ≈ $0.06/1M tokens ($0.000014 per 235-token request, billing-verified Oct 2026)

SiliconFlow has the lowest cost floor of the four documented providers (a rounding error per tiny Qwen3-8B request), but it is a floor, not zero — sustained agentic workloads draw down the account balance.

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
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | ✅ Always on | ❌ | ~~FREE TIER~~ **removed from live catalog (R07.29 probe)** |
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
        """SiliconFlow's free-tier model list — EMPTY (R07.29 billing
        probe: no free models exist; every model bills)."""
        # Formerly returned ["Qwen/Qwen3-8B"] — the billing console proved
        # it bills (235 input tokens → $0.000014), so the verified-free
        # set is empty. Re-verify via the console if a free tier returns.
        return []
```

### 2. FREE_ONLY Enforcement

```python
# Set SILICONFLOW_FREE_ONLY=1 env var
# Filters the catalog to the free models — currently NONE exist, so the
# listing empties and generate() RAISES before any billable request
```

Unlike NVIDIA/Cloudflare where FREE_ONLY doesn't filter (because quota is account-wide), SiliconFlow's FREE_ONLY genuinely filters (per-model pricing) — but with NO free models on the API (R07.29 billing probe), the honest filtered result is an EMPTY list, and generate() refuses rather than swapping to a "cheap" fallback that would silently bill under a flag that promises free. `SILICONFLOW_FREE_FALLBACK_MODEL` stays reserved for a future free tier.

### 3. Plugin Manifest

```json
{
    "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
    "name": "siliconflow",
    "version": "0.1.0",
    "description": "SiliconFlow API backend for the 79-model live catalog (DeepSeek, Qwen, GLM, Kimi, MiniMax, Hunyuan, Gemma, gpt-oss) via OpenAI Chat-Completions API at api.siliconflow.com — no free tier: every model bills (Qwen/Qwen3-8B is the cheapest known, input ≈$0.06/1M tokens, billing-verified R07.29)",
    "backend_class": "agentkthx.backends.siliconflow.SiliconFlowBackend",
    "backend_type": "cloud",
    "env_vars": ["SILICONFLOW_API_KEY"],
    "optional_env_vars": ["SILICONFLOW_FREE_ONLY", "SILICONFLOW_BASE_URL"],
    "default_base_url": "https://api.siliconflow.com/v1",
    "free_tier": false  # no free tier — every model bills (R07.29 billing probe)
}
```

### 4. Existing Patterns That Apply Directly

- **`CloudBackend` base class** (R07.05 MAINT-02): inherits retry helpers, SSE streaming, JSON-endpoint layout for free.
- **`api_resilience.py`**: 429 classified as transient (TPM rate limit). For paid-model quota exhaustion (message contains "balance"), AgentKthx should mark as permanent — the same special-case pattern used for NVIDIA credit exhaustion. Live-verified R07.29: balance exhaustion arrives as **402** "Sorry, your account balance is insufficient" (429 carries the TPM wording) — the AgentKthx `_looks_like_balance_exhaustion` classifier catches both statuses via body wording, with the transient indicators vetoing first.
- **`tool_support.json` cache**: same `<model>` plain-key namespace. Model IDs include the `<author>/` prefix — keep them intact.
- **`is_local_base_url`**: correctly classifies `api.siliconflow.com` and `api.siliconflow.cn` as remote.
- **`_close_http_response`** (R07.25 ROB-06): deterministic close applies automatically.
- **`reasoning_content` field**: existing AgentKthx already surfaces thinking content from the ZAI/OpenRouter implementations that include DeepSeek-R1 — same parser path applies.

### 5. Unique SiliconFlow-Specific Considerations

- **`enable_thinking` toggle**: Only the second provider (after ZAI) to expose a thinking on/off switch. AgentKthx's existing `thinking: {type: "enabled"|"disabled"}` config maps onto `enable_thinking: true|false`.
- **Heterogeneous error envelopes**: 401 and 404 return plain strings, not JSON. The error parser must handle both. This is a divergence from the OpenAI spec — handled in the `SiliconFlowErrorHandler` above.
- **CN TLD alternative**: For users inside China, `api.siliconflow.cn` offers lower latency than `api.siliconflow.com`. The base URL is configurable via `SILICONFLOW_BASE_URL` env var.
- **DeepSeek-OCR is NOT a chat model**: It's an OCR endpoint (image → text), and it (plus the formerly-free R1-Distill-7B) was removed from the live catalog entirely per the R07.29 probe — and with the billing correction there is no free tier at all; `Qwen/Qwen3-8B` is merely the cheapest known chat model (input ≈$0.06/1M — bills).

---

## Troubleshooting Matrix

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `401 "Invalid token"` | Wrong API key, or key revoked | Regenerate at https://cloud.siliconflow.com/account/ak |
| `404 "404 page not found"` | Wrong endpoint URL | Verify base URL is `https://api.siliconflow.com/v1` (no trailing slash, no path beyond `/v1`) |
| `400` with `code: 20012` | Bad request — usually model-specific issue | Check `message` field for details; verify model supports requested features |
| `402` "Sorry, your account balance is insufficient" | Account balance exhausted (live-verified R07.29 smoke run) | Top up at cloud.siliconflow.com — NOT retryable, no free model exists to switch to; AgentKthx fast-fails it with the top-up message |
| `429` with "TPM limit reached" | Tokens-per-minute rate limit | Backoff with Retry-After; reduce max_tokens |
| `429` with "balance" or "quota" | Account balance exhausted | Top up balance (no free model exists to switch to — every model bills) |
| `503` with `code: 50505` | Model service overloaded | Retry with longer backoff; try alternative model in same family |
| `504` | Gateway timeout (typically DeepSeek-R1 with long thinking) | Reduce max_tokens, set `enable_thinking: false`, or use non-streaming |
| `400` with "tool calls not supported" | Reasoning model (R1, GLM-Z1-thinking, Kimi-K2-Thinking) with `tools` in body | Disable native tools, use ReAct mode |
| Empty `content` but `reasoning_content` populated | Thinking model returned reasoning only | This is normal for R1 — set `enable_thinking: false` to skip reasoning |
| Streaming stalls > 60s | DeepSeek-R1 with long thinking trace | Use non-streaming, increase timeout, or disable thinking |
| `tool_calls` empty despite model support | `tool_choice: "none"` set, or model not in the supported list | Verify model supports tools via `test_tool_support` probe |
| Image request returns `400` | Model doesn't support vision, or image too large | Use a vision-capable model (deepseek-vl2, Qwen2.5-VL, GLM-V); re-encode image <5MB |

---

## Appendix: Model Pricing Reality (no free tier)

### Billing-Verified Pricing (Oct 2026)

| Model ID | Family | Type | Context | Pricing |
|----------|--------|------|---------|---------|
| `Qwen/Qwen3-8B` | Qwen3 | Chat + thinking | 32K | Input ≈ **$0.06/1M tokens** (235 tokens → $0.000014, meter `qwen/qwen3-8b.online.input-tokens`); output unverified — not on the SSR pricing page |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` | DeepSeek-R1 distill | Reasoning | 32K | ~~Formerly free~~ removed from live catalog (R07.29 probe) |
| `deepseek-ai/DeepSeek-OCR` | DeepSeek OCR | OCR (image→text) | 4K | ~~Formerly free~~ removed from live catalog (R07.29 probe) |

Flagship list prices (SSR pricing page, siliconflow.com/pricing, extracted 2026-10-09): Kimi-K3 $2.7/$13.5, GLM-5.3 $1.4/$4.4 per 1M input/output. Qwen3-8B is absent from the page — the billing console is the ground truth for it.

### Cost-Conscious Recommendations for AgentKthx

- **For tool-using agentic workflows**: `Qwen/Qwen3-8B` is the cheapest known tool-capable model (input ≈$0.06/1M — a rounding error per tiny request, but NOT free).
- **For pure reasoning tasks**: use the paid `deepseek-ai/DeepSeek-R1` (the former free R1-Distill-7B was removed from the live catalog, R07.29 probe).
- **For long-running agentic sessions**: no quota caps (only TPM), but the balance drains — monitor usage in the billing console.

### Billing Gotchas

- **$0.0000 display rounding**: the console renders amounts to 4 decimals — a $0.0000 row can be a real sub-$0.00005 charge (e.g. 0.563K tokens → $0.0000338 displays as $0.0000). Check the token counts and unit price, never just the displayed total.
- **Every POST logs a usage row** — even tiny probes. GETs log nothing (GET-only tooling is the zero-cost path).
- **The free-tier model set can change** (it already did: R1-Distill-7B and DeepSeek-OCR vanished, and Qwen3-8B's "free" status turned out to be display rounding). Verify via the live `/models` endpoint + your billing console before relying on any model's cost profile for production.
- **Pricing is NOT API-exposed**: no `/v1/pricing`, cards carry no pricing fields — the web pricing page + the billing console are the only sources.

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
