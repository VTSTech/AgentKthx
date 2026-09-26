# JEV API Mode — System-One Decisions via Any LLM

**Added:** R05.7 (Alpha)
**Status:** Experimental — wrapper-based emulation of the Jev API shape

## What is JEV mode?

`--api jev` is a new API mode (sibling of `openre` and `openai`) that wraps any
chat-capable backend with a constrained decision prompt + JSON output mode,
returning a structured decision envelope compatible with the Jev API shape:

```json
{
  "decision": "spam",
  "probability": 0.92,
  "alternatives": [
    {"value": "promotions", "probability": 0.06},
    {"value": "inbox", "probability": 0.02}
  ],
  "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
  "latency_ms": 250.0,
  "_jev": true,
  "_parse_ok": true
}
```

This mode **does not** call the real TypeSafe Jev API (`api.typesafe.ai/v1/systemone`).
Instead, it uses TypeSafe's "System One LLM wrapper" pattern: any chat-capable
LLM is constrained via a decision prompt + `response_format={"type": "json_object"}`
to emit Jev-shaped decisions.

## Why use it?

Jev (launched September 2026 by TypeSafe AI) introduced a "System-One" model
class: fast, structured, calibrated decisions instead of free-form text
generation. Use cases:

- **Routing** — "which agent should handle this?"
- **Classification** — "is this spam, inbox, or promotions?"
- **Scoring** — "how relevant is this document? (0.0-1.0)"
- **Threshold checks** — "should we escalate this?"
- **Choice selection** — "which of these 5 options is best?"

Native TypeSafe Jev is paid and waitlisted. JEV mode lets you get the same
decision envelope shape using **free models you already have** (GLM-4.5-flash
via ZAI, free OpenRouter models, local Ollama models, etc.).

## Usage

### CLI — single-shot decision

```bash
# Free ZAI model + JEV mode
agentkthx run "Classify this email: 'You won a prize!'" \
    --api jev \
    --backend zai \
    --model glm-4.5-flash

# Free OpenRouter model + JEV mode
agentkthx run "Is this a bug or feature request?" \
    --api jev \
    --backend openrouter \
    --model poolside/laguna-xs-2.1:free

# Local Ollama model + JEV mode
agentkthx run "Route this ticket to math, file, or general agent" \
    --api jev \
    --backend ollama \
    --model qwen2.5:0.5b
```

Output is a JSON decision envelope (printed as the `content` field).

### Python API — direct decision call

```python
from agentkthx.backends import get_backend

# ZAI free model
backend = get_backend("zai", api_mode="jev")

decision = backend.generate_decision(
    model="glm-4.5-flash",
    state="Email from unknown@xyz.com with subject 'You won a prize!'",
    choices=["spam", "inbox", "promotions"],
    question="Where should this email be routed?",
)

print(decision["decision"])      # "spam"
print(decision["probability"])   # 0.92
print(decision["alternatives"])  # [{"value": "promotions", "probability": 0.06}, ...]
```

### Python API — within an agent pipeline

```python
from agentkthx import Agent, Orchestrator, AgentCard

# Use JEV for routing decisions inside an orchestrator
router = get_backend("openrouter", api_mode="jev")

# Free OpenRouter model picks which sub-agent should handle the task
state = {"task": "calculate 15 * 8 and save to file"}
decision = router.generate_decision(
    model="poolside/laguna-xs-2.1:free",
    state=state,
    choices=["math_agent", "file_agent", "general_agent"],
)

# Then dispatch to the chosen sub-agent (using normal OPENAI mode)
chosen_agent = decision["decision"]
confidence = decision["probability"]
if confidence < 0.5:
    chosen_agent = "general_agent"  # fallback on low confidence
```

## Backends that support JEV mode

| Backend | Status | Notes |
|---------|--------|-------|
| `ollama` | ✅ | Uses `/v1/chat/completions` with `response_format=json_object` |
| `zai` | ✅ | Routes through `_generate_with_auth` (Bearer auth + ZAI_FREE_ONLY) |
| `openrouter` | ✅ | Routes through `generate()` (429 retry + OPENROUTER_FREE_ONLY) |
| `gemini` | ✅ | Routes through `generate()` — `_jev_call_completions` flips api_mode to OPENAI and forces `reasoning_effort="minimal"` for decisions (saves tokens on Gemini 3.x) |
| `llama-server` | ✅ | Inherits from OllamaBackend |
| `bitnet` | ✅ | Inherits from LlamaServerBackend |

## How it works

### Architecture

