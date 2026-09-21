"""
R06.54 API Resilience Tests — survive rate limits and provider hiccups.

The codebase-audit investigation showed free-tier OpenRouter models get
HTTP 429 "Provider returned error" every few requests, and the old harness
killed the whole run after 3 quick retries. These tests pin the new
two-layer resilience:

  Layer 1 (plugin):  429/502/503/504 → exponential back-off + Retry-After
  Layer 2 (agent):   transient generate() exceptions → retry with back-off,
                     permanent errors fail fast, sustained failures
                     terminate gracefully with valid history.

Written by VTSTech — https://www.vts-tech.org
"""

import pytest

from agentkthx import Agent
from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.core.api_resilience import (
    backoff_delay,
    classify_error_kind,
    describe_terminal,
    is_transient_api_error,
    max_api_retries_from_env,
    DEFAULT_API_BACKOFF_CAP,
    DEFAULT_MAX_API_RETRIES,
)
from agentkthx.core.types import BackendType, StepResultType
from agentkthx.tools import make_builtin_registry
from tests.test_loop_resilience import StubBackend, native_call


# ============================================================================
# Transient classification
# ============================================================================

class TestIsTransientApiError:
    @pytest.mark.parametrize("msg", [
        "OpenRouter rate limit: Provider returned error. Retried 3 times.",
        "HTTP 429 Too Many Requests",
        "OpenRouter returned an empty response (no content, no tool_calls).",
        "Connection reset by peer",
        "Read timed out after 120s",
        "OpenRouter API error 502: Bad gateway",
        "OpenRouter API error 503: Service Unavailable",
        "Provider returned error",
        "server is overloaded, try again",
    ])
    def test_transient_messages(self, msg):
        assert is_transient_api_error(RuntimeError(msg)) is True

    @pytest.mark.parametrize("msg", [
        "OpenRouter authentication failed. Please check your OPENROUTER_API_KEY.",
        "OpenRouter API error 401: Unauthorized",
        "OpenRouter API error 400: invalid request payload",
        "OpenRouter API error 404: model not found",
        "HTTP 403 Forbidden",
        "insufficient credits for this model",
    ])
    def test_permanent_messages(self, msg):
        assert is_transient_api_error(RuntimeError(msg)) is False

    def test_unknown_error_is_not_transient(self):
        assert is_transient_api_error(RuntimeError("something odd happened")) is False

    def test_empty_message_is_not_transient(self):
        assert is_transient_api_error(RuntimeError("")) is False

    def test_permanent_wins_over_transient(self):
        # Contains both "429" and "authentication" → permanent
        msg = "authentication failed while handling 429"
        assert is_transient_api_error(RuntimeError(msg)) is False


class TestBackoff:
    def test_delays_increase(self):
        d1 = backoff_delay(1)
        d2 = backoff_delay(2)
        d3 = backoff_delay(3)
        assert d1 < d2 < d3

    def test_cap_respected(self):
        for attempt in range(1, 10):
            assert backoff_delay(attempt) <= DEFAULT_API_BACKOFF_CAP * 1.21

    def test_jitter_bounds(self):
        # base for attempt 1 is 10 → delay ∈ [8, 12]
        for _ in range(50):
            d = backoff_delay(1, base=10.0, cap=120.0)
            assert 8.0 <= d <= 12.0


class TestMaxApiRetriesFromEnv:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("AGENTKTHX_MAX_API_RETRIES", raising=False)
        assert max_api_retries_from_env() == DEFAULT_MAX_API_RETRIES

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "2")
        assert max_api_retries_from_env() == 2

    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "banana")
        assert max_api_retries_from_env() == DEFAULT_MAX_API_RETRIES

    def test_zero_disables_retries(self, monkeypatch):
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "0")
        assert max_api_retries_from_env() == 0


# ============================================================================
# Agent loop: transient errors don't kill the run
# ============================================================================

