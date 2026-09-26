"""Tests for PERF-01: real streaming display via Agent.run(stream=True).

Covers the three components added for streaming support:
  - Agent._generate_stream(): SSE chunk accumulation, tool_call fragment
    merging, return shape parity with _generate()
  - Agent._run_core_streaming(): agentic loop with streaming generation
  - Agent._run_core(stream=True): delegation to _run_core_streaming
"""
import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from agentkthx.agent import Agent
from agentkthx.core.models import AgentRun
from agentkthx.core.types import StepResultType
from agentkthx.core.openresponses import ToolChoiceType


def _make_agent():
    """Construct an Agent bypassing __init__ — we only test methods."""
    a = Agent.__new__(Agent)
    # Minimal attrs needed by _generate_stream / _run_core_streaming
    a.model = "test-model"
    a.backend = MagicMock()
    a.backend.base_url = "http://test"
    a.backend.backend_type = None
    a.memory = MagicMock()
    a.memory.get_messages.return_value = [{"role": "user", "content": "hi"}]
    a.memory.add = MagicMock()
    a.memory.add_tool_call = MagicMock()
    a.memory.add_tool_result = MagicMock()
    a.tools = MagicMock()
    a.tools.all.return_value = []
    a.tools.names.return_value = []
    a.tools.__len__ = lambda self: 0
    a.model_config = MagicMock()
    a.model_config.stop_tokens = []
    a.model_config.default_temperature = 0.7
    a.model_config.default_max_tokens = 128
    a.model_config.default_top_p = 0.9
    a._think = None
    a._reasoning_effort = None
    a._temperature = None
    a._top_p = None
    a._num_predict = None
    a.num_ctx = None
    a.model_family = None
    a._response_format = None
    a.tool_choice = MagicMock()
    # Use the real AUTO enum value so == comparisons against REQUIRED/SPECIFIC
    # return False — a generic MagicMock returns True for any ==, which
    # trips the needs_tool branch and breaks the streaming loop tests.
    a.tool_choice.type = ToolChoiceType.AUTO
    a.tool_choice.to_dict = MagicMock(return_value={})
    a._allowed_tools = None
    a.truncation = "auto"
    # _is_comp_mode is a computed property: hasattr(backend, 'api_mode') and
    # backend.api_mode == ApiMode.OPENAI. Setting api_mode = None satisfies
    # the hasattr check but compares unequal to OPENAI, yielding False.
    a.backend.api_mode = None
    a.debug = False
    a.max_api_retries = 3
    a.max_steps = 10
    a._runtime_kwargs = {}
    a._response_history = {}
    a._error_tracker = MagicMock()
    a._error_tracker.reset = MagicMock()
    a._error_tracker.should_block_repeat = MagicMock(return_value=False)
    a._error_tracker.record_failure = MagicMock()
    a._error_tracker.record_success = MagicMock()
    a._error_tracker.should_terminate = MagicMock(return_value=False)
    a._error_tracker.consecutive_all = 0
    a._parser = MagicMock()
    a._parser.parse.return_value = []
    a._parser.is_final_answer.return_value = False
    a._retry_on_error = False
    a._max_tool_retries = 3
    return a


class TestGenerateStreamBasics(unittest.TestCase):
    """_generate_stream returns the same shape as _generate."""

    def test_returns_dict_with_required_keys(self):
        a = _make_agent()
        # Mock backend with generate_completions_stream yielding text deltas
        def _stream_gen(**kwargs):
            yield {"delta": "Hello", "tool_calls": None, "finish_reason": None}
            yield {"delta": ", world!", "tool_calls": None, "finish_reason": None}
            yield {"delta": "", "tool_calls": None, "finish_reason": "stop"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        # Suppress stdout writes from the streaming path
        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(result["content"], "Hello, world!")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["_finish_reason"], "stop")
        self.assertEqual(result["usage"], {})
        self.assertEqual(result["reasoning_content"], "")

    def test_stream_false_returns_agent_run(self):
        """_run_core(stream=False) must NOT delegate to _run_core_streaming."""
        a = _make_agent()
        # Make _generate return a final-answer response (no tool calls).
        a.backend.generate = MagicMock(return_value={
            "content": "Hello",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        })
        a._parser.is_final_answer.return_value = False
        a._parser.parse.return_value = []
        # The non-streaming path will accept "Hello" as the final answer.
        with patch("sys.stdout", new=io.StringIO()):
            result = a._run_core("hi", stream=False)
        self.assertIsInstance(result, AgentRun)
        self.assertEqual(result.final_answer, "Hello")

    def test_stream_true_returns_agent_run(self):
        """_run_core(stream=True) delegates to _run_core_streaming and returns AgentRun."""
        a = _make_agent()
        def _stream_gen(**kwargs):
            yield {"delta": "Hello", "tool_calls": None, "finish_reason": None}
            yield {"delta": "", "tool_calls": None, "finish_reason": "stop"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)
        with patch("sys.stdout", new=io.StringIO()):
            result = a._run_core("hi", stream=True)
        self.assertIsInstance(result, AgentRun)
        self.assertEqual(result.final_answer, "Hello")


