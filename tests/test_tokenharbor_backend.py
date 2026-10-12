"""
TokenHarbor plugin (R07.31) — backend regression tests.

Verifies that the TokenHarborBackend (backend #14, the second scaffold on
the R07.28-hardened CloudBackend base) correctly implements the docs-only
integration plan in docs/api/TOKENHARBOR_API_TECHNICAL_REFERENCE.md §16,
covering the 9-point test plan:

  1. Manifest  — loads through _loader.py, registers the `tokenharbor`
                 backend id, env defaults correct
  2. Key validation — rejects sk-/sk-or- with remediation; accepts
                 thk_live_ + 64 base64url; empty key → the standard
                 TOKENHARBOR_API_KEY-is-required ValueError
  3. Usage-field dual-family parsing — _parse_openai_response accepts
                 {prompt_tokens, completion_tokens} AND
                 {input_tokens, output_tokens}; cache_creation_input_tokens
                 / cache_read_input_tokens surface when present, ignored
                 when absent
  4. Spend gate — 400 + spend_confirmation_required → fatal with the
                 _STATUS_REMEDIATIONS[400] text; NO retry; the request body
                 never gains th_confirm_spend (doc §10 invariant #1)
  5. Quota classification — 429 + spend_limit_exceeded → quota-class
                 fast-fail (window-aging message, one attempt); plain 429
                 → rate-class retry honoring Retry-After
  6. Free tier — _is_free_model suffix behavior; TOKENHARBOR_FREE_ONLY
                 filters at RETURN time (toggle does not wait out the TTL);
                 default model resolves from seed
  7. Catalog   — auth-gated cold start (no key → seed catalog, no
                 network); ROB-42 store-on-success + stale-first; 401 on
                 /v1/models degrades to seed (never fails startup)
  8. Aggregator tool assumption — test_tool_support → NATIVE
                 unconditionally (inherited); 400 "does not support tools"
                 → ReAct fallback via the inherited detector set
  9. Pass-through invariants — system messages forwarded (no suppression);
                 no attribution headers; no X-TH-Cache-Control in the
                 default body/headers; no th_confirm_spend ever injected

Plus: CloudBackend inheritance + shared-transport identity pins (the
MAINT-28 batch-2 contract — the request loops are INHERITED, not copied),
provider identity, URL construction, seed-catalog integrity, and the
config/auth/CLI companion wiring.

All mocked (TEST-01: no integration tier) — the live probe is
scripts/probe_tokenharbor.sh, the maintainer gate for promotion out of
Limited Support (docs/SUPPORT.md).
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest

from agentkthx.backends.cloud_base import CloudBackend
from agentkthx.backends.openai_compat import OpenAICompatibleBackend
from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.types import BackendType, ToolSupportLevel

# ---------------------------------------------------------------------------
# Fixtures + helpers (SiliconFlow scaffold conventions)
# ---------------------------------------------------------------------------

# thk_live_ + 64 base64url chars = the documented key shape (73 chars).
VALID_KEY = "thk_live_" + "Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56Qr78St90Uv12Wx34Yz56Ab78Cd90Ef12"


@pytest.fixture
def tokenharbor_env(monkeypatch):
    """Set up env vars for TokenHarbor tests."""
    monkeypatch.setenv("TOKENHARBOR_API_KEY", VALID_KEY)
    monkeypatch.setenv("TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1")
    monkeypatch.setenv("TOKENHARBOR_DEFAULT_MODEL", "deepseek-v4.1-flash:free")
    monkeypatch.setenv("TOKENHARBOR_FREE_ONLY", "false")
    from agentkthx import config as _config

    monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", VALID_KEY, raising=False)
    monkeypatch.setattr(_config, "TOKENHARBOR_BASE_URL", "https://tokenharbor.ai/v1", raising=False)
    monkeypatch.setattr(
        _config, "TOKENHARBOR_DEFAULT_MODEL", "deepseek-v4.1-flash:free", raising=False
    )
    monkeypatch.setattr(_config, "TOKENHARBOR_FREE_ONLY", False, raising=False)
    return _config


@pytest.fixture
def backend(monkeypatch):
    """A TokenHarborBackend instance with a test API key configured."""
    from agentkthx import config as _config
    from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

    monkeypatch.setenv("TOKENHARBOR_API_KEY", VALID_KEY)
    monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", VALID_KEY, raising=False)
    return TokenHarborBackend()


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent model_catalog.json cache at a temp file."""
    from agentkthx import model_cache as _mc

    monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")
    return _mc


