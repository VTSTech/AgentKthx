"""
R06.59 regression tests for _generate_with_retry (MAINT-04 Phase 1).

Pins the contract of the shared API-resilience retry loop that was extracted
from _run_core and _run_core_streaming. These tests verify:

1. Happy path: generate_fn succeeds on first call → returns (response, False).
2. Transient error then success: one retry then success → returns (response, False).
3. Terminal error (non-transient): no retry → returns (None, True).
4. Exhausted retries: max_api_retries+1 transient failures → returns (None, True).
5. Context-length 400 with compaction recovery enabled: compaction runs,
   retry succeeds → returns (response, False).
6. Context-length 400 with compaction recovery DISABLED: falls through to
   transient path (since context-length errors aren't transient), terminates.
7. KeyboardInterrupt always re-raises, never swallowed.
8. Both _run_core and _run_core_streaming actually use _generate_with_retry
   (so a future refactor can't silently bypass it).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from agentkthx.agent import Agent
from agentkthx.core.models import StepResult, StepResultType


def _make_agent(max_api_retries: int = 3):
    """Build a minimal Agent with the retry-related attrs stubbed."""
    agent = Agent.__new__(Agent)
    agent.max_api_retries = max_api_retries
    agent.debug = False
    agent.memory = MagicMock()
    agent.memory.compact_messages = MagicMock(return_value=0)
    agent._snapshot_running_tokens = MagicMock()
    agent._compaction_threshold = 0.85
    agent.num_ctx = 8192
    agent._running_tokens_in = 0
    agent._running_tokens_out = 0
    return agent


def _make_response():
    """Build a minimal Response-like mock."""
    r = MagicMock()
    r.mark_failed = MagicMock()
    return r


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------

def test_happy_path_returns_response_no_termination():
    agent = _make_agent()
    response = _make_response()
    expected = {"content": "hello", "tool_calls": []}
    gen_fn = MagicMock(return_value=expected)

    result, terminated = agent._generate_with_retry(
        gen_fn, step_num=0, steps=[], response=response,
        enable_compaction_recovery=False,
    )
    assert result == expected
    assert terminated is False
    assert gen_fn.call_count == 1
    response.mark_failed.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Transient error then success
# ---------------------------------------------------------------------------

def test_transient_error_then_success_retries_once():
    agent = _make_agent()
    response = _make_response()
    expected = {"content": "ok"}
    # First call raises a transient error, second succeeds
    gen_fn = MagicMock(side_effect=[
        ConnectionError("connection reset"),
        expected,
    ])

    with patch('agentkthx.agent.is_transient_api_error', return_value=True):
        with patch('agentkthx.agent.backoff_delay', return_value=0.01):
            with patch('agentkthx.agent.time.sleep'):
                result, terminated = agent._generate_with_retry(
                    gen_fn, step_num=0, steps=[], response=response,
                    enable_compaction_recovery=False,
                )
    assert result == expected
    assert terminated is False
    assert gen_fn.call_count == 2


# ---------------------------------------------------------------------------
# 3. Terminal (non-transient) error → no retry, terminate
# ---------------------------------------------------------------------------

def test_non_transient_error_terminates_without_retry():
    agent = _make_agent()
    response = _make_response()
    err = ValueError("bad request — not transient")
    gen_fn = MagicMock(side_effect=err)

    with patch('agentkthx.agent.is_transient_api_error', return_value=False):
        with patch('agentkthx.agent.describe_terminal') as mock_describe:
            result, terminated = agent._generate_with_retry(
                gen_fn, step_num=0, steps=[], response=response,
                enable_compaction_recovery=False,
            )
    assert result is None
    assert terminated is True
    assert gen_fn.call_count == 1  # no retry
    response.mark_failed.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Exhausted retries
# ---------------------------------------------------------------------------

def test_exhausted_retries_terminates():
    agent = _make_agent(max_api_retries=3)
    response = _make_response()
    err = ConnectionError("persistent connection issue")
    gen_fn = MagicMock(side_effect=err)

    with patch('agentkthx.agent.is_transient_api_error', return_value=True):
        with patch('agentkthx.agent.backoff_delay', return_value=0.001):
            with patch('agentkthx.agent.time.sleep'):
                with patch('agentkthx.agent.describe_terminal'):
                    result, terminated = agent._generate_with_retry(
                        gen_fn, step_num=0, steps=[], response=response,
                        enable_compaction_recovery=False,
                    )
    assert result is None
    assert terminated is True
    # Should have tried max_api_retries + 1 times (initial + retries)
    assert gen_fn.call_count == agent.max_api_retries + 1


# ---------------------------------------------------------------------------
# 5. Context-length 400 + compaction recovery ENABLED → compaction runs, retry succeeds
# ---------------------------------------------------------------------------

def test_context_length_400_with_compaction_recovery_compacts_and_retries():
    agent = _make_agent()
    response = _make_response()
    # First call: context-length error. Second call: success.
    ctx_err = Exception("context length exceeded (12345 > 8192)")
    expected = {"content": "ok after compaction"}
    gen_fn = MagicMock(side_effect=[ctx_err, expected])
    # Compaction reports it freed 5 messages
    agent.memory.compact_messages = MagicMock(return_value=5)

    with patch('agentkthx.agent.is_transient_api_error', return_value=False):
        result, terminated = agent._generate_with_retry(
            gen_fn, step_num=0, steps=[], response=response,
            enable_compaction_recovery=True,
        )
    assert result == expected
    assert terminated is False
    assert gen_fn.call_count == 2
    # Compaction must have been called with keep_count=10
    agent.memory.compact_messages.assert_called_once_with(keep_count=10)
    # Snapshot must have been refreshed after compaction
    agent._snapshot_running_tokens.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Context-length 400 + compaction recovery DISABLED → no compaction, terminate
# ---------------------------------------------------------------------------

def test_context_length_400_without_compaction_recovery_does_not_compact():
    """Non-streaming path must NOT trigger compaction on context-length 400.
    The handler is streaming-only (enable_compaction_recovery=True)."""
    agent = _make_agent()
    response = _make_response()
    ctx_err = Exception("context length exceeded")
    gen_fn = MagicMock(side_effect=ctx_err)
    agent.memory.compact_messages = MagicMock(return_value=5)

    with patch('agentkthx.agent.is_transient_api_error', return_value=False):
        result, terminated = agent._generate_with_retry(
            gen_fn, step_num=0, steps=[], response=response,
            enable_compaction_recovery=False,
        )
    assert result is None
    assert terminated is True
    # Compaction must NOT have been called
    agent.memory.compact_messages.assert_not_called()


# ---------------------------------------------------------------------------
# 7. KeyboardInterrupt re-raises
# ---------------------------------------------------------------------------

def test_keyboard_interrupt_propagates():
    agent = _make_agent()
    response = _make_response()
    gen_fn = MagicMock(side_effect=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        agent._generate_with_retry(
            gen_fn, step_num=0, steps=[], response=response,
            enable_compaction_recovery=False,
        )
    assert gen_fn.call_count == 1


# ---------------------------------------------------------------------------
# 8. Both _run_core and _run_core_streaming call _generate_with_retry
# ---------------------------------------------------------------------------

def test_run_core_uses_generate_with_retry():
    """The agentic loop's source must call self._generate_with_retry (so a
    future refactor can't silently bypass the shared retry loop).
    R07.00 Phase 5: the loop body lives in AgenticLoopMixin._run_loop_iteration;
    the per-path enable_compaction_recovery flag is set by the thin wrappers."""
    src = inspect.getsource(Agent._run_loop_iteration)
    assert "_generate_with_retry" in src, (
        "the agentic loop no longer calls _generate_with_retry — the MAINT-04 "
        "Phase 1 refactor was reverted or bypassed"
    )
    # Non-streaming wrapper must pass enable_compaction_recovery=False
    wrapper_src = inspect.getsource(Agent._run_core)
    assert "enable_compaction_recovery=False" in wrapper_src


def test_run_core_streaming_uses_generate_with_retry():
    """The streaming wrapper must delegate with
    enable_compaction_recovery=True (so the context-length compaction
    handler still fires on long streaming runs)."""
    src = inspect.getsource(Agent._run_core_streaming)
    assert "_generate_stream" in src, (
        "_run_core_streaming no longer uses the streaming generator"
    )
    assert "enable_compaction_recovery=True" in src
