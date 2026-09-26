"""
R07.05 regression: ZAI models show as ``paid`` in ``/models`` command
output, including the genuinely-free glm-4.5-flash / glm-4.7-flash.

Bug: the ``/models`` and ``/models free`` chat slash-commands rely on
``details.free_tier`` to mark models as free. ``ZaiBackend.list_models()``
never set ``free_tier`` at all — every model defaulted to
``free_tier=False`` (paid). So glm-4.5-flash and glm-4.7-flash showed as
``paid``, and ``/models free`` returned no matches. Same bug class
OpenRouter had in R07.05 (see ``tests/test_openrouter_free_models.py``).

Fix: ``list_models()`` and ``get_model_info()`` derive ``free_tier`` from
the catalog pricing via ``_is_free_model()`` (zero input + zero output).

Catalog correction (same release): glm-5.3-flash was erroneously priced
0.0/0.0 in the static catalog. Per ZAI's official per-1M-token pricing
table it costs $0.15 input / $0.50 output — it is NOT free despite the
name. The ONLY free ZAI models are glm-4.5-flash and glm-4.7-flash.
These tests pin both the free_tier wiring AND the catalog pricing so
catalog drift cannot silently re-introduce either bug.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.plugins.zai.zai import ZaiBackend, ZAI_MODELS


def _make_zai_backend():
    """Construct a ZaiBackend without network or __init__ side effects."""
    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    b._api_key = "test-key"
    return b


def _fake_urlopen_response(payload: dict):
    """Build a mock for urllib.request.urlopen returning ``payload`` JSON.

    ``list_models()`` uses ``with urllib.request.urlopen(req) as response:``
    so the mock needs context-manager support (MagicMock provides it).
    """
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(payload).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


# ---------------------------------------------------------------------------
# Catalog pricing ground truth (ZAI official per-1M-token table)
# ---------------------------------------------------------------------------

class TestCatalogPricing(unittest.TestCase):
    """Catalog pricing must match ZAI's official per-1M-token table."""

    FREE_MODELS = {"glm-4.5-flash", "glm-4.7-flash"}

    def test_free_models_are_exactly_two(self):
        """The ONLY zero-priced catalog models are glm-4.5-flash and
        glm-4.7-flash. If this fails, either pricing drifted or a model
        was added with placeholder 0.0 pricing."""
        zero_priced = {
            name for name, meta in ZAI_MODELS.items()
            if meta.get("pricing", {}).get("input") == 0.0
            and meta.get("pricing", {}).get("output") == 0.0
        }
        self.assertEqual(zero_priced, self.FREE_MODELS)

    def test_glm_5_3_flash_is_not_free(self):
        """glm-5.3-flash costs $0.15 input / $0.50 output per 1M — it is
        NOT free despite the name. Regression for the 0.0/0.0 catalog
        entry that made it appear free."""
        pricing = ZAI_MODELS["glm-5.3-flash"]["pricing"]
        self.assertEqual(pricing["input"], 0.15)
        self.assertEqual(pricing["output"], 0.5)

    def test_pricing_table_matches_official_values(self):
        """Pin every corrected catalog price (USD per 1M tokens)."""
        expected = {
            "glm-5.1": (1.4, 4.4),
            "glm-5.2": (1.4, 4.4),
            "glm-5": (1.0, 3.2),
            "glm-5.3": (1.4, 4.4),
            "glm-5.3-flash": (0.15, 0.5),
            "glm-5.3-flashx": (0.37, 1.25),
            "glm-4.7": (0.6, 2.2),
            "glm-4.7-flash": (0.0, 0.0),
            "glm-4.7-flashx": (0.07, 0.4),
            "glm-4.6": (0.6, 2.2),
            "glm-4.5": (0.6, 2.2),
            "glm-4.5-flash": (0.0, 0.0),
            "glm-4.5-air": (0.2, 1.1),
            "glm-4.5-x": (2.2, 8.9),
            "glm-4.5-airx": (1.1, 4.5),
            "glm-4-32b-0414-128k": (0.1, 0.1),
        }
        for name, (inp, out) in expected.items():
            self.assertIn(name, ZAI_MODELS, f"{name} missing from catalog")
            pricing = ZAI_MODELS[name]["pricing"]
            self.assertEqual(pricing["input"], inp, f"{name} input price")
            self.assertEqual(pricing["output"], out, f"{name} output price")


