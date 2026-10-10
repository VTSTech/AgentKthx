"""
AgentKthx — `agentkthx discord setup` Wizard Tests (M0.1)

Fully offline: I/O is injected (input_fn / secret_fn / confirm_fn / out /
rest_factory), the env file lives under tmp_path, and token validation
uses a fake REST client. Covers file primitives (parse/merge/write/load),
input normalizers, the wizard flow (fresh, keep-existing, validate-fail,
abort, DMs default/stored, OAuth2 invite URL), and the `discord setup`
CLI dispatch.

Written by VTSTech — https://www.vts-tech.org
"""

import importlib
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.discord import setup as setup_mod  # noqa: E402
from agentkthx.plugins.discord.discord_bot import BotConfig  # noqa: E402
from agentkthx.plugins.discord.setup import (  # noqa: E402
    MANAGED_KEYS,
    default_env_path,
    invite_url,
    load_env_file,
    merge_env_lines,
    normalize_ids,
    parse_env_file,
    redact_token,
    run_setup,
    token_warning,
    write_env_file,
)

CLI_MAIN_MODULE = importlib.import_module("agentkthx.cli.main")

GOOD_TOKEN = "MTAkNzIzNDU2Nzg5MDEyMzQ1Njc4.Gabcde.0123456789abcdefghijklmnopqrstuvw"


class FakeRest:
    """Stands in for DiscordRest in wizard validation."""

    def __init__(self, me: dict | None = None, error: Exception | None = None):
        self._me = me or {"id": "1558579664803471480", "username": "AgentKthx"}
        self._error = error

    def get_self(self) -> dict:
        if self._error is not None:
            raise self._error
        return self._me

    def get_application(self) -> dict:
        if self._error is not None:
            raise self._error
        return {"id": "999888777", "name": "AgentKthx"}


def rest_ok(token: str) -> FakeRest:
    return FakeRest()


def rest_401(token: str) -> FakeRest:
    from agentkthx.plugins.discord.rest import DiscordRestError

    return FakeRest(error=DiscordRestError(401, None, "Unauthorized"))


def make_wizard(inputs: list[str], secrets: list[str], confirm: bool = True):
    """Build injectable I/O fns that replay scripted answers.
    Exhausted scripts answer "" (like pressing Enter) instead of raising."""
    it_inputs = iter(inputs)
    it_secrets = iter(secrets)
    return (
        lambda prompt="": next(it_inputs, ""),
        lambda prompt="": next(it_secrets, ""),
        lambda prompt, default: confirm,
    )


# ---------------------------------------------------------------------------
# Env-file primitives
# ---------------------------------------------------------------------------


class TestParseLine:
    def test_plain(self):
        assert setup_mod._parse_line("KEY=value") == ("KEY", "value")

    def test_quotes_stripped(self):
        assert setup_mod._parse_line('KEY="v w"') == ("KEY", "v w")
        assert setup_mod._parse_line("KEY='v w'") == ("KEY", "v w")

    def test_export_prefix(self):
        assert setup_mod._parse_line("export KEY=val") == ("KEY", "val")

    def test_comment_blank_junk(self):
        assert setup_mod._parse_line("# hi") is None
        assert setup_mod._parse_line("") is None
        assert setup_mod._parse_line("   ") is None
        assert setup_mod._parse_line("not-a-pair") is None


