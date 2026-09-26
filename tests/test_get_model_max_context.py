"""
R07.05 — get_model_max_context regression tests.

Verifies that the ``agentkthx models --backend <cloud>`` command works
for all cloud backends. The bug: ``OpenAICompatibleBackend.get_model_runtime_context``
delegates to ``self.get_model_max_context(model)``, but that method was
only defined on ``OllamaBackend``. Cloud backends (ZAI, OpenRouter,
OpenAI, HuggingFace, OrcaRouter) crashed with ``AttributeError`` when
the ``models`` CLI command was invoked.

The fix: ``CloudBackend`` (the MAINT-02 base class) now provides a
catalog-based ``get_model_max_context`` + ``get_model_runtime_context``
implementation. Backends that inherit from ``CloudBackend`` (ZAI,
OrcaRouter) get the fix automatically. Backends that still inherit
from ``OpenAICompatibleBackend`` directly (OpenRouter, Gemini, OpenAI,
HuggingFace) already had their own overrides — these tests verify
they continue to work.

Test coverage:
  - CloudBackend.get_model_max_context returns catalog value when present
  - CloudBackend.get_model_max_context returns 128000 fallback when absent
  - CloudBackend.get_model_max_context strips provider prefix
  - CloudBackend.get_model_runtime_context delegates to get_model_max_context
  - CloudBackend.get_model_max_context accepts (and ignores) family arg
  - ZaiBackend inherits the fix (regression for the R07.05 MAINT-02 migration)
  - OrcaRouterBackend inherits the fix (regression for the new plugin)
  - All 5 cloud backends respond to get_model_max_context without raising
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# CloudBackend.get_model_max_context (the new method)
# ---------------------------------------------------------------------------

class TestCloudBackendGetModelMaxContext:
    """Verify the new CloudBackend.get_model_max_context implementation."""

    def test_returns_catalog_value_when_present(self, monkeypatch):
        """When the model is in the MODELS catalog, return its context_length."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        # ZAI's catalog has explicit context_lengths
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend, ZAI_MODELS
        b = ZaiBackend()
        # glm-5.1 has context_length=204800 in the catalog
        assert ZAI_MODELS["glm-5.1"]["context_length"] == 204800
        assert b.get_model_max_context("glm-5.1") == 204800

    def test_returns_128k_fallback_when_not_in_catalog(self, monkeypatch):
        """When the model is NOT in the catalog, return 128000 (safe default)."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()
        # An unknown model should return 128000
        assert b.get_model_max_context("nonexistent/model-xyz") == 128000

    def test_strips_provider_prefix(self, monkeypatch):
        """Provider prefix (e.g. 'zai/glm-5.1') is stripped before catalog lookup."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        # 'zai/glm-5.1' should resolve to 'glm-5.1' in the catalog
        assert b.get_model_max_context("zai/glm-5.1") == 204800

    def test_accepts_family_kwarg_and_ignores_it(self, monkeypatch):
        """The family kwarg is accepted (for OllamaBackend compat) but
        ignored — the catalog is authoritative per-model, not per-family."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        # family="anything" should not change the result
        assert b.get_model_max_context("glm-5.1", family="glm") == 204800
        assert b.get_model_max_context("glm-5.1", family="unknown") == 204800
        assert b.get_model_max_context("glm-5.1", family=None) == 204800

    def test_returns_int(self, monkeypatch):
        """The return value is always an int (not None, not str)."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()
        result = b.get_model_max_context("any-model")
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# CloudBackend.get_model_runtime_context
# ---------------------------------------------------------------------------

class TestCloudBackendGetModelRuntimeContext:
    """Verify the new CloudBackend.get_model_runtime_context implementation."""

    def test_delegates_to_get_model_max_context(self, monkeypatch):
        """For cloud backends, runtime context == max context (no separate
        runtime context like Ollama's Modelfile num_ctx)."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        # Both should return the same value for the same model
        assert b.get_model_runtime_context("glm-5.1") == b.get_model_max_context("glm-5.1")

    def test_returns_int(self, monkeypatch):
        """The return value is always an int."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test1234567890")
        from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
        b = OrcaRouterBackend()
        result = b.get_model_runtime_context("any-model")
        assert isinstance(result, int)
        assert result > 0


