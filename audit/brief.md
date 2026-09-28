# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-09-28 | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `1033b6b` (R07.11, PyPI 0.7.11)
> Supersedes: R07.06 brief (2026-09-27) — updated in the R07.11 re-audit pass: +2 plugins since (mistral R07.09, pollinations R07.11 → 12 plugins / 7 cloud backends / 11 backend types), suite 1461 → 1751 passed (+290), register 98 → 104 findings (62 open). Full regeneration still due — this pass refreshed the header, suite counts, and the audit-findings sections.

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, and skills |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib `urllib`/`json`/`sqlite3`/`ast`/`subprocess`/`socket`/`ipaddress`/`threading` only); dev: pytest/black/ruff |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py:main()` → `cli/parser.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI 0.7.06) or `pip install -e .` from source; `agentkthx chat`, `agentkthx version`, `agentkthx models`, etc. (84+ CLI flags across 15 subcommands) |
| **Test Command** | `python -m pytest tests/ -q` → **1751 passed / 16 skipped in ~25s** (was 1461 / 9 at R07.06; +290 across R07.07–R07.11, incl. +94 for the pollinations plugin); CI matrix Python 3.12 / 3.13 in `.github/workflows/ci.yml`, parallel `coverage` job uploads 30-day `coverage.xml` artifact |

---

## Architecture Map

```
agentkthx/agent.py            → Agent class — 5-mixin composition (1089 LOC; stable since R07.00)
agentkthx/agent_mode.py       → AgentMode + TaskPlan/Step/Action with rollback (822 LOC)
agentkthx/orchestrator.py     → Multi-agent orchestrator (sequential + parallel + LLM-router, 461 LOC)
agentkthx/core/               → 20 modules (was 21; args_normal.py + model_config.py deleted in R07.05), ~9,150 LOC
  ├─ agentic_loop.py          → Unified loop body (794 LOC); ROB-01 Ctrl+C → state.terminated=True (R07.06)
  ├─ streaming.py             → SSE streaming + OpenResponses event generator (862 LOC; unchanged in R07.05/06)
  ├─ compaction.py            → Context-window compaction mixin (199 LOC)
  ├─ tool_parse.py            → ReAct / native-JSON / XML tool-call parser (546 LOC; SEC-02 ast removal + ROB-13 debug chain R07.05/06)
  ├─ tool_execution.py       → Tool dispatch entrypoint (103 LOC)
  ├─ agent_setup.py           → Agent.__init__ (22 params + **kwargs) + soul loading + 3 system-prompt variants (529 LOC)
  ├─ openresponses.py         → OpenResponses spec: Response state machine, items, 9 SSE event types (1053 LOC)
  ├─ api_resilience.py        → Transient-vs-permanent classifier + backoff w/ Retry-After + ±20% jitter; ROB-10 body-arg + snake_case markers (235 LOC)
  ├─ error_recovery.py        → ErrorRecoveryTracker, is_error_result, _is_simple_result; ROB-07 alt-traceback regex (923 LOC)
  ├─ helpers.py               → Security primitives (validate_path, sanitize_command, is_safe_url with new _iter_hostname_ips, sanitize_tool_output new in R07.05) + normalize_args + safe_eval re-export (1384 LOC, +270 since R07.04)
  ├─ safe_eval.py             → AST-walking eval replacement (rejects Attribute/Subscript/Lambda/comprehensions) (263 LOC)
  ├─ memory.py                → Sliding-window + sanitize_history; ROB-08 token-tier pruning, default max_tokens=0 (432 LOC)
  ├─ persistent_memory.py     → SQLite-backed PersistentMemory(Memory); ROB-03 _write_lock + SEC-07 0o600/0o700 (500 LOC)
  ├─ model_family_config.py  → Per-family stop tokens / temperature / no-think directives (479 LOC)
  └─ types.py                 → BackendType enum incl. ORCAROUTER (9 values, R07.05) (188 LOC)
agentkthx/cli/                → 23-file CLI package
agentkthx/cli/commands/        → 15 command modules: chat (1230 LOC), test, config, models, agent, tools, soul, ...
agentkthx/cli/agent_factory.py→ New apply_model_switch() (R07.06 ROB-14) re-derives per-model state on /model switch
agentkthx/backends/           → cloud_base.py NEW (R07.05 MAINT-02, 488 LOC) + openai_compat (965 LOC) + ollama + llama_server + bitnet + ollama_registry + base
agentkthx/plugins/            → 8 plugins (was 7; +orcarouter NEW in R07.05): acp, bitnet, gemini, huggingface, openai, openrouter, orcarouter, turboquant, zai — each with plugin.json + main .py
  ├─ _loader.py               → PluginManager, manifest v0.2 parser, Kahn topological-sort dependency loader, sha256 pin verification (R07.05 SEC-06), perms advisory (1531 LOC, +139)
  ├─ orcarouter/orcarouter.py→ NEW: 1156 LOC, free-tier error classification, fallback models, cost reporting
  └─ zai/zai.py               → Migrated to CloudBackend base (1141 LOC, was 1162; ~30 LOC of __init__ collapsed)
agentkthx/skills/             → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py
agentkthx/soul/                → Soul Spec v0.5 persona packages: loader.py, types.py
agentkthx/souls/              → 3 bundled souls: nova-helper, nova-skills, nova-trading
agentkthx/tools/               → builtins.py (1363 LOC, +25: _SSRFSafeRedirectHandler new in R07.05), registry.py, sandboxed_repl.py (521 LOC)
agentkthx/update_check.py    → Live PyPI + GitHub version check on EVERY CLI invocation (3 HTTPS requests; intentional per owner, ROB-05 WONTFIX)
agentkthx/config.py            → Env-var-derived singletons
audit/                         → This brief + audit.md (R07.06 baseline; R07.07 delta appended for new findings)
docs/                          → ARCH.md, PLUGIN_SPEC.md, *_API_TECHNICAL_REFERENCE.md (now under docs/api/), CHANGELOG.md, TESTS.md
tests/                         → 43 files, ~18,263 LOC (was 14,300), 1461 tests (was 984); biggest: test_orcarouter_backend (949 LOC NEW), test_cloud_backend_base (533 LOC NEW), test_r07_05_sec_fixes (433 LOC NEW), test_r07_06_rob_fixes (387 LOC NEW), test_model_switch_context (247 LOC NEW), test_zai_free_models (225 LOC NEW), test_openrouter_free_models (298 LOC NEW), test_get_model_max_context (207 LOC NEW), test_tool_output_sanitization (268 LOC NEW), test_r07_05_audit_fixes (364 LOC NEW)
scripts/                       → probe_{ollama,openai,gemini,zai,openrouter,huggingface,llama_server,bitnet}.sh, bump-version.sh
patches/                       → 2 llama.cpp turboquant patches + standalone .py applier
schemas/v0.2/                  → plugin.schema.json (declared but NOT validated by code — ad-hoc dict-shape checks instead)
```

