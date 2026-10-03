"""`agentkthx mcp` subcommand (R07.22 Phase 1.6).

Three actions, all using only stdlib + the existing ``agentkthx.mcp`` package:

* ``agentkthx mcp list``   — show configured MCP servers
* ``agentkthx mcp init``   — write a commented example ``~/.agentkthx/mcp.json``
* ``agentkthx mcp probe <name>`` — connect to one server, list its tools, exit

The probe subcommand is the most useful for debugging: it answers
"can AgentKthx actually talk to this MCP server, and what tools does it
expose?" without launching a full chat session. Optional ``--call TOOL
JSON_ARGS`` round-trips a real tool call so operators can verify the
result format end-to-end.

This module deliberately avoids importing anything heavy at module scope
— the only top-level imports are stdlib. The ``agentkthx.mcp`` package
loads lazily inside each handler so a broken MCP install doesn't break
``agentkthx mcp init`` (which writes a config file the user might need
precisely because MCP isn't working yet).
"""

from __future__ import annotations

import argparse
import json
import os

from ...colors import bold, cyan, dim, green, red, yellow


def cmd_mcp(args: argparse.Namespace) -> int:
    """Dispatch the mcp subcommand."""
    sub = getattr(args, "mcp_command", None)
    if sub == "list":
        return _mcp_list(args)
    if sub == "init":
        return _mcp_init(args)
    if sub == "probe":
        return _mcp_probe(args)
    # No subcommand → print help
    print(bold("\n⚛️  AgentKthx MCP") + dim(" · Model Context Protocol client management"))
    print()
    print("  " + cyan("agentkthx mcp list") + dim("              — show configured servers"))
    print(
        "  "
        + cyan("agentkthx mcp init")
        + dim("              — write example ~/.agentkthx/mcp.json")
    )
    print("  " + cyan("agentkthx mcp probe <name>") + dim("    — connect + list tools"))
    print()
    print(dim("  Use --mcp on chat/run/agent to enable servers in a session."))
    print(dim("  See docs/mcp/ROADMAP.md for the full MCP plan."))
    print()
    return 0


def _mcp_list(args: argparse.Namespace) -> int:
    """Print the configured MCP servers."""
    try:
        from ...mcp import default_config_path, load_mcp_config
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    cfg_path = default_config_path()
    if not cfg_path.exists():
        print(yellow(f"No MCP config at {cfg_path}"))
        print()
        print(f"  Run {cyan('agentkthx mcp init')} to create one.")
        return 0

    try:
        configs = load_mcp_config(cfg_path)
    except Exception as e:
        print(f"{red('Error:')} config load failed: {e}")
        return 1

    print(bold("\n⚛️  MCP servers") + dim(f" · {cfg_path}"))
    print(dim("─" * 74))

    if not configs:
        print(yellow("  No enabled servers in config."))
        print(dim('  Edit the file to set "enabled": true on the servers you want.'))
        return 0

    name_w = max(len(c.name) for c in configs)
    for c in configs:
        # Show enabled state + command + first arg (usually the package name)
        marker = green("✓") if c.enabled else dim("○")
        argv_preview = " ".join([c.command, *c.args[:2]])
        if len(argv_preview) > 50:
            argv_preview = argv_preview[:47] + "..."
        timeout_str = f"{c.timeout_seconds}s" if c.timeout_seconds != 30 else ""
        print(f"  {marker} {cyan(c.name.ljust(name_w))}  {dim(argv_preview)} {dim(timeout_str)}")

    print(dim("─" * 74))
    print()
    print(
        dim(
            f"  Enable in chat:  {cyan('agentkthx chat --mcp')} (all) or {cyan('--mcp fs git')} (subset)"
        )
    )
    print(dim(f"  Probe one:       {cyan('agentkthx mcp probe <name>')}"))
    print()
    return 0


