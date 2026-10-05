"""Smoke tests for the MCP client scaffold.

These tests cover the pure-Python pieces of the scaffold:
* Config loading + validation (no subprocess needed)
* Tool name namespacing
* inputSchema -> ToolParam conversion
* CallToolResult flattening

They do NOT test the actual subprocess transport — that requires a real
MCP server binary and belongs in an integration test tier (TEST-01, open).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Make sure the agentkthx package is importable when running tests from
# the repo root without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.mcp.config import (  # noqa: E402
    MCPConfigError,
    MCPServerConfig,
    load_mcp_config,
    write_example_config,
)
from agentkthx.mcp.manager import (  # noqa: E402
    MCPManager,
    _extract_params,
    _flatten_call_result,
    _ns,
    _split_ns,
)

# ----------------------------- config -----------------------------


def test_server_config_validates_name():
    """Names must be alphanumeric/-/_ (used as a tool-name prefix)."""
    with pytest.raises(MCPConfigError):
        MCPServerConfig(name="bad/name", command="echo")
    with pytest.raises(MCPConfigError):
        MCPServerConfig(name="", command="echo")
    with pytest.raises(MCPConfigError):
        MCPServerConfig(name="has space", command="echo")


def test_server_config_validates_command():
    with pytest.raises(MCPConfigError):
        MCPServerConfig(name="x", command="")


def test_server_config_resolve_command_rejects_relative(tmp_path):
    """Relative paths that aren't on $PATH should fail resolve_command."""
    cfg = MCPServerConfig(name="x", command="./nonexistent-binary")
    with pytest.raises(MCPConfigError):
        cfg.resolve_command()


def test_server_config_resolve_command_accepts_python(tmp_path):
    """`python3` should resolve via $PATH on any POSIX CI runner."""
    cfg = MCPServerConfig(name="x", command="python3")
    resolved = cfg.resolve_command()
    assert os.path.isabs(resolved)
    assert "python" in os.path.basename(resolved).lower()


def test_load_mcp_config_returns_empty_when_missing(tmp_path):
    """Missing config file is not an error — MCP is opt-in."""
    assert load_mcp_config(tmp_path / "does-not-exist.json") == []


def test_load_mcp_config_parses_valid_file(tmp_path):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "version": "0.1",
                "servers": [
                    {
                        "name": "fs",
                        "command": "python3",
                        "args": ["-c", "pass"],
                        "enabled": True,
                    },
                    {
                        "name": "git",
                        "command": "python3",
                        "enabled": False,  # should be filtered
                    },
                ],
            }
        )
    )
    configs = load_mcp_config(cfg_path)
    assert len(configs) == 1
    assert configs[0].name == "fs"


def test_load_mcp_config_rejects_duplicate_names(tmp_path):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "version": "0.1",
                "servers": [
                    {"name": "x", "command": "python3"},
                    {"name": "x", "command": "python3"},
                ],
            }
        )
    )
    with pytest.raises(MCPConfigError, match="duplicate"):
        load_mcp_config(cfg_path)


def test_load_mcp_config_rejects_bad_version(tmp_path):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(json.dumps({"version": "9.9", "servers": []}))
    with pytest.raises(MCPConfigError, match="unsupported config version"):
        load_mcp_config(cfg_path)


def test_write_example_config_round_trips(tmp_path):
    """write_example_config produces a file load_mcp_config can read."""
    target = tmp_path / "mcp.json"
    result_path = write_example_config(target)
    assert result_path == target
    configs = load_mcp_config(target)
    # The embedded _EXAMPLE_CONFIG (config.py) has 3 enabled servers
    # (filesystem + sequential-thinking + memory) and 1 disabled (audit —
    # Phase 2 placeholder). R07.23 (2026-10-04): the deprecated 'git' entry
    # was removed entirely from the embedded example — operators discover
    # replacements via `agentkthx mcp search git`. R07.23-dev: memory was
    # promoted to enabled-by-default after the user smoke test confirmed
    # 9 tools bridge cleanly.
    assert len(configs) >= 3  # filesystem + sequential-thinking + memory
    names = {c.name for c in configs}
    assert "filesystem" in names
    assert "sequential-thinking" in names
    assert "memory" in names
    # git should NOT appear in the generated config at all (R07.23 removed it)
    # load_mcp_config filters disabled entries, so we re-read the raw file
    # to confirm the entry is gone entirely, not just disabled.
    import json as _json

    raw = _json.loads(target.read_text())
    raw_names = {s["name"] for s in raw["servers"]}
    assert "git" not in raw_names


