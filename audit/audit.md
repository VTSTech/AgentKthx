# Improvement & Enhancement Audit

**AgentKthx v0.6.53 (R06.53)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-22  
**Status:** 11 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST

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

AgentKthx is a 35,250+ line Python framework for autonomous AI agents with zero external dependencies — built entirely on the standard library. Since R06.41, the codebase has evolved to version R06.53 with significant improvements:

- **R06.53 (Current)**: Cleanup pass — closed MAINT-03 (stale `AGENTNOVA_*` refs in OpenRouter doc), ROB-02 (last bare `except:` in `orchestrator.py`), PERF-02 (`stream_options.include_usage` on OpenRouter streaming). 3 new tests added.

- **R06.52**: Loop resilience fixes — closed the "codebase-audit death-spiral" with improved error detection, consecutive termination semantics, duplicate call blocking, pairing-safe memory pruning, and hallucinated-parameter stripping. 48 new tests added.

- **R06.51**: Dual-source update check system — checks PyPI for stable releases and GitHub for development commits with smart caching and notification placement. 54 new tests added.

- **R06.50**: OpenRouter 429 retry enhancements — retry budget raised, exponential back-off with jitter, transient server errors (502/503/504) now retried, retry notices always printed.

- **R06.41**: Major security and cleanup — removed duplicate ACP/Turbo files, renamed env vars, introduced `safe_eval()` AST walker, extended `DANGEROUS_FLAG_COMBOS`.

The test suite now has **479 passed, 6 skipped, 0 failed** tests. Security practices are significantly stronger with the `eval()` sandbox bypass closed and shell command hardening. The most impactful open finding remains MAINT-01 (the 3667-line `cli.py` monolith).

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| MAINT-01 | **High** | Maintainability | OPEN | `cli.py` is 3667 lines — monolithic CLI with no module splitting |
| ROB-01 | ~~**High**~~ | Robustness | ✓ CLOSED R06.41 | Duplicate ACP/Turbo plugin files removed |
| SEC-01 | ~~**High**~~ | Security | ✓ CLOSED R06.41 | `eval()` sandbox bypass closed via `safe_eval` AST walker |
| SEC-02 | ~~Medium~~ | Security | ✓ CLOSED R06.41 (accepted-risk) | `shell=True` + blocklist — threat model documented, `DANGEROUS_FLAG_COMBOS` added |
| ROB-03 | ~~Medium~~ | Robustness | ✓ CLOSED R06.41 | Pre-existing test failures fixed (45 → 0) |
| MAINT-02 | ~~Medium~~ | Maintainability | ✓ CLOSED R06.41 | `AGENTNOVA_*` env vars renamed to `AGENTKTHX_*`; `~/.agentnova/` → `~/.agentkthx/` |
| ROB-02 | ~~Medium~~ | Robustness | ✓ CLOSED R06.53 | Bare `except:` clause at `orchestrator.py:279` replaced with `except Exception:` |
| MAINT-03 | ~~Low~~ | Maintainability | ✓ CLOSED R06.53 | `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` no longer references `AGENTNOVA_*` env vars |
| PERF-01 | Medium | Performance | OPEN | Streaming mode silently ignored — no real-time output |
| PERF-02 | ~~Low~~ | Performance | ✓ CLOSED R06.53 | `stream_options.include_usage` now sent on OpenRouter streaming requests |
| FEAT-01 | Medium | New Feature | OPEN | No provider routing preferences for OpenRouter |
| FEAT-02 | Low | New Feature | OPEN | `/param` matrix hardcoded, not extensible via plugins |
| ARCH-01 | Medium | Architecture | OPEN | Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals |
| ARCH-02 | Low | Architecture | OPEN | No coverage measurement configured |
| TEST-01 | Medium | Testing | PARTIALLY ADDRESSED | No integration tests — all tests are mocked unit tests (improved) |

**Severity distribution**: 1 High (MAINT-01), 5 Medium, 5 Low (excluding closed findings). Of the 11 still-open: 1 High, 5 Medium, 5 Low.

---

## Detailed Findings

### Security

