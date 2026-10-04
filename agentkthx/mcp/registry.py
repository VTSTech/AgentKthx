"""
\u269b\ufe0f AgentKthx \u2014 Live MCP server registry (npm + GitHub)

Stdlib-only live search for MCP servers across two sources:

1. **npm registry** (``registry.npmjs.org``) \u2014 covers all
   ``@modelcontextprotocol/server-*`` packages plus community ones.
   Public, no auth, JSON. This is where the packages actually live.
2. **GitHub repository search** (``api.github.com/search/repositories``)
   \u2014 catches servers published only as repos, not npm packages
   (e.g. oraios/serena, jlowin/fastmcp). Works anonymously at
   10 req/min; set ``AGENTKTHX_GITHUB_TOKEN`` for 5000 req/min.

Results are normalized to a common shape::

    {
        "name":           "<short-name>",       # derived from package/repo
        "package":        "<full-identifier>",  # npm name or "github:owner/repo"
        "version":        "<version>",          # from npm, or "unknown"
        "description":    "<one-liner>",
        "source":         "npm" | "github",
        "is_official":    True | False,         # @modelcontextprotocol/* = official
        "homepage":       "<url>",
        "install_hint":   "<copy-paste cmd>",   # e.g. "npx -y @modelcontextprotocol/server-filesystem"
        "stars":          <int>,                # GitHub stars (0 for npm)
        "license":        "<spdx>",             # from npm, or "" for GitHub
    }

All network calls use stdlib ``urllib.request`` with a 10-second timeout.
Failures (offline, DNS, timeout, HTTP error) raise
:class:`MCPRegistryError` with a clear message; the caller is expected
to catch and degrade gracefully (e.g. fall back to cache, or print an
error).

Public API
----------
.. autofunction:: search_all
.. autofunction:: npm_search
.. autofunction:: github_search
.. autofunction:: npm_package_info
.. autofunction:: derive_short_name
.. autofunction:: build_config_snippet
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 10  # seconds

NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
NPM_PKG_URL = "https://registry.npmjs.org/{name}"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

# MCP-relevance filter: a package matches if its name or description
# contains any of these tokens (case-insensitive). Without this filter,
# a generic search like "filesystem" would return hundreds of
# unrelated packages.
_MCP_TOKENS = ("mcp", "model context protocol", "modelcontextprotocol")


class MCPRegistryError(RuntimeError):
    """Raised when a registry lookup fails (network, HTTP, or parse error)."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _http_get_json(
    url: str, *, headers: dict[str, str] | None = None, timeout: int = DEFAULT_TIMEOUT
) -> Any:
    """GET ``url`` and return the parsed JSON. Raise :class:`MCPRegistryError` on any failure."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise MCPRegistryError(f"HTTP {e.code} from {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise MCPRegistryError(f"network error fetching {url}: {e.reason}") from e
    except TimeoutError as e:
        raise MCPRegistryError(f"timeout fetching {url} ({timeout}s)") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise MCPRegistryError(f"invalid JSON from {url}: {e}") from e


def _is_mcp_relevant(name: str, description: str) -> bool:
    """Heuristic: does this package/repo look like an MCP server?

    True if the name or description mentions MCP or Model Context Protocol.
    False otherwise. Used to filter generic search results.
    """
    haystack = f"{name} {description}".lower()
    return any(token in haystack for token in _MCP_TOKENS)


def derive_short_name(package_or_repo: str) -> str:
    """Derive a short MCP server name from an npm package name or GitHub repo.

    Examples::

        >>> derive_short_name("@modelcontextprotocol/server-filesystem")
        'filesystem'
        >>> derive_short_name("@modelcontextprotocol/server-sequential-thinking")
        'sequential-thinking'
        >>> derive_short_name("mcp-server-fetch")
        'fetch'
        >>> derive_short_name("oraios/serena")
        'serena'
        >>> derive_short_name("@alfe.ai/github-mcp")
        'github'
    """
    s = package_or_repo.strip()
    # Official scope: @modelcontextprotocol/server-<name>
    prefix = "@modelcontextprotocol/server-"
    if s.startswith(prefix):
        return s[len(prefix) :]
    # Common unscoped pattern: mcp-server-<name>
    if s.startswith("mcp-server-"):
        return s[len("mcp-server-") :]
    # GitHub repo: owner/repo \u2014 take the repo segment
    if "/" in s and not s.startswith("@"):
        s = s.split("/", 1)[1]
    # Scoped package: @scope/name \u2014 take the name segment
    if s.startswith("@") and "/" in s:
        s = s.split("/", 1)[1]
    # Strip common suffixes
    for suffix in ("-mcp", "-server", "_mcp", "_server", "-modelcontextprotocol"):
        if s.lower().endswith(suffix):
            s = s[: -len(suffix)]
            break
    # Sanitize: keep alnum + dash + underscore (matches MCPServerConfig name rule)
    s = re.sub(r"[^A-Za-z0-9_-]", "-", s)
    return s or "server"


def build_config_snippet(
    *,
    package: str,
    short_name: str | None = None,
    source: str = "npm",
    version: str = "",
    description: str = "",
    command: str | None = None,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """Build a ready-to-paste mcp.json entry for one server.

    For npm packages, defaults to ``{"command": "npx", "args": ["-y", "<package>"]}``.
    For GitHub repos, defaults to ``{"command": "uvx", "args": ["--from",
    "git+https://github.com/<owner>/<repo>", "<repo>"]}`` \u2014 a best-effort
    guess; the user should verify and adjust.
    """
    name = short_name or derive_short_name(package)
    if command is None or args is None:
        if source == "github":
            # Best-effort: assume uvx + git URL. The user may need to adjust
            # the entrypoint (e.g. "serena start-mcp-server").
            repo_url = package.replace("github:", "") if package.startswith("github:") else package
            repo_seg = repo_url.split("/")[-1] if "/" in repo_url else repo_url
            command = command or "uvx"
            args = args or ["--from", f"git+https://github.com/{repo_url}", repo_seg]
        else:
            command = command or "npx"
            args = args or ["-y", package]
    comment = f"Installed via `mcp install`. Source: {source}"
    if version:
        comment += f" v{version}"
    if description:
        # Truncate description to keep the comment on one line
        desc = description.split("\n")[0][:80]
        comment += f". {desc}"
    return {
        "name": name,
        "command": command,
        "args": args,
        "enabled": True,
        "comment": comment,
    }


# ---------------------------------------------------------------------------
# npm
# ---------------------------------------------------------------------------


def npm_search(
    query: str, *, size: int = 25, timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Search the npm registry for MCP-relevant packages matching ``query``.

    The npm search endpoint returns packages whose name/description
    matches the text query. We further filter to packages that look
    like MCP servers (name or description mentions "mcp" or "model
    context protocol").

    Args:
        query: Search text (e.g. "filesystem", "git", "mcp").
        size: Max results from npm (1-250).
        timeout: Per-request timeout in seconds.

    Returns:
        List of normalized result dicts. Sorted by npm search score
        (descending).
    """
    size = max(1, min(250, int(size)))
    params = urllib.parse.urlencode({"text": query, "size": str(size)})
    url = f"{NPM_SEARCH_URL}?{params}"
    data = _http_get_json(url, timeout=timeout)
    objects = data.get("objects", []) if isinstance(data, dict) else []

    results: list[dict[str, Any]] = []
    for obj in objects:
        pkg = obj.get("package", {})
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name", "")
        desc = pkg.get("description", "") or ""
        if not _is_mcp_relevant(name, desc):
            continue
        score = obj.get("score", {}).get("final", 0)
        links = pkg.get("links", {}) if isinstance(pkg.get("links"), dict) else {}
        results.append(
            {
                "name": derive_short_name(name),
                "package": name,
                "version": pkg.get("version", ""),
                "description": desc,
                "source": "npm",
                "is_official": name.startswith("@modelcontextprotocol/"),
                "homepage": links.get("homepage", "") or links.get("npm", ""),
                "install_hint": f"npx -y {name}",
                "stars": 0,
                "license": (
                    (pkg.get("license") or "") if isinstance(pkg.get("license"), str) else ""
                ),
                "search_score": float(score) if score else 0,
            }
        )
    # Sort by search score descending (npm's relevance ranking)
    results.sort(key=lambda r: r.get("search_score", 0), reverse=True)
    return results


