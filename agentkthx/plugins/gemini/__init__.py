"""
AgentKthx Plugin — Gemini Cloud Backend

Google AI Studio / Gemini API via OpenAI-compatible endpoint
(https://generativelanguage.googleapis.com/v1beta/openai/).

Provides access to Gemini 3.x and 2.5 family models with native
function calling, streaming, multimodal input, and the thinking /
reasoning configuration described in
docs/GEMINI_API_TECHNICAL_REFERENCE.md.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Gemini backend with the plugin manager."""
    from .gemini import GeminiBackend
    manager.register_backend("gemini", GeminiBackend)


def unregister(manager) -> None:
    """Unregister the Gemini backend from the plugin manager."""
    manager.unregister_backend("gemini")
