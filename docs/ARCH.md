## Architecture

AgentKthx is a modular agent framework designed for local LLMs with tool-calling capabilities. It implements the OpenResponses specification for multi-provider, interoperable LLM interfaces.

**Specification Compliance**: 100% (R03.5+) -- R04.x, R05.x, R06.0–R07.02

**Version**: R07.02
- OpenResponses API: 100%
- Chat Completions API: 100%
- Soul Spec v0.5: 100%
- ACP v1.0.6: 100%
- AgentSkills: 100%
- Plugin Spec v0.2: 100%

```
agentkthx/
├── core/
│   ├── types.py              # Enum types (StepResultType, BackendType.{OLLAMA, LLAMA_SERVER, BITNET, ZAI, OPENROUTER, GEMINI, HUGGINGFACE}, ApiMode.OPENRE/OPENAI, ToolSupportLevel)
│   ├── models.py             # Data models (Tool, ToolParam, StepResult, AgentRun)
│   ├── memory.py             # Sliding window conversation memory
│   ├── persistent_memory.py  # SQLite-backed PersistentMemory(Memory) subclass (R04.3)
│   ├── tool_parse.py         # ReAct/JSON tool call extraction (see Tool Parser section)
│   ├── tool_cache.py         # Persistent tool support detection cache (R03.6)
│   ├── helpers.py            # Utilities (fuzzy match, argument normalization, security)
│   ├── model_config.py       # Model configuration (temperature, max tokens)
│   ├── model_family_config.py # Family-specific behavior (stop tokens, formats)
│   ├── prompts.py            # Tool argument aliases (TOOL_ARG_ALIASES), platform constants,
│   │                         # few-shot prompting suffixes, system prompt builders
│   ├── args_normal.py        # Full argument normalizer, calculator argument fixer,
│   │                         # missing argument synthesizer
│   ├── error_recovery.py     # ErrorRecoveryTracker, build_enhanced_observation(),
│   │                         # build_retry_context(), is_error_result()
│   ├── openresponses.py      # OpenResponses specification types
│   │
│   │                         # ── Agent runtime mixins (R07.00) ──
│   │                         # The Agent class composes these five; see the
│   │                         # Agent section below for the mixin table.
│   ├── agent_setup.py        # AgentSetupMixin — constructor, 30+ attribute
│   │                         # inits, default system-prompt builder (Phase 9)
│   ├── agentic_loop.py       # AgenticLoopMixin — the unified agentic loop:
│   │                         # _run_loop_iteration + _execute_single_tool_call
│   │                         # + _process_tool_result, parameterized by the
│   │                         # LoopCallbacks dataclass (Phase 5, MAINT-04)
│   ├── streaming.py          # StreamingMixin — SSE machinery: run_stream(),
│   │                         # _generate_stream_chunks(), _generate_stream() (Phase 6)
│   ├── compaction.py         # CompactionMixin — context compaction + running-
│   │                         # token snapshots (Phase 7)
│   └── tool_execution.py     # ToolExecutionMixin — _execute_tool(): registry
│                             # lookup, confirmation gate, argument
│                             # normalization, execution, error formatting (Phase 10)
│
├── tools/
│   ├── registry.py           # Tool registry with decorator-based registration
│   ├── builtins.py           # Built-in tools (calculator, shell, file ops, http, web-search)
│   └── sandboxed_repl.py     # Sandboxed Python REPL execution
│
├── backends/
│   ├── __init__.py           # Backend registry: native (ollama, llama-server) + plugin
│   │                         # lazy loading via _ensure_plugin() (R05.0)
│   ├── base.py               # Abstract BaseBackend class, BackendConfig dataclass
│   ├── ollama.py             # Ollama backend (dual API: openre + openai)
│   ├── llama_server.py       # LlamaServerBackend(OllamaBackend) for llama.cpp / TurboQuant (R04.2)
│   │                         # - Dual API support (OpenRE via /completion, OpenAI via /v1/chat/completions)
│   │                         # - BitNet mode with conversation budgeting
│   │                         # - /props fallback for model name discovery
│   │                         # - Family-aware prompt formatting
│   │                         # - Turn-bleed guards
│   ├── ollama_registry.py    # Ollama model registry: manifest discovery, GGUF header
│   │                         # parsing via mmap, TurboQuant compatibility (R04.5)
│   ├── openai_compat.py      # OpenAICompatibleBackend — shared base for cloud
│   │                         # backends speaking the OpenAI protocol (R06.55;
│   │                         # subclassed by ZAI, OpenRouter, Gemini plugins)
│   └── bitnet.py             # Deprecated alias: BitNetBackend is now
│                             # LlamaServerBackend(bitnet_mode=True)
│
├── plugins/                  # Plugin system (R05.0)
│   ├── __init__.py           # get_plugin_manager() singleton export
│   ├── _loader.py            # PluginManager: discovery, loading, dependency resolution,
│   │                         # backend/CLI/config registration, lazy loading
│   ├── acp/                  # ACP (Agent Control Panel) plugin
│   │   ├── plugin.json       # Manifest (type: feature)
│   │   └── acp_plugin.py     # ACP v1.0.6 integration (audit logging, session monitoring)
│   ├── bitnet/               # BitNet backend plugin
│   │   ├── plugin.json       # Manifest (type: backend, provides: bitnet)
│   │   └── bitnet.py          # BitNetBackend: LlamaServerBackend with bitnet_mode=True
│   ├── zai/                  # ZAI cloud API plugin
│   │   ├── plugin.json       # Manifest (type: backend, provides: zai)
│   │   └── zai.py             # ZaiBackend: GLM models via ZAI API, 13-model catalog
│   ├── gemini/               # Google Gemini cloud API plugin (R06.56)
│   │   ├── plugin.json       # Manifest (type: backend, provides: gemini)
│   │   ├── __init__.py       # register()/unregister() entrypoints
│   │   └── gemini.py          # GeminiBackend: 71-model catalog, <thought> tag parser,
│   │                         # free-tier data from AI Studio, thinking config routing,
│   │                         # 429 retry with spend-limit detection
│   ├── huggingface/          # Hugging Face Inference Router plugin (R07.02)
│   │   ├── plugin.json       # Manifest (type: backend, provides: huggingface + hf alias)
│   │   ├── __init__.py       # register()/unregister() with alias_of="huggingface"
│   │   └── huggingface.py    # HuggingFaceBackend: 154-model live catalog, per-provider
│   │                         # is_free auto-detection, whoami-v2 free-tier probe,
│   │                         # :cheapest routing suffix support, HTTP 402 credit-
│   │                         # exhaustion fallback to HF_FREE_FALLBACK_MODEL
│   ├── turboquant/           # TurboQuant server management plugin
│   │   ├── plugin.json       # Manifest (type: feature, provides: turbo CLI command)
│   │   └── turbo.py           # Server lifecycle, Ollama model registry, GGUF parsing
│   └── test-plugin/          # Plugin system validation plugin
│       ├── plugin.json       # Manifest (type: feature, provides: test-backend, plugin-test)
│       ├── __init__.py       # register()/unregister() entrypoints
│       └── test_backend.py    # Minimal stub backend for integration testing
│
├── skills/
│   ├── loader.py             # Skill loader (Agent Skills spec)
│   │                         # - Description validation (1-1024 chars)
│   │                         # - SPDX license validation
│   │                         # - Compatibility parsing
│   │                         # - Environment compatibility checks
│   └── test-harness/         # Diagnostic skill for testing skill system
│       └── SKILL.md
│
├── soul/
│   ├── types.py              # Soul Spec v0.5 data structures
│   └── loader.py             # SoulLoader with progressive disclosure + dynamic tools
│
├── souls/
│   ├── nova-helper/          # Diagnostic assistant soul (skill-less LLM testing)
│   │   ├── soul.json         # Manifest
│   │   ├── SOUL.md           # Persona definition (concise)
│   │   ├── IDENTITY.md       # Identity (concise)
│   │   ├── STYLE.md          # Communication style (concise)
│   │   └── AGENTS.md         # Agent configuration
│   └── nova-skills/          # Skill-guided assistant soul (for use with --skills)
│       ├── soul.json         # Manifest
│       ├── SOUL.md           # Persona definition (concise)
│       ├── IDENTITY.md       # Identity (concise)
│       ├── STYLE.md          # Communication style (concise)
│       └── AGENTS.md         # Agent configuration
│
├── examples/                 # Test examples and benchmarks
│
├── agent.py                  # Agent class — facade composing the five core
│                             # mixins (R07.00); retains run(), the MAINT-04
│                             # Phase 1-4 shared helpers, _generate(), and the
│                             # thin _run_core/_run_core_streaming wrappers
├── agent_mode.py             # Autonomous agent mode (state machine)
├── orchestrator.py           # Multi-agent orchestration (R03.6)
│                             # - Router, Pipeline, Parallel modes
│                             # - LLM-based routing (optional)
│                             # - True parallel execution with ThreadPoolExecutor
│                             # - Fallback agents, timeout handling
│                             # - Result merging strategies
├── colors.py                 # Shared ANSI color utilities (R03.6)
│                             # - Color class with ANSI codes
│                             # - Color functions: green, yellow, cyan, etc.
│                             # - Utility: visible_len, pad_colored
├── config.py                 # Core framework config (OLLAMA_BASE_URL, LLAMA_SERVER_BASE_URL)
│                             # Plugin-owned config (BitNet, ZAI, ACP, TurboQuant) reads from
│                             # env vars with defaults defined in each plugin's plugin.json
├── cli/                      # CLI package (R07.00 Phase 8 — was the 4270-line
│   ├── __init__.py           # cli.py). Compatibility facade re-exporting every
│   │                         # name that existed on the old module, so
│   │                         # `from agentkthx.cli import X` and
│   │                         # monkeypatching `agentkthx.cli.X` keep working
│   ├── __main__.py           # `python -m agentkthx.cli` shim
│   ├── parser.py             # create_parser() — argparse construction
│   ├── agent_factory.py      # _build_agent, _init_acp, skill-prompt loading
│   ├── banner.py             # ASCII banner + update-check notice
│   ├── headers.py            # Session/run header + summary printers
│   ├── utils.py              # Model resolution, step printing, tool cache
│   ├── main.py               # main() — dispatch + plugin wiring
│   └── commands/             # One module per subcommand (14 modules):
│                             # run, chat, agent, models, test, config, turbo,
│                             # soul, skills, sessions, plugins, modelfile,
│                             # tools, version (+update)
├── model_discovery.py        # Ollama model listing and selection
├── shared_args.py            # Shared CLI argument definitions + SharedConfig dataclass (R04.2)
│
├── docs/                     # Documentation
│   ├── ARCH.md               # Technical documentation for developers
│   ├── CHANGELOG.md          # Version history and release notes
│   ├── CREDITS.md            # Credits, acknowledgments, and development history
│   ├── PLUGIN_SPEC.md        # Plugin system specification (R05.0)
│   ├── TESTS.md              # Benchmark results and testing guide
│   ├── JEV_API_MODE.md       # JEV (System-One) API mode reference
│   ├── ZAI_API_TECHNICAL_REFERENCE.md  # ZAI API reference
│   ├── OPENROUTER_API_TECHNICAL_REFERENCE.md  # OpenRouter API reference
│   ├── GEMINI_API_TECHNICAL_REFERENCE.md  # Gemini API reference (R06.56)
│   └── R07.00-MODULARIZATION-PLAN.md     # Modularization plan (executed in R07.00)
│
├── audit/                    # Audit materials (R06.41)
│   ├── audit.md              # Codebase audit findings report
│   └── brief.md              # Condensed project orientation brief
│
├── README.md                 # Project overview, quick start, features
└── LICENSE                   # MIT License
```

