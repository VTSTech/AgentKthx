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
        # R07.24 (MCP-04): lazy mode records configs without spawning.
        # Servers in this dict haven't been spawned yet; warmup_server()
        # moves them out of here and into self._clients on first use.
        self._lazy_configs: dict[str, MCPServerConfig] = {}
        # R07.24 (MCP-04): the target registry passed by register_into().
        # Stashed so warmup_server() can register tools into the SAME
        # live registry later (post-prompt-build).
        self._target_registry: ToolRegistry | None = None
        # R07.24 (MCP-05): optional per-server callback fired when
        # notifications/tools/list_changed arrives. Set by the agent
        # factory or any caller who wants to react to runtime tool
        # surface changes. The callback receives (server_name, added, removed)
        # where added/removed are lists of tool def dicts.
        self._on_tools_changed: "callable | None" = None

    # ---- lifecycle ----

    def connect_all(
        self,
        *,
        skip_failures: bool = True,
        verbose: bool = False,
        lazy: bool = False,
    ) -> list[tuple[str, str]]:
        """Connect to every configured server and enumerate their tools.

        Args:
            skip_failures: If True (default), a server that fails to
                connect or list tools is skipped with a warning. If False,
                the first failure raises :class:`MCPManagerError`.
            verbose: If True, print per-server progress to stderr so the
                operator sees what's happening during the (potentially
                slow) probe loop. Each server prints a "probing" line
                before connect, then a result line with the tool count
                or the error. Default False — silent for programmatic use.
            lazy: If True (R07.24, MCP-04), record configs without spawning
                any subprocesses. Servers stay deferred until
                :meth:`warmup_server` is called (either explicitly by the
                operator, or implicitly by :meth:`_invoke` on first tool
                dispatch to that server's namespace). The trade-off: the
                agent's system prompt will NOT include MCP tools for
                deferred servers — the model can't pick them until they
                warm up. Operators who want lazy startup AND prompt-time
                tool surface should warmup_server("<name>") before
                Agent.__init__ builds the prompt. Default False (eager).

        Returns:
            List of ``(server_name, error_message)`` for servers that
            failed and were skipped. Empty list if all servers connected
            (or if lazy=True — no failures because no spawns attempted).
        """
        failures: list[tuple[str, str]] = []
        with self._lock:
            if verbose:
                import sys

                names = ", ".join(self._configs.keys())
                count = len(self._configs)
                mode = "lazy (deferred)" if lazy else "eager"
                print(
                    f"[MCP] probing {count} server(s) [{mode}]: {names}",
                    file=sys.stderr,
                )
            if lazy:
                # R07.24 (MCP-04): record all configs as deferred. No
                # subprocess spawns, no tools/list, no shim Tool registration
                # — the operator must call warmup_server() per server
                # before the agent can use its tools (or _invoke will
                # warm up on first call, paying the spawn cost then).
                self._lazy_configs = dict(self._configs)
                self._connected = True  # mark connected so register_into() doesn't raise
                if verbose:
                    import sys

                    print(
                        f"[MCP]   {len(self._lazy_configs)} server(s) deferred — "
                        f"call warmup_server('<name>') to spawn on demand",
                        file=sys.stderr,
                    )
                return []
            for name, cfg in self._configs.items():
                if verbose:
                    import sys

                    print(f"[MCP]   {name}: connecting...", file=sys.stderr)
                try:
                    client = MCPClient(cfg)
                    # R07.24 (MCP-05): wire the list_changed notification
                    # handler BEFORE connect — connect sends initialize
                    # with listChanged=True capability, and some servers
                    # push an immediate list_changed after the handshake.
                    self._install_list_changed_handler(client, name)
                    client.connect()
                    tools = client.list_tools()
                    self._clients[name] = client
                    for tool_def in tools:
                        self._register_tool_def(name, tool_def)
                    if verbose:
                        print(
                            f"[MCP]   {name}: {len(tools)} tool(s)",
                            file=sys.stderr,
                        )
                except (MCPClientError, MCPTransportError) as e:
                    if not skip_failures:
                        raise MCPManagerError(f"MCP server '{name}' failed: {e}") from e
                    failures.append((name, str(e)))
                    if verbose:
                        print(
                            f"[MCP]   {name}: failed ({e}); skipped",
                            file=sys.stderr,
                        )
            self._connected = True
        return failures

    def warmup_server(self, name: str, *, verbose: bool = False) -> int:
        """R07.24 (MCP-04): spawn a deferred server + enumerate its tools.

        Moves the server from ``_lazy_configs`` to ``_clients`` and
        registers its tools as shim Tools via :meth:`_register_tool_def`.
        If :meth:`register_into` was called previously (the agent prompt
        is already built), the new tools are ALSO registered into the
        live ``_target_registry`` so the model can pick them on the
        next turn (the system prompt is rebuilt per-turn by the agent loop,
        so newly-registered tools surface naturally).

        Safe to call multiple times — a no-op if the server is already
        connected, raises if the server isn't configured at all.

        Returns:
            Number of tools discovered (and registered).

        Raises:
            MCPManagerError: If the server name isn't configured, or if
                connect + tools/list fails (no skip_failures here — the
                caller asked for a specific server, so failure is real).
        """
        with self._lock:
            if name in self._clients:
                # Already warmed up — return the existing tool count.
                return sum(1 for n, (s, _, _) in self._tools.items() if s == name)
            cfg = self._lazy_configs.pop(name, None) or self._configs.get(name)
            if cfg is None:
                raise MCPManagerError(
                    f"MCP server '{name}' is not configured (known: "
                    f"{list(self._configs.keys())})"
                )
            if verbose:
                import sys

                print(f"[MCP]   {name}: warmup → connecting...", file=sys.stderr)
            try:
                client = MCPClient(cfg)
                # R07.24 (MCP-05): wire list_changed handler before connect
                self._install_list_changed_handler(client, name)
                client.connect()
                tools = client.list_tools()
                self._clients[name] = client
                # Register tool defs in the manager's _tools dict + the
                # live target registry (if available) so newly-discovered
                # tools surface in the agent's next prompt.
                live_count = 0
                for tool_def in tools:
                    self._register_tool_def(name, tool_def)
                    if self._target_registry is not None:
                        ns_name = _ns(name, tool_def.get("name", ""))
                        if ns_name not in self._target_registry:
                            shim = self._build_shim_tool(
                                ns_name, name, tool_def.get("name", ""), tool_def
                            )
                            self._target_registry.register_tool(shim)
                            live_count += 1
                if verbose:
                    import sys

                    print(
                        f"[MCP]   {name}: warmup complete, {len(tools)} tool(s) "
                        f"({live_count} newly registered in live registry)",
                        file=sys.stderr,
                    )
                return len(tools)
            except (MCPClientError, MCPTransportError) as e:
                # Put the config back so a future warmup retry can attempt it.
                self._lazy_configs[name] = cfg
                raise MCPManagerError(f"MCP server '{name}' warmup failed: {e}") from e

    def _refresh_tools_for_server(self, name: str) -> tuple[list[dict], list[dict]]:
        """R07.24 (MCP-05): re-query ``tools/list`` and diff against the cached surface.

        Called when :class:`MCPClient` receives a
        ``notifications/tools/list_changed`` notification from the server.
        Returns ``(added, removed)`` where each is a list of tool def
        dicts. Shim Tools are added/removed in the live
        ``_target_registry`` before this returns so the next prompt
        rebuild sees the new state. If a callback was registered via
        :meth:`on_tools_changed`, it's called after the live registry
        is updated.

        No-op (returns ``([], [])``) if the server isn't connected or
        the tools/list call fails — a transient failure here shouldn't
        break the agent loop.
        """
        added: list[dict] = []
        removed: list[dict] = []
        with self._lock:
            client = self._clients.get(name)
            if client is None:
                return added, removed
            try:
                live_tools = client.list_tools()
            except (MCPClientError, MCPTransportError):
                # Transient — leave the cached surface alone and let the
                # next list_changed notification retry.
                return added, removed

            # Build sets of (mcp_tool_name) for diffing
            live_names = {t.get("name", "") for t in live_tools if isinstance(t, dict)}
            cached_names = {
                mcp_name for ns, (srv, mcp_name, _td) in self._tools.items() if srv == name
            }

            # Removed: in cache but not in live
            for ns, (srv, mcp_name, td) in list(self._tools.items()):
                if srv == name and mcp_name not in live_names:
                    removed.append(td)
                    del self._tools[ns]
                    if self._target_registry is not None:
                        unreg = getattr(self._target_registry, "unregister_tool", None)
                        if unreg is not None:
                            unreg(ns)

            # Added: in live but not in cache
            for td in live_tools:
                if not isinstance(td, dict):
                    continue
                mcp_name = td.get("name", "")
                if mcp_name and mcp_name not in cached_names:
                    added.append(td)
                    self._register_tool_def(name, td)
                    if self._target_registry is not None:
                        ns_name = _ns(name, mcp_name)
                        if ns_name not in self._target_registry:
                            shim = self._build_shim_tool(ns_name, name, mcp_name, td)
                            self._target_registry.register_tool(shim)

        # Fire the callback (if registered) AFTER the lock is released so
        # the callback can safely call back into the manager.
        if self._on_tools_changed is not None and (added or removed):
            try:
                self._on_tools_changed(name, added, removed)
            except Exception:
                pass  # callback failure must not break the agent loop

        return added, removed

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
            self._lazy_configs.clear()
            self._target_registry = None
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

        Note:
            R07.24 (MCP-04): in lazy mode, this returns 0 — no tools
            are registered until :meth:`warmup_server` is called. The
            registry passed here is stashed so warmup_server() can
            register tools into the SAME live registry later.
        """
        if not self._connected:
            raise MCPManagerError("MCPManager.connect_all() must be called before register_into()")

        # R07.24 (MCP-04): stash the target registry so warmup_server()
        # can register tools into the SAME live registry later. Without
        # this, tools discovered post-prompt-build wouldn't be visible
        # to the agent.
        self._target_registry = registry

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

    def unregister_tool(self, registry: ToolRegistry, namespaced_name: str) -> bool:
        """R07.24 (MCP-05): remove a shim Tool from the target registry.

        Called when a `notifications/tools/list_changed` notification
        reports a tool has been removed from the server's surface.
        Also removes the tool from this manager's `_tools` dict so a
        subsequent re-register doesn't bring it back.

        Returns:
            True if the tool was found and removed, False if not present.
        """
        with self._lock:
            if namespaced_name not in self._tools:
                return False
            del self._tools[namespaced_name]
            # Remove from the live registry if it has the unregister_tool method
            # (R07.24 added this to ToolRegistry — older instances don't have it).
            unregister = getattr(registry, "unregister_tool", None)
            if unregister is not None:
                unregister(namespaced_name)
            return True

    def on_tools_changed(self, callback: "callable | None") -> None:
        """R07.24 (MCP-05): register a callback for runtime tool surface changes.

        The callback fires when a server pushes
        `notifications/tools/list_changed` — the manager re-queries
        `tools/list`, diffs against the previous surface, and calls the
        callback with ``(server_name, added, removed)`` where
        ``added``/``removed`` are lists of tool def dicts. The shim
        Tools are added/removed in the target ToolRegistry before the
        callback fires so the callback can react to the live state.
        """
        self._on_tools_changed = callback

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

    def _install_list_changed_handler(self, client: MCPClient, name: str) -> None:
        """R07.24 (MCP-05): wire the per-server list_changed callback.

        The handler is a closure that captures ``name`` and trampolines
        into :meth:`_refresh_tools_for_server`. Re-queries ``tools/list``,
        diffs against the cached surface, adds/removes shim Tools in the
        live ``_target_registry``, and fires the user-registered
        ``on_tools_changed`` callback if any.

        Defensive: uses ``getattr`` so test fakes / mock clients that
        don't implement :meth:`MCPClient.set_notification_handler` are
        silently skipped (the original R07.22 behavior — notifications
        get logged to the stderr ring buffer by the transport).
        """
        # Capture `name` in the closure (not `self._config.name` — the
        # client may outlive the manager if the registry retains the shim
        # Tools after close_all, but the handler is only called while the
        # client is alive and connected).
        server_name = name

        def _on_list_changed(_msg: dict) -> None:
            self._refresh_tools_for_server(server_name)

        setter = getattr(client, "set_notification_handler", None)
        if setter is not None:
            setter("notifications/tools/list_changed", _on_list_changed)

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

        R07.24 (MCP-03): if the shim tool was built with the
        ``arguments_json`` fallback (the inputSchema had oneOf/anyOf/$ref
        or nested properties), the model passes a single string arg
        named ``arguments_json``. Parse it as JSON and forward the
        parsed object as the MCP ``arguments`` field. On parse failure,
        return a clean error string to the model — the JSON-shaped
        parameter description makes the expected shape clear.

        R07.24 (MCP-04): if the server is in ``_lazy_configs`` (lazy mode
        + not yet warmed up), warm it up on first dispatch. The spawn +
        initialize + tools/list latency (~200-500ms) is paid here on the
        first call to that server. If warmup fails, return a clean error
        string to the model rather than raising — the agent loop treats
        raised exceptions as tool-execution failures; a returned string
        lets the model reason about the failure and try an alternative.
        """
        # R07.24 (MCP-04): lazy warmup on first dispatch
        if server_name in self._lazy_configs:
            try:
                self.warmup_server(server_name)
            except MCPManagerError as e:
                return f"[MCP warmup error: {e}]"

        client = self._clients.get(server_name)
        if client is None:
            return f"[MCP error: server '{server_name}' is not connected]"

        # R07.24 (MCP-03): unwrap arguments_json if present
        forwarded_args: dict[str, Any]
        if "arguments_json" in args and len(args) == 1:
            # Fallback-shim path — the model passed a single JSON string
            import json as _json

            raw = args["arguments_json"]
            if not isinstance(raw, str):
                # Tolerant: some tool-call frameworks may pre-parse the JSON
                # for us. Forward as-is in that case.
                forwarded_args = raw if isinstance(raw, dict) else {"value": raw}
            else:
                try:
                    parsed = _json.loads(raw)
                except _json.JSONDecodeError as e:
                    return (
                        f"[MCP error: arguments_json is not valid JSON: {e}. "
                        f"Pass the full arguments object as a JSON string per "
                        f"the parameter description.]"
                    )
                if isinstance(parsed, dict):
                    forwarded_args = parsed
                else:
                    # The schema asked for an object but the model passed a
                    # bare string/number/array. Wrap it so the MCP server
                    # at least gets a structured argument (it will likely
                    # reject, but the error will be more informative than
                    # a TypeError on the .get() path).
                    forwarded_args = {"value": parsed}
        else:
            forwarded_args = args

        try:
            result = client.call_tool(mcp_name, forwarded_args)
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
    """Convert an MCP ``inputSchema`` JSON Schema into a ToolParam list.

    Handles the common case: ``type: object`` with ``properties`` and
    ``required``. Each property is mapped to a :class:`ToolParam` via the
    JSON-Schema-type → simplified-type mapping.

    R07.24 (MCP-03): when the schema contains constructs we can't
    structurally flatten — ``oneOf``, ``anyOf``, ``allOf``, ``$ref``,
    or nested ``properties`` deeper than one level — we fall back to a
    single ``arguments_json`` string parameter whose description carries
    the original schema as JSON. The model passes the full arguments
    object as a JSON string; :meth:`MCPClient.call_tool` parses it and
    forwards it as the ``arguments`` field. This preserves the rich
    schema (the model sees a JSON string with the original schema in
    its description) at the cost of slightly more prompt tokens.

    The detection is conservative: if ANY property has one of the
    un-flattenable constructs, the WHOLE tool falls back to
    ``arguments_json``. This is simpler than per-property fallback
    (mixed params + arguments_json is hard for the model to reason
    about) and matches the original finding's recommendation.
    """
    if not isinstance(input_schema, dict):
        return []

    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    if not isinstance(props, dict):
        return []

    # R07.24 (MCP-03): detect un-flattenable constructs. If any property
    # uses oneOf/anyOf/allOf/$ref or has nested properties deeper than
    # one level, the whole tool falls back to arguments_json.
    if _schema_has_complex_constructs(input_schema):
        return _build_arguments_json_fallback(input_schema)

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


