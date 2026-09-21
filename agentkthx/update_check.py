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

Behavior (same policy as pip / npm / AWS CLI):

  - At most one network round per source per 24h; results cached in
    ~/.agentkthx/update_check.json (per-source entries, shared timestamp).
  - Failed sources are negatively cached for 6h so offline users never stall,
    and each source fails independently and silently.
  - Opt out entirely with AGENTKTHX_NO_UPDATE_CHECK=1 (also true/yes/on).

Zero dependencies — stdlib urllib only.

Used by cli.py:
  - main()            runs check_for_update() once, stashes the result,
                      prints a pip-style notice after non-chat commands
  - cmd_chat()        prints the notice under the chat banner
  - cmd_version()     shows "Latest on PyPI:" / "GitHub main:" lines

Written by VTSTech — https://www.vts-tech.org
"""

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

from . import __version__

PYPI_JSON_URL = "https://pypi.org/pypi/agentkthx/json"
GITHUB_COMMITS_URL = "https://api.github.com/repos/VTSTech/AgentKthx/commits/HEAD"

#: Cache location — follows the established ~/.agentkthx/ user-data convention.
DEFAULT_CACHE_FILE = Path.home() / ".agentkthx" / "update_check.json"

#: Fresh-check TTL: at most one request per source per day.
SUCCESS_TTL = 24 * 3600
#: Negative-cache TTL for failed checks: retry after 6h, not on every startup.
FAILURE_TTL = 6 * 3600

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
# Cache plumbing
# ----------------------------------------------------------------------------

def _opted_out() -> bool:
    """True if AGENTKTHX_NO_UPDATE_CHECK is truthy (1/true/yes/on)."""
    return os.environ.get("AGENTKTHX_NO_UPDATE_CHECK", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _read_cache(cache_file: Path) -> dict:
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(cache_file: Path, data: dict) -> None:
    """Best-effort cache write — never raises."""
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


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


def _cache_fresh(entry, cached_ts: float, now: float) -> bool:
    """True if a per-source cache entry is still inside its TTL."""
    if not isinstance(entry, dict):
        return False
    ttl = FAILURE_TTL if entry.get("error") else SUCCESS_TTL
    return (now - cached_ts) < ttl


# ----------------------------------------------------------------------------
# The check
# ----------------------------------------------------------------------------

def check_for_update(
    force: bool = False,
    timeout: float = 1.0,
    cache_file: Optional[Path] = None,
) -> Optional[dict]:
    """
    Check both release tracks. Never raises; returns None only when opted out.

    Returns:
        {
          "pypi_latest": "0.6.51" | None,     # latest stable on PyPI
          "github_sha":  "<full sha>" | None, # latest commit on GitHub main
          "from_git":    bool,                # installed copy is a git checkout
          "source":      "cache" | "network", # where the answer(s) came from
          "checked_at":  epoch,
        }
        None when AGENTKTHX_NO_UPDATE_CHECK is set.

    Each source resolves independently: one may answer from cache while the
    other is refetched; one may fail (cached as an error for 6h) without
    affecting the other. The GitHub dev-track check only runs for git
    checkouts — pip installs have no commit hash to compare against.

    Args:
        force:      bypass caches and hit both endpoints (still silent on failure)
        timeout:    socket timeout in seconds — kept small so startup stalls
                    are bounded (once per TTL per source at worst)
        cache_file: override the cache location (tests)
    """
    if _opted_out():
        return None

    cache_file = Path(cache_file) if cache_file else DEFAULT_CACHE_FILE
    now = time.time()
    cached = _read_cache(cache_file)
    cached_ts = float(cached.get("checked_at", 0) or 0)

    # Migrate pre-0.6.51 single-source cache files ({"checked_at",
    # "latest_version"} / {"error": true}) into the per-source layout.
    if "pypi" not in cached and "latest_version" in cached and not cached.get("error"):
        cached["pypi"] = {"latest_version": cached.get("latest_version")}

    from_git = bool(git_hash())
    result = {
        "pypi_latest": None,
        "github_sha": None,
        "from_git": from_git,
        "source": "network",
        "checked_at": now,
    }
    fresh = {}  # per-source entries to persist for this cycle

    # --- Track 1: stable (PyPI) -------------------------------------------
    entry = cached.get("pypi")
    if not force and _cache_fresh(entry, cached_ts, now):
        result["pypi_latest"] = entry.get("latest_version")
        result["source"] = "cache"
    else:
        try:
            result["pypi_latest"] = _fetch_pypi_latest(timeout)
            fresh["pypi"] = {"latest_version": result["pypi_latest"]}
        except Exception:
            fresh["pypi"] = {"error": True}

    # --- Track 2: development (GitHub main commits) ------------------------
    # Only meaningful with a commit baseline: pip installs skip it entirely.
    if from_git:
        entry = cached.get("github")
        if not force and _cache_fresh(entry, cached_ts, now):
            result["github_sha"] = entry.get("sha")
            result["source"] = "cache"
        else:
            try:
                result["github_sha"] = _fetch_github_sha(timeout)
                fresh["github"] = {"sha": result["github_sha"]}
            except Exception:
                fresh["github"] = {"error": True}

    # Persist this cycle: fresh entries win, still-valid cached entries stay.
    _write_cache(cache_file, {**cached, **fresh, "checked_at": now})
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

    Returns None when: no result, no track has anything newer, or the only
    signal is a dev-track commit for a pip-installed copy (no baseline).
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

    gh_sha = str(result.get("github_sha") or "").strip().lower()
    if installed and gh_sha and not gh_sha.startswith(installed):
        lines.append(
            f"Development: new commits on GitHub main ({gh_sha[:7]}) \u2014 Run: agentkthx update"
        )

    if not lines:
        return None
    return "\u26a1 agentkthx updates available:\n   " + "\n   ".join(lines)
