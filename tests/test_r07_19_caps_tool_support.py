"""R07.19 follow-up #9 — capabilities-first test_tool_support for OllamaBackend.

`agentkthx models --tool-support` (and chat-time auto-detection) now derives
the verdict from GET /api/tags' per-model `capabilities` field — the server's
own declaration, derived from the model's template/GGUF — instead of the old
sampled probe (one 100-token "what's the weather" request per model, no
system prompt), which misclassified capable-but-chatty models as REACT.

Only servers that predate the capabilities field (or models absent from the
listing) fall through to the sampled probe / cache path, unchanged.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from agentkthx.backends.ollama import OllamaBackend
from agentkthx.core.types import ToolSupportLevel

# ─────────────────────────────────────────────────────────────────────────────
# urlopen mocking helpers
# ─────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal context-manager response for urllib.request.urlopen."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UrlopenRecorder:
    """Stands in for urllib.request.urlopen inside agentkthx.backends.ollama.

    Serves `tags_payload` for GET /api/tags (or raises `tags_error` when set)
    and records every request URL + method so tests can assert on the traffic
    shape (e.g. "no POST /api/chat was ever attempted").
    """

    def __init__(self, tags_payload: dict | None, tags_error: Exception | None = None):
        self.tags_payload = tags_payload
        self.tags_error = tags_error
        self.requests: list[tuple[str, str]] = []

    def __call__(self, req, timeout=None):
        url = getattr(req, "full_url", str(req))
        method = getattr(req, "method", None) or "GET"
        self.requests.append((method, url))
        if self.tags_error is not None:
            raise self.tags_error
        if "/api/tags" in url and method == "GET" and self.tags_payload is not None:
            return _FakeResponse(self.tags_payload)
        raise AssertionError(f"unexpected request in test: {method} {url}")

    @property
    def chat_post_count(self) -> int:
        return sum(1 for m, u in self.requests if "/api/chat" in u and m == "POST")

    @property
    def tags_get_count(self) -> int:
        return sum(1 for m, u in self.requests if "/api/tags" in u and m == "GET")


TAGS = {
    "models": [
        {"name": "toolmodel:1b", "capabilities": ["completion", "tools"]},
        {"name": "insertmodel:1b", "capabilities": ["completion", "insert"]},
        {"name": "plainmodel:1b", "capabilities": ["completion"]},
        {"name": "nocapsfield:1b"},  # capable server, one entry missing the field
    ]
}


@pytest.fixture()
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent tool-support cache at a temp dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


def _make_backend(monkeypatch, recorder: _UrlopenRecorder, api_mode="openre") -> OllamaBackend:
    backend = OllamaBackend(base_url="http://mock:11434", api_mode=api_mode)
    # ollama.py imports urllib.request INSIDE its methods (stdlib-only lazy
    # imports), so patch the global urllib.request.urlopen — every local
    # `import urllib.request` resolves to this same module object.
    monkeypatch.setattr("urllib.request.urlopen", recorder)
    return backend


# ─────────────────────────────────────────────────────────────────────────────
# Verdicts from the declared capabilities
# ─────────────────────────────────────────────────────────────────────────────


class TestCapsVerdicts:
    def test_tools_capability_is_native(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("toolmodel:1b", force_test=True) is (
            ToolSupportLevel.NATIVE
        )

    def test_no_tools_capability_is_none(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("plainmodel:1b", force_test=True) is (
            ToolSupportLevel.NONE
        )

    def test_insert_alone_is_not_tools(self, monkeypatch, isolated_cache):
        # "insert" (fill-in-middle) must not be confused with tool calling.
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("insertmodel:1b", force_test=True) is (
            ToolSupportLevel.NONE
        )

    def test_no_model_load_no_inference(self, monkeypatch, isolated_cache):
        # The whole point: the verdict costs ONE GET /api/tags and zero
        # sampled chat requests.
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        backend.test_tool_support("toolmodel:1b", force_test=True)
        backend.test_tool_support("plainmodel:1b", force_test=True)
        assert rec.chat_post_count == 0
        assert rec.tags_get_count == 1  # memoized across models

    def test_verdict_is_api_mode_independent(self, monkeypatch, isolated_cache):
        # The runner template governs openre and openai alike — both cache
        # namespaces get the same declared verdict.
        rec_openre = _UrlopenRecorder(TAGS)
        rec_openai = _UrlopenRecorder(TAGS)
        b_re = _make_backend(monkeypatch, rec_openre, api_mode="openre")
        b_ai = _make_backend(monkeypatch, rec_openai, api_mode="openai")
        assert b_re.test_tool_support("toolmodel:1b") is ToolSupportLevel.NATIVE
        assert b_ai.test_tool_support("toolmodel:1b") is ToolSupportLevel.NATIVE

    def test_cache_written_from_capabilities(self, monkeypatch, isolated_cache):
        from agentkthx.core.tool_cache import get_cached_tool_support

        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        backend.test_tool_support("toolmodel:1b", family="qwen2")
        cached = get_cached_tool_support("toolmodel:1b", api_mode="openre")
        assert cached is ToolSupportLevel.NATIVE

    def test_force_test_false_still_prefers_capabilities(self, monkeypatch, isolated_cache):
        # Runtime auto-detection (agent_factory calls with force_test=False)
        # benefits too: fresh truth without a prior `models --tool-support` run.
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("toolmodel:1b", force_test=False) is (
            ToolSupportLevel.NATIVE
        )

    def test_missing_caps_field_falls_back_to_sampled(self, monkeypatch, isolated_cache):
        # A server that reports capabilities for OTHER models but omits the
        # field for this one: no declaration exists -> NO signal -> sampled
        # fallback (we never fabricate NONE without a declaration).
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.model_capabilities("nocapsfield:1b") is None

        sampled = []

        def fake_generate(model, messages, tools=None, **kwargs):
            sampled.append(model)
            return {
                "message": {
                    "tool_calls": [{"name": "get_weather", "arguments": {"location": "Tokyo"}}],
                    "content": "",
                }
            }

        monkeypatch.setattr(backend, "generate", fake_generate)
        monkeypatch.setattr(backend, "unload_model", lambda model: None)
        assert backend.test_tool_support("nocapsfield:1b", force_test=True) is (
            ToolSupportLevel.NATIVE
        )
        assert sampled == ["nocapsfield:1b"]  # the sampled probe ran


# ─────────────────────────────────────────────────────────────────────────────
# Legacy-server and degraded paths
# ─────────────────────────────────────────────────────────────────────────────


class TestLegacyFallback:
    def test_legacy_server_falls_back_to_sampled_probe(self, monkeypatch, isolated_cache):
        # Old Ollama: /api/tags has NO capabilities field anywhere.
        legacy = {"models": [{"name": "toolmodel:1b"}, {"name": "plainmodel:1b"}]}
        rec = _UrlopenRecorder(legacy)
        backend = _make_backend(monkeypatch, rec)

        calls = []

        def fake_generate(model, messages, tools=None, **kwargs):
            calls.append(model)
            return {
                "message": {
                    "tool_calls": [{"name": "get_weather", "arguments": {"location": "Tokyo"}}],
                    "content": "",
                }
            }

        monkeypatch.setattr(backend, "generate", fake_generate)
        monkeypatch.setattr(backend, "unload_model", lambda model: None)

        assert backend.test_tool_support("toolmodel:1b", force_test=True) is (
            ToolSupportLevel.NATIVE
        )
        assert calls == ["toolmodel:1b"]  # sampled probe ran
        assert rec.chat_post_count == 0  # generate() was mocked, no real POST

    def test_legacy_server_force_test_false_reads_cache(self, monkeypatch, isolated_cache):
        from agentkthx.core.tool_cache import cache_tool_support

        legacy = {"models": [{"name": "toolmodel:1b"}]}
        rec = _UrlopenRecorder(legacy)
        backend = _make_backend(monkeypatch, rec)
        cache_tool_support("toolmodel:1b", ToolSupportLevel.REACT, api_mode="openre")
        assert backend.test_tool_support("toolmodel:1b", force_test=False) is (
            ToolSupportLevel.REACT
        )

    def test_legacy_server_force_test_false_untested_without_cache(
        self, monkeypatch, isolated_cache
    ):
        legacy = {"models": [{"name": "toolmodel:1b"}]}
        rec = _UrlopenRecorder(legacy)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("toolmodel:1b", force_test=False) is (
            ToolSupportLevel.UNTESTED
        )

    def test_unknown_model_on_capable_server_falls_back(self, monkeypatch, isolated_cache):
        # Model not in /api/tags: no capabilities signal -> legacy path ->
        # force_test=False returns UNTESTED (never a fabricated verdict).
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.model_capabilities("ghost:latest") is None
        assert backend.test_tool_support("ghost:latest", force_test=False) is (
            ToolSupportLevel.UNTESTED
        )

    def test_fetch_failure_is_not_memoized(self, monkeypatch, isolated_cache):
        # First fetch fails (transient) -> {} WITHOUT memoizing; a later call
        # must retry and succeed.
        rec = _UrlopenRecorder(TAGS, tags_error=urllib.error.URLError("boom"))
        backend = _make_backend(monkeypatch, rec)
        assert backend._capabilities_map() == {}
        assert backend._caps_map is None  # not memoized
        rec.tags_error = None  # server recovers
        rec.tags_payload = TAGS
        assert backend.model_capabilities("toolmodel:1b") == ["completion", "tools"]
        assert backend._caps_map is not None  # now memoized

    def test_connection_error_falls_back_to_cache(self, monkeypatch, isolated_cache):
        from agentkthx.core.tool_cache import cache_tool_support

        rec = _UrlopenRecorder(None, tags_error=urllib.error.URLError("down"))
        backend = _make_backend(monkeypatch, rec)
        cache_tool_support("toolmodel:1b", ToolSupportLevel.NATIVE, api_mode="openre")
        assert backend.test_tool_support("toolmodel:1b", force_test=False) is (
            ToolSupportLevel.NATIVE
        )


# ─────────────────────────────────────────────────────────────────────────────
# Memoization + surface
# ─────────────────────────────────────────────────────────────────────────────


class TestCapsMapSurface:
    def test_one_fetch_serves_all_models_and_modes(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        for name in ("toolmodel:1b", "plainmodel:1b", "insertmodel:1b"):
            backend.test_tool_support(name, force_test=True)
        backend.test_tool_support("toolmodel:1b", force_test=True)  # repeat
        assert rec.tags_get_count == 1

    def test_model_capabilities_returns_declared_list(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.model_capabilities("toolmodel:1b") == ["completion", "tools"]
        assert backend.model_capabilities("ghost:latest") is None

    def test_empty_capabilities_array_is_none_verdict(self, monkeypatch, isolated_cache):
        tags = {"models": [{"name": "weird:1b", "capabilities": []}]}
        rec = _UrlopenRecorder(tags)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_tool_support("weird:1b", force_test=True) is (ToolSupportLevel.NONE)

    def test_stub_subclass_without_init_is_safe(self, monkeypatch, isolated_cache):
        # Subclasses that skip __init__ (the cmd_models test stubs) must not
        # AttributeError when the capability path is reached.
        class Bare(OllamaBackend):
            def __init__(self):  # deliberately skips super().__init__ side effects
                self._base_url = "http://mock:11434"  # read-only property backing

        rec = _UrlopenRecorder(TAGS)
        backend = Bare()
        monkeypatch.setattr("urllib.request.urlopen", rec)
        assert backend.test_tool_support("toolmodel:1b", force_test=True) is (
            ToolSupportLevel.NATIVE
        )
