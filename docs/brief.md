# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-21 | Auditor: Super-Z (Z.ai) | Commit: f0e48f1

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | Minimal, hackable agentic framework for autonomous AI agents — runs locally with Ollama, in the cloud with ZAI/OpenRouter |
| **Tech Stack** | Python 3.9+ (stdlib only — zero dependencies), argparse CLI, urllib for HTTP, SQLite for persistent memory |
| **Entry Point** | `agentkthx/cli.py:main()` — CLI entry point; `agentkthx/__main__.py` for `python -m agentkthx` |
| **Build/Run** | `pip install -e .` for dev; `pip install agentkthx` for PyPI; `agentkthx run/chat/agent` CLI binary |
| **Test Command** | `pytest` — 506 tests across 11 test files, 245 passing, 3 skipped, 9 pre-existing failures |

---

## Architecture Map

```
agentkthx/
├── cli.py              → CLI entry point, all subcommands (3478 lines — largest file)
├── agent.py            → Agent class + agentic loop (1747 lines)
├── config.py           → Central config from env vars
├── shared_args.py      → Shared argparse arguments for run/chat/agent
├── orchestrator.py     → Multi-agent router/pipeline/parallel modes
├── agent_mode.py       → Autonomous agent mode (task planning)
├── model_discovery.py  → Ollama model discovery + benchmarking
├── turbo.py            → TurboQuant server management
├── acp_plugin.py       → ACP plugin (duplicate of plugins/acp/)
├── colors.py           → Terminal ANSI color helpers
├── core/               → Core framework types + logic
│   ├── types.py        → Enums: ApiMode, ThinkingLevel, BackendType, ToolSupportLevel
│   ├── models.py       → Dataclasses: Tool, StepResult, AgentRun, ToolCall
│   ├── helpers.py      → Security: sanitize_command, validate_path, is_safe_url (1003 lines)
│   ├── memory.py       → Sliding window conversation memory
│   ├── persistent_memory.py → SQLite-backed persistent memory
│   ├── openresponses.py → OpenResponses spec implementation (1067 lines)
│   ├── tool_parse.py   → ReAct/JSON tool call extraction
│   ├── tool_cache.py   → Persistent tool support detection cache
│   ├── model_family_config.py → Model family configs (stop tokens, prompts, thinking)
│   ├── error_recovery.py → Retry-with-error-feedback logic
│   ├── prompts.py      → System prompt builders, tool argument aliases
│   ├── args_normal.py  → Argument normalization for small models
│   └── math_prompts.py → Math-specific prompts + safe eval calculator
├── backends/           → Inference backends (always available)
│   ├── base.py         → BaseBackend ABC + BackendConfig
│   ├── ollama.py       → OllamaBackend (1602 lines — native + OpenAI paths)
│   ├── llama_server.py → LlamaServerBackend (923 lines)
│   ├── bitnet.py       → BitNetBackend (deprecated wrapper of LlamaServerBackend)
│   └── ollama_registry.py → Ollama model registry helpers
├── plugins/            → Plugin system (loaded on demand)
│   ├── _loader.py     → PluginManager singleton, discovery, loading
│   ├── zai/           → ZAI cloud backend (Bearer auth, GLM models)
│   ├── openrouter/    → OpenRouter backend (500+ models, 429 retry)
│   ├── bitnet/        → BitNet plugin wrapper (wraps LlamaServerBackend)
│   ├── acp/           → ACP (Agent Control Panel) integration
│   ├── turboquant/    → TurboQuant server management
│   └── test-plugin/   → Test backend plugin
├── tools/             → Built-in tools (17 tools)
│   ├── builtins.py     → shell, read_file, write_file, calculator, http_get, etc (1430 lines)
│   ├── registry.py     → ToolRegistry, ToolParam
│   └── sandboxed_repl.py → Sandboxed Python REPL
├── soul/               → Soul Spec v0.5 persona system
│   ├── loader.py       → SoulLoader, build_system_prompt (1066 lines)
│   ├── types.py        → SoulManifest, Environment, InteractionMode dataclasses
│   └── __init__.py     → Exports
├── souls/              → Default soul packages
│   ├── nova-helper/    → Default helper persona
│   ├── nova-skills/    → Skill-guided assistant persona
│   └── nova-trading/   → Trading analyst persona
├── skills/             → AgentSkills spec
│   ├── loader.py        → SkillLoader, SkillRegistry (734 lines)
│   ├── codebase-audit/ → Codebase audit skill
│   ├── crypto-signals/ → Crypto trading signal skill
│   ├── skill-creator/  → Skill creation/validation toolkit
│   └── test-harness/   → Diagnostic testing skill
└── examples/          → 12 diagnostic/benchmark test scripts

# Redirect packages (backward compat)
agentnova/              → Redirect stub → agentkthx
localclaw/              → Redirect stub → agentkthx

# Standalone PyPI redirect packages
agentnova-redirect/     → PyPI package: "agentnova" depends on agentkthx
localclaw-redirect/     → PyPI package: "localclaw" depends on agentkthx
```

### Skip List

