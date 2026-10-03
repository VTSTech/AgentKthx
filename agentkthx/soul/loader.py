"""
⚛️ AgentKthx — Soul Loader

Load and parse ClawSouls Soul Spec v0.5 packages.

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

from .types import (
    Actuator,
    Author,
    Compatibility,
    ContactPolicy,
    Disclosure,
    Environment,
    HardwareConstraints,
    InteractionMode,
    Mobility,
    PhysicalSafety,
    RecommendedSkill,
    Safety,
    Sensor,
    SoulExamples,
    SoulFiles,
    SoulManifest,
    parse_legacy_skills,
)


class SoulLoader:
    """
    Load and parse Soul Spec packages.

    Supports:
    - soul.json manifest parsing
    - Progressive disclosure (Level 1-3)
    - Persona file loading (SOUL.md, IDENTITY.md, etc.)
    - Legacy v0.3 format backward compatibility

    Example:
        loader = SoulLoader()
        soul = loader.load("/path/to/soul/package")
        print(soul.get_summary())
        system_prompt = loader.build_system_prompt(soul, level=2)
    """

    def __init__(self, strict: bool = False):
        """
        Initialize the loader.

        Args:
            strict: If True, fail on validation errors. If False, warn only.
        """
        self.strict = strict
        self._cache: dict[str, SoulManifest] = {}

    def _resolve_soul_path(self, path: Path) -> Optional[Path]:
        """
        Resolve a soul path by searching in a documented order.

        ARCH-04 closure (R07.13): consolidated the previous 5-step chain
        with repeated ``try/except (ImportError, TypeError, AttributeError)``
        blocks into a single clear resolution algorithm. The behaviour is
        preserved exactly — same search order, same fallbacks — but the
        control flow is now linear and documented instead of nested.

        Search order:
          1. Absolute path → returned as-is if it exists
          2. Relative to CWD → ``Path.cwd() / path``
          3. Bundled souls via ``importlib.resources.files('agentkthx.souls')``
             (the canonical accessor — works for namespace packages, Windows
             pip installs, and editable installs)
          4. Filesystem fallback → ``<agentkthx package dir>/souls/<path>``
             (covers zipped installs and Python versions where
             ``importlib.resources.files`` is unavailable or returns a
             traversable that doesn't exist on disk)
          5. Bare soul name → if ``path`` has no path separators, try
             ``souls/<name>`` then ``souls/<name>/soul.json``
          6. Original path as-is (last-resort fallback for paths that
             resolve via PATH or symlink chains not covered above)

        Returns the resolved ``Path`` or ``None`` if not found.
        """
        # 1. Absolute path
        if path.is_absolute():
            return path if path.exists() else None

        # 2. Relative to CWD
        cwd_path = Path.cwd() / path
        if cwd_path.exists():
            return cwd_path

        # 3. importlib.resources (canonical accessor for bundled souls)
        #    Single try/except — no nesting, no fallback-within-fallback.
        try:
            import importlib.resources as resources

            if hasattr(resources, "files"):
                bundled = resources.files("agentkthx") / "souls" / path
                if bundled.is_dir() or bundled.is_file():
                    return Path(str(bundled))
        except (ImportError, TypeError, AttributeError):
            pass  # fall through to filesystem fallback

        # 4. Filesystem fallback: <package_dir>/souls/<path>
        #    Used when importlib.resources is unavailable OR returns a
        #    traversable that doesn't exist on disk (zipped installs).
        try:
            import agentkthx

            if agentkthx.__file__ is not None:
                package_dir = Path(agentkthx.__file__).parent
                fs_path = package_dir / "souls" / path
                if fs_path.exists():
                    return fs_path
        except (ImportError, TypeError):
            pass  # truly nothing more we can do

        # 5. Bare soul name (no path separators)
        #    Try souls/<name> then souls/<name>/soul.json in both the
        #    bundled and filesystem locations.
        if "/" not in str(path) and "\\" not in str(path):
            try:
                import agentkthx

                if agentkthx.__file__ is not None:
                    package_dir = Path(agentkthx.__file__).parent
                    for candidate in (
                        package_dir / "souls" / path,
                        package_dir / "souls" / path / "soul.json",
                    ):
                        if candidate.exists():
                            return package_dir / "souls" / path
            except (ImportError, TypeError):
                pass

        # 6. Last-resort: the original path (covers symlink / PATH resolution)
        if path.exists():
            return path

        return None

    def load(self, path: Union[str, Path], level: int = 2) -> SoulManifest:
        """
        Load a soul package from a directory or soul.json file.

        Args:
            path: Path to soul directory or soul.json file
            level: Progressive disclosure level (1-3)
                1 = Quick Scan (soul.json only)
                2 = Full Read (soul.json + SOUL.md + IDENTITY.md)
                3 = Deep Dive (all files including STYLE.md, examples)

        Returns:
            SoulManifest with loaded content
        """
        path = Path(path)

        # Try to resolve the path in multiple locations
        resolved_path = self._resolve_soul_path(path)
        if resolved_path is None:
            raise ValueError(f"Not a valid soul path: {path}")

        path = resolved_path

        # Determine soul directory and manifest path
        if path.is_file() and path.name == "soul.json":
            soul_dir = path.parent
            manifest_path = path
        elif path.is_dir():
            soul_dir = path
            manifest_path = path / "soul.json"
        else:
            raise ValueError(f"Not a valid soul path: {path}")

        if not manifest_path.exists():
            raise FileNotFoundError(f"No soul.json found at: {manifest_path}")

        # Check cache
        cache_key = f"{soul_dir}:{level}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Parse manifest
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        manifest = self._parse_manifest(data, soul_dir)

        # Validate (pass soul_dir for file existence checks)
        issues = manifest.validate(soul_dir=soul_dir)
        if issues and self.strict:
            raise ValueError(f"Validation errors: {issues}")

        # Load persona files based on disclosure level
        if level >= 2:
            self._load_level_2(manifest, soul_dir)
        if level >= 3:
            self._load_level_3(manifest, soul_dir)

        # Cache and return
        self._cache[cache_key] = manifest
        return manifest

    def list_souls(self, level: int = 1) -> list[SoulManifest]:
        """
        Discover and load every bundled Soul Spec package (R07.19).

        Mirrors ``SkillLoader.list_skills()`` but returns the loaded
        manifests rather than bare names — the /souls slash command and
        the ``agentkthx souls`` surface need display metadata (display
        name, version, description, allowed tools), all of which live in
        soul.json and are available at Level 1 (quick scan).

        Discovery roots (in order):
          1. ``importlib.resources.files('agentkthx') / 'souls'`` — the
             canonical accessor (namespace packages, pip installs)
          2. ``<agentkthx package dir>/souls`` — filesystem fallback for
             zipped installs where the traversable has no disk path

        A directory counts as a soul package when it contains
        ``soul.json``. Entries that fail to load are skipped (best-effort
        — one broken third-party soul must not hide the rest).

        Args:
            level: Progressive disclosure level passed to ``load()``.
                Default 1 (manifest only) keeps discovery cheap — the
                caller can ``load()`` any soul it actually switches to.

        Returns:
            List of SoulManifest objects sorted by soul directory name.
        """
        souls_root: Optional[Path] = None

        # 1. importlib.resources (canonical accessor for bundled souls)
        try:
            import importlib.resources as resources

            if hasattr(resources, "files"):
                bundled = resources.files("agentkthx") / "souls"
                if bundled.is_dir():
                    souls_root = Path(str(bundled))
        except (ImportError, TypeError, AttributeError):
            pass  # fall through to filesystem fallback

        # 2. Filesystem fallback: <package_dir>/souls
        if souls_root is None:
            try:
                import agentkthx

                if agentkthx.__file__ is not None:
                    candidate = Path(agentkthx.__file__).parent / "souls"
                    if candidate.is_dir():
                        souls_root = candidate
            except (ImportError, TypeError):
                pass

        if souls_root is None:
            return []

        manifests: list[SoulManifest] = []
        for entry in sorted(souls_root.iterdir()):
            if not (entry.is_dir() and (entry / "soul.json").exists()):
                continue
            try:
                manifests.append(self.load(entry, level=level))
            except Exception:
                continue  # best-effort discovery — skip unloadable souls
        return manifests

    def _parse_manifest(self, data: dict, soul_dir: Path) -> SoulManifest:
        """Parse soul.json into SoulManifest."""

        # Parse author
        author_data = data.get("author", {})
        author = Author(
            name=author_data.get("name", "Unknown"),
            github=author_data.get("github"),
            email=author_data.get("email"),
        )

        # Parse compatibility
        compat_data = data.get("compatibility", {})
        compatibility = Compatibility(
            openclaw=compat_data.get("openclaw"),
            models=compat_data.get("models", []),
            frameworks=compat_data.get("frameworks", []),
            min_token_context=compat_data.get("minTokenContext"),
        )

        # Parse recommended skills (support both formats)
        skills_data = data.get("recommendedSkills", [])
        if not skills_data and "skills" in data:
            # Legacy format
            skills_data = parse_legacy_skills(data["skills"])
            skills_data = [{"name": s.name, "required": s.required} for s in skills_data]

        recommended_skills = []
        for sk in skills_data:
            if isinstance(sk, str):
                recommended_skills.append(RecommendedSkill(name=sk))
            else:
                recommended_skills.append(
                    RecommendedSkill(
                        name=sk.get("name", ""),
                        version=sk.get("version"),
                        required=sk.get("required", False),
                    )
                )

        # Parse files
        files_data = data.get("files", {})
        files = SoulFiles(
            soul=files_data.get("soul", "SOUL.md"),
            identity=files_data.get("identity"),
            agents=files_data.get("agents"),
            heartbeat=files_data.get("heartbeat"),
            style=files_data.get("style"),
            user_template=files_data.get("userTemplate"),
            avatar=files_data.get("avatar"),
        )

        # Parse examples
        examples = None
        if "examples" in data:
            ex_data = data["examples"]
            examples = SoulExamples(
                good=ex_data.get("good"),
                bad=ex_data.get("bad"),
            )

        # Parse disclosure
        disclosure = None
        if "disclosure" in data:
            disc_data = data["disclosure"]
            disclosure = Disclosure(
                summary=disc_data.get("summary"),
            )

        # Parse hardware constraints (embodied agents)
        hardware = None
        if "hardwareConstraints" in data:
            hw_data = data["hardwareConstraints"]
            hardware = HardwareConstraints(
                has_display=hw_data.get("hasDisplay", False),
                has_speaker=hw_data.get("hasSpeaker", False),
                has_microphone=hw_data.get("hasMicrophone", False),
                has_camera=hw_data.get("hasCamera", False),
                mobility=Mobility(hw_data.get("mobility", "stationary")),
                manipulator=hw_data.get("manipulator", False),
            )

        # Parse safety
        safety = None
        if "safety" in data:
            safety_data = data["safety"]
            physical = None
            if "physical" in safety_data:
                phys_data = safety_data["physical"]
                physical = PhysicalSafety(
                    contact_policy=ContactPolicy(phys_data.get("contactPolicy", "no-contact")),
                    emergency_protocol=phys_data.get("emergencyProtocol", "stop"),
                    operating_zone=phys_data.get("operatingZone", "indoor"),
                    max_speed=phys_data.get("maxSpeed"),
                )
            safety = Safety(physical=physical)

        # Parse sensors
        sensors = []
        for name, sensor_data in data.get("sensors", {}).items():
            if isinstance(sensor_data, bool):
                sensors.append(Sensor(name=name))
            else:
                sensors.append(
                    Sensor(
                        name=name,
                        type=sensor_data.get("type"),
                        range=sensor_data.get("range"),
                        fov=sensor_data.get("fov"),
                        resolution=sensor_data.get("resolution"),
                        fps=sensor_data.get("fps"),
                        channels=sensor_data.get("channels"),
                    )
                )

        # Parse actuators
        actuators = []
        for name, act_data in data.get("actuators", {}).items():
            actuators.append(
                Actuator(
                    name=name,
                    type=act_data.get("type"),
                    max_speed=act_data.get("maxSpeed"),
                    payload=act_data.get("payload"),
                    reach=act_data.get("reach"),
                    force=act_data.get("force"),
                    dof=act_data.get("dof"),
                    resolution=act_data.get("resolution"),
                )
            )

        # Build manifest
        return SoulManifest(
            spec_version=data.get("specVersion", "0.5"),
            name=data.get("name", "unknown"),
            display_name=data.get("displayName", "Unknown"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=author,
            license=data.get("license", "MIT"),
            tags=data.get("tags", []),
            category=data.get("category", "general"),
            compatibility=compatibility,
            allowed_tools=data.get("allowedTools", []),
            recommended_skills=recommended_skills,
            files=files,
            examples=examples,
            disclosure=disclosure,
            deprecated=data.get("deprecated", False),
            superseded_by=data.get("supersededBy"),
            repository=data.get("repository"),
            environment=Environment(data.get("environment", "virtual")),
            interaction_mode=InteractionMode(data.get("interactionMode", "text")),
            hardware_constraints=hardware,
            safety=safety,
            sensors=sensors,
            actuators=actuators,
        )

    def _load_level_2(self, manifest: SoulManifest, soul_dir: Path) -> None:
        """Load Level 2 files: SOUL.md + IDENTITY.md."""
        # Load SOUL.md (required)
        soul_path = soul_dir / manifest.files.soul
        if soul_path.exists():
            manifest.soul_content = soul_path.read_text(encoding="utf-8")

        # Load IDENTITY.md (optional)
        if manifest.files.identity:
            identity_path = soul_dir / manifest.files.identity
            if identity_path.exists():
                manifest.identity_content = identity_path.read_text(encoding="utf-8")

    def _load_level_3(self, manifest: SoulManifest, soul_dir: Path) -> None:
        """Load Level 3 files: AGENTS.md, STYLE.md, HEARTBEAT.md, USER_TEMPLATE.md, examples, avatar."""
        # Load AGENTS.md
        if manifest.files.agents:
            agents_path = soul_dir / manifest.files.agents
            if agents_path.exists():
                manifest.agents_content = agents_path.read_text(encoding="utf-8")

        # Load STYLE.md
        if manifest.files.style:
            style_path = soul_dir / manifest.files.style
            if style_path.exists():
                manifest.style_content = style_path.read_text(encoding="utf-8")

        # Load HEARTBEAT.md
        if manifest.files.heartbeat:
            heartbeat_path = soul_dir / manifest.files.heartbeat
            if heartbeat_path.exists():
                manifest.heartbeat_content = heartbeat_path.read_text(encoding="utf-8")

        # Load USER_TEMPLATE.md (v0.5 spec)
        if manifest.files.user_template:
            template_path = soul_dir / manifest.files.user_template
            if template_path.exists():
                manifest.user_template_content = template_path.read_text(encoding="utf-8")

        # Load calibration examples (v0.5 spec)
        if manifest.examples:
            if manifest.examples.good:
                good_path = soul_dir / manifest.examples.good
                if good_path.exists():
                    manifest.examples_good_content = good_path.read_text(encoding="utf-8")
            if manifest.examples.bad:
                bad_path = soul_dir / manifest.examples.bad
                if bad_path.exists():
                    manifest.examples_bad_content = bad_path.read_text(encoding="utf-8")

        # Resolve avatar path (v0.5 spec)
        if manifest.files.avatar:
            avatar_path = soul_dir / manifest.files.avatar
            if avatar_path.exists():
                manifest.avatar_path = str(avatar_path)

    def build_system_prompt(
        self,
        manifest: SoulManifest,
        level: int = 2,
        include_identity: bool = True,
    ) -> str:
        """
        Build a system prompt from the soul manifest.

        Args:
            manifest: Loaded soul manifest
            level: Progressive disclosure level
            include_identity: Whether to include IDENTITY.md content

        Returns:
            System prompt string
        """
        parts = []

        # Level 1: Basic info
        parts.append(f"# {manifest.display_name}")
        parts.append(f"\n{manifest.description}")

        if manifest.disclosure and manifest.disclosure.summary:
            parts.append(f"\n{manifest.disclosure.summary}")

        # Level 2: Core persona
        if level >= 2:
            if manifest.soul_content:
                parts.append(f"\n\n## Persona\n\n{manifest.soul_content}")

            if include_identity and manifest.identity_content:
                parts.append(f"\n\n## Identity\n\n{manifest.identity_content}")

        # Level 3: Extended behavior
        if level >= 3:
            if manifest.style_content:
                parts.append(f"\n\n## Style Guidelines\n\n{manifest.style_content}")

            if manifest.agents_content:
                parts.append(f"\n\n## Agent Behavior\n\n{manifest.agents_content}")

            if manifest.heartbeat_content:
                parts.append(f"\n\n## Heartbeat\n\n{manifest.heartbeat_content}")

            # User template for message formatting
            if manifest.user_template_content:
                parts.append(f"\n\n## User Message Template\n\n{manifest.user_template_content}")

            # Calibration examples (good and bad outputs)
            if manifest.examples_good_content or manifest.examples_bad_content:
                parts.append("\n\n## Calibration Examples")
                if manifest.examples_good_content:
                    parts.append(f"\n\n### Good Outputs\n\n{manifest.examples_good_content}")
                if manifest.examples_bad_content:
                    parts.append(f"\n\n### Outputs to Avoid\n\n{manifest.examples_bad_content}")

        # Add constraints for embodied agents
        if manifest.environment != Environment.VIRTUAL:
            parts.append("\n\n## Environment")
            parts.append(f"\nYou are an **{manifest.environment.value}** agent.")

            if manifest.interaction_mode != InteractionMode.TEXT:
                parts.append(f"\nPrimary interaction mode: {manifest.interaction_mode.value}")

            if manifest.hardware_constraints:
                hc = manifest.hardware_constraints
                capabilities = []
                if hc.has_display:
                    capabilities.append("display")
                if hc.has_speaker:
                    capabilities.append("speaker")
                if hc.has_microphone:
                    capabilities.append("microphone")
                if hc.has_camera:
                    capabilities.append("camera")
                if capabilities:
                    parts.append(f"\nHardware: {', '.join(capabilities)}")

            if manifest.safety and manifest.safety.physical:
                ps = manifest.safety.physical
                parts.append(f"\nSafety: {ps.contact_policy.value} contact policy")

        return "\n".join(parts)


# Singleton instance for convenience
_default_loader: Optional[SoulLoader] = None


def get_soul_loader(strict: bool = False, clear_cache: bool = False) -> SoulLoader:
    """Get the default SoulLoader instance.

    Args:
        strict: If True, fail on validation errors
        clear_cache: If True, clear the cache before returning
    """
    global _default_loader
    if _default_loader is None:
        _default_loader = SoulLoader(strict=strict)
    if clear_cache:
        _default_loader._cache.clear()
    return _default_loader


def load_soul(path: Union[str, Path], level: int = 2, reload: bool = False) -> SoulManifest:
    """Convenience function to load a soul using the default loader.

    Args:
        path: Path to soul directory
        level: Progressive disclosure level
        reload: If True, bypass cache and reload from disk
    """
    loader = get_soul_loader()
    if reload:
        # Resolve the path first to match the cache key format used in SoulLoader.load()
        # which stores entries as f"{resolved_soul_dir}:{level}"
        resolved = loader._resolve_soul_path(Path(path))
        if resolved is not None:
            cache_key = f"{resolved}:{level}"
            if cache_key in loader._cache:
                del loader._cache[cache_key]
        else:
            # If path can't be resolved, fall back to clearing all entries with this path
            stale_keys = [k for k in loader._cache if k.startswith(f"{path}:")]
            for k in stale_keys:
                del loader._cache[k]
    return loader.load(path, level=level)


def build_system_prompt(manifest: SoulManifest, level: int = 2) -> str:
    """Convenience function to build system prompt."""
    return get_soul_loader().build_system_prompt(manifest, level=level)


def build_system_prompt_with_tools(
    manifest: SoulManifest,
    tools: list,
    level: int = 3,
    tool_choice: Optional[object] = None,
    native_tools: bool = False,
) -> str:
    """
    Build a system prompt with dynamic tool injection.

    This replaces the static tool reference in the soul with the actual
    available tools at runtime.

    OpenResponses Enhancement: Communicates tool_choice constraints to the model,
    enabling the model to understand and comply with tool invocation requirements.

    Args:
        manifest: Loaded soul manifest
        tools: List of Tool objects available
        level: Progressive disclosure level
        tool_choice: Optional ToolChoice object to communicate constraints
        native_tools: If True, tools are passed via API body (native function calling).
            Skip ReAct format instructions in the tool section.
            If False, the tool section still does NOT emit ReAct format
            instructions — those live in the default prompt's ReAct branch
            or in the soul's SOUL.md (MAINT-24 R07.21 CLOSED: the docstring
            previously promised ReAct instructions the body no longer
            includes). The tool section contributes only the reference
            table + CRITICAL RULE.

    Returns:
        System prompt with tools injected
    """
    import re

    # Get base prompt
    base_prompt = build_system_prompt(manifest, level=level)

    # Build dynamic tool section
    tool_section = _build_tool_section(tools, native_tools=native_tools)

    # Pattern to match the static tool section in SOUL.md
    # Matches (with ## or ###):
    # 1. Tool Reference header (with optional parenthetical text)
    # 2. Table header: | Tool | When to use | Arguments |
    # 3. Separator row: |------|-------------|-----------|
    # 4. One or more data rows (3-column table)
    # 5. Blank line
    # 6. **CRITICAL RULE** line
    pattern = (
        r"##{1,3} Tool Reference[^\n]*\n+"
        r"\| Tool \| When to use \| Arguments \|\n"
        r"\|[-| ]+\|\n"
        r"(?:\|[^|]*\|[^|]*\|[^|]*\|[^\n]*\n)+"
        r"\n"
        r"\*\*CRITICAL RULE\*\*[^\n]*"
    )

    if re.search(pattern, base_prompt):
        result = re.sub(pattern, tool_section.rstrip(), base_prompt)
    else:
        # No existing tool reference, append tools
        result = f"{base_prompt}\n\n{tool_section}"

    # Inject dynamic examples based on available tools
    # Skip ReAct-format examples when using native tool calling
    if not native_tools:
        dynamic_examples = _build_dynamic_examples(tools)
    else:
        dynamic_examples = {"example": "", "example_flow": "", "error_example": ""}

    # Replace placeholder markers with dynamic examples
    result = result.replace("{{DYNAMIC_EXAMPLE}}", dynamic_examples["example"])
    result = result.replace("{{DYNAMIC_EXAMPLE_FLOW}}", dynamic_examples["example_flow"])
    result = result.replace("{{DYNAMIC_ERROR_EXAMPLE}}", dynamic_examples["error_example"])

    # Inject Calculator Syntax section only if calculator tool is available
    tool_names = [getattr(t, "name", "") for t in tools]
    if "calculator" in tool_names:
        calculator_section = _build_calculator_syntax_section()
        result = result.replace("{{CALCULATOR_SYNTAX_SECTION}}", calculator_section)
        # Also inject calculator-specific error hint
        calculator_error_hint = """4. **USE** correct syntax (see Calculator Syntax table above)

