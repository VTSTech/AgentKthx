# Improvement & Enhancement Audit
**AgentKthx v0.7.19 (R07.19 — Primary User + host-environment probe + dynamic models-table Name column (ROB-37))**
**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-10-02  
**Commit:** R07.19 (nine-commit release: feature + eight follow-ups) | **Test Suite:** 2514 passed / 16 skipped  
112 Findings | 35 Open | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  
Severity (open): 0 High | 14 Medium | 21 Low  
35 OPEN | 77 archived in deltas.md (70 CLOSED + 7 WONTFIX) — generate_audit_dash.py merges both for the dashboard

> **R07.18 delta (2026-10-02, re-audit of commit 155b9c2):** No closures this pass — all 34 carried-forward OPEN findings were re-verified in the current tree (the two `verify_open_findings.py` heuristic flags — ROB-06 `PATTERN_GONE`, FEAT-03 `PATTERN_GONE` — were investigated manually and are false positives: `stream_gen.close()` survives at streaming.py:982, and FEAT-03 remains a missing feature). Two new findings from the R07.17/R07.18 surface, both Robustness-Low: ROB-35 (`parse_shared_args` falsy-coalescing silently drops the documented `0` sentinel — `--repeat-last-n 0` / `--num-ctx 0` reach `SharedConfig` as `None`, or the env-var value when `AGENTKTHX_*` is set) and ROB-36 (`_parse_token_size` accepts `inf`/`1e400` numeric parts → `OverflowError` escapes both argparse's clean-error path and `_env_int`'s `except (ValueError, TypeError)` — raw traceback on the main CLI). R07.17/R07.18 shipped regression tests (`test_num_batch.py`, `test_r07_18_repeat_penalty.py`, `test_r07_19_quant_and_tokens.py`, `test_cli_footer.py` — the R07.16 no-regression-file gap noted in the previous brief is closed as of these releases) but closed none of the register's near-term tier. Line references updated for the R07.17/18 shifts (ROB-06 → streaming.py:972-990, ROB-33 ladder → agent_factory.py:562-580, ROB-34 → chat.py:287-292, MAINT-01 → 1,368 lines, MAINT-25 → agent_factory.py:278-344 + parser.py:218). Register: 111 findings — 36 open / 68 closed / 7 wontfix (68% archived). Suite 2051 → 2211 (+160 across the two releases).

> **R07.19 delta (2026-10-02, feature release — Primary User + host-environment probe):** One closure. **ROB-34 CLOSED** — byte-level verification of the R07.18 tree showed the no-readline fallback prompt already carries a proper CSI sequence (`"\033[33mYou:\033[0m "` at chat.py:291), not the bare-ESC form (`"\033You:\033 "`) the finding described, so the mangled-`ou:`-prompt defect was not present in the code the finding was filed against; R07.19 then rewrote both prompt branches entirely for the Primary User feature (the name replaces the hardcoded `You:`), and a source-level pin in `tests/test_r07_19_primary_user_env.py` now asserts the bare-ESC form can never return. The R07.18 register note describing the pattern as re-verified stands as a false-positive verification — noted here for the record. The feature release also adds the host-environment probe (`agentkthx/core/environment.py`: stdlib-only OS/distro/kernel/arch detection appended to every system prompt as a `# Host Environment` section so the model knows which argument syntax to pass to the shell tool — cmd.exe on Windows vs /bin/sh POSIX on Linux/macOS; `AGENTKTHX_NO_ENV_PROBE=1` opts out; BitNet sessions get a compact single line to respect the lean-prompt crash threshold) and the Primary User chat flow (`--user` flag > `AGENTKTHX_USER` env > TTY-only interactive naming prompt with the OS login name as default; control-character/ANSI sanitization capped at 32 chars since the name renders on every REPL turn). **42 new tests in `tests/test_r07_19_primary_user_env.py`**; one pre-existing source pin updated to the renamed-prompt contract (`test_zai_session_fallback.py::TestChatPromptColor`), one exact-equality system-prompt assertion relaxed to a prefix assertion (`test_agent_setup_subsystem.py`). Suite 2211 → 2253 (+42). Register: 111 findings — 35 open / 69 closed / 7 wontfix (68% archived).

> **R07.19 delta #2 (2026-10-02, follow-up commit — dynamic models-table Name column):** One finding opened and closed in the same release (second R07.19 commit, no version bump). **ROB-37 CLOSED** — the `agentkthx models` table fixed its Name column at 48 (local) / 50 (cloud) chars while R07.18's no-truncation policy lets longer names render past the slot, pushing Size/Quant/Context right for that row; the user's live `--tool-support` listing contained `krith/meta-llama-3.2-1b-instruct-uncensored:IQ4_XS` (50 chars, 2 over) and the broken grid is visible in the report. Fix: NAME_W is now measured from the longest name in the loaded (and free-filtered) models list — `max(48/50 floor, longest)` — computed before the header/separator render, so header, separator and every data row share one width; both layout formulas grow (local 76+W, cloud 43+W) and the floors keep short listings byte-identical to R07.18. **8 new tests in `tests/test_r07_19_models_table_width.py`** driving the real `cmd_models` over stub Ollama/cloud backends (cross-row grid alignment, widening formulas, floor preservation, no truncation, premise pin). Suite 2253 → 2261 (+8). Register: 112 findings — 35 open / 70 closed / 7 wontfix (69% archived). Full detail in `audit/deltas.md` (archived section).

