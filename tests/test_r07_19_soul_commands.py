"""R07.19 follow-up #5 — /souls + /soul chat commands & config env-var completeness.

Covers four layers of the soul-command feature and the config reference burn-down:

1. ``SoulLoader.list_souls()`` — bundled soul discovery (Level-1 manifests,
   kthx-* packages, cache-safe, best-effort on unloadable entries).
2. ``AgentSetupMixin.switch_soul()`` — the mid-session soul switch that
   backs ``/soul <name>``: manifest swap, ``allowed_tools`` re-filter,
   system-prompt rebuild (soul + skills + host environment), memory
   system-message replacement, tool-parser refresh.
3. ``cmd_chat`` slash-command wiring — source pins (the chat REPL is a
   single function; command presence is pinned the way test_r07_12_quick_wins
   pins its slash-command plumbing).
4. ``agentkthx config`` env-var reference — every user-facing env var read
   by the package must appear in the reference (the Mistral, OrcaRouter
   and Pollinations plugins were entirely missing, along with the R07.19
   core vars AGENTKTHX_USER / AGENTKTHX_NO_ENV_PROBE).
"""

import io
import re
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SOULS_DIR = Path(__file__).resolve().parent.parent / "agentkthx" / "souls"


# ═══════════════════════════════════════════════════════════════════════
# 1. SoulLoader.list_souls()
# ═══════════════════════════════════════════════════════════════════════


class TestSoulDiscovery:
    def test_list_souls_returns_all_bundled_souls(self):
        from agentkthx.soul import SoulLoader

        manifests = SoulLoader().list_souls()
        names = [m.name for m in manifests]
        assert names == [
            "kthx-helper",
            "kthx-skills",
            "kthx-trading",
        ], f"list_souls() must discover the three bundled kthx-* souls, got {names}"

    def test_level1_manifests_carry_display_metadata(self):
        """Level-1 (manifest-only) discovery is enough for the /souls listing."""
        from agentkthx.soul import SoulLoader

        for m in SoulLoader().list_souls():
            assert m.display_name, f"{m.name}: display_name missing"
            assert m.version, f"{m.name}: version missing"
            assert m.description, f"{m.name}: description missing"
            # Level 1 = soul.json only — persona files stay unloaded (cheap)
            assert m.soul_content is None, f"{m.name}: level-1 load pulled SOUL.md"

    def test_list_souls_matches_shipped_directories(self):
        """Every souls/<dir> with a soul.json is discovered — and vice versa."""
        from agentkthx.soul import SoulLoader

        shipped = {p.name for p in SOULS_DIR.iterdir() if (p / "soul.json").exists()}
        listed = {m.name for m in SoulLoader().list_souls()}
        assert listed == shipped

    def test_list_souls_is_cache_safe(self):
        from agentkthx.soul import SoulLoader

        loader = SoulLoader()
        first = [(m.name, m.version) for m in loader.list_souls()]
        second = [(m.name, m.version) for m in loader.list_souls()]
        assert first == second


# ═══════════════════════════════════════════════════════════════════════
# 2. AgentSetupMixin.switch_soul()
# ═══════════════════════════════════════════════════════════════════════

TRADING_TOOLS = {
    "http_get",
    "python_repl",
    "parse_json",
    "web_search",
    "calculator",
}


def _make_agent(**overrides):
    from agentkthx.agent import Agent

    params = dict(
        model="qwen2.5:0.5b",
        tools=["calculator", "shell"],
        soul="kthx-helper",
    )
    params.update(overrides)
    return Agent(**params)


