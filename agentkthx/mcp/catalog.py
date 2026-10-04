"""
⚛️ AgentKthx — Curated catalog of known MCP servers

A stdlib-only, in-repo registry of MCP servers that are known to work
with AgentKthx's MCP client (stdio JSON-RPC 2.0). Used by
``agentkthx mcp search`` to let operators discover servers without
leaving the terminal or hitting an external registry.

Design
------
* **No network calls.** The catalog is a Python literal embedded in
  this file. Updates require a code change (PR). This is deliberate:
  the project ethos is "zero runtime dependencies," and a live
  registry fetch would add an HTTPS round-trip + a JSON-schema
  dependency the project doesn't have.
* **Each entry has a copy-paste-ready config snippet** so an operator
  can go from ``mcp search git`` to a working ``mcp.json`` entry in
  under a minute.
* **Tiered trust.** ``status`` is one of:

    - ``verified`` — AgentKthx maintainers have run ``mcp probe``
      against this server successfully on the listed date.
    - ``community`` — Known to work, not directly verified by
      AgentKthx maintainers; package is widely used.
    - ``deprecated`` — Server is no longer maintained, or the
      package was removed from npm. Listed for discoverability of
      replacements.

* **Free-tier-friendly.** Each entry has ``free_tier`` (True/False)
  and ``requires_api_key`` (True/False). Servers that need a paid
  API key are still listed but flagged in the search output.

Adding a new entry
------------------
1. Append to :data:`KNOWN_MCP_SERVERS` below (keep alphabetical by ``name``).
2. Include a ``config_snippet`` that the user can paste directly into
   ``~/.agentkthx/mcp.json``'s ``servers`` array. Use a generic
   placeholder (``<DIR>``, ``<TOKEN>``, ``<DB_PATH>``) for any path-
   or credential-shaped arg.
3. If you have personally verified the server, set ``status`` to
   ``"verified"`` and add a ``verified_date`` (ISO 8601).
4. Run ``python -m pytest tests/test_mcp_cli.py::TestMcpSearch -q``
   to confirm the new entry is searchable.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
#
# Field reference:
#   name           (str)       Short identifier; same rules as MCPServerConfig
#                              (alphanumeric/-/_). Used as the JSON "name".
#   package        (str)       npm/PyPI package name, or "builtin" if the
#                              server is launched with `python3 -m ...`.
#   install        (str)       One-line install/launch command for docs.
#   use_case       (str)       One-sentence description.
#   tags           (list[str]) Lowercase keywords for substring search.
#   free_tier      (bool)      True if the server can be used at no cost.
#   requires_api_key (bool)    True if the server needs an out-of-band API
#                              key (env var or token) to function.
#   status         (str)       "verified" | "community" | "deprecated".
#   verified_date  (str|None)  ISO 8601 date if status == "verified".
#   config_snippet (dict)      Ready-to-paste JSON entry for mcp.json.
#   notes          (str|None)  Optional longer-form caveats.

KNOWN_MCP_SERVERS: list[dict[str, Any]] = [
    {
        "name": "filesystem",
        "package": "@modelcontextprotocol/server-filesystem",
        "install": "npx -y @modelcontextprotocol/server-filesystem <DIR>",
        "use_case": "Sandboxed file operations (read, write, list, search) scoped to one directory.",
        "tags": ["files", "io", "read", "write", "directory", "stdlib"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "verified",
        "verified_date": "2026-10-04",
        "config_snippet": {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "<DIR>"],
            "enabled": True,
            "timeout_seconds": 30,
        },
        "notes": "Pass one or more directory paths as args. The server refuses to operate outside them.",
    },
    {
        "name": "sequential-thinking",
        "package": "@modelcontextprotocol/server-sequential-thinking",
        "install": "npx -y @modelcontextprotocol/server-sequential-thinking",
        "use_case": "Chain-of-thought scratchpad tool; the agent externalizes multi-step reasoning into it.",
        "tags": ["reasoning", "cot", "scratchpad", "thinking", "planning"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "verified",
        "verified_date": "2026-10-04",
        "config_snippet": {
            "name": "sequential-thinking",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
            "enabled": True,
        },
        "notes": "Useful for tasks where the model benefits from explicit intermediate steps before answering.",
    },
    {
        "name": "memory",
        "package": "@modelcontextprotocol/server-memory",
        "install": "npx -y @modelcontextprotocol/server-memory",
        "use_case": "Persistent knowledge graph (entities + relations) backed by a JSON file.",
        "tags": ["memory", "knowledge", "graph", "persistent"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "memory",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
            "enabled": False,
            "comment": "Persistent knowledge graph (JSON file). Enable if you want long-term agent memory.",
        },
        "notes": "Storage path defaults to ./memory.json in the server's CWD; consider setting cwd explicitly.",
    },
    {
        "name": "sqlite",
        "package": "@modelcontextprotocol/server-sqlite",
        "install": "npx -y @modelcontextprotocol/server-sqlite <DB_PATH>",
        "use_case": "Natural-language to SQL: exposes a SQLite DB as tools the agent can query.",
        "tags": ["database", "sql", "sqlite", "query"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "sqlite",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sqlite", "<DB_PATH>"],
            "enabled": True,
        },
        "notes": "Pass the path to a .sqlite/.db file as the last arg. Server creates it if missing.",
    },
    {
        "name": "time",
        "package": "@modelcontextprotocol/server-time",
        "install": "npx -y @modelcontextprotocol/server-time",
        "use_case": "Timezone conversion and current-time lookup.",
        "tags": ["time", "timezone", "date", "clock"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "time",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-time"],
            "enabled": True,
        },
        "notes": None,
    },
    {
        "name": "fetch",
        "package": "mcp-server-fetch",
        "install": "uvx mcp-server-fetch",
        "use_case": "Web page retrieval — fetches a URL and returns the rendered text content.",
        "tags": ["web", "http", "fetch", "scrape", "url"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "fetch",
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "enabled": True,
        },
        "notes": "Requires uv/uvx on $PATH (https://docs.astral.sh/uv/).",
    },
    {
        "name": "puppeteer",
        "package": "@modelcontextprotocol/server-puppeteer",
        "install": "npx -y @modelcontextprotocol/server-puppeteer",
        "use_case": "Headless Chrome automation — navigate, click, screenshot, evaluate JS.",
        "tags": ["browser", "chrome", "automation", "scraping", "screenshot"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "puppeteer",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
            "enabled": False,
            "comment": "Heavy dependency — downloads Chromium on first run. Enable only if you need browser automation.",
        },
        "notes": "First launch downloads ~150 MB of Chromium binaries.",
    },
    {
        "name": "serena",
        "package": "oraios/serena",
        "install": "uvx --from git+https://github.com/oraios/serena serena start-mcp-server",
        "use_case": "LSP-based code intelligence — symbols, references, find/replace, git ops. Drop-in replacement for the deprecated server-git.",
        "tags": ["code", "lsp", "symbols", "git", "refactor", "intellisense"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "serena",
            "command": "uvx",
            "args": [
                "--from",
                "git+https://github.com/oraios/serena",
                "serena",
                "start-mcp-server",
            ],
            "enabled": False,
            "comment": "LSP-based code intelligence. Recommended replacement for @modelcontextprotocol/server-git.",
        },
        "notes": "Requires uv on $PATH. Supports many languages via LSP; check oraios/serena README for the list.",
    },
    {
        "name": "brave-search",
        "package": "@modelcontextprotocol/server-brave-search",
        "install": "npx -y @modelcontextprotocol/server-brave-search",
        "use_case": "Web + image + news search via the Brave Search API.",
        "tags": ["search", "web", "brave", "internet"],
        "free_tier": True,
        "requires_api_key": True,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "brave-search",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-brave-search"],
            "env": {"BRAVE_API_KEY": "<your-brave-api-key>"},
            "enabled": False,
            "comment": "Free tier: 2000 queries/month. Get a key at https://brave.com/search/api/",
        },
        "notes": "Free tier is 2000 queries/month, sufficient for typical agent use.",
    },
    {
        "name": "github",
        "package": "@modelcontextprotocol/server-github",
        "install": "npx -y @modelcontextprotocol/server-github",
        "use_case": "GitHub API — repos, issues, PRs, file contents, code search.",
        "tags": ["github", "git", "code", "issues", "prs"],
        "free_tier": True,
        "requires_api_key": True,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "<your-github-pat>"},
            "enabled": False,
            "comment": "Create a PAT at https://github.com/settings/tokens (read-only scopes recommended).",
        },
        "notes": "Use a fine-grained PAT with read-only scopes; do not use a classic token with full repo access.",
    },
    {
        "name": "git-deprecated",
        "package": "@modelcontextprotocol/server-git",
        "install": "(removed from npm — 404 as of 2026-10-04)",
        "use_case": "Local git repository operations — REMOVED from npm. Listed here only to help users find the replacement.",
        "tags": ["git", "deprecated", "removed"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "deprecated",
        "verified_date": None,
        "config_snippet": {},
        "notes": "Package was removed from npm. Use `serena` for git ops + code intelligence, or `github` for remote GitHub API access.",
    },
    {
        "name": "audit",
        "package": "agentkthx.skills.codebase_audit.mcp_server",
        "install": "python3 -m agentkthx.skills.codebase_audit.mcp_server --repo <REPO>",
        "use_case": "AgentKthx's own codebase-audit skill exposed as an MCP server (Phase 2 — planned).",
        "tags": ["audit", "codebase", "review", "vulnerability", "agentkthx"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "community",
        "verified_date": None,
        "config_snippet": {
            "name": "audit",
            "command": "python3",
            "args": [
                "-m",
                "agentkthx.skills.codebase_audit.mcp_server",
                "--repo",
                "<REPO>",
            ],
            "enabled": False,
            "comment": "Phase 2 — kthx-audit MCP server (see docs/mcp/ROADMAP.md). Not yet implemented.",
        },
        "notes": "Tracked as Phase 2 in docs/mcp/ROADMAP.md.",
    },
    {
        "name": "conformance",
        "package": "@modelcontextprotocol/conformance",
        "install": "npx -y @modelcontextprotocol/conformance",
        "use_case": "NOT an MCP server. CLI test harness that runs the official conformance suite against a server implementation.",
        "tags": ["test", "conformance", "cli", "not-a-server"],
        "free_tier": True,
        "requires_api_key": False,
        "status": "deprecated",
        "verified_date": None,
        "config_snippet": {},
        "notes": (
            "This package is a CLI test harness, not an MCP server. Adding it to mcp.json "
            "will fail with 'subprocess closed stdout while waiting for id=1'. To USE it, "
            "invoke directly: `npx -y @modelcontextprotocol/conformance server --command npx "
            "--args -y,@modelcontextprotocol/server-filesystem,/tmp`. Listed here so users "
            "who hit the failure can find the explanation."
        ),
    },
]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_catalog(
    query: str | None = None,
    *,
    tag: str | None = None,
    status: str | None = None,
    include_deprecated: bool = True,
) -> list[dict[str, Any]]:
    """Filter the catalog by substring match against ``query``.

    The query is matched (case-insensitive) against the entry's
    ``name``, ``package``, ``use_case``, and each ``tag``. A None or
    empty query returns every entry.

    Args:
        query: Substring to search for. None or "" returns all entries.
        tag: Optional tag to filter on (case-insensitive). Combined
            with the query using AND semantics.
        status: Optional status filter ("verified" / "community" /
            "deprecated"). Combined with AND.
        include_deprecated: If False, entries with status == "deprecated"
            are filtered out. Default True — they are useful for
            discoverability of replacements.

    Returns:
        List of matching catalog entries (shallow copies so callers
        can safely mutate without affecting the global catalog).
    """
    q = (query or "").lower().strip()
    t = (tag or "").lower().strip()
    s = (status or "").lower().strip() or None

    out: list[dict[str, Any]] = []
    for entry in KNOWN_MCP_SERVERS:
        if not include_deprecated and entry.get("status") == "deprecated":
            continue
        if s is not None and entry.get("status") != s:
            continue
        if t:
            tags = [str(x).lower() for x in entry.get("tags", [])]
            if t not in tags:
                continue
        if q:
            haystacks = [
                str(entry.get("name", "")),
                str(entry.get("package", "")),
                str(entry.get("use_case", "")),
                str(entry.get("install", "")),
                " ".join(str(x) for x in entry.get("tags", [])),
            ]
            haystack = "\n".join(haystacks).lower()
            if q not in haystack:
                continue
        out.append(dict(entry))
    return out


def get_entry(name: str) -> dict[str, Any] | None:
    """Return a shallow copy of the catalog entry for ``name``, or None."""
    for entry in KNOWN_MCP_SERVERS:
        if entry.get("name") == name:
            return dict(entry)
    return None


__all__ = ["KNOWN_MCP_SERVERS", "search_catalog", "get_entry"]
