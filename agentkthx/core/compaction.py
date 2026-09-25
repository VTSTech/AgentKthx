"""Compaction subsystem — memory compaction + running-token tracking.

R07.00 Phase 7 extraction (from ``agent.py``). Pure move, no logic change.

Consolidates the compaction logic that was previously scattered across
``agent.py`` (3 sites) into one addressable module:

- ``_check_compaction()`` — the preventive compaction path. Runs before
  each generate call; compares estimated memory tokens against
  ``num_ctx * _compaction_threshold`` and compacts when over.
- ``_snapshot_running_tokens()`` — recomputes ``_running_tokens_in/out``
  from current memory. Single source of truth for the footer's ctx%
  display (R06.58 bugfix: snapshot, never cumulative).
- ``_update_running_tokens()`` — the per-step token-tracking block that
  was inline in ``_run_core_streaming`` (R06.58: snapshot semantics for
  input, estimate for output).

Related logic that intentionally stays elsewhere:
- ``Memory.compact_messages()`` (``core/memory.py``) — the actual
  truncation primitive. This mixin decides *when*; memory decides *how*.
- The reactive context-length-400 recovery inside
  ``_generate_with_retry()`` (``agent.py``) — that path compacts as an
  error-recovery measure, not preventively.

Host contract (attributes the mixin expects on ``self``):
- ``self.memory`` — iterable of message objects with ``.content`` and
  optional ``.tool_calls`` (the ``Memory`` class satisfies this).
- ``self.num_ctx`` — context window size in tokens (int or None).
- ``self._compaction_threshold`` — float in [0.0, 1.0); >= 1.0 disables.
- ``self._running_tokens_in`` / ``self._running_tokens_out`` — ints.
"""

import json


