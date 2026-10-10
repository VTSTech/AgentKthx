"""
AgentKthx Plugin — Discord Bot Gateway

Runs AgentKthx as a Discord bot. Feature plugin per docs/PLUGIN_SPEC_v0.2.md —
provides the `agentkthx discord` CLI command and an on_shutdown hook. All
Discord-specific work lives in this plugin; core code is untouched.

M0 scope (R07.33): gateway client (RFC 6455 + Discord v10 op layer),
REST client, policy gatekeeper, CLI stub with --dry-run. The chat responder
(worker pool + Agent invocation) lands in M1 / R07.34 — see
docs/DISCORD_PLUGIN_PLAN.md.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the discord CLI command + on_shutdown hook (lazy imports)."""
    from .discord_bot import cmd_discord, on_shutdown, setup_parser

    if hasattr(manager, "register_cli_command"):
        manager.register_cli_command(
            "discord", cmd_discord, setup_parser, plugin="discord"
        )
    if hasattr(manager, "register_hook"):
        manager.register_hook("on_shutdown", on_shutdown, plugin="discord")


def unregister(manager) -> None:
    """Remove all registrations made in register()."""
    if hasattr(manager, "unregister_cli_command"):
        manager.unregister_cli_command("discord")
    if hasattr(manager, "unregister_hook"):
        try:
            from .discord_bot import on_shutdown

            manager.unregister_hook("on_shutdown", on_shutdown)
        except Exception:
            pass