---

## Key Components

### Agent (`agent.py` + `core/` mixins)

Since R07.00 the Agent is a thin facade composing five focused mixins — one per subsystem:

| Mixin | Module | Owns |
|-------|--------|------|
| `AgentSetupMixin` | `core/agent_setup.py` | Constructor, 30+ attribute inits, default system-prompt builder |
| `CompactionMixin` | `core/compaction.py` | `_check_compaction()`, running-token snapshots |
| `ToolExecutionMixin` | `core/tool_execution.py` | `_execute_tool()`: lookup, confirmation gate, normalization, execution |
| `StreamingMixin` | `core/streaming.py` | `run_stream()`, SSE chunk parsing, stream accumulation |
| `AgenticLoopMixin` | `core/agentic_loop.py` | `_run_loop_iteration()` — the unified agentic loop |

```python
class Agent(
    AgentSetupMixin,
    CompactionMixin,
    ToolExecutionMixin,
    StreamingMixin,
    AgenticLoopMixin,
): ...
```

`agent.py` itself (1096 lines, down from 3466 at R06.58) retains `run()`, the MAINT-04 Phase 1-4 shared helpers (`_generate_with_retry`, `_handle_finish_reason`, `_check_tool_choice_required`, `_parse_tool_calls`, `_finalize_run`, `_enforce_final_answer`, `_handle_blocked_tool_call`, `_reject_for_tool_choice`), `_generate()`, and the thin `_run_core` / `_run_core_streaming` wrappers. The public API is unchanged: `from agentkthx import Agent`.

#### Unified agentic loop (R07.00 Phase 5, closes MAINT-04)

`_run_core` and `_run_core_streaming` were near-identical ~500-line loop bodies. Both are now thin wrappers around a single loop body, `_run_loop_iteration()`. Per-path differences are expressed explicitly through the `LoopCallbacks` dataclass — hooks the streaming wrapper fills in, plus two behavioral toggles that preserve the pre-R07.00 per-path behavior exactly:

| Hook / toggle | Non-streaming | Streaming |
|---|---|---|
| `on_step_start(step)` | no-op | Preventive compaction at top of step |
| `on_generated(step, response)` | no-op | Token snapshot + CLI footer refresh |
| `on_tool_executed(count, name, args, result)` | no-op | Inline `[N] tool …` print (R06.55) |
| `on_tool_result_committed(n_calls)` | no-op | Between-calls compaction when a step carried multiple tool calls (R06.58) |
| `include_format_hint` | `True` | `False` (ReAct hint in tool_choice rejection) |
| `mark_response_completed` | `True` | `False` (pre-existing streaming behavior, preserved + test-asserted) |

Debug output was unified to the non-streaming superset, closing the MAINT-04 29-check debug divergence between the two paths.

The main Agent class implements the **OpenResponses Agentic Loop**:

```
1. Model samples from input
2. If tool call: execute tool, return observation, continue
3. If no tool call: return final output items
```

**Key principle**: All tool calls must come from the model itself. No fallbacks that bypass the AI model.

**Features**:
- Unified ReAct prompting for all models
- Soul Spec integration for persona/personality
- Dynamic tool injection into system prompt
- Default context window: 4096 tokens
- Debug output with OpenResponses item tracking
- Persistent memory sessions via PersistentMemory (R04.3)
- Error recovery with retry context injection
- Dangerous tool confirmation via `confirm_dangerous` callback (R04.2)
- Audit logging for shell, write_file, edit_file outcomes (R04.2)
- Ctrl+C cancellation at backend, tool, and agent loop levels (R05.0)

### Orchestrator (`orchestrator.py`)

Multi-agent orchestration with three execution modes (enhanced in R03.6):

**Execution Modes**:

| Mode | Description | Use Case |
|------|-------------|----------|
| `router` | Routes task to best matching agent | Task dispatch to specialists |
| `pipeline` | Sequential execution, output chaining | Multi-step transformations |
| `parallel` | Simultaneous execution, result merging | Ensemble/consensus tasks |

**Key Features**:
- **LLM-based routing** - Optional router model decides which agent to use
- **True parallelism** - ThreadPoolExecutor for concurrent agent execution
- **Fault tolerance** - Fallback agents when primary fails
- **Timeout handling** - Per-agent timeouts prevent hanging
- **Result merging** - Strategies: `concat`, `first`, `vote`, `best`

```python
from agentkthx import Orchestrator, AgentCard, Agent
from agentkthx.tools import make_builtin_registry

# Create specialized agents
tools = make_builtin_registry()

math_card = AgentCard(
    name="math_agent",
    description="Handles mathematical calculations",
    capabilities=["calculate", "math", "compute"],
    tools=["calculator"],
    priority=2,         # Higher priority for math tasks
    timeout=30.0,       # 30 second timeout
)

code_card = AgentCard(
    name="code_agent", 
    description="Writes and executes code",
    capabilities=["code", "python", "script"],
    tools=["shell", "write_file"],
    fallback=True,      # Use as fallback if others fail
)

# Create orchestrator
orchestrator = Orchestrator(
    mode="router",
    router_model="qwen2.5:0.5b",  # Optional LLM routing
    merge_strategy="best",
)
orchestrator.register(math_card)
orchestrator.register(code_card)

# Run task
result = orchestrator.run("Calculate 15 * 8")
print(result.final_answer)
print(f"Agent used: {result.chosen_agent}")
```

**OrchestratorResult Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `mode` | str | Execution mode used |
| `chosen_agent` | str | Agent selected (router mode) |
| `agents_used` | list | All agents that ran |
| `final_answer` | str | Merged/selected result |
| `agent_results` | dict | Results by agent name |
| `agent_times` | dict | Execution times by agent |
| `total_ms` | float | Total orchestration time |
| `success` | bool | Whether execution succeeded |

### OpenResponses Specification (`core/openresponses.py`)

