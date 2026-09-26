## [R06.58] - 2026-09-25 11:07:05 AM

### Bug Fixes

- **Compaction: oversized messages in the recent window bypassed compaction** — a single oversized message (e.g. a 200KB `read_file` result) inside `keep_count` survived compaction untouched because it was "recent", quietly consuming half the context. `Memory.compact_messages()` now applies a per-message size cap (`max_kept_msg_chars` = 8KB, ~2K tokens) to ALL non-system messages regardless of position, keeping head + tail (the tail is where tool success/error markers usually live). Compaction is now idempotent — existing `[compacted]`/`[truncated]` markers prevent re-truncating already-compacted content.
- **ctx% footer pinned at 100% after compaction** — the fallback token-estimation path did `_running_tokens_in += _est_in` every step, where `_est_in` was the size of the ENTIRE history — after N steps the running total was N× the actual memory size. Compounding that, the reset only fired when `compacted > 0`, so a fully-compacted memory followed by a nothing-to-compact call kept the stale inflated totals forever (users reported "compaction not firing at 100% ctx" — the display was lying, not the compaction). New `_snapshot_running_tokens()` recomputes the running in/out totals from current memory after every step's generate and after every compaction attempt — the single source of truth for the footer's ctx%.
- **Context-length 400 death-loop on long agentic runs** — compaction previously only fired on the FIRST context-length failure (`_api_failure == 0`). When the first compaction wasn't aggressive enough, every subsequent retry hit the same wall with no further compaction. Now compaction is attempted on EVERY context-length failure as long as the previous attempt actually freed something (retry counter resets on success); if compaction freed nothing, the input is already minimal and the error falls through to the transient-error path instead of infinite-looping.
- **Compaction between tool calls within one assistant message** — when the model emits multiple tool calls in one response ("read A, read B, read C"), each result is appended to memory inside the dispatch loop, but compaction only fired at the TOP of the next step — so 5 large tool results could blow past the context window before compaction ever noticed. `_check_compaction()` now runs after each tool result commits when a message carries multiple tool calls, keeping memory bounded mid-step too.
- **Agent mode `--stream` was silently ignored** — `cmd_agent` never passed the stream flag to `AgentMode`, so `--stream` had no effect (no typewriter output, no inline tool-call printing, no `[Compaction]`/`[Context]` events). `AgentMode` now accepts `stream=`, and `cmd_agent` resolves it exactly like `cmd_chat`: explicit `--stream`/`--no-stream` wins, otherwise cloud backends default to streaming and local backends to non-streaming.
- **Agent mode step counter stuck at [1/M]** — `run_task()` iterates steps with `enumerate()` but never called `plan.advance()`, so `current_step_index` stayed at 0 for every step: the ⟳ progress line always read `[1/M]` and `get_status()`/`get_progress()` reported the wrong step mid-run. `_execute_step()` now syncs `current_step_index` before executing each step.

### Features

- **Agent mode persistent footer** — the 2-line scroll-region footer from chat mode (line 1: version, model, prompt size, ctx, max_tokens, temperature; line 2: backend, token usage ↑in/↓out, ctx% with warning coloring) is now ported to `cmd_agent`, updating in place during streaming via the same `_on_step_callback` mechanism as `cmd_chat`.
- **Agent mode upfront plan display** — the full plan now prints before execution starts (`📋 Plan: N step(s)` followed by numbered steps), matching the verbosity users expect from autonomous agent CLIs, and the ⟳ line carries `[N/M]` step progress.
- **`scripts/bump-version.sh`** — new release tooling. Bumps the version across all 4 declaration sites (pyproject.toml, `__init__.py` header comment, `__version__`, README title) in one command; accepts a release tag (`R06.58`) or semver (`0.6.58`) and auto-derives the other; `--dry-run` previews changes, `--current` prints the current version. Intentionally does NOT touch historical `R06.xx:` code comments (those reference the version that introduced a change) or `docs/CHANGELOG.md` (prose, not regex-able). Exit codes: 0 success / 1 bad invocation / 2 already at target version.

### Architecture

- **MAINT-04 Phase 1 — `_generate_with_retry()`** — the near-identical ~67-line API-resilience retry loops in `_run_core()` and `_run_core_streaming()` (~80 lines of overlap) extracted into a single shared helper. The only behavioral difference between the two paths — the context-length-400 compaction recovery — is preserved via the `enable_compaction_recovery` flag (streaming only). Future retry/backoff/terminal-error fixes now land in ONE place and apply to both paths automatically.
- **MAINT-04 Phase 2 — `_handle_finish_reason()`** — the duplicated ~25-line `length`/`content_filter` terminal-reason blocks extracted; both paths now produce identical StepResult entries and response status transitions for the same `finish_reason`.
- **MAINT-04 Phase 3 — `_check_tool_choice_required()` + `_parse_tool_calls()` + `_finalize_run()`** — the tool_choice enforcement check (duplicated 4×, ~24 lines), the tool-call parser handling native OpenAI-format and ReAct/JSON/XML parsed calls (duplicated 2×, ~40 lines), and the response-finalization block (duplicated across 10 exit points, ~70 lines) extracted. Every exit path now stores the response, sets `total_tokens`, and computes `total_ms` identically.
- **MAINT-04 Phase 4 — `_enforce_final_answer()` + `_handle_blocked_tool_call()` + `_reject_for_tool_choice()`** — the Final-Answer enforcement block (duplicated 4×, ~56 lines), the repeat-blocked-call guard (duplicated 2×, ~46 lines), and the tool_choice rejection block (duplicated 4×, ~40 lines) extracted. The rejection helper parameterizes the previously-accidental user-facing message variation between the streaming and non-streaming paths, preserving each path's behavior exactly. Phases 1-4 total: 9 extracted methods, ~380 lines of duplicated agentic-loop logic eliminated.

### Documentation

- **R07.00 Modularization Plan** — new `docs/R07.00-MODULARIZATION-PLAN.md` (269 lines): the draft plan for Phases 5-10 — agentic-loop unification (`core/agentic_loop.py`), streaming machinery (`core/streaming.py`), compaction subsystem (`core/compaction.py`), agent setup, tool execution, and the full `cli.py` → `cli/` package split. Recommended execution order 7 → 9 → 10 → 6 → 5 → 8 (lowest risk first). Targets: `agent.py` 3466 → ~300 lines, no file > 800 lines, zero behavioral changes, 933-test suite green after every phase.

### Tests

- 933 passed, 9 skipped, 0 failed (was 833 at R06.57 baseline; +100 new tests)
- New: `tests/test_compaction_tokens.py` (7), `tests/test_agent_mode_verbosity.py` (8), `tests/test_bump_version_script.py` (9), `tests/test_agent_mode_footer.py` (3), `tests/test_generate_with_retry.py` (9), `tests/test_handle_finish_reason.py` (9), `tests/test_maint04_phase3_helpers.py` (24), `tests/test_maint04_phase4_helpers.py` (31)

## [R06.57] - 2026-09-24 9:59:54 PM

### Bug Fixes

