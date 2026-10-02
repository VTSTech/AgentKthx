"""
Insufficient-credits session model switch for the ZAI backend.

The 429 "insufficient credits" recovery works in two layers:

1. PER-REQUEST (inside the backend): the current request retries inline on
   the free fallback model so the turn completes. Historical behavior.

2. SESSION (the /model switch path): the backend fires a model-switch
   callback — registered by the CLI via
   ``agent_factory.register_insufficient_credits_switch`` — which runs the
   exact same code path as the in-chat ``/model`` command
   (``apply_model_switch``), so the WHOLE SESSION moves to the fallback
   model with num_ctx / num_predict / family config re-derived and the
   footer updated. This replaced the earlier internal fallback-flag map,
   which silently served the free model while ``agent.model`` (and every
   derived value) still showed the paid model.

The prompt-color tests at the bottom pin the chat "You:" prompt color
(yellow ``\\033[33m``, not the old dim grey ``\\033[90m``).
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.cli.agent_factory import register_insufficient_credits_switch
from agentkthx.plugins.zai.zai import ZAI_FREE_FALLBACK_MODEL, ZaiBackend

SSE_LINES = [
    b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]


def _make_zai_backend():
    """Construct a ZaiBackend without network or __init__ side effects."""
    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    b._api_key = "test-key"
    b._model_switch_callback = None
    b._get_model_defaults = MagicMock(
        return_value={
            "temperature": 0.7,
            "max_tokens": 4096,
        }
    )
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
# Backend side: 429 fires the callback, request still falls back inline
# ---------------------------------------------------------------------------


class TestStreamCreditsCallback(unittest.TestCase):

    def test_stream_429_fires_model_switch_callback(self):
        """A 429 on a paid model fires the callback with (paid, fallback)
        and the retried request uses the free model."""
        b = _make_zai_backend()
        fired = []
        b.set_model_switch_callback(lambda failed, fb: fired.append((failed, fb)))

        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            if len(calls) == 1:
                raise _http_429()
            return _sse_response()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            chunks = list(
                b.generate_completions_stream(
                    model="glm-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )

        self.assertEqual(fired, [("glm-5.1", ZAI_FREE_FALLBACK_MODEL)])
        self.assertEqual(calls[0]["model"], "glm-5.1")
        self.assertEqual(calls[1]["model"], ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual("".join(c["delta"] for c in chunks), "Hello")

    def test_stream_no_callback_still_falls_back_per_request(self):
        """Without a registered callback (library usage), the historical
        per-request fallback keeps the turn working."""
        b = _make_zai_backend()
        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(json.loads(req.data.decode("utf-8")))
            if len(calls) == 1:
                raise _http_429()
            return _sse_response()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            chunks = list(
                b.generate_completions_stream(
                    model="glm-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )

        self.assertEqual(calls[1]["model"], ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual("".join(c["delta"] for c in chunks), "Hello")

    def test_stream_callback_exception_never_breaks_the_request(self):
        """A broken callback must not turn a recoverable 429 into a
        crashed turn — the fallback retry still completes."""
        b = _make_zai_backend()

        def _boom(failed, fb):
            raise RuntimeError("callback bug")

        b.set_model_switch_callback(_boom)

        def _fake_urlopen(req, timeout=None):
            body = json.loads(req.data.decode("utf-8"))
            if body["model"] != ZAI_FREE_FALLBACK_MODEL:
                raise _http_429()
            return _sse_response()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            chunks = list(
                b.generate_completions_stream(
                    model="glm-5.1",
                    messages=[{"role": "user", "content": "hi"}],
                )
            )

        self.assertEqual("".join(c["delta"] for c in chunks), "Hello")


class TestNonStreamCreditsCallback(unittest.TestCase):

    def test_nonstream_429_fires_model_switch_callback(self):
        """Non-streaming 429 on a paid model fires the callback; the
        retried request succeeds."""
        b = _make_zai_backend()
        fired = []
        b.set_model_switch_callback(lambda failed, fb: fired.append((failed, fb)))

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

        self.assertEqual(fired, [("glm-5.1", ZAI_FREE_FALLBACK_MODEL)])
        self.assertEqual(calls[1]["model"], ZAI_FREE_FALLBACK_MODEL)
        self.assertEqual(result["content"], "ok")

    def test_nonstream_no_callback_still_falls_back_per_request(self):
        """Callback-less non-streaming path keeps the inline fallback."""
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

        self.assertEqual(result["content"], "ok")

    def test_free_model_429_never_fires_callback(self):
        """A 429 on an already-free model has nothing to switch to — it
        raises (existing behavior) and the callback is NOT fired."""
        b = _make_zai_backend()
        fired = []
        b.set_model_switch_callback(lambda failed, fb: fired.append((failed, fb)))

        with patch(
            "urllib.request.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(_http_429())
        ):
            with self.assertRaises(RuntimeError):
                b._generate_with_auth(
                    model="glm-4.5-flash",
                    messages=[{"role": "user", "content": "hi"}],
                )

        self.assertEqual(fired, [])


# ---------------------------------------------------------------------------
# CLI side: the callback runs the proper /model switch path
# ---------------------------------------------------------------------------


def _make_dummy_agent_with_zai():
    """A stand-in Agent wired to a catalog-mocked ZaiBackend."""
    backend = _make_zai_backend()
    backend.is_cloud = True
    backend._context_safe_max_tokens = 12345  # stale value from the OLD model
    backend.get_model_info = MagicMock(return_value=None)
    backend.get_model_max_context = MagicMock(return_value=132000)
    backend._get_model_defaults = MagicMock(
        return_value={
            "temperature": 0.7,
            "max_tokens": 98304,
        }
    )

    agent = SimpleNamespace(
        model="glm-5.1",
        num_ctx=204800,
        _num_predict=None,
        backend=backend,
        model_config=SimpleNamespace(default_max_tokens=131072, default_temperature=0.7),
        model_family="glm",
        _num_ctx_explicit=False,
        _num_predict_explicit=False,
    )
    return agent, backend


class TestRegisterInsufficientCreditsSwitch(unittest.TestCase):

    def test_registers_callback_on_capable_backend(self):
        agent, backend = _make_dummy_agent_with_zai()
        register_insufficient_credits_switch(agent)
        self.assertIsNotNone(backend._model_switch_callback)
        self.assertTrue(callable(backend._model_switch_callback))

    def test_callback_runs_the_model_switch_path(self):
        """Firing the callback must switch agent.model through
        apply_model_switch — per-model state re-derived, stale
        context-safe max_tokens cleared."""
        agent, backend = _make_dummy_agent_with_zai()
        register_insufficient_credits_switch(agent)

        buf = io.StringIO()
        with patch("sys.stdout", new=buf):
            backend._model_switch_callback("glm-5.1", ZAI_FREE_FALLBACK_MODEL)

        # The session model actually moved (footer renders agent.model)
        self.assertEqual(agent.model, ZAI_FREE_FALLBACK_MODEL)
        # num_ctx re-derived from the NEW model's catalog (132000, was 204800)
        self.assertEqual(agent.num_ctx, 132000)
        # num_predict re-derived (98304, was None = model default)
        self.assertEqual(agent._num_predict, 98304)
        # stale context-safe max_tokens cleared by the switch
        self.assertIsNone(backend._context_safe_max_tokens)
        # the user-visible notice printed
        self.assertIn("session model switched", buf.getvalue())
        self.assertIn("glm-5.1 -> glm-4.5-flash", buf.getvalue())

    def test_callback_guard_does_not_clobber_user_switch(self):
        """If the session already moved to a different model (explicit
        /model), a stale 429 for the old model must not switch it."""
        agent, backend = _make_dummy_agent_with_zai()
        agent.model = "glm-4.7"  # user switched mid-flight
        register_insufficient_credits_switch(agent)

        backend._model_switch_callback("glm-5.1", ZAI_FREE_FALLBACK_MODEL)

        self.assertEqual(agent.model, "glm-4.7", "user's explicit model must win")

    def test_register_skips_backends_without_hook(self):
        """Non-cloud / non-ZAI backends without set_model_switch_callback
        are simply skipped — no error."""
        agent = SimpleNamespace(model="m", backend=SimpleNamespace())
        register_insufficient_credits_switch(agent)  # must not raise

    def test_register_skips_missing_backend(self):
        agent = SimpleNamespace(model="m")  # no backend attr at all
        register_insufficient_credits_switch(agent)  # must not raise


# ---------------------------------------------------------------------------
# Chat "You:" prompt color
# ---------------------------------------------------------------------------


class TestChatPromptColor(unittest.TestCase):

    def test_primary_user_prompt_is_yellow(self):
        """R07.19: the chat input prompt renders '{primary_user}:' in yellow
        (was the hardcoded 'You:' pre-R07.19). Source-level pin: the prompt
        is built inside a closure, so grep the module source. The yellow
        SGR form (\\033[33m) and the readline zero-width markers
        (\\001 / \\002) must both survive the rename."""
        chat_py = Path(__file__).resolve().parents[1] / "agentkthx" / "cli" / "commands" / "chat.py"
        src = chat_py.read_text(encoding="utf-8")
        self.assertIn(
            "\\033[33m\\002{primary_user}:",
            src,
            "chat prompt must render the Primary User in yellow (\\033[33m)",
        )
        self.assertNotIn(
            "\\002You:",
            src,
            "hardcoded 'You:' prompt must not return (R07.19 renames it)",
        )


if __name__ == "__main__":
    unittest.main()
