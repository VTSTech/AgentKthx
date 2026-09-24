"""Tests for R06.54: ZaiBackend.generate_completions_stream override.

R06.53 added a similar override on OpenRouterBackend — ZAI had the same
bug: the inherited ``OllamaBackend.generate_completions_stream`` builds
the URL as ``{self.base_url}/v1/chat/completions`` which on ZAI yields
``https://api.z.ai/v1/chat/completions`` (nginx 404). ZAI's actual
endpoint is ``/api/paas/v4/chat/completions``.

These tests verify the override exists, uses the correct URL, emits the
expected dict shape, and handles the SSE stream parsing correctly.
"""
import io
import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from agentkthx.plugins.zai.zai import ZaiBackend


def _make_zai_backend():
    """Construct a ZaiBackend bypassing __init__."""
    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    # OllamaBackend uses self.base_url as a property — mirror that.
    # The property reads self._base_url, so we just set the underlying attr.
    b._api_key = "test-key"
    # _get_model_defaults is needed — mock it.
    b._get_model_defaults = MagicMock(return_value={
        "temperature": 0.6,
        "max_tokens": 4096,
    })
    b.config = MagicMock()
    b.config.timeout = 30.0
    return b


class TestZaiStreamMethodOverride(unittest.TestCase):
    """ZaiBackend must override generate_completions_stream (R06.54).

    Same bug class as OpenRouter in R06.53: inherited OllamaBackend method
    builds ``{base_url}/v1/chat/completions`` which returns 404 on ZAI.
    """

    def test_method_is_defined_on_zai_not_inherited(self):
        """generate_completions_stream must be in ZaiBackend's __dict__,
        not inherited from OllamaBackend."""
        self.assertIn(
            "generate_completions_stream",
            ZaiBackend.__dict__,
            "ZaiBackend must override generate_completions_stream — "
            "otherwise it inherits OllamaBackend's URL builder which "
            "produces a 404 on ZAI's /v1/chat/completions path.",
        )

    def test_method_uses_zai_endpoint(self):
        """_get_chat_completions_url must return ZAI's endpoint path,
        not OllamaBackend's /v1/chat/completions path."""
        b = _make_zai_backend()
        url = b._get_chat_completions_url()
        self.assertIn("/api/paas/v4/chat/completions", url)
        self.assertNotIn("/v1/chat/completions", url)

    def test_method_does_not_use_ollama_path(self):
        """_get_chat_completions_url must NOT contain /v1/chat/completions."""
        b = _make_zai_backend()
        url = b._get_chat_completions_url()
        self.assertNotIn("/v1/chat/completions", url)


