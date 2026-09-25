"""
Update check for AgentKthx — "how will users know there is a new version?"

Two release tracks, two sources (VTSTech does not cut GitHub Releases, so the
GitHub *commits* API is the source of truth for the dev track):

  - **Stable** — a newer package exists on PyPI
    (https://pypi.org/pypi/agentkthx/json). Suggested fix depends on install
    source: pip installs -> `pip install --upgrade agentkthx`; git checkouts
    -> `agentkthx update`.
  - **Development** — new commits exist on GitHub main
    (https://api.github.com/repos/VTSTech/AgentKthx/commits/HEAD) that are not
    the commit this checkout was built from. Only reported for git checkouts:
    a pip install carries no commit hash, so there is no baseline to compare
    against. Action: `agentkthx update`.

Behavior (R07.00: cache removed — always live):

  - Every check queries both endpoints live; nothing is written to or read
    from disk. The R06.57 hourly cache (~/.agentkthx/update_check.json,
    1h success / 15min negative TTLs) was removed after it kept hiding
    freshly-cut releases from the developer — the one user who needs the
    live answer most. A stale cache file from an older install is simply
    ignored (safe to delete).
  - Each source fails independently and silently; a failed source leaves
    its field None for this invocation and is retried live on the next one
    (no negative cache).
  - Offline cost: at most one timeout (default 1s) per source, once per
    process — opt out entirely with AGENTKTHX_NO_UPDATE_CHECK=1
    (also true/yes/on).

Zero dependencies — stdlib urllib only.

Used by the CLI:
  - main()            runs check_for_update() once per process, stashes
                      the result, prints a pip-style notice after
                      non-chat commands
  - cmd_chat()        prints the notice under the chat banner
  - cmd_version()     shows "Latest on PyPI:" / "GitHub main:" lines

Written by VTSTech — https://www.vts-tech.org
"""

import json
import os
import time
import urllib.request
from typing import Optional

from . import __version__

PYPI_JSON_URL = "https://pypi.org/pypi/agentkthx/json"
GITHUB_COMMITS_URL = "https://api.github.com/repos/VTSTech/AgentKthx/commits/HEAD"
# R06.57: raw URL for the version string in __init__.py on GitHub main.
# Used for the pip-installed dev track — pip installs have no commit hash
# baseline, so we compare the installed version number directly against the
# version number declared in __init__.py on main. This surfaces dev releases
# (R06.55, R06.56, R06.57, ...) that haven't been pushed to PyPI yet.
GITHUB_RAW_INIT_URL = "https://raw.githubusercontent.com/VTSTech/AgentKthx/main/agentkthx/__init__.py"

#: indirection so tests can monkeypatch the network call
_urlopen = urllib.request.urlopen


# ----------------------------------------------------------------------------
# Version string helpers
# ----------------------------------------------------------------------------

def base_version(version: str = "") -> str:
    """
    Strip local/git decorations from a version string.

    "0.6.51-f754294" -> "0.6.51"   (git checkout; _get_git_short_hash suffix)
    "0.6.51+local"   -> "0.6.51"   (PEP 440 local segment)
    "0.6.51"         -> "0.6.51"
    """
    s = str(version if version else __version__).strip()
    s = s.split("+", 1)[0]
    s = s.split("-", 1)[0]
    return s.strip()


def git_hash(version: str = "") -> str:
    """
    Return the short git commit hash embedded in a version string, or "".

    "0.6.51-f754294" -> "f754294"  (running from a git checkout)
    "0.6.51"         -> ""         (pip-installed — no commit baseline)
    """
    s = str(version if version else __version__).strip()
    if "+" in s:
        s = s.split("+", 1)[0]
    if "-" in s:
        return s.split("-", 1)[1].strip()
    return ""