class TestWriteEnvFile:
    def test_fresh_file_header_and_keys(self, tmp_path):
        path = str(tmp_path / "discord.env")
        rc = write_env_file(path, {"DISCORD_BOT_TOKEN": GOOD_TOKEN})
        assert rc is None
        text = Path(path).read_text(encoding="utf-8")
        assert f"DISCORD_BOT_TOKEN={GOOD_TOKEN}" in text
        for key in ("DISCORD_ALLOW_GUILDS", "DISCORD_ALLOW_USERS", "DISCORD_ALLOW_DMS"):
            assert f"{key}=" in text
        assert "do NOT commit" in text

    def test_permissions_0600(self, tmp_path):
        path = str(tmp_path / "discord.env")
        write_env_file(path, {"DISCORD_BOT_TOKEN": "t"})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_no_tmp_leftover(self, tmp_path):
        path = str(tmp_path / "discord.env")
        write_env_file(path, {"DISCORD_BOT_TOKEN": "t"})
        assert not (tmp_path / "discord.env.tmp").exists()

    def test_merge_preserves_unknown_keys_and_comments(self, tmp_path):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(
            "# my comment\n"
            "DISCORD_BOT_TOKEN=oldtoken\n"
            "DISCORD_MAX_WORKERS=4  # hand-tuned\n"
            "OTHER_APP_KEY=keepme\n",
            encoding="utf-8",
        )
        write_env_file(
            path,
            {
                "DISCORD_BOT_TOKEN": "newtoken",
                "DISCORD_ALLOW_GUILDS": "111",
                "DISCORD_ALLOW_USERS": "222",
            },
        )
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        assert "DISCORD_BOT_TOKEN=newtoken" in lines
        assert "DISCORD_MAX_WORKERS=4  # hand-tuned" in lines
        assert "OTHER_APP_KEY=keepme" in lines
        assert "# my comment" in lines
        assert "DISCORD_ALLOW_GUILDS=111" in lines
        assert "DISCORD_ALLOW_USERS=222" in lines
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_merge_replaces_in_place_order_stable(self, tmp_path):
        path = str(tmp_path / "discord.env")
        write_env_file(
            path,
            {"DISCORD_BOT_TOKEN": "a", "DISCORD_ALLOW_GUILDS": "1", "DISCORD_ALLOW_USERS": "2"},
        )
        write_env_file(path, {"DISCORD_BOT_TOKEN": "b"})
        text = Path(path).read_text(encoding="utf-8")
        assert "DISCORD_BOT_TOKEN=b" in text
        assert "DISCORD_BOT_TOKEN=a" not in text
        assert text.index("DISCORD_BOT_TOKEN") < text.index("DISCORD_ALLOW_GUILDS")

    def test_creates_parent_dir(self, tmp_path):
        path = str(tmp_path / "sub" / "dir" / "discord.env")
        write_env_file(path, {"DISCORD_BOT_TOKEN": "t"})
        assert Path(path).exists()


class TestLoadEnvFile:
    def test_missing_file_returns_zero(self, tmp_path):
        assert load_env_file(str(tmp_path / "nope.env")) == 0

    def test_fills_only_missing_discord_keys(self, tmp_path, monkeypatch):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(
            "DISCORD_BOT_TOKEN=filetok\nOTHER_KEY=x\n",
            encoding="utf-8",
        )
        env: dict = {"DISCORD_BOT_TOKEN": "envwins"}
        applied = load_env_file(path, environ=env)
        assert applied == 0  # token overridden by env; OTHER_KEY not DISCORD_*
        assert env["DISCORD_BOT_TOKEN"] == "envwins"

    def test_applies_discord_keys_only(self, tmp_path):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(
            f"DISCORD_BOT_TOKEN={GOOD_TOKEN}\n" "DISCORD_ALLOW_GUILDS=111,222\n" "NOT_DISCORD=1\n",
            encoding="utf-8",
        )
        env: dict = {}
        applied = load_env_file(path, environ=env)
        assert applied == 2
        assert env["DISCORD_BOT_TOKEN"] == GOOD_TOKEN
        assert env["DISCORD_ALLOW_GUILDS"] == "111,222"
        assert "NOT_DISCORD" not in env

    def test_botconfig_picks_up_file_token(self, tmp_path, monkeypatch):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(f"DISCORD_BOT_TOKEN={GOOD_TOKEN}\n", encoding="utf-8")
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        load_env_file(path)
        assert BotConfig.from_env().token == GOOD_TOKEN


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


class TestNormalizeIds:
    def test_mixed_separators(self):
        assert normalize_ids("111, 222 333,,444") == (["111", "222", "333", "444"], [])

    def test_dedup_preserving_order(self):
        assert normalize_ids("111, 222,111") == (["111", "222"], [])

    def test_drops_non_numeric(self):
        valid, dropped = normalize_ids("111, abc, def 222")
        assert valid == ["111", "222"]
        assert dropped == ["abc", "def"]

    def test_empty(self):
        assert normalize_ids("") == ([], [])


class TestTokenWarning:
    def test_ok(self):
        assert token_warning(GOOD_TOKEN) is None

    def test_empty(self):
        assert token_warning("") is not None

    def test_whitespace(self):
        assert token_warning(f"{GOOD_TOKEN} ") is None  # trailing strip handled
        assert "whitespace" in (token_warning("a b c") or "")

    def test_dot_count(self):
        assert "dot-separated" in (token_warning("nodots" * 20) or "")

    def test_short(self):
        assert "truncated" in (token_warning("MTA.bcd.efg") or "")


class TestRedactToken:
    def test_long(self):
        shown = redact_token(GOOD_TOKEN)
        assert shown.startswith("MTAk")
        assert shown.endswith("uvw")
        assert GOOD_TOKEN not in shown

    def test_short_and_empty(self):
        assert redact_token("tiny") == "••••"
        assert redact_token("") == "(none)"


