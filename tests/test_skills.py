"""
⚛️ AgentKthx — Skill Loader & Registry Tests

Tests for the AgentSkills system: SkillLoader, SkillRegistry, Skill dataclass,
SPDX validation, compatibility parsing, and system prompt generation.

These tests require NO model — they test the skill infrastructure directly.
"""

import os
import tempfile
import pytest
from pathlib import Path

from agentkthx.skills import (
    SkillLoader,
    Skill,
    SkillRegistry,
    validate_spdx_license,
    parse_compatibility,
    SPDX_LICENSES,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skills directory with sample skills."""
    skills = tmp_path / "skills"
    skills.mkdir()

    # Valid skill: acp-like
    acp_dir = skills / "acp"
    acp_dir.mkdir()
    (acp_dir / "SKILL.md").write_text(
        "---\n"
        "name: acp\n"
        "description: Agent Control Panel for monitoring and control of AI agents.\n"
        "license: MIT\n"
        "compatibility: ollama\n"
        "allowed-tools: calculator shell\n"
        "metadata:\n"
        "  version: 1.0\n"
        "---\n\n"
        "# ACP Skill\n\n"
        "This is the ACP skill content.\n"
        "Boot strap the agent control panel.\n"
    )

    # Valid skill: minimal (no optional fields)
    minimal_dir = skills / "minimal"
    minimal_dir.mkdir()
    (minimal_dir / "SKILL.md").write_text(
        "---\n"
        "name: minimal\n"
        "description: A minimal skill with no optional fields.\n"
        "---\n\n"
        "# Minimal Skill\n\n"
        "Just instructions.\n"
    )

    return skills


@pytest.fixture
def loader(skills_dir):
    """Create a SkillLoader pointing at the test skills directory."""
    return SkillLoader(skills_dir)


# ============================================================================
# SPDX License Validation Tests
# ============================================================================

class TestSPDXValidation:
    """Test validate_spdx_license() function."""

    def test_valid_mit(self):
        valid, msg = validate_spdx_license("MIT")
        assert valid is True
        assert "MIT" in msg

    def test_valid_apache(self):
        valid, msg = validate_spdx_license("Apache-2.0")
        assert valid is True

    def test_valid_gpl(self):
        valid, msg = validate_spdx_license("GPL-3.0")
        assert valid is True

    def test_valid_cc0(self):
        valid, msg = validate_spdx_license("CC0-1.0")
        assert valid is True

    def test_valid_with_exception(self):
        valid, msg = validate_spdx_license("Apache-2.0 WITH LLVM-exception")
        assert valid is True
        assert "exception" in msg

    def test_unknown_license(self):
        valid, msg = validate_spdx_license("SomeFakeLicense-1.0")
        assert valid is False
        assert "Unknown" in msg

    def test_empty_license(self):
        valid, msg = validate_spdx_license("")
        assert valid is True
        assert "No license" in msg

    def test_case_insensitive_proprietary(self):
        valid, msg = validate_spdx_license("PROPRIETARY")
        assert valid is True

    def test_or_combination(self):
        valid, msg = validate_spdx_license("MIT OR Apache-2.0")
        assert valid is True
        assert "Complex" in msg

    def test_all_spdx_in_set(self):
        """Every identifier in SPDX_LICENSES should validate."""
        for lic in SPDX_LICENSES:
            valid, _ = validate_spdx_license(lic)
            assert valid is True, f"SPDX_LICENSES contains invalid entry: {lic}"


# ============================================================================
# Compatibility Parsing Tests
# ============================================================================

class TestCompatibilityParsing:
    """Test parse_compatibility() function."""

    def test_python_version(self):
        result = parse_compatibility("python>=3.8")
        assert result["python"] is not None
        assert result["python"]["min_version"] == "3.8"
        assert result["python"]["operator"] == ">="

    def test_runtimes(self):
        result = parse_compatibility("ollama, openai")
        assert len(result["runtimes"]) == 2
        assert result["runtimes"][0]["name"] == "ollama"

    def test_combined(self):
        result = parse_compatibility("python>=3.8, ollama")
        assert result["python"]["min_version"] == "3.8"
        assert len(result["runtimes"]) == 1

    def test_empty(self):
        result = parse_compatibility("")
        assert result["python"] is None
        assert result["runtimes"] == []
        assert result["frameworks"] == []

    def test_raw_preserved(self):
        result = parse_compatibility("python>=3.10, ollama, openai")
        assert result["raw"] == "python>=3.10, ollama, openai"


# ============================================================================
# Skill Dataclass Tests
# ============================================================================

class TestSkill:
    """Test Skill dataclass validation."""

    def test_valid_skill_creation(self):
        skill = Skill(
            name="test-skill",
            description="A test skill for unit testing.",
            instructions="Do things.",
            path=Path("/tmp/test-skill"),
        )
        assert skill.name == "test-skill"
        assert skill.description == "A test skill for unit testing."
        assert skill.license is None
        assert skill.license_valid is True
        assert skill.allowed_tools == []

    def test_skill_with_all_fields(self):
        skill = Skill(
            name="full-skill",
            description="A skill with all fields populated.",
            instructions="Instructions here.",
            path=Path("/tmp/full-skill"),
            license="MIT",
            compatibility="python>=3.8, ollama",
            allowed_tools=["calculator", "shell"],
            metadata={"version": "1.0", "author": "test"},
        )
        assert skill.name == "full-skill"
        assert skill.license == "MIT"
        assert skill.license_valid is True
        assert len(skill.allowed_tools) == 2
        assert skill.metadata["version"] == "1.0"
        assert skill.compatibility_info["python"]["min_version"] == "3.8"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="required"):
            Skill(name="", description="desc", instructions="body", path=Path("/tmp/x"))

    def test_invalid_name_uppercase_raises(self):
        with pytest.raises(ValueError, match="Invalid skill name"):
            Skill(name="MySkill", description="desc", instructions="body", path=Path("/tmp/x"))

    def test_invalid_name_leading_hyphen_raises(self):
        with pytest.raises(ValueError, match="Invalid skill name"):
            Skill(name="-skill", description="desc", instructions="body", path=Path("/tmp/x"))

    def test_invalid_name_consecutive_hyphens_raises(self):
        with pytest.raises(ValueError, match="Invalid skill name"):
            Skill(name="skill--name", description="desc", instructions="body", path=Path("/tmp/x"))

    def test_name_too_long_raises(self):
        long_name = "a" * 65
        with pytest.raises(ValueError, match="too long"):
            Skill(name=long_name, description="desc", instructions="body", path=Path("/tmp/x"))

    def test_empty_description_raises(self):
        with pytest.raises(ValueError, match="description is required"):
            Skill(name="test", description="", instructions="body", path=Path("/tmp/x"))

    def test_description_too_long_raises(self):
        long_desc = "A" * 1025
        with pytest.raises(ValueError, match="too long"):
            Skill(name="test", description=long_desc, instructions="body", path=Path("/tmp/x"))

    def test_unknown_license_warns_not_fails(self):
        skill = Skill(
            name="test", description="desc", instructions="body", path=Path("/tmp/x"),
            license="FakeLicense",
        )
        assert skill.license_valid is False
        assert "Unknown" in skill.license_warning


# ============================================================================
# SkillLoader Tests
# ============================================================================

class TestSkillLoader:
    """Test SkillLoader class."""

    def test_list_skills(self, skills_dir):
        sl = SkillLoader(skills_dir)
        skills = sl.list_skills()
        assert "acp" in skills
        assert "minimal" in skills

    def test_list_skills_empty_dir(self, tmp_path):
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        sl = SkillLoader(empty_dir)
        assert sl.list_skills() == []

    def test_list_skills_no_skill_md(self, tmp_path):
        no_skill_dir = tmp_path / "no_skill_md"
        no_skill_dir.mkdir()
        (no_skill_dir / "README.md").write_text("# Not a skill")
        sl = SkillLoader(no_skill_dir)
        assert sl.list_skills() == []

    def test_load_valid_skill(self, loader):
        skill = loader.load("acp")
        assert skill.name == "acp"
        assert "Control Panel" in skill.description
        assert "ACP Skill" in skill.instructions
        assert skill.license == "MIT"
        assert "ollama" in skill.compatibility

    def test_load_skill_caches(self, loader):
        skill1 = loader.load("acp")
        skill2 = loader.load("acp")
        assert skill1 is skill2  # Same object from cache

    def test_load_nonexistent_raises(self, loader):
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent")

    def test_load_skill_name_mismatch_raises(self, tmp_path):
        """Skill name in frontmatter must match directory name."""
        bad_dir = tmp_path / "bad-name"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text(
            "---\n"
            "name: wrong-name\n"
            "description: Mismatched name.\n"
            "---\n\n"
        )
        sl = SkillLoader(tmp_path)
        with pytest.raises(ValueError, match="doesn't match"):
            sl.load("bad-name")

    def test_load_all(self, loader):
        skills = loader.load_all()
        assert isinstance(skills, dict)
        assert len(skills) >= 2
        assert "acp" in skills
        assert "minimal" in skills
        # Values are Skill objects
        assert skills["acp"].name == "acp"
        assert skills["minimal"].name == "minimal"

    def test_get_skill_descriptions(self, loader):
        descs = loader.get_skill_descriptions()
        assert isinstance(descs, dict)
        assert "acp" in descs

    def test_clear_cache(self, loader):
        loader.load("acp")
        assert loader.is_cached("acp")
        loader.clear_cache()
        assert not loader.is_cached("acp")

    def test_invalidate_cache(self, loader):
        loader.load("acp")
        assert loader.is_cached("acp")
        loader.invalidate("acp")
        assert not loader.is_cached("acp")

    def test_get_cache_stats(self, loader):
        loader.load("acp")
        loader.load("minimal")
        stats = loader.get_cache_stats()
        assert stats["cache_size"] == 2
        assert "acp" in stats["cached_skills"]
        assert "minimal" in stats["cached_skills"]


# ============================================================================
# SkillRegistry Tests
# ============================================================================

class TestSkillRegistry:
    """Test SkillRegistry class."""

    def test_empty_registry(self):
        reg = SkillRegistry()
        assert reg.list() == []
        assert reg.to_system_prompt_addition() == ""

    def test_add_and_list(self, skills_dir):
        loader = SkillLoader(skills_dir)
        skill = loader.load("acp")
        reg = SkillRegistry()
        reg.add(skill)
        assert "acp" in reg.list()
        assert "acp" in reg.to_system_prompt_addition()

    def test_add_multiple(self, skills_dir):
        loader = SkillLoader(skills_dir)
        acp = loader.load("acp")
        minimal = loader.load("minimal")
        reg = SkillRegistry()
        reg.add(acp)
        reg.add(minimal)
        assert len(reg.list()) == 2

    def test_remove(self, skills_dir):
        loader = SkillLoader(skills_dir)
        skill = loader.load("acp")
        reg = SkillRegistry()
        reg.add(skill)
        reg.remove("acp")
        assert "acp" not in reg.list()

    def test_get(self, skills_dir):
        loader = SkillLoader(skills_dir)
        skill = loader.load("acp")
        reg = SkillRegistry()
        reg.add(skill)
        assert reg.get("acp") is skill
        assert reg.get("nonexistent") is None

    def test_has(self, skills_dir):
        loader = SkillLoader(skills_dir)
        skill = loader.load("acp")
        reg = SkillRegistry()
        reg.add(skill)
        assert reg.has("acp") is True
        assert reg.has("nope") is False

    def test_to_system_prompt_addition_format(self, skills_dir):
        loader = SkillLoader(skills_dir)
        acp = loader.load("acp")
        reg = SkillRegistry()
        reg.add(acp)

        prompt = reg.to_system_prompt_addition()

        assert "# ⚡ ACTIVE SKILLS" in prompt
        assert "acp" in prompt
        assert "ACP Skill" in prompt
        assert "instructions for using skills" in prompt.lower()

    def test_to_system_prompt_addition_multiple(self, skills_dir):
        loader = SkillLoader(skills_dir)
        acp = loader.load("acp")
        minimal = loader.load("minimal")
        reg = SkillRegistry()
        reg.add(acp)
        reg.add(minimal)

        prompt = reg.to_system_prompt_addition()
        assert "acp" in prompt
        assert "minimal" in prompt

    def test_to_system_prompt_empty(self):
        reg = SkillRegistry()
        assert reg.to_system_prompt_addition() == ""


# ============================================================================
# Integration: Real Built-in Skills
# ============================================================================

class TestBuiltinSkills:
    """Test loading of actual built-in skills shipped with AgentKthx."""

    def test_builtin_skills_dir_exists(self):
        """The built-in skills directory should exist."""
        from agentkthx.skills.loader import SkillLoader
        # Use the default skills dir
        default = Path(__file__).parent.parent / "skills"
        if default.exists():
            assert default.is_dir()

    def test_load_acp_skill(self):
        """ACP skill should load successfully."""
        from agentkthx.skills.loader import SkillLoader
        skills_dir = Path(__file__).parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("Built-in skills directory not found")
        sl = SkillLoader(skills_dir)
        skill = sl.load("acp")
        assert skill.name == "acp"
        assert len(skill.description) > 0
        assert len(skill.instructions) > 0

    def test_load_skill_creator_skill(self):
        """skill-creator skill should load successfully."""
        from agentkthx.skills.loader import SkillLoader
        skills_dir = Path(__file__).parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("Built-in skills directory not found")
        sl = SkillLoader(skills_dir)
        skill = sl.load("skill-creator")
        assert skill.name == "skill-creator"
        assert len(skill.description) > 0

    def test_removed_skills_not_present(self):
        """datetime and web-search skills should NOT be present."""
        from agentkthx.skills.loader import SkillLoader
        skills_dir = Path(__file__).parent.parent / "skills"
        if not skills_dir.exists():
            pytest.skip("Built-in skills directory not found")
        sl = SkillLoader(skills_dir)
        skills = sl.list_skills()
        assert "datetime" not in skills, "datetime skill should be removed"
        assert "web-search" not in skills, "web-search skill should be removed"


# ============================================================================
# web-search Tool Tests
# ============================================================================

class TestWebSearchTool:
    """Test the web_search tool function."""

    def test_import(self):
        """web_search should be importable from builtins."""
        from agentkthx.tools.builtins import web_search
        assert callable(web_search)

    def test_web_search_in_registry(self):
        """web-search tool should be registered in the builtin registry."""
        from agentkthx.tools.builtins import BUILTIN_REGISTRY
        assert BUILTIN_REGISTRY.get("web-search") is not None

    def test_web_search_description(self):
        """web-search tool should have a meaningful description."""
        from agentkthx.tools.builtins import BUILTIN_REGISTRY
        tool = BUILTIN_REGISTRY.get("web-search")
        assert "search" in tool.description.lower() or "search" in tool.description.lower()

    def test_web_search_params(self):
        """web-search tool should have query and num_results params."""
        from agentkthx.tools.builtins import BUILTIN_REGISTRY
        tool = BUILTIN_REGISTRY.get("web-search")
        param_names = [p.name for p in tool.params]
        assert "query" in param_names
        assert "num_results" in param_names
