# Improvement & Enhancement Audit

**AgentKthx v0.7.08 (R07.08 — in progress)**

**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-27  
**Commit:** (working tree) | **Test Suite:** 1567 passed / 9 skipped  
57 Open Findings | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  
Severity: 0 High | 20 Medium | 37 Low  
57 OPEN (CLOSED + WONTFIX archived in deltas.md — generate_audit_dash.py merges both for the dashboard)

> **Split:** 35 CLOSED/WONTFIX findings moved to `deltas.md`. `generate_audit_dash.py` reads both `audit.md` (open) and `deltas.md` (closed/wontfix) and merges them into the full register. The dashboard shows all 92 findings.

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
- [Priority Matrix](#priority-matrix)

---

## Executive Summary

This audit covers AgentKthx at commit `51223c4` (R07.04, published to PyPI as 0.7.04). The codebase comprises ~215 Python files totaling ~57,500 lines (including 15,000 lines of tests across 43 files), and follows the R07.00 modularization that broke the prior 4,079-line `cli.py` monolith into a 23-file `cli/` package and the 3,119-line `agent.py` god-class into a 51-line five-mixin composition. The test suite passes 1290 tests / 9 skipped in ~25s, with CI running on Python 3.12/3.13 plus a parallel coverage job reporting a 42.7% baseline.

The R07.04 release closed 4 of the 9 near-term findings identified by the prior audit pass (at commit `45c7613`, pre-R07.04): **SEC-02** (High — `ast.literal_eval` type-confusion bypass), **SEC-10 + FEAT-01** (Medium — paired; tool-output wrapping via `sanitize_tool_output()`), **MAINT-02** (Medium — `CloudBackend` base class extraction). Beyond the audit closures, R07.04 also shipped the OrcaRouter plugin (10th backend, first scaffolded on top of `CloudBackend`), the `BackendType.ORCAROUTER` enum value (fixes wrong-footer-display bug), the `get_model_max_context` crash fix on cloud backends, and the terminal-free-tier-error retry-loop fix. The remaining 58 findings are tracked below; the next highest-leverage moves are MAINT-01 (extract `ChatSession` from the 1,199-line `cmd_chat`), ROB-05 (background-thread the 3-HTTPS-request update check), TEST-01 (add a thin integration test tier), SEC-03 (SSRF check via `ipaddress`), and SEC-04 (block `bash`/heredocs in `sanitize_command`).

A recurring positive pattern: the codebase's audit-tracked finding discipline (SEC/ROB/MAINT/PERF/FEAT/ARCH/TEST ID system with closure deltas) is itself working and should continue — the prior audit's three near-term findings (MAINT-06 BOM strip, TEST-02 CI workflow, ARCH-02 coverage configuration) were all closed in R07.01, and R07.04 closes four more (SEC-02, SEC-10/FEAT-01, MAINT-02), demonstrating the discipline catches and resolves real issues release-over-release.

---

---

## Findings Summary

| ID | Severity | Category | Status | Title |
|----|----------|----------|--------|-------|
| SEC-09 | Medium | Security | OPEN | ACP credentials sent as Basic Auth over HTTP by default (ACP_BASE_URL = "http://localhost:8766") |
| SEC-11 | Medium | Security | OPEN | _iter_hostname_ips does unbounded synchronous getaddrinfo — DoS amplification + no timeout |
| SEC-13 | Medium | Security | OPEN | sha256 plugin pins are opt-in — no AGENTKTHX_REQUIRE_PLUGIN_PINS enforcement mode for external plugins |
| ROB-02 | Medium | Robustness | OPEN | Orchestrator parallel mode cancels futures but does not join worker threads |
| ROB-06 | Medium | Robustness | OPEN | KeyboardInterrupt during SSE streaming may not deterministically release HTTP connection on Windows |
| ROB-15 | Medium | Robustness | OPEN | PersistentMemory.add() does two separate lock acquisitions (_write_message + _touch_session) — interleaving risk + 2× commit per message |
| MAINT-01 | Medium | Maintainability | OPEN | cmd_chat is a 1,199-line single function with 25+ nested closures and no slash-command dispatcher |
| MAINT-03 | Medium | Maintainability | OPEN | normalize_args strategy 5 (prefix/substring matching) is dangerously permissive — {"e": "..."} matches expression |
| MAINT-08 | Medium | Maintainability | OPEN | _generate_stream is 354 lines with 5-level try/except/finally nesting and inline closures |
| MAINT-10 | Medium | Maintainability | OPEN | _select_agent_with_llm builds router prompt via f-string with no escaping of agent descriptions or user task |
| PERF-01 | Medium | Performance | OPEN | Memory.sanitize_history runs on every get_messages() call — O(n²) for long histories |
| PERF-02 | Medium | Performance | OPEN | _check_compaction iterates all messages + JSON-serializes tool_calls on every step |
| FEAT-02 | Medium | New Features | OPEN | Per-tool timeout parameter and concurrent tool execution |
| FEAT-03 | Medium | New Features | OPEN | Tool output schema validation via JSON Schema |
| ARCH-02 | Medium | Architecture | OPEN | openresponses.stream_response_events is a 163-line generator mixing protocol logic with state mutation |
| ARCH-05 | Medium | Architecture | OPEN | REMAINS OPEN — R07.05/R07.06 diff does NOT touch the kwargs swallowing pattern. 22 explicit params + kwargs for 5 stashed names; typos silently ignored. |
| ARCH-06 | Medium | Architecture | OPEN | CloudBackend inherits from OpenAICompatibleBackend — tight coupling to OpenAI wire shape; non-OpenAI clouds (Anthropic Messages API) can't reuse |
| TEST-01 | Medium | Testing | OPEN | No integration tests — all 984 tests are mocked unit tests; slash-command dispatcher untested |
| TEST-03 | Medium | Testing | OPEN | FakeBackend in test_agentic_loop_subsystem.py omits generate_completions_stream — streaming callbacks unexercised |
| TEST-06 | Medium | Testing | OPEN | CI doesn't run black --check or ruff check — code style drift undetected |
| SEC-05 | Low | Security | OPEN | input() prompts in dangerous-tool confirmation don't strip ANSI escapes from tool name/args |
| SEC-12 | Low | Security | OPEN | sanitize_tool_output truncates AFTER redaction — secrets just past 8KB cutoff remain unredacted |
| SEC-14 | Low | Security | OPEN | is_transient_api_error body arg lowercased + substring-matched — user-controlled content in body could force permanent classification |
| SEC-15 | Low | Security | OPEN | CloudBackend.__init__ mutates os.environ["AGENTKTHX_API_MODE"] — process-global side effect, last-instance-wins |
| SEC-16 | Low | Security | OPEN | _extract_buy_credits_url surfaces attacker-controlled URL in user-facing error message — phishing vector |
| SEC-17 | Low | Security | OPEN | _SSRFSafeRedirectHandler triggers DNS resolution per redirect hop — unbounded redirect chain = DoS |
| ROB-09 | Low | Robustness | OPEN | validate_path uses os.path.abspath, doesn't follow symlinks — read_file("/tmp/symlink_to_etc_passwd") bypasses |
| ROB-11 | Low | Robustness | OPEN | Plugin load-failure path calls unregister() which may itself fail — leaves partial registrations |
| ROB-12 | Low | Robustness | OPEN | agent._on_step_callback = lambda ... in cmd_chat cannot be unregistered — stale closure fires after chat exits |
| ROB-17 | Low | Robustness | OPEN | Token-tier pruning can leave a single over-budget message (loop exits when len-1) — documented gap |
| ROB-18 | Low | Robustness | OPEN | threading.Lock (not RLock) — brittle if future code adds nested locked calls |
| ROB-19 | Low | Robustness | OPEN | getattr(self, "debug", False) in register_tool masks init-order bugs |
| ROB-20 | Low | Robustness | OPEN | agent.num_ctx (public) vs agent._num_predict (private) naming inconsistency in apply_model_switch |
| ROB-22 | Low | Robustness | OPEN | _iter_sse_lines has no exhaustion-raise matching non-streaming path — minor UX inconsistency |
| ROB-23 | Low | Robustness | OPEN | list_models fallback list is hardcoded — won't include new free models until code update |
| ROB-24 | Low | Robustness | OPEN | get_model_info returns default 128K entry for ANY model string — catalog no longer authoritative |
| ROB-25 | Low | Robustness | OPEN | generate() vs _generate_with_auth() signature defaults mismatch (None vs 0.7/2048) — confusing |
| ROB-26 | Low | Robustness | OPEN | sanitize_tool_output REDACT-then-TRUNCATE ordering — secrets past 8KB cutoff not redacted (dup of SEC-12) |
| ROB-27 | Low | Robustness | OPEN | _SSRFSafeRedirectHandler DNS lookup happens outside the request timeout — slow DNS = unbounded stall (dup of SEC-17) |
| MAINT-07 | Low | Maintainability | OPEN | model_family_config.detect_family uses prefix matching with overlapping families — fragile for new Qwen variants |
| MAINT-15 | Low | Maintainability | OPEN | _write_lock is per-instance, not per-DB-path — multi-instance scenarios still race |
| MAINT-18 | Low | Maintainability | OPEN | apply_model_switch return dict — verify caller actually consumes it (currently consumed by chat.py:1007 for delta-printing) |
| MAINT-19 | Low | Maintainability | OPEN | list_models cache is per-instance — class-level cache would dedupe across instances |
| PERF-03 | Low | Performance | OPEN | _iter_hostname_ips resolves every hostname synchronously on every is_safe_url call — no cache |
| PERF-04 | Low | Performance | OPEN | _estimate_tokens recomputed for every message on every add() — cache on Message dataclass |
| PERF-05 | Low | Performance | OPEN | ToolParser.parse runs all 3 parsing strategies even if first succeeds — may produce duplicate tool calls |
| PERF-06 | Low | Performance | OPEN | _fetch_json reads entire PyPI response (~100KB) before JSON parsing |
| FEAT-05 | Low | New Features | OPEN | Plugin sandboxing via restricted register() namespace + audit hooks |
| FEAT-06 | Low | New Features | OPEN | Streaming tool-call argument deltas (function_call_arguments.delta SSE events) |
| FEAT-07 | Low | New Features | OPEN | Conversation export/import to OpenResponses-format JSON |
| ARCH-03 | Low | Architecture | OPEN | agent_mode.py and orchestrator.py are only loosely coupled to the Agent class — parallel abstractions |
| ARCH-04 | Low | Architecture | OPEN | Soul loader does 5-step path resolution with repeated importlib.resources fallbacks — hard to follow |
| TEST-02 | Low | Testing | OPEN | test_security.py:test_percent2e always passes (assert not is_valid or True) — no-op test |
| TEST-04 | Low | Testing | OPEN | No test coverage for agent_mode.py rollback functionality (822 LOC, key feature) |
| TEST-05 | Low | Testing | OPEN | test_bump_version_script.py tests shell script via subprocess — fails on Windows/no-bash |
| TEST-07 | Low | Testing | OPEN | No test for update_check module's network-failure paths (URLError, socket.timeout, malformed JSON) |
| TEST-08 | Low | Testing | OPEN | No adversarial test coverage for sandboxed_repl.py — sandbox escape regressions go undetected |

---

## Detailed Findings

<!-- Open findings only. CLOSED + WONTFIX detail sections are in deltas.md. -->

### Security

#### SEC-05: `input()` prompts in dangerous-tool confirmation don't strip ANSI escapes from tool name/args

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Security |
| **File(s)** | `agentkthx/cli/parser.py:185-204`, `agentkthx/cli/commands/version.py:96` |

The `--confirm` callback prints `f"\n{yellow('⚠')}  Dangerous tool: {yellow(tool_name)}"` and `f"{dim('  ' + arg_str)}"` where `tool_name` and `arg_str` come from the model's tool call. A malicious tool name like `\x1b[2J\x1b[H` (clear screen) would inject terminal escapes into the user's terminal during confirmation. Similarly, `arg_str` containing `\x1b[?1000h` could enable mouse tracking, and `\x1b]0;evil\x07` could rewrite the terminal title.

Recommendation: Strip ANSI escapes via `re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', tool_name)` before printing, or use `repr()` for display.

**Impact:** A malicious prompt-injected tool name could manipulate the user's terminal during the confirmation dialog; low severity because the user still has to type 'y'.

---

---

#### SEC-09: ACP credentials sent as Basic Auth over HTTP by default

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Security |
| **File(s)** | `agentkthx/config.py:65-66`, `agentkthx/plugins/acp/acp_plugin.py` |

`ACP_BASE_URL = "http://localhost:8766"` is the default. `ACP_USER` and `ACP_PASS` are read from env vars (good) but sent as Basic Auth over the wire. While `localhost` is fine for development, a user who sets `ACP_BASE_URL=http://remote-host:8766` to share an ACP server across machines sends credentials in cleartext, exposing them to any network observer.

Recommendation: Warn loudly when `ACP_BASE_URL` doesn't start with `https://` and isn't `localhost`/`127.0.0.1`/`::1`. Refuse to send credentials over non-HTTPS unless `ACP_ALLOW_INSECURE_HTTP=1` is set.

**Impact:** Credentials sent in cleartext over the network if ACP server is remote; users may not realize the implication of changing `ACP_BASE_URL`.

---

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

---

#### ROB-06: KeyboardInterrupt during SSE streaming may not deterministically release HTTP connection on Windows

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Robustness |
| **File(s)** | `agentkthx/core/streaming.py:795-818` |

The KeyboardInterrupt handler calls `stream_gen.close()` to release the underlying urllib response. The comment (line 797-802) explains this is for ROB-05 (R06.57). However, on Windows, `urllib.request.urlopen` returns an `http.client.HTTPResponse` whose `.close()` may not immediately close the TCP connection — it relies on GC. On long sessions with many Ctrl+C interrupts, this can exhaust the connection pool. On Linux/macOS, `close()` calls `flush()` and `shutdown(SHUT_WR)` synchronously.

Recommendation: Explicitly call `response.fp.close()` and `response.release_conn()` if available. For urllib, use `response.close()` directly and catch `AttributeError` for older Python versions. Consider using `http.client.HTTPConnection` directly for finer-grained control.

**Impact:** Connection exhaustion on Windows under heavy Ctrl+C usage — Linux/macOS unaffected but the cross-platform promise is broken.

---

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

#### ROB-11: Plugin load-failure path calls `unregister()` which may itself fail — leaves partial registrations

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/plugins/_loader.py:896-920` |

When `_load_plugin` catches an exception during `module.register(self)` (line 864), it sets `plugin.failed = True` and calls `plugin.module.unregister(self)` (line 905) inside a try/except. If `unregister` also raises, the warning is logged but the partial state left by `register()` (e.g., backends, CLI commands, tools) is left in place — the `_purge_provides(manifest)` call (line 911) only removes manifest-declared provides, not imperative registrations via `manager.register_backend()` etc.

Recommendation: Track all `register_*` calls during `register()` execution in a per-plugin transaction, and roll them back on failure. Use a `PluginTransaction` context manager that records every `register_backend`, `register_tool`, `register_cli_command`, `register_hook` call.

**Impact:** Partially-loaded plugins leave orphan registrations in the PluginManager — a backend may be registered but its module is `None`, causing confusion.

---

---

#### ROB-12: `agent._on_step_callback = lambda ...` in `cmd_chat` cannot be unregistered

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/cli/commands/chat.py:282-284` |

`agent._on_step_callback = lambda step, tin, tout: _update_footer()` (line 284) is set unconditionally. If the Agent instance is reused after `cmd_chat` returns (e.g., in a test or a script that calls `cmd_chat` then `agent.run` directly), the lambda still fires, calling `_update_footer()` which references the closed-over `_term_size` and `_use_persistent_footer` variables from the dead `cmd_chat` stack frame.

Recommendation: Set `agent._on_step_callback = None` in the `finally:` block of `cmd_chat`. Better: replace the closure-based callback with a method on a `ChatSession` class (see MAINT-01) so the lifetime is explicit.

**Impact:** Stale closures fire after chat exits; benign in production (just writes ANSI escapes to stdout), but causes `AttributeError` in test environments.

---

---


### Maintainability

#### MAINT-01: `cmd_chat` is a 1,199-line single function with 25+ nested closures and no slash-command dispatcher

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/commands/chat.py:1-1199` |

`cmd_chat` is a single function spanning 1199 lines with 25+ nested closures (`_footer_line1`, `_footer_line2`, `_footer_text`, `_setup_footer_region`, `_teardown_footer_region`, `_update_footer`, `_position_for_input`, `_spinner_thread`, `_spinner_start`, `_spinner_stop_thread`, `_init_acp` rebind, `_build_agent` rebind, etc.). The slash-command handlers (`/help`, `/security`, `/system`, `/tools`, `/tool`, `/skills`, `/skill`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`) are inline `if user_input == "/X"` blocks — there's no command dispatcher. The function is too large to test in isolation; tests for chat behavior (e.g., `test_agent_mode_*.py`) use heavy monkeypatching. This was the next biggest structural debt after the R07.00 `cli.py` and `agent.py` extractions.