def test_write_example_config_substitutes_home_dir(tmp_path, monkeypatch):
    """The generated config has the actual home dir, no REPLACE_ME placeholder.

    Regression guard for the 2026-10-04 fix: users should be able to run
    `agentkthx mcp init` and immediately `mcp probe filesystem` without
    manually editing the config.
    """
    # Force Path.home() to return our tmp_path so the test is hermetic
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    target = tmp_path / "mcp.json"
    write_example_config(target)

    raw = target.read_text(encoding="utf-8")
    # No placeholder should remain
    assert "REPLACE_ME" not in raw
    # The actual home path should be in the filesystem args
    assert str(fake_home) in raw
    # The single referenced directory should have been created
    assert (fake_home / "workspace").is_dir()

    # The config should load successfully with the substituted paths
    configs = load_mcp_config(target)
    fs = next(c for c in configs if c.name == "filesystem")
    assert str(fake_home) in fs.args[-1]  # last arg is the path


def test_write_example_config_refuses_overwrite(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text("{}")
    with pytest.raises(MCPConfigError, match="already exists"):
        write_example_config(target)


# ----------------------------- namespacing -----------------------------


def test_ns_roundtrip():
    n = _ns("filesystem", "read_file")
    assert n == "filesystem__read_file"
    server, tool = _split_ns(n)
    assert server == "filesystem"
    assert tool == "read_file"


def test_split_ns_rejects_bare_name():
    with pytest.raises(ValueError):
        _split_ns("not_namespaced")


# ----------------------------- inputSchema conversion -----------------------------


def test_extract_params_basic_object():
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path"},
            "mode": {"type": "string", "enum": ["r", "w"]},
            "encoding": {"type": "string", "default": "utf-8"},
        },
        "required": ["path"],
    }
    params = _extract_params(schema)
    assert len(params) == 3
    by_name = {p.name: p for p in params}
    assert by_name["path"].required is True
    assert by_name["path"].type == "string"
    assert by_name["mode"].enum == ["r", "w"]
    assert by_name["encoding"].required is False
    assert by_name["encoding"].default == "utf-8"


def test_extract_params_handles_nullable_union():
    """JSON Schema ['string', 'null'] should map to 'string'."""
    schema = {
        "type": "object",
        "properties": {
            "filter": {"type": ["string", "null"]},
        },
    }
    params = _extract_params(schema)
    assert len(params) == 1
    assert params[0].type == "string"


def test_extract_params_empty_schema():
    assert _extract_params({}) == []
    assert _extract_params(None) == []  # type: ignore[arg-type]


# ----------------------------- result flattening -----------------------------


def test_flatten_call_result_text():
    result = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
    }
    assert _flatten_call_result(result) == "hello\nworld"


def test_flatten_call_result_error_flag():
    result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
    out = _flatten_call_result(result)
    assert out.startswith("[MCP tool reported error]")
    assert "boom" in out


def test_flatten_call_result_image_skipped():
    """Image content should be replaced with a placeholder, not mangled."""
    result = {
        "content": [
            {"type": "image", "mimeType": "image/png", "data": "iVBOR..."},
            {"type": "text", "text": "ok"},
        ]
    }
    out = _flatten_call_result(result)
    assert "iVBOR" not in out
    assert "image/png" in out
    assert "ok" in out


def test_flatten_call_result_resource():
    result = {"content": [{"type": "resource", "resource": {"uri": "file:///etc/hosts"}}]}
    out = _flatten_call_result(result)
    assert "file:///etc/hosts" in out


