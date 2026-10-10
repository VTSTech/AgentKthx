"""
AgentKthx — Discord Gateway Client Tests (M0)

RFC 6455 frame codec vectors (masking, length forms, fragmentation,
control-frame rules, guards), Discord op state machine over FakeTransport
(Hello→Identify→READY, heartbeat, resume, fatal close codes), and the
activity watchdog. Zero network — the real SocketTransport is never
imported. See docs/DISCORD_PLUGIN_PLAN.md §16.

Written by VTSTech — https://www.vts-tech.org
"""

import json
import struct
import time

import pytest

from agentkthx.plugins.discord.gateway import (
    FRAME_CLOSE,
    FRAME_PING,
    FRAME_TEXT,
    OP_DISPATCH,
    OP_HEARTBEAT,
    OP_HEARTBEAT_ACK,
    OP_HELLO,
    OP_IDENTIFY,
    OP_INVALID_SESSION,
    OP_RECONNECT,
    OP_RESUME,
    FatalGatewayError,
    GatewayClient,
    ProtocolError,
    build_identify,
    build_resume,
    decode_frames,
    encode_close,
    encode_frame,
    encode_text,
    expected_accept,
    parse_close_code,
)

READY_PAYLOAD = {
    "op": OP_DISPATCH,
    "t": "READY",
    "s": 1,
    "d": {
        "session_id": "sess-A",
        "user": {"id": "999", "username": "kthx-bot"},
    },
}


def dispatch_json(t: str, seq: int, d: dict) -> bytes:
    payload = json.dumps({"op": OP_DISPATCH, "t": t, "s": seq, "d": d}, separators=(",", ":"))
    return encode_text(payload, mask=False)


def hello(interval_ms: int = 41250) -> bytes:
    payload = json.dumps(
        {"op": OP_HELLO, "d": {"heartbeat_interval": interval_ms}}, separators=(",", ":")
    )
    return encode_text(payload, mask=False)


# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------


class FakeTransport:
    """Scripted server->client byte stream + recording client->server log.

    eof_when_empty=True models a remote that closes cleanly once the script
    is consumed (recv -> b""), instead of an idle connection (recv -> None).
    """

    def __init__(self, script: list[bytes] | None = None, *, eof_when_empty: bool = False):
        self.script = list(script or [])
        self.sent: list[bytes] = []
        self.closed = False
        self.connects = 0
        self.rx_delay = 0.0
        self.eof_when_empty = eof_when_empty

    def queue(self, data: bytes) -> None:
        self.script.append(data)

    def connect(self) -> None:
        self.connects += 1

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    def recv(self, timeout: float) -> bytes | None:
        if self.script:
            time.sleep(self.rx_delay)
            return self.script.pop(0)
        if self.eof_when_empty:
            return b""
        time.sleep(min(timeout, 0.01))
        return None

    def close(self) -> None:
        self.closed = True

    # test helpers
    def sent_json(self) -> list[dict]:
        out = []
        for chunk in self.sent:
            for _fin, op, payload in decode_frames(bytearray(chunk)):
                if op == FRAME_TEXT:
                    out.append(json.loads(payload.decode("utf-8")))
        return out

    def sent_opcodes(self) -> list[int]:
        return [p["op"] for p in self.sent_json()]


# ---------------------------------------------------------------------------
# Frame codec vectors
# ---------------------------------------------------------------------------


