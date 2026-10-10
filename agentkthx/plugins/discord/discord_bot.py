"""
AgentKthx Plugin — `agentkthx discord` command (M0 skeleton)

M0 scope (docs/DISCORD_PLUGIN_PLAN.md §15): connect to the Discord
gateway, hold the session (identify / heartbeat / resume / watchdog),
evaluate every MESSAGE_CREATE through the policy gatekeeper, and prove
the REST round-trip. The chat responder (worker pool + Agent invocation
+ chunked replies) lands in M1 — triggered messages in live mode get an
explicit "M0 skeleton" notice so operators are never confused.

Dry-run mode connects and logs decisions but never touches message-sending
REST calls (typing/sends). GET /users/@me still runs in dry-run: it is the
token self-check and needs no bot permissions.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from .gateway import (
    FATAL_CLOSE_CODES,
    INTENT_DIRECT_MESSAGES,
    INTENT_GUILD_MESSAGES,
    INTENT_GUILDS,
    INTENT_MESSAGE_CONTENT,
    FatalGatewayError,
    GatewayClient,
)
from .policy import Policy, context_from_payload
from .rest import DiscordRest, DiscordRestError, chunk_reply

# The four intents from plan §5.2. MESSAGE_CONTENT is privileged — the
# portal toggle must be on or the gateway closes with 4014 (fatal).
DISCORD_INTENTS = (
    INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT
)

_M0_NOTICE = (
    "AgentKthx Discord plugin online (M0 skeleton) — gateway + policy + REST "
    "verified. The chat responder lands in R07.34 (M1)."
)

# Set by cmd_discord; read by the on_shutdown plugin hook.
_ACTIVE_GATEWAY: GatewayClient | None = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _cfg(key: str, default: str) -> str:
    """Env var wins; otherwise fall back to the manifest-registered default."""
    env = os.environ.get(key)
    if env is not None:
        return env
    try:
        from agentkthx.plugins import get_plugin_manager

        pm = get_plugin_manager(init=False)
        if pm is not None:
            defaults = pm.get_config_defaults_by_prefix("DISCORD")
            return str(defaults.get(key, default))
    except Exception:  # noqa: BLE001 - config lookup must never crash the CLI
        pass
    return default


def _split_ids(value: str) -> list[str]:
    return [p.strip() for p in value.split(",") if p.strip()]


def _as_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class BotConfig:
    token: str
    app_id: str
    allow_guilds: list[str] = field(default_factory=list)
    allow_channels: list[str] = field(default_factory=list)
    allow_users: list[str] = field(default_factory=list)
    allow_dms: bool = False
    cooldown_s: float = 10.0
    max_prompt_chars: int = 1500
    max_reply_msgs: int = 3
    max_steps: int = 5
    tools: str = "calculator,parse_json,todo,web_search,http_get"
    dry_run: bool = False
    backend: str | None = None
    model: str | None = None

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "BotConfig":
        overrides = overrides or {}
        cfg = cls(
            token=_cfg("DISCORD_BOT_TOKEN", "").strip(),
            app_id=_cfg("DISCORD_APP_ID", "").strip(),
            allow_guilds=_split_ids(_cfg("DISCORD_ALLOW_GUILDS", "")),
            allow_channels=_split_ids(_cfg("DISCORD_ALLOW_CHANNELS", "")),
            allow_users=_split_ids(_cfg("DISCORD_ALLOW_USERS", "")),
            allow_dms=_as_bool(_cfg("DISCORD_ALLOW_DMS", "false")),
            cooldown_s=float(_cfg("DISCORD_USER_COOLDOWN_S", "10") or 10),
            max_prompt_chars=int(_cfg("DISCORD_MAX_PROMPT_CHARS", "1500") or 1500),
            max_reply_msgs=int(_cfg("DISCORD_MAX_REPLY_MSGS", "3") or 3),
            max_steps=int(_cfg("DISCORD_MAX_STEPS", "5") or 5),
            tools=_cfg("DISCORD_TOOLS", "calculator,parse_json,todo,web_search,http_get"),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(cfg, key, value)
        return cfg


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def setup_parser(parser) -> None:
    """Plugin CLI hook — adds the `agentkthx discord` arguments."""
    parser.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        metavar="SUBCOMMAND",
        help="optional: setup — interactive wizard writing ~/.agentkthx/.env",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect, log policy decisions, skip agent runs and message sends",
    )
    parser.add_argument("--backend", default=None, help="Backend override (M0: reported only)")
    parser.add_argument("--model", default=None, help="Model override (M0: reported only)")
    parser.add_argument(
        "--max-steps", type=int, default=None, help="Agent step cap override (M1)"
    )


def cmd_discord(args) -> int:
    """Entry point for `agentkthx discord`."""
    global _ACTIVE_GATEWAY
    sub = getattr(args, "subcommand", None)
    if sub == "setup":
        from .setup import run_setup  # lazy import per plugin spec

        return run_setup()
    if sub is not None:
        print(f"[discord] unknown subcommand {sub!r} — try 'agentkthx discord setup'")
        return 2
    # Secrets/config file support: ~/.agentkthx/.env (written by `discord setup`)
    # is loaded with setdefault semantics — exported env vars always win.
    from .setup import default_env_path, load_env_file  # lazy import per plugin spec

    _env_path = default_env_path()
    _loaded = load_env_file(_env_path)
    if _loaded:
        print(f"[discord] loaded {_loaded} setting(s) from {_env_path}")
    cfg = BotConfig.from_env(
        overrides={
            "dry_run": getattr(args, "dry_run", False),
            "backend": getattr(args, "backend", None),
            "model": getattr(args, "model", None),
            "max_steps": getattr(args, "max_steps", None),
        }
    )
    if not cfg.token:
        print(
            "[discord] DISCORD_BOT_TOKEN is not set.\n"
            "  Fix: run 'agentkthx discord setup' (writes ~/.agentkthx/.env), or\n"
            "  export DISCORD_BOT_TOKEN=<token from the Developer Portal>\n"
            "  Also enable the MESSAGE CONTENT INTENT toggle (Bot settings ->\n"
            "  Privileged Gateway Intents) or the gateway will close with 4014."
        )
        return 1

    mode = "DRY-RUN" if cfg.dry_run else "LIVE (M0 skeleton)"
    print(f"[discord] starting — mode={mode}")
    print(
        f"[discord] allowlists: guilds={len(cfg.allow_guilds)} "
        f"channels={len(cfg.allow_channels)} users={len(cfg.allow_users)} "
        f"dms={cfg.allow_dms} cooldown={cfg.cooldown_s:g}s"
    )
    print(f"[discord] intents={DISCORD_INTENTS} tools={cfg.tools}")

    rest = DiscordRest(cfg.token)
    try:
        me = rest.get_self()
    except DiscordRestError as err:
        print(f"[discord] token self-check failed: {err}")
        if err.status == 401:
            print("  Fix: DISCORD_BOT_TOKEN is invalid or was reset — recheck the portal.")
        return 1
    bot_user_id = str(me.get("id", ""))
    print(f"[discord] connected as {me.get('username', '?')} ({bot_user_id})")

    policy = Policy(
        bot_user_id=bot_user_id,
        allow_guilds=cfg.allow_guilds,
        allow_channels=cfg.allow_channels,
        allow_users=cfg.allow_users,
        allow_dms=cfg.allow_dms,
        cooldown_s=cfg.cooldown_s,
        max_prompt_chars=cfg.max_prompt_chars,
        max_reply_msgs=cfg.max_reply_msgs,
    )

    def dispatcher(event: str, data: dict) -> None:
        if event == "MESSAGE_CREATE":
            _on_message(cfg, policy, rest, data)
        # READY / RESUMED / everything else is already logged by the client.

    gateway = GatewayClient(cfg.token, DISCORD_INTENTS, dispatcher, log=_print_log)
    _ACTIVE_GATEWAY = gateway
    try:
        gateway.run_forever()
        return 0
    except FatalGatewayError as err:
        print(f"[discord] FATAL: {err}")
        if err.code in FATAL_CLOSE_CODES:
            print("  No reconnect will be attempted — fix the cause and restart.")
        return 1
    except KeyboardInterrupt:
        print("\n[discord] Ctrl+C — shutting down")
        gateway.stop()
        return 0
    finally:
        gateway.stop()
        _ACTIVE_GATEWAY = None


def _print_log(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Event handling (M0)
# ---------------------------------------------------------------------------


def _on_message(cfg: BotConfig, policy: Policy, rest: DiscordRest, data: dict) -> None:
    """M0 message path: policy gate -> dry-run log / live skeleton notice."""
    ev = context_from_payload(data)
    decision = policy.check_event(ev)
    if not decision.allowed:
        # Silence is the default: only non-trigger denials stay fully quiet.
        if decision.reason not in ("no-trigger",):
            print(f"[discord] denied {ev.message_id}: {decision.reason}")
        return
    prompt = policy.sanitize_prompt(ev.content)
    preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
    if cfg.dry_run:
        print(f"[discord] DRY-RUN would answer {ev.username}: {preview!r}")
        return
    rate = policy.check_rate(ev.user_id)
    if not rate.allowed:
        wait = f" (retry in {rate.retry_after:.0f}s)" if rate.retry_after else ""
        try:
            rest.send_message(
                ev.channel_id, f"Rate limited{wait} — cooldown {cfg.cooldown_s:g}s."
            )
        except DiscordRestError as err:
            print(f"[discord] rate-limit reply failed: {err}")
        return
    try:
        rest.trigger_typing(ev.channel_id)
        for chunk in chunk_reply(_M0_NOTICE, max_msgs=cfg.max_reply_msgs):
            rest.send_message(ev.channel_id, chunk)
        print(f"[discord] answered M0 notice to {ev.username} in {ev.channel_id}")
    except DiscordRestError as err:
        print(f"[discord] reply failed: {err}")


def on_shutdown(context: dict) -> None:
    """Plugin hook: emitted via atexit by the CLI (spec §Hooks)."""
    gateway = _ACTIVE_GATEWAY
    if gateway is not None:
        gateway.stop()
        print("[discord] gateway closed (on_shutdown)")
