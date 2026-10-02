"""R07.19 follow-up #12 — ZAI tool-support probe hardening.

Live finding: glm-4.7-flash IS a native tool-calling model (verified
against api.z.ai — finish_reason "tool_calls" on every clean call), but
``models --backend zai`` labeled it react. Root cause chain:

  1. the free tier rate-limits rapid sequential probes — HTTP 429
     ``{"error":{"code":"1302","message":"Rate limit reached for
     requests"}}`` reproduced with 5 back-to-back probes;
  2. the old probe cached REACT for ANY HTTPError;
  3. ``--no-cache`` only skips cache READS — the poisoned write still
     happened, so the false react verdict outlived the run.

Pinned here:

  - probe body sends ``thinking: {"type": "disabled"}`` and
    ``max_tokens: 512`` (was 200, thinking left at model default);
  - transient failures (429 / 5xx / rate-limit text / network errors /
    empty-choices 200s) retry with backoff; a persisting transient
    failure returns UNTESTED and DOES NOT write the cache;
  - a definitive tools-param rejection (400 + rejection language)
    still caches REACT;
  - non-rejection HTTP errors (401 auth) return UNTESTED uncached;
  - clean tool_calls replies cache NATIVE; ReAct text patterns and
    tools-accepted-no-call replies cache REACT (unchanged).
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest

from agentkthx.core.tool_cache import get_cached_tool_support
from agentkthx.core.types import ToolSupportLevel
from agentkthx.plugins.zai.zai import ZaiBackend

MODEL = "glm-4.7-flash"


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal context-manager response for urlopen mocking."""

    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._raw


