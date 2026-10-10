"""
AgentKthx — Discord M2 Slash Command Tests

Offline: interactions are parsed from raw dispatch dicts, the gate and
quick handlers run as pure functions with injected fakes, and the pool
wiring is exercised by calling the interaction-thread entry points
directly (no executor waiting, no inference, no network).
See docs/DISCORD_PLUGIN_PLAN.md §10, §12.

Written by VTSTech — https://www.vts-tech.org
"""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core.models import AgentRun  # noqa: E402
from agentkthx.core.persistent_memory import PersistentMemory  # noqa: E402
from agentkthx.plugins.discord import discord_bot as db_mod  # noqa: E402
from agentkthx.plugins.discord.commands import (  # noqa: E402
    CALLBACK_DEFER,
    CALLBACK_MESSAGE,
    EPHEMERAL,
    SLASH_COMMANDS,
    Interaction,
    gate_interaction,
    handle_model,
    handle_reset,
    handle_soul,
    handle_status,
    is_owner,
    parse_interaction,
)
from agentkthx.plugins.discord.discord_bot import (  # noqa: E402
    BotConfig,
    Job,
    ResponderPool,
)
from agentkthx.plugins.discord.policy import Policy  # noqa: E402
from agentkthx.plugins.discord.rest import DiscordRestError  # noqa: E402

GUILD = "1550663536752066570"
CHANNEL = "222333444555666777"
USER = "403985188666867712"
DM_CHANNEL = "333444555666777888"


def inter_payload(
    command="status", *, guild=GUILD, channel=CHANNEL, user=USER, options=None, iid="i1", app="app1"
):
    d = {
        "id": iid,
        "token": f"itok-{iid}",
        "application_id": app,
        "channel_id": channel,
        "data": {
            "name": command,
            "options": [{"name": k, "value": v} for k, v in (options or {}).items()],
        },
    }
    if guild is None:
        d["user"] = {"id": user, "username": "vtstech"}
    else:
        d["guild_id"] = guild
        d["member"] = {"user": {"id": user, "username": "vtstech"}}
    return d


def make_inter(**kw) -> Interaction:
    return parse_interaction(inter_payload(**kw))


# ---------------------------------------------------------------------------
# parse_interaction
# ---------------------------------------------------------------------------


class TestParseInteraction:
    def test_guild_payload_uses_member_user(self):
        inter = make_inter(command="ask", options={"prompt": "hi", "ephemeral": True})
        assert inter.guild_id == GUILD
        assert not inter.is_dm
        assert inter.user_id == USER
        assert inter.username == "vtstech"
        assert inter.command == "ask"
        assert inter.options == {"prompt": "hi", "ephemeral": True}
        assert inter.ephemeral is True
        assert inter.token.startswith("itok-")
        assert inter.app_id == "app1"

    def test_dm_payload_uses_top_level_user(self):
        inter = make_inter(command="reset", guild=None, channel=DM_CHANNEL)
        assert inter.guild_id is None
        assert inter.is_dm
        assert inter.channel_id == DM_CHANNEL

    def test_ephemeral_false_variants(self):
        assert make_inter(options={"ephemeral": False}).ephemeral is False
        assert make_inter(command="ask").ephemeral is False
        assert make_inter(options={"ephemeral": "true"}).ephemeral is True

    def test_tolerant_missing_fields(self):
        inter = parse_interaction({})
        assert inter.command == ""
        assert inter.user_id == ""
        assert inter.options == {}
        assert inter.is_dm


