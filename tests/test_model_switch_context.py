"""
R07.06 regression (ROB-14): in-chat ``/model <name>`` must re-derive the
per-model settings, not just swap ``agent.model``.

Found while smoke testing R07.06: switching models via ``/model`` in chat
mode kept the OLD model's ``num_ctx`` and ``num_predict``. Example:
start on glm-5.3 (1M context) → ``/model glm-4.7-flash`` (200K context)
left ``num_ctx=1048576`` — every request invites a context-length 400.
Conversely, switching TO a bigger model kept the smaller window.

The fix (``agentkthx.cli.agent_factory.apply_model_switch``) re-derives,
mirroring ``_build_agent`` startup precedence:

  num_ctx:     explicit --num-ctx  >  catalog context_length  >  config
  num_predict: explicit --num-predict  >  catalog max_tokens (capped)

Values the user pinned explicitly (CLI flags or ``/param``) survive the
switch. Also refreshed: ``model_config`` / ``model_family`` (pure
functions of the model name — previously stale: wrong stop tokens and
generation defaults after a switch), and the backend's
``_context_safe_max_tokens`` persisted by a previous model's
context-length-400 recovery (must not cap the new model).

All catalog lookups are static — no network in these tests.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.cli.agent_factory import apply_model_switch
from agentkthx.core.model_family_config import get_family_config
from agentkthx.plugins.zai.zai import ZAI_MODELS, ZaiBackend


def _make_zai_backend():
    """Construct a ZaiBackend without network or __init__ side effects."""
    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    b._api_key = "test-key"
    b._context_safe_max_tokens = None
    return b


class _LocalBackend:
    """Stand-in for a local (non-cloud) backend — no catalog defaults."""

    is_cloud = False


class _StubAgent:
    """Minimal agent surface touched by apply_model_switch."""

    def __init__(self, backend, model="glm-5.3", num_ctx=1048576, num_predict=32768):
        self.model = model
        self.num_ctx = num_ctx
        self._num_predict = num_predict
        self.model_config = "old-config"
        self.model_family = "old-family"
        self.backend = backend


def _glm_ctx(name: str) -> int:
    return ZAI_MODELS[name]["context_length"]


def _glm_predict(name: str) -> int:
    """num_predict the catalog yields: default_max_tokens capped to ctx//32."""
    meta = ZAI_MODELS[name]
    return min(meta["default_max_tokens"], meta["context_length"] // 32)


# ---------------------------------------------------------------------------
# Catalog-driven switches (ZAI backend, static catalog)
# ---------------------------------------------------------------------------


class TestZaiCatalogSwitch(unittest.TestCase):
    """Switching between ZAI catalog models must follow the catalog."""

    def setUp(self):
        self.agent = _StubAgent(
            _make_zai_backend(),
            model="glm-5.3",
            num_ctx=_glm_ctx("glm-5.3"),
            num_predict=_glm_predict("glm-5.3"),
        )

    def test_switch_shrinks_num_ctx_to_new_catalog(self):
        """glm-5.3 (1M ctx) → glm-4.7-flash (200K ctx): num_ctx follows."""
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(self.agent.model, "glm-4.7-flash")
        self.assertEqual(self.agent.num_ctx, _glm_ctx("glm-4.7-flash"))
        self.assertEqual(changes["num_ctx"], (_glm_ctx("glm-5.3"), _glm_ctx("glm-4.7-flash")))

    def test_switch_updates_num_predict_to_new_cap(self):
        """num_predict follows the new model's capped catalog max_tokens."""
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(self.agent._num_predict, _glm_predict("glm-4.7-flash"))
        self.assertEqual(
            changes["num_predict"], (_glm_predict("glm-5.3"), _glm_predict("glm-4.7-flash"))
        )

    def test_switch_to_bigger_model_grows_window(self):
        """The reverse direction: 200K → 1M must GROW num_ctx again."""
        small = _StubAgent(
            _make_zai_backend(),
            model="glm-4.7-flash",
            num_ctx=_glm_ctx("glm-4.7-flash"),
            num_predict=_glm_predict("glm-4.7-flash"),
        )
        apply_model_switch(small, "glm-5.3")
        self.assertEqual(small.num_ctx, _glm_ctx("glm-5.3"))
        self.assertEqual(small._num_predict, _glm_predict("glm-5.3"))

    def test_unknown_model_uses_safe_fallback(self):
        """Off-catalog model: 128K ctx / 8192 output capped to ctx//32."""
        changes = apply_model_switch(self.agent, "glm-unicorn")
        self.assertEqual(self.agent.num_ctx, 128000)
        self.assertEqual(self.agent._num_predict, 128000 // 32)
        self.assertEqual(changes["num_ctx"], (_glm_ctx("glm-5.3"), 128000))

    def test_provider_prefix_stripped_for_catalog(self):
        """``zai/glm-4.7-flash`` resolves the catalog entry (name kept as typed)."""
        apply_model_switch(self.agent, "zai/glm-4.7-flash")
        self.assertEqual(self.agent.model, "zai/glm-4.7-flash")
        self.assertEqual(self.agent.num_ctx, _glm_ctx("glm-4.7-flash"))

    def test_same_model_is_a_noop(self):
        """Switching to the model already active changes nothing."""
        changes = apply_model_switch(self.agent, "glm-5.3")
        self.assertEqual(changes, {})
        self.assertEqual(self.agent.num_ctx, _glm_ctx("glm-5.3"))

    def test_changes_report_only_actual_changes(self):
        """A key is absent when the value did not move (caller prints deltas)."""
        # Pre-set the target values so only the model itself changes.
        self.agent.num_ctx = _glm_ctx("glm-4.7-flash")
        self.agent._num_predict = _glm_predict("glm-4.7-flash")
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(set(changes), {"model"})
        self.assertEqual(changes["model"], ("glm-5.3", "glm-4.7-flash"))


class TestPinnedValues(unittest.TestCase):
    """Values the user set explicitly must survive model switches."""

    def setUp(self):
        self.agent = _StubAgent(
            _make_zai_backend(),
            model="glm-5.3",
            num_ctx=_glm_ctx("glm-5.3"),
            num_predict=_glm_predict("glm-5.3"),
        )

    def test_explicit_num_ctx_survives(self):
        """--num-ctx pin: catalog ctx for the new model is ignored."""
        self.agent._num_ctx_explicit = True
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(self.agent.num_ctx, _glm_ctx("glm-5.3"))
        self.assertNotIn("num_ctx", changes)
        # num_predict is still unpinned → follows the catalog.
        self.assertEqual(self.agent._num_predict, _glm_predict("glm-4.7-flash"))

    def test_explicit_num_predict_survives(self):
        """--num-predict pin: catalog max_tokens for the new model is ignored."""
        self.agent._num_predict_explicit = True
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(self.agent._num_predict, _glm_predict("glm-5.3"))
        self.assertNotIn("num_predict", changes)
        # num_ctx is still unpinned → follows the catalog.
        self.assertEqual(self.agent.num_ctx, _glm_ctx("glm-4.7-flash"))

    def test_both_pinned_only_model_moves(self):
        self.agent._num_ctx_explicit = True
        self.agent._num_predict_explicit = True
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertEqual(set(changes), {"model"})

    def test_missing_flags_default_to_unpinned(self):
        """Agents built before the pin flags existed re-derive both values."""
        self.assertFalse(hasattr(self.agent, "_num_ctx_explicit"))
        changes = apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertIn("num_ctx", changes)
        self.assertIn("num_predict", changes)


class TestLocalBackend(unittest.TestCase):
    """Local backends have no catalog — semantics must match fresh startup."""

    def test_num_ctx_unchanged_and_predict_falls_to_model_default(self):
        """{} catalog defaults: num_ctx stays config-derived; num_predict
        returns to None (model default) exactly like a fresh local start."""
        agent = _StubAgent(_LocalBackend(), model="qwen2.5:0.5b", num_ctx=8192, num_predict=512)
        changes = apply_model_switch(agent, "llama3.2:3b")
        self.assertEqual(agent.num_ctx, 8192)
        self.assertIsNone(agent._num_predict)
        self.assertEqual(changes["num_predict"], (512, None))
        self.assertNotIn("num_ctx", changes)

    def test_local_explicit_predict_survives(self):
        agent = _StubAgent(_LocalBackend(), model="qwen2.5:0.5b", num_ctx=8192, num_predict=512)
        agent._num_predict_explicit = True
        apply_model_switch(agent, "llama3.2:3b")
        self.assertEqual(agent._num_predict, 512)


# ---------------------------------------------------------------------------
# Derived per-model state: family config + 400-recovery persistence
# ---------------------------------------------------------------------------


class TestDerivedStateRefresh(unittest.TestCase):
    """model_config / model_family / _context_safe_max_tokens follow too."""

    def setUp(self):
        self.agent = _StubAgent(_make_zai_backend(), model="glm-5.3")

    def test_model_config_and_family_refreshed(self):
        """Family config is a pure function of the name — must re-derive.

        MAINT-07 (R07.15): qwen2.5 is now an explicit FAMILY_CONFIGS entry,
        so model_config.family reports the ACTUAL detected family
        ("qwen2.5") instead of the old 2-step indirection that silently
        resolved through the qwen2 config object. Template values (start/
        stop tokens, temperature) are identical — the qwen2.5 entry is a
        clone of qwen2 — so only the reported name changed."""
        apply_model_switch(self.agent, "qwen2.5:0.5b")
        self.assertEqual(self.agent.model_family, "qwen2.5")
        self.assertEqual(self.agent.model_config.family, "qwen2.5")
        # Template parity with qwen2 is preserved (the clone contract)
        self.assertEqual(
            self.agent.model_config.stop_tokens,
            get_family_config("qwen2").stop_tokens,
        )

    def test_unknown_family_resolves_to_unknown(self):
        apply_model_switch(self.agent, "glm-5.3")
        self.assertIsNone(self.agent.model_family)
        self.assertEqual(self.agent.model_config.family, "unknown")

    def test_model_config_actually_replaced(self):
        """The old config object must be gone, not merely kept."""
        old = self.agent.model_config
        apply_model_switch(self.agent, "qwen2.5:0.5b")
        self.assertIsNot(self.agent.model_config, old)

    def test_context_safe_max_tokens_cleared_on_switch(self):
        """A persisted 400-recovery value from the OLD model must not cap
        the NEW model's num_predict (cleared BEFORE catalog re-derivation)."""
        self.agent.backend._context_safe_max_tokens = 1024
        apply_model_switch(self.agent, "glm-4.7-flash")
        self.assertIsNone(self.agent.backend._context_safe_max_tokens)
        self.assertEqual(self.agent._num_predict, _glm_predict("glm-4.7-flash"))

    def test_backend_without_safe_value_attr_tolerated(self):
        """Backends lacking the attr (local stubs) must not explode."""
        agent = _StubAgent(_LocalBackend(), model="qwen2.5:0.5b")
        apply_model_switch(agent, "llama3.2:3b")  # must not raise
        self.assertEqual(agent.model, "llama3.2:3b")


if __name__ == "__main__":
    unittest.main()
