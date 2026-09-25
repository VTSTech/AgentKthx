"""
R06.59 regression tests for MAINT-04 Phase 3 helpers.

Pins the contracts of the three shared helpers extracted from the
duplicated tool-call-processing logic in _run_core and _run_core_streaming:

Phase 3a: _check_tool_choice_required(tool_calls) → (needs_tool, reason)
Phase 3b: _parse_tool_calls(content, native_tool_calls, response) → list[dict]
Phase 3c: _finalize_run(...) → AgentRun  +  _extract_last_final_answer(steps) → str

These tests verify each helper's contract in isolation, plus source-level
verification that both call sites actually use them.
"""

from __future__ import annotations

import inspect
import time
from unittest.mock import MagicMock

import pytest

from agentkthx.agent import Agent
from agentkthx.core.models import StepResult, StepResultType
from agentkthx.core.openresponses import ResponseStatus, ToolChoiceType


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_agent(tool_choice_type=ToolChoiceType.AUTO, tool_choice_name=None,
                debug=False):
    """Build a minimal Agent stub with the attrs the helpers touch.

    Note: ``_is_comp_mode`` is a read-only property on Agent that reads
    ``self.backend.api_mode``, so we stub the backend too.
    """
    agent = Agent.__new__(Agent)
    agent.debug = debug
    # Stub backend so _is_comp_mode property doesn't crash
    agent.backend = MagicMock()
    agent.backend.api_mode = None  # not OPENAI → _is_comp_mode returns False
    tc = MagicMock()
    tc.type = tool_choice_type
    tc.name = tool_choice_name
    agent.tool_choice = tc
    agent._response_history = {}
    return agent


def _make_response():
    """Build a minimal Response-like mock."""
    r = MagicMock()
    r.id = "test-resp-id"
    r.status = ResponseStatus.IN_PROGRESS
    r.usage = {}
    r.mark_completed = MagicMock()
    r.mark_failed = MagicMock()
    r.mark_incomplete = MagicMock()
    return r


# ===========================================================================
# Phase 3a: _check_tool_choice_required
# ===========================================================================