class TestSlashCommandDefs:
    def test_six_unique_commands(self):
        names = [c["name"] for c in SLASH_COMMANDS]
        assert sorted(names) == ["ask", "model", "reset", "soul", "status", "think"]
        assert len(set(names)) == len(names)

    def test_ask_has_prompt_required_and_ephemeral_bool(self):
        ask = next(c for c in SLASH_COMMANDS if c["name"] == "ask")
        by_name = {o["name"]: o for o in ask["options"]}
        assert by_name["prompt"]["required"] is True
        assert by_name["prompt"]["type"] == 3
        assert by_name["ephemeral"]["type"] == 5
        assert by_name["ephemeral"]["required"] is False

    def test_every_option_has_type_and_description(self):
        for cmd in SLASH_COMMANDS:
            for opt in cmd.get("options", []):
                assert opt["type"] in (3, 5)
                assert opt.get("description")


# ---------------------------------------------------------------------------
# gate_interaction + owner
# ---------------------------------------------------------------------------


class TestGate:
    def base(self, **kw):
        params = dict(
            allow_guilds=frozenset({GUILD}),
            allow_channels=frozenset(),
            allow_users=frozenset({USER}),
            allow_dms=False,
        )
        params.update(kw)
        return params

    def test_guild_allowlisted_user_ok(self):
        assert gate_interaction(make_inter(), **self.base()) is None

    def test_guild_not_allowlisted(self):
        inter = make_inter(guild="999")
        assert gate_interaction(inter, **self.base()) == "guild-not-allowed"

    def test_empty_guild_allowlist_denies(self):
        inter = make_inter()
        assert gate_interaction(inter, **self.base(allow_guilds=frozenset())) == (
            "guild-not-allowed"
        )

    def test_channel_allowlist_narrows(self):
        inter = make_inter(channel="999")
        assert gate_interaction(inter, **self.base(allow_channels=frozenset({"c-ok"}))) == (
            "channel-not-allowed"
        )

    def test_user_allowlist(self):
        inter = make_inter(user="222")
        assert gate_interaction(inter, **self.base()) == "user-not-allowed"

    def test_dm_denied_by_default(self):
        inter = make_inter(guild=None)
        assert gate_interaction(inter, **self.base()) == "dms-disabled"

    def test_dm_allowed_with_allowlist(self):
        inter = make_inter(guild=None)
        params = self.base(allow_dms=True)
        assert gate_interaction(inter, **params) is None
        bad = make_inter(guild=None, user="222")
        assert gate_interaction(bad, **params) == "user-not-allowed"

    def test_no_user_denied(self):
        inter = make_inter(user="")
        assert gate_interaction(inter, **self.base()) == "no-user"

    def test_is_owner(self):
        assert is_owner(USER, [USER])
        assert not is_owner("222", [USER])
        assert not is_owner(USER, [])  # empty owner list == nobody


# ---------------------------------------------------------------------------
# Quick handlers (pure)
# ---------------------------------------------------------------------------


