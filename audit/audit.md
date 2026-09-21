# Improvement & Enhancement Audit

**AgentKthx v0.6.4 (R06.4)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-21  
14 Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST

> **Status (R06.41):** ROB-01 ✓ fixed, MAINT-02 ✓ fixed (env vars + paths renamed),
> ROB-03 ✓ fixed (45 → 0 failures via root-cause fixes + stale-test cleanup),
> SEC-01 ✓ fixed (new `core/safe_eval.py` AST walker; `eval()` removed from
> production source), SEC-02 ✓ accepted-risk + modestly hardened (threat model
> documented; `DANGEROUS_FLAG_COMBOS` extended; `shell=False` deferred).
> See `docs/CHANGELOG.md` R06.41 entry for full details. Remaining findings
> (ROB-02, PERF-01/02, FEAT-01/02, ARCH-01/02, TEST-01, MAINT-01) are still open.

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

AgentKthx is a 40,917-line Python framework for autonomous AI agents with zero external dependencies — built entirely on the standard library. The audit reviewed 15 core modules, 6 plugin backends, 17 built-in tools, and 11 test files (506 tests total, 245 passing). The codebase demonstrates strong security practices (command blocklist, injection detection, SSRF protection, path validation, runtime security toggle) and a clean plugin system with directory-scan discovery. The recent R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility (redirect stubs, env var preservation, filesystem path preservation).

The most impactful findings are two High-severity items: duplicate ACP plugin files creating a maintenance divergence risk, and `eval()` usage in the calculator tool that could be exploited with crafted AST payloads despite the `__builtins__` sandbox. Medium findings address bare `except:` clauses, 9 broken pre-existing tests, and the 3478-line monolithic `cli.py`. The framework's streaming architecture is notably incomplete — `stream=True` is accepted but silently ignored, resulting in a degraded UX where users see a spinner until the full response arrives. Performance findings address missing `stream_options.include_usage` on OpenRouter and unbounded token usage on free-tier models. The codebase is well-positioned for R06.5+ development with clear extension points for streaming display, provider routing, and additional sampling parameters.

---

## Findings Summary

| ID | Severity | Category | Title |
|----|----------|----------|-------|
| SEC-01 | **High** | Security | `eval()` in calculator tool with bypassable sandbox |
| SEC-02 | Medium | Security | `shell=True` subprocess execution with blocklist-only protection |
| ROB-01 | **High** | Robustness | Duplicate ACP plugin files create maintenance divergence |
| ROB-02 | Medium | Robustness | Bare `except:` clauses suppress all exceptions silently |
| ROB-03 | Medium | Robustness | 9 pre-existing test failures unreferenced in CI |
| MAINT-01 | **High** | Maintainability | `cli.py` is 3478 lines — monolithic CLI with no module splitting |
| MAINT-02 | Medium | Maintainability | `AGENTNOVA_*` env var naming inconsistent with package rename |
| PERF-01 | Medium | Performance | Streaming mode silently ignored — no real-time output |
| PERF-02 | Low | Performance | Missing `stream_options.include_usage` on OpenRouter |
| FEAT-01 | Medium | New Feature | No provider routing preferences for OpenRouter |
| FEAT-02 | Low | New Feature | `/param` matrix is hardcoded, not extensible via plugins |
| ARCH-01 | Medium | Architecture | Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals |
| ARCH-02 | Low | Architecture | No coverage measurement configured |
| TEST-01 | Medium | Testing | No integration tests — all tests are mocked unit tests |

---

## Detailed Findings

### Security

#### SEC-01: `eval()` in calculator tool with bypassable sandbox

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Security |
| **File(s)** | `agentkthx/core/math_prompts.py:220`, `agentkthx/core/helpers.py:820` |

The calculator tool uses Python's `eval()` with a restricted namespace `{"__builtins__": {}}` to evaluate mathematical expressions. While this blocks direct access to built-in functions, the sandbox is bypassable through AST manipulation. A crafted expression containing `().__class__.__bases__[0].__subclasses__()` can access arbitrary Python objects including `os.system`, `subprocess.Popen`, and `open()`. The `helpers.py:820` usage has the same pattern but evaluates model-generated expressions (not user input), making it slightly lower risk but still exploitable if the model is jailbroken.

