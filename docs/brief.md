# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-04-15 | Auditor: Super-Z | Commit: 91bc48e (R04.7)

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for running AI agents with Ollama, BitNet, llama-server/TurboQuant, or ZAI cloud backends. Includes TurboQuant server lifecycle management, Ollama model registry, and a skill/soul persona system. |
| **Tech Stack** | Python 3.9+, zero runtime dependencies (stdlib only: urllib, json, subprocess, dataclasses, threading, sqlite3, ast, mmap, struct, concurrent.futures) |
| **Entry Point** | `agentkthx/cli.py:main` (CLI) or `from agentkthx import Agent` (Python API) |
| **Build/Run** | `pip install -e ".[dev]"` (dev: pytest, black, ruff) |
| **Test Command** | `pytest` (unit) or `python -m agentkthx.examples.01_quick_diagnostic` (integration) |
| **Package** | PyPI: `agentkthx` · CLI entry points: `agentkthx` + `localclaw` (backward-compat) |
| **Version** | 0.4.7 (R04.7) · Status: Alpha |

---

## Architecture Map

```
agentkthx/
├── core/           → Data types, models, memory, tool parsing, security helpers, OpenResponses spec, persistent memory (SQLite), model family config
├── tools/          → Tool registry (decorator-based), 17 built-in tools, sandboxed Python REPL
├── backends/       → LLM inference backends (Ollama, LlamaServer, ZAI cloud) with dual API (OpenResponses + OpenAI Chat-Completions)
├── backends/ollama_registry.py → Ollama model registry: discovers models from manifests, resolves GGUF blob paths, reads binary headers via mmap (architecture, head_dim, quantization, context_length), TurboQuant compatibility checking
├── backends/zai.py → ZAI cloud backend: Bearer auth, dynamic model discovery, 13 GLM models, free-only mode, credit-exhaustion auto-fallback, tool rejection fallback
├── skills/         → AgentSkills spec loader (YAML frontmatter, SPDX license validation, compatibility checking)
├── soul/           → Soul Spec v0.5 loader (persona packages with 3-level progressive disclosure)
├── souls/          → Pre-built soul packages: nova-helper (diagnostic), nova-skills (skill-guided), nova-trading (TSX/TSX-V quant analyst)
├── examples/       → 12 benchmark/test suites (basic agent through analogical reasoning, GSM8K 50q)
agent.py            → Core Agent class: OpenResponses agentic loop (prompt → tool → observe → repeat), native tool calling support
agent_mode.py       → Autonomous agent mode: state machine (IDLE → WORKING → PAUSED → STOPPING) with rollback, memory isolation
orchestrator.py     → Multi-agent orchestration: router (keyword/LLM), pipeline (sequential), parallel (threaded)
turbo.py            → TurboQuant server lifecycle manager: start/stop/status detached llama-server processes, persistent state (PID file + JSON state), schema versioning
acp_plugin.py       → ACP v1.0.6: status reporting, activity logging, A2A messaging, batch context manager, health tracking
cli.py              → Full CLI: run, chat, agent, models, tools, test, soul, config, version, modelfile, skills, sessions, update, turbo (2143 lines)
config.py           → Centralized config from env vars. Factory functions for dynamic defaults. ACP + ZAI credentials. TurboQuant config.
shared_args.py      → Shared CLI argument definitions (DRY for run/chat/agent parsers) + SharedConfig dataclass
colors.py           → Shared ANSI color utilities (Color class, pad_colored, visible_len)
model_discovery.py  → Ollama model listing, fuzzy model name matching, benchmark model selection
```

### Skip List

- `localclaw/`, `localclaw-redirect/` — legacy backward-compat redirects, just re-exports
- `audit/` — contains audit page images, not code
- `tests/` — standard pytest unit tests, not critical for framework understanding
- `.git/`, `AgentKthx.ipynb` — Colab notebook, not core code
- `patches/` — TurboQuant patches for external projects
- All `__pycache__/` directories

---

## Critical Files Index

