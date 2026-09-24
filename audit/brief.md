# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-25 (updated R06.57) | Auditor: Super Z (running codebase-audit skill) | Version: R06.57 (0.6.57) | Commit: pending

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for local + cloud LLM inference — zero dependencies, Python stdlib only |
| **Tech Stack** | Python 3.9+ (stdlib only: urllib, sqlite3, argparse, json, re, ast, subprocess, pathlib) |
| **Entry Point** | `agentkthx/__main__.py` → `agentkthx.cli:main()` — installed as `agentkthx` console script |
| **Build/Run** | `pip install -e .` (dev) or `pip install agentkthx` (PyPI). Run with `agentkthx chat` / `agentkthx run "<prompt>"` / `python -m agentkthx ...` |
| **Test Command** | `ZAI_API_KEY=test_dummy_key_12345 GEMINI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` (env vars required for cloud backend imports) |
| **Current Version** | 0.6.57 (R06.57) |

---

## Architecture Map

```
agentkthx/                    → Main package
├── __init__.py               → Public API: Agent, Backends, Config, ACPPlugin
├── __main__.py               → CLI entry: `python -m agentkthx`
├── agent.py (3119 lines)     → Agent class — agentic loop, tool calling, JEV dispatch
│                              ALSO: _generate_stream() + _run_core_streaming() (PERF-01 closed)
│                              DUPLICATION: _run_core() vs _run_core_streaming() (MAINT-04, WORSENED)
├── agent_mode.py             → AgentMode/AgentState/TaskPlan (R05.x agentic plan mode)
├── orchestrator.py           → Multi-agent orchestrator (AgentCard, fallbacks)
├── cli.py (4079 lines)       → CLI — see MAINT-01 (monolith, +278 lines in R06.56).
│                              MAINT-05 (NEW): 8 hardcoded backend allowlists; BUG-01/02 were patches.
├── colors.py                 → ANSI color helpers + glyph mode (AGENTKTHX_GLYPHS env var)
├── config.py                 → Env-var-driven config (AGENTKTHX_* + GEMINI_* env vars)
├── shared_args.py            → Shared argparse definitions + SharedConfig dataclass
├── update_check.py           → Update check system (stable + dev tracks)
├── model_discovery.py        → Ollama model listing, fuzzy match, pick_best_model
│
├── core/                     → Core utilities (no plugin coupling)
│   ├── models.py             → Dataclasses: AgentRun, StepResult, Tool, ToolParam, ToolCall
│   ├── types.py              → Enums: ApiMode (OPENRE/OPENAI/JEV), BackendType (+GEMINI R06.56), StepResultType, ToolSupportLevel
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
│   └── api_resilience.py     → API error resilience and retry logic
│
├── backends/                 → Backend implementations
│   ├── base.py               → BaseBackend + BackendConfig (abstract)
│   ├── openai_compat.py (789 lines, R06.55) → OpenAICompatibleBackend — shared OpenAI-compat logic
│   │                          (JEV dispatch, _build_openai_body, _parse_openai_response,
│   │                          generate_completions_stream, api_mode property, family context)
│   │                          PERF-03: dead `think` parameter on generate_completions_stream (line 620)
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
│   ├── zai/                   → Z.AI API backend (GLM-4.x, GLM-5.x family) — uses urllib (ROB-04 closed)
│   ├── openrouter/            → OpenRouter backend (500+ models via OpenAI-compat API)
│   │                          MIGRATED to stdlib urllib in R06.55 (ROB-04 closed).
│   │                          ROB-06 (NEW): _iter_sse_lines lacks try/finally response.close() (ZAI has it)
│   ├── gemini/ (NEW R06.56)  → Google Gemini API backend via OpenAI-compat endpoint
│   │                          gemini.py (1864 lines): 10-model catalog, 429 retry w/ Retry-After,
│   │                          context-length 400 recovery, Gemma <thought> tag parser, free-tier data.
│   │                          FEAT-03 (NEW): thought-signature stateful continuation NOT yet implemented.
│   │                          ARCH-03 (NEW): ~400 lines of 429 retry logic duplicated with OpenRouter.
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
| `agentkthx/cli.py` (4079 lines) | CLI — all subcommands, slash commands, banner, footer | Flagged MAINT-01 (monolithic, +278 lines in R06.56). MAINT-05: 8 hardcoded backend allowlists — adding 5th cloud backend requires 8 edits. Any CLI change lands here. |
| `agentkthx/agent.py` (3119 lines) | Agent class — agentic loop, tool calling, streaming | Flagged MAINT-04. `_run_core()` (line 649, ~727 lines) and `_run_core_streaming()` (line 2399, ~602 lines) are near-duplicates. Debug-check divergence WORSENED: 36 vs 7 (was 31 vs 7), gap = 29 (was 24). UX-01 changes (reasoning panel state at lines 2139-2143, helpers at 2145/2154) exist ONLY in streaming path. |
| `agentkthx/backends/openai_compat.py` (789 lines) | Shared OpenAI-compat base class | JEV dispatch, `_build_openai_body()`, `_parse_openai_response()`, `generate_completions_stream()`. PERF-03: `think` param accepted (line 620) but never forwarded to `_build_openai_body()` (line 669-683). |
| `agentkthx/backends/ollama.py` (1344 lines) | OllamaBackend — native + OpenAI mode | Parent of LlamaServerBackend. Has its own `generate_completions_stream` (logprobs support). |
| `agentkthx/plugins/gemini/gemini.py` (1864 lines, NEW R06.56) | Gemini backend | 4th cloud backend. Implements all 4 abstract hooks: `_get_chat_completions_url` (1165), `_get_auth_headers` (1175), `_iter_sse_lines` (1440), `_get_model_defaults` (1060). ROB-06: streaming lacks try/finally response.close(). FEAT-03: thought-signature continuation NOT implemented (line 50-52). 429 retry logic duplicated with OpenRouter (ARCH-03). |
| `agentkthx/plugins/openrouter/openrouter.py` (1235 lines) | OpenRouter backend | MIGRATED to stdlib urllib (R06.55, ROB-04 closed). ROB-06 (NEW): `_iter_sse_lines` (1103-1148) and `_stream_request` (781-808) lack try/finally response.close() — ZAI has the correct pattern at lines 705-712. |
| `agentkthx/plugins/zai/zai.py` (1119 lines) | ZAI backend | Uses `/api/paas/v4/chat/completions` endpoint. REFERENCE IMPLEMENTATION for streaming cleanup — `_iter_sse_lines` (705-712) has correct `try: yield ... finally: response.close()` pattern that OpenRouter/Gemini should adopt. |
| `agentkthx/core/helpers.py` (~1129 lines) | Security utilities + arg normalization | `sanitize_command()`, `validate_path()`, `is_safe_url()`, `normalize_tool_args()`. The security boundary. |
| `agentkthx/core/safe_eval.py` (~280 lines) | AST-walking math expression evaluator | Replaces `eval()` everywhere. Rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus. |
| `agentkthx/config.py` | Env-var-driven config | All `AGENTKTHX_*` env vars + 6 new `GEMINI_*` (lines 84-95). DOC-01 (NEW): GEMINI_* env vars NOT documented in README Configuration section. |
| `agentkthx/core/api_resilience.py` | API error resilience | Handles transient failures, rate limiting, retry logic for all backends. |

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

Streaming path (PERF-01 closed R06.53, MAINT-04 OPEN):
  agent.run(stream=True) → _run_core(stream=True) → _run_core_streaming()
  _run_core_streaming() calls _generate_stream() instead of _generate()
  _generate_stream() picks: generate_completions_stream() (OpenAI SSE)
                            or generate_stream() (native text)
                            or generate() (non-streaming fallback)
  Content/reasoning deltas printed to stdout as they arrive
  Tool_calls fragments accumulated across SSE chunks
  UX-01 (R06.56): reasoning panel emitted ABOVE AgentKthx: prefix, 4-space indented
  ROB-05 (OPEN): KeyboardInterrupt handler (line 2342-2354) doesn't call stream_gen.close()
  Returns same dict shape as _generate() so agentic loop is unchanged
```

