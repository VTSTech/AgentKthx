# ⚛️ AgentKthx Discord Plugin

Run AgentKthx as a Discord bot: pure-stdlib Gateway v10 WebSocket client + REST v10,
zero third-party dependencies. The bot answers @mentions, replies-to-bot, and DMs
through the same `Agent` loop the CLI uses — sessions persist per channel, **tools
are off by default** (the bot answers chat directly; opt in via `DISCORD_TOOLS`),
and slash commands manage the conversation.

```
agentkthx discord setup        # one-time wizard -> ~/.agentkthx/.env (0600)
agentkthx discord --register-commands   # one-time slash registration, then exit
agentkthx discord --dry-run    # live gateway, policy decisions only, no sends
agentkthx discord              # LIVE
```

---

## 1. Portal setup (one-time)

1. <https://discord.com/developers/applications> → **New Application** → name it.
2. **Bot** tab → **Reset Token** → copy (shown once). This is `DISCORD_BOT_TOKEN`.
3. Same tab → **Privileged Gateway Intents** → enable **MESSAGE CONTENT INTENT**.
   Without it the gateway closes with `4014` on startup (fatal, by design).
4. **OAuth2 → URL Generator** → scope `bot`; permissions **View Channels + Send
   Messages + Read Message History**; open the URL, pick your server, Authorize.
   (`agentkthx discord setup` prints this URL for you after validating the token.)
5. User Settings → Advanced → **Developer Mode ON**, then right-click your server /
   a username → **Copy Server ID / Copy User ID** for the allowlists.

## 2. The setup wizard

`agentkthx discord setup` writes `~/.agentkthx/.env` (mode 0600, atomic replace,
comments and unknown keys preserved). It asks for:

| Step | Key | Hint shown |
|------|-----|------------|
| 1 | `DISCORD_BOT_TOKEN` | pasted blind; optional live validation against `GET /users/@me`; on success resolves the **Application ID** and prints the ready-to-open invite URL |
| 2 | `DISCORD_ALLOW_GUILDS` | comma-separated server IDs; **empty = silent everywhere** |
| 3 | `DISCORD_ALLOW_USERS` | comma-separated user IDs; also the DM gate |
| 4 | `DISCORD_ALLOW_DMS` | `Answer DMs?` — default **off** |

`export`ed environment variables always win over the file (systemd/CI friendly).

## 3. Configuration reference

