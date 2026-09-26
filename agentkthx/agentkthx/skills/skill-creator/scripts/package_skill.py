#!/usr/bin/env python3
"""
Skill Packager - Creates a distributable .skill file of a skill folder

Usage:
    python utils/package_skill.py <path/to/skill-folder> [output-directory]

Example:
    python utils/package_skill.py skills/public/my-skill
    python utils/package_skill.py skills/public/my-skill ./dist
"""

import sys
import zipfile
from pathlib import Path

from quick_validate import validate_skill

# UTF-8 BOM (EF BB BF) — Python tolerates it, but linters, ast.parse, and
# naive byte-greps silently fail. The packager refuses to ship BOM-bearing
# .py files so downstream contributors never hit that friction. Audit MAINT-06.
_UTF8_BOM = bytes([0xEF, 0xBB, 0xBF])

# Directories never packaged (applies to both BOM scan and zip walk).
# Hoisted to module scope so the BOM scan helper reuses the same exclusion
# list as the zip write loop — they must agree on what "in the skill" means.
EXCLUDED_DIRS = {".git", ".svn", ".hg", "__pycache__", "node_modules"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _find_bom_python_files(skill_path: Path) -> list[Path]:
    """Return .py files under skill_path that begin with a UTF-8 BOM.

    Refuses to package them rather than silently stripping — the source file
    should be fixed so the issue does not recur on the next save.
    """
    offenders: list[Path] = []
    for candidate in skill_path.rglob("*.py"):
        if any(part in EXCLUDED_DIRS for part in candidate.relative_to(skill_path).parts):
            continue
        try:
            with candidate.open("rb") as fh:
                if fh.read(3) == _UTF8_BOM:
                    offenders.append(candidate)
        except OSError:
            # Unreadable file — let the packaging loop fail loudly later.
            continue
    return offenders


def package_skill(skill_path, output_dir=None):
    """
    Package a skill folder into a .skill file.

    Args:
        skill_path: Path to the skill folder
        output_dir: Optional output directory for the .skill file (defaults to current directory)

    Returns:
        Path to the created .skill file, or None if error
    """
    skill_path = Path(skill_path).resolve()

    # Validate skill folder exists
    if not skill_path.exists():
        print(f"[ERROR] Skill folder not found: {skill_path}")
        return None

    if not skill_path.is_dir():
        print(f"[ERROR] Path is not a directory: {skill_path}")
        return None

    # Validate SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        print(f"[ERROR] SKILL.md not found in {skill_path}")
        return None

    # Run validation before packaging
    print("Validating skill...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"[ERROR] Validation failed: {message}")
        print("   Please fix the validation errors before packaging.")
        return None
    print(f"[OK] {message}\n")

    # BOM guard (audit MAINT-06): refuse to ship BOM-bearing .py files.
    bom_files = _find_bom_python_files(skill_path)
    if bom_files:
        print(f"[ERROR] {len(bom_files)} .py file(s) start with a UTF-8 BOM (EF BB BF).")
        print("   BOMs break naive ast.parse / byte-greps and are stripped from the")
        print("   AgentKthx package itself. Fix the source, then re-package.")
        print("   Strip in place with:")
        print("     sed -i '1s/^\\xef\\xbb\\xbf//' <file>")
        print("   Offending files:")
        for f in bom_files:
            print(f"     - {f}")
        return None
    print("[OK] No UTF-8 BOMs in .py files\n")

    # Determine output location
    skill_name = skill_path.name
    if output_dir:
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()

    skill_filename = output_path / f"{skill_name}.skill"

    # Create the .skill file (zip format)
    try:
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Walk through the skill directory
            for file_path in skill_path.rglob("*"):
                # Security: never follow or package symlinks.
                if file_path.is_symlink():
                    print(f"[WARN] Skipping symlink: {file_path}")
                    continue

                rel_parts = file_path.relative_to(skill_path).parts
                if any(part in EXCLUDED_DIRS for part in rel_parts):
                    continue

                if file_path.is_file():
                    resolved_file = file_path.resolve()
                    if not _is_within(resolved_file, skill_path):
                        print(f"[ERROR] File escapes skill root: {file_path}")
                        return None
                    # If output lives under skill_path, avoid writing archive into itself.
                    if resolved_file == skill_filename.resolve():
                        print(f"[WARN] Skipping output archive: {file_path}")
                        continue

                    # Calculate the relative path within the zip.
                    arcname = Path(skill_name) / file_path.relative_to(skill_path)
                    zipf.write(file_path, arcname)
                    print(f"  Added: {arcname}")

        print(f"\n[OK] Successfully packaged skill to: {skill_filename}")
        return skill_filename

    except Exception as e:
        print(f"[ERROR] Error creating .skill file: {e}")
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python utils/package_skill.py <path/to/skill-folder> [output-directory]")
        print("\nExample:")
        print("  python utils/package_skill.py skills/public/my-skill")
        print("  python utils/package_skill.py skills/public/my-skill ./dist")
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Packaging skill: {skill_path}")
    if output_dir:
        print(f"   Output directory: {output_dir}")
    print()

    result = package_skill(skill_path, output_dir)

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
