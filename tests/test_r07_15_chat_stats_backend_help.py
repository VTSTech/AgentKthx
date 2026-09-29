"""R07.15 amendment regression tests: per-response stats in chat mode +
the dynamic ``--backend`` help text (both user-reported in the same VM
smoke pass as the four-finding batch).

1. ``chat -h`` / ``agent -h`` / ``run -h`` showed a HARDCODED backend
   list that had drifted: plugin backends mistral, orcarouter and
   pollinations (plus every alias) were accepted at runtime but absent
   from the help text. Fix: the help string is built at parser-build
   time from ``get_backend_choices()`` (main() loads all plugins
   before ``create_parser()``, so the merged native+plugin registry is
   visible) with a core-only fallback for standalone example-script
   use.

2. Chat mode printed nothing about how long a response took or how
   many tool calls it made, while agent mode's verbose footer showed
   "⏱️ N steps, M tool calls, Xms" (+ "🔧 Tools used:"). Fix: the
   formatting is extracted to ``agent_mode._format_response_stats()``
   (library layer, next to ``_step_tool_stats``) and both agent mode
   and the chat turn loop render through it — one shared format, no
   drifting copies.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.agent_mode import _format_response_stats, _step_tool_stats
from agentkthx.core.models import AgentRun, StepResult, ToolCall
from agentkthx.core.types import StepResultType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tool(name: str, args: dict | None = None) -> StepResult:
    return StepResult(
        type=StepResultType.TOOL_CALL,
        content="ok",
        tool_call=ToolCall(name=name, arguments=args or {}),
        tool_result="result",
    )


def _subparser_help(command: str) -> str:
    """Build the real CLI parser (with plugins loaded, mirroring main())
    and return the subcommand's rendered help text."""
    from agentkthx.plugins import get_plugin_manager

    pm = get_plugin_manager()
    pm.load_all()  # main() does this before create_parser() — same order

    from agentkthx.cli.parser import create_parser

    parser = create_parser()
    sub_action = parser._subparsers_action
    assert command in sub_action.choices, f"missing '{command}' subcommand"
    return sub_action.choices[command].format_help()


# ---------------------------------------------------------------------------
# 1. Dynamic --backend help text
# ---------------------------------------------------------------------------

class TestBackendHelp:
    @pytest.mark.parametrize("command", ["chat", "agent", "run"])
    def test_help_lists_plugin_backends(self, command):
        """The previously-missing plugin backends must appear in every
        subcommand's --backend help (they always worked at runtime)."""
        help_text = _subparser_help(command)
        for backend in ("mistral", "orcarouter", "pollinations"):
            assert backend in help_text, (
                f"'{backend}' missing from '{command} --help' --backend text"
            )

    @pytest.mark.parametrize("command", ["chat", "agent", "run"])
    def test_help_lists_core_backends(self, command):
        help_text = _subparser_help(command)
        for backend in ("ollama", "llama-server", "bitnet", "zai",
                        "openrouter", "gemini", "openai", "huggingface"):
            assert backend in help_text

    def test_choices_for_help_dynamic_contains_merged_registry(self):
        from agentkthx.shared_args import _backend_choices_for_help

        names = _backend_choices_for_help()
        # Native core names…
        assert "ollama" in names
        assert "llama-server" in names
        # …and plugin-registered names (present because the CLI/main()
        # contract loads plugins before the parser is built).
        assert "orcarouter" in names
        assert "mistral" in names
        assert "pollinations" in names

    def test_choices_for_help_fallback_on_empty_registry(self, monkeypatch):
        """If the registry reports nothing (standalone example-script
        use with no plugins), the fallback core list keeps the help
        text informative."""
        import agentkthx.backends as backends_mod
        from agentkthx import shared_args

        monkeypatch.setattr(backends_mod, "get_backend_choices", lambda: [])
        names = shared_args._backend_choices_for_help()
        assert "ollama" in names
        assert "orcarouter" in names  # fallback names the shipped plugins
        assert names == [
            "ollama", "bitnet", "llama-server", "zai", "openrouter",
            "huggingface", "gemini", "openai", "mistral", "orcarouter",
            "pollinations",
        ]

    def test_choices_for_help_survives_registry_error(self, monkeypatch):
        """A raising registry must never break --help rendering."""
        import agentkthx.backends as backends_mod
        from agentkthx import shared_args

        def _boom():
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr(backends_mod, "get_backend_choices", _boom)
        names = shared_args._backend_choices_for_help()
        assert "ollama" in names
        assert "pollinations" in names

    def test_no_hardcoded_list_left_in_shared_args(self):
        """Pin the drift class: shared_args.py must not embed a literal
        backend list in the help string anymore."""
        import agentkthx.shared_args as sa

        src = inspect.getsource(sa)
        stale = (
            "(ollama, bitnet, llama-server, zai, openrouter, huggingface, "
            "gemini, openai)"
        )
        assert stale not in src


# ---------------------------------------------------------------------------
# 2. Per-response stats formatting (shared by agent mode + chat)
# ---------------------------------------------------------------------------

