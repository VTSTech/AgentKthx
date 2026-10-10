"""
AgentKthx Plugin — `agentkthx discord setup` (interactive wizard)

Collects the three minimum settings for running the bot:
  1. DISCORD_BOT_TOKEN      (secret — pasted blind, optionally validated
                             against GET /users/@me)
  2. DISCORD_ALLOW_GUILDS   (deny-by-default server allowlist)
  3. DISCORD_ALLOW_USERS    (user allowlist, used for DM/owner checks in M1+)

Each prompt carries a "where do I find this" hint. Everything is persisted
to ~/.agentkthx/.env with 0600 permissions (atomic replace, unknown keys and
comments preserved). `agentkthx discord` loads that file on startup; real
environment variables always win, so systemd/CI users can still `export`.

The wizard only writes the file AFTER all prompts complete — Ctrl+C at any
point leaves the previous file untouched.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Callable

MANAGED_KEYS = ("DISCORD_BOT_TOKEN", "DISCORD_ALLOW_GUILDS", "DISCORD_ALLOW_USERS")

_FILE_HEADER = (
    "# AgentKthx Discord plugin settings",
    "# Written by: agentkthx discord setup",
    "# This file contains a bot secret — do NOT commit or share it.",
    "# Loaded automatically by 'agentkthx discord'; exported env vars win.",
)

_PORTAL_URL = "https://discord.com/developers/applications"


def default_env_path() -> str:
    """~/.agentkthx/.env, resolved at call time (tests monkeypatch HOME)."""
    return os.path.expanduser("~/.agentkthx/.env")


# ---------------------------------------------------------------------------
# Env-file primitives
# ---------------------------------------------------------------------------


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse one KEY=VALUE line -> (key, value). Handles `export ` prefix and
    matching single/double quotes. Comments, blanks, and junk -> None."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s.startswith("export "):
        s = s[len("export ") :].lstrip()
    if "=" not in s:
        return None
    key, _, val = s.partition("=")
    key = key.strip()
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return (key, val) if key else None


def parse_env_file(path: str) -> dict[str, str]:
    """Read KEY=VALUE pairs from `path`; missing/unreadable file -> {}."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, PermissionError, IsADirectoryError):
        return {}
    except UnicodeDecodeError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    return values


def merge_env_lines(existing: str, updates: dict[str, str]) -> str:
    """Rewrite only the managed keys, preserving every other line (comments,
    ordering, foreign keys like DISCORD_MAX_WORKERS). Missing managed keys
    are appended at the end."""
    out: list[str] = []
    seen: set[str] = set()
    for line in existing.splitlines():
        parsed = _parse_line(line)
        if parsed is not None and parsed[0] in updates:
            out.append(f"{parsed[0]}={updates[parsed[0]]}")
            seen.add(parsed[0])
        else:
            out.append(line)
    for key in MANAGED_KEYS:
        if key in updates and key not in seen:
            if out and out[-1].strip():
                out.append("")  # blank separator before appended block
            out.append(f"{key}={updates[key]}")
    return "\n".join(out) + ("\n" if out else "")


def write_env_file(path: str, updates: dict[str, str]) -> None:
    """Atomically merge `updates` into the env file with 0600 permissions.

    Creates ~/.agentkthx/ if needed (0700). An existing file keeps its
    comments and unknown keys; a new file gets an explanatory header.
    """
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = ""
    if p.exists():
        existing = p.read_text(encoding="utf-8")
    if existing:
        new_text = merge_env_lines(existing, updates)
    else:
        lines = list(_FILE_HEADER)
        for key in MANAGED_KEYS:  # fresh files always carry all three keys
            lines.append(f"{key}={updates.get(key, '')}")
        new_text = "\n".join(lines) + "\n"
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new_text)
    os.replace(str(tmp), str(p))
    try:  # belt-and-braces: pre-existing file may have had wider perms
        os.chmod(str(p), 0o600)
    except OSError:
        pass


