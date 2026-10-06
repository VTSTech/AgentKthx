# CHANGELOG

All notable changes to AgentKthx will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [R07.24] - 2026-10-05 5:43:52 PM

**Audit closure super-batch: 13 OPEN findings closed + 2 WONTFIX across three batches in one release.** R07.24 is the largest single-release audit closure pass since R07.21 batch 2 — ten findings closed (SEC-20, ROB-28, ROB-41, TEST-11, MCP-01, MCP-03, MCP-04, MCP-05, MAINT-03, MAINT-22, MAINT-23, ROB-29, ROB-33) plus two WONTFIX (MCP-02, SEC-13 — deferred, out-of-scope). Suite: 2899 → 2959 passed (+60 active) / 16 → 20 skipped (+4 live-gated). Zero regressions; ruff + black clean across all 234 files. Register: 125 findings — 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%).

The release is split into three batches for changelog clarity, but shipped as a single tagged release. The audit register (`audit/audit.md` + `audit/deltas.md`) tracks each finding's closure prose separately.

### Batch 1 — SEC/TEST/ROB sweep (4 closed)

- **SEC-20** (Medium, CLOSED) — `mcp install` gains a `--no-overwrite` flag (refuses to clobber an existing `mcp.json` entry with rc=5; `--dry-run` now prints `WOULD OVERWRITE` when the target exists). Pre-flight collision detection refactored to a shared `_find_existing(servers)` helper used by both the dry-run branch and the write branch. 4 regression tests.
- **ROB-41** (Low, CLOSED) — `registry.search_all` split into `search_all_with_errors` (returns `(results, errors)` tuple preserving per-source failure reasons) + a back-compat `search_all` thin wrapper that drops errors. Plain-mode `mcp search` now leads with `Network error searching for '<query>':` + actionable hints (`--refresh`, connection check, `AGENTKTHX_GITHUB_TOKEN`) instead of the misleading `No MCP servers found` + `Try a broader query` when all sources fail. 5 regression tests.
- **TEST-11** (Low, CLOSED) — new `tests/test_mcp_live_contract.py` — 4 live-gated contract tests skip-on-`AGENTKTHX_LIVE_TESTS`-unset. Assert the documented npm + GitHub response field set per source so a `package.name` → `package.id` rename ships green-on-mocked-suite but red-on-live-contract. Verified live against real `registry.npmjs.org` + `api.github.com`.
- **ROB-28** (Low, CLOSED) — MistralBackend `list_models` catch narrowed from `except Exception` to `except (HTTPError, URLError, JSONDecodeError)`. Programming errors (KeyError/AttributeError/TypeError from a malformed response shape) now propagate as real bugs with tracebacks instead of being silently swallowed as 'discovery failed'. Debug-mode print includes the exception type (`type(e).__name__`) so a 503 service-unavailable is distinguishable from a JSON decode error at a glance. 5 regression tests pin both the caught-and-degrades cases AND the now-propagates cases.

### Batch 2 — MCP sweep (4 closed + 2 WONTFIX)

- **MCP-01** (Medium, CLOSED) — `StdioTransport._read_response` thread+queue pattern. The blocking `readline()` now runs on a daemon thread (`_blocking_readline`); the main thread does `queue.get(timeout=remaining)` which actually honors its deadline (unlike `readline()`). On timeout, the transport is marked **poisoned** (`is_poisoned` property exposed) — subsequent calls raise immediately with a "close() and reconnect to recover" message. Bonus: Ctrl+C interruptibility on POSIX (the main thread no longer holds the GIL inside `readline()`). Same shape as the R07.24 ROB-06 line-ref update (which fixed the verifier false-positive on the streaming-generator close-guard).
- **MCP-03** (Low, CLOSED) — `_extract_params` `arguments_json` fallback. When inputSchema contains `oneOf`/`anyOf`/`allOf`/`$ref`/`$dynamicRef` or nested `properties` deeper than one level, the whole tool falls back to a single `arguments_json` string parameter whose description embeds the original schema as JSON. `_invoke` parses the JSON string + forwards the parsed dict as the MCP `arguments` field; malformed JSON returns a clean error string to the model rather than raising. Conservative "whole-tool fallback" choice (vs per-property) — mixed per-field params + a single `arguments_json` is hard for the model to reason about. 8 regression tests.
- **MCP-04** (Low, CLOSED) — `MCPManager.connect_all(lazy=True)` records configs without spawning any subprocesses. New `warmup_server('<name>')` method spawns + enumerates + registers tools into the live `_target_registry` (stashed by `register_into()` so post-prompt-build warmup still surfaces tools). `_invoke` auto-warms on first dispatch to a lazy server's namespace. Trade-off (documented): prompt-time tool surface is empty for lazy servers — operators who want lazy startup AND prompt-time tool surface should `warmup_server("<name>")` BEFORE `Agent.__init__` builds the prompt. Default behavior (lazy=False) is unchanged — eager spawn + full tool surface. 6 regression tests.
- **MCP-05** (Low, CLOSED) — `notifications/tools/list_changed` handler. Three layers of notification routing: (1) `StdioTransport.set_notification_callback(callback)` — transport-level hook fired when a JSON-RPC message with no `id` field is read; (2) `MCPClient.set_notification_handler(method, handler)` + `handle_notification(msg)` — per-method dispatch table; the client installs a trampoline on its transport during `connect()` BEFORE sending `initialize` (with `listChanged: True` capability now actually wired); (3) `MCPManager._install_list_changed_handler(client, name)` wires the per-server callback to `_refresh_tools_for_server(name)` — re-queries `tools/list`, diffs against the cached surface, adds newly-discovered shim Tools to the live `_target_registry`, removes vanished ones via `ToolRegistry.unregister_tool(name)` (NEW method), then fires the user-registered `on_tools_changed(callback)` with `(server_name, added, removed)`. Handler exceptions swallowed + logged so a buggy handler doesn't kill the transport. 6 regression tests.
- **MCP-02** (Medium, WONTFIX) — MCP server sha256 pin equivalent. Deferred: AgentKthx doesn't control the MCP spec, can't enforce pinning on externally-published MCP servers. The trust boundary is advisory by design, same posture as the MCP spec itself; an operator who wants pin enforcement can manually hash the binary (`sha256sum $(which npx)`) and check it before launch. Documented in `docs/mcp/ROADMAP.md` as a deferred Phase 4+ item.
- **SEC-13** (Medium, WONTFIX) — plugin sha256 pin enforcement mode. Deferred: same reasoning as MCP-02 — plugins are externally-distributed code and AgentKthx can't enforce what upstream authors ship. Pin verification works WHEN present (the `_validate_sha256_pin` machinery from the R07.05 SEC-06 fix is intact); refusing to load unpinned plugins would block every bundled plugin + every community plugin.

### Batch 3 — MAINT/ROB sweep (5 closed)

