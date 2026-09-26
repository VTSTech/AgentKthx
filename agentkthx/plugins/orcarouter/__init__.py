"""
AgentKthx Plugin — OrcaRouter Cloud Backend

OrcaRouter is a zero-markup gateway to 11 upstream LLM providers
(OpenAI, Anthropic, Google, DeepSeek, Grok, Qwen, Kimi, MiniMax,
ZAI, Kling, BytePlus). 200+ models via OpenAI Chat-Completions API.
Free tier has 4 genuinely $0/token models; paid tier is the upstream
provider's per-token rate with no markup.

See docs/ORCAROUTER_API_TECHNICAL_REFERENCE.md for full API details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the OrcaRouter backend with the plugin manager.

    Registers the canonical backend name ``orcarouter`` plus an ``orca``
    alias (mirrors the HuggingFace plugin's ``hf`` alias pattern).
    Both names resolve to the same ``OrcaRouterBackend`` class —
    ``agentkthx chat --backend orca`` and ``--backend orcarouter`` are
    fully interchangeable.
    """
    from .orcarouter import OrcaRouterBackend
    manager.register_backend("orcarouter", OrcaRouterBackend)
    manager.register_backend("orca", OrcaRouterBackend, alias_of="orcarouter")


def unregister(manager) -> None:
    """Unregister the OrcaRouter backend from the plugin manager."""
    manager.unregister_backend("orcarouter")
    manager.unregister_backend("orca")