class TestGenerateStreamToolCallAccumulation(unittest.TestCase):
    """_generate_stream must merge tool_call fragments across SSE chunks.

    OpenAI streaming splits a single tool_call into multiple deltas:
    - First chunk: {index: 0, id: "call_1", function: {name: "shell"}}
    - Subsequent: {index: 0, function: {arguments: "{\"cmd\""}}
    - More:        {index: 0, function: {arguments: ":\"ls\"}"}}
    """

    def test_single_tool_call_assembled_from_fragments(self):
        a = _make_agent()
        def _stream_gen(**kwargs):
            # First chunk: id + name, no args yet
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_abc",
                    "function": {"name": "shell", "arguments": ""},
                }],
                "finish_reason": None,
            }
            # Subsequent chunks: arguments string grows
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": "{\"command\":"},
                }],
                "finish_reason": None,
            }
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": " \"ls -la\"}"},
                }],
                "finish_reason": None,
            }
            yield {"delta": "", "tool_calls": None, "finish_reason": "tool_calls"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(result["tool_calls"], [{
            "id": "call_abc",
            "name": "shell",
            "arguments": {"command": "ls -la"},
        }])
        self.assertEqual(result["content"], "")
        self.assertEqual(result["_finish_reason"], "tool_calls")

    def test_multiple_tool_calls_assembled_in_index_order(self):
        a = _make_agent()
        def _stream_gen(**kwargs):
            # First tool call (index 0)
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "read_file", "arguments": ""},
                }],
                "finish_reason": None,
            }
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": "{\"file_path\": \"/tmp/a\"}"},
                }],
                "finish_reason": None,
            }
            # Second tool call (index 1)
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 1,
                    "id": "call_2",
                    "function": {"name": "read_file", "arguments": ""},
                }],
                "finish_reason": None,
            }
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 1,
                    "function": {"arguments": "{\"file_path\": \"/tmp/b\"}"},
                }],
                "finish_reason": None,
            }
            yield {"delta": "", "tool_calls": None, "finish_reason": "tool_calls"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["tool_calls"][0]["id"], "call_1")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"file_path": "/tmp/a"})
        self.assertEqual(result["tool_calls"][1]["id"], "call_2")
        self.assertEqual(result["tool_calls"][1]["arguments"], {"file_path": "/tmp/b"})

    def test_malformed_arguments_json_falls_back_to_raw_wrapper(self):
        """If arguments JSON can't be parsed, surface the raw string."""
        a = _make_agent()
        def _stream_gen(**kwargs):
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_x",
                    "function": {"name": "shell", "arguments": "not-valid-json{"},
                }],
                "finish_reason": None,
            }
            yield {"delta": "", "tool_calls": None, "finish_reason": "tool_calls"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "shell")
        self.assertIn("_raw_arguments", result["tool_calls"][0]["arguments"])
        self.assertEqual(
            result["tool_calls"][0]["arguments"]["_raw_arguments"],
            "not-valid-json{",
        )

    def test_empty_arguments_become_empty_dict(self):
        """Tool calls with no arguments string should yield {} arguments."""
        a = _make_agent()
        def _stream_gen(**kwargs):
            yield {
                "delta": "",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "get_time", "arguments": ""},
                }],
                "finish_reason": None,
            }
            yield {"delta": "", "tool_calls": None, "finish_reason": "tool_calls"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(result["tool_calls"][0]["arguments"], {})


class TestGenerateStreamFallback(unittest.TestCase):
    """_generate_stream falls back to _generate() if backend has no streaming method."""

    def test_fallback_to_non_streaming_when_no_stream_method(self):
        a = _make_agent()
        # Remove any streaming methods from the backend mock
        a.backend = MagicMock(spec=["generate"])
        a.backend.base_url = "http://test"
        a.backend.generate = MagicMock(return_value={
            "content": "Hello",
            "tool_calls": [],
            "usage": {},
            "finish_reason": "stop",
        })
        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()
        self.assertEqual(result["content"], "Hello")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["_finish_reason"], "stop")

    def test_native_generate_stream_path_used_when_no_openai_compat(self):
        """When backend only has generate_stream (native), use it and accumulate text."""
        a = _make_agent()
        # Remove generate_completions_stream, keep generate_stream
        a.backend = MagicMock(spec=["generate_stream"])
        a.backend.base_url = "http://test"
        a.backend.generate_stream = MagicMock(return_value=iter([
            "Hello", " world",
        ]))
        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()
        self.assertEqual(result["content"], "Hello world")
        self.assertEqual(result["tool_calls"], [])


class TestGenerateStreamKeyboardInterrupt(unittest.TestCase):
    """_generate_stream handles KeyboardInterrupt mid-stream cleanly."""

    def test_keyboard_interrupt_returns_cancelled_response(self):
        a = _make_agent()
        def _stream_gen(**kwargs):
            yield {"delta": "Hello", "tool_calls": None, "finish_reason": None}
            raise KeyboardInterrupt
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)

        with patch("sys.stdout", new=io.StringIO()):
            result = a._generate_stream()

        self.assertEqual(result["content"], "Hello")  # partial content preserved
        self.assertTrue(result["_cancelled"])
        self.assertEqual(result["_finish_reason"], "cancelled")


class TestRunCoreStreamingAgentRun(unittest.TestCase):
    """_run_core_streaming returns an AgentRun with the streamed final answer."""

    def test_streamed_response_returned_in_agent_run(self):
        a = _make_agent()
        def _stream_gen(**kwargs):
            yield {"delta": "Hello", "tool_calls": None, "finish_reason": None}
            yield {"delta": " streamed!", "tool_calls": None, "finish_reason": "stop"}
        a.backend.generate_completions_stream = MagicMock(side_effect=_stream_gen)
        with patch("sys.stdout", new=io.StringIO()):
            result = a._run_core_streaming("hi")
        self.assertIsInstance(result, AgentRun)
        self.assertEqual(result.final_answer, "Hello streamed!")
        # Steps should contain at least one FINAL_ANSWER step
        self.assertTrue(any(s.type == StepResultType.FINAL_ANSWER for s in result.steps))


if __name__ == "__main__":
    unittest.main()
