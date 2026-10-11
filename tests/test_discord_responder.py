"""
AgentKthx — Discord M1 Chat Responder Tests

Offline: the Agent class is stubbed (no inference), REST is a recording
fake, and the gateway is untouched. Covers session identity, per-channel
overrides, TTL pruning, the cooldown race, the worker pipeline end-to-end
(mention -> typing -> envelope -> chunked reply), cooldown/queue-full/
backend-error/empty-trigger paths, tool policy + deny-all confirm gate,
and the real _build_agent wiring against a captured constructor.

Written by VTSTech — https://www.vts-tech.org
"""

import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core.models import AgentRun  # noqa: E402
from agentkthx.core.persistent_memory import PersistentMemory  # noqa: E402
from agentkthx.plugins.discord import discord_bot as db_mod  # noqa: E402
from agentkthx.plugins.discord.discord_bot import (  # noqa: E402
    BotConfig,
    Job,
    ResponderPool,
    _deny_dangerous,
)
from agentkthx.plugins.discord.policy import MessageContext, Policy  # noqa: E402
from agentkthx.plugins.discord.rest import DiscordRestError  # noqa: E402
from agentkthx.plugins.discord.sessions import (  # noqa: E402
    DEFAULT_SESSION_TTL_DAYS,
    ChannelConfig,
    channel_id_from_key,
    is_discord_session,
    prune_discord_sessions,
    resolve_channel_config,
    resolve_soul_allowed_tools,
    session_key_for,
    session_key_with_prefix,
    session_key_with_run_stamp,
)

BOT_ID = "999000111222333444"
GUILD = "1550663536752066570"
CHANNEL = "222333444555666777"
USER = "403985188666867712"
DM_CHANNEL = "333444555666777888"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRest:
    """Records every send/typing/channel/interaction call. Never network."""

    def __init__(self, *, fail_typing=False, fail_channel=False):
        # fail_typing: True -> 403, or an int status (e.g. 404)
        self._typing_status = int(fail_typing) if fail_typing else 0
        self.sent: list[tuple[str, str]] = []
        self.typings: list[str] = []
        self.channels_queried: list[str] = []
        self.callbacks: list[tuple[str, dict]] = []  # (interaction_id, payload)
        self.followups: list[tuple[str, dict]] = []  # (token, payload)
        self._fail_channel = fail_channel

    def send_message(self, channel_id: str, content: str):
        self.sent.append((channel_id, content))
        return {"id": "m"}

    def trigger_typing(self, channel_id: str) -> None:
        if self._typing_status:
            raise DiscordRestError(self._typing_status, None, "typing refused")
        self.typings.append(channel_id)

    def interaction_callback(self, interaction_id: str, token: str, payload: dict) -> None:
        self.callbacks.append((interaction_id, payload))

    def followup(self, app_id: str, token: str, payload: dict) -> dict:
        self.followups.append((token, payload))
        return {"id": "f"}

    def get_channel(self, channel_id: str) -> dict:
        self.channels_queried.append(channel_id)
        if self._fail_channel:
            raise DiscordRestError(403, None, "no access")
        return {"id": channel_id, "name": "general"}


class FakeAgent:
    def __init__(self, answer="the answer", *, fail=None, delay=0.0, success=True):
        self.answer = answer
        self.fail = fail
        self.delay = delay
        self.success = success
        self.prompts: list[str] = []
        self.lock = threading.Lock()

    def run(self, prompt: str) -> AgentRun:
        with self.lock:
            self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return AgentRun(final_answer=self.answer, success=self.success)


class AgentCapture:
    """Stands in for the real Agent class; records constructor kwargs."""

    instances: list[dict] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        AgentCapture.instances.append(kwargs)

    def run(self, prompt: str) -> AgentRun:
        return AgentRun(final_answer="ok")


def make_cfg(**kw) -> BotConfig:
    base = dict(
        token="tok",
        app_id="",
        allow_guilds=[GUILD],
        allow_users=[USER],
        cooldown_s=0.2,
        max_reply_msgs=3,
        max_steps=5,
        tools="calculator,shell,python_repl",
        queue_max=8,
        max_workers=1,
        backend="fake",
        model="fake-model",
    )
    base.update(kw)
    return BotConfig(**base)


def make_policy(**kw) -> Policy:
    base = dict(
        allow_guilds=[GUILD],
        allow_users=[USER],
        cooldown_s=0.2,
    )
    base.update(kw)
    return Policy(bot_user_id=BOT_ID, **base)