### `agentkthx/agent.py` — Core Agentic Loop (~1608 lines)
- **Purpose**: Implements the entire agentic loop. Every tool call, Final Answer extraction, error recovery, and tool_choice enforcement flows through here.
- **Blast radius**: Imported by `cli.py`, `orchestrator.py`, `agent_mode.py`. Every CLI command creates an Agent instance.
- **Key signatures**:
  ```python
  class Agent:
      def __init__(self, model, tools=None, backend=None, max_steps=5,
                   memory_config=None, debug=False, system_prompt=None,
                   soul="nova-helper", soul_level=3, num_ctx=None,
                   temperature=None, top_p=None, num_predict=None,
                   tool_choice="auto", allowed_tools=None, skills_prompt=None,
                   retry_on_error=True, max_tool_retries=2, **kwargs):
      def run(self, prompt: str, stream: bool = False) -> AgentRun:
  ```
- **R04.7 critical change**: `_is_comp_mode` property (checks `backend.api_mode == ApiMode.OPENAI`) now gates 40+ code paths. `_build_default_prompt()` has 4 code paths: no-tools, BitNet (ultra-lean <500 chars), comp-mode (no ReAct format — native tool calling), default (full ReAct).
- **R04.7 fix**: When `_is_comp_mode` is True AND model has native tool support, ReAct format instructions (`Action:/Action Input:`) are suppressed in both the default prompt path and soul loader path. This was a critical bug — native-capable models (e.g. glm-4.5-flash) were forced into text ReAct by system prompt injection, losing parallel tool calls and structured argument typing.
- **Gotchas**: `tools` param accepts 4 types (`ToolRegistry | list[str] | list[Tool] | None`). `response_format` and tools are **mutually exclusive** (lines 232-236). BitNet detection checks MODEL FAMILY via `detect_family(model)`, NOT backend type. Non-BitNet models on the BitNet backend get full context/prompts.

### `agentkthx/backends/zai.py` — ZAI Cloud Backend (814 lines) **[NEW in R04.6, expanded R04.7]**
- **Purpose**: Cloud backend for ZAI API (api.z.ai). OpenAI Chat-Completions compatible with Bearer auth, dynamic model discovery, free-only mode, and auto-fallback on credit exhaustion.
- **Blast radius**: Imported by `backends/__init__.py` (registered as `"zai"`), `__init__.py` (public export), `cli.py` (all subcommands).
- **Inheritance quirk**: Calls `super(OllamaBackend, self).__init__()` — skips Ollama's init, goes straight to `BaseBackend`. Reuses OpenAI completion logic from parent without Ollama server setup.
- **Key types**:
  ```python
  ZAI_MODELS: dict  # 13 GLM models with context_length + pricing metadata
  ZAI_BASE_URL = "https://api.z.ai"
  ZAI_API_KEY  # from env (required)
  ZAI_FREE_ONLY  # env: restrict to free models
  ZAI_FREE_FALLBACK_MODEL = "glm-4.5-flash"
  ```
- **Auto-fallback** (R04.7): On HTTP 429 with "insufficient balance" → retries with free fallback model. On "does not support tools" error → retries with tools stripped.
- **Free models**: `glm-4.5-flash`, `glm-4.7-flash` (pricing 0.0/0.0).
- **Always OPENAI mode**: Forces `ApiMode.OPENAI`, ignores `--api openre`.
- **Endpoints**: `POST /api/paas/v4/chat/completions`, `GET /api/paas/v4/models`.

### `agentkthx/turbo.py` — TurboQuant Server Lifecycle Manager (694 lines) **[R04.5, updated R04.6]**
- **Purpose**: End-to-end lifecycle management for llama-cpp-turboquant server. Detached subprocess management, persistent state, auto KV cache detection.
- **R04.6 additions**: TurboState schema versioning (`_version: int = 1`). `load()` rejects files from newer AgentKthx versions. Forward-compatible: ignores unknown keys. Server logs now append to `~/.agentkthx/turbo.log` (was DEVNULL).
- **Blast radius**: Imported by `cli.py` (cmd_turbo). Standalone module.
- **Gotchas**: Server starts detached (`start_new_session=True`). Health check polls `/health` endpoint. Reads Ollama manifests directly from `~/.ollama/models/`. No dependency on Ollama being running.

### `agentkthx/backends/ollama_registry.py` — Ollama Model Registry (481 lines) **[R04.5]**
- **Purpose**: Discovers Ollama models by reading manifest files, resolves GGUF blob paths, reads binary headers via `mmap`. Provides TurboQuant compatibility checking and recommended KV cache configuration.
- **Blast radius**: Imported by `turbo.py` (primary consumer). Standalone module.
- **Gotchas**: `_GGUF_MAGIC = 0x46554747` (little-endian "GGUF"). `_parse_ollama_name()` handles `library/repo:tag` format. `_filename_heuristic()` has 47 candidate patterns. `_TURBO_D = 128` (TurboQuant head_dim minimum).

