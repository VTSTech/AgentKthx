"""main() entry point: plugin wiring, subcommand dispatch, update check.

Extracted verbatim from cli.py in R07.00 Phase 8. Composition root —
imports command handlers directly rather than through the facade."""

from __future__ import annotations

from typing import Callable, Optional

from .banner import _print_update_notice, _run_update_check, print_banner
from .commands import cmd_agent, cmd_chat, cmd_config, cmd_modelfile, cmd_models, cmd_plugins, cmd_run, cmd_sessions, cmd_skills, cmd_soul, cmd_test, cmd_tools, cmd_turbo, cmd_update, cmd_version
from .parser import create_parser

def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    
    # Load plugins early so that CLI arguments can include plugin backends
    try:
        from ..plugins import get_plugin_manager
        pm = get_plugin_manager()
        pm.load_all()  # Load all plugins to register backends
    except Exception as e:
        # Plugin loading should not prevent CLI from working
        pass

    # on_shutdown: emit once before interpreter exit (spec §Hooks, §Lifecycle)
    try:
        import atexit as _atexit
        from ..plugins import get_plugin_manager as _get_pm
        _pm_ref = _get_pm(init=False)

        def _emit_on_shutdown_once():
            try:
                if _pm_ref is not None and not getattr(_pm_ref, "_shutdown_emitted", False):
                    _pm_ref._shutdown_emitted = True
                    _pm_ref.emit("on_shutdown", {})
            except Exception:
                pass

        _atexit.register(_emit_on_shutdown_once)
    except Exception:
        pass
    
    parser = create_parser()

    # Discover plugin-provided CLI commands, add as subparsers, and mark with *
    _plugin_cli_handlers: dict[str, Callable] = {}
    try:
        from ..plugins import get_plugin_manager
        pm = get_plugin_manager()
        manifests = pm.discover()

        if manifests:
            plugin_commands = set()
            for m in manifests:
                for cmd in m.provides.get("cli_commands", []):
                    plugin_commands.add(cmd)

            if plugin_commands:
                # Use the stashed _SubParsersAction (not parser._subparsers, which
                # is an _ArgumentGroup, not the subparsers registry).
                subparsers_action = getattr(parser, "_subparsers_action", None)
                if subparsers_action is not None:
                    for cmd_name in plugin_commands:
                        if cmd_name in subparsers_action.choices:
                            # Already exists as a native subparser — mark with *
                            # Help text lives in _choices_actions, not on the parser
                            for ca in subparsers_action._choices_actions:
                                if ca.dest == cmd_name:
                                    ca.help = f"* {ca.help} [plugin]"
                                    break
                        else:
                            # Add a new subparser for this plugin command
                            subparsers_action.add_parser(
                                cmd_name, help=f"* {cmd_name} [plugin]"
                            )

                # Load all plugins so their register_cli_command() handlers are wired
                pm.load_all()

                # Collect the handlers for dispatch
                for cmd_name, cmd_info in pm.get_cli_commands().items():
                    _plugin_cli_handlers[cmd_name] = cmd_info["handler"]
    except Exception as e:
        import sys
        print(f"[PluginManager] Warning: plugin CLI discovery failed: {e}", file=sys.stderr)

    args = parser.parse_args(argv)

    if args.command is None:
        print_banner()
        parser.print_help()
        return 0

    # Update check — live PyPI+GitHub query, silent on failure / opt-out
    # (AGENTKTHX_NO_UPDATE_CHECK=1). Runs once per process (R07.00: always
    # live, no cache); `version` does its own inline check;
    # `update` obviously doesn't need one.
    if args.command not in ("version", "update"):
        _run_update_check()

    commands = {
        "run": cmd_run,
        "chat": cmd_chat,
        "agent": cmd_agent,
        "models": cmd_models,
        "tools": cmd_tools,
        "test": cmd_test,
        "turbo": cmd_turbo,
        "version": cmd_version,
        "config": cmd_config,
        "modelfile": cmd_modelfile,
        "skills": cmd_skills,
        "soul": cmd_soul,
        "sessions": cmd_sessions,
        "plugins": cmd_plugins,
        "update": cmd_update,
    }

    handler = commands.get(args.command)
    if handler is None:
        # Check plugin-provided CLI commands
        handler = _plugin_cli_handlers.get(args.command)

    if handler:
        rc = handler(args)
        # Post-run notice (pip-style) for non-interactive commands. Chat already
        # printed it under the banner; version/update never stashed a result.
        # --json invocations stay machine-readable — no notice, either stream.
        if args.command != "chat" and not getattr(args, "json", False):
            _print_update_notice()
        return rc

    parser.print_help()
    return 0