class TestZaiStreamMethodShape(unittest.TestCase):
    """Smoke tests for the streaming method's behavior.

    We can't make real HTTP calls, but we can mock urllib to verify the
    method builds the correct request and yields the expected dict shape.
    """

    def test_yields_correct_dict_shape_from_sse(self):
        """Mock urllib.urlopen to yield a fake SSE stream, verify the
        method yields the dict shape Agent._generate_stream() expects."""
        b = _make_zai_backend()

        # Fake SSE response: two content deltas then [DONE]
        sse_lines = [
            b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"content":", world!"},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        # Mock urllib.request.urlopen to return an iterable response
        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(return_value=iter(sse_lines))
        fake_response.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake_response):
            chunks = list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            ))

        # Should yield 3 chunks (the [DONE] marker is consumed internally)
        self.assertEqual(len(chunks), 3)

        # Verify each chunk has the expected shape
        for chunk in chunks:
            self.assertIn("delta", chunk)
            self.assertIn("tool_calls", chunk)
            self.assertIn("finish_reason", chunk)

        # Verify content accumulation
        content = "".join(c["delta"] for c in chunks)
        self.assertEqual(content, "Hello, world!")

        # Verify finish_reason on the final chunk
        self.assertEqual(chunks[-1]["finish_reason"], "stop")

    def test_yields_reasoning_content_when_present(self):
        """Thinking models emit reasoning_content in deltas — must be
        surfaced in the yielded chunk."""
        b = _make_zai_backend()

        sse_lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking...","content":""},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(return_value=iter(sse_lines))
        fake_response.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake_response):
            chunks = list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            ))

        # First chunk should carry reasoning_content
        self.assertEqual(chunks[0]["reasoning_content"], "thinking...")
        # Second chunk should NOT carry reasoning_content (empty/missing)
        self.assertNotIn("reasoning_content", chunks[1])

    def test_yields_tool_calls_delta_when_present(self):
        """Tool calls arrive as deltas — must be surfaced in yielded chunk."""
        b = _make_zai_backend()

        sse_lines = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"shell","arguments":""}}]},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\":\\"ls\\"}"}}]},"finish_reason":null}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(return_value=iter(sse_lines))
        fake_response.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake_response):
            chunks = list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "run ls"}],
            ))

        # First chunk should carry the tool_calls delta with id + name
        self.assertIsNotNone(chunks[0]["tool_calls"])
        self.assertEqual(chunks[0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(chunks[0]["tool_calls"][0]["function"]["name"], "shell")
        # Second chunk: arguments fragment
        self.assertIsNotNone(chunks[1]["tool_calls"])
        self.assertEqual(
            chunks[1]["tool_calls"][0]["function"]["arguments"],
            '{"command":"ls"}',
        )

    def test_body_includes_stream_options_include_usage(self):
        """PERF-02: stream_options.include_usage must be in the request body
        so ZAI emits a final usage-carrying chunk."""
        b = _make_zai_backend()

        # Capture the request body by inspecting the urllib call
        captured_body = {}

        def _fake_urlopen(req, timeout=None):
            captured_body["body"] = json.loads(req.data.decode("utf-8"))
            # Return an empty SSE stream
            fake_response = MagicMock()
            fake_response.__iter__ = MagicMock(return_value=iter([]))
            fake_response.close = MagicMock()
            return fake_response

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            ))

        self.assertIn("body", captured_body)
        body = captured_body["body"]
        self.assertEqual(body["stream"], True)
        self.assertEqual(
            body.get("stream_options"),
            {"include_usage": True},
            "ZAI streaming requests must send stream_options.include_usage "
            "so usage stats are emitted in the final SSE chunk.",
        )

    def test_body_uses_zai_endpoint(self):
        """Request URL must be the ZAI-specific /api/paas/v4/chat/completions,
        not OllamaBackend's /v1/chat/completions."""
        b = _make_zai_backend()

        captured_url = {}

        def _fake_urlopen(req, timeout=None):
            captured_url["url"] = req.full_url
            fake_response = MagicMock()
            fake_response.__iter__ = MagicMock(return_value=iter([]))
            fake_response.close = MagicMock()
            return fake_response

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            ))

        self.assertIn("url", captured_url)
        url = captured_url["url"]
        self.assertIn("/api/paas/v4/chat/completions", url)
        self.assertNotIn(
            "/v1/chat/completions", url,
            f"URL must not contain /v1/chat/completions (that's OllamaBackend's "
            f"path which returns 404 on ZAI). Got: {url}",
        )

    def test_authorization_header_present(self):
        """ZAI requires Bearer token auth — must be in the request headers."""
        b = _make_zai_backend()

        captured_headers = {}

        def _fake_urlopen(req, timeout=None):
            captured_headers["headers"] = dict(req.headers)
            fake_response = MagicMock()
            fake_response.__iter__ = MagicMock(return_value=iter([]))
            fake_response.close = MagicMock()
            return fake_response

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            list(b.generate_completions_stream(
                model="glm-4.5-flash",
                messages=[{"role": "user", "content": "hi"}],
            ))

        # urllib normalizes header names — Authorization becomes Authorization
        # but keys are case-insensitive via req.headers
        auth_present = any(
            k.lower() == "authorization" and "test-key" in v
            for k, v in captured_headers["headers"].items()
        )
        self.assertTrue(
            auth_present,
            f"Authorization header with Bearer token must be present. "
            f"Got headers: {captured_headers['headers']}",
        )


if __name__ == "__main__":
    unittest.main()
