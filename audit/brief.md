# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-26 | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `45c7613` (R07.04, PyPI 0.7.03)
> Supersedes: R07.00 + R07.01 brief (2026-09-26) — regenerated because 27 commits (R07.02 → R07.04) touched 57 files; ~25% of the files referenced in the prior brief were modified.

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, and skills |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib `urllib`/`json`/`sqlite3`/`ast`/`subprocess` only); dev: pytest/black/ruff |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py:main()` → `cli/parser.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI) or `pip install -e .` from source; `agentkthx chat`, `agentkthx version`, `agentkthx models`, etc. (84+ CLI flags across 15 subcommands) |
| **Test Command** | `python -m pytest tests/ -q` → ~984 tests; CI matrix Python 3.12 / 3.13 in `.github/workflows/ci.yml`, parallel `coverage` job uploads 30-day `coverage.xml` artifact |

---

## Architecture Map

```
agentkthx/agent.py           → Agent class — 5-mixin composition (1089 LOC; was 3,119-line monolith pre-R07.00)
agentkthx/agent_mode.py      → AgentMode + TaskPlan/Step/Action with rollback (822 LOC)
agentkthx/orchestrator.py    → Multi-agent orchestrator (sequential + parallel + LLM-router modes, 461 LOC)
agentkthx/core/              → 21 modules, ~9,150 LOC — the engine
  ├─ agentic_loop.py         → Unified loop body (R07.00 Phase 5 extraction), tool dispatch, error recovery
  ├─ streaming.py            → SSE streaming + OpenResponses event generator
  ├─ compaction.py           → Context-window compaction mixin (fires at 85% of num_ctx)
  ├─ tool_parse.py           → ReAct / native-JSON / XML tool-call parser
  ├─ tool_execution.py      → Tool dispatch + arg normalization entrypoint
  ├─ agent_setup.py          → Agent.__init__ (22 params + **kwargs) + soul loading + system prompt
  ├─ openresponses.py        → OpenResponses spec: Response state machine, items, 9 SSE event types
  ├─ api_resilience.py       → Transient-vs-permanent error classifier + backoff w/ Retry-After + ±20% jitter
  ├─ error_recovery.py       → ErrorRecoveryTracker, is_error_result, build_enhanced_observation
  ├─ helpers.py              → Security primitives (validate_path, sanitize_command, is_safe_url) + fuzzy match + arg normalization (1114 LOC — shared by 18+ modules)
  ├─ safe_eval.py            → AST-walking eval replacement (rejects Attribute/Subscript/Lambda/comprehensions)
  ├─ memory.py               → Sliding-window conversation memory + sanitize_history (orphan tool results)
  ├─ persistent_memory.py    → SQLite-backed PersistentMemory(Memory) subclass
  ├─ model_family_config.py → Per-family stop tokens / temperature / no-think directives
  └─ ...
agentkthx/cli/               → 23-file CLI package: main, parser, agent_factory, headers, banner, utils
agentkthx/cli/commands/      → 15 command modules: chat (1201 LOC!), test, config, models, agent, tools, soul, ...
agentkthx/backends/          → "Native" backends: ollama, llama_server, bitnet, ollama_registry, openai_compat (shared base), base
agentkthx/plugins/           → 7 plugins: acp, bitnet, gemini, huggingface, openai, openrouter, turboquant, zai — each with plugin.json + main .py
  └─ _loader.py              → PluginManager singleton, manifest parser, Kahn topological-sort dependency loader (1392 LOC)
agentkthx/skills/            → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py (621 LOC)
agentkthx/soul/              → Soul Spec v0.5 persona packages: loader.py (1046 LOC), types.py
agentkthx/souls/             → 3 bundled souls: nova-helper, nova-skills, nova-trading
agentkthx/tools/             → builtins.py (1340 LOC: calculator, shell, read/write/edit_file, http_get, web_search, python_repl, todo), registry.py, sandboxed_repl.py (521 LOC)
agentkthx/update_check.py   → Live PyPI + GitHub version check on EVERY CLI invocation (no cache since R07.00)
agentkthx/config.py          → Env-var-derived singletons (OLLAMA_BASE_URL, OPENROUTER_API_KEY, AGENTKTHX_BACKEND, ...)
audit/                       → This brief + audit.md
docs/                        → ARCH.md (long), PLUGIN_SPEC.md, *_API_TECHNICAL_REFERENCE.md, CHANGELOG.md, TESTS.md
tests/                       → 38 files, ~14,300 LOC, ~984 tests; biggest: test_huggingface_backend, test_gemini_backend, test_openai_backend, test_security (775 LOC), test_plugin_spec (687 LOC)
scripts/                     → probe_{ollama,openai,gemini,zai,openrouter,huggingface,llama_server,bitnet}.sh, bump-version.sh
patches/                     → 2 llama.cpp turboquant patches + standalone .py applier
schemas/v0.2/                → plugin.schema.json (declared but NOT validated by code — ad-hoc dict-shape checks instead)
```

