"""
AgentKthx Plugin — OpenRouter Cloud Backend

OpenRouter API backend for 500+ models via OpenAI Chat-Completions API.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the OpenRouter backend with the plugin manager."""
    from .openrouter import OpenRouterBackend
    manager.register_backend("openrouter", OpenRouterBackend)


def unregister(manager) -> None:
    """Unregister the OpenRouter backend from the plugin manager."""
    manager.unregister_backend("openrouter")