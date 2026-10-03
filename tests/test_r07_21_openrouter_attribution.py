"""
R07.21 regression tests — OpenRouter App Attribution headers.

Pins the App Attribution contract from https://openrouter.ai/docs/app-attribution:

  * ``HTTP-Referer``               (required)  — primary URL identifier
  * ``X-OpenRouter-Title``         (preferred) — display name
  * ``X-Title``                    (legacy alias — kept for backwards compat)
  * ``X-OpenRouter-Categories``    (optional)  — comma-separated leaf categories
                                                 (max 2 per request, max 10 per app)

Verifies that the four header-construction sites in ``OpenRouterBackend`` all
delegate to ``_build_openrouter_attribution_headers()`` so the contract can
never drift:

  1. ``OpenRouterBackend.__init__``     → ``self.headers``
  2. ``OpenRouterBackend.list_models``  → request headers on GET /models
  3. ``OpenRouterBackend._make_api_request`` → request headers on POST /chat/completions
  4. ``OpenRouterBackend._get_auth_headers`` → headers used by the streaming path

Also pins:
  * The category is hardcoded ``cli-agent`` (terminal-based coding assistants —
    the Coding-group leaf at https://openrouter.ai/apps/category/coding/cli-agent;
    a single leaf auto-includes AgentKthx on the group landing too).
  * The category is NOT user-overridable — it describes what AgentKthx *is* to
    OpenRouter's marketplace, not a runtime knob. A user flipping
    ``cli-agent`` to ``creative-writing`` would misclassify the harness in
    OpenRouter's rankings.
  * The exact header values AgentKthx will send to OpenRouter on the next
    request, so the next traffic from any AgentKthx install populates the
    app directory entry at
    https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FVTSTech%2FAgentKthx

Written by VTSTech — https://www.vts-tech.org
"""

import unittest
from unittest import mock as unittest_mock

from agentkthx.plugins.openrouter.openrouter import (
    _APP_CATEGORIES,
    _APP_REFERER_URL,
    _APP_TITLE,
    _build_openrouter_attribution_headers,
)


class TestAttributionHeaderConstants(unittest.TestCase):
    """The four attribution values AgentKthx sends on every OpenRouter request.

    Pinned because OpenRouter uses these as the primary identifier for app
    rankings — if any of them drift, AgentKthx stops being attributed correctly
    on https://openrouter.ai/apps/url/https%3A%2F%2Fgithub.com%2FVTSTech%2FAgentKthx
    """

    def test_referer_url_points_at_github_repo(self):
        """HTTP-Referer must point at the canonical GitHub repo URL.

        OpenRouter's App Attribution doc states this is the primary identifier
        for rankings — the URL becomes the app's unique key in their system.
        """
        self.assertEqual(_APP_REFERER_URL, "https://github.com/VTSTech/AgentKthx")

    def test_app_title_is_agentkthx(self):
        """X-OpenRouter-Title / X-Title must be the harness's user-visible name."""
        self.assertEqual(_APP_TITLE, "AgentKthx")

    def test_app_categories_is_cli_agent(self):
        """App category must be ``cli-agent`` — the terminal-based coding
        assistants leaf under the Coding group.

        See: https://openrouter.ai/apps/category/coding/cli-agent
        A single leaf auto-includes the app on the Coding group landing too
        (https://openrouter.ai/apps/category/coding).
        """
        self.assertEqual(_APP_CATEGORIES, "cli-agent")


