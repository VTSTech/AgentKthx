"""
⚛️ AgentKthx — MCP (Model Context Protocol) client support

Status: **SCAFFOLD** — Phase 1 in progress.

This package implements MCP **client** mode: AgentKthx connects to external
MCP servers (stdio subprocess transport) and bridges their tools into the
existing :class:`agentkthx.tools.registry.ToolRegistry` so the agent can
call them like any built-in tool.

Design goals
------------
1. **Stdlib-only** — no third-party deps; ``subprocess`` + ``json`` + ``os``
   only, in keeping with the project's zero-dependency invariant.
2. **Lazy discovery** — MCP servers are started on first use, not at agent
   construction. A session that doesn't trigger MCP tools pays zero startup
   cost (closes the "MCP adds startup latency" objection).
3. **Defense-in-depth preserved** — every MCP tool result flows through
   :func:`agentkthx.core.helpers.sanitize_tool_output` exactly like built-in
   tools. The security boundary does not weaken because the tool came from
   a subprocess.
4. **Explicit config** — servers are declared in ``~/.agentkthx/mcp.json``;
   no auto-discovery of arbitrary binaries on ``$PATH``. This is deliberate
   (SEC-13 plugin-pin enforcement is still open; MCP follows the same
   trust-the-operator stance).
5. **Per-session opt-in** — MCP servers are NOT loaded by default. The
   operator must enable them via config or ``--mcp`` flag.

What is NOT here yet
--------------------
* MCP **server** mode (exposing AgentKthx's own tools as an MCP server).
  Tracked as a Phase 2 deliverable — see ``docs/mcp/ROADMAP.md``.
* HTTP/SSE transport (only stdio is implemented). HTTP/SSE is on the
  roadmap but stdio covers the dominant deployment pattern (local
  subprocesses).
* Resource subscriptions (``resources/subscribe``). Tools only for v0.1.
* Prompts (``prompts/list``, ``prompts/get``). Not exposed.
* Sampling (``sampling/createMessage``) — would let an MCP server ask
  AgentKthx to run an LLM call. Deliberately deferred: this is a privilege
  escalation surface and we want client-mode to stabilize first.

Public API
----------
.. autoclass:: MCPClient
.. autoclass:: MCPServerConfig
.. autoclass:: MCPManager
.. autofunction:: load_mcp_config
"""

from __future__ import annotations

from .cache import DEFAULT_TTL as CACHE_DEFAULT_TTL
from .cache import cache_path, clear_cache, get_cached, set_cached
from .client import MCPClient, MCPClientError
from .config import (
    MCPConfigError,
    MCPServerConfig,
    default_config_path,
    load_mcp_config,
    write_example_config,
)
from .manager import MCPManager, MCPManagerError
from .registry import (
    MCPRegistryError,
    build_config_snippet,
    derive_short_name,
    github_search,
    npm_package_info,
    npm_search,
    search_all,
    search_all_with_errors,
)
from .transport import MCPTransportError, StdioTransport

__all__ = [
    # Config
    "MCPServerConfig",
    "MCPConfigError",
    "load_mcp_config",
    "write_example_config",
    "default_config_path",
    # Transport + client
    "StdioTransport",
    "MCPTransportError",
    "MCPClient",
    "MCPClientError",
    "MCPManager",
    "MCPManagerError",
    # Live registry (R07.23)
    "MCPRegistryError",
    "npm_search",
    "npm_package_info",
    "github_search",
    "search_all",
    "search_all_with_errors",
    "derive_short_name",
    "build_config_snippet",
    # Cache (R07.23)
    "CACHE_DEFAULT_TTL",
    "cache_path",
    "get_cached",
    "set_cached",
    "clear_cache",
    # Version
    "__version__",
]

__version__ = "0.2.0-scaffold"