> **R07.19 delta #3 (2026-10-02, follow-up commit #2 — Tool Reference real example arguments):** No register entry — prompt-rendering polish, not a defect finding. The system prompt's Tool Reference table rendered every string parameter as the generic `"..."` placeholder (user report: the shell row `{"command": "...", "timeout": 10}` read like the prompt itself had been truncated). String params now render REAL values — curated per param name (`shell` → `"echo Hello, World!"`; 16 names covering the builtin registry), else the param's own non-empty string default (todo `priority` → `"medium"`), else `"..."`; enum params show their first always-valid value, booleans render `true`, objects render `{}`. The "When to use" cell cap moved 40 → 60 chars with word-boundary cutting so `Execute shell commands (with security restrictions)` fits in full. **17 new tests in `tests/test_r07_19_tool_examples.py`** (exact shell-row pin, no-placeholder sweep over the full builtin registry, per-type rendering rules, fallback precedence, word-boundary cap). Suite 2261 → 2278 (+17).

> **R07.19 delta #4 (2026-10-02, follow-up commit #3 — nova-* → kthx-* soul rename + kthx-helper accuracy review):** No register entry — branding rename plus a requested accuracy pass, not a defect finding. The three bundled souls rename `nova-helper` → `kthx-helper`, `nova-skills` → `kthx-skills`, `nova-trading` → `kthx-trading` (directories via git mv, soul.json `name`, AGENTS.md self-references, the trading soul's `Nova Trading Analyst` branding → `Kthx Trading Analyst`) — the souls were the last user-visible surfaces still carrying the pre-R06.0 AgentNova naming. Every live reference adjusted: the Agent constructor default (`agent_setup.py`, 3 sites), a loader comment, the ARCH-04 regression pins in `test_r07_13_arch_closures.py` (5 sites), `docs/TESTS.md` (25 sites) + `docs/ARCH.md` (tree rows + example + the stale "crypto-signals demo" comment corrected to TSX/TSX-V paper trading), the `brief.md` souls line, and the MAINT-24 finding's soul example. Historical changelog sections keep their `nova-*` mentions — they record what the files were called at the time. The user-requested accuracy review of kthx-helper verified against the current tree: all 16 `allowedTools` names exist in the 17-tool builtin registry (web_search deliberately excluded — AGENTS.md documents its absence as an escalation trigger), the static Tool Reference table's argument names all match the registry, every `{{...}}` placeholder in SOUL.md is loader-substituted, and AGENTS.md's orchestrator claims hold (`AgentCard.tools`/`priority`/`fallback`, concat/first/vote/best merge strategies, pipeline forwards Final Answer only). ONE defect fixed: the Common Mistakes hallucination example called `17 - 9` WRONG while the Time Calculation section teaches `17 - 9` as the CORRECT 9 AM→5 PM conversion (5 PM → 17) — the example now hallucinates `15 - 4` against the 24/8/6 apples example. **11 new tests in `tests/test_r07_19_soul_rename.py`** pin the rename and the review conclusions (load-by-new-name, no stale dirs/files, constructor signature, display name, allowedTools ⊆ registry, table tools real, placeholder support, no contradictory example). Suite 2278 → 2289 (+11).

> **R07.19 delta #5 (2026-10-02, follow-up commit #4 — lint burn-down):** No register entry. Installed black + ruff in the dev environment and ran the CI lint pair for real: one file failed both — `tests/test_r07_19_models_table_width.py` (follow-up #1) carried an unused `pytest` import (F401, plus one more auto-fixable) and two black 26.x line-join spots. `ruff --fix` + `black` applied; `ruff check agentkthx/ tests/` → all checks passed, `black --check` → 203 files unchanged, suite unchanged at **2289 passed / 16 skipped**. Zero logic change.

> **R07.19 delta #6 (2026-10-02, follow-up commit #5 — /souls + /soul chat commands + config env-var reference):** No register entry — feature addition, not a defect finding. The chat REPL gains `/souls` (list the bundled souls, ✓ = active, mirroring `/skills`) and `/soul` (show or switch the active persona mid-session). Backed by two new APIs: `SoulLoader.list_souls()` — bundled-soul discovery at Level 1 (importlib.resources first, filesystem fallback, best-effort skip on unloadable entries, mirroring `SkillLoader.list_skills()`), and `AgentSetupMixin.switch_soul()` — mirrors the startup `--soul` path: `allowedTools` re-filter on the CURRENT registry (tools can be filtered out, never added), prompt rebuilt as soul + accumulated `--skills`/`/skill` text + `# Host Environment`, ToolParser refreshed, memory system message swapped while conversation history is untouched. The `/skill` command now also accumulates skill blocks into `agent._skills_prompt` so mid-session skill loads survive soul switches. `agentkthx config` reference burn-down: the env-var reference was missing everything added after its last revision — the entire Mistral (8), OrcaRouter (7) and Pollinations (8) plugin surfaces, the R07.19 core vars (`AGENTKTHX_USER`, `AGENTKTHX_NO_ENV_PROBE`, `AGENTKTHX_NO_UPDATE_CHECK`), `AGENTKTHX_MAX_API_RETRIES` / `AGENTKTHX_PARALLEL_TOOLS`, `AGENTKTHX_ACP` / `_ACP_URL` / `_PLUGIN_PATH` / `_USER_AGENT`, `OPENAI_MAX_429_RETRIES`, `OLLAMA_MODELS`, `HF_BASE_URL_LEGACY`, and a new Display/platform group (`NO_COLOR`, `CLICOLOR`, `CLICOLOR_FORCE`, `AGENTKTHX_GLYPHS`, `XDG_CACHE_HOME`, `XDG_STATE_HOME`) — now 96 listed vars with zero stale entries; `--urls` 9 → 12 backend URLs; `--full` gains the three plugin sections. **68 new tests in `tests/test_r07_19_soul_commands.py`** (discovery pins, switch behavior pins incl. the tool-filter and memory-swap contracts, chat wiring source pins, per-var config reference pins + a drift pin cross-checking every `os.environ` literal in `agentkthx/` against the printed reference). Suite 2289 → **2357 passed, 16 skipped, 0 failures**. Register unchanged: 112 findings — **35 OPEN / 70 CLOSED / 7 WONTFIX (77 archived, 69%)**.