class TestSwitchSoul:
    def test_soul_level_stored_at_construction(self):
        agent = _make_agent(soul_level=2)
        assert agent._soul_level == 2

    def test_switch_changes_manifest_and_prompt(self):
        agent = _make_agent()
        before = agent._custom_system_prompt
        new_soul = agent.switch_soul("kthx-trading")
        assert agent.soul is new_soul
        assert new_soul.name == "kthx-trading"
        assert "Kthx Trading Analyst" in agent._custom_system_prompt
        # The helper persona is gone from the prompt
        assert "AgentKthx - LLM Diagnostic" not in agent._custom_system_prompt
        assert agent._custom_system_prompt != before

    def test_switch_filters_tools_like_startup(self):
        """Trading's allowedTools must drop `shell` — startup contract."""
        agent = _make_agent()
        agent.switch_soul("kthx-trading")
        new_tools = set(agent.tools.names())
        assert new_tools == {"calculator", "shell"} & TRADING_TOOLS
        assert "shell" not in new_tools

    def test_switch_never_adds_missing_tools(self):
        """A permissive soul cannot resurrect tools the session never had."""
        agent = _make_agent(tools=["calculator"], soul="kthx-helper")
        agent.switch_soul("kthx-trading")  # allows 7 tools — calculator stays alone
        assert set(agent.tools.names()) == {"calculator"}

    def test_switch_replaces_memory_system_message(self):
        agent = _make_agent()
        agent.switch_soul("kthx-trading")
        msgs = agent.memory.get_messages()
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 1, "switch must replace, not stack, system messages"
        assert system_msgs[0]["content"] == agent._custom_system_prompt

    def test_switch_preserves_conversation_history(self):
        agent = _make_agent()
        agent.memory.add("user", "remember-me-marker")
        agent.switch_soul("kthx-trading")
        contents = [m["content"] for m in agent.memory.get_messages()]
        assert any("remember-me-marker" in c for c in contents)

    def test_switch_preserves_skills_text(self):
        """--skills startup text + mid-session /skill blocks survive the switch."""
        agent = _make_agent()
        agent._skills_prompt = "SKILLS-BLOCK-MARKER"
        agent.switch_soul("kthx-trading")
        assert "SKILLS-BLOCK-MARKER" in agent._custom_system_prompt
        # ...and it lands in memory too (the rebuilt prompt is stored wholesale)
        assert "SKILLS-BLOCK-MARKER" in agent.memory.get_messages()[0]["content"]

    def test_switch_keeps_host_environment_section(self):
        agent = _make_agent()
        assert "# Host Environment" in agent._custom_system_prompt
        agent.switch_soul("kthx-trading")
        assert "# Host Environment" in agent._custom_system_prompt

    def test_switch_refreshes_tool_parser(self):
        agent = _make_agent()
        agent.switch_soul("kthx-trading")
        parser_names = set(agent._parser.tool_names)
        assert parser_names == set(agent.tools.names())

    def test_switch_round_trip(self):
        agent = _make_agent()
        agent.switch_soul("kthx-trading")
        agent.switch_soul("kthx-helper")
        assert agent.soul.name == "kthx-helper"
        assert "AgentKthx - LLM Diagnostic" in agent._custom_system_prompt
        assert "# Host Environment" in agent._custom_system_prompt

    def test_switch_level_override(self):
        agent = _make_agent(soul_level=2)
        agent.switch_soul("kthx-trading", level=1)
        assert agent._soul_level == 1
        # Level 1 = manifest only: no SOUL.md persona in the prompt
        assert "SOUL — Kthx Trading Analyst" not in agent._custom_system_prompt
        agent.switch_soul("kthx-trading")  # back to agent level (1)
        assert "SOUL — Kthx Trading Analyst" not in agent._custom_system_prompt
        agent.switch_soul("kthx-trading", level=2)
        assert "SOUL — Kthx Trading Analyst" in agent._custom_system_prompt

    def test_switch_unknown_soul_raises(self):
        agent = _make_agent()
        with pytest.raises((FileNotFoundError, ValueError)):
            agent.switch_soul("no-such-soul-anywhere")

    def test_switch_from_no_soul(self):
        """A default-prompt session (soul=None) can adopt a soul mid-session."""
        agent = _make_agent(soul=None, tools=["calculator"])
        assert agent.soul is None
        agent.switch_soul("kthx-helper")
        assert agent.soul.name == "kthx-helper"
        assert "AgentKthx - LLM Diagnostic" in agent._custom_system_prompt


# ═══════════════════════════════════════════════════════════════════════
# 3. cmd_chat slash-command wiring (source pins)
# ═══════════════════════════════════════════════════════════════════════

CHAT_PATH = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"


class TestChatSlashWiring:
    @pytest.fixture(scope="class")
    def chat_source(self):
        return CHAT_PATH.read_text(encoding="utf-8")

    def test_souls_command_registered(self, chat_source):
        assert 'if user_input == "/souls":' in chat_source

    def test_soul_command_registered_with_arg_form(self, chat_source):
        assert 'if user_input == "/soul" or user_input.startswith("/soul "):' in chat_source

    def test_soul_switch_delegates_to_switch_soul(self, chat_source):
        assert "agent.switch_soul(name)" in chat_source

    def test_help_lists_both_commands(self, chat_source):
        assert "('/souls')" in chat_source
        assert "('/soul')" in chat_source

    def test_skill_handler_tracks_skills_prompt(self, chat_source):
        """Mid-session /skill loads must update _skills_prompt for /soul rebuilds."""
        assert "agent._skills_prompt" in chat_source

    def test_switch_soul_exists_on_mixin(self):
        from agentkthx.core.agent_setup import AgentSetupMixin

        assert hasattr(AgentSetupMixin, "switch_soul")

    def test_list_souls_exists_on_loader(self):
        from agentkthx.soul import SoulLoader

        assert hasattr(SoulLoader, "list_souls")


# ═══════════════════════════════════════════════════════════════════════
# 4. agentkthx config — env-var reference completeness
# ═══════════════════════════════════════════════════════════════════════

# OS-internal probes / runtime handoff vars — never user configuration.
_ENV_SKIP = {
    "HOME",
    "LOCALAPPDATA",
    "USERPROFILE",
    "WINDIR",
    "WT_SESSION",
    "TERM_PROGRAM",
    "MINTTY",
    "ANSICON",
    # Written by the backends at runtime (SEC-15 first-instance-wins), read
    # once for a debug gate — internal plumbing, not a user setting.
    "AGENTKTHX_API_MODE",
    # Test-suite live gate, not a runtime variable.
    "GEMINI_RUN_LIVE_TESTS",
}
# Alias fallbacks already documented inside their primary entry's description
# (e.g. "GEMINI_API_KEY ... (or GOOGLE_API_KEY)").
_ENV_ALIASES = {"GOOGLE_API_KEY", "HF_API_KEY", "HUGGING_FACE_HUB_TOKEN"}


