"""
AgentKthx — Discord Policy Gatekeeper Tests (M0)

Trigger matrix, deny-by-default allowlists, cooldown math, prompt
sanitization, and the Discord tool filter. Pure logic — no network,
no Discord. See docs/DISCORD_PLUGIN_PLAN.md §7.3, §8, §12.

Written by VTSTech — https://www.vts-tech.org
"""

import pytest

from agentkthx.plugins.discord.policy import (
    Decision,
    MessageContext,
    Policy,
    context_from_payload,
)

BOT_ID = "999"


def ctx(**overrides) -> MessageContext:
    base = dict(
        message_id="m1",
        channel_id="c1",
        guild_id="g1",
        user_id="111",
        username="alice",
        content="hello bot",
        mentions=(BOT_ID,),
        reference_author_id=None,
        author_bot=False,
        is_dm=False,
    )
    base.update(overrides)
    return MessageContext(**base)


def make_policy(**overrides) -> Policy:
    kwargs = dict(
        bot_user_id=BOT_ID,
        allow_guilds=["g1"],
        allow_channels=[],
        allow_users=[],
        allow_dms=False,
        cooldown_s=10.0,
        max_prompt_chars=100,
    )
    kwargs.update(overrides)
    return Policy(**kwargs)


# ---------------------------------------------------------------------------
# context_from_payload
# ---------------------------------------------------------------------------


class TestContextFromPayload:
    def test_guild_message_full_mapping(self):
        d = {
            "id": "m42",
            "channel_id": "777",
            "guild_id": "555",
            "author": {"id": "111", "username": "alice", "bot": False},
            "content": "yo <@999>",
            "mentions": [{"id": "999"}],
            "referenced_message": {"author": {"id": "999"}},
        }
        ev = context_from_payload(d)
        assert ev.message_id == "m42"
        assert ev.channel_id == "777"
        assert ev.guild_id == "555"
        assert ev.user_id == "111"
        assert ev.content == "yo <@999>"
        assert ev.mentions == ("999",)
        assert ev.reference_author_id == "999"
        assert ev.is_dm is False
        assert ev.author_bot is False

    def test_dm_has_no_guild(self):
        d = {"id": "m1", "channel_id": "dm1", "author": {"id": "111"}, "content": "hi"}
        ev = context_from_payload(d)
        assert ev.is_dm is True
        assert ev.guild_id is None

    def test_webhook_counts_as_bot(self):
        d = {
            "id": "m1",
            "channel_id": "c",
            "guild_id": "g",
            "author": {"id": "222", "username": "hook"},
            "content": "x",
            "webhook_id": "333",
        }
        assert context_from_payload(d).author_bot is True

    def test_missing_fields_tolerated(self):
        ev = context_from_payload({})
        assert ev.message_id == ""
        assert ev.mentions == ()
        assert ev.reference_author_id is None


# ---------------------------------------------------------------------------
# check_event — the trigger matrix
# ---------------------------------------------------------------------------


class TestTriggerMatrix:
    @pytest.mark.parametrize(
        "overrides,expected_allowed",
        [
            ({"mentions": (BOT_ID,)}, True),  # @mention
            ({"mentions": (), "reference_author_id": BOT_ID}, True),  # reply to bot
            ({"mentions": (), "reference_author_id": "111"}, False),  # reply to human
            ({"mentions": ()}, False),  # plain message
        ],
    )
    def test_guild_triggers(self, overrides, expected_allowed):
        policy = make_policy()
        decision = policy.check_event(ctx(**overrides))
        assert decision.allowed is expected_allowed

    def test_self_message_denied(self):
        decision = make_policy().check_event(ctx(user_id=BOT_ID))
        assert decision.allowed is False
        assert decision.reason == "self-message"

    def test_bot_author_denied(self):
        decision = make_policy().check_event(ctx(author_bot=True))
        assert decision.allowed is False
        assert decision.reason == "bot-author"

    def test_dm_denied_by_default(self):
        decision = make_policy().check_event(ctx(is_dm=True, guild_id=None))
        assert decision.allowed is False
        assert decision.reason == "dms-disabled"

    def test_dm_allowed_when_enabled(self):
        policy = make_policy(allow_dms=True)
        decision = policy.check_event(ctx(is_dm=True, guild_id=None))
        assert decision.allowed is True

    def test_dm_plain_message_triggers_without_mention(self):
        """M1.1: a DM is a 1:1 conversation — every message from an
        allowlisted user triggers, no @mention required (guild messages
        still need one)."""
        policy = make_policy(allow_dms=True)
        decision = policy.check_event(
            ctx(is_dm=True, guild_id=None, mentions=(), content="plain hi")
        )
        assert decision.allowed is True
        assert decision.reason == "ok"

    def test_dm_plain_message_respects_user_allowlist(self):
        policy = make_policy(allow_dms=True, allow_users=["111"])
        ok = policy.check_event(
            ctx(is_dm=True, guild_id=None, mentions=(), user_id="111")
        )
        denied = policy.check_event(
            ctx(is_dm=True, guild_id=None, mentions=(), user_id="222")
        )
        assert ok.allowed is True
        assert not denied.allowed and denied.reason == "user-not-allowed"

    def test_guild_plain_message_still_needs_trigger(self):
        policy = make_policy()
        decision = policy.check_event(ctx(mentions=()))
        assert decision.allowed is False
        assert decision.reason == "no-trigger"

    def test_dm_user_allowlist(self):
        policy = make_policy(allow_dms=True, allow_users=["111"])
        assert policy.check_event(ctx(is_dm=True, guild_id=None)).allowed
        denied = policy.check_event(ctx(is_dm=True, guild_id=None, user_id="222"))
        assert not denied.allowed and denied.reason == "user-not-allowed"


