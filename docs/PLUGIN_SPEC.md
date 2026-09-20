# AgentKthx Plugin Specification v0.1

## Overview

AgentKthx plugins extend the framework without modifying core code. Plugins can provide inference backends, CLI commands, configuration defaults, and tools. Discovery is done via directory scanning — no pip installation required (for now).

## Directory Layout

```
agentkthx/plugins/
├── __init__.py          # Plugin types, exports
├── _loader.py           # PluginManager singleton
├── bitnet/              # Example: backend plugin
│   ├── plugin.json      # Manifest (required)
│   ├── __init__.py      # register() / unregister() entrypoints
│   └── bitnet.py        # Plugin implementation
├── turboquant/          # Example: feature plugin
│   ├── plugin.json
│   ├── __init__.py
│   └── turbo.py
└── ...
```

**Rules:**
- Each plugin lives in its own subdirectory under `agentkthx/plugins/`
- Directories starting with `_` or `.` are skipped during discovery
- Every plugin directory must contain a `plugin.json` manifest
- The `plugin.json` file must be listed in `pyproject.toml` package-data

## Manifest Format (`plugin.json`)

```json
{
  "name": "my-plugin",
  "version": "0.1.0",
  "display_name": "My Plugin",
  "description": "What this plugin does",
  "author": "Author Name",
  "license": "MIT",
  "type": "backend",
  "entrypoint": "__init__",
  "depends": [],
  "optional_depends": [],
  "config": {
    "env_prefix": "MY_PLUGIN",
    "defaults": {
      "MY_PLUGIN_URL": "http://localhost:9000"
    }
  },
  "provides": {
    "backends": {
      "my-backend": "module.BackendClass"
    },
    "cli_commands": ["my-cmd"],
    "cli_flags": {
      "--backend": ["my-backend"]
    }
  },
  "compatibility": {
    "agentkthx": ">=0.5.0"
  }
}
```