### `agentkthx/core/tool_parse.py` — Tool Call Extraction (688 lines)
- **Purpose**: Parses tool calls from model text output in 4+ formats. Central to how ReAct tool calling works.
- **Blast radius**: Only imported by `agent.py`, but critical to it.
- **Supported formats**: Plain ReAct, JSON-wrapped ReAct, Markdown code block JSON, simultaneous tool call + Final Answer.
- **R04.4 change**: Added `ast.literal_eval` fallback for single-quote Python dicts.
- **Gotchas**: If model outputs both a tool call AND `Final Answer:`, the Final Answer takes priority.

### `agentkthx/core/helpers.py` — Fuzzy Matching, Arg Normalization, Security
- **Purpose**: God-module for small model support. Fuzzy tool name matching, argument normalization, security utilities, repetition detection.
- **Blast radius**: Imported by `builtins.py`, `tool_parse.py`, `agent.py`, and transitively everything.
- **Gotchas**: `sanitize_command()` returns the ORIGINAL command unmodified — security is purely rejection-based. `validate_path()` allows `/tmp`, `/home`, and system temp dirs.

### `agentkthx/core/model_family_config.py` — Unified Model Family Configuration (530 lines)
- **Purpose**: Single source of truth for all model-family-specific settings: stop tokens, prompt formatting, tool format, temperature, thinking modes. 10 families: gemma3, granite, granitemoe, qwen2, qwen3, qwen35, llama, dolphin, deepseek-r1, deepseek.
- **Blast radius**: Imported by `agent.py`, `backends/ollama.py`, `backends/llama_server.py`, `core/tool_parse.py`.
- **Gotchas**: `detect_family()` returns most specific family string (e.g. "qwen2.5") but `FAMILY_CONFIGS` stores base families. `get_family_config()` bridges via partial matching. Family alias: `bitnet → llama`.

### `agentkthx/core/prompts.py` — System Prompt Templates (386 lines)
- **Purpose**: Tool argument aliases (~100+), few-shot examples, system prompt builders.
- **R04.7 change**: `get_tool_prompt(tools, tool_support, family)` — the `tool_support` parameter is now **functional**. When `tool_support` is `"native"` or `"openai"`, ReAct format instructions and `FEW_SHOT_COMPACT` examples are skipped. Tool reference table still included.
- **Gotchas**: `TOOL_ARG_ALIASES` maps natural language to canonical param names. `CONTEXTUAL_ALIASES` applied only when no real params matched.

### `agentkthx/soul/loader.py` — Soul Loader (1067 lines)
- **Purpose**: ClawSouls Soul Spec v0.5 parser. Progressive disclosure (3 levels), dynamic tool injection into system prompts.
- **R04.7 change**: `build_system_prompt_with_tools()` accepts `native_tools` parameter. When True, emits tool reference table without `Action:/Action Input:` format block and skips `_build_dynamic_examples()`.
- **Gotchas**: `_build_dynamic_examples()` generates 9 tool-type-specific example flows. `_parse_frontmatter()` is a hand-rolled YAML parser (no PyYAML). Path resolution checks 5 locations including `importlib.resources` for Windows pip.

### `agentkthx/backends/ollama.py` — Primary Local Backend
- **Purpose**: Dual API backend supporting OpenResponses (`/api/chat`) and OpenAI Chat-Completions (`/v1/chat/completions`).
- **Blast radius**: Parent class of `LlamaServerBackend` and `ZaiBackend`. Instantiated by `get_backend()` in cli.py and orchestrator.py.
- **Gotchas**: Tool support detection is per-model (NOT per-family). Cache: `~/.cache/agentkthx/tool_support.json`.

### `agentkthx/backends/llama_server.py` — llama-server / BitNet Backend
- **Purpose**: `LlamaServerBackend(OllamaBackend)` for llama.cpp / TurboQuant. BitNetBackend is a 63-line thin wrapper.
- **R04.6 change**: `BackendType.LLAMA_SERVER` (was `CUSTOM`). `_is_actual_bitnet` checks `detect_family(model)`, not `self._bitnet_mode`.
- **Gotchas**: Default port `8764` (changed from 8080 in R04.5). Non-BitNet models on BitNet backend receive full context and family-correct formatting.