- `__pycache__/`, `.git/`, `.pytest_cache/`
- `agentkthx/examples/` — diagnostic scripts, not core
- `audit/` — screenshot images from earlier testing
- `patches/` — historical patches for turboquant

---

## Critical Files Index

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/cli.py` | CLI entry point, all subcommands | 3478 lines — every command, slash command, and display logic lives here. `/param`, `/skills`, `/status`, chat loop, run loop all in this file |
| `agentkthx/agent.py` | Agent class + agentic loop | 1747 lines — the core reasoning loop, tool execution, memory management, JEV dispatch, thinking controls |
| `agentkthx/backends/ollama.py` | OllamaBackend | 1602 lines — largest backend. Contains `generate()`, `generate_completions()`, `generate_stream()`, `generate_decision()`, `_maybe_jev_dispatch()`, `_jev_call_completions()`. Also the parent class for ZAI and OpenRouter backends |
| `agentkthx/core/helpers.py` | Security utilities | 1003 lines — `sanitize_command()`, `validate_path()`, `is_safe_url()`, command blocklist, injection detection |
| `agentkthx/tools/builtins.py` | 17 built-in tools | 1430 lines — shell, read_file, write_file, edit_file, calculator, http_get, python_repl, web_search, etc |
| `agentkthx/plugins/zai/zai.py` | ZAI backend | 993 lines — Bearer auth, GLM model catalog, `_generate_with_auth()`, `_jev_call_completions()` |
| `agentkthx/plugins/openrouter/openrouter.py` | OpenRouter backend | 1103 lines — 429 retry, model cache, `_make_api_request()`, `_parse_openai_response()` |
| `agentkthx/config.py` | Central config | All env var defaults live here. `AGENTNOVA_*` env vars (kept for backward compat) |
| `agentkthx/core/types.py` | Enums | `ApiMode` (OPENRE/OPENAI/JEV), `ThinkingLevel` (OFF/AUTO/LOW/MEDIUM/HIGH), `BackendType`, `ToolSupportLevel` |
| `agentkthx/shared_args.py` | Shared CLI args | `add_agent_args()` — all flags shared by run/chat/agent commands |

---

## Request / Execution Lifecycle

```
1. User runs: agentkthx run/chat/agent "prompt" --backend X --api Y
2. cli.py: create_parser() → parse args → _build_agent() → Agent(...)
3. Agent.run(prompt, stream=bool)
4. Agent._generate() → backend.generate(model, messages, tools, **kwargs)
5. Backend dispatches:
   ├── api_mode == JEV → _maybe_jev_dispatch() → generate_decision()
   ├── api_mode == OPENAI → generate_completions()
   └── api_mode == OPENRE → native /api/chat (Ollama) or _generate_with_auth() (ZAI)
6. Response parsed → content + tool_calls + reasoning_content
7. If tool_calls: execute tools → add observation to memory → loop (step 4)
8. If no tool_calls: accept as final answer → return AgentRun
9. CLI displays: final_answer + optional reasoning_content (if --think)
```

JEV mode flow:
```
generate() → _maybe_jev_dispatch() → generate_decision()
  → _build_jev_messages() [system prompt + user state]
  → _jev_call_completions() [per-backend: ZAI→_generate_with_auth, OR→self.generate()]
  → _parse_jev_response() [JSON parsing, probability clamping, fuzzy matching]
  → return {decision, probability, alternatives, usage, reasoning_content}