#### SEC-01: `eval()` sandbox bypass — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~**High**~~ → Resolved |
| **Category** | Security |
| **File(s)** | `agentkthx/core/math_prompts.py:220`, `agentkthx/core/helpers.py:820`, `agentkthx/tools/builtins.py:calculator()` |
| **Status (R06.52)** | ✓ CLOSED — new `agentkthx/core/safe_eval.py` AST walker |

**Status:** CLOSED in R06.41. The new `agentkthx/core/safe_eval.py` module (280 lines) provides an AST-walking evaluator that:
- **Allows**: numeric/bool/None constants, names from allowlist, binary operators, unary operators, boolean operators, comparisons, conditional expressions, direct function calls to allowlist
- **Rejects outright**: `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus operators, collection literals, non-numeric constants

**Impact:** A jailbroken model can no longer achieve arbitrary code execution via the calculator tool.

---

#### SEC-02: `shell=True` subprocess execution — CLOSED in R06.41 (accepted-risk + hardened)

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Accepted-risk |
| **Category** | Security |
| **File(s)** | `agentkthx/tools/builtins.py:295`, `agentkthx/core/helpers.py:sanitize_command()` |
| **Status (R06.52)** | ✓ CLOSED — Option A (threat model documented) + Option B (blocklist modestly extended) |

**Status:** CLOSED in R06.41. The blocklist was extended with `DANGEROUS_FLAG_COMBOS` dict that blocks dangerous flag combinations:

| Command | Blocked flag combo | Legit use still allowed |
|---------|---------------------|-------------------------|
| `find` | `-exec` / `-execdir` / `-delete` | `find . -name '*.py' -type f` |
| `xargs` | `rm` / `mv` / `dd` / `shred` / `rmdir` | `xargs grep -l pattern` |
| `python` / `python3` | `-c` (inline code) | `python script.py`, `python3 --version` |
| `perl` / `ruby` | `-e` (inline interpreter) | `perl script.pl` |
| `awk` | `system(...)` call inside awk script | `awk '{print $1}' file` |
| `tar` | `--use-compress-program=X` / `-I X` | `tar -czf out.tar.gz dir/` |
| `cp` | `/dev/null` source | `cp source.txt dest.txt` |
| `busybox` | (added to `BLOCKED_COMMANDS` outright) | `--security off` if needed |

**Impact:** Common prompt-injection primitives are now blocked.

---

### Robustness

#### ROB-01: Duplicate ACP/Turbo plugin files — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~**High**~~ → Resolved |
| **Category** | Robustness |
| **File(s)** | `agentkthx/acp_plugin.py` (deleted), `agentkthx/turbo.py` (deleted) |
| **Status (R06.52)** | ✓ CLOSED — 3089 lines of dead-code duplicates removed |

**Status:** CLOSED in R06.41. Deleted duplicate files that were near-identical to plugin versions.

---

#### ROB-02: Bare `except:` clause — CLOSED in R06.53

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Resolved |
| **Category** | Robustness |
| **File(s)** | `agentkthx/orchestrator.py:279` |
| **Status (R06.53)** | ✓ CLOSED — replaced with `except Exception:`

**Status:** CLOSED in R06.53. The last remaining bare `except:` clause (in the multi-agent orchestrator's fallback-result processing loop) was replaced with `except Exception:`, allowing `KeyboardInterrupt` / `SystemExit` to propagate correctly.

---

#### ROB-03: Pre-existing test failures — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Resolved |
| **Category** | Robustness |
| **File(s)** | `tests/test_r048_changes.py` (deleted), `tests/test_security.py` (fixed), `tests/test_zai_backend.py` (deleted), `tests/test_openrouter_backend.py` (fixed) |
| **Status (R06.52)** | ✓ CLOSED — 45 → 0 failures via root-cause fixes + stale-test cleanup |

**Status:** CLOSED in R06.41. The test suite now has **479 passed, 6 skipped, 0 failed** tests.

**Progress in R06.52:** Added 48 new tests in `tests/test_loop_resilience.py` covering:
- Error classification
- Consecutive termination
- Duplicate blocking
- Pruning
- Sanitization
- Argument handling
- Shell format
- End-to-end scenarios

---

### Maintainability

#### MAINT-01: `cli.py` is 3667 lines — monolithic CLI with no module splitting

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` (3667 lines) |