```
agentkthx run "..." --api jev --backend zai -m glm-4.5-flash
                            │
                            ▼
              ZaiBackend(api_mode="jev")
                            │
                            ▼
        ZaiBackend.generate(messages)
                            │
                            ▼
         _maybe_jev_dispatch()  ←─ returns dict or None
                            │
                            ▼ (api_mode == JEV)
         OllamaBackend.generate_decision()
                            │
                            ▼
         _build_jev_messages() — builds System-One prompt
                            │
                            ▼
         _jev_call_completions() — overridden per backend
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   OllamaBackend.generate_completions()  ZaiBackend._generate_with_auth()
   (default)                             (Bearer auth)
                                          │
                                          ▼
                              OpenRouterBackend.generate()
                              (429 retry)
                            │
                            ▼ (LLM returns JSON)
         _parse_jev_response() — strips fences, clamps probabilities,
                                  snaps to constrained choice if fuzzy match
                            │
                            ▼
         Decision envelope dict
```

### Key methods

| Method | Location | Purpose |
|--------|----------|---------|
| `ApiMode.JEV` | `core/types.py` | Enum value `"jev"` |
| `BaseBackend.generate_decision()` | `backends/base.py` | Abstract primitive; default raises `NotImplementedError` |
| `OllamaBackend.generate_decision()` | `backends/ollama.py` | Concrete wrapper impl using decision prompt + JSON mode |
| `OllamaBackend._build_jev_messages()` | `backends/ollama.py` | Static: builds system + user messages for decision call |
| `OllamaBackend._parse_jev_response()` | `backends/ollama.py` | Static: parses JSON output (tolerant of fences, malformed) |
| `OllamaBackend._maybe_jev_dispatch()` | `backends/ollama.py` | Dispatch hook called from `generate()` — returns dict or None |
| `OllamaBackend._jev_call_completions()` | `backends/ollama.py` | Hook for underlying LLM call (default: `generate_completions`) |
| `ZaiBackend._jev_call_completions()` | `plugins/zai/zai.py` | Override: routes through `_generate_with_auth` (Bearer auth) |
| `OpenRouterBackend._jev_call_completions()` | `plugins/openrouter/openrouter.py` | Override: routes through `generate()` (429 retry) |

### Decision envelope shape

```python
{
    "decision": str,                # chosen option (or generated answer)
    "probability": float,           # 0.0-1.0 calibrated confidence
    "alternatives": [               # ranked runner-ups
        {"value": str, "probability": float},
        ...
    ],
    "usage": {
        "input_tokens": int,
        "output_tokens": int,
        "total_tokens": int,
    },
    "latency_ms": float,
    "raw": dict,                    # underlying LLM response
    "_jev": True,                   # marker for downstream code
    "_parse_ok": bool,              # False if JSON parsing failed (fallback)
}
```

### When `--api jev` is active in `generate()`

The `content` field of the response is the JSON-serialized decision envelope.
This lets the existing agent loop / CLI still see a string in the place it
expects one. The `_jev: True` marker in the response dict lets downstream
code detect JEV mode and parse the content as JSON if needed.

```python
# CLI usage
result = backend.generate(model="glm-4.5-flash", messages=[...])
# result["content"] is a JSON string: '{"decision": "spam", "probability": 0.92, ...}'
# result["_jev"] is True

# Programmatic usage — preferred
decision = backend.generate_decision(model="glm-4.5-flash", state="...", choices=[...])
# decision is a dict: {"decision": "spam", "probability": 0.92, ...}
```

## Configuration

No new env vars. JEV mode uses the existing backend configuration:

```bash
# ZAI free models (existing config)
export ZAI_API_KEY="sk-..."
export ZAI_FREE_ONLY=true                    # restrict to free models
export ZAI_FREE_FALLBACK_MODEL="glm-4.5-flash"

# OpenRouter free models (existing config)
export OPENROUTER_API_KEY="sk-or-..."
export OPENROUTER_FREE_ONLY=1                # restrict to :free models
```

## Recommended free models for JEV mode

### ZAI (free tier)
- `glm-4.5-flash` — fast, 128K context, free
- `glm-4.7-flash` — newer, 200K context, free
- `glm-5.3-flash` — newest, 1M context, free

### OpenRouter (free tier)
- `poolside/laguna-xs-2.1:free`
- `meta-llama/llama-3.2-3b-instruct:free`
- `google/gemini-flash-1.5:free`
- `qwen/qwen-2.5-7b-instruct:free`

