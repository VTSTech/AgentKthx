"""
AgentKthx Plugin — Discord slash commands (M2)

Interaction plumbing per plan §10: parse INTERACTION_CREATE payloads,
the deny-by-default interaction gate, the six command definitions, and
the quick handlers (/reset /status /model /soul) as pure, injectable
functions. /ask and /think are routed through the responder pool by
discord_bot.py (they run the agent loop; everything else answers in a
single REST callback).

Rule (plan §10): ACK within 3 seconds — /ask and /think reply type 5
(deferred) first and answer via the webhook followup; quick commands
reply type 4 (channel message with source) directly.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Interaction callback types (Discord API)
CALLBACK_MESSAGE = 4  # CHANNEL_MESSAGE_WITH_SOURCE — immediate reply
CALLBACK_DEFER = 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE — "thinking…"
EPHEMERAL = 64  # message flag: visible only to the invoker


@dataclass(frozen=True)
class Interaction:
    """Normalized view of one INTERACTION_CREATE dispatch (chat input)."""

    interaction_id: str
    token: str  # per-interaction webhook token (never log)
    app_id: str
    guild_id: str | None  # None for DM interactions
    channel_id: str
    user_id: str
    username: str
    command: str
    options: dict[str, str] = field(default_factory=dict)

    @property
    def is_dm(self) -> bool:
        return self.guild_id is None

    @property
    def ephemeral(self) -> bool:
        raw = str(self.options.get("ephemeral", ""))
        return raw.lower() in ("1", "true", "yes", "on")


def parse_interaction(d: dict) -> Interaction:
    """Map a raw INTERACTION_CREATE payload to an Interaction (tolerant).

    Guild dispatches carry the user at member.user; DM dispatches at user.
    Option values arrive as a list of {name, value} pairs — stringified
    here (bool false would stringify to "False", which ephemeral handles).
    """
    member = d.get("member") or {}
    user = member.get("user") or d.get("user") or {}
    data = d.get("data") or {}
    options: dict[str, str] = {}
    for opt in data.get("options") or []:
        name = opt.get("name")
        if name:
            options[str(name)] = opt.get("value", "")
    guild_id = d.get("guild_id")
    return Interaction(
        interaction_id=str(d.get("id", "")),
        token=str(d.get("token", "")),
        app_id=str(d.get("application_id", "")),
        guild_id=str(guild_id) if guild_id is not None else None,
        channel_id=str(d.get("channel_id", "")),
        user_id=str(user.get("id", "")),
        username=str(user.get("username", "")),
        command=str(data.get("name", "")),
        options=options,
    )


# Option types: 3 = string, 5 = boolean.
SLASH_COMMANDS = [
    {
        "name": "ask",
        "description": "Ask AgentKthx (same as @mention, explicit)",
        "options": [
            {"name": "prompt", "description": "Your question", "type": 3, "required": True},
            {
                "name": "ephemeral",
                "description": "Reply visible only to you",
                "type": 5,
                "required": False,
            },
        ],
    },
    {
        "name": "think",
        "description": "Ask with the model's reasoning shown first",
        "options": [
            {"name": "prompt", "description": "Your question", "type": 3, "required": True},
        ],
    },
    {
        "name": "model",
        "description": "Show or switch the model for this channel (owner)",
        "options": [
            {
                "name": "name",
                "description": "Model to switch to (omit to show current)",
                "type": 3,
                "required": False,
            },
        ],
    },
    {
        "name": "soul",
        "description": "Show or set the soul for this channel (owner)",
        "options": [
            {
                "name": "name",
                "description": "Soul to switch to (omit to show current)",
                "type": 3,
                "required": False,
            },
        ],
    },
    {
        "name": "reset",
        "description": "Clear this channel's conversation memory",
    },
    {
        "name": "status",
        "description": "Show backend/model/session status",
    },
]

COMMAND_NAMES = frozenset(c["name"] for c in SLASH_COMMANDS)


def gate_interaction(
    inter: Interaction,
    *,
    allow_guilds,
    allow_channels,
    allow_users,
    allow_dms: bool,
) -> str | None:
    """Deny-by-default gate for interactions (plan §12). Mirrors the
    message gates: allowlists first, no trigger matrix (commands ARE the
    trigger). Returns None when allowed, else a machine reason string."""
    if not inter.user_id:
        return "no-user"
    if inter.is_dm:
        if not allow_dms:
            return "dms-disabled"
        if allow_users and inter.user_id not in allow_users:
            return "user-not-allowed"
        return None
    if not allow_guilds or inter.guild_id not in allow_guilds:
        return "guild-not-allowed"
    if allow_channels and inter.channel_id not in allow_channels:
        return "channel-not-allowed"
    if allow_users and inter.user_id not in allow_users:
        return "user-not-allowed"
    return None


def is_owner(user_id: str, owner_ids) -> bool:
    """Owner check for /model + /soul. `owner_ids` falls back to the user
    allowlist upstream (single-operator setups), but empty == nobody."""
    return bool(owner_ids) and user_id in set(owner_ids)


# ---------------------------------------------------------------------------
# Quick handlers — pure, injectable, each returns (content, ephemeral)
# ---------------------------------------------------------------------------


def handle_reset(inter: Interaction, *, session_key: str, delete_fn) -> tuple[str, bool]:
    """/reset — clear this channel's session. `delete_fn(session_key)` is
    PersistentMemory.delete_session (injectable for offline tests)."""
    try:
        existed = bool(delete_fn(session_key))
    except Exception as err:  # noqa: BLE001 - never crash the interaction
        return f"Could not clear {session_key}: {type(err).__name__}", True
    if existed:
        return f"Session cleared ({session_key}) — next message starts fresh.", True
    return f"No stored session for {session_key} — nothing to clear.", True


def handle_status(
    inter: Interaction,
    *,
    backend: str,
    model: str,
    soul: str,
    tools: str,
    max_steps: int,
    cooldown_s: float,
    uptime_s: float,
    queue_depth: int,
    agent_runs: int,
    session_key: str,
) -> tuple[str, bool]:
    """/status — backend, model, uptime, queue depth, last-run counters."""
    minutes, seconds = divmod(max(0, int(uptime_s)), 60)
    hours, minutes = divmod(minutes, 60)
    lines = [
        "**AgentKthx status**",
        f"- backend: {backend} | model: {model}",
        f"- soul: {soul} | tools: {tools}",
        f"- max-steps: {max_steps} | cooldown: {cooldown_s:g}s",
        f"- uptime: {hours}h{minutes:02d}m{seconds:02d}s | queue: {queue_depth}",
        f"- agent runs this session: {agent_runs}",
        f"- session: `{session_key}`",
    ]
    return "\n".join(lines), True


def handle_model(
    inter: Interaction, *, current_model: str, owner: bool, name: str
) -> tuple[str, bool]:
    """/model — show (anyone) or switch (owner) the channel model."""
    if not name:
        return f"Model for this channel: **{current_model}**", True
    if not owner:
        return "Only owners can switch the model (DISCORD_OWNER_IDS).", True
    return f"Model for this channel set to **{name}** (this channel only).", True


def handle_soul(
    inter: Interaction,
    *,
    current_soul: str,
    owner: bool,
    name: str,
    validate_fn=None,
) -> tuple[str, bool]:
    """/soul — show (anyone) or set (owner) the channel soul. `validate_fn`
    checks the name against the souls loader (None = skip validation)."""
    if not name:
        shown = current_soul or "none"
        return f"Soul for this channel: **{shown}**", True
    if not owner:
        return "Only owners can switch the soul (DISCORD_OWNER_IDS).", True
    if validate_fn is not None:
        try:
            known = bool(validate_fn(name))
        except Exception:  # noqa: BLE001 - validation must not crash
            known = True
        if not known:
            return f"Unknown soul '{name}' — see `agentkthx souls`.", True
    return f"Soul for this channel set to **{name}** (this channel only).", True
