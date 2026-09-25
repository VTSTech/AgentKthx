"""`agentkthx plugins` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import bold, yellow, dim, pad_colored, green, cyan, red, bright_cyan




def cmd_plugins(args: argparse.Namespace) -> int:
    """List and manage plugins (v0.2 spec §CLI integration)."""
    from ...plugins import get_plugin_manager

    pm = get_plugin_manager()

    # --- Management actions (--load / --unload / --reload) ---
    action = getattr(args, "load", None) and ("load", args.load) \
        or getattr(args, "unload", None) and ("unload", args.unload) \
        or getattr(args, "reload", None) and ("reload", args.reload)
    if action:
        verb, name = action
        if verb == "load":
            ok = pm.load(name) is not None
        elif verb == "unload":
            ok = pm.unload(name)
        else:  # reload
            pm.unload(name)
            pm.discover(force=True)
            ok = pm.load(name) is not None
        if ok:
            print(green(f"\u2713 {verb} '{name}' OK (state: {pm.get_plugin_state(name)})"))
            return 0
        print(red(f"\u2717 {verb} '{name}' failed"))
        for w in pm.warnings[-5:]:
            print(dim(f"  {w}"))
        return 1

    # --- Machine-readable listing (--json) ---
    if getattr(args, "json", False):
        import json as _json
        manifests = pm.discover()
        out = []
        for m in sorted(manifests, key=lambda x: x.name):
            out.append({
                "name": m.name,
                "version": m.version,
                "display_name": m.display_name,
                "description": m.description,
                "author": m.author,
                "license": m.license,
                "type": m.type,
                "entrypoint": m.entrypoint,
                "depends": m.depends,
                "optional_depends": m.optional_depends,
                "config": m.config,
                "provides": m.provides,
                "compatibility": m.compatibility,
                "schema": m.schema,
                "root": str(m.dir) if m.dir else None,
                "root_kind": m.root_kind,
                "legacy_fields_used": m.legacy_fields_used,
                "state": pm.get_plugin_state(m.name),
            })
        print(_json.dumps(out, indent=2))
        return 0

    # Discover all plugins
    manifests = pm.discover()

    if not manifests:
        print(yellow("No plugins found."))
        print(dim("  Plugins should be in agentkthx/plugins/<name>/plugin.json,"))
        print(dim(f"  ~/.agentkthx/plugins/, or $AGENTKTHX_PLUGIN_PATH"))
        return 0

    print(bold(bright_cyan("PLUGINS")))
    print()

    # Table header
    name_w = max(len(m.name) for m in manifests) + 4
    name_w = max(name_w, 14)
    type_w = 10
    ver_w = 10

    header = (
        pad_colored(bold("Name"), name_w) +
        pad_colored(bold("Type"), type_w) +
        pad_colored(bold("Version"), ver_w) +
        bold("Description")
    )
    print(header)
    print(dim("  " + "-" * (name_w + type_w + ver_w + 40)))

    for m in sorted(manifests, key=lambda x: x.name):
        state = pm.get_plugin_state(m.name)
        if state == "loaded":
            status = green("●")
        elif state == "failed":
            status = red("✗")
        else:
            status = dim("○")
        desc = m.description[:60] + ("..." if len(m.description) > 60 else "")

        line = (
            pad_colored(f"{status} {m.name}", name_w) +
            pad_colored(cyan(m.type), type_w) +
            pad_colored(dim(m.version), ver_w) +
            desc
        )
        print(line)

        if getattr(args, "verbose", False):
            if m.depends:
                print(f"    {dim('depends:')} {', '.join(m.depends)}")
            if m.provides.get("backends"):
                print(f"    {dim('backends:')} {', '.join(m.provides['backends'].keys())}")
            if m.provides.get("cli_commands"):
                print(f"    {dim('cli_commands:')} {', '.join(m.provides['cli_commands'])}")
            if m.provides.get("tools"):
                print(f"    {dim('tools:')} {', '.join(m.provides['tools'])}")
            if m.provides.get("hooks"):
                print(f"    {dim('hooks:')} {', '.join(m.provides['hooks'].keys())}")
            entry = m.entrypoint or m.name
            print(f"    {dim('entrypoint:')} {entry}")
            if m.dir:
                print(f"    {dim('root:')} {m.dir} ({m.root_kind})")
            if m.legacy_fields_used:
                print(f"    {yellow('deprecated top-level fields:')} {', '.join(m.legacy_fields_used)}")
            if state == "failed":
                print(f"    {red('error:')} {pm._failed.get(m.name, 'unknown')}")

    print()
    n_loaded = sum(1 for m in manifests if pm.is_loaded(m.name))
    print(dim(f"  {len(manifests)} plugin(s) found, {n_loaded} loaded"))

    return 0