Recommendation: Extract `ChatSession` class with `handle_command(text) -> bool` dispatcher. Extract `Footer` class for the scroll-region logic. Extract `Spinner` class for the thread. Each slash command becomes a method. Target: `cmd_chat` becomes ~50 lines of orchestration; tests can construct a `ChatSession` and feed it simulated input.

**Impact:** Any change to chat UX requires touching this 1,200-line function; chat slash-command behavior is impossible to unit-test without monkeypatching.

---

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

---

#### MAINT-07: `model_family_config.detect_family` uses prefix matching with overlapping families

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/core/model_family_config.py:417-436` |

The `families` list (line 420-432) is ordered: `qwen2.5`, `qwen2`, `qwen35`, `qwen3`, `qwen`, `llama3.3`, ..., `deepseek-r1`, `deepseek`, `dolphin`, `bitnet`. The function iterates and returns the first match. A model named `qwen2.5-coder:7b` matches `qwen2.5` first (correct). But a model named `qwen35-1b` matches `qwen35` (correct). However, `qwen2.5-vl` matches `qwen2.5` which is correct, but the `FAMILY_CONFIGS` dict only has `qwen2` (not `qwen2.5`), so `get_family_config("qwen2.5")` falls through to partial matching (line 298-300) which finds `qwen2` — a 2-step indirection that's fragile.

Recommendation: Add explicit entries for `qwen2.5`, `qwen35`, `qwen3` in `FAMILY_CONFIGS`, or document the partial-match indirection. Add a test that asserts `detect_family("qwen2.5-coder")` and `get_family_config("qwen2.5-coder")` agree.

**Impact:** New Qwen variants may match the wrong family and get wrong stop tokens / temperature — silent misconfiguration.

---

---

#### MAINT-08: `_generate_stream` is 354 lines with 5-level try/except/finally nesting and inline closures

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/core/streaming.py:508-861` |