def _schema_has_complex_constructs(input_schema: dict[str, Any]) -> bool:
    """R07.24 (MCP-03): detect oneOf/anyOf/allOf/$ref/nested-properties.

    Conservative — returns True if ANY property uses an un-flattenable
    construct. Triggers a whole-tool fallback to ``arguments_json``.
    """
    if not isinstance(input_schema, dict):
        return False

    # Top-level constructs (the schema itself is a union/ref)
    for key in ("oneOf", "anyOf", "allOf", "$ref", "$dynamicRef"):
        if key in input_schema:
            return True

    props = input_schema.get("properties", {})
    if not isinstance(props, dict):
        return False

    for prop_schema in props.values():
        if not isinstance(prop_schema, dict):
            continue
        # Per-property union/ref constructs
        for key in ("oneOf", "anyOf", "allOf", "$ref", "$dynamicRef"):
            if key in prop_schema:
                return True
        # Nested properties deeper than one level — a property whose
        # type is "object" with its own "properties" dict can't be
        # expressed as a flat ToolParam.
        if prop_schema.get("type") == "object":
            nested_props = prop_schema.get("properties")
            if isinstance(nested_props, dict) and nested_props:
                return True
        # Array of objects also has this shape (items.properties)
        if prop_schema.get("type") == "array":
            items = prop_schema.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                nested_items_props = items.get("properties")
                if isinstance(nested_items_props, dict) and nested_items_props:
                    return True
    return False


def _build_arguments_json_fallback(input_schema: dict[str, Any]) -> list[ToolParam]:
    """R07.24 (MCP-03): emit a single ``arguments_json`` string parameter.

    The model passes the full arguments object as a JSON string; the
    MCPClient parses it and forwards it as the ``arguments`` field. The
    parameter description carries the original schema so the model can
    reason about the expected shape.
    """
    import json as _json

    schema_json = _json.dumps(input_schema, indent=2, ensure_ascii=False)
    description = (
        "Pass the full arguments object as a JSON string. The original "
        "inputSchema contained constructs (oneOf/anyOf/allOf/$ref/nested "
        "properties) that this client cannot flatten to per-field params; "
        "the schema is included here for reference.\n\n"
        f"Original inputSchema:\n```json\n{schema_json}\n```"
    )
    return [
        ToolParam(
            name="arguments_json",
            type="string",
            description=description,
            required=True,
        )
    ]


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