class TestAgentRetriesTransientErrors:
    def test_rate_limit_then_success_completes(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: sleeps.append(s))
        backend = StubBackend([
            RuntimeError("OpenRouter rate limit: Provider returned error. Retried 3 times."),
            {"content": "Final Answer: audit complete"},
        ])
        agent = Agent(model="stub", backend=backend,
                      tools=make_builtin_registry().subset(["read_file"]),
                      max_steps=5, soul=None, max_api_retries=3)
        result = agent.run("audit")
        assert result.final_answer == "audit complete"
        assert result.success is True
        assert len(sleeps) == 1  # one back-off wait happened

    def test_retries_do_not_consume_steps(self, monkeypatch):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)
        # 3 rate-limit hits, then a final answer — all inside step 1.
        backend = StubBackend([
            RuntimeError("429 Too Many Requests"),
            RuntimeError("429 Too Many Requests"),
            RuntimeError("429 Too Many Requests"),
            {"content": "Final Answer: done"},
        ])
        agent = Agent(model="stub", backend=backend, tools=None,
                      max_steps=2, soul=None, max_api_retries=5)
        result = agent.run("hi")
        assert result.success is True
        assert len(result.steps) == 1  # only one step consumed

    def test_multiple_transient_failures_across_steps(self, monkeypatch):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)
        backend = StubBackend([
            RuntimeError("connection reset"),
            {"content": "Final Answer: ok"},
        ])
        agent = Agent(model="stub", backend=backend, tools=None,
                      max_steps=3, soul=None, max_api_retries=2)
        result = agent.run("hi")
        assert result.final_answer == "ok"

    def test_permanent_error_fails_immediately(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: sleeps.append(s))
        backend = StubBackend([
            RuntimeError("OpenRouter authentication failed. Check OPENROUTER_API_KEY."),
        ])
        agent = Agent(model="stub", backend=backend, tools=None,
                      max_steps=5, soul=None, max_api_retries=5)
        result = agent.run("hi")
        assert result.success is False
        assert "authentication" in result.steps[-1].error
        assert sleeps == []  # no pointless waiting on a permanent error

    def test_sustained_transient_failures_terminate_gracefully(self, monkeypatch):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)

        class AlwaysLimited(StubBackend):
            def generate(self, *a, **k):
                self.calls.append(k.get("messages", []))
                raise RuntimeError("OpenRouter rate limit: Provider returned error")

        agent = Agent(model="stub", backend=AlwaysLimited([]), tools=None,
                      max_steps=10, soul=None, max_api_retries=2)
        result = agent.run("hi")
        assert result.success is False
        assert any(s.type == StepResultType.ERROR for s in result.steps)
        # Graceful: an ERROR step recorded, run ended, no infinite hang.
        assert len(result.steps) >= 1

    def test_max_api_retries_zero_disables_retry(self, monkeypatch):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)
        backend = StubBackend([RuntimeError("429 Too Many Requests")])
        agent = Agent(model="stub", backend=backend, tools=None,
                      max_steps=3, soul=None, max_api_retries=0)
        result = agent.run("hi")
        assert result.success is False

    def test_history_stays_valid_after_api_failure_termination(self, monkeypatch):
        """After a fatal API error the memory must still be API-valid:
        no assistant tool_calls without results."""
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)
        backend = StubBackend([
            {"tool_calls": [native_call("c1", "read_file", {"file_path": "README.md"})],
             "content": "reading"},
            RuntimeError("OpenRouter rate limit: Provider returned error"),
            RuntimeError("OpenRouter rate limit: Provider returned error"),
            RuntimeError("OpenRouter rate limit: Provider returned error"),
        ])
        agent = Agent(model="stub", backend=backend,
                      tools=make_builtin_registry().subset(["read_file"]),
                      max_steps=5, soul=None, max_api_retries=2)
        result = agent.run("audit")
        assert result.success is False
        msgs = agent.memory.get_messages()
        for msg in msgs:
            for tc in (msg.get("tool_calls") or []):
                cid = tc.get("id")
                assert any(
                    m.get("role") == "tool" and m.get("tool_call_id") == cid
                    for m in msgs
                ), f"dangling tool_call {cid}"