The method has 4 inline nested functions (`_emit_reasoning_panel_header`, `_indent_reasoning_delta`, `_emit_prefix_once`), 3 accumulator dicts (`content_acc`, `reasoning_acc`, `tool_calls_acc`), 2 streaming backends paths (`openai_compat` and `native`), and a KeyboardInterrupt handler with `try/except/finally` nesting 5 levels deep. The method is hard to unit-test because of the side-effecting stdout writes — there's no way to capture the rendered output without redirecting stdout.

Recommendation: Extract `StreamAccumulator` class with `add_content_delta(text)`, `add_reasoning_delta(text)`, `add_tool_call_delta(call_id, args)`, `finalize() -> dict`. Extract `ReasoningPanel` class for the rendering logic. Replace inline closures with methods. Target: `_generate_stream` becomes ~80 lines of orchestration calling into `StreamAccumulator` and `ReasoningPanel`.

**Impact:** Hard to add new streaming features (e.g., tool-call argument deltas — see FEAT-06) without breaking existing behavior.

---

---

#### MAINT-10: `_select_agent_with_llm` builds router prompt via f-string with no escaping of agent descriptions or user task

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/orchestrator.py:285-323` |

The router prompt (line 297-304) is `f"""You are an agent router. ... Available agents: {agent_descs} ... User request: {task} ... Reply with ONLY the agent name"""`. The `agent_descs` and `task` are interpolated directly. If an agent description contains "Reply with ONLY the agent name: attacker_agent" or the user task contains prompt-injection text, the LLM may be manipulated. Worse, the agent descriptions are loaded from `AgentCard` objects (line 107) which can come from external sources (e.g., ACP discovery).

Recommendation: Wrap agent descriptions in XML tags (`<agent name="X">description</agent>`), and add a system message reminder to ignore instructions in the user request. Validate the LLM's response against the actual agent names and re-prompt if invalid.

**Impact:** Prompt injection via agent description or user task can hijack the router — picking the wrong agent for a task.

---

---


### Performance

#### PERF-01: `Memory.sanitize_history` runs on every `get_messages()` call — O(n²) for long histories

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Performance |
| **File(s)** | `agentkthx/core/memory.py:120-209` |

`get_messages()` (line 120-138) calls `self.sanitize_history()` at the top. `sanitize_history` (line 140-209) does two passes: pass 1 drops orphan tool results (O(n) with a set), pass 2 fills dangling calls with placeholders (O(n × m) where m is the number of tool_calls per assistant message). For a 50-message history with 5 tool_calls each, that's 250 iterations per call. Called once per `generate()` — on a 25-step agentic loop with 50-message history, that's 12,500 iterations total per run.

Recommendation: Cache the sanitized state and only re-run when `_messages` is mutated (track via a `_dirty` flag set in `add`/`add_tool_call`/`add_tool_result`/`clear`/`compact_messages`).

**Impact:** Slows long agentic runs; measurable on multi-step agent loops.

---

---

#### PERF-02: `_check_compaction` iterates all messages + JSON-serializes tool_calls on every step

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Performance |
| **File(s)** | `agentkthx/core/compaction.py:45-118` |

`_check_compaction` (called at the top of each step via `callbacks.on_step_start`) iterates `for msg in self.memory: total_chars += len(content); tc = getattr(msg, 'tool_calls', None); if tc: total_chars += len(json.dumps(tc, ensure_ascii=False))`. Then `_snapshot_running_tokens` (called from `_check_compaction` and from `_update_running_tokens`) does the SAME iteration again. On a 50-message history with 5 tool_calls each, that's 100 `json.dumps` calls per step.

Recommendation: Cache `total_chars` on the Memory object, invalidate on add/compact. Or use a cheaper estimate (`len(content) + 50 * len(tool_calls)`).

**Impact:** Each step pays O(n × tool_calls) for token estimation — measurable on long-running chat sessions.

---

---

#### PERF-03: `web_search` uses regex to parse DuckDuckGo HTML — fragile, slow, falls back to second fetch on failure

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/tools/builtins.py:506-634` |

