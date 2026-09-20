"""
AgentKthx Plugin Loader v0.1

Implements PluginManifest, Plugin, and the singleton PluginManager.

Plugin discovery:
  Scan ``agentkthx/plugins/`` for subdirectories containing ``plugin.json``.
  Subdirectories starting with ``_`` or ``.`` are skipped.

Plugin types:
  - ``backend``   -- registers an inference backend class
  - ``feature``   -- extends framework functionality (CLI commands, config)
  - ``tools``     -- registers custom tools (future)
  - ``hook``      -- registers lifecycle hooks (future)

Lifecycle:
  discover() -> load(name) -> register() -> [active] -> unregister() -> unload()

Dependencies:
  The ``depends`` field in plugin.json is respected via topological sort.
  Missing / failed dependencies cause the dependent plugin to be skipped
  with a warning.

Written by VTSTech -- https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """Parsed ``plugin.json`` manifest."""

    name: str
    version: str
    display_name: str
    description: str
    author: str = ""
    license: str = ""
    type: str = "feature"                       # backend | feature | tools | hook
    entrypoint: str = ""                        # Python module name inside plugin dir
    depends: list[str] = field(default_factory=list)
    optional_depends: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)  # {env_prefix, defaults}
    provides: dict = field(default_factory=dict) # {backends, cli_commands, cli_flags}
    compatibility: dict = field(default_factory=dict)


def _parse_manifest(path: Path) -> PluginManifest:
    """Read and validate a ``plugin.json`` file."""
    if not path.exists():
        raise FileNotFoundError(f"plugin.json not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    # Required fields
    for req in ("name", "version", "description", "type"):
        if req not in raw:
            raise ValueError(f"plugin.json missing required field: {req}")

    return PluginManifest(
        name=raw["name"],
        version=raw.get("version", "0.0.0"),
        display_name=raw.get("display_name", raw["name"]),
        description=raw.get("description", ""),
        author=raw.get("author", ""),
        license=raw.get("license", ""),
        type=raw.get("type", "feature"),
        entrypoint=raw.get("entrypoint", raw["name"]),
        depends=raw.get("depends", []),
        optional_depends=raw.get("optional_depends", []),
        config=raw.get("config", {}),
        provides=raw.get("provides", {}),
        compatibility=raw.get("compatibility", {}),
    )


# ---------------------------------------------------------------------------
# Plugin Instance
# ---------------------------------------------------------------------------

@dataclass
class Plugin:
    """A loaded plugin with its manifest, module, and state."""

    manifest: PluginManifest
    path: Path
    module: Any = None          # The imported Python module
    loaded: bool = False

    def __repr__(self) -> str:
        state = "loaded" if self.loaded else "discovered"
        return f"Plugin({self.manifest.name!r}, {state})"


# ---------------------------------------------------------------------------
# Plugin Manager
# ---------------------------------------------------------------------------

class PluginManager:
    """
    Central plugin registry.  Singleton via ``get_plugin_manager()``.

    Responsibilities:
      - Discover plugins from the plugins directory
      - Load/unload plugins via their ``register()``/``unregister()`` entrypoints
      - Merge plugin-provided backends into the backend registry
      - Provide dynamic ``--backend`` choices and CLI subcommands
      - Aggregate plugin config defaults
    """

    def __init__(self, plugins_dir: Path | None = None):
        self._plugins_dir = plugins_dir or Path(__file__).parent
        self._plugins: dict[str, Plugin] = {}

        # Aggregated registrations (core + plugins)
        self._backend_classes: dict[str, type] = {}
        self._backend_aliases: dict[str, str] = {}       # alias -> canonical name
        self._cli_commands: dict[str, dict] = {}         # name -> {handler, setup_parser}
        self._cli_flag_values: dict[str, list[str]] = {} # flag -> allowed values
        self._config_defaults: dict[str, dict] = {}      # env_prefix -> {KEY: value}
        self._config_env_vars: dict[str, str] = {}       # env var name -> default value

    # ------------------------------------------------------------------ #
    #  Discovery                                                         #
    # ------------------------------------------------------------------ #

    def discover(self) -> list[PluginManifest]:
        """
        Scan the plugins directory for ``plugin.json`` manifests.

        Returns a list of parsed manifests in discovery order.
        Directories starting with ``_`` or ``.`` are skipped.
        """
        manifests: list[PluginManifest] = []
        if not self._plugins_dir.is_dir():
            return manifests

        for entry in sorted(self._plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_") or entry.name.startswith("."):
                continue

            manifest_path = entry / "plugin.json"
            if not manifest_path.exists():
                continue

            try:
                manifest = _parse_manifest(manifest_path)
                manifests.append(manifest)
            except Exception as e:
                print(f"[PluginManager] Warning: failed to parse {manifest_path}: {e}")

        return manifests

    # ------------------------------------------------------------------ #
    #  Dependency Resolution                                              #
    # ------------------------------------------------------------------ #

    def _resolve_load_order(self, manifests: list[PluginManifest]) -> list[PluginManifest]:
        """
        Topological sort of manifests respecting ``depends``.

        Plugins with missing hard dependencies are dropped from the result.
        Circular dependencies are detected and rejected.
        """
        available = {m.name for m in manifests}
        # Build adjacency: dep -> dependents
        graph: dict[str, list[str]] = {m.name: [] for m in manifests}
        in_degree: dict[str, int] = {m.name: 0 for m in manifests}

        for m in manifests:
            for dep in m.depends:
                if dep in available:
                    graph[dep].append(m.name)
                    in_degree[m.name] += 1
                else:
                    # Hard dependency missing -- skip this plugin
                    print(
                        f"[PluginManager] Skipping '{m.name}': "
                        f"missing dependency '{dep}'"
                    )
                    in_degree.pop(m.name, None)

        # Kahn's algorithm
        queue = [name for name, deg in in_degree.items() if deg == 0]
        ordered: list[str] = []

        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for neighbour in graph.get(node, []):
                if neighbour in in_degree:
                    in_degree[neighbour] -= 1
                    if in_degree[neighbour] == 0:
                        queue.append(neighbour)

        # Detect cycles
        if len(ordered) != len(in_degree):
            remaining = set(in_degree.keys()) - set(ordered)
            print(
                f"[PluginManager] Circular dependency detected among: {remaining}"
            )

        manifest_map = {m.name: m for m in manifests}
        return [manifest_map[name] for name in ordered if name in manifest_map]

    # ------------------------------------------------------------------ #
    #  Load / Unload                                                     #
    # ------------------------------------------------------------------ #

    def load(self, name: str) -> Plugin | None:
        """
        Load a single plugin by name.

        Finds the plugin directory, parses ``plugin.json``, imports the
        entrypoint module, and calls ``register(manager)``.
        """
        # Already loaded?
        if name in self._plugins and self._plugins[name].loaded:
            return self._plugins[name]

        plugin_dir = self._plugins_dir / name
        if not plugin_dir.is_dir():
            print(f"[PluginManager] Plugin directory not found: {plugin_dir}")
            return None

        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            print(f"[PluginManager] plugin.json not found in: {plugin_dir}")
            return None

        try:
            manifest = _parse_manifest(manifest_path)
        except Exception as e:
            print(f"[PluginManager] Failed to parse {manifest_path}: {e}")
            return None

        # Check dependencies
        for dep in manifest.depends:
            if dep not in self._plugins or not self._plugins[dep].loaded:
                print(
                    f"[PluginManager] Cannot load '{name}': "
                    f"dependency '{dep}' not loaded"
                )
                return None

        return self._load_plugin(manifest, plugin_dir)

    def load_all(self) -> list[Plugin]:
        """
        Discover and load all plugins in dependency order.

        Returns the list of successfully loaded plugins.
        """
        manifests = self.discover()
        ordered = self._resolve_load_order(manifests)

        loaded: list[Plugin] = []
        for manifest in ordered:
            plugin_dir = self._plugins_dir / manifest.name
            plugin = self._load_plugin(manifest, plugin_dir)
            if plugin is not None:
                loaded.append(plugin)

        if loaded:
            names = [p.manifest.name for p in loaded]
            print(f"[PluginManager] Loaded {len(loaded)} plugin(s): {', '.join(names)}")

        return loaded

    def _load_plugin(self, manifest: PluginManifest, plugin_dir: Path) -> Plugin | None:
        """
        Import the plugin module and call ``register()``.
        """
        plugin = Plugin(manifest=manifest, path=plugin_dir)

        try:
            # Import the plugin package (entrypoint is always __init__ now,
            # which contains register()/unregister() that lazy-import the
            # actual backend/module code).
            module = __import__(
                f"agentkthx.plugins.{manifest.name}",
                fromlist=["register", "unregister"],
            )

            plugin.module = module

            # Call register
            if hasattr(module, "register"):
                module.register(self)

            plugin.loaded = True
            self._plugins[manifest.name] = plugin

            # Merge config defaults
            if manifest.config:
                env_prefix = manifest.config.get("env_prefix", "")
                defaults = manifest.config.get("defaults", {})
                if env_prefix and defaults:
                    self._config_defaults[env_prefix] = defaults
                    for key, val in defaults.items():
                        self._config_env_vars[key] = val

            # Merge CLI flag values
            flag_values = manifest.provides.get("cli_flags", {})
            for flag, values in flag_values.items():
                if flag not in self._cli_flag_values:
                    self._cli_flag_values[flag] = []
                self._cli_flag_values[flag].extend(values)

            return plugin

        except Exception as e:
            print(f"[PluginManager] Failed to load plugin '{manifest.name}': {e}")
            return None

    def unload(self, name: str) -> bool:
        """
        Unload a plugin, call ``unregister()``, remove registrations.
        """
        plugin = self._plugins.get(name)
        if plugin is None or not plugin.loaded:
            return False

        try:
            if plugin.module and hasattr(plugin.module, "unregister"):
                plugin.module.unregister(self)
        except Exception as e:
            print(f"[PluginManager] Error unloading '{name}': {e}")

        # Remove backend registrations from this plugin
        backend_provides = plugin.manifest.provides.get("backends", {})
        for bname in backend_provides:
            self._backend_classes.pop(bname, None)
            self._backend_aliases.pop(bname, None)

        # Remove CLI commands
        cli_cmds = plugin.manifest.provides.get("cli_commands", [])
        for cmd in cli_cmds:
            self._cli_commands.pop(cmd, None)

        # Remove CLI flag values
        flag_values = plugin.manifest.provides.get("cli_flags", {})
        for flag, values in flag_values.items():
            if flag in self._cli_flag_values:
                for v in values:
                    if v in self._cli_flag_values[flag]:
                        self._cli_flag_values[flag].remove(v)

        plugin.loaded = False
        return True

    # ------------------------------------------------------------------ #
    #  Backend Registration (merged with core)                            #
    # ------------------------------------------------------------------ #

    def register_backend(self, name: str, cls: type, *, alias_of: str | None = None) -> None:
        """
        Register a backend class provided by a plugin.

        Parameters
        ----------
        name : str
            Backend name (e.g. ``"bitnet"``).
        cls : type
            Backend class (must be a subclass of BaseBackend).
        alias_of : str, optional
            If this backend is an alias of another, set to canonical name.
            The manager will route ``get_backend(name)`` to the canonical class.
        """
        if alias_of:
            self._backend_aliases[name] = alias_of
        else:
            self._backend_classes[name] = cls

    def unregister_backend(self, name: str) -> None:
        """Remove a plugin-registered backend."""
        self._backend_classes.pop(name, None)
        self._backend_aliases.pop(name, None)

    def find_plugin_for_backend(self, backend_name: str) -> str | None:
        """
        Find the plugin name that provides a given backend.

        Scans discovered manifests' ``provides.backends`` to build a
        reverse mapping from backend name to plugin name.

        Returns the plugin name (e.g. ``"test-plugin"``) or ``None``.
        """
        for manifest in self.discover():
            backends = manifest.provides.get("backends", {})
            if backend_name in backends:
                return manifest.name
        return None

    def get_backend_class(self, name: str) -> type | None:
        """
        Get a backend class by name.

        Resolves aliases to their canonical class.
        """
        # Check direct registration first
        if name in self._backend_classes:
            return self._backend_classes[name]

        # Check aliases
        canonical = self._backend_aliases.get(name)
        if canonical and canonical in self._backend_classes:
            return self._backend_classes[canonical]

        return None

    def list_backend_names(self) -> list[str]:
        """List all plugin-registered backend names (not including core)."""
        names = list(self._backend_classes.keys())
        names.extend(self._backend_aliases.keys())
        return sorted(set(names))

    # ------------------------------------------------------------------ #
    #  CLI Extension                                                     #
    # ------------------------------------------------------------------ #

    def register_cli_command(
        self,
        name: str,
        handler: Callable,
        setup_parser: Callable | None = None,
    ) -> None:
        """
        Register a CLI subcommand provided by a plugin.

        Parameters
        ----------
        name : str
            Command name (e.g. ``"turbo"``).
        handler : callable
            Function that handles the command (receives argparse.Namespace).
        setup_parser : callable, optional
            Function that sets up the subparser for this command.
        """
        self._cli_commands[name] = {
            "handler": handler,
            "setup_parser": setup_parser,
        }

    def unregister_cli_command(self, name: str) -> None:
        """Remove a plugin-registered CLI command."""
        self._cli_commands.pop(name, None)

    def get_cli_commands(self) -> dict[str, dict]:
        """Get all plugin-registered CLI commands."""
        return dict(self._cli_commands)

    def get_backend_choices(self) -> list[str]:
        """
        Get the merged list of ``--backend`` choices.

        Includes core backends (ollama, llama-server) plus any values
        plugins have registered via ``cli_flags["--backend"]``.
        """
        core = ["ollama", "llama-server", "llama_server"]
        plugin_values = self._cli_flag_values.get("--backend", [])
        combined = core + [v for v in plugin_values if v not in core]
        return combined

    def get_all_cli_flag_choices(self, flag: str) -> list[str]:
        """Get merged choices for any CLI flag."""
        return self._cli_flag_values.get(flag, [])

    # ------------------------------------------------------------------ #
    #  Config Extension                                                  #
    # ------------------------------------------------------------------ #

    def register_config_defaults(self, env_prefix: str, defaults: dict) -> None:
        """
        Register plugin's default config values.

        These are available via ``get_all_config_defaults()`` so that
        ``config.py`` can merge them at init time.
        """
        self._config_defaults[env_prefix] = defaults
        for key, val in defaults.items():
            self._config_env_vars[key] = val

    def get_all_config_defaults(self) -> dict[str, str]:
        """
        Get all plugin config defaults as a flat ``{ENV_VAR: default}`` dict.
        """
        return dict(self._config_env_vars)

    def get_config_defaults_by_prefix(self, env_prefix: str) -> dict[str, str]:
        """Get config defaults for a specific plugin's env prefix."""
        return dict(self._config_defaults.get(env_prefix, {}))

    # ------------------------------------------------------------------ #
    #  Query                                                             #
    # ------------------------------------------------------------------ #

    def is_loaded(self, name: str) -> bool:
        plugin = self._plugins.get(name)
        return plugin is not None and plugin.loaded

    def list_plugins(self) -> list[str]:
        return list(self._plugins.keys())

    def get_plugin(self, name: str) -> Plugin | None:
        return self._plugins.get(name)

    def get_plugin_info(self, name: str) -> PluginManifest | None:
        plugin = self._plugins.get(name)
        return plugin.manifest if plugin else None

    def __repr__(self) -> str:
        loaded = sum(1 for p in self._plugins.values() if p.loaded)
        return (
            f"PluginManager(plugins={len(self._plugins)}, "
            f"loaded={loaded}, "
            f"backends={self.list_backend_names()})"
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_plugin_manager: PluginManager | None = None


def get_plugin_manager(init: bool = True) -> PluginManager:
    """
    Get the global PluginManager singleton.

    Parameters
    ----------
    init : bool
        If True (default), initialize on first call.
        Set to False to get ``None`` if not yet initialized.
    """
    global _plugin_manager
    if _plugin_manager is None and init:
        _plugin_manager = PluginManager()
    return _plugin_manager


__all__ = [
    "PluginManifest",
    "Plugin",
    "PluginManager",
    "get_plugin_manager",
]
