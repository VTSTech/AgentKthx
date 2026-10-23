"""R07.35 (MCP-06) — MCP auto-reconnect tests.

Covers the mid-session recovery path that previously required restarting
the whole agent: a hung or crashed MCP server poisons its transport
(R07.24 MCP-01), every later call raised "transport poisoned — close()
and reconnect to recover", and nothing ever reconnected automatically.

Test layout (all offline — no npm/network):

* **Transport + client integration** (real subprocesses): a fake MCP
  echo server (stdlib-only script in ``tmp_path``) that hangs on a
  marker argument. Pins the Gone-vs-plain error split, the poisoned
  fast-fail, and the full ``MCPClient.reconnect()`` recovery round-trip
  (fresh subprocess, re-handshake, surviving notification handlers).
* **Manager unit tests** (FakeClient monkeypatching, house style from
  ``test_mcp_scaffold.py``): ``_invoke`` auto-reconnect classification
  (Gone reconnects, protocol errors don't, legacy plain errors follow
  the liveness heuristic), config kill-switch, attempt exhaustion,
  ``_reconnect_server`` client swap.
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
    MCPTransportError,
    MCPTransportGone,
    StdioTransport,
)

# ---------------------------------------------------------------------------
# Fake MCP echo server (real subprocess for transport/client tests)
# ---------------------------------------------------------------------------

# stdlib-only JSON-RPC echo server. Handles initialize / tools/list /
# tools/call. tools/call SLEEPS FOREVER-ISH (20s, far past any test
# timeout) when the q argument is exactly "hang" — that's how tests
# trigger the per-call timeout → poisoned → Gone path deterministically.
# Anything else echoes back. Unknown methods get a JSON-RPC error, which
# pins the "plain MCPTransportError, NOT Gone" classification.
_FAKE_ECHO_SERVER = textwrap.dedent(
    """
    import json
    import sys
    import time

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            continue
        if "id" not in msg:
            continue  # notification (shutdown/exit) — no response
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
            q = args.get("q", "")
            if q == "hang":
                time.sleep(20)  # parent kills us via close()/terminate()
            result = {"content": [{"type": "text", "text": "echo: " + str(q)}]}
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {"code": -32601, "message": "method not found: " + method},
                    }
                )
                + "\\n"
            )
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
        sys.stdout.flush()
    """
).lstrip()


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


# ---------------------------------------------------------------------------
# Integration: transport poisoning + client reconnect (real subprocesses)
# ---------------------------------------------------------------------------


def test_timeout_poisons_transport_and_raises_gone(echo_server_config):
    """Per-call timeout → Gone (not plain), poisoned flag, fast-fail next call.

    Pins the R07.35 classification: the timeout raise is
    ``MCPTransportGone`` (still an ``MCPTransportError`` subclass so
    pre-existing except handlers keep working), the transport poisons
    itself (MCP-01 preserved), and the NEXT call fails fast with the
    poisoned message instead of burning another full timeout window.
    """
    transport = StdioTransport(echo_server_config)
    try:
        start = time.monotonic()
        with pytest.raises(MCPTransportGone) as exc_info:
            transport.request(
                "tools/call", {"name": "echo", "arguments": {"q": "hang"}}
            )
        elapsed = time.monotonic() - start

        # Timeout honored (1s window) and the class hierarchy intact.
        assert elapsed < 5.0
        assert isinstance(exc_info.value, MCPTransportError)  # back-compat
        assert transport.is_poisoned is True

        # Next call fails FAST with the poisoned message (no second
        # full timeout window — this is the exact error VTSTech hit).
        start = time.monotonic()
        with pytest.raises(MCPTransportGone) as fast_fail:
            transport.request(
                "tools/call", {"name": "echo", "arguments": {"q": "fine"}}
            )
        assert time.monotonic() - start < 1.0
        assert "poisoned" in str(fast_fail.value)
        assert "reconnect" in str(fast_fail.value)
    finally:
        transport.close(timeout=0.2)


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


def test_client_reconnect_recovers_after_poisoning(echo_server_config):
    """Full recovery round-trip: poison → reconnect() → call succeeds.

    This is the session-saving path: after the transport poisons, the
    client rebuilds it (fresh subprocess + initialize handshake), the
    poisoned flag clears, and the same client object serves calls again.
    """
    client = MCPClient(echo_server_config)
    try:
        client.connect()
        assert client.server_info.get("name") == "fake-echo"

        # Hang the server → poison the transport.
        with pytest.raises(MCPTransportGone):
            client.call_tool("echo", {"q": "hang"})
        assert client.is_poisoned is True

        # Reconnect — short teardown timeout (the old subprocess is
        # known-wedged; terminate() fires after 0.2s).
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
    client.set_notification_handler(
        "notifications/tools/list_changed", lambda m: fired.append(m)
    )

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
        json.dumps(
            {"version": "0.1", "servers": [{"name": "git", "command": "python3"}]}
        ),
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
