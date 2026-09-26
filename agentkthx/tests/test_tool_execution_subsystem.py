"""R07.00 Phase 10 — ToolExecutionMixin extraction tests.

Verifies the structural move of ``_execute_tool`` from ``agent.py`` to
``agentkthx/core/tool_execution.py`` and the behavior that must survive
the move verbatim: registry lookup, dangerous-tool gate, numeric-string
coercion, hallucinated-param stripping, error formatting.
"""

from agentkthx.core.tool_execution import ToolExecutionMixin
from agentkthx.core.models import Tool, ToolParam


def _make_tool(name="echo", params=None, dangerous=False, fn=None):
    def execute(**kwargs):
        if fn:
            return fn(**kwargs)
        return "ok:" + ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    tool = Tool(name=name, description="test tool", params=params or [])
    tool.execute = execute
    if dangerous:
        tool.dangerous = True
    return tool


class _Host(ToolExecutionMixin):
    """Minimal host: a dict-backed registry + confirmation callback."""

    def __init__(self, tools=(), confirm=None):
        self._tools = {t.name: t for t in tools}
        self._confirm_dangerous = confirm

    class tools:
        pass

    def _registry(self):
        return self

    def get(self, name):  # NOT used — registry protocol below
        raise AssertionError

    # emulate ToolRegistry surface used by the mixin
    class _Reg:
        def __init__(self, d):
            self._d = d
        def get(self, name):
            return self._d.get(name)
        def names(self):
            return list(self._d)
    @property
    def tools(self):
        return _Host._Reg(self._tools)


def test_mixin_provides_method_and_agent_inherits():
    assert callable(getattr(ToolExecutionMixin, "_execute_tool", None))
    from agentkthx.agent import Agent
    assert issubclass(Agent, ToolExecutionMixin)
    assert "_execute_tool" not in vars(Agent), (
        "Agent.__dict__ still contains _execute_tool — extraction left a "
        "shadowing copy behind")


def test_unknown_tool_returns_error_string_with_available_names():
    host = _Host(tools=[_make_tool("calculator")])
    result = host._execute_tool("nonexistent", {})
    assert isinstance(result, str) and result.startswith("Error: Unknown tool")
    assert "calculator" in result  # tells the model what IS available


def test_dangerous_tool_confirmation_gate():
    calls = []
    def confirm(name, args):
        calls.append((name, args))
        return False
    host = _Host(tools=[_make_tool("nuke", dangerous=True)], confirm=confirm)
    result = host._execute_tool("nuke", {"target": "x"})
    assert "blocked by user confirmation" in result
    assert calls == [("nuke", {"target": "x"})]


def test_numeric_string_coercion_and_hallucinated_param_stripping():
    t = _make_tool("depth_tool", params=[ToolParam(name="depth", type="integer",
                                                   description="d")])
    host = _Host(tools=[t])
    result = host._execute_tool("depth_tool", {"depth": "3", "bogus": "x"})
    # depth coerced to int 3; bogus stripped and reported back
    assert "depth=3" in result
    assert "bogus" in result and "ignored unknown parameter" in result


def test_execution_exception_returns_error_string_not_raise():
    def boom(**kw):
        raise RuntimeError("kaboom")
    host = _Host(tools=[_make_tool("exploder", fn=boom)])
    result = host._execute_tool("exploder", {})
    assert isinstance(result, str) and "Error executing tool: kaboom" in result
