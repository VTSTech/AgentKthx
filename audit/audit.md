# Improvement & Enhancement Audit
**AgentKthx v0.7.16 (R07.16 — TurboQuant handling + llama-server tool-calling)**
**Repository:** https://github.com/VTSTech/AgentKthx  
**Author:** VTSTech | **License:** MIT | **Date:** 2026-09-30  
**Commit:** 52d2f56 (R07.16) | **Test Suite:** 2051 passed / 16 skipped  
109 Findings | 34 Open | 7 Categories | SEC, ROB, MAINT, PERF, FEAT, ARCH, TEST  
Severity (open): 0 High | 14 Medium | 20 Low  
34 OPEN | 75 archived in deltas.md (68 CLOSED + 7 WONTFIX) — generate_audit_dash.py merges both for the dashboard
---
## Table of Contents
- [Executive Summary](#executive-summary)
- [Findings Summary](#findings-summary)
- [Detailed Findings](#detailed-findings)
- [Priority Matrix](#priority-matrix)
---
## Executive Summary
This re-audit covers AgentKthx at commit `52d2f56` (R07.16, PyPI 0.7.16). The codebase comprises 124 Python source files totaling ~59,115 LOC, with ~30,285 lines of tests across 70 test files — the suite passes **2051 tests / 16 skipped in ~12s**, with CI on Python 3.12/3.13 plus a parallel coverage job and a promoted-to-required lint job. The R07.00 modularization (5-mixin `Agent` composition, 23-file `cli/` package) remains stable, and the split-register layout holds: this file carries the 34 OPEN findings, while `deltas.md` archives the 75 closed/wontfix findings with the full closure timeline (R07.00 → R07.15).
R07.16 is a feature/UX release focused on making `agentkthx turbo start` + `agentkthx chat --backend turboquant` work end-to-end on CPU-only hardware: `turbo start` now auto-derives `--ctx` from the model's GGUF `context_length` metadata and `--n-predict` as `ctx // 32`, gained CPU speedup knobs (`--threads-batch`, `--batch-size`, `--ubatch-size`, `--mlock`, `--numa`) and a tri-state `--flash-attn on|off|auto` matching the TurboQuant fork v0.3.0's changed `-fa` flag; the chat side auto-derives ctx/predict via a three-source ladder (TurboState → local Ollama GGUF catalog → remote `list_models()` probe) and auto-detects tool-calling strategy (`_use_native_tools` + cached `test_tool_support` lookups switching local backends to ReAct prompting). It also ships the user-facing backend rename `llama-server` → `turboquant` (both old names and `LLAMA_SERVER_BASE_URL` kept as backward-compat aliases), ReAct parser handling of markdown-bold keywords (`**Action:**`), a defensive `shell()` timeout clamp, the Windows `readline` graceful fallback, system-prompt ReAct de-duplication, and a new llama-server tool-calling probe script. The new surface is where all four new findings live (ROB-33, ROB-34, MAINT-24, MAINT-25) — the auto-detection and catalog-probe paths are the first AgentKthx code that runs unconditionally at chat startup against user-local state files and remote HTTP endpoints.
Cumulative closure state: **68 CLOSED + 7 WONTFIX of 109 findings (75 archived, 69%)**. Closures span R07.00 → R07.15 (R07.15 closed ten across two batches; the closure history and per-release test-count deltas live in `deltas.md`). R07.16 closed none — its register movement is +4 new findings. The highest-leverage remaining closures: ROB-33 (new — the Windows `os.kill(pid, 0)` process-kill gotcha now sits on the flagship `turbo start` → `chat` workflow), ROB-31 (entitlement-aware Pollinations fallback filtering), the MAINT-23/ROB-29 retry-loop family (one `CloudBackend` primitive closes ~160 LOC of duplication across Mistral + Pollinations), MAINT-01 (extract `ChatSession` from the now-1,307-line `cmd_chat`), and TEST-01 (the integration-test tier — still the largest structural gap; every one of the 2051 suite tests remains a mocked unit test).
Process note: this pass is the first full re-audit under the split-register discipline. The six previously summary-only OPEN rows (SEC-13, ROB-15, ROB-17, ROB-18, ROB-20, ROB-25) were completed with evidence-backed detail sections; line references shifted by R07.16 were updated (SEC-09 → `config.py:82-84`, ROB-06 → `streaming.py:963-978`, MAINT-01 → 1,307 lines); and the new findings continue the established ID numbering (ROB-33/34 after archived ROB-32, MAINT-24/25 after archived MAINT-23).
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
| MAINT-01 | Medium | Maintainability | OPEN | cmd_chat is a 1,307-line single function with 25+ nested closures and no slash-command dispatcher |
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
| ROB-34 | Low | Robustness | OPEN | Windows no-readline fallback prompt "\033You:\033 " renders as "ou:" — ESC Y is a consumed 2-byte VT escape sequence |
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
| ROB-34 | Low | Robustness | `agentkthx/cli/commands/chat.py:285-292` | Windows no-readline fallback prompt "\033You:\033 " renders as "ou:" — ESC Y is a consumed 2-byte VT escape sequence |
| MAINT-24 | Low | Maintainability | `agentkthx/soul/loader.py:726-728,777-786` | _build_tool_section docstring still promises ReAct format instructions the body no longer includes |
| MAINT-25 | Low | Maintainability | `agentkthx/cli/agent_factory.py:227-290`, `agentkthx/cli/parser.py:191` | Tool-support auto-detection has no opt-out; debug hint suggests --force-react=False which argparse rejects |
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
| **File(s)** | `agentkthx/core/streaming.py:963-978` |
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
| **File(s)** | `agentkthx/plugins/turboquant/turbo.py:159-183`, `agentkthx/cli/agent_factory.py:490-497` |
New in R07.16. `_is_process_alive(pid)` uses `os.kill(pid, 0)` as an existence probe, then checks `/proc/<pid>/stat` for zombies. On Windows, `os.kill` with any signal other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` does not probe — per the `os.kill` documentation, the target is **unconditionally killed via `TerminateProcess`** with the signal value as the exit code (0 here). The `/proc` zombie check is Linux-only and its absence handler just falls through to `return True`, so Windows always takes the destructive path. Pre-R07.16 this only endangered `turbo` command flows; R07.16 placed `TurboState.load()` (which calls `_is_process_alive(state.pid)`) into `_get_local_catalog_defaults` — executed on EVERY chat startup against a non-cloud backend with a local base_url, and for `--backend ollama` too, since the state file is global (`~/.agentkthx/turbo.state`). Concrete failure: `agentkthx turbo start <model>` (server running, state file present) followed by `agentkthx chat --backend turboquant` on Windows terminates the just-started server during the liveness check. Worse: if the state file is stale and the OS reused the pid for an unrelated process, chat startup kills that process.
Recommendation: gate the probe by platform — on Windows use a non-destructive check (`ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, ...)` + `GetExitCodeProcess` comparing against `STILL_ACTIVE`, all stdlib); keep `os.kill(pid, 0)` on POSIX. Add a regression test that runs the liveness check against a live child process and asserts it still exists afterwards (fails on Windows today).
**Impact:** The R07.16 flagship workflow (`turbo start` → `chat`) silently kills its own server on Windows — the platform this release specifically targeted.
---
#### ROB-34: Windows no-readline fallback prompt `"\033You:\033 "` renders as `ou:` — `ESC Y` is a consumed 2-byte VT escape sequence
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Robustness |
| **File(s)** | `agentkthx/cli/commands/chat.py:285-292` |
New in R07.16. When `readline` is unavailable (Windows without `pyreadline3`), the REPL falls back to `_prompt = "\033You:\033 "` — a bare ESC immediately followed by `Y`. Per the VT parser state machine, ESC followed by a final byte in 0x30–0x7E is a COMPLETE 2-byte escape sequence, so `ESC Y` is consumed by conforming terminals (xterm, Windows Terminal) and never renders; the trailing `ESC + space` then pairs with the next byte (the first echoed character of user input) as an ESC-intermediate-final sequence. Verified in a terminal emulator (pyte): feeding `\033You:\033 x` renders `ou:x`. The ESC also serves no purpose — there is no CSI introducer (`ESC [`), so no color is applied in either prompt.
Recommendation: make the fallback prompt plain `"You: "`, or if color is intended use a proper CSI sequence (`"\033[36mYou:\033[0m "`). Add a prompt-rendering assertion to the Windows CI path if one exists.
**Impact:** Every Windows chat session shows a mangled prompt and can visually lose the first typed character — the exact cosmetic class the R07.16 readline fix set out to clean up.
---
### Maintainability
#### MAINT-01: `cmd_chat` is a 1,307-line single function with 25+ nested closures and no slash-command dispatcher
| Property | Value |
|----------|-------|
| **Severity** | Medium |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/commands/chat.py:1-1307` |
`cmd_chat` is a single function spanning 1,307 lines (grew from 1,199 at R07.15 — the Windows readline fallback added ~30 more lines inside the REPL loop), with 25+ nested closures (`_footer_line1`, `_footer_line2`, `_footer_text`, `_setup_footer_region`, `_teardown_footer_region`, `_update_footer`, `_position_for_input`, `_spinner_thread`, `_spinner_start`, `_spinner_stop_thread`, `_init_acp` rebind, `_build_agent` rebind, etc.). The slash-command handlers (`/help`, `/security`, `/system`, `/tools`, `/tool`, `/skills`, `/skill`, `/param`, `/models`, `/model`, `/debug`, `/clear`, `/status`) are inline `if user_input == "/X"` blocks — there's no command dispatcher. The function is too large to test in isolation; tests for chat behavior (e.g., `test_agent_mode_*.py`) use heavy monkeypatching. This was the next biggest structural debt after the R07.00 `cli.py` and `agent.py` extractions.
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
New in R07.16. The R07.16 prompt de-duplication removed the `Action:`/`Action Input:` format block from `_build_tool_section` (the body now contributes only the tool reference table + CRITICAL RULE) — correct for the two known instruction providers (the no-soul default prompt's ReAct branch, and souls like `nova-helper` that ship their own block). But the docstring (lines 726-728) STILL reads "If False, include ReAct Action/Action Input format instructions" — the contract and the behavior have drifted. There is also a residual behavioral gap: a CUSTOM soul with neither its own ReAct block nor example placeholders, run with `force_react=True` (now also set implicitly by the R07.16 tool-support auto-detection), gets a tool table with zero format instructions — pre-R07.16 the tool section supplied them.
Recommendation: update the docstring to the new contract, and have the prompt assembler detect "ReAct mode active + no `Action Input`-style instructions anywhere in the assembled prompt" and append the canonical block exactly once (the same single-source discipline the default-prompt branch already follows).
**Impact:** Docstring matches behavior, and custom-soul ReAct sessions can't silently lose the format contract the parser expects.
---
#### MAINT-25: Local-backend tool-support auto-detection has no opt-out — and its debug hint suggests `--force-react=False`, which the `store_true` argparse flag rejects
| Property | Value |
|----------|-------|
| **Severity** | Low |
| **Category** | Maintainability |
| **File(s)** | `agentkthx/cli/agent_factory.py:227-290`, `agentkthx/cli/parser.py:191` |
New in R07.16. The `_build_agent` auto-detection defaults local backends to `force_react=True` when the cached `test_tool_support` verdict is REACT or UNTESTED — reasonable for small CPU models, but there is no CLI opt-out back to native tools (the in-code comment admits "currently no opt-out beyond cache clearing", i.e. hand-deleting `~/.agentkthx/tool_support.json`). The UNTESTED debug hint tells the user to run `--force-react=False` — but `--force-react` is declared `action="store_true"` (parser.py:191), so passing `=False` raises `argument --force-react: ignored explicit argument 'False'`: the suggested remedy is an argparse error. Users on local models that DO support native function calling (or who simply prefer it) are locked out of the native path by the UNTESTED default.
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
| **Near term (R07.17–R07.18)** | ROB-33 (non-destructive Windows liveness check — unblocks the flagship turbo start → chat workflow), ROB-31 (entitlement-aware fallback filter), ROB-02 (join worker threads), ROB-06 (deterministic Windows conn release), ROB-15 (single-transaction add, with ROB-18's RLock first), SEC-09 (warn on non-HTTPS ACP), SEC-13 (require-plugin-pins mode), MAINT-03 (drop strategy 5 of `normalize_args`), MAINT-22 (streaming `_build_body()` virtual), MAINT-23 (lift retry-loop skeleton to CloudBackend — closes ROB-29 in the same move), MAINT-01 (extract `ChatSession`), TEST-01 (integration test tier), TEST-03 (add `FakeStreamingBackend`) |
| **Short term (R07.19–R07.22)** | ROB-09 (`realpath` for symlinks), ROB-20 (public `num_predict` accessor), ROB-25 (shared sampling/cap defaults), ROB-30 (narrow Pollinations catalog catch-all), ROB-34 (plain Windows fallback prompt), MAINT-24 (tool-section docstring contract + single-source ReAct block), MAINT-25 (tri-state `--force-react`), FEAT-03 (tool output JSON Schema), TEST-09 (plugin streaming-path integration test), TEST-10 (live-shape free-model contract test) |
| **Medium term (R08.00+)** | ROB-17 (truncate/warn on the single over-budget message), ROB-18 (RLock — fold into the ROB-15 work if not done sooner), FEAT-05 (plugin sandbox), FEAT-06 (streaming tool-arg deltas), FEAT-07 (conversation export/import), FEAT-08 (paid_only free-TIER filter mode), TEST-04 (rollback tests), TEST-05 (bump-version test portability), TEST-07 (update_check failure paths) |
Closed/wontfix placements from earlier revisions are archived in `deltas.md`'s closure timeline (R07.00 → R07.15).
Guidelines for timeline assignment:
- **Near term** — High severity findings and the most impactful Medium severity findings; should be fixed in the next 1-2 releases
- **Short term** — Medium severity findings addressable within 2-4 releases
- **Medium term** — Low severity findings and larger architectural changes that can be picked up during other work
---