# ---------------------------------------------------------------------------
# Regression: all 5 cloud backends respond without AttributeError
# ---------------------------------------------------------------------------

class TestAllCloudBackendsHaveGetModelMaxContext:
    """Verify all 5 cloud backends respond to get_model_max_context.

    Pre-fix: ZAI and OrcaRouter crashed with AttributeError because they
    inherited from CloudBackend (which didn't define the method) instead
    of overriding it like OpenRouter/Gemini/OpenAI/HuggingFace do.
    """

    @pytest.mark.parametrize("backend_name,env_var,env_value", [
        ("zai", "ZAI_API_KEY", "test-zai-key-1234567890"),
        ("orcarouter", "ORCAROUTER_API_KEY", "sk-orca-test1234567890"),
        ("openrouter", "OPENROUTER_API_KEY", "sk-or-test1234567890"),
        ("openai", "OPENAI_API_KEY", "sk-test1234567890"),
        ("huggingface", "HF_TOKEN", "hf_test1234567890"),
    ])
    def test_backend_responds_to_get_model_max_context(
        self, monkeypatch, backend_name, env_var, env_value
    ):
        """Each cloud backend's get_model_max_context returns an int > 0
        without raising AttributeError."""
        monkeypatch.setenv(env_var, env_value)
        from agentkthx import get_backend
        b = get_backend(backend_name)
        # Should NOT raise AttributeError — should return a positive int
        result = b.get_model_max_context("test-model")
        assert isinstance(result, int), f"{backend_name}: expected int, got {type(result)}"
        assert result > 0, f"{backend_name}: expected positive int, got {result}"

    @pytest.mark.parametrize("backend_name,env_var,env_value", [
        ("zai", "ZAI_API_KEY", "test-zai-key-1234567890"),
        ("orcarouter", "ORCAROUTER_API_KEY", "sk-orca-test1234567890"),
        ("openrouter", "OPENROUTER_API_KEY", "sk-or-test1234567890"),
        ("openai", "OPENAI_API_KEY", "sk-test1234567890"),
        ("huggingface", "HF_TOKEN", "hf_test1234567890"),
    ])
    def test_backend_responds_to_get_model_runtime_context(
        self, monkeypatch, backend_name, env_var, env_value
    ):
        """Each cloud backend's get_model_runtime_context returns an int > 0
        without raising AttributeError (the original crash site)."""
        monkeypatch.setenv(env_var, env_value)
        from agentkthx import get_backend
        b = get_backend(backend_name)
        # Should NOT raise AttributeError — should return a positive int
        result = b.get_model_runtime_context("test-model")
        assert isinstance(result, int), f"{backend_name}: expected int, got {type(result)}"
        assert result > 0, f"{backend_name}: expected positive int, got {result}"


# ---------------------------------------------------------------------------
# Regression: ZAI catalog values are correctly surfaced
# ---------------------------------------------------------------------------

class TestZaiCatalogContextLengths:
    """Verify ZAI's catalog values flow through get_model_max_context.

    ZAI was migrated to CloudBackend in MAINT-02 (R07.05). Before this
    fix, the ``agentkthx models --backend zai`` command crashed because
    CloudBackend didn't define get_model_max_context. Now the catalog
    values should flow through correctly.
    """

    def test_zai_glm_5_1_context(self, monkeypatch):
        """glm-5.1 has context_length=204800 (200K) per the ZAI catalog."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        assert b.get_model_max_context("glm-5.1") == 204800

    def test_zai_glm_5_3_context(self, monkeypatch):
        """glm-5.3 has context_length=1048576 (1M) per the ZAI catalog."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        assert b.get_model_max_context("glm-5.3") == 1048576

    def test_zai_unknown_model_returns_128k(self, monkeypatch):
        """An unknown ZAI model returns the 128K safe default."""
        monkeypatch.setenv("ZAI_API_KEY", "test-zai-key-1234567890")
        from agentkthx.plugins.zai.zai import ZaiBackend
        b = ZaiBackend()
        assert b.get_model_max_context("nonexistent-glm") == 128000
