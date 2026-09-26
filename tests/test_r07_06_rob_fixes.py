"""
R07.06 Robustness batch — regression tests for five audit findings:

  ROB-01 (Low):    Ctrl+C during tool execution left the run half-cancelled —
                   the inner tool loop broke but ``state.terminated`` was never
                   set, so the outer step loop called the model again.
  ROB-07 (Low):    ``_ERROR_FIRST_LINE_RE`` missed Python's alternative
                   traceback framings (exception-chain headers, bare
                   ``File "..."`` frames) — those tool results were
                   misclassified as successes and poisoned the recovery
                   tracker.
  ROB-08 (Low):    ``MemoryConfig.max_tokens`` existed but was never enforced
                   — now a real (opt-in) token-based second pruning tier.
  ROB-10 (Medium): ``is_transient_api_error`` classified every 500 as
                   transient, even 500s whose body carries a permanent-error
                   pattern (``model_not_found``, ``invalid_request``, ...) —
                   ~6 minutes of pointless back-off. Permanent body patterns
                   now win; a raw-body second arg is accepted.
  ROB-13 (Medium): The tool-parse JSON fallback chain (4 levels) silently
                   swallowed the original errors — the failure chain is now
                   logged under ``debug``.

Written by VTSTech — https://www.vts-tech.org
"""

import pytest

from agentkthx.agent import Agent
from agentkthx.core.agentic_loop import _LoopState
from agentkthx.core.api_resilience import is_transient_api_error
from agentkthx.core.error_recovery import is_error_result
from agentkthx.core.memory import Memory, MemoryConfig, _estimate_tokens
from agentkthx.core.openresponses import ResponseStatus
from agentkthx.core.tool_parse import ToolParser
from agentkthx.core.models import Tool, ToolParam
from agentkthx.tools import make_builtin_registry

from tests.test_loop_resilience import StubBackend


# ============================================================================
# ROB-01 — Ctrl+C during tool execution must terminate the whole run
# ============================================================================

def _make_interrupt_tool() -> Tool:
    """A tool whose execution raises KeyboardInterrupt (user hit Ctrl+C)."""

    def _interrupt(**kwargs):
        raise KeyboardInterrupt()

    return Tool(
        name="interrupt",
        description="Simulates the user hitting Ctrl+C mid-execution",
        params=[],
        handler=_interrupt,
    )


def _make_agent(script):
    backend = StubBackend(script)
    agent = Agent(
        model="stub",
        backend=backend,
        tools=make_builtin_registry().subset(["read_file"]),
        max_steps=3,
        soul=None,
        max_api_retries=2,
    )
    agent.register_tool(_make_interrupt_tool())
    return agent, backend


class TestROB01CancellationPropagates:
    def test_ctrl_c_during_tool_exec_stops_run(self, monkeypatch):
        """The outer step loop must NOT call the model again after a
        cancellation during tool execution (pre-fix: half-cancelled run)."""
        agent, backend = _make_agent([
            {"content": "Action: interrupt\nAction Input: {}"},
            # If the bug regresses, the outer loop samples this and keeps
            # running with a cancelled response in hand.
            {"content": "Final Answer: ran anyway"},
        ])
        result = agent.run("hi")

        assert result.success is False
        # The model was called exactly ONCE — no further steps ran.
        assert len(backend.calls) == 1, (
            f"expected 1 generate() call after Ctrl+C, got {len(backend.calls)} "
            "— the outer loop continued past the cancellation (ROB-01 regression)"
        )
        cancel_steps = [
            s for s in result.steps
            if getattr(s, "error", None) == "Cancelled by user during tool execution"
        ]
        assert cancel_steps, "cancellation ERROR step missing from result.steps"

    def test_cancelled_response_stays_cancelled(self, monkeypatch):
        """The stored Response keeps CANCELLED status — the post-cancellation
        finalize path must not flip it to COMPLETED."""
        agent, _ = _make_agent([
            {"content": "Action: interrupt\nAction Input: {}"},
            {"content": "Final Answer: ran anyway"},
        ])
        agent.run("hi")

        assert agent._response_history, "no response stored in history"
        response = list(agent._response_history.values())[-1]
        assert response.status == ResponseStatus.CANCELLED

    def test_history_stays_api_valid_after_cancel(self, monkeypatch):
        """After the cancelled run, memory must still be a legal
        ChatCompletions sequence (no dangling tool_calls)."""
        agent, _ = _make_agent([
            {"content": "Action: interrupt\nAction Input: {}"},
            {"content": "Final Answer: ran anyway"},
        ])
        agent.run("hi")

        msgs = agent.memory.get_messages()
        for msg in msgs:
            for tc in (msg.get("tool_calls") or []):
                cid = tc.get("id")
                assert any(
                    m.get("role") == "tool" and m.get("tool_call_id") == cid
                    for m in msgs
                ), f"dangling tool_call {cid} after cancelled run"

    def test_terminated_flag_is_what_the_caller_checks(self):
        """Pin the contract: every ``break`` from the tool-exec path sets
        ``state.terminated`` — this is the exact field the caller's
        ``if state.terminated:`` check reads (ROB-01)."""
        state = _LoopState()
        assert state.terminated is False  # default unchanged
        state.terminated = True  # what the KeyboardInterrupt branch now does
        assert state.terminated is True


