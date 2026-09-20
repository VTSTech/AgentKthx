"""
⚛️ AgentNova — Core Types
Enumeration types used throughout the framework.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Literal


class StepResultType(Enum):
    """Result type of an agent step."""
    TOOL_CALL = auto()
    FINAL_ANSWER = auto()
    ERROR = auto()
    MAX_STEPS = auto()


class ToolSupportLevel(Enum):
    """Level of tool support provided by a model.
    
    Detection is purely runtime-based. Each model must be tested individually
    because tool support depends on the model's template, not its family.
    
    Use backend.test_tool_support() for runtime detection, or check the cache
    via tool_cache.get_cached_tool_support().
    """
    NATIVE = "native"      # Native function calling support (API tool_calls)
    REACT = "react"        # Text-based tool use via ReAct prompting
    NONE = "none"          # No tool support (pure reasoning)
    UNTESTED = "untested"  # Not yet tested - each model must be tested individually

    @classmethod
    def detect(cls, model_name: str, backend=None, use_cache: bool = True) -> "ToolSupportLevel":
        """
        Get tool support level for a model.
        
        This method checks the cache first. If not cached, returns UNTESTED.
        For actual runtime testing, use backend.test_tool_support(force_test=True).
        
        Args:
            model_name: Name of the model
            backend: Optional backend for runtime testing (not used by default)
            use_cache: If True, check cache first (default: True)
            
        Returns:
            ToolSupportLevel (UNTESTED if not in cache)
        """
        if use_cache:
            from .tool_cache import get_cached_tool_support
            cached = get_cached_tool_support(model_name)
            if cached is not None:
                return cached
        
        # If backend provided and we want to test, do so
        if backend is not None and hasattr(backend, 'test_tool_support'):
            return backend.test_tool_support(model_name, force_test=True)
        
        # Cannot determine without testing
        return cls.UNTESTED


class BackendType(Enum):
    """Supported backend types."""
    OLLAMA = "ollama"
    LLAMA_SERVER = "llama_server"
    BITNET = "bitnet"
    ZAI = "zai"
    OPENROUTER = "openrouter"


class ApiMode(Enum):
    """API mode for backend communication.
    
    - OPENRE: OpenResponses API (open spec for agentic workflows)
    - OPENAI: OpenAI Chat-Completions API
    - JEV:    System-One decision mode (Jev-compatible shape)
              Uses OpenAI Chat-Completions wire format under the hood,
              but wraps the call with a constrained decision prompt
              and parses JSON output into a {decision, probability,
              alternatives} envelope. Works with any chat-capable
              backend (Ollama, ZAI, OpenRouter, llama-server).
              Native TypeSafe Jev (api.typesafe.ai/v1/systemone) is
              NOT used — this mode emulates the Jev API shape using
              free LLMs you already have access to.
    
    Ollama supports both endpoints:
    - /api/chat - OpenResponses format (default)
    - /v1/chat/completions - OpenAI-compatible format
    """
    OPENRE = "openre"      # OpenResponses API (/api/chat)
    OPENAI = "openai"      # OpenAI Chat-Completions API (/v1/chat/completions)
    JEV = "jev"            # System-One decision shape, powered by any LLM


# Type aliases for clarity
ModelName = str
ToolName = str
MessageRole = Literal["system", "user", "assistant", "tool"]