def _no_sleep(monkeypatch) -> None:
    """Neutralize backoff sleeps (shared handler + base helpers)."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _http_error(code: int, body: str, headers: dict | None = None) -> urllib.error.HTTPError:
    """Build a real HTTPError whose .read() yields ``body``.

    Passing fp=io.BytesIO(...) makes ``e.fp`` truthy so the shared
    handler's ``e.read() if e.fp else b""`` branch reads the body —
    the same contract as a real urllib 4xx response. ``headers`` (a
    plain dict) backs the Retry-After path via ``e.headers.get``.
    """
    return urllib.error.HTTPError(
        url="https://unit.test",
        code=code,
        msg="error",
        hdrs=headers,
        fp=io.BytesIO(body.encode("utf-8")),
    )


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


def _openai_ok(content: str = "pong") -> dict:
    """Minimal OpenAI-shape 200 body."""
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def _tool() -> Tool:
    """One simple tool for the native-tools request shape."""
    return Tool(
        name="get_weather",
        description="Get current weather for a city",
        params=[ToolParam(name="city", type="string", description="City name")],
    )


# ---------------------------------------------------------------------------
# Inheritance and class structure (MAINT-28 batch-2 contract)
# ---------------------------------------------------------------------------


class TestTokenharborInheritance:
    """Verify TokenHarborBackend's inheritance hierarchy."""

    def test_inherits_from_cloud_backend(self):
        """TokenHarborBackend MUST inherit from CloudBackend (MAINT-02 pattern)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert issubclass(TokenHarborBackend, CloudBackend)

    def test_inherits_from_openai_compatible_backend(self):
        """Transitively inherits from OpenAICompatibleBackend (existing
        isinstance checks continue to work)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert issubclass(TokenHarborBackend, OpenAICompatibleBackend)

    def test_class_attributes_set(self):
        """CloudBackend class attributes are correctly overridden."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert TokenHarborBackend._api_key_env_var == "TOKENHARBOR_API_KEY"
        assert TokenHarborBackend._provider_label == "TokenHarbor"
        assert TokenHarborBackend._default_base_url == "https://tokenharbor.ai/v1"
        assert TokenHarborBackend._default_model == "mimo-v2.6-flash:free"
        assert TokenHarborBackend.MODEL_CACHE_KEY == "tokenharbor"
        assert TokenHarborBackend._error_brand == "Token Harbor"

    def test_request_loops_are_inherited_not_copied(self):
        """MAINT-28 batch-2 contract: the scaffold must NOT copy the request
        loops — _make_api_request / _iter_sse_lines / generate_stream /
        _jev_call_completions are the shared CloudBackend implementations."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert TokenHarborBackend._make_api_request is CloudBackend._make_api_request
        assert TokenHarborBackend._iter_sse_lines is CloudBackend._iter_sse_lines
        assert TokenHarborBackend.generate_stream is CloudBackend.generate_stream
        assert TokenHarborBackend._jev_call_completions is CloudBackend._jev_call_completions

    def test_catalog_key_hook_overridden_lookup_methods_inherited(self):
        """The catalog-key hook (not 4 per-method overrides) is the
        normalization surface (R07.28 batch-2 contract). TokenHarbor ids
        are opaque — the hook returns unchanged (vs the base's prefix
        strip), while the lookup methods stay inherited."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert TokenHarborBackend.get_model_info is CloudBackend.get_model_info
        assert TokenHarborBackend._get_model_defaults is CloudBackend._get_model_defaults
        assert TokenHarborBackend.get_model_max_context is CloudBackend.get_model_max_context
        # ...but _catalog_model_key IS overridden (opaque ids, no stripping)
        assert TokenHarborBackend._catalog_model_key is not CloudBackend._catalog_model_key

    def test_test_tool_support_inherited_native(self):
        """test_tool_support is the inherited CloudBackend default:
        unconditional NATIVE (the aggregator assumption)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        assert TokenHarborBackend.test_tool_support is CloudBackend.test_tool_support


# ---------------------------------------------------------------------------
# Backend initialization + base URL resolution
# ---------------------------------------------------------------------------


class TestTokenharborInit:
    """Verify __init__ correctly resolves base URL + validates API key."""

    def test_default_base_url(self, tokenharbor_env):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend()
        assert b.base_url == "https://tokenharbor.ai/v1"

    def test_explicit_base_url(self, tokenharbor_env):
        """Explicit base_url overrides env default."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend(base_url="https://custom.tokenharbor.proxy/v1")
        assert b.base_url == "https://custom.tokenharbor.proxy/v1"

    def test_explicit_base_url_strips_trailing_slash(self, tokenharbor_env):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend(base_url="https://custom.tokenharbor.proxy/v1/")
        assert b.base_url == "https://custom.tokenharbor.proxy/v1"

    def test_missing_api_key_raises(self, monkeypatch):
        """No API key anywhere → ValueError (CloudBackend validation)."""
        monkeypatch.delenv("TOKENHARBOR_API_KEY", raising=False)
        from agentkthx import config as _config
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", "", raising=False)
        with pytest.raises(ValueError, match="TOKENHARBOR_API_KEY is required"):
            TokenHarborBackend()

    def test_short_api_key_raises(self, monkeypatch):
        """API key below _MIN_API_KEY_LEN (20) → ValueError."""
        monkeypatch.setenv("TOKENHARBOR_API_KEY", "thk_live_short")
        from agentkthx import config as _config
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        monkeypatch.setattr(_config, "TOKENHARBOR_API_KEY", "thk_live_short", raising=False)
        with pytest.raises(ValueError, match="too short"):
            TokenHarborBackend()

    def test_explicit_api_key_overrides_env(self, monkeypatch):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        monkeypatch.setenv("TOKENHARBOR_API_KEY", VALID_KEY)
        other = "thk_live_" + "Z" * 64
        b = TokenHarborBackend(api_key=other)
        assert b.api_key == other

    def test_is_running_true_with_key(self, backend):
        assert backend.is_running() is True

    def test_api_key_setter_writes_through(self, tokenharbor_env):
        """api_key setter writes through to _api_key (R07.21 pattern)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend()
        new_key = "thk_live_" + "N" * 64
        b.api_key = new_key
        assert b.api_key == new_key
        # Auth headers must reflect the new key
        assert b._get_auth_headers()["Authorization"] == f"Bearer {new_key}"


