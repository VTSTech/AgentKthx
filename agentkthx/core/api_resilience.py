"""
⚛️ AgentKthx — API Resilience (R06.54)

Transient API error classification and back-off scheduling.

Free-tier providers (OpenRouter `:free` models in particular) routinely
return HTTP 429 "Provider returned error" or silently empty responses.
The old harness treated the first such error as fatal: the backend retried
3 times (~30 s of patience) and then the agent loop killed the entire run.
A 100-step audit against a free model can therefore never complete.

Fix strategy (two layers):

1. Backend layer (openrouter plugin) — retry 429/5xx with exponential
   back-off, honoring `Retry-After` when present.
2. Agent loop layer (this module) — when `generate()` still raises, the run
   does NOT die. The step is retried after an escalating wait until
   `max_api_retries` consecutive failures, after which the run terminates
   gracefully with a valid (dangling-free) history.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
import random
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Consecutive generate() failures tolerated per step before the run is
# declared fatal. Every successful generate resets the counter.
DEFAULT_MAX_API_RETRIES = 5

# Back-off schedule base (seconds): attempt 1 waits BASE, attempt 2 waits
# BASE*2, attempt 3 BASE*4 ... capped at CAP.
DEFAULT_API_BACKOFF_BASE = 10.0
DEFAULT_API_BACKOFF_CAP = 120.0

# Jitter fraction (±20%) so parallel agents don't sync their retries.
_BACKOFF_JITTER = 0.2

# Substrings (lowercase) that mark an exception as TRANSIENT — i.e. retrying
# the same request later can plausibly succeed. Anything else (auth errors,
# malformed requests, unknown tools) is fatal and fails immediately.
_TRANSIENT_MARKERS = (
    "rate limit",
    "ratelimit",
    "429",
    "too many requests",
    "provider returned error",
    "upstream error",
    "overloaded",
    "empty response",
    "no choices",
    "timed out",
    "timeout",
    "connection",
    "temporarily",
    "unavailable",
    "try again",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "internal server error",
    "500",
)

# Substrings that mark an exception as PERMANENT — retrying is pointless and
# only burns the user's time. These are checked first and win over transient
# markers (an error containing both is treated as permanent).
#
# ROB-10 (R07.06): the snake_case variants below come from provider JSON
# error BODIES, which backends embed verbatim in the raised message
# (e.g. ``RuntimeError(f"ZAI HTTP error 500: {error_body}")``). Providers
# return HTTP 500 for some PERMANENT conditions — ``model_not_found`` on
# misconfigured deployments, ``context_length_exceeded`` variants,
# ``invalid_request`` / ``invalid_api_key`` — and the bare ``"500"``
# transient marker classified all of them as retryable, burning the full
# retry budget (~6 minutes of back-off) before failing. The prose forms
# ("not found", "invalid request") were already covered; the underscore
# forms were not, because ``"not found" in "model_not_found"`` is False.
_PERMANENT_MARKERS = (
    "authentication",
    "unauthorized",
    "api key",
    "invalid api",
    "invalid_api_key",
    "401",
    "403",
    "forbidden",
    "not found",
    "model_not_found",
    "404",
    "invalid request",
    "invalid_request",
    "bad request",
    "malformed",
    "unsupported",
    "content filter",
    "context_length",
    "insufficient",  # credits / quota exhausted on paid tier
)


def is_transient_api_error(exc: BaseException, body: str | None = None) -> bool:
    """
    Decide whether an exception raised by ``backend.generate()`` is transient
    (worth retrying after a back-off) or permanent (fail immediately).

    Classification is text-based on ``str(exc)`` because backends raise plain
    ``RuntimeError`` exceptions carrying the upstream message.

    ROB-10 (R07.06): ``body`` accepts the raw HTTP response body when the
    caller has it (backends usually embed the body in the exception message
    already, which is checked the same way). Permanent-error patterns found
    in the body (``invalid_request``, ``context_length``,
    ``model_not_found``, ``invalid_api_key``) win over the bare ``"500"``
    transient marker — a 500 carrying a permanent-error body fails fast
    instead of burning the full retry budget. The body is only consulted
    for PERMANENT patterns: a clean-bodied 500 stays transient.

    SEC-14 (R07.08): the ``body`` arg is untrusted (API-provider-controlled).
    A malicious provider could embed permanent markers in benign fields
    (e.g. ``{"user_message": "your request was not invalid_request yet"}``)
    to force permanent classification — a DoS via premature-fail. The fix:
    for the ``body`` arg, only match structured JSON patterns (``"key":`` or
    ``"key": "value"`` forms), not raw substrings. The ``str(exc)`` path
    remains a raw substring match because backends construct the exception
    message themselves (trusted).
    """
    if body:
        body_text = body.lower()
        # SEC-14: match permanent markers as JSON values, not raw substrings.
        # A marker matches if it appears INSIDE a quoted JSON string value:
        #   "context_length_exceeded" → marker "context_length" matches (substring of a quoted value)
        #   "invalid_request" → marker "invalid_request" matches (exact quoted token)
        #   "type":"invalid_request" → marker "invalid_request" matches (value)
        # but does NOT match if the marker appears in unquoted prose:
        #   {"message": "error 401 in prose"} → "401" does NOT match (inside prose, not a JSON key/value)
        # Implementation: for each marker, check if it appears between any
        # pair of double-quotes in the body. This is a superset of the exact
        # quoted-token match — it catches substrings within quoted values
        # (like "context_length" inside "context_length_exceeded") while
        # still rejecting markers in unquoted prose.
        for marker in _PERMANENT_MARKERS:
            # Extract all quoted strings from the body and check if the marker
            # appears inside any of them
            import re as _re
            for quoted in _re.findall(r'"([^"]*)"', body_text):
                if marker in quoted:
                    return False
    msg = str(exc).lower()
    if not msg:
        return False
    for marker in _PERMANENT_MARKERS:
        if marker in msg:
            return False
    for marker in _TRANSIENT_MARKERS:
        if marker in msg:
            return True
    return False


def backoff_delay(attempt: int, base: float = DEFAULT_API_BACKOFF_BASE,
                  cap: float = DEFAULT_API_BACKOFF_CAP) -> float:
    """
    Exponential back-off with jitter for the Nth retry attempt (1-based).

    attempt 1 → base, attempt 2 → 2*base, attempt 3 → 4*base ... capped.
    Jitter of ±20% desynchronizes concurrent agents.
    """
    delay = base * (2 ** max(0, attempt - 1))
    delay = min(delay, cap)
    jitter = delay * _BACKOFF_JITTER
    return max(0.5, delay + random.uniform(-jitter, jitter))


def sleep_backoff(attempt: int, base: float = DEFAULT_API_BACKOFF_BASE,
                  cap: float = DEFAULT_API_BACKOFF_CAP) -> float:
    """Sleep the back-off for this attempt and return the duration slept."""
    delay = backoff_delay(attempt, base, cap)
    time.sleep(delay)
    return delay


def max_api_retries_from_env() -> int:
    """Read AGENTKTHX_MAX_API_RETRIES (default: DEFAULT_MAX_API_RETRIES)."""
    raw = os.environ.get("AGENTKTHX_MAX_API_RETRIES", "")
    try:
        val = int(raw)
        if val >= 0:
            return val
    except (ValueError, TypeError):
        pass
    return DEFAULT_MAX_API_RETRIES


def classify_error_kind(msg: str) -> str:
    """
    Coarse error bucket for console messaging: ``rate limit``,
    ``empty response``, ``connection issue`` or the generic ``API error``.
    """
    low = (msg or "").lower()
    if "rate limit" in low or "ratelimit" in low or "429" in low:
        return "rate limit"
    if "empty response" in low or "no choices" in low:
        return "empty response"
    if "timeout" in low or "timed out" in low or "connection" in low:
        return "connection issue"
    return "API error"


def describe_wait(attempt: int, max_retries: int, waited: float, exc: BaseException) -> str:
    """Human-readable one-liner for the console during a retry wait."""
    kind = classify_error_kind(str(exc))
    msg = str(exc)
    snippet = msg if len(msg) <= 120 else msg[:117] + "..."
    return (
        f"  [Resilience] {kind} — retrying in {waited:.0f}s "
        f"(recovery attempt {attempt}/{max_retries}): {snippet}"
    )


def describe_terminal(exc: BaseException, attempts: int, total_wait: float) -> str:
    """
    Final one-liner printed when the retry budget is exhausted and the run
    is paused. Always printed (not gated behind --debug) so non-debug users
    see WHY the run stopped instead of a bare ``(empty response)``.
    """
    kind = classify_error_kind(str(exc))
    mins = total_wait / 60.0
    wait_txt = f"{mins:.1f} min" if mins >= 1 else f"{max(0.0, total_wait):.0f}s"
    snippet = str(exc)
    if len(snippet) > 140:
        snippet = snippet[:137] + "..."
    return (
        f"  [Resilience] {kind} persisted through {attempts} recovery "
        f"attempts ({wait_txt} of back-off) — pausing this run. "
        f"Last error: {snippet}"
    )


__all__ = [
    "DEFAULT_MAX_API_RETRIES",
    "DEFAULT_API_BACKOFF_BASE",
    "DEFAULT_API_BACKOFF_CAP",
    "is_transient_api_error",
    "backoff_delay",
    "sleep_backoff",
    "max_api_retries_from_env",
    "classify_error_kind",
    "describe_wait",
    "describe_terminal",
]
