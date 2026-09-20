"""
AgentKthx Plugin — Agent Control Panel (ACP)

ACP v1.0.6 integration for monitoring, token tracking, STOP/Resume control,
and A2A agent-to-agent messaging.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the ACP plugin with the plugin manager.

    This is a feature plugin (not a backend), so we store a reference
    on the manager for later access by the agent runtime.
    """
    from .acp_plugin import ACPPlugin
    manager.acp_plugin = ACPPlugin()


def unregister(manager) -> None:
    """Unregister the ACP plugin from the plugin manager."""
    if hasattr(manager, 'acp_plugin'):
        if hasattr(manager.acp_plugin, 'shutdown'):
            try:
                manager.acp_plugin.shutdown("Plugin unloaded")
            except Exception:
                pass
        delattr(manager, 'acp_plugin')