class TestMergeEnvLines:
    def test_appends_missing_with_separator(self):
        merged = merge_env_lines("A=1\n", {"DISCORD_BOT_TOKEN": "t"})
        lines = merged.splitlines()
        assert lines[0] == "A=1"
        assert lines[-1] == "DISCORD_BOT_TOKEN=t"

    def test_empty_existing(self):
        assert merge_env_lines("", {"DISCORD_BOT_TOKEN": "t"}).startswith("DISCORD_BOT_TOKEN=t\n")


# ---------------------------------------------------------------------------
# Wizard flow
# ---------------------------------------------------------------------------


class TestRunSetup:
    def _run(self, tmp_path, inputs, secrets, rest=rest_ok, confirm=True):
        in_fn, sec_fn, conf_fn = make_wizard(inputs, secrets, confirm)
        outs: list[str] = []
        rc = run_setup(
            input_fn=in_fn,
            secret_fn=sec_fn,
            confirm_fn=conf_fn,
            rest_factory=rest,
            path=str(tmp_path / "discord.env"),
            out=outs.append,
        )
        return rc, outs

    def test_fresh_run_writes_and_validates(self, tmp_path):
        rc, outs = self._run(tmp_path, ["111, 222 abc", "999"], [GOOD_TOKEN])
        assert rc == 0
        values = parse_env_file(str(tmp_path / "discord.env"))
        assert values["DISCORD_BOT_TOKEN"] == GOOD_TOKEN
        assert values["DISCORD_ALLOW_GUILDS"] == "111,222"  # abc dropped as non-numeric
        assert values["DISCORD_ALLOW_USERS"] == "999"
        assert values["DISCORD_ALLOW_DMS"] == "true"  # scripted confirm=True
        joined = "\n".join(outs)
        assert "OK — token valid: AgentKthx" in joined
        assert "Application ID: 999888777" in joined
        assert "client_id=999888777&scope=bot&permissions=68608" in joined
        assert "dropped non-numeric IDs: abc" in joined
        assert GOOD_TOKEN not in joined  # screen output is redacted
        assert "DISCORD_BOT_TOKEN=MTAk…tuvw" in joined

    def test_keeps_stored_token_on_enter(self, tmp_path):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(
            f"DISCORD_BOT_TOKEN={GOOD_TOKEN}\nDISCORD_ALLOW_GUILDS=555\n",
            encoding="utf-8",
        )
        rc, _ = self._run(tmp_path, ["666", ""], [""])  # Enter on token keeps stored
        assert rc == 0
        values = parse_env_file(str(path))
        assert values["DISCORD_BOT_TOKEN"] == GOOD_TOKEN
        assert values["DISCORD_ALLOW_GUILDS"] == "666"
        assert values["DISCORD_ALLOW_USERS"] == ""  # empty written explicitly
        assert values["DISCORD_ALLOW_DMS"] == "true"  # scripted confirm=True

    def test_validation_failure_still_saves(self, tmp_path):
        rc, outs = self._run(tmp_path, ["111"], [GOOD_TOKEN], rest=rest_401)
        assert rc == 0
        assert "Validation failed" in "\n".join(outs)
        values = parse_env_file(str(tmp_path / "discord.env"))
        assert values["DISCORD_BOT_TOKEN"] == GOOD_TOKEN

    def test_abort_leaves_file_untouched(self, tmp_path):
        path = tmp_path / "discord.env"
        path.write_text("DISCORD_BOT_TOKEN=old\n", encoding="utf-8")
        in_fn, _, conf_fn = make_wizard([], [GOOD_TOKEN])
        in_fn = lambda prompt="": (_ for _ in ()).throw(KeyboardInterrupt())  # noqa: E731
        outs: list[str] = []
        rc = run_setup(
            input_fn=in_fn,
            secret_fn=lambda prompt="": GOOD_TOKEN,
            confirm_fn=conf_fn,
            rest_factory=rest_ok,
            path=str(path),
            out=outs.append,
        )
        assert rc == 1
        assert "aborted" in "\n".join(outs)
        assert path.read_text(encoding="utf-8") == "DISCORD_BOT_TOKEN=old\n"

    def test_no_token_reprompts(self, tmp_path):
        rc, _ = self._run(tmp_path, ["111"], ["", GOOD_TOKEN])
        assert rc == 0
        values = parse_env_file(str(tmp_path / "discord.env"))
        assert values["DISCORD_BOT_TOKEN"] == GOOD_TOKEN

    def test_non_numeric_warning(self, tmp_path):
        rc, outs = self._run(tmp_path, ["abc, 111"], [GOOD_TOKEN])
        assert rc == 0
        assert "dropped non-numeric IDs: abc" in "\n".join(outs)

    def test_hints_present(self, tmp_path):
        rc, outs = self._run(tmp_path, ["111"], [GOOD_TOKEN])
        joined = "\n".join(outs)
        assert "discord.com/developers/applications" in joined
        assert "Developer Mode" in joined
        assert "Copy Server ID" in joined
        assert "Copy User ID" in joined
        assert "MESSAGE CONTENT" in joined
        assert "URL Generator" in joined
        assert "ALLOW DIRECT MESSAGES" in joined

    def test_custom_path_respected(self, tmp_path):
        custom = str(tmp_path / "custom.env")
        in_fn, sec_fn, conf_fn = make_wizard(["111"], [GOOD_TOKEN])
        rc = run_setup(
            input_fn=in_fn,
            secret_fn=sec_fn,
            confirm_fn=conf_fn,
            rest_factory=rest_ok,
            path=custom,
            out=lambda m: None,
        )
        assert rc == 0
        assert Path(custom).exists()

    def test_dms_off_by_default(self, tmp_path):
        rc, outs = self._run(tmp_path, ["111"], [GOOD_TOKEN], confirm=False)
        assert rc == 0
        values = parse_env_file(str(tmp_path / "discord.env"))
        assert values["DISCORD_ALLOW_DMS"] == "false"
        assert "DMs: OFF" in "\n".join(outs)

    def test_dms_keeps_stored_true_on_enter(self, tmp_path):
        path = str(tmp_path / "discord.env")
        Path(path).write_text(
            f"DISCORD_BOT_TOKEN={GOOD_TOKEN}\nDISCORD_ALLOW_DMS=true\n",
            encoding="utf-8",
        )
        in_fn, sec_fn, _ = make_wizard(["", ""], [""])  # Enter everywhere
        outs: list[str] = []
        rc = run_setup(
            input_fn=in_fn,
            secret_fn=sec_fn,
            confirm_fn=lambda prompt, default: default,  # Enter = shown default
            rest_factory=rest_ok,
            path=path,
            out=outs.append,
        )
        assert rc == 0
        assert parse_env_file(path)["DISCORD_ALLOW_DMS"] == "true"
        assert "DMs: ON" in "\n".join(outs)
        assert "Current: true (Enter keeps it)" in "\n".join(outs)

    def test_invite_url_format(self):
        assert invite_url("123456789012345678") == (
            "https://discord.com/oauth2/authorize"
            "?client_id=123456789012345678&scope=bot&permissions=68608"
        )


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


