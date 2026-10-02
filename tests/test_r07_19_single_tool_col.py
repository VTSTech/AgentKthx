"""R07.19 follow-ups #10 + #11 — single tools column, NONE→REACT fallback,
and the new thinking/reasoning support column.

Follow-up #10 (user directive: "remove openai/openre dual checks ... a single
column 'tools' ... when support is detected as 'None' default to ReAct as a
fallback. no models should have None. None is essentially untested."):

- ``cmd_models`` renders ONE ``tools`` column; the ``openre``/``openai``
  header pair is gone and each model is tested exactly once.
- NONE is retired as a produced verdict: capabilities-without-tools and the
  explicit "does not support tools" rejection return REACT; legacy "none"
  cache entries normalize to REACT on every read
  (``ToolSupportLevel.effective`` / ``detect`` / backend cache paths).
- Cache writes land on the ONE plain-key entry (no ``model:openai``
  namespaces from internal callers).
- ``agent_factory`` treats NONE exactly like UNTESTED → force_react=True.

Follow-up #11 (user directive: "add a column for thinking/reasoning support"):

- ``cmd_models`` renders a ``think`` column fed by
  ``backend.test_thinking_support`` — Ollama reads the server's
  ``thinking`` capability declaration; cloud backends use conservative
  name heuristics; no signal → UNKNOWN (never cached).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re

import pytest

from agentkthx.backends.ollama import OllamaBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.cli.commands import models as models_mod
from agentkthx.core.tool_cache import (
    cache_thinking_support,
    cache_tool_support,
    get_cached_thinking_support,
    get_cached_tool_support,
)
from agentkthx.core.types import ThinkingSupport, ToolSupportLevel

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers (same mocking shape as test_r07_19_caps_tool_support.py)
# ─────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _UrlopenRecorder:
    """Serves tags_payload for GET /api/tags; records every request."""

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


TAGS = {
    "models": [
        {"name": "toolmodel:1b", "capabilities": ["completion", "tools"]},
        {"name": "thinker:1b", "capabilities": ["completion", "thinking", "tools"]},
        {"name": "thinker-notools:1b", "capabilities": ["completion", "thinking"]},
        {"name": "plainmodel:1b", "capabilities": ["completion"]},
        {"name": "nocapsfield:1b"},
    ]
}


@pytest.fixture()
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent tool-support cache at a temp dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path / "cache"


def _make_backend(monkeypatch, recorder: _UrlopenRecorder, api_mode="openre") -> OllamaBackend:
    backend = OllamaBackend(base_url="http://mock:11434", api_mode=api_mode)
    monkeypatch.setattr("urllib.request.urlopen", recorder)
    return backend


# ─────────────────────────────────────────────────────────────────────────────
# cmd_models table shape: one tools column + think column
# ─────────────────────────────────────────────────────────────────────────────


def _entry(name: str, size_gb: float = 0.7, quant: str = "Q4_K_M", family: str = "llama") -> dict:
    return {
        "name": name,
        "size": int(size_gb * 1024**3),
        "details": {"family": family, "quantization_level": quant},
    }


class StubOllamaBackend(OllamaBackend):
    """No-network OllamaBackend stand-in (same shape as the width tests)."""

    def __init__(self, entries, caps=None):
        self._entries = entries
        self._base_url = "http://stub:11434"
        self.api_mode = None
        self._caps = caps

    def is_running(self):
        return True

    def list_models(self):
        return list(self._entries)

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768

    def model_capabilities(self, model):
        if self._caps is None:
            return None
        return self._caps.get(model)


class StubCloudBackend:
    is_cloud = True

    def __init__(self, entries):
        self._entries = entries
        self.base_url = "https://stub.cloud/v1"
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return list(self._entries)

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768


_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def _run_models(
    monkeypatch, backend, entries, tool_support=False, no_cache=False, tool_cache_override=None
) -> str:
    """Drive the real cmd_models over a stub backend, return captured stdout."""
    monkeypatch.setattr(
        "agentkthx.cli.commands.models.get_config",
        lambda: argparse.Namespace(backend="stub"),
    )
    monkeypatch.setattr(
        "agentkthx.cli.commands.models.get_backend", lambda name, api_mode=None: backend
    )
    monkeypatch.setattr("agentkthx.cli._init_acp", lambda *a, **k: (None, None))
    if tool_cache_override is not None:
        get_tools = tool_cache_override
    else:
        get_tools = lambda model, api_mode="openre": None  # noqa: E731
    monkeypatch.setattr("agentkthx.core.tool_cache.get_cached_tool_support", get_tools)
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_thinking_support",
        lambda model: None,
    )
    args = argparse.Namespace(
        backend="stub", tool_support=tool_support, api_mode=None, no_cache=no_cache
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = models_mod.cmd_models(args)
    assert rc == 0
    return buf.getvalue()


class TestSingleToolsColumn:
    """Follow-up #10: the openre/openai dual columns are gone."""

    def test_header_has_single_tools_column(self, monkeypatch):
        out = _run_models(monkeypatch, StubOllamaBackend([_entry("qwen2.5:0.5b")]), None)
        header_line = next(ln for ln in out.splitlines() if "Name" in ln and "Context" in ln)
        header = _ANSI_RE.sub("", header_line)
        assert "tools" in header
        assert "think" in header
        assert "openre" not in header
        assert "openai" not in header

    def test_cloud_header_has_single_tools_column(self, monkeypatch):
        out = _run_models(
            monkeypatch,
            StubCloudBackend([{"name": "glm-4.5-flash", "size": 0, "details": {}}]),
            None,
        )
        header_line = next(ln for ln in out.splitlines() if "Name" in ln and "Context" in ln)
        header = _ANSI_RE.sub("", header_line)
        assert "tools" in header and "think" in header
        assert "openre" not in header and "openai" not in header

    def test_legend_has_no_none_entry(self, monkeypatch):
        out = _run_models(monkeypatch, StubOllamaBackend([_entry("qwen2.5:0.5b")]), None)
        assert "✗ none" not in out
        assert "✓ native" in out
        assert "○ react" in out
        assert "✓ yes" in out  # think legend

    def test_tool_support_flag_announces_single_check(self, monkeypatch):
        out = _run_models(
            monkeypatch,
            StubOllamaBackend([_entry("qwen2.5:0.5b")]),
            None,
            tool_support=True,
        )
        assert "single capability check" in out

    def test_tool_support_tests_model_exactly_once(self, monkeypatch):
        # The dual-mode loop made 1-2 test_tool_support calls per model; the
        # single check makes exactly one.
        calls = []

        class Counting(StubOllamaBackend):
            def test_tool_support(self, model, family=None, force_test=False):
                calls.append(model)
                return ToolSupportLevel.NATIVE

        backend = Counting([_entry("qwen2.5:0.5b"), _entry("gemma3:270m")])
        _run_models(monkeypatch, backend, None, tool_support=True)
        assert calls == ["qwen2.5:0.5b", "gemma3:270m"]
        assert len(calls) == len(set(calls))  # no model tested twice

    def test_no_cache_retests_regardless_of_store(self, monkeypatch, isolated_cache):
        cache_tool_support("qwen2.5:0.5b", ToolSupportLevel.NATIVE)
        seen = []

        class Counting(StubOllamaBackend):
            def test_tool_support(self, model, family=None, force_test=False):
                seen.append(model)
                return ToolSupportLevel.REACT

        backend = Counting([_entry("qwen2.5:0.5b")])
        out = _run_models(monkeypatch, backend, None, tool_support=True, no_cache=True)
        assert seen == ["qwen2.5:0.5b"]
        assert "react" in _ANSI_RE.sub("", out)