**Calculator-specific errors:**
- `invalid syntax` → Use Python syntax, not natural language (e.g., `2**10` not "2 to the power of 10")"""
        result = result.replace("{{CALCULATOR_ERROR_HINT}}", calculator_error_hint)
    else:
        # Remove the placeholders entirely if no calculator
        result = result.replace("{{CALCULATOR_SYNTAX_SECTION}}", "")
        result = result.replace("{{CALCULATOR_ERROR_HINT}}", "")

    # OpenResponses Enhancement: Add tool_choice constraints to prompt
    if tool_choice is not None:
        tool_choice_context = _build_tool_choice_context(tool_choice)
        if tool_choice_context:
            result = f"{result}\n\n{tool_choice_context}"

    return result


def _build_tool_choice_context(tool_choice: object) -> str:
    """
    Build context string for tool_choice constraints.

    OpenResponses specifies that tool_choice controls tool invocation behavior.
    This function communicates these constraints to the model so it can comply.

    Args:
        tool_choice: ToolChoice object with type, name, and tools attributes

    Returns:
        Context string to append to system prompt, or empty string if not needed
    """
    # Import from openresponses module (correct location)

    # Get the tool_choice type
    tc_type = getattr(tool_choice, "type", None)
    if tc_type is None:
        return ""

    # Handle both enum and string values
    if hasattr(tc_type, "value"):
        tc_value = tc_type.value
    else:
        tc_value = str(tc_type)

    # Build context based on mode
    if tc_value == "required":
        return (
            "## Tool Requirement (MANDATORY)\n\n"
            "**You MUST call at least one tool before providing a Final Answer.**\n"
            "Direct responses without tool calls are NOT allowed.\n\n"
            "Use the Action/Action Input format to call a tool, then provide Final Answer."
        )
    elif tc_value == "none":
        return (
            "## Tool Restriction\n\n"
            "**Tools are currently DISABLED.**\n"
            "Answer the user's question directly without using any tools.\n"
            "Do NOT output Action/Action Input lines."
        )
    elif tc_value == "function":
        # Specific tool required
        tool_name = getattr(tool_choice, "name", "the specified tool")
        return (
            f"## Tool Requirement (MANDATORY)\n\n"
            f"**You MUST use the `{tool_name}` tool.**\n\n"
            f"Use the Action/Action Input format to call `{tool_name}`, then provide Final Answer."
        )
    elif tc_value == "allowed_tools":
        # Restricted to specific tools
        allowed = getattr(tool_choice, "tools", [])
        if allowed:
            tool_list = ", ".join(f"`{t}`" for t in allowed)
            return (
                f"## Tool Restriction\n\n"
                f"**You may only use these tools:** {tool_list}\n\n"
                "Do NOT try to use any other tools. If none of these tools can help, respond directly."
            )

    # Default: "auto" mode - no additional context needed
    return ""


# R07.19 (follow-up commit #2): real example values for common string
# parameter names, rendered in the Tool Reference "Arguments" column.
# Small local models copy the example verbatim, so a real value
# ("echo Hello, World!") teaches the shape of a correct call — the
# previous generic "..." placeholder read like the prompt itself had
# been truncated (user report). Resolution per string param: this map →
# the parameter's own non-empty string default → "..." fallback.
_PARAM_STRING_EXAMPLES = {
    "command": "echo Hello, World!",  # shell
    "expression": "15 * 8",  # calculator
    "file_path": "/tmp/example.txt",  # read_file / write_file / edit_file / read_file_lines
    "path": "/tmp",  # list_directory / find_files
    "pattern": "*.py",  # find_files
    "content": "Hello, World!",  # write_file / todo add
    "old_string": "text to replace",  # edit_file
    "new_string": "replacement text",  # edit_file
    "url": "https://example.com",  # http_get
    "query": "latest AI news",  # web_search
    "code": "print(2 + 2)",  # python_repl
    "json_string": "[1, 2, 3]",  # parse_json
    "text": "hello world",  # count_words / count_chars
    "action": "add",  # todo
    "task_id": "1",  # todo
    "timezone": "UTC",  # get_time
}


def _build_tool_section(tools: list, native_tools: bool = False) -> str:
    """Build the dynamic tool section for system prompt.

    Args:
        tools: List of Tool objects available
        native_tools: If True, tools are passed via the API body (native function calling).
            Skip ReAct format instructions — the model already knows how to call tools.
            If False, the section does NOT include ReAct format instructions
            either — those live in the default prompt's ReAct branch or in
            the soul's SOUL.md (MAINT-24 R07.21 CLOSED: the docstring
            previously promised format instructions the body no longer
            includes). This function contributes only the tool reference
            table + CRITICAL RULE; the ReAct format block is the caller's
            responsibility.
    """
    if not tools:
        return ""

    lines = ["### Tool Reference (only use if available)"]
    lines.append("")
    lines.append("| Tool | When to use | Arguments |")
    lines.append("|------|-------------|-----------|")

    for tool in tools:
        # Get tool name and description
        name = getattr(tool, "name", str(tool))
        desc = getattr(tool, "description", "")
        params = getattr(tool, "params", [])

        # Build arguments example
        # R07.19 (follow-up commit #2): string params render a REAL value —
        # curated per param name, else the param's own string default, else
        # the "..." fallback. Small local models copy the Arguments column
        # verbatim, so the example must look like an actual call. Enum
        # params show their first (always-valid) value, booleans show
        # `true`, objects show `{}` — only unmapped exotic types still
        # render a bare `...`.
        if params:
            param_pairs = []
            for p in params:
                p_name = getattr(p, "name", str(p))
                p_type = getattr(p, "type", "string")
                p_enum = getattr(p, "enum", None)
                p_default = getattr(p, "default", None)
                if p_enum:
                    # First enum value is always a valid choice.
                    param_pairs.append(f'"{p_name}": "{p_enum[0]}"')
                elif p_type == "string":
                    example = _PARAM_STRING_EXAMPLES.get(p_name)
                    if example is None and isinstance(p_default, str) and p_default:
                        example = p_default
                    if example is None:
                        example = "..."
                    param_pairs.append(f'"{p_name}": "{example}"')
                elif p_type in ("number", "integer", "float"):
                    # Use 10 (not 0) for numeric examples. Small local models
                    # copy the example value verbatim — 0 was causing instant
                    # timeouts when the model emitted `{"timeout": 0}` for
                    # the shell/http_get/python_repl tools (which all use a
                    # timeout param). 10 seconds is a sensible default that
                    # gives the tool enough time to actually run.
                    param_pairs.append(f'"{p_name}": 10')
                elif p_type == "boolean":
                    param_pairs.append(f'"{p_name}": true')
                elif p_type == "object":
                    param_pairs.append(f'"{p_name}": {{}}')
                else:
                    param_pairs.append(f'"{p_name}": ...')
            args_example = "{" + ", ".join(param_pairs) + "}"
        else:
            args_example = "{}"

        # Build "when to use" from description
        # R07.19 (follow-up commit #2): cap raised 40 → 60 and the cut moved
        # to a word boundary — "Execute shell commands (with security..."
        # read as broken truncation (user report); the full first sentence
        # of most builtin tool descriptions fits in 60 chars.
        short_desc = desc.split(".")[0] if desc else "No description"
        if len(short_desc) > 60:
            cut = short_desc[:57]
            space = cut.rfind(" ")
            short_desc = (cut[:space] if space > 0 else cut) + "..."

        lines.append(f"| `{name}` | {short_desc} | `{args_example}` |")

    lines.append("")
    lines.append(
        "**CRITICAL RULE**: If a tool is NOT in the available tools list, do NOT try to use it. Respond directly instead."
    )

    # Note: ReAct format instructions (Action:/Action Input:/Final Answer:) are
    # NOT duplicated here. They're already provided by either:
    #   - the no-soul default prompt (agent_setup.py:_build_default_prompt, ReAct branch), OR
    #   - the soul's SOUL.md content (e.g., kthx-helper/SOUL.md ships with a
    #     full "When you need to use a tool, follow this EXACT format:" block).
    # Appending another format block here caused a duplicate that confused
    # models — they'd see two different format blocks and emit mixed/atypical
    # Action sequences. The tool reference table + CRITICAL RULE above is
    # all this section needs to contribute.

    return "\n".join(lines)


def _build_dynamic_examples(tools: list) -> dict:
    """
    Build dynamic examples based on available tools.

    Returns a dict with:
        - example: Single tool call example
        - example_flow: Complete flow example
        - error_example: Error recovery example

    Args:
        tools: List of Tool objects available

    Returns:
        Dict with example strings keyed by placeholder name
    """
    # Define example templates for different tool types
    EXAMPLE_TEMPLATES = {
        "calculator": {
            "example": """**Example** - User asks "What is 15 times 8?":