`cli.py` contains the entire CLI: argument parser construction, all 13+ subcommands, all 9 slash commands, the chat loop, the agent display logic, the banner ASCII art, the `_build_agent()` factory, the `/param` matrix (100+ lines inline), and the reasoning display logic.

**Recommendation:** Split into logical modules:
- `cli/parser.py` — argument parser construction
- `cli/chat.py` — chat loop + slash commands
- `cli/display.py` — banner, footer, spinner, step display, reasoning display
- `cli/agent_factory.py` — `_build_agent()` and `_load_skills_prompt()`
- `cli/params.py` — the `/param` matrix and parameter handling
- `cli.py` — thin entry point that imports and dispatches

**Impact:** Reduces cognitive load for contributors, makes code review faster, and enables targeted testing.

---

#### MAINT-02: `AGENTNOVA_*` env vars renamed — CLOSED in R06.41

| Property | Value |
|----------|-------|
| **Severity** | ~~Medium~~ → Resolved |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/config.py`, `agentkthx/cli.py`, and 36 other files |
| **Status (R06.52)** | ✓ CLOSED — env vars + filesystem paths renamed; no aliases kept |

**Status:** CLOSED in R06.41. 225 replacements across 38 files.

---

#### MAINT-03: OpenRouter API reference still mentions `AGENTNOVA_*` env vars — CLOSED in R06.53

| Property | Value |
|----------|-------|
| **Severity** | ~~Low~~ → Resolved |
| **Category** | Maintainability |
| **File(s)** | `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md:631-636` |
| **Status (R06.53)** | ✓ CLOSED — section rewritten

**Status:** CLOSED in R06.53. The "Backward-compatibility env vars" section was doubly wrong: (a) it used `AGENTNOVA_*` names, (b) it claimed backward-compat aliases were retained — MAINT-02 explicitly removed all aliases. Section renamed to "Configuration env vars" and rewritten to reflect the `AGENTKTHX_*` prefix with a note about the R06.41 rename.

---

### Performance

#### PERF-01: Streaming mode silently ignored — no real-time output

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Performance |
| **File(s)** | `agentkthx/agent.py:536` (`run()` method) |

`Agent.run(prompt, stream=True)` accepts the `stream` parameter but never uses it — the method always runs the non-streaming code path.

**Recommendation:** Implement a `run_stream_console()` method that:
1. Calls the backend's streaming method
2. Prints text chunks as they arrive (typewriter effect)
3. Accumulates `tool_calls` fragments across SSE chunks
4. After stream completes, checks if tool_calls were found and continues the agentic loop

**Impact:** Dramatically improves perceived latency for cloud-provider users.

---

#### PERF-02: Missing `stream_options.include_usage` on OpenRouter — CLOSED in R06.53

| Property | Value |
|----------|-------|
| **Severity** | ~~Low~~ → Resolved |
| **Category** | Performance |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py` |
| **Status (R06.53)** | ✓ CLOSED — `stream` parameter added to `_build_openai_body()`

**Status:** CLOSED in R06.53. `_build_openai_body()` now accepts a `stream: bool = False` parameter. When `stream=True` is passed, the body includes `"stream_options": {"include_usage": True}` so OpenRouter emits a final SSE chunk carrying token-usage stats. Non-streaming requests are unaffected — `stream_options` is omitted entirely. Three new tests in `tests/test_openrouter_backend.py` cover both branches and the default.

---

### New Features

#### FEAT-01: No provider routing preferences for OpenRouter

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py` |

OpenRouter supports a `provider` object in the request body that controls routing. AgentKthx does not send this field.

**Recommendation:** Add CLI flags `--provider-order`, `--provider-ignore`, `--provider-data-collection deny` that get forwarded as the `provider` object in the request body.

**Impact:** Gives users control over which upstream providers serve their requests.

---

#### FEAT-02: `/param` matrix is hardcoded, not extensible via plugins

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/cli.py` (inline `PARAM_MATRIX` dict in `cmd_chat`) |

