"""
⚛️ AgentKthx — Memory Management
Sliding window memory with optional summarization.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class MemoryConfig:
    """Configuration for agent memory.

    ``max_tokens`` (ROB-08, R07.06): estimated-token budget for the
    conversation window — the second pruning tier, evaluated after the
    ``max_messages`` count tier on every ``add()``. Estimated via the
    audit's ``len(content) // 4`` heuristic across NON-system messages
    (the system prompt is fixed overhead pruning can never reclaim);
    when exceeded, the window slides (oldest first, pairing-safe) down
    to ``max_tokens * summarization_threshold`` estimated tokens.

    Defaults to ``0`` (tier disabled). The historical default of 4096 was
    NEVER enforced; turning it on unconditionally would prune tool-heavy
    histories to ~2 results (tool output is capped at 8KB by result
    sanitization ≈ 2K estimated tokens each), degrading agent quality —
    so the tier ships opt-in. Set it explicitly to enable (e.g.
    ``MemoryConfig(max_messages=200, max_tokens=100000)``).
    """

    max_messages: int = 50
    max_tokens: int = 0
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
                # ROB-40 (R07.21 CLOSED): preserve Gemini's thought_signature
                # on each tool_call. The signature lives at
                # extra_content.google.thought_signature in the OpenAI-compat
                # shape. Gemini thinking models require it to be present on
                # the assistant message's tool_calls when the conversation
                # history is sent back, or the API 400s on the second turn.
                # The field is only present on Gemini responses; other
                # backends don't emit it (thought_sig stays "" → skipped).
                _thought_sig = tc.get("thought_signature") or ""
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
                        },
                    }
                    # ROB-40: re-attach thought_signature if present
                    if _thought_sig:
                        openai_tc["extra_content"] = {"google": {"thought_signature": _thought_sig}}
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
                        },
                    }
                    # ROB-40: re-attach thought_signature if present
                    if _thought_sig:
                        openai_tc["extra_content"] = {"google": {"thought_signature": _thought_sig}}
                    openai_tool_calls.append(openai_tc)
            result["tool_calls"] = openai_tool_calls
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.name:
            result["name"] = self.name
        return result


def _estimate_tokens(text: str) -> int:
    """
    Rough token estimate for the ROB-08 (R07.06) token-based pruning tier.

    Uses the audit's ~4-chars-per-token heuristic. This deliberately errs
    on the high side for dense tool output (JSON, base64, minified code
    run closer to 2-3 chars/token) — an over-estimate prunes earlier,
    which is the safe direction for context-window overflow.
    """
    return len(text) // 4 if text else 0


class Memory:
    """
    Conversation memory with sliding window management.

    Features:
    - Configurable message limit
    - Token-based pruning (ROB-08, R07.06 — opt-in via ``max_tokens``)
    - System message preservation
    - Recent message retention
    """

    def __init__(self, config: MemoryConfig | None = None):
        self.config = config or MemoryConfig()
        self._messages: list[Message] = []
        self._system_prompt: str | None = None
        # PERF-01 (R07.14): sanitize_history is idempotent, so its
        # result only needs recomputing after a mutation. Every mutating
        # path (add/clear/compact_messages/sanitize/load) funnels through
        # ``_invalidate_caches()``; get_messages() skips the two-pass
        # repair while the state is clean.
        self._sanitize_dirty: bool = True
        # PERF-02 (R07.14): cached size estimate for the compaction
        # heuristic (len(content) + len(json.dumps(tool_calls)) per
        # message). ``None`` means stale; recomputed lazily by
        # ``estimated_chars()`` and invalidated by ``_invalidate_caches()``.
        self._chars_cache: int | None = None

    def _invalidate_caches(self) -> None:
        """Mark the sanitize + size caches stale (PERF-01/PERF-02, R07.14).

        Called by every mutating path: ``add`` (the sole entry point for
        append-style changes, covering ``add_tool_call``/``add_tool_result``),
        ``clear``, ``compact_messages`` (which ALSO truncates ``msg.content``
        in place — a structural-only check would miss that), and
        ``PersistentMemory.load`` (rebuilds ``_messages`` from the DB).
        """
        self._sanitize_dirty = True
        self._chars_cache = None

    def add(self, role: str, content: str, **kwargs) -> None:
        """Add a message to memory."""
        msg = Message(role=role, content=content, **kwargs)

        # Track system prompt separately
        if role == "system":
            self._system_prompt = content
            # Remove any existing system messages
            self._messages = [m for m in self._messages if m.role != "system"]

        self._messages.append(msg)
        self._invalidate_caches()
        self._prune_if_needed()

    def add_tool_call(self, role: str, content: str, tool_calls: list[dict]) -> None:
        """Add a message with tool calls."""
        self.add(role, content, tool_calls=tool_calls)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result message."""
        self.add("tool", content, tool_call_id=tool_call_id, name=name)

    def estimated_chars(self) -> int:
        """Cached size estimate for the compaction heuristic (PERF-02).

        Sums ``len(content)`` plus ``len(json.dumps(tool_calls))`` over all
        messages — the exact quantity ``CompactionMixin``'s threshold
        check and running-token snapshot previously recomputed with a full
        scan + per-message ``json.dumps`` on EVERY agentic step (three
        scans per step across ``_check_compaction``,
        ``_snapshot_running_tokens`` and ``_update_running_tokens``).

        The cache is invalidated by ``_invalidate_caches()`` on every
        mutation and recomputed lazily here, so the estimate is computed
        once per mutation instead of once per consumer per step.
        """
        if self._chars_cache is None:
            total = 0
            for m in self._messages:
                total += len(m.content or "")
                tc = getattr(m, "tool_calls", None)
                if tc:
                    total += len(json.dumps(tc, ensure_ascii=False))
            self._chars_cache = total
        return self._chars_cache

    def get_messages(self) -> list[dict]:
        """Get all messages as dictionaries."""
        # R06.52: repair tool-call pairing before handing history to the
        # backend. Orphan tool results and dangling tool calls both produce
        # illegal ChatCompletions sequences (HTTP 400 on OpenRouter,
        # code 1214 on ZAI). Sanitizing is idempotent.
        # PERF-01 (R07.14): the two-pass repair is O(n × tool_calls);
        # on a 25-step agentic loop that used to re-run on every step even
        # when nothing had changed. The state produced here is already
        # sanitized, so only re-run after a mutation flipped the flag.
        if self._sanitize_dirty:
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
            m.tool_call_id for m in cleaned if m.role == "tool" and m.tool_call_id
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
                    final.append(
                        Message(
                            role="tool",
                            content=(
                                "Error: no result was recorded for this tool "
                                "call (the run was interrupted before it "
                                "completed). Continue, but do not retry it "
                                "blindly."
                            ),
                            tool_call_id=cid,
                            name=tc.get("name"),
                        )
                    )
                    answered.add(cid)
            i = j

        self._messages = final
        # The state is now repaired: mark it clean (PERF-01) and drop the
        # size cache — dropped orphans / inserted placeholders change the
        # total (PERF-02).
        self._sanitize_dirty = False
        self._chars_cache = None

    def clear(self) -> None:
        """Clear all messages (except system prompt if configured)."""
        if self.config.keep_system and self._system_prompt:
            self._messages = []
        else:
            self._messages = []
            self._system_prompt = None
        self._invalidate_caches()

    def _prune_if_needed(self) -> None:
        """
        Prune messages if limits exceeded (R06.52, pairing-safe).

        Two tiers — either may fire; the count tier runs first:

        1. Message-count tier — the window *slides* down to the
           summarization threshold when ``max_messages`` is exceeded:

               keep_count = max(1, int(max_messages * summarization_threshold))

           e.g. 50 messages @ 0.8 → slide to 40. This reclaims headroom so
           the next few adds don't re-trigger pruning, and keeps recent
           tool-call pairs intact.

        2. Token tier (ROB-08, R07.06) — when ``max_tokens > 0`` and the
           estimated tokens of the non-system messages
           (``len(content) // 4``) exceed the budget, the window slides the
           same way down to ``max_tokens * summarization_threshold``
           estimated tokens. Catches conversations that stay under the
           message-count window but overflow the context window with large
           tool results (e.g. 10 × 50KB results = 500K chars ≈ 125K
           estimated tokens, far past a 32K context).

        Tool results whose announcing assistant message fell out of the
        window are dropped from the head (they would otherwise be orphaned
        and make the API sequence illegal).
        """
        # ---- tier 1: message-count window ----
        if len(self._messages) > self.config.max_messages:
            keep_count = max(1, int(self.config.max_messages * self.config.summarization_threshold))

            systems = [m for m in self._messages if m.role == "system"]
            non_system = [m for m in self._messages if m.role != "system"]

            # R07.23: preserve the first user message. OpenAI + ZAI require
            # the first non-system message to be role=user; an
            # assistant(message with tool_calls) as the first non-system
            # message is invalid and triggers HTTP 400 "messages parameter
            # is illegal" (ZAI code 1214). The previous pairing-safe head
            # trim only dropped leading tool results — it missed the case
            # where the sliding window drops the original user prompt and
            # leaves an assistant(tool_calls) exposed at the head. We now
            # pin the first user message before trimming and re-prepend it
            # after, so the API sequence always starts with system → user.
            first_user = None
            if non_system and non_system[0].role == "user":
                first_user = non_system[0]
                non_system = non_system[1:]

            excess = len(non_system) - keep_count
            if excess > 0:
                non_system = non_system[excess:]

            # If we pinned the first user, account for it in the budget:
            # re-prepend it even if it pushes us 1 over keep_count. The
            # alternative (dropping it to stay at keep_count) recreates the
            # bug — a 1-message overshoot is always preferable to an
            # illegal API sequence.
            if first_user is not None:
                non_system = [first_user] + non_system

            # Pairing-safe head trim: a kept window must not START with a
            # tool result (its call is gone) — drop leading tool results.
            # Also handle the edge case where the first_user was prepended
            # but the NEXT message is an orphaned tool result (its
            # announcing assistant was dropped by the slide).
            while non_system and non_system[0].role == "tool":
                non_system.pop(0)

            self._messages = systems + non_system

        # ---- tier 2: token budget (ROB-08, R07.06) ----
        # ``max_tokens <= 0`` disables the tier (the default — see
        # MemoryConfig for why the tier ships opt-in).
        if self.config.max_tokens <= 0:
            return

        systems = [m for m in self._messages if m.role == "system"]
        non_system = [m for m in self._messages if m.role != "system"]
        if len(non_system) <= 1:
            return

        estimates = [_estimate_tokens(m.content) for m in non_system]
        total = sum(estimates)
        if total <= self.config.max_tokens:
            return

        # Slide to the summarization threshold (mirrors the count tier's
        # headroom approach so the next few adds don't immediately
        # re-trigger pruning), keeping at least one message.
        target = max(1, int(self.config.max_tokens * self.config.summarization_threshold))

        drop = 0
        acc = total
        while (len(non_system) - drop) > 1 and acc > target:
            acc -= estimates[drop]
            drop += 1
        kept = non_system[drop:]

        # ROB-17 (R07.21 CLOSED): if a single message's token estimate
        # still exceeds the budget after pruning everything else, truncate
        # its content (keeping the tail, which is usually the recent part)
        # so the over-budget window doesn't ship to the backend as-is.
        # The loop above deliberately keeps at least one message, but a
        # single 100K-char pasted file read can still exceed max_tokens —
        # silently defeating the tier exactly when the budget is most
        # exceeded. We now truncate + emit a visible marker so the model
        # sees the truncation and the user can tell from the footer.
        if len(kept) == 1 and acc > target:
            _msg = kept[0]
            _msg_tokens = estimates[drop] if drop < len(estimates) else acc
            # Estimate chars from tokens (4 chars/token heuristic).
            _max_chars = max(100, int(target * 4))
            _orig_len = len(_msg.content)
            if _orig_len > _max_chars:
                _tail = _msg.content[-_max_chars:]
                _msg.content = (
                    f"[...truncated {_orig_len - _max_chars} chars — "
                    f"message exceeded token-tier budget...]\n" + _tail
                )
                # Update the estimates list so the post-trim acc reflects
                # the truncation (cosmetic — the tier has already committed).
                acc = target

        # R07.23: same first-user preservation as the count tier above.
        # The token-tier slide can also drop the original user prompt and
        # leave an assistant(tool_calls) exposed at the head → ZAI 1214.
        # Pin the first user message before the slide, re-prepend after.
        first_user = None
        if kept and kept[0].role == "user":
            first_user = kept[0]
            kept = kept[1:]

        # Pairing-safe head trim, same rule as the count tier: a kept
        # window must not START with a tool result whose call is gone.
        while kept and kept[0].role == "tool":
            kept.pop(0)

        if first_user is not None:
            kept = [first_user] + kept

        self._messages = systems + kept

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
                if (
                    msg.content
                    and len(msg.content) > max_kept_msg_chars
                    and "[truncated]" not in msg.content
                ):
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

        # The per-message cap truncates ``msg.content`` IN PLACE, so the
        # caches must drop regardless of which branch returns (PERF-02 —
        # a structural-only invalidation would miss the shrinkage).
        self._invalidate_caches()

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
