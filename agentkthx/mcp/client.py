"""
⚛️ AgentKthx — MCP client (per-server)

Wraps a :class:`StdioTransport` and exposes the MCP protocol methods we
care about for v0.1: ``initialize``, ``tools/list``, ``tools/call``.

Methods
-------
* :meth:`MCPClient.connect` — send ``initialize``, then ``notifications/initialized``
* :meth:`MCPClient.list_tools` — call ``tools/list``, return raw tool defs
* :meth:`MCPClient.call_tool` — call ``tools/call`` with name + args, return result
* :meth:`MCPClient.close` — shut down the transport

The client is **stateless between calls** aside from the underlying
transport and the server's declared capabilities.

R07.24 (MCP-05): the client now supports registering per-method
notification handlers via :meth:`set_notification_handler`. When the
transport reads a notification (a JSON-RPC message with no ``id``
field), the client routes it to the registered handler if one exists;
otherwise the notification is logged to the stderr ring buffer (the
original R07.22 behavior). The manager wires
``notifications/tools/list_changed`` to its
:meth:`MCPManager._refresh_tools_for_server` method on connect.
"""

from __future__ import annotations

from typing import Any, Callable

from .config import MCPServerConfig
from .transport import StdioTransport

# Protocol version we speak. MCP is still pre-1.0 in the wild; this is
# the version the reference servers expect as of 2026-Q4.
_MCP_PROTOCOL_VERSION = "2025-06-18"

_CLIENT_NAME = "agentkthx"
_CLIENT_VERSION = "0.7.27"  # mirror __init__ package version


class MCPClientError(RuntimeError):
    """Raised when an MCP server returns a protocol-level error.

    Distinct from :class:`MCPTransportError` (subprocess / IO failure)
    and from generic ``Exception`` (tool execution errors, which are
    returned to the model as part of the tool result, not raised).
    """


