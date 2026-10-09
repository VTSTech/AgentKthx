# Improvement & Enhancement Audit
**AgentKthx v0.7.31 (R07.31 — Stable Diffusion via sd.cpp as the 13th backend plugin and the FIRST image-generation backend (Ollama pattern applied to images: externally-managed sd-server, OpenAI-shaped /v1/images/generations, size-capped base64 PNG artifacts written to AGENTKTHX_ARTIFACTS_DIR, capabilities-first discovery of the REAL loaded weights); R07.30 added the DuckDuckGo AI Chat backend as the 12th cloud backend — the first keyless/anonymous one and the only BaseBackend-direct subclass, rebuilt to the challenge-based x-vqd-hash-1 protocol within the same release window; 2026-10-10 re-audit of both releases: all 18 carried-forward OPEN findings re-verified, 10 new findings registered (SEC-21 Medium + 9 Low), no closures — register 146 findings — 28 OPEN / 108 CLOSED / 10 WONTFIX)**
**AgentKthx v0.7.29 (R07.29 — SiliconFlow cloud backend scaffolded as the 11th plugin: the first backend built on the post-R07.28 hardened CloudBackend patterns — shared transport inherited untouched (identity-pinned), ROB-42-hardened list_models, MAINT-28 hook surface only; 106 unit tests; 2 findings registered (FEAT-09 enable_thinking passthrough, TEST-12 live-shape contract test); 15 OPEN carried forward → 17 OPEN)**
**AgentKthx v0.7.28 (R07.28 — MAINT-28 + ROB-42 CLOSED in two batches: batch 1 deduplicated the ×4 retry-loop skeleton into shared CloudBackend template methods and fixed the list_models() cache-provenance on both new backends; batch 2 lifted the request loops themselves (shared HTTP transport + catalog-key hook) and extended the ROB-42 catch-narrowing to zai + pollinations; MAINT-31 registered as the remaining dedup backlog; NVIDIA NIM + Cloudflare Workers AI remain backends 9 & 10 from R07.26/27)**
**R07.24 closure super-batch (v0.7.24): thirteen findings closed + two WONTFIX across three batches — SEC-20, ROB-28, ROB-41, TEST-11, MCP-01, MCP-03, MCP-04, MCP-05, MAINT-03, MAINT-22, MAINT-23, ROB-29, ROB-33 closed; MCP-02 + SEC-13 wontfix**
**R07.25 follow-up (in-progress): SEC-09 WONTFIX (owner decision — ACP has no attack surface, the `http://localhost:8766` default is a placeholder; users deploying remotely put it behind HTTPS themselves) + TEST-09 CLOSED (`scripts/smoke_test_r07_25.sh` now exercises both streaming + non-streaming paths per cloud backend — closes the R07.09 streaming-only bug class)**
**R07.28 (2026-10-09 closure pass): MAINT-28 + ROB-42 CLOSED — the ×4 retry-loop skeleton deduplicated into shared CloudBackend template methods (per-backend hooks: remediation table, quota classifier, fixed-param 400, 403/5035 override; ~400 LOC of duplication collapsed, cloudflare "limit" docstring drift structurally fixed) and the list_models() failure path on both new backends rebuilt to the Mistral ROB-28/30 pattern (narrowed except, store-on-success, stale-first service); batch 2 (same release) moved the request loops themselves onto CloudBackend — shared `_make_api_request`/`_iter_sse_lines`/`generate_stream`/`_jev_call_completions` + the `_catalog_model_key`/`_tweak_request_body` hooks (nvidia.py 1,070 → 807 LOC, cloudflare.py 1,461 → 1,213 LOC) — fixed a latent streaming-retry UnboundLocalError the per-backend loops carried, and extended the ROB-42 catch-narrowing to zai + pollinations — +41 regression tests total, suite 3,212; MAINT-31 registered for the remaining backlog. Batch 3 (same release) closed the four quick wins from the same surface — ROB-43 (the attempt == 0 quota gate dropped from the shared `_check_quota_429`: a quota 429 now fast-fails at ANY attempt with the clear quota message instead of burning the retry budget), ROB-44 (Cloudflare `_get_models_url` derives scheme+host from `_base_url` — discovery honors `CLOUDFLARE_BASE_URL` exactly like chat traffic), MAINT-29 (test_tool_support docstrings rewritten to the name-pattern-only contract; the `_is_free_model` "conservatively paid" drift aligned), MAINT-30 (NVIDIA NIM row restored to docs/SUPPORT.md's Fully Supported table) — +15 regression tests, suite 3,227. Prior: R07.26–R07.27 (feature releases — NVIDIA NIM + Cloudflare Workers AI cloud backends 9 & 10, `agentkthx auth` subcommand, streaming spinner fix, paid-plan-only 403/5035 detection): no closures; re-audit 2026-10-09 registered six new findings — ROB-42, ROB-43, ROB-44, MAINT-28, MAINT-29, MAINT-30**
**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-10-10 (R07.30/R07.31 re-audit)  
**Commit:** `986428a` (R07.31 + this re-audit pass in the working tree; PyPI 0.7.31 latest) | **Test Suite:** 3498 passed / 20 skipped in ~25.3s   
146 Findings | 28 Open | 8 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST, MCP  
Severity (open): 0 High | 6 Medium | 22 Low  
28 OPEN | 118 archived in deltas.md (108 CLOSED + 10 WONTFIX) — generate_audit_dash.py merges both for the dashboard

---
## Table of Contents
- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
- [Priority Matrix](#priority-matrix)
---
## Executive Summary
This re-audit covers AgentKthx at commit `986428a` (R07.31 tree — Stable Diffusion via sd.cpp as the 13th backend plugin; PyPI 0.7.31 latest, matching the tree). The codebase comprises 150 Python source files totaling ~74,700 LOC, with ~51,800 lines of tests across 103 test files — the suite passes **3,498 tests / 20 skipped in ~25.3s**, with CI on Python 3.12/3.13, a parallel coverage job, and a required lint job (pinned ruff + black, verified clean at this commit). Two feature releases shipped since the last register update: R07.30 (the DuckDuckGo AI Chat backend — the 12th cloud backend, the first keyless/anonymous one, and the only BaseBackend-direct subclass, scaffolded and then protocol-REWRITTEN to the challenge-based x-vqd-hash-1 wire in the same window, +3,605/−7) and R07.31 (the Stable Diffusion sd.cpp image-generation backend — the FIRST backend that generates images instead of text, live-verified end-to-end on Colab CPU, +1,470/−26), plus uncommitted-to-changelog R07.32 follow-up work already in the tree (--max-steps → diffusion sample_steps remap, "Sample Steps" session-header relabel, capabilities cache warm).
This pass re-verified all 18 carried-forward OPEN findings and audited the ~2,850 LOC of new backend code, registering **ten findings: SEC-21 (Medium) + nine Low**. The headline — and the first OPEN Security finding since the R07.25 surface-complete declaration — is **SEC-21**: the DuckDuckGo synth proof mode (the default) executes server-supplied challenge JavaScript in a Node subprocess that keeps full Node capabilities (`require`/`process` reachable from the eval'd challenge — no realm isolation) AND inherits the entire parent environment, so a hostile or compromised duck.ai response means arbitrary local code execution with every sibling-provider API key along for the ride. It is NOT a regression of any closed control — it is a NEW trust boundary the challenge protocol introduces (executing the challenge IS the authentication) — and the cheap fix is non-breaking: env-allowlist both node spawns. Related: MAINT-32 (the capture proof mode — the structural SEC-21 fix — is a silent no-op whose phantom `ddg_capture.js` the remediation text tells users to copy from a repo that doesn't contain it) and MAINT-33 (the `BackendType.DUCKDUCKGO` enum comment still teaches the retired x-vqd-4 protocol and the 2025 catalog).
The robustness batch is the usual new-backend surface tax, concentrated on the two new plugins: ROB-45 (DDG subprocess failures leak raw tracebacks past the otherwise-careful error taxonomy — the inverse of the closed ROB-28/30 class), ROB-46 (the class-level /chat pacing and the durableStream lazy mint are unsynchronized — concurrency defeats the anti-429 guarantee exactly when parallel agents need it), ROB-47 (mid-stream retry after the first yielded delta garbles the accumulator), ROB-48 (SD startup stacks ~30s of sequential probes on a hung tunnel — Known Landmine #7's family, extended to a backend whose env override explicitly invites Colab tunnels), ROB-49 (out-of-range `--max-steps` on sd → raw constructor ValueError, the ROB-36 clean-error-path class), ROB-50 (SD artifact filename collisions silently overwrite generated images across model-switch rebuilds or same-second processes), and the cross-cutting ROB-51 — both new backends advertise REACT tool support while suppressing the system prompt at the wire, so the ReAct scaffolding never reaches the model and tools silently cannot engage (SD's docstring is honest about the suppression; the CAPABILITY GAP is what this register tracks, with three remediation options).
Cumulative closure state: **146 findings — 28 OPEN / 108 CLOSED / 10 WONTFIX (118 archived in deltas.md, ~81% resolved lifetime)** after the split moves FEAT-10 (CLOSED R07.30 by the protocol rewrite — true `generate_stream()`) into the archive. No closures this pass. The Priority Matrix gains SEC-21's env-allowlist hardening as the top near-term item (one small diff, no behavior change), with ROB-48/ROB-49 and the MAINT-32 honest-labeling pass as the cheap follow-on batch; TEST-13's remaining live SSE turn and TEST-12's topped-up-key smoke run are the two live-gated pending items carried from R07.29/R07.30.
Process note: `verify_open_findings.py` returned STILL_OPEN_LIKELY for 14 of 18 carried findings, with the other four verdicts (FEAT-03 PATTERN_GONE, FEAT-07/TEST-05 FILE_EXISTS_NO_PATTERN, TEST-01 UNKNOWN) being the same heuristic false-positive classes documented since the R07.18 delta (missing-feature findings have no pattern to find; file-only references have no pattern to check). Manual spot-checks confirmed MAINT-01 (chat.py grew 1,733 → 1,946 LOC with the R07.31 header work — still a single function), ROB-31 (the paid_only surface is intact), and TEST-03 (FakeBackend still deliberately omits `generate_completions_stream`). No register drift; no silent closures. The ten new findings continue the established ID numbering (SEC-21 after the WONTFIX'd SEC-20 surface, ROB-45 after the R07.28-closed ROB-44, MAINT-32 after MAINT-31).
---

## Findings Summary
| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-21 | Medium | Security | OPEN | DuckDuckGo synth proof mode executes server-supplied challenge JS with full Node capabilities in a subprocess inheriting the ENTIRE parent environment — sibling-provider API keys exposed to remote code on a hostile duck.ai response |
| ROB-31 | Medium | Robustness | OPEN | healthy_fallbacks() under POLLINATIONS_ANON_CATALOG=1 ranks paid_only models the key cannot generate against — 8 of the anonymous top-10 are out-of-entitlement; fallback redirect 403s |
| MAINT-01 | Medium | Maintainability | OPEN | cmd_chat is a 1,733-line single function with 30+ nested closures and no slash-command dispatcher |
| FEAT-03 | Medium | New Features | OPEN | Tool output schema validation via JSON Schema |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all 2608 tests are mocked unit tests; slash-command dispatcher untested |
| TEST-03 | Medium | Testing | OPEN | FakeBackend in test_agentic_loop_subsystem.py omits generate_completions_stream — streaming callbacks unexercised |
| ROB-45 | Low | Robustness | OPEN | DDG proof-path subprocess failures (node TimeoutExpired, malformed solver JSON) bypass the backend's error taxonomy — raw tracebacks outside the retry loop's try/except |
| ROB-46 | Low | Robustness | OPEN | DDG class-level /chat pacing + lazy durableStream mint are unsynchronized — orchestrator/parallel-batch concurrency races the anti-429 guarantee |
| ROB-47 | Low | Robustness | OPEN | generate_stream mid-stream retry appends the restarted turn after already-yielded partial text — garbled accumulator on ERR_CHALLENGE/401 after the first delta |
| ROB-48 | Low | Robustness | OPEN | SD startup discovery stacks ~30s of sequential probes (temp list_models + capabilities warm) on a hung tunnel endpoint — landmine #7 family on a 'local' backend |
| ROB-49 | Low | Robustness | OPEN | --max-steps out of range on the sd backend surfaces as a raw constructor ValueError — no clean argparse-formatted error (ROB-36 class) |
| ROB-50 | Low | Robustness | OPEN | SD artifact filenames collide across backend instances/processes within one second (second-resolution stamp + per-instance counter) — write_bytes silently overwrites |
| ROB-51 | Low | Robustness | OPEN | REACT tool verdicts on the system-prompt-suppressing backends (DDG + SD) are aspirational — the scaffolding is never transmitted, tools silently cannot engage |
| MAINT-27 | Low | Maintainability | OPEN | New `/sh` slash command added R07.21 — inline if/elif branch in cmd_chat (MAINT-01 family); no slash-command dispatcher yet |
| FEAT-05 | Low | New Features | OPEN | Plugin sandboxing via restricted register() namespace + audit hooks |
| FEAT-06 | Low | New Features | OPEN | Streaming tool-call argument deltas (function_call_arguments.delta SSE events) |
| FEAT-07 | Low | New Features | OPEN | Conversation export/import to OpenResponses-format JSON |
| FEAT-08 | Low | New Features | OPEN | Free TIER (paid_only boundary on bare GET /models) unreachable — FREE_ONLY exposes only the 16 zero-cost models, not the ~102 Quest-Pollen-eligible text models the key can run |
| TEST-04 | Low | Testing | OPEN | No test coverage for agent_mode.py rollback functionality (822 LOC, key feature) |
| TEST-05 | Low | Testing | OPEN | test_bump_version_script.py tests shell script via subprocess — fails on Windows/no-bash |
| TEST-07 | Low | Testing | OPEN | No test for update_check module's network-failure paths (URLError, socket.timeout, malformed JSON) |
| TEST-10 | Low | Testing | OPEN | Zero live-shape coverage for free-model detection — v0.1.2 shipped with _card_is_free blind to the live currency-only encoding while all 83 fixture tests stayed green |
| MAINT-31 | Low | Maintainability | OPEN | Remaining CloudBackend dedup backlog — list_models ×6 template, test_tool_support ×7 name-pattern shape, _fetch_live_models transport trio, legacy request loops (mistral/pollinations/zai/orcarouter) never migrated to the shared MAINT-28 machinery |
| MAINT-32 | Low | Maintainability | OPEN | DUCKDUCKGO_PROOF_MODE=capture is a silent no-op — _PROOF_MODE never consumed, _CAPTURE_JS points at a ddg_capture.js that is not in the repo, module docstring oversells; only `agentkthx config` says RESERVED |
| MAINT-33 | Low | Maintainability | OPEN | BackendType.DUCKDUCKGO enum comment still teaches the retired x-vqd-4 protocol + the 2025 catalog (pre-rewrite scaffold text) |
| FEAT-09 | Low | New Features | OPEN | SiliconFlow enable_thinking + repetition_penalty request-field passthrough — the API supports both fields; the R07.29 scaffold drops them (cloud-drop-repeat_* house convention), and mapping think→enable_thinking needs BOTH _build_stream_body and _tweak_request_body to avoid the MAINT-22 streaming-drift class |
| TEST-12 | Low | Testing | OPEN | No live-shape contract test for the SiliconFlow scaffold — the seed catalog/envelope/error shapes are mock-verified only; the R07.27 Cloudflare lesson says the live /v1/models shape diverges from the docs until the maintainer's first smoke run — 2026-10-09: the smoke attempt verified auth/catalog/one `--think` generation before the account balance drained (tool steps → 402); the 402 balance shape is now test-pinned, the tools/429 live shapes still await a topped-up key |
| TEST-13 | Low | Testing | OPEN | Live-shape verification for the DuckDuckGo backend — PARTIALLY CLOSED by the 2026-10-09 protocol work: the challenge handshake (x-vqd-accept → solve → X-Vqd-Hash-1) was verified LIVE from an unrestricted network via the ddg-challenge-solver package (`--status-only` solves repeatedly at 54–987ms; a 404 ERR_MODEL_UNAVAILABLE JSON body proved auth + request shape parse correctly, isolating the model-ID rotation), and the 8-model seed catalog was re-verified against the live dropdown + benoitpetit/duckduckgo-chat-cli models.go; STILL OPEN: a live 200 SSE turn through the plugin itself (`scripts/probe_duckduckgo.py --live` automates it end-to-end via the backend) |
---
## R07.18 New Findings
Two findings, both from the R07.18 flag-parsing surface (`_parse_token_size` + the `SharedConfig` coalescing layer). No closures this pass — all 34 carried-forward OPEN findings re-verified in current code.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
---
## R07.20 New Findings
Two findings, both Low, both observed in the R07.19 surface (the post-release smoke test + the OpenRouter tool-support review). No closures this pass — all 35 carried-forward OPEN findings re-verified in current code.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|

## R07.26–R07.27 New Findings
Six findings from the 2026-10-09 re-audit of the R07.26/R07.27 surface (NVIDIA NIM + Cloudflare Workers AI backends, `agentkthx auth`, streaming spinner fix). No closures this pass. The headline: ROB-42 regresses the R07.24 ROB-28/ROB-30 closure class on both new backends; MAINT-28 is urgent before the planned SiliconFlow/DuckDuckGo scaffolds copy the pattern again.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| ROB-42 | Medium | Robustness | `agentkthx/plugins/nvidia/nvidia.py` (`list_models`, :591-599), `agentkthx/plugins/cloudflare/cloudflare.py` (`list_models`, :879-887) | `list_models()` failure path persists the seed fallback as fresh API-labeled cache — bare `except Exception` + unconditional `store_models(source="api")`, no stale-cache service (regresses the R07.24 ROB-28/ROB-30 closure class) |
| MAINT-28 | Medium | Maintainability | `agentkthx/plugins/nvidia/nvidia.py` (:705-861, :867-1042), `agentkthx/plugins/cloudflare/cloudflare.py` (:1039-1219, :1244-1406) | Retry-loop skeleton (envelope parse, context-length 400, quota handlers, 401/404/422) duplicated ×4 across the two new backends — drift already fired ("limit" docstring) |
| ROB-43 | Low | Robustness | `agentkthx/plugins/nvidia/nvidia.py` (:754-764, :907-918), `agentkthx/plugins/cloudflare/cloudflare.py` (:1089-1100, :1269-1280) | Quota-exhaustion fast-fail gated to `attempt == 0` — a quota 429 after ≥1 transient retry burns the retry budget and surfaces the generic error |
| ROB-44 | Low | Robustness | `agentkthx/plugins/cloudflare/cloudflare.py` (`_get_models_url`, :526-544) | Catalog discovery hardcodes `api.cloudflare.com` — ignores a `CLOUDFLARE_BASE_URL` override that chat traffic honors |
| MAINT-29 | Low | Maintainability | `agentkthx/plugins/nvidia/nvidia.py` (`test_tool_support`, :656-681), `agentkthx/plugins/cloudflare/cloudflare.py` (`test_tool_support`, :988-1014) | Docstrings claim a first-use probe + tool_cache write the overrides never perform (name-pattern returns only) |
| MAINT-30 | Low | Maintainability | `docs/SUPPORT.md` (Fully Supported + Limited Support tables) | Tier tables miss NVIDIA entirely — 9 rows for a claimed 10-cloud-backend register |

---

## R07.28 New Findings
One finding, registered during the batch-2 lift pass (2026-10-09). Two findings CLOSED this pass (ROB-42 + MAINT-28 — archived in deltas.md), plus the four Low quick wins in batch 3 (ROB-43, ROB-44, MAINT-29, MAINT-30 — archived in deltas.md). The batch-2 lift itself (shared transport + hooks, zai/pollinations catch-narrowing, streaming-retry UnboundLocalError fix) is documented in the R07.28 blockquote above and is NOT a register entry — it completes the MAINT-28/ROB-42 closures.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| MAINT-31 | Low | Maintainability | `agentkthx/plugins/{mistral,pollinations,zai,orcarouter}/*.py` (request loops + list_models), `agentkthx/plugins/{nvidia,cloudflare,mistral,pollinations,zai,orcarouter}/*.py` (test_tool_support ×7, _apply_free_only ×7), `agentkthx/plugins/{openai,gemini,huggingface,openrouter}/*.py` (429-retry family) | Remaining CloudBackend dedup backlog — the shared transport landed in batch 2, but the catalog/service shapes and the legacy loops still duplicate |

---

## R07.29 New Findings
Two findings from the SiliconFlow scaffold pass (2026-10-09). No closures this pass — all 15 carried-forward OPEN findings re-verified in current code (the scaffold itself is a feature addition, not a closure; its delta prose is in deltas.md). Both new findings are Low: the enable_thinking passthrough gap is a registered follow-up the API supports but the scaffold deliberately defers (streaming/non-streaming drift risk), and the live-shape gap is the R07.27 Cloudflare lesson applied prospectively.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| FEAT-09 | Low | New Features | `agentkthx/plugins/siliconflow/siliconflow.py` (`generate`, `_tweak_request_body` inherited no-op) | SiliconFlow enable_thinking + repetition_penalty request-field passthrough — both fields documented as supported (vLLM-style passthrough), neither forwarded by the scaffold; think is accepted-but-ignored |
| TEST-12 | Low | Testing | `tests/test_siliconflow_backend.py`, `scripts/smoke_test.sh` (siliconflow step) | No live-shape contract test for the SiliconFlow scaffold — 106 mocked tests pin the documented shapes, but the live /v1/models + error envelopes are unverified until the maintainer's first smoke run (2026-10-09: auth/catalog/`--think` verified + the 402 balance shape pinned; tools/429 shapes still gated on a topped-up key) |

---

## R07.31 New Findings
Ten findings from the 2026-10-10 re-audit of the R07.30 DuckDuckGo + R07.31 Stable Diffusion backends (+2,848 LOC of new backend code, +164 tests net). No closures this pass. The headline is SEC-21 — the first OPEN Security finding since the R07.25 surface-complete declaration, and it is NOT a regression of any closed control: the keyless challenge-proof protocol introduces a NEW trust boundary (executing server-supplied JS to authenticate), and the cheap fix (env-allowlisted subprocess spawn) is non-breaking. The Robustness set is the usual new-backend surface tax (error-taxonomy gaps, concurrency races, startup probe stacking) plus the cross-cutting ROB-51 — both new backends suppress the system prompt at the wire while advertising REACT, so the tool scaffolding never reaches the model and tools silently cannot engage.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| SEC-21 | Medium | Security | `agentkthx/plugins/duckduckgo/duckduckgo.py:503-511` (`_solve_challenge` env inheritance), `agentkthx/plugins/duckduckgo/ddg_vqd.js:255` (`new Function` eval), `agentkthx/plugins/duckduckgo/duckduckgo.py:529-533` (`_mint_durable` default env) | Remote-supplied challenge JS executed with full Node capabilities (`require`/`process` reachable) in a subprocess inheriting the ENTIRE parent environment — sibling-provider API keys exposed to remote code on a hostile duck.ai response; synth is the DEFAULT proof mode |
| ROB-45 | Low | Robustness | `agentkthx/plugins/duckduckgo/duckduckgo.py:494-544` (`_solve_challenge` 45s timeout + `json.loads`; `_mint_durable` 20s), call sites `:952` / `:1162` outside the try | Proof-path subprocess failures (TimeoutExpired, solver-stdout JSONDecodeError) bypass the DDG error taxonomy — raw tracebacks instead of the house RuntimeError + remediation shape |
| ROB-46 | Low | Robustness | `agentkthx/plugins/duckduckgo/duckduckgo.py:377-378` (class attr), `:858-876` (`_pace_chat`), `:769-770` (`_durable` lazy mint) | Class-level /chat pacing + durableStream lazy-mint are unsynchronized — orchestrator parallel mode / 4-worker tool batches race the anti-429 guarantee and can double-mint the keypair |
| ROB-47 | Low | Robustness | `agentkthx/plugins/duckduckgo/duckduckgo.py:1123-1275` (yield `:1187`, retry `continue` `:1275`) | generate_stream mid-stream retry (ERR_CHALLENGE/401 after the first delta) appends the restarted turn's full text after the already-yielded partial — garbled accumulator |
| ROB-48 | Low | Robustness | `agentkthx/cli/agent_factory.py:187-199` (temp list_models), `:227-233` (cache warm), `agentkthx/plugins/stablediffusion/backend.py:191-202` (models fallback 10s), `:272-281` (capabilities 10s) | SD startup discovery stacks up to ~30s of sequential probes on a hung (accepting, never-answering) SD_BASE_URL — the same sequential-silent-probe family as Known Landmine #7, extended to a backend whose env override explicitly invites tunnels/LAN |
| ROB-49 | Low | Robustness | `agentkthx/plugins/stablediffusion/backend.py:153-166` (setter raise), `agentkthx/cli/agent_factory.py:215-216` (pass-through) | `--max-steps 200 --backend sd` raises a raw ValueError from inside the StableDiffusionBackend constructor — nothing in _build_agent/cmd_chat catches it; house convention is the clean argparse-formatted error (ROB-36 precedent) |
| ROB-50 | Low | Robustness | `agentkthx/plugins/stablediffusion/backend.py:608-621` (`_write_artifact`, write_bytes `:620`) | Artifact filename = second-resolution UTC stamp + PER-INSTANCE counter — model-switch rebuilds (counter reset) or two processes in the same second collide and path.write_bytes silently truncates the first image |
| ROB-51 | Low | Robustness | `agentkthx/plugins/duckduckgo/duckduckgo.py:810-852` (`_strip_system_messages`) + `:1423-1439` (test_tool_support), `agentkthx/plugins/stablediffusion/backend.py:565-575` + `:594-617` (`_flatten_messages`) | REACT tool verdicts on both system-prompt-suppressing backends are aspirational — the factory force_reacts and assembles ReAct scaffolding that the wire then DROPS; the model never sees the tool list or the Action/Action Input grammar; tools silently cannot engage |
| MAINT-32 | Low | Maintainability | `agentkthx/plugins/duckduckgo/duckduckgo.py:102-108, 172, 198-203, 484-492`, `agentkthx/cli/commands/config.py:272-277` | DUCKDUCKGO_PROOF_MODE=capture is a silent no-op — _PROOF_MODE parsed at import and never consumed; _CAPTURE_JS names a ddg_capture.js absent from the repo (git ls-files: only ddg_vqd.js + ddg_durable.js shipped); the _require_helper remediation tells users to copy a phantom file; module docstring describes capture in present tense; only `agentkthx config` honestly says RESERVED / NOT YET IMPLEMENTED |
| MAINT-33 | Low | Maintainability | `agentkthx/core/types.py:202-216` | BackendType.DUCKDUCKGO enum comment is pre-rewrite scaffold text — still teaches the retired x-vqd-4 header-token handshake and the 2025 catalog (GPT-4o mini, o3-mini, Llama 3.3 70B…) that now 404s; same doc-drift class as the cloudflare "limit" docstring structurally fixed in R07.28 |

---

## Detailed Findings
<!-- Open findings only. CLOSED + WONTFIX detail sections are in deltas.md. -->
### Security
#### SEC-21: DuckDuckGo synth proof mode executes server-supplied challenge JS with full Node capabilities in a subprocess inheriting the entire parent environment
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:503-511` (`_solve_challenge` — `env = dict(os.environ)` + `subprocess.run`), `agentkthx/plugins/duckduckgo/duckduckgo.py:529-533` (`_mint_durable` — no env kwarg, inherits by default), `agentkthx/plugins/duckduckgo/ddg_vqd.js:248-256` (base64 decode + `new Function('return (' + js + ');')()`) |
| **Status** | OPEN — registered 2026-10-10 during the R07.30/R07.31 re-audit; the first OPEN Security finding since the R07.25 surface-complete declaration (SEC-20 was the last closure; 20 CLOSED + 5 WONTFIX lifetime) |

**Detail:** The x-vqd-hash-1 protocol's `synth` proof mode (the DEFAULT) fetches a challenge blob from the duck.ai `/status` response header and EXECUTES it: `ddg_vqd.js` decodes the base64 payload and evals it via `new Function(...)` inside the SAME Node realm — no `vm` isolation, no realm restriction, so challenge code reaches `require`, `process`, and the full standard library (`child_process`, `fs`, `net`) — against a `globalThis` carrying the emulated browser globals. The Python side spawns that solver with `env = dict(os.environ)` (the `_mint_durable` keygen inherits by default too), so EVERY environment variable the CLI process holds — `OPENROUTER_API_KEY`, `ZAI_API_KEY`, `GITHUB_TOKEN`, whatever the operator has loaded — is inherited by a process running server-controlled code. Exploit precondition: duck.ai (or an edge/CDN layer in its response path) turning malicious or compromised during a `/status` response; impact is arbitrary local code execution with the operator's full credential set. This execution is INHERENT to the challenge protocol — the maintained Go client ships the same shape via a JS runtime — and the `capture` proof mode (a real headless Chromium building its own proof) is the structural fix, but it is UNIMPLEMENTED (MAINT-32) and synth is the default. The house treats exactly this class of boundary seriously elsewhere: plugin sha256 pins (SEC-06/SEC-13), `is_safe_url`, `sanitize_command`.

**Recommendation:** (1) cheap + non-breaking NOW: spawn both node helpers with an allowlisted env (`PATH`, `HOME`, `LANG`, `TMPDIR`, `DDG_SOLVE_UA`) instead of `dict(os.environ)` — the solver needs nothing else; (2) document the trust boundary in `SECURITY.md` next to the plugin-pins section (executing the challenge = opting into duck.ai as a code source); (3) longer term: implement capture mode (MAINT-32) or run the challenge in a restricted realm (ShadowRealm / `vm` context with the browser globals installed and `require`/`process` removed).

**Impact:** A hostile or compromised duck.ai response can achieve arbitrary code execution on the operator's machine with access to every sibling-provider credential in the environment — the keyless backend becomes the highest-value attack surface in the plugin set despite handling no secrets of its own.

---
### Robustness
#### ROB-31: healthy_fallbacks() under ANON_CATALOG ranks models outside the key's entitlement
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/pollinations/pollinations.py` (`healthy_fallbacks` + `_fetch_model_cards`) |
With `POLLINATIONS_ANON_CATALOG=1` (the recommended browse configuration for keyed users), `self._model_cards` holds the full 307-card public feed — including all 173 `paid_only` models the key cannot generate against. `healthy_fallbacks()` sorts that cache with no entitlement awareness: at audit time, 8 of the anonymous top-10 were `paid_only`, two of them holding `success_rate=100` off a single request (the rolling-window weakness). A fallback redirect landing on one of these fails at generation time with 403 — the resilience layer then burns retries and provider-failover on a model that could never have succeeded.
Recommendation: when a key is set, either intersect the fallback candidate set with the entitlement feed (fetch both once), or mark cached cards with the bare `/models` `paid_only` boundary before ordering. Also weigh `success_rate` by `requests` volume (the corrected reference doc's guidance) so 1-request `sr=100` entries stop outranking proven models.
**Impact:** The health-ordered fallback chain — the plugin's flagship feature — can consist mostly of models the key can't use, converting a graceful-degradation path into a guaranteed 403 detour.

---
#### ROB-45: DDG proof-path subprocess failures bypass the backend's error taxonomy
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:494-518` (`_solve_challenge` — 45s subprocess timeout, `json.loads` on stdout), `:520-544` (`_mint_durable` — 20s), call sites `:952` (generate) and `:1162` (generate_stream), both OUTSIDE the retry loop's `try` |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** `generate()` maintains a careful error taxonomy — 401/403/404/418/429/5xx HTTP handlers with remediation text, SSE `ERR_CHALLENGE`/`ERR_CONVERSATION_LIMIT` handling, and the retry ladder — but the proof acquisition that PRECEDES the `try` block can itself fail in ways nothing catches: a missing node raises a clean RuntimeError (handled, good), but `subprocess.TimeoutExpired` (cold node start, loaded machine, the 45s ceiling) and `json.JSONDecodeError` from `json.loads(proc.stdout)` on malformed solver output propagate as raw tracebacks to the REPL/run caller. Same for `_mint_durable` on first chat. This is the INVERSE of the closed ROB-28/30 bare-except class — those swallowed too much; these leak too much.

**Recommendation:** Wrap both helper bodies: `TimeoutExpired` → `RuntimeError("DuckDuckGo challenge solver timed out after 45s — node may be cold or the machine loaded; retry or raise the budget")`; `JSONDecodeError` → RuntimeError naming the solver-stdout contract; both then ride the existing taxonomy so `error_recovery.py`'s transient/permanent classification sees a familiar shape.

**Impact:** A transient node hiccup reads as a framework crash instead of a retryable backend error.

---
#### ROB-46: DDG class-level /chat pacing and lazy durableStream mint are unsynchronized
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:377-378` (class attribute), `:858-876` (`_pace_chat` read-sleep-write, no lock), `:769-770` (`self._durable` lazy mint) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** `_last_chat_monotonic` is a CLASS-level attribute mutated without a lock; `_pace_chat` does read → sleep → write with no synchronization, so orchestrator parallel mode (R07.25 ROB-02's `FIRST_COMPLETED` pool) or the 4-worker parallel tool batches (FEAT-02) holding multiple DDG agents interleave the check and POST concurrently — exactly the back-to-back pattern the pacing exists to prevent (the 2026-10-09 429 storm the comment cites). The same instance races too: two threads calling `generate()` on ONE agent both see `self._durable is None` → two node keygen subprocesses spawn, and one `conversationId` overwrites the other mid-conversation.

**Recommendation:** A module-level `threading.Lock` around the pace read-modify-write (keep the class-level timestamp so cross-instance pacing survives) and around the durable mint.

**Impact:** Under concurrency the anti-429 guarantee degrades to advisory precisely when it matters most.

---
#### ROB-47: generate_stream mid-stream retry appends the restarted turn after already-yielded partial text
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:1123-1275` (text yield `:1187`, retry `continue` `:1275`) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** On an SSE `ERR_CHALLENGE` / HTTP 401 arriving AFTER text deltas were yielded, the attempt-2 retry re-POSTs from scratch and yields the full new turn — the consumer's StreamAccumulator receives partial-turn-1 + full-turn-2 CONCATENATED. The docstring frames "already-yielded text is NOT re-yielded — the retry restarts the turn" as the contract, but the user-visible render is duplication: the retry's full text contains turn 1's content again, regenerated. Streaming is this backend's whole point (FEAT-10), so the path will be exercised in real sessions; a mid-stream challenge error after real text is rare (proofs usually fail at the HTTP layer before any delta) but the 418 ladder's per-turn rotation makes it reachable.

**Recommendation:** Track whether any delta was yielded; if so, re-raise instead of retrying (fail loudly — the error taxonomy already produces a good message), or buffer a prefix before first-yield, or coordinate with FEAT-06 on a stream-reset signal.

**Impact:** A rare-but-real mid-stream proof failure renders as duplicated/garbled text instead of an actionable error.

---
#### ROB-48: SD startup discovery stacks ~30s of sequential probes on a hung endpoint
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/cli/agent_factory.py:187-199` (SD discovery branch — temp backend `list_models()`), `:227-233` (second backend + `get_model_info` cache warm), `agentkthx/plugins/stablediffusion/backend.py:272-281` (`_fetch_capabilities` 10s), `:191-202` (`/v1/models` fallback 10s) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** `_build_agent`'s SD branch runs `temp_backend.list_models()` (capabilities 10s → `/v1/models` 10s when the server half-responds), then constructs the REAL backend, then `backend.get_model_info(model)` (another capabilities 10s). A hung endpoint — accepting connections but never answering — stalls startup up to ~30s before falling back to `"sd-cpp-local"`. This is the same sequential-silent-probe family as Known Landmine #7 (`_probe_remote_catalog` + `_detect_weight_quant` stacking), extended to a backend whose `SD_BASE_URL` env override explicitly invites Colab tunnels and LAN endpoints, where "local" no longer means fast-fail (connection-refused fails instantly; half-open endpoints do not). The two constructed backends also throw away the temp one's warmed cache — the double construction exists only for the fallback model name.

**Recommendation:** Reuse the FIRST backend instance instead of constructing twice (its cache is already warm — kills one probe class entirely), and consider a shared probe deadline across discovery + warm so worst-case startup stays bounded.

**Impact:** A dead Colab tunnel adds ~30s to every `agentkthx chat --backend sd` startup, with the silent fallbacks hiding why.

---
#### ROB-49: --max-steps out of range on the sd backend surfaces as a raw constructor ValueError
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/stablediffusion/backend.py:153-166` (setter `raise ValueError`), `agentkthx/cli/agent_factory.py:215-216` (factory pass-through, no guard) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** `--max-steps 200 --backend sd` reaches `sample_steps` validation inside the `StableDiffusionBackend` constructor and raises `ValueError("sample_steps must be an integer in 1..100 …")` — nothing in `_build_agent`/`cmd_chat` catches it, so the user gets a raw traceback at startup. The house's clean-error-path convention (ROB-36 closed exactly this shape for `_parse_token_size`: raw OverflowError → clean argparse-formatted ValueError) applies: the fail-fast instinct is right, the failure SURFACE is wrong.

**Recommendation:** Guard in the factory before construction — post-parse, when the backend is sd and the value is out of range, raise a clean usage error (`SystemExit(2)` with the message, or re-point the setter's message through `parser.error`); or wrap the `get_backend` call and re-raise as a usage error.

**Impact:** A one-character CLI typo reads as a framework bug.

---
#### ROB-50: SD artifact filename collision silently overwrites a generated image
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/stablediffusion/backend.py:608-621` (`_write_artifact`; `path.write_bytes` at `:620`) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** Filenames are `sd_<UTC YYYYmmdd-HHMMSS>_<NNN>.png` with the counter PER-INSTANCE. Two collision paths exist: (a) `apply_model_switch` rebuilds the Agent — a NEW backend instance whose counter restarts at 001 — so image → `/model` switch → image within the same wall-clock second collides; (b) two processes (two terminals, a script loop) each start at 001. In both cases `path.write_bytes` silently truncates the first image. Lossy-silent is the worst failure mode for an artifact pipeline — the response even reports the (now-overwritten) path as success.

**Recommendation:** Add a uniqueness suffix (`os.getpid()` + a `secrets.token_hex(4)` slice — `_rand_id()`'s DDG precedent) or create with `os.O_EXCL` and bump until unique.

**Impact:** Rapid successive generations (scripted demos, model-switch flows) can lose images with no error anywhere.

---
#### ROB-51: REACT tool verdicts on the system-prompt-suppressing backends are aspirational
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:810-852` (`_strip_system_messages` — DROPS system content) + `:1423-1439` (`test_tool_support` → REACT), `agentkthx/plugins/stablediffusion/backend.py:565-575` (REACT) + `:594-617` (`_flatten_messages` — suppresses system) |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit); SD's docstring already documents the suppression honestly — this finding registers the CAPABILITY GAP, not a hidden decision |

**Detail:** Both backends report `ToolSupportLevel.REACT` (the R07.19 follow-up-#10 convention for "no native tools"), so the factory force_reacts and assembles the full ReAct system prompt — tool list, Action/Action Input grammar, format instructions — which BOTH backends then drop at the wire (DDG: system messages DROPPED after the live jailbreak-lecture finding, a deliberate R07.30 protocol decision; SD: suppressed as diffusion-irrelevant). The model never sees the tools or the grammar; tool RESULTS still fold into user turns, but nothing ever tells the model tools exist or how to request one. Net effect: the models table advertises `tools ○ react`, an agent session starts with the tool registry wired, and the tools silently cannot engage — the "advertised ≠ effective" class the register tracks.

**Recommendation (pick one):** (a) fold a MINIMAL tool notice into the last user turn — tool names + "to use a tool, emit Action:/Action Input:" — bounded, per-turn, never the full harness prompt (keeps the jailbreak-lecture lesson intact); (b) keep the suppression and surface a one-time session notice ("tools unavailable on this backend — scaffolding is not transmitted"); (c) document the gap in `docs/SUPPORT.md`'s tier table with an explicit "tools: not effective" cell for both backends.

**Impact:** Users selecting a tools-capable mental model get silent no-op tools on two backends.

---
### Maintainability
#### MAINT-01: `cmd_chat` is a 1,733-line single function with 30+ nested closures and no slash-command dispatcher
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/commands/chat.py:1-1733` |
`cmd_chat` is a single function spanning 1,733 lines (1,307 at R07.16, 1,368 at R07.18 — R07.19 added the Primary User naming flow, the `/souls` + `/soul` handlers, the `/models` picker + startup-picker path, and their helpers: `_resolve_primary_user`, `_sanitize_primary_user`, `_model_menu_labels`, `_report_model_switch`, `_interactive_model_switch`, `_startup_model_pick`), with 30+ nested closures (`_footer_line1`, `_footer_line2`, `_footer_text`, `_setup_footer_region`, `_teardown_footer_region`, `_update_footer`, `_position_for_input`, `_spinner_thread`, `_spinner_start`, `_spinner_stop_thread`, `_init_acp` rebind, `_build_agent` rebind, etc.). The slash-command handlers (`/help`, `/security`, `/system`, `/tools`, `/tool`, `/skills`, `/skill`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`) are inline `if user_input == "/X"` blocks — there's no command dispatcher. The function is too large to test in isolation; tests for chat behavior (e.g., `test_agent_mode_*.py`) use heavy monkeypatching. This was the next biggest structural debt after the R07.00 `cli.py` and `agent.py` extractions.
Recommendation: Extract `ChatSession` class with `handle_command(text) -> bool` dispatcher. Extract `Footer` class for the scroll-region logic. Extract `Spinner` class for the thread. Each slash command becomes a method. Target: `cmd_chat` becomes ~50 lines of orchestration; tests can construct a `ChatSession` and feed it simulated input.
**Impact:** Any change to chat UX requires touching this 1,700-line function; chat slash-command behavior is impossible to unit-test without monkeypatching.
---

#### MAINT-27: New `/sh` slash command added R07.21 — inline if/elif branch in cmd_chat (MAINT-01 family)
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/commands/chat.py:1530-1611` (new /sh branch + `/help` entry at :560-561); `tests/test_r07_21_sh_command.py` (new, 13 tests); `docs/USAGE.md` (new §"/sh — Local Shell Command" subsection) |
| **Status** | OPEN — the command ships and works; this finding tracks the structural debt (the inline-branch pattern, not the feature itself) |
New in R07.21. The `/sh` slash command runs a local shell command, displays the output, and (by default) injects the output into the agent's context as a user-role message so the model can use it on the next turn. Pass `-n` before the command to display-only (skip the context injection). The command reuses the built-in `shell()` tool from `agentkthx.tools.builtins`, so the same security checks (`sanitize_command`), timeout clamping (max 300s), and exit-code formatting apply — output is formatted as `<shell_output command='...'>...</shell_output>` when injected so the model can parse it cleanly.

The implementation adds a ~80-line inline `if user_input == "/sh" or user_input.startswith("/sh "):` branch to `cmd_chat`, joining the existing inline if/elif chain (no slash-command dispatcher — MAINT-01 family). The branch parses the `-n` flag manually, calls `shell()` directly, prints the output, and calls `agent.memory.add("user", context_msg)` to inject. The 13-test regression file `test_r07_21_sh_command.py` covers: (1) the -n flag is parsed correctly in all reasonable invocations (bare `/sh`, bare `/sh -n`, simple commands, complex commands with pipes, quoted args); (2) the context-injection call happens (or doesn't, when -n is passed); (3) the integration with the real `shell()` builtin works (echo produces output, false includes exit-code marker, ls on a nonexistent path includes the error).

Recommendation: when MAINT-01 lands (extract `ChatSession` from `cmd_chat`), the `/sh` branch should become a `cmd_sh(session, args)` method on the session object — same shape as the other slash commands. The manual `-n` parsing should move to argparse once the slash-command dispatcher exists (the pattern would be `sh -n <command...>` with `n` as a `store_true` flag). Until then, the inline branch is the established pattern and the regression tests pin the contract.
**Impact:** The `/sh` command itself is a useful UX addition (lets the user feed local context to the model without leaving the chat loop); the structural debt is the same MAINT-01 family — every new slash command deepens the case for the dispatcher extraction.
---

#### MAINT-31: Remaining CloudBackend dedup backlog — catalog/service shapes and legacy request loops still duplicate
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/plugins/mistral/mistral.py` (`_make_api_request` :780s, `_iter_sse_lines`, `_fetch_live_models`), `agentkthx/plugins/pollinations/pollinations.py` (`_make_api_request`, `_fetch_live_models` card pipeline), `agentkthx/plugins/zai/zai.py` (`_generate_with_auth` :608+, `test_tool_support` :247+, `_fetch_live_models`), `agentkthx/plugins/orcarouter/orcarouter.py` (`_generate_with_auth`, `test_tool_support`), `agentkthx/plugins/nvidia/nvidia.py` + `cloudflare.py` (`list_models`, `test_tool_support`, `_fetch_live_models`), `agentkthx/plugins/{openai,gemini,huggingface,openrouter}/*.py` (`_max_429_retries` ×4, `_429_backoff` ×4, `_apply_free_only`) |
| **Status** | OPEN — registered 2026-10-09 during the R07.28 batch-2 lift; found by the cross-backend structural-diff analysis (method-by-method hashing across all 10 cloud backends) |

**Detail:** R07.28 batch 1 collapsed the retry-loop ERROR handlers into CloudBackend; batch 2 moved the request LOOPS themselves (`_make_api_request`/`_iter_sse_lines`/`generate_stream`/`_jev_call_completions`) plus the catalog-key hook. What remains duplicated, sized by the post-batch-2 analysis (~700–900 LOC): (1) **`list_models` ×6** across the CloudBackend children (~440 LOC) — every child repeats L1 in-process cache → L2 `model_cache.get_cached_models` → `ensure_seeded` → try `_fetch_live_models` → stale-first service → seed fallback; hooks needed: a post-cache-hit hook (Pollinations' raw-card rehydration) and a catalog-filter hook (Cloudflare's cf-paid filter, the FREE_ONLY backends). (2) **`test_tool_support` ×7** name-pattern implementations (nvidia/cloudflare/orcarouter/openai/gemini/openrouter/huggingface) — same NATIVE/REACT-from-regexes shape with different pattern tables; only ZAI runs a real probe (169 LOC override). A `_tool_support_patterns()` table hook would collapse the seven AND structurally fix MAINT-29's doc-drift class (one honest shared docstring). (3) **`_fetch_live_models`**: mistral/zai/nvidia share an identical GET → `data[]` → merge-with-catalog transport (cloudflare's envelope + pollinations' card pipeline genuinely differ) — lift the transport, keep the merge as a hook. (4) **Legacy request loops never migrated to the MAINT-28 machinery**: mistral `_make_api_request` (~127 LOC) inline re-implements `_parse_error_envelope`, which the base has owned since batch 1; pollinations (~99 LOC) keeps its own retryable-set/backoff inline; zai `_generate_with_auth` (~222 LOC, the biggest holdout) hardcodes the URL and headers instead of `_get_chat_completions_url()`/`_get_auth_headers()`; orcarouter (~157 LOC) is closer but still its own loop. (5) **`_apply_free_only` ×7** and **`_max_retries` ×2** — identical bodies differing only in env-var names. (6) The four OpenAICompatibleBackend-direct siblings (openai/gemini/huggingface/openrouter — NOT CloudBackend heirs) duplicate the 429-retry family: `_max_429_retries` ×4 identical modulo env name, `_429_backoff` ×4 in two flavors (fixed schedule ±20% jitter vs full-jitter) — this half belongs in `openai_compat.py`, not `cloud_base.py`.

**Recommendation:** Land in two moves: (a) `list_models` + `test_tool_support` unification via hooks BEFORE the SiliconFlow/DuckDuckGo scaffolds (they would otherwise copy the shapes again — the exact MAINT-28 failure mode); (b) migrate mistral/pollinations request loops to the shared skeleton (mostly deletion — the base owns the pieces) and fold zai/orcarouter loops into the shared path as follow-up. The OpenAICompat 429-family goes to `openai_compat.py` as a separate small batch.
**Impact:** ~700–900 LOC of further dedup; drift risk is live (the "limit" docstring drift fired once already, and mistral's inline envelope parsing has already diverged from the base's `_parse_error_envelope` behavior); every new cloud backend scaffolded before (a) lands copies the shapes again.
---
#### MAINT-32: DUCKDUCKGO_PROOF_MODE=capture is a silent no-op — phantom helper file, overselling docstring
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/plugins/duckduckgo/duckduckgo.py:102-108` (module docstring — capture described in present tense), `:172` (`_CAPTURE_JS` path constant), `:198-203` (`_PROOF_MODE` parsed at import, never consumed), `:484-492` (`_require_helper` remediation names ddg_capture.js), `agentkthx/cli/commands/config.py:272-277` (the ONLY honest surface — "RESERVED … NOT YET IMPLEMENTED") |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** `DUCKDUCKGO_PROOF_MODE=capture` — the headless-Chromium proof-lift mode the docstrings present as an available alternative — is not implemented: `_PROOF_MODE` is parsed at module import and never read anywhere (no code path branches on it), and `_CAPTURE_JS` names `ddg_capture.js`, which is NOT in the repository (`git ls-files agentkthx/plugins/duckduckgo/` → only `ddg_vqd.js`, `ddg_durable.js`, `duckduckgo.py`, `plugin.json`, `__init__.py`; the R07.30 changelog's "316 LOC of bundled Node helpers" = ddg_vqd.js 276 + ddg_durable.js 40). The `_require_helper` remediation text even tells users to "copy ddg_capture.js if you use capture mode from the repo's agentkthx/plugins/duckduckgo/" — a file that does not exist there. A user setting the env var gets synth behavior with no notice (a silent no-op of a documented mode), and the false remediation fires only if the path is ever checked. Doc drift with teeth: this is also the SEC-21 mitigation path, so the gap masks the security hardening option.

**Recommendation:** Either implement capture mode (it is the structural SEC-21 fix — prioritize on that basis), or mark every mention RESERVED to match `config.py`'s honest label and drop the phantom remediation text until the file ships.

**Impact:** A documented-but-unimplemented mode silently ignores its own env var, and the docstring actively misrepresents the trust-hardening option.

---
#### MAINT-33: BackendType.DUCKDUCKGO enum comment still teaches the retired x-vqd-4 protocol and the 2025 catalog
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/core/types.py:202-216` |
| **Status** | OPEN — registered 2026-10-10 (R07.31 re-audit) |

**Detail:** The enum member's comment ("custom x-vqd-4 header-token handshake (bootstrap via GET /status, rotate on every /chat response) … Catalog: GPT-4o mini, o3-mini, Claude Haiku, Llama 3.3 70B, Mistral Small 3 24B — the upstream frontier set") is pre-rotation scaffold text that never survived the protocol rewrite it now misdescribes: x-vqd-4 is GONE (replaced by the challenge-based x-vqd-hash-1 proof executed by the bundled Node solver), and that 2025 catalog now 404s with ERR_MODEL_UNAVAILABLE — the live set is the 8 reseeded wire IDs (gpt-6-luna, gpt-5.6-luna, gpt-5.4-nano/mini, claude-haiku-4-5, mistral-small-2603, tinfoil/gpt-oss-120b, tinfoil/gemma4-31b). Same doc-drift class as the cloudflare "limit" docstring drift structurally fixed in R07.28 — and the enum is the FIRST contract surface a new contributor reads.

**Recommendation:** Rewrite the comment to the shipped protocol (x-vqd-hash-1 challenge/proof, role-based SSE, the 8-model catalog) or replace the protocol prose with a pointer to `docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md` so it cannot drift again.

**Impact:** The canonical type definition teaches a dead protocol and a dead catalog.

---

### New Features
---
#### FEAT-03: Tool output schema validation via JSON Schema
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/core/tool_execution.py:84`, `agentkthx/core/models.py:Tool` |
Grounded in observation: `core/tool_execution.py:84` `result = tool.execute(**normalized_args)` returns `Any`; the agentic loop treats it as `str(result)`. Tools can return dicts, lists, exceptions, or `None`. There's no contract between tool implementation and the agent loop.
Proposal: Add an optional `output_schema: dict | None` field to `Tool` (JSON Schema). When set, `tool.execute()`'s return value is validated against the schema; mismatches trigger a `ToolOutputError` that the error recovery tracker records. This enables: (a) structured tool results that the model can parse reliably, (b) automatic JSON-serialization for the `function_call_output` item, (c) contract testing for tool implementations.
**Impact:** Makes tool outputs predictable and machine-parseable; enables type-safe tool composition.
---

#### FEAT-05: Plugin sandboxing via restricted `register()` namespace + audit hooks
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/_loader.py:864`, `agentkthx/plugins/*/plugin.json` |
Grounded in SEC-06 (external plugins executed with no path restriction). `plugins/_loader.py:864` `module.register(self)` gives the plugin full access to the PluginManager — plugins can introspect other plugins, mutate global state, or import arbitrary modules at register time.
Proposal: Add a `PluginSandbox` wrapper that exposes only a restricted API to `register(manager)`: `register_backend`, `register_tool`, `register_cli_command`, `register_hook`, `register_config_defaults` — but NOT `manager._plugins`, `manager._manifests`, `manager.discover()`, or `manager.load()`. Plugins receive the sandbox, not the raw manager. Add an optional `permissions` field to `plugin.json` (`["network", "filesystem:/tmp", "subprocess"]`) that the sandbox enforces via `sys.addaudithook` (Python 3.8+).
**Impact:** Limits blast radius of malicious plugins; makes the plugin trust boundary explicit and configurable.
---

#### FEAT-06: Streaming tool-call argument deltas (`function_call_arguments.delta` SSE events)
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/core/openresponses.py:932-1001`, `agentkthx/core/streaming.py:731-748` |
Grounded in observation: `core/openresponses.py:932-1001` `stream_function_call_events` exists but is never called from the agentic loop — the loop waits for the full response before parsing tool calls. The OpenAI Responses API streams `function_call_arguments.delta` events.
Proposal: In `_generate_stream` (streaming.py:731-748), when a `tool_calls` delta arrives, emit a `FUNCTION_CALL_ARGUMENTS_DELTA` SSE event immediately (via `stream_function_call_events`). This lets ACP clients and OpenResponses-compatible UIs show the model "typing" the tool arguments in real-time, improving UX for long tool calls (e.g., `write_file` with large content).
**Impact:** Parity with OpenAI Responses API streaming; improves UX for chat clients that support streaming.
---

#### FEAT-07: Conversation export/import to OpenResponses-format JSON
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/core/persistent_memory.py` (target of change) |
Grounded in observation: `core/persistent_memory.py` stores messages in SQLite with a custom schema; there's no way to export a conversation for sharing or migration. Users who want to share a bug reproduction, migrate to a different backend, or version-control conversations have to manually extract from SQLite.
Proposal: Add `agent.export_session(session_id) -> dict` that returns the conversation as an OpenResponses-compatible JSON (`{responses: [...], items: [...], usage: {...}}`). Add `agent.import_session(data: dict)` that reconstructs the Memory. CLI: `agentkthx sessions export <id> > conv.json` and `agentkthx sessions import < conv.json`.
**Impact:** Enables conversation portability, bug reproduction, and audit logging; aligns with OpenResponses spec.
---


#### FEAT-08: paid_only free-TIER filter mode unreachable — bare /models boundary never fetched
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/pollinations/pollinations.py` (`_card_is_free`, `list_models`) |
`POLLINATIONS_FREE_ONLY` filters on `_card_is_free()` — the zero-cost tier (currency-only pricing, 16 models live). But Pollinations' practical "free" surface is the free TIER: every `paid_only != True` model (102 text cards, Quest-Pollen-eligible — exactly what the keyed entitlement feed returns and what enter.pollinations.ai labels "free"). That boundary lives only on the bare `GET /models` feed (`paid_only` field), which the plugin never fetches. Result: FREE_ONLY hides ~86 Quest-Pollen-runnable models from users whose key can actually use them.
Proposal: a `POLLINATIONS_FREE_TIER=1` mode (or tri-state FREE_ONLY = off | zero-cost | tier) that fetches the bare feed once, marks cached cards with the tier boundary, and filters/redirects on it. `scripts/probe_pollinations.sh` already demonstrates the full discovery logic to port.
**Impact:** Users who want "everything my key can run without paying cash" get a 16-model subset of the ~102-model tier they're entitled to browse.
---

#### FEAT-09: SiliconFlow `enable_thinking` + `repetition_penalty` request-field passthrough
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/siliconflow/siliconflow.py` (`generate` — `think` accepted-but-ignored; `_tweak_request_body` inherited no-op) |
SiliconFlow documents two request fields the R07.29 scaffold does not forward: `enable_thinking` (boolean — switches hybrid Qwen3/GLM models between thinking and non-thinking modes; the second provider after ZAI to expose a thinking on/off switch) and `repetition_penalty` (vLLM-style passthrough; the AgentKthx `repeat_penalty` kwarg maps onto it 1:1). Both are dropped deliberately at scaffold time: cloud backends drop `repeat_*` by house convention (the OpenAI allowlist), and `think` is accepted-but-ignored like NVIDIA/ZAI. The gap: mapping `think` onto `enable_thinking` and `repeat_penalty` onto `repetition_penalty` must touch BOTH `_build_stream_body` (streaming) AND `_tweak_request_body` (non-streaming + JEV) — touching only one reproduces the MAINT-22 drift class (Mistral's `random_seed`/`safe_prompt` silently diverging between paths until R07.24).
Proposal: override `_build_stream_body` to delegate through a shared `_build_siliconflow_body` that appends the two fields from kwargs, and override `_tweak_request_body` to apply the same appends on the non-streaming/JEV path (it fires once per request from the shared `_make_api_request`). Gate `enable_thinking` on the thinking-capable name patterns to avoid 400s on non-hybrid models.
**Impact:** Users lose the API's thinking-mode toggle and anti-loop sampling knob on the one provider that supports both per-request; wiring them is the difference between parity and passthrough.
---

### Architecture
<!-- ARCH-02, ARCH-03, ARCH-04, ARCH-05, ARCH-06 all CLOSED in R07.13.
     Detailed entries moved to deltas.md per the established convention
     for archived findings. The R07.13 closure batch closed all 5
     OPEN Architecture findings in one pass. -->
### Testing
#### TEST-01: No integration tests — all 984 tests are mocked unit tests; slash-command dispatcher untested
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (entire directory) |
All 2,608 tests are mocked unit tests — there's no integration tier. `tests/test_agent_mode_*.py` tests the `AgentMode` class, but there's no test for `cmd_chat`'s handling of `/security`, `/tool`, `/skill`, `/souls`, `/soul`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`. These are 15+ slash commands with non-trivial logic (e.g., `/param` has a per-backend `PARAM_MATRIX` dict). `test_agent.py` tests the Agent class but not the CLI layer. Coverage baseline: 42.7% line coverage (R07.01) — concentrated on the most-tested modules; the CLI commands and the chat dispatcher are well below.
Recommendation: Add `test_chat_commands.py` that feeds simulated user input to a mock `cmd_chat` and asserts the output. Add a record/replay integration tier: run `agentkthx chat --backend=test-backend --record` to capture backend responses, then `--replay` to re-run without network. Target: 60% coverage on `cli/commands/chat.py` and `cli/commands/run.py`.
**Impact:** Regressions in slash-command behavior go undetected; coverage gaps in the CLI layer are unknown.
---

#### TEST-03: `FakeBackend` in `test_agentic_loop_subsystem.py` omits `generate_completions_stream`
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/test_agentic_loop_subsystem.py:18-37` |
The `FakeBackend` class (line 18-37) deliberately omits `generate_completions_stream` so the streaming path falls back to `generate()`. The test comment (line 24-28) says "this keeps these tests focused on LOOP equivalence. SSE parsing itself is covered by test_streaming.py." However, this means the streaming-specific callbacks (`on_step_start`, `on_generated`, `on_tool_executed`, `on_tool_result_committed`) are never exercised in the loop-equivalence tests.
Recommendation: Add a `FakeStreamingBackend` that yields chunks via `generate_completions_stream`. Test that the streaming callbacks fire in the expected order with the expected arguments.
**Impact:** Streaming callback bugs (e.g., the R06.58 between-calls compaction bug) aren't caught by the loop tests.
---

#### TEST-04: No test coverage for `agent_mode.py` rollback functionality
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `agentkthx/agent_mode.py` (822 LOC untested) |
`agent_mode.py` has 822 LOC implementing `Action`, `Step`, `TaskPlan`, `AgentMode` with rollback support (`create_file_write_action` stores original content, `create_file_delete_action` moves to temp, `create_shell_action` runs an `undo_command`). There's no test file `test_agent_mode_rollback.py` — only `test_agent_mode_verbosity.py` and `test_agent_mode_footer.py` which test display, not rollback.
Recommendation: Add tests that create a file via `create_file_write_action`, roll back, and verify the original content is restored. Test rollback chains where Step N's rollback depends on Step N-1.
**Impact:** The rollback feature (a key selling point of "agent mode") is untested; regressions would go undetected.
---

#### TEST-05: `test_bump_version_script.py` tests shell script via subprocess — fails on Windows/no-bash
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_bump_version_script.py` (203 LOC) |
The test runs `scripts/bump-version.sh` as a subprocess and asserts the output. This is fragile — it depends on `bash` being available, the script being executable, and the repo being in a git checkout.
Recommendation: Extract the version-bump logic into a Python function (`scripts/bump_version.py:main(args)`) and test that directly. The shell script becomes a thin wrapper: `python3 -m scripts.bump_version "$@"`.
**Impact:** Test fails on Windows (no bash) and in CI environments without git; limits portability.
---
#### TEST-07: No test for `update_check` module's network-failure paths
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_update_check.py` (537 LOC), `agentkthx/update_check.py` |
`update_check.py` has `test_update_check.py` (537 LOC) but the tests mock `_urlopen` to return canned responses. There's no test for what happens when `urlopen` raises `URLError` (network down), `socket.timeout`, or returns malformed JSON.
Recommendation: Add tests that inject `URLError`, `socket.timeout`, and malformed-JSON responses. Verify the module returns gracefully without crashing the CLI.
**Impact:** Update check may crash on network edge cases — bugs only surface in production.
---
#### TEST-10: No live-shape contract test for free-model detection
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_pollinations_backend.py` |
The v0.1.2 bug is the evidence: `_card_is_free()` required zero-valued price fields, the live gateway encodes zero-cost as currency-only dicts, and the entire 83-test suite stayed green while FREE_ONLY listed 0 models on every real feed — the fixtures encoded a price-0 shape that never occurs live. The user's "website shows 30 free models, the API shows none" report surfaced it. TEST-09's pattern (user testing catching what fixtures mask), one release later.
Recommendation: a live-gated contract test (skips without `POLLINATIONS_API_KEY`): fetch the real `/v1/models`; if any card carries currency-only pricing, assert `_card_is_free()` is True for it and FREE_ONLY listing is non-empty. Cheap, runs in CI when the secret is configured, and fails the moment the gateway re-encodes the boundary.
**Impact:** Gateway encoding drift is invisible to the suite until a user reports it — the exact class of bug v0.1.2 shipped with.
---

#### TEST-12: No live-shape contract test for the SiliconFlow scaffold
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_siliconflow_backend.py` (106 mocked tests), `scripts/smoke_test.sh` (siliconflow step) |
The R07.27 Cloudflare scaffold is the precedent: its docs-vs-reality mismatch (the model ID lives in `name` not `id`; the category is `task.name == "Text Generation"` not a lowercase `type`) was caught only by the maintainer's first live `agentkthx models --backend cf` — the 88 mocked tests stayed green on the documented shape. The SiliconFlow scaffold is in the same posture: 106 mocked tests pin the documented OpenAI `{data: [...]}` envelope, the plain-string 401/404/504 bodies, the heterogeneous error shapes, and the 30-model seed — but the live `/v1/models` field set, the actual 429 wording, and whether `/v1/models` carries context metadata are all unverified until a key-holding user or the maintainer runs the smoke step. 2026-10-09 update: the maintainer's smoke run ATTEMPTED — auth, the 79→58 catalog (parity with the probe), and one `--think` generation verified before the account balance drained; both tool-call steps returned 402 "Sorry, your account balance is insufficient", now pinned by `test_402_balance_exhaustion_live_evidence` + the zero-retry full-loop test — the remaining unknowns are the tools/429 live shapes on a topped-up key.
Recommendation: after the first `./scripts/smoke_test.sh --backend siliconflow` pass, add a live-gated contract test (skips without `SILICONFLOW_API_KEY`, the TEST-11 `AGENTKTHX_LIVE_TESTS=1` pattern): fetch the real `/v1/models`, assert the OpenAI list envelope + non-empty chat subset after the blocklist, and pin the first observed 429 body wording against `_looks_like_balance_exhaustion`'s classifier split.
**Impact:** Live-shape drift (the Cloudflare class) surfaces via user bug reports instead of CI — one cheap gated test closes the gap after the first smoke run.
---

#### TEST-13: No live-shape verification for the DuckDuckGo scaffold (egress-blocked probe) — PARTIALLY CLOSED 2026-10-09
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_duckduckgo_backend.py` (99 mocked tests), `scripts/probe_duckduckgo.py` (the live probe tool) |
| **Status** | OPEN — handshake live-verified; one live plugin turn remains |
The scaffold was built with the user's "live probe first, seed only what answers" directive, but the maintainer sandbox could not reach duckduckgo.com at all — every DDG property (duckduckgo.com, www, duck.ai, html., lite.) times out while github.com and api.siliconflow.com answer, i.e. a whole-IP-range egress block, most likely DDG's datacenter-IP anti-abuse list. The original pivot (mumu-lhl v3.3.0 + mrgick/duck_chat source verification) is now HISTORICAL: on first live contact the ENTIRE x-vqd-4 protocol proved retired. The 2026-10-09 reverse-engineering pass (ddg-challenge-solver package, run live from an unrestricted network) verified the NEW handshake end-to-end: /status challenge fetch, Node solve at 54–987ms per challenge (repeatedly, stable), and a built X-Vqd-Hash-1 accepted by POST /chat — the server's 404 ERR_MODEL_UNAVAILABLE JSON body (a request-shape-aware error, not a 401/403) proves auth + body parsing passed and isolates the failure to the model-ID string, which was then reseeded from the live dropdown + the benoitpetit/duckduckgo-chat-cli models.go cross-check. The 99 tests pin the CURRENT protocol shapes (challenge lifecycle, proof header, role-based SSE, durableStream body, error taxonomy).
Recommendation: run `python3 scripts/probe_duckduckgo.py --live` from an unrestricted network — it drives the backend itself (challenge solve → capabilities dump → one minimal generate per seed model → seed-catalog JSON emit). A single 200 SSE turn closes this finding; reconcile any live wording drift into the error-taxonomy tests.
**Impact:** If the 8 reseeded wire IDs drift again before the first live run, the backend surfaces its catalog-rotation remediation message (404 path) rather than failing silently — and `fetch_capabilities()` (GET /duckchat/v1/capabilities, proof-gated retry) provides the live-catalog escape hatch without a release.
---

<!-- Filed at the R07.23 re-audit (commit 1d7f1ee). All three from the new mcp search + install surface. -->

## Priority Matrix
| Timeline | Findings |
|----------|----------|
| **Near term (R07.31–R07.32)** | SEC-21's env-allowlist hardening first (one small non-breaking diff: allowlisted env for both node spawns + a SECURITY.md trust-boundary note — the solver needs only PATH/HOME/LANG/TMPDIR/DDG_SOLVE_UA), then the cheap follow-on batch: ROB-49 (clean argparse error for out-of-range --max-steps on sd), ROB-50 (artifact filename uniqueness suffix), ROB-48 (construct the SD backend ONCE — the temp instance's warm cache kills a probe class), MAINT-32 (either implement capture mode — the structural SEC-21 fix — or mark every mention RESERVED and drop the phantom remediation text) + MAINT-33 (enum comment rewrite to the shipped protocol). Live-gated pending: a topped-up `./scripts/smoke_test.sh --backend siliconflow` pass (closes TEST-12's remaining tools/429 shapes) and one live 200 SSE turn via `scripts/probe_duckduckgo.py --live` (closes TEST-13). Standing Mediums: ROB-31 (entitlement-aware fallback filter — Limited Support backend) and MAINT-01 (cmd_chat dispatcher — the standing Medium; now 1,946 lines) |
| **Short term (R07.32–R08.00)** | ROB-45 (wrap the DDG node subprocesses in the error taxonomy), ROB-46 (lock the pacing read-modify-write + the durable mint), ROB-47 (no mid-stream retry after the first yielded delta), MAINT-27 (move `/sh` branch to a `cmd_sh` method when MAINT-01 lands), FEAT-03 (tool output JSON Schema), FEAT-09 (SiliconFlow enable_thinking + repetition_penalty passthrough — both paths), TEST-01 (integration tier), TEST-03 (streaming-capable FakeBackend), TEST-10 (live-shape free-model contract test), TEST-12 (SiliconFlow live-gated contract test, after the first smoke run) |
| **Medium term (R08.00+)** | FEAT-05 (plugin sandbox), FEAT-06 (streaming tool-arg deltas), FEAT-07 (conversation export/import), FEAT-08 (paid_only free-TIER filter mode), TEST-04 (rollback tests), TEST-05 (bump-version test portability), TEST-07 (update_check failure paths) |
Closed/wontfix placements from earlier revisions are archived in `deltas.md`'s closure timeline (R07.00 → R07.25). The R07.21 closure batches closed 16 findings total; R07.24 closed 13 across three batches (SEC-20, ROB-28, ROB-41, TEST-11, MCP-01/03/04/05, MAINT-03/22/23, ROB-29/33) + 2 WONTFIX (MCP-02, SEC-13); R07.25 closed 3 (ROB-02, ROB-06, ROB-15) + 1 WONTFIX (SEC-09) + TEST-09. R07.26/R07.27 closed none (feature releases) and added the six findings registered in the R07.26–R07.27 re-audit. R07.28 closed ROB-42 + MAINT-28 (23 regression tests in `tests/test_r07_28_batch1_closures.py`) plus the four quick wins in batch 3 (+15 tests in `tests/test_r07_28_batch3_quick_wins.py`). R07.29 registered FEAT-09 + TEST-12 from the SiliconFlow scaffold (no closures — a feature release). R07.30 closed FEAT-10 (true generate_stream via the protocol-rotation rewrite) and registered TEST-13. The R07.31 re-audit (2026-10-10) registered ten findings from the DuckDuckGo + Stable Diffusion surface (SEC-21 + ROB-45..51 + MAINT-32/33) with no closures; register 146 findings — 28 OPEN / 108 CLOSED / 10 WONTFIX (118 archived).
Guidelines for timeline assignment:
- **Near term** — High severity findings and the most impactful Medium severity findings; should be fixed in the next 1-2 releases
- **Short term** — Medium severity findings addressable within 2-4 releases
- **Medium term** — Low severity findings and larger architectural changes that can be picked up during other work
---

## Architecture Strengths

- **The split-register audit tooling is self-hosting** (`audit/`): `split-audit.py` (idempotent open/closed split with dry-run), `verify_open_findings.py` (heuristic re-verification of every OPEN finding — pattern presence, file existence, cross-file drift, silent-closure git scan), and `generate_audit_dash.py` (merges `audit.md` + `deltas.md` into a dashboard + JSON endpoints with a reconcile drift-checker). The workflow is documented in the project's own shipped skill (`agentkthx/skills/codebase-audit/`) — the project audits itself with its own tooling, and every closure since R07.00 is reconstructable from `deltas.md`'s timeline.
- **Capabilities-first tool-support detection with a none-proof normalizer** (`core/types.py`, `core/tool_cache.py`, `backends/ollama.py`): one authoritative `/api/tags` capabilities read feeds a single `tools` column through one cache namespace; `ToolSupportLevel.effective()` normalizes every would-be none verdict to REACT at every surface (detection, cache read, table render, `agent_factory`) — the "model shows none" bug class is structurally closed, pinned by 53 tests in `test_r07_19_single_tool_col.py`.
- **The ZAI probe's error taxonomy enforces its contract in code, not prose** (`plugins/zai/zai.py`): transient (429/5xx/rate-limit text/network/empty-choices) → retry with backoff and persist UNTESTED WITHOUT caching; definitive 400 tools-rejection → REACT; auth → UNTESTED uncached. "Capability unknown is not capability absent" is a property of the control flow — and the probe body itself (`thinking: disabled` + `max_tokens 512`) documents the live failure mode it was built against.
- **Fail-safe best-effort contracts are consistent and tested**: `build_environment_section()` returns `""` on ANY probe failure (same contract as the soul loader), BitNet sessions get a compact single line to respect the lean-prompt crash threshold, and `test_r07_19_primary_user_env.py` pins every OS family's output shape plus the kill-switch env var.
- **`picker.py` is structured for terminal-free testing**: rendering is pure strings, navigation is plain state mutation, the POSIX path is a thin cbreak wrapper with a guaranteed `finally` termios restore (:330-338), and a zero-third-party-import AST pin keeps the module stdlib-only forever.
- **Test discipline at scale**: 84 test files / ~37,147 LOC / 2,608 tests in ~13s; every regression ships with a dedicated file; source-level pins guard contract-critical strings (the bare-ESC prompt form can never return; the help-sort walk auto-expands to new subparsers); lint (pinned ruff + black) is a REQUIRED CI check, not advisory.
---