---

## Dependency Graph

```
cli.py → agent.py → backends/* + tools/* + core/*
                       │
                       ├─ backends/openai_compat.py ← (parent of) ollama, zai, openrouter, gemini
                       │   ├── ollama.py ← (parent of) llama_server.py
                       │   ├── zai.py (ZAI /api/paas/v4, Bearer auth) — REFERENCE for SSE cleanup
                       │   ├── openrouter.py (stdlib urllib since R06.55) — ROB-06 leak
                       │   └── gemini.py (NEW R06.56, OpenAI-compat + extra_body.google.*)
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
| **Config** | Env-var-driven; module-level constants in `config.py`. `AGENTKTHX_*` prefix + per-backend prefixes (`GEMINI_*`, `OPENROUTER_*`, `ZAI_*`) |
| **Backend abstraction** | `BaseBackend` (abstract) → `OpenAICompatibleBackend` (shared OpenAI-compat) → concrete backends. ARCH-01 (R06.55) decoupled ZAI/OpenRouter from OllamaBackend. R06.56 added GeminiBackend following same pattern. |
| **API modes** | `ApiMode` enum: `OPENRE` (OpenResponses, native), `OPENAI` (Chat Completions), `JEV` (System-One decision wrapper). GeminiBackend silently normalizes `OPENRE → OPENAI` (R06.56 BUG-01 defense). |
| **Tool calling** | Three-tier: native (function-calling API), ReAct (text-based `<tool>...</tool>`), none (auto-detected) |
| **Memory** | `Memory` (in-memory sliding window) + `PersistentMemory` (SQLite-backed) |
| **Security** | Defense-in-depth: `sanitize_command()` → `validate_path()` → `is_safe_url()` → `--security max\|off` |
| **Math eval** | `safe_eval()` AST walker (R06.41) — no `eval()` in production |
| **Plugin discovery** | Directory scan: `plugins/*/plugin.json` manifest (v0.2 spec) |
| **Streaming** | `run(stream=True)` → `_run_core_streaming()` → `_generate_stream()` → `backend.generate_completions_stream()`. Each backend provides `_get_chat_completions_url()`, `_get_auth_headers()`, `_iter_sse_lines()`, `_get_model_defaults()`. ZAI's `_iter_sse_lines` is the reference for cleanup pattern. |
| **Naming** | `snake_case` for vars/funcs, `PascalCase` for classes, `UPPER_SNAKE` for constants |
| **Tests** | pytest. Mocked unit tests + streaming tests + 3 Gemini live-API tests (skipped by default). 766 passed, 9 skipped. NO integration tests (TEST-01 OPEN). |

---

## Known Landmines

- **`agentkthx/cli.py` is 4079 lines** (MAINT-01, worsened +278 in R06.56) — any CLI feature change lands in this single file. Split is a known refactor target.

- **`agentkthx/agent.py` is 3119 lines** (MAINT-04, worsened +243 in R06.56) — `_run_core()` (~727 lines) and `_run_core_streaming()` (~602 lines) are near-duplicates. The streaming version is missing **29** `if self.debug` checks (was 24). UX-01 reasoning panel state (lines 2139-2143) and helpers (lines 2145, 2154) exist ONLY in streaming path. Any future fix to the agentic loop must be applied in TWO places.

- **8 hardcoded backend allowlists in `cli.py`** (MAINT-05, NEW) — lines 514, 587, 645, 787, 1953, 1972, 2326, 2380. The R06.56 BUG-01 fix changed `("openrouter")` → `("openrouter", "gemini")` at line 2326 — a patch, not a generalization. A 5th cloud backend would hit the same crash. The 6 BackendType.GEMINI additions (BUG-02 `replace_all`) are also patches.

- ~~**OpenRouter & Gemini streaming leak HTTP connections** (ROB-06, NEW)~~ — ✓ CLOSED R06.57. All 4 sites now have `try: ... finally: response.close()` matching ZAI's pattern: `openrouter.py:_iter_sse_lines` (1146-1158), `openrouter.py:_stream_request` (801-818), `gemini.py:_iter_sse_lines` (1473-1486), `gemini.py:_stream_request` (1426-1444). Combined with ROB-05's `stream_gen.close()` on Ctrl+C, the streaming cleanup contract is now uniform across all 4 cloud backends.

- ~~**`_generate_stream()` KeyboardInterrupt doesn't close HTTP** (ROB-05)~~ — ✓ CLOSED R06.57. `agent.py:2342-2365` now calls `stream_gen.close()` before returning the cancelled-response dict.

- ~~**Gemini env vars not in README** (DOC-01)~~ — ✓ CLOSED R06.57. README now has a `### Gemini Configuration` subsection (lines 379-416) with all 7 env vars + usage examples, plus a Gemini block in the master env-var table (lines 593-600).

- **FIX-01 (R06.57, user-reported)** — Pip-installed users now see dev releases. New `_fetch_github_latest_version()` in `update_check.py` fetches `https://raw.githubusercontent.com/VTSTech/AgentKthx/main/agentkthx/__init__.py` and parses `__version__ = "X.Y.Z"`. `format_notice` surfaces the dev track for pip installs with a `pip install --force-reinstall git+...` command. `cmd_version` shows a new "GitHub main: X.Y.Z" line. Surfaces R06.55+, R06.56+, R06.57+ that aren't on PyPI. +11 new tests in `test_update_check.py` (65 total, was 54).

- **ZAI now has `num_ctx/32` cap + 400 recovery** (R06.57, ROB-06 parity) — `zai.py:_get_model_defaults` caps `max_tokens` to `context_length // 32` (mirrors OpenRouter R06.55 + Gemini R06.56). `_iter_sse_lines` and `_generate_with_auth` both now have context-length 400 recovery with `_calculate_safe_max_tokens` + `_context_safe_max_tokens` persistence. All 3 cloud backends (OpenRouter, Gemini, ZAI) now share this pattern — extraction to `OpenAICompatibleBackend` is the long-term fix (ARCH-03).

- **`_generate_stream()` has a dead `think` parameter** (PERF-03) — accepted at `openai_compat.py:620` but never forwarded to `_build_openai_body()` at line 669-683. Misleading API surface. Affects all OpenAI-compat backends (ZAI, OpenRouter, Gemini).

- **PARAM_MATRIX excludes Gemini from 5 params** (FEAT-02 sub-issue) — `cli.py:1519-1622` `top_k`/`seed`/`n`/`presence_penalty`/`frequency_penalty` backend sets don't include `"gemini"`. Likely a bug — Gemini's OpenAI-compat endpoint accepts these. Verify on real VM.

- **`GeminiBackend._api_mode` accepts strings silently** — `__init__` normalizes `OPENRE → OPENAI` (lines 818-820) per BUG-01 defense. This is intentionally more permissive than `OpenRouterBackend`'s strict check. Asymmetry is documented but creates maintenance drift.

- **`agentnova/` and `localclaw/` redirect stub packages** — kept for backward compat, emit DeprecationWarning on `import`.

- **Test isolation issue**: some tests mutate `ZAI_API_KEY` env var or `agentkthx.config.ZAI_API_KEY` module attribute without restoring.

- **Test count discrepancy**: R06.55 audit.md says 672 passed; R06.56 changelog TEST-02 says R06.55 baseline was 710 passed. ~38 tests added between audit (2026-09-22) and R06.55 release tag (2026-09-23).

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | stdlib only (urllib, sqlite3, ast, etc.) | Eliminates supply chain, version conflicts. ROB-04 (requests dep) CLOSED R06.55 — OpenRouter migrated to urllib. |
| **Plugin discovery** | Directory scan of `plugins/*/plugin.json` | Local-first users can drop a backend folder |
| **`shell=True` kept in subprocess** | Accepted-risk + `DANGEROUS_FLAG_COMBOS` hardening | Threat model is "model makes a casual mistake", not "determined adversary" |
| **`AGENTNOVA_*` env vars dropped (not aliased)** | No backward-compat aliases | User explicitly opted in |
| **`agentnova/` redirect stub kept** | Backward-compat for `import agentnova` | Downstream compatibility |
| **JEV as `ApiMode`, not a separate `JevBackend`** | `ApiMode.JEV` sibling of `openre`/`openai` | Any chat-capable backend can produce Jev-shaped decisions |
| **`ThinkingLevel` enum + `parse_thinking_arg()` helper** | Enum + parser + per-backend forwarding | Clean separation |
| **Update check dual-source** | PyPI + GitHub commits | Covers both stable and dev tracks |
| **ARCH-01: `OpenAICompatibleBackend` extraction** | ZAI/OpenRouter/Gemini inherit from new base, not OllamaBackend | R06.53/R06.54 streaming 404 bugs made structurally impossible. 534 lines of duplication removed. GeminiBackend (R06.56) follows the pattern. |
| **Streaming path duplicated, not parameterized** | `_run_core_streaming()` is a near-copy of `_run_core()` | Non-streaming path has years of bug fixes (R06.52 loop resilience). Threading through a single parameterized implementation was deemed too risky. Trade-off: MAINT-04. |
| **GeminiBackend silently normalizes `OPENRE → OPENAI`** | Defensive permissiveness vs OpenRouter's strict check | BUG-01 fix: avoids future CLI code paths crashing Gemini. Asymmetry documented. |
| **Gemini thought-signature NOT yet implemented** | v0.1 ships without stateful continuation | Plumbing exists (`thought_signature` kwarg forwarded); accumulator + capture from response chunks deferred to v0.2. Trade-off: ~2-3x reasoning token cost on multi-turn loops. |
| **Gemma `<thought>...</thought>` tag parser** (R06.56 FEAT-03) | Streaming state machine + non-streaming one-shot | Gemma emits reasoning inline as tags, not in `reasoning_content`. Strips stray closing tags, flushes unclosed. |

---

## What's Missing / Incomplete

- **`cli.py` split** (MAINT-01) — 4079-line monolith. No modules split yet. Grew +278 lines in R06.56.
- **`agent.py` split** (MAINT-04) — 3119-line file with duplicated agentic loop. `_run_core` + `_run_core_streaming` should converge or split into `agent_loop.py`. Debug divergence now 29 (was 24).
- **Backend allowlists hardcoded** (MAINT-05, NEW) — 8 sites in cli.py. Should use `is_cloud` class attribute or `isinstance(backend, OpenAICompatibleBackend)`.
- **Streaming `_iter_sse_lines` connection leak** (ROB-06, NEW) — OpenRouter and Gemini lack try/finally. ZAI has the pattern.
- **Streaming KeyboardInterrupt connection leak** (ROB-05) — HTTP response not closed on Ctrl+C mid-stream.
- **Dead `think` parameter** (PERF-03) — `_generate_stream()` accepts but never forwards.
- **Gemini thought-signature continuation** (FEAT-03, NEW) — v0.1 limitation, ~2-3x reasoning token cost.
- **OpenRouter `provider` routing** (FEAT-01) — no `provider.order` / `provider.ignore` / `provider.data_collection` flags.
- **`/param` matrix extensibility** (FEAT-02) — hardcoded inline; Gemini excluded from 5 params (sub-issue).
- **429 retry logic duplication** (ARCH-03, NEW) — ~400 lines duplicated between OpenRouter and Gemini. Should extract to base class.
- **Coverage measurement** (ARCH-02) — no `pytest-cov` configured.
- **Integration tests** (TEST-01) — most tests are mocked. BUG-01 and BUG-02 caught by manual VM testing.
- **Gemini env vars in README** (DOC-01, NEW) — 6 GEMINI_* env vars declared in config.py and plugin.json but not in README Configuration section.

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py` (agent loop) and `cli.py` (CLI surface)
2. **Understand the Request Lifecycle** — `cmd_run` → `_build_agent` → `agent.run` → `backend.generate` → tool dispatch → final answer
3. **Streaming path** — `agent.run(stream=True)` → `_run_core_streaming()` → `_generate_stream()` → `backend.generate_completions_stream()`. The streaming loop is a near-copy of the non-streaming loop — check BOTH when fixing agentic-loop bugs. Streaming now has UX-01 reasoning panel state (lines 2139-2143) that non-streaming lacks.
4. **Backend hierarchy** — `BaseBackend` → `OpenAICompatibleBackend` → concrete (Ollama/ZAI/OpenRouter/Gemini). Each backend provides `_get_chat_completions_url()`, `_get_auth_headers()`, `_iter_sse_lines()`, `_get_model_defaults()`. **For SSE cleanup, copy ZAI's pattern** (`zai.py:705-712`); OpenRouter and Gemini are buggy (ROB-06).
5. **Check Known Landmines** — especially `cli.py` size + hardcoded allowlists (MAINT-01/05), `agent.py` duplication (MAINT-04), `_iter_sse_lines` leak (ROB-06), `_api_mode` normalization asymmetry
6. **Follow Patterns & Conventions** — `AGENTKTHX_*` env vars, `safe_eval()` for math, `sanitize_command()` for shell

