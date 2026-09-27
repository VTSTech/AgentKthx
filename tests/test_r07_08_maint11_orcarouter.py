"""
R07.08 Maintainability batch — regression tests for the OrcaRouter MAINT-11
refactor (the OrcaRouter half of the MAINT-11 ID collision):

  MAINT-11 (Medium, OrcaRouter): ~150 LOC of retry-recovery logic was
    duplicated verbatim between ``_generate_with_auth`` and
    ``_iter_sse_lines`` in ``plugins/orcarouter/orcarouter.py``. Extracted
    a shared ``_classify_and_handle_http_error`` helper + ``_HttpErrorAction``
    action object so both call sites route through one classifier.

These tests pin the helper directly (no live HTTP) — covering the three
action kinds (raise / retry / fallthrough), the terminal-vs-retryable
free-tier split, the fallback-model swap, the Retry-After sleep, the
access_denied fatal path, and the fallthrough for non-free-tier errors.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.orcarouter.orcarouter import (
    OrcaRouterBackend,
    _HttpErrorAction,
    ORCAROUTER_FREE_FALLBACK_MODEL,
)


# ------------------------------------------------------------------ #
#  Test fixture: a minimal OrcaRouterBackend that skips __init__      #
# ------------------------------------------------------------------ #

@pytest.fixture
def backend():
    """A OrcaRouterBackend with __init__ skipped (avoids needing an API key).

    _classify_and_handle_http_error doesn't touch any instance state
    beyond what the caller passes in, so this is safe.
    """
    return OrcaRouterBackend.__new__(OrcaRouterBackend)


# ------------------------------------------------------------------ #
#  _HttpErrorAction — the action object itself                        #
# ------------------------------------------------------------------ #

class TestHttpErrorAction:
    def test_retry_factory(self):
        a = _HttpErrorAction.retry()
        assert a.kind == "retry"
        assert a.error is None

    def test_raise_factory(self):
        exc = RuntimeError("boom")
        a = _HttpErrorAction.raise_(exc)
        assert a.kind == "raise"
        assert a.error is exc

    def test_fallthrough_factory(self):
        a = _HttpErrorAction.fallthrough()
        assert a.kind == "fallthrough"
        assert a.error is None

    def test_repr(self):
        assert repr(_HttpErrorAction.retry()) == "_HttpErrorAction(kind='retry')"

    def test_slots_no_dict(self):
        """__slots__ prevents attribute dict (memory + typo guard)."""
        a = _HttpErrorAction.retry()
        with pytest.raises(AttributeError):
            a.foo = 1  # type: ignore[attr-defined]


# ------------------------------------------------------------------ #
#  _classify_and_handle_http_error — terminal free-tier → RAISE        #
# ------------------------------------------------------------------ #

class TestTerminalFreeTierRaises:
    """err_free_used / free_quota_exhausted / err_free_access_denied /
    err_free_prompt_cap are terminal — the helper must RAISE, not retry."""

    @pytest.mark.parametrize("reason", [
        "err_free_used",
        "free_quota_exhausted",
        "err_free_access_denied",
        "err_free_prompt_cap",
    ])
    def test_terminal_raises(self, backend, reason):
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        action = backend._classify_and_handle_http_error(
            error_body=f'{{"error":{{"reason":"{reason}"}}}}',
            error_msg=f'{{"error":{{"reason":"{reason}"}}}}',
            http_code=429,
            headers={"Retry-After": "5"},
            body=body,
            attempt=0,
        )
        assert action.kind == "raise", f"{reason} should be terminal (raise)"
        assert isinstance(action.error, RuntimeError)
        # the remedy mentions the buy-credits URL + the daily cap
        msg = str(action.error)
        assert "buy_credits_url" in msg.lower() or "orcarouter.ai/console" in msg.lower()
        assert "free-tier access denied" in msg.lower()

    def test_access_denied_remedy_differs(self, backend):
        """err_free_access_denied mentions GitHub linking specifically."""
        body = {"model": "orcarouter/free"}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":{"reason":"err_free_access_denied"}}',
            error_msg='{"error":{"reason":"err_free_access_denied"}}',
            http_code=429,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "raise"
        msg = str(action.error)
        assert "github" in msg.lower(), "access_denied remedy must mention GitHub"

    def test_terminal_does_not_mutate_body(self, backend):
        """Terminal errors must NOT swap the body's model (no point — the
        gate applies to ALL free models)."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        backend._classify_and_handle_http_error(
            error_body='{"error":{"reason":"err_free_used"}}',
            error_msg='{"error":{"reason":"err_free_used"}}',
            http_code=429,
            headers={"Retry-After": "5"},
            body=body,
            attempt=0,
        )
        assert body["model"] == "deepseek/deepseek-v4-flash-free", "terminal must not swap"

    def test_terminal_does_not_sleep(self, backend):
        """Terminal errors must raise immediately — no time.sleep."""
        body = {"model": "orcarouter/free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep") as mock_sleep:
            action = backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"free_quota_exhausted"}}',
                error_msg='{"error":{"reason":"free_quota_exhausted"}}',
                http_code=429,
                headers={"Retry-After": "5"},
                body=body,
                attempt=0,
            )
            assert action.kind == "raise"
            assert mock_sleep.call_count == 0, "terminal must not sleep"