class TestFrameCodec:
    def test_roundtrip_small_text(self):
        raw = encode_text("Hello")
        assert raw[0] == 0x81  # FIN + text
        assert raw[1] == 0x80 | 5  # MASK + 7-bit length
        buf = bytearray(raw)
        frames = decode_frames(buf)
        assert len(frames) == 1
        fin, op, payload = frames[0]
        assert (fin, op) == (True, FRAME_TEXT)
        assert payload == b"Hello"
        assert buf == bytearray()  # fully consumed

    def test_client_frames_are_masked(self):
        raw = encode_text("maskme")
        key = raw[2:6]
        masked = raw[6:]
        unmasked = bytes(b ^ key[i % 4] for i, b in enumerate(masked))
        assert unmasked == b"maskme"

    def test_16bit_length_form(self):
        payload = b"x" * 200
        raw = encode_frame(FRAME_TEXT, payload)
        assert raw[1] & 0x7F == 126
        assert struct.unpack_from("!H", raw, 2)[0] == 200
        frames = decode_frames(bytearray(raw))
        assert frames[0][2] == payload

    def test_64bit_length_form(self):
        payload = b"y" * 70000
        raw = encode_frame(FRAME_TEXT, payload)
        assert raw[1] & 0x7F == 127
        assert struct.unpack_from("!Q", raw, 2)[0] == 70000
        frames = decode_frames(bytearray(raw))
        assert frames[0][2] == payload

    def test_unmasked_decode(self):
        """Server frames are unmasked; decoder must accept them."""
        raw = encode_frame(FRAME_TEXT, b"from server", mask=False)
        assert raw[1] == 11  # no MASK bit, length 11
        frames = decode_frames(bytearray(raw))
        assert frames[0][2] == b"from server"

    def test_fragmentation_assembly(self):
        part1 = encode_frame(FRAME_TEXT, b"Hel", mask=False, fin=False)
        part2 = encode_frame(0x0, b"lo", mask=False, fin=True)
        frames = decode_frames(bytearray(part1 + part2))
        assert [(f, o) for f, o, _ in frames] == [(False, FRAME_TEXT), (True, 0x0)]
        assert b"".join(p for _, _, p in frames) == b"Hello"

    def test_truncated_buffer_returns_nothing(self):
        raw = encode_text("Hello")
        frames = decode_frames(bytearray(raw[:4]))
        assert frames == []

    def test_rsv_bits_rejected(self):
        raw = encode_text("hi", mask=False)
        raw = bytes([raw[0] | 0x40]) + raw[1:]  # set RSV1
        with pytest.raises(ProtocolError, match="RSV"):
            decode_frames(bytearray(raw))

    def test_oversized_length_guard(self):
        header = bytes([0x81, 0x7F]) + struct.pack("!Q", 2**32)
        with pytest.raises(ProtocolError, match="guard"):
            decode_frames(bytearray(header))

    def test_fragmented_control_frame_rejected(self):
        raw = encode_frame(FRAME_PING, b"p", mask=False, fin=False)
        with pytest.raises(ProtocolError, match="fragmented control"):
            decode_frames(bytearray(raw))

    def test_oversized_control_frame_rejected(self):
        raw = encode_frame(FRAME_PING, b"p" * 126, mask=False)
        with pytest.raises(ProtocolError, match="125"):
            decode_frames(bytearray(raw))

    def test_close_code_parse(self):
        raw = encode_close(4004, "auth failed")
        fin, op, payload = decode_frames(bytearray(raw))[0]
        assert (fin, op) == (True, FRAME_CLOSE)
        assert parse_close_code(payload) == (4004, "auth failed")
        assert parse_close_code(b"") == (1005, "")

    def test_multiple_frames_in_one_chunk(self):
        """Two frames arriving together must both survive (pending queue)."""
        raw = encode_text("one", mask=False) + encode_text("two", mask=False)
        frames = decode_frames(bytearray(raw))
        assert [p for _, _, p in frames] == [b"one", b"two"]

    def test_expected_accept_rfc_vector(self):
        # RFC 6455 §1.3 worked example
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        assert expected_accept(key) == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


class TestPayloadBuilders:
    def test_identify_shape(self):
        obj = build_identify("tok", 37377)
        assert obj["op"] == OP_IDENTIFY
        assert obj["d"]["token"] == "tok"
        assert obj["d"]["intents"] == 37377
        assert obj["d"]["properties"]["browser"] == "AgentKthx"

    def test_resume_shape(self):
        obj = build_resume("tok", "sess-1", 42)
        assert obj["op"] == OP_RESUME
        assert obj["d"] == {"token": "tok", "session_id": "sess-1", "seq": 42}


# ---------------------------------------------------------------------------
# Session state machine over FakeTransport
# ---------------------------------------------------------------------------


def make_client(script: list[bytes], **kwargs) -> tuple[GatewayClient, FakeTransport, list]:
    events: list[tuple[str, dict]] = []
    transport = FakeTransport(script)
    logs: list[str] = []

    def on_dispatch(event, data):
        events.append((event, data))

    kwargs.setdefault("min_heartbeat_s", 0.0)
    client = GatewayClient(
        "test-token",
        37377,
        on_dispatch,
        transport_factory=lambda: transport,
        log=logs.append,
        **kwargs,
    )
    return client, transport, events


