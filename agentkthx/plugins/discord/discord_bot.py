"""
AgentKthx Plugin — `agentkthx discord` command (M1 chat responder)

M1 scope (docs/DISCORD_PLUGIN_PLAN.md §15): the bot answers. Message flow
(plan §8): gateway thread -> policy gate -> bounded dispatch queue ->
worker pool -> typing indicator (refreshed ~8s) -> Agent(session_id=...)
run under a global semaphore -> final_answer chunked into <=2000-char
messages -> sequential REST sends.

Safety posture (plan §12): deny-by-default allowlists, per-user cooldowns,
tool policy excluding `shell`/`python_repl`, `confirm_dangerous` denies
everything (no human at the terminal to approve), secrets redacted.

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
from .rest import DiscordRest, DiscordRestError, chunk_reply
from .sessions import (
    ChannelConfig,
    default_discord_json_path,
    prune_discord_sessions,
    resolve_channel_config,
    resolve_soul_allowed_tools,
    session_key_for,
    session_key_with_prefix,
)

# The four intents from plan §5.2. MESSAGE_CONTENT is privileged — the
# portal toggle must be on or the gateway closes with 4014 (fatal).
DISCORD_INTENTS = (
    INTENT_GUILDS | INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT
)

DEFAULT_TOOLS = "calculator,parse_json,todo,web_search,http_get"
DEFAULT_SOUL = "kthx-helper"

TYPING_REFRESH_S = 8.0   # Discord typing state expires after 10s (plan §8)
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
    session_ttl_days: int = 30
    discord_json: str = ""
    soul: str | None = None
    api_mode: str | None = None
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
            tools=_cfg("DISCORD_TOOLS", DEFAULT_TOOLS),
            queue_max=max(1, int(_cfg("DISCORD_QUEUE_MAX", "8") or 8)),
            max_workers=max(1, int(_cfg("DISCORD_MAX_WORKERS", "2") or 2)),
            session_ttl_days=int(_cfg("DISCORD_SESSION_TTL_DAYS", "30") or 30),
            discord_json=_cfg("DISCORD_CONFIG", "") or default_discord_json_path(),
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
    parser.add_argument(
        "--api", default=None, help="API mode for the backend (openre | openai)"
    )
    parser.add_argument("--soul", default=None, help=f"Soul override (default: {DEFAULT_SOUL})")
    parser.add_argument(
        "--tools", default=None, help="Comma-separated tool allowlist override"
    )
    parser.add_argument(
        "--max-steps", type=int, default=None, help="Agent step cap override"
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
        f"tools={cfg.tools} soul={cfg.soul or 'per-channel/' + DEFAULT_SOUL}"
    )
    print(
        f"[discord] pool: workers={cfg.max_workers} queue={cfg.queue_max} "
        f"max-steps={cfg.max_steps} session-ttl={cfg.session_ttl_days}d"
    )

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

    pruned = prune_discord_sessions(cfg.session_ttl_days)
    if pruned:
        print(f"[discord] pruned {len(pruned)} stale session(s) (>{cfg.session_ttl_days}d)")

    pool = ResponderPool(cfg, policy, rest)
    _ACTIVE_POOL = pool
    pool.start()

    def dispatcher(event: str, data: dict) -> None:
        if event == "MESSAGE_CREATE":
            pool.submit_event(context_from_payload(data))
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


# ---------------------------------------------------------------------------
# Responder pool (plan §4: bounded queue + workers + semaphore)
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """One accepted trigger awaiting an agent run."""

    ev: MessageContext
    prompt: str                    # sanitized message text (no envelope)
    session_key: str
    ch_cfg: ChannelConfig


class ResponderPool:
    """
    Owns the bounded dispatch queue and the worker pool.

    - gateway thread calls submit_event() — never blocks (put_nowait)
    - `max_workers` threads pull jobs; agent runs are serialized by a
      semaphore so local backends never face concurrent inference
    - one-line notices (cooldown / queue-full / backend error) are capped
      at 1 per SMALL_REPLY_GAP_S per channel so the bot can't spam
    """

    def __init__(self, cfg: BotConfig, policy: Policy, rest: DiscordRest):
        self.cfg = cfg
        self.policy = policy
        self.rest = rest
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
        self.agent_runs = 0  # observable for tests/status

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        for _ in range(max(1, self.cfg.max_workers)):
            self._executor.submit(self._worker_loop)

    def stop(self) -> None:
        self._stop.set()
        pending = self._queue.qsize()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if pending:
            print(f"[discord] pool stopped with {pending} queued job(s) dropped")

    # -- intake ----------------------------------------------------------------

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
        if self.cfg.dry_run:
            preview = prompt[:80] + ("…" if len(prompt) > 80 else "")
            print(f"[discord] DRY-RUN would answer {ev.username}: {preview!r}")
            return
        base_key = session_key_for(ev.guild_id, ev.channel_id, ev.user_id)
        ch_cfg = resolve_channel_config(ev.channel_id, self.cfg.discord_json)
        job = Job(
            ev=ev,
            prompt=prompt,
            session_key=session_key_with_prefix(base_key, ch_cfg),
            ch_cfg=ch_cfg,
        )
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            self._send_small_reply(
                ev.channel_id, "Queue is full — try again in a moment."
            )
            print(f"[discord] queue full, dropped {ev.message_id}")

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
            self._send_small_reply(
                ev.channel_id,
                f"Rate limited{wait} — cooldown {self.cfg.cooldown_s:g}s.",
            )
            print(f"[discord] cooldown {ev.username}: {rate.retry_after:.1f}s remaining")
            return

        stop_typing = threading.Event()
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
                run = agent.run(self._build_envelope(job))
            self.agent_runs += 1
        except Exception as err:  # noqa: BLE001 - reply one line, log full trace
            traceback.print_exc()
            self._send_small_reply(
                ev.channel_id,
                f"Backend error: {type(err).__name__} — try again later.",
            )
            print(f"[discord] agent run failed for {ev.username}: {err}")
            return
        finally:
            stop_typing.set()
            typing_thread.join(timeout=3)
        self._send_reply(job, run)

    def _build_agent(self, job: Job):
        """Construct the Agent exactly as the CLI would, but session-scoped
        to the Discord channel and deny-all on dangerous tools."""
        from agentkthx.agent import Agent  # lazy: heavy import, worker threads only

        ch = job.ch_cfg
        soul = ch.soul or self.cfg.soul or DEFAULT_SOUL
        tools_raw: str | list[str] = (
            ch.tools if ch.tools is not None else self.cfg.tools
        )
        tool_list = self.policy.filter_tools(
            tools_raw, soul_allowed=resolve_soul_allowed_tools(soul)
        )
        return Agent(
            model=self.cfg.model or _default_model(),
            backend=self.cfg.backend or None,
            tools=tool_list,
            soul=soul,
            soul_level=2,
            session_id=job.session_key,
            max_steps=self.cfg.max_steps,
            confirm_dangerous=_deny_dangerous,
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
        try:
            chunks = chunk_reply(text, max_msgs=self.cfg.max_reply_msgs)
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
        return (
            f"[Discord] guild={guild} channel={label} "
            f"user={ev.username}\n{job.prompt}"
        )

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
