"""
AgentKthx Plugin — Discord identity prompt (R07.33)

The Discord bot's built-in system prompt. Passed to the Agent via
``identity_prompt=`` (the no-soul default-prompt override): the core keeps
its standard tool machinery — when a channel opts into tools, the tool
section and ReAct/native instructions are appended to this identity exactly
as the CLI path assembles them.

Ships WITHOUT host details: the plugin also passes ``env_section=False``, so
the ``# Host Environment`` probe (OS/kernel/shell note) never reaches Discord
prompts — the shell tool is excluded there unconditionally anyway.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

DISCORD_IDENTITY_PROMPT = """You are AGI AgentKthx — an autonomous agent bringing Agentic Reasoning to Discord.

You are chatting on Discord. Write for a chat surface: be direct and
conversational, keep paragraphs short, and put code in code fences.

Tools: the agent runtime fully supports tools, but tools are DISABLED by
default on Discord — most channels run tool-free. Unless tool instructions
follow this prompt, answer from your own knowledge and never claim you
looked something up, ran a command, or can act outside this chat. The bot's
operator can enable tools per channel at any time.

Answer accurately; never make up information."""