def parse_version(version: str) -> tuple:
    """
    Parse a version string into a comparable int tuple (PEP-440-lite).

    Handles the project's actual scheme (0.6.51) plus git-hash suffixes.
    Non-numeric segments degrade to 0 instead of raising. Pads to at least
    three segments so "0.6" compares sanely against "0.6.41".
    """
    if not str(version).strip():
        return (0, 0, 0)
    s = base_version(version)
    parts = []
    for seg in s.split("."):
        digits = ""
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` is strictly newer than `current`."""
    try:
        a, b = parse_version(latest), parse_version(current)
    except Exception:
        return False
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


# ----------------------------------------------------------------------------
# Opt-out + network fetches
# ----------------------------------------------------------------------------

def _opted_out() -> bool:
    """True if AGENTKTHX_NO_UPDATE_CHECK is truthy (1/true/yes/on)."""
    return os.environ.get("AGENTKTHX_NO_UPDATE_CHECK", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _fetch_json(url: str, timeout: float) -> dict:
    """GET a JSON document; raises on any network/parse problem."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"agentkthx/{base_version()} (update-check)",
            "Accept": "application/json",
        },
    )
    resp = _urlopen(req, timeout=timeout)
    try:
        return json.loads(resp.read().decode("utf-8"))
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _fetch_pypi_latest(timeout: float) -> str:
    payload = _fetch_json(PYPI_JSON_URL, timeout)
    latest = str(payload["info"]["version"]).strip()
    if not latest:
        raise ValueError("PyPI returned an empty version")
    return latest


def _fetch_github_sha(timeout: float) -> str:
    payload = _fetch_json(GITHUB_COMMITS_URL, timeout)
    sha = str(payload["sha"]).strip().lower()
    if not sha:
        raise ValueError("GitHub returned an empty commit sha")
    return sha


def _fetch_github_latest_version(timeout: float) -> str:
    """Fetch the version string declared in __init__.py on GitHub main.

    R06.57: Pip-installed users have no git commit hash to compare against
    the dev track, so the SHA-based comparison in format_notice silently
    skips them. Fetching raw.githubusercontent.com/.../__init__.py and
    parsing ``__version__ = "X.Y.Z"`` gives us a version-number baseline
    they can be compared against, surfacing dev releases (R06.55+,
    R06.56+, ...) that haven't been pushed to PyPI yet.

    Returns the bare version string (e.g. ``"0.6.57"``), no git suffix.
    Raises on any network / parse problem — caller caches the failure.
    """
    import re
    req = urllib.request.Request(
        GITHUB_RAW_INIT_URL,
        headers={
            "User-Agent": f"agentkthx/{base_version()} (update-check)",
            "Accept": "text/plain; charset=utf-8",
        },
    )
    resp = _urlopen(req, timeout=timeout)
    try:
        text = resp.read().decode("utf-8", errors="replace")
    finally:
        try:
            resp.close()
        except Exception:
            pass
    # Match: __version__ = "0.6.57"  # R06.57
    #        __version__ = "0.6.57"
    #        __version__='0.6.57'
    #        __version__ = ""          (empty → caught below as "empty")
    m = re.search(r"""__version__\s*=\s*['"]([^'"]*)['"]""", text)
    if not m:
        raise ValueError("GitHub __init__.py has no __version__ assignment")
    version = m.group(1).strip()
    if not version:
        raise ValueError("GitHub __init__.py __version__ is empty")
    return base_version(version)


# ----------------------------------------------------------------------------
# The check — always live (R07.00: on-disk cache removed)
# ----------------------------------------------------------------------------

def check_for_update(timeout: float = 1.0) -> Optional[dict]:
    """
    Check both release tracks — always live, never cached. Never raises;
    returns None only when opted out.

    Returns:
        {
          "pypi_latest": "0.6.51" | None,      # latest stable on PyPI
          "github_sha":  "<full sha>" | None,    # latest commit on GitHub main (git checkouts only)
          "github_latest_version": "0.6.57" | None,  # R06.57: __init__.py version on main
          "from_git":    bool,                   # installed copy is a git checkout
          "checked_at":  epoch,                  # when this live check ran
        }
        None when AGENTKTHX_NO_UPDATE_CHECK is set.

    Each source resolves independently and silently: one may fail without
    affecting the others, and a failed source is retried on the next
    invocation (no negative cache). The GitHub SHA check only runs for git
    checkouts (pip installs have no commit hash to compare against); the
    GitHub version-number check (R06.57) runs for everyone so pip-installed
    users can see dev releases that haven't been pushed to PyPI yet.

    R07.00: the on-disk cache (~/.agentkthx/update_check.json, 1h/15min
    TTLs) was removed — every invocation fetches the latest live results.
    The per-process stash in cli/banner.py still avoids duplicate fetches
    within a single run.

    Args:
        timeout:    socket timeout in seconds — kept small so startup stalls
                    are bounded (once per process at worst)
    """
    if _opted_out():
        return None

    from_git = bool(git_hash())
    result = {
        "pypi_latest": None,
        "github_sha": None,
        "github_latest_version": None,  # R06.57
        "from_git": from_git,
        "checked_at": time.time(),
    }

    # --- Track 1: stable (PyPI) -------------------------------------------
    try:
        result["pypi_latest"] = _fetch_pypi_latest(timeout)
    except Exception:
        pass

    # --- Track 2a: development (GitHub main commit SHA) — git checkouts only
    # Only meaningful with a commit baseline: pip installs skip it entirely.
    if from_git:
        try:
            result["github_sha"] = _fetch_github_sha(timeout)
        except Exception:
            pass

    # --- Track 2b: development (GitHub main __init__.py version) — everyone
    # R06.57: Pip-installed users have no commit hash baseline, so the SHA
    # comparison silently skips them. Fetching the version number declared
    # in __init__.py on main gives them a baseline they can be compared
    # against — surfaces dev releases (R06.55+, R06.56+, ...) that haven't
    # been pushed to PyPI. Git checkouts also benefit: if the SHA fetch
    # failed but the version fetch succeeded, we still have a signal.
    try:
        result["github_latest_version"] = _fetch_github_latest_version(timeout)
    except Exception:
        pass

    return result


# ----------------------------------------------------------------------------
# Notice formatting
# ----------------------------------------------------------------------------

def format_notice(result: Optional[dict], current: str = "") -> Optional[str]:
    """
    Build the pip-style "updates available" text, or None if nothing to say.

    Both tracks can appear in one block:
        ⚡ agentkthx updates available:
           Stable: 0.6.50 → 0.6.51 — Run: pip install --upgrade agentkthx
           Development: new commits on GitHub main (f754294) — Run: agentkthx update

    R06.57: For pip-installed users (no git hash), the dev track now also
    fires when the version number in __init__.py on GitHub main is newer
    than the installed version — surfaces dev releases that haven't been
    pushed to PyPI yet:
        Development: 0.6.54 → 0.6.57 on GitHub main — Run: pip install --force-reinstall git+https://github.com/VTSTech/AgentKthx.git

    Returns None when: no result, no track has anything newer.
    """
    if not result:
        return None
    cur = base_version(current if current else __version__)
    installed = git_hash(__version__)

    lines = []
    pypi_latest = str(result.get("pypi_latest") or "").strip()
    if pypi_latest and is_newer(pypi_latest, cur):
        # A git checkout should stay on the git track — `agentkthx update`
        # fast-forwards to main, which includes the stable release anyway.
        stable_cmd = "agentkthx update" if installed else "pip install --upgrade agentkthx"
        lines.append(f"Stable: {cur} \u2192 {pypi_latest} \u2014 Run: {stable_cmd}")

    # R06.57: GitHub dev track — two complementary baselines.
    # - Git checkouts: compare commit SHAs (existing path).
    # - Pip installs:  compare version numbers from __init__.py on main
    #                  (new path — surfaces dev releases not on PyPI).
    gh_sha = str(result.get("github_sha") or "").strip().lower()
    gh_version = str(result.get("github_latest_version") or "").strip()

    if installed and gh_sha and not gh_sha.startswith(installed):
        # Git checkout path — SHA-based detection (unchanged since R06.51).
        lines.append(
            f"Development: new commits on GitHub main ({gh_sha[:7]}) \u2014 Run: agentkthx update"
        )
    elif gh_version and is_newer(gh_version, cur):
        # R06.57: Pip-installed path — version-number detection. The
        # ``pip install --force-reinstall git+...`` command grabs the
        # latest main HEAD regardless of what's on PyPI.
        #
        # Skip the dev notice when the PyPI stable track already surfaced
        # an upgrade to a version >= gh_version — i.e. stable has caught
        # up to dev, so the dev track adds no extra signal. (Example: PyPI
        # is 0.6.57 and GitHub main is also 0.6.57 — the stable notice
        # already covers it. But if PyPI is 0.6.55 and GitHub is 0.6.57,
        # we still want to surface the dev track because it's newer.)
        pypi_already_covers_dev = (
            pypi_latest
            and is_newer(pypi_latest, cur)            # PyPI is firing an upgrade notice
            and not is_newer(gh_version, pypi_latest)  # gh_version <= pypi_latest
        )
        if not pypi_already_covers_dev:
            cmd = "agentkthx update" if installed else \
                  "pip install --force-reinstall git+https://github.com/VTSTech/AgentKthx.git"
            lines.append(
                f"Development: {cur} \u2192 {gh_version} on GitHub main \u2014 Run: {cmd}"
            )

    if not lines:
        return None
    return "\u26a1 agentkthx updates available:\n   " + "\n   ".join(lines)
