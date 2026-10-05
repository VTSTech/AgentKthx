"""R07.24 batch 3 regression tests: MAINT-03, MAINT-22, MAINT-23/ROB-29, ROB-33.

Five audit findings closed in this batch:
  MAINT-03: normalize_args strategy 5 (prefix/substring matching) removed
  MAINT-22: Mistral streaming path now routes through _build_mistral_body
            (random_seed/safe_prompt/prompt_cache_key/tool_choice mapping
            apply on streaming, not just non-streaming)
  MAINT-23: Mistral _iter_sse_lines + _make_api_request retry skeleton
            lifted to CloudBackend (_compute_retry_after,
            _is_retryable_http_status, _compute_network_backoff)
  ROB-29:   closed in the same move as MAINT-23 (same duplicated logic)
  ROB-33:   _is_process_alive on Windows now uses OpenProcess +
            GetExitCodeProcess instead of os.kill(pid, 0) (which TERMINATES
            the target on Windows)

All non-breaking — the default behavior for vanilla OpenAI-shape backends
is byte-identical to pre-R07.24. The fixes only affect:
- Mistral (MAINT-22, MAINT-23, ROB-29)
- normalize_args callers (MAINT-03) — prefix matching dropped
- _is_process_alive callers on Windows (ROB-33) — ctypes path
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MAINT-03 (R07.24): normalize_args strategy 5 (prefix/substring) REMOVED
# ---------------------------------------------------------------------------


class TestMaint03Strategy5Removed:
    """Strategy 5 (prefix/substring matching) must NOT match anymore.

    Before R07.24, a key like "e" matched "expression" (e is a substring),
    "pat" matched "path", "v" matched "value". This was dangerously
    permissive — a model that hallucinates a single-letter arg name would
    silently succeed instead of failing with a clear "unknown argument"
    message. R07.24 drops the strategy entirely.
    """

    def test_single_letter_e_does_not_match_expression(self):
        """{"e": "..."} must NOT match the "expression" param."""
        from agentkthx.core.helpers import normalize_args

        result = normalize_args({"e": "1+1"}, ["expression"])
        # Before R07.24: {"expression": "1+1"} (silent prefix match)
        # After R07.24: {"e": "1+1"} (unmatched key preserved as-is)
        assert "expression" not in result, (
            "MAINT-03 regression: strategy 5 prefix matching still active — "
            "single-letter 'e' should NOT match 'expression'"
        )
        # The unmatched key is preserved in the output (callers can warn
        # about unknown args)
        assert "e" in result

    def test_short_prefix_pat_does_not_match_path(self):
        """{"pat": "/x"} must NOT match the "path" param."""
        from agentkthx.core.helpers import normalize_args

        result = normalize_args({"pat": "/x"}, ["path"])
        assert "path" not in result
        assert "pat" in result

    def test_substring_value_does_not_match_value_param(self):
        """{"value": 1} where 'value' is also a substring of 'values' —
        the substring direction (`param in key_lower`) was the dangerous
        one. After R07.24, only exact + alias matches work.
        """
        from agentkthx.core.helpers import normalize_args

        # 'value' is exact match for 'value' param → still works
        result = normalize_args({"value": 1}, ["value"])
        assert result.get("value") == 1

        # 'val' is a prefix of 'value' → no longer matches
        result = normalize_args({"val": 1}, ["value"])
        assert "value" not in result
        assert "val" in result

    def test_exact_match_still_works(self):
        """Regression guard: exact name matching is unaffected."""
        from agentkthx.core.helpers import normalize_args

        result = normalize_args({"path": "/x", "mode": "r"}, ["path", "mode"])
        assert result == {"path": "/x", "mode": "r"}

    def test_case_insensitive_match_still_works(self):
        """Regression guard: case-insensitive matching is unaffected."""
        from agentkthx.core.helpers import normalize_args

        result = normalize_args({"PATH": "/x"}, ["path"])
        # Case-insensitive match should still work
        assert "path" in result
        assert result["path"] == "/x"

    def test_canonical_alias_match_still_works(self):
        """Regression guard: the ARG_ALIASES table is unaffected."""
        from agentkthx.core.helpers import normalize_args

        # 'cmd' is a known alias for 'command' (per ARG_ALIASES)
        result = normalize_args({"cmd": "ls"}, ["command"])
        assert "command" in result
        assert result["command"] == "ls"


# ---------------------------------------------------------------------------
# MAINT-22 (R07.24): Mistral _build_stream_body routes through _build_mistral_body
# ---------------------------------------------------------------------------


class TestMaint22StreamBodyRouting:
    """The streaming path now uses _build_mistral_body, not _build_openai_body.

    Before R07.24, the base class's generate_completions_stream called
    _build_openai_body(stream=True) directly — bypassing Mistral's
    random_seed/safe_prompt/prompt_cache_key/tool_choice="required"→"any"
    mapping + OpenAI-only kwarg stripping. R07.24 added a
    _build_stream_body hook on OpenAICompatibleBackend (default delegates
    to _build_openai_body(stream=True) for back-compat) and Mistral
    overrides it to delegate to _build_mistral_body(stream=True).
    """

    def test_mistral_overrides_build_stream_body(self):
        """MistralBackend must have its own _build_stream_body method."""
        from agentkthx.backends.openai_compat import OpenAICompatibleBackend
        from agentkthx.plugins.mistral.mistral import MistralBackend

        # The method must be defined on MistralBackend itself, not just
        # inherited from the base class.
        assert (
            "_build_stream_body" in MistralBackend.__dict__
        ), "MAINT-22 regression: MistralBackend must override _build_stream_body"
        # Base class must also have it (the hook itself)
        assert "_build_stream_body" in OpenAICompatibleBackend.__dict__

    def test_mistral_build_stream_body_produces_mistral_shaped_body(self):
        """Calling _build_stream_body on Mistral should include random_seed
        when the seed kwarg is passed — proving it routes through
        _build_mistral_body, not _build_openai_body.
        """
        from agentkthx.plugins.mistral.mistral import MistralBackend

        backend = MistralBackend.__new__(MistralBackend)
        # Bypass __init__ — we only need the body-builder methods

        body = backend._build_stream_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            seed=42,  # Mistral-specific: seed → random_seed
        )
        # Mistral-specific shaping MUST be present (was missing pre-R07.24)
        assert body.get("random_seed") == 42, (
            "MAINT-22 regression: streaming body doesn't include random_seed — "
            "the _build_stream_body hook isn't routing through _build_mistral_body"
        )
        assert body.get("stream") is True
        assert body.get("stream_options") == {"include_usage": True}

    def test_mistral_build_stream_body_includes_safe_prompt_when_env_set(self, monkeypatch):
        """MISTRAL_SAFE_PROMPT=true must inject safe_prompt on streaming too."""
        from agentkthx.plugins.mistral.mistral import MistralBackend

        monkeypatch.setenv("MISTRAL_SAFE_PROMPT", "true")
        backend = MistralBackend.__new__(MistralBackend)

        body = backend._build_stream_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
        )
        # safe_prompt MUST be present (was missing pre-R07.24 on streaming)
        assert (
            body.get("safe_prompt") is True
        ), "MAINT-22 regression: streaming body doesn't include safe_prompt"

    def test_vanilla_backend_default_routes_through_openai_body(self):
        """The base class default _build_stream_body delegates to _build_openai_body.

        Vanilla OpenAI-shape backends (ZAI, OpenRouter, HuggingFace,
        Pollinations, BitNet) must be byte-identical to pre-R07.24 —
        no behavior change for them.

        We can't easily instantiate OpenAICompatibleBackend directly
        (it's abstract — requires backend_type, base_url, generate,
        generate_stream, list_models, test_tool_support). Instead we
        verify the delegation by reading the source of the default
        implementation — it must call self._build_openai_body(stream=True, ...).
        """
        import inspect

        from agentkthx.backends.openai_compat import OpenAICompatibleBackend

        # Read the source of the default _build_stream_body implementation
        source = inspect.getsource(OpenAICompatibleBackend._build_stream_body)
        # The default impl must call self._build_openai_body(stream=True, ...)
        assert "_build_openai_body" in source, (
            "MAINT-22 regression: default _build_stream_body must delegate to "
            "_build_openai_body (vanilla backends rely on this for byte-identical "
            "behavior with pre-R07.24)"
        )
        assert (
            "stream=True" in source
        ), "MAINT-22 regression: default _build_stream_body must pass stream=True"


# ---------------------------------------------------------------------------
# MAINT-23 / ROB-29 (R07.24): CloudBackend shared retry helpers
# ---------------------------------------------------------------------------


class TestMaint23Rob29SharedRetryHelpers:
    """CloudBackend now exposes _compute_retry_after, _is_retryable_http_status,
    and _compute_network_backoff. Mistral's _iter_sse_lines + _make_api_request
    both delegate to them, eliminating the ~80 LOC of copy-pasted retry skeleton.
    """

    # Concrete stub subclass — CloudBackend itself is abstract (requires
    # generate + generate_stream + list_models implementations). This stub
    # implements the abstract methods as no-ops so we can instantiate it
    # purely for testing the retry helpers.
    class _ConcreteCloudBackend:
        """Minimal concrete CloudBackend subclass for testing retry helpers."""

        # Pull in the CloudBackend helpers we're testing
        from agentkthx.backends.cloud_base import CloudBackend as _Base

        _BACKOFF_BASE = _Base._BACKOFF_BASE
        _BACKOFF_CAP = _Base._BACKOFF_CAP
        _MAX_RETRIES = _Base._MAX_RETRIES
        _compute_retry_after = _Base._compute_retry_after
        _is_retryable_http_status = _Base._is_retryable_http_status
        _compute_network_backoff = _Base._compute_network_backoff
        _max_retries = _Base._max_retries

    def _make_backend(self):
        """Instantiate a concrete stub backend for retry-helper testing.

        CloudBackend itself is abstract (requires generate +
        generate_stream implementations), so we instantiate MistralBackend
        which is concrete and inherits the helpers from CloudBackend.
        Bypass __init__ via object.__new__ — we only need the helpers,
        not the full backend wiring.
        """
        from agentkthx.plugins.mistral.mistral import MistralBackend

        backend = object.__new__(MistralBackend)
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0
        backend._MAX_RETRIES = 4
        return backend

    def test_compute_retry_after_honors_retry_after_header(self):
        """A Retry-After header value is honored (capped at _BACKOFF_CAP)."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0

        class FakeHeaders:
            def get(self, name, default=""):
                if name == "Retry-After":
                    return "5"
                return default

        # Retry-After: 5 → sleep 5 seconds (under the cap, so honored)
        assert backend._compute_retry_after(FakeHeaders(), attempt=0) == 5.0

    def test_compute_retry_after_caps_honored_retry_after_at_backoff_cap(self):
        """An uncapped Retry-After (e.g. 3600s) is capped at _BACKOFF_CAP."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 30.0  # smaller cap for the test

        class FakeHeaders:
            def get(self, name, default=""):
                if name == "Retry-After":
                    return "3600"  # ROB-16 lesson: uncapped would hang for 1h
                return default

        # Capped at 30s, not 3600s
        assert backend._compute_retry_after(FakeHeaders(), attempt=0) == 30.0

    def test_compute_retry_after_falls_back_to_exponential_backoff(self):
        """No Retry-After header → exponential backoff with jitter."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0

        class FakeHeaders:
            def get(self, name, default=""):
                return default  # no Retry-After

        # Attempt 0 → base = 1 * 2^0 = 1, plus 0–20% jitter → 1.0–1.2s
        # Clamped to ≥1s.
        ra = backend._compute_retry_after(FakeHeaders(), attempt=0)
        assert 1.0 <= ra <= 1.2 + 0.01  # small epsilon for float compare

        # Attempt 1 → base = 1 * 2^1 = 2, plus 0–20% jitter → 2.0–2.4s
        ra = backend._compute_retry_after(FakeHeaders(), attempt=1)
        assert 2.0 <= ra <= 2.4 + 0.01

        # Attempt 2 → base = 1 * 2^2 = 4
        ra = backend._compute_retry_after(FakeHeaders(), attempt=2)
        assert 4.0 <= ra <= 4.8 + 0.01

    def test_compute_retry_after_handles_malformed_retry_after(self):
        """A non-numeric Retry-After falls back to exponential backoff."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0

        class FakeHeaders:
            def get(self, name, default=""):
                if name == "Retry-After":
                    return "not-a-number"
                return default

        # Should not raise — falls back to backoff
        ra = backend._compute_retry_after(FakeHeaders(), attempt=0)
        assert ra >= 1.0

    def test_compute_retry_after_handles_none_headers(self):
        """Passing headers=None (URLError case) doesn't crash."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0

        # None headers — no Retry-After to read, falls through to backoff
        ra = backend._compute_retry_after(None, attempt=0)
        assert ra >= 1.0

    def test_is_retryable_http_status_429_and_5xx(self):
        """429 + 5xx are retryable; 4xx (except 429) is not."""

        backend = self._make_backend()
        assert backend._is_retryable_http_status(429) is True
        assert backend._is_retryable_http_status(500) is True
        assert backend._is_retryable_http_status(502) is True
        assert backend._is_retryable_http_status(503) is True
        assert backend._is_retryable_http_status(504) is True
        # 4xx (except 429) is not retryable
        assert backend._is_retryable_http_status(400) is False
        assert backend._is_retryable_http_status(401) is False
        assert backend._is_retryable_http_status(403) is False
        assert backend._is_retryable_http_status(404) is False
        assert backend._is_retryable_http_status(422) is False
        # 2xx is not "retryable" (it's success)
        assert backend._is_retryable_http_status(200) is False

    def test_compute_network_backoff_exponential(self):
        """_compute_network_backoff uses exponential backoff with jitter (no Retry-After)."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 60.0

        # Attempt 0 → ~1s, attempt 1 → ~2s, attempt 2 → ~4s
        for attempt, expected_base in [(0, 1.0), (1, 2.0), (2, 4.0)]:
            backoff = backend._compute_network_backoff(attempt)
            # Allow up to 20% jitter on top
            assert expected_base <= backoff <= expected_base * 1.2 + 0.01

    def test_compute_network_backoff_capped(self):
        """Backoff is capped at _BACKOFF_CAP even for high attempt numbers."""

        backend = self._make_backend()
        backend._BACKOFF_BASE = 1.0
        backend._BACKOFF_CAP = 30.0

        # Attempt 10 → base = 1 * 2^10 = 1024, but capped at 30s + 20% jitter
        backoff = backend._compute_network_backoff(10)
        assert backoff <= 30.0 * 1.2  # cap + max jitter

    def test_mistral_uses_cloud_backend_helpers(self):
        """MistralBackend inherits the new helpers from CloudBackend."""
        from agentkthx.plugins.mistral.mistral import MistralBackend

        # The helpers must be accessible on MistralBackend (inherited)
        assert callable(MistralBackend._compute_retry_after)
        assert callable(MistralBackend._is_retryable_http_status)
        assert callable(MistralBackend._compute_network_backoff)
        # And Mistral's own _max_retries still works (reads MISTRAL_MAX_RETRIES)
        assert callable(MistralBackend._max_retries)


# ---------------------------------------------------------------------------
# ROB-33 (R07.24): _is_process_alive platform-safe liveness probe
# ---------------------------------------------------------------------------


class TestRob33ProcessAlivePlatformSafe:
    """_is_process_alive must NOT use os.kill(pid, 0) on Windows — that
    TERMINATES the target. R07.24 branches on os.name == 'nt' and uses
    ctypes OpenProcess + GetExitCodeProcess (non-destructive) on Windows.
    """

    def test_returns_false_for_zero_pid(self):
        """pid <= 0 is invalid → False."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive

        assert _is_process_alive(0) is False
        assert _is_process_alive(-1) is False

    def test_returns_false_for_nonexistent_pid(self):
        """A pid that doesn't exist → False (no such process)."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive

        # Pid 0xFFFFFFFF (4 billion) is effectively never a real process
        # on Linux. os.kill raises ProcessLookupError.
        assert _is_process_alive(0xFFFFFFFF) is False

    def test_returns_true_for_current_pid(self):
        """The current process's own pid should be alive."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive

        # os.getpid() is always alive (it's us)
        assert _is_process_alive(os.getpid()) is True

    def test_windows_helper_exists(self):
        """The Windows-specific helper function must be defined."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive_windows

        # Must be callable (will be invoked only on os.name == 'nt', but
        # the function must always exist for testability)
        assert callable(_is_process_alive_windows)

    def test_windows_helper_returns_false_for_zero_pid(self):
        """Even the Windows helper guards against pid <= 0."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive_windows

        # Note: the Windows helper is called AFTER the pid <= 0 guard in
        # _is_process_alive, so it doesn't need to re-check — but it
        # should still return False for invalid pids if called directly.
        # On non-Windows, the ctypes call will fail and we fall through
        # to the fail-closed branch.
        result = _is_process_alive_windows(0)
        assert result is False  # fail-closed

    def test_windows_helper_returns_false_for_nonexistent_pid(self):
        """On non-Windows, the Windows helper fails closed (returns False)."""
        from agentkthx.plugins.turboquant.turbo import _is_process_alive_windows

        # On Linux, ctypes.WinDLL("kernel32") raises OSError → fail-closed
        result = _is_process_alive_windows(0xFFFFFFFF)
        assert result is False

    def test_no_os_kill_on_windows_path(self, monkeypatch):
        """When os.name == 'nt', _is_process_alive must NOT call os.kill.

        This is the core ROB-33 contract: the Windows path uses
        OpenProcess+GetExitCodeProcess (non-destructive), not os.kill
        (which would terminate the target).
        """
        from agentkthx.plugins.turboquant import turbo as turbo_mod

        # Track os.kill calls
        kill_calls = []

        def tracking_kill(pid, sig):
            kill_calls.append((pid, sig))
            # Simulate the Windows behavior: any signal terminates
            # (this is what would happen pre-R07.24)
            raise ProcessLookupError("simulated")

        # Force os.name == 'nt' for the test
        monkeypatch.setattr(turbo_mod.os, "name", "nt")
        monkeypatch.setattr(turbo_mod.os, "kill", tracking_kill)

        # Call _is_process_alive for a non-existent pid
        result = turbo_mod._is_process_alive(999999)

        # os.kill must NOT have been called (Windows path uses ctypes)
        assert kill_calls == [], (
            "ROB-33 regression: os.kill was called on the Windows path — "
            "this would TERMINATE the target process. The Windows path must "
            "use OpenProcess+GetExitCodeProcess via ctypes, not os.kill."
        )
        # Result is False (process doesn't exist OR Windows helper failed-closed)
        assert result is False

    def test_posix_path_uses_os_kill(self, monkeypatch):
        """When os.name != 'nt', _is_process_alive uses os.kill (the
        POSIX non-destructive liveness check). This is the unchanged
        behavior — the regression guard verifies the POSIX path is intact.
        """
        from agentkthx.plugins.turboquant import turbo as turbo_mod

        kill_called = [False]

        def fake_kill(pid, sig):
            kill_called[0] = True
            # POSIX signal 0 = liveness check, no actual signal sent
            # For an invalid pid, raise ProcessLookupError
            raise ProcessLookupError("simulated")

        # Force os.name == 'posix' for the test
        monkeypatch.setattr(turbo_mod.os, "name", "posix")
        monkeypatch.setattr(turbo_mod.os, "kill", fake_kill)

        result = turbo_mod._is_process_alive(999999)
        # POSIX path must have called os.kill (signal 0 liveness check)
        assert kill_called[0] is True, (
            "ROB-33 regression: POSIX path should call os.kill(pid, 0) — "
            "the non-destructive liveness check on POSIX systems."
        )
        assert result is False  # ProcessLookupError → not alive


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
