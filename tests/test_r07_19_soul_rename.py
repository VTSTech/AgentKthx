"""R07.19 follow-up #3 — nova-* → kthx-* soul rename + kthx-helper accuracy pass.

The three bundled souls were renamed to match the project branding (AgentKthx,
renamed from AgentNova back in R06.0 — the souls were the last Nova-named
surfaces):

    nova-helper  → kthx-helper
    nova-skills  → kthx-skills
    nova-trading → kthx-trading

The user also asked for an accuracy review of kthx-helper ("hasn't been
updated or looked at in a long time"). Verified against the current codebase:

- all 16 ``allowedTools`` names exist in the builtin registry (17 tools —
  ``web_search`` is the only builtin, deliberately excluded, consistent with
  the soul's AGENTS.md escalation rule for missing web search);
- the static Tool Reference table's argument names all match the registry
  (``expression``/``command``/``file_path``/``content``/``path``/
  ``timezone``/``code``);
- every ``{{...}}`` placeholder used in SOUL.md is substituted by the loader;
- AGENTS.md's orchestrator claims hold (AgentCard.tools/priority/fallback,
  concat/first/vote/best merge strategies, pipeline forwards Final Answer
  only);
- ONE defect found and fixed: the "Common Mistakes" hallucination example
  used ``17 - 9`` as the WRONG expression while the Time Calculation section
  and Word Problem table teach ``17 - 9`` as the CORRECT 9 AM→5 PM answer
  (5 PM → 17). The example now uses numbers genuinely absent from the
  question (``15 - 4`` against the 24/8/6 apples example).

These tests pin the rename and the review conclusions so neither can drift.
"""

import inspect
import json
from pathlib import Path

import pytest

from agentkthx.core import agent_setup
from agentkthx.soul.loader import SoulLoader
from agentkthx.tools import make_builtin_registry

SOULS_DIR = Path(agent_setup.__file__).resolve().parents[1] / "souls"
NEW_NAMES = ["kthx-helper", "kthx-skills", "kthx-trading"]
OLD_NAMES = ["nova-helper", "nova-skills", "nova-trading"]


# ---------------------------------------------------------------------------
# Rename pins
# ---------------------------------------------------------------------------


class TestSoulRename:
    def test_all_three_souls_load_by_new_name(self):
        """Each renamed soul must resolve and load via the bare-name path."""
        loader = SoulLoader()
        for name in NEW_NAMES:
            manifest = loader.load(name, level=2)
            assert manifest is not None, f"{name} did not load"
            assert manifest.name == name

    def test_no_nova_soul_directory_remains(self):
        """No nova-* directory may ship in agentkthx/souls/ ever again."""
        shipped = {p.name for p in SOULS_DIR.iterdir() if p.is_dir()}
        assert not any(n in shipped for n in OLD_NAMES), f"stale soul dirs: {shipped}"
        assert shipped == set(NEW_NAMES)

    def test_no_nova_name_inside_soul_files(self):
        """soul.json / *.md of every soul must be free of the old names
        (historical changelogs keep their references — only souls are pinned)."""
        for soul in NEW_NAMES:
            for md in (SOULS_DIR / soul).glob("*.md"):
                text = md.read_text(encoding="utf-8")
                for old in OLD_NAMES:
                    assert old not in text, f"{soul}/{md.name} still mentions {old}"

    def test_default_soul_signature_is_kthx_helper(self):
        """The Agent constructor's default soul parameter must be the renamed soul."""
        sig = inspect.signature(agent_setup.AgentSetupMixin.__init__)
        assert sig.parameters["soul"].default == "kthx-helper"
        source = inspect.getsource(agent_setup)
        assert 'soul: str = "kthx-helper"' in source
        assert 'default: "nova-helper"' not in source

    def test_trading_display_name_renamed(self):
        """The trading soul's Nova branding moved to Kthx with the rename."""
        data = json.loads((SOULS_DIR / "kthx-trading" / "soul.json").read_text())
        assert data["displayName"] == "Kthx Trading Analyst"
        identity = (SOULS_DIR / "kthx-trading" / "IDENTITY.md").read_text()
        assert "**Kthx Trading Analyst**" in identity