class CompactionMixin:
    """Mixin providing compaction + running-token tracking for Agent.

    R07.00 Phase 7: extracted verbatim from ``agent.py`` (R06.58 state).
    All call sites (``self._check_compaction()``,
    ``self._snapshot_running_tokens()``, ``self._update_running_tokens()``)
    are unchanged — the Agent class simply inherits them now.
    """

    def _check_compaction(self) -> int:
        """Check if memory needs compaction and compact if over threshold.

        Estimates the total token count of all messages in memory and
        compares against ``num_ctx * _compaction_threshold``. If over
        threshold, calls ``memory.compact_messages()`` to truncate older
        messages while keeping recent ones intact.

        ROB-06: This is the preventive compaction path — it runs before
        each generate call to avoid context-length 400s on long agentic
        runs. The reactive path (in _iter_sse_lines) still handles the
        case where compaction wasn't enough.

        Returns:
            Number of messages that were compacted (0 if none needed).
        """
        if getattr(self, "_compaction_threshold", 0.85) >= 1.0:
            return 0  # compaction disabled

        # Estimate total tokens: ~4 chars per token (rough heuristic)
        total_chars = 0
        for msg in self.memory:
            content = getattr(msg, 'content', '') or ''
            total_chars += len(content)
            # Also count tool_calls (small but present)
            tc = getattr(msg, 'tool_calls', None)
            if tc:
                total_chars += len(json.dumps(tc, ensure_ascii=False))
        estimated_tokens = total_chars // 4

        # Get context limit
        ctx = self.num_ctx or 8192
        threshold_tokens = int(ctx * getattr(self, "_compaction_threshold", 0.85))

        if estimated_tokens <= threshold_tokens:
            # R06.58 BUGFIX: even when no compaction is needed, the
            # running token totals may be stale (e.g., the fallback
            # estimation path used to accumulate the whole history every
            # step). Re-snapshot from current memory so the footer's ctx%
            # reflects reality, not a stale cumulative total.
            self._snapshot_running_tokens()
            return 0  # under threshold, no compaction needed

        # Over threshold — compact older messages
        # Keep the most recent 10 messages intact
        keep_count = 10
        compacted = self.memory.compact_messages(keep_count=keep_count)

        if compacted > 0:
            # Recount post-compaction size for an accurate log line.
            post_chars = 0
            for msg in self.memory:
                c = getattr(msg, 'content', '') or ''
                post_chars += len(c)
                tc = getattr(msg, 'tool_calls', None)
                if tc:
                    post_chars += len(json.dumps(tc, ensure_ascii=False))
            post_tokens = post_chars // 4
            print(f"  [Compaction] {compacted} messages compacted "
                  f"(~{estimated_tokens // 1000}K → "
                  f"~{post_tokens // 1000}K tokens, "
                  f"threshold {threshold_tokens // 1000}K of "
                  f"{ctx // 1000}K context)")

        # R06.58 BUGFIX: ALWAYS re-snapshot running totals from the
        # post-compaction memory state, regardless of whether compaction
        # actually truncated anything. Previously the reset only ran when
        # ``compacted > 0``, which meant that if compaction ran once and
        # truncated everything, the next call would return 0 (nothing to
        # truncate), and the stale inflated running totals would persist
        # — keeping ctx% pinned at 100% forever.
        self._snapshot_running_tokens()

        return compacted

    def _snapshot_running_tokens(self) -> None:
        """Recompute _running_tokens_in/out from the current memory state.

        R06.58: This is the single source of truth for the footer's ctx%
        display. Called after every step's generate (so the snapshot
        reflects the just-added assistant message + tool results), and
        after every compaction (so the snapshot reflects the truncated
        state).

        The split is ~90% input / ~10% output because most of the
        in-memory context is input (tool results, system prompt,
        conversation history). The just-generated output is small
        compared to the accumulated input.
        """
        total_chars = 0
        for msg in self.memory:
            content = getattr(msg, 'content', '') or ''
            total_chars += len(content)
            tc = getattr(msg, 'tool_calls', None)
            if tc:
                total_chars += len(json.dumps(tc, ensure_ascii=False))
        total_tokens = total_chars // 4
        self._running_tokens_in = int(total_tokens * 0.9)
        self._running_tokens_out = int(total_tokens * 0.1)

    def _update_running_tokens(self, content: str, native_tool_calls: list, tokens: int) -> None:
        """Per-step token tracking (was inline in ``_run_core_streaming``).

        R07.00 Phase 7: moved verbatim from the streaming loop body. The
        footer computes ctx% from
        ``(_running_tokens_in + _running_tokens_out) / num_ctx``, so these
        MUST reflect the CURRENT memory size, not a cumulative total.

        R06.58 BUGFIX: previously the fallback path did
        ``self._running_tokens_in += _est_in`` every step, where
        ``_est_in`` was the size of the ENTIRE history. After N steps
        the running total was N× the actual memory size, so ctx%
        climbed to 100% and stayed there forever (even after a
        successful compaction reset, the very next step re-added the
        whole history again). This made users report "compaction not
        firing when ctx is 100%" — the display was lying, not the
        compaction logic.

        Fix: always treat _running_tokens_in/out as a SNAPSHOT of the
        current memory state. If the provider returns real usage we
        still snapshot from memory (provider usage is per-request, so
        it already reflects the post-compaction state for input).

        Args:
            content: The assistant message content just generated.
            native_tool_calls: Native tool calls from the response (used
                for the output-half estimate).
            tokens: Provider-reported usage (prompt+completion), if any.
        """
        _est_in_chars = 0
        for msg in self.memory:
            c = getattr(msg, 'content', '') or ''
            _est_in_chars += len(c)
            tc = getattr(msg, 'tool_calls', None)
            if tc:
                _est_in_chars += len(json.dumps(tc, ensure_ascii=False))
        _est_out_chars = len(content) + sum(
            len(json.dumps(tc, ensure_ascii=False))
            for tc in native_tool_calls
        )
        # If the provider returned real usage, prefer it for the OUTPUT
        # half (it's accurate for this turn's generated tokens). For
        # INPUT we always snapshot from memory — provider usage on
        # streaming :free models is often 0 or unreliable, and memory
        # size is what actually matters for the next compaction check.
        self._running_tokens_in = _est_in_chars // 4
        if tokens and tokens > 0:
            # Provider usage is prompt+completion combined; use the
            # completion portion if we can split it, else fall back to
            # the estimate. We add the new output tokens ON TOP of the
            # input snapshot so the footer reflects both halves of
            # the current in-memory state.
            self._running_tokens_out = _est_out_chars // 4
        else:
            self._running_tokens_out = _est_out_chars // 4
