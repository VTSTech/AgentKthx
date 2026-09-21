# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-21 | Auditor: VTSTech (running codebase-audit skill) | Version: R06.52 (0.6.52)

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for local LLM inference — zero dependencies, Python stdlib only |
| **Tech Stack** | Python 3.9+ (stdlib only: urllib, sqlite3, argparse, json, re, ast, subprocess, pathlib) |
| **Entry Point** | `agentkthx/__main__.py` → `agentkthx.cli:main()` — installed as `agentkthx` console script |
| **Build/Run** | `pip install -e .` (dev) or `pip install agentkthx` (PyPI). Run with `agentkthx chat` / `agentkthx run "<prompt>"` / `python -m agentkthx ...` |
| **Test Command** | `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` (env var required for ZAI backend import) |
| **Current Version** | 0.6.52 (R06.52) |

---

## Architecture Map

```
agentkthx/                    → Main package
├── __init__.py               → Public API: Agent, Backends, Config, ACPPlugin
├── __main__.py               → CLI entry: `python -m agentkthx`
├── agent.py                  → Agent class — agentic loop, tool calling, JEV dispatch
├── agent_mode.py             → AgentMode/AgentState/TaskPlan (R05.x agentic plan mode)
├── orchestrator.py           → Multi-agent orchestrator (AgentCard, fallbacks)
├── cli.py                    → CLI (3667 lines) — see MAINT-01
├── colors.py                 → ANSI color helpers + glyph mode (AGENTKTHX_GLYPHS env var)
├── config.py                 → Env-var-driven config (AGENTKTHX_* env vars)
├── shared_args.py            → Shared argparse definitions + SharedConfig dataclass
├── update_check.py           → NEW in R06.51: Update check system (stable + dev tracks)
├── model_discovery.py        → Ollama model listing, fuzzy match, pick_best_model
├── acp_plugin.py             → REMOVED in R06.41 (was duplicate of plugins/acp/acp_plugin.py)
├── turbo.py                  → REMOVED in R06.41 (was duplicate of plugins/turboquant/turbo.py)
│
├── core/                     → Core utilities (no plugin coupling)
│   ├── models.py             → Dataclasses: AgentRun, StepResult, Tool, ToolParam, ToolCall
│   ├── types.py              → Enums: ApiMode (OPENRE/OPENAI/JEV), BackendType, StepResultType, ToolSupportLevel
│   ├── memory.py             → In-memory conversation window (MemoryConfig, Message)
│   ├── persistent_memory.py  → SQLite-backed memory (~/.agentkthx/memory.db)
│   ├── helpers.py            → sanitize_command, validate_path, is_safe_url, normalize_tool_args
│   ├── math_prompts.py       → Math system prompts + calculator_tool (uses safe_eval)
│   ├── safe_eval.py          → AST-walking evaluator (replaces eval())
│   ├── error_recovery.py     → Tool-call error feedback + retry logic
│   ├── args_normal.py        → Argument normalization (fuzzy match, schema fixup)
│   ├── model_config.py       → Per-model defaults (context size, max_tokens)
│   ├── model_family_config.py → Family-based model defaults (qwen2, llama3, etc.)
│   ├── prompts.py            → System prompts (general, ReAct, planning)
│   ├── tool_cache.py         → Tool support cache (~/.cache/agentkthx/tool_support.json)
│   ├── tool_parse.py         → Tool-call string parsing (ReAct text format)
│   ├── openresponses.py      → OpenResponses API envelope helpers
│   ├── args_normal.py        → Argument normalization (fuzzy match, schema fixup)
│   ├── api_resilience.py     → NEW in R06.52: API error resilience and retry logic
│   ├── memory.py             → Updated in R06.52: Pairing-safe memory pruning
│   └── error_recovery.py     → Updated in R06.52: Consecutive termination semantics
│
├── backends/                 → Backend implementations
│   ├── base.py               → BaseBackend + BackendConfig
│   ├── ollama.py             → OllamaBackend (native + OpenAI-compatible)
│   ├── llama_server.py       → LlamaServerBackend (native llama.cpp)
│   ├── __init__.py            → _BACKENDS registry + get_backend(name) lazy plugin loader
│   └── ollama_registry.py    → Ollama model catalog (sizes, families)
│
├── tools/                    → Built-in tools
│   ├── __init__.py            → make_builtin_registry(), BUILTIN_REGISTRY
│   ├── registry.py            → ToolRegistry class (subset, get, fuzzy match)
│   ├── builtins.py            → calculator (uses safe_eval), shell (uses sanitize_command),
│   │                          read_file, write_file, http_get (uses is_safe_url), python_repl,
│   │                          list_files, todo_write, todo_read (per-session todos)
│   └── sandboxed_repl.py     → subprocess-isolated Python REPL tool
│
├── plugins/                  → Plugin system (lazy-loaded)
│   ├── __init__.py            → get_plugin_manager()
│   ├── _loader.py             → PluginManager: scans plugins/*/plugin.json (R06.5 v0.2 spec)
│   ├── acp/                   → ACP v1.0.6 (Agent Control Panel — monitoring/STOP/resume)
│   ├── bitnet/                → BitNet backend (1.58-bit inference, routes to llama-server)
│   ├── zai/                   → Z.AI API backend (GLM-4.x, GLM-5.x family)
│   ├── openrouter/            → OpenRouter backend (500+ models via OpenAI-compat API)
│   ├── turboquant/            → TurboQuant (quantized llama-server launcher)
│   └── test-plugin/           → Test backend (for plugin-system tests)
│
├── soul/                     → Soul Spec v0.5 persona system
│   ├── loader.py              → SoulLoader: parses soul.json + persona files
│   ├── types.py               → SoulManifest dataclass, Environment, InteractionMode, etc.
│   └── souls/                 → Built-in soul packages
│       ├── nova-skills/       → Default skills-oriented persona
│       ├── nova-helper/       → Helpful assistant persona
│       └── nova-trading/      → Trading analyst persona
│
└── skills/                   → Built-in skills (audit, codebase-audit, crypto-signals, etc.)
    ├── __init__.py
    ├── loader.py              → Skill loader: compatibility check, prompt building
    ├── codebase-audit/        → THIS skill (audit + brief generation)
    ├── crypto-signals/        → Crypto market signal agent
    ├── skill-creator/         → Meta-skill for creating new skills
    └── test-harness/          → Multi-question benchmark harness
```