The `math_prompts.py` usage does parse the AST first and validates function names against an allowlist (`allowed_names`), which provides better protection than a bare `eval()`. However, attribute access (`.__class__`, `.__bases__`, `.__subclasses__()`) is not blocked by the AST validation, which only checks function call names.

**Recommendation:** Replace `eval()` with a proper AST-walking evaluator that rejects attribute access nodes (`ast.Attribute`) and subscription nodes (`ast.Subscript`) entirely. Libraries like `simpleeval` or `asteval` do this correctly, but since the project has zero dependencies, a custom walker is needed. Alternatively, restrict the AST to only `ast.BinOp`, `ast.UnaryOp`, `ast.Num`, `ast.Name` nodes and reject everything else.

**Impact:** A jailbroken model or crafted prompt could achieve arbitrary code execution via the calculator tool, bypassing all other security measures.

---

#### SEC-02: `shell=True` subprocess execution with blocklist-only protection

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/tools/builtins.py:295` |
| **Status (R06.41)** | **Accepted-risk (Option A) + modestly hardened (Option B)** |

The `shell()` tool executes commands via `subprocess.run(validated_cmd, shell=True)`. Security relies on `sanitize_command()` which implements a command blocklist (`BLOCKED_COMMANDS` set) and injection pattern detection (regex for `;`, `|`, `&&`, `||`, backticks, `$()`, `${}`, `>`, `<`). With `--security off`, all checks are disabled and the model can run any command.

The blocklist approach is inherently incomplete — new dangerous commands can be added by upstream packages (e.g., `busybox rm`, `python -c "import os; os.system('rm -rf /')"`, `perl -e "system('...')"`). The injection detection regex catches common patterns but misses Unicode-based bypass, hex encoding, and nested quoting tricks. The `shell=True` flag itself is the root issue — it invokes `/bin/sh -c` which interprets the entire command string.

**Original recommendation (audit, R06.4):** Use `shell=False` with `shlex.split()` for command parsing. This prevents shell metacharacter interpretation entirely. For commands that genuinely need pipes/redirects, require the model to use explicit tool calls (e.g., `write_file` for output redirection) rather than shell syntax.

**Resolution (R06.41 — accepted-risk + modest hardening):** After review, the project maintainer determined that the audit's threat model (determined adversary crafting payloads) doesn't match AgentKthx's actual threat model (the model itself making a casual mistake). Anyone prompting the model is a user of the same system it would break — they have no incentive to bypass the blocklist, and a model that gets an initial refusal rarely pivots to a bypass technique to "achieve the goal anyway". Switching to `shell=False` would break legitimate agent workflows (pipes, redirects) for a threat that doesn't manifest in practice.

Two mitigations were applied instead:

1. **Documented the threat model** in `core/helpers.py:sanitize_command()` docstring + a new multi-paragraph comment above `BLOCKED_COMMANDS`. The docstring now explicitly states that the function is a guardrail against model mistakes, not a defense against determined prompt injection. Future contributors will understand why `shell=True` was kept.

2. **Modestly extended the blocklist** with a new `DANGEROUS_FLAG_COMBOS` dict — context-aware blocks on otherwise-safe commands paired with dangerous flags:
   - `find -exec` / `-execdir` / `-delete` (arbitrary command execution + mass deletion)
   - `xargs rm` / `mv` / `dd` / `shred` / `rmdir` (chained destructive operations)
   - `python -c` and `python3 -c` (inline code execution)
   - `perl -e` and `ruby -e` (inline interpreter)
   - `awk system(...)` (shell exec from inside awk script)
   - `tar --use-compress-program=X` and `tar -I X` (arbitrary compressor execution)
   - `cp /dev/null <file>` (file-truncation trick)
   - Also added `busybox` to `BLOCKED_COMMANDS` outright (universal multi-call binary bypasses per-binary blocks)

These cover the most common prompt-injection primitives (the gap the audit specifically called out) without breaking legitimate uses of the underlying commands. A determined adversary can still construct bypasses (Unicode normalization, base64-decoded payloads, brace expansion) — for that threat, `--security max` AND validation/sanitization of tool output before display to the model is the recommended defense-in-depth.

**Impact (revised):** With `--security max`, the blocklist + flag-combo check catches the obvious model-mistake and prompt-injection-via-tool-output payloads. With `--security off`, the model has unrestricted shell access — by design (power-user escape hatch for trusted models). Switching to `shell=False` remains a future hardening option if the threat model ever shifts toward adversarial users or untrusted content pipelines.

---

### Robustness

#### ROB-01: Duplicate ACP plugin files create maintenance divergence

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Robustness |
| **File(s)** | `agentkthx/acp_plugin.py` (2396 lines), `agentkthx/plugins/acp/acp_plugin.py` (2396 lines) |

`agentkthx/acp_plugin.py` is a near-exact copy of `agentkthx/plugins/acp/acp_plugin.py` — the only difference is import paths (relative `from .core.models import` vs absolute `from agentkthx.core.models import`). Both are 2396 lines. Similarly, `agentkthx/turbo.py` duplicates `agentkthx/plugins/turboquant/turbo.py` (693 lines each, same pattern).

This creates a maintenance hazard: if a bug is fixed in one copy but not the other, the behavior diverges silently. The plugin loader (`_loader.py`) imports from `agentkthx.plugins.acp.acp_plugin`, so the root-level `acp_plugin.py` is likely dead code. But `agentkthx/__init__.py` has a try/except import for `.acp_plugin`, suggesting it was the original location before the plugin system existed.

**Recommendation:** Delete `agentkthx/acp_plugin.py` and `agentkthx/turbo.py` (the root-level copies). If any code imports them directly, redirect those imports to the plugin versions. Run `grep -rn "from .acp_plugin\|from agentkthx.acp_plugin\|from .turbo import\|from agentkthx.turbo import"` to find all references before deleting.

**Impact:** Eliminates 3089 lines of duplicate code and the risk of silent behavioral divergence when one copy is updated but not the other.

---

#### ROB-02: Bare `except:` clauses suppress all exceptions silently

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/helpers.py:833`, `agentkthx/orchestrator.py:279` |