### Field Reference

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | `string` | **Yes** | — | Unique plugin identifier (lowercase, hyphens ok). Must match the directory name. |
| `version` | `string` | **Yes** | `"0.0.0"` | Semver version string. |
| `display_name` | `string` | No | `name` | Human-readable name shown in `agentkthx plugins`. |
| `description` | `string` | **Yes** | `""` | Short description of what the plugin does. |
| `author` | `string` | No | `""` | Plugin author. |
| `license` | `string` | No | `""` | SPDX license identifier. |
| `type` | `string` | **Yes** | `"feature"` | Plugin type. One of: `backend`, `feature`, `tools`, `hook`. |
| `entrypoint` | `string` | No | `name` | Python module name inside the plugin directory. Use `"__init__"` to import the package itself. |
| `depends` | `string[]` | No | `[]` | Hard dependencies — other plugin names that must be loaded first. Missing dependencies cause this plugin to be skipped. |
| `optional_depends` | `string[]` | No | `[]` | Soft dependencies — plugin will load even if these are missing (future: conditional features). |
| `config` | `object` | No | `{}` | Configuration defaults. See [Config](#config) section. |
| `provides` | `object` | No | `{}` | What the plugin registers. See [Provides](#provides) section. |
| `compatibility` | `object` | No | `{}` | Version constraints. See [Compatibility](#compatibility) section. |

## Plugin Types

### `backend`

Registers an inference backend class that can be selected via `--backend <name>`.

```json
{
  "provides": {
    "backends": {
      "bitnet": "bitnet.BitNetBackend"
    },
    "cli_flags": {
      "--backend": ["bitnet"]
    }
  }
}
```

The backend class must be a subclass of `BaseBackend` (defined in `agentkthx/backends/base.py`).

### `feature`

Extends framework functionality — CLI commands, configuration, integrations.

```json
{
  "provides": {
    "cli_commands": ["turbo"]
  }
}
```

Feature plugins do not provide backends but can add subcommands, config defaults, or modify framework behavior.

### `tools` (future)

Will register custom tools that agents can invoke.

### `hook` (future)

Will register lifecycle hooks (on_init, on_run_start, on_run_end, etc.).

## Entrypoint Contract

Every plugin must expose two functions in its entrypoint module (`__init__.py`):

```python
def register(manager) -> None:
    """
    Called when the plugin is loaded.

    Use this to register backends, CLI commands, config defaults, etc.
    The `manager` argument is the PluginManager singleton.

    Lazy imports are recommended — import heavy modules inside
    register() rather than at module level.
    """
    from .my_module import MyBackend
    manager.register_backend("my-backend", MyBackend)


def unregister(manager) -> None:
    """
    Called when the plugin is unloaded.

    Remove all registrations made in register().
    """
    manager.unregister_backend("my-backend")
```

**Important:** Use lazy imports inside `register()` to avoid loading plugin code at discovery time. The PluginManager only imports the entrypoint module when `load()` is called.

## Provides

### `backends`

```json
{
  "provides": {
    "backends": {
      "bitnet": "bitnet.BitNetBackend"
    }
  }
}
```

Maps backend names to class paths (relative to the plugin directory). The class must implement the `BaseBackend` interface.

### `cli_commands`

```json
{
  "provides": {
    "cli_commands": ["turbo"]
  }
}
```

Lists CLI subcommand names that the plugin registers. These appear in `agentkthx --help`.

### `cli_flags`

```json
{
  "provides": {
    "cli_flags": {
      "--backend": ["zai", "bitnet"]
    }
  }
}
```

Extends the valid values for CLI flags. Currently supports `"--backend"` to add backend choices to the `--backend` argument.

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
- `defaults`: Key-value pairs of default configuration values. These are accessible via `manager.get_all_config_defaults()` and merged by `config.py` at init time.

## Compatibility

```json
{
  "compatibility": {
    "agentkthx": ">=0.5.0"
  }
}
```

Declares the minimum AgentKthx version required (future: enforced at load time).

## PluginManager API

The `PluginManager` is a singleton accessed via `get_plugin_manager()`.

```python
from agentkthx.plugins import get_plugin_manager

pm = get_plugin_manager()
```

### Discovery & Loading

| Method | Description |
|--------|-------------|
| `discover() -> list[PluginManifest]` | Scan plugins directory, parse all `plugin.json` manifests. Does not load any code. |
| `load(name: str) -> Plugin \| None` | Load a single plugin by name. Respects dependency ordering. |
| `load_all() -> list[Plugin]` | Discover and load all plugins in dependency order. |
| `unload(name: str) -> bool` | Unload a plugin, call `unregister()`, remove all registrations. |

### Backend Registration

| Method | Description |
|--------|-------------|
| `register_backend(name, cls, *, alias_of=None)` | Register a backend class. Use `alias_of` for aliases. |
| `unregister_backend(name)` | Remove a backend registration. |
| `get_backend_class(name) -> type \| None` | Get a backend class by name (resolves aliases). |
| `list_backend_names() -> list[str]` | List all plugin-registered backend names. |
| `get_backend_choices() -> list[str]` | Merged list of core + plugin backend names for `--backend`. |

### CLI Extension

| Method | Description |
|--------|-------------|
| `register_cli_command(name, handler, setup_parser=None)` | Register a CLI subcommand. |
| `unregister_cli_command(name)` | Remove a CLI subcommand. |
| `get_cli_commands() -> dict` | Get all registered CLI commands. |

### Config Extension

| Method | Description |
|--------|-------------|
| `register_config_defaults(env_prefix, defaults)` | Register plugin config defaults. |
| `get_all_config_defaults() -> dict` | Flat `{ENV_VAR: default}` dict of all plugin configs. |
| `get_config_defaults_by_prefix(env_prefix) -> dict` | Config defaults for a specific plugin. |

### Query

| Method | Description |
|--------|-------------|
| `is_loaded(name) -> bool` | Check if a plugin is currently loaded. |
| `list_plugins() -> list[str]` | List all discovered plugin names. |
| `get_plugin(name) -> Plugin \| None` | Get a Plugin instance. |
| `get_plugin_info(name) -> PluginManifest \| None` | Get a plugin's manifest. |

## Dependency Resolution

Plugins declare hard dependencies via the `depends` field:

```json
{
  "name": "my-advanced-plugin",
  "depends": ["base-plugin"]
}
```

The PluginManager resolves dependencies using **topological sort** (Kahn's algorithm):

1. Build a dependency graph from all discovered manifests.
2. Load plugins with zero dependencies first.
3. Load dependent plugins only after their dependencies are satisfied.
4. **Missing hard dependencies** → plugin is skipped with a warning.
5. **Circular dependencies** → detected and rejected with a warning.

## Lifecycle

```
                    ┌──────────────┐
                    │   discover   │  Scan directory, parse manifests
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  load(name)  │  Import entrypoint module
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  register()  │  Plugin registers backends, commands, etc.
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    active    │  Plugin is loaded and functional
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ unregister() │  Plugin removes all registrations
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    unloaded  │
                    └──────────────┘
```

## Packaging

Plugins are included in the AgentKthx wheel via `pyproject.toml`:

```toml
[tool.setuptools.package-data]
agentkthx = [
    "py.typed",
    "skills/*/SKILL.md",
    "souls/*/soul.json",
    "souls/*/*.md",
    "plugins/*/plugin.json"    # <-- includes all plugin manifests
]
```

Both the Python modules and `plugin.json` files are shipped. The Python modules are automatically included because `agentkthx.*` is in the package find pattern.

## Writing a Plugin — Step by Step

### Minimal Backend Plugin

**1. Create the directory:**

```
agentkthx/plugins/my-backend/
```

**2. Create `plugin.json`:**

```json
{
  "name": "my-backend",
  "version": "0.1.0",
  "description": "My custom inference backend",
  "type": "backend",
  "entrypoint": "__init__",
  "depends": [],
  "provides": {
    "backends": {
      "my-backend": "backend.MyBackend"
    },
    "cli_flags": {
      "--backend": ["my-backend"]
    }
  }
}
```

**3. Create `__init__.py`:**

```python
def register(manager) -> None:
    from .backend import MyBackend
    manager.register_backend("my-backend", MyBackend)


def unregister(manager) -> None:
    manager.unregister_backend("my-backend")
```

**4. Create `backend.py`:**

```python
from agentkthx.backends.base import BaseBackend, BackendConfig


class MyBackend(BaseBackend):
    def __init__(self, config=None):
        super().__init__(config or BackendConfig())

    def generate(self, model, messages, tools=None, temperature=0.7,
                 max_tokens=4096, top_p=1.0, **kwargs):
        # Your inference logic here
        return {
            "content": "Hello from my backend!",
            "tool_calls": [],
            "usage": {"total_tokens": 42},
            "finish_reason": "stop",
        }
```

**5. Verify:**

```bash
agentkthx plugins          # Should list my-backend
agentkthx --backend my-backend run "Hello"
```

### Minimal Feature Plugin (CLI Command)

**`plugin.json`:**

```json
{
  "name": "hello",
  "version": "0.1.0",
  "description": "Says hello",
  "type": "feature",
  "entrypoint": "__init__",
  "provides": {
    "cli_commands": ["hello"]
  }
}
```

**`__init__.py`:**

```python
def register(manager) -> None:
    def hello_handler(args):
        print("Hello from the hello plugin!")

    manager.register_cli_command("hello", hello_handler)


def unregister(manager) -> None:
    manager.unregister_cli_command("hello")
```

## Native vs Plugin

| Component | Location | Loaded By |
|-----------|----------|-----------|
| Ollama backend | `agentkthx/backends/ollama.py` | Core — always available |
| llama-server backend | `agentkthx/backends/llama_server.py` | Core — always available |
| BitNet backend | `agentkthx/plugins/bitnet/` | Plugin — loaded on demand |
| ZAI backend | `agentkthx/plugins/zai/` | Plugin — loaded on demand |
| TurboQuant | `agentkthx/plugins/turboquant/` | Plugin — loaded on demand |
| ACP integration | `agentkthx/plugins/acp/` | Plugin — loaded on demand |

Core backends cannot be removed or overridden by plugins. Plugin backends are additive.

## Changelog

- **v0.1** — Initial spec. Directory scan discovery, backend and feature plugin types, dependency resolution, CLI extension, config defaults.