def npm_package_info(name: str, *, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch full metadata for one npm package.

    Returns None if the package doesn't exist (404). Raises
    :class:`MCPRegistryError` on other errors.
    """
    # npm registry requires URL-encoding of scoped package names:
    # @modelcontextprotocol/server-filesystem -> @modelcontextprotocol%2Fserver-filesystem
    encoded = urllib.parse.quote(name, safe="")
    url = NPM_PKG_URL.format(name=encoded)
    try:
        data = _http_get_json(url, timeout=timeout)
    except MCPRegistryError as e:
        if "HTTP 404" in str(e):
            return None
        raise
    if not isinstance(data, dict):
        return None
    latest = (data.get("dist-tags") or {}).get("latest", "")
    versions = data.get("versions") or {}
    latest_info = versions.get(latest, {}) if isinstance(versions, dict) else {}
    repo = data.get("repository")
    repo_url = ""
    if isinstance(repo, dict):
        repo_url = repo.get("url", "")
    elif isinstance(repo, str):
        repo_url = repo
    homepage = data.get("homepage") or ""
    if not homepage and repo_url:
        homepage = repo_url
    return {
        "name": data.get("name", name),
        "package": data.get("name", name),
        "version": latest,
        "description": data.get("description", "") or "",
        "source": "npm",
        "is_official": name.startswith("@modelcontextprotocol/"),
        "homepage": homepage,
        "install_hint": f"npx -y {name}",
        "stars": 0,
        "license": latest_info.get("license", "") if isinstance(latest_info, dict) else "",
        "repository_url": repo_url,
    }


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def _github_token() -> str | None:
    """Return a GitHub token from env, or None."""
    for env_var in ("AGENTKTHX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        token = os.environ.get(env_var, "").strip()
        if token:
            return token
    return None


def github_search(
    query: str, *, size: int = 25, timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Search GitHub repositories for MCP servers matching ``query``.

    Uses the GitHub repository search API. Works anonymously at
    10 requests/minute; set ``AGENTKTHX_GITHUB_TOKEN`` (or
    ``GITHUB_TOKEN``) for 5000/minute.

    The query is augmented with ``mcp`` to bias toward MCP-relevant
    repos. Results are filtered to those whose name or description
    mentions MCP.
    """
    # Augment the query to bias toward MCP servers
    full_query = f"{query} mcp" if query.lower() != "mcp" else "mcp server"
    size = max(1, min(100, int(size)))
    params = urllib.parse.urlencode(
        {
            "q": full_query,
            "sort": "stars",
            "order": "desc",
            "per_page": str(size),
        }
    )
    url = f"{GITHUB_SEARCH_URL}?{params}"
    headers: dict[str, str] = {}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _http_get_json(url, headers=headers, timeout=timeout)
    items = data.get("items", []) if isinstance(data, dict) else []

    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = item.get("full_name", "")
        desc = item.get("description", "") or ""
        if not _is_mcp_relevant(full_name, desc):
            continue
        results.append(
            {
                "name": derive_short_name(full_name),
                "package": f"github:{full_name}",
                "version": "unknown",
                "description": desc,
                "source": "github",
                "is_official": full_name.startswith("modelcontextprotocol/"),
                "homepage": item.get("html_url", "") or item.get("homepage", ""),
                "install_hint": f"see {item.get('html_url', '')} for install instructions",
                "stars": int(item.get("stargazers_count", 0)),
                "license": "",
                "search_score": float(item.get("stargazers_count", 0)),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Combined search
# ---------------------------------------------------------------------------


def search_all(
    query: str, *, size: int = 25, timeout: int = DEFAULT_TIMEOUT
) -> list[dict[str, Any]]:
    """Search npm + GitHub, dedupe by package name, sort by relevance.

    Failures from either source are swallowed \u2014 the other source's
    results are still returned. If both fail, returns an empty list.
    """
    npm_results: list[dict[str, Any]] = []
    github_results: list[dict[str, Any]] = []

    try:
        npm_results = npm_search(query, size=size, timeout=timeout)
    except MCPRegistryError:
        pass  # degrade gracefully

    try:
        github_results = github_search(query, size=size, timeout=timeout)
    except MCPRegistryError:
        pass  # degrade gracefully

    # Dedupe by package identifier (npm name or github:owner/repo)
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for r in npm_results + github_results:
        pkg = r.get("package", "")
        if pkg in seen:
            continue
        seen.add(pkg)
        merged.append(r)

    # Sort: official first, then by search_score descending
    merged.sort(key=lambda r: (not r.get("is_official", False), -r.get("search_score", 0)))
    return merged


__all__ = [
    "MCPRegistryError",
    "DEFAULT_TIMEOUT",
    "npm_search",
    "npm_package_info",
    "github_search",
    "search_all",
    "derive_short_name",
    "build_config_snippet",
]
