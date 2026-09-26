"""
⚛️ AgentKthx — Agent Tests

Written by VTSTech — https://www.vts-tech.org
"""

import pytest
from agentkthx.core.types import StepResultType, ToolSupportLevel, BackendType
from agentkthx.core.models import Tool, ToolParam, StepResult, AgentRun
from agentkthx.core.memory import Memory, MemoryConfig
from agentkthx.core.tool_parse import ToolParser
from agentkthx.core.helpers import fuzzy_match, normalize_args, sanitize_command, validate_path, is_safe_url
from agentkthx.tools import ToolRegistry, make_builtin_registry


class TestTypes:
    """Test core type enums."""

    def test_step_result_type_values(self):
        assert StepResultType.TOOL_CALL.value > 0
        assert StepResultType.FINAL_ANSWER.value > 0
        assert StepResultType.ERROR.value > 0

    def test_tool_support_level(self):
        assert ToolSupportLevel.NATIVE.value == "native"
        assert ToolSupportLevel.REACT.value == "react"
        assert ToolSupportLevel.NONE.value == "none"

    def test_tool_support_detect(self):
        # R03.5: detect() returns UNTESTED without force_test=True
        # Tool support must be explicitly tested via backend.test_tool_support()
        assert ToolSupportLevel.detect("qwen2.5:7b") == ToolSupportLevel.UNTESTED
        assert ToolSupportLevel.detect("llama3.1:8b") == ToolSupportLevel.UNTESTED
        assert ToolSupportLevel.detect("unknown-model") == ToolSupportLevel.UNTESTED
        
        # Test the enum values are correct
        assert ToolSupportLevel.NATIVE.value == "native"
        assert ToolSupportLevel.REACT.value == "react"
        assert ToolSupportLevel.UNTESTED.value == "untested"

    def test_backend_type(self):
        assert BackendType.OLLAMA.value == "ollama"
        assert BackendType.LLAMA_SERVER.value == "llama_server"
        assert BackendType.BITNET.value == "bitnet"


class TestModels:
    """Test data models."""

    def test_tool_param(self):
        param = ToolParam(name="test", type="string", description="Test param")
        schema = param.to_json_schema()
        assert schema["type"] == "string"
        assert schema["description"] == "Test param"

    def test_tool(self):
        tool = Tool(
            name="test_tool",
            description="A test tool",
            params=[ToolParam(name="input", type="string")],
        )
        schema = tool.to_json_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"

    def test_step_result(self):
        result = StepResult(
            type=StepResultType.FINAL_ANSWER,
            content="Test answer",
            tokens_used=100,
        )
        assert result.type == StepResultType.FINAL_ANSWER
        assert result.content == "Test answer"

    def test_agent_run(self):
        run = AgentRun(
            final_answer="Test",
            steps=[StepResult(type=StepResultType.FINAL_ANSWER, content="Test")],
            total_tokens=100,
            total_ms=50.0,
        )
        assert run.final_answer == "Test"
        assert run.iterations == 1
        assert run.success is True


class TestMemory:
    """Test memory management."""

    def test_memory_add(self):
        memory = Memory()
        memory.add("user", "Hello")
        assert len(memory) == 1

    def test_memory_system_prompt(self):
        memory = Memory()
        memory.add("system", "You are helpful")
        memory.add("user", "Hello")

        messages = memory.get_messages()
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful"

    def test_memory_clear(self):
        memory = Memory()
        memory.add("user", "Hello")
        memory.clear()
        assert len(memory) == 0

    def test_memory_pruning(self):
        config = MemoryConfig(max_messages=10, keep_recent=3)
        memory = Memory(config)

        memory.add("system", "System")
        for i in range(15):
            memory.add("user", f"Message {i}")

        # Should have pruned some messages
        assert len(memory.get_messages()) <= 15