# ------------------------------------------------------------------ #
#  _classify_and_handle_http_error — retryable free-tier → RETRY       #
# ------------------------------------------------------------------ #

class TestRetryableFreeTierRetries:
    """err_free_rate / free_rate_limited are retryable — swap to fallback,
    sleep Retry-After, return RETRY."""

    @pytest.mark.parametrize("reason", ["err_free_rate", "free_rate_limited"])
    def test_retryable_returns_retry(self, backend, reason):
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep"):
            action = backend._classify_and_handle_http_error(
                error_body=f'{{"error":{{"reason":"{reason}"}}}}',
                error_msg=f'{{"error":{{"reason":"{reason}"}}}}',
                http_code=429,
                headers={"Retry-After": "2"},
                body=body,
                attempt=0,
            )
        assert action.kind == "retry", f"{reason} should be retryable"

    def test_retryable_swaps_to_fallback_model(self, backend):
        """If the current model isn't the fallback, swap it."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep"):
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "1"},
                body=body,
                attempt=0,
            )
        assert body["model"] == ORCAROUTER_FREE_FALLBACK_MODEL

    def test_retryable_no_swap_when_already_on_fallback(self, backend):
        """If already on the fallback, don't swap — just wait + retry."""
        body = {"model": ORCAROUTER_FREE_FALLBACK_MODEL}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep"):
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "1"},
                body=body,
                attempt=0,
            )
        assert body["model"] == ORCAROUTER_FREE_FALLBACK_MODEL, "must not swap off fallback"

    def test_retryable_sleeps_retry_after(self, backend):
        """When Retry-After is present, sleep exactly that many seconds."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep") as mock_sleep:
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "7"},
                body=body,
                attempt=0,
            )
        mock_sleep.assert_called_once_with(7.0)

    def test_retryable_no_retry_after_sleeps_10s(self, backend):
        """When Retry-After is absent, sleep the fixed 10s window."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep") as mock_sleep:
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={},
                body=body,
                attempt=0,
            )
        mock_sleep.assert_called_once_with(10)

    def test_retryable_caps_retry_after_at_60s(self, backend):
        """ROB-16: Retry-After > 60s is capped (malicious upstream guard)."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep") as mock_sleep:
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "3600"},
                body=body,
                attempt=0,
            )
        mock_sleep.assert_called_once_with(60.0)

    def test_retryable_attempt_3_fallthrough(self, backend):
        """When the retry budget is exhausted (attempt >= 3), fall through
        to the caller's generic 'exhausted retries' raise rather than
        retrying forever."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep"):
            action = backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "1"},
                body=body,
                attempt=3,
            )
        assert action.kind == "fallthrough"


# ------------------------------------------------------------------ #
#  _classify_and_handle_http_error — 401/403 access_denied → RAISE     #
# ------------------------------------------------------------------ #

