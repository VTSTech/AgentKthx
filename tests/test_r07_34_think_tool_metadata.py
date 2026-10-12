"""
R07.34 — think/tool detection, second pass (owner cross-backend live-run
report).

Five fixes, one suite:

1. ``fmt_token_size`` — context-scale non-multiples round to decimal-K
   (TokenHarbor live windows 991808 / 983616 rendered as raw ints).
2. Thinking name heuristics — more current-generation families
   (Nemotron 3/3.5+, inclusionAI Ling, version-less gemini-*-latest
   aliases) and a NON-thinking exclusion list (tts / transcribe /
   whisper / embedding / robotics / safety classifiers) that yields NO
   and takes precedence over family matches — the first-pass gemini
   family regex flagged ``gemini-2.5-flash-preview-tts`` as ✓ yes.
3. Provider-metadata verdicts — ``details.supports_reasoning`` /
   ``details.supports_tools`` (hoisted at parse time from OpenRouter's
   ``supported_parameters``) beat every name heuristic in both
   directions. Raw ``model_data`` payloads are deliberately NOT trusted
   generically (a foreign payload shape carrying a parameter list
   without "tools" would downgrade every model on backends that merely
   store the raw dict).
4. OpenRouter non-chat slug matching — slug-WORD lookbehind replaces the
   raw substring test: ``thinkingmachines/inkling`` contains "kling",
   which mis-classified a chat model as non-chat (``tools ? untested``).
5. The "Models command works best with Ollama backend" hint no longer
   prints for cloud backends (they have had full catalog support since
   R07.20; the hint was noise on every tokenharbor/openrouter/zai run).

The conftest autouse fixture points AGENTKTHX_MODEL_CACHE at a per-test
file; the fixtures below isolate the tool_cache and model_cache modules
the same way the R07.19 detection suites do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.types import ThinkingSupport, ToolSupportLevel

VALID_KEY = "thk_live_test1234567890abcdef1234567890"


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


class _ConcreteCompat(OpenAICompatibleBackend):
    """Minimal concrete OpenAI-compatible backend (ABC needs impls)."""

    @property
    def backend_type(self):
        raise NotImplementedError

    @property
    def base_url(self):
        return "https://compat.stub/v1"

    def generate(self, *a, **k):
        raise NotImplementedError

    def generate_stream(self, *a, **k):
        raise NotImplementedError

    def list_models(self):
        return []

    def test_tool_support(self, model, family=None, force_test=False):
        return ToolSupportLevel.NATIVE

    def _get_model_defaults(self, model):
        return {"temperature": 0.7, "max_tokens": 4096}


class _ConcreteCloud(CloudBackend):
    """Minimal concrete cloud backend for the shared tool-verdict tests."""

    MODELS = {"test-model": {"context_length": 128000}}

    @property
    def backend_type(self):
        raise NotImplementedError

    @property
    def base_url(self):
        return "https://cloud.stub/v1"

    def _get_chat_completions_url(self):
        return "https://cloud.stub/v1/chat/completions"

    @property
    def _api_key_env_var(self):
        return "TEST_CLOUD_API_KEY"

    @property
    def _default_base_url(self):
        return "https://cloud.stub/v1"

    @property
    def _default_model(self):
        return "test-model"

    @property
    def _provider_label(self):
        return "TestCloud"

    def generate(self, *a, **k):
        raise NotImplementedError

    def generate_stream(self, *a, **k):
        raise NotImplementedError

    def _get_model_defaults(self, model):
        return {"temperature": 0.7, "max_tokens": 4096}


class _CloudCloudStub:
    """cmd_models stand-in: cloud backend, no catalog cache needed."""

    MODEL_CACHE_KEY = None
    is_cloud = True

    def __init__(self):
        self.base_url = "https://stub.cloud/v1"
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return [{"name": "stub-model", "size": 0, "details": {"family": "stub"}}]

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768

    def test_tool_support(self, name, family=None, force_test=False):
        return ToolSupportLevel.NATIVE

    def test_thinking_support(self, name, family=None, force_test=False):
        return ThinkingSupport.UNKNOWN


class _LocalStub:
    """cmd_models stand-in: static local backend (not Ollama)."""

    MODEL_CACHE_KEY = None
    is_cloud = False

    def __init__(self):
        self.base_url = "http://stub.local:1337"
        self.api_mode = None

    def is_running(self):
        return True

    def list_models(self):
        return [{"name": "stub-model", "size": 0, "details": {"family": "stub"}}]

    def get_model_runtime_context(self, name):
        return None

    def get_model_max_context(self, name, family=None):
        return 32768

    def test_tool_support(self, name, family=None, force_test=False):
        return ToolSupportLevel.NATIVE

    def test_thinking_support(self, name, family=None, force_test=False):
        return ThinkingSupport.UNKNOWN


@pytest.fixture
def isolated_tool_cache(monkeypatch, tmp_path):
    """Point the tool_support.json cache (thinking verdicts) at a temp file."""
    from agentkthx.core import tool_cache as _tc

    monkeypatch.setattr(_tc, "get_cache_file", lambda: tmp_path / "tool_support.json")
    return _tc


@pytest.fixture
def tokenharbor_env(monkeypatch):
    """Env + config module state for TokenHarbor construction."""
    from agentkthx import config as _config

    monkeypatch.setenv("TOKENHARBOR_API_KEY", VALID_KEY)
    monkeypatch.setenv("TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1")
    monkeypatch.setenv("TOKENHARBOR_DEFAULT_MODEL", "deepseek-v4.1-flash:free")
    monkeypatch.setenv("TOKENHARBOR_FREE_ONLY", "false")
    monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", VALID_KEY, raising=False)


@pytest.fixture
def th_backend(tokenharbor_env):
    """TokenHarborBackend with class-level catalog state reset afterwards."""
    from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

    b = TokenHarborBackend()
    yield b
    for attr, val in (("_model_cache", None), ("_cache_time", 0.0)):
        if hasattr(type(b), attr):
            try:
                setattr(type(b), attr, val)
            except Exception:
                pass


@pytest.fixture
def or_backend(monkeypatch):
    """OpenRouterBackend without HTTP side effects + class-state cleanup."""
    from agentkthx.plugins.openrouter.openrouter import OpenRouterBackend

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test1234567890")
    b = OpenRouterBackend()
    yield b
    for attr, val in (("_model_cache", None), ("_cache_time", 0.0)):
        if hasattr(type(b), attr):
            try:
                setattr(type(b), attr, val)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 1. fmt_token_size — odd-window rounding + earlier-pass regressions
# ---------------------------------------------------------------------------


class TestFmtTokenSizeRounding:
    @pytest.mark.parametrize(
        "n,expected",
        [
            # R07.34: live-API odd windows round to the nearest decimal-K
            (991808, "992K"),  # TokenHarbor qwen3.8-27b (was raw '991808')
            (983616, "984K"),  # TokenHarbor qwen3.8-flash (was raw '983616')
            (999999, "1000K"),  # rounding can cross the K boundary
            (150001, "150K"),
            # decimal forms unchanged
            (128000, "128K"),
            (200000, "200K"),
            (400000, "400K"),
            (1000000, "1M"),
            (1050000, "1050K"),
            # exact binary multiples still win (no rounding drift)
            (131072, "128K"),
            (983040, "960K"),
            (262144, "256K"),
            (1048576, "1M"),
            (2097152, "2M"),
            # R07.18 small-value behavior byte-identical
            (8192, "8K"),
            (4096, "4K"),
            (1024, "1K"),
            (512, "512"),
            (1500, "1500"),
            (999, "999"),
            (0, "0"),
        ],
    )
    def test_formats(self, n, expected):
        from agentkthx.cli.footer import fmt_token_size

        assert fmt_token_size(n) == expected

    def test_none_renders_placeholder(self):
        from agentkthx.cli.footer import fmt_token_size

        assert fmt_token_size(None) == "?"
        assert fmt_token_size("  991808 ") == "992K"  # string coercion kept


# ---------------------------------------------------------------------------
# 2. R07.34 name heuristics — new families, exclusions, preserved unknowns
# ---------------------------------------------------------------------------


class TestThinkingNameVerdictR0736:
    @pytest.mark.parametrize(
        "model",
        [
            # NVIDIA Nemotron 3/3.5 hybrid reasoning (owner report:
            # super/ultra/lightning showed ? unknown)
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "nvidia/nemotron-3-nano-omni-30b-a3b",
            # inclusionAI Ling hybrid thinking (HF + OpenRouter catalogs)
            "inclusionAI/Ling-3.0-flash-Fin",
            "inclusionai/ling-3.0-flash-sante:free",
            # version-less Google aliases (gemini catalog)
            "gemini-flash-lite-latest",
            "gemini-flash-latest",
            "gemini-pro-latest",
        ],
    )
    def test_new_families_yes(self, model):
        assert OpenAICompatibleBackend._name_matches_thinking(model) is True
        assert (
            OpenAICompatibleBackend._name_thinking_verdict(model)
            is ThinkingSupport.YES
        )

    @pytest.mark.parametrize(
        "model",
        [
            # TTS / transcribe: the first-pass gemini family regex false-YESed
            "gemini-2.5-flash-preview-tts",
            "gemini-3.8-flash-tts",
            "gemini-3.8-flash-lite-tts",
            "gemini-3.5-transcribe",
            "gemini-3.5-transcribe-live",
            # embeddings
            "gemini-embedding-001",
            "gemini-embedding-2-preview",
            # robotics (VLA, not chat reasoning)
            "gemini-robotics-er-2-preview",
            "gemini-robotics-er-2-streaming-preview",
            # safety classifiers / guard models
            "nvidia/nemotron-3.5-content-safety:free",  # beats nemotron-YES
            "meta-llama/llama-guard-4",
            # ASR
            "openai/whisper-large-v3",
        ],
    )
    def test_modality_exclusions_no(self, model):
        """Exclusions yield NO and override the family YES patterns."""
        assert (
            OpenAICompatibleBackend._name_thinking_verdict(model)
            is ThinkingSupport.NO
        )
        assert OpenAICompatibleBackend._name_matches_thinking(model) is False

    @pytest.mark.parametrize(
        "model",
        [
            # ungrounded families stay UNKNOWN (first-pass pins preserved)
            "apex",
            "muse-spark-1-3",
            "muse-spark-1.2",
            # inkling: vendor-bleed guard intact for THINKING (R07.19)
            "thinkingmachines/inkling",
            "thinkingmachines/inkling-small:free",
            # no published reasoning evidence
            "openrouter/free",
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it",
            "poolside/laguna-s-2.1:free",
            "liquid/lfm-2.5-2.6b:free",
            "apodex/apodex-1.1-mini:free",
            "cohere/north-mini-code:free",
            "dots-studio/dots-3-note-preview:free",
            "antigravity-preview-latest",
            "prism-ml/Ternary-Bonsai-27B-AWQ-4bit",
            # old generations still unmatched
            "gpt-4o",
            "gemini-2.0-flash-exp",
        ],
    )
    def test_unknown_stays_unknown(self, model):
        assert (
            OpenAICompatibleBackend._name_thinking_verdict(model)
            is ThinkingSupport.UNKNOWN
        )

    def test_exclusions_match_segment_not_org(self):
        """The org may carry an exclusion word without poisoning the model;
        the model segment carrying it still excludes."""
        # org "guardworks" + plain model name → not excluded, not marked
        assert (
            OpenAICompatibleBackend._name_thinking_verdict("guardworks/base-7b")
            is ThinkingSupport.UNKNOWN
        )
        # org "safety" + a marker-bearing model still YES (segment rules)
        assert (
            OpenAICompatibleBackend._name_thinking_verdict(
                "safety-labs/claude-sonnet-5"
            )
            is ThinkingSupport.YES
        )


# ---------------------------------------------------------------------------
# 3. Metadata-driven thinking verdicts (OpenAICompatibleBackend)
# ---------------------------------------------------------------------------


class TestMetadataThinking:
    def test_reasoning_true_yes_and_cached(self, isolated_tool_cache):
        """Provider says reasoning → YES, even with no name marker."""
        from agentkthx.core.tool_cache import get_cached_thinking_support

        b = _ConcreteCompat()
        type(b)._model_cache = [
            {
                "name": "cohere/north-mini-code:free",
                "details": {"context_length": 256000, "supports_reasoning": True},
            }
        ]
        try:
            assert b.test_thinking_support("cohere/north-mini-code:free") is (
                ThinkingSupport.YES
            )
            assert (
                get_cached_thinking_support("cohere/north-mini-code:free")
                is ThinkingSupport.YES
            )
        finally:
            type(b)._model_cache = None

    def test_reasoning_false_no_and_cached(self, isolated_tool_cache):
        """Provider says NOT reasoning → NO, even with a family marker."""
        from agentkthx.core.tool_cache import get_cached_thinking_support

        b = _ConcreteCompat()
        type(b)._model_cache = [
            {
                "name": "acme/claude-opus-5",
                "details": {"context_length": 200000, "supports_reasoning": False},
            }
        ]
        try:
            assert b.test_thinking_support("acme/claude-opus-5") is (
                ThinkingSupport.NO
            )
            assert (
                get_cached_thinking_support("acme/claude-opus-5")
                is ThinkingSupport.NO
            )
        finally:
            type(b)._model_cache = None

    def test_raw_payload_not_trusted_generically(self, isolated_tool_cache):
        """model_data.supported_parameters WITHOUT hoisted details flags is
        ignored — foreign payloads must not downgrade unrelated backends."""
        b = _ConcreteCompat()
        type(b)._model_cache = [
            {
                "name": "vendor/model-x",
                "details": {"context_length": 128000},
                "model_data": {"supported_parameters": ["temperature"]},
            }
        ]
        try:
            reasoning, tools = b._entry_capability_flags("vendor/model-x")
            assert reasoning is None and tools is None
        finally:
            type(b)._model_cache = None

    def test_segment_matching_finds_prefixed_entry(self, isolated_tool_cache):
        """Query without the vendor prefix still finds the catalog entry."""
        b = _ConcreteCompat()
        type(b)._model_cache = [
            {
                "name": "nvidia/nemotron-3-super",
                "details": {"supports_reasoning": True, "supports_tools": True},
            }
        ]
        try:
            reasoning, tools = b._entry_capability_flags("nemotron-3-super")
            assert reasoning is True and tools is True
        finally:
            type(b)._model_cache = None

    def test_no_catalog_returns_none_flags(self):
        """Backends without an in-process catalog degrade gracefully."""
        b = _ConcreteCompat()
        assert not hasattr(type(b), "_model_cache") or type(b)._model_cache is None
        assert b._entry_capability_flags("anything") == (None, None)

    def test_junk_flags_coerce_to_none(self):
        """Non-bool flag values are treated as absent, not falsy evidence."""
        b = _ConcreteCompat()
        type(b)._model_cache = [
            {
                "name": "junk/model",
                "details": {
                    "supports_reasoning": "yes-please",
                    "supports_tools": 1,
                },
            }
        ]
        try:
            assert b._entry_capability_flags("junk/model") == (None, None)
        finally:
            type(b)._model_cache = None

    def test_cached_yes_loses_to_new_exclusion(self, isolated_tool_cache):
        """Second-pass self-heal: a YES verdict cached by the pre-exclusion
        first-pass heuristic (gemini TTS family) is overridden by the new
        exclusion evidence — owner machines heal without a cache purge."""
        from agentkthx.core.tool_cache import (
            cache_thinking_support,
            get_cached_thinking_support,
        )

        cache_thinking_support("gemini-2.5-flash-preview-tts", ThinkingSupport.YES)
        # Pin the precondition: the stale YES is genuinely in the cache.
        # Without this, the NO verdict below could come from the exclusion
        # path alone (TTS matches an exclusion even uncached) and the test
        # would pass even if the cache write had silently failed.
        assert (
            get_cached_thinking_support("gemini-2.5-flash-preview-tts")
            is ThinkingSupport.YES
        )
        b = _ConcreteCompat()
        type(b)._model_cache = None
        try:
            assert b.test_thinking_support("gemini-2.5-flash-preview-tts") is (
                ThinkingSupport.NO
            )
        finally:
            type(b)._model_cache = None

    def test_cached_yes_kept_without_exclusion(self, isolated_tool_cache):
        """Cached YES for an ordinary reasoning family is untouched."""
        from agentkthx.core.tool_cache import cache_thinking_support

        cache_thinking_support("claude-opus-5.5", ThinkingSupport.YES)
        b = _ConcreteCompat()
        assert b.test_thinking_support("claude-opus-5.5") is ThinkingSupport.YES

    def test_heuristics_still_fire_without_metadata(
        self, isolated_tool_cache
    ):
        """No entries → plain name heuristics (YES for a marked family)."""
        b = _ConcreteCompat()
        type(b)._model_cache = None
        assert b.test_thinking_support("claude-opus-5.5") is ThinkingSupport.YES

    def test_real_cloud_backend_metadata_flow(self, th_backend, isolated_tool_cache):
        """End-to-end on a real CloudBackend subclass (TokenHarbor): a
        hoisted entry flips both columns without any live call."""
        type(th_backend)._model_cache = [
            {
                "name": "future/reasoner-pro",
                "details": {
                    "context_length": 400000,
                    "supports_reasoning": True,
                    "supports_tools": False,
                },
            }
        ]
        try:
            assert (
                th_backend.test_thinking_support("future/reasoner-pro")
                is ThinkingSupport.YES
            )
            assert (
                th_backend.test_tool_support("future/reasoner-pro")
                is ToolSupportLevel.REACT
            )
        finally:
            type(th_backend)._model_cache = None


# ---------------------------------------------------------------------------
# 4. CloudBackend.test_tool_support — metadata beats NATIVE default
# ---------------------------------------------------------------------------


class TestCloudToolMetadata:
    def _backend_with(self, entry):
        b = _ConcreteCloud(api_key="test-key-1234567890abcdef")
        type(b)._model_cache = [entry] if entry else None
        return b

    def test_supports_tools_false_react(self, isolated_tool_cache):
        b = self._backend_with(
            {
                "name": "prov/no-tools-model",
                "details": {"supports_tools": False},
            }
        )
        try:
            assert b.test_tool_support("prov/no-tools-model") is (
                ToolSupportLevel.REACT
            )
        finally:
            type(b)._model_cache = None

    def test_supports_tools_true_native(self, isolated_tool_cache):
        b = self._backend_with(
            {
                "name": "prov/tools-model",
                "details": {"supports_tools": True},
            }
        )
        try:
            assert b.test_tool_support("prov/tools-model") is (
                ToolSupportLevel.NATIVE
            )
        finally:
            type(b)._model_cache = None

    def test_no_entry_defaults_native(self, isolated_tool_cache):
        b = self._backend_with(None)
        assert b.test_tool_support("anything/else") is ToolSupportLevel.NATIVE

    def test_model_data_only_entry_stays_native(self, isolated_tool_cache):
        """Raw payload without hoisted flags must NOT downgrade (safety)."""
        b = self._backend_with(
            {
                "name": "prov/raw-only",
                "details": {"context_length": 128000},
                "model_data": {"supported_parameters": ["temperature"]},
            }
        )
        try:
            assert b.test_tool_support("prov/raw-only") is (
                ToolSupportLevel.NATIVE
            )
        finally:
            type(b)._model_cache = None


# ---------------------------------------------------------------------------
# 5. OpenRouter — parse-time hoisting + legacy-cache self-heal
# ---------------------------------------------------------------------------


class TestOpenRouterParseHoist:
    def test_supported_parameters_hoisted(self, or_backend):
        parsed = or_backend._parse_openrouter_model(
            {
                "id": "nvidia/nemotron-3-super-120b-a12b:free",
                "context_length": 256000,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": [
                    "temperature",
                    "tools",
                    "reasoning",
                    "structured_outputs",
                ],
            }
        )
        assert parsed["details"]["supports_reasoning"] is True
        assert parsed["details"]["supports_tools"] is True

    def test_supported_parameters_without_reasoning(self, or_backend):
        parsed = or_backend._parse_openrouter_model(
            {
                "id": "google/gemma-4-31b-it:free",
                "context_length": 256000,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["temperature", "top_k"],
            }
        )
        assert parsed["details"]["supports_reasoning"] is False
        assert parsed["details"]["supports_tools"] is False

    def test_include_reasoning_alias(self, or_backend):
        parsed = or_backend._parse_openrouter_model(
            {
                "id": "vendor/alias-model",
                "context_length": 128000,
                "pricing": {"prompt": "0", "completion": "0"},
                "supported_parameters": ["include_reasoning"],
            }
        )
        assert parsed["details"]["supports_reasoning"] is True

    def test_missing_or_junk_parameters_no_flags(self, or_backend):
        for junk in (None, "tools", {"tools": True}, []):
            parsed = or_backend._parse_openrouter_model(
                {
                    "id": "vendor/junk-model",
                    "context_length": 128000,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": junk,
                }
            )
            assert "supports_reasoning" not in parsed["details"], junk
            assert "supports_tools" not in parsed["details"], junk


class TestOpenRouterHoistLegacyCache:
    def _legacy_entry(self, params):
        return {
            "name": "vendor/legacy-model",
            "size": 0,
            "details": {
                "family": "vendor",
                "backend": "openrouter",
                "context_length": 256000,
            },
            "model_data": {"id": "vendor/legacy-model", "supported_parameters": params},
        }

    def test_hoist_upgrades_legacy_entry_in_place(self, or_backend):
        entry = self._legacy_entry(["tools", "reasoning"])
        result = or_backend._hoist_capability_flags([entry])
        assert result[0] is entry  # in place, same dict
        assert entry["details"]["supports_tools"] is True
        assert entry["details"]["supports_reasoning"] is True

    def test_hoist_skips_already_hoisted(self, or_backend):
        entry = self._legacy_entry(["tools"])
        entry["details"]["supports_tools"] = False  # pre-set
        entry["details"]["supports_reasoning"] = False
        or_backend._hoist_capability_flags([entry])
        assert entry["details"]["supports_tools"] is False  # untouched

    def test_hoist_ignores_entries_without_payload(self, or_backend):
        entry = {
            "name": "vendor/bare",
            "details": {"context_length": 128000},
        }
        or_backend._hoist_capability_flags([entry])
        assert "supports_tools" not in entry["details"]

    def test_hoist_ignores_junk_and_non_lists(self, or_backend):
        entry = self._legacy_entry("tools")  # string, not list
        or_backend._hoist_capability_flags([entry])
        assert "supports_tools" not in entry["details"]

    def test_l2_restore_path_self_heals(self, or_backend, monkeypatch):
        """list_models() on a fresh L2 hit upgrades pre-R07.34 entries —
        no --refresh and no live fetch required."""
        import agentkthx.model_cache as mc

        legacy = [
            self._legacy_entry(["tools", "reasoning", "temperature"]),
            {
                "name": "vendor/legacy-no-params",
                "size": 0,
                "details": {"context_length": 128000},
                "model_data": {"id": "vendor/legacy-no-params"},
            },
        ]
        monkeypatch.setattr(mc, "get_cached_models", lambda key: [dict(e) for e in legacy])
        # openrouter binds OPENROUTER_FREE_ONLY at module load — patch ITS
        # namespace (the developer's ~/.agentkthx/.env must not flip this).
        monkeypatch.setattr(
            "agentkthx.plugins.openrouter.openrouter.OPENROUTER_FREE_ONLY",
            False,
            raising=False,
        )

        # __init__ already ran list_models once (constructor warm-up) and a
        # fetch failure can leave the L1 class cache populated — reset it so
        # the call below takes the L2-restore path under test.
        type(or_backend)._model_cache = None
        type(or_backend)._cache_time = 0.0

        result = or_backend.list_models()
        by_name = {m["name"]: m for m in result}
        assert by_name["vendor/legacy-model"]["details"]["supports_tools"] is True
        assert (
            by_name["vendor/legacy-model"]["details"]["supports_reasoning"] is True
        )
        # no payload → no flags, entry still served
        assert (
            "supports_tools"
            not in by_name["vendor/legacy-no-params"]["details"]
        )
        # and the L1 class cache got the same upgraded list
        assert type(or_backend)._model_cache is result or (
            type(or_backend)._model_cache == result
        )


# ---------------------------------------------------------------------------
# 6. OpenRouter test_tool_support — inkling fix + metadata
# ---------------------------------------------------------------------------


class TestOpenRouterToolSupportR0736:
    def test_inkling_is_chat_native(self, or_backend):
        """THE bug: 'thinkingmachines/inkling' contains 'kling'; the raw
        substring test mis-classified it as non-chat (? untested)."""
        assert (
            or_backend.test_tool_support("thinkingmachines/inkling:free")
            is ToolSupportLevel.NATIVE
        )
        assert (
            or_backend.test_tool_support("thinkingmachines/inkling")
            is ToolSupportLevel.NATIVE
        )

    @pytest.mark.parametrize(
        "model",
        [
            "bytedance/kling-v2",  # real Kling still non-chat
            "kling-2-master",  # slug-start still matches
            "openai/whisper-large-v3",
            "sixtysecond/llama-tts",
            "black-forest-labs/flux.1-schnell",
            "openai/dall-e-3",
        ],
    )
    def test_non_chat_slugs_still_untested(self, or_backend, model):
        assert (
            or_backend.test_tool_support(model) is ToolSupportLevel.UNTESTED
        )

    def test_word_boundary_blocks_midword_hits(self, or_backend):
        """'guard' inside 'vanguard' and 'clip' inside 'eclipse' must not
        classify a chat model as non-chat."""
        assert (
            or_backend.test_tool_support("acme/vanguard-chat")
            is ToolSupportLevel.NATIVE
        )
        assert (
            or_backend.test_tool_support("acme/eclipse-chat")
            is ToolSupportLevel.NATIVE
        )

    def test_metadata_downgrade_to_react(self, or_backend, isolated_tool_cache):
        type(or_backend)._model_cache = [
            {
                "name": "nvidia/nemotron-3-super-120b-a12b:free",
                "details": {"supports_tools": False, "supports_reasoning": True},
            }
        ]
        try:
            assert or_backend.test_tool_support(
                "nvidia/nemotron-3-super-120b-a12b:free"
            ) is ToolSupportLevel.REACT
        finally:
            type(or_backend)._model_cache = None

    def test_metadata_upgrade_stays_native(self, or_backend, isolated_tool_cache):
        type(or_backend)._model_cache = [
            {
                "name": "vendor/tools-model",
                "details": {"supports_tools": True},
            }
        ]
        try:
            assert or_backend.test_tool_support("vendor/tools-model") is (
                ToolSupportLevel.NATIVE
            )
        finally:
            type(or_backend)._model_cache = None

    def test_non_chat_wins_over_metadata(self, or_backend, isolated_tool_cache):
        """A TTS slug stays UNTESTED even if a payload claimed tools."""
        type(or_backend)._model_cache = [
            {
                "name": "vendor/thing-tts",
                "details": {"supports_tools": True},
            }
        ]
        try:
            assert (
                or_backend.test_tool_support("vendor/thing-tts")
                is ToolSupportLevel.UNTESTED
            )
        finally:
            type(or_backend)._model_cache = None


# ---------------------------------------------------------------------------
# 7. CLI — the Ollama hint no longer fires for cloud backends
# ---------------------------------------------------------------------------


def _stub_env(monkeypatch, backend_obj, backend_name: str) -> None:
    """Wire cmd_models' collaborators to the stub (R07.34 harness seams)."""
    import agentkthx.cli.commands.models as models_mod
    import agentkthx.config as config_mod

    monkeypatch.setattr(
        models_mod, "get_config", lambda: argparse.Namespace(backend=backend_name)
    )
    monkeypatch.setattr(
        models_mod, "get_backend", lambda name, api_mode=None: backend_obj
    )
    monkeypatch.setattr("agentkthx.cli._init_acp", lambda *a, **k: (None, None))
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_tool_support",
        lambda model, api_mode="openre": None,
    )
    monkeypatch.setattr(
        "agentkthx.core.tool_cache.get_cached_thinking_support",
        lambda model: None,
    )
    monkeypatch.setattr(config_mod, "OPENROUTER_FREE_ONLY", False, raising=False)
    monkeypatch.setattr(config_mod, "ZAI_FREE_ONLY", False, raising=False)


def _run(argv: list[str], capsys, monkeypatch, backend_obj, backend_name: str):
    from agentkthx.cli.commands.models import cmd_models
    from agentkthx.cli.parser import create_parser

    _stub_env(monkeypatch, backend_obj, backend_name)
    # No --backend on the CLI: the parser's choices only cover local
    # backends — cmd_models falls back to the stubbed config.backend,
    # exactly like the R07.34 harness drives cloud backends.
    ns = create_parser().parse_args(["models"])
    rc = cmd_models(ns)
    out = capsys.readouterr().out
    return rc, out


class TestOllamaHintGating:
    def test_cloud_backend_no_hint(self, capsys, monkeypatch):
        rc, out = _run(
            ["models"],
            capsys,
            monkeypatch,
            _CloudCloudStub(),
            "tokenharbor",
        )
        assert rc == 0
        assert "works best with Ollama" not in out
        assert "stub-model" in out

    def test_local_static_backend_keeps_hint(self, capsys, monkeypatch):
        rc, out = _run(
            ["models"],
            capsys,
            monkeypatch,
            _LocalStub(),
            "llamacpp",
        )
        assert rc == 0
        assert "works best with Ollama" in out
