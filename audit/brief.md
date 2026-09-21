# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-20 | Auditor: Super Z (running codebase-audit skill v0.2.0) | Version: R06.41 (0.6.41)

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for local LLM inference — zero dependencies, Python stdlib only |
| **Tech Stack** | Python 3.9+ (stdlib only: urllib, sqlite3, argparse, json, re, ast, subprocess, pathlib) |
| **Entry Point** | `agentkthx/__main__.py` → `agentkthx.cli:main()` — installed as `agentkthx` console script |
| **Build/Run** | `pip install -e .` (dev) or `pip install agentkthx` (PyPI). Run with `agentkthx chat` / `agentkthx run "<prompt>"` / `python -m agentkthx ...` |
| **Test Command** | `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` (env var required for ZAI backend import) |

---

## Architecture Map

```
agentkthx/                    → Main package
├── __init__.py               → Public API: Agent, Backends, Config, ACPPlugin
├── __main__.py               → CLI entry: `python -m agentkthx`
├── agent.py                  → Agent class — agentic loop, tool calling, JEV dispatch
├── agent_mode.py             → AgentMode/AgentState/TaskPlan (R05.x agentic plan mode)
├── orchestrator.py           → Multi-agent orchestrator (AgentCard, fallbacks)
├── cli.py                    → CLI (3478 lines — flagged MAINT-01)
├── colors.py                 → ANSI color helpers + glyph mode (AGENTKTHX_GLYPHS env var)
├── config.py                 → Env-var-driven config (AGENTKTHX_* env vars)
├── shared_args.py            → Shared argparse definitions + SharedConfig dataclass
├── model_discovery.py        → Ollama model listing, fuzzy match, pick_best_model
├── turbo.py                  → REMOVED in R06.41 (was duplicate of plugins/turboquant/turbo.py)
├── acp_plugin.py             → REMOVED in R06.41 (was duplicate of plugins/acp/acp_plugin.py)
│
├── core/                     → Core utilities (no plugin coupling)
│   ├── base.py               → (not present; backends/base.py is the backend base)
│   ├── models.py             → Dataclasses: AgentRun, StepResult, Tool, ToolParam, ToolCall
│   ├── types.py              → Enums: ApiMode (OPENRE/OPENAI/JEV), BackendType, StepResultType, ToolSupportLevel
│   ├── memory.py             → In-memory conversation window (MemoryConfig, Message)
│   ├── persistent_memory.py  → SQLite-backed memory (~/.agentkthx/memory.db)
│   ├── helpers.py           → sanitize_command, validate_path, is_safe_url, normalize_tool_args
│   ├── math_prompts.py       → Math system prompts + calculator_tool (uses safe_eval)
│   ├── safe_eval.py          → NEW in R06.41: AST-walking evaluator (replaces eval())
│   ├── tool_cache.py         → Tool support cache (~/.cache/agentkthx/tool_support.json)
│   ├── tool_parse.py         → Tool-call string parsing (ReAct text format)
│   ├── openresponses.py      → OpenResponses API envelope helpers
│   ├── error_recovery.py     → Tool-call error feedback + retry logic
│   ├── args_normal.py        → Argument normalization (fuzzy match, schema fixup)
│   ├── model_config.py       → Per-model defaults (context size, max_tokens)
│   ├── model_family_config.py → Family-based model defaults (qwen2, llama3, etc.)
│   └── prompts.py            → System prompts (general, ReAct, planning)
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
│   ├── _loader.py             → PluginManager: scans plugins/*/plugin.json
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

audit/                        → Audit materials (R06.41)
├── audit.md                  → Current audit (R06.41)
├── brief.md                  → Current brief (this file, R06.41)
├── audit_r06.4.md            → Historical R06.4 audit (preserved for reference)
└── brief_r06.4.md            → Historical R06.4 brief

docs/                         → Documentation
├── ARCH.md                   → Technical architecture doc
├── CHANGELOG.md              → Version history (R06.41 entry covers 5 closed findings)
├── old_CHANGELOG.md          → Pre-R06.0 historical changelog
├── PLUGIN_SPEC.md            → Plugin manifest format spec
├── JEV_API_MODE.md           → JEV (System-One) decision API mode
├── ZAI_API_TECHNICAL_REFERENCE.md → ZAI API reference
├── OPENROUTER_API_TECHNICAL_REFERENCE.md → OpenRouter API reference (has stale AGENTNOVA_ refs — MAINT-03)
├── TESTS.md                  → Benchmark results (R04.5 era, stale)
├── CREDITS.md               → Credits/acknowledgments
└── brief.md                  → REMOVED in R06.41 (moved to audit/)

agentnova/                    → Redirect stub package (deprecated, kept for compat)
localclaw/                   → Redirect stub package (deprecated, kept for compat)
agentnova-redirect/           → Standalone PyPI package for the agentnova redirect
localclaw-redirect/           → Standalone PyPI package for the localclaw redirect

patches/                      → Manual patch files (turbo_v_padding fix, etc.)
tests/                        → Test suite (8 files, 435 tests)
AgentKthx.ipynb              → Jupyter notebook demo
pyproject.toml                → Package config (version 0.6.41)
```

