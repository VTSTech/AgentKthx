"""
DuckDuckGo plugin — backend regression tests (x-vqd-hash-1 protocol).

Verifies that the DuckDuckGoBackend (the 12th cloud backend, and the
ONLY one that subclasses BaseBackend directly instead of CloudBackend —
the /duckchat/v1 protocol is non-OpenAI) correctly implements the
CHALLENGE-BASED protocol that replaced the retired x-vqd-4 token
(2026-10 rotation):

  - BaseBackend (NOT CloudBackend) inheritance — the design pin
  - BackendType.DUCKDUCKGO enum value; base_url → https://duck.ai
  - Keyless construction (no API-key validation — nothing to validate)
  - Proof lifecycle: /status bootstrap (x-vqd-accept: 1 → challenge
    header), Node solve (DDG_SOLVE_UA injected), X-Vqd-Hash-1 build
    (server_hashes passthrough, client_hashes SHA-256-hashed, meta
    gains origin/stack/duration), per-turn rotation via the /chat
    response header
  - Chat wire shape: {model, messages, canUseTools:false,
    canUseApproxLocation:null, reasoningEffort (per-model),
    durableStream{messageId, conversationId, publicKey}} — sampling
    params dropped
  - Role-based SSE grammar (assistant/source/tool-invocation/
    ui-component/error) + legacy action-chunk fallback + bracketed
    markers ([DONE] / [PING] / [LIMIT_CONVERSATION] / [CHAT_TITLE:])
  - generate() buffered happy path + estimated usage
  - generate_stream() TRUE incremental yield (FEAT-10 — closed)
  - Error taxonomy with remediation messages: 401 re-solve retry
    (once), 404 ERR_MODEL_UNAVAILABLE (catalog rotation), 403
    anti-bot, 429 rate limit, 5xx upstream, ERR_CHALLENGE re-solve
    (once, then "persisted"), ERR_CONVERSATION_LIMIT / bare
    [LIMIT_CONVERSATION]
  - fetch_capabilities() — live catalog discovery, 401-gated retry
    with a solved proof, soft-fail to None
  - Catalog: 8 client-verified models served offline from the seed,
    cache-routed, alias-aware
  - Capability verdicts as PROTOCOL FACTS: REACT tools / NO thinking
  - Alias resolution (2025-era wire IDs + short aliases → the
    current catalog)
  - _collapse_system_into_user (system folds into the first user
    turn; tool/function roles fold to user — DDG only understands
    user/assistant)
  - Node dependency: fail-fast remediation when node is missing;
    bundled .js helpers ship with the plugin
  - Plugin manifest + register()/unregister() contract
  - model_seed.json carries the 8-model duckduckgo catalog
  - config.py + CLI env reference expose the DUCKDUCKGO_* vars

The Node solver/keygen subprocesses are FAKED at the instance level
(_solve_challenge/_mint_durable) — these tests exercise the Python
protocol plumbing; the solver itself is verified offline against
captured challenges by the ddg-challenge-solver package.
"""

import base64
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.base import BaseBackend  # noqa: E402
from agentkthx.core.types import BackendType, ThinkingSupport, ToolSupportLevel  # noqa: E402
from agentkthx.plugins.duckduckgo import duckduckgo as ddg  # noqa: E402
from agentkthx.plugins.duckduckgo.duckduckgo import (  # noqa: E402
    _MODEL_ALIASES,
    _WIRE_MODELS,
    DuckDuckGoBackend,
    _classify_sse_payload,
    _resolve_model,
)

# The 8 client-verified wire IDs (live duck.ai dropdown + the
# benoitpetit/duckduckgo-chat-cli models.go cross-check, 2026-10-09).
CANONICAL_IDS = {
    "gpt-6-luna",
    "gpt-5.6-luna",
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "claude-haiku-4-5",
    "mistral-small-2603",
    "tinfoil/gpt-oss-120b",
    "tinfoil/gemma4-31b",
}

BASE = "https://duck.ai"
STATUS_URL = f"{BASE}/duckchat/v1/status"
CHAT_URL = f"{BASE}/duckchat/v1/chat"
CAPABILITIES_URL = f"{BASE}/duckchat/v1/capabilities"

# Canned RAW solver output — what ddg_vqd.js emits for a challenge.
_RAW_SOLUTION = {
    "server_hashes": ["srv-1", "srv-2", "srv-3"],
    "client_hashes": [
        "Mozilla/5.0 (X11; Linux x86_64) Chrome/136",
        "probe2-raw",
        "95",
    ],
    "signals": {},
    "meta": {
        "v": "4",
        "challenge_id": "cid-abc123",
        "timestamp": "2026-10-09T00:00:00Z",
        "debug": "",
    },
}