class TestNoneRetiredFallback:
    """Follow-up #10: no model is ever classified or displayed as none."""

    def test_effective_maps_none_to_react(self):
        assert ToolSupportLevel.effective(ToolSupportLevel.NONE) is ToolSupportLevel.REACT
        assert ToolSupportLevel.effective(ToolSupportLevel.NATIVE) is ToolSupportLevel.NATIVE
        assert ToolSupportLevel.effective(ToolSupportLevel.REACT) is ToolSupportLevel.REACT
        assert ToolSupportLevel.effective(ToolSupportLevel.UNTESTED) is ToolSupportLevel.UNTESTED

    def test_detect_normalizes_legacy_none_cache(self, monkeypatch, isolated_cache):
        cache_tool_support("legacy:1b", ToolSupportLevel.NONE)
        assert ToolSupportLevel.detect("legacy:1b") is ToolSupportLevel.REACT

    def test_backend_cache_read_normalizes_legacy_none(self, monkeypatch, isolated_cache):
        # Legacy server (no capabilities field) + a pre-#10 "none" cache
        # entry → the read path must answer REACT, never NONE.
        legacy = {"models": [{"name": "oldmodel:1b"}]}
        rec = _UrlopenRecorder(legacy)
        backend = _make_backend(monkeypatch, rec)
        cache_tool_support("oldmodel:1b", ToolSupportLevel.NONE)
        assert backend.test_tool_support("oldmodel:1b", force_test=False) is (
            ToolSupportLevel.REACT
        )

    def test_error_path_caches_react_not_none(self, monkeypatch, isolated_cache):
        # Explicit tools rejection ("Unsupported param: tools") — was NONE.
        rec = _UrlopenRecorder({"models": [{"name": "rejector:1b"}]})

        backend = _make_backend(monkeypatch, rec)

        def boom(model, messages, tools=None, **kwargs):
            raise RuntimeError(
                'Ollama HTTP error 500: {"error":{"message":"Unsupported param: tools"}}'
            )

        monkeypatch.setattr(backend, "generate", boom)
        monkeypatch.setattr(backend, "unload_model", lambda model: None)
        assert backend.test_tool_support("rejector:1b", force_test=True) is (ToolSupportLevel.REACT)
        cached = get_cached_tool_support("rejector:1b")
        assert cached is ToolSupportLevel.REACT

    def test_models_table_never_renders_none(self, monkeypatch, isolated_cache):
        # Even a stale none cache entry renders as react in the table.
        cache_tool_support("stale:1b", ToolSupportLevel.NONE)

        def fake_get(model, api_mode="openre"):
            return ToolSupportLevel.NONE if model == "stale:1b" else None

        class StaleCap(StubOllamaBackend):
            def model_capabilities(self, model):
                return ["completion"]  # declared, no tools → REACT anyway

        out = _run_models(
            monkeypatch, StaleCap([_entry("stale:1b")]), None, tool_cache_override=fake_get
        )
        visible = _ANSI_RE.sub("", out)
        assert "✗ none" not in visible
        assert "react" in visible


