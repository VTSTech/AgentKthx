"""
AgentKthx Plugin — ZAI Cloud Backend

ZAI API backend for GLM models via OpenAI Chat-Completions.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the ZAI backend with the plugin manager."""
    from .zai import ZaiBackend
    manager.register_backend("zai", ZaiBackend)


def unregister(manager) -> None:
    """Unregister the ZAI backend from the plugin manager."""
    manager.unregister_backend("zai")
