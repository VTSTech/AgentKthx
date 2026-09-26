# CREDITS.md

AgentKthx is built upon the work of many open-source projects, model creators, API designers, and community specifications. This document acknowledges every project, inspiration, API, model creator, and specification that makes AgentKthx possible.

---

## Author

**VTSTech (Nigel Todman)** — [https://www.vts-tech.org](https://www.vts-tech.org) · [GitHub](https://github.com/VTSTech)

Licensed under the **MIT License**. Copyright (c) 2026 Nigel Todman.

---

## Inference Backends

These projects provide the runtime environments that AgentKthx connects to for local LLM inference.

| Project | Description | Link |
|---------|-------------|------|
| **Ollama** | Local LLM inference server supporting a vast ecosystem of open-weight models with native tool calling and OpenAI-compatible APIs | [https://ollama.com](https://ollama.com) |
| **BitNet** | Microsoft's 1-bit quantized LLM inference engine optimized for extreme CPU efficiency | [https://github.com/microsoft/BitNet](https://github.com/microsoft/BitNet) |
| **llama.cpp (TurboQuant)** | CPU-optimized LLM inference server with TurboQuant KV cache compression, enabling sub-4K context workloads with minimal memory. AgentKthx's `turbo` subcommand discovers Ollama models and manages the llama-server lifecycle. | [https://github.com/TheTom/llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant) |
| **ZAI** | Cloud-hosted LLM inference API providing GLM series models with an OpenAI Chat-Completions compatible endpoint. Offers free-tier models (GLM-4.5-Flash, GLM-5.3-Flash) and paid models with up to 1M context. AgentKthx includes free-only mode and automatic credit-exhaustion fallback. | [https://api.z.ai](https://api.z.ai) |
| **OpenRouter** | Cloud aggregator providing access to 500+ models from Anthropic, OpenAI, Google, Cohere, and more via an OpenAI-compatible API. AgentKthx supports free-only filtering, 429 retry with `Retry-After` honoring, and context-safe `max_tokens` persistence. | [https://openrouter.ai](https://openrouter.ai) |
| **Google Gemini** | Cloud-hosted LLM inference API providing Gemini 3.x, Gemini 2.5, and Gemma 4 models via its OpenAI-compatible endpoint. Free tier (5 RPM / 250K TPM / 1500 RPD on gemini-3.8-flash). Includes `GEMINI_FREE_ONLY` filter, thinking config routing, and 429 RESOURCE_EXHAUSTED retry. | [https://ai.google.dev](https://ai.google.dev) |
| **Hugging Face Inference Router** | Unified proxy exposing 100+ open-weight models (Llama, Qwen, DeepSeek, Mistral, Gemma, GLM, Phi, Command-R, gpt-oss) served by ~18 partner providers (Together, Groq, Novita, DeepInfra, Fireworks, Cerebras, Replicate, Fal AI, Featherless, Baseten, Cohere, Nscale, OVHcloud, Public AI, Scaleway, WaveSpeedAI, Z.ai, HF Inference) through a single OpenAI-compatible `/chat/completions` endpoint at `router.huggingface.co/v1`. AgentKthx's `huggingface` backend (R07.02) ships with `HF_FREE_ONLY` whitelist enforcement (31 open-weight models), per-provider `is_free` auto-detection, `whoami-v2` free-tier probe (auto-enables protection for `canPay=false` accounts), provider-routing suffix support (`:fastest` / `:cheapest` / `:preferred` / `:<partner-name>`), and HTTP 402 credit-exhaustion fallback to `HF_FREE_FALLBACK_MODEL`. Free-tier credit is $0.10/mo at partner provider rates (no per-model `is_free=true` flag exists in the API as of Sept 2026 — the free-tier model is purely credit-based). | [https://huggingface.co](https://huggingface.co) |

---

## Architectural Inspirations

AgentKthx draws design philosophy and patterns from several influential projects and research.

### OpenClaw

AgentKthx was **inspired by the architecture of OpenClaw**, rebuilt from scratch for local-first operation. The original OpenClaw project served as the conceptual foundation upon which AgentKthx was designed, with a focus on stripping away cloud dependencies and enabling fully local agentic workflows.

### ReAct (Reasoning + Acting)

The core agentic loop in AgentKthx implements the **ReAct paradigm** — a framework where language models interleave reasoning traces (Thought) with action execution (Action/Action Input), observe results (Observation), and produce final answers. This pattern, introduced in the paper *"ReAct: Synergizing Reasoning and Acting in Language Models"* (Yao et al., 2022), is the backbone of how AgentKthx orchestrates tool-calling with models that lack native function calling support. The unified ReAct prompting system, enhanced observation formatting, and few-shot examples for small models all stem from this research.

### ATLAS-Autonomous

The **retry-with-error-feedback** feature introduced in R04.1 was inspired by the [ATLAS-Autonomous](https://github.com/itigges22/ATLAS) benchmark infrastructure. The ATLAS project demonstrated that giving models a chance to correct failed tool calls before the agent gives up significantly improves success rates on error-prone tasks. AgentKthx adopted this concept, implementing it as configurable retry context injection that works across both native tool-calling and ReAct text-based paths.

### Pi Coding Agent

The **Pi Coding Agent** ([pi-mono](https://github.com/pi-mono)) is a coding-focused AI assistant that demonstrated the practical value of cloud-hosted LLM APIs — specifically the ZAI platform — for agentic workflows. Experience building with and using the Pi Coding Agent on ZAI inspired the addition of cloud backend support in AgentKthx R04.6, marking the project's first step beyond purely local inference. The ZAI backend's design — OpenAI Chat-Completions compatibility, Bearer token authentication, free-tier awareness, and credit-exhaustion fallback — draws directly from lessons learned working with ZAI through the Pi Coding Agent ecosystem.

---

## Development History

AgentKthx's origins are a testament to the power of rapid, iterative AI-assisted development — or as the kids call it, **vibe coding**.

### LocalClaw Era — Vibe Coding with DeepSeek and Gemini

The project began life as **LocalClaw**, a local-first reimagining of the OpenClaw agentic framework. In its earliest iterations, LocalClaw was written through an unconventional development workflow: **vibe coding sessions** with **DeepSeek** and **Google Gemini** acting as pair-programming assistants. Rather than writing detailed specifications and planning documents first, the approach was conversational and exploratory — describing the desired behavior in natural language, iterating rapidly on generated code, and refining through hands-on testing against real local models running on Ollama.

This vibe coding process proved remarkably effective for a project of this nature. The agentic loop, tool-calling infrastructure, ReAct prompt parsing, security layer, and multi-backend architecture all emerged from these freeform coding sessions. DeepSeek excelled at generating the initial scaffolding and handling the more complex algorithmic components like the tool parser and error recovery system, while Gemini contributed to architectural decisions, the spec compliance layer, and the soul/skills system design. The result was a working agentic framework that, despite its unconventional genesis, maintained clean code quality, comprehensive test coverage, and strong architectural principles throughout.

### The Pivot to AgentKthx R00 — GLM-5 and Super-Z

By the time the project reached its **R00 release milestone** and was renamed from LocalClaw to **AgentKthx**, the development workflow shifted exclusively to **GLM-5** and its agentic platform persona, **Super-Z**. This transition marked a significant evolution in both the project's identity and its development methodology. Where the earlier vibe coding sessions were exploratory and multi-model, the AgentKthx era brought a focused, sustained development relationship with a single AI pair-programmer that understood the full project context, architectural decisions, and design philosophy.

Under the GLM-5/Super-Z collaboration, the project underwent substantial maturation: the OpenResponses specification was implemented with full compliance, the ACP integration reached v1.0.5, the Soul Spec v0.5 persona system was designed and shipped, the AgentSkills packaging format was defined, and the multi-agent orchestrator with router, pipeline, and parallel modes was built out. The comprehensive test suite — spanning reasoning benchmarks, tool-calling validation, security audits, and spec compliance checks — was also largely developed and maintained through this partnership.

The progression from LocalClaw's freewheeling vibe coding origins to AgentKthx's disciplined development under GLM-5 demonstrates that AI-assisted development can scale from rapid prototyping to production-grade software engineering. The entire codebase — thousands of lines of carefully structured Python with zero external dependencies, 800+ tests, and multi-spec compliance — stands as evidence that vibe coding is not just a meme but a legitimate and powerful development methodology when paired with the right AI collaborator.

---

## Specifications & Standards

AgentKthx implements and complies with several open specifications and standards. These define how the framework handles API interactions, tool calling, persona management, agent monitoring, and skill packaging.

| Specification | Version | Compliance | Description | Link |
|---------------|:-------:|:----------:|-------------|------|
| **OpenResponses** | 1.0 | 100% | Multi-provider interoperable LLM interface defining items (MessageItem, FunctionCallItem, FunctionCallOutputItem, ReasoningItem), response state machines (queued, in_progress, completed, failed, incomplete, cancelled), tool_choice modes (auto, required, none, specific, allowed_tools), and SSE streaming events | [https://www.openresponses.org/specification](https://www.openresponses.org/specification) |
| **OpenAI Chat Completions API** | — | Full | OpenAI-compatible endpoint format (`/v1/chat/completions`) for cross-platform tool integration. Supports tool_choice, response_format, logprobs, streaming (SSE), and extended parameters (stop, presence_penalty, frequency_penalty, top_p) | [OpenAI API Reference](https://platform.openai.com/docs/api-reference/chat) |
| **Soul Spec** | v0.5 | 100% | ClawSouls specification for persona packages (soul.json manifests, SOUL.md/IDENTITY.md/STYLE.md files, progressive disclosure levels 1-3, calibration examples, embodied agent support) | [https://github.com/clawsouls/soulspec](https://github.com/clawsouls/soulspec) |
| **ACP** | v1.0.6 | Full (mandatory requirements, hints, orphan handling, nudge support, batch ops, shutdown, A2A, JSON-RPC 2.0, primary agent nudge delivery) | Agent Control Panel specification for monitoring, activity logging (READ, WRITE, EDIT, BASH, SEARCH, API, A2A), STOP flag handling, agent-to-agent communication, and health tracking | [VTSTech/ACP-Agent-Control-Panel](https://github.com/VTSTech/ACP-Agent-Control-Panel) |
| **AgentSkills** | — | 100% | Skill packaging specification defining SKILL.md with YAML frontmatter (name, description, license, compatibility, allowed-tools), scripts/references/assets directories, SPDX license validation, and environment compatibility checking | [https://agentskills.io/](https://agentskills.io/) |
| **JSON-RPC 2.0** | 2.0 | Full | Remote procedure call protocol used for A2A (Agent-to-Agent) messaging between agents and the ACP server | [https://www.jsonrpc.org/specification](https://www.jsonrpc.org/specification) |
| **Server-Sent Events (SSE)** | — | Full | Streaming protocol for real-time delivery of OpenResponses lifecycle events (response.queued, response.in_progress, response.output_item.added, response.output_text.delta, etc.) | [W3C Specification](https://html.spec.whatwg.org/multipage/server-sent-events.html) |
| **SPDX License Identifiers** | 3.x | Subset | Standardized license identifier format used for skill license validation. AgentKthx validates skill licenses against a curated set of common SPDX identifiers (MIT, Apache-2.0, GPL-3.0, BSD-3-Clause, etc.) including WITH exception support (e.g., "Apache-2.0 WITH LLVM-exception") | [https://spdx.org/licenses/](https://spdx.org/licenses/) |
| **IANA Time Zone Database** | — | Used | Standardized timezone identifiers (e.g., "America/New_York", "Europe/London", "UTC") used by the `get_time` tool via Python's `zoneinfo` module | [IANA Time Zone Database](https://www.iana.org/time-zones) |
| **Keep a Changelog** | 1.0.0 | Used | Changelog formatting standard used for CHANGELOG.md | [https://keepachangelog.com/en/1.0.0/](https://keepachangelog.com/en/1.0.0/) |

---

## Model Creators & Supported Models

AgentKthx is optimized to run efficiently on small, local LLMs. The framework includes family-specific configurations (prompting style, stop tokens, tool format, temperature defaults, thinking mode handling) for the following model families and their creators.

### Alibaba Cloud — Qwen Series

The **Qwen** family from Alibaba Cloud is the most extensively configured and tested model family in AgentKthx. Multiple model sizes and variants are supported with native tool calling.

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **Qwen 2.5** (`qwen2.5`) | 0.5B – 72B | Native (XML) | ChatML format (`<\|im_start\|>`/`<\|im_end\|>`). Primary development target. Excellent performance at all sizes. |
| **Qwen 2.5 Coder** (`qwen2.5-coder`) | 0.5B – 32B | ReAct | Same family as Qwen 2.5 but different template — uses ReAct text parsing instead of native tool calling. |
| **Qwen 3** (`qwen3`) | 0.6B – 235B | Native (openre) / ReAct (openai) | Extended thinking mode with `<think/>` tags. Native tool calling on openre only; ReAct on openai. AgentKthx sends `/no_think` directive by default for tool-calling workflows. |
| **Qwen 3.5** (`qwen35`) | 0.8B – TBD | Native (openai) / ReAct (openre) | Successor to Qwen 3. Native tool calling on openai only; ReAct text parsing on openre. Does NOT have thinking mode (simpler template than Qwen 3). |
| **Qwen 2** (`qwen2`) | 0.5B – 72B | ReAct | Earlier generation. ReAct text parsing (no native tool calling on either API mode). |
| **Qwen** (base) | 0.5B – 7B | ReAct | Base Qwen model. Falls back to ReAct text parsing. |
| **Qwen 1.8** (`qwen:1.8b`) | 1.8B | ReAct | Largest base Qwen tested. ReAct text parsing on both API modes. |

### Meta AI — LLaMA Series

Meta's **LLaMA** family provides strong general-purpose performance with native tool calling support.

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **LLaMA 3.3** | 70B | Native | Latest LLaMA generation. Full native tool support. |
| **LLaMA 3.2** | 1B – 3B | Native | Smaller variants ideal for local deployment. |
| **LLaMA 3.1** | 8B – 405B | Native | Extended context (131K tokens). |
| **LLaMA 3** | 8B – 70B | Native | Original LLaMA 3 release. |

### Google — Gemma & FunctionGemma

Google's **Gemma** models offer compact, efficient inference with specific handling requirements.

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **Gemma 3** (`gemma3`) | 270M – 27B | None (ReAct) | Requires first-user system prompt style. No native tool calling — AgentKthx uses ReAct prompting. Special `no_tools_system_prompt` for pure reasoning. |
| **Gemma 2** (`gemma2`) | 2B – 27B | None (ReAct) | Similar behavior to Gemma 3. |
| **FunctionGemma** | 270M | Native | Specialized function-calling variant. Native tool support on both openre and openai API modes. |

### IBM — Granite Series

IBM's **Granite** models are well-suited for enterprise and tool-calling tasks.

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **Granite 4** (`granite4`) | 350M | Native (XML) | Excellent tool-calling performance. Native on both openre and openai. Achieved 100% on diagnostic benchmarks at just 350M parameters. Best sub-500M model tested (88% on tool tests). |
| **Granite 3.1 MoE** (`granitemoe`) | 1B (active) | Native (XML) | Mixture-of-Experts architecture. Has known schema dump issue and JSON truncation — AgentKthx includes specific workarounds. |
| **Granite** (base) | Various | Native (XML) | Base Granite family with IBM-specific role token format (`<\|start_of_role\|>`). |

### DeepSeek

**DeepSeek** provides both standard and reasoning-specialized models.

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **DeepSeek-R1** (`deepseek-r1`) | 1.5B – 671B | Native | Reasoning model with extended thinking via `<think/>` tags. AgentKthx strips think tags by default. |
| **DeepSeek** (standard) | Various | Native | Standard DeepSeek models including coder variants. |
| **DeepSeek Coder** | 1.3B – 33B | Varies | Code-specialized models. Tool support depends on specific variant. |

### Mistral AI — Mistral & Mixtral

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **Mistral** | 7B | Native | Full native tool support. |
| **Mixtral** | 8x7B (47B total) | Native | Mixture-of-Experts model with native tools. |

### Microsoft — Phi

| Model | Parameters | Tool Support | Notes |
|-------|:----------:|:------------:|-------|
| **Phi 3** (`phi3`) | 3.8B – 14B | Native | Microsoft's compact model. Large context window (128K tokens). |

### Zhipu AI — GLM Series (ZAI)

**Zhipu AI**'s **GLM** models are accessed through the ZAI cloud API. AgentKthx supports the full GLM lineup with native tool calling via the OpenAI Chat-Completions compatible endpoint.

| Model | Parameters | Tool Support | Pricing | Notes |
|-------|:----------:|:------------:|---------|-------|
| **GLM 5.1** | TBD | Native | $1.40/$4.40 | Latest GLM generation. Default model for ZAI backend. |
| **GLM 5 Turbo** | TBD | Native | $1.20/$4.00 | Optimized for speed. |
| **GLM 5** | TBD | Native | $1.00/$3.20 | Standard GLM 5 generation. |
| **GLM 4.7** | TBD | Native | $0.60/$2.20 | |
| **GLM 4.7 Flash** | TBD | Native | Free | Free-tier model. 128K context. |
| **GLM 4.7 FlashX** | TBD | Native | $0.07/$0.40 | Low-cost variant. |
| **GLM 4.6** | TBD | Native | $0.60/$2.20 | |
| **GLM 4.5 X** | TBD | Native | $2.20/$8.90 | Highest capability GLM 4.5 variant. |
| **GLM 4.5 AirX** | TBD | Native | $1.10/$4.50 | |
| **GLM 4.5 Air** | TBD | Native | $0.20/$1.10 | |
| **GLM 4.5 Flash** | TBD | Native | Free | Free-tier model. 128K context. Default free fallback. |
| **GLM 4.5** | TBD | Native | $0.60/$2.20 | Standard GLM 4.5. |
| **GLM 4 32B** | 32B | Native | $0.10/$0.10 | Legacy model. 128K context window. |

Pricing shown as input/output per 1M tokens. Free models require no credits. AgentKthx auto-detects insufficient credits (HTTP 429, error code 1113) and falls back to a free model.

### Community Fine-tunes

| Model | Base | Creator | Notes |
|-------|------|---------|-------|
| **Dolphin 3.0** (Qwen2.5) | 500M | [nchapman](https://huggingface.co/nchapman) | Dolphin fine-tune on Qwen 2.5 base. ChatML format. ReAct text parsing. |
| **Dolphin 3.0** (LLaMA 3) | 1B | nchapman | Dolphin fine-tune on LLaMA 3. |

---

## APIs & Web Services

### DuckDuckGo — Web Search

The built-in `web_search` tool uses **DuckDuckGo Lite** (`https://lite.duckduckgo.com/lite/`) as its primary search endpoint, falling back to **DuckDuckGo HTML** (`https://html.duckduckgo.com/html/`) when Lite returns no results. This provides free, API-key-free web search capability directly from the command line or agent workflow.

### OpenAI Chat Completions API

AgentKthx implements the **OpenAI Chat Completions API** format through Ollama's `/v1/chat/completions` endpoint. This enables integration with any tool or middleware that expects the OpenAI request/response format, including `tool_choice`, `response_format`, `logprobs`, `stop`, `presence_penalty`, `frequency_penalty`, `top_p`, and `n` parameters.

### Ollama API

AgentKthx communicates with Ollama through two API endpoints:

- **`/api/chat`** — Ollama's native chat API (OpenResponses mode). Supports native tool calling, model listing (`/api/tags`), model inspection (`/api/show`), and streaming.
- **`/v1/chat/completions`** — OpenAI-compatible endpoint (Chat Completions mode). Supports SSE streaming, tool calling, and the full OpenAI parameter set.

### llama-server / TurboQuant API

The llama-server backend (including TurboQuant builds) communicates through two endpoints:

- **`/completion`** — llama.cpp native text completion API with `prompt`, `n_predict`, `temperature`, `stop`, and `cache_type_k`/`cache_type_v` (TurboQuant KV compression) parameters. Used in OpenRE mode with ReAct tool parsing.
- **`/v1/chat/completions`** — OpenAI-compatible endpoint (Chat Completions mode). Supports SSE streaming, tool calling, and the full OpenAI parameter set.
- **`/v1/models`** — Model listing endpoint. Falls back to `/props` (llama.cpp native) for model name discovery when `/v1/models` returns nothing useful.
- **`/health`** — Health check endpoint used for readiness polling and server status.

### BitNet API

The BitNet backend communicates through a simple completion endpoint:

- **`/completion`** — Text completion API with `prompt`, `n_predict`, `temperature`, and `stop` parameters. No native tool calling support — AgentKthx uses ReAct text parsing.

### ZAI API

The ZAI backend communicates through two endpoints:

- **`/api/paas/v4/chat/completions`** — OpenAI Chat-Completions compatible endpoint with Bearer token authentication. Supports native tool calling, streaming (SSE), and the full OpenAI parameter set. Used exclusively by AgentKthx's ZAI backend (no native/openre mode — always Chat-Completions).
- **`/api/paas/v4/models`** — Model discovery endpoint. Returns available models dynamically. AgentKthx merges API results with a static catalog to include flash variants and other models not returned by the endpoint.

---

## Benchmarks & Evaluation Frameworks

AgentKthx's test suite draws on and adapts questions from established evaluation benchmarks to measure reasoning, tool-calling, and knowledge capabilities.

| Benchmark | Source | Usage in AgentKthx |
|-----------|--------|-------------------|
| **GSM8K** | [Cobbe et al., 2021](https://arxiv.org/abs/2110.14168) | 50 math word problems adapted for calculator tool-calling evaluation (`test 04_gsm8k_benchmark`) |
| **BIG-bench** | [Srivastava et al., 2022](https://arxiv.org/abs/2210.09261) | Common sense reasoning (25 Q), causal reasoning (25 Q), logical deduction (25 Q), general knowledge (25 Q), and implicit reasoning (25 Q) questions adapted for local LLM evaluation |
| **BIG-bench Hard (BBH)** | [Suzgun et al., 2022](https://arxiv.org/abs/2210.09261) | Reasoning test structure (14 Q across 8 categories: logical deduction, common sense, multi-step, pattern, counter-intuitive, spatial, causal, comparative) |

---

## Development Tools

| Tool | Purpose | Link |
|------|---------|------|
| **pytest** | Unit testing framework (`tests/` directory with 800+ tests covering skills, security, builtins, spec compliance, and agent behavior) | [https://pytest.org](https://pytest.org) |
| **black** | Opinionated Python code formatter (line-length: 100, target: Python 3.9-3.12) | [https://github.com/psf/black](https://github.com/psf/black) |
| **ruff** | Fast Python linter (rules: E, F, W, I) | [https://github.com/astral-sh/ruff](https://github.com/astral-sh/ruff) |
| **setuptools** | Build system and package distribution | [https://setuptools.pypa.io](https://setuptools.pypa.io) |

---

## Python Standard Library

AgentKthx proudly uses **zero external dependencies** at runtime. The entire framework — HTTP requests, JSON parsing, tool execution, security validation, streaming, and all backend communication — relies exclusively on the Python standard library (`urllib`, `json`, `subprocess`, `math`, `datetime`, `re`, `os`, `pathlib`, `time`, `uuid`, `collections`, `itertools`, `zoneinfo`, `platform`). This design choice ensures maximum portability, minimal attack surface, and ease of installation.

---

## Additional Acknowledgments

- **Google Colab** — AgentKthx provides a [Jupyter notebook](https://colab.research.google.com/github/VTSTech/AgentKthx/blob/main/AgentKthx.ipynb) for cloud-based experimentation.
- **PyPI** — Package distribution at [https://pypi.org/project/agentkthx/](https://pypi.org/project/agentkthx/).
- **badgen** and **shields.io** — Badge services used in the README for download counts, license, and commit tracking.
- **TheTom** — Author of [llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant), the TurboQuant KV cache compression fork of llama.cpp that AgentKthx's `turbo` subcommand manages. The `recommended_turbo_config()` mapping (symmetric vs asymmetric KV based on weight quantization) is based on TheTom's turboquant_plus research findings.
- The broader **local LLM community** — including the Ollama, Hugging Face, and open-source AI communities — whose collective work makes running powerful models locally possible.

---

*Last updated: 2026-09-25 (AgentKthx R06.57)*

*Development history section added: 2026-03-30*
*TurboQuant backend, Ollama registry, and tool support data updated: 2026-04-04*
*ZAI backend, GLM model family, and API documentation added: 2026-04-15*
*OpenRouter + Gemini backends, is_cloud attribute, shared context-length recovery, FIX-01 pip dev release detection, TurboQuant port cleanup + zombie detection, head_dim check removal added: 2026-09-25 (R06.57)*