def make_ev(content=f"<@{BOT_ID}> hello", **kw) -> MessageContext:
    base = dict(
        message_id="m1",
        channel_id=CHANNEL,
        guild_id=GUILD,
        user_id=USER,
        username="vtstech",
        content=content,
        mentions=(BOT_ID,),
    )
    base.update(kw)
    return MessageContext(**base)


def make_pool(cfg=None, policy=None, rest=None, agent=None, run_stamp=None):
    cfg = cfg or make_cfg()
    policy = policy or make_policy()
    rest = rest or FakeRest()
    agent = agent or FakeAgent()
    pool = ResponderPool(cfg, policy, rest, run_stamp=run_stamp)
    built: list[Job] = []
    pool._build_agent = lambda job: (built.append(job), agent)[1]  # shadows method
    return pool, rest, agent, built


def wait_until(fn, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


# ---------------------------------------------------------------------------
# Session identity (sessions.py)
# ---------------------------------------------------------------------------


class TestSessionKeys:
    def test_guild_key(self):
        assert session_key_for(GUILD, CHANNEL, USER) == f"discord-g{GUILD}-c{CHANNEL}"

    def test_dm_key(self):
        assert session_key_for(None, DM_CHANNEL, USER) == f"discord-dm-{USER}"

    def test_channel_id_from_key(self):
        assert channel_id_from_key(f"discord-g{GUILD}-c{CHANNEL}") == CHANNEL
        assert channel_id_from_key(f"discord-dm-{USER}") is None

    def test_channel_id_from_key_tolerates_suffixes(self):
        """Run stamp / prefix decorations don't break channel extraction."""
        key = f"discord-g{GUILD}-c{CHANNEL}-psupport-r1791675399"
        assert channel_id_from_key(key) == CHANNEL

    def test_run_stamp_helper(self):
        base = f"discord-g{GUILD}-c{CHANNEL}"
        assert session_key_with_run_stamp(base, "1791675399") == base + "-r1791675399"
        assert session_key_with_run_stamp(base, None) == base
        assert session_key_with_run_stamp(base, "") == base  # falsy = --keep

    def test_default_ttl_lowered_to_seven_days(self):
        """R07.33: 30d was 'a lot' — fresh-on-restart leaves one row per run
        behind, so the store self-cleans after a week of inactivity."""
        assert DEFAULT_SESSION_TTL_DAYS == 7

    def test_is_discord_session(self):
        assert is_discord_session("discord-g1-c2")
        assert is_discord_session("discord-dm-5")
        assert not is_discord_session("cli-default")

    def test_prefix(self):
        cfg = resolve_channel_config(CHANNEL, None)
        base = session_key_for(GUILD, CHANNEL, USER)
        assert session_key_with_prefix(base, cfg) == base
        assert session_key_with_prefix(base, ChannelConfigFix()) == base + "-psupport"

    def test_prefix_applied_in_job(self, tmp_path):
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"session_prefix": "support"}}}' % CHANNEL,
            encoding="utf-8",
        )
        cfg = make_cfg(discord_json=str(path))
        pool = ResponderPool(cfg, make_policy(), FakeRest(), run_stamp="1700000000")
        pool.submit_event(make_ev())
        job = pool._queue.get_nowait()
        assert job.session_key.endswith("-psupport-r1700000000")

    def test_fresh_default_scopes_keys_to_the_run(self):
        """R07.33 default: each pool (bot run) gets its own session keys, so a
        restart starts new conversations per channel."""
        base = f"discord-g{GUILD}-c{CHANNEL}"
        pool_a, _ra, _aa, _ba = make_pool(run_stamp="1111111111")
        pool_b, _rb, _ab, _bb = make_pool(run_stamp="2222222222")
        pool_a.submit_event(make_ev(message_id="a1"))
        pool_b.submit_event(make_ev(message_id="b1"))
        key_a = pool_a._queue.get_nowait().session_key
        key_b = pool_b._queue.get_nowait().session_key
        assert key_a == base + "-r1111111111"
        assert key_b == base + "-r2222222222"
        assert key_a != key_b

    def test_keep_mode_restores_stable_keys(self):
        """--keep / DISCORD_KEEP_SESSIONS=true: no run stamp, history resumes
        across restarts (pre-R07.33 behavior)."""
        base = f"discord-g{GUILD}-c{CHANNEL}"
        pool, _r, _a, _b = make_pool(cfg=make_cfg(keep_sessions=True))
        pool.submit_event(make_ev())
        assert pool._run_stamp is None
        assert pool._queue.get_nowait().session_key == base
        # an explicit run_stamp is ignored in keep mode
        pool_k = ResponderPool(make_cfg(keep_sessions=True), make_policy(), FakeRest())
        assert pool_k._run_stamp is None


