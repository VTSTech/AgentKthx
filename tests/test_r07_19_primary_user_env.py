"""R07.19 regression: host-environment probe + Primary User prompt.

Two features:

1. **Host Environment probe** (`agentkthx/core/environment.py`): a light,
   stdlib-only OS/shell detection appended to the system prompt at Agent
   construction, so the model knows which argument syntax to pass to the
   shell tool (cmd.exe on Windows vs /bin/sh POSIX on Linux/macOS).
   Fail-safe (returns "" on any error), opt-out via
   ``AGENTKTHX_NO_ENV_PROBE=1``, BitNet gets a compact single line
   (lean-prompt crash threshold).

2. **Primary User** (`agentkthx/cli/commands/chat.py`): the chat REPL asks
   who is chatting at session start and renders their name in the prompt
   instead of the hardcoded ``You:``. Resolution order:
   ``--user`` flag > ``AGENTKTHX_USER`` env > interactive naming prompt
   (TTY only) > OS login name > "You". Includes the ROB-34 pin: the
   no-readline fallback prompt must be a proper CSI sequence, never a
   bare-ESC string (``ESC Y`` is a consumed 2-byte VT escape).

All tests are pure logic / mocked — no network, no real platform variation
required (the platform readers are monkeypatched per family).
"""

import argparse
import unittest
from pathlib import Path
from unittest import mock

from agentkthx.core.environment import (
    _shell_note,
    build_environment_section,
    probe_environment,
)

# ============================================================================
# Probe — per-family platform readers (monkeypatched platform surfaces)
# ============================================================================


class _FakeWinVer:
    """Stand-in for sys.getwindowsversion() (namedtuple-ish)."""

    def __init__(self, major, minor, build):
        self.major = major
        self.minor = minor
        self.build = build
        self.platform = 2
        self.service_pack = ""


class TestProbeEnvironment(unittest.TestCase):
    def test_windows_10(self):
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Windows"),
            mock.patch(
                "agentkthx.core.environment.sys.getwindowsversion",
                create=True,
                return_value=_FakeWinVer(10, 0, 19045),
            ),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="AMD64"),
        ):
            env = probe_environment()
        self.assertEqual(env["family"], "windows")
        self.assertIn("Windows 10", env["os"])
        self.assertIn("build 19045", env["os"])
        self.assertIn("NT 10.0", env["os"])
        self.assertEqual(env["arch"], "AMD64")

    def test_windows_11_build_boundary(self):
        """Build >= 22000 is Windows 11 (NT version fields froze at 10.0)."""
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Windows"),
            mock.patch(
                "agentkthx.core.environment.sys.getwindowsversion",
                create=True,
                return_value=_FakeWinVer(10, 0, 22631),
            ),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="AMD64"),
        ):
            env = probe_environment()
        self.assertIn("Windows 11", env["os"])
        self.assertIn("build 22631", env["os"])

    def test_windows_11_build_just_below_boundary(self):
        """Build 22000 is the FIRST Win11 build — 21999 is still Windows 10."""
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Windows"),
            mock.patch(
                "agentkthx.core.environment.sys.getwindowsversion",
                create=True,
                return_value=_FakeWinVer(10, 0, 21999),
            ),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="AMD64"),
        ):
            env = probe_environment()
        self.assertIn("Windows 10", env["os"])

    def test_windows_other_nt_major(self):
        """Non-10 NT majors (6.x era) render as 'Windows (NT x.y)' — not Win10."""
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Windows"),
            mock.patch(
                "agentkthx.core.environment.sys.getwindowsversion",
                create=True,
                return_value=_FakeWinVer(6, 1, 7601),
            ),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="x86"),
        ):
            env = probe_environment()
        self.assertIn("Windows (NT 6.1)", env["os"])

    def test_linux_pretty_name_and_kernel(self):
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Linux"),
            mock.patch(
                "agentkthx.core.environment.platform.freedesktop_os_release",
                return_value={"PRETTY_NAME": "Ubuntu 24.04.1 LTS"},
            ),
            mock.patch(
                "agentkthx.core.environment.platform.release",
                return_value="6.8.0-45-generic",
            ),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="x86_64"),
        ):
            env = probe_environment()
        self.assertEqual(env["family"], "linux")
        self.assertEqual(env["os"], "Ubuntu 24.04.1 LTS")
        self.assertEqual(env["kernel"], "6.8.0-45-generic")
        self.assertEqual(env["arch"], "x86_64")

    def test_linux_name_fallback_when_no_pretty_name(self):
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Linux"),
            mock.patch(
                "agentkthx.core.environment.platform.freedesktop_os_release",
                return_value={"NAME": "Alpine"},
            ),
            mock.patch("agentkthx.core.environment.platform.release", return_value="6.6.0-lts"),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="aarch64"),
        ):
            env = probe_environment()
        self.assertEqual(env["os"], "Alpine")

    def test_linux_malformed_os_release_is_not_fatal(self):
        """Non-freedistro / stripped containers: kernel-only identification."""
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Linux"),
            mock.patch(
                "agentkthx.core.environment.platform.freedesktop_os_release",
                side_effect=OSError("no /etc/os-release"),
            ),
            mock.patch("agentkthx.core.environment.platform.release", return_value="6.6.0"),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="x86_64"),
        ):
            env = probe_environment()
        self.assertEqual(env["family"], "linux")
        self.assertEqual(env["os"], "")
        self.assertEqual(env["kernel"], "6.6.0")

    def test_linux_pretty_name_truncated(self):
        """/etc/os-release is attacker-adjacent on multi-user boxes — cap it."""
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Linux"),
            mock.patch(
                "agentkthx.core.environment.platform.freedesktop_os_release",
                return_value={"PRETTY_NAME": "X" * 500},
            ),
            mock.patch("agentkthx.core.environment.platform.release", return_value="6.6.0"),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="x86_64"),
        ):
            env = probe_environment()
        self.assertLessEqual(len(env["os"]), 80)

    def test_macos_version_and_darwin_kernel(self):
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Darwin"),
            mock.patch(
                "agentkthx.core.environment.platform.mac_ver",
                return_value=("15.2", ("", "", ""), ""),
            ),
            mock.patch("agentkthx.core.environment.platform.release", return_value="24.2.0"),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="arm64"),
        ):
            env = probe_environment()
        self.assertEqual(env["family"], "macos")
        self.assertEqual(env["os"], "macOS 15.2")
        self.assertEqual(env["kernel"], "Darwin 24.2.0")
        self.assertEqual(env["arch"], "arm64")

    def test_unknown_platform(self):
        with (
            mock.patch("agentkthx.core.environment.platform.system", return_value="Plan9"),
            mock.patch("agentkthx.core.environment.platform.release", return_value="4th"),
            mock.patch("agentkthx.core.environment.platform.machine", return_value="mips"),
        ):
            env = probe_environment()
        self.assertEqual(env["family"], "unknown")
        self.assertIn("Plan9", env["os"])


