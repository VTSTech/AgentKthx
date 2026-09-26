"""
AgentKthx Plugin Loader v0.2

Implements PluginManifest, Plugin, and the singleton PluginManager per
docs/PLUGIN_SPEC_v0.2.md.

v0.2 highlights (over v0.1):
  - ``$schema`` version targeting + dual-form manifest parsing
    (extensions-first with legacy top-level fallback).
  - Multi-root discovery: package dir, ``~/.agentkthx/plugins/``,
    ``$AGENTKTHX_PLUGIN_PATH`` (first root wins on name collisions).
  - Strict plugin name constraints + name/directory match.
  - Honored ``entrypoint`` (package ``__init__`` or submodule; external
    roots load by path).
  - ``tools`` and ``hook`` plugin types (tool registry bridge, five
    lifecycle events with error isolation).
  - ``PLUGIN_ROOT`` / ``PLUGIN_DATA`` env vars + persistent data dirs.
  - Warn-only ``compatibility`` enforcement (``agentnova`` legacy alias).
  - Complete ``unload()`` (config, tools, hooks now purged too).
  - Failure boundaries: bad plugin never blocks the rest.

Plugin types:
  - ``backend``   -- registers an inference backend class
  - ``feature``   -- extends framework functionality (CLI commands, config)
  - ``tools``     -- registers custom tools
  - ``hook``      -- registers lifecycle hooks

Lifecycle:
  discover() -> load(name) -> register() -> [active: on_init / on_run_start /
  on_run_end / on_error / on_shutdown] -> unregister() -> unload()

Written by VTSTech -- https://www.vts-tech.org
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Constants (spec: docs/PLUGIN_SPEC_v0.2.md)
# ---------------------------------------------------------------------------

#: Reverse-domain extension namespace for AgentKthx-specific manifest data.
EXT_NAMESPACE = "org.vts-tech.agentkthx"

#: Canonical $schema identifier for manifest version targeting.
CANONICAL_SCHEMA = (
    "https://raw.githubusercontent.com/VTSTech/AgentKthx/"
    "main/schemas/v0.2/plugin.schema.json"
)

#: Top-level manifest fields understood by this client (v0.2 dual-form).
KNOWN_TOP_LEVEL = {
    "$schema", "name", "version", "description", "author", "license",
    "extensions",
    # legacy v0.1 top-level fields (deprecated, still parsed)
    "display_name", "type", "entrypoint", "depends", "optional_depends",
    "config", "provides", "compatibility",
}

#: AgentKthx-specific fields whose canonical home is the extension namespace.
EXTENSION_FIELDS = {
    "display_name", "type", "entrypoint", "depends", "optional_depends",
    "config", "provides", "compatibility",
}

#: Known plugin types.
PLUGIN_TYPES = {"backend", "feature", "tools", "hook"}

#: Known lifecycle hook events.
KNOWN_HOOK_EVENTS = {"on_init", "on_run_start", "on_run_end", "on_error", "on_shutdown"}

#: Plugin name constraint (spec §Plugin name constraints).
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

#: Package-safe name (no dots) — required in built-in (package) roots.
PKG_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

#: Entrypoint module name constraint (single identifier or ``__init__``).
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Compatibility constraint, e.g. ">=0.5.0" or ">=0.5.0,<0.7.0".
CONSTRAINT_RE = re.compile(r"^(>=|<=|>|<|==|!=)?(\d+)\.(\d+)\.(\d+)$")

#: Secret-shaped config default keys warn (spec §Config).
SECRET_SUFFIX_RE = re.compile(r"(KEY|PASS|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)

#: Placeholder pattern for ${PLUGIN_ROOT} / ${PLUGIN_DATA}.
_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def _warn(warnings: list[str] | None, msg: str) -> None:
    """Record (and print) a warning. Tests assert on the collected list."""
    if warnings is not None:
        warnings.append(msg)
    # Diagnostics go to stderr so stdout stays machine-readable (e.g. --json)
    print(f"[PluginManager] Warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Small spec helpers
# ---------------------------------------------------------------------------

def parse_version(value: Any) -> tuple[int, int, int] | None:
    """Extract a (major, minor, patch) tuple from a version-ish string."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def constraint_satisfied(version: tuple[int, int, int] | None, constraint: str) -> bool:
    """
    Evaluate a comma-separated compatibility constraint against ``version``.

    Malformed constraints fail open (treated as satisfied) because v0.2
    enforcement is advisory. A None version (unparseable framework version)
    also fails open.
    """
    for part in constraint.split(","):
        part = part.strip()
        m = CONSTRAINT_RE.match(part)
        if not m:
            return True  # malformed -> fail open
        op = m.group(1) or "=="
        target = (int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if version is None:
            return True
        ok = {
            "<": version < target,
            "<=": version <= target,
            ">": version > target,
            ">=": version >= target,
            "==": version == target,
            "!=": version != target,
        }[op]
        if not ok:
            return False
    return True


def expand_placeholders(
    value: str,
    plugin_root: Path | None,
    plugin_data: Path | None,
) -> str:
    """
    Single-pass, non-recursive expansion of ${PLUGIN_ROOT} / ${PLUGIN_DATA}.

    Unrecognized placeholder-like text stays literal (spec §Expansion).
    """
    if not isinstance(value, str) or "${" not in value:
        return value

    def _repl(m: re.Match) -> str:
        name = m.group(1).strip()
        if name == "PLUGIN_ROOT" and plugin_root is not None:
            return str(plugin_root)
        if name == "PLUGIN_DATA" and plugin_data is not None:
            return str(plugin_data)
        return m.group(0)  # unrecognized -> literal

    return _PLACEHOLDER_RE.sub(_repl, value)


def valid_plugin_name(name: str) -> bool:
    """Full name-constraint check (length, charset, repetition)."""
    if not isinstance(name, str) or not (1 <= len(name) <= 64):
        return False
    if "--" in name or ".." in name:
        return False
    return bool(NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Plugin Manifest
# ---------------------------------------------------------------------------

@dataclass
class PluginManifest:
    """Parsed ``plugin.json`` manifest (v0.2)."""

    name: str
    version: str
    display_name: str
    description: str
    author: str = ""
    license: str = ""
    type: str = "feature"                       # backend | feature | tools | hook
    entrypoint: str = "__init__"                # module inside the plugin dir
    depends: list[str] = field(default_factory=list)
    optional_depends: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)  # {env_prefix, defaults}
    provides: dict = field(default_factory=dict)  # {backends, cli_commands, cli_flags, tools, hooks}
    compatibility: dict = field(default_factory=dict)
    # --- v0.2 additions ---
    schema: str | None = None                   # $schema value (None = legacy form)
    dir: Path | None = None                     # plugin root directory
    root_kind: str = "builtin"                  # builtin | user | env
    legacy_fields_used: list[str] = field(default_factory=list)


def _normalize_author(raw: Any, plugin_name: str, warnings: list[str]) -> str:
    """Accept a string or an Agent Plugins-style author object."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        allowed = {"name", "email", "url"}
        unknown = set(raw) - allowed
        if unknown or any(not isinstance(v, str) for v in raw.values()):
            raise ValueError(
                "author object may only contain the string fields "
                "'name', 'email', 'url'"
            )
        return raw.get("name") or raw.get("email") or raw.get("url") or ""
    raise ValueError("author must be a string or an object with name/email/url")


def _check_spdx_license(license_id: str, name: str, warnings: list[str]) -> None:
    """Warn (never reject) on a non-SPDX license identifier."""
    if not license_id:
        return
    try:
        from ..skills.loader import validate_spdx_license
        ok, _msg = validate_spdx_license(license_id)
        if not ok:
            _warn(
                warnings,
                f"{name}: license {license_id!r} is not a recognized SPDX identifier",
            )
    except ImportError:
        pass  # skills loader unavailable; skip advisory check


def _parse_manifest(
    path: Path,
    *,
    root_kind: str = "builtin",
    warnings: list[str] | None = None,
) -> PluginManifest:
    """
    Read and validate a ``plugin.json`` file.

    Raises on fatal problems (missing required fields, invalid name,
    name/directory mismatch). Advisory problems produce warnings.
    """
    if not path.exists():
        raise FileNotFoundError(f"plugin.json not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("plugin.json must contain a top-level JSON object")

    if warnings is None:
        warnings = []

    schema = raw.get("$schema")
    if isinstance(schema, str) and schema and schema != CANONICAL_SCHEMA:
        _warn(
            warnings,
            f"{path.parent.name}: unrecognized $schema {schema!r}; "
            f"attempting v0.2 parsing anyway",
        )
    v02_form = isinstance(schema, str) and bool(schema)
    if not v02_form:
        _warn(
            warnings,
            f"{path.parent.name}: no $schema field (legacy v0.1 manifest); "
            f"consider adding {CANONICAL_SCHEMA}",
        )

    # Unknown top-level fields: warn + ignore (spec §Manifest)
    for key in raw:
        if key not in KNOWN_TOP_LEVEL:
            _warn(warnings, f"{path.parent.name}: unknown manifest field {key!r} ignored")

    # Required fields
    for req in ("name", "description"):
        value = raw.get(req)
        if not isinstance(value, str) or not value:
            raise ValueError(f"plugin.json missing or invalid required field: {req}")

    name = raw["name"]
    if not valid_plugin_name(name):
        raise ValueError(f"invalid plugin name: {name!r} (spec §Plugin name constraints)")
    if root_kind == "builtin" and not PKG_NAME_RE.match(name):
        raise ValueError(
            f"plugin name {name!r} is not usable as a Python package component "
            f"in a built-in root (dots are only valid for external roots)"
        )

    # Name must match the directory (spec §Discovery rule 4)
    dir_name = path.parent.name
    if name != dir_name:
        raise ValueError(
            f"manifest name {name!r} does not match directory name {dir_name!r}"
        )

    author = _normalize_author(raw.get("author", ""), name, warnings)
    license_id = raw.get("license", "")
    if not isinstance(license_id, str):
        raise ValueError("license must be a string")
    _check_spdx_license(license_id, name, warnings)

    # --- dual-form extension resolution --------------------------------
    extensions = raw.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        _warn(warnings, f"{name}: 'extensions' is not an object; ignored")
        extensions = None

    ns_obj: dict = {}
    if isinstance(extensions, dict):
        ns_value = extensions.get(EXT_NAMESPACE)
        if ns_value is not None:
            if isinstance(ns_value, dict):
                ns_obj = ns_value
                for key in ns_obj:
                    if key not in EXTENSION_FIELDS:
                        _warn(
                            warnings,
                            f"{name}: unknown field {key!r} in extensions['{EXT_NAMESPACE}'] ignored",
                        )
            else:
                _warn(
                    warnings,
                    f"{name}: extensions['{EXT_NAMESPACE}'] is not an object; ignored",
                )
        # other namespaces are ignored without validation (spec §Extensions)

    legacy_fields_used: list[str] = []

    def ext_field(field_name: str, default: Any) -> Any:
        """extensions-first resolution with legacy top-level fallback."""
        in_ext = field_name in ns_obj
        in_top = field_name in raw
        if in_ext and in_top:
            _warn(
                warnings,
                f"{name}: field '{field_name}' present in both extensions and "
                f"top-level; using the extensions value",
            )
            # Record it: the legacy field still ships in the manifest and
            # should be cleaned up (surfaced by `agentkthx plugins --verbose`).
            legacy_fields_used.append(field_name)
        if in_ext:
            return ns_obj[field_name]
        if in_top:
            if v02_form:
                _warn(
                    warnings,
                    f"{name}: legacy top-level field '{field_name}' is deprecated; "
                    f"move it under extensions['{EXT_NAMESPACE}']",
                )
                legacy_fields_used.append(field_name)
            return raw[field_name]
        return default

    def as_str_list(value: Any, field_name: str) -> list[str]:
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return list(value)
        _warn(warnings, f"{name}: {field_name} must be a list of strings; ignored")
        return []

    # type
    ptype = ext_field("type", "feature")
    if not isinstance(ptype, str) or ptype not in PLUGIN_TYPES:
        _warn(
            warnings,
            f"{name}: unknown plugin type {ptype!r}; loading without "
            f"type-specific behavior (known: {sorted(PLUGIN_TYPES)})",
        )

    # entrypoint (v0.2: this field is honored)
    entrypoint = ext_field("entrypoint", "__init__")
    if not isinstance(entrypoint, str) or not ENTRYPOINT_RE.match(entrypoint or ""):
        raise ValueError(f"invalid entrypoint: {entrypoint!r}")

    # depends / optional_depends
    depends = as_str_list(ext_field("depends", []), "depends")
    optional_depends = as_str_list(ext_field("optional_depends", []), "optional_depends")

    # config
    config = ext_field("config", {})
    if config is None:
        config = {}
    if not isinstance(config, dict):
        _warn(warnings, f"{name}: config must be an object; ignored")
        config = {}

    # provides
    provides = ext_field("provides", {})
    if provides is None:
        provides = {}
    if not isinstance(provides, dict):
        _warn(warnings, f"{name}: provides must be an object; ignored")
        provides = {}
    else:
        shape = {
            "backends": dict,
            "cli_commands": list,
            "cli_flags": dict,
            "tools": list,
            "hooks": dict,
        }
        for key, expected in shape.items():
            if key in provides and not isinstance(provides[key], expected):
                _warn(warnings, f"{name}: provides.{key} has the wrong shape; ignored")
                provides[key] = expected()

    # compatibility
    compatibility = ext_field("compatibility", {})
    if compatibility is None:
        compatibility = {}
    if not isinstance(compatibility, dict):
        _warn(warnings, f"{name}: compatibility must be an object; ignored")
        compatibility = {}

    version = raw.get("version", "0.0.0")
    if not isinstance(version, str):
        _warn(warnings, f"{name}: version must be a string; using 0.0.0")
        version = "0.0.0"

    return PluginManifest(
        name=name,
        version=version,
        display_name=ext_field("display_name", name) or name,
        description=raw["description"],
        author=author,
        license=license_id,
        type=ptype if isinstance(ptype, str) else "feature",
        entrypoint=entrypoint,
        depends=depends,
        optional_depends=optional_depends,
        config=config,
        provides=provides,
        compatibility=compatibility,
        schema=schema if isinstance(schema, str) else None,
        dir=path.parent,
        root_kind=root_kind,
        legacy_fields_used=legacy_fields_used,
    )


# ---------------------------------------------------------------------------
# Plugin Instance
# ---------------------------------------------------------------------------

@dataclass
class Plugin:
    """A loaded plugin with its manifest, module, and state."""

    manifest: PluginManifest
    path: Path                                  # plugin root directory
    module: Any = None                          # imported entrypoint module
    loaded: bool = False
    failed: bool = False                        # load attempted and failed
    error: str | None = None                    # failure message (if failed)
    # v0.2: PLUGIN_ROOT / PLUGIN_DATA as attributes (spec §Environment)
    root: Path | None = None
    data_dir: Path | None = None

    @property
    def state(self) -> str:
        if self.loaded:
            return "loaded"
        if self.failed:
            return "failed"
        return "discovered"

    def __repr__(self) -> str:
        return f"Plugin({self.manifest.name!r}, {self.state})"


# ---------------------------------------------------------------------------
# Plugin Manager
# ---------------------------------------------------------------------------

class PluginManager:
    """
    Central plugin registry.  Singleton via ``get_plugin_manager()``.

    Responsibilities (v0.2):
      - Discover plugins from prioritized plugin roots
      - Load/unload plugins via their ``register()``/``unregister()`` entrypoints
      - Merge plugin-provided backends into the backend registry
      - Provide dynamic ``--backend`` choices and CLI subcommands
      - Aggregate plugin config defaults (with placeholder expansion)
      - Bridge plugin tools into the core tool registry
      - Dispatch lifecycle hooks with per-hook error isolation
      - Provide PLUGIN_ROOT / PLUGIN_DATA per plugin
    """

    def __init__(self, plugins_dir: Path | str | list[Path | str] | None = None):
        if plugins_dir is None:
            self._roots: list[tuple[Path, str]] = self._default_roots()
        elif isinstance(plugins_dir, (list, tuple)):
            self._roots = [(Path(p), "env") for p in plugins_dir]
        else:
            self._roots = [(Path(plugins_dir), "env")]

        # Backward-compatible attribute: the primary (built-in) root.
        self._plugins_dir = self._roots[0][0] if self._roots else None

        self._plugins: dict[str, Plugin] = {}
        self._failed: dict[str, str] = {}       # name -> error message
        self._manifests: list[PluginManifest] | None = None  # discovery cache
        self._on_init_emitted = False
        self._shutdown_emitted = False

        # Aggregated registrations (core + plugins)
        self._backend_classes: dict[str, tuple[type, str | None]] = {}
        self._backend_aliases: dict[str, str] = {}        # alias -> canonical
        self._cli_commands: dict[str, dict] = {}          # name -> {handler, setup_parser, owner}
        self._cli_flag_values: dict[str, list[str]] = {}  # flag -> allowed values
        self._config_defaults: dict[str, dict] = {}       # env_prefix -> {KEY: value}
        self._config_owners: dict[str, str] = {}          # env_prefix -> plugin name
        self._config_env_vars: dict[str, str] = {}        # ENV var -> default value
        self._config_env_owners: dict[str, str] = {}      # ENV var -> env_prefix
        self._tools: dict[str, dict] = {}                 # name -> {tool, owner}
        self._hooks: dict[str, list[dict]] = {}           # event -> [entry]

        # Collected warnings (also printed); inspectable by tests/tooling.
        self.warnings: list[str] = []

    # ------------------------------------------------------------------ #
    #  Plugin roots                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_plugin_path_env(value: str) -> list[Path]:
        """Parse $AGENTKTHX_PLUGIN_PATH (os.pathsep-separated directories)."""
        return [
            Path(part.strip()).expanduser()
            for part in (value or "").split(os.pathsep)
            if part.strip()
        ]

    @staticmethod
    def _default_roots() -> list[tuple[Path, str]]:
        """Built-in root, user root, then $AGENTKTHX_PLUGIN_PATH entries."""
        roots: list[tuple[Path, str]] = [(Path(__file__).parent, "builtin")]
        roots.append((Path.home() / ".agentkthx" / "plugins", "user"))
        for extra in PluginManager.parse_plugin_path_env(
            os.environ.get("AGENTKTHX_PLUGIN_PATH", "")
        ):
            roots.append((extra, "env"))
        return roots

    def plugin_data_dir(self, name: str) -> Path:
        """
        Client-managed persistent data directory for one plugin instance
        (spec §Environment). Created on demand.
        """
        if os.name == "nt" or sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            data_dir = base / "agentkthx" / "plugins" / name
        else:
            xdg = os.environ.get("XDG_STATE_HOME")
            base = Path(xdg) if xdg else Path.home() / ".local" / "state"
            data_dir = base / "agentkthx" / "plugins" / name
        return data_dir

    # ------------------------------------------------------------------ #
    #  Discovery                                                         #
    # ------------------------------------------------------------------ #

    def discover(self, force: bool = False) -> list[PluginManifest]:
        """
        Scan all plugin roots for ``plugin.json`` manifests.

        Results are cached (``force=True`` re-scans). Directories starting
        with ``_`` or ``.`` are skipped. First root wins on name collisions.
        No plugin code is imported here.
        """
        if self._manifests is not None and not force:
            return list(self._manifests)

        manifests: list[PluginManifest] = []
        seen: set[str] = set()

        for root, kind in self._roots:
            if kind == "user" and not root.exists():
                try:
                    root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass  # unwritable home; skip silently
            if not root.is_dir():
                if kind == "env" and str(root):
                    _warn(self.warnings, f"plugin path does not exist, skipped: {root}")
                continue

            try:
                entries = sorted(root.iterdir())
            except OSError as e:
                _warn(self.warnings, f"cannot scan plugin root {root}: {e}")
                continue

            for entry in entries:
                if not entry.is_dir():
                    continue
                if entry.name.startswith("_") or entry.name.startswith("."):
                    continue

                manifest_path = entry / "plugin.json"
                if not manifest_path.exists():
                    continue

                try:
                    manifest = _parse_manifest(
                        manifest_path, root_kind=kind, warnings=self.warnings
                    )
                except Exception as e:
                    _warn(self.warnings, f"failed to parse {manifest_path}: {e}")
                    continue

                if manifest.name in seen:
                    _warn(
                        self.warnings,
                        f"duplicate plugin '{manifest.name}' in {root}; "
                        f"using the copy from a higher-priority root",
                    )
                    continue
                seen.add(manifest.name)
                manifest.dir = entry
                manifests.append(manifest)

        self._manifests = manifests
        return list(manifests)

    # ------------------------------------------------------------------ #
    #  Compatibility (warn-only, spec §Compatibility)                     #
    # ------------------------------------------------------------------ #

    def _framework_version(self) -> str:
        try:
            from .. import __version__ as fw_version
            return fw_version
        except Exception:
            return "0.0.0"

    def _check_compatibility(self, manifest: PluginManifest) -> None:
        compat = manifest.compatibility
        if not isinstance(compat, dict) or not compat:
            return
        fw_version = self._framework_version()
        parsed = parse_version(fw_version)
        for key_raw, constraint in compat.items():
            key = key_raw
            if key_raw == "agentnova":
                _warn(
                    self.warnings,
                    f"{manifest.name}: compatibility key 'agentnova' is "
                    f"deprecated; use 'agentkthx'",
                )
                key = "agentkthx"
            if key != "agentkthx":
                _warn(
                    self.warnings,
                    f"{manifest.name}: unknown compatibility key {key_raw!r} ignored",
                )
                continue
            if not isinstance(constraint, str):
                _warn(
                    self.warnings,
                    f"{manifest.name}: compatibility.{key} must be a string; ignored",
                )
                continue
            if not constraint_satisfied(parsed, constraint):
                _warn(
                    self.warnings,
                    f"{manifest.name}: requires agentkthx {constraint}, "
                    f"running {fw_version}; loading anyway (advisory)",
                )

    # ------------------------------------------------------------------ #
    #  Dependency Resolution                                              #
    # ------------------------------------------------------------------ #

    def _resolve_load_order(self, manifests: list[PluginManifest]) -> list[PluginManifest]:
        """
        Topological sort of manifests respecting ``depends`` (Kahn's algorithm).

        Plugins with missing hard dependencies are dropped from the result.
        Circular dependencies are detected and rejected (cycle members named).
        """
        available = {m.name for m in manifests}
        graph: dict[str, list[str]] = {m.name: [] for m in manifests}
        in_degree: dict[str, int] = {m.name: 0 for m in manifests}

        for m in manifests:
            for dep in m.depends:
                if dep in available:
                    graph[dep].append(m.name)
                    in_degree[m.name] += 1
                else:
                    _warn(
                        self.warnings,
                        f"skipping '{m.name}': missing dependency '{dep}'",
                    )
                    in_degree.pop(m.name, None)

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

        if len(ordered) != len(in_degree):
            remaining = sorted(set(in_degree.keys()) - set(ordered))
            _warn(
                self.warnings,
                f"circular dependency detected among: {', '.join(remaining)}",
            )

        manifest_map = {m.name: m for m in manifests}
        return [manifest_map[n] for n in ordered if n in manifest_map]

    # ------------------------------------------------------------------ #
    #  Import / Entrypoint                                                #
    # ------------------------------------------------------------------ #

    def _import_entrypoint(self, manifest: PluginManifest, plugin_dir: Path):
        """
        Import the plugin's entrypoint module (spec §Entrypoint contract).

        Built-ins import as ``agentkthx.plugins.<name>[.<entrypoint>]``.
        External roots load the package by path (hyphenated names welcome).
        """
        entry = manifest.entrypoint or "__init__"

        if manifest.root_kind == "builtin":
            pkg_name = f"agentkthx.plugins.{manifest.name}"
            importlib.import_module(pkg_name)
            if entry != "__init__":
                return importlib.import_module(f"{pkg_name}.{entry}")
            return sys.modules[pkg_name]

        # External root: load package by path
        safe = re.sub(r"[^A-Za-z0-9_]", "_", manifest.name)
        pkg_name = f"agentkthx_ext_plugins_{safe}"
        if pkg_name in sys.modules:
            package = sys.modules[pkg_name]
        else:
            init_file = plugin_dir / "__init__.py"
            if not init_file.exists():
                raise ValueError(f"external plugin missing __init__.py: {plugin_dir}")
            spec = importlib.util.spec_from_file_location(
                pkg_name, init_file, submodule_search_locations=[str(plugin_dir)]
            )
            if spec is None or spec.loader is None:
                raise ValueError(f"cannot create import spec for {plugin_dir}")
            package = importlib.util.module_from_spec(spec)
            sys.modules[pkg_name] = package
            spec.loader.exec_module(package)

        if entry != "__init__":
            return importlib.import_module(f"{pkg_name}.{entry}")
        return package

    # ------------------------------------------------------------------ #
    #  Load / Unload                                                     #
    # ------------------------------------------------------------------ #

    def load(self, name: str) -> Plugin | None:
        """Load a single plugin by name (uses the discovery cache)."""
        if name in self._plugins and self._plugins[name].loaded:
            return self._plugins[name]

        manifest = next(
            (m for m in self.discover() if m.name == name), None
        )
        if manifest is None:
            _warn(self.warnings, f"plugin not found: {name}")
            return None

        for dep in manifest.depends:
            if dep not in self._plugins or not self._plugins[dep].loaded:
                _warn(
                    self.warnings,
                    f"cannot load '{name}': dependency '{dep}' not loaded",
                )
                return None

        return self._load_plugin(manifest, manifest.dir or self._plugins_dir / name)

    def load_all(self) -> list[Plugin]:
        """
        Discover and load all plugins in dependency order.

        Idempotent: already-loaded plugins are skipped (the CLI calls this
        both at startup and during subparser wiring).
        """
        manifests = self.discover()
        ordered = self._resolve_load_order(manifests)

        loaded: list[Plugin] = []
        for manifest in ordered:
            existing = self._plugins.get(manifest.name)
            if existing is not None and existing.loaded:
                continue
            plugin = self._load_plugin(
                manifest, manifest.dir or self._plugins_dir / manifest.name
            )
            if plugin is not None:
                loaded.append(plugin)

        if loaded:
            names = [p.manifest.name for p in loaded]
            print(
                f"[PluginManager] Loaded {len(loaded)} plugin(s): {', '.join(names)}",
                file=sys.stderr,
            )

        # on_init: emitted once, after all plugins are loaded (spec §Lifecycle)
        if not self._on_init_emitted:
            self._on_init_emitted = True
            self.emit("on_init", {"plugin_manager": self})

        return loaded

    def _load_plugin(self, manifest: PluginManifest, plugin_dir: Path) -> Plugin | None:
        """Import the entrypoint, call ``register()``, merge declarations."""
        # Compatibility check first: advisory (warn-only) in v0.2
        self._check_compatibility(manifest)

        plugin = Plugin(manifest=manifest, path=plugin_dir)
        plugin.root = plugin_dir

        # PLUGIN_DATA: create before register() (spec §Environment)
        try:
            data_dir = self.plugin_data_dir(manifest.name)
            data_dir.mkdir(parents=True, exist_ok=True)
            plugin.data_dir = data_dir
        except OSError as e:
            _warn(
                self.warnings,
                f"cannot create plugin data dir for '{manifest.name}': {e}",
            )

        try:
            module = self._import_entrypoint(manifest, plugin_dir)
            plugin.module = module

            if not hasattr(module, "register") or not callable(module.register):
                raise ValueError(
                    f"entrypoint module {manifest.entrypoint!r} has no register() function"
                )

            module.register(self)

            plugin.loaded = True
            self._plugins[manifest.name] = plugin
            self._failed.pop(manifest.name, None)

            # Merge manifest-declared config (expanded), CLI flags, hooks
            self._merge_manifest_config(manifest, plugin)

            flag_values = manifest.provides.get("cli_flags", {})
            if isinstance(flag_values, dict):
                for flag, values in flag_values.items():
                    self._cli_flag_values.setdefault(flag, [])
                    for v in values:
                        if v not in self._cli_flag_values[flag]:
                            self._cli_flag_values[flag].append(v)

            hooks = manifest.provides.get("hooks", {})
            if isinstance(hooks, dict):
                for event, spec in hooks.items():
                    self._hooks.setdefault(event, []).append(
                        {
                            "plugin": manifest.name,
                            "fn": None,
                            "spec": spec,
                            "resolved": False,
                            "failed": False,
                        }
                    )

            return plugin

        except Exception as e:
            # Failure boundary: this plugin fails, others continue.
            _warn(self.warnings, f"failed to load plugin '{manifest.name}': {e}")
            plugin.failed = True
            plugin.error = str(e)
            self._failed[manifest.name] = str(e)

            if plugin.module is not None and hasattr(plugin.module, "unregister"):
                try:
                    plugin.module.unregister(self)
                except Exception as ue:
                    _warn(
                        self.warnings,
                        f"unregister() also failed for '{manifest.name}': {ue}",
                    )
            self._purge_provides(manifest)
            # Purge config registered before the failure (imperative or merged)
            env_prefix = manifest.config.get("env_prefix") if manifest.config else None
            for prefix, owner in list(self._config_owners.items()):
                if owner == manifest.name or (env_prefix and prefix == env_prefix):
                    for key in self._config_defaults.pop(prefix, {}):
                        self._config_env_vars.pop(key, None)
                        self._config_env_owners.pop(key, None)
                    self._config_owners.pop(prefix, None)
            return None

    def unload(self, name: str) -> bool:
        """
        Unload a plugin: call ``unregister()``, purge everything it
        registered (backends, CLI, config, tools, hooks).
        """
        plugin = self._plugins.get(name)
        if plugin is None or not plugin.loaded:
            return False

        manifest = plugin.manifest
        try:
            if plugin.module and hasattr(plugin.module, "unregister"):
                plugin.module.unregister(self)
        except Exception as e:
            _warn(self.warnings, f"error unloading '{name}': {e}")

        self._purge_provides(manifest)

        # Purge config defaults owned by this plugin
        env_prefix = manifest.config.get("env_prefix") if manifest.config else None
        for prefix, owner in list(self._config_owners.items()):
            if owner == name or (env_prefix and prefix == env_prefix):
                for key in self._config_defaults.pop(prefix, {}):
                    self._config_env_vars.pop(key, None)
                    self._config_env_owners.pop(key, None)
                self._config_owners.pop(prefix, None)

        # Purge tools owned by this plugin
        declared_tools = manifest.provides.get("tools", [])
        for tool_name, info in list(self._tools.items()):
            if info.get("owner") == name or tool_name in declared_tools:
                self._tools.pop(tool_name, None)

        # Purge hooks owned by this plugin (declarative + imperative)
        for event, entries in self._hooks.items():
            self._hooks[event] = [e for e in entries if e.get("plugin") != name]

        plugin.loaded = False
        return True

    def _purge_provides(self, manifest: PluginManifest) -> None:
        """Remove everything the manifest's ``provides`` declared."""
        # Backends
        for bname in manifest.provides.get("backends", {}):
            self._backend_classes.pop(bname, None)
            self._backend_aliases.pop(bname, None)

        # CLI commands
        for cmd in manifest.provides.get("cli_commands", []):
            self._cli_commands.pop(cmd, None)

        # CLI flag values
        for flag, values in manifest.provides.get("cli_flags", {}).items():
            if flag in self._cli_flag_values:
                for v in values:
                    if v in self._cli_flag_values[flag]:
                        self._cli_flag_values[flag].remove(v)

        # Tools declared but perhaps registered imperatively
        for tool_name in manifest.provides.get("tools", []):
            info = self._tools.get(tool_name)
            if info is not None and info.get("owner") in (None, manifest.name):
                self._tools.pop(tool_name, None)

        # Declarative hooks
        for event in manifest.provides.get("hooks", {}):
            entries = self._hooks.get(event)
            if entries:
                self._hooks[event] = [
                    e for e in entries if e.get("plugin") != manifest.name
                ]

    # ------------------------------------------------------------------ #
    #  Config Extension (with expansion + secret guard)                   #
    # ------------------------------------------------------------------ #

    def _merge_manifest_config(self, manifest: PluginManifest, plugin: Plugin) -> None:
        config = manifest.config or {}
        env_prefix = config.get("env_prefix", "")
        defaults = config.get("defaults", {})
        if not env_prefix or not isinstance(defaults, dict):
            return
        expanded = {
            key: expand_placeholders(val, plugin.root, plugin.data_dir)
            if isinstance(val, str)
            else val
            for key, val in defaults.items()
        }
        self.register_config_defaults(env_prefix, expanded, plugin=manifest.name)

    def register_config_defaults(
        self, env_prefix: str, defaults: dict, *, plugin: str | None = None
    ) -> None:
        """
        Register config defaults. ``plugin`` records ownership so that
        ``unload()`` can purge them (spec §Lifecycle).
        """
        self._config_defaults[env_prefix] = dict(defaults)
        if plugin:
            self._config_owners[env_prefix] = plugin
        for key, val in defaults.items():
            self._config_env_vars[key] = val
            self._config_env_owners[key] = env_prefix
            if (
                isinstance(val, str)
                and val
                and SECRET_SUFFIX_RE.search(key)
            ):
                _warn(
                    self.warnings,
                    f"{plugin or env_prefix}: config default '{key}' looks like a "
                    f"secret with a non-empty default; provide secrets via the "
                    f"environment instead",
                )

    def get_all_config_defaults(self) -> dict[str, str]:
        """Flat ``{ENV_VAR: default}`` dict of all plugin configs."""
        return dict(self._config_env_vars)

    def get_config_defaults_by_prefix(self, env_prefix: str) -> dict[str, str]:
        """Config defaults for one plugin's env prefix."""
        return dict(self._config_defaults.get(env_prefix, {}))

    # ------------------------------------------------------------------ #
    #  Backend Registration                                               #
    # ------------------------------------------------------------------ #

    def register_backend(
        self, name: str, cls: type, *, alias_of: str | None = None, plugin: str | None = None
    ) -> None:
        """
        Register a backend class provided by a plugin.

        The class MUST subclass ``BaseBackend``; failing that, the
        individual registration is rejected with a warning (spec §Plugin
        types / §Failure boundaries).
        """
        if alias_of:
            self._backend_aliases[name] = alias_of
            return
        try:
            from ..backends.base import BaseBackend
            if not (isinstance(cls, type) and issubclass(cls, BaseBackend)):
                _warn(
                    self.warnings,
                    f"backend '{name}' rejected: {cls!r} is not a BaseBackend subclass",
                )
                return
        except ImportError:
            _warn(
                self.warnings,
                f"backend '{name}' rejected: BaseBackend unavailable to verify",
            )
            return
        self._backend_classes[name] = (cls, plugin)

    def unregister_backend(self, name: str) -> None:
        """Remove a plugin-registered backend."""
        self._backend_classes.pop(name, None)
        self._backend_aliases.pop(name, None)

    def find_plugin_for_backend(self, backend_name: str) -> str | None:
        """Reverse-mapping backend name -> plugin name (uses the cache).

        Checks two locations so that aliases declared in
        ``cli_flags."--backend"`` (e.g. ``"hf"`` for the huggingface
        plugin) resolve to the right plugin before the plugin's
        ``register()`` has had a chance to call
        ``register_backend(alias_of=...)``. Without this lookup, the
        ``_ensure_plugin()`` chicken-and-egg path fails because the
        alias map is only populated AFTER the plugin loads, but the
        plugin loads only AFTER ``_ensure_plugin`` resolves the name.
        """
        for manifest in self.discover():
            if backend_name in manifest.provides.get("backends", {}):
                return manifest.name
            # Also check the --backend cli_flags for declared aliases
            # (e.g. the huggingface plugin declares ["huggingface", "hf"]
            # — both should resolve to the "huggingface" plugin name).
            cli_backend_choices = (
                manifest.provides.get("cli_flags", {}).get("--backend", [])
            )
            if backend_name in cli_backend_choices:
                return manifest.name
        return None

    def get_backend_class(self, name: str) -> type | None:
        """Get a backend class by name (aliases resolved)."""
        if name in self._backend_classes:
            return self._backend_classes[name][0]
        canonical = self._backend_aliases.get(name)
        if canonical and canonical in self._backend_classes:
            return self._backend_classes[canonical][0]
        return None

    def list_backend_names(self) -> list[str]:
        """All plugin-registered backend names (not including core)."""
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
        *,
        plugin: str | None = None,
    ) -> None:
        """Register a CLI subcommand provided by a plugin."""
        self._cli_commands[name] = {
            "handler": handler,
            "setup_parser": setup_parser,
            "owner": plugin,
        }

    def unregister_cli_command(self, name: str) -> None:
        """Remove a plugin-registered CLI command."""
        self._cli_commands.pop(name, None)

    def get_cli_commands(self) -> dict[str, dict]:
        """All plugin-registered CLI commands."""
        return dict(self._cli_commands)

    def get_backend_choices(self) -> list[str]:
        """Merged ``--backend`` choices (core + plugin values)."""
        core = ["ollama", "llama-server", "llama_server"]
        plugin_values = self._cli_flag_values.get("--backend", [])
        return core + [v for v in plugin_values if v not in core]

    # ------------------------------------------------------------------ #
    #  Tools (spec §Tools)                                                #
    # ------------------------------------------------------------------ #

    def register_tool(self, tool: Any, *, plugin: str | None = None) -> bool:
        """
        Register a tool (a ``agentkthx.core.models.Tool`` object).

        Name collisions with core/other-plugin tools are rejected with a
        warning; the existing tool stays untouched.
        """
        name = getattr(tool, "name", None)
        if not name or not isinstance(name, str):
            _warn(self.warnings, f"tool rejected from '{plugin}': missing a string 'name'")
            return False
        if name in self._tools:
            _warn(
                self.warnings,
                f"tool '{name}' rejected from '{plugin}': already registered "
                f"(owner: {self._tools[name].get('owner')})",
            )
            return False
        self._tools[name] = {"tool": tool, "owner": plugin}
        return True

    def unregister_tool(self, name: str) -> None:
        """Remove a tool registration."""
        self._tools.pop(name, None)

    def list_tool_names(self) -> list[str]:
        """All plugin-registered tool names."""
        return sorted(self._tools.keys())

    def get_tool(self, name: str) -> Any | None:
        """Look up a plugin-registered tool."""
        info = self._tools.get(name)
        return info["tool"] if info else None

    def plugin_tools(self) -> list[Any]:
        """All plugin-registered Tool objects (for merging into a registry)."""
        return [info["tool"] for info in self._tools.values()]

    def apply_to_registry(self, registry: Any) -> int:
        """
        Merge plugin tools into a core ``ToolRegistry`` instance.
        Returns the number of tools added. Used by ``Agent.__init__``.
        """
        added = 0
        for tool in self.plugin_tools():
            try:
                registry.register_tool(tool)
                added += 1
            except Exception as e:
                _warn(self.warnings, f"could not add plugin tool {tool!r}: {e}")
        return added

    # ------------------------------------------------------------------ #
    #  Hooks (spec §Hooks)                                                #
    # ------------------------------------------------------------------ #

    def register_hook(self, event: str, fn: Callable, *, plugin: str | None = None) -> None:
        """Register a handler for a lifecycle event (imperative form)."""
        if event not in KNOWN_HOOK_EVENTS:
            _warn(
                self.warnings,
                f"unknown hook event '{event}' from '{plugin}' "
                f"(known: {sorted(KNOWN_HOOK_EVENTS)})",
            )
        self._hooks.setdefault(event, []).append(
            {"plugin": plugin, "fn": fn, "spec": None, "resolved": True, "failed": False}
        )

    def unregister_hook(self, event: str, fn: Callable) -> None:
        """Remove a specific handler from an event."""
        entries = self._hooks.get(event)
        if entries:
            self._hooks[event] = [e for e in entries if e.get("fn") is not fn]

    def _resolve_hook(self, entry: dict) -> Callable | None:
        """Lazily resolve a declarative hook spec (warn once on failure)."""
        if entry.get("resolved"):
            return entry.get("fn")
        if entry.get("failed"):
            return None

        plugin = self._plugins.get(entry.get("plugin") or "")
        spec_path = entry.get("spec")
        base = getattr(getattr(plugin, "module", None), "__name__", None)

        fn = None
        if isinstance(spec_path, str) and base:
            if "." in spec_path:
                mod_name, func_name = spec_path.rsplit(".", 1)
                try:
                    mod = importlib.import_module(f"{base}.{mod_name}")
                    fn = getattr(mod, func_name, None)
                except Exception as e:
                    _warn(
                        self.warnings,
                        f"hook '{spec_path}' of '{entry.get('plugin')}' "
                        f"unresolvable: {e}",
                    )
            else:
                fn = getattr(plugin.module, spec_path, None)
        elif isinstance(spec_path, str):
            # No loaded module (declarative spec after unload) — resolve
            # against the entrypoint module name directly.
            try:
                mod = importlib.import_module(spec_path)
                fn = mod
            except Exception:
                fn = None

        if callable(fn):
            entry["fn"] = fn
            entry["resolved"] = True
            return fn

        entry["failed"] = True
        _warn(
            self.warnings,
            f"hook spec '{spec_path}' of '{entry.get('plugin')}' not found or not callable",
        )
        return None

    def emit(self, event: str, context: dict) -> None:
        """
        Run all handlers for an event with per-hook error isolation
        (spec §Hooks): a raising handler warns and the rest still run.
        """
        for entry in list(self._hooks.get(event, [])):
            fn = self._resolve_hook(entry)
            if fn is None:
                continue
            try:
                fn(context)
            except Exception as e:
                _warn(
                    self.warnings,
                    f"hook '{event}' from '{entry.get('plugin')}' raised: {e}",
                )

    def list_hooks(self, event: str | None = None) -> list[dict]:
        """List hook registrations (optionally for one event)."""
        events = [event] if event else sorted(self._hooks.keys())
        out = []
        for ev in events:
            for entry in self._hooks.get(ev, []):
                out.append(
                    {
                        "event": ev,
                        "plugin": entry.get("plugin"),
                        "spec": entry.get("spec"),
                        "resolved": entry.get("resolved", False),
                    }
                )
        return out

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

    def get_plugin_data_dir(self, name: str) -> Path | None:
        """PLUGIN_DATA for a plugin (None if never loaded)."""
        plugin = self._plugins.get(name)
        return plugin.data_dir if plugin else None

    def get_plugin_state(self, name: str) -> str:
        """'loaded' | 'failed' | 'discovered'."""
        plugin = self._plugins.get(name)
        if plugin is not None:
            return plugin.state
        if name in self._failed:
            return "failed"
        if any(m.name == name for m in self.discover()):
            return "discovered"
        return "unknown"

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


def get_plugin_manager(init: bool = True) -> PluginManager | None:
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
    "EXT_NAMESPACE",
    "CANONICAL_SCHEMA",
    "KNOWN_HOOK_EVENTS",
    "expand_placeholders",
    "constraint_satisfied",
    "valid_plugin_name",
    "parse_version",
]
