"""
Build hooks for AgentKthx — bakes the source git commit into the package.

Why: `pip install git+https://...` and `python -m build` both run inside a real
git checkout, but the installed wheel lands in site-packages far from any .git.
Baking the commit at build time lets every install method report the exact
GitHub commit it was built from via `agentkthx/_git_meta.py`.

Generated file contract (agentkthx/_git_meta.py):
    SOURCE_COMMIT = "acf1d72"   # short SHA at build time, or None

Rules:
  - build_py writes the file into build_lib ONLY when a commit can be resolved
    (never clobbers a value already baked into an sdist with None).
  - sdist writes the file into the source tree just long enough to include it
    (MANIFEST.in), then removes it (the tree stays clean; it is .gitignored).
  - Editable installs are unaffected at runtime: __init__ prefers the live
    verified .git lookup so `git pull` updates the hash without reinstall.

This file intentionally contains NO metadata (all of it lives in pyproject.toml)
— setuptools merges the two.
"""

import os
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist

_PKG_DIR = "agentkthx"
_META_NAME = os.path.join(_PKG_DIR, "_git_meta.py")

_META_TEMPLATE = '''"""Generated at build time by setup.py — do not edit or commit."""
# Short SHA of the git commit this distribution was built from,
# or None when built outside a git checkout (e.g. from a plain sdist).
SOURCE_COMMIT = {commit!r}
'''


def _resolve_source_commit() -> str | None:
    """Return the short SHA of HEAD, or None when git/checkout is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)) or ".",
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass
    return None


def _write_meta(path: str, commit: str | None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_META_TEMPLATE.format(commit=commit))


class build_py(_build_py):
    """Write _git_meta.py into the build output (wheels)."""

    def run(self):
        super().run()
        commit = _resolve_source_commit()
        if commit is None:
            # No git info here — keep any value already present (e.g. baked
            # into an sdist we are building a wheel from). Writing None would
            # erase it.
            return
        target = os.path.join(self.build_lib, _META_NAME)
        _write_meta(target, commit)


class sdist(_sdist):
    """Include _git_meta.py in the source distribution (SHA, or None if
    built outside a git checkout)."""

    def run(self):
        _write_meta(_META_NAME, _resolve_source_commit())
        try:
            super().run()
        finally:
            try:
                os.remove(_META_NAME)
            except OSError:
                pass


setup(cmdclass={"build_py": build_py, "sdist": sdist})
