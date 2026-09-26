"""
Tests for --thinking and --think CLI args + thinking-level forwarding.

R05.8 added two new CLI flags:
- --thinking off|auto|low|medium|high  → controls model thinking behavior
- --think                              → toggles display of reasoning_content

These tests cover:
1. ThinkingLevel enum exists and has the expected members
2. parse_thinking_arg() maps strings/enums to (think, reasoning_effort) tuples
3. CLI accepts --thinking with each valid value
4. CLI rejects invalid --thinking values
5. CLI accepts --think boolean flag
6. Agent.__init__() accepts thinking_level, think, reasoning_effort, show_reasoning
7. Agent stores the resolved (think, reasoning_effort) tuple correctly
8. Explicit Agent kwargs override what thinking_level would resolve to
9. StepResult carries reasoning_content field
10. _print_agent_steps accepts show_reasoning kwarg (signature compat)

These are pure logic tests — no network / LLM calls are made.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import pytest


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_dummy_api_keys(monkeypatch):
    """Avoid ValueError when ZAI/OpenRouter constructors check for keys."""
    monkeypatch.setenv("ZAI_API_KEY", "sk-test-dummy-key-for-test-1234")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy-key-1234")


# ─────────────────────────────────────────────────────────────────────
# 1. ThinkingLevel enum
# ─────────────────────────────────────────────────────────────────────

def test_thinking_level_enum_exists():
    """ThinkingLevel enum should be defined in core.types."""
    from agentkthx.core.types import ThinkingLevel
    assert hasattr(ThinkingLevel, "OFF")
    assert hasattr(ThinkingLevel, "AUTO")
    assert hasattr(ThinkingLevel, "LOW")
    assert hasattr(ThinkingLevel, "MEDIUM")
    assert hasattr(ThinkingLevel, "HIGH")


def test_thinking_level_values():
    """ThinkingLevel members should have lowercase string values."""
    from agentkthx.core.types import ThinkingLevel
    assert ThinkingLevel.OFF.value == "off"
    assert ThinkingLevel.AUTO.value == "auto"
    assert ThinkingLevel.LOW.value == "low"
    assert ThinkingLevel.MEDIUM.value == "medium"
    assert ThinkingLevel.HIGH.value == "high"


def test_thinking_level_parses_from_string():
    """ThinkingLevel('off') etc. should construct correctly."""
    from agentkthx.core.types import ThinkingLevel
    assert ThinkingLevel("off") == ThinkingLevel.OFF
    assert ThinkingLevel("auto") == ThinkingLevel.AUTO
    assert ThinkingLevel("medium") == ThinkingLevel.MEDIUM


# ─────────────────────────────────────────────────────────────────────
# 2. parse_thinking_arg()
# ─────────────────────────────────────────────────────────────────────

def test_parse_thinking_arg_off():
    """'off' should map to (False, None) — disable thinking."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("off") == (False, None)


def test_parse_thinking_arg_auto():
    """'auto' should map to (None, None) — let model decide."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("auto") == (None, None)


def test_parse_thinking_arg_low():
    """'low' should map to (True, 'low')."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("low") == (True, "low")


def test_parse_thinking_arg_medium():
    """'medium' should map to (True, 'medium')."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("medium") == (True, "medium")


def test_parse_thinking_arg_high():
    """'high' should map to (True, 'high')."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("high") == (True, "high")


def test_parse_thinking_arg_none():
    """None should map to (None, None) — default behavior."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg(None) == (None, None)


def test_parse_thinking_arg_empty_string():
    """Empty string should map to (None, None)."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("") == (None, None)