| Env var | Default | Meaning |
|---------|---------|---------|
| `DISCORD_BOT_TOKEN` | *(empty)* | Bot token (**required**) |
| `DISCORD_APP_ID` | *(empty)* | Application ID for slash commands (auto-resolved from the token when empty) |
| `DISCORD_ALLOW_GUILDS` | *(empty)* | Deny-by-default server allowlist |
| `DISCORD_ALLOW_CHANNELS` | *(empty)* | Optional channel narrowing inside allowed guilds |
| `DISCORD_ALLOW_USERS` | *(empty)* | Optional user allowlist (also gates DMs; doubles as owner fallback) |
| `DISCORD_ALLOW_DMS` | `false` | Answer direct messages |
| `DISCORD_OWNER_IDS` | *(empty)* | Owners for `/model` + `/soul`; falls back to `DISCORD_ALLOW_USERS` |
| `DISCORD_REGISTER_SLASH` | `false` | Register slash commands on every startup |
| `DISCORD_USER_COOLDOWN_S` | `10` | Per-user cooldown between agent runs |
| `DISCORD_MAX_PROMPT_CHARS` | `1500` | Prompt cap after sanitization |
| `DISCORD_MAX_REPLY_MSGS` | `3` | Max 2000-char chunks per answer |
| `DISCORD_MAX_STEPS` | `5` | Agent step cap (Discord has its **own** knob — the CLI/`AGENTKTHX_MAX_STEPS` setting does not apply here) |
| `DISCORD_NUM_CTX` | *(detect)* | Context window (chat parity) — plain int or human suffix, e.g. `32768` / `128k`. Unset = **detected from the backend/model catalog exactly like `agentkthx chat`** (zai glm-4.5-flash → 132000), falling back to `AGENTKTHX_NUM_CTX`, then the agent default (8192) |
| `DISCORD_MAX_TOKENS` | *(detect)* | Generation cap (chat parity, same as chat's `--num-predict`) — e.g. `4096` / `2k`. Unset = detected from the catalog when the backend publishes it (zai glm-4.5-flash → 4125); when nothing is detected the agent caps to `num_ctx//32`. **Without either knob the 8K-default cap was 256 tokens — the cause of truncated replies (`finish_reason: length`)** |
| `DISCORD_TOOLS` | *(none)* | **No tools by default (R07.33)** — opt in with a comma list, e.g. `calculator,web_search`; `none`/`off`/empty all mean off; `shell`/`python_repl` always excluded |
| `DISCORD_UNSAFE_TOOLS` | `false` | Lifts the shell exclusion (banner warns) |
| `DISCORD_DEBUG` | `false` | Debug echo: backend prompts/responses/errors + pipeline details (same as `--debug`) |
| `DISCORD_KEEP_SESSIONS` | `false` | **Fresh sessions by default (R07.33)** — `true` resumes per-channel history across restarts (same as `--keep`) |
| `DISCORD_QUEUE_MAX` | `8` | Bounded dispatch queue; overflow gets a one-liner |
| `DISCORD_MAX_WORKERS` | `2` | Worker threads (agent runs serialize on a semaphore) |
| `DISCORD_SESSION_TTL_DAYS` | `7` | Inactivity GC: `discord-*` sessions untouched this many days are deleted from the store at startup (`0` disables). Does **not** control restart resume — that's `--keep` |
| `DISCORD_CONFIG` | `~/.agentkthx/discord.json` | Per-channel override file |
| `DISCORD_SOUL` | *(none)* | Soul name; **no soul by default** |

CLI flags: `--backend` `--model` `--api` `--soul` `--tools` `--max-steps`
`--num-ctx` `--max-tokens` `--keep` `--dry-run` `--debug` `--register-commands`.

### Conversation history (sessions)

**Fresh on every restart (R07.33).** Each bot run scopes its session keys with
a run stamp (`-r<unix-start>`), so every channel/DM starts a **new conversation
after a restart** — the model never sees messages from before the restart:

```
discord-g{guild}-c{channel}-r1791675399   # this run's conversation
discord-g{guild}-c{channel}               # --keep: stable, resumes forever
```

- **`--keep`** / `DISCORD_KEEP_SESSIONS=true` restores the old behavior:
  stable keys and history resumes across restarts.
- Within a run, history accumulates normally per channel (or per DM user).
- **`/reset`** clears the *current* conversation for that channel.
- Sessions stay visible in `agentkthx sessions` (the stamp is a unix start
  time — `date -d @1791675399` tells you which run a session belonged to).
- **Session TTL is not resume control.** `DISCORD_SESSION_TTL_DAYS` (default
  `7`, was 30) only garbage-collects sessions from `~/.agentkthx/memory.db`
  after that many days of inactivity — fresh runs leave one row per channel
  behind, and this keeps the store from growing forever. Set `0` to disable.

### Debugging (`--debug` / `DISCORD_DEBUG=true`)

- Startup banner prints `debug: ON`.
- The core `Agent` is built with `debug=True` — **the same machinery as
  `agentkthx chat --debug`**: per-step backend prompts/responses, tool-call
  payloads and errors are echoed while a job runs.
- The plugin adds `[discord:debug]` pipeline lines on stdout: run start (user,
  session, model, prompt size), the full envelope, and the outcome (success,
  step count, tokens, ms) — including an explicit marker when a run ends
  incomplete (the max-steps case).

### Per-channel overrides (`~/.agentkthx/discord.json`)

```json
{
  "channels": {
    "222333444555666777": {
      "soul": "kthx-trading",
      "tools": ["calculator", "web_search"],
      "model": "qwen3:8b",
      "session_prefix": "support"
    }
  }
}
```

Semantics (R07.33): the global default is **no tools**. A channel `tools` entry
opts that channel in; tools then intersect with the soul's `allowedTools`
(stricter wins). A soul alone never grants tools — set `DISCORD_TOOLS` (or the
channel entry) too. `/model` + `/soul` write runtime overrides for the current
process; the file wins on restart.

## 4. Triggers and sessions

| Trigger | Gate |
|---------|------|
| @mention in a guild | guild allowlist (+channel/user allowlists) |
| Reply to a bot message | same |
| DM | `DISCORD_ALLOW_DMS=true` + user allowlist — **no @ needed** |

Sessions: `discord-g{guild}-c{channel}[-r{run}]` in guilds,
`discord-dm-{user}[-r{run}]` for DMs — visible in `agentkthx sessions` like
any CLI session. See **Conversation history** above for the fresh-on-restart
default and `--keep`.

### The built-in Discord prompt

With no soul configured, every Discord run uses the plugin's own identity
instead of the stock one-liner:

> You are AGI AgentKthx — an autonomous agent bringing **Agentic Reasoning to
> Discord**. … Tools: the agent runtime fully supports tools, but tools are
> **DISABLED by default** on Discord — most channels run tool-free. Unless
> tool instructions follow this prompt, answer from your own knowledge and
> never claim you looked something up, ran a command, or can act outside
> this chat. The bot's operator can enable tools per channel at any time.

- **No host details**: the CLI's host-environment probe (OS/kernel/shell
  section) is skipped on Discord — the shell tool is excluded there
  unconditionally anyway, so it is dead weight in a chat prompt.
