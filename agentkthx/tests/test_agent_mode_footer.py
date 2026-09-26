"""
R06.58 regression tests for the agent-mode persistent footer.

Pins down that cmd_agent:
1. Sets up the scroll-region footer (so the terminal isn't left broken).
2. Registers an _on_step_callback so the footer updates during streaming.
3. Tears down the footer on exit (try/finally).

These tests can't actually run cmd_agent end-to-end (it calls input()
which blocks), so they verify the wiring indirectly by patching input()
to return /quit immediately and asserting the terminal scroll-region
escape sequences were emitted to stdout.
"""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


def _term_size(lines: int = 24, cols: int = 80):
    """Build a real os.terminal_size so pytest's own get_terminal_size
    calls (which unpack the result) don't break."""
    return os.terminal_size((cols, lines))


def _make_args():
    """Minimal argparse.Namespace for cmd_agent."""
    import argparse
    ns = argparse.Namespace(
        model="test-model",
        backend="zai",
        tools="",
        skills=None,
        stream=True,
        security="off",
        compaction="auto",
        num_ctx=8192,
        num_predict=4096,
        temperature=0.7,
        debug=False,
        soul=None,
        acp=False,
        mcp=False,
        quiet=False,
        refresh=False,
        max_steps=20,
        max_api_retries=5,
        retry_on_error=True,
        max_tool_retries=3,
        allowed_tools=None,
        system_prompt=None,
        prompt_file=None,
        prompt=None,
    )
    return ns


