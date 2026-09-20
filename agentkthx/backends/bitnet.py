"""
⚛️ AgentKthx — BitNet Backend (Deprecated)

BitNet is now merged into LlamaServerBackend.
Use --backend bitnet (alias) or --backend llama-server.

This module is kept for backward compatibility (import paths).
All functionality lives in llama_server.py with bitnet_mode=True.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from .llama_server import LlamaServerBackend


class BitNetBackend(LlamaServerBackend):
    """
    Backend for BitNet inference engine (deprecated).

    BitNet is a 1-bit LLM inference engine — it IS a patched llama.cpp/llama-server.
    This class is now a thin wrapper around LlamaServerBackend with bitnet_mode=True.

    Behavior when bitnet_mode=True:
    - Default API mode: OPENRE (/completion only, no OpenAI endpoint)
    - Default base URL: BITNET_BASE_URL (localhost:8765)
    - Stop sequences: empty (no defaults)
    - Tool support: always REACT
    - list_models: hardcoded stub
    - backend_type: BackendType.BITNET

    Migration:
        # Before:
        backend = BitNetBackend()
        agentkthx chat --backend bitnet

        # After (both still work):
        backend = BitNetBackend()           # via this wrapper
        backend = get_backend("bitnet")     # via alias
        backend = LlamaServerBackend(bitnet_mode=True)  # direct
    """

    def __init__(
        self,
        base_url: str | None = None,
        host: str | None = None,
        port: int | None = None,
        config=None,
        **kwargs,
    ):
        # Pass through all args, force bitnet_mode=True
        super().__init__(
            base_url=base_url,
            host=host,
            port=port,
            config=config,
            bitnet_mode=True,
            **kwargs,
        )

    def __repr__(self) -> str:
        return f"BitNetBackend(url={self._base_url})"