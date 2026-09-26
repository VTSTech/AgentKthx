"""
R06.52 Loop Resilience Tests — death-spiral prevention.

Covers the five-bug cascade found during the codebase-audit investigation:
  1. is_error_result() full-text substring matching (false positives)
  2. Lifetime-total termination killing healthy long runs
  3. Identical failing tool calls re-issued forever (hallucinated args)
  4. Termination that didn't terminate + dangling tool_calls (API 400s)
  5. Memory pruning that orphaned tool results / collapsed context

Written by VTSTech — https://www.vts-tech.org
"""

import json

import pytest

from agentkthx.core.error_recovery import (
    ErrorRecoveryTracker,
    is_error_result,
    DEFAULT_MAX_IDENTICAL_FAILURES,
    DEFAULT_MAX_CONSECUTIVE_ALL_FAILURES,
)
from agentkthx.core.memory import Memory, MemoryConfig
from agentkthx.agent import Agent
from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.core.types import BackendType
from agentkthx.tools import make_builtin_registry


# ============================================================================
# Test doubles
# ============================================================================

class StubBackend(BaseBackend):
    """Scripted backend: pops one response per generate() call."""

    def __init__(self, script):
        super().__init__(config=BackendConfig(), base_url="http://stub")
        self.script = list(script)
        self.calls = []

    @property
    def backend_type(self) -> BackendType:
        return BackendType.OLLAMA

    @property
    def base_url(self) -> str:
        return "http://stub"

    def generate(self, model, messages, tools=None, temperature=0.1,
                 max_tokens=8192, **kwargs):
        self.calls.append(messages)
        if not self.script:
            return {"content": "Final Answer: done", "tool_calls": [],
                    "usage": {"total_tokens": 1}, "finish_reason": "stop"}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"content": item.get("content", ""),
                "tool_calls": item.get("tool_calls", []),
                "usage": {"total_tokens": 1},
                "finish_reason": item.get("finish_reason", "stop")}

    def generate_stream(self, model, messages, tools=None, **kwargs):
        yield "Final Answer: done"

    def list_models(self):
        return ["stub"]

    def test_tool_support(self, model):
        return True


def native_call(call_id, name, arguments):
    return {"id": call_id, "name": name, "arguments": arguments}


# ============================================================================
# is_error_result — first-line detection
# ============================================================================

class TestIsErrorResult:
    def test_empty_result_is_error(self):
        assert is_error_result("") is True

    def test_whitespace_result_is_error(self):
        assert is_error_result("   \n\t  ") is True

    def test_error_prefix(self):
        assert is_error_result("Error: something broke") is True

    def test_error_mid_text_mention_is_not_error(self):
        # A result that merely MENTIONS errors later in the text
        assert is_error_result(
            "grep scanned 40 files\n0 matches for 'error'\nall clean"
        ) is False

    def test_log_excerpt_containing_timeout_is_not_error(self):
        assert is_error_result(
            "server log:\nrequest handled after timeout=30s\nok"
        ) is False

    def test_exit_code_marker_first_line(self):
        assert is_error_result("[Exit code: 2]\nError: ls: cannot access") is True

    def test_exit_code_zero_is_not_error(self):
        assert is_error_result("[Exit code: 0]\nall good") is False

    def test_file_not_found(self):
        assert is_error_result("File not found: /workspace/brief.md") is True

    def test_file_not_found_mentioned_later_is_not_error(self):
        assert is_error_result("scanned tree\n(note: File not found: x was expected)") is False

    def test_permission_denied(self):
        assert is_error_result("Permission denied: /etc/shadow") is True

    def test_command_timed_out(self):
        assert is_error_result("Command timed out after 30 seconds") is True

    def test_traceback(self):
        assert is_error_result("Traceback (most recent call last):\n  ...") is True

    def test_sandbox_error(self):
        assert is_error_result("[Sandbox Error] process exited 1") is True

    def test_sandbox_timeout(self):
        assert is_error_result("[Sandbox] execution timeout") is True

    def test_tool_blocked(self):
        assert is_error_result("Tool 'shell' was blocked by the security policy") is True

    def test_tool_not_found(self):
        assert is_error_result("Tool 'frobnicate' not found in registry") is True

    def test_plain_success(self):
        assert is_error_result("total 124\ndrwxrwxr-x 14 vtstech vtstech") is False

    def test_leading_whitespace_then_error(self):
        assert is_error_result("  \nError: boom") is True

    def test_case_insensitive(self):
        assert is_error_result("ERROR: nope") is True


