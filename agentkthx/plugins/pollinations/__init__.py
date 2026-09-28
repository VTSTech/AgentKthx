"""
AgentKthx Plugin — Pollinations Cloud Backend

Pollinations unified gateway backend for the OpenAI Chat-Completions
wire format at https://gen.pollinations.ai/v1.

Pollinations is an OpenAI-compatible *aggregator*: one credential routes
to dozens of vendor families (openai/, anthropic/, google/, z-ai/,
deepseek/, qwen/, mistralai/, meta/, community/...) plus media
generation (images, video, TTS, embeddings) behind the same key.
Deliberate deltas vs the OpenAI spec — provider-prefixed model ids,
the Pollinations error envelope with ``requestId``, 402
PAYMENT_REQUIRED for pollen-budget exhaustion, live per-model health
telemetry, and the ``safe`` moderation flag — are documented in
``docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md``.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Pollinations backend with the plugin manager.

    Registers the canonical backend name ``pollinations`` plus a
    ``poll`` alias (using ``alias_of``) so users can write
    ``agentkthx chat --backend poll`` for ergonomics. The alias
    resolves to the same ``PollinationsBackend`` class — both names
    produce identical backend instances.
    """
    from .pollinations import PollinationsBackend
    manager.register_backend("pollinations", PollinationsBackend)
    manager.register_backend("poll", PollinationsBackend, alias_of="pollinations")


def unregister(manager) -> None:
    """Unregister the Pollinations backend from the plugin manager."""
    manager.unregister_backend("pollinations")
    manager.unregister_backend("poll")
