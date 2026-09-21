# AgentKthx Plugin Specification v0.2

**Status: Implemented** (loader, CLI, hooks, tools, manifests, and tests land in the same release — see Appendix B for the implemented change map)

**Supersedes:** Plugin Specification v0.1 (`docs/PLUGIN_SPEC.md`)

Written by VTSTech — https://www.vts-tech.org

## Table of contents

1. [Overview](#overview)
2. [Conformance language](#conformance-language)
3. [Terminology](#terminology)
4. [Plugin roots and discovery](#plugin-roots-and-discovery)
5. [Directory layout](#directory-layout)
6. [Manifest format (`plugin.json`)](#manifest-format-pluginjson)
7. [Extensions](#extensions)
8. [Plugin types](#plugin-types)
9. [Entrypoint contract](#entrypoint-contract)
10. [Provides](#provides)
11. [Hooks](#hooks)
12. [Tools](#tools)
13. [Config](#config)
14. [Environment variables and placeholder expansion](#environment-variables-and-placeholder-expansion)
15. [Compatibility](#compatibility)
16. [Dependency resolution](#dependency-resolution)
17. [Lifecycle](#lifecycle)
18. [Failure boundaries](#failure-boundaries)
19. [CLI integration](#cli-integration)
20. [Packaging and distribution](#packaging-and-distribution)
21. [Migration from v0.1](#migration-from-v01)
22. [Appendix A: Conformance checklist](#appendix-a-conformance-checklist)
23. [Appendix B: Implementation checklist](#appendix-b-implementation-checklist)
24. [Changelog](#changelog)

## Overview

AgentKthx plugins extend the framework without modifying core code. Plugins can
provide inference backends, CLI commands, configuration defaults, tools, and
lifecycle hooks. Discovery is done via directory scanning — no pip installation
required.

v0.2 keeps the v0.1 directory-scan, `plugin.json`-manifest model and adds:

- **`$schema` field** — manifest version targeting with a canonical, per-release
  schema identifier (aligned with the Agent Plugins Specification 1.0.0).
- **`extensions` namespace** — AgentKthx-specific manifest fields move under a
  reverse-domain namespace (`org.vts-tech.agentkthx`), making an AgentKthx
  plugin structurally compatible with the Agent Plugins package format.
  Legacy top-level fields continue to load (dual-form, deprecated in v0.3).
- **`tools` and `hook` plugin types** — previously "future", now normative.
  Tools bridge to the core `ToolRegistry`; hooks observe framework lifecycle
  events.
- **External plugin roots** — plugins can live outside the installed package:
  `~/.agentkthx/plugins/` and `$AGENTKTHX_PLUGIN_PATH`.
- **`PLUGIN_ROOT` / `PLUGIN_DATA`** — standardized environment variables and
  per-plugin persistent data directories.
- **Honored `entrypoint`** — the field now actually selects the module that
  provides `register()` / `unregister()`.
- **Enforced (warn-only) `compatibility`** — version constraints are checked at
  load time; mismatches warn but do not block loading.
- **Strict name rules, author-as-object, failure boundaries, complete unload,
  CLI plugin management** (`agentkthx plugins --load/--unload/--reload`).

## Conformance language

In this document, the key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT,
SHOULD, SHOULD NOT, RECOMMENDED, MAY, and OPTIONAL are to be interpreted as
described in RFC 2119 and RFC 8174 when, and only when, they appear in all
capitals.

Appendix A and Appendix B are non-normative. All other sections are normative.

Because AgentKthx currently has exactly one conformant client (the AgentKthx
framework itself), "the client" below means the AgentKthx PluginManager. The
requirements are written client-agnostically so that independent
reimplementations (e.g. AgentNova) can claim v0.2 conformance.

## Terminology

| Term | Kind | Description |
|------|------|-------------|
| Plugin | Package unit | A self-contained directory with a manifest and plugin code. |
| Plugin root | Filesystem path | The top-level directory of one plugin package (contains `plugin.json`). |
| Plugin root directory | Search path | A directory containing one or more plugin roots (see §Plugin roots). |
| Manifest | Metadata document | A `plugin.json` file at the plugin root. |
| Entrypoint | Code module | The Python module named by `entrypoint`; provides `register()` / `unregister()`. |
| Provides | Declaration | Manifest section declaring what a plugin registers: backends, CLI commands/flags, tools, hooks. |
| Extension namespace | Namespaced key | A reverse-domain identifier (`org.vts-tech.agentkthx`) under which client-specific manifest data lives. |
| `PLUGIN_ROOT` | Environment variable | Absolute path to a plugin's filesystem-resolved root, set for plugin subprocesses. |
| `PLUGIN_DATA` | Environment variable | Absolute path to a client-managed persistent data directory for one plugin instance. |

## Plugin roots and discovery

The client MUST discover plugins by scanning *plugin root directories* in the
following order:

| Priority | Plugin root directory | Purpose |
|----------|----------------------|---------|
| 1 (built-in) | `agentkthx/plugins/` (the package directory itself) | Plugins shipped inside the AgentKthx wheel. |
| 2 (user) | `~/.agentkthx/plugins/` (`Path.home() / ".agentkthx" / "plugins"`) | User-installed third-party plugins. Created on demand if absent. |
| 3 (extra) | Each directory listed in `$AGENTKTHX_PLUGIN_PATH` | Development, testing, and custom deployments. |

`$AGENTKTHX_PLUGIN_PATH` syntax:

- Values are separated by `os.pathsep` (`:` on POSIX, `;` on Windows).
- Each entry MUST be a directory containing plugin roots (one level of
  indirection — the entry itself is not a plugin root).
- Empty entries are ignored. Nonexistent or non-directory entries are ignored
  with a warning. Relative entries are resolved against the current working
  directory at discovery time.

Discovery rules:

1. Each plugin root directory MUST be scanned for immediate subdirectories.
2. Subdirectories whose names start with `_` or `.` MUST be skipped.
3. A subdirectory that does not contain a `plugin.json` file is not a plugin;
   the client MUST ignore it.
4. A plugin directory whose manifest `name` does not match the directory name
   MUST be rejected with a warning (v0.1 silently misbehaved here).
5. **Name collisions:** if two plugin roots contain plugins with the same
   `name`, the plugin from the highest-priority root MUST win and the duplicate
   MUST be skipped with a warning.
6. Discovery MUST NOT import any plugin code. Parsing `plugin.json` is the only
   I/O performed at discovery time.

Example:

```bash
# Development: load plugins from a work tree
export AGENTKTHX_PLUGIN_PATH="$HOME/work/my-plugins:/opt/shared-plugins"
agentkthx plugins --verbose
```

## Directory layout

Built-in (shipped in the wheel):

```
agentkthx/plugins/
├── _loader.py           # PluginManager implementation
├── bitnet/              # Example: backend plugin
│   ├── plugin.json      # Manifest (required)
│   ├── __init__.py      # register() / unregister() entrypoints
│   └── bitnet.py        # Plugin implementation
└── turboquant/          # Example: feature plugin
    ├── plugin.json
    ├── __init__.py
    └── turbo.py
```

User-installed (external):

```
~/.agentkthx/plugins/
└── my-backend/          # Directory name MUST equal manifest "name"
    ├── plugin.json
    ├── __init__.py
    ├── backend.py
    └── hooks.py         # Optional: hook handlers referenced by provides.hooks
```

Rules:

- Each plugin lives in its own subdirectory of a plugin root directory.
- Every plugin directory MUST contain a `plugin.json` manifest at its root.
- Built-in plugin manifests MUST be listed in `pyproject.toml` package-data
  (`plugins/*/plugin.json`); Python modules ship via the normal package find
  pattern.
- Plugin code SHOULD lazy-import heavy modules inside `register()` rather than
  at module level.

## Manifest format (`plugin.json`)

### Location and loading

The client MUST check for a manifest at `plugin.json` in the plugin root. No
other file can replace or supplement it. The manifest MUST be JSON and MUST
contain a top-level object.

The client MUST NOT fetch the schema over the network. A local copy of the
schema ships with the framework at `schemas/v0.2/plugin.schema.json`.

### `$schema`

The optional `$schema` field identifies the specification version the plugin
targets. For v0.2 its canonical value is:

```
https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json
```

- If `$schema` is absent, the client MUST fall back to legacy (v0.1) parsing
  rules and SHOULD emit a deprecation warning.
- If `$schema` is present and recognized, v0.2 parsing rules apply and
  `extensions` is the canonical location for client-specific fields.
- If `$schema` is present but unrecognized, the client MUST warn and continue
  loading with best-effort v0.2 rules. (A single-client ecosystem favors
  recovery over rejection; a future major version MAY harden this.)

### Minimal manifest (v0.2 form)

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "my-backend",
  "description": "My custom inference backend",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "type": "backend",
      "entrypoint": "__init__",
      "provides": {
        "backends": { "my-backend": "backend.MyBackend" },
        "cli_flags": { "--backend": ["my-backend"] }
      }
    }
  }
}
```

### Legacy manifest (v0.1 form — still valid, deprecated)

```json
{
  "name": "my-backend",
  "version": "0.1.0",
  "display_name": "My Plugin",
  "description": "What this plugin does",
  "author": "Author Name",
  "license": "MIT",
  "type": "backend",
  "entrypoint": "__init__",
  "depends": [],
  "optional_depends": [],
  "config": { "env_prefix": "MY_PLUGIN", "defaults": {} },
  "provides": { "backends": { "my-backend": "module.BackendClass" } },
  "compatibility": { "agentkthx": ">=0.5.0" }
}
```

All v0.1 top-level fields MUST continue to parse in v0.2. See
[Migration from v0.1](#migration-from-v01) for the field mapping and
deprecation timeline.

### Field reference (core fields)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `$schema` | `string` | No | — | Canonical schema identifier. See §$schema. |
| `name` | `string` | **Yes** | — | Unique plugin identifier. Constraints below. MUST match the directory name. |
| `version` | `string` | No | `"0.0.0"` | Semver version string (v0.1 required this field; v0.2 reconciles spec and code by making it optional). |
| `description` | `string` | **Yes** | — | Short description of what the plugin does. |
| `author` | `string` or `object` | No | `""` | Author name, or object with optional `name`, `email`, `url` string fields (Agent Plugins form). Any other object field makes the manifest invalid. |
| `license` | `string` | No | `""` | SPDX license identifier. Validated against the SPDX list already shipped in `skills/loader.py`; invalid values warn but do not reject. |
| `extensions` | `object` | No | `{}` | Client-specific data keyed by extension namespace. See §Extensions. |

### Field reference (AgentKthx extension fields — canonical home under `extensions`)

These fields live under `extensions["org.vts-tech.agentkthx"]` in v0.2 form.
Each is also accepted as a legacy top-level field with a deprecation warning.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `display_name` | `string` | No | `name` | Human-readable name shown in `agentkthx plugins`. |
| `type` | `string` | No | `"feature"` | Plugin type. One of: `backend`, `feature`, `tools`, `hook`. Unknown values warn and load without type-specific behavior (forward compatibility). |
| `entrypoint` | `string` | No | `"__init__"` | Python module **inside the plugin directory** that provides `register()`/`unregister()`. This field is now honored by the loader (v0.1 ignored it). |
| `depends` | `string[]` | No | `[]` | Hard dependencies — plugin names that must load first. Missing dependencies cause this plugin to be skipped with a warning. |
| `optional_depends` | `string[]` | No | `[]` | Soft dependencies — plugin loads even if these are missing (future: conditional features). |
| `config` | `object` | No | `{}` | Configuration defaults. See §Config. |
| `provides` | `object` | No | `{}` | What the plugin registers. See §Provides. |
| `compatibility` | `object` | No | `{}` | Version constraints. See §Compatibility. |

Unknown top-level fields and unknown fields inside the AgentKthx extension
namespace MUST be reported with a warning and ignored. The client MUST NOT
assign semantics to unknown fields. (Unlike the Agent Plugins Specification,
v0.2 does not treat unknown fields as fatal; closing the schema is planned for
v0.3 once the dual-form migration completes.)

### Plugin name constraints

The manifest `name` value MUST satisfy all of the following:

| Constraint | Requirement | Description |
|------------|-------------|-------------|
| Length | 1–64 characters | Inclusive. |
| Character set | `a-z`, `0-9`, `-`, `.` | Lowercase alphanumeric, hyphens, and periods only. |
| Start and end | Alphanumeric | First and last characters MUST be alphanumeric. |
| Repetition | No `--` or `..` | Consecutive hyphens and consecutive periods are not allowed. |
| Directory match | Equal to directory name | Enforced at discovery (see §Plugin roots and discovery). |

Valid names: `my-plugin`, `acme.tools`, `lint3r`, `a`
Invalid names: `My-Plugin` (uppercase), `-start` (leading hyphen),
`has--double` (consecutive hyphens), `too.many..dots` (consecutive periods),
`` (empty)

The name MUST also be a valid Python package identifier component when the
plugin lives in a package root (no periods in that case — a dotted name is
only valid for external roots loaded by path).

## Extensions

Client-specific manifest data MUST be represented under a reverse-domain
namespace in the top-level `extensions` object. AgentKthx's namespace is:

```
org.vts-tech.agentkthx
```

(reverse-domain form of `vts-tech.org`, which VTSTech controls; `agentkthx`
scopes the namespace to this client so the same manifest can later carry
namespaces for other clients, e.g. AgentNova.)

### Processing rules (dual-form migration)

1. For every AgentKthx-specific field (`display_name`, `type`, `entrypoint`,
   `depends`, `optional_depends`, `config`, `provides`, `compatibility`), the
   client MUST read from `extensions["org.vts-tech.agentkthx"]` first.
2. If the field is absent from the extension object, the client MUST fall back
   to the legacy top-level field of the same name and SHOULD emit a
   **deprecation warning** naming the plugin and the field.
3. If the field is present in **both** places, the extension value MUST win
   and the client MUST warn about the conflict.
4. The client MUST ignore extension namespaces it does not implement, without
   validating their contents.
5. If `extensions` is present but not an object, the client MUST warn and
   ignore it, continuing to legacy top-level fields.

### Example: same plugin in both forms

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "turbo",
  "version": "1.0.0",
  "description": "TurboQuant server lifecycle management",
  "author": { "name": "VTSTech", "url": "https://www.vts-tech.org" },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "TurboQuant Server Manager",
      "type": "feature",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "TURBOQUANT",
        "defaults": { "TURBOQUANT_PORT": "8764" }
      },
      "provides": { "cli_commands": ["turbo"] },
      "compatibility": { "agentkthx": ">=0.5.0" }
    }
  }
}
```

This manifest is simultaneously a structurally valid Agent Plugins package
(its core fields — `name`, `version`, `description`, `author`, `license` —
match the Agent Plugins 1.0.0 manifest; the AgentKthx-specific data is
namespaced where Agent Plugins expects client data). Full cross-spec
conformance is a non-goal for v0.2; structural alignment is the intent.

## Plugin types

### `backend`

Registers an inference backend class that can be selected via
`--backend <name>`.

```json
{
  "provides": {
    "backends": { "bitnet": "bitnet.BitNetBackend" },
    "cli_flags": { "--backend": ["bitnet"] }
  }
}
```

The backend class MUST be a subclass of `BaseBackend`
(`agentkthx/backends/base.py`). The client MUST verify the subclass
relationship at registration time and MUST reject the individual backend
registration (with a warning) if it does not hold. Core backends
(`ollama`, `llama-server`) cannot be removed or overridden by plugins;
plugin backends are additive.

### `feature`

Extends framework functionality — CLI commands, configuration, integrations.

```json
{
  "provides": { "cli_commands": ["turbo"] }
}
```

Feature plugins do not provide backends but can add subcommands, config
defaults, tools, or hooks (any plugin type may provide tools/hooks; `feature`
is simply the default type when a plugin provides none of the backend
behaviors).

### `tools`

A plugin whose primary purpose is registering custom tools agents can invoke.
Tools are registered imperatively via the PluginManager (see §Tools); the
`tools` type is a semantic marker for listing and discovery, not a distinct
registration path.

```json
{
  "extensions": {
    "org.vts-tech.agentkthx": {
      "type": "tools",
      "provides": { "tools": ["crypto-quotes"] }
    }
  }
}
```

### `hook`

A plugin whose primary purpose is observing framework lifecycle events. See
§Hooks for the event contract.

```json
{
  "extensions": {
    "org.vts-tech.agentkthx": {
      "type": "hook",
      "provides": {
        "hooks": { "on_run_start": "hooks.on_run_start" }
      }
    }
  }
}
```

## Entrypoint contract

The entrypoint is the Python module named by `entrypoint` inside the plugin
directory (default `"__init__"`).

**Import rules (normative in v0.2 — v0.1 documented but never honored this
field):**

- If `entrypoint` is `"__init__"`, the client MUST import the plugin package
  itself (`agentkthx.plugins.<name>` for built-ins, or the directory loaded
  by path for external plugins).
- Otherwise, the client MUST import `agentkthx.plugins.<name>.<entrypoint>`
  (built-ins) or the named module inside the plugin directory (external),
  after importing the package.
- A missing entrypoint module MUST fail the plugin load with a warning.

Every plugin MUST expose:

```python
def register(manager) -> None:
    """
    Called when the plugin is loaded.

    Use this to register backends, CLI commands, config defaults, tools,
    and hooks. The `manager` argument is the PluginManager singleton.

    Lazy imports are RECOMMENDED — import heavy modules inside register()
    rather than at module level.
    """
    from .my_module import MyBackend
    manager.register_backend("my-backend", MyBackend)


def unregister(manager) -> None:
    """
    Called when the plugin is unloaded or when load fails partway.

    Remove all registrations made in register().
    """
    manager.unregister_backend("my-backend")
```

- `register` is REQUIRED. A plugin whose entrypoint lacks `register` MUST
  fail to load with a warning (v0.1 silently marked it loaded).
- `unregister` is REQUIRED. If absent, the client MUST warn and perform
  best-effort cleanup from the manifest's `provides` declarations.
- If `register()` raises, the client MUST call `unregister()` best-effort,
  purge all manifest-declared `provides` registrations, mark the plugin as
  **failed** (not loaded), warn, and continue loading other plugins.

**Important:** use lazy imports inside `register()` to avoid loading plugin
code at discovery time. The PluginManager only imports the entrypoint module
when `load()` is called.

## Provides

`provides` declares everything a plugin registers. The client uses it for
loading (with `backends`), CLI surface (`cli_commands`, `cli_flags`),
ownership tracking on unload (all keys), and reporting.

| Key | Shape | Purpose |
|-----|-------|---------|
| `backends` | `{ name: "module.ClassPath" }` | Backend names mapped to class paths relative to the plugin directory. Class MUST subclass `BaseBackend`. |
| `cli_commands` | `["name", ...]` | CLI subcommand names the plugin registers. |
| `cli_flags` | `{ "--flag": ["value", ...] }` | Extends valid values for CLI flags. v0.1 supported `--backend`; v0.2 clients MAY support arbitrary flags and MUST ignore unsupported flags with a warning. |
| `tools` | `["tool-name", ...]` | Tool names the plugin registers (see §Tools). |
| `hooks` | `{ "event": "module.function" }` | Lifecycle hooks mapped to function paths relative to the plugin directory (see §Hooks). |

### `backends`

```json
{
  "provides": {
    "backends": { "bitnet": "bitnet.BitNetBackend" }
  }
}
```

Maps backend names to class paths (relative to the plugin directory). The
class must implement the `BaseBackend` interface.

### `cli_commands`

```json
{ "provides": { "cli_commands": ["turbo"] } }
```

Lists CLI subcommand names that the plugin registers. These appear in
`agentkthx --help`.

### `cli_flags`

```json
{
  "provides": {
    "cli_flags": { "--backend": ["zai", "bitnet"] }
  }
}
```

Extends the valid values for CLI flags.

### `tools`

```json
{ "provides": { "tools": ["crypto-quotes", "portfolio-value"] } }
```

Names of tools the plugin registers. Declared names are used for ownership
tracking and unload; the client SHOULD warn when a declared name is never
registered or a tool is registered without being declared.

### `hooks`

```json
{
  "provides": {
    "hooks": { "on_run_start": "hooks.on_run_start" }
  }
}
```

Maps event names to function paths (relative to the plugin directory).
Declarative registration is preferred because it enables discovery and
ownership tracking without importing plugin code.

## Hooks

Hooks let plugins observe framework lifecycle events without modifying core
code. Hooks are observers: the client MUST ignore hook return values. Hooks
MUST NOT throw across the boundary — see error isolation below.

### Events (v0.2 set)

| Event | Emitted when | Context keys (MAY be extended; treat as read-only unless documented) |
|-------|--------------|----------------------------------------------------------------------|
| `on_init` | Framework init complete; all plugins loaded. | `{"plugin_manager": pm}` |
| `on_run_start` | Before each agent run. | `{"prompt": str, "session": str \| None, "backend": str, "model": str}` |
| `on_run_end` | After a successful agent run. | `{"prompt": str, "session": str \| None, "usage": dict, "duration_ms": int}` |
| `on_error` | When an agent run raises. | `{"prompt": str, "error": str, "exception": Exception}` |
| `on_shutdown` | Before interpreter/CLI exit. | `{}` |

### Registration

Two equivalent forms:

1. **Declarative (RECOMMENDED)** — `provides.hooks` maps event names to
   function paths inside the plugin:

   ```json
   { "provides": { "hooks": { "on_run_start": "hooks.on_run_start" } } }
   ```

   The client resolves `module.function` lazily at first emit, relative to the
   plugin package. An unresolvable path warns once and is skipped at emit
   time.

2. **Imperative** — inside `register()`:

   ```python
   def register(manager) -> None:
       manager.register_hook("on_run_start", my_hook, plugin="my-plugin")
   ```

   The `plugin` keyword is OPTIONAL in v0.2 but RECOMMENDED: ownership recorded
   under a plugin name is what `unload()` uses to remove imperative
   registrations. Declarative hooks are owned by their declaring plugin
   automatically.

### Handler signature and ordering

```python
def my_hook(context: dict) -> None:
    ...
```

- Handlers MUST accept a single `dict` argument (the event context).
- Handlers run in registration order, which equals dependency-resolved load
  order (see §Dependency resolution).
- **Error isolation:** if a handler raises, the client MUST warn, skip the
  remaining work of that handler, and run all remaining handlers. A hook
  failure MUST NOT abort the underlying framework operation.
- Registering the same handler twice for the same event MAY warn and MUST NOT
  double-invoke.

### PluginManager API

| Method | Description |
|--------|-------------|
| `register_hook(event, fn, *, plugin=None)` | Register a handler for an event. Unknown event names warn. |
| `unregister_hook(event, fn)` | Remove a specific handler. |
| `list_hooks(event=None) -> list` | List handlers (optionally for one event), each with owning plugin. |
| `emit(event, context)` | Run all handlers for an event with error isolation. Called by the framework, not by plugins. |

## Tools

Tools registered by plugins become available to agents through the existing
core `ToolRegistry` (`agentkthx/tools/registry.py`) — the same registry used
by built-in tools. A tool is a `agentkthx.core.models.Tool` object.

### PluginManager API

| Method | Description |
|--------|-------------|
| `register_tool(tool: Tool, *, plugin=None)` | Register a tool. MUST route into the core `ToolRegistry`. Rejects (warn) if the tool name collides with a core or other-plugin tool that is currently loaded. |
| `unregister_tool(name: str)` | Remove a tool registration. |
| `list_tool_names() -> list[str]` | All plugin-registered tool names. |
| `get_tool(name: str) -> Tool \| None` | Look up a tool. |

### Example

```python
# agentkthx/plugins/my-tools/__init__.py
def register(manager) -> None:
    from .tools import build_quote_tool   # lazy import
    manager.register_tool(build_quote_tool(), plugin="my-tools")

def unregister(manager) -> None:
    manager.unregister_tool("crypto-quotes")
```

```json
{
  "extensions": {
    "org.vts-tech.agentkthx": {
      "type": "tools",
      "provides": { "tools": ["crypto-quotes"] }
    }
  }
}
```

Tool unload MUST remove the tool from the core registry. On framework
shutdown, tools need no special cleanup beyond `unregister()`.

## Config

```json
{
  "config": {
    "env_prefix": "ZAI",
    "defaults": {
      "ZAI_BASE_URL": "https://api.z.ai",
      "ZAI_API_KEY": ""
    }
  }
}
```

- `env_prefix`: Namespace for environment variables.
- `defaults`: Key-value pairs of default configuration values. These are
  accessible via `manager.get_all_config_defaults()` and merged by
  `config.py` at init time. Real environment variables of the same name MUST
  override defaults.

### Secrets guard (new in v0.2)

Plugin manifests are visible package data, not a secret mechanism. If a
default key matches `*(KEY|PASS|TOKEN|SECRET|PASSWORD)` (case-insensitive
suffix match) **and** its default value is a non-empty string, the client
MUST warn. `ZAI_API_KEY: ""` (empty default) is fine;
`ACP_PASS: "secret"` produces a warning. Plugins SHOULD read secrets from
the real environment at runtime instead of shipping defaults.

## Environment variables and placeholder expansion

### Subprocess environment

When a client launches a subprocess on behalf of a plugin (now or in future
features), it MUST provide:

- `PLUGIN_ROOT` — absolute path to the filesystem-resolved plugin root.
- `PLUGIN_DATA` — absolute path to a client-managed persistent data directory
  dedicated to that plugin instance.

`PLUGIN_DATA` location (v0.2 default):

| Platform | Path |
|----------|------|
| POSIX | `${XDG_STATE_HOME:-~/.local/state}/agentkthx/plugins/<name>` |
| Windows | `%LOCALAPPDATA%\agentkthx\plugins\<name>` |

The client MUST create `PLUGIN_DATA` before calling `register()`, MUST make
it writable, and MUST preserve its contents across framework/plugin updates.
The client MAY delete it when the plugin is uninstalled. Use `PLUGIN_DATA`
for caches, generated files, installed dependencies, and other state that
should survive updates; use `PLUGIN_ROOT` for files bundled with the plugin.

The `Plugin` object MUST expose both paths as attributes
(`plugin.root`, `plugin.data_dir`), and the manager MUST provide
`get_plugin_data_dir(name)`.

### Placeholder expansion

Configuration default values (`config.defaults`) MAY reference:

- `${PLUGIN_ROOT}` — the plugin's own root directory.
- `${PLUGIN_DATA}` — the plugin's persistent data directory.

Expansion rules (mirroring the Agent Plugins Specification):

- Expansion is a single, non-recursive textual replacement of every exact
  occurrence of either placeholder. Text introduced by a replacement MUST NOT
  be scanned for further placeholders.
- Unrecognized placeholder-like text MUST remain literal. No other
  environment-variable expansion is performed.
- Expanded values MUST remain within the plugin root (for `${PLUGIN_ROOT}`)
  or the plugin data directory (for `${PLUGIN_DATA}`); escaping values warn.

```json
{
  "config": {
    "env_prefix": "MY_PLUGIN",
    "defaults": {
      "MY_PLUGIN_CACHE": "${PLUGIN_DATA}/cache",
      "MY_PLUGIN_MODEL_PATH": "${PLUGIN_ROOT}/models/model.bin"
    }
  }
}
```

## Compatibility

```json
{ "compatibility": { "agentkthx": ">=0.5.0" } }
```

Declares the framework versions the plugin supports. v0.2 makes this
**enforced with warn-only semantics**:

- The client MUST compare the constraint against the running framework
  version (`agentkthx.__version__`) at load time.
- On mismatch, the client MUST emit a warning naming the plugin, the
  constraint, and the running version — and MUST load the plugin anyway.
- The constraint key MUST be `agentkthx`. The legacy key `agentnova` (present
  in all v0.1-era manifests) MUST be accepted as an alias for `agentkthx`
  with a deprecation warning.
- Unknown keys in `compatibility` MUST be ignored with a warning.

Constraint grammar (per key): a comma-separated list of comparisons, each of
the form `<operator><semver>` with operators `>=`, `<=`, `>`, `<`, `==`, `!=`.
Multiple comparisons are ANDed:

```json
{ "compatibility": { "agentkthx": ">=0.5.0,<0.7.0" } }
```

Malformed constraints warn and are treated as satisfied (fail-open), because
enforcement is advisory in v0.2.

## Dependency resolution

Plugins declare hard dependencies via the `depends` field:

```json
{ "name": "my-advanced-plugin", "depends": ["base-plugin"] }
```

The PluginManager resolves dependencies using **topological sort** (Kahn's
algorithm):

1. Build a dependency graph from all discovered manifests.
2. Load plugins with zero dependencies first.
3. Load dependent plugins only after their dependencies are satisfied.
4. **Missing hard dependencies** → plugin is skipped with a warning.
5. **Circular dependencies** → all members of the cycle are rejected with a
   warning.
6. **Dependency version** is not part of `depends` in v0.2 (names only);
   use `compatibility` for version constraints. (Future: `">=0.2"`-style
   per-dependency constraints.)

`optional_depends` never affects load order in v0.2; they exist to document
soft relationships (future: conditional features).

## Lifecycle

```
                    ┌──────────────┐
                    │   discover   │  Scan plugin roots, parse manifests
                    │              │  (no code imported; data dir NOT created)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  load(name)  │  Check compatibility (warn-only),
                    │              │  check deps, create PLUGIN_DATA,
                    │              │  import entrypoint module
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  register()  │  Plugin registers backends, commands,
                    │              │  config, tools, hooks
                    └──────┬───────┘
                           │
              ┌────────────▼────────────┐
              │         active          │  Emit on_init once, after all loads
              │  ... on_run_start       │
              │  ... on_run_end/on_error│
              └────────────┬────────────┘
                           │
                    ┌──────▼───────┐
                    │ unregister() │  Plugin removes registrations;
                    │              │  manager purges manifest-declared
                    │              │  provides + config defaults
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   unloaded   │  PLUGIN_DATA preserved
                    └──────────────┘
```

`on_shutdown` is emitted before CLI exit for all loaded plugins (ordering:
reverse load order).

## Failure boundaries

A failure isolated to one plugin, registration, or hook MUST NOT prevent the
client from loading or running anything else. The client SHOULD report every
failure it skips around.

| Failure | Boundary |
|---------|----------|
| Manifest missing / not JSON / invalid required field | Skip that plugin; warn; continue others. |
| Name constraint violation or name ≠ directory | Reject that plugin; warn. |
| Unrecognized `$schema` | Warn; attempt v0.2 parsing anyway. |
| Unknown `type` value | Warn; load without type-specific behavior. |
| Unknown manifest fields (top-level or namespace) | Warn; ignore; continue load. |
| Compatibility mismatch | Warn; load anyway (advisory in v0.2). |
| Missing hard dependency | Skip dependent plugin; warn. |
| Circular dependencies | Reject all cycle members; warn. |
| `entrypoint` module missing or lacks `register` | Fail that plugin's load; warn. |
| `register()` raises | Best-effort `unregister()`, purge declared provides, mark failed; warn. |
| Backend class not a `BaseBackend` subclass | Reject that registration only; plugin stays loaded; warn. |
| Declared hook path unresolvable | Warn once; skip at emit time. |
| Hook handler raises | Warn; skip that handler; run remaining handlers; framework operation continues. |
| Tool name collision | Reject the new registration; warn; existing tool untouched. |
| Config default looks like a secret | Warn; keep the value (advisory). |

## CLI integration

### Startup behavior

The CLI MUST load all plugins (`load_all()`) before constructing the argument
parser, so that plugin backends appear in `--backend` choices, plugin
subcommands appear in `--help`, and hooks are active for the first run.
This matches v0.1 behavior and is the documented v0.2 contract. Lazy
per-backend loading via `backends/__init__._ensure_plugin()` remains the
mechanism for backend resolution, unchanged.

### `agentkthx plugins`

| Flag | Description |
|------|-------------|
| *(none)* | Table of discovered plugins: status (● loaded / ○ failed / ○ discovered), name, type, version, description. |
| `--verbose` | Additionally show depends, provides (backends, cli_commands, tools, hooks), entrypoint, plugin root, and any deprecation warnings. |
| `--load <name>` | Load a single plugin by name and report the result. |
| `--unload <name>` | Unload a running plugin and confirm what was removed. |
| `--reload <name>` | Unload then load. Intended for plugin development against `$AGENTKTHX_PLUGIN_PATH`. |
| `--json` | Machine-readable listing (all manifest fields, load state, failure messages). RECOMMENDED for tooling. |

Exit codes: `0` on success (including a clean empty listing); `1` when a
requested operation failed (unknown plugin, load failure, unload of a
non-loaded plugin).

## Packaging and distribution

### Built-in plugins

Shipped inside the AgentKthx wheel via `pyproject.toml`:

```toml
[tool.setuptools.package-data]
agentkthx = [
    "py.typed",
    "skills/*/SKILL.md",
    "souls/*/soul.json",
    "souls/*/*.md",
    "plugins/*/plugin.json",
    "schemas/v0.2/plugin.schema.json"
]
```

Both the Python modules and `plugin.json` files ship. The Python modules are
included automatically because `agentkthx.*` is in the package find pattern.

### External plugins

- An external plugin is a plain directory — no pip install, no archive
  format required. Distribution is any mechanism that places the directory
  under a plugin root directory: a zip/tarball unpacked into
  `~/.agentkthx/plugins/`, or a git clone referenced via
  `$AGENTKTHX_PLUGIN_PATH`.
- Third-party Python dependencies are the plugin author's problem in v0.2:
  plugins SHOULD lazy-import optional dependencies and document installation
  steps. (Future consideration: a declarative `requirements` field with
  client-managed installation into `PLUGIN_DATA`.)
- External plugin manifests are NOT packaged in the wheel; nothing to change
  in `pyproject.toml`.

## Migration from v0.1

### Field mapping

| v0.1 location (top-level) | v0.2 canonical location |
|---------------------------|--------------------------|
| `name` | unchanged (top-level) |
| `version` | unchanged (top-level; now optional) |
| `description` | unchanged (top-level) |
| `author` | unchanged (top-level; object form now accepted) |
| `license` | unchanged (top-level) |
| `display_name` | `extensions["org.vts-tech.agentkthx"].display_name` |
| `type` | `extensions["org.vts-tech.agentkthx"].type` |
| `entrypoint` | `extensions["org.vts-tech.agentkthx"].entrypoint` |
| `depends` | `extensions["org.vts-tech.agentkthx"].depends` |
| `optional_depends` | `extensions["org.vts-tech.agentkthx"].optional_depends` |
| `config` | `extensions["org.vts-tech.agentkthx"].config` |
| `provides` | `extensions["org.vts-tech.agentkthx"].provides` |
| `compatibility` | `extensions["org.vts-tech.agentkthx"].compatibility` |
| *(new)* | `$schema`, `extensions` are new top-level fields |

### Timeline

| Release | Legacy top-level extension fields | `$schema` | Notes |
|---------|-----------------------------------|-----------|-------|
| v0.2 | Accepted, deprecation warning | Optional (encouraged) | Dual-form. All six bundled plugins migrate in the same release that ships v0.2 code. |
| v0.3 (planned) | Rejected with a clear error | Required | Schema closed (`additionalProperties: false` behavior), aligning with Agent Plugins strictness. |

### Behavioral changes that affect existing plugins

1. **Name/directory mismatch** now rejected (was silent breakage).
2. **Missing `register()`** now fails the load (was silently "loaded").
3. **`entrypoint`** is honored — plugins relying on package-`__init__` import
   while declaring a different entrypoint will change behavior (none of the
   six bundled plugins do; all declare `"__init__"`).
4. **`compatibility` key** `agentnova` warns and maps to `agentkthx`.
5. **Secret-shaped config defaults** with non-empty values now warn.
6. **`unload()`** now also purges config defaults, tools, and hooks (v0.1
   leaked config entries).

## Appendix A: Conformance checklist

*Non-normative convenience summary — the normative text governs.*

### Plugin loader

- [ ] Scan plugin roots in priority order: package, `~/.agentkthx/plugins/`, `$AGENTKTHX_PLUGIN_PATH` (§Plugin roots)
- [ ] Skip `_`/`.`-prefixed dirs and dirs without `plugin.json` (§Discovery)
- [ ] Enforce name constraints + name/directory match (§Name constraints, §Discovery)
- [ ] Resolve name collisions by root priority (§Discovery)
- [ ] Parse `$schema` (absent → legacy form; recognized → v0.2 form; other → warn+continue) (§$schema)
- [ ] Read extension fields from `extensions["org.vts-tech.agentkthx"]` first, fall back to top-level with deprecation warning (§Extensions)
- [ ] Warn + ignore unknown fields; ignore unimplemented extension namespaces (§Manifest, §Extensions)
- [ ] Accept `author` as string or object (§Manifest)
- [ ] Validate SPDX license (warn-only) (§Manifest)
- [ ] Check `compatibility` (warn-only), map legacy `agentnova` key (§Compatibility)
- [ ] Import honored `entrypoint` module; require `register`; warn-miss `unregister` (§Entrypoint)
- [ ] Create `PLUGIN_DATA` before `register()`; expose `plugin.root`/`plugin.data_dir` (§Environment)
- [ ] Expand `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` in config defaults, non-recursively (§Expansion)
- [ ] Topological dependency resolution; skip missing-dep plugins; reject cycles (§Dependency resolution)
- [ ] Purge **all** registration categories + config on unload (§Lifecycle, §Migration)
- [ ] Apply the §Failure boundaries table everywhere

### Registrations

- [ ] Backends: verify `BaseBackend` subclass; reject individual bad registrations (§Plugin types)
- [ ] CLI: merge `cli_commands` / `cli_flags`; ignore unsupported flags with warning (§Provides)
- [ ] Tools: route into core `ToolRegistry`; track ownership; collision = reject new (§Tools)
- [ ] Hooks: declarative + imperative registration; emit `on_init/on_run_start/on_run_end/on_error/on_shutdown` with error isolation (§Hooks)

### CLI

- [ ] `agentkthx plugins` listing with load state (§CLI)
- [ ] `--verbose`, `--load`, `--unload`, `--reload`, `--json` (§CLI)
- [ ] Load all plugins before parser construction (§CLI startup)

## Appendix B: Implementation checklist

*Non-normative working plan mapping spec sections to code changes. Use this
to resume work across sessions.*

**`agentkthx/plugins/_loader.py`**

1. `_parse_manifest()`: `$schema` handling; strict name validation (regex +
   length + no doubles); name/directory match check; `author` object form;
   SPDX check via `skills/loader.validate_spdx_license`; unknown-field
   warnings; extensions-first field resolution with deprecation warnings
   (§Manifest, §Extensions).
2. `PluginManifest` dataclass: add `schema`, `root` (path), `data_dir`,
   keep legacy fields; record `legacy_fields_used: list[str]`.
3. `PluginManager.__init__()`: accept `plugins_dir` **list** (roots in
   priority order); build defaults from package dir + `~/.agentkthx/plugins/`
   + `$AGENTKTHX_PLUGIN_PATH` (§Plugin roots).
4. `discover()`: multi-root scan, collision resolution, cache result
   (`self._manifests`); `find_plugin_for_backend()` reads the cache (§Discovery).
5. `_resolve_load_order()`: unchanged Kahn's algorithm; add cycle-member
   naming in warning (§Dependency resolution).
6. `load()` / `_load_plugin()`: compatibility warn-only check + `agentnova`
   alias; honored entrypoint import (package vs submodule; importlib by path
   for external roots); `PLUGIN_DATA` creation before `register()`;
   `plugin.root`/`plugin.data_dir` attributes; missing-`register` = load
   failure; on `register()` exception → best-effort unregister + purge +
   failed state (§Entrypoint, §Environment, §Failure boundaries).
7. `unload()`: purge config defaults/env vars, tools, hooks (new), plus
   existing backends/CLI purge (§Lifecycle).
8. New registries: `_tools`, `_hooks` (+ ownership maps), `register_tool`,
   `unregister_tool`, `list_tool_names`, `get_tool`, `register_hook`,
   `unregister_hook`, `list_hooks`, `emit` with error isolation (§Hooks, §Tools).
9. `register_backend()`: subclass verification against `BaseBackend`
   (lazy import to avoid cycles) (§Plugin types).
10. Config: secret-shaped default warning; `${PLUGIN_ROOT}`/`${PLUGIN_DATA}`
    single-pass expansion at merge time (§Config, §Expansion).

**`agentkthx/agent.py`**

11. Emit `on_run_start` / `on_run_end` / `on_error` via `pm.emit(...)`
    with the §Hooks context keys; emit `on_shutdown` in CLI exit path
    (reverse load order) (§Hooks, §Lifecycle).

**`agentkthx/cli.py`**

12. `cmd_plugins()`: `--verbose`, `--load`, `--unload`, `--reload`,
    `--json`; show failed state and deprecation warnings (§CLI).
13. Emit `on_shutdown` before exit after runs (§Hooks).

**`agentkthx/backends/__init__.py`**

14. `_ensure_plugin()`: use manifest cache instead of re-discovering per
    lookup (§Discovery).

**Manifests (all six bundled plugins)**

15. Add `$schema`; move fields under `extensions["org.vts-tech.agentkthx"]`;
    `compatibility` key `agentnova` → `agentkthx`; remove non-empty
    secret defaults (`ACP_PASS`) (§Migration).

**Tests**

16. `tests/test_plugin_spec.py`: name constraints (valid/invalid table);
    name/directory mismatch rejection; dual-form parsing + precedence +
    deprecation warnings; unknown `$schema` recovery; compatibility warn-only
    + `agentnova` alias; entrypoint honored (submodule case); missing
    `register` rejection; register-raises cleanup; unload purges everything;
    multi-root discovery + collision priority; `AGENTKTHX_PLUGIN_PATH`
    parsing; placeholder expansion; hooks emit + error isolation; tool
    registration/unload ownership; config secret warning (§ all).

**Packaging**

17. `pyproject.toml` package-data: add `schemas/v0.2/plugin.schema.json`
    (§Packaging).

## Changelog

- **v0.2 (draft)** — `$schema` version targeting; `extensions`
  reverse-domain namespace with dual-form migration; `tools` and `hook`
  plugin types normative (tool registry bridge, five lifecycle events with
  error isolation); external plugin roots (`~/.agentkthx/plugins/`,
  `AGENTKTHX_PLUGIN_PATH`) with collision resolution; `PLUGIN_ROOT` /
  `PLUGIN_DATA` environment and persistent data dirs; `entrypoint` honored;
  `compatibility` warn-only enforcement (`agentnova` legacy alias); strict
  plugin name constraints + directory match; `author` object form; SPDX
  validation; failure-boundary table; complete `unload()`; CLI
  `plugins --load/--unload/--reload/--json/--verbose`; published JSON Schema;
  spec/code reconciliation (`version` optional; `type` defaulted).
- **v0.1** — Initial spec. Directory scan discovery, backend and feature
  plugin types, dependency resolution, CLI extension, config defaults.