### Skip List

- `__pycache__/`, `.git/`, `*.egg-info/`, `build/`, `dist/`
- `agentnova-redirect/` and `localclaw-redirect/` — thin shims for backward-compat package names
- `agentkthx/plugins/test-plugin/` — fixture for plugin spec tests
- `agentkthx/examples/` — 11 demo scripts (not run by pytest)
- `AgentKthx.ipynb` — root-level notebook, not referenced in docs

---

## Critical Files Index

The most important files in the codebase. Touch these for almost any meaningful change.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/agent.py` (1089 LOC) | `Agent` class — 5-mixin composition: `AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin`. Holds `run()`, `_generate_with_retry()`, `_generate()`, `_run_core()`, `add_tool()`. | Every code path that uses the framework flows through here. R07.00 broke the 3,119-line god-class into 5 mixins but `agent.py` still owns the retry loop (`_generate_with_retry`, lines 176-280) which is shared by streaming + non-streaming paths. |
| `agentkthx/core/helpers.py` (1114 LOC) | Security primitives + arg normalization + fuzzy matching + calc-expression extraction. `validate_path`, `sanitize_command`, `is_safe_url`, `normalize_args`, `extract_calc_expression`, `safe_eval` re-export. | Imported by 18+ modules. A bug here affects every tool call, every shell command, every URL fetch. The SSRF/path-traversal/security surface of the entire framework is concentrated in this single file. |
| `agentkthx/core/agentic_loop.py` (760 LOC) | `_run_loop_iteration` — the unified agentic loop body extracted in R07.00 Phase 5. Drives `Response` state machine, tool dispatch, error recovery, finish_reason handling. | This IS the agentic loop. Every chat/run/agent command ultimately calls this. Bug here = bug everywhere. |
| `agentkthx/core/error_recovery.py` (910 LOC) | `ErrorRecoveryTracker` state machine, `is_error_result` classifier (regex on first non-empty line), `should_terminate` (consecutive_all counter), `build_enhanced_observation`. | Drives the "agent stops retrying a broken tool" decision. The `consecutive_all` counter resets on any success — easy footgun (see Known Landmines #9). |
| `agentkthx/plugins/_loader.py` (1392 LOC) | `PluginManager` singleton, manifest v0.2 parser, Kahn topological-sort dependency loader, hook dispatch with per-plugin failure isolation, external plugin import via `spec_from_file_location`. | Owns plugin lifecycle. A bug here breaks every cloud backend (ZAI/OpenRouter/Gemini/OpenAI/HuggingFace are all plugins). |
| `agentkthx/backends/openai_compat.py` (965 LOC) | Shared OpenAI Chat-Completions base class. JEV dispatch (`generate_decision`), SSE streaming, context-length 400 recovery (`_handle_context_length_400`), 429 retry with Retry-After. | Every cloud backend plugin (zai, openrouter, gemini, openai, huggingface) inherits from this. A bug here affects 5 backends. |
| `agentkthx/core/tool_parse.py` (491 LOC) | `ToolParser.parse(text)` — tries native JSON, ReAct (Action/Action Input), XML; per-strategy `_parse_*` methods. | Every model response flows through this. Parsing regressions = silent tool-call loss. The `ast.literal_eval` fallback for Python-dict-style args (line 217-228) is a known risk surface. |
| `agentkthx/core/streaming.py` (862 LOC) | `StreamingMixin._generate_stream` (354 lines!) + OpenResponses SSE event generator + reasoning-panel rendering. | Owns the chat UX. Hard to test (side-effecting stdout writes). The KeyboardInterrupt path (lines 795-818) closes the urllib response — historically buggy (ROB-06/ROB-08). |
| `agentkthx/core/agent_setup.py` (518 LOC) | `AgentSetupMixin.__init__` — 22 explicit params + `**kwargs` for 5 more. Soul loading, system prompt assembly (4 variants: no-tools, BitNet lean, comp-mode native-tools, ReAct). | Every `Agent(...)` construction flows through here. The `**kwargs` pattern means typos in `response_format`, `confirm_dangerous`, `persistent`, `session_id`, `memory_db` are silently ignored. |
| `agentkthx/config.py` (303 LOC) | Env-var-derived module-level singletons: `OLLAMA_BASE_URL`, `BITNET_BASE_URL`, `ZAI_BASE_URL`, `OPENROUTER_API_KEY`, `ACP_BASE_URL/USER/PASS`, `DEFAULT_MODEL`, `AGENTKTHX_BACKEND`. | Read by every backend/plugin/CLI module at import time. Changing an env var name here is a breaking change for every deployment. |

---

## Request / Execution Lifecycle

```
1. `agentkthx chat`  ──────────────────────────────────────────────────────────
   └─ cli/__main__.py → cli/main.py:main()
       ├─ get_plugin_manager().load_all()  → plugins/_loader.py:_resolve_load_order (Kahn topological sort)
       ├─ atexit.register(emit on_shutdown)
       ├─ create_parser()  → cli/parser.py (stashes private parser._subparsers_action — argparse internals hack)
       ├─ plugin-discovered CLI commands → subparsers_action.add_parser()
       ├─ _run_update_check()  → cli/banner.py:94  → update_check.py:check_for_update(timeout=1.0)
       │     └─ 3 sequential HTTPS requests: pypi.org + GitHub commits API + raw GitHub __init__.py
       └─ dispatch commands[command] → cli/commands/chat.py:cmd_chat