### Skip List

- `agentnova/`, `localclaw/`, `*-redirect/` — redirect stubs, kept for compat (separate concern from MAINT-02)
- `agentkthx/skills/skill-creator/scripts/` — template scripts with TODO placeholders (by design)
- `docs/old_CHANGELOG.md`, `audit/audit_r06.4.md`, `audit/brief_r06.4.md` — historical records
- `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, `*.egg-link`

---

## Critical Files Index

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/agent.py` (1747 lines) | Agent class — agentic loop | The agent's `run()` is the entry point for every prompt. Tool calls, JEV dispatch, thinking controls, runtime kwargs all live here. Touch this for any agent-behavior change. |
| `agentkthx/cli.py` (3478 lines) | CLI — all subcommands, slash commands, banner, footer | Flagged MAINT-01 (monolithic). Any CLI change lands here. Split is a known refactor target. |
| `agentkthx/backends/ollama.py` (1602 lines) | OllamaBackend — native + OpenAI mode | Parent of ZAI/OpenRouter backends (flagged ARCH-01). JEV dispatch (`_maybe_jev_dispatch`) lives here and is inherited. Touch for any backend behavior change. |
| `agentkthx/core/helpers.py` (1129 lines) | Security utilities + arg normalization | `sanitize_command()` (blocklist + DANGEROUS_FLAG_COMBOS), `validate_path()`, `is_safe_url()`, `normalize_tool_args()`. The security boundary — read before touching tool security. |
| `agentkthx/core/safe_eval.py` (NEW, ~280 lines) | AST-walking math expression evaluator | NEW in R06.41 (SEC-01 fix). Replaces `eval()` everywhere. Only allows: numeric literals, names from allowlist, BinOp/UnaryOp/BoolOp/Compare/IfExp/Call. Rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus. |
| `agentkthx/config.py` | Central config — all `AGENTKTHX_*` env vars | Single source of truth for env-var-driven config. Edit here when adding a new env var. |
| `agentkthx/tools/builtins.py` (1334 lines) | Built-in tools — calculator, shell, file I/O | Calculator uses `safe_eval`; shell uses `sanitize_command`; file I/O uses `validate_path`. The tool surface. |
| `agentkthx/plugins/openrouter/openrouter.py` (1103 lines) | OpenRouter backend | Inherits OllamaBackend. Has its own `_jev_call_completions` (ARCH-01 fragile inheritance). Has the `_api_mode` test-bypass issue (fixed in tests but production hardening deferred). |
| `agentkthx/soul/loader.py` (1066 lines) | Soul manifest loader | Has fallback paths to `agentkthx.__file__` (fixed in R06.41 from `agentnova.__file__` NameError bug). |

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

## Dependency Graph