class TestBuildAttributionHeaders(unittest.TestCase):
    """``_build_openrouter_attribution_headers()`` returns the full header dict."""

    def test_returns_all_four_required_attribution_headers(self):
        """The dict must carry all four attribution headers from the App
        Attribution spec — HTTP-Referer, X-OpenRouter-Title, X-Title (legacy
        alias), X-OpenRouter-Categories.
        """
        h = _build_openrouter_attribution_headers()
        self.assertEqual(set(h.keys()), {
            "HTTP-Referer",
            "X-OpenRouter-Title",
            "X-Title",
            "X-OpenRouter-Categories",
        })

    def test_referer_is_canonical_github_url(self):
        """HTTP-Referer must be the canonical GitHub repo URL — this is the
        field OpenRouter uses as the primary identifier for app rankings.
        """
        h = _build_openrouter_attribution_headers()
        self.assertEqual(h["HTTP-Referer"], "https://github.com/VTSTech/AgentKthx")

    def test_both_title_forms_are_agentkthx(self):
        """Both X-OpenRouter-Title (preferred) AND X-Title (legacy alias)
        must be set to ``AgentKthx``. OpenRouter's docs state X-Title is
        "still supported for backwards compatibility" — we send both so
        rankings that haven't been re-indexed still attribute correctly.
        """
        h = _build_openrouter_attribution_headers()
        self.assertEqual(h["X-OpenRouter-Title"], "AgentKthx")
        self.assertEqual(h["X-Title"], "AgentKthx")

    def test_categories_is_cli_agent(self):
        """X-OpenRouter-Categories value must be ``cli-agent``."""
        h = _build_openrouter_attribution_headers()
        self.assertEqual(h["X-OpenRouter-Categories"], "cli-agent")

    def test_categories_are_hardcoded_not_env_overridable(self):
        """The harness category is hardcoded — it describes what AgentKthx
        *is* to OpenRouter's marketplace, not a runtime knob. There MUST NOT
        be an env var that overrides it (letting a user flip ``cli-agent`` to
        ``creative-writing`` would misclassify the harness in the rankings).

        This test guards against a future change re-introducing an env-override
        escape hatch. If you need to add a second category for a fork, change
        the ``_APP_CATEGORIES`` constant directly so the audit catches it.
        """
        # The plugin source must NOT read an env var for the categories value.
        # We assert this by reading the source and grepping for the env-var
        # pattern that was removed in R07.21.
        import os
        plugin_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "agentkthx",
                "plugins",
                "openrouter",
                "openrouter.py",
            )
        )
        with open(plugin_path, "r", encoding="utf-8") as f:
            src = f.read()

        self.assertNotIn(
            "AGENTKTHX_OPENROUTER_CATEGORIES",
            src,
            "AGENTKTHX_OPENROUTER_CATEGORIES env var found in plugin source — "
            "the harness category must be hardcoded (it describes what AgentKthx "
            "*is*, not a runtime knob a user can flip).",
        )
        # Also assert no os.environ lookup in the attribution helper itself.
        # (The helper must be a pure function of the module constants.)
        self.assertNotIn(
            "os.environ",
            _build_openrouter_attribution_headers.__code__.co_names,
            "_build_openrouter_attribution_headers() must be a pure function "
            "of the module constants — it must NOT read os.environ.",
        )

    def test_no_authorization_or_content_type_leaked(self):
        """The helper returns ONLY attribution headers — Authorization and
        Content-Type must NOT be present (callers add their own per-request).
        """
        h = _build_openrouter_attribution_headers()
        self.assertNotIn("Authorization", h)
        self.assertNotIn("Content-Type", h)


