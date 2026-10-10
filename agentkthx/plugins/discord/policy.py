"""
AgentKthx Plugin — Discord Policy Gatekeeper (pure logic, no I/O)

Deny-by-default event gate for Discord triggers: allowlists, the
trigger matrix (mention / reply-to-bot / DM), per-user cooldowns, and
prompt sanitization. Everything here is deterministic and unit-tested
without network. See docs/DISCORD_PLUGIN_PLAN.md §7.3, §8, §12.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageContext:
    """Normalized view of one MESSAGE_CREATE payload (op 0, t=MESSAGE_CREATE)."""

    message_id: str
    channel_id: str
    guild_id: str | None  # None for DMs
    user_id: str
    username: str
    content: str
    mentions: tuple[str, ...] = ()  # user ids mentioned in the message
    reference_author_id: str | None = None  # author of the replied-to message
    author_bot: bool = False
    is_dm: bool = False


@dataclass
class Decision:
    """Result of a policy check. `reason` is a stable machine string."""

    allowed: bool
    reason: str = ""
    retry_after: float | None = None  # populated on cooldown denials


# ---------------------------------------------------------------------------
# MESSAGE_CREATE payload -> MessageContext
# ---------------------------------------------------------------------------


def context_from_payload(d: dict) -> MessageContext:
    """Map a raw MESSAGE_CREATE payload to a MessageContext (tolerant)."""
    author = d.get("author") or {}
    referenced = d.get("referenced_message") or {}
    mention_ids = tuple(
        str(m.get("id")) for m in (d.get("mentions") or []) if m.get("id") is not None
    )
    guild_id = d.get("guild_id")
    return MessageContext(
        message_id=str(d.get("id", "")),
        channel_id=str(d.get("channel_id", "")),
        guild_id=str(guild_id) if guild_id is not None else None,
        user_id=str(author.get("id", "")),
        username=str(author.get("username", "")),
        content=str(d.get("content", "")),
        mentions=mention_ids,
        reference_author_id=(
            str(referenced.get("author", {}).get("id"))
            if referenced.get("author", {}).get("id") is not None
            else None
        ),
        author_bot=bool(author.get("bot", False)) or bool(d.get("webhook_id")),
        is_dm=guild_id is None,
    )


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def _as_id_list(value) -> tuple[str, ...]:
    """Accept a comma-separated string or an iterable of ids; normalize."""
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    else:
        parts = list(value)
    return tuple(p.strip() for p in parts if p and p.strip())


class Policy:
    """
    Deny-by-default gatekeeper.

    Precedence (first failure wins):
      self -> bot author -> DM gate -> guild allowlist -> channel allowlist
      -> user allowlist -> trigger matrix (DM / mention / reply-to-bot).

    An empty allow list denies everything in its scope — the bot stays
    silent until the operator opts in via DISCORD_ALLOW_*.
    """

    def __init__(
        self,
        *,
        bot_user_id: str,
        allow_guilds=(),
        allow_channels=(),
        allow_users=(),
        allow_dms: bool = False,
        cooldown_s: float = 10.0,
        max_prompt_chars: int = 1500,
        max_reply_msgs: int = 3,
    ):
        self.bot_user_id = str(bot_user_id)
        self.allow_guilds = frozenset(_as_id_list(allow_guilds))
        self.allow_channels = frozenset(_as_id_list(allow_channels))
        self.allow_users = frozenset(_as_id_list(allow_users))
        self.allow_dms = bool(allow_dms)
        self.cooldown_s = float(cooldown_s)
        self.max_prompt_chars = int(max_prompt_chars)
        self.max_reply_msgs = int(max_reply_msgs)
        self._last_run: dict[str, float] = {}  # user_id -> monotonic ts
        self._rate_lock = threading.Lock()  # concurrent workers must not race

    # -- event gate -----------------------------------------------------------

    def check_event(self, ev: MessageContext, *, now: float | None = None) -> Decision:
        """Full gate: identity + allowlists + trigger matrix (no cooldown)."""
        if ev.user_id == self.bot_user_id:
            return Decision(False, "self-message")
        if ev.author_bot:
            return Decision(False, "bot-author")
        if ev.is_dm:
            if not self.allow_dms:
                return Decision(False, "dms-disabled")
            if self.allow_users and ev.user_id not in self.allow_users:
                return Decision(False, "user-not-allowed")
        else:
            if not self.allow_guilds or ev.guild_id not in self.allow_guilds:
                return Decision(False, "guild-not-allowed")
            if self.allow_channels and ev.channel_id not in self.allow_channels:
                return Decision(False, "channel-not-allowed")
            if self.allow_users and ev.user_id not in self.allow_users:
                return Decision(False, "user-not-allowed")
        if not self._is_trigger(ev):
            return Decision(False, "no-trigger")
        return Decision(True, "ok")

    def _is_trigger(self, ev: MessageContext) -> bool:
        """DM, mention of the bot, or direct reply to a bot-authored message.

        DMs trigger WITHOUT a mention (plan §16 trigger matrix: a DM is a
        1:1 conversation, not a channel ping) — the dms-disabled /
        user-not-allowed gates have already run by the time we get here,
        so guild chatter still requires an explicit @ or reply.
        """
        if ev.is_dm:
            return True
        if self.bot_user_id in ev.mentions:
            return True
        return ev.reference_author_id == self.bot_user_id

    # -- rate gate --------------------------------------------------------------

    def check_rate(self, user_id: str, *, now: float | None = None) -> Decision:
        """Per-user cooldown. On allow, records this run's timestamp.

        Lock-guarded: with multiple workers pulling from the dispatch
        queue, two near-simultaneous mentions must not both pass the
        bucket (M1 AC: rapid double-mention -> second held by cooldown).
        """
        now = time.monotonic() if now is None else now
        with self._rate_lock:
            last = self._last_run.get(user_id)
            if last is not None:
                elapsed = now - last
                if elapsed < self.cooldown_s:
                    return Decision(
                        False,
                        "cooldown",
                        retry_after=round(self.cooldown_s - elapsed, 3),
                    )
            self._last_run[user_id] = now
            return Decision(True, "ok")

    # -- prompt hygiene -----------------------------------------------------------

    def sanitize_prompt(self, text: str, *, bot_user_id: str | None = None) -> str:
        """
        Make a Discord message safe to hand to the agent loop:
        strip the bot's own mention forms, neutralize @everyone/@here pings,
        collapse space/tab runs (newlines preserved), cap length.
        """
        bot_id = bot_user_id or self.bot_user_id
        for mention in (f"<@{bot_id}>", f"<@!{bot_id}>"):
            text = text.replace(mention, " ")
        text = text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")
        # collapse horizontal whitespace only — newlines are meaningful prompts
        text = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
        cap = self.max_prompt_chars
        if cap and len(text) > cap:
            text = text[:cap]
        return text

    # -- tool policy ---------------------------------------------------------------

    def filter_tools(self, requested, *, soul_allowed=None, unsafe: bool = False) -> list[str]:
        """
        Resolve the effective tool allowlist.

        `requested`: DISCORD_TOOLS string or list. **No tools is the Discord
        default (R07.33)** — `''` / `'none'` / `'off'` (and `[]`) all resolve
        to an empty list; tools only run when explicitly opted in.
        `soul_allowed` (optional): the soul's allowedTools list — the stricter
        set wins (intersection), and a soul never grants tools on its own.
        Discord always excludes `shell`/`python_repl`; `unsafe=True` lifts
        the built-in exclusion (caller must gate that on DISCORD_UNSAFE_TOOLS
        + owner check).
        """
        if isinstance(requested, str) and requested.strip().lower() in (
            "",
            "none",
            "off",
        ):
            return []
        items = (
            requested.replace(";", ",").split(",")
            if isinstance(requested, str)
            else list(requested)
        )
        tools = [t.strip() for t in items if t and t.strip()]
        if not unsafe:
            tools = [t for t in tools if t not in _DISCORD_EXCLUDED_TOOLS]
        if soul_allowed is not None:
            allowed = (
                set(_as_id_list(soul_allowed))
                if isinstance(soul_allowed, str)
                else set(soul_allowed)
            )
            tools = [t for t in tools if t in allowed]
        seen: set[str] = set()
        return [t for t in tools if not (t in seen or seen.add(t))]


# `shell` is dangerous (confirm-gated + audit-logged) and python_repl, while
# sandboxed, is excluded from Discord by default per plan §12 — a chat
# surface should not become a compute surface by accident.
_DISCORD_EXCLUDED_TOOLS = frozenset({"shell", "python_repl"})