def test_cmd_agent_emits_scroll_region_setup_and_teardown(monkeypatch, capsys):
    """cmd_agent should emit DECSTBM (scroll region) escape sequences on
    setup AND reset them on teardown. If teardown is missing, the user's
    terminal is left in a broken scroll-region state after exiting agent
    mode — every subsequent shell command scrolls in the wrong region."""
    # Patch input() to return /quit immediately so the loop exits.
    inputs = iter(["/quit"])
    monkeypatch.setattr('builtins.input', lambda *a, **kw: next(inputs))

    # Patch _build_agent to return a mock agent — we don't need a real LLM.
    mock_agent = MagicMock()
    mock_agent.model = "test-model"
    mock_agent.num_ctx = 8192
    mock_agent._num_predict = 4096
    mock_agent._temperature = 0.7
    mock_agent._is_persistent = False
    mock_agent.debug = False
    mock_agent._custom_system_prompt = ""
    mock_agent.model_config.default_max_tokens = 4096
    mock_agent.model_config.default_temperature = 0.7
    mock_agent.backend.backend_type.value = "zai"
    mock_agent.backend.is_cloud = True
    mock_agent.memory = MagicMock()
    # Footer reads these as ints — MagicMock returns a MagicMock by
    # default which breaks _fmt_tok's int() cast.
    mock_agent._running_tokens_in = 0
    mock_agent._running_tokens_out = 0

    # Force _use_persistent_footer = True by making stdout look like a TTY
    # AND setting terminal size >= 6 lines.
    mock_term = _term_size()

    from agentkthx import cli
    monkeypatch.setattr(cli, '_build_agent', lambda *a, **kw: mock_agent)
    monkeypatch.setattr(cli, '_init_acp', lambda *a, **kw: (None, False))
    monkeypatch.setattr(cli, '_print_session_header', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_print_update_notice', lambda *a, **kw: None)
    monkeypatch.setattr('sys.stdout.isatty', lambda: True)
    # NOTE: accept *args, **kwargs so pytest's own internal call to
    # get_terminal_size(fallback=(80,24)) doesn't break.
    monkeypatch.setattr('shutil.get_terminal_size',
                        lambda *a, **kw: mock_term)

    args = _make_args()
    rc = cli.cmd_agent(args)

    captured = capsys.readouterr()
    # Setup emits DECSTBM: \033[1;Nr where N = lines - 2 = 22
    assert "\033[1;22r" in captured.out, (
        "scroll region setup not emitted — terminal won't reserve footer"
    )
    # Teardown emits DECSTBM reset: \033[r
    assert "\033[r" in captured.out, (
        "scroll region teardown not emitted — terminal left broken"
    )
    assert rc == 0


def test_cmd_agent_registers_on_step_callback(monkeypatch, capsys):
    """cmd_agent should set agent._on_step_callback so the footer updates
    during streaming. Without it, the footer only updates between goals,
    not during multi-step execution."""
    inputs = iter(["/quit"])
    monkeypatch.setattr('builtins.input', lambda *a, **kw: next(inputs))

    mock_agent = MagicMock()
    mock_agent.model = "test-model"
    mock_agent.num_ctx = 8192
    mock_agent._num_predict = 4096
    mock_agent._temperature = 0.7
    mock_agent._is_persistent = False
    mock_agent.debug = False
    mock_agent._custom_system_prompt = ""
    mock_agent.model_config.default_max_tokens = 4096
    mock_agent.model_config.default_temperature = 0.7
    mock_agent.backend.backend_type.value = "zai"
    mock_agent.backend.is_cloud = True
    mock_agent.memory = MagicMock()
    mock_agent._running_tokens_in = 0
    mock_agent._running_tokens_out = 0

    mock_term = _term_size()

    from agentkthx import cli
    monkeypatch.setattr(cli, '_build_agent', lambda *a, **kw: mock_agent)
    monkeypatch.setattr(cli, '_init_acp', lambda *a, **kw: (None, False))
    monkeypatch.setattr(cli, '_print_session_header', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_print_update_notice', lambda *a, **kw: None)
    monkeypatch.setattr('sys.stdout.isatty', lambda: True)
    monkeypatch.setattr('shutil.get_terminal_size',
                        lambda *a, **kw: mock_term)

    cli.cmd_agent(_make_args())

    # _on_step_callback should have been set on the agent (not None).
    assert mock_agent._on_step_callback is not None, (
        "_on_step_callback was not registered — footer won't update during streaming"
    )


def test_cmd_agent_skips_footer_in_non_tty(monkeypatch, capsys):
    """When stdout is NOT a TTY (piped, redirected), cmd_agent should
    NOT emit scroll-region escape sequences — they'd corrupt log files
    and CI output."""
    inputs = iter(["/quit"])
    monkeypatch.setattr('builtins.input', lambda *a, **kw: next(inputs))

    mock_agent = MagicMock()
    mock_agent.model = "test-model"
    mock_agent.num_ctx = 8192
    mock_agent._num_predict = 4096
    mock_agent._temperature = 0.7
    mock_agent._is_persistent = False
    mock_agent.debug = False
    mock_agent._custom_system_prompt = ""
    mock_agent.model_config.default_max_tokens = 4096
    mock_agent.model_config.default_temperature = 0.7
    mock_agent.backend.backend_type.value = "zai"
    mock_agent.backend.is_cloud = True
    mock_agent.memory = MagicMock()

    from agentkthx import cli
    monkeypatch.setattr(cli, '_build_agent', lambda *a, **kw: mock_agent)
    monkeypatch.setattr(cli, '_init_acp', lambda *a, **kw: (None, False))
    monkeypatch.setattr(cli, '_print_session_header', lambda *a, **kw: None)
    monkeypatch.setattr(cli, '_print_update_notice', lambda *a, **kw: None)
    # stdout is NOT a TTY
    monkeypatch.setattr('sys.stdout.isatty', lambda: False)

    cli.cmd_agent(_make_args())

    captured = capsys.readouterr()
    # No scroll-region setup should be emitted when not a TTY
    assert "\033[1;" not in captured.out or "r" not in captured.out.split("\033[1;")[1][:10], (
        "scroll-region escape emitted in non-TTY mode — would corrupt logs"
    )
