"""
R07.35 — model capability detection (context + thinking) regression tests.

Covers the owner-reported ``agentkthx models`` defects on the TokenHarbor
catalog (69 models, all showing "125K" context and "? unknown" thinking):

  1. ``fmt_token_size`` decimal-K/M branch — cloud providers advertise
     context in DECIMAL tokens (128000, 200000, 400000, 1000000) and
     128000 is an exact multiple of 1024 (125 × 1024), so the binary-only
     formatter rendered every 128K cloud model as the confusing "125K".
     Now: 128000 → '128K', 200000 → '200K', 1000000 → '1M', while every
     R07.18 expectation (512/1024/131072/1500/1000) is preserved.

  2. Thinking name-heuristics (shared cloud detector) — extended from
     explicit reasoning markers to current-generation families (Claude
     3.7+/4+/5+, GPT-5/6, Gemini 2.5+/3+, Grok 4+, DeepSeek V3.1+/V4,
     Kimi K2.5+/K3, MiMo, Mistral Large), grounded in the sibling
     backends' seed ``supports_thinking`` flags. Old generations
     (gpt-4o, gemini-2.0, claude-3-5-*, kimi-k2 base) stay UNKNOWN,
     and the vendor-segment isolation (follow-up #13) still holds.

  3. ``CloudBackend.get_model_max_context`` 4-step ladder — static
     catalog exact value → live in-process catalog entry (only REAL
     values; the legacy stamped-in 128000 is skipped so pre-R07.35
     caches self-heal) → family-knowledge table → generic 128000
     fallback. The old step 2 re-read the static catalog via
     ``get_model_info()`` (which stamps 128000 into every entry),
     shadowing everything below it.

  4. TokenHarbor live-catalog shaping — context fields are read
     TOLERANTLY from undocumented /v1/models payloads
     (``_extract_live_context``), and ``details.context_length`` is
     written only from real data (live field or seed meta) — never the
     fabricated 128000 default stamp.

All mocked (TEST-01: no integration tier), SiliconFlow/TokenHarbor
scaffold conventions.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.cli.footer import fmt_token_size
from agentkthx.core.types import ThinkingSupport
from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

# ---------------------------------------------------------------------------
# Fixtures (TokenHarbor scaffold conventions — see test_tokenharbor_backend.py)
# ---------------------------------------------------------------------------

VALID_KEY = "thk_live_" + "Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78St90Uv12Wx34Yz56Ab78Cd90Ef12"


class _FakeResponse:
    """Context-manager JSON response standing in for urlopen's return."""

    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def tokenharbor_env(monkeypatch):
    """Env + config module state for TokenHarbor construction."""
    from agentkthx import config as _config

    monkeypatch.setenv("TOKENHARBOR_API_KEY", VALID_KEY)
    monkeypatch.setenv("TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1")
    monkeypatch.setenv("TOKENHARBOR_DEFAULT_MODEL", "deepseek-v4.1-flash:free")
    monkeypatch.setenv("TOKENHARBOR_FREE_ONLY", "false")
    monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", VALID_KEY, raising=False)
    monkeypatch.setattr(
        _config, "TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1", raising=False
    )
    monkeypatch.setattr(
        _config, "TOKENHARBOR_DEFAULT_MODEL", "deepseek-v4.1-flash:free", raising=False
    )
    monkeypatch.setattr(_config, "TOKENHARBOR_FREE_ONLY", False, raising=False)
    return _config