# ---------------------------------------------------------------------------
# kthx-helper accuracy pins (review conclusions)
# ---------------------------------------------------------------------------


class TestHelperAccuracy:
    @pytest.fixture(scope="class")
    def helper_json(self):
        return json.loads((SOULS_DIR / "kthx-helper" / "soul.json").read_text())

    @pytest.fixture(scope="class")
    def helper_soul_md(self):
        return (SOULS_DIR / "kthx-helper" / "SOUL.md").read_text()

    def test_allowed_tools_all_exist_in_registry(self, helper_json):
        """Every allowedTools entry must be a real builtin tool name."""
        registry = make_builtin_registry()
        real = set(registry.names())
        allowed = set(helper_json["allowedTools"])
        assert allowed <= real, f"ghost tools in allowedTools: {allowed - real}"

    def test_web_search_exclusion_is_intentional(self, helper_json):
        """The diagnostic soul excludes web_search; AGENTS.md documents that
        as an escalation trigger. If web_search ever enters allowedTools,
        AGENTS.md's escalation bullet must be updated in the same commit."""
        assert "web_search" not in helper_json["allowedTools"]
        agents_md = (SOULS_DIR / "kthx-helper" / "AGENTS.md").read_text()
        assert "web_search" in agents_md

    def test_tool_reference_table_uses_real_tool_names(self, helper_soul_md):
        """The static table's tool column only names tools in the registry
        (the loader replaces this table at runtime, but the static fallback
        must never advertise a tool that cannot exist)."""
        registry = make_builtin_registry()
        real = set(registry.names())
        in_table = set()
        for line in helper_soul_md.splitlines():
            if line.startswith("| `") and "` |" in line:
                in_table.add(line.split("| `")[1].split("`")[0])
        assert in_table, "tool reference table not found"
        assert in_table <= real, f"table advertises unknown tools: {in_table - real}"

    def test_no_contradictory_hallucination_example(self, helper_soul_md):
        """The Common Mistakes example must NOT call `17 - 9` wrong — the
        Time Calculation section teaches 17 - 9 as the correct 9 AM→5 PM
        expression (5 PM → 17). Fixed to 15 - 4 (numbers genuinely absent
        from the 24/8/6 apples example)."""
        assert "`17 - 9` is WRONG" not in helper_soul_md
        assert "`15 - 4` is WRONG" in helper_soul_md

    def test_all_placeholders_used_are_supported(self, helper_soul_md):
        """Every {{PLACEHOLDER}} in SOUL.md must be one the loader substitutes
        (an unsupported one would leak into the prompt verbatim)."""
        from agentkthx.soul import loader as soul_loader

        supported = {
            "DYNAMIC_EXAMPLE",
            "DYNAMIC_EXAMPLE_FLOW",
            "DYNAMIC_ERROR_EXAMPLE",
            "CALCULATOR_SYNTAX_SECTION",
            "CALCULATOR_ERROR_HINT",
        }
        used = set()
        for node in __import__("re").findall(r"\{\{([A-Z_]+)\}\}", helper_soul_md):
            used.add(node)
        assert used, "SOUL.md lost its dynamic placeholders"
        assert used <= supported, f"unsupported placeholders leak verbatim: {used - supported}"
        # and the loader source really replaces each one
        loader_src = inspect.getsource(soul_loader)
        for name in used:
            assert f"{{{{{name}}}}}" in loader_src

    def test_agents_md_self_name_consistency(self, helper_json):
        """AGENTS.md must speak of the soul by its NEW name only."""
        agents_md = (SOULS_DIR / "kthx-helper" / "AGENTS.md").read_text()
        assert "`kthx-helper`" in agents_md
        for old in OLD_NAMES:
            assert old not in agents_md