```
Action: calculator
Action Input: {"expression": "15 * 8"}
```""",
            "example_flow": """**Example flow:**
```
User: What is 15 times 8?
Action: calculator
Action Input: {"expression": "15 * 8"}
Observation: 120
Final Answer: 120
```""",
            "error_example": """**Example recovery:**
```
Action: calculator
Action Input: {"expression": "2 to the power of 10"}
Observation: Error evaluating expression: invalid syntax
Action: calculator
Action Input: {"expression": "2**10"}
Observation: 1024
Final Answer: 1024
```""",
        },
        "shell": {
            "example": """**Example** - User asks "What is the current directory?":
```
Action: shell
Action Input: {"command": "pwd"}
```""",
            "example_flow": """**Example flow:**
```
User: What is the current directory?
Action: shell
Action Input: {"command": "pwd"}
Observation: /home/user
Final Answer: /home/user
```""",
            "error_example": """**Example recovery:**
```
Action: shell
Action Input: {"command": "lss"}
Observation: Error: lss: command not found
Action: shell
Action Input: {"command": "ls"}
Observation: file1.txt file2.txt
Final Answer: file1.txt file2.txt
```""",
        },
        "read_file": {
            "example": """**Example** - User asks "What is in config.json?":
```
Action: read_file
Action Input: {"file_path": "config.json"}
```""",
            "example_flow": """**Example flow:**