class TestAccessDeniedRaises:
    @pytest.mark.parametrize("code", [401, 403])
    def test_access_denied_raises(self, backend, code):
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":{"code":"access_denied"}}',
            error_msg='{"error":{"code":"access_denied"}}',
            http_code=code,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "raise"
        msg = str(action.error)
        assert "access denied" in msg.lower()
        assert f"code={code}" in msg
        assert "ORCAROUTER_API_KEY" in msg

    def test_403_without_access_denied_fallthrough(self, backend):
        """403 with a non-access_denied body is NOT the access_denied path —
        falls through to the caller's generic raise."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":"forbidden for some other reason"}',
            error_msg='{"error":"forbidden for some other reason"}',
            http_code=403,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "fallthrough"


# ------------------------------------------------------------------ #
#  _classify_and_handle_http_error — fallthrough for everything else   #
# ------------------------------------------------------------------ #

class TestFallthrough:
    def test_generic_500_fallthrough(self, backend):
        """A 500 that isn't a free-tier error falls through — the caller's
        own 'generic HTTP error' raise handles it."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":"internal server error"}',
            error_msg='{"error":"internal server error"}',
            http_code=500,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "fallthrough"

    def test_400_context_length_fallthrough(self, backend):
        """Context-length 400 is handled by the caller's own
        _handle_context_length_400 branch — helper falls through."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":{"message":"context_length_exceeded"}}',
            error_msg='{"error":{"message":"context_length_exceeded"}}',
            http_code=400,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "fallthrough"

    def test_no_tools_support_fallthrough(self, backend):
        """The 'does not support tools' 400 is handled by the caller's own
        no-tools-fallback branch — helper falls through."""
        body = {"model": "deepseek/deepseek-v4-flash-free", "tools": [...]}
        action = backend._classify_and_handle_http_error(
            error_body='{"error":{"message":"model does not support tools"}}',
            error_msg='{"error":{"message":"model does not support tools"}}',
            http_code=400,
            headers={},
            body=body,
            attempt=0,
        )
        assert action.kind == "fallthrough"


# ------------------------------------------------------------------ #
#  log_tag parameterization (the only caller difference)              #
# ------------------------------------------------------------------ #

class TestLogTag:
    def test_log_tag_appears_in_output(self, backend, capsys):
        """The log_tag must appear in the stderr output so users can tell
        which path (streaming vs non-streaming) emitted the message."""
        body = {"model": "deepseek/deepseek-v4-flash-free"}
        with patch("agentkthx.plugins.orcarouter.orcarouter.time.sleep"):
            backend._classify_and_handle_http_error(
                error_body='{"error":{"reason":"err_free_rate"}}',
                error_msg='{"error":{"reason":"err_free_rate"}}',
                http_code=429,
                headers={"Retry-After": "1"},
                body=body,
                attempt=0,
                log_tag="[OrcaRouter-Stream]",
            )
        captured = capsys.readouterr()
        assert "[OrcaRouter-Stream]" in captured.err
        assert "[OrcaRouter]" not in captured.err.replace("[OrcaRouter-Stream]", "")


# ------------------------------------------------------------------ #
#  Regression: both call sites use the helper (deduplication check)    #
# ------------------------------------------------------------------ #

class TestDeduplication:
    def test_remedy_strings_live_only_in_helper(self):
        """Before the refactor: the two remedy prose blocks (access_denied
        vs err_free_used) were duplicated verbatim in BOTH _generate_with_auth
        and _iter_sse_lines — 4 copies of "established GitHub account" in
        the remedy strings alone. After: the remedy strings appear only in
        _classify_and_handle_http_error. We count occurrences in the actual
        remedy assignments (the f-string/syntax lines starting with
        "Either (a) link" or containing "link an established") — should be 2
        (one per remedy branch) + a few in comments/prose, but the key
        signal is that neither _generate_with_auth nor _iter_sse_lines
        contains the remedy prose anymore."""
        import agentkthx.plugins.orcarouter.orcarouter as mod
        src = Path(mod.__file__).read_text()
        # The remedy prose "Either (a) link an established GitHub account"
        # (access_denied branch) + "link an established GitHub account at"
        # (err_free_used branch) should each appear exactly ONCE now.
        assert src.count("link an established GitHub account") == 2, (
            "remedy prose must live only in the helper (2 branches), not "
            "duplicated across both call sites"
        )

    def test_no_duplicated_sleep_call_in_free_tier_blocks(self):
        """Before: time.sleep appeared in both duplicated blocks (4 calls:
        2 retry_after + 2 ten-second). After: only in the helper (2 calls)."""
        import agentkthx.plugins.orcarouter.orcarouter as mod
        src = Path(mod.__file__).read_text()
        assert src.count("time.sleep(retry_after)") == 1, (
            "retry_after sleep must be in helper only (was duplicated)"
        )
        assert src.count("time.sleep(10)") == 1, (
            "10s sleep must be in helper only (was duplicated)"
        )

    def test_both_call_sites_invoke_helper(self):
        """Both _generate_with_auth and _iter_sse_lines must call
        _classify_and_handle_http_error (the deduplication contract)."""
        import agentkthx.plugins.orcarouter.orcarouter as mod
        src = Path(mod.__file__).read_text()
        # The helper is called twice — once per call site.
        assert src.count("self._classify_and_handle_http_error(") == 2, (
            "both call sites must invoke the shared helper"
        )
