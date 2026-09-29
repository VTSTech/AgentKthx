"""
Regression tests for R07.07 audit closures.

Each test verifies a specific fix from the R07.07 re-audit delta. The
tests are organized by finding ID (MAINT-14, SEC-12, ROB-16, etc.) so
the audit trail is traceable from finding → fix → test.

Findings closed in this batch (10 total):
  - MAINT-14 (HIGH): tool_parse.py \bTrue\b regex substitutions
  - SEC-12 / ROB-26: sanitize_tool_output truncation-after-redaction
  - ROB-16: OrcaRouter time.sleep(retry_after) unbounded
  - ROB-21: CloudBackend API key min length 8 → 20
  - MAINT-12: CloudBackend 128000 fallback → class attribute
  - MAINT-16: Agent.add_tool DeprecationWarning
  - MAINT-17: dedupe untrusted-tool-output instruction
  - MAINT-13: zai.py list_models use _catalog_family_name()
  - MAINT-20: zai.py get_model_info free_tier redundant set
  - MAINT-09: plugins/_loader.py reject '.' in path parts (defensive)
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helper: minimal concrete CloudBackend subclass for testing
# ---------------------------------------------------------------------------


def _make_test_cloud_backend(**overrides):
    """Build a minimal concrete CloudBackend subclass for testing.

    CloudBackend is abstract (requires generate/generate_stream). This
    factory returns a concrete subclass with no-op implementations so
    tests can exercise __init__ validation without needing a real backend.
    """
    from agentkthx.backends.cloud_base import CloudBackend
    from agentkthx.core.types import BackendType

    class _TestCloudBackend(CloudBackend):
        MODELS = overrides.pop("MODELS", {})
        _api_key_env_var = overrides.pop("_api_key_env_var", "TEST_R07_07_API_KEY")
        _default_base_url = overrides.pop("_default_base_url", "http://localhost")
        _default_model = overrides.pop("_default_model", "test-model")
        _provider_label = overrides.pop("_provider_label", "Test")
        _MIN_API_KEY_LEN = overrides.pop("_MIN_API_KEY_LEN", 20)
        _DEFAULT_CONTEXT_FALLBACK = overrides.pop("_DEFAULT_CONTEXT_FALLBACK", 128000)

        def backend_type(self):
            return BackendType.OLLAMA  # any enum value

        def _get_chat_completions_url(self):
            return "http://localhost/v1/chat/completions"

        def generate(self, *args, **kwargs):
            raise NotImplementedError("test backend")

        def generate_stream(self, *args, **kwargs):
            raise NotImplementedError("test backend")

        def generate_completions_stream(self, *args, **kwargs):
            raise NotImplementedError("test backend")

    return _TestCloudBackend


# ---------------------------------------------------------------------------
# MAINT-14: \bTrue\b / \bFalse\b / \bNone\b substitutions respect strings
# ---------------------------------------------------------------------------


class TestMaint14PythonLiteralSubstitution:
    """MAINT-14 (HIGH): silent data corruption in tool argument parsing.

    The prior ``\\bTrue\\b`` / ``\\bFalse\\b`` / ``\\bNone\\b`` regex substitutions
    in ``_sanitize_model_json`` and the inline ``_parse_react`` python-dict→JSON
    conversion ran on the whole string without respecting string-literal
    boundaries. This silently mangled values containing these words as prose
    (e.g. ``"None of the above is True"`` → ``"null of the above is true"``).

    The fix: a single-pass regex (``_PY_LITERAL_OR_STR_RE``) that matches
    string literals first (and passes them through unchanged) so keywords
    inside string values are never substituted.
    """

    def test_substitute_preserves_true_in_string_value(self):
        """The reproducer case: 'True' inside a string value must survive."""
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"prompt": "None of the above is True"}'
        out = _substitute_python_literals(raw)
        assert out == raw, f"BUG: {out!r} != {raw!r}"

    def test_substitute_preserves_false_in_string_value(self):
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"note": "Result is False"}'
        out = _substitute_python_literals(raw)
        assert out == raw

    def test_substitute_preserves_none_in_string_value(self):
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"msg": "None of your business"}'
        out = _substitute_python_literals(raw)
        assert out == raw

    def test_substitute_converts_bare_true_outside_strings(self):
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"flag": True, "other": False, "nothing": None}'
        out = _substitute_python_literals(raw)
        assert out == '{"flag": true, "other": false, "nothing": null}'

    def test_substitute_converts_array_keywords(self):
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"values": [True, False, None]}'
        out = _substitute_python_literals(raw)
        assert out == '{"values": [true, false, null]}'

    def test_substitute_preserves_quoted_string_values(self):
        from agentkthx.core.tool_parse import _substitute_python_literals

        raw = '{"a": "True", "b": "False", "c": "None"}'
        out = _substitute_python_literals(raw)
        assert out == raw

    def test_sanitize_model_json_preserves_string_with_colon_true(self):
        """``_sanitize_model_json`` also gets the fix — the prior
        ``:\\s*True\\b`` regex would mangle ``": True"`` inside string values."""
        from agentkthx.core.tool_parse import _sanitize_model_json

        raw = '{"note": "Result: True"}'
        out = _sanitize_model_json(raw)
        assert out == raw, f"BUG: {out!r} != {raw!r}"

    def test_sanitize_model_json_converts_real_keywords(self):
        from agentkthx.core.tool_parse import _sanitize_model_json

        raw = '{"flag": True, "other": False}'
        out = _sanitize_model_json(raw)
        # Both should be substituted (the values True/False are outside strings)
        assert "true" in out and "false" in out

    def test_parse_react_full_pipeline_preserves_string_value(self):
        """End-to-end: a Python dict (single-quoted) with 'None' / 'True' in
        a string value parses correctly through the full _parse_react pipeline.

        _parse_react returns (thought, tool_name, tool_args, final_answer).
        """
        from agentkthx.core.tool_parse import _parse_react

        react = (
            "Thought: I need to read the file.\n"
            "Action: read_file\n"
            "Action Input: {'path': 'None_of_the_above_is_True.txt', 'flag': True}"
        )
        thought, tool_name, tool_args, _ = _parse_react(react, ["read_file"])
        assert tool_name == "read_file"
        assert tool_args["path"] == "None_of_the_above_is_True.txt"
        assert tool_args["flag"] is True

    def test_parse_react_string_value_with_true_not_mangled(self):
        """Specific regression: a string value containing 'True' as prose
        must not be mangled to 'true'."""
        from agentkthx.core.tool_parse import _parse_react

        react = "Thought: Test\n" "Action: echo\n" 'Action Input: {"message": "True Believer"}'
        _, tool_name, tool_args, _ = _parse_react(react, ["echo"])
        assert tool_name == "echo"
        assert (
            tool_args["message"] == "True Believer"
        ), f"MAINT-14 regression: 'True Believer' was mangled to {tool_args['message']!r}"


# ---------------------------------------------------------------------------
# SEC-12 / ROB-26: sanitize_tool_output truncates AFTER redaction
# ---------------------------------------------------------------------------


class TestSec12Rob26TruncateBeforeRedact:
    """SEC-12 / ROB-26: sanitize_tool_output ordering fix.

    The prior order was redact→truncate. A secret spanning the truncation
    boundary would not be redacted by the line-based regex (which requires
    ``\\S+`` value to fully match), and the truncated body would end with
    the exposed secret fragment.

    The fix: truncate FIRST, then redact — so the redaction regex sees the
    EXACT bytes that will be returned to the model.
    """

    def test_secret_on_own_line_within_truncation_is_redacted(self):
        """A secret on its own line, within the truncated body, is redacted."""
        from agentkthx.core.helpers import sanitize_tool_output

        body = "Result line\npassword=secret123\nMore text" + "x" * 200
        out = sanitize_tool_output(body, tool_name="shell", tool_call_id="c1", max_chars=100)
        # The secret is within the first 100 chars, so it should be redacted.
        assert "secret123" not in out, f"BUG: secret leaked in truncated output: {out!r}"
        assert "password=[REDACTED]" in out

    def test_secret_just_past_truncation_is_not_in_output(self):
        """A secret past the truncation point is cut away entirely —
        neither the key nor value appears in the output."""
        from agentkthx.core.helpers import sanitize_tool_output

        # 100 chars of padding, then a secret that's past the 100-char truncation.
        body = "x" * 100 + "\npassword=secret_past_truncation"
        out = sanitize_tool_output(body, tool_name="shell", tool_call_id="c1", max_chars=100)
        # The secret is past the truncation point — it's simply gone.
        assert "secret_past_truncation" not in out
        # "password=" should not appear because it's past byte 100
        assert "password=" not in out

    def test_secret_within_truncated_boundary_is_redacted(self):
        """Edge case: secret starts within the truncated boundary."""
        from agentkthx.core.helpers import sanitize_tool_output

        # 50 chars padding + newline + secret = within 120-char truncation
        body = "x" * 50 + "\npassword=boundary_secret" + "y" * 200
        out = sanitize_tool_output(body, tool_name="shell", tool_call_id="c1", max_chars=120)
        assert "boundary_secret" not in out
        assert "password=[REDACTED]" in out

    def test_no_truncation_when_under_max(self):
        """No truncation when body is under max_chars — secret still redacted."""
        from agentkthx.core.helpers import sanitize_tool_output

        body = "password=short_secret"
        out = sanitize_tool_output(body, tool_name="shell", tool_call_id="c1", max_chars=8192)
        assert "short_secret" not in out
        assert "password=[REDACTED]" in out
        assert "truncated" not in out

    def test_bearer_token_within_truncated_body_is_redacted(self):
        """Bearer token within truncated body is redacted."""
        from agentkthx.core.helpers import sanitize_tool_output

        body = "Header line\nBearer abc123token456\nFooter" + "x" * 200
        out = sanitize_tool_output(body, tool_name="http_get", tool_call_id="c1", max_chars=80)
        assert "abc123token456" not in out
        assert "Bearer [REDACTED]" in out


# ---------------------------------------------------------------------------
# ROB-16: OrcaRouter Retry-After cap to 60s
# ---------------------------------------------------------------------------


class TestRob16RetryAfterCap:
    """ROB-16: OrcaRouter ``time.sleep(retry_after)`` was unbounded.

    The prior ``_parse_retry_after_seconds`` returned ``float(header)``
    with no cap. A malicious or buggy upstream returning ``Retry-After: 3600``
    would hang the agent for an hour.

    The fix: cap at ``_MAX_RETRY_AFTER_SECONDS = 60.0``.
    """

    def test_normal_retry_after_passes_through(self):
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds

        assert _parse_retry_after_seconds("", "30") == 30.0
        assert _parse_retry_after_seconds("", "5.5") == 5.5

    def test_excessive_retry_after_capped_at_60(self):
        from agentkthx.plugins.orcarouter.orcarouter import (
            _MAX_RETRY_AFTER_SECONDS,
            _parse_retry_after_seconds,
        )

        # 1 hour → capped to 60s
        result = _parse_retry_after_seconds("", "3600")
        assert result == _MAX_RETRY_AFTER_SECONDS
        assert result == 60.0

        # 1 day → still 60s
        result = _parse_retry_after_seconds("", "86400")
        assert result == 60.0

    def test_negative_retry_after_clamped_to_zero(self):
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds

        # A negative Retry-After is nonsensical — clamp to 0 rather than sleep negatively.
        result = _parse_retry_after_seconds("", "-10")
        assert result == 0.0

    def test_none_header_returns_none(self):
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds

        assert _parse_retry_after_seconds("", None) is None
        assert _parse_retry_after_seconds("", "") is None

    def test_malformed_header_returns_none(self):
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds

        assert _parse_retry_after_seconds("", "not-a-number") is None
        assert _parse_retry_after_seconds("", "abc") is None

    def test_just_under_cap_passes_through(self):
        from agentkthx.plugins.orcarouter.orcarouter import _parse_retry_after_seconds

        # 59.9s — just under cap, passes through unchanged
        assert _parse_retry_after_seconds("", "59.9") == 59.9

    def test_exactly_at_cap_passes_through(self):
        from agentkthx.plugins.orcarouter.orcarouter import (
            _MAX_RETRY_AFTER_SECONDS,
            _parse_retry_after_seconds,
        )

        # Exactly 60s — at cap, passes through
        assert _parse_retry_after_seconds("", "60") == _MAX_RETRY_AFTER_SECONDS

    def test_just_over_cap_is_capped(self):
        from agentkthx.plugins.orcarouter.orcarouter import (
            _MAX_RETRY_AFTER_SECONDS,
            _parse_retry_after_seconds,
        )

        # 60.001s — just over cap, capped
        assert _parse_retry_after_seconds("", "60.001") == _MAX_RETRY_AFTER_SECONDS


# ---------------------------------------------------------------------------
# ROB-21: CloudBackend API key min length 8 → 20
# ---------------------------------------------------------------------------


class TestRob21ApiKeyMinLength:
    """ROB-21: CloudBackend ``_MIN_API_KEY_LEN`` was 8 (too weak).

    Real cloud API keys are 30+ chars (OpenAI ``sk-...`` is 51 chars, ZAI
    is similar). 8 chars only catches the most egregious typos. Bumped to
    20 and made it a class attribute so subclasses can override.
    """

    def test_min_length_is_20(self):
        from agentkthx.backends.cloud_base import CloudBackend

        assert CloudBackend._MIN_API_KEY_LEN == 20

    def test_subclass_can_override_min_length(self, monkeypatch):
        """A subclass can set ``_MIN_API_KEY_LEN = 4`` for a dev sandbox."""
        cls = _make_test_cloud_backend(_MIN_API_KEY_LEN=4)
        monkeypatch.setenv(cls._api_key_env_var, "short")
        b = cls()
        assert b.api_key == "short"

    def test_19_char_key_rejected(self, monkeypatch):
        """A 19-char key (was valid at 8-char threshold) now raises."""
        cls = _make_test_cloud_backend()
        monkeypatch.setenv(cls._api_key_env_var, "x" * 19)
        with pytest.raises(ValueError, match=r"too short"):
            cls()

    def test_20_char_key_accepted(self, monkeypatch):
        """A 20-char key (exactly at threshold) is accepted."""
        cls = _make_test_cloud_backend()
        monkeypatch.setenv(cls._api_key_env_var, "x" * 20)
        b = cls()
        assert b.api_key == "x" * 20


# ---------------------------------------------------------------------------
# MAINT-12: CloudBackend 128000 fallback is a class attribute
# ---------------------------------------------------------------------------


class TestMaint12ContextFallbackAttribute:
    """MAINT-12: CloudBackend hardcoded ``128000`` at 4 sites.

    Now a single class attribute ``_DEFAULT_CONTEXT_FALLBACK = 128000`` so
    backends with smaller models (e.g. a 4K-context model) can override.
    """

    def test_default_is_128000(self):
        from agentkthx.backends.cloud_base import CloudBackend

        assert CloudBackend._DEFAULT_CONTEXT_FALLBACK == 128000

    def test_subclass_can_override(self):
        from agentkthx.backends.cloud_base import CloudBackend

        class _SmallModelBackend(CloudBackend):
            _DEFAULT_CONTEXT_FALLBACK = 4096

        assert _SmallModelBackend._DEFAULT_CONTEXT_FALLBACK == 4096

    def test_get_model_max_context_uses_fallback(self, monkeypatch):
        """When catalog lookup fails and live cache is empty, the fallback
        value comes from the class attribute."""
        cls = _make_test_cloud_backend(_DEFAULT_CONTEXT_FALLBACK=8192)
        monkeypatch.setenv(cls._api_key_env_var, "x" * 25)
        b = cls()
        # Unknown model + empty catalog → fallback
        ctx = b.get_model_max_context("unknown-model")
        assert ctx == 8192, f"Expected 8192 (overridden fallback), got {ctx}"


# ---------------------------------------------------------------------------
# MAINT-16: Agent.add_tool emits DeprecationWarning
# ---------------------------------------------------------------------------


class TestMaint16AddToolDeprecationWarning:
    """MAINT-16: ``Agent.add_tool`` was deprecated in R07.05 but emitted no
    ``DeprecationWarning``. Callers had no programmatic signal to migrate.

    The fix: emit ``DeprecationWarning`` with ``stacklevel=2`` so the
    warning points at the caller, not at ``add_tool`` itself.
    """

    def _make_agent(self):
        from unittest.mock import MagicMock

        from agentkthx.agent import Agent
        from agentkthx.tools import make_builtin_registry

        mock_backend = MagicMock()
        mock_backend.backend_type = MagicMock()
        mock_backend.backend_type.value = "test"
        mock_backend.api_mode = MagicMock()
        mock_backend.api_mode.value = "openai"
        mock_backend.is_cloud = False
        mock_backend.config = MagicMock(timeout=120)

        return Agent(
            model="test-model",
            tools=make_builtin_registry(),
            backend=mock_backend,
            debug=False,
        )

    def test_add_tool_emits_deprecation_warning(self):
        import warnings as _w

        from agentkthx.core.models import Tool, ToolParam

        agent = self._make_agent()
        new_tool = Tool(
            name="test_deprecation",
            description="Test",
            params=[ToolParam(name="text", type="string")],
            handler=lambda text="": text,
        )

        # add_tool should emit exactly one DeprecationWarning
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            agent.add_tool(new_tool)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Expected at least one DeprecationWarning"
        # The warning message should mention register_tool
        msg = str(deprecation_warnings[0].message)
        assert "register_tool" in msg, f"Warning should mention register_tool: {msg!r}"

    def test_register_tool_does_not_emit_warning(self):
        """``register_tool`` (the recommended replacement) does NOT warn."""
        import warnings as _w

        from agentkthx.core.models import Tool, ToolParam

        agent = self._make_agent()
        new_tool = Tool(
            name="test_no_warn",
            description="Test",
            params=[ToolParam(name="text", type="string")],
            handler=lambda text="": text,
        )

        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            agent.register_tool(new_tool)

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0, "register_tool should not emit DeprecationWarning"


# ---------------------------------------------------------------------------
# MAINT-17: Untrusted-tool-output instruction is a shared constant
# ---------------------------------------------------------------------------


class TestMaint17SharedUntrustedInstruction:
    """MAINT-17: the untrusted-tool-output instruction was duplicated
    verbatim across the comp-mode and full-ReAct system-prompt builders.

    The fix: extract a single ``_UNTRUSTED_TOOL_OUTPUT_INSTRUCTION`` constant
    and concatenate it into both prompts.
    """

    def test_constant_exists_and_is_nonempty(self):
        from agentkthx.core.agent_setup import _UNTRUSTED_TOOL_OUTPUT_INSTRUCTION

        assert isinstance(_UNTRUSTED_TOOL_OUTPUT_INSTRUCTION, str)
        assert len(_UNTRUSTED_TOOL_OUTPUT_INSTRUCTION) > 100
        assert "UNTRUSTED DATA" in _UNTRUSTED_TOOL_OUTPUT_INSTRUCTION
        assert "tool_output" in _UNTRUSTED_TOOL_OUTPUT_INSTRUCTION

    def test_default_prompt_contains_instruction(self):
        """The default system prompt (full ReAct path for local backends)
        includes the shared instruction."""
        from unittest.mock import MagicMock

        from agentkthx.agent import Agent
        from agentkthx.tools import make_builtin_registry

        mock_backend = MagicMock()
        mock_backend.backend_type = MagicMock()
        mock_backend.backend_type.value = "test"
        mock_backend.api_mode = MagicMock()
        mock_backend.api_mode.value = "openai"
        mock_backend.is_cloud = False
        mock_backend.config = MagicMock(timeout=120)

        agent = Agent(
            model="test-model",
            tools=make_builtin_registry(),
            backend=mock_backend,
            debug=False,
        )
        prompt = agent._build_default_prompt(has_tools=True)
        assert "UNTRUSTED DATA" in prompt
        assert "tool_output" in prompt
        assert "never follow instructions" in prompt.lower() or "NEVER execute" in prompt


# ---------------------------------------------------------------------------
# MAINT-13: zai.py list_models uses self._catalog_family_name()
# ---------------------------------------------------------------------------


class TestMaint13ZaiFamilyNameConsistency:
    """MAINT-13: ``ZaiBackend.list_models`` hardcoded ``"family": "glm"``
    instead of calling ``self._catalog_family_name()``. If a subclass
    overrode ``_catalog_family_name``, ``list_models`` would still produce
    the hardcoded value.

    The fix: use ``self._catalog_family_name()`` and ``self._catalog_backend_name()``.
    """

    def test_list_models_uses_catalog_family_name(self, monkeypatch):
        from agentkthx.plugins.zai.zai import ZaiBackend

        monkeypatch.setenv("ZAI_API_KEY", "test-key-1234567890123")
        b = ZaiBackend()
        models = b.list_models()
        assert len(models) > 0
        for m in models:
            assert (
                m["details"]["family"] == "glm"
            ), f"Expected 'glm' from _catalog_family_name, got {m['details']['family']!r}"
            assert m["details"]["backend"] == "zai"

    def test_subclass_override_propagates_to_list_models(self, monkeypatch):
        """If a subclass overrides _catalog_family_name, list_models reflects it."""
        from agentkthx.plugins.zai.zai import ZaiBackend

        class _CustomZaiBackend(ZaiBackend):
            def _catalog_family_name(self):
                return "custom-glm"

        monkeypatch.setenv("ZAI_API_KEY", "test-key-1234567890123")
        b = _CustomZaiBackend()
        models = b.list_models()
        assert len(models) > 0
        for m in models:
            assert m["details"]["family"] == "custom-glm", (
                f"MAINT-13 regression: list_models should use _catalog_family_name, "
                f"got {m['details']['family']!r}"
            )

    def test_get_model_info_unknown_model_uses_catalog_family_name(self, monkeypatch):
        """get_model_info for an unknown model uses _catalog_family_name()."""
        from agentkthx.plugins.zai.zai import ZaiBackend

        monkeypatch.setenv("ZAI_API_KEY", "test-key-1234567890123")
        b = ZaiBackend()
        info = b.get_model_info("nonexistent-model-xyz")
        assert info is not None
        assert info["details"]["family"] == "glm"
        assert info["details"]["backend"] == "zai"


# ---------------------------------------------------------------------------
# MAINT-20: zai.py get_model_info no longer redundantly sets free_tier
# ---------------------------------------------------------------------------


class TestMaint20ZaiFreeTierNotRedundant:
    """MAINT-20: ``ZaiBackend.get_model_info`` re-set ``free_tier`` even
    though the parent ``CloudBackend.get_model_info`` already sets it.

    The fix: skip the redundant assignment; only enrich with the
    ZAI-specific fields (``is_chat_model``, ``pricing``) the parent
    doesn't know about.
    """

    def test_known_model_has_free_tier_from_parent(self, monkeypatch):
        """A catalog-known model gets free_tier from the parent (not the override)."""
        from agentkthx.plugins.zai.zai import ZaiBackend

        monkeypatch.setenv("ZAI_API_KEY", "test-key-1234567890123")
        b = ZaiBackend()
        # glm-5.3-flash is in the ZAI catalog
        info = b.get_model_info("glm-5.3-flash")
        assert info is not None
        # free_tier should be present (from parent CloudBackend.get_model_info)
        assert "free_tier" in info["details"]
        # is_chat_model and pricing are ZAI-specific enrichments
        assert info["details"]["is_chat_model"] is True
        assert "pricing" in info["details"]

    def test_free_tier_value_matches_is_free_model(self, monkeypatch):
        """The free_tier value in get_model_info matches _is_free_model."""
        from agentkthx.plugins.zai.zai import ZaiBackend

        monkeypatch.setenv("ZAI_API_KEY", "test-key-1234567890123")
        b = ZaiBackend()
        for model_name in ["glm-5.3-flash", "glm-5.1", "glm-4.7-flash"]:
            info = b.get_model_info(model_name)
            assert info is not None, f"No info for {model_name}"
            expected = b._is_free_model(model_name)
            actual = info["details"]["free_tier"]
            assert actual == expected, (
                f"{model_name}: get_model_info says free_tier={actual}, "
                f"_is_free_model says {expected}"
            )


# ---------------------------------------------------------------------------
# MAINT-09: plugins/_loader.py path validation (defensive '.' check)
# ---------------------------------------------------------------------------


class TestMaint09Sha256PinPathValidation:
    """MAINT-09: ``_validate_sha256_pin`` rejects unsafe path parts.

    Note: Python's ``Path`` already collapses ``.`` parts, so the ``'.' in
    Path(fname).parts`` check is defensive (belt-and-braces). The primary
    protection against path traversal is the ``..`` check and the
    verify-time ``target.resolve().is_relative_to(root)`` check. This test
    suite verifies all the path-traversal defenses work correctly.
    """

    def _valid_hash(self):
        return "a" * 64

    def test_dotdot_path_part_rejected(self):
        from agentkthx.plugins._loader import _validate_sha256_pin

        with pytest.raises(ValueError, match=r"relative paths inside"):
            _validate_sha256_pin({"../escape.py": self._valid_hash()}, "test-plugin")

    def test_absolute_path_rejected(self):
        from agentkthx.plugins._loader import _validate_sha256_pin

        with pytest.raises(ValueError, match=r"relative paths inside"):
            _validate_sha256_pin({"/etc/passwd": self._valid_hash()}, "test-plugin")

    def test_backslash_path_rejected(self):
        from agentkthx.plugins._loader import _validate_sha256_pin

        with pytest.raises(ValueError, match=r"relative paths inside"):
            _validate_sha256_pin({"foo\\bar.py": self._valid_hash()}, "test-plugin")

    def test_clean_relative_path_accepted(self):
        from agentkthx.plugins._loader import _validate_sha256_pin

        pin = _validate_sha256_pin({"subdir/file.py": self._valid_hash()}, "test-plugin")
        assert pin == {"subdir/file.py": self._valid_hash()}

    def test_single_filename_accepted(self):
        from agentkthx.plugins._loader import _validate_sha256_pin

        pin = _validate_sha256_pin({"__init__.py": self._valid_hash()}, "test-plugin")
        assert pin == {"__init__.py": self._valid_hash()}

    def test_nested_relative_path_accepted(self):
        """A deeply nested relative path (no traversal) is accepted."""
        from agentkthx.plugins._loader import _validate_sha256_pin

        pin = _validate_sha256_pin(
            {"subdir/deeper/even_deeper/file.py": self._valid_hash()},
            "test-plugin",
        )
        assert "subdir/deeper/even_deeper/file.py" in pin
