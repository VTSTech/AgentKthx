"""
R06.59 regression tests for MAINT-04 Phase 4 helpers.

Pins the contracts of the three shared helpers extracted in Phase 4:

Phase 4a: _enforce_final_answer(...) → AgentRun
Phase 4b: _handle_blocked_tool_call(...) → (was_blocked, should_terminate)
Phase 4c: _reject_for_tool_choice(content, is_final_answer_context, include_format_hint) → None

Plus source-level verification that all call sites use the helpers and
no duplicated blocks remain.
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
# Helper factories (shared with test_maint04_phase3_helpers.py pattern)
# ---------------------------------------------------------------------------

def _make_agent(tool_choice_type=ToolChoiceType.AUTO, tool_choice_name=None,
                debug=False):
    agent = Agent.__new__(Agent)
    agent.debug = debug
    agent.backend = MagicMock()
    agent.backend.api_mode = None
    tc = MagicMock()
    tc.type = tool_choice_type
    tc.name = tool_choice_name
    agent.tool_choice = tc
    agent._response_history = {}
    agent._error_tracker = MagicMock()
    agent._error_tracker.should_terminate = MagicMock(return_value=False)
    agent._error_tracker.format_repeat_block = MagicMock(return_value="blocked: repeat")
    agent._error_tracker.record_failure = MagicMock()
    agent.memory = MagicMock()
    return agent


def _make_response():
    r = MagicMock()
    r.id = "test-resp-id"
    r.status = ResponseStatus.IN_PROGRESS
    r.usage = {}
    r.mark_completed = MagicMock()
    r.mark_failed = MagicMock()
    return r


# ===========================================================================
# Phase 4a: _enforce_final_answer
# ===========================================================================

class TestEnforceFinalAnswer:

    def test_returns_agent_run_with_last_successful_result(self):
        """_enforce_final_answer should use _last_successful_result as the
        final_answer, NOT the model's potentially-wrong answer."""
        agent = _make_agent()
        response = _make_response()
        result = agent._enforce_final_answer(
            _last_successful_result="42",
            tokens=100,
            reasoning_content="",
            steps=[],
            total_tokens=100,
            start_time=time.time(),
            tool_calls=2,
            response=response,
        )
        assert result.final_answer == "42"
        assert result.success is True

    def test_appends_final_answer_step_to_steps(self):
        agent = _make_agent()
        response = _make_response()
        steps = []
        agent._enforce_final_answer(
            _last_successful_result="42",
            tokens=100,
            reasoning_content="thinking...",
            steps=steps,
            total_tokens=100,
            start_time=time.time(),
            tool_calls=0,
            response=response,
        )
        assert len(steps) == 1
        assert steps[0].type == StepResultType.FINAL_ANSWER
        assert steps[0].content == "42"

    def test_stores_response_in_history(self):
        agent = _make_agent()
        response = _make_response()
        agent._enforce_final_answer(
            _last_successful_result="42",
            tokens=0, reasoning_content="",
            steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0,
            response=response,
        )
        assert response.id in agent._response_history

    def test_debug_context_emits_print_when_debug_true(self, capsys):
        agent = _make_agent(debug=True)
        response = _make_response()
        agent._enforce_final_answer(
            _last_successful_result="42",
            tokens=0, reasoning_content="",
            steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0,
            response=response,
            debug_context="Model tried to call tools",
        )
        captured = capsys.readouterr().out
        assert "FINAL ANSWER ENFORCEMENT" in captured
        assert "Model tried to call tools" in captured

    def test_empty_debug_context_suppresses_print(self, capsys):
        """When debug_context="" (streaming path), no debug print fires."""
        agent = _make_agent(debug=True)
        response = _make_response()
        agent._enforce_final_answer(
            _last_successful_result="42",
            tokens=0, reasoning_content="",
            steps=[], total_tokens=0,
            start_time=time.time(), tool_calls=0,
            response=response,
            debug_context="",  # streaming path
        )
        captured = capsys.readouterr().out
        assert "FINAL ANSWER ENFORCEMENT" not in captured


# ===========================================================================
# Phase 4b: _handle_blocked_tool_call
# ===========================================================================

