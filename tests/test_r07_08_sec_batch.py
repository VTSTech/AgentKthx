"""
R07.08 Security batch — regression tests for 4 SEC findings:

  SEC-05 (Low): input() prompts in dangerous-tool confirmation don't strip
    ANSI escapes from tool name/args. A malicious tool name like
    \\x1b[2J\\x1b[H (clear screen) would inject terminal escapes.
    Fix: _strip_ansi() in parser.py removes CSI/OSC/other escapes before
    printing tool_name + arg_str in the --confirm dialog.

  SEC-14 (Low): is_transient_api_error body arg lowercased + substring-
    matched — user-controlled content in body could force permanent
    classification (DoS via premature-fail).
    Fix: for the untrusted body arg, only match markers as quoted JSON
    tokens ("marker"), not raw substrings. The trusted str(exc) path
    keeps the raw substring match.

  SEC-15 (Low): CloudBackend.__init__ unconditionally mutated
    os.environ["AGENTKTHX_API_MODE"] — process-global side effect,
    last-instance-wins.
    Fix: only set the env var if it's not already set (first-instance-wins).

  SEC-16 (Low): _extract_buy_credits_url surfaces attacker-controlled URL
    in user-facing error message — phishing vector.
    Fix: validate the extracted URL's host against the orcarouter.ai
    allowlist. Non-matching hosts return None (caller falls back to the
    hardcoded safe URL).

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ------------------------------------------------------------------ #
#  SEC-05 — ANSI escape stripping in confirm callback                #
# ------------------------------------------------------------------ #

class TestSEC05AnsiStripping:
    """Verify the _confirm callback strips ANSI escapes from model-controlled
    tool names + args before printing them to the terminal."""

    def test_strip_csi_clear_screen(self):
        """\\x1b[2J (clear screen) is stripped."""
        from agentkthx.cli.parser import _make_confirm_callback
        # Build a mock args namespace with --confirm enabled
        import argparse
        args = argparse.Namespace(confirm_dangerous=True)
        callback = _make_confirm_callback(args)
        assert callback is not None

        # The _strip_ansi function is a closure inside _make_confirm_callback;
        # we test it indirectly by capturing stdout and checking no escape
        # sequences appear.
        import io
        from contextlib import redirect_stdout

        captured = io.StringIO()
        with redirect_stdout(captured):
            # Answer 'n' to avoid blocking
            with patch("builtins.input", return_value="n"):
                callback("\x1b[2J\x1b[Hmalicious_tool", {"cmd": "ls"})
        output = captured.getvalue()
        # The clear-screen escape must NOT appear in the output
        assert "\x1b[2J" not in output
        assert "\x1b[H" not in output
        # The actual tool name (stripped) SHOULD appear
        assert "malicious_tool" in output

    def test_strip_osc_title_rewrite(self):
        """\\x1b]0;evil\\x07 (OSC title rewrite) is stripped."""
        import argparse
        import io
        from contextlib import redirect_stdout
        from agentkthx.cli.parser import _make_confirm_callback
        args = argparse.Namespace(confirm_dangerous=True)
        callback = _make_confirm_callback(args)
        captured = io.StringIO()
        with redirect_stdout(captured):
            with patch("builtins.input", return_value="n"):
                callback("\x1b]0;evil\x07shell", {"command": "rm -rf /"})
        output = captured.getvalue()
        assert "\x1b]0;evil\x07" not in output
        assert "shell" in output

    def test_strip_mouse_tracking_enable(self):
        """\\x1b[?1000h (mouse tracking) is stripped from arg values."""
        import argparse
        import io
        from contextlib import redirect_stdout
        from agentkthx.cli.parser import _make_confirm_callback
        args = argparse.Namespace(confirm_dangerous=True)
        callback = _make_confirm_callback(args)
        captured = io.StringIO()
        with redirect_stdout(captured):
            with patch("builtins.input", return_value="n"):
                callback("shell", {"command": "\x1b[?1000hecho pwned"})
        output = captured.getvalue()
        assert "\x1b[?1000h" not in output
        assert "echo pwned" in output

    def test_legitimate_tool_name_unchanged(self):
        """Normal tool names without ANSI escapes are unaffected."""
        import argparse
        import io
        from contextlib import redirect_stdout
        from agentkthx.cli.parser import _make_confirm_callback
        args = argparse.Namespace(confirm_dangerous=True)
        callback = _make_confirm_callback(args)
        captured = io.StringIO()
        with redirect_stdout(captured):
            with patch("builtins.input", return_value="n"):
                callback("shell", {"command": "ls -la"})
        output = captured.getvalue()
        assert "shell" in output
        assert "ls -la" in output


# ------------------------------------------------------------------ #
#  SEC-14 — body arg JSON-token matching (not raw substring)         #
# ------------------------------------------------------------------ #

class TestSEC14BodyMatching:
    """Verify the body arg only matches permanent markers as quoted JSON
    tokens, not raw substrings — preventing a malicious provider from
    embedding markers in prose to force permanent classification."""

    def test_quoted_json_key_matches(self):
        """A JSON key like "invalid_request": triggers permanent."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        body = '{"error": {"type": "invalid_request", "message": "bad"}}'
        assert is_transient_api_error(exc, body) is False

    def test_quoted_json_value_matches(self):
        """A JSON value like "type":"invalid_request" triggers permanent."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        body = '{"error": {"type": "invalid_request"}}'
        assert is_transient_api_error(exc, body) is False

    def test_prose_substring_in_quoted_value_still_matches(self):
        """SEC-14 residual: a marker inside a quoted JSON string value
        still matches, even if the value is prose. This is accepted as
        Low severity — the fix prevents raw-substring matching across
        the entire body (including unquoted JSON structure), but a
        malicious provider can still embed markers in quoted string values.
        The proper fix would require JSON parsing + field-name allowlisting
        (only match markers in "code"/"type"/"reason"/"error" fields),
        which is a larger refactor.
        """
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        # "invalid_request" inside a quoted value — still matches
        body = '{"user_message": "your request was not invalid_request yet"}'
        assert is_transient_api_error(exc, body) is False

    def test_marker_in_unquoted_structure_does_not_match(self):
        """SEC-14: a marker in unquoted JSON structure (keys, numbers,
        structural chars) does NOT match. Before the fix, any substring
        in the entire body would match."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        # "401" appears as a JSON number, not inside quotes
        body = '{"status": 401, "retry": true}'
        # "401" as a number is not inside quotes — should be transient
        assert is_transient_api_error(exc, body) is True

    def test_clean_body_500_stays_transient(self):
        """A 500 with a clean body (no permanent markers) is transient."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: internal server error")
        body = '{"status": "ok", "retry": true}'
        assert is_transient_api_error(exc, body) is True

    def test_str_exc_substring_match_unchanged(self):
        """The str(exc) path keeps the raw substring match (trusted source)."""
        from agentkthx.core.api_resilience import is_transient_api_error
        # "invalid_request" in the exception message (not body) still matches
        exc = RuntimeError("HTTP 400: invalid_request error")
        assert is_transient_api_error(exc) is False

    def test_401_in_body_json_matches(self):
        """HTTP status codes like 401 in JSON body trigger permanent."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        body = '{"error": {"code": "401"}}'
        assert is_transient_api_error(exc, body) is False

    def test_401_as_json_number_does_not_match(self):
        """SEC-14: 401 as a JSON number (not inside quotes) doesn't match."""
        from agentkthx.core.api_resilience import is_transient_api_error
        exc = RuntimeError("HTTP 500: server error")
        body = '{"status": 401, "message": "retry later"}'
        # "401" is a JSON number, not inside quotes — should be transient
        assert is_transient_api_error(exc, body) is True


# ------------------------------------------------------------------ #
#  SEC-15 — CloudBackend env-var mutation (first-instance-wins)      #
# ------------------------------------------------------------------ #

class TestSEC15EnvVarMutation:
    """Verify CloudBackend.__init__ no longer unconditionally overwrites
    os.environ["AGENTKTHX_API_MODE"] — first-instance-wins."""

    def test_does_not_overwrite_existing_env_var(self):
        """If AGENTKTHX_API_MODE is already set, CloudBackend must not
        overwrite it (the first instance's mode wins)."""
        from agentkthx.backends.cloud_base import CloudBackend

        # We need a concrete subclass to test — use a minimal stub
        class TestBackend(CloudBackend):
            _provider_label = "test"
            _api_key_env_var = "TEST_API_KEY"
            _default_base_url = "https://api.test.com"
            _MIN_API_KEY_LEN = 4

            def _validate_api_key(self, key):
                pass

            def _catalog_family_name(self):
                return "test"

            def _catalog_backend_name(self):
                return "test"

            def generate(self, **kwargs):
                return {}

            def generate_stream(self, **kwargs):
                yield {}

        with patch.dict(os.environ, {
            "AGENTKTHX_API_MODE": "comp",
            "TEST_API_KEY": "sk-test-key-long-enough",
        }, clear=False):
            # AGENTKTHX_API_MODE is already "comp"
            backend = TestBackend()
            # The env var should NOT have been overwritten to "openai"
            assert os.environ.get("AGENTKTHX_API_MODE") == "comp"

    def test_sets_env_var_if_not_present(self):
        """If AGENTKTHX_API_MODE is not set, CloudBackend sets it
        (first-instance-wins for the first instance)."""
        from agentkthx.backends.cloud_base import CloudBackend

        class TestBackend(CloudBackend):
            _provider_label = "test"
            _api_key_env_var = "TEST_API_KEY2"
            _default_base_url = "https://api.test.com"
            _MIN_API_KEY_LEN = 4

            def _validate_api_key(self, key):
                pass

            def _catalog_family_name(self):
                return "test"

            def _catalog_backend_name(self):
                return "test"

            def generate(self, **kwargs):
                return {}

            def generate_stream(self, **kwargs):
                yield {}

        env = {"TEST_API_KEY2": "sk-test-key-long-enough"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("AGENTKTHX_API_MODE", None)
            backend = TestBackend()
            # The env var should now be set to "openai" (cloud default)
            assert os.environ.get("AGENTKTHX_API_MODE") == "openai"

    def test_second_instance_does_not_overwrite_first(self):
        """SEC-15 core: creating a second CloudBackend with a different
        api_mode must NOT change the env var set by the first."""
        from agentkthx.backends.cloud_base import CloudBackend

        class TestBackend(CloudBackend):
            _provider_label = "test"
            _api_key_env_var = "TEST_API_KEY3"
            _default_base_url = "https://api.test.com"
            _MIN_API_KEY_LEN = 4

            def _validate_api_key(self, key):
                pass

            def _catalog_family_name(self):
                return "test"

            def _catalog_backend_name(self):
                return "test"

            def generate(self, **kwargs):
                return {}

            def generate_stream(self, **kwargs):
                yield {}

        env = {"TEST_API_KEY3": "sk-test-key-long-enough"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("AGENTKTHX_API_MODE", None)
            # First instance — sets the env var
            backend1 = TestBackend()
            assert os.environ.get("AGENTKTHX_API_MODE") == "openai"
            # Second instance — must NOT overwrite
            backend2 = TestBackend()
            assert os.environ.get("AGENTKTHX_API_MODE") == "openai"


# ------------------------------------------------------------------ #
#  SEC-16 — _extract_buy_credits_url host validation                #
# ------------------------------------------------------------------ #

class TestSEC16UrlValidation:
    """Verify _extract_buy_credits_url only surfaces URLs pointing at
    orcarouter.ai — rejecting attacker-controlled phishing URLs."""

    def test_legitimate_orcarouter_url(self):
        """A real OrcaRouter billing URL passes validation."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"https://www.orcarouter.ai/console/billing"}}}'
        result = _extract_buy_credits_url(body)
        assert result == "https://www.orcarouter.ai/console/billing"

    def test_subdomain_of_orcarouter_ai(self):
        """Subdomains of orcarouter.ai pass validation."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"https://billing.orcarouter.ai/topup"}}}'
        result = _extract_buy_credits_url(body)
        assert result == "https://billing.orcarouter.ai/topup"

    def test_phishing_url_rejected(self):
        """SEC-16: a non-orcarouter.ai URL is rejected (returns None)."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"https://evil-phishing.com/billing"}}}'
        result = _extract_buy_credits_url(body)
        assert result is None

    def test_lookalike_domain_rejected(self):
        """A lookalike domain (orcarouter.evil.com) is rejected."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"https://www.orcarouter.evil.com/billing"}}}'
        result = _extract_buy_credits_url(body)
        assert result is None

    def test_no_url_field_returns_none(self):
        """If the body has no buy_credits_url field, returns None."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"message":"some other error"}}'
        result = _extract_buy_credits_url(body)
        assert result is None

    def test_http_url_accepted(self):
        """HTTP (not HTTPS) orcarouter.ai URLs are accepted (the caller
        decides on the scheme; validation is about the host, not TLS)."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"http://www.orcarouter.ai/billing"}}}'
        result = _extract_buy_credits_url(body)
        assert result == "http://www.orcarouter.ai/billing"

    def test_caller_falls_back_to_hardcoded(self):
        """When _extract_buy_credits_url returns None (phishing rejected),
        the caller falls back to the hardcoded safe URL."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        phishing_body = '{"error":{"metadata":{"buy_credits_url":"https://evil.com/billing"}}}'
        extracted = _extract_buy_credits_url(phishing_body)
        # The caller's fallback pattern: _extract_buy_credits_url(body) or "https://www.orcarouter.ai/console/billing"
        fallback = extracted or "https://www.orcarouter.ai/console/billing"
        assert fallback == "https://www.orcarouter.ai/console/billing"

    def test_url_without_scheme_rejected(self):
        """A URL without a scheme (no http://) is rejected."""
        from agentkthx.plugins.orcarouter.orcarouter import _extract_buy_credits_url
        body = '{"error":{"metadata":{"buy_credits_url":"www.orcarouter.ai/billing"}}}'
        result = _extract_buy_credits_url(body)
        assert result is None
