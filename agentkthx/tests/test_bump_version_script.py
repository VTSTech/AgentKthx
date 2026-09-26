"""
R06.58 tests for scripts/bump-version.sh.

These tests invoke the bash helper directly and assert on its stdout/stderr
+ exit code + the actual file contents after a bump. They pin the contract
so future edits to the script don't silently break a release.

Run with: ``python -m pytest tests/test_bump_version_script.py -v``
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "bump-version.sh"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run the copy of bump-version.sh inside ``cwd`` (so it operates on the
    tmp repo, not the real one). The script does ``cd "$REPO_ROOT"`` based
    on its own location, so we MUST run the copy, not the original."""
    script_in_cwd = cwd / "scripts" / "bump-version.sh"
    return subprocess.run(
        ["bash", str(script_in_cwd), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.fixture
def fresh_repo_copy(tmp_path: Path) -> Path:
    """Copy the repo to a tmp_path so we can mutate without affecting the
    real checkout. Faster than git clone — just copies files."""
    import shutil
    dst = tmp_path / "AgentKthx"
    # Copy only the files the script touches + the script itself, so the
    # test is fast and isolated. We re-create the minimal tree:
    #   AgentKthx/
    #     scripts/bump-version.sh
    #     pyproject.toml
    #     agentkthx/__init__.py
    #     README.md
    (dst / "scripts").mkdir(parents=True)
    (dst / "agentkthx").mkdir(parents=True)
    shutil.copy(SCRIPT, dst / "scripts" / "bump-version.sh")
    os.chmod(dst / "scripts" / "bump-version.sh", 0o755)

    # Seed with a known starting version (R06.57 / 0.6.57)
    (dst / "pyproject.toml").write_text(textwrap.dedent('''\
        [build-system]
        requires = ["setuptools>=61.0", "wheel"]
        build-backend = "setuptools.build_meta"

        [project]
        name = "agentkthx"
        version = "0.6.57"
        description = "test"
    '''))
    (dst / "agentkthx" / "__init__.py").write_text(textwrap.dedent('''\
        """
        ⚛️ AgentKthx R06.57
        A test stub.
        """

        __version__ = "0.6.57"  # R06.57
        __author__ = "VTSTech"
    '''))
    (dst / "README.md").write_text("# ⚛️ AgentKthx R06.57\n\nTest README.\n")
    return dst


# ---------------------------------------------------------------------------
# --current
# ---------------------------------------------------------------------------

def test_current_flag_prints_version(fresh_repo_copy: Path):
    """--current should print 'R06.57 (0.6.57)' and exit 0."""
    result = _run(["--current"], fresh_repo_copy)
    assert result.returncode == 0, result.stderr
    assert "R06.57" in result.stdout
    assert "0.6.57" in result.stdout


# ---------------------------------------------------------------------------
# happy path: R06.57 → R06.58
# ---------------------------------------------------------------------------

def test_bump_to_r06_58_writes_all_four_sites(fresh_repo_copy: Path):
    """Bumping R06.57 → R06.58 should update all 4 declaration sites."""
    result = _run(["R06.58"], fresh_repo_copy)
    assert result.returncode == 0, result.stderr
    assert "bumped to R06.58" in result.stdout

    # Verify each file
    toml = (fresh_repo_copy / "pyproject.toml").read_text()
    init = (fresh_repo_copy / "agentkthx" / "__init__.py").read_text()
    readme = (fresh_repo_copy / "README.md").read_text()

    assert 'version = "0.6.58"' in toml
    assert "⚛️ AgentKthx R06.58" in init
    assert '__version__ = "0.6.58"  # R06.58' in init
    assert "# ⚛️ AgentKthx R06.58" in readme

    # Old version should NOT appear in version declaration lines
    assert 'version = "0.6.57"' not in toml
    assert "⚛️ AgentKthx R06.57" not in init
    assert '__version__ = "0.6.57"' not in init
    assert "# ⚛️ AgentKthx R06.57" not in readme


# ---------------------------------------------------------------------------
# semver input form
# ---------------------------------------------------------------------------

def test_bump_accepts_semver_form(fresh_repo_copy: Path):
    """Passing '0.6.58' should work the same as 'R06.58'."""
    result = _run(["0.6.58"], fresh_repo_copy)
    assert result.returncode == 0, result.stderr
    init = (fresh_repo_copy / "agentkthx" / "__init__.py").read_text()
    assert '__version__ = "0.6.58"  # R06.58' in init


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_does_not_write(fresh_repo_copy: Path):
    """--dry-run should print the diff but leave files untouched."""
    result = _run(["R06.58", "--dry-run"], fresh_repo_copy)
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout.lower()

    # Files must still be at the original version
    init = (fresh_repo_copy / "agentkthx" / "__init__.py").read_text()
    assert '__version__ = "0.6.57"  # R06.57' in init
    assert '__version__ = "0.6.58"' not in init


# ---------------------------------------------------------------------------
# no-op when already at target
# ---------------------------------------------------------------------------

def test_no_op_when_already_at_target(fresh_repo_copy: Path):
    """Running with the current version should exit 2 with a 'nothing to do' msg."""
    result = _run(["R06.57"], fresh_repo_copy)
    assert result.returncode == 2, result.stderr
    assert "nothing to do" in result.stdout.lower()


# ---------------------------------------------------------------------------
# bad format
# ---------------------------------------------------------------------------

def test_bad_format_rejected(fresh_repo_copy: Path):
    """A malformed version string should exit 1 with a clear error."""
    result = _run(["v0.6.58"], fresh_repo_copy)
    assert result.returncode == 1
    assert "bad version format" in result.stderr.lower()


def test_missing_arg_shows_usage(fresh_repo_copy: Path):
    """No args should print usage and exit 1."""
    result = _run([], fresh_repo_copy)
    assert result.returncode == 1
    assert "Usage" in result.stderr or "Usage" in result.stdout


# ---------------------------------------------------------------------------
# round-trip: R06.58 ↔ 0.6.58
# ---------------------------------------------------------------------------

def test_round_trip_release_to_semver_to_release(fresh_repo_copy: Path):
    """Bumping to R06.58 then reading --current should give back R06.58."""
    _run(["R06.58"], fresh_repo_copy)
    result = _run(["--current"], fresh_repo_copy)
    assert "R06.58" in result.stdout
    assert "0.6.58" in result.stdout


# ---------------------------------------------------------------------------
# safety: pattern-mismatch detection
# ---------------------------------------------------------------------------

def test_fails_loudly_when_pattern_not_found(fresh_repo_copy: Path):
    """If a file's version string was manually edited and no longer matches
    the expected pattern, the script should fail loudly rather than
    silently bumping 3/4 sites."""
    # Corrupt the __init__.py so the __version__ line doesn't match
    init_path = fresh_repo_copy / "agentkthx" / "__init__.py"
    init_path.write_text('__version__ = "9.9.99"  # weird format\n')

    result = _run(["R06.58"], fresh_repo_copy)
    assert result.returncode != 0
    assert "pattern not found" in result.stderr.lower() or \
           "missing" in result.stderr.lower()
