# Codebase Intelligence Brief: AgentKthx

> Generated: 2026-10-06 (R07.24 closure batch 1+2) | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `R07.24` (in-progress) — closure batch 1: SEC-20 `mcp install --no-overwrite`, ROB-41 `search_all_with_errors` + network-error hint, TEST-11 live-gated mcp contract test, ROB-28 MistralBackend catch-narrowing (four findings closed). Closure batch 2: MCP-01 thread+queue, MCP-03 arguments_json fallback, MCP-04 lazy warmup, MCP-05 list_changed handler (four more CLOSED); MCP-02 + SEC-13 WONTFIX (deferred — AgentKthx doesn't control MCP spec/plugin ecosystem).
> R07.24 closure batches. Batch 1: 4 findings closed (SEC-20, ROB-41, TEST-11, ROB-28). Batch 2: 4 more closed (MCP-01 thread+queue, MCP-03 arguments_json, MCP-04 lazy warmup, MCP-05 list_changed handler) + 2 WONTFIX (MCP-02 + SEC-13 — deferred, out-of-scope: AgentKthx doesn't control MCP spec or plugin ecosystem, can't enforce pinning on externally-published code). Suite: 2,932 passed / 20 skipped in ~21s; register: 125 findings — 24 OPEN (in `audit/audit.md`) + 101 archived (92 CLOSED / 9 WONTFIX in `audit/deltas.md`, ~81%). Dashboard: `python3 audit/generate_audit_dash.py --audit audit/audit.md --deltas audit/deltas.md --brief audit/brief.md --output dashboard.html`.

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, souls, and skills |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib `urllib`/`json`/`sqlite3`/`ast`/`subprocess`/`socket`/`ipaddress`/`threading`/`weakref` only); dev: pytest/black/ruff |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py:main()` → `cli/parser.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI 0.7.22 latest; this tree is 0.7.24-dev — closure batch in progress) or `pip install -e .`; `agentkthx chat`, `agentkthx turbo start <model>`, `agentkthx models`, `agentkthx souls`, `agentkthx mcp <init|list|probe|search|install|uninstall>`, `agentkthx version`, ... (96+ CLI flags across 17 subcommands) |
| **Test Command** | `python -m pytest tests/ -q` → **2,932 passed / 20 skipped in ~21s**; CI matrix Python 3.12/3.13 in `.github/workflows/ci.yml` + parallel coverage job + **required `lint` job** (`ruff check` + `black --check`, pinned versions). Live-gated contract tests (`tests/test_mcp_live_contract.py`) skip without `AGENTKTHX_LIVE_TESTS=1`. |

---

## Architecture Map

