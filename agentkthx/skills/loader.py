"""
⚛️ AgentKthx R02 - Skills Loader

Implements the Agent Skills specification for loading SKILL.md files.
See: https://agentskills.io/

Written by VTSTech — https://www.vts-tech.org — https://github.com/VTSTech/AgentKthx
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Any, Set


# Common SPDX license identifiers (subset of most common ones)
# Full list: https://spdx.org/licenses/
SPDX_LICENSES: Set[str] = {
    # OSI Approved
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
    "LGPL-2.1", "LGPL-3.0", "MPL-2.0", "ISC", "0BSD", "Artistic-2.0",
    "EPL-1.0", "EPL-2.0", "CDDL-1.0", "CDDL-1.1",
    # Creative Commons
    "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    # Public Domain
    "Unlicense", "WTFPL",
    # Proprietary
    "Proprietary", "Commercial",
    # Common variations
    "MIT-0", "Apache-2.0 WITH LLVM-exception",
}


def validate_spdx_license(license_str: str) -> tuple[bool, str]:
    """
    Validate a license string against SPDX identifiers.
    
    Args:
        license_str: License identifier to validate
        
    Returns:
        Tuple of (is_valid, message)
    """
    if not license_str:
        return True, "No license specified"
    
    # Normalize whitespace and case
    normalized = license_str.strip()
    
    # Check for exact match
    if normalized in SPDX_LICENSES:
        return True, f"Valid SPDX identifier: {normalized}"
    
    # Check for WITH exception (e.g., "Apache-2.0 WITH LLVM-exception")
    if " WITH " in normalized:
        parts = normalized.split(" WITH ")
        if len(parts) == 2 and parts[0] in SPDX_LICENSES:
            return True, f"Valid SPDX identifier with exception: {normalized}"
    
    # Check for OR/AND combinations
    if " OR " in normalized or " AND " in normalized:
        # Complex expression - just warn but accept
        return True, f"Complex license expression (not fully validated): {normalized}"
    
    # Check for common non-SPDX patterns that are acceptable
    if normalized.lower().startswith("proprietary") or normalized.lower() == "all rights reserved":
        return True, f"Proprietary license: {normalized}"
    
    # Unknown license - warn but don't fail
    return False, f"Unknown license identifier (not in SPDX list): {normalized}"


def parse_compatibility(compatibility_str: str) -> Dict[str, Any]:
    """
    Parse a compatibility string into structured requirements.
    
    Supports formats:
        - "python>=3.8"
        - "python>=3.8, node>=16"
        - "agentnova>=1.0"
        - "ollama, openai"
        
    Args:
        compatibility_str: Compatibility requirements string
        
    Returns:
        Dict with parsed requirements:
        {
            "python": {"min_version": "3.8"},
            "runtimes": ["ollama", "openai"],
            "frameworks": ["agentnova"],
            "raw": "python>=3.8, ollama"
        }
    """
    result = {
        "python": None,
        "runtimes": [],
        "frameworks": [],
        "raw": compatibility_str
    }
    
    if not compatibility_str:
        return result
    
    # Split by comma
    parts = [p.strip() for p in compatibility_str.split(",")]
    
    for part in parts:
        # Check for version specifier
        version_match = re.match(r'^(\w+)\s*(>=|<=|>|<|==|!=)\s*([\d.]+)$', part)
        if version_match:
            name, op, version = version_match.groups()
            name_lower = name.lower()
            
            if name_lower == "python":
                result["python"] = {"min_version": version, "operator": op}
            elif name_lower in ("agentnova", "langchain", "llamaindex"):
                result["frameworks"].append({"name": name, "min_version": version, "operator": op})
            else:
                result["runtimes"].append({"name": name, "min_version": version, "operator": op})
        else:
            # Just a name (no version)
            name_lower = part.lower()
            if name_lower in ("ollama", "openai", "anthropic", "groq", "vllm"):
                result["runtimes"].append({"name": part})
            elif name_lower in ("agentnova", "langchain", "llamaindex"):
                result["frameworks"].append({"name": part})
    
    return result


@dataclass
class Skill:
    """
    Represents a loaded skill following the Agent Skills specification.
    
    Attributes:
        name: Skill identifier (1-64 chars, lowercase, hyphens only)
        description: What the skill does and when to use it (1-1024 chars)
        instructions: The Markdown body after frontmatter
        path: Path to the skill directory
        license: Optional license information (SPDX identifier preferred)
        compatibility: Optional environment requirements
        metadata: Optional additional metadata
        allowed_tools: Optional list of pre-approved tools
    """
    name: str
    description: str
    instructions: str
    path: Path
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    allowed_tools: List[str] = field(default_factory=list)
    _license_valid: bool = field(default=False, repr=False)
    _license_warning: str = field(default="", repr=False)
    _compatibility_parsed: Dict[str, Any] = field(default_factory=dict, repr=False)
    
    def __post_init__(self):
        """Validate skill fields after initialization."""
        self._validate_name()
        self._validate_description()
        self._validate_license()
        self._parse_compatibility()
    
    def _validate_name(self):
        """Validate the name field follows Agent Skills spec."""
        if not self.name:
            raise ValueError("Skill name is required")
        if len(self.name) > 64:
            raise ValueError(f"Skill name too long: {len(self.name)} chars (max 64)")
        if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', self.name):
            raise ValueError(
                f"Invalid skill name '{self.name}': must be lowercase, "
                "alphanumeric with hyphens, no leading/trailing/consecutive hyphens"
            )
    
    def _validate_description(self):
        """Validate the description field follows Agent Skills spec.
        
        Spec requirement: 1-1024 characters describing what the skill does.
        """
        if not self.description:
            raise ValueError("Skill description is required")
        if len(self.description) > 1024:
            raise ValueError(
                f"Skill description too long: {len(self.description)} chars (max 1024)"
            )
    
    def _validate_license(self):
        """Validate and cache the license field.
        
        Validates against SPDX identifiers but does not fail on unknown licenses.
        """
        if self.license:
            is_valid, msg = validate_spdx_license(self.license)
            self._license_valid = is_valid
            self._license_warning = "" if is_valid else msg
        else:
            self._license_valid = True  # No license is acceptable
            self._license_warning = ""
    
    def _parse_compatibility(self):
        """Parse and cache the compatibility field."""
        if self.compatibility:
            self._compatibility_parsed = parse_compatibility(self.compatibility)
        else:
            self._compatibility_parsed = {
                "python": None,
                "runtimes": [],
                "frameworks": [],
                "raw": ""
            }
    
    @property
    def license_valid(self) -> bool:
        """Check if the license is a valid SPDX identifier."""
        return self._license_valid
    
    @property
    def license_warning(self) -> str:
        """Get the license validation warning (empty if valid)."""
        return self._license_warning
    
    @property
    def compatibility_info(self) -> Dict[str, Any]:
        """Get parsed compatibility requirements."""
        return self._compatibility_parsed
    
    def check_compatibility(
        self,
        runtime: str | None = None,
        python_version: str | None = None,
    ) -> tuple[bool, List[str]]:
        """
        Check if the skill is compatible with the given environment.
        
        Args:
            runtime: Runtime name (e.g., "ollama", "openai")
            python_version: Python version string (e.g., "3.10")
            
        Returns:
            Tuple of (is_compatible, warnings_list)
        """
        warnings = []
        
        # Check Python version (zero-dep: tuple comparison on split version strings)
        py_req = self._compatibility_parsed.get("python")
        if py_req and python_version:
            min_ver = py_req.get("min_version", "0")
            op = py_req.get("operator", ">=")
            try:
                py_ver = tuple(int(x) for x in python_version.split(".") if x.isdigit())
                req_ver = tuple(int(x) for x in min_ver.split(".") if x.isdigit())
                if op == ">=" and py_ver < req_ver:
                    warnings.append(f"Python {python_version} < required {min_ver}")
                elif op == ">" and py_ver <= req_ver:
                    warnings.append(f"Python {python_version} <= required >{min_ver}")
            except (ValueError, AttributeError):
                pass
        
        # Check runtime
        runtimes = self._compatibility_parsed.get("runtimes", [])
        if runtimes and runtime:
            runtime_names = [r.get("name", "").lower() for r in runtimes]
            if runtime.lower() not in runtime_names:
                warnings.append(f"Runtime '{runtime}' not in required: {runtime_names}")
        
        return len(warnings) == 0, warnings
    
    @property
    def scripts_dir(self) -> Optional[Path]:
        """Path to scripts directory if it exists."""
        scripts = self.path / "scripts"
        return scripts if scripts.is_dir() else None
    
    @property
    def references_dir(self) -> Optional[Path]:
        """Path to references directory if it exists."""
        refs = self.path / "references"
        return refs if refs.is_dir() else None
    
    @property
    def assets_dir(self) -> Optional[Path]:
        """Path to assets directory if it exists."""
        assets = self.path / "assets"
        return assets if assets.is_dir() else None
    
    def get_script(self, script_name: str) -> Optional[Path]:
        """Get path to a specific script file."""
        if self.scripts_dir:
            script = self.scripts_dir / script_name
            return script if script.exists() else None
        return None
    
    def get_reference(self, ref_name: str) -> Optional[Path]:
        """Get path to a specific reference file."""
        if self.references_dir:
            ref = self.references_dir / ref_name
            return ref if ref.exists() else None
        return None
    
    def get_asset(self, asset_name: str) -> Optional[Path]:
        """Get path to a specific asset file."""
        if self.assets_dir:
            asset = self.assets_dir / asset_name
            return asset if asset.exists() else None
        return None
    
    def to_system_prompt(self) -> str:
        """Convert skill instructions to a system prompt addition."""
        return f"\n\n---\n## Skill: {self.name}\n\n{self.instructions}"
    
    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description[:50]}...)"


class SkillLoader:
    """
    Loads skills from directories containing SKILL.md files.
    
    Follows the Agent Skills specification:
    - SKILL.md with YAML frontmatter (name, description required)
    - Optional scripts/, references/, assets/ directories
    
    Usage:
        loader = SkillLoader("/path/to/skills")
        skill = loader.load("my-skill")
        skills = loader.list_skills()  # Get all available skills
    """
    
    def __init__(self, skills_dir: Optional[str] = None):
        """
        Initialize the skill loader.
        
        Args:
            skills_dir: Directory containing skill folders. 
                       If None, uses 'skills' in same directory as this file.
        """
        if skills_dir:
            self.skills_dir = Path(skills_dir)
        else:
            self.skills_dir = Path(__file__).parent
        
        self._cache: Dict[str, Skill] = {}
    
    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from SKILL.md content.
        
        Returns:
            Tuple of (frontmatter_dict, body_content)
        """
        # Check for frontmatter
        if not content.startswith("---"):
            raise ValueError("SKILL.md must start with YAML frontmatter (---)")
        
        # Find the closing ---
        end_match = re.search(r'\n---\s*\n', content[3:])
        if not end_match:
            raise ValueError("SKILL.md frontmatter not closed (missing ---)")
        
        frontmatter_text = content[3:end_match.start() + 3]
        body = content[end_match.end() + 3:]
        
        # Parse YAML frontmatter (simple parser for the spec format)
        frontmatter: Dict[str, Any] = {}
        current_key = None
        current_value: Any = None

        for line in frontmatter_text.split('\n'):
            stripped = line.strip()

            if not stripped:
                continue

            # Check for key: value
            if ':' in line and not line.startswith(' '):
                # Save previous key
                if current_key:
                    frontmatter[current_key] = current_value

                key, _, value = line.partition(':')
                current_key = key.strip()
                value = value.strip()

                # Handle different value types
                if value.startswith('"') and value.endswith('"'):
                    current_value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    current_value = value[1:-1]
                elif value == '':
                    current_value = None
                else:
                    current_value = value
            elif line.startswith('  ') and current_key:
                # Multiline value (like metadata)
                if current_key not in frontmatter:
                    frontmatter[current_key] = {}
                
                if ':' in line:
                    sub_key, _, sub_value = line.strip().partition(':')
                    sub_value = sub_value.strip()
                    if sub_value.startswith('"') and sub_value.endswith('"'):
                        sub_value = sub_value[1:-1]
                    frontmatter[current_key][sub_key] = sub_value
        
        # Save last key
        if current_key and current_key not in frontmatter:
            frontmatter[current_key] = current_value
        
        return frontmatter, body
    
    def load(self, skill_name: str, use_cache: bool = True) -> Skill:
        """
        Load a skill by name.
        
        Args:
            skill_name: Name of the skill (directory name)
            use_cache: Whether to use cached skill if available
            
        Returns:
            Skill object
            
        Raises:
            FileNotFoundError: If skill directory or SKILL.md doesn't exist
            ValueError: If SKILL.md is invalid
        """
        if use_cache and skill_name in self._cache:
            return self._cache[skill_name]
        
        skill_path = self.skills_dir / skill_name
        if not skill_path.is_dir():
            raise FileNotFoundError(f"Skill directory not found: {skill_path}")
        
        skill_md_path = skill_path / "SKILL.md"
        if not skill_md_path.exists():
            raise FileNotFoundError(f"SKILL.md not found: {skill_md_path}")
        
        # Read and parse SKILL.md
        content = skill_md_path.read_text(encoding='utf-8')
        frontmatter, instructions = self._parse_frontmatter(content)
        
        # Extract required fields
        name = frontmatter.get('name', skill_name)
        description = frontmatter.get('description', '')
        
        if not description:
            raise ValueError(f"Skill '{skill_name}' missing required 'description' field")
        
        # Create Skill object
        skill = Skill(
            name=name,
            description=description,
            instructions=instructions.strip(),
            path=skill_path,
            license=frontmatter.get('license'),
            compatibility=frontmatter.get('compatibility'),
            metadata=frontmatter.get('metadata', {}),
            allowed_tools=frontmatter.get('allowed-tools', '').split() if frontmatter.get('allowed-tools') else []
        )
        
        # Validate name matches directory
        if skill.name != skill_name:
            raise ValueError(
                f"Skill name '{skill.name}' doesn't match directory '{skill_name}'. "
                f"The 'name' field in SKILL.md frontmatter must match the directory name."
            )
        
        self._cache[skill_name] = skill
        return skill
    
    def list_skills(self) -> List[str]:
        """
        List all available skill names.
        
        Returns:
            List of skill names (directory names containing SKILL.md)
        """
        skills = []
        if not self.skills_dir.is_dir():
            return skills
        
        for item in self.skills_dir.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                skills.append(item.name)
        
        return sorted(skills)
    
    def load_all(self) -> Dict[str, Skill]:
        """
        Load all available skills.
        
        Returns:
            Dict mapping skill names to Skill objects
        """
        skills = {}
        for name in self.list_skills():
            try:
                skills[name] = self.load(name)
            except Exception as e:
                print(f"⚠️ Failed to load skill '{name}': {e}")
        
        return skills
    
    def get_skill_descriptions(self) -> Dict[str, str]:
        """
        Get name and description for all skills without loading full instructions.
        Reads only the YAML frontmatter from each SKILL.md for efficiency.

        Returns:
            Dict mapping skill names to their descriptions
        """
        descriptions = {}
        for name in self.list_skills():
            skill_md = self.skills_dir / name / "SKILL.md"
            if not skill_md.exists():
                continue
            # Return from cache if already loaded
            if name in self._cache:
                descriptions[name] = self._cache[name].description
                continue
            try:
                content = skill_md.read_text(encoding='utf-8')
                frontmatter, _ = self._parse_frontmatter(content)
                descriptions[name] = frontmatter.get('description', 'No description')
            except Exception:
                pass
        return descriptions



    # ========================================================================
    # Cache Management (v1.0 - 100% Spec Compliance)
    # ========================================================================
    
    def clear_cache(self) -> int:
        """
        Clear the entire skill cache.
        
        This is useful when skill files have been modified externally
        and you need to force a fresh load on next access.
        
        Returns:
            Number of cache entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        return count
    
    def invalidate(self, skill_name: str) -> bool:
        """
        Invalidate a specific skill from the cache.
        
        Use this when a skill file has been modified and you want to
        force a fresh load on next access, without clearing the entire cache.
        
        Args:
            skill_name: Name of the skill to invalidate
            
        Returns:
            True if the skill was in cache and removed, False otherwise
        """
        if skill_name in self._cache:
            del self._cache[skill_name]
            return True
        return False
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring and debugging.
        
        Returns:
            Dict with cache statistics:
            - cached_skills: List of cached skill names
            - cache_size: Number of cached skills
            - skills_dir: Path to skills directory
        """
        return {
            "cached_skills": list(self._cache.keys()),
            "cache_size": len(self._cache),
            "skills_dir": str(self.skills_dir),
        }
    
    def is_cached(self, skill_name: str) -> bool:
        """
        Check if a skill is currently in the cache.
        
        Args:
            skill_name: Name of the skill to check
            
        Returns:
            True if the skill is cached, False otherwise
        """
        return skill_name in self._cache
    
    def reload(self, skill_name: str) -> Skill:
        """
        Force reload a skill from disk, bypassing the cache.
        
        This is equivalent to invalidate(skill_name) followed by load(skill_name).
        
        Args:
            skill_name: Name of the skill to reload
            
        Returns:
            Freshly loaded Skill object
        """
        self.invalidate(skill_name)
        return self.load(skill_name, use_cache=False)

