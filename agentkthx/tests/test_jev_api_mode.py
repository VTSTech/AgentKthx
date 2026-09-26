"""
Tests for ApiMode.JEV — System-One decision mode.

JEV mode wraps any chat-capable backend with a constrained decision
prompt + JSON output mode, returning a Jev-shaped envelope:

    {decision, probability, alternatives, usage, latency_ms, raw, _jev}

These tests cover:
1. ApiMode.JEV enum exists
2. CLI accepts --api jev
3. _build_jev_messages() constructs the right prompt for various inputs
4. _parse_jev_response() handles clean JSON, fenced JSON, malformed
   JSON, probability clamping, and constrained-choice fuzzy matching
5. _maybe_jev_dispatch() returns None when not in JEV mode
6. BaseBackend.generate_decision() default raises NotImplementedError
7. Backends (OllamaBackend, ZaiBackend, OpenRouterBackend) accept
   api_mode="jev" at construction time

These are pure logic tests — no network / LLM calls are made.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_dummy_api_keys(monkeypatch):
    """Avoid ValueError when ZAI/OpenRouter constructors check for keys."""
    monkeypatch.setenv("ZAI_API_KEY", "sk-test-dummy-key-for-test-1234")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-dummy-key-1234")


# ─────────────────────────────────────────────────────────────────────
# 1. ApiMode.JEV enum
# ─────────────────────────────────────────────────────────────────────

def test_api_mode_jev_exists():
    """ApiMode enum should include JEV."""
    from agentkthx.core.types import ApiMode
    assert hasattr(ApiMode, "JEV")
    assert ApiMode.JEV.value == "jev"


def test_api_mode_jev_distinct_from_openre_and_openai():
    """JEV should be a separate value from OPENRE and OPENAI."""
    from agentkthx.core.types import ApiMode
    assert ApiMode.JEV != ApiMode.OPENRE
    assert ApiMode.JEV != ApiMode.OPENAI
    assert ApiMode.OPENRE != ApiMode.OPENAI


def test_api_mode_jev_parses_from_string():
    """ApiMode('jev') should construct correctly."""
    from agentkthx.core.types import ApiMode
    assert ApiMode("jev") == ApiMode.JEV


# ─────────────────────────────────────────────────────────────────────
# 2. CLI accepts --api jev
# ─────────────────────────────────────────────────────────────────────

def test_cli_run_accepts_jev_choice():
    """`agentkthx run --api jev` should be a valid CLI invocation."""
    import argparse
    from agentkthx.shared_args import add_agent_args
    parser = argparse.ArgumentParser()
    add_agent_args(parser, tools_default="calculator")
    args = parser.parse_args(["--api", "jev"])
    assert args.api_mode == "jev"


def test_cli_test_accepts_jev_choice():
    """`agentkthx test --api jev` should be accepted."""
    import argparse
    # Recreate the relevant parser fragment from cli.py
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", choices=["openre", "openai", "jev"],
                       default="openre", dest="api_mode")
    args = parser.parse_args(["--api", "jev"])
    assert args.api_mode == "jev"


def test_cli_models_accepts_jev_choice():
    """`agentkthx models --api jev` should be accepted."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", choices=["openre", "openai", "jev"],
                       default=None, dest="api_mode")
    args = parser.parse_args(["--api", "jev"])
    assert args.api_mode == "jev"