```
agentkthx/agent.py            → Agent class — 5-mixin composition (~1,112 LOC; _generate_with_retry now
                                also records per-response TPS: _gen_start/_gen_end/_gen_tokens_out)
agentkthx/agent_mode.py       → AgentMode + TaskPlan/Step/Action with rollback (822 LOC, untested — TEST-04)
agentkthx/orchestrator.py     → Multi-agent orchestrator (sequential + parallel + LLM-router, ~461 LOC)
agentkthx/core/               → 20 files incl. __init__, ~10,000 LOC
  ├─ agentic_loop.py          → Unified loop body; Ctrl+C → state.terminated (ROB-01)
  ├─ streaming.py             → SSE streaming + OpenResponses event generator (~1,083 LOC;
  │                             _prepare_stream_params + _generate_stream_chunks forward num_batch +
  │                             repeat_penalty/repeat_last_n; StreamAccumulator/StreamRenderer split held)
  ├─ environment.py           → NEW R07.19 (225 LOC): stdlib-only host probe (OS family/version,
  │                             distro, kernel, arch) → `# Host Environment` system-prompt section;
  │                             returns "" on ANY failure; AGENTKTHX_NO_ENV_PROBE=1 opts out; BitNet
  │                             sessions get a compact single line (lean-prompt crash threshold)
  ├─ compaction.py            → Context-window compaction mixin (~200 LOC)
  ├─ tool_parse.py            → ReAct / native-JSON / XML parser (661 LOC; markdown-bold-tolerant ReAct regexes)
  ├─ tool_execution.py        → Tool dispatch + parallel independent tool-call batches (FEAT-02)
  ├─ agent_setup.py           → Agent.__init__ (~684 LOC; R07.17/18: num_batch, repeat_penalty,
  │                             repeat_last_n constructor params + ARCH-05 valid-kwargs list updated)
  ├─ openresponses.py         → OpenResponses spec: Response state machine, items, SSE events (~1,050 LOC)
  ├─ api_resilience.py        → Transient-vs-permanent classifier + backoff w/ Retry-After + jitter
  ├─ error_recovery.py        → ErrorRecoveryTracker, is_error_result (~920 LOC)
  ├─ helpers.py               → Security primitives (validate_path, sanitize_command, is_safe_url w/ bounded
  │                             DNS, sanitize_tool_output) + normalize_args (~1,384 LOC)
  ├─ safe_eval.py             → AST-walking eval replacement (~263 LOC)
  ├─ memory.py                → Sliding-window + opt-in token-tier pruning; ROB-17 over-budget gap
  ├─ persistent_memory.py     → SQLite PersistentMemory(Memory); per-DB-path write locks; ROB-15/18 open
  ├─ model_family_config.py   → Per-family stop tokens / temperature / no-think directives (~480 LOC)
  ├─ types.py                 → BackendType enum (TURBOQUANT primary, LLAMA_SERVER deprecated alias) +
  │                             ToolSupportLevel (effective() normalizes NONE→REACT everywhere — R07.19
  │                             follow-up #10) + ThinkingSupport (YES/NO/UNKNOWN)
  ├─ tool_cache.py            → tool_support.json cache; R07.19: ONE plain-key namespace (no per-mode
  │                             keys) + `thinking:<model>` keys for the second axis
agentkthx/cli/                → 26-file CLI package
agentkthx/cli/commands/       → 16 command modules: chat (1,733 LOC — MAINT-01), souls (NEW R07.19,
                                145 LOC), test, config (R07.19: 96-var env reference), models (R07.19:
                                ONE `tools` column + `think` column, ROB-37 dynamic Name width),
                                agent, tools, soul, turbo, ...
agentkthx/cli/picker.py       → NEW R07.19 (397 LOC): ArrowMenu — termios cbreak POSIX / msvcrt Windows /
                                numbered fallback; pure-string rendering + state-mutation nav (terminal-free
                                tests); zero-third-party-import AST pin
agentkthx/cli/agent_factory.py→ THE wiring file (820 LOC): _build_agent (tool-support auto-detection now at
                                :280-347 — NONE normalizes to REACT like UNTESTED; wires num_batch/repeat_*
                                + _explicit pins + _detect_weight_quant), _get_catalog_defaults ladder,
                                apply_model_switch (:728)
agentkthx/cli/footer.py       → Shared footer formatters (227 LOC; R07.17/18: fmt_token_size 128K/1M,
                                🔧 batch + ⚡ TPS + 🧊 quant segments, _fmt_temp VS16/:g fixes)
agentkthx/cli/commands/chat.py→ R07.19: Primary User naming flow (_resolve_primary_user: --user >
                                AGENTKTHX_USER > TTY prompt > getpass; _sanitize_primary_user strips
                                control/ANSI, 32-char cap; named REPL prompt replaces `You:`), /souls +
                                /soul, /models opens the picker, startup picker when --model omitted;
                                /param matrix unchanged (num_batch + repeat_*)
agentkthx/backends/           → cloud_base.py + openai_compat + ollama + llama_server (R07.18 kwargs-
                                forwarding loop; BitNet repeat_penalty=1.3 is a default, not a hardcode)
                                + bitnet + ollama_registry + base; R07.19: ollama reads /api/tags
                                capabilities for tool+thinking verdicts (test_thinking_support);
                                openai_compat carries the cloud thinking name-heuristics — R07.20 #13
                                fixed them to match the MODEL SEGMENT only (vendor prefixes can't bleed)
agentkthx/shared_args.py      → 563 LOC; R07.17/18: --num-batch/--repeat-penalty/--repeat-last-n flags,
                                _parse_token_size (128k/1m/2g → int; used as argparse `type=`), _env_int
                                accepts suffixes, SharedConfig grew num_batch/repeat_* fields (ROB-35/36 live here)
agentkthx/plugins/            → 12 plugins: acp, bitnet, gemini, huggingface, mistral, openai, openrouter,
                                orcarouter, pollinations, turboquant, zai (+ test-plugin fixture)
  ├─ _loader.py               → PluginManager, manifest v0.2, Kahn-topo loader, sha256 pins (opt-in — SEC-13),
  │                             transactional registration (~1,530 LOC)
  ├─ turboquant/turbo.py      → llama-server lifecycle (923 LOC): TurboState (+num_predict, str flash_attn),
  │                             _build_command, _is_process_alive (ROB-33), start_server w/ GGUF-derived ctx
  ├─ mistral/ + pollinations/ → carry the copy-paste retry-loop family (ROB-29/MAINT-23, ~160 LOC dup)
  └─ zai/ + orcarouter/       → CloudBackend-based; _generate_with_auth (ROB-25 defaults mismatch);
                                zai's test_tool_support is the hardened probe (R07.19 #12: transient
                                429/5xx/network retried 3x backoff → UNTESTED uncached; only definitive
                                400 tools-rejection caches REACT; thinking disabled + max_tokens 512)
agentkthx/skills/             → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py
agentkthx/soul/               → Soul Spec v0.5 persona packages: loader.py (_build_tool_section — MAINT-24;
                                R07.19: list_souls() discovery + _PARAM_STRING_EXAMPLES real example args
                                in the Tool Reference table), types.py
agentkthx/souls/              → 3 bundled souls: kthx-helper, kthx-skills, kthx-trading
agentkthx/tools/              → builtins.py (shell timeout clamp; _SSRFSafeRedirectHandler 5-hop budget),
                                registry.py, sandboxed_repl.py (521 LOC)
agentkthx/update_check.py     → Live PyPI + GitHub check on EVERY CLI invocation (intentional, ROB-05 WONTFIX;
                                opt out AGENTKTHX_NO_UPDATE_CHECK=1)
agentkthx/config.py           → Env-var-derived singletons; TURBOQUANT_BASE_URL primary (LLAMA_SERVER_BASE_URL fallback)
agentkthx/mcp/                → NEW R07.22: MCP client package over stdio JSON-RPC 2.0 — config.py + transport.py +
                                client.py + manager.py + (R07.23) registry.py + cache.py; zero new runtime deps
                                (stdlib subprocess + json + urllib.request only). MCP-01..05 filed in R07.22.
                                R07.23: live `mcp search` + `mcp install` replaced the deleted offline catalog.
audit/                        → brief + audit.md (24 OPEN) + deltas.md (101 archived) + split/verify/dash tooling
docs/                         → ARCH.md, USAGE.md, PLUGIN_SPEC.md(+v0.2), CHANGELOG.md (R07.23 + R07.22 sections),
                                SECURITY.md + CONTRIBUTING.md (R07.22 — trust artifacts), mcp/ROADMAP.md (Phase 1-5 plan)
                                TESTS.md, docs/api/*_API_TECHNICAL_REFERENCE.md (8 backends)
tests/                        → 94 files, ~42,100 LOC, 2,932 tests — all mocked unit tests, no integration tier (TEST-01);
                                R07.19 regression files: test_r07_19_primary_user_env.py (42), test_r07_19_models_table_width.py
                                (8), test_r07_19_tool_examples.py (17), test_r07_19_soul_rename.py (11),
                                test_r07_19_soul_commands.py (68), test_r07_19_help_sort.py (59),
                                test_r07_19_model_picker.py (96), test_r07_19_caps_tool_support.py (18),
                                test_r07_19_single_tool_col.py (53), test_r07_19_zai_probe_transient.py (13)
scripts/                      → probe_*.sh family, probe_ollama_tools.py (NEW R07.19: capabilities-based
                                tool-support probe), probe_llama_server_tools.py, diagnose_ollama.sh (839-line
                                health checker), bump-version.sh
patches/                      → llama.cpp turboquant patches + standalone .py applier
schemas/v0.2/                 → plugin.schema.json (declared but NOT validated by code — ad-hoc dict-shape checks)
```

### Skip List

- `__pycache__/`, `.git/`, `*.egg-info/`, `build/`, `dist/`
- `agentnova-redirect/` and `localclaw-redirect/` — thin shims for backward-compat package names
- `agentkthx/plugins/test-plugin/` — fixture for plugin spec tests
- `agentkthx/examples/` — 11 demo scripts (not run by pytest; do NOT import shared_args — they define their own args)
- `AgentKthx.ipynb` — root-level notebook, not referenced in docs
- `agentkthx/core/prompts.py` — dead-code-but-kept `_build_tool_section` duplicate

---

## Critical Files Index

The 10 most important files. Touch these for almost any meaningful change.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/cli/agent_factory.py` (815 LOC, +80 in R07.17/18) | Wires CLI args → `Agent`. `_build_agent` (tool-support auto-detection), `_detect_weight_quant` (NEW R07.18: /api/show → list_models fallback → None; populates `agent._weight_quant` for the 🧊 footer segment), `_get_catalog_defaults` ladder → local/remote branches, `apply_model_switch`, `_register_model_switch_callback`. | The hottest file post-R07.16. Auto-detection (now lines 278-344): for non-cloud backends with tools, reads the cached `test_tool_support` verdict — NATIVE keeps native, REACT/UNTESTED force `force_react=True`, NONE keeps native; **no CLI opt-out (MAINT-25)** — the debug hint `--force-react=False` errors because the flag is `store_true` (parser.py:218). R07.17/18 wiring: `num_batch`/`repeat_penalty`/`repeat_last_n` passed to the Agent + `_explicit` pin flags stashed (lines ~355-400); `_weight_quant = _detect_weight_quant(backend, model)` runs 1-2 synchronous HTTP calls at startup — on a dead remote backend this stacks extra timeout windows on top of the existing `_probe_remote_catalog` stall (silent `except Exception: pass` ×2 — same family as ROB-28/30). The catalog ladder (lines ~556-640): local base_url → TurboState.load() [PID-verified — **ROB-33: that check kills the server on Windows**, now at line 566] → Ollama GGUF `context_length` → `{}`; remote base_url → `list_models()` probe (`num_predict = ctx // 32`). `apply_model_switch` re-derives per-model state on `/model` AND on insufficient-credits callbacks — it intentionally does NOT re-derive num_batch/repeat_* (per-request options survive switches; comments in-code). |
| `agentkthx/shared_args.py` (563 LOC, +163 R07.17/18) | Shared flag definitions for chat/run/agent (`add_agent_args`), example scripts (`add_shared_args`), `SharedConfig` dataclass, `parse_shared_args`, `_env_int`/`_env_float`, and `_parse_token_size`. | R07.18 surface: `--num-ctx`/`--num-predict` take `type=_parse_token_size` — accepts `131072`, `128k`/`1m`/`2g`, fractional `2.5k`, sentinels `-1`/`0`; `--num-batch`, `--repeat-penalty`, `--repeat-last-n` flags on all three surfaces; env fallbacks (`AGENTKTHX_NUM_BATCH`, `AGENTKTHX_REPEAT_PENALTY`, `AGENTKTHX_REPEAT_LAST_N`) also accept suffix forms. **Two NEW Low findings live here**: ROB-35 — `parse_shared_args` `or`-coalescing (lines 448-462) drops the documented `0` sentinel (`--repeat-last-n 0` reaches SharedConfig as None/env-value; main CLI unaffected — it bypasses SharedConfig); ROB-36 — `_parse_token_size` accepts `inf`/`1e400` numeric parts → `OverflowError` escapes argparse's clean-error path AND `_env_int`'s `except (ValueError, TypeError)` → raw traceback. When adding flags here, wire them in BOTH `add_agent_args` AND `add_shared_args` + `parse_shared_args` + the `test` subcommand parser, and mirror the `_explicit` pin pattern. |
| `agentkthx/backends/llama_server.py` (~500 LOC, +68 R07.18) | `LlamaServerBackend(OllamaBackend)` — user-facing name "turboquant"; `/completion` (OpenRE) + OpenAI-compat + streaming paths; BitNet mode flag. | R07.18 fixed the kwargs parity bug: `_generate_completion` + `_stream_completion` previously built a hardcoded 4-field body (`prompt`, `n_predict`, `temperature`, `stop`) and silently dropped every other sampling param. Both now run a generic forwarding loop: skip `_already_set` (the 4 + `stream`) and `_agent_internal` (`think`, `reasoning_effort`, `tool_choice`, `response_format`, `truncation`, `num_ctx`, `num_predict`, `num_batch`); everything else goes verbatim into the /completion body (top_p, top_k, seed, repeat_penalty, repeat_last_n, typical_p, tfs_z, mirostat*, min_p, grammar...). BitNet's `repeat_penalty=1.3` is a DEFAULT now — an explicit kwarg overrides it (repeat_penalty is deliberately NOT skipped). If you add a new agent-internal kwarg, you MUST add it to `_agent_internal` in BOTH methods or it leaks into the request body. The two loops are copy-paste (~17 LOC ×2) — keep them in sync. |
| `agentkthx/core/agent_setup.py` (~684 LOC, +59 R07.17/18) | `AgentSetupMixin.__init__` — soul loading, memory wiring, system-prompt assembly, per-request param storage. | R07.17/18: new constructor params `num_batch`, `repeat_penalty`, `repeat_last_n` (stored as `self._num_batch` / `self._repeat_penalty` / `self._repeat_last_n`, default None = backend default; BitNet: 1.3, Ollama num_batch: 512). Also initializes the TPS trio `_gen_start_time`/`_gen_end_time`/`_gen_tokens_out` (0.0/0.0/0) so the footer can getattr safely before the first generate. `_use_native_tools` property (R07.16) still gates ALL `_is_comp_mode` tool-prompt decisions. The ARCH-05 kwargs fail-fast message lists every valid kwarg — update it whenever the constructor grows. |
| `agentkthx/cli/commands/chat.py` (1,368 LOC, grew +61) | `cmd_chat` — the interactive REPL (MAINT-01: single function, 25+ nested closures, no slash-command dispatcher). | R07.17/18 additions are all inside `/param`: `num_batch` (backends: ollama only), `repeat_penalty`/`repeat_last_n` (ollama/llama_server/bitnet) entries in PARAM_MATRIX + `agent_attr` pin-flag plumbing (`_num_batch_explicit`, `_repeat_*_explicit` set on set, cleared on `/param reset`) + `changes` dict handling. `/param` values land on agent attrs (is-not-None forwarded) or `_runtime_kwargs` — they do NOT go through SharedConfig, so ROB-35 does not affect `/param`. Slash commands remain an inline if/elif chain. The no-readline fallback prompt `"\033You:\033 "` (line 291) is STILL broken on Windows (ROB-34 — renders as `ou:`; use plain `"You: "`). |
| `agentkthx/cli/footer.py` (227 LOC, +145 R07.17/18) | Shared footer text builders: `footer_line1`, `footer_line2`, `fmt_tok`, `fmt_token_size` (NEW), `_fmt_temp` (NEW). | Line 1: version, model, prompt-size, ctx + max-tokens (now via `fmt_token_size`: `128K`/`1M`/`?`; falsy ctx renders `?`), temp (`:g` formatting, thermometer VS16 removed — renders single-space), 🔧 batch segment (when `_num_batch` set), 🧊 quant segment (when `_weight_quant` set). Line 2: backend, tok counters, ⚡ per-response TPS = `_gen_tokens_out / (_gen_end_time - _gen_start_time)` (set in `_generate_with_retry` — the single chokepoint for BOTH streaming and non-streaming paths, verified; omitted before first completion + guarded against div-by-zero), ctx %, debug. Pure formatters — no I/O; all agent attr reads use `getattr` defaults. |
| `agentkthx/agent.py` (~1,112 LOC, +37 R07.17/18) | `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. | R07.17/18: `_generate_with_retry` records `_gen_start_time`/`_gen_end_time`/`_gen_tokens_out` around EVERY generate_fn() call (per-response TPS; retries re-mark the start so timing reflects the successful attempt; falls back to `len(content)//4` when usage is missing — note: that estimate assumes ~4 chars/token, CJK over-counts speed). `_generate` forwards `num_batch`/`repeat_penalty`/`repeat_last_n` into backend_kwargs when not None (non-streaming path; streaming forwards in `_prepare_stream_params` + `_generate_stream_chunks`). `add_tool` remains a deprecated alias that clears memory and emits NO DeprecationWarning (MAINT-16 open) — use `register_tool` mid-session. |
| `agentkthx/core/helpers.py` (~1,384 LOC) | Security primitives + `normalize_args` + calc extraction. `validate_path`, `sanitize_command`, `is_safe_url` (bounded DNS), `sanitize_tool_output`. | Imported by 18+ modules — blast radius for any security change is huge. Open items: ROB-09 (`abspath` not `realpath` — symlink traversal), MAINT-03 (`normalize_args` strategy 5 prefix/substring matching; `CONTEXTUAL_ALIASES` mitigates known-ambiguous cases only). `sanitize_tool_output` wraps EVERY tool result (8KB truncation + secret redaction + ANSI strip). Unchanged by R07.17/18. |
| `agentkthx/core/agentic_loop.py` (~800 LOC) | `_run_loop_iteration` — unified loop body: Response state machine, tool dispatch, error recovery, finish_reason handling. | Both streaming and non-streaming paths funnel through `_generate_with_retry` (line 264) — new per-response instrumentation only needs to hook there. `_process_tool_result` wraps every tool result via `sanitize_tool_output` BEFORE memory / FunctionCallOutputItem. FEAT-02 parallel batches commit in ORIGINAL call order; shell/write_file/edit_file/todo stay sequential; `AGENTKTHX_PARALLEL_TOOLS=0` escape hatch. Unchanged by R07.17/18 except the TPS chokepoint comment. |
| `agentkthx/plugins/_loader.py` (~1,530 LOC) | `PluginManager` singleton, manifest v0.2 parser, Kahn topo-sort dependency loader, hook dispatch, external plugin import. | Transactional registration (`_PluginTransaction` LIFO undo, ROB-11 closed) + sha256 pins fail-closed WHEN present but **opt-in — no `AGENTKTHX_REQUIRE_PLUGIN_PINS` enforcement mode (SEC-13 open)**; a manifest without a pin loads silently. Unchanged by R07.17/18. |

### Additional files of note

| File | Why It Matters |
|------|----------------|
| `agentkthx/cli/commands/models.py` (+77 R07.18) | New Quant column (between Size and Context) fed by `details.quantization_level`; NAME_W widened 36→48; SIZE_W 8→9 (the 1-char "xxx.xx GB" overflow used to cascade into every later column); Context column now renders via `fmt_token_size` (128K style); long names deliberately NOT truncated (user copies them into `-m`); cloud providers skip Size/Quant/Family columns. |
| `agentkthx/core/tool_parse.py` (661 LOC) | `ToolParser.parse(text)` — native JSON → ReAct → XML; every ReAct keyword regex tolerates markdown bold (`**Action:**`). Historical note: the MAINT-14 string-literal-safe True/False/None handling is closed. Unchanged by R07.17/18. |
| `agentkthx/backends/__init__.py` + `config.py` + `core/types.py` | Backend registry maps `"turboquant"` (primary) + `"llama-server"`/`"llama_server"` (aliases) → `LlamaServerBackend`; `TURBOQUANT_BASE_URL` env primary with `LLAMA_SERVER_BASE_URL` fallback. BitNet routes through the same class with `_bitnet_mode=True`. |
| `agentkthx/backends/ollama_registry.py` | `OllamaModel.exists` returns False when `blob_path` is empty (remote `turbo list` shows `✗ blob missing`); `discover_models` parses GGUF headers — the `context_length` feeding both `turbo start` auto-ctx and the chat ladder. `details.quantization_level` from `/api/tags` feeds the models Quant column. |
| `agentkthx/tools/builtins.py` (~1,363 LOC) | `shell()` clamps model-supplied timeout (`max(1, min(timeout, 300))`, handles `0`/`"10"`/`-5`/`None`); `_SSRFSafeRedirectHandler` (5-hop budget) for `http_get`; `BUILTIN_REGISTRY` todo store is a module-level singleton shared across Agent instances in-process. |
| `agentkthx/core/persistent_memory.py` | Per-DB-path write locks via realpath-keyed `WeakValueDictionary` (MAINT-15). Open: ROB-15 (`add()` = two lock acquisitions, 2× commit), ROB-18 (`threading.Lock`, not RLock). `0o600`/`0o700` perms. No conversation export/import (FEAT-07). |
| `agentkthx/plugins/pollinations/pollinations.py` | The only keyless backend. Open cluster: ROB-31 (entitlement-blind fallback ranking), ROB-30 (`_fetch_model_cards` bare `except Exception`), MAINT-23 (retry-loop dup ×2), FEAT-08 (free-TIER boundary unreachable), TEST-10 (no live-shape contract test). Zero-cost models are currency-only pricing dicts. |
| `scripts/diagnose_ollama.sh` (NEW R07.18, 839 LOC) | Standalone Ollama model health checker: single model or `--all`, JSON output, `--max-size` OOM guard + `--force` bypass; detects fp16 silent expansion (file_size << peak_RSS), context overrides, GGUF metadata lying about quant, load failures/OOM kills, tok/s + load time. Exit codes 0/1/2/3 = healthy/warning/broken/usage. `set -u`, no `set -e` (intentional — continues past per-model failures). |
| `scripts/probe_llama_server_tools.py` (R07.16) | Stdlib-only diagnostic hitting a running llama-server with 7 request shapes; bypasses AgentKthx's chat path so errors shown are the server's. Auto-reads `~/.agentkthx/turbo.state`. |
| `agentkthx/mcp/registry.py` (NEW R07.23, ~290 LOC) | Stdlib-only live search across npm + GitHub: `npm_search`, `npm_package_info`, `github_search`, `search_all`, `derive_short_name`, `build_config_snippet`, `MCPRegistryError`. All network calls use `urllib.request` with a 10s timeout; `search_all` swallows per-source failures (graceful degradation — **ROB-41**: all-source-down returns `[]` indistinguishable from 0-result success). Anonymous GitHub works at 10 req/min; `AGENTKTHX_GITHUB_TOKEN`/`GITHUB_TOKEN`/`GH_TOKEN` env vars raise to 5000/min. | New file. The `search_all` swallow-failures contract is a deliberate UX choice (one source down should not break the search) but the all-source-down case needs a network-error hint in plain mode (the `--json` payload already has an `errors` field; the plain-mode path doesn't consume it). |
| `agentkthx/mcp/cache.py` (NEW R07.23, ~150 LOC) | JSON-backed TTL cache at `~/.agentkthx/mcp_cache.json` (mode 0o600). `get_cached(key)`/`set_cached(key, value, ttl=600)`/`clear_cache()`. Atomic writes via tmp+rename. Configurable via `AGENTKTHX_MCP_CACHE_TTL` env var (seconds; `0` disables). Cache key format: `"search:<source>:<query>:<limit>"`. | New file. Lazy eviction (expired entries skipped on read, overwritten on next write). File access is NOT locked — two concurrent `mcp search` calls could race on write (worst case: lost cache update, NOT corruption — atomic rename prevents file-level damage). |

---

## Request / Execution Lifecycle

```
1. `agentkthx chat` ──────────────────────────────────────────────────────────
   └─ cli/__main__.py → cli/main.py:main()
       ├─ get_plugin_manager().load_all()  → plugins/_loader.py (Kahn topo sort;
       │     sha256 pins verified BEFORE exec_module when present; ROB-11
       │     transactional registration rolls back partial loads)
       ├─ _run_update_check() → 3 sequential HTTPS requests (pypi + GitHub commits
       │     + raw __init__.py) unless AGENTKTHX_NO_UPDATE_CHECK=1 (ROB-05 WONTFIX)
       └─ dispatch → cli/commands/chat.py:cmd_chat

2. cmd_chat → _build_agent (cli/agent_factory.py)  ─────────────────────────
   ├─ args: --num-ctx/--num-predict parsed via _parse_token_size (128k → 131072);
   │     --num-batch/--repeat-penalty/--repeat-last-n float/int flags (R07.17/18)
   ├─ backend = get_backend(name)  ("turboquant" primary; aliases kept)
   ├─ TOOL-SUPPORT AUTO-DETECTION (agent_factory.py:280-347, non-cloud + tools):
   │     support = backend.test_tool_support(model, force_test=False)   # cached
   │     NATIVE → keep native · REACT → force_react=True · UNTESTED → force_react=True
   │     NONE → normalized to REACT (R07.19 follow-up #10 — no model surfaces none)
   │     · user --force-react wins (no opt-out — MAINT-25)
   ├─ CATALOG LADDER for num_ctx/num_predict (_get_catalog_defaults):
   │     local  → 1) TurboState.load() [PID-verified — ROB-33 Windows kill, line 571]
   │               2) Ollama GGUF context_length (find_model)
   │               3) {} → config.num_ctx
   │     remote → _probe_remote_catalog: list_models() exact-name-else-first;
   │               num_predict = ctx // 32
   │     (+ _detect_weight_quant: /api/show → /api/tags → None, for the 🧊 segment)
   └─ Agent(model, tools, backend, force_react=effective, num_batch=...,
         repeat_penalty=..., repeat_last_n=..., ...) → AgentSetupMixin.__init__
         ├─ system prompt: _use_native_tools ? native-tools text : ReAct text
         │   (soul path: soul/loader.py + _build_tool_section table; MAINT-24 caveat)
         ├─ memory: Memory or PersistentMemory (sqlite, 0o600)
         └─ TPS trio + _explicit pin flags stashed on the agent

3. REPL loop (chat.py) ──────────────────────────────────────────────────────
   ├─ prompt: Primary User named prompt (R07.19): `<name>:` with readline zero-width markers;
   │     resolution --user > AGENTKTHX_USER > TTY naming prompt > getpass > "You" (ROB-34 CLOSED —
   │     the old bare-ESC fallback prompt is gone, source pins assert it can't return)
   ├─ slash commands: inline if/elif chain (no dispatcher — MAINT-01)
   │     /param num_batch|repeat_penalty|repeat_last_n <v> → agent attr + _explicit pin
   │     /param reset ...      → clears value + pin flag
   │     /model <name>         → apply_model_switch → re-derives ctx/predict/family
   │                             (num_batch/repeat_* intentionally survive — per-request)
   └─ agent.run(user_input, stream=True)

4. agent.run() → _run_core → _run_loop_iteration (agentic_loop.py) ─────────
   for step in range(max_steps):
     ├─ _generate_with_retry (agent.py) — ALSO records TPS trio per attempt:
     │    generate_fn() → backend.generate / _generate_stream
     │    transient? → backoff (Retry-After + jitter) · 400 ctx-length? → compact
     ├─ backend kwargs (all paths): num_ctx, num_predict, num_batch,
     │    repeat_penalty, repeat_last_n forwarded when not None;
     │    ollama → body["options.*"] via generic loop · llama-server → /completion
     │    top level via R07.18 kwargs loop (minus _agent_internal) · cloud → dropped
     ├─ parse: native tool_calls OR ReAct 4-level fallback chain
     │    R07.16: ReAct keywords tolerate markdown bold (**Action:**)
     ├─ tools: FEAT-02 — independent batches run on 4-worker pool, results commit
     │    in call order; sequential for shell/write_file/edit_file/todo
     └─ every result → sanitize_tool_output (8KB + secrets + ANSI) → memory

5. backend.generate (ollama / llama_server(turboquant) / openai_compat / cloud_base)
   ├─ cap max_tokens to num_ctx // 32 (R06.55); family stop tokens; no-think directives
   └─ streaming: SSE chunks → StreamAccumulator/StreamRenderer; footer shows ⚡ TPS
        from the _gen_* trio; KeyboardInterrupt closes the generator (ROB-06 open)
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
       config.py ◄─── referenced by EVERYTHING
       shared_args.py ◄── parser.py (chat/run/agent/test) + __init__.py export (NO in-tree SharedConfig consumers)
```

Key coupling points:
- `core/helpers.py` is imported by 18+ modules — blast radius for any security change is huge
- `backends/cloud_base.py` is the shared base for 8 cloud plugins — bug here × 8 backends
- `cli/agent_factory.py` is touched by BOTH chat startup paths (catalog ladder + tool detection + quant detection) and the model-switch callback — changes here affect every backend launch
- `backends/llama_server.py` `_agent_internal` tuple is duplicated across `_generate_completion` + `_stream_completion` — a new agent-internal kwarg MUST be added to BOTH or it leaks into /completion bodies
- `shared_args.py` flags must be wired in 3 surfaces (`add_agent_args`, `add_shared_args`, `test` parser) + `parse_shared_args` + `SharedConfig`
- `plugins/_loader.py` PluginManager singleton loads at startup; failure cascades to all backends
- `agentkthx/__init__.py` has try/except optional imports (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Class composition** | Mixin pattern: `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. Mixins access host via `self.X` with docstring-declared "host contract" — no type-checker verification |
| **Tool calling** | HYBRID: native function-calling when `_use_native_tools` (comp mode AND not force_react — default for cloud backends); ReAct text prompting otherwise. The `tools` array always ships in the request body. R07.19: ONE capabilities-first check feeds the models-table `tools` column (`/api/tags` for Ollama, name-heuristics for cloud — model-segment-only matching since #13), and `ToolSupportLevel.effective()` normalizes every would-be NONE to REACT at every surface |
| **Tool-call parsing** | Native JSON path first; ReAct path = 4-level fallback chain (json.loads → sanitized json.loads → regex python-dict→JSON → expression extraction → `{"input": raw}`), all ReAct keyword regexes markdown-bold-tolerant |
| **Per-request sampling params** | `num_batch` / `repeat_penalty` / `repeat_last_n` flow: CLI flag → `_build_agent` → Agent attr (`is not None` gate) → `_generate` + `_prepare_stream_params`/`_generate_stream_chunks` → backend. Ollama: `options.*`; llama-server/TurboQuant/BitNet: top-level /completion (R07.18 kwargs loop); cloud: silently dropped (OpenAI allowlist). `/param` writes agent attrs directly — bypasses SharedConfig |
| **Token sizes** | CLI accepts `128k`/`1m`/`2g` forms via `_parse_token_size` (argparse `type=`); display via `fmt_token_size` (`128K`/`1M`/`?`) — distinct from `fmt_tok` (fractional-k counters). Agent/backends only ever see plain ints |
| **Explicit-pin pattern** | Every user-set param gets `agent._<param>_explicit = True` (set at `_build_agent`, set by `/param`, cleared by `/param reset`); `apply_model_switch` consults pins before re-deriving. num_batch/repeat_* are never re-derived (per-request, not model-derived) |
| **Tool arg normalization** | 5-strategy matcher in `helpers.py:normalize_args`: alias → direct → case-insensitive → generic alias → prefix/substring (last is dangerously permissive — MAINT-03 open) |
| **Tool output sanitization** | `sanitize_tool_output()` wraps every tool result in `<tool_output>` tags with 8KB truncation, secret redaction, ANSI stripping |
| **Parallel tools** | Independent tool-call batches run concurrently (4 workers), results commit in call order; mutating/sequential tools excluded; `AGENTKTHX_PARALLEL_TOOLS=0` escape hatch |
| **Error classification** | `is_error_result` regex on first non-empty line; `is_transient_api_error(e, body=None)` — permanent markers first, auth/404 permanent, 429/5xx transient |
| **Context defaults** | `num_predict = num_ctx // 32` everywhere; GGUF `context_length` beats hardcoded 8K |
| **Security** | Defense-in-depth: `validate_path` (allowed-prefix; abspath not realpath — ROB-09), `sanitize_command` (denylist + shell/heredoc block), `is_safe_url` (ipaddress-based, bounded DNS 5s/32-records fail-closed, 5-hop redirect budget), `safe_eval` (AST walker), `sanitize_tool_output`, plugin sha256 pins (opt-in — SEC-13) |
| **Local backend UX** | `turbo start` auto-derives ctx/num-predict from GGUF metadata; tri-state `--flash-attn on|off|auto`; CPU knobs `-tb/-b/-ub/--mlock/--numa`; `--` passthrough |
| **Memory** | Sliding window on message count; token tier opt-in (`MemoryConfig.max_tokens`, default `0`); compaction at 85% of num_ctx |
| **File naming** | `snake_case.py` modules, `PascalCase` classes, `SCREAMING_SNAKE` constants |
| **Tests** | Co-located in `tests/`, `test_*.py`, pytest fixtures; 2608 tests, all mocked unit tests — no integration tier (TEST-01). Per-release regression files (`test_r07_19_single_tool_col.py`, `test_r07_19_zai_probe_transient.py`, `test_r07_19_model_picker.py` are the R07.19 exemplars of the house convention) |

---

## Known Landmines

1. **`_is_process_alive` KILLS the target on Windows** (ROB-33, `plugins/turboquant/turbo.py:159-183`) — `os.kill(pid, 0)` is TerminateProcess on Windows (any sig ≠ CTRL_* kills). R07.16 put this on the chat startup path via `TurboState.load()` in `_get_local_catalog_defaults` (agent_factory.py:571) — `turbo start` + `chat` on Windows kills its own server; a stale state file with a reused PID can kill an UNRELATED process. POSIX is safe (`/proc` zombie check present).
2. **Pre-#13 thinking caches keep stale vendor-bleed verdicts** — machines that ran an R07.19-era `models` scan cached `YES` for `thinkingmachines/*` under `thinking:<model>` keys (the #13 fix stopped NEW false YES writes but does not invalidate old ones); they render `think ✓ yes` until the cache entry expires or `~/.agentkthx/tool_support.json` is deleted.
3. **`--force-react=False` is not a valid CLI invocation** (MAINT-25) — `--force-react` is `store_true` (parser.py:312); passing `=False` is an argparse ERROR. The UNTESTED debug hint suggests exactly that. No opt-out exists for the local-backend default-to-ReAct behavior short of deleting `~/.agentkthx/tool_support.json`.
4. **Custom souls can lose ReAct format instructions** (MAINT-24) — R07.16 removed the ReAct block from `_build_tool_section` (dedup). Souls that don't ship their own `Action:`/`Action Input:` block (and lack example placeholders) now produce prompts with ZERO format instructions when ReAct is active — the parser then sees no valid tool calls. The docstring still claims otherwise.
5. **`parse_shared_args` drops the documented `0` sentinel** (ROB-35, NEW R07.18, `shared_args.py:448-462`) — `0 or _env_int(...)` coalescing means `--repeat-last-n 0` ("0 = full context" per its own help) reaches `SharedConfig` as `None` (or the env-var value). Main chat/run/agent/test CLI unaffected (passes args straight to `_build_agent`); the bug bites example scripts + programmatic `SharedConfig` users. Fix pattern: `is not None` checks.
6. **`--num-ctx infk` crashes with a raw OverflowError** (ROB-36, NEW R07.18, `shared_args.py:480-543`) — `float("inf")` succeeds, `int(inf*1024)` raises `OverflowError`, which argparse does NOT convert to a usage error (it only catches ValueError/TypeError) and `_env_int`'s `except (ValueError, TypeError)` also misses. Affects any `inf*`/`1e400k`-shaped input on flag or env path.
7. **Remote-catalog probe can stall chat startup** — `_probe_remote_catalog` calls `backend.list_models()` synchronously with the backend's own timeouts (llama-server `/v1/models` timeout=10s). A dead Cloudflare tunnel adds up to ~10s before falling back to `config.num_ctx`. R07.18's `_detect_weight_quant` adds up to TWO more sequential HTTP calls at startup (`get_model_info` then `list_models`) with silent `except Exception: pass` — the stall stacks.
8. **`_is_local_base_url` excludes 172.16/12** — RFC1918 `172.16-31.x.x` backends take the REMOTE probe path (documented in-code); local TurboState/Ollama-catalog lookups are skipped for them. Empty/unknown URLs are treated as local (conservative).
9. **`Agent.add_tool` is deprecated but emits NO `DeprecationWarning`** (MAINT-16) — use `register_tool` mid-session (no memory clear). `add_tool` still clears conversation for backward compat, with no programmatic migration signal.
10. **`--api` default is `"openai"`, not `"openre"`** — `shared_args.py` sets openai (Chat-Completions); surprising given the OpenResponses branding. Note `repeat_penalty`/`repeat_last_n`/`num_batch` only work on Ollama via `--api openre` (the OpenAI-compat path doesn't accept them).
11. **`update_check.py` makes 3 sequential HTTPS requests on every CLI invocation** — INTENTIONAL per owner (ROB-05 WONTFIX). Opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`.
12. **`Memory.sanitize_history` mutates `_messages` in place** on every `get_messages()` (PERF-01 open) — subtle bugs possible in nested iteration.
13. **Streaming-path JSON parse errors fall back to `{"_raw_arguments": ...}`** without the debug chain the ReAct path has — malformed streaming tool-call args are hard to diagnose without a reproducer.
14. **`ErrorRecoveryTracker.consecutive_all` resets on ANY success** — alternating fail/succeed tool calls loop until `max_steps`.
15. **`MemoryConfig.max_tokens` defaults to `0`** (tier disabled) — token-tier pruning is opt-in; long agentic runs rely on compaction at 85% num_ctx. Single messages larger than the whole budget still defeat the tier (ROB-17).
16. **`BUILTIN_REGISTRY` todo store is a module-level singleton** — two `Agent` instances in one process share todos unless `set_todo_session()` is called.
17. **`__init__.py` optional imports are silent `None`** — PersistentMemory/ACPPlugin/Soul import failures surface only as missing features, never as warnings.
18. **`normalize_args` strategy 5 matches substrings** (`{"e": ...}` → `expression`) — last-match-wins on dict order (MAINT-03). Prefer exact keys in tool args.
19. **llama-server kwargs forwarding has an exclusion-list trap** (R07.18) — `_agent_internal` in `llama_server.py` lists the kwargs that must NOT reach /completion; it's duplicated in two methods. Add a new agent-internal kwarg to only one → the other leaks it into request bodies (harmless-ish, but pollutes and can 400 on strict servers).
20. **BitNet `repeat_penalty=1.3` is a default, not a hardcode** (R07.18) — an explicit kwarg/`/param repeat_penalty` overrides it. Don't "restore" the hardcode; the kwargs loop deliberately does NOT skip repeat_penalty.
21. **OpenRouter shows `tools ✓ native` for non-chat slugs** (ROB-39, `openrouter.py:817-850`) — `lyria`/`gpt-audio`/`llama-guard` display native with zero probing; runtime is safe (400→ReAct fallback) but the table overstates. Fix: static non-chat name-pattern set → UNKNOWN.
22. **The chat empty-answer boilerplate misdiagnoses fatal errors** (ROB-38, `chat.py:1593-1647`) — after a definitive quota/auth failure the REPL still prints "likely a rate limit (429)… try again in a few seconds"; the real error printed above is the truth — ignore the boilerplate until ROB-38 lands.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | `dependencies = []` | Reproducible install, no supply-chain surface; trade-off: hand-rolled SSE, SSRF, AST eval |
| **Backend rename** (R07.16) | `turboquant` primary; `llama-server`/`llama_server` aliases; `TURBOQUANT_BASE_URL` env primary | Match the TurboQuant fork branding; binary stays `llama-server` (llama.cpp upstream name) |
| **Local tool calling** (R07.16) | Auto-detect via cached `test_tool_support`; UNTESTED defaults to ReAct for local backends | Small CPU models typically aren't trained for native function calling; user `--force-react` wins |
| **Context auto-derivation** (R07.16) | GGUF `context_length` → ctx; `num_predict = ctx // 32` | Kills the hardcoded 8K mismatch; matches the cloud max_tokens cap empirically found in R06.55 |
| **llama-server kwargs forwarding** (R07.18) | Generic loop forwards every non-`_already_set`, non-`_agent_internal` kwarg verbatim to /completion | Fixes the parity bug where only temperature/n_predict/stop reached the server; Ollama already had the generic loop — this makes llama-server behave the same |
| **BitNet repeat_penalty** (R07.18) | 1.3 is a default applied when no kwarg is supplied, overridable | 0.5b models loop without it (1.2 insufficient); users tuning via `/param repeat_penalty` were being silently ignored before |
| **num_batch / repeat_* are per-request** (R07.17/18) | Forwarded every call when not None; NEVER re-derived by `apply_model_switch`; `_explicit` pins kept for `/param reset` parity | They're sampling knobs, not functions of the model name; user-pinned values survive `/model` switches |
| **num_batch primary support: Ollama only** (R07.17) | `options.num_batch` on native `/api/chat`; llama-server needs `turbo start --batch-size`; cloud silently drops | llama.cpp batch size is a server-start flag, not per-request; Ollama accepts per-request options |
| **Human-friendly token sizes** (R07.18) | `_parse_token_size` at the CLI/env boundary only; agent+backends see plain ints | `--num-ctx 128k` ergonomics without touching backend code; display side uses `fmt_token_size` (integer K/M) |
| **TPS = per-response, not per-run** (R07.17) | Footer ⚡ segment from `_gen_*` trio recorded in `_generate_with_retry` | Run-average skews low (tool execution + memory gaps); per-response reflects actual model speed |
| **Weight quant detection** (R07.18) | `_detect_weight_quant`: /api/show → list_models → None; looked up once at startup, not re-derived on /model | Best-effort display; a fresh API call per switch was deferred until someone asks |
| **HTTP client** | `urllib.request` (stdlib) | Zero-dep; trade-off: no pooling, manual SSE buffering, GC-dependent close (ROB-06) |
| **Agent composition** | 5 mixins over god-class (R07.00) | Testable separation; trade-off: unverified mixin host contract |
| **`is_cloud` attribute** | Per-class on `BaseBackend` | Replaced hardcoded provider lists; cloud catalog defaults + detection gates hang off it |
| **`CloudBackend` base** (R07.05) | New cloud backends = ~100 LOC | Subclasses override data + 5 methods; retry-loop skeleton NOT yet lifted (ROB-29/MAINT-23 open) |
| **Update check always-live** | No cache (R07.00) | Owner's refresh script relies on uncached check (ROB-05 WONTFIX) |
| **Plugin pins opt-in** (R07.05) | sha256 verified fail-closed WHEN present | Closes SEC-06; no enforcement mode yet (SEC-13 open) |
| **Parallel tool batches** (R07.15) | Concurrent for independent batches, committed in call order | Latency win with byte-identical transcripts; mutating tools stay sequential |
| ** Souls + skills bundling** | 3 souls, 4 skills in-tree | Persona/format split; soul loading is best-effort with default-prompt fallback |
| **Capabilities-first tool probe** (R07.19) | `/api/tags` capabilities verdict over sampled probing; ONE `tools` column; NONE→REACT normalizer; `think` column off the same declaration | Authoritative, zero-inference, one cache namespace; a model can never surface none anywhere |
| **ZAI probe error taxonomy** (R07.19 #12) | Transient → retry/backoff → UNTESTED uncached; definitive 400 rejection → REACT; probe sends thinking-disabled + max_tokens 512 | "Capability unknown ≠ capability absent" — one rate-limited scan must not poison the cache (glm-4.7-flash lesson) |
| **OpenRouter aggregator assumption** (R07.05, reviewed R07.19) | `test_tool_support` returns NATIVE unconditionally; runtime 400→ReAct fallback is the safety net | No per-model probe across 467 models; non-chat slug classification tracked as ROB-39 for R07.20 |

---

## What's Missing / Incomplete

1. **24 OPEN findings** in `audit/audit.md` (full detail + priority matrix there): Security 1 (SEC-09 ACP Basic-Auth-over-HTTP default) — SEC-13 WONTFIX R07.24 + SEC-20 closed R07.24 · Robustness 9 (incl. ROB-33 Windows process-kill, ROB-31 Pollinations entitlement mismatch, ROB-02 orchestrator thread join, ROB-06 Windows conn release, ROB-15 memory-store double-lock, ROB-29 Mistral retry-loop dup) — ROB-28 + ROB-41 + MCP-01 + MCP-05 closed R07.24 · Maintainability 5 (MAINT-01 1,733-line cmd_chat, MAINT-03 fuzzy args, MAINT-22 streaming body bypass, MAINT-23 retry skeleton, MAINT-27 `/sh` inline branch) — MCP-03 closed R07.24 · New Features 5 (FEAT-03 tool output schema, FEAT-05 plugin sandbox, FEAT-06 streaming arg deltas, FEAT-07 conversation export, FEAT-08 free-TIER mode) · Testing 5 (TEST-01 integration tier, TEST-03/04/05/07/09/10) — TEST-11 closed R07.24 · Performance 0 (MCP-04 closed R07.24) · Architecture 0 (MCP-02 WONTFIX R07.24).
2. **No integration tests** — all 2,932 tests are mocked unit tests (TEST-01); coverage baseline 42.7% (R07.01), CLI layer well below. TEST-09/10 were the same shape recurring (live-shape contract test gap) — TEST-11 closed it for `mcp search`; TEST-09/10 still open for plugin streaming + Pollinations free-model.
3. **No mypy** — no `[tool.mypy]`; mixins' host contracts are unverifiable by tooling.
4. **`schemas/v0.2/plugin.schema.json` declared but not validated** — ad-hoc dict checks in `_parse_manifest`. (R07.22 shipped `SECURITY.md` + `CONTRIBUTING.md`, closing the trust-artifacts gap.)
5. **No conversation export/import** (FEAT-07); no tool output JSON Schema validation (FEAT-03); no streaming `function_call_arguments.delta` (FEAT-06).
6. **`agentkthx/examples/`** are demo scripts, not doctests; they do NOT use `shared_args`/`SharedConfig` (despite its docstring claim).
7. **`patches/` not integrated into the build.**
8. **101 findings archived** in `audit/deltas.md` (92 CLOSED across R07.00–R07.24 + 9 WONTFIX with owner rationale). R07.22 added MCP-01..05 (filed but missing from Findings Summary — reconcile drift corrected at R07.23 re-audit). R07.23 added SEC-20, ROB-41, TEST-11. R07.24 closed 8 in two batches (batch 1: SEC-20, ROB-41, TEST-11, ROB-28; batch 2: MCP-01, MCP-03, MCP-04, MCP-05) + WONTFIX 2 (MCP-02, SEC-13 — deferred, out-of-scope). The register's near-term tier (ROB-33, ROB-31, ROB-02, ROB-06, ROB-15, SEC-09, MAINT-03, MAINT-22, MAINT-23, MAINT-01, TEST-01, TEST-03) shipped unchanged through R07.22 → R07.24. `generate_audit_dash.py` merges both files for the full register.

---

## Quick Start for Developer

1. **Read the Critical Files Index** — start with `cli/agent_factory.py` (wiring), `shared_args.py` (flags + the two NEW findings), `plugins/turboquant/turbo.py` (server lifecycle), `core/agent_setup.py` (prompt strategy + constructor). The "Why It Matters" column tells you when to touch each.
2. **Understand the Lifecycle** — `cmd_chat → _build_agent (auto-detection + catalog ladder + quant detect) → Agent.run → _run_loop_iteration → _generate_with_retry (records TPS) → backend.generate (kwargs forwarding) → tool dispatch (sanitize → memory)`.
3. **Check Known Landmines** before changing:
   - Never probe process liveness with `os.kill(pid, 0)` on Windows paths (ROB-33)
   - Don't follow the UNTESTED debug hint — `--force-react=False` errors (MAINT-25)
   - Mid-session tool addition: `register_tool`, NOT `add_tool` (memory clear, no warning)
   - New agent-internal kwarg → add to `_agent_internal` in BOTH llama_server methods
   - New CLI param → wire in 3 surfaces + `parse_shared_args` + `SharedConfig`, use `is not None` (not `or`), mirror the `_explicit` pin
   - `AGENTKTHX_NO_UPDATE_CHECK=1` skips the 3-request startup check
   - `MemoryConfig.max_tokens=0` default — token tier is opt-in
4. **Follow Patterns** — hybrid native/ReAct tool calling via `_use_native_tools`; `num_predict = ctx // 32`; `sanitize_tool_output` on every tool result; per-release regression-test files (e.g. `tests/test_r07_18_repeat_penalty.py`).
5. **Blast radius**: `core/helpers.py` → 18+ modules · `backends/cloud_base.py` → 8 cloud plugins · `cli/agent_factory.py` → every backend launch · `plugins/_loader.py` → every backend load · `shared_args.py` → every CLI surface.
6. **Run tests before committing**: `python -m pytest tests/ -q` (~21s, 2,932 tests, +20 skipped). Lint is a REQUIRED CI check: `ruff check agentkthx/ tests/ && black --check agentkthx/ tests/`. Live-gated contract tests: `AGENTKTHX_LIVE_TESTS=1 python -m pytest tests/test_mcp_live_contract.py -v`.
7. **Read the register before adding work**: `audit/audit.md` (24 OPEN, ID-indexed, priority matrix) + `audit/deltas.md` (101 archived with closure prose). Re-audit workflow, split tooling, and the dashboard parser contract are specified in `agentkthx/skills/codebase-audit/SKILL.md`.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. The `core/` package is the engine — most changes start there; R07.19-era work concentrates in `cli/commands/chat.py` + `cli/picker.py` + `cli/commands/models.py` + `core/environment.py` + `core/tool_cache.py` + `backends/ollama.py`/`openai_compat.py` + `plugins/zai/zai.py`; R07.22 added `agentkthx/mcp/` (MCP client package); R07.23 expanded `mcp` to 5 actions (`list`/`init`/`probe`/`search`/`install`) + `registry.py` + `cache.py` + memory first-user preservation fix + FREE_ONLY picker parity fix; R07.24 (closure batch 1) added `--no-overwrite` + `WOULD OVERWRITE` (SEC-20), `search_all_with_errors` + plain-mode network-error hint (ROB-41), live-gated contract test file `tests/test_mcp_live_contract.py` (TEST-11), MistralBackend catch-narrowing (ROB-28); R07.24 (closure batch 2) added `_read_response` thread+queue + `is_poisoned` (MCP-01), `_extract_params` `arguments_json` fallback + `_invoke` unwrap (MCP-03), `connect_all(lazy=True)` + `warmup_server()` + `_invoke` auto-warm (MCP-04), `MCPClient.set_notification_handler` + `_refresh_tools_for_server` + `ToolRegistry.unregister_tool` + `MCPManager.on_tools_changed` callback (MCP-05); WONTFIX: MCP-02 + SEC-13 (deferred — out-of-scope).

