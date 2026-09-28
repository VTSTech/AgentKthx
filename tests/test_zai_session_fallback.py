"""
Session-sticky insufficient-credits fallback for the ZAI backend.

Before this change, the 429 "insufficient credits" fallback was
PER-REQUEST: every request re-attempted the paid model, ate a failed
round-trip, then silently switched to the free fallback for that one
request only. The next request paid the same penalty again.

Now the switch is RECORDED on the ZaiBackend instance (which lives for
the whole Agent/chat session — see cli/agent_factory.py — and survives
/model switches): subsequent requests for a burned-out model go straight
to the free fallback model with no doomed paid attempt.

Also pins the chat "You:" prompt color (changed from dim grey
``\\033[90m`` to yellow ``\\033[33m``) so readline marker refactors
cannot silently revert it.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.zai.zai import ZaiBackend, ZAI_FREE_FALLBACK_MODEL

SSE_LINES = [
    b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    b'data: [DONE]\n\n',
]


def _make_zai_backend():
    """Construct a ZaiBackend without network or __init__ side effects."""
    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    b._api_key = "test-key"
    # Session-sticky fallback state (normally initialized in __init__).
    b._credits_fallback_models = {}
    b._credits_fallback_announced = set()
    b._get_model_defaults = MagicMock(return_value={
        "temperature": 0.7,
        "max_tokens": 4096,
    })
    b.config = MagicMock()
    b.config.timeout = 30.0
    return b


def _http_429():
    """A 429 HTTPError whose body matches ZAI's insufficient-credits text."""
    return urllib.error.HTTPError(
        "https://api.z.ai/api/paas/v4/chat/completions",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(b'{"error":{"code":"1113","message":"Insufficient balance"}}'),
    )


def _sse_response():
    mock = MagicMock()
    mock.__iter__ = MagicMock(return_value=iter(SSE_LINES))
    mock.close = MagicMock()
    return mock


def _json_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _completion_payload(model: str = ZAI_FREE_FALLBACK_MODEL) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "model": model,
    }


# ---------------------------------------------------------------------------
# Streaming path (generate_completions_stream -> _iter_sse_lines)
# ---------------------------------------------------------------------------