def load_env_file(path: str, environ: dict | None = None) -> int:
    """Load DISCORD_* keys from the env file into the environment.

    setdefault semantics: real environment variables always win over the
    file, so `export DISCORD_...` stays the override path. Returns the
    number of keys actually applied.
    """
    env = os.environ if environ is None else environ
    values = parse_env_file(path)
    applied = 0
    for key, val in values.items():
        if not key.startswith("DISCORD_"):
            continue
        if key not in env:
            env[key] = val
            applied += 1
    return applied


# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def normalize_ids(raw: str) -> tuple[list[str], list[str]]:
    """Split comma/space-separated IDs -> (valid_snowflakes, dropped).

    Discord snowflakes are numeric; non-numeric tokens are dropped (with
    the wizard printing them) rather than silently corrupting the allowlist.
    Duplicates are de-duplicated, order preserved.
    """
    valid: list[str] = []
    dropped: list[str] = []
    for token in re.split(r"[,\s]+", raw.strip()):
        if not token:
            continue
        if token.isdigit():
            if token not in valid:
                valid.append(token)
        else:
            dropped.append(token)
    return valid, dropped


def token_warning(token: str) -> str | None:
    """Light sanity check on a pasted token. Never hard-rejects (token
    formats can change); returns a human warning or None."""
    token = token.strip()
    if not token:
        return "token is empty"
    if re.search(r"\s", token):
        return "token contains whitespace — likely a copy/paste mistake"
    if token.count(".") != 2:
        return (
            "bot tokens normally have three dot-separated parts "
            "(looks unusual — saving anyway)"
        )
    if len(token) < 50:
        return f"token is only {len(token)} chars (typical ~70) — may be truncated"
    return None


def redact_token(token: str) -> str:
    """Safe-for-screen form of a token (first 4 + … + last 4)."""
    if not token:
        return "(none)"
    if len(token) <= 8:
        return "••••"
    return f"{token[:4]}…{token[-4:]}"


def _validate_token(token: str, rest_factory: Callable[[str], Any], out) -> bool:
    """GET /users/@me with the pasted token — catches typos instantly."""
    try:
        me = rest_factory(token).get_self() or {}
    except Exception as err:  # noqa: BLE001 - any failure = "not validated"
        out(f"   ! Validation failed: {err}")
        out("     (saved anyway — fix the token or network and re-run setup)")
        return False
    out(f"   OK — token valid: {me.get('username', '?')} ({me.get('id', '?')})")
    return True


def _default_rest_factory(token: str):
    from .rest import DiscordRest  # lazy import per plugin spec

    return DiscordRest(token, timeout=10.0)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


