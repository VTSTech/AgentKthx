"""
AgentKthx — Discord REST Client Tests (M0)

chunk_reply edge cases (2000-char boundary, code-fence integrity,
hard slices, truncation cap), token redaction, and DiscordRest against
a localhost http.server stub (success, 429 retry, global pause, 401
error parsing). Zero external network — the stub binds 127.0.0.1 only.
See docs/DISCORD_PLUGIN_PLAN.md §5.1, §16.

Written by VTSTech — https://www.vts-tech.org
"""

import http.server
import json
import threading
import urllib.request

import pytest

from agentkthx.plugins.discord.rest import (
    DEFAULT_TRUNCATION_MARKER,
    MESSAGE_MAX_LEN,
    DiscordRest,
    DiscordRestError,
    chunk_reply,
    redact,
)

TOKEN = "secret-bot-token-abc123"


# ---------------------------------------------------------------------------
# chunk_reply
# ---------------------------------------------------------------------------


class TestChunkReply:
    def test_none_and_empty(self):
        assert chunk_reply(None) == []
        assert chunk_reply("") == []
        assert chunk_reply("   \n  ") == []

    def test_short_text_single_chunk(self):
        assert chunk_reply("hello") == ["hello"]

    def test_exact_boundary(self):
        text = "a" * MESSAGE_MAX_LEN
        assert chunk_reply(text) == [text]

    def test_boundary_plus_one_splits(self):
        text = "a" * (MESSAGE_MAX_LEN + 1)
        chunks = chunk_reply(text)
        assert len(chunks) == 2
        assert all(len(c) <= MESSAGE_MAX_LEN for c in chunks)
        assert "".join(chunks) == text  # no chars lost

    def test_line_boundaries_preferred(self):
        text = "\n".join("word " * 5 for _ in range(100))  # many short lines
        chunks = chunk_reply(text)
        assert all(len(c) <= MESSAGE_MAX_LEN for c in chunks)
        # no chars lost across the split (no fences here; outer strip is by design)
        assert "\n".join(chunks) == text.strip()

    def test_code_fence_closed_and_reopened(self):
        lines = ["prose before"]
        lines.append("```python")
        lines += [f"code line {i}" for i in range(60)]  # pushes fence across boundary
        lines.append("```")
        lines.append("prose after")
        text = "\n".join(lines)
        chunks = chunk_reply(text, max_len=400, max_msgs=10)
        assert len(chunks) >= 2
        total_fences = sum(c.count("```") for c in chunks)
        assert total_fences % 2 == 0, "every chunk set must leave fences balanced"
        # the chunk that cut inside the fence must close it…
        cut = next(i for i, c in enumerate(chunks) if c.startswith("```") and i > 0)
        assert chunks[cut - 1].endswith("```")
        # …and the next one must reopen it (plain fence; language tag not preserved)
        assert chunks[cut].startswith("```")

    def test_fence_balance_invariant_randomish(self):
        text = (
            "intro\n```\n" + "\n".join(f"l{i}" for i in range(120)) + "\n```\noutro"
        )
        chunks = chunk_reply(text, max_len=200, max_msgs=20)
        assert sum(c.count("```") for c in chunks) % 2 == 0

    def test_hard_slice_giant_line(self):
        text = "z" * 5000
        chunks = chunk_reply(text)
        assert all(len(c) <= MESSAGE_MAX_LEN for c in chunks)
        assert "".join(chunks) == text

    def test_hard_slice_with_fence_open(self):
        text = "```\n" + "z" * 5000
        chunks = chunk_reply(text)
        assert all(len(c) <= MESSAGE_MAX_LEN for c in chunks)

    def test_cap_and_truncation_marker(self):
        paras = [f"para {i}\n" + "x" * 30 for i in range(6)]
        text = "\n\n".join(paras)
        chunks = chunk_reply(text, max_len=40, max_msgs=2, marker=" …more")
        assert len(chunks) == 2
        assert all(len(c) <= 40 for c in chunks)
        assert chunks[-1].endswith(" …more")

    def test_tiny_max_len_degrades_marker(self):
        text = "\n".join("abcdefgh" for _ in range(20))
        chunks = chunk_reply(text, max_len=10, max_msgs=2, marker=DEFAULT_TRUNCATION_MARKER)
        assert len(chunks) == 2
        assert all(len(c) <= 10 for c in chunks)

    def test_no_marker_when_within_cap(self):
        text = "\n".join(f"line {i} with padding text here" for i in range(5))
        chunks = chunk_reply(text, max_len=60, max_msgs=10)
        assert not any(DEFAULT_TRUNCATION_MARKER in c for c in chunks)


# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


class TestRedact:
    def test_redacts_token(self):
        assert redact("failed with secret-bot-token-abc123 in URL", TOKEN) == (
            "failed with [redacted] in URL"
        )

    def test_empty_secret_noop(self):
        assert redact("nothing to hide", "") == "nothing to hide"
        assert redact("nothing to hide", None) == "nothing to hide"


# ---------------------------------------------------------------------------
# DiscordRest against a localhost stub
# ---------------------------------------------------------------------------


def start_stub(responder):
    """Run a ThreadingHTTPServer on 127.0.0.1:0; `responder(call, n)` returns
    (status, body_bytes, headers_dict). Returns (server, calls, shutdown)."""
    calls: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            calls.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                }
            )
            status, resp_body, headers = responder(calls[-1], len(calls))
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)

        do_GET = do_POST = do_PUT = _handle

        def log_message(self, *args):  # silence test output
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, calls


