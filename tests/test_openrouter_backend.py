"""
Tests for OpenRouterBackend — tool-call parsing, request construction,
and ReAct fallback when the upstream rejects the `tools` field.

Written by VTSTech — https://www.vts-tech.org
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from agentnova.plugins.openrouter.openrouter import OpenRouterBackend
from agentnova.core.models import Tool, ToolParam
from agentnova.core.types import ToolSupportLevel


def _make_tool() -> Tool:
    return Tool(
        name="shell",
        description="Run a shell command",
        params=[ToolParam(name="command", type="string", description="cmd")],
    )


class TestParseOpenAiResponse(unittest.TestCase):
    """Direct tests for the static _parse_openai_response helper."""

    def test_parses_native_tool_calls(self):
        """OpenAI-format tool_calls are extracted and arguments JSON-decoded."""
        raw = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        out = OpenRouterBackend._parse_openai_response(raw)
        self.assertEqual(out["content"], "")
        self.assertEqual(out["finish_reason"], "tool_calls")
        self.assertEqual(len(out["tool_calls"]), 1)
        tc = out["tool_calls"][0]
        self.assertEqual(tc["id"], "call_abc")
        self.assertEqual(tc["name"], "shell")
        self.assertEqual(tc["arguments"], {"command": "echo hi"})
        self.assertEqual(out["usage"]["total_tokens"], 15)

    def test_handles_arguments_as_object(self):
        """Some providers return arguments as an object, not a JSON string."""
        raw = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "calc",
                            "arguments": {"expression": "2+2"},
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        out = OpenRouterBackend._parse_openai_response(raw)
        self.assertEqual(out["tool_calls"][0]["arguments"], {"expression": "2+2"})

    def test_handles_malformed_arguments_gracefully(self):
        """Malformed JSON arguments don't crash — wrapped in _raw."""
        raw = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": "not-valid-json{",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        out = OpenRouterBackend._parse_openai_response(raw)
        # Should NOT crash; _raw fallback surfaces the bad payload.
        self.assertEqual(out["tool_calls"][0]["arguments"], {"_raw": "not-valid-json{"})

    def test_no_choices_returns_empty(self):
        """Missing choices → empty content/tool_calls, no crash."""
        out = OpenRouterBackend._parse_openai_response({})
        self.assertEqual(out["content"], "")
        self.assertEqual(out["tool_calls"], [])
        self.assertIsNone(out["finish_reason"])

    def test_text_only_response(self):
        """Plain text response (no tool_calls) parsed correctly."""
        raw = {
            "choices": [{
                "message": {"content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 4},
        }
        out = OpenRouterBackend._parse_openai_response(raw)
        self.assertEqual(out["content"], "Hello!")
        self.assertEqual(out["tool_calls"], [])
        self.assertEqual(out["finish_reason"], "stop")


class TestBuildOpenAiBody(unittest.TestCase):
    """Tests for the _build_openai_body request constructor."""

    def _backend(self):
        # Bypass __init__ — we only need the methods, not real config.
        b = OpenRouterBackend.__new__(OpenRouterBackend)
        return b

    def test_includes_tools_when_provided(self):
        b = self._backend()
        body = b._build_openai_body(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.5,
            max_tokens=128,
        )
        self.assertEqual(body["model"], "openai/gpt-4o")
        self.assertEqual(body["temperature"], 0.5)
        self.assertEqual(body["max_tokens"], 128)
        self.assertEqual(body["stream"], False)
        self.assertIn("tools", body)
        self.assertEqual(body["tools"][0]["function"]["name"], "shell")

    def test_omits_tools_when_none(self):
        b = self._backend()
        body = b._build_openai_body(
            model="m",
            messages=[],
            tools=None,
            temperature=0.7,
            max_tokens=10,
        )
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)

    def test_optional_params_added_only_when_provided(self):
        b = self._backend()
        body = b._build_openai_body(
            model="m",
            messages=[],
            tools=None,
            temperature=0.7,
            max_tokens=10,
            top_p=0.9,
            stop="END",
            tool_choice="auto",
            response_format={"type": "json_object"},
        )
        self.assertEqual(body["top_p"], 0.9)
        self.assertEqual(body["stop"], ["END"])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_uses_max_tokens_not_max_completion_tokens(self):
        """Compatibility: max_tokens is universally supported by free providers."""
        b = self._backend()
        body = b._build_openai_body(
            model="m", messages=[], tools=None, temperature=0.7, max_tokens=512,
        )
        self.assertIn("max_tokens", body)
        self.assertNotIn("max_completion_tokens", body)


class TestIsToolsNotSupportedError(unittest.TestCase):
    """Tests for the error-text matcher used by the ReAct fallback path."""

    def test_matches_known_indicators(self):
        cases = [
            "OpenRouter API error 400: Model does not support tools",
            "OpenRouter API error 400: tools are not supported for this model",
            "OpenRouter API error 400: Tool calling is not supported",
            "OpenRouter API error 400: does not support function calling",
            "OpenRouter API error 400: Function calling is not supported",
        ]
        for err in cases:
            with self.subTest(err=err):
                self.assertTrue(OpenRouterBackend._is_tools_not_supported_error(err))

    def test_does_not_match_unrelated_errors(self):
        cases = [
            "OpenRouter API error 401: unauthorized",
            "OpenRouter API error 429: rate limit",
            "OpenRouter API error 500: internal server error",
            "Connection refused",
        ]
        for err in cases:
            with self.subTest(err=err):
                self.assertFalse(OpenRouterBackend._is_tools_not_supported_error(err))


class TestGenerateFlow(unittest.TestCase):
    """End-to-end tests of generate() with _make_api_request mocked."""

    def _backend(self):
        b = OpenRouterBackend.__new__(OpenRouterBackend)
        b.api_key = "test-key"
        from agentnova.backends.base import BackendConfig
        b.config = BackendConfig()
        return b

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_sends_tools_and_parses_response(self, mock_req):
        """Happy path: tools sent, native tool_calls returned."""
        mock_req.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        b = self._backend()
        result = b.generate(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=100,
        )
        # Verify request body sent to _make_api_request
        sent_body = mock_req.call_args[0][1]
        self.assertEqual(sent_body["model"], "openai/gpt-4o")
        self.assertIn("tools", sent_body)
        self.assertEqual(sent_body["tools"][0]["function"]["name"], "shell")

        # Verify parsed response
        self.assertEqual(result["finish_reason"], "tool_calls")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "shell")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"command": "echo hi"})
        self.assertIn("latency_ms", result)

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_falls_back_when_tools_rejected(self, mock_req):
        """ReAct fallback: if upstream rejects tools, retry without tools."""
        # First call: 400 error saying tools not supported
        # Second call: success with text response (no tool_calls)
        mock_req.side_effect = [
            RuntimeError("OpenRouter API error 400: Model does not support tools"),
            {
                "choices": [{
                    "message": {"content": "I'll help with that."},
                    "tool_calls": [],
                    "finish_reason": "stop",
                }],
                "usage": {"total_tokens": 10},
            },
        ]
        b = self._backend()
        result = b.generate(
            model="some/free-model",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.5,
            max_tokens=50,
        )

        # _make_api_request called twice
        self.assertEqual(mock_req.call_count, 2)

        # Second call should NOT include tools
        second_body = mock_req.call_args_list[1][0][1]
        self.assertNotIn("tools", second_body)
        self.assertNotIn("tool_choice", second_body)

        # Result should reflect the text response
        self.assertEqual(result["content"], "I'll help with that.")
        self.assertEqual(result["tool_calls"], [])

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_does_not_fall_back_on_unrelated_error(self, mock_req):
        """Non-tool-related errors should propagate, not trigger fallback."""
        mock_req.side_effect = RuntimeError("OpenRouter API error 500: internal error")
        b = self._backend()
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(
                model="m",
                messages=[],
                tools=[_make_tool()],
                max_tokens=10,
            )
        # Should only have been called once (no retry)
        self.assertEqual(mock_req.call_count, 1)
        self.assertIn("500", str(ctx.exception))

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_synthesizes_finish_reason_when_missing(self, mock_req):
        """Some providers omit finish_reason — synthesize one."""
        mock_req.return_value = {
            "choices": [{
                "message": {"content": "hello"},
                # no finish_reason
            }],
        }
        b = self._backend()
        result = b.generate(model="m", messages=[], tools=None, max_tokens=10)
        self.assertEqual(result["finish_reason"], "stop")


class TestTestToolSupport(unittest.TestCase):
    """Tests for test_tool_support() — should always return NATIVE without probing."""

    def _backend(self):
        b = OpenRouterBackend.__new__(OpenRouterBackend)
        b.api_key = "test-key"
        from agentnova.backends.base import BackendConfig
        b.config = BackendConfig()
        return b

    def test_returns_native_without_api_call(self):
        b = self._backend()
        with patch.object(b, "_make_api_request") as m:
            result = b.test_tool_support("any/model", force_test=True)
            self.assertEqual(result, ToolSupportLevel.NATIVE)
            # Critical: NO live API call should be made
            m.assert_not_called()

    def test_returns_native_without_force_test(self):
        b = self._backend()
        result = b.test_tool_support("any/model", force_test=False)
        self.assertEqual(result, ToolSupportLevel.NATIVE)


if __name__ == "__main__":
    unittest.main()
