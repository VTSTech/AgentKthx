"""
⚛️ AgentKthx — MCP manager (multi-server orchestrator + registry bridge)

The manager is the integration point between :mod:`agentkthx.mcp` and the
existing :class:`agentkthx.tools.registry.ToolRegistry`.

Responsibilities
----------------
1. Hold a collection of :class:`MCPClient` instances, one per configured server.
2. On :meth:`MCPManager.connect_all`, query each server for its tool list.
3. For each MCP tool, create a shim :class:`agentkthx.core.models.Tool`
   whose handler forwards the call to the right :class:`MCPClient`, then
   pipes the result through
   :func:`agentkthx.core.helpers.sanitize_tool_output`.
4. Register all shim Tools into a target ToolRegistry (typically the
   agent's).

Tool name namespacing
---------------------
MCP tools are namespaced by server name to avoid collisions:

    server_name + "__" + tool_name

So ``filesystem/read_file`` becomes the Python identifier
``filesystem__read_file``. The ``__`` separator is chosen because:
* It's not a valid character in MCP tool names (they use ``/`` or
  ``_``), so collisions are impossible if server names follow our
  alphanumeric/-/_ rule (enforced in MCPServerConfig).
* It visually distinguishes from a single-segment name.

Lazy vs eager
-------------
v0.1 is **eager** — :meth:`connect_all` opens every server and queries
every tool list. This is simplest and surfaces config errors immediately.
The roadmap has lazy mode for v0.2 (servers started on first tool call).
"""

from __future__ import annotations

import threading
from typing import Any

from ..core.helpers import sanitize_tool_output
from ..core.models import Tool, ToolParam
from ..tools.registry import ToolRegistry
from .client import MCPClient, MCPClientError
from .config import MCPServerConfig, load_mcp_config
from .transport import MCPTransportError


class MCPManagerError(RuntimeError):
    """Raised when the manager encounters a configuration or aggregation error."""


def _ns(server_name: str, tool_name: str) -> str:
    """Build a namespaced tool identifier."""
    return f"{server_name}__{tool_name}"


def _split_ns(namespaced: str) -> tuple[str, str]:
    """Split a namespaced identifier back into (server, tool_name)."""
    if "__" not in namespaced:
        raise ValueError(f"Not a namespaced tool id: {namespaced!r}")
    server, tool = namespaced.split("__", 1)
    return server, tool


