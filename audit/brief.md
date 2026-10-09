# Codebase Intelligence Brief: AgentKthx

> Regenerated: 2026-10-10 (R07.31 tree, commit `986428a` — two feature releases since the last register update: **R07.31** shipped the Stable Diffusion image-generation backend via sd.cpp as the 13th backend plugin and the FIRST backend that generates images instead of text — `StableDiffusionBackend(OpenAICompatibleBackend)`, `is_cloud=False`, externally-managed sd-server (Ollama pattern: the framework never owns the process), every chat turn becomes ONE image via `/v1/images/generations` → pre-decode 32MB-capped base64 PNG → `AGENTKTHX_ARTIFACTS_DIR`, capabilities-first discovery surfaces the REAL loaded weights (`sd_turbo`) over the hardcoded `sd-cpp-local` pseudo id, `--model` advisory (no model field on the wire), models-table crash fix (defensive `get_model_runtime_context`), live-verified end-to-end on Colab CPU (first image 2026-10-10); plus in-tree R07.32 follow-ups: `--max-steps` → diffusion `sample_steps` remap (sd-only, validated 1..100, rides the wire via the source-pinned `sd_cpp_extra_args` prompt block), "Sample Steps" session-header relabel, capabilities cache warm. **R07.30** shipped the DuckDuckGo AI Chat backend as the 12th cloud backend — the first keyless/anonymous one and the only BaseBackend-direct subclass — scaffolded and then protocol-REWRITTEN to plugin v0.2.0 in the same window after DDG retired the x-vqd-4 token AND the 2025 catalog: x-vqd-hash-1 JS-challenge proofs (bundled Node solver `ddg_vqd.js`, DDG_SOLVE_UA lockstep), per-turn challenge rotation, role-based SSE with legacy fallback, durableStream RSA-2048 JWK (`ddg_durable.js`), system prompt DROPPED at the wire (live jailbreak-lecture finding), canUseTools=false (REACT), 8-model reseeded catalog with forward-resolving legacy aliases, anti-bot contract v3 (min cookies + client hints + 418 teapot ladder + 3s /chat pacing), FEAT-10 CLOSED (true `generate_stream()`). **2026-10-10 re-audit of both releases: all 18 carried-forward OPEN findings re-verified; 10 new findings registered — SEC-21 (Medium: the synth proof mode executes server-supplied challenge JS in a Node subprocess that inherits the ENTIRE parent environment — sibling-provider API keys exposed to remote code on a hostile duck.ai response; env-allowlist fix is cheap and non-breaking) + ROB-45..51 + MAINT-32/33 (all Low); no closures; suite 3,498 passed / 20 skipped; register 146 findings / 28 OPEN.** Prior entry: R07.29 — SiliconFlow cloud backend scaffolded as the 11th plugin, the first built on the post-R07.28 hardened CloudBackend patterns (shared transport inherited untouched, ROB-42-hardened list_models, MAINT-28 hook surface only, balance-vs-TPM 429 classifier, 30-model live-verified seed); 2 findings registered (FEAT-09, TEST-12). Prior entry: R07.28 closure pass — MAINT-28 + ROB-42 CLOSED in two batches (shared CloudBackend template methods + lifted request loops; nvidia.py 1,070 → 807 LOC, cloudflare.py 1,461 → 1,213 LOC) + batch 3 quick wins (ROB-43/44, MAINT-29/30); MAINT-31 registered. Prior entry: R07.26–R07.27 re-audit — NVIDIA NIM + Cloudflare Workers AI as backends 9 & 10; six findings (ROB-42/43/44, MAINT-28/29/30). Prior entries: R07.24 closure super-batch (13 CLOSED + 2 WONTFIX), R07.25 (ROB-02/06/15 CLOSED, SEC-09 WONTFIX, TEST-09 CLOSED, backend support tiers introduced) | Auditor: Super-Z (GLM) via `codebase-audit` v0.2.0 | Commit: `986428a` (R07.31 + re-audit in the working tree; PyPI 0.7.31 latest)

---

## Project Identity

| Field | Value |
|-------|-------|
| **Purpose** | A minimal, hackable, stdlib-only agentic framework + CLI for autonomous LLM agents with local and cloud backends, tool calling, streaming, plugins, souls, and skills |
| **Tech Stack** | Python >= 3.12, **zero runtime dependencies** (`dependencies = []` — stdlib `urllib`/`json`/`sqlite3`/`ast`/`subprocess`/`socket`/`ipaddress`/`threading`/`weakref` only); dev: pytest/black/ruff. **DuckDuckGo backend additionally needs Node >= 18 on PATH** (challenge solver + RSA keygen subprocesses, no npm packages); Stable Diffusion needs an externally-run sd-server binary |
| **Entry Point** | Console script `agentkthx` → `agentkthx.cli:main` → `cli/main.py:main()` → `cli/parser.py` dispatch → `cli/commands/<cmd>.py` |
| **Build/Run** | `pip install agentkthx` (PyPI 0.7.31 latest, matching the tree) or `pip install -e .`; `agentkthx chat`, `agentkthx turbo start <model>`, `agentkthx models`, `agentkthx souls`, `agentkthx auth`, `agentkthx mcp <init|list|probe|search|install|uninstall>`, `agentkthx version`, ... (96+ CLI flags across 18 subcommands). Image generation: `agentkthx run "a prompt" --backend sd` (sd-server must already be listening) |
| **Test Command** | `python -m pytest tests/ -q` → **3,498 passed / 20 skipped in ~25s** (103 test files, ~51,800 LOC); CI matrix Python 3.12/3.13 in `.github/workflows/ci.yml` + parallel coverage job + **required `lint` job** (`ruff check` + `black --check`, pinned versions — verified clean at this commit). Live-gated contract tests (`tests/test_mcp_live_contract.py`) skip without `AGENTKTHX_LIVE_TESTS=1` |

---

## Architecture Map

```
agentkthx/agent.py            → Agent class — 5-mixin composition (~1,112 LOC; _generate_with_retry records
                                per-response TPS: _gen_start/_gen_end/_gen_tokens_out)
agentkthx/agent_mode.py       → AgentMode + TaskPlan/Step/Action with rollback (822 LOC, untested — TEST-04)
agentkthx/orchestrator.py     → Multi-agent orchestrator (sequential + parallel + LLM-router, ~461 LOC;
                                ROB-02 FIRST_COMPLETED + cancel_futures)
agentkthx/core/               → 20 files incl. __init__, ~10,000 LOC
  ├─ agentic_loop.py          → Unified loop body; Ctrl+C → state.terminated (ROB-01)
  ├─ streaming.py             → SSE streaming + OpenResponses event generator (~1,083 LOC; R07.28 shared
  │                             transport lifted the per-backend loops; StreamAccumulator/StreamRenderer split)
  ├─ environment.py           → stdlib-only host probe → `# Host Environment` system-prompt section (225 LOC)
  ├─ compaction.py            → Context-window compaction mixin (~200 LOC)
  ├─ tool_parse.py            → ReAct / native-JSON / XML parser (661 LOC; markdown-bold-tolerant regexes)
  ├─ tool_execution.py        → Tool dispatch + parallel independent tool-call batches (FEAT-02)
  ├─ agent_setup.py           → Agent.__init__ (~684 LOC; num_batch/repeat_* params + ARCH-05 kwargs list)
  ├─ openresponses.py         → OpenResponses spec: Response state machine, items, SSE events (~1,050 LOC)
  ├─ api_resilience.py        → Transient-vs-permanent classifier + backoff w/ Retry-After + jitter
  ├─ error_recovery.py        → ErrorRecoveryTracker, is_error_result (~920 LOC)
  ├─ helpers.py               → Security primitives (validate_path, sanitize_command, is_safe_url w/ bounded
  │                             DNS, sanitize_tool_output) + normalize_args (~1,384 LOC)
  ├─ safe_eval.py             → AST-walking eval replacement (~263 LOC)
  ├─ memory.py                → Sliding-window + opt-in token-tier pruning
  ├─ persistent_memory.py     → SQLite PersistentMemory(Memory); per-DB-path write locks; RLock transactions
  ├─ model_family_config.py   → Per-family stop tokens / temperature / no-think directives (~480 LOC)
  ├─ types.py                 → BackendType enum — now 13 backend values incl. DUCKDUCKGO (R07.30) +
  │                             STABLE_DIFFUSION (R07.31; the DDG enum comment is STALE — MAINT-33) +
  │                             ToolSupportLevel (effective() normalizes NONE→REACT) + ThinkingSupport
  ├─ tool_cache.py            → tool_support.json cache; ONE plain-key namespace + `thinking:<model>` keys
agentkthx/cli/                → 26-file CLI package
agentkthx/cli/commands/       → 16 command modules: chat (1,946 LOC — MAINT-01, grew +213 with the R07.31
                                "Sample Steps" relabel), souls, test, config (R07.31: SD_BASE_URL +
                                AGENTKTHX_ARTIFACTS_DIR + DUCKDUCKGO_* env reference rows), models (R07.31:
                                crash fix for non-cloud OpenAICompat rows — get_model_max_context → None
                                renders "?"), agent, tools, soul, turbo, ...
agentkthx/cli/picker.py       → ArrowMenu — termios cbreak POSIX / msvcrt Windows / numbered fallback (397 LOC)
agentkthx/cli/agent_factory.py→ THE wiring file (~850 LOC): _build_agent (tool-support auto-detection),
                                _detect_weight_quant, _get_catalog_defaults ladder, apply_model_switch.
                                R07.31: SD discovery branch (:187-199 — temp backend list_models, model =
                                capabilities stem else "sd-cpp-local"), sample_steps pass-through (:215-216),
                                get_model_info cache warm (:227-233). Landmine: the three sequential probes
                                stack ~30s on a hung tunnel (ROB-48)
agentkthx/cli/headers.py      → Session header; R07.31: "Sample Steps:" relabel for the sd backend
agentkthx/cli/footer.py       → Shared footer formatters (227 LOC; fmt_token_size, 🔧/⚡/🧊 segments)
agentkthx/backends/           → cloud_base.py (CloudBackend: shared _make_api_request/_iter_sse_lines/
                                generate_stream/_jev_call_completions + hook surface) + openai_compat
                                (R07.31: defensive get_model_runtime_context via getattr) + ollama +
                                llama_server + bitnet + ollama_registry + base
agentkthx/shared_args.py      → 563 LOC; --num-batch/--repeat-* flags, _parse_token_size, SharedConfig.
                                R07.31/32: --max-steps help text now documents the sd remap (the VALUE
                                is remapped in agent_factory, not here)
agentkthx/plugins/            → 16 plugins: acp, bitnet, cloudflare, duckduckgo (R07.30 — keyless challenge
                                protocol, BaseBackend-direct), gemini, huggingface, mistral, nvidia, openai,
                                openrouter, orcarouter, pollinations, siliconflow, stablediffusion (R07.31 —
                                sd.cpp images), turboquant, zai (+ test-plugin fixture)
  ├─ _loader.py               → PluginManager, manifest v0.2, Kahn-topo loader, sha256 pins (opt-in — SEC-13),
  │                             transactional registration (~1,530 LOC)
  ├─ duckduckgo/duckduckgo.py → 1,454 LOC: x-vqd-hash-1 proof lifecycle (_fetch_challenge → _solve_challenge
  │                             [node subprocess, 45s] → _build_proof_header), per-turn rotation,
  │                             role-based SSE parser (_classify_sse_payload — module-level, testable),
  │                             _strip_system_messages (system DROPPED), _pace_chat (class-level, no lock —
  │                             ROB-46), 418 teapot ladder, buffered + incremental generation, seed-backed
  │                             offline catalog. LANDMINES: SEC-21 (subprocess inherits FULL parent env),
  │                             ROB-45 (TimeoutExpired/JSONDecodeError leak raw), ROB-47 (mid-stream retry
  │                             duplicates text), MAINT-32 (PROOF_MODE=capture is a no-op; ddg_capture.js
  │                             doesn't exist)
  ├─ duckduckgo/ddg_vqd.js    → 276 LOC Node challenge solver: installs browser globals on globalThis
  │                             (window IS globalThis — the pxjzr probe demands it), then `new Function`
  │                             evals the server challenge — require/process remain reachable (SEC-21)
  ├─ duckduckgo/ddg_durable.js→ 40 LOC: RSA-2048 JWK mint (generateKeyPairSync)
  ├─ stablediffusion/backend.py → 621 LOC: _flatten_messages (system suppressed), /v1/images/generations,
  │                             _decode_image (pre-decode 32MB cap), _write_artifact (second-resolution
  │                             stamp + per-instance counter — collision edge, ROB-50), capabilities-first
  │                             list_models, sample_steps property (1..100 fail-fast — ROB-49's raw
  │                             ValueError), sd_cpp_extra_args prompt encoding (caller-supplied wins)
  └─ zai/ + orcarouter/       → CloudBackend-based; zai's test_tool_support is the hardened probe
agentkthx/skills/             → 4 bundled skills (codebase-audit, crypto-signals, skill-creator, test-harness) + loader.py
agentkthx/soul/               → Soul Spec v0.5 persona packages: loader.py, types.py
agentkthx/souls/              → 3 bundled souls: kthx-helper, kthx-skills, kthx-trading
agentkthx/tools/              → builtins.py (shell timeout clamp; _SSRFSafeRedirectHandler 5-hop budget),
                                registry.py, sandboxed_repl.py (521 LOC)
agentkthx/update_check.py     → Live PyPI + GitHub check on EVERY CLI invocation (intentional, ROB-05 WONTFIX;
                                opt out AGENTKTHX_NO_UPDATE_CHECK=1)
agentkthx/config.py           → Env-var-derived singletons; R07.30/31: SD_BASE_URL + AGENTKTHX_ARTIFACTS_DIR +
                                DUCKDUCKGO_BASE_URL/USER_AGENT/DEFAULT_MODEL (the DDG block documents the
                                challenge-proof "authentication" model)
agentkthx/mcp/                → MCP client package over stdio JSON-RPC 2.0 — config/transport/client/manager/
                                registry/cache; zero new runtime deps
audit/                        → brief + audit.md (28 OPEN) + deltas.md (118 archived, incl. the R07.30/R07.31
                                feature deltas) + split/verify/dash tooling
docs/                         → ARCH.md, USAGE.md, PLUGIN_SPEC.md(+v0.2), CHANGELOG.md (R07.31 + R07.30
                                sections), SECURITY.md + CONTRIBUTING.md, mcp/ROADMAP.md, TESTS.md,
                                SUPPORT.md (tier tables), docs/api/*_API_TECHNICAL_REFERENCE.md (11 backends —
                                DUCKDUCKGO added R07.30) + STABLE_DIFFUSION_BACKEND_PLAN.md (§8 = follow-ups)
tests/                        → 103 files, ~51,800 LOC, 3,498 tests — all mocked unit tests, no integration tier
                                (TEST-01); R07.30/31: test_duckduckgo_backend.py (99 tests, 1,416 LOC),
                                test_stablediffusion_backend.py (35 tests, 915 LOC), +2 models-table
                                regression tests in test_get_model_max_context.py
scripts/                      → probe_*.sh family, probe_duckduckgo.py (R07.30: drives the plugin itself —
                                TEST-13's automation; --live mode), diagnose_ollama.sh, bump-version.sh
patches/                      → llama.cpp turboquant patches + standalone .py applier
schemas/v0.2/                 → plugin.schema.json (declared but NOT validated by code — ad-hoc dict-shape checks)
```

### Skip List

- `__pycache__/`, `.git/`, `*.egg-info/`, `build/`, `dist/`
- `agentnova-redirect/` and `localclaw-redirect/` — thin shims for backward-compat package names
- `agentkthx/plugins/test-plugin/` — fixture for plugin spec tests
- `agentkthx/examples/` — 11 demo scripts (not run by pytest; do NOT import shared_args — they define their own args)
- `AgentKthx.ipynb` — root-level notebook, NOT skipped anymore for SD: the R07.31 notebook cells are the documented sd-server rollout vehicle (download/compile/model/start)
- `agentkthx/core/prompts.py` — dead-code-but-kept `_build_tool_section` duplicate

---

## Critical Files Index

The most important files. Touch these for almost any meaningful change.

| File | Purpose | Why It Matters |
|------|---------|----------------|
| `agentkthx/plugins/duckduckgo/duckduckgo.py` (1,454 LOC, NEW R07.30) | `DuckDuckGoBackend(BaseBackend)` — the only cloud backend NOT built on CloudBackend (the /duckchat/v1 protocol shares nothing with OpenAI-compat). Owns the x-vqd-hash-1 proof lifecycle (`_fetch_challenge` → node `_solve_challenge` → `_build_proof_header`), per-turn rotation via the /chat response header, the 418 teapot ladder (3 retries, duration floor 250ms, ladder-challenge preferred), `_strip_system_messages` (system prompt NEVER forwarded — live jailbreak-lecture finding, VTSTech R07.30 decision), role-based SSE parsing with legacy-action fallback, `_pace_chat` (class-level 3s /chat interval), buffered `generate()` + TRUE incremental `generate_stream()` (FEAT-10), seed-backed offline catalog + `fetch_capabilities()`. | Every DDG landmine lives here: **SEC-21** (`_solve_challenge` spawns node with `env = dict(os.environ)` and `ddg_vqd.js` evals the server challenge via `new Function` — sibling API keys ride into a process running remote code; the env-allowlist fix is the top near-term item), **ROB-45** (TimeoutExpired/solver-JSON errors fire OUTSIDE the retry loop's try — raw tracebacks), **ROB-46** (class-level pacing + `_durable` lazy mint, no locks), **ROB-47** (mid-stream retry appends the restarted turn after already-yielded deltas), **MAINT-32** (`DUCKDUCKGO_PROOF_MODE=capture` is a silent no-op; `_CAPTURE_JS` names a `ddg_capture.js` that is not in the repo). Node >= 18 is a hard runtime dep — `_require_node` fails fast with remediation. Error taxonomy (401/403/404/418/429/5xx + SSE ERR_CHALLENGE/ERR_CONVERSATION_LIMIT) is thorough — mirror it, don't bypass it. |
| `agentkthx/plugins/stablediffusion/backend.py` (621 LOC, NEW R07.31) | `StableDiffusionBackend(OpenAICompatibleBackend)` — the first image backend. `_flatten_messages` (system suppressed, DDG precedent) → `/v1/images/generations` (`{"prompt","n","size","output_format"}` — NO model field) → `_decode_image` (32MB pre-decode cap) → `_write_artifact` (`AGENTKTHX_ARTIFACTS_DIR`, backend-generated filename). Capabilities-first discovery (real weights stem over the `sd-cpp-local` pseudo id); `get_model_max_context`/`get_model_runtime_context` → None (pixel limits are not token context — the models-table crash fix); `sample_steps` property (1..100 fail-fast) + `sample_steps_display()` (never network IO). | **ROB-48**: `_build_agent` constructs the backend TWICE (temp `list_models()` + real `get_model_info()` warm) — up to ~30s of stacked 10s probes on a hung tunnel (reuse the temp instance's warm cache instead). **ROB-49**: out-of-range `--max-steps` raises a raw constructor ValueError — nothing catches it (clean argparse error wanted, ROB-36 class). **ROB-50**: artifact filename = second-resolution stamp + PER-INSTANCE counter — model-switch rebuilds or same-second processes silently overwrite (`write_bytes`). The `sd_cpp_extra_args` JSON block appended for sample_steps is source-pinned (routes_openai.cpp:11 at 228c707) — a caller-supplied block wins, never double-append; the block MUST stay on ONE line (the server's extraction regex `.` doesn't match newlines). |
| `agentkthx/cli/agent_factory.py` (~850 LOC, +36 R07.31/32) | Wires CLI args → `Agent`. `_build_agent` (tool-support auto-detection :280-347), `_detect_weight_quant`, `_get_catalog_defaults` ladder → local/remote branches, `apply_model_switch`, `_register_model_switch_callback`. R07.31/32 additions: SD discovery branch (:187-199 — temp backend `list_models()`, `model = capabilities stem else "sd-cpp-local"`, never inherit `config.default_model`), `sample_steps` pass-through for sd (:215-216), `get_model_info(model)` cache warm (:227-233). | Touched by BOTH chat startup paths and the model-switch callback — changes here affect every backend launch. The SD branch is the newest instance of the sequential-probe family (landmine #7 + ROB-48): on a hung endpoint the three probes stack ~30s. `_detect_weight_quant` still has silent `except Exception: pass` ×2 (:87, :98). `--max-steps` is REMAPPED to sample_steps for sd only (:215-216) — every other backend keeps the reasoning-loop semantics; validation lives in the backend setter (see ROB-49 for the error-surface gap). |
| `agentkthx/shared_args.py` (563 LOC) | Shared flag definitions for chat/run/agent (`add_agent_args`), example scripts (`add_shared_args`), `SharedConfig` dataclass, `parse_shared_args`, `_env_int`/`_env_float`, `_parse_token_size`. R07.32: `--max-steps` help documents the sd remap. | When adding flags here, wire them in BOTH `add_agent_args` AND `add_shared_args` + `parse_shared_args` + the `test` subcommand parser, and mirror the `_explicit` pin pattern. Use `is not None` (not `or`) — the ROB-35 lesson. `_parse_token_size` has the `math.isfinite` guard (ROB-36 closed). |
| `agentkthx/backends/llama_server.py` (~500 LOC) | `LlamaServerBackend(OllamaBackend)` — user-facing name "turboquant"; `/completion` (OpenRE) + OpenAI-compat + streaming paths; BitNet mode flag. | The kwargs-parity loop (R07.18) forwards every non-`_already_set`, non-`_agent_internal` kwarg verbatim. `_agent_internal` is duplicated across `_generate_completion` (:443) + `_stream_completion` (:558) — a new agent-internal kwarg MUST be added to BOTH or it leaks (landmine #19). BitNet's `repeat_penalty=1.3` is a DEFAULT, not a hardcode. |
| `agentkthx/core/agent_setup.py` (~684 LOC) | `AgentSetupMixin.__init__` — soul loading, memory wiring, system-prompt assembly, per-request param storage. | New constructor params need the ARCH-05 kwargs fail-fast list updated. `_use_native_tools` gates ALL `_is_comp_mode` tool-prompt decisions. NOTE for DDG/SD: the assembled ReAct system prompt is NEVER TRANSMITTED on those backends (both suppress system at the wire — ROB-51: tools silently cannot engage there). |
| `agentkthx/cli/commands/chat.py` (1,946 LOC, +213 R07.31/32) | `cmd_chat` — the interactive REPL (MAINT-01: single function, 30+ nested closures, no slash-command dispatcher). | R07.31/32: the /debug branch now shows "Sample steps" (from `agent.backend.sample_steps_display()`) instead of "Max steps" when the backend is STABLE_DIFFUSION (function-local `from ...core.types import BackendType` import). `/param` values land on agent attrs — they do NOT go through SharedConfig. The inline if/elif slash-command chain (MAINT-01/MAINT-27) remains the standing structural debt. |
| `agentkthx/cli/footer.py` (227 LOC) | Shared footer text builders: `footer_line1`, `footer_line2`, `fmt_tok`, `fmt_token_size`, `_fmt_temp`. | Pure formatters — no I/O; all agent attr reads use `getattr` defaults. |
| `agentkthx/agent.py` (~1,112 LOC) | `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. | `_generate_with_retry` records the TPS trio around EVERY generate_fn() call (the single chokepoint for both paths). `_generate` forwards `num_batch`/`repeat_penalty`/`repeat_last_n` into backend_kwargs when not None. `add_tool` is a deprecated alias (warns once since R07.07) — use `register_tool` mid-session. |
| `agentkthx/core/helpers.py` (~1,384 LOC) | Security primitives + `normalize_args` + calc extraction. `validate_path`, `sanitize_command`, `is_safe_url` (bounded DNS), `sanitize_tool_output`. | Imported by 18+ modules — blast radius for any security change is huge. `sanitize_tool_output` wraps EVERY tool result (8KB truncation + secret redaction + ANSI strip). MAINT-03 (strategy-5 removal) closed R07.24; ROB-09 (abspath not realpath) still open. |
| `agentkthx/core/agentic_loop.py` (~800 LOC) | `_run_loop_iteration` — unified loop body: Response state machine, tool dispatch, error recovery, finish_reason handling. | Both paths funnel through `_generate_with_retry` (:264). `_process_tool_result` wraps every tool result via `sanitize_tool_output` BEFORE memory. FEAT-02 parallel batches commit in ORIGINAL call order; `AGENTKTHX_PARALLEL_TOOLS=0` escape hatch. |
| `agentkthx/plugins/_loader.py` (~1,530 LOC) | `PluginManager` singleton, manifest v0.2 parser, Kahn topo-sort dependency loader, hook dispatch, external plugin import. | Transactional registration (LIFO undo) + sha256 pins fail-closed WHEN present but opt-in (SEC-13). The two new plugins load through this — their manifests pin nothing (self-authored, in-tree). |

### Additional files of note

| File | Why It Matters |
|------|----------------|
| `agentkthx/plugins/duckduckgo/ddg_vqd.js` (276 LOC) | The Node challenge solver: installs browser globals ON `globalThis` (window IS globalThis — the pxjzr probe demands `(function(){return this;}()) === window`), emulates DOM/navigator/computed-style probe surface, then `new Function('return (' + js + ');')()` evals the server challenge. `require`/`process` remain reachable from challenge code — the SEC-21 crux. `--report` mode traces API accesses to stderr (reverse-engineering tool). DDG_SOLVE_UA env feeds `navigator.userAgent` — MUST match the request UA or the proof invalidates. |
| `agentkthx/cli/commands/models.py` | The R07.31 crash fix: SD was the first non-cloud `OpenAICompatibleBackend` to reach the models table — `cmd_models` calls `get_model_max_context()`/`get_model_runtime_context()` unconditionally; `openai_compat` now delegates defensively and SD returns None → "?" cells. Quant/Name/Size columns per R07.18/19. Cloud providers skip Size/Quant/Family. |
| `agentkthx/core/tool_parse.py` (661 LOC) | `ToolParser.parse(text)` — native JSON → ReAct → XML; ReAct keyword regexes tolerate markdown bold. Unchanged by R07.30/31. |
| `agentkthx/backends/openai_compat.py` | R07.31: `get_model_runtime_context` delegates via `getattr(self, "get_model_max_context", None)` — subclasses without context info yield None (the SD crash fix, +2 regression tests). Carries the cloud thinking name-heuristics (model-segment-only matching). |
| `agentkthx/tools/builtins.py` (~1,363 LOC) | `shell()` clamps model-supplied timeout; `_SSRFSafeRedirectHandler` (5-hop budget); `BUILTIN_REGISTRY` todo store is a module-level singleton (landmine #16). |
| `agentkthx/core/persistent_memory.py` | Per-DB-path write locks; `_transaction()` single-lock single-commit (ROB-15 closed). `0o600`/`0o700` perms. |
| `agentkthx/plugins/pollinations/pollinations.py` | The only keyless backend until DDG. Open: ROB-31 (entitlement-blind fallback ranking), FEAT-08, TEST-10. |
| `scripts/probe_duckduckgo.py` (131 LOC, NEW R07.30) | Drives the PLUGIN BACKEND itself from an unrestricted network — TEST-13's automation: proof check → capabilities → per-model sweep → seed emit (`--json`). `--live` mode runs the end-to-end turn that would close TEST-13's remaining item. Requires node on PATH. |
| `scripts/diagnose_ollama.sh` (839 LOC) | Standalone Ollama model health checker. Exit codes 0/1/2/3 = healthy/warning/broken/usage. |
| `agentkthx/mcp/registry.py` + `cache.py` | Stdlib-only live MCP search (npm + GitHub) + TTL cache at `~/.agentkthx/mcp_cache.json`. `search_all` degrades gracefully (ROB-41 closed: `search_all_with_errors` + plain-mode network-error hint). |
| `docs/STABLE_DIFFUSION_BACKEND_PLAN.md` | The SD design doc (D1–D5 rollout) — §8 holds the registered follow-ups (sd_cpp_extra_args PINNED at 228c707 / multi-model pool LRU / width-height CLI flags / img2img). |

---

## Request / Execution Lifecycle

```
1. `agentkthx chat` ──────────────────────────────────────────────────────────
   └─ cli/__main__.py → cli/main.py:main()
       ├─ get_plugin_manager().load_all()  → plugins/_loader.py (Kahn topo sort;
       │     sha256 pins verified BEFORE exec_module when present; transactional
       │     registration rolls back partial loads)
       ├─ _run_update_check() → 3 sequential HTTPS requests unless
       │     AGENTKTHX_NO_UPDATE_CHECK=1 (ROB-05 WONTFIX — intentional)
       └─ dispatch → cli/commands/chat.py:cmd_chat

2. cmd_chat → _build_agent (cli/agent_factory.py)  ─────────────────────────
   ├─ args: --num-ctx/--num-predict parsed via _parse_token_size (128k → 131072)
   ├─ backend = get_backend(name)  ("turboquant" primary; "sd"/"stable-diffusion"
   │     and "ddg"/"duckduckgo" are plugin-registered aliases)
   ├─ SD branch (R07.31): NO --model → temp_backend.list_models() → model =
   │     capabilities stem ("sd_turbo") else "sd-cpp-local"; real backend built;
   │     get_model_info(model) warms the capabilities cache. ROB-48: these are
   │     2-3 sequential 10s probes on a hung endpoint
   ├─ TOOL-SUPPORT AUTO-DETECTION (non-cloud + tools):
   │     NATIVE → keep native · REACT/UNTESTED → force_react=True · NONE → REACT
   │     · --force-react tri-state wins. NOTE (ROB-51): on ddg/sd the assembled
   │     ReAct system prompt is DROPPED at the wire — tools cannot engage
   ├─ CATALOG LADDER for num_ctx/num_predict:
   │     local  → TurboState.load() → Ollama GGUF context_length → {}
   │     remote → _probe_remote_catalog: list_models() exact-name-else-first;
   │               num_predict = ctx // 32
   └─ Agent(model, tools, backend, force_react=effective, ...) → AgentSetupMixin.__init__
         ├─ system prompt: _use_native_tools ? native-tools text : ReAct text
         ├─ memory: Memory or PersistentMemory (sqlite, 0o600)
         └─ TPS trio + _explicit pin flags stashed on the agent

3. REPL loop (chat.py) ──────────────────────────────────────────────────────
   ├─ prompt: Primary User named prompt; slash commands = inline if/elif chain
   ├─ /param ... → agent attr + _explicit pin; /model → apply_model_switch
   └─ agent.run(user_input, stream=True)

4. agent.run() → _run_core → _run_loop_iteration (agentic_loop.py) ─────────
   for step in range(max_steps):
     ├─ _generate_with_retry (agent.py) — records TPS trio per attempt
     ├─ parse: native tool_calls OR ReAct 4-level fallback chain
     ├─ tools: FEAT-02 parallel batches (results commit in call order)
     └─ every result → sanitize_tool_output → memory

5. backend.generate — per-backend wire shapes ──────────────────────────────
   ├─ ollama/llama_server(turboquant)/bitnet: kwargs forwarded (R07.18 loop)
   ├─ cloud (10 CloudBackend children): shared _make_api_request + hooks
   ├─ duckduckgo (R07.30): _pace_chat (3s) → _acquire_proof (/status challenge →
   │     node solve → base64(SHA256) post-processing) → POST /chat with
   │     X-Vqd-Hash-1 + system-DROPPED body + durableStream JWK → SSE
   │     (role-based grammar); 401/ERR_CHALLENGE re-solve ×1; 418 ladder ×3;
   │     response header x-vqd-hash-1 = next challenge
   └─ stablediffusion (R07.31): _flatten_messages (system suppressed,
         sample_steps → sd_cpp_extra_args block appended) → POST
         /v1/images/generations (900s timeout) → 32MB-capped decode →
         artifacts write → content = "[image saved: <path>]"
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
               ┌─────────────────┴┴─────────────────────┐
               ▼                                        ▼
      backends/base.py ◄── backends/cloud_base.py ◄── plugins/{zai,openrouter,gemini,openai,huggingface,mistral,pollinations,orcarouter,nvidia,cloudflare,siliconflow}
               │                                            ▲
               │                                            │
               │                                   backends/openai_compat.py ◄── plugins/stablediffusion (R07.31, images)
               │                                            ▲
               ▼                                            │
      plugins/duckduckgo (R07.30 — BaseBackend DIRECT, no cloud_base) ┘
               │
               ▼
      config.py ◄─── referenced by EVERYTHING
      shared_args.py ◄── parser.py + __init__.py export
```

Key coupling points:
- `core/helpers.py` is imported by 18+ modules — blast radius for any security change is huge
- `backends/cloud_base.py` is the shared base for 11 cloud plugins — bug here × 11 backends
- `agentkthx/plugins/duckduckgo/` shares NOTHING with cloud_base (deliberate — the protocol can't); its node subprocesses are the only place remote-supplied code executes (SEC-21)
- `cli/agent_factory.py` is touched by BOTH chat startup paths and the model-switch callback — changes here affect every backend launch
- `backends/llama_server.py` `_agent_internal` tuple is duplicated across TWO methods — a new agent-internal kwarg MUST be added to BOTH
- `shared_args.py` flags must be wired in 3 surfaces + `parse_shared_args` + `SharedConfig`
- `agentkthx/__init__.py` has try/except optional imports (PersistentMemory, ACPPlugin, Soul) — silent `None` on failure (landmine #17)

---

## Patterns & Conventions

| Aspect | Pattern |
|--------|---------|
| **Class composition** | Mixin pattern: `Agent(AgentSetupMixin, CompactionMixin, ToolExecutionMixin, StreamingMixin, AgenticLoopMixin)`. Mixins access host via `self.X` with docstring-declared "host contract" — no type-checker verification (no mypy) |
| **Tool calling** | HYBRID: native function-calling when `_use_native_tools`; ReAct text prompting otherwise; `ToolSupportLevel.effective()` normalizes every would-be NONE to REACT at every surface. **R07.31 caveat (ROB-51)**: ddg + sd report REACT but suppress the system prompt at the wire — the scaffolding never reaches the model; tools silently cannot engage there |
| **System-prompt suppression** | NEW R07.30/31 convention: backends whose wire has no system role or whose upstream punishes harness prompts DROP/ suppress system messages at the backend layer (`_strip_system_messages` / `_flatten_messages`). DDG: live testing showed forwarded harness prompts read as jailbreak attempts. Tool RESULTS still fold into user turns |
| **Challenge-proof auth (DDG)** | The keyless backend authenticates by EXECUTING a server-issued JS challenge (Node subprocess) and returning the solved proof — per-turn rotation via the response header. Trust boundary: see SEC-21. Node >= 18 required; fail-fast with remediation when missing |
| **Image-backend conventions (SD)** | Every chat turn = ONE image; `--model` advisory (no wire field); context columns render "?" (pixel limits are not tokens); `GENERATION_TIMEOUT=900s` (CPU diffusion takes minutes); artifacts written to `AGENTKTHX_ARTIFACTS_DIR` with backend-generated filenames only (untrusted-input discipline); usage `{"estimated": True}` |
| **Per-request sampling params** | `num_batch`/`repeat_penalty`/`repeat_last_n`: CLI flag → agent attr (is-not-None gate) → backend. Cloud: dropped. **R07.32 remap**: on sd, `--max-steps` means diffusion sample steps (1..100), NOT the loop ceiling — relabeled "Sample Steps" in the session header + /debug |
| **Explicit-pin pattern** | Every user-set param gets `agent._<param>_explicit = True`; `apply_model_switch` consults pins before re-deriving; num_batch/repeat_* are never re-derived |
| **Error classification** | `is_error_result` regex; `is_transient_api_error(e, body=None)` — permanent markers first, auth/404 permanent, 429/5xx transient. CloudBackend hook surface: `_STATUS_REMEDIATIONS`, quota classifier + message, fixed-param 400, 403/5035 override. DDG has its own taxonomy (401/403/404/418/429/5xx + SSE frames) — the house pattern applied to a non-OpenAI wire |
| **Context defaults** | `num_predict = num_ctx // 32` everywhere; GGUF `context_length` beats hardcoded 8K; SD/DDG opt out (None / seed-catalog 128K) |
| **Security** | Defense-in-depth: `validate_path`, `sanitize_command`, `is_safe_url` (bounded DNS, 5-hop redirect budget), `safe_eval`, `sanitize_tool_output`, plugin sha256 pins (opt-in — SEC-13). **NEW surface (SEC-21)**: the DDG challenge solver executes remote-supplied JS — env-allowlist the subprocess spawn + document the trust boundary |
| **Parallel tools** | Independent tool-call batches run concurrently (4 workers), results commit in call order; `AGENTKTHX_PARALLEL_TOOLS=0` escape hatch |
| **Anti-bot pacing (DDG)** | Class-level minimum /chat interval (`DUCKDUCKGO_MIN_INTERVAL`, default 3s) + minimum cookie set + Chromium client hints + 418 teapot ladder with proof-duration floor. ROB-46: no lock — races under concurrency |
| **Model catalogs** | Seed-backed (`agentkthx/data/model_seed.json` — now 13 backend keys incl. `duckduckgo` 8 models); persistent `model_cache` with source provenance; cloud `list_models()` = narrowed-except + store-on-success + `get_stale_models()` stale-first (the ROB-42 shape); DDG list_models is OFFLINE (seed only — no network in model listing); SD prefers live capabilities over the pseudo id |
| **File naming** | `snake_case.py` modules, `PascalCase` classes, `SCREAMING_SNAKE` constants. Note: DDG plugin uses `duckduckgo.py`, SD uses `backend.py` (plugin.json entrypoint decides — both valid) |
| **Tests** | Co-located in `tests/`, `test_*.py`, pytest fixtures; per-release regression files are the house convention (R07.30: `test_duckduckgo_backend.py` 99 tests; R07.31: `test_stablediffusion_backend.py` 35 tests + 2 base-class tests). All mocked — no integration tier (TEST-01) |

---

## Known Landmines

> **Refreshed 2026-10-10 against the R07.31 tree (commit `986428a`) by Super-Z** — all 7 original active landmines re-verified in code (line refs below updated), 5 intentional/as-designed unchanged, 10 closed-and-verified-fixed unchanged. Five NEW landmines registered from the R07.30/R07.31 surface (#23–#27), all cross-referenced to their audit-register IDs.

### Active (12)

2. **Pre-#13 thinking caches keep stale vendor-bleed verdicts** — machines that ran an R07.19-era `models` scan cached `YES` for `thinkingmachines/*` under `thinking:<model>` keys (`tool_cache.py`; still NO cache-version or invalidation mechanism — re-verified R07.31). They render `think ✓ yes` until the entry expires or `~/.agentkthx/tool_support.json` is deleted.
7. **Remote-catalog probe can stall chat startup** — `_probe_remote_catalog` calls `backend.list_models()` synchronously with the backend's own timeouts; a dead tunnel adds ~10s before falling back. `_detect_weight_quant` (`agent_factory.py:52`) still stacks up to TWO more sequential HTTP calls with silent `except Exception: pass` ×2 (`:87`, `:98` — re-verified R07.31).
8. **`_is_local_base_url` excludes 172.16/12** (`agent_factory.py:801`, comment `:840`) — RFC1918 `172.16-31.x.x` backends take the REMOTE probe path; local TurboState/Ollama-catalog lookups are skipped for them. Empty/unknown URLs are treated as local (conservative).
13. **Streaming-path JSON parse errors fall back to `{"_raw_arguments": ...}`** (`streaming.py:206`) without the debug chain the ReAct path has.
16. **`BUILTIN_REGISTRY` todo store is a module-level singleton** (`builtins.py:1136-1140`) — two `Agent` instances in one process share todos unless `set_todo_session()` is called during init.
17. **`__init__.py` optional imports are silent `None`** — three feature guards fail silent: PersistentMemory, ACPPlugin, Soul types (verified R07.31 — same pattern, shifted lines).
19. **llama-server kwargs forwarding has an exclusion-list trap** — `_agent_internal` is duplicated across `_generate_completion` (`llama_server.py:443`) and `_stream_completion` (`:558`) (re-verified R07.31 — same line numbers). Add a new agent-internal kwarg to only one → the other leaks it into /completion bodies.
23. **NEW (SEC-21, Medium) — the DDG challenge solver executes remote-supplied JS with full Node capabilities in a subprocess that inherits the ENTIRE parent environment** (`duckduckgo.py:503-511` `env = dict(os.environ)`; `ddg_vqd.js:255` `new Function` eval — `require`/`process` reachable). A hostile/compromised duck.ai /status response = arbitrary local code execution with every sibling-provider API key in the env. Cheap non-breaking fix: env-allowlist both node spawns (`PATH/HOME/LANG/TMPDIR/DDG_SOLVE_UA`). Capture proof mode is the structural fix but is UNIMPLEMENTED (#24).
24. **NEW (MAINT-32) — `DUCKDUCKGO_PROOF_MODE=capture` is a silent no-op** — `_PROOF_MODE` parsed at import and never consumed; `_CAPTURE_JS` names a `ddg_capture.js` that is NOT in the repo (the `_require_helper` remediation tells users to copy a phantom file); the module docstring presents capture in present tense. Only `agentkthx config` honestly says "RESERVED / NOT YET IMPLEMENTED".
25. **NEW (ROB-46) — DDG class-level `/chat` pacing and the durableStream lazy mint are unsynchronized** (`duckduckgo.py:377-378, 858-876, 769-770`) — orchestrator parallel mode / 4-worker tool batches race the read-sleep-write and can double-mint the RSA keypair; the anti-429 guarantee is advisory under concurrency.
26. **NEW (ROB-48) — SD startup discovery stacks up to ~30s of sequential probes on a hung endpoint** (`agent_factory.py:187-199` temp `list_models()` + `:227-233` cache warm; 10s timeouts in `stablediffusion/backend.py:191-202, 272-281`) — landmine #7's family extended to a "local" backend whose `SD_BASE_URL` env override explicitly invites Colab tunnels/LAN. Fix: reuse the temp instance (its cache is warm).
27. **NEW (ROB-50) — SD artifact filenames collide across backend instances/processes within one wall-clock second** (`stablediffusion/backend.py:608-621` — second-resolution stamp + per-instance counter; `write_bytes` truncates silently). Model-switch rebuilds reset the counter; two processes each start at 001.

### Intentional / as-designed (5) — do NOT "fix"

10. **`--api` default is `"openai"`, not `"openre"`** (`shared_args.py`) — surprising given the OpenResponses branding. Note `repeat_penalty`/`repeat_last_n`/`num_batch` only work on Ollama via `--api openre`.
11. **`update_check.py` makes 3 sequential HTTPS requests on every CLI invocation** — INTENTIONAL per owner (ROB-05 WONTFIX). Opt out with `AGENTKTHX_NO_UPDATE_CHECK=1`.
14. **`ErrorRecoveryTracker.consecutive_all` resets on ANY success** — deliberate R06.52 semantics; alternating fail/succeed tool calls can loop until `max_steps`.
15. **`MemoryConfig.max_tokens` defaults to `0`** (`memory.py`) — token-tier pruning is opt-in; long agentic runs rely on compaction at 85% num_ctx.
20. **BitNet `repeat_penalty=1.3` is a default, not a hardcode** — an explicit kwarg/`/param repeat_penalty` overrides it. Don't "restore" the hardcode.

### NEW intentional decisions from R07.30/31 — do NOT "fix" these either

- **DDG system-prompt suppression** (`_strip_system_messages` DROPS system content): live testing showed forwarded harness/system prompts read as JAILBREAK ATTEMPTS to the upstream models (they refuse + lecture + derail). Do not "smuggle" the system prompt into user turns. Tool results fold into user turns — that's the ReAct loop's path.
- **DDG `canUseTools=false` / REACT verdict**: the wire HAS a native tools surface (WebSearch/GenerateImage/NewsSearch/...) but it is undocumented and rotates independently — shipping canUseTools=false until it stabilizes is the protocol decision.
- **SD `--model` is advisory**: the wire has NO model field (the server serves its loaded pool under the hardcoded `sd-cpp-local` id). Never "fix" the missing model field.
- **SD `GENERATION_TIMEOUT=900`**: the 120s `BackendConfig` default aborts mid-diffusion on CPU. Explicit `get_backend(timeout=...)` still wins.
- **`sd_cpp_extra_args` block must stay on ONE line** — the server's extraction regex `.` does not match newlines; a pretty-printed block is left in the prompt verbatim.

### Closed — verified fixed in the R07.31 tree (10)

1. ~~**`_is_process_alive` KILLS the target on Windows**~~ (ROB-33, CLOSED R07.24) — ctypes `OpenProcess` path (`turbo.py`).
3. ~~**`--force-react=False` is not a valid CLI invocation**~~ (MAINT-25, CLOSED R07.21) — tri-state `on/off/auto`.
4. ~~**Custom souls can lose ReAct format instructions**~~ (MAINT-24, CLOSED R07.21) — `_build_tool_section` emits real per-tool examples.
5. ~~**`parse_shared_args` drops the documented `0` sentinel**~~ (ROB-35, CLOSED R07.21) — `is None` checks.
6. ~~**`--num-ctx infk` crashes with a raw OverflowError**~~ (ROB-36, CLOSED R07.21) — `math.isfinite` guard (the class ROB-49 now extends to the sd setter).
9. ~~**`Agent.add_tool` emits no `DeprecationWarning`**~~ (MAINT-16, CLOSED R07.07) — warns once per call site.
12. ~~**`Memory.sanitize_history` mutates `_messages` in place**~~ (PERF-01, CLOSED R07.14) — `_invalidate_caches()` funnel.
18. ~~**`normalize_args` strategy 5 matches substrings**~~ (MAINT-03, CLOSED R07.24) — strategy 5 removed entirely.
21. ~~**OpenRouter shows `tools ✓ native` for non-chat slugs**~~ (ROB-39, CLOSED R07.21) — `_NON_CHAT_SLUG_PATTERNS`.
22. ~~**The chat empty-answer boilerplate misdiagnoses fatal errors**~~ (ROB-38, CLOSED R07.21) — definitive fatal-error branch.

---

## Active Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Zero dependencies** | `dependencies = []` | Reproducible install, no supply-chain surface; trade-off: hand-rolled SSE, SSRF, AST eval — and, since R07.30, bundled Node helpers for the one protocol stdlib can't serve (challenge solving, RSA minting) |
| **Backend rename** (R07.16) | `turboquant` primary; `llama-server`/`llama_server` aliases | Match the TurboQuant fork branding |
| **Local tool calling** (R07.16) | Auto-detect via cached `test_tool_support`; UNTESTED defaults to ReAct | Small CPU models typically aren't trained for native function calling |
| **Context auto-derivation** (R07.16) | GGUF `context_length` → ctx; `num_predict = ctx // 32` | Kills the hardcoded 8K mismatch |
| **CloudBackend base** (R07.05, hardened R07.28) | New cloud backends = hooks only, shared transport inherited untouched | SiliconFlow (R07.29) proved the pattern; DuckDuckGo (R07.30) deliberately does NOT use it (protocol shares nothing — BaseBackend direct); StableDiffusion (R07.31) extends `OpenAICompatibleBackend` instead (local OpenAI-shaped surface) |
| **System-prompt suppression on DDG/SD** (R07.30/31) | Backend-layer drop; never forward the harness prompt | DDG: live jailbreak-lecture finding; SD: diffusion-irrelevant scaffolding would render into images. ROB-51 tracks the tool-capability consequence |
| **Challenge-proof auth (R07.30)** | Execute DDG's JS challenge via a Node subprocess; per-turn rotation; 418 ladder + duration floor + 3s pacing | The retired x-vqd-4 token left no cheaper keyless path; the maintained Go client ships the same shape. SEC-21 registers the trust boundary — env-allowlist the spawn |
| **Image generation = external server (R07.31)** | sd-server is NEVER managed by the framework (Ollama pattern) | No process lifecycle to own; the backend is pure HTTP; the notebook cells are the documented serve vehicle |
| **`--max-steps` sd remap (R07.32-in-tree)** | On sd only, remapped to diffusion sample steps (1..100, fail-fast); every other backend keeps loop-ceiling semantics | The flag name is generic enough to carry both meanings; the session header relabels so operators see the value that actually governs generation |
| **HTTP client** | `urllib.request` (stdlib) | Zero-dep; trade-off: no pooling, manual SSE buffering (ROB-06 closed: deterministic close) |
| **`is_cloud` attribute** | Per-class on `BaseBackend`; DDG overrides True, SD overrides False | R06.57 MAINT-05 precedent — replaced hardcoded provider lists |
| **Update check always-live** | No cache (R07.00) | Owner's refresh script relies on uncached check (ROB-05 WONTFIX) |
| **Plugin pins opt-in** (R07.05) | sha256 verified fail-closed WHEN present | Closes SEC-06; no enforcement mode yet (SEC-13) |
| **Parallel tool batches** (R07.15) | Concurrent for independent batches, committed in call order | Latency win with byte-identical transcripts |
| **Capabilities-first tool probe** (R07.19) | `/api/tags` capabilities verdict; ONE `tools` column; NONE→REACT normalizer | Authoritative, zero-inference; ROB-51 documents the DDG/SD caveat |
| **OpenRouter aggregator assumption** (R07.05, reviewed R07.19) | `test_tool_support` returns NATIVE unconditionally; runtime 400→ReAct fallback is the safety net | No per-model probe across hundreds of models |

---

## What's Missing / Incomplete

1. **28 OPEN findings** in `audit/audit.md` (full detail + priority matrix there): Security 1 (SEC-21 — the DDG challenge-execution trust boundary; first OPEN security finding since R07.25, NOT a regression of any closed control) · Robustness 8 (ROB-31 Pollinations entitlement mismatch; ROB-45..51 from the R07.30/31 surface: DDG subprocess taxonomy / pacing races / stream-retry duplication, SD probe stacking / raw ValueError / artifact collision, and the cross-cutting REACT-verdict gap) · Maintainability 5 (MAINT-01 1,946-line cmd_chat, MAINT-27 `/sh` inline branch, MAINT-31 CloudBackend dedup backlog, MAINT-32 phantom capture mode, MAINT-33 stale enum comment) · New Features 6 (FEAT-03 tool output schema, FEAT-05 plugin sandbox, FEAT-06 streaming arg deltas, FEAT-07 conversation export, FEAT-08 free-TIER mode, FEAT-09 SiliconFlow passthrough) · Testing 7 (TEST-01 integration tier, TEST-03/04/05/07/10/12/13) — TEST-09 closed R07.25, TEST-11 closed R07.24, FEAT-10 closed R07.30.
2. **No integration tests** — all 3,498 tests are mocked unit tests (TEST-01); coverage baseline 42.7% (R07.01), CLI layer well below. The smoke-test family (`scripts/smoke_test.sh` + `smoke_test_r07_25.sh`) is the manual pre-push gate per owner policy (streaming + non-streaming paths per cloud backend). TEST-12's tools/429 shapes and TEST-13's one live SSE turn are the two live-gated pending items.
3. **No mypy** — no `[tool.mypy]`; mixins' host contracts are unverifiable by tooling.
4. **`schemas/v0.2/plugin.schema.json` declared but not validated** — ad-hoc dict checks in `_parse_manifest`.
5. **No conversation export/import** (FEAT-07); no tool output JSON Schema validation (FEAT-03); no streaming `function_call_arguments.delta` (FEAT-06).
6. **`agentkthx/examples/`** are demo scripts, not doctests; they do NOT use `shared_args`/`SharedConfig`.
7. **`patches/` not integrated into the build.**
8. **SD registered follow-ups** (`docs/STABLE_DIFFUSION_BACKEND_PLAN.md` §8): multi-model pool LRU eviction semantics, `width`/`height` CLI flags not yet plumbed (kwargs already wired to `size`), `/v1/images/edits` (img2img).
9. **118 findings archived** in `audit/deltas.md` (108 CLOSED across R07.00–R07.31 + 10 WONTFIX with owner rationale; register totals 146 after the R07.30 pair + the 2026-10-10 re-audit's ten). FEAT-10 was the only R07.30/31 closure (true `generate_stream()` via the protocol rewrite; split-audit moved it to deltas.md). R07.31 registered nothing from the release itself — the re-audit pass did. The dashboard generator (`audit/generate_audit_dash.py`) merges both files for the full register; run it manually before GitHub/CI per owner policy.

---

## Quick Start for Developer

1. **Read the Critical Files Index** — start with `plugins/duckduckgo/duckduckgo.py` + `plugins/stablediffusion/backend.py` (the two newest, where all ten new findings live), then `cli/agent_factory.py` (wiring), `shared_args.py` (flags), `plugins/turboquant/turbo.py` (server lifecycle), `core/agent_setup.py` (prompt strategy). The "Why It Matters" column tells you when to touch each.
2. **Understand the Lifecycle** — `cmd_chat → _build_agent (auto-detection + catalog ladder + SD discovery) → Agent.run → _run_loop_iteration → _generate_with_retry (records TPS) → backend.generate (per-backend wire shape) → tool dispatch (sanitize → memory)`.
3. **Check Known Landmines** before changing (refreshed R07.31 — 12 active):
   - New agent-internal kwarg → add to `_agent_internal` in BOTH llama_server methods (`:443` + `:558`)
   - New CLI param → wire in 3 surfaces + `parse_shared_args` + `SharedConfig`, use `is not None`, mirror the `_explicit` pin
   - Touching DDG: the challenge solver's env is a SECURITY boundary (SEC-21) — don't add env vars to the subprocess; the solver UA must stay in lockstep with the request UA; system messages must stay dropped; new node helpers go in pyproject package-data (`plugins/*/*.js`) or pip installs silently drop them
   - Touching SD: never open model-supplied paths; the `sd_cpp_extra_args` block stays on ONE line; artifact filenames must stay backend-generated (add uniqueness, don't reuse the counter)
   - Mid-session tool addition: `register_tool`, NOT `add_tool`; `AGENTKTHX_NO_UPDATE_CHECK=1` skips the 3-request startup check; `MemoryConfig.max_tokens=0` default — token tier is opt-in
4. **Follow Patterns** — hybrid native/ReAct tool calling via `_use_native_tools`; `num_predict = ctx // 32`; `sanitize_tool_output` on every tool result; per-release regression-test files (R07.30/31 exemplars: `test_duckduckgo_backend.py`, `test_stablediffusion_backend.py`); CloudBackend children = hooks only, no copied loops.
5. **Blast radius**: `core/helpers.py` → 18+ modules · `backends/cloud_base.py` → 11 cloud plugins · `cli/agent_factory.py` → every backend launch · `plugins/_loader.py` → every backend load · `shared_args.py` → every CLI surface · `ddg_vqd.js` → every DDG turn (and, via SEC-21, potentially the whole machine).
6. **Run tests before committing**: `python -m pytest tests/ -q` (~25s, 3,498 tests, 20 skipped). Lint is a REQUIRED CI check: `ruff check agentkthx/ tests/ && black --check agentkthx/ tests/` (clean at this commit). Live-gated: `AGENTKTHX_LIVE_TESTS=1 python -m pytest tests/test_mcp_live_contract.py -v`. Manual smoke + dashboard (before GitHub/CI per owner policy): `./scripts/smoke_test.sh --backend <name>` + `python3 audit/generate_audit_dash.py --audit audit/audit.md --deltas audit/deltas.md --brief audit/brief.md --output dashboard.html`.
7. **Read the register before adding work**: `audit/audit.md` (28 OPEN, ID-indexed, priority matrix — near-term tier leads with SEC-21's env-allowlist fix + the ROB-49/50/48 + MAINT-32/33 quick batch) + `audit/deltas.md` (118 archived with closure prose). Re-audit workflow, split tooling, and the dashboard parser contract are specified in `agentkthx/skills/codebase-audit/SKILL.md`. Backend support tiers are documented in `docs/SUPPORT.md`.

Do NOT start by reading every file. Use this brief as your map and read only what you need for your specific task. The `core/` package is the engine — most changes start there; R07.30-era work concentrates in `plugins/duckduckgo/*` + `core/types.py` + `config.py`; R07.31/32-era work in `plugins/stablediffusion/*` + `cli/agent_factory.py` + `cli/headers.py` + `cli/commands/{chat,models,config}.py` + `backends/openai_compat.py`. R07.22 added `agentkthx/mcp/`; R07.23 expanded `mcp` to 5 actions; R07.24 was the closure super-batch; R07.25 introduced support tiers; R07.28 hardened the CloudBackend shared machinery; R07.29 scaffolded SiliconFlow on it; R07.30/31 went off-pattern for two genuinely different protocols (keyless challenge-proof chat; local image generation) — read those two plugins before assuming any cloud-backend convention applies to them.
