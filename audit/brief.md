# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-30 | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `52d2f56` (R07.16, PyPI 0.7.16)
> Full regeneration — supersedes the R07.15-amended R07.12 brief in its entirety. Every section below was re-verified against the R07.16 tree (suite 2051 passed / 16 skipped). Register: 109 findings — 34 OPEN (in `audit/audit.md`) + 75 archived (68 CLOSED / 7 WONTFIX in `audit/deltas.md`, 69%). Dashboard: `python3 audit/generate_audit_dash.py --audit audit/audit.md --deltas audit/deltas.md --brief audit/brief.md --output dashboard.html`.

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, souls, and skills |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib `urllib`/`json`/`sqlite3`/`ast`/`subprocess`/`socket`/`ipaddress`/`threading`/`weakref` only); dev: pytest/black/ruff |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py:main()` → `cli/parser.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI 0.7.16) or `pip install -e .`; `agentkthx chat`, `agentkthx turbo start <model>`, `agentkthx models`, `agentkthx version`, ... (84+ CLI flags across 15 subcommands) |
| **Test Command** | `python -m pytest tests/ -q` → **2051 passed / 16 skipped in ~12s**; CI matrix Python 3.12/3.13 in `.github/workflows/ci.yml` + parallel coverage job + **required `lint` job** (`ruff check` + `black --check`, promoted to required in R07.15) |

---

## Architecture Map

```
agentkthx/agent.py            → Agent class — 5-mixin composition (~1,093 LOC; stable since R07.00)
agentkthx/agent_mode.py       → AgentMode + TaskPlan/Step/Action with rollback (822 LOC, untested — TEST-04)
agentkthx/orchestrator.py     → Multi-agent orchestrator (sequential + parallel + LLM-router, ~461 LOC)
agentkthx/core/               → 19 files incl. __init__, ~9,600 LOC
  ├─ agentic_loop.py          → Unified loop body; Ctrl+C → state.terminated (R07.06 ROB-01)
  ├─ streaming.py             → SSE streaming + OpenResponses event generator (1,065 LOC; R07.15 MAINT-08 split StreamAccumulator/StreamRenderer)
  ├─ compaction.py            → Context-window compaction mixin (~200 LOC)
  ├─ tool_parse.py            → ReAct / native-JSON / XML parser (661 LOC; R07.16: markdown-bold-tolerant ReAct regexes)
  ├─ tool_execution.py        → Tool dispatch + R07.15 FEAT-02 parallel independent tool-call batches
  ├─ agent_setup.py           → Agent.__init__ + soul loading + prompt variants (654 LOC; R07.16: _use_native_tools property)
  ├─ openresponses.py         → OpenResponses spec: Response state machine, items, SSE events (~1,050 LOC)
  ├─ api_resilience.py        → Transient-vs-permanent classifier + backoff w/ Retry-After + jitter (235 LOC)
  ├─ error_recovery.py        → ErrorRecoveryTracker, is_error_result (~920 LOC)
  ├─ helpers.py               → Security primitives (validate_path, sanitize_command, is_safe_url w/ bounded DNS, sanitize_tool_output) + normalize_args (~1,384 LOC)
  ├─ safe_eval.py             → AST-walking eval replacement (~263 LOC)
  ├─ memory.py                → Sliding-window + opt-in token-tier pruning (ROB-08); ROB-17 over-budget gap
  ├─ persistent_memory.py     → SQLite PersistentMemory(Memory); per-DB-path write locks (R07.15 MAINT-15); ROB-15/18 open
  ├─ model_family_config.py   → Per-family stop tokens / temperature / no-think directives (~480 LOC)
  └─ types.py                 → BackendType enum (R07.16: TURBOQUANT primary, LLAMA_SERVER deprecated alias) + ToolSupportLevel
agentkthx/cli/                → 23-file CLI package
agentkthx/cli/commands/       → 15 command modules: chat (1,307 LOC — MAINT-01), test, config, models, agent, tools, soul, turbo, ...
agentkthx/cli/agent_factory.py→ THE wiring file (735 LOC, +290 in R07.16): _build_agent (tool-support auto-detection), _get_catalog_defaults ladder (cloud/local/remote), apply_model_switch + insufficient-credits switch callback
agentkthx/backends/           → cloud_base.py + openai_compat + ollama + llama_server (class LlamaServerBackend — user-facing name "turboquant") + bitnet + ollama_registry + base
agentkthx/plugins/            → 12 plugins: acp, bitnet, gemini, huggingface, mistral, openai, openrouter, orcarouter, pollinations, turboquant, zai (+ test-plugin fixture) — each with plugin.json
  ├─ _loader.py               → PluginManager, manifest v0.2, Kahn-topo dependency loader, sha256 pins (opt-in — SEC-13), R07.15 ROB-11 transactional registration (~1,530 LOC)
  ├─ turboquant/turbo.py      → llama-server lifecycle: TurboState (now with num_predict, str flash_attn), _build_command (R07.16 speedup flags), _is_process_alive (ROB-33), start_server with GGUF-derived ctx (923 LOC)
  ├─ mistral/ + pollinations/ → carry the copy-paste retry-loop family (ROB-29/MAINT-23, ~160 LOC dup)
  └─ zai/ + orcarouter/       → CloudBackend-based; _generate_with_auth (ROB-25 defaults mismatch)
agentkthx/skills/             → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py
agentkthx/soul/               → Soul Spec v0.5 persona packages: loader.py (_build_tool_section — MAINT-24), types.py
agentkthx/souls/              → 3 bundled souls: nova-helper, nova-skills, nova-trading
agentkthx/tools/              → builtins.py (shell timeout clamp R07.16; _SSRFSafeRedirectHandler 5-hop budget), registry.py, sandboxed_repl.py (521 LOC)
agentkthx/update_check.py     → Live PyPI + GitHub check on EVERY CLI invocation (intentional, ROB-05 WONTFIX; opt out AGENTKTHX_NO_UPDATE_CHECK=1)
agentkthx/config.py           → Env-var-derived singletons; R07.16: TURBOQUANT_BASE_URL (old LLAMA_SERVER_BASE_URL still read as fallback)
audit/                        → This brief + audit.md (open findings) + deltas.md (archive) + split/verify/dash tooling
docs/                         → ARCH.md, USAGE.md (new R07.16), PLUGIN_SPEC.md(+v0.2), CHANGELOG.md, TESTS.md, docs/api/*_API_TECHNICAL_REFERENCE.md (8 backends)
tests/                        → 70 files, ~30,285 LOC, 2051 tests — all mocked unit tests, no integration tier (TEST-01)
scripts/                      → probe_{ollama,openai,gemini,zai,openrouter,huggingface,llama_server,bitnet,pollinations}.sh, probe_llama_server_tools.py (NEW R07.16 diagnostic), bump-version.sh
patches/                      → llama.cpp turboquant patches + standalone .py applier
schemas/v0.2/                 → plugin.schema.json (declared but NOT validated by code — ad-hoc dict-shape checks)
```

