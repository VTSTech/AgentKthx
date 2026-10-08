"""
AgentKthx Plugin — SiliconFlow Cloud Backend

SiliconFlow API backend for 200+ models (DeepSeek, Qwen, GLM, Llama, Kimi,
MiniMax, ERNIE, Hunyuan, Gemma, gpt-oss) via the OpenAI Chat-Completions
API at api.siliconflow.com. Free tier with 3 permanently-free models
(Qwen3-8B, DeepSeek-R1-Distill-Qwen-7B, DeepSeek-OCR), no quota, no
credit card required.

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