# ============================================================================
# Consecutive termination semantics
# ============================================================================

class TestConsecutiveTermination:
    def test_fresh_tracker_does_not_terminate(self):
        t = ErrorRecoveryTracker()
        assert t.should_terminate() is False

    def test_consecutive_failures_reach_threshold(self):
        t = ErrorRecoveryTracker()
        for i in range(DEFAULT_MAX_CONSECUTIVE_ALL_FAILURES):
            t.record_failure("shell", "Error: x", step=i)
        assert t.should_terminate() is True

    def test_success_resets_consecutive_counter(self):
        t = ErrorRecoveryTracker()
        for i in range(DEFAULT_MAX_CONSECUTIVE_ALL_FAILURES - 1):
            t.record_failure("shell", "Error: x", step=i)
        t.record_success("shell")
        t.record_failure("shell", "Error: y", step=99)
        assert t.should_terminate() is False

    def test_scattered_failures_never_terminate(self):
        t = ErrorRecoveryTracker()
        for i in range(30):
            t.record_failure("shell", "Error: x", step=i)
            t.record_success("read_file")
        assert t.should_terminate() is False
        assert t.total_failures == 30  # lifetime stat still tracked

    def test_consecutive_all_property(self):
        t = ErrorRecoveryTracker()
        t.record_failure("a", "Error: x", step=0)
        t.record_failure("b", "Error: y", step=1)
        assert t.consecutive_all_failures == 2
        t.record_success("a")
        assert t.consecutive_all_failures == 0

    def test_reset_clears_everything(self):
        t = ErrorRecoveryTracker()
        t.record_failure("shell", "Error: x", step=0, arguments={"cmd": "ls"})
        t.reset()
        assert t.total_failures == 0
        assert t.consecutive_all_failures == 0
        assert t.recent_failures == {}
        assert t.should_terminate() is False


# ============================================================================
# Identical duplicate-call blocking
# ============================================================================

class TestDuplicateCallBlocking:
    def test_first_failure_not_blocked(self):
        t = ErrorRecoveryTracker()
        t.record_failure("read_file", "Error: not found", step=0,
                         arguments={"file_path": "/a.md"})
        assert t.should_block_repeat("read_file", {"file_path": "/a.md"}) is False

    def test_identical_failure_blocked_after_threshold(self):
        t = ErrorRecoveryTracker()
        args = {"file_path": "/a.md"}
        for i in range(DEFAULT_MAX_IDENTICAL_FAILURES):
            t.record_failure("read_file", "Error: not found", step=i, arguments=args)
        assert t.should_block_repeat("read_file", args) is True

    def test_different_args_not_blocked(self):
        t = ErrorRecoveryTracker()
        for i in range(DEFAULT_MAX_IDENTICAL_FAILURES):
            t.record_failure("read_file", "Error: not found", step=i,
                             arguments={"file_path": "/a.md"})
        assert t.should_block_repeat("read_file", {"file_path": "/b.md"}) is False

    def test_signature_is_order_independent(self):
        t = ErrorRecoveryTracker()
        for i in range(DEFAULT_MAX_IDENTICAL_FAILURES):
            t.record_failure("shell", "Error: x", step=i,
                             arguments={"command": "ls -la", "timeout": 5})
        assert t.should_block_repeat("shell", {"timeout": 5, "command": "ls -la"}) is True

    def test_get_repeat_failure_count(self):
        t = ErrorRecoveryTracker()
        args = {"file_path": "/a.md"}
        t.record_failure("read_file", "Error: e1", step=0, arguments=args)
        t.record_failure("read_file", "Error: e2", step=1, arguments=args)
        assert t.get_repeat_failure("read_file", args) == 2

    def test_format_repeat_block_contents(self):
        t = ErrorRecoveryTracker()
        args = {"file_path": "/a.md"}
        t.record_failure("read_file", "Error: not found", step=0, arguments=args)
        t.record_failure("read_file", "Error: not found", step=1, arguments=args)
        msg = t.format_repeat_block("read_file", args)
        assert "Repeated identical tool call blocked" in msg
        assert "read_file" in msg
        assert "Do NOT repeat the same call" in msg


# ============================================================================
# Memory pruning — pairing-safe sliding window
# ============================================================================