### Ollama (local, always free)
- `qwen2.5:0.5b` — fastest, decent decisions
- `qwen2.5:1.5b` — better calibration
- `qwen2.5:7b` — best local decisions
- `functiongemma:270m` — tiny, native function calling

## Limitations

1. **Not a real Jev** — JEV mode emulates the Jev API shape using any LLM.
   It does not call TypeSafe's `api.typesafe.ai/v1/systemone` endpoint.
   Calibrated probabilities may be less accurate than native Jev.

2. **Single-shot only** — JEV mode is designed for single decision calls,
   not multi-turn chat sessions. The agent loop expects text content; JEV
   mode stuffs JSON into `content` which may confuse multi-step reasoning.

3. **JSON mode dependency** — JEV mode requests
   `response_format={"type": "json_object"}`. If the underlying model
   doesn't honor this, output is still parsed best-effort (markdown fence
   stripping, fallback to raw text as decision).

4. **No streaming** — `generate_decision()` does not stream. The decision
   is returned as a single envelope.

5. **Tool calling disabled** — Decisions never call tools. `tools=None`
   is always passed to the underlying LLM call.

6. **Reasoning models inflate latency** — Thinking-capable models (e.g.
   GLM-4.5-flash) emit a `reasoning_content` field before the final JSON
   envelope. This reasoning is billable (counts toward `completion_tokens`)
   but not used by the decision parser. Verified smoke test showed
   GLM-4.5-flash producing 319 reasoning tokens vs ~80 tokens of actual
   decision JSON, inflating latency to ~22s for a trivial classification.

   **Workaround** (R05.8+): Use `--thinking off` to disable thinking entirely
   at the CLI level. This drops GLM-4.5-flash latency from ~22s to ~2-3s on
   trivial decisions:

   ```bash
   agentkthx run "Is this spam?" \
       --api jev --backend zai -m glm-4.5-flash --thinking off
   ```

   Programmatic callers can pass `think=False` directly:

   ```python
   decision = backend.generate_decision(
       model="glm-4.5-flash",
       state="...",
       choices=["a", "b"],
       think=False,  # disable reasoning for fast decisions
   )
   ```

   To inspect the reasoning_content (when you DO want to see what the
   model was thinking), use `--think` to display it in CLI output:

   ```bash
   agentkthx run "Is this spam?" \
       --api jev --backend zai -m glm-4.5-flash --think
   ```

## Testing

```bash
# Run the JEV test suite (33 tests, no network)
python -m pytest tests/test_jev_api_mode.py -v

# Quick smoke test (requires ZAI API key)
agentkthx run "Is 15 * 8 = 120? Answer yes or no." \
    --api jev --backend zai -m glm-4.5-flash
```

## Verified

**Smoke test** — 2026-09-20, ZAI free tier (GLM-4.5-flash):

```bash
$ agentkthx run "Is 'You won a prize' spam or inbox?" \
    --api jev --backend zai -m glm-4.5-flash
```

Result envelope (formatted):
```json
{
  "decision": "spam",
  "probability": 0.95,
  "alternatives": [{"value": "inbox", "probability": 0.05}],
  "usage": {"input_tokens": 292, "output_tokens": 319, "total_tokens": 611},
  "latency_ms": 22139,
  "_jev": true,
  "_parse_ok": true
}
```

Probabilities correctly summed to 1.0 (0.95 + 0.05). Decision parsing
succeeded on the first attempt — no markdown fence stripping needed.

## Future work

- **Native Jev plugin** — A future `agentkthx/plugins/jev/` plugin could
  hit TypeSafe's real `/v1/systemone` endpoint for users who want to pay
  for native calibrated decisions. This would override `generate_decision()`
  to skip the LLM wrapper and call the decision endpoint directly.

- **Orchestrator integration** — The `Orchestrator` could use
  `generate_decision()` for router-mode agent selection, replacing the
  current text-based routing.

- **Streaming decisions** — Yield OpenResponses-style events
  (`response.in_progress` → `response.output_item.done`) for streaming
  decision envelopes.

## References

- [TypeSafe AI — Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) (Sep 16, 2026)
- [LangChain — What Is Jev? A Guide to TypeSafe AI's System One Model](https://www.langchain.com) (Sep 18, 2026)
- [DataCamp — Jev: TypeSafe's System One Model That Never Hallucinates](https://www.datacamp.com) (Sep 17, 2026)
- [OpenRouter — Jev 1.13 API Pricing & Providers](https://openrouter.ai/typesafe/jev-1.13) (Sep 18, 2026)

---

Written by VTSTech — https://www.vts-tech.org
