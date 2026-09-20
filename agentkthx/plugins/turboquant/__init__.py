"""
AgentKthx Plugin — TurboQuant Server Manager

llama.cpp TurboQuant server lifecycle management (start/stop/status)
with auto KV cache detection and Ollama model discovery.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the TurboQuant CLI command with the plugin manager."""
    from agentkthx.plugins.turboquant.turbo import (
        start_server,
        stop_server,
        get_status,
        print_model_list,
        print_status,
    )

    # Register the 'turbo' CLI command handler
    if hasattr(manager, 'register_cli_command'):
        manager.register_cli_command('turbo', {
            'start': start_server,
            'stop': stop_server,
            'status': get_status,
            'list': print_model_list,
        })


def unregister(manager) -> None:
    """Unregister the TurboQuant CLI command from the plugin manager."""
    if hasattr(manager, 'unregister_cli_command'):
        manager.unregister_cli_command('turbo')