@pytest.fixture
def rest_factory():
    servers = []

    def _make(responder) -> tuple[DiscordRest, list[dict]]:
        server, calls = start_stub(responder)
        servers.append(server)
        rest = DiscordRest(
            TOKEN,
            base_url=f"http://127.0.0.1:{server.server_port}",
            timeout=5.0,
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({})),
        )
        return rest, calls

    yield _make
    for server in servers:
        server.shutdown()
        server.server_close()


class TestDiscordRest:
    def test_get_self_success_and_auth_header(self, rest_factory):
        def responder(call, n):
            return 200, json.dumps({"id": "999", "username": "kthx"}).encode(), {}

        rest, calls = rest_factory(responder)
        me = rest.get_self()
        assert me["id"] == "999"
        assert calls[0]["method"] == "GET"
        assert calls[0]["path"] == "/users/@me"
        assert calls[0]["headers"]["authorization"] == f"Bot {TOKEN}"
        assert "AgentKthx" in calls[0]["headers"]["user-agent"]

    def test_send_message_json_body(self, rest_factory):
        def responder(call, n):
            return 200, json.dumps({"id": "m1"}).encode(), {}

        rest, calls = rest_factory(responder)
        result = rest.send_message("chan-1", "hello there")
        assert result["id"] == "m1"
        assert calls[0]["path"] == "/channels/chan-1/messages"
        assert json.loads(calls[0]["body"]) == {"content": "hello there"}
        assert calls[0]["headers"]["content-type"] == "application/json"

    def test_send_message_rejects_oversized_content(self, rest_factory):
        rest, calls = rest_factory(lambda call, n: (200, b"{}", {}))
        with pytest.raises(ValueError, match="chunk_reply"):
            rest.send_message("chan", "x" * (MESSAGE_MAX_LEN + 1))
        assert calls == []  # nothing hit the wire

    def test_trigger_typing_204(self, rest_factory):
        rest, calls = rest_factory(lambda call, n: (204, b"", {}))
        assert rest.trigger_typing("chan-1") is None
        assert calls[0]["path"] == "/channels/chan-1/trigger-typing"

    def test_401_raises_parsed_error_and_redacts(self, rest_factory):
        body = json.dumps(
            {"code": 0, "message": f"401: Unauthorized for {TOKEN}"}
        ).encode()

        def responder(call, n):
            return 401, body, {}

        rest, _ = rest_factory(responder)
        with pytest.raises(DiscordRestError) as excinfo:
            rest.get_self()
        err = excinfo.value
        assert err.status == 401
        assert err.code == 0
        assert TOKEN not in str(err)
        assert "[redacted]" in str(err)

    def test_429_retry_after_then_success(self, rest_factory):
        def responder(call, n):
            if n == 1:
                return (
                    429,
                    json.dumps({"retry_after": 0.1}).encode(),
                    {"X-RateLimit-Global": "true"},
                )
            return 200, json.dumps({"ok": True}).encode(), {}

        rest, calls = rest_factory(responder)
        result = rest.get_self()
        assert result == {"ok": True}
        assert len(calls) == 2

    def test_429_twice_gives_up(self, rest_factory):
        def responder(call, n):
            return 429, json.dumps({"retry_after": 0.05}).encode(), {}

        rest, calls = rest_factory(responder)
        with pytest.raises(DiscordRestError) as excinfo:
            rest.get_self()
        assert excinfo.value.status == 429
        assert len(calls) == 2  # exactly one retry, no hammering

    def test_transport_error_wrapped(self, rest_factory):
        rest, _ = rest_factory(lambda call, n: (200, b"{}", {}))
        rest.base_url = "http://127.0.0.1:1"  # nothing listens on port 1
        with pytest.raises(DiscordRestError) as excinfo:
            rest.get_self()
        assert excinfo.value.status == 0
        assert TOKEN not in str(excinfo.value)

    # -- M2: slash registration + interactions --------------------------------

    def test_register_commands_global(self, rest_factory):
        def responder(call, n):
            return 200, json.dumps([{"id": "c1", "name": "ask"}]).encode(), {}

        rest, calls = rest_factory(responder)
        cmds = [{"name": "ask", "description": "d"}]
        out = rest.register_commands("app1", cmds)
        assert out[0]["name"] == "ask"
        assert calls[0]["method"] == "PUT"
        assert calls[0]["path"] == "/applications/app1/commands"
        assert json.loads(calls[0]["body"]) == cmds
        assert calls[0]["headers"]["authorization"] == f"Bot {TOKEN}"

    def test_register_commands_guild_scoped(self, rest_factory):
        rest, calls = rest_factory(lambda call, n: (200, b"[]", {}))
        rest.register_commands("app1", [{"name": "ask"}], guild_id="g1")
        assert calls[0]["path"] == "/applications/app1/guilds/g1/commands"

    def test_interaction_callback_no_auth_header(self, rest_factory):
        rest, calls = rest_factory(lambda call, n: (204, b"", {}))
        rest.interaction_callback("i1", "tok", {"type": 5})
        assert calls[0]["method"] == "POST"
        assert calls[0]["path"] == "/interactions/i1/tok/callback"
        assert json.loads(calls[0]["body"]) == {"type": 5}
        # auth is carried by the path token — no bot-token header
        assert "authorization" not in calls[0]["headers"]

    def test_followup_no_auth_and_flags(self, rest_factory):
        rest, calls = rest_factory(lambda call, n: (200, json.dumps({"id": "f1"}).encode(), {}))
        out = rest.followup("app1", "tok", {"content": "hi", "flags": 64})
        assert out == {"id": "f1"}
        assert calls[0]["path"] == "/webhooks/app1/tok"
        assert json.loads(calls[0]["body"]) == {"content": "hi", "flags": 64}
        assert "authorization" not in calls[0]["headers"]