class TestSingleCacheNamespace:
    """Follow-up #10: one authoritative check → one plain-key cache entry."""

    def test_capabilities_verdict_writes_plain_key_only(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend_openre = _make_backend(monkeypatch, rec, api_mode="openre")
        backend_openre.test_tool_support("toolmodel:1b", force_test=True)

        cache = json.loads((isolated_cache / "agentkthx" / "tool_support.json").read_text())
        assert "toolmodel:1b" in cache
        assert "toolmodel:1b:openai" not in cache
        assert cache["toolmodel:1b"]["support"] == "native"

    def test_same_entry_visible_from_both_api_modes(self, monkeypatch, isolated_cache):
        # The single plain-key entry answers reads regardless of the
        # backend's api_mode — the legacy fallback bridges old-style reads.
        rec = _UrlopenRecorder(TAGS)
        b_re = _make_backend(monkeypatch, rec, api_mode="openre")
        b_ai = _make_backend(monkeypatch, rec, api_mode="openai")
        b_re.test_tool_support("toolmodel:1b", force_test=True)
        assert b_ai.test_tool_support("toolmodel:1b", force_test=False) is (ToolSupportLevel.NATIVE)


class TestAgentFactoryNoneFallback:
    """Follow-up #10: agent_factory treats NONE exactly like UNTESTED."""

    def _build(self, monkeypatch, support):
        from types import SimpleNamespace

        from agentkthx.agent import Agent
        from agentkthx.cli import agent_factory as fact
        from tests.test_loop_resilience import StubBackend

        class NoneBackend(StubBackend):
            def test_tool_support(self, model, force_test=False):
                return support

        captured = {}

        class _CaptureAgent(Agent):
            def __init__(self, **kwargs):
                captured.update(kwargs)
                super().__init__(**kwargs)

        monkeypatch.setattr(fact, "get_backend", lambda *a, **kw: NoneBackend([]))
        monkeypatch.setattr(fact, "Agent", _CaptureAgent)
        args = argparse.Namespace(
            model="test-model",
            backend="stub",
            debug=False,
            tools="shell,read_file",
            force_react=False,
            security="max",
            soul=None,
            soul_level=2,
        )
        cfg = SimpleNamespace(
            backend="stub",
            default_model="test-model",
            num_ctx=8192,
            max_tool_retries=2,
            debug=False,
        )
        fact._build_agent(args, cfg)
        return captured.get("force_react")

    def test_legacy_none_cache_forces_react(self, monkeypatch):
        assert self._build(monkeypatch, ToolSupportLevel.NONE) is True

    def test_untested_still_forces_react(self, monkeypatch):
        assert self._build(monkeypatch, ToolSupportLevel.UNTESTED) is True

    def test_native_keeps_native(self, monkeypatch):
        assert self._build(monkeypatch, ToolSupportLevel.NATIVE) is False

    def test_react_forces_react(self, monkeypatch):
        assert self._build(monkeypatch, ToolSupportLevel.REACT) is True


# ─────────────────────────────────────────────────────────────────────────────
# Follow-up #11: thinking / reasoning support
# ─────────────────────────────────────────────────────────────────────────────


class TestOllamaThinkingDetection:
    """Ollama reads the authoritative /api/tags thinking declaration."""

    def test_thinking_capability_is_yes(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_thinking_support("thinker:1b") is ThinkingSupport.YES

    def test_no_thinking_capability_is_no(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_thinking_support("plainmodel:1b") is ThinkingSupport.NO

    def test_thinking_without_tools_is_still_yes(self, monkeypatch, isolated_cache):
        # Reasoning and tool calling are independent axes.
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_thinking_support("thinker-notools:1b") is ThinkingSupport.YES

    def test_unknown_not_cached(self, monkeypatch, isolated_cache):
        # Legacy server: no capabilities field → UNKNOWN, and UNKNOWN is
        # never persisted (the situation is recoverable).
        legacy = {"models": [{"name": "oldmodel:1b"}]}
        rec = _UrlopenRecorder(legacy)
        backend = _make_backend(monkeypatch, rec)
        assert backend.test_thinking_support("oldmodel:1b") is ThinkingSupport.UNKNOWN
        assert get_cached_thinking_support("oldmodel:1b") is None

    def test_verdict_cached(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        backend.test_thinking_support("thinker:1b")
        assert get_cached_thinking_support("thinker:1b") is ThinkingSupport.YES

    def test_one_tags_get_serves_tools_and_thinking(self, monkeypatch, isolated_cache):
        rec = _UrlopenRecorder(TAGS)
        backend = _make_backend(monkeypatch, rec)
        backend.test_tool_support("thinker:1b", force_test=True)
        backend.test_thinking_support("thinker:1b")
        assert rec.chat_post_count == 0
        # One GET /api/tags total (memoized capabilities map shared by both).
        assert sum(1 for m, u in rec.requests if "/api/tags" in u) == 1


class _ConcreteCompat(OpenAICompatibleBackend):
    """Minimal concrete OpenAI-compatible backend (ABC needs implementations)."""

    @property
    def backend_type(self):
        raise NotImplementedError

    @property
    def base_url(self):
        return "https://compat.stub/v1"

    def generate(self, *a, **k):
        raise NotImplementedError

    def generate_stream(self, *a, **k):
        raise NotImplementedError

    def list_models(self):
        return []

    def test_tool_support(self, model, family=None, force_test=False):
        return ToolSupportLevel.UNTESTED


class TestCloudThinkingHeuristic:
    """OpenAI-compat backends: conservative model-name heuristics."""

    @pytest.mark.parametrize(
        "name",
        [
            "deepseek/deepseek-r1:free",
            "deepseek-r1:8b",
            "deepseek/deepseek-r1-0528",
            "qwen/qwq-32b:free",
            "openai/o1",
            "openai/o3-mini",
            "openai/o3:batch",
            "openai/o4-mini",
            "z-ai/glm-4.5-air:free",
            "z-ai/glm-4.6",
            "~z-ai/glm-5-latest",
            "qwen/qwen3-30b-a3b:free",
            "org/supermodel-thinking",
            "mistralai/magistral-small-2506",
            "microsoft/phi-4-reasoning-plus:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        ],
    )
    def test_markers_hit(self, name):
        assert OpenAICompatibleBackend._name_matches_thinking(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "openai/gpt-4o",  # must NOT hit the o-series pattern
            "openai/gpt-4o-mini",
            "qwen2.5:0.5b",
            "llama3.1:8b",
            "gemma3:270m",
            "granite4:350m",
            "mistralai/mistral-small-24b-instruct",
            # R07.19 follow-up #13: vendor-name bleed — the ORG carries the
            # marker word, the model does not. Was a false YES (smoke test:
            # 4 thinkingmachines/inkling rows flagged via "thinking").
            "thinkingmachines/inkling",
            "thinkingmachines/inkling-small",
            "thinkingmachines/inkling-small:free",
            "thinkingmachines/inkling:free",
            "reasonstack/base-model",
            "~z-ai/glm-latest",  # routed alias without a version marker
        ],
    )
    def test_non_markers_miss(self, name):
        assert OpenAICompatibleBackend._name_matches_thinking(name) is False

    def test_hit_returns_yes_and_caches(self, monkeypatch, isolated_cache):
        backend = _ConcreteCompat()
        assert backend.test_thinking_support("deepseek-r1:8b") is ThinkingSupport.YES
        assert get_cached_thinking_support("deepseek-r1:8b") is ThinkingSupport.YES

    def test_miss_returns_unknown_not_no(self, monkeypatch, isolated_cache):
        # Absence of a marker is NOT evidence of absence for cloud models.
        backend = _ConcreteCompat()
        assert backend.test_thinking_support("gpt-4o") is ThinkingSupport.UNKNOWN
        assert get_cached_thinking_support("gpt-4o") is None  # unknown not cached

    def test_cached_verdict_wins(self, monkeypatch, isolated_cache):
        cache_thinking_support("gpt-4o", ThinkingSupport.NO)
        backend = _ConcreteCompat()
        assert backend.test_thinking_support("gpt-4o") is ThinkingSupport.NO


class TestThinkingCacheAndBase:
    """Cache helpers + the BaseBackend default."""

    def test_round_trip(self, monkeypatch, isolated_cache):
        cache_thinking_support("m:1b", ThinkingSupport.YES, family="qwen3")
        assert get_cached_thinking_support("m:1b") is ThinkingSupport.YES

    def test_miss_returns_none(self, monkeypatch, isolated_cache):
        assert get_cached_thinking_support("ghost:1b") is None

    def test_tool_and_thinking_entries_coexist(self, monkeypatch, isolated_cache):
        cache_tool_support("m:1b", ToolSupportLevel.NATIVE)
        cache_thinking_support("m:1b", ThinkingSupport.NO)
        assert get_cached_tool_support("m:1b") is ToolSupportLevel.NATIVE
        assert get_cached_thinking_support("m:1b") is ThinkingSupport.NO

    def test_base_backend_default_unknown(self, monkeypatch, isolated_cache):
        from agentkthx.backends.base import BaseBackend

        class Bare(BaseBackend):
            @property
            def backend_type(self):
                raise NotImplementedError

            @property
            def base_url(self):
                return "http://bare"

            def generate(self, *a, **k):
                raise NotImplementedError

            def generate_stream(self, *a, **k):
                raise NotImplementedError

            def list_models(self):
                return []

            def test_tool_support(self, model, family=None, force_test=False):
                return ToolSupportLevel.UNTESTED

        assert Bare().test_thinking_support("whatever:1b") is ThinkingSupport.UNKNOWN


class TestThinkingDisplay:
    """The think column renders and keeps the grid aligned."""

    def test_thinking_status_strings(self):
        from agentkthx.cli.utils import _thinking_status

        assert _ANSI_RE.sub("", _thinking_status("yes")) == "✓ yes"
        assert _ANSI_RE.sub("", _thinking_status("no")) == "✗ no"
        assert _ANSI_RE.sub("", _thinking_status("unknown")) == "? unknown"
        assert _ANSI_RE.sub("", _thinking_status("error")) == "✗ error"
        assert _ANSI_RE.sub("", _thinking_status("garbage")) == "? unknown"

    def test_table_renders_think_column_values(self, monkeypatch, isolated_cache):
        caps = {
            "thinker:1b": ["completion", "thinking", "tools"],
            "plain:1b": ["completion"],
        }
        backend = StubOllamaBackend([_entry("thinker:1b"), _entry("plain:1b")], caps=caps)
        out = _run_models(monkeypatch, backend, None)
        visible = _ANSI_RE.sub("", out)
        assert "✓ yes" in visible
        assert "✗ no" in visible

    def test_stub_backend_without_thinking_support_shows_unknown(self, monkeypatch):
        # Third-party stubs without test_thinking_support degrade to
        # unknown, never crash.
        class NoThink(StubCloudBackend):
            pass

        out = _run_models(
            monkeypatch, NoThink([{"name": "glm-4.5-flash", "size": 0, "details": {}}]), None
        )
        visible = _ANSI_RE.sub("", out)
        assert "? unknown" in visible

    def test_cloud_backend_heuristic_populates_think(self, monkeypatch, isolated_cache):
        out = _run_models(
            monkeypatch,
            StubCloudBackend(
                [
                    {"name": "deepseek/deepseek-r1:free", "size": 0, "details": {}},
                    {"name": "openai/gpt-4o", "size": 0, "details": {}},
                ]
            ),
            None,
        )
        visible = _ANSI_RE.sub("", out)
        # r1 hits the name heuristic → yes; gpt-4o misses → unknown.
        assert "✓ yes" in visible
        assert "? unknown" in visible