> **R07.19 delta #7 (2026-10-02, follow-up commit #6 — alphabetical `-h` output):** No register entry — UX polish, not a defect finding. User report: `agentkthx chat -h` listed options in registration order (the `add_agent_args()` build order), making flags hard to find past ~30 entries; the same applied to every other `-h`. Two-layer fix. (1) `SortedHelpFormatter` (`agentkthx/cli/parser.py`) — an `argparse.HelpFormatter` subclass sorting the "options:" listing AND the `usage:` line by each flag's primary long name (`-m, --model` under "model", `-h, --help` under "help", the bare `--` llama-server passthrough in `turbo start` first); positionals keep registration order everywhere (semantic order: `run prompt`, `soul path`, `turbo start model`). Applied recursively by `apply_sorted_help()` — legal post-construction because argparse reads `formatter_class` at help-render time — at the end of `create_parser()` and again in `main()` after plugin CLI subparsers register (idempotent). `apply_sorted_help()` also re-sorts each `_SubParsersAction` registry in place (`choices` IS `_name_parser_map` — rebinding would desync parse lookup from the rendered metavar — plus the `_choices_actions` listing) so late-registered plugin commands land alphabetically instead of after `version`. (2) Registration order alphabetized: plugins before run, tools before turbo, turbo status before stop. **59 new tests in `tests/test_r07_19_help_sort.py`** (per-parser options-section sortedness + usage/listing order agreement across all 20 parsers, root metavar/command listing + turbo sub-listing, plugin insertion, `_sort_key` contract, positional preservation, wiring + parse pins). Suite 2357 → **2416 passed, 16 skipped, 0 failures**. Register unchanged: 112 findings — **35 OPEN / 70 CLOSED / 7 WONTFIX (77 archived, 69%)**.