- When a channel opts into tools, the standard tool section and ReAct/native
  instructions are appended to this identity automatically — the prompt stays
  truthful either way.
- A configured soul (`--soul`, `DISCORD_SOUL`, `discord.json`, `/soul`)
  replaces this identity entirely (souls carry their own).

## 5. Slash commands (M2)

Register once with `agentkthx discord --register-commands` (global; needs the
token, app id auto-resolves). Every interaction is ACKed within 3 seconds
(`type 5` defer for agent-backed commands, `type 4` direct for quick ones).

| Command | Who | Behavior |
|---------|-----|----------|
| `/ask <prompt>` [ephemeral] | allowlisted | Same pipeline as a mention; reply via webhook (ephemeral when requested) |
| `/think <prompt>` | allowlisted | Like `/ask`, but the model's reasoning is shown first in a code fence |
| `/model [name]` | owner to set | Show or switch this channel's model (runtime override) |
| `/soul [name]` | owner to set | Show or switch this channel's soul (validated against the souls loader) |
| `/reset` | allowlisted | Delete this channel's persisted session; next message starts fresh |
| `/status` | allowlisted | Backend, model, soul, tools, uptime, queue depth, run counters |

Non-owners get an ephemeral "insufficient permission" on `/model`/`/soul` sets.

### Text commands (no registration needed)

Every command above also works typed as plain message text — this is what
happens when you send `@AgentKthx /status` as a regular message (Discord only
fires real interactions for picker invocations):

```text
@AgentKthx /status
@AgentKthx /think why is the sky blue?
@AgentKthx /model qwen3:8b        (owner)
/reset                            (DM — no @ needed)
```

- Quick commands (`/status` `/reset` `/model` `/soul`) answer in-channel on
  the spot — no cooldown (same as native quick interactions).
- `/ask` + `/think` run the full agent pipeline: cooldown, sessions, chunking.
- Case-insensitive; a leading `/` that is NOT one of the six commands
  (`/usr/bin/env`, `/shrug`, typos) goes to the agent as a normal prompt.