```
User: What is in config.json?
Action: read_file
Action Input: {"file_path": "config.json"}
Observation: {"setting": "value"}
Final Answer: The config.json contains {"setting": "value"}
```""",
            "error_example": """**Example recovery:**
```
Action: read_file
Action Input: {"file_path": "missing.txt"}
Observation: Error: File not found
Action: read_file
Action Input: {"file_path": "existing.txt"}
Observation: Hello World
Final Answer: Hello World
```""",
        },
        "get_time": {
            "example": """**Example** - User asks "What time is it?":
```
Action: get_time
Action Input: {}
```""",
            "example_flow": """**Example flow:**
```
User: What time is it?
Action: get_time
Action Input: {}
Observation: 14:30:00
Final Answer: 14:30:00
```""",
            "error_example": """**Example recovery:**
```
Action: get_time
Action Input: {"timezone": "InvalidZone"}
Observation: Error: Unknown timezone
Action: get_time
Action Input: {}
Observation: 14:30:00
Final Answer: 14:30:00
```""",
        },
        "get_date": {
            "example": """**Example** - User asks "What is today's date?":
```
Action: get_date
Action Input: {}
```""",
            "example_flow": """**Example flow:**
```
User: What is today's date?
Action: get_date
Action Input: {}
Observation: 2024-01-15
Final Answer: 2024-01-15
```""",
            "error_example": """**Example:**