class TestHandleBlockedToolCall:

    def test_returns_was_blocked_true(self):
        agent = _make_agent()
        response = _make_response()
        was_blocked, should_term = agent._handle_blocked_tool_call(
            tool_name="shell",
            tool_args={"cmd": "rm -rf /"},
            tool_call_id="call_1",
            native_tool_calls=[{"name": "shell"}],
            step_num=0,
            tool_calls=0,
            tokens=50,
            steps=[],
            response=response,
        )
        assert was_blocked is True

    def test_records_failure_on_error_tracker(self):
        """A blocked call must count as a failure so the consecutive
        counter increments — otherwise a stubborn model could loop at
        max_steps."""
        agent = _make_agent()
        response = _make_response()
        agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={"cmd": "x"},
            tool_call_id="c1", native_tool_calls=[],
            step_num=0, tool_calls=0, tokens=10,
            steps=[], response=response,
        )
        agent._error_tracker.record_failure.assert_called_once()

    def test_appends_error_step(self):
        agent = _make_agent()
        response = _make_response()
        steps = []
        agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={"cmd": "x"},
            tool_call_id="c1", native_tool_calls=[],
            step_num=0, tool_calls=0, tokens=10,
            steps=steps, response=response,
        )
        assert len(steps) == 1
        assert steps[0].type == StepResultType.ERROR

    def test_native_calls_use_add_tool_result(self):
        agent = _make_agent()
        response = _make_response()
        agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={},
            tool_call_id="c1",
            native_tool_calls=[{"name": "shell"}],  # truthy
            step_num=0, tool_calls=0, tokens=10,
            steps=[], response=response,
        )
        agent.memory.add_tool_result.assert_called_once()

    def test_react_calls_use_add_user_observation(self):
        agent = _make_agent()
        response = _make_response()
        agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={},
            tool_call_id="c1",
            native_tool_calls=[],  # falsy → ReAct path
            step_num=0, tool_calls=0, tokens=10,
            steps=[], response=response,
        )
        agent.memory.add.assert_called_once_with("user", "Observation: blocked: repeat")

    def test_should_terminate_true_when_tracker_says_so(self):
        agent = _make_agent()
        agent._error_tracker.should_terminate = MagicMock(return_value=True)
        response = _make_response()
        _, should_term = agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={},
            tool_call_id="c1", native_tool_calls=[],
            step_num=0, tool_calls=0, tokens=10,
            steps=[], response=response,
        )
        assert should_term is True
        response.mark_failed.assert_called_once()

    def test_should_terminate_false_normal_case(self):
        agent = _make_agent()
        response = _make_response()
        _, should_term = agent._handle_blocked_tool_call(
            tool_name="shell", tool_args={},
            tool_call_id="c1", native_tool_calls=[],
            step_num=0, tool_calls=0, tokens=10,
            steps=[], response=response,
        )
        assert should_term is False


# ===========================================================================
# Phase 4c: _reject_for_tool_choice
# ===========================================================================