```
cli.py → agent.py → backends/* + tools/* + core/*
                       │
                       ├─ backends/ollama.py ← (parent of) plugins/zai, plugins/openrouter
                       │                       (ARCH-01: fragile inheritance)
                       │
                       ├─ tools/builtins.py → core/helpers.py (sanitize_command, validate_path)
                       │                   → core/safe_eval.py (calculator tool)
                       │
                       └─ core/helpers.py → core/safe_eval.py (normalize_tool_args)
                                          (helpers.py:822 uses safe_eval — R06.41 fix)

plugins/_loader.py → discovers plugins/*/plugin.json at startup
                    → backends/__init__.py:get_backend() lazy-loads on demand

soul/loader.py → agentkthx.__file__ (R06.41 fix — was agentnova.__file__ NameError)

skills/loader.py → checks frameworks: ["agentnova", ...] (kept for compat — separate concern)
```

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Config** | Env-var-driven; module-level constants in `config.py`. `AGENTKTHX_*` prefix (renamed from `AGENTNOVA_*` in R06.41; no aliases kept). |
| **Backend abstraction** | `BaseBackend` abstract class. Native backends (`OllamaBackend`, `LlamaServerBackend`) eager-loaded. Plugin backends (`ZaiBackend`, `OpenRouterBackend`, `BitnetBackend`) lazy-loaded via `PluginManager`. |
| **API modes** | `ApiMode` enum: `OPENRE` (OpenResponses, native), `OPENAI` (Chat Completions), `JEV` (System-One decision wrapper). JEV is API-mode, not backend — any chat-capable backend can produce Jev-shaped decisions. |
| **Tool calling** | Three-tier: native (function-calling API), ReAct (text-based `<tool>...</tool>`), none (auto-detected via `test_tool_support`). |
| **Memory** | `Memory` (in-memory sliding window) + `PersistentMemory` (SQLite-backed, survives restarts). Both subclassable. |
| **Security** | Defense-in-depth: `sanitize_command()` (blocklist + `DANGEROUS_FLAG_COMBOS` + injection regex) → `validate_path()` (allowed dirs + traversal prevention) → `is_safe_url()` (SSRF blocklist + IPv6 hostname extraction). Runtime toggle via `--security max\|off`. |
| **Math eval** | `safe_eval()` AST walker (R06.41) — no `eval()` in production. Rejects `ast.Attribute`/`ast.Subscript` outright. |
| **Plugin discovery** | Directory scan: `plugins/*/plugin.json` manifest. No pip-install or entry points needed — drop a folder to add a backend. |
| **Naming** | `snake_case` for vars/funcs, `PascalCase` for classes, `UPPER_SNAKE` for constants. `~/.agentkthx/` for user data (renamed from `~/.agentnova/` in R06.41). |
| **Tests** | pytest. Mocked unit tests only (TEST-01: no integration tests). 435 tests collected, 429 pass, 6 skip (all with explicit `@pytest.mark.skip(reason=...)`). |

---

## Known Landmines

- **`agentkthx/cli.py` is 3478 lines** (MAINT-01) — any CLI feature change lands in this single file. The `/param` matrix is ~100 inline lines. Split into `cli/parser.py`, `cli/chat.py`, `cli/display.py`, `cli/agent_factory.py`, `cli/params.py` is the audit's recommendation.

- **`OllamaBackend` is the parent of `ZaiBackend` and `OpenRouterBackend`** (ARCH-01) — backend-specific changes (e.g., `_jev_call_completions`) must be carefully threaded through the inheritance. The R06.2 OpenRouter JEV recursion bug was caused by this coupling. Extract an `OpenAICompatibleBackend` mixin to decouple.

- **`OllamaBackend._maybe_jev_dispatch()` accesses `self._api_mode` without a `hasattr` guard** — line 590: `if self._api_mode != ApiMode.JEV:`. Line 1403 uses `hasattr(self, "_api_mode")` defensively. If a test bypasses `__init__` via `__new__` (as some do), `_api_mode` is missing → `AttributeError`. The test-side fix is in place (set `b._api_mode = ApiMode.OPENAI`); production-side hardening is deferred.

- **`docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` references `AGENTNOVA_*` env vars** (MAINT-03, NEW in R06.41) — lines 633-635 still say `AGENTNOVA_BACKEND=openrouter` etc. The MAINT-02 rename missed this doc. Low severity but inconsistent.

- **`skills/loader.py:118,127` accepts `"agentnova"` as a framework identifier** — kept intentionally (existing skill manifests may declare `frameworks: ["agentnova"]` in their `soul.json`). Adding `"agentkthx"` as an accepted alias is a separate enhancement, not a bug.

- **`agentkthx/soul/loader.py:77,103` references `agentkthx.__file__`** — fixed in R06.41 from a `NameError` bug (the file imported `agentkthx` but referenced `agentnova.__file__`). Watch for similar leftover `agentnova.` references if touching this file.

- **`agentnova/` and `localclaw/` redirect stub packages** are kept for backward compat — `import agentnova` still works (emits DeprecationWarning). Separately published as their own PyPI packages (`agentnova-redirect/`, `localclaw-redirect/`). Don't touch unless doing a deprecation cycle.

