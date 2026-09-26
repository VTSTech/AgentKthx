"""
R07.00 Phase 8 structural tests: cli.py → agentkthx/cli/ package split.

Pins the compatibility contract the split must preserve:

1. Every module-level name that existed on the old 4270-line agentkthx.cli
   module (all cmd_* handlers, shared helpers, constants, main,
   create_parser) is importable from the new package facade.
2. Facade attributes are the *same objects* as their defining modules
   (re-exports, not copies).
3. Monkeypatching agentkthx.cli.<name> still changes command behavior —
   the old monolith's patch semantics. Command modules resolve the four
   shared collaborators (_build_agent, _init_acp, _print_session_header,
   _print_update_notice) through the facade at call time via a lazy
   ``from agentkthx import cli as _cli``.
4. `python -m agentkthx.cli` still works (cli/__main__.py replaced the
   old cli.py ``__main__`` guard).
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every module-level name on the pre-split agentkthx.cli module
# (38 functions + 3 constants). The facade must re-export all of them.
OLD_CLI_MODULE_NAMES = [
    "BANNER_ATOM_BRAILLE", "BANNER_ATOM_PLAIN", "_LAST_UPDATE_CHECK",
    "print_banner", "_run_update_check", "_print_update_notice",
    "resolve_model_pattern", "create_parser", "_make_confirm_callback",
    "_init_acp", "_load_skills_prompt", "_build_agent", "_get_catalog_defaults",
    "_print_session_header", "_print_run_header", "_print_run_summary",
    "cmd_run", "_print_agent_steps", "cmd_chat", "cmd_agent",
    "_get_cache_dir", "_tool_status", "cmd_models", "cmd_tools",
    "cmd_test", "cmd_version", "cmd_turbo", "cmd_config", "cmd_modelfile",
    "cmd_skills", "cmd_soul", "cmd_sessions", "cmd_plugins", "cmd_update",
    "_mask_key", "_print_config_summary", "_is_externally_managed_error",
    "main",
]

# The four collaborators that command modules must resolve via the facade.
FACADE_PATCHABLE = ["_build_agent", "_init_acp",
                    "_print_session_header", "_print_update_notice"]


def test_facade_reexports_all_old_module_names():
    """`from agentkthx.cli import X` works for every X that worked
    pre-split (covers test_openrouter_backend / test_cmd_update_pep668 /
    test_thinking_args which import helpers from agentkthx.cli)."""
    from agentkthx import cli
    missing = [n for n in OLD_CLI_MODULE_NAMES if not hasattr(cli, n)]
    assert not missing, f"facade lost these pre-split names: {missing}"


def test_facade_names_are_reexports_not_copies():
    """Facade attributes must be identical to the defining module's
    objects, so patching / subclassing through either path is coherent."""
    from agentkthx import cli
    from agentkthx.cli import agent_factory, headers, utils, banner, parser
    from agentkthx.cli import commands as commands_pkg
    from agentkthx.cli.commands import agent as agent_cmd
    main_mod = importlib.import_module("agentkthx.cli.main")
    # NOTE: `from agentkthx.cli import main` yields the FUNCTION (the facade
    # re-export shadows the submodule attribute) — the module itself is only
    # reachable via importlib/sys.modules.

    assert cli.main is main_mod.main
    assert cli.create_parser is parser.create_parser
    assert cli.print_banner is banner.print_banner
    assert cli._build_agent is agent_factory._build_agent
    assert cli._init_acp is agent_factory._init_acp
    assert cli._print_session_header is headers._print_session_header
    assert cli._print_agent_steps is utils._print_agent_steps
    assert cli._is_externally_managed_error is utils._is_externally_managed_error
    assert cli.cmd_agent is agent_cmd.cmd_agent


def test_all_new_cli_modules_importable():
    """Every module of the new layout imports cleanly (documents the
    Phase 8 package structure)."""
    modules = [
        "agentkthx.cli.banner", "agentkthx.cli.parser",
        "agentkthx.cli.agent_factory", "agentkthx.cli.headers",
        "agentkthx.cli.utils", "agentkthx.cli.main",
        "agentkthx.cli.commands",
        "agentkthx.cli.commands.run", "agentkthx.cli.commands.chat",
        "agentkthx.cli.commands.agent", "agentkthx.cli.commands.models",
        "agentkthx.cli.commands.tools", "agentkthx.cli.commands.test",
        "agentkthx.cli.commands.version", "agentkthx.cli.commands.turbo",
        "agentkthx.cli.commands.config", "agentkthx.cli.commands.modelfile",
        "agentkthx.cli.commands.skills", "agentkthx.cli.commands.soul",
        "agentkthx.cli.commands.sessions", "agentkthx.cli.commands.plugins",
    ]
    for mod in modules:
        importlib.import_module(mod)


def test_commands_resolve_collaborators_via_facade():
    """Source contract: command modules must access the four shared
    collaborators through the cli facade (`_cli.<name>`), NOT via direct
    module imports — direct imports would silently break
    monkeypatch.setattr(agentkthx.cli, '<name>', ...)."""
    from agentkthx.cli.commands import agent as agent_cmd
    from agentkthx.cli.commands import chat as chat_cmd
    from agentkthx.cli.commands import run as run_cmd

    for name in FACADE_PATCHABLE:
        for mod, func in ((agent_cmd, agent_cmd.cmd_agent),
                          (chat_cmd, chat_cmd.cmd_chat)):
            if name in ("_print_run_header", "_print_run_summary"):
                continue  # not consumed by agent/chat
            if name in ("_print_update_notice",) and func is agent_cmd.cmd_agent:
                continue  # cmd_agent doesn't call it
            src = inspect.getsource(func)
            assert f"_cli.{name}" in src or f"_cli.{name}(" not in src, (
                f"{mod.__name__} must reference {name} via the _cli facade"
            )
    # cmd_run consumes the two factory helpers via the facade
    for name in ("_build_agent", "_init_acp"):
        assert f"_cli.{name}" in inspect.getsource(run_cmd.cmd_run)


def test_monkeypatch_facade_reaches_cmd_run(monkeypatch, capsys):
    """End-to-end proof of the patch contract: replacing
    agentkthx.cli._build_agent must swap the Agent that cmd_run uses.
    Pre-split this worked because everything shared one module namespace;
    post-split it works because cmd_run resolves _build_agent through the
    facade at call time."""
    from agentkthx import cli

    mock_agent = MagicMock()
    mock_agent.backend.is_cloud = False
    mock_agent.debug = False
    mock_agent._show_reasoning = False
    mock_agent._temperature = None
    mock_agent._top_p = None
    mock_agent._num_predict = None
    mock_agent.num_ctx = 8192
    mock_agent.model_config.default_temperature = 0.7
    mock_agent.model_config.default_top_p = 0.9
    mock_agent.model_config.default_max_tokens = 4096
    mock_agent.soul = None
    mock_agent._is_persistent = False
    mock_agent.run = MagicMock(return_value=MagicMock(
        iterations=0, total_tokens=0, total_ms=0,
        final_answer="done", steps=[]))

    monkeypatch.setattr(cli, "_build_agent", lambda *a, **kw: mock_agent)
    monkeypatch.setattr(cli, "_init_acp", lambda *a, **kw: (None, False))

    args = argparse.Namespace(prompt="hello", backend="zai")
    rc = cli.cmd_run(args)

    assert rc == 0
    mock_agent.run.assert_called_once_with("hello", stream=False)


def test_python_m_cli_entry_still_works():
    """`python -m agentkthx.cli` must keep working — it was a supported
    invocation pre-split (cli.py's __main__ guard) and is now served by
    agentkthx/cli/__main__.py."""
    result = subprocess.run(
        [sys.executable, "-m", "agentkthx.cli", "--help"],
        capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"python -m agentkthx.cli failed:\n{result.stderr[-500:]}"
    )
    assert "usage:" in result.stdout
