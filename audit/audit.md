# Improvement & Enhancement Audit

**AgentKthx v0.6.55 (R06.55)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-22  
**Status:** 13 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
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

AgentKthx is a 41,732-line Python framework for autonomous AI agents with zero declared external dependencies — built entirely on the standard library. The codebase has evolved significantly through R06.41 to R06.55, with major work in R06.53–R06.55 on real streaming display (PERF-01), backend inheritance decoupling (ARCH-01), and loop resilience fixes (R06.52).

This fresh audit identified 13 open findings across 6 categories. The most impactful is **MAINT-01** (the 3801-line `cli.py` monolith), followed by a new finding **MAINT-04** (the 2876-line `agent.py` with ~1290 lines of duplicated agentic-loop code between `_run_core()` and `_run_core_streaming()`). The streaming path introduced in R06.53 already shows divergence from the non-streaming path — 24 `if self.debug` checks are missing, meaning `--debug` output is inconsistent between the two modes.

A notable robustness finding is **ROB-04**: the codebase declares `dependencies = []` with a "Zero dependencies!" comment, but `OpenRouterBackend` imports and uses the `requests` library at module level. If `requests` isn't installed, the OpenRouter plugin fails to load silently with a generic "failed to load plugin" warning that doesn't mention the missing dependency.

The R06.55 ARCH-01 refactor was a significant structural improvement — extracting `OpenAICompatibleBackend` removed 534 lines of duplicated code and made the R06.53/R06.54 streaming 404 bug class structurally impossible. However, it also introduced ROB-04 (methods that were inherited from OllamaBackend were missing on the new hierarchy until patched post-release).

The test suite has 672 passed, 6 skipped, 0 failed tests. Security practices remain strong with `safe_eval()` AST walker, `DANGEROUS_FLAG_COMBOS` context-aware flag blocking, and defense-in-depth security model.

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| MAINT-01 | **High** | Maintainability | OPEN | `cli.py` is 3801 lines — monolithic CLI with no module splitting |
| MAINT-04 | **Medium** | Maintainability | OPEN (NEW) | `agent.py` is 2876 lines — `_run_core()` and `_run_core_streaming()` are near-duplicates with divergent debug output |
| ROB-04 | ~~Medium~~ | Robustness | ✓ CLOSED R06.56 | `requests` dependency removed — OpenRouter now uses stdlib `urllib.request` |
| ROB-05 | Low | Robustness | OPEN (NEW) | KeyboardInterrupt during streaming doesn't close HTTP connections |
| PERF-03 | Low | Performance | OPEN (NEW) | `_generate_stream()` has a dead `think` parameter — accepted but never forwarded |
| FEAT-01 | Medium | New Feature | OPEN | No provider routing preferences for OpenRouter |
| FEAT-02 | Low | New Feature | OPEN | `/param` matrix hardcoded, not extensible via plugins |
| ARCH-02 | Low | Architecture | OPEN | No coverage measurement configured |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all tests are mocked unit tests |
| SEC-01 | ~~High~~ | Security | ✓ CLOSED R06.41 | `eval()` sandbox bypass closed via `safe_eval` AST walker |
| SEC-02 | ~~Medium~~ | Security | ✓ CLOSED R06.41 | `shell=True` + blocklist — threat model documented, `DANGEROUS_FLAG_COMBOS` added |
| ROB-01 | ~~High~~ | Robustness | ✓ CLOSED R06.41 | Duplicate ACP/Turbo plugin files removed |
| ROB-02 | ~~Medium~~ | Robustness | ✓ CLOSED R06.53 | Bare `except:` clause replaced with `except Exception:` |
| ROB-03 | ~~Medium~~ | Robustness | ✓ CLOSED R06.41 | Pre-existing test failures fixed (45 → 0) |
| MAINT-02 | ~~Medium~~ | Maintainability | ✓ CLOSED R06.41 | `AGENTNOVA_*` env vars renamed to `AGENTKTHX_*` |
| MAINT-03 | ~~Low~~ | Maintainability | ✓ CLOSED R06.53 | OpenRouter API reference no longer references `AGENTNOVA_*` env vars |
| PERF-01 | ~~Medium~~ | Performance | ✓ CLOSED R06.53 | Real streaming display via `_generate_stream()` + `_run_core_streaming()` |
| PERF-02 | ~~Low~~ | Performance | ✓ CLOSED R06.53 | `stream_options.include_usage` on streaming requests |
| ARCH-01 | ~~Medium~~ | Architecture | ✓ CLOSED R06.55 | `OpenAICompatibleBackend` extracted — ZAI/OpenRouter no longer inherit OllamaBackend |