Two bare `except:` clauses catch and silently suppress ALL exceptions, including `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`. The `helpers.py:833` usage is in the calculator argument parsing fallback (catches parse failures from `ast.literal_eval`), and `orchestrator.py:279` is in the multi-agent orchestrator's result processing loop.

Bare `except:` is considered an anti-pattern in modern Python because it hides unexpected errors (e.g., `MemoryError`, `RecursionError`, `KeyboardInterrupt`) that should propagate. The 119 uses of `except Exception` elsewhere in the codebase are more appropriate (they don't catch `KeyboardInterrupt`/`SystemExit`).

**Recommendation:** Replace both bare `except:` with `except (ValueError, SyntaxError):` (for the `literal_eval` case) and `except Exception:` (for the orchestrator case). This preserves the intended error-suppression behavior while allowing system-level exceptions to propagate.

**Impact:** Prevents silent suppression of critical errors like `KeyboardInterrupt` (Ctrl+C during agent execution) and `MemoryError`.

---

#### ROB-03: 9 pre-existing test failures unreferenced in CI

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `tests/test_r048_changes.py` (8 failures), `tests/test_security.py` (1 failure) |

8 tests in `test_r048_changes.py` fail with `ModuleNotFoundError: No module named 'agentnova.backends.zai'` — they reference the pre-rename import path `agentnova.backends.zai` instead of `agentkthx.plugins.zai.zai`. 1 test in `test_security.py` fails on IPv6 loopback SSRF detection (`::1` not blocked). These failures existed before the R06.0 rename and haven't been fixed.

Since there's no CI pipeline configured (no `.github/workflows/` directory), these failures are only visible to developers who run `pytest` locally. The 9 failures create noise that can mask new regressions — a developer running `pytest` sees "9 failed, 245 passed" and may not investigate whether the 9 failures are pre-existing or new.

**Recommendation:** Fix the 8 `test_r048_changes.py` failures by updating import paths from `agentnova.backends.zai` to `agentkthx.plugins.zai.zai`. Fix the IPv6 loopback test by adding `::1` to the SSRF blocklist in `is_safe_url()`. Alternatively, mark them with `@pytest.mark.skip(reason="Pre-existing failure from R04.8")` and create a tracking issue.

**Impact:** Eliminates test noise and makes regression detection reliable.

---

### Maintainability

#### MAINT-01: `cli.py` is 3478 lines — monolithic CLI with no module splitting

| Property | Value |
|----------|-------|
| **Severity** | **High** |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli.py` |

`cli.py` contains the entire CLI: argument parser construction, all 13+ subcommands (run, chat, agent, models, test, turbo, skills, soul, config, sessions, plugins, update, version), all 9 slash commands (/help, /status, /skills, /param, /model, /security, /debug, /clear, /system), the chat loop, the agent display logic, the footer rendering, the spinner, the banner ASCII art, the `_build_agent()` factory, the `_load_skills_prompt()` helper, the `/param` matrix (100+ lines inline), and the reasoning display logic.

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

#### MAINT-02: `AGENTNOVA_*` env var naming inconsistent with package rename

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/config.py`, `agentkthx/cli.py` (scattered references) |

After the R06.0 rename from AgentNova to AgentKthx, all `AGENTNOVA_*` environment variables (`AGENTNOVA_BACKEND`, `AGENTNOVA_MODEL`, `AGENTNOVA_DEBUG`, `AGENTNOVA_MAX_STEPS`, etc.) were intentionally kept for backward compatibility. This means the codebase has 30+ references to `AGENTNOVA_*` env vars in a package called `agentkthx`, which is confusing for new contributors who see the package name `agentkthx` but must use `AGENTNOVA_*` env vars.

The filesystem paths (`~/.agentnova/`, `~/.cache/agentnova/`) have the same issue — they're named `agentnova` but the package is `agentkthx`.

**Recommendation:** Add `AGENTKTHX_*` aliases that take precedence over `AGENTNOVA_*`:
```python
AGENTNOVA_BACKEND = os.environ.get("AGENTKTHX_BACKEND") or os.environ.get("AGENTNOVA_BACKEND", "ollama").lower()
```
Document both in the README, with `AGENTKTHX_*` as the recommended form. Don't remove `AGENTNOVA_*` — just make `AGENTKTHX_*` the primary.

**Impact:** Reduces confusion for new users and contributors; allows gradual migration without breaking existing configs.

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

**Impact:** Dramatically improves perceived latency for cloud provider users — they see text appearing as it's generated instead of waiting for the full response.

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

**Recommendation:** Add CLI flags `--provider-order`, `--provider-ignore`, `--provider-data-collection deny` that get forwarded as the `provider` object in the request body. Also support via `/param` slash command for runtime changes.

**Impact:** Gives users control over which upstream providers serve their requests, important for privacy, cost, and reliability.

---

#### FEAT-02: `/param` matrix is hardcoded, not extensible via plugins

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | New Feature |
| **File(s)** | `agentkthx/cli.py` (inline `PARAM_MATRIX` dict in `cmd_chat`) |

The `/param` slash command's parameter support matrix is a hardcoded dict inside `cmd_chat()`. Adding a new parameter requires editing this 100+ line inline data structure in the 3478-line `cli.py`. There's no way for a plugin to register a new parameter that would show up in `/param`.

**Recommendation:** Move the `PARAM_MATRIX` to a separate `cli/params.py` module and expose a `register_param(name, spec)` API that plugins can call. The plugin's `register()` function could add backend-specific parameters (e.g., ZAI's `do_sample` parameter, OpenRouter's `min_p` sampling).

**Impact:** Makes the parameter system extensible and reduces the size of `cli.py`.

---

### Architecture

#### ARCH-01: Backend inheritance couples ZAI/OpenRouter to OllamaBackend internals

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/plugins/zai/zai.py`, `agentkthx/plugins/openrouter/openrouter.py` |

Both `ZaiBackend` and `OpenRouterBackend` inherit from `OllamaBackend`. This means:
- Any change to `OllamaBackend.generate()` affects all three backends
- The JEV dispatch (`_maybe_jev_dispatch()`, `generate_decision()`, `_jev_call_completions()`) lives on `OllamaBackend` and is inherited — but each subclass overrides `_jev_call_completions()` to route through their own auth path
- The `_maybe_jev_dispatch()` method calls `self.generate_decision()` which calls `self._jev_call_completions()` — the override chain works but is fragile (the OpenRouter JEV recursion bug in R06.2 was caused by `_jev_call_completions()` calling `self.generate()` which triggered `_maybe_jev_dispatch()` again)

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

The project has 506 tests but no coverage measurement configured. `pytest-cov` is not in dev dependencies, and there's no `--cov` flag in `addopts`. The actual coverage percentage is unknown — it could be 30% or 80%.

**Recommendation:** Add `pytest-cov` to dev dependencies, configure `addopts = "-v --tb=short --cov=agentkthx --cov-report=term-missing"` in `pyproject.toml`. Set a minimum coverage threshold (e.g., `--cov-fail-under=50`) to prevent coverage from dropping below a baseline.

**Impact:** Makes coverage visible and prevents silent coverage regression.

---

### Testing

#### TEST-01: No integration tests — all tests are mocked unit tests

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `tests/` (all 11 test files) |

All 506 tests use `MagicMock`, `monkeypatch`, or source-level string inspection (`inspect.getsource()` + `assert "X" in src`). No test makes a real HTTP call to a backend, no test runs a full agent loop end-to-end, and no test exercises the actual CLI (`agentkthx run "..."`).

This means integration bugs (like the JEV recursion, the `reasoning_content` propagation gap, the `--stream` flag missing from chat) are only caught by manual testing. The source-inspection tests (`assert "reasoning_content" in src`) are brittle — they verify that a string appears in the source code, not that the behavior works.

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
| **Near term (R06.5–R06.6)** | SEC-01 (eval sandbox bypass), ROB-01 (duplicate ACP files), MAINT-01 (cli.py split), PERF-01 (streaming display) |
| **Short term (R06.7–R07.0)** | SEC-02 (shell=True), ROB-02 (bare except), ROB-03 (broken tests), MAINT-02 (env var aliases), FEAT-01 (provider routing), ARCH-01 (backend inheritance), TEST-01 (integration tests) |
| **Medium term (R07.0+)** | PERF-02 (stream_options), FEAT-02 (param matrix extensibility), ARCH-02 (coverage measurement) |

---

## Architecture Strengths

1. **Zero dependencies by design** — The entire framework runs on Python stdlib (urllib, sqlite3, argparse, json, re, ast). This is a deliberate architectural choice that eliminates dependency management, supply chain attacks, and version conflicts. It makes the framework installable in any Python 3.9+ environment with `pip install agentkthx` and no transitive dependencies.

2. **Plugin system with directory-scan discovery** — The `PluginManager` in `plugins/_loader.py` discovers plugins by scanning `plugins/*/plugin.json` manifests, not via pip install or entry points. This is ideal for local-first users who can drop a new backend into the plugins directory without modifying `pyproject.toml` or running `pip install`. The manifest format (`plugin.json`) is clean and well-documented in `PLUGIN_SPEC.md`.

3. **Defense-in-depth security** — The security model is layered: `sanitize_command()` (blocklist + injection detection) → `validate_path()` (allowed dirs + path traversal prevention) → `is_safe_url()` (SSRF blocking). Each layer is independently testable and runtime-toggleable via `--security max|off`. The `python_repl` tool uses a separate subprocess with restricted builtins. The audit logging (`~/.agentnova/audit.log`) provides post-hoc visibility into what the agent did.

4. **JEV mode as an API mode, not a backend** — The decision to implement JEV as `ApiMode.JEV` (sibling of `openre`/`openai`) rather than as a separate `JevBackend` plugin was architecturally correct. It allows any chat-capable backend to produce Jev-shaped decisions by wrapping its existing `generate_completions()` call with a constrained decision prompt. The `_jev_call_completions()` hook pattern lets each backend route through its own auth path while sharing the JEV prompt-building and JSON-parsing logic.

5. **Backward compatibility as a first-class concern** — The R06.0 rename from AgentNova to AgentKthx was executed with full backward compatibility: redirect stubs (`agentnova/__init__.py`, `localclaw/__init__.py`) re-export everything with `DeprecationWarning`, env vars kept as `AGENTNOVA_*`, filesystem paths kept as `~/.agentnova/`. No existing user script, env var config, or SQLite session was broken by the rename.

6. **`ThinkingLevel` enum + `parse_thinking_arg()` helper** — The thinking controls (`--thinking off|auto|low|medium|high`) are cleanly separated into a user-facing enum, a parser that maps to `(think, reasoning_effort)` tuples, and per-backend forwarding logic. Adding a new thinking level or a new backend's thinking API is straightforward — just add to the enum and the backend's body construction.