@pytest.fixture
def backend(tokenharbor_env):
    """A TokenHarborBackend instance with a test API key configured."""
    from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

    return TokenHarborBackend()


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent model_catalog.json cache at a temp file."""
    from agentkthx import model_cache as _mc

    monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")
    return _mc


@pytest.fixture
def isolated_tool_cache(monkeypatch, tmp_path):
    """Point the tool_support.json cache (thinking verdicts) at a temp file."""
    from agentkthx.core import tool_cache as _tc

    monkeypatch.setattr(_tc, "get_cache_file", lambda: tmp_path / "tool_support.json")
    return _tc


# ---------------------------------------------------------------------------
# 1. fmt_token_size — decimal-K/M branch (R07.35)
# ---------------------------------------------------------------------------


class TestFmtTokenSizeDecimal:
    def test_decimal_128k(self):
        """128000 (125 × 1024 — the old '125K' bug) now renders '128K'."""
        assert fmt_token_size(128000) == "128K"

    def test_decimal_context_windows(self):
        """Decimal-advertised cloud context windows render cleanly."""
        assert fmt_token_size(200000) == "200K"
        assert fmt_token_size(400000) == "400K"
        assert fmt_token_size(256000) == "256K"
        assert fmt_token_size(100000) == "100K"
        assert fmt_token_size(1000000) == "1M"
        assert fmt_token_size(2000000) == "2M"

    def test_binary_windows_unchanged(self):
        """Binary window sizes keep the R07.18 rendering."""
        assert fmt_token_size(131072) == "128K"
        assert fmt_token_size(262144) == "256K"
        assert fmt_token_size(1048576) == "1M"
        assert fmt_token_size(2097152) == "2M"
        assert fmt_token_size(204800) == "200K"  # 200 × 1024 (zai glm-5.x)
        assert fmt_token_size(65536) == "64K"

    def test_r07_18_regressions(self):
        """Every documented R07.18 example is byte-identical."""
        assert fmt_token_size(0) == "0"
        assert fmt_token_size(512) == "512"
        assert fmt_token_size(1024) == "1K"
        assert fmt_token_size(2048) == "2K"
        assert fmt_token_size(8192) == "8K"
        assert fmt_token_size(None) == "?"
        assert fmt_token_size("2048") == "2K"

    def test_small_non_multiples_stay_ints(self):
        """The ≥100000 decimal floor keeps small values as plain ints."""
        assert fmt_token_size(1500) == "1500"
        assert fmt_token_size(1000) == "1000"
        assert fmt_token_size(99999) == "99999"


# ---------------------------------------------------------------------------
# 2. Thinking name-heuristics (R07.35 family expansion)
# ---------------------------------------------------------------------------


class TestThinkingNamePatterns:
    @pytest.mark.parametrize(
        "model",
        [
            # Claude 4/5+ tier ids (TokenHarbor live catalog shapes)
            "claude-opus-5.5",
            "claude-opus-5-fast",
            "claude-sonnet-4.6",
            "claude-sonnet-5.5",
            "claude-haiku-5.5:free",
            "claude-fable-5",
            "claude-3-7-sonnet",
            # GPT-5/6 reasoning-first lineup
            "gpt-5.5",
            "gpt-5.6-luna",
            "gpt-5.6-sol-fast",
            "gpt-6-astra",
            "gpt-6-luna-fast",
            "gpt-oss-120b",
            # Gemini 2.5+/3+
            "gemini-2.5-pro",
            "gemini-3.6-flash",
            "gemini-3.8-flash",
            # Grok 4+
            "grok-4.5",
            "grok-4.7",
            # DeepSeek V3.1+/V4 + R-series
            "deepseek-v3.2",
            "deepseek-v4-flash",
            "deepseek-v4.1-flash:free",
            "deepseek-r1",
            # Kimi K2.5+/K3 + explicit thinking markers
            "kimi-k2.6",
            "kimi-k3-fast",
            "kimi-k2-thinking",
            # MiMo / Mistral Large / GLM / Qwen3 / o-series (pre-existing)
            "mimo-v2.5:free",
            "mimo-v2.6-pro",
            "mistral-large-2512",
            "mistral-large-4",
            "glm-5.3-flashx",
            "qwen3.8-max",
            "o3-mini",
            "magistral-medium-latest",
            # vendor prefixes don't bleed (segment matching)
            "openai/gpt-5.4-mini",
            "anthropic/claude-sonnet-4.6",
        ],
    )
    def test_current_families_yes(self, model):
        assert OpenAICompatibleBackend._name_matches_thinking(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            # old generations stay UNKNOWN (never NO)
            "gpt-4o",
            "gpt-4.1-mini",
            "gemini-2.0-flash-exp",
            "claude-3-5-sonnet-20241022",
            "kimi-k2-instruct",
            "deepseek-v3",  # V3.0 predates the V3.1 hybrid mode
            "grok-2",
            # ungrounded families stay UNKNOWN
            "apex",
            "muse-spark-1-3",
            "muse-spark-1.2",
            # follow-up #13: vendor/ORG names must not bleed into the verdict
            "thinkingmachines/inkling",
            "reasonfactory/planner-small",
            "mimo-labs/plain-chat",  # org containing 'mimo' with a non-MiMo model
        ],
    )
    def test_unknown_stays_unknown(self, model):
        assert OpenAICompatibleBackend._name_matches_thinking(model) is False

    def test_verdict_yes_and_cached(
        self, backend, isolated_tool_cache, monkeypatch
    ):
        """test_thinking_support → YES for a current family, cached."""
        from agentkthx.core.tool_cache import get_cached_thinking_support

        verdict = backend.test_thinking_support("claude-opus-5.5")
        assert verdict is ThinkingSupport.YES
        assert get_cached_thinking_support("claude-opus-5.5") is ThinkingSupport.YES

    def test_verdict_unknown_not_cached(
        self, backend, isolated_tool_cache
    ):
        """UNKNOWN is never cached — pattern expansion takes effect live."""
        from agentkthx.core.tool_cache import get_cached_thinking_support

        verdict = backend.test_thinking_support("muse-spark-1-3")
        assert verdict is ThinkingSupport.UNKNOWN
        assert get_cached_thinking_support("muse-spark-1-3") is None


# ---------------------------------------------------------------------------
# 3. Family context table + get_model_max_context ladder (R07.35)
# ---------------------------------------------------------------------------


class TestFamilyContextGuess:
    @pytest.mark.parametrize(
        "model,expected",
        [
            # Pro/Max tiers resolve before their mainline families
            ("gemini-3.1-pro-preview", 2097152),
            ("gemini-2.5-pro", 2097152),
            ("gemini-3.8-flash", 1048576),
            ("gemini-2.5-flash", 1048576),
            ("glm-5.3", 1048576),
            ("glm-5.2", 1048576),
            ("glm-5.1", 204800),
            ("glm-4.7", 204800),
            ("gpt-6-astra", 400000),
            ("gpt-6.1-sol", 400000),
            ("gpt-5.6-luna", 200000),
            ("gpt-5.5", 200000),
            ("claude-opus-5.5", 200000),
            ("claude-haiku-5.5:free", 200000),
            ("grok-4.7", 256000),
            ("mistral-large-2512", 262144),
            ("magistral-small-latest", 131072),
            ("kimi-k2.6", 131072),
            ("kimi-k3", 131072),
            ("qwen3.8-max", 262144),
            ("tokenharbor/qwen3-max", 262144),  # segment matching
            ("qwen3.8-flash", 128000),
            ("deepseek-v4.1-flash", 131072),
            ("deepseek-v3.2", 131072),
        ],
    )
    def test_family_windows(self, model, expected):
        assert CloudBackend._family_context_guess(model) == expected

    @pytest.mark.parametrize(
        "model",
        [
            "apex",
            "muse-spark-1-3",
            "mimo-v2.5",  # no published window in repo data → None
            "llama-3.1-8b",
            "",
        ],
    )
    def test_unknown_family_none(self, model):
        assert CloudBackend._family_context_guess(model) is None

    def test_segment_isolation(self):
        """Org/vendor prefix must not leak a family verdict."""
        assert CloudBackend._family_context_guess("claudemirror/mistral-large-4") == 262144
        assert CloudBackend._family_context_guess("notgpt/gopher-7b") is None


class TestGetModelMaxContextLadder:
    """The 4-step resolution ladder on a concrete CloudBackend."""

    def test_step1_static_catalog_exact_value_wins(self, backend):
        """A seed meta with an explicit context_length beats every layer."""
        backend.MODELS = {"catalog-model": {"context_length": 204800}}
        assert backend.get_model_max_context("catalog-model") == 204800

    def test_step2_live_cache_real_value_wins(self, backend):
        """A list_models entry with a REAL context value is honored."""
        backend.MODELS = {}
        backend._model_cache = [
            {"name": "live-model", "details": {"context_length": 262144}}
        ]
        assert backend.get_model_max_context("live-model") == 262144

    def test_step2_legacy_default_stamp_skipped(self, backend):
        """Pre-R07.35 caches stamped 128000 into every entry — that value
        carries no information, so the family layer decides instead
        (self-healing without --refresh)."""
        backend.MODELS = {}
        backend._model_cache = [
            {"name": "legacy-claude-sonnet-5", "details": {"context_length": 128000}}
        ]
        assert backend.get_model_max_context("legacy-claude-sonnet-5") == 200000

    def test_step2_entry_without_context_falls_through(self, backend):
        """R07.35-shaped entries (context omitted) fall to the family layer."""
        backend.MODELS = {}
        backend._model_cache = [
            {"name": "claude-opus-5.5", "details": {"family": "tokenharbor"}}
        ]
        assert backend.get_model_max_context("claude-opus-5.5") == 200000

    def test_step3_family_table(self, backend):
        """No catalog, no live cache → family knowledge."""
        backend.MODELS = {}
        assert backend.get_model_max_context("gpt-6-astra") == 400000

    def test_step4_generic_fallback(self, backend):
        """Nothing known anywhere → the 128000 class default."""
        backend.MODELS = {}
        assert backend.get_model_max_context("apex") == 128000

    def test_junk_context_values_ignored(self, backend):
        """Negative/zero/junk context values never resolve."""
        backend.MODELS = {"junk": {"context_length": -5}}
        backend._model_cache = [
            {"name": "junk-live", "details": {"context_length": 0}}
        ]
        assert backend.get_model_max_context("junk") == 128000
        assert backend.get_model_max_context("junk-live") == 128000

    def test_missing_model_cache_attr_is_safe(self, backend):
        """Backends without _model_cache (getattr default) don't crash."""
        backend.MODELS = {}
        assert not hasattr(backend, "_model_cache") or backend._model_cache is None
        assert backend.get_model_max_context("claude-opus-5.5") == 200000


