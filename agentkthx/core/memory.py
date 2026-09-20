"""
⚛️ AgentKthx — Memory Management
Sliding window memory with optional summarization.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MemoryConfig:
    """Configuration for agent memory."""
    max_messages: int = 50
    max_tokens: int = 4096
    summarization_threshold: float = 0.8
    keep_system: bool = True
    keep_recent: int = 5


@dataclass
class Message:
    """A single message in the conversation."""
    role: str
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # For tool messages

    def to_dict(self) -> dict:
        """Convert to dictionary for API calls."""
        import json
        result = {"role": self.role, "content": self.content}
        if self.tool_calls:
            # Convert internal format to OpenAI ChatCompletions API format
            # Internal: {"id": "x", "name": "tool", "arguments": {...}}
            # OpenAI: {"id": "x", "type": "function", "function": {"name": "tool", "arguments": "{...}"}}
            # Note: arguments MUST be a JSON string, not an object!
            openai_tool_calls = []
            for tc in self.tool_calls:
                if "function" in tc:
                    # Already in function format, ensure arguments is a string
                    func = tc.get("function", {})
                    args = func.get("arguments", {})
                    # Convert object to JSON string if needed
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    openai_tc = {
                        "id": tc.get("id", ""),
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": func.get("name", ""),
                            "arguments": args,
                        }
                    }
                    openai_tool_calls.append(openai_tc)
                else:
                    # Convert from internal format
                    args = tc.get("arguments", {})
                    # Convert object to JSON string
                    if isinstance(args, dict):
                        args = json.dumps(args)
                    openai_tc = {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": args,
                        }
                    }
                    openai_tool_calls.append(openai_tc)
            result["tool_calls"] = openai_tool_calls
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.name:
            result["name"] = self.name
        return result


class Memory:
    """
    Conversation memory with sliding window management.

    Features:
    - Configurable message limit
    - Token-based pruning
    - System message preservation
    - Recent message retention
    """

    def __init__(self, config: MemoryConfig | None = None):
        self.config = config or MemoryConfig()
        self._messages: list[Message] = []
        self._system_prompt: str | None = None

    def add(self, role: str, content: str, **kwargs) -> None:
        """Add a message to memory."""
        msg = Message(role=role, content=content, **kwargs)

        # Track system prompt separately
        if role == "system":
            self._system_prompt = content
            # Remove any existing system messages
            self._messages = [m for m in self._messages if m.role != "system"]

        self._messages.append(msg)
        self._prune_if_needed()

    def add_tool_call(self, role: str, content: str, tool_calls: list[dict]) -> None:
        """Add a message with tool calls."""
        self.add(role, content, tool_calls=tool_calls)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result message."""
        self.add("tool", content, tool_call_id=tool_call_id, name=name)

    def get_messages(self) -> list[dict]:
        """Get all messages as dictionaries."""
        result = []

        # Add system prompt first if present
        if self._system_prompt:
            result.append({"role": "system", "content": self._system_prompt})

        # Add other messages (excluding any system messages in the list)
        for msg in self._messages:
            if msg.role != "system":
                result.append(msg.to_dict())

        return result

    def get_recent(self, n: int = 5) -> list[dict]:
        """Get the n most recent messages."""
        messages = self.get_messages()
        return messages[-n:] if len(messages) > n else messages

    def clear(self) -> None:
        """Clear all messages (except system prompt if configured)."""
        if self.config.keep_system and self._system_prompt:
            self._messages = []
        else:
            self._messages = []
            self._system_prompt = None

    def _prune_if_needed(self) -> None:
        """Prune messages if limits exceeded."""
        if len(self._messages) <= self.config.max_messages:
            return

        # Calculate how many to remove
        threshold = int(self.config.max_messages * self.config.summarization_threshold)
        excess = len(self._messages) - threshold

        if excess <= 0:
            return

        # Keep system messages and recent messages
        recent_to_keep = self.config.keep_recent
        messages_to_remove = len(self._messages) - recent_to_keep - 1  # -1 for system

        if messages_to_remove > 0:
            # Remove oldest non-system messages
            new_messages = []
            skipped = 0
            for msg in self._messages:
                if msg.role == "system":
                    new_messages.append(msg)
                elif skipped >= messages_to_remove:
                    new_messages.append(msg)
                else:
                    skipped += 1
            self._messages = new_messages

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __repr__(self) -> str:
        return f"Memory(messages={len(self._messages)}, max={self.config.max_messages})"