class ChannelConfigFix:
    """Standalone ChannelConfig with a prefix (avoids import alias noise)."""

    def __init__(self):
        from agentkthx.plugins.discord.sessions import ChannelConfig

        self._c = ChannelConfig(session_prefix="support")

    def __getattr__(self, item):
        return getattr(self._c, item)


class TestChannelConfig:
    def test_missing_file(self, tmp_path):
        cfg = resolve_channel_config(CHANNEL, str(tmp_path / "nope.json"))
        assert (cfg.soul, cfg.tools, cfg.session_prefix) == (None, None, None)

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "discord.json"
        path.write_text("{not json", encoding="utf-8")
        cfg = resolve_channel_config(CHANNEL, str(path))
        assert cfg.soul is None

    def test_channel_entry(self, tmp_path):
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"soul": "kthx-trading", '
            '"tools": ["calculator", "todo"], "session_prefix": "support"}}}' % CHANNEL,
            encoding="utf-8",
        )
        cfg = resolve_channel_config(CHANNEL, str(path))
        assert cfg.soul == "kthx-trading"
        assert cfg.tools == ["calculator", "todo"]
        assert cfg.session_prefix == "support"

    def test_other_channel_ignored(self, tmp_path):
        path = tmp_path / "discord.json"
        path.write_text('{"channels": {"1": {"soul": "x"}}}', encoding="utf-8")
        assert resolve_channel_config(CHANNEL, str(path)).soul is None


class TestPruneSessions:
    def _seed(self, db_path: str) -> None:
        sqlite3.connect(db_path).close()  # touch file so _init_db runs
        PersistentMemory.list_sessions(db_path)  # ensures schema exists
        rows = [
            ("discord-g1-c1", "2020-01-01 00:00:00"),  # stale -> pruned
            ("discord-g1-c2", "2100-01-01 00:00:00"),  # fresh -> kept
            ("cli-local-1", "2020-01-01 00:00:00"),  # not ours -> kept
            ("discord-dm-9", "garbage-ts"),  # unparseable -> kept
        ]
        conn = sqlite3.connect(db_path)
        try:
            for sid, ts in rows:
                conn.execute(
                    "INSERT INTO sessions (session_id, model, created_at, "
                    "updated_at, message_count, metadata) VALUES (?,?,?,?,?,?)",
                    (sid, "m", ts, ts, 2, "{}"),
                )
            conn.commit()
        finally:
            conn.close()

    def test_prunes_only_stale_discord_sessions(self, tmp_path):
        db = str(tmp_path / "mem.db")
        self._seed(db)
        deleted = prune_discord_sessions(30, db_path=db)
        assert deleted == ["discord-g1-c1"]
        remaining = {r["session_id"] for r in PersistentMemory.list_sessions(db)}
        assert remaining == {"discord-g1-c2", "cli-local-1", "discord-dm-9"}

    def test_ttl_zero_disables(self, tmp_path):
        db = str(tmp_path / "mem.db")
        self._seed(db)
        assert prune_discord_sessions(0, db_path=db) == []
        assert len(PersistentMemory.list_sessions(db)) == 4

    def test_bad_db_path_is_safe(self, tmp_path):
        assert prune_discord_sessions(30, db_path=str(tmp_path / "no-dir" / "x.db")) == []


class TestSoulTools:
    def test_default_soul_resolves_or_none(self):
        result = resolve_soul_allowed_tools("kthx-helper")
        assert result is None or isinstance(result, list)
        if isinstance(result, list):
            assert "calculator" in result

    def test_unknown_soul_is_none(self):
        assert resolve_soul_allowed_tools("soul-does-not-exist-xyz") is None

    def test_empty_is_none(self):
        assert resolve_soul_allowed_tools(None) is None

    def test_deny_dangerous(self, capsys):
        assert _deny_dangerous("shell", {}) is False
        assert "denied dangerous tool shell" in capsys.readouterr().out