- **ROB-05** — Streaming `KeyboardInterrupt` now calls `stream_gen.close()` before returning, releasing the HTTP connection deterministically instead of waiting for GC.
- **ROB-06** — OpenRouter & Gemini `_iter_sse_lines` + `_stream_request` now wrap the yield loop in `try/finally response.close()`, matching ZAI's existing pattern. All 4 cloud backends now have uniform streaming cleanup.
- **BitNet `bitnet_mode` kwarg collision** — `BitNetBackend.__init__` now pops `bitnet_mode` from kwargs before passing the hardcoded `True` to `super()`. Previously `get_backend("bitnet", api_mode=...)` crashed with `TypeError: got multiple values for keyword argument 'bitnet_mode'`.
- **BitNet "Unsupported param: tools" fallback** — `OllamaBackend.generate()`, `generate_completions()`, and `test_tool_support()` now match `"unsupported param: tools"` (llama-server 500) in addition to `"does not support tools"` (Ollama 400). Retries without `tools` (ReAct mode) instead of dying as a fatal API error.
- **TurboQuant port cleanup** — `turbo start` now calls `_free_port()` before starting, killing any stale/zombie llama-server process holding port 8764. Previously hung for 120 seconds printing dots if a zombie from a previous session was still on the port.
- **TurboQuant zombie detection** — `_is_process_alive()` now reads `/proc/<pid>/stat` and treats zombie processes (state `Z`) as dead. Previously zombies passed the `os.kill(pid, 0)` check and caused the startup health-check loop to hang.
- **TurboQuant startup error reporting** — When the server process dies during startup, the error message now includes the last 5 lines of `~/.agentkthx/turbo.log` so the user sees the actual error (e.g. "couldn't bind HTTP server socket" or "error loading model") instead of a generic "died during startup" message.
- **`max_tokens` cap applied at agent level** — The `num_ctx/32` cap was only in `_get_model_defaults` (fires when `max_tokens is None`), but the agent always passes `max_tokens` explicitly from `model_config.default_max_tokens` (8192). Now capped directly in `agent.py` at both the non-streaming and streaming paths. `max_tokens=8192` with `num_ctx=8192` (which left zero room for input) now correctly caps to 256.
- **Reasoning panel order in non-streaming mode** — `cmd_chat` and `cmd_run` non-streaming paths now show the `reasoning:` panel ABOVE the `AgentKthx:` response, matching the streaming UX-01 layout. Previously non-streaming showed the answer first, then reasoning below.
- **TurboQuant `recommended_turbo_config` cache types** — Changed the asymmetric path from `q8_0/turbo4` to `turbo4/turbo4` for all non-high-quality weight types. Sending `q8_0` directly crashes on qwen35 architecture (`rope.dimension_sections` bug); `turbo4` works because the server's own auto-asymmetric logic upgrades K to `q8_0` internally when it detects a high GQA ratio.
- **TurboQuant `head_dim` compatibility check removed** — Empirical testing (14 models × 5 cache types = 70 combinations) showed the `head_dim >= 128` check was completely wrong: 7 models with `head_dim=64` loaded fine with all turbo cache types, and 1 model with `head_dim=128` (functiongemma) actually failed. The server itself is now the authority — if a model can't load, it reports a clear error.
- **`LlamaServerBackend` queries `/v1/models` for context** — `get_model_runtime_context()` now reads `meta.n_ctx` from the server's `/v1/models` response (the actual `--ctx-size` the server was started with). `get_model_max_context()` reads `meta.n_ctx_train` (the model's trained max context). Falls back to `NUM_CTX` env var / family heuristics / 4096.
- **`LlamaServerBackend.list_models()` parses `meta` fields** — Now extracts `n_ctx`, `n_ctx_train`, `n_params`, and `size` from the `/v1/models` response. Previously only extracted the model `id` and threw away everything else.

### Architecture

- **MAINT-05** — Replaced 8 hardcoded backend allowlists in `cli.py` with a single `is_cloud: bool` class attribute on the backend hierarchy. Adding a 5th cloud backend is now a 1-line change instead of an 8-site edit.
- **ARCH-03** — Lifted the triplicated 429 retry / `num_ctx/32` cap / `_calculate_safe_max_tokens` pattern into `OpenAICompatibleBackend` as 3 shared methods + 6 overridable class attributes. Concrete backends shed 213 lines of duplication.
- **Local backend parity** — `OllamaBackend._get_model_defaults` now uses the shared `_apply_max_tokens_cap` helper, so Ollama/llama-server/BitNet get the same `num_ctx/32` cap + `_context_safe_max_tokens` persistence as the cloud backends. `OllamaBackend._iter_sse_lines` now uses the shared `_handle_context_length_400` handler for context-length 400 recovery. `OllamaBackend.__init__` initializes `_context_safe_max_tokens`.

### Features

- **FIX-01** — Pip-installed users now see dev releases. New `_fetch_github_latest_version()` fetches `raw.githubusercontent.com/.../__init__.py`, parses `__version__`, and `format_notice` surfaces the dev track with a `pip install --force-reinstall git+...` command. `agentkthx version` shows a new "GitHub main: X.Y.Z" line. `agentkthx version --refresh` bypasses the cache.
- **DOC-01** — Added `### Gemini Configuration` subsection to README with all 7 `GEMINI_*` env vars + usage examples.
- **Notebook TurboQuant cells** — Added 6 new cells to `AgentKthx.ipynb`: clone `feature/turboquant-kv-cache` branch, patch GCC 13.3 + build llama-server (static CPU-only), start server (reuses Ollama blob path), Cloudflare tunnel, backup binary to Drive, restore binary from Drive.

### Tests

- 833 passed, 9 skipped, 0 failed (was 766 at R06.56 baseline; +67 new tests)
- New: `tests/test_is_cloud_attribute.py` (24 tests — 13 is_cloud + 6 BitNet kwarg + 5 unsupported-param-tools), `tests/test_context_length_recovery.py` (23 tests), `tests/test_update_check.py` +20 tests

## [R06.56] - 2026-09-24 1:35:20 PM

### Features

- **FEAT-01 — Gemini cloud backend** (`agentkthx/plugins/gemini/`) — First new cloud provider since R06.41's plugin system. Adds Google Gemini via its OpenAI-compat endpoint with a 10-model static catalog (Flash/Pro 2.x–3.x), 1-hour `list_models` cache, `GEMINI_FREE_ONLY` filter, ROB-06 context-safe `max_tokens` persistence, `reasoning_effort` ↔ `extra_body.google.thinking_config` mutual exclusivity, `429 RESOURCE_EXHAUSTED` retry with `Retry-After` honoring (5s→90s cap), and spend-limit 401/403 actionable migration messages. Free tier: 5 RPM / 250K TPM / 1500 RPD on `gemini-3.8-flash`. New env vars: `GEMINI_BASE_URL`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `GEMINI_DEFAULT_MODEL`, `GEMINI_FREE_ONLY`, `GEMINI_THINKING_LEVEL`, `GEMINI_SERVICE_TIER`, `GEMINI_MAX_429_RETRIES`.
- **FEAT-02** — Embedded `FREE_TIER_LIMITS` table (53 entries) transcribed from Google AI Studio's rate-limits page (API exposes no pricing/free-tier endpoint). New `_is_free_tier_model()` 3-step classifier (exact table → family heuristic → default NOT free) replaces the naive `"flash" in model_id` check; `FREE_TIER_LIMITS` consistency asserted against the catalog.
- **FEAT-03 — Gemma `<thought>...</thought>` inline tag parser** — New `ThoughtTagParser` stateful streaming parser (2-state machine: OUTSIDE/INSIDE) routes reasoning to `reasoning_content` instead of leaking raw tags into the user-facing stream. Handles partial tags at chunk boundaries, stray closing tags, and unclosed-at-EOF.
- **FEAT-04** — Verified Gemma IS chat-capable via `/v1beta/openai/chat/completions` on real VM; removed `"gemma-"` from `_NON_CHAT_PATTERNS`.
- **FEAT-05** — New `/models` (with `free`/`chat`/`⚠deprecated` markers + `free`/`chat` filters), `/tool` (mid-session tool loading, comma-separated, fuzzy suggestions), `/skill` slash commands. `/tools` and `/skills` now show ALL available with `✓`/`○` markers, not just loaded.
- **FEAT-06** — `gemini-2.5-*` models show `⚠deprecated` marker in `/models` — Google's API returns 404 for new users on 2.5 variants; client-side heuristic saves a 404 round-trip.

### Bug Fixes

- **BUG-01** — `agentkthx models --backend gemini` crashed (`ValueError: Gemini backend only supports OpenAI Chat-Completions`) because `cli.py:2091` hardcoded `ApiMode.OPENRE` for any non-OpenRouter backend. Fixed by adding `gemini` to the api_mode allowlist; Gemini's `__init__` also normalizes `OPENRE → OPENAI` defensively.
- **BUG-02** — `agentkthx models --backend gemini` showed empty table (count 10, 0 rows) because `BackendType.GEMINI` was missing from all 6 cloud-provider allowlists in `cli.py`. Fixed via `replace_all`; side benefit: Gemini now gets default-streaming=True and catalog-based `num_ctx`/`num_predict` defaults.
- **UX-01** — Reasoning was shown twice (inline during streaming + as post-stream panel). Fixed: reasoning now streams as a `reasoning:` panel above the `AgentKthx:` prompt with 4-space indentation; post-stream panel suppressed in streaming mode.

### Documentation

- **DOC-01** — New `docs/GEMINI_API_TECHNICAL_REFERENCE.md` (1553 lines, 64 KB) — 11-section technical reference grounded in 11 live Gemini docs pages, plus appendices on OpenAI-compat limitations, region availability, and reasoning token costs.
- **DOC-02** — Added comment blocks in `gemini.py` documenting the endpoint each non-chat model family uses (image gen → `/images/generations`, video → `/videos`, etc.) and the source/refresh instructions for `FREE_TIER_LIMITS` (AI Studio is the only authoritative source).

### Known Limitations (v0.1)

- Thought-signature stateful continuation NOT yet implemented (~2-3× reasoning-token overhead on multi-turn Gemini 3.x loops); `thought_signature` parameter is plumbed through `_build_openai_body()`. Native Files API (>20MB multipart) and Live API / Computer Use out of scope.

### Tests