> **R07.19 delta #8 (2026-10-03, follow-up commit #7 — arrow-key model picker + optional `--model` + `agentkthx souls`):** No register entry — feature work, not a defect finding. User request: add a `souls` subcommand, replace the chat `/models` list with an interactive arrow-navigable model switch, and make `-m/--model` optional on chat by invoking that switcher at startup when omitted. (1) New `agentkthx/cli/picker.py` — `ArrowMenu`, a pure-stdlib arrow-key single-select menu (termios + cbreak on POSIX with ISIG preserved so Ctrl+C maps to cancel instead of killing the REPL, msvcrt on Windows, numbered-input fallback on non-TTY stdin); rendering is a pure string and navigation is plain state mutation, so the core is unit-tested without a terminal; the frame redraws in place (cursor-up + clear-line) and is wiped on exit; the viewport is clamped to the terminal height for the chat scroll region. (2) `/models` in chat now opens the picker over the (still filterable) model list and switches via the same `apply_model_switch()` path as `/model <name>` (ROB-14 re-derive semantics preserved; picking the current model is a no-op; piped stdin keeps the plain listing); outcome printing factored into the shared `_report_model_switch()` helper. (3) `agentkthx chat` without `-m/--model` (TTY, no ACP session, no `AGENTKTHX_MODEL` override) runs the picker BEFORE `_build_agent` — cloud backends probed in OPENAI mode, local in OPENRE, mirroring `agentkthx models`; failures/empty/cancel degrade to the classic default-model resolution (bitnet discovery → `config.default_model`); `run`/`agent`/`test` unchanged by design. (4) New `agentkthx souls` subcommand (`agentkthx/cli/commands/souls.py`, registered between `soul` and `test`) — lists the bundled souls with the constructor-default marked via `AgentSetupMixin.__init__` signature introspection (drift-proof), and `souls <name>` prints a manifest-level detail view with fuzzy-match on unknown names. **96 new tests in `tests/test_r07_19_model_picker.py`** (+2 auto-expanded dynamic help-sort walk instances for the new subparser); the MAINT-18 source pin in `test_r07_12_quick_wins.py` moved with the shared reporter. Suite 2416 → **2514 passed, 16 skipped, 0 failures**. Register unchanged: 112 findings — **35 OPEN / 70 CLOSED / 7 WONTFIX (77 archived, 69%)**.
---
## Table of Contents
- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
- [Priority Matrix](#priority-matrix)
---
## Executive Summary
This re-audit covers AgentKthx at commit `155b9c2` (R07.18, PyPI 0.7.18). The codebase comprises 124 Python source files totaling ~59,794 LOC, with ~33,262 lines of tests across 74 test files — the suite passes **2211 tests / 16 skipped in ~12s**, with CI on Python 3.12/3.13 plus a parallel coverage job and a promoted-to-required lint job. The R07.00 modularization (5-mixin `Agent` composition, 23-file `cli/` package) remains stable, and the split-register layout holds: this file carries the 36 OPEN findings, while `deltas.md` archives the 75 closed/wontfix findings with the full closure timeline (R07.00 → R07.16).
R07.17 and R07.18 are parameter-surface releases: R07.17 added the `num_batch` prompt-processing batch-size parameter throughout the agent stack (primary support for Ollama per-request `options.num_batch`, graceful no-op elsewhere), CLI footer batch-size + per-response TPS segments, and the thermometer VS16 spacing fix; R07.18 fixed a real parity bug — llama-server/TurboQuant/BitNet in OpenRE mode silently dropped every sampling param except `temperature`/`n_predict`/`stop` — with a generic kwargs-forwarding loop in both `_generate_completion` and `_stream_completion` (agent-internal kwargs explicitly excluded), exposed `repeat_penalty`/`repeat_last_n` as CLI flags + `/param` entries + `Agent()` constructor params (making BitNet's `repeat_penalty=1.3` a default rather than a hardcode), added `_parse_token_size` (`--num-ctx 128k`/`1m`/`2g`), `fmt_token_size` (`128K`/`1M` display), weight-quantization detection in the footer (🧊 segment) and a Quant column in `agentkthx models`, and a new 839-line `scripts/diagnose_ollama.sh` health-checker. Both releases shipped dedicated regression-test files — the R07.16 no-regression-file gap is closed. The new surface is where both new findings live (ROB-35, ROB-36 — both in `shared_args.py`'s flag-parsing/coalescing layer).
Cumulative closure state: **69 CLOSED + 7 WONTFIX of 111 findings (76 archived, 68%)**. Closures span R07.00 → R07.15 (R07.15 closed ten across two batches; the closure history and per-release test-count deltas live in `deltas.md`). R07.16 closed none (+4 new findings); R07.17/R07.18 closed none (+2 new findings) — the near-term tier identified in the R07.16 re-audit is unchanged and still waiting. The highest-leverage remaining closures: ROB-33 (the Windows `os.kill(pid, 0)` process-kill gotcha on the flagship `turbo start` → `chat` workflow), ROB-31 (entitlement-aware Pollinations fallback filtering), the MAINT-23/ROB-29 retry-loop family (one `CloudBackend` primitive closes ~160 LOC of duplication across Mistral + Pollinations), MAINT-01 (extract `ChatSession` from the now-1,368-line `cmd_chat`), and TEST-01 (the integration-test tier — still the largest structural gap; every one of the 2211 suite tests remains a mocked unit test).
Process note: this pass re-verified all 34 carried-forward OPEN findings against the R07.18 tree — including manual investigation of the two `verify_open_findings.py` heuristic flags (ROB-06 and FEAT-03 `PATTERN_GONE` verdicts were false positives: the streaming close-guard survives at streaming.py:982 and FEAT-03 is a missing feature, so its absence is expected). Line references shifted by R07.17/R07.18 were updated (ROB-06 → `streaming.py:972-990`, ROB-33 → `agent_factory.py:562-580`, ROB-34 → `chat.py:287-292`, MAINT-01 → 1,368 lines, MAINT-25 → `agent_factory.py:278-344` + `parser.py:218`), and the new findings continue the established ID numbering (ROB-35/36 after open ROB-34).
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
| MAINT-01 | Medium | Maintainability | OPEN | cmd_chat is a 1,368-line single function with 25+ nested closures and no slash-command dispatcher |
| MAINT-03 | Medium | Maintainability | OPEN | normalize_args strategy 5 (prefix/substring matching) is dangerously permissive — {"e": "..."} matches expression |
| MAINT-22 | Medium | Maintainability | OPEN | Streaming path bypasses _build_mistral_body — random_seed/safe_prompt/prompt_cache_key/OpenAI-only kwarg stripping NOT applied on streaming (only non-streaming) |
| MAINT-23 | Medium | Maintainability | OPEN | _make_api_request + _iter_sse_lines duplicate ~80 LOC of retry-loop skeleton (third consecutive cloud backend — ROB-29/MAINT-11 pattern); helpers are shared but the loop itself is copy-paste |
| FEAT-03 | Medium | New Features | OPEN | Tool output schema validation via JSON Schema |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all 2051 tests are mocked unit tests; slash-command dispatcher untested |
| TEST-03 | Medium | Testing | OPEN | FakeBackend in test_agentic_loop_subsystem.py omits generate_completions_stream — streaming callbacks unexercised |
| ROB-09 | Low | Robustness | OPEN | validate_path uses os.path.abspath, doesn't follow symlinks — read_file("/tmp/symlink_to_etc_passwd") bypasses |
| ROB-17 | Low | Robustness | OPEN | Token-tier pruning can leave a single over-budget message (loop exits when len-1) — documented gap |
| ROB-18 | Low | Robustness | OPEN | threading.Lock (not RLock) — brittle if future code adds nested locked calls |
| ROB-20 | Low | Robustness | OPEN | agent.num_ctx (public) vs agent._num_predict (private) naming inconsistency in apply_model_switch |
| ROB-25 | Low | Robustness | OPEN | generate() vs _generate_with_auth() signature defaults mismatch (None vs 0.7/2048) — confusing |
| ROB-28 | Low | Robustness | OPEN | MistralBackend.list_models catches bare Exception on top of HTTPError/URLError — masks KeyError/AttributeError as "discovery failed" with no traceback |
| ROB-29 | Low | Robustness | OPEN | MistralBackend _iter_sse_lines + _make_api_request have ~80 LOC duplicated retry/backoff logic — mirrors the MAINT-11 OrcaRouter pattern closed in R07.08 |
| ROB-30 | Low | Robustness | OPEN | _fetch_model_cards catch-all Exception silently degrades to the 13-model static catalog — card-parse bugs masquerade as "network down" (ROB-28 pattern, third backend) |
| ROB-35 | Low | Robustness | OPEN | parse_shared_args or-coalescing silently drops the documented 0 sentinel — --repeat-last-n 0 / --num-ctx 0 reach SharedConfig as None (or the env-var value) |
| ROB-36 | Low | Robustness | OPEN | _parse_token_size accepts inf/1e400 numeric parts — OverflowError escapes argparse's clean-error path and _env_int's except clause (raw traceback) |
| MAINT-24 | Low | Maintainability | OPEN | _build_tool_section docstring still promises ReAct format instructions the body no longer includes — custom souls without their own block get none |
| MAINT-25 | Low | Maintainability | OPEN | Local-backend tool-support auto-detection has no opt-out — debug hint suggests --force-react=False, which the store_true argparse flag rejects |
| FEAT-05 | Low | New Features | OPEN | Plugin sandboxing via restricted register() namespace + audit hooks |
| FEAT-06 | Low | New Features | OPEN | Streaming tool-call argument deltas (function_call_arguments.delta SSE events) |
| FEAT-07 | Low | New Features | OPEN | Conversation export/import to OpenResponses-format JSON |
| FEAT-08 | Low | New Features | OPEN | Free TIER (paid_only boundary on bare GET /models) unreachable — FREE_ONLY exposes only the 16 zero-cost models, not the ~102 Quest-Pollen-eligible text models the key can run |
| TEST-04 | Low | Testing | OPEN | No test coverage for agent_mode.py rollback functionality (822 LOC, key feature) |
| TEST-05 | Low | Testing | OPEN | test_bump_version_script.py tests shell script via subprocess — fails on Windows/no-bash |
| TEST-07 | Low | Testing | OPEN | No test for update_check module's network-failure paths (URLError, socket.timeout, malformed JSON) |
| TEST-09 | Low | Testing | OPEN | Plugin scaffolds don't include a "agent loop streaming path actually calls through" smoke test — R07.09.0 streaming bug caught by user testing, not test suite |
| TEST-10 | Low | Testing | OPEN | Zero live-shape coverage for free-model detection — v0.1.2 shipped with _card_is_free blind to the live currency-only encoding while all 83 fixture tests stayed green |
---
## R07.16 New Findings
Four findings, all from the R07.16 surface (TurboQuant lifecycle + chat-side auto-derivation + tool-calling auto-detection + Windows fallbacks). No closures this pass — all 30 carried-forward OPEN findings re-verified in current code.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| ROB-33 | Medium | Robustness | `agentkthx/plugins/turboquant/turbo.py:159-183`, `agentkthx/cli/agent_factory.py:490-497` | _is_process_alive probes liveness with os.kill(pid, 0) — on Windows that TERMINATES the target; now on the chat startup path |
| MAINT-24 | Low | Maintainability | `agentkthx/soul/loader.py:726-728,777-786` | _build_tool_section docstring still promises ReAct format instructions the body no longer includes |
| MAINT-25 | Low | Maintainability | `agentkthx/cli/agent_factory.py:227-290`, `agentkthx/cli/parser.py:191` | Tool-support auto-detection has no opt-out; debug hint suggests --force-react=False which argparse rejects |
---
## R07.18 New Findings
Two findings, both from the R07.18 flag-parsing surface (`_parse_token_size` + the `SharedConfig` coalescing layer). No closures this pass — all 34 carried-forward OPEN findings re-verified in current code.
| ID | Severity | Category | File(s) | Title |
|----|----------|----------|---------|-------|
| ROB-35 | Low | Robustness | `agentkthx/shared_args.py:448-462` | parse_shared_args or-coalescing silently drops the documented 0 sentinel — --repeat-last-n 0 / --num-ctx 0 reach SharedConfig as None (or the env-var value) |
| ROB-36 | Low | Robustness | `agentkthx/shared_args.py:480-543`, `:465-478` | _parse_token_size accepts inf/1e400 numeric parts — OverflowError escapes argparse's clean-error path and _env_int's except clause (raw traceback) |
---
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
| **File(s)** | `agentkthx/core/streaming.py:972-990` |
The KeyboardInterrupt handler calls `stream_gen.close()` to release the underlying urllib response. The comment (lines 965-971) explains this is for ROB-05 (R06.57). However, on Windows, `urllib.request.urlopen` returns an `http.client.HTTPResponse` whose `.close()` may not immediately close the TCP connection — it relies on GC. On long sessions with many Ctrl+C interrupts, this can exhaust the connection pool. On Linux/macOS, `close()` calls `flush()` and `shutdown(SHUT_WR)` synchronously.
Recommendation: Explicitly call `response.fp.close()` and `response.release_conn()` if available. For urllib, use `response.close()` directly and catch `AttributeError` for older Python versions. Consider using `http.client.HTTPConnection` directly for finer-grained control.
**Impact:** Connection exhaustion on Windows under heavy Ctrl+C usage — Linux/macOS unaffected but the cross-platform promise is broken.
---

#### ROB-09: `validate_path` uses `os.path.abspath`, doesn't follow symlinks
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/helpers.py:491-555` |
The function checks the resolved absolute path against allowed directories using `os.path.abspath(path)`. If `/tmp/safe_link` is a symlink to `/etc/passwd`, `validate_path("/tmp/safe_link")` returns `(True, "")` because `os.path.abspath` doesn't follow symlinks — `/tmp/safe_link`'s abspath starts with `/tmp`. The actual file accessed via `open()` will follow the symlink to `/etc/passwd`.
Recommendation: Use `os.path.realpath(path)` instead of `os.path.abspath(path)` for the security check. `realpath` resolves symlinks recursively. Add a test case: create a symlink to `/etc/passwd` and verify `validate_path` rejects it.
**Impact:** Symlink-based path traversal — a model that creates a symlink via `shell` tool and then calls `read_file` on it can read protected files.
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
#### ROB-17: Token-tier pruning can leave a single over-budget message (loop exits when len-1) — documented gap
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/memory.py:371-382` |
Re-verified in current code (detail section authored during the R07.16 re-audit — previously a summary-only row). The token-tier loop is `while (len(non_system) - drop) > 1 and acc > target:` — it deliberately keeps at least one message, so when a single message's own token estimate exceeds the entire `max_tokens` budget (a pasted 100K-char file read, for example), the loop exits with `acc > target` still true and the over-budget window ships to the backend as-is. The code comment documents the intent ("keeping at least one message") but not the failure mode: the tier silently stops enforcing its budget exactly when the budget is most exceeded.
Recommendation: after the loop, if `kept` is a single message whose estimate still exceeds the budget, truncate its content head (keeping the tail, which is usually the recent part) or emit a visible warning. Either is better than silently defeating the tier.
**Impact:** A single oversized message silently bypasses the token tier the suite of R07.06 tests validates.
---
#### ROB-18: PersistentMemory write locks are threading.Lock (not RLock) — brittle if future code adds nested locked calls
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/persistent_memory.py:73-83` |
Re-verified in current code (detail section authored during the R07.16 re-audit — previously a summary-only row). The per-DB-path write-lock registry builds plain `threading.Lock()` objects (lines 77-83). Every call site currently acquires once, but the moment a locked helper calls another locked helper (the exact shape ROB-15's single-transaction fix would create — a transaction wrapper around `_write_message`), the second acquire deadlocks. `threading.RLock` costs a slightly slower acquire and removes the entire failure class.
Recommendation: switch `_get_write_lock` to `threading.RLock()` as part of the ROB-15 transaction work (one-line change, do it before rather than after).
**Impact:** Removes a deadlock class from the code path the ROB-15 fix will build on.
---
#### ROB-20: agent.num_ctx (public) vs agent._num_predict (private) naming inconsistency in apply_model_switch
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/cli/agent_factory.py` (`apply_model_switch`) |
Re-verified in current code (detail section authored during the R07.16 re-audit — previously a summary-only row). `apply_model_switch` reads `old_ctx = agent.num_ctx` and `old_predict = getattr(agent, "_num_predict", None)`, then writes `agent.num_ctx = new_ctx` and `agent._num_predict = new_predict` — one attribute public, its sibling private, plus the `_num_ctx_explicit`/`_num_predict_explicit` markers. R07.16 added a second consumer of the same asymmetry (the `_on_insufficient_credits` model-switch callback reads the `changes` dict this function produces), so the naming inconsistency now propagates further than the original `/model` command.
Recommendation: expose `num_predict` as a public property (backed by `_num_predict`) or rename both to private with public accessors — one convention across the switch surface.
**Impact:** A consistent API surface for every future model-switch consumer; removes the getattr-with-default guessing game.
---
#### ROB-25: generate() vs _generate_with_auth() signature defaults mismatch (0.1/8192 vs 0.7/2048)
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/backends/base.py:81-86`, `agentkthx/plugins/zai/zai.py:852-858`, `agentkthx/plugins/orcarouter/orcarouter.py:871-877` |
Re-verified in current code (detail section authored during the R07.16 re-audit — previously a summary-only row). The abstract `generate()` declares `temperature: float = 0.1, max_tokens: int = 8192`; both `_generate_with_auth` implementations (ZAI, OrcaRouter) declare `temperature: float = 0.7, max_tokens: int = 2048`. Any caller that relies on signature defaults — or that moves between the public and auth paths — silently gets different sampling behavior and a 4× token-cap difference depending on which entry point handled the call. No correctness bug today (the agent loop always passes explicit values), but the asymmetry is a trap for library users and new backends.
Recommendation: define the sampling/cap defaults once (module-level constants or `BackendConfig` fields) and have both signatures reference them.
**Impact:** One consistent sampling baseline across backends and entry points.
---
#### ROB-28: MistralBackend.list_models catch-all Exception masks real bugs
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/mistral/mistral.py:500-505` |
`list_models()` wraps catalog fetch + parse in `except Exception` on top of the specific `HTTPError`/`URLError` handlers, returning the static catalog with only a debug-mode print. A `KeyError`/`AttributeError` introduced by a gateway schema change (or by future code edits) is indistinguishable from "network down" — no traceback, no telemetry, silent degradation to the static list.
Recommendation: catch only `(urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, ValueError)` at the boundary; let unexpected exceptions crash loudly (the agent loop's resilience layer already handles backend exceptions).
**Impact:** Catalog-shape regressions look like outages; users browse a stale static list with no signal that live discovery is broken.
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
#### ROB-30: _fetch_model_cards catch-all Exception silently degrades to the static catalog
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/pollinations/pollinations.py:704-711` |
Same pattern as ROB-28 (Mistral), third occurrence: `_fetch_model_cards` catches bare `Exception` after the specific HTTP/URL handlers and returns `{}` — the caller falls back to the 13-model static `POLLINATIONS_MODELS` catalog with only an `AGENTKTHX_DEBUG` print. Card-shape bugs (a renamed field, a `None` where a dict is expected) present as "catalog unreachable". The blast radius is larger than Mistral's: the live-card cache also feeds `_first_free_model()` and `healthy_fallbacks()`, so FREE_ONLY redirect quality silently degrades too.
Recommendation: narrow the catch to `(urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError)` — the documented failure modes — and let card-shape bugs surface with tracebacks.
**Impact:** Live-catalog regressions masquerade as outages; FREE_ONLY and fallback ordering quietly degrade to a 13-model stale view.
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
| **File(s)** | `agentkthx/plugins/turboquant/turbo.py:159-183`, `agentkthx/cli/agent_factory.py:562-580` |
New in R07.16. `_is_process_alive(pid)` uses `os.kill(pid, 0)` as an existence probe, then checks `/proc/<pid>/stat` for zombies. On Windows, `os.kill` with any signal other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` does not probe — per the `os.kill` documentation, the target is **unconditionally killed via `TerminateProcess`** with the signal value as the exit code (0 here). The `/proc` zombie check is Linux-only and its absence handler just falls through to `return True`, so Windows always takes the destructive path. Pre-R07.16 this only endangered `turbo` command flows; R07.16 placed `TurboState.load()` (which calls `_is_process_alive(state.pid)`) into `_get_local_catalog_defaults` — executed on EVERY chat startup against a non-cloud backend with a local base_url, and for `--backend ollama` too, since the state file is global (`~/.agentkthx/turbo.state`). Concrete failure: `agentkthx turbo start <model>` (server running, state file present) followed by `agentkthx chat --backend turboquant` on Windows terminates the just-started server during the liveness check. Worse: if the state file is stale and the OS reused the pid for an unrelated process, chat startup kills that process.
Recommendation: gate the probe by platform — on Windows use a non-destructive check (`ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)` + `GetExitCodeProcess` comparing against `STILL_ACTIVE`, all stdlib); keep `os.kill(pid, 0)` on POSIX. Add a regression test that runs the liveness check against a live child process and asserts it still exists afterwards (fails on Windows today).
**Impact:** The R07.16 flagship workflow (`turbo start` → `chat`) silently kills its own server on Windows — the platform this release specifically targeted.
---
#### ROB-35: `parse_shared_args` falsy-coalescing silently drops the documented `0` sentinel for the R07.17/R07.18 per-request params
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/shared_args.py:448-462` |
New in R07.17/R07.18. `parse_shared_args` builds `SharedConfig` with `or`-coalescing: `repeat_last_n=getattr(args, "repeat_last_n", None) or _env_int("AGENTKTHX_REPEAT_LAST_N")` (line 458; `num_ctx`/`num_predict`/`num_batch`/`repeat_penalty` share the shape at 453-457). `--repeat-last-n 0` is DOCUMENTED as valid — its own help text says "0 = full context" and `_parse_token_size` explicitly supports the `0` sentinel — but `0 or _env_int(...)` evaluates the right side: the user's explicit 0 silently becomes `None` (backend default) when no env var is set, or the env-var value when one is. Verified live: `parse_args(['--repeat-last-n', '0'])` yields `args.repeat_last_n == 0`, but the resulting `SharedConfig.repeat_last_n` is `None`; with `AGENTKTHX_REPEAT_LAST_N=4096` set it silently becomes 4096. The main chat/run/agent/test CLI path is UNAFFECTED (it passes `args` directly to `_build_agent`, whose `getattr(args, ...) is not None` wiring forwards 0 correctly) — the bug is confined to the `SharedConfig` surface: a public API (exported via `agentkthx/__init__.py` + `__all__`) consumed by example scripts and programmatic users. Zero in-tree consumers is the only reason this is Low rather than Medium.
Recommendation: replace the `or`-coalescing with explicit `is not None` checks (the pattern `_build_agent` already uses), or a small `_coalesce(val, env_name, parser)` helper applied to all six numeric fields. Add a SharedConfig-level test asserting `--repeat-last-n 0` survives into `SharedConfig.repeat_last_n == 0`.
**Impact:** Documented sentinel values silently ignored in the shared-args surface every example script and library consumer is told to use.
---
#### ROB-36: `_parse_token_size` accepts `inf`/`1e400` numeric parts — `OverflowError` escapes both argparse's clean-error path and `_env_int`'s except clause
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/shared_args.py:480-543`, `:465-478` |
New in R07.18. The suffix branch does `num = float(num_part)` under `except ValueError` only (line 533) — but `float("inf")` succeeds, so `--num-ctx infk` (or `1e400k`) computes `int(inf * 1024)` at line 539 → `OverflowError`, which is neither `ValueError` nor `TypeError`. Two verified escape paths: (1) on the main CLI, argparse converts type-function `ValueError`/`TypeError` into clean "invalid value" usage errors but lets `OverflowError` propagate — `create_parser().parse_args(['chat', '--num-ctx', 'infk'])` raises a raw `OverflowError` traceback instead of a usage message; (2) via `_env_int`, whose `except (ValueError, TypeError)` (line 475) also misses `OverflowError`, so `AGENTKTHX_NUM_CTX=infk` crashes `parse_shared_args` with a traceback.
Recommendation: guard the conversion — `if not math.isfinite(num): raise ValueError(...)` — and/or catch `OverflowError` alongside `ValueError` in both `_parse_token_size` and `_env_int`. One regression test each (`infk` via the parser object, via the env var) pins the clean-error behavior.
**Impact:** A bizarre-but-parseable token size produces a stack trace instead of a clean CLI error — cosmetic class, but on the exact human-facing surface R07.18 polished.
---
### Maintainability
#### MAINT-01: `cmd_chat` is a 1,368-line single function with 25+ nested closures and no slash-command dispatcher
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/commands/chat.py:1-1368` |
`cmd_chat` is a single function spanning 1,368 lines (grew from 1,307 at R07.16 — R07.17/R07.18 added the `/param num_batch` + `repeat_penalty`/`repeat_last_n` PARAM_MATRIX entries and pin-flag plumbing inside the REPL loop), with 25+ nested closures (`_footer_line1`, `_footer_line2`, `_footer_text`, `_setup_footer_region`, `_teardown_footer_region`, `_update_footer`, `_position_for_input`, `_spinner_thread`, `_spinner_start`, `_spinner_stop_thread`, `_init_acp` rebind, `_build_agent` rebind, etc.). The slash-command handlers (`/help`, `/security`, `/system`, `/tools`, `/tool`, `/skills`, `/skill`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`) are inline `if user_input == "/X"` blocks — there's no command dispatcher. The function is too large to test in isolation; tests for chat behavior (e.g., `test_agent_mode_*.py`) use heavy monkeypatching. This was the next biggest structural debt after the R07.00 `cli.py` and `agent.py` extractions.
Recommendation: Extract `ChatSession` class with `handle_command(text) -> bool` dispatcher. Extract `Footer` class for the scroll-region logic. Extract `Spinner` class for the thread. Each slash command becomes a method. Target: `cmd_chat` becomes ~50 lines of orchestration; tests can construct a `ChatSession` and feed it simulated input.
**Impact:** Any change to chat UX requires touching this 1,300-line function; chat slash-command behavior is impossible to unit-test without monkeypatching.
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
#### MAINT-24: `_build_tool_section` docstring still promises ReAct format instructions the body no longer includes
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/soul/loader.py:726,777` |
New in R07.16. The R07.16 prompt de-duplication removed the `Action:`/`Action Input:` format block from `_build_tool_section` (the body now contributes only the tool reference table + CRITICAL RULE) — correct for the two known instruction providers (the no-soul default prompt's ReAct branch, and souls like `kthx-helper` that ship their own block). But the docstring (lines 726-728) STILL reads "If False, include ReAct Action/Action Input format instructions" — the contract and the behavior have drifted. There is also a residual behavioral gap: a CUSTOM soul with neither its own ReAct block nor example placeholders, run with `force_react=True` (now also set implicitly by the R07.16 tool-support auto-detection), gets a tool table with zero format instructions — pre-R07.16 the tool section supplied them.
Recommendation: update the docstring to the new contract, and have the prompt assembler detect "ReAct mode active + no `Action Input`-style instructions anywhere in the assembled prompt" and append the canonical block exactly once (the same single-source discipline the default-prompt branch already follows).
**Impact:** Docstring matches behavior, and custom-soul ReAct sessions can't silently lose the format contract the parser expects.
---
#### MAINT-25: Local-backend tool-support auto-detection has no opt-out — and its debug hint suggests `--force-react=False`, which the `store_true` argparse flag rejects
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/agent_factory.py:278-344`, `agentkthx/cli/parser.py:218` |
New in R07.16. The `_build_agent` auto-detection defaults local backends to `force_react=True` when the cached `test_tool_support` verdict is REACT or UNTESTED — reasonable for small CPU models, but there is no CLI opt-out back to native tools (the in-code comment admits "currently no opt-out beyond cache clearing", i.e. hand-deleting `~/.agentkthx/tool_support.json`). The UNTESTED debug hint tells the user to run `--force-react=False` — but `--force-react` is declared `action="store_true"` (parser.py:218), so passing `=False` raises `argument --force-react: ignored explicit argument 'False'`: the suggested remedy is an argparse error. Users on local models that DO support native function calling (or who simply prefer it) are locked out of the native path by the UNTESTED default.
Recommendation: make `--force-react` tri-state (`on|off|auto`, mirroring the new `--flash-attn` pattern — `auto` preserves the auto-detection, `off` forces native) or honor `AGENTKTHX_FORCE_REACT=0`; then fix the debug hint to describe the real remedy.
**Impact:** A supported escape hatch for the new default, and a debug hint that doesn't error when followed.
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
All 984 tests are mocked unit tests — there's no integration tier. `tests/test_agent_mode_*.py` tests the `AgentMode` class, but there's no test for `cmd_chat`'s handling of `/security`, `/tool`, `/skill`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`. These are 12+ slash commands with non-trivial logic (e.g., `/param` has a per-backend `PARAM_MATRIX` dict). `test_agent.py` tests the Agent class but not the CLI layer. Coverage baseline: 42.7% line coverage (R07.01) — concentrated on the most-tested modules; the CLI commands and the chat dispatcher are well below.
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
Recommendation: a `tests/test_plugin_streaming_integration.py` that instantiates each bundled cloud backend over a mocked HTTP layer and drives one streaming turn through `Agent`-level machinery — one parameterized test, all 7 cloud backends, permanently closes the finding class.
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
## Priority Matrix
| Timeline | Findings |
|----------|----------|
| **Near term (R07.20–R07.21)** | ROB-33 (non-destructive Windows liveness check — unblocks the flagship turbo start → chat workflow), ROB-31 (entitlement-aware fallback filter), ROB-02 (join worker threads), ROB-06 (deterministic Windows conn release), ROB-15 (single-transaction add, with ROB-18's RLock first), SEC-09 (warn on non-HTTPS ACP), SEC-13 (require-plugin-pins mode), MAINT-03 (drop strategy 5 of `normalize_args`), MAINT-22 (streaming `_build_body()` virtual), MAINT-23 (lift retry-loop skeleton to CloudBackend — closes ROB-29 in the same move), MAINT-01 (extract `ChatSession`), TEST-01 (integration test tier), TEST-03 (add `FakeStreamingBackend`) |
| **Short term (R07.21–R07.24)** | ROB-09 (`realpath` for symlinks), ROB-20 (public `num_predict` accessor), ROB-25 (shared sampling/cap defaults), ROB-30 (narrow Pollinations catalog catch-all), MAINT-24 (tool-section docstring contract + single-source ReAct block), MAINT-25 (tri-state `--force-react`), FEAT-03 (tool output JSON Schema), TEST-09 (plugin streaming-path integration test), TEST-10 (live-shape free-model contract test) |
| **Medium term (R08.00+)** | ROB-17 (truncate/warn on the single over-budget message), ROB-18 (RLock — fold into the ROB-15 work if not done sooner), FEAT-05 (plugin sandbox), FEAT-06 (streaming tool-arg deltas), FEAT-07 (conversation export/import), FEAT-08 (paid_only free-TIER filter mode), TEST-04 (rollback tests), TEST-05 (bump-version test portability), TEST-07 (update_check failure paths), ROB-35 (SharedConfig sentinel coalescing), ROB-36 (`_parse_token_size` finite-guard) |
Closed/wontfix placements from earlier revisions are archived in `deltas.md`'s closure timeline (R07.00 → R07.15). The R07.16 re-audit's near-term tier (R07.17–R07.18) shipped unclosed — tiers are cumulative, not reset per release.
Guidelines for timeline assignment:
- **Near term** — High severity findings and the most impactful Medium severity findings; should be fixed in the next 1-2 releases
- **Short term** — Medium severity findings addressable within 2-4 releases
- **Medium term** — Low severity findings and larger architectural changes that can be picked up during other work
---
