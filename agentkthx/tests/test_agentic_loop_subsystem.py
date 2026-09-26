"""R07.00 Phase 5 — unified agentic loop tests.

Verifies the AgenticLoopMixin extraction: structural wiring, wrapper
thinness, callback hooks firing on the streaming path (and NOT on the
non-streaming path), the per-path behavioral toggles, and — most
importantly — behavioral equivalence: both paths produce the same
AgentRun for the same conversation.
"""

import json

import pytest

from agentkthx.agent import Agent
from agentkthx.core.agentic_loop import AgenticLoopMixin, LoopCallbacks


class FakeBackend:
    backend_type = None
    api_mode = None
    base_url = "http://fake"

    def __init__(self, responses=None):
        # responses: list of dicts returned per generate() call.
        # NOTE: deliberately has NO generate_completions_stream — the
        # streaming path falls back to generate() (verified behavior of
        # _generate_stream), which keeps these tests focused on LOOP
        # equivalence. SSE parsing itself is covered by test_streaming.py.
        self.responses = list(responses or [])
        self.calls = 0

    def generate(self, messages, tools=None, **kw):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return {"content": "done", "tool_calls": [], "usage": {},
                "finish_reason": "stop"}


def _make_agent(backend, **kw):
    return Agent(model="fake", backend=backend,
                 system_prompt="sys", tools=None, soul=None, **kw)


# ---------------------------------------------------------------------------
# 1. Structural wiring
# ---------------------------------------------------------------------------

def test_mixin_provides_unified_loop_and_tool_dispatch():
    for name in ("_run_loop_iteration", "_execute_single_tool_call",
                 "_process_tool_result"):
        assert callable(getattr(AgenticLoopMixin, name, None))
    assert issubclass(Agent, AgenticLoopMixin)


def test_wrappers_are_thin_delegators():
    """_run_core / _run_core_streaming must delegate to the unified loop —
    no duplicated loop body may creep back in."""
    import inspect
    for wrapper in (Agent._run_core, Agent._run_core_streaming):
        src = inspect.getsource(wrapper)
        assert "_run_loop_iteration" in src
        # the old duplicated bodies contained these; wrappers must NOT
        for absent in ("should_block_repeat", "_is_simple_result",
                       "build_enhanced_observation"):
            assert absent not in src, f"{wrapper.__name__} re-grew loop body ({absent})"


def test_loop_body_does_not_live_on_agent():
    for name in ("_run_loop_iteration", "_execute_single_tool_call",
                 "_process_tool_result"):
        assert name not in vars(Agent), (
            f"Agent.__dict__ still contains {name} — extraction left a copy")


# ---------------------------------------------------------------------------
# 2. LoopCallbacks dataclass defaults
# ---------------------------------------------------------------------------

def test_loop_callbacks_defaults_are_non_streaming_noops():
    cb = LoopCallbacks()
    assert cb.on_step_start is None
    assert cb.on_generated is None
    assert cb.on_tool_executed is None
    assert cb.on_tool_result_committed is None
    assert cb.include_format_hint is False
    assert cb.mark_response_completed is False


# ---------------------------------------------------------------------------
# 3. Behavioral equivalence: same conversation → same AgentRun (both paths)
# ---------------------------------------------------------------------------

def _final_answer_conversation():
    return [{"content": "Thought: thinking\nFinal Answer: 42",
             "tool_calls": [], "usage": {"total_tokens": 7},
             "finish_reason": "stop"}]


def test_both_paths_same_final_answer_and_shape():
    run_a = _make_agent(FakeBackend(_final_answer_conversation())).run("q")
    run_b = _make_agent(FakeBackend(_final_answer_conversation())).run("q", stream=True)
    assert run_a.final_answer == run_b.final_answer == "42"
    for field in ("steps", "total_tokens", "tool_calls", "success"):
        assert getattr(run_a, field) == getattr(run_b, field), (
            f"AgentRun.{field} diverged between paths")


def test_both_paths_direct_answer_acceptance():
    resp = [{"content": "just an answer", "tool_calls": [], "usage": {},
             "finish_reason": "stop"}]
    run_a = _make_agent(FakeBackend(list(resp))).run("q")
    run_b = _make_agent(FakeBackend(list(resp))).run("q", stream=True)
    assert run_a.final_answer == run_b.final_answer == "just an answer"