# Canned durableStream state — what ddg_durable.js mints.
_DURABLE = {
    "conversation_id": "conv" + "0" * 28,
    "public_key": {"kty": "RSA", "n": "abc123", "e": "AQAB"},
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal urllib response double: read(n) chunking + headers."""

    def __init__(self, body: bytes = b"", headers: dict | None = None, status: int = 200):
        self._data = body
        self._pos = 0
        self.headers = headers or {}
        self.status = status

    def read(self, n: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos : self._pos + n] if n and n > 0 else self._data[self._pos :]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sse(*events: str) -> bytes:
    """Build a DDG SSE body from `data: ...` payload strings."""
    return ("\n".join(f"data: {e}" for e in events) + "\n").encode("utf-8")


def _status_ok(challenge: str = "CHAL-S1") -> _FakeResponse:
    """A /status response carrying a fresh challenge."""
    return _FakeResponse(b'{"status":"ok"}', {"x-vqd-hash-1": challenge})


def _chat_ok(chunks: list[str], fresh: str = "CHAL-C1") -> _FakeResponse:
    """A role-based /chat SSE success carrying the next challenge."""
    body = _sse(
        *[json.dumps({"role": "assistant", "message": c}) for c in chunks],
        "[DONE]",
    )
    return _FakeResponse(body, {"x-vqd-hash-1": fresh})


def _chat_sse_error(err_type: str, status: int = 401, fresh: str = "CHAL-C1") -> _FakeResponse:
    """A /chat 200 whose SSE stream carries an error frame."""
    body = _sse(json.dumps({"action": "error", "status": status, "type": err_type}))
    return _FakeResponse(body, {"x-vqd-hash-1": fresh})


def _http_error(
    code: int, body: bytes = b"", url: str = CHAT_URL, headers: dict | None = None
) -> urllib.error.HTTPError:
    """A raisable HTTPError with a drainable body (+ optional headers
    — e.g. the x-vqd-hash-1 ladder challenge a 418 teapot carries)."""
    return urllib.error.HTTPError(url, code, "boom", headers or {}, io.BytesIO(body))


class _Recorder:
    """urlopen double dispatching by URL, recording every request."""

    def __init__(self):
        self.requests: list[urllib.request.Request] = []
        self.status_responses: list = []
        self.chat_responses: list = [_chat_ok(["Hello", " world"], "CHAL-C1")]
        self.chat_errors: list = []  # exceptions raised before any response
        self.capabilities_responses: list = []
        self.capabilities_errors: list = []
        self.root_responses: list = []  # GET / (fe-meta scrape)

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        if url == STATUS_URL:
            if self.status_responses:
                return self.status_responses.pop(0)
            return _status_ok("CHAL-S-extra")
        if url == CHAT_URL:
            if self.chat_errors:
                raise self.chat_errors.pop(0)
            if self.chat_responses:
                return self.chat_responses.pop(0)
            return _chat_ok(["fallback"], "CHAL-C-next")
        if url == CAPABILITIES_URL:
            if self.capabilities_errors:
                raise self.capabilities_errors.pop(0)
            if self.capabilities_responses:
                return self.capabilities_responses.pop(0)
            return _FakeResponse(b"{}", {})
        if url in (BASE, f"{BASE}/"):
            # The fe-meta scrape (best-effort HTML read)
            if self.root_responses:
                return self.root_responses.pop(0)
            return _FakeResponse(b"<html></html>", {})
        raise AssertionError(f"unexpected URL: {url}")

    # -- assertion helpers -------------------------------------------------
    @property
    def chat_requests(self):
        return [r for r in self.requests if r.full_url == CHAT_URL]

    @property
    def status_requests(self):
        return [r for r in self.requests if r.full_url == STATUS_URL]

    @property
    def capabilities_requests(self):
        return [r for r in self.requests if r.full_url == CAPABILITIES_URL]

    @staticmethod
    def header(req: urllib.request.Request, name: str):
        # urllib.request.Request capitalizes header keys on storage
        # ("X-Vqd-Hash-1" -> "X-vqd-hash-1"); try the common spellings.
        for key in (name, name.capitalize(), name.lower(), name.upper()):
            if key in req.headers:
                return req.headers[key]
        return None

    def chat_header(self, index: int, name: str):
        return self.header(self.chat_requests[index], name)

    def chat_body(self, index: int) -> dict:
        return json.loads(self.chat_requests[index].data.decode("utf-8"))


@pytest.fixture
def backend():
    """A DuckDuckGoBackend with the Node helpers FAKED (no subprocess
    spawn) and clean proof state (catalog auto-isolated by the conftest
    _isolated_model_cache fixture)."""
    b = DuckDuckGoBackend()
    b._node_ok = True  # solver/keygen faked below — node never spawns
    b._solve_challenge = lambda ch: json.loads(json.dumps(_RAW_SOLUTION))
    b._mint_durable = lambda: json.loads(json.dumps(_DURABLE))
    b._MIN_CHAT_INTERVAL_S = 0  # pacing off — the suite never waits
    DuckDuckGoBackend._last_chat_monotonic = 0.0  # no cross-test pace leaks
    return b


@pytest.fixture
def recorder():
    return _Recorder()


def _wire(monkeypatch, recorder: _Recorder) -> None:
    monkeypatch.setattr("urllib.request.urlopen", recorder)


# ---------------------------------------------------------------------------
# Identity & wiring
# ---------------------------------------------------------------------------


class TestDuckDuckGoIdentity:
    """Class wiring pins — the scaffold's core design decisions."""

    def test_subclasses_base_backend_directly(self):
        """THE design pin: DDG is cloud but NOT CloudBackend — the
        /duckchat/v1 protocol (challenge handshake, own SSE shape, no
        /models, no sampling params) shares nothing with the
        OpenAI-compat shared transport."""
        from agentkthx.backends.cloud_base import CloudBackend

        assert issubclass(DuckDuckGoBackend, BaseBackend)
        assert not issubclass(DuckDuckGoBackend, CloudBackend)

    def test_backend_type_enum(self):
        assert BackendType.DUCKDUCKGO.value == "duckduckgo"
        assert DuckDuckGoBackend().backend_type is BackendType.DUCKDUCKGO

    def test_is_cloud_true(self, backend):
        assert backend.is_cloud is True

    def test_base_url_default_is_duck_ai(self, backend):
        """The 2026-10 protocol rotation moved the chat surface from
        duckduckgo.com to duck.ai."""
        assert backend.base_url == "https://duck.ai"

    def test_keyless_construction_needs_no_api_key(self, monkeypatch):
        """No API key env var, no key kwarg — construction succeeds
        (contrast: CloudBackend subclasses raise on a missing key)."""
        monkeypatch.delenv("DUCKDUCKGO_USER_AGENT", raising=False)
        b = DuckDuckGoBackend()  # no key anywhere — fine
        assert b._user_agent.startswith("Mozilla/5.0")

    def test_user_agent_precedence(self, backend):
        """Explicit kwarg beats the env var beats the built-in default."""
        b = DuckDuckGoBackend(user_agent="MyUA/1.0")
        assert b._user_agent == "MyUA/1.0"

    def test_default_model_constant(self):
        assert ddg.DUCKDUCKGO_DEFAULT_MODEL_STR == "gpt-6-luna"


# ---------------------------------------------------------------------------
# Node dependency + solver subprocess plumbing
# ---------------------------------------------------------------------------


class TestNodeDependency:
    """Node is a runtime dep (challenge solver + keygen) — fail fast."""

    def test_missing_node_fails_fast_with_remediation(self):
        b = DuckDuckGoBackend()
        b._node_ok = False
        with pytest.raises(RuntimeError, match="Node.js"):
            b._require_node("challenge solving")
        with pytest.raises(RuntimeError, match="Node.js"):
            b._solve_challenge("X")
        with pytest.raises(RuntimeError, match="Node.js"):
            b._mint_durable()

    def test_solve_challenge_spawns_node_with_solve_ua(self, monkeypatch):
        """The solver subprocess MUST see DDG_SOLVE_UA = the request
        UA — a mismatch invalidates the proof."""
        captured = {}

        class _Proc:
            returncode = 0
            stdout = json.dumps(_RAW_SOLUTION).encode("utf-8")
            stderr = b""

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["input"] = kwargs.get("input")
            captured["env"] = kwargs.get("env")
            return _Proc()

        monkeypatch.setattr(ddg.subprocess, "run", fake_run)
        b = DuckDuckGoBackend(user_agent="MyUA/1.0")
        b._node_ok = True
        raw = b._solve_challenge("CHALLENGE-B64")
        assert captured["argv"][:2] == ["node", ddg._SOLVER_JS]
        assert captured["input"] == b"CHALLENGE-B64"
        assert captured["env"]["DDG_SOLVE_UA"] == "MyUA/1.0"
        assert raw["server_hashes"] == _RAW_SOLUTION["server_hashes"]

    def test_solve_challenge_failure_raises(self, monkeypatch):
        class _Proc:
            returncode = 1
            stdout = b""
            stderr = b"solver exploded"

        monkeypatch.setattr(ddg.subprocess, "run", lambda *a, **k: _Proc())
        b = DuckDuckGoBackend()
        b._node_ok = True
        with pytest.raises(RuntimeError, match="challenge solver failed"):
            b._solve_challenge("X")

    def test_mint_durable_failure_raises(self, monkeypatch):
        class _Proc:
            returncode = 1
            stdout = b""
            stderr = b"keygen exploded"

        monkeypatch.setattr(ddg.subprocess, "run", lambda *a, **k: _Proc())
        b = DuckDuckGoBackend()
        b._node_ok = True
        with pytest.raises(RuntimeError, match="durableStream keygen failed"):
            b._mint_durable()

    def test_node_helpers_ship_with_plugin(self):
        """The .js helpers are runtime deps — they must be in the
        plugin package (self-contained backend)."""
        assert Path(ddg._SOLVER_JS).is_file()
        assert Path(ddg._SOLVER_JS).name == "ddg_vqd.js"
        assert Path(ddg._DURABLE_JS).is_file()
        assert Path(ddg._DURABLE_JS).name == "ddg_durable.js"


# ---------------------------------------------------------------------------
# Proof lifecycle
# ---------------------------------------------------------------------------


class TestProofLifecycle:
    """Challenge bootstrap → solve → X-Vqd-Hash-1 build → rotation."""

    def test_status_headers_carry_vqd_accept(self, backend):
        """x-vqd-accept: 1 is the opt-in that makes /status issue a
        challenge instead of the bare status JSON."""
        headers = backend._build_status_headers()
        assert headers["x-vqd-accept"] == "1"
        assert headers["Referer"] == "https://duck.ai/"
        assert headers["User-Agent"].startswith("Mozilla/5.0")
        assert re.fullmatch(r"[0-9a-f]{32}", headers["x-ddg-journey-id"])

    def test_missing_status_challenge_raises(self, backend, recorder, monkeypatch):
        """/status without the x-vqd-hash-1 header = protocol rotated
        again — surface it loudly."""
        recorder.status_responses = [_FakeResponse(b'{"status":"ok"}', {})]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="no x-vqd-hash-1"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_chat_headers_carry_proof_and_browser_contract(self, backend):
        headers = backend._build_chat_headers("PROOF-VALUE")
        assert headers["X-Vqd-Hash-1"] == "PROOF-VALUE"
        assert headers["Accept"] == "text/event-stream"
        assert headers["Origin"] == "https://duck.ai"
        assert headers["Referer"] == "https://duck.ai/"
        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"].startswith("Mozilla/5.0")
        assert headers["x-fe-version"].startswith("serp_")
        assert re.fullmatch(r"[0-9a-f]{32}", headers["x-ddg-journey-id"])

    def test_fe_signals_decode_to_engagement_events(self, backend):
        signals = backend._fe_signals()
        payload = json.loads(base64.b64decode(signals))
        names = [e["name"] for e in payload["events"]]
        assert "startNewChat_free" in names
        assert "action" in names
        assert payload["end"] >= payload["start"]

    def test_build_proof_header_hashes_client_hashes(self, backend):
        """The page bundle's post-processing: server_hashes pass through
        UNTOUCHED, client_hashes become base64(SHA256(String(value))),
        meta gains origin/stack/duration."""
        hdr = backend._build_proof_header(_RAW_SOLUTION, 1234)
        assert hdr.startswith("eyJ")  # base64 of a JSON object
        sol = json.loads(base64.b64decode(hdr))
        assert sol["server_hashes"] == _RAW_SOLUTION["server_hashes"]
        assert sol["client_hashes"] == [
            base64.b64encode(hashlib.sha256(str(v).encode()).digest()).decode()
            for v in _RAW_SOLUTION["client_hashes"]
        ]
        assert sol["signals"] == {}
        assert sol["meta"]["origin"] == "https://duck.ai"
        assert "duckai-dist" in sol["meta"]["stack"]
        assert sol["meta"]["duration"] == "1234"
        assert sol["meta"]["v"] == "4"
        assert sol["meta"]["challenge_id"] == "cid-abc123"

    def test_scrape_fe_meta_refreshes_version(self, backend, recorder, monkeypatch):
        """The serp version is scraped from GET / when available."""
        html = (
            b'<script src="/dist/duckai-dist/entry.duckai.deadbeefcafe.js">'
            b"</script>serp_20261009_120000_ET-abcdef123"
        )
        recorder.root_responses = [_FakeResponse(html, {}), _FakeResponse(html, {})]
        _wire(monkeypatch, recorder)
        meta = backend._scrape_fe_meta()
        assert meta["fe_version"] == "serp_20261009_120000_ET-abcdef123"
        assert meta["entry_js"] == "entry.duckai.deadbeefcafe.js"
        headers = backend._build_chat_headers("P")
        assert headers["x-fe-version"] == "serp_20261009_120000_ET-abcdef123"
        assert "entry.duckai.deadbeefcafe.js" in backend._synth_stack()


# ---------------------------------------------------------------------------
# SSE grammar
# ---------------------------------------------------------------------------


class TestSSEGrammar:
    """_classify_sse_payload — role-based (current), action-based
    (legacy fallback), and bracketed markers."""

    def test_assistant_text_delta(self):
        ev = _classify_sse_payload(json.dumps({"role": "assistant", "message": "hi"}).encode())
        assert ev == {"kind": "text", "text": "hi"}

    def test_assistant_state_only_frame_ignored(self):
        assert (
            _classify_sse_payload(json.dumps({"role": "assistant", "status": "thinking"}).encode())
            is None
        )

    def test_source_event(self):
        ev = _classify_sse_payload(
            json.dumps({"role": "source", "source": {"url": "https://x", "title": "X"}}).encode()
        )
        assert ev == {"kind": "source", "url": "https://x", "title": "X"}

    def test_source_without_url_ignored(self):
        assert _classify_sse_payload(json.dumps({"role": "source", "source": {}}).encode()) is None

    def test_tool_invocation_event(self):
        ev = _classify_sse_payload(
            json.dumps(
                {"role": "tool-invocation", "toolName": "WebSearch", "state": "done"}
            ).encode()
        )
        assert ev == {"kind": "tool", "tool": "WebSearch", "state": "done"}

    def test_ui_component_event(self):
        ev = _classify_sse_payload(json.dumps({"role": "ui-component", "name": "chart"}).encode())
        assert ev == {"kind": "ui", "name": "chart"}

    def test_role_error_event(self):
        ev = _classify_sse_payload(
            json.dumps({"role": "error", "type": "ERR_X", "status": 500}).encode()
        )
        assert ev == {"kind": "error", "type": "ERR_X", "status": 500}

    def test_action_error_event(self):
        ev = _classify_sse_payload(
            json.dumps({"action": "error", "type": "ERR_CHALLENGE", "status": 401}).encode()
        )
        assert ev == {"kind": "error", "type": "ERR_CHALLENGE", "status": 401}

    def test_legacy_action_chunk_still_parsed(self):
        """DDG has flipped event shapes before — the 2025 action
        grammar stays as a fallback."""
        ev = _classify_sse_payload(json.dumps({"action": "chunk", "message": "old"}).encode())
        assert ev == {"kind": "text", "text": "old"}

    def test_bracketed_markers(self):
        assert _classify_sse_payload(b"[DONE]") == {"kind": "done"}
        assert _classify_sse_payload(b"[PING]") == {"kind": "ping"}
        assert _classify_sse_payload(b"[LIMIT_CONVERSATION]") == {"kind": "limit"}
        ev = _classify_sse_payload(b"[CHAT_TITLE: Greetings]")
        assert ev == {"kind": "title", "text": "Greetings"}

    def test_noise_payloads_ignored(self):
        assert _classify_sse_payload(b"not json") is None
        assert _classify_sse_payload(b"[1, 2, 3]") is None  # non-dict JSON
        assert _classify_sse_payload(json.dumps({"action": "start"}).encode()) is None


# ---------------------------------------------------------------------------
# Message shaping
# ---------------------------------------------------------------------------


class TestMessageShaping:
    """_collapse_system_into_user — DDG strips the system role and
    only understands user/assistant."""

    def test_system_prepended_to_first_user(self, backend):
        convo = backend._collapse_system_into_user(
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Bye"},
            ]
        )
        assert convo[0] == {"role": "user", "content": "Be terse.\n\nHi"}
        assert convo[1] == {"role": "assistant", "content": "Hello"}
        assert convo[2] == {"role": "user", "content": "Bye"}

    def test_multiple_systems_join(self, backend):
        convo = backend._collapse_system_into_user(
            [
                {"role": "system", "content": "Rule A."},
                {"role": "system", "content": "Rule B."},
                {"role": "user", "content": "Hi"},
            ]
        )
        assert convo == [{"role": "user", "content": "Rule A.\n\nRule B.\n\nHi"}]

    def test_system_only_dropped(self, backend):
        """No user turn to host the system content → empty convo →
        generate() refuses (ValueError) rather than sending nothing."""
        assert backend._collapse_system_into_user([{"role": "system", "content": "X"}]) == []

    def test_tool_role_folded_as_user(self, backend):
        """Tool/function outputs ride as user turns — the ReAct loop
        depends on this (DDG rejects foreign roles)."""
        convo = backend._collapse_system_into_user(
            [
                {"role": "user", "content": "Search for X"},
                {"role": "assistant", "content": "Thought: I will act"},
                {"role": "tool", "content": "result payload"},
            ]
        )
        assert [m["role"] for m in convo] == ["user", "assistant", "user"]
        assert convo[2]["content"] == "result payload"

    def test_tool_role_can_host_pending_system(self, backend):
        convo = backend._collapse_system_into_user(
            [
                {"role": "system", "content": "S"},
                {"role": "tool", "content": "r"},
            ]
        )
        assert convo == [{"role": "user", "content": "S\n\nr"}]

    def test_leading_assistant_gets_user_primer(self, backend):
        convo = backend._collapse_system_into_user(
            [{"role": "assistant", "content": "Hi"}, {"role": "user", "content": "OK"}]
        )
        # a synthetic "Hello" user turn primes the conversation (DDG
        # requires a leading user message); the assistant turn survives
        assert convo == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "OK"},
        ]

    def test_content_part_list_coerced(self, backend):
        convo = backend._collapse_system_into_user(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part1"},
                        {"type": "text", "text": "part2"},
                    ],
                }
            ]
        )
        assert convo[0]["content"] == "part1\npart2"

    def test_empty_messages_refused(self, backend):
        with pytest.raises(ValueError, match="no messages after system collapse"):
            backend.generate("gpt-6-luna", [])
        with pytest.raises(ValueError, match="no messages after system collapse"):
            backend.generate("gpt-6-luna", [{"role": "system", "content": "X"}])


