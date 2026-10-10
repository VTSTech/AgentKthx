"""Tests for missing-binary resilience (R07.32).

Covers two "minimal system" failure modes reported from Debian 12
(Bookworm) testing:

1. ``agentkthx update`` used to shell out to pip with a ``git+https://``
   URL, which requires the git binary. On a netinstall/container without
   git the update died with a cryptic pip error. Now cmd_update falls
   back to the GitHub source tarball, which pip can download and build
   without git (plain setuptools backend — no setuptools-scm).

2. MCP servers configured with an ``npx``/``uvx`` launcher used to fail
   with a bare ``not found on $PATH`` error. The error now carries an
   actionable install hint (``command_install_hint``), shared with the
   ``agentkthx mcp install`` pre-launch warning.
"""

from __future__ import annotations

import argparse
import subprocess as sp
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentkthx.cli.commands.version import cmd_update
from agentkthx.mcp.config import MCPConfigError, MCPServerConfig, command_install_hint

# --- cmd_update: git vs tarball source selection ---------------------------


def _fake_pip_result(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.mark.parametrize("git_path", [None])
def test_update_without_git_uses_source_tarball(capsys, git_path):
    """No git on PATH → pip installs from the GitHub tarball, not git+https."""
    with (
        patch("agentkthx.cli.commands.version.shutil.which", return_value=git_path),
        patch.object(sp, "run", side_effect=[_fake_pip_result(), _fake_pip_result()]),
    ):
        rc = cmd_update(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "git not found" in out
    assert "archive/refs/heads/main.tar.gz" in out
    assert "git+https://github.com/VTSTech/AgentKthx.git" not in out


def test_update_with_git_keeps_git_url(capsys):
    """git present → behavior unchanged (git+https URL, no fallback note)."""
    with (
        patch("agentkthx.cli.commands.version.shutil.which", return_value="/usr/bin/git"),
        patch.object(sp, "run", side_effect=[_fake_pip_result(), _fake_pip_result()]),
    ):
        rc = cmd_update(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "git+https://github.com/VTSTech/AgentKthx.git" in out
    assert "main.tar.gz" not in out
    assert "git not found" not in out


def test_update_tarball_path_receives_pep668_retry_flag():
    """The PEP 668 y/n retry appends --break-system-packages to whichever
    source URL was selected — the fallback path keeps that guarantee."""
    pip_calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        pip_calls.append(list(cmd))
        if "--break-system-packages" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        # First attempt: fail with a PEP 668 stderr to trigger the retry prompt.
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="error: externally-managed-environment\n"
            "hint: pass --break-system-packages to override. See PEP 668.",
        )

    with (
        patch("agentkthx.cli.commands.version.shutil.which", return_value=None),
        patch.object(sp, "run", side_effect=fake_run),
        patch("builtins.input", return_value="y"),
    ):
        rc = cmd_update(argparse.Namespace())
    assert rc == 0
    assert any("archive/refs/heads/main.tar.gz" in " ".join(c) for c in pip_calls)
    assert any("--break-system-packages" in c for c in pip_calls)


# --- command_install_hint --------------------------------------------------


def test_hint_npx_mentions_node_and_package_managers():
    hint = command_install_hint("npx")
    assert "Node.js" in hint
    assert "apt install nodejs npm" in hint
    assert "nodejs.org" in hint


def test_hint_uvx_mentions_uv():
    hint = command_install_hint("uvx")
    assert "uv" in hint
    assert "pip install uv" in hint


def test_hint_unknown_command_generic_fallback():
    hint = command_install_hint("weirdrun")
    assert "weirdrun" in hint
    assert "$PATH" in hint


# --- resolve_command: runtime launch path carries the hint -----------------


def test_resolve_command_npx_missing_includes_hint(monkeypatch):
    monkeypatch.setattr("agentkthx.mcp.config.shutil.which", lambda _: None)
    cfg = MCPServerConfig(name="filesystem", command="npx", args=["-y", "somepkg"])
    with pytest.raises(MCPConfigError) as excinfo:
        cfg.resolve_command()
    msg = str(excinfo.value)
    assert "not found on $PATH" in msg  # original contract preserved
    assert "apt install nodejs npm" in msg  # new: actionable hint


def test_resolve_command_unknown_launcher_generic_hint(monkeypatch):
    monkeypatch.setattr("agentkthx.mcp.config.shutil.which", lambda _: None)
    cfg = MCPServerConfig(name="x", command="weirdrun")
    with pytest.raises(MCPConfigError) as excinfo:
        cfg.resolve_command()
    assert "install 'weirdrun'" in str(excinfo.value)


def test_resolve_command_found_returns_path(monkeypatch):
    monkeypatch.setattr("agentkthx.mcp.config.shutil.which", lambda c: f"/usr/bin/{c}")
    cfg = MCPServerConfig(name="x", command="npx")
    assert cfg.resolve_command() == "/usr/bin/npx"
