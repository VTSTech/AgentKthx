# MCP Support — Roadmap & Design

> Status: **Phase 1 CLI integration complete** — `agentkthx mcp` subcommand + `--mcp` flag wired
> Last updated: 2026-10-04
> Maintainer: VTSTech

This document tracks AgentKthx's MCP (Model Context Protocol) integration: what's done, what's planned, what was explicitly deferred, and why.

---

## Why MCP for AgentKthx

AgentKthx is a stdlib-only agentic framework with a built-in tool registry. MCP lets the agent call tools hosted in external subprocesses, which:

1. **Decouples tool capability from the framework** — community-written MCP servers (filesystem, git, sqlite, serena, brave-search, ...) become usable by AgentKthx without us shipping or maintaining them.
2. **Saves context window** — MCP servers can pre-process output (return summaries, not raw dumps) and only the tools a session actually loads enter the system prompt.
3. **Creates a dogfooding surface** — exposing AgentKthx's own capabilities (especially the `codebase-audit` skill) as MCP servers lets other MCP clients (Cline, Continue, Roo Code, Claude Desktop, mcphost) consume them.
4. **Fits the project ethos** — MCP is JSON-RPC over stdio, trivially implementable in stdlib `subprocess` + `json`. No new runtime dependencies required.

For free-tier users specifically: MCP servers run locally as subprocesses, so the tool ecosystem is free regardless of which LLM backend the operator chooses.

---

## What's done (Phase 1 scaffold)

The `agentkthx/mcp/` package contains:

| Module | Purpose | Status |
|--------|---------|--------|
| `config.py` | Load/validate `~/.agentkthx/mcp.json`; per-server config dataclass | ✅ scaffold + tests |
| `transport.py` | StdioTransport — JSON-RPC 2.0 over subprocess stdin/stdout | ✅ scaffold |
| `client.py` | MCPClient — wraps transport; `initialize`, `tools/list`, `tools/call` | ✅ scaffold |
| `manager.py` | MCPManager — multi-server orchestrator + ToolRegistry bridge | ✅ scaffold + tests |
| `mcp.example.json` | Reference config with filesystem/git/memory/audit entries | ✅ |