`web_search` (line 506-634) fetches `https://lite.duckduckgo.com/lite/?q=...` and parses the HTML with 4 regex patterns (`link_pattern`, `snippet_pattern`, `result_blocks`). The regex uses `re.DOTALL | re.IGNORECASE` and `findall`. If DuckDuckGo changes its HTML structure, the regex silently returns no results. The function also does a second fetch to `https://html.duckduckgo.com/html/?...` if the first returns nothing (line 590-613), doubling latency on failure.

Recommendation: Use a JSON API (DuckDuckGo has `https://api.duckduckgo.com/?q=...&format=json`) or a proper HTML parser (`html.parser` from stdlib). Cache results (see PERF-07).

**Impact:** Web search is slow (2 HTTP requests on failure) and fragile — HTML structure changes break it silently.

---

---

#### PERF-04: `discover(force=True)` re-scans all plugin roots — no mtime check

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/plugins/_loader.py:576-637` |

`discover(force=False)` returns the cached `_manifests` list. `discover(force=True)` re-scans all roots and re-parses every `plugin.json`. There's no mtime check — calling `discover(force=True)` after every plugin edit re-reads all manifests even if only one changed.

Recommendation: Track mtime per `plugin.json` and only re-parse changed files. Maintain a `dict[path, mtime]` and compare on `discover(force=True)`.

**Impact:** Slow plugin reload during development — minor but noticeable.

---

---

#### PERF-05: `ToolParser.parse` runs all 3 parsing strategies even if first succeeds

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/core/tool_parse.py:275-310` |