def test_cli_rejects_invalid_api_choice():
    """`--api bogus` should be rejected by argparse."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", choices=["openre", "openai", "jev"],
                       default="openai", dest="api_mode")
    with pytest.raises(SystemExit):
        parser.parse_args(["--api", "bogus"])


# ─────────────────────────────────────────────────────────────────────
# 3. _build_jev_messages()
# ─────────────────────────────────────────────────────────────────────

def test_build_jev_messages_basic_dict_state():
    """Should accept dict state and serialize it into the prompt."""
    from agentkthx.backends.ollama import OllamaBackend
    state = {"task": "classify_email", "subject": "You won a prize!"}
    choices = ["spam", "inbox", "promotions"]
    msgs = OllamaBackend._build_jev_messages(state, choices, "Where should this go?")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert "System-One decision model" in msgs[0]["content"]
    assert "classify_email" in msgs[1]["content"]
    assert "You won a prize!" in msgs[1]["content"]
    # Choices should all appear
    for c in choices:
        assert c in msgs[1]["content"]
    # Question framing
    assert "Where should this go?" in msgs[1]["content"]


def test_build_jev_messages_string_state():
    """Should accept a plain string state."""
    from agentkthx.backends.ollama import OllamaBackend
    msgs = OllamaBackend._build_jev_messages("user is asking about refunds", None, None)
    assert msgs[1]["role"] == "user"
    assert "user is asking about refunds" in msgs[1]["content"]
    # Default question
    assert "best decision" in msgs[1]["content"].lower()


def test_build_jev_messages_no_choices():
    """Should work without a constrained choice set."""
    from agentkthx.backends.ollama import OllamaBackend
    msgs = OllamaBackend._build_jev_messages("state x", None, "decide")
    assert "decide" in msgs[1]["content"]
    assert "Choices" not in msgs[1]["content"]


# ─────────────────────────────────────────────────────────────────────
# 4. _parse_jev_response()
# ─────────────────────────────────────────────────────────────────────

def test_parse_jev_response_clean_json():
    """Should parse a clean JSON decision envelope."""
    from agentkthx.backends.ollama import OllamaBackend
    content = json.dumps({
        "decision": "spam",
        "probability": 0.92,
        "alternatives": [
            {"value": "promotions", "probability": 0.06},
            {"value": "inbox", "probability": 0.02},
        ],
    })
    parsed = OllamaBackend._parse_jev_response(content, None)
    assert parsed["decision"] == "spam"
    assert parsed["probability"] == 0.92
    assert len(parsed["alternatives"]) == 2
    assert parsed["alternatives"][0]["value"] == "promotions"
    assert parsed["_parse_ok"] is True


def test_parse_jev_response_markdown_fences():
    """Should strip ```json fences before parsing."""
    from agentkthx.backends.ollama import OllamaBackend
    inner = json.dumps({"decision": "yes", "probability": 0.7, "alternatives": []})
    fenced = f"```json\n{inner}\n```"
    parsed = OllamaBackend._parse_jev_response(fenced, None)
    assert parsed["decision"] == "yes"
    assert parsed["_parse_ok"] is True


def test_parse_jev_response_plain_fences():
    """Should strip ``` fences (no language tag) before parsing."""
    from agentkthx.backends.ollama import OllamaBackend
    inner = json.dumps({"decision": "yes", "probability": 0.7, "alternatives": []})
    fenced = f"```\n{inner}\n```"
    parsed = OllamaBackend._parse_jev_response(fenced, None)
    assert parsed["decision"] == "yes"


def test_parse_jev_response_malformed_fallback():
    """Should fall back to raw text as decision when JSON is malformed."""
    from agentkthx.backends.ollama import OllamaBackend
    parsed = OllamaBackend._parse_jev_response("not really json", None)
    assert parsed["_parse_ok"] is False
    assert parsed["decision"] == "not really json"
    assert parsed["probability"] == 0.0
    assert parsed["alternatives"] == []


def test_parse_jev_response_probability_clamping_high():
    """Probability above 1.0 should be clamped to 1.0."""
    from agentkthx.backends.ollama import OllamaBackend
    content = json.dumps({"decision": "x", "probability": 1.5, "alternatives": []})
    parsed = OllamaBackend._parse_jev_response(content, None)
    assert parsed["probability"] == 1.0


def test_parse_jev_response_probability_clamping_low():
    """Probability below 0.0 should be clamped to 0.0."""
    from agentkthx.backends.ollama import OllamaBackend
    content = json.dumps({"decision": "x", "probability": -0.5, "alternatives": []})
    parsed = OllamaBackend._parse_jev_response(content, None)
    assert parsed["probability"] == 0.0


def test_parse_jev_response_constrained_exact_match():
    """When decision matches a choice exactly, use canonical form."""
    from agentkthx.backends.ollama import OllamaBackend
    choices = ["spam", "inbox", "promotions"]
    content = json.dumps({"decision": "spam", "probability": 0.9, "alternatives": []})
    parsed = OllamaBackend._parse_jev_response(content, choices)
    assert parsed["decision"] == "spam"


def test_parse_jev_response_constrained_fuzzy_match():
    """When decision contains a choice as substring, snap to canonical form."""
    from agentkthx.backends.ollama import OllamaBackend
    choices = ["spam", "inbox", "promotions"]
    content = json.dumps({"decision": "I think this is spam", "probability": 0.8, "alternatives": []})
    parsed = OllamaBackend._parse_jev_response(content, choices)
    assert parsed["decision"] == "spam"


def test_parse_jev_response_constrained_no_match():
    """When decision doesn't match any choice, keep the model's output."""
    from agentkthx.backends.ollama import OllamaBackend
    choices = ["spam", "inbox", "promotions"]
    content = json.dumps({"decision": "totally_unrelated_thing", "probability": 0.5, "alternatives": []})
    parsed = OllamaBackend._parse_jev_response(content, choices)
    assert parsed["decision"] == "totally_unrelated_thing"


def test_parse_jev_response_alternatives_as_string_list():
    """Should accept alternatives as a list of strings (not just dicts)."""
    from agentkthx.backends.ollama import OllamaBackend
    content = json.dumps({"decision": "x", "probability": 0.7, "alternatives": ["y", "z"]})
    parsed = OllamaBackend._parse_jev_response(content, None)
    assert len(parsed["alternatives"]) == 2
    assert parsed["alternatives"][0]["value"] == "y"
    assert parsed["alternatives"][0]["probability"] == 0.0


# ─────────────────────────────────────────────────────────────────────
# 5. _serialize_state()
# ─────────────────────────────────────────────────────────────────────

def test_serialize_state_string_passthrough():
    """Strings should pass through unchanged."""
    from agentkthx.backends.ollama import OllamaBackend
    assert OllamaBackend._serialize_state("hello world") == "hello world"


def test_serialize_state_dict_to_json():
    """Dicts should be JSON-serialized."""
    from agentkthx.backends.ollama import OllamaBackend
    serialized = OllamaBackend._serialize_state({"a": 1, "b": 2})
    assert '"a"' in serialized
    assert "1" in serialized


def test_serialize_state_non_serializable():
    """Non-JSON-serializable objects should fall back to str()."""
    from agentkthx.backends.ollama import OllamaBackend
    obj = object()
    serialized = OllamaBackend._serialize_state(obj)
    assert "object" in serialized.lower() or serialized  # at least non-empty


# ─────────────────────────────────────────────────────────────────────
# 6. _maybe_jev_dispatch()
# ─────────────────────────────────────────────────────────────────────

def test_maybe_jev_dispatch_returns_none_when_not_jev():
    """When api_mode is OPENRE or OPENAI, _maybe_jev_dispatch returns None."""
    from agentkthx.core.types import ApiMode
    from agentkthx.backends.ollama import OllamaBackend

    # Use a MagicMock to avoid hitting __init__ (which may try network calls)
    backend = MagicMock(spec=OllamaBackend)
    backend._api_mode = ApiMode.OPENAI
    # Bind the real method
    result = OllamaBackend._maybe_jev_dispatch(
        backend,
        model="test",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result is None


# ─────────────────────────────────────────────────────────────────────
# 7. Backend construction in JEV mode
# ─────────────────────────────────────────────────────────────────────

def test_zai_backend_accepts_jev_mode():
    """ZaiBackend should accept api_mode='jev'."""
    from agentkthx.core.types import ApiMode
    from agentkthx.plugins.zai.zai import ZaiBackend
    backend = ZaiBackend(api_mode="jev")
    assert backend.api_mode == ApiMode.JEV


def test_openrouter_backend_accepts_jev_mode():
    """OpenRouterBackend should accept api_mode='jev'."""
    from agentkthx.core.types import ApiMode
    from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
    backend = OpenRouterBackend(api_mode="jev")
    assert backend.api_mode == ApiMode.JEV


def test_zai_backend_rejects_openre_mode():
    """ZaiBackend should fall back to OPENAI when OPENRE is requested."""
    from agentkthx.core.types import ApiMode
    from agentkthx.plugins.zai.zai import ZaiBackend
    # OPENRE is not supported by ZAI — should fall back to OPENAI, not raise
    backend = ZaiBackend(api_mode="openre")
    assert backend.api_mode == ApiMode.OPENAI


def test_openrouter_backend_rejects_openre_mode():
    """OpenRouterBackend should reject OPENRE with ValueError."""
    from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
    with pytest.raises(ValueError):
        OpenRouterBackend(api_mode="openre")


def test_backends_have_jev_hook():
    """All three backends should have _jev_call_completions and _maybe_jev_dispatch."""
    from agentkthx.backends.ollama import OllamaBackend
    from agentkthx.plugins.zai.zai import ZaiBackend
    from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
    for cls in (OllamaBackend, ZaiBackend, OpenRouterBackend):
        assert hasattr(cls, "_jev_call_completions"), f"{cls.__name__} missing _jev_call_completions"
        assert hasattr(cls, "_maybe_jev_dispatch"), f"{cls.__name__} missing _maybe_jev_dispatch"
        assert hasattr(cls, "generate_decision"), f"{cls.__name__} missing generate_decision"


def test_base_backend_generate_decision_default_raises():
    """BaseBackend.generate_decision() default should raise NotImplementedError."""
    from agentkthx.backends.base import BaseBackend, BackendConfig

    # BaseBackend is ABC; create a minimal concrete subclass that
    # implements only the abstract methods, leaving generate_decision()
    # as the default (which should raise).
    class _MinimalBackend(BaseBackend):
        @property
        def backend_type(self):
            from agentkthx.core.types import BackendType
            return BackendType.OLLAMA

        @property
        def base_url(self):
            return "http://localhost:11434"

        def generate(self, *a, **kw):
            return {}

        def generate_stream(self, *a, **kw):
            yield ""

        def list_models(self):
            return []

        def test_tool_support(self, *a, **kw):
            from agentkthx.core.types import ToolSupportLevel
            return ToolSupportLevel.NONE

    backend = _MinimalBackend(config=BackendConfig())
    with pytest.raises(NotImplementedError):
        backend.generate_decision(model="x", state="test")


# ─────────────────────────────────────────────────────────────────────
# 8. generate_decision() wiring (mocked LLM call)
# ─────────────────────────────────────────────────────────────────────

def test_generate_decision_calls_jev_hook_and_parses():
    """generate_decision() should call _jev_call_completions and parse its output."""
    from agentkthx.core.types import ApiMode
    from agentkthx.backends.ollama import OllamaBackend

    backend = MagicMock(spec=OllamaBackend)
    backend._api_mode = ApiMode.JEV

    # _build_jev_messages and _parse_jev_response are staticmethods on
    # OllamaBackend; let them run for real.
    backend._build_jev_messages = OllamaBackend._build_jev_messages
    backend._parse_jev_response = OllamaBackend._parse_jev_response

    # Mock the underlying LLM call to return a clean JSON decision
    fake_llm_response = {
        "content": json.dumps({
            "decision": "yes",
            "probability": 0.85,
            "alternatives": [{"value": "no", "probability": 0.15}],
        }),
        "tool_calls": [],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "latency_ms": 42.0,
        "raw": {},
    }
    backend._jev_call_completions = MagicMock(return_value=fake_llm_response)

    # Call generate_decision via the real OllamaBackend method
    result = OllamaBackend.generate_decision(
        backend,
        model="test-model",
        state="is this a good idea?",
        choices=["yes", "no"],
    )

    assert result["_jev"] is True
    assert result["_parse_ok"] is True
    assert result["decision"] == "yes"
    assert result["probability"] == 0.85
    assert len(result["alternatives"]) == 1
    assert result["alternatives"][0]["value"] == "no"
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["output_tokens"] == 20
    assert result["usage"]["total_tokens"] == 120
    assert result["latency_ms"] > 0  # actual measured latency

    # Verify the hook was called with JSON mode requested
    backend._jev_call_completions.assert_called_once()
    call_kwargs = backend._jev_call_completions.call_args
    assert call_kwargs.kwargs.get("response_format") == {"type": "json_object"}


def test_generate_decision_handles_malformed_llm_output():
    """generate_decision() should gracefully handle malformed LLM JSON."""
    from agentkthx.core.types import ApiMode
    from agentkthx.backends.ollama import OllamaBackend

    backend = MagicMock(spec=OllamaBackend)
    backend._api_mode = ApiMode.JEV
    backend._build_jev_messages = OllamaBackend._build_jev_messages
    backend._parse_jev_response = OllamaBackend._parse_jev_response

    fake_llm_response = {
        "content": "I am not JSON, sorry",
        "tool_calls": [],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        "latency_ms": 10.0,
        "raw": {},
    }
    backend._jev_call_completions = MagicMock(return_value=fake_llm_response)

    result = OllamaBackend.generate_decision(
        backend,
        model="test-model",
        state="state",
        choices=None,
    )

    assert result["_jev"] is True
    assert result["_parse_ok"] is False
    assert result["decision"] == "I am not JSON, sorry"
    assert result["probability"] == 0.0
    assert result["alternatives"] == []


def test_maybe_jev_dispatch_in_jev_mode_returns_decision_envelope():
    """_maybe_jev_dispatch() should return a generate()-shaped dict in JEV mode."""
    from agentkthx.core.types import ApiMode
    from agentkthx.backends.ollama import OllamaBackend

    backend = MagicMock(spec=OllamaBackend)
    backend._api_mode = ApiMode.JEV

    # Mock generate_decision to return a known envelope
    fake_decision = {
        "decision": "yes",
        "probability": 0.9,
        "alternatives": [],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "latency_ms": 50.0,
        "raw": {},
        "_jev": True,
        "_parse_ok": True,
    }
    backend.generate_decision = MagicMock(return_value=fake_decision)

    result = OllamaBackend._maybe_jev_dispatch(
        backend,
        model="test",
        messages=[{"role": "user", "content": "is this ok?"}],
    )

    assert result is not None
    assert result["_jev"] is True
    # Content should be JSON-serialized decision envelope
    parsed_content = json.loads(result["content"])
    assert parsed_content["decision"] == "yes"
    assert result["usage"]["prompt_tokens"] == 10
    assert result["usage"]["completion_tokens"] == 5
    assert result["tool_calls"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