class TestCooldownRace:
    def test_concurrent_checks_allow_one(self):
        policy = make_policy(cooldown_s=5.0)
        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def probe():
            barrier.wait()
            ok = policy.check_rate("u1").allowed
            with lock:
                results.append(ok)

        threads = [threading.Thread(target=probe) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1  # exactly one run admitted


# ---------------------------------------------------------------------------
# Envelope + labels
# ---------------------------------------------------------------------------


class TestEnvelope:
    def _pool_with_cache(self, rest=None):
        rest = rest or FakeRest()
        pool = ResponderPool(make_cfg(), make_policy(), rest)
        return pool, rest

    def test_guild_envelope(self):
        pool, rest = self._pool_with_cache()
        job = Job(
            make_ev(),
            "hello",
            f"discord-g{GUILD}-c{CHANNEL}",
            resolve_channel_config(CHANNEL, None),
        )
        assert pool._build_envelope(job) == (
            f"[Discord] guild={GUILD} channel=#general user=vtstech\nhello"
        )
        assert rest.channels_queried == [CHANNEL]  # name fetched once

    def test_label_cached(self):
        pool, rest = self._pool_with_cache()
        pool._channel_label(CHANNEL)
        pool._channel_label(CHANNEL)
        assert rest.channels_queried == [CHANNEL]

    def test_label_fallback_on_error(self):
        pool, rest = self._pool_with_cache(rest=FakeRest(fail_channel=True))
        assert pool._channel_label(CHANNEL) == f"#{CHANNEL}"

    def test_dm_envelope(self):
        pool, rest = self._pool_with_cache()
        job = Job(
            make_ev(guild_id=None, channel_id=DM_CHANNEL, content=f"<@{BOT_ID}> hi"),
            "hi",
            f"discord-dm-{USER}",
            resolve_channel_config(DM_CHANNEL, None),
        )
        assert pool._build_envelope(job).startswith("[Discord] guild=dm channel=dm user=vtstech\n")
        assert rest.channels_queried == []  # DMs skip the REST name lookup


# ---------------------------------------------------------------------------
# Worker pipeline (end-to-end with stubbed Agent)
# ---------------------------------------------------------------------------


class TestResponderEndToEnd:
    def test_mention_gets_answer(self):
        pool, rest, agent, built = make_pool(run_stamp="1700000000")
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent), "no reply sent"
            channel_id, text = rest.sent[-1]
            assert channel_id == CHANNEL
            assert text == "the answer"
            assert rest.typings, "typing indicator never triggered"
            assert len(built) == 1
            assert built[0].session_key == f"discord-g{GUILD}-c{CHANNEL}-r1700000000"
            assert agent.prompts[0].startswith("[Discord] guild=")
            assert "hello" in agent.prompts[0]
            assert pool.agent_runs == 1
        finally:
            pool.stop()

    def test_debug_echoes_pipeline(self, capsys):
        """--debug: [discord:debug] lines carry run start, envelope, outcome."""
        pool, rest, agent, built = make_pool(cfg=make_cfg(debug=True))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent), "no reply sent"
        finally:
            pool.stop()
        out = capsys.readouterr().out
        assert "[discord:debug] run start user=vtstech" in out
        assert "envelope:" in out
        assert "[Discord] guild=" in out
        assert "[discord:debug] run done success=True" in out

    def test_debug_echo_silent_when_off(self, capsys):
        pool, rest, agent, built = make_pool()
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent), "no reply sent"
        finally:
            pool.stop()
        assert "[discord:debug]" not in capsys.readouterr().out

    def test_second_rapid_mention_held_by_cooldown(self):
        cfg = make_cfg(cooldown_s=0.5, max_workers=1)
        pool, rest, agent, built = make_pool(cfg=cfg, agent=FakeAgent(delay=0.15))
        pool.start()
        try:
            pool.submit_event(make_ev(message_id="m1"))
            pool.submit_event(make_ev(message_id="m2"))
            assert wait_until(lambda: len(rest.sent) >= 2)
            time.sleep(0.1)  # let both jobs settle
            texts = [t for _, t in rest.sent]
            assert sum("Rate limited" in t for t in texts) == 1
            assert sum(t == "the answer" for t in texts) == 1
            assert len(agent.prompts) == 1  # no double agent run (M1 AC)
        finally:
            pool.stop()

    def test_queue_full_path(self):
        cfg = make_cfg(queue_max=1)
        pool, rest, _, _ = make_pool(cfg=cfg)
        pool._queue.put_nowait(
            Job(make_ev(message_id="m0"), "x", "k", resolve_channel_config(CHANNEL, None))
        )
        pool.submit_event(make_ev())  # queue already at maxsize
        assert any("Queue is full" in t for _, t in rest.sent)

    def test_backend_error_one_liner(self):
        pool, rest, agent, _ = make_pool(agent=FakeAgent(fail=ValueError("boom")))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent)
            assert "Backend error: ValueError" in rest.sent[-1][1]
            assert "the answer" not in [t for _, t in rest.sent]
        finally:
            pool.stop()

    def test_max_steps_marker(self):
        pool, rest, _, _ = make_pool(agent=FakeAgent(answer="partial", success=False))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent)
            text = rest.sent[-1][1]
            assert "partial" in text
            assert "(incomplete — maximum steps reached)" in text
        finally:
            pool.stop()

    def test_long_reply_chunked(self):
        pool, rest, _, _ = make_pool(agent=FakeAgent(answer="x" * 7000))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: len(rest.sent) >= 3)
            assert len(rest.sent) == 3  # max_reply_msgs=3
            assert all(len(t) <= 2000 for _, t in rest.sent)
            assert "truncated" in rest.sent[-1][1]
        finally:
            pool.stop()

    def test_typing_refreshed_during_run(self, monkeypatch):
        monkeypatch.setattr(db_mod, "TYPING_REFRESH_S", 0.05)
        pool, rest, _, _ = make_pool(agent=FakeAgent(delay=0.25))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent)
            assert len(rest.typings) >= 2  # initial + refresh(es)
        finally:
            pool.stop()

    def test_typing_failure_does_not_kill_run(self):
        pool, rest, _, _ = make_pool(rest=FakeRest(fail_typing=True))
        pool.start()
        try:
            pool.submit_event(make_ev())
            assert wait_until(lambda: rest.sent)
            assert rest.sent[-1][1] == "the answer"
        finally:
            pool.stop()

    def test_empty_bare_ping_ignored(self):
        pool, rest, agent, built = make_pool()
        pool.submit_event(make_ev(message_id="m9", content=f"<@{BOT_ID}>"))
        assert pool._queue.empty()
        assert rest.sent == []
        assert built == []

    def test_unlisted_guild_denied(self, capsys):
        pool, rest, _, built = make_pool()
        pool.submit_event(make_ev(guild_id="666"))
        assert pool._queue.empty()
        assert "guild-not-allowed" in capsys.readouterr().out

    def test_dry_run_prints_only(self, capsys):
        cfg = make_cfg(dry_run=True)
        pool, rest, _, built = make_pool(cfg=cfg)
        pool.submit_event(make_ev())
        out = capsys.readouterr().out
        assert "DRY-RUN would answer" in out
        assert pool._queue.empty()
        assert rest.sent == []
        assert built == []


