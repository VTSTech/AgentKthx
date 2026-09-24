# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-22 | Auditor: Super Z (running codebase-audit skill) | Version: R06.55 (0.6.55)

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for local LLM inference — zero dependencies, Python stdlib only |
| **Tech Stack** | Python 3.9+ (stdlib only: urllib, sqlite3, argparse, json, re, ast, subprocess, pathlib) |
| **Entry Point** | `agentkthx/__main__.py` → `agentkthx.cli:main()` — installed as `agentkthx` console script |
| **Build/Run** | `pip install -e .` (dev) or `pip install agentkthx` (PyPI). Run with `agentkthx chat` / `agentkthx run "<prompt>"` / `python -m agentkthx ...` |
| **Test Command** | `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` (env var required for ZAI backend import) |
| **Current Version** | 0.6.55 (R06.55) |

---

## Architecture Map

```
agentkthx/                    → Main package
├── __init__.py               → Public API: Agent, Backends, Config, ACPPlugin
├── __main__.py               → CLI entry: `python -m agentkthx`
├── agent.py (2876 lines)     → Agent class — agentic loop, tool calling, JEV dispatch
│                               ALSO: _generate_stream() + _run_core_streaming() (PERF-01)
├── agent_mode.py             → AgentMode/AgentState/TaskPlan (R05.x agentic plan mode)
├── orchestrator.py           → Multi-agent orchestrator (AgentCard, fallbacks)
├── cli.py (3801 lines)       → CLI — see MAINT-01 (monolith). MAINT-04: agent.py also large
├── colors.py                 → ANSI color helpers + glyph mode (AGENTKTHX_GLYPHS env var)
├── config.py                 → Env-var-driven config (AGENTKTHX_* env vars)
├── shared_args.py            → Shared argparse definitions + SharedConfig dataclass
├── update_check.py           → Update check system (stable + dev tracks)
├── model_discovery.py        → Ollama model listing, fuzzy match, pick_best_model
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
│   ├── api_resilience.py     → API error resilience and retry logic
│   └── api_resilience.py     → API error resilience and retry logic (R06.50)
│
├── backends/                 → Backend implementations
│   ├── base.py               → BaseBackend + BackendConfig (abstract)
│   ├── openai_compat.py (NEW R06.55) → OpenAICompatibleBackend — shared OpenAI-compat logic
│   │                          (JEV dispatch, _build_openai_body, _parse_openai_response,
│   │                          generate_completions_stream, api_mode property, family context)
│   ├── ollama.py (1344 lines) → OllamaBackend (native /api/chat + OpenAI-compat /v1)
│   ├── llama_server.py       → LlamaServerBackend (native llama.cpp)
│   └── __init__.py            → _BACKENDS registry + get_backend(name) lazy plugin loader
│
├── tools/                    → Built-in tools
│   ├── __init__.py            → make_builtin_registry(), BUILTIN_REGISTRY
│   ├── registry.py            → ToolRegistry class (subset, get, fuzzy match)
│   ├── builtins.py            → calculator (uses safe_eval), shell (uses sanitize_command),
│   │                          read_file, write_file, http_get, python_repl, list_files,
│   │                          todo_write, todo_read (per-session todos)
│   └── sandboxed_repl.py     → subprocess-isolated Python REPL tool
│
├── plugins/                  → Plugin system (lazy-loaded)
│   ├── __init__.py            → get_plugin_manager()
│   ├── _loader.py             → PluginManager: scans plugins/*/plugin.json (v0.2 spec)
│   ├── acp/                   → ACP v1.0.6 (Agent Control Panel — monitoring/STOP/resume)
│   ├── bitnet/                → BitNet backend (1.58-bit inference, routes to llama-server)
│   ├── zai/                   → Z.AI API backend (GLM-4.x, GLM-5.x family)
│   ├── openrouter/            → OpenRouter backend (500+ models via OpenAI-compat API)
│   ├── turboquant/            → TurboQuant (quantized llama-server launcher)
│   └── test-plugin/           → Test backend (for plugin-system tests)
│
├── soul/                     → Soul Spec v0.5 persona system
│   ├── loader.py              → SoulLoader: parses soul.json + persona files
│   └── souls/                 → Built-in soul packages (nova-skills, nova-helper, nova-trading)
│
└── skills/                   → Built-in skills (audit, codebase-audit, crypto-signals, etc.)
    ├── loader.py              → Skill loader: compatibility check, prompt building
    └── codebase-audit/        → THIS skill (audit + brief generation)
```