class TestQuickHandlers:
    def test_reset_found_and_missing(self):
        inter = make_inter(command="reset")
        content, eph = handle_reset(inter, session_key="s1", delete_fn=lambda k: True)
        assert "cleared" in content and "s1" in content and eph
        content, _ = handle_reset(inter, session_key="s1", delete_fn=lambda k: False)
        assert "nothing to clear" in content

    def test_reset_delete_crash_is_caught(self):
        inter = make_inter(command="reset")

        def boom(key):
            raise RuntimeError("db locked")

        content, _ = handle_reset(inter, session_key="s1", delete_fn=boom)
        assert "RuntimeError" in content

    def test_status_content(self):
        inter = make_inter(command="status")
        content, eph = handle_status(
            inter,
            backend="zai",
            model="glm-4.5-flash",
            soul="none",
            tools="calculator",
            max_steps=5,
            cooldown_s=10.0,
            uptime_s=3725.0,
            queue_depth=2,
            agent_runs=7,
            session_key="discord-g1-c2",
        )
        assert eph
        assert "zai" in content and "glm-4.5-flash" in content
        assert "1h02m05s" in content
        assert "agent runs this session: 7" in content
        assert "discord-g1-c2" in content

    def test_model_show_and_set(self):
        inter = make_inter(command="model")
        content, _ = handle_model(inter, current_model="glm-4.5-flash", owner=True, name="")
        assert "glm-4.5-flash" in content
        content, _ = handle_model(
            make_inter(command="model", options={"name": "qwen3:8b"}),
            current_model="glm-4.5-flash",
            owner=True,
            name="qwen3:8b",
        )
        assert "qwen3:8b" in content

    def test_model_owner_gate(self):
        content, eph = handle_model(
            make_inter(command="model", options={"name": "x"}),
            current_model="m",
            owner=False,
            name="x",
        )
        assert "owners" in content and eph

    def test_soul_show_and_set(self):
        content, _ = handle_soul(make_inter(command="soul"), current_soul=None, owner=True, name="")
        assert "none" in content
        content, _ = handle_soul(
            make_inter(command="soul", options={"name": "kthx-trading"}),
            current_soul="none",
            owner=True,
            name="kthx-trading",
            validate_fn=lambda n: n == "kthx-trading",
        )
        assert "kthx-trading" in content

    def test_soul_owner_gate_and_unknown(self):
        content, _ = handle_soul(
            make_inter(command="soul", options={"name": "x"}),
            current_soul="none",
            owner=False,
            name="x",
        )
        assert "owners" in content
        content, _ = handle_soul(
            make_inter(command="soul", options={"name": "nope"}),
            current_soul="none",
            owner=True,
            name="nope",
            validate_fn=lambda n: False,
        )
        assert "Unknown soul" in content

    def test_soul_validator_crash_does_not_block(self):
        def boom(n):
            raise RuntimeError("loader offline")

        content, _ = handle_soul(
            make_inter(command="soul", options={"name": "x"}),
            current_soul="none",
            owner=True,
            name="x",
            validate_fn=boom,
        )
        assert "set to" in content


# ---------------------------------------------------------------------------
# Pool wiring (interaction thread entry points called directly)
# ---------------------------------------------------------------------------


class FakeRest:
    """Same recording surface as the responder tests, minimal here."""

    def __init__(self):
        self.fail_typing = 0  # 0 = succeed; otherwise a REST status int
        self.sent = []
        self.typings = []
        self.channels_queried = []
        self.callbacks = []  # (interaction_id, payload)
        self.followups = []  # (token, payload)

    def send_message(self, channel_id, content):
        self.sent.append((channel_id, content))
        return {"id": "m"}

    def trigger_typing(self, channel_id):
        if self.fail_typing:
            raise DiscordRestError(int(self.fail_typing), None, "typing refused")
        self.typings.append(channel_id)

    def interaction_callback(self, interaction_id, token, payload):
        self.callbacks.append((interaction_id, payload))

    def followup(self, app_id, token, payload):
        self.followups.append((token, payload))
        return {"id": "f"}

    def get_channel(self, channel_id):
        self.channels_queried.append(channel_id)
        return {"id": channel_id, "name": "general"}


def make_pool(**cfg_kw):
    run_stamp = cfg_kw.pop("run_stamp", None)
    base = dict(
        token="tok",
        app_id="app1",
        allow_guilds=[GUILD],
        allow_users=[USER],
        cooldown_s=0.2,
        max_steps=5,
        tools="calculator",
        queue_max=8,
        max_workers=1,
        backend="fake",
        model="fake-model",
    )
    base.update(cfg_kw)
    cfg = BotConfig(**base)
    policy = Policy(
        bot_user_id="999000111222333444",
        allow_guilds=cfg.allow_guilds,
        allow_users=cfg.allow_users,
        allow_dms=cfg.allow_dms,
        cooldown_s=cfg.cooldown_s,
    )
    rest = FakeRest()
    return ResponderPool(cfg, policy, rest, run_stamp=run_stamp), rest


