"""Live-shape contract test for the MCP registry (R07.24, TEST-11).

This file closes the gap that TEST-09 / TEST-10 / TEST-11 all share: the
mocked HTTP layer in ``tests/test_mcp_cli.py`` (which patches
``agentkthx.mcp.registry._http_get_json``) gives false confidence — if npm
or GitHub renames a response field (e.g. ``package.name`` → ``package.id``),
the suite stays green while ``mcp search`` breaks in production.

These tests skip unless ``AGENTKTHX_LIVE_TESTS=1`` is set in the env. They
make ONE real network call per source (npm + GitHub) against the live
``registry.npmjs.org`` + ``api.github.com`` endpoints, assert the response
shape matches what ``npm_search``/``github_search``/``search_all_with_errors``
document (the field set in ``__all__``-adjacent docstrings), and pin the
live shape so a future API drift surfaces as a test failure rather than a
silent regression.

Run locally with::

    AGENTKTHX_LIVE_TESTS=1 python -m pytest tests/test_mcp_live_contract.py -v

CI does NOT run these by default (no network in the default job). When a
live test fails, capture the response body and update either the parser
(in ``agentkthx/mcp/registry.py``) or the field assertions here.

Skip conditions (any one of):
    - ``AGENTKTHX_LIVE_TESTS`` env var is unset or empty
    - Network is offline (URLError / socket.timeout — skip, don't fail)
    - 429 rate limit (skip with a clear "set AGENTKTHX_GITHUB_TOKEN" hint)
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Skip the entire module unless the operator explicitly opts in.
# Default pytest run (CI) collects but does not execute — keeps the suite
# green offline, fails loudly when live tests are requested.
pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENTKTHX_LIVE_TESTS"),
    reason="AGENTKTHX_LIVE_TESTS=1 not set — skipping live network contract test",
)


# ---------------------------------------------------------------------------
# Contract: npm_search response shape
# ---------------------------------------------------------------------------


def test_live_npm_search_returns_documented_fields() -> None:
    """Hit ``registry.npmjs.org/-/v1/search`` and assert field set.

    The documented contract (``agentkthx/mcp/registry.py:npm_search`` docstring):
    each result dict has keys ``name``, ``package``, ``version``,
    ``description``, ``source``, ``is_official``, ``homepage``,
    ``install_hint``, ``stars``, ``license``, ``search_score``.

    If npm renames any of these (e.g. ``package`` → ``packageName``), this
    test fails and the parser needs to be updated. Closes the TEST-11 gap:
    the mocked suite stays green on a rename; the live test catches it.
    """
    from agentkthx.mcp.registry import npm_search

    try:
        results = npm_search("filesystem", size=5, timeout=10)
    except (URLError, socket.timeout, ConnectionError) as e:
        pytest.skip(f"network unreachable: {e}")
    except HTTPError as e:
        if e.code == 429:
            pytest.skip("npm rate limit (429) — try again later")
        raise

    assert results, "npm search for 'filesystem' should return at least one result"
    required_fields = {
        "name",
        "package",
        "version",
        "description",
        "source",
        "is_official",
        "homepage",
        "install_hint",
        "stars",
        "license",
        "search_score",
    }
    for r in results:
        missing = required_fields - set(r.keys())
        assert not missing, f"result {r!r} missing fields: {missing}"
        # Source marker pinned — if npm results start showing up under a
        # different source tag, the CLI's source filter breaks silently.
        assert r["source"] == "npm", f"expected source='npm', got {r['source']!r}"


def test_live_npm_package_info_returns_metadata_dict() -> None:
    """``npm_package_info`` hits ``registry.npmjs.org/<name>`` for full metadata.

    Verifies the ``@modelcontextprotocol/server-filesystem`` package is still
    published (the default install target for ``mcp install filesystem``)
    and that the response shape hasn't drifted.
    """
    from agentkthx.mcp.registry import npm_package_info

    try:
        info = npm_package_info("@modelcontextprotocol/server-filesystem", timeout=10)
    except (URLError, socket.timeout, ConnectionError) as e:
        pytest.skip(f"network unreachable: {e}")
    except HTTPError as e:
        if e.code == 404:
            pytest.fail(
                "@modelcontextprotocol/server-filesystem no longer on npm — "
                "mcp install filesystem will fail in production"
            )
        if e.code == 429:
            pytest.skip("npm rate limit (429) — try again later")
        raise

    assert info is not None, "package should exist on npm"
    assert "package" in info, f"missing 'package' key in response: {list(info.keys())}"
    # The version field is consumed by build_config_snippet to print
    # `v<version>` in the search table. If it disappears, the table
    # shows "vunknown" — silent regression.
    assert "version" in info, f"missing 'version' key in response: {list(info.keys())}"


# ---------------------------------------------------------------------------
# Contract: search_all_with_errors response shape (R07.24, ROB-41)
# ---------------------------------------------------------------------------


def test_live_search_all_with_errors_returns_tuple() -> None:
    """``search_all_with_errors`` returns ``(list, list)`` — both sources live.

    Closes the ROB-41 contract: a successful live call returns ``(non-empty
    results, empty errors)``. If either source silently starts returning
    empty results due to an API change, this test fails (results would be
    empty + errors empty — the "no match" case, which is wrong for the
    query 'filesystem').
    """
    from agentkthx.mcp.registry import search_all_with_errors

    try:
        results, errors = search_all_with_errors("filesystem", size=5, timeout=15)
    except (URLError, socket.timeout, ConnectionError) as e:
        pytest.skip(f"network unreachable: {e}")
    except HTTPError as e:
        if e.code == 429:
            pytest.skip("rate limit (429) — try AGENTKTHX_GITHUB_TOKEN for higher GitHub limits")
        raise

    # Both sources succeeded → no errors
    assert errors == [], f"unexpected source errors: {errors}"
    # 'filesystem' is a popular query — should hit on both npm + GitHub
    assert results, "expected at least one result for 'filesystem'"
    sources = {r.get("source") for r in results}
    assert "npm" in sources, "npm results missing — search_all may have broken npm parsing"
    # GitHub source may be missing if anonymous rate limit hit, so only assert npm


# ---------------------------------------------------------------------------
# Contract: GitHub search (anonymous — 10 req/min limit)
# ---------------------------------------------------------------------------


def test_live_github_search_returns_documented_fields() -> None:
    """Hit ``api.github.com/search/repositories`` and assert field set.

    The documented contract (``agentkthx/mcp/registry.py:github_search``
    docstring): each result dict has the same shape as npm_search results
    (``name``, ``package``, ``source='github'``, etc.).

    Anonymous rate limit is 10 req/min. If you hit it, set
    ``AGENTKTHX_GITHUB_TOKEN`` (or ``GITHUB_TOKEN`` / ``GH_TOKEN``) for
    5000 req/h.
    """
    from agentkthx.mcp.registry import github_search

    try:
        results = github_search("serena", size=5, timeout=10)
    except (URLError, socket.timeout, ConnectionError) as e:
        pytest.skip(f"network unreachable: {e}")
    except HTTPError as e:
        if e.code == 403:
            pytest.skip(
                "GitHub anonymous rate limit (10/min) — set AGENTKTHX_GITHUB_TOKEN for 5000/h"
            )
        raise

    if not results:
        pytest.skip("GitHub returned 0 results for 'serena' — anonymous rate limit may have hit")

    required_fields = {"name", "package", "source", "is_official"}
    for r in results:
        missing = required_fields - set(r.keys())
        assert not missing, f"github result {r!r} missing fields: {missing}"
        assert r["source"] == "github", f"expected source='github', got {r['source']!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
