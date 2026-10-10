# ⚛️ AgentKthx Discord Plugin

Run AgentKthx as a Discord bot: pure-stdlib Gateway v10 WebSocket client + REST v10,
zero third-party dependencies. The bot answers @mentions, replies-to-bot, and DMs
through the same `Agent` agentic loop the CLI uses — sessions persist per channel,
tool calls are policy-gated, and slash commands manage the conversation.

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
| `DISCORD_MAX_STEPS` | `5` | Agent step cap (marked "(incomplete)" when hit) |
| `DISCORD_TOOLS` | `calculator,parse_json,todo,web_search,http_get` | Tool allowlist (`shell`/`python_repl` always excluded here) |
| `DISCORD_UNSAFE_TOOLS` | `false` | Lifts the shell exclusion (banner warns) |
| `DISCORD_QUEUE_MAX` | `8` | Bounded dispatch queue; overflow gets a one-liner |
| `DISCORD_MAX_WORKERS` | `2` | Worker threads (agent runs serialize on a semaphore) |
| `DISCORD_SESSION_TTL_DAYS` | `30` | Stale `discord-*` session prune at startup |
| `DISCORD_CONFIG` | `~/.agentkthx/discord.json` | Per-channel override file |
| `DISCORD_SOUL` | *(none)* | Soul name; **no soul by default** |

CLI flags: `--backend` `--model` `--api` `--soul` `--tools` `--max-steps`
`--dry-run` `--register-commands`.

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

Only narrows: tools intersect with `DISCORD_TOOLS`, and a soul's `allowedTools`
intersects again (stricter wins). `/model` + `/soul` write runtime overrides for
the current process; the file wins on restart.

## 4. Triggers and sessions

| Trigger | Gate |
|---------|------|
| @mention in a guild | guild allowlist (+channel/user allowlists) |
| Reply to a bot message | same |
| DM | `DISCORD_ALLOW_DMS=true` + user allowlist — **no @ needed** |

Sessions: `discord-g{guild}-c{channel}` in guilds, `discord-dm-{user}` for DMs —
visible in `agentkthx sessions` like any CLI session.

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
| Slash commands missing | Never registered | `agentkthx discord --register-commands`, restart the client |
| `insufficient permission` on `/model` | Not in `DISCORD_OWNER_IDS` | Add your user ID |
| 429 rate-limit loop | Discord REST bucket | Handled internally (single retry + global pause); slow down bulk tests |

## 8. Security posture (plan §12)

- Deny-by-default: empty allowlist = silent bot; nothing is queued before the gate.
- `shell` / `python_repl` are excluded from the Discord tool surface; dangerous
  tools are confirmed by a callback that **always denies** (nobody at the terminal).
- Prompt hygiene: bot's own mention stripped, `@everyone`/`@here` neutralized,
  prompts capped at `DISCORD_MAX_PROMPT_CHARS`.
- Secrets: the env file is 0600, tokens are redacted on every log/error path,
  interaction webhook tokens are never logged.
- Discord run metadata appends to `~/.agentkthx/discord_audit.log` with hashed user IDs.
