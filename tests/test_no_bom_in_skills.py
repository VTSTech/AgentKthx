"""
Regression test for audit finding MAINT-06 (R07.01): no .py file shipped under
``agentkthx/skills/`` may begin with a UTF-8 BOM (EF BB BF).

Background: 15 files under ``skills/`` carried BOMs at R07.00. They were stripped
in R07.01, and ``package_skill.py`` now refuses to package a skill containing
BOM-bearing .py files. This test guards against re-introduction.

Python itself executes BOM-bearing files fine, but tooling does not:
``ast.parse`` fails on them unless read with ``encoding="utf-8-sig"`` and naive
byte-greps silently miss or mis-anchor matches — both failures actually bit the
R07.00 dead-code analysis. The fix is structural (don't ship BOMs), so the test
asserts on bytes, not on import success.
"""

from pathlib import Path

import pytest

UTF8_BOM = b"\xef\xbb\xbf"

# The shipped package's skills root. Resolves whether tests run from a source
# checkout, an editable install, or a wheel — `agentkthx` is always importable
# because the test suite imports it as the package under test.
import agentkthx

SKILLS_ROOT = Path(agentkthx.__file__).parent / "skills"


def _python_files_under_skills() -> list[Path]:
    """Every .py file shipped under agentkthx/skills/, excluding __pycache__."""
    if not SKILLS_ROOT.exists():
        # Should not happen in a normal install, but fail loudly if it does.
        return []
    return [
        p
        for p in SKILLS_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_skills_root_exists():
    """Sanity: the skills directory must ship with the package."""
    assert SKILLS_ROOT.exists(), f"skills root not found at {SKILLS_ROOT}"
    assert SKILLS_ROOT.is_dir()


def test_no_python_file_in_skills_has_utf8_bom():
    """No .py file under agentkthx/skills/ may begin with the UTF-8 BOM.

    Audit MAINT-06: 15 BOM files were stripped in R07.01. This test prevents
    regression — a re-introduced BOM would break downstream linters, ast.parse
    calls, and byte-greps against the shipped package.
    """
    py_files = _python_files_under_skills()
    assert py_files, "no .py files found under agentkthx/skills/ — test setup is wrong"

    offenders: list[Path] = []
    for path in py_files:
        with path.open("rb") as fh:
            if fh.read(3) == UTF8_BOM:
                offenders.append(path)

    if offenders:
        rel = [str(p.relative_to(SKILLS_ROOT)) for p in offenders]
        pytest.fail(
            f"{len(offenders)} .py file(s) under agentkthx/skills/ begin with a UTF-8 BOM "
            f"(EF BB BF). Strip with: sed -i '1s/^\\xef\\xbb\\xbf//' <file>. "
            f"Offenders: {rel}"
        )


def test_package_skill_module_rejects_bom_files(tmp_path, monkeypatch):
    """The package_skill guard refuses to package skills containing BOM .py files.

    Audits MAINT-06: the prevention guard is in package_skill.package_skill().
    It must return None (refuse to package) and print a clear error message
    when a .py file under the skill folder starts with a UTF-8 BOM.
    """
    import importlib.util

    scripts_dir = Path(agentkthx.__file__).parent / "skills" / "skill-creator" / "scripts"
    pkg_skill_path = scripts_dir / "package_skill.py"
    if not pkg_skill_path.exists():
        pytest.skip("package_skill.py not present in this install layout")

    # package_skill does ``from quick_validate import validate_skill`` — a
    # sibling-module import that only resolves when scripts_dir is on sys.path.
    monkeypatch.syspath_prepend(str(scripts_dir))
    spec = importlib.util.spec_from_file_location("package_skill_test", pkg_skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Build a minimal skill folder whose only .py file has a BOM.
    skill_dir = tmp_path / "bom-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bom-skill\ndescription: A skill with a BOM-bearing script.\n---\n# bom-skill\n",
        encoding="utf-8",
    )
    scripts_dir_in_skill = skill_dir / "scripts"
    scripts_dir_in_skill.mkdir()
    bom_file = scripts_dir_in_skill / "helper.py"
    bom_file.write_bytes(b"\xef\xbb\xbf" + b'"""BOM-bearing helper."""\n')

    out = tmp_path / "out"
    result = mod.package_skill(str(skill_dir), str(out))

    # Must refuse to package: returns None, no .skill file produced.
    assert result is None, "package_skill must refuse to package BOM-bearing skills"
    assert not list(out.glob("*.skill")), "no .skill file should have been written"


def test_package_skill_accepts_clean_skill(tmp_path, monkeypatch):
    """Smoke check: a clean skill (no BOMs) packages successfully.

    Guards against over-eager BOM detection — the prevention must not
    reject well-formed skills.
    """
    import importlib.util

    scripts_dir = Path(agentkthx.__file__).parent / "skills" / "skill-creator" / "scripts"
    pkg_skill_path = scripts_dir / "package_skill.py"
    if not pkg_skill_path.exists():
        pytest.skip("package_skill.py not present in this install layout")

    monkeypatch.syspath_prepend(str(scripts_dir))
    spec = importlib.util.spec_from_file_location("package_skill_clean_test", pkg_skill_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    skill_dir = tmp_path / "clean-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: clean-skill\ndescription: A skill with no BOMs.\n---\n# clean-skill\n",
        encoding="utf-8",
    )
    scripts_dir_in_skill = skill_dir / "scripts"
    scripts_dir_in_skill.mkdir()
    (scripts_dir_in_skill / "helper.py").write_text(
        '"""Clean helper — no BOM."""\nprint("ok")\n',
        encoding="utf-8",
    )

    out = tmp_path / "out"
    result = mod.package_skill(str(skill_dir), str(out))

    assert result is not None, "package_skill must succeed on a BOM-free skill"
    out_files = list(out.glob("*.skill"))
    assert len(out_files) == 1, f"expected 1 .skill file, got {out_files}"
    assert out_files[0].name == "clean-skill.skill"