**Severity distribution**: 1 High (MAINT-01), 2 Medium, 4 Low (excluding closed findings). Of the 8 still-open: 1 High, 2 Medium, 4 Low.

---

## Detailed Findings

### Robustness

#### ROB-04: `requests` dependency violates zero-dependency claim — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py:39`, `pyproject.toml` |

The codebase declares `dependencies = []` in `pyproject.toml` with a comment "Zero dependencies! Uses Python stdlib only." However, `OpenRouterBackend` imports `requests` at module level (line 39: `import requests`) and uses it for all HTTP calls — `requests.get()` for model listing (line 381), `requests.post()` for non-streaming generation (line 625), and `requests.post(stream=True)` for streaming (line 729 and the new `_iter_sse_lines` at line 1031).

If `requests` is not installed, the plugin loader catches the `ImportError` with a generic `except Exception as e:` in `_load_plugin()` and emits a warning: `failed to load plugin 'openrouter': No module named 'requests'`. This message does not tell the user what to do — they need to figure out that `pip install requests` is the fix. Users on minimal Python installs (e.g. `python3-minimal` on Debian/Ubuntu) may not have `requests` pre-installed.

**Recommendation:** Either:
1. **Add `requests` as an optional dependency** in `pyproject.toml`: `[project.optional-dependencies] openrouter = ["requests"]` and document that `pip install agentkthx[openrouter]` is needed for OpenRouter support.
2. **Migrate `OpenRouterBackend` to stdlib `urllib.request`** — ZAI already uses `urllib.request` for all HTTP calls (in `_generate_with_auth` and `_iter_sse_lines`). The OpenRouter code would need to replace `requests.post(stream=True)` with `urllib.request.urlopen` + manual SSE line iteration, which is exactly what ZAI already does.

Option 2 preserves the zero-dependency claim and is consistent with ZAI's implementation.

**Impact:** Users on minimal Python installs can use OpenRouter without a confusing silent plugin-load failure.

---

#### ROB-05: KeyboardInterrupt during streaming doesn't close HTTP connections — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/agent.py:2198` (`_generate_stream()` KeyboardInterrupt handler) |

When the user hits Ctrl+C during streaming, `_generate_stream()` catches `KeyboardInterrupt` at line 2198 and returns a cancelled response dict immediately. However, the underlying HTTP response object is not explicitly closed:

- For OpenAI-compat streaming: the generator from `backend.generate_completions_stream()` is abandoned mid-iteration. Python's garbage collector will eventually call `.close()` on the generator, which triggers the `finally` block in `ZaiBackend._iter_sse_lines()` (line 828) or the `response.close()` in `OpenRouterBackend._iter_sse_lines()`. But GC timing is not guaranteed.
- For native streaming: the generator from `backend.generate_stream()` is similarly abandoned.
- For the non-streaming fallback: `backend.generate()` returns a dict (no connection to close).

The practical impact is that on Ctrl+C mid-stream, the HTTP connection may remain open until GC runs, which could exhaust connection limits on long-running sessions with many interrupts.

**Recommendation:** In the `KeyboardInterrupt` handler, explicitly close the stream generator before returning:

```python
except KeyboardInterrupt:
    # Close the stream generator to release the HTTP connection
    try:
        stream_gen.close()
    except Exception:
        pass
    sys.stdout.write("\n")
    sys.stdout.flush()
    return { ... }
```

**Impact:** Ensures HTTP connections are released immediately on user cancellation, preventing potential connection pool exhaustion.

---

### Maintainability

