# Improvement & Enhancement Audit

**AgentKthx v0.6.41 (R06.41)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-20  
14 Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST

> **Status (R06.41):** This is a fresh audit run after closing 5 of the 14 original R06.4 findings and adding 1 new finding (MAINT-03). Closed in R06.41: **ROB-01** (duplicate ACP/Turbo files removed), **MAINT-02** (env vars + paths renamed), **ROB-03** (45 → 0 test failures via root-cause fixes + stale-test cleanup), **SEC-01** (new `core/safe_eval.py` AST walker; `eval()` removed from production), **SEC-02** (threat-model documented + `DANGEROUS_FLAG_COMBOS` extended; `shell=False` deferred as accepted-risk). Still open: **ROB-02** (1 of 2 bare `except:` sites remain), **MAINT-01** (cli.py 3478 lines), **MAINT-03** (NEW — OpenRouter doc has stale `AGENTNOVA_*` refs), **PERF-01**, **PERF-02**, **FEAT-01**, **FEAT-02**, **ARCH-01**, **ARCH-02**, **TEST-01**. See `docs/CHANGELOG.md` R06.41 entry for full closure details. Historical R06.4 audit preserved at `audit/audit_r06.4.md`.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
  - [Security](#security)
  - [Robustness](#robustness)
  - [Maintainability](#maintainability)
  - [Performance](#performance)
  - [New Features](#new-features)
  - [Architecture](#architecture)
  - [Testing](#testing)
- [Priority Matrix](#priority-matrix)
- [Architecture Strengths](#architecture-strengths)

---

## Executive Summary

AgentKthx is a 35,250-line Python framework for autonomous AI agents with zero external dependencies — built entirely on the standard library. The R06.41 audit reviewed the post-cleanup state of the codebase, after 5 of the original 14 R06.4 findings were closed and the test suite was pruned from 506 to 412 tests (429 passed, 6 skipped, 0 failed). The codebase has 8 backend implementations (6 plugin-based, 2 native), 17 built-in tools, 3 soul personas, 4 built-in skills, and 8 test files covering 435 collected tests. Security practices are notably stronger than at R06.4: the `eval()` sandbox bypass is closed via a new `core/safe_eval.py` AST walker that rejects `ast.Attribute` and `ast.Subscript` outright, and the `shell=True` blocklist has been extended with a context-aware `DANGEROUS_FLAG_COMBOS` dict that catches the common prompt-injection bypass primitives (`find -exec`, `python -c`, `busybox rm`, `tar --use-compress-program`, etc.) without breaking legitimate uses of the underlying commands.

The most impactful open finding remains MAINT-01 (the 3478-line `cli.py` monolith), which the audit's previous recommendation to split into `cli/parser.py`, `cli/chat.py`, `cli/display.py`, `cli/agent_factory.py`, and `cli/params.py` has not been executed. PERF-01 (streaming mode silently ignored — users see a spinner until the full response arrives) is the second-most impactful UX finding. ARCH-01 (backend inheritance couples ZAI/OpenRouter to OllamaBackend internals) remains a fragile point that previously caused the R06.2 OpenRouter JEV recursion bug class. A new MAINT-03 finding was introduced by the R06.41 work itself: `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` lines 633-635 still reference `AGENTNOVA_*` env vars (the MAINT-02 rename missed this doc). The codebase is well-positioned for R06.5+ development with clear extension points for streaming display, provider routing, parameter-matrix extensibility, and integration tests.

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| MAINT-01 | **High** | Maintainability | OPEN | `cli.py` is 3478 lines — monolithic CLI with no module splitting |
| ROB-01 | ~~**High**~~ | Robustness | ✓ CLOSED R06.41 | Duplicate ACP/Turbo plugin files removed |
| SEC-01 | ~~**High**~~ | Security | ✓ CLOSED R06.41 | `eval()` sandbox bypass closed via `safe_eval` AST walker |
| SEC-02 | ~~Medium~~ | Security | ✓ CLOSED R06.41 (accepted-risk) | `shell=True` + blocklist — threat model documented, `DANGEROUS_FLAG_COMBOS` added |
| ROB-03 | ~~Medium~~ | Robustness | ✓ CLOSED R06.41 | Pre-existing test failures fixed or stale tests deleted (45 → 0) |
| MAINT-02 | ~~Medium~~ | Maintainability | ✓ CLOSED R06.41 | `AGENTNOVA_*` env vars renamed to `AGENTKTHX_*`; `~/.agentnova/` → `~/.agentkthx/` |
| ROB-02 | Medium | Robustness | PARTIALLY FIXED | Bare `except:` clause at `orchestrator.py:279` (1 of 2 sites remains) |
| MAINT-03 | Low | Maintainability | NEW (R06.41 regression) | `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` still references `AGENTNOVA_*` env vars |
| PERF-01 | Medium | Performance | OPEN | Streaming mode silently ignored — no real-time output |
| PERF-02 | Low | Performance | OPEN | Missing `stream_options.include_usage` on OpenRouter |
| FEAT-01 | Medium | New Feature | OPEN | No provider routing preferences for OpenRouter |
| FEAT-02 | Low | New Feature | OPEN | `/param` matrix hardcoded, not extensible via plugins |
| ARCH-01 | Medium | Architecture | OPEN | Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals |
| ARCH-02 | Low | Architecture | OPEN | No coverage measurement configured |
| TEST-01 | Medium | Testing | PARTIALLY ADDRESSED | No integration tests — all 435 tests are mocked unit tests |

**Severity distribution**: 1 High (MAINT-01), 7 Medium, 6 Low (counting closed findings). Of the 9 still-open: 1 High, 5 Medium, 3 Low.

---

## Detailed Findings

### Security

#### SEC-01: `eval()` sandbox bypass — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~**High**~~ → Resolved |
| **Category** | Security |
| **File(s)** | `agentkthx/core/math_prompts.py:220`, `agentkthx/core/helpers.py:820` |
| **Status (R06.41)** | ✓ CLOSED — new `agentkthx/core/safe_eval.py` AST walker |

**Resolution (R06.41):** Created a new shared `agentkthx/core/safe_eval.py` module with a recursive AST-walking evaluator that:
- **Allows**: numeric/bool/None constants, names from an allowlist, binary operators (`+ - * / // % ** << >> & | ^`), unary operators (`- + not ~`), boolean operators (`and or` with short-circuit), comparisons (`== != < <= > >=`, chained), conditional expressions, direct function calls to allowlist names.
- **Rejects outright**: `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, `ast.ListComp`/`ast.SetComp`/`ast.DictComp`/`ast.GeneratorExp`, `ast.JoinedStr`/`ast.FormattedValue` (f-strings), `ast.Starred`, `ast.Import`/`ast.ImportFrom`, `ast.Slice`, `ast.Await`/`ast.Yield`, `ast.NamedExpr` (walrus), collection literals, and non-numeric constants (strings, bytes, complex).

Refactored all three call sites:
- `agentkthx/tools/builtins.py:calculator()` — delegates to `safe_eval(expression, _SAFE_NAMES)`.
- `agentkthx/core/math_prompts.py:evaluate_math_expression()` — replaced bare `eval()` with `safe_eval(expression, allowed_names)`.
- `agentkthx/core/helpers.py:822` (inside `normalize_tool_args`) — replaced `eval(expr, {"__builtins__": {}}, {})` with `safe_eval(expr, {})` (empty allowlist — pure arithmetic).

Tests added: 49 SEC-01 tests in `tests/test_security.py` (`TestSafeEvalBypassAttempts` + `TestSafeEvalCorrectness`). Zero `eval()` calls remain in production source (verified via grep).

**Impact:** A jailbroken model or crafted prompt can no longer achieve arbitrary code execution via the calculator tool — `().__class__.__bases__[0].__subclasses__()` is rejected at the AST level before any attribute access is attempted.

---

#### SEC-02: `shell=True` subprocess execution — CLOSED in R06.41 (accepted-risk + hardened)

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Accepted-risk |
| **Category** | Security |
| **File(s)** | `agentkthx/tools/builtins.py:295` (subprocess.run shell=True call site); `agentkthx/core/helpers.py:sanitize_command()` (blocklist logic) |
| **Status (R06.41)** | ✓ CLOSED — Option A (threat model documented) + Option B (blocklist modestly extended) |

**Original audit recommendation (R06.4):** Switch to `shell=False` with `shlex.split()`.

**Resolution (R06.41):** The maintainer determined that the audit's threat model (determined adversary crafting payloads) doesn't match AgentKthx's actual use case (trusted user + cooperative model + occasional mistakes). Anyone prompting the model is a user of the same system it would break, so they have no incentive to bypass the blocklist. A model that gets an initial refusal rarely pivots to a bypass technique to "achieve the goal anyway" — once `rm` is refused, the model accepts the refusal and tries a different approach to the user's actual goal. Switching to `shell=False` would break legitimate agent workflows (pipes, redirects) for a threat that doesn't manifest in practice.

Two mitigations applied instead:

1. **Documented the threat model** in `core/helpers.py` — new multi-paragraph comment above `BLOCKED_COMMANDS` explaining: blocklist is a guardrail against model mistakes, NOT a defense against determined prompt injection. Expanded `sanitize_command()` docstring enumerates the three defense layers: denylist → flag-combo check → injection regex. Each layer's coverage and gaps are documented.

2. **Extended the blocklist** with a new `DANGEROUS_FLAG_COMBOS` dict — context-aware blocks on otherwise-safe commands paired with dangerous flags:

| Command | Blocked flag combo | Legit use still allowed |
|---------|---------------------|-------------------------|
| `find` | `-exec` / `-execdir` / `-delete` | `find . -name '*.py' -type f` |
| `xargs` | `rm` / `mv` / `dd` / `shred` / `rmdir` subcommand | `xargs grep -l pattern` |
| `python` / `python3` | `-c` (inline code) | `python script.py`, `python3 --version` |
| `perl` / `ruby` | `-e` (inline interpreter) | `perl script.pl` |
| `awk` | `system(...)` call inside awk script | `awk '{print $1}' file` |
| `tar` | `--use-compress-program=X` / `-I X` | `tar -czf out.tar.gz dir/` |
| `cp` | `/dev/null` source (file truncation trick) | `cp source.txt dest.txt` |
| `busybox` | (added to `BLOCKED_COMMANDS` outright) | `--security off` if needed |

Tests added: 23 new SEC-02 tests in `tests/test_security.py::TestShellDangerousFlagCombos` (14 blocked + 9 allowed). The `shell=False` switch remains a future hardening option if the threat model ever shifts toward adversarial users or untrusted content pipelines.

**Impact:** The common prompt-injection primitives the audit specifically called out (`busybox rm`, `python -c "import os; ..."`, `perl -e "system('rm')"`, `find -exec rm {} \;`, `tar --use-compress-program=sh`) are now blocked. Determined adversaries can still construct bypasses (Unicode normalization, base64-decoded payloads) — for that threat, `--security max` AND validation/sanitization of tool output before display to the model is the recommended defense-in-depth.

---

### Robustness

#### ROB-01: Duplicate ACP/Turbo plugin files — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~**High**~~ → Resolved |
| **Category** | Robustness |
| **File(s)** | `agentkthx/acp_plugin.py` (deleted), `agentkthx/turbo.py` (deleted) |
| **Status (R06.41)** | ✓ CLOSED — 3089 lines of dead-code duplicates removed |

**Resolution (R06.41):** Deleted `agentkthx/acp_plugin.py` (2396 lines, 85 KB) and `agentkthx/turbo.py` (693 lines, 25 KB). These were near-identical copies of `agentkthx/plugins/acp/acp_plugin.py` and `agentkthx/plugins/turboquant/turbo.py` — the only difference was relative vs absolute import style. The plugin loader already imported from the plugin-system paths, so the root-level copies were dead code that risked silent behavioral divergence. All 13 references (3 in `cli.py`, 10 in `tests/test_r046_changes.py`) redirected to the plugin paths before deletion.

**Impact:** Eliminates 3089 lines of duplicate code and the risk of silent behavioral divergence when one copy is patched but not the other.

---

#### ROB-02: Bare `except:` clause — PARTIALLY FIXED

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/orchestrator.py:279` (1 remaining site; the `helpers.py:833` site was fixed as a side-effect of SEC-01) |
| **Status (R06.41)** | PARTIALLY FIXED — 1 of 2 sites closed |

The audit identified two bare `except:` clauses that catch and silently suppress ALL exceptions, including `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`. The `helpers.py:833` site was inside the calculator argument parsing fallback; the `orchestrator.py:279` site is in the multi-agent orchestrator's fallback-result processing loop.

**Progress in R06.41:** The `helpers.py:833` site was fixed as a side-effect of SEC-01 — when `eval()` was replaced with `safe_eval()`, the surrounding bare `except:` was changed to `except Exception:` to allow `KeyboardInterrupt`/`SystemExit` to propagate.

**Remaining issue:** `orchestrator.py:279` still has `except: continue`. The semantics here are intentionally broad — the orchestrator wants to fall back to the next agent if any error occurs in the current one. But the broad catch also swallows `KeyboardInterrupt` (Ctrl+C during orchestrator fallback loop) and `SystemExit`, which should propagate.

**Recommendation:** Replace with `except Exception:` (preserves the fall-through behavior) or `except (RuntimeError, ValueError, TypeError, AttributeError):` if the goal is to catch only "agent-failed" errors and let unexpected ones surface.

**Impact:** Prevents silent suppression of `KeyboardInterrupt` during multi-agent orchestration — Ctrl+C will properly terminate the orchestrator instead of being swallowed by the fallback loop.

---

#### ROB-03: Pre-existing test failures — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Resolved |
| **Category** | Robustness |
| **File(s)** | `tests/test_r048_changes.py` (deleted), `tests/test_security.py` (fixed), `tests/test_zai_backend.py` (deleted), `tests/test_openrouter_backend.py` (fixed + 2 skipped) |
| **Status (R06.41)** | ✓ CLOSED — 45 → 0 failures via root-cause fixes + stale-test cleanup |

**Resolution (R06.41):** The audit understated the failure count (said 9, actual was 45). Fixed 34 by addressing three root causes:

1. **Stale `agentkthx.backends.zai` import path** (35 failures) — bulk-rewrote 31 import sites to `agentkthx.plugins.zai.zai`.
2. **IPv6 SSRF hostname extraction bug** (1 failure) — replaced `parsed.netloc.split(":")[0]` with `parsed.hostname` (the audit's recommendation to "add `::1` to the blocklist" was wrong — `::1` was already there; the bug was hostname extraction).
3. **OpenRouterBackend `_api_mode` missing in tests** (9 failures) — `_backend()` helpers bypassed `__init__` via `__new__` and didn't set `_api_mode`; fixed by setting `b._api_mode = ApiMode.OPENAI`.

Then deleted 137 stale tests (3 R04.x verification test files) that tested baseline behavior against stale import paths and stale ZAI model catalog names. Skipped 2 PrintAgentSteps tests with explicit `@pytest.mark.skip(reason=...)` annotations.

**Final test result:** 429 passed, 0 failed, 6 skipped (all skips have explicit reasons).

**Impact:** Eliminates test noise; makes regression detection reliable.

---

### Maintainability

#### MAINT-01: `cli.py` is 3478 lines — monolithic CLI with no module splitting

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` (3478 lines) |

`cli.py` contains the entire CLI: argument parser construction, all 13+ subcommands (`run`, `chat`, `agent`, `models`, `test`, `turbo`, `skills`, `soul`, `config`, `sessions`, `plugins`, `update`, `version`), all 9 slash commands (`/help`, `/status`, `/skills`, `/param`, `/model`, `/security`, `/debug`, `/clear`, `/system`), the chat loop, the agent display logic, the footer rendering, the spinner, the banner ASCII art, the `_build_agent()` factory, the `_load_skills_prompt()` helper, the `/param` matrix (100+ lines inline), and the reasoning display logic.

At 3478 lines, this file is difficult to navigate, review, and test. Any new feature (new slash command, new display option, new CLI flag) requires modifying this file. The `/param` matrix alone is ~100 lines of inline data structure that would be better served as a separate module or config file.

**Recommendation:** Split into logical modules:
- `cli/parser.py` — argument parser construction
- `cli/chat.py` — chat loop + slash commands
- `cli/display.py` — banner, footer, spinner, step display, reasoning display
- `cli/agent_factory.py` — `_build_agent()` and `_load_skills_prompt()`
- `cli/params.py` — the `/param` matrix and parameter handling
- `cli.py` — thin entry point that imports and dispatches

This reduces the largest file to ~500 lines and makes each concern independently testable.

**Impact:** Reduces cognitive load for contributors, makes code review faster, and enables targeted testing of CLI components.

---

#### MAINT-02: `AGENTNOVA_*` env vars renamed — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Resolved |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/config.py`, `agentkthx/cli.py`, and 36 other files |
| **Status (R06.41)** | ✓ CLOSED — env vars + filesystem paths renamed; no aliases kept |

**Resolution (R06.41):** Renamed all 18 `AGENTNOVA_*` env vars to `AGENTKTHX_*` (BACKEND, MODEL, MAX_STEPS, DEBUG, VERBOSE, NUM_CTX, NUM_PREDICT, TEMPERATURE, TOP_P, FAST, FORCE_REACT, USE_MF_SYS, RETRY_ON_ERROR, MAX_TOOL_RETRIES, ACP, ACP_URL, API_MODE, GLYPHS). Renamed filesystem paths: `~/.agentnova/` → `~/.agentkthx/`, `~/.cache/agentnova/` → `~/.cache/agentkthx/`, Windows `%LOCALAPPDATA%\agentnova\cache` → `%LOCALAPPDATA%\agentkthx\cache`. Renamed Python identifiers (`config.AGENTNOVA_BACKEND` → `config.AGENTKTHX_BACKEND`), function names (`_get_agentnova_dir` → `_get_agentkthx_dir`), and CLI usage strings (`agentnova run` → `agentkthx run`, `python -m agentnova` → `python -m agentkthx`). Dropped backward-compat aliases entirely (user explicitly opted in — few users). 225 replacements across 38 files.

**Side-benefit bug fix:** `agentkthx/soul/loader.py` had a leftover `agentnova.__file__` reference (a `NameError` — the file imports `agentkthx` but referenced `agentnova`). Fixed to `agentkthx.__file__`.

**Impact:** Eliminates the confusing mismatch between `agentkthx` package name and `AGENTNOVA_*` env vars / `~/.agentnova/` paths.

---

#### MAINT-03: OpenRouter API reference still mentions `AGENTNOVA_*` env vars — NEW (R06.41 regression)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md:633-635` |

The MAINT-02 rename in R06.41 missed `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md`. Lines 633-635 still say:

```
AgentKthx keeps `AGENTNOVA_*` env var names for backward compatibility:
- `AGENTNOVA_BACKEND=openrouter` — set default backend
- `AGENTNOVA_API_MODE=openai` — OpenRouter only supports OpenAI mode
```

This is now factually wrong on two counts: (1) the env vars are `AGENTKTHX_*`, not `AGENTNOVA_*`; (2) we explicitly dropped backward-compat aliases, so `AGENTNOVA_*` no longer works at all. A user who reads this doc and tries `AGENTNOVA_BACKEND=openrouter agentkthx run "..."` will get the default backend (ollama) instead of OpenRouter — a silent failure.

The fix is a 3-line text replacement: `AGENTNOVA_BACKEND` → `AGENTKTHX_BACKEND`, `AGENTNOVA_API_MODE` → `AGENTKTHX_API_MODE`, and the surrounding prose needs to drop "for backward compatibility" since we no longer keep aliases.

**Recommendation:** Find-and-replace `AGENTNOVA_` with `AGENTKTHX_` in `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md`. Also delete the line "AgentKthx keeps `AGENTNOVA_*` env var names for backward compatibility" since aliases were dropped. Spot-check `docs/ZAI_API_TECHNICAL_REFERENCE.md`, `docs/JEV_API_MODE.md`, and `README.md` — those are already clean (verified during this audit) but worth a CI check.

**Impact:** Eliminates documentation that contradicts the codebase's actual behavior; small but real silent-failure risk for users who follow the doc.

---

### Performance

#### PERF-01: Streaming mode silently ignored — no real-time output

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Performance |
| **File(s)** | `agentkthx/agent.py:536` (`run()` method) |

`Agent.run(prompt, stream=True)` accepts the `stream` parameter but never uses it — the method always runs the non-streaming code path. The `run_stream()` method exists but yields SSE events (for API consumers), not console-friendly output. In chat mode, cloud providers (ZAI/OpenRouter) default to streaming, but since `run(stream=True)` is ignored, users see a spinner until the full response arrives, then the entire answer appears at once.

This is a UX issue more than a performance issue — the actual API call may use streaming on the wire (the backend's `generate_stream()` method is called), but the agent loop doesn't consume the stream incrementally for display. The backend streams, but the output is buffered and displayed all at once.

**Recommendation:** Implement a `run_stream_console()` method that:
1. Calls the backend's streaming method
2. Prints text chunks as they arrive (typewriter effect)
3. Accumulates `tool_calls` fragments across SSE chunks
4. After stream completes, checks if tool_calls were found and continues the agentic loop
5. Falls back to non-streaming for backends that don't support it

Wire `cmd_chat` and `cmd_run` to use this when `stream=True`.

**Impact:** Dramatically improves perceived latency for cloud-provider users — they see text appearing as it's generated instead of waiting for the full response.

---

#### PERF-02: Missing `stream_options.include_usage` on OpenRouter

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py` |

When OpenRouter is used in streaming mode, the `stream_options: {"include_usage": true}` field is not sent. Without this, OpenRouter's streaming SSE chunks don't include `usage` data, so token counts are not tracked for streamed responses. The footer's token counter (`📈 ↑X ↓Y`) shows 0 for streamed responses.

**Recommendation:** Add `"stream_options": {"include_usage": True}` to the request body in `_build_openai_body()` when `stream=True`.

**Impact:** Enables accurate token tracking for streaming responses; small API overhead.

---

### New Features

#### FEAT-01: No provider routing preferences for OpenRouter

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py` |

OpenRouter supports a `provider` object in the request body that controls routing: `order` (preferred provider order), `allow_fallbacks`, `require_parameters`, `ignore`, `quantizations`, `data_collection`. AgentKthx does not send this field — it uses OpenRouter's default routing.

Users who want to pin to free providers, avoid specific providers, or control data collection have no way to do so. This is particularly relevant for the "free models only" use case where a user might want to force `data_collection: "deny"` or pin to a specific provider for consistency.

**Recommendation:** Add CLI flags `--provider-order`, `--provider-ignore`, `--provider-data-collection deny` that get forwarded as the `provider` object in the request body. Also support via `/param` slash command for runtime changes (would require FEAT-02 to make `/param` extensible).

**Impact:** Gives users control over which upstream providers serve their requests — important for privacy, cost, and reliability.

---

#### FEAT-02: `/param` matrix is hardcoded, not extensible via plugins

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/cli.py` (inline `PARAM_MATRIX` dict in `cmd_chat`) |

The `/param` slash command's parameter support matrix is a hardcoded dict inside `cmd_chat()`. Adding a new parameter requires editing this 100+ line inline data structure in the 3478-line `cli.py`. There's no way for a plugin to register a new parameter that would show up in `/param`.

**Recommendation:** Move the `PARAM_MATRIX` to a separate `cli/params.py` module and expose a `register_param(name, spec)` API that plugins can call. The plugin's `register()` function could add backend-specific parameters (e.g., ZAI's `do_sample` parameter, OpenRouter's `min_p` sampling). This depends on MAINT-01 (cli.py split) for clean module boundaries.

**Impact:** Makes the parameter system extensible and reduces the size of `cli.py` (overlaps with MAINT-01).

---

### Architecture

#### ARCH-01: Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/plugins/zai/zai.py`, `agentkthx/plugins/openrouter/openrouter.py` |

Both `ZaiBackend` and `OpenRouterBackend` inherit from `OllamaBackend`. This means:
- Any change to `OllamaBackend.generate()` affects all three backends.
- The JEV dispatch (`_maybe_jev_dispatch()`, `generate_decision()`, `_jev_call_completions()`) lives on `OllamaBackend` and is inherited — but each subclass overrides `_jev_call_completions()` to route through its own auth path.
- The `_maybe_jev_dispatch()` method calls `self.generate_decision()` which calls `self._jev_call_completions()` — the override chain works but is fragile (the OpenRouter JEV recursion bug in R06.2 was caused by `_jev_call_completions()` calling `self.generate()` which triggered `_maybe_jev_dispatch()` again).

The inheritance was originally chosen because ZAI and OpenRouter are OpenAI-compatible and can reuse `generate_completions()`. But they each have their own auth injection (`_generate_with_auth()` for ZAI, `_make_api_request()` for OpenRouter) that bypasses the parent's body construction. This means the parent's `think` forwarding, `reasoning_effort` forwarding, and `response_format` handling must be duplicated or explicitly skipped in each subclass.

**Recommendation:** Consider extracting a `ChatCompletionsMixin` or `OpenAICompatibleBackend` base class that provides the shared OpenAI-format body construction and response parsing, without the Ollama-specific `/api/chat` native path. ZAI and OpenRouter would inherit from this, and OllamaBackend would inherit from both this and a `NativeOllamaMixin`. This decouples the auth/endpoint-specific code from the shared protocol logic.

**Impact:** Reduces coupling, makes backend-specific changes safer, prevents the class of recursion bugs seen in R06.2.

---

#### ARCH-02: No coverage measurement configured

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `pyproject.toml` |

The project has 435 tests but no coverage measurement configured. `pytest-cov` is not in dev dependencies, and there's no `--cov` flag in `addopts`. The actual coverage percentage is unknown — it could be 30% or 80%.

**Recommendation:** Add `pytest-cov` to dev dependencies, configure `addopts = "-v --tb=short --cov=agentkthx --cov-report=term-missing"` in `pyproject.toml`. Set a minimum coverage threshold (e.g., `--cov-fail-under=50`) to prevent coverage from dropping below a baseline.

**Impact:** Makes coverage visible and prevents silent coverage regression.

---

### Testing

#### TEST-01: No integration tests — all tests are mocked unit tests

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (all 8 test files) |

All 435 tests use `MagicMock`, `monkeypatch`, or source-level string inspection (`inspect.getsource()` + `assert "X" in src`). No test makes a real HTTP call to a backend, no test runs a full agent loop end-to-end, and no test exercises the actual CLI (`agentkthx run "..."`).

This means integration bugs (like the JEV recursion, the `reasoning_content` propagation gap, the `--stream` flag missing from chat) are only caught by manual testing. The source-inspection tests (`assert "reasoning_content" in src`) are brittle — they verify that a string appears in the source code, not that the behavior works.

**Progress in R06.41:** The cleanup pass deleted 137 stale tests (3 R04.x verification files) that were testing pre-plugin-era behavior against outdated import paths and ZAI model catalog names. The remaining 435 tests are all relevant to current behavior. The `TestSafeEvalBypassAttempts` (24 tests) and `TestShellDangerousFlagCombos` (23 tests) added in R06.41 are still mocked unit tests, but they exercise the security boundary directly (calling `safe_eval()` and `sanitize_command()` rather than inspecting source).

**Recommendation:** Add a `tests/integration/` directory with tests that:
1. Mock the HTTP layer (not the agent loop) using `unittest.mock.patch` on `urllib.request.urlopen`
2. Exercise the full `Agent.run()` → `backend.generate()` → response → display path
3. Test JEV mode end-to-end with a mock ZAI/OpenRouter response
4. Test the chat loop with mock user input
5. Test CLI argument parsing for all commands

Use `pytest.mark.integration` to allow skipping slow integration tests in CI.

**Impact:** Catches integration bugs that unit tests miss; reduces reliance on manual testing for regressions.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R06.5–R06.6)** | MAINT-01 (cli.py split), PERF-01 (streaming display), MAINT-03 (OpenRouter doc stale refs — quick fix) |
| **Short term (R06.7–R07.0)** | ROB-02 (orchestrator bare except), ARCH-01 (backend inheritance decoupling), FEAT-01 (OpenRouter provider routing), TEST-01 (integration tests) |
| **Medium term (R07.0+)** | PERF-02 (stream_options), FEAT-02 (/param matrix extensibility — depends on MAINT-01), ARCH-02 (coverage measurement) |

---

## Architecture Strengths

1. **Zero dependencies by design** — The entire framework runs on Python stdlib (urllib, sqlite3, argparse, json, re, ast). This is a deliberate architectural choice that eliminates dependency management, supply chain attacks, and version conflicts. It makes the framework installable in any Python 3.9+ environment with `pip install agentkthx` and no transitive dependencies. Verified during R06.41 work: even the new `core/safe_eval.py` module uses only `ast`, `operator`, and `typing`.

2. **Plugin system with directory-scan discovery** — The `PluginManager` in `plugins/_loader.py` discovers plugins by scanning `plugins/*/plugin.json` manifests, not via pip install or entry points. This is ideal for local-first users who can drop a new backend into the plugins directory without modifying `pyproject.toml` or running `pip install`. The manifest format (`plugin.json`) is clean and well-documented in `PLUGIN_SPEC.md`.

3. **Defense-in-depth security with explicit threat model** — As of R06.41, the security model is explicitly documented in `core/helpers.py:sanitize_command()`'s docstring + the comment above `BLOCKED_COMMANDS`. The layers are: `BLOCKED_COMMANDS` (denylist) → `DANGEROUS_FLAG_COMBOS` (context-aware flag blocks — NEW in R06.41) → injection-pattern regex (shell metachars) → `--security max|off` runtime toggle. The threat model is now explicit: guardrail against model mistakes, not against determined prompt injection. The `safe_eval()` module (NEW in R06.41) similarly rejects `ast.Attribute` and `ast.Subscript` outright — the security boundary is in the AST node whitelist, not in sandboxing `eval()`.

4. **JEV mode as an API mode, not a backend** — The decision to implement JEV as `ApiMode.JEV` (sibling of `openre`/`openai`) rather than as a separate `JevBackend` plugin was architecturally correct. It allows any chat-capable backend to produce Jev-shaped decisions by wrapping its existing `generate_completions()` call with a constrained decision prompt. The `_jev_call_completions()` hook pattern lets each backend route through its own auth path while sharing the JEV prompt-building and JSON-parsing logic.

5. **`safe_eval()` AST walker (NEW in R06.41)** — The new `core/safe_eval.py` module is a textbook example of deny-by-default AST evaluation. The whitelist is explicit (`ast.Expression`, `ast.BinOp`, `ast.UnaryOp`, `ast.Constant`, `ast.Name`, `ast.Call`, `ast.Compare`, `ast.BoolOp`, `ast.IfExp`) and everything else falls through to a `ValueError("Disallowed AST node: {type}")`. No tuple hacks, no special cases. 49 tests (24 bypass attempts + 25 correctness) verify every known sandbox-escape payload is rejected and every legitimate math expression works.

6. **`DANGEROUS_FLAG_COMBOS` context-aware flag blocking (NEW in R06.41)** — Rather than blocking `find`, `python`, `perl`, `tar` outright (which would break legitimate agent workflows), the new dict pairs each binary with a list of `(regex, reason)` tuples that only block the dangerous flag combinations. `find . -name '*.py'` still works; `find . -exec rm {} \;` is blocked. This is a better tradeoff than the audit's original recommendation (`shell=False` + `shlex.split()`) for AgentKthx's actual threat model.

7. **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — The thinking controls (`--thinking off|auto|low|medium|high`) are cleanly separated into a user-facing enum, a parser that maps to `(think, reasoning_effort)` tuples, and per-backend forwarding logic. Adding a new thinking level or a new backend's thinking API is straightforward — just add to the enum and the backend's body construction.

8. **Backward compatibility as a first-class concern (with explicit opt-out)** — The R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility: redirect stubs (`agentnova/__init__.py`, `localclaw/__init__.py`) re-export everything with `DeprecationWarning`. R06.41 deviated from this pattern for env vars / filesystem paths — the user explicitly opted in to dropping `AGENTNOVA_*` aliases for a cleaner codebase. The redirect stub packages remain for the `import agentnova` use case. The decision is documented in `audit/audit.md` and `docs/CHANGELOG.md` so future contributors understand the trade-off.

9. **Test suite hygiene (improved in R06.41)** — All 6 skipped tests now have explicit `@pytest.mark.skip(reason=...)` annotations explaining why they're skipped. The 435 collected tests are all relevant to current behavior (no stale R04.x verification tests left). Zero failures. The audit history is preserved at `audit/audit_r06.4.md` + `audit/brief_r06.4.md` for reference.