- Ephemeral replies are a native-interaction feature only; text forms answer
  in-channel.

The startup banner reports the native registration state so the picker's
behavior is never a mystery:

```text
[discord] slash: 6 global command(s) registered (native / picker + @bot /command text both live)
[discord] slash: NOT registered — the native / picker will not list commands. Run
          'agentkthx discord --register-commands' once; @bot /command text forms work either way
```

## 6. Run cookbook

```bash
# ZAI cloud
agentkthx discord --backend zai --model glm-4.5-flash

# Local Ollama
ollama serve &
agentkthx discord --backend ollama --model qwen3:8b

# Trading soul in one channel only (discord.json) + everything else stock
agentkthx discord

# systemd unit sketch
[Service]
ExecStart=/usr/bin/python3 -c "from agentkthx.cli import main; main()" discord --backend zai
Environment=DISCORD_REGISTER_SLASH=true
Restart=on-failure
User=youruser   # ~/.agentkthx/.env is 0600 — run as the owner
```

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Gateway closes `4014` | MESSAGE CONTENT INTENT toggle off | Portal → Bot → Privileged Intents → enable, restart |
| Gateway closes `4004` | Invalid/reset token | Re-run `agentkthx discord setup` |
| Bot online but silent | Empty/wrong allowlists, or no @mention | Check the startup allowlists banner; @ it or DM with `DISCORD_ALLOW_DMS=true` |
| `user-not-allowed` in log | Sender not in `DISCORD_ALLOW_USERS` | Add their ID (comma-separated) |
| `cooldown` reply | 10s per-user cooldown | Wait, or raise `DISCORD_USER_COOLDOWN_S` |
| `typing unavailable (404)` | Fresh-session race or channel type without typing | Cosmetic; logged once per channel, replies unaffected |
| `(incomplete — maximum steps reached)` | Step budget exhausted — only possible when tools were opted in (each tool round burns a step) | Tools are off by default now; if you enabled them, raise `DISCORD_MAX_STEPS` or drop them again. Note Discord has its own `DISCORD_MAX_STEPS` (default 5) — the CLI max-steps setting does not apply |
| Replies vague or backend misbehaving | Need visibility | Run with `--debug` (or `DISCORD_DEBUG=true`) — backend prompts/responses/errors are echoed; check `/status` for the effective model |
| Bot "forgot" the conversation | Fresh-sessions default (R07.33): every restart starts new conversations | Expected — run with `--keep` (or `DISCORD_KEEP_SESSIONS=true`) to resume history across restarts; `/reset` clears the current conversation either way |
| Slash commands missing | Never registered | `agentkthx discord --register-commands`, restart the client — the startup banner reports `slash: NOT registered` when so; `@bot /command` text forms work either way |
| `insufficient permission` on `/model` | Not in `DISCORD_OWNER_IDS` | Add your user ID |
| 429 rate-limit loop | Discord REST bucket | Handled internally (single retry + global pause); slow down bulk tests |

## 8. Security posture (plan §12)

- Deny-by-default: empty allowlist = silent bot; nothing is queued before the gate.
- **No tools by default (R07.33)** — the Discord surface answers chat; tool use
  requires an explicit `DISCORD_TOOLS` / `--tools` / channel opt-in.
- `shell` / `python_repl` are excluded from the Discord tool surface even when
  opted in; dangerous tools are confirmed by a callback that **always denies**
  (nobody at the terminal).
- Prompt hygiene: bot's own mention stripped, `@everyone`/`@here` neutralized,
  prompts capped at `DISCORD_MAX_PROMPT_CHARS`.
- Secrets: the env file is 0600, tokens are redacted on every log/error path,
  interaction webhook tokens are never logged.
- Discord run metadata appends to `~/.agentkthx/discord_audit.log` with hashed user IDs.
