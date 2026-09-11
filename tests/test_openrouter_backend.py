"""
Tests for OpenRouterBackend — tool-call parsing, request construction,
and ReAct fallback when the upstream rejects the `tools` field.

Also tests the CLI's _print_agent_steps helper that surfaces tool calls
to the user in chat / run mode.

Written by VTSTech — https://www.vts-tech.org
"""

import io
import json
import sys
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

    def test_no_choices_raises(self):
        """Missing choices raises RuntimeError so the chat loop can surface it."""
        with self.assertRaises(RuntimeError) as ctx:
            OpenRouterBackend._parse_openai_response({})
        self.assertIn("no choices", str(ctx.exception).lower())

    def test_provider_error_field_raises(self):
        """HTTP 200 + top-level `error` field raises with the provider message.

        OpenRouter sometimes returns 200 with a provider-side error (e.g.
        "Provider rate limited"). This must surface as a RuntimeError so
        the user sees a real message instead of an empty response.
        """
        # Format 1: error as dict with message
        with self.assertRaises(RuntimeError) as ctx:
            OpenRouterBackend._parse_openai_response({
                "error": {"message": "Provider rate limited", "code": 429},
            })
        self.assertIn("Provider rate limited", str(ctx.exception))

        # Format 2: error as string
        with self.assertRaises(RuntimeError) as ctx:
            OpenRouterBackend._parse_openai_response({
                "error": "Upstream connection error",
            })
        self.assertIn("Upstream connection error", str(ctx.exception))

    def test_provider_error_takes_precedence_over_choices(self):
        """If both `error` and `choices` exist, the error wins."""
        with self.assertRaises(RuntimeError):
            OpenRouterBackend._parse_openai_response({
                "error": {"message": "Provider failed mid-stream"},
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            })

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

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_raises_on_empty_response(self, mock_req):
        """Empty content + no tool_calls should raise, not silently return."""
        mock_req.return_value = {
            "choices": [{
                "message": {"content": "", "tool_calls": []},
                "finish_reason": "stop",
            }],
        }
        b = self._backend()
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(model="m", messages=[], tools=None, max_tokens=10)
        self.assertIn("empty response", str(ctx.exception).lower())

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_raises_on_whitespace_only_response(self, mock_req):
        """Whitespace-only content + no tool_calls should also raise."""
        mock_req.return_value = {
            "choices": [{
                "message": {"content": "   \n  \n  ", "tool_calls": []},
                "finish_reason": "stop",
            }],
        }
        b = self._backend()
        with self.assertRaises(RuntimeError) as ctx:
            b.generate(model="m", messages=[], tools=None, max_tokens=10)
        self.assertIn("empty response", str(ctx.exception).lower())

    @patch.object(OpenRouterBackend, "_make_api_request")
    def test_generate_does_not_raise_on_empty_content_with_tool_calls(self, mock_req):
        """Empty content WITH tool_calls is valid (model called a tool)."""
        mock_req.return_value = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "x", "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        b = self._backend()
        result = b.generate(model="m", messages=[], tools=None, max_tokens=10)
        self.assertEqual(len(result["tool_calls"]), 1)


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


class TestPrintAgentSteps(unittest.TestCase):
    """Tests for the CLI's _print_agent_steps helper.

    This helper surfaces tool calls + their results to the user in chat
    and run mode, so the agent's actions are visible — not just the
    final answer.
    """

    def _make_run(self, steps):
        """Build a minimal AgentRun-like object with the given steps."""
        from agentnova.core.models import AgentRun, StepResult, ToolCall
        from agentnova.core.types import StepResultType
        return AgentRun(
            final_answer="done",
            steps=steps,
            total_tokens=0,
            total_ms=0.0,
            tool_calls=len(steps),
            success=True,
        )

    def _make_tool_step(self, name, args, result):
        from agentnova.core.models import StepResult, ToolCall
        from agentnova.core.types import StepResultType
        return StepResult(
            type=StepResultType.TOOL_CALL,
            tool_call=ToolCall(name=name, arguments=args),
            tool_result=result,
        )

    def _capture_stdout(self, fn):
        """Run fn() and return everything it printed to stdout."""
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            fn()
        finally:
            sys.stdout = old
        return buf.getvalue()

    def test_prints_tool_calls_when_present(self):
        """A run with tool calls should print each call + truncated result."""
        from agentnova.cli import _print_agent_steps
        run = self._make_run([
            self._make_tool_step("shell", {"command": "echo hi"}, "hi\n"),
            self._make_tool_step("read_file", {"file_path": "/tmp/x"}, "file contents"),
        ])
        out = self._capture_stdout(lambda: _print_agent_steps(run, debug=False))
        self.assertIn("shell", out)
        self.assertIn("echo hi", out)
        self.assertIn("hi", out)
        self.assertIn("read_file", out)
        self.assertIn("file contents", out)

    def test_prints_nothing_in_debug_mode(self):
        """In debug mode the agent already prints verbose output — skip."""
        from agentnova.cli import _print_agent_steps
        run = self._make_run([
            self._make_tool_step("shell", {"command": "echo hi"}, "hi"),
        ])
        out = self._capture_stdout(lambda: _print_agent_steps(run, debug=True))
        self.assertEqual(out, "")

    def test_prints_nothing_when_no_tool_calls(self):
        """A run with no tool calls (just text answer) prints nothing."""
        from agentnova.cli import _print_agent_steps
        from agentnova.core.models import StepResult
        from agentnova.core.types import StepResultType
        run = self._make_run([
            StepResult(type=StepResultType.FINAL_ANSWER, content="answer"),
        ])
        out = self._capture_stdout(lambda: _print_agent_steps(run, debug=False))
        self.assertEqual(out, "")

    def test_truncates_long_tool_results(self):
        """Tool results longer than 200 chars are truncated for display."""
        from agentnova.cli import _print_agent_steps
        long_result = "x" * 500
        run = self._make_run([
            self._make_tool_step("shell", {"command": "cat big"}, long_result),
        ])
        out = self._capture_stdout(lambda: _print_agent_steps(run, debug=False))
        # Should be truncated to ~200 chars + ellipsis
        # (the full 500-char result should NOT be in the output)
        self.assertIn("...", out)
        self.assertNotIn("x" * 500, out)

    def test_truncates_long_args(self):
        """Tool args JSON longer than 120 chars are truncated."""
        from agentnova.cli import _print_agent_steps
        long_arg = "y" * 200
        run = self._make_run([
            self._make_tool_step("shell", {"command": long_arg}, "ok"),
        ])
        out = self._capture_stdout(lambda: _print_agent_steps(run, debug=False))
        self.assertIn("...", out)
        # Full arg should NOT be in output
        self.assertNotIn("y" * 200, out)


if __name__ == "__main__":
    unittest.main()
