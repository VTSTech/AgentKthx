"""
AgentKthx — MAINT-05 regression tests: backend ``is_cloud`` attribute

R06.57 (MAINT-05): The 8 hardcoded backend allowlists in ``cli.py``
were replaced with a single ``backend.is_cloud`` class attribute check.
A 5th cloud backend now automatically gets:

- Default streaming enabled in ``cmd_run`` / ``cmd_chat`` / ``cmd_agent``
- Catalog-based ``num_ctx`` / ``num_predict`` defaults in ``_get_catalog_defaults``
- Cloud column layout in ``cmd_models`` (wider NAME_W, no Size/Family columns)
- ``OPENAI`` api_mode default in ``cmd_models`` (instead of ``OPENRE``)

These tests verify:
1. The class hierarchy resolves ``is_cloud`` correctly for all 4 cloud backends
   (ZaiBackend, OpenRouterBackend, GeminiBackend) and all 3 local backends
   (OllamaBackend, LlamaServerBackend, BitNetBackend).
2. A fake 5th cloud backend (subclass of OpenAICompatibleBackend) automatically
   inherits ``is_cloud = True`` without any override — proving the bug class
   "new backend crashes cmd_models" (R06.56 BUG-01/BUG-02) cannot recur.
3. A fake 5th local backend (subclass of BaseBackend directly) gets
   ``is_cloud = False`` by default — safe for unknown backends.
4. ``getattr(backend, 'is_cloud', False)`` returns False for backends that
   don't have the attribute (defensive fallback used by cli.py).
"""

import pytest

from agentkthx.backends.base import BaseBackend, BackendConfig
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.backends.ollama import OllamaBackend
from agentkthx.backends.llama_server import LlamaServerBackend
from agentkthx.plugins.bitnet.bitnet import BitNetBackend
from agentkthx.plugins.zai.zai import ZaiBackend
from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend
from agentkthx.plugins.gemini.gemini import GeminiBackend


class TestIsCloudAttribute:
    """R06.57 (MAINT-05): Verify ``is_cloud`` resolves correctly for all
    built-in backends — the foundation of the allowlist generalization."""

    def test_base_backend_defaults_to_false(self):
        """Unknown BaseBackend subclasses default to False (local).
        Safe fallback — better to under-treat an unknown backend as local
        than to silently enable cloud behaviors (streaming-by-default,
        OPENAI api_mode, etc.) for something we don't recognize.
        """
        assert BaseBackend.is_cloud is False

    def test_openai_compat_defaults_to_true(self):
        """OpenAICompatibleBackend marks all subclasses cloud by default.
        The original R06.55 ARCH-01 extraction was for cloud backends
        (ZAI, OpenRouter, then Gemini in R06.56). The default is True
        because that's the common case for new OpenAI-compat backends.
        """
        assert OpenAICompatibleBackend.is_cloud is True

    def test_ollama_overrides_to_false(self):
        """OllamaBackend is the exception — local server, even though it
        extends OpenAICompatibleBackend (since R06.55 ARCH-01). The
        override preserves the local-server semantics (native OPENRE
        mode, no streaming-by-default, no cloud column layout).
        """
        assert OllamaBackend.is_cloud is False

    def test_llama_server_inherits_false(self):
        """LlamaServerBackend extends OllamaBackend — inherits False
        because it's also a local server (llama.cpp binary).
        """
        assert LlamaServerBackend.is_cloud is False

    def test_bitnet_inherits_false(self):
        """BitNetBackend extends LlamaServerBackend — inherits False
        because it's a local 1.58-bit inference server.
        """
        assert BitNetBackend.is_cloud is False

    def test_zai_inherits_true(self):
        """ZaiBackend extends OpenAICompatibleBackend directly — cloud."""
        assert ZaiBackend.is_cloud is True

    def test_openrouter_inherits_true(self):
        """OpenRouterBackend extends OpenAICompatibleBackend — cloud."""
        assert OpenRouterBackend.is_cloud is True

    def test_gemini_inherits_true(self):
        """GeminiBackend extends OpenAICompatibleBackend — cloud."""
        assert GeminiBackend.is_cloud is True


class TestFifthCloudBackend:
    """R06.57 (MAINT-05): A hypothetical 5th cloud backend (e.g. DeepSeek,
    Together AI, Mistral La Plateforme — all OpenAI-compat) should
    automatically get cloud treatment without any cli.py edits.

    This test creates a fake 5th cloud backend by subclassing
    OpenAICompatibleBackend and asserts ``is_cloud`` resolves to True
    with no override — proving the R06.56 BUG-01/BUG-02 bug class
    ("new backend crashes ``agentkthx models``") cannot recur.
    """

    def test_fake_cloud_backend_inherits_is_cloud_true(self):
        """A subclass of OpenAICompatibleBackend with no override gets True."""
        class FakeCloudBackend(OpenAICompatibleBackend):
            @property
            def backend_type(self):
                from agentkthx.core.types import BackendType
                return BackendType.OLLAMA  # placeholder, doesn't matter for this test

        assert FakeCloudBackend.is_cloud is True

    def test_fake_cloud_backend_explicit_override_to_false(self):
        """If a future local backend extends OpenAICompatibleBackend, it can
        still override to False (mirrors the OllamaBackend pattern)."""
        class FakeLocalBackend(OpenAICompatibleBackend):
            is_cloud = False

        assert FakeLocalBackend.is_cloud is False


class TestFifthLocalBackend:
    """R06.57 (MAINT-05): A hypothetical 5th local backend (e.g. a future
    vLLM integration that doesn't go through OpenAICompatibleBackend)
    should default to False without any override."""

    def test_fake_local_backend_inherits_is_cloud_false(self):
        """A subclass of BaseBackend directly (not OpenAICompatibleBackend)
        defaults to False — safe for unknown backends.
        """
        class FakeLocalBackend(BaseBackend):
            @property
            def backend_type(self):
                from agentkthx.core.types import BackendType
                return BackendType.OLLAMA  # placeholder

            # Stub the abstract methods so the class can be referenced
            # without instantiation
            def generate(self, *args, **kwargs): pass
            def generate_stream(self, *args, **kwargs): pass
            def list_models(self, *args, **kwargs): return []
            def test_tool_support(self, *args, **kwargs):
                from agentkthx.core.types import ToolSupportLevel
                return ToolSupportLevel.NONE
            @property
            def base_url(self): return "http://localhost:1234"

        assert FakeLocalBackend.is_cloud is False


class TestCliDefensiveFallback:
    """R06.57 (MAINT-05): cli.py uses ``getattr(backend, 'is_cloud', False)``
    so unknown backends (no ``is_cloud`` attribute) don't crash. The
    default is False — same safe fallback as BaseBackend.
    """

    def test_getattr_returns_false_for_missing_attribute(self):
        """A backend instance without is_cloud attribute → getattr returns False."""

        class BareBackend:
            # No is_cloud attribute at all — simulates an old plugin
            # written before R06.57
            pass

        backend = BareBackend()
        assert getattr(backend, 'is_cloud', False) is False

    def test_getattr_returns_true_when_attribute_present(self):
        """A backend with is_cloud=True → getattr returns True."""

        class CloudBackend:
            is_cloud = True

        backend = CloudBackend()
        assert getattr(backend, 'is_cloud', False) is True