# ---------------------------------------------------------------------------
# generate() — buffered happy path
# ---------------------------------------------------------------------------


class TestGenerateHappyPath:
    """scrape → /status → solve → /chat → buffered content + rotation."""

    def test_bootstrap_chat_rotate_order(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hello"}])
        assert out["content"] == "Hello world"
        assert out["model"] == "gpt-6-luna"
        assert out["tool_calls"] == []
        assert out["finish_reason"] == "stop"
        assert out["usage"]["estimated"] is True
        assert out["usage"]["total_tokens"] >= 2
        # request order: fe-meta scrape, challenge bootstrap, chat
        assert [r.full_url for r in recorder.requests] == [
            f"{BASE}/",
            STATUS_URL,
            CHAT_URL,
        ]
        # the NEXT challenge is parked for the following turn
        assert backend._next_challenge_b64 == "CHAL-C1"

    def test_second_generate_reuses_rotated_challenge(self, backend, recorder, monkeypatch):
        """The /chat response header carries the next challenge — the
        second turn must NOT hit /status again."""
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "One"}])
        backend.generate("gpt-6-luna", [{"role": "user", "content": "Two"}])
        assert len(recorder.status_requests) == 1
        assert len(recorder.chat_requests) == 2

    def test_chat_request_carries_solved_proof(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        proof = recorder.chat_header(0, "X-Vqd-Hash-1")
        assert proof and proof.startswith("eyJ")
        sol = json.loads(base64.b64decode(proof))
        assert sol["server_hashes"] == _RAW_SOLUTION["server_hashes"]
        assert sol["meta"]["origin"] == "https://duck.ai"

    def test_request_body_protocol_shape(self, backend, recorder, monkeypatch):
        """Body is EXACTLY the current wire shape — sampling params
        (temperature / max_tokens / top_p) are dropped, tools off."""
        _wire(monkeypatch, recorder)
        backend.generate(
            "gpt-6-luna",
            [{"role": "user", "content": "Hello"}],
            tools=[{"name": "x"}],
            temperature=0.7,
            max_tokens=100,
            think=True,
        )
        body = recorder.chat_body(0)
        assert set(body) == {
            "model",
            "messages",
            "canUseTools",
            "canUseApproxLocation",
            "reasoningEffort",
            "durableStream",
        }
        assert body["model"] == "gpt-6-luna"
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert body["canUseTools"] is False
        assert body["canUseApproxLocation"] is None
        assert body["reasoningEffort"] == "none"
        ds = body["durableStream"]
        assert ds["conversationId"] == _DURABLE["conversation_id"]
        assert ds["publicKey"] == _DURABLE["public_key"]
        assert re.fullmatch(r"[0-9a-f]{32}", ds["messageId"])

    def test_reasoning_effort_per_model(self, backend, recorder, monkeypatch):
        """The frontend sends reasoningEffort 'low' for the Tinfoil
        open-weight pair, 'none' for everything else."""
        _wire(monkeypatch, recorder)
        backend.generate("tinfoil/gpt-oss-120b", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(0)["reasoningEffort"] == "low"
        backend.generate("tinfoil/gemma4-31b", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(1)["reasoningEffort"] == "low"
        backend.generate("claude-haiku-4-5", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(2)["reasoningEffort"] == "none"

    def test_alias_resolved_on_the_wire(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        backend.generate("claude-3-haiku", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(0)["model"] == "claude-haiku-4-5"
        backend.generate("llama", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(1)["model"] == "tinfoil/gpt-oss-120b"

    def test_system_and_tools_collapsed_into_body(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        backend.generate(
            "gpt-6-luna",
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "tool", "content": "tool output"},
            ],
        )
        body = recorder.chat_body(0)
        assert body["messages"] == [
            {"role": "user", "content": "Be terse.\n\nHi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "tool output"},
        ]

    def test_non_text_events_do_not_pollute_content(self, backend, recorder, monkeypatch):
        """sources / pings / state frames / titles are parsed but only
        assistant text reaches the buffered content."""
        recorder.chat_responses = [
            _FakeResponse(
                _sse(
                    json.dumps({"role": "source", "source": {"url": "https://x", "title": "X"}}),
                    json.dumps({"role": "assistant", "message": "hi"}),
                    "[PING]",
                    json.dumps({"role": "assistant", "status": "typing"}),
                    json.dumps({"role": "assistant", "message": " there"}),
                    "[CHAT_TITLE: Greetings]",
                    "[DONE]",
                ),
                {"x-vqd-hash-1": "CHAL-C1"},
            )
        ]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "hi there"

    def test_stream_end_without_done_marker_is_success(self, backend, recorder, monkeypatch):
        """DDG streams may simply END — an exhausted stream with
        content is a success (protocol nuance from the field)."""
        recorder.chat_responses = [
            _FakeResponse(
                _sse(
                    json.dumps({"role": "assistant", "message": "partial"}),
                ),
                {"x-vqd-hash-1": "CHAL-C1"},
            )
        ]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "partial"

    def test_legacy_action_grammar_still_streams(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _FakeResponse(
                _sse(
                    json.dumps({"action": "start", "model": "gpt-6-luna"}),
                    json.dumps({"action": "chunk", "message": "old "}),
                    json.dumps({"action": "chunk", "message": "grammar"}),
                    "[DONE]",
                ),
                {"x-vqd-hash-1": "CHAL-C1"},
            )
        ]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "old grammar"


# ---------------------------------------------------------------------------
# generate() — error taxonomy
# ---------------------------------------------------------------------------


class TestGenerateErrorTaxonomy:
    """Every failure mode surfaces a remediation message."""

    def test_401_reproof_and_retry_once(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(401)]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "Hello world"  # retry landed on the success
        assert len(recorder.chat_requests) == 2
        assert len(recorder.status_requests) == 2  # re-solve hit /status

    def test_401_twice_surfaces(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(401), _http_error(401)]
        _wire(monkeypatch, recorder)
        with pytest.raises(urllib.error.HTTPError):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert len(recorder.chat_requests) == 2

    def test_404_model_unavailable_mentions_catalog_rotation(self, backend, recorder, monkeypatch):
        detail = json.dumps({"action": "error", "status": 404, "type": "ERR_MODEL_UNAVAILABLE"})
        recorder.chat_errors = [_http_error(404, detail.encode())]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="catalog rotated"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_403_surfaces_anti_bot_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(403)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="anti-bot"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_429_surfaces_rate_limit_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(429)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="rate limit"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_503_surfaces_upstream_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(503)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="upstream"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_conversation_limit_via_sse_error(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [_chat_sse_error("ERR_CONVERSATION_LIMIT", 429)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="conversation limit"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_conversation_limit_via_marker(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _FakeResponse(_sse("[LIMIT_CONVERSATION]"), {"x-vqd-hash-1": "CHAL-C1"})
        ]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="conversation limit"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    def test_err_challenge_reproof_then_success(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [_chat_sse_error("ERR_CHALLENGE", 401)]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "fallback"
        assert len(recorder.chat_requests) == 2

    def test_err_challenge_twice_mentions_persistence(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _chat_sse_error("ERR_CHALLENGE", 401),
            _chat_sse_error("ERR_CHALLENGE", 401),
        ]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="persisted"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])

    # -- HTTP 418 teapot: proof rejected / challenge ladder escalation --

    def test_418_ladder_reproof_then_success(self, backend, recorder, monkeypatch):
        """A 418 carrying x-vqd-hash-1 hands us the NEXT challenge — the
        retry must solve THAT, not re-bootstrap from /status."""
        monkeypatch.setattr("time.sleep", lambda s: None)
        detail = json.dumps(
            {
                "action": "error",
                "status": 418,
                "type": "ERR_CHALLENGE",
                "overrideCode": "d78d",
                "cd": {"i": "3"},
            }
        ).encode()
        recorder.chat_errors = [_http_error(418, detail, headers={"x-vqd-hash-1": "CHAL-LADDER"})]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "Hello world"
        assert len(recorder.chat_requests) == 2
        # the ladder was used — exactly ONE /status (the first bootstrap)
        assert len(recorder.status_requests) == 1

    def test_418_without_ladder_rebootstraps_from_status(self, backend, recorder, monkeypatch):
        """No challenge on the 418 → fall back to a fresh /status."""
        monkeypatch.setattr("time.sleep", lambda s: None)
        recorder.chat_errors = [_http_error(418, b'{"type":"ERR_CHALLENGE"}')]
        _wire(monkeypatch, recorder)
        out = backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert out["content"] == "Hello world"
        assert len(recorder.status_requests) == 2

    def test_418_retry_applies_duration_floor(self, backend, recorder, monkeypatch):
        """Retried proofs report a plausible solve duration (>= the
        floor) — strict routes flag sub-100ms solves. The FIRST
        attempt stays honest."""
        monkeypatch.setattr("time.sleep", lambda s: None)
        recorder.chat_errors = [_http_error(418, b"teapot")]
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        first = json.loads(base64.b64decode(recorder.chat_header(0, "X-Vqd-Hash-1")))
        assert int(first["meta"]["duration"]) >= 1  # honest, unfloored
        retried = json.loads(base64.b64decode(recorder.chat_header(1, "X-Vqd-Hash-1")))
        assert int(retried["meta"]["duration"]) >= ddg._PROOF_DURATION_FLOOR_MS

    def test_418_persisted_after_budget_raises(self, backend, recorder, monkeypatch):
        """1 initial + _CHALLENGE_RETRIES re-solves, then a RuntimeError
        carrying the 418 body for diagnosis."""
        monkeypatch.setattr("time.sleep", lambda s: None)
        recorder.chat_errors = [_http_error(418, b'teapot {"type":"ERR_CHALLENGE"}')] * 4
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="418"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        assert len(recorder.chat_requests) == 4

    def test_anti_bot_cookies_and_client_hints_present(self, backend, recorder, monkeypatch):
        """The minimum cookie set (5=1; dcm=3; dcs=1) + Chromium client
        hints ride on EVERY request — the anti-bot contract the strict
        routes enforce (mirrors benoitpetit/duckduckgo-chat-cli)."""
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])
        for req in (recorder.status_requests[0], recorder.chat_requests[0]):
            assert recorder.header(req, "Cookie") == "5=1; dcm=3; dcs=1"
            assert recorder.header(req, "sec-ch-ua") == ddg._SEC_CH_UA
            assert recorder.header(req, "sec-ch-ua-mobile") == "?0"
            assert recorder.header(req, "sec-ch-ua-platform") == '"Linux"'

    def test_generic_sse_error_surfaces(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [_chat_sse_error("ERR_SOMETHING_ELSE", 500)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="SSE error"):
            backend.generate("gpt-6-luna", [{"role": "user", "content": "Hi"}])


# ---------------------------------------------------------------------------
# generate_stream() — TRUE incremental (FEAT-10)
# ---------------------------------------------------------------------------


class TestGenerateStream:
    """FEAT-10 closed: incremental deltas + the same error taxonomy."""

    def test_yields_deltas_in_order(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        chunks = list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        assert chunks == ["Hello", " world"]
        assert backend._next_challenge_b64 == "CHAL-C1"

    def test_stream_uses_parked_challenge_on_second_call(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "A"}]))
        list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "B"}]))
        assert len(recorder.status_requests) == 1

    def test_stream_system_and_tool_roles_collapsed(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        list(
            backend.generate_stream(
                "gpt-6-luna",
                [
                    {"role": "system", "content": "S"},
                    {"role": "user", "content": "Hi"},
                    {"role": "tool", "content": "t"},
                ],
            )
        )
        body = recorder.chat_body(0)
        assert [m["role"] for m in body["messages"]] == ["user", "user"]
        assert body["messages"][0]["content"] == "S\n\nHi"

    def test_stream_body_and_proof_match_buffered_path(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        body = recorder.chat_body(0)
        assert set(body) == {
            "model",
            "messages",
            "canUseTools",
            "canUseApproxLocation",
            "reasoningEffort",
            "durableStream",
        }
        assert recorder.chat_header(0, "X-Vqd-Hash-1").startswith("eyJ")

    def test_stream_legacy_action_grammar(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _FakeResponse(
                _sse(
                    json.dumps({"action": "chunk", "message": "a"}),
                    json.dumps({"action": "chunk", "message": "b"}),
                    "[DONE]",
                ),
                {"x-vqd-hash-1": "CHAL-C1"},
            )
        ]
        _wire(monkeypatch, recorder)
        assert list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}])) == [
            "a",
            "b",
        ]

    def test_stream_end_without_done_is_success(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _FakeResponse(_sse(json.dumps({"role": "assistant", "message": "x"})), {})
        ]
        _wire(monkeypatch, recorder)
        assert list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}])) == [
            "x"
        ]

    def test_stream_401_reproof_once(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(401)]
        _wire(monkeypatch, recorder)
        chunks = list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        assert chunks == ["Hello", " world"]  # retry landed on the success
        assert len(recorder.chat_requests) == 2

    def test_stream_err_challenge_reproof_once(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [_chat_sse_error("ERR_CHALLENGE", 401)]
        _wire(monkeypatch, recorder)
        chunks = list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        assert chunks == ["fallback"]

    def test_stream_err_challenge_twice_mentions_persistence(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _chat_sse_error("ERR_CHALLENGE", 401),
            _chat_sse_error("ERR_CHALLENGE", 401),
        ]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="persisted"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_418_ladder_reproof_then_success(self, backend, recorder, monkeypatch):
        """Stream path climbs the 418 ladder: the response-header
        challenge is solved for the retry (no /status re-bootstrap)."""
        monkeypatch.setattr("time.sleep", lambda s: None)
        recorder.chat_errors = [
            _http_error(
                418,
                b'{"type":"ERR_CHALLENGE"}',
                headers={"x-vqd-hash-1": "CHAL-L2"},
            )
        ]
        _wire(monkeypatch, recorder)
        chunks = list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        assert chunks == ["Hello", " world"]
        assert len(recorder.chat_requests) == 2
        assert len(recorder.status_requests) == 1

    def test_stream_418_persisted_after_budget_raises(self, backend, recorder, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda s: None)
        recorder.chat_errors = [_http_error(418, b"teapot")] * 4
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="418"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))
        assert len(recorder.chat_requests) == 4

    def test_stream_404_mentions_catalog_rotation(self, backend, recorder, monkeypatch):
        detail = json.dumps({"action": "error", "status": 404, "type": "ERR_MODEL_UNAVAILABLE"})
        recorder.chat_errors = [_http_error(404, detail.encode())]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="catalog rotated"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_403_surfaces_anti_bot_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(403)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="anti-bot"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_429_surfaces_rate_limit_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(429)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="rate limit"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_503_surfaces_upstream_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors = [_http_error(503)]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="upstream"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_conversation_limit_surfaces(self, backend, recorder, monkeypatch):
        recorder.chat_responses = [
            _FakeResponse(_sse("[LIMIT_CONVERSATION]"), {"x-vqd-hash-1": "CHAL-C1"})
        ]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="conversation limit"):
            list(backend.generate_stream("gpt-6-luna", [{"role": "user", "content": "Hi"}]))

    def test_stream_empty_messages_refused(self, backend):
        with pytest.raises(ValueError, match="no messages after system collapse"):
            list(backend.generate_stream("gpt-6-luna", []))