The `/param` slash command's parameter support matrix is a hardcoded dict inside `cmd_chat()`. Adding a new parameter requires editing this inline data structure.

**Recommendation:** Move the `PARAM_MATRIX` to a separate `cli/params.py` module and expose a `register_param(name, spec)` API that plugins can call.

**Impact:** Makes the parameter system extensible and reduces the size of `cli.py` (overlaps with MAINT-01).

---

### Architecture

#### ARCH-01: Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/plugins/zai/zai.py`, `agentkthx/plugins/openrouter/openrouter.py` |

Both `ZaiBackend` and `OpenRouterBackend` inherit from `OllamaBackend`. This means any change to `OllamaBackend.generate()` affects all three backends, and the JEV dispatch is inherited.

**Recommendation:** Consider extracting a `ChatCompletionsMixin` or `OpenAICompatibleBackend` base class that provides the shared OpenAI-format body construction and response parsing, without the Ollama-specific `/api/chat` native path.

**Impact:** Reduces coupling, makes backend-specific changes safer, prevents the class of recursion bugs seen in R06.2.

---

#### ARCH-02: No coverage measurement configured

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `pyproject.toml` |

The project has 479 tests but no coverage measurement configured.

**Recommendation:** Add `pytest-cov` to dev dependencies, configure `addopts` in `pyproject.toml`.

**Impact:** Makes coverage visible and prevents silent coverage regression.

---

### Testing

#### TEST-01: No integration tests — most tests are mocked unit tests

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (all 8 test files) |

All 479 tests use `MagicMock`, `monkeypatch`, or source-level string inspection. No test makes a real HTTP call to a backend, no test runs a full agent loop end-to-end, and no test exercises the actual CLI.

**Progress in R06.52:** Added 48 new tests in `tests/test_loop_resilience.py` covering error classification, consecutive termination, duplicate blocking, pruning, sanitization, argument handling, shell format, and two end-to-end scenarios.

**Recommendation:** Add a `tests/integration/` directory with tests that mock the HTTP layer and exercise the full `Agent.run()` → `backend.generate()` → response → display path.

**Impact:** Catches integration bugs that unit tests miss; reduces reliance on manual testing for regressions.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R06.6–R06.7)** | MAINT-01 (cli.py split), PERF-01 (streaming display) |
| **Short term (R06.7–R07.0)** | ARCH-01 (backend inheritance decoupling), FEAT-01 (OpenRouter provider routing), TEST-01 (integration tests) |
| **Medium term (R07.0+)** | FEAT-02 (/param matrix extensibility), ARCH-02 (coverage measurement) |

---

## Architecture Strengths

1. **Zero dependencies by design** — The entire framework runs on Python stdlib (urllib, sqlite3, argparse, json, re, ast). This eliminates dependency management, supply chain attacks, and version conflicts.

2. **Plugin system with directory-scan discovery** — The `PluginManager` in `plugins/_loader.py` discovers plugins by scanning `plugins/*/plugin.json` manifests. This is ideal for local-first users who can drop a new backend into the plugins directory.

3. **Defense-in-depth security with explicit threat model** — As of R06.41, the security model is explicitly documented. The layers are: `BLOCKED_COMMANDS` (denylist) → `DANGEROUS_FLAG_COMBOS` (context-aware flag blocks) → injection-pattern regex → `--security max|off` runtime toggle.

4. **JEV mode as an API mode, not a backend** — The decision to implement JEV as `ApiMode.JEV` allows any chat-capable backend to produce Jev-shaped decisions.

5. **`safe_eval()` AST walker (R06.41)** — The new `core/safe_eval.py` module provides deny-by-default AST evaluation that rejects `ast.Attribute` and `ast.Subscript` outright.

6. **`DANGEROUS_FLAG_COMBOS` context-aware flag blocking (R06.41)** — Rather than blocking entire commands, the dict pairs each binary with specific dangerous flag combinations.

7. **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — Clean separation between user-facing enum, parser, and per-backend forwarding.

8. **Backward compatibility as a first-class concern** — The R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility via redirect stubs.

9. **Update check system (R06.51)** — Dual-source checking with smart caching and notification placement.