def _mcp_init(args: argparse.Namespace) -> int:
    """Write the example config to the default or specified path."""
    try:
        from ...mcp import MCPConfigError, default_config_path, write_example_config
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    target = args.path or str(default_config_path())
    if os.path.exists(target) and not args.force:
        print(f"{red('Error:')} {target} already exists (use --force to overwrite)")
        return 1

    try:
        if args.force and os.path.exists(target):
            os.remove(target)
        written = write_example_config(target)
    except MCPConfigError as e:
        print(f"{red('Error:')} {e}")
        return 1
    except OSError as e:
        print(f"{red('Error:')} cannot write {target}: {e}")
        return 1

    print(green(f"Wrote: {written}"))
    print()
    print(dim("The config uses your actual home directory — no editing required."))
    print(dim("Two directories were created for you:"))
    print(dim("  ~/projects/  — drop files here for the filesystem MCP server"))
    print(dim("  ~/repo/      — target for the (Phase 2) kthx-audit MCP server"))
    print()
    print(dim("Servers enabled by default:"))
    print(dim("  • filesystem           — sandboxed file ops (~/projects)"))
    print(dim("  • sequential-thinking  — chain-of-thought scratchpad"))
    print()
    print(dim(f"Probe one now:    {cyan('agentkthx mcp probe filesystem')}"))
    print(dim(f"Enable in chat:   {cyan('agentkthx chat --mcp')}"))
    print(dim(f"Edit to add more: {cyan(str(target))}"))
    return 0


def _mcp_probe(args: argparse.Namespace) -> int:
    """Connect to one server, list its tools, optionally call one."""
    try:
        from ...mcp import MCPClient, MCPClientError, MCPTransportError, load_mcp_config
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    try:
        configs = load_mcp_config(args.config)
    except Exception as e:
        print(f"{red('Error:')} config load failed: {e}")
        return 1

    match = next((c for c in configs if c.name == args.name), None)
    if match is None:
        available = ", ".join(c.name for c in configs) or "(none)"
        print(f"{red('Error:')} server {args.name!r} not in config (available: {available})")
        return 1

    client = MCPClient(match)
    print(bold(f"\n⚛️  Probing MCP server: {cyan(match.name)}"))
    print(dim(f"  command: {match.command} {' '.join(match.args)}"))
    print()

    try:
        client.connect()
    except (MCPClientError, MCPTransportError) as e:
        print(f"{red('Connect failed:')} {e}")
        # Surface stderr if we got any
        tail = client.stderr_tail
        if tail:
            print(dim("  stderr tail:"))
            for line in tail[-5:]:
                print(dim(f"    {line}"))
        return 2

    print(green("✓ connected"))
    info = client.server_info
    if info:
        print(dim(f"  server: {info.get('name', '?')} v{info.get('version', '?')}"))
    caps = client.server_capabilities
    if caps:
        cap_keys = list(caps.keys())
        print(dim(f"  caps:   {', '.join(cap_keys)}"))
    print()

    # List tools
    try:
        tools = client.list_tools()
    except (MCPClientError, MCPTransportError) as e:
        print(f"{red('tools/list failed:')} {e}")
        return 3

    if not tools:
        print(yellow("  Server exposes no tools."))
    else:
        print(bold(f"Tools ({len(tools)}):"))
        print(dim("─" * 74))
        for t in tools:
            name = t.get("name", "?")
            desc = (t.get("description") or "").split("\n")[0][:60]
            print(f"  {cyan(name)}  {dim(desc)}")
        print(dim("─" * 74))
        print()

    # Optional tool call
    if args.call:
        tool_name, json_args = args.call
        try:
            parsed_args = json.loads(json_args)
            if not isinstance(parsed_args, dict):
                raise ValueError("args must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"{red('Error:')} --call JSON_ARGS parse failed: {e}")
            return 4

        print(bold(f"\nCalling: {cyan(tool_name)}"))
        print(dim(f"  args: {json.dumps(parsed_args)}"))
        print()
        try:
            result = client.call_tool(tool_name, parsed_args)
        except (MCPClientError, MCPTransportError) as e:
            print(f"{red('tools/call failed:')} {e}")
            return 5

        # Flatten the result for display
        from ...mcp.manager import _flatten_call_result

        text = _flatten_call_result(result)
        print(green("✓ result:"))
        # Indent multi-line results for readability
        for line in text.split("\n"):
            print(f"  {line}")

    print()
    client.close()
    print(dim(f"Disconnected from {match.name}."))
    return 0
