# AgentKthx Discord Integration Plan

**Status: Proposed** (target: R07.33 → R08.2)

A plan for running AgentKthx as a Discord bot via a Plugin Spec v0.2 **feature plugin**, using a **stdlib-only Discord client** (no `discord.py`, no third-party dependencies) so the project's zero-dependency ethos stays intact.

Written by [VTSTech](https://www.vts-tech.org) · [GitHub](https://github.com/VTSTech/AgentKthx)

---

## Table of contents

1. [Summary](#1-summary)
2. [Goals and non-goals](#2-goals-and-non-goals)
3. [Why a feature plugin](#3-why-a-feature-plugin)
4. [Architecture overview](#4-architecture-overview)
5. [Stdlib-only Discord client](#5-stdlib-only-discord-client)
6. [Plugin layout and manifest](#6-plugin-layout-and-manifest)
7. [Component specifications](#7-component-specifications)
8. [Message flow](#8-message-flow)
9. [Sessions, souls, and memory](#9-sessions-souls-and-memory)
10. [Slash commands (M2)](#10-slash-commands-m2)
11. [Image generation relay (M3)](#11-image-generation-relay-m3)
12. [Security and abuse controls](#12-security-and-abuse-controls)
13. [Configuration reference](#13-configuration-reference)
14. [CLI surface](#14-cli-surface)
15. [Milestones and acceptance criteria](#15-milestones-and-acceptance-criteria)
16. [Test plan](#16-test-plan)
17. [Documentation deliverables](#17-documentation-deliverables)
18. [Risks and mitigations](#18-risks-and-mitigations)
19. [Open questions](#19-open-questions)

---

## 1. Summary

AgentKthx already runs as a CLI (`agentkthx chat` / `agentkthx agent`) against 15+ local and cloud backends. This plan adds a Discord presence: a long-running `agentkthx discord` command that connects to the Discord **Gateway v10** over WebSocket, watches for @mentions / replies / DMs, feeds each trigger message through the existing `Agent` agentic loop, and posts the final answer back to the channel.

The integration ships as a **feature plugin** (`agentkthx/plugins/discord/`) — no core code changes — exactly like the TurboQuant (`turbo`) precedent. It reuses, unchanged:

- `Agent` (`agentkthx/agent.py`) and its unified agentic loop, tool execution, compaction, and error recovery
- The plugin system (`Plugin Spec v0.2`): manifest discovery, `register()`/`unregister()`, `register_cli_command`, config `env_prefix` + defaults, hooks
- `PersistentMemory` sessions (SQLite) so each Discord channel keeps its own conversation
- Souls (`soul.json` v0.5) so channels can carry distinct personas
- The dangerous-tool confirmation gate (`confirm_dangerous`) and audit log (`~/.agentkthx/audit.log`)

Everything Discord-specific (Gateway WebSocket, REST client, rate limits, allowlists) is written against **Python stdlib only** (`socket`, `ssl`, `urllib.request`, `threading`, `json`, `base64`, `struct`, `hashlib`, `concurrent.futures`).

Estimated total: **~1,800 lines** of plugin code + **~900 lines** of tests across five milestones (M0–M4).

---

## 2. Goals and non-goals

### Goals (v1)

| # | Goal |
|---|------|
| G1 | `agentkthx discord` — a long-running bot process answering @mentions, direct replies to the bot, and DMs through the configured backend |
| G2 | Per-channel persistent sessions via `PersistentMemory` (`/reset` support in M2) |
| G3 | Deny-by-default security: guild/channel/user allowlists, per-user cooldowns, queue depth caps, restricted default toolset |
| G4 | Zero new dependencies — minimal RFC 6455 WebSocket client + REST client in stdlib |
| G5 | Plugin Spec v0.2 conformance: manifest, lazy imports, `register()`/`unregister()`, secrets guard compliance |
| G6 | Slash commands: `/ask`, `/model`, `/think`, `/soul`, `/reset`, `/status` (M2) |
| G7 | Image relay: Stable Diffusion backend artifacts posted as Discord attachments (M3) |
| G8 | Offline-first test suite (no network in CI) + opt-in live probe script |

### Non-goals (v1)

| # | Non-goal | Rationale |
|---|----------|-----------|
| N1 | Voice channels / STT / TTS | Different Gateway surface (voice UDP), large scope; revisit post-R08 |
| N2 | Embeds, buttons, modals beyond basic interaction ACKs | Cosmetic; plain text + code fences cover v1 |
| N3 | Multi-server sharding | Single-session bots serve small deployments fine; sharding adds session management complexity |
| N4 | Replacing the CLI | Discord is a *surface* over the same `Agent`; CLI remains primary |
| N5 | `discord.py` support | See §5 for the stdlib decision |

---

## 3. Why a feature plugin

The repo already has the exact precedent: the `turbo` plugin registers the `agentkthx turbo` subcommand via `provides.cli_commands`, and the `acp` plugin wires external reporting without touching core. Discord follows the same shape:

- **`type: feature`** (not `backend` — Discord is not an inference backend)
- **`provides.cli_commands: ["discord"]`** — the loader's `register_cli_command(name, handler, setup_parser, *, plugin=...)` wires `agentkthx discord` into the argparse subparsers with zero parser.py edits
- **`config.env_prefix: "DISCORD"`** — defaults merged by `config.py` at init; real env vars override
- **`provides.hooks: {"on_shutdown": "discord_bot.on_shutdown"}`** — graceful gateway close on interpreter exit
- No `provides.backends` / `provides.tools` in M0/M1 — the bot *consumes* agents, it does not provide inference. (M3 optionally registers a `discord-send` tool so agents can push proactive messages; declared in `provides.tools` then.)

Benefits of the plugin route:

1. **Users who never touch Discord pay nothing** — the plugin lazy-imports `discord_bot.py` only when `agentkthx discord` runs (Plugin Spec §Entrypoint: "lazy imports are RECOMMENDED").
2. **`plugins --load/--unload/--reload/--json` management works for free** — including the sha256 pin option for verified installs (SEC-06).
3. **Removal is trivial** — `unregister()` pops the CLI command and hook; no core surgery.
4. **External variant possible** — the same plugin dir can be dropped into `~/.agentkthx/plugins/` (or `$AGENTKTHX_PLUGIN_PATH`) by fork maintainers.

---

## 4. Architecture overview

### Process model

`agentkthx discord` is one process with three thread groups:

```
┌────────────────────────────────────────────────────────────────────┐
│ agentkthx discord  (main process)                                  │
│                                                                    │
│  ┌──────────────────┐        ┌──────────────────────────────────┐  │
│  │ Gateway thread   │        │ Worker pool (ThreadPoolExecutor) │  │
│  │ (main thread)    │        │ DISCORD_MAX_WORKERS, default 2   │  │
│  │                  │        │                                  │  │
│  │ socket/ssl read  │        │ per MESSAGE_CREATE:              │  │
│  │ frame decode     │──enqueue─▶ policy.check()                │  │
│  │ heartbeat timer  │  queue  │  agent.run(prompt)               │  │
│  │ resume/backoff   │        │  rest.send(reply chunks)         │  │
│  └──────────────────┘        └──────────────────────────────────┘  │
│           │                                   │                    │
│  ┌────────▼─────────┐              ┌──────────▼───────────────┐   │
│  │ Discord Gateway  │              │ Discord REST (urllib)    │   │
│  │ wss v10          │              │ POST /channels/../messages│  │
│  └──────────────────┘              │ POST .../trigger-typing  │   │
│                                    └──────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Shared: get_backend() instance(s), ToolRegistry, audit log   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### Threading rules

| Thread | Owns | Must not |
|--------|------|----------|
| Main (gateway) | Gateway socket, heartbeat timer thread, resume state machine, event dispatch | Run agents, touch REST send |
| Heartbeat timer | `threading.Event.wait(interval)` loop sending op 1 heartbeats | Block the socket reader |
| Workers (pool) | `agent.run()`, REST sends, typing refresh | Share `Agent`/memory instances across threads |

Backpressure: the dispatch queue is bounded (`DISCORD_QUEUE_MAX`, default 8). When full, the bot posts a one-line "queue is full, try again shortly" reply and drops the event — a flooded channel must never wedge the gateway loop (the gateway thread only ever does `queue.put_nowait`).

Concurrency guard: a process-wide `threading.Semaphore` (default 1 for local backends, 2 for cloud — keyed on `backend.is_cloud`) serializes agent runs so a burst of mentions can't hammer a local 4-bit model.

### Agent construction

The bot bypasses `_build_agent()` (that path prints CLI session headers/footers) and constructs `Agent` directly, following `examples/00_basic_agent.py`:

```python
from agentkthx import Agent
from agentkthx.backends import get_backend

backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
agent = Agent(
    model=model,
    backend=backend,
    tools=tool_registry,          # filtered per §12 tool policy
    soul=soul_path,               # per-channel override or None
    soul_level=2,
    session_id=session_key,       # "discord-g{guild}-c{channel}" → PersistentMemory
    max_steps=discord_max_steps,  # default 5 on Discord (vs CLI 10): chat turns must stay snappy
    confirm_dangerous=_deny_all,  # §12: no interactive confirmation over Discord
)
run = agent.run(prompt)           # AgentRun.final_answer → chunked reply
```

A new `Agent` per request keeps memory instances single-owner (no cross-thread `PersistentMemory` sharing). `backend` and the `ToolRegistry` are shared read-only; both are already used concurrently by `orchestrator.py`'s ThreadPoolExecutor path, so the pattern is precedented.

---

## 5. Stdlib-only Discord client

**Decision: no `discord.py`.** Reasons: (a) the README leads with *"Zero dependencies — Uses Python stdlib only (urllib for HTTP)"*; (b) the plugin spec's config model is env-based and dependency-free; (c) Discord's protocol surface for a text bot is small — one WebSocket + six REST endpoints.

### 5.1 REST client (`rest.py`)

Pure `urllib.request` against `https://discord.com/api/v10`:

| Endpoint | Use |
|----------|-----|
| `POST /channels/{id}/messages` | Reply (JSON body; multipart when attachments exist) |
| `POST /channels/{id}/trigger-typing` | Typing indicator (empty 200) |
| `GET /users/@me` | Startup self-check, resolve bot username |
| `PUT /applications/{app_id}/commands` | Slash command registration (M2) |
| `POST /webhooks/{app_id}/{token}` | Interaction followups (M2) |
| `PATCH /channels/{id}/messages/{msg_id}` | Edit-in-place for `/status` progress (M2, optional) |

Requirements:

- **Auth**: `Authorization: Bot <token>` header; token read from `DISCORD_BOT_TOKEN` only.
- **429 handling**: honor JSON `retry_after` (sleep + one retry); track `X-RateLimit-Remaining`/`Bucket` headers per route bucket; on `X-RateLimit-Global: true`, pause all REST sends via a shared `threading.Lock` + timestamp. Never hammer: no automatic third retry within the same call.
- **Chunking**: Discord hard-limits messages to **2000 chars**. `chunk_reply()` splits on paragraph boundaries, then sentences; never splits inside an unclosed code fence (close it and reopen on the next chunk); caps at `DISCORD_MAX_REPLY_MSGS` (default 3) with a trailing `…(truncated, ask in DM for full output)`.
- **Errors**: non-2xx → `DiscordRestError(status, code, message)`; 401/403 are fatal at startup (bad token / missing intent), logged once.
- **Redaction**: any exception text containing the token is scrubbed before logging (`_redact()` in `rest.py`, unit-tested).

### 5.2 Gateway client (`gateway.py`) — minimal RFC 6455 over `ssl`

Python's stdlib has no WebSocket client, so `gateway.py` implements the minimal subset Discord needs (~350 lines):

**Handshake** (stdlib `socket` + `ssl`):

```
GET /?v=10&encoding=json HTTP/1.1
Host: gateway.discord.gg
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <base64(16 random bytes)>
Sec-WebSocket-Version: 13
```

Verify `Sec-WebSocket-Accept == base64(sha1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))` before proceeding; reject on mismatch.

**Frame codec** (RFC 6455 §5.2):

- Client→server frames **MUST** be masked (random 4-byte key, `struct.pack`/`bytearray`).
- Opcodes: text `0x1`, close `0x8`, ping `0x9`, pong `0xA`. Binary `0x2` is not requested (no `compress` query param → no `zlib-stream` payload) — text-only keeps the parser simple; this is the single biggest complexity saver vs. full clients.
- Payload length: 7-bit / 16-bit (126) / 64-bit (127) forms; reject lengths > 2^31 (Discord payloads for text events are far smaller).
- Server pings → answer with matching pong payloads; server pongs are ignored.
- Fragmented messages (FIN=0 continuation) are assembled before JSON parse — rare, but Discord does fragment large `READY` payloads.

**Discord layer** (JSON `{"op", "d", "s", "t"}`):

| op | Meaning | Bot behavior |
|----|---------|--------------|
| 10 | Hello | Read `d.heartbeat_interval` (~41.25s), start heartbeat timer, send Identify or Resume |
| 1 | Heartbeat | Send op 1 immediately with last `seq` |
| 11 | Heartbeat ACK | Mark connection healthy (reset watchdog) |
| 0 | Dispatch | Route by `t`: `READY`, `MESSAGE_CREATE`, `INTERACTION_CREATE`, `RESUMED` |
| 7 | Reconnect | Close 1000, reconnect + Resume |
| 9 | Invalid Session | `d==true` → Resume; `d==false` → fresh Identify (new session) |
| 2 | Identify | — (we send) |
| 6 | Resume | — (we send) |

**Identify payload**:

```json
{
  "op": 2,
  "d": {
    "token": "…",
    "intents": 1 << 0 | 1 << 9 | 1 << 12 | 1 << 15,
    "properties": {"os": "linux", "browser": "AgentKthx", "device": "AgentKthx"}
  }
}
```

Intents: `GUILDS` (1<<0), `GUILD_MESSAGES` (1<<9), `DIRECT_MESSAGES` (1<<12), `MESSAGE_CONTENT` (1<<15, **privileged** — must be toggled in the Developer Portal; see §12 and §18).

**Resume state machine**: persist `session_id` (from `READY`) and last `seq` (from every op 0). On any drop: exponential backoff `min(30, 1.5^n) + jitter` seconds, then Resume (op 6 with token/session/seq). Fatal close codes stop the bot with a clear message:

- `4004` — invalid token
- `4013` — invalid intents
- `4014` — disallowed intents (MESSAGE_CONTENT not enabled in portal)

**Watchdog**: if no op 0/11 arrives within `2 × heartbeat_interval`, force close and resume — catches half-open TCP silently dropped by NATs.

### 5.3 Why this is testable offline

The frame codec is a pure function pair (`encode_frame`, `decode_stream` over a `bytearray` buffer) — unit-tested against hand-built RFC 6455 vectors, no network. The event loop talks to a `Transport` interface (two implementations: real SSL socket, and a loopback `FakeTransport` used by the offline end-to-end test in §16).

---

## 6. Plugin layout and manifest

```
agentkthx/plugins/discord/
├── plugin.json        # Manifest (required, v0.2 form)
├── __init__.py        # register() / unregister() — lazy imports only
├── discord_bot.py     # Bot: gateway lifecycle + event dispatch + agent invocation
├── gateway.py         # RFC 6455 client + Discord op layer (Transport interface)
├── rest.py            # REST client: send/chunk/typing/upload, bucket rate limiter
├── policy.py          # Allowlists, cooldowns, queue caps, tool policy
├── sessions.py        # channel → session_key / soul / toolset resolution
├── commands.py        # (M2) slash command registration + interaction handling
└── attachments.py     # (M3) multipart upload of SD artifacts
```

`plugin.json` (v0.2 form; secrets guard compliant — `DISCORD_BOT_TOKEN` default is the empty string):

```json
{
  "$schema": "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/schemas/v0.2/plugin.schema.json",
  "name": "discord",
  "version": "0.1.0",
  "description": "Discord gateway integration - run AgentKthx as a Discord bot (stdlib-only client)",
  "author": { "name": "VTSTech", "url": "https://www.vts-tech.org" },
  "license": "MIT",
  "extensions": {
    "org.vts-tech.agentkthx": {
      "display_name": "Discord Bot Gateway",
      "type": "feature",
      "entrypoint": "__init__",
      "depends": [],
      "optional_depends": [],
      "config": {
        "env_prefix": "DISCORD",
        "defaults": {
          "DISCORD_BOT_TOKEN": "",
          "DISCORD_MAX_WORKERS": "2",
          "DISCORD_QUEUE_MAX": "8",
          "DISCORD_USER_COOLDOWN_S": "10",
          "DISCORD_MAX_PROMPT_CHARS": "1500",
          "DISCORD_MAX_REPLY_MSGS": "3",
          "DISCORD_MAX_STEPS": "5",
          "DISCORD_TOOLS": "calculator,parse_json,todo,web_search,http_get",
          "DISCORD_ALLOW_DMS": "false"
        }
      },
      "provides": {
        "cli_commands": ["discord"],
        "hooks": { "on_shutdown": "discord_bot.on_shutdown" }
      },
      "compatibility": { "agentkthx": ">=0.7.32" }
    }
  }
}
```

Notes:

- Name `discord` satisfies the manifest constraints (lowercase, no `--`/`..`, matches directory).
- `pyproject.toml` already ships `plugins/*/plugin.json` as package-data — no packaging change (verify at M0).
- `__init__.py` pattern:

```python
def register(manager) -> None:
    from .discord_bot import cmd_discord, setup_parser, on_shutdown  # lazy
    manager.register_cli_command("discord", cmd_discord, setup_parser, plugin="discord")
    manager.register_hook("on_shutdown", on_shutdown, plugin="discord")

def unregister(manager) -> None:
    manager.unregister_cli_command("discord")
    manager.unregister_hook("on_shutdown", on_shutdown_ref)
```

---

## 7. Component specifications

### 7.1 `gateway.py` — Gateway transport

```python
class Transport:                 # seam for offline tests
    def connect(self, host, port) -> None: ...
    def send_text(self, s: str) -> None: ...
    def recv_text(self, timeout: float) -> str | None: ...
    def close(self, code: int = 1000) -> None: ...

class GatewayClient:
    def __init__(self, token, intents, on_dispatch: Callable[[str, dict], None],
                 transport: Transport | None = None): ...
    def run_forever(self) -> None:      # blocking; reconnect/resume inside
    def stop(self) -> None:             # thread-safe close (Ctrl+C, on_shutdown hook)
    # internals: _handshake, _encode_frame, _decode_stream, _identify,
    #            _resume, _heartbeat_loop (timer thread), _watchdog
```

State exposed to the bot: `bot_user_id`, `session_id`, `last_seq`, `connected_at`.

### 7.2 `rest.py` — REST + rate limiter

```python
class DiscordRest:
    def __init__(self, token: str): ...
    def send_message(self, channel_id: str, content: str,
                     files: list[tuple[str, bytes, str]] | None = None) -> dict: ...
    def trigger_typing(self, channel_id: str) -> None: ...
    def register_commands(self, app_id: str, commands: list[dict]) -> None:  # M2
    def followup(self, app_id: str, interaction_token: str, payload: dict) -> None:  # M2
    # internal: _request(method, path, body, files) with bucket limiter, 429 retry,
    #            _redact(token) on all error paths
```

`chunk_reply(text, max_len=2000, max_msgs) -> list[str]` lives here (pure function, heavily unit-tested).

### 7.3 `policy.py` — gatekeeper (pure logic, no I/O)

```python
@dataclass
class Decision:
    allowed: bool
    reason: str = ""          # stable machine reasons, unit-asserted

class Policy:
    def check_event(self, ev: MessageContext) -> Decision: ...   # allowlist + triggers
    def check_rate(self, user_id: str, now: float) -> Decision:  # cooldown bucket
    def filter_tools(self, requested: str, channel: str) -> list[str]:
    def sanitize_prompt(self, text: str) -> str:                 # length cap, strip @everyone
```

Allowlists are deny-by-default. Trigger rules (§8) also live here so they're testable without Discord.

### 7.4 `sessions.py` — identity mapping

```python
def session_key(ev) -> str:
    # guild channel:  f"discord-g{guild_id}-c{channel_id}"
    # DM:             f"discord-dm-{user_id}"
def resolve_channel_config(channel_key: str, cfg_path) -> ChannelConfig:
    # merges ~/.agentkthx/discord.json (soul, tools, system prefix) over env defaults
```

Session keys are chosen to be visible and greppable in `agentkthx sessions` output and `~/.agentkthx/` SQLite store.

### 7.5 `discord_bot.py` — the glue

Owns: CLI handler (`cmd_discord(args)`), backend bootstrap, worker pool, dispatch queue, typing refresh thread, `on_shutdown` hook (gateway `stop()` + pool drain with timeout). ~400 lines.

---

## 8. Message flow

### Trigger rules (M1 — `policy.check_event`)

| Signal | Acts? | Notes |
|--------|-------|-------|
| @mention of the bot anywhere in a guild message | ✅ | `mention_roles`/`mentions` contains `bot_user_id` |
| Reply to a bot-authored message | ✅ | `referenced_message.author.id == bot_user_id` |
| DM to the bot | ✅ if `DISCORD_ALLOW_DMS=true` | else one-time polite refusal |
| Any other guild message | ❌ ignored | not even fetched (gateway filters via intents) |

Additional guards: ignore the bot's own messages and other bots (`author.bot`), ignore empty-content pings, strip the @mention from the prompt text, collapse whitespace, cap length (`DISCORD_MAX_PROMPT_CHARS`), neutralize `@everyone`/`@here` pings in both prompt echo and replies.

### Prompt envelope

```
[Discord] guild={guild_name} channel=#{channel_name} user={display_name}
{message text}
```

The envelope is a normal user message — the agentic loop, ReAct prompting, tool execution, and error recovery are exactly the CLI path (ARCH.md §Data Flow). Nothing Discord-specific enters the system prompt.

### Reply path

1. On accept: `POST .../trigger-typing` immediately; a worker-local timer re-triggers every ~8s while the agent runs (Discord typing expires after 10s).
2. `agent.run(prompt)` → `AgentRun`.
3. On success: `run.final_answer` → `chunk_reply()` → sequential `send_message` calls (bucket limiter spaces them).
4. On `max_steps` exhaustion: post the status + partial answer marker, exactly mirroring CLI behavior ("incomplete — maximum steps reached").
5. On backend/API exception: post one short line (`Backend error: <exception type> — try again later`), full traceback to stdout log only; `on_error` semantics preserved since we use `Agent.run` unmodified.
6. On policy denial: silent for non-trigger messages; one-line reason for cooldown/queue-full denials (rate-limit-safe: the reply itself is exempted from the cooldown so users aren't left hanging, but is capped at 1/10s per channel by the same bucket).

---

## 9. Sessions, souls, and memory

- Every trigger message maps to `session_key = discord-g{guild}-c{channel}` (or `discord-dm-{user}`) and is passed to `Agent(session_id=...)` → `PersistentMemory` loads prior turns automatically. Channels therefore have real, resumable conversations.
- `/reset` (M2) deletes the session row via the same store the `sessions` CLI command manages, then confirms.
- Per-channel persona and toolset overrides live in `~/.agentkthx/discord.json`:

```json
{
  "channels": {
    "123456789012345678": {
      "soul": "kthx-helper",
      "tools": ["calculator", "parse_json", "todo"],
      "session_prefix": "support"
    },
    "234567890123456789": { "soul": "kthx-trading" }
  }
}
```

- Souls resolve through the existing souls loader (`agentkthx/souls/…`, `--soul`-compatible names); `allowedTools` from `soul.json` intersects with the Discord tool policy (§12) — the stricter set wins.
- Sessions appear in `agentkthx sessions` like any CLI session; operators can inspect/prune them with existing tooling. `DISCORD_SESSION_TTL_DAYS` (default 30) prunes stale Discord sessions at startup.

---

## 10. Slash commands (M2)

Registration: on startup when `DISCORD_REGISTER_SLASH=true`, `PUT /applications/{app_id}/commands` (global; guild-scoped list via `discord.json` for testing). Interactions arrive as `INTERACTION_CREATE` dispatches.

Rule: **ACK within 3 seconds** — every handler replies `type 5` (deferred channel message with source) first, then does the work and answers via `POST /webhooks/{app_id}/{interaction_token}`.

| Command | Behavior | Backing |
|---------|----------|---------|
| `/ask <prompt>` | Same as mention, but explicit; optional `ephemeral=true` responses | Agent.run |
| `/think <prompt>` | Sets `--think`-equivalent display of reasoning_content | Agent run flag |
| `/model [name]` | Show or switch session model (validated against backend catalog) | `apply_model_switch` path |
| `/soul [name]` | Show or set channel soul (from `agentkthx souls` list) | souls loader |
| `/reset` | Clear this channel's PersistentMemory session | sessions store |
| `/status` | Backend, model, uptime, queue depth, last-run stats | in-process counters |

`/model` and `/soul` are **owner-gated** (`DISCORD_OWNER_IDS`, comma-separated) — everyone else gets an ephemeral "insufficient permission".

---

## 11. Image generation relay (M3)

When the effective backend is `stable-diffusion` (`--backend sd`) for a channel:

1. The flattened conversation is the prompt (mirrors CLI SD behavior — harness prompt suppressed).
2. The backend writes the PNG under `AGENTKTHX_ARTIFACTS_DIR` (default `./generated/`).
3. `attachments.py` uploads it as a multipart attachment: `multipart/form-data` with a `payload_json` part + `files[0]` part — hand-rolled with `uuid4` boundary (stdlib only), reusing `rest.py`'s bucket limiter.
4. Reply text carries the `[image saved: <path>]` line as today, plus the attachment.

Additionally, M3 registers one plugin tool (`discord-send`, declared in `provides.tools`) so an agent running anywhere (CLI included) can proactively post a message to an allowlisted channel. Optional; disabled unless `DISCORD_ENABLE_SEND_TOOL=true`.

---

## 12. Security and abuse controls

| Control | Mechanism |
|---------|-----------|
| **Deny-by-default allowlists** | `DISCORD_ALLOW_GUILDS`, `DISCORD_ALLOW_CHANNELS`, optional `DISCORD_ALLOW_USERS`; empty guild allowlist = bot stays silent everywhere. Enforced in `policy.check_event` before queueing. |
| **Privileged intent hygiene** | Bot requests only the 4 intents in §5.2. `MESSAGE_CONTENT` is required for mention-triggered content; if the portal toggle is missing, the gateway closes with `4014` and the bot prints a fix-it message (no retry loop). |
| **Tool policy** | Default Discord toolset excludes `shell` and `python_repl`: `DISCORD_TOOLS=calculator,parse_json,todo,web_search,http_get`. Per-channel `discord.json` can only *narrow*, never widen beyond a new owner-only override `DISCORD_UNSAFE_TOOLS=true` (default false, prints a banner warning when set). |
| **Dangerous-tool confirmation** | `Agent(confirm_dangerous=lambda name, args: False)` by default — `shell` can never fire from a Discord trigger even if misconfigured, because there is no human at a terminal to press `y`. Owner-approval flow (reply `kthx approve <nonce>`) is a M4 stretch goal. |
| **Audit continuity** | Any audited tool that does execute still lands in `~/.agentkthx/audit.log` (R04.2 machinery untouched). Discord run metadata (guild/channel/user id hash, decision, duration) appends to a plugin-local JSONL at `~/.agentkthx/discord_audit.log` — user IDs hashed with a per-install random salt. |
| **Rate limits** | Per-user cooldown (`DISCORD_USER_COOLDOWN_S`, default 10s), one concurrent run per user, global queue cap (`DISCORD_QUEUE_MAX`), prompt length cap, reply chunk cap. |
| **Secrets hygiene** | Token only via `DISCORD_BOT_TOKEN` env (manifest default stays empty per the v0.2 secrets guard). `_redact()` scrubs token strings from every log/exception path (unit-tested). Interaction tokens are never logged. |
| **Prompt-injection posture** | Foreign Discord messages are untrusted input: they are delivered inside the user message (never concatenated into the system prompt), `@everyone`/`@here` is neutralized, and the tool policy bounds blast radius. Documented in docs/DISCORD.md. |
| **Single-session discipline** | One process per token (Discord enforces this per session anyway); `discord.json` documents it; duplicate-start detection via `GET /users/@me` + a lockfile `~/.agentkthx/discord.lock` (stale-lock safe). |

---

## 13. Configuration reference

### Environment variables (`env_prefix: DISCORD`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | *(empty)* | Bot token — **required**, env only, never in files |
| `DISCORD_APP_ID` | *(empty)* | Application ID (required for slash commands) |
| `DISCORD_ALLOW_GUILDS` | *(empty)* | Comma-separated guild IDs allowed to invoke the bot |
| `DISCORD_ALLOW_CHANNELS` | *(empty)* | Optional narrower channel allowlist |
| `DISCORD_ALLOW_USERS` | *(empty)* | Optional user allowlist (further restriction) |
| `DISCORD_ALLOW_DMS` | `false` | Permit DM conversations |
| `DISCORD_OWNER_IDS` | *(empty)* | Users permitted to `/model`, `/soul`, unsafe overrides |
| `DISCORD_MAX_WORKERS` | `2` | Worker threads running agent loops |
| `DISCORD_QUEUE_MAX` | `8` | Max queued triggers before "busy" replies |
| `DISCORD_USER_COOLDOWN_S` | `10` | Per-user minimum seconds between runs |
| `DISCORD_MAX_PROMPT_CHARS` | `1500` | Prompt length cap after @mention strip |
| `DISCORD_MAX_REPLY_MSGS` | `3` | Max 2000-char messages per reply |
| `DISCORD_MAX_STEPS` | `5` | Agentic loop steps on Discord (snappier than CLI's 10) |
| `DISCORD_TOOLS` | `calculator,parse_json,todo,web_search,http_get` | Default tool allowlist |
| `DISCORD_UNSAFE_TOOLS` | `false` | Owner opt-in allowing wider toolsets (warns loudly) |
| `DISCORD_REGISTER_SLASH` | `false` | Register global slash commands at startup (M2) |
| `DISCORD_ENABLE_SEND_TOOL` | `false` | Register the `discord-send` tool (M3) |
| `DISCORD_SESSION_TTL_DAYS` | `30` | Prune stale `discord-*` sessions at startup |
| `DISCORD_CONFIG` | `~/.agentkthx/discord.json` | Per-channel overrides path |

Standard shared knobs still apply: `--backend`, `--model`, `--api`, `--soul`, `AGENTKTHX_*` retries, and all backend env vars (`ZAI_API_KEY`, `OPENROUTER_API_KEY`, …) — the bot is just another Agent consumer.

---

## 14. CLI surface

```
agentkthx discord [--backend NAME] [--model NAME] [--api MODE] [--soul NAME]
                  [--tools LIST] [--max-steps N] [--dry-run] [--register-commands]
```

| Flag | Behavior |
|------|----------|
| `--dry-run` | Full gateway connect + policy evaluation, but triggers print to stdout instead of running agents / calling REST. The M0/M1 test seam and the safe way to validate allowlists. |
| `--register-commands` | One-shot slash registration then exit (M2) |
| others | Same semantics as `chat` (reuses the shared option block pattern from `parser.py`) |

Startup output mirrors CLI conventions (banner → session-style header: backend, model, tools, allowlist summary, gateway URL, "Ctrl+C to stop").

---

## 15. Milestones and acceptance criteria

### M0 — Plugin skeleton + gateway client (target R07.33)

Deliverables: plugin dir + manifest, `gateway.py` (Transport seam, frame codec, heartbeat, resume), `policy.py`, `rest.py` (message send + 429), CLI command stub, `--dry-run`.

**AC:**
- [ ] `agentkthx plugins` lists `discord` as loaded; `--help` shows `discord`
- [ ] `agentkthx discord --dry-run` against a real token: connects, identifies, heartbeats 30 min without drop, survives a manual kill -STOP of the socket via watchdog resume
- [ ] Frame codec passes RFC 6455 unit vectors (mask, 7/16/64-bit lengths, fragmentation, close)
- [ ] Zero network in unit tests; offline suite green in CI

### M1 — Chat responder MVP (target R07.34)

Deliverables: full `discord_bot.py` (dispatch queue, workers, typing, chunked replies), `sessions.py` + PersistentMemory wiring, allowlists + cooldowns enforced, tool policy + `confirm_dangerous` deny-all, `scripts/probe_discord.sh`.

**AC:**
- [ ] End-to-end: @mention → typed indicator → correct reply, on one cloud backend (ZAI) and one local backend (Ollama)
- [ ] Reply-to-bot and DM triggers behave per §8 matrix; non-trigger messages never wake the agent
- [ ] Unlisted guild/channel/user gets zero responses; cooldown and queue-full paths verified by unit test and live probe
- [ ] Two rapid mentions from the same user → second one held by cooldown (no double agent runs)
- [ ] Sessions persist: follow-up "what did I just ask?" in-channel answered correctly after bot restart
- [ ] 5/5 pass of `scripts/probe_discord.sh`

### M2 — Slash commands + channel souls (target R08.0)

**AC:**
- [ ] Six commands registered; every interaction ACKed < 3s (defer + followup)
- [ ] `/reset` clears the channel session; next message starts fresh (verified in SQLite)
- [ ] `/soul kthx-trading` switches persona for that channel only; other channels unaffected
- [ ] `/model` and `/soul` rejected for non-owners with ephemeral error
- [ ] `docs/DISCORD.md` published; README docs table updated

### M3 — Image relay + send tool (target R08.1)

**AC:**
- [ ] `--backend sd` channel: mention → PNG arrives as Discord attachment with `[image saved: …]` line
- [ ] `--max-steps` remap to sample steps behaves like the CLI path (R07.32 semantics)
- [ ] `discord-send` tool registered only when `DISCORD_ENABLE_SEND_TOOL=true`; unload removes it from the registry

### M4 — Hardening + promotion (target R08.2)

**AC:**
- [ ] 72-hour soak: zero unclean exits, heartbeat resume count logged and bounded
- [ ] `/status` reflects queue depth and last-run stats; owner approval flow for dangerous tools shipped or explicitly descoped
- [ ] `docs/SUPPORT.md` tier assigned after 5/5 smoke on two backends
- [ ] Full CHANGELOG entry; test count and coverage badge updated

---

## 16. Test plan

Follows repo conventions (`tests/test_*.py`, fixtures in `conftest.py`, offline-first, `scripts/probe_*` for live checks):

| File | Covers |
|------|--------|
| `tests/test_discord_gateway.py` | RFC 6455 frame encode/decode vectors (masking, length forms, fragmentation, ping/pong/close); Hello→Identify→READY sequence on `FakeTransport`; heartbeat scheduling; resume after simulated drop; fatal close codes 4004/4013/4014 |
| `tests/test_discord_rest.py` | `http.server`-based stub: JSON send, multipart upload (M3), 429 `retry_after` single-retry, bucket spacing, `_redact()` on token-bearing errors, `chunk_reply()` edge cases (2000/2001 chars, code fence integrity, cap + truncation marker) |
| `tests/test_discord_policy.py` | Trigger matrix (mention/reply/DM/self/bot/other), allowlist precedence (guild > channel > user), cooldown bucket math, tool filtering incl. soul `allowedTools` intersection, prompt sanitize (@mention strip, @everyone neutralize, length cap) |
| `tests/test_discord_sessions.py` | session_key formats, `discord.json` merge/precedence, TTL pruning |
| `tests/test_discord_bot_e2e.py` | Offline end-to-end: fake gateway server (same frame codec, server side) drives READY → MESSAGE_CREATE → `FakeAgent.run()` → stub REST records reply chunks + typing calls; queue-full and cooldown paths |
| `tests/test_discord_plugin.py` | Manifest validates against `schemas/v0.2/plugin.schema.json`; register/unregister round-trip; lazy import (no `discord_bot` import at discovery) |

Test doubles (in `conftest.py`): `FakeTransport` (scripted frames), `FakeAgent` (records prompts, returns canned `AgentRun`s incl. an error case), `StubRest` (records calls, programmable 429s).

`scripts/probe_discord.sh` (live, opt-in; mirrors `probe_zai.sh` style): requires `DISCORD_BOT_TOKEN` + `DISCORD_TEST_CHANNEL_ID`; checks REST auth → sends a `!probe` message via REST → asserts the bot's reply arrives (read-back via REST) → exercises `/status` (M2) → clean shutdown. Skipped with a clear message when env vars absent, matching `probe_all.sh` behavior.

CI: all `tests/test_discord_*.py` are network-free and included in the default suite; the probe script is not.

---

## 17. Documentation deliverables

| Doc | Change |
|-----|--------|
| `docs/DISCORD.md` (new) | Portal setup (bot + intents), env var reference, `discord.json` guide, run cookbook (ZAI/Ollama examples), slash commands, troubleshooting matrix (4014 intent, 4004 token, silent bot = allowlist, rate-limit 429 loop, SD attachment failures) |
| `README.md` | Features bullet + docs-table row for `docs/DISCORD.md` |
| `docs/USAGE.md` | `agentkthx discord` section in the CLI command table + Discord env vars |
| `docs/ARCH.md` | New "Discord Integration (`plugins/discord/`)" section (thread model, data flow diagram) |
| `docs/SUPPORT.md` | Discord integration support tier (proposed: Fully Supported after M4 5/5 smoke) |
| `docs/CHANGELOG.md` | Per-milestone entries (R07.33 / R07.34 / R08.0 / R08.1 / R08.2) |
| `pyproject.toml` | Verify `plugins/*/plugin.json` package-data glob picks up the new plugin (no change expected) |

---

## 18. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| RFC 6455 client bugs (masking, fragmentation, TLS half-close) | Gateway drops, reconnect storms | Pure-function codec with RFC vectors; no compression mode; watchdog + capped exponential backoff with jitter; 72h soak in M4 |
| Discord deprecates/changes Gateway v10 details | Bot stops connecting | Pin `?v=10`; tolerate unknown ops (MUST-ignore per spec); version bump is a one-file change in `gateway.py` |
| `MESSAGE_CONTENT` intent not enabled in portal | Bot sees empty message bodies → silent | Detect empty content on mention triggers → one-line hint reply ("enable MESSAGE CONTENT INTENT"); `4014` fatal path prints exact fix |
| Slow local models block channel(s) | Queue backlog, user-facing hangs | `DISCORD_MAX_STEPS=5`, worker pool + queue caps + busy replies, typing refresh keeps users informed, semaphore protects backend |
| Small models produce ReAct garbage in chat | Confusing replies | Existing tool-parse/normalizer stack already tuned for small models (R07.07); max reply cap; `/think` optional |
| Abuse / flooding from rogue guild | API ban risk | Deny-by-default allowlists, cooldowns, queue caps, global REST pause on 429, plugin-local audit log |
| Token leakage via logs/errors | Credential exposure | `_redact()` on every path, unit-tested; interaction tokens never logged; secrets guard keeps token out of manifest defaults |
| Two processes share one token | Gateway session fights (8000-class closes) | Lockfile `~/.agentkthx/discord.lock` + docs |
| Windows signal differences | Ctrl+C handling drift | `stop()` is thread-safe event-driven (not signal-dependent); smoke test on both platforms listed in M4 |
| Scope creep (voice, embeds, sharding) | Milestone slip | Non-goals list (§2) is normative for v1; new RFCs required to amend |

---

## 19. Open questions

1. **Ephemeral-by-default for `/ask`?** Ephemeral responses keep channels clean but hide the bot's activity from others. Proposal: public by default, `ephemeral=true` option on the command.
2. **Owner approval flow for dangerous tools** (reply `kthx approve <nonce>` within 60s) — ship in M4 or descope to a separate RFC? Lean: descope; the deny-all default is safe and simple.
3. **Should Discord sessions participate in the Orchestrator** (e.g., route #math to a specialist AgentCard)? Deferred to post-R08; the plumbing (per-channel AgentCard mapping) would extend `discord.json`.
4. **Workspace attachments (files uploaded to Discord) as prompt context** — text files could be inlined under the prompt-length cap; needs a size/type policy. Proposal: M4 stretch, text-only, ≤ 100 KB.
5. **`agentnova` parity** — the repo carries `agentnova-redirect/`; if AgentNova shares the plugin spec, this plugin should be portable by construction (no `agentkthx.*` imports outside the documented public API: `Agent`, `get_backend`, souls loader).