class TestAllowlists:
    def test_empty_guild_allowlist_silences_everything(self):
        policy = make_policy(allow_guilds=[])
        decision = policy.check_event(ctx())
        assert decision.allowed is False
        assert decision.reason == "guild-not-allowed"

    def test_unlisted_guild_denied(self):
        decision = make_policy().check_event(ctx(guild_id="g-other"))
        assert decision.reason == "guild-not-allowed"

    def test_channel_allowlist_narrows(self):
        policy = make_policy(allow_channels=["c-ok"])
        assert policy.check_event(ctx(channel_id="c-ok")).allowed
        denied = policy.check_event(ctx(channel_id="c-nope"))
        assert not denied.allowed and denied.reason == "channel-not-allowed"

    def test_user_allowlist_narrows(self):
        policy = make_policy(allow_users=["111"])
        assert policy.check_event(ctx()).allowed
        denied = policy.check_event(ctx(user_id="222"))
        assert not denied.allowed and denied.reason == "user-not-allowed"

    def test_string_allowlists_normalized(self):
        policy = make_policy(allow_guilds="g1, g2", allow_users="111")
        assert policy.check_event(ctx()).allowed

    def test_deny_by_default_no_bypass_without_trigger(self):
        """Even allowlisted users can't wake the bot without a trigger."""
        policy = make_policy(allow_users=["111"])
        decision = policy.check_event(ctx(mentions=()))
        assert not decision.allowed and decision.reason == "no-trigger"


# ---------------------------------------------------------------------------
# check_rate — cooldowns
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_first_run_allowed_then_cooldown(self):
        policy = make_policy(cooldown_s=10.0)
        assert policy.check_rate("111", now=100.0).allowed
        denied = policy.check_rate("111", now=105.0)
        assert not denied.allowed
        assert denied.reason == "cooldown"
        assert denied.retry_after == pytest.approx(5.0)

    def test_cooldown_expires(self):
        policy = make_policy(cooldown_s=10.0)
        assert policy.check_rate("111", now=100.0).allowed
        assert policy.check_rate("111", now=110.5).allowed

    def test_users_are_independent(self):
        policy = make_policy(cooldown_s=10.0)
        assert policy.check_rate("111", now=100.0).allowed
        assert policy.check_rate("222", now=100.0).allowed

    def test_uses_monotonic_when_now_omitted(self):
        policy = make_policy(cooldown_s=0.0)  # no cooldown -> always allowed
        assert policy.check_rate("111").allowed
        assert policy.check_rate("111").allowed


# ---------------------------------------------------------------------------
# sanitize_prompt
# ---------------------------------------------------------------------------


class TestSanitizePrompt:
    def test_strips_bot_mention_forms(self):
        policy = make_policy()
        out = policy.sanitize_prompt(f"<@{BOT_ID}> what is 2+2 <@!{BOT_ID}>?")
        assert "<@" not in out
        assert "what is 2+2 ?" in out

    def test_neutralizes_pings(self):
        policy = make_policy()
        out = policy.sanitize_prompt("hey @everyone and @here")
        assert "@everyone" not in out.replace("@\u200beveryone", "")
        assert "@\u200beveryone" in out
        assert "@\u200bhere" in out

    def test_collapses_spaces_preserves_newlines(self):
        policy = make_policy()
        out = policy.sanitize_prompt("line one  with\tspaces\nline   two")
        assert out == "line one with spaces\nline two"

    def test_length_cap(self):
        policy = make_policy(max_prompt_chars=10)
        assert policy.sanitize_prompt("a" * 100) == "a" * 10

    def test_mention_strip_does_not_eat_other_mentions(self):
        policy = make_policy()
        out = policy.sanitize_prompt(f"<@{BOT_ID}> ping <@123> for me")
        assert "<@123>" in out


# ---------------------------------------------------------------------------
# filter_tools
# ---------------------------------------------------------------------------


class TestFilterTools:
    def test_default_set_excludes_shell_and_repl(self):
        policy = make_policy()
        tools = policy.filter_tools("calculator,shell,python_repl,parse_json")
        assert tools == ["calculator", "parse_json"]

    def test_unsafe_lifts_exclusion(self):
        policy = make_policy()
        tools = policy.filter_tools("calculator,shell", unsafe=True)
        assert tools == ["calculator", "shell"]

    def test_soul_allowlist_intersects(self):
        policy = make_policy()
        tools = policy.filter_tools(
            ["calculator", "web_search", "todo"], soul_allowed=["calculator", "todo"]
        )
        assert tools == ["calculator", "todo"]

    def test_soul_string_form(self):
        policy = make_policy()
        tools = policy.filter_tools("calculator,web_search", soul_allowed="web_search")
        assert tools == ["web_search"]

    def test_deduplicates_and_strips(self):
        policy = make_policy()
        tools = policy.filter_tools(" calculator , calculator ,")
        assert tools == ["calculator"]

    def test_unknown_tools_pass_through(self):
        """Plugin tools are allowed — filtering is exclusion/intersection only."""
        policy = make_policy()
        assert policy.filter_tools("crypto-quotes,calculator") == [
            "crypto-quotes",
            "calculator",
        ]