# ============================================================================
# Rendering — section bodies + shell-syntax guidance
# ============================================================================


class TestShellNote(unittest.TestCase):
    def test_windows_notes_cmd_exe_and_powershell_escape_hatch(self):
        note = _shell_note("windows")
        self.assertIn("cmd.exe", note)
        self.assertIn("powershell -Command", note)

    def test_macos_notes_bsd_userland(self):
        note = _shell_note("macos")
        self.assertIn("/bin/sh", note)
        self.assertIn("BSD", note)

    def test_linux_notes_posix_and_coreutils(self):
        note = _shell_note("linux")
        self.assertIn("/bin/sh", note)
        self.assertIn("GNU coreutils", note)


class TestBuildEnvironmentSection(unittest.TestCase):
    def _patch_probe(self, env_dict):
        return mock.patch("agentkthx.core.environment.probe_environment", return_value=env_dict)

    def test_full_section_shape(self):
        with self._patch_probe(
            {
                "family": "linux",
                "os": "Ubuntu 24.04.1 LTS",
                "kernel": "6.8.0-45-generic",
                "arch": "x86_64",
            }
        ):
            section = build_environment_section()
        self.assertTrue(section.startswith("# Host Environment"))
        self.assertIn("- OS: Ubuntu 24.04.1 LTS, kernel 6.8.0-45-generic, x86_64", section)
        self.assertIn("- Shell tool:", section)
        self.assertIn("/bin/sh", section)

    def test_bitnet_compact_single_line(self):
        """BitNet's lean prompt must stay under ~500 chars — one plain line."""
        with self._patch_probe(
            {
                "family": "windows",
                "os": "Windows 11 (NT 10.0 build 22631)",
                "kernel": "",
                "arch": "AMD64",
            }
        ):
            section = build_environment_section(is_bitnet=True)
        self.assertFalse(section.startswith("#"))
        self.assertTrue(section.startswith("Environment: "))
        self.assertIn("cmd.exe syntax", section)
        self.assertLess(len(section), 200)
        self.assertNotIn("\n", section)

    def test_kill_switch_env_var(self):
        with mock.patch.dict("os.environ", {"AGENTKTHX_NO_ENV_PROBE": "1"}):
            self.assertEqual(build_environment_section(), "")
            self.assertEqual(build_environment_section(is_bitnet=True), "")

    def test_probe_failure_returns_empty_string(self):
        """The section is decoration — a probe crash must never break setup."""
        with mock.patch(
            "agentkthx.core.environment.probe_environment",
            side_effect=RuntimeError("boom"),
        ):
            self.assertEqual(build_environment_section(), "")