### `agentkthx/tools/builtins.py` — All 17 Built-in Tools (~1170 lines)
- **Purpose**: calculator, shell, read_file, write_file, edit_file, list_directory, http_get, python_repl, web_search, parse_json, count_words, count_chars, read_file_lines, find_files, get_time, get_date, todo.
- **R04.6 change**: Per-session todo isolation — `_todo_stores: dict[str, list[dict]]` keyed by session_id.
- **R04.6 GOTCHA**: Per-session isolation **not wired up** — `_get_todo_store(session_id)` accepts session_id but no caller passes one. All operations use `"default"` store.
- **Security**: Calculator uses `eval()` with `{"__builtins__": {}}`. Shell uses `shell=True` with `sanitize_command()`. File ops use `validate_path()`. HTTP uses `is_safe_url()`. Python REPL runs in sandboxed subprocess. Response limits: files 512KB, HTTP 256KB.
- **Audit logging**: `shell()`, `write_file()`, `edit_file()` log to `~/.agentkthx/audit.log` (JSON-lines, fire-and-forget).
- **Dangerous flag**: `shell`, `write_file`, `edit_file` have `dangerous=True`. Enforced by `confirm_dangerous` callback.

### `agentkthx/cli.py` — Full CLI (2143 lines)
- **Commands**: run, chat, agent, config, models, modelfile, sessions, skills, soul, test, tools, turbo, update, version.
- **R04.7 chat UX overhaul**: 8 slash commands (/help, /status, /system, /tools, /model, /debug, /clear, /quit), braille spinner on stderr, persistent emoji status footer bar with token counting, grey `You:` prompt, response spacing, /status crash fix, /help reformatted to two-column layout.
- **R04.7 chat footer**: `⚛️ R04.7 🧠 glm-4.5-flash 📦 125K 💬 8K 🌡️ 0.1 🔌 zai 📈 ↑1.2k ↓0.8k`. Shows version, model, context, max tokens, temperature, backend, cumulative session token usage.
- **R04.7 token tracking**: Accumulates `_session_tokens_in`/`_session_tokens_out` using ~60/40 split on `step.tokens_used`.
- **Gotchas**: Footer version string is hardcoded (`'R04.7'`), not derived from `__version__`. `cmd_models()` checks tool support cache before testing; `--no-cache` forces re-test.

### `agentkthx/config.py` — Central Configuration (265 lines)
- **Purpose**: Single source of truth for all URLs, defaults, security settings, ACP + ZAI credentials, TurboQuant config.
- **Key defaults**: `OLLAMA_BASE_URL` (localhost:11434), `ZAI_BASE_URL` (api.z.ai), `LLAMA_SERVER_BASE_URL` (localhost:8764), `BITNET_BASE_URL` (localhost:8765), `DEFAULT_MODEL` (backend-dependent: qwen2.5:0.5b for Ollama, glm-5.1 for ZAI).
- **ZAI env vars**: `ZAI_API_KEY` (required), `ZAI_FREE_ONLY` (default false), `ZAI_FREE_FALLBACK_MODEL` (default glm-4.5-flash).
- **Gotchas**: Module-level constants set at import time, but `Config` dataclass fields use `default_factory` for dynamic re-evaluation. `get_config(reload=True)` forces fresh read.

### `agentkthx/core/persistent_memory.py` — SQLite-Backed Persistent Memory
- **Purpose**: `PersistentMemory(Memory)` subclass. WAL mode. Session management. Same sliding-window behavior as in-memory `Memory`.
- **Gotchas**: `close()` must be called on exit or Ctrl+C to flush WAL. CLI handles this. Python API users MUST call `agent.memory.close()`.

### `agentkthx/orchestrator.py` — Multi-Agent Orchestration (488 lines)
- **Modes**: Router (keyword/LLM), Pipeline (sequential), Parallel (ThreadPoolExecutor).
- **Gotchas**: `_select_agent_with_llm()` hardcodes `get_backend("ollama")`. Pipeline appends `[Previous output: ...]` as plain text.

### `agentkthx/agent_mode.py` — Autonomous Agent Mode (855 lines)
- **State machine**: IDLE → WORKING → PAUSED → STOPPING with pause/resume/rollback.
- **Gotchas**: `reset_memory_between_steps` parameter (default False). `_inject_context()` prepends `[Step N of M] Goal: ...` headers.

---

## Request / Execution Lifecycle