class TestSessionStateMachine:
    def test_hello_then_identify_then_ready(self):
        client, transport, events = make_client(
            [hello(), encode_text(json.dumps(READY_PAYLOAD), mask=False)]
        )
        # stop after READY by queueing nothing and stopping via event
        import threading

        stopper = threading.Timer(0.3, client.stop)
        stopper.start()
        client.run_forever()
        stopper.join()
        sent = transport.sent_json()
        assert sent[0]["op"] == OP_IDENTIFY
        assert sent[0]["d"]["token"] == "test-token"
        assert sent[0]["d"]["intents"] == 37377
        assert client.session_id == "sess-A"
        assert client.bot_user_id == "999"
        assert client.last_seq == 1
        assert ("READY", READY_PAYLOAD["d"]) in events

    def test_heartbeat_sent_on_interval(self):
        client, transport, _ = make_client([hello(80)])
        import threading

        stopper = threading.Timer(0.3, client.stop)
        stopper.start()
        client.run_forever()
        stopper.join()
        heartbeats = [p for p in transport.sent_json() if p["op"] == OP_HEARTBEAT]
        assert len(heartbeats) >= 2  # 300ms window / 80ms interval

    def test_server_heartbeat_request_answered(self):
        hb_req = encode_text(json.dumps({"op": OP_HEARTBEAT, "d": None}), mask=False)
        client, transport, _ = make_client([hello(), hb_req])
        import threading

        stopper = threading.Timer(0.2, client.stop)
        stopper.start()
        client.run_forever()
        stopper.join()
        # identify (reply to hello) + heartbeat (reply to op 1)
        assert transport.sent_opcodes()[:2] == [OP_IDENTIFY, OP_HEARTBEAT]

    def test_dispatch_routed_to_callback(self):
        msg = dispatch_json("MESSAGE_CREATE", 2, {"id": "m1", "content": "hi"})
        client, transport, events = make_client(
            [hello(), encode_text(json.dumps(READY_PAYLOAD), mask=False), msg]
        )
        import threading

        threading.Timer(0.3, client.stop).start()
        client.run_forever()
        assert ("MESSAGE_CREATE", {"id": "m1", "content": "hi"}) in events

    def test_fatal_close_4014_no_retry(self):
        close = encode_close(4014, "disallowed intents", mask=False)
        client, transport, _ = make_client([hello(), close])
        with pytest.raises(FatalGatewayError) as excinfo:
            client.run_forever()
        assert excinfo.value.code == 4014
        assert "MESSAGE CONTENT" in str(excinfo.value)
        assert transport.closed

    def test_eof_triggers_reconnect_and_resume(self):
        """Session established, remote drops, reconnect sends Resume."""
        import threading

        t1 = FakeTransport(
            [hello(), encode_text(json.dumps(READY_PAYLOAD), mask=False)],
            eof_when_empty=True,
        )
        t2 = FakeTransport([hello()], eof_when_empty=True)
        events: list = []
        transports = [t1, t2]
        client = GatewayClient(
            "test-token",
            37377,
            lambda e, d: events.append((e, d)),
            transport_factory=lambda: transports.pop(0) if transports else t2,
            log=lambda m: None,
            min_heartbeat_s=0.0,
        )
        threading.Timer(5.0, client.stop).start()  # safety net — never hang

        # After t2's Hello arrives, stop once resume has been sent.
        def maybe_stop():
            if any(p.get("op") == OP_RESUME for p in t2.sent_json()):
                client.stop()
            else:
                threading.Timer(0.05, maybe_stop).start()

        threading.Timer(0.05, maybe_stop).start()
        client.run_forever()
        resumes = [p for p in t2.sent_json() if p["op"] == OP_RESUME]
        assert len(resumes) == 1
        assert resumes[0]["d"]["session_id"] == "sess-A"
        assert resumes[0]["d"]["seq"] == 1
        assert client.session_id == "sess-A"

    def test_invalid_session_false_reidentifies(self):
        """op 9 with d=false clears the session and sends a fresh Identify."""
        ready2 = {
            "op": OP_DISPATCH,
            "t": "READY",
            "s": 5,
            "d": {"session_id": "sess-B", "user": {"id": "999"}},
        }
        invalid = encode_text(json.dumps({"op": OP_INVALID_SESSION, "d": False}), mask=False)
        client, transport, _ = make_client(
            [hello(), invalid, encode_text(json.dumps(ready2), mask=False)],
            invalid_session_wait=(0.0, 0.0),
        )
        import threading

        threading.Timer(0.3, client.stop).start()
        client.run_forever()
        ops = transport.sent_opcodes()
        assert ops.count(OP_IDENTIFY) == 2  # initial + re-identify
        assert client.session_id == "sess-B"

    def test_op7_reconnect_is_not_fatal(self):
        reconnect = encode_text(json.dumps({"op": OP_RECONNECT, "d": None}), mask=False)
        client, _, _ = make_client([hello(), reconnect])
        # run_forever should treat op 7 as a drop, not a FatalGatewayError.
        import threading

        threading.Timer(0.4, client.stop).start()
        client.run_forever()  # must not raise

    def test_heartbeat_ack_and_unknown_ops_ignored(self):
        ack = encode_text(json.dumps({"op": OP_HEARTBEAT_ACK, "d": None}), mask=False)
        unknown = encode_text(json.dumps({"op": 99, "d": None}), mask=False)
        client, _, _ = make_client([hello(), ack, unknown])
        import threading

        threading.Timer(0.2, client.stop).start()
        client.run_forever()  # no exception — unknown ops MUST be ignored


class TestWatchdog:
    def test_no_activity_forces_reconnect(self):
        """After Hello with a tiny interval, silence trips the watchdog."""
        # Only Hello arrives; nothing afterwards. Watchdog = 2 * 50ms = 100ms.
        client, transport, _ = make_client([hello(50)])
        start = time.monotonic()
        with pytest.raises(Exception) as excinfo:  # ConnectionError from watchdog
            client._connect_once()
        elapsed = time.monotonic() - start
        assert "watchdog" in str(excinfo.value)
        assert elapsed < 2.0  # watchdog fired, not hung
        client.stop()
