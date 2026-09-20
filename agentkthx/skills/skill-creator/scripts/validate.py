#!/usr/bin/env python3
"""
Skill Validator - Validates a skill against the Agent Skills specification.

Usage:
    python validate.py path/to/skill/

Checks:
    - SKILL.md exists and has valid frontmatter
    - name field: 1-64 chars, lowercase, hyphens only, ends with -skill
    - description field: 1-1024 chars
    - SKILL.md body: <500 lines
    - No hardcoded API keys
    - No incomplete markers in scripts
"""

import re
import sys
from pathlib import Path


def validate_skill(skill_path: Path) -> list[str]:
    """Validate a skill directory. Returns list of errors."""
    errors = []
    
    # Check SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        errors.append("SKILL.md not found")
        return errors
    
    content = skill_md.read_text(encoding='utf-8')
    
    # Check frontmatter
    if not content.startswith("---"):
        errors.append("SKILL.md must start with YAML frontmatter (---)")
        return errors
    
    # Extract frontmatter
    fm_match = re.search(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if not fm_match:
        errors.append("SKILL.md frontmatter not properly closed")
        return errors
    
    frontmatter = fm_match.group(1)
    
    # Parse frontmatter fields
    name_match = re.search(r'^name:\s*(.+)$', frontmatter, re.MULTILINE)
    desc_match = re.search(r'^description:\s*[>-]?\s*(.+?)(?=^[a-z]+:|\Z)', frontmatter, re.MULTILINE | re.DOTALL)
    
    # Validate name
    if not name_match:
        errors.append("Missing 'name' field in frontmatter")
    else:
        name = name_match.group(1).strip()
        if len(name) > 64:
            errors.append(f"Skill name too long: {len(name)} chars (max 64)")
        # Allow names ending with -skill OR special meta-skills
        valid_name = (
            re.match(r'^[a-z0-9]+(-[a-z0-9]+)*-skill$', name) or
            name in ('skill-creator', 'agent-skill-creator')
        )
        if not valid_name:
            errors.append(f"Skill name must be lowercase with hyphens and end with -skill: {name}")
        # Check name matches directory
        if name != skill_path.name and not (name == 'agent-skill-creator' and skill_path.name == 'skill-creator'):
            errors.append(f"Skill name '{name}' doesn't match directory '{skill_path.name}'")
    
    # Validate description
    if not desc_match:
        errors.append("Missing 'description' field in frontmatter")
    else:
        desc = desc_match.group(1).strip()
        desc = re.sub(r'\n\s+', ' ', desc)  # Remove YAML continuation
        if len(desc) > 1024:
            errors.append(f"Description too long: {len(desc)} chars (max 1024)")
        if len(desc) < 10:
            errors.append("Description too short (should describe when to use the skill)")
    
    # Check body length
    body = content[fm_match.end():]
    body_lines = body.split('\n')
    if len(body_lines) > 500:
        errors.append(f"SKILL.md body too long: {len(body_lines)} lines (max 500)")
    
    # Check for hardcoded API keys
    key_patterns = [
        r'api[_-]?key\s*=\s*["\'][^"\']{20,}["\']',
        r'secret[_-]?key\s*=\s*["\'][^"\']{20,}["\']',
        r'Bearer\s+[A-Za-z0-9_-]{20,}',
        r'sk-[A-Za-z0-9]{20,}',
    ]
    for pattern in key_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            errors.append("Possible hardcoded API key detected")
            break
    
    # Check scripts for incomplete code patterns
    scripts_dir = skill_path / "scripts"
    if scripts_dir.exists():
        for script in scripts_dir.glob("*.py"):
            script_content = script.read_text(encoding='utf-8')
            # Check for incomplete markers with colon (more specific)
            incomplete_patterns = [
                (r'#\s*TODO:', 'TODO marker'),
                (r'#\s*FIXME:', 'FIXME marker'),
                (r'#\s*XXX:', 'XXX marker'),
            ]
            for pattern, desc in incomplete_patterns:
                if re.search(pattern, script_content, re.IGNORECASE):
                    errors.append(f"{script.name}: Contains {desc}")
                    break
            # Check for empty functions
            if re.search(r'def\s+\w+\([^)]*\):\s*pass', script_content):
                errors.append(f"{script.name}: Contains empty function (pass)")
    
    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate.py path/to/skill/")
        sys.exit(1)
    
    skill_path = Path(sys.argv[1])
    if not skill_path.is_dir():
        print(f"Error: {skill_path} is not a directory")
        sys.exit(1)
    
    print(f"Validating skill: {skill_path.name}")
    print("-" * 40)
    
    errors = validate_skill(skill_path)
    
    if errors:
        print("VALIDATION FAILED")
        for err in errors:
            print(f"  X {err}")
        sys.exit(1)
    else:
        print("VALIDATION PASSED")
        print("  OK Skill is valid and ready to use")
        sys.exit(0)


if __name__ == "__main__":
    main()
