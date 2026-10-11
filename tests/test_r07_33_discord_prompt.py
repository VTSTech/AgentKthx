"""
AgentKthx — Discord identity prompt (R07.33)

The Discord bot ships its own system prompt: AGI AgentKthx — bringing
Agentic Reasoning to Discord, with tools explicitly supported-but-disabled-
by-default and NO host details. Core seam under test: Agent(identity_prompt=
..., env_section=False) — the identity replaces the stock no-soul prompt
while the standard tool machinery (native/ReAct + tool section) keeps
applying when tools are attached.

Written by VTSTech — https://www.vts-tech.org
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.agent import Agent  # noqa: E402
from agentkthx.core.agent_setup import AgentSetupMixin  # noqa: E402
from agentkthx.plugins.discord.prompt import DISCORD_IDENTITY_PROMPT  # noqa: E402

STOCK_NO_TOOLS = "You are AGI AgentKthx. Answer questions directly and accurately."


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


class TestDiscordPromptContent:
    def test_identity_and_tagline(self):
        assert "AGI AgentKthx" in DISCORD_IDENTITY_PROMPT
        assert "bringing Agentic Reasoning to Discord" in DISCORD_IDENTITY_PROMPT

    def test_tools_supported_but_disabled_by_default(self):
        low = " ".join(DISCORD_IDENTITY_PROMPT.lower().split())
        assert "supports tools" in low
        assert "disabled by default" in low

    def test_no_host_details(self):
        assert "# Host Environment" not in DISCORD_IDENTITY_PROMPT
        assert "OS:" not in DISCORD_IDENTITY_PROMPT
        assert "shell tool runs" not in DISCORD_IDENTITY_PROMPT.lower()

    def test_honest_when_tools_attached(self):
        """The 'unless tool instructions follow this prompt' clause keeps the
        identity truthful when a channel opts into tools."""
        low = " ".join(DISCORD_IDENTITY_PROMPT.lower().split())
        assert "unless tool instructions follow this prompt" in low


# ---------------------------------------------------------------------------
# Core seam: Agent(identity_prompt=..., env_section=...)
# ---------------------------------------------------------------------------


class TestAgentIdentitySeam:
    def test_identity_replaces_no_tools_prompt(self):
        agent = Agent(
            model="qwen2.5:0.5b",
            tools=[],
            soul=None,
            identity_prompt="You are TestKthx.",
            env_section=False,
        )
        assert agent._custom_system_prompt == "You are TestKthx."

    def test_stock_prompt_when_no_identity(self):
        agent = Agent(model="qwen2.5:0.5b", tools=[], soul=None, env_section=False)
        assert agent._custom_system_prompt == STOCK_NO_TOOLS

    def test_env_section_skipped_when_disabled(self):
        agent = Agent(model="qwen2.5:0.5b", tools=[], soul=None, env_section=False)
        assert "# Host Environment" not in agent._custom_system_prompt

    def test_env_section_present_by_default(self):
        """Default stays True — CLI/chat behavior is unchanged (R07.19)."""
        agent = Agent(model="qwen2.5:0.5b", tools=[], soul=None)
        assert "# Host Environment" in agent._custom_system_prompt

    def test_system_prompt_still_wins_over_identity(self):
        agent = Agent(
            model="qwen2.5:0.5b",
            tools=[],
            system_prompt="Custom override.",
            identity_prompt="You are TestKthx.",
            env_section=False,
        )
        assert agent._custom_system_prompt == "Custom override."

    def test_identity_prepended_to_react_prompt(self):
        """Tools attached + ReAct protocol: identity first, tool machinery intact."""
        agent = Agent(
            model="qwen2.5:0.5b",
            tools=["calculator"],
            soul=None,
            identity_prompt="You are TestKthx.",
            env_section=False,
        )
        prompt = agent._custom_system_prompt
        assert prompt.startswith("You are TestKthx.")
        assert "Action Input" in prompt  # ReAct format block preserved
        assert "### Tool Reference" in prompt  # tool section still appended

    def test_soul_ignores_identity(self):
        """A configured soul carries its own identity — the Discord prompt
        must not leak into it."""
        agent = Agent(
            model="qwen2.5:0.5b",
            tools=[],
            soul="kthx-helper",
            identity_prompt="You are TestKthx.",
            env_section=False,
        )
        assert agent._custom_system_prompt.startswith("You are TestKthx.") is False
        assert agent.soul is not None


class _Host(AgentSetupMixin):
    """Never fully initialized — exposes only what _build_default_prompt reads."""

    def __init__(self, *, is_bitnet=False, comp=False, force_react=False, identity=None):
        self._is_bitnet = is_bitnet
        self._comp = comp
        self.force_react = force_react
        self._identity_prompt = identity

    @property
    def _is_comp_mode(self):
        return self._comp


class TestDefaultPromptVariants:
    def test_no_tools_with_identity(self):
        assert _Host(identity="IDENT")._build_default_prompt(False) == "IDENT"

    def test_no_tools_without_identity_is_stock(self):
        assert _Host()._build_default_prompt(False) == STOCK_NO_TOOLS

    def test_react_with_identity(self):
        prompt = _Host(identity="IDENT")._build_default_prompt(True)
        assert prompt.startswith("IDENT")
        assert "Action Input" in prompt

    def test_react_without_identity_is_stock(self):
        prompt = _Host()._build_default_prompt(True)
        assert prompt.startswith("You are AI AgentKthx with access to tools.")

    def test_native_with_identity(self):
        prompt = _Host(identity="IDENT", comp=True)._build_default_prompt(True)
        assert prompt.startswith("IDENT")
        assert "function calls" in prompt
        assert "Action Input" not in prompt

    def test_bitnet_ignores_identity(self):
        """BitNet's lean prompt stays under its crash budget — identity never
        grows it."""
        prompt = _Host(is_bitnet=True, identity="X" * 10_000)._build_default_prompt(True)
        assert len(prompt) < 500
        assert "X" * 100 not in prompt