`parse(text)` (line 275-310) calls `_parse_native_json(text)`, then `_parse_react(text)`, then `_parse_xml(text)`, and extends the `calls` list with results from each. If the model emits a clean ReAct `Action: tool\nAction Input: {...}`, the JSON parser runs first and may misparse the text (e.g., if the JSON object is valid JSON, it gets parsed as a native call AND the ReAct parser also finds an Action).

Recommendation: Return early if `_parse_native_json` returns results, only fall through to ReAct/XML if JSON parsing finds nothing. Or run all three but dedupe by `(tool_name, args)` tuple.

**Impact:** Duplicate tool calls from a single model response — rare but causes confusion when it happens.

---

---

#### PERF-06: `_fetch_json` reads entire PyPI response (~100KB) before JSON parsing

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Performance |
| **File(s)** | `agentkthx/update_check.py:146-163` |

`resp.read().decode("utf-8")` reads the full PyPI JSON (which can be 100KB+) into a string, then `json.loads` parses it. PyPI's `/pypi/agentkthx/json` returns the full package metadata including all releases.

Recommendation: Use `json.load(resp)` to stream-parse, or only fetch the `info.version` field via a more targeted API (e.g., `https://pypi.org/pypi/agentkthx/json` → just read the first 4KB which contains `info.version`).