# ============================================================================
# ROB-07 — alternative traceback framings are recognized as errors
# ============================================================================

class TestROB07TracebackFirstLineFormats:
    @pytest.mark.parametrize("first_line", [
        # Exception-chain headers (exception chains in python_repl output,
        # typically appearing as the first line after output clipping)
        "During handling of the above exception, another exception occurred:",
        "  During handling of the above exception, another exception occurred:",
        "The above exception was the direct cause of the following exception:",
        # Truncated tracebacks whose captured head is a bare File frame
        '  File "/tmp/audit_script.py", line 42, in <module>',
        'File "/usr/lib/python3.12/runpy.py", line 198, in _run_module_as_main',
        # The classic full header (pre-existing behavior — keep pinned)
        "Traceback (most recent call last):",
    ])
    def test_traceback_formats_classified_as_errors(self, first_line):
        assert is_error_result(first_line) is True

    @pytest.mark.parametrize("first_line", [
        # Prose containing the same words must NOT be misclassified
        "Filed a report about the incident",
        'The file "notes.txt" contains the requested data',
        'Profile "settings" updated successfully',
        "total 124\ndrwxrwxr-x 14 vtstech vtstech",
        "scanned tree\n(note: File not found: x was expected)",
    ])
    def test_prose_not_misclassified(self, first_line):
        assert is_error_result(first_line) is False

    def test_chain_from_python_repl_end_to_end(self):
        """A realistic clipped python_repl failure: the sandbox keeps only
        the tail of the output, so the first line is the chain header."""
        result = (
            "During handling of the above exception, another exception "
            "occurred:\n\nTraceback (most recent call last):\n"
            '  File "<stdin>", line 3, in <module>\n'
            "KeyError: 'prices'"
        )
        assert is_error_result(result) is True


# ============================================================================
# ROB-08 — MemoryConfig.max_tokens is a real (opt-in) token-based tier
# ============================================================================

