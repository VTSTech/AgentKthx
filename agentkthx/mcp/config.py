"""
⚛️ AgentKthx — MCP config loading

Reads ``~/.agentkthx/mcp.json`` (or an explicit path) and returns a list
of :class:`MCPServerConfig` objects.

Config format (v0.1)
--------------------

The file is a JSON object with a ``servers`` array. Each entry declares
one MCP server:

.. code-block:: json

    {
      "version": "0.1",
      "servers": [
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem",
                   "/home/user/projects"],
          "env": {"NODE_NO_WARNINGS": "1"},
          "enabled": true,
          "timeout_seconds": 30
        },
        {
          "name": "git",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-git",
                   "/home/user/projects"],
          "enabled": true
        },
        {
          "name": "audit",
          "command": "python3",
          "args": ["-m", "agentkthx.skills.codebase_audit.mcp_server",
                   "--repo", "/home/user/repo"],
          "enabled": false,
          "comment": "Phase 2 — not yet implemented"
        }
      ]
    }

Fields
------
* ``name`` (str, required) — short identifier; used as a prefix for
  bridged tool names (``filesystem__read_file``).
* ``command`` (str, required) — executable to launch.
* ``args`` (list[str], optional) — argv passed to the command.
* ``env`` (dict[str, str], optional) — additional environment variables
  for the subprocess. Inherited env is NOT replaced.
* ``enabled`` (bool, default ``true``) — set to ``false`` to keep a
  server declared but inactive.
* ``timeout_seconds`` (int, default ``30``) — per-request timeout for
  JSON-RPC calls. Does NOT limit the lifetime of the subprocess itself.
* ``comment`` (str, optional) — ignored by code; for human readers.

Security model
--------------
* Config file must be owned by the current user and not group/world
  writable (mode ``0o600`` recommended). A warning is emitted otherwise.
* ``command`` must be an absolute path OR resolvable via ``shutil.which``
  on ``$PATH``. No shell expansion, no ``~`` expansion (caller should
  expand ``~`` before writing the file).
* Subprocess inherits NO additional file descriptors beyond stdin/stdout.
  stderr is captured to a ring buffer for diagnostics.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


class MCPConfigError(ValueError):
    """Raised when the MCP config file is missing, malformed, or unsafe."""


@dataclass
class MCPServerConfig:
    """Declarative configuration for a single MCP server subprocess."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: int = 30
    comment: str = ""

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise MCPConfigError("MCP server 'name' must be a non-empty string")
        # Reject names that could escape the namespacing scheme
        if not all(c.isalnum() or c in ("-", "_") for c in self.name):
            raise MCPConfigError(
                f"MCP server name '{self.name}' must be alphanumeric/-/_ "
                f"(used as a tool-name prefix)"
            )
        if not self.command or not isinstance(self.command, str):
            raise MCPConfigError(f"Server '{self.name}': 'command' must be a non-empty string")

    def resolve_command(self) -> str:
        """Return the absolute path to the executable, or raise.

        We refuse to launch commands that aren't either absolute paths or
        resolvable via ``shutil.which``. This is a guardrail against
        config files that try to invoke ``bash -c ...`` or relative
        binaries — both are common CVE shapes in subprocess-launch code.
        """
        if os.path.isabs(self.command):
            if not os.path.isfile(self.command) or not os.access(self.command, os.X_OK):
                raise MCPConfigError(
                    f"Server '{self.name}': command '{self.command}' is not " f"an executable file"
                )
            return self.command
        resolved = shutil.which(self.command)
        if resolved is None:
            raise MCPConfigError(
                f"Server '{self.name}': command '{self.command}' not found "
                f"on $PATH (use an absolute path if intentional)"
            )
        return resolved

    def to_launch_args(self) -> tuple[str, list[str], dict[str, str]]:
        """Return (argv0, argv_rest, env_overrides) ready for subprocess."""
        return self.resolve_command(), list(self.args), dict(self.env)


def default_config_path() -> Path:
    """Return the default MCP config path: ``~/.agentkthx/mcp.json``.

    The directory may not exist yet — caller should handle FileNotFoundError.
    """
    return Path.home() / ".agentkthx" / "mcp.json"