### Skip List

- `__pycache__/`, `.git/`, `*.egg-info/`, `build/`, `dist/`
- `agentnova-redirect/` and `localclaw-redirect/` — thin shims for backward-compat package names
- `agentkthx/plugins/test-plugin/` — fixture for plugin spec tests
- `agentkthx/examples/` — 11 demo scripts (not run by pytest)
- `AgentKthx.ipynb` — root-level notebook, not referenced in docs
- `agentkthx/core/args_normal.py` and `agentkthx/core/model_config.py` — DELETED in R07.05 (MAINT-04, MAINT-06 closures)
- `agentkthx/cli/utils.py`'s dead-code trio (`_load_tool_cache`, `_save_tool_cache`, `_get_cloud_model_size`) — DELETED in R07.05 (MAINT-05 closure)

---

## Critical Files Index

The 10 most important files. Touch these for almost any meaningful change. 9 of 10 changed in R07.05-R07.06.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/agent.py` (1089 LOC) | `Agent` class — 5-mixin composition. Holds `run()`, `_generate_with_retry()`, `_generate()`, `_run_core()`, `add_tool`/`register_tool`/`rebuild_system_prompt` (R07.05 split). | R07.05 split `add_tool` into 3 methods: `register_tool()` (no memory clear — safe mid-session), `rebuild_system_prompt()` (explicit clear+rebuild for soul swaps), and `add_tool()` (deprecated alias, still clears for backward compat). **`add_tool` does NOT emit `DeprecationWarning`** — third-party callers have no programmatic migration signal. |
| `agentkthx/core/helpers.py` (1384 LOC, +270) | Security primitives + arg normalization + fuzzy matching + calc extraction. `validate_path`, `sanitize_command`, `is_safe_url` (now with `_iter_hostname_ips`/`_ip_address_blocked`), NEW `sanitize_tool_output` (R07.05 SEC-10). | Imported by 18+ modules. R07.05 added `sanitize_tool_output()` (8KB truncation + secret redaction + ANSI stripping) wired into every tool result. New SSRF defenses are DNS-resolution-based: **`_iter_hostname_ips` does unbounded synchronous `getaddrinfo` with no timeout** — DoS amplification risk on adversarial DNS (NEW finding SEC-11). |
| `agentkthx/core/agentic_loop.py` (794 LOC, +34) | `_run_loop_iteration` — unified agentic loop body. Drives `Response` state machine, tool dispatch, error recovery, finish_reason handling. | R07.06 ROB-01: Ctrl+C in `_execute_single_tool_call` now sets `state.terminated = True` (was: returned `"break"` without flag, leaving the run half-cancelled). R07.05 SEC-10: `_process_tool_result` wraps every tool result via `sanitize_tool_output` BEFORE passing to memory / FunctionCallOutputItem / `build_enhanced_observation`. **`_is_simple_result` now sees `<tool_output>` wrapper as first line** — regex checks for numeric/date/time no longer fire on sanitized output; behavior mitigated by `< 200` length check + `simple_tools` set membership. |
| `agentkthx/core/error_recovery.py` (923 LOC, +13) | `ErrorRecoveryTracker` state machine, `is_error_result` classifier, `should_terminate`, `build_enhanced_observation`, `_is_simple_result`. | R07.06 ROB-07: `_ERROR_FIRST_LINE_RE` expanded with alternative traceback framings (`During handling of the above exception`, `The above exception was the direct cause`, bare `File "...", line N`). **Residual risk**: prose containing `File "notes.txt"` in a tool result is still misclassified, but SEC-10 wrapping shields this — the wrapper tag is now the first line. |
| `agentkthx/plugins/_loader.py` (1531 LOC, +139) | `PluginManager` singleton, manifest v0.2 parser, Kahn topological-sort dependency loader, hook dispatch with per-plugin failure isolation, external plugin import via `spec_from_file_location`. | R07.05 SEC-06: optional `sha256` field on `plugin.json` (string = `__init__.py` hash; dict = relative file paths). `_validate_sha256_pin` fails manifest parse on malformed pins; `_verify_sha256_pins` runs BEFORE `exec_module` (fail-closed on mismatch/missing/escaping path); `_warn_loose_plugin_perms` advisory on group/world-writable plugin dirs (POSIX only, built-ins skipped). **Pin is OPT-IN** — plugins without `sha256` field still load (NEW finding SEC-13: no `AGENTKTHX_REQUIRE_PLUGIN_PINS` enforcement mode). |
| `agentkthx/backends/cloud_base.py` (488 LOC, NEW) | NEW `CloudBackend(OpenAICompatibleBackend)` base class consolidating ~5K LOC of structurally-duplicated cloud-backend boilerplate. | Class attributes subclasses MUST override: `MODELS`, `_api_key_env_var`, `_default_base_url`, `_default_model`, `_provider_label`. Optional hooks: `_validate_api_key`, `_extra_auth_headers`, `_catalog_family_name`, `_catalog_backend_name`. `backend_type` left as `raise NotImplementedError`. **Constructor mutates `os.environ["AGENTKTHX_API_MODE"]`** (line 173) — process-global side effect, last-instance-wins (NEW finding SEC-15, inherited ARCH-01 pattern). Hardcoded 128K context fallback. API-key min length 8 chars (weak). |
| `agentkthx/core/api_resilience.py` (235 LOC, +6) | Transient-vs-permanent error classifier + backoff w/ Retry-After + ±20% jitter. | R07.06 ROB-10: `is_transient_api_error(exc, body=None)` accepts optional body arg. When provided, body is lowercased + checked against `_PERMANENT_MARKERS` FIRST. New markers: `invalid_request`, `context_length`, `model_not_found`, `invalid_api_key` (snake_case forms that prose-form markers never matched). **No backend currently passes `body`** — backends embed body in `RuntimeError(f"... {body}")` already, so the str(exc) check still catches it. Forward-looking API addition, unused today. |
| `agentkthx/core/tool_parse.py` (546 LOC, +55) | `ToolParser.parse(text)` — tries native JSON, ReAct, XML; per-strategy `_parse_*` methods. | R07.05 SEC-02: `ast.literal_eval` fallback REMOVED. Replaced by regex-based Python-dict→JSON converter (single→double quotes, `True`→`true`, `False`→`false`, `None`→`null`). R07.06 ROB-13: `ToolParser(tool_names, debug=False)` now records failure-reason per level and prints full chain under `debug`. **HIGH-SEVERITY BUG**: the `\bTrue\b`/`\bFalse\b`/`\bNone\b` regex substitutions do NOT respect string-literal boundaries — `{"prompt": "None of the above is True"}` gets silently mangled to `{"prompt": "null of the above is true"}` (NEW finding MAINT-14, should be re-classified ROB-High). Verified by reproducer. |
| `agentkthx/core/streaming.py` (862 LOC, unchanged) | `StreamingMixin._generate_stream` (354 lines!) + OpenResponses SSE event generator + reasoning-panel rendering. | Unchanged in R07.05/06. Still owns chat UX. Hard to test (side-effecting stdout writes). KeyboardInterrupt path closes the urllib response. MAINT-08 (extract `StreamAccumulator`) still open. |
| `agentkthx/core/agent_setup.py` (529 LOC, +11) | `AgentSetupMixin.__init__` — 22 explicit params + `**kwargs` for 5 more. Soul loading, system prompt assembly (4 variants). | R07.06 ROB-13: `ToolParser(self.tools.names(), debug=self.debug)` threads debug flag. R07.05 SEC-10: all 3 system-prompt builders gained "untrusted tool output" instruction (duplicated verbatim 3× — NEW finding MAINT-17). **ARCH-05 `**kwargs` swallowing concern REMAINS OPEN** — typos in `response_format`, `confirm_dangerous`, `persistent`, `session_id`, `memory_db` are silently ignored. R07.05/06 did NOT address this. |

### Additional files of note

| File | Why It Matters |
|------|----------------|
| `agentkthx/plugins/orcarouter/orcarouter.py` (1156 LOC, NEW) | 10th backend (6th cloud), first scaffolded from scratch on `CloudBackend`. Free-tier error classification distinguishes retryable (`err_free_rate`) from terminal (`err_free_used`, `free_quota_exhausted`). `ORCAROUTER_FALLBACK_MODELS` env var → `extra_body.models` (up to 5, `route: "fallback"`). `ORCAROUTER_INCLUDE_COST` → per-request cost reporting. **NEW findings**: `time.sleep(retry_after)` unbounded (R07.06 ROB-23 — `Retry-After: 3600` hangs agent for 1 hour); `_extract_buy_credits_url` surfaces attacker-controlled URL in user-facing error (NEW SEC-16 — phishing vector); ~150 LOC of retry logic duplicated between streaming/non-streaming paths (NEW MAINT-11). |
| `agentkthx/plugins/zai/zai.py` (1141 LOC, refactored) | First plugin migrated to `CloudBackend` base. `__init__` collapsed to single `super().__init__()` call. Catalog updated: `glm-5.3-flash` correctly marked as NOT free (was bug). `get_model_info` returns default 128K entry for unknown models (ZAI accepts any model ID). |
| `agentkthx/cli/agent_factory.py` (R07.06 +94 LOC) | NEW `apply_model_switch(agent, new_model) -> dict`. Re-derives `num_ctx`, `num_predict`, `model_config`, `model_family` on `/model` switch. `_build_agent` stashes `_num_ctx_explicit` / `_num_predict_explicit` flags; `/param num_ctx <v>` at runtime sets the flag too (chat.py:900). `/model` prints derived deltas. |
| `agentkthx/tools/builtins.py` (1363 LOC, +25) | NEW `_SSRFSafeRedirectHandler` (R07.05 SEC-03). `http_get` opens through this handler so 30x redirects re-validate via `is_safe_url` on every hop. **NEW finding SEC-17**: each `is_safe_url` call on a redirect hop triggers DNS resolution (`_iter_hostname_ips` → `socket.getaddrinfo`) — unbounded redirect chain = DoS. |
| `agentkthx/core/memory.py` (432 LOC, +6) | R07.06 ROB-08: `MemoryConfig.max_tokens` default flipped `4096 → 0` (was never enforced; turning it on would prune tool-heavy histories to ~2 results since `sanitize_tool_output` caps results at 8KB ≈ 2K est. tokens each). Token-tier pruning now real but opt-in. |

---

## Request / Execution Lifecycle

```
1. `agentkthx chat`  ──────────────────────────────────────────────────────────
   └─ cli/__main__.py → cli/main.py:main()
       ├─ get_plugin_manager().load_all()  → plugins/_loader.py:_resolve_load_order (Kahn topological sort)
       │     └─ _verify_sha256_pins() runs BEFORE exec_module (R07.05 SEC-06, fail-closed on mismatch)
       │     └─ _warn_loose_plugin_perms() advisory (POSIX only, built-ins skipped)
       ├─ atexit.register(emit on_shutdown)
       ├─ create_parser()  → cli/parser.py (stashes private parser._subparsers_action — argparse internals hack)
       ├─ plugin-discovered CLI commands → subparsers_action.add_parser()
       ├─ _run_update_check()  → cli/banner.py:94  → update_check.py:check_for_update(timeout=1.0)
       │     └─ 3 sequential HTTPS requests: pypi.org + GitHub commits API + raw GitHub __init__.py
       │     (INTENTIONAL per owner — ROB-05 WONTFIX; opt out with AGENTKTHX_NO_UPDATE_CHECK=1)
       └─ dispatch commands[command] → cli/commands/chat.py:cmd_chat

