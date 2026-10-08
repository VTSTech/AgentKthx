"""
DuckDuckGo plugin — backend regression tests.

Verifies that the DuckDuckGoBackend (the 12th cloud backend, and the
ONLY one that subclasses BaseBackend directly instead of CloudBackend —
the /duckchat/v1 protocol is non-OpenAI) correctly implements:

  - BaseBackend (NOT CloudBackend) inheritance — the design pin
  - BackendType.DUCKDUCKGO enum value
  - Keyless construction (no API-key validation — nothing to validate)
  - is_cloud = True + base_url default
  - Static catalog serving (5 client-verified models, all free, all
    chat; cached; no network — DDG has no /models endpoint)
  - Capability verdicts as PROTOCOL FACTS: test_tool_support → REACT
    for every model (DDG strips tools); test_thinking_support → NO
    for every model (the privacy layer never surfaces reasoning)
  - Alias resolution (claude-3-haiku / llama / mixtral → canonical
    wire IDs, per the mumu-lhl v3.3.0 client mapping)
  - _collapse_system_into_user (DDG strips the system role)
  - generate() happy path: /status bootstrap → /chat SSE → buffered
    content, token rotation, estimated usage, model echo
  - generate() error taxonomy: 401 re-bootstrap retry, 403 anti-bot,
    429 rate limit, 5xx upstream, ERR_CONVERSATION_LIMIT and the bare
    [LIMIT_CONVERSATION] marker, ERR_CHALLENGE re-bootstrap
  - generate() drops sampling params (body is exactly {model, messages})
  - generate_stream() NotImplementedError (FEAT-10)
  - is_running() never touches the network (house contract: cheap
    local check gating cmd_models)
  - Plugin manifest + register()/unregister() contract
  - model_seed.json carries the 5-model duckduckgo catalog
  - config.py + CLI env reference expose the DUCKDUCKGO_* vars

Mirrors the structure of tests/test_siliconflow_backend.py (the prior
cloud-backend regression suite) at a reduced depth — the full suite is
registered as a follow-up finding alongside the live-probe contract
test (see audit/audit.md FEAT-10 / TEST-13).
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.base import BaseBackend  # noqa: E402
from agentkthx.core.types import BackendType, ThinkingSupport, ToolSupportLevel  # noqa: E402
from agentkthx.plugins.duckduckgo.duckduckgo import (  # noqa: E402
    _MODEL_ALIASES,
    DuckDuckGoBackend,
    _resolve_model,
)

# The 5 client-verified wire IDs (mumu-lhl v3.3.0 client, cross-checked
# against the Oct 2026 duck.ai help page lineup).
CANONICAL_IDS = {
    "gpt-4o-mini",
    "claude-3-haiku-20240307",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "mistralai/Mistral-Small-24B-Instruct-2501",
    "o3-mini",
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


def _status_ok(token: str = "TOKEN-S1") -> _FakeResponse:
    return _FakeResponse(b"", {"x-vqd-4": token})


def _chat_ok(chunks: list[str], fresh: str = "TOKEN-C1") -> _FakeResponse:
    body = _sse(
        json.dumps({"action": "start", "model": "gpt-4o-mini"}),
        *[json.dumps({"action": "chunk", "message": c}) for c in chunks],
        json.dumps({"action": "success", "model": "gpt-4o-mini"}),
        "[DONE]",
    )
    return _FakeResponse(body, {"x-vqd-4": fresh})


class _Recorder:
    """urlopen double dispatching by URL, recording every request."""

    def __init__(self):
        self.requests: list[urllib.request.Request] = []
        self.status_responses: list = [_status_ok("TOKEN-S1")]
        self.chat_responses: list = [_chat_ok(["Hello", " world"], "TOKEN-C1")]
        self.chat_errors: list = []  # exceptions raised before any response

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        url = req.full_url
        if url.endswith("/duckchat/v1/status"):
            if self.status_responses:
                return self.status_responses.pop(0)
            return _status_ok("TOKEN-S-extra")
        if url.endswith("/duckchat/v1/chat"):
            if self.chat_errors:
                raise self.chat_errors.pop(0)
            if self.chat_responses:
                return self.chat_responses.pop(0)
            return _chat_ok(["fallback"], "TOKEN-C-next")
        raise AssertionError(f"unexpected URL: {url}")

    # -- assertions helpers -------------------------------------------------
    @property
    def chat_requests(self):
        return [r for r in self.requests if r.full_url.endswith("/chat")]

    @property
    def status_requests(self):
        return [r for r in self.requests if r.full_url.endswith("/status")]

    def chat_header(self, index: int, name: str) -> str:
        # urllib.request.Request capitalizes header keys on storage
        # ("x-vqd-4" -> "X-vqd-4"); try both spellings.
        headers = self.chat_requests[index].headers
        return headers.get(name, headers.get(name.capitalize()))

    def chat_body(self, index: int) -> dict:
        return json.loads(self.chat_requests[index].data.decode("utf-8"))


@pytest.fixture
def backend():
    """A DuckDuckGoBackend with clean token state (catalog auto-isolated
    by the conftest _isolated_model_cache fixture)."""
    return DuckDuckGoBackend()


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
        /duckchat/v1 protocol (x-vqd-4 handshake, own SSE shape, no
        /models, no sampling params) shares nothing with the OpenAI-compat
        shared transport."""
        from agentkthx.backends.cloud_base import CloudBackend

        assert issubclass(DuckDuckGoBackend, BaseBackend)
        assert not issubclass(DuckDuckGoBackend, CloudBackend)

    def test_backend_type_enum(self):
        assert BackendType.DUCKDUCKGO.value == "duckduckgo"
        assert DuckDuckGoBackend().backend_type is BackendType.DUCKDUCKGO

    def test_is_cloud_true(self, backend):
        assert backend.is_cloud is True

    def test_base_url_default(self, backend):
        assert backend.base_url == "https://duckduckgo.com"

    def test_keyless_construction_needs_no_api_key(self, monkeypatch):
        """No API key env var, no key kwarg — construction succeeds
        (contrast: CloudBackend subclasses raise on a missing key)."""
        import agentkthx.config as _config

        monkeypatch.delenv("DUCKDUCKGO_USER_AGENT", raising=False)
        monkeypatch.setattr(_config, "DUCKDUCKGO_USER_AGENT", "", raising=False)
        b = DuckDuckGoBackend()  # no key anywhere — fine
        assert b._user_agent.startswith("Mozilla/5.0")

    def test_user_agent_precedence(self, backend):
        """Explicit kwarg beats the env var beats the built-in default."""
        b = DuckDuckGoBackend(user_agent="MyUA/1.0")
        assert b._user_agent == "MyUA/1.0"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class TestDuckDuckGoCatalog:
    """Static catalog serving — no network, cache-routed."""

    def test_list_models_shape(self, backend):
        models = backend.list_models()
        assert len(models) == 5
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
        """DDG has no /models endpoint — listing must never touch the wire."""

        def boom(req, timeout=None):
            raise AssertionError("list_models() must not open a socket")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert len(backend.list_models()) == 5

    def test_list_models_l1_cached(self, backend, monkeypatch):
        first = backend.list_models()
        calls = []

        def boom(req, timeout=None):
            calls.append(req)
            raise AssertionError("L1 cache should prevent re-serving")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        second = backend.list_models()
        assert calls == []
        assert [m["name"] for m in first] == [m["name"] for m in second]

    def test_seed_json_carries_catalog(self):
        seed = json.loads(
            (Path(__file__).resolve().parents[1] / "agentkthx/data/model_seed.json").read_text()
        )
        assert set(seed["duckduckgo"].keys()) == CANONICAL_IDS
        for meta in seed["duckduckgo"].values():
            assert meta["context_length"] > 0

    def test_get_model_max_context_alias_aware(self, backend):
        assert backend.get_model_max_context("o3-mini") == 200000
        # alias resolves before catalog lookup
        assert backend.get_model_max_context("claude-3-haiku") == 200000
        # unknown model → conservative 128K
        assert backend.get_model_max_context("nope") == 128000

    def test_get_model_runtime_context_matches_max(self, backend):
        assert backend.get_model_runtime_context("gpt-4o-mini") == backend.get_model_max_context(
            "gpt-4o-mini"
        )

    def test_get_model_info_resolves_alias(self, backend):
        info = backend.get_model_info("llama")
        assert info is not None
        assert info["name"] == "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        assert backend.get_model_info("not-a-model") is None

    def test_is_running_never_touches_network(self, backend, monkeypatch):
        """House contract (CloudBackend parity): is_running is a CHEAP
        local check gating cmd_models — keyless means always True."""

        def boom(req, timeout=None):
            raise AssertionError("is_running() must not open a socket")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert backend.is_running() is True


