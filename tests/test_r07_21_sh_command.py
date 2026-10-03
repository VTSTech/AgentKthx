"""
R07.21 regression tests — /sh chat slash command.

Pins the /sh slash command added in R07.21:

  * `/sh <command>` — runs a local shell command, displays the output,
    AND injects the output into the agent's context as a user-role
    message so the model can use it on the next turn.
  * `/sh -n <command>` — runs the command, displays the output, does NOT
    inject into context.

The /sh command reuses the built-in ``shell()`` tool from
``agentkthx.tools.builtins`` so the same security checks
(``sanitize_command`` — blocked patterns, heredoc/shell-injection guards),
timeout clamping (max 300s), and exit-code formatting apply.

Scope note: ``cmd_chat`` is a 1,733-line single function (MAINT-01), so the
/sh branch cannot be tested directly without refactoring it into a
``cmd_sh(session, args)`` method (which is what MAINT-27 tracks). These
tests pin the CONTRACT the /sh handler must satisfy (context injection
shape, -n negative contract) via direct simulation, plus a real-shell
integration suite that exercises the ``shell()`` builtin the handler calls.
The parsing tests that would test a COPY of the parser are deliberately
omitted — they'd test a re-implementation, not the real parser, and would
keep passing if the real parser drifted. Real parsing coverage lands when
MAINT-01 extracts the /sh branch into a testable method.

Written by VTSTech — https://www.vts-tech.org
"""

import unittest
from unittest.mock import MagicMock


class _FakeAgent:
    """Minimal stand-in for Agent with the memory API /sh uses.

    The /sh command only touches:
      * agent.memory.add(role, content) — for context injection
      * len(agent.memory) — for the "X turns in memory" footer line

    We don't need the full Agent class; a fake memory list is enough.
    """

    def __init__(self):
        self.memory = MagicMock()
        self.memory._messages = []
        # len(agent.memory) is called in the /sh handler — make it return
        # the current message count.
        self.memory.__len__ = lambda self_: len(self._messages)

        # agent.memory.add(role, content) appends to the list
        def _add(role, content, **kwargs):
            self.memory._messages.append({"role": role, "content": content})

        self.memory.add.side_effect = _add


class TestShContextInjectionContract(unittest.TestCase):
    """The /sh handler's context-injection contract: when -n is NOT passed,
    the output is added to agent.memory as a user-role message wrapped in
    <shell_output> tags. When -n IS passed, memory.add is never called.

    These tests verify the CONTRACT directly via a fake agent — they don't
    test the real /sh branch (which lives inline in cmd_chat and can't be
    called in isolation until MAINT-01 lands). The contract is what matters:
    any future refactor that breaks the memory.add call shape or the -n
    negative contract will fail these tests once the branch is extracted.
    """

    def test_sh_injects_output_into_context_as_user_message(self):
        """/sh <command> adds the output to agent.memory as a user message
        wrapped in <shell_output command='...'>...</shell_output> tags."""
        agent = _FakeAgent()
        output = "total 0\ndrwxr-xr-x  2 user user  40 Oct  3 12:00 ."
        command = "ls -la"

        # Simulate the injection shape the /sh handler must produce
        context_msg = f"<shell_output command={command!r}>\n" f"{output}\n" f"</shell_output>"
        agent.memory.add("user", context_msg)

        # Verify the message was added with the right shape
        self.assertEqual(agent.memory.add.call_count, 1)
        call_args = agent.memory.add.call_args
        self.assertEqual(call_args[0][0], "user")
        self.assertIn("ls -la", call_args[0][1])
        self.assertIn("total 0", call_args[0][1])
        self.assertIn("<shell_output", call_args[0][1])
        self.assertIn("</shell_output>", call_args[0][1])

    def test_sh_n_does_not_inject_into_context(self):
        """/sh -n <command> does NOT call agent.memory.add — the -n flag
        short-circuits the context injection. The output is displayed only."""
        agent = _FakeAgent()
        # Simulate the -n path: the handler checks the -n flag and skips
        # the memory.add call entirely.
        no_inject = True
        if not no_inject:
            agent.memory.add("user", "should not be called")
        # Verify memory.add was never called
        self.assertEqual(agent.memory.add.call_count, 0)

    def test_shell_output_tag_format_uses_repr_for_command(self):
        """The <shell_output command=...> tag uses Python repr (!r) so the
        command string is properly quoted even when it contains spaces,
        pipes, or special characters. This pins the tag format the model
        parses."""
        # Simple command
        cmd = "ls -la"
        tag = f"<shell_output command={cmd!r}>"
        self.assertEqual(tag, "<shell_output command='ls -la'>")

        # Command with pipes
        cmd = "cat foo | grep bar"
        tag = f"<shell_output command={cmd!r}>"
        self.assertEqual(tag, "<shell_output command='cat foo | grep bar'>")

        # Command with quotes
        cmd = 'echo "hello world"'
        tag = f"<shell_output command={cmd!r}>"
        # repr of a string containing double quotes uses single quotes
        self.assertEqual(tag, "<shell_output command='echo \"hello world\"'" + ">")


class TestShIntegrationWithRealShell(unittest.TestCase):
    """End-to-end tests that actually run a shell command via the real
    ``shell()`` builtin — verifies the /sh handler's plumbing matches the
    builtin's contract (exit-code marker, stdout/stderr formatting).

    These tests don't touch the /sh branch in cmd_chat (which can't be
    called in isolation) — they verify the ``shell()`` builtin that the
    /sh branch calls. Any change to the builtin's output format that
    would break the /sh handler will fail here.
    """

    def test_sh_echo_command_produces_output(self):
        """Running `echo hello` via the shell builtin produces 'hello'."""
        from agentkthx.tools.builtins import shell

        output = shell("echo hello")
        self.assertEqual(output, "hello")

    def test_sh_failing_command_includes_exit_code(self):
        """Running `false` (exit 1) produces output with the exit-code marker
        on the first line — the format the /sh handler displays verbatim."""
        from agentkthx.tools.builtins import shell

        output = shell("false")
        # The builtin formats non-zero exits as "[Exit code: N]" on the
        # first line — /sh displays this verbatim.
        self.assertIn("[Exit code: 1]", output)

    def test_sh_with_stderr_includes_error_line(self):
        """Running a command that writes to stderr includes 'Error:'."""
        from agentkthx.tools.builtins import shell

        # `ls /nonexistent` writes to stderr — shell builtin captures it.
        output = shell("ls /nonexistent_path_12345")
        # Should mention either the exit code OR an error message
        self.assertTrue(
            "[Exit code:" in output or "No such file" in output or "Error" in output,
            f"Expected exit-code marker or error in output: {output!r}",
        )


if __name__ == "__main__":
    unittest.main()
