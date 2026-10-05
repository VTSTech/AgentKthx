# Improvement & Enhancement Audit
**AgentKthx v0.7.24 (R07.24 — closure batch: SEC-20 `mcp install --no-overwrite`, ROB-41 `search_all_with_errors` + network-error hint, TEST-11 live-gated mcp contract test, ROB-28 MistralBackend catch-narrowing — four findings closed in one pass)**
**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-10-06  
**Commit:** `R07.24` (in-progress) | **Test Suite:** 2912 passed / 20 skipped in ~22s  
125 Findings | 30 Open | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  
Severity (open): 0 High | 11 Medium | 19 Low  
30 OPEN | 95 archived in deltas.md (88 CLOSED + 7 WONTFIX) — generate_audit_dash.py merges both for the dashboard

> **R07.24 closure batch (2026-10-06, this pass — in-progress):** Closure batch — four OPEN findings closed in a single surgical pass, all non-breaking fixes with clear patterns. SEC-20 (`mcp install --no-overwrite` flag — refuses to clobber an existing mcp.json entry with rc=5; `--dry-run` now prints `WOULD OVERWRITE` when the target exists, ~50 LOC across `parser.py` argparse + `_mcp_install` handler; 4 regression tests in `tests/test_mcp_cli.py`); ROB-41 (`registry.search_all` swallow-failures split into `search_all_with_errors` returning `(results, errors)` tuple — back-compat `search_all` is a thin wrapper that drops errors; plain-mode `mcp search` now leads with `Network error searching for '<query>':` + actionable hints (`--refresh`, connection check, `AGENTKTHX_GITHUB_TOKEN`) instead of the misleading `No MCP servers found` + `Try a broader query`, ~30 LOC in `registry.py` + 20 LOC in `_mcp_search`; 5 regression tests in `TestSearchAllWithErrors`; existing `test_search_handles_network_error_gracefully` updated to assert the new (correct) message shape); TEST-11 (`tests/test_mcp_live_contract.py` — new 4-test live-gated file skip-on-`AGENTKTHX_LIVE_TESTS`-unset; asserts the documented npm + GitHub response field set per source so a `package.name → package.id` rename ships green-on-mocked-suite but red-on-live-contract; verified live against real `registry.npmjs.org` + `api.github.com`); ROB-28 (MistralBackend `list_models` catch narrowed from `except Exception` to `except (HTTPError, URLError, JSONDecodeError)` — programming errors KeyError/AttributeError now propagate as real bugs with tracebacks instead of being masked as 'discovery failed'; 5 regression tests in `TestListModelsCatchNarrowing` pinning both the caught-and-degrades cases AND the now-propagates cases). Suite: 2899 → 2912 passed (+13 active) / 16 → 20 skipped (+4 live-gated). Zero regressions; ruff + black clean. Register: 125 findings — 30 OPEN / 88 CLOSED / 7 WONTFIX (95 archived, ~76%).
> **R07.23 re-audit delta (2026-10-06, commit 1d7f1ee):** All 26 carried-forward OPEN findings re-verified against the current tree via `verify_open_findings.py`: 21 STILL_OPEN_LIKELY, 2 PATTERN_GONE (ROB-06 — false positive, the `stream_gen.close()` block shifted from `streaming.py:972-990` to `:1005-1020` due to R07.19/R07.20/R07.22/R07.23 line growth but the pattern survives; FEAT-03 — expected, the `output_schema` field is still absent from `core/models.py:Tool`), 2 FILE_EXISTS_NO_PATTERN (FEAT-07, TEST-05 — feature/file proposals where no code pattern is expected), 1 UNKNOWN (TEST-01 — `tests/` directory exists but the verifier doesn't enumerate test-type shape). MCP-01..05 (filed in R07.22, present in the Detailed Findings section but absent from the Findings Summary table — a reconcile-drift bug in the R07.22 audit pass) are now added to the Findings Summary; the prose header count moves from `26 OPEN` → `34 OPEN` to match the parsed table (was a silent reconcile `matches: false`). Three new findings, all from the R07.23 surface: SEC-20 (Medium — `mcp install` overwrites existing mcp.json entries by default, no `--no-overwrite` opt-out; the maintainer spec calls this intentional but a user who has customized args/paths loses their changes silently on a re-install), ROB-41 (Low — `registry.search_all` swallows failures from both npm and GitHub, returning `[]` with no signal that the cause was network rather than 0 results; the `--json` payload's `errors` field already exists per the changelog but plain-mode `mcp search` prints `No results found` instead of a network-error hint), TEST-11 (Low — `tests/test_mcp_cli.py` mocks `agentkthx.mcp.registry._http_get_json` so no real network shape is exercised; same shape as TEST-09/TEST-10 — a live npm/GitHub response-shape contract test, skip-gated on `AGENTKTHX_LIVE_TESTS=1`, would catch a `package.name` → `package.id` rename before users do). The R07.23 `agentkthx/core/memory.py` first-user preservation fix (ZAI 1214 / OpenAI 400 on sliding-window dropping the original user prompt) is a latent-bug fix that pre-empted any prior audit finding — no closure recorded, noted here for completeness. The R07.22 surface added MCP-01..05 (filed in-passing during the release, registered retroactively in this re-audit). Suite: 2880 (R07.22 changelog) → 2899 passed (+19 from `tests/test_loop_resilience.py` + `tests/test_mcp_cli.py` expansions). Register: 125 findings — 34 OPEN / 84 CLOSED / 7 WONTFIX (91 archived, ~73%).
> **R07.22 re-audit delta (2026-10-04, retroactive):** The R07.22 release added 5 new MCP-category findings (MCP-01 through MCP-05) covering: MCP-01 — `StdioTransport._read_response` uses blocking `readline()` so per-call timeouts don't actually interrupt (Medium, same shape as ROB-02/ROB-06); MCP-02 — MCP server configs have no sha256 pin equivalent (SEC-13 analogue, Medium); MCP-03 — `_extract_params` flattens `oneOf`/`anyOf`/`$ref` JSON Schema constructs to default `string` (Low); MCP-04 — eager server startup adds 1-3s latency to every `--mcp` session even when no MCP tools are called (Low); MCP-05 — no `notifications/tools/list_changed` handler, runtime tool surface changes invisible until next session (Low). All 5 were filed during the R07.22 release pass but were placed in the Detailed Findings section without corresponding Findings Summary rows — this re-audit corrects the drift. No closures. Suite: 2802 → 2858 passed (+56 from MCP scaffold + CLI integration tests).
> **R07.21 closure batch 2 (2026-10-03):** 8 more OPEN findings closed — all surgical, non-breaking. ROB-09 (validate_path abspath→realpath, 1 line — symlink traversal security fix); ROB-17 (token-tier truncation of single over-budget message, ~15 lines); ROB-20 (Agent.num_predict public @property, ~10 lines — mirrors num_ctx); ROB-25 (shared DEFAULT_GENERATE_TEMPERATURE/MAX_TOKENS constants in base.py, ~20 lines — generate() + _generate_with_auth() now aligned); ROB-30 (_fetch_model_cards catch-all narrowed to specific exceptions, 1 line); MAINT-24 (_build_tool_section docstrings updated to match behavior, 2 lines); MAINT-25 (--force-react tri-state on/off/auto with bare-flag backwards compat, ~30 lines across 3 argparse sites + consumer in agent_factory); MAINT-26 (moved to CLOSED — OpenRouter directory description/main_url/slug fields have NO documented mechanism to set them; the gap is structural on OpenRouter's side, not an AgentKthx defect). Suite: 2783 → 2802 passed (+19 from `tests/test_r07_21_audit_closures_batch2.py`). Zero regressions; ruff + black clean. Register: 116 findings — 26 OPEN / 83 CLOSED / 7 WONTFIX.
> **R07.21 closure batch 1 (2026-10-03):** 5 OPEN findings closed in a single surgical pass — all non-breaking fixes with clear patterns. ROB-18 (PersistentMemory Lock → RLock, 1 line — strict superset of Lock); ROB-35 (parse_shared_args `or`-coalescing → `is not None`, ~10 lines — preserves the documented `0` sentinel); ROB-36 (`_parse_token_size` `math.isfinite` guard, ~5 lines — turns `inf`/`1e400` OverflowError into clean ValueError); ROB-38 (chat.py empty-answer fatal-error branch, ~15 lines — detects 401/402/403/quota before the throttle branch); ROB-39 (OpenRouter `_NON_CHAT_SLUG_PATTERNS` frozenset, ~50 lines — classifies image/audio/moderation/embedding slugs as UNTESTED instead of NATIVE; runtime still safe via 400→ReAct fallback). Suite: 2747 → 2783 passed (+36 from `tests/test_r07_21_audit_closures.py`, +1 relaxed in `tests/test_r07_05_audit_fixes.py` to accept either Lock or RLock since ROB-18 changed the type). Zero regressions; ruff + black clean. Closure details in `audit/deltas.md` §R07.21 Audit Closure Batch.
> **R07.21 in-progress delta (2026-10-03):** MAINT-26 added (OpenRouter App Attribution — `X-OpenRouter-Categories` + `X-OpenRouter-Title` headers hardcoded `cli-agent`, 19-test regression file `test_r07_21_openrouter_attribution.py`, `OPENROUTER_API_TECHNICAL_REFERENCE.md` rewritten + expanded with App Directory Entry + Client/Harness Metrics & Reporting + Generation Inspection sections). MAINT-27 added (new `/sh` slash command — runs local shell, displays output, injects into context as user message; `-n` flag skips injection; reuses the `shell()` builtin for security/timeout; 6-test regression file `test_r07_21_sh_command.py` pinning the context-injection contract + real-shell integration; the redundant parsing tests that would test a copy of the parser are deliberately omitted until MAINT-01 extracts the branch; USAGE.md updated with `/sh` subsection). `TestVersionPin` removed from `test_r07_20_auth_picker.py` (-2 tests) — the exact-string pins broke on every release bump and were redundant with bump-version.sh's own site-verification step. Suite: 2783 passed / 16 skipped. OpenRouter directory entry verified live: App ID 5072126, categories now `["cli-agent"]` (was `[]`). This delta will be folded into the R07.21 re-audit pass once the release ships.
> **R07.20 re-audit delta (2026-10-02, this pass — commit 98ee377):** All 35 carried-forward OPEN findings re-verified against the current tree; no closures. `verify_open_findings.py`: 33 STILL_OPEN_LIKELY, 2 FILE_EXISTS_NO_PATTERN (FEAT-07, TEST-05 — feature proposals/files where no code pattern is expected), and the three PATTERN_GONE heuristic flags re-investigated manually (ROB-06: `stream_gen.close()` survives at streaming.py:982 — false positive; FEAT-03: `output_schema` is still absent from `core/models.py` — expected, the feature remains missing; ROB-39: the verifier greps for the live-catalog slugs named in the detail (lyria/gpt-audio/llama-guard) which by design never appear in plugin source — the unconditional `return ToolSupportLevel.NATIVE` is verified at openrouter.py:850). Line references updated for the R07.19 shifts (MAINT-01 → 1,733 lines / cmd_chat at :206; MAINT-25 → `agent_factory.py:280-347` + `parser.py:312`; MAINT-24 → `soul/loader.py:813` + docstrings :649/:819; ROB-33 → `agent_factory.py:571`; ROB-20 → `apply_model_switch` at :728; TEST-01 count prose → 2,608; TEST-09 → 8 cloud backends). Two new findings, both Low, both from the R07.19 surface: ROB-38 (the empty-final-answer boilerplate blames a rate limit and advises "try again in a few seconds" even after definitive fatal errors — quota/auth; observed live in the smoke test) and ROB-39 (OpenRouter `test_tool_support` returns NATIVE unconditionally — non-chat slugs display `tools ✓ native`; the deferred classification candidate now holds a register ID). Executive Summary regenerated for R07.20-dev; the register's first Architecture Strengths section added. Register: 114 findings — 37 open / 70 closed / 7 wontfix (77 archived, 68%). Suite 2608 passed / 16 skipped.
---
## Table of Contents
- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
- [Priority Matrix](#priority-matrix)
---
## Executive Summary
This re-audit covers AgentKthx at commit `1d7f1ee` (R07.23 — `mcp search` + `mcp install` + memory first-user preservation + FREE_ONLY picker parity; PyPI 0.7.22 latest published per the changelog header). The codebase comprises 139 Python source files totaling ~66,743 LOC, with ~41,806 lines of tests across 93 test files — the suite passes **2,899 tests / 16 skipped in ~19s**, with CI on Python 3.12/3.13 plus a parallel coverage job and a promoted-to-required lint job. The R07.00 modularization (5-mixin `Agent` composition, now a 26-file `cli/` package) remains stable, and the split-register layout holds: this file carries the 34 OPEN findings, while `deltas.md` archives the 91 closed/wontfix findings with the full closure timeline (R07.00 → R07.21).
R07.22 was the largest single-release surface expansion since R07.19: MCP (Model Context Protocol) client mode over stdio JSON-RPC 2.0 — the new `agentkthx/mcp/` package (`config.py` + `transport.py` + `client.py` + `manager.py`), the `agentkthx mcp` subcommand (`init` / `list` / `probe`), `--mcp [SERVER...]` + `--mcp-config PATH` flags on chat/run/agent, `_wire_mcp()` in `agent_factory.py` running BEFORE `Agent(...)` construction so the system prompt's Tool Reference section enumerates the namespaced MCP tools, and `sanitize_tool_output` extended to cover MCP tool results (the 8 KB truncation + secret redaction + ANSI strip defense-in-depth posture does not weaken because a tool came from a subprocess). End-to-end verified against the official `@modelcontextprotocol/server-filesystem` (14 tools) and `@modelcontextprotocol/server-sequential-thinking` (1 tool). The release also added the long-missing `SECURITY.md` + `CONTRIBUTING.md` trust artifacts and shipped the first 5 MCP-category findings (MCP-01..05). R07.23 then expanded the `mcp` subcommand from three actions to five — `list` / `init` / `probe` / **`search`** / **`install`** — replacing the deleted offline catalog with a live registry (`agentkthx/mcp/registry.py`, stdlib `urllib.request` only, hits npm + GitHub with a 10-minute TTL cache at `~/.agentkthx/mcp_cache.json`), removed the deprecated `@modelcontextprotocol/server-git` entry from `mcp init`'s example config (the package was deleted from npm, 404 as of 2026-10-04), and fixed three latent defects: a memory sliding-window bug that could drop the original user prompt and expose an assistant(tool_calls) at the head of the message array (triggering ZAI code 1214 / OpenAI HTTP 400), a stale footer during long agentic runs with many tool calls (ctx% only refreshed after a GENERATE call), and a FREE_ONLY picker gap (the startup model picker showed paid models when `OPENROUTER_FREE_ONLY=1` or `ZAI_FREE_ONLY=1` was set). The three new findings this pass live in the R07.23 surface: SEC-20 (`mcp install` silent-overwrite), ROB-41 (`registry.search_all` swallows all-source failures), and TEST-11 (mocked HTTP layer for `mcp search`).
Cumulative closure state: **84 CLOSED + 7 WONTFIX of 125 findings (91 archived, ~73%)**. Closures span R07.00 → R07.21 (the closure history and per-release test-count deltas live in `deltas.md`). R07.16/R07.17/R07.18 closed none (+6 new findings across the three); R07.19 closed two (ROB-34 closed on verification evidence; ROB-37 opened and closed in the same release); R07.20 closed none + added two (ROB-38/ROB-39); R07.21 closed 13 in two batches (ROB-09, ROB-17, ROB-18, ROB-20, ROB-25, ROB-30, ROB-35, ROB-36, ROB-38, ROB-39, MAINT-24, MAINT-25, MAINT-26) + added MAINT-27; R07.22 closed none + added MCP-01..05 (filed but missing from Findings Summary table — a reconcile-drift bug corrected this pass); R07.23 closed none + added SEC-20, ROB-41, TEST-11. The highest-leverage remaining closures are unchanged: ROB-33 (the Windows `os.kill(pid, 0)` process-kill gotcha on the flagship `turbo start` → `chat` workflow), ROB-31 (entitlement-aware Pollinations fallback filtering), the MAINT-23/ROB-29 retry-loop family (one `CloudBackend` primitive closes ~160 LOC of duplication across Mistral + Pollinations), MAINT-01 (extract `ChatSession` from the now-1,733-line `cmd_chat`), and TEST-01 (the integration-test tier — still the largest structural gap; every one of the 2,899 suite tests remains a mocked unit test).
Process note: this pass re-verified all 26 carried-forward OPEN findings against the R07.23 tree — `verify_open_findings.py` returned STILL_OPEN_LIKELY for 21, PATTERN_GONE for 2 (ROB-06 — false positive: the `stream_gen.close()` block survives at `streaming.py:1005-1020`, shifted from the cited `:972-990` by R07.19/R07.20/R07.22/R07.23 line growth; FEAT-03 — expected: the `output_schema` field is still absent from `core/models.py:Tool`), FILE_EXISTS_NO_PATTERN for 2 (FEAT-07, TEST-05 — feature/file proposals), and UNKNOWN for 1 (TEST-01 — `tests/` exists but the verifier doesn't enumerate test-type shape). MCP-01..05 were re-confirmed in their R07.22-cited locations: MCP-01 — `_read_response` blocking `readline()` survives at `mcp/transport.py:170-205`; MCP-02 — `MCPServerConfig.resolve_command` still has no sha256 pin field at `mcp/config.py:88-110`; MCP-03 — `_extract_params` flattens `oneOf`/`anyOf`/`$ref` to default `string` at `mcp/manager.py:191-225`; MCP-04 — `MCPManager.connect_all` eager startup at `cli/agent_factory.py:513-514`; MCP-05 — no `notifications/tools/list_changed` handler in `mcp/client.py` (verified by grep). Line refs for ROB-06 were updated to `streaming.py:1005-1020`. The three new findings continue the established ID numbering (SEC-20 after the R07.12-wontfix SEC-19, ROB-41 after the R07.21-closed ROB-40, TEST-11 after open TEST-10).
## Findings Summary
| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-09 | Medium | Security | OPEN | ACP credentials sent as Basic Auth over HTTP by default (ACP_BASE_URL = "http://localhost:8766") |
| SEC-13 | Medium | Security | OPEN | sha256 plugin pins are opt-in — no AGENTKTHX_REQUIRE_PLUGIN_PINS enforcement mode for external plugins |
| ROB-02 | Medium | Robustness | OPEN | Orchestrator parallel mode cancels futures but does not join worker threads |
| ROB-06 | Medium | Robustness | OPEN | KeyboardInterrupt during SSE streaming may not deterministically release HTTP connection on Windows |
| ROB-15 | Medium | Robustness | OPEN | PersistentMemory.add() does two separate lock acquisitions (_write_message + _touch_session) — interleaving risk + 2× commit per message |
| ROB-31 | Medium | Robustness | OPEN | healthy_fallbacks() under POLLINATIONS_ANON_CATALOG=1 ranks paid_only models the key cannot generate against — 8 of the anonymous top-10 are out-of-entitlement; fallback redirect 403s |
| ROB-33 | Medium | Robustness | OPEN | _is_process_alive probes liveness with os.kill(pid, 0) — on Windows that TERMINATES the target; R07.16 moved the call onto the chat startup path via TurboState.load() |
| MAINT-01 | Medium | Maintainability | OPEN | cmd_chat is a 1,733-line single function with 30+ nested closures and no slash-command dispatcher |
| MAINT-03 | Medium | Maintainability | OPEN | normalize_args strategy 5 (prefix/substring matching) is dangerously permissive — {"e": "..."} matches expression |
| MAINT-22 | Medium | Maintainability | OPEN | Streaming path bypasses _build_mistral_body — random_seed/safe_prompt/prompt_cache_key/OpenAI-only kwarg stripping NOT applied on streaming (only non-streaming) |
| MAINT-23 | Medium | Maintainability | OPEN | _make_api_request + _iter_sse_lines duplicate ~80 LOC of retry-loop skeleton (third consecutive cloud backend — ROB-29/MAINT-11 pattern); helpers are shared but the loop itself is copy-paste |
| FEAT-03 | Medium | New Features | OPEN | Tool output schema validation via JSON Schema |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all 2608 tests are mocked unit tests; slash-command dispatcher untested |
| TEST-03 | Medium | Testing | OPEN | FakeBackend in test_agentic_loop_subsystem.py omits generate_completions_stream — streaming callbacks unexercised |
| ROB-29 | Low | Robustness | OPEN | MistralBackend _iter_sse_lines + _make_api_request have ~80 LOC duplicated retry/backoff logic — mirrors the MAINT-11 OrcaRouter pattern closed in R07.08 |
| MAINT-27 | Low | Maintainability | OPEN | New `/sh` slash command added R07.21 — inline if/elif branch in cmd_chat (MAINT-01 family); no slash-command dispatcher yet |
| FEAT-05 | Low | New Features | OPEN | Plugin sandboxing via restricted register() namespace + audit hooks |
| FEAT-06 | Low | New Features | OPEN | Streaming tool-call argument deltas (function_call_arguments.delta SSE events) |
| FEAT-07 | Low | New Features | OPEN | Conversation export/import to OpenResponses-format JSON |
| FEAT-08 | Low | New Features | OPEN | Free TIER (paid_only boundary on bare GET /models) unreachable — FREE_ONLY exposes only the 16 zero-cost models, not the ~102 Quest-Pollen-eligible text models the key can run |
| TEST-04 | Low | Testing | OPEN | No test coverage for agent_mode.py rollback functionality (822 LOC, key feature) |
| TEST-05 | Low | Testing | OPEN | test_bump_version_script.py tests shell script via subprocess — fails on Windows/no-bash |
| TEST-07 | Low | Testing | OPEN | No test for update_check module's network-failure paths (URLError, socket.timeout, malformed JSON) |
| TEST-09 | Low | Testing | OPEN | Plugin scaffolds don't include a "agent loop streaming path actually calls through" smoke test — R07.09.0 streaming bug caught by user testing, not test suite |
| TEST-10 | Low | Testing | OPEN | Zero live-shape coverage for free-model detection — v0.1.2 shipped with _card_is_free blind to the live currency-only encoding while all 83 fixture tests stayed green |
| MCP-01 | Medium | Robustness | OPEN | StdioTransport uses blocking readline — per-call timeouts don't actually interrupt (same shape as ROB-02/ROB-06) |
| MCP-02 | Medium | Security | OPEN | MCP server configs have no sha256 pin equivalent (SEC-13 analogue) |
| MCP-03 | Low | Maintainability | OPEN | Complex JSON Schema constructs (oneOf/anyOf/$ref) flatten to default `string` in inputSchema conversion |
| MCP-04 | Low | Performance | OPEN | Eager server startup adds 1-3s latency to every `--mcp` session even when no MCP tools are called |
| MCP-05 | Low | Robustness | OPEN | No `notifications/tools/list_changed` handler — runtime tool surface changes invisible to the registry until next session |
---
## R07.16 New Findings
Four findings, all from the R07.16 surface (TurboQuant lifecycle + chat-side auto-derivation + tool-calling auto-detection + Windows fallbacks). No closures this pass — all 30 carried-forward OPEN findings re-verified in current code. **R07.23 update:** MAINT-24 and MAINT-25 (originally listed here) were closed in the R07.21 closure batch 2 — their detail sections moved to `deltas.md`. They are removed from this delta table to keep `audit.md` focused on OPEN findings (the R07.16 historical record survives in `deltas.md`'s `## R07.16 Closures` section).
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| ROB-33 | Medium | Robustness | `agentkthx/plugins/turboquant/turbo.py:159-183`, `agentkthx/cli/agent_factory.py:490-497` | _is_process_alive probes liveness with os.kill(pid, 0) — on Windows that TERMINATES the target; now on the chat startup path |
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
---
## R07.22 New Findings
Five findings, all from the R07.22 MCP client surface (`agentkthx/mcp/` package + `agentkthx mcp` subcommand + `_wire_mcp()` in `agent_factory.py`). No closures this pass — all 26 carried-forward OPEN findings re-verified in current code. The 5 MCP findings were placed in the Detailed Findings section but absent from the Findings Summary table — a reconcile-drift bug corrected at the R07.23 re-audit (the prose header count moved from `26 OPEN` → `31 OPEN` to match the parsed table, then to `34 OPEN` after the R07.23 additions).
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| MCP-01 | Medium | Robustness | `agentkthx/mcp/transport.py:170-205` (`_read_response`) | StdioTransport uses blocking `readline()` — per-call timeouts don't actually interrupt |
| MCP-02 | Medium | Security | `agentkthx/mcp/config.py:88-110` (`MCPServerConfig.resolve_command`) | MCP server configs have no sha256 pin equivalent (SEC-13 analogue) |
| MCP-03 | Low | Maintainability | `agentkthx/mcp/manager.py:191-225` (`_extract_params`) | Complex JSON Schema constructs flatten to default `string` in inputSchema conversion |
| MCP-04 | Low | Performance | `agentkthx/cli/agent_factory.py:513-514` (`MCPManager.connect_all`) | Eager server startup adds latency to every `--mcp` session even when no MCP tools are called |
| MCP-05 | Low | Robustness | `agentkthx/mcp/client.py` (no handler for the notification) | No `notifications/tools/list_changed` handling — runtime tool surface changes invisible to the registry |
---
## Detailed Findings
<!-- Open findings only. CLOSED + WONTFIX detail sections are in deltas.md. -->
### Security
#### SEC-09: ACP credentials sent as Basic Auth over HTTP by default
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/config.py:82-84`, `agentkthx/plugins/acp/acp_plugin.py` |
`ACP_BASE_URL = "http://localhost:8766"` is the default. `ACP_USER` and `ACP_PASS` are read from env vars (good) but sent as Basic Auth over the wire. While `localhost` is fine for development, a user who sets `ACP_BASE_URL=http://remote-host:8766` to share an ACP server across machines sends credentials in cleartext, exposing them to any network observer.
Recommendation: Warn loudly when `ACP_BASE_URL` doesn't start with `https://` and isn't `localhost`/`127.0.0.1`/`::1`. Refuse to send credentials over non-HTTPS unless `ACP_ALLOW_INSECURE_HTTP=1` is set.
**Impact:** Credentials sent in cleartext over the network if ACP server is remote; users may not realize the implication of changing `ACP_BASE_URL`.
---
#### SEC-13: sha256 plugin pins are opt-in — no enforcement mode for external plugins
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/plugins/_loader.py:130,251,286` |
Re-verified in current code (detail section authored during the R07.16 re-audit — this was previously a summary-only row). The R07.05 SEC-06 fix gave manifests an *optional* integrity pin: `_MANIFEST_KNOWN_KEYS` accepts `"sha256"` (line 81/103), the manifest schema declares `sha256: str | dict[str, str] | None = None` (line 251), and `_validate_sha256_pin` (lines 286-300) rejects malformed pins ("must be exactly 64 hex chars"). But a manifest with NO pin loads silently — there is no `AGENTKTHX_REQUIRE_PLUGIN_PINS` enforcement variable anywhere in the codebase (grep-verified), no config field, and no policy hook. A user installing a third-party plugin gets integrity verification only if the plugin author chose to ship a pin; an attacker-supplied (or supply-chain-tampered) manifest simply omits the field and skips the check entirely.
Recommendation: add `AGENTKTHX_REQUIRE_PLUGIN_PINS=1` (env or config field) that refuses to load any plugin whose manifest lacks a well-formed sha256 pin, with an allowlist exception for the bundled plugins (which ship in-tree and are covered by the repo's own integrity). Document the flag in the PLUGIN_SPEC and in `plugin.schema.json`.
**Impact:** The plugin trust boundary becomes enforceable instead of advisory — users gain an actual guarantee mode, not just a mechanism the author may or may not use.
---

### Robustness
#### ROB-02: Orchestrator parallel mode cancels futures but does not join worker threads
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/orchestrator.py:386-398` |
`_run_parallel` uses `concurrent.futures.ThreadPoolExecutor(max_workers=len(self._agent_list))` and `concurrent.futures.wait(futures, timeout=self.timeout, return_when=ALL_COMPLETED)`. On timeout, `for future in not_done: future.cancel()` is called — but `future.cancel()` only prevents a future from STARTING; if the underlying callable is already running, it cannot be cancelled (Python docs: "Returns False if the call is currently being executed or finished"). The threads continue running to completion, holding open HTTP connections and consuming tokens. On shared state (e.g., two agents sharing a `Memory` instance — not the default but possible), this causes race conditions.
Recommendation: Use `concurrent.futures.FIRST_COMPLETED` and explicitly close the executor with `executor.shutdown(wait=False, cancel_futures=True)` (Python 3.9+). For long-running HTTP backends, pass a `threading.Event` to the agent's `generate_fn` and have the backend check it between SSE chunks.
**Impact:** Long-running cloud API calls keep running after the orchestrator returns, possibly for minutes, consuming tokens and holding connections.
---

#### ROB-06: KeyboardInterrupt during SSE streaming may not deterministically release HTTP connection on Windows
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/streaming.py:1005-1020` |
**R07.23 line-ref update:** the `stream_gen.close()` block shifted from the originally-cited `:972-990` to `:1005-1020` due to R07.19/R07.20/R07.22/R07.23 line growth (R07.19 added ~30 LOC of `StreamAccumulator`/`StreamRenderer` plumbing above this point; R07.20 added the `describe_terminal` fatal-error branch; R07.22 added the MCP-cleanup `finally` block; R07.23 added the `--debug` messages-array dump on fatal API error). The underlying finding is unchanged — `verify_open_findings.py` flagged PATTERN_GONE because its grep hunted the old line range; manual verification at `:1005-1020` confirms `stream_gen.close()` survives inside the `try/except Exception: pass` block, immediately preceded by the ROB-05 (R06.57) comment about the urllib response being abandoned mid-iteration.

The KeyboardInterrupt handler calls `stream_gen.close()` to release the underlying urllib response. The comment (lines 1003-1009) explains this is for ROB-05 (R06.57). However, on Windows, `urllib.request.urlopen` returns an `http.client.HTTPResponse` whose `.close()` may not immediately close the TCP connection — it relies on GC. On long sessions with many Ctrl+C interrupts, this can exhaust the connection pool. On Linux/macOS, `close()` calls `flush()` and `shutdown(SHUT_WR)` synchronously.
Recommendation: Explicitly call `response.fp.close()` and `response.release_conn()` if available. For urllib, use `response.close()` directly and catch `AttributeError` for older Python versions. Consider using `http.client.HTTPConnection` directly for finer-grained control.
**Impact:** Connection exhaustion on Windows under heavy Ctrl+C usage — Linux/macOS unaffected but the cross-platform promise is broken.
---


---

---

#### ROB-15: PersistentMemory.add() does two separate lock acquisitions — interleaving risk + 2× commit per message
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/persistent_memory.py:236-248` |
Re-verified in current code (detail section authored during the R07.16 re-audit — previously a summary-only row). `add()` (lines 236-241) calls `self._write_message(role, content, **kwargs)` and then `self._touch_session()` as two independent operations; `add_tool_call()` (243-248) has the same shape. Each helper acquires the per-DB-path write lock (the MAINT-15 registry) and releases it before the next call. Two consequences: (1) under concurrent writers (orchestrator parallel mode), another thread's `add()` can interleave between the message-write and the session-touch, committing rows in an order that doesn't match any single logical turn; (2) every message costs two full lock/commit cycles instead of one transaction.
Recommendation: introduce a `_transaction()` context manager on the store that acquires the per-DB lock once and commits at exit; route `_write_message` + `_touch_session` through it in `add()`/`add_tool_call()`/`add_tool_result()`. Pairs naturally with ROB-18 (the lock must become reentrant first, or the helpers need lock/no-lock variants).
**Impact:** Message-atomicity under concurrent writers and half the SQLite commits per turn.
---

---

---

---
#### ROB-29: ~80 LOC duplicated retry/backoff between _iter_sse_lines and _make_api_request
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/mistral/mistral.py:981-1139` + `:1187-1300` |
The streaming and non-streaming paths each hand-roll the full attempt loop: HTTPError status classification, Retry-After parsing, backoff computation, retry-vs-raise decisions, and exhaustion messages — duplicated with small drifts (the ROB-22 exhaustion-message inconsistency came from exactly this kind of drift). This is the MAINT-11 (OrcaRouter) pattern's second occurrence; MAINT-23 (Pollinations) is the third.
Recommendation: lift the R07.08 `_classify_and_handle_http_error` helper from `OrcaRouterBackend` to `CloudBackend` and shape it so both paths consume it; close the whole family in one move (ROB-29 + MAINT-23).
**Impact:** Every new cloud backend re-copies ~80 LOC; drift between the copies produces inconsistent retry behavior (observed once already as ROB-22).
---

---
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
#### ROB-33: `_is_process_alive` probes liveness with `os.kill(pid, 0)` — on Windows that TERMINATES the target, and R07.16 moved the call onto the chat startup path
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/turboquant/turbo.py:159-183`, `agentkthx/cli/agent_factory.py:571 (TurboState.load)` |
New in R07.16. `_is_process_alive(pid)` uses `os.kill(pid, 0)` as an existence probe, then checks `/proc/<pid>/stat` for zombies. On Windows, `os.kill` with any signal other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` does not probe — per the `os.kill` documentation, the target is **unconditionally killed via `TerminateProcess`** with the signal value as the exit code (0 here). The `/proc` zombie check is Linux-only and its absence handler just falls through to `return True`, so Windows always takes the destructive path. Pre-R07.16 this only endangered `turbo` command flows; R07.16 placed `TurboState.load()` (which calls `_is_process_alive(state.pid)`) into `_get_local_catalog_defaults` — executed on EVERY chat startup against a non-cloud backend with a local base_url, and for `--backend ollama` too, since the state file is global (`~/.agentkthx/turbo.state`). Concrete failure: `agentkthx turbo start <model>` (server running, state file present) followed by `agentkthx chat --backend turboquant` on Windows terminates the just-started server during the liveness check. Worse: if the state file is stale and the OS reused the pid for an unrelated process, chat startup kills that process.
Recommendation: gate the probe by platform — on Windows use a non-destructive check (`ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)` + `GetExitCodeProcess` comparing against `STILL_ACTIVE`, all stdlib); keep `os.kill(pid, 0)` on POSIX. Add a regression test that runs the liveness check against a live child process and asserts it still exists afterwards (fails on Windows today).
**Impact:** The R07.16 flagship workflow (`turbo start` → `chat`) silently kills its own server on Windows — the platform this release specifically targeted.
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

#### MAINT-03: `normalize_args` strategy 5 (prefix/substring matching) is dangerously permissive
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/core/helpers.py:144-282` |
The function tries 5 strategies: (1) tool-specific alias lookup, (2) direct match, (3) case-insensitive match, (4) generic aliases (ARG_ALIASES), (5) prefix/substring matching. Strategy 5 (line 254-260) does `if param in key_lower or key_lower.startswith(param):` which is extremely permissive — a model that passes `{"ex": "2+2"}` to a tool with param `expression` will match because `"ex" in "expression"`. But `{"e": "..."}` would also match because `"e" in "expression"`. The `CONTEXTUAL_ALIASES` set (line 133-143) tries to mitigate this but only for known-ambiguous aliases. Worse, when MULTIPLE params match a single key, the last match wins (line 261-265) — non-deterministic based on dict iteration order.
Recommendation: Drop strategy 5 entirely. If fuzzy matching is needed, require the match to be at least 3 characters AND not be a prefix of multiple params. Add `--strict-args` flag to disable fuzzy matching entirely for production use.
**Impact:** Argument misattribution when models use single-letter keys — silent wrong behavior rather than a clear "missing argument" error.
---

#### MAINT-22: Streaming path bypasses _build_mistral_body — Mistral knobs not sent
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/plugins/mistral/mistral.py:957-1008` (`_iter_sse_lines`), inherited `generate_completions_stream` |
The inherited `generate_completions_stream()` builds its request body via the generic `_build_openai_body()`; `_build_mistral_body()`'s wire-format deltas (`random_seed` aliasing, `safe_prompt` injection, `prompt_cache_key` from `session_id`, OpenAI-only kwarg stripping) apply to non-streaming only. Streaming works — it just silently drops every Mistral-specific knob. The same gap exists in Pollinations (safe-flag injection is non-streaming-only there too).
Recommendation: route streaming body construction through a backend-owned `_build_body()` virtual the base class calls — one indirection closes both plugins' variant of this.
**Impact:** Users setting MISTRAL_SAFE_PROMPT/POLLINATIONS_SAFE or relying on seed determinism get silently different behavior between streaming and non-streaming turns.
---
#### MAINT-23: Retry-loop skeleton duplicated between _make_api_request and _iter_sse_lines (third backend)
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/plugins/pollinations/pollinations.py:1414-1512` + `:1540-1638` |
The Pollinations plugin extracted genuinely shared pieces (`_sleep_for_retry`, `_raise_for_status`, `_parse_error_envelope` — better than Mistral's ROB-29 state), but the attempt-loop skeleton is still copy-paste ×2: `for attempt in range(max_retries + 1)` → build request → HTTPError parse → ARCH-03 first-attempt-400 branch → retryable classification → Retry-After sleep → URLError backoff → exhaustion raise. The streaming copy differs only in yielding lines plus the ROB-06 close-guard. Third consecutive cloud backend carrying the pattern (OrcaRouter → Mistral → Pollinations); the family grows ~80 LOC per plugin.
Recommendation: generalize the ROB-29 fix — lift a `CloudBackend` retry-loop primitive (`_request_with_retry(url, body, headers, *, stream=False)`) that returns parsed JSON or yields SSE lines; each backend contributes only body building, response parsing, and error-class prose. Closes the ROB-29 + MAINT-23 family and prevents the fourth occurrence.
**Impact:** ~160 LOC of near-duplicate control flow in one plugin; every retry-policy fix must be applied in both loops (the Retry-After cap already had to be, twice).
---

---

---

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
### New Features
---
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
#### TEST-09: Plugin scaffolds miss agent-loop streaming-path integration test
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_mistral_backend.py` (whole file) |
The R07.09 streaming bug (missing `_iter_sse_lines` abstract hook — first shipped as `NotImplementedError` at chat invocation) was caught by the user's live `agentkthx chat --backend mistral` run, not by the 64-test suite: tests asserted the method existed and unit-tested its pieces, but nothing exercised the agent loop → `generate_completions_stream` → `_iter_sse_lines` call-through. TEST-10 is the same lesson recurring one release later (live feed shape vs fixtures).
Recommendation: a `tests/test_plugin_streaming_integration.py` that instantiates each bundled cloud backend over a mocked HTTP layer and drives one streaming turn through `Agent`-level machinery — one parameterized test, all 8 cloud backends, permanently closes the finding class.
**Impact:** Every new cloud backend can ship the same abstract-hook omission; the suite gives false confidence until a user hits it live.
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

### MCP (R07.22 — new category)
#### MCP-01: StdioTransport uses blocking readline — per-call timeouts don't actually interrupt
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness (MCP) |
| **File(s)** | `agentkthx/mcp/transport.py:170-205` (`_read_response`) |
The scaffold's `_read_response` calls `self._proc.stdout.readline()` with no timeout. The `timeout` parameter is honored only via the loop's deadline check, but a hung MCP server that produces no output blocks the calling thread on `readline()` indefinitely — the deadline check never gets a chance to fire. Same shape as ROB-06 (Windows conn release) and ROB-02 (orchestrator thread join): a blocking stdlib I/O call with no cancellation path. In practice this means a misbehaving MCP server can freeze the agent's main thread for the full `timeout_seconds` window, and Ctrl+C is unreliable because the signal won't interrupt the readline on all platforms.
Recommendation: thread+queue pattern — spawn a daemon thread that does the blocking `readline()` and pushes the result to a `queue.Queue`; the main thread does `queue.get(timeout=remaining)`. On timeout, mark the transport as poisoned (subsequent calls raise immediately) and let the daemon thread die naturally on subprocess close. This is the same pattern `subprocess.communicate` uses internally. Bonus: also fixes Ctrl+C interruptibility.
**Impact:** A hung MCP server freezes the agent's main thread for up to `timeout_seconds` (default 30s) with no clean escape. Multi-server setups where one server hangs block all tool calls to other servers (because each transport serializes its own calls, but the manager's `connect_all` calls each server sequentially).
---

#### MCP-02: MCP server configs have no sha256 pin equivalent (SEC-13 analogue)
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security (MCP) |
| **File(s)** | `agentkthx/mcp/config.py:88-110` (`MCPServerConfig.resolve_command`) |
An MCP server entry declares `command` (string) + `args` (list). The launcher resolves `command` via `shutil.which` or treats it as an absolute path, then spawns it with `subprocess.Popen(shell=False)`. There is no sha256 pin on the binary itself — an attacker who can write `~/.agentkthx/mcp.json` (or any path the operator passes via `--mcp-config`) can substitute any binary for a declared server name. The config file permission check warns on group/world-writable (line 156-167) but does not fail, and on containers running as root with default umask the warning is the only signal. This is the direct analogue of SEC-13 for plugins, with the same opt-in trust posture.
Recommendation: add an optional `sha256` field to `MCPServerConfig` (string, hex). When present, `resolve_command` hashes the resolved binary and refuses to launch on mismatch (fail-closed, same as `_validate_sha256_pin` for plugins). Add `AGENTKTHX_REQUIRE_MCP_PINS=1` env var that refuses to load any MCP server entry without a pin. Document both in `docs/mcp/ROADMAP.md` and in `SECURITY.md`. Future: consider a `paths` allowlist field that restricts where binaries can be resolved from.
**Impact:** The MCP trust boundary is advisory — same as SEC-13 for plugins. A tampered `mcp.json` substitutes arbitrary code under a trusted server name with no signal to the operator beyond a file-permission warning that's easy to miss.
---

#### MCP-03: Complex JSON Schema constructs flatten to default `string` in inputSchema conversion
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability (MCP) |
| **File(s)** | `agentkthx/mcp/manager.py:191-225` (`_extract_params`) |
The `_extract_params` helper converts an MCP tool's `inputSchema` (JSON Schema) into the project's flat `ToolParam` list. It handles `type: object` with `properties` + `required` and the common type cases (`string`/`integer`/`number`/`boolean`/`array`/`object`), plus nullable unions (`["string", "null"]`). It does NOT handle `oneOf`, `anyOf`, `allOf`, `$ref`, or nested `properties` deeper than one level — these fall through to `param_type = "string"` (the default). A tool with a sophisticated schema will appear simpler to the model than the server actually accepts, leading to malformed tool calls and the model being blamed for "hallucinating" args that the schema actually permitted.
Recommendation: when `_extract_params` encounters a schema construct it can't structurally convert, emit a single `arguments_json` string parameter whose description tells the model to pass the full arguments object as JSON. The MCP client then parses the JSON and forwards it as the `arguments` field. This preserves the rich schema (the model sees a JSON string with the original schema in its description) at the cost of slightly more prompt tokens. Alternative: extend `ToolParam` to carry an arbitrary JSON Schema dict (bigger change — touches `to_json_schema` in `core/models.py`).
**Impact:** Tools with `oneOf`/`anyOf`/`$ref` schemas appear deceptively simple to the model. Tool calls fail with "missing required field" or "wrong type" errors that the model can't easily diagnose because the schema it was shown didn't reflect reality.
---

#### MCP-04: Eager server startup adds latency to every `--mcp` session even when no MCP tools are called
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance (MCP) |
| **File(s)** | `agentkthx/cli/agent_factory.py:513-514` (`MCPManager.connect_all`) |
The scaffold's `connect_all` is eager — every configured server is spawned at agent construction, the `initialize` handshake runs, and `tools/list` is queried for every server, all before the agent's first turn. For a user with 4–5 MCP servers configured (filesystem + git + memory + serena + audit), this adds ~1–3 seconds to `agentkthx chat` startup. If the user's session ends up not calling any MCP tools (e.g., they just ask the model a coding question and the agent uses built-in `shell`/`read_file`), the startup cost was wasted.
Recommendation: lazy mode — `MCPManager.connect_all` records the configured servers but does not spawn them; a server is spawned on first tool call to that server's namespace. The agent's tool registry still shows all the namespaced tool names (queried lazily via a separate `tools/list` on first access), so the model can pick the tool; the actual subprocess spawn happens when the tool is dispatched. Trade-off: the first tool call to a server pays the spawn + handshake latency (~200–500ms), which the model can't predict. Mitigation: warm-up the most-likely servers (filesystem, git) eagerly and the rest lazily.
**Impact:** Every `--mcp` session pays ~1–3s of startup latency for servers that may never be used. Not a correctness bug but a UX regression vs. the non-MCP path.
---

#### MCP-05: No `notifications/tools/list_changed` handling — runtime tool surface changes invisible to the registry
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness (MCP) |
| **File(s)** | `agentkthx/mcp/client.py` (no handler for the notification) |
The MCP protocol allows a server to push `notifications/tools/list_changed` when its tool surface changes at runtime (e.g., a filesystem MCP server that adds/removes tools based on which directories are accessible). The scaffold's `MCPClient` does not register a handler for this notification — `StdioTransport._read_response` skips any message without an `id` field (line 242-244), so the notification is silently dropped. The agent's `ToolRegistry` therefore shows the tool surface as it was at `connect_all` time; tools added or removed at runtime are invisible until the next `connect_all` (typically the next session).
Recommendation: register a per-server callback in `MCPClient` for `notifications/tools/list_changed`. When fired, the client re-queries `tools/list`, diffs against the previous list, and notifies the `MCPManager` to add/remove the shim Tools in the agent's `ToolRegistry`. The manager needs an `unregister_tool(name)` method on `ToolRegistry` (currently has `register_tool` only). Trade-off: tool removal mid-session is a slight surprise to the model if it just picked a tool that's now gone — wrap the removal in a small grace period (1 turn) and emit a system message.
**Impact:** Tools added/removed at runtime by MCP servers are invisible to the agent until session restart. Most current MCP servers have a static tool surface, so this is rarely hit in practice — but it's a latent gap that will bite when dynamic-tool servers become common.
---

<!-- Filed at the R07.23 re-audit (commit 1d7f1ee). All three from the new mcp search + install surface. -->

## Priority Matrix
| Timeline | Findings |
|----------|----------|
| **Near term (R07.24–R07.25)** | ROB-33 (non-destructive Windows liveness check — unblocks the flagship turbo start → chat workflow), ROB-31 (entitlement-aware fallback filter), ROB-02 (join worker threads), ROB-06 (deterministic Windows conn release), ROB-15 (single-transaction add — pairs naturally with the now-closed ROB-18 RLock), SEC-09 (warn on non-HTTPS ACP), SEC-13 (require-plugin-pins mode), SEC-20 (`mcp install` `--no-overwrite` + TTY prompt — pairs with MCP-02 to make the MCP trust boundary enforceable), MAINT-03 (drop strategy 5 of `normalize_args`), MAINT-22 (streaming `_build_body()` virtual), MAINT-23 (lift retry-loop skeleton to CloudBackend — closes ROB-29 in the same move), MAINT-01 (extract `ChatSession` — would also close MAINT-27), TEST-01 (integration test tier), TEST-03 (add `FakeStreamingBackend`), MCP-01 (thread+queue for StdioTransport — unblocks Ctrl+C and per-call timeouts), MCP-02 (MCP server sha256 pins — SEC-13 analogue, same enforcement-mode gap) |
| **Short term (R07.25–R07.27)** | MAINT-27 (move `/sh` branch to a `cmd_sh` method when MAINT-01 lands), FEAT-03 (tool output JSON Schema), TEST-09 (plugin streaming-path integration test), TEST-10 (live-shape free-model contract test), TEST-11 (live npm/GitHub response-shape contract test — TEST-09 family), MCP-03 (oneOf/anyOf/$ref schema flattening — arguments_json fallback), MCP-04 (lazy MCP server startup — eager mode is a UX regression for multi-server configs), ROB-41 (`registry.search_all` per-source failure surfacing — plain-mode network-error hint; pairs with TEST-11 for the user-facing error path) |
| **Medium term (R08.00+)** | FEAT-05 (plugin sandbox), FEAT-06 (streaming tool-arg deltas), FEAT-07 (conversation export/import), FEAT-08 (paid_only free-TIER filter mode), TEST-04 (rollback tests), TEST-05 (bump-version test portability), TEST-07 (update_check failure paths), MCP-05 (notifications/tools/list_changed handler — wait until dynamic-tool MCP servers are common) |
Closed/wontfix placements from earlier revisions are archived in `deltas.md`'s closure timeline (R07.00 → R07.21). The R07.21 closure batches closed 13 findings total: batch 1 — ROB-18, ROB-35, ROB-36, ROB-38, ROB-39; batch 2 — ROB-09, ROB-17, ROB-20, ROB-25, ROB-30, MAINT-24, MAINT-25, MAINT-26. R07.22 closed none + added MCP-01..05. R07.23 closed none + added SEC-20, ROB-41, TEST-11; the latent memory first-user preservation bug was fixed in-code without a prior audit finding to close. Tiers are cumulative, not reset per release.
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