class TestSubmitInteractionWiring:
    def test_gate_denial_is_silent_and_sync(self, capsys):
        pool, rest = make_pool()
        pool.submit_interaction(inter_payload(user="222"))  # not allowlisted
        assert rest.callbacks == []
        assert "denied interaction" in capsys.readouterr().out

    def test_unknown_command_ignored(self, capsys):
        pool, rest = make_pool()
        pool.submit_interaction(inter_payload(command="frobnicate"))
        assert rest.callbacks == []
        assert "unknown command" in capsys.readouterr().out

    def test_ask_defers_then_enqueues(self):
        pool, rest = make_pool(run_stamp="1700000000")
        pool._inter_defer_and_enqueue(make_inter(command="ask", options={"prompt": "hi bot"}))
        assert rest.callbacks[0][1]["type"] == CALLBACK_DEFER
        assert rest.callbacks[0][1]["data"] == {}  # not ephemeral
        job = pool._queue.get_nowait()
        assert job.prompt == "hi bot"
        assert job.want_think is False
        assert job.interaction is not None
        assert job.ev.is_dm is False
        # fresh mode (default): slash-triggered jobs share the run-stamped key
        assert job.session_key == f"discord-g{GUILD}-c{CHANNEL}-r1700000000"

    def test_ask_ephemeral_defers_with_flag(self):
        pool, rest = make_pool()
        pool._inter_defer_and_enqueue(
            make_inter(command="ask", options={"prompt": "hi", "ephemeral": True})
        )
        assert rest.callbacks[0][1] == {"type": CALLBACK_DEFER, "data": {"flags": EPHEMERAL}}

    def test_ask_empty_prompt_follows_up(self):
        pool, rest = make_pool()
        pool._inter_defer_and_enqueue(make_inter(command="ask", options={"prompt": "  "}))
        assert rest.callbacks[0][1]["type"] == CALLBACK_DEFER
        assert any("empty" in p["content"] for _, p in rest.followups)
        assert pool._queue.empty()

    def test_ask_queue_full_follows_up(self):
        pool, rest = make_pool(queue_max=1)
        pool._queue.put_nowait(
            Job(make_ev_placeholder(), "x", "k", db_mod.resolve_channel_config(CHANNEL, None))
        )
        pool._inter_defer_and_enqueue(make_inter(command="ask", options={"prompt": "hi"}))
        assert any("Queue is full" in p["content"] for _, p in rest.followups)

    def test_think_marks_want_think(self):
        pool, rest = make_pool()
        pool._inter_defer_and_enqueue(make_inter(command="think", options={"prompt": "why"}))
        job = pool._queue.get_nowait()
        assert job.want_think is True

    def test_dm_ask_session_key(self):
        pool, rest = make_pool(run_stamp="1700000000", allow_dms=True)
        pool._inter_defer_and_enqueue(
            make_inter(command="ask", guild=None, channel=DM_CHANNEL, options={"prompt": "yo"})
        )
        job = pool._queue.get_nowait()
        assert job.session_key == f"discord-dm-{USER}-r1700000000"
        assert job.ev.is_dm is True

    def test_status_replies_type4_ephemeral(self):
        pool, rest = make_pool()
        pool.start()
        inter = make_inter(command="status")
        pool._inter_quick(inter, pool._run_status)
        iid, payload = rest.callbacks[0]
        assert iid == "i1"
        assert payload["type"] == CALLBACK_MESSAGE
        assert payload["data"]["flags"] == EPHEMERAL
        assert "fake-model" in payload["data"]["content"]
        pool.stop()

    def test_model_non_owner_denied_no_override(self, capsys):
        pool, rest = make_pool()
        pool._inter_quick(
            make_inter(command="model", user="222", options={"name": "m2"}), pool._run_model
        )
        content = rest.callbacks[0][1]["data"]["content"]
        assert "owners" in content
        assert CHANNEL not in pool._channel_overrides

    def test_model_owner_sets_runtime_override(self):
        pool, rest = make_pool()
        pool._inter_quick(
            make_inter(command="model", options={"name": "qwen3:8b"}), pool._run_model
        )
        content = rest.callbacks[0][1]["data"]["content"]
        assert "qwen3:8b" in content
        assert pool._channel_cfg(CHANNEL).model == "qwen3:8b"

    def test_model_show_does_not_override(self):
        pool, rest = make_pool()
        pool._inter_quick(make_inter(command="model"), pool._run_model)
        assert "fake-model" in rest.callbacks[0][1]["data"]["content"]
        assert CHANNEL not in pool._channel_overrides

    def test_soul_owner_sets_override_and_validates(self, monkeypatch):
        monkeypatch.setattr(db_mod, "_soul_exists", lambda n: n == "kthx-helper")
        pool, rest = make_pool()
        pool._inter_quick(
            make_inter(command="soul", options={"name": "kthx-helper"}), pool._run_soul
        )
        assert pool._channel_cfg(CHANNEL).soul == "kthx-helper"
        pool2, rest2 = make_pool()
        pool2._inter_quick(
            make_inter(command="soul", options={"name": "not-a-soul"}), pool2._run_soul
        )
        assert "Unknown soul" in rest2.callbacks[0][1]["data"]["content"]
        assert CHANNEL not in pool2._channel_overrides

    def test_reset_calls_delete_session(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            PersistentMemory,
            "delete_session",
            staticmethod(lambda session_id, db_path=None: calls.append(session_id) or True),
        )
        pool, rest = make_pool(run_stamp="1700000000")
        pool._inter_quick(make_inter(command="reset"), pool._run_reset)
        # fresh mode (default): /reset clears THIS run's stamped conversation
        assert calls == [f"discord-g{GUILD}-c{CHANNEL}-r1700000000"]
        assert "cleared" in rest.callbacks[0][1]["data"]["content"]

    def test_reset_keep_mode_clears_stable_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            PersistentMemory,
            "delete_session",
            staticmethod(lambda session_id, db_path=None: calls.append(session_id) or True),
        )
        pool, _rest = make_pool(keep_sessions=True)
        pool._inter_quick(make_inter(command="reset"), pool._run_reset)
        assert calls == [f"discord-g{GUILD}-c{CHANNEL}"]

    def test_owners_fallback_to_allow_users(self):
        pool, _ = make_pool()  # no owner_ids -> allow_users are owners
        assert pool._owners == [USER]
        pool2, _ = make_pool(owner_ids=["111"])
        assert pool2._owners == ["111"]


