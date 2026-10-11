"""
AgentKthx Plugin — `agentkthx discord` command (M2 chat responder + slash)

M1 scope (docs/DISCORD_PLUGIN_PLAN.md §15): the bot answers. Message flow
(plan §8): gateway thread -> policy gate -> bounded dispatch queue ->
worker pool -> typing indicator (refreshed ~8s) -> Agent(session_id=...)
run under a global semaphore -> final_answer chunked into <=2000-char
messages -> sequential REST sends.

M2 scope (plan §10): six slash commands (/ask /think /model /soul /reset
/status). INTERACTION_CREATE -> parse + gate -> single interaction thread
ACKs (type 4 direct or type 5 defer) -> /ask //think enqueue regular jobs
answered via webhook followups; quick commands answer in the callback.
/model + /soul are owner-gated and apply per-channel runtime overrides.

Text commands (R07.33): the same six commands also work as plain message
text — `@AgentKthx /status`, `@AgentKthx /think why ...`, `/reset` in a DM —
so the bot responds even when the native / picker was never registered.
Discord only dispatches real interactions for picker invocations; typed
`/command` text arrives as an ordinary MESSAGE_CREATE and is parsed here.

Discord identity prompt (R07.33): with no soul configured, runs are built
with `identity_prompt=DISCORD_IDENTITY_PROMPT` (AGI AgentKthx — bringing
Agentic Reasoning to Discord; tools supported but disabled by default) and
`env_section=False` — no host details in the prompt. A configured soul
(--soul / DISCORD_SOUL / discord.json / /soul) replaces the identity.

Safety posture (plan §12): deny-by-default allowlists, per-user cooldowns,
**no tools by default** (opt in via DISCORD_TOOLS / --tools / channel
override; shell/python_repl always excluded), `confirm_dangerous` denies
everything (no human at the terminal to approve), secrets redacted.

Conversation memory (R07.33): **sessions start fresh on every restart** —
each run scopes its keys with a run stamp, so channels begin new
conversations. `--keep` / DISCORD_KEEP_SESSIONS=true restores the old
stable keys (history resumes across restarts). DISCORD_SESSION_TTL_DAYS
(default 7) garbage-collects stale sessions from the store; it does not
affect restart resume.

The prompt envelope is a normal user message — the agentic loop, ReAct
prompting, tool execution, and error recovery are exactly the CLI path.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# Chat-parity token-size parsing: --num-ctx 128k / DISCORD_NUM_CTX=131072.
# Intentionally re-uses the CLI's parser so `agentkthx discord` accepts the
# same human-friendly forms as `agentkthx chat` (R07.18 suffixes).
from agentkthx.shared_args import _parse_token_size

from .commands import (
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
    parse_text_command,
)
from .gateway import (
    FATAL_CLOSE_CODES,
    INTENT_DIRECT_MESSAGES,
    INTENT_GUILD_MESSAGES,
    INTENT_GUILDS,
    INTENT_MESSAGE_CONTENT,
    FatalGatewayError,
    GatewayClient,
)
from .policy import MessageContext, Policy, context_from_payload
from .prompt import DISCORD_IDENTITY_PROMPT
from .rest import DiscordRest, DiscordRestError, chunk_reply
from .sessions import (
    ChannelConfig,
    default_discord_json_path,
    prune_discord_sessions,
    resolve_channel_config,
    resolve_soul_allowed_tools,
    session_key_for,
    session_key_with_prefix,
    session_key_with_run_stamp,
)

# The four intents from plan §5.2. MESSAGE_CONTENT is privileged — the
# portal toggle must be on or the gateway closes with 4014 (fatal).
DISCORD_INTENTS = (
    INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT
)

# No tools by default (R07.33): the Discord responder answers chat directly.
# Tool use on a chat surface burned the whole step budget (the observed
# "maximum steps reached" loop) and nothing on Discord needs tools yet.
# Opt in explicitly: DISCORD_TOOLS=calculator,web_search / --tools /
# per-channel discord.json "tools". `none`/`off`/empty all mean no tools.
DEFAULT_TOOLS = ""
# No soul by default (R07.33): `--soul` / DISCORD_SOUL / per-channel
# discord.json / /soul opt into one; Agent(soul=None) uses the built-in
# no-soul fallback system prompt. A soul no longer grants tools on its own —
# DISCORD_TOOLS (or a channel override) must opt in too.

TYPING_REFRESH_S = 8.0  # Discord typing state expires after 10s (plan §8)
SMALL_REPLY_GAP_S = 10.0  # per-channel cap for one-line notices (plan §8.6)

# Set by cmd_discord; read by the on_shutdown plugin hook.
_ACTIVE_GATEWAY: GatewayClient | None = None
_ACTIVE_POOL: "ResponderPool | None" = None


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


def _size_cfg(key: str) -> int | None:
    """Parse an optional token-size setting (DISCORD_NUM_CTX / DISCORD_MAX_TOKENS).

    Empty/unset -> None (the Agent keeps its built-in defaults). Accepts the
    same human-friendly forms as the CLI's --num-ctx / --num-predict
    (``32768``, ``128k``, ``2.5k`` …) via the shared _parse_token_size.
    A malformed value is reported and ignored rather than crashing startup.
    """
    raw = _cfg(key, "").strip()
    if not raw:
        return None
    try:
        return _parse_token_size(raw)
    except (ValueError, TypeError):
        print(f"[discord] ignoring invalid {key}={raw!r} (expected int or e.g. 128k)")
        return None


def _config_num_ctx() -> int | None:
    """config.num_ctx fallback (chat parity): OLLAMA_NUM_CTX / AGENTKTHX_NUM_CTX."""
    try:
        from agentkthx.config import get_config

        return get_config().num_ctx
    except Exception:  # noqa: BLE001 - lookup must never crash the bot
        return None


def resolve_gen_params(
    backend_name: str | None,
    model: str,
    num_ctx: int | None,
    max_tokens: int | None,
) -> tuple[int | None, int | None]:
    """Chat-parity generation params for `agentkthx discord` (mirrors
    agent_factory._build_agent): an explicit value always wins; otherwise
    num_ctx/max_tokens are DETECTED from the backend/model catalog exactly
    as chat does — _get_catalog_defaults (offline static tables for cloud
    backends, TurboState/GGUF metadata/remote probe for local ones); the
    final num_ctx fallback is config.num_ctx (env), then the Agent built-in
    8192. Returns (num_ctx, max_tokens).

    Live-verified: --backend zai -m glm-4.5-flash detects num_ctx=132000,
    max_tokens=4125 — the same numbers `agentkthx chat` sends.
    """
    if num_ctx is not None and max_tokens is not None:
        return num_ctx, max_tokens  # fully explicit — skip detection entirely
    if backend_name is None:  # chat: args.backend or config.backend
        try:
            from agentkthx.config import get_config

            backend_name = get_config().backend
        except Exception:  # noqa: BLE001
            backend_name = "ollama"
    defaults: dict = {}
    try:
        from agentkthx.backends import get_backend
        from agentkthx.cli.agent_factory import _get_catalog_defaults

        defaults = _get_catalog_defaults(get_backend(backend_name), model) or {}
    except Exception:  # noqa: BLE001 - unknown backend / offline -> agent defaults
        pass
    eff_ctx = num_ctx if num_ctx is not None else (defaults.get("num_ctx") or _config_num_ctx())
    eff_pred = max_tokens if max_tokens is not None else defaults.get("num_predict")
    return eff_ctx, eff_pred


def _gen_display(set_val: int | None, eff_val: int | None) -> str:
    """Banner rendering for one gen param: '(set)' = explicit,
    '(catalog)' = detected from the backend/model catalog (chat parity),
    'auto' = agent default (8192 / num_ctx//32 cap)."""
    if set_val is not None:
        return f"{set_val} (set)"
    if eff_val is not None:
        return f"{eff_val} (catalog)"
    return "auto"


def _tools_display(raw) -> str:
    """Banner/status rendering: '' / 'none' / 'off' / empty list -> 'none';
    a channel override list renders comma-joined."""
    if raw is None:
        return "none"
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in ("none", "off"):
            return "none"
        return text
    return ", ".join(str(t) for t in raw) or "none"


def _default_model() -> str:
    """Same default the CLI chat path resolves (config.default_model)."""
    try:
        from agentkthx.config import get_config

        return str(get_config().default_model)
    except Exception:  # noqa: BLE001 - fall back to a known-good small model
        return "qwen2.5:0.5b"


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
    tools: str = DEFAULT_TOOLS
    queue_max: int = 8
    max_workers: int = 2
    session_ttl_days: int = 7
    keep_sessions: bool = False  # False = fresh conversations on every restart
    discord_json: str = ""
    soul: str | None = None
    owner_ids: list[str] = field(default_factory=list)
    register_slash: bool = False
    api_mode: str | None = None
    dry_run: bool = False
    backend: str | None = None
    model: str | None = None
    debug: bool = False
    # Chat-parity generation params. None = DETECT from the backend/model
    # catalog exactly like `agentkthx chat` (zai glm-4.5-flash detects
    # num_ctx=132000 / max_tokens=4125; live-verified) — not the agent's
    # 8192/num_ctx//32 defaults that truncated Discord replies.
    num_ctx: int | None = None
    max_tokens: int | None = None

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
            tools=_cfg("DISCORD_TOOLS", DEFAULT_TOOLS) or "",  # no tools by default
            queue_max=max(1, int(_cfg("DISCORD_QUEUE_MAX", "8") or 8)),
            max_workers=max(1, int(_cfg("DISCORD_MAX_WORKERS", "2") or 2)),
            session_ttl_days=int(_cfg("DISCORD_SESSION_TTL_DAYS", "7") or 7),
            keep_sessions=_as_bool(_cfg("DISCORD_KEEP_SESSIONS", "false")),
            discord_json=_cfg("DISCORD_CONFIG", "") or default_discord_json_path(),
            owner_ids=_split_ids(_cfg("DISCORD_OWNER_IDS", "")),
            register_slash=_as_bool(_cfg("DISCORD_REGISTER_SLASH", "false")),
            soul=(_cfg("DISCORD_SOUL", "") or None),  # no soul by default (R07.33)
            debug=_as_bool(_cfg("DISCORD_DEBUG", "false")),
            num_ctx=_size_cfg("DISCORD_NUM_CTX"),
            max_tokens=_size_cfg("DISCORD_MAX_TOKENS"),
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
    parser.add_argument("--backend", default=None, help="Backend override (e.g. ollama, zai)")
    parser.add_argument("--model", default=None, help="Model override")
    parser.add_argument("--api", default=None, help="API mode for the backend (openre | openai)")
    parser.add_argument(
        "--soul", default=None, help="Soul override (default: none — opt in per flag/channel)"
    )
    parser.add_argument("--tools", default=None, help="Comma-separated tool allowlist override")
    parser.add_argument("--max-steps", type=int, default=None, help="Agent step cap override")
    parser.add_argument(
        "--num-ctx",
        type=_parse_token_size,
        default=None,
        metavar="TOKENS",
        help="Context window override, e.g. 32768 or 128k (default: detected "
        "from the backend/model catalog like chat; DISCORD_NUM_CTX also works)",
    )
    parser.add_argument(
        "--max-tokens",
        type=_parse_token_size,
        default=None,
        metavar="TOKENS",
        help="Generation cap override, e.g. 4096 or 2k (default: catalog "
        "value, else the agent's num_ctx//32 cap; DISCORD_MAX_TOKENS also works)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Resume per-channel history across restarts (default: every "
        "restart starts fresh conversations; DISCORD_KEEP_SESSIONS=true "
        "also opts in)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Echo backend prompts/responses/errors and pipeline details "
        "(or set DISCORD_DEBUG=true)",
    )
    parser.add_argument(
        "--register-commands",
        action="store_true",
        help="Register slash commands (global) then exit without the gateway",
    )


def cmd_discord(args) -> int:
    """Entry point for `agentkthx discord`."""
    global _ACTIVE_GATEWAY, _ACTIVE_POOL
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
            "api_mode": getattr(args, "api", None),
            "soul": getattr(args, "soul", None),
            "max_steps": getattr(args, "max_steps", None),
            "num_ctx": getattr(args, "num_ctx", None),
            "max_tokens": getattr(args, "max_tokens", None),
            # flag wins only when raised — None keeps the DISCORD_DEBUG env value
            "debug": True if getattr(args, "debug", False) else None,
            # same None-skip semantics for --keep (DISCORD_KEEP_SESSIONS env works alone)
            "keep_sessions": True if getattr(args, "keep", False) else None,
        }
    )
    tools_arg = getattr(args, "tools", None)
    if tools_arg:
        cfg.tools = tools_arg
    if not cfg.token:
        print(
            "[discord] DISCORD_BOT_TOKEN is not set.\n"
            "  Fix: run 'agentkthx discord setup' (writes ~/.agentkthx/.env), or\n"
            "  export DISCORD_BOT_TOKEN=<token from the Developer Portal>\n"
            "  Also enable the MESSAGE CONTENT INTENT toggle (Bot settings ->\n"
            "  Privileged Gateway Intents) or the gateway will close with 4014."
        )
        return 1

    mode = "DRY-RUN" if cfg.dry_run else "LIVE"
    print(f"[discord] starting — mode={mode}")
    print(
        f"[discord] allowlists: guilds={len(cfg.allow_guilds)} "
        f"channels={len(cfg.allow_channels)} users={len(cfg.allow_users)} "
        f"dms={cfg.allow_dms} cooldown={cfg.cooldown_s:g}s"
    )
    print(
        f"[discord] backend={cfg.backend or 'default'} model={cfg.model or 'default'} "
        f"tools={_tools_display(cfg.tools)} soul={cfg.soul or 'none'}"
    )
    if cfg.register_slash:
        print(f"[discord] slash: register on startup (app={cfg.app_id or 'auto'})")
    print(
        f"[discord] pool: workers={cfg.max_workers} queue={cfg.queue_max} "
        f"max-steps={cfg.max_steps} sessions={'keep' if cfg.keep_sessions else 'fresh'} "
        f"session-ttl={cfg.session_ttl_days}d"
    )
    # Resolve the banner's gen line up front (offline for static catalogs,
    # same chain as the pool's per-model resolution).
    eff_ctx, eff_pred = resolve_gen_params(
        cfg.backend, cfg.model or _default_model(), cfg.num_ctx, cfg.max_tokens
    )
    print(
        f"[discord] gen: num_ctx={_gen_display(cfg.num_ctx, eff_ctx)} "
        f"max_tokens={_gen_display(cfg.max_tokens, eff_pred)}"
    )
    if cfg.debug:
        print("[discord] debug: ON — backend prompts/responses/errors will be echoed")

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

    # Slash registration (M2): DISCORD_APP_ID env or auto-resolved via
    # GET /oauth2/applications/@me. --register-commands exits after.
    app_id = cfg.app_id
    if not app_id:
        try:
            app_id = str((rest.get_application() or {}).get("id") or "")
        except DiscordRestError as err:
            print(f"[discord] app id lookup failed: {err}")
            app_id = ""
    register_now = bool(getattr(args, "register_commands", False))
    if register_now or cfg.register_slash:
        if not app_id:
            print(
                "[discord] cannot register slash commands: DISCORD_APP_ID is not set "
                "and could not be resolved from the token"
            )
            if register_now:
                return 1
        else:
            try:
                registered = rest.register_commands(app_id, SLASH_COMMANDS)
                print(f"[discord] registered {len(registered)} slash command(s) (global)")
            except DiscordRestError as err:
                print(f"[discord] slash registration failed: {err}")
                if register_now:
                    return 1
    if register_now:
        print("[discord] --register-commands: done — gateway not started")
        return 0
    # Startup visibility (R07.33): report whether native slash commands are
    # actually registered — the #1 "slash commands do not appear" cause.
    if app_id and not cfg.register_slash:
        try:
            known = rest.list_commands(app_id)
            if known:
                print(
                    f"[discord] slash: {len(known)} global command(s) registered "
                    f"(native / picker + @bot /command text both live)"
                )
            else:
                print(
                    "[discord] slash: NOT registered — the native / picker will "
                    "not list commands. Run 'agentkthx discord --register-commands' "
                    "once; @bot /command text forms work either way"
                )
        except DiscordRestError as err:
            print(f"[discord] slash status check failed: {err}")

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

    pruned = prune_discord_sessions(cfg.session_ttl_days)
    if pruned:
        print(f"[discord] pruned {len(pruned)} stale session(s) (>{cfg.session_ttl_days}d)")

    pool = ResponderPool(cfg, policy, rest)
    _ACTIVE_POOL = pool
    pool.start()

    def dispatcher(event: str, data: dict) -> None:
        if event == "MESSAGE_CREATE":
            pool.submit_event(context_from_payload(data))
        elif event == "INTERACTION_CREATE":
            pool.submit_interaction(data)
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
        pool.stop()
        _ACTIVE_POOL = None


