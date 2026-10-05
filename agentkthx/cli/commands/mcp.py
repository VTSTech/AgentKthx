"""`agentkthx mcp` subcommand (R07.22 Phase 1.6 + R07.23 live registry).

Five actions, all using only stdlib + the existing ``agentkthx.mcp`` package:

* ``agentkthx mcp list``    — show configured MCP servers
* ``agentkthx mcp init``    — write a commented example ``~/.agentkthx/mcp.json``
* ``agentkthx mcp probe <name>`` — connect to one server, list its tools, exit
* ``agentkthx mcp search [query]`` — live search across npm for MCP
  servers. Results are cached for 10 minutes in ``~/.agentkthx/mcp_cache.json``
  (R07.23, replaces the offline catalog that briefly shipped mid-release).
  GitHub search was removed (R07.23 follow-up): too many non-stdio results;
  ``mcp install github:owner/repo`` still works for verified stdio-capable repos.
* ``agentkthx mcp install <name>`` — fetch live metadata for one server and
  write it directly to ``~/.agentkthx/mcp.json``. Always live (no offline
  fallback); overwrites existing entries with the same name by default
  (R07.23).

The probe subcommand is the most useful for debugging: it answers
"can AgentKthx actually talk to this MCP server, and what tools does it
expose?" without launching a full chat session. Optional ``--call TOOL
JSON_ARGS`` round-trips a real tool call so operators can verify the
result format end-to-end.

The search subcommand lets operators discover servers without leaving the
terminal. The live sources are npm (``registry.npmjs.org``) and GitHub
(``api.github.com/search/repositories``); both are hit in parallel and
results are deduped by package identifier. GitHub search works
anonymously at 10 req/min; set ``AGENTKTHX_GITHUB_TOKEN`` for 5000/min.

The install subcommand populates ``~/.agentkthx/mcp.json`` directly —
no copy-paste from search output required. Short names (e.g. "filesystem")
are resolved via npm search, preferring ``@modelcontextprotocol/*``
packages. Full npm package names (e.g.
``@modelcontextprotocol/server-filesystem``) are fetched directly.
GitHub repos (e.g. ``oraios/serena``) require ``--command`` and ``--args``
since the install command varies by repo.

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
    if sub == "search":
        return _mcp_search(args)
    if sub == "install":
        return _mcp_install(args)
    if sub == "uninstall":
        return _mcp_uninstall(args)
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
    print("  " + cyan("agentkthx mcp search [query]") + dim("  — live search npm (10m cache)"))
    print(
        "  "
        + cyan("agentkthx mcp install <name>")
        + dim("   — install a server to ~/.agentkthx/mcp.json")
    )
    print(
        "  "
        + cyan("agentkthx mcp uninstall <name>")
        + dim(" — remove a server from ~/.agentkthx/mcp.json")
    )
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
            f"  Enable in chat:  {cyan('agentkthx chat --mcp')} (all) or {cyan('--mcp filesystem sequential-thinking')} (subset)"
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
    print(dim("One directory was created for you:"))
    print(dim("  ~/workspace/  — drop files here for the filesystem MCP server"))
    print(dim("                — also the target for the (Phase 2) kthx-audit MCP server"))
    print()
    print(dim("Servers enabled by default:"))
    print(dim("  • filesystem           — sandboxed file ops (~/workspace)"))
    print(dim("  • sequential-thinking  — chain-of-thought scratchpad"))
    print(dim("  • memory               — knowledge graph (persists to ~/.agentkthx/memory.json)"))
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


def _mcp_search(args: argparse.Namespace) -> int:
    """Live search npm for MCP servers (R07.23).

    Replaces the curated offline catalog with a live registry
    search. Results are cached for 10 minutes in
    ``~/.agentkthx/mcp_cache.json`` so repeated calls within the window
    don't re-hit the network.

    GitHub search was removed (R07.23 follow-up): too many non-stdio
    results (Java/Go/Rust repos, Ghidra/IDA extensions, browser plugins)
    that look like MCP servers but can't be launched as subprocesses.
    GitHub installs still work via ``mcp install github:owner/repo`` when
    the operator has manually verified the repo is stdio-capable.

    Usage::

        agentkthx mcp search              # default query: "mcp"
        agentkthx mcp search filesystem   # substring search
        agentkthx mcp search --refresh    # bypass cache
        agentkthx mcp search --json       # machine-readable
    """
    try:
        from ...mcp import get_cached, set_cached
        from ...mcp.registry import MCPRegistryError, npm_search
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    query = args.query or "mcp"
    limit = getattr(args, "limit", 25) or 25
    refresh = getattr(args, "refresh", False)

    cache_key = f"search:npm:{query}:{limit}"
    results: list | None = None
    if not refresh:
        hit, cached = get_cached(cache_key)
        if hit and isinstance(cached, list):
            results = cached

    errors: list[str] = []
    if results is None:
        results = []
        try:
            results = npm_search(query, size=limit)
        except MCPRegistryError as e:
            errors.append(str(e))
        # Cache the results (even if empty, to avoid re-hitting on repeats)
        try:
            set_cached(cache_key, results)
        except Exception:
            pass  # cache write failure is non-fatal

    if args.json:
        payload = {
            "query": query,
            "source": "npm",
            "results": results,
        }
        if errors:
            payload["errors"] = errors
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not results:
        print(yellow(f"  No MCP servers found for {query!r}."))
        if errors:
            print()
            print(red("  Errors:"))
            for e in errors:
                print(red(f"    {e}"))
        print()
        print(
            dim("  Try a broader query, or run `agentkthx mcp search mcp` to list popular servers.")
        )
        return 0

    print(
        bold("\n⚛️  MCP servers")
        + dim(f" · {len(results)} result{'s' if len(results)!=1 else ''} for ")
        + cyan(query)
        + dim(" (source: npm)")
    )
    if errors:
        print(yellow(f"  ⚠ {len(errors)} source error(s) — partial results"))
    print(dim("─" * 74))

    name_w = max(len(str(r.get("name", ""))) for r in results)
    for r in results:
        name = str(r.get("name", ""))
        pkg = str(r.get("package", ""))
        desc = str(r.get("description", ""))[:60]
        if len(desc) == 60:
            desc = desc[:57] + "..."
        is_official = r.get("is_official", False)
        version = r.get("version", "")

        marker = green("✓") if is_official else dim("·")
        ver_str = dim(f" v{version}") if version and version != "unknown" else ""
        print(f"  {marker} {cyan(name.ljust(name_w))}  {dim(pkg[:50])}  {ver_str}")
        if desc:
            print(f"      {' ' * name_w}  {dim(desc)}")

    print(dim("─" * 74))
    print()
    print(dim(f"  Install:  {cyan('agentkthx mcp install <name>')}"))
    print(dim(f"  Refresh:  {cyan('agentkthx mcp search --refresh <query>')}"))
    print(dim(f"  As JSON:  {cyan('agentkthx mcp search <query> --json')}"))
    print()
    return 0


def _mcp_install(args: argparse.Namespace) -> int:
    """Install an MCP server to ~/.agentkthx/mcp.json (R07.23).

    Always live — fetches fresh metadata from npm (or uses GitHub repo
    metadata) before writing. Overwrites any existing entry with the
    same name (per maintainer spec; print a warning so the user knows).

    Name formats (R07.23 redesign — avoids collisions since many MCP
    servers share project names like "ghidra", "git", "memory")::

        @scope/package                     → npm install
        github:owner/repo                  → GitHub install
        owner/repo                         → GitHub install (shorthand)
        bare-name                          → npm search, prefers @modelcontextprotocol/*

    Examples::

        agentkthx mcp install @modelcontextprotocol/server-filesystem
        agentkthx mcp install github:LaurieWired/GhidraMCP
        agentkthx mcp install oraios/serena --command uvx --args='--from git+https://github.com/oraios/serena serena start-mcp-server'
        agentkthx mcp install filesystem    # bare name → npm search
        agentkthx mcp install <name> --dry-run
        agentkthx mcp install <name> --json
        agentkthx mcp install <name> --as myfs
    """
    try:
        from ...mcp import default_config_path
        from ...mcp.registry import (
            MCPRegistryError,
            build_config_snippet,
            npm_package_info,
            npm_search,
        )
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    name = args.name
    as_name = getattr(args, "as_name", None)
    override_command = getattr(args, "launch_command", None)
    override_args_str = getattr(args, "args", None)
    # --args is a single string; split on spaces for the launch argv
    override_args = override_args_str.split() if override_args_str else None
    dry_run = getattr(args, "dry_run", False)
    as_json = getattr(args, "json", False)
    cfg_path = getattr(args, "config", None) or str(default_config_path())

    # Suppress progress messages when --json is set (so output is parseable JSON)
    def _progress(msg: str) -> None:
        if not as_json:
            print(dim(msg))

    # Detect what kind of name we have. The format determines which registry
    # to hit and how to build the config snippet.
    #
    #   - "@scope/package"             → npm (scoped package)
    #   - "github:owner/repo"           → GitHub (explicit prefix)
    #   - "owner/repo"                  → GitHub (shorthand — has slash, no @, no spaces)
    #   - bare name (e.g. "filesystem") → npm search (resolves to a package)
    #
    # The collision problem the maintainer flagged: many MCP servers share
    # project names (e.g. "ghidra" appears 5+ times in search results).
    # Requiring the full @scope/package or github:owner/repo format makes
    # the install target unambiguous. Bare names still work via npm search
    # but the user is warned to prefer the full form.
    snippet: dict | None = None
    resolved_package = name

    is_npm_scoped = name.startswith("@") and "/" in name
    is_github_prefixed = name.startswith("github:")
    is_github_shorthand = not name.startswith("@") and "/" in name and " " not in name

    if is_npm_scoped:
        # @scope/package → npm direct fetch
        _progress(f"  Fetching npm metadata for {name!r}...")
        try:
            info = npm_package_info(name)
        except MCPRegistryError as e:
            print(f"{red('Error:')} npm lookup failed: {e}")
            return 2
        if info is None:
            print(f"{red('Error:')} package {name!r} not found on npm (404)")
            return 3
        snippet = build_config_snippet(
            package=info["package"],
            short_name=as_name,
            source="npm",
            version=info.get("version", ""),
            description=info.get("description", ""),
            command=override_command,
            args=override_args,
        )
    elif is_github_prefixed:
        # github:owner/repo → GitHub install
        # Strip the "github:" prefix; the rest is owner/repo
        repo = name[len("github:") :]
        resolved_package = f"github:{repo}"
        _progress(f"  Building GitHub snippet for {repo!r}...")
        snippet = build_config_snippet(
            package=f"github:{repo}",
            short_name=as_name,
            source="github",
            command=override_command,
            args=override_args,
        )
    elif is_github_shorthand:
        # owner/repo → GitHub install (shorthand for github:owner/repo)
        resolved_package = f"github:{name}"
        _progress(f"  Building GitHub snippet for {name!r}...")
        snippet = build_config_snippet(
            package=f"github:{name}",
            short_name=as_name,
            source="github",
            command=override_command,
            args=override_args,
        )
    else:
        # Bare name → npm search, prefer @modelcontextprotocol/* hits
        _progress(f"  Resolving {name!r} via npm search...")
        if not as_json:
            print(
                yellow(
                    "  ⚠ Tip: bare names can match multiple packages. "
                    "For unambiguous installs, use the full form:"
                )
            )
            print(dim(f"      {cyan('mcp install @scope/package')}   (npm)"))
            print(dim(f"      {cyan('mcp install github:owner/repo')} (GitHub)"))
        try:
            results = npm_search(name, size=10)
        except MCPRegistryError as e:
            print(f"{red('Error:')} npm search failed: {e}")
            return 2
        # Prefer official packages, then any MCP-relevant hit
        official = [r for r in results if r.get("is_official")]
        match = official[0] if official else (results[0] if results else None)
        if match is None:
            print(f"{red('Error:')} no npm package matched {name!r}")
            print(
                dim(
                    f"  Try: {cyan('mcp install @modelcontextprotocol/server-' + name)} "
                    f"or {cyan('mcp search ' + name)}"
                )
            )
            return 3
        resolved_package = match["package"]
        # Fetch full metadata for the resolved package
        try:
            info = npm_package_info(resolved_package)
        except MCPRegistryError as e:
            print(f"{yellow('Warning:')} could not fetch full metadata: {e}")
            info = None
        meta = info if info is not None else match
        snippet = build_config_snippet(
            package=meta.get("package", resolved_package),
            short_name=as_name,
            source="npm",
            version=meta.get("version", ""),
            description=meta.get("description", ""),
            command=override_command,
            args=override_args,
        )

    if as_json:
        print(json.dumps(snippet, indent=2, ensure_ascii=False))
        return 0

    if dry_run:
        print(bold(f"\n\u269b\ufe0f  Dry run: snippet for {cyan(snippet['name'])}"))
        print(dim("\u2500" * 74))
        print(json.dumps(snippet, indent=2, ensure_ascii=False))
        print(dim("\u2500" * 74))
        print(dim(f"  Would write to: {cfg_path}"))
        print(dim(f"  To install for real: {cyan('agentkthx mcp install ' + name)}"))
        _check_launch_command(snippet["command"], as_json=as_json)
        return 0

    # Pre-flight: check if the launch command is on $PATH. Warn (don't fail)
    # if it's missing — the user may install it later, or override with --command.
    _check_launch_command(snippet["command"], as_json=as_json)

    # Write to mcp.json \u2014 overwrite any existing entry with the same name
    import json as _json
    import os
    from pathlib import Path

    cfg_file = Path(cfg_path)
    if cfg_file.exists():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if not isinstance(data, dict):
                print(f"{red('Error:')} {cfg_file} is not a JSON object")
                return 4
        except (OSError, _json.JSONDecodeError) as e:
            print(f"{red('Error:')} cannot read {cfg_file}: {e}")
            return 4
    else:
        data = {"version": "0.1", "servers": []}

    servers = data.get("servers", [])
    if not isinstance(servers, list):
        print(f"{red('Error:')} {cfg_file} 'servers' is not an array")
        return 4

    # Find + replace existing entry with the same name (overwrite by default)
    short_name = snippet["name"]
    replaced = False
    for i, s in enumerate(servers):
        if isinstance(s, dict) and s.get("name") == short_name:
            servers[i] = snippet
            replaced = True
            break
    if not replaced:
        servers.append(snippet)
    data["servers"] = servers

    # Write back atomically
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_suffix(cfg_file.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, cfg_file)

    print(green(f"\u2713 Installed: {cyan(short_name)}"))
    action = "overwrote" if replaced else "added"
    print(dim(f"  {action} entry in {cfg_file}"))
    print(dim(f"  package: {resolved_package}"))
    print(dim(f"  command: {snippet['command']} {' '.join(snippet['args'])}"))
    # For GitHub installs, warn that the launch command is a best-effort
    # guess \u2014 many GitHub MCP servers are Node.js/Java/Go/Rust, not
    # Python, so `uvx` won't work. They should check the repo's README.
    if resolved_package.startswith("github:"):
        repo_url = resolved_package.replace("github:", "https://github.com/")
        print()
        print(yellow("  \u26a0  GitHub install commands are a best-effort guess."))
        print(dim("    `uvx --from git+...` only works for Python packages."))
        print(dim("    If the repo is Java/Go/Rust/Node.js or a host plugin"))
        print(dim("    (Ghidra/IDA extension, browser ext, etc.), this won't work."))
        print(dim(f"    Check the README: {repo_url}"))
        print(dim("    Then override with --command and --args, or uninstall with:"))
        print(dim(f"      {cyan('agentkthx mcp uninstall ' + snippet['name'])}"))
    print()
    print(dim(f"  Probe:   {cyan('agentkthx mcp probe ' + short_name)}"))
    print(dim(f"  Enable:  {cyan('agentkthx chat --mcp ' + short_name)}"))
    return 0


def _check_launch_command(command: str, *, as_json: bool = False) -> None:
    """Warn (don't fail) if the launch command isn't on $PATH.

    Printed to stderr so it doesn't break --json output on stdout.
    Provides install hints for the common missing commands (uvx, npx, uv).
    """
    import shutil
    import sys

    if not command:
        return
    # Don't check absolute paths \u2014 the resolve_command() in MCPServerConfig
    # will give a clearer error at probe time. We only warn for PATH lookups.
    if os.path.isabs(command):
        return
    if shutil.which(command) is not None:
        return  # found, no warning needed

    install_hints = {
        "uvx": "pip install uv   # or: curl -LsSf https://astral.sh/uv/install.sh | sh",
        "uv": "pip install uv   # or: curl -LsSf https://astral.sh/uv/install.sh | sh",
        "npx": "install Node.js (includes npx): https://nodejs.org/",
        "pipx": "pip install pipx",
        "python3": "install Python 3.12+ from https://python.org/",
    }
    hint = install_hints.get(command, f"install '{command}' and ensure it's on your $PATH")
    print(
        yellow(f"  \u26a0  Launch command {command!r} not found on $PATH"),
        file=sys.stderr,
    )
    print(dim(f"     Install it:  {hint}"), file=sys.stderr)
    print(
        dim("     Or override with --command <path> --args '...'"),
        file=sys.stderr,
    )


def _mcp_uninstall(args: argparse.Namespace) -> int:
    """Remove a server entry from ~/.agentkthx/mcp.json (R07.23).

    Pairs with ``mcp install`` so users don't have to edit mcp.json by hand
    when an install doesn't work out (e.g. Java-only repos that can't be
    launched as a stdio subprocess, packages that 404, broken configs).

    Usage::

        agentkthx mcp uninstall GhidraMCP
        agentkthx mcp uninstall filesystem --config /path/to/custom.json
    """
    try:
        from ...mcp import default_config_path
    except ImportError:
        print(f"{red('Error:')} agentkthx.mcp module not available")
        return 1

    name = args.name
    cfg_path = getattr(args, "config", None) or str(default_config_path())

    import json as _json
    import os
    from pathlib import Path

    cfg_file = Path(cfg_path)
    if not cfg_file.exists():
        print(f"{red('Error:')} {cfg_file} does not exist")
        print(dim("  Nothing to uninstall. Run `agentkthx mcp list` to see configured servers."))
        return 1

    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            print(f"{red('Error:')} {cfg_file} is not a JSON object")
            return 2
    except (OSError, _json.JSONDecodeError) as e:
        print(f"{red('Error:')} cannot read {cfg_file}: {e}")
        return 2

    servers = data.get("servers", [])
    if not isinstance(servers, list):
        print(f"{red('Error:')} {cfg_file} 'servers' is not an array")
        return 2

    # Find the entry to remove
    found_idx: int | None = None
    for i, s in enumerate(servers):
        if isinstance(s, dict) and s.get("name") == name:
            found_idx = i
            break

    if found_idx is None:
        print(f"{red('Error:')} server {name!r} not found in {cfg_file}")
        # List available names to help the user
        available = [s.get("name", "?") for s in servers if isinstance(s, dict)]
        if available:
            print(dim(f"  Available: {', '.join(available)}"))
        return 3

    # Snapshot the entry for display, then remove it
    removed_entry = servers.pop(found_idx)
    data["servers"] = servers

    # Write back atomically
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_suffix(cfg_file.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, cfg_file)

    print(green(f"\u2713 Uninstalled: {cyan(name)}"))
    print(dim(f"  removed entry from {cfg_file}"))
    cmd_str = removed_entry.get("command", "?")
    args_list = removed_entry.get("args", [])
    if args_list:
        cmd_str += " " + " ".join(args_list)
    print(dim(f"  was: {cmd_str}"))
    remaining = len(servers)
    print(dim(f"  {remaining} server{'s' if remaining != 1 else ''} remaining"))
    if remaining:
        print()
        print(dim(f"  List:    {cyan('agentkthx mcp list')}"))
    return 0