def test_flatten_call_result_bare_string():
    """Some servers return content as a bare string instead of a list."""
    assert _flatten_call_result({"content": "hello"}) == "hello"


# ----------------------------- manager (no subprocess) -----------------------------


def test_manager_describe_before_connect():
    """describe() should report configured-but-not-connected before connect_all."""
    cfg = MCPServerConfig(name="x", command="python3")
    mgr = MCPManager([cfg])
    info = mgr.describe()
    assert "x" in info
    assert info["x"]["connected"] is False
    assert info["x"]["tools"] == 0


def test_manager_register_into_requires_connect():
    """register_into() should raise if connect_all() hasn't been called."""
    cfg = MCPServerConfig(name="x", command="python3")
    mgr = MCPManager([cfg])
    from agentkthx.tools.registry import ToolRegistry

    with pytest.raises(Exception, match="connect_all"):
        mgr.register_into(ToolRegistry())


# ----------------------------- connect_all verbose -----------------------------


def test_connect_all_verbose_prints_progress(capsys, monkeypatch):
    """connect_all(verbose=True) should print per-server progress to stderr.

    Uses mocking to avoid spawning real subprocesses — verifies the verbose
    output shape (header + per-server lines) without requiring a live MCP
    server binary.
    """
    cfg_a = MCPServerConfig(name="alpha", command="python3")
    cfg_b = MCPServerConfig(name="beta", command="python3")
    mgr = MCPManager([cfg_a, cfg_b])

    # Mock MCPClient so connect() + list_tools() succeed without subprocess
    class FakeClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def connect(self):
            pass

        def list_tools(self):
            return [{"name": f"{self.cfg.name}_tool", "description": "test"}]

        def close(self, timeout=2.0):
            pass

        @property
        def is_alive(self):
            return True

        @property
        def server_info(self):
            return {"name": "fake", "version": "1.0"}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", FakeClient)

    failures = mgr.connect_all(verbose=True)
    assert failures == []

    captured = capsys.readouterr()
    # Header should list both servers
    assert "[MCP] probing 2 server(s): alpha, beta" in captured.err
    # Per-server "connecting" line
    assert "[MCP]   alpha: connecting..." in captured.err
    assert "[MCP]   beta: connecting..." in captured.err
    # Per-server result line with tool count
    assert "[MCP]   alpha: 1 tool(s)" in captured.err
    assert "[MCP]   beta: 1 tool(s)" in captured.err


def test_connect_all_verbose_prints_failures(capsys, monkeypatch):
    """connect_all(verbose=True) should print the error for skipped servers."""
    cfg = MCPServerConfig(name="broken", command="python3")
    mgr = MCPManager([cfg])

    from agentkthx.mcp.client import MCPClientError

    class FailingClient:
        def __init__(self, cfg):
            pass

        def connect(self):
            raise MCPClientError("simulated connect failure")

    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", FailingClient)

    failures = mgr.connect_all(skip_failures=True, verbose=True)
    assert len(failures) == 1
    assert failures[0][0] == "broken"

    captured = capsys.readouterr()
    assert "[MCP] probing 1 server(s): broken" in captured.err
    assert "[MCP]   broken: connecting..." in captured.err
    assert "[MCP]   broken: failed (simulated connect failure); skipped" in captured.err


def test_connect_all_silent_by_default(capsys, monkeypatch):
    """connect_all(verbose=False) (the default) should produce NO stderr output."""
    cfg = MCPServerConfig(name="quiet", command="python3")
    mgr = MCPManager([cfg])

    class FakeClient:
        def __init__(self, cfg):
            pass

        def connect(self):
            pass

        def list_tools(self):
            return []

        def close(self, timeout=2.0):
            pass

    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", FakeClient)

    mgr.connect_all()  # verbose defaults to False
    captured = capsys.readouterr()
    assert captured.err == ""


if __name__ == "__main__":
    # Allow running this file directly for quick smoke checks during dev
    sys.exit(pytest.main([__file__, "-v"]))
