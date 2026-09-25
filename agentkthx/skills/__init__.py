"""
⚛️ AgentKthx R02 - Skills Module

Agent Skills support for AgentKthx.
Loads skills from SKILL.md files following the Agent Skills specification.

Specification: https://agentskills.io/

Usage:
    from agentkthx.skills import SkillLoader, SkillRegistry
    
    loader = SkillLoader()
    skill = loader.load("calculator")
    
    registry = SkillRegistry()
    registry.add(skill)
    system_prompt += registry.to_system_prompt_addition()

Written by VTSTech — https://www.vts-tech.org — https://github.com/VTSTech/AgentKthx
"""

from .loader import (
    SkillLoader,
    Skill,
    SkillRegistry,
    SPDX_LICENSES,
    validate_spdx_license,
    parse_compatibility,
)

__all__ = [
    "SkillLoader",
    "Skill",
    "SkillRegistry",
    "SPDX_LICENSES",
    "validate_spdx_license",
    "parse_compatibility",
]