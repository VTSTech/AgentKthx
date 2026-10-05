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
        assert set(mcp_sub.choices) == {"list", "init", "probe", "search", "install", "uninstall"}

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
        assert "search" in captured.out
        assert "install" in captured.out
        assert "uninstall" in captured.out


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
                            "name": "memory",
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
        # filesystem is enabled — should be in the listing
        assert "filesystem" in captured.out
        # NOTE: load_mcp_config() filters out disabled entries, so `memory`
        # does NOT appear in the listing itself. The footer hint is the only
        # place it could appear, and the hint no longer mentions disabled
        # servers by name (R07.23 removed the deprecated `git` reference).
        # The pre-existing `_mcp_list` has dead-code markers for disabled
        # servers (○) — the listing only iterates enabled configs. This is
        # tracked as a separate cleanup item.


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

    def test_mcp_init_no_git_entry(self, tmp_path):
        """R07.23 regression guard: the deprecated @modelcontextprotocol/server-git
        package was removed from npm (404). The init config should NOT emit a
        'git' entry — operators should discover replacements via `mcp search git`.
        """
        target = tmp_path / "mcp.json"
        args = create_parser().parse_args(["mcp", "init", "--path", str(target)])
        rc = cmd_mcp(args)
        assert rc == 0
        with open(target) as f:
            data = json.load(f)
        names = {s["name"] for s in data["servers"]}
        assert "git" not in names
        # The audit Phase-2 placeholder should still be there
        assert "audit" in names


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


# ----------------------------- mcp search (R07.23 — live) -----------------------------


def _fake_npm_search_response(query: str) -> dict:
    """Build a fake npm search response shaped like the real registry.npmjs.org output."""
    # Canonical fake packages — enough to exercise the filter + dedupe logic
    packages = {
        "filesystem": "@modelcontextprotocol/server-filesystem",
        "git": "@modelcontextprotocol/server-git",
        "memory": "@modelcontextprotocol/server-memory",
        "fetch": "mcp-server-fetch",
        "sequential-thinking": "@modelcontextprotocol/server-sequential-thinking",
        "serena": "oraios/serena",  # this one won't be on npm; simulates GitHub-only
    }
    pkg_name = packages.get(query, f"@example/{query}-mcp")
    return {
        "objects": [
            {
                "package": {
                    "name": pkg_name,
                    "version": "1.2.3",
                    "description": f"MCP server for {query} operations",
                    "links": {
                        "homepage": f"https://example.com/{query}",
                        "npm": f"https://npmjs.com/package/{pkg_name}",
                    },
                    "license": "MIT",
                },
                "score": {"final": 0.95},
            }
        ]
    }


def _fake_npm_package_info(name: str) -> dict:
    """Build a fake npm package metadata response."""
    return {
        "name": name,
        "description": f"The {name} MCP server",
        "dist-tags": {"latest": "1.2.3"},
        "versions": {"1.2.3": {"license": "MIT"}},
        "homepage": f"https://example.com/{name}",
        "repository": {"url": f"git+https://github.com/example/{name}"},
    }


def _fake_github_search_response(query: str) -> dict:
    """Build a fake GitHub repo search response.

    Kept for TestMcpInstall (which exercises `mcp install github:owner/repo`);
    TestMcpSearch no longer uses GitHub search since the R07.23 follow-up
    removed GitHub from `mcp search` (too many non-stdio results).
    """
    return {
        "items": [
            {
                "full_name": f"example/{query}-mcp",
                "description": f"Community MCP server for {query}",
                "html_url": f"https://github.com/example/{query}-mcp",
                "stargazers_count": 42,
            }
        ]
    }