2. cmd_chat (chat.py:22, 1230 LOC, single function with 25+ nested closures)
   ├─ _init_acp() / _build_agent()  → cli/agent_factory.py:104
   │     └─ Agent(model, tools, backend, ...) → AgentSetupMixin.__init__ (agent_setup.py:54)
   │           ├─ soul loader (soul/loader.py)  — try/except chain, falls back to _build_default_prompt()
   │           ├─ memory_config → Memory or PersistentMemory(sqlite, 0o600 file mode R07.05 SEC-07)
   │           ├─ max_steps, max_api_retries, max_tool_retries, retry_on_error flags
   │           ├─ thinking_level / think / reasoning_effort → model_family_config.needs_no_think_directive()
   │           ├─ ToolParser(self.tools.names(), debug=self.debug)  ← R07.06 ROB-13
   │           └─ stashes _num_ctx_explicit / _num_predict_explicit for /model switch (R07.06 ROB-14)
   ├─ _setup_footer_region()  → ANSI scroll-region escape (terminal-only)
   └─ REPL loop:  input("\001\033\002You:\001\033\002 ")
        ├─ slash command ("/help", "/tool", "/skill", "/param", "/model", ...) → inline if/elif chain (no dispatcher)
        │     ├─ /param num_ctx <v>  → sets _num_ctx_explicit=True (chat.py:900, survives /model switch)
        │     ├─ /param reset        → un-pins _num_ctx_explicit/_num_predict_explicit (chat.py:781)
        │     └─ /model <name>       → apply_model_switch(agent, new_model) → re-derives per-model state (R07.06 ROB-14)
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
        │     ├─ on Exception: is_transient_api_error(e) ?  ← R07.06 ROB-10 now checks body arg if passed
        │     │     └─ yes + retries < max: backoff_delay() w/ Retry-After + ±20% jitter, retry
        │     │     └─ no or exhausted: describe_terminal(e), _terminated=True, break
        │     └─ on 400 context-length (streaming only): self.memory.compact_messages(keep_count=10), retry
        ├─ parse gen_response → tool_calls / final_answer / neither
        │     └─ if ReAct: _parse_react tries 4-level fallback chain:
        │          1. json.loads(raw_args)
        │          2. json.loads(_sanitize_model_json(raw_args))
        │          3. regex python-dict→JSON conversion: single→double quotes, True→true, False→false, None→null (R07.05 SEC-02)
        │             ⚠️ MAINT-14: \bTrue\b/\bFalse\b/\bNone\b substitutions corrupt values containing these words as prose
        │          4. regex extraction of 'expression' field
        │          5. fallback {"input": raw_args}
        │          └─ R07.06 ROB-13: per-level failure reasons recorded + printed under debug
        ├─ if tool_calls: for each call → _execute_single_tool_call (agentic_loop.py:484)
        │     ├─ ToolExecutionMixin._execute_tool (tool_execution.py:33)
        │     │     ├─ tool lookup
        │     │     ├─ if tool.dangerous and confirm_dangerous: prompt user
        │     │     ├─ normalize_args(args, tool.params, tool_name)  → helpers.py:144
        │     │     └─ tool.execute(**normalized_args)
        │     ├─ on KeyboardInterrupt: state.terminated = True; response.mark_cancelled; return "break"  ← R07.06 ROB-01
        │     └─ _process_tool_result →
        │          ├─ sanitized_output = sanitize_tool_output(result, tool_name, call_id, max_chars=8192)  ← R07.05 SEC-10
        │          │   ├─ truncation to 8KB with [truncated, N more chars] marker
        │          │   ├─ secret redaction: password=, api_key:, Bearer, AWS_ACCESS_KEY_ID=, aws_secret_access_key=, connection_string=
        │          │   └─ ANSI escape stripping
        │          ├─ create_function_call_output(sanitized_output)
        │          ├─ memory.add_tool_result(sanitized_output)
        │          ├─ build_enhanced_observation(sanitized_output)
        │          └─ state.last_successful_result = sanitized_output
        ├─ elif "Final Answer:" pattern → extract, break
        └─ else: enforce final answer or accept as final