# ============================================================================
# OpenRouter plugin: 429/5xx retry
# ============================================================================

class _FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
        }
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


@pytest.fixture
def openrouter_plugin():
    from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
    import os
    if not os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = "test-key"
    # Construct without __init__ so no network call (list_models) happens.
    plugin = OpenRouterBackend.__new__(OpenRouterBackend)
    plugin.api_key = "test-key"
    plugin._base_url = "https://openrouter.ai/api/v1"  # read-only property
    from agentkthx.backends.base import BackendConfig
    plugin.config = BackendConfig(timeout=5)
    return plugin


class TestOpenRouter429Retry:
    def test_429_then_success(self, openrouter_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: calls.append(url) or (
                _FakeResponse(429, {"error": {"message": "Provider returned error"}},
                              {"Retry-After": "1"})
                if len(calls) < 3 else _FakeResponse(200)
            ),
        )
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.time.sleep", lambda s: None)
        out = openrouter_plugin._make_api_request("chat/completions", {"x": 1})
        assert out["choices"][0]["message"]["content"] == "ok"
        assert len(calls) == 3

    def test_429_exponential_backoff_when_no_header(self, openrouter_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: calls.append(url) or (
                _FakeResponse(429) if len(calls) < 2 else _FakeResponse(200)
            ),
        )
        slept = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.time.sleep",
            lambda s: slept.append(s))
        openrouter_plugin._make_api_request("chat/completions", {"x": 1})
        # No Retry-After header → exponential schedule (attempt 1 ≈ 5s base)
        assert 3.0 <= slept[0] <= 7.0

    def test_429_exhausted_raises_with_budget(self, openrouter_plugin, monkeypatch):
        monkeypatch.setenv("OPENROUTER_MAX_429_RETRIES", "2")
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: _FakeResponse(
                429, {"error": {"message": "Provider returned error"}}),
        )
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.time.sleep", lambda s: None)
        with pytest.raises(RuntimeError, match="Retried 2 times"):
            openrouter_plugin._make_api_request("chat/completions", {"x": 1})

    def test_502_retried_then_success(self, openrouter_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: calls.append(url) or (
                _FakeResponse(502, {"error": {"message": "Bad gateway"}})
                if len(calls) < 2 else _FakeResponse(200)
            ),
        )
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.time.sleep", lambda s: None)
        out = openrouter_plugin._make_api_request("chat/completions", {"x": 1})
        assert out["choices"][0]["message"]["content"] == "ok"

    def test_400_not_retried(self, openrouter_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: calls.append(url) or _FakeResponse(
                400, {"error": {"message": "invalid request"}}),
        )
        with pytest.raises(RuntimeError, match="400"):
            openrouter_plugin._make_api_request("chat/completions", {"x": 1})
        assert len(calls) == 1  # no retry on a bad request

    def test_401_not_retried(self, openrouter_plugin, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.requests.post",
            lambda url, **kw: calls.append(url) or _FakeResponse(401),
        )
        with pytest.raises(RuntimeError, match="authentication"):
            openrouter_plugin._make_api_request("chat/completions", {"x": 1})
        assert len(calls) == 1

    def test_default_budget_is_six(self, openrouter_plugin, monkeypatch):
        monkeypatch.delenv("OPENROUTER_MAX_429_RETRIES", raising=False)
        assert openrouter_plugin._max_429_retries() == 6


# ============================================================================
# Terminal UX — pause/fatal notices are always printed (never debug-only)
# ============================================================================