#### MAINT-01: `cli.py` is 3801 lines — monolithic CLI with no module splitting

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` (3801 lines) |

`cli.py` contains the entire CLI: argument parser construction, all 13+ subcommands, all 9 slash commands, the chat loop, the agent display logic, the banner ASCII art, the `_build_agent()` factory, the `/param` matrix (100+ lines inline), the streaming-aware display logic, the models table rendering, the PEP 668 update prompt, and the reasoning display logic.

**Recommendation:** Split into logical modules:
- `cli/parser.py` — argument parser construction
- `cli/chat.py` — chat loop + slash commands
- `cli/display.py` — banner, footer, spinner, step display, reasoning display
- `cli/agent_factory.py` — `_build_agent()` and `_load_skills_prompt()`
- `cli/params.py` — the `/param` matrix and parameter handling
- `cli/models.py` — models table rendering (`cmd_models`)
- `cli.py` — thin entry point that imports and dispatches

**Impact:** Reduces cognitive load for contributors, makes code review faster, and enables targeted testing.

---

#### MAINT-04: `agent.py` is 2876 lines — `_run_core()` and `_run_core_streaming()` are near-duplicates — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/agent.py` (2876 lines), specifically `_run_core()` (line 630, ~670 lines) and `_run_core_streaming()` (line 2255, ~620 lines) |

The R06.53 streaming implementation (PERF-01) intentionally created `_run_core_streaming()` as a near-copy of `_run_core()` rather than parameterizing the existing method. The rationale was that the non-streaming path has years of bug fixes (R06.52 loop resilience, pairing-safe memory pruning, error tracker integration) that would be risky to thread through a single parameterized implementation.

However, the duplication has already caused divergence:
- `_run_core()` has 31 `if self.debug` checks for verbose debug output
- `_run_core_streaming()` has only 7 `if self.debug` checks
- This means `--debug` mode shows inconsistent output between streaming and non-streaming paths — the streaming path is missing tool-call tracing, memory-add logging, finish_reason reporting, and OpenResponses item-creation debug prints

Any future fix to the agentic loop (error recovery, memory handling, tool dispatch, final-answer enforcement) must be applied in BOTH places. The two methods are already out of sync.

**Recommendation:** Converge the two paths by extracting the shared agentic-loop body into a `_run_loop_iteration()` method that accepts a `generate_fn` callable. The non-streaming path passes `self._generate`, the streaming path passes `self._generate_stream`. Debug output is unified. The duplicated tool-dispatch, error-recovery, and memory-management code collapses into one implementation.

Alternatively, if convergence is too risky, at minimum add a test that asserts both paths produce the same debug output for the same input, so divergence is caught early.

**Impact:** Eliminates the maintenance burden of keeping two ~650-line methods in sync. Prevents future divergence bugs.

---

### Performance

#### PERF-03: `_generate_stream()` has a dead `think` parameter — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/backends/openai_compat.py:620` (`generate_completions_stream` signature), `agentkthx/backends/openai_compat.py:476` (`_build_openai_body` signature) |

The base class `OpenAICompatibleBackend.generate_completions_stream()` accepts `think: bool | None = None` as an explicit parameter (line 620). However, when it calls `_build_openai_body()` (line 656), `think` is NOT passed as a keyword argument — it's an explicit parameter of `generate_completions_stream` but not in `**kwargs`, so it's silently dropped.

The `_build_openai_body()` method also accepts `think` in its `**kwargs` (via the `generate_completions_stream` call) but never adds it to the body. The non-streaming `OllamaBackend.generate_completions()` explicitly adds `body["think"] = think` (line 466), but the base class `_build_openai_body()` does not.

