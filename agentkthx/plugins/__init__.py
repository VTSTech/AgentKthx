"""
AgentKthx Plugin System v0.1

Provides a directory-scan-based plugin architecture for extending AgentKthx
with backends, CLI commands, configuration, and feature hooks.

Plugins are discovered from ``agentnova/plugins/`` directories that contain
a ``plugin.json`` manifest.  Each plugin exposes ``register()`` and
``unregister()`` entrypoints that the PluginManager calls during lifecycle.

Core (native) backends -- ollama and llama-server -- are never treated as
plugins; they ship inside ``agentnova/backends/`` and are always available.

Usage::

    from agentkthx.plugins import get_plugin_manager

    pm = get_plugin_manager()
    pm.load_all()

    # Backends registered by plugins are available via the normal path
    from agentkthx.backends import get_backend
    backend = get_backend("bitnet")

Written by VTSTech -- https://www.vts-tech.org
"""

from ._loader import (
    PluginManifest,
    Plugin,
    PluginManager,
    get_plugin_manager,
)

__all__ = [
    "PluginManifest",
    "Plugin",
    "PluginManager",
    "get_plugin_manager",
]