5. backend.generate (ollama.py / openai_compat.py / zai.py / orcarouter.py / ...)
   ├─ resolve thinking params (think, reasoning_effort, model_family_config.needs_no_think_directive)
   ├─ cap max_tokens to num_ctx // 32 (empirical finding from R06.55)
   ├─ POST to backend URL (e.g., http://localhost:11434/api/chat for ollama)
   ├─ if streaming: yield SSE chunks, _iter_sse_lines() handles 429 retry + context-length 400 recovery
   │     └─ OrcaRouter: _iter_sse_lines also handles free-tier retryable/terminal classification, fallback model swap,
   │        Retry-After wait (UNBOUNDED — R07.06 candidate finding)
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
        backends/base.py ◄── backends/cloud_base.py (NEW R07.05) ◄── plugins/{zai,openrouter,gemini,openai,huggingface,orcarouter}
                │                                              ▲
                ▼                                              │
        backends/openai_compat.py ◄────────────────────────────┘
                │
                ▼
        backends/ollama.py
                │
                ▼
        config.py ◄─── referenced by EVERYTHING
```

Key coupling points:
- `core/helpers.py` is imported by 18+ modules — blast radius for any security change is huge
- `backends/cloud_base.py` is the new shared base for 6 cloud plugins (5 migrated + OrcaRouter NEW) — bug here × 6 backends
- `plugins/_loader.py` PluginManager singleton loads at startup; failure cascades to all backends
- `agentkthx/__init__.py` has 3 try/except optional imports (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Class composition** | Mixin pattern: `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. Mixins access host via `self.X` with docstring-declared "host contract" — no type-checker verification |
| **Tool calling** | ReAct prompting for ALL models (`Action: tool_name\nAction Input: {json}`). No native-tool-call fallback — model must emit the format |
| **Tool args parsing** | 4-level fallback chain (R07.05 SEC-02): `json.loads` → `json.loads(sanitized)` → regex python-dict→JSON conversion (single→double quotes, `True`→`true`, etc.) → regex `expression` extraction → `{"input": raw_args}`. **`ast.literal_eval` removed**. ⚠️ **MAINT-14 NEW**: regex substitutions mangle values containing `True`/`False`/`None` as prose |
| **Tool arg normalization** | 5-strategy matcher in `helpers.py:normalize_args`: alias → direct → case-insensitive → generic alias → prefix/substring (last is dangerously permissive — MAINT-03 still open) |
| **Tool output sanitization** (R07.05 NEW) | `sanitize_tool_output()` wraps every tool result in `<tool_output tool="X" call_id="Y">...</tool_output>` with 8KB truncation, secret redaction, ANSI stripping. All 3 system prompts updated with untrusted-data instruction |
| **Error classification** | `is_error_result(result)` regex on first non-empty line — `traceback`, `error:`, `failed:` markers + R07.06 alternative traceback framings. `is_transient_api_error(e, body=None)` checks permanent markers in body FIRST (R07.06), then auth/404 (permanent), then 429/5xx (transient) |
| **API retry** | `max_api_retries=5` default, exponential backoff with `Retry-After` honor + ±20% jitter. OrcaRouter: `time.sleep(retry_after)` UNBOUNDED (candidate finding) |
| **Security** | Defense-in-depth: `validate_path` (allowed-prefix; **still uses abspath not realpath — ROB-09 open**), `sanitize_command` (regex denylist + R07.05 shell block + heredoc detection), `is_safe_url` (R07.05 `ipaddress`-based with DNS resolution; **NEW SEC-11 unbounded getaddrinfo**), `safe_eval` (AST walker), `sanitize_tool_output` (R07.05 tool-output wrapping), plugin `sha256` pin verification (R07.05, opt-in) |
| **Optional features** | 3 try/except ImportError blocks in `__init__.py` (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure, no warning |
| **Plugin manifest** | Dual-form: legacy top-level fields + `extensions["org.vts-tech.agentkthx"]` namespace. `compatibility` constraints warn-only. **R07.05 NEW**: optional `sha256` field for content verification (string=package `__init__.py`; dict=relative file paths) |
| **Backend abstraction** | `is_cloud: bool` attribute on `BaseBackend`. R07.05 NEW `CloudBackend` base class in `backends/cloud_base.py` consolidates ~5K LOC of duplicated cloud-backend boilerplate. New cloud backend = ~100 LOC instead of ~1500 LOC. Cloud backends as of R07.11 (7): zai, openrouter, gemini, openai, huggingface, mistral (R07.09), pollinations (R07.11 — the ONLY keyless/anonymous-tier backend; `poll` alias) |
| **Memory** | Sliding window on message count (`max_messages`); R07.06 NEW token-based second pruning tier (`MemoryConfig.max_tokens`, default `0` = disabled, opt-in via `MemoryConfig(max_tokens=100000)`). Long agentic runs rely on `CompactionMixin` at 85% num_ctx |
| **Soul loading** | 5-step path resolution: absolute → CWD-relative → `agentkthx.__file__` parent → `importlib.resources` → repeat with name suffix |
| **File naming** | `snake_case.py` for modules, `PascalCase` for classes, `SCREAMING_SNAKE` for module constants |
| **Tests** | Co-located in `tests/`, `test_*.py` naming, pytest fixtures; 1461 tests, all mocked unit tests — no integration tier (TEST-01 still open) |
| **Comments** | Commit-message-style block comments at top of mixins documenting WHY extraction happened + line-count savings; closure deltas embedded as banner blocks in audit.md |

---

## Known Landmines

1. **`Agent.add_tool` is now deprecated but emits NO `DeprecationWarning`** (`agent.py:1131`, R07.05 ROB-04 closure) — split into `register_tool` (safe mid-session, no memory clear), `rebuild_system_prompt` (explicit clear+rebuild), `add_tool` (deprecated, still clears for backward compat). Third-party code has no programmatic signal to migrate. **NEW finding MAINT-16.**

2. **`api_mode` default inconsistency** — `shared_args.py:171` sets `--api` default to `"openai"`, but `agent_factory.py:121` reads `getattr(args, "api_mode", "openre")`. Actual default is `"openai"` (OpenAI Chat-Completions mode), NOT OpenResponses — surprising given the framework's OpenResponses branding.

3. **`update_check.py` makes 3 sequential HTTPS requests on every CLI invocation** — INTENTIONAL per owner (ROB-05 WONTFIX in R07.05): VTSTech's refresh script relies on the uncached check. Opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`.

4. **`Memory.sanitize_history` mutates `_messages` in place** (`memory.py:140-209`) — Called on every `get_messages()` (every generate call). Mutating during iteration causes subtle bugs in nested calls. PERF-01 still open.

5. **`_generate_stream` swallows JSON parse errors** (`streaming.py:838-843`) — When the model emits malformed tool_call argument JSON across SSE chunks, fallback is `args = {"_raw_arguments": args_str}`. R07.06 ROB-13 closure added debug logging in `_parse_react`, but the streaming-path JSON parse in `_generate_stream` does not have the same debug chain.

6. **`ErrorRecoveryTracker.consecutive_all` resets on ANY success** (`error_recovery.py:540-549`) — A single successful `get_time` call between two failing `calculator` calls resets the counter. Agent loops until `max_steps` if it alternates failing/succeeding tools. Still open.

7. **`plugins/_loader.py:768` sets `sys.modules[pkg_name] = package` BEFORE `spec.loader.exec_module()`** — Standard pattern for circular imports, but means a plugin that raises during `exec_module` leaves a partially-initialized module in `sys.modules`. ROB-11 still open.

8. **`cli/parser.py:26` stashes `parser._subparsers_action = subparsers`** — Private argparse attribute. ARCH issue, unchanged.

9. **External plugin import has no path restriction** (`plugins/_loader.py:753-769`) — `_import_entrypoint` for `root_kind in ("user", "env")` executes arbitrary Python from `~/.agentkthx/plugins/<name>/__init__.py` or `$AGENTKTHX_PLUGIN_PATH`. R07.05 SEC-06 added optional `sha256` pin verification, but **pins are opt-in** — plugins without `sha256` field still load (NEW finding SEC-13: no enforcement mode).

10. **`MemoryConfig.max_tokens` default flipped to `0`** (R07.06 ROB-08) — Was `4096` (never enforced). Now real but opt-in. Existing users who relied on (claimed) 4096 enforcement now have NO token-tier pruning. Set `MemoryConfig(max_tokens=100000)` explicitly to enable.

11. **`Agent.__init__` `**kwargs` silently swallows typos** (`agent_setup.py:54-87`) — 5 stashed kwargs (`response_format`, `confirm_dangerous`, `persistent`, `session_id`, `memory_db`). ARCH-05 still OPEN — R07.05/06 did NOT address this.

12. **`BUILTIN_REGISTRY = make_builtin_registry()` is a module-level singleton** (`builtins.py:1341`) — Two `Agent` instances in the same process share the same todo store unless `set_todo_session(session_id)` is called.

13. **3 try/except ImportError blocks in `__init__.py`** — `PersistentMemory`, `ACPPlugin`, Soul types are silently `None` on import failure.

14. **`is_safe_url` SSRF check now resolves DNS** (R07.05 SEC-03 closure) — `_iter_hostname_ips` calls `socket.getaddrinfo(host, None)` synchronously with no timeout, no cache, no cap on returned IPs. **NEW finding SEC-11**: malicious DNS server returning thousands of A records blocks the agent; slow upstream resolver stalls every tool call. Combined with `_SSRFSafeRedirectHandler` (NEW finding SEC-17): every redirect hop triggers another DNS lookup, unbounded redirect chain = DoS.

15. **`sanitize_command` heredoc regex** (R07.05 SEC-04 closure) — `bash`/`sh`/`zsh`/`ksh`/`fish` added to `BLOCKED_COMMANDS`; heredoc pattern `<<\s*['\"]?[A-Za-z_]\w*` added to injection regexes. **Residual gaps**: brace expansion and ANSI-C quoting documented but not blocked. `bash <script>` form rejected; direct `./script.sh` invocation still works (shebang honored).

16. **`sanitize_tool_output` truncates AFTER redaction** (`helpers.py:1306`) — A secret whose `password=secret` lands just past the 8KB cutoff is NOT redacted — the truncation drops the redacted prefix but the unredacted tail still contains the secret value. **NEW finding SEC-12 (subagent mislabeled as ROB-13).** Fix: redact AFTER truncating.

17. **`CloudBackend.__init__` mutates `os.environ["AGENTKTHX_API_MODE"]`** (`cloud_base.py:173`) — Process-global side effect from a constructor. Two `CloudBackend` instances with different `api_mode` fight over this env var; last-constructed wins. Inherited from the old per-plugin pattern; the consolidation inherits the issue rather than fixing it. **NEW finding SEC-15.**

18. **`tool_parse.py` `\bTrue\b` / `\bFalse\b` / `\bNone\b` regex substitutions mangle string values** (R07.05 SEC-02 closure) — Substitutions run AFTER single→double quote conversion but on the WHOLE string, not respecting string-literal boundaries. `{"prompt": "None of the above is True"}` becomes `{"prompt": "null of the above is true"}`. Verified by reproducer. **NEW finding MAINT-14 — should be re-classified ROB-High (correctness bug, silent data corruption).**

19. **OrcaRouter `time.sleep(retry_after)` unbounded** (`orcarouter.py:848`) — `_parse_retry_after_seconds` returns `float(header)` with no cap. Malicious or buggy upstream returning `Retry-After: 3600` hangs the agent for an hour. **NEW finding ROB-23.**

20. **OrcaRouter `_extract_buy_credits_url` surfaces attacker-controlled URL** (`orcarouter.py:229`) — The URL from the upstream provider's error body is interpolated directly into the user-facing RuntimeError message. An attacker controlling a malicious upstream provider (or MITM if HTTPS isn't enforced) could inject a phishing URL. **NEW finding SEC-16.**

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **ORM/Database** | SQLite via stdlib `sqlite3` (no ORM) | Zero-dep constraint; SQLite universally available; `PersistentMemory` is a thin `Memory` subclass |
| **HTTP client** | `urllib.request` (stdlib) | Zero-dep. Trade-off: no connection pooling, manual SSE line buffering |
| **Tool calling** | ReAct prompting for ALL models (no native function-calling) | Single codepath; works with models that don't support native tool calls. Trade-off: 200-500 tokens of prompt overhead per call |
| **Agent class composition** | 5 mixins over god-class (R07.00) | Testable in isolation; clear separation of concerns. Trade-off: no type-checker can verify the mixin "host contract" |
| **`is_cloud` backend attribute** | Per-class attribute on `BaseBackend` | Replaces hardcoded `[OPENROUTER, ZAI, GEMINI]` lists in 8 sites |
| **Plugin manager singleton** | Lazy-loaded global `_plugin_manager` | Backends lazy-resolved on first `get_backend(name)` call |
| **Soul Spec optional** | Default `soul="nova-helper"`; falls back to `_build_default_prompt()` on any error | Lets framework boot without soul packages. Trade-off: silent failure means Soul misconfigs invisible without `--debug` |
| **Update check always-live** | Removed cache in R07.00 | "Cache kept hiding freshly-cut releases from the developer." INTENTIONAL per owner — ROB-05 WONTFIX R07.05 |
| **Zero dependencies** | `dependencies = []` in `pyproject.toml` | Fully reproducible install; `pip install -e .` in ~5s. Trade-off: hand-rolled SSE parsing, JSON streaming, AST walking, SSRF protection |
| **Backends split across two locations** | Native (ollama, llama_server, bitnet) in `backends/`; cloud (zai, openrouter, gemini, openai, huggingface, orcarouter) in `plugins/` | Cloud backends are optional (loaded if env vars present). ARCH-01 still open |
| **`CloudBackend` base class** (R07.05 NEW) | New base in `backends/cloud_base.py` consolidating ~5K LOC of duplicated boilerplate. Subclasses override data (`MODELS`) + 5 methods, get ~400 LOC of common code | Closes MAINT-02. Trade-off: hardcodes OpenAI Chat-Completions wire shape (NEW ARCH-06); `__init__` mutates env var (NEW SEC-15) |
| **`sha256` plugin pins opt-in** (R07.05 NEW) | Optional field on `plugin.json`; verification fail-closed when present, no-op when absent | Closes SEC-06. Trade-off: plugins without pins still load — no enforcement mode (NEW SEC-13) |
| **Token-tier memory pruning opt-in** (R07.06 NEW) | `MemoryConfig.max_tokens` default `0` (disabled). Real enforcement when set explicitly | Closes ROB-08. Trade-off: default-flip is silent behavior change for anyone relying on (claimed) 4096 enforcement |
| **`is_transient_api_error` body arg** (R07.06 NEW) | Optional `body: str | None` second arg | Closes ROB-10. Trade-off: no backend currently passes `body` — forward-looking API addition |
| **`/model` switch state derivation** (R07.06 NEW) | `apply_model_switch(agent, new_model)` re-derives num_ctx/num_predict/family config | Closes ROB-14. `/param num_ctx <v>` at runtime sets `_num_ctx_explicit=True` so the value survives the switch |

---

## What's Missing / Incomplete

1. **No integration tests** — All 1751 tests are mocked unit tests. TEST-01 still open. Coverage baseline: 42.7% line coverage (R07.01).
2. **No `black --check` or `ruff check` in CI** — TEST-06 still open.
3. **No `mypy` / type checking** — `pyproject.toml` has no `[tool.mypy]` section.
4. **No `CONTRIBUTING.md`** — `docs/CREDITS.md` lists contributors but no guide.
5. **No `SECURITY.md`** — No documented vulnerability disclosure policy.
6. **`schemas/v0.2/plugin.schema.json` declared but not validated** — `_parse_manifest` does ad-hoc dict-shape checks; `jsonschema` is never used.
7. **`agentkthx/examples/` (11 files)** are demo scripts, not doctests.
8. **`patches/fix_turbo_v_padding.py`** standalone script — not integrated into build, not tested.
9. **Tool output schema validation absent** — `tool.execute(**args)` returns `Any`; agentic loop treats it as `str(result)`. FEAT-03 still open.
10. **No conversation export/import** — FEAT-07 still open.
11. **No streaming `function_call_arguments.delta` events** — FEAT-06 still open.
12. **`docs/` has no API reference** — Only `ARCH.md`, `PLUGIN_SPEC.md`, `TESTS.md`, per-backend `*_API_TECHNICAL_REFERENCE.md`.
13. **MAINT-01 still open** — `cmd_chat` is still 1230 LOC single function with 25+ nested closures and no slash-command dispatcher.
14. **ARCH-05 still open** — `Agent.__init__` `**kwargs` swallowing concern NOT addressed by R07.05/06.
15. **ARCH-01 still open** — Backends split across `backends/` (native) and `plugins/` (cloud). New `CloudBackend` lives in `backends/` but OrcaRouter plugin that uses it lives in `plugins/`.
16. **`PersistentMemory.add()` does two lock acquisitions** (`_write_message` + `_touch_session`) — interleaving risk + 2× commit per message. **NEW finding ROB-15.**
17. **`_write_lock` is `threading.Lock`, not `RLock`** — brittle if future code adds nested locked calls. **NEW finding ROB-18.**
18. **Pollinations catalog is key-scoped** — keyed `GET /v1/models` silently drops every `paid_only` model (307 anon vs 134 keyed cards, verified 2026-09-28); `POLLINATIONS_ANON_CATALOG=1` fetches the public catalog while keeping generation keyed. Zero-cost models are encoded as currency-only pricing dicts (NOT zero-valued fields). **NEW findings ROB-31, FEAT-08, TEST-10 (R07.11 re-audit).**
19. **Cloud-backend retry skeleton copy-paste family** — third consecutive backend carries ~80 duplicated LOC (MAINT-11 closed → ROB-29 → **NEW MAINT-23**); provider error prose injection reopened twice (SEC-14 closed → SEC-18 → **NEW SEC-19**). Lifting the retry primitive + a shared `sanitize_provider_message()` to `CloudBackend` closes four findings at once.

---

## Quick Start for Developer

1. **Read the Critical Files Index above** — start with `agent.py`, `core/agentic_loop.py`, `core/helpers.py`. The "Why It Matters" column tells you when to touch each.
2. **Understand the Request Lifecycle** — every chat / run / agent command flows through `cmd_chat → Agent.run → _run_core → _run_loop_iteration → backend.generate → tool dispatch → sanitize_tool_output → memory`.
3. **Check Known Landmines** before changing:
   - Don't call `agent.add_tool` mid-session — use `agent.tools.register_tool` directly (R07.05 ROB-04 split)
   - `--api` defaults to `"openai"`, not `"openre"`
   - `AGENTKTHX_NO_UPDATE_CHECK=1` skips the 3-HTTPS-request startup cost (intentional per owner)
   - `ErrorRecoveryTracker` resets on any success — don't rely on it stopping infinite loops
   - **R07.05+**: tool args go through `sanitize_tool_output` before reaching memory — don't pattern-match on raw tool output
   - **R07.06+**: `MemoryConfig.max_tokens` default is `0` — set explicitly to enable token-tier pruning
4. **Follow Patterns & Conventions** — ReAct prompting for all models, 4-level tool-arg parse fallback chain (R07.05 SEC-02: `ast.literal_eval` gone, regex python-dict→JSON instead), defense-in-depth security.
5. **If changing a critical file**, check the Dependency Graph for blast radius:
   - Touching `core/helpers.py` affects 18+ modules
   - Touching `backends/cloud_base.py` affects 6 cloud plugins
   - Touching `plugins/_loader.py` affects every backend
6. **Run tests before committing**: `python -m pytest tests/ -q` (~25s, 1461 tests). For coverage: `pytest --cov=agentkthx --cov-report=term-missing`.
7. **Read prior audits**: `audit/audit.md` tracks findings by ID (SEC-XX, ROB-XX, MAINT-XX, etc.) with closure deltas. **R07.07 delta** (appended) extends the ID sequence with new findings surfaced by re-auditing the R07.06 codebase.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. The 20-module `core/` package is the engine — most changes start there.