class TestToolParser:
    """Test tool call parsing."""

    def test_parse_react_format(self):
        parser = ToolParser(["calculator", "shell"])
        text = """Action: calculator
Action Input: {"expression": "2 + 2"}"""

        calls = parser.parse(text)
        assert len(calls) == 1
        assert calls[0].name == "calculator"
        assert calls[0].arguments == {"expression": "2 + 2"}

    def test_parse_react_single_quote_python_dict(self):
        """SEC-02 (R07.05): single-quoted Python dict literals (common from
        small models like qwen2.5:0.5b and BitNet) must still parse to
        valid string-typed args. Previously this fell through to
        ``ast.literal_eval`` which also accepted ``bytes``, ``complex``,
        ``frozenset``, etc. — a type-confusion vector that could bypass
        ``validate_path``'s string-prefix checks.
        """
        parser = ToolParser(["calculator"])
        text = "Action: calculator\nAction Input: {'expression': '15 + 27'}"

        calls = parser.parse(text)
        assert len(calls) == 1
        assert calls[0].name == "calculator"
        assert calls[0].arguments == {"expression": "15 + 27"}
        # Critical: the value MUST be a str, not bytes
        assert isinstance(calls[0].arguments["expression"], str)

    def test_parse_react_python_bool_and_none(self):
        """SEC-02 (R07.05): Python True/False/None literals in single-quoted
        dict args must convert to JSON true/false/null. ``ast.literal_eval``
        would have accepted these as native Python types, but downstream
        tool handlers expect JSON-native types only.
        """
        parser = ToolParser(["test_tool"])
        text = "Action: test_tool\nAction Input: {'flag': True, 'other': None, 'val': False}"

        calls = parser.parse(text)
        assert len(calls) == 1
        assert calls[0].arguments == {"flag": True, "other": None, "val": False}

    def test_parse_react_bytes_literal_rejected(self):
        """SEC-02 (R07.05): ``ast.literal_eval`` accepted ``b'...'`` bytes
        literals, which could bypass string-prefix security checks in tool
        handlers (``os`` accepts bytes for paths). The new regex-based
        converter must NOT produce bytes-typed values.
        """
        parser = ToolParser(["read_file"])
        # Malicious payload that would have bypassed validate_path via
        # bytes-typed arg under the old ast.literal_eval fallback.
        text = "Action: read_file\nAction Input: {b'file_path': b'/etc/passwd'}"

        calls = parser.parse(text)
        # The parser must not produce a bytes-typed file_path value.
        # It either rejects the call entirely (no calls) or coerces to str.
        if calls:
            for call in calls:
                for key, val in call.arguments.items():
                    # No bytes values should reach the tool handler
                    assert not isinstance(val, bytes), (
                        f"SEC-02 violation: argument {key!r}={val!r} is bytes-typed, "
                        f"which can bypass validate_path string-prefix checks"
                    )

    def test_parse_json_format(self):
        parser = ToolParser(["calculator"])
        text = '{"name": "calculator", "arguments": {"expression": "2 + 2"}}'

        calls = parser.parse(text)
        assert len(calls) == 1
        assert calls[0].name == "calculator"

    def test_fuzzy_match(self):
        parser = ToolParser(["calculator", "shell"])

        # Exact match
        calls = parser.parse("Action: calculator\nAction Input: {}")
        assert calls[0].name == "calculator"

    def test_is_final_answer(self):
        parser = ToolParser([])

        # R03.5: Conservative matching - only explicit ReAct format markers
        assert parser.is_final_answer("Final Answer: 42")
        assert parser.is_final_answer("Final Answer: The result is 42")
        
        # These should NOT match (conservative to prevent small models bypassing tools)
        assert not parser.is_final_answer("The answer is 42")
        assert not parser.is_final_answer("Answer: 42")
        assert not parser.is_final_answer("Action: calculator")

    def test_extract_final_answer(self):
        parser = ToolParser([])

        answer = parser.extract_final_answer("Final Answer: 42")
        assert answer == "42"


class TestHelpers:
    """Test helper functions."""

    def test_fuzzy_match(self):
        candidates = ["calculator", "shell", "read_file"]

        # Exact match
        assert fuzzy_match("calculator", candidates) == "calculator"

        # Fuzzy match
        assert fuzzy_match("calc", candidates) == "calculator"
        assert fuzzy_match("readfile", candidates) == "read_file"

    def test_normalize_args(self):
        expected = ["expression", "timeout"]
        args = {"expr": "2 + 2", "time_limit": 30}

        normalized = normalize_args(args, expected)
        assert "expression" in normalized or "expr" in normalized

    def test_sanitize_command_safe(self):
        is_safe, error, cmd = sanitize_command("echo hello")
        assert is_safe is True

    def test_sanitize_command_blocked(self):
        is_safe, error, cmd = sanitize_command("rm -rf /")
        assert is_safe is False
        assert "Blocked" in error

    def test_validate_path_safe(self):
        is_valid, error = validate_path("./output/test.txt")
        assert is_valid is True

    def test_validate_path_unsafe(self):
        is_valid, error = validate_path("/etc/passwd")
        assert is_valid is False

    def test_is_safe_url(self):
        is_safe, _ = is_safe_url("https://example.com")
        assert is_safe is True

    def test_is_safe_url_ssrf(self):
        is_safe, _ = is_safe_url("http://localhost/admin")
        assert is_safe is False


class TestToolRegistry:
    """Test tool registry."""

    def test_register_function(self):
        registry = ToolRegistry()

        @registry.register(description="Test tool")
        def test_func(input: str) -> str:
            return input

        assert "test_func" in registry.names()

    def test_register_tool(self):
        registry = ToolRegistry()
        tool = Tool(name="test", description="Test", handler=lambda x: x)
        registry.register_tool(tool)

        assert registry.get("test") == tool

    def test_subset(self):
        registry = make_builtin_registry()
        subset = registry.subset(["calculator", "shell"])

        assert len(subset) == 2
        assert "calculator" in subset.names()

    def test_fuzzy_get(self):
        registry = make_builtin_registry()

        tool = registry.get_fuzzy("calc")
        assert tool is not None
        assert tool.name == "calculator"


class TestBuiltins:
    """Test built-in tools."""

    def test_calculator(self):
        registry = make_builtin_registry()
        tool = registry.get("calculator")

        result = tool.execute(expression="2 + 2")
        assert result == "4"

    def test_calculator_complex(self):
        registry = make_builtin_registry()
        tool = registry.get("calculator")

        result = tool.execute(expression="sqrt(16)")
        # Result can be "4" or "4.0" depending on Python version/formatting
        assert result in ("4", "4.0")

    def test_count_words(self):
        registry = make_builtin_registry()
        tool = registry.get("count_words")

        result = tool.execute(text="hello world")
        assert result == "2"

    def test_get_time(self):
        registry = make_builtin_registry()
        tool = registry.get("get_time")

        result = tool.execute()
        assert len(result) > 0  # Returns timestamp string


if __name__ == "__main__":
    pytest.main([__file__, "-v"])