2. cmd_chat (chat.py:22, 1199 LOC, single function with 25+ nested closures)
   ├─ _init_acp() / _build_agent()  → cli/agent_factory.py:104
   │     └─ Agent(model, tools, backend, ...) → AgentSetupMixin.__init__ (agent_setup.py:54)
   │           ├─ soul loader (soul/loader.py)  — try/except chain, falls back to _build_default_prompt()
   │           ├─ memory_config → Memory or PersistentMemory(sqlite)
   │           ├─ max_steps, max_api_retries, max_tool_retries, retry_on_error flags
   │           └─ thinking_level / think / reasoning_effort → model_family_config.needs_no_think_directive()
   ├─ _setup_footer_region()  → ANSI scroll-region escape (terminal-only)
   └─ REPL loop:  input("\001\033[90m\002You:\001\033[0m\002 ")
        ├─ slash command ("/help", "/tool", "/skill", "/param", ...) → inline if/elif chain (no dispatcher)
        └─ agent.run(user_input, stream=True)

3. agent.run() (agent.py:100)  ─────────────────────────────────────────────────
   ├─ emit on_run_start plugin hook (best-effort, silent on failure)
   └─ _run_core(prompt, stream) (agent.py:698)
        └─ if stream: _run_core_streaming (delegates to AgenticLoopMixin._run_loop_iteration
              with generate_fn=self._generate_stream)
           else: _run_loop_iteration(generate_fn=self._generate)

