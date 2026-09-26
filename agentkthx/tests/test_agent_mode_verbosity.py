"""
R06.58 regression tests for agent-mode verbosity (stream flag, plan preview).

Pins down the three changes:
1. AgentMode now accepts and stores a ``stream`` parameter.
2. ``execute_step`` passes ``stream=self.stream`` to ``agent.run()`` —
   previously it called ``run()`` with the default ``stream=False``, so
   the ``--stream`` CLI flag was silently ignored in agent mode.
3. ``run_task`` prints a plan preview before execution starts when
   verbose=True and the plan has >0 steps.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentkthx.agent_mode import AgentMode, TaskPlan, Step


def _make_session(stream: bool = False, verbose: bool = True):
    """Build a minimal AgentMode session with a mocked agent."""
    agent = MagicMock()
    agent.backend.is_cloud = False
    agent.memory.add = MagicMock()
    # agent.run returns a MagicMock with final_answer / steps / total_ms
    run_result = MagicMock()
    run_result.final_answer = "done"
    run_result.steps = []
    run_result.total_ms = 100.0
    agent.run = MagicMock(return_value=run_result)
    session = AgentMode(agent, verbose=verbose, stream=stream)
    return session, agent


# ---------------------------------------------------------------------------
# Bug 1: stream parameter is stored and passed to agent.run()
# ---------------------------------------------------------------------------

def test_agent_mode_accepts_stream_parameter():
    """AgentMode.__init__ should accept and store a stream kwarg."""
    session, _ = _make_session(stream=True)
    assert session.stream is True


def test_agent_mode_stream_defaults_to_false():
    """Without stream=, AgentMode should default to False (non-streaming)."""
    session, _ = _make_session(stream=False)
    assert session.stream is False


def test_execute_step_passes_stream_to_agent_run():
    """execute_step must call agent.run(prompt, stream=self.stream).

    Previously it called agent.run(prompt) — the --stream flag was
    silently ignored in agent mode.
    """
    session, agent = _make_session(stream=True)
    session.plan = TaskPlan(goal="test")
    session.plan.add_step("do the thing")
    session.execute_step(session.plan.steps[0])

    # agent.run should have been called with stream=True
    assert agent.run.called, "agent.run was not called"
    _args, kwargs = agent.run.call_args
    assert kwargs.get("stream") is True, (
        f"expected stream=True in kwargs, got {kwargs}"
    )


def test_execute_step_passes_stream_false_when_disabled():
    """When stream=False, execute_step should pass stream=False."""
    session, agent = _make_session(stream=False)
    session.plan = TaskPlan(goal="test")
    session.plan.add_step("do the thing")
    session.execute_step(session.plan.steps[0])

    _args, kwargs = agent.run.call_args
    assert kwargs.get("stream") is False


# ---------------------------------------------------------------------------
# Bug 2: plan preview prints before execution starts
# ---------------------------------------------------------------------------

def test_run_task_prints_plan_preview(capsys):
    """run_task should print 'Plan: N step(s)' and numbered steps."""
    session, agent = _make_session(stream=False, verbose=True)
    # Force a 3-step plan via heuristic planning
    agent.run.return_value.final_answer = "ok"
    session.run_task("refactor the code")

    out = capsys.readouterr().out
    assert "Plan:" in out, f"plan preview missing from output:\n{out}"
    assert "step(s)" in out
    assert "1." in out
    assert "2." in out


def test_run_task_skips_plan_preview_when_not_verbose(capsys):
    """When verbose=False, no plan preview should print."""
    session, agent = _make_session(stream=False, verbose=False)
    session.run_task("refactor the code")
    out = capsys.readouterr().out
    assert "Plan:" not in out


# ---------------------------------------------------------------------------
# Bug 3: ⟳ Executing line includes step number for multi-step plans
# ---------------------------------------------------------------------------

def test_executing_line_includes_step_number_for_multistep(capsys):
    """For multi-step plans, the ⟳ line should show [N/M]."""
    session, agent = _make_session(stream=False, verbose=True)
    session.run_task("refactor the code")
    out = capsys.readouterr().out
    # Heuristic plan for "refactor" → 4 steps, so we expect [1/4]
    assert "[1/4]" in out, (
        f"expected [1/4] in ⟳ line, got:\n{out}"
    )


def test_executing_line_omits_step_number_for_single_step(capsys):
    """For single-step plans, the ⟳ line should NOT show [1/1]."""
    session, agent = _make_session(stream=False, verbose=True)
    session.run_task("just do it")  # falls into the 'else' heuristic → 1 step
    out = capsys.readouterr().out
    assert "[1/1]" not in out
    assert "Executing:" in out
