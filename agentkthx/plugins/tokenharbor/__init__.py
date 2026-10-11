"""
AgentKthx Plugin — TokenHarbor Cloud Backend

TokenHarbor API backend (one key, every model) via the OpenAI
Chat-Completions API at tokenharbor.ai/v1. thk_live_ universal keys,
:free-suffixed free tier, auth-gated /v1/models, and a never-auto-confirm
spend gate.

See docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the TokenHarbor backend with the plugin manager."""
    from .tokenharbor import TokenHarborBackend

    manager.register_backend("tokenharbor", TokenHarborBackend)


def unregister(manager) -> None:
    """Unregister the TokenHarbor backend from the plugin manager."""
    manager.unregister_backend("tokenharbor")