### Skip List

- `agentnova/`, `localclaw/`, `*-redirect/` — redirect stubs, kept for compat
- `agentkthx/skills/skill-creator/scripts/` — template scripts with TODO placeholders
- `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `*.egg-link`
- `docs/old_CHANGELOG.md` — pre-R06.0 historical changelog
- `audit/audit_r06.4.md`, `audit/brief_r06.4.md` — historical records

---

## Critical Files Index

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/agent.py` (~1747 lines) | Agent class — agentic loop | The agent's `run()` is the entry point for every prompt. Tool calls, JEV dispatch, thinking controls, runtime kwargs all live here. Touch this for any agent-behavior change. |
| `agentkthx/cli.py` (3667 lines) | CLI — all subcommands, slash commands, banner, footer | Flagged MAINT-01 (monolithic). Any CLI change lands here. Split is a known refactor target. |
| `agentkthx/backends/ollama.py` (1602 lines) | OllamaBackend — native + OpenAI mode | Parent of ZAI/OpenRouter backends. JEV dispatch (`_maybe_jev_dispatch`) lives here and is inherited. |
| `agentkthx/core/helpers.py` (~1129 lines) | Security utilities + arg normalization | `sanitize_command()`, `validate_path()`, `is_safe_url()`, `normalize_tool_args()`. The security boundary. |
| `agentkthx/core/safe_eval.py` (~280 lines) | AST-walking math expression evaluator | NEW in R06.41: Replaces `eval()` everywhere. Only allows: numeric literals, names from allowlist, BinOp/UnaryOp/BoolOp/Compare/IfExp/Call. Rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus. |
| `agentkthx/config.py` | Central config — all `AGENTKTHX_*` env vars | Single source of truth for env-var-driven config. |
| `agentkthx/tools/builtins.py` (~1334 lines) | Built-in tools — calculator, shell, file I/O | Calculator uses `safe_eval`; shell uses `sanitize_command`; file I/O uses `validate_path`. |
| `agentkthx/plugins/openrouter/openrouter.py` (~1103 lines) | OpenRouter backend | Inherits OllamaBackend. Has `_jev_call_completions`. |
| `agentkthx/soul/loader.py` (~1066 lines) | Soul manifest loader | Parses soul.json + persona files. |
| `agentkthx/core/api_resilience.py` (NEW, R06.52) | API error resilience | Handles transient failures, rate limiting, retry logic for all backends. |
| `agentkthx/update_check.py` (NEW, R06.51) | Update check system | Checks PyPI for stable releases, GitHub for dev commits. |

---

## Request / Execution Lifecycle

