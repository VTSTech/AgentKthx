"""
AgentKthx Plugin — SiliconFlow Cloud Backend

SiliconFlow API backend for the 79-model live catalog (DeepSeek, Qwen,
GLM, Kimi, MiniMax, Hunyuan, Gemma, gpt-oss) via the OpenAI
Chat-Completions API at api.siliconflow.com. No free tier — every
model bills (Qwen/Qwen3-8B is the cheapest known, input ≈$0.06/1M
tokens, billing-verified R07.29).

See docs/api/SILICONFLOW_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the SiliconFlow backend with the plugin manager."""
    from .siliconflow import SiliconFlowBackend

    manager.register_backend("siliconflow", SiliconFlowBackend)
    # Alias for convenience — `--backend siliconflow` and `--backend sf`
    # both work. Mirrors the NVIDIA plugin's `nim` alias pattern.
    manager.register_backend("sf", SiliconFlowBackend)


def unregister(manager) -> None:
    """Unregister the SiliconFlow backend from the plugin manager."""
    manager.unregister_backend("siliconflow")
    manager.unregister_backend("sf")
