"""
AgentKthx Plugin — DuckDuckGo AI Chat Backend

DuckDuckGo AI Chat (duck.ai) backend — the keyless, anonymous,
zero-cost LLM surface: no API key, no signup, no quota. Frontier
upstream models (GPT-4o mini, o3-mini, Claude Haiku, Llama 3.3 70B,
Mistral Small 3 24B) proxied through DuckDuckGo's privacy layer via
the NON-OpenAI /duckchat/v1 protocol (x-vqd-4 token handshake).

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