```
User Prompt
    │
    ▼
1. Agent.__init__()                                           # agent.py
   ├── BitNet detection: detect_family(model) == "bitnet"    # model_family_config.py
   │   └── (NOT backend type — non-BitNet on BitNet backend OK)
   ├── If _is_bitnet: tighten memory (max=6, keep_recent=4)
   ├── If session_id or persistent: PersistentMemory()        # core/persistent_memory.py
   ├── If soul specified: soul/loader.py → load_soul()
   │   ├── build_system_prompt_with_tools(soul, tools, native_tools)  # R04.7: native_tools flag
   │   └── soul.allowed_tools filters tool registry
   ├── If skills specified: SkillLoader → SkillRegistry
   ├── If response_format set → clear tools, force tool_choice="none"
   ├── If _is_bitnet: lean default prompt (<500 chars)
   ├── If _is_comp_mode + native tools: simplified prompt (no ReAct format)  # R04.7 FIX
   ├── Create ToolParser(tools.names())
   ├── Memory.add("system", system_prompt)
   └── Initialize ErrorRecoveryTracker
    │
    ▼
2. Agent.run(prompt)
   ├── Create OpenResponses Response object
   ├── Memory.add("user", prompt)
    │
    ▼
3. Agentic Loop (for step in range(max_steps))                  # Default: 5 steps
   ├─ 3a. Backend.generate(memory.get_messages())
   │       ├── If api_mode=OPENRE:  POST /api/chat (Ollama) or /completion (llama-server)
   │       └── If api_mode=OPENAI: POST /v1/chat/completions (all backends)
   │              └── ZAI: adds Bearer auth, free-only check, credit fallback
   │
   ├─ 3b. Check finish_reason (length → incomplete, content_filter → failed)
   │
   ├─ 3c. Check for tool calls (two sources):
   │       ├── NATIVE: backend returns tool_calls in response JSON
   │       └── REACT: ToolParser.parse(content) extracts from text
   │
   ├─ 3d. If tool calls found:
   │       ├── Final Answer Enforcement: if _expecting_final_answer, force last result
   │       ├── Check confirm_dangerous callback → block if denied
   │       ├── Check allowed_tools → block if not in list
   │       ├── Execute via _execute_tool(name, args, prompt)
   │       ├── Track success/failure in ErrorRecoveryTracker
   │       └── Continue agentic loop
   │
   ├─ 3e. Check for "Final Answer:" in content
   │       ├── Enforce tool_choice=required if no tools called yet
   │       └── Extract → create MessageItem → break
   │
   └─ 3f. No tool call, no Final Answer → accept as direct response
```

---

## Dependency Graph

```
cli.py ─────────────────────────────────────────────────────────────┐
  ├──→ agent.py ────────────────────────────────────────────────────────┤
  │    ├── core/memory.py                                           │
  │    ├── core/persistent_memory.py → core/memory.py, sqlite3        │
  │    ├── core/tool_parse.py ─→ core/models.py                     │
  │    ├── core/error_recovery.py                                     │
  │    ├── core/openresponses.py ─→ core/models.py                    │
  │    ├── core/helpers.py ─→ (security for all tool operations)      │
  │    ├── core/model_family_config.py                                │
  │    ├── core/prompts.py (now respects tool_support parameter)      │
  │    ├── tools/registry.py ─→ tools/builtins.py                    │
  │    └── soul/loader.py ─→ soul/types.py                           │
  │                                                                    │
  ├──→ orchestrator.py ─→ agent.py (creates Agent instances)           │
  ├──→ agent_mode.py ──→ agent.py (wraps in state machine)           │
  ├──→ skills/loader.py (via _load_skills_prompt, optional)          │
  │                                                                    │
  ├──→ turbo.py ──→ backends/ollama_registry.py                      │
  │                                                                    │
  └──→ backends/ ─────────────────────────────────────────────────────┤
       ├── ollama.py (base for local backends)                        │
       ├── llama_server.py → ollama.py (BitNet = thin wrapper)       │
       ├── zai.py → ollama.py (cloud backend, Bearer auth)           │
       └── ollama_registry.py (TurboQuant model discovery)            │
                                                                       │
config.py ← referenced by EVERYTHING above                             │
shared_args.py ← referenced by cli.py                                  │
core/types.py ← referenced by agent.py, backends, tool_cache          │
acp_plugin.py ← referenced by cli.py (optional import)                │
```