def load_mcp_config(path: str | os.PathLike | None = None) -> list[MCPServerConfig]:
    """Load and validate an MCP config file.

    Args:
        path: Path to the config file. If ``None``, uses
            :func:`default_config_path`. If that file does not exist,
            returns an empty list (MCP is opt-in; absence is not an error).

    Returns:
        List of enabled :class:`MCPServerConfig` objects. Disabled entries
        are filtered out.

    Raises:
        MCPConfigError: If the file exists but is malformed, or contains
            an unsafe configuration.
    """
    if path is None:
        cfg_path = default_config_path()
    else:
        cfg_path = Path(path)

    if not cfg_path.exists():
        return []

    # Permission check — warn (not fail) if the file is group/world writable.
    # Failing would break containers that run as root with default umask.
    try:
        mode = cfg_path.stat().st_mode
        if mode & 0o022:
            import sys

            print(
                f"[MCP] warning: {cfg_path} is group/world writable "
                f"(mode {oct(mode & 0o777)}) — anyone on this host can "
                f"edit your MCP server list",
                file=sys.stderr,
            )
    except OSError:
        pass  # stat failed; the open() below will give a better error

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise MCPConfigError(f"{cfg_path}: invalid JSON: {e}") from e
    except OSError as e:
        raise MCPConfigError(f"{cfg_path}: cannot read: {e}") from e

    if not isinstance(data, dict):
        raise MCPConfigError(f"{cfg_path}: top level must be an object")

    version = data.get("version", "0.1")
    if version not in ("0.1",):
        raise MCPConfigError(
            f"{cfg_path}: unsupported config version {version!r} " f"(supported: '0.1')"
        )

    servers = data.get("servers", [])
    if not isinstance(servers, list):
        raise MCPConfigError(f"{cfg_path}: 'servers' must be an array")

    configs: list[MCPServerConfig] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(servers):
        if not isinstance(entry, dict):
            raise MCPConfigError(f"{cfg_path}: servers[{i}] must be an object")
        try:
            cfg = MCPServerConfig(
                name=entry["name"],
                command=entry["command"],
                args=list(entry.get("args", [])),
                env=dict(entry.get("env", {})),
                enabled=bool(entry.get("enabled", True)),
                timeout_seconds=int(entry.get("timeout_seconds", 30)),
                comment=str(entry.get("comment", "")),
            )
        except KeyError as e:
            raise MCPConfigError(f"{cfg_path}: servers[{i}] missing required field {e}") from e
        except (TypeError, ValueError) as e:
            raise MCPConfigError(f"{cfg_path}: servers[{i}] invalid: {e}") from e

        if cfg.name in seen_names:
            raise MCPConfigError(f"{cfg_path}: duplicate server name {cfg.name!r}")
        seen_names.add(cfg.name)
        configs.append(cfg)

    # Filter out disabled entries
    return [c for c in configs if c.enabled]


def write_example_config(path: str | os.PathLike | None = None) -> Path:
    """Write a commented example config to ``path`` (or the default).

    Useful for ``agentkthx mcp init``. Does NOT overwrite an existing file.

    The generated config has the user's actual home directory substituted
    into all path arguments (no ``REPLACE_ME`` placeholder) so it works out
    of the box. The two directories the example references — ``~/projects``
    (for the filesystem server) and ``~/repo`` (for the audit server) —
    are created if they don't already exist, so a fresh ``mcp init``
    followed by ``mcp probe filesystem`` succeeds without manual setup.
    """
    target = Path(path) if path else default_config_path()
    if target.exists():
        raise MCPConfigError(f"{target} already exists; refusing to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Resolve the user's home directory and the two paths the example
    # references. Use forward slashes everywhere — Node (npx) and Python
    # both accept forward slashes on Windows, which avoids the JSON
    # backslash-escaping headache.
    home = str(Path.home()).replace("\\", "/")
    projects_dir = Path.home() / "projects"
    repo_dir = Path.home() / "repo"

    # Best-effort: create the directories the filesystem MCP server needs
    # to start cleanly. If creation fails (read-only home, permissions),
    # the config still references them — the user will get a clear error
    # from the MCP server when they probe, which is more actionable than
    # refusing to write the config.
    for d in (projects_dir, repo_dir):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # config still useful even if dir creation fails

    content = _build_example_config(home)
    target.write_text(content, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass  # chmod fails on some filesystems; not fatal
    return target


def _build_example_config(home: str) -> str:
    """Build the example config JSON with ``home`` substituted into paths.

    Kept as a separate function so tests can call it without writing a
    file. Returns a pretty-printed JSON string with 2-space indentation,
    matching the hand-written format the example has always used.
    """
    # Use json.dumps so escaping is handled correctly (paths with spaces,
    # unicode home dir names, etc.). preserve_indentation via indent=2.
    import json as _json

    projects = f"{home}/projects"
    repo = f"{home}/repo"

    config = {
        "version": "0.1",
        "servers": [
            {
                "name": "filesystem",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    projects,
                ],
                "enabled": True,
                "timeout_seconds": 30,
            },
            {
                "name": "sequential-thinking",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                "enabled": True,
                "comment": "Chain-of-thought scratchpad — verified on npm 2026-10-04",
            },
            {
                "name": "git",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-git",
                    projects,
                ],
                "enabled": False,
                "comment": (
                    "DISABLED 2026-10-04: package removed from npm (404). "
                    "Check github.com/modelcontextprotocol/servers for "
                    "current alternatives, or use serena (LSP-based, "
                    "includes git ops)."
                ),
            },
            {
                "name": "audit",
                "command": "python3",
                "args": [
                    "-m",
                    "agentkthx.skills.codebase_audit.mcp_server",
                    "--repo",
                    repo,
                ],
                "enabled": False,
                "comment": ("Phase 2 — kthx-audit MCP server " "(see docs/mcp/ROADMAP.md)"),
            },
        ],
    }
    return _json.dumps(config, indent=2, ensure_ascii=False) + "\n"


# NOTE: _EXAMPLE_CONFIG was a static string template with /home/REPLACE_ME
# placeholders. Removed 2026-10-04 in favor of _build_example_config(home)
# which substitutes the actual home directory at write time — users no
# longer need to manually edit the generated config before it works.
# The mcp.example.json file in this directory still ships the static
# template (with REPLACE_ME) for repo readers; `agentkthx mcp init` uses
# _build_example_config() and produces a ready-to-use config.