# ---------------------------------------------------------------------------
# 4. TokenHarbor live-catalog shaping (R07.35)
# ---------------------------------------------------------------------------


class TestExtractLiveContext:
    def test_openrouter_style_top_level(self):
        assert (
            TokenHarborBackend._extract_live_context({"context_length": 400000})
            == 400000
        )

    def test_alternate_field_conventions(self):
        assert (
            TokenHarborBackend._extract_live_context({"context_size": 200000})
            == 200000
        )
        assert (
            TokenHarborBackend._extract_live_context({"max_context_tokens": 1048576})
            == 1048576
        )
        assert (
            TokenHarborBackend._extract_live_context({"max_input_tokens": 131072})
            == 131072
        )

    def test_nested_top_provider(self):
        assert (
            TokenHarborBackend._extract_live_context(
                {"top_provider": {"context_length": 262144}}
            )
            == 262144
        )

    @pytest.mark.parametrize(
        "entry",
        [
            {},
            {"id": "some-model"},  # identity only — the documented minimum
            {"context_length": 0},
            {"context_length": -1},
            {"context_length": True},  # bool is an int — guarded
            {"context_length": "200000"},  # string junk
            {"context_length": 128.5},  # float junk
            {"top_provider": "openrouter"},  # non-dict nest
            {"top_provider": {"context_length": None}},
            None,
        ],
    )
    def test_junk_yields_none(self, entry):
        assert TokenHarborBackend._extract_live_context(entry) is None

    def test_first_known_field_wins(self):
        entry = {"context_size": 128000, "max_context_tokens": 262144}
        assert TokenHarborBackend._extract_live_context(entry) == 128000


