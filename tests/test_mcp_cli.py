"""CLI integration tests for the MCP Phase 1 wiring (R07.21).

Covers:
* ``--mcp`` / ``--mcp-config`` flags exist on chat/run/agent parsers
* ``agentkthx mcp`` subcommand is registered
* ``agentkthx mcp list`` works against an empty / missing config
* ``agentkthx mcp init`` writes the example config (and refuses overwrite)
* ``agentkthx mcp probe <name>`` errors cleanly on unknown server
* ``_wire_mcp`` is a no-op when ``--mcp`` is not passed (regression guard)
* ``_wire_mcp`` skips gracefully when no config exists

These tests do NOT spawn real MCP servers (consistent with the project's
mocked-unit-test convention, TEST-01 open). End-to-end subprocess tests
belong in an integration tier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.cli.agent_factory import _wire_mcp  # noqa: E402
from agentkthx.cli.commands.mcp import cmd_mcp  # noqa: E402
from agentkthx.cli.parser import create_parser  # noqa: E402

# ----------------------------- flag wiring -----------------------------


class TestMcpFlagsWired:
    """--mcp and --mcp-config should appear on every agent-args subcommand."""

    @pytest.mark.parametrize("cmd", ["chat", "run", "agent"])
    def test_mcp_flag_present(self, cmd):
        parser = create_parser()
        # run requires a positional prompt, which must come BEFORE --mcp
        # because nargs="*" greedily consumes everything after the flag.
        # chat/agent have no positional so the order doesn't matter.
        if cmd == "run":
            args = parser.parse_args([cmd, "hello", "--mcp", "fs", "git"])
        else:
            args = parser.parse_args([cmd, "--mcp", "fs", "git"])
        assert args.mcp == ["fs", "git"]

    @pytest.mark.parametrize("cmd", ["chat", "run", "agent"])
    def test_mcp_flag_bare_enables_all(self, cmd):
        """Bare --mcp (no names) should set args.mcp to [] (enable-all sentinel)."""
        parser = create_parser()
        if cmd == "run":
            args = parser.parse_args([cmd, "hello", "--mcp"])
        else:
            args = parser.parse_args([cmd, "--mcp"])
        assert args.mcp == []

    @pytest.mark.parametrize("cmd", ["chat", "run", "agent"])
    def test_mcp_flag_absent_is_none(self, cmd):
        """Without --mcp, args.mcp is None (the opt-in sentinel)."""
        parser = create_parser()
        if cmd == "run":
            args = parser.parse_args([cmd, "hello"])
        else:
            args = parser.parse_args([cmd])
        assert args.mcp is None

    def test_mcp_config_flag(self):
        parser = create_parser()
        args = parser.parse_args(["chat", "--mcp-config", "/tmp/custom.json"])
        assert args.mcp_config == "/tmp/custom.json"

    def test_mcp_flag_not_on_test_subcommand(self):
        """The `test` subcommand has its own parser and should NOT have --mcp
        (it's not in add_agent_args; test takes a subset of flags only)."""
        parser = create_parser()
        args = parser.parse_args(["test", "all"])
        assert getattr(args, "mcp", None) is None
        assert getattr(args, "mcp_config", None) is None


# ----------------------------- subcommand registration -----------------------------


class TestMcpSubcommandRegistered:
    def test_mcp_in_subcommands(self):
        parser = create_parser()
        assert "mcp" in parser._subparsers_action.choices

    def test_mcp_subcommands_registered(self):
        parser = create_parser()
        mcp_parser = parser._subparsers_action.choices["mcp"]
        mcp_sub = next(a for a in mcp_parser._actions if isinstance(a, argparse._SubParsersAction))
        assert set(mcp_sub.choices) == {"list", "init", "probe"}

    def test_mcp_probe_has_name_positional(self):
        parser = create_parser()
        args = parser.parse_args(["mcp", "probe", "filesystem"])
        assert args.mcp_command == "probe"
        assert args.name == "filesystem"

    def test_mcp_init_has_force_flag(self):
        parser = create_parser()
        args = parser.parse_args(["mcp", "init", "--force", "--path", "/tmp/x.json"])
        assert args.force is True
        assert args.path == "/tmp/x.json"

    def test_mcp_no_subcommand_prints_help(self, capsys):
        """`agentkthx mcp` (no subcommand) should print help and return 0."""
        args = create_parser().parse_args(["mcp"])
        rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "MCP" in captured.out
        assert "list" in captured.out and "init" in captured.out and "probe" in captured.out


# ----------------------------- mcp list -----------------------------


class TestMcpList:
    def test_mcp_list_missing_config(self, capsys, tmp_path, monkeypatch):
        """When the config file doesn't exist, list should exit 0 with a hint."""
        # Point default_config_path at a non-existent path
        fake_path = tmp_path / "nonexistent.json"
        with patch("agentkthx.mcp.default_config_path", return_value=fake_path):
            args = create_parser().parse_args(["mcp", "list"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No MCP config" in captured.out
        assert "agentkthx mcp init" in captured.out

    def test_mcp_list_empty_config(self, capsys, tmp_path):
        """An empty (no-servers) config should print 'No enabled servers'."""
        cfg = tmp_path / "mcp.json"
        cfg.write_text(json.dumps({"version": "0.1", "servers": []}))
        with patch("agentkthx.mcp.default_config_path", return_value=cfg):
            args = create_parser().parse_args(["mcp", "list"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No enabled servers" in captured.out

    def test_mcp_list_with_servers(self, capsys, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {
                            "name": "filesystem",
                            "command": "python3",
                            "args": ["-c", "pass"],
                            "enabled": True,
                        },
                        {
                            "name": "git",
                            "command": "python3",
                            "enabled": False,
                        },
                    ],
                }
            )
        )
        with patch("agentkthx.mcp.default_config_path", return_value=cfg):
            args = create_parser().parse_args(["mcp", "list"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "filesystem" in captured.out
        assert "git" in captured.out


# ----------------------------- mcp init -----------------------------


class TestMcpInit:
    def test_mcp_init_writes_file(self, capsys, tmp_path):
        target = tmp_path / "mcp.json"
        args = create_parser().parse_args(["mcp", "init", "--path", str(target)])
        rc = cmd_mcp(args)
        assert rc == 0
        assert target.exists()
        # Verify the file is valid JSON and load-able
        with open(target) as f:
            data = json.load(f)
        assert data["version"] == "0.1"
        assert any(s["name"] == "filesystem" for s in data["servers"])
        # File should be 0o600 (or close — some FSs don't honor chmod)
        captured = capsys.readouterr()
        assert "Wrote:" in captured.out

    def test_mcp_init_refuses_overwrite(self, capsys, tmp_path):
        target = tmp_path / "mcp.json"
        target.write_text("{}")
        args = create_parser().parse_args(["mcp", "init", "--path", str(target)])
        rc = cmd_mcp(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "already exists" in captured.out

    def test_mcp_init_force_overwrites(self, capsys, tmp_path):
        target = tmp_path / "mcp.json"
        target.write_text('{"old": true}')
        args = create_parser().parse_args(["mcp", "init", "--force", "--path", str(target)])
        rc = cmd_mcp(args)
        assert rc == 0
        with open(target) as f:
            data = json.load(f)
        assert "old" not in data
        assert "servers" in data


# ----------------------------- mcp probe -----------------------------


class TestMcpProbe:
    def test_mcp_probe_unknown_server(self, capsys, tmp_path):
        cfg = tmp_path / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {"name": "filesystem", "command": "python3", "enabled": True},
                    ],
                }
            )
        )
        args = create_parser().parse_args(["mcp", "probe", "nonexistent", "--config", str(cfg)])
        rc = cmd_mcp(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "not in config" in captured.out
        assert "filesystem" in captured.out  # available list


# ----------------------------- _wire_mcp (agent_factory) -----------------------------


class TestWireMcpNoOp:
    """When --mcp is not passed, _wire_mcp should be a complete no-op."""

    def test_no_mcp_flag_is_noop(self):
        """An args namespace without `mcp` should not touch the agent."""

        class FakeAgent:
            pass

        args = argparse.Namespace()  # no `mcp` attr at all
        # Should not raise, should not set any attr on the agent
        _wire_mcp(FakeAgent(), args, tools=None)
        # Agent should have no _mcp_manager
        assert not hasattr(FakeAgent(), "_mcp_manager")

    def test_mcp_none_is_noop(self):
        """args.mcp = None (flag absent) should not touch the agent."""

        class FakeAgent:
            pass

        agent = FakeAgent()
        args = argparse.Namespace(mcp=None, mcp_config=None)
        _wire_mcp(agent, args, tools=None)
        assert not hasattr(agent, "_mcp_manager")

    def test_mcp_with_missing_config_does_not_raise(self, capsys):
        """--mcp passed but config file missing → warn + no _mcp_manager set."""

        class FakeAgent:
            pass

        agent = FakeAgent()
        # load_mcp_config returns [] (no servers) — simulates missing/empty config
        with patch("agentkthx.mcp.load_mcp_config", return_value=[]):
            args = argparse.Namespace(mcp=[], mcp_config=None)
            _wire_mcp(agent, args, tools=None)
        # Should have printed a warning, not raised
        captured = capsys.readouterr()
        assert "no servers configured" in captured.err
        assert not hasattr(agent, "_mcp_manager")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