class TestStreamSessionFallback(unittest.TestCase):

    def test_stream_429_records_session_fallback(self):
        """A 429 on a paid model records the paid->free switch for the session."""
        b = _make_zai_backend()
        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            if len(calls) == 1:
                raise _http_429()
            return _sse_response()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            chunks = list(b.generate_completions_stream(
                model="glm-5.1",
                messages=[{"role": "user", "content": "hi"}],
            ))

        # First request attempted the paid model, retry used the free one
        self.assertEqual(calls[0]["model"], "glm-5.1")
        self.assertEqual(calls[1]["model"], ZAI_FREE_FALLBACK_MODEL)
        # ...and the switch is now sticky for the session
        self.assertEqual(
            b._credits_fallback_models,
            {"glm-5.1": ZAI_FREE_FALLBACK_MODEL},
        )
        self.assertEqual("".join(c["delta"] for c in chunks), "Hello")

    def test_stream_second_request_skips_paid_model(self):
        """The request AFTER a recorded burnout must not attempt the paid
        model at all — one urlopen call, straight to the free model."""
        b = _make_zai_backend()
        b._credits_fallback_models = {"glm-5.1": ZAI_FREE_FALLBACK_MODEL}

        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            return _sse_response()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            list(b.generate_completions_stream(
                model="glm-5.1",
                messages=[{"role": "user", "content": "hi"}],
            ))

        self.assertEqual(
            len(calls), 1,
            "recorded burnout must skip the paid attempt — exactly one "
            "request expected",
        )
        self.assertEqual(calls[0]["model"], ZAI_FREE_FALLBACK_MODEL)

    def test_stream_announces_sticky_switch_once(self):
        """The 'no credits left this session' notice prints once per model,
        not on every subsequent request."""
        b = _make_zai_backend()
        b._credits_fallback_models = {"glm-5.1": ZAI_FREE_FALLBACK_MODEL}

        buf = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: _sse_response()):
            with contextlib.redirect_stdout(buf):
                list(b.generate_completions_stream(
                    model="glm-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                ))
                list(b.generate_completions_stream(
                    model="glm-5.1",
                    messages=[{"role": "user", "content": "hi again"}],
                ))

        self.assertEqual(
            buf.getvalue().count("no credits left this session"), 1,
            "sticky-switch notice must print exactly once per model",
        )


# ---------------------------------------------------------------------------
# Non-streaming path (_generate_with_auth)
# ---------------------------------------------------------------------------

class TestNonStreamSessionFallback(unittest.TestCase):

    def test_nonstream_429_records_session_fallback(self):
        """Non-streaming 429 on a paid model records the switch and the
        retried request succeeds."""
        b = _make_zai_backend()
        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            if len(calls) == 1:
                raise _http_429()
            return _json_response(_completion_payload())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            result = b._generate_with_auth(
                model="glm-5.1",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(calls[0]["model"], "glm-5.1")
        self.assertEqual(calls[1]["model"], ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual(
            b._credits_fallback_models,
            {"glm-5.1": ZAI_FREE_FALLBACK_MODEL},
        )
        self.assertEqual(result["content"], "ok")

    def test_nonstream_second_call_skips_paid_model(self):
        """Subsequent non-streaming call goes straight to the free model."""
        b = _make_zai_backend()
        b._credits_fallback_models = {"glm-5.1": ZAI_FREE_FALLBACK_MODEL}

        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            return _json_response(_completion_payload())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            b._generate_with_auth(
                model="glm-5.1",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], ZAI_FREE_FALLBACK_MODEL)


# ---------------------------------------------------------------------------
# Scoping / safety of the sticky map
# ---------------------------------------------------------------------------

class TestSessionFallbackScoping(unittest.TestCase):

    def test_other_models_not_redirected(self):
        """Recording a burnout for glm-5.1 must not affect other models."""
        b = _make_zai_backend()
        b._credits_fallback_models = {"glm-5.1": ZAI_FREE_FALLBACK_MODEL}

        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            return _json_response(_completion_payload())

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            b._generate_with_auth(
                model="glm-4.7",
                messages=[{"role": "user", "content": "hi"}],
            )

        self.assertEqual(calls[0]["model"], "glm-4.7")

    def test_free_model_429_not_recorded(self):
        """A 429 on an already-free model has nothing to fall back to —
        it raises (existing behavior) and must NOT pollute the map."""
        b = _make_zai_backend()

        with patch("urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(_http_429())):
            with self.assertRaises(RuntimeError):
                b._generate_with_auth(
                    model="glm-4.5-flash",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertEqual(b._credits_fallback_models, {})

    def test_record_ignores_free_models(self):
        """_record_credit_fallback is a no-op for free models."""
        b = _make_zai_backend()
        self.assertIsNone(b._record_credit_fallback("glm-4.5-flash"))
        self.assertIsNone(b._record_credit_fallback("glm-4.7-flash"))
        self.assertIsNone(b._record_credit_fallback(""))
        self.assertEqual(b._credits_map(), {})

    def test_map_isolated_per_instance(self):
        """Each backend instance (== each session) has its own map."""
        b1 = _make_zai_backend()
        b2 = _make_zai_backend()
        b1._record_credit_fallback("glm-5.1")
        self.assertEqual(b1._credits_map(), {"glm-5.1": ZAI_FREE_FALLBACK_MODEL})
        self.assertEqual(b2._credits_map(), {})

    def test_apply_session_fallback_announces_once(self):
        """_apply_session_fallback rewrites the model and announces the
        sticky switch exactly once; later calls are silent no-ops."""
        b = _make_zai_backend()
        b._record_credit_fallback("glm-5.1")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m1 = b._apply_session_fallback("glm-5.1")
            m2 = b._apply_session_fallback("glm-5.1")
            # Free / unknown models pass through untouched
            m3 = b._apply_session_fallback("glm-4.5-flash")

        self.assertEqual(m1, ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual(m2, ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual(m3, "glm-4.5-flash")
        self.assertEqual(buf.getvalue().count("no credits left this session"), 1)


# ---------------------------------------------------------------------------
# Chat "You:" prompt color
# ---------------------------------------------------------------------------

class TestChatPromptColor(unittest.TestCase):

    def test_you_prompt_is_yellow(self):
        """The chat input prompt must render 'You:' in yellow (\\033[33m),
        not the old dim grey (\\033[90m). Source-level pin: the prompt is
        built inside a closure, so grep the module source."""
        chat_py = (
            Path(__file__).resolve().parents[1]
            / "agentkthx" / "cli" / "commands" / "chat.py"
        )
        src = chat_py.read_text(encoding="utf-8")
        self.assertIn(
            "\\033[33m\\002You:",
            src,
            "chat 'You:' prompt must use yellow (\\033[33m)",
        )
        self.assertNotIn(
            "\\033[90m\\002You:",
            src,
            "chat 'You:' prompt must not revert to dim grey (\\033[90m)",
        )


if __name__ == "__main__":
    unittest.main()