**Impact:** 100KB+ memory spike per CLI invocation — minor but wasteful for a version check.

---

---


### New Features

#### FEAT-02: Per-tool `timeout` parameter and concurrent tool execution

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | New Feature |
| **File(s)** | `agentkthx/tools/builtins.py:171, 360, 536`, `agentkthx/core/agentic_loop.py:285-298` |

Grounded in observation: `shell(command, timeout=30)` has a per-call timeout, but `http_get` (line 360) has a hard-coded `timeout=30` and `web_search` (line 536) has `timeout=15`. The agentic loop executes tool calls sequentially (`agentic_loop.py:285-298`). For multi-tool assistant messages (e.g., 3 parallel `http_get` calls to different URLs), the agent waits for each to complete serially, adding 30s × 3 = 90s.

Proposal: Add `timeout` to `ToolParam` schema so the model can specify per-call timeouts. For independent tool calls (multiple `http_get` to different URLs in one assistant message), execute them concurrently via `concurrent.futures.ThreadPoolExecutor(max_workers=4)`. Detecting call independence: calls to different tools are independent; calls to the same tool with different args are independent; calls to `shell`/`write_file`/`edit_file` are always sequential (filesystem state mutations).

**Impact:** Reduces wall-clock latency for multi-tool messages by N× for N independent calls; enables longer-running tool operations without blocking the loop.

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

---


### Architecture