class TestCliDispatch:
    def test_discord_setup_invokes_wizard(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []

        def fake_run_setup(**kwargs):
            calls.append(kwargs)
            return 7  # sentinel rc must pass through

        monkeypatch.setattr(setup_mod, "run_setup", fake_run_setup)
        rc = CLI_MAIN_MODULE.main(["discord", "setup"])
        assert rc == 7
        assert len(calls) == 1  # cmd_discord delegates with defaults (path resolves via HOME)

    def test_unknown_subcommand_rc2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        rc = CLI_MAIN_MODULE.main(["discord", "bogus"])
        assert rc == 2

    def test_normal_run_loads_env_file(self, tmp_path, monkeypatch, capsys):
        """No subcommand: ~/.agentkthx/.env is loaded before the token check
        (proven with an allowlists-only file so the run stays fully offline)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        env_dir = tmp_path / ".agentkthx"
        env_dir.mkdir()
        (env_dir / ".env").write_text("DISCORD_ALLOW_GUILDS=111\n", encoding="utf-8")
        rc = CLI_MAIN_MODULE.main(["discord", "--dry-run"])
        captured = capsys.readouterr().out
        assert "loaded 1 setting(s)" in captured
        assert "DISCORD_BOT_TOKEN is not set" in captured
        assert rc == 1

    def test_default_env_path_uses_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert default_env_path() == str(tmp_path / ".agentkthx" / ".env")

    def test_managed_keys_order(self):
        assert MANAGED_KEYS == (
            "DISCORD_BOT_TOKEN",
            "DISCORD_ALLOW_GUILDS",
            "DISCORD_ALLOW_USERS",
            "DISCORD_ALLOW_DMS",
        )


# ---------------------------------------------------------------------------
# Offline safety
# ---------------------------------------------------------------------------


def test_setup_module_lazy_rest_import():
    """Plugin spec: lazy imports. A fresh import of the setup module must not
    pull .rest (network) at module level — only inside functions."""
    for name in [n for n in sys.modules if n.startswith("agentkthx.plugins.discord.rest")]:
        del sys.modules[name]
    importlib.reload(setup_mod)
    assert "agentkthx.plugins.discord.rest" not in sys.modules