def _ok_payload(tool_calls=None, content="It is sunny in Tokyo."):
    message: dict = {"content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def _native_payload():
    return _ok_payload(
        tool_calls=[
            {
                "type": "function",
                "id": "call_test",
                "function": {"name": "get_weather", "arguments": '{"location":"Tokyo"}'},
            }
        ],
        content="",
    )


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.z.ai/api/paas/v4/chat/completions",
        code=code,
        msg="error",
        hdrs={},
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _rate_limited() -> urllib.error.HTTPError:
    return _http_error(429, '{"error":{"code":"1302","message":"Rate limit reached for requests"}}')


class _ProbeScript:
    """urlopen stand-in playing a queued script and recording requests."""

    def __init__(self, script):
        # script items: payload dicts (200 reply) or Exception instances
        self._script = list(script)
        self.requests: list = []
        self.calls = 0

    def __call__(self, req, timeout=None):  # noqa: ANN001, ANN003
        self.calls += 1
        self.requests.append(req)
        if not self._script:
            raise urllib.error.URLError("probe script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


@pytest.fixture()
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent tool-support cache at a temp dir."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def _probe(monkeypatch, script):
    """Install the script as urlopen, run one force probe, return both."""
    recorder = _ProbeScript(script)
    monkeypatch.setattr(urllib.request, "urlopen", recorder)
    backend = ZaiBackend(api_key="test-key-0123456789abcdef", base_url="https://api.z.ai")
    verdict = backend.test_tool_support(MODEL, force_test=True)
    return verdict, recorder


def _request_body(recorder) -> dict:
    return json.loads(recorder.requests[0].data.decode("utf-8"))


# ─────────────────────────────────────────────────────────────────────
# Probe body
# ─────────────────────────────────────────────────────────────────────


def test_probe_body_disables_thinking_and_uses_512_tokens(isolated_cache, no_sleep, monkeypatch):
    verdict, recorder = _probe(monkeypatch, [_native_payload()])

    assert verdict is ToolSupportLevel.NATIVE
    body = _request_body(recorder)
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 512
    assert body["stream"] is False
    assert body["tools"], "probe must include the tools schema"


# ─────────────────────────────────────────────────────────────────────
# Clean-verdict caching (unchanged behavior)
# ─────────────────────────────────────────────────────────────────────


def test_native_tool_calls_cached(isolated_cache, no_sleep, monkeypatch):
    verdict, _ = _probe(monkeypatch, [_native_payload()])

    assert verdict is ToolSupportLevel.NATIVE
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.NATIVE


def test_react_text_pattern_cached(isolated_cache, no_sleep, monkeypatch):
    payload = _ok_payload(content="Thought: I need the weather\nAction: get_weather")
    verdict, _ = _probe(monkeypatch, [payload])

    assert verdict is ToolSupportLevel.REACT
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.REACT


def test_tools_accepted_no_calls_cached_react(isolated_cache, no_sleep, monkeypatch):
    verdict, _ = _probe(monkeypatch, [_ok_payload()])

    assert verdict is ToolSupportLevel.REACT
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.REACT


# ─────────────────────────────────────────────────────────────────────
# Transient failures — retry, then UNTESTED without poisoning cache
# ─────────────────────────────────────────────────────────────────────


def test_rate_limit_then_success_returns_native(isolated_cache, no_sleep, monkeypatch):
    """One 429 between clean calls must not flip the verdict to react."""
    verdict, recorder = _probe(monkeypatch, [_rate_limited(), _native_payload()])

    assert verdict is ToolSupportLevel.NATIVE
    assert recorder.calls == 2
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.NATIVE


def test_persistent_rate_limit_untested_and_not_cached(isolated_cache, no_sleep, monkeypatch):
    verdict, recorder = _probe(monkeypatch, [_rate_limited() for _ in range(3)])

    assert verdict is ToolSupportLevel.UNTESTED
    assert recorder.calls == 3, "must exhaust the retry budget"
    assert get_cached_tool_support(MODEL) is None, "transient failure must not cache"


def test_persistent_server_error_untested_and_not_cached(isolated_cache, no_sleep, monkeypatch):
    script = [_http_error(500, '{"error":{"message":"upstream unavailable"}}') for _ in range(3)]
    verdict, _ = _probe(monkeypatch, script)

    assert verdict is ToolSupportLevel.UNTESTED
    assert get_cached_tool_support(MODEL) is None


def test_persistent_timeout_untested_and_not_cached(isolated_cache, no_sleep, monkeypatch):
    """The old generic except cached REACT on timeouts — now UNTESTED."""
    script = [urllib.error.URLError("timed out") for _ in range(3)]
    verdict, _ = _probe(monkeypatch, script)

    assert verdict is ToolSupportLevel.UNTESTED
    assert get_cached_tool_support(MODEL) is None


def test_empty_choices_retries_then_untested_uncached(isolated_cache, no_sleep, monkeypatch):
    verdict, recorder = _probe(monkeypatch, [{"choices": []} for _ in range(3)])

    assert verdict is ToolSupportLevel.UNTESTED
    assert recorder.calls == 3
    assert get_cached_tool_support(MODEL) is None


def test_auth_error_not_cached(isolated_cache, no_sleep, monkeypatch):
    """401/403 leave capability unknown — no REACT guess, no cache write."""
    auth_error = _http_error(401, '{"error":{"message":"Invalid API key"}}')
    verdict, recorder = _probe(monkeypatch, [auth_error])

    assert verdict is ToolSupportLevel.UNTESTED
    assert recorder.calls == 1, "auth errors are not retried"
    assert get_cached_tool_support(MODEL) is None


def test_rate_limit_detected_from_body_even_on_odd_status(isolated_cache, no_sleep, monkeypatch):
    """Some gateways return 200-wrapped or odd statuses with rate-limit text."""
    weird = _http_error(418, '{"error":{"code":"1302","message":"Rate limit reached"}}')
    verdict, recorder = _probe(monkeypatch, [weird, _native_payload()])

    assert verdict is ToolSupportLevel.NATIVE
    assert recorder.calls == 2


# ─────────────────────────────────────────────────────────────────────
# Definitive rejection — still caches REACT
# ─────────────────────────────────────────────────────────────────────


def test_tools_param_rejection_caches_react(isolated_cache, no_sleep, monkeypatch):
    rejection = _http_error(400, '{"error":{"message":"tools not supported for this model"}}')
    verdict, recorder = _probe(monkeypatch, [rejection])

    assert verdict is ToolSupportLevel.REACT
    assert recorder.calls == 1, "definitive rejection must not retry"
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.REACT


def test_does_not_support_rejection_caches_react(isolated_cache, no_sleep, monkeypatch):
    rejection = _http_error(400, '{"error":{"message":"this model does not support tools"}}')
    verdict, _ = _probe(monkeypatch, [rejection])

    assert verdict is ToolSupportLevel.REACT
    assert get_cached_tool_support(MODEL) is ToolSupportLevel.REACT