### Skip List

- `agentnova/`, `localclaw/`, `*-redirect/` — redirect stubs, kept for compat
- `agentkthx/skills/skill-creator/scripts/` — template scripts with TODO placeholders
- `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `*.egg-link`
- `docs/old_CHANGELOG.md` — pre-R06.0 historical changelog
- `agentnova/` and `localclaw/` redirect stub packages are kept for backward compat

---

## Critical Files Index

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/cli.py` (3801 lines) | CLI — all subcommands, slash commands, banner, footer | Flagged MAINT-01 (monolithic). Any CLI change lands here. Split is a known refactor target. |
| `agentkthx/agent.py` (2876 lines) | Agent class — agentic loop, tool calling, streaming | Flagged MAINT-04. `_run_core()` (670 lines) and `_run_core_streaming()` (620 lines) are near-duplicates with divergent debug output. |
| `agentkthx/backends/openai_compat.py` (771 lines, NEW R06.55) | Shared OpenAI-compat base class | JEV dispatch, `_build_openai_body()`, `_parse_openai_response()`, `generate_completions_stream()`. All backends inherit from this now. Touch this for any shared backend behavior change. |
| `agentkthx/backends/ollama.py` (1344 lines) | OllamaBackend — native + OpenAI mode | Parent of LlamaServerBackend. Has its own `generate_completions_stream` (logprobs support). Keeps `_jev_call_completions`. |
| `agentkthx/core/helpers.py` (~1129 lines) | Security utilities + arg normalization | `sanitize_command()`, `validate_path()`, `is_safe_url()`, `normalize_tool_args()`. The security boundary. |
| `agentkthx/core/safe_eval.py` (~280 lines) | AST-walking math expression evaluator | Replaces `eval()` everywhere. Only allows: numeric literals, names from allowlist, BinOp/UnaryOp/BoolOp/Compare/IfExp/Call. Rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus. |
| `agentkthx/plugins/openrouter/openrouter.py` (1068 lines) | OpenRouter backend | Uses `requests` library (violates zero-dep claim — see ROB-04). Has `_make_api_request` with 429 retry. |
| `agentkthx/plugins/zai/zai.py` (1119 lines) | ZAI backend | Uses `/api/paas/v4/chat/completions` endpoint. Has `_generate_with_auth` with credit-fallback. |
| `agentkthx/core/api_resilience.py` | API error resilience | Handles transient failures, rate limiting, retry logic for all backends. |
| `agentkthx/update_check.py` | Update check system | Checks PyPI for stable releases, GitHub for dev commits. |

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
         OR (if stream=True): _generate_stream() → typewriter output
      ii.  If JEV mode: _maybe_jev_dispatch() → generate_decision() → JSON envelope
      iii. Parse response: extract content + tool_calls
      iv.  If tool_calls: dispatch via ToolRegistry, capture results
      v.   Append assistant message + tool results to memory
      vi.  If final_answer: break
   c. Return AgentRun(final_answer, steps, total_tokens, total_ms, ...)
5. cli.py:_print_agent_steps(run, debug) — surfaces tool calls + results
6. (chat mode) Read next user input; loop back to step 4

Streaming path (PERF-01, R06.53):
  agent.run(stream=True) → _run_core(stream=True) → _run_core_streaming()
  _run_core_streaming() calls _generate_stream() instead of _generate()
  _generate_stream() picks: generate_completions_stream() (OpenAI SSE)
                            or generate_stream() (native text)
                            or generate() (non-streaming fallback)
  Content/reasoning deltas printed to stdout as they arrive
  Tool_calls fragments accumulated across SSE chunks
  Returns same dict shape as _generate() so agentic loop is unchanged