class TestMcpSearch:
    """``agentkthx mcp search`` — live search across npm (R07.23).

    All network calls are mocked via ``unittest.mock.patch`` on
    ``agentkthx.mcp.registry._http_get_json`` so the tests stay fast and
    offline. The cache is pointed at a tmp_path file so tests don't pollute
    the real ``~/.agentkthx/mcp_cache.json``.

    GitHub search was removed (R07.23 follow-up): too many non-stdio results
    (Java/Go/Rust repos, Ghidra/IDA extensions, browser plugins). GitHub
    installs still work via `mcp install github:owner/repo`.
    """

    def test_search_returns_results_from_npm(self, capsys, tmp_path, monkeypatch):
        """`mcp search filesystem` should hit npm and print at least one result."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        def fake_http(url, *, headers=None, timeout=10):
            assert "registry.npmjs.org" in url, f"search should only hit npm, got: {url}"
            assert "api.github.com" not in url, "GitHub search was removed in R07.23 follow-up"
            return _fake_npm_search_response("filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(["mcp", "search", "filesystem"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "filesystem" in captured.out
        assert "@modelcontextprotocol/server-filesystem" in captured.out
        # Output should indicate npm source
        assert "source: npm" in captured.out

    def test_search_writes_to_cache(self, tmp_path, monkeypatch):
        """A successful search should write results to the cache file."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        with patch(
            "agentkthx.mcp.registry._http_get_json",
            side_effect=lambda url, **kw: _fake_npm_search_response("filesystem"),
        ):
            args = create_parser().parse_args(["mcp", "search", "filesystem"])
            cmd_mcp(args)
        assert cache_file.exists()
        with open(cache_file) as f:
            cache_data = json.load(f)
        # Cache key format is "search:npm:<query>:<limit>"
        cache_keys = list(cache_data.keys())
        assert any("filesystem" in k for k in cache_keys)
        assert any("npm" in k for k in cache_keys)

    def test_search_uses_cache_on_second_call(self, capsys, tmp_path, monkeypatch):
        """A second search within the TTL should NOT re-hit the network."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        call_count = {"n": 0}

        def fake_http(url, *, headers=None, timeout=10):
            call_count["n"] += 1
            return _fake_npm_search_response("filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args1 = create_parser().parse_args(["mcp", "search", "filesystem"])
            cmd_mcp(args1)
            args2 = create_parser().parse_args(["mcp", "search", "filesystem"])
            cmd_mcp(args2)
        # First call: 1 npm fetch. Second call: 0 (cache hit).
        assert call_count["n"] == 1

    def test_search_refresh_bypasses_cache(self, tmp_path, monkeypatch):
        """`--refresh` should force a fresh fetch even if the cache is warm."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        call_count = {"n": 0}

        def fake_http(url, *, headers=None, timeout=10):
            call_count["n"] += 1
            return _fake_npm_search_response("filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            cmd_mcp(create_parser().parse_args(["mcp", "search", "filesystem"]))
            # Warm the cache; second call with --refresh should re-fetch
            cmd_mcp(create_parser().parse_args(["mcp", "search", "filesystem", "--refresh"]))
        assert call_count["n"] == 2  # 1 from first call + 1 from refresh

    def test_search_does_not_hit_github(self, tmp_path, monkeypatch):
        """R07.23 follow-up regression guard: search must NOT hit api.github.com.

        GitHub search was removed because too many results were non-stdio
        (Java/Go/Rust repos, Ghidra/IDA extensions, browser plugins). This
        test ensures no future change accidentally re-enables it.
        """
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        urls_hit: list[str] = []

        def fake_http(url, *, headers=None, timeout=10):
            urls_hit.append(url)
            return _fake_npm_search_response("filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(["mcp", "search", "filesystem"])
            rc = cmd_mcp(args)
        assert rc == 0
        # No GitHub URL should be hit during `mcp search`
        assert not any(
            "github.com" in u for u in urls_hit
        ), f"GitHub was hit during mcp search: {urls_hit}"

    def test_search_json_emits_valid_payload(self, capsys, tmp_path, monkeypatch):
        """`--json` emits a parseable JSON payload with results array."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        with patch(
            "agentkthx.mcp.registry._http_get_json",
            side_effect=lambda url, **kw: _fake_npm_search_response("filesystem"),
        ):
            args = create_parser().parse_args(["mcp", "search", "filesystem", "--json"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "results" in payload
        assert "query" in payload
        assert payload["query"] == "filesystem"
        assert payload["source"] == "npm"
        assert isinstance(payload["results"], list)
        assert len(payload["results"]) > 0
        assert "name" in payload["results"][0]

    def test_search_no_results_prints_helpful_message(self, capsys, tmp_path, monkeypatch):
        """A query that returns no results should print 'No MCP servers found'."""
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        def fake_http(url, *, headers=None, timeout=10):
            return {"objects": []}

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(["mcp", "search", "zzz_no_match_zzz"])
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No MCP servers found" in captured.out

    def test_search_handles_network_error_gracefully(self, capsys, tmp_path, monkeypatch):
        """If npm fails, search should print a Network-error hint (R07.24, ROB-41).

        Previously printed "No MCP servers found" + the error in red, which was
        misleading — the user's query was fine, the registry was unreachable.
        Now leads with "Network error" + actionable hints (--refresh, check
        connection, AGENTKTHX_GITHUB_TOKEN).
        """
        cache_file = tmp_path / "mcp_cache.json"
        monkeypatch.setattr("agentkthx.mcp.cache.cache_path", lambda: cache_file)

        from agentkthx.mcp.registry import MCPRegistryError

        def fake_http(url, *, headers=None, timeout=10):
            raise MCPRegistryError(f"simulated failure for {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(["mcp", "search", "filesystem"])
            rc = cmd_mcp(args)
        assert rc == 0  # graceful — returns 0 with empty results
        captured = capsys.readouterr()
        # R07.24 (ROB-41): lead with the network-error framing, not "No MCP servers found"
        assert "Network error" in captured.out
        assert "simulated failure" in captured.out  # the actual error reason surfaces
        assert "--refresh" in captured.out  # actionable hint
        # The misleading "No MCP servers found" message must NOT appear when errors present
        assert "No MCP servers found" not in captured.out


# ----------------------------- mcp install (R07.23) -----------------------------


class TestMcpInstall:
    """``agentkthx mcp install <name>`` — write a server to mcp.json (R07.23).

    Always live — fetches npm metadata before writing. Overwrites existing
    entries with the same name. Network calls are mocked via patch on
    ``agentkthx.mcp.registry._http_get_json``.
    """

    def test_install_short_name_resolves_via_npm_search(self, capsys, tmp_path, monkeypatch):
        """`mcp install filesystem` should resolve via npm search, fetch metadata, write to mcp.json."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            if "registry.npmjs.org" in url:
                return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")
            raise AssertionError(f"unexpected URL: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "filesystem", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        # Verify the entry was written
        with open(cfg_file) as f:
            data = json.load(f)
        names = [s["name"] for s in data["servers"]]
        assert "filesystem" in names
        fs_entry = next(s for s in data["servers"] if s["name"] == "filesystem")
        assert fs_entry["command"] == "npx"
        assert "@modelcontextprotocol/server-filesystem" in fs_entry["args"]

    def test_install_full_npm_package_name(self, capsys, tmp_path, monkeypatch):
        """`mcp install @modelcontextprotocol/server-filesystem` fetches metadata directly."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            # Should NOT hit /-/v1/search — only the package metadata endpoint
            assert "/-/v1/search" not in url, "should fetch package directly, not via search"
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "@modelcontextprotocol/server-filesystem"]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert any(s["name"] == "filesystem" for s in data["servers"])

    def test_install_creates_mcp_json_if_missing(self, tmp_path, monkeypatch):
        """If mcp.json doesn't exist, `mcp install` creates it with the new entry."""
        cfg_file = tmp_path / "subdir" / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "filesystem", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        assert cfg_file.exists()
        with open(cfg_file) as f:
            data = json.load(f)
        assert data["version"] == "0.1"
        assert len(data["servers"]) == 1

    def test_install_overwrites_existing_entry(self, capsys, tmp_path, monkeypatch):
        """Per maintainer spec: `mcp install` overwrites an existing entry with the same name."""
        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {
                            "name": "filesystem",
                            "command": "old-cmd",
                            "args": ["old"],
                            "enabled": False,
                        },
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "filesystem", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert len(data["servers"]) == 1  # not duplicated
        entry = data["servers"][0]
        assert entry["command"] == "npx"  # not "old-cmd"
        assert "@modelcontextprotocol/server-filesystem" in entry["args"]
        captured = capsys.readouterr()
        assert "overwrote" in captured.out

    def test_install_dry_run_does_not_write(self, capsys, tmp_path, monkeypatch):
        """`--dry-run` prints the snippet but does not modify mcp.json."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "filesystem", "--dry-run", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        assert not cfg_file.exists()  # nothing was written
        captured = capsys.readouterr()
        assert "Dry run" in captured.out
        assert "filesystem" in captured.out

    # ------------------------- R07.24 SEC-20 -------------------------

    def test_install_dry_run_warns_would_overwrite(self, capsys, tmp_path, monkeypatch):
        """`--dry-run` against an existing entry prints `WOULD OVERWRITE` (SEC-20)."""
        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {
                            "name": "filesystem",
                            "command": "old-cmd",
                            "args": ["old"],
                            "enabled": True,
                        },
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "filesystem", "--dry-run", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        # mcp.json untouched on dry-run
        with open(cfg_file) as f:
            data = json.load(f)
        assert data["servers"][0]["command"] == "old-cmd"
        captured = capsys.readouterr()
        assert "WOULD OVERWRITE" in captured.out
        assert "--no-overwrite" in captured.out

    def test_install_no_overwrite_refuses_on_collision(self, capsys, tmp_path, monkeypatch):
        """`--no-overwrite` against an existing entry fails with rc=5 (SEC-20)."""
        cfg_file = tmp_path / "mcp.json"
        original = {
            "version": "0.1",
            "servers": [
                {
                    "name": "filesystem",
                    "command": "old-cmd",
                    "args": ["old"],
                    "enabled": True,
                },
            ],
        }
        cfg_file.write_text(json.dumps(original))
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "filesystem",
                    "--no-overwrite",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 5  # distinct from rc=4 (unreadable) and rc=3 (not found)
        # mcp.json untouched
        with open(cfg_file) as f:
            data = json.load(f)
        assert data == original  # the old entry is preserved verbatim
        captured = capsys.readouterr()
        assert "Refusing to overwrite" in captured.out
        assert "mcp uninstall filesystem" in captured.out

    def test_install_no_overwrite_allows_when_no_collision(self, capsys, tmp_path, monkeypatch):
        """`--no-overwrite` with no existing entry installs normally (SEC-20 regression guard)."""
        cfg_file = tmp_path / "mcp.json"  # does not exist
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "filesystem",
                    "--no-overwrite",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0  # no collision → install proceeds
        with open(cfg_file) as f:
            data = json.load(f)
        assert len(data["servers"]) == 1
        captured = capsys.readouterr()
        assert "Installed" in captured.out

    def test_install_no_overwrite_flag_exists(self):
        """`--no-overwrite` is wired on the install subparser (SEC-20 contract)."""
        parser = create_parser()
        args = parser.parse_args(["mcp", "install", "filesystem", "--no-overwrite"])
        assert args.no_overwrite is True
        # default (flag not passed) is False
        args_default = parser.parse_args(["mcp", "install", "filesystem"])
        assert args_default.no_overwrite is False

    def test_install_json_emits_snippet(self, capsys, tmp_path, monkeypatch):
        """`--json` emits the snippet as JSON, doesn't touch mcp.json."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(["mcp", "install", "filesystem", "--json"])
            rc = cmd_mcp(args)
        assert rc == 0
        assert not cfg_file.exists()
        captured = capsys.readouterr()
        snippet = json.loads(captured.out)
        assert snippet["name"] == "filesystem"
        assert snippet["command"] == "npx"

    def test_install_github_repo_with_overrides(self, capsys, tmp_path, monkeypatch):
        """`mcp install github:owner/repo --command uvx --args=...` builds a GitHub snippet."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        # GitHub repo install should NOT hit npm at all
        def fake_http(url, *, headers=None, timeout=10):
            raise AssertionError(f"GitHub-only install should not hit network: {url}")

        # --args is a single string (split on spaces in the handler) so that
        # values starting with -- (like --from) don't confuse argparse.
        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "github:oraios/serena",
                    "--command",
                    "uvx",
                    "--args=--from git+https://github.com/oraios/serena serena start-mcp-server",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        entry = data["servers"][0]
        assert entry["name"] == "serena"
        assert entry["command"] == "uvx"
        assert "git+https://github.com/oraios/serena" in entry["args"]

    def test_install_github_shorthand_owner_repo(self, capsys, tmp_path, monkeypatch):
        """`mcp install owner/repo` (no github: prefix) also works as GitHub shorthand."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            raise AssertionError(f"GitHub-only install should not hit network: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "LaurieWired/GhidraMCP",
                    "--command",
                    "uvx",
                    "--args=--from git+https://github.com/LaurieWired/GhidraMCP GhidraMCP",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        entry = data["servers"][0]
        assert entry["name"] == "GhidraMCP"
        assert entry["command"] == "uvx"
        assert "git+https://github.com/LaurieWired/GhidraMCP" in entry["args"]

    def test_install_github_prefix_strips_correctly(self, capsys, tmp_path, monkeypatch):
        """`github:owner/repo` should strip the prefix and build the right git URL."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            raise AssertionError(f"GitHub-only install should not hit network: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "github:LaurieWired/GhidraMCP",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        entry = data["servers"][0]
        # Default command for GitHub is uvx, default args include git+https URL
        assert entry["command"] == "uvx"
        # The git URL should NOT contain "github:" — the prefix should be stripped
        args_str = " ".join(entry["args"])
        assert "github:LaurieWired" not in args_str
        assert "git+https://github.com/LaurieWired/GhidraMCP" in args_str

    def test_install_at_scope_package_npm(self, capsys, tmp_path, monkeypatch):
        """`mcp install @scope/package` fetches npm metadata directly (no search step)."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            # Should NOT hit /-/v1/search — only the package metadata endpoint
            assert "/-/v1/search" not in url, "should fetch package directly, not via search"
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "@modelcontextprotocol/server-filesystem",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert any(s["name"] == "filesystem" for s in data["servers"])

    def test_install_warns_when_launch_command_not_on_path(self, capsys, tmp_path, monkeypatch):
        """If the launch command isn't on $PATH, install should warn to stderr
        with an install hint. The entry is still written (user may install the
        dep later)."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        # Force shutil.which to return None for everything — simulates a
        # system where the launch command (npx) isn't installed.
        monkeypatch.setattr("shutil.which", lambda cmd: None)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "@modelcontextprotocol/server-filesystem",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0  # still writes the entry
        captured = capsys.readouterr()
        assert "not found on $PATH" in captured.err
        assert "Install it" in captured.err
        with open(cfg_file) as f:
            data = json.load(f)
        assert any(s["name"] == "filesystem" for s in data["servers"])

    def test_install_github_warns_about_best_effort_command(self, capsys, tmp_path, monkeypatch):
        """GitHub installs should warn that the launch command is a best-effort
        guess and point to the repo's README for the actual install command."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            raise AssertionError(f"GitHub-only install should not hit network: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "github:LaurieWired/GhidraMCP",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        captured = capsys.readouterr()
        # Should mention the README check, the GitHub URL, and the uninstall hint
        assert "best-effort guess" in captured.out
        assert "https://github.com/LaurieWired/GhidraMCP" in captured.out
        assert "mcp uninstall" in captured.out

    def test_install_404_returns_3(self, capsys, tmp_path, monkeypatch):
        """If npm returns 404 for a package, install should return rc=3."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        from agentkthx.mcp.registry import MCPRegistryError

        def fake_http(url, *, headers=None, timeout=10):
            raise MCPRegistryError(
                "HTTP 404 from https://registry.npmjs.org/nonexistent: Not Found"
            )

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                ["mcp", "install", "@example/nonexistent-mcp", "--config", str(cfg_file)]
            )
            rc = cmd_mcp(args)
        assert rc == 3
        captured = capsys.readouterr()
        assert "not found on npm" in captured.out

    def test_install_as_overrides_short_name(self, capsys, tmp_path, monkeypatch):
        """`--as <name>` overrides the derived short name in the written entry."""
        cfg_file = tmp_path / "mcp.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        def fake_http(url, *, headers=None, timeout=10):
            if "/-/v1/search" in url:
                return _fake_npm_search_response("filesystem")
            return _fake_npm_package_info("@modelcontextprotocol/server-filesystem")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            args = create_parser().parse_args(
                [
                    "mcp",
                    "install",
                    "filesystem",
                    "--as",
                    "myfs",
                    "--config",
                    str(cfg_file),
                ]
            )
            rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert any(s["name"] == "myfs" for s in data["servers"])


# ----------------------------- mcp uninstall (R07.23) -----------------------------


class TestMcpUninstall:
    """``agentkthx mcp uninstall <name>`` — remove a server from mcp.json (R07.23).

    Pairs with ``mcp install`` so users don't have to edit mcp.json by hand
    when an install doesn't work out (e.g. Java-only repos that can't be
    launched as a stdio subprocess, packages that 404, broken configs).
    """

    def test_uninstall_removes_entry(self, capsys, tmp_path, monkeypatch):
        """`mcp uninstall <name>` removes the named entry from mcp.json."""
        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {
                            "name": "filesystem",
                            "command": "npx",
                            "args": ["-y", "fs"],
                            "enabled": True,
                        },
                        {
                            "name": "GhidraMCP",
                            "command": "uvx",
                            "args": ["--from", "x"],
                            "enabled": True,
                        },
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        args = create_parser().parse_args(
            ["mcp", "uninstall", "GhidraMCP", "--config", str(cfg_file)]
        )
        rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        names = [s["name"] for s in data["servers"]]
        assert "GhidraMCP" not in names
        assert "filesystem" in names  # the other entry is untouched
        captured = capsys.readouterr()
        assert "Uninstalled: GhidraMCP" in captured.out
        assert "1 server remaining" in captured.out

    def test_uninstall_unknown_name_returns_3(self, capsys, tmp_path, monkeypatch):
        """Uninstalling a name that isn't in mcp.json should return rc=3 + list available."""
        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {"name": "filesystem", "command": "npx", "args": [], "enabled": True},
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        args = create_parser().parse_args(
            ["mcp", "uninstall", "nonexistent", "--config", str(cfg_file)]
        )
        rc = cmd_mcp(args)
        assert rc == 3
        captured = capsys.readouterr()
        assert "not found" in captured.out
        assert "filesystem" in captured.out  # available list

    def test_uninstall_missing_config_returns_1(self, capsys, tmp_path, monkeypatch):
        """If mcp.json doesn't exist, uninstall should return rc=1 (nothing to remove)."""
        cfg_file = tmp_path / "nonexistent.json"
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        args = create_parser().parse_args(
            ["mcp", "uninstall", "anything", "--config", str(cfg_file)]
        )
        rc = cmd_mcp(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.out

    def test_uninstall_last_entry_leaves_empty_servers_array(self, capsys, tmp_path, monkeypatch):
        """Uninstalling the last entry should leave an empty servers array, not delete the file."""
        cfg_file = tmp_path / "mcp.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        {"name": "only-one", "command": "npx", "args": [], "enabled": True},
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        args = create_parser().parse_args(
            ["mcp", "uninstall", "only-one", "--config", str(cfg_file)]
        )
        rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert data["servers"] == []
        assert data["version"] == "0.1"  # version field preserved
        captured = capsys.readouterr()
        assert "0 servers remaining" in captured.out

    def test_uninstall_preserves_other_entries_intact(self, capsys, tmp_path, monkeypatch):
        """Uninstalling one entry should not modify any other entry."""
        cfg_file = tmp_path / "mcp.json"
        original_fs = {
            "name": "filesystem",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "enabled": True,
            "comment": "preserve me",
        }
        original_st = {
            "name": "sequential-thinking",
            "command": "npx",
            "args": ["-y", "st"],
            "enabled": True,
        }
        cfg_file.write_text(
            json.dumps(
                {
                    "version": "0.1",
                    "servers": [
                        original_fs,
                        {"name": "broken", "command": "uvx", "args": ["x"], "enabled": True},
                        original_st,
                    ],
                }
            )
        )
        monkeypatch.setattr("agentkthx.mcp.default_config_path", lambda: cfg_file)

        args = create_parser().parse_args(["mcp", "uninstall", "broken", "--config", str(cfg_file)])
        rc = cmd_mcp(args)
        assert rc == 0
        with open(cfg_file) as f:
            data = json.load(f)
        assert len(data["servers"]) == 2
        # filesystem entry should be byte-for-byte unchanged
        assert data["servers"][0] == original_fs
        assert data["servers"][1] == original_st


# ----------------------------- _wire_mcp (agent_factory) -----------------------------


class TestWireMcpNoOp:
    """When --mcp is not passed, _wire_mcp should return None (complete no-op)."""

    def test_no_mcp_flag_is_noop(self):
        """An args namespace without `mcp` should return None."""
        args = argparse.Namespace()  # no `mcp` attr at all
        result = _wire_mcp(args, tools=None)
        assert result is None

    def test_mcp_none_is_noop(self):
        """args.mcp = None (flag absent) should return None."""
        args = argparse.Namespace(mcp=None, mcp_config=None)
        result = _wire_mcp(args, tools=None)
        assert result is None

    def test_mcp_with_missing_config_does_not_raise(self, capsys):
        """--mcp passed but config file missing → warn + return None."""
        # load_mcp_config returns [] (no servers) — simulates missing/empty config
        with patch("agentkthx.mcp.load_mcp_config", return_value=[]):
            args = argparse.Namespace(mcp=[], mcp_config=None)
            result = _wire_mcp(args, tools=None)
        # Should have printed a warning, not raised
        captured = capsys.readouterr()
        assert "no servers configured" in captured.err
        assert result is None

    def test_mcp_only_session_creates_empty_registry(self):
        """--mcp without --tools: empty ToolRegistry created, MCP tools bridged in.

        This is the regression guard for the R07.22 fix. Before the fix,
        passing --mcp without --tools left tools=None, so MCP tools were
        never bridged and the system prompt had no Tool Reference section.
        The model would then hallucinate `shell` (not in the registry)
        instead of picking an MCP tool.

        After the fix: _wire_mcp creates an empty ToolRegistry, bridges
        MCP tools into it, and stashes it as manager._bridged_registry
        so the caller can pass it to Agent.__init__.
        """
        from agentkthx.mcp.config import MCPServerConfig
        from agentkthx.mcp.manager import MCPManager
        from agentkthx.tools.registry import ToolRegistry

        # Mock a successful MCP connection with one server + one tool
        mock_config = MCPServerConfig(name="testserver", command="python3")
        with patch("agentkthx.mcp.load_mcp_config", return_value=[mock_config]):
            with patch.object(MCPManager, "connect_all", return_value=[]):
                with patch.object(MCPManager, "register_into", return_value=1):
                    args = argparse.Namespace(mcp=[], mcp_config=None)
                    manager = _wire_mcp(args, tools=None)

        # Manager should be returned (not None)
        assert manager is not None
        # The bridged registry should exist and be a ToolRegistry
        assert hasattr(manager, "_bridged_registry")
        assert isinstance(manager._bridged_registry, ToolRegistry)
        # The caller (agent_factory._build_agent) reads this to pass to Agent.__init__
        # so the system prompt builder sees has_tools=True


# ----------------------------- registry helpers (R07.24) -----------------------------


class TestSearchAllWithErrors:
    """``search_all_with_errors`` (R07.24, ROB-41) — returns ``(results, errors)``.

    Distinguishes "0 results from a successful search" from "0 results because
    all sources failed". The original ``search_all`` is a thin wrapper that
    drops the errors tuple — back-compat preserved.
    """

    def test_search_all_with_errors_returns_both_npm_and_github_hits(self, monkeypatch):
        from agentkthx.mcp.registry import search_all_with_errors

        def fake_http(url, *, headers=None, timeout=10):
            if "registry.npmjs.org" in url:
                return _fake_npm_search_response("filesystem")
            if "api.github.com" in url:
                return {
                    "total_count": 1,
                    "items": [
                        {
                            "name": "serena",
                            "full_name": "oraios/serena",
                            "description": "LSP-based MCP server",
                            "stargazers_count": 500,
                            "license": {"spdx_id": "MIT"},
                            "html_url": "https://github.com/oraios/serena",
                        }
                    ],
                }
            raise AssertionError(f"unexpected URL: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            results, errors = search_all_with_errors("filesystem")
        assert errors == []  # both sources succeeded
        assert len(results) >= 2  # at least one npm + one github
        sources = {r.get("source") for r in results}
        assert "npm" in sources
        assert "github" in sources

    def test_search_all_with_errors_captures_per_source_failure(self, monkeypatch):
        """When both npm + GitHub fail, errors list preserves per-source reasons."""
        from agentkthx.mcp.registry import MCPRegistryError, search_all_with_errors

        def fake_http(url, *, headers=None, timeout=10):
            if "registry.npmjs.org" in url:
                raise MCPRegistryError("npm timeout")
            if "api.github.com" in url:
                raise MCPRegistryError("github 403")
            raise AssertionError(f"unexpected URL: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            results, errors = search_all_with_errors("filesystem")
        assert results == []  # no results because both failed
        assert len(errors) == 2
        sources = {src for src, _ in errors}
        assert sources == {"npm", "github"}
        # Per-source messages preserved (this is the ROB-41 contract — a caller
        # can branch on the source that failed)
        msgs = " ".join(msg for _, msg in errors)
        assert "npm timeout" in msgs
        assert "github 403" in msgs

    def test_search_all_with_errors_partial_failure(self, monkeypatch):
        """One source fails, the other succeeds — partial results + the failure recorded."""
        from agentkthx.mcp.registry import MCPRegistryError, search_all_with_errors

        def fake_http(url, *, headers=None, timeout=10):
            if "registry.npmjs.org" in url:
                return _fake_npm_search_response("filesystem")
            if "api.github.com" in url:
                raise MCPRegistryError("github down")
            raise AssertionError(f"unexpected URL: {url}")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            results, errors = search_all_with_errors("filesystem")
        # npm results survive
        assert len(results) >= 1
        assert all(r.get("source") == "npm" for r in results)
        # GitHub failure recorded
        assert len(errors) == 1
        assert errors[0][0] == "github"
        assert "github down" in errors[0][1]

    def test_search_all_drops_errors_for_back_compat(self, monkeypatch):
        """``search_all`` (legacy) returns just results — back-compat preserved."""
        from agentkthx.mcp.registry import MCPRegistryError, search_all

        def fake_http(url, *, headers=None, timeout=10):
            raise MCPRegistryError("simulated failure")

        with patch("agentkthx.mcp.registry._http_get_json", side_effect=fake_http):
            results = search_all("filesystem")
        # legacy returns a list, not a tuple — and silently swallows the failure
        assert results == []
        assert isinstance(results, list)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