### Skip List

- `__pycache__/`, `.git/`, `*.egg-info/`, `build/`, `dist/`
- `agentnova-redirect/` and `localclaw-redirect/` — thin shims for backward-compat package names
- `agentkthx/plugins/test-plugin/` — fixture for plugin spec tests
- `agentkthx/examples/` — 11 demo scripts (not run by pytest)
- `AgentKthx.ipynb` — root-level notebook, not referenced in docs
- `agentkthx/core/prompts.py` — dead-code-but-kept `_build_tool_section` duplicate (kept for consistency; R07.16 synced its numeric example to `10`)

---

## Critical Files Index

The 10 most important files. Touch these for almost any meaningful change.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/cli/agent_factory.py` (735 LOC, +290 R07.16) | Wires CLI args → `Agent`. `_build_agent` (tool-support auto-detection), `_get_catalog_defaults` → `_get_cloud_catalog_defaults` / `_get_local_catalog_defaults` / `_probe_remote_catalog` / `_extract_ctx_from_model_entry`, `apply_model_switch`, `_register_model_switch_callback`. | The hottest file post-R07.16. Auto-detection (lines 227-290): for non-cloud backends with tools, reads the cached `test_tool_support` verdict — NATIVE keeps native, REACT/UNTESTED force `force_react=True`, NONE keeps native (model can't call tools either way); **no CLI opt-out (MAINT-25)** — the debug hint `--force-react=False` errors because the flag is `store_true`. The catalog ladder (lines ~448-580): local base_url → TurboState (PID-verified; **ROB-33: that check kills the server on Windows**) → Ollama GGUF `context_length` → `{}`; remote base_url → `list_models()` probe (exact name match, else first model; `n_ctx` for llama-server, `context_length` for Ollama; `num_predict = ctx // 32` per R06.55). `apply_model_switch` re-derives per-model state on `/model` AND on ZAI/OrcaRouter insufficient-credits fallback callbacks. |
| `agentkthx/plugins/turboquant/turbo.py` (923 LOC) | llama-server (the binary keeps llama.cpp's name) lifecycle: `TurboState` dataclass + load/save/clear, `_build_command`, `start_server`, `print_model_list`, `_is_process_alive`, `_free_port`. | R07.16 additions: `TurboState.num_predict` (int) and `flash_attn` changed bool→str (`"on"/"off"/"auto"`; legacy bool state files still load via `from_dict`'s dataclass-field filter + `isinstance` back-compat path in `_build_command`); ctx auto-derives from GGUF `context_length` (fallback `TURBOQUANT_DEFAULT_CTX`); `num_predict = ctx // 32` (`--num-predict 0` disables); new speedup flags `-tb/-b/-ub/--mlock/--numa`; `-fa` now always emitted WITH a value (the fork v0.3.0 rejects bare `-fa`). `_is_process_alive` (159-183) is the ROB-33 site — `os.kill(pid, 0)` = TerminateProcess on Windows. |
| `agentkthx/core/agent_setup.py` (654 LOC) | `AgentSetupMixin.__init__` — soul loading, memory wiring, system-prompt assembly. | R07.16: new `_use_native_tools` property = `_is_comp_mode and not force_react` — ALL behavior-affecting `_is_comp_mode` tool-prompt decisions now route through it. The no-soul default prompt has two variants: native ("call them naturally as function calls") vs ReAct (`Action:`/`Action Input:`/`Final Answer:`). `--force-react` (or the auto-detection) flips the prompt to ReAct while the `tools` array still ships in the request body (harmless for backends that ignore it). |
| `agentkthx/core/tool_parse.py` (661 LOC) | `ToolParser.parse(text)` — tries native JSON, ReAct, XML; per-strategy `_parse_*` methods. | R07.16: every ReAct keyword regex gained `\*{0,2}` on both sides — `**Action:**`, `**Action Input:**`, `**Thought:**`, `**Final Answer:**` (markdown-bold decorations from small local models, e.g. nemotron-3-nano:4b on Windows) parse identically to the plain form. Applies to `_THOUGHT_RE`, `_ACTION_RE`, `_ACTION_RE_SAMELINE`, `_FINAL_RE`, `is_final_answer()`, `extract_final_answer()`. Historical note: the R07.07 MAINT-14 fix (string-literal-safe True/False/None handling) is closed — the old "mangles prose" landmine is gone. |
| `agentkthx/cli/commands/chat.py` (1,307 LOC, grew from 1,199) | `cmd_chat` — the interactive REPL (MAINT-01: single function, 25+ nested closures, no slash-command dispatcher). | R07.16: `import readline` wrapped in try/except ImportError (Windows without pyreadline3 → `readline = None`, skip `parse_and_bind`); prompt built conditionally — readline form `"\001\033\002You:\001\033\002 "`, fallback `"\033You:\033 "` (**ROB-34: the fallback renders as `ou:` — bare `ESC Y` is a consumed 2-byte VT escape; verified in a terminal emulator; use plain `"You: "`**). Slash commands remain an inline if/elif chain; `/param`/`/model` behavior unchanged (see `apply_model_switch`). |
| `agentkthx/agent.py` (~1,093 LOC) | `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. | R07.16: `_rebuild_system_prompt_with_tools` (mid-session `add_tool`/`rebuild_system_prompt` path) now uses `_use_native_tools` instead of `_is_comp_mode` — previously a mid-session tool addition reverted the prompt to native-tools text even under `force_react`. `add_tool` remains a deprecated alias that clears memory and emits NO DeprecationWarning (MAINT-16 open) — use `register_tool` mid-session. `_generate_with_retry` wraps every generate with `is_transient_api_error` classification + backoff. |
| `agentkthx/core/helpers.py` (~1,384 LOC) | Security primitives + `normalize_args` + calc extraction. `validate_path`, `sanitize_command`, `is_safe_url` (bounded DNS via `_iter_hostname_ips`, R07.12), `sanitize_tool_output`. | Imported by 18+ modules — blast radius for any security change is huge. Open items here: ROB-09 (`validate_path` uses `abspath`, not `realpath` — symlink traversal), MAINT-03 (`normalize_args` strategy 5 prefix/substring matching is permissive; `CONTEXTUAL_ALIASES` mitigates known-ambiguous cases only). `sanitize_tool_output` wraps EVERY tool result (8KB truncation + secret redaction + ANSI strip; truncation-then-redaction order fixed R07.07, SEC-12 closed). |
| `agentkthx/core/agentic_loop.py` (~800 LOC) | `_run_loop_iteration` — unified agentic loop body: Response state machine, tool dispatch, error recovery, finish_reason handling. | `_process_tool_result` wraps every tool result via `sanitize_tool_output` BEFORE memory / FunctionCallOutputItem / `build_enhanced_observation`. R07.15 FEAT-02 (in `tool_execution.py`): INDEPENDENT tool-call batches execute concurrently on a 4-worker ThreadPoolExecutor, results commit in ORIGINAL call order (byte-identical transcript); shell/write_file/edit_file/todo stay sequential; `AGENTKTHX_PARALLEL_TOOLS=0` is the escape hatch. Ctrl+C in `_execute_single_tool_call` sets `state.terminated = True` (ROB-01). |
| `agentkthx/core/streaming.py` (1,065 LOC) | `StreamingMixin` — `_generate_stream` + OpenResponses SSE event generator + reasoning rendering. | R07.15 MAINT-08 split the 354-line orchestration into `StreamAccumulator` + `StreamRenderer`. KeyboardInterrupt path closes the stream generator (ROB-06 open: `.close()` may not deterministically release the TCP connection on Windows). Malformed tool-call argument JSON in streaming still falls back to `_raw_arguments` without the debug chain the ReAct path has. FEAT-06 open: `function_call_arguments.delta` SSE events not emitted. |
| `agentkthx/plugins/_loader.py` (~1,530 LOC) | `PluginManager` singleton, manifest v0.2 parser, Kahn topological-sort dependency loader, hook dispatch, external plugin import. | R07.15 ROB-11: transactional registration — `_PluginTransaction` records undo for every imperative `register_*` call during plugin `register()` and rolls back LIFO on load failure (the old sys.modules-before-exec_module landmine is closed). sha256 pins: `_validate_sha256_pin` (64-hex, fail-closed when present) but **opt-in — no `AGENTKTHX_REQUIRE_PLUGIN_PINS` enforcement mode (SEC-13 open)**; a manifest without a pin loads silently. |

### Additional files of note

| File | Why It Matters |
|------|----------------|
| `agentkthx/soul/loader.py` | `_build_tool_section` builds the `### Tool Reference` table appended to soul prompts. R07.16: numeric param examples are `10` (was `0` — small models copy the example verbatim and `timeout: 0` meant instant TimeoutExpired); the ReAct format block was REMOVED from this function (dedup — provided by the default prompt or the soul's own SOUL.md). Docstring still claims it includes them (**MAINT-24**); custom souls with neither their own block nor example placeholders get zero format instructions under ReAct. Also `_build_tool_section`'s forced-ReAct helper strings ("You MUST call at least one tool...") live here. |
| `agentkthx/backends/__init__.py` + `config.py` + `core/types.py` | R07.16 backend rename: `_BACKENDS` registry maps `"turboquant"` (primary) + `"llama-server"`/`"llama_server"` (aliases) → `LlamaServerBackend`; `BackendType.TURBOQUANT` is returned by `backend_type` (footer shows `🔌 turboquant`), `LLAMA_SERVER` enum value kept for third-party compat; `TURBOQUANT_BASE_URL` env var primary, `LLAMA_SERVER_BASE_URL` read as fallback, module alias exported. BitNet still routes through the same class with `_bitnet_mode=True` → `BackendType.BITNET`. |
| `agentkthx/backends/ollama_registry.py` | `OllamaModel.exists` (R07.16 fix): returns False when `blob_path` is empty (`Path("").exists()` is True — resolves to CWD), so `turbo list` against a REMOTE Ollama correctly shows `✗ blob missing / not pulled` while `weight_quant`/`context_length` now come from the API `details` block. `discover_models` parses GGUF headers (the `context_length` that feeds both `turbo start` auto-ctx and the chat-side ladder). |
| `agentkthx/tools/builtins.py` (~1,363 LOC) | R07.16: `shell()` clamps model-supplied timeout — `int(timeout)` with TypeError/ValueError → 30, then `max(1, min(timeout, 300))` (same clamp as `http_get`/`python_repl`; handles `timeout=0`, `"10"`, `-5`, `None`). Also `_SSRFSafeRedirectHandler` (R07.05 SEC-03, R07.12 5-hop budget) for `http_get`. `todo` store: `BUILTIN_REGISTRY` is a module-level singleton (shared across Agent instances in-process). |
| `agentkthx/core/persistent_memory.py` | Per-DB-path write locks via realpath-keyed `WeakValueDictionary` (R07.15 MAINT-15). Open: ROB-15 (`add()` = two separate lock acquisitions, 2× commit), ROB-18 (locks are `threading.Lock`, not `RLock`). `0o600`/`0o700` file perms (SEC-07). No conversation export/import (FEAT-07). |
| `agentkthx/plugins/pollinations/pollinations.py` | The only keyless backend. Open cluster: ROB-31 (`healthy_fallbacks()` ranks `paid_only` models the key can't generate under `POLLINATIONS_ANON_CATALOG=1`), ROB-30 (`_fetch_model_cards` bare `except Exception` → silent static-catalog degradation), MAINT-23 (retry-loop skeleton dup ×2, third backend in the family), FEAT-08 (free-TIER boundary on bare `/models` unreachable; FREE_ONLY exposes only 16 zero-cost models, not the ~102 Quest-Pollen-eligible), TEST-10 (no live-shape contract test). Zero-cost models are encoded as currency-only pricing dicts, NOT zero-valued fields. |
| `scripts/probe_llama_server_tools.py` (NEW R07.16) | Stdlib-only diagnostic that hits a running llama-server with 7 request shapes (health, models, bare chat, tools, tool_choice auto/required, /tools) and dumps raw HTTP — bypasses AgentKthx's chat path so errors shown are the server's, not ours. Auto-reads `~/.agentkthx/turbo.state`. |

---

## Request / Execution Lifecycle

```
1. `agentkthx chat` ──────────────────────────────────────────────────────────
   └─ cli/__main__.py → cli/main.py:main()
       ├─ get_plugin_manager().load_all()  → plugins/_loader.py (Kahn topo sort;
       │     sha256 pins verified BEFORE exec_module when present; R07.15 ROB-11
       │     transactional registration rolls back partial loads)
       ├─ _run_update_check() → 3 sequential HTTPS requests (pypi + GitHub commits
       │     + raw __init__.py) unless AGENTKTHX_NO_UPDATE_CHECK=1 (intentional, ROB-05 WONTFIX)
       └─ dispatch → cli/commands/chat.py:cmd_chat

2. cmd_chat → _build_agent (cli/agent_factory.py)  ── R07.16-heavy ──────────
   ├─ backend = get_backend(name)  ("turboquant" primary; "llama-server"/"llama_server"
   │     aliases; base_url from TURBOQUANT_BASE_URL, env LLAMA_SERVER_BASE_URL fallback)
   ├─ TOOL-SUPPORT AUTO-DETECTION (agent_factory.py:227-290, non-cloud + tools only):
   │     support = backend.test_tool_support(model, force_test=False)   # cached
   │     NATIVE → keep native · REACT → force_react=True · UNTESTED → force_react=True
   │     NONE → keep native (model can't call tools either way) · user --force-react wins
   │     (no opt-out — MAINT-25; cache file: ~/.agentkthx/tool_support.json)
   ├─ CATALOG LADDER for num_ctx/num_predict (_get_catalog_defaults):
   │     cloud  → provider catalog (existing R06.57 logic, extracted)
   │     local  → 1) TurboState.load() [PID-verified — ROB-33 Windows kill]
   │               2) Ollama GGUF context_length (find_model)
   │               3) {} → config.num_ctx
   │     remote → _probe_remote_catalog: backend.list_models() — exact name match,
   │               else first model; n_ctx (llama-server /v1/models meta) or
   │               context_length (Ollama /api/tags); num_predict = ctx // 32
   └─ Agent(model, tools, backend, force_react=effective, ...) → AgentSetupMixin.__init__
         ├─ system prompt: _use_native_tools ? native-tools text : ReAct text
         │   (soul path: soul/loader.py + _build_tool_section table; MAINT-24 caveat)
         └─ memory: Memory or PersistentMemory (sqlite, 0o600)

3. REPL loop (chat.py) ──────────────────────────────────────────────────────
   ├─ prompt: readline ? "\001\033\002You:\001\033\002 " : "\033You:\033 "  ← ROB-34
   ├─ slash commands: inline if/elif chain (no dispatcher — MAINT-01)
   │     /param num_ctx <v> → pins _num_ctx_explicit (survives /model)
   │     /model <name>      → apply_model_switch → re-derive ctx/predict/family
   └─ agent.run(user_input, stream=True)

4. agent.run() → _run_core → _run_loop_iteration (agentic_loop.py) ─────────
   for step in range(max_steps):
     ├─ _generate_with_retry (agent.py):
     │    generate_fn() → backend.generate / generate_completions_stream
     │    transient? → backoff (Retry-After + jitter) · 400 context-length? → compact
     ├─ parse: native tool_calls OR ReAct 4-level fallback chain
     │    (json → sanitized json → python-dict→JSON regex → expression → {"input": raw})
     │    R07.16: ReAct keywords tolerate markdown bold (**Action:**)
     ├─ tools: FEAT-02 — independent batches run on 4-worker pool, results commit
     │    in call order; sequential for shell/write_file/edit_file/todo
     └─ every result → sanitize_tool_output (8KB + secrets + ANSI) → memory

5. backend.generate (ollama / llama_server(turboquant) / openai_compat / cloud_base)
   ├─ cap max_tokens to num_ctx // 32 (R06.55); family stop tokens; no-think directives
   └─ streaming: SSE chunks → StreamAccumulator/StreamRenderer (R07.15); KeyboardInterrupt
        closes the generator (ROB-06: Windows conn release not deterministic)
```

---

## Dependency Graph

```
cli/commands/*  →  cli/agent_factory  →  Agent (agent.py)
                                             │
               ┌───────────────────────────────┤
               ▼                               ▼
      AgentSetupMixin                  AgenticLoopMixin ── ToolExecutionMixin ── CompactionMixin ── StreamingMixin
      (agent_setup.py)                 (agentic_loop.py)  (tool_execution.py)  (compaction.py)   (streaming.py)
               │                               │                                   │
               ▼                               ▼                                   ▼
       soul/loader.py                  core/error_recovery.py             core/tool_parse.py
               │                               │                                   │
               ▼                               ▼                                   ▼
       core/models.py ◄──── core/helpers.py ◄────────────────────────────── core/api_resilience.py
                                 ▲
                                 │
               ┌─────────────────┴┴─────────────────┐
               │                                    │
       backends/base.py ◄── backends/cloud_base.py ◄── plugins/{zai,openrouter,gemini,openai,huggingface,mistral,pollinations,orcarouter}
               │                                             ▲
               ▼                                             │
       backends/openai_compat.py ◄───────────────────────────┘
               │
               ▼
       backends/ollama.py ◄── backends/llama_server.py ◄── plugins/turboquant (lifecycle: TurboState, _build_command)
               │
               ▼
       config.py ◄─── referenced by EVERYTHING (TURBOQUANT_BASE_URL, OLLAMA_BASE_URL, ACP_*, ...)
```

Key coupling points:
- `core/helpers.py` is imported by 18+ modules — blast radius for any security change is huge
- `backends/cloud_base.py` is the shared base for 8 cloud plugins — bug here × 8 backends
- `cli/agent_factory.py` is now touched by BOTH chat startup paths (catalog ladder + tool detection) and the model-switch callback — changes here affect every backend launch
- `plugins/_loader.py` PluginManager singleton loads at startup; failure cascades to all backends
- `agentkthx/__init__.py` has try/except optional imports (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Class composition** | Mixin pattern: `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. Mixins access host via `self.X` with docstring-declared "host contract" — no type-checker verification |
| **Tool calling** | HYBRID since R07.16: native function-calling when `_use_native_tools` (comp mode AND not force_react — default for cloud backends); ReAct text prompting otherwise (`--force-react`, or local-backend auto-detection REACT/UNTESTED). The `tools` array always ships in the request body. Default prompt has a dedicated ReAct branch. |
| **Tool-call parsing** | Native JSON path first; ReAct path = 4-level fallback chain (json.loads → sanitized json.loads → regex python-dict→JSON → expression extraction → `{"input": raw}`), all ReAct keyword regexes markdown-bold-tolerant (`\*{0,2}` quantifiers, R07.16) |
| **Tool arg normalization** | 5-strategy matcher in `helpers.py:normalize_args`: alias → direct → case-insensitive → generic alias → prefix/substring (last is dangerously permissive — MAINT-03 open) |
| **Tool output sanitization** | `sanitize_tool_output()` wraps every tool result in `<tool_output>` tags with 8KB truncation, secret redaction, ANSI stripping (redact-then-truncate since R07.07) |
| **Parallel tools** | R07.15 FEAT-02: independent tool-call batches run concurrently (4 workers), results commit in call order; mutating/sequential tools excluded; `AGENTKTHX_PARALLEL_TOOLS=0` escape hatch |
| **Error classification** | `is_error_result` regex on first non-empty line; `is_transient_api_error(e, body=None)` — permanent markers first, auth/404 permanent, 429/5xx transient |
| **Context defaults** | `num_predict = num_ctx // 32` everywhere (R06.55 empirical cap, matched by `turbo start` and the chat-side ladder); GGUF `context_length` beats hardcoded 8K |
| **Security** | Defense-in-depth: `validate_path` (allowed-prefix; abspath not realpath — ROB-09), `sanitize_command` (denylist + shell/heredoc block), `is_safe_url` (ipaddress-based, bounded DNS 5s/32-records fail-closed, 5-hop redirect budget), `safe_eval` (AST walker), `sanitize_tool_output`, plugin sha256 pins (opt-in — SEC-13) |
| **Local backend UX** | R07.16: `turbo start` auto-derives ctx/num-predict from GGUF metadata; tri-state `--flash-attn on|off|auto`; CPU knobs `-tb/-b/-ub/--mlock/--numa`; `--` passthrough for anything else |
| **Memory** | Sliding window on message count; token tier opt-in (`MemoryConfig.max_tokens`, default `0`); compaction at 85% of num_ctx |
| **File naming** | `snake_case.py` modules, `PascalCase` classes, `SCREAMING_SNAKE` constants |
| **Tests** | Co-located in `tests/`, `test_*.py`, pytest fixtures; 2051 tests, all mocked unit tests — no integration tier (TEST-01). Per-release regression files (`test_r07_16_*` style is the convention; R07.16 added variants to existing files instead) |

---

## Known Landmines

1. **`_is_process_alive` KILLS the target on Windows** (ROB-33, `plugins/turboquant/turbo.py:159-183`) — `os.kill(pid, 0)` is TerminateProcess on Windows (any sig ≠ CTRL_* kills). R07.16 put this on the chat startup path via `TurboState.load()` in `_get_local_catalog_defaults` — `turbo start` + `chat` on Windows kills its own server; a stale state file with a reused PID can kill an UNRELATED process. POSIX is safe (`/proc` zombie check present).

2. **The no-readline chat prompt renders wrong** (ROB-34, `chat.py:291`) — `"\033You:\033 "`: `ESC Y` is a complete 2-byte VT escape (consumed → prompt shows `ou:`), and the trailing `ESC + space` pairs with the next echoed byte. Verified in a terminal emulator. Use plain `"You: "` or a real CSI sequence.

3. **`--force-react=False` is not a valid CLI invocation** (MAINT-25) — `--force-react` is `store_true` (parser.py:191); passing `=False` is an argparse ERROR. The UNTESTED debug hint suggests exactly that. No opt-out exists for the local-backend default-to-ReAct behavior short of deleting `~/.agentkthx/tool_support.json`.

4. **Custom souls can lose ReAct format instructions** (MAINT-24) — R07.16 removed the ReAct block from `_build_tool_section` (dedup). Souls that don't ship their own `Action:`/`Action Input:` block (and lack example placeholders) now produce prompts with ZERO format instructions when ReAct is active — the parser then sees no valid tool calls. The docstring still claims otherwise.

5. **Remote-catalog probe can stall chat startup** — `_probe_remote_catalog` calls `backend.list_models()` synchronously with the backend's own timeouts (llama-server `/v1/models` timeout=10s). A dead Cloudflare tunnel adds up to ~10s before falling back to `config.num_ctx`. Failure is silent (broad `except: pass`).

6. **`_is_local_base_url` excludes 172.16/12** — RFC1918 `172.16-31.x.x` backends take the REMOTE probe path (documented in-code); local TurboState/Ollama-catalog lookups are skipped for them. Empty/unknown URLs are treated as local (conservative).

7. **`Agent.add_tool` is deprecated but emits NO `DeprecationWarning`** (MAINT-16) — use `register_tool` mid-session (no memory clear). `add_tool` still clears conversation for backward compat, with no programmatic migration signal.

8. **`--api` default is `"openai"`, not `"openre"`** — `shared_args.py` sets openai (Chat-Completions); surprising given the OpenResponses branding.

9. **`update_check.py` makes 3 sequential HTTPS requests on every CLI invocation** — INTENTIONAL per owner (ROB-05 WONTFIX). Opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`.

10. **`Memory.sanitize_history` mutates `_messages` in place** on every `get_messages()` (PERF-01 open) — subtle bugs possible in nested iteration.

11. **Streaming-path JSON parse errors fall back to `{"_raw_arguments": ...}`** without the debug chain the ReAct path has — malformed streaming tool-call args are hard to diagnose without a reproducer.

12. **`ErrorRecoveryTracker.consecutive_all` resets on ANY success** — alternating fail/succeed tool calls loop until `max_steps`.

13. **`MemoryConfig.max_tokens` defaults to `0`** (tier disabled) — token-tier pruning is opt-in; long agentic runs rely on compaction at 85% num_ctx. Single messages larger than the whole budget still defeat the tier (ROB-17).

14. **`BUILTIN_REGISTRY` todo store is a module-level singleton** — two `Agent` instances in one process share todos unless `set_todo_session()` is called.

15. **`__init__.py` optional imports are silent `None`** — PersistentMemory/ACPPlugin/Soul import failures surface only as missing features, never as warnings.

16. **`normalize_args` strategy 5 matches substrings** (`{"e": ...}` → `expression`) — last-match-wins on dict order (MAINT-03). Prefer exact keys in tool args.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | `dependencies = []` | Reproducible install, no supply-chain surface; trade-off: hand-rolled SSE, SSRF, AST eval |
| **Backend rename** (R07.16) | `turboquant` primary; `llama-server`/`llama_server` aliases; `TURBOQUANT_BASE_URL` env primary with `LLAMA_SERVER_BASE_URL` fallback | Match the TurboQuant fork branding; binary stays `llama-server` (llama.cpp upstream name); old names kept so scripts and third-party enum checks keep working |
| **Local tool calling** (R07.16) | Auto-detect via cached `test_tool_support`; UNTESTED defaults to ReAct for local backends | Small CPU models typically aren't trained for native function calling; safe default, user `--force-react` wins |
| **Context auto-derivation** (R07.16) | GGUF `context_length` → ctx; `num_predict = ctx // 32` | Kills the hardcoded 8K mismatch (8K window vs 256K server); matches the cloud-backend max_tokens cap empirically found in R06.55 |
| **Tri-state flags** (R07.16) | `--flash-attn on|off|auto` (was `store_true`) | TurboQuant fork v0.3.0 requires an explicit `-fa <value>`; bare `-fa` crashed the server |
| **HTTP client** | `urllib.request` (stdlib) | Zero-dep; trade-off: no pooling, manual SSE buffering, GC-dependent close (ROB-06) |
| **Agent composition** | 5 mixins over god-class (R07.00) | Testable separation; trade-off: unverified mixin host contract |
| **`is_cloud` attribute** | Per-class on `BaseBackend` | Replaced hardcoded provider lists; cloud catalog defaults + detection gates hang off it |
| **`CloudBackend` base** (R07.05) | New cloud backends = ~100 LOC | Subclasses override data + 5 methods; retry-loop skeleton NOT yet lifted (ROB-29/MAINT-23 open) |
| **Update check always-live** | No cache (R07.00) | Owner's refresh script relies on uncached check (ROB-05 WONTFIX) |
| **Plugin pins opt-in** (R07.05) | sha256 verified fail-closed WHEN present | Closes SEC-06; no enforcement mode yet (SEC-13 open) |
| **Transactional plugin registration** (R07.15) | `_PluginTransaction` LIFO undo | Closes ROB-11 — partial registrations from failed loads are rolled back |
| **Parallel tool batches** (R07.15) | Concurrent for independent batches, committed in call order | Latency win with byte-identical transcripts; mutating tools stay sequential |
| **`/model` switch derivation** (R07.06) | `apply_model_switch` + explicit-flags | Re-derives ctx/predict/family; now also drives insufficient-credits auto-switch callbacks (R07.16 wiring) |
| ** Souls + skills bundling** | 3 souls, 4 skills in-tree | Persona/format split; soul loading is best-effort with default-prompt fallback |

---

## What's Missing / Incomplete

1. **34 OPEN findings** in `audit/audit.md` (full detail + priority matrix there): Security 2 (SEC-09 ACP Basic-Auth-over-HTTP default, SEC-13 no plugin-pin enforcement mode) · Robustness 14 (incl. ROB-33 Windows process-kill, ROB-34 broken fallback prompt, ROB-31 Pollinations entitlement mismatch, ROB-02 orchestrator thread join, ROB-06 Windows conn release, ROB-15/17/18 memory-store gaps, ROB-20/25 API asymmetries, ROB-09 symlink validate_path, ROB-28/29/30 catalog catch-alls + retry dup) · Maintainability 6 (MAINT-01 1,307-line cmd_chat, MAINT-03 fuzzy args, MAINT-22 streaming body bypass, MAINT-23 retry skeleton, MAINT-24 docstring contract, MAINT-25 no opt-out) · New Features 4 (FEAT-03 tool output schema, FEAT-05 plugin sandbox, FEAT-06 streaming arg deltas, FEAT-07 conversation export, FEAT-08 free-TIER mode) · Testing 8 (TEST-01 integration tier, TEST-03/04/05/07/09/10).
2. **No integration tests** — all 2051 tests are mocked unit tests (TEST-01); coverage baseline 42.7% (R07.01), CLI layer well below.
3. **No mypy** — no `[tool.mypy]`; mixins' host contracts are unverifiable by tooling.
4. **No `CONTRIBUTING.md` / `SECURITY.md`**.
5. **`schemas/v0.2/plugin.schema.json` declared but not validated** — ad-hoc dict checks in `_parse_manifest`.
6. **No conversation export/import** (FEAT-07); no tool output JSON Schema validation (FEAT-03); no streaming `function_call_arguments.delta` (FEAT-06).
7. **`agentkthx/examples/`** are demo scripts, not doctests; `patches/` not integrated into the build.
8. **R07.16 shipped no regression-test file** (suite count unchanged at 2051) — the auto-derivation ladder, tool-support auto-detection, and `_probe_remote_catalog` are tested only indirectly. Per house convention a `tests/test_r07_16_*.py` would pin the ladder precedence + the bold-tolerant parser + the Windows liveness check (which fails on Windows today per ROB-33).
9. **75 findings archived** in `audit/deltas.md` (68 CLOSED across R07.00–R07.15 + 7 WONTFIX with owner rationale — the closure timeline and per-release test-count deltas live there; `generate_audit_dash.py` merges both files for the full register).

---

## Quick Start for Developer

1. **Read the Critical Files Index** — start with `cli/agent_factory.py` (wiring), `plugins/turboquant/turbo.py` (server lifecycle), `core/agent_setup.py` (prompt strategy). The "Why It Matters" column tells you when to touch each.
2. **Understand the Lifecycle** — `cmd_chat → _build_agent (auto-detection + catalog ladder) → Agent.run → _run_loop_iteration → backend.generate → tool dispatch (sanitize → memory)`.
3. **Check Known Landmines** before changing:
   - Never probe process liveness with `os.kill(pid, 0)` on Windows paths (ROB-33)
   - Don't follow the UNTESTED debug hint — `--force-react=False` errors (MAINT-25)
   - Mid-session tool addition: `register_tool`, NOT `add_tool` (memory clear, no warning)
   - `AGENTKTHX_NO_UPDATE_CHECK=1` skips the 3-request startup check
   - `MemoryConfig.max_tokens=0` default — token tier is opt-in
4. **Follow Patterns** — hybrid native/ReAct tool calling via `_use_native_tools`; `num_predict = ctx // 32`; `sanitize_tool_output` on every tool result; per-release regression-test files.
5. **Blast radius**: `core/helpers.py` → 18+ modules · `backends/cloud_base.py` → 8 cloud plugins · `cli/agent_factory.py` → every backend launch · `plugins/_loader.py` → every backend load.
6. **Run tests before committing**: `python -m pytest tests/ -q` (~12s, 2051 tests). Lint is a REQUIRED CI check: `ruff check agentkthx/ tests/ && black --check agentkthx/ tests/`.
7. **Read the register before adding work**: `audit/audit.md` (34 OPEN, ID-indexed, priority matrix) + `audit/deltas.md` (75 archived with closure prose). Re-audit workflow, split tooling, and the dashboard parser contract are specified in `agentkthx/skills/codebase-audit/SKILL.md`.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. The `core/` package is the engine — most changes start there; R07.16-era work concentrates in `cli/agent_factory.py` + `plugins/turboquant/`.