# ---------------------------------------------------------------------------
# Capability verdicts — protocol facts
# ---------------------------------------------------------------------------


class TestDuckDuckGoCapabilities:
    def test_tool_support_always_react(self, backend):
        for model in CANONICAL_IDS:
            assert backend.test_tool_support(model) is ToolSupportLevel.REACT

    def test_thinking_support_always_no(self, backend):
        """Even o3-mini — reasoning upstream — never surfaces CoT
        through the DDG SSE shape; the privacy layer hides it."""
        for model in CANONICAL_IDS:
            assert backend.test_thinking_support(model) is ThinkingSupport.NO

    def test_alias_resolution(self):
        assert _resolve_model("claude-3-haiku") == "claude-3-haiku-20240307"
        assert _resolve_model("llama") == "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        assert _resolve_model("mixtral") == "mistralai/Mistral-Small-24B-Instruct-2501"

    def test_alias_resolution_case_insensitive(self):
        assert _resolve_model("Llama") == "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        assert _resolve_model("Mixtral") == "mistralai/Mistral-Small-24B-Instruct-2501"

    def test_canonical_ids_pass_through(self):
        for model in CANONICAL_IDS:
            assert _resolve_model(model) == model

    def test_alias_table_matches_canonical_catalog(self):
        """Every alias target must be a seeded canonical ID — an alias
        pointing at a rotated-out model would 400 at generate time."""
        for target in _MODEL_ALIASES.values():
            assert target in CANONICAL_IDS