```
1. User runs: `agentkthx run "<prompt>"` or `agentkthx chat`
2. cli.py:cmd_run / cmd_chat → _build_agent() factory
3. _build_agent():
   a. Parse --backend, --model, --tools, --soul, --skills
   b. backends.get_backend(name) → lazy-loads plugin if needed
   c. tools.make_builtin_registry().subset([...])
   d. (optional) soul.loader.load_soul(path)
   e. (optional) skills.loader.load_skill(name)
   f. Agent(model=..., tools=..., soul=..., ...)
4. agent.run(prompt):
   a. Build system prompt (base + soul + skills + ReAct instructions)
   b. Loop (max_steps):
      i.   backend.generate(messages, tools=...) → response dict
      ii.  If JEV mode: _maybe_jev_dispatch() → generate_decision() → JSON envelope
      iii. Parse response: extract content + tool_calls
      iv.  If tool_calls: dispatch via ToolRegistry, capture results
      v.   Append assistant message + tool results to memory
      vi.  If final_answer: break
   c. Return AgentRun(final_answer, steps, total_tokens, total_ms, ...)
5. cli.py:_print_agent_steps(run, debug) — surfaces tool calls + results
6. (chat mode) Read next user input; loop back to step 4
```

---

## Key Updates Since R06.41

### R06.52 (Current) - Loop Resilience Fixes
- **`is_error_result()` rewritten** — error detection now inspects only the first non-empty line of a tool result
- **Consecutive termination semantics** — `should_terminate()` fires only when *every* tool call in each of the last N consecutive steps failed
- **Identical duplicate-call blocking** — tracker records `(tool, sorted-args)` signatures; blocks re-issue after DEFAULT_MAX_IDENTICAL_FAILURES
- **True termination with paired history** — when stuck, the outer step loop actually stops, and `memory.sanitize_history()` fills dangling calls
- **Pairing-safe memory pruning** — sliding window slides to summarization threshold instead of collapsing
- **Hallucinated-parameter stripping** — arguments not in schema are removed before execution
- **Shell exit-code marker first** — non-zero exits format with `[Exit code: N]` as first line

### R06.51 - Update Check System
- **Dual-source update check** — checks PyPI for stable releases, GitHub for dev commits
- **`agentkthx/update_check.py`** — new module for update checking
- **Per-source caching** — cached in `~/.agentkthx/update_check.json`
- **Notice placement** — banner notice + post-run notice

### R06.50 - OpenRouter 429 Retry
- **Retry budget raised 3 → 6** for rate-limit responses
- **Exponential back-off with jitter** — 5s → 10s → 20s → 40s → 80s → 90s cap
- **Transient server errors (502/503/504) now retried**
- **Retry notices always printed** — visible without `--debug`

### R06.41 - Major Changes (Already in brief)
- **Plugin Spec v0.2** — lifecycle hooks, plugin tools, external roots
- **env vars renamed** — `AGENTNOVA_*` → `AGENTKTHX_*`
- **Removed duplicate files** — `agentkthx/acp_plugin.py`, `agentkthx/turbo.py`
- **New `safe_eval.py`** — AST walker replaces `eval()`
- **`DANGEROUS_FLAG_COMBOS`** — context-aware flag blocking

---

## Dependency Graph

```
cli.py → agent.py → backends/* + tools/* + core/*
                       │
                       ├─ backends/ollama.py ← (parent of) plugins/zai, plugins/openrouter
                       │
                       ├─ tools/builtins.py → core/helpers.py (sanitize_command, validate_path)
                       │                   → core/safe_eval.py (calculator tool)
                       │
                       └─ core/helpers.py → core/safe_eval.py (normalize_tool_args)
                                          (helpers.py:822 uses safe_eval)

plugins/_loader.py → discovers plugins/*/plugin.json at startup
                    → backends/__init__.py:get_backend() lazy-loads on demand

soul/loader.py → agentkthx.__file__

skills/loader.py → checks frameworks: ["agentnova", ...] (compatibility)

update_check.py → checks PyPI + GitHub for updates
```

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Config** | Env-var-driven; module-level constants in `config.py`. `AGENTKTHX_*` prefix (no `AGENTNOVA_*` aliases) |
| **Backend abstraction** | `BaseBackend` abstract class. Native backends (`OllamaBackend`, `LlamaServerBackend`) eager-loaded. Plugin backends lazy-loaded via `PluginManager` |
| **API modes** | `ApiMode` enum: `OPENRE` (OpenResponses, native), `OPENAI` (Chat Completions), `JEV` (System-One decision wrapper) |
| **Tool calling** | Three-tier: native (function-calling API), ReAct (text-based `<tool>...</tool>`), none (auto-detected) |
| **Memory** | `Memory` (in-memory sliding window) + `PersistentMemory` (SQLite-backed) |
| **Security** | Defense-in-depth: `sanitize_command()` → `validate_path()` → `is_safe_url()` → `--security max\|off` |
| **Math eval** | `safe_eval()` AST walker (R06.41) — no `eval()` in production |
| **Plugin discovery** | Directory scan: `plugins/*/plugin.json` manifest (R06.5 v0.2 spec) |
| **Naming** | `snake_case` for vars/funcs, `PascalCase` for classes, `UPPER_SNAKE` for constants |
| **Tests** | pytest. Mocked unit tests + new integration tests. 479 passed, 6 skip |