class MCPManager:
    """Manages a collection of MCP clients and bridges their tools.

    Typical usage in :mod:`agentkthx.cli.agent_factory`::

        from ..mcp import MCPManager, load_mcp_config

        mcp_configs = load_mcp_config()  # from ~/.agentkthx/mcp.json
        if mcp_configs:
            manager = MCPManager(mcp_configs)
            manager.connect_all()
            manager.register_into(agent.tools)
            # Stash on the agent so it can be closed at session end
            agent._mcp_manager = manager

    The agent then treats MCP-backed tools exactly like built-ins: same
    JSON schema in the prompt, same dispatch path, same
    :func:`sanitize_tool_output` wrapping of results.
    """

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs: dict[str, MCPServerConfig] = {c.name: c for c in configs}
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, tuple[str, str, dict[str, Any]]] = {}
        # namespaced_name -> (server_name, mcp_tool_name, mcp_tool_def)
        self._lock = threading.Lock()
        self._connected = False

    # ---- lifecycle ----

    def connect_all(self, *, skip_failures: bool = True) -> list[tuple[str, str]]:
        """Connect to every configured server and enumerate their tools.

        Args:
            skip_failures: If True (default), a server that fails to
                connect or list tools is skipped with a warning. If False,
                the first failure raises :class:`MCPManagerError`.

        Returns:
            List of ``(server_name, error_message)`` for servers that
            failed and were skipped. Empty list if all servers connected.
        """
        failures: list[tuple[str, str]] = []
        with self._lock:
            for name, cfg in self._configs.items():
                try:
                    client = MCPClient(cfg)
                    client.connect()
                    tools = client.list_tools()
                    self._clients[name] = client
                    for tool_def in tools:
                        self._register_tool_def(name, tool_def)
                except (MCPClientError, MCPTransportError) as e:
                    if not skip_failures:
                        raise MCPManagerError(f"MCP server '{name}' failed: {e}") from e
                    failures.append((name, str(e)))
            self._connected = True
        return failures

    def close_all(self, timeout: float = 2.0) -> None:
        """Close every client. Safe to call multiple times."""
        with self._lock:
            for client in self._clients.values():
                try:
                    client.close(timeout=timeout)
                except Exception:
                    pass
            self._clients.clear()
            self._tools.clear()
            self._connected = False

    # ---- registry bridge ----

    def register_into(self, registry: ToolRegistry, *, overwrite: bool = False) -> int:
        """Register all discovered MCP tools as shim Tools in ``registry``.

        Args:
            registry: Target ToolRegistry (typically the agent's).
            overwrite: If True, replace any existing tool with the same
                namespaced name. If False (default), skip and warn.

        Returns:
            Number of tools registered.
        """
        if not self._connected:
            raise MCPManagerError("MCPManager.connect_all() must be called before register_into()")

        count = 0
        for namespaced_name, (server, mcp_name, tool_def) in self._tools.items():
            if not overwrite and namespaced_name in registry:
                import sys

                print(
                    f"[MCP] skip: tool '{namespaced_name}' already registered",
                    file=sys.stderr,
                )
                continue

            shim = self._build_shim_tool(namespaced_name, server, mcp_name, tool_def)
            registry.register_tool(shim)
            count += 1
        return count

    # ---- introspection ----

    @property
    def server_names(self) -> list[str]:
        return list(self._clients.keys())

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def describe(self) -> dict[str, dict[str, Any]]:
        """Return a per-server diagnostic dict (for ``agentkthx mcp list``)."""
        out: dict[str, dict[str, Any]] = {}
        for name, cfg in self._configs.items():
            client = self._clients.get(name)
            if client is None:
                out[name] = {
                    "configured": True,
                    "connected": False,
                    "tools": 0,
                    "command": cfg.command,
                }
            else:
                tool_count = sum(1 for n, (s, _, _) in self._tools.items() if s == name)
                out[name] = {
                    "configured": True,
                    "connected": client.is_alive,
                    "tools": tool_count,
                    "command": cfg.command,
                    "server_info": client.server_info,
                    "stderr_tail": client.stderr_tail[-5:],
                }
        return out

    # ---- internals ----

    def _register_tool_def(self, server_name: str, tool_def: dict[str, Any]) -> None:
        """Stash an MCP tool def for later shim creation."""
        if not isinstance(tool_def, dict):
            return
        mcp_name = tool_def.get("name")
        if not isinstance(mcp_name, str) or not mcp_name:
            return
        namespaced = _ns(server_name, mcp_name)
        self._tools[namespaced] = (server_name, mcp_name, tool_def)

    def _build_shim_tool(
        self,
        namespaced_name: str,
        server_name: str,
        mcp_name: str,
        tool_def: dict[str, Any],
    ) -> Tool:
        """Build a :class:`Tool` whose handler forwards to the MCP client."""
        description = tool_def.get("description", "") or ""
        # Prefix the description so the model knows where the tool came from
        # and that it's an MCP-backed tool.
        full_desc = f"[MCP:{server_name}] {description}".strip()
        params = _extract_params(tool_def.get("inputSchema", {}))

        # Capture the names in a closure (don't bind self in the handler —
        # the Tool may outlive the manager if the registry retains it).
        manager_ref = self

        def handler(**kwargs: Any) -> str:
            return manager_ref._invoke(server_name, mcp_name, namespaced_name, kwargs)

        return Tool(
            name=namespaced_name,
            description=full_desc,
            params=params,
            handler=handler,
            dangerous=False,  # MCP tools are sandboxed by their server
            category="mcp",
        )

    def _invoke(
        self,
        server_name: str,
        mcp_name: str,
        namespaced_name: str,
        args: dict[str, Any],
    ) -> str:
        """Forward a tool call to the MCP client and sanitize the result.

        This is the chokepoint that ensures MCP tool output goes through
        :func:`sanitize_tool_output` exactly like built-in tools. Never
        bypass this — the security primitive is the project's defense
        against prompt-injection via malicious tool output.
        """
        client = self._clients.get(server_name)
        if client is None:
            return f"[MCP error: server '{server_name}' is not connected]"

        try:
            result = client.call_tool(mcp_name, args)
        except MCPTransportError as e:
            # Transport failure — return a clean error string to the model
            # rather than raising. The agent loop treats raised exceptions
            # as tool-execution failures; a returned string lets the model
            # reason about the failure and try an alternative.
            return f"[MCP transport error: {e}]"
        except MCPClientError as e:
            return f"[MCP protocol error: {e}]"

        # Flatten the MCP CallToolResult into a string for sanitization
        text = _flatten_call_result(result)
        return sanitize_tool_output(
            text,
            tool_name=namespaced_name,
            tool_call_id="",  # filled in by the agent loop if available
        )


