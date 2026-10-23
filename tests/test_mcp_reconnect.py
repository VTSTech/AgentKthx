"""R07.35 (MCP-06/MCP-07) — MCP auto-reconnect + timeout-tolerance tests.

Covers the mid-session recovery paths that previously required restarting
the whole agent:

* MCP-06 (R07.35): a crashed/fatal transport reconnects automatically.
* MCP-07 (R07.35): a SLOW call no longer destroys the connection at all.
  R07.35 decoded the owner's live failure: ``search_files`` over a large
  workspace exceeded ``timeout_seconds`` → the R07.24 poison-on-timeout
  design killed the connection → R07.35 re-dispatched the SAME slow call
  at the SAME timeout → "auto-reconnect failed after 2 attempt(s)" on a
  server that was alive the whole time.

Test layout (all offline — no npm/network):

* **Transport + client integration** (real subprocesses): a fake MCP
  echo server (stdlib-only script in ``tmp_path``, thread-per-message so
  one hanging call doesn't block the next) that hangs for a configurable
  duration on a ``hang`` marker argument and can push a
  ``notifications/tools/list_changed`` on a ``notify`` marker. Pins the
  Gone-vs-plain-vs-timeout error split, connection survival across a
  timeout, late-response drop, fast close, and the full
  ``MCPClient.reconnect()`` recovery after a REAL subprocess death.
* **Manager unit tests** (FakeClient monkeypatching, house style from
  ``test_mcp_scaffold.py``): ``_invoke`` auto-reconnect classification
  (Gone reconnects, protocol errors don't, legacy plain errors follow
  the liveness heuristic, single timeouts stay clean, consecutive
  timeouts reconnect as wedged), config kill-switch, attempt exhaustion
  with escalated re-dispatch timeout, ``_reconnect_server`` client swap.
* **Config tests**: the ``reconnect`` / ``reconnect_attempts`` /
  ``reconnect_backoff`` knobs parse from mcp.json with sane defaults.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.mcp.client import MCPClient, MCPClientError  # noqa: E402
from agentkthx.mcp.config import MCPServerConfig, load_mcp_config  # noqa: E402
from agentkthx.mcp.manager import MCPManager, MCPManagerError  # noqa: E402
from agentkthx.mcp.transport import (  # noqa: E402
    MCPRequestTimeoutError,
    MCPTransportError,
    MCPTransportGone,
    StdioTransport,
)

# ---------------------------------------------------------------------------
# Fake MCP echo server (real subprocess for transport/client tests)
# ---------------------------------------------------------------------------

# stdlib-only JSON-RPC echo server, thread-per-message so one hanging
# tools/call doesn't block the next (mirrors how a slow glob must not
# wedge the test's subsequent calls). Handles initialize / tools/list /
# tools/call:
#
# * q == "hang"      → sleeps 20s (far past any test timeout; parent
#                      kills us via close()/terminate())
# * q == "hang:N"    → sleeps N seconds (deterministic late-response
#                      tests: timeout at 1s, response arrives at Ns)
# * q == "notify"    → pushes notifications/tools/list_changed BEFORE
#                      the response (deferred-dispatch deadlock test)
# * anything else    → echoes back
# Unknown methods get a JSON-RPC error, which pins the "plain
# MCPTransportError, NOT Gone" classification.
_FAKE_ECHO_SERVER = textwrap.dedent("""
    import json
    import sys
    import threading
    import time

    _write_lock = threading.Lock()

    def _send(obj):
        with _write_lock:
            sys.stdout.write(json.dumps(obj) + "\\n")
            sys.stdout.flush()

    def _handle(msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return
        if "id" not in msg:
            return  # notification (shutdown/exit) — no response
        mid = msg["id"]
        method = msg.get("method", "")
        params = msg.get("params") or {}
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-echo", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo the q argument back",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"q": {"type": "string"}},
                            "required": ["q"],
                        },
                    }
                ]
            }
        elif method == "tools/call":
            args = params.get("arguments") or {}
            q = str(args.get("q", ""))
            if q == "notify":
                _send({
                    "jsonrpc": "2.0",
                    "method": "notifications/tools/list_changed",
                    "params": {},
                })
            if q.startswith("hang"):
                duration = 20.0
                if ":" in q:
                    try:
                        duration = float(q.split(":", 1)[1])
                    except ValueError:
                        pass
                time.sleep(duration)
            result = {"content": [{"type": "text", "text": "echo: " + q}]}
        else:
            _send({
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32601, "message": "method not found: " + method},
            })
            return
        _send({"jsonrpc": "2.0", "id": mid, "result": result})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        threading.Thread(target=_handle, args=(msg,), daemon=True).start()
    """).lstrip()


@pytest.fixture()
def echo_server_config(tmp_path: Path) -> MCPServerConfig:
    """Config for the fake echo server with a 1s per-call timeout."""
    script = tmp_path / "fake_echo_server.py"
    script.write_text(_FAKE_ECHO_SERVER, encoding="utf-8")
    return MCPServerConfig(
        name="flaky",
        command=sys.executable,
        args=[str(script)],
        timeout_seconds=1,
    )


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll ``predicate`` until true or ``timeout`` elapsed (async-test glue)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Integration: timeout tolerance + reconnect after real death (real subprocesses)
# ---------------------------------------------------------------------------


def test_timeout_does_not_kill_connection(echo_server_config):
    """Per-call timeout → MCPRequestTimeoutError, connection stays usable.

    Pins the R07.35 (MCP-07) behavior that fixes the owner's live
    failure: a SLOW call (here 2s against a 1s window) raises a plain
    (non-Gone) MCPRequestTimeoutError, the transport does NOT poison,
    and the very next call round-trips normally. The late response for
    the abandoned request is dropped as LATE-RESPONSE when it arrives.
    """
    transport = StdioTransport(echo_server_config)
    try:
        start = time.monotonic()
        with pytest.raises(MCPRequestTimeoutError) as exc_info:
            transport.request("tools/call", {"name": "echo", "arguments": {"q": "hang:2"}})
        elapsed = time.monotonic() - start

        # Timeout honored (1s window); the class is a PLAIN transport
        # error — NOT MCPTransportGone, so callers won't reconnect.
        assert 0.8 < elapsed < 5.0
        assert isinstance(exc_info.value, MCPTransportError)  # back-compat
        assert not isinstance(exc_info.value, MCPTransportGone)
        assert "connection kept open" in str(exc_info.value)
        assert transport.is_poisoned is False
        assert transport.consecutive_timeouts == 1

        # The connection survives — the next call round-trips normally
        # (thread-per-message server: the hanging call doesn't block it).
        result = transport.request("tools/call", {"name": "echo", "arguments": {"q": "fine"}})
        assert result["content"][0]["text"] == "echo: fine"
        # Any response resets the wedged-detection counter.
        assert transport.consecutive_timeouts == 0

        # The abandoned request's response arrives ~1s later (2s sleep
        # started at dispatch) — the reader must drop it, not mis-route.
        assert _wait_for(
            lambda: any("LATE-RESPONSE" in line for line in transport.stderr_tail),
            timeout=5.0,
        )
    finally:
        transport.close(timeout=0.2)


def test_client_reconnect_recovers_after_real_death(echo_server_config):
    """Full recovery round-trip: subprocess killed → Gone → reconnect() → call succeeds.

    This is the session-saving path: after the subprocess genuinely dies
    mid-session (EOF → poisoned — a REAL fatal failure under MCP-07),
    the client rebuilds the transport (fresh subprocess + initialize
    handshake), the poisoned flag clears, and the same client object
    serves calls again.
    """
    client = MCPClient(echo_server_config)
    try:
        client.connect()
        assert client.server_info.get("name") == "fake-echo"

        # Kill the subprocess out from under the client — the reader
        # thread hits EOF and marks the transport dead/poisoned.
        client._transport._proc.kill()
        assert _wait_for(lambda: client._transport._dead, timeout=5.0)

        # The next call fails FAST with Gone (poisoned fast-fail), not
        # a full timeout window.
        start = time.monotonic()
        with pytest.raises(MCPTransportGone) as fast_fail:
            client.call_tool("echo", {"q": "fine"})
        assert time.monotonic() - start < 1.0
        assert "poisoned" in str(fast_fail.value) or "reconnect" in str(fast_fail.value)
        assert client.is_poisoned is True

        # Reconnect — short teardown timeout (the old subprocess is
        # known-dead).
        old_transport = client._transport
        client.reconnect(timeout=0.2)

        assert client._transport is not old_transport
        assert client.is_poisoned is False
        assert client._initialized is True
        # Handshake re-ran on the fresh subprocess.
        assert client.server_info.get("name") == "fake-echo"

        # The same client serves calls again.
        result = client.call_tool("echo", {"q": "hello"})
        text = result["content"][0]["text"]
        assert text == "echo: hello"
    finally:
        client.close(timeout=0.2)


def test_notification_midcall_does_not_deadlock(echo_server_config):
    """A notification arriving mid-call must not deadlock the transport.

    Regression guard for the latent MCP-05 deadlock: the OLD reader
    dispatched notifications inline from the read path — a handler that
    called back into list_tools() → request() → the NON-reentrant
    transport lock (held by the in-flight call) blocked forever.
    R07.35 defers notifications: the reader only queues them and
    request() dispatches them outside the lock, so a manager-style
    refresh handler that re-enters the client completes cleanly.
    """
    client = MCPClient(echo_server_config)
    try:
        client.connect()

        refresh_calls: list = []

        def _refresh(_msg: dict) -> None:
            # Simulates MCPManager._refresh_tools_for_server: calls
            # back into the client from notification dispatch.
            refresh_calls.append(client.list_tools())

        client.set_notification_handler("notifications/tools/list_changed", _refresh)

        # The server pushes list_changed BEFORE the response — the OLD
        # code ran the handler mid-read on a locked transport.
        result = client.call_tool("echo", {"q": "notify"})
        assert result["content"][0]["text"] == "echo: notify"

        # The deferred handler ran (after the response, outside the lock)
        # and its nested tools/list round-trip completed.
        assert len(refresh_calls) == 1
        assert [t["name"] for t in refresh_calls[0]] == ["echo"]
    finally:
        client.close(timeout=0.2)


def test_close_teardown_is_fast(echo_server_config):
    """close() finishes well under its window: stdin closes first.

    R07.35 closes stdin BEFORE wait() — MCP stdio servers exit on stdin
    EOF — so teardown takes milliseconds instead of always burning the
    full wait() window and falling through to SIGTERM.
    """
    transport = StdioTransport(echo_server_config)
    transport.request("tools/list")  # spawn + round-trip
    assert transport.is_alive

    start = time.monotonic()
    transport.close(timeout=2.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.5  # the old code typically burned ~2s + SIGTERM
    assert transport.is_alive is False


def test_jsonrpc_error_is_plain_transport_error_not_gone(echo_server_config):
    """A JSON-RPC error response is NOT connection-fatal.

    The server is alive and answered — the error stays a plain
    ``MCPTransportError``, the transport stays unpoisoned and usable
    for the next call. Reconnecting here would be wrong (MCP-06).
    """
    transport = StdioTransport(echo_server_config)
    try:
        with pytest.raises(MCPTransportError) as exc_info:
            transport.request("bogus/method")
        assert not isinstance(exc_info.value, MCPTransportGone)
        assert "JSON-RPC error" in str(exc_info.value)
        assert transport.is_poisoned is False

        # The transport still works — alive and answering.
        result = transport.request("tools/list")
        tool_names = [t["name"] for t in result["tools"]]
        assert tool_names == ["echo"]
    finally:
        transport.close(timeout=0.2)


def test_client_reconnect_preserves_notification_handlers(monkeypatch):
    """Handlers registered on the client survive the transport swap.

    MCP-05 handlers live on ``MCPClient``, not the transport — reconnect
    must re-wire the NEW transport's callback to the client's trampoline
    so ``notifications/tools/list_changed`` keeps flowing after a
    mid-session recovery.
    """
    created: list = []

    class FakeTransport:
        def __init__(self, config):
            self.closed = False
            self.callback = None
            created.append(self)

        def set_notification_callback(self, cb):
            self.callback = cb

        def request(self, method, params=None, timeout=None):
            return {"serverInfo": {"name": "fake"}, "capabilities": {}}

        def notify(self, method, params=None):
            pass

        def close(self, timeout=2.0):
            self.closed = True

        @property
        def is_alive(self):
            return False

        @property
        def is_poisoned(self):
            return False

        @property
        def stderr_tail(self):
            return []

    monkeypatch.setattr("agentkthx.mcp.client.StdioTransport", FakeTransport)

    cfg = MCPServerConfig(name="test", command="python3")
    client = MCPClient(cfg)

    fired: list = []
    client.set_notification_handler("notifications/tools/list_changed", lambda m: fired.append(m))

    old = client._transport
    client.reconnect()

    # Old transport closed, brand-new one installed, handshake re-ran.
    assert old.closed is True
    assert client._transport is not old
    assert client._initialized is True
    assert len(created) == 2

    # The handler dict survived AND the new transport got the trampoline
    # (bound methods compare equal per (func, instance) — `is` would be
    # wrong since each attribute access creates a fresh bound object).
    assert "notifications/tools/list_changed" in client._notification_handlers
    assert client._transport.callback == client.handle_notification

    # End-to-end: a notification through the new transport fires the handler.
    client.handle_notification({"method": "notifications/tools/list_changed"})
    assert len(fired) == 1


# ---------------------------------------------------------------------------
# Manager unit tests (FakeClient — house style from test_mcp_scaffold.py)
# ---------------------------------------------------------------------------


def _seed_manager(cfg: MCPServerConfig, client) -> MCPManager:
    """Build a manager with one connected (fake) server + cached tool def."""
    mgr = MCPManager([cfg])
    mgr._clients[cfg.name] = client
    mgr._tools[f"{cfg.name}__echo"] = (
        cfg.name,
        "echo",
        {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}},
    )
    mgr._connected = True
    return mgr


def test_invoke_auto_reconnects_on_gone_error(monkeypatch, capsys):
    """Gone on dispatch → auto-reconnect → re-dispatch succeeds.

    The shim-tool path is invisible here: the manager swaps the client
    and re-runs the call, returning the sanitized result as if nothing
    happened. A reconnect line goes to stderr for operator visibility.
    """
    closed: list = []

    class DyingClient:
        """First instance raises Gone; its replacement serves the call."""

        def __init__(self, cfg):
            self.generation = len(closed)

        def connect(self):
            pass

        def call_tool(self, name, args):
            if self.generation == 0:
                raise MCPTransportGone("flaky: transport poisoned (simulated)")
            return {"content": [{"type": "text", "text": "recovered"}]}

        def list_tools(self):
            return [{"name": "echo", "description": "Echo"}]

        def close(self, timeout=2.0):
            closed.append(self.generation)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(
        name="flaky",
        command="python3",
        reconnect_attempts=2,
        reconnect_backoff=0.0,  # tests stay fast
    )
    original = DyingClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", DyingClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "hi"})

    assert "recovered" in result
    assert closed == [0]  # the dead client was closed
    assert mgr._clients["flaky"] is not original
    assert mgr._clients["flaky"].generation == 1

    err = capsys.readouterr().err
    assert "connection lost" in err
    assert "reconnecting (attempt 1/2)" in err


def test_invoke_reconnect_exhaustion_returns_clean_error(monkeypatch, capsys):
    """Reconnect rounds exhausted → ONE clean error naming both failures."""
    closed: list = []

    class AlwaysDyingClient:
        def __init__(self, cfg):
            AlwaysDyingClient.instances += 1

        instances = 0

        def connect(self):
            pass

        def call_tool(self, name, args):
            raise MCPTransportGone("flaky: transport poisoned (simulated)")

        def list_tools(self):
            return [{"name": "echo", "description": "Echo"}]

        def close(self, timeout=2.0):
            closed.append(True)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(
        name="flaky",
        command="python3",
        reconnect_attempts=2,
        reconnect_backoff=0.0,
    )
    mgr = _seed_manager(cfg, AlwaysDyingClient(cfg))
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", AlwaysDyingClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "hi"})

    # Clean string (model-readable), NOT a raise, names both the original
    # failure and the reconnect outcome.
    assert result.startswith("[MCP transport error:")
    assert "auto-reconnect failed after 2 attempt(s)" in result
    assert "transport poisoned" in result
    # Two fresh clients were attempted (the replacement each round).
    assert AlwaysDyingClient.instances == 3  # seed + 2 reconnect rounds
    # No raise escaped; the agent loop only ever sees the string.


def test_invoke_single_timeout_returns_clean_error_no_reconnect(monkeypatch, capsys):
    """One MCPRequestTimeoutError → clean '[MCP timeout:' error, NO reconnect.

    The R07.35 fix for the owner's live session: a slow call (search_files
    over a large workspace) must not churn the connection. The same
    client object stays installed; stderr says the connection is still
    healthy; the model gets actionable guidance instead of a reconnect
    failure saga.
    """
    closed: list = []

    class SlowCallClient:
        def __init__(self, cfg):
            pass

        def connect(self):
            pass

        def call_tool(self, name, args, timeout=None):
            raise MCPRequestTimeoutError(
                "flaky: timeout waiting for response to id=4 after 30.0s "
                "(subprocess alive — connection kept open)"
            )

        def list_tools(self):
            return [{"name": "echo", "description": "Echo"}]

        def close(self, timeout=2.0):
            closed.append(True)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def consecutive_timeouts(self):
            return 1

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(name="flaky", command="python3")
    original = SlowCallClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", SlowCallClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "big-glob"})

    assert result.startswith("[MCP timeout:")
    assert "connection kept open" in result  # original error surfaced
    assert "narrower arguments" in result  # actionable guidance
    assert closed == []  # connection NOT torn down
    assert mgr._clients["flaky"] is original  # same client stays installed

    err = capsys.readouterr().err
    assert "connection still healthy" in err
    assert "reconnecting" not in err


def test_invoke_wedged_server_reconnects_after_consecutive_timeouts(monkeypatch, capsys):
    """Two consecutive timeouts → wedged → reconnect → escalated re-dispatch.

    A server with an alive pipe but a stuck event loop times out on
    EVERY call. After _WEDGED_TIMEOUT_THRESHOLD consecutive timeouts the
    manager stops believing "the server is just slow" and takes the
    MCP-06 reconnect path — and the re-dispatch gets an ESCALATED
    timeout (timeout_seconds * (round+1), capped at 120s) so a recovered
    server gets a fair window instead of failing identically.
    """
    closed: list = []
    calls: list = []

    class WedgedThenHealedClient:
        built = 0

        def __init__(self, cfg):
            type(self).built += 1
            self.generation = type(self).built

        def connect(self):
            pass

        def call_tool(self, name, args, timeout=None):
            calls.append((self.generation, timeout))
            if self.generation == 1:
                raise MCPRequestTimeoutError(
                    "flaky: timeout waiting for response to id=4 after 30.0s "
                    "(subprocess alive — connection kept open)"
                )
            return {"content": [{"type": "text", "text": "wedged-recovered"}]}

        def list_tools(self):
            return [{"name": "echo", "description": "Echo"}]

        def close(self, timeout=2.0):
            closed.append(self.generation)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def consecutive_timeouts(self):
            return 2 if self.generation == 1 else 0

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(
        name="flaky",
        command="python3",
        timeout_seconds=30,
        reconnect_attempts=2,
        reconnect_backoff=0.0,
    )
    original = WedgedThenHealedClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", WedgedThenHealedClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "x"})

    assert "wedged-recovered" in result
    assert closed == [1]
    assert mgr._clients["flaky"].generation == 2
    # First dispatch used the default window (None); the re-dispatch got
    # the escalated one: 30s * (1+1) = 60s.
    assert calls == [(1, None), (2, 60.0)]

    err = capsys.readouterr().err
    assert "consecutive request timeouts" in err
    assert "treating server as wedged" in err
    assert "reconnecting (attempt 1/2)" in err
    assert "reconnected — call recovered" in err


def test_invoke_reconnect_disabled_returns_clean_error(monkeypatch):
    """``reconnect=False`` restores the pre-R07.35 behavior exactly."""
    closed: list = []

    class DyingClient:
        def __init__(self, cfg):
            pass

        def call_tool(self, name, args):
            raise MCPTransportGone("flaky: transport poisoned (simulated)")

        def list_tools(self):
            return []

        def close(self, timeout=2.0):
            closed.append(True)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(name="flaky", command="python3", reconnect=False)
    original = DyingClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", DyingClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "hi"})

    assert result.startswith("[MCP transport error:")
    assert "poisoned" in result
    # No reconnect: same client object, never closed, no new instances.
    assert closed == []
    assert mgr._clients["flaky"] is original


def test_invoke_no_reconnect_on_protocol_error(monkeypatch):
    """MCPClientError (server answered) must NOT trigger a reconnect."""
    closed: list = []

    class RudeClient:
        def __init__(self, cfg):
            pass

        def call_tool(self, name, args):
            raise MCPClientError("flaky: JSON-RPC error -32602 invalid params")

        def list_tools(self):
            return []

        def close(self, timeout=2.0):
            closed.append(True)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(name="flaky", command="python3")
    original = RudeClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", RudeClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "bad"})

    assert result.startswith("[MCP protocol error:")
    assert closed == []  # server is alive — nothing to reconnect
    assert mgr._clients["flaky"] is original


def test_invoke_no_reconnect_on_plain_error_when_client_alive(monkeypatch):
    """Plain MCPTransportError + alive client → old behavior (no rebuild)."""
    closed: list = []

    class PlainErrorClient:
        def __init__(self, cfg):
            pass

        def call_tool(self, name, args):
            raise MCPTransportError("flaky: JSON-RPC error 42 nope")

        def list_tools(self):
            return []

        def close(self, timeout=2.0):
            closed.append(True)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(name="flaky", command="python3")
    original = PlainErrorClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", PlainErrorClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "x"})
    assert result.startswith("[MCP transport error:")
    assert closed == []
    assert mgr._clients["flaky"] is original


def test_invoke_reconnects_on_plain_error_when_client_dead(monkeypatch):
    """Legacy heuristic: plain error + dead client → reconnect anyway.

    Covers transports written before R07.35 that raise the plain class
    for fatal conditions (and any third-party subclass doing the same).
    """
    closed: list = []

    class LegacyClient:
        """Old-style transport errors + the subprocess actually died."""

        def __init__(self, cfg):
            LegacyClient.built = getattr(LegacyClient, "built", 0) + 1
            self.generation = LegacyClient.built

        built = 0

        def connect(self):
            pass

        def call_tool(self, name, args):
            if self.generation == 1:
                raise MCPTransportError("flaky: subprocess not running")
            return {"content": [{"type": "text", "text": "legacy-recovered"}]}

        def list_tools(self):
            return [{"name": "echo", "description": "Echo"}]

        def close(self, timeout=2.0):
            closed.append(self.generation)

        @property
        def is_alive(self):
            return self.generation > 1

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(
        name="flaky",
        command="python3",
        reconnect_attempts=1,
        reconnect_backoff=0.0,
    )
    mgr = _seed_manager(cfg, LegacyClient(cfg))
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", LegacyClient)

    result = mgr._invoke("flaky", "echo", "flaky__echo", {"q": "x"})

    assert "legacy-recovered" in result
    assert closed == [1]


def test_reconnect_server_replaces_client_and_refreshes_surface(monkeypatch):
    """_reconnect_server: old closed, new installed, tool surface re-diffed."""
    closed: list = []

    class SwappableClient:
        def __init__(self, cfg):
            SwappableClient.built = getattr(SwappableClient, "built", 0) + 1
            self.generation = SwappableClient.built

        built = 0

        def connect(self):
            pass

        def list_tools(self):
            # Generation 2 adds a tool — proves the refresh path runs.
            if self.generation >= 2:
                return [
                    {"name": "echo", "description": "Echo"},
                    {"name": "extra", "description": "New after restart"},
                ]
            return [{"name": "echo", "description": "Echo"}]

        def call_tool(self, name, args):
            return {"content": [{"type": "text", "text": "ok"}]}

        def close(self, timeout=2.0):
            closed.append(self.generation)

        @property
        def is_alive(self):
            return True

        @property
        def is_poisoned(self):
            return False

        @property
        def server_info(self):
            return {}

        @property
        def server_capabilities(self):
            return {}

        @property
        def stderr_tail(self):
            return []

    cfg = MCPServerConfig(name="flaky", command="python3")
    original = SwappableClient(cfg)
    mgr = _seed_manager(cfg, original)
    monkeypatch.setattr("agentkthx.mcp.manager.MCPClient", SwappableClient)

    new_client = mgr._reconnect_server("flaky")

    assert new_client is not original
    assert new_client.generation == 2
    assert closed == [1]
    assert mgr._clients["flaky"] is new_client
    # The added tool surfaced in the manager's cache (diff ran).
    assert "flaky__extra" in mgr._tools


def test_reconnect_server_unknown_name_raises():
    """_reconnect_server on an unconfigured name → MCPManagerError."""
    cfg = MCPServerConfig(name="known", command="python3")
    mgr = MCPManager([cfg])
    with pytest.raises(MCPManagerError) as exc_info:
        mgr._reconnect_server("nope")
    assert "not configured" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Config: reconnect knobs parse from mcp.json
# ---------------------------------------------------------------------------


def test_config_reconnect_fields_parse_from_json(tmp_path):
    """Explicit reconnect knobs round-trip through load_mcp_config."""
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text(
        json.dumps(
            {
                "version": "0.1",
                "servers": [
                    {
                        "name": "filesystem",
                        "command": "python3",
                        "reconnect": False,
                        "reconnect_attempts": 5,
                        "reconnect_backoff": 1.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    configs = load_mcp_config(cfg_file)
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.reconnect is False
    assert cfg.reconnect_attempts == 5
    assert cfg.reconnect_backoff == 1.5


def test_config_reconnect_defaults_absent_fields(tmp_path):
    """Pre-R07.35 config files (no reconnect keys) get the defaults."""
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text(
        json.dumps({"version": "0.1", "servers": [{"name": "git", "command": "python3"}]}),
        encoding="utf-8",
    )
    configs = load_mcp_config(cfg_file)
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg.reconnect is True
    assert cfg.reconnect_attempts == 2
    assert cfg.reconnect_backoff == 0.5


def test_config_reconnect_bad_types_raise(tmp_path):
    """Non-numeric reconnect_attempts → MCPConfigError (existing guard)."""
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text(
        json.dumps(
            {
                "version": "0.1",
                "servers": [
                    {
                        "name": "bad",
                        "command": "python3",
                        "reconnect_attempts": "lots",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    from agentkthx.mcp.config import MCPConfigError

    with pytest.raises(MCPConfigError):
        load_mcp_config(cfg_file)


def test_dataclass_reconnect_fields_default():
    """Direct MCPServerConfig construction gets the reconnect defaults."""
    cfg = MCPServerConfig(name="x", command="python3")
    assert cfg.reconnect is True
    assert cfg.reconnect_attempts == 2
    assert cfg.reconnect_backoff == 0.5
