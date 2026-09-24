"""
AgentKthx — ARCH-03 regression tests: shared context-length 400 recovery

R06.57 (ARCH-03): The previously-triplicated 429 retry / `num_ctx/32` cap /
`_calculate_safe_max_tokens` pattern was lifted from OpenRouterBackend
(R06.55), GeminiBackend (R06.56), and ZaiBackend (R06.57) into three shared
methods on ``OpenAICompatibleBackend``:

- ``_apply_max_tokens_cap(max_tokens, context_length, temperature)`` —
  returns a model-defaults dict with the `num_ctx / 32` cap applied,
  honoring a persisted ``_context_safe_max_tokens`` if set.
- ``_calculate_safe_max_tokens(error_body, body)`` — parses a 400 error
  body using per-backend regex patterns, returns a safe max_tokens.
- ``_handle_context_length_400(error_body, body)`` — orchestrates the
  parse/persist/mutate dance; returns True if caller should retry.

Per-backend differences are encapsulated in three class attributes that
concrete backends override:
- ``_CONTEXT_LENGTH_MAX_PATTERN`` — regex for max context length
- ``_CONTEXT_LENGTH_INPUT_PATTERN`` — regex for input tokens
- ``_CONTEXT_LENGTH_TOOL_PATTERN`` — regex for tool input tokens (None = no tool input)

These tests verify:
1. The shared helpers work correctly with the default patterns (OpenRouter/ZAI format)
2. The shared helpers work correctly with Gemini's overridden patterns
3. A fake 5th cloud backend inherits the default patterns automatically
4. The persisted safe value wins over the proactive cap
5. `_handle_context_length_400` returns the right boolean + mutates body correctly
6. The fallback (1/3 reduction) fires when the regex can't parse
"""

import pytest

from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.core.types import ApiMode


# ----------------------------------------------------------------------------
# Test fixtures — minimal concrete backends that exercise the shared helpers
# ----------------------------------------------------------------------------

class _DefaultPatternsBackend(OpenAICompatibleBackend):
    """Test double using the default regex patterns (OpenRouter/ZAI format).

    Instantiates without going through a real __init__ (which would require
    API keys etc.) — we only need the shared helpers, which don't touch
    instance state beyond ``_context_safe_max_tokens``.
    """

    def __init__(self):
        # Bypass OpenAICompatibleBackend.__init__ (which calls BaseBackend.__init__
        # and would require api_mode etc.). Set only what the helpers need.
        self.config = BackendConfig()
        self._context_safe_max_tokens = None

    # Stub the abstract methods so the class can be instantiated
    @property
    def backend_type(self):
        from agentkthx.core.types import BackendType
        return BackendType.OLLAMA  # placeholder

    @property
    def base_url(self):
        return "http://localhost:1234"

    def generate(self, *args, **kwargs): pass
    def generate_stream(self, *args, **kwargs): pass
    def list_models(self, *args, **kwargs): return []
    def test_tool_support(self, *args, **kwargs):
        from agentkthx.core.types import ToolSupportLevel
        return ToolSupportLevel.NONE
    def _get_chat_completions_url(self): return "http://localhost/v1/chat/completions"
    def _get_auth_headers(self): return {}
    def _iter_sse_lines(self, url, body, headers): return iter([])
    def _get_model_defaults(self, model): return {}


class _GeminiPatternsBackend(_DefaultPatternsBackend):
    """Test double using Gemini's overridden regex patterns."""

    _CONTEXT_LENGTH_MAX_PATTERN = r"maximum context length of (\d+)"
    _CONTEXT_LENGTH_INPUT_PATTERN = r"(\d+) in the input"
    _CONTEXT_LENGTH_TOOL_PATTERN = None  # Gemini doesn't separate tool input


class _FakeFifthCloudBackend(_DefaultPatternsBackend):
    """Test double simulating a hypothetical 5th cloud backend.

    Subclasses OpenAICompatibleBackend with no overrides — should inherit
    the default patterns automatically (proving ARCH-03's value: a 5th
    cloud backend gets context-length 400 recovery for free).
    """
    pass


# ----------------------------------------------------------------------------
# _apply_max_tokens_cap
# ----------------------------------------------------------------------------