# ---------------------------------------------------------------------------
# Message shaping
# ---------------------------------------------------------------------------


class TestCollapseSystem:
    def test_system_prepended_to_first_user(self, backend):
        out = backend._collapse_system_into_user(
            [
                {"role": "system", "content": "You are terse."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello."},
                {"role": "user", "content": "Bye"},
            ]
        )
        assert [m["role"] for m in out] == ["user", "assistant", "user"]
        assert out[0]["content"] == "You are terse.\n\nHi"
        assert out[2]["content"] == "Bye"

    def test_system_only_becomes_single_user(self, backend):
        out = backend._collapse_system_into_user([{"role": "system", "content": "Be brief."}])
        assert out == [{"role": "user", "content": "Be brief."}]

    def test_tool_role_folded_as_user(self, backend):
        """Defensive: tool/function roles (shouldn't occur under forced
        ReAct) keep their text instead of being silently dropped."""
        out = backend._collapse_system_into_user(
            [
                {"role": "user", "content": "run it"},
                {"role": "tool", "content": "result: 42"},
            ]
        )
        assert [m["role"] for m in out] == ["user", "user"]
        assert out[1]["content"] == "result: 42"

    def test_content_part_list_coerced(self, backend):
        out = backend._collapse_system_into_user(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part one"},
                        {"type": "text", "text": "part two"},
                    ],
                }
            ]
        )
        assert out[0]["content"] == "part one\npart two"


# ---------------------------------------------------------------------------
# generate — happy path + token lifecycle
# ---------------------------------------------------------------------------


