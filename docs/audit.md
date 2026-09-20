# Improvement & Enhancement Audit

**AgentKthx v0.4.7 (R04.7)**

**Repository:** https://github.com/VTSTech/AgentKthx
**Author:** VTSTech | **License:** MIT | **Date:** 2026-04-15
16 Findings | 6 Categories | SEC, ROB, MAINT, FEAT, ARCH, TEST

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
  - [Security](#security)
  - [Robustness](#robustness)
  - [Maintainability](#maintainability)
  - [New Features](#new-features)
  - [Architecture](#architecture)
  - [Testing](#testing)
- [Priority Matrix](#priority-matrix)
- [Architecture Strengths](#architecture-strengths)

---

## Executive Summary

AgentKthx R04.7 is a well-engineered, zero-dependency agentic framework that has evolved significantly across three rapid releases (R04.5 through R04.7). The audit reviewed ~8,500 lines of core Python code across 12 modules, the full CLI (2,143 lines), the ZAI cloud backend (814 lines), and the soul/skill subsystems. The codebase demonstrates strong architectural discipline: clean backend abstraction, comprehensive security layers (command blocklists, path whitelists, SSRF protection, sandboxed REPL), thoughtful small-model support (fuzzy matching, argument normalization, repetition detection), and robust state management (schema versioning, atomic cache writes, WAL-mode SQLite). The R04.7 release fixed a critical bug where native tool-calling-capable models were forced into text-based ReAct format by system prompt injection, restoring proper structured tool_calls for models like glm-4.5-flash. The most impactful findings involve a silently-unwired per-session todo isolation feature, hardcoded version strings that will drift on the next release, and test coverage gaps around the new chat UX features. No high-severity security vulnerabilities were found — the existing defense-in-depth model is sound for the framework's threat profile. The codebase is well-positioned for continued development, with clear extension points and a philosophy of zero external dependencies that eliminates supply chain risk entirely.

---

## Findings Summary

| ID | Severity | Category | Title |
|----|----------|----------|-------|
| SEC-01 | Medium | Security | Calculator `eval()` sandbox bypassable via `__import__` chain |
| SEC-02 | Medium | Security | `shell=True` with rejection-based command sanitization |
| SEC-03 | Low | Security | ZAI API key stored in env without format validation |
| ROB-01 | Medium | Robustness | ZAI credit-exhaustion fallback silently swaps models without user notification |
| ROB-02 | Low | Robustness | ZAI tool-rejection fallback silently downgrades to non-tool mode |
| ROB-03 | Low | Robustness | Web search depends on DuckDuckGo HTML scraping — no fallback |
| MAINT-01 | Medium | Maintainability | Per-session todo isolation infrastructure exists but is not wired up |
| MAINT-02 | Low | Maintainability | Chat footer version string hardcoded — will drift on next release |
| MAINT-03 | Low | Maintainability | ARCH.md version string stale (R04.6, should be R04.7) |
| FEAT-01 | Medium | New Feature | No streaming support for the ReAct tool-calling loop |
| FEAT-02 | Low | New Feature | Memory pruning drops messages without summarization |
| ARCH-01 | Medium | Architecture | `ZaiBackend` skips parent `OllamaBackend.__init__` — fragile inheritance |
| ARCH-02 | Low | Architecture | CLI file at 2,143 lines with mixed concerns (parsing, display, business logic) |
| ARCH-03 | Low | Architecture | Orchestrator LLM router hardcodes `get_backend("ollama")` |
| TEST-01 | Medium | Testing | No tests for R04.7 chat UX features (spinner, footer, slash commands, token tracking) |
| TEST-02 | Low | Testing | No tests for per-session todo isolation or SkillLoader cache management |

Severity levels:
- **High** — Affects correctness, security, or data integrity. Fix soon.
- Medium — Impacts maintainability, reliability, or UX. Address in planned work.
- Low — Nice-to-have improvement. Address opportunistically.

Omit categories with no findings from the detailed findings section below, but keep them in the TOC with a note like "No findings in this category."

Performance: No findings in this category.

---

## Detailed Findings

### Security

#### SEC-01: Calculator `eval()` sandbox bypassable via `__import__` chain

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/tools/builtins.py` |

The calculator tool uses Python's `eval()` with `__builtins__` set to an empty dict and a `safe_dict` containing `math`, `sqrt`, `pow`, etc. While the empty `__builtins__` blocks direct access to `import`, `exec`, `open`, and other dangerous functions, the sandbox relies entirely on the contents of `safe_dict` not being subverted. The `MAX_EXPONENT=10000` guard prevents numeric DoS via `2**9999999`, which is good. However, the fundamental approach of using `eval()` with a curated namespace remains a defense-in-depth concern — if any future addition to `safe_dict` inadvertently exposes a callable that provides attribute access to `__builtins__` or `__class__.__mro__`, the sandbox is bypassed. The current implementation is safe for the curated dictionary, but the pattern is inherently fragile for long-term maintenance.

**Impact:** A future maintainer adding a function to `safe_dict` could unknowingly break the sandbox, allowing arbitrary code execution through the calculator tool.

#### SEC-02: `shell=True` with rejection-based command sanitization

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/core/helpers.py` (`sanitize_command`), `agentkthx/tools/builtins.py` (`shell`) |

The shell tool runs commands with `subprocess.run(cmd, shell=True)` after passing them through `sanitize_command()`. The sanitizer uses a blocklist approach: it checks for dangerous patterns (pipe chains `|`, redirects `>` `>>`, `&&` `||`, backticks, `$()`, `;`, environment variable expansion, `rm -rf`, etc.) and rejects commands matching any pattern. However, `sanitize_command()` returns the ORIGINAL command string unmodified — it does not transform or escape input, only accepts or rejects it. The comment in the code explicitly acknowledges this design choice. While the blocklist is comprehensive, rejection-based approaches are inherently weaker than allowlist approaches for command execution. New shell injection techniques or encoding tricks could bypass the blocklist. The risk is mitigated by the `dangerous=True` flag (requires `--confirm` for interactive use) and audit logging to `~/.agentkthx/audit.log`.

**Impact:** An attacker who discovers a blocklist bypass could execute arbitrary shell commands through the agent's shell tool.

#### SEC-03: ZAI API key stored in env without format validation

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Security |
| **File(s)** | `agentkthx/config.py`, `agentkthx/backends/zai.py` |

The `ZAI_API_KEY` environment variable is read directly from `os.environ` and used as a Bearer token with no format validation. The key is required for the ZAI backend to function, but there's no check that it looks like a valid API key before making requests. An empty string, whitespace, or a completely wrong value would only be caught when the API returns a 401 error. This is a minor DX issue rather than a security vulnerability — the key isn't logged or exposed, and API authentication failures are handled gracefully.

**Impact:** Users with misconfigured API keys get unclear 401 errors instead of an immediate configuration error at startup.

---

### Robustness

#### ROB-01: ZAI credit-exhaustion fallback silently swaps models without user notification

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/backends/zai.py` (lines ~542-563) |

When the ZAI backend receives HTTP 429 with an error body containing "insufficient balance", "insufficient", or "no resource package" (ZAI error code 1113), it automatically retries the same request with `ZAI_FREE_FALLBACK_MODEL` (default: `glm-4.5-flash`). The fallback happens silently — only a debug-level warning is printed. The user may have intentionally selected `glm-5.1` for its reasoning capabilities and receive a response from `glm-4.5-flash` (a much smaller, less capable model) without any visible indication. This is documented in the CHANGELOG and is a deliberate design choice for resilience, but the silent nature of the swap could confuse users who notice quality degradation without understanding why. The `ZAI_FREE_ONLY` env var prevents this by blocking paid models upfront, but the default behavior allows the fallback.

**Impact:** Users may unknowingly receive responses from a less capable model when their credits are exhausted, with no clear indication in the output.

#### ROB-02: ZAI tool-rejection fallback silently downgrades to non-tool mode

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/backends/zai.py` (lines ~564-580) |

When `_generate_with_auth()` receives an error containing "does not support tools", it strips the `tools` parameter from the request body and retries. This means the model loses all tool-calling capability for that request. While this prevents a hard error (the agent gets a text-only response), it silently degrades the agent's capabilities. The agent loop may then interpret the text response as a direct answer (no tool calls found) and return it as the final answer, even though the user expected the agent to use tools. There's no mechanism to inform the agent or user that tool support was unavailable.

**Impact:** An agent using a ZAI model that doesn't support tools will silently receive text-only responses, potentially returning incomplete or inaccurate results without any error indication.

#### ROB-03: Web search depends on DuckDuckGo HTML scraping — no fallback

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/tools/builtins.py` (`web_search`) |

The web search tool scrapes `html.duckduckgo.com` and `lite.duckduckgo.com` with regex to extract search results. Any change to DuckDuckGo's HTML structure will silently break web search without any error — the regex will simply return no matches, and the tool will return an empty or minimal result set. There's no fallback search provider, no API-based search option, and no validation that the expected HTML elements exist. This is a known limitation acknowledged in the codebase documentation.

**Impact:** Any DuckDuckGo HTML layout change silently breaks web search for all agents, returning empty results without any error indication.

---

### Maintainability

#### MAINT-01: Per-session todo isolation infrastructure exists but is not wired up

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/tools/builtins.py` (lines ~896-1067) |

The R04.6 release introduced per-session todo isolation: `_todo_stores: dict[str, list[dict]]` keyed by `session_id`, with `_get_todo_store(session_id)` lazily creating stores. However, no caller actually passes a `session_id` — every todo function (`todo_add`, `todo_list`, `todo_complete`, `todo_remove`, `todo_clear`) and the `_todo_dispatch` unified handler all call `_get_todo_store()` with no argument, defaulting to `"default"`. The `Agent` class doesn't pass its `session_id` through to tool invocations. This means the isolation infrastructure is dead code — all sessions still share the `"default"` todo store, exactly as before the R04.6 change. The CHANGELOG explicitly claims "Multiple agent sessions no longer share a todo list" but this is not true in practice. The test in `test_r046_changes.py` verifies the infrastructure works when session_id is explicitly passed, but doesn't test the actual agent-integrated path (which doesn't exist).

**Impact:** The per-session todo isolation feature is marketed as working but is non-functional — a maintenance trap where developers believe sessions are isolated when they aren't.

#### MAINT-02: Chat footer version string hardcoded — will drift on next release

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` (line ~666) |

The chat mode footer bar displays the version as a hardcoded string `cyan('R04.7')` rather than deriving it dynamically from `__version__`. Other parts of the CLI (like `print_banner()`) correctly derive the version from `__version__`, but the footer was implemented with a literal string. On the next release, the footer will show R04.7 while the banner shows the new version, creating an inconsistency. The emoji constants in the footer are correctly extracted to named variables for Python 3.10 compatibility, which shows attention to detail — the version string simply wasn't included in that cleanup.

**Impact:** Footer will show stale version string after the next release, creating confusion for users who rely on the footer for version info.

#### MAINT-03: ARCH.md version string stale

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `ARCH.md` (line 7) |

The ARCH.md document header states "Version: R04.6" but the codebase is at R04.7. The ARCH.md was rewritten in R04.6 but wasn't updated for R04.7 changes (ZAI native tool calling fix, free-only mode, expanded catalog, chat UX overhaul). The specification compliance section also doesn't reflect R04.7.

**Impact:** Developers reading ARCH.md for the current state will have an inaccurate understanding of the version and recent changes.

---

### New Features

#### FEAT-01: No streaming support for the ReAct tool-calling loop

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/agent.py`, `agentkthx/backends/` |

The `--stream` flag exists in the CLI and `Agent.run(stream=True)` is accepted, but streaming is not implemented for the ReAct tool-calling path. The ZAI backend has a `generate_stream()` method that implements SSE parsing via `urllib.request`, and the Ollama backend supports streaming, but the agentic loop in `agent.py` doesn't integrate streaming into the tool-calling workflow. Each agent step waits for the full response before processing tool calls or final answers. For long-running models (especially cloud-based ZAI models with higher latency), this means users see no output for potentially seconds at a time, despite the `--stream` flag implying real-time output. The chat mode's braille spinner partially addresses this UX gap but doesn't show actual content streaming.

**Impact:** Users experience perceived latency during agent runs despite the `--stream` flag being available, particularly noticeable with cloud backends.

#### FEAT-02: Memory pruning drops messages without summarization

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/core/memory.py` |

The `Memory` class's `_prune_if_needed()` method triggers at `max_messages * 0.8` (default 40 messages) and simply drops the oldest messages to stay within budget. There's no summarization of dropped content — early conversation context is silently lost. The method is named with a threshold involving `summarization_threshold` which is misleading since no summarization actually occurs. For long conversations, this means the agent loses the original task description and early context, which can lead to repetition or task drift. This is a known limitation documented in the brief and CHANGELOG.

**Impact:** Long conversations lose early context without any summarization, potentially causing the agent to forget its original task or repeat earlier work.

---

### Architecture

#### ARCH-01: `ZaiBackend` skips parent `OllamaBackend.__init__` — fragile inheritance

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/backends/zai.py` (lines ~188-190) |

`ZaiBackend` inherits from `OllamaBackend` but calls `super(OllamaBackend, self).__init__()` to skip the parent class's `__init__` entirely, going straight to `BaseBackend.__init__()`. This is done because OllamaBackend's init sets up Ollama-specific server state (base URL validation, server running check) that doesn't apply to ZAI. While this pattern works, it's fragile: if `OllamaBackend.__init__` gains important state in a future release (e.g., shared cache initialization, default header setup, or common configuration), ZaiBackend will silently miss it. A cleaner approach would be to either extract the shared logic into `BaseBackend` (so both inherit it naturally) or use composition rather than inheritance. The current design means `ZaiBackend` is tightly coupled to the internal implementation details of `OllamaBackend.__init__`.

**Impact:** Future changes to OllamaBackend initialization could silently break ZaiBackend, with no compile-time or import-time indication of the problem.

#### ARCH-02: CLI file at 2,143 lines with mixed concerns

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `agentkthx/cli.py` |

The CLI module contains command parsing (argparse definitions), business logic (agent construction, test execution), display/formatting (colored output, footer bar, spinner animation, formatted tables), and state management (session token tracking, tool cache persistence) all in a single 2,143-line file. While this is common for CLI tools and the code is well-organized with clear function boundaries, the file has grown significantly across releases (from ~1,975 lines in R04.5 to 2,143 in R04.7). The chat UX features added in R04.7 (spinner, footer, slash commands, token tracking) account for ~130 of those new lines and could potentially be extracted into a `ChatUI` helper class.

**Impact:** The growing CLI file makes navigation harder and increases merge conflict risk when multiple features are developed concurrently.

#### ARCH-03: Orchestrator LLM router hardcodes `get_backend("ollama")`

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `agentkthx/orchestrator.py` (`_select_agent_with_llm`) |

The orchestrator's LLM-based routing mode calls `get_backend("ollama")` to create a backend for the routing model, ignoring whatever backend the user has configured. If a user is running AgentKthx with `--backend zai` or `--backend llama-server`, the router will still try to connect to Ollama for routing decisions. This limits the orchestrator's LLM routing to Ollama-only deployments.

**Impact:** LLM-based routing in the orchestrator doesn't work when Ollama isn't running, even if the user has a perfectly functional ZAI or llama-server backend.

---

### Testing

#### TEST-01: No tests for R04.7 chat UX features

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (gap) |

The R04.7 release added significant chat UX features — braille spinner (threaded animation on stderr), emoji status footer bar with ANSI escape sequences, session token tracking with 60/40 split heuristic, 4 new slash commands (/system, /tools, /model, /debug), /status crash fix, and reformatted /help — but none of these have test coverage. The spinner and footer are particularly important to test because they use `threading`, ANSI escape sequences, and terminal manipulation that can behave differently across platforms. The token tracking heuristic (60% input, 40% completion from `step.tokens_used`) should be verified for accuracy. The slash commands modify agent state at runtime (/model swaps the model, /debug toggles debug flag) and should have regression tests.

**Impact:** The R04.7 chat UX features are untested — regressions in spinner behavior, footer formatting, token counting accuracy, or slash command state changes won't be caught by CI.

#### TEST-02: No tests for per-session todo isolation or SkillLoader cache management

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_r046_changes.py`, `tests/` (gap) |

While `test_r046_changes.py` tests the `_get_todo_store()` infrastructure directly (verifying session isolation when `session_id` is explicitly passed), there's no integration test verifying that the Agent class actually passes `session_id` through to tool invocations. Since the feature is not wired up (see MAINT-01), such a test would currently fail — but that failure would be valuable as it would expose the gap. Additionally, the `SkillLoader` has cache management methods (`clear_cache`, `invalidate`, `get_cache_stats`, `is_cached`, `reload`) that are completely untested.

**Impact:** The per-session todo isolation gap won't be caught by tests (the unit test passes, integration doesn't exist), and SkillLoader cache behavior is unverified.

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (v0.4.8–v0.5.0)** | MAINT-01 (wire todo isolation or remove dead code), ARCH-01 (refactor ZaiBackend inheritance), TEST-01 (chat UX tests) |
| **Short term (v0.5.0–v0.6.0)** | SEC-01 (consider replacing eval with ast-based safe eval), ROB-01 (user-visible notification on model fallback), FEAT-01 (ReAct streaming), ARCH-03 (configurable router backend) |
| **Medium term (v0.6.0+)** | SEC-02 (allowlist-based shell execution), FEAT-02 (memory summarization), MAINT-02/MAINT-03 (dynamic version strings), ARCH-02 (extract ChatUI class), TEST-02 (integration tests) |

---

## Architecture Strengths

AgentKthx demonstrates several architectural patterns worth preserving during future development:

**Zero-dependency philosophy**: The entire framework runs on Python stdlib only — `urllib` for HTTP, `sqlite3` for persistence, `mmap` for binary parsing, `threading` for concurrency, `concurrent.futures` for parallel orchestration. This eliminates supply chain risk entirely, maximizes portability, and means the framework can be installed and used in any environment with Python 3.9+ without pip install. The `check_compatibility()` function in the skills loader was explicitly refactored from using `packaging.version` to tuple comparison (R04.6) to maintain this constraint.

**Defense-in-depth security model**: The security system layers multiple independent protections: command blocklists + injection detection for shell, path whitelist validation for file operations, SSRF pattern blocking for HTTP, sandboxed subprocess for Python REPL, response size limits (512KB files, 256KB HTTP), dangerous tool confirmation callback, and audit logging. Each layer is independently useful — even if one is bypassed, the others still provide protection. The `dangerous=True` flag with `--confirm` opt-in ensures destructive operations require explicit user consent.

**Backend abstraction with pragmatic inheritance**: The `BaseBackend` → `OllamaBackend` → `LlamaServerBackend` hierarchy provides clean extension points. BitNet as a 63-line thin wrapper over LlamaServerBackend eliminated ~170 lines of duplicated code while maintaining distinct behavioral modes (conversation budgeting, markdown sanitization). The backend registry (`_BACKENDS` dict + `get_backend()` factory + `register_backend()`) makes adding new backends trivial.

**Schema versioning for persistent state**: The `TurboState` dataclass includes a `_version` field with forward-compatible loading: version 0 (pre-versioning) loads successfully, current version loads normally, and future versions are rejected to prevent corruption. The `from_dict()` method silently ignores unknown keys for forward compatibility, and a comment placeholder marks where migration logic would go. This is a clean, production-ready pattern for state file management.

**Atomic cache persistence**: Tool support cache writes use `tempfile.mkstemp()` + `os.fsync()` + `os.replace()` for atomic writes, preventing cache corruption from interrupted writes. This pattern is applied consistently in `cli.py` for the tool support cache.

**Small-model-first design**: The framework is specifically designed for models under 1B parameters. This isn't an afterthought — it's reflected in the fuzzy tool name matching (0.4 threshold), ~100+ argument aliases in `TOOL_ARG_ALIASES`, `ast.literal_eval` fallback for single-quote dicts, repetition detection, `is_small_model()` heuristic, calculator syntax coaching in system prompts, and BitNet-specific constraints (prompt budgeting, markdown sanitization, exchange caps). The crypto-signals skill even uses a two-phase architecture where a Python script does the heavy computation and the model only reads JSON, specifically designed for 0.5B-1B models.

**Progressive disclosure at multiple levels**: Both the Soul Spec (3 levels: manifest only → +identity → +full persona) and the Skill spec (metadata → instructions → resources) implement progressive disclosure, keeping context usage minimal for small models while allowing rich configuration when context allows.