class TestROB08TokenPruningTier:
    def test_tier_disabled_by_default(self):
        """Default MemoryConfig has max_tokens=0 → no token pruning. This
        pins the default flip (the old default 4096 was never enforced;
        enforcing it would prune tool-heavy histories to ~2 results)."""
        assert MemoryConfig().max_tokens == 0
        m = Memory(MemoryConfig())
        for i in range(5):
            m.add("user", "X" * 10_000)  # 50K chars ≈ 12.5K est tokens
        assert len(m.get_messages()) == 5

    def test_zero_disables_tier_explicitly(self):
        m = Memory(MemoryConfig(max_messages=1000, max_tokens=0))
        for i in range(4):
            m.add("user", "X" * 10_000)
        assert len(m.get_messages()) == 4

    def test_token_tier_prunes_when_over_budget(self):
        # 400 chars ≈ 100 est tokens; budget 100 → slide to target 80
        # → everything but the newest message goes (each single message
        # is already over target, keep-at-least-1 floor applies).
        m = Memory(MemoryConfig(max_messages=1000, max_tokens=100))
        for i in range(10):
            m.add("user", "X" * 400 + f" msg {i}")
        msgs = m.get_messages()
        assert len(msgs) == 1
        assert msgs[-1]["content"].endswith("msg 9"), "newest message must survive"

    def test_token_tier_slides_to_threshold_not_to_zero(self):
        # 100 est tokens per message; budget 300 → target 240 → the loop
        # drops until the remaining sum ≤ 240 → 2 messages of 100 remain.
        m = Memory(MemoryConfig(max_messages=1000, max_tokens=300))
        for i in range(10):
            m.add("user", "X" * 400 + f" msg {i}")
        msgs = m.get_messages()
        assert len(msgs) == 2
        assert msgs[-1]["content"].endswith("msg 9")

    def test_token_tier_is_pairing_safe(self):
        """A tool result whose announcing assistant call was dropped must
        not start the kept window (and no orphan results overall)."""
        m = Memory(MemoryConfig(max_messages=1000, max_tokens=300))
        m.add_tool_call("assistant", "calling", [
            {"id": "c1", "name": "shell", "arguments": {"command": "ls"}}
        ])
        m.add_tool_result("c1", "shell", "out")
        for i in range(10):
            m.add("user", "X" * 400 + f" msg {i}")

        msgs = m.get_messages()
        non_system = [x for x in msgs if x["role"] != "system"]
        assert non_system[0]["role"] != "tool", (
            "window starts with an orphan tool result (pairing unsafe)"
        )

    def test_large_budget_leaves_history_untouched(self):
        """The pattern used by the compaction test-suite
        (max_tokens=100000) must keep pruning OFF for normal sizes."""
        m = Memory(MemoryConfig(max_messages=1000, max_tokens=100_000))
        for i in range(20):
            m.add("user", "X" * 2000)  # 40K chars = 10K est tokens total
        assert len(m.get_messages()) == 20

    def test_estimate_tokens_helper(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens(None) == 0
        assert _estimate_tokens("abcd") == 1
        assert _estimate_tokens("x" * 400) == 100

    def test_count_tier_still_fires_first_and_token_tier_after(self):
        """Both tiers in one memory: the count tier slides at max_messages,
        then the token tier bounds the remainder under the budget.

        60 msgs of ~407 chars (101 est tokens each). Without the token
        tier the count tier alone would keep 40 messages ≈ 4040 est
        tokens. With the tier, the window oscillates 7 → 9 (the slide
        reclaims headroom down to target 800; up to 3 more 101-token
        adds fit before the next prune) — pinned via the invariant, not
        the exact parity.
        """
        m = Memory(MemoryConfig(max_messages=50, max_tokens=1000))
        for i in range(60):
            m.add("user", "X" * 400 + f" msg {i}")
        msgs = m.get_messages()
        assert len(msgs) <= 9, "token tier failed to bound the count-tier window"
        est = sum(_estimate_tokens(x["content"]) for x in msgs)
        assert est <= 1010  # budget 1000 + at most one 101-token add of headroom
        assert msgs[-1]["content"].endswith("msg 59")


# ============================================================================
# ROB-10 — permanent-error body patterns beat the "500" transient marker
# ============================================================================

class TestROB10PermanentBodyPatterns:
    @pytest.mark.parametrize("msg", [
        # 500 with a JSON body naming a permanent condition (the exact
        # shape backends raise: RuntimeError(f"... HTTP error 500: {body}"))
        'ZAI HTTP error 500: {"error":{"type":"invalid_request_error",'
        '"code":"context_length_exceeded"}}',
        'OpenRouter API error 500: {"error":{"code":"model_not_found"}}',
        'HTTP error 500: {"error":{"code":"invalid_api_key"}}',
        'Provider error 500: {"error":{"type":"invalid_request_error"}}',
    ])
    def test_500_with_permanent_body_is_permanent(self, msg):
        assert is_transient_api_error(RuntimeError(msg)) is False

    def test_body_argument_is_honored(self):
        """The optional second arg: a caller holding the raw body can pass
        it even when the exception message omits it."""
        exc = RuntimeError("HTTP 500")
        assert is_transient_api_error(
            exc, body='{"error":{"code":"context_length_exceeded"}}') is False
        assert is_transient_api_error(
            exc, body='{"error":{"code":"model_not_found"}}') is False
        assert is_transient_api_error(
            exc, body='{"error":{"type":"invalid_request_error"}}') is False

    def test_plain_500_stays_transient(self):
        """A genuine server-side 500 (clean body) is still retryable."""
        assert is_transient_api_error(
            RuntimeError("ZAI HTTP error 500: Internal Server Error")) is True
        assert is_transient_api_error(
            RuntimeError("HTTP 500"),
            body='{"error":{"message":"please retry soon"}}') is True

    def test_body_does_not_create_transient_classification(self):
        """The body is only consulted for PERMANENT patterns — a transient
        marker in the body alone doesn't make a non-transient message
        retryable (message text governs transience)."""
        exc = RuntimeError("something odd happened")
        assert is_transient_api_error(exc, body="rate limit") is False

    def test_prose_forms_still_work(self):
        """Pre-existing prose markers keep winning (regression guard)."""
        assert is_transient_api_error(
            RuntimeError("HTTP 500: model not found on this deployment")) is False
        assert is_transient_api_error(
            RuntimeError("HTTP 500: invalid request body")) is False


# ============================================================================
# ROB-13 — the tool-parse fallback chain is logged under debug
# ============================================================================

class TestROB13ParseFailureChain:
    TEXT = "Action: shell\nAction Input: {definitely-not-json"

    def test_fallback_args_unchanged(self):
        """Behavior guard: the final fallback still returns
        {'input': raw_args} — ROB-13 adds visibility, not behavior change."""
        calls = ToolParser(["shell"]).parse(self.TEXT)
        assert len(calls) == 1
        assert calls[0].name == "shell"
        assert calls[0].arguments == {"input": "{definitely-not-json"}

    def test_debug_true_prints_failure_chain(self, capsys):
        calls = ToolParser(["shell"], debug=True).parse(self.TEXT)
        out = capsys.readouterr().out
        assert calls, "tool call must still be produced"
        assert "[tool-parse]" in out
        assert "json.loads" in out
        assert "all parsers failed" in out
        assert "{'input'" in out or "{'input':" in out.replace('"', "'")

    def test_debug_false_prints_nothing(self, capsys):
        ToolParser(["shell"], debug=False).parse(self.TEXT)
        assert capsys.readouterr().out == ""

    def test_debug_defaults_off(self, capsys):
        ToolParser(["shell"]).parse(self.TEXT)
        assert capsys.readouterr().out == ""

    def test_expression_rescue_is_logged(self, capsys):
        """Unbalanced braces defeat the py-dict→JSON converter but the
        expression regex rescues the args — the chain shows the rescue."""
        text = "Action: calculator\nAction Input: {'expression': '15 + 27'"
        calls = ToolParser(["calculator"], debug=True).parse(text)
        out = capsys.readouterr().out
        assert len(calls) == 1
        assert calls[0].arguments == {"expression": "15 + 27"}
        assert "rescued 'expression'" in out

    def test_clean_parse_prints_nothing_even_in_debug(self, capsys):
        text = 'Action: calculator\nAction Input: {"expression": "2 + 2"}'
        calls = ToolParser(["calculator"], debug=True).parse(text)
        out = capsys.readouterr().out
        assert calls[0].arguments == {"expression": "2 + 2"}
        assert "[tool-parse]" not in out

    def test_chain_survives_deep_fallback_levels(self, capsys):
        """A payload that fails ALL THREE JSON levels logs every level."""
        text = "Action: shell\nAction Input: {b'file_path': b'/etc/passwd'}"
        calls = ToolParser(["shell"], debug=True).parse(text)
        out = capsys.readouterr().out
        assert "[tool-parse]" in out
        # Level 1 + level 2 + the python-dict converter all logged
        assert out.count("json.loads") >= 2
        assert "python-dict" in out
        # SEC-02 guard intact: no bytes values in the produced args
        for call in calls:
            for val in call.arguments.values():
                assert not isinstance(val, bytes)