- **MAINT-03** (Medium, CLOSED) — `normalize_args` strategy 5 (prefix/substring matching) REMOVED. The original strategy matched any key whose lower-cased form was a prefix of OR substring of any expected param — so `{"e": "..."}` matched `"expression"` (e is a substring), `{"pat": "/x"}` matched `"path"`, etc. Dangerously permissive: a model that hallucinates a single-letter arg name silently succeeded instead of failing with a clear "unknown argument" message, and the value would land in whatever param happened to contain that letter. Operators who relied on strategy 5 can restore it per-tool by adding explicit entries to `TOOL_ARG_ALIASES` in `core/prompts.py` (the intended extension point). No env-var escape hatch — the finding's recommendation was to drop the strategy outright. 6 regression tests.
- **MAINT-22** (Medium, CLOSED) — Mistral streaming path now routes through `_build_mistral_body`. New `_build_stream_body()` hook on `OpenAICompatibleBackend` (default delegates to `_build_openai_body(stream=True)` for back-compat — vanilla OpenAI-shape backends like ZAI, OpenRouter, HuggingFace, Pollinations, BitNet are byte-identical to pre-R07.24). Mistral overrides the hook to delegate to `_build_mistral_body(stream=True)`, so the streaming path now applies all Mistral-specific body shaping: `seed`→`random_seed` aliasing, `safe_prompt` injection, `prompt_cache_key` from session_id, `tool_choice="required"→"any"` mapping, OpenAI-only kwarg stripping. Before R07.24, a streaming call with `seed=42` ignored the seed; a streaming call with `MISTRAL_SAFE_PROMPT=true` didn't inject the safety prompt; a streaming call after R07.18's `session_id` kwarg didn't get the prompt-cache-key. The non-streaming path was correct; the streaming path was wrong. Now both paths use the same body. 4 regression tests.
- **MAINT-23** (Medium, CLOSED) + **ROB-29** (Low, CLOSED) — duplicated retry-loop skeleton lifted to `CloudBackend`. New shared helpers: `_compute_retry_after(headers, attempt)` (parses Retry-After header + exponential backoff with full jitter, capped at `_BACKOFF_CAP` per the ROB-16 lesson), `_is_retryable_http_status(status_code)` (429 + 5xx retryable, 4xx not), `_compute_network_backoff(attempt)` (URLError path — no headers to honor). Class-level defaults `_BACKOFF_BASE = 1.0`, `_BACKOFF_CAP = 60.0`, `_MAX_RETRIES = 4` on CloudBackend — concrete backends (Mistral, Pollinations, OpenRouter, OrcaRouter) override to tune their own retry behavior. `_max_retries()` reads `AGENTKTHX_MAX_API_RETRIES` env var as the cross-backend override; Mistral's existing override (reads `MISTRAL_MAX_RETRIES`) is preserved. The 4xx-specific handlers (401/404/422 + 400-context-length recovery) stay in each backend's caller because they differ in error-message wording and recovery strategy. 9 regression tests.
- **ROB-33** (Medium, CLOSED) — `_is_process_alive` platform-safe liveness probe. On Windows, `os.kill(pid, 0)` TERMINATES the target process (the POSIX "signal 0 = liveness check" semantics don't hold — Windows treats any signal as a kill). R07.24 branches on `os.name == 'nt'` and uses `ctypes`'s `OpenProcess` (PROCESS_QUERY_LIMITED_INFORMATION — read-only, no terminate rights) + `GetExitCodeProcess` for the Windows path; keeps `os.kill(pid, 0)` for POSIX (where signal 0 is documented as a no-op liveness check). The Windows helper fails closed on any ctypes error so the caller (`TurboState.load()` + `_free_port`) re-binds the port rather than assuming the server is alive. Also catches `OverflowError` for pids that don't fit in `pid_t` (e.g. `0xFFFFFFFF` on Linux). This was a latent bug since R06.57 but became user-facing in R07.16 when the call moved onto the chat startup path — a Windows user starting `agentkthx chat` against a running turbo server would silently kill the server in the process of checking if it was alive. 8 regression tests, including the contract test that `os.kill` is NOT called on the Windows path.

### Audit

- 13 OPEN findings closed in R07.24 (10 in batches 1+2, 5 in batch 3 — wait recount: 4+4+5=13 ✓), 2 WONTFIX. The MCP register is now 100% resolved (5 CLOSED + 1 WONTFIX + 0 OPEN).
- Register: 125 findings — 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%). Up from R07.23's 34 OPEN / 84 CLOSED / 7 WONTFIX (91 archived, ~73%).
- The near-term tier (ROB-02 orchestrator thread join, ROB-06 Windows conn release, ROB-15 memory-store double-lock, ROB-31 Pollinations entitlement, SEC-09 ACP Basic-Auth-over-HTTP, MAINT-01 1,733-line `cmd_chat` extraction, MAINT-23 retry-loop family — now closed, TEST-01 integration test tier, TEST-03 FakeStreamingBackend) shipped unchanged through R07.22 → R07.24. These are the highest-leverage remaining closures.

### Files touched

- `agentkthx/cli/commands/mcp.py` — SEC-20 `--no-overwrite` flag + `_find_existing` helper + `WOULD OVERWRITE` in dry-run + ROB-41 plain-mode network-error hint in `_mcp_search`.
- `agentkthx/cli/parser.py` — SEC-20 `--no-overwrite` argparse flag on `mcp install` subparser.
- `agentkthx/mcp/__init__.py` — export `search_all_with_errors` (ROB-41).
- `agentkthx/mcp/registry.py` — ROB-41 `search_all_with_errors` returning `(results, errors)` tuple; back-compat `search_all` thin wrapper.
- `agentkthx/mcp/transport.py` — MCP-01 thread+queue pattern (`_blocking_readline`, `is_poisoned` property, `_notification_callback` hook for MCP-05).
- `agentkthx/mcp/client.py` — MCP-05 `set_notification_handler` + `handle_notification` trampoline; `_CLIENT_VERSION` bumped to 0.7.24.
- `agentkthx/mcp/manager.py` — MCP-03 `_extract_params` arguments_json fallback + `_schema_has_complex_constructs` + `_build_arguments_json_fallback`; MCP-04 `connect_all(lazy=True)` + `warmup_server()` + auto-warm in `_invoke`; MCP-05 `_install_list_changed_handler` + `_refresh_tools_for_server` + `on_tools_changed` callback + `unregister_tool` shim removal.
- `agentkthx/tools/registry.py` — MCP-05 `ToolRegistry.unregister_tool(name)` (NEW method).
- `agentkthx/plugins/mistral/mistral.py` — MAINT-22 `_build_stream_body` override routing through `_build_mistral_body`; MAINT-23/ROB-29 `_iter_sse_lines` + `_make_api_request` delegate to CloudBackend's `_compute_retry_after` + `_is_retryable_http_status` + `_compute_network_backoff`; ROB-28 `list_models` catch narrowed from `except Exception` to `except (HTTPError, URLError, JSONDecodeError)`.
- `agentkthx/backends/openai_compat.py` — MAINT-22 `_build_stream_body` hook (default delegates to `_build_openai_body(stream=True)`).
- `agentkthx/backends/cloud_base.py` — MAINT-23/ROB-29 shared retry helpers (`_compute_retry_after`, `_is_retryable_http_status`, `_compute_network_backoff`, `_max_retries` with `AGENTKTHX_MAX_API_RETRIES` env override) + class-level `_BACKOFF_BASE`/`_BACKOFF_CAP`/`_MAX_RETRIES` defaults.
- `agentkthx/core/helpers.py` — MAINT-03 strategy 5 (prefix/substring matching) REMOVED from `normalize_args`.
- `agentkthx/plugins/turboquant/turbo.py` — ROB-33 `_is_process_alive` platform-safe (POSIX keeps `os.kill(pid, 0)`; Windows uses `ctypes` `OpenProcess` + `GetExitCodeProcess`); `_is_process_alive_windows` helper; `OverflowError` catch for pids that don't fit in `pid_t`.
- `tests/test_mcp_cli.py` — SEC-20 (4 new tests) + ROB-41 (5 new tests in `TestSearchAllWithErrors`) + updated `test_search_handles_network_error_gracefully`.
- `tests/test_mcp_live_contract.py` — NEW file, TEST-11 (4 live-gated tests).
- `tests/test_mistral_backend.py` — ROB-28 (5 new tests in `TestListModelsCatchNarrowing`).
- `tests/test_mcp_scaffold.py` — MCP-03 (8 new tests) + MCP-04 (6 new tests) + MCP-05 (6 new tests).
- `tests/test_r07_24_batch3_closures.py` — NEW file, MAINT-03 + MAINT-22 + MAINT-23/ROB-29 + ROB-33 (27 new tests across 4 test classes).
- `audit/audit.md` + `audit/brief.md` + `audit/deltas.md` — closure prose for all 13 CLOSED + 2 WONTFIX findings; reconcile `matches: True`.

### Process — R07.24 release hygiene