4. _run_loop_iteration (agentic_loop.py:134)  ─────────────────────────────────
   ├─ Response(status=QUEUED)  → mark IN_PROGRESS
   ├─ ErrorRecoveryTracker.reset()
   └─ for step_num in range(self.max_steps):
        ├─ callbacks.on_step_start(step_num)  → streaming-only compaction check
        ├─ _generate_with_retry(generate_fn, step_num, ...) (agent.py:176)
        │     ├─ gen_response = generate_fn()  → backend.generate(model, messages, tools, ...)
        │     ├─ on Exception: is_transient_api_error(e) ?
        │     │     └─ yes + retries < max: backoff_delay() w/ Retry-After + ±20% jitter, retry
        │     │     └─ no or exhausted: describe_terminal(e), _terminated=True, break
        │     └─ on 400 context-length (streaming only): self.memory.compact_messages(keep_count=10), retry
        ├─ parse gen_response → tool_calls / final_answer / neither
        ├─ if tool_calls: for each call → _execute_single_tool_call (agentic_loop.py:484)
        │     ├─ ToolExecutionMixin._execute_tool (tool_execution.py:33)
        │     │     ├─ tool lookup
        │     │     ├─ if tool.dangerous and confirm_dangerous: prompt user
        │     │     ├─ normalize_args(args, tool.params, tool_name)  → helpers.py:144
        │     │     └─ tool.execute(**normalized_args)
        │     ├─ on KeyboardInterrupt: response.mark_cancelled, return "break"
        │     └─ _process_tool_result → memory.add_tool_result / memory.add("user", observation)
        ├─ elif "Final Answer:" pattern → extract, break
        └─ else: enforce final answer or accept as final