# ---------------------------------------------------------------------------
# Real agent construction (captured constructor, no inference)
# ---------------------------------------------------------------------------


class TestBuildAgent:
    def test_wiring_defaults(self, monkeypatch):
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        pool = ResponderPool(make_cfg(), make_policy(), FakeRest())
        job = Job(
            make_ev(),
            "hello",
            f"discord-g{GUILD}-c{CHANNEL}",
            resolve_channel_config(CHANNEL, None),
        )
        agent = pool._build_agent(job)
        kw = agent.kwargs
        assert kw["model"] == "fake-model"
        assert kw["backend"] == "fake"
        assert kw["tools"] == ["calculator"]  # shell/python_repl excluded by policy
        assert kw["soul"] is None  # R07.33: no soul unless opted in
        assert kw["session_id"] == f"discord-g{GUILD}-c{CHANNEL}"
        assert kw["max_steps"] == 5
        assert kw["confirm_dangerous"] is _deny_dangerous

    def test_channel_soul_override(self, monkeypatch, tmp_path):
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"soul": "kthx-trading"}}}' % CHANNEL,
            encoding="utf-8",
        )
        cfg = make_cfg(discord_json=str(path))
        pool = ResponderPool(cfg, make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        kw = AgentCapture.instances[-1]
        assert kw["soul"] == "kthx-trading"  # channel override beats default
        assert kw["tools"] == ["calculator"]  # default tools minus exclusions

    def test_channel_tools_override(self, monkeypatch, tmp_path):
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"tools": ["todo"]}}}' % CHANNEL,
            encoding="utf-8",
        )
        pool = ResponderPool(make_cfg(discord_json=str(path)), make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["tools"] == ["todo"]

    def test_soul_constraint_intersects(self, monkeypatch, tmp_path):
        """kthx-helper allows todo/calculator/shell/http_get/python_repl/
        parse_json — 'web_search' in DISCORD_TOOLS must be dropped, and
        shell/python_repl dropped by the Discord exclusion (stricter wins)."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"soul": "kthx-helper"}}}' % CHANNEL,
            encoding="utf-8",
        )
        cfg = make_cfg(tools="calculator,web_search,shell", discord_json=str(path))
        pool = ResponderPool(cfg, make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["tools"] == ["calculator"]

    def test_no_soul_keeps_full_tool_list(self, monkeypatch):
        """R07.33 default: soul=None constrains nothing — the tool list is
        only narrowed by the Discord exclusion (shell/python_repl)."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        cfg = make_cfg(tools="calculator,web_search,shell")
        pool = ResponderPool(cfg, make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, None))
        pool._build_agent(job)
        kw = AgentCapture.instances[-1]
        assert kw["soul"] is None
        assert kw["tools"] == ["calculator", "web_search"]

    def test_channel_model_override(self, monkeypatch, tmp_path):
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"model": "qwen3:8b"}}}' % CHANNEL,
            encoding="utf-8",
        )
        pool = ResponderPool(
            make_cfg(discord_json=str(path), model="cfg-model"), make_policy(), FakeRest()
        )
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["model"] == "qwen3:8b"  # channel wins

    def test_env_file_override_flag_precedence(self):
        """--tools flag (already merged into cfg.tools by cmd_discord) wins
        over channel defaults when no per-channel override exists."""
        cfg = make_cfg(tools="todo")
        pool = ResponderPool(cfg, make_policy(), FakeRest())
        assert pool._build_agent.__self__.cfg.tools == "todo"

    def test_no_tools_by_default(self, monkeypatch):
        """R07.33: the default config carries no tools — the responder
        answers chat directly instead of burning the step budget on tool
        rounds ('maximum steps reached')."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        pool = ResponderPool(make_cfg(tools=""), make_policy(), FakeRest())
        job = Job(
            make_ev(),
            "hello",
            f"discord-g{GUILD}-c{CHANNEL}",
            resolve_channel_config(CHANNEL, None),
        )
        pool._build_agent(job)
        kw = AgentCapture.instances[-1]
        assert kw["tools"] == []
        assert kw["soul"] is None

    def test_soul_does_not_grant_tools(self, monkeypatch, tmp_path):
        """A soul's allowedTools never re-enables tools on its own — an
        explicit DISCORD_TOOLS / channel 'tools' opt-in is required too."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"soul": "kthx-helper"}}}' % CHANNEL,
            encoding="utf-8",
        )
        pool = ResponderPool(make_cfg(tools="", discord_json=str(path)), make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        kw = AgentCapture.instances[-1]
        assert kw["soul"] == "kthx-helper"
        assert kw["tools"] == []

    def test_channel_tools_override_opts_in(self, monkeypatch, tmp_path):
        """A per-channel 'tools' override is itself the opt-in — it works
        even though the global default is no-tools."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        path = tmp_path / "discord.json"
        path.write_text(
            '{"channels": {"%s": {"tools": ["calculator", "todo"]}}}' % CHANNEL,
            encoding="utf-8",
        )
        pool = ResponderPool(make_cfg(tools="", discord_json=str(path)), make_policy(), FakeRest())
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, str(path)))
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["tools"] == ["calculator", "todo"]

    def test_from_env_defaults_to_no_tools(self, monkeypatch):
        """BotConfig.from_env: DISCORD_TOOLS unset resolves to '' (manifest
        default), and 'none' is honored when set."""
        monkeypatch.delenv("DISCORD_TOOLS", raising=False)
        assert BotConfig.from_env().tools == ""
        monkeypatch.setenv("DISCORD_TOOLS", "none")
        assert BotConfig.from_env().tools == "none"

    def test_build_agent_forwards_debug(self, monkeypatch):
        """--debug reaches the core Agent (chat --debug machinery)."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, None))
        pool = ResponderPool(make_cfg(debug=True), make_policy(), FakeRest())
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["debug"] is True
        pool_off = ResponderPool(make_cfg(), make_policy(), FakeRest())
        pool_off._build_agent(job)
        assert AgentCapture.instances[-1]["debug"] is False

    def test_from_env_debug_flag_and_override_semantics(self, monkeypatch):
        """DISCORD_DEBUG env enables; the --debug flag wins only when raised
        (None = flag absent keeps the env value)."""
        monkeypatch.delenv("DISCORD_DEBUG", raising=False)
        assert BotConfig.from_env().debug is False
        monkeypatch.setenv("DISCORD_DEBUG", "true")
        assert BotConfig.from_env().debug is True
        assert BotConfig.from_env(overrides={"debug": None}).debug is True
        assert BotConfig.from_env(overrides={"debug": True}).debug is True
        monkeypatch.setenv("DISCORD_DEBUG", "false")
        assert BotConfig.from_env(overrides={"debug": True}).debug is True
        assert BotConfig.from_env().debug is False

    def test_from_env_fresh_sessions_by_default(self, monkeypatch):
        """R07.33: keep_sessions defaults False (fresh conversations on every
        restart); DISCORD_KEEP_SESSIONS=true opts back into history resume;
        session TTL default dropped 30 -> 7 days."""
        monkeypatch.delenv("DISCORD_KEEP_SESSIONS", raising=False)
        monkeypatch.delenv("DISCORD_SESSION_TTL_DAYS", raising=False)
        cfg = BotConfig.from_env()
        assert cfg.keep_sessions is False
        assert cfg.session_ttl_days == 7
        monkeypatch.setenv("DISCORD_KEEP_SESSIONS", "true")
        assert BotConfig.from_env().keep_sessions is True
        monkeypatch.setenv("DISCORD_KEEP_SESSIONS", "false")
        assert BotConfig.from_env().keep_sessions is False
        monkeypatch.setenv("DISCORD_SESSION_TTL_DAYS", "30")
        assert BotConfig.from_env().session_ttl_days == 30

    def test_keep_flag_and_override_semantics(self, monkeypatch):
        """--keep flows through overrides with the same None-skip semantics as
        --debug: the flag wins only when raised, env alone works."""
        monkeypatch.delenv("DISCORD_KEEP_SESSIONS", raising=False)
        assert BotConfig.from_env().keep_sessions is False
        assert BotConfig.from_env(overrides={"keep_sessions": None}).keep_sessions is False
        assert BotConfig.from_env(overrides={"keep_sessions": True}).keep_sessions is True
        monkeypatch.setenv("DISCORD_KEEP_SESSIONS", "true")
        assert BotConfig.from_env(overrides={"keep_sessions": None}).keep_sessions is True

    def test_from_env_num_ctx_and_max_tokens_defaults(self, monkeypatch):
        """Chat-parity gen params default to None — the Agent keeps its
        built-ins (num_ctx 8192, max_tokens = num_ctx//32 cap)."""
        monkeypatch.delenv("DISCORD_NUM_CTX", raising=False)
        monkeypatch.delenv("DISCORD_MAX_TOKENS", raising=False)
        cfg = BotConfig.from_env()
        assert cfg.num_ctx is None
        assert cfg.max_tokens is None

    def test_from_env_num_ctx_and_max_tokens_parse(self, monkeypatch):
        """Plain ints and R07.18 human suffixes (128k/2k) both parse, exactly
        like `agentkthx chat`; a malformed value is reported and ignored."""
        monkeypatch.setenv("DISCORD_NUM_CTX", "32768")
        monkeypatch.setenv("DISCORD_MAX_TOKENS", "4096")
        cfg = BotConfig.from_env()
        assert cfg.num_ctx == 32768
        assert cfg.max_tokens == 4096
        monkeypatch.setenv("DISCORD_NUM_CTX", "128k")
        monkeypatch.setenv("DISCORD_MAX_TOKENS", "2k")
        cfg = BotConfig.from_env()
        assert cfg.num_ctx == 131072
        assert cfg.max_tokens == 2048
        monkeypatch.setenv("DISCORD_NUM_CTX", "bananas")
        cfg = BotConfig.from_env()
        assert cfg.num_ctx is None

    def test_num_ctx_max_tokens_flag_override_semantics(self, monkeypatch):
        """--num-ctx/--max-tokens flow through overrides with the same
        None-skip semantics as --debug/--keep: flag wins when raised,
        env alone works."""
        monkeypatch.delenv("DISCORD_NUM_CTX", raising=False)
        monkeypatch.delenv("DISCORD_MAX_TOKENS", raising=False)
        cfg = BotConfig.from_env(overrides={"num_ctx": None, "max_tokens": None})
        assert cfg.num_ctx is None
        assert cfg.max_tokens is None
        cfg = BotConfig.from_env(overrides={"num_ctx": 65536, "max_tokens": 8192})
        assert cfg.num_ctx == 65536
        assert cfg.max_tokens == 8192

    def test_build_agent_forwards_num_ctx_and_max_tokens(self, monkeypatch):
        """--num-ctx/--max-tokens reach the core Agent as num_ctx/num_predict
        (chat parity); an explicit num_predict skips the num_ctx//32 cap that
        produced the 256-token truncation on Discord."""
        monkeypatch.setattr("agentkthx.agent.Agent", AgentCapture)
        AgentCapture.instances.clear()
        job = Job(make_ev(), "hello", "k", resolve_channel_config(CHANNEL, None))
        pool = ResponderPool(make_cfg(num_ctx=32768, max_tokens=4096), make_policy(), FakeRest())
        pool._build_agent(job)
        assert AgentCapture.instances[-1]["num_ctx"] == 32768
        assert AgentCapture.instances[-1]["num_predict"] == 4096
        pool_def = ResponderPool(make_cfg(), make_policy(), FakeRest())
        pool_def._build_agent(job)
        assert AgentCapture.instances[-1]["num_ctx"] is None
        assert AgentCapture.instances[-1]["num_predict"] is None


class TestToolsDisplay:
    """Banner / /status rendering helper."""

    def test_falsy_and_off_variants_render_none(self):
        assert db_mod._tools_display(None) == "none"
        assert db_mod._tools_display("") == "none"
        assert db_mod._tools_display("none") == "none"
        assert db_mod._tools_display(" OFF ") == "none"

    def test_enabled_list_renders_verbatim(self):
        assert db_mod._tools_display("calculator,web_search") == "calculator,web_search"
        assert db_mod._tools_display(["todo", "calculator"]) == "todo, calculator"


class TestStatusToolsDisplay:
    """/status reflects the effective tool setting, not the raw string."""

    def test_status_shows_none_when_disabled(self):
        pool = ResponderPool(make_cfg(tools=""), make_policy(), FakeRest())
        inter = db_mod.Interaction(
            interaction_id="i1",
            token="itok-x",
            app_id="app1",
            guild_id=GUILD,
            channel_id=CHANNEL,
            user_id=USER,
            username="vtstech",
            command="status",
        )
        content, ephemeral = pool._run_status(inter)
        assert "tools: none" in content
        assert ephemeral is True

    def test_status_shows_channel_opt_in(self):
        pool = ResponderPool(make_cfg(tools=""), make_policy(), FakeRest())
        pool._channel_overrides[CHANNEL] = ChannelConfig(tools=["todo"])
        inter = db_mod.Interaction(
            interaction_id="i2",
            token="itok-x",
            app_id="app1",
            guild_id=GUILD,
            channel_id=CHANNEL,
            user_id=USER,
            username="vtstech",
            command="status",
        )
        content, _ = pool._run_status(inter)
        assert "tools: todo" in content


# ---------------------------------------------------------------------------
# Shutdown hook
# ---------------------------------------------------------------------------


class TestShutdownHook:
    def test_on_shutdown_stops_pool(self, monkeypatch, capsys):
        pool, rest, _, _ = make_pool()
        pool.start()
        monkeypatch.setattr(db_mod, "_ACTIVE_POOL", pool)
        monkeypatch.setattr(db_mod, "_ACTIVE_GATEWAY", None)
        db_mod.on_shutdown({})
        assert pool._stop.is_set()
        assert "responder pool stopped" in capsys.readouterr().out

    def test_stop_reports_dropped_jobs(self, capsys):
        pool, _, _, _ = make_pool(cfg=make_cfg())
        pool._queue.put_nowait(Job(make_ev(), "x", "k", resolve_channel_config(CHANNEL, None)))
        pool.stop()
        assert "1 queued job(s) dropped" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Offline safety
# ---------------------------------------------------------------------------


def test_responder_module_never_opens_sockets():
    """GatewayClient/sockets are cmd_discord's job — the pool module must
    not import socket machinery at module level (plugin spec: lazy)."""
    src = Path(db_mod.__file__).read_text(encoding="utf-8")
    for banned in ("import socket", "import ssl", "urllib.request"):
        assert banned not in src
