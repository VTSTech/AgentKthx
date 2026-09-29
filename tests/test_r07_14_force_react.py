"""R07.14 — ROB-32 regression tests: force_react re-promoted + factory contract.

User smoke test of R07.14 (2026-09-29):

    agentkthx chat -m z-ai/glm-5.3-flash-free --backend orca --stream --think ...
    TypeError: Agent.__init__ got unexpected keyword argument(s): force_react.

Root cause: ``agent_factory._build_agent`` has passed ``force_react=args.force_react``
since the R07.00 CLI split, but the attribute silently vanished from
``Agent.__init__`` in R03.3 and the kwarg was swallowed by ``**kwargs`` ever
since. ARCH-05 (R07.13) converted that latent drift into a hard TypeError on
EVERY ``agentkthx chat`` / ``agentkthx agent`` invocation. The 1882-test suite
never caught it because the CLI tests patch ``_build_agent`` itself — the
factory/Agent kwarg contract was never exercised end-to-end.

Fix: ``force_react`` re-promoted as an explicit Agent parameter (the README's
``Agent(model=..., force_react=True)`` contract) and wired into ``ToolParser``
as ReAct-only parsing — its documented meaning ("Force ReAct mode for tool
calling"): ReAct is for small models on local inference that emit the ReAct
text protocol; all cloud providers support native tools, so the flag is
False almost always and the default chain must stay byte-identical.

Pinned here:

- ROB-32  ``Agent`` accepts ``force_react`` (default False), stores it, and
          threads it into the ToolParser — both the initial construction
          AND the ``register_tool`` parser rebuild (same init-order
          reasoning as ROB-19's debug flag).
- ROB-32  ``force_react=True`` enforces ReAct-only parsing: explicit
          Action/Action Input blocks still parse; native-JSON (incl. the
          JSON-wrapped ReAct dict) and XML shapes produce NO tool calls.
          Default False runs the full native → ReAct → XML chain.
- ROB-32  THE CONTRACT: ``_build_agent`` → real ``Agent`` construction
          succeeds (the exact smoke-test crash), the ``--force-react``
          flag reaches the agent as True, and the
          ``AGENTKTHX_FORCE_REACT=1`` env path (shared_args) still works.
"""

from __future__ import annotations

import argparse
import inspect
import os
from types import SimpleNamespace

import pytest

from agentkthx.agent import Agent
from agentkthx.cli import agent_factory as fact
from agentkthx.core.models import Tool
from agentkthx.core.tool_parse import ToolParser
from agentkthx.shared_args import parse_shared_args

from tests.test_loop_resilience import StubBackend


# ----------------------------------------------------------------------------
# Fixtures
# ============================================================================

REACT_TEXT = 'Thought: I should list files\nAction: shell\nAction Input: {"command": "ls"}'
# Native strategy: the JSON-wrapped ReAct dict is unwrapped by the native-JSON
# matcher — the ReAct regex cannot match it (quoted keys: no literal "Action:").
WRAPPED_JSON_TEXT = '{"Action": "shell", "Action Input": {"command": "ls"}}'
XML_TEXT = '<tool>shell</tool><args>{"command": "ls"}</args>'

ALL_STRATEGY_TEXTS = [REACT_TEXT, WRAPPED_JSON_TEXT, XML_TEXT]


def _make_shell_tool() -> Tool:
    def _shell(**kwargs):
        return "ok"

    return Tool(
        name="shell",
        description="Runs a shell command",
        params=[],
        handler=_shell,
    )


def _stub_backend() -> StubBackend:
    return StubBackend([])


def _cli_args(**overrides) -> argparse.Namespace:
    """Minimal cmd_chat-shaped namespace — the rest are getattr defaults."""
    fields = dict(
        model="test-model",
        backend="stub",
        debug=False,
        tools="shell,read_file",
        force_react=False,
        security="max",
        soul=None,
        soul_level=2,
    )
    fields.update(overrides)
    return argparse.Namespace(**fields)


def _cli_config() -> SimpleNamespace:
    return SimpleNamespace(
        backend="stub",
        default_model="test-model",
        num_ctx=8192,
        max_tool_retries=2,
        debug=False,
    )


# ----------------------------------------------------------------------------
# ROB-32 — Agent.__init__ accepts and stores force_react
# ============================================================================

class TestAgentForceReactParam:

    def test_default_is_false(self):
        a = Agent(model="test")
        assert a.force_react is False

    def test_explicit_true_stored(self):
        a = Agent(model="test", force_react=True)
        assert a.force_react is True

    def test_signature_has_force_react(self):
        params = inspect.signature(Agent.__init__).parameters
        assert "force_react" in params
        assert params["force_react"].default is False

    def test_typeerror_message_lists_force_react_as_valid(self):
        # The ARCH-05 fail-fast message lists the valid kwargs — force_react
        # must appear there (it IS valid now) so the error stays truthful.
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            Agent(model="test", force_reactt=True)  # typo
        with pytest.raises(TypeError) as ei:
            Agent(model="test", completely_unknown=1)
        assert "force_react" in str(ei.value)


# ----------------------------------------------------------------------------
# ROB-32 — ToolParser: force_react enforces ReAct-only parsing
# ============================================================================