def run_setup(
    *,
    input_fn: Callable[[str], str] = input,
    secret_fn: Callable[[str], str] = None,  # type: ignore[assignment]
    confirm_fn: Callable[[str, bool], bool] | None = None,
    rest_factory: Callable[[str], Any] | None = None,
    path: str | None = None,
    out: Callable[[str], Any] = print,
) -> int:
    """Interactive wizard. Injectable I/O (`input_fn` / `secret_fn` /
    `confirm_fn` / `out`) keeps this fully unit-testable offline.

    Returns 0 on success, 1 on abort/write failure, 1 on Ctrl+C/EOF.
    """
    import getpass

    if secret_fn is None:
        secret_fn = getpass.getpass
    if confirm_fn is None:

        def confirm_fn(prompt: str, default: bool) -> bool:  # noqa: F811
            hint = "Y/n" if default else "y/N"
            ans = input_fn(f"{prompt} [{hint}]: ").strip().lower()
            if not ans:
                return default
            return ans in ("y", "yes")

    if rest_factory is None:
        rest_factory = _default_rest_factory
    path = path or default_env_path()

    stored = parse_env_file(path)
    cur_token = stored.get("DISCORD_BOT_TOKEN", "")
    cur_guilds = stored.get("DISCORD_ALLOW_GUILDS", "")
    cur_users = stored.get("DISCORD_ALLOW_USERS", "")

    out(f"AgentKthx Discord setup — writes {path}")
    out("The file holds a bot secret: written with 0600 perms, never commit it.")
    out("(Ctrl+C at any point aborts without writing.)")
    out("")

    try:
        # -- 1) bot token ---------------------------------------------------
        out("1) BOT TOKEN")
        out(f"   Where: {_PORTAL_URL}")
        out("   -> your application -> Bot -> Reset Token -> Copy (shown ONCE).")
        out("   While there, enable Privileged Gateway Intent 'MESSAGE CONTENT")
        out("   INTENT' or the gateway will close with 4014 on startup.")
        if cur_token:
            out(f"   Stored token: {redact_token(cur_token)} (Enter keeps it)")
        token = ""
        while True:
            entered = secret_fn("   Paste token (input hidden): ").strip()
            if entered:
                token = entered
                warn = token_warning(token)
                if warn:
                    out(f"   ! {warn}")
                break
            if cur_token:
                token = cur_token
                break
            out("   ! No token stored yet — paste a token (or Ctrl+C to abort).")
        if token and confirm_fn("   Validate token against Discord now?", True):
            _validate_token(token, rest_factory, out)

        # -- 2) allowed guilds ----------------------------------------------
        out("")
        out("2) ALLOWED GUILDS (server IDs) — deny-by-default allowlist")
        out("   Where: Discord -> User Settings -> Advanced -> Developer Mode ON,")
        out("   then right-click your server icon -> Copy Server ID.")
        out("   Comma-separated. EMPTY list = the bot stays silent in EVERY server.")
        if cur_guilds:
            out(f"   Current: {cur_guilds} (Enter keeps it)")
        raw_guilds = input_fn("   Server IDs: ").strip()
        if not raw_guilds and cur_guilds:
            raw_guilds = cur_guilds
        guild_ids, dropped_g = normalize_ids(raw_guilds)
        if dropped_g:
            out(f"   ! dropped non-numeric IDs: {', '.join(dropped_g)}")
        if not guild_ids:
            out("   ! empty allowlist — nothing will be answered in any server")

        # -- 3) allowed users -------------------------------------------------
        out("")
        out("3) ALLOWED USERS (user IDs)")
        out("   Where: right-click a username (Developer Mode ON) -> Copy User ID.")
        out("   Comma-separated. Used for DM/owner checks (DMs also need")
        out("   DISCORD_ALLOW_DMS=true — default off).")
        if cur_users:
            out(f"   Current: {cur_users} (Enter keeps it)")
        raw_users = input_fn("   User IDs: ").strip()
        if not raw_users and cur_users:
            raw_users = cur_users
        user_ids, dropped_u = normalize_ids(raw_users)
        if dropped_u:
            out(f"   ! dropped non-numeric IDs: {', '.join(dropped_u)}")
    except (KeyboardInterrupt, EOFError):
        out("")
        out("Setup aborted — nothing was written.")
        return 1

    updates = {
        "DISCORD_BOT_TOKEN": token,
        "DISCORD_ALLOW_GUILDS": ",".join(guild_ids),
        "DISCORD_ALLOW_USERS": ",".join(user_ids),
    }
    try:
        write_env_file(path, updates)
    except OSError as err:
        out(f"! could not write {path}: {err}")
        return 1
    mode = stat.S_IMODE(os.stat(path).st_mode)
    out("")
    out(f"Wrote {path} (mode {mode:04o}):")
    out(f"  DISCORD_BOT_TOKEN={redact_token(token)}")
    out(f"  DISCORD_ALLOW_GUILDS={','.join(guild_ids) or '(empty)'}")
    out(f"  DISCORD_ALLOW_USERS={','.join(user_ids) or '(empty)'}")
    out("")
    out("Next:  agentkthx discord --dry-run   # policy decisions only, no sends")
    out("       agentkthx discord             # live gateway (replies land in M1)")
    return 0