# ============================================================================
# Agent wiring — section lands in the system prompt on every path
# ============================================================================


class TestAgentEnvSectionWiring(unittest.TestCase):
    """The Agent constructor appends the section to custom / default prompts."""

    _WIN = {
        "family": "windows",
        "os": "Windows 11 (NT 10.0 build 22631)",
        "kernel": "",
        "arch": "AMD64",
    }

    def test_appended_to_custom_system_prompt(self):
        from agentkthx.agent import Agent

        with mock.patch("agentkthx.core.environment.probe_environment", return_value=self._WIN):
            agent = Agent(model="qwen2.5:0.5b", system_prompt="You are a test agent.")
        self.assertTrue(
            agent._custom_system_prompt.startswith("You are a test agent.\n\n# Host Environment")
        )
        self.assertIn("cmd.exe", agent._custom_system_prompt)
        # the custom prompt itself survived verbatim above the section
        self.assertIn("You are a test agent.", agent._custom_system_prompt)

    def test_appended_to_default_prompt(self):
        from agentkthx.agent import Agent

        with mock.patch("agentkthx.core.environment.probe_environment", return_value=self._WIN):
            agent = Agent(model="qwen2.5:0.5b", soul=None)
        self.assertIn("# Host Environment", agent._custom_system_prompt)

    def test_kill_switch_leaves_prompt_untouched(self):
        from agentkthx.agent import Agent

        with (
            mock.patch.dict("os.environ", {"AGENTKTHX_NO_ENV_PROBE": "1"}),
            mock.patch("agentkthx.core.environment.probe_environment", return_value=self._WIN),
        ):
            agent = Agent(model="qwen2.5:0.5b", system_prompt="You are a test agent.")
        self.assertEqual(agent._custom_system_prompt, "You are a test agent.")

    def test_bitnet_agent_gets_compact_line(self):
        """BitNet detection is backend-driven — pass backend='bitnet'."""
        from agentkthx.agent import Agent

        with mock.patch("agentkthx.core.environment.probe_environment", return_value=self._WIN):
            agent = Agent(model="bitnet-b1.58-2b-4t", system_prompt="Lean.", backend="bitnet")
        self.assertIn("Environment: Windows 11", agent._custom_system_prompt)
        self.assertNotIn("# Host Environment", agent._custom_system_prompt)


# ============================================================================
# Primary User — resolution precedence + sanitization
# ============================================================================


class TestPrimaryUserSanitize(unittest.TestCase):
    def test_strips_control_chars_and_ansi(self):
        from agentkthx.cli.commands.chat import _sanitize_primary_user

        self.assertEqual(_sanitize_primary_user("bob\n"), "bob")
        self.assertEqual(_sanitize_primary_user("  alice  "), "alice")
        # ESC (\033) is a control char → stripped; printable remainder kept
        self.assertEqual(_sanitize_primary_user("\033[31meve\033[0m"), "[31meve[0m")
        self.assertEqual(_sanitize_primary_user("a\tb"), "ab")

    def test_caps_length(self):
        from agentkthx.cli.commands.chat import _MAX_PRIMARY_USER_LEN, _sanitize_primary_user

        self.assertEqual(len(_sanitize_primary_user("x" * 100)), _MAX_PRIMARY_USER_LEN)

    def test_empty_after_clean_falls_back_empty(self):
        from agentkthx.cli.commands.chat import _sanitize_primary_user

        self.assertEqual(_sanitize_primary_user("\n\t"), "")


class TestDefaultPrimaryUser(unittest.TestCase):
    def test_os_login_name(self):
        import agentkthx.cli.commands.chat as chat

        with mock.patch.object(chat.getpass, "getuser", return_value="nigel"):
            self.assertEqual(chat._default_primary_user(), "nigel")

    def test_falls_back_to_you_when_unavailable(self):
        import agentkthx.cli.commands.chat as chat

        with mock.patch.object(chat.getpass, "getuser", side_effect=OSError("no login")):
            self.assertEqual(chat._default_primary_user(), "You")

    def test_sanitizes_login_name(self):
        import agentkthx.cli.commands.chat as chat

        with mock.patch.object(chat.getpass, "getuser", return_value="machine\x1b]0;evil"):
            # ESC + ] is a terminal escape — control chars stripped
            self.assertEqual(chat._default_primary_user(), "machine]0;evil")