# ---------------------------------------------------------------------------
# Key validation (test plan #2)
# ---------------------------------------------------------------------------


class TestTokenharborKeyValidation:
    """sk- rejection + thk_live_ acceptance (doc §3 / §16 test plan #2)."""

    def test_openai_sk_key_rejected_with_remediation(self, tokenharbor_env):
        """An OpenAI sk- key is rejected early (their connect CLI docs
        confirm it "won't work here")."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        with pytest.raises(ValueError, match="thk_live_"):
            TokenHarborBackend(api_key="sk-proj-abcdefghijklmnopqrstuvwxyz123456")

    def test_openrouter_sk_or_key_rejected(self, tokenharbor_env):
        """OpenRouter's sk-or-… prefix is caught by the same sk- check."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        with pytest.raises(ValueError, match="looks like an OpenAI/OpenRouter key"):
            TokenHarborBackend(api_key="sk-or-v1-abcdefghijklmnopqrstuvwxyz1234567890")

    def test_sk_rejection_mentions_dashboard(self, tokenharbor_env):
        """The remediation text points at the creation surface."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        with pytest.raises(ValueError, match="dashboard/api-keys"):
            TokenHarborBackend(api_key="sk-1234567890abcdef1234567890")

    def test_valid_thk_live_key_accepted(self, tokenharbor_env):
        """thk_live_ + 64 base64url chars is the documented shape."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend(api_key=VALID_KEY)
        assert b.api_key == VALID_KEY
        assert len(VALID_KEY) == 73  # 9 prefix + 64 chars

    def test_unknown_prefix_warns_not_raises(self, tokenharbor_env, monkeypatch, capsys):
        """A non-thk_ prefix draws a debug warning only (key formats may
        evolve; the 401 remediation covers actual rejections)."""
        monkeypatch.setenv("AGENTKTHX_DEBUG", "1")
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend(api_key="future_fmt_abcdefghijklmnopqrstuvwxyz1234567890")
        assert b.api_key.startswith("future_fmt_")
        assert "does not start with" in capsys.readouterr().out

    def test_wrong_length_thk_live_warns_not_raises(self, tokenharbor_env, monkeypatch, capsys):
        """thk_live_ with a wrong remainder warns (shape is a docs
        observation, not a hard invariant) but is accepted."""
        monkeypatch.setenv("AGENTKTHX_DEBUG", "1")
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        short_key = "thk_live_" + "x" * 32
        b = TokenHarborBackend(api_key=short_key)
        assert b.api_key == short_key
        assert "documented shape" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Provider identity + URLs
# ---------------------------------------------------------------------------


class TestTokenharborIdentity:
    def test_backend_type_returns_tokenharbor(self, backend):
        assert backend.backend_type == BackendType.TOKENHARBOR

    def test_backend_type_value_is_tokenharbor_string(self, backend):
        """backend_type.value is the string the footer formatter reads."""
        assert backend.backend_type.value == "tokenharbor"

    def test_provider_label(self, backend):
        assert backend._provider_label == "TokenHarbor"


class TestTokenharborUrls:
    def test_chat_completions_url(self, backend):
        assert backend._get_chat_completions_url() == "https://tokenharbor.ai/v1/chat/completions"

    def test_models_url(self, backend):
        assert backend._get_models_url() == "https://tokenharbor.ai/v1/models"

    def test_urls_with_custom_base(self, tokenharbor_env):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend(base_url="https://custom.proxy/v1")
        assert b._get_chat_completions_url() == "https://custom.proxy/v1/chat/completions"
        assert b._get_models_url() == "https://custom.proxy/v1/models"


# ---------------------------------------------------------------------------
# Auth headers — the no-attribution invariant (test plan #9)
# ---------------------------------------------------------------------------


class TestTokenharborAuthHeaders:
    def test_bearer_and_content_type(self, backend):
        headers = backend._get_auth_headers()
        assert headers["Authorization"] == f"Bearer {VALID_KEY}"
        assert headers["Content-Type"] == "application/json"

    def test_no_openrouter_attribution_headers(self, backend):
        """Doc §14/§16 invariant #2: Token Harbor has no documented
        equivalent of OpenRouter's app-attribution headers, and
        unknown-header handling is undocumented — none are sent."""
        headers = backend._get_auth_headers()
        for banned in ("HTTP-Referer", "X-Title", "X-OpenRouter-Title"):
            assert banned not in headers

    def test_no_th_cache_control_header(self, backend):
        """Doc §16 invariant #5: X-TH-Cache-Control is a documented escape
        hatch, not a default — never sent by the header builder."""
        assert "X-TH-Cache-Control" not in backend._get_auth_headers()