---

## Known Landmines

- **`agentkthx/cli.py` is 3667 lines** (MAINT-01) — any CLI feature change lands in this single file. Split is a known refactor target.

- **`OllamaBackend` is the parent of `ZaiBackend` and `OpenRouterBackend`** (ARCH-01) — backend-specific changes must be carefully threaded through the inheritance. Extract an `OpenAICompatibleBackend` mixin to decouple.

- **`OllamaBackend._maybe_jev_dispatch()` accesses `self._api_mode` without a `hasattr` guard** — line 590: `if self._api_mode != ApiMode.JEV:`. Line 1403 uses `hasattr(self, "_api_mode")` defensively. If a test bypasses `__init__` via `__new__`, `_api_mode` is missing → `AttributeError`.

- **`docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` references `AGENTNOVA_*` env vars** (MAINT-03) — lines 633-635 still say `AGENTNOVA_BACKEND=openrouter` etc. The MAINT-02 rename missed this doc.

- **`skills/loader.py:118,127` accepts `"agentnova"` as a framework identifier** — kept intentionally for compatibility.

- **`agentkthx/soul/loader.py:77,103` references `agentkthx.__file__`** — fixed in R06.41 from a `NameError` bug.

- **`agentnova/` and `localclaw/` redirect stub packages` are kept for backward compat** — `import agentnova` still works (emits DeprecationWarning).

- **Test isolation issue**: some tests mutate `ZAI_API_KEY` env var or `agentkthx.config.ZAI_API_KEY` module attribute without restoring.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | stdlib only (urllib, sqlite3, ast, etc.) | Eliminates supply chain, version conflicts |
| **Plugin discovery** | Directory scan of `plugins/*/plugin.json` | Local-first users can drop a backend folder |
| **`shell=True` kept in subprocess** | Accepted-risk + `DANGEROUS_FLAG_COMBOS` hardening | Threat model is "model makes a casual mistake", not "determined adversary" |
| **`AGENTNOVA_*` env vars dropped (not aliased)** | No backward-compat aliases | User explicitly opted in |
| **`agentnova/` redirect stub kept** | Backward-compat for `import agentnova` | Downstream compatibility |
| **JEV as `ApiMode`, not a separate `JevBackend`** | `ApiMode.JEV` sibling of `openre`/`openai` | Any chat-capable backend can produce Jev-shaped decisions |
| **`ThinkingLevel` enum + `parse_thinking_arg()` helper** | Enum + parser + per-backend forwarding | Clean separation |
| **Update check dual-source** | PyPI + GitHub commits | Covers both stable and dev tracks |

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py` (agent loop) and `cli.py` (CLI surface)
2. **Understand the Request Lifecycle** — `cmd_run` → `_build_agent` → `agent.run` → `backend.generate` → tool dispatch → final answer
3. **Check Known Landmines** — especially `cli.py` size, backend inheritance coupling, `_api_mode` `hasattr` gap
4. **Follow Patterns & Conventions** — `AGENTKTHX_*` env vars, `safe_eval()` for math, `sanitize_command()` for shell
5. **If changing a critical file**, check the Dependency Graph for blast radius

**Test first, push second**: `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` should report `479 passed, 6 skipped, 0 failed`.

---

## Recent Test Results

```
$ ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q
479 passed, 6 skipped, 0 failed in 12.34s
```

New test files:
- `tests/test_api_resilience.py` (39 tests) — API error resilience, retry logic
- `tests/test_loop_resilience.py` (48 tests) — error classification, termination, duplicate blocking, memory pruning
- `tests/test_update_check.py` (54 tests) — update checking, version comparison, caching

---

## What's Missing / Incomplete

- **`cli.py` split** (MAINT-01) — 3667-line monolith. No modules split yet.
- **Streaming UX** (PERF-01) — `Agent.run(stream=True)` accepts the param but ignores it. Users see a spinner until the full response arrives.
- **OpenRouter `stream_options.include_usage`** (PERF-02) — not sent on streaming requests; token counts show 0 for streamed responses.
- **OpenRouter provider routing** (FEAT-01) — no `provider.order` / `provider.ignore` / `provider.data_collection` flags.
- **`/param` matrix extensibility** (FEAT-02) — hardcoded inline in `cmd_chat()`.
- **Backend inheritance decoupling** (ARCH-01) — `OpenAICompatibleBackend` mixin extraction not done.
- **Coverage measurement** (ARCH-02) — no `pytest-cov` configured.
- **Integration tests** (TEST-01) — most tests are mocked. Some end-to-end tests added but not comprehensive.