"""
AgentKthx Plugin — Stable Diffusion (sd.cpp) Backend

Image OUTPUT on free local inference via leejet/stable-diffusion.cpp,
following the Ollama pattern: the server is an EXTERNAL process
(notebook serve cell / user shell) that AgentKthx never manages —
this backend only talks HTTP to its OpenAI-shaped image surface.

    agentkthx run "a lovely cat" --backend sd
    # shorthand alias for --backend stable-diffusion

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Stable Diffusion backend (canonical name + 'sd' alias)."""
    from .backend import StableDiffusionBackend

    manager.register_backend("stable-diffusion", StableDiffusionBackend)
    # Shorthand alias — plugins/_loader.py resolves aliases via
    # _backend_aliases (get_backend_class falls through to the canonical
    # registration), so "sd" and "stable-diffusion" are the same class.
    manager.register_backend("sd", StableDiffusionBackend, alias_of="stable-diffusion")


def unregister(manager) -> None:
    """Unregister the Stable Diffusion backend and its alias."""
    manager.unregister_backend("sd")
    manager.unregister_backend("stable-diffusion")
