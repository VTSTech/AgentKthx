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
_PERMANENT_MARKERS = (
    "authentication",
    "unauthorized",
    "api key",
    "invalid api",
    "401",
    "403",
    "forbidden",
    "not found",
    "404",
    "invalid request",
    "bad request",
    "malformed",
    "unsupported",
    "content filter",
    "insufficient",  # credits / quota exhausted on paid tier
)


def is_transient_api_error(exc: BaseException) -> bool:
    """
    Decide whether an exception raised by ``backend.generate()`` is transient
    (worth retrying after a back-off) or permanent (fail immediately).

    Classification is text-based on ``str(exc)`` because backends raise plain
    ``RuntimeError`` exceptions carrying the upstream message.
    """
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