class SkillRegistry:
    """
    Registry for managing active skills in an agent session.
    
    Usage:
        registry = SkillRegistry()
        registry.add(skill)
        
        # Get combined system prompt
        prompt = registry.get_combined_instructions()
        
        # Check if skill is active
        if registry.has("calculator"):
            ...
    """
    
    def __init__(self):
        self._skills: Dict[str, Skill] = {}
    
    def add(self, skill: Skill) -> None:
        """Add a skill to the registry."""
        self._skills[skill.name] = skill
    
    def remove(self, skill_name: str) -> bool:
        """Remove a skill from the registry. Returns True if removed."""
        if skill_name in self._skills:
            del self._skills[skill_name]
            return True
        return False
    
    def get(self, skill_name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(skill_name)
    
    def has(self, skill_name: str) -> bool:
        """Check if a skill is in the registry."""
        return skill_name in self._skills
    
    def list(self) -> List[str]:
        """List all active skill names."""
        return list(self._skills.keys())
    
    def get_combined_instructions(self) -> str:
        """Get all skill instructions combined into one string."""
        if not self._skills:
            return ""
        
        parts = []
        for skill in self._skills.values():
            parts.append(f"## Skill: {skill.name}\n\n{skill.instructions}")
        
        return "\n\n---\n\n".join(parts)
    
    def to_system_prompt_addition(self) -> str:
        """Get skills as a system prompt addition."""
        combined = self.get_combined_instructions()
        if combined:
            skill_names = ", ".join(self.list())
            return (
                f"\n\n"
                f"# ⚡ ACTIVE SKILLS\n\n"
                f"You have access to the following skills: {skill_names}\n"
                f"These skills provide specialized knowledge and instructions. "
                f"READ AND FOLLOW the skill instructions below when they are relevant to the user's request.\n\n"
                f"{combined}\n\n"
                f"# Instructions for using skills:\n"
                f"1. When the user's request matches a skill's purpose, follow that skill's instructions.\n"
                f"2. Skills may reference scripts, references, or assets - these are available in the skill directory.\n"
                f"3. Use your available tools (shell, write_file, etc.) to execute the skill's instructions.\n"
            )
        return ""
    
    def get_resource_path(self, skill_name: str, resource_type: str, resource_name: str) -> Optional[Path]:
        """
        Get path to a specific resource within a skill.
        
        Args:
            skill_name: Name of the skill
            resource_type: Type of resource ('scripts', 'references', 'assets')
            resource_name: Name of the resource file
            
        Returns:
            Path to the resource or None if not found
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        
        resource_map = {
            'scripts': skill.scripts_dir,
            'references': skill.references_dir,
            'assets': skill.assets_dir,
        }
        
        resource_dir = resource_map.get(resource_type)
        if resource_dir:
            resource_path = resource_dir / resource_name
            return resource_path if resource_path.exists() else None
        return None
    
    def get_skill_info(self) -> str:
        """Get a formatted string with all skill information for the agent."""
        if not self._skills:
            return "No active skills."
        
        lines = ["Active Skills:"]
        for name, skill in self._skills.items():
            lines.append(f"  - {name}: {skill.description}")
            if skill.scripts_dir:
                scripts = list(skill.scripts_dir.glob("*.py"))
                if scripts:
                    lines.append(f"    Scripts: {', '.join(s.name for s in scripts)}")
            if skill.references_dir:
                refs = list(skill.references_dir.glob("*.md"))
                if refs:
                    lines.append(f"    References: {', '.join(r.name for r in refs)}")
        return "\n".join(lines)
    
    def __len__(self) -> int:
        return len(self._skills)
    
    def __repr__(self) -> str:
        return f"SkillRegistry(skills={list(self._skills.keys())})"