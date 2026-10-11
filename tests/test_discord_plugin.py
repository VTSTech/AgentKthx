"""
AgentKthx — Discord Plugin Integration Tests (M0)

The real manifest against the v0.2 loader (discovery, provides shape,
secrets-guard compliance), register/unregister round-trip through a real
PluginManager, and the cli.main setup_parser wiring that makes
`agentkthx discord --dry-run` parseable. Offline — cmd_discord exits
before any REST call when DISCORD_BOT_TOKEN is unset.

Written by VTSTech — https://www.vts-tech.org
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.cli.main import main as cli_main  # noqa: E402
from agentkthx.plugins import PluginManager  # noqa: E402
from agentkthx.plugins.discord.discord_bot import (  # noqa: E402
    DISCORD_INTENTS,
    BotConfig,
)

# The agentkthx.cli facade re-exports `main` (the function) as a package
# attribute, shadowing the submodule for attribute access — resolve the real
# module through the import system for monkeypatching (see
# tests/test_cli_package_split.py for the same trap).
CLI_MAIN_MODULE = importlib.import_module("agentkthx.cli.main")

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "agentkthx" / "plugins" / "discord"


# ---------------------------------------------------------------------------
# Manifest compliance
# ---------------------------------------------------------------------------


class TestManifest:
    def _manifest(self) -> dict:
        raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        return raw["extensions"]["org.vts-tech.agentkthx"]

    def test_discovered_by_real_loader(self):
        pm = PluginManager()
        manifests = pm.discover(force=True)
        discord = next(m for m in manifests if m.name == "discord")
        assert discord.name == "discord"  # discovery implies dir-name match

    def test_type_feature_with_cli_command(self):
        ext = self._manifest()
        assert ext["type"] == "feature"
        assert ext["provides"]["cli_commands"] == ["discord"]

    def test_provides_on_shutdown_hook(self):
        ext = self._manifest()
        assert ext["provides"]["hooks"] == {"on_shutdown": "discord_bot.on_shutdown"}

    def test_config_env_prefix_and_defaults(self):
        ext = self._manifest()
        assert ext["config"]["env_prefix"] == "DISCORD"
        defaults = ext["config"]["defaults"]
        # secrets guard: token default MUST be empty (non-empty -> loader warns)
        assert defaults["DISCORD_BOT_TOKEN"] == ""
        assert defaults["DISCORD_USER_COOLDOWN_S"] == "10"
        assert defaults["DISCORD_MAX_REPLY_MSGS"] == "3"
        # R07.33: tools are OFF by default on Discord
        assert defaults["DISCORD_TOOLS"] == ""
        # R07.33: fresh sessions per restart; TTL lowered 30 -> 7 days
        assert defaults["DISCORD_KEEP_SESSIONS"] == "false"
        assert defaults["DISCORD_SESSION_TTL_DAYS"] == "7"

    def test_no_backends_provided(self):
        """Discord is a feature plugin — it must not register inference."""
        ext = self._manifest()
        assert "backends" not in ext["provides"]

    def test_name_constraints(self):
        raw = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        assert raw["name"] == PLUGIN_DIR.name  # dir name match


# ---------------------------------------------------------------------------
# register / unregister round-trip
# ---------------------------------------------------------------------------


class TestRegisterUnregister:
    def test_load_registers_command_and_hook(self):
        pm = PluginManager()
        pm.discover(force=True)
        loaded = pm.load("discord")
        assert loaded is not None
        commands = pm.get_cli_commands()
        assert "discord" in commands
        info = commands["discord"]
        assert callable(info["handler"])
        assert callable(info["setup_parser"])
        assert info["owner"] == "discord"
        assert pm.list_hooks("on_shutdown"), "on_shutdown hook must be registered"
        pm.unload("discord")
        assert "discord" not in pm.get_cli_commands()
        assert not pm.list_hooks("on_shutdown")

    def test_load_all_survives_discord_plugin(self):
        """Loading every plugin (as cli.main does) must not explode."""
        pm = PluginManager()
        pm.discover(force=True)
        loaded = pm.load_all()
        names = {p.manifest.name for p in loaded}
        assert "discord" in names


# ---------------------------------------------------------------------------
# CLI wiring (setup_parser now invoked by cli.main)
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_ALLOW_GUILDS", raising=False)
    monkeypatch.setenv("AGENTKTHX_NO_UPDATE_CHECK", "1")
    yield


class TestCliWiring:
    def test_discord_help_lists_flags(self, capsys):
        """--help on the plugin subparser proves setup_parser was invoked."""
        with pytest.raises(SystemExit) as excinfo:
            cli_main(["discord", "--help"])
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "--dry-run" in out
        assert "--backend" in out
        assert "--debug" in out
        assert "--keep" in out
        assert "--num-ctx" in out
        assert "--max-tokens" in out

    def test_discord_without_token_fails_cleanly(self, monkeypatch, capsys, clean_env):
        monkeypatch.setattr(CLI_MAIN_MODULE, "_run_update_check", lambda: None)
        monkeypatch.setattr(CLI_MAIN_MODULE, "_print_update_notice", lambda: None)
        rc = cli_main(["discord"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "DISCORD_BOT_TOKEN" in out

    def test_discord_accepts_flags(self, monkeypatch, capsys, clean_env):
        """Flags parse (no unrecognized-argument error) even without a token."""
        monkeypatch.setattr(CLI_MAIN_MODULE, "_run_update_check", lambda: None)
        monkeypatch.setattr(CLI_MAIN_MODULE, "_print_update_notice", lambda: None)
        rc = cli_main(
            [
                "discord",
                "--dry-run",
                "--backend",
                "ollama",
                "--num-ctx",
                "16384",
                "--max-tokens",
                "2k",
            ]
        )
        out = capsys.readouterr().out
        assert rc == 1  # still no token in CI
        assert "DISCORD_BOT_TOKEN" in out

    def test_intents_constant(self):
        # GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
        assert DISCORD_INTENTS == (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)


# ---------------------------------------------------------------------------
# BotConfig
# ---------------------------------------------------------------------------


class TestBotConfig:
    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        monkeypatch.setenv("DISCORD_ALLOW_GUILDS", "g1, g2")
        monkeypatch.setenv("DISCORD_ALLOW_DMS", "true")
        monkeypatch.setenv("DISCORD_USER_COOLDOWN_S", "3.5")
        cfg = BotConfig.from_env()
        assert cfg.token == "tok"
        assert cfg.allow_guilds == ["g1", "g2"]
        assert cfg.allow_dms is True
        assert cfg.cooldown_s == 3.5

    def test_argparse_overrides_win(self, monkeypatch):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        cfg = BotConfig.from_env({"dry_run": True, "max_steps": 7})
        assert cfg.dry_run is True
        assert cfg.max_steps == 7

    def test_none_override_keeps_env_value(self, monkeypatch):
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
        cfg = BotConfig.from_env({"backend": None})
        assert cfg.token == "tok"
        assert cfg.backend is None
