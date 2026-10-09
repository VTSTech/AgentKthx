"""
AgentKthx Plugin — DuckDuckGo AI Chat Backend

DuckDuckGo AI Chat (duck.ai) backend — the keyless, anonymous,
zero-cost LLM surface: no API key, no signup, no quota. Current
lineup (GPT-6 Luna, GPT-5.6 Luna, GPT-5.4 Nano/Mini, Claude
Haiku 4.5, Mistral Small 4, and the Tinfoil-hosted gpt-oss-120b /
Gemma 4 31B) proxied through DuckDuckGo's privacy layer via the
NON-OpenAI /duckchat/v1 protocol — now CHALLENGE-BASED (the
x-vqd-hash-1 JS proof replaced the retired x-vqd-4 token; solved
by the bundled Node helper).

The only AgentKthx cloud backend that subclasses BaseBackend
directly instead of CloudBackend — see duckduckgo.py for the
protocol rationale.

See docs/api/DUCKDUCKGO_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the DuckDuckGo backend with the plugin manager."""
    from .duckduckgo import DuckDuckGoBackend

    manager.register_backend("duckduckgo", DuckDuckGoBackend)
    # Alias for convenience — `--backend duckduckgo` and `--backend ddg`
    # both work. Mirrors the SiliconFlow `sf` alias pattern.
    manager.register_backend("ddg", DuckDuckGoBackend)


def unregister(manager) -> None:
    """Unregister the DuckDuckGo backend from the plugin manager."""
    manager.unregister_backend("duckduckgo")
    manager.unregister_backend("ddg")