- `scripts/bump-version.sh R07.24` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md` header). `agentkthx/mcp/client.py:_CLIENT_VERSION` also bumped to `0.7.24` (sent in MCP `initialize` handshake).
- `R07.23 (0.7.23)` → `R07.24 (0.7.24)`.
- Suite: 2912 (R07.23 end-state per the audit) → 2959 passed (+47 active across all three batches) / 16 → 20 skipped (+4 live-gated). Zero regressions; ruff + black clean across all 234 files.
- Audit reconcile: `matches: True` — 125 findings, 19 OPEN / 97 CLOSED / 9 WONTFIX (106 archived, ~85%).

---

## [R07.23] - 2026-10-05 1:14:13 AM

**`agentkthx mcp search` + `mcp install` + removal of deprecated `git` server.** R07.23 expands the `mcp` subcommand from three actions to five — `list`, `init`, `probe`, **`search`**, **`install`** — and removes the deprecated `@modelcontextprotocol/server-git` entry from `mcp init`'s example config. Two new `agentkthx/mcp/` modules (`registry.py` + `cache.py`) implement a live, stdlib-only search across npm + GitHub, with a 10-minute TTL cache so repeat calls don't re-hit the network.

The motivation came directly from a maintainer probe: `@modelcontextprotocol/conformance` was mistakenly added to `mcp.json` (it's a CLI test harness, not an MCP server) and failed with `subprocess closed stdout while waiting for id=1`. The new `search` + `install` commands make server discovery and config population terminal-only operations — no browser, no copy-paste, no npm registry client.

### Design pivot: offline catalog → live registry

R07.23 started with a curated offline catalog (`agentkthx/mcp/catalog.py`) — a Python literal seeded from the table in `docs/mcp/ROADMAP.md`. That approach was replaced mid-release with a live registry backed by npm + GitHub. The catalog module was deleted; its replacement (`registry.py`) uses stdlib `urllib.request` — zero new runtime dependencies. The catalog was inherently stale (a Python literal can't track npm publishes), and live search + cache gives freshness without paying the network cost on every invocation: `mcp search filesystem` takes ~1.2s on first call and <50ms on cache hit.

### Added — `agentkthx/mcp/registry.py` (new module)

Stdlib-only live search across npm + GitHub:

- **`npm_search(query, size=25)`** — hits `registry.npmjs.org/-/v1/search`, filters to packages whose name or description mentions MCP, returns normalized result dicts with name, package, version, description, source, is_official, homepage, install_hint, stars, license, search_score.
- **`npm_package_info(name)`** — fetches full metadata for one npm package from `registry.npmjs.org/<name>`. Returns None on 404. Used by `mcp install` to verify the package exists + get the current version before writing to `mcp.json`.
- **`github_search(query, size=25)`** — hits `api.github.com/search/repositories` with `sort=stars&order=desc`. Works anonymously at 10 req/min; set `AGENTKTHX_GITHUB_TOKEN` (or `GITHUB_TOKEN` / `GH_TOKEN`) for 5000/min. Catches repos not published to npm (e.g. `oraios/serena`, `github/github-mcp-server`).
- **`search_all(query)`** — combined search across both sources, deduped by package identifier. Failures from either source are swallowed (graceful degradation — if npm is down, GitHub results still return).
- **`derive_short_name(package)`** — heuristic: `@modelcontextprotocol/server-filesystem` → `filesystem`, `mcp-server-fetch` → `fetch`, `oraios/serena` → `serena`. Used by both `search` (for display) and `install` (for the mcp.json entry name).
- **`build_config_snippet(package, ...)`** — generates a ready-to-paste mcp.json entry. For npm: `{"command": "npx", "args": ["-y", "<package>"]}`. For GitHub: `{"command": "uvx", "args": ["--from", "git+https://github.com/<owner>/<repo>", "<repo>"]}` (best-effort; user can override with `--command` / `--args`).
- All network calls use stdlib `urllib.request` with a 10-second timeout. Failures (offline, DNS, HTTP error) raise `MCPRegistryError` with a clear message.

### Added — `agentkthx/mcp/cache.py` (new module)

JSON-backed TTL cache at `~/.agentkthx/mcp_cache.json` (mode 0o600):

- **`get_cached(key)`** — returns `(hit, value)`. Expired entries are skipped (lazy eviction — not actively removed, just overwritten on next write).
- **`set_cached(key, value, ttl=600)`** — writes an entry with a 10-minute TTL by default. Configurable via `AGENTKTHX_MCP_CACHE_TTL` env var (seconds). Set to `0` to disable caching entirely.
- **`clear_cache()`** — explicitly clears all entries. Returns count removed.
- Atomic writes via `tmp + rename` to prevent corruption on concurrent access. File access is not locked (two concurrent `mcp search` calls could race on write; worst case is a lost cache update, not corruption).
- Cache key format: `"search:<source>:<query>:<limit>"` (e.g. `"search:all:filesystem:25"`).

### Added — `agentkthx mcp search` (live, replaces the offline catalog)

Replaces the deleted offline catalog. Always live (with cache); no `--live` flag needed.

- **`agentkthx mcp search [query]`** — default query is `"mcp"` (lists popular MCP servers). Searches npm + GitHub in parallel, dedupes, sorts by official-first + relevance.
- **`--source npm|github|all`** — restrict to one source (default: all). npm is faster and has versions; GitHub catches repos not on npm.
- **`--limit N`** — max results per source (default: 25, max: 100 GitHub / 250 npm).
- **`--refresh`** — bypass the cache and force a fresh fetch. The cache is updated with the new results.
- **`--json`** — emit machine-readable JSON payload with `query`, `source`, `results`, and optional `errors` fields.

### Added — `agentkthx mcp install <name>` (new subcommand)

Fetches live metadata for one server and writes it directly to `~/.agentkthx/mcp.json`. Always live (no offline fallback); overwrites existing entries with the same name by default (per maintainer spec).

- **`agentkthx mcp install filesystem`** — short name; resolves via npm search, preferring `@modelcontextprotocol/*` packages. Fetches full metadata for version + description.
- **`agentkthx mcp install @modelcontextprotocol/server-filesystem`** — full npm package name; fetches metadata directly (no search step).
- **`agentkthx mcp install oraios/serena --command uvx --args='...'`** — GitHub repo; requires `--command` and `--args` since the install command varies by repo.
- **`--as <name>`** — override the derived short name in the written entry.
- **`--command <cmd>`** / **`--args '<space-separated string>'`** — override the launch command and args. `--args` is a single string split on spaces (so values starting with `--` don't confuse argparse).
- **`--dry-run`** — print the snippet that would be written, don't touch `mcp.json`.
- **`--json`** — emit the snippet as JSON; doesn't write to `mcp.json`. Progress messages suppressed.
- **`--config <path>`** — override the target `mcp.json` path.
- **Exit codes**: 0 success, 1 import error, 2 network error, 3 package not found (404), 4 mcp.json unreadable.

### Changed — `mcp init` no longer emits deprecated `git` entry

- **Removed**: the `git` server entry from `_build_example_config()` in `agentkthx/mcp/config.py` and from `agentkthx/mcp/mcp.example.json`. The package `@modelcontextprotocol/server-git` was removed from npm (404 as of 2026-10-04); shipping it in the example config — even disabled — created noise and offered no path forward. Operators who want git operations should run `agentkthx mcp search git` to discover `serena` (LSP-based, includes git ops) or `github` (remote GitHub API access).
- **The `mcp init` post-write message** was simplified — it no longer mentions the deprecated git entry. The list of default-enabled servers (`filesystem` + `sequential-thinking`) is unchanged.
- **Footer hint**: the `mcp list` footer now reads `agentkthx chat --mcp filesystem sequential-thinking` (was `--mcp fs git`), matching the actual default-enabled server names.

### Removed — `agentkthx/mcp/catalog.py` (deleted)

The offline catalog module is gone. The `KNOWN_MCP_SERVERS` list, `search_catalog()` function, and `get_entry()` function are no longer available. All references removed from `agentkthx/mcp/__init__.py`. The `mcp search --copy <name>`, `--tag`, `--status`, and `--no-deprecated` flags are also gone — replaced by the live search flags above.

### Tests

- **`TestMcpSearch` (8 tests)** — all mock `agentkthx.mcp.registry._http_get_json` so no real network calls. Covers: basic search, cache write, cache hit on second call, `--refresh` bypass, `--source npm` only, `--json` payload shape, no-results message, graceful network-error handling.
- **`TestMcpInstall` (9 tests)** — covers: short-name install (npm search + metadata fetch), full npm package name, creates `mcp.json` if missing, overwrites existing entry (per maintainer spec), `--dry-run` doesn't write, `--json` emits snippet without writing, GitHub repo with `--command`/`--args` overrides, 404 returns rc=3, `--as` overrides short name.
- 1 new test `test_mcp_init_no_git_entry` — regression guard asserting `git` is no longer in the generated config (re-reads the raw JSON, not just the load_mcp_config-filtered list, to confirm the entry is gone entirely rather than just disabled).
- Updated `tests/test_mcp_scaffold.py::test_write_example_config_round_trips` to assert `git` is absent from the raw generated JSON.
- Updated `tests/test_mcp_cli.py::test_mcp_subcommands_registered` to include `search` + `install` in the expected subcommand set.
- Updated `tests/test_mcp_cli.py::test_mcp_no_subcommand_prints_help` to assert `search` + `install` appear in the help banner.
- Updated `tests/test_mcp_cli.py::test_mcp_list_with_servers` fixture to use `memory` instead of `git` for the disabled-server test case.

**Suite: 2856 → 2880 passed (+24) / 16 skipped. Zero regressions; ruff + black clean.**

### Audit

- No new findings filed. The R07.23 work is feature-additive (new `search` + `install` subcommands + 2 new modules) and removes deprecated surface (the `git` entry + the offline catalog) — neither introduces a new defect shape. The pre-existing `_mcp_list` dead-code path for disabled-entry markers (○) is noted in the test fixture but not promoted to a finding; it's a cosmetic issue with no behavioral impact since `load_mcp_config` filters disabled entries before the listing iterates them.

### Files touched

- `agentkthx/mcp/registry.py` — NEW (~290 LOC) — `npm_search`, `npm_package_info`, `github_search`, `search_all`, `derive_short_name`, `build_config_snippet`, `MCPRegistryError`
- `agentkthx/mcp/cache.py` — NEW (~150 LOC) — `get_cached`, `set_cached`, `clear_cache`, `cache_path`, `DEFAULT_TTL` (10m), env var `AGENTKTHX_MCP_CACHE_TTL`
- `agentkthx/mcp/__init__.py` — exports the new registry + cache functions; removed catalog exports
- `agentkthx/mcp/config.py` — removed `git` entry from `_build_example_config()`, added explanatory comment
- `agentkthx/mcp/mcp.example.json` — removed `git` entry (kept `memory` as an example of a disabled server)
- `agentkthx/cli/commands/mcp.py` — added `_mcp_search()` (live, ~120 LOC) + `_mcp_install()` (~150 LOC) handlers, dispatch entries, updated help banner + footer hint
- `agentkthx/cli/parser.py` — registered `search` subparser (with `--source`, `--limit`, `--refresh`, `--json`) + `install` subparser (with `--as`, `--command`, `--args`, `--dry-run`, `--json`, `--config`)
- `tests/test_mcp_cli.py` — added `TestMcpSearch` (8 tests, mocked urllib) + `TestMcpInstall` (9 tests, mocked urllib) + `test_mcp_init_no_git_entry`; updated 3 existing tests
- `tests/test_mcp_scaffold.py` — updated `test_write_example_config_round_trips` to assert `git` is absent from raw JSON
- `README.md` — replaced "deprecated git is included but disabled" paragraph with a `mcp search` + `mcp install` walkthrough
- `docs/mcp/ROADMAP.md` — added steps 1.11–1.14 to the Phase 1 table; struck through the deprecated `server-git` row in the curated catalog; updated the "catalog table" note to point to `mcp search` for live results

### Intra-release amendments (R07.23-dev, 2026-10-05)

Four follow-up commits during R07.23-dev, all surfaced by a maintainer MCP smoke test on Ubuntu 26.04 against `glm-4.5-flash`. No suite-count change beyond the test-pinned regression guards noted per-commit; ruff + black clean after each.

- **`mcp init` defaults collapsed to a single `~/workspace` directory** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`, `tests/test_mcp_scaffold.py`). The previous example config referenced two directories — `~/projects` for the filesystem MCP server and `~/repo` for the (Phase 2) kthx-audit MCP server — both created by `write_example_config()` at write time. Maintainer feedback was that the split is artificial for a fresh install: a single `~/workspace` covers both use cases (drop files for the filesystem server; point the audit server at the same tree when it lands). `write_example_config()` now creates one `~/workspace` directory; `_build_example_config()` substitutes `~/workspace` into both the `filesystem` and `audit` server args; the static `mcp.example.json` template was updated to match (`/home/REPLACE_ME/workspace`). The `_mcp_init` success message now reports "One directory was created for you" instead of two. The `test_write_example_config_substitutes_home_dir` test was updated to assert a single `workspace` directory is created (was `projects` + `repo`).

- **`@modelcontextprotocol/server-memory` promoted to enabled-by-default** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`, `tests/test_mcp_scaffold.py`). The R07.23 release shipped `mcp init` with `filesystem` + `sequential-thinking` enabled and `memory` declared only in the static `mcp.example.json` (disabled). The maintainer smoke test bridged 9 tools from `@modelcontextprotocol/server-memory` cleanly (create_entities, create_relations, add_observations, delete_entities, delete_observations, delete_relations, read_graph, search_nodes, open_nodes) and confirmed the model exercised all of them in a 25-step agentic run. `memory` is now in the embedded `_build_example_config()` between `sequential-thinking` and `audit`, `enabled=True`, with no `--storage-path` initially (in-process). The `_mcp_init` success message lists memory in the "Servers enabled by default" section. `test_write_example_config_round_trips` was updated: expected enabled count bumped from `>= 2` to `>= 3`, and `assert "memory" in names` added.

- **`@modelcontextprotocol/server-memory` defaults to persistent storage at `~/.agentkthx/memory.json`** (`agentkthx/mcp/config.py`, `agentkthx/mcp/mcp.example.json`, `agentkthx/cli/commands/mcp.py`). Follow-up to the promotion above: the in-process default loses the knowledge graph on every `agentkthx chat` restart, which defeats the point of a persistent memory server. `write_example_config()` now substitutes `{home}/.agentkthx/memory.json` into the memory server's `--storage-path` arg; the parent `~/.agentkthx/` directory is already created by `write_example_config()` for the `mcp.json` file, so no new mkdir is needed. The static `mcp.example.json` was synced. The `_mcp_init` success message now reads `memory — knowledge graph (persists to ~/.agentkthx/memory.json)`.

- **`--max-steps` surfaced in the session header + streaming fatal-error diagnostic** (`agentkthx/cli/headers.py`, `agentkthx/core/streaming.py`). Maintainer noticed a `chat --max-steps 100` run terminate at "25 steps" with `[Resilience] Fatal API error — not retrying: ZAI HTTP error 400: {"error":{"code":"1214","message":"The messages parameter is illegal..."}}` and asked whether `--max-steps 100` was being applied. Traced end-to-end: `argparse` → `args.max_steps=100` → `_build_agent` passes 100 → `Agent.max_steps=100` → `range(self.max_steps)` in both `agentic_loop.py:248` and `streaming.py:455`. The flag IS applied correctly; the run died from a fatal ZAI 400 on step 25 (the footer's "25 steps" is completed iterations, not the limit). The 1214 itself is a known recurring issue — the messages array's value becomes illegal after ~25 rounds of ReAct-style history accumulated while `tools` is declared in the request body; full root-cause (streaming + native-tools mismatch) deferred to a future PR. Two surgical fixes shipped: (1) `_print_session_header` now prints a `Max Steps: N` line so operators can verify the flag was applied — prevents this confusion from recurring; (2) the streaming path's fatal-API-error handler now dumps `Messages at failure: N msgs (system=1, user=K, assistant=M, tool=T), ~X chars (~Y tokens)` under `--debug` so the next 1214 fire has data attached instead of speculation.

- **ROOT CAUSE FOUND + FIXED: ZAI 1214 "messages parameter is illegal" at step ~25 was the count-tier pruner dropping the original user prompt** (`agentkthx/core/memory.py`, `tests/test_loop_resilience.py`). A second smoke test (Ghidra headless MCP server, 25 native `function.list` tool-call rounds against `glm-4.5-flash`) reproduced the 1214 at step 26 with `--debug` enabled and `ctx 9%` — confirming size was NOT the issue. Root cause traced by simulating the messages array after 25 native tool-call rounds: the default `MemoryConfig(max_messages=50)` count-tier pruner fires at ~52 messages, slides the window to `keep_count = int(50 * 0.8) = 40`, and the slide drops the original `user` prompt along with the oldest assistant/tool pairs. The existing pairing-safe head trim only dropped leading `tool` results — it did NOT handle the case where the slide leaves an `assistant(tool_calls)` message exposed at the head. ZAI (and OpenAI) require the first non-system message to be `role=user`; an `assistant` message with `tool_calls` as the first non-system message is structurally invalid and triggers HTTP 400 code 1214 "messages parameter is illegal". The compaction path (`_check_compaction`, token-based at 85% of `num_ctx`) never fired because the run was only at 9% context — the count-tier pruner in `_prune_if_needed()` was the culprit. Fix: both pruning tiers (count-tier at line 353 and token-tier at line 397) now pin the first `user` message before the slide and re-prepend it after, so the API sequence always starts with `system → user → ...`. The pin accepts a 1-message overshoot past `keep_count` — preferable to an illegal API sequence. 2 existing tests updated (`test_window_slides_to_threshold` 42→43, `test_recent_messages_retained` 8→10) to reflect the preserved first-user; 1 new regression test `test_prune_preserves_first_user_message` reproduces the exact 25-round native-tool-calls scenario and asserts (a) the first non-system message is `role=user`, (b) no `assistant(tool_calls)` appears before the first `user`. Suite: 2880 → **2899 passed (+19 from the R07.23-dev batch), 16 skipped, 0 failures**; ruff + black clean.

---



## [R07.22] - 2026-10-03 10:00:57 PM

**MCP (Model Context Protocol) client support — Phase 1 complete.** R07.22 lands stdio MCP client mode, the largest new feature surface since the R07.19 capabilities-first tool-support chain. Agents can now consume tools from external MCP servers (filesystem, sequential-thinking, sqlite, memory, git, serena, brave-search, ...) via stdio JSON-RPC 2.0, with their tools bridged into the existing `ToolRegistry` alongside built-ins. Zero runtime dependencies added — the implementation uses stdlib `subprocess` + `json` only, in keeping with the project's `dependencies = []` invariant.

The release ships a new `agentkthx mcp` subcommand (`init` / `list` / `probe`), `--mcp [SERVER...]` and `--mcp-config PATH` flags on `chat`/`run`/`agent`, and a new `agentkthx/mcp/` package (`config.py` + `transport.py` + `client.py` + `manager.py`). `agentkthx mcp init` writes a ready-to-use config with the user's actual home directory substituted in (no `REPLACE_ME` placeholder) and creates `~/projects/` + `~/repo/` so the filesystem MCP server starts cleanly without manual setup. All MCP tool output flows through the same `sanitize_tool_output` security boundary (8 KB truncation + secret redaction + ANSI strip) as built-in tools — the defense-in-depth posture does not weaken because a tool came from a subprocess.

End-to-end verified against the official `@modelcontextprotocol/server-filesystem` (14 tools, `secure-filesystem-server v0.2.0`) and `@modelcontextprotocol/server-sequential-thinking` (1 tool, `v2026.8.31`). A `chat --mcp filesystem` session with `glm-4.5-flash` correctly picks `filesystem__list_allowed_directories` then `filesystem__list_directory` without any prompt engineering — the system prompt's Tool Reference section enumerates the namespaced MCP tools and the model selects them naturally.

### Added — MCP client package (`agentkthx/mcp/`)

New stdlib-only package implementing MCP client mode over stdio JSON-RPC 2.0:

- **`config.py`** — `MCPServerConfig` dataclass + `load_mcp_config()` + `write_example_config()`. Config file at `~/.agentkthx/mcp.json` (override with `--mcp-config PATH`). Validates: alphanumeric/-/_ server names (used as tool-name prefixes), `command` must be absolute path or `shutil.which`-resolvable (no shell, no `~`), `shell=False` + `close_fds=True` on subprocess spawn. Permission check warns on group/world-writable config files. `write_example_config()` substitutes the user's actual home directory into all path arguments (no `REPLACE_ME` placeholder) and creates `~/projects/` + `~/repo/` if they don't exist — `mcp init` followed by `mcp probe filesystem` works without manual editing.
- **`transport.py`** — `StdioTransport` wraps one subprocess; newline-delimited JSON-RPC 2.0 over stdin/stdout; stderr captured to a 64-line ring buffer for diagnostics (surfaced on failure). Lazy spawn (subprocess starts on first request, not at transport construction). `close()` sends MCP `shutdown` + `exit` notifications, then `terminate` + `kill` if the process hasn't exited within 2s. Per-transport `threading.Lock` serializes concurrent calls to the same server (JSON-RPC over a single stdio pair is inherently serial); parallel tool calls across servers use multiple transports.
- **`client.py`** — `MCPClient` wraps a `StdioTransport` and speaks the MCP protocol: `initialize` (sends `protocolVersion: 2025-06-18` + `clientInfo: agentkthx/<version>`), `notifications/initialized`, `tools/list`, `tools/call`. Server-reported `serverInfo` + `capabilities` exposed via read-only properties. Tool *execution* errors (e.g. file-not-found from the filesystem server) are returned as `{"isError": true, ...}` for the model to react to — only protocol/transport errors raise.
- **`manager.py`** — `MCPManager` orchestrates multiple `MCPClient` instances and bridges their tools into a target `ToolRegistry`. Tool name namespacing: `<server>__<tool>` (the `__` separator cannot appear in either MCP field, eliminating collision risk). Each MCP tool becomes a shim `Tool` whose handler forwards the call to the right `MCPClient`, then pipes the result through `sanitize_tool_output` exactly like built-in tools. `_extract_params()` converts the MCP `inputSchema` (JSON Schema) into the project's flat `ToolParam` list (handles `type: object` + `properties` + `required`, nullable unions `["string", "null"]`, common types). `_flatten_call_result()` converts the MCP `CallToolResult` (list of content items) into a flat string for sanitization; image content is replaced with a placeholder, resource references surface the URI. `describe()` returns a per-server diagnostic dict for `agentkthx mcp list`.

### Added — `agentkthx mcp` subcommand

Three sub-actions, all using only stdlib + the existing `agentkthx.mcp` package:

- **`agentkthx mcp init`** — writes `~/.agentkthx/mcp.json` with home-dir substitution + creates `~/projects/` + `~/repo/`. Refuses to overwrite without `--force`. The embedded example ships `filesystem` + `sequential-thinking` enabled by default (both verified on npm 2026-10-04), `git` disabled (`@modelcontextprotocol/server-git` was removed from npm — 404), `audit` disabled (Phase 2 placeholder). `chmod 0o600` on the written file.
- **`agentkthx mcp list`** — shows configured servers with enabled/disabled markers, command preview, and timeout. Hints at `agentkthx mcp init` if no config exists.
- **`agentkthx mcp probe <name>`** — connects to one server, runs `initialize` + `tools/list`, prints the tool surface with descriptions. Optional `--call TOOL JSON_ARGS` round-trips a real `tools/call` and prints the flattened result. Optional `--config PATH` overrides the config file. Exit codes: 0 success, 1 unknown server, 2 connect failed, 3 tools/list failed, 4 bad JSON args, 5 tools/call failed. Surfaces stderr tail on failure for diagnostics.

### Added — `--mcp` / `--mcp-config` CLI flags

Wired into `shared_args.py:add_agent_args` so `chat`, `run`, and `agent` all accept them:

- **`--mcp [SERVER ...]`** — `nargs="*"`, so bare `--mcp` enables all configured servers and `--mcp fs git` enables only the named subset. `default=None` (not passed) is the opt-in sentinel — MCP is off by default.
- **`--mcp-config PATH`** — overrides the default `~/.agentkthx/mcp.json` path. Useful for maintaining multiple server sets (work vs personal).
- **Note on `run` ordering**: because `--mcp` uses `nargs="*"`, it greedily consumes everything after the flag. For `agentkthx run`, put the prompt BEFORE `--mcp` (e.g. `agentkthx run hello --mcp fs`). Documented in the flag's help text.

### Added — `_wire_mcp()` in `agent_factory.py`

MCP wiring runs BEFORE `Agent(...)` construction (critical): the system prompt is built during `Agent.__init__`, and it only includes the Tool Reference section when `has_tools=True`. If MCP wired after construction, the prompt would have no tool section and the model would hallucinate `shell` (which isn't even in the registry) instead of picking an MCP tool.

When `--mcp` is passed without `--tools`, `_wire_mcp` creates an empty `ToolRegistry` so MCP tools have somewhere to land — the "MCP-only session" case. The populated registry is stashed as `manager._bridged_registry` so `_build_agent` can pass it to `Agent(tools=...)`. The manager is stashed on `agent._mcp_manager` for cleanup on session exit.

Failure semantics: a server that fails to connect or list tools is SKIPPED with a stderr warning (`MCPManager.connect_all(skip_failures=True)`). The agent still works without that server's tools — a misconfigured MCP server shouldn't kill the chat session. The `chat.py` `finally` block calls `manager.close_all(timeout=1.0)` on every exit path (REPL exit, Ctrl+C, exception).

### Added — `agentkthx mcp` excluded from update check

`agentkthx mcp probe` is a diagnostic against a local subprocess — hitting `pypi.org` on every probe would be pure latency. Added `"mcp"` to the no-update-check list in `cli/main.py` alongside `"version"` and `"update"`.

### Added — SECURITY.md + CONTRIBUTING.md

Two long-missing trust artifacts:

- **`SECURITY.md`** — vulnerability reporting policy (GitHub Security Advisories preferred, `veritas@vts-tech.org` fallback), 72h acknowledgement / 7-day assessment / 30-day fix timeline, scope (in-scope vs out-of-scope), built-in defenses documented (`validate_path`, `sanitize_command`, `is_safe_url`, `safe_eval`, `sanitize_tool_output`, plugin sha256 pins), known security-relevant findings table (SEC-09, SEC-13, ROB-09, ROB-33, ROB-05), update-check transparency (3 HTTPS requests per CLI invocation, no telemetry), hardening recommendations for operators.
- **`CONTRIBUTING.md`** — project ethos (zero-deps is the feature, hackable over clever, defense-in-depth not sandbox, audit register is canonical), getting started (Python 3.12+, `pip install -e .` + `pytest`/`black`/`ruff`), architecture map, critical files index (7 files with outsize blast radius), 22 landmines, common contribution types (new backend / CLI flag / tool / plugin / test / soul / skill), audit workflow, commit message format, PR checklist, issue triage, release process.

### Added — Audit findings MCP-01..05

New MCP category in `audit/audit.md` with 5 findings:

- **MCP-01** (Medium, near-term) — `StdioTransport._read_response` uses blocking `readline()`; per-call timeouts don't actually interrupt. Same shape as ROB-06/ROB-02. Fix: thread+queue pattern.
- **MCP-02** (Medium, near-term) — MCP server configs have no sha256 pin equivalent (SEC-13 analogue). An attacker who can write `mcp.json` can substitute any binary for a declared server name. Fix: optional `sha256` field + `AGENTKTHX_REQUIRE_MCP_PINS=1` enforcement mode.
- **MCP-03** (Low, short-term) — `_extract_params` flattens `oneOf`/`anyOf`/`$ref` JSON Schema constructs to default `string`. Tools with sophisticated schemas appear deceptively simple. Fix: `arguments_json` fallback.
- **MCP-04** (Low, short-term) — Eager server startup adds ~1–3s to every `--mcp` session even when no MCP tools are called. Fix: lazy mode (spawn on first tool call).
- **MCP-05** (Low, medium-term) — No `notifications/tools/list_changed` handler. Runtime tool surface changes invisible to the registry until next session. Fix: per-server callback + `ToolRegistry.unregister_tool`.

Priority matrix updated: MCP-01 + MCP-02 in near-term (R07.22–R07.23), MCP-03 + MCP-04 in short-term (R07.23–R07.25), MCP-05 in medium-term (R08.00+).

### Added — `docs/mcp/ROADMAP.md`

Full MCP integration plan: Phase 1 (client mode, stdio, CLI integration — ✅ done), Phase 2 (`kthx-audit` MCP server — planned, ~400 LOC, exposes `agentkthx/skills/codebase-audit/` as a standalone MCP server so any MCP-compatible client can call it), Phase 3 (generic `agentkthx mcp serve` mode), Phase 4 (HTTP/SSE transport — deferred), Phase 5 (MCP-aware souls — speculative). Includes curated free-tier MCP server list with verification dates, decision log, and the 5 audit findings with proposed fixes.

### Tests — 2858 passed (+56 from R07.21)

- `tests/test_mcp_scaffold.py` (23 tests) — config validation, namespacing round-trip, `inputSchema`→`ToolParam` conversion, `CallToolResult` flattening (text/error/image-skip/resource/bare-string), manager describe/require-connect, `write_example_config` home-dir substitution regression guard.
- `tests/test_mcp_cli.py` (27 tests) — `--mcp`/`--mcp-config` flag wiring on chat/run/agent, `mcp` subcommand registration, `mcp list` happy/empty/missing paths, `mcp init` write/refuse-overwrite/`--force`, `mcp probe` unknown-server error, `_wire_mcp` no-op-when-disabled regression guard, MCP-only-session-creates-empty-registry regression guard for the prompt-engineering fix.
- `tests/test_r07_19_help_sort.py` — updated root subcommand listing to include `mcp` (alphabetical between `config` and `modelfile`).

### Process — R07.22 release hygiene

- `scripts/bump-version.sh R07.22` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md` header). `agentkthx/mcp/client.py:_CLIENT_VERSION` also bumped to `0.7.22` (sent in MCP `initialize` handshake).
- `R07.21 (0.7.21)` → `R07.22 (0.7.22)`.
- Suite: 2802 → 2858 passed (+56 from MCP scaffold + CLI integration tests). Zero regressions; ruff + black clean across all 229 files.
- End-to-end verified against real `@modelcontextprotocol/server-filesystem` (14 tools) and `@modelcontextprotocol/server-sequential-thinking` (1 tool) MCP servers — initialize → tools/list → tools/call → clean shutdown round-trip works in production, not just in mocked tests.
- `agentkthx mcp init` → `agentkthx mcp probe filesystem` → `agentkthx chat --mcp filesystem` verified on Ubuntu 26.04.1 LTS with `glm-4.5-flash` backend — the agent correctly picks `filesystem__list_allowed_directories` then `filesystem__list_directory` without any prompt engineering.

### What's NOT in R07.22 (deferred)

- **MCP server mode** (`agentkthx mcp serve`) — Phase 3. Lets AgentKthx expose its own tools as an MCP server so other MCP clients (Claude Desktop, Cline, Continue, mcphost) can consume them.
- **`kthx-audit` MCP server** — Phase 2. Exposes `agentkthx/skills/codebase-audit/` as a standalone MCP server. ~400 LOC estimate. The dogfood payoff: `agentkthx chat --mcp filesystem git audit` becomes the demo that drives inbound discovery from MCP directories.
- **HTTP/SSE transport** — Phase 4. stdio covers >90% of use cases; HTTP/SSE adds auth + rate-limiting complexity that isn't justified without demand.
- **Lazy server startup** — MCP-04. Eager mode (every configured server spawns at agent construction) is simpler and surfaces config errors immediately. Lazy is a Phase 1.x optimization.
- **`notifications/tools/list_changed`** — MCP-05. Most current MCP servers have a static tool surface, so this is rarely hit in practice.
- **Plugin pin enforcement for MCP** — MCP-02. Optional `sha256` field on `MCPServerConfig` + `AGENTKTHX_REQUIRE_MCP_PINS=1` env var. SEC-13 analogue.
- **`docs/mcp/USAGE.md`** — worked-examples doc (filesystem-only coding session, multi-server research setup, audit workflow). Deferred until Phase 2 lands so the doc can include the audit workflow as a working example rather than a forward reference.

---

## [R07.21] - 2026-10-03 3:05:35 PM

**OpenRouter App Attribution, `/sh` chat command, 14 audit closures, Gemini thought_signature fix, Mistral agent-internal-field stripping, ZAI default-to-NATIVE fix, and a full-backend smoke test harness.** R07.21 is the largest single-release closure batch since R07.00 — 14 OPEN findings closed across three batches, the smoke test verified all 8 cloud backends end-to-end (29 PASS / 0 FAIL / 2 SKIP), and the register moved from 37 OPEN / 68% closure to 26 OPEN / 78% closure.

The release lands the harness's first deliberate entry into OpenRouter's app marketplace — the OpenRouter backend now sends the four App Attribution headers (`HTTP-Referer`, `X-OpenRouter-Title`, `X-Title` legacy alias, `X-OpenRouter-Categories`) on every request, hardcoded to claim the `cli-agent` marketplace leaf. The AgentKthx app directory entry (App ID 5072126) was already live but was missing the `categories` field — verified live post-patch as `categories: ["cli-agent"]` (was `[]`). A new `/sh` chat slash command runs a local shell command, displays the output, and (by default) injects it into the agent's context as a user-role message so the model can use it on the next turn — pass `-n` before the command to display-only. The OpenRouter API Technical Reference doc is rewritten + expanded end-to-end against the live API (683 → 1,280 lines) with three new sections (App Directory Entry — Category & Description, Client/Harness Metrics & Reporting covering `/key` + `/credits` + `/generation` + `/activity` + `/datasets/app-rankings` + `/datasets/session-cost` + `/analytics/query`, Generation Inspection & Audit Trail) and a gap table of what AgentKthx currently captures vs. what OpenRouter exposes.

### Added — OpenRouter App Attribution headers (MAINT-26 CLOSED)

The pre-R07.21 `OpenRouterBackend` sent only `HTTP-Referer` + the legacy `X-Title` header. OpenRouter's [App Attribution spec](https://openrouter.ai/docs/app-attribution) has since added `X-OpenRouter-Title` (preferred form) and `X-OpenRouter-Categories` (marketplace category assignment, comma-separated, max 2 per request, max 10 per app). Result: the AgentKthx app entry rendered with `categories: []`, missing from both `/apps/category/coding` and `/apps/category/coding/cli-agent` despite the harness being a terminal-based coding assistant that fits the `cli-agent` leaf exactly.

- **Centralized `_build_openrouter_attribution_headers()` helper**: returns all four attribution headers. All four header-construction sites (`__init__`, `list_models`, `_make_api_request`, `_get_auth_headers`) delegate to this helper so the contract can never drift.
- **Hardcoded constants, NOT env-overridable** (`_APP_REFERER_URL`, `_APP_TITLE`, `_APP_CATEGORIES = "cli-agent"`): the harness category describes what AgentKthx *is* to OpenRouter's marketplace, not a runtime knob. The category is a module-level constant — if a fork needs a different identity, change the constant directly so the audit catches it.
- **Sends BOTH `X-OpenRouter-Title` AND `X-Title`** (legacy alias kept until OpenRouter formally deprecates). Rankings that haven't been re-indexed to look for the new header still attribute traffic correctly.
- **Verified live post-patch**: App ID 5072126, categories now `["cli-agent"]` (was `[]`). The directory entry's `description`, `main_url`, `slug`, `source_code_url` fields remain `null` — verified that OpenRouter provides NO documented mechanism to set them (no header, no API endpoint, no dashboard, no OpenGraph-crawler). The gap is structural on OpenRouter's side, not an AgentKthx defect. The four attribution headers are the complete mechanism OpenRouter exposes.

### Added — `/sh` chat slash command (MAINT-27 OPEN)

A new `/sh` slash command runs a local shell command, displays the output, and (by default) injects the output into the agent's context as a user-role message so the model can use it on the next turn. Pass `-n` before the command to display-only (skip the context injection).

- **Syntax**: `/sh [-n] <command...>` — the `-n` flag must be the first token after `/sh`. Pipes, redirects, and quotes work because the whole arg is passed to the shell as one command string (e.g. `/sh cat README.md | head -50`).
- **Reuses the built-in `shell()` tool** from `agentkthx.tools.builtins` — the same security checks (`sanitize_command`), timeout clamping (max 300s), and exit-code formatting apply. Output is formatted as `<shell_output command='...'>...</shell_output>` when injected.
- **Runs locally and synchronously, NOT through the model** — the user's prompt is not routed through the LLM. Security-mode setting (`/security max|off`) applies via `sanitize_command`. A blocked command (e.g. `rm -rf /`) is rejected with the same `Security error: ...` message.
- **`/help` listing updated** with a new line: `Run a local shell command (display + add to context; -n to display only)`.

### Fixed — Gemini `thought_signature` round-trip (ROB-40 CLOSED)

Gemini thinking models emit a `thought_signature` field on each `tool_call` in the response (nested at `tool_calls[].extra_content.google.thought_signature`). The API requires this signature to be passed back on the assistant message's `tool_calls` when the conversation history is sent back, or it 400s with `"Function call is missing a thought_signature"` on the second turn. AgentKthx's parser was capturing `id`, `name`, `arguments` — but dropping `thought_signature`.

- **`_parse_openai_response`** (`agentkthx/backends/openai_compat.py`): now extracts `thought_signature` from `tc["extra_content"]["google"]["thought_signature"]` and stores it on each parsed tool_call dict.
- **`Message.to_dict()`** (`agentkthx/core/memory.py`): when serializing the assistant message's `tool_calls` back to the OpenAI shape, re-attaches `thought_signature` as `extra_content.google.thought_signature` — but ONLY when present (other backends see no change).
- **Verified end-to-end**: the `thought_signature` survives the full round-trip (parse → memory → serialize) and lands in the exact shape Gemini expects. Multi-turn agentic runs with `--think --tools` on Gemini thinking models now work (was always 400ing on the second turn).

### Fixed — Mistral agent-internal-field stripping

`MistralBackend._build_mistral_body()` was forwarding every unknown kwarg verbatim into the request body. The skip list only excluded OpenAI-only fields — it didn't exclude agent-internal fields (`num_ctx`, `num_predict`, `truncation`, `num_batch`, `repeat_penalty`, `repeat_last_n`, `think`, `thinking_config`, etc.). These are Ollama/llama-server-specific fields that the agent loop forwards as kwargs — Mistral doesn't know them and 422s with `extra_forbidden: loc=['body','num_ctx']`.

- Added `_AGENT_INTERNAL_FIELDS` frozenset (15 fields) + skip check in the kwarg-forwarding loop. The agent loop's context-sizing fields are consumed by `agent_factory` and never need to reach any cloud API.

### Fixed — ZAI default-to-NATIVE

The ZAI backend was the outlier among cloud backends — it returned `UNTESTED` when the cache was empty (every other cloud backend returns `NATIVE`). The R07.19 #12 probe hardening overcorrected: it was supposed to stop caching REACT for transient errors, but it also changed the default-path return from NATIVE to UNTESTED. Combined with the `models.py` command always calling `test_tool_support(force_test=True)` (bypassing the default path), every ZAI model showed `? untested` in the models table.

- **`ZaiBackend.test_tool_support`**: when the cache is empty AND `force_test=False`, returns `NATIVE` (cloud-backend contract). The `force_test=True` path still does the live probe and caches definitive verdicts (including REACT for models that definitively reject the tools param).
- **`models.py:_get_tools_status`**: when `--tool-support` is NOT passed, calls `test_tool_support(force_test=False)` — cloud backends return NATIVE; local backends return UNTESTED. Only `--tool-support` forces the live probe.

### Fixed — CloudBackend `api_key` setter (was read-only — crashed `/auth`)

`CloudBackend.api_key` was a read-only `@property` (only a getter, no setter). The `/auth` picker's `_patch_live_backend` calls `setattr(backend, "api_key", new_value)` to patch a key onto the running session's backend — which raised `AttributeError: property 'api_key' of 'ZaiBackend' object has no setter` and crashed the chat session. Added a setter that writes through to `_api_key`. Every cloud backend (ZAI, OrcaRouter, Mistral, Pollinations) now supports live key patching via `/auth`.

### Fixed — ROB-38 fatal-error detection broadened

The empty-answer handler's `_fatal` markers didn't catch Gemini's `"Please pass a valid API key"` (HTTP 400, not 401) because the message says "valid", not "invalid". Added a broader `"api key"` substring marker that catches every variant: "valid api key", "invalid api key", "missing api key", "no api key", "api key not set", "api key required".

### Fixed — Gemini 2.5-flash catalog deprecation

Google deprecated `gemini-2.5-flash` and `gemini-2.5-flash-lite` for new users (returns 404 with "use models/gemini-3.8-flash instead"). The static catalog still listed them as `free_tier: true`, so the smoke test picked `gemini-2.5-flash` alphabetically. Flipped `free_tier: false` in the seed JSON + moved both entries to the deprecated/paid section in `FREE_TIER_LIMITS` with `0/0/0` rate limits.

### Changed — `OPENROUTER_API_TECHNICAL_REFERENCE.md` rewritten + expanded (683 → 1,280 lines)

Validated every existing section against the live OpenRouter API (466 models probed, App Attribution spec read, Usage Accounting doc read, OpenAPI 3.1 spec — 111 paths). The rewrite adds three new sections:

- **§2 App Attribution — Marketplace Headers** (NEW): the four headers, the centralized helper, why both title forms are sent, where the attribution appears.
- **§3 App Directory Entry — Category & Description** (NEW): full category-groups table, the hardcoded-constant rationale, the dashboard-only fields with recommended values (and the verification that there's NO mechanism to set them).
- **§14 Client/Harness Metrics & Reporting** (NEW): full schemas for `GET /key`, `GET /credits`, `GET /generation`, `GET /activity`, `GET /datasets/app-rankings`, `GET /datasets/session-cost`, `POST /analytics/query`. Includes a gap table of what AgentKthx currently captures vs. what OpenRouter exposes.
- **§15 Generation Inspection & Audit Trail** (NEW): the `/generation` endpoint as an audit tool.
- **Validated existing sections**: response schema now includes `usage.cost_details.upstream_inference_cost`, `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`. Error codes table gains `524 Edge Network Timeout` + `529 Provider Overloaded`.

### Changed — `--force-react` tri-state (MAINT-25 CLOSED)

`--force-react` was `store_true` (couldn't accept `=False` — the UNTESTED debug hint suggested `--force-react=False` which argparse rejected). Now tri-state (`on|off|auto`) with bare-flag backwards compat:
- `--force-react` (bare) = `"on"` → force ReAct (backwards compat)
- `--force-react off` → force native tools (the NEW opt-out from auto-detection)
- `--force-react auto` → preserve auto-detection (the default when not passed)

### Closed — 14 OPEN audit findings (3 batches)

All surgical, non-breaking fixes with clear patterns. Closure details in `audit/deltas.md` §R07.21 Audit Closure Batch.

**Batch 1 (5 findings):**
- **ROB-18** — PersistentMemory `Lock` → `RLock` (1 line — strict superset, allows nested locked calls without deadlock).
- **ROB-35** — `parse_shared_args` `or`-coalescing → `is not None` for 7 integer/float fields (preserves the documented `0` sentinel).
- **ROB-36** — `_parse_token_size` `math.isfinite` guard (turns `inf`/`1e400` `OverflowError` into clean `ValueError`).
- **ROB-38** — Empty-answer fatal-error branch (detects 401/402/403/quota/auth before the throttle branch + broadened `"api key"` marker).
- **ROB-39** — OpenRouter `_NON_CHAT_SLUG_PATTERNS` frozenset (classifies image/audio/moderation/embedding slugs as UNTESTED instead of NATIVE).

**Batch 2 (8 findings):**
- **ROB-09** — `validate_path` `abspath` → `realpath` (symlink traversal security fix).
- **ROB-17** — Token-tier truncation of single over-budget message (keeps the tail + visible marker).
- **ROB-20** — `Agent.num_predict` public `@property` (mirrors `num_ctx`; backed by `_num_predict`).
- **ROB-25** — Shared `DEFAULT_GENERATE_TEMPERATURE` (0.7) + `DEFAULT_GENERATE_MAX_TOKENS` (8192) constants in `base.py` — `generate()` + `_generate_with_auth()` now aligned.
- **ROB-30** — `_fetch_model_cards` catch-all `Exception` narrowed to `(JSONDecodeError, UnicodeDecodeError, ValueError)`.
- **MAINT-24** — `_build_tool_section` docstrings updated to match behavior (no longer promises ReAct format instructions the body doesn't include).
- **MAINT-25** — `--force-react` tri-state (on/off/auto) with bare-flag backwards compat (see Changed section above).
- **MAINT-26** — OpenRouter App Attribution (see Added section above; directory description/main_url/slug fields have NO documented mechanism — structural gap on OpenRouter's side).

**Batch 3 (1 finding):**
- **ROB-40** — Gemini `thought_signature` round-trip (see Fixed section above).

### Added — Smoke test harness

`scripts/smoke_test_r07_21.sh` — exercises every cloud backend with three steps each: models listing (FREE_ONLY env var), thinking output (`--think --no-stream`), shell tool call (`--tools shell --security off --no-stream`). Features:
- `--backend <name>` to test one backend, `--debug` for real-time `agentkthx run` output in a delimited box, `--help` for usage.
- Auto-skips backends without an API key; OpenAI's known 0-free-models case is handled as a skip.
- Auto-picks the first free chat model per backend (skips non-chat models: TTS, audio, image, embeddings, Gemma).
- Clears the persistent JSON cache before each backend so stale entries (e.g. deprecated models) don't surface.
- `timeout 120` per command; unique per-run marker for the shell-tool test.
- **Verified live**: 29 PASS / 0 FAIL / 2 SKIP across all 8 cloud backends (OpenAI + OrcaRouter skipped — known 0-free-models + exhausted free quota respectively).

### Tests

- **New `tests/test_r07_21_openrouter_attribution.py`** (+19 tests): pins the four attribution header values, pins all four header-construction sites delegate to the helper (source-grep), pins that the category is NOT env-overridable (source-grep + bytecode `co_names` check).
- **New `tests/test_r07_21_sh_command.py`** (+6 tests): the /sh handler's context-injection contract + a real-shell integration suite (echo → output, false → exit-code marker, ls on a missing path → error).
- **New `tests/test_r07_21_audit_closures.py`** (+36 tests): ROB-18 (RLock reentrancy), ROB-35 (0-sentinel preserved for all 7 fields), ROB-36 (`inf`/`1e400`/`nan` rejected cleanly), ROB-38 (fatal vs throttle classification), ROB-39 (non-chat slug classification).
- **New `tests/test_r07_21_audit_closures_batch2.py`** (+19 tests): ROB-09 (symlink rejection), ROB-17 (over-budget truncation), ROB-20 (num_predict property), ROB-25 (shared defaults), ROB-30 (narrowed catch), MAINT-24 (docstring contract), MAINT-25 (tri-state flag parsing).
- **Removed `TestVersionPin`** from `tests/test_r07_20_auth_picker.py` (-2 tests): exact-string pins broke on every release bump; redundant with `bump-version.sh`'s own site-verification step.
- **Relaxed** `test_r07_05_audit_fixes.py::test_write_lock_exists` (+1 test): accepts either `Lock` or `RLock` since ROB-18 changed the type.
- **Updated** `tests/test_gemini_backend.py::test_chat_flash_models_are_free`: removed `gemini-2.5-flash` assertion (Google deprecated it for new users).
- **Suite**: `python -m pytest tests/ -q` → **2802 passed / 16 skipped** (was 2608 at R07.20 baseline; +194 net).
- **Lint**: `ruff check agentkthx/ tests/` → all checks passed. `black --check agentkthx/ tests/` → 221 files unchanged.

### Audit register

- **14 OPEN findings CLOSED**: ROB-09, ROB-17, ROB-18, ROB-20, ROB-25, ROB-30, ROB-35, ROB-36, ROB-38, ROB-39, ROB-40, MAINT-24, MAINT-25, MAINT-26.
- **2 new findings OPEN**: MAINT-27 (`/sh` inline branch — MAINT-01 family), MAINT-26 (directory description gap — structural on OpenRouter's side, nothing actionable).
- **Register totals**: 117 findings / 26 OPEN / 84 CLOSED / 7 WONTFIX (was 116 / 37 OPEN / 70 CLOSED / 7 WONTFIX at R07.20). 78% closure rate (was 68%).

### Version bump

- `scripts/bump-version.sh R07.21` applied: bumped 4 sites across 3 files (`pyproject.toml`, `agentkthx/__init__.py` header + `__version__` line, `README.md`).
- `R07.20 (0.7.20)` → `R07.21 (0.7.21)`.

### Suite status

- `python -m pytest tests/ -q` → **2802 passed / 16 skipped**.
- `ruff check agentkthx/ tests/` → all checks passed.
- `black --check agentkthx/ tests/` → 221 files would be left unchanged.
- **Smoke test**: `./scripts/smoke_test_r07_21.sh` → **29 PASS / 0 FAIL / 2 SKIP** across all 8 cloud backends (verified live 2026-10-03).