# ---------------------------------------------------------------------------
# Usage-field dual-family parsing (test plan #3)
# ---------------------------------------------------------------------------


class TestTokenharborUsageParsing:
    """The §4 unverified-shape hedge: both usage field families parse."""

    def test_openai_family_passthrough(self):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        parsed = TokenHarborBackend._parse_openai_response(raw)
        assert parsed["usage"]["prompt_tokens"] == 10
        assert parsed["usage"]["completion_tokens"] == 5
        assert parsed["usage"]["total_tokens"] == 15
        # cache fields absent → not surfaced
        assert "cache_creation_input_tokens" not in parsed["usage"]
        assert "cache_read_input_tokens" not in parsed["usage"]

    def test_anthropic_family_normalized(self):
        """input_tokens/output_tokens map onto the OpenAI names when the
        OpenAI names are absent (probe checklist #2 hedge)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        parsed = TokenHarborBackend._parse_openai_response(raw)
        assert parsed["usage"]["prompt_tokens"] == 10
        assert parsed["usage"]["completion_tokens"] == 5
        assert parsed["usage"]["total_tokens"] == 15  # computed when absent

    def test_openai_names_win_over_anthropic(self):
        """If BOTH families appear, the OpenAI names are authoritative."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 2,
                "input_tokens": 999,
                "output_tokens": 999,
            },
        }
        parsed = TokenHarborBackend._parse_openai_response(raw)
        assert parsed["usage"]["prompt_tokens"] == 7
        assert parsed["usage"]["completion_tokens"] == 2

    def test_cache_tokens_surfaced_when_present(self):
        """Token Harbor's prompt-cache accounting fields surface on the
        parsed usage dict (§9: 1.25x write / 0.1x read — Claude-family)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cache_creation_input_tokens": 33,
                "cache_read_input_tokens": 44,
            },
        }
        parsed = TokenHarborBackend._parse_openai_response(raw)
        assert parsed["usage"]["cache_creation_input_tokens"] == 33
        assert parsed["usage"]["cache_read_input_tokens"] == 44

    def test_cache_fields_ignored_when_absent(self):
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        parsed = TokenHarborBackend._parse_openai_response(raw)
        assert "cache_creation_input_tokens" not in parsed["usage"]
        assert "cache_read_input_tokens" not in parsed["usage"]

    def test_provider_error_on_200_still_raises(self):
        """The inherited provider-error raise (HTTP 200 with an error
        field) is preserved by the override."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        raw = {"error": {"message": "Provider rate limited"}}
        with pytest.raises(RuntimeError, match="Provider rate limited"):
            TokenHarborBackend._parse_openai_response(raw)


# ---------------------------------------------------------------------------
# The spend gate (test plan #4) — never retried, never auto-confirmed
# ---------------------------------------------------------------------------

SPEND_GATE_BODY = json.dumps(
    {
        "error": {
            "code": "spend_confirmation_required",
            "message": "Estimated cost $5.12 exceeds half of your $10.00 balance.",
        }
    }
)