class TestToolParserForceReact:

    def test_default_chain_parses_all_three_shapes(self):
        # Default False: byte-identical to the standing native → ReAct → XML
        # chain (baseline pin — the flag must not change existing behavior).
        parser = ToolParser(["shell"])
        for text in ALL_STRATEGY_TEXTS:
            calls = parser.parse(text)
            assert len(calls) == 1, f"shape failed under default chain: {text!r}"
            assert calls[0].name == "shell"

    def test_forced_parses_react_blocks(self):
        parser = ToolParser(["shell"], force_react=True)
        assert parser.force_react is True
        calls = parser.parse(REACT_TEXT)
        assert len(calls) == 1
        assert calls[0].name == "shell"
        assert calls[0].arguments == {"command": "ls"}

    def test_forced_skips_native_json(self):
        parser = ToolParser(["shell"], force_react=True)
        assert parser.parse(WRAPPED_JSON_TEXT) == []

    def test_forced_skips_xml(self):
        parser = ToolParser(["shell"], force_react=True)
        assert parser.parse(XML_TEXT) == []

    def test_default_flag_is_false(self):
        assert ToolParser(["shell"]).force_react is False


# ----------------------------------------------------------------------------
# ROB-32 — the flag survives the register_tool parser rebuild
# ============================================================================

class TestAgentParserThreading:

    def test_initial_parser_threads_flag(self):
        a = Agent(model="test", force_react=True)
        assert a._parser.force_react is True

    def test_flag_survives_register_tool_rebuild(self):
        a = Agent(model="test", force_react=True)
        a.register_tool(_make_shell_tool())
        # register_tool re-creates the parser (ROB-13/ROB-19 rebuild) — the
        # force_react flag must survive exactly like debug does.
        assert a._parser.force_react is True

    def test_react_only_end_to_end_through_agent(self):
        a = Agent(model="test", force_react=True)
        a.register_tool(_make_shell_tool())
        calls = a._parser.parse(REACT_TEXT)
        assert len(calls) == 1 and calls[0].name == "shell"
        assert a._parser.parse(WRAPPED_JSON_TEXT) == []


# ----------------------------------------------------------------------------
# ROB-32 — THE CONTRACT: _build_agent → real Agent (the smoke-test crash)
# ============================================================================

class TestFactoryAgentContract:

    def test_build_agent_constructs_real_agent(self, monkeypatch):
        # The exact user smoke test: _build_agent passing force_react to a
        # REAL Agent raised TypeError before the fix. No Agent mocking —
        # only the backend is stubbed (offline).
        monkeypatch.setattr(fact, "get_backend", lambda *a, **kw: _stub_backend())
        agent = fact._build_agent(_cli_args(), _cli_config())
        assert isinstance(agent, Agent)
        assert agent.force_react is False
        assert agent._parser.force_react is False

    def test_build_agent_threads_force_react_flag(self, monkeypatch):
        monkeypatch.setattr(fact, "get_backend", lambda *a, **kw: _stub_backend())
        agent = fact._build_agent(_cli_args(force_react=True), _cli_config())
        assert agent.force_react is True
        assert agent._parser.force_react is True
        assert agent._parser.parse(REACT_TEXT) != []
        assert agent._parser.parse(WRAPPED_JSON_TEXT) == []

    def test_factory_kwargs_all_accepted_by_agent_signature(self, monkeypatch):
        # Belt-and-braces variant of the contract: capture what the factory
        # passes and assert every kwarg exists on the real Agent signature.
        # (The real-construction tests above already fail on unknown kwargs
        # via ARCH-05's fail-fast; this pins the full set for diagnostics.)
        # NOTE: capture via a subclass of the REAL Agent — a MagicMock
        # subclass pollutes **kwargs with the mock machinery's own
        # (name/parent/wraps/...) arguments.
        captured = {}
        real_init = Agent.__init__

        class _CaptureAgent(Agent):
            def __init__(self, **kwargs):
                captured.update(kwargs)
                real_init(self, **kwargs)

        monkeypatch.setattr(fact, "get_backend", lambda *a, **kw: _stub_backend())
        monkeypatch.setattr(fact, "Agent", _CaptureAgent)
        fact._build_agent(_cli_args(), _cli_config())

        real_params = set(inspect.signature(Agent.__init__).parameters)
        unknown = set(captured) - real_params
        assert not unknown, f"factory passes kwargs Agent rejects: {sorted(unknown)}"
        assert captured.get("force_react") is False

    def test_env_var_path_still_sets_force_react(self, monkeypatch):
        # shared_args: AGENTKTHX_FORCE_REACT=1 — the non-CLI surface.
        monkeypatch.setenv("AGENTKTHX_FORCE_REACT", "1")
        cfg = parse_shared_args(argparse.Namespace(force_react=False))
        assert cfg.force_react is True

    def test_env_var_unset_keeps_flag_false(self, monkeypatch):
        monkeypatch.delenv("AGENTKTHX_FORCE_REACT", raising=False)
        cfg = parse_shared_args(argparse.Namespace(force_react=False))
        assert cfg.force_react is False
