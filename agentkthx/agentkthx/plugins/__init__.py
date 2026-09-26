"""
AgentKthx Plugin System v0.2

Provides a directory-scan-based plugin architecture for extending AgentKthx
with backends, CLI commands, configuration, tools, and lifecycle hooks.
Implements docs/PLUGIN_SPEC_v0.2.md:

  - Multi-root discovery (package dir, ~/.agentkthx/plugins/,
    $AGENTKTHX_PLUGIN_PATH)
  - plugin.json manifests with $schema + extensions namespace
    (org.vts-tech.agentkthx), legacy v0.1 top-level fields still accepted
  - PLUGIN_ROOT / PLUGIN_DATA per plugin
  - Warn-only compatibility enforcement
  - Tool bridge (ToolRegistry) and lifecycle hooks with error isolation

Plugins are discovered from plugin root directories that contain a
``plugin.json`` manifest.  Each plugin exposes ``register()`` and
``unregister()`` entrypoints that the PluginManager calls during lifecycle.

Core (native) backends -- ollama and llama-server -- are never treated as
plugins; they ship inside ``agentkthx/backends/`` and are always available.

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