class MCPClient:
    """One MCP server connection. Not thread-safe; use one per server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._transport = StdioTransport(config)
        self._initialized = False
        self._server_caps: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        # R07.24 (MCP-05): per-method notification handlers.
        # Keyed by JSON-RPC method name (e.g. "notifications/tools/list_changed").
        # The handler receives the full message dict (params + method).
        self._notification_handlers: dict[str, Callable[[dict], None]] = {}

    def set_notification_handler(self, method: str, handler: Callable[[dict], None] | None) -> None:
        """R07.24 (MCP-05): register a callback for a JSON-RPC notification method.

        When the transport reads a notification (a message with no ``id``
        field), the client routes it to the registered handler if one
        exists for that method. Pass ``handler=None`` to remove a
        previously-registered handler.

        The handler is called from the transport's reader thread context
        — keep it fast and non-blocking. If it raises, the exception is
        swallowed (logged to the stderr ring) so a buggy handler doesn't
        kill the transport.

        The standard MCP notification we care about is
        ``notifications/tools/list_changed`` — the manager wires this to
        :meth:`MCPManager._refresh_tools_for_server`.
        """
        if handler is None:
            self._notification_handlers.pop(method, None)
        else:
            self._notification_handlers[method] = handler

    def handle_notification(self, msg: dict) -> bool:
        """R07.24 (MCP-05): route a notification to its registered handler.

        Called by the transport when it reads a notification (no ``id``
        field). Returns True if a handler was registered for the method
        and ran (or raised); False if no handler was registered (the
        transport logs the notification to the stderr ring buffer in
        that case, preserving the R07.22 behavior).

        Handlers run in the caller's context (the transport's reader
        thread for inline notifications, the main thread for
        inter-call notifications drained via :meth:`drain_notifications`).
        Exceptions are swallowed so a buggy handler doesn't kill the
        transport.
        """
        method = msg.get("method", "")
        handler = self._notification_handlers.get(method)
        if handler is None:
            return False
        try:
            handler(msg)
        except Exception:
            # Buggy handler — log + continue. The transport must not die.
            import sys

            print(
                f"[MCP] notification handler for {method!r} raised; "
                f"swallowing (handler bug, not a transport fault)",
                file=sys.stderr,
            )
        return True

    # ---- lifecycle ----

    def connect(self) -> None:
        """Send the MCP ``initialize`` handshake.

        Idempotent — calling twice is a no-op after the first success.

        R07.24 (MCP-05): also installs a notification trampoline on the
        underlying transport so JSON-RPC notifications (e.g.
        ``notifications/tools/list_changed``) get routed to per-method
        handlers registered via :meth:`set_notification_handler`. Before
        R07.24, notifications were silently logged to the stderr ring
        buffer; now they fire the registered handler (if any).

        Raises:
            MCPClientError: On protocol mismatch or failed handshake.
            MCPTransportError: On subprocess / IO failure.
        """
        if self._initialized:
            return

        # R07.24 (MCP-05): wire the transport's notification callback to
        # our trampoline BEFORE sending initialize — servers may push
        # ``notifications/initialized`` ack (no, we send that), but more
        # importantly, some servers push ``notifications/tools/list_changed``
        # immediately after init if their tool surface is dynamic. Wiring
        # before init guarantees we don't miss early notifications.
        self._transport.set_notification_callback(self.handle_notification)

        result = self._transport.request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "clientInfo": {
                    "name": _CLIENT_NAME,
                    "version": _CLIENT_VERSION,
                },
                "capabilities": {
                    # We support nothing beyond the base protocol for v0.1.
                    # R07.24 (MCP-05): listChanged=True tells the server
                    # we want notifications/tools/list_changed pushes.
                    "tools": {"listChanged": True},
                },
            },
        )

        if not isinstance(result, dict):
            raise MCPClientError(
                f"MCP server '{self._config.name}': initialize returned "
                f"non-object: {type(result).__name__}"
            )

        # The server may negotiate a different protocol version. For v0.1
        # we accept any version the server offers — we don't have features
        # that depend on a specific version yet. Record it for diagnostics.
        self._server_info = result.get("serverInfo", {})
        self._server_caps = result.get("capabilities", {})

        # Send the initialized notification (no response expected)
        self._transport.notify("notifications/initialized")

        self._initialized = True

    def close(self, timeout: float = 2.0) -> None:
        """Close the underlying transport. Safe to call multiple times."""
        self._initialized = False
        self._transport.close(timeout=timeout)

    # ---- protocol methods ----

    def list_tools(self) -> list[dict[str, Any]]:
        """Call ``tools/list`` and return the raw tool def array.

        Each entry looks like:

        .. code-block:: json

            {
              "name": "read_file",
              "description": "Read a file from the filesystem",
              "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
              }
            }

        Returns:
            List of tool def dicts. Empty list if the server exposes no tools.

        Raises:
            MCPClientError: If the server returns a non-list response.
            MCPTransportError: On transport failure.
        """
        result = self._transport.request("tools/list")
        if not isinstance(result, dict):
            raise MCPClientError(
                f"MCP server '{self._config.name}': tools/list returned "
                f"non-object: {type(result).__name__}"
            )
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise MCPClientError(f"MCP server '{self._config.name}': 'tools' field is not a list")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call ``tools/call`` and return the structured result.

        Args:
            name: Tool name as returned by :meth:`list_tools`.
            arguments: Tool arguments (may be empty or None).

        Returns:
            The MCP ``CallToolResult`` object. Shape::

                {
                  "content": [
                    {"type": "text", "text": "..."},
                    ...
                  ],
                  "isError": false  // optional
                }

        Raises:
            MCPClientError: If the server returns a non-object response.
            MCPTransportError: On transport failure.

        Note:
            Tool *execution* errors (e.g., file-not-found from the
            filesystem server) are NOT raised — they're returned as
            ``{"isError": true, "content": [...]}`` so the model can
            see and react to them. Only protocol/transport errors raise.
        """
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments

        result = self._transport.request("tools/call", params)
        if not isinstance(result, dict):
            raise MCPClientError(
                f"MCP server '{self._config.name}': tools/call returned "
                f"non-object: {type(result).__name__}"
            )
        return result

    # ---- introspection ----

    @property
    def server_name(self) -> str:
        return self._config.name

    @property
    def server_info(self) -> dict[str, Any]:
        """Server-reported info dict (name, version) from ``initialize``."""
        return dict(self._server_info)

    @property
    def server_capabilities(self) -> dict[str, Any]:
        """Server-reported capabilities dict from ``initialize``."""
        return dict(self._server_caps)

    @property
    def stderr_tail(self) -> list[str]:
        """Recent stderr lines from the subprocess (for diagnostics)."""
        return self._transport.stderr_tail

    @property
    def is_alive(self) -> bool:
        return self._transport.is_alive

    def __repr__(self) -> str:
        state = "connected" if self._initialized else "disconnected"
        return f"<MCPClient server={self._config.name!r} {state}>"