This means:
- In non-streaming mode: `think=True` works for Ollama (Ollama's own `generate_completions` adds it)
- In streaming mode: `think=True` has no effect — the parameter is accepted but never forwarded to the request body

This is not a functional bug for ZAI (ZAI manages thinking internally) or OpenRouter (uses `reasoning_effort`, not `think`). But it's a misleading API surface — a user passing `think=True` to a streaming call would expect it to work.

**Recommendation:** Either:
1. **Forward `think` to `_build_openai_body`** explicitly and add it to the body when not None (matching Ollama's non-streaming behavior)
2. **Remove `think` from the `generate_completions_stream` signature** if it's not applicable to OpenAI-compat streaming (ZAI/OpenRouter don't use it)

Option 1 is safer for backward compatibility with Ollama streaming (which uses its own `generate_completions_stream` but could be refactored to use the base class in the future).

**Impact:** Eliminates a misleading dead parameter that could confuse developers and users.

---

### New Features

#### FEAT-01: No provider routing preferences for OpenRouter

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py` |

OpenRouter supports a `provider` object in the request body that controls routing. AgentKthx does not send this field. Users have no way to pin to specific providers, ignore unreliable ones, or control data collection.

**Recommendation:** Add CLI flags `--provider-order`, `--provider-ignore`, `--provider-data-collection deny` that get forwarded as the `provider` object in the request body via `_build_openai_body()`.

**Impact:** Gives users control over which upstream providers serve their requests.

---

#### FEAT-02: `/param` matrix is hardcoded, not extensible via plugins

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/cli.py` (inline `PARAM_MATRIX` dict in `cmd_chat`, line 1337) |

The `/param` slash command's parameter support matrix is a hardcoded dict inside `cmd_chat()`. Adding a new parameter requires editing this inline data structure.

**Recommendation:** Move the `PARAM_MATRIX` to a separate `cli/params.py` module and expose a `register_param(name, spec)` API that plugins can call.

**Impact:** Makes the parameter system extensible and reduces the size of `cli.py` (overlaps with MAINT-01).

---

### Architecture

#### ARCH-02: No coverage measurement configured

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `pyproject.toml` |

The project has 672 tests but no coverage measurement configured. `pytest-cov` is installed (available in the dev environment) but not declared in `dev` dependencies and not configured in `addopts`.

**Recommendation:** Add `pytest-cov` to `dev` dependencies, configure `addopts = "-v --tb=short --cov=agentkthx --cov-report=term-missing"` in `pyproject.toml`.

**Impact:** Makes coverage visible and prevents silent coverage regression.

---

### Testing

#### TEST-01: No integration tests — all tests are mocked unit tests

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (all 15 test files) |

All 672 tests use `MagicMock`, `monkeypatch`, or source-level string inspection. No test makes a real HTTP call to a backend, no test runs a full agent loop end-to-end, and no test exercises the actual CLI. The R06.53 streaming tests (`test_streaming.py`, 359 lines) mock the backend's `generate_completions_stream` with fake SSE chunks — they verify accumulation logic but not real HTTP transport.

The R06.55 ARCH-01 refactor exposed the risk: `get_model_runtime_context()` was missing on `OpenRouterBackend` after the inheritance change, but no test caught it because no test actually instantiates `OpenRouterBackend` and calls `get_model_runtime_context()`. The bug was only found by manual testing on the VM.

**Recommendation:** Add a `tests/integration/` directory with tests that:
1. Instantiate real backend objects (with mock HTTP transport at the `urllib`/`requests` level)
2. Exercise the full `Agent.run()` → `backend.generate()` → response → display path
3. Test `cmd_models`, `cmd_chat`, `cmd_run` with mocked stdin/stdout
4. Verify all methods called by `cli.py` exist on each backend (catches the ARCH-01 regression class)

**Impact:** Catches integration bugs that unit tests miss; reduces reliance on manual VM testing for regressions.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R06.6–R06.7)** | MAINT-01 (cli.py split), ROB-04 (requests dependency), MAINT-04 (agent.py duplication) |
| **Short term (R06.7–R07.0)** | FEAT-01 (OpenRouter provider routing), TEST-01 (integration tests), ROB-05 (streaming Ctrl+C connection leak) |
| **Medium term (R07.0+)** | FEAT-02 (/param matrix extensibility), ARCH-02 (coverage measurement), PERF-03 (dead think parameter) |

---

## Architecture Strengths

1. **`OpenAICompatibleBackend` extraction (R06.55, ARCH-01)** — The new intermediate base class (771 lines) provides shared JEV dispatch, body construction (`_build_openai_body`), response parsing (`_parse_openai_response`), streaming SSE (`generate_completions_stream`), and the `api_mode` property. Each concrete backend provides four abstract hooks (`_get_chat_completions_url`, `_get_auth_headers`, `_iter_sse_lines`, `_get_model_defaults`). This made the R06.53/R06.54 streaming 404 bug class structurally impossible — each backend provides its own URL, and the shared streaming method uses it. 534 lines of duplicated code were removed.

