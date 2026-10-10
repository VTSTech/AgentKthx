"""
AgentKthx Plugin — Discord Session Identity & Channel Overrides (M1)

Maps Discord channels to PersistentMemory session keys and resolves
per-channel overrides from ~/.agentkthx/discord.json:

    session_key  =  discord-g{guild_id}-c{channel_id}[-p{prefix}][-r{run}]
                    discord-dm-{user_id}[-p{prefix}][-r{run}]

Keys are chosen to be visible and greppable in `agentkthx sessions` output
and the ~/.agentkthx/ SQLite store. Passing the key to Agent(session_id=...)
restores that conversation.

Fresh sessions by default (R07.33): the bot stamps every key with its run
id (`-r<unix-start>`), so each restart starts new conversations per channel.
`--keep` / DISCORD_KEEP_SESSIONS=true drops the stamp and restores the old
stable keys (history resumes across restarts). DISCORD_SESSION_TTL_DAYS
(default 7) is independent: it only garbage-collects sessions from the
SQLite store after that many days of inactivity — it never affects whether
a restart resumes.

Also owns the TTL pruning and the soul -> allowedTools resolution
used by the Discord tool policy (plan §9, §12).

Pure stdlib. No network. See docs/DISCORD_PLUGIN_PLAN.md §7.4, §9.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SESSION_PREFIX = "discord-"
# R07.33: lowered 30 -> 7. Fresh-on-restart is now the default, so every bot
# run leaves its own discord-* session rows behind; a week of inactivity is
# plenty before the store self-cleans. DISCORD_SESSION_TTL_DAYS overrides
# (set 0 to disable pruning entirely).
DEFAULT_SESSION_TTL_DAYS = 7

# Guild session keys start with discord-g<digits>-c<digits>; anything after
# that (session prefix, run stamp) is decoration as far as identity goes.
_GUILD_KEY_RE = re.compile(r"^discord-g(\d+)-c(\d+)")


# ---------------------------------------------------------------------------
# Identity mapping
# ---------------------------------------------------------------------------


def session_key_for(guild_id: str | None, channel_id: str, user_id: str) -> str:
    """Guild channels share one conversation per channel; DMs are per-user."""
    if guild_id:
        return f"discord-g{guild_id}-c{channel_id}"
    return f"discord-dm-{user_id}"


def channel_id_from_key(key: str) -> str | None:
    """Extract the channel id from a guild session key (None for DM keys).

    Tolerates suffixed keys — ``discord-g1-c2-psupport-r1700000000`` still
    yields ``2`` (the old rsplit('-c') approach would have returned the
    whole tail).
    """
    match = _GUILD_KEY_RE.match(key)
    return match.group(2) if match else None


def session_key_with_run_stamp(base_key: str, run_stamp: str | None) -> str:
    """Scope a session key to one bot run (fresh-sessions default, R07.33).

    ``run_stamp`` is the bot's start time as a unix-seconds string. With a
    stamp the key becomes ``<base>-r<stamp>`` — a restart produces a new
    stamp, hence a new empty conversation per channel. ``run_stamp=None``
    is the --keep mode: the stable key resumes history across restarts.
    """
    if not run_stamp:
        return base_key
    return f"{base_key}-r{run_stamp}"


def is_discord_session(session_id: str) -> bool:
    return session_id.startswith(SESSION_PREFIX)


# ---------------------------------------------------------------------------
# Per-channel overrides (~/.agentkthx/discord.json)
# ---------------------------------------------------------------------------


@dataclass
class ChannelConfig:
    """Per-channel overrides merged over env/CLI defaults (stricter wins)."""

    soul: str | None = None
    tools: list[str] | None = None
    session_prefix: str | None = None
    model: str | None = None  # /model slash command or discord.json override


def default_discord_json_path() -> str:
    return os.path.expanduser("~/.agentkthx/discord.json")


def resolve_channel_config(channel_id: str | None, cfg_path: str | None = None) -> ChannelConfig:
    """
    Merge the channel's entry from ~/.agentkthx/discord.json over defaults:

        {"channels": {"123...": {"soul": "kthx-trading",
                                  "tools": ["calculator"],
                                  "session_prefix": "support",
                                  "model": "qwen3:8b"}}}

    Missing file / missing channel / malformed JSON -> all-None defaults.
    Never raises: a broken overrides file must not take the bot down.
    """
    path = Path(cfg_path or default_discord_json_path())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return ChannelConfig()
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return ChannelConfig()
    entry = (raw.get("channels") or {}).get(str(channel_id or ""))
    if not isinstance(entry, dict):
        return ChannelConfig()
    tools = entry.get("tools")
    return ChannelConfig(
        soul=str(entry["soul"]) if entry.get("soul") else None,
        tools=[str(t) for t in tools] if isinstance(tools, list) else None,
        session_prefix=(str(entry["session_prefix"]) if entry.get("session_prefix") else None),
        model=str(entry["model"]) if entry.get("model") else None,
    )


def session_key_with_prefix(base_key: str, channel_cfg: ChannelConfig) -> str:
    """Apply an optional per-channel session_prefix (plan §9 example:
    "support" prefix for a support channel's separate history thread)."""
    prefix = channel_cfg.session_prefix
    if not prefix:
        return base_key
    return f"{base_key}-p{prefix}"


# ---------------------------------------------------------------------------
# Session TTL pruning (DISCORD_SESSION_TTL_DAYS)
# ---------------------------------------------------------------------------


def _parse_updated_at(raw: str) -> datetime | None:
    """SQLite CURRENT_TIMESTAMP format ('YYYY-MM-DD HH:MM:SS', UTC)."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


def prune_discord_sessions(
    ttl_days: int = DEFAULT_SESSION_TTL_DAYS, *, db_path: str | None = None
) -> list[str]:
    """
    Delete `discord-*` sessions older than ttl_days. Returns the deleted
    session ids (empty list = nothing pruned). Non-Discord sessions are
    never touched, and a failing prune must never crash startup.
    """
    deleted: list[str] = []
    if not ttl_days or ttl_days <= 0:
        return deleted
    try:
        # Lazy import: keep the plugin loader's import graph light.
        from agentkthx.core.persistent_memory import PersistentMemory

        now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC match
        for row in PersistentMemory.list_sessions(db_path):
            sid = str(row.get("session_id", ""))
            if not is_discord_session(sid):
                continue
            updated = _parse_updated_at(str(row.get("updated_at", "")))
            if updated is None:
                continue  # unparseable timestamps are kept, not deleted
            age_days = (now - updated).total_seconds() / 86400.0
            if age_days > float(ttl_days):
                if PersistentMemory.delete_session(sid, db_path):
                    deleted.append(sid)
    except Exception:  # noqa: BLE001 - pruning is best-effort at startup
        return deleted
    return deleted


# ---------------------------------------------------------------------------
# Soul -> allowedTools (tool policy intersection, plan §9)
# ---------------------------------------------------------------------------


def resolve_soul_allowed_tools(soul_name: str | None) -> list[str] | None:
    """
    Resolve a soul's allowedTools list by name ("kthx-helper", ...), or
    None when the soul doesn't constrain tools / can't be loaded. The
    caller intersects this with the Discord allowlist (stricter wins).
    """
    if not soul_name:
        return None
    try:
        from agentkthx.soul.loader import get_soul_loader, load_soul

        loader = get_soul_loader()
        resolved = loader._resolve_soul_path(Path(soul_name))  # noqa: SLF001
        if resolved is None:
            return None
        manifest = load_soul(resolved, level=1)
        allowed = getattr(manifest, "allowed_tools", None)
        if not allowed:
            return None
        return [str(t) for t in allowed]
    except Exception:  # noqa: BLE001 - a bad soul name must not kill the bot
        return None
