# Improvement & Enhancement Audit

**AgentKthx v0.6.57 (R06.57)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-25  
**Status:** 8 Open Findings | 8 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST, DOC

> **R06.57 delta (2026-09-25):** Closed **ROB-05** (streaming KeyboardInterrupt now calls `stream_gen.close()` in `agent.py:2342-2365`), closed **ROB-06** (OpenRouter & Gemini `_iter_sse_lines` + `_stream_request` now have `try/finally response.close()` matching ZAI's pattern — 4 sites patched), closed **DOC-01** (Gemini env vars now documented in README Configuration section + master env-var table), closed **MAINT-05** (8 hardcoded backend allowlists in cli.py replaced with `getattr(backend, 'is_cloud', False)` — new `is_cloud` class attribute on `BaseBackend`/`OpenAICompatibleBackend`/`OllamaBackend`; a 5th cloud backend is now a 1-line change instead of an 8-site edit), closed **ARCH-03** (triplicated 429 retry / `num_ctx/32` cap / `_calculate_safe_max_tokens` pattern lifted to `OpenAICompatibleBackend` as 3 shared methods + 6 class attributes; concrete backends shed 213 lines of duplication; Gemini overrides 3 regex patterns for its different error format), brought ZAI to parity with OpenRouter/Gemini on the context-length 400 recovery pattern, shipped **FIX-01** (pip-installed users now see dev releases via new `_fetch_github_latest_version` — surfaces R06.55+, R06.56+, R06.57+ that aren't on PyPI), and shipped **FIX-02** (update-check cache TTL reduced from 24h to 1h success / 6h to 15min failure + new `agentkthx version --refresh` flag — fixes user-reported "PyPI says 0.6.54 but 0.6.55+0.6.56 are out" stale-cache bug). Test suite grew 766 → **822 passed** (+56 new tests: 11 for FIX-01 + 9 for FIX-02 + 13 for MAINT-05 + 23 for ARCH-03, 6 existing tests updated for new URL counts). See `docs/CHANGELOG.md` for the full R06.57 entry.

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
  - [Documentation](#documentation)
- [Priority Matrix](#priority-matrix)
- [Architecture Strengths](#architecture-strengths)

---

## Executive Summary

AgentKthx is a 43,000+ line Python framework for autonomous AI agents with zero declared external dependencies — built entirely on the standard library. The codebase has evolved significantly through R06.41 to R06.56, with major work in R06.55 on real streaming display (PERF-01), backend inheritance decoupling (ARCH-01), and loop resilience fixes (R06.52). R06.56 (released 2026-09-24) added a 4th cloud backend — Google Gemini via its OpenAI-compatible endpoint — and migrated OpenRouter from the `requests` library to stdlib `urllib.request`, closing the long-standing ROB-04 dependency violation.

This fresh audit identified **13 open findings** across 8 categories. The most impactful is **MAINT-01** (the 4079-line `cli.py` monolith, which grew +278 lines in R06.56), followed by **MAINT-04** (the 3119-line `agent.py` with ~1330 lines of duplicated agentic-loop code between `_run_core()` and `_run_core_streaming()` — debug-check divergence has worsened from 24 to 29 missing checks, and UX-01 reasoning panel state now exists ONLY in the streaming path). A new finding **MAINT-05** documents that the R06.56 BUG-01/BUG-02 fixes for Gemini were patches, not generalizations — 8 hardcoded backend allowlists in `cli.py` mean the next cloud backend will hit the same crash class.

The most impactful new robustness finding is **ROB-06**: the OpenRouter and Gemini streaming implementations (`_iter_sse_lines` and `_stream_request`) lack the `try: ... finally: response.close()` pattern that ZAI already implements correctly at `zai.py:705-712`. Combined with the still-open **ROB-05** (KeyboardInterrupt mid-stream doesn't call `stream_gen.close()`), HTTP connections can leak on long-running sessions with frequent cancellations.

A new architecture finding **ARCH-03** documents ~400 lines of 429 retry / SSE recovery / token-parsing logic duplicated nearly verbatim between OpenRouter and Gemini — the `OpenAICompatibleBackend` base class eliminated duplication for body construction and response parsing, but never absorbed the retry layer. A future 5th cloud backend (DeepSeek, Together AI, Mistral — all OpenAI-compat) would need to copy this boilerplate.

The test suite has grown from 710 (R06.55 release) to **766 passed, 9 skipped, 0 failed** — +56 new tests, all from the new `tests/test_gemini_backend.py` (1111 lines, 97 test methods). However, **TEST-01** (no integration tests) remains open: the R06.56 BUG-01 and BUG-02 cli.py regressions were caught by manual VM testing, not by tests, because no test instantiates a real backend and exercises `cmd_models` end-to-end. Security practices remain strong with `safe_eval()` AST walker, `DANGEROUS_FLAG_COMBOS` context-aware flag blocking, and defense-in-depth security model.

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| MAINT-01 | **High** | Maintainability | OPEN (worsened +278 lines) | `cli.py` is 4079 lines — monolithic CLI with no module splitting |
| MAINT-04 | Medium | Maintainability | OPEN (worsened, gap 29) | `agent.py` is 3119 lines — `_run_core()` and `_run_core_streaming()` near-duplicates with divergent debug output |
| FEAT-01 | Medium | New Feature | OPEN | No provider routing preferences for OpenRouter |
| FEAT-03 | Medium | New Feature | OPEN (NEW, acknowledged v0.1 limitation) | Gemini thought-signature stateful continuation NOT implemented — multi-turn loops re-derive reasoning (~2-3x token cost) |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all tests are mocked unit tests; BUG-01/02 caught by manual VM testing |
| PERF-03 | Low | Performance | OPEN | `_generate_stream()` has a dead `think` parameter — accepted but never forwarded |
| FEAT-02 | Low | New Feature | OPEN (sub-issue worsened) | `/param` matrix hardcoded; Gemini excluded from 5 params |
| ARCH-02 | Low | Architecture | OPEN | No coverage measurement configured |
| ~~ROB-05~~ | ~~Low~~ | Robustness | ✓ CLOSED R06.57 | KeyboardInterrupt during streaming doesn't close HTTP connections — fixed in `agent.py:2342-2365` via `stream_gen.close()` |
| ~~ROB-06~~ | ~~Low~~ | Robustness | ✓ CLOSED R06.57 | OpenRouter & Gemini `_iter_sse_lines` + `_stream_request` lack `try/finally response.close()` — fixed in 4 sites, all now match ZAI's pattern |
| ~~MAINT-05~~ | ~~Medium~~ | Maintainability | ✓ CLOSED R06.57 | 8 hardcoded backend allowlists in `cli.py` — replaced with `getattr(backend, 'is_cloud', False)`; new `is_cloud` class attribute on `BaseBackend`/`OpenAICompatibleBackend`/`OllamaBackend` |
| ~~ARCH-03~~ | ~~Low~~ | Architecture | ✓ CLOSED R06.57 | ~400 lines of 429 retry / SSE recovery / `num_ctx/32` cap duplicated across OpenRouter, Gemini, ZAI — lifted to `OpenAICompatibleBackend` as 3 shared methods (`_apply_max_tokens_cap`, `_calculate_safe_max_tokens`, `_handle_context_length_400`) + 6 class attributes; concrete backends shed 213 lines |
| ~~DOC-01~~ | ~~Low~~ | Documentation | ✓ CLOSED R06.57 | Gemini env vars not documented in README Configuration section — fixed with new `### Gemini Configuration` subsection + master env-var table block |

**Severity distribution (R06.57 final)**: 1 High (MAINT-01), 4 Medium (MAINT-04, FEAT-01, FEAT-03, TEST-01), 3 Low. **5 findings closed in R06.57** (ROB-05, ROB-06, MAINT-05, ARCH-03, DOC-01). **2 user-reported bugs fixed** (FIX-01: pip-installed users now see dev releases via `agentkthx update` / `agentkthx version`; FIX-02: update-check cache TTL reduced 24h→1h + `agentkthx version --refresh` flag for on-demand cache bypass).

**Findings closed since R06.55 audit**:
- ~~ROB-04~~ (Medium → CLOSED R06.55): `requests` dependency removed — OpenRouter migrated to stdlib `urllib.request`. Verified: `import urllib.request` at line 37-38, no `import requests` anywhere in module.
- ~~SEC-01~~ (High → CLOSED R06.41): `eval()` sandbox bypass closed via `safe_eval` AST walker.
- ~~SEC-02~~ (Medium → CLOSED R06.41): `shell=True` + blocklist — threat model documented, `DANGEROUS_FLAG_COMBOS` added.
- ~~ROB-01/02/03~~ (CLOSED R06.41–R06.53): duplicate plugin files, bare `except:`, pre-existing test failures.
- ~~MAINT-02/03~~ (CLOSED R06.41–R06.53): env var rename, doc reference updates.
- ~~PERF-01/02~~ (CLOSED R06.53): real streaming display, `stream_options.include_usage`.
- ~~ARCH-01~~ (Medium → CLOSED R06.55): `OpenAICompatibleBackend` extracted — ZAI/OpenRouter no longer inherit OllamaBackend. 534 lines of duplication removed. GeminiBackend (R06.56) follows the same pattern.

---

## Detailed Findings

### Security

No new security findings. The R06.41 security posture remains intact:
- `safe_eval()` AST walker rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, walrus operators.
- `DANGEROUS_FLAG_COMBOS` provides context-aware flag blocking (e.g. `find` + `-exec`, `python` + `-c`, `tar` + `--use-compress-program`).
- `BLOCKED_COMMANDS` denylist, injection-pattern regex, and `--security max|off` runtime toggle.
- `validate_path()` and `is_safe_url()` provide SSRF and path-traversal protection.
- The R06.55 urllib migration preserved all SSL verification (default `urllib.request.urlopen` behavior) and added explicit `urllib.error.URLError` handling.

The new Gemini plugin (R06.56) does not introduce any new security surface — it uses the same `OpenAICompatibleBackend` base class and inherits all sanitization. The `extra_body.google.*` parameter surface is constructed from typed `kwargs`, not raw user input.

---

### Robustness

#### ROB-05: KeyboardInterrupt during streaming doesn't close HTTP connections — OPEN

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/agent.py:2342-2354` (`_generate_stream()` KeyboardInterrupt handler) |

When the user hits Ctrl+C during streaming, `_generate_stream()` catches `KeyboardInterrupt` at line 2342 and returns a cancelled response dict immediately. However, the underlying HTTP response object is not explicitly closed:

```python
except KeyboardInterrupt:
    # User cancelled mid-stream
    sys.stdout.write("\n")
    sys.stdout.flush()
    return {
        "content": "",
        "_finish_reason": "cancelled",
        "_cancelled": True,
        ...
    }
```

The `stream_gen` variable (assigned inside the `try` block at line 2231 for OpenAI-compat path and line 2318 for native path) is abandoned mid-iteration without `.close()`. Python's garbage collector will eventually call `.close()` on the generator, which triggers the `finally` block in `ZaiBackend._iter_sse_lines()` (line 705-712). But GC timing is not guaranteed, and OpenRouter/Gemini don't have the `finally` block at all (see ROB-06).

The practical impact is that on Ctrl+C mid-stream, the HTTP connection may remain open until GC runs, which could exhaust connection limits on long-running sessions with many interrupts.

**Recommendation:** In the `KeyboardInterrupt` handler, explicitly close the stream generator before returning:

```python
except KeyboardInterrupt:
    # Close the stream generator to release the HTTP connection
    try:
        if stream_gen is not None:
            stream_gen.close()
    except Exception:
        pass
    sys.stdout.write("\n")
    sys.stdout.flush()
    return { ... }
```

Pairs naturally with the ROB-06 fix — once OpenRouter and Gemini have proper `try/finally` in `_iter_sse_lines`, calling `stream_gen.close()` here will deterministically release the HTTP connection.

**Impact:** Ensures HTTP connections are released immediately on user cancellation, preventing potential connection pool exhaustion on long-running chat sessions.

---

#### ROB-06: OpenRouter & Gemini `_iter_sse_lines` lack `try/finally response.close()` — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Low (Medium when combined with ROB-05) |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py:1103-1148` (`_iter_sse_lines`), `:781-808` (`_stream_request`); `agentkthx/plugins/gemini/gemini.py:1440-1475` (`_iter_sse_lines`), `:1406-1434` (`_stream_request`); `agentkthx/plugins/zai/zai.py:705-712` (reference implementation with correct pattern) |

The R06.55 urllib migration was done correctly for non-streaming `_make_api_request` — both OpenRouter (`openrouter.py:682`) and Gemini (`gemini.py:1300`) use `with urllib.request.urlopen(req, timeout=...) as resp:` so the response is closed on exit. But for the streaming paths (`_iter_sse_lines` and `_stream_request`), neither backend uses `with` or `try/finally`:

OpenRouter `_iter_sse_lines` (lines 1123-1148):
```python
response = urllib.request.urlopen(req, timeout=self.config.timeout)
# (no with, no try around the next line)
for line in response:
    yield line
return  # success — don't retry
```

Gemini `_iter_sse_lines` (lines 1454-1475) is identical in shape. Same pattern in OpenRouter `_stream_request` (lines 790-808) and Gemini `_stream_request` (lines 1415-1434).

For contrast, ZAI's `_iter_sse_lines` (lines 705-712) does it correctly:
```python
try:
    for line in response:
        yield line
finally:
    try:
        response.close()
    except Exception:
        pass
```

When the generator is abandoned mid-iteration (Ctrl+C per ROB-05, an exception inside the consumer, or just the base-class `break` at `openai_compat.py:703` on `[DONE]`), the OpenRouter/Gemini HTTP response object is leaked until GC. The ZAI version closes it deterministically. The non-streaming `_make_api_request` in both new backends is fine (uses `with`), so the leak only affects streaming.

**Recommendation:** Add the `try: for line in response: yield line finally: response.close()` pattern to all four sites. The fix is ~5 lines per site, mirroring ZAI's existing pattern:

```python
response = urllib.request.urlopen(req, timeout=self.config.timeout)
try:
    for line in response:
        yield line
finally:
    try:
        response.close()
    except Exception:
        pass
```

Add a regression test in `tests/test_openrouter_backend.py` and `tests/test_gemini_backend.py` that creates a generator from `_iter_sse_lines`, calls `next()` once, then `.close()`s the generator, and asserts the underlying mock response's `.close()` was called. This mirrors the test that should exist for ZAI's pattern.

**Impact:** Eliminates a connection-leak class that compounds with ROB-05 (Ctrl+C) and the base-class `break` on `[DONE]`. Makes the streaming cleanup contract uniform across all 4 cloud backends.

---

### Maintainability

#### MAINT-01: `cli.py` is 4079 lines — monolithic CLI with no module splitting — OPEN (worsened)

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` (4079 lines, was 3801 in R06.55; **+278 lines**) |

`cli.py` contains the entire CLI: argument parser construction, all 13+ subcommands, all 9+ slash commands (plus R06.56's new `/models`, `/tool`, `/skill` per FEAT-05), the chat loop, the agent display logic, the banner ASCII art, the `_build_agent()` factory, the `/param` matrix (100+ lines inline at line 1519), the streaming-aware display logic (UX-01 reasoning panel state), the models table rendering, the PEP 668 update prompt, and the reasoning display logic. R06.56 added the BUG-02 `replace_all` of `BackendType.GEMINI` into 6 cloud-provider allowlist sites, plus the new slash commands, plus the deprecation-warning column.

**Recommendation:** Split into logical modules:
- `cli/parser.py` — argument parser construction
- `cli/chat.py` — chat loop + slash commands
- `cli/display.py` — banner, footer, spinner, step display, reasoning display (UX-01 logic at lines 2139-2154)
- `cli/agent_factory.py` — `_build_agent()` and `_load_skills_prompt()`
- `cli/params.py` — the `/param` matrix and parameter handling (overlaps with FEAT-02)
- `cli/models.py` — models table rendering (`cmd_models`)
- `cli.py` — thin entry point that imports and dispatches

Pairs naturally with MAINT-05 (generalize backend allowlists) and FEAT-02 (extract `PARAM_MATRIX`) — both touch the same file and can be done as part of the same refactor.

**Impact:** Reduces cognitive load for contributors, makes code review faster, and enables targeted testing. Without a split, MAINT-05 and FEAT-02 each require touching a 4079-line file, which discourages the work.

---

#### MAINT-04: `agent.py` is 3119 lines — `_run_core()` and `_run_core_streaming()` are near-duplicates — OPEN (worsened)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/agent.py` (3119 lines, was 2876; **+243 lines**); specifically `_run_core()` (line 649, spans ~727 lines) and `_run_core_streaming()` (line 2399, spans ~602 lines) |

The R06.53 streaming implementation (PERF-01) intentionally created `_run_core_streaming()` as a near-copy of `_run_core()` rather than parameterizing the existing method. The rationale was that the non-streaming path has years of bug fixes (R06.52 loop resilience, pairing-safe memory pruning, error tracker integration) that would be risky to thread through a single parameterized implementation.

However, the duplication has continued to diverge:
- `_run_core()` has **36** `if self.debug` checks for verbose debug output (was 31 in R06.55 → +5)
- `_run_core_streaming()` has only **7** `if self.debug` checks (unchanged)
- Gap = **29** missing debug checks (was 24)
- This means `--debug` mode shows inconsistent output between streaming and non-streaming paths

R06.56's UX-01 changes (reasoning panel above `AgentKthx:` prefix) added new state variables `_reasoning_panel_started` and `_reasoning_first_line_emitted` (lines 2139-2143) and two new helpers `_emit_reasoning_panel_header()` (line 2145) and `_indent_reasoning_delta()` (line 2154) that exist **only** in `_generate_stream()` / `_run_core_streaming()`. The non-streaming path has no equivalent — so the streaming and non-streaming UX now diverge too, not just the debug output.

Any future fix to the agentic loop (error recovery, memory handling, tool dispatch, final-answer enforcement) must be applied in BOTH places. The two methods are already out of sync.

**Recommendation:** Converge the two paths by extracting the shared agentic-loop body into a `_run_loop_iteration(generate_fn)` method that accepts a `generate_fn` callable. The non-streaming path passes `self._generate`, the streaming path passes `self._generate_stream`. Debug output is unified. The duplicated tool-dispatch, error-recovery, and memory-management code collapses into one implementation.

Alternatively, if convergence is too risky, at minimum add a test that asserts both paths produce the same debug output for the same input, so divergence is caught early. The UX-01 reasoning panel state should also be lifted to a shared helper so the non-streaming path can use it.

**Impact:** Eliminates the maintenance burden of keeping two ~650-line methods in sync. Prevents future divergence bugs. The current gap of 29 missing debug checks means streaming users see ~24% less diagnostic output than non-streaming users when `--debug` is on.

---

#### MAINT-05: 8 hardcoded backend allowlists in `cli.py`; BUG-01/02 fixes were patches, not generalizations — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py:514, 587, 645, 787, 1953, 1972, 2326, 2380-2383` |

The R06.56 changelog BUG-01 entry describes the fix as a two-part defense: (1) extend the `cli.py:2326` allowlist from `("openrouter")` to `("openrouter", "gemini")`; (2) soften `GeminiBackend.__init__` to normalize `OPENRE → OPENAI` instead of raising. The changelog BUG-02 entry describes a second fix: adding `BackendType.GEMINI` to all 6 cloud-provider allowlists via `replace_all`. **Both fixes preserved the hardcoded architecture** rather than generalizing it.

Concrete sites where a backend name or `BackendType` is hardcoded:

| Line | Code | Purpose |
|------|------|---------|
| 514 | `backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]` | `cmd_run` cloud-provider default-streaming |
| 587 | same | `cmd_chat` cloud-provider default-streaming |
| 645 | same | catalog-based `num_ctx`/`num_predict` defaults |
| 787 | same | `cmd_run` second cloud check |
| 1953 | same | `/status` slash command cloud display |
| 1972 | same | `/status` cloud default stream |
| 2326 | `if backend_name in ("openrouter", "gemini"):` | `cmd_models` api_mode allowlist |
| 2380-2383 | `is_cloud_provider = backend.backend_type in [OPENROUTER, ZAI, GEMINI]` | `cmd_models` column layout |

A 5th cloud backend would require editing all 8 sites. The plugin manifest already declares `cli_flags: {"--backend": ["gemini"]}` (in `agentkthx/plugins/gemini/plugin.json:34-38`), but this isn't consulted for the api_mode decision or the cloud-provider flag.

**Recommendation:**

1. **Generalize the cloud-provider check** — Replace `is_cloud_provider` checks with `isinstance(backend, OpenAICompatibleBackend) and not isinstance(backend, OllamaBackend)`. The `OllamaBackend` exclusion is needed because `OllamaBackend` also extends `OpenAICompatibleBackend` (it was the original parent before ARCH-01 extraction). Alternatively, add an `is_cloud: bool = True` class attribute on `OpenAICompatibleBackend` and override `is_cloud = False` on `OllamaBackend`. This eliminates 6 of the 8 hardcoded sites.

2. **Generalize the api_mode allowlist** — Replace the `cli.py:2326` allowlist with a query against the backend's supported modes. `GeminiBackend.__init__` already silently normalizes `OPENRE → OPENAI` (lines 818-820), so the cli.py allowlist is redundant defensive code. If the team wants to keep the strict path for OpenRouter, expose `SUPPORTED_API_MODES: set[ApiMode]` on each backend class and let `cmd_models` choose from that.

3. **Test coverage** — Add a unit test that asserts every backend in `plugins/*/plugin.json` passes through `cmd_models` without a hardcoded allowlist lookup. This would have caught BUG-01/BUG-02 at test time instead of at manual VM time.

**Impact:** Adding a 5th cloud backend (DeepSeek, Together AI, Mistral, etc.) becomes a 1-line change (add `BackendType.DEEPSEEK`) instead of an 8-site edit. Eliminates the recurring "new backend crashes `agentkthx models`" bug class.

---

### Performance

#### PERF-03: `_generate_stream()` has a dead `think` parameter — OPEN

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/backends/openai_compat.py:620` (`generate_completions_stream` signature), `:669-683` (call site), `:466` (`_build_openai_body` signature); `agentkthx/plugins/gemini/gemini.py:1186` (`_build_openai_body` override) |

The base class `OpenAICompatibleBackend.generate_completions_stream()` accepts `think: bool | None = None` as an explicit parameter (line 620). However, when it calls `_build_openai_body()` (lines 669-683), `think` is NOT passed as a keyword argument — it's an explicit parameter of `generate_completions_stream` but not in `**kwargs`, so it's silently dropped.

The `_build_openai_body()` method (line 466) accepts `think` in its `**kwargs` (via the `generate_completions_stream` call) but never adds it to the body. The non-streaming `OllamaBackend.generate_completions()` explicitly adds `body["think"] = think` (line 466 in the old version — verify exact line in current code), but the base class `_build_openai_body()` does not.

Gemini's `_build_openai_body` override (line 1186) also doesn't forward `think` — it handles `reasoning_effort` and `extra_body.google.thinking_config` separately, but a caller passing `think=True` to a streaming Gemini call gets nothing.

This means:
- In non-streaming mode: `think=True` works for Ollama (Ollama's own `generate_completions` adds it)
- In streaming mode: `think=True` has no effect on any OpenAI-compat backend — the parameter is accepted but never forwarded to the request body

This is not a functional bug for ZAI (ZAI manages thinking internally) or OpenRouter (uses `reasoning_effort`, not `think`). But it's a misleading API surface — a user passing `think=True` to a streaming call would expect it to work.

**Recommendation:** Either:
1. **Forward `think` to `_build_openai_body`** explicitly and add it to the body when not None (matching Ollama's non-streaming behavior)
2. **Remove `think` from the `generate_completions_stream` signature** if it's not applicable to OpenAI-compat streaming (ZAI/OpenRouter/Gemini don't use it)

Option 1 is safer for backward compatibility with Ollama streaming (which uses its own `generate_completions_stream` but could be refactored to use the base class in the future).

**Impact:** Eliminates a misleading dead parameter that could confuse developers and users.

---

### New Features

#### FEAT-01: No provider routing preferences for OpenRouter — OPEN

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py`, `agentkthx/backends/openai_compat.py:466-528` (`_build_openai_body`) |

OpenRouter supports a `provider` object in the request body that controls routing. AgentKthx does not send this field. Users have no way to pin to specific providers, ignore unreliable ones, or control data collection.

The base class `_build_openai_body()` (lines 487-528) builds the body but never injects a `provider` key. The OpenRouter `_build_openai_body` override was removed in R06.55 (now inherited), so there's no OpenRouter-specific injection point either.

**Recommendation:** Add CLI flags `--provider-order`, `--provider-ignore`, `--provider-data-collection deny` that get forwarded as the `provider` object in the request body via `_build_openai_body()`. Per-call overrides via `agent.run(provider=...)` for the Python API.

**Impact:** Gives users control over which upstream providers serve their requests. Particularly useful for users on strict data-residency requirements (e.g. EU-only, no Anthropic) or for routing around providers with known reliability issues.

---

#### FEAT-02: `/param` matrix is hardcoded, not extensible via plugins — OPEN (sub-issue worsened)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/cli.py:1519-1622` (inline `PARAM_MATRIX`) |

The `/param` slash command's parameter support matrix is a hardcoded dict inside `cmd_chat()` at line 1519. Adding a new parameter requires editing this inline data structure.

**New sub-issue introduced in R06.56**: the per-parameter `"backends"` sets were not updated for Gemini. Specific exclusions:
- `top_k` (line 1562): `{"openrouter", "ollama", "llama_server", "bitnet"}` — Gemini excluded
- `seed` (line 1569): same set — Gemini excluded
- `n` (line 1576): `{"openrouter", "ollama"}` — Gemini excluded
- `presence_penalty` (line 1583): `{"openrouter", "zai", "ollama"}` — Gemini excluded
- `frequency_penalty` (line 1590): `{"openrouter", "zai", "ollama"}` — Gemini excluded

These params are sent by the OpenAI-compat API which Gemini supports, so the exclusion is likely incorrect — but even if intentional, it shows the hardcoded matrix has missed a backend on its first release. This is exactly the fragility the original FEAT-02 predicted.

**Recommendation:** Move the `PARAM_MATRIX` to a separate `cli/params.py` module and expose a `register_param(name, spec)` API that plugins can call. Per-backend support should be derived from a backend capability query (e.g. `backend.supports_param("top_k")`), not a hardcoded set. Additionally, verify on a real Gemini VM whether the 5 excluded params are actually rejected — if not, add `"gemini"` to all 5 sets.

**Impact:** Makes the parameter system extensible, reduces the size of `cli.py` (overlaps with MAINT-01), and prevents the recurring "new backend missing from param matrix" bug.

---

#### FEAT-03: Gemini thought-signature stateful continuation NOT implemented — OPEN (NEW, acknowledged v0.1 limitation)

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/plugins/gemini/gemini.py:50-52` (docstring), `:1242-1243` (forward-only plumbing), `:1534` (docstring); `tests/test_gemini_backend.py:923-931` (only verifies forwarding, no stateful accumulator test) |

The Gemini backend explicitly documents this gap at module-docstring lines 50-52:

> *"Thought-signature stateful continuation is NOT yet implemented in v0.1. Multi-turn agent loops will re-derive reasoning from scratch each turn, which costs ~2-3x more reasoning tokens. Track this for v0.2."*

The plumbing is half-built: `_build_openai_body()` does forward a caller-supplied `thought_signature` kwarg into `body["extra_body"]["google"]["thinking_config"]["thought_signature"]` (lines 1242-1243), and the test `test_thought_signature_routed_via_thinking_config` (line 923) verifies that. But **nothing captures the `thought_signature` from response chunks and feeds it back into the next request**. In a multi-turn agent loop, the agent calls `generate()` (or `generate_completions_stream()`) per turn, but the previous turn's signature is discarded — so Gemini 3.x re-derives its reasoning from scratch each turn.

**Recommendation:** Add a per-model accumulator on `GeminiBackend` (e.g. `self._thought_signatures: dict[str, str] = {}`). In `_parse_openai_response()` (or in `generate()` post-processing), extract `choices[0].message.thought_signature` (or whatever field Gemini uses in OpenAI-compat mode). In `_build_openai_body()`, if no explicit `thought_signature` was passed but `self._thought_signatures.get(model)` exists, inject it. Add tests that simulate a 2-turn conversation and assert the signature is forwarded on turn 2.

**Note on ID collision:** The R06.56 *changelog* uses "FEAT-03" to mean "Gemma `<thought>` tag parser". The audit's FEAT-03 is a different finding (the thought-signature stateful continuation gap). To avoid confusion, this could be renumbered FEAT-07 in a future audit.

**Impact:** Eliminates ~2-3x reasoning token cost on multi-turn Gemini 3.x conversations. Significant cost reduction for users running long agent loops on Gemini's free tier (5 RPM / 250K TPM / 1500 RPD limits are easily exhausted by redundant reasoning).

---

### Architecture

#### ARCH-02: No coverage measurement configured — OPEN

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `pyproject.toml:50-55` (`[project.optional-dependencies] dev`), `:92-96` (`[tool.pytest.ini_options]`) |

The project has 766 tests but no coverage measurement configured. `pytest-cov` is not declared in `dev` dependencies (lines 51-55 list only `pytest`, `black`, `ruff`) and not configured in `addopts` (line 96 is still `"-v --tb=short"`).

R06.56 added 56 new tests (710→766 per changelog TEST-02) without coverage measurement, so coverage regressions remain invisible.

**Recommendation:** Add `pytest-cov` to `dev` dependencies, configure `addopts = "-v --tb=short --cov=agentkthx --cov-report=term-missing"` in `pyproject.toml`. Optionally add `--cov-fail-under=80` to enforce a coverage floor once a baseline is established.

**Impact:** Makes coverage visible and prevents silent coverage regression. Particularly valuable for the new Gemini backend (1864 lines, 97 tests) — without coverage, it's unclear which code paths are untested.

---

#### ARCH-03: ~400 lines of 429 retry / SSE recovery duplicated between OpenRouter and Gemini — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `agentkthx/plugins/openrouter/openrouter.py:631-779` (`_make_api_request`, ~149 lines), `:1103-1196` (`_iter_sse_lines` + `_calculate_safe_max_tokens`, ~94 lines); `agentkthx/plugins/gemini/gemini.py:1262-1404` (`_make_api_request`, ~143 lines), `:1440-1510` (`_iter_sse_lines` + `_calculate_safe_max_tokens`, ~71 lines) |

The 4th cloud backend (Gemini) was added in R06.56. Both OpenRouter and Gemini have nearly-identical `_make_api_request()` methods: same retry loop structure, same `Retry-After` parsing, same exponential backoff with jitter, same 429/502/503/504 retryable classification, same 401/403 actionable error messages. The only per-backend differences are: (a) error-message prefixes ("OpenRouter" vs "Gemini"), (b) Gemini's `spend_limit_exceeded` 60s minimum wait (lines 1343-1345), (c) Gemini's 403 surfaces the Sept-2026 standard→auth-key migration message (lines 1379-1384). ~120 lines of the ~143 are duplicated.

Similarly, both backends have nearly-identical `_iter_sse_lines()` context-length 400 recovery (OpenRouter lines 1116-1148, Gemini lines 1447-1475) and identical `_calculate_safe_max_tokens()` token-parsing helpers (OpenRouter lines 1150-1196, Gemini lines 1477-1510). The only per-backend difference is the regex pattern matching the error message format (`r"maximum context length is (\d+) tokens"` vs `r"maximum context length of (\d+)"`).

A future 5th cloud backend (e.g. DeepSeek, Together AI, Mistral La Plateforme — all OpenAI-compat) would need to copy this same ~400-line retry/parse/recovery boilerplate. The `OpenAICompatibleBackend` base class was supposed to eliminate this kind of duplication (R06.55 ARCH-01 closed 534 lines of duplicated code), but the 429 retry logic was never lifted to the base.

**Recommendation:** Extract a `_make_request_with_retry(url, body, headers, *, retryable_statuses={429, 502, 503, 504}, error_prefix="...")` method on `OpenAICompatibleBackend`. Per-backend config goes into class attributes (`_MAX_429_RETRIES`, `_429_BACKOFF_BASE`, `_429_BACKOFF_CAP`, `SUPPORTED_API_MODES`). Per-backend error-message customization goes through overridable hooks (e.g. `_format_429_error(err_data, status_code) -> str`, `_format_auth_error(status_code) -> str`). This collapses ~400 lines of duplication across OpenRouter + Gemini into ~100 lines on the base class + ~30 lines per concrete backend.

Similarly, lift `_calculate_safe_max_tokens()` to the base class with a per-backend `_context_length_error_pattern` class attribute (regex pattern).

**Impact:** Adding a 5th cloud backend drops from ~400 lines of boilerplate to ~30 lines of overrides. Reduces the maintenance surface — a fix to the retry logic (e.g. honoring a new `Retry-After` variant) currently requires editing 2 files and would require 3+ after a 5th backend.

---

### Testing

#### TEST-01: No integration tests — all tests are mocked unit tests — OPEN

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (17 flat files, no `tests/integration/`) |

All 766 tests use `MagicMock`, `monkeypatch`, or source-level string inspection. No test makes a real HTTP call to a backend, no test runs a full agent loop end-to-end, and no test exercises the actual CLI. The R06.55 streaming tests (`test_streaming.py`, 359 lines) mock the backend's `generate_completions_stream` with fake SSE chunks — they verify accumulation logic but not real HTTP transport. The R06.55 `test_api_resilience.py` was rewritten to mock `urllib.request.urlopen` directly via `monkeypatch.setattr` — closer to the wire but still mocked.

The R06.56 BUG-01 and BUG-02 cli.py regressions were caught by manual VM testing per the changelog, not by tests. No test instantiates a real `GeminiBackend` and calls `get_model_runtime_context()` or runs `cmd_models` end-to-end. The bug class "new backend crashes `agentkthx models`" will recur for the 5th cloud backend unless an integration test is added.

`tests/test_gemini_backend.py` includes 3 live-API tests in `TestLiveGeminiAPI` (lines 1070-1107), but they are skipped by default via `@pytest.mark.skipif(not _RUN_LIVE, ...)` where `_RUN_LIVE = _LIVE_KEY and _LIVE_OPT_IN` (lines 47-49) requires BOTH a real-looking key (≥20 chars, not starting with `test`) AND explicit `GEMINI_RUN_LIVE_TESTS=1`. So in normal `pytest` runs, these behave as skipped unit tests, not integration tests.

**Recommendation:** Add a `tests/integration/` directory with tests that:
1. Instantiate real backend objects (with mock HTTP transport at the `urllib.request.urlopen` level — not at the backend method level)
2. Exercise the full `Agent.run()` → `backend.generate()` → response → display path
3. Test `cmd_models`, `cmd_chat`, `cmd_run` with mocked stdin/stdout (closes the BUG-01/02 regression class)
4. Verify all methods called by `cli.py` exist on each backend (catches the ARCH-01 regression class — `get_model_runtime_context()` was missing on `OpenRouterBackend` after the inheritance change)
5. Run the live-API tests in CI with a separate `pytest --live` flag and stored test API keys

**Impact:** Catches integration bugs that unit tests miss; reduces reliance on manual VM testing for regressions. Would have caught BUG-01 (GeminiBackend api_mode rejection) and BUG-02 (cmd_models cloud-provider allowlist missing GEMINI) at test time.

---

### Documentation

#### DOC-01: Gemini env vars not documented in README Configuration section — OPEN (NEW)

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Documentation |
| **File(s)** | `README.md` Configuration section (~lines 525-565); `agentkthx/config.py:84-95` (correctly declared); `agentkthx/plugins/gemini/plugin.json:18-28` (correctly declared); `agentkthx/plugins/gemini/gemini.py:19-27` (correctly documented in module docstring) |

The README's Configuration section documents env vars for OLLAMA, LLAMA_SERVER, BITNET, ZAI, ACP, TURBOQUANT, OPENROUTER (lines ~525-565) but has **no Gemini subsection**. The 6 documented env vars (`GEMINI_BASE_URL`, `GEMINI_API_KEY`, `GEMINI_DEFAULT_MODEL`, `GEMINI_FREE_ONLY`, `GEMINI_THINKING_LEVEL`, `GEMINI_SERVICE_TIER`) are correctly declared in `agentkthx/config.py:84-95` and in the plugin manifest (`agentkthx/plugins/gemini/plugin.json:18-28`), and they are listed in the R06.56 changelog, and they are referenced in `gemini.py` module docstring (lines 19-27). But the README — the primary user-facing entry point — never mentions them. The only Gemini reference in README is a table-of-contents link to `docs/GEMINI_API_TECHNICAL_REFERENCE.md` (line 40) and usage examples (lines 142-143).

Also undocumented in README: `GEMINI_MAX_429_RETRIES` (used in `gemini.py:1144` but read directly from `os.environ` rather than declared in `config.py`, so it's an implicit 7th env var).

**Note on ID collision:** The R06.56 changelog uses "DOC-01" to mean the technical reference doc, and "DOC-02" for code comments. The audit's DOC-01 is a new finding (README gap). Renumber to DOC-03 to avoid confusion in future audits.

**Recommendation:** Add a "Gemini plugin" subsection under README's Configuration section with all 7 env vars. Reference `docs/GEMINI_API_TECHNICAL_REFERENCE.md` for deep details. Mirror the format of the existing "OpenRouter Configuration" subsection. Should also mention `GOOGLE_API_KEY` as a fallback for `GEMINI_API_KEY` (mirrors Google's SDK precedence, documented in `gemini.py:87`).

**Impact:** Users discovering Gemini via the README cannot configure it without reading the source or the technical reference doc. Adding the env var table to the README closes the discovery gap.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R06.6–R06.7)** | MAINT-01 (cli.py split), MAINT-05 (generalize backend allowlists), ROB-06 (try/finally in streaming SSE), ROB-05 (stream_gen.close on KeyboardInterrupt) |
| **Short term (R06.7–R07.0)** | ARCH-03 (extract 429 retry to base class), FEAT-03 (Gemini thought-signature continuation), TEST-01 (integration tests), FEAT-01 (OpenRouter provider routing) |
| **Medium term (R07.0+)** | MAINT-04 (`_run_core` converge), FEAT-02 (`/param` matrix extensibility + Gemini param fix), DOC-01 (README Gemini env vars), ARCH-02 (coverage measurement), PERF-03 (dead think parameter) |

Guidelines for timeline assignment:
- **Near term** — High severity findings + low-risk fixes that should land in the next 1-2 releases. ROB-05 and ROB-06 are ~25 lines of code total and mirror an existing pattern.
- **Short term** — Medium severity findings addressable within 2-4 releases. ARCH-03 pairs naturally with MAINT-05 (both about backend abstraction hygiene).
- **Medium term** — Low severity findings that can be picked up during other work. MAINT-04 is risky to do before MAINT-01 lands.

---

## Architecture Strengths

1. **`OpenAICompatibleBackend` extraction (R06.55, ARCH-01)** — The intermediate base class (789 lines) provides shared JEV dispatch, body construction (`_build_openai_body`), response parsing (`_parse_openai_response`), streaming SSE (`generate_completions_stream`), and the `api_mode` property. Each concrete backend provides four abstract hooks (`_get_chat_completions_url`, `_get_auth_headers`, `_iter_sse_lines`, `_get_model_defaults`). This made the R06.53/R06.54 streaming 404 bug class structurally impossible — each backend provides its own URL, and the shared streaming method uses it. 534 lines of duplication were removed. **GeminiBackend (R06.56) is the proof point**: it shipped as a 1864-line plugin with only ~30 lines of per-backend boilerplate for the 4 abstract hooks + thinking-config routing — the rest is inherited.

2. **Zero dependencies by design — now actually true** — The entire framework runs on Python stdlib (urllib, sqlite3, argparse, json, re, ast). This eliminates dependency management, supply chain attacks, and version conflicts. ROB-04 (the `requests` dependency violation) was closed in R06.55 when OpenRouter was migrated to `urllib.request`. All 4 cloud backends now use stdlib HTTP.

3. **Plugin system with directory-scan discovery** — The `PluginManager` in `plugins/_loader.py` discovers plugins by scanning `plugins/*/plugin.json` manifests. Plugin load failures are isolated — one broken plugin doesn't prevent others from loading. R06.56's Gemini plugin follows the v0.2 spec with the `extensions` block and `compatibility.agentkthx >= 0.5.0` constraint.

4. **Defense-in-depth security with explicit threat model** — Security layers: `BLOCKED_COMMANDS` (denylist) → `DANGEROUS_FLAG_COMBOS` (context-aware flag blocks) → injection-pattern regex → `--security max|off` runtime toggle. The `safe_eval()` AST walker rejects `ast.Attribute` and `ast.Subscript` outright. No new security findings in R06.56 — the Gemini plugin inherits all sanitization via the base class.

5. **JEV mode as an API mode, not a backend** — `ApiMode.JEV` allows any chat-capable backend to produce Jev-shaped decisions via `generate_decision()`. The decision prompt, JSON parsing, and constrained-choice snapping are all in the shared base class. GeminiBackend's `_jev_call_completions()` override (line 1659) flips api_mode to `OPENAI` temporarily to avoid infinite recursion and forces `reasoning_effort="minimal"` for decisions (saves tokens on Gemini 3.x which can't fully disable thinking).

6. **`safe_eval()` AST walker (R06.41)** — Deny-by-default AST evaluation that rejects `ast.Attribute`, `ast.Subscript`, `ast.Lambda`, comprehensions, f-strings, and walrus operators. A jailbroken model can no longer achieve arbitrary code execution via the calculator tool.

7. **`DANGEROUS_FLAG_COMBOS` context-aware flag blocking (R06.41)** — Rather than blocking entire commands, the dict pairs each binary with specific dangerous flag combinations (e.g. `find` + `-exec`, `python` + `-c`, `tar` + `--use-compress-program`).

8. **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — Clean separation between user-facing enum, parser, and per-backend forwarding. R06.56 Gemini extends this with `extra_body.google.thinking_config` mutual-exclusivity enforcement.

9. **Backward compatibility as a first-class concern** — The R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility via redirect stubs (`agentnova/`, `localclaw/` packages still importable). R06.56 preserved this — no breaking changes since R06.0.

10. **Loop resilience (R06.52)** — Improved error detection, consecutive termination semantics, duplicate call blocking, pairing-safe memory pruning, and hallucinated-parameter stripping prevent the "death-spiral" scenario where the agent re-issues the same failing tool call forever.

11. **API resilience (R06.50)** — Transient API errors (rate limits, empty responses, connection blips, provider 5xx) are retried at the agent-loop level with escalating back-off. Permanent errors fail fast. Both `run()` and `run_stream()` share the semantics.

12. **Update check system (R06.51)** — Dual-source checking (PyPI + GitHub commits) with smart caching (24h positive, 6h negative) and notification placement (banner + post-run).

13. **Real streaming display (R06.53, PERF-01)** — `_generate_stream()` + `_run_core_streaming()` provide typewriter-style output with tool-call fragment accumulation across SSE chunks. R06.56 UX-01 improved the layout: reasoning panel emitted ABOVE the `AgentKthx:` prompt, 4-space indented, no duplicates.

14. **Gemma `<thought>...</thought>` tag parser (R06.56 FEAT-03 changelog)** — Stateful streaming parser that extracts inline reasoning tags from Gemma 4 output and routes them to `reasoning_content`. Handles partial tags at chunk boundaries, strips stray closing tags, flushes unclosed blocks. Parameterized via `OPENING_TAG` / `CLOSING_TAG` class constants — extensible to future models with other tag formats.

15. **Gemini free-tier data (R06.56 FEAT-02 changelog)** — 53-entry `FREE_TIER_LIMITS` table transcribed manually from Google AI Studio (Google's API does not expose pricing or free-tier info). The `GEMINI_FREE_ONLY` filter uses this table — accurate classification of all 20 free models vs 33 paid models. Catalog consistency test asserts the static `GEMINI_MODELS` catalog matches the `FREE_TIER_LIMITS` table.

16. **Soft api_mode normalization on Gemini (R06.56 BUG-01 defense)** — Rather than mirroring OpenRouter's strict `ValueError` on unsupported api_modes, `GeminiBackend.__init__` silently normalizes `OPENRE → OPENAI`. This defensive pattern means future CLI code paths that don't special-case Gemini won't break it. Asymmetry is documented and intentional.

---

## Files Changed Since R06.55 Audit (2026-09-22 → 2026-09-25)

- Version: 0.6.55 → 0.6.56
- `cli.py`: 3801 → 4079 lines (+278, MAINT-01 worsened, MAINT-05 NEW)
- `agent.py`: 2876 → 3119 lines (+243, MAINT-04 worsened — debug gap 24 → 29, UX-01 reasoning panel state in streaming only)
- `openai_compat.py`: 771 → 789 lines (+18)
- `openrouter.py`: 1068 → 1235 lines (+167, R06.55 urllib migration, ROB-04 closed; ROB-06 NEW — missing try/finally)
- `gemini.py`: 0 → 1864 lines (NEW, R06.56)
- `config.py`: +19 lines (6 GEMINI_* env vars at lines 84-95)
- `core/types.py`: +1 line (`GEMINI = "gemini"` enum value)
- `tests/test_gemini_backend.py`: 0 → 1111 lines (97 test methods, NEW)
- `tests/test_api_resilience.py`: rewritten for urllib mock transport
- `docs/GEMINI_API_TECHNICAL_REFERENCE.md`: 0 → 1553 lines (NEW)
- `docs/CHANGELOG.md`: +245 lines (R06.56 entries)
- `docs/ARCH.md`: +30 lines
- `docs/TESTS.md`: +50 lines
- `README.md`: +28 lines (Gemini usage examples, multi-cloud line, plugin list)
- Test count: 710 → 766 passed (+56, all from test_gemini_backend.py per changelog TEST-02)
- **No open findings closed since R06.55 audit.** ROB-04 was already closed in R06.55 release (the audit.md summary table marked it closed, but the brief.md text had not been regenerated).
- **5 new findings opened:** MAINT-05, ROB-06, FEAT-03, ARCH-03, DOC-01.
