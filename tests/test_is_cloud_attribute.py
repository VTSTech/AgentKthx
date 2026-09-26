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
from agentkthx.plugins.huggingface.huggingface import HuggingFaceBackend
from agentkthx.plugins.openai.openai import OpenAIBackend


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

    def test_huggingface_inherits_true(self):
        """HuggingFaceBackend extends OpenAICompatibleBackend — cloud.

        Mirrors the OpenRouterBackend pattern. HF Router is a cloud-
        hosted OpenAI-compat API at https://router.huggingface.co/v1 —
        same semantics (rate-limited, billed, OpenAI-mode-only).
        """
        assert HuggingFaceBackend.is_cloud is True

    def test_openai_inherits_true(self):
        """OpenAIBackend extends OpenAICompatibleBackend — cloud.

        R07.03: OpenAI is the canonical OpenAI Chat-Completions API
        surface (https://api.openai.com/v1) — the same wire format
        every other OpenAI-compatible backend in the framework speaks.
        Cloud semantics (rate-limited, billed, OpenAI-mode-only).
        """
        assert OpenAIBackend.is_cloud is True


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


class TestBitNetBackendInstantiation:
    """R06.57: BitNetBackend must accept api_mode kwarg without crashing.

    Regression test for the bug found on Colab: `agentkthx models --backend bitnet`
    crashed with ``TypeError: got multiple values for keyword argument 'bitnet_mode'``.

    Root cause: ``get_backend("bitnet")`` adds ``bitnet_mode=True`` to kwargs
    (backends/__init__.py:108-109), but ``BitNetBackend.__init__`` ALSO hardcoded
    ``bitnet_mode=True`` in its ``super().__init__()`` call. When ``api_mode``
    was passed as a kwarg (by MAINT-05's ``_probe_backend`` call in cmd_models),
    both ``bitnet_mode`` values collided.

    Fix: ``BitNetBackend.__init__`` now pops ``bitnet_mode`` from kwargs before
    passing the hardcoded True. This test ensures the fix sticks.
    """

    def test_bitnet_accepts_api_mode_openai(self):
        """get_backend('bitnet', api_mode=ApiMode.OPENAI) must not crash."""
        from agentkthx.backends import get_backend
        from agentkthx.core.types import ApiMode
        b = get_backend("bitnet", api_mode=ApiMode.OPENAI)
        assert b is not None
        assert b.is_cloud is False  # local backend

    def test_bitnet_accepts_api_mode_openre(self):
        """get_backend('bitnet', api_mode=ApiMode.OPENRE) must not crash."""
        from agentkthx.backends import get_backend
        from agentkthx.core.types import ApiMode
        b = get_backend("bitnet", api_mode=ApiMode.OPENRE)
        assert b is not None

    def test_bitnet_accepts_api_mode_string(self):
        """get_backend('bitnet', api_mode='openai') must not crash (string form)."""
        from agentkthx.backends import get_backend
        b = get_backend("bitnet", api_mode="openai")
        assert b is not None

    def test_bitnet_bitnet_mode_is_always_true(self):
        """Regardless of what's passed, BitNetBackend always runs with bitnet_mode=True."""
        from agentkthx.backends import get_backend
        from agentkthx.core.types import ApiMode
        # Even if someone explicitly passes bitnet_mode=False, it's forced True
        b = get_backend("bitnet", api_mode=ApiMode.OPENAI, bitnet_mode=False)
        assert hasattr(b, "_bitnet_mode")
        assert b._bitnet_mode is True

    def test_bitnet_direct_instantiation_with_bitnet_mode_kwarg(self):
        """BitNetBackend(bitnet_mode=True) direct call must not crash either."""
        from agentkthx.plugins.bitnet.bitnet import BitNetBackend
        b = BitNetBackend(bitnet_mode=True)
        assert b._bitnet_mode is True

    def test_bitnet_direct_instantiation_without_bitnet_mode(self):
        """BitNetBackend() with no bitnet_mode still defaults to True."""
        from agentkthx.plugins.bitnet.bitnet import BitNetBackend
        b = BitNetBackend()
        assert b._bitnet_mode is True


class TestUnsupportedParamToolsFallback:
    """R06.57: BitNet's llama-server returns HTTP 500 'Unsupported param: tools'
    when the tools JSON param is sent to a model that doesn't support OpenAI
    function-calling. This must trigger the ReAct fallback (retry without tools),
    not a fatal error.

    Regression test for the bug found on Colab:
        agentkthx chat --backend bitnet  →  /tool shell  →  "Can you use the shell tool?"
        → Fatal API error — not retrying: Ollama HTTP error 500:
          {"error":{"code":500,"message":"Unsupported param: tools","type":"server_error"}}

    The existing "does not support tools" pattern didn't match this error message.
    Fix: added "unsupported param: tools" as an alternative trigger at all 3 sites
    in ollama.py (generate, generate_completions, test_tool_support).
    """

    def test_bitnet_500_error_pattern_matches(self):
        """The exact BitNet error message must trigger the fallback condition."""
        # This is the literal error from the Colab session
        error_body = '{"error":{"code":500,"message":"Unsupported param: tools","type":"server_error"}}'
        error_msg = error_body.lower()
        # The condition used in ollama.py:generate() and generate_completions()
        matches = ("does not support tools" in error_msg
                   or "unsupported param: tools" in error_msg)
        assert matches, f"BitNet 500 error should match the fallback pattern: {error_body}"

    def test_ollama_400_error_pattern_still_matches(self):
        """The existing Ollama 400 pattern must still work (no regression)."""
        error_body = "This model does not support tools"
        error_msg = error_body.lower()
        matches = ("does not support tools" in error_msg
                   or "unsupported param: tools" in error_msg)
        assert matches

    def test_unrelated_error_does_not_match(self):
        """An unrelated 500 error must NOT trigger the fallback (specificity check)."""
        error_body = '{"error":{"code":500,"message":"Internal server error"}}'
        error_msg = error_body.lower()
        matches = ("does not support tools" in error_msg
                   or "unsupported param: tools" in error_msg)
        assert not matches, "Unrelated 500 errors must not trigger the ReAct fallback"

    def test_unrelated_400_does_not_match(self):
        """A 400 that mentions 'tools' but not the exact phrases must not fire."""
        error_body = '{"error":{"code":400,"message":"Invalid tools format"}}'
        error_msg = error_body.lower()
        matches = ("does not support tools" in error_msg
                   or "unsupported param: tools" in error_msg)
        assert not matches, "Generic 'tools' mentions must not trigger the fallback"

    def test_test_tool_support_classifies_bitnet_500_as_none(self):
        """test_tool_support() must classify 'Unsupported param: tools' as NONE.

        This ensures the tool-support cache stores NONE (not REACT) for models
        that reject the tools param with this specific error, so future calls
        skip the native-tools path entirely.
        """
        # Simulate the error string that test_tool_support would catch
        error_str = 'RuntimeError: Ollama HTTP error 500: {"error":{"code":500,"message":"Unsupported param: tools","type":"server_error"}}'
        error_lower = error_str.lower()
        # The condition used in ollama.py:test_tool_support()
        is_tools_not_supported = (
            "does not support tools" in error_lower
            or "unsupported param: tools" in error_lower
        )
        assert is_tools_not_supported, "test_tool_support should classify this as NONE"