def make_ev_placeholder():
    """Minimal MessageContext for queue-filling (never handled)."""
    from agentkthx.plugins.discord.policy import MessageContext

    return MessageContext(
        message_id="m1",
        channel_id=CHANNEL,
        guild_id=GUILD,
        user_id=USER,
        username="vtstech",
        content="x",
        mentions=(),
    )


# ---------------------------------------------------------------------------
# Reply paths: followup vs channel send, /think reasoning, typing fixes
# ---------------------------------------------------------------------------


class TestReplyPaths:
    def _interaction_job(self, pool, *, command="ask", ephemeral=False, dm=False):
        options = {"prompt": "hi"}
        if ephemeral:
            options["ephemeral"] = True
        inter = make_inter(
            command=command,
            guild=None if dm else GUILD,
            channel=DM_CHANNEL if dm else CHANNEL,
            options=options,
        )
        pool._inter_defer_and_enqueue(inter)
        job = pool._queue.get_nowait()
        return job

    def test_interaction_reply_uses_followup_not_send(self):
        pool, rest = make_pool()
        job = self._interaction_job(pool)
        run = AgentRun(final_answer="answer text")
        pool._send_reply(job, run)
        assert rest.sent == []  # channel send bypassed
        assert len(rest.followups) == 1
        token, payload = rest.followups[0]
        assert token == "itok-i1"
        assert payload == {"content": "answer text"}

    def test_interaction_reply_ephemeral_flagged(self):
        pool, rest = make_pool()
        job = self._interaction_job(pool, ephemeral=True)
        pool._send_reply(job, AgentRun(final_answer="secret"))
        _, payload = rest.followups[0]
        assert payload["flags"] == EPHEMERAL

    def test_message_reply_still_uses_send(self):
        pool, rest = make_pool()
        job = Job(
            make_ev_placeholder(),
            "hello",
            "k",
            db_mod.resolve_channel_config(CHANNEL, None),
        )
        pool._send_reply(job, AgentRun(final_answer="plain"))
        assert rest.sent == [(CHANNEL, "plain")]
        assert rest.followups == []

    def test_think_prepends_reasoning_fence(self):
        pool, rest = make_pool()
        job = self._interaction_job(pool, command="think")
        run = AgentRun(
            final_answer="final answer",
            steps=[
                SimpleNamespace(reasoning_content="step one thought"),
                SimpleNamespace(reasoning_content=""),
                SimpleNamespace(reasoning_content="step two thought"),
            ],
        )
        pool._send_reply(job, run)
        _, payload = rest.followups[0]
        assert payload["content"].startswith("```text\nstep one thought")
        assert "step two thought" in payload["content"]
        assert payload["content"].rstrip().endswith("final answer")

    def test_think_without_reasoning_is_plain(self):
        pool, rest = make_pool()
        job = self._interaction_job(pool, command="think")
        run = AgentRun(final_answer="just the answer")
        pool._send_reply(job, run)
        _, payload = rest.followups[0]
        assert payload["content"] == "just the answer"

    def test_typing_skipped_for_dm_jobs(self, capsys, monkeypatch):
        # Stub the real Agent class: _handle_job must never construct a
        # backend in offline tests.
        monkeypatch.setattr(
            "agentkthx.agent.Agent",
            lambda **kw: SimpleNamespace(run=lambda p: AgentRun(final_answer="ok")),
        )
        pool, rest = make_pool(allow_dms=True)
        job = self._interaction_job(pool, dm=True)
        pool._handle_job(job)
        assert rest.typings == []  # DM channels 404 trigger-typing — skip

    def test_typing_404_quiet_after_first_channel(self, capsys):
        pool, rest = make_pool()
        rest.fail_typing = 404
        pool._typing_loop("chan-a", threading.Event())
        pool._typing_loop("chan-a", threading.Event())  # same channel: no log
        pool._typing_loop("chan-b", threading.Event())  # different channel: logs
        out = capsys.readouterr().out
        assert out.count("typing unavailable in chan-a") == 1
        assert "typing unavailable in chan-b" in out

    def test_typing_403_still_logged(self, capsys):
        pool, rest = make_pool()
        rest.fail_typing = 403
        pool._typing_loop("chan-a", threading.Event())
        assert "typing refresh failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# REST wiring errors stay non-fatal
# ---------------------------------------------------------------------------


class TestInteractionErrorTolerance:
    def test_defer_failure_drops_job(self, capsys):
        pool, rest = make_pool()

        def boom(interaction_id, token, payload):
            raise DiscordRestError(500, None, "boom")

        rest.interaction_callback = boom
        pool._inter_defer_and_enqueue(make_inter(command="ask", options={"prompt": "hi"}))
        assert pool._queue.empty()
        assert "defer failed" in capsys.readouterr().out

    def test_quick_reply_failure_never_raises(self, capsys):
        pool, rest = make_pool()

        def boom(interaction_id, token, payload):
            raise DiscordRestError(500, None, "boom")

        rest.interaction_callback = boom
        pool._inter_quick(make_inter(command="status"), pool._run_status)  # no raise
        assert "interaction reply failed" in capsys.readouterr().out

    def test_followup_failure_never_raises(self, capsys):
        pool, rest = make_pool()

        def boom(app_id, token, payload):
            raise DiscordRestError(500, None, "boom")

        rest.followup = boom
        pool._inter_followup_text(make_inter(command="ask"), "notice")  # no raise
        assert "interaction followup failed" in capsys.readouterr().out
