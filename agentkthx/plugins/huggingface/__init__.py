"""
AgentKthx Plugin — Hugging Face Inference Router Backend

Hugging Face Inference Router backend for 100+ open models via the
OpenAI Chat-Completions API at https://router.huggingface.co/v1.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Hugging Face backend with the plugin manager.

    Registers the canonical backend name ``huggingface`` plus an ``hf``
    alias (using ``alias_of``) so users can write
    ``agentkthx chat --backend hf`` for ergonomics. The alias resolves
    to the same ``HuggingFaceBackend`` class — both names produce
    identical backend instances.
    """
    from .huggingface import HuggingFaceBackend
    manager.register_backend("huggingface", HuggingFaceBackend)
    manager.register_backend("hf", HuggingFaceBackend, alias_of="huggingface")


def unregister(manager) -> None:
    """Unregister the Hugging Face backend from the plugin manager."""
    manager.unregister_backend("huggingface")
    manager.unregister_backend("hf")