class TestApplyMaxTokensCap:
    """R06.57 (ARCH-03): The ``num_ctx / 32`` cap + persisted-safe-value logic."""

    def test_caps_max_tokens_to_context_div_32(self):
        """max_tokens > context_length/32 → capped to context_length/32."""
        b = _DefaultPatternsBackend()
        # 128K context, 128K max_tokens → cap to 128000//32 = 4000
        result = b._apply_max_tokens_cap(131072, 128000, temperature=0.7)
        assert result["max_tokens"] == 4000
        assert result["temperature"] == 0.7
        assert result["context_length"] == 128000

    def test_no_cap_when_max_tokens_already_smaller(self):
        """max_tokens < context_length/32 → returned as-is (min() picks the smaller)."""
        b = _DefaultPatternsBackend()
        # 256K context, 4K max_tokens → cap is 8K, so max_tokens (4K) wins
        result = b._apply_max_tokens_cap(4096, 262144, temperature=0.7)
        assert result["max_tokens"] == 4096

    def test_persisted_safe_value_wins_over_cap(self):
        """If _context_safe_max_tokens is set, it overrides the cap."""
        b = _DefaultPatternsBackend()
        b._context_safe_max_tokens = 2048  # persisted from a previous 400
        result = b._apply_max_tokens_cap(131072, 128000, temperature=0.7)
        assert result["max_tokens"] == 2048  # not the cap (4000)

    def test_persisted_safe_value_wins_even_when_larger_than_cap(self):
        """Persisted safe value wins even if it's larger than the proactive cap.

        This happens when the persisted value was calculated from a 400 error
        and is the only value that won't re-trigger the 400.
        """
        b = _DefaultPatternsBackend()
        b._context_safe_max_tokens = 6000  # larger than cap (4000)
        result = b._apply_max_tokens_cap(131072, 128000, temperature=0.7)
        assert result["max_tokens"] == 6000

    def test_custom_divisor_via_class_attribute(self):
        """A backend can override _MAX_TOKENS_CAP_DIVISOR for a different cap."""
        class _TightCapBackend(_DefaultPatternsBackend):
            _MAX_TOKENS_CAP_DIVISOR = 8  # more aggressive cap
        b = _TightCapBackend()
        # 128K context, 128K max_tokens, divisor=8 → cap to 128000//8 = 16000
        result = b._apply_max_tokens_cap(131072, 128000, temperature=0.7)
        assert result["max_tokens"] == 16000


# ----------------------------------------------------------------------------
# _calculate_safe_max_tokens — default patterns (OpenRouter/ZAI format)
# ----------------------------------------------------------------------------