# ---------------------------------------------------------------------------
# _is_free_model classification
# ---------------------------------------------------------------------------

class TestIsFreeModel(unittest.TestCase):
    """_is_free_model must classify per catalog pricing."""

    def test_free_models_true(self):
        b = _make_zai_backend()
        self.assertTrue(b._is_free_model("glm-4.5-flash"))
        self.assertTrue(b._is_free_model("glm-4.7-flash"))

    def test_paid_models_false(self):
        b = _make_zai_backend()
        for name in ("glm-5.1", "glm-5.3-flash", "glm-5.3-flashx",
                     "glm-4.7-flashx", "glm-4.6", "glm-4.5-air"):
            self.assertFalse(b._is_free_model(name), f"{name} must be paid")

    def test_unknown_model_false(self):
        """Unknown models default to paid (safe assumption)."""
        b = _make_zai_backend()
        self.assertFalse(b._is_free_model("glm-9.9-ultra"))

    def test_provider_prefix_stripped(self):
        b = _make_zai_backend()
        self.assertTrue(b._is_free_model("zai/glm-4.5-flash"))
        self.assertFalse(b._is_free_model("zai/glm-5.3-flash"))


# ---------------------------------------------------------------------------
# list_models() free_tier wiring
# ---------------------------------------------------------------------------

class TestListModelsFreeTier(unittest.TestCase):
    """list_models() must set details.free_tier on every entry."""

    def test_api_branch_sets_free_tier(self):
        """API-discovered models get free_tier from catalog pricing."""
        b = _make_zai_backend()
        api_payload = {
            "data": [
                {"id": "glm-5.1"},            # paid
                {"id": "glm-4.7-flash"},      # free
                {"id": "zai/glm-4.5-flash"},  # free, with provider prefix
            ],
        }
        with patch("urllib.request.urlopen", return_value=_fake_urlopen_response(api_payload)):
            models = b.list_models()

        by_name = {m["name"]: m for m in models}
        self.assertTrue(by_name["glm-4.7-flash"]["details"]["free_tier"])
        self.assertTrue(by_name["glm-4.5-flash"]["details"]["free_tier"])
        self.assertFalse(by_name["glm-5.1"]["details"]["free_tier"])

    def test_catalog_only_branch_sets_free_tier(self):
        """When discovery fails, catalog-only entries still carry
        free_tier (exactly two True)."""
        b = _make_zai_backend()
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("network down")):
            models = b.list_models()

        self.assertGreater(len(models), 0)
        free = {m["name"] for m in models if m["details"].get("free_tier")}
        self.assertEqual(free, {"glm-4.5-flash", "glm-4.7-flash"})
        # Every entry must have the flag at all (not just defaulting)
        for m in models:
            self.assertIn("free_tier", m["details"], f"{m['name']} missing free_tier")

    def test_models_free_filter_returns_two(self):
        """The /models free filter (details.free_tier == True) returns
        exactly glm-4.5-flash and glm-4.7-flash."""
        b = _make_zai_backend()
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("network down")):
            models = b.list_models()

        free_models = [m for m in models if m.get("details", {}).get("free_tier", False)]
        self.assertEqual(
            sorted(m["name"] for m in free_models),
            ["glm-4.5-flash", "glm-4.7-flash"],
        )


# ---------------------------------------------------------------------------
# get_model_info() free_tier wiring
# ---------------------------------------------------------------------------

class TestGetModelInfoFreeTier(unittest.TestCase):
    """get_model_info() must expose free_tier for catalog hits."""

    def test_free_model_info(self):
        b = _make_zai_backend()
        info = b.get_model_info("glm-4.5-flash")
        self.assertIsNotNone(info)
        self.assertTrue(info["details"]["free_tier"])

    def test_paid_flash_model_info(self):
        """glm-5.3-flash: in catalog, but paid."""
        b = _make_zai_backend()
        info = b.get_model_info("glm-5.3-flash")
        self.assertIsNotNone(info)
        self.assertFalse(info["details"]["free_tier"])

    def test_unknown_model_info_defaults_paid(self):
        b = _make_zai_backend()
        info = b.get_model_info("totally-made-up-model")
        self.assertIsNotNone(info)
        self.assertFalse(info["details"]["free_tier"])


if __name__ == "__main__":
    unittest.main()