class TestCheckToolChoiceRequired:

    def test_auto_mode_never_needs_tool(self):
        """tool_choice=AUTO → always (False, "") regardless of tool_calls."""
        agent = _make_agent(tool_choice_type=ToolChoiceType.AUTO)
        assert agent._check_tool_choice_required(0) == (False, "")
        assert agent._check_tool_choice_required(5) == (False, "")

    def test_required_mode_needs_tool_when_zero_calls(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        needs, reason = agent._check_tool_choice_required(0)
        assert needs is True
        assert "required" in reason.lower()

    def test_required_mode_satisfied_when_calls_exist(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        needs, reason = agent._check_tool_choice_required(1)
        assert needs is False
        assert reason == ""

    def test_specific_mode_needs_named_tool_when_zero_calls(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.SPECIFIC,
                            tool_choice_name="calculator")
        needs, reason = agent._check_tool_choice_required(0)
        assert needs is True
        assert "calculator" in reason
        assert "specific" in reason.lower() or "requires" in reason.lower()

    def test_specific_mode_satisfied_when_calls_exist(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.SPECIFIC,
                            tool_choice_name="calculator")
        needs, _ = agent._check_tool_choice_required(1)
        assert needs is False


# ===========================================================================
# Phase 3b: _parse_tool_calls
# ===========================================================================

class TestParseToolCalls:

    def test_empty_content_and_no_native_returns_empty_list(self):
        agent = _make_agent()
        response = _make_response()
        result = agent._parse_tool_calls("", [], response)
        assert result == []

    def test_native_tool_calls_normalized_to_unified_shape(self):
        agent = _make_agent()
        response = _make_response()
        native = [
            {"name": "shell", "arguments": {"cmd": "ls"}, "id": "call_1"},
            {"name": "read_file", "arguments": {"path": "/tmp/x"}, "id": "call_2"},
        ]
        result = agent._parse_tool_calls("", native, response)
        assert len(result) == 2
        assert result[0] == {"name": "shell", "arguments": {"cmd": "ls"}, "id": "call_1"}
        assert result[1] == {"name": "read_file", "arguments": {"path": "/tmp/x"}, "id": "call_2"}

    def test_native_takes_precedence_over_content_parsing(self):
        """When native_tool_calls is non-empty, content is NOT parsed."""
        agent = _make_agent()
        response = _make_response()
        # If content were parsed, this would raise (parser is a MagicMock)
        native = [{"name": "shell", "arguments": {}, "id": "c1"}]
        result = agent._parse_tool_calls("some content", native, response)
        assert len(result) == 1
        assert result[0]["name"] == "shell"

    def test_react_parsed_calls_include_final_answer_field(self):
        """ReAct-parsed calls should include 'final_answer' (may be None)."""
        agent = _make_agent()
        response = _make_response()
        # Stub the parser to return a fake parsed call
        fake_call = MagicMock()
        fake_call.name = "calculator"
        fake_call.arguments = {"expr": "2+2"}
        fake_call.final_answer = "4"
        fake_call.thought = "I need to add 2 and 2"
        agent._parser = MagicMock()
        agent._parser.parse = MagicMock(return_value=[fake_call])

        result = agent._parse_tool_calls("Thought: ...", [], response)
        assert len(result) == 1
        assert result[0]["name"] == "calculator"
        assert result[0]["final_answer"] == "4"
        assert result[0]["id"] == ""  # ReAct calls have no id

    def test_react_call_with_thought_adds_reasoning_item(self):
        """When a parsed call has a 'thought', a ReasoningItem is added to
        the response's output items."""
        agent = _make_agent()
        response = _make_response()
        fake_call = MagicMock()
        fake_call.name = "search"
        fake_call.arguments = {"q": "test"}
        fake_call.final_answer = None
        fake_call.thought = "I should search for test"
        agent._parser = MagicMock(parse=MagicMock(return_value=[fake_call]))

        agent._parse_tool_calls("Thought: ...", [], response)
        # response.add_output_item should have been called with a ReasoningItem
        assert response.add_output_item.called

    def test_react_call_without_thought_does_not_add_reasoning_item(self):
        agent = _make_agent()
        response = _make_response()
        fake_call = MagicMock()
        fake_call.name = "search"
        fake_call.arguments = {"q": "test"}
        fake_call.final_answer = None
        fake_call.thought = None  # No thought
        agent._parser = MagicMock(parse=MagicMock(return_value=[fake_call]))

        agent._parse_tool_calls("Action: search", [], response)
        response.add_output_item.assert_not_called()


# ===========================================================================
# Phase 3c: _finalize_run + _extract_last_final_answer
# ===========================================================================

class TestFinalizeRun:

    def test_returns_agent_run_with_correct_fields(self):
        agent = _make_agent()
        agent._response_history = {}
        response = _make_response()
        start = time.time()

        result = agent._finalize_run(
            final_answer="42",
            steps=[MagicMock()],
            total_tokens=100,
            start_time=start,
            tool_calls=3,
            response=response,
            success=True,
        )
        assert result.final_answer == "42"
        assert result.total_tokens == 100
        assert result.tool_calls == 3
        assert result.success is True
        assert result.total_ms > 0

    def test_stores_response_in_history(self):
        """_finalize_run must store the response in _response_history for
        previous_response_id support."""
        agent = _make_agent()
        agent._response_history = {}
        response = _make_response()

        agent._finalize_run(
            final_answer="x", steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0, response=response,
        )
        assert response.id in agent._response_history
        assert agent._response_history[response.id] is response

    def test_sets_total_tokens_on_response_usage(self):
        agent = _make_agent()
        agent._response_history = {}
        response = _make_response()

        agent._finalize_run(
            final_answer="x", steps=[], total_tokens=42,
            start_time=time.time(), tool_calls=0, response=response,
        )
        assert response.usage["total_tokens"] == 42

    def test_mark_completed_true_marks_in_progress_response(self):
        agent = _make_agent()
        agent._response_history = {}
        response = _make_response()
        # response.status is IN_PROGRESS by default (from _make_response)

        agent._finalize_run(
            final_answer="x", steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0, response=response,
            mark_completed=True,
        )
        response.mark_completed.assert_called_once()

    def test_mark_completed_false_does_not_mark(self):
        """When mark_completed=False (e.g. terminated runs), the response
        is NOT marked completed — it stays in its current (failed) state."""
        agent = _make_agent()
        agent._response_history = {}
        response = _make_response()

        agent._finalize_run(
            final_answer="", steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0, response=response,
            success=False,
            mark_completed=False,
        )
        response.mark_completed.assert_not_called()


class TestExtractLastFinalAnswer:

    def test_empty_steps_returns_empty_string(self):
        assert Agent._extract_last_final_answer([]) == ""

    def test_no_final_answer_step_returns_empty_string(self):
        steps = [
            StepResult(type=StepResultType.TOOL_CALL, content="x"),
            StepResult(type=StepResultType.ERROR, error="err"),
        ]
        assert Agent._extract_last_final_answer(steps) == ""

    def test_returns_last_final_answer_content(self):
        steps = [
            StepResult(type=StepResultType.FINAL_ANSWER, content="first"),
            StepResult(type=StepResultType.FINAL_ANSWER, content="second"),
        ]
        assert Agent._extract_last_final_answer(steps) == "second"

    def test_returns_empty_string_for_none_content(self):
        """A FINAL_ANSWER step with content=None should return "" not None."""
        steps = [StepResult(type=StepResultType.FINAL_ANSWER, content=None)]
        assert Agent._extract_last_final_answer(steps) == ""


# ===========================================================================
# Source-level verification: both call sites use the helpers
# ===========================================================================

class TestCallSitesUseHelpers:

    def test_run_core_uses_all_three_helpers(self):
        # R07.00 Phase 5: loop body lives in AgenticLoopMixin._run_loop_iteration
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_check_tool_choice_required" in src
        assert "_parse_tool_calls" in src
        assert "_finalize_run" in src

    def test_run_core_streaming_uses_all_three_helpers(self):
        # R07.00 Phase 5: single unified loop + streaming wrapper delegation
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_check_tool_choice_required" in src
        assert "_parse_tool_calls" in src
        assert "_finalize_run" in src
        wrapper_src = inspect.getsource(Agent._run_core_streaming)
        assert "_run_loop_iteration" in wrapper_src

    def test_no_remaining_duplicated_finalize_blocks_in_run_core(self):
        """_run_core should NOT contain the old duplicated 7-line finalize
        block — it should only call _finalize_run."""
        src = inspect.getsource(Agent._run_core)
        # The old pattern had multiple 'return AgentRun(' calls. Now there
        # should be ZERO direct AgentRun() constructor calls in _run_core
        # (all go through _finalize_run).
        assert "return AgentRun(" not in src, (
            "_run_core still has a direct 'return AgentRun(' call — the "
            "Phase 3c refactor missed an exit point"
        )

    def test_no_remaining_duplicated_finalize_blocks_in_streaming(self):
        src = inspect.getsource(Agent._run_core_streaming)
        assert "return AgentRun(" not in src, (
            "_run_core_streaming still has a direct 'return AgentRun(' call"
        )
