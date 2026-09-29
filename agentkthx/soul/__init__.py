"""
⚛️ AgentKthx — Soul Spec Module

Support for ClawSouls Soul Spec v0.5 persona packages.

This module is DISABLED by default. Enable with --soul flag.

Written by VTSTech — https://www.vts-tech.org

Example:
    from agentkthx.soul import load_soul, build_system_prompt

    # Load a soul package
    soul = load_soul("/path/to/soul/package")

    # Build system prompt for the agent
    system_prompt = build_system_prompt(soul, level=2)

    # Use with Agent
    agent = Agent(model="qwen2.5", system_prompt=system_prompt)
"""

from .loader import (
    SoulLoader,
    build_system_prompt,
    build_system_prompt_with_tools,
    get_soul_loader,
    load_soul,
)
from .types import (
    # Constants
    ALLOWED_LICENSES,
    KNOWN_FRAMEWORKS,
    Actuator,
    Author,
    Compatibility,
    ContactPolicy,
    Disclosure,
    # Embodied agent types
    Environment,
    HardwareConstraints,
    InteractionMode,
    Mobility,
    PhysicalSafety,
    # Skill types
    RecommendedSkill,
    Safety,
    Sensor,
    SoulExamples,
    SoulFiles,
    # Core types
    SoulManifest,
    # Helpers
    parse_legacy_skills,
)

__all__ = [
    # Core types
    "SoulManifest",
    "Author",
    "Compatibility",
    "SoulFiles",
    "SoulExamples",
    "Disclosure",
    # Skill types
    "RecommendedSkill",
    # Embodied agent types
    "Environment",
    "InteractionMode",
    "HardwareConstraints",
    "PhysicalSafety",
    "Safety",
    "Sensor",
    "Actuator",
    "Mobility",
    "ContactPolicy",
    # Constants
    "ALLOWED_LICENSES",
    "KNOWN_FRAMEWORKS",
    # Helpers
    "parse_legacy_skills",
    # Loader
    "SoulLoader",
    "load_soul",
    "build_system_prompt",
    "build_system_prompt_with_tools",
    "get_soul_loader",
]