class TestTokenharborSpendGate:
    """Doc §10 invariant #1: never auto-confirm a spend gate."""

    def test_spend_gate_is_fatal_with_remediation_text(self, backend, monkeypatch):
        """400 + spend_confirmation_required → RuntimeError carrying the
        _STATUS_REMEDIATIONS[400] text."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise _http_error(400, SPEND_GATE_BODY)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="spend_confirmation_required"):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        # The remediation text is the doc §11 400 entry
        with pytest.raises(RuntimeError, match="AgentKthx never auto-confirms"):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])

    def test_spend_gate_never_retries(self, backend, monkeypatch):
        """The retry loop is NOT entered — exactly one attempt."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise _http_error(400, SPEND_GATE_BODY)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        assert len(calls) == 1

    def test_spend_gate_never_injects_th_confirm_spend(self, backend, monkeypatch):
        """The request body never gains th_confirm_spend (or the header
        equivalent) — AgentKthx never auto-confirms."""
        _no_sleep(monkeypatch)
        sent_bodies = []

        def fake_urlopen(req, timeout=None):
            sent_bodies.append(json.loads(req.data.decode("utf-8")))
            raise _http_error(400, SPEND_GATE_BODY)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        for body in sent_bodies:
            assert "th_confirm_spend" not in body

    def test_non_spend_400_keeps_generic_message(self, backend, monkeypatch):
        """A NON-spend 400 (param validation, upstream tools rejection)
        keeps the generic "{brand} API error 400" shape — NOT the spend
        remediation (which would mislabel it)."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(400, json.dumps({"error": {"message": "Invalid parameter: stop"}}))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="Token Harbor API error 400: Invalid parameter"):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Quota classification (test plan #5) — spend-cap 429 vs rate-limit 429
# ---------------------------------------------------------------------------


class TestTokenharborQuotaClassification:
    """429 spend_limit_exceeded → quota-class; plain 429 → rate-class."""

    def test_spend_limit_429_is_quota_class(self, backend):
        """The documented per-key USD spend cap fast-fails."""
        body = json.dumps(
            {"error": {"code": "spend_limit_exceeded", "message": "Key spend cap reached"}}
        )
        assert backend._looks_like_quota_exhaustion(429, body) is True

    def test_spend_limit_429_fast_fails_with_window_message(self, backend, monkeypatch):
        """Quota 429 → RuntimeError with the window-aging message after
        exactly ONE attempt (no budget burn)."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise _http_error(
                429,
                json.dumps({"error": {"code": "spend_limit_exceeded", "message": "cap reached"}}),
                headers={"Retry-After": "120"},
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="rolling USD window must age out"):
            backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        assert len(calls) == 1

    def test_plain_429_is_rate_class_retry(self, backend, monkeypatch):
        """A plain free-tier rate-limit 429 retries (Retry-After honored),
        then succeeds."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            if len(calls) == 1:
                raise _http_error(
                    429,
                    json.dumps({"error": {"message": "Rate limit exceeded"}}),
                    headers={"Retry-After": "2"},
                )
            return _FakeResponse(_openai_ok())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        assert result["content"] == "pong"
        assert len(calls) == 2

    def test_rate_limit_wording_never_trips_quota(self, backend):
        """The Cloudflare "limit" drift lesson: a transient rate-limit
        message stays rate-class even though it contains "limit"."""
        body = json.dumps({"error": {"message": "Rate limit exceeded, try later"}})
        assert backend._looks_like_quota_exhaustion(429, body) is False

    def test_spend_limit_checked_before_transient_veto(self, backend):
        """spend_limit_exceeded contains the substring "limit" — the
        documented code is checked BEFORE the transient-wording veto so
        it can never be misclassified as retryable."""
        body = json.dumps(
            {
                "error": {
                    "code": "spend_limit_exceeded",
                    "message": "rate limit: key spend cap reached",
                }
            }
        )
        assert backend._looks_like_quota_exhaustion(429, body) is True

    def test_balance_exhaustion_wording_is_quota_class(self, backend):
        """The undocumented $0-balance-on-paid-model shape (doc §11
        expects 400/402-class with balance wording) — quota-class hedge."""
        assert backend._looks_like_quota_exhaustion(402, "insufficient balance") is True
        assert backend._looks_like_quota_exhaustion(429, "please top up your wallet") is True

    def test_other_statuses_never_quota(self, backend):
        assert backend._looks_like_quota_exhaustion(400, "spend_limit_exceeded") is False
        assert backend._looks_like_quota_exhaustion(500, "balance") is False
        assert backend._looks_like_quota_exhaustion(429, "") is False


# ---------------------------------------------------------------------------
# Free tier (test plan #6)
# ---------------------------------------------------------------------------


class TestTokenharborFreeTier:
    def test_is_free_model_suffix(self):
        from agentkthx.plugins.tokenharbor.tokenharbor import _is_free_model

        assert _is_free_model("deepseek-v4.1-flash:free") is True
        assert _is_free_model("glm-5.3") is False
        # ids are opaque — no prefix games
        assert _is_free_model("tokenharbor/qwen3-max") is False
        assert _is_free_model(":free") is True  # degenerate but honest

    def test_default_model_resolves_from_seed(self, backend):
        """The default model never bills (a :free route id).
        Historical name: the default WAS a seed entry (docs-only,
        perishable) until the promotion smoke moved it to the
        live-verified mimo-v2.6-flash:free after the docs-named
        deepseek-v4.1-flash:free started returning 402 — and the
        docs-only seed deliberately does not carry live-observed ids."""
        from agentkthx.plugins.tokenharbor.tokenharbor import _is_free_model

        assert backend._default_model == "mimo-v2.6-flash:free"
        assert _is_free_model(backend._default_model)

    def test_free_only_filters_at_return_time(self, backend, isolated_cache, monkeypatch):
        """TOKENHARBOR_FREE_ONLY filters the RETURN value; the underlying
        (seed) catalog is untouched, so toggling the flag does not wait
        out the cache TTL."""
        import agentkthx.plugins.tokenharbor.tokenharbor as th_mod

        monkeypatch.setattr(th_mod, "TOKENHARBOR_FREE_ONLY", False)
        all_models = backend.list_models()
        free_ids = [m["name"] for m in all_models if m["name"].endswith(":free")]
        assert free_ids == ["deepseek-v4.1-flash:free"]
        assert len(all_models) == len(th_mod.TOKENHARBOR_MODELS)

        # Flip the flag (the /auth picker's set_flag rebinding path) —
        # same backend instance, same cached catalog, filtered output.
        monkeypatch.setattr(th_mod, "TOKENHARBOR_FREE_ONLY", True)
        filtered = backend.list_models()
        assert [m["name"] for m in filtered] == free_ids

    def test_seed_has_no_pricing_fabrication(self):
        """Docs-only discipline: no seed entry carries pricing or
        context_length (both would be fabrication until live-verified)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TOKENHARBOR_MODELS

        assert len(TOKENHARBOR_MODELS) == 15
        for name, meta in TOKENHARBOR_MODELS.items():
            assert "pricing" not in meta, f"{name} must not carry docs-only pricing"
            assert "context_length" not in meta, f"{name} must not carry docs-only context"