class TestMemoryPruning:
    def test_no_prune_below_limit(self):
        m = Memory(MemoryConfig(max_messages=50))
        for i in range(30):
            m.add("user", f"msg {i}")
        assert len(m.get_messages()) == 30

    def test_window_slides_to_threshold(self):
        m = Memory(MemoryConfig(max_messages=50, summarization_threshold=0.8))
        m.add("system", "sys")
        for i in range(51):
            m.add("user", f"msg {i}")
        msgs = m.get_messages()
        # Prune slides to 40 non-system at add #50; the 51st add lands in the
        # reclaimed headroom before the next prune → system + 41.
        assert len(msgs) == 42
        assert msgs[0]["role"] == "system"
        # Newest message retained
        assert msgs[-1]["content"] == "msg 50"

    def test_recent_messages_retained(self):
        m = Memory(MemoryConfig(max_messages=10, summarization_threshold=0.8))
        for i in range(20):
            m.add("user", f"msg {i}")
        msgs = m.get_messages()
        assert msgs[-1]["content"] == "msg 19"
        assert len(msgs) == 8  # keep_count = 8

    def test_no_orphan_tool_results_at_window_head(self):
        m = Memory(MemoryConfig(max_messages=10, summarization_threshold=0.8))
        m.add_tool_call("assistant", "calling", [native_call("c1", "shell", {"command": "ls"})])
        m.add_tool_result("c1", "shell", "output")
        for i in range(20):
            m.add("user", f"msg {i}")
        msgs = m.get_messages()
        # The window must not START with a tool result whose call fell out
        assert msgs[0]["role"] != "tool"

    def test_prune_keeps_tool_pairs_intact(self):
        m = Memory(MemoryConfig(max_messages=12, summarization_threshold=0.8))
        for i in range(15):
            m.add_tool_call("assistant", "call", [native_call(f"p{i}", "shell", {"command": f"cmd{i}"})])
            m.add_tool_result(f"p{i}", "shell", f"out {i}")
        msgs = m.get_messages()
        roles = [x["role"] for x in msgs]
        # Every tool result must follow its assistant call
        for idx, role in enumerate(roles):
            if role == "tool":
                assert roles[idx - 1] == "assistant"


# ============================================================================
# sanitize_history — API-valid sequences
# ============================================================================