**22 smoke tests pass in 0.26s** — pure-Python paths only. No subprocess is spawned by the test suite (consistent with the project's mocked-unit-test convention; TEST-01 still open).

### Security guarantees (scaffold)

- Config file must be owner-writable; group/world writable triggers a warning
- `command` must be absolute path or resolvable via `shutil.which` — no shell expansion, no `~`, no `bash -c`
- Subprocesses spawn with `shell=False`, `close_fds=True`, inherited env + explicit overrides only
- Every MCP tool result flows through `sanitize_tool_output` (8 KB truncation + secret redaction + ANSI strip) — identical to built-in tools. The security boundary does not weaken because the tool came from a subprocess.
- MCP tool names are namespaced `<server>__<tool>` to prevent collisions and cross-server impersonation

### What's NOT in the scaffold (deliberate deferrals)

- **`--mcp` CLI flag** — not yet wired into `shared_args.py` or `cli/agent_factory.py`. The scaffold can be exercised programmatically but not from the CLI yet.
- **`agentkthx mcp` subcommand** — `mcp list`, `mcp init`, `mcp probe` are planned but not implemented.
- **Lazy server startup** — v0.1 is eager (all configured servers start at `connect_all`). Lazy is on the roadmap.
- **HTTP/SSE transport** — stdio only for v0.1.
- **Resource subscriptions** — `resources/subscribe` flow not implemented.
- **Prompts** — `prompts/list`, `prompts/get` not exposed.
- **Sampling** — `sampling/createMessage` deliberately deferred (privilege escalation surface).
- **Per-call timeouts that actually interrupt** — the scaffold uses blocking `readline()`; a hung server blocks the full timeout window. Tracked as TODO in `transport.py`.

---

## Roadmap

### Phase 1 — Client mode, stdio, CLI integration (current)

**Goal:** `agentkthx chat --mcp` works end-to-end against the official MCP reference servers.

| Step | Description | Status |
|------|-------------|--------|
| 1.1 | `agentkthx/mcp/` package scaffold | ✅ done |
| 1.2 | `--mcp` flag in `shared_args.py:add_agent_args` (multi-arg; accepts server names to enable) | ✅ done |
| 1.3 | `--mcp-config <path>` flag (override `~/.agentkthx/mcp.json`) | ✅ done |
| 1.4 | `MCPManager` integration in `cli/agent_factory.py:_build_agent` (after backend setup, before agent construction) | ✅ done |
| 1.5 | `agent._mcp_manager` lifecycle: connect at startup, close at session end (REPL exit + Ctrl+C) | ✅ done |
| 1.6 | `agentkthx mcp` subcommand: `list`, `init`, `probe <name>` | ✅ done |
| 1.7 | End-to-end smoke test (real subprocess, mocked-unit-test convention preserved) | ✅ done (echo server, not in test suite) |
| 1.8 | Documentation: `docs/mcp/USAGE.md` with worked examples | ☐ todo |
| 1.9 | Audit findings MCP-01..05 filed in `audit/audit.md` | ✅ done |
| 1.10 | `README.md` section: "Use MCP servers with AgentKthx" | ☐ todo |

**Phase 1 status (2026-10-04):** 2856 passed / 16 skipped (up from 2802).
48 new MCP tests across two files (`test_mcp_scaffold.py` + `test_mcp_cli.py`).
End-to-end verified against a stdlib-only echo MCP server subprocess (initialize → tools/list → tools/call → clean shutdown).
MCP-01..05 filed in `audit/audit.md` with priority-matrix placements.

### Phase 2 — `kthx-audit` MCP server (planned, high priority)

**Goal:** Expose the `agentkthx/skills/codebase-audit/` skill as a standalone MCP server so any MCP-compatible client (Claude Desktop, Cline, Continue, mcphost) can call it.

This is the **single most leveraged integration available to the project** because:

1. **It dogfoods Phase 1.** Once the audit MCP server exists, AgentKthx's own `chat` mode can connect to it via the MCP client scaffold — proving the round-trip works in a real workflow.
2. **It fills a gap.** There is no good off-the-shelf "audit my repo and tell me what's wrong" MCP server as of late 2026. The official servers cover file/git/sqlite/search; none cover semantic audit.
3. **It drives inbound discovery.** Listing on `mcp.so` and `smithery.ai` puts AgentKthx in front of every MCP-client user, not just every AgentKthx user.
4. **The codebase-audit skill already exists.** It generates `brief.md`, `audit.md`, `deltas.md`, and a dashboard. Wrapping it as a stdio MCP server is bounded work (~300-500 LOC).
5. **It demonstrates the framework's value.** "AgentKthx audited itself, and you can call that same audit from your favorite chat UI" is a compelling pitch.

#### Proposed tool surface

The MCP server would expose these tools (names subject to change):

| Tool | Input | Output | Notes |
|------|-------|--------|-------|
| `audit_repo` | `path`, `depth?` ("quick" \| "full") | `{brief_path, finding_count, open_count, archived_count}` | Triggers a fresh audit; returns paths to the generated artifacts |
| `get_brief` | `path` | `{brief_markdown}` | Returns the contents of `audit/brief.md` if present |
| `list_findings` | `path`, `status?` ("open" \| "archived"), `severity?` | `[{id, title, severity, status}]` | Filtered list |
| `get_finding` | `path`, `id` | `{id, title, severity, body_markdown, suggested_fix}` | Full detail |
| `close_finding` | `path`, `id`, `closure_prose` | `{ok, delta_entry}` | Writes to `audit/deltas.md` |
| `re_audit` | `path`, `since_release?` | `{changed_findings, new_findings, closed_findings}` | Incremental |
| `generate_dashboard` | `path` | `{dashboard_html_path}` | Runs `audit/generate_audit_dash.py` |

#### Implementation sketch

```
agentkthx/skills/codebase_audit/
├── __init__.py            # existing
├── SKILL.md               # existing
├── ...                    # existing audit logic
└── mcp_server.py          # NEW — stdio MCP server entry point
                            # python3 -m agentkthx.skills.codebase_audit.mcp_server --repo <path>
```

The server module would:

1. Reuse the stdlib JSON-RPC pattern from `agentkthx/mcp/transport.py` (server-side counterpart)
2. Reuse the existing audit logic in the skill — do not duplicate
3. Read `--repo <path>` from argv; refuse to operate outside that path (same `validate_path` discipline as built-in tools)
4. Return markdown content as `text` content items (not `resource` — keep v0.1 simple)
5. Long-running audits (`audit_repo` with `depth=full`) should stream progress via `notifications/progress` (the MCP spec includes this for exactly this case)

#### Sizing estimate

- ~150 LOC for the JSON-RPC server loop (mirror of `transport.py`'s client side)
- ~150 LOC for the tool dispatch table (7 tools × ~20 LOC each)
- ~50 LOC for argv parsing + repo path validation
- ~50 LOC for tests (mocked; same convention as Phase 1 scaffold)
- **~400 LOC total**, plus the existing skill code it delegates to

This is a 1-2 day implementation once Phase 1's CLI integration is stable.

#### Phase 2 acceptance criteria

- [ ] `python3 -m agentkthx.skills.codebase_audit.mcp_server --repo .` starts and responds to `initialize`
- [ ] All 7 tools listed in the table are implemented and have tests
- [ ] The server passes the official MCP server test suite (when one exists — currently the reference servers are tested ad-hoc)
- [ ] An end-to-end test: AgentKthx chat mode connects to its own audit server via the Phase 1 client scaffold and successfully calls `audit_repo` on its own repo
- [ ] Listed on `mcp.so` and `smithery.ai` with a clear description
- [ ] `docs/mcp/ROADMAP.md` (this file) updated with closure notes

### Phase 3 — MCP server mode (general)

**Goal:** Any AgentKthx tool can be exposed as an MCP server via `agentkthx mcp serve`.

This generalizes Phase 2: instead of one-off MCP server modules per skill, a generic adapter exposes the agent's entire `ToolRegistry` over stdio MCP. Other MCP clients can then drive AgentKthx's tools.

| Step | Description | Priority |
|------|-------------|----------|
| 3.1 | `agentkthx/mcp/server.py` — generic stdio MCP server that exposes a ToolRegistry | medium |
| 3.2 | `agentkthx mcp serve [--tool <name>...] [--exclude <name>...]` CLI | medium |
| 3.3 | Auto-generate `inputSchema` from `Tool.to_json_schema()` (mostly already there) | medium |
| 3.4 | Sampling support (`sampling/createMessage`) — lets an MCP server ask the host to run an LLM call. **Privilege escalation surface — design carefully.** | low (deferred) |
| 3.5 | Resource subscriptions for file-backed tools (`read_file` → `resources/subscribe`) | low |

### Phase 4 — HTTP/SSE transport (deferred)

stdio covers the dominant deployment (local subprocess). HTTP/SSE is needed for:

- Remote MCP servers (e.g., a hosted kthx-audit service)
- Web-based MCP clients (LibreChat, custom web UIs)

Not on the near-term roadmap. Adding it requires:

- A stdlib HTTP server (`http.server` + `socketserver`) with SSE support
- Authentication (Bearer token, at minimum)
- Rate limiting
- A different threat model (remote callers are not trusted by default)

This is at least a 2-week project and isn't justified until there's real demand. The stdio transport will cover 90%+ of users.

### Phase 5 — MCP-aware souls (speculative)

Souls could declare which MCP servers they expect (`soul.yaml` field `mcp_servers: [filesystem, git, audit]`). The agent factory would auto-connect those servers when the soul is loaded.

This is speculative — it depends on whether soul authors actually want this. Defer until 2+ real souls ask for it.

---

## Known limitations & audit findings (to be filed)

When Phase 1 lands for real (CLI wired up, integration test passing), these findings should be filed in `audit/audit.md`:

| ID (proposed) | Severity | Summary |
|----------------|----------|---------|
| MCP-01 | Medium | `StdioTransport._read_response` uses blocking `readline()`; a hung MCP server blocks the calling thread for the full timeout window and cannot be interrupted cleanly by Ctrl+C. Same shape as ROB-06. Fix: thread+queue pattern. |
| MCP-02 | Medium | MCP server configs in `~/.agentkthx/mcp.json` have no sha256 pin equivalent (contrast SEC-13 for plugins). An attacker who can write the config file can substitute any binary for a declared server name. Mitigation: file perm warning (already implemented), future: optional pin field + `AGENTKTHX_REQUIRE_MCP_PINS=1`. |
| MCP-03 | Low | `_extract_params` flattens complex JSON Schema constructs (`oneOf`, `$ref`, `allOf`) to default `string`. A tool with a sophisticated schema will appear simpler to the model than it should. Fix: emit a single `arguments_json` string param for schemas we can't structurally convert. |
| MCP-04 | Low | Eager startup (`connect_all`) means every configured server is spawned at agent construction even if no MCP tool is called during the session. Lazy startup (per-server on first tool call) would reduce startup latency for users with many configured servers. |
| MCP-05 | Low | No `notifications/tools/list_changed` handling. If an MCP server adds/removes tools at runtime, AgentKthx's registry won't update until the next `connect_all`. |

---

## Free-tier-friendly MCP servers (curated)

These are tested to work with local LLMs and require no paid API keys. They are the recommended starting set for users who want to try AgentKthx + MCP without spending money:

| Server | Install | Use case |
|--------|---------|----------|
| `@modelcontextprotocol/server-filesystem` | `npx -y @modelcontextprotocol/server-filesystem <dir>` | Sandboxed file operations |
| `@modelcontextprotocol/server-git` | `npx -y @modelcontextprotocol/server-git <dir>` | Repo operations |
| `@modelcontextprotocol/server-sqlite` | `npx -y @modelcontextprotocol/server-sqlite <db>` | NL → SQL |
| `@modelcontextprotocol/server-memory` | `npx -y @modelcontextprotocol/server-memory` | Persistent knowledge graph |
| `@modelcontextprotocol/server-time` | `npx -y @modelcontextprotocol/server-time` | Timezone conversion |
| `@modelcontextprotocol/server-sequential-thinking` | `npx -y @modelcontextprotocol/server-sequential-thinking` | CoT scratchpad |
| `mcp-server-fetch` | `uvx mcp-server-fetch` | Web page retrieval |
| `mcp-server-puppeteer` | `npx -y @modelcontextprotocol/server-puppeteer` | Headless Chrome automation |
| `oraios/serena` | `uvx --from git+https://github.com/oraios/serena serena start-mcp-server` | LSP-based code intelligence |
| `@modelcontextprotocol/server-brave-search` | `npx -y @modelcontextprotocol/server-brave-search` | Web search (free 2000 queries/mo) |
| `@modelcontextprotocol/server-github` | `npx -y @modelcontextprotocol/server-github` | GitHub API (free PAT) |

**Avoid:** community `mcp-shell` / `mcp-server-commands` variants that expose generic shell execution. They typically lack command filtering, timeout clamping, and output truncation — your built-in `shell()` tool is safer.

---

## Decision log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-04 | Phase 1 will be stdio-only | stdio covers >90% of use cases; HTTP/SSE adds auth + rate-limiting complexity that isn't justified without demand |
| 2026-10-04 | Phase 1 will be eager, not lazy | Eager surfaces config errors at startup rather than mid-session. Lazy is a Phase 1.x optimization |
| 2026-10-04 | `sampling/createMessage` deferred to Phase 3 at earliest | Lets an MCP server ask the host to run an LLM call — privilege escalation surface. Want client-mode stabilization first |
| 2026-10-04 | Tool namespacing uses `__` separator | MCP tool names allow `/` and `_`; `__` cannot appear in either field, eliminating collision risk |
| 2026-10-04 | `kthx-audit` MCP server prioritized over generic `mcp serve` | Dogfooding + unique capability + bounded scope. Generic server mode is Phase 3 |
| 2026-10-04 | No auto-discovery of MCP servers on `$PATH` | Operator must explicitly declare servers in `mcp.json`. Consistent with the plugin pin ethos (SEC-13 still open) |

---

## References

- MCP specification: <https://modelcontextprotocol.io/specification>
- Official reference servers: <https://github.com/modelcontextprotocol/servers>
- Serena (LSP-based code intelligence): <https://github.com/oraios/serena>
- MCP directory: <https://mcp.so>
- Smithery (MCP registry): <https://smithery.ai>

---

*This document is the source of truth for MCP-related work in AgentKthx. Update it when Phase 1 lands for real, when Phase 2 starts, and whenever a deferred item is revisited.*
