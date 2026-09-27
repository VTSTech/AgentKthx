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

<!-- To view a closed/wontfix finding's detail, see deltas.md. -->

---

## Priority Matrix

| Timeline | Findings |
|----------|----------|
| **Near term** | — |
| **Short term** | SEC-09, ROB-02, ROB-06, MAINT-01, MAINT-03, MAINT-08, MAINT-10, PERF-01, PERF-02, FEAT-02, FEAT-03, ARCH-02, ARCH-05, TEST-01, TEST-03, TEST-06, SEC-11, SEC-13, ROB-15, ARCH-06 |
| **Medium term** | SEC-05, ROB-09, ROB-11, ROB-12, MAINT-07, PERF-03, PERF-04, PERF-05, PERF-06, FEAT-05, FEAT-06, FEAT-07, ARCH-03, ARCH-04, TEST-02, TEST-04, TEST-05, TEST-07, TEST-08, SEC-12, SEC-14, SEC-15, SEC-16, SEC-17, ROB-17, ROB-18, ROB-19, ROB-20, ROB-22, ROB-23, ROB-24, ROB-25, ROB-26, ROB-27, MAINT-15, MAINT-18, MAINT-19 |

---

## Architecture Strengths

<!-- Preserved from the original audit.md. See git history for the full text. -->