class TestResolvePrimaryUser(unittest.TestCase):
    def _args(self, user=None):
        return argparse.Namespace(user=user)

    def test_flag_wins_over_everything(self):
        import agentkthx.cli.commands.chat as chat

        with (
            mock.patch.dict("os.environ", {"AGENTKTHX_USER": "EnvUser"}),
            mock.patch("builtins.input", side_effect=AssertionError("must not prompt")),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args("FlagUser")), "FlagUser")

    def test_env_wins_over_interactive(self):
        import agentkthx.cli.commands.chat as chat

        with (
            mock.patch.dict("os.environ", {"AGENTKTHX_USER": "EnvUser"}),
            mock.patch.object(chat.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=AssertionError("must not prompt")),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args(None)), "EnvUser")

    def test_interactive_prompt_accepts_input(self):
        import agentkthx.cli.commands.chat as chat

        env = {k: v for k, v in __import__("os").environ.items() if k != "AGENTKTHX_USER"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(chat.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value="Charlie") as m_input,
            mock.patch.object(chat.getpass, "getuser", return_value="nigel"),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args(None)), "Charlie")
            self.assertIn("Primary User [nigel]", m_input.call_args[0][0])

    def test_interactive_empty_input_uses_default(self):
        import agentkthx.cli.commands.chat as chat

        env = {k: v for k, v in __import__("os").environ.items() if k != "AGENTKTHX_USER"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(chat.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", return_value=""),
            mock.patch.object(chat.getpass, "getuser", return_value="nigel"),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args(None)), "nigel")

    def test_non_tty_never_prompts(self):
        """Piped stdin (tests / ACP / scripting) must not block on naming."""
        import agentkthx.cli.commands.chat as chat

        env = {k: v for k, v in __import__("os").environ.items() if k != "AGENTKTHX_USER"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(chat.sys.stdin, "isatty", return_value=False),
            mock.patch("builtins.input", side_effect=AssertionError("must not prompt")),
            mock.patch.object(chat.getpass, "getuser", return_value="nigel"),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args(None)), "nigel")

    def test_eof_at_naming_prompt_falls_back_to_default(self):
        import agentkthx.cli.commands.chat as chat

        env = {k: v for k, v in __import__("os").environ.items() if k != "AGENTKTHX_USER"}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(chat.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=EOFError),
            mock.patch.object(chat.getpass, "getuser", return_value="nigel"),
        ):
            self.assertEqual(chat._resolve_primary_user(self._args(None)), "nigel")


# ============================================================================
# CLI surface + source pins
# ============================================================================


class TestChatUserFlag(unittest.TestCase):
    def test_parser_accepts_user_flag(self):
        from agentkthx.cli.parser import create_parser

        args = create_parser().parse_args(["chat", "--user", "Nigel"])
        self.assertEqual(args.user, "Nigel")

    def test_parser_accepts_short_flag(self):
        from agentkthx.cli.parser import create_parser

        args = create_parser().parse_args(["chat", "-u", "Nigel"])
        self.assertEqual(args.user, "Nigel")

    def test_parser_default_is_none(self):
        from agentkthx.cli.parser import create_parser

        args = create_parser().parse_args(["chat"])
        self.assertIsNone(args.user)

    def test_user_flag_is_chat_only(self):
        """--user / -u must NOT leak onto other subcommands (non-interactive)."""
        from agentkthx.cli.parser import create_parser

        parser = create_parser()
        for sub in ("agent", "run", "test"):
            subparser = parser._subparsers_action.choices[sub]
            opts = {a for act in subparser._actions for a in act.option_strings}
            self.assertNotIn("--user", opts, f"{sub} must not accept --user")
            self.assertNotIn("-u", opts, f"{sub} must not accept -u")


class TestPromptSourcePins(unittest.TestCase):
    """Pin the prompt-rendering contract at source level (house style)."""

    def _src(self) -> str:
        path = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"
        return path.read_text(encoding="utf-8")

    def test_readline_prompt_uses_primary_user(self):
        src = self._src()
        self.assertIn('_prompt = f"\\001\\033[33m\\002{primary_user}:\\001\\033[0m\\002 "', src)

    def test_no_readline_prompt_uses_primary_user(self):
        src = self._src()
        self.assertIn('_prompt = f"\\033[33m{primary_user}:\\033[0m "', src)

    def test_no_hardcoded_you_prompt_remains(self):
        src = self._src()
        self.assertNotIn("\\002You:", src)
        self.assertNotIn("\\033[33mYou:", src)

    def test_rob34_no_bare_esc_prompt(self):
        """ROB-34 pin: the no-readline fallback must be a proper CSI sequence.

        A bare ESC followed by a printable byte (e.g. "\\033You") forms a
        complete 2-byte VT escape that terminals CONSUME — the prompt would
        render as "ou:" and eat the first echoed character. R07.19 verified
        the tree carries the CSI form; this pin keeps it that way.
        """
        src = self._src()
        self.assertNotIn('"\\033You', src)

    def test_chat_announces_primary_user(self):
        src = self._src()
        self.assertIn("Chatting with", src)
        self.assertIn("primary_user = _resolve_primary_user(args)", src)


if __name__ == "__main__":
    unittest.main()