- **`ACP plugin "source" field is `"agentnova"`** in `plugins/acp/acp_plugin.py:781` — sent to external ACP servers as an identifier. Kept intentionally (wire-format contract).

- **Test isolation issue**: some tests mutate `ZAI_API_KEY` env var or `agentkthx.config.ZAI_API_KEY` module attribute without restoring. Now-historical (the affected test file was deleted in R06.41).

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | stdlib only (urllib, sqlite3, ast, etc.) | Eliminates supply chain, version conflicts. Installable in any Python 3.9+ env. |
| **Plugin discovery** | Directory scan of `plugins/*/plugin.json` | Local-first users can drop a backend folder without `pip install` or entry points. |
| **`shell=True` kept in subprocess** | Accepted-risk + `DANGEROUS_FLAG_COMBOS` hardening (SEC-02) | Threat model is "model makes a casual mistake", not "determined adversary". `shell=False` would break agent workflows (pipes, redirects) for a threat that doesn't manifest. Power users opt out via `--security off` for trusted models. |
| **`AGENTNOVA_*` env vars dropped (not aliased)** | No backward-compat aliases | User explicitly opted in ("we don't have many users"). Clean rename; `AGENTKTHX_*` is the only namespace. |
| **`agentnova/` redirect stub kept** | Backward-compat for `import agentnova` | Downstream users may still `import agentnova`. Separate deprecation-cycle concern. |
| **JEV as `ApiMode`, not a separate `JevBackend`** | `ApiMode.JEV` sibling of `openre`/`openai` | Any chat-capable backend can produce Jev-shaped decisions by wrapping `generate_completions()`. `_jev_call_completions()` hook per-backend routes through own auth. |
| **`ThinkingLevel` enum + `parse_thinking_arg()` helper** | Enum + parser + per-backend forwarding | Clean separation: user-facing enum, parser maps to `(think, reasoning_effort)` tuple, each backend forwards the right fields. |
| **`safe_eval()` rejects strings** | AST walker blocks non-numeric literals | Strings aren't needed for math; closing the surface prevents attribute-name-construction payloads. |

---

## What's Missing / Incomplete

- **`cli.py` split** (MAINT-01) — 3478-line monolith. No modules split yet.
- **Streaming UX** (PERF-01) — `Agent.run(stream=True)` accepts the param but ignores it. Users see a spinner until the full response arrives. Cloud-provider users don't get typewriter-style streaming output.
- **OpenRouter `stream_options.include_usage`** (PERF-02) — not sent on streaming requests; token counts show 0 for streamed responses.
- **OpenRouter provider routing** (FEAT-01) — no `provider.order` / `provider.ignore` / `provider.data_collection` flags. Users can't pin to free providers or avoid specific ones.
- **`/param` matrix extensibility** (FEAT-02) — hardcoded inline in `cmd_chat()`. Plugins can't register new params.
- **Backend inheritance decoupling** (ARCH-01) — `OpenAICompatibleBackend` mixin extraction not done. R06.2 OpenRouter JEV recursion bug class still possible.
- **Coverage measurement** (ARCH-02) — no `pytest-cov` configured. Actual coverage percentage unknown.
- **Integration tests** (TEST-01) — all 435 tests are mocked. No real HTTP calls, no end-to-end agent loop, no CLI subprocess tests.
- **`docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` stale AGENTNOVA_ refs** (MAINT-03, NEW) — three lines still reference the old env var names.
- **`docs/TESTS.md`** — R04.5 era benchmark tables; references models that have been renamed. Could be archived or refreshed.
- **PrintAgentSteps output capture** (2 tests skipped) — `_print_agent_steps` likely uses `rich.Console` or stderr; `sys.stdout` capture in tests misses output. Functionality works interactively; capture mechanism needs investigation.

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py` (agent loop) and `cli.py` (CLI surface).
2. **Understand the Request Lifecycle** — `cmd_run` → `_build_agent` → `agent.run` → `backend.generate` → tool dispatch → final answer.
3. **Check Known Landmines** — especially `cli.py` size, backend inheritance coupling, `_api_mode` `hasattr` gap.
4. **Follow Patterns & Conventions** — `AGENTKTHX_*` env vars (no `AGENTNOVA_*` aliases), `safe_eval()` for math (no `eval()`), `sanitize_command()` for shell (blocklist + flag-combos).
5. **If changing a critical file**, check the Dependency Graph for blast radius — `backends/ollama.py` changes affect ZAI + OpenRouter via inheritance.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task.

**Test first, push second**: `ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` should report `429 passed, 6 skipped`. If anything fails, you broke something.