**Highest blast radius changes:**
1. `config.py` — changing a URL or default affects the entire system
2. `core/helpers.py` — changing security behavior affects all built-in tools
3. `agent.py` — changing the agentic loop affects all modes
4. `core/model_family_config.py` — changing family config affects ALL model interactions
5. `core/prompts.py` — changing tool prompt affects ReAct/native behavior for all models
6. `backends/zai.py` — changing fallback behavior affects ZAI credit/tool handling

---

## Patterns & Conventions

| Aspect | Pattern |
|---|---|
| API specification | OpenResponses (openresponses.org) — Items, Response state machine, tool_choice modes |
| Tool calling (ReAct) | `Action: name\nAction Input: {json}` format for models without native support |
| Tool calling (Native) | API body `tool_calls` for models with native function calling (R04.7 fix) |
| Native/ReAct gating | `_is_comp_mode` + `tool_support` level → suppresses ReAct format in system prompt |
| Error recovery | Retry-with-error-feedback: inject previous failure context. Max 2 retries. |
| Small model support | Fuzzy matching (0.4 threshold), arg normalization, `ast.literal_eval`, repetition detection |
| Security | Defense-in-depth: command blocklist, path whitelist, SSRF protection, sandboxed REPL, response limits, dangerous tool confirmation, audit logging |
| Backend extensibility | Abstract `BaseBackend` + `_BACKENDS` dict + `register_backend()`. 4 backend classes: Ollama, LlamaServer, BitNet (wrapper), ZAI |
| ZAI resilience | Free-only mode (env gate), credit-exhaustion auto-fallback (429→free model), tool rejection fallback (strip tools→retry) |
| Persistent memory | SQLite WAL. Session management via `--session <name>`. Must call `agent.memory.close()`. |
| TurboQuant integration | Zero-conversion Ollama usage. mmap GGUF headers. head_dim >= 128. Detached server lifecycle. Schema versioning. |
| Chat UX | Slash commands, braille spinner (stderr, threaded), emoji status footer with token tracking, grey prompt |
| Optional imports | ACP, Soul, PersistentMemory wrapped in `try/except`. Check `if X is not None`. |
| Zero runtime deps | Python stdlib only — urllib for HTTP, sqlite3 for persistence, mmap for binary parsing |

---

## Known Landmines

### Native tool calling requires `--api openai` (R04.7)
```
# ReAct format is ONLY suppressed when api_mode == ApiMode.OPENAI
# Using --api openre with a native-capable model still gets ReAct instructions
agentkthx chat --backend zai --model glm-4.5-flash   # OK: ZAI forces OPENAI
agentkthx chat --backend ollama --model llama3 --api openai  # OK: comp mode
agentkthx chat --backend ollama --model llama3 --api openre  # BUG: ReAct overrides native
```

### ZAI auto-fallback silently swaps models
```python
# backends/zai.py: generate() → on 429 "insufficient balance" → retries with glm-4.5-flash
# User requests glm-5.1 but gets glm-4.5-flash without explicit notification
# Only a debug warning is printed
```

### Todo per-session isolation is NOT wired up
```python
# tools/builtins.py: _get_todo_store(session_id="default") — session_id param exists
# BUT: no caller passes session_id. All operations use "default" store.
# Infrastructure ready, Agent doesn't pass session_id through tool invocations.
```

### Chat footer version is hardcoded
```python
# cli.py: f"{dim(_e_brand)} {cyan('R04.7')}"
# Should derive from __version__ dynamically. Will drift on next release.
```

### `calculator()` uses `eval()` — sandboxed but still eval
```python
result = eval(expression, {"__builtins__": {}}, safe_dict)
# MAX_EXPONENT=10000 guards against DoS. Adding os/sys/__import__ breaks sandbox.
```

### `shell()` runs with `shell=True` — rejection-based security
```python
# sanitize_command() REJECTS dangerous commands but does NOT modify input.
# Any bypass of the blocklist allows arbitrary execution.
```

### Memory pruning drops messages without summarization
```python
# _prune_if_needed() triggers at max_messages * 0.8 (default 40)
# Just DROPS old messages — no summarization.
```

### `PersistentMemory.close()` must be called on exit
```python
# CLI handles this, but Python API users who create Agent(session_id="x")
# MUST call agent.memory.close() or risk WAL growth and locked files.
```

### `response_format` and tools are mutually exclusive
```python
# Setting response_format clears all tools and forces tool_choice="none"
# agent.py lines 232-236.
```

