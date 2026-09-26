# ⚛️ AgentKthx R07.04

**Status: Alpha**

A minimal, modular, python stdlib, agentic framework for tool calling AI agents. Runs **locally** with [Ollama](https://ollama.com), [BitNet](https://github.com/microsoft/BitNet), [TurboQuant](https://github.com/TheTom/llama-cpp-turboquant), **in the cloud** with [OpenRouter](https://openrouter.ai), [ZAI](https://api.z.ai), [HuggingFace](https://huggingface.co/), [OpenAI](https://openai.com), [OrcaRouter](https://www.orcarouter.ai) and [Google Gemini](https://ai.google.dev/gemini-api/docs). Extensible via a manifest-based **plugin system** for additional backends and features.

Inspired by the architecture of OpenClaw, rebuilt from scratch for local-first operation.

**Written by [VTSTech](https://www.vts-tech.org)** · [GitHub](https://github.com/VTSTech/AgentKthx) [Discord](https://discord.gg/vSK3Ba2aQ)

> **ℹ️ Renamed from `AgentNova` (R06.0)**
>
> This project was previously named `AgentNova`. As of R06.0, the package has been renamed to `AgentKthx` (PyPI: `agentkthx`, CLI: `agentkthx`). The old `agentnova` PyPI package still works as a redirect — existing scripts and env vars (`AGENTKTHX_*`) continue to work without changes. See the [Migration Guide](#migration-from-agentnova) section below.

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/VTSTech/AgentKthx/blob/main/AgentKthx.ipynb)
[![GitHub commits](https://badgen.net/github/commits/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![GitHub latest commit](https://badgen.net/github/last-commit/VTSTech/AgentKthx)](https://GitHub.com/VTSTech/AgentKthx/commit/) [![CI](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml/badge.svg)](https://github.com/VTSTech/AgentKthx/actions/workflows/ci.yml)

[![pip - agentkthx](https://img.shields.io/badge/pip-agentkthx-2ea44f?logo=PyPi)](https://pypi.org/project/agentkthx/) [![PyPI version fury.io](https://badge.fury.io/py/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download month](https://img.shields.io/pypi/dm/agentkthx.svg)](https://pypi.org/project/agentkthx/) [![PyPI download day](https://img.shields.io/pypi/dd/agentkthx.svg)](https://pypi.org/project/agentkthx/)

[![License](https://img.shields.io/badge/License-MIT-blue)](#license) [![Go to Python website](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2FVTSTech%2FAgentKthx%2Frefs%2Fheads%2Fmain%2Fpyproject.toml&query=project.requires-python&label=python&logo=python&logoColor=white)](https://python.org)

<img width="854" height="645" alt="image" src="https://github.com/user-attachments/assets/c8daa2a6-0274-43b6-9364-2a604ad1aa4d" />
<img width="986" height="623" alt="image" src="https://github.com/user-attachments/assets/bbe21b8e-4f22-4dfb-8b82-1e95127643e7" />
<img width="768" height="428" alt="image" src="https://github.com/user-attachments/assets/daacd57c-4bd1-449b-9d0f-56a5058a3b9e" />
<img width="735" height="631" alt="image" src="https://github.com/user-attachments/assets/e36e916f-b815-4376-b8f1-f32394deae86" />
<img width="729" height="436" alt="image" src="https://github.com/user-attachments/assets/f5306ec6-77b1-4dee-8a20-d675e63814b2" />
<img width="848" height="316" alt="image" src="https://github.com/user-attachments/assets/86724683-b217-4788-9277-1e16b65d74c2" />
<img width="626" height="620" alt="image" src="https://github.com/user-attachments/assets/df0d91ec-8be5-47f1-a5fb-e89e57cdd38f" />
<img width="1219" height="662" alt="image" src="https://github.com/user-attachments/assets/063d6ece-ad1d-4ac6-901e-5634cc3222a9" />


## 📚 Documentation

| Document | Description |
|----------|-------------|
| [ARCH.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/ARCH.md) | Technical documentation for developers (directory structure, core design, orchestrator modes) |
| [CHANGELOG.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CHANGELOG.md) | Version history and release notes (includes LocalClaw history) |
| [TESTS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/TESTS.md) | Benchmark results, model recommendations, and testing guide |
| [PLUGIN_SPEC_v0.2.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/PLUGIN_SPEC_v0.2.md) | Plugin spec v0.2 — lifecycle hooks, plugin tools API, external plugin roots, dual-form manifests, migration guide from v0.1 |
| [JEV_API_MODE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/JEV_API_MODE.md) | JEV API mode — System-One decisions via any free LLM (Jev-compatible shape) |
| [ZAI_API_TECHNICAL_REFERENCE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/ZAI_API_TECHNICAL_REFERENCE.md) | ZAI API technical reference (auth, endpoints, parameters, error codes) |
| [OPENROUTER_API_TECHNICAL_REFERENCE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/OPENROUTER_API_TECHNICAL_REFERENCE.md) | OpenRouter API technical reference (sampling params, model catalog, provider routing, rate limits) |
| [GEMINI_API_TECHNICAL_REFERENCE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/GEMINI_API_TECHNICAL_REFERENCE.md) | Gemini API technical reference (OpenAI-compat endpoint, thinking config, free-tier limits, Gemma `<thought>` tag parser, 71-model catalog) |
| [HUGGINGFACE_API_TECHNICAL_REFERENCE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/HUGGINGFACE_API_TECHNICAL_REFERENCE.md) | Hugging Face Inference Router technical reference (router.huggingface.co/v1, 18 partner providers, :fastest/:cheapest/:preferred routing suffixes, 31-model free-tier whitelist, HF_FREE_ONLY enforcement, HTTP 402 credit-exhaustion fallback) |
| [OPENAI_API_TECHNICAL_REFERENCE.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/OPENAI_API_TECHNICAL_REFERENCE.md) | OpenAI API technical reference (Chat Completions + Responses API, GPT-6/GPT-5.6 Daybreak/gpt-realtime/gpt-image/gpt-transcribe lineup, service tiers, reasoning_effort enum, OPENAI_FREE_MODEL_WHITELIST) — blueprint for the planned R07.0x OpenAI plugin |
| [CREDITS.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CREDITS.md) | Acknowledges every project, inspiration, API, model creator, and specification that makes AgentKthx possible |

## Features

- **Zero dependencies** — Uses Python stdlib only (urllib for HTTP)
- **Plugin system** — Manifest-based plugin discovery, lazy loading, and dependency resolution (R05.0)
- **Plugin Spec v0.2 (R06.5)** — Lifecycle hooks (`on_init`/`on_run_start`/`on_run_end`/`on_error`/`on_shutdown`), plugin-provided tools, external plugin roots (`~/.agentkthx/plugins/`, `$AGENTKTHX_PLUGIN_PATH`), dual-form manifests (`extensions` block) with deprecation warnings for legacy fields, `plugins --load/--unload/--reload/--json/--verbose` management
- **Native + plugin backends** — Ollama built-in; OpenRouter, BitNet, ZAI, ACP, TurboQuant, Gemini as plugins
- **Multi-cloud support** — Access to 500+ models from OpenRouter, OpenAI, Anthropic, Google (Gemini + Gemma), Cohere
- **Dual API support** — OpenResponses (`--api openre`) and OpenAI Chat-Completions (`--api openai`)
- **JEV decision mode** — System-One decisions via any free LLM (`--api jev`) — Jev-compatible shape, no TypeSafe API key required
- **Thinking controls** — `--thinking off|auto|low|medium|high` to control model reasoning effort, `--think` flag to display reasoning_content (chain-of-thought) in CLI output
- **Three-tier tool support** — Native, ReAct, or none (auto-detected)
- **Small model optimized** — Fuzzy matching, argument normalization
- **Built-in security** — Path validation, command blocklist, SSRF protection (toggleable via `--security max|off`)
- **Multi-agent orchestration** — Router, pipeline, and parallel modes
- **Soul Spec v0.5** — Persona packages with progressive disclosure
- **AgentSkills spec** — Skill loading with SPDX license validation
- **Thinking models support** — Automatic handling of qwen3, deepseek-r1 thinking mode
- **Ctrl+C cancellation** — Graceful interrupt at backend, tool, and agent loop levels (R05.0)
- **Persistent memory** — SQLite-backed conversation persistence with session management (`--session`)
- **17 built-in tools** — Calculator, shell, file ops (read/write/edit/list/find), HTTP, web search, JSON parse, Python REPL, todo list, datetime, word/char count. Load mid-session via `/tool shell,read_file`
- **Dangerous tool confirmation** — `--confirm` flag for interactive approval of destructive operations
- **Audit logging** — Automatic JSON-lines logging of shell, write, and edit operations
- **Argument normalization** — ~100+ tool argument aliases for small model compatibility
- **JSON structured output** — `--response-format json` for structured JSON responses
- **Self-update** — `agentkthx update` to update to latest version from GitHub
- **Update check** — Startup + post-run notice for both release tracks: **stable** (new package on PyPI) and **development** (new commits on GitHub main); `agentkthx version` shows both too; always live (no cache — every run queries PyPI + GitHub main directly), fails silently offline, opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`
- **Persistent status footer** — 2-line terminal footer with live model/backend/token info (R05.4, scroll-region based)
- **OpenRouter 429 retry** — Automatic retry with `Retry-After` header support for rate-limited providers (R05.4)
- **Tool-call visibility** — Tool calls and results displayed in chat mode (R05.4)

## Installation

```bash
# Latest Development Release
pip install git+https://github.com/VTSTech/AgentKthx.git --force-reinstall

# Last Stable (as stable as Alpha can be) Release
pip install agentkthx
```

## Quick Start

### CLI Usage

```bash
# Run a single prompt
agentkthx run "What is 15 * 8?" --tools calculator

# Interactive chat
agentkthx chat -m qwen2.5:0.5b --tools calculator,shell

# Autonomous agent mode
agentkthx agent -m qwen2.5:7b --tools calculator,shell,write_file

# Use OpenAI Chat-Completions API
agentkthx chat -m qwen2.5:0.5b --api openai

# List available models
agentkthx models

# List available tools
agentkthx tools

# Resume a previous session
agentkthx chat -m qwen2.5:0.5b --session my-session

# Dangerous tool confirmation
agentkthx agent -m qwen2.5:7b --tools shell,write_file --confirm

# Force ReAct mode
agentkthx run "What is 15 * 8?" --tools calculator --force-react

# List persistent memory sessions
agentkthx sessions

# TurboQuant server management
agentkthx turbo list
agentkthx turbo start qwen2.5:7b
agentkthx turbo status
agentkthx turbo stop

# Self-update
agentkthx update
```

### Backend Options

```bash
# Native backends (always available)
agentkthx chat -m qwen2.5:0.5b --backend ollama         # Ollama (default)
agentkthx chat -m qwen2.5:7b --backend llama-server      # llama.cpp / TurboQuant

# Plugin backends (loaded on demand)
agentkthx chat -m poolside/laguna-xs-2.1:free --backend openrouter     # OpenRouter (free tier, plugin)
agentkthx chat -m openai/gpt-4o --backend openrouter                # OpenRouter (OpenAI models)
agentkthx chat -m anthropic/claude-3.5-sonnet --backend openrouter   # OpenRouter (Anthropic models)
agentkthx chat -m bitnet-b1.58-2b-4t --backend bitnet              # BitNet (plugin)
agentkthx chat -m glm-4.5-flash --backend zai                       # ZAI (free tier, plugin)
agentkthx chat -m glm-5.1 --backend zai                             # ZAI (paid, plugin)
agentkthx chat -m gemini-3.8-flash --backend gemini               # Google Gemini (free tier, plugin)
agentkthx chat -m gemma-4-26b-a4b-it --backend gemini              # Gemma via Gemini API (free, generous RPD)
agentkthx chat -m openai/gpt-oss-120b --backend huggingface          # Hugging Face Inference Router (plugin)
agentkthx chat -m openai/gpt-oss-120b --backend hf                  # Same as above, using the `hf` alias
agentkthx chat -m Qwen/Qwen3-4B-Thinking-2507 --backend hf --think   # Reasoning model + chain-of-thought display
agentkthx chat -m deepseek-ai/DeepSeek-R1 --backend hf               # Reasoning model via HF Router

# Plugin management
agentkthx plugins                    # List discovered plugins
```

### Chat Mode Slash Commands

In chat mode, use these slash commands to manage tools, skills, and models mid-session:

```bash
/models              # List all available models (✓ = current, free/paid, chat/non-chat)
/models free chat    # Filter: free-tier + chat-capable models only
/model gemini-3.8-flash   # Switch to a different model
/tools               # List all available tools (✓ = loaded, ○ = available)
/tool shell,read_file,write_file   # Load tools mid-session (comma-separated)
/skills              # List all available skills (✓ = loaded)
/skill codebase-audit  # Load a skill mid-session (appends to system prompt)
/param temperature 0.3   # Set generation parameters
/status              # Show model, backend, tools, skills, memory info
/help                # Show all slash commands
```

### JEV API Mode — System-One Decisions

JEV mode wraps any free chat-capable LLM with a constrained decision prompt,
returning a Jev-compatible envelope `{decision, probability, alternatives}`.
No TypeSafe API key or waitlist required — uses your existing ZAI / OpenRouter /
Gemini / Ollama free models.

```bash
# Classify an email using ZAI free model
agentkthx run "Email subject: 'You won a prize!' — classify as spam/inbox/promotions" \
    --api jev --backend zai --model glm-4.5-flash

# Route a ticket using free OpenRouter model
agentkthx run "Task: calculate 15 * 8 and save to file — route to math/file/general agent" \
    --api jev --backend openrouter --model poolside/laguna-xs-2.1:free

# Local Ollama model making a decision
agentkthx run "Is this a bug or feature request? 'App crashes on startup'" \
    --api jev --backend ollama --model qwen2.5:0.5b

# Google Gemini free tier making a decision
agentkthx run "Should I use Python or Rust for a CLI tool? — classify as python/rust/either" \
    --api jev --backend gemini --model gemini-3.8-flash
```

Output is a JSON decision envelope:

```json
{
  "decision": "spam",
  "probability": 0.92,
  "alternatives": [
    {"value": "promotions", "probability": 0.06},
    {"value": "inbox", "probability": 0.02}
  ]
}
```

See [JEV_API_MODE.md](docs/JEV_API_MODE.md) for the full spec, Python API,
and architecture details.

### Thinking Controls

Two CLI flags give you fine-grained control over thinking-capable models
(GLM-4.5+, OpenAI o-series, deepseek-r1, qwen3 in thinking mode, etc.):

- **`--thinking off|auto|low|medium|high`** — controls model thinking behavior
  - `off` → disable thinking entirely (fastest, recommended for JEV decisions)
  - `auto` → let the model decide (default)
  - `low`/`medium`/`high` → forward `reasoning_effort` to OpenAI-compatible models
- **`--think`** — display the model's `reasoning_content` (chain-of-thought)
  in the CLI output under each step. Off by default.

```bash
# Fast JEV decision — disable thinking entirely
# (drops GLM-4.5-flash latency from ~22s to ~2-3s on trivial classifications)
agentkthx run "Is this spam?" --api jev --backend zai -m glm-4.5-flash --thinking off

# Heavy reasoning — for hard problems where you want maximum thinking
agentkthx run "Prove that the sum of two odds is even." --thinking high

# Inspect what the model was thinking
agentkthx run "What is 15 * 8?" --think

# Combine: heavy thinking + show the chain-of-thought
agentkthx run "Design a load balancer for 10k RPS" --thinking high --think
```

**Python API:**

```python
from agentkthx import Agent
from agentkthx.core.types import parse_thinking_arg

# Parse a level string → (think, reasoning_effort) tuple
think, effort = parse_thinking_arg("high")  # (True, "high")

agent = Agent(
    model="glm-5.1",
    backend="zai",
    thinking_level="high",       # or pass think=True, reasoning_effort="high"
    show_reasoning=True,         # equivalent to --think
)

# Decisions also respect --thinking off (fixes JEV latency)
from agentkthx.backends import get_backend
backend = get_backend("zai", api_mode="jev")
decision = backend.generate_decision(
    model="glm-4.5-flash",
    state="...",
    choices=["a", "b"],
    think=False,  # disable thinking for fast decisions
)
```

### Python API

```python
from agentkthx import Agent
from agentkthx.tools import make_builtin_registry

# Create tools
tools = make_builtin_registry().subset(["calculator", "shell"])

# Create agent
agent = Agent(
    model="qwen2.5:0.5b",
    tools=tools,
    backend="ollama",
)

# Run
result = agent.run("What is 15 * 8?")
print(result.final_answer)
print(f"Completed in {result.total_ms:.0f}ms")
```

### Persistent Memory

```python
from agentkthx import Agent

# Create agent with session persistence
agent = Agent(
    model="qwen2.5:0.5b",
    tools=["calculator"],
    session_id="my-session",  # Enables persistent memory
)

result = agent.run("What is 15 * 8?")
print(result.final_answer)  # "120"

# Later... resume the session
agent2 = Agent(
    model="qwen2.5:0.5b",
    tools=["calculator"],
    session_id="my-session",
)
# Previous conversation is restored from SQLite

# Clean up when done
agent.memory.close()
```

### Dangerous Tool Confirmation

```python
agent = Agent(
    model="qwen2.5:7b",
    tools=["shell", "write_file", "edit_file"],
    confirm_dangerous=lambda tool, args: input(f"Run {tool}? [y/N] ").lower() == "y",
)
```

### JSON Structured Output

```python
agent = Agent(
    model="qwen2.5:0.5b",
    response_format={"type": "json_object"},  # Enables JSON mode, disables tools
)
result = agent.run('Return JSON with keys: "name", "age", "city"')
print(result.final_answer)  # Valid JSON string
```

### TurboQuant Server Management

```python
from agentkthx.plugins.turboquant.turbo import start_server, stop_server, get_status

# Start TurboQuant server with an Ollama model
state = start_server("qwen2.5:7b", ctx=8192)

# Check status
status = get_status()
if status:
    print(f"Running: {status.model_name} on port {status.port}")

# Stop server
stop_server()
```

### OpenRouter Configuration

OpenRouter provides access to 500+ models from Anthropic, OpenAI, Google, Cohere, and other providers.

#### Environment Variables

```bash
export OPENROUTER_API_KEY="your_api_key_here"                    # Required
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"        # Optional (default)
export OPENROUTER_DEFAULT_MODEL="anthropic/claude-3.5-sonnet"    # Optional
export OPENROUTER_FREE_ONLY="1"                                 # Optional (filter to free models)
```

#### Usage Examples

```bash
# Basic usage with free model
agentkthx chat --backend openrouter --model poolside/laguna-xs-2.1:free

# OpenAI models via OpenRouter
agentkthx chat --backend openrouter --model openai/gpt-4o

# Anthropic models via OpenRouter
agentkthx chat --backend openrouter --model anthropic/claude-3.5-sonnet

# Google models via OpenRouter
agentkthx chat --backend openrouter --model google/gemini-flash

# List available models
agentkthx models --backend openrouter

# Free models only
OPENROUTER_FREE_ONLY=1 agentkthx models --backend openrouter
```

### Gemini Configuration

Google Gemini API backend via its OpenAI-compatible endpoint. Provides access to Gemini 3.x, Gemini 2.5, and Gemma 4 chat models. Free tier available (5 RPM / 250,000 TPM / 1,500 RPD on `gemini-3.8-flash`, no credit card required).

#### Environment Variables

```bash
export GEMINI_API_KEY="your_api_key_here"                                  # Required (or GOOGLE_API_KEY)
export GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/"  # Optional (default)
export GEMINI_DEFAULT_MODEL="gemini-3.8-flash"                              # Optional
export GEMINI_FREE_ONLY="1"                                                 # Optional (filter to free-tier models)
export GEMINI_THINKING_LEVEL="minimal"                                      # Optional: minimal|low|medium|high (Gemini 3.x)
export GEMINI_SERVICE_TIER="standard"                                       # Optional: standard|flex|priority
export GEMINI_MAX_429_RETRIES="6"                                           # Optional (default 6, 5s→90s backoff)
```

`GEMINI_API_KEY` is the documented env var. `GOOGLE_API_KEY` is accepted as a fallback (mirrors Google's SDK precedence).

#### Usage Examples

```bash
# Basic usage with free-tier model
agentkthx chat --backend gemini --model gemini-3.8-flash --tools calculator

# Gemini 2.5 (can fully disable thinking)
agentkthx chat --backend gemini --model gemini-2.5-flash

# Gemma 4 open-source model (uses inline <thought>...</thought> tags for reasoning)
agentkthx chat --backend gemini --model gemma-4-26b-a4b-it --think

# List available models (10-model static catalog with free-tier markers)
agentkthx models --backend gemini

# Free-tier models only
GEMINI_FREE_ONLY=1 agentkthx models --backend gemini
```

See [docs/GEMINI_API_TECHNICAL_REFERENCE.md](docs/GEMINI_API_TECHNICAL_REFERENCE.md) for the full 11-section reference (auth, models, function calling, streaming, error codes, rate limits, multimodal, thinking config, integration notes, troubleshooting, 71-model catalog with free-tier data transcribed from Google AI Studio).

### Hugging Face Configuration

Hugging Face Inference Router backend (`https://router.huggingface.co/v1`) — proxies 100+ open-weight models (Llama, Qwen, DeepSeek, Mistral, Gemma, GLM, Phi, Command-R, gpt-oss) served by ~18 partner providers (Together, Groq, Novita, DeepInfra, Fireworks, Cerebras, Replicate, Fal AI, Featherless, Baseten, Cohere, Nscale, OVHcloud, Public AI, Scaleway, WaveSpeedAI, Z.ai, HF Inference) through a single OpenAI-compatible `/chat/completions` endpoint. Unlike OpenRouter, HF doesn't use a `:free` model-id suffix — free-tier status is determined by account credit + provider routing, so `HF_FREE_ONLY` enforces a curated 31-model whitelist.

#### Environment Variables

```bash
export HF_TOKEN="hf_your_fine_grained_token"     # Required (or HUGGING_FACE_HUB_TOKEN)
export HF_BASE_URL="https://router.huggingface.co/v1"  # Optional (default — Inference Router)
export HF_DEFAULT_MODEL="openai/gpt-oss-120b"    # Optional
export HF_FREE_ONLY="1"                           # Optional (strict 31-model whitelist)
export HF_FREE_FALLBACK_MODEL="Qwen/Qwen2.5-7B-Instruct-1M"  # Optional (swap on HTTP 402)
export HF_PROVIDER_POLICY="cheapest"              # Optional: "" | fastest | cheapest | preferred | <partner-name>
```

If `HF_FREE_ONLY` is **unset**, the backend probes `https://huggingface.co/api/whoami-v2` on init and auto-detects free-tier users (`canPay=false`) — auto-enables the whitelist + emits a one-time stderr warning. Explicit `HF_FREE_ONLY=false` opts out (no auto-detect, no warning).

#### Provider Routing

The router supports a suffix on the model id to control which partner provider serves the request:

- **No suffix** (default) — `:fastest` (highest throughput, router default)
- **`:cheapest`** — lowest price per output token (auto-applied when `HF_FREE_ONLY=true`)
- **`:preferred`** — your preference order at huggingface.co/settings/inference-providers
- **`:<partner-name>`** — pin to a specific partner (`:groq`, `:together`, `:novita`, `:deepinfra`, `:fireworks`, `:cerebras`, etc.)

```bash
# Default routing (fastest)
agentkthx chat --backend hf --model openai/gpt-oss-120b

# Pin to Groq (very fast for Llama-3.x)
agentkthx chat --backend hf --model meta-llama/Llama-3.3-70B-Instruct:groq

# Pin to Together (better for Qwen3-Coder)
agentkthx chat --backend hf --model Qwen/Qwen3-Coder-480B-A35B-Instruct:together
```

#### Usage Examples

```bash
# Basic usage — openai/gpt-oss-120b is the default model
agentkthx chat --backend hf

# Reasoning model with chain-of-thought display
agentkthx chat --backend hf -m Qwen/Qwen3-4B-Thinking-2507 --stream --think

# DeepSeek-R1 for hard reasoning tasks
agentkthx chat --backend hf -m deepseek-ai/DeepSeek-R1 --stream --think

# List available models (live /v1/models + 31-model static catalog fallback)
agentkthx models --backend hf

# Free-tier whitelist only (31 models, auto-appends :cheapest suffix)
HF_FREE_ONLY=1 agentkthx models --backend hf

# Pin to a specific partner provider
agentkthx chat --backend hf -m "meta-llama/Llama-3.3-70B-Instruct:groq"
```

See [docs/HUGGINGFACE_API_TECHNICAL_REFERENCE.md](docs/HUGGINGFACE_API_TECHNICAL_REFERENCE.md) for the full 14-section reference (auth, request/response, sampling params, model catalog, function calling, streaming, provider routing, error codes, rate limits, free-tier behavior, multimodal, implementation notes, proposed plugin.json, troubleshooting matrix).

### Chat-Completions Streaming

```python
from agentkthx.backends import get_backend
from agentkthx.core.types import ApiMode

# Use Chat-Completions mode with streaming
backend = get_backend("ollama", api_mode=ApiMode.OPENAI)

for chunk in backend.generate_completions_stream(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Hello!"}],
    response_format={"type": "json_object"}
):
    print(chunk["delta"], end="", flush=True)
```

### JEV Decision Mode

```python
from agentkthx.backends import get_backend

# Get a JEV-mode backend (uses any free LLM underneath)
backend = get_backend("zai", api_mode="jev")

# Make a structured decision
decision = backend.generate_decision(
    model="glm-4.5-flash",
    state="Email from unknown@xyz.com — subject: 'You won a prize!'",
    choices=["spam", "inbox", "promotions"],
    question="Where should this email be routed?",
)

print(decision["decision"])      # "spam"
print(decision["probability"])   # 0.92
print(decision["alternatives"])  # [{"value": "promotions", "probability": 0.06}, ...]
print(decision["usage"])         # {"input_tokens": 100, "output_tokens": 20, ...}
```

### Skill License Validation

```python
from agentkthx.skills import validate_spdx_license, parse_compatibility

# Validate SPDX license identifier
valid, msg = validate_spdx_license("MIT")  # (True, "Valid SPDX identifier: MIT")
valid, msg = validate_spdx_license("Custom")  # (False, "Unknown license...")

# Parse compatibility requirements
compat = parse_compatibility("python>=3.8, ollama")
# Returns: {"python": ">=3.8", "runtimes": ["ollama"], "frameworks": []}
```

### Multi-Agent Orchestration

```python
from agentkthx import Agent, Orchestrator, AgentCard

orchestrator = Orchestrator(mode="router")

# Register specialized agents
orchestrator.register(AgentCard(
    name="math_agent",
    description="Handles mathematical calculations",
    capabilities=["calculate", "math", "compute"],
    tools=["calculator"],
))

orchestrator.register(AgentCard(
    name="file_agent",
    description="Handles file operations",
    capabilities=["read", "write", "file"],
    tools=["read_file", "write_file"],
))

# Route tasks to appropriate agent
result = orchestrator.run("Calculate 15 * 8 and save to file")
```

## Tool Support Levels

AgentKthx supports three levels of tool use:

1. **Native** — Models with built-in function calling (qwen2.5, llama3.1+, mistral, granite, functiongemma)
2. **ReAct** — Text-based tool use via reasoning prompts (qwen2.5-coder, qwen3)
3. **None** — Pure reasoning without tools

Tool support is auto-detected by running `agentkthx models --tool-support`. Results are cached in `~/.cache/agentkthx/tool_support.json`.

```bash
# Test and cache tool support for all models
agentkthx models --tool-support

# Re-test (ignore cache)
agentkthx models --tool-support --no-cache
```

You can also force ReAct mode:

```python
agent = Agent(model="qwen2.5:0.5b", force_react=True)
```

```bash
# Force ReAct mode via CLI
agentkthx run "What is 15 * 8?" --tools calculator --force-react
```

## Model Families

Configured model families with optimized prompts:

- **qwen2.5** — Native tool support, excellent performance
- **llama3.1/3.2/3.3** — Native tool support
- **mistral/mixtral** — Native tool support
- **gemma2/gemma3** — ReAct mode, special prompting
- **granite/granitemoe** — Native tool support
- **phi3** — Native tool support
- **deepseek** — Native with `<think/>` tag handling
- **qwen3** — ReAct mode, thinking model (auto think=False)
- **qwen3.5** — Native on OpenAI, ReAct on OpenResponses
- **glm (ZAI)** — Native tool support via ZAI cloud API (GLM 4.5/4.6/4.7/5/5.1)
- **dolphin** — ReAct mode

## Security Features

Built-in security for safe operation, with a runtime-toggleable mode:

- **Two security modes** — `max` (default, all checks enabled) and `off` (all checks disabled)
- **Toggle at runtime** — `/security max` or `/security off` in chat mode, or `--security off` at startup
- **Command blocklist** — Blocks dangerous shell commands (rm, sudo, etc.)
- **Path validation** — Prevents access to sensitive directories
- **SSRF protection** — Blocks requests to local/internal URLs
- **Injection detection** — Detects shell injection patterns (`&&`, `||`, `|`, `;`, `$()`, backticks, etc.)
- **Dangerous tool confirmation** — `--confirm` flag requires interactive approval before shell, write, or edit operations
- **Audit logging** — Shell, write, and edit operations logged to `~/.agentkthx/audit.log`
- **Response size limits** — Files capped at 512KB, HTTP responses at 256KB

```bash
# Default — all security checks enabled
agentkthx chat --backend openrouter --tools shell,python_repl

# Disable all security checks (use with caution — model can run any command)
agentkthx chat --backend openrouter --tools shell,python_repl --security off
```

In chat mode, toggle at runtime:
```
/security              — show current mode
/security max          — enable all checks (default)
/security off          — disable ALL checks (model can run any command,
                        read/write any path, fetch any URL)
```

**Warning**: `--security off` disables ALL safety checks. Only use when you trust the model and need unrestricted access (e.g., local dev with a fine-tuned model that legitimately uses `&&`, `|`, etc.).

## Configuration

Environment variables:

```bash
# Backend URLs and per-backend settings
OLLAMA_BASE_URL=https://your-ollama-server.com    # Default: http://localhost:11434
LLAMA_SERVER_BASE_URL=http://localhost:8764     # llama-server URL (default: 8764)

# BitNet plugin
BITNET_BASE_URL=http://localhost:8765              # BitNet server URL
BITNET_TUNNEL=https://your-tunnel.com              # Alternative BitNet URL

# ZAI plugin
ZAI_BASE_URL=https://api.z.ai                    # ZAI API endpoint
ZAI_API_KEY=sk-...                                # ZAI API key (required)
ZAI_FREE_ONLY=true                               # Restrict to free models only
ZAI_FREE_FALLBACK_MODEL=glm-4.5-flash            # Fallback when credits run out

# Gemini plugin
GEMINI_API_KEY=...                                # Required (or GOOGLE_API_KEY)
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  # Optional (default)
GEMINI_DEFAULT_MODEL=gemini-3.8-flash             # Optional
GEMINI_FREE_ONLY=1                                # Optional (filter to free-tier models)
GEMINI_THINKING_LEVEL=minimal                     # Optional: minimal|low|medium|high (Gemini 3.x)
GEMINI_SERVICE_TIER=standard                      # Optional: standard|flex|priority
GEMINI_MAX_429_RETRIES=6                          # Optional (default 6, 5s→90s backoff)

# Hugging Face plugin
HF_TOKEN=hf_...                                 # Required (or HUGGING_FACE_HUB_TOKEN)
HF_BASE_URL=https://router.huggingface.co/v1     # Optional (default — Inference Router)
HF_BASE_URL_LEGACY=https://api-inference.huggingface.co  # Legacy Serverless TGI surface (not used by v0.1)
HF_DEFAULT_MODEL=openai/gpt-oss-120b             # Optional
HF_FREE_ONLY=true                               # Strict free-tier whitelist (31 models)
HF_FREE_FALLBACK_MODEL=Qwen/Qwen2.5-7B-Instruct-1M  # Swap on HTTP 402 when HF_FREE_ONLY=false
HF_PROVIDER_POLICY=cheapest                     # Optional: "" | fastest | cheapest | preferred | <partner-name>
                                                # When HF_FREE_ONLY=true, policy is forced to :cheapest

# ACP plugin
ACP_BASE_URL=http://localhost:8766                 # ACP server URL

# TurboQuant plugin
TURBOQUANT_SERVER_PATH=llama-server                # llama-server binary path
TURBOQUANT_PORT=8764                               # TurboQuant server port
TURBOQUANT_CTX=8192                                # Context window size

# Agent settings
AGENTKTHX_BACKEND=ollama      # Default backend: ollama, llama-server, bitnet, zai, gemini, ...
AGENTKTHX_MODEL=qwen2.5:0.5b  # Default model
AGENTKTHX_MAX_STEPS=10        # Maximum reasoning steps
AGENTKTHX_DEBUG=false         # Enable debug output

# Retry settings
AGENTKTHX_RETRY_ON_ERROR=true          # Retry failed tool calls with error feedback
AGENTKTHX_MAX_TOOL_RETRIES=2           # Maximum retries per tool call failure
```

Check current configuration:
```bash
agentkthx config
agentkthx config --urls  # Show only URLs
```

### CLI Options (run, chat, agent)

| Option | Description |
|--------|-------------|
| `--api openre\|openai\|jev` | API mode: OpenResponses (default), OpenAI Chat-Completions, or JEV (System-One decisions via any LLM) |
| `--thinking off\|auto\|low\|medium\|high` | Thinking / reasoning effort: `off` (fastest, recommended for JEV), `auto` (default), or `low`/`medium`/`high` (forwarded as `reasoning_effort` for thinking-capable models) |
| `--think` | Display reasoning_content (chain-of-thought) in CLI output when the model emits it. Off by default. |
| `--response-format text\|json` | Response format (Chat-Completions mode) |
| `--truncation auto\|disabled` | Truncation behavior for long responses |
| `--soul <path>` | Load Soul Spec persona package |
| `--soul-level 1-3` | Progressive disclosure level |
| `--num-ctx <tokens>` | Context window size (default: 4096) |
| `--timeout <seconds>` | Request timeout (default: 120) |
| `--acp` | Enable ACP (Agent Control Panel) logging |
| `--acp-url <url>` | ACP server URL |
| `--confirm` | Require y/N confirmation before dangerous tools (shell, write_file, edit_file) |
| `--session <name>` | Resume or create a persistent memory session |
| `--force-react` | Force ReAct text-based tool calling (skip native tool detection) |
| `--num-predict <tokens>` | Maximum tokens to generate |
| `--stream` | Stream output in real-time |
| `-q, --quiet` | Suppress header and summary output |
| `-v, --verbose` | Verbose output |
| `--no-retry` | Disable retry-with-error-feedback on tool failures |
| `--max-retries N` | Maximum retries per tool call failure (default: 2) |
| `--security max\|off` | Security mode: `max` (default, all checks) or `off` (disable all checks) |

## LocalClaw Redirect

The `localclaw` command is provided for backward compatibility:

```bash
# Both work identically
localclaw run "What is 2+2?"
agentkthx run "What is 2+2?"
```

## Tests & Examples

AgentKthx includes a comprehensive suite of tests for validating agent capabilities across reasoning, knowledge, and tool usage:

```bash
# Basic agent test (no tools)
python -m agentkthx.examples.00_basic_agent

# Quick 5-question diagnostic
python -m agentkthx.examples.01_quick_diagnostic

# Tool usage tests (calculator, shell, datetime, file, python_repl)
python -m agentkthx.examples.02_tool_test

# Logic and reasoning tests (BBH-style)
python -m agentkthx.examples.03_reasoning_test

# GSM8K math benchmark (50 questions)
python -m agentkthx.examples.04_gsm8k_benchmark

# Common sense reasoning (BIG-bench)
python -m agentkthx.examples.05_common_sense

# Causal reasoning (BIG-bench)
python -m agentkthx.examples.06_causal_reasoning

# Logical deduction (BIG-bench)
python -m agentkthx.examples.07_logical_deduction

# Reading comprehension
python -m agentkthx.examples.08_reading_comprehension

# General knowledge (BIG-bench)
python -m agentkthx.examples.09_general_knowledge

# Implicit reasoning
python -m agentkthx.examples.10_implicit_reasoning

# Analogical reasoning
python -m agentkthx.examples.11_analogical_reasoning
```

### Test Categories

| Test | Questions | Focus |
|------|-----------|-------|
| Basic Agent | 1 | Single prompt, no tools |
| Quick Diagnostic | 5 | Calculator tool, multi-step reasoning |
| Tool Test | 10 | Calculator, shell, datetime, file, python_repl tools |
| Reasoning Test | 14 | Logic, deduction, patterns, spatial |
| GSM8K Benchmark | 50 | Math word problems |
| Common Sense | 25 | Physical properties, everyday reasoning |
| Causal Reasoning | 25 | Cause and effect relationships |
| Logical Deduction | 25 | Formal logic puzzles |
| Reading Comprehension | 25 | Passage-based Q&A |
| General Knowledge | 25 | Science, history, geography |
| Implicit Reasoning | 25 | Unstated assumptions and inference |
| Analogical Reasoning | 25 | Pattern matching and analogies |

### Benchmark Results (Quick Diagnostic)

| Model | Score | Time | Tool Support |
|-------|-------|------|-------------|
| functiongemma:270m | 5/5 (100%) | ~20s | native |
| granite4:350m | 5/5 (100%) | ~50s | native |
| qwen2.5:0.5b | 5/5 (100%) | 38s | native |
| qwen2.5-coder:0.5b | 5/5 (100%) | 93s | native |
| qwen3:0.6b | 5/5 (100%) | 70s | react |
| deepseek-r1:1.5b | 5/5 (100%) | ~305s | native |

All tested models achieve 100% on the Quick Diagnostic. Native models are ~2x faster than ReAct models due to direct API tool calling.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run unit tests
pytest

# Format code
black agentkthx
ruff check agentkthx
```

## License

MIT License - See LICENSE file for details.

## Author

**VTSTech** — [https://www.vts-tech.org](https://www.vts-tech.org)

## Contributing

Contributions welcome!

## Changelog

See [docs/CHANGELOG.md](https://github.com/VTSTech/AgentKthx/blob/main/docs/CHANGELOG.md) for detailed version history and release notes.

## Migration from AgentNova

R06.0 renamed the project from `AgentNova` → `AgentKthx`. The rename was driven by name collisions in the AI agent space (multiple projects, Instagram accounts, etc. were using the AgentNova name).

### What changed

| Old | New |
|-----|-----|
| PyPI package: `agentnova` | `agentkthx` |
| CLI command: `agentnova` | `agentkthx` |
| Python import: `import agentnova` | `import agentkthx` |
| GitHub repo: `VTSTech/AgentNova` | `VTSTech/AgentKthx` |

### What stays the same (backward compat)

- ✅ The `agentnova` CLI command still works (redirects to `agentkthx`)
- ✅ `import agentnova` still works (re-exports from `agentkthx`, emits DeprecationWarning)
- ✅ All `AGENTKTHX_*` env vars still work unchanged
- ✅ `localclaw` CLI command still works (redirects through to `agentkthx`)
- ✅ All existing skills, souls, plugins, and configs continue to work
- ✅ SQLite persistent memory sessions remain compatible

### Migration steps (recommended but not required)

```bash
# Uninstall old package (optional — both can coexist)
pip uninstall agentnova

# Install new package
pip install agentkthx

# Update your scripts (optional — old imports still work with a warning)
# Old: from agentnova import Agent
# New: from agentkthx import Agent
```

### Why "AgentKthx"?

The name honors the IRC-era slang "kthx" (OK, thanks) — a callback to early internet culture. It's distinctive, memorable, and (most importantly) completely unused by any other AI agent project as of September 2026. The CLI binary `agentkthx` is short and easy to type.