Full implementation of the OpenResponses specification (https://www.openresponses.org/specification):

**Items**: Atomic units of context with lifecycle states
- `MessageItem`: Conversation turns (user/assistant/system)
- `FunctionCallItem`: Tool invocations from the model
- `FunctionCallOutputItem`: Tool execution results
- `ReasoningItem`: Model's internal thought process

**State Machines**:
```
Response: queued → in_progress → completed/failed/incomplete/cancelled
Items: in_progress → completed/failed/incomplete
```

**tool_choice modes**:
| Mode | Behavior |
|------|----------|
| `"auto"` | Model decides whether to call tools (default) |
| `"required"` | Model MUST call at least one tool |
| `"none"` | Model MUST NOT call tools |
| `ToolChoice.specific("name")` | Force specific tool |
| `ToolChoice.allowed_tools([...])` | Restrict to tool list |

```python
from agentkthx import Agent
from agentkthx.core.openresponses import ToolChoice

# Default: model decides
agent = Agent(model="qwen2.5:0.5b", tools=["calculator"])

# Force tool usage
agent = Agent(model="llama3", tools=["calculator"], tool_choice="required")

# Restrict to specific tools
agent = Agent(
    model="llama3",
    tools=["calculator", "shell", "read_file"],
    allowed_tools=["calculator"]  # Only calculator available
)

# Disable tools
agent = Agent(model="llama3", tools=["calculator"], tool_choice="none")
```

### Tool Parser (`core/tool_parse.py`)

Parses tool calls from model output in multiple formats:

**Supported Formats**:

1. **Plain ReAct format**:
```
Action: calculator
Action Input: {"expression": "15 * 8"}
```

2. **JSON-wrapped ReAct** (from small models):
```json
{
  "action": "calculator",
  "actionInput": {"expression": "15 * 8"}
}
```

3. **Markdown code block JSON**:
```json
{
  "action": "calculator",
  "action_input": {"expression": "15 * 8"}
}
```

4. **With Final Answer** (simultaneous):
```
Action: calculator
Action Input: {"expression": "15 * 8"}
Final Answer: 120
```

**Key variations handled**:
- `action`, `Action`, `ACTION`
- `actionInput`, `action_input`, `Action Input`
- Fuzzy matching for hallucinated tool names

### Tool Support Detection (`core/tool_cache.py`, `core/types.py`)

**IMPORTANT**: Tool support is NOT determined by model family. It depends on the model's template, which can vary within the same family.

**Problem with family-based assumptions**:
```
qwen2.5:0.5b (base)      → native tools ✓
qwen2.5-coder:0.5b       → ReAct only ○ (same family, different template!)
deepseek (coder)         → varies by variant
deepseek-r1:1.5b         → ReAct only ○ (reasoning model, no native tools)
```

**Detection Flow**:
```
┌─────────────────────┐
│ ToolSupportLevel    │
│ .detect(model)      │
└──────────┬──────────┘
           │
           ▼
    ┌──────────────┐
    │ Check Cache  │ ──→ ~/.cache/agentkthx/tool_support.json
    └──────┬───────┘
           │
     ┌─────┴─────┐
     │           │
  Cached      Not Cached
     │           │
     ▼           ▼
 Return      Return UNTESTED
 Level       (use --tool-support to test)
```

**Cache Module API**:
```python
from agentkthx.core.tool_cache import (
    get_cached_tool_support,    # Get cached level or None
    cache_tool_support,          # Save detection result
    load_tool_cache,             # Load full cache dict
    save_tool_cache,             # Save full cache dict
)
from agentkthx.core.types import ToolSupportLevel

# Check if model has cached support level
support = get_cached_tool_support("qwen2.5-coder:0.5b")
# Returns: ToolSupportLevel.REACT or None if not cached

# Cache a detection result
cache_tool_support(
    model="qwen2.5-coder:0.5b",
    support=ToolSupportLevel.REACT,
    family="qwen2"
)

```

**CLI Usage**:
```bash
# List models with cached tool support (or "? untested")
agentkthx models

# Test and cache tool support for all models
agentkthx models --tool-support

# Ignore cache
agentkthx models --tool-support --no-cache
```

**ToolSupportLevel Values**:
| Level | Meaning | Display |
|-------|---------|---------|
| `NATIVE` | API returns `tool_calls` structure | native |
| `REACT` | Model outputs JSON as text, parsed by AgentKthx | react |
| `NONE` | Model explicitly rejects tools (HTTP 400) | none |
| `UNTESTED` | Not yet tested | untested |

### Soul System (`soul/`)

ClawSouls Soul Spec v0.5 support for persona packages:

**Progressive Disclosure**:
- Level 1: soul.json manifest only
- Level 2: + SOUL.md + IDENTITY.md
- Level 3: + STYLE.md + AGENTS.md + HEARTBEAT.md

**Dynamic Tool Injection**:
The static tool reference in SOUL.md is replaced with actual available tools at runtime:

```python
# Static in SOUL.md:
## Tool Reference (only use if available)
| Tool | When to use | Arguments |
...

# Dynamically replaced with actual tools:
### Tool Reference (only use if available)
| Tool | When to use | Arguments |
|------|-------------|-----------|
| `calculator` | Evaluate mathematical expressions | `{"expression": "..."}` |
```

**Cache Management**:
```python
from agentkthx.soul import load_soul

# Force reload from disk after modifying soul files
# (R07.00: the zero-caller clear_soul_cache() was removed)
soul = load_soul("nova-helper", reload=True)
```

---

## Backends

### Architecture (R05.0)

AgentKthx uses a two-tier backend architecture:

1. **Native backends** -- Ollama and llama-server are built-in, always available, zero overhead.
2. **Plugin backends** -- BitNet, ZAI, and any future backends are loaded on demand via the plugin system. Plugins register their backend classes through `register_backend(name, cls)` and are lazily loaded by `_ensure_plugin()` when first requested.

Backend resolution in `get_backend(name)`:
```
1. Check native registry (ollama, llama-server) → immediate
2. Check PluginManager.get_backend_class(name) → lazy load plugin if needed
3. Raise ValueError if not found
```

### Backend Registry

The `--backend` flag selects which backend to use:

| Backend Flag | Source | Class | Description |
|-------------|--------|-------|-------------|
| `ollama` | native | `OllamaBackend` | Ollama server (default) |
| `llama-server` / `llama_server` | native | `LlamaServerBackend` | llama.cpp HTTP server / TurboQuant |
| `bitnet` | plugin | `BitNetBackend` | BitNet 1.58b models via llama.cpp |
| `zai` | plugin | `ZaiBackend` | ZAI cloud API (GLM models) |
| `openrouter` | plugin | `OpenRouterBackend` | OpenRouter cloud API (500+ models) |
| `gemini` | plugin | `GeminiBackend` | Google Gemini API (71 models, free tier, Gemma) |
| `huggingface` (alias: `hf`) | plugin | `HuggingFaceBackend` | Hugging Face Inference Router (154 models, 18 partner providers, free-tier auto-detect) |

Plugin backends are automatically discovered and loaded on first use. See `docs/PLUGIN_SPEC.md` for the full plugin specification.

### OllamaBackend (`backends/ollama.py`)

The original backend for Ollama. Handles both OpenResponses and OpenAI Chat-Completions API endpoints.

### LlamaServerBackend (`backends/llama_server.py`) (R04.2)

Extends `OllamaBackend` with llama.cpp server support. Used for both standard llama.cpp deployments and TurboQuant.

**Features**:
- **Dual API support** -- OpenRE via `/completion`, OpenAI via `/v1/chat/completions`
- **BitNet mode** -- Activated via `bitnet_mode=True` with conversation budgeting:
  - 1024-character prompt budget for context window management
  - 4-exchange cap per conversation
  - `repeat_penalty=1.3` to reduce repetition
- **`/props` fallback** -- When model name is unknown, queries `/props` endpoint for model discovery
- **Family-aware prompt formatting** -- Adjusts prompt structure based on detected model family
- **Turn-bleed guards** -- Stop tokens `\nUser:` and `\nAssistant:` prevent the model from generating additional conversation turns
- **Default URL**: `LLAMA_SERVER_BASE_URL` defaults to `http://localhost:8764`

### BitNet Backend (`plugins/bitnet/`) (R04.2, plugin in R05.0)

The BitNet backend is a plugin that provides `BitNetBackend`, a thin wrapper inheriting from `LlamaServerBackend` with `bitnet_mode=True`. All logic lives in `backends/llama_server.py`. Lazy-loaded via the plugin system when `--backend bitnet` is used.

```python
class BitNetBackend(LlamaServerBackend):
    def __init__(self, **kwargs):
        kwargs.setdefault("bitnet_mode", True)
        super().__init__(**kwargs)
```

### ZAI Backend (`plugins/zai/`) (R04.6, expanded R04.7, plugin in R05.0)

The ZAI backend is a plugin providing `ZaiBackend`, connecting to the ZAI cloud API (`https://api.z.ai`) for GLM series models. Unlike local backends, ZAI is always OpenAI Chat-Completions — no openre mode. Lazy-loaded when `--backend zai` is used.

**Key differences from local backends**:
- **Always OPENAI API mode** — Ignores `api_mode` parameter, forces `ApiMode.OPENAI`
- **Bearer token auth** — Injects `Authorization: Bearer <key>` into every request
- **Cloud endpoint** — No server management, no `is_running()` health check
- **Dynamic model discovery** — Queries `/api/paas/v4/models`, merges with static catalog
- **Free-only mode** — `ZAI_FREE_ONLY=true` swaps paid models to free fallback before calling the API
- **Credit-exhaustion fallback** — On HTTP 429 (error 1113 "Insufficient balance"), auto-retries with free model
- **Tool rejection fallback** — If a model doesn't support tools, strips `tools` param and retries (ReAct mode)

**Endpoints**:
- `POST /api/paas/v4/chat/completions` — OpenAI Chat-Completions compatible
- `GET /api/paas/v4/models` — Dynamic model discovery

**Environment Variables**:

| Variable | Default | Description |
|----------|---------|-------------|
| `ZAI_BASE_URL` | `https://api.z.ai` | API endpoint |
| `ZAI_API_KEY` | (none) | API key (required) |
| `ZAI_FREE_ONLY` | `false` | Restrict to free models only |
| `ZAI_FREE_FALLBACK_MODEL` | `glm-4.5-flash` | Free model used when paid model fails |

**Model Catalog** (13 models, pricing per 1M tokens input/output):

| Model | Pricing | Free? |
|-------|---------|-------|
| GLM 5.1 | $1.40/$4.40 | No |
| GLM 5 Turbo | $1.20/$4.00 | No |
| GLM 5 | $1.00/$3.20 | No |
| GLM 4.7 | $0.60/$2.20 | No |
| GLM 4.7 Flash | Free | Yes |
| GLM 4.7 FlashX | $0.07/$0.40 | No |
| GLM 4.6 | $0.60/$2.20 | No |
| GLM 4.5 | $0.60/$2.20 | No |
| GLM 4.5 Flash | Free | Yes |
| GLM 4.5 X | $2.20/$8.90 | No |
| GLM 4.5 Air | $0.20/$1.10 | No |
| GLM 4.5 AirX | $1.10/$4.50 | No |
| GLM 4 32B | $0.10/$0.10 | No |

**Usage**:
```bash
# Free model (no credits needed)
agentkthx chat --backend zai --model glm-4.5-flash

# Paid model with free-only mode (auto-swaps to free)
export ZAI_FREE_ONLY=true
agentkthx chat --backend zai --model glm-5.1

# Credit-exhaustion fallback (auto-retries on 429)
agentkthx chat --backend zai --model glm-5.1
```

---

### Gemini Backend (`plugins/gemini/`) (R06.56)

The Gemini backend is a plugin that provides `GeminiBackend`, inheriting from `OpenAICompatibleBackend` (the shared base class extracted in R06.55). It connects to Google's OpenAI-compatible endpoint at `https://generativelanguage.googleapis.com/v1beta/openai/` and supports all Gemini 3.x, Gemini 2.5, and Gemma 4 models.

Key features:
- **71-model live catalog** from the `/models` endpoint (with static catalog fallback for 10 core models when the API is unreachable)
- **Free-tier data embedded** from Google AI Studio — 20 confirmed free models with actual RPM/TPM/RPD numbers (no API endpoint exposes pricing; data transcribed manually)
- **`<thought>` tag parser** for Gemma — stateful streaming parser routes inline `<thought>...</thought>` blocks to `reasoning_content` so AgentKthx shows them as collapsible "thought" panels (same UX as Gemini 3.x native thinking)
- **Thinking config routing** — `reasoning_effort` ↔ `extra_body.google.thinking_config` mutual exclusivity enforced; `service_tier` routing (standard/flex/priority)
- **429 RESOURCE_EXHAUSTED retry** with `Retry-After` honoring, exponential backoff (5s→90s cap), spend-limit detection (60s min wait for paid tiers)
- **ROB-06 context-length 400 recovery** — parses Gemini's "X in the input, Y in the output" error format, calculates safe `max_tokens`, persists across calls
- **Chat-capability classifier** — `_NON_CHAT_PATTERNS` identifies non-chat models (embeddings, video gen, music gen, robotics, etc.) and marks them `✗ none` in `test_tool_support()` so users don't accidentally try to chat with an embedding model
- **`GEMINI_FREE_ONLY` filter** — restrict model list to the 20 confirmed free-tier models

Configuration env vars: `GEMINI_API_KEY` (or `GOOGLE_API_KEY` fallback), `GEMINI_BASE_URL`, `GEMINI_DEFAULT_MODEL`, `GEMINI_FREE_ONLY`, `GEMINI_THINKING_LEVEL`, `GEMINI_SERVICE_TIER`.

See `docs/GEMINI_API_TECHNICAL_REFERENCE.md` for the 1553-line technical reference covering all endpoints, error codes, rate limits, and implementation details.

---

### Hugging Face Backend (`plugins/huggingface/`) (R07.02)

The Hugging Face backend is a plugin that provides `HuggingFaceBackend`, inheriting from `OpenAICompatibleBackend` (the shared base class extracted in R06.55). It connects to the Hugging Face Inference Router at `https://router.huggingface.co/v1` — a unified proxy that exposes 100+ open-weight models (Llama, Qwen, DeepSeek, Mistral, Gemma, GLM, Phi, Command-R, gpt-oss) served by ~18 partner providers (Together, Groq, Novita, DeepInfra, Fireworks, Cerebras, Replicate, Fal AI, Featherless, Baseten, Cohere, Nscale, OVHcloud, Public AI, Scaleway, WaveSpeedAI, Z.ai, HF Inference) through a single OpenAI-compatible `/chat/completions` endpoint. This is the third cloud-provider backend (after ZAI and OpenRouter) and the first to ship with a dedicated API Technical Reference written **before** the implementation, as the blueprint (see `docs/HUGGINGFACE_API_TECHNICAL_REFERENCE.md` committed in R07.01).

Key features:
- **154-model live catalog** from `/v1/models` (with 31-model static catalog `HF_MODELS` as fallback when the API is unreachable)
- **Per-provider parse shape** (R07.02 polish) — `_parse_hf_model()` captures the full per-provider array: `pricing: {input, output}` (USD per 1M tokens, MIN aggregated as `cheapest_input_per_1m` / `cheapest_output_per_1m`), `is_free` (OR aggregated as `any_free_provider`), `supports_tools` (any/all signals), `supports_structured_output`, `first_token_latency_ms`, `throughput`, `status`, `is_model_author`. Top-level `context_length` and `max_completion_tokens` aggregated as MAX across providers (best-case budget — actual budget depends on which partner the router picks under `:fastest` routing). Full raw `providers[]` array preserved on the parsed model for forward-compat.
- **`is_free` auto-detection** (R07.02 polish) — `_is_free_model_live()` instance method supplements the static `HF_FREE_MODEL_WHITELIST` (31 open-weight models verified to have free-tier access via partner providers) with live API `is_free` flag consultation. Currently `false` for all 336 combos even with auth (Sept 2026 state), but if HF flips any combo free tomorrow (sponsored/promo window), AgentKthx auto-picks it up with zero code change.
- **`whoami-v2` free-tier probe** (R07.02 polish) — `_probe_whoami()` hits `https://huggingface.co/api/whoami-v2` on every `__init__` to (a) validate the token before the first chat call (catches typos/expired tokens early), and (b) detect free-tier users via `canPay=false`. Best-effort — failures swallowed; `_user_info` stays None and the backend still works (paid inference will surface its own 401 at request time). A `read`-role fine-grained token (the default) is enough; higher-role endpoints (`/api/inference-providers`, `/api/billing/usage`) require `write`/`admin` role but we don't need them.
- **`_resolve_free_only_mode()` auto-detection** (R07.02 polish) — explicit env var > whoami auto-detect > module-constant fallback: explicit `HF_FREE_ONLY=true` → strict (no auto-detect); explicit `HF_FREE_ONLY=false` → permissive (opt-out — skips whoami entirely); unset → auto-detect: `canPay=false` → strict + one-time stderr warning, `canPay=true` → permissive, whoami unreachable → fall back to module-level `HF_FREE_ONLY` constant. VTSTech's account (`canPay=false`) gets auto-enabled whitelist protection + warning even without setting the env var explicitly; if a billing card is later added, the auto-detection flips to permissive with no code change.
- **Provider routing via model-id suffix** — `:fastest` (default), `:cheapest` (auto-applied when `HF_FREE_ONLY=true`), `:preferred` (user's preference order at huggingface.co/settings/inference-providers), `:<partner-name>` (pin to a specific partner like `:groq`, `:together`, `:novita`, `:deepinfra`, `:fireworks`, `:cerebras`). Controlled by the `HF_PROVIDER_POLICY` env var (auto-applied when no explicit suffix on the model id).
- **`HF_FREE_FALLBACK_MODEL` swap on HTTP 402** (mirrors ZAI's 429 insufficient-balance fallback at `zai.py:706-714`) — when free-tier credit is exhausted mid-run, the backend swaps to the configured fallback model (default `Qwen/Qwen2.5-7B-Instruct-1M`) and retries once. Strict mode (`HF_FREE_ONLY=true`) treats 402 as a hard failure (no retry — retrying burns router quota without resolving).
- **429 retry with `Retry-After` honor** — exponential back-off schedule (5s → 10s → 20s → 40s → 80s → 90s cap with ±20% jitter, 6 retries default — mirrors OpenRouter R06.54). Override via `HF_MAX_429_RETRIES`.
- **ReAct fallback** when a partner provider rejects the `tools` field — HF-specific error patterns added beyond the OpenRouter set: `tool use is not supported`, `tool_calls not supported on this model`, plus the TGI/llama-server form `unsupported param: tools`.
- **Reasoning-content capture** for thinking-capable HF models (Qwen3-Thinking family, DeepSeek-R1, openai/gpt-oss-20b-reasoning) via the `reasoning` field on the response message (mirrors OpenRouter's R06.53 streaming capture — surfaced in the CLI `reasoning:` panel above the `AgentKthx:` prompt when `--think` is set).
- **Context-length 400 recovery** via the shared `OpenAICompatibleBackend._handle_context_length_400` helper (ARCH-03 R06.57 — HF Router's error format matches the base-class default regex patterns, so no override is needed).
- **JEV dispatch** via `_jev_call_completions()` routing through `self.generate()` so auth + 429 retry + `HF_FREE_ONLY` are preserved for decision-mode calls.
- **Both canonical (`huggingface`) and alias (`hf`) `--backend` values** work end-to-end (alias registered via `alias_of="huggingface"` in `__init__.py`).
- **Once-per-process warning flag** (R07.02 polish) — the free-tier stderr warning is process-scoped via a class-level `_free_tier_warning_emitted: bool = False` flag. The CLI may instantiate `HuggingFaceBackend` twice in one command (once for `_probe_backend` feature discovery, once for the actual `cmd_models` / `cmd_chat` invocation), and the flag prevents the warning from firing twice — subsequent instances in the same process skip the print but **still enforce the whitelist**.

Configuration env vars: `HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN` fallback), `HF_BASE_URL`, `HF_BASE_URL_LEGACY` (documented but not used by v0.1), `HF_DEFAULT_MODEL`, `HF_FREE_ONLY`, `HF_FREE_FALLBACK_MODEL`, `HF_PROVIDER_POLICY`, `HF_MAX_429_RETRIES`.

See `docs/HUGGINGFACE_API_TECHNICAL_REFERENCE.md` for the 1019-line technical reference covering all endpoints, error codes, rate limits, and implementation details. The parallel `docs/OPENAI_API_TECHNICAL_REFERENCE.md` (1677 lines, also committed in R07.01) awaits the planned R07.0x OpenAI plugin — same blueprint pattern.

```bash
# Default routing (fastest)
agentkthx chat --backend hf --model openai/gpt-oss-120b

# Pin to Groq (very fast for Llama-3.x)
agentkthx chat --backend hf --model meta-llama/Llama-3.3-70B-Instruct:groq

# Reasoning model with chain-of-thought display
agentkthx chat --backend hf --model Qwen/Qwen3-4B-Thinking-2507 --stream --think

# Free-tier whitelist only (31 models, auto-appends :cheapest suffix)
HF_FREE_ONLY=1 agentkthx models --backend hf

# Auto-detection mode (unset HF_FREE_ONLY — probe whoami-v2 first)
unset HF_FREE_ONLY
agentkthx models --backend hf   # warning fires once, 31-model whitelist auto-enforced
```

---

## Plugin System (R05.0)

The plugin system enables extending AgentKthx with additional backends, CLI commands, and configuration without modifying the core framework. See `docs/PLUGIN_SPEC.md` for the full specification.

### PluginManager (`plugins/_loader.py`)

Central singleton registry for all plugin operations:

```python
from agentkthx.plugins import get_plugin_manager

pm = get_plugin_manager()
manifests = pm.discover()           # Scan plugins/ for plugin.json manifests
pm.load_all()                      # Load all plugins in dependency order
plugin = pm.load("bitnet")         # Load a specific plugin
pm.register_backend("name", cls)  # Register a backend class
pm.register_cli_command("name", handler)  # Register a CLI subcommand
```

### Manifest Format (`plugin.json`)

Each plugin ships a `plugin.json` manifest:

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "type": "backend",
  "entrypoint": "__init__",
  "depends": [],
  "provides": {
    "backends": { "my-backend": "module.MyBackendClass" },
    "cli_commands": ["my-command"],
    "cli_flags": { "--backend": ["my-backend"] }
  },
  "config": { "env_prefix": "MY_PLUGIN", "defaults": { "MY_PLUGIN_URL": "http://localhost:8080" } }
}
```

### Key Features

- **Manifest-based discovery** -- Scan `agentkthx/plugins/` for subdirectories containing `plugin.json`
- **Topological dependency resolution** -- `depends` field respected via Kahn's algorithm
- **Lazy loading** -- Plugins loaded on first use via `_ensure_plugin()` in `backends/__init__.py`
- **Backend name independence** -- Backend name (e.g. `test-backend`) differs from plugin directory name (e.g. `test-plugin`)
- **CLI extension** -- Plugin commands appear in `--help` with `*` suffix (e.g. `plugin-test* [plugin]`)
- **Config aggregation** -- Plugin defaults merged into PluginManager for framework-wide access

### Shipped Plugins

| Plugin | Type | Provides |
|--------|------|----------|
| `acp` | feature | ACP v1.0.6 integration (audit logging, session monitoring) |
| `bitnet` | backend | `bitnet` backend (LlamaServerBackend with bitnet_mode) |
| `zai` | backend | `zai` backend (GLM models via ZAI API, 13-model catalog) |
| `openrouter` | backend | `openrouter` backend (500+ models via OpenRouter API) |
| `gemini` | backend | `gemini` backend (71 Gemini/Gemma models, free-tier data, `<thought>` tag parser) |
| `huggingface` (alias: `hf`) | backend | `huggingface` backend (154 models via HF Inference Router, 31-model free-tier whitelist, per-provider `is_free` auto-detection, whoami-v2 free-tier probe) |
| `turboquant` | feature | `turbo` CLI command (server lifecycle, model registry) |
| `test-plugin` | feature | `test-backend` backend, `plugin-test` CLI command |

### Plugin Lifecycle

```
discover() → load(name) → register(manager) → [active] → unregister() → unload()
```

Plugins are discovered by scanning `agentkthx/plugins/` for subdirectories containing `plugin.json`. The entrypoint module (always `__init__`) must export `register(manager)` and `unregister(manager)` functions. The `register()` function is called with the PluginManager instance, where the plugin registers its backends, CLI commands, and config defaults.

---

## Ctrl+C Cancellation (R05.0)

Graceful Ctrl+C handling at three layers of the agent execution stack:

1. **Backend HTTP call** -- `KeyboardInterrupt` during `backend.generate()` returns a cancelled response dict with `_cancelled: True` and empty content, preventing connection leaks.
2. **Tool execution** -- `KeyboardInterrupt` during `_execute_tool()` marks the tool call as `FAILED`, appends a cancellation step, and breaks the agent step loop. In streaming mode, yields a `RESPONSE_FAILED` SSE event.
3. **Agent step loop** -- Cancelled responses from layer 1 are detected via the `_cancelled` flag, appending an error step and breaking the loop.

`_execute_tool()` now re-raises `KeyboardInterrupt` (was previously caught by `except Exception`), allowing the step loop to handle cancellation cleanly.

---

## Persistent Memory (R04.3)

`core/persistent_memory.py` provides `PersistentMemory`, a SQLite-backed subclass of `Memory` that stores conversation sessions to disk.

**Features**:
- SQLite storage with WAL journal mode for safe concurrent access
- Database stored at `~/.agentkthx/memory.db`
- Session management: `list_sessions()`, `delete_session()`
- Activated via `--session <name>` CLI flag or `session_id` parameter on `Agent`
- Implements the same `Memory` API: `add()`, `get_history()`, `clear()`
- **Must call `agent.memory.close()` on exit** to flush WAL to disk

```python
from agentkthx import Agent

agent = Agent(
    model="qwen2.5:0.5b",
    tools=["calculator"],
    session="my-session",  # Activates PersistentMemory
)

# ... agent runs ...

agent.memory.close()  # Required for clean shutdown
```

```bash
# Activate via CLI
agentkthx chat -m qwen2.5:0.5b --session my-session

# Manage sessions
agentkthx sessions list
agentkthx sessions delete my-session
```

---

## Error Recovery System (R04.1+)

`core/error_recovery.py` provides the `ErrorRecoveryTracker` and helper functions for detecting, contextualizing, and recovering from tool execution failures.

**Components**:

- **`ErrorRecoveryTracker`** -- Tracks consecutive failures per tool across the agentic loop
- **`TOOL_ERROR_HINTS`** -- Tool-specific error hints (e.g., calculator gets Python syntax advice)
- **`TOOL_NAME_SUGGESTIONS`** -- Maps common misspellings to correct tool names
- **`TOOL_ALTERNATIVES`** -- Suggests alternative tools when one fails (e.g., `web_search` when `http_get` fails)

**Key Functions**:

| Function | Purpose |
|----------|---------|
| `is_error_result(result)` | Detects error strings, timeout patterns, and exception messages |
| `build_enhanced_observation(tool_name, result, ...)` | Wraps tool result with contextual hints for small models |
| `build_retry_context(tool_name, args, error)` | Generates retry-with-error-feedback message (ATLAS-inspired) |

See the Retry-with-Error-Feedback section below for full details.

---

## Argument Normalization System (R04.2)

`core/args_normal.py` and `core/prompts.py` provide a comprehensive argument normalization pipeline that helps small models use tools correctly despite natural language variations in argument formatting.

### Tool Argument Aliases (`core/prompts.py`)

`TOOL_ARG_ALIASES` defines ~100+ aliases across 10+ tools, mapping natural language expressions to canonical parameter names:

```python
TOOL_ARG_ALIASES = {
    "calculator": {
        "math_expression": "expression",
        "equation": "expression",
        "calculation": "expression",
        "formula": "expression",
        "compute": "expression",
        "math": "expression",
        # ...
    },
    "shell": {
        "command_to_run": "command",
        "cmd": "command",
        "bash_command": "command",
        "execute": "command",
        # ...
    },
    # ... 10+ more tools
}
```

`CONTEXTUAL_ALIASES` provides disambiguation for aliases that only apply in specific contexts.

### Normalization Pipeline (`core/args_normal.py`)

Three core functions:

| Function | Purpose |
|----------|---------|
| `normalize_args(tool_name, args)` | Full normalization: alias resolution, prefix/substring matching, type coercion |
| `fix_calculator_args(args)` | Calculator-specific fixes: power operation combination, operator normalization |
| `synthesize_missing_args(tool_name, args)` | Synthesizes missing required arguments from context clues |

**Matching strategies**:
- **Exact alias match** -- Direct lookup in `TOOL_ARG_ALIASES`
- **Prefix matching** -- `"math_ex"` matches `"math_expression"`
- **Substring matching** -- `"express"` matches `"expression"`
- **Type coercion** -- Numeric strings converted to int/float, booleans normalized
- **Power operation combination** -- `"to the power of"` and similar patterns mapped to `**` operator in calculator expressions

---

## TurboQuant Server Management (`plugins/turboquant/`) (R04.5, plugin in R05.0)

The TurboQuant plugin provides the `agentkthx turbo` CLI command for managing llama.cpp inference servers. Lazy-loaded on first use.

`plugins/turboquant/turbo.py` manages the lifecycle of a TurboQuant (llama.cpp) inference server for running quantized models.

### TurboState

```python
@dataclass
class TurboState:
    pid: Optional[int]         # Server process ID
    port: int                  # Server port (default: 8764)
    model: Optional[str]       # Loaded model name
    status: str                # "running", "stopped", "unknown"
```

State is persisted to `~/.agentkthx/turbo_state.json`.

### Key Functions

| Function | Description |
|----------|-------------|
| `start_server(model, ...)` | Launch TurboQuant server with detected/optimal KV cache config |
| `stop_server()` | Gracefully stop running server by PID |
| `get_status()` | Return current `TurboState` |

### Model Discovery

Uses `ollama_registry.discover_models()` to find Ollama-compatible models, then:
- Parses GGUF binary headers via mmap to extract weight quantization info
- Auto-detects KV cache configuration from weight quantization (tensor count, head dimensions)
- R06.57: The head_dim compatibility check was removed — empirical testing showed it was wrong (head_dim=64 models work fine). The server itself reports a clear error if a model cannot load.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TURBOQUANT_SERVER_PATH` | `llama-server` | Path to llama-server binary |
| `TURBOQUANT_PORT` | `8764` | Server listen port |
| `TURBOQUANT_CTX` | `8192` | Context window size |

### CLI

```bash
agentkthx turbo list        # List TurboQuant-compatible models
agentkthx turbo start MODEL # Start server with model
agentkthx turbo stop        # Stop running server
agentkthx turbo status      # Show server status
```

---

## Ollama Model Registry (R04.5)

`backends/ollama_registry.py` provides Ollama model discovery and GGUF analysis for TurboQuant compatibility.

### OllamaModel Dataclass

```python
@dataclass
class OllamaModel:
    name: str                # Model name (e.g., "qwen2.5:0.5b")
    path: str                # GGUF blob path
    size: int                # File size in bytes
    file_type: int           # GGUF file_type constant
    quant_name: str          # Human-readable quantization name
    family: str              # Model family
```

### Key Functions

| Function | Description |
|----------|-------------|
| `discover_models()` | Walks Ollama manifest directory, resolves GGUF blob paths |
| `find_model(name)` | Three-tier matching: exact, tag-only, fuzzy substring |
| `_detect_weight_quant(file_type)` | Maps 37+ GGUF `file_type` constants to quantization names |
| `recommended_turbo_config(model)` | Returns optimal KV cache config based on weight quantization |

### GGUF Header Parsing

Uses `mmap` to read GGUF binary headers without loading the full file into memory:
- Reads magic number, version, tensor count, metadata KV pairs
- Extracts `general.architecture`, `llama.context_length`, `llama.attention.head_count`
- Maps file_type integers to quantization names (e.g., `7` = "Q4_0", `15` = "IQ4_XS")

### Three-Tier Model Matching

1. **Exact match** -- Full model name matches
2. **Tag match** -- Matches after `:` (e.g., `"0.5b"` matches `"qwen2.5:0.5b"`)
3. **Fuzzy match** -- Substring match on full name (lowest priority)

---

## Dangerous Tool Confirmation (R04.2)

Tools marked `dangerous=True` require explicit confirmation before execution.

**Dangerous tools**: `shell`, `write_file`, `edit_file`

```bash
# Enable confirmation prompt (interactive y/N)
agentkthx chat --confirm
```

The `Agent` class accepts a `confirm_dangerous` callback that is invoked before executing any dangerous tool. The callback receives the tool name and arguments and returns `True` to proceed or `False` to abort.

---

## Audit Logging (R04.2)

`_audit_log()` writes structured JSON-lines to `~/.agentkthx/audit.log` tracking outcomes of dangerous tool executions.

**Audited tools**: `shell`, `write_file`, `edit_file`

Each log entry records:
- Timestamp
- Tool name and arguments
- Success/failure status
- Result summary

---

## AgentSkills System (`skills/loader.py`)

The skills loader implements the AgentSkills specification with full validation support.

### Skill Validation (R03.5)

The `Skill` dataclass validates all fields during initialization:

```python
from agentkthx.skills import Skill

skill = Skill(
    name="my-skill",                    # 1-64 chars, lowercase, hyphens only
    description="A skill description",  # 1-1024 chars (enforced)
    instructions="...",                 # Markdown body
    path="/path/to/skill",
    license="MIT",                      # Validated against SPDX
    compatibility="python>=3.8"         # Parsed into structured data
)
```

**Validation Methods**:
- `_validate_name()` - Enforces `^[a-z0-9]+(-[a-z0-9]+)*$` format, max 64 chars
- `_validate_description()` - Enforces 1-1024 character limit per spec
- `_validate_license()` - Validates against SPDX identifiers
- `_parse_compatibility()` - Parses requirements into structured dict

### SPDX License Validation

Validates license identifiers against the SPDX license list:

```python
from agentkthx.skills import validate_spdx_license, SPDX_LICENSES

# Validate a license
valid, msg = validate_spdx_license("MIT")
# Returns: (True, "Valid SPDX identifier: MIT")

valid, msg = validate_spdx_license("Apache-2.0 WITH LLVM-exception")
# Returns: (True, "Valid SPDX identifier with exception: Apache-2.0 WITH LLVM-exception")

valid, msg = validate_spdx_license("Proprietary")
# Returns: (False, "Unknown license identifier: Proprietary")

# Common SPDX licenses included
print(SPDX_LICENSES)
# {'MIT', 'Apache-2.0', 'GPL-3.0', 'BSD-3-Clause', 'ISC', 'MPL-2.0', ...}
```

### Compatibility Parsing

Parses skill compatibility requirements into structured data:

```python
from agentkthx.skills import parse_compatibility

# Python version requirement
compat = parse_compatibility("python>=3.8")
# Returns: {"python": ">=3.8", "runtimes": [], "frameworks": []}

# Multiple requirements
compat = parse_compatibility("python>=3.8, ollama, agentkthx>=1.0")
# Returns: {"python": ">=3.8", "runtimes": ["ollama"], "frameworks": ["agentkthx>=1.0"]}
```

### Skill Compatibility Checking

Check if a skill is compatible with the current environment:

```python
from agentkthx.skills import Skill

skill = Skill(
    name="web-search",
    license="MIT",
    compatibility="python>=3.8, ollama"
)

# Check compatibility
is_compatible, warnings = skill.check_compatibility(
    runtime="ollama",
    python_version="3.10"
)

if is_compatible:
    print("Skill is compatible!")
else:
    for warning in warnings:
        print(f"Warning: {warning}")

# Check if license is valid SPDX
if skill.license_valid:
    print(f"License: {skill.license}")
else:
    print(f"License warning: {skill.license_warning}")
```

---

## Data Flow

```
User Prompt
     │
     ▼
┌─────────────┐
│    Agent    │ ── loads soul (optional)
│             │ ── builds system prompt with dynamic tools
│             │ ── creates Response object (OpenResponses)
│             │ ── if session_id → PersistentMemory() → memory.load()
└─────────────┘
     │
     ▼
┌─────────────┐
│   Backend   │ ── sends to Ollama/LlamaServer/BitNet/ZAI (ReAct prompting)
└─────────────┘
     │
     ▼
┌─────────────┐
│ Tool Parser │ ── extracts tool calls from text
│             │ ── handles: ReAct, JSON-wrapped, markdown
│             │ ── extracts final_answer if present
└─────────────┘
     │
     ▼
┌─────────────┐
│ Argument    │ ── normalize_args() resolves aliases
│ Normalizer  │ ── fix_calculator_args() fixes math expressions
│             │ ── synthesize_missing_args() fills gaps
└─────────────┘
     │
     ▼
┌─────────────┐
│Tool Registry│ ── executes tool (if allowed)
│             │ ── confirm_dangerous() for dangerous tools (R04.2)
│             │ ── _audit_log() for dangerous tool outcomes (R04.2)
└─────────────┘
     │
     ▼
┌─────────────┐
│ Error       │ ── is_error_result() detects failures
│ Recovery    │ ── build_enhanced_observation() adds hints
│             │ ── build_retry_context() generates retry message
└─────────────┘
     │
     ▼
┌─────────────┐
│   Memory    │ ── adds Observation (or PersistentMemory for sessions)
└─────────────┘
     │
     ▼ (loop until Final Answer or max_steps)
     │
┌─────────────┐
│   Result    │ ── AgentRun with final_answer
└─────────────┘
```

---

## Tool Calling Strategy

### Unified ReAct Prompting

All models use ReAct prompting regardless of native tool capabilities. This provides:
- Consistent behavior across all models
- Predictable parsing
- Better control for small models

**System Prompt Structure**:
```
# Agent Name
Description

## Core Directives
1. Answer Accurately
2. Follow Instructions
3. Use Tools

### Tool Reference (only use if available)
| Tool | When to use | Arguments |
|------|-------------|-----------|
| `calculator` | Evaluate mathematical expressions | `{"expression": "..."}` |

**CRITICAL RULE**: If a tool is NOT in the available tools list, do NOT try to use it.

## Tool Calling Format (MANDATORY)

When you need to use a tool, output EXACTLY:

Action: <tool_name>
Action Input: <JSON arguments>

**Example**:
Action: calculator
Action Input: {"expression": "15 * 8"}

## Calculator Syntax (CRITICAL)

The calculator uses **Python syntax**. Use these correct formats:

| Natural Language | Correct Python Syntax |
|------------------|----------------------|
| "2 to the power of 10" | `2**10` |
| "square root of 144" | `sqrt(144)` or `144**0.5` |
| "15 times 8" | `15 * 8` |

## After Tool Result - MANDATORY

**IMMEDIATELY after receiving an Observation, output:**

```
Final Answer: <the result>
```

**DO NOT:**
- Call the same tool again with the result
- Call another tool unless you need MORE information

## Error Recovery

If a tool returns an error:
1. STOP and read the error message
2. THINK about what went wrong
3. TRY a different approach - do NOT repeat the same failed call
```

### Enhanced Observation Format

Tool results include contextual guidance to help small models understand the next action:

```python
# Success result - prompts for Final Answer
observation_msg = f"Observation: {result}\n\nNow output: Final Answer: <the result>"

# Error result - prompts for recovery with syntax hint
observation_msg = f"Observation: {error}\n\nNote: Try a different approach. For calculator, use Python syntax (e.g., 2**10 for power)."
```

This guidance is critical for models under 1B parameters that may not understand the ReAct flow without explicit direction.

### No Fallbacks

Following OpenResponses principles, these were removed:
- Greeting short-circuit
- Calculator synthesis for math prompts
- Auto-execution of no-arg tools
- Wrong datetime tool auto-correction
- Empty response retry with hints

The model MUST explicitly format tool calls.

---

## Built-in Tools

17 built-in tools are registered in `tools/builtins.py`:

| Tool | Description | Dangerous | Notes |
|------|-------------|-----------|-------|
| `calculator` | Evaluate mathematical expressions | No | Python syntax, math functions |
| `shell` | Execute shell commands | Yes | Audit logged |
| `read_file` | Read file contents | No | Full file read |
| `read_file_lines` | Read file by line range | No | 500-line cap, line range selection |
| `write_file` | Write/create files | Yes | Audit logged |
| `edit_file` | Search-and-replace in files | Yes | Audit logged |
| `list_directory` | List directory contents | No | |
| `find_files` | Recursive file search | No | fnmatch glob, max_results cap |
| `http_get` | HTTP GET requests | No | |
| `get_time` | Get current time | No | |
| `get_date` | Get current date | No | |
| `python_repl` | Sandboxed Python execution | No | Via sandboxed_repl.py |
| `web_search` | Web search | No | |
| `parse_json` | Parse JSON strings | No | |
| `count_words` | Count words in text | No | |
| `count_chars` | Count characters in text | No | |
| `todo` | In-memory todo CRUD | No | Priority support, module-level store |

### Tool Details

**edit_file**: Search-and-replace operations within files. Marked `dangerous=True` for safety. All operations are audit-logged.

**todo**: In-memory task list with full CRUD operations. Supports priority levels. Store is module-level (shared across invocations within a process). Useful for tracking multi-step tasks.

**read_file_lines**: Reads a specific range of lines from a file. Enforces a 500-line cap per request to prevent excessive memory usage.

**find_files**: Recursive file search using fnmatch glob patterns. Supports `max_results` parameter to cap output and prevent runaway searches.

---

## Dual API Support

AgentKthx supports both OpenResponses and OpenAI Chat-Completions API endpoints through Ollama and LlamaServer. This allows flexibility for different integration scenarios.

### API Modes

| Mode | Flag | Endpoint | Description |
|------|------|----------|-------------|
| **OpenResponses** | `--api openre` | `/api/chat` | OpenResponses API (default for Ollama) |
| **OpenAI** | `--api openai` | `/v1/chat/completions` | OpenAI Chat-Completions API |

### When to Use Each Mode

**OpenResponses (`--api openre`)**:
- Default mode for Ollama-native deployments
- Full OpenResponses specification compliance
- Detailed item tracking with `[OpenResponses]` debug output
- Recommended for AgentKthx-specific applications

**OpenAI Chat-Completions (`--api openai`)**:
- OpenAI-compatible endpoint for cross-platform tools
- Cleaner debug output without OpenResponses internals
- Useful when integrating with OpenAI-compatible clients
- Required when using middleware that expects `/v1/chat/completions`
- LlamaServer default mode (via `/v1/chat/completions`)

### Debug Output by Mode

**OpenResponses mode** shows internal state tracking:
```
[OpenResponses] tool_choice initialized: type=auto
[OpenResponses] Response created: id=resp_...
[OpenResponses] Response status: in_progress
[OpenResponses] Tool calls detected: 1
[OpenResponses] Parsed: name=calculator, args={'expression': '15 + 27'}
```

**Chat-Completions mode** shows API transport only:
```
[Ollama] Dispatching to OpenAI-compatible API (mode=openai)
[OpenAI-Comp] Request: tools=0
[OpenAI-Comp] Content: Action: calculator...
[OpenAI-Comp] Tool calls: []
```

### Usage

```bash
# Default: OpenResponses API
agentkthx chat -m qwen2.5:0.5b

# OpenAI Chat-Completions API
agentkthx chat -m qwen2.5:0.5b --api openai

# LlamaServer backend (uses OpenAI endpoint by default)
agentkthx chat -m qwen2.5:0.5b --backend llama-server

# BitNet backend
agentkthx chat -m bitnet-1.58b --backend bitnet

# With debug output
agentkthx test 01 --api openai --debug
```

### Implementation Details

```python
from agentkthx.backends import get_backend
from agentkthx.core.types import ApiMode

# Ollama - OpenResponses mode (default)
backend = get_backend("ollama", api_mode=ApiMode.OPENRE)

# Ollama - OpenAI Chat-Completions mode
backend = get_backend("ollama", api_mode=ApiMode.OPENAI)

# LlamaServer / TurboQuant
backend = get_backend("llama-server")

# BitNet (thin wrapper around LlamaServer)
backend = get_backend("bitnet")
```

Both modes use ReAct prompting - tool definitions are not passed to the API. The model outputs tool calls in text format, which are parsed by the Tool Parser.

### Chat-Completions Streaming (R03.3)

The Chat-Completions mode supports SSE (Server-Sent Events) streaming for real-time output:

```python
from agentkthx.backends import get_backend
from agentkthx.core.types import ApiMode

backend = get_backend("ollama", api_mode=ApiMode.OPENAI)

# Stream response chunks
for chunk in backend.generate_completions_stream(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
):
    if chunk.get("delta"):
        print(chunk["delta"], end="", flush=True)
    if chunk.get("finish_reason"):
        print(f"\nFinished: {chunk['finish_reason']}")
```

### Chat-Completions Parameters (R03.3+R03.5)

Additional parameters supported in Chat-Completions mode:

| Parameter | Type | Description |
|-----------|------|-------------|
| `stop` | str \| list | Stop sequences (e.g., `["\n", "Observation:"]`) |
| `presence_penalty` | float | Presence penalty (-2.0 to 2.0) |
| `frequency_penalty` | float | Frequency penalty (-2.0 to 2.0) |
| `response_format` | dict | Response format (e.g., `{"type": "json_object"}`) |
| `top_p` | float | Top-p sampling (0.0 to 1.0) |
| `think` | bool \| None | For thinking models (qwen3, deepseek-r1): None=auto, False=disable thinking |
| `tool_choice` | str \| dict | Tool choice mode: `"auto"`, `"none"`, `"required"`, or `{"type": "function", "function": {"name": "..."}}` (R03.5) |
| `logprobs` | bool | Return log probabilities of output tokens |
| `top_logprobs` | int | Number of most likely tokens per position |
| `n` | int | Number of completions to generate |
| `user` | str | End-user identifier for abuse monitoring |

```python
# JSON mode with additional parameters
result = backend.generate_completions(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Return JSON"}],
    response_format={"type": "json_object"},
    temperature=0.7,
    top_p=0.9,
    stop=["\n\n"],
    presence_penalty=0.1
)

# Force tool usage (R03.5)
result = backend.generate_completions(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "What is 15 + 27?"}],
    tools=[calculator_tool],
    tool_choice="required"  # Model MUST call a tool
)

# Force specific tool (R03.5)
result = backend.generate_completions(
    model="qwen2.5:0.5b",
    messages=[{"role": "user", "content": "Calculate something"}],
    tools=[calculator_tool, shell_tool],
    tool_choice={"type": "function", "function": {"name": "calculator"}}
)
```

### Response Fields (R03.5)

The `generate_completions()` method returns a dict with:

| Field | Type | Description |
|-------|------|-------------|
| `content` | str | Generated text content |
| `tool_calls` | list | Parsed tool calls with `id`, `name`, `arguments` |
| `finish_reason` | str | Completion reason: `"stop"`, `"length"`, `"tool_calls"`, `"content_filter"` |
| `usage` | dict | Token counts (`prompt_tokens`, `completion_tokens`, `total_tokens`) |
| `latency_ms` | float | Request latency in milliseconds |
| `logprobs` | dict \| None | Log probabilities (if requested) |
| `raw` | dict | Raw API response |

### Thinking Models Support (R03.4)

For models with extended thinking capabilities (qwen3, deepseek-r1), the `think` parameter controls thinking mode:

```python
# Disable thinking for faster responses (still uses ReAct prompting)
result = backend.generate_completions(
    model="qwen3:0.6b",
    messages=[{"role": "user", "content": "Calculate 2+2"}],
    think=False  # Disable thinking mode
)

# Enable thinking (default for thinking models)
result = backend.generate_completions(
    model="deepseek-r1:1.5b",
    messages=[{"role": "user", "content": "Explain quantum computing"}],
    think=True  # Enable extended thinking
)
```

**Note**: The Agent class automatically handles `think=False` for models that need the `/no_think` directive (qwen3, deepseek-r1) based on model family detection. This ensures optimal performance for tool-calling workflows.

---

## OpenResponses Compliance for Small Models

Small models (under 1B parameters) require additional guidance to comply with the OpenResponses agentic loop. The following enhancements ensure reliable tool usage:

### Soul Prompt Structure

The nova-helper soul includes structured sections that guide small models:

1. **Tool Reference Table** - Dynamic injection of available tools with argument examples
2. **Tool Calling Format** - Explicit Action/Action Input format with examples
3. **Calculator Syntax Table** - Maps natural language to Python syntax
4. **After Tool Result** - MANDATORY Final Answer output rules
5. **Error Recovery** - STOP/THINK/TRY pattern with common errors

### Decision Point Guidance

Each decision point in the agentic loop has explicit guidance:

| Decision Point | Guidance |
|----------------|----------|
| Should I use a tool? | Tool Reference table with "When to use" |
| How to format tool call? | Exact format with example |
| What syntax for calculator? | Natural language to Python syntax table |
| What to do after result? | MANDATORY Final Answer, with DO NOT rules |
| What if tool errors? | Error Recovery section with recovery example |

### Observation Enhancement

The agent adds contextual hints to tool results:

```python
# In the agentic loop (core/agentic_loop.py, non-streaming path) — tool-result
# processing; the streaming path (core/streaming.py) builds the same observation
if result_str.startswith("Error"):
    observation_msg = f"Observation: {result_str}\n\nNote: Try a different approach..."
else:
    observation_msg = f"Observation: {result_str}\n\nNow output: Final Answer: <the result>"
```

This ensures the model always knows what action to take next, preventing common failure modes:
- Re-calling the tool with the result
- Outputting reasoning instead of Final Answer
- Repeating the same failed expression

---

## ACP Integration (`acp_plugin.py`)

AgentKthx implements ACP (Agent Control Panel) v1.0.6 for monitoring, control, and activity logging.

### Features

- **Status reporting** -- Report agent status (idle, working, paused, stopping)
- **Activity logging** -- Log READ, WRITE, EDIT, BASH, SEARCH, API activities
- **STOP flag handling** -- Graceful shutdown when requested
- **A2A messaging** -- Agent-to-Agent JSON-RPC 2.0 support

### Batch Context Manager (R03.3)

Group multiple activities into an atomic batch operation:

```python
from agentkthx.plugins.acp.acp_plugin import ACPPlugin

acp = ACPPlugin(agent_name="CodeAssistant", base_url="http://localhost:8766")

# Batch multiple activities
with acp.batch_context("Read and analyze multiple files") as batch:
    batch.add_read("/src/main.py")
    batch.add_read("/src/utils.py")
    batch.add_read("/src/config.py")
# All activities automatically started and completed as a group

# Mixed activity batch
with acp.batch_context("Refactor operation") as batch:
    batch.add_read("/src/old_module.py")
    batch.add_write("/src/new_module.py")
    batch.add_bash("pytest tests/")
```

### Activity Types

| Activity | Method | Description |
|----------|--------|-------------|
| READ | `add_read(path)` | File read operation |
| WRITE | `add_write(path)` | File write operation |
| EDIT | `add_edit(path)` | File edit operation |
| BASH | `add_bash(command)` | Shell command execution |
| SEARCH | `add_search(query)` | Search operation |
| API | `add_api(url, method)` | API call |

### CLI Usage

```bash
# Enable ACP logging
agentkthx chat --acp

# With custom ACP server
agentkthx agent --acp --acp-url https://tunnel.example.com
```

---

## Model Family Configuration

`core/model_family_config.py` defines behavior for 10 model families:

**Supported Families**: gemma3, granite, granitemoe, qwen2, qwen3, qwen35, llama, dolphin, deepseek-r1, deepseek

### Family Aliases

`_FAMILY_ALIASES` maps alternative family names to canonical families:

```python
_FAMILY_ALIASES = {
    "bitnet": "llama",  # BitNet models use llama prompt formatting
    # ... additional aliases
}
```

This ensures BitNet models (which report `"bitnet"` as their architecture in GGUF headers) get the correct prompt formatting, stop tokens, and behavior configuration.

### Per-Family Configuration

- **Thinking mode** -- Automatically disabled for qwen3 and deepseek-r1 families
- **Stop tokens** -- Family-specific stop sequences
- **Format preferences** -- Template and prompt structure adjustments

---

## CLI Commands

### CLI Package Layout (R07.00)

The CLI lives in the `agentkthx/cli/` package (Phase 8 split of the former 4270-line `cli.py`): shared machinery in 8 top-level modules (`parser`, `agent_factory`, `banner`, `headers`, `utils`, `main`, `__init__`, `__main__`), one module per subcommand under `commands/` (14 modules).

`cli/__init__.py` is a **compatibility facade**: it re-exports every module-level name that existed on the old `cli` module — all `cmd_*` handlers, helpers, constants, `main`, `create_parser` — so `from agentkthx.cli import X` keeps working unchanged. The four collaborators shared across command modules (`_build_agent`, `_init_acp`, `_print_session_header`, `_print_update_notice`) are resolved by command modules **through the facade at call time**, so `monkeypatch.setattr("agentkthx.cli._build_agent", …)` affects all consumers exactly as it did pre-split — the patch-compatibility contract is test-enforced (`tests/test_cli_package_split.py`).

| Command | Description |
|---------|-------------|
| `run` | Run a single prompt |
| `chat` | Interactive chat mode |
| `agent` | Autonomous agent mode |
| `models` | List available models (with tool support status) |
| `tools` | List available tools |
| `test` | Run diagnostic tests |
| `skills` | List available skills |
| `soul` | Inspect a Soul Spec package |
| `config` | Show current configuration |
| `version` | Show version info |
| `turbo` | TurboQuant server management (list/start/stop/status) |
| `sessions` | List/delete persistent memory sessions |
| `modelfile` | Show model's Modelfile info |
| `update` | Self-update from GitHub |

## Common Options

| Option | Commands | Description |
|--------|----------|-------------|
| `-m, --model` | run, chat, agent, test | Model to use |
| `--tools` | run, chat, agent | Comma-separated tool list |
| `--skills` | run, chat, agent | Comma-separated skill names to load |
| `--backend` | all | Backend (ollama, bitnet, llama-server, zai, openrouter, gemini, huggingface/hf) |
| `--api` | run, chat, agent, test | API mode: `openre` (OpenResponses) or `openai` (OpenAI Chat-Completions) |
| `--response-format` | run, chat, agent | Response format: `text` or `json` (Chat-Completions mode) |
| `--truncation` | run, chat, agent | Truncation behavior: `auto` or `disabled` |
| `--soul` | run, chat, agent | Path to Soul Spec package |
| `--soul-level` | run, chat, agent | Progressive disclosure (1-3) |
| `--num-ctx` | run, chat, agent, test | Context window size (default: 4096) |
| `--num-predict` | run, chat, agent | Maximum tokens to generate |
| `--timeout` | run, chat, agent, test | Request timeout (seconds) |
| `--acp` | run, chat, agent, test | Enable ACP logging |
| `--acp-url` | run, chat, agent, test | ACP server URL |
| `--no-retry` | run, chat, agent | Disable retry-with-error-feedback on tool failures |
| `--max-retries N` | run, chat, agent | Maximum retries per tool call failure, default 2 |
| `--confirm` | run, chat, agent | Require confirmation for dangerous tools |
| `--session <name>` | run, chat, agent | Activate persistent memory session |
| `--force-react` | run, chat, agent | Force ReAct text-based tool calling |
| `--stream` | run | Stream output (run command) |
| `-q, --quiet` | all | Suppress header and summary |
| `-v, --verbose` | all | Verbose output |
| `--debug` | run, chat, agent, test | Enable debug output |

## Models Command Options

| Option | Description |
|--------|-------------|
| `--tool-support` | Test each model's tool support and cache results |
| `--no-cache` | Ignore cached tool support results |

```bash
# List models with cached tool support status
agentkthx models

# Test tool support for all models (caches results)
agentkthx models --tool-support

# Re-test ignoring cache
agentkthx models --tool-support --no-cache
```

---

## Example: Complete Tool Call Flow

```
User: "What is 15 times 8?"

Step 1: Model generates
-------------------------
Action: calculator
Action Input: {"expression": "15 * 8"}
Final Answer: 120

Step 2: Parser extracts
-------------------------
[OpenResponses] Tool calls detected: 1
[OpenResponses] Parsed: name=calculator, args={'expression': '15 * 8'}, final_answer=120

Step 3: Tool executed
---------------------
Tool: calculator({'expression': '15 * 8'})
Result: 120

Step 4: Final answer used
-------------------------
[OpenResponses] Model provided final_answer with tool call
[OpenResponses] Using final_answer: 120

Result: AgentRun(final_answer="120", tool_calls=1, success=True)
```

### Example: Small Model with Enhanced Observation

Small models (under 1B params) receive additional guidance in the Observation:

```
User: "What is 2 to the power of 10?"

Step 1: Model generates (with correct syntax from soul prompt)
--------------------------------------------------------------
Action: calculator
Action Input: {"expression": "2**10"}

Step 2: Tool executed
---------------------
Tool: calculator({'expression': '2**10'})
Result: 1024

Step 3: Enhanced Observation added to memory
---------------------------------------------
Observation: 1024

Now output: Final Answer: <the result>

Step 4: Model generates Final Answer
-------------------------------------
Final Answer: 1024

Result: AgentRun(final_answer="1024", tool_calls=1, success=True)
```

### Example: Error Recovery

When a tool error occurs, the Observation includes recovery guidance:

```
User: "What is 2 to the power of 10?"

Step 1: Model generates (incorrect syntax)
------------------------------------------
Action: calculator
Action Input: {"expression": "2 to the power of 10"}

Step 2: Tool error
------------------
Tool: calculator({'expression': '2 to the power of 10'})
Result: Error evaluating expression: invalid syntax

Step 3: Enhanced Observation with recovery hint
-----------------------------------------------
Observation: Error evaluating expression: invalid syntax

Note: Try a different approach. For calculator, use Python syntax (e.g., 2**10 for power, sqrt(144) for roots).

Step 4: Model recovers with correct syntax
------------------------------------------
Action: calculator
Action Input: {"expression": "2**10"}

Step 5: Success
---------------
Observation: 1024

Now output: Final Answer: <the result>

Final Answer: 1024
```

---

## Configuration

### Default Values

| Setting | Default | Description |
|---------|---------|-------------|
| `num_ctx` | 4096 | Context window size for Ollama |
| `max_steps` | 10 | Maximum reasoning steps |
| `tool_choice` | "auto" | Tool invocation mode |
| `soul_level` | 3 | Soul disclosure level |
| `temperature` | 0.7 | Model temperature (varies by model) |
| `retry_on_error` | `true` | Retry failed tool calls with error feedback |
| `max_tool_retries` | 2 | Maximum retries per tool call failure |
| `LLAMA_SERVER_BASE_URL` | `http://localhost:8764` | LlamaServer / TurboQuant endpoint |
| `TURBOQUANT_SERVER_PATH` | `llama-server` | Path to llama-server binary |
| `TURBOQUANT_PORT` | `8764` | TurboQuant listen port |
| `TURBOQUANT_CTX` | `8192` | TurboQuant context window size |
| `AGENTKTHX_RETRY_ON_ERROR` | `true` | Enable retry context injection (env var) |
| `AGENTKTHX_MAX_TOOL_RETRIES` | `2` | Maximum retries per tool failure (env var) |

### Model-Specific Configs

Model configurations are defined in `core/model_config.py`:
- Temperature defaults
- Max token limits
- Stop sequences

Family-specific behavior in `core/model_family_config.py`:
- Thinking mode (disabled for qwen3, deepseek-r1)
- Format preferences
- Family aliases (e.g., `bitnet` to `llama`)

---

### Retry-with-Error-Feedback (R04.1)

When a tool call fails, the agent can optionally inject a **retry context** message into the conversation, giving the model a chance to correct its arguments before giving up. This feature was inspired by the [ATLAS-Autonomous](https://github.com/itigges22/ATLAS) benchmark infrastructure.

**How it works**:

```
Tool call fails -> is_error_result() detects error
                -> build_retry_context() generates hint message
                -> Follow-up user message injected into memory
                -> Model receives previous attempt + correction instruction
                -> Model retries with corrected arguments (or tries different approach)
```

**Retry context format**:
```
--- Retry Context ---
Previous attempt: calculator({"expression": "10/0"})
The tool returned an error. Please try again with corrected arguments.
```

After 2+ failures on the same tool, the message escalates:
```
This tool has failed N times. Consider using a different tool or approach.
```

**Configuration**:

| Setting | Default | Description |
|---------|---------|-------------|
| `retry_on_error` | `true` | Enable/disable retry context injection |
| `max_tool_retries` | `2` | Maximum retries before stopping retry injection |

| Control | CLI Flag | Env Var | Programmatic |
|---------|----------|---------|-------------|
| Enable/disable | `--no-retry` | `AGENTKTHX_RETRY_ON_ERROR` | `Agent(retry_on_error=...)` |
| Max retries | `--max-retries N` | `AGENTKTHX_MAX_TOOL_RETRIES` | `Agent(max_tool_retries=...)` |

**Dual-path support**:
- **Native tool calls**: After `memory.add_tool_result()`, retry context is injected as a follow-up user message
- **ReAct text path**: Retry context is embedded within the enhanced observation via `build_enhanced_observation()`
- **Streaming path**: Same enhanced observation handling as ReAct path

**Guardrails**:
- Retry context is not injected when consecutive failures exceed `max_tool_retries`, preventing infinite retry loops
- Timeout detection (`is_error_result()`) catches both explicit errors and timeout patterns

---

## Error Handling

### Tool Not Allowed
```
Tool: read_file
Result: Error: Unknown tool 'read_file'. Available tools: ['get_time', 'get_date']
```

### Tool Execution Error
```
Tool: calculator({})
Result: Error: calculator() missing 1 required positional argument: 'expression'
```

### Max Steps Reached
```
Response status: incomplete
Output: "Maximum steps reached without final answer"
```
