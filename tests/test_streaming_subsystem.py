"""R07.00 Phase 6 — StreamingMixin extraction tests.

Verifies the structural move of the streaming generators from
``agent.py`` to ``agentkthx/core/streaming.py``.
"""

from agentkthx.core.streaming import StreamingMixin


def test_mixin_provides_all_three_generators():
    for name in ("run_stream", "_generate_stream_chunks", "_generate_stream"):
        assert callable(getattr(StreamingMixin, name, None)), (
            f"StreamingMixin is missing {name}")


def test_agent_inherits_streaming_mixin():
    from agentkthx.agent import Agent
    assert issubclass(Agent, StreamingMixin)
    mro = [c.__name__ for c in Agent.__mro__]
    assert "StreamingMixin" in mro


def test_agent_does_not_redefine_moved_generators():
    """Moved, not copied — a shadowing copy would diverge silently."""
    from agentkthx.agent import Agent
    for name in ("run_stream", "_generate_stream_chunks", "_generate_stream"):
        assert name not in vars(Agent), (
            f"Agent.__dict__ still contains {name} — extraction left a "
            f"shadowing copy behind")


def test_run_stream_yields_sse_events():
    """End-to-end through the mixin: a mocked backend produces
    response.created → output_text deltas → response.completed."""
    from agentkthx.agent import Agent

    class FakeBackend:
        backend_type = None
        api_mode = None
        def generate(self, messages, tools=None, **kw):
            return {"content": "hello", "tool_calls": [], "usage": {},
                    "finish_reason": "stop"}

    agent = Agent(model="fake", backend=FakeBackend(),
                  system_prompt="sys", tools=None, soul=None)
    events = list(agent.run_stream("hi"))
    types = []
    for e in events:
        first = e.split("\n")[0]
        if first.startswith("event: "):
            types.append(first[len("event: "):].strip())
    assert "response.created" in types or "response.queued" in types
    assert "response.completed" in types


def test_generate_stream_returns_generate_shaped_dict():
    """Non-streaming-fallback path: dict shape identical to _generate()."""
    from agentkthx.agent import Agent

    class FakeBackend:
        backend_type = None
        api_mode = None
        def generate(self, messages, tools=None, **kw):
            return {"content": "the answer", "tool_calls": [], "usage": {},
                    "finish_reason": "stop"}

    agent = Agent(model="fake", backend=FakeBackend(),
                  system_prompt="sys", tools=None, soul=None)
    result = agent._generate_stream()
    assert isinstance(result, dict)
    assert result.get("content") == "the answer"
    assert result.get("tool_calls") == []


def test_run_stream_keyboardinterrupt_during_tool_yields_response_failed():
    """R07.01 regression: Ctrl+C while a tool executes inside run_stream()
    must yield a clean ``response.failed`` SSE event.

    Pre-R07.01 this path referenced the undefined ``ResponseStateEvent``
    (survived verbatim from the original agent.py:1499) and raised
    NameError instead of emitting the cancellation event.
    """
    from agentkthx.agent import Agent

    class FakeBackend:
        backend_type = None
        api_mode = None
        def generate(self, messages, tools=None, **kw):
            return {
                "content": 'Action: calculator\nAction Input: {"expression": "1+1"}',
                "tool_calls": [], "usage": {}, "finish_reason": "stop",
            }

    agent = Agent(model="fake", backend=FakeBackend(),
                  system_prompt="sys", tools=["calculator"], soul=None)

    def _interrupt(name, args, prompt=""):
        raise KeyboardInterrupt()

    agent._execute_tool = _interrupt

    events = list(agent.run_stream("hi"))
    types = []
    for e in events:
        first = e.split("\n")[0]
        if first.startswith("event: "):
            types.append(first[len("event: "):].strip())

    assert "response.failed" in types, (
        f"expected response.failed after Ctrl+C during tool execution, "
        f"got event sequence: {types}")
