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
        # R06.52: repair tool-call pairing before handing history to the
        # backend. Orphan tool results and dangling tool calls both produce
        # illegal ChatCompletions sequences (HTTP 400 on OpenRouter,
        # code 1214 on ZAI). Sanitizing is idempotent.
        self.sanitize_history()
        result = []

        # Add system prompt first if present
        if self._system_prompt:
            result.append({"role": "system", "content": self._system_prompt})

        # Add other messages (excluding any system messages in the list)
        for msg in self._messages:
            if msg.role != "system":
                result.append(msg.to_dict())

        return result

    def sanitize_history(self) -> None:
        """
        Repair tool-call pairing in the history (R06.52, idempotent).

        1. Drops orphan ``tool`` results whose tool_call_id was never
           announced by a preceding assistant message (or whose announcing
           assistant message was pruned away).
        2. Inserts a placeholder tool result for dangling assistant
           tool_calls that never received a result (the run was interrupted
           before execution), so the sequence stays API-valid.
        """
        # ---- pass 1: drop orphan tool results ----
        available: set[str] = set()
        cleaned: list[Message] = []
        for m in self._messages:
            if m.role == "assistant" and m.tool_calls:
                cleaned.append(m)
                for tc in m.tool_calls:
                    if isinstance(tc, dict):
                        cid = tc.get("id", "")
                        if cid:
                            available.add(cid)
            elif m.role == "tool":
                if m.tool_call_id and m.tool_call_id in available:
                    cleaned.append(m)
                # else: orphan result — drop silently
            else:
                cleaned.append(m)

        # ---- pass 2: fill dangling calls with placeholder results ----
        answered: set[str] = {
            m.tool_call_id for m in cleaned
            if m.role == "tool" and m.tool_call_id
        }
        final: list[Message] = []
        i = 0
        n = len(cleaned)
        while i < n:
            m = cleaned[i]
            final.append(m)
            i += 1
            if not (m.role == "assistant" and m.tool_calls):
                continue
            # Consume the contiguous run of tool results that follows this
            # assistant message, THEN append placeholders — real results
            # stay adjacent to their call, placeholders come after.
            j = i
            while j < n and cleaned[j].role == "tool":
                final.append(cleaned[j])
                j += 1
            for tc in m.tool_calls:
                if not isinstance(tc, dict):
                    continue
                cid = tc.get("id", "")
                if cid and cid not in answered:
                    final.append(Message(
                        role="tool",
                        content=(
                            "Error: no result was recorded for this tool "
                            "call (the run was interrupted before it "
                            "completed). Continue, but do not retry it "
                            "blindly."
                        ),
                        tool_call_id=cid,
                        name=tc.get("name"),
                    ))
                    answered.add(cid)
            i = j

        self._messages = final

    def clear(self) -> None:
        """Clear all messages (except system prompt if configured)."""
        if self.config.keep_system and self._system_prompt:
            self._messages = []
        else:
            self._messages = []
            self._system_prompt = None

    def _prune_if_needed(self) -> None:
        """
        Prune messages if limits exceeded (R06.52, pairing-safe).

        Instead of collapsing history down to ``keep_recent`` messages (which
        destroyed context and orphaned tool results mid-pair), the window
        *slides* down to the summarization threshold:

            keep_count = max(1, int(max_messages * summarization_threshold))

        e.g. 50 messages @ 0.8 → slide to 40. This reclaims headroom so the
        next few adds don't re-trigger pruning, and keeps recent tool-call
        pairs intact. Tool results whose announcing assistant message fell
        out of the window are dropped from the head (they would otherwise
        be orphaned and make the API sequence illegal).
        """
        if len(self._messages) <= self.config.max_messages:
            return

        keep_count = max(1, int(
            self.config.max_messages * self.config.summarization_threshold
        ))

        systems = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]

        excess = len(non_system) - keep_count
        if excess > 0:
            non_system = non_system[excess:]

        # Pairing-safe head trim: a kept window must not START with a tool
        # result (its call is gone) — drop leading tool results.
        while non_system and non_system[0].role == "tool":
            non_system.pop(0)

        self._messages = systems + non_system

    def compact_messages(self, keep_count: int = 10) -> int:
        """Compact older messages to reduce token usage without dropping context.

        Instead of pruning (dropping messages entirely), this method preserves
        tool-call context by truncating older messages while keeping recent ones
        intact. This is the ROB-06 "memory pressure" path for long agentic runs
        where input alone exceeds the context window.

        What compaction does:
        - System messages: always kept intact
        - Recent N messages (keep_count): kept intact (subject to the
          per-message size cap — see ``max_kept_msg_chars`` below)
        - Older messages: content truncated to first 200 chars, tool results
          truncated to first 200 chars + "[compacted]" marker. Tool call names
          and args are preserved (they're small and essential for context).

        R06.58 BUGFIX: a single oversized message in the "recent" window
        (e.g., a 200KB ``read_file`` result) used to survive compaction
        untouched because it was within ``keep_count``. Now any single
        message larger than ``max_kept_msg_chars`` (default 8KB) is also
        truncated, regardless of position. This catches the common failure
        mode where the agent reads a large file and then keeps referencing
        it — the read result stays in the recent window, consuming half
        the context by itself.

        Args:
            keep_count: Number of recent non-system messages to keep intact.

        Returns:
            Number of messages that were compacted.
        """
        # R06.58: per-message size cap — 8KB. Tuned to ~2K tokens, so a
        # 128K context can hold ~60 such messages before compaction. The
        # cap only kicks in for genuinely oversized results (full file
        # dumps, large command outputs) — normal tool results stay intact.
        max_kept_msg_chars = 8192

        systems = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]

        compacted_count = 0

        # R06.58: per-message size cap. Apply to ALL non-system messages,
        # including the "kept" recent ones. An oversized read_file result
        # in the last 10 messages used to bypass compaction entirely.
        if max_kept_msg_chars > 0:
            for msg in non_system:
                if (msg.content
                        and len(msg.content) > max_kept_msg_chars
                        and "[truncated]" not in msg.content):
                    # Keep head + tail so the agent retains both the
                    # start (often the most important context) and the
                    # end (recent output). For tool results the tail is
                    # usually where the success/error marker lives.
                    head = max_kept_msg_chars // 2
                    tail = max_kept_msg_chars // 4
                    msg.content = (
                        msg.content[:head]
                        + f"\n...[truncated {len(msg.content) - head - tail} chars]...\n"
                        + msg.content[-tail:]
                    )
                    compacted_count += 1

        if len(non_system) <= keep_count:
            # Even if nothing was compacted by position, the per-message
            # cap above may have truncated oversized messages. Re-assemble
            # and report.
            self._messages = systems + non_system
            return compacted_count

        # Split into "to compact" (older) and "to keep" (recent)
        to_compact = non_system[:-keep_count] if keep_count > 0 else non_system
        to_keep = non_system[-keep_count:] if keep_count > 0 else []

        for msg in to_compact:
            # Compact content — truncate to 200 chars
            if msg.content and len(msg.content) > 200:
                # Don't re-compact something already compacted
                if "[compacted]" not in msg.content:
                    msg.content = msg.content[:200] + "\n[compacted]"
                    compacted_count += 1

            # Compact tool_calls — keep name + args (small), but they're
            # already compact (args are usually short). Don't truncate.
            # The real token cost is in tool results, which are in the
            # "tool" role messages.

            # For tool results (role="tool"), the content is the result
            # which can be very large (file contents, command output).
            # Already handled above by truncating msg.content.

        self._messages = systems + to_compact + to_keep
        return compacted_count

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self):
        return iter(self._messages)

    def __repr__(self) -> str:
        return f"Memory(messages={len(self._messages)}, max={self.config.max_messages})"