```

---

## Dependency Graph

```
cli.py → agent.py → backends/* + tools/* + core/*
                       │
                       ├─ backends/openai_compat.py ← (parent of) ollama, zai, openrouter
                       │   ├── ollama.py ← (parent of) llama_server.py
                       │   ├── zai.py (ZAI /api/paas/v4, Bearer auth)
                       │   └── openrouter.py (OpenRouter /chat/completions, Bearer+headers)
                       │
                       ├─ tools/builtins.py → core/helpers.py (sanitize_command, validate_path)
                       │                   → core/safe_eval.py (calculator tool)
                       │
                       └─ core/helpers.py → core/safe_eval.py (normalize_tool_args)

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
| **Backend abstraction** | `BaseBackend` (abstract) → `OpenAICompatibleBackend` (shared OpenAI-compat) → concrete backends. ARCH-01 (R06.55) decoupled ZAI/OpenRouter from OllamaBackend. |
| **API modes** | `ApiMode` enum: `OPENRE` (OpenResponses, native), `OPENAI` (Chat Completions), `JEV` (System-One decision wrapper) |
| **Tool calling** | Three-tier: native (function-calling API), ReAct (text-based `<tool>...</tool>`), none (auto-detected) |
| **Memory** | `Memory` (in-memory sliding window) + `PersistentMemory` (SQLite-backed) |
| **Security** | Defense-in-depth: `sanitize_command()` → `validate_path()` → `is_safe_url()` → `--security max\|off` |
| **Math eval** | `safe_eval()` AST walker (R06.41) — no `eval()` in production |
| **Plugin discovery** | Directory scan: `plugins/*/plugin.json` manifest (v0.2 spec) |
| **Streaming** | `run(stream=True)` → `_run_core_streaming()` → `_generate_stream()` → `backend.generate_completions_stream()`. Each backend provides `_get_chat_completions_url()`, `_get_auth_headers()`, `_iter_sse_lines()`. |
| **Naming** | `snake_case` for vars/funcs, `PascalCase` for classes, `UPPER_SNAKE` for constants |
| **Tests** | pytest. Mocked unit tests + streaming tests. 672 passed, 6 skip |

---

## Known Landmines

- **`agentkthx/cli.py` is 3801 lines** (MAINT-01) — any CLI feature change lands in this single file. Split is a known refactor target.

- **`agentkthx/agent.py` is 2876 lines** (MAINT-04) — `_run_core()` (670 lines) and `_run_core_streaming()` (620 lines) are near-duplicates. The streaming version is already missing 24 `if self.debug` checks. Any future fix to the agentic loop must be applied in TWO places.

- **`OpenRouterBackend` imports `requests`** (ROB-04) — the codebase claims `dependencies = []` ("Zero dependencies! Uses Python stdlib only.") but `openrouter.py` line 39 does `import requests`. If `requests` isn't installed, the OpenRouter plugin fails to load silently with a generic "failed to load plugin" warning. The user won't know to `pip install requests`.

- **`_generate_stream()` has a dead `think` parameter** (PERF-03) — accepted but never forwarded to `_build_openai_body()`. The `think` parameter works in non-streaming mode (Ollama adds it to the body) but has no effect on streaming. Misleading API surface.

- **KeyboardInterrupt during streaming doesn't close HTTP connections** (ROB-05) — `_generate_stream()` catches `KeyboardInterrupt` and returns immediately, but the underlying HTTP response (from `urllib.request.urlopen` or `requests.post`) is not explicitly closed. The generator from `generate_completions_stream()` is abandoned without `.close()`.

- **`OllamaBackend._maybe_jev_dispatch()` accesses `self._api_mode` without a `hasattr` guard** — line 590: `if self._api_mode != ApiMode.JEV:`. If a test bypasses `__init__` via `__new__`, `_api_mode` is missing → `AttributeError`.

- **`skills/loader.py:118,127` accepts `"agentnova"` as a framework identifier** — kept intentionally for compatibility.

- **`agentnova/` and `localclaw/` redirect stub packages are kept for backward compat** — `import agentnova` still works (emits DeprecationWarning).

- **Test isolation issue**: some tests mutate `ZAI_API_KEY` env var or `agentkthx.config.ZAI_API_KEY` module attribute without restoring.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | stdlib only (urllib, sqlite3, ast, etc.) | Eliminates supply chain, version conflicts — BUT OpenRouter uses `requests` (ROB-04) |
| **Plugin discovery** | Directory scan of `plugins/*/plugin.json` | Local-first users can drop a backend folder |
| **`shell=True` kept in subprocess** | Accepted-risk + `DANGEROUS_FLAG_COMBOS` hardening | Threat model is "model makes a casual mistake", not "determined adversary" |
| **`AGENTNOVA_*` env vars dropped (not aliased)** | No backward-compat aliases | User explicitly opted in |
| **`agentnova/` redirect stub kept** | Backward-compat for `import agentnova` | Downstream compatibility |
| **JEV as `ApiMode`, not a separate `JevBackend`** | `ApiMode.JEV` sibling of `openre`/`openai` | Any chat-capable backend can produce Jev-shaped decisions |
| **`ThinkingLevel` enum + `parse_thinking_arg()` helper** | Enum + parser + per-backend forwarding | Clean separation |
| **Update check dual-source** | PyPI + GitHub commits | Covers both stable and dev tracks |
| **ARCH-01: `OpenAICompatibleBackend` extraction** | ZAI/OpenRouter inherit from new base, not OllamaBackend | R06.53/R06.54 streaming 404 bugs made structurally impossible. 534 lines of duplication removed. |
| **Streaming path duplicated, not parameterized** | `_run_core_streaming()` is a near-copy of `_run_core()` | Non-streaming path has years of bug fixes (R06.52 loop resilience). Threading through a single parameterized implementation was deemed too risky. Trade-off: MAINT-04. |

---

## What's Missing / Incomplete

- **`cli.py` split** (MAINT-01) — 3801-line monolith. No modules split yet.
- **`agent.py` split** (MAINT-04) — 2876-line file with duplicated agentic loop. `_run_core` + `_run_core_streaming` should converge or split into `agent_loop.py`.
- **`requests` dependency unacknowledged** (ROB-04) — OpenRouter uses `requests` but `pyproject.toml` declares `dependencies = []`. Either add `requests` as optional dep or migrate to stdlib `urllib`.
- **Streaming `think` parameter dead** (PERF-03) — `_generate_stream()` accepts `think` but never forwards it.
- **Streaming KeyboardInterrupt connection leak** (ROB-05) — HTTP response not closed on Ctrl+C mid-stream.
- **OpenRouter `provider` routing** (FEAT-01) — no `provider.order` / `provider.ignore` / `provider.data_collection` flags.
- **`/param` matrix extensibility** (FEAT-02) — hardcoded inline in `cmd_chat()`.
- **Coverage measurement** (ARCH-02) — no `pytest-cov` configured.
- **Integration tests** (TEST-01) — most tests are mocked. Some end-to-end tests added but not comprehensive.

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py` (agent loop) and `cli.py` (CLI surface)
2. **Understand the Request Lifecycle** — `cmd_run` → `_build_agent` → `agent.run` → `backend.generate` → tool dispatch → final answer
3. **Streaming path** — `agent.run(stream=True)` → `_run_core_streaming()` → `_generate_stream()` → `backend.generate_completions_stream()`. The streaming loop is a near-copy of the non-streaming loop — check BOTH when fixing agentic-loop bugs.
4. **Backend hierarchy** — `BaseBackend` → `OpenAICompatibleBackend` → concrete (Ollama/ZAI/OpenRouter). Each backend provides `_get_chat_completions_url()`, `_get_auth_headers()`, `_iter_sse_lines()`, `_get_model_defaults()`.
5. **Check Known Landmines** — especially `cli.py` size, `agent.py` duplication, `requests` dependency, `_api_mode` `hasattr` gap
6. **Follow Patterns & Conventions** — `AGENTKTHX_*` env vars, `safe_eval()` for math, `sanitize_command()` for shell

**Test first, push second**: `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` should report `672 passed, 6 skipped, 0 failed`.

---

## Recent Test Results

```
$ ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q
672 passed, 6 skipped, 0 failed in 1.71s
```

Test files (6,747 lines total):
- `tests/test_security.py` (775 lines) — command sanitization, path validation, URL safety
- `tests/test_openrouter_backend.py` (725 lines) — body construction, response parsing, streaming
- `tests/test_plugin_spec.py` (687 lines) — plugin manifest, loader, lifecycle
- `tests/test_jev_api_mode.py` (518 lines) — JEV dispatch, decision parsing, envelope shape
- `tests/test_skills.py` (495 lines) — skill loader, compatibility, prompt building
- `tests/test_loop_resilience.py` (472 lines) — error classification, termination, duplicate blocking
- `tests/test_api_resilience.py` (443 lines) — API error resilience, retry logic
- `tests/test_thinking_args.py` (429 lines) — thinking argument parsing, per-backend forwarding
- `tests/test_builtins.py` (421 lines) — calculator, shell, file I/O tools
- `tests/test_update_check.py` (398 lines) — update checking, version comparison, caching
- `tests/test_spec_compliance.py` (382 lines) — OpenAI spec compliance, streaming, logprobs
- `tests/test_streaming.py` (359 lines) — _generate_stream, tool_call accumulation, AgentRun return
- `tests/test_agent.py` (282 lines) — agent loop, tool dispatch, memory
- `tests/test_zai_streaming.py` (270 lines) — ZAI streaming override, SSE parsing, auth headers
- `tests/test_cmd_update_pep668.py` (88 lines) — PEP 668 detection, y/n prompt
