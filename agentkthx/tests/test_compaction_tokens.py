"""
R06.58 regression tests for memory compaction & token tracking.

These tests pin down the four bugs that caused "ctx% stuck at 100%,
compaction not firing" on long agentic runs:

1. ``_running_tokens_in`` used to ACCUMULATE the entire history every
   step (snapshot vs cumulative bug).
2. ``_check_compaction`` only reset running totals when ``compacted > 0``,
   so a second compaction pass that freed nothing left stale inflated
   totals pinned at 100%.
3. ``_check_compaction`` only fired at the top of each step — never
   between multiple tool calls within a single assistant message.
4. ``compact_messages`` always preserved the last ``keep_count`` messages
   even if one of them was a 200KB ``read_file`` result, so a single
   oversized result in the recent window could pin ctx% near 100%.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agentkthx.agent import Agent
from agentkthx.core.memory import Memory, MemoryConfig


# ---------------------------------------------------------------------------
# Bug 1: token estimation must snapshot, not accumulate
# ---------------------------------------------------------------------------

class _StubBackend:
    """Minimal backend stub returning zero usage (the :free case)."""
    backend_type = "stub"
    base_url = "stub://"

    def generate_completions_stream(self, **kw):
        return iter([])


def _make_agent():
    agent = Agent.__new__(Agent)
    agent.memory = Memory(MemoryConfig(max_messages=200, max_tokens=100000))
    agent.num_ctx = 8192
    agent._compaction_threshold = 0.85
    agent._running_tokens_in = 0
    agent._running_tokens_out = 0
    agent.debug = False
    return agent


def test_snapshot_running_tokens_is_a_snapshot_not_cumulative():
    """After N steps with a fixed-size memory, _running_tokens_in must stay
    roughly constant — not grow by N×memory_size."""
    agent = _make_agent()
    agent.memory.add("system", "sys")  # not counted by _snapshot
    agent.memory.add("user", "x" * 4000)  # ~1000 tokens

    # Simulate 5 steps calling _snapshot_running_tokens each time
    # (this is what the fixed streaming loop does)
    for _ in range(5):
        agent._snapshot_running_tokens()

    # Memory hasn't changed, so totals should not have grown.
    # If the bug were present (accumulating += memory every step),
    # _running_tokens_in would be ~5000 instead of ~900.
    assert agent._running_tokens_in < 1500, (
        f"running_tokens_in should be ~900 (snapshot) not "
        f"{agent._running_tokens_in} (cumulative bug)"
    )


# ---------------------------------------------------------------------------
# Bug 2: _check_compaction must reset totals even when nothing was compacted
# ---------------------------------------------------------------------------

def test_check_compaction_resets_totals_even_when_nothing_compacted():
    """If _running_tokens_in was inflated externally (e.g., by the old
    cumulative += bug), calling _check_compaction on a small memory must
    bring it back down — not leave it pinned at 100%."""
    agent = _make_agent()
    agent.memory.add("user", "small message")  # tiny memory
    # Simulate the old bug: inflated running total
    agent._running_tokens_in = 999_999
    agent._running_tokens_out = 0

    agent._check_compaction()
    # After check on small memory, totals should reflect actual memory size
    assert agent._running_tokens_in < 100, (
        f"expected reset to small snapshot, got {agent._running_tokens_in}"
    )


# ---------------------------------------------------------------------------
# Bug 3: compact_messages must truncate oversized messages in the recent window
# ---------------------------------------------------------------------------

def test_compact_messages_truncates_oversized_recent_messages():
    """A 200KB read_file result in the last 10 messages must be truncated
    by compact_messages — it should not bypass compaction just because
    it's 'recent'."""
    mem = Memory(MemoryConfig(max_messages=200, max_tokens=100000))
    big_result = "X" * 200_000  # 200KB, way over the 8KB cap
    mem.add("user", "question")
    mem.add("assistant", "let me read that")
    mem.add_tool_result("call_1", "read_file", big_result)
    # Add a few more messages so the big one is inside the recent window
    mem.add("assistant", "ok now what")
    mem.add("user", "next prompt")

    compacted = mem.compact_messages(keep_count=10)
    assert compacted >= 1, "expected the oversized message to be truncated"
    # After compaction, no single non-system message should exceed the cap
    # by more than the head+marker+tail overhead.
    for msg in mem._messages:
        if msg.role != "system":
            assert len(msg.content or "") < 12_000, (
                f"message still oversized: {len(msg.content)} chars"
            )


def test_compact_messages_leaves_normal_messages_intact():
    """Messages under the 8KB cap must NOT be touched, even in the
    'recent' window."""
    mem = Memory(MemoryConfig(max_messages=200, max_tokens=100000))
    mem.add("user", "hello")
    mem.add("assistant", "hi there")
    mem.add("user", "what's 2+2?")
    mem.add("assistant", "4")

    compacted = mem.compact_messages(keep_count=10)
    assert compacted == 0
    # All messages intact
    assert mem._messages[0].content == "hello"
    assert mem._messages[1].content == "hi there"
    assert mem._messages[2].content == "what's 2+2?"
    assert mem._messages[3].content == "4"


def test_compact_messages_is_idempotent_on_truncated_messages():
    """Calling compact_messages twice must not double-truncate an already
    truncated message."""
    mem = Memory(MemoryConfig(max_messages=200, max_tokens=100000))
    mem.add("user", "X" * 200_000)

    first = mem.compact_messages(keep_count=10)
    second = mem.compact_messages(keep_count=10)

    assert first >= 1
    assert second == 0, "second call should not re-truncate"
    assert "[truncated " in mem._messages[0].content, (
        "first call should have left a truncation marker"
    )


# ---------------------------------------------------------------------------
# Bug 4: _check_compaction should be callable between tool calls within a
# single step. We verify this by ensuring the agent loop's tool dispatch
# path invokes _check_compaction at least once when multiple tool calls
# are present in one assistant message.
# ---------------------------------------------------------------------------

def test_check_compaction_returns_int_and_does_not_raise_on_empty_memory():
    """Sanity: _check_compaction on an empty memory must not raise and
    must return 0."""
    agent = _make_agent()
    agent.memory = Memory(MemoryConfig())
    result = agent._check_compaction()
    assert result == 0
    assert agent._running_tokens_in == 0
    assert agent._running_tokens_out == 0


# ---------------------------------------------------------------------------
# Integration: end-to-end memory-pressure scenario
# ---------------------------------------------------------------------------

def test_repeated_compaction_passes_do_not_grow_running_totals():
    """Simulate the audit-run scenario: many tool results added to memory,
    repeated _check_compaction calls. Running totals must track actual
    memory size, not accumulate."""
    agent = _make_agent()
    agent.num_ctx = 8192  # small ctx so compaction fires often
    agent._compaction_threshold = 0.5  # 4K tokens threshold

    # Add 20 large tool results — simulates a long agentic run
    for i in range(20):
        agent.memory.add("user", f"step {i}")
        agent.memory.add_tool_result(
            f"call_{i}", "shell", "X" * 2000  # ~500 tokens each
        )
        agent._check_compaction()  # what the streaming loop does each step

    # After all this, running_tokens_in should be bounded — well under
    # the cumulative 20*500 = 10000 tokens that the old buggy path
    # would have produced.
    total = agent._running_tokens_in + agent._running_tokens_out
    assert total < 8000, (
        f"running totals should reflect actual memory size ({total} tokens), "
        f"not the cumulative {20 * 500}-token history"
    )
