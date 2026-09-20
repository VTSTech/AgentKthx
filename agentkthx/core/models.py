"""
⚛️ AgentKthx — Core Models
Data models for agent execution.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from .types import StepResultType


@dataclass
class ToolParam:
    """Parameter definition for a tool."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None
    enum: list[str] | None = None

    def to_json_schema(self) -> dict:
        """Convert to JSON Schema format."""
        schema = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        return schema


@dataclass
class Tool:
    """Tool definition for agent use."""
    name: str
    description: str
    params: list[ToolParam] = field(default_factory=list)
    handler: callable | None = None
    dangerous: bool = False
    category: str = "general"

    def to_json_schema(self) -> dict:
        """Convert to JSON Schema format for function calling (Ollama native format)."""
        properties = {}
        required = []

        for param in self.params:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
    
    def to_openai_schema(self) -> dict:
        """Convert to OpenAI Chat-Completions tool format.
        
        This is the format expected by OpenAI's /v1/chat/completions endpoint
        and Ollama's OpenAI-compatible endpoint.
        
        The format is identical to Ollama's native format, so we delegate.
        """
        return self.to_json_schema()

    def execute(self, **kwargs) -> Any:
        """Execute the tool with given arguments."""
        if self.handler is None:
            raise RuntimeError(f"Tool '{self.name}' has no handler")
        return self.handler(**kwargs)


@dataclass
class ToolCall:
    """Represents a parsed tool call from model output.
    
    OpenResponses Enhancement: Includes thought capture for ReasoningItem.
    """
    name: str
    arguments: dict[str, Any]
    raw: str = ""  # Original text that was parsed
    confidence: float = 1.0  # Confidence of parsing (for fuzzy matches)
    final_answer: str | None = None  # OpenResponses: Final answer if present in same content
    thought: str | None = None  # OpenResponses: Captured reasoning for ReasoningItem


@dataclass
class StepResult:
    """Result of a single agent step."""
    type: StepResultType
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: Any = None
    error: str | None = None
    raw_response: str = ""
    tokens_used: int = 0
    latency_ms: float = 0.0
    # R05.8: Captured reasoning_content (chain-of-thought) emitted by
    # thinking-capable models (GLM-4.5+, o-series, deepseek-r1, qwen3
    # in thinking mode). Populated by the backend; surfaced in the CLI
    # via --think. Empty string for non-thinking models.
    reasoning_content: str = ""


@dataclass
class AgentRun:
    """Complete result of an agent execution."""
    final_answer: str
    steps: list[StepResult] = field(default_factory=list)
    total_tokens: int = 0
    total_ms: float = 0.0
    tool_calls: int = 0
    success: bool = True
    error: str | None = None

    @property
    def iterations(self) -> int:
        """Number of iterations taken."""
        return len(self.steps)