def test_tool_call_path_produces_tool_steps_both_paths():
    from agentkthx.core.models import Tool, ToolParam

    def _exec(**kw):
        return "3"

    t = Tool(name="calc", description="calc",
             params=[ToolParam(name="expr", type="string", description="e")])
    t.execute = _exec

    resp = [{"content": "", "tool_calls": [
                 {"id": "c1", "type": "function",
                  "function": {"name": "calc", "arguments": "{\"expr\": \"1+2\"}"}}],
             "usage": {}, "finish_reason": "tool_calls"},
            {"content": "Final Answer: 3", "tool_calls": [],
             "usage": {}, "finish_reason": "stop"}]
    run_a = Agent(model="fake", backend=FakeBackend(list(resp)),
                  tools=[t], system_prompt="sys", soul=None).run("q")
    run_b = Agent(model="fake", backend=FakeBackend(list(resp)),
                  tools=[t], system_prompt="sys", soul=None).run("q", stream=True)
    assert run_a.tool_calls == run_b.tool_calls == 1
    assert run_a.final_answer == run_b.final_answer == "3"


# ---------------------------------------------------------------------------
# 4. Streaming hooks fire on streaming path only
# ---------------------------------------------------------------------------

def test_streaming_hooks_fire():
    fired = {"step_start": 0, "generated": 0, "tool": 0, "committed": 0}

    class Probe:
        pass

    agent = _make_agent(FakeBackend(_final_answer_conversation()))
    # reach into the wrapper by monkey-wrapping the hook constructors is
    # complex; instead verify indirectly: _run_loop_iteration invokes
    # provided callbacks.
    cb = LoopCallbacks(
        on_step_start=lambda n: fired.__setitem__("step_start", fired["step_start"] + 1),
        on_generated=lambda n, r: fired.__setitem__("generated", fired["generated"] + 1),
        on_tool_executed=lambda c, n, a, r: fired.__setitem__("tool", fired["tool"] + 1),
        on_tool_result_committed=lambda c: fired.__setitem__("committed", fired["committed"] + 1),
    )
    agent._run_loop_iteration("q", agent._generate,
                              enable_compaction_recovery=False, callbacks=cb)
    assert fired["step_start"] == 1 and fired["generated"] == 1
    # no tool calls in this conversation → tool hooks stay at 0
    assert fired["tool"] == 0 and fired["committed"] == 0


def test_non_streaming_defaults_are_noops():
    """LoopCallbacks() defaults must not raise anywhere in the loop."""
    agent = _make_agent(FakeBackend(_final_answer_conversation()))
    run = agent._run_loop_iteration("q", agent._generate,
                                    enable_compaction_recovery=False,
                                    callbacks=LoopCallbacks())
    assert run.final_answer == "42"


# ---------------------------------------------------------------------------
# 5. Per-path toggles
# ---------------------------------------------------------------------------

def test_wrappers_set_opposite_toggles():
    import inspect
    ns = inspect.getsource(Agent._run_core)
    st = inspect.getsource(Agent._run_core_streaming)
    assert "include_format_hint=True" in ns
    assert "mark_response_completed=True" in ns
    assert "enable_compaction_recovery=False" in ns
    assert "include_format_hint=False" in st
    assert "mark_response_completed=False" in st
    assert "enable_compaction_recovery=True" in st


def test_mark_response_completed_toggle_controls_response_status():
    from agentkthx.core.openresponses import ResponseStatus
    # non-streaming: response ends COMPLETED
    agent = _make_agent(FakeBackend(_final_answer_conversation()))
    agent.run("q")
    last = list(agent._response_history.values())[-1]
    assert last.status == ResponseStatus.COMPLETED

    # streaming: response stays IN_PROGRESS (preserved historical behavior)
    agent2 = _make_agent(FakeBackend(_final_answer_conversation()))
    agent2.run("q", stream=True)
    last2 = list(agent2._response_history.values())[-1]
    assert last2.status == ResponseStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# 6. Unified debug output (MAINT-04 goal: both paths emit the same
#    OpenResponses lifecycle lines in --debug mode)
# ---------------------------------------------------------------------------

def test_debug_output_parity_between_paths(capsys):
    a = _make_agent(FakeBackend(_final_answer_conversation()), debug=True)
    a.run("q")
    out_a = capsys.readouterr().out
    b = _make_agent(FakeBackend(_final_answer_conversation()), debug=True)
    b.run("q", stream=True)
    out_b = capsys.readouterr().out
    for lifecycle in ("[OpenResponses] Response created",
                      "[OpenResponses] Input item added",
                      "[Step 1]"):
        assert lifecycle in out_a, f"non-streaming lost: {lifecycle}"
        assert lifecycle in out_b, f"streaming lost: {lifecycle}"