class TestClassifyErrorKind:
    @pytest.mark.parametrize("msg,kind", [
        ("OpenRouter rate limit: Provider returned error", "rate limit"),
        ("HTTP 429 Too Many Requests", "rate limit"),
        ("Ratelimit exceeded for free-models", "rate limit"),
        ("OpenRouter returned an empty response", "empty response"),
        ("no choices returned by provider", "empty response"),
        ("Connection reset by peer", "connection issue"),
        ("Read timed out after 120s", "connection issue"),
        ("OpenRouter API error 500: Internal Server Error", "API error"),
        ("", "API error"),
    ])
    def test_buckets(self, msg, kind):
        assert classify_error_kind(msg) == kind


class TestDescribeTerminal:
    def test_mentions_attempts_wait_and_pause(self):
        exc = RuntimeError(
            "OpenRouter rate limit: Provider returned error. Retried 6 times.")
        out = describe_terminal(exc, 5, 630.0)
        assert "rate limit" in out
        assert "5 recovery attempts" in out
        assert "10.5 min" in out
        assert "pausing this run" in out

    def test_seconds_formatting_under_a_minute(self):
        out = describe_terminal(RuntimeError("rate limit"), 3, 25.0)
        assert "25s of back-off" in out

    def test_long_error_snippet_truncated(self):
        exc = RuntimeError("rate limit: " + "x" * 300)
        out = describe_terminal(exc, 5, 60.0)
        assert "..." in out
        assert len(out) < 400


class TestTerminalNoticesAlwaysPrinted:
    """Regression: the pre-resilience code printed NOTHING on terminal API
    failure outside --debug, so chat users saw a bare '(empty response)'.
    The pause/fatal reason must always be visible."""

    def test_sustained_failure_prints_pause_notice_without_debug(
            self, monkeypatch, capsys):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)

        class AlwaysLimited(StubBackend):
            def generate(self, *a, **k):
                self.calls.append(k.get("messages", []))
                raise RuntimeError(
                    "OpenRouter rate limit: Provider returned error")

        agent = Agent(model="stub", backend=AlwaysLimited([]), tools=None,
                      max_steps=10, soul=None, max_api_retries=2, debug=False)
        result = agent.run("hi")
        out = capsys.readouterr().out
        assert result.success is False
        assert "[Resilience]" in out
        assert "pausing this run" in out
        assert "2 recovery attempts" in out

    def test_permanent_error_prints_fatal_notice_without_debug(
            self, monkeypatch, capsys):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)

        class AlwaysAuthFail(StubBackend):
            def generate(self, *a, **k):
                self.calls.append(k.get("messages", []))
                raise RuntimeError("OpenRouter authentication failed")

        agent = Agent(model="stub", backend=AlwaysAuthFail([]), tools=None,
                      max_steps=10, soul=None, max_api_retries=2, debug=False)
        result = agent.run("hi")
        out = capsys.readouterr().out
        assert result.success is False
        assert "Fatal API error" in out
        assert "not retrying" in out

    def test_stream_sustained_failure_prints_pause_notice(
            self, monkeypatch, capsys):
        monkeypatch.setattr("agentkthx.agent.time.sleep", lambda s: None)

        class AlwaysLimitedStream(StubBackend):
            def generate(self, *a, **k):
                self.calls.append(k.get("messages", []))
                raise RuntimeError(
                    "OpenRouter rate limit: Provider returned error")

            def generate_stream(self, model, messages, tools=None, **kwargs):
                raise RuntimeError(
                    "OpenRouter rate limit: Provider returned error")
                yield  # pragma: no cover — makes this a generator

        agent = Agent(model="stub", backend=AlwaysLimitedStream([]),
                      tools=None, max_steps=10, soul=None,
                      max_api_retries=2, debug=False)
        events = list(agent.run_stream("hi"))
        out = capsys.readouterr().out
        assert "[Resilience]" in out
        assert "pausing this run" in out
        assert any("response.failed" in ev for ev in events)
