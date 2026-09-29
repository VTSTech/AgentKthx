"""
AgentKthx Plugin — Mistral Cloud Backend

Mistral La Plateforme backend for the OpenAI Chat-Completions wire format
at https://api.mistral.ai/v1.

Mistral's API is OpenAI-shaped with deliberate deltas — `random_seed`
instead of `seed`, `finish_reason: "model_length"` for context overflow,
a `reasoning_effort` ladder that includes the extra `"xhigh"` rung,
optional `safe_prompt` injection, and AssistantMessage `prefix: true`
prefill support. See
``docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md`` for the full delta list.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Mistral backend with the plugin manager.

    Registers the canonical backend name ``mistral`` plus an ``mst``
    alias (using ``alias_of``) so users can write
    ``agentkthx chat --backend mst`` for ergonomics. The alias resolves
    to the same ``MistralBackend`` class — both names produce identical
    backend instances.
    """
    from .mistral import MistralBackend

    manager.register_backend("mistral", MistralBackend)
    manager.register_backend("mst", MistralBackend, alias_of="mistral")


def unregister(manager) -> None:
    """Unregister the Mistral backend from the plugin manager."""
    manager.unregister_backend("mistral")
    manager.unregister_backend("mst")
