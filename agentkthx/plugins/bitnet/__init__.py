"""
AgentKthx Plugin — BitNet 1.58b Backend

Thin wrapper around LlamaServerBackend with bitnet_mode=True.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the BitNet backend with the plugin manager."""
    from .bitnet import BitNetBackend
    manager.register_backend("bitnet", BitNetBackend)


def unregister(manager) -> None:
    """Unregister the BitNet backend from the plugin manager."""
    manager.unregister_backend("bitnet")
