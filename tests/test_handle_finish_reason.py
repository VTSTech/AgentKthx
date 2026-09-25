"""
R06.59 regression tests for _handle_finish_reason (MAINT-04 Phase 2).

Pins the contract of the shared finish_reason handler extracted from both
_run_core and _run_core_streaming. These tests verify:

1. finish_reason="stop" → returns False (caller continues).
2. finish_reason="length" → marks response incomplete, appends MAX_STEPS
   step, returns True (caller breaks).
3. finish_reason="content_filter" → marks response failed, appends ERROR
   step, returns True (caller breaks).
4. Unknown finish_reason → returns False (caller continues, same as "stop").
5. Both _run_core and _run_core_streaming call _handle_finish_reason (so a
   future refactor can't silently bypass the shared helper).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from agentkthx.agent import Agent
from agentkthx.core.models import StepResult, StepResultType


def _make_agent(debug: bool = False):
    """Build a minimal Agent with the attrs _handle_finish_reason touches."""
    agent = Agent.__new__(Agent)
    agent.debug = debug
    return agent


def _make_response():
    """Build a minimal Response-like mock."""
    r = MagicMock()
    r.mark_incomplete = MagicMock()
    r.mark_failed = MagicMock()
    return r


# ---------------------------------------------------------------------------
# 1. "stop" → continue
# ---------------------------------------------------------------------------

def test_stop_finish_reason_returns_false():
    agent = _make_agent()
    response = _make_response()
    gen_response = {"_finish_reason": "stop", "usage": {"total_tokens": 100}}

    should_break = agent._handle_finish_reason(gen_response, [], response)
    assert should_break is False
    response.mark_incomplete.assert_not_called()
    response.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# 2. "length" → mark incomplete, return True
# ---------------------------------------------------------------------------

def test_length_finish_reason_marks_incomplete_and_breaks():
    agent = _make_agent()
    response = _make_response()
    steps = []
    gen_response = {"_finish_reason": "length", "usage": {"total_tokens": 5000}}

    should_break = agent._handle_finish_reason(gen_response, steps, response)
    assert should_break is True
    response.mark_incomplete.assert_called_once()
    response.mark_failed.assert_not_called()
    # Should have appended a MAX_STEPS step
    assert len(steps) == 1
    assert steps[0].type == StepResultType.MAX_STEPS
    assert steps[0].tokens_used == 5000
    assert "truncated" in steps[0].content.lower()


# ---------------------------------------------------------------------------
# 3. "content_filter" → mark failed, return True
# ---------------------------------------------------------------------------

def test_content_filter_finish_reason_marks_failed_and_breaks():
    agent = _make_agent()
    response = _make_response()
    steps = []
    gen_response = {"_finish_reason": "content_filter", "usage": {"total_tokens": 50}}

    should_break = agent._handle_finish_reason(gen_response, steps, response)
    assert should_break is True
    response.mark_failed.assert_called_once()
    response.mark_incomplete.assert_not_called()
    assert len(steps) == 1
    assert steps[0].type == StepResultType.ERROR
    assert steps[0].tokens_used == 50
    assert "content filter" in steps[0].error.lower() or "filtered" in steps[0].error.lower()


# ---------------------------------------------------------------------------
# 4. Unknown finish_reason → continue (same as "stop")
# ---------------------------------------------------------------------------

def test_unknown_finish_reason_returns_false():
    agent = _make_agent()
    response = _make_response()
    gen_response = {"_finish_reason": "unknown_reason", "usage": {"total_tokens": 10}}

    should_break = agent._handle_finish_reason(gen_response, [], response)
    assert should_break is False
    response.mark_incomplete.assert_not_called()
    response.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Missing finish_reason → defaults to "stop" → continue
# ---------------------------------------------------------------------------

def test_missing_finish_reason_defaults_to_stop():
    agent = _make_agent()
    response = _make_response()
    gen_response = {"usage": {"total_tokens": 10}}  # no _finish_reason key

    should_break = agent._handle_finish_reason(gen_response, [], response)
    assert should_break is False


# ---------------------------------------------------------------------------
# 6. Both call sites actually use the helper
# ---------------------------------------------------------------------------

def test_run_core_uses_handle_finish_reason():
    """The agentic loop's source must call self._handle_finish_reason.
    R07.00 Phase 5: the loop body lives in AgenticLoopMixin._run_loop_iteration."""
    src = inspect.getsource(Agent._run_loop_iteration)
    assert "_handle_finish_reason" in src, (
        "the agentic loop no longer calls _handle_finish_reason — the MAINT-04 "
        "Phase 2 refactor was reverted or bypassed"
    )


def test_run_core_streaming_uses_handle_finish_reason():
    """The unified agentic loop covers the streaming path too — same
    assertion target as the non-streaming test (single loop since R07.00
    Phase 5), plus the streaming wrapper must delegate to it."""
    src = inspect.getsource(Agent._run_loop_iteration)
    assert "_handle_finish_reason" in src
    wrapper_src = inspect.getsource(Agent._run_core_streaming)
    assert "_run_loop_iteration" in wrapper_src


# ---------------------------------------------------------------------------
# 7. Debug output — verify the debug print fires when debug=True
# ---------------------------------------------------------------------------

def test_length_finish_reason_emits_debug_when_debug_true(capsys):
    agent = _make_agent(debug=True)
    response = _make_response()
    gen_response = {"_finish_reason": "length", "usage": {"total_tokens": 100}}

    agent._handle_finish_reason(gen_response, [], response)
    captured = capsys.readouterr().out
    assert "finish_reason='length'" in captured


def test_length_finish_reason_silent_when_debug_false(capsys):
    agent = _make_agent(debug=False)
    response = _make_response()
    gen_response = {"_finish_reason": "length", "usage": {"total_tokens": 100}}

    agent._handle_finish_reason(gen_response, [], response)
    captured = capsys.readouterr().out
    assert "finish_reason" not in captured
