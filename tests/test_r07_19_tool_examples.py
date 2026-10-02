"""
AgentKthx — Tool Reference real example arguments (R07.19 follow-up commit #2)

``_build_tool_section()`` used to render every string parameter as
``"param": "..."``. The user reported the shell row
(``{"command": "...", "timeout": 10}``) reads as if the prompt itself
had been truncated — and a placeholder teaches a small local model
nothing it can copy. Small local models copy the Arguments column
verbatim (the same mechanism that made the old ``0`` timeout example
dangerous), so the column now renders REAL values:

- curated per-param-name examples (``_PARAM_STRING_EXAMPLES`` — the
  shell ``command`` shows ``echo Hello, World!``),
- the parameter's own non-empty string default when the name is
  unmapped (todo ``priority`` → ``"medium"``),
- the first enum value for enum params (always valid),
- ``true`` for booleans, ``{}`` for objects — a bare ``...`` only
  survives for unmapped exotic types.

The "When to use" cell cap also moved 40 → 60 with word-boundary
cutting, so ``Execute shell commands (with security restrictions)``
fits in full instead of ``(with security...``.
"""

import pytest

from agentkthx.core.models import Tool, ToolParam
from agentkthx.soul.loader import _PARAM_STRING_EXAMPLES, _build_tool_section
from agentkthx.tools import make_builtin_registry


def _tool(name: str, desc: str, *params: ToolParam) -> Tool:
    return Tool(name=name, description=desc, params=list(params))


def _shell_tool() -> Tool:
    return _tool(
        "shell",
        "Execute shell commands (with security restrictions)",
        ToolParam(name="command", type="string", description="Shell command to execute"),
        ToolParam(
            name="timeout",
            type="integer",
            description="Timeout in seconds",
            required=False,
            default=30,
        ),
    )


class TestShellRowRealCommand:
    """The user-reported row: arguments must be a real, copyable call."""

    def test_shell_row_exact(self):
        """Pin the exact user-visible shell row — real command, no ellipsis."""
        section = _build_tool_section([_shell_tool()])
        expected = (
            "| `shell` | Execute shell commands (with security restrictions) "
            '| `{"command": "echo Hello, World!", "timeout": 10}` |'
        )
        assert expected in section

    def test_shell_args_contain_no_placeholder(self):
        section = _build_tool_section([_shell_tool()])
        assert '"command": "..."' not in section
        assert "..." not in section

    def test_shell_description_not_truncated(self):
        """The old 40-char cap cut the description mid-clause."""
        section = _build_tool_section([_shell_tool()])
        assert "Execute shell commands (with security restrictions)" in section
        assert "with security..." not in section


class TestDefaultRegistryFullyReal:
    """Every builtin tool row must render real values — no placeholders."""

    def test_default_registry_has_no_string_placeholder(self):
        tools = make_builtin_registry().all()
        section = _build_tool_section(tools)
        assert '": "..."' not in section

    def test_default_registry_has_no_bare_ellipsis(self):
        tools = make_builtin_registry().all()
        section = _build_tool_section(tools)
        assert ": ..." not in section

    def test_every_mapped_param_renders_its_example(self):
        tools = make_builtin_registry().all()
        section = _build_tool_section(tools)
        for value in _PARAM_STRING_EXAMPLES.values():
            assert f'"{value}"' in section, f"missing example value: {value}"

    def test_priority_uses_param_default(self):
        """todo.priority has default='medium' and no curated entry — the
        param's own default must surface as the example."""
        tools = make_builtin_registry().all()
        section = _build_tool_section(tools)
        assert '"priority": "medium"' in section

    def test_numeric_params_still_render_10(self):
        """Pin the R07.x rule: numeric examples are 10, never 0 (small
        models copy the value verbatim and 0 caused instant timeouts)."""
        tools = make_builtin_registry().all()
        section = _build_tool_section(tools)
        assert '"timeout": 10' in section
        assert '"max_results": 10' in section


class TestParamTypeRules:
    """Synthetic tools pin each branch of the example-value resolution."""

    def test_boolean_renders_true(self):
        tool = _tool(
            "edit_file",
            "Edit a file by finding and replacing a specific text segment",
            ToolParam(name="replace_all", type="boolean", required=False),
        )
        section = _build_tool_section([tool])
        assert '"replace_all": true' in section

    def test_object_renders_empty_dict(self):
        tool = _tool(
            "http_get",
            "Make an HTTP GET request to a URL",
            ToolParam(name="headers", type="object", required=False),
        )
        section = _build_tool_section([tool])
        assert '"headers": {}' in section

    def test_string_default_used_when_name_unmapped(self):
        tool = _tool(
            "synthetic",
            "A synthetic tool for the default fallback.",
            ToolParam(name="flavor", type="string", default="vanilla"),
        )
        section = _build_tool_section([tool])
        assert '"flavor": "vanilla"' in section

    def test_enum_uses_first_value(self):
        tool = _tool(
            "synthetic",
            "A synthetic tool with an enum param.",
            ToolParam(name="mode", type="string", enum=["fast", "slow"]),
        )
        section = _build_tool_section([tool])
        assert '"mode": "fast"' in section

    def test_unknown_string_param_falls_back_to_ellipsis(self):
        """The documented fallback: unmapped name + no default keeps "...",
        so unknown custom tools still render a syntactically valid example."""
        tool = _tool(
            "synthetic",
            "A synthetic tool with an exotic param.",
            ToolParam(name="mystery", type="string"),
        )
        section = _build_tool_section([tool])
        assert '"mystery": "..."' in section

    def test_curated_example_wins_over_default(self):
        """A mapped name must use the curated example even when the param
        carries a different default (command's default is unset/None in the
        builtin shell tool; synthesize a conflicting one to pin precedence)."""
        tool = _tool(
            "synthetic",
            "A synthetic tool with a mapped param name.",
            ToolParam(name="command", type="string", default="ls"),
        )
        section = _build_tool_section([tool])
        assert '"command": "echo Hello, World!"' in section
        assert '"command": "ls"' not in section


class TestDescriptionCap:
    """When-to-use cell: 60-char cap with word-boundary cutting."""

    def test_long_description_cut_at_word_boundary(self):
        desc = (
            "This tool performs an extraordinarily long winded operation "
            "that far exceeds sixty characters in its first sentence"
        )
        tool = _tool("synthetic", desc + ". Second sentence.")
        section = _build_tool_section([tool])
        row = next(line for line in section.splitlines() if line.startswith("| `synthetic` |"))
        cell = row.split(" | ")[1]
        assert cell.endswith("...")
        stem = cell[:-3]
        # The cut must end on a whole word — no partial word before "...".
        assert not desc.startswith(stem) or desc[len(stem)] == " " or stem == ""

    def test_description_at_60_chars_untouched(self):
        desc = "x" * 60
        tool = _tool("synthetic", desc)
        section = _build_tool_section([tool])
        assert f"| `synthetic` | {desc} |" in section

    def test_description_over_60_but_under_window_stays_whole_words(self):
        desc = "Write content to a file, creating parent directories if needed"
        tool = _tool("write_file", desc)
        section = _build_tool_section([tool])
        row = next(line for line in section.splitlines() if line.startswith("| `write_file` |"))
        cell = row.split(" | ")[1]
        assert cell.endswith("...")
        assert cell[:-3] == "Write content to a file, creating parent directories if"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
