"""
AgentKthx Plugin — NVIDIA NIM Cloud Backend

NVIDIA NIM API backend for 80+ models (Llama, Mistral, Qwen, Phi, DeepSeek,
NV Nemotron, Granite, GLM) via OpenAI Chat-Completions API at
integrate.api.nvidia.com. Free tier with monthly-recurring 1,000 inference
credits, no credit card required.

See docs/api/NVIDIA_NIM_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the NVIDIA NIM backend with the plugin manager."""
    from .nvidia import NvidiaBackend

    manager.register_backend("nvidia", NvidiaBackend)
    # Alias for convenience — `--backend nvidia` and `--backend nim`
    # both work. Mirrors the OpenAI plugin's `oai` alias pattern.
    manager.register_backend("nim", NvidiaBackend)


def unregister(manager) -> None:
    """Unregister the NVIDIA NIM backend from the plugin manager."""
    manager.unregister_backend("nvidia")
    manager.unregister_backend("nim")