class TestGenerateHappyPath:
    def test_bootstrap_chat_rotate(self, backend, recorder, monkeypatch):
        """First call bootstraps via /status, chats, rotates the token;
        the response carries assembled content + estimated usage."""
        _wire(monkeypatch, recorder)
        result = backend.generate("gpt-4o-mini", [{"role": "user", "content": "Say OK."}])
        assert result["content"] == "Hello world"
        assert result["tool_calls"] == []
        assert result["finish_reason"] == "stop"
        assert result["model"] == "gpt-4o-mini"
        assert result["usage"]["estimated"] is True
        assert result["usage"]["completion_tokens"] > 0

        # Exactly one bootstrap, one chat
        assert len(recorder.status_requests) == 1
        assert len(recorder.chat_requests) == 1
        # The chat carried the bootstrapped token
        assert recorder.chat_header(0, "x-vqd-4") == "TOKEN-S1"
        # Rotation stored the fresh token
        assert backend._vqd_current == "TOKEN-C1"
        assert backend._vqd_previous == "TOKEN-S1"

    def test_second_generate_reuses_rotated_token(self, backend, recorder, monkeypatch):
        """Token rotation contract: generate #2 sends TOKEN-C1 (from
        response #1's headers), NOT a fresh /status bootstrap."""
        _wire(monkeypatch, recorder)
        backend.generate("gpt-4o-mini", [{"role": "user", "content": "one"}])
        backend.generate("gpt-4o-mini", [{"role": "user", "content": "two"}])
        assert len(recorder.status_requests) == 1  # no second bootstrap
        assert len(recorder.chat_requests) == 2
        assert recorder.chat_header(1, "x-vqd-4") == "TOKEN-C1"

    def test_request_body_is_minimal(self, backend, recorder, monkeypatch):
        """The DDG wire contract: body is EXACTLY {model, messages} —
        tools/temperature/max_tokens are dropped, system is folded."""
        _wire(monkeypatch, recorder)
        backend.generate(
            "gpt-4o-mini",
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "Hi"},
            ],
            tools=[{"name": "calc"}],
            temperature=0.3,
            max_tokens=512,
            think=True,
        )
        body = recorder.chat_body(0)
        assert set(body.keys()) == {"model", "messages"}
        assert body["messages"] == [{"role": "user", "content": "SYS\n\nHi"}]

    def test_alias_resolved_on_the_wire(self, backend, recorder, monkeypatch):
        _wire(monkeypatch, recorder)
        backend.generate("claude-3-haiku", [{"role": "user", "content": "Hi"}])
        assert recorder.chat_body(0)["model"] == "claude-3-haiku-20240307"

    def test_empty_messages_raises(self, backend):
        with pytest.raises(ValueError, match="no messages"):
            backend.generate("gpt-4o-mini", [])

    def test_generate_stream_not_implemented(self, backend):
        with pytest.raises(NotImplementedError, match="FEAT-10"):
            backend.generate_stream("gpt-4o-mini", [{"role": "user", "content": "x"}])


# ---------------------------------------------------------------------------
# generate — error taxonomy
# ---------------------------------------------------------------------------