class TestFormatResponseStats:
    def test_plain_chat_reply_single_step_zero_tools(self):
        """The common chat shape: one FINAL_ANSWER step, no tools."""
        run = AgentRun(
            final_answer="hi",
            steps=[StepResult(type=StepResultType.FINAL_ANSWER, content="hi")],
            total_ms=1234.0,
        )
        lines = _format_response_stats(run, indent="  ")
        assert lines == ["  ⏱️ 1 step, 0 tool calls, 1234ms"]

    def test_ms_uses_agent_mode_rounding(self):
        """:.0f — same rendering agent mode always used."""
        run = SimpleNamespace(steps=[], total_ms=26927.7)
        line = _format_response_stats(run, indent="")[0]
        assert line == "⏱️ 0 steps, 0 tool calls, 26928ms"

    def test_multi_tool_turn_lists_names_deduped(self):
        run = AgentRun(
            final_answer="done",
            steps=[
                _tool("shell", {"command": "uname"}),
                _tool("http_get", {"url": "https://x"}),
                _tool("shell", {"command": "free -h"}),
                StepResult(type=StepResultType.FINAL_ANSWER, content="done"),
            ],
            total_ms=26927.0,
        )
        lines = _format_response_stats(run, indent="  ")
        assert lines[0] == "  ⏱️ 4 steps, 3 tool calls, 26927ms"
        # 3 calls, 2 unique names, first-seen order — same as _step_tool_stats
        assert lines[1] == "  🔧 Tools used: shell, http_get"
        assert len(lines) == 2

    def test_singular_tool_call(self):
        run = AgentRun(
            final_answer="x",
            steps=[
                _tool("calculator"),
                StepResult(type=StepResultType.FINAL_ANSWER, content="x"),
            ],
            total_ms=500.0,
        )
        line = _format_response_stats(run, indent="  ")[0]
        assert line == "  ⏱️ 2 steps, 1 tool call, 500ms"

    def test_agent_mode_default_indent(self):
        """Agent mode renders through the same helper with its own
        4-space indent (nested under the '⟳ Executing:' line)."""
        run = AgentRun(
            final_answer="x",
            steps=[
                _tool("shell"),
                StepResult(type=StepResultType.FINAL_ANSWER, content="x"),
            ],
            total_ms=250.0,
        )
        lines = _format_response_stats(run)  # default indent="    "
        assert lines[0] == "    ⏱️ 2 steps, 1 tool call, 250ms"
        assert lines[1] == "    🔧 Tools used: shell"

    def test_duck_typed_run_without_total_ms(self):
        """Only .steps is required; a missing total_ms renders as 0ms
        instead of raising (getattr fallback)."""
        run = SimpleNamespace(
            steps=[StepResult(type=StepResultType.FINAL_ANSWER, content="a")],
        )
        line = _format_response_stats(run, indent="")[0]
        assert line == "⏱️ 1 step, 0 tool calls, 0ms"

    def test_empty_run(self):
        """A run with no steps at all (hard failure exit) still renders."""
        run = SimpleNamespace(steps=[], total_ms=99.5)
        lines = _format_response_stats(run, indent="  ")
        assert lines == ["  ⏱️ 0 steps, 0 tool calls, 100ms"]

    def test_stats_consistent_with_step_tool_stats(self):
        """_format_response_stats must agree with _step_tool_stats for
        the same run (single source of truth)."""
        run = AgentRun(
            final_answer="done",
            steps=[_tool("web_search"), _tool("web_search"),
                   StepResult(type=StepResultType.FINAL_ANSWER, content="d")],
            total_ms=10.0,
        )
        count, names = _step_tool_stats(run)
        lines = _format_response_stats(run, indent="")
        assert f"{count} tool calls" in lines[0]
        assert ", ".join(names) in lines[1]


# ---------------------------------------------------------------------------
# 3. Wiring pins — both call sites render through the shared helper
# ---------------------------------------------------------------------------

class TestWiringPins:
    def test_agent_mode_display_uses_shared_helper(self):
        """The old inline f-string in agent_mode's verbose footer is
        replaced by _format_response_stats — pin against the display
        logic drifting back to a private copy."""
        import agentkthx.agent_mode as am

        src = inspect.getsource(am)
        assert "_format_response_stats(run)" in src
        # The old inline format (pluralizing nothing, no shared logic):
        assert '{len(run.steps)} steps, {tool_call_count} tool calls' not in src

    def test_chat_renders_stats_per_response(self):
        """chat.py must import the shared helper and render its lines
        before the footer refresh."""
        import agentkthx.cli.commands.chat as chat_mod

        src = inspect.getsource(chat_mod)
        assert "from ...agent_mode import _format_response_stats" in src
        stats_idx = src.index('_format_response_stats(result, indent="  ")')
        # The LAST _update_footer() occurrence in the file is the
        # turn-loop refresh that follows the stats render (earlier
        # occurrences are the nested def and scroll-region setup).
        assert stats_idx < src.rindex("_update_footer()")