def _run_cmd_config(full=False, urls=False) -> str:
    from agentkthx.cli.commands.config import cmd_config

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_config(argparse_namespace(full=full, urls=urls))
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def argparse_namespace(full: bool, urls: bool):
    import argparse

    return argparse.Namespace(full=full, urls=urls)


class TestConfigEnvReference:
    @pytest.fixture(scope="class")
    def reference_output(self):
        return _run_cmd_config()

    @pytest.mark.parametrize(
        "var",
        [
            # R07.19 core vars that had never been listed
            "AGENTKTHX_USER",
            "AGENTKTHX_NO_ENV_PROBE",
            "AGENTKTHX_NO_UPDATE_CHECK",
            "AGENTKTHX_MAX_API_RETRIES",
            "AGENTKTHX_PARALLEL_TOOLS",
            "AGENTKTHX_USER_AGENT",
            "AGENTKTHX_ACP",
            "AGENTKTHX_ACP_URL",
            "AGENTKTHX_PLUGIN_PATH",
            # Plugin vars that were entirely missing
            "MISTRAL_BASE_URL",
            "MISTRAL_API_KEY",
            "MISTRAL_DEFAULT_MODEL",
            "MISTRAL_FREE_ONLY",
            "MISTRAL_FREE_FALLBACK_MODEL",
            "MISTRAL_SAFE_PROMPT",
            "MISTRAL_SERVICE_TIER",
            "MISTRAL_MAX_RETRIES",
            "ORCAROUTER_BASE_URL",
            "ORCAROUTER_API_KEY",
            "ORCAROUTER_DEFAULT_MODEL",
            "ORCAROUTER_FREE_ONLY",
            "ORCAROUTER_FREE_FALLBACK_MODEL",
            "ORCAROUTER_FALLBACK_MODELS",
            "ORCAROUTER_INCLUDE_COST",
            "POLLINATIONS_BASE_URL",
            "POLLINATIONS_API_KEY",
            "POLLINATIONS_DEFAULT_MODEL",
            "POLLINATIONS_FALLBACK_MODEL",
            "POLLINATIONS_SAFE",
            "POLLINATIONS_FREE_ONLY",
            "POLLINATIONS_ANON_CATALOG",
            "POLLINATIONS_MAX_RETRIES",
            # Assorted gaps
            "OPENAI_MAX_429_RETRIES",
            "OLLAMA_MODELS",
            "HF_BASE_URL_LEGACY",
            # Display / platform
            "NO_COLOR",
            "CLICOLOR",
            "CLICOLOR_FORCE",
            "AGENTKTHX_GLYPHS",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
        ],
    )
    def test_reference_lists_var(self, reference_output, var):
        assert re.search(
            rf"^\s+{var}\s+- ", reference_output, re.M
        ), f"{var} is read by the codebase but missing from 'agentkthx config' reference"

    def test_reference_covers_every_env_var_read(self):
        """Drift pin: any NEW os.environ read must land in the reference.

        Scans agentkthx/ for os.environ.get / os.getenv / os.environ[...]
        literals and demands each (minus the OS-internal skip set and the
        documented alias fallbacks) appears in the printed reference.
        """
        package_root = Path(__file__).resolve().parent.parent / "agentkthx"
        pattern = re.compile(
            r"""(?:os\.environ\.get|os\.getenv|os\.environ\[)\(\s*["']([A-Z0-9_]+)["']"""
        )
        read_vars = set()
        for py in package_root.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            read_vars |= set(pattern.findall(py.read_text(encoding="utf-8", errors="ignore")))

        listed = set(re.findall(r"^\s+([A-Z][A-Z0-9_]{2,})\s+- ", _run_cmd_config(), re.M))
        missing = sorted((read_vars - listed) - _ENV_SKIP - _ENV_ALIASES)
        assert not missing, f"env vars read but not documented in config: {missing}"

    def test_urls_dump_includes_new_backends(self):
        out = _run_cmd_config(urls=True)
        assert "MISTRAL_BASE_URL=" in out
        assert "ORCAROUTER_BASE_URL=" in out
        assert "POLLINATIONS_BASE_URL=" in out

    def test_full_dump_has_plugin_sections(self):
        out = _run_cmd_config(full=True)
        for section, marker in (
            ("Mistral", "MISTRAL_DEFAULT_MODEL:"),
            ("OrcaRouter", "ORCAROUTER_DEFAULT_MODEL:"),
            ("Pollinations", "POLLINATIONS_DEFAULT_MODEL:"),
        ):
            assert section in out, f"--full dump lost the {section} section"
            assert marker in out, f"--full dump lost {marker}"