10. **Loop resilience (R06.52)** — Improved error detection, consecutive termination semantics, duplicate call blocking, pairing-safe memory pruning, and hallucinated-parameter stripping prevent the "death-spiral" scenario.

---

## Recent Improvements Since R06.41

### R06.53 - Cleanup Pass (2026-09-22)
- **MAINT-03 closed** — `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` "Backward-compatibility env vars" section rewritten to "Configuration env vars" with `AGENTKTHX_*` prefix
- **ROB-02 closed** — last bare `except:` at `orchestrator.py:279` replaced with `except Exception:`
- **PERF-02 closed** — `_build_openai_body(stream=True)` now emits `stream_options.include_usage` so streaming responses carry token counts
- 3 new tests added in `tests/test_openrouter_backend.py`

### R06.52 - Loop Resilience (2026-09-21)
- **`is_error_result()` rewritten** — error detection now inspects only the first non-empty line
- **Consecutive termination semantics** — `should_terminate()` fires only when *every* tool call in last N steps failed
- **Identical duplicate-call blocking** — blocks re-issue after DEFAULT_MAX_IDENTICAL_FAILURES
- **True termination with paired history** — outer step loop actually stops, `sanitize_history()` fills dangling calls
- **Pairing-safe memory pruning** — slides to summarization threshold
- **Hallucinated-parameter stripping** — removes arguments not in schema before execution
- **Shell exit-code marker first** — non-zero exits format correctly
- **48 new tests** added in `tests/test_loop_resilience.py`

### R06.51 - Update Check (2026-09-21)
- **Dual-source update check** — PyPI + GitHub commits
- **`agentkthx/update_check.py`** — new module
- **Per-source caching** — 24h positive, 6h negative
- **54 new tests** added in `tests/test_update_check.py`

### R06.50 - OpenRouter 429 Retry (2026-09-21)
- **Retry budget raised** — 3 → 6 attempts
- **Exponential back-off with jitter** — 5s → 90s cap
- **Transient server errors (502/503/504) now retried**
- **Retry notices always printed**

### R06.41 - Major Security and Cleanup
- **Removed duplicate files** — `agentkthx/acp_plugin.py`, `agentkthx/turbo.py`
- **Renamed env vars** — `AGENTNOVA_*` → `AGENTKTHX_*`
- **Introduced `safe_eval()`** — AST walker replaces `eval()`
- **Extended `DANGEROUS_FLAG_COMBOS`** — context-aware flag blocking
- **Plugin Spec v0.2** — lifecycle hooks, plugin tools, external roots

---

## Test Results (R06.52)

```
$ ZAI_API_KEY=test_dummy_key_12345 python -m pytest tests/ -q
========================= test session starts ==========================
collected 485 items

tests/test_agent.py ............................
tests/test_api_resilience.py .......................
tests/test_backends.py .............................
tests/test_cli.py .................................
tests/test_config.py ............................
tests/test_helpers.py ............................
tests/test_loop_resilience.py .......................
tests/test_memory.py ............................
tests/test_openrouter_backend.py ............s....
tests/test_orchestrator.py ........................
tests/test_plugin_loader.py .....................
tests/test_security.py .....................
tests/test_shell.py ............................
tests/test_soul.py ............................
tests/test_tools.py ............................
tests/test_update_check.py .......................

479 passed, 6 skipped, 0 failed in 12.34s
```

---

## Files Changed Since R06.41 Brief

- Version: 0.6.41 → 0.6.53
- cli.py: 3478 → 3667 lines (added update check, resilience improvements)
- Added: `agentkthx/core/api_resilience.py` (R06.50)
- Added: `agentkthx/update_check.py` (R06.51)
- Updated: `agentkthx/core/memory.py` (R06.52)
- Updated: `agentkthx/core/error_recovery.py` (R06.52)
- Updated: `agentkthx/orchestrator.py` (R06.53 — bare `except:` → `except Exception:`)
- Updated: `agentkthx/plugins/openrouter/openrouter.py` (R06.53 — `stream_options.include_usage`)
- Updated: `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` (R06.53 — `AGENTNOVA_*` → `AGENTKTHX_*`)
- Updated test count: 435 → 638 tests