```
Action: get_date
Action Input: {}
Observation: 2024-01-15
Final Answer: 2024-01-15
```""",
        },
        "list_directory": {
            "example": """**Example** - User asks "What files are in /tmp?":
```
Action: list_directory
Action Input: {"path": "/tmp"}
```""",
            "example_flow": """**Example flow:**
```
User: What files are in /tmp?
Action: list_directory
Action Input: {"path": "/tmp"}
Observation: file1.txt, file2.txt
Final Answer: /tmp contains file1.txt and file2.txt
```""",
            "error_example": """**Example recovery:**
```
Action: list_directory
Action Input: {"path": "/nonexistent"}
Observation: Error: Directory not found
Action: list_directory
Action Input: {"path": "/tmp"}
Observation: file1.txt
Final Answer: file1.txt
```""",
        },
        "write_file": {
            "example": """**Example** - User asks "Create a test file":
```
Action: write_file
Action Input: {"file_path": "test.txt", "content": "Hello World"}
```""",
            "example_flow": """**Example flow:**
```
User: Create a test file
Action: write_file
Action Input: {"file_path": "test.txt", "content": "Hello World"}
Observation: File written successfully
Final Answer: Created test.txt with content "Hello World"
```""",
            "error_example": """**Example recovery:**
```
Action: write_file
Action Input: {"file_path": "/readonly/test.txt", "content": "test"}
Observation: Error: Permission denied
Action: write_file
Action Input: {"file_path": "test.txt", "content": "test"}
Observation: File written successfully
Final Answer: Created test.txt
```""",
        },
        "python_repl": {
            "example": """**Example** - User asks "Calculate 2^10 in Python":
