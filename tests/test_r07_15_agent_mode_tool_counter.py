"""R07.15 regression: agent mode's verbose footer always reported
"0 tool calls" — even while tool calls were rendering in the transcript
(``[1] tool shell {...}`` etc.).

Root cause (spotted during the 2026-09-29 VM smoke test of R07.15):
``agent_mode.py`` compared ``step.type`` against the STRING
``"tool_call"`` — but ``StepResult.type`` is a ``StepResultType`` ENUM
member, so the comparison was always False. It also collected names via
``getattr(s, 'tool_name', '')`` when the actual field is
``step.tool_call.name``. The footer therefore never counted a tool call
and never printed the "🔧 Tools used:" line.

Fix: ``_step_tool_stats()`` helper using the same pattern as
``cli/utils.py``'s step summary — ``step.type == StepResultType.TOOL_CALL``
with the name from ``step.tool_call.name``, deduplicated in first-seen
order.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.agent_mode import _step_tool_stats
from agentkthx.core.models import AgentRun, StepResult, ToolCall
from agentkthx.core.types import StepResultType


def _tool(name: str, args: dict | None = None) -> StepResult:
    return StepResult(
        type=StepResultType.TOOL_CALL,
        content="ok",
        tool_call=ToolCall(name=name, arguments=args or {}),
        tool_result="result",
    )


class _FakeRun:
    """Duck-typed AgentRun — only the fields the helper reads."""

    def __init__(self, steps):
        self.steps = steps


class TestStepToolStats:
    def test_counts_tool_call_steps(self):
        run = _FakeRun(
            [
                _tool("shell", {"command": "uname -r"}),
                _tool("shell", {"command": "free -h"}),
                StepResult(type=StepResultType.FINAL_ANSWER, content="done"),
            ]
        )
        count, names = _step_tool_stats(run)
        assert count == 2
        assert names == ["shell"]

    def test_names_deduped_first_seen_order(self):
        run = _FakeRun(
            [
                _tool("shell"),
                _tool("calculator"),
                _tool("shell"),
                StepResult(type=StepResultType.FINAL_ANSWER, content="done"),
            ]
        )
        count, names = _step_tool_stats(run)
        assert count == 3
        assert names == ["shell", "calculator"]

    def test_zero_when_no_tools(self):
        run = _FakeRun([StepResult(type=StepResultType.FINAL_ANSWER, content="answer")])
        count, names = _step_tool_stats(run)
        assert count == 0
        assert names == []

    def test_tool_call_without_tool_call_field_is_counted_but_unnamed(self):
        """A TOOL_CALL step with tool_call=None still counts (it happened),
        but contributes no name — matches the old loop's shape except the
        count now actually works."""
        run = _FakeRun(
            [
                StepResult(type=StepResultType.TOOL_CALL, content="no field"),
                _tool("shell"),
            ]
        )
        count, names = _step_tool_stats(run)
        assert count == 2
        assert names == ["shell"]

    def test_works_on_real_agent_run(self):
        """Same helper must accept the real AgentRun dataclass."""
        run = AgentRun(
            final_answer="done",
            steps=[
                _tool("web_search"),
                StepResult(type=StepResultType.FINAL_ANSWER, content="done"),
            ],
            total_tokens=10,
            total_ms=123.0,
        )
        count, names = _step_tool_stats(run)
        assert count == 1
        assert names == ["web_search"]

    def test_string_comparison_regressions_gone(self):
        """Pin the exact bug: no string 'tool_call' comparison and no
        nonexistent tool_name attribute reads may return to agent_mode."""
        import agentkthx.agent_mode as am

        src = inspect.getsource(am)
        assert 's.type == "tool_call"' not in src
        assert "getattr(s, 'tool_name'" not in src
        assert "StepResultType.TOOL_CALL" in src