def _print_log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _deny_dangerous(tool_name: str, tool_args: dict) -> bool:
    """confirm_dangerous callback: always deny (plan §12).

    A Discord bot has no human at the terminal to approve dangerous tool
    runs, so anything flagged dangerous=True is refused and audit-logged
    by the standard gate.
    """
    print(f"[discord] denied dangerous tool {tool_name} (confirm gate: deny-all)")
    return False


def _soul_exists(name: str) -> bool:
    """True when `name` resolves to a known soul package (souls loader)."""
    if not name:
        return False
    try:
        from pathlib import Path

        from agentkthx.soul.loader import get_soul_loader

        return get_soul_loader()._resolve_soul_path(Path(name)) is not None  # noqa: SLF001
    except Exception:  # noqa: BLE001 - loader trouble: don't block the set
        return True


# ---------------------------------------------------------------------------
# Responder pool (plan §4: bounded queue + workers + semaphore)
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One accepted trigger awaiting an agent run."""

    ev: MessageContext
    prompt: str  # sanitized message text (no envelope)
    session_key: str
    ch_cfg: ChannelConfig
    interaction: Interaction | None = None  # set for /ask + /think jobs
    want_think: bool = False  # /think — show step reasoning


class ResponderPool:
    """
    Owns the bounded dispatch queue and the worker pool.

    - gateway thread calls submit_event() — never blocks (put_nowait)
    - `max_workers` threads pull jobs; agent runs are serialized by a
      semaphore so local backends never face concurrent inference
    - one-line notices (cooldown / queue-full / backend error) are capped
      at 1 per SMALL_REPLY_GAP_S per channel so the bot can't spam
    """

    def __init__(
        self,
        cfg: BotConfig,
        policy: Policy,
        rest: DiscordRest,
        run_stamp: str | None = None,
    ):
        self.cfg = cfg
        self.policy = policy
        self.rest = rest
        # Fresh sessions by default (R07.33): scope every session key to this
        # bot run so a restart starts new conversations per channel. --keep /
        # DISCORD_KEEP_SESSIONS=true restores the stable cross-restart keys.
        self._run_stamp = None if cfg.keep_sessions else (run_stamp or str(int(time.time())))
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, cfg.queue_max))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, cfg.max_workers), thread_name_prefix="discord-worker"
        )
        self._stop = threading.Event()
        self._agent_sem = threading.Semaphore(1)
        self._small_guard: dict[str, float] = {}
        self._small_lock = threading.Lock()
        self._label_cache: dict[str, str] = {}
        self._label_lock = threading.Lock()
        self._typing_quiet: set[str] = set()  # channels that 404'd typing once
        self._channel_overrides: dict[str, ChannelConfig] = {}  # /model, /soul
        # (num_ctx, max_tokens) resolution cache, keyed by model name —
        # catalog detection runs once per model, not per message.
        self._gen_params: dict[str, tuple[int | None, int | None]] = {}
        self._owners = cfg.owner_ids or cfg.allow_users  # owner fallback
        self._inter_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discord-inter")
        self._started_at = 0.0
        self.agent_runs = 0  # observable for tests/status

    def _session_key(self, base_key: str, ch_cfg: ChannelConfig) -> str:
        """Full session key: channel prefix (discord.json) then run stamp
        (fresh-on-restart default; None under --keep)."""
        return session_key_with_run_stamp(
            session_key_with_prefix(base_key, ch_cfg), self._run_stamp
        )

    def _dbg(self, msg: str) -> None:
        """--debug / DISCORD_DEBUG pipeline echo (backend payloads themselves
        are printed by the core Agent when built with debug=True)."""
        if self.cfg.debug:
            print(f"[discord:debug] {msg}")

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._started_at = time.monotonic()
        for _ in range(max(1, self.cfg.max_workers)):
            self._executor.submit(self._worker_loop)

    def stop(self) -> None:
        self._stop.set()
        pending = self._queue.qsize()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._inter_executor.shutdown(wait=False)
        if pending:
            print(f"[discord] pool stopped with {pending} queued job(s) dropped")

    # -- intake ----------------------------------------------------------------

    def _channel_cfg(self, channel_id: str) -> ChannelConfig:
        """File-based overrides merged with runtime /model + /soul changes
        (runtime overrides win for this process; file wins on restart)."""
        base = resolve_channel_config(channel_id, self.cfg.discord_json)
        override = self._channel_overrides.get(channel_id)
        if override is None:
            return base
        return ChannelConfig(
            soul=override.soul if override.soul is not None else base.soul,
            tools=override.tools if override.tools is not None else base.tools,
            session_prefix=(
                override.session_prefix
                if override.session_prefix is not None
                else base.session_prefix
            ),
            model=override.model if override.model is not None else base.model,
        )

    def submit_event(self, ev: MessageContext) -> None:
        """Gateway-thread side: policy gate -> dry-run print -> enqueue."""
        decision = self.policy.check_event(ev)
        if not decision.allowed:
            if decision.reason not in ("no-trigger", "self-message", "bot-author"):
                print(f"[discord] denied {ev.message_id}: {decision.reason}")
            return
        prompt = self.policy.sanitize_prompt(ev.content)
        if not prompt:
            print(f"[discord] ignored empty trigger {ev.message_id} (bare ping)")
            return
        text_cmd = parse_text_command(prompt)
        if text_cmd is not None:
            self._submit_text_command(ev, text_cmd[0], text_cmd[1])
            return
        if self.cfg.dry_run:
            preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
            print(f"[discord] DRY-RUN would answer {ev.username}: {preview!r}")
            return
        base_key = session_key_for(ev.guild_id, ev.channel_id, ev.user_id)
        ch_cfg = self._channel_cfg(ev.channel_id)
        job = Job(
            ev=ev,
            prompt=prompt,
            session_key=self._session_key(base_key, ch_cfg),
            ch_cfg=ch_cfg,
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._send_small_reply(ev.channel_id, "Queue is full — try again in a moment.")
            print(f"[discord] queue full, dropped {ev.message_id}")

    # -- interactions (M2, plan §10) --------------------------------------------

    def submit_interaction(self, data: dict) -> None:
        """Gateway-thread side: parse + gate, then hand to the single
        interaction thread (never block the gateway on REST calls)."""
        try:
            inter = parse_interaction(data)
        except Exception as err:  # noqa: BLE001 - malformed dispatch
            print(f"[discord] bad INTERACTION_CREATE: {type(err).__name__}: {err}")
            return
        reason = gate_interaction(
            inter,
            allow_guilds=self.policy.allow_guilds,
            allow_channels=self.policy.allow_channels,
            allow_users=self.policy.allow_users,
            allow_dms=self.policy.allow_dms,
        )
        if reason:
            print(f"[discord] denied interaction {inter.interaction_id}: {reason}")
            return
        cmd = inter.command
        if cmd in ("ask", "think"):
            self._inter_executor.submit(self._inter_defer_and_enqueue, inter)
        elif cmd == "reset":
            self._inter_executor.submit(self._inter_quick, inter, self._run_reset)
        elif cmd == "status":
            self._inter_executor.submit(self._inter_quick, inter, self._run_status)
        elif cmd == "model":
            self._inter_executor.submit(self._inter_quick, inter, self._run_model)
        elif cmd == "soul":
            self._inter_executor.submit(self._inter_quick, inter, self._run_soul)
        else:
            print(f"[discord] interaction for unknown command {cmd!r} — ignored")

    def _inter_defer_and_enqueue(self, inter: Interaction) -> None:
        """/ask + /think: ACK with type 5 (<3s rule), then enqueue a normal
        agent job — cooldown, sessions, semaphore and chunking all apply."""
        defer_data = {"flags": EPHEMERAL} if inter.ephemeral else {}
        try:
            self.rest.interaction_callback(
                inter.interaction_id, inter.token, {"type": CALLBACK_DEFER, "data": defer_data}
            )
        except DiscordRestError as err:
            print(f"[discord] defer failed for {inter.command}: {err}")
            return
        prompt = self.policy.sanitize_prompt(inter.options.get("prompt", ""))
        if not prompt:
            self._inter_followup_text(inter, "Prompt is empty.")
            return
        base_key = session_key_for(inter.guild_id, inter.channel_id, inter.user_id)
        ch_cfg = self._channel_cfg(inter.channel_id)
        job = Job(
            ev=MessageContext(
                message_id=inter.interaction_id,
                channel_id=inter.channel_id,
                guild_id=inter.guild_id,
                user_id=inter.user_id,
                username=inter.username,
                content=prompt,
                is_dm=inter.is_dm,
            ),
            prompt=prompt,
            session_key=self._session_key(base_key, ch_cfg),
            ch_cfg=ch_cfg,
            interaction=inter,
            want_think=(inter.command == "think"),
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._inter_followup_text(inter, "Queue is full — try again in a moment.")
            print(f"[discord] queue full, dropped interaction {inter.interaction_id}")

    def _inter_quick(self, inter: Interaction, handler) -> None:
        """Quick commands: one REST callback (type 4) carries the answer."""
        try:
            content, ephemeral = handler(inter)
            payload: dict = {"content": content[:1900]}
            if ephemeral:
                payload["flags"] = EPHEMERAL
            self.rest.interaction_callback(
                inter.interaction_id, inter.token, {"type": CALLBACK_MESSAGE, "data": payload}
            )
        except DiscordRestError as err:
            print(f"[discord] interaction reply failed: {err}")

    def _inter_followup_text(self, inter: Interaction, content: str) -> None:
        """Ephemeral one-liner via the interaction webhook."""
        try:
            self.rest.followup(
                inter.app_id, inter.token, {"content": content[:1900], "flags": EPHEMERAL}
            )
        except DiscordRestError as err:
            print(f"[discord] interaction followup failed: {err}")

    # -- text commands (R07.33): "/status" typed as a plain message -----------

    _TEXT_QUICK_HANDLERS = {
        "reset": "_run_reset",
        "status": "_run_status",
        "model": "_run_model",
        "soul": "_run_soul",
    }

    def _submit_text_command(self, ev: MessageContext, name: str, options: dict) -> None:
        """Route a text-typed command through the same handlers as native
        interactions. /ask + /think enqueue regular jobs (cooldown, sessions
        and chunking all apply); quick commands run on the interaction thread
        and reply in-channel — there is no interaction webhook to ACK."""
        if self.cfg.dry_run:
            suffix = f" {options}" if options else ""
            print(f"[discord] DRY-RUN would run /{name}{suffix} for {ev.username}")
            return
        inter = Interaction(
            interaction_id=f"text-{ev.message_id}",
            token="",  # never used — text commands reply via send_message
            app_id="",
            guild_id=ev.guild_id,
            channel_id=ev.channel_id,
            user_id=ev.user_id,
            username=ev.username,
            command=name,
            options=options,
        )
        if name in ("ask", "think"):
            prompt = self.policy.sanitize_prompt(options.get("prompt", ""))
            if not prompt:
                self._send_command_reply(ev.channel_id, "Prompt is empty.")
                return
            base_key = session_key_for(ev.guild_id, ev.channel_id, ev.user_id)
            ch_cfg = self._channel_cfg(ev.channel_id)
            job = Job(
                ev=ev,
                prompt=prompt,
                session_key=self._session_key(base_key, ch_cfg),
                ch_cfg=ch_cfg,
                want_think=(name == "think"),
            )
            try:
                self._queue.put_nowait(job)
            except queue.Full:
                self._send_command_reply(ev.channel_id, "Queue is full — try again in a moment.")
                print(f"[discord] queue full, dropped text /{name} from {ev.username}")
            return
        handler = getattr(self, self._TEXT_QUICK_HANDLERS[name])
        self._inter_executor.submit(self._text_quick, inter, handler)

    def _text_quick(self, inter: Interaction, handler) -> None:
        """Run a quick command and answer in-channel (ephemeral is not
        possible for plain messages — the flag is intentionally ignored)."""
        try:
            content, _ephemeral = handler(inter)
            self._send_command_reply(inter.channel_id, content)
        except Exception as err:  # noqa: BLE001 - never kill the executor thread
            print(f"[discord] text /{inter.command} failed: {type(err).__name__}: {err}")
            self._send_command_reply(inter.channel_id, f"Command failed: {type(err).__name__}")

    def _send_command_reply(self, channel_id: str, content: str) -> None:
        """Channel message for text-command answers (no rate guard — these
        are instant lookups, mirroring native interactions which bypass the
        cooldown too)."""
        if self.cfg.dry_run:
            print(f"[discord] DRY-RUN notice would send: {content!r}")
            return
        try:
            self.rest.send_message(channel_id, content[:1900])
        except DiscordRestError as err:
            print(f"[discord] text command reply failed: {err}")

    def _run_reset(self, inter: Interaction) -> tuple[str, bool]:
        from agentkthx.core.persistent_memory import PersistentMemory  # lazy

        # Delete the CURRENT conversation key (prefix + run stamp included) —
        # deleting the bare base key would miss this run's fresh session.
        key = self._session_key(
            session_key_for(inter.guild_id, inter.channel_id, inter.user_id),
            self._channel_cfg(inter.channel_id),
        )
        return handle_reset(inter, session_key=key, delete_fn=PersistentMemory.delete_session)

    def _run_status(self, inter: Interaction) -> tuple[str, bool]:
        ch = self._channel_cfg(inter.channel_id)
        key = session_key_for(inter.guild_id, inter.channel_id, inter.user_id)
        eff_ctx, eff_pred = self._effective_gen_params(
            ch.model or self.cfg.model or _default_model()
        )
        return handle_status(
            inter,
            backend=self.cfg.backend or "default",
            model=ch.model or self.cfg.model or _default_model(),
            soul=ch.soul or self.cfg.soul or "none",
            tools=_tools_display(ch.tools if ch.tools is not None else self.cfg.tools),
            max_steps=self.cfg.max_steps,
            cooldown_s=self.cfg.cooldown_s,
            num_ctx=eff_ctx,
            max_tokens=eff_pred,
            uptime_s=time.monotonic() - (self._started_at or time.monotonic()),
            queue_depth=self._queue.qsize(),
            agent_runs=self.agent_runs,
            session_key=self._session_key(key, ch),
        )

    def _run_model(self, inter: Interaction) -> tuple[str, bool]:
        ch = self._channel_cfg(inter.channel_id)
        current = ch.model or self.cfg.model or _default_model()
        name = str(inter.options.get("name", "")).strip()
        content, ephemeral = handle_model(
            inter, current_model=current, owner=is_owner(inter.user_id, self._owners), name=name
        )
        if name and is_owner(inter.user_id, self._owners):
            self._channel_overrides[inter.channel_id] = ChannelConfig(model=name)
        return content, ephemeral

    def _run_soul(self, inter: Interaction) -> tuple[str, bool]:
        ch = self._channel_cfg(inter.channel_id)
        name = str(inter.options.get("name", "")).strip()
        owner = is_owner(inter.user_id, self._owners)
        valid = _soul_exists(name) if name else True
        content, ephemeral = handle_soul(
            inter,
            current_soul=ch.soul or self.cfg.soul or "none",
            owner=owner,
            name=name,
            validate_fn=_soul_exists,
        )
        if name and valid and owner:
            self._channel_overrides[inter.channel_id] = ChannelConfig(soul=name)
        return content, ephemeral

    # -- workers ---------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._handle_job(job)
            except Exception:  # noqa: BLE001 - a worker must never die
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _handle_job(self, job: Job) -> None:
        """Worker side: cooldown -> typing -> agent run -> chunked reply."""
        ev = job.ev
        rate = self.policy.check_rate(ev.user_id)
        if not rate.allowed:
            wait = f" (retry in {rate.retry_after:.0f}s)" if rate.retry_after else ""
            text = f"Rate limited{wait} — cooldown {self.cfg.cooldown_s:g}s."
            if job.interaction is not None:
                self._inter_followup_text(job.interaction, text)
            else:
                self._send_small_reply(ev.channel_id, text)
            print(f"[discord] cooldown {ev.username}: {rate.retry_after:.1f}s remaining")
            return

        # Typing indicator: guild channels only — DM channels 404 on
        # trigger-typing (R07.33), and typing is cosmetic anyway.
        typing_thread = None
        stop_typing = threading.Event()
        if not ev.is_dm:
            typing_thread = threading.Thread(
                target=self._typing_loop,
                args=(ev.channel_id, stop_typing),
                daemon=True,
                name=f"discord-typing-{ev.channel_id}",
            )
            typing_thread.start()
        try:
            with self._agent_sem:
                agent = self._build_agent(job)
                envelope = self._build_envelope(job)
                self._dbg(
                    f"run start user={ev.username} session={job.session_key} "
                    f"model={getattr(agent, 'model', '?')} "
                    f"max-steps={self.cfg.max_steps} prompt={len(job.prompt)}ch"
                )
                self._dbg(f"envelope:\n{envelope}")
                run = agent.run(envelope)
            self.agent_runs += 1
            self._dbg(
                f"run done success={run.success} steps={len(getattr(run, 'steps', []) or [])} "
                f"tokens={getattr(run, 'total_tokens', '?')} "
                f"{getattr(run, 'total_ms', 0) or 0:.0f}ms"
            )
            if not run.success:
                self._dbg(
                    "incomplete run — check backend errors above "
                    "(core debug echoes prompts/responses per step)"
                )
        except Exception as err:  # noqa: BLE001 - reply one line, log full trace
            traceback.print_exc()
            text = f"Backend error: {type(err).__name__} — try again later."
            if job.interaction is not None:
                self._inter_followup_text(job.interaction, text)
            else:
                self._send_small_reply(ev.channel_id, text)
            print(f"[discord] agent run failed for {ev.username}: {err}")
            return
        finally:
            stop_typing.set()
            if typing_thread is not None:
                typing_thread.join(timeout=3)
        self._send_reply(job, run)

    def _effective_gen_params(self, model: str) -> tuple[int | None, int | None]:
        """Resolved (num_ctx, max_tokens) for this model — explicit cfg wins,
        else backend/model catalog detection (chat parity), cached per model
        so a channel /model override pays the lookup once."""
        if model not in self._gen_params:
            self._gen_params[model] = resolve_gen_params(
                self.cfg.backend, model, self.cfg.num_ctx, self.cfg.max_tokens
            )
        return self._gen_params[model]

    def _build_agent(self, job: Job):
        """Construct the Agent exactly as the CLI would, but session-scoped
        to the Discord channel and deny-all on dangerous tools."""
        from agentkthx.agent import Agent  # lazy: heavy import, worker threads only

        ch = job.ch_cfg
        soul = ch.soul or self.cfg.soul  # None = no soul (no-soul fallback prompt)
        tools_raw: str | list[str] = ch.tools if ch.tools is not None else self.cfg.tools
        tool_list = self.policy.filter_tools(
            tools_raw, soul_allowed=resolve_soul_allowed_tools(soul)
        )
        model = ch.model or self.cfg.model or _default_model()
        num_ctx, max_tokens = self._effective_gen_params(model)
        return Agent(
            model=model,
            backend=self.cfg.backend or None,
            tools=tool_list,
            soul=soul,
            soul_level=2,
            # Discord's own identity (R07.33): replaces the stock no-soul
            # one-liner; a configured soul wins. The core still appends the
            # standard tool section when a channel opts into tools.
            identity_prompt=DISCORD_IDENTITY_PROMPT,
            # No host details in the Discord prompt (shell is excluded on
            # Discord unconditionally, so the OS/shell section is dead weight).
            env_section=False,
            session_id=job.session_key,
            max_steps=self.cfg.max_steps,
            num_ctx=num_ctx,
            num_predict=max_tokens,
            confirm_dangerous=_deny_dangerous,
            debug=self.cfg.debug,
        )

    # -- replies ---------------------------------------------------------------

    def _send_reply(self, job: Job, run) -> None:
        ev = job.ev
        text = (run.final_answer or "").strip()
        if not run.success:
            marker = "(incomplete — maximum steps reached)"
            text = f"{text}\n\n{marker}".strip() if text else marker
        if not text:
            text = "(empty response)"
        if job.want_think:
            reasoning = "\n\n".join(
                step.reasoning_content.strip()
                for step in run.steps
                if getattr(step, "reasoning_content", "") and step.reasoning_content.strip()
            )
            if reasoning:
                if len(reasoning) > 1500:
                    reasoning = reasoning[:1500] + " …(reasoning truncated)"
                text = f"```text\n{reasoning}\n```\n\n{text}"
        try:
            chunks = chunk_reply(text, max_msgs=self.cfg.max_reply_msgs)
            if job.interaction is not None:
                inter = job.interaction
                for chunk in chunks:
                    payload: dict = {"content": chunk}
                    if inter.ephemeral:
                        payload["flags"] = EPHEMERAL
                    self.rest.followup(inter.app_id, inter.token, payload)
            else:
                for chunk in chunks:
                    self.rest.send_message(ev.channel_id, chunk)
            print(
                f"[discord] answered {ev.username} in {ev.channel_id}: "
                f"{len(chunks)} msg(s), {run.total_tokens} tokens, {run.total_ms:.0f}ms"
            )
        except DiscordRestError as err:
            print(f"[discord] reply failed: {err}")

    def _send_small_reply(self, channel_id: str, text: str) -> None:
        """One-line notice, capped at 1/SMALL_REPLY_GAP_S per channel."""
        now = time.monotonic()
        with self._small_lock:
            last = self._small_guard.get(channel_id, 0.0)
            if now - last < SMALL_REPLY_GAP_S:
                return
            self._small_guard[channel_id] = now
        if self.cfg.dry_run:
            print(f"[discord] DRY-RUN notice would send: {text!r}")
            return
        try:
            self.rest.send_message(channel_id, text)
        except DiscordRestError as err:
            print(f"[discord] notice failed: {err}")

    # -- typing + envelope helpers ----------------------------------------------

    def _typing_loop(self, channel_id: str, stop: threading.Event) -> None:
        while True:
            try:
                self.rest.trigger_typing(channel_id)
            except DiscordRestError as err:
                if err.status == 404:
                    # Known transient right after session start (and on some
                    # channel types) — log once per channel, then stay quiet.
                    if channel_id not in self._typing_quiet:
                        self._typing_quiet.add(channel_id)
                        print(f"[discord] typing unavailable in {channel_id} (404) — skipping")
                else:
                    print(f"[discord] typing refresh failed: {err}")
                return
            if stop.wait(TYPING_REFRESH_S):
                return

    def _build_envelope(self, job: Job) -> str:
        """Prompt envelope (plan §8) — a normal user message for the loop."""
        ev = job.ev
        if ev.guild_id:
            label = self._channel_label(ev.channel_id)
        else:
            label = "dm"  # DM channels have no name; skip the REST lookup
        guild = ev.guild_id if ev.guild_id else "dm"
        return f"[Discord] guild={guild} channel={label} " f"user={ev.username}\n{job.prompt}"

    def _channel_label(self, channel_id: str) -> str:
        """#name resolved once per channel (REST), raw id as fallback."""
        with self._label_lock:
            cached = self._label_cache.get(channel_id)
        if cached:
            return cached
        label = f"#{channel_id}"
        try:
            info = self.rest.get_channel(channel_id) or {}
            if info.get("name"):
                label = f"#{info['name']}"
        except DiscordRestError as err:
            print(f"[discord] channel name lookup failed: {err}")
        with self._label_lock:
            self._label_cache[channel_id] = label
        return label


def on_shutdown(context: dict) -> None:
    """Plugin hook: emitted via atexit by the CLI (spec §Hooks)."""
    gateway = _ACTIVE_GATEWAY
    if gateway is not None:
        gateway.stop()
        print("[discord] gateway closed (on_shutdown)")
    pool = _ACTIVE_POOL
    if pool is not None:
        pool.stop()
        print("[discord] responder pool stopped (on_shutdown)")
