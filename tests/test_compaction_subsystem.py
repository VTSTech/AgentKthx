"""R07.00 Phase 7 — CompactionMixin extraction tests.

Verifies the structural move of the compaction subsystem from
``agent.py`` to ``agentkthx/core/compaction.py``:

1. The mixin exists at the new import path and provides all 3 methods.
2. ``Agent`` inherits from ``CompactionMixin`` (MRO wiring).
3. ``Agent`` no longer *defines* the methods itself (they come from the
   mixin — proves the move, not a copy).
4. The mixin works on a minimal host class (decoupled from Agent).
5. ``_update_running_tokens`` (the block that was inline in
   ``_run_core_streaming``) keeps its snapshot semantics on both the
   provider-usage and no-usage branches.
"""

import json

from agentkthx.core.compaction import CompactionMixin


class _Msg:
    """Minimal message stand-in matching the host contract."""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _MinimalHost(CompactionMixin):
    """Smallest possible host satisfying the mixin's attribute contract."""

    def __init__(self, messages, num_ctx=8192, threshold=0.85):
        self._messages = list(messages)
        self.num_ctx = num_ctx
        self._compaction_threshold = threshold
        self._running_tokens_in = 0
        self._running_tokens_out = 0

    def __iter__(self):
        return iter(self._messages)

    @property
    def memory(self):
        return self  # iterable + attribute access on messages


def test_mixin_provides_all_three_methods():
    for name in ("_check_compaction", "_snapshot_running_tokens",
                 "_update_running_tokens"):
        assert callable(getattr(CompactionMixin, name, None)), (
            f"CompactionMixin is missing {name}")
        host = _MinimalHost([])
        assert callable(getattr(host, name)), (
            f"host instance cannot call {name}")


def test_agent_inherits_from_mixin():
    from agentkthx.agent import Agent
    assert issubclass(Agent, CompactionMixin), (
        "Agent must inherit CompactionMixin after the Phase 7 extraction")


def test_agent_does_not_redefine_moved_methods():
    """The methods must be MOVED to the mixin, not copied.

    If Agent still defines them, future compaction fixes would land in
    agent.py and silently shadow the mixin — reintroducing the exact
    divergence this phase eliminates.
    """
    from agentkthx.agent import Agent
    for name in ("_check_compaction", "_snapshot_running_tokens",
                 "_update_running_tokens"):
        defined_on_agent = name in vars(Agent)
        assert not defined_on_agent, (
            f"Agent.__dict__ still contains {name} — extraction left a "
            f"shadowing copy behind")


def test_mixin_works_on_minimal_host_decoupled_from_agent():
    """The mixin must not require Agent — only its documented contract."""
    host = _MinimalHost([_Msg("x" * 400)], num_ctx=1000)
    host._snapshot_running_tokens()
    # 400 chars // 4 = 100 tokens; split 90/10
    assert host._running_tokens_in == 90
    assert host._running_tokens_out == 10
    # _check_compaction runs end-to-end on the minimal host (under
    # threshold → 0 compacted, snapshot refreshed)
    assert host._check_compaction() == 0


def test_update_running_tokens_snapshot_semantics_both_branches():
    host = _MinimalHost([_Msg("y" * 800)])
    # Branch 1: provider returned real usage
    host._update_running_tokens("hello world", [], tokens=12345)
    assert host._running_tokens_in == 200  # 800 chars // 4 — snapshot
    # Branch 2: no usage (0)
    host._running_tokens_in = 999_999  # stale inflated total
    host._update_running_tokens("hello world", [], tokens=0)
    assert host._running_tokens_in == 200  # re-snapshot, not cumulative