```

---

## Dependency Graph

```
cli.py → shared_args.py → config.py
cli.py → agent.py → backends/*.py → core/helpers.py (security)
cli.py → agent.py → core/memory.py → core/persistent_memory.py
agent.py → core/openresponses.py → core/models.py → core/types.py
agent.py → backends/ollama.py ← plugins/zai/zai.py (inherits)
agent.py → backends/ollama.py ← plugins/openrouter/openrouter.py (inherits)
plugins/_loader.py → plugins/*/plugin.json (manifests)
cli.py → skills/loader.py → skills/*/SKILL.md
agent.py → soul/loader.py → souls/*/soul.json
```

Key coupling points:
- ZAI and OpenRouter backends both inherit from OllamaBackend — changes to OllamaBackend affect all three
- `agent.py` references `_maybe_jev_dispatch()` and `generate_decision()` which live on OllamaBackend
- CLI is monolithic — all slash commands and display logic in one 3478-line file

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| Error handling | `except Exception` used 119 times; 2 bare `except:` (helpers.py:833, orchestrator.py:279) |
| Backend inheritance | ZaiBackend(OpenaiBackend), OpenRouterBackend(OllamaBackend) — share generate_completions() |
| Config | All env vars prefixed `AGENTNOVA_*` (kept for backward compat from rename) |
| Security | `--security max|off` runtime toggle; command blocklist + injection detection in sanitize_command() |
| Memory | Sliding window (default 50 messages); SQLite persistent mode via `--session` |
| Thinking | `--thinking off\|auto\|low\|medium\|high` → `parse_thinking_arg()` → `(think, reasoning_effort)` |
| Plugin discovery | Directory scan `plugins/*/plugin.json`, no pip install required |
| Tool support | Auto-detected: NATIVE (API tools), REACT (text parsing), NONE (pure reasoning) |
| Redirect stubs | `agentnova` and `localclaw` packages re-export from `agentkthx` with DeprecationWarning |

---

## Known Landmines

- **`cli.py` is 3478 lines** — adding any new feature requires touching this file. No module splitting for slash commands, display logic, or agent construction.
- **Duplicate files**: `agentkthx/acp_plugin.py` is a near-exact copy of `agentkthx/plugins/acp/acp_plugin.py` (only import paths differ). Same for `agentkthx/turbo.py` vs `agentkthx/plugins/turboquant/turbo.py`.
- **`eval()` in calculator**: `core/math_prompts.py:220` and `core/helpers.py:820` use `eval()` with `{"__builtins__": {}}` — safe-ish but can be bypassed with carefully crafted AST. The calculator is the only tool that evaluates user input.
- **`shell=True` in subprocess**: `tools/builtins.py:295` runs `subprocess.run(validated_cmd, shell=True)`. Security relies entirely on `sanitize_command()` blocklist + injection detection. With `--security off`, all checks are disabled and the model can run any command.
- **ZAI's `thinking` object format**: ZAI uses `{"type": "disabled"}` not a bare `think=false`. If you forget to convert, `--thinking off` silently does nothing (was a real bug in R06.3).
- **OpenRouter JEV recursion**: `_jev_call_completions()` calls `self.generate()` which calls `_maybe_jev_dispatch()`. Must temporarily flip `_api_mode` to `OPENAI` to avoid infinite recursion (was a real bug in R06.2).
- **`agentnova` redirect stub**: `__all__` in `agentkthx/__init__.py` must stay in sync with actual imports. A pre-existing mismatch (OPENROUTER_* listed but not imported) was only caught when the redirect stub did `from agentkthx import *`.
- **9 pre-existing test failures**: `tests/test_r048_changes.py` (8 failures — module path `agentnova.backends.zai` no longer exists) and `tests/test_security.py` (1 failure — IPv6 loopback SSRF detection). These were broken before the R06.0 rename and haven't been fixed.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Zero dependencies | Python stdlib only (urllib, sqlite3) | Hackable, no dependency hell, works in minimal environments |
| Plugin system | Directory scan, not pip install | Simpler for local-first users; no virtualenv management |
| Env var names | Kept `AGENTNOVA_*` (not renamed to `AGENTKTHX_*`) | Backward compat with existing user configs after rename |
| Filesystem paths | Kept `~/.agentnova/` (not renamed) | Existing SQLite sessions + tool cache continue to work |
| JEV mode | Emulation via any LLM (not native TypeSafe Jev API) | Free models only; no TypeSafe API key or waitlist required |
| Default max-steps | 25 (was 10) | Enough for codebase audits; not so high that infinite loops burn tokens |
| Default streaming | Cloud providers stream, local don't | Cloud feels faster with streaming; local Ollama is fast enough non-streaming |
| `think` parameter | ZAI uses `{"type": "disabled"}` object; Ollama uses bare `think=false`; OpenRouter ignores it | Each backend has different thinking model APIs |

---

## What's Missing / Incomplete

- **Streaming display**: `agent.run(stream=True)` accepts the param but never uses it — always runs non-streaming path. `run_stream()` exists but yields SSE events, not console output. Real streaming display (typewriter effect + live tool call display) is not implemented.
- **No integration tests**: All tests are unit tests with mocks. No end-to-end test that exercises a full agent run with a real backend.
- **`acp_plugin.py` and `turbo.py` duplicates**: Two copies exist — one at repo root, one in `plugins/`. Should be consolidated.
- **9 broken tests**: `test_r048_changes.py` references `agentnova.backends.zai` (old path). `test_security.py` IPv6 loopback test fails. Pre-existing, not caused by R06.x changes.
- **No OpenRouter `stream_options.include_usage`**: Streaming responses have no token usage info.
- **No `provider` routing preferences**: Can't pin to specific OpenRouter providers or control failover.
- **No `transforms` or `plugins` support**: OpenRouter's middle-out truncation and web search plugins not wired.
- **`/param` matrix is hardcoded**: Adding a new parameter requires editing the inline dict in `cmd_chat`. Not extensible via plugin system.
- **No coverage measurement**: `pytest --cov` not configured; actual coverage unknown.

---

## Quick Start for Developer

1. Read the Critical Files Index above — start with `cli.py`, `agent.py`, `backends/ollama.py`
2. Understand the Request Lifecycle — `_build_agent()` → `Agent.run()` → `backend.generate()` → response
3. Check Known Landmines — especially the JEV recursion guard and ZAI thinking object format
4. Follow Patterns & Conventions — zero deps, env var backward compat, plugin directory scan
5. If touching backends, check the inheritance chain: ZAI/OpenRouter inherit from OllamaBackend
6. If touching CLI, note that `cli.py` is 3478 lines — consider whether a new module would help

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task.