class TestSanitizeHistory:
    def test_orphan_tool_result_removed(self):
        m = Memory()
        m.add_tool_result("ghost", "shell", "stale output")
        msgs = m.get_messages()
        assert all(x["role"] != "tool" for x in msgs)

    def test_dangling_call_gets_placeholder(self):
        m = Memory()
        m.add_tool_call("assistant", "calling", [native_call("c9", "shell", {"command": "ls"})])
        msgs = m.get_messages()
        tool_msgs = [x for x in msgs if x["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "c9"
        assert "no result was recorded" in tool_msgs[0]["content"]

    def test_paired_history_untouched(self):
        m = Memory()
        m.add_tool_call("assistant", "calling", [native_call("ok1", "shell", {"command": "ls"})])
        m.add_tool_result("ok1", "shell", "file list")
        snapshot = json.dumps(m.get_messages(), sort_keys=True)
        m.sanitize_history()
        assert json.dumps(m.get_messages(), sort_keys=True) == snapshot

    def test_sanitize_is_idempotent(self):
        m = Memory()
        m.add_tool_result("ghost", "shell", "stale")
        m.add_tool_call("assistant", "calling", [native_call("d1", "shell", {"command": "ls"})])
        first = json.dumps(m.get_messages(), sort_keys=True)
        m.sanitize_history()
        second = json.dumps(m.get_messages(), sort_keys=True)
        m.sanitize_history()
        third = json.dumps(m.get_messages(), sort_keys=True)
        assert second == third == first

    def test_partial_multi_call_completion(self):
        m = Memory()
        m.add_tool_call("assistant", "calling", [
            native_call("m1", "shell", {"command": "a"}),
            native_call("m2", "shell", {"command": "b"}),
        ])
        m.add_tool_result("m1", "shell", "out a")
        msgs = m.get_messages()
        tool_ids = [x["tool_call_id"] for x in msgs if x["role"] == "tool"]
        assert tool_ids == ["m1", "m2"]


# ============================================================================
# _execute_tool — argument sanitization
# ============================================================================

class TestExecuteToolArgSanitization:
    def _agent(self):
        return Agent(
            model="stub",
            backend=StubBackend([]),
            tools=make_builtin_registry().subset(["read_file", "calculator"]),
            max_steps=3,
            soul=None,
        )

    def test_hallucinated_param_stripped_not_fatal(self):
        agent = self._agent()
        result = agent._execute_tool("read_file", {
            "file_path": "README.md",
            "encoding": "utf-8",  # hallucinated
        })
        assert not result.startswith("Error:")
        assert "ignored unknown parameter(s) encoding" in result

    def test_numeric_string_coerced_for_calculator(self):
        agent = self._agent()
        result = agent._execute_tool("calculator", {"expression": "2+2"})
        assert not result.startswith("Error:")

    def test_unknown_tool_reports_error(self):
        agent = self._agent()
        result = agent._execute_tool("does_not_exist", {})
        assert "Unknown tool" in result


# ============================================================================
# Shell error format — marker first
# ============================================================================

class TestShellErrorFormat:
    def test_nonzero_exit_marker_is_first_line(self, tmp_path):
        from agentkthx.tools.builtins import shell
        script = tmp_path / "fail.sh"
        script.write_text("#!/bin/bash\necho 'partial output'\nexit 3\n")
        # SEC-04: shells (bash/sh/...) are blocked as base commands —
        # invoke the script directly; the shebang is still honored.
        script.chmod(0o755)
        result = shell(str(script))
        assert result.startswith("[Exit code: 3]")
        assert "partial output" in result

    def test_success_has_no_marker(self, tmp_path):
        from agentkthx.tools.builtins import shell
        script = tmp_path / "ok.sh"
        script.write_text("#!/bin/bash\necho hello world\n")
        script.chmod(0o755)
        result = shell(str(script))
        assert result.startswith("hello world")
        assert "[Exit code" not in result

    def test_bash_by_name_is_blocked(self):
        """SEC-04 companion check: `bash <script>` is rejected outright."""
        from agentkthx.tools.builtins import shell
        result = shell("bash /tmp/whatever.sh")
        assert result.startswith("Security error:")
        assert "bash" in result


# ============================================================================
# End-to-end: termination leaves a valid history
# ============================================================================

class TestTerminationLeavesValidHistory:
    def test_stubborn_backend_terminates_with_paired_history(self):
        """A backend that keeps re-issuing the same failing call must be
        blocked, terminated, and never leave dangling tool_calls."""
        call = native_call("t1", "read_file", {"file_path": "definitely_missing.md"})
        backend = StubBackend([
            {"tool_calls": [call], "content": "trying"},
            {"tool_calls": [call], "content": "retrying"},
            {"tool_calls": [call], "content": "again"},
            {"tool_calls": [call], "content": "once more"},
            {"tool_calls": [call], "content": "and again"},
            {"tool_calls": [call], "content": "persist"},
        ])
        agent = Agent(
            model="stub",
            backend=backend,
            tools=make_builtin_registry().subset(["read_file"]),
            max_steps=10,
            soul=None,
        )
        result = agent.run("audit")
        assert result.success is False
        # The run must terminate before max_steps via the tracker
        assert len(result.steps) < 10
        # The wire format must contain zero dangling tool_calls
        msgs = agent.memory.get_messages()
        for i, msg in enumerate(msgs):
            for tc in (msg.get("tool_calls") or []):
                cid = tc.get("id")
                assert any(
                    m.get("role") == "tool" and m.get("tool_call_id") == cid
                    for m in msgs
                ), f"dangling tool_call {cid} at msg {i}"
        # And the repeat-block must have engaged
        assert any(
            "Repeated identical tool call blocked" in (m.get("content") or "")
            for m in msgs if m.get("role") in ("tool", "user")
        )

    def test_healthy_run_with_scattered_errors_survives(self):
        """Errors alternating with successes must NOT terminate the run."""
        script = []
        for i in range(4):
            script.append({"tool_calls": [native_call(f"h{i}", "read_file",
                                                      {"file_path": "README.md"})],
                           "content": "checking"})
            script.append({"tool_calls": [native_call(f"e{i}", "read_file",
                                                      {"file_path": f"no_such_file_{i}.md"})],
                           "content": "also checking"})
        script.append({"content": "Final Answer: audit done"})
        backend = StubBackend(script)
        agent = Agent(
            model="stub",
            backend=backend,
            tools=make_builtin_registry().subset(["read_file"]),
            max_steps=20,
            soul=None,
        )
        result = agent.run("audit")
        assert result.final_answer == "audit done"
        assert result.success is True