class TestTokenHarborEntryShaping:
    def test_no_fabricated_default_in_entries(self, backend):
        """Seed meta without context_length → the key is OMITTED (was:
        stamped 128000, rendering every model 125K in the models table)."""
        entry = backend._catalog_entry("claude-opus-5.5", {})
        assert "context_length" not in entry["details"]
        assert entry["details"]["family"] == "tokenharbor"
        assert entry["details"]["free_tier"] is False

    def test_seed_meta_context_flows_through(self, backend):
        entry = backend._catalog_entry("some-model", {"context_length": 204800})
        assert entry["details"]["context_length"] == 204800

    def test_live_ctx_beats_seed_meta(self, backend):
        entry = backend._catalog_entry(
            "some-model", {"context_length": 131072}, live_ctx=400000
        )
        assert entry["details"]["context_length"] == 400000

    def test_free_suffix_still_detected(self, backend):
        entry = backend._catalog_entry("deepseek-v4.1-flash:free", {})
        assert entry["details"]["free_tier"] is True

    def test_fallback_list_omits_unknown_context(self, backend):
        """The 15-id seed carries no context_length → none stamped."""
        for entry in backend._catalog_fallback_list():
            assert "context_length" not in entry["details"]


class TestLiveFetchContextShaping:
    def test_live_payload_context_captured(self, backend, isolated_cache, monkeypatch):
        """A /v1/models entry that carries context fields keeps them."""
        live = {
            "data": [
                {"id": "openrouter-style", "context_length": 400000},
                {"id": "nested-style", "top_provider": {"context_length": 262144}},
                {"id": "contextless-model"},
            ]
        }

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(live)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        models = backend.list_models()
        by_name = {m["name"]: m for m in models}

        assert by_name["openrouter-style"]["details"]["context_length"] == 400000
        assert by_name["nested-style"]["details"]["context_length"] == 262144
        assert "context_length" not in by_name["contextless-model"]["details"]

    def test_live_entries_reach_context_ladder(
        self, backend, isolated_cache, monkeypatch
    ):
        """End-to-end: live fetch → list_models → get_model_max_context."""
        live = {
            "data": [
                {"id": "ctx-model", "context_length": 2097152},
                {"id": "family-claude-sonnet-5"},  # no field → family table
            ]
        }

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(live)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        backend.list_models()  # populates _model_cache

        assert backend.get_model_max_context("ctx-model") == 2097152
        assert backend.get_model_max_context("family-claude-sonnet-5") == 200000