def test_parse_thinking_arg_case_insensitive():
    """Should accept uppercase / mixed case."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("OFF") == (False, None)
    assert parse_thinking_arg("High") == (True, "high")


def test_parse_thinking_arg_unknown_falls_back_to_auto():
    """Unknown strings should fall back to (None, None), not raise."""
    from agentkthx.core.types import parse_thinking_arg
    assert parse_thinking_arg("bogus") == (None, None)
    assert parse_thinking_arg("turbo") == (None, None)


def test_parse_thinking_arg_accepts_enum():
    """Should accept ThinkingLevel enum values directly."""
    from agentkthx.core.types import ThinkingLevel, parse_thinking_arg
    assert parse_thinking_arg(ThinkingLevel.OFF) == (False, None)
    assert parse_thinking_arg(ThinkingLevel.HIGH) == (True, "high")
    assert parse_thinking_arg(ThinkingLevel.AUTO) == (None, None)


# ─────────────────────────────────────────────────────────────────────
# 3. CLI --thinking accepts valid values
# ─────────────────────────────────────────────────────────────────────

def _make_parser():
    """Build a parser with add_agent_args."""
    from agentkthx.shared_args import add_agent_args
    parser = argparse.ArgumentParser()
    add_agent_args(parser, tools_default="calculator")
    return parser


@pytest.mark.parametrize("level", ["off", "auto", "low", "medium", "high"])
def test_cli_thinking_accepts_valid_values(level):
    """`--thinking <level>` should parse without error for each valid value."""
    parser = _make_parser()
    args = parser.parse_args(["--thinking", level])
    assert args.thinking_level == level


def test_cli_thinking_default_is_auto():
    """Default thinking_level should be 'auto'."""
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.thinking_level == "auto"


def test_cli_thinking_rejects_invalid_value():
    """`--thinking bogus` should be rejected by argparse."""
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--thinking", "bogus"])


def test_cli_thinking_choices_in_help():
    """Help string should mention all 5 levels."""
    from agentkthx.shared_args import add_agent_args
    parser = argparse.ArgumentParser()
    add_agent_args(parser, tools_default="calculator")
    help_text = parser.format_help()
    for level in ("off", "auto", "low", "medium", "high"):
        assert level in help_text


# ─────────────────────────────────────────────────────────────────────
# 4. CLI --think boolean flag
# ─────────────────────────────────────────────────────────────────────

def test_cli_think_flag_default_false():
    """--think should default to False."""
    parser = _make_parser()
    args = parser.parse_args([])
    assert args.show_reasoning is False


def test_cli_think_flag_sets_true():
    """`--think` should set show_reasoning=True."""
    parser = _make_parser()
    args = parser.parse_args(["--think"])
    assert args.show_reasoning is True


def test_cli_thinking_and_think_can_combine():
    """Both --thinking and --think can be used together."""
    parser = _make_parser()
    args = parser.parse_args(["--thinking", "high", "--think"])
    assert args.thinking_level == "high"
    assert args.show_reasoning is True


# ─────────────────────────────────────────────────────────────────────
# 5. Agent.__init__() accepts new params
# ─────────────────────────────────────────────────────────────────────

def _make_agent(**kwargs):
    """Build a minimal Agent for testing without running it."""
    from agentkthx import Agent
    defaults = {"model": "qwen2.5:0.5b"}
    defaults.update(kwargs)
    return Agent(**defaults)


def test_agent_accepts_thinking_level():
    """Agent should accept thinking_level kwarg without error."""
    agent = _make_agent(thinking_level="off")
    assert agent._thinking_level == "off"


def test_agent_thinking_level_off_resolves_to_think_false():
    """thinking_level='off' should resolve to self._think=False."""
    agent = _make_agent(thinking_level="off")
    assert agent._think is False
    assert agent._reasoning_effort is None


def test_agent_thinking_level_auto_resolves_to_think_none():
    """thinking_level='auto' should resolve to self._think=None."""
    agent = _make_agent(thinking_level="auto")
    assert agent._think is None
    assert agent._reasoning_effort is None


def test_agent_thinking_level_medium_resolves_correctly():
    """thinking_level='medium' should resolve to think=True, effort='medium'."""
    agent = _make_agent(thinking_level="medium")
    assert agent._think is True
    assert agent._reasoning_effort == "medium"


def test_agent_thinking_level_high_resolves_correctly():
    """thinking_level='high' should resolve to think=True, effort='high'."""
    agent = _make_agent(thinking_level="high")
    assert agent._think is True
    assert agent._reasoning_effort == "high"


def test_agent_explicit_think_kwarg_overrides_thinking_level():
    """Explicit think= kwarg should override thinking_level resolution."""
    agent = _make_agent(thinking_level="auto", think=True)
    # thinking_level would resolve to (None, None), but explicit think=True wins
    assert agent._think is True


def test_agent_explicit_reasoning_effort_kwarg_overrides():
    """Explicit reasoning_effort= kwarg should override thinking_level."""
    agent = _make_agent(thinking_level="auto", reasoning_effort="low")
    assert agent._reasoning_effort == "low"


def test_agent_accepts_show_reasoning():
    """Agent should accept show_reasoning kwarg."""
    agent = _make_agent(show_reasoning=True)
    assert agent._show_reasoning is True


def test_agent_show_reasoning_default_false():
    """show_reasoning should default to False."""
    agent = _make_agent()
    assert agent._show_reasoning is False


def test_agent_thinking_level_default_is_auto():
    """thinking_level should default to 'auto' when not specified."""
    agent = _make_agent()
    assert agent._thinking_level == "auto"


# ─────────────────────────────────────────────────────────────────────
# 6. StepResult carries reasoning_content
# ─────────────────────────────────────────────────────────────────────

def test_step_result_has_reasoning_content_field():
    """StepResult dataclass should have reasoning_content field."""
    from agentkthx.core.models import StepResult
    from agentkthx.core.types import StepResultType
    sr = StepResult(type=StepResultType.FINAL_ANSWER)
    assert hasattr(sr, "reasoning_content")


def test_step_result_reasoning_content_default_empty():
    """StepResult.reasoning_content should default to empty string."""
    from agentkthx.core.models import StepResult
    from agentkthx.core.types import StepResultType
    sr = StepResult(type=StepResultType.FINAL_ANSWER)
    assert sr.reasoning_content == ""


def test_step_result_accepts_reasoning_content():
    """StepResult should accept reasoning_content in constructor."""
    from agentkthx.core.models import StepResult
    from agentkthx.core.types import StepResultType
    sr = StepResult(
        type=StepResultType.FINAL_ANSWER,
        content="spam",
        reasoning_content="I think this is spam because...",
    )
    assert sr.reasoning_content == "I think this is spam because..."


# ─────────────────────────────────────────────────────────────────────
# 7. _print_agent_steps accepts show_reasoning kwarg
# ─────────────────────────────────────────────────────────────────────

def test_print_agent_steps_accepts_show_reasoning_kwarg():
    """_print_agent_steps() should accept show_reasoning kwarg without error."""
    from agentkthx.cli import _print_agent_steps
    from agentkthx.core.models import AgentRun
    # Empty AgentRun — function should early-return because steps list is empty
    result = AgentRun(final_answer="test")
    # Should not raise
    _print_agent_steps(result, debug=False, show_reasoning=True)
    _print_agent_steps(result, debug=False, show_reasoning=False)


# ─────────────────────────────────────────────────────────────────────
# 8. OllamaBackend response carries reasoning_content
# ─────────────────────────────────────────────────────────────────────

def test_ollama_backend_response_dict_has_reasoning_content_key():
    """OllamaBackend.generate_completions response should have reasoning_content key.

    We can't easily test the actual HTTP path without a running Ollama,
    but we can verify the _parse_choice helper captures it.
    """
    # The _parse_choice function is a closure inside generate_completions.
    # We can verify the field appears by checking the response shape via
    # _parse_jev_response (which doesn't help here). Instead, verify by
    # inspecting the source for the field name.
    import inspect
    from agentkthx.backends.ollama import OllamaBackend
    src = inspect.getsource(OllamaBackend.generate_completions)
    assert "reasoning_content" in src, "OllamaBackend.generate_completions should reference reasoning_content"


def test_ollama_backend_native_response_has_reasoning_content_key():
    """OllamaBackend.generate (native /api/chat) response should have reasoning_content key."""
    import inspect
    from agentkthx.backends.ollama import OllamaBackend
    src = inspect.getsource(OllamaBackend.generate)
    assert "reasoning_content" in src


def test_jev_decision_envelope_has_reasoning_content_key():
    """generate_decision() return dict should include reasoning_content field."""
    import inspect
    from agentkthx.backends.ollama import OllamaBackend
    src = inspect.getsource(OllamaBackend.generate_decision)
    assert "reasoning_content" in src, "generate_decision should surface reasoning_content"


# ─────────────────────────────────────────────────────────────────────
# 9. End-to-end CLI arg flow
# ─────────────────────────────────────────────────────────────────────

def test_cli_args_to_agent_thinking_flow():
    """Simulate: parse CLI args → resolve via parse_thinking_arg → pass to Agent.

    This is the same flow cli.py uses in _create_agent_from_args().
    """
    from agentkthx.core.types import parse_thinking_arg
    from agentkthx import Agent

    parser = _make_parser()
    args = parser.parse_args(["--thinking", "off", "--think", "-m", "qwen2.5:0.5b"])

    # Resolve --thinking to (think, reasoning_effort)
    think_param, reasoning_effort = parse_thinking_arg(args.thinking_level)
    assert think_param is False
    assert reasoning_effort is None

    # Build Agent (mirrors cli.py logic)
    agent = Agent(
        model=args.model,
        thinking_level=args.thinking_level,
        think=think_param,
        reasoning_effort=reasoning_effort,
        show_reasoning=args.show_reasoning,
    )

    assert agent._thinking_level == "off"
    assert agent._think is False
    assert agent._reasoning_effort is None
    assert agent._show_reasoning is True


def test_cli_args_to_agent_thinking_high_flow():
    """End-to-end: --thinking high → Agent with reasoning_effort='high'."""
    from agentkthx.core.types import parse_thinking_arg
    from agentkthx import Agent

    parser = _make_parser()
    args = parser.parse_args(["--thinking", "high", "-m", "qwen2.5:0.5b"])

    think_param, reasoning_effort = parse_thinking_arg(args.thinking_level)
    agent = Agent(
        model=args.model,
        thinking_level=args.thinking_level,
        think=think_param,
        reasoning_effort=reasoning_effort,
    )

    assert agent._think is True
    assert agent._reasoning_effort == "high"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