**Test first, push second**: `ZAI_API_KEY=test_dummy_key_12345 GEMINI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q` should report `766 passed, 9 skipped, 0 failed`.

---

## Recent Test Results

```
$ ZAI_API_KEY=test_dummy_key_12345 GEMINI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q
777 passed, 9 skipped, 0 failed in 1.86s
```

Test files (total):
- `tests/test_security.py` (775 lines) — command sanitization, path validation, URL safety
- `tests/test_openrouter_backend.py` — body construction, response parsing, streaming (rewritten R06.55 for urllib migration)
- `tests/test_plugin_spec.py` (687 lines) — plugin manifest, loader, lifecycle
- `tests/test_gemini_backend.py` (1111 lines, NEW R06.56, 97 test methods) — backend init, model family detection, free-tier classification, thought-tag parser, build body, retry helpers, 3 live-API tests (skipped by default)
- `tests/test_jev_api_mode.py` (518 lines) — JEV dispatch, decision parsing, envelope shape
- `tests/test_skills.py` (495 lines) — skill loader, compatibility, prompt building
- `tests/test_loop_resilience.py` (472 lines) — error classification, termination, duplicate blocking
- `tests/test_api_resilience.py` (443 lines, rewritten R06.55) — API error resilience, urllib mock transport
- `tests/test_thinking_args.py` (429 lines) — thinking argument parsing, per-backend forwarding
- `tests/test_builtins.py` (421 lines) — calculator, shell, file I/O tools
- `tests/test_update_check.py` (~570 lines, 65 tests, +11 in R06.57) — update checking, version comparison, caching, FIX-01 pip-installed dev track via raw __init__.py
- `tests/test_spec_compliance.py` (382 lines) — OpenAI spec compliance, streaming, logprobs
- `tests/test_streaming.py` (359 lines) — _generate_stream, tool_call accumulation, AgentRun return
- `tests/test_agent.py` (282 lines) — agent loop, tool dispatch, memory
- `tests/test_zai_streaming.py` (270 lines) — ZAI streaming override, SSE parsing, auth headers
- `tests/test_cmd_update_pep668.py` (88 lines) — PEP 668 detection, y/n prompt
