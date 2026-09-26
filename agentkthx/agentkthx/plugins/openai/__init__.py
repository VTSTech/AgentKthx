"""
AgentKthx Plugin — OpenAI Cloud Backend

OpenAI API backend for GPT-6/GPT-5.6/GPT-4o families via the OpenAI
Chat-Completions API at https://api.openai.com/v1.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the OpenAI backend with the plugin manager.

    Registers the canonical backend name ``openai`` plus an ``oai``
    alias (using ``alias_of``) so users can write
    ``agentkthx chat --backend oai`` for ergonomics. The alias resolves
    to the same ``OpenAIBackend`` class — both names produce identical
    backend instances.
    """
    from .openai import OpenAIBackend
    manager.register_backend("openai", OpenAIBackend)
    manager.register_backend("oai", OpenAIBackend, alias_of="openai")


def unregister(manager) -> None:
    """Unregister the OpenAI backend from the plugin manager."""
    manager.unregister_backend("openai")
    manager.unregister_backend("oai")