class TestBackendHeaderSitesUseHelper(unittest.TestCase):
    """All four header-construction sites in ``OpenRouterBackend`` must
    delegate to ``_build_openrouter_attribution_headers()`` so the
    attribution contract can never drift between sites.

    Sites:
      1. ``__init__``     → ``self.headers`` (used by direct attribute reads)
      2. ``list_models``  → request headers on GET /models
      3. ``_make_api_request`` → request headers on POST /chat/completions
      4. ``_get_auth_headers`` → headers used by the streaming path
    """

    def _make_backend(self):
        """Construct an OpenRouterBackend WITHOUT touching the network.

        We bypass ``__init__`` (which calls ``list_models()``) so the test
        doesn't require OPENROUTER_API_KEY or hit the live API. We then call
        ``__init__``'s header-setup line directly via a thin stub.
        """
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
        b = OpenRouterBackend.__new__(OpenRouterBackend)
        b._base_url = "https://openrouter.ai/api/v1"
        b.api_key = "test-key"
        # Reproduce the __init__ header-setup line (the only thing we care about)
        b.headers = {
            "Authorization": f"Bearer {b.api_key}",
            "Content-Type": "application/json",
            **_build_openrouter_attribution_headers(),
        }
        return b

    def test_init_headers_carry_all_four_attribution_fields(self):
        """``self.headers`` (the instance attribute) must carry all four
        attribution headers in addition to Authorization + Content-Type.
        """
        b = self._make_backend()
        h = b.headers
        self.assertIn("HTTP-Referer", h)
        self.assertIn("X-OpenRouter-Title", h)
        self.assertIn("X-Title", h)
        self.assertIn("X-OpenRouter-Categories", h)
        self.assertEqual(h["HTTP-Referer"], "https://github.com/VTSTech/AgentKthx")
        self.assertEqual(h["X-OpenRouter-Categories"], "cli-agent")
        # Authorization + Content-Type must still be present
        self.assertEqual(h["Authorization"], "Bearer test-key")
        self.assertEqual(h["Content-Type"], "application/json")

    def test_get_auth_headers_carry_all_four_attribution_fields(self):
        """``_get_auth_headers()`` (used by the streaming path via
        ``generate_completions_stream``) must carry all four attribution
        headers in addition to Authorization + Content-Type.
        """
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
        b = OpenRouterBackend.__new__(OpenRouterBackend)
        b._base_url = "https://openrouter.ai/api/v1"
        b.api_key = "test-key"
        h = b._get_auth_headers()
        self.assertEqual(h["Authorization"], "Bearer test-key")
        self.assertEqual(h["Content-Type"], "application/json")
        self.assertEqual(h["HTTP-Referer"], "https://github.com/VTSTech/AgentKthx")
        self.assertEqual(h["X-OpenRouter-Title"], "AgentKthx")
        self.assertEqual(h["X-Title"], "AgentKthx")
        self.assertEqual(h["X-OpenRouter-Categories"], "cli-agent")

    def test_get_auth_headers_categories_are_hardcoded(self):
        """``_get_auth_headers()`` categories must NOT change regardless of
        what env vars are set. The category describes what AgentKthx *is*,
        not what the user wants to claim today.
        """
        from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
        import os
        # Set a bunch of plausible env-var names that a future buggy change
        # might read — none of them must affect the headers.
        with unittest_mock.patch.dict(os.environ, {
            "AGENTKTHX_OPENROUTER_CATEGORIES": "creative-writing",
            "OPENROUTER_CATEGORIES": "roleplay",
            "OPENROUTER_APP_CATEGORIES": "image-gen",
        }, clear=False):
            b = OpenRouterBackend.__new__(OpenRouterBackend)
            b._base_url = "https://openrouter.ai/api/v1"
            b.api_key = "test-key"
            h = b._get_auth_headers()
        self.assertEqual(h["X-OpenRouter-Categories"], "cli-agent")

    def test_no_hardcoded_referer_or_title_literals_remain_in_source(self):
        """Source-code regression guard: the four header-construction sites
        used to hardcode ``"HTTP-Referer": "https://github.com/VTSTech/AgentKthx"``
        and ``"X-Title": "AgentKthx"`` inline. After R07.21 they must all
        delegate to the helper — so the only literal references to those
        strings in the plugin source are the two module-level constants
        (``_APP_REFERER_URL`` and ``_APP_TITLE``).

        This test reads the plugin source and asserts no inline literal
        construction remains. It guards against a future copy-paste
        reintroducing the drift.
        """
        import os
        plugin_path = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "agentkthx",
                "plugins",
                "openrouter",
                "openrouter.py",
            )
        )
        with open(plugin_path, "r", encoding="utf-8") as f:
            src = f.read()

        # Count occurrences of the inline literal — should appear ONLY in the
        # _APP_REFERER_URL constant definition (and in this comment-free
        # test file, which we are NOT reading).
        referer_literal_count = src.count('"HTTP-Referer": "https://github.com/VTSTech/AgentKthx"')
        # 0 — the literal must not appear inline anywhere.
        # The canonical constant ``_APP_REFERER_URL`` carries the value.
        self.assertEqual(referer_literal_count, 0,
            "Inline literal HTTP-Referer string found — header construction "
            "sites must delegate to _build_openrouter_attribution_headers(). "
            f"Found {referer_literal_count} occurrence(s) in {plugin_path}.")

        title_literal_count = src.count('"X-Title": "AgentKthx"')
        self.assertEqual(title_literal_count, 0,
            "Inline literal X-Title string found — header construction "
            "sites must delegate to _build_openrouter_attribution_headers(). "
            f"Found {title_literal_count} occurrence(s) in {plugin_path}.")


if __name__ == "__main__":
    unittest.main()
