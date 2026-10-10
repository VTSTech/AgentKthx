"""
AgentKthx Plugin — Discord Gateway Client (stdlib-only)

Minimal RFC 6455 WebSocket client + Discord Gateway v10 op layer, written
against the Python standard library only (socket, ssl, struct, base64,
hashlib, threading). No compression is negotiated (no `compress` query
param), so all server payloads are unmasked text frames — no zlib-stream
inflater needed. See docs/DISCORD_PLUGIN_PLAN.md §5.

Layer map:

    SocketTransport   raw TCP+TLS + HTTP Upgrade handshake (the only
                      network-touching class; never imported by tests)
    Transport         seam interface — tests substitute FakeTransport
    GatewayClient     frame codec + message assembler + Discord op state
                      machine (identify / heartbeat / resume / watchdog)

Discord opcodes (Gateway v10):
    0 Dispatch, 1 Heartbeat, 2 Identify, 6 Resume, 7 Reconnect,
    9 Invalid Session, 10 Hello, 11 Heartbeat ACK

Fatal close codes (no reconnect): 4004 invalid token, 4013 invalid
intents, 4014 disallowed intents (MESSAGE_CONTENT not enabled).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse
from collections import deque

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_PAYLOAD = 2**31  # defensive guard; real Discord payloads are far smaller

# Discord Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_PRESENCE = 3
OP_VOICE_STATE = 4
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# RFC 6455 frame opcodes
FRAME_CONT = 0x0
FRAME_TEXT = 0x1
FRAME_BINARY = 0x2
FRAME_CLOSE = 0x8
FRAME_PING = 0x9
FRAME_PONG = 0xA

# Intents (Gateway v2). MESSAGE_CONTENT (1<<15) is PRIVILEGED and must be
# enabled in the Developer Portal; without it the gateway closes with 4014.
INTENT_GUILDS = 1 << 0
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_MESSAGE_CONTENT = 1 << 15

FATAL_CLOSE_CODES = {
    4004: "authentication failed (invalid token)",
    4013: "invalid intent(s) in Identify",
    4014: "disallowed intent(s) — enable MESSAGE CONTENT INTENT in the "
    "Developer Portal (Bot settings → Privileged Gateway Intents)",
}

BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """RFC 6455 framing violation (RSV bits, oversized payload, bad control frame)."""


class FatalGatewayError(Exception):
    """Gateway close code that must NOT be retried (auth/intent problems)."""

    def __init__(self, code: int, reason: str = ""):
        hint = FATAL_CLOSE_CODES.get(code, reason or "fatal gateway close")
        super().__init__(f"gateway closed with {code}: {hint}")
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------------------
# Frame codec (pure functions — unit-tested against hand-built RFC vectors)
# ---------------------------------------------------------------------------


def encode_frame(
    opcode: int, payload: bytes = b"", *, mask: bool = True, fin: bool = True
) -> bytes:
    """Build one RFC 6455 frame. Client frames MUST be masked (mask=True)."""
    b0 = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header = bytearray([b0, mask_bit | length])
    elif length < 65536:
        header = bytearray([b0, mask_bit | 126])
        header += struct.pack("!H", length)
    else:
        header = bytearray([b0, mask_bit | 127])
        header += struct.pack("!Q", length)
    if not mask:
        return bytes(header) + payload
    key = os.urandom(4)
    header += key
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def encode_text(message: str, **kwargs) -> bytes:
    """Build a masked text frame carrying a UTF-8 payload."""
    return encode_frame(FRAME_TEXT, message.encode("utf-8"), **kwargs)


def encode_close(code: int = 1000, reason: str = "", **kwargs) -> bytes:
    """Build a close frame with a 2-byte status code + UTF-8 reason."""
    return encode_frame(FRAME_CLOSE, struct.pack("!H", code) + reason.encode("utf-8"), **kwargs)


def decode_frames(buf: bytearray) -> list[tuple[bool, int, bytes]]:
    """
    Parse every complete frame out of `buf` (mutated: consumed bytes removed).

    Returns a list of (fin, opcode, payload). Accepts masked server frames
    even though RFC 6455 says servers must not mask (robustness over purity).
    Raises ProtocolError on framing violations.
    """
    frames: list[tuple[bool, int, bytes]] = []
    while True:
        if len(buf) < 2:
            return frames
        b0 = buf[0]
        b1 = buf[1]
        if b0 & 0x70:
            raise ProtocolError(f"RSV bits set (0x{b0:02x}) — no extension negotiated")
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < offset + 2:
                return frames
            length = struct.unpack_from("!H", buf, offset)[0]
            offset += 2
        elif length == 127:
            if len(buf) < offset + 8:
                return frames
            length = struct.unpack_from("!Q", buf, offset)[0]
            offset += 8
        if length > MAX_PAYLOAD:
            raise ProtocolError(f"frame payload {length} exceeds guard {MAX_PAYLOAD}")
        mask_key = b""
        if masked:
            if len(buf) < offset + 4:
                return frames
            mask_key = bytes(buf[offset : offset + 4])
            offset += 4
        if len(buf) < offset + length:
            return frames
        payload = bytes(buf[offset : offset + length])
        del buf[: offset + length]
        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        if opcode >= 0x8:  # control frame rules (RFC 6455 §5.5)
            if not fin:
                raise ProtocolError("fragmented control frame")
            if length > 125:
                raise ProtocolError("control frame payload > 125 bytes")
        frames.append((fin, opcode, payload))


def parse_close_code(payload: bytes) -> tuple[int, str]:
    """Extract (code, reason) from a close frame payload (RFC 6455 §5.5.1)."""
    if len(payload) < 2:
        return 1005, ""  # no status code present
    code = struct.unpack("!H", payload[:2])[0]
    reason = payload[2:].decode("utf-8", "replace")
    return code, reason


def expected_accept(key: str) -> str:
    """RFC 6455 §1.3 — Sec-WebSocket-Accept value for a given Sec-WebSocket-Key."""
    digest = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


# ---------------------------------------------------------------------------
# Transport seam
# ---------------------------------------------------------------------------


class Transport:
    """Blocking byte transport used by GatewayClient.

    Contract:
      connect()          perform the underlying connection (for sockets:
                         TCP + TLS + HTTP Upgrade, verify 101 + accept key)
      send(data)         write raw bytes (blocking)
      recv(timeout)      return available raw bytes; None on timeout;
                         b"" when the remote closed cleanly
      close()            release everything; idempotent
    """

    def connect(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def send(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def recv(self, timeout: float) -> bytes | None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class SocketTransport(Transport):
    """Real transport: TCP + TLS + RFC 6455 HTTP Upgrade handshake."""

    def __init__(self, url: str = DEFAULT_GATEWAY_URL, connect_timeout: float = 30.0):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "wss":
            raise ValueError(f"only wss:// gateway URLs are supported, got {parsed.scheme!r}")
        self._host = parsed.hostname or "gateway.discord.gg"
        self._port = parsed.port or 443
        self._path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self._connect_timeout = connect_timeout
        self._sock: ssl.SSLSocket | None = None

    def connect(self) -> None:
        raw = socket.create_connection((self._host, self._port), timeout=self._connect_timeout)
        ctx = ssl.create_default_context()
        self._sock = ctx.wrap_socket(raw, server_hostname=self._host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: AgentKthx (+https://github.com/VTSTech/AgentKthx)\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake: connection closed during upgrade")
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("handshake: response too large")
        head = response.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        status_line = head.split("\r\n", 1)[0]
        if " 101 " not in status_line + " ":
            raise ConnectionError(f"handshake: expected 101, got {status_line!r}")
        accept = ""
        for line in head.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                accept = value.strip()
                break
        if accept != expected_accept(key):
            raise ConnectionError("handshake: Sec-WebSocket-Accept mismatch")

    def send(self, data: bytes) -> None:
        if self._sock is None:
            raise ConnectionError("send: not connected")
        self._sock.sendall(data)

    def recv(self, timeout: float) -> bytes | None:
        if self._sock is None:
            raise ConnectionError("recv: not connected")
        self._sock.settimeout(timeout)
        try:
            return self._sock.recv(4096)
        except socket.timeout:
            return None

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Discord payload builders (pure, unit-tested)
# ---------------------------------------------------------------------------


def build_identify(token: str, intents: int, properties: dict | None = None) -> dict:
    return {
        "op": OP_IDENTIFY,
        "d": {
            "token": token,
            "intents": int(intents),
            "properties": properties
            or {
                "os": sys.platform,
                "browser": "AgentKthx",
                "device": "AgentKthx",
            },
        },
    }


def build_resume(token: str, session_id: str, seq: int) -> dict:
    return {
        "op": OP_RESUME,
        "d": {"token": token, "session_id": session_id, "seq": seq},
    }


def build_heartbeat(last_seq: int | None) -> dict:
    return {"op": OP_HEARTBEAT, "d": last_seq}


# ---------------------------------------------------------------------------
# GatewayClient
# ---------------------------------------------------------------------------


class GatewayClient:
    """
    Discord Gateway v10 client.

    on_dispatch(event_name, data) is invoked for every op-0 Dispatch
    (READY, RESUMED, MESSAGE_CREATE, ...). run_forever() blocks, owns
    reconnect/resume, and returns when stop() is called.
    """

    def __init__(
        self,
        token: str,
        intents: int,
        on_dispatch,
        *,
        transport_factory=None,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        log=None,
        connect_timeout: float = 30.0,
        invalid_session_wait: tuple[float, float] = (1.0, 5.0),
        min_heartbeat_s: float = 1.0,
    ):
        self.token = token
        self.intents = int(intents)
        self.on_dispatch = on_dispatch
        self._transport_factory = transport_factory or (
            lambda: SocketTransport(gateway_url, connect_timeout)
        )
        self._log = log or (lambda msg: None)
        self._invalid_session_wait = invalid_session_wait
        self._min_heartbeat_s = float(min_heartbeat_s)

        # session state
        self.bot_user_id: str | None = None
        self.session_id: str | None = None
        self.last_seq: int | None = None
        self.connected_at: float | None = None

        self._transport: Transport | None = None
        self._buf = bytearray()
        self._pending_frames: deque = deque()
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._hb_thread: threading.Thread | None = None
        self._hb_interval: float | None = None
        self._last_rx = 0.0

    # -- public lifecycle ---------------------------------------------------

    def run_forever(self) -> None:
        """Connect, reconnect with capped backoff, resume when possible."""
        attempt = 0
        while not self._stop.is_set():
            established = False
            try:
                established = self._connect_once()
                attempt = 0 if established else attempt + 1
            except FatalGatewayError:
                self._teardown()
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any drop
                if self._stop.is_set():
                    break
                self._log(f"[gateway] dropped: {exc!r}")
            finally:
                self._teardown()
            if self._stop.is_set():
                break
            delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (1.5 ** min(attempt, 10)))
            delay += random.uniform(0.0, 1.0)
            attempt += 1
            self._log(f"[gateway] reconnecting in {delay:.1f}s (attempt {attempt})")
            self._stop.wait(delay)

    def stop(self) -> None:
        """Thread-safe shutdown — wakes the recv loop and heartbeat thread."""
        self._stop.set()
        transport = self._transport
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001 - stop() must never raise
                pass

    # -- internals: connection ----------------------------------------------

    def _connect_once(self) -> bool:
        """One full session. Returns True if READY/RESUMED was established."""
        self._buf = bytearray()
        self._pending_frames.clear()
        self._last_rx = time.monotonic()
        self._transport = self._transport_factory()
        self._transport.connect()
        self._log("[gateway] connected, awaiting Hello")
        established = False
        while not self._stop.is_set():
            message = self._read_message()
            if message is None:
                break
            opcode, payload = message
            try:
                payload_obj = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(f"undecodable gateway payload: {exc}") from exc
            if not isinstance(payload_obj, dict) or "op" not in payload_obj:
                raise ProtocolError("gateway payload is not a {op, d} object")
            if self._handle_payload(payload_obj):
                established = True
        return established

    def _teardown(self) -> None:
        self._stop_heartbeat()
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    # -- internals: socket -> messages ---------------------------------------

    def _read_message(self) -> tuple[int, bytes] | None:
        """Next complete text/binary message; control frames handled inline.

        Returns None when stop() was requested. Raises ConnectionError on
        remote close / watchdog expiry.
        """
        fragments: list[bytes] = []
        frag_opcode = 0
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == FRAME_PING:
                self._send_frame(encode_frame(FRAME_PONG, payload))
                continue
            if opcode == FRAME_PONG:
                continue
            if opcode == FRAME_CLOSE:
                code, reason = parse_close_code(payload)
                if code in FATAL_CLOSE_CODES:
                    raise FatalGatewayError(code, reason)
                try:  # polite close handshake; failures are irrelevant
                    self._send_frame(encode_close(1000))
                except Exception:  # noqa: BLE001
                    pass
                raise ConnectionError(f"gateway close {code}: {reason}")
            if opcode in (FRAME_TEXT, FRAME_BINARY):
                if fin:
                    return opcode, payload
                fragments = [payload]
                frag_opcode = opcode
                continue
            if opcode == FRAME_CONT:
                if not fragments:
                    raise ProtocolError("continuation frame without a started message")
                fragments.append(payload)
                if fin:
                    return frag_opcode, b"".join(fragments)
                continue
            raise ProtocolError(f"unexpected frame opcode 0x{opcode:02x}")

    def _read_frame(self) -> tuple[bool, int, bytes]:
        while True:
            if self._pending_frames:
                return self._pending_frames.popleft()
            frames = decode_frames(self._buf)
            if frames:
                self._pending_frames.extend(frames[1:])
                return frames[0]
            if self._stop.is_set():
                raise ConnectionError("stop requested")
            assert self._transport is not None
            chunk = self._transport.recv(0.5)
            now = time.monotonic()
            if chunk is None:
                # recv timeout — enforce the activity watchdog before retrying
                if self._hb_interval is not None and now - self._last_rx > 2 * self._hb_interval:
                    raise ConnectionError("watchdog: no gateway activity for 2x heartbeat interval")
                continue
            if chunk == b"":
                raise ConnectionError("connection closed by remote")
            self._last_rx = now
            self._buf.extend(chunk)

    # -- internals: Discord op state machine ---------------------------------

    def _handle_payload(self, payload_obj: dict) -> bool:
        """Route one gateway payload. Returns True if session established."""
        op = payload_obj["op"]
        d = payload_obj.get("d")
        seq = payload_obj.get("s")
        event = payload_obj.get("t")
        if seq is not None:
            self.last_seq = seq

        if op == OP_HELLO:
            interval_ms = (d or {}).get("heartbeat_interval", 41250)
            self._start_heartbeat(max(self._min_heartbeat_s, float(interval_ms) / 1000.0))
            if self.session_id is not None and self.last_seq is not None:
                self._send_json(build_resume(self.token, self.session_id, self.last_seq))
                self._log(f"[gateway] resuming session {self.session_id} @ seq {self.last_seq}")
            else:
                self._send_json(build_identify(self.token, self.intents))
                self._log("[gateway] identifying")
            return False

        if op == OP_HEARTBEAT:
            self._send_json(build_heartbeat(self.last_seq))
            return False

        if op == OP_HEARTBEAT_ACK:
            return False

        if op == OP_RECONNECT:
            raise ConnectionError("server requested reconnect (op 7) — will resume")

        if op == OP_INVALID_SESSION:
            resumable = bool(d)
            if resumable and self.session_id is not None and self.last_seq is not None:
                self._send_json(build_resume(self.token, self.session_id, self.last_seq))
            else:
                self.session_id = None
                self.last_seq = None
                lo, hi = self._invalid_session_wait
                self._stop.wait(random.uniform(lo, hi))
                self._send_json(build_identify(self.token, self.intents))
            return False

        if op == OP_DISPATCH:
            if event == "READY":
                self.session_id = (d or {}).get("session_id")
                self.bot_user_id = ((d or {}).get("user") or {}).get("id")
                self.connected_at = time.monotonic()
                self._log(f"[gateway] READY — session {self.session_id}")
                self.on_dispatch("READY", d or {})
                return True
            if event == "RESUMED":
                self._log("[gateway] RESUMED")
                self.on_dispatch("RESUMED", d or {})
                return True
            self.on_dispatch(event, d or {})
            return False

        # Unknown opcodes MUST be ignored (Gateway v10 forward compat).
        self._log(f"[gateway] ignoring unknown op {op}")
        return False

    # -- internals: sending ---------------------------------------------------

    def _send_json(self, obj: dict) -> None:
        self._send_frame(encode_text(json.dumps(obj, separators=(",", ":"))))

    def _send_frame(self, frame: bytes) -> None:
        transport = self._transport
        if transport is None:
            raise ConnectionError("send: not connected")
        with self._send_lock:
            transport.send(frame)

    # -- internals: heartbeat ---------------------------------------------------

    def _start_heartbeat(self, interval: float) -> None:
        self._stop_heartbeat()
        self._hb_interval = interval
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop, name="discord-heartbeat", daemon=True
        )
        self._hb_thread.start()

    def _stop_heartbeat(self) -> None:
        self._hb_interval = None
        self._hb_thread = None  # loop exits via Event.wait + interval=None guard

    def _heartbeat_loop(self) -> None:
        # Event.wait doubles as both the sleep and the stop signal; the
        # interval is captured once per iteration so teardown (None) ends it.
        while not self._stop.is_set():
            interval = self._hb_interval
            if interval is None:
                return
            if self._stop.wait(interval):
                return
            try:
                self._send_json(build_heartbeat(self.last_seq))
            except Exception as exc:  # noqa: BLE001 - wake the main loop
                self._log(f"[gateway] heartbeat send failed: {exc!r}")
                transport = self._transport
                if transport is not None:
                    try:
                        transport.close()
                    except Exception:  # noqa: BLE001
                        pass
                return