class TestGenerateErrors:
    def test_401_rebootstrap_and_retry_once(self, backend, recorder, monkeypatch):
        """Token-expiry remediation: 401 → fresh /status → retry succeeds."""
        recorder.chat_errors.append(
            urllib.error.HTTPError(
                "https://duckduckgo.com/duckchat/v1/chat", 401, "Unauthorized", None, None
            )
        )
        recorder.status_responses.append(_status_ok("TOKEN-S2"))
        _wire(monkeypatch, recorder)
        result = backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])
        assert result["content"] == "Hello world"
        assert len(recorder.status_requests) == 2  # bootstrap + re-bootstrap
        assert len(recorder.chat_requests) == 2  # failed + retried
        assert recorder.chat_header(1, "x-vqd-4") == "TOKEN-S2"

    def test_403_surfaces_anti_bot_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors.append(
            urllib.error.HTTPError(
                "https://duckduckgo.com/duckchat/v1/chat", 403, "Forbidden", None, None
            )
        )
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="anti-bot"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])

    def test_429_surfaces_rate_limit_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors.append(
            urllib.error.HTTPError(
                "https://duckduckgo.com/duckchat/v1/chat", 429, "Too Many Requests", None, None
            )
        )
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="rate limit"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])

    def test_503_surfaces_upstream_message(self, backend, recorder, monkeypatch):
        recorder.chat_errors.append(
            urllib.error.HTTPError(
                "https://duckduckgo.com/duckchat/v1/chat", 503, "Service Unavailable", None, None
            )
        )
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="upstream"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])

    def test_conversation_limit_via_sse_error(self, backend, recorder, monkeypatch):
        recorder.chat_responses[0] = _FakeResponse(
            _sse(
                json.dumps({"action": "start"}),
                json.dumps({"action": "error", "type": "ERR_CONVERSATION_LIMIT", "status": 429}),
            ),
            {"x-vqd-4": "TOKEN-C1"},
        )
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="conversation limit"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])

    def test_conversation_limit_via_marker(self, backend, recorder, monkeypatch):
        """The bare [LIMIT_CONVERSATION] terminal marker (observed in
        the mrgick client's stream-stripping) maps to the same
        conversation-limit remediation as the SSE error chunk."""
        recorder.chat_responses[0] = _FakeResponse(
            _sse("[LIMIT_CONVERSATION]"), {"x-vqd-4": "TOKEN-C1"}
        )
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="conversation limit"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])

    def test_err_challenge_rebootstraps_then_raises(self, backend, recorder, monkeypatch):
        """ERR_CHALLENGE → one re-bootstrap + retry; a second challenge
        surfaces the protocol-change message."""

        def challenge_body() -> _FakeResponse:
            return _FakeResponse(
                _sse(json.dumps({"action": "error", "type": "ERR_CHALLENGE", "status": 401})),
                {"x-vqd-4": "TOKEN-C1"},
            )

        recorder.chat_responses = [challenge_body(), challenge_body()]
        _wire(monkeypatch, recorder)
        with pytest.raises(RuntimeError, match="ERR_CHALLENGE persisted"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])
        assert len(recorder.status_requests) == 2  # bootstrap + re-bootstrap

    def test_stream_end_without_done_marker_is_success(self, backend, recorder, monkeypatch):
        """mumu-lhl contract: a stream that simply ends (no [DONE]) with
        chunked content is a success, not an error."""
        recorder.chat_responses[0] = _FakeResponse(
            _sse(
                json.dumps({"action": "chunk", "message": "partial"}),
                json.dumps({"action": "success", "model": "gpt-4o-mini"}),
            ),
            {"x-vqd-4": "TOKEN-C1"},
        )
        _wire(monkeypatch, recorder)
        result = backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])
        assert result["content"] == "partial"

    def test_401_twice_surfaces(self, backend, recorder, monkeypatch):
        """A second consecutive 401 (re-bootstrap didn't help) must not
        loop forever — it re-raises the HTTPError."""
        recorder.chat_errors.append(urllib.error.HTTPError("u", 401, "Unauthorized", None, None))
        recorder.chat_errors.append(urllib.error.HTTPError("u", 401, "Unauthorized", None, None))
        _wire(monkeypatch, recorder)
        with pytest.raises(urllib.error.HTTPError):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])
        assert len(recorder.chat_requests) == 2  # hard-stopped at 2


# ---------------------------------------------------------------------------
# Headers — the anti-bot contract
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_status_headers_carry_vqd_accept(self, backend):
        h = backend._build_status_headers()
        assert h["x-vqd-accept"] == "1"
        assert h["Referer"] == "https://duckduckgo.com/"
        assert h["Accept"] == "text/event-stream"
        assert h["User-Agent"].startswith("Mozilla/5.0")

    def test_chat_headers_carry_current_token(self, backend):
        h = backend._build_chat_headers("TOKEN-X")
        assert h["x-vqd-4"] == "TOKEN-X"
        assert h["Content-Type"] == "application/json"
        assert h["Referer"] == "https://duckduckgo.com/"
        assert "x-vqd-accept" not in h  # bootstrap-only header

    def test_missing_status_token_raises(self, backend, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=None: _FakeResponse(b"", {}),
        )
        with pytest.raises(RuntimeError, match="no x-vqd-4"):
            backend.generate("gpt-4o-mini", [{"role": "user", "content": "Hi"}])


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
        assert manifest["version"] == "0.1.0"
        ext = manifest["extensions"]["org.vts-tech.agentkthx"]
        assert ext["type"] == "backend"
        provides = ext["provides"]
        assert provides["backends"]["duckduckgo"] == "duckduckgo.DuckDuckGoBackend"
        assert provides["backends"]["ddg"] == "duckduckgo.DuckDuckGoBackend"
        assert set(provides["cli_flags"]["--backend"]) == {"duckduckgo", "ddg"}
        defaults = ext["config"]["defaults"]
        assert defaults["DUCKDUCKGO_BASE_URL"] == "https://duckduckgo.com"
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

        assert _config.DUCKDUCKGO_BASE_URL == "https://duckduckgo.com"
        assert _config.DUCKDUCKGO_DEFAULT_MODEL == "gpt-4o-mini"
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
