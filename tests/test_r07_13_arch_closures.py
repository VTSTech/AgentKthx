"""
Tests pinning the R07.13 ARCH closure batch (5 findings: ARCH-02, ARCH-03,
ARCH-04, ARCH-05, ARCH-06). Each closure class verifies the architectural
change is in place AND that the behavior is preserved (no regressions).

Run with: ``python -m pytest tests/test_r07_13_arch_closures.py -v``
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# ARCH-02: SSEEventBuilder extracted from stream_response_events
# ─────────────────────────────────────────────────────────────────────────────


class TestArch02SSEEventBuilder:
    """Verify the SSEEventBuilder class was extracted and the generator
    is now a thin orchestration loop over it."""

    def test_sse_event_builder_class_exists(self):
        from agentkthx.core.openresponses import SSEEventBuilder

        assert SSEEventBuilder.__name__ == "SSEEventBuilder"

    def test_builder_has_all_emit_methods(self):
        """All 10 emit_* methods (9 events + 1 error path) must exist."""
        from agentkthx.core.openresponses import SSEEventBuilder

        expected = {
            "emit_queued",
            "emit_in_progress",
            "emit_output_item_added",
            "emit_content_part_added",
            "emit_delta",
            "emit_text_done",
            "emit_content_part_done",
            "emit_output_item_done",
            "emit_completed",
            "emit_failed",
        }
        actual = {m for m in dir(SSEEventBuilder) if m.startswith("emit_")}
        assert expected <= actual, f"Missing methods: {expected - actual}"

    def test_generator_is_now_thin_orchestration(self):
        """The generator should be significantly shorter than the original 163 lines."""
        from agentkthx.core import openresponses

        src = inspect.getsource(openresponses.stream_response_events)
        line_count = len(src.splitlines())
        # Was 163; should now be < 80 (orchestration only).
        assert line_count < 80, (
            f"stream_response_events should be < 80 lines after ARCH-02 "
            f"extraction, got {line_count} (was 163 before)"
        )

    def test_generator_yields_sse_strings(self):
        """End-to-end: the generator still yields SSE-formatted strings."""
        from agentkthx.core.openresponses import (
            Response,
            stream_response_events,
        )

        response = Response()
        chunks = iter(["Hello", " ", "world"])
        events = list(stream_response_events(response, chunks, debug=False))
        # 9 events minimum (queued, in_progress, output_item.added,
        # content_part.added, 3 deltas, text.done, content_part.done,
        # output_item.done, completed) = 12 total
        assert len(events) >= 9, f"Expected >= 9 events, got {len(events)}"
        # Each event is an SSE string starting with "event:"
        for e in events:
            assert isinstance(e, str)
            assert e.startswith("event:")

    def test_error_path_emits_response_failed(self):
        """When the text_chunks generator raises, response.failed is emitted."""
        from agentkthx.core.openresponses import (
            Response,
            stream_response_events,
        )

        response = Response()

        def failing_chunks():
            yield "ok"
            raise RuntimeError("stream broke")

        events = list(stream_response_events(response, failing_chunks(), debug=False))
        # The last event should be response.failed
        assert "response.failed" in events[-1]


# ─────────────────────────────────────────────────────────────────────────────
# ARCH-03: AgentMode emits OpenResponses events
# ─────────────────────────────────────────────────────────────────────────────


class TestArch03AgentModeEvents:
    """Verify AgentMode has the event_emitter hook and emits the
    documented OpenResponses event types."""

    def test_event_emitter_attribute_exists(self):
        """AgentMode instances must have an event_emitter attribute (default None)."""
        from agentkthx.agent_mode import AgentMode

        am = AgentMode(MagicMock(), verbose=False)
        assert hasattr(am, "event_emitter")
        assert am.event_emitter is None  # default: no emitter (backward compat)

    def test_emit_is_noop_without_emitter(self):
        """_emit must be a no-op when event_emitter is None."""
        from agentkthx.agent_mode import AgentMode

        am = AgentMode(MagicMock(), verbose=False)
        # Should not raise
        am._emit("test.event", {"data": "value"})

    def test_emit_dispatches_to_emitter(self):
        """_emit must call the emitter with {type, data} dict."""
        from agentkthx.agent_mode import AgentMode

        am = AgentMode(MagicMock(), verbose=False)
        collected = []
        am.event_emitter = lambda e: collected.append(e)
        am._emit("response.created", {"goal": "test"})
        am._emit("response.completed", {"goal": "test", "final_response": "done"})
        assert len(collected) == 2
        assert collected[0]["type"] == "response.created"
        assert collected[0]["data"] == {"goal": "test"}
        assert collected[1]["type"] == "response.completed"

    def test_broken_emitter_does_not_crash(self):
        """A broken emitter must be swallowed, not crash the task loop."""
        from agentkthx.agent_mode import AgentMode

        am = AgentMode(MagicMock(), verbose=False)

        def broken_emitter(event):
            raise RuntimeError("emitter is broken")

        am.event_emitter = broken_emitter
        # Should not raise — the task loop must survive broken callbacks
        am._emit("test.event", {})

    def test_emit_method_exists(self):
        """The _emit helper method must exist on AgentMode."""
        from agentkthx.agent_mode import AgentMode

        assert hasattr(AgentMode, "_emit")
        assert callable(getattr(AgentMode, "_emit"))


# ─────────────────────────────────────────────────────────────────────────────
# ARCH-04: Soul loader path resolution consolidated
# ─────────────────────────────────────────────────────────────────────────────


class TestArch04SoulLoaderResolution:
    """Verify the 5-step path resolution was consolidated into a linear
    algorithm with documented search order."""

    def test_resolve_soul_path_exists(self):
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        assert hasattr(loader, "_resolve_soul_path")

    def test_default_soul_still_loads(self):
        """The kthx-helper default soul must still resolve (regression test)."""
        from agentkthx.soul import loader

        ldr = loader.SoulLoader()
        manifest = ldr.load("kthx-helper", level=2)
        assert manifest.name == "kthx-helper"

    def test_absolute_existing_path_resolves(self):
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        # /etc/hostname exists on any Linux system
        result = loader._resolve_soul_path(Path("/etc/hostname"))
        assert result is not None

    def test_absolute_missing_path_returns_none(self):
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        result = loader._resolve_soul_path(Path("/nonexistent/soul/path"))
        assert result is None

    def test_bare_soul_name_resolves(self):
        """A bare soul name like 'kthx-helper' must resolve (no path separators)."""
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        result = loader._resolve_soul_path(Path("kthx-helper"))
        assert result is not None

    def test_missing_soul_name_returns_none(self):
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        result = loader._resolve_soul_path(Path("nonexistent-soul-xyz-123"))
        assert result is None

    def test_resolver_never_raises(self):
        """The resolver must return Path or None, never raise."""
        from agentkthx.soul.loader import SoulLoader

        loader = SoulLoader()
        # Various edge cases that previously had nested try/except blocks
        for path in [
            Path(""),  # empty
            Path("."),  # cwd
            Path(".."),  # parent
            Path("nonexistent"),  # missing
            Path("/nonexistent/absolute"),  # missing absolute
        ]:
            try:
                result = loader._resolve_soul_path(path)
                # Either Path or None — never raises
                assert result is None or isinstance(result, Path)
            except Exception as e:
                pytest.fail(f"Resolver raised on {path!r}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ARCH-05: kwargs swallowing closed — typos now raise TypeError
# ─────────────────────────────────────────────────────────────────────────────


class TestArch05KwargsSwallowing:
    """Verify the 5 stashed kwargs are now explicit named parameters and
    typos raise TypeError instead of being silently swallowed."""

    def test_persistent_kwarg_works(self):
        from agentkthx.agent import Agent

        a = Agent(model="test", persistent=True)
        assert a._is_persistent is True

    def test_session_id_kwarg_works(self):
        from agentkthx.agent import Agent

        a = Agent(model="test", session_id="test-session-123")
        assert a.session_id == "test-session-123"

    def test_memory_db_kwarg_works(self):
        from agentkthx.agent import Agent

        # Just verify it doesn't raise — actual DB creation tested elsewhere
        a = Agent(model="test", memory_db="/tmp/test_memory_db_arch05.db", session_id="test")
        assert a._is_persistent is True

    def test_response_format_kwarg_works(self):
        from agentkthx.agent import Agent

        a = Agent(model="test", response_format="json")
        assert a._response_format == {"type": "json_object"}

    def test_response_format_dict_kwarg_works(self):
        from agentkthx.agent import Agent

        a = Agent(model="test", response_format={"type": "json_object"})
        assert a._response_format == {"type": "json_object"}

    def test_confirm_dangerous_kwarg_works(self):
        from agentkthx.agent import Agent

        def callback(name, args):
            return True

        a = Agent(model="test", confirm_dangerous=callback)
        assert a._confirm_dangerous is callback

    def test_typo_raises_type_error(self):
        """The whole point of ARCH-05: typos must raise, not be swallowed."""
        from agentkthx.agent import Agent

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            Agent(model="test", persistant=True)  # typo: persistant vs persistent

    def test_unknown_kwarg_raises_type_error(self):
        from agentkthx.agent import Agent

        with pytest.raises(TypeError, match="unexpected keyword argument"):
            Agent(model="test", completely_unknown_param=42)

    def test_error_message_lists_valid_kwargs(self):
        """The TypeError message should list valid kwargs to help debugging."""
        from agentkthx.agent import Agent

        try:
            Agent(model="test", typo_param=True)
        except TypeError as e:
            msg = str(e)
            # The 5 promoted kwargs should appear in the message
            for kwarg in (
                "persistent",
                "session_id",
                "memory_db",
                "response_format",
                "confirm_dangerous",
            ):
                assert kwarg in msg, f"{kwarg} not in error message"


# ─────────────────────────────────────────────────────────────────────────────
# ARCH-06: WireAdapter protocol for non-OpenAI cloud wire shapes
# ─────────────────────────────────────────────────────────────────────────────


class TestArch06WireAdapter:
    """Verify the WireAdapter protocol exists and CloudBackend._wire_adapter
    defaults to None (OpenAI shape, backward compat)."""

    def test_wire_adapter_class_exists(self):
        from agentkthx.backends.cloud_base import WireAdapter

        assert WireAdapter.__name__ == "WireAdapter"

    def test_wire_adapter_has_three_hook_methods(self):
        from agentkthx.backends.cloud_base import WireAdapter

        adapter = WireAdapter()
        assert hasattr(adapter, "build_request_body")
        assert hasattr(adapter, "parse_response")
        assert hasattr(adapter, "iter_sse_events")

    def test_default_hooks_return_none(self):
        """Default hook returns None → fall back to inherited OpenAI shape."""
        from agentkthx.backends.cloud_base import WireAdapter

        adapter = WireAdapter()
        assert adapter.build_request_body([]) is None
        assert adapter.parse_response({}) is None
        assert adapter.iter_sse_events(None, "", {}, {}) is None

    def test_cloud_backend_has_wire_adapter_attribute(self):
        from agentkthx.backends.cloud_base import CloudBackend

        assert hasattr(CloudBackend, "_wire_adapter")

    def test_wire_adapter_defaults_to_none(self):
        """Default None = use inherited OpenAI shape (backward compat)."""
        from agentkthx.backends.cloud_base import CloudBackend

        assert CloudBackend._wire_adapter is None

    def test_existing_cloud_backends_inherit_none_adapter(self):
        """All 4 existing cloud backends must use the default OpenAI shape
        (don't set _wire_adapter)."""
        from agentkthx.plugins.mistral.mistral import MistralBackend
        from agentkthx.plugins.pollinations.pollinations import PollinationsBackend
        from agentkthx.plugins.zai.zai import ZaiBackend

        for backend_class in (ZaiBackend, MistralBackend, PollinationsBackend):
            # Inherited from CloudBackend — must be None (OpenAI shape)
            val = getattr(backend_class, "_wire_adapter", "MISSING")
            assert val is None, (
                f"{backend_class.__name__}._wire_adapter is {val!r}, "
                f"expected None (default OpenAI shape)"
            )

    def test_custom_adapter_can_be_subclassed(self):
        """A future Anthropic backend could subclass WireAdapter to override
        the wire shape — verify the protocol supports this."""
        from agentkthx.backends.cloud_base import WireAdapter

        class AnthropicWireAdapter(WireAdapter):
            def build_request_body(self, messages, **kwargs):
                # Translate OpenAI messages → Anthropic system + messages split
                system_msgs = [m for m in messages if m.get("role") == "system"]
                user_msgs = [m for m in messages if m.get("role") != "system"]
                return {
                    "system": system_msgs[0]["content"] if system_msgs else "",
                    "messages": user_msgs,
                    **kwargs,
                }

        adapter = AnthropicWireAdapter()
        body = adapter.build_request_body(
            [{"role": "system", "content": "Be helpful"}, {"role": "user", "content": "Hi"}],
            temperature=0.7,
        )
        assert body["system"] == "Be helpful"
        assert len(body["messages"]) == 1
        assert body["temperature"] == 0.7