def _extract_params(input_schema: dict[str, Any]) -> list[ToolParam]:
    """Convert an MCP ``inputSchema`` JSON Schema into ToolParam list.

    We only handle the common case: ``type: object`` with ``properties``
    and ``required``. Anything more complex (oneOf, $ref, etc.) is
    flattened to a single string param named ``arguments_json`` by the
    caller — TODO for v0.2, not yet implemented.
    """
    if not isinstance(input_schema, dict):
        return []

    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    if not isinstance(props, dict):
        return []

    params: list[ToolParam] = []
    for prop_name, prop_schema in props.items():
        if not isinstance(prop_schema, dict):
            continue
        # JSON Schema type -> our simplified type system
        json_type = prop_schema.get("type", "string")
        if isinstance(json_type, list):
            # oneOf-style ["string", "null"] — pick the first non-null
            json_type = next((t for t in json_type if t != "null"), "string")
        type_map = {
            "string": "string",
            "integer": "number",
            "number": "number",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
        }
        param_type = type_map.get(json_type, "string")
        params.append(
            ToolParam(
                name=prop_name,
                type=param_type,
                description=str(prop_schema.get("description", "")),
                required=prop_name in required,
                default=prop_schema.get("default"),
                enum=prop_schema.get("enum"),
            )
        )
    return params


def _flatten_call_result(result: dict[str, Any]) -> str:
    """Convert an MCP ``CallToolResult`` into a flat string for sanitization.

    The MCP result is a list of content items; we concatenate text items
    and stringify non-text items. The ``isError`` flag is preserved as
    a prefix so the model can see whether the tool considers the call a
    failure.
    """
    if not isinstance(result, dict):
        return f"[MCP: non-object result: {type(result).__name__}]"

    parts: list[str] = []
    if result.get("isError"):
        parts.append("[MCP tool reported error]")

    content = result.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            item_type = item.get("type", "text")
            if item_type == "text":
                parts.append(str(item.get("text", "")))
            elif item_type == "image":
                # Drop image data — sanitize_tool_output would mangle it
                # anyway, and we don't yet support multimodal tool output.
                parts.append(f"[MCP image content: {item.get('mimeType', 'unknown')}]")
            elif item_type == "resource":
                # Resource reference — surface the URI for the model
                resource = item.get("resource", {})
                parts.append(f"[MCP resource: {resource.get('uri', '?')}]")
            else:
                parts.append(f"[MCP {item_type} content]")
    elif isinstance(content, str):
        # Some servers return a bare string instead of the structured shape
        parts.append(content)

    return "\n".join(p for p in parts if p)


__all__ = [
    "MCPManager",
    "MCPManagerError",
    "load_mcp_config",
    "MCPClient",
    "MCPClientError",
    "MCPServerConfig",
]