5. backend.generate (ollama.py / openai_compat.py / zai.py / ...)
   ├─ resolve thinking params (think, reasoning_effort, model_family_config.needs_no_think_directive)
   ├─ cap max_tokens to num_ctx // 32 (empirical finding from R06.55)
   ├─ POST to backend URL (e.g., http://localhost:11434/api/chat for ollama)
   ├─ if streaming: yield SSE chunks, _iter_sse_lines() handles 429 retry + context-length 400 recovery
   └─ return dict: {content, tool_calls, finish_reason, usage}
```

---

## Dependency Graph

```
cli/commands/*  →  cli/agent_factory  →  Agent (agent.py)
                                              │
                ┌───────────────────────────────┤
                ▼                               ▼
       AgentSetupMixin                  AgenticLoopMixin ── ToolExecutionMixin ── CompactionMixin ── StreamingMixin
       (agent_setup.py)                 (agentic_loop.py) (tool_execution.py)   (compaction.py)    (streaming.py)
                │                               │                                   │
                ▼                               ▼                                   ▼
        soul/loader.py                   core/error_recovery.py            core/tool_parse.py
                │                               │                                   │
                ▼                               ▼                                   ▼
        core/models.py ◄──── core/helpers.py ◄────────────────────────────── core/api_resilience.py
                                  ▲                                      
                                  │                                      
                ┌─────────────────┴┴─────────────────┐
                │                                   │
        backends/base.py ◄── backends/openai_compat.py ◄── plugins/{zai,openrouter,gemini,openai,huggingface}
                │
                ▼
        backends/ollama.py
                │
                ▼
        config.py ◄─── referenced by EVERYTHING
```

Key coupling points:
- `core/helpers.py` is imported by 18+ modules — blast radius for any security change is huge
- `backends/openai_compat.py` is the shared base for 5 cloud plugins — bug here × 5 backends
- `plugins/_loader.py` PluginManager singleton loads at startup; failure cascades to all backends
- `agentkthx/__init__.py` has 3 try/except optional imports (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Class composition** | Mixin pattern: `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. Mixins access host via `self.X` with docstring-declared "host contract" — no type-checker verification |
| **Tool calling** | ReAct prompting for ALL models (`Action: tool_name\nAction Input: {json}`). No native-tool-call fallback — model must emit the format |
| **Tool args parsing** | 4-level fallback chain: `json.loads` → `json.loads(sanitized)` → `ast.literal_eval` → regex `expression` extraction → `{"input": raw_args}` |
| **Tool arg normalization** | 5-strategy matcher in `helpers.py:normalize_args`: alias → direct → case-insensitive → generic alias → prefix/substring (last is dangerously permissive) |
| **Error classification** | `is_error_result(result)` regex on first non-empty line — `traceback`, `error:`, `failed:` markers. `is_transient_api_error(e)` checks auth/404 (permanent) before 429/5xx (transient) |
| **API retry** | `max_api_retries=5` default, exponential backoff with `Retry-After` honor + ±20% jitter to desync concurrent agents |
| **Security** | Defense-in-depth via `validate_path` (allowed-prefix check), `sanitize_command` (regex denylist — docstring admits it's "a guardrail against model mistakes, not a defense against determined prompt injection"), `is_safe_url` (substring hostname blocklist), `safe_eval` (AST walker rejecting Attribute/Subscript/Lambda) |
| **Optional features** | 3 try/except ImportError blocks in `__init__.py` (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure, no warning |
| **Plugin manifest** | Dual-form: legacy top-level fields + `extensions["org.vts-tech.agentkthx"]` namespace. `compatibility` constraints warn-only, never enforce |
| **Backend abstraction** | `is_cloud: bool` attribute on `BaseBackend` (R06.57) — replaces prior hardcoded `[OPENROUTER, ZAI, GEMINI]` list across 8 sites |
| **Memory** | Sliding window on message count only (`max_messages`); `max_tokens` field exists but is unused (pruning deferred to `CompactionMixin` at 85% num_ctx) |
| **Soul loading** | 5-step path resolution: absolute → CWD-relative → `agentkthx.__file__` parent → `importlib.resources` → repeat with name suffix |
| **File naming** | `snake_case.py` for modules, `PascalCase` for classes, `SCREAMING_SNAKE` for module constants |
| **Tests** | Co-located in `tests/` (not next to source), `test_*.py` naming, pytest fixtures; ~984 tests, all mocked unit tests — no integration tier |
| **Comments** | Commit-message-style block comments at top of mixins documenting WHY extraction happened + line-count savings (e.g., `# MAINT-04 Phase 1: shared API-resilience retry loop`) |

---

## Known Landmines

1. **`Agent.add_tool` wipes conversation memory** (`agent.py:1061-1080`) — calls `self.memory.clear()` after registering the tool. The CLI's `/tool` slash command bypasses this by calling `agent.tools.register_tool(tool)` directly. Third-party code using the public `add_tool` API will be surprised. Fix: split into `register_tool` + explicit `rebuild_system_prompt`.

2. **`api_mode` default inconsistency** — `shared_args.py:171` sets `--api` default to `"openai"`, but `agent_factory.py:121` reads `getattr(args, "api_mode", "openre")`. Actual default is `"openai"` (OpenAI Chat-Completions mode), NOT OpenResponses — surprising given the framework's OpenResponses branding. Cloud backends need OpenAI mode; local backends work with either.

3. **`update_check.py` makes 3 sequential HTTPS requests on every CLI invocation** — `pypi.org/pypi/agentkthx/json` (~100KB), `api.github.com/repos/VTSTech/AgentKthx/commits/HEAD`, `raw.githubusercontent.com/.../__init__.py`. Each has a 1s timeout. On a slow/offline network, that's up to 3s of startup latency for EVERY `agentkthx chat`. Opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`. The cache was removed in R07.00 because "it kept hiding freshly-cut releases from the developer."

4. **`Memory.sanitize_history` mutates `_messages` in place** (`memory.py:140-209`) — Called on every `get_messages()` (every generate call). Mutating during iteration causes subtle bugs in nested calls.

5. **`_generate_stream` swallows JSON parse errors** (`streaming.py:838-843`) — When the model emits malformed tool_call argument JSON across SSE chunks, fallback is `args = {"_raw_arguments": args_str}`. This reaches the tool handler as a dict with a single `_raw_arguments` key that `normalize_args` doesn't know how to handle. The error appears as a generic "Error: missing required argument" with no hint that the JSON was malformed.

6. **`ErrorRecoveryTracker.consecutive_all` resets on ANY success** (`error_recovery.py:540-549`) — A single successful `get_time` call between two failing `calculator` calls resets the counter. The agent loops forever until `max_steps` if it alternates failing/succeeding tools.

7. **`plugins/_loader.py:768` sets `sys.modules[pkg_name] = package` BEFORE `spec.loader.exec_module()`** — Standard pattern for circular imports, but means a plugin that raises during `exec_module` leaves a partially-initialized module in `sys.modules`. Subsequent `import` returns the broken module without re-execution.

8. **`cli/parser.py:26` stashes `parser._subparsers_action = subparsers`** — Private argparse attribute. `cli/main.py:62` reads it via `getattr(parser, "_subparsers_action", None)`. If CPython argparse internals change, plugin CLI discovery silently breaks.

9. **External plugin import has no path restriction** (`plugins/_loader.py:753-769`) — `_import_entrypoint` for `root_kind in ("user", "env")` executes arbitrary Python from `~/.agentkthx/plugins/<name>/__init__.py` or `$AGENTKTHX_PLUGIN_PATH` with the user's privileges. No signature verification, no sandboxing. Document that plugin roots are trusted paths; `~/.agentkthx/plugins/` should be `0700`.

10. **`MemoryConfig.max_tokens` is unused** (`memory.py:18`) — Field exists with default `4096` but never enforced. Sliding window only fires on message count. Long agentic runs with large tool outputs hit `CompactionMixin` at 85% num_ctx, not the `max_tokens` ceiling.

11. **`Agent.__init__` `**kwargs` silently swallows typos** (`agent_setup.py:54-87`) — 5 stashed kwargs (`response_format`, `confirm_dangerous`, `persistent`, `session_id`, `memory_db`). Misspelling `confirm_dangerous=True` as `confirm_dangerous_tool=True` silently does nothing.

12. **`BUILTIN_REGISTRY = make_builtin_registry()` is a module-level singleton** (`builtins.py:1341`) — `_todo_stores` and `_active_todo_session` globals are also module-level. Two `Agent` instances in the same process share the same todo store unless `set_todo_session(session_id)` is called (Agent constructor does this only if `"todo" in self.tools.names()`).

13. **3 try/except ImportError blocks in `__init__.py`** (lines 134-158) — `PersistentMemory`, `ACPPlugin`, Soul types are silently `None` on import failure. Code that does `from agentkthx import ACPPlugin` gets `None` and only fails when instantiation is attempted.

14. **`is_safe_url` SSRF check uses substring matching on hostname** (`helpers.py:558-602`) — Bypassable via DNS rebinding (attacker.com resolves to 127.0.0.1 at request time), decimal/octal IP encoding (`http://2130706433/`), IPv6 mapped (`http://[::ffff:7f00:1]/`). Also `0.0.0.0` is blocked but `[::]` is not.

15. **`sanitize_command` returns the original command unchanged** (`helpers.py:605-706`) — The docstring is explicit: security comes from the blocked-command/injection-pattern denylist, not from sanitization. `bash` is NOT in `BLOCKED_COMMANDS` (only `exec`, `eval`, `source`). Heredoc `python3 - <<'EOF'` is not blocked. For production: run shell tool in seccomp sandbox.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **ORM/Database** | SQLite via stdlib `sqlite3` (no ORM) | Zero-dep constraint; SQLite is universally available; `PersistentMemory` is a thin `Memory` subclass |
| **HTTP client** | `urllib.request` (stdlib) | Zero-dep; works without `requests`. Trade-off: no connection pooling, manual SSE line buffering |
| **Tool calling** | ReAct prompting for ALL models (no native function-calling) | Single codepath; works with models that don't support native tool calls (small local models). Trade-off: pays 200-500 tokens of prompt overhead per call |
| **JEV (System-One decision) emulation** | Wrap any chat-capable LLM with a JSON-decision prompt | Avoids vendor lock-in to System-One; works with free LLMs. Trade-off: 2-3x reasoning tokens vs native API |
| **Agent class composition** | 5 mixins over god-class (R07.00) | Testable in isolation; clear separation of concerns. Trade-off: no type-checker can verify the mixin "host contract"; mixin methods access `self.X` declared only in another mixin |
| **`is_cloud` backend attribute** | Per-class attribute on `BaseBackend` | Replaces hardcoded `[OPENROUTER, ZAI, GEMINI]` lists in 8 sites. Adding a 5th cloud backend is now 1 line |
| **Plugin manager singleton** | Lazy-loaded global `_plugin_manager` | Backends can be lazy-resolved on first `get_backend(name)` call. Trade-off: import-time errors deferred to first use |
| **Soul Spec optional** | Default `soul="nova-helper"`; falls back to `_build_default_prompt()` on any error | Lets the framework boot even without soul packages installed. Trade-off: silent failure means Soul misconfigurations invisible without `--debug` |
| **Update check always-live** | Removed cache in R07.00 | "Cache kept hiding freshly-cut releases from the developer." Trade-off: 1-3s startup latency on every CLI invocation |
| **Zero dependencies** | `dependencies = []` in `pyproject.toml` | Fully reproducible install; `pip install -e .` in ~5s. Trade-off: must hand-roll SSE parsing, JSON streaming, AST walking |
| **Backends split across two locations** | Native (ollama, llama_server, bitnet) in `backends/`; cloud (zai, openrouter, gemini, openai, huggingface) in `plugins/` | Cloud backends are optional (only loaded if env vars present). Trade-off: a developer looking for "the OpenAI backend" must check both locations |

---

## What's Missing / Incomplete

1. **No integration tests** — All 984 tests are mocked unit tests. No test exercises the full `agentkthx chat` → backend → tool execution → memory persistence path end-to-end. Coverage baseline: **42.7% line coverage** over 13,580 measured statements (R07.01).
2. **No `black --check` or `ruff check` in CI** — `pyproject.toml` configures both tools but CI only runs pytest. Style drift goes undetected.
3. **No `mypy` / type checking** — `pyproject.toml` has no `[tool.mypy]` section despite using `from __future__ import annotations` and PEP 604 `X | Y` syntax. The mixin "host contract" would benefit most.
4. **No `CONTRIBUTING.md`** — `docs/CREDITS.md` lists contributors but there's no guide for new contributors.
5. **No `SECURITY.md`** — No documented vulnerability disclosure policy.
6. **`schemas/v0.2/plugin.schema.json` declared but not validated** — `_parse_manifest` does ad-hoc dict-shape checks; `jsonschema` is never used.
7. **`agentkthx/examples/` (11 files)** are demo scripts, not doctests — not run by pytest.
8. **`patches/fix_turbo_v_padding.py`** is a standalone script that patches llama-cpp-turboquant's C++ source — not integrated into the build, not tested, and the `.patch` files in `patches/` appear to be hand-written diffs with fake blob hashes.
9. **`agentkthx/core/model_config.py`** is a 30-line deprecated module emitting a `DeprecationWarning` on import — scheduled but not bound to a removal date.
10. **Tool output schema validation absent** — `tool.execute(**args)` returns `Any`; the agentic loop treats it as `str(result)`. No JSON Schema validation of tool outputs.
11. **No conversation export/import** — `PersistentMemory` stores in SQLite with a custom schema; no way to export a conversation as OpenResponses JSON for sharing or migration.
12. **No streaming `function_call_arguments.delta` events** — `core/openresponses.py:stream_function_call_events` exists but is never called from the agentic loop. OpenAI Responses API parity is incomplete.
13. **`docs/` has no API reference** — Only `ARCH.md` (long-form architecture), `PLUGIN_SPEC.md`, `TESTS.md`, and per-backend `*_API_TECHNICAL_REFERENCE.md` files. No Sphinx/MkDocs auto-generated API docs.

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py`, `core/agentic_loop.py`, `core/helpers.py`. The "Why It Matters" column tells you when to touch each.
2. **Understand the Request Lifecycle** — every chat / run / agent command flows through `cmd_chat → Agent.run → _run_core → _run_loop_iteration → backend.generate → tool dispatch`.
3. **Check Known Landmines** before changing:
   - Don't call `agent.add_tool` mid-session (use `agent.tools.register_tool` directly)
   - `--api` defaults to `"openai"`, not `"openre"`
   - `AGENTKTHX_NO_UPDATE_CHECK=1` skips the 3-HTTPS-request startup cost
   - `ErrorRecoveryTracker` resets on any success — don't rely on it stopping infinite loops
4. **Follow Patterns & Conventions** — ReAct prompting for all models, 4-level tool-arg parse fallback chain, defense-in-depth security (validate_path + sanitize_command + is_safe_url + safe_eval).
5. **If changing a critical file**, check the Dependency Graph for blast radius:
   - Touching `core/helpers.py` affects 18+ modules
   - Touching `backends/openai_compat.py` affects 5 cloud plugins
   - Touching `plugins/_loader.py` affects every backend
6. **Run tests before committing**: `python -m pytest tests/ -q` (~2.5s fast path). For coverage: `pytest --cov=agentkthx --cov-report=term-missing` (~5s).
7. **Read prior audits**: `audit/audit.md` tracks findings by ID (SEC-XX, ROB-XX, MAINT-XX, etc.) with closure deltas. New findings should extend the ID sequence.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. The 21-module `core/` package is the engine — most changes start there.