# ---------------------------------------------------------------------------
# Chat pacing — DUCKDUCKGO_MIN_INTERVAL wiring (anti-429 courtesy)
# ---------------------------------------------------------------------------


class TestChatPacing:
    """The minimum /chat POST gap — declared in v0.2.0 but only wired
    after the 2026-10-09 429 storm + 418 escalation session."""

    def test_second_post_waits_out_the_interval(self, backend, recorder, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        backend._MIN_CHAT_INTERVAL_S = 5.0
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "A"}])
        assert sleeps == []  # no prior chat — the first POST is immediate
        backend.generate("gpt-6-luna", [{"role": "user", "content": "B"}])
        assert sleeps and 0 < sleeps[0] <= 5.0

    def test_interval_zero_disables_pacing(self, backend, recorder, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
        backend._MIN_CHAT_INTERVAL_S = 0
        _wire(monkeypatch, recorder)
        backend.generate("gpt-6-luna", [{"role": "user", "content": "A"}])
        backend.generate("gpt-6-luna", [{"role": "user", "content": "B"}])
        assert sleeps == []

    def test_env_override_sets_interval(self, monkeypatch):
        monkeypatch.setenv("DUCKDUCKGO_MIN_INTERVAL", "0")
        assert DuckDuckGoBackend()._MIN_CHAT_INTERVAL_S == 0
        monkeypatch.setenv("DUCKDUCKGO_MIN_INTERVAL", "7.5")
        assert DuckDuckGoBackend()._MIN_CHAT_INTERVAL_S == 7.5


# ---------------------------------------------------------------------------
# fetch_capabilities — live catalog discovery
# ---------------------------------------------------------------------------


class TestFetchCapabilities:
    def test_happy_path_parses_json(self, backend, recorder, monkeypatch):
        payload = {"models": {"gpt-6-luna": {"name": "GPT-6 Luna"}}}
        recorder.capabilities_responses = [_FakeResponse(json.dumps(payload).encode(), {})]
        _wire(monkeypatch, recorder)
        assert backend.fetch_capabilities() == payload

    def test_401_gated_retry_carries_solved_proof(self, backend, recorder, monkeypatch):
        """When the endpoint is proof-gated, retry once with a solved
        X-Vqd-Hash-1 (bootstrap /status → solve → resend)."""
        payload = {"models": {}}
        recorder.capabilities_errors = [_http_error(401, url=CAPABILITIES_URL)]
        recorder.capabilities_responses = [_FakeResponse(json.dumps(payload).encode(), {})]
        _wire(monkeypatch, recorder)
        assert backend.fetch_capabilities() == payload
        assert len(recorder.capabilities_requests) == 2
        assert len(recorder.status_requests) == 1
        second = recorder.capabilities_requests[1]
        proof = _Recorder.header(second, "X-Vqd-Hash-1")
        assert proof and proof.startswith("eyJ")

    def test_network_failure_returns_none(self, backend, recorder, monkeypatch):
        recorder.capabilities_errors = [urllib.error.URLError("no route")]
        _wire(monkeypatch, recorder)
        assert backend.fetch_capabilities() is None

    def test_non_json_body_returns_none(self, backend, recorder, monkeypatch):
        recorder.capabilities_responses = [_FakeResponse(b"<html>nope</html>", {})]
        _wire(monkeypatch, recorder)
        assert backend.fetch_capabilities() is None


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class TestDuckDuckGoCatalog:
    """Seed-backed catalog serving — no network, cache-routed."""

    def test_list_models_shape(self, backend):
        models = backend.list_models()
        names = {m["name"] for m in models}
        assert names == CANONICAL_IDS
        for m in models:
            assert m["size"] == 0
            d = m["details"]
            assert d["backend"] == "duckduckgo"
            assert d["context_length"] > 0
            assert d["free_tier"] is True
            assert d["is_chat_model"] is True
            assert d["pricing"] == {"input": 0.0, "output": 0.0}

    def test_list_models_no_network(self, backend, monkeypatch):
        """DDG serves the catalog from the seed — listing must never
        touch the wire (the house contract for `agentkthx models`)."""

        def boom(req, timeout=None):
            raise AssertionError("list_models() must not open a socket")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert len(backend.list_models()) == 8

    def test_list_models_l1_cached(self, backend, monkeypatch):
        first = backend.list_models()

        def boom(req, timeout=None):
            raise AssertionError("L1 cache should prevent re-serving")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert backend.list_models() == first

    def test_seed_json_carries_catalog(self):
        seed = json.loads(
            (Path(__file__).resolve().parents[1] / "agentkthx/data/model_seed.json").read_text()
        )
        assert set(seed["duckduckgo"]) == CANONICAL_IDS
        for meta in seed["duckduckgo"].values():
            assert meta["context_length"] > 0

    def test_get_model_max_context_alias_aware(self, backend):
        direct = backend.get_model_max_context("gpt-6-luna")
        aliased = backend.get_model_max_context("gpt")  # short alias
        assert direct == aliased == 128000
        assert backend.get_model_max_context("never-heard-of-it") == 128000

    def test_get_model_runtime_context_matches_max(self, backend):
        """cmd_models calls this unconditionally — must exist and
        equal the catalog context."""
        assert backend.get_model_runtime_context("gpt-6-luna") == backend.get_model_max_context(
            "gpt-6-luna"
        )

    def test_get_model_info_resolves_alias(self, backend):
        info = backend.get_model_info("claude-3-haiku")
        assert info and info["name"] == "claude-haiku-4-5"
        assert backend.get_model_info("no-such-model-anywhere") is None

    def test_is_running_never_touches_network(self, backend, monkeypatch):
        def boom(req, timeout=None):
            raise AssertionError("is_running() must not open a socket")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert backend.is_running() is True


# ---------------------------------------------------------------------------
# Capability verdicts — protocol facts, not probes
# ---------------------------------------------------------------------------


class TestDuckDuckGoCapabilities:
    def test_tool_support_always_react(self, backend):
        for name in CANONICAL_IDS:
            assert backend.test_tool_support(name) is ToolSupportLevel.REACT

    def test_thinking_support_always_no(self, backend):
        for name in CANONICAL_IDS:
            assert backend.test_thinking_support(name) is ThinkingSupport.NO


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    def test_alias_resolution(self):
        # 2025-era wire IDs → the current catalog
        assert _resolve_model("gpt-4o-mini") == "gpt-5.6-luna"
        assert _resolve_model("o3-mini") == "gpt-5.4-mini"
        assert _resolve_model("o4mini") == "gpt-5.4-mini"
        assert _resolve_model("claude-3-haiku") == "claude-haiku-4-5"
        assert _resolve_model("claude-3-haiku-20240307") == "claude-haiku-4-5"
        assert _resolve_model("mistralai/Mistral-Small-24B-Instruct-2501") == "mistral-small-2603"
        assert _resolve_model("meta-llama/Llama-3.3-70B-Instruct-Turbo") == "tinfoil/gpt-oss-120b"
        # short aliases
        assert _resolve_model("gpt") == "gpt-6-luna"
        assert _resolve_model("claude") == "claude-haiku-4-5"
        assert _resolve_model("mistral") == "mistral-small-2603"
        assert _resolve_model("mixtral") == "mistral-small-2603"
        assert _resolve_model("llama") == "tinfoil/gpt-oss-120b"
        assert _resolve_model("oss") == "tinfoil/gpt-oss-120b"
        assert _resolve_model("gemma") == "tinfoil/gemma4-31b"
        assert _resolve_model("nano") == "gpt-5.4-nano"
        assert _resolve_model("mini") == "gpt-5.4-mini"

    def test_alias_resolution_case_insensitive(self):
        assert _resolve_model("GPT-6-LUNA") == "gpt-6-luna"
        assert _resolve_model("Claude-3-Haiku") == "claude-haiku-4-5"

    def test_canonical_ids_pass_through(self):
        for wire_id in CANONICAL_IDS:
            assert _resolve_model(wire_id) == wire_id

    def test_unknown_ids_pass_through_for_upstream_error(self):
        """An unknown ID is sent as-is so the server's
        ERR_MODEL_UNAVAILABLE (with its catalog hint) surfaces."""
        assert _resolve_model("totally-unknown") == "totally-unknown"

    def test_alias_table_matches_canonical_catalog(self):
        canonical = {m["id"] for m in _WIRE_MODELS}
        assert canonical == CANONICAL_IDS
        for target in _MODEL_ALIASES.values():
            assert target in canonical

    def test_wire_model_effort_map(self):
        by_id = {m["id"]: m["effort"] for m in _WIRE_MODELS}
        assert by_id["tinfoil/gpt-oss-120b"] == "low"
        assert by_id["tinfoil/gemma4-31b"] == "low"
        assert by_id["gpt-6-luna"] == "none"


# ---------------------------------------------------------------------------
# Plugin manifest + registration
# ---------------------------------------------------------------------------


class TestPluginContract:
    def test_manifest_shape(self):
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[1] / "agentkthx/plugins/duckduckgo/plugin.json"
            ).read_text()
        )
        assert manifest["name"] == "duckduckgo"
        assert manifest["version"] == "0.2.0"
        # the description must reflect the CURRENT protocol
        assert "x-vqd-hash-1" in manifest["description"]
        assert "challenge" in manifest["description"].lower()
        ext = manifest["extensions"]["org.vts-tech.agentkthx"]
        assert ext["type"] == "backend"
        provides = ext["provides"]
        assert provides["backends"]["duckduckgo"] == "duckduckgo.DuckDuckGoBackend"
        assert provides["backends"]["ddg"] == "duckduckgo.DuckDuckGoBackend"
        assert set(provides["cli_flags"]["--backend"]) == {"duckduckgo", "ddg"}
        defaults = ext["config"]["defaults"]
        assert defaults["DUCKDUCKGO_BASE_URL"] == "https://duck.ai"
        assert defaults["DUCKDUCKGO_DEFAULT_MODEL"] == "gpt-6-luna"
        # NO API key default — the backend is keyless
        assert "DUCKDUCKGO_API_KEY" not in defaults

    def test_register_and_unregister(self):
        class _FakeManager:
            def __init__(self):
                self.backends = {}

            def register_backend(self, name, cls):
                self.backends[name] = cls

            def unregister_backend(self, name):
                self.backends.pop(name, None)

        from agentkthx.plugins.duckduckgo import register, unregister

        m = _FakeManager()
        register(m)
        assert set(m.backends) == {"duckduckgo", "ddg"}
        assert m.backends["duckduckgo"] is DuckDuckGoBackend
        unregister(m)
        assert m.backends == {}

    def test_get_backend_resolves_via_plugin_system(self):
        """End-to-end: the lazy plugin path resolves both names."""
        from agentkthx.backends import get_backend

        assert isinstance(get_backend("duckduckgo"), DuckDuckGoBackend)
        assert isinstance(get_backend("ddg"), DuckDuckGoBackend)


# ---------------------------------------------------------------------------
# Config + CLI help surfaces
# ---------------------------------------------------------------------------


class TestConfigSurfaces:
    def test_config_module_exports_env_vars(self):
        import agentkthx.config as _config

        assert _config.DUCKDUCKGO_BASE_URL == "https://duck.ai"
        assert _config.DUCKDUCKGO_DEFAULT_MODEL == "gpt-6-luna"
        assert isinstance(_config.DUCKDUCKGO_USER_AGENT, str)

    def test_cli_env_reference_documents_duckduckgo(self):
        from agentkthx.cli.commands.config import _env_reference_entries

        names = {name for name, _ in _env_reference_entries()}
        assert "DUCKDUCKGO_BASE_URL" in names
        assert "DUCKDUCKGO_USER_AGENT" in names
        assert "DUCKDUCKGO_DEFAULT_MODEL" in names

    def test_backend_slug_label_mapping(self):
        from agentkthx.cli.commands.config import _BACKEND_SLUG_TO_LABEL

        assert _BACKEND_SLUG_TO_LABEL["duckduckgo"] == "DuckDuckGo"
        assert _BACKEND_SLUG_TO_LABEL["ddg"] == "DuckDuckGo"
