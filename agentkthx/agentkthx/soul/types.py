"""
⚛️ AgentKthx — Soul Spec Types

Data structures for ClawSouls Soul Spec v0.5.
https://github.com/clawsouls/soulspec

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# Allowed licenses (permissive only)
ALLOWED_LICENSES = {
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause",
    "CC-BY-4.0", "CC0-1.0", "ISC", "Unlicense",
}

# Known framework identifiers
KNOWN_FRAMEWORKS = {
    "openclaw", "clawdbot", "zeroclaw", "cursor",
    "windsurf", "continue", "ros2", "isaac", "webots", "gazebo",
}


class Environment(str, Enum):
    """Deployment context for the soul."""
    VIRTUAL = "virtual"      # Text/chat-based agent (default)
    EMBODIED = "embodied"    # Physical robot, kiosk, or IoT device
    HYBRID = "hybrid"        # Operates in both virtual and physical


class InteractionMode(str, Enum):
    """Primary interaction modality."""
    TEXT = "text"
    VOICE = "voice"
    MULTIMODAL = "multimodal"
    GESTURE = "gesture"


class ContactPolicy(str, Enum):
    """Physical contact policy for embodied agents."""
    NO_CONTACT = "no-contact"
    GENTLE_CONTACT = "gentle-contact"
    FULL_CONTACT = "full-contact"


class Mobility(str, Enum):
    """Mobility type for embodied agents."""
    STATIONARY = "stationary"
    MOBILE = "mobile"
    LIMITED = "limited"


@dataclass
class Author:
    """Creator information."""
    name: str
    github: Optional[str] = None
    email: Optional[str] = None


@dataclass
class RecommendedSkill:
    """A recommended skill with version constraint."""
    name: str
    version: Optional[str] = None  # Semver range (e.g., ">=1.0.0")
    required: bool = False


@dataclass
class Compatibility:
    """Compatibility requirements."""
    openclaw: Optional[str] = None        # Minimum OpenClaw version
    models: list[str] = field(default_factory=list)  # Glob patterns for models
    frameworks: list[str] = field(default_factory=list)  # Compatible frameworks
    min_token_context: Optional[int] = None  # Minimum context window


@dataclass
class SoulFiles:
    """Paths to soul persona files."""
    soul: str = "SOUL.md"                  # Required
    identity: Optional[str] = None         # IDENTITY.md
    agents: Optional[str] = None           # AGENTS.md
    heartbeat: Optional[str] = None        # HEARTBEAT.md
    style: Optional[str] = None            # STYLE.md
    user_template: Optional[str] = None    # USER_TEMPLATE.md
    avatar: Optional[str] = None           # avatar.png


@dataclass
class SoulExamples:
    """Calibration examples."""
    good: Optional[str] = None  # Path to good outputs
    bad: Optional[str] = None   # Path to bad outputs


@dataclass
class Disclosure:
    """Progressive disclosure configuration."""
    summary: Optional[str] = None  # Max 200 chars for Level 1


@dataclass
class HardwareConstraints:
    """Physical hardware capabilities for embodied agents."""
    has_display: bool = False
    has_speaker: bool = False
    has_microphone: bool = False
    has_camera: bool = False
    mobility: Mobility = Mobility.STATIONARY
    manipulator: bool = False


@dataclass
class PhysicalSafety:
    """Physical safety rules for embodied agents."""
    contact_policy: ContactPolicy = ContactPolicy.NO_CONTACT
    emergency_protocol: str = "stop"  # stop, alert_operator, return_home
    operating_zone: str = "indoor"    # indoor, outdoor, both
    max_speed: Optional[str] = None   # e.g., "0.5m/s"


@dataclass
class Safety:
    """Safety configuration."""
    physical: Optional[PhysicalSafety] = None


@dataclass
class Sensor:
    """Sensor capability description."""
    name: str
    type: Optional[str] = None
    range: Optional[str] = None
    fov: Optional[int] = None  # Field of view in degrees
    resolution: Optional[str] = None
    fps: Optional[int] = None
    channels: Optional[int] = None


@dataclass
class Actuator:
    """Actuator capability description."""
    name: str
    type: Optional[str] = None
    max_speed: Optional[str] = None
    payload: Optional[str] = None
    reach: Optional[str] = None
    force: Optional[str] = None
    dof: Optional[int] = None  # Degrees of freedom
    resolution: Optional[str] = None


@dataclass
class SoulManifest:
    """
    Complete Soul Spec v0.5 manifest.
    
    This represents the soul.json file that defines a persona package.
    """
    # Required fields
    spec_version: str
    name: str                    # kebab-case unique identifier
    display_name: str
    version: str                 # Semver
    description: str             # Max 160 chars
    author: Author
    license: str                 # SPDX identifier
    tags: list[str]              # Max 10
    category: str                # Category path (e.g., "work/devops")
    
    # Optional fields
    compatibility: Compatibility = field(default_factory=Compatibility)
    allowed_tools: list[str] = field(default_factory=list)
    recommended_skills: list[RecommendedSkill] = field(default_factory=list)
    files: SoulFiles = field(default_factory=SoulFiles)
    examples: Optional[SoulExamples] = None
    disclosure: Optional[Disclosure] = None
    deprecated: bool = False
    superseded_by: Optional[str] = None  # owner/name of replacement
    repository: Optional[str] = None
    
    # Embodied agent fields
    environment: Environment = Environment.VIRTUAL
    interaction_mode: InteractionMode = InteractionMode.TEXT
    hardware_constraints: Optional[HardwareConstraints] = None
    safety: Optional[Safety] = None
    sensors: list[Sensor] = field(default_factory=list)
    actuators: list[Actuator] = field(default_factory=list)
    
    # Loaded content (not in JSON)
    soul_content: Optional[str] = None
    identity_content: Optional[str] = None
    agents_content: Optional[str] = None
    style_content: Optional[str] = None
    heartbeat_content: Optional[str] = None
    user_template_content: Optional[str] = None  # USER_TEMPLATE.md content
    examples_good_content: Optional[str] = None  # Good calibration examples
    examples_bad_content: Optional[str] = None   # Bad calibration examples
    avatar_path: Optional[str] = None            # Resolved path to avatar file
    
    def validate(self, soul_dir: Optional["Path"] = None) -> list[str]:
        """
        Validate the manifest and return list of issues.
        Empty list means valid.
        
        Parameters
        ----------
        soul_dir : Optional[Path]
            Path to soul directory for file existence validation (avatar, etc.)
        """
        issues = []
        
        # Check spec version
        if self.spec_version not in ("0.3", "0.4", "0.5"):
            issues.append(f"Unknown spec version: {self.spec_version}")
        
        # Check name format (kebab-case)
        import re
        if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', self.name):
            issues.append(f"Name must be kebab-case: {self.name}")
        
        # Check description length
        if len(self.description) > 160:
            issues.append(f"Description too long ({len(self.description)} chars, max 160)")
        
        # Check license
        if self.license not in ALLOWED_LICENSES:
            issues.append(f"License not in allowed list: {self.license}")
        
        # Check tags count
        if len(self.tags) > 10:
            issues.append(f"Too many tags ({len(self.tags)}, max 10)")
        
        # Check disclosure summary length
        if self.disclosure and self.disclosure.summary:
            if len(self.disclosure.summary) > 200:
                issues.append(f"Disclosure summary too long ({len(self.disclosure.summary)} chars, max 200)")
        
        # Check embodied agent has safety config
        if self.environment == Environment.EMBODIED:
            if not self.safety or not self.safety.physical:
                issues.append("Embodied souls should have safety.physical configuration")
        
        # Check full-contact has justification in soul_content
        if self.safety and self.safety.physical:
            if self.safety.physical.contact_policy == ContactPolicy.FULL_CONTACT:
                if self.soul_content:
                    # Look for justification keywords in SOUL.md
                    content_lower = self.soul_content.lower()
                    if "full-contact" not in content_lower and "physical interaction" not in content_lower:
                        issues.append("Full-contact policy requires justification in SOUL.md (mention 'full-contact' or 'physical interaction')")
        
        # Check avatar file exists if specified
        if self.files.avatar and soul_dir:
            from pathlib import Path
            avatar_path = Path(soul_dir) / self.files.avatar
            if not avatar_path.exists():
                issues.append(f"Avatar file not found: {self.files.avatar}")
        
        return issues
    
    def get_summary(self) -> str:
        """Get Level 1 summary for quick-scan discovery."""
        if self.disclosure and self.disclosure.summary:
            return self.disclosure.summary
        return self.description


# Legacy skill format support (v0.3)
def parse_legacy_skills(skills: list[str]) -> list[RecommendedSkill]:
    """Convert legacy skills: string[] to recommendedSkills format."""
    return [RecommendedSkill(name=s, required=False) for s in skills]