class TestRejectForToolChoice:

    def test_adds_assistant_message_to_memory(self):
        agent = _make_agent()
        agent._reject_for_tool_choice("I will answer directly")
        agent.memory.add.assert_any_call("assistant", "I will answer directly")

    def test_specific_tool_choice_mentions_tool_name(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.SPECIFIC,
                            tool_choice_name="calculator")
        agent._reject_for_tool_choice("some content")
        # The user message should mention "calculator"
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert any("calculator" in msg for msg in calls)

    def test_required_tool_choice_says_at_least_one_tool(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        agent._reject_for_tool_choice("content")
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert any("at least one tool" in msg for msg in calls)

    def test_final_answer_context_adds_qualifier(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        agent._reject_for_tool_choice("content", is_final_answer_context=True)
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert any("before providing a final answer" in msg for msg in calls)

    def test_no_final_answer_context_omits_qualifier(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        agent._reject_for_tool_choice("content", is_final_answer_context=False)
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert all("before providing a final answer" not in msg for msg in calls)

    def test_include_format_hint_true_adds_action_format(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        agent._reject_for_tool_choice("content", include_format_hint=True)
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert any("Action/Action Input" in msg for msg in calls)

    def test_include_format_hint_false_omits_action_format(self):
        agent = _make_agent(tool_choice_type=ToolChoiceType.REQUIRED)
        agent._reject_for_tool_choice("content", include_format_hint=False)
        calls = [c.args[1] for c in agent.memory.add.call_args_list if c.args[0] == "user"]
        assert all("Action/Action Input" not in msg for msg in calls)


# ===========================================================================
# Source-level verification: no duplicated blocks remain
# ===========================================================================

class TestNoRemainingDuplication:

    def test_run_core_uses_enforce_final_answer(self):
        # R07.00 Phase 5: loop body lives in AgenticLoopMixin._run_loop_iteration
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_enforce_final_answer" in src

    def test_run_core_streaming_uses_enforce_final_answer(self):
        # R07.00 Phase 5: single unified loop + streaming wrapper delegation
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_enforce_final_answer" in src
        wrapper_src = inspect.getsource(Agent._run_core_streaming)
        assert "_run_loop_iteration" in wrapper_src

    def test_run_core_uses_handle_blocked_tool_call(self):
        # R07.00 Phase 5: per-call dispatch lives in _execute_single_tool_call
        src = inspect.getsource(Agent._execute_single_tool_call)
        assert "_handle_blocked_tool_call" in src

    def test_run_core_streaming_uses_handle_blocked_tool_call(self):
        # R07.00 Phase 5: single unified dispatch covers both paths
        src = inspect.getsource(Agent._execute_single_tool_call)
        assert "_handle_blocked_tool_call" in src

    def test_run_core_uses_reject_for_tool_choice(self):
        # R07.00 Phase 5: loop body lives in AgenticLoopMixin._run_loop_iteration
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_reject_for_tool_choice" in src

    def test_run_core_streaming_uses_reject_for_tool_choice(self):
        # R07.00 Phase 5: single unified loop; per-path format hint via callbacks
        src = inspect.getsource(Agent._run_loop_iteration)
        assert "_reject_for_tool_choice" in src
        assert "include_format_hint=callbacks.include_format_hint" in src

    def test_no_remaining_duplicated_enforce_blocks_in_run_core(self):
        """_run_core should NOT contain the old duplicated 'FINAL ANSWWER
        ENFORCEMENT' inline block — only calls to _enforce_final_answer."""
        src = inspect.getsource(Agent._run_core)
        assert "FINAL ANSWWER ENFORCEMENT" not in src, (
            "_run_core still has the old inline 'FINAL ANSWWER ENFORCEMENT' "
            "block — Phase 4a missed an instance"
        )

    def test_no_remaining_duplicated_enforce_blocks_in_streaming(self):
        src = inspect.getsource(Agent._run_core_streaming)
        assert "FINAL ANSWWER ENFORCEMENT" not in src

    def test_no_remaining_duplicated_blocked_msg_blocks_in_run_core(self):
        """_run_core should NOT contain the old 'format_repeat_block' inline
        block — only calls to _handle_blocked_tool_call."""
        src = inspect.getsource(Agent._run_core)
        # The helper itself has this call, but the methods should not
        # (they delegate to the helper)
        assert "self._error_tracker.format_repeat_block" not in src, (
            "_run_core still calls format_repeat_block directly — Phase 4b "
            "missed an instance"
        )

    def test_no_remaining_duplicated_blocked_msg_blocks_in_streaming(self):
        src = inspect.getsource(Agent._run_core_streaming)
        assert "self._error_tracker.format_repeat_block" not in src

    def test_no_remaining_duplicated_rejection_messages_in_run_core(self):
        """_run_core should NOT contain inline 'You must use the' messages —
        only calls to _reject_for_tool_choice."""
        src = inspect.getsource(Agent._run_core)
        assert "You must use the '{self.tool_choice.name}'" not in src, (
            "_run_core still has inline rejection messages — Phase 4c "
            "missed an instance"
        )

    def test_no_remaining_duplicated_rejection_messages_in_streaming(self):
        src = inspect.getsource(Agent._run_core_streaming)
        assert "You must use the '{self.tool_choice.name}'" not in src