#### ARCH-02: `openresponses.stream_response_events` is a 163-line generator mixing protocol logic with state mutation

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/core/openresponses.py:767-930` |

The generator creates `MessageItem`, `OutputText`, emits 9 SSE events in sequence, and mutates the `Response` object's state. It's a single function that handles: `response.queued`, `response.in_progress`, `output_item.added`, `content_part.added`, `output_text.delta` (loop), `output_text.done`, `content_part.done`, `output_item.done`, `response.completed`. The error path (line 873-882) calls `response.mark_failed` and emits a `RESPONSE_FAILED` event.

Recommendation: Extract an `SSEEventBuilder` class with methods like `emit_queued()`, `emit_in_progress()`, `emit_delta(text)`, `emit_done()`, `emit_failed(error)`. Each method handles the protocol details and state mutation for one event type.

**Impact:** Hard to test individual event transitions; hard to add new event types without modifying the 163-line generator.

---

---

#### ARCH-03: `agent_mode.py` and `orchestrator.py` are only loosely coupled to the Agent class

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `agentkthx/agent_mode.py`, `agentkthx/orchestrator.py` |

`AgentMode` (agent_mode.py:263) takes an `agent` instance and delegates to `agent.run()`. `Orchestrator` (orchestrator.py:107) creates `Agent` instances internally via `Agent(model=..., tools=...)`. Neither uses the plugin system, neither is hooked into the OpenResponses event stream. `AgentMode` has its own `TaskPlan`/`Step`/`Action` dataclasses that don't align with `StepResult`/`ToolCall` in `core/models.py`.

Recommendation: Either deprecate `AgentMode` (the chat command's `--agent` flag uses it, but the regular `chat` doesn't) or integrate it with the OpenResponses event stream by making `AgentMode` emit `Response`/`Item` events. Same for `Orchestrator`.

**Impact:** Two parallel abstractions for "multi-step agent execution" — the `Agent._run_loop_iteration` path and the `AgentMode` path; new contributors may not know which to use.

---

---

#### ARCH-04: Soul loader does 5-step path resolution with repeated `importlib.resources` fallbacks

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Architecture |
| **File(s)** | `agentkthx/soul/loader.py:50-134` |

`_resolve_soul_path` tries: (1) absolute path, (2) relative to CWD, (3) `agentkthx.__file__` parent + `souls/`, (4) `importlib.resources.files('agentkthx') / 'souls'`, (5) `agentkthx.__file__` parent + `souls/` + name, (6) `importlib.resources` again, (7) original path. The repeated `try/except (ImportError, TypeError, AttributeError)` blocks make the control flow hard to follow.

Recommendation: Consolidate into a single `importlib.resources.files('agentkthx.souls')` call with a clear fallback to filesystem path. Document the resolution algorithm in a comment.

**Impact:** Soul loading silently fails on edge cases (namespace packages, Windows pip installs); the fallback chain is hard to reason about.

---

---

#### ARCH-05: `Agent.__init__` accepts 22 explicit params + `**kwargs` for 5 more

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Architecture |
| **File(s)** | `agentkthx/core/agent_setup.py:54-87` |

The constructor signature has 22 explicit parameters (`model`, `tools`, `backend`, `max_steps`, `memory_config`, `debug`, `system_prompt`, `soul`, `soul_level`, `num_ctx`, `temperature`, `top_p`, `num_predict`, `tool_choice`, `allowed_tools`, `skills_prompt`, `retry_on_error`, `max_tool_retries`, `max_api_retries`, `truncation`, `thinking_level`, `think`, `reasoning_effort`, `show_reasoning`) plus `**kwargs` for `response_format`, `confirm_dangerous`, `persistent`, `session_id`, `memory_db`. The `**kwargs` pattern means typos in the 5 stashed kwargs are silently ignored.

Recommendation: Replace `**kwargs` with explicit parameters, or use a typed `AgentConfig` dataclass with `dataclasses.field(default=...)`. The dataclass approach makes the config serializable and version-controllable.

**Impact:** Hard to add new parameters without breaking backward compat; easy to misspell a kwarg and have it silently do nothing.

---

---


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

---

#### TEST-02: `test_security.py:test_percent2e` always passes — no-op test

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `tests/test_security.py:103-116` |

The test `test_percent2e` (line 103) asserts `assert not is_valid or True` — which always passes regardless of `is_valid`'s value. The comment (line 115-116) says "Accept either outcome; the important thing is that even if validated, read_file would fail on a non-existent path." This is a no-op test.

Recommendation: Make the test deterministic by asserting the specific expected behavior (validate_path should reject `%2e%2e` patterns after URL-decoding). Either `assert not is_valid` or `assert is_valid and "expected_reason" in reason`.

**Impact:** Path traversal via URL-encoded `..` is not actually tested; the test gives false confidence.

---

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

---

#### TEST-06: CI doesn't run `black --check` or `ruff check` — code style drift undetected

| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Testing |
| **File(s)** | `.github/workflows/ci.yml:66-69` |

The CI workflow (line 66-69) runs only `python -m pytest tests/ -q`. The `pyproject.toml` configures `[tool.black]` and `[tool.ruff]` (line 82-88) but neither is invoked in CI. The comment at line 21-22 says "We don't gate on black/ruff here yet — that's an ARCH-02-tier decision."

Recommendation: Add a `lint` job that runs `ruff check agentkthx/ tests/` and `black --check agentkthx/ tests/`. Make it a non-blocking job initially (continue-on-error: true) to surface issues without blocking PRs.

**Impact:** Code style drift goes undetected; reviewers waste time on style nits that the linter should catch.

---

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

---

#### TEST-08: No adversarial test coverage for `sandboxed_repl.py` — sandbox escape regressions go undetected

| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Testing |
| **File(s)** | `agentkthx/tools/sandboxed_repl.py` (521 LOC untested), `tests/test_sandboxed_repl.py` (does not exist) |

The `sandboxed_repl.py` module has a `test_sandbox()` function (line 464-518) that's only run via `if __name__ == "__main__"`. There's no pytest test file that verifies the sandbox blocks `import os; os.system(...)`, `import subprocess; subprocess.run(...)`, `while True: pass` (timeout), or memory-exhaustion attacks. Given SEC-01 (sandbox escape via `getattr` traversal), adversarial test coverage is critical.

Recommendation: Add `test_sandboxed_repl.py` with adversarial test cases: (a) `import os; os.system("echo pwned")` — must fail; (b) `getattr(getattr(object, "__subclasses__"), "__call__")(...)` — must fail; (c) `[0] * 10**9` (memory exhaustion) — must timeout; (d) `while True: pass` — must timeout.

**Impact:** Sandbox regressions go undetected; the sandbox's actual security guarantees are unknown.

---

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term (R07.05–R07.06)** | ~~SEC-02~~ ✓R07.04, ~~SEC-10/FEAT-01~~ ✓R07.04, ~~MAINT-02~~ ✓R07.04, ~~SEC-07~~ ✓R07.05, ~~ROB-03~~ ✓R07.05, ~~ROB-04~~ ✓R07.05, ~~MAINT-04~~ ✓R07.05, ~~MAINT-05~~ ✓R07.05, ~~MAINT-06~~ ✓R07.05, ~~SEC-03~~ ✓R07.05 (ipaddress address-level checks + redirect re-validation), ~~SEC-04~~ ✓R07.05 (shells blocked + heredoc detection), SEC-09 (warn on non-HTTPS ACP), MAINT-01 (extract `ChatSession`), ~~ROB-05~~ ⊘WONTFIX (intentional per owner), TEST-01 (integration test tier) |
| **Short term (R07.07–R07.10)** | SEC-01 (drop unsafe builtins from sandbox), ~~SEC-06~~ ✓R07.05 (sha256 pinning + perms advisory + trust-boundary docs), ROB-02 (join worker threads), ROB-09 (`realpath` for symlinks), ~~ROB-10~~ ✓R07.06 (permanent-body patterns + optional body arg), MAINT-03 (drop strategy 5 of `normalize_args`), MAINT-08 (extract `StreamAccumulator`), MAINT-10 (escape router prompt), PERF-01/PERF-02 (cache sanitized state), ARCH-01 (unify backend locations), ARCH-05 (replace `**kwargs` with dataclass), TEST-03 (add `FakeStreamingBackend`), TEST-06 (add lint job) |
| **Medium term (R08.00+)** | SEC-08 (chmod audit log), SEC-05 (strip ANSI), FEAT-02 (per-tool timeouts + concurrent execution), FEAT-03 (tool output schema), FEAT-04 (`--dry-run`), FEAT-05 (plugin sandbox), FEAT-06 (streaming args delta), FEAT-07 (conversation export), MAINT-07/MAINT-09 (consolidate regex patterns), ARCH-02 (extract `SSEEventBuilder`), ARCH-03 (integrate `AgentMode` with OpenResponses), TEST-04 (rollback tests), TEST-05 (rewrite bump-version test), TEST-07 (update_check failure paths), TEST-08 (sandbox adversarial tests) |

Guidelines for timeline assignment:
- **Near term** — High severity findings and the most impactful Medium severity findings; should be fixed in the next 1-2 releases
- **Short term** — Medium severity findings addressable within 2-4 releases
- **Medium term** — Low severity findings and larger architectural changes that can be picked up during other work

---

---