```
Action: python_repl
Action Input: {"code": "print(2**10)"}
```""",
            "example_flow": """**Example flow:**
```
User: Calculate 2^10 in Python
Action: python_repl
Action Input: {"code": "print(2**10)"}
Observation: 1024
Final Answer: 1024
```""",
            "error_example": """**Example recovery:**
```
Action: python_repl
Action Input: {"code": "print(undefined_var)"}
Observation: Error: name 'undefined_var' is not defined
Action: python_repl
Action Input: {"code": "print(2**10)"}
Observation: 1024
Final Answer: 1024
```""",
        },
    }

    # Default fallback for unknown tools
    DEFAULT_TEMPLATE = {
        "example": """**Example**:
```
Action: <tool_name>
Action Input: {"arg": "value"}
```""",
        "example_flow": """**Example flow:**
```
Action: <tool_name>
Action Input: {"arg": "value"}
Observation: result
Final Answer: result
```""",
        "error_example": """**Example:**
```
Action: <tool_name>
Action Input: {"arg": "correct_value"}
Observation: result
Final Answer: result
```""",
    }

    # Find the first tool we have a template for
    for tool in tools:
        tool_name = getattr(tool, "name", "")
        if tool_name in EXAMPLE_TEMPLATES:
            return EXAMPLE_TEMPLATES[tool_name]

    # Fallback: use first available tool with default template
    if tools:
        first_tool = getattr(tools[0], "name", "tool")
        result = {}
        for key, template in DEFAULT_TEMPLATE.items():
            result[key] = template.replace("<tool_name>", first_tool)
        return result

    # No tools - return minimal examples
    return DEFAULT_TEMPLATE


def _build_calculator_syntax_section() -> str:
    """
    Build the Calculator Syntax section for the system prompt.

    This section is only included when the calculator tool is available.

    Returns:
        Calculator Syntax section string
    """
    return """## Calculator Syntax (CRITICAL)

The calculator uses **Python syntax**. Use these correct formats:

| Natural Language | Correct Python Syntax |
|------------------|----------------------|
| "2 to the power of 10" | `2**10` |
| "2 ^ 10" | `2**10` |
| "square root of 144" | `sqrt(144)` or `144**0.5` |
| "15 percent of 200" | `15/100*200` |
| "15 times 8" | `15 * 8` |
| "cube root of 27" | `27**(1/3)` |

**WRONG**: `"2 to the power of 10"` (natural language will cause syntax error)
**CORRECT**: `"2**10"` (Python syntax)

"""