2. **Zero dependencies by design** — The entire framework runs on Python stdlib (urllib, sqlite3, argparse, json, re, ast). This eliminates dependency management, supply chain attacks, and version conflicts. (Caveat: OpenRouter uses `requests` — see ROB-04.)

3. **Plugin system with directory-scan discovery** — The `PluginManager` in `plugins/_loader.py` discovers plugins by scanning `plugins/*/plugin.json` manifests. Plugin load failures are isolated — one broken plugin doesn't prevent others from loading.

4. **Defense-in-depth security with explicit threat model** — Security layers: `BLOCKED_COMMANDS` (denylist) → `DANGEROUS_FLAG_COMBOS` (context-aware flag blocks) → injection-pattern regex → `--security max|off` runtime toggle. The `safe_eval()` AST walker rejects `ast.Attribute` and `ast.Subscript` outright.

5. **JEV mode as an API mode, not a backend** — `ApiMode.JEV` allows any chat-capable backend to produce Jev-shaped decisions via `generate_decision()`. The decision prompt, JSON parsing, and constrained-choice snapping are all in the shared base class.

6. **`safe_eval()` AST walker (R06.41)** — Deny-by-default AST evaluation that rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, and walrus operators. A jailbroken model can no longer achieve arbitrary code execution via the calculator tool.

7. **`DANGEROUS_FLAG_COMBOS` context-aware flag blocking (R06.41)** — Rather than blocking entire commands, the dict pairs each binary with specific dangerous flag combinations (e.g. `find` + `-exec`, `python` + `-c`, `tar` + `--use-compress-program`).

8. **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — Clean separation between user-facing enum, parser, and per-backend forwarding.

9. **Backward compatibility as a first-class concern** — The R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility via redirect stubs (`agentnova/`, `localclaw/` packages still importable).

10. **Loop resilience (R06.52)** — Improved error detection, consecutive termination semantics, duplicate call blocking, pairing-safe memory pruning, and hallucinated-parameter stripping prevent the "death-spiral" scenario where the agent re-issues the same failing tool call forever.

11. **API resilience (R06.50)** — Transient API errors (rate limits, empty responses, connection blips, provider 5xx) are retried at the agent-loop level with escalating back-off. Permanent errors fail fast. Both `run()` and `run_stream()` share the semantics.

12. **Update check system (R06.51)** — Dual-source checking (PyPI + GitHub commits) with smart caching (24h positive, 6h negative) and notification placement (banner + post-run).

13. **Real streaming display (R06.53, PERF-01)** — `_generate_stream()` + `_run_core_streaming()` provide typewriter-style output with tool-call fragment accumulation across SSE chunks. The `AgentKthx:` prefix is emitted once per user prompt. Same `AgentRun` return shape as non-streaming.

---

## Files Changed Since R06.41

- Version: 0.6.41 → 0.6.55
- cli.py: 3478 → 3801 lines (added update check, resilience improvements, streaming display, PEP 668 prompt, models table polish)
- agent.py: 1747 → 2876 lines (added `_generate_stream()` + `_run_core_streaming()` for PERF-01, ~1290 lines of near-duplicated loop)
- Added: `agentkthx/backends/openai_compat.py` (R06.55 — new `OpenAICompatibleBackend` base class, 771 lines)
- Added: `agentkthx/core/api_resilience.py` (R06.50)
- Added: `agentkthx/update_check.py` (R06.51)
- Updated: `agentkthx/backends/ollama.py` (R06.55 — parent changed to `OpenAICompatibleBackend`, JEV methods removed, 233 lines removed)
- Updated: `agentkthx/plugins/zai/zai.py` (R06.54 — streaming override; R06.55 — parent changed to `OpenAICompatibleBackend`, 90 lines removed)
- Updated: `agentkthx/plugins/openrouter/openrouter.py` (R06.53 — streaming + `stream_options`; R06.55 — parent changed to `OpenAICompatibleBackend`, 211 lines removed)
- Updated: `agentkthx/orchestrator.py` (R06.53 — bare `except:` → `except Exception:`)
- Updated: `docs/OPENROUTER_API_TECHNICAL_REFERENCE.md` (R06.53 — `AGENTNOVA_*` → `AGENTKTHX_*`)
- Updated test count: 435 → 672 tests
