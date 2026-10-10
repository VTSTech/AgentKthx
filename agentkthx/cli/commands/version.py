"""`agentkthx version` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import shutil
import sys

from ...colors import bright_cyan, bright_green, cyan, dim, green, red, yellow
from ..banner import print_banner
from ..utils import _is_externally_managed_error


def cmd_version(args: argparse.Namespace) -> int:
    """Show version information."""
    from ... import __author__, __status__, __version__

    print_banner()
    print(f"   {dim('Version:')} {bright_green(__version__)}")
    print(f"   {dim('Status:')}  {yellow(__status__)}")
    print(f"   {dim('Author:')}  {cyan(__author__)}")
    print(f"   {dim('Repo:')}    {dim('https://github.com/VTSTech/AgentKthx')}")
    print(f"   {dim('Discord:')} {cyan('https://discord.gg/vSK3Ba2aQ')}")

    # Latest releases (live checks — silent on failure / opt-out):
    # stable track via PyPI, development track via GitHub main commits
    # (the commit line only appears for git checkouts, which have a baseline).
    #
    # R06.57: pip-installed users now also see a "GitHub main:" version line
    # (parsed from raw.githubusercontent.com/.../__init__.py) — surfaces
    # dev releases that haven't been pushed to PyPI yet.
    # R07.00: update checks are always live — the on-disk cache was removed,
    # so `version --refresh` was retired (nothing left to bypass).
    try:
        from ...update_check import base_version, check_for_update, git_hash, is_newer

        _latest_info = check_for_update(timeout=1.0)
    except Exception:
        _latest_info = None
    if _latest_info:
        _latest = str(_latest_info.get("pypi_latest") or "").strip()
        if _latest:
            if is_newer(_latest, base_version(__version__)):
                print(
                    f"   {dim('Latest on PyPI:')} {bright_green(_latest)} {yellow('(stable update available)')}"
                )
            else:
                print(f"   {dim('Latest on PyPI:')} {_latest} {dim('(up to date)')}")
        _gh = str(_latest_info.get("github_sha") or "").strip().lower()
        _installed = git_hash(__version__)
        if _gh and _installed:
            if not _gh.startswith(_installed):
                print(
                    f"   {dim('GitHub main:')} {bright_green(_gh[:7])} {yellow('(development release available)')}"
                )
            else:
                print(f"   {dim('GitHub main:')} {_gh[:7]} {dim('(up to date)')}")
        # R06.57: pip-installed dev track — version-number comparison
        _gh_version = str(_latest_info.get("github_latest_version") or "").strip()
        if _gh_version and not (_gh and _installed):
            # Only show this line if the SHA-based path above didn't fire
            # (avoids two "GitHub main:" lines for git checkouts).
            if is_newer(_gh_version, base_version(__version__)):
                print(
                    f"   {dim('GitHub main:')} {bright_green(_gh_version)} {yellow('(development release available)')}"
                )
            else:
                print(f"   {dim('GitHub main:')} {_gh_version} {dim('(up to date)')}")
    print()

    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Update AgentKthx to the latest version from GitHub.

    On PEP 668 externally-managed-environment errors (Debian/Ubuntu/Fedora
    system Python), prompts the user once with a y/n to retry with
    ``--break-system-packages``. Never silently enables the flag — the user
    always has to opt in after seeing the failure.
    """
    print(f"{bright_cyan('⚖ AgentKthx')} - Updating from GitHub...")
    print(
        f"{dim('Questions or feedback? Join the Discord:')} {cyan('https://discord.gg/vSK3Ba2aQ')}"
    )
    # R07.32: pip needs the git binary to install from a git+https URL. On
    # minimal systems (fresh Debian/Ubuntu containers, Bookworm netinstall)
    # git isn't present — fall back to the GitHub source tarball, which pip
    # can download and build without git (the project has a plain setuptools
    # backend, no setuptools-scm, so a tarball build resolves the version
    # the same way).
    if shutil.which("git") is not None:
        source = "git+https://github.com/VTSTech/AgentKthx.git"
    else:
        source = "https://github.com/VTSTech/AgentKthx/archive/refs/heads/main.tar.gz"
        print(
            f"{dim('git not found — installing from the GitHub source tarball instead (no git needed).')}"
        )
    base_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        source,
        "--force-reinstall",
    ]
    print(f"{dim('Running:')} {' '.join(base_cmd[1:])}")
    print()

    import subprocess as sp

    result = sp.run(base_cmd, capture_output=True, text=True)

    # PEP 668 detection: pip exits non-zero with a stderr mention of
    # "externally-managed-environment" on Debian/Ubuntu/Fedora system
    # Python installs. We do NOT silently add --break-system-packages —
    # we surface the failure and ask the user explicitly.
    if result.returncode != 0 and _is_externally_managed_error(result.stderr):
        print(f"{red('✗ Update failed.')}")
        print(f"{yellow('This Python environment is externally managed (PEP 668).')}")
        print(
            f"{dim('The system Python on Debian/Ubuntu/Fedora blocks pip installs to')} "
            f"{dim('protect the OS package manager — overriding it risks breaking the OS.')}"
        )
        print()
        try:
            choice = (
                input(f"  {dim('Retry with')} --break-system-packages{dim('? [y/N]')} ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice in ("y", "yes"):
            retry_cmd = base_cmd + ["--break-system-packages"]
            print()
            print(f"{dim('Running:')} {' '.join(retry_cmd[1:])}")
            print()
            result = sp.run(retry_cmd, capture_output=True, text=True)
        else:
            print(f"{dim('Skipped. Use a venv, or re-run with --break-system-packages manually.')}")
            return 1

    if result.returncode == 0:
        print(f"{green('✓ Updated successfully!')}")
        # Show the installed version
        try:
            version_result = sp.run(
                [sys.executable, "-m", "agentkthx", "version"],
                capture_output=True,
                text=True,
            )
            if version_result.returncode == 0 and version_result.stdout.strip():
                print(version_result.stdout.strip())
        except Exception:
            pass
    else:
        print(f"{red('✗ Update failed.')}")
        if result.stderr:
            print()
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"  {dim(line)}")
        return 1

    return 0