### BitNet constraints gated by MODEL FAMILY, not backend type
```python
# agent.py: self._is_bitnet = (detect_family(model) == "bitnet")
# A qwen2.5 model on --backend bitnet gets FULL context, NO truncation.
```

### Web search depends on HTML scraping — fragile
```python
# Scrapes DuckDuckGo Lite with regex. Any HTML change silently breaks web search.
```

### ZAI `ZaiBackend` skips parent init
```python
# zai.py: super(OllamaBackend, self).__init__() — bypasses OllamaBackend.__init__
# Goes straight to BaseBackend. Fragile if OllamaBackend.__init__ gains state.
```

### `_parse_frontmatter()` limited YAML support (skills/loader.py)
```python
# Custom parser — no PyYAML. Handles: key: value, key: "quoted", multiline.
# DOES NOT handle: nested objects, arrays, null, booleans, numbers without quotes.
```

### `LLAMA_SERVER_BASE_URL` default is 8764 (was 8080)
```python
# Pre-R04.5: http://localhost:8080
# R04.5+: http://localhost:8764
# Set LLAMA_SERVER_BASE_URL=http://localhost:8080 for custom setups.
```

### ARCH.md version string is stale
```python
# ARCH.md header says "Version: R04.6" but code is at R04.7
```

---

## Active Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Zero runtime dependencies | Python stdlib only | Maximizes portability, zero supply chain risk |
| Unified ReAct + Native | ReAct for OPENRE, native for OPENAI, gated by `_is_comp_mode` | Respects native tool calling when available, preserves ReAct for spec compliance |
| ZAI as first-class backend | `ZaiBackend(OllamaBackend)` with Bearer auth | Cloud GLM models alongside local inference; free models enable zero-cost usage |
| ZAI auto-fallback | Silent model swap on 429/credit exhaustion | Prevents hard session failures; user gets degraded but functional response |
| Dual API support | OpenResponses + OpenAI Chat-Completions | Spec compliance + ecosystem integration |
| SQLite for persistence | stdlib sqlite3 with WAL mode | Zero-dep persistent memory |
| Path whitelist security | Whitelist-based file access | Defense in depth |
| Calculator via eval | Restricted eval with `__builtins__: {}` | Simplicity + full Python math without a parser |
| TurboQuant zero-conversion | Ollama blobs used directly as GGUF | No conversion step, instant model loading |
| mmap GGUF parsing | `mmap.find()` for key search | Fast, handles large files, no dependencies |
| Detached server lifecycle | PID/state file persistence | Server survives CLI exit |
| Schema versioning | `_version` field on TurboState | Forward-compatible state loading |
| Per-session todos | `_todo_stores` dict keyed by session_id | Session isolation infrastructure (not yet wired to Agent) |
| Chat footer UX | Emoji status bar with token tracking | Rich terminal experience without dependencies |
| `check_compatibility` zero-dep | Tuple comparison instead of `packaging.version` | No external dependency for skill version checking |
| DEFAULT_MODEL uses factory | `_get_default_model()` re-reads env vars | Fixes frozen-at-import bug for backend switching |

---

## What's Missing / Incomplete

- **Todo session isolation not wired** — `_get_todo_store(session_id)` exists but Agent doesn't pass session_id through tool calls
- **No streaming for ReAct path** — `--stream` flag exists but streaming not fully implemented for the tool calling loop
- **Memory pruning has no summarization** — old messages silently dropped, not summarized
- **Pipeline mode output chaining is plain text** — `[Previous output: ...]` loses formatting and tool results
- **Parallel merge strategies are simplistic** — `vote` normalizes to first 100 chars; `best` just picks longest
- **No ACL per-tool** — `allowed_tools` at Agent level, no per-role or per-context permissions
- **PersistentMemory has no migration** — schema changes require manual DB deletion
- **No token budget enforcement** — no hard limit in Agent
- **ARCH.md stale** — references R04.6, should be R04.7
- **No chat UX tests** — spinner, footer, token counting, slash commands untested
- **No SkillLoader cache tests** — cache management methods untested

---

## Quick Start for Developer

1. Read the **Critical Files Index** above — each entry has purpose, blast radius, and gotchas
2. Trace the **Request / Execution Lifecycle** — this is how the entire system works end-to-end
3. Check **Known Landmines** — these save real debugging time
4. Follow **Patterns & Conventions** — write code that fits
5. If changing a critical file, check the **Dependency Graph** for blast radius

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task.