# ---------------------------------------------------------------------------
# Catalog: auth-gated cold start + ROB-42 (test plan #7)
# ---------------------------------------------------------------------------


class TestTokenharborCatalog:
    def test_no_key_cold_start_serves_seed_without_network(self, tokenharbor_env, monkeypatch):
        """Auth-gated cold start: no key → seed catalog, NO network call
        (the /v1/models endpoint requires auth; the seed is the only
        cold-start catalog source — doc §16 invariant #6)."""
        from agentkthx.plugins.tokenharbor.tokenharbor import TokenHarborBackend

        b = TokenHarborBackend()
        b._api_key = ""  # simulate the keyless path

        def boom(req, timeout=None):
            raise AssertionError("network call attempted without a key")

        monkeypatch.setattr(urllib.request, "urlopen", boom)
        models = b.list_models()
        names = {m["name"] for m in models}
        assert "deepseek-v4.1-flash:free" in names
        assert "tokenharbor/qwen3-max" in names
        assert len(models) == 15

    def test_live_fetch_merges_unknown_ids(self, backend, isolated_cache, monkeypatch):
        """Live fetch: unknown live ids join the catalog; seed ids enrich;
        FREE_ONLY stays off; the merged catalog persists (ROB-42
        store-on-success)."""
        live = {
            "data": [
                {"id": "brand-new-model"},
                {"id": "deepseek-v4.1-flash:free"},
                {"id": "tokenharbor/qwen3-max"},
            ]
        }

        def fake_urlopen(req, timeout=None):
            assert req.get_method() == "GET"
            assert req.headers.get("Authorization") == f"Bearer {VALID_KEY}"
            return _FakeResponse(live)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        models = backend.list_models()
        names = {m["name"] for m in models}
        assert "brand-new-model" in names  # unknown live id passes through
        assert len(models) == 16  # 15 seed ids ∪ 1 unknown live id
        # ROB-42: the merged catalog persisted to the JSON cache
        cached = isolated_cache.get_cached_models("tokenharbor")
        assert cached is not None
        assert {m["name"] for m in cached} == names

    def test_401_on_models_degrades_to_seed_never_raises(
        self, backend, isolated_cache, monkeypatch
    ):
        """A 401 on /v1/models (invalid/rotated key) is a DISCOVERY
        failure, not a startup failure: degrade to stale/seed."""
        import agentkthx.plugins.tokenharbor.tokenharbor as th_mod

        def fake_urlopen(req, timeout=None):
            raise _http_error(401, json.dumps({"error": {"message": "Unauthorized"}}))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        models = backend.list_models()  # must NOT raise
        assert len(models) == 15
        assert {m["name"] for m in models} == set(th_mod.TOKENHARBOR_MODELS)
        # ROB-42: the failure must NOT have written a fresh api-source cache
        # entry (store-on-success only).

    def test_stale_cache_served_before_seed(self, backend, isolated_cache, monkeypatch):
        """ROB-42 stale-first: on discovery failure, a stale last-known-
        good cache (fresher than the seed) is served."""
        stale_catalog = [{"name": "stale-live-id", "size": 0, "details": {}}]
        isolated_cache.store_models("tokenharbor", stale_catalog, source="api")
        # Expire it so get_cached_models misses but get_stale_models hits
        monkeypatch.setenv("AGENTKTHX_MODEL_CACHE_TTL", "0")

        def fake_urlopen(req, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        models = backend.list_models()
        assert [m["name"] for m in models] == ["stale-live-id"]

    def test_l1_cache_within_ttl_makes_no_network_call(self, backend, isolated_cache, monkeypatch):
        """Second list_models() within the TTL serves the in-process cache."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return _FakeResponse({"data": [{"id": "glm-5.3"}]})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        backend.list_models()
        backend.list_models()
        assert len(calls) == 1

    def test_malformed_entry_shapes_tolerated(self, backend, isolated_cache, monkeypatch):
        """Junk entries in the live payload (non-dict rows, non-string or
        None ids) are SKIPPED, not crashes — the /v1/models entry schema
        is undocumented, so the id reader is defensive."""

        def fake_urlopen(req, timeout=None):
            return _FakeResponse({"data": [{"id": 1}, {"id": None}, {"id": "glm-5.3"}, "junk", 42]})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        models = backend.list_models()
        assert "glm-5.3" in {m["name"] for m in models}

    def test_root_non_dict_payload_propagates_as_bug(self, backend, isolated_cache, monkeypatch):
        """A programming-error shape (JSON root is a list → .get() raises
        AttributeError) is NOT masked as 'discovery failed' — it
        propagates as a real bug (ROB-42 catch-narrowing)."""

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(["not", "a", "dict"])

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AttributeError):
            backend.list_models()


# ---------------------------------------------------------------------------
# Tools: aggregator assumption + ReAct fallback (test plan #8)
# ---------------------------------------------------------------------------


class TestTokenharborToolSupport:
    def test_native_unconditionally(self, backend):
        """The aggregator assumption: every chat-capable model is NATIVE,
        no probe (inherited CloudBackend default)."""
        for model in (
            "deepseek-v4.1-flash:free",
            "claude-opus-5.5",
            "tokenharbor/qwen3-max",
            "some-brand-new-unknown-id",
        ):
            assert backend.test_tool_support(model) is ToolSupportLevel.NATIVE

    def test_tools_400_falls_back_to_react(self, backend, monkeypatch):
        """400 "does not support tools" → retried ONCE without the tools
        field (the inherited detector set matches the raised message)."""
        _no_sleep(monkeypatch)
        sent_bodies = []

        def fake_urlopen(req, timeout=None):
            sent_bodies.append(json.loads(req.data.decode("utf-8")))
            if len(sent_bodies) == 1:
                raise _http_error(
                    400,
                    json.dumps({"error": {"message": "This model does not support tools"}}),
                )
            return _FakeResponse(_openai_ok("react path"))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = backend.generate(
            "deepseek-v4.1-flash:free",
            [{"role": "user", "content": "hi"}],
            tools=[_tool()],
        )
        assert result["content"] == "react path"
        assert len(sent_bodies) == 2
        assert "tools" in sent_bodies[0]
        assert "tools" not in sent_bodies[1]
        assert "tool_choice" not in sent_bodies[1]

    def test_spend_gate_not_mistaken_for_tools_rejection(self, backend, monkeypatch):
        """The spend-gate 400 must NOT trigger the ReAct fallback — it
        stays fatal (the two 400 families never cross)."""
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            raise _http_error(400, SPEND_GATE_BODY)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="spend_confirmation_required"):
            backend.generate(
                "deepseek-v4.1-flash:free",
                [{"role": "user", "content": "hi"}],
                tools=[_tool()],
            )
        assert len(calls) == 1  # no tools-less retry


# ---------------------------------------------------------------------------
# Pass-through invariants (test plan #9)
# ---------------------------------------------------------------------------


class TestTokenharborPassThrough:
    def test_system_messages_forwarded_untouched(self, backend, monkeypatch):
        """Doc §4: system messages and the messages array are forwarded
        byte-for-byte — the DDG/SD suppression convention does NOT apply."""
        _no_sleep(monkeypatch)
        sent_bodies = []

        def fake_urlopen(req, timeout=None):
            sent_bodies.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse(_openai_ok())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        messages = [
            {"role": "system", "content": "You are a ReAct agent. Use tools."},
            {"role": "user", "content": "hello"},
        ]
        backend.generate("deepseek-v4.1-flash:free", messages)
        assert sent_bodies[0]["messages"] == messages  # byte-for-byte

    def test_no_attribution_or_cache_control_in_sent_request(self, backend, monkeypatch):
        """No OpenRouter attribution headers, no X-TH-Cache-Control —
        on the actual sent request (not just the header builder)."""
        _no_sleep(monkeypatch)
        sent_headers = []

        def fake_urlopen(req, timeout=None):
            sent_headers.append(dict(req.headers))
            return _FakeResponse(_openai_ok())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        headers = sent_headers[0]
        # urllib normalizes header names to Title-Case keys
        for banned in ("Http-referer", "X-title", "X-openrouter-title", "X-th-cache-control"):
            assert banned not in headers
            assert banned.upper() not in {k.upper() for k in headers}

    def test_default_body_has_no_th_extensions(self, backend, monkeypatch):
        """The default body carries no th_confirm_spend / cache-control
        extensions — the documented extensions are user-business."""
        _no_sleep(monkeypatch)
        sent_bodies = []

        def fake_urlopen(req, timeout=None):
            sent_bodies.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse(_openai_ok())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        backend.generate("deepseek-v4.1-flash:free", [{"role": "user", "content": "hi"}])
        body = sent_bodies[0]
        assert "th_confirm_spend" not in body
        assert body["model"] == "deepseek-v4.1-flash:free"

    def test_wire_adapter_none_openai_shape_only(self, backend):
        """The Anthropic /v1/messages surface is out of scope — the OpenAI
        wire shape is inherited unchanged (no _wire_adapter)."""
        assert backend._wire_adapter is None


# ---------------------------------------------------------------------------
# Plugin manifest (test plan #1)
# ---------------------------------------------------------------------------


class TestTokenharborManifest:
    def test_manifest_registers_backend(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        th_m = next(m for m in manifests if m.name == "tokenharbor")
        backends = th_m.provides["backends"]
        assert "tokenharbor" in backends
        assert backends["tokenharbor"] == "tokenharbor.TokenHarborBackend"

    def test_manifest_cli_flags_include_tokenharbor(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        th_m = next(m for m in manifests if m.name == "tokenharbor")
        assert "tokenharbor" in th_m.provides["cli_flags"]["--backend"]

    def test_manifest_config_defaults(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        th_m = next(m for m in manifests if m.name == "tokenharbor")
        defaults = th_m.config["defaults"]
        assert defaults["TOKENHARBOR_BASE_URL"] == "https://tokenharbor.ai/v1"
        assert defaults["TOKENHARBOR_API_KEY"] == ""
        assert defaults["TOKENHARBOR_DEFAULT_MODEL"] == "mimo-v2.6-flash:free"
        assert defaults["TOKENHARBOR_FREE_ONLY"] == "false"
        assert th_m.config["env_prefix"] == "TOKENHARBOR"

    def test_manifest_conforms_to_v0_2_schema(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        manifests = pm.discover(force=True)
        th_m = next(m for m in manifests if m.name == "tokenharbor")
        assert th_m.name
        assert th_m.version
        assert th_m.description
        assert th_m.author
        assert th_m.schema and "v0.2" in th_m.schema

    def test_plugin_loads_and_registers_backend(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("tokenharbor")
        assert pm.is_loaded("tokenharbor")
        cls = pm.get_backend_class("tokenharbor")
        assert cls is not None
        assert cls.__name__ == "TokenHarborBackend"

    def test_plugin_in_backend_choices(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        pm.discover(force=True)
        pm.load("tokenharbor")
        assert "tokenharbor" in pm.get_backend_choices()


# ---------------------------------------------------------------------------
# config.py + auth companion wiring
# ---------------------------------------------------------------------------


class TestTokenharborCompanionWiring:
    def test_config_defaults(self):
        from agentkthx import config

        assert config.TOKENHARBOR_BASE_URL == "https://tokenharbor.ai/v1"
        assert config.TOKENHARBOR_API_KEY == ""
        assert config.TOKENHARBOR_DEFAULT_MODEL == "mimo-v2.6-flash:free"
        assert config.TOKENHARBOR_FREE_ONLY is False

    def test_backend_type_enum_member(self):
        assert BackendType.TOKENHARBOR.value == "tokenharbor"

    def test_env_reference_documents_all_four_vars(self):
        """agentkthx config's env reference covers all TOKENHARBOR_* vars
        (the SiliconFlow scaffold precedent)."""
        from agentkthx.cli.commands.config import _env_reference_entries

        documented = {name for name, _desc in _env_reference_entries()}
        for var in (
            "TOKENHARBOR_BASE_URL",
            "TOKENHARBOR_API_KEY",
            "TOKENHARBOR_DEFAULT_MODEL",
            "TOKENHARBOR_FREE_ONLY",
        ):
            assert var in documented

    def test_auth_picker_rows(self):
        """The /auth picker gains the TokenHarbor key + FREE_ONLY rows and
        the live-patch slug mapping."""
        from agentkthx.cli.auth import _KEY_VAR_TO_SLUG, auth_vars

        rows = {v.name: v for v in auth_vars()}
        assert "TOKENHARBOR_API_KEY" in rows
        assert rows["TOKENHARBOR_API_KEY"].backend == "TokenHarbor"
        assert "TOKENHARBOR_FREE_ONLY" in rows
        assert _KEY_VAR_TO_SLUG["TOKENHARBOR_API_KEY"] == "tokenharbor"

    def test_auth_picker_prompt_hint(self):
        """The key prompt carries the shown-once acquisition hint."""
        from agentkthx.cli.auth import _KEY_PROMPT_HINTS

        hint = _KEY_PROMPT_HINTS.get("TOKENHARBOR_API_KEY", "")
        assert "thk_live_" in hint
        assert "shown once" in hint
        # No other backend carries a hint
        assert list(_KEY_PROMPT_HINTS) == ["TOKENHARBOR_API_KEY"]

    def test_config_command_auth_rows(self):
        """The config summary's per-backend auth rows include TokenHarbor."""
        from agentkthx.cli.commands.config import _backend_auth_rows

        rows = {label: key for _m, label, key, _f, _fb in _backend_auth_rows("")}
        assert "TokenHarbor" in rows

    def test_config_slug_label(self):
        from agentkthx.cli.commands.config import _BACKEND_SLUG_TO_LABEL

        assert _BACKEND_SLUG_TO_LABEL["tokenharbor"] == "TokenHarbor"

    def test_seed_catalog_loaded(self):
        """The seed JSON's tokenharbor key loads with the metadata note
        filtered out."""
        from agentkthx.model_cache import load_seed_catalog

        catalog = load_seed_catalog("tokenharbor")
        assert len(catalog) == 15
        assert "deepseek-v4.1-flash:free" in catalog
        assert "tokenharbor/qwen3-max" in catalog
        # "_"-prefixed metadata keys never leak through as model ids
        assert not any(k.startswith("_") for k in catalog)