- 766 passed, 9 skipped, 0 failed (was 710 at R06.55 baseline; +56 new tests)
- New: `tests/test_gemini_backend.py` (+41 unit + 3 live-API opt-in), `TestFreeTierClassification` (+19), `TestThoughtTagParser` (+13), `TestChatCapabilityClassification` (+11), plus BUG-01/BUG-02 regressions.

### Distribution

- **DIST-02** — `AgentKthx-gemini-plugin.zip` (879 KB, 211 files, sha256 `4f3a356e2ba27d21…`) on the session preview panel; verified by extracting to `/tmp` and running the suite (766 passed).

## [R06.55] - 2026-09-23 9:18:11 PM

### Architecture

- **ARCH-01 — `OpenAICompatibleBackend` extracted** — New intermediate base class (`agentkthx/backends/openai_compat.py`, 709 lines) between `BaseBackend` and concrete backends, holding shared JEV dispatch, body construction (`_build_openai_body`), response parsing (`_parse_openai_response`), streaming SSE (`generate_completions_stream`), and the `api_mode` property. Concrete backends implement 4 abstract hooks (`_get_chat_completions_url`, `_get_auth_headers`, `_iter_sse_lines`, `_get_model_defaults`). 534 lines of duplication removed; the R06.53/R06.54 streaming 404 bug class is now structurally impossible (each backend owns its URL).
- **BUG (post-ARCH-01)** — `agentkthx models --backend openrouter` crashed with `AttributeError: get_model_runtime_context` because 3 OllamaBackend-only methods weren't inherited. Fixed by moving `FAMILY_CONTEXT_DEFAULTS`, `get_context_by_family`, `get_model_runtime_context`, `get_model_context_size` to `OpenAICompatibleBackend`.
- **POLISH** — `agentkthx models` cloud-provider table drops the always-`unknown` `Size` column; `NAME_W` widened to 50 chars so long OpenRouter model names (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`) no longer push the Context column askew. Local layout (Size + Family) unchanged.

### Bug Fixes

- **ROB-04 — `requests` dependency removed** — Rewrote all 4 `requests` usage sites in `openrouter.py` to stdlib `urllib.request` (module-level import, `list_models`, `_make_api_request`, `_stream_request`/`_iter_sse_lines`). `pyproject.toml`'s `dependencies = []` is now accurate; users on minimal Python installs (no `requests`) no longer see a silent plugin-load failure.
- **ROB-06 — Context-length 400 handling for OpenRouter streaming** — Three-layer fix: preventive `max_tokens = context_length // 32` cap (empirical 3% reserve) in `_get_model_defaults()`; reactive parse of the 400 error message in `_iter_sse_lines()` extracts actual token counts and retries with `safe_max`; safe value persisted on `self._context_safe_max_tokens` so future steps reuse it. Long agentic runs (30+ calls, 200K+ tokens) no longer die mid-run on `:free` models with large context windows.

### Features

- **Streaming tool output** — `_run_core_streaming()` now prints each tool call + result inline during the agentic loop (tool number in dim grey, "tool" in cyan, name in yellow, args/result truncated to 120/200 chars). Post-run `_print_agent_steps()` summary suppressed in streaming mode to avoid duplication.
- **Memory Compaction** — New `--compaction` CLI parameter (`auto`/`85`/`90`/`off`); default `auto` triggers compaction at 85% of `num_ctx`. `Memory.compact_messages(keep_count=10)` truncates older message content to 200 chars + `[compacted]` marker but preserves tool names/args; system messages and recent 10 messages kept intact. Reactive compaction also fires on context-length 400s.
- **Session token % in status footer** — Footer line 2 shows `ctx N%` colored green (0-59%) / yellow (60-84%) / red (85%+), computed from `(session_in + session_out) / num_ctx * 100`.
- **Real-time token tracking** — Usage chunks from OpenRouter's final SSE chunk (`choices: []` with `usage: {...}`) now captured via `_usage` key, surfaced to `_generate_stream()`, and used to update `self._running_tokens_in/out` after each step. Token estimation fallback (`chars ÷ 4`) for `:free` models that don't return usage. After compaction, running totals recalculate from post-compaction memory state (no longer stuck at 100%).
- **`_will_stream` UnboundLocalError fix** — `--debug` mode crashed after first response; moved variable outside the `if not agent.debug:` block. OpenRouter API docs updated to reflect `stream_options.include_usage` is now implemented.

### Tests

- 672 passed, 6 skipped, 0 failed (was 671 — added 1 test for `_iter_sse_lines` presence on OpenRouter).

## [R06.54] - 2026-09-22

### Bug Fixes

- **BUG — ZAI streaming hit HTTP 404 via inherited `OllamaBackend` method** — `ZaiBackend.generate_completions_stream()` override now uses the correct `/api/paas/v4/chat/completions` endpoint (not OllamaBackend's `/v1/chat/completions`), injects `Authorization: Bearer` headers, sends `stream_options.include_usage=True`, and mirrors `_generate_with_auth` error recovery (429 insufficient credits → free fallback, "Does not support tools" → ReAct fallback).

### Tests

- 671 passed, 6 skipped, 0 failed (was 662; +9 new in `tests/test_zai_streaming.py` covering override detection, dict shape, SSE parsing, URL, headers, reasoning_content, tool_calls, stream_options).

## [R06.53] - 2026-09-22

### Features

- **PERF-01 — Real Streaming Display** — `Agent.run(stream=True)` previously accepted but silently ignored `stream`; cloud users saw a spinner until the full response arrived. New `_generate_stream()` (~250 lines) picks the best transport (OpenAI Chat-Completions SSE → native Ollama → non-streaming), prints content deltas immediately (typewriter), emits `reasoning_content` in dim grey inline, accumulates `tool_calls` fragments across chunks (with malformed-JSON `_raw_arguments` fallback), and returns the same dict shape as `_generate()` so callers are agnostic. New `_run_core_streaming()` (~500 lines) is intentionally a near-copy of `_run_core()` to preserve the non-streaming path's bug fixes. KeyboardInterrupt mid-stream returns `{"_cancelled": True}` cleanly.
- **PERF-02 — `stream_options.include_usage` on OpenRouter** — `OpenRouterBackend._build_openai_body(stream=True)` now emits `stream_options.include_usage=True`; non-streaming requests unaffected. Without this, OpenRouter's SSE chunks omit usage data entirely, making token tracking impossible for streamed responses.

### Bug Fixes

- **BUG — OpenRouter streaming hit HTTP 404 via inherited `OllamaBackend` method** — Inherited method built `{base_url}/v1/chat/completions` which on OpenRouter yields a doubled `/v1` path (404). New `OpenRouterBackend.generate_completions_stream()` override delegates to the existing `_make_api_request(stream=True)` retry path.
- **ROB-02 — Last bare `except:` replaced** — Final bare `except:` at `orchestrator.py:279` (multi-agent fallback-result processing loop) replaced with `except Exception:`. Bare `except:` silently suppressed user-initiated `KeyboardInterrupt`/`SystemExit`. Global scan confirmed it was the only remaining site.
- **BUG — `You:` prompt input overwrote the line at terminal EOL** — Three contributing causes fixed: `_position_for_input()` explicitly re-asserts `\033[?7h` (auto-wrap ON) before every prompt; `_update_footer()` redraw wrapped in `try/finally` to guarantee re-enable; readline prompt now uses `\001`/`\002` markers around ANSI escapes so readline counts prompt width correctly, plus `readline.parse_and_bind("set horizontal-scroll-mode off")`.
- **BUG — `agentkthx update` failed silently on PEP 668 externally-managed environments** — `cmd_update()` now detects PEP 668 via `_is_externally_managed_error(stderr)` (canonical / spaced / co-occurring / hint-pairing patterns) and prompts `Retry with --break-system-packages? [y/N]` after explicit user consent. Never silent, EOF/Ctrl+C safe, idempotent, bounded.
- **BUG — Banner showed R06.52** — R06.53 version bump only updated `pyproject.toml`; `agentkthx/__init__.py:__version__` was still `0.6.52`. Fixed.

### Documentation

- **MAINT-03** — `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` no longer references `AGENTNOVA_*` env vars (MAINT-02 missed it). Section renamed "Backward-compatibility env vars" → "Configuration env vars" and rewritten to reflect `AGENTKTHX_*` prefix.

### Tests

- 662 passed, 6 skipped, 0 failed (was 638; +27 new: 11 PERF-01 streaming, 3 PERF-02, 11 PEP 668, 2 OpenRouter stream method override — all in their respective test files).

## [R06.52] - 2026-09-21 1:46:06 PM

### Architecture

- **API Resilience — rate limits no longer kill agentic runs** — Layer 1: OpenRouter `_make_api_request` retry budget raised 3 → 6 (overridable via `OPENROUTER_MAX_429_RETRIES`), exponential backoff with jitter (5s→10s→20s→40s→80s→90s cap, ±20% jitter), 502/503/504 now retried through the same schedule, retry notices always printed (no longer `--debug`-only). Layer 2: new `agentkthx/core/api_resilience.py` retries transient `generate()` exceptions at the same step (10s base, doubling, 120s cap, doesn't consume `max_steps`), permanent errors (auth, 401/403/404, invalid request, insufficient credits) fail fast via text classification. `max_api_retries` (default 5, env `AGENTKTHX_MAX_API_RETRIES`) bounds consecutive failures. Chat mode explains how to resume (`continue` resumes from where it stopped).
- **Loop Resilience — codebase-audit death-spiral fix** — Five-bug cascade fixed: `is_error_result()` rewritten to inspect only the first non-empty line against a strict pattern list (no more "0 matches for 'error'" false positives); `should_terminate()` fires only when *every* tool call in each of the last N consecutive steps failed; identical-duplicate-call blocking (after 2 failures of the same `(tool, sorted-args)` signature, the harness blocks re-issue and injects a teaching observation); true termination with paired history (`memory.sanitize_history()` fills dangling tool_calls with placeholders — no more 400s on the next request); hallucinated-parameter stripping (`_execute_tool` removes args not in the tool's schema and coerces numeric strings); shell exit-code marker formatted as `[Exit code: N]\n<stdout>\nError: <stderr>` so the error marker is always the first line.

### Tests

- 638 passed, 6 skipped, 0 failed (was 599). +39 new in `tests/test_api_resilience.py` + 48 new in `tests/test_loop_resilience.py`.

## [R06.51] - 2026-09-21 9:48:55 AM

### Features

- **Update Check** — Installed copies now notice when a newer version is available on both release tracks. New `agentkthx/update_check.py` (stdlib-only) compares installed vs latest on PyPI (`pypi.org/pypi/agentkthx/json`) for stable and against the GitHub commits API for the dev track (only for git checkouts — pip installs carry no commit hash). At most one request per source per 24h, failed sources negatively cached for 6h. Notice placed under chat banner + pip-style post-run notice after non-interactive commands. `agentkthx version` shows `Latest on PyPI:` and `GitHub main:` lines. `--json` invocations never receive the notice. Opt-out via `AGENTKTHX_NO_UPDATE_CHECK=1`.

### Tests

- 54 new tests in `tests/test_update_check.py` (version compare, git-hash extraction, per-source cache hit/stale/expiry/negative-cache, pip-vs-checkout routing, silent failures, notice formatting, endpoint URLs, timeout forwarding, cache-dir creation).

## [R06.5] - 2026-09-21 8:19:42 AM

Plugin Specification v0.2 — tools + hooks are normative, `entrypoint` is honored, external plugin roots are discovered, manifests migrate to the `extensions` form. Spec and schema ship side-by-side with the untouched v0.1 spec.

### Features

- **`docs/PLUGIN_SPEC_v0.2.md`** — 24-section normative spec (RFC 2119 conformance language, plugin root discovery order, strict name constraints, manifest dual-form rules, `extensions` namespace, 4 plugin types, `entrypoint` contract, `provides` table, hooks spec, tools API, config + secret protection, env/placeholder expansion, dependency resolution, lifecycle diagram, failure boundaries, CLI contract, packaging, v0.1 → v0.2 migration mapping + timeline, conformance checklist).
- **`schemas/v0.2/plugin.schema.json`** — JSON Schema draft-07, dual-form (canonical `extensions["org.vts-tech.agentkthx"]` + LEGACY top-level fields marked deprecated), name pattern constraints, `provides.hooks` event enumeration, semver constraint patterns.
- **Loader rewrite (`agentkthx/plugins/_loader.py`)** — Dual-form manifests (canonical `extensions` + legacy top-level fields with deprecation warnings, `agentnova` compat key aliased warn-only); strict name validation (`^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$`, name must equal directory name); `entrypoint` honored (v0.1 always imported `__init__`); multi-root discovery (built-in → `~/.agentkthx/plugins/` → `$AGENTKTHX_PLUGIN_PATH`); topological dependency resolution (cycles rejected and named); `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` expansion; secret-shaped config values warn; complete `unload()` (purges config/tools/hooks/backends/CLI commands including failure paths); `provides.backends` entries must be `BaseBackend` subclasses.
- **Tools + hooks API on `PluginManager`** — `register_tool` / `unregister_tool` / `list_tool_names` / `get_tool` + `apply_to_registry` (bridges plugin tools into the core `ToolRegistry`, non-fatal). Five lifecycle hooks: `on_init`, `on_run_start`, `on_run_end`, `on_error`, `on_shutdown`; lazy subscriber resolution; `emit()` isolates errors per subscriber (one bad hook cannot kill a run); `on_init` fires once after all plugins load; `on_shutdown` fires in reverse load order via atexit-once guard.
- **CLI plugin management** — `agentkthx plugins` gains `--load NAME`, `--unload NAME`, `--reload NAME`, `--json` (clean stdout — chatter to stderr), `--verbose` (shows plugin root, legacy fields, failed state).
- **Bundled manifests migrated** — All 6 bundled plugin manifests (bitnet, zai, openrouter, turboquant, acp, test-plugin) now carry `$schema` + `extensions` blocks; the `agentnova` compat key renamed to `agentkthx`; hardcoded `ACP_PASS` secret removed (set via env instead). `pyproject.toml` package-data now ships `schemas/v0.2/plugin.schema.json`.

### Migration

- v0.1 manifests keep working (legacy top-level fields parse with deprecation warnings). Move manifest fields into `extensions["org.vts-tech.agentkthx"]` before v0.3 (hard cutoff). Rename `"agentnova": ">=0.4.0"` compat key to `"agentkthx"` (old key is aliased with warning). Don't ship secrets in manifests — reference env vars via `env` entries. PyPI: R06.5 ships as **0.6.50** (literal `0.6.5` would be a downgrade on PyPI).

### Tests

- 479 passed, 6 skipped, 0 failed (was 429). +50 new conformance tests in `tests/test_plugin_spec.py` covering names, dual-form parsing, `$schema`, entrypoint, compatibility aliases, multi-root discovery, env parsing, placeholder expansion, secret detection, hook error isolation, tool registry, unload completeness, dependency ordering, cycle rejection, on_init-once, `BaseBackend` rejection, bundled-manifest smoke.

## [R06.41] - 2026-09-20 8:52:23 PM

Audit fixes (ROB-01, MAINT-02, ROB-03, SEC-01) + cleanup.

### Security

- **SEC-01 — `eval()` sandbox bypass closed (HIGH)** — `eval()` with bypassable `{"__builtins__": {}}` namespace at `core/math_prompts.py:220` and `core/helpers.py:822` (attribute traversal payloads like `().__class__.__bases__[0].__subclasses__()` still worked). New `agentkthx/core/safe_eval.py` is a recursive AST walker that allows numeric/bool/None constants, names from an allowlist, binary/unary/boolean operators, comparisons, conditional expressions, and direct function calls to allowlist names. Rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, `ast.Starred`, `ast.Import`, `ast.Slice`, `ast.Await`/`ast.Yield`, `ast.NamedExpr`, and all collection literals. Three call sites refactored (`tools/builtins.py:calculator`, `core/math_prompts.py:evaluate_math_expression`, `core/helpers.py:normalize_tool_args`). Zero `eval()` calls remain in production source.
- **SEC-02 — `shell=True` accepted-risk + modestly hardened (Medium)** — Documented the threat model (blocklist is a guardrail against model mistakes, NOT a defense against determined prompt injection; `shell=False` would break legitimate agent workflows for a threat that doesn't manifest in practice). New `DANGEROUS_FLAG_COMBOS` dict — context-aware blocks on otherwise-safe commands paired with dangerous flags (`find -exec`/`-delete`, `xargs rm`, `python -c`, `perl -e`, `awk system(...)`, `tar --use-compress-program`, `cp /dev/null`, `busybox` added to outright blocklist). Legitimate uses of the underlying binaries still pass.

### Robustness

- **ROB-01 — Duplicate ACP/Turbo plugin files removed** — Deleted `agentkthx/acp_plugin.py` (2396 lines) and `agentkthx/turbo.py` (693 lines) — near-identical copies of `agentkthx/plugins/acp/acp_plugin.py` and `agentkthx/plugins/turboquant/turbo.py` that risked silent behavioral divergence. 3089 lines of dead-code duplicate removed; 3 in-function imports + 10 test imports + 7 `monkeypatch.setattr()` paths redirected.
- **ROB-03 — Pre-existing test failures (45 → 0)** — Three root causes: stale `agentkthx.backends.zai` import path (35 failures fixed by rewriting to `agentkthx.plugins.zai.zai`); IPv6 SSRF hostname extraction bug (`is_safe_url("http://[::1]/admin")` returned True — `parsed.netloc.split(":")[0]` mangles `[::1]` to `[`, replaced with `parsed.hostname`); `OpenRouterBackend._api_mode` missing in tests (9 failures — test helpers bypassed `__init__` via `__new__`, fixed by setting `b._api_mode = ApiMode.OPENAI`).

### Maintenance

- **MAINT-02 — `AGENTNOVA_*` env vars and filesystem paths renamed to `AGENTKTHX_*`** — Backward-compat aliases dropped entirely (user opted in — few users). 18 env vars renamed (`AGENTNOVA_BACKEND` → `AGENTKTHX_BACKEND`, etc.); filesystem paths `~/.agentnova/` → `~/.agentkthx/`, `~/.cache/agentnova/` → `~/.cache/agentkthx/` (Unix), `%LOCALAPPDATA%\agentnova\cache` → `%LOCALAPPDATA%\agentkthx\cache` (Windows); Python identifiers (`config.AGENTNOVA_*` → `config.AGENTKTHX_*`, `_get_agentnova_dir` → `_get_agentkthx_dir`, `_DEFAULT_DB_DIR`); CLI usage strings (`agentnova run` → `agentkthx run`). Side-benefit: fixed latent `agentkthx/soul/loader.py` NameError (`agentnova.__file__` → `agentkthx.__file__`).
- **Cleanup** — Removed 13 outdated audit PNGs (3.6 MB); moved `docs/audit.md` → `audit/audit.md` and `docs/brief.md` → `audit/brief.md`; updated `docs/ARCH.md` file tree. Deleted 3 stale test files: `tests/test_r046_changes.py` (21 tests — R04.6 verification, now baseline), `tests/test_r048_changes.py` (81 tests — R04.8 plugin-ization verification, now baseline; SEC-01 calculator coverage duplicated in `tests/test_security.py`), `tests/test_zai_backend.py` (35 tests — pre-plugin path `agentkthx.backends.zai`, 9 were failing on baseline). 2 PrintAgentSteps tests `@pytest.mark.skip` with explicit reasons (test-vs-code mismatch on `sys.stdout` capture).

### Scope Boundaries (intentionally NOT changed)

- `agentnova/` and `localclaw/` redirect stub packages kept as-is (downstream compat). `"agentnova"` framework identifier in `skills/loader.py` kept as-is (existing skill manifests may declare `frameworks: ["agentnova"]`). `"source": "agentnova"` field in ACP plugin API calls kept as-is (external wire-format contract with ACP servers).

### Tests

- 429 passed, 0 failed, 6 skipped (was 457 passed / 45 failed / 4 skipped; +72 new security tests: 49 SEC-01 + 23 SEC-02). 6 skips all have explicit `@pytest.mark.skip(reason=...)` annotations.

## [R06.4] - 2026-09-20 12:53:41 PM

### Features

- **`--stream` and `--no-stream` on all commands** — Previously `--stream` was only on `run`. Now `chat`, `run`, and `agent` all accept both flags. Three-state logic: `None` (default — stream for cloud, non-stream for local), `True`, `False`. Also settable at runtime via `/param stream true|false`.
- **OpenRouter API Technical Reference** — New `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` (680 lines) covering auth, endpoints, request/response schema, sampling parameters, model catalog, function calling, streaming, provider routing, transforms/plugins, error codes, rate limits, free-tier behavior, implementation notes, and troubleshooting matrix. Mirrors `ZAI_API_TECHNICAL_REFERENCE.md`.

### Bug Fixes

- **JEV mode infinite recursion on OpenRouter** — `OpenRouterBackend._jev_call_completions()` → `self.generate()` → `_maybe_jev_dispatch()` → `generate_decision()` → `_jev_call_completions()` → `RecursionError`. Fixed by temporarily flipping `_api_mode` to `OPENAI` during the JEV call so `_maybe_jev_dispatch()` returns None (no JEV dispatch), restored in `finally`. ZAI unaffected (calls `_generate_with_auth` directly).
- **OpenRouter JEV empty response** — `:free` models silently return empty when `response_format={"type": "json_object"}` is forced. Fixed by stripping `response_format` from kwargs before `self.generate()` (JEV System-One prompt already instructs JSON-only output); belt-and-suspenders retry with simplified prompt on empty first attempt.
- **Calculator tool auto-loaded in JEV mode** — `run_parser` defaults `tools_default="calculator"` but decisions never call tools (`generate_decision()` passes `tools=None`). Fixed: `_build_agent()` suppresses tool loading entirely when `api_mode == "jev"`.

### Changes

- **`_load_skills_prompt()` returns a tuple** — Now `(str | None, list[str])` (prompt + loaded skill names) so `/skills` and `/status` can display them. Stashed on `agent._loaded_skills`.
- **Default `--max-steps` increased 10 → 25** — Better fit for agent workflows like codebase audits.
- **Agent loop forwards `_runtime_kwargs` to backend** — `top_k`, `seed`, `n`, `presence_penalty`, `frequency_penalty` set via `/param` are stashed in `agent._runtime_kwargs` and forwarded via `backend_kwargs` in both `run()` and `run_stream()`.

### Tests

- 245/245 tests pass.

## [R06.3] - 2026-09-20 12:10:12 PM

### Features

- **`/param` slash command** — Show or set model generation parameters with per-backend support matrix. Params filtered by what the current backend actually forwards (`top_k` rejected on ZAI, accepted on OpenRouter/Ollama). Supported: `temperature`, `top_p`, `max_tokens` (alias `num_predict`), `max_steps`, `num_ctx`, `top_k`, `seed`, `n`, `presence_penalty`, `frequency_penalty`, `thinking` (alias `thinking_level`), `think` (alias `show_reasoning`), `stream` (read-only). `/param reset <name>` resets to model default.
- **`/skills` slash command** — Lists loaded skills with descriptions; if none loaded, also lists available skills so you can see what to pass to `--skills`.
- **`/status` shows loaded skills + max steps** — `Max steps: 25` line added so you can verify the agent loop budget at a glance.

### Changes

- Default `--max-steps` increased 10 → 25 (across `agent.py`, `config.py`, `cli.py`, `shared_args.py`).
- `_load_skills_prompt()` returns `tuple[str | None, list[str]]` (prompt + skill names) stashed on `agent._loaded_skills` for `/skills` and `/status` display.
- Agent loop forwards `_runtime_kwargs` (`top_k`, `seed`, `n`, `presence_penalty`, `frequency_penalty`) to backend via `backend_kwargs` in both `run()` and `run_stream()`.

### Tests

- 245/245 tests pass.

## [R06.2] - 2026-09-20

### Bug Fixes

- **Chat mode displayed "Agent Nova:" instead of "AgentKthx:"** — Hardcoded `bright_green("Agent Nova")` in two places in `cli.py`. Updated to `AgentKthx`. Soul files, skill files, test docstrings, OpenRouter comment, and skill `repository` URLs similarly updated (`nova-helper/soul.json` and `nova-skills/soul.json` repo URL `VTSTech/AgentNova` → `VTSTech/AgentKthx`).
- **Restored missing `agentkthx/skills/` directory** — Accidentally lost during the R06.0 bulk rename. Restored from R05.7 baseline; all imports inside skill scripts updated `agentnova` → `agentkthx`.
- **Chat mode 2-minute startup caused by 600MB history file** — `cmd_chat` called `readline.read_history_file(~/.agentnova_history)` on every chat session start, and `readline.write_history_file()` rewrote the whole file after every prompt. Removed all history file I/O (still imports `readline` so arrow keys work in `input()`); existing `~/.agentnova_history` files can be safely deleted.
- **`--think` flag had no effect in chat mode and run mode** — `reasoning_content` display logic was added to `cmd_chat` but NOT `cmd_run`. Fixed: both now print `reasoning_content` under the answer (dimmed, indented, truncated to 200 chars) when `--think` is set AND the last FINAL_ANSWER step has `reasoning_content`.
- **ZAI and OpenRouter backends didn't surface `reasoning_content`** — `ZaiBackend._generate_with_auth()` and `OpenRouterBackend._parse_openai_response()` (separate response parsers from `OllamaBackend.generate_completions()`) were dropping `message.reasoning_content` on the floor. Both now extract it into the response dict.
- **Agent loop didn't propagate `reasoning_content` to `StepResult` in all code paths** — 5 places construct `StepResult(type=StepResultType.FINAL_ANSWER, ...)`; only ONE initially included `reasoning_content`. All 5 now include it.

### Tests

- 245/245 tests pass.

## [R06.1] - 2026-09-20

### Bug Fixes

- **Plugin loader hardcoded `agentnova.plugins` path** — `PluginManager.load()` was using `__import__("agentnova.plugins.<name>")` instead of `agentkthx.plugins.<name>`. Caused ALL plugins to fail loading with `ModuleNotFoundError: No module named 'agentnova.plugins'` — backends (bitnet, openrouter, zai) and feature plugins (acp, turboquant, test-plugin) were all silently unavailable after the R06.0 rename.
- **CLI parser prog name was `agentnova`** — `argparse.ArgumentParser(prog="agentnova")` caused the help text to display `usage: agentnova [-h]` instead of `agentkthx [-h]`.
- **Test module references** — `cli.py` had hardcoded `"agentnova.examples.00_basic_agent"` etc. strings used by `agentkthx test` command. Updated to `"agentkthx.examples.X"`.
- **`test_r046_changes.py` monkeypatch paths** — Tests were using `monkeypatch.setattr("agentnova.turbo.TURBOQUANT_STATE_FILE", ...)` — would fail with `AttributeError` since the redirect stub doesn't expose turbo state. Updated to `agentkthx.turbo.X`.

### Tests

- 245/245 existing tests pass after the fixes. All 6 plugins now load successfully on `agentkthx` startup: acp, bitnet, openrouter, test-plugin, turboquant, zai. CLI parser `--help` shows `usage: agentkthx [-h]` correctly.

## [R06.0] - 2026-09-20

### Project Rename: AgentNova → AgentKthx

Driven by name collisions in the AI agent space — multiple other projects, Instagram accounts, and PyPI packages were using "AgentNova". The new name `AgentKthx` honors the IRC-era slang "kthx" (OK, thanks). Verified unused across PyPI, GitHub, and major social platforms as of September 2026.

### Changes

- **Package rename** — PyPI `agentnova` → `agentkthx`; CLI `agentnova` → `agentkthx`; Python import `import agentnova` → `import agentkthx`; GitHub `VTSTech/AgentNova` → `VTSTech/AgentKthx`; source directory `agentnova/` → `agentkthx/`; all internal imports `from agentnova.X` → `from agentkthx.X`.
- **Backward compatibility preserved** — `agentnova` CLI binary still works (installed by `agentkthx` PyPI package as a redirect binary that calls `agentkthx` with a deprecation notice); `import agentnova` still works (emits `DeprecationWarning` and re-exports from `agentkthx`); all `AGENTNOVA_*` env vars and `~/.agentnova/`, `~/.cache/agentnova/` filesystem paths unchanged. New `agentnova-redirect/` and `localclaw-redirect/` standalone PyPI packages will be published as stubs depending on `agentkthx`.
- **`__all__` mismatch fix** — `agentkthx/__init__.py`'s `__all__` referenced `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_DEFAULT_MODEL` but they were never imported from `config`. Latent bug surfaced when the redirect stub did `from agentkthx import *`.

### Intentionally NOT Changed

- `AGENTNOVA_*` env var names; `~/.agentnova/` and `~/.cache/agentnova/` filesystem paths; soul/skill file paths (internal to the package, rename with the package); plugin manifest format; OpenResponses / OpenAI / JEV API modes; thinking controls; JEV decision envelope shape `{decision, probability, alternatives, usage, latency_ms, _jev}`.

### Tests

- 245/245 existing tests pass after the rename with zero code changes to test logic. Test file imports updated `from agentnova` → `from agentkthx`.

## [R05.8] - 2026-09-20

### Features

- **`--thinking off|auto|low|medium|high` CLI flag** — Controls model thinking behavior across all backends. `off` → `think=False` (fastest, recommended for JEV); `auto` → `think=None` (let model decide, default); `low`/`medium`/`high` → `think=True` + forward `reasoning_effort` to OpenAI-compatible thinking models (o-series, GLM-5.x, etc.).
- **`--think` CLI flag** — Boolean toggle for displaying `reasoning_content` (chain-of-thought) in CLI output. Off by default. When set, reasoning printed under each step in a dim/indented style.
- **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — New enum in `core/types.py` (`OFF`, `AUTO`, `LOW`, `MEDIUM`, `HIGH`); `parse_thinking_arg()` maps a user-facing level string (or `ThinkingLevel`) to a `(think, reasoning_effort)` tuple. Tolerant — unknown values fall back to `AUTO`.
- **`reasoning_content` capture + `StepResult.reasoning_content`** — `OllamaBackend.generate()` (both native `/api/chat` and OpenAI `/v1/chat/completions` paths) now captures `reasoning_content` (or `thinking` key for Ollama-native format). New `StepResult.reasoning_content` dataclass field carries the model's chain-of-thought per step.
- **JEV decision envelope includes `reasoning_content`** — `generate_decision()` surfaces it so `--think` works in JEV mode too.
- **Backend forwarding of `reasoning_effort`** — All three Ollama paths (native, OpenAI-compat, streaming) forward `reasoning_effort` when set; silently ignored by models that don't recognize it.

### Limitations

- `--thinking off` honored by all thinking-capable models tested (GLM-4.5+, qwen3, deepseek-r1); `--thinking low/medium/high` honored by OpenAI o-series and GLM-5.x; other models silently ignore it. Streaming mode does not yet surface reasoning in real-time.

### Tests

- 43 new tests in `tests/test_thinking_args.py` covering `ThinkingLevel` enum, `parse_thinking_arg()`, CLI `--thinking`/`--think` acceptance + combination, `Agent.__init__()` new params, `StepResult.reasoning_content` field, source-level verification that `OllamaBackend.generate()` / `generate_completions()` / `generate_decision()` reference `reasoning_content`, end-to-end CLI → Agent flow tests for both `--thinking off` and `--thinking high`.

## [R05.7] - 2026-09-20

### Features

- **JEV API Mode** — Added `--api jev` (sibling of `openre` / `openai`) — System-One decision mode that wraps any chat-capable LLM with a constrained decision prompt, returning a Jev-compatible envelope `{decision, probability, alternatives, usage}`. Works with free ZAI / OpenRouter / Ollama models — no TypeSafe API key required.
- **`generate_decision()` primitive + `_jev_call_completions()` hook** — New `BaseBackend.generate_decision(model, state, choices, ...)` for programmatic decision calls (default raises `NotImplementedError`). Per-backend override: ZAI routes through `_generate_with_auth` (Bearer auth + `ZAI_FREE_ONLY`), OpenRouter routes through `generate()` (429 retry), Ollama routes through `generate_completions()`.
- **`_maybe_jev_dispatch()` helper** — Shared dispatch hook called from `generate()` — returns a generate()-shaped dict if `api_mode == JEV`, else None so normal OPENRE/OPENAI path runs. Lets ZAI/OpenRouter/Ollama all handle JEV uniformly.
- **Tolerant JSON parsing** — Handles ` ```json ` fences, plain ` ``` ` fences, malformed JSON (fallback to raw text as decision), and `alternatives` as string lists. Constrained-choice fuzzy-matching snaps the model's decision to the canonical choice form (exact or substring match). Probabilities clamped to `[0.0, 1.0]`.

### Limitations

- Not a real Jev (emulates the API shape using any LLM; does not call TypeSafe's `api.typesafe.ai/v1/systemone` endpoint). Single-shot only (not multi-turn chat). No streaming. Tool calling disabled (decisions never call tools — `tools=None` always passed). Thinking-capable models (e.g. GLM-4.5-flash) emit billable `reasoning_content` before the JSON envelope, inflating latency (~22s for trivial classification).

### Tests

- 33 new tests in `tests/test_jev_api_mode.py` covering `ApiMode.JEV` enum, CLI `--api jev` choice, `_build_jev_messages()`, `_parse_jev_response()`, `_serialize_state()`, `_maybe_jev_dispatch()`, backend construction in JEV mode, mocked `generate_decision()` calls, `BaseBackend.generate_decision()` default raising `NotImplementedError`. Combined suite: 202/202 tests pass (33 new + 169 existing).

## [R05.6] - 2026-09-19 3:00:48 PM

### Features

- **ZAI API Technical Reference** — New `docs/ZAI_API_TECHNICAL_REFERENCE.md` for improved ZAI backend integration.
- **Default Streaming for Cloud Providers** — ZAI and OpenRouter backends now default to streaming; local providers (Ollama, BitNet, TurboQuant) default to non-streaming.
- **Configurable Max Steps** — New `--max-steps` CLI parameter (default raised 5 → 10); also configurable via `AGENTNOVA_MAX_STEPS` env var.
- **Enhanced Terminal Input** — Readline support for arrow key navigation (← → ↑ ↓) and message history browsing with persistent storage in `~/.agentkthx_history`.

### Bug Fixes

- **Max Steps Default Error** — `TypeError: 'NoneType' object cannot be interpreted as an integer` when `--max-steps` not specified; added defensive null-check in Agent constructor. Also removed duplicate `--max-steps` definition in `shared_args.py` causing `argparse.ArgumentError`.
- **Truncation Logic** — Removed debug mode truncation condition that was hiding step-by-step progress in regular chat mode.
- **Backend Variable Scoping** — Fixed `UnboundLocalError: cannot access local variable 'backend'`; resolved `NameError: name 'timeout' is not defined`; added full traceback display for unexpected errors.

### Changes

- Step summary hidden when only 1 step exists (just final answer); shown for multi-step (2+ steps). Step-by-step progress visible in regular chat mode (not just debug mode). Improved model configuration auto-detection for ZAI model families. Enhanced retry logic with exponential backoff for rate limits and server errors.

## [0.5.5] - 2026-09-18 3:21:26 PM

### Added

- **ZAI Model Accuracy Improvements** — Comprehensive `max_tokens` documentation in ZAI backend with confirmed model specifications; `FREE_ONLY` filtering for ZAI backend (glm-4.5-flash, glm-4.7-flash); intelligent tool support defaults for cloud providers (no expensive API calls needed).
- **Intelligent CLI Defaults for Cloud Providers** — Auto-populates `--num-ctx` and `--num-predict` with catalog values when not specified by user; enables streaming by default for cloud providers (OpenRouter, ZAI) for optimal UX.

### Changed

- **Cloud Provider Model Listing** — Removed redundant family column for cloud providers; improved context size formatting with proper "K" suffix (128K → 128K, not 125K); fixed cloud provider backend detection using `BackendType` enum instead of `isinstance` checks; cloud model size set to "unknown" (accurate since cloud APIs don't provide this info).
- **OpenRouter Backend Enhancement** — Now uses live API data for `max_tokens` and context length instead of static catalog; cache populated on backend initialization to ensure live data is always available; `_get_model_defaults()` checks both cache and catalog for accurate defaults.

### Fixed

- **ZAI Context Length Accuracy** — Updated ZAI catalog context lengths based on official API documentation (glm-4.5/4.5-flash: 128K → 132K for proper 128K display; glm-4.7/4.7-flash: 128K → 204800 for proper 200K display). Removed orphaned ZAI backend code from `backends/zai.py` (was duplicated in plugin system). Fixed `FREE_ONLY` filtering using `ZAI_FREE_ONLY=true` env var.
- **Cloud Provider Max Token Accuracy** — OpenRouter: `max_tokens` fallback 4096 → live API values (e.g., 32K for `poolside/laguna-xs-2.1:free`). ZAI: incorrect 131072 → model-specific limits (98304 for GLM-4.5 series, 131072 for GLM-4.6/5). Fixed cache population issue where `list_models()` wasn't called before `run()`.
- **CLI Streaming Defaults** — OpenRouter and ZAI now automatically enable streaming mode; Run and Chat commands auto-detect cloud providers and enable streaming without explicit `--stream` flag.

### Documentation

- Added cache refresh endpoint comments to cloud provider backends (OpenRouter: GET /v1/models, ZAI: GET /api/paas/v4/models — both 1-hour cache timeout). Added comprehensive ZAI documentation with context lengths, max tokens, and pricing information for all GLM models (128K, 200K, 1M variants). Documented OpenRouter live API integration with `max_completion_tokens` and `context_length` fields.

## [R05.4] - 09-11-2026 7:21:00 PM

OpenRouter Tool-Calling Fix, CLI Visibility, Security Modes & 429 Retry. Fixes a critical defect where the OpenRouter backend never sent or parsed native tool calls; adds runtime-toggleable security mode, automatic 429 retry with `Retry-After` support, a 2-line persistent terminal status footer, CLI visibility for tool-call execution, and proper error surfacing for empty responses and provider-side rate limits. This release makes the OpenRouter backend fully usable for agentic workflows with cloud models.

### Bug Fixes

- **OpenRouter Backend Never Sent or Parsed Tool Calls** — `generate()` built the request body without the `tools` field — the model hallucinated its own non-standard text format (`shellcommand\`echo ...\``) instead of using proper function-calling JSON, and every tool request was silently accepted as a final answer. Rewrote `generate()` to build the body with `tools` in OpenAI function-calling schema, send `max_tokens` (universally supported, not `max_completion_tokens`), parse `choices[0].message.tool_calls` (normalizing JSON string → dict with `_raw` fallback for malformed), and synthesize `finish_reason` when providers omit it.
- **OpenRouter HTTP Errors Not Surfaced** — `_make_api_request()` raised bare `requests.exceptions.HTTPError` with no upstream error message. Now extracts the upstream message from any 4xx/5xx response (handles `{"error": {"message": ...}}`, `{"error": "..."}`, `{"message": "..."}` shapes) and raises `RuntimeError("OpenRouter API error {status}: {message}")`. `_parse_openai_response()` now checks for a top-level `error` field *before* looking at `choices`.
- **ReAct Fallback Safety Net for Free Models** — Some `:free` / fine-tuned models reject the `tools` field at runtime with HTTP 400 ("Model does not support tools"). `generate()` now detects this in the error message and retries once *without* the `tools` field, letting the model fall back to text-format (ReAct) tool calls that the Agent's `ToolParser` can still parse from response content.
- **OpenRouter 429 Rate Limit Retry** — `_make_api_request()` now retries up to 3 times on HTTP 429, honoring the `Retry-After` header (capped at 60s). In debug mode prints `[OpenRouter] 429 rate limited (attempt 1/4): <message>. Retrying in 10s...`.
- **Empty Response Detection** — `generate()` now raises `RuntimeError("OpenRouter returned an empty response...")` when the API response has no content AND no tool_calls. CLI checks `result.final_answer` and displays `Agent Nova: (empty response)` with a hint instead of a blank line.

### Features

- **Chat Mode Status Footer** — 2-line persistent footer using a terminal scroll region (DECSTBM `Set Top and Bottom Margins`). Line 1: version (⚛️), model (🧠), prompt size (📝), context window (📦), max response tokens (💬), temperature (🌡️). Line 2: backend (🔌), session token usage (📈 ↑in ↓out), debug indicator (🐛). Footer drawn in place via save-cursor / clear-line / write / restore-cursor — no footer text ever enters scrollback history. `try/finally` resets the scroll region on every exit path. Graceful fallback: if stdout is not a TTY or terminal is <6 lines, the scroll region is skipped.
- **Security Mode Toggle** — Runtime-toggleable: `max` (default, all checks enabled) or `off` (ALL security checks disabled — model can run any command, read/write any path, fetch any URL). New `SecurityMode` type + `set_security_mode()` / `get_security_mode()` API; `sanitize_command()`, `validate_path()`, `is_safe_url()` skip checks when mode is `"off"`. New `/security` slash command + `--security max|off` CLI flag; mode displayed in `/status`.
- **CLI Tool-Call Visibility** — New `_print_agent_steps(result, debug)` helper prints a compact summary of each tool call between user prompt and final answer (`[1] tool shell {"command": "echo hi"} → hi`). Wired into both `cmd_chat` and `cmd_run`; suppressed in debug mode (already verbose); long args >120 chars and results >200 chars truncated.

### Changed

- **OpenRouter `test_tool_support()` simplified** — Always returns `ToolSupportLevel.NATIVE` without any API call (OpenRouter only exposes models that already support function calling on their underlying provider). Defensive ReAct fallback in `generate()` handles the rare runtime rejection case.
- **`_build_openai_body()` / `_parse_openai_response()`** — New helpers centralize OpenAI Chat-Completions request body construction and response parsing. `_build_openai_body()` adds optional fields (`top_p`, `top_k`, `seed`, `n`, `presence_penalty`, `frequency_penalty`, `stop`, `response_format`, `tool_choice`) only when explicitly provided. `_parse_openai_response()` handles arguments as JSON string (OpenAI spec) or object (some providers) with `_raw` fallback for malformed JSON.

### Tests

- 39 new tests in `tests/test_openrouter_backend.py` covering response parsing (native tool_calls, object args, malformed args, missing choices, provider error field, text-only response), request body construction (tools included/omitted, optional params, `max_tokens` vs `max_completion_tokens`), error-text matching for ReAct fallback, full `generate()` flow with mocked HTTP (happy path, fallback retry, unrelated-error propagation, missing finish_reason synthesis, empty response detection, whitespace-only response, empty content + tool_calls valid), `test_tool_support()` returns NATIVE without API call, `_print_agent_steps` CLI helper, security mode toggle (default/max/off, invalid rejection, injection allowed/blocked per mode).

## [R05.3] - 09-11-2026 4:22:00 PM

Documentation updates & version bump for OpenRouter plugin release (R05.2 was the implementation; this release ensures consistency across all user-facing materials).

### Changes

- **Version bump** — `pyproject.toml` 0.5.2 → 0.5.3; `README.md` title R05.2 → R05.3; CLI banner docstring R05.3; `agentkthx/__init__.py:__version__` "0.5.3".
- **Documentation enhancements** — Added OpenRouter usage examples (different model types: OpenAI, Anthropic, Google); documented all OpenRouter configuration env vars; updated backend options section; added link to `docs/CHANGELOG.md` in README.

## [R05.2] - 09-11-2026 2:37:37 PM

Adds the OpenRouter cloud backend as a first-class alternative to Ollama — 500+ models from Anthropic, OpenAI, Google, Cohere, and other providers via OpenAI Chat-Completions compatible API. Implements model discovery with 1-hour caching, free model filtering via `OPENROUTER_FREE_ONLY`, and proper error handling for rate limits and authentication.

### Features

- **OpenRouter Plugin (`plugins/openrouter/`)** — `OpenRouterPlugin` provides access to OpenRouter's API. Inherits from `OllamaBackend` to reuse OpenAI Chat-Completions logic. OpenRouter only supports Chat-Completions format (`--api openai`); validated at construction time. Uses `OPENROUTER_API_KEY` env var for Bearer token auth; sends OpenRouter-specific headers (`HTTP-Referer`, `X-Title`). `list_models()` queries `GET /models` with 1-hour cache. `OPENROUTER_FREE_ONLY` env var filters to `:free` suffix or common free model patterns. 401 errors produce clear messages directing users to check their API key. `--backend openrouter` works across all subcommands. `OpenRouterBackend` exported from `agentkthx.__init__`. New env vars: `OPENROUTER_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_DEFAULT_MODEL`, `OPENROUTER_FREE_ONLY`.
- **Default API Mode Change** — Global default API mode changed `"openre"` → `"openai"` for better cloud provider compatibility. When `--backend openrouter` is used without `--api`, the system auto-defaults to `openai`.
- **`BackendType.OPENROUTER`** — Added new enum value to properly identify OpenRouter backends; fixed footer to show "🔌 openrouter" instead of "🔌 zai".

### Bug Fixes

- **API Mode Default for OpenRouter** — OpenRouter backend required explicit `--api openai` flag, failing with confusing error when using default `--api openre`. Now automatically defaults to OpenAI mode; `OPENROUTER_API_KEY` is the only requirement for basic usage.
- **Backend Footer Display** — `backend_type` property returned `BackendType.ZAI` instead of `BackendType.OPENROUTER`. Corrected.
- **Response Parsing** — Custom `generate()` returned raw OpenRouter API response, causing empty content display. Now parses `choices[0].message.content` and returns dict with `content`, `tool_calls`, `usage`, `raw` matching `OllamaBackend.generate_completions()` output format.
- **Debug Output Cleanup** — Removed all `print(f"[DEBUG] ...")` statements from OpenRouter backend methods.

## [R05.1] - 04-27-2026 8:07:20 PM

### Fixed

- **Missing import in `cli.py`** — Added missing import that was causing a runtime error. (Single-line fix; no other changes in this release.)

## [R05.0] - 04-15-2026 1:54:57 PM

Largest architectural change since R04.0. Introduces a full plugin system with manifest-based discovery, topological dependency resolution, and lazy loading. Backends previously compiled into the framework (BitNet, ZAI, ACP, TurboQuant) are now pluggable, reducing the native surface to just Ollama and llama-server. Adds Ctrl+C cancellation at three layers. Consolidates all documentation under `/docs/` and publishes a Plugin Specification. Version bumped to 0.5.0.

### Features

- **Plugin System (`plugins/_loader.py`, `plugins/__init__.py`)** — `PluginManager` central singleton for plugin discovery, loading, dependency resolution, backend registration, CLI extension, and config aggregation. Manifest-based discovery (each plugin ships a `plugin.json` with name, version, type, entrypoint, dependencies, config defaults, capabilities). Topological dependency resolution (Kahn's algorithm; missing hard deps cause skip with warning; circular deps detected and rejected). Lazy loading — `_ensure_plugin()` in `backends/__init__.py` resolves backend names to plugins via manifest scan (e.g. `test-backend` → `test-plugin`), then loads only the required plugin. Plugin types: `backend`, `feature`, `tools` (future), `hook` (future). Lifecycle: `discover()` → `load(name)` → `register(manager)` → `[active]` → `unregister()` → `unload()`.
- **Plugins: BitNet, ZAI, ACP, TurboQuant, Test-Plugin** — BitNet (269 lines extracted), ZAI (835 lines), ACP (2397 lines), TurboQuant (694 lines) each extracted to self-contained plugins with their own `plugin.json`. Test-Plugin validates every plugin system feature (discovery, backend registration, CLI command, config defaults, dependency resolution, lifecycle). Config defaults (`BITNET_BASE_URL`, `ZAI_BASE_URL`, `ACP_URL`, `TURBOQUANT_SERVER_PATH`, etc.) moved from `config.py` to each plugin's `plugin.json`.
- **Ctrl+C Cancellation (3 layers)** — Backend HTTP call: `KeyboardInterrupt` during `backend.generate()` returns a cancelled response dict with `_cancelled: True` and empty content, preventing connection leaks. Tool execution: `KeyboardInterrupt` during `_execute_tool()` marks the tool call as `FAILED`, appends a cancellation step, breaks the agent step loop; in streaming mode, yields `RESPONSE_FAILED` SSE event. Agent step loop: cancelled responses detected via `_cancelled` flag, appending an error step and breaking the loop. `_execute_tool()` now re-raises `KeyboardInterrupt` (was previously caught by `except Exception`).
- **Documentation restructure** — Moved `ARCH.md`, `CHANGELOG.md`, `CREDITS.md`, `audit.md`, `brief.md`, `TESTS.md` from repository root to `docs/` directory. New `docs/PLUGIN_SPEC.md` (462 lines, Plugin Specification v0.1) covering manifest format, plugin types, entrypoint contract, lifecycle, dependency resolution, backend registration API, CLI extension API, config defaults API, and a complete example plugin walkthrough.

### Changes

- **Backend Registry Simplified (`backends/__init__.py`)** — Native registry reduced to 2 backends (`ollama`, `llama-server`). `get_backend()` checks native registry first, then delegates to `_ensure_plugin()` + `PluginManager.get_backend_class()` for plugin backends. Removed hardcoded `BitNetBackend`, `ZaiBackend` imports.
- **Config Module Decentralized (`config.py`)** — Plugin-owned config defaults moved to manifests; module-level variables kept as thin wrappers reading from environment variables with plugin-defined defaults as fallbacks (backward compatible).
- **Public API Exports (`__init__.py`)** — Removed `BitNetBackend`, `ZaiBackend` exports (now via plugin system); added `LlamaServerBackend`, `get_backend_choices`. `ACPPlugin` import path updated `from .acp_plugin import ACPPlugin` → `from .plugins.acp.acp_plugin import ACPPlugin` (graceful import with fallback). Version bumped 0.4.8 → 0.5.0.
- **CLI Plugin Integration (`cli.py`, `shared_args.py`)** — `main()` discovers plugin commands from manifests, adds them as argparse subparsers with `* [plugin]` help text. `create_parser()` saves the `_SubParsersAction` as `parser._subparsers_action` for later dynamic subparser addition. `get_backend_choices()` merges native backends with plugin-provided values. Warning on plugin discovery failure (was silent `except Exception: pass`).
- **`pyproject.toml`** — Added `"plugins/*/plugin.json"` to `package-data` glob so manifests are included in the wheel distribution.

### Bug Fixes

- **Plugin CLI Subparser Registration** — `parser._subparsers` is an `_ArgumentGroup`, not the `_SubParsersAction` that holds `.choices`. Caused `AttributeError` silently swallowed by `except Exception: pass`. Fix: stash the return value of `parser.add_subparsers()` as `parser._subparsers_action`. Help text for existing subparsers lives in `_choices_actions`, not on the `ArgumentParser` object. Replaced silent `except Exception: pass` with `except Exception as e: print(..., file=sys.stderr)`.
- **Backend-to-Plugin Name Resolution** — `get_backend("test-backend")` tried to load a plugin directory named `test-backend`, which didn't exist (the plugin is named `test-plugin`). Backend name and plugin directory name are independent. Fix: added `PluginManager.find_plugin_for_backend(name)` which scans discovered manifests' `provides.backends` to reverse-map backend name → plugin directory name.