class TestCalculateSafeMaxTokensDefaultPatterns:
    """R06.57 (ARCH-03): Default regex patterns parse OpenRouter/ZAI errors."""

    def test_parses_openrouter_error_format(self):
        """OpenRouter: 'maximum context length is 262144 tokens ... 85421 of text input ... 305 of tool input ... 196608 in the output'"""
        b = _DefaultPatternsBackend()
        error = ('This endpoint\'s maximum context length is 262144 tokens. '
                 'However, you requested about 282334 tokens (85421 of text input, '
                 '305 of tool input, 196608 in the output).')
        body = {"max_tokens": 196608}
        # safe = 262144 - 85421 - 305 - 2048 = 174370
        result = b._calculate_safe_max_tokens(error, body)
        assert result == 262144 - 85421 - 305 - 2048
        assert result < 196608  # must be less than the original

    def test_parses_zai_error_format(self):
        """ZAI: 'maximum context length is 131072 tokens ... 120000 of text input ... 20000 in the output'"""
        b = _DefaultPatternsBackend()
        error = ("This model's maximum context length is 131072 tokens. "
                 "However, you requested 140000 tokens (120000 of text input, "
                 "20000 in the output).")
        body = {"max_tokens": 20000}
        # safe = 131072 - 120000 - 2048 = 9024
        result = b._calculate_safe_max_tokens(error, body)
        assert result == 131072 - 120000 - 2048

    def test_returns_none_when_safe_max_exceeds_original(self):
        """If computed safe_max >= original max_tokens, returns None (already safe)."""
        b = _DefaultPatternsBackend()
        error = ('maximum context length is 262144 tokens. '
                 '85421 of text input, 196608 in the output.')
        body = {"max_tokens": 1000}  # already very small
        # safe = 262144 - 85421 - 2048 = 174675, which is > 1000 → return None
        result = b._calculate_safe_max_tokens(error, body)
        assert result is None

    def test_floors_at_1024_when_input_exceeds_context(self):
        """If safe_max < 1024, it's floored to 1024."""
        b = _DefaultPatternsBackend()
        # Input is so large that even 1K output doesn't fit
        error = ('maximum context length is 131072 tokens. '
                 '131000 of text input, 20000 in the output.')
        body = {"max_tokens": 20000}
        # safe = 131072 - 131000 - 2048 = -1976 → floored to 1024
        result = b._calculate_safe_max_tokens(error, body)
        assert result == 1024

    def test_falls_back_to_third_reduction_when_regex_fails(self):
        """If the regex can't parse the error, fall back to old_max // 3 (>=4096)."""
        b = _DefaultPatternsBackend()
        error = "Something went wrong but no token counts here"
        body = {"max_tokens": 12000}
        # Fallback: max(12000 // 3, 4096) = 4000... wait, 12000//3 = 4000, so 4096 wins
        # Actually max(4000, 4096) = 4096, but 4096 < 12000 so return 4096
        result = b._calculate_safe_max_tokens(error, body)
        assert result == max(12000 // 3, 4096)  # = 4096
        assert result < 12000

    def test_returns_none_on_third_reduction_when_already_small(self):
        """If the 1/3 reduction doesn't actually reduce, return None."""
        b = _DefaultPatternsBackend()
        error = "Unparseable error"
        body = {"max_tokens": 4096}
        # Fallback: max(4096 // 3, 4096) = max(1365, 4096) = 4096
        # 4096 < 4096 is False, so return None
        result = b._calculate_safe_max_tokens(error, body)
        assert result is None


# ----------------------------------------------------------------------------
# _calculate_safe_max_tokens — Gemini patterns (overridden)
# ----------------------------------------------------------------------------

class TestCalculateSafeMaxTokensGeminiPatterns:
    """R06.57 (ARCH-03): Gemini's overridden regex patterns parse Gemini errors."""

    def test_parses_gemini_error_format(self):
        """Gemini: 'maximum context length of 1048576 tokens ... 1000000 in the input ... 100000 in the output'"""
        b = _GeminiPatternsBackend()
        error = ('Request exceeds the maximum context length of 1048576 tokens. '
                 'You requested 1100000 tokens (1000000 in the input, 100000 in the output).')
        body = {"max_tokens": 100000}
        # safe = 1048576 - 1000000 - 2048 = 46528
        result = b._calculate_safe_max_tokens(error, body)
        assert result == 1048576 - 1000000 - 2048

    def test_gemini_patterns_do_not_match_openrouter_format(self):
        """Gemini's regex should NOT match OpenRouter's error format (and vice versa).

        This proves the per-backend override is actually being applied —
        not silently falling through to the default.
        """
        b = _GeminiPatternsBackend()
        # OpenRouter format: "maximum context length is N tokens" + "N of text input"
        # Gemini patterns: "maximum context length of N" + "N in the input"
        # → Gemini patterns won't match → falls back to 1/3 reduction
        error = ('maximum context length is 262144 tokens. '
                 '85421 of text input, 305 of tool input, 196608 in the output.')
        body = {"max_tokens": 196608}
        result = b._calculate_safe_max_tokens(error, body)
        # Should fall back to 1/3 reduction, not parse correctly
        assert result == max(196608 // 3, 4096)  # = 65536

    def test_gemini_tool_pattern_is_none(self):
        """Gemini doesn't separate tool input — _CONTEXT_LENGTH_TOOL_PATTERN is None."""
        assert _GeminiPatternsBackend._CONTEXT_LENGTH_TOOL_PATTERN is None

    def test_default_tool_pattern_is_set(self):
        """Default backends (OpenRouter/ZAI) DO have a tool input pattern."""
        assert _DefaultPatternsBackend._CONTEXT_LENGTH_TOOL_PATTERN is not None
        assert "tool input" in _DefaultPatternsBackend._CONTEXT_LENGTH_TOOL_PATTERN


# ----------------------------------------------------------------------------
# _handle_context_length_400
# ----------------------------------------------------------------------------

class TestHandleContextLength400:
    """R06.57 (ARCH-03): The orchestration helper."""

    def test_returns_true_and_mutates_body_on_context_length_400(self):
        """On a context-length 400, returns True + mutates body['max_tokens'] + persists."""
        b = _DefaultPatternsBackend()
        error = ('maximum context length is 131072 tokens. '
                 '120000 of text input, 20000 in the output.')
        body = {"max_tokens": 20000}
        result = b._handle_context_length_400(error, body)
        assert result is True
        assert body["max_tokens"] < 20000  # was reduced
        assert b._context_safe_max_tokens == body["max_tokens"]  # persisted

    def test_returns_false_on_non_context_length_400(self):
        """On a 400 that doesn't mention 'context length', returns False + no mutation."""
        b = _DefaultPatternsBackend()
        error = "Model does not support tools"
        body = {"max_tokens": 20000}
        result = b._handle_context_length_400(error, body)
        assert result is False
        assert body["max_tokens"] == 20000  # unchanged
        assert b._context_safe_max_tokens is None  # not persisted

    def test_returns_false_when_safe_max_equals_original(self):
        """If _calculate_safe_max_tokens returns None (already safe), returns False."""
        b = _DefaultPatternsBackend()
        # Construct an error where the computed safe_max >= original max_tokens
        error = ('maximum context length is 262144 tokens. '
                 '85421 of text input, 196608 in the output.')
        body = {"max_tokens": 1000}  # already tiny → safe_max (174675) >= 1000 → None
        result = b._handle_context_length_400(error, body)
        assert result is False
        assert body["max_tokens"] == 1000  # unchanged
        assert b._context_safe_max_tokens is None  # not persisted

    def test_case_insensitive_context_length_match(self):
        """The 'context length' substring check is case-insensitive."""
        b = _DefaultPatternsBackend()
        error = ('MAXIMUM CONTEXT LENGTH IS 131072 TOKENS. '
                 '120000 of text input, 20000 in the output.')
        body = {"max_tokens": 20000}
        result = b._handle_context_length_400(error, body)
        assert result is True


# ----------------------------------------------------------------------------
# 5th cloud backend inheritance
# ----------------------------------------------------------------------------

class TestFifthCloudBackendInheritance:
    """R06.57 (ARCH-03): A hypothetical 5th cloud backend inherits everything."""

    def test_fake_5th_cloud_backend_uses_default_patterns(self):
        """A subclass of OpenAICompatibleBackend with no override inherits default patterns."""
        b = _FakeFifthCloudBackend()
        # Should inherit the default patterns
        assert b._CONTEXT_LENGTH_MAX_PATTERN == r"maximum context length is (\d+) tokens"
        assert b._CONTEXT_LENGTH_INPUT_PATTERN == r"(\d+) of text input"
        assert b._CONTEXT_LENGTH_TOOL_PATTERN == r"(\d+) of tool input"

    def test_fake_5th_cloud_backend_can_parse_openrouter_errors(self):
        """The inherited default patterns parse OpenRouter-format errors correctly."""
        b = _FakeFifthCloudBackend()
        error = ('maximum context length is 262144 tokens. '
                 '85421 of text input, 305 of tool input, 196608 in the output.')
        body = {"max_tokens": 196608}
        result = b._calculate_safe_max_tokens(error, body)
        assert result == 262144 - 85421 - 305 - 2048

    def test_fake_5th_cloud_backend_gets_context_length_400_recovery(self):
        """The inherited _handle_context_length_400 works for a 5th backend."""
        b = _FakeFifthCloudBackend()
        error = ('maximum context length is 131072 tokens. '
                 '120000 of text input, 20000 in the output.')
        body = {"max_tokens": 20000}
        result = b._handle_context_length_400(error, body)
        assert result is True
        assert b._context_safe_max_tokens is not None

    def test_fake_5th_cloud_backend_gets_proactive_cap(self):
        """The inherited _apply_max_tokens_cap works for a 5th backend."""
        b = _FakeFifthCloudBackend()
        result = b._apply_max_tokens_cap(131072, 128000, temperature=0.7)
        assert result["max_tokens"] == 4000  # 128000 // 32
