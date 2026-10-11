"""
AgentKthx Plugin — Discord REST Client (stdlib-only)

urllib-based client for the six endpoints the Discord plugin needs
(docs/DISCORD_PLUGIN_PLAN.md §5.1): message send, typing, self-check,
slash registration + followups (M2), and multipart uploads (M3).

Includes `chunk_reply()` — the 2000-char message splitter with code-fence
integrity — and token redaction on every error path.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

DISCORD_API_BASE = "https://discord.com/api/v10"
USER_AGENT = "AgentKthx (+https://github.com/VTSTech/AgentKthx)"
MESSAGE_MAX_LEN = 2000
DEFAULT_TRUNCATION_MARKER = " …(truncated, ask in DM for full output)"
MAX_RETRY_AFTER_S = 30.0


# ---------------------------------------------------------------------------
# Errors + helpers
# ---------------------------------------------------------------------------


class DiscordRestError(Exception):
    """Non-2xx REST response (or transport failure). Never leaks the token."""

    def __init__(self, status: int, code: int | None = None, message: str = ""):
        self.status = status
        self.code = code
        self.message = message
        parts = [f"Discord REST error {status}"]
        if code is not None:
            parts.append(f"(code {code})")
        if message:
            parts.append(f": {message}")
        super().__init__(" ".join(parts))


def redact(text: str, secret: str | None) -> str:
    """Scrub `secret` from any string destined for logs/exceptions."""
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


def chunk_reply(
    text: str | None,
    max_len: int = MESSAGE_MAX_LEN,
    max_msgs: int = 3,
    marker: str = DEFAULT_TRUNCATION_MARKER,
) -> list[str]:
    """
    Split a reply into Discord-safe messages (<= max_len chars each).

    - prefers paragraph boundaries ("\\n\\n"), then line boundaries
    - never leaves an unclosed code fence: an open fence is closed at the
      end of the chunk and reopened at the start of the next one
    - a single line longer than a whole chunk is hard-sliced (fence state
      is not tracked through hard slices — oversized single lines are
      prose by construction)
    - output is capped at max_msgs; the last chunk then carries `marker`
    """
    if text is None:
        return []
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    assert max_msgs >= 1, "max_msgs must be >= 1"

    REOPEN = 4  # "```\n" at chunk start when continuing an open fence
    CLOSE = 4  # "\n```" at chunk end when the fence is left open

    def hard_slice(line: str) -> None:
        piece_size = max(1, max_len - 16)
        for i in range(0, len(line), piece_size):
            chunks.append(line[i : i + piece_size])

    def commit(line: str) -> None:
        """Append `line` to a fresh chunk (caller guarantees it fits)."""
        nonlocal cur, cur_len, has_close, fence_open, reopen_active
        cur = [line]
        reopen_active = fence_open  # state before this line flips it
        cur_len = (REOPEN if fence_open else 0) + len(line) + (CLOSE if open_after(line) else 0)
        has_close = open_after(line)
        fence_open = open_after(line)

    def open_after(line: str) -> bool:
        """Fence state after `line` is committed (state flips on odd ``` count)."""
        return fence_open != (line.count("```") % 2 == 1)

    def flush() -> None:
        """Emit the current chunk (reopen + close fences as needed)."""
        nonlocal cur, cur_len, has_close, reopen_active
        if not cur:
            return
        body = ("```\n" if reopen_active else "") + "\n".join(cur)
        if has_close:
            body += "\n```"
        chunks.append(body)
        cur = []
        cur_len = 0
        has_close = False
        reopen_active = False

    lines = text.split("\n")
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0  # exact final length if flushed right now
    has_close = False  # does cur_len already include the close suffix?
    fence_open = False
    reopen_active = False  # current chunk begins with a reopened fence

    for line in lines:
        if not cur:
            need = (REOPEN if fence_open else 0) + len(line) + (CLOSE if open_after(line) else 0)
            if need > max_len:
                hard_slice(line)
                continue
            commit(line)
            continue
        delta = 1 + len(line) + (CLOSE if open_after(line) else 0) - (CLOSE if has_close else 0)
        if cur_len + delta > max_len:
            flush()
            need = (REOPEN if fence_open else 0) + len(line) + (CLOSE if open_after(line) else 0)
            if need > max_len:
                hard_slice(line)
                continue
            commit(line)
            continue
        cur.append(line)
        cur_len += delta
        has_close = open_after(line)
        fence_open = open_after(line)
    flush()

    if len(chunks) > max_msgs:
        chunks = chunks[:max_msgs]
        if len(marker) >= max_len:  # degenerate tiny max_len — degrade marker
            marker = "…"
        allowed = max_len - len(marker)
        if len(chunks[-1]) > allowed:
            chunks[-1] = chunks[-1][:allowed]
        chunks[-1] += marker
    return chunks


# ---------------------------------------------------------------------------
# DiscordRest
# ---------------------------------------------------------------------------


class DiscordRest:
    """
    Discord REST v10 client. One instance per process; thread-safe enough
    for the M0 single-consumer pattern (global-pause state is lock-guarded).
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DISCORD_API_BASE,
        timeout: float = 15.0,
        opener: urllib.request.OpenerDirector | None = None,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener
        self._global_pause_until = 0.0
        self._global_lock = threading.Lock()

    # -- endpoint sugar -------------------------------------------------------

    def get_self(self) -> dict:
        """GET /users/@me — startup self-check; resolves the bot's user id."""
        data = self._request("GET", "/users/@me")
        return data or {}

    def send_message(self, channel_id: str, content: str) -> dict:
        """POST /channels/{id}/messages — content must be pre-chunked."""
        if len(content) > MESSAGE_MAX_LEN:
            raise ValueError(f"content exceeds {MESSAGE_MAX_LEN} chars — use chunk_reply() first")
        data = self._request(
            "POST", f"/channels/{channel_id}/messages", json_body={"content": content}
        )
        return data or {}

    def trigger_typing(self, channel_id: str) -> None:
        """POST /channels/{id}/trigger-typing — 10s "Bot is typing…" state."""
        self._request("POST", f"/channels/{channel_id}/trigger-typing")

    def get_channel(self, channel_id: str) -> dict:
        """GET /channels/{id} — resolves the #name used in prompt envelopes."""
        data = self._request("GET", f"/channels/{channel_id}")
        return data or {}

    def get_application(self) -> dict:
        """GET /oauth2/applications/@me — resolves the OAuth2 application id
        (the client_id used in bot invite URLs; usually differs from the
        bot's user id)."""
        data = self._request("GET", "/oauth2/applications/@me")
        return data or {}

    def list_commands(self, app_id: str) -> list:
        """GET /applications/{app_id}/commands — currently registered global
        slash commands (startup status check: tells the operator whether the
        native / picker has commands at all)."""
        data = self._request("GET", f"/applications/{app_id}/commands")
        return data or []

    def register_commands(
        self, app_id: str, commands: list[dict], guild_id: str | None = None
    ) -> list:
        """PUT /applications/{app_id}/commands — bulk slash registration (M2).
        With `guild_id`, registers guild-scoped (instant-propagating) instead
        of global commands. Returns the registered command list."""
        if guild_id:
            path = f"/applications/{app_id}/guilds/{guild_id}/commands"
        else:
            path = f"/applications/{app_id}/commands"
        data = self._request("PUT", path, json_body=list(commands))
        return data or []

    def interaction_callback(
        self, interaction_id: str, interaction_token: str, payload: dict
    ) -> None:
        """POST /interactions/{id}/{token}/callback — ACK an interaction (M2).
        Auth is carried by the path token, not the bot token."""
        self._request(
            "POST",
            f"/interactions/{interaction_id}/{interaction_token}/callback",
            json_body=payload,
            auth=False,
        )

    def followup(self, app_id: str, interaction_token: str, payload: dict) -> dict:
        """POST /webhooks/{app_id}/{token} — interaction followup message
        (M2). Auth-free by design (the token in the path IS the secret).
        flags=64 in the payload marks the message ephemeral."""
        data = self._request(
            "POST",
            f"/webhooks/{app_id}/{interaction_token}",
            json_body=payload,
            auth=False,
        )
        return data or {}

    # -- core request path ------------------------------------------------------

    def _request(
        self, method: str, path: str, *, json_body: dict | None = None, auth: bool = True
    ) -> dict | None:
        url = f"{self.base_url}{path}"
        headers = {
            "User-Agent": USER_AGENT,
        }
        if auth:
            headers["Authorization"] = f"Bot {self.token}"
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            self._wait_global_pause()
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                opener = self._opener or urllib.request.build_opener()
                resp = opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as err:
                raw = err.read().decode("utf-8", "replace")
                if err.code == 429 and attempt == 0:
                    retry_after = self._parse_retry_after(raw, err.headers)
                    if (err.headers or {}).get("X-RateLimit-Global"):
                        self._set_global_pause(retry_after)
                    time.sleep(min(retry_after, MAX_RETRY_AFTER_S))
                    attempt += 1
                    continue
                code, message = self._parse_error_body(raw)
                raise DiscordRestError(
                    err.code, code, redact(message or raw[:300], self.token)
                ) from None
            except (urllib.error.URLError, OSError) as err:
                raise DiscordRestError(
                    0, None, redact(str(getattr(err, "reason", err)), self.token)
                ) from None
            status = getattr(resp, "status", resp.getcode())
            data = resp.read()
            if status == 204 or not data:
                return None
            try:
                return json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise DiscordRestError(status, None, "malformed JSON response") from None

    @staticmethod
    def _parse_retry_after(raw_body: str, headers) -> float:
        try:
            value = float(json.loads(raw_body).get("retry_after"))
        except Exception:  # noqa: BLE001 - header fallback below
            value = 0.0
        if value <= 0 and headers is not None:
            try:
                value = float(headers.get("Retry-After", 0))
            except (TypeError, ValueError):
                value = 0.0
        return max(0.05, min(value, MAX_RETRY_AFTER_S))

    @staticmethod
    def _parse_error_body(raw_body: str) -> tuple[int | None, str]:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict):
                return parsed.get("code"), str(parsed.get("message", ""))
        except json.JSONDecodeError:
            pass
        return None, ""

    def _wait_global_pause(self) -> None:
        with self._global_lock:
            remaining = self._global_pause_until - time.monotonic()
        if remaining > 0:
            time.sleep(min(remaining, MAX_RETRY_AFTER_S))

    def _set_global_pause(self, seconds: float) -> None:
        with self._global_lock:
            self._global_pause_until = max(self._global_pause_until, time.monotonic() + seconds)
