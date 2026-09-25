"""R07.01 — smoke tests for the Agent OpenResponses convenience API.

``Agent.create_response`` / ``Agent.get_response`` / ``Agent.add_tool`` are
frozen public API (per the R07.00 "public API is frozen" principle) but had
zero callers AND zero test coverage — flagged in the R07.00 dead-code audit.
These tests pin their basic contract so future refactors can't silently
break them.
"""

from agentkthx.agent import Agent
from agentkthx.core.openresponses import ResponseStatus


class _FakeBackend:
    backend_type = None
    api_mode = None

    def generate(self, messages, tools=None, **kw):
        return {"content": "ok", "tool_calls": [], "usage": {},
                "finish_reason": "stop"}


def _make_agent(**kw):
    return Agent(model="fake", backend=_FakeBackend(),
                 system_prompt="sys", tools=None, soul=None, **kw)


def test_create_response_defaults_to_queued():
    agent = _make_agent()
    response = agent.create_response()
    assert response.status == ResponseStatus.QUEUED
    assert response.model == "fake"
    assert response.input == []


def test_create_response_extends_input_items():
    agent = _make_agent()
    items = [
        {"type": "message", "role": "user", "content": "hello"},
        {"type": "function_call_output", "call_id": "c1", "output": "42"},
    ]
    response = agent.create_response(input_items=items)
    assert response.input == items


def test_create_response_with_previous_response_id_links_history():
    """create_response alone does not run the loop; it links context only
    after the previous response was stored (run() stores via
    _finalize_run). Verify the lookup path through get_response."""
    agent = _make_agent()

    # No history yet — previous_response_id must not explode
    response = agent.create_response(previous_response_id="nope")
    assert response.previous_response_id == "nope"

    # Store a response manually and verify context linking
    first = agent.create_response(
        input_items=[{"type": "message", "role": "user", "content": "hi"}])
    first.status = ResponseStatus.COMPLETED
    first.output = [{"type": "message", "role": "assistant", "content": "hello"}]
    agent._response_history[first.id] = first

    second = agent.create_response(previous_response_id=first.id)
    # previous input + output become context for the second response
    assert second.input == first.input + first.output
    assert agent.get_response(first.id) is first
    assert agent.get_response("missing") is None


def test_add_tool_registers_and_rebuilds_prompt():
    """add_tool: registry gains the tool, parser is rebuilt, system prompt
    gains a Tool Reference section."""
    from agentkthx.core.models import Tool, ToolParam

    agent = _make_agent()
    assert len(agent.tools) == 0
    assert "### Tool Reference" not in agent._custom_system_prompt

    calc = Tool(
        name="calculator",
        description="Evaluate a mathematical expression",
        params=[ToolParam(name="expression", type="string",
                          description="The expression", required=True)],
    )
    agent.add_tool(calc)

    assert "calculator" in agent.tools.names()
    assert "calculator" in agent._parser.tool_names
    assert "### Tool Reference" in agent._custom_system_prompt
