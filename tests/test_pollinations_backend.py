"""
Tests for PollinationsBackend — manifest v0.2 compliance, catalog
integrity, the keyless-init contract (the ONLY backend that runs with
no API key), model-id alias resolution, request-body Pollinations
deltas (safe flag), Pollinations error-envelope parsing with 402
budget-exhausted mapping, Retry-After capping (ROB-16), health-aware
fallback ordering, the SSE streaming path, and the plugin
register/unregister smoke path.

Live API tests (gated on POLLINATIONS_API_KEY) live at the bottom of
the file. They are skipped automatically when the env var is missing —
CI runs without keys, the unit tests still pass. (The public /v1/models
catalog needs no key, but live tests are kept under the same gate so
CI never depends on external reachability.)

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make the AgentKthx package importable when running from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentkthx.plugins.pollinations.pollinations import (
    PollinationsBackend,
    POLLINATIONS_MODELS,
    _expand_safe_flag,
    _pollen_to_per_million,
    _card_is_free,
)
from agentkthx.plugins.pollinations import register, unregister
from agentkthx.core.types import BackendType, ApiMode
from agentkthx.core.models import Tool, ToolParam


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(name: str = "shell") -> Tool:
    return Tool(
        name=name,
        description="Run a shell command",
        params=[ToolParam(name="command", type="string", description="cmd")],
    )


def _make_backend(api_key: str = "sk_" + "x" * 32) -> PollinationsBackend:
    """Construct a PollinationsBackend with a fake key (skips env lookup)."""
    return PollinationsBackend(api_key=api_key)


def _make_keyless_backend() -> PollinationsBackend:
    """Construct a keyless backend (anonymous tier) — env-safe."""
    # Ensure the env var doesn't leak a real key into the keyless test.
    saved = os.environ.pop("POLLINATIONS_API_KEY", None)
    try:
        return PollinationsBackend(api_key="")
    finally:
        if saved is not None:
            os.environ["POLLINATIONS_API_KEY"] = saved


def _fake_cards() -> dict[str, dict]:
    """Three live-shaped model cards for deterministic fallback tests."""
    return {
        "openai/gpt-5.4-nano": {
            "id": "openai/gpt-5.4-nano",
            "category": "text",
            "community": False,
            "tools": True,
            "context_length": 400000,
            "aliases": ["gpt-5.4-nano", "openai"],
            "pricing": {
                "promptTextTokens": "0.00000015",
                "completionTextTokens": "0.0000009375",
            },
            "health": {"status": "healthy", "success_rate": 99.95, "requests": 75499},
        },
        "z-ai/glm-5.3-flash": {
            "id": "z-ai/glm-5.3-flash",
            "category": "text",
            "community": False,
            "tools": True,
            "context_length": 131072,
            "aliases": ["glm"],
            "pricing": {
                "promptTextTokens": "0.0000001",
                "completionTextTokens": "0.0000004",
            },
            "health": {"status": "healthy", "success_rate": 99.99, "requests": 12000},
        },
        "community/someone/free-model": {
            "id": "community/someone/free-model",
            "category": "text",
            "community": True,
            "tools": True,
            "context_length": 32768,
            "aliases": ["free-model"],
            "pricing": {
                "promptTextTokens": "0",
                "completionTextTokens": "0",
            },
            "health": {"status": "healthy", "success_rate": 100.0, "requests": 10},
        },
        "black-forest-labs/flux.1-schnell": {
            # image model — must be EXCLUDED from the chat backend list
            "id": "black-forest-labs/flux.1-schnell",
            "category": "image",
            "community": False,
            "tools": False,
            "context_length": 0,
            "aliases": ["flux"],
            "pricing": {"promptTextTokens": "1", "completionTextTokens": "0"},
            "health": {"status": "healthy", "success_rate": 99.0, "requests": 500},
        },
    }


def _http_error(status: int, body: bytes, headers: dict | None = None) -> urllib.error.HTTPError:
    """Build an HTTPError with a JSON body and optional headers."""
    err = urllib.error.HTTPError(
        url="https://gen.pollinations.ai/v1/chat/completions",
        code=status,
        msg="Error",
        hdrs=MagicMock(),
        fp=io.BytesIO(body),
    )
    err.headers = headers or {}
    return err


def _ok_response(payload: dict) -> MagicMock:
    """Build a mock urlopen response returning JSON."""
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
    return resp


# ---------------------------------------------------------------------------
# Manifest compliance (v0.2 form)
# ---------------------------------------------------------------------------

class TestManifestCompliance(unittest.TestCase):
    """The plugin.json must parse as a v0.2 manifest."""

    def setUp(self):
        self.plugin_dir = (
            Path(__file__).resolve().parents[1]
            / "agentkthx"
            / "plugins"
            / "pollinations"
        )
        self.manifest_path = self.plugin_dir / "plugin.json"
        self.manifest = json.loads(
            self.manifest_path.read_text(encoding="utf-8")
        )

    def test_manifest_has_v02_schema(self):
        """The $schema field must point at the v0.2 schema."""
        assert self.manifest["$schema"] == (
            "https://raw.githubusercontent.com/VTSTech/AgentKthx/"
            "main/schemas/v0.2/plugin.schema.json"
        )

    def test_manifest_name_matches_directory(self):
        """Plugin name MUST match the plugin directory name (spec)."""
        assert self.manifest["name"] == self.plugin_dir.name

    def test_manifest_extension_namespace_is_correct(self):
        """Extension data must live under org.vts-tech.agentkthx."""
        assert "org.vts-tech.agentkthx" in self.manifest["extensions"]

    def test_manifest_provides_backend(self):
        """Manifest declares the 'pollinations' backend."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        assert "pollinations" in ext["provides"]["backends"]
        assert (
            ext["provides"]["backends"]["pollinations"]
            == "pollinations.PollinationsBackend"
        )

    def test_manifest_does_not_use_legacy_top_level_fields(self):
        """v0.2 form: type/entrypoint/provides/etc. live under extensions."""
        legacy_fields = {
            "display_name", "type", "entrypoint", "depends",
            "optional_depends", "config", "provides", "compatibility",
        }
        used_legacy = legacy_fields & set(self.manifest.keys())
        assert not used_legacy, (
            f"manifest still uses legacy top-level fields: {used_legacy}"
        )

    def test_manifest_does_not_use_agentnova_compat_key(self):
        """Compatibility block must use 'agentkthx', not the legacy
        'agentnova' alias."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        compat = ext.get("compatibility", {})
        assert "agentnova" not in compat
        assert "agentkthx" in compat

    def test_manifest_includes_poll_alias_in_cli_flags(self):
        """CLI --backend should accept both 'pollinations' and 'poll'."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        flags = ext["provides"].get("cli_flags", {})
        assert "pollinations" in flags.get("--backend", [])
        assert "poll" in flags.get("--backend", [])

    def test_manifest_config_defaults_cover_env_vars(self):
        """Config defaults must mirror the config.py env var names."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        defaults = ext["config"]["defaults"]
        assert defaults["POLLINATIONS_BASE_URL"] == "https://gen.pollinations.ai/v1"
        assert defaults["POLLINATIONS_DEFAULT_MODEL"] == "openai/gpt-5.4-nano"
        assert defaults["POLLINATIONS_API_KEY"] == ""


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------

class TestCatalog(unittest.TestCase):
    """The POLLINATIONS_MODELS catalog must satisfy CloudBackend's contract."""

    def test_catalog_has_required_default_models(self):
        """Default/fallback pair plus representative families must be present."""
        required = {
            "openai/gpt-5.4-nano",      # platform default
            "z-ai/glm-5.3-flash",       # fallback chain anchor
            "z-ai/glm-5.3-flashx",      # 1M-context tier
        }
        assert required <= set(POLLINATIONS_MODELS.keys()), (
            f"missing required models: {required - set(POLLINATIONS_MODELS.keys())}"
        )

    def test_every_catalog_entry_has_required_fields(self):
        """CloudBackend requires context_length, default_max_tokens,
        default_temperature, pricing on every entry."""
        for name, meta in POLLINATIONS_MODELS.items():
            assert "context_length" in meta, f"{name} missing context_length"
            assert "default_max_tokens" in meta, f"{name} missing default_max_tokens"
            assert "default_temperature" in meta, f"{name} missing default_temperature"
            assert "pricing" in meta, f"{name} missing pricing"
            assert isinstance(meta["context_length"], int) and meta["context_length"] > 0
            assert isinstance(meta["default_max_tokens"], int) and meta["default_max_tokens"] > 0

    def test_catalog_keys_are_provider_slash_model(self):
        """Every catalog key must be in provider/model form (the
        Pollinations canonical id pattern)."""
        for name in POLLINATIONS_MODELS:
            assert "/" in name, f"catalog key {name!r} lacks provider prefix"

    def test_default_model_context_is_card_verified(self):
        """gpt-5.4-nano carries the card-verified 400K context."""
        assert POLLINATIONS_MODELS["openai/gpt-5.4-nano"]["context_length"] == 400_000

    def test_pollen_conversion(self):
        """Per-token card prices convert to per-1M (0.00000015 -> 0.15)."""
        assert _pollen_to_per_million("0.00000015") == 0.15
        assert _pollen_to_per_million("0") == 0.0
        assert _pollen_to_per_million(None) is None
        assert _pollen_to_per_million("junk") is None


# ---------------------------------------------------------------------------
# Keyless init — the contract unique to Pollinations
# ---------------------------------------------------------------------------

class TestKeylessInit(unittest.TestCase):
    """Pollinations is the ONLY backend that constructs with no key."""

    def test_keyless_construction_does_not_raise(self):
        """CloudBackend hard-requires a key; Pollinations must not."""
        backend = _make_keyless_backend()
        assert backend.api_key == ""

    def test_keyless_is_running_true(self):
        """Anonymous surface is always 'running' (no key needed)."""
        backend = _make_keyless_backend()
        assert backend.is_running() is True

    def test_keyless_auth_headers_omit_authorization(self):
        """No key → no Authorization header (anonymous surface)."""
        backend = _make_keyless_backend()
        headers = backend._get_auth_headers()
        assert "Authorization" not in headers
        assert headers.get("Content-Type") == "application/json"

    def test_keyless_get_balance_raises_valueerror(self):
        """Balance inspection requires a key — anonymous has no pollen."""
        backend = _make_keyless_backend()
        with pytest.raises(ValueError, match="anonymous"):
            backend.get_balance()

    def test_bad_key_prefix_rejected(self):
        """A key without sk_/pk_ prefix is rejected with guidance."""
        with pytest.raises(ValueError, match="sk_"):
            PollinationsBackend(api_key="not-a-pollinations-key-12345")

    def test_short_key_rejected(self):
        """A too-short key is rejected (ROB-21 pattern)."""
        with pytest.raises(ValueError):
            PollinationsBackend(api_key="sk_")

    def test_pk_legacy_key_accepted(self):
        """Legacy raw pk_ keys are tolerated (per the reference)."""
        backend = PollinationsBackend(api_key="pk_" + "y" * 20)
        assert backend.api_key.startswith("pk_")


# ---------------------------------------------------------------------------
# URLs & identity
# ---------------------------------------------------------------------------

class TestURLsAndIdentity(unittest.TestCase):

    def test_backend_type(self):
        assert _make_backend().backend_type == BackendType.POLLINATIONS

    def test_chat_completions_url(self):
        assert _make_backend()._get_chat_completions_url() == (
            "https://gen.pollinations.ai/v1/chat/completions"
        )

    def test_models_url_is_public(self):
        assert _make_backend()._get_models_url() == (
            "https://gen.pollinations.ai/v1/models"
        )

    def test_balance_url(self):
        assert _make_backend()._get_balance_url() == (
            "https://gen.pollinations.ai/v1/account/balance"
        )

    def test_base_url_env_override(self):
        with patch.dict(os.environ, {"POLLINATIONS_BASE_URL": "https://proxy.example/v1"}):
            backend = PollinationsBackend(api_key="sk_" + "z" * 32)
            assert backend.base_url == "https://proxy.example/v1"
            assert backend._get_chat_completions_url() == (
                "https://proxy.example/v1/chat/completions"
            )

    def test_is_cloud_attribute(self):
        assert PollinationsBackend.is_cloud is True


# ---------------------------------------------------------------------------
# Model-id normalization & alias resolution
# ---------------------------------------------------------------------------

class TestNormalizeModelId(unittest.TestCase):

    def test_provider_slash_model_passthrough(self):
        """Canonical ids pass through unchanged."""
        backend = _make_backend()
        assert backend.normalize_model_id("openai/gpt-5.4-nano") == "openai/gpt-5.4-nano"
        assert backend.normalize_model_id("community/owner/model") == "community/owner/model"

    def test_static_alias_resolution(self):
        """Doc-verified static seeds resolve without any catalog fetch."""
        backend = _make_backend()
        assert backend.normalize_model_id("openai") == "openai/gpt-5.4-nano"
        assert backend.normalize_model_id("gpt-5.4-nano") == "openai/gpt-5.4-nano"
        assert backend.normalize_model_id("gpt-oss") == "openai/gpt-oss-20b"

    def test_live_alias_resolution(self):
        """Aliases[] from fetched cards resolve (simulated live catalog)."""
        backend = _make_backend()
        backend._model_cards = _fake_cards()
        # simulate what list_models() builds
        merged = {"glm": "z-ai/glm-5.3-flash", "free-model": "community/someone/free-model"}
        backend._alias_map.update(merged)
        assert backend.normalize_model_id("glm") == "z-ai/glm-5.3-flash"

    def test_unknown_bare_alias_passthrough(self):
        """Unknown bare names pass through — the server 404s cleanly."""
        backend = _make_backend()
        assert backend.normalize_model_id("totally-unknown") == "totally-unknown"

    def test_empty_model_passthrough(self):
        backend = _make_backend()
        assert backend.normalize_model_id("") == ""


# ---------------------------------------------------------------------------
# Request-body construction (Pollinations deltas)
# ---------------------------------------------------------------------------

class TestBodyConstruction(unittest.TestCase):

    def test_model_id_normalized_in_body(self):
        """Bare aliases are normalized to provider/model in the body."""
        backend = _make_backend()
        body = backend._build_pollinations_body(
            model="openai",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=1024,
        )
        assert body["model"] == "openai/gpt-5.4-nano"

    def test_safe_flag_injected_when_env_set(self):
        """POLLINATIONS_SAFE=true expands to privacy,secrets in the body."""
        backend = _make_backend()
        with patch.dict(os.environ, {"POLLINATIONS_SAFE": "true"}):
            body = backend._build_pollinations_body(
                model="openai/gpt-5.4-nano",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=1024,
            )
        assert body["safe"] == "privacy,secrets"

    def test_safe_flag_omitted_when_env_empty(self):
        """No safe param when the env var is unset (platform default OFF)."""
        backend = _make_backend()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POLLINATIONS_SAFE", None)
            body = backend._build_pollinations_body(
                model="openai/gpt-5.4-nano",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=1024,
            )
        assert "safe" not in body

    def test_stream_options_include_usage(self):
        """stream=True must set stream_options.include_usage (PERF-02)."""
        backend = _make_backend()
        body = backend._build_pollinations_body(
            model="openai/gpt-5.4-nano",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=1024,
            stream=True,
        )
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}

    def test_tools_serialized_to_openai_schema(self):
        backend = _make_backend()
        body = backend._build_pollinations_body(
            model="openai/gpt-5.4-nano",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=1024,
        )
        assert isinstance(body["tools"], list) and len(body["tools"]) == 1

    def test_seed_passes_through_natively(self):
        """seed is NOT aliased to random_seed (that's the Mistral delta)."""
        backend = _make_backend()
        body = backend._build_pollinations_body(
            model="openai/gpt-5.4-nano",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=1024,
            seed=42,
        )
        assert body["seed"] == 42
        assert "random_seed" not in body

    def test_reasoning_effort_passthrough(self):
        backend = _make_backend()
        body = backend._build_pollinations_body(
            model="openai/gpt-5.4-nano",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=1024,
            reasoning_effort="high",
        )
        assert body["reasoning_effort"] == "high"


# ---------------------------------------------------------------------------
# Error envelope parsing
# ---------------------------------------------------------------------------

class TestErrorEnvelope(unittest.TestCase):

    def test_pollinations_envelope_parsed(self):
        """The canonical envelope: code + message + requestId extracted."""
        body = json.dumps({
            "status": 402,
            "success": False,
            "error": {
                "code": "PAYMENT_REQUIRED",
                "message": "Insufficient pollen balance",
                "timestamp": "2026-01-01T00:00:00.000Z",
                "requestId": "req_abc123",
            },
        }).encode()
        msg, code, rid = PollinationsBackend._parse_error_envelope(body, 402)
        assert msg == "Insufficient pollen balance"
        assert code == "PAYMENT_REQUIRED"
        assert rid == "req_abc123"

    def test_openai_wrapper_tolerated(self):
        body = json.dumps({"error": {"message": "boom", "code": "x"}}).encode()
        msg, code, rid = PollinationsBackend._parse_error_envelope(body, 400)
        assert msg == "boom"
        assert code == "x"

    def test_flat_message_tolerated(self):
        body = json.dumps({"message": "flat message"}).encode()
        msg, _, _ = PollinationsBackend._parse_error_envelope(body, 400)
        assert msg == "flat message"

    def test_non_json_body_falls_back(self):
        msg, code, rid = PollinationsBackend._parse_error_envelope(
            "<html>oops</html>", 502
        )
        assert "oops" in msg
        assert code is None and rid is None

    def test_empty_body_falls_back_to_status(self):
        msg, _, _ = PollinationsBackend._parse_error_envelope(b"", 429)
        assert msg == "HTTP 429"


# ---------------------------------------------------------------------------
# Error mapping via _make_api_request (mocked urlopen)
# ---------------------------------------------------------------------------

class TestErrorMapping(unittest.TestCase):

    def test_402_budget_exhausted_never_retried(self):
        """THE headline: 402 maps to a budget-exhausted RuntimeError with
        requestId surfaced, and is NOT retried (single HTTP call)."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01
        backend._BACKOFF_CAP = 0.05

        err = _http_error(402, json.dumps({
            "status": 402, "success": False,
            "error": {"code": "PAYMENT_REQUIRED",
                      "message": "Insufficient pollen balance",
                      "requestId": "req_402xyz"},
        }).encode())

        with patch("urllib.request.urlopen", side_effect=err) as m:
            with pytest.raises(RuntimeError) as exc_info:
                backend._make_api_request(
                    {"model": "openai/gpt-5.4-nano", "messages": [], "max_tokens": 100}
                )

        assert m.call_count == 1, "402 must NOT be retried"
        assert "budget" in str(exc_info.value).lower()
        assert "req_402xyz" in str(exc_info.value)
        assert "balance" in str(exc_info.value).lower()

    def test_401_auth_error_mentions_env_var(self):
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01
        err = _http_error(401, json.dumps({
            "error": {"code": "UNAUTHORIZED", "message": "invalid key",
                      "requestId": "req_1"},
        }).encode())
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="POLLINATIONS_API_KEY"):
                backend._make_api_request(
                    {"model": "openai/gpt-5.4-nano", "messages": [], "max_tokens": 100}
                )

    def test_404_model_not_found_mentions_catalog(self):
        backend = _make_backend()
        err = _http_error(404, json.dumps({
            "error": {"code": "NOT_FOUND", "message": "unknown model",
                      "requestId": "req_4"},
        }).encode())
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="/v1/models"):
                backend._make_api_request(
                    {"model": "nope/missing", "messages": [], "max_tokens": 100}
                )

    def test_422_content_policy_mentions_rephrase(self):
        backend = _make_backend()
        err = _http_error(422, json.dumps({
            "error": {"code": "content_policy_violation",
                      "message": "blocked", "requestId": "req_9"},
        }).encode())
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="content_policy_violation"):
                backend._make_api_request(
                    {"model": "openai/gpt-5.4-nano", "messages": [], "max_tokens": 100}
                )

    def test_429_retries_then_succeeds(self):
        """429 honors Retry-After, retries, then succeeds."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01
        backend._BACKOFF_CAP = 0.05

        err = _http_error(429, json.dumps({
            "error": {"code": "RATE_LIMITED", "message": "slow down",
                      "requestId": "req_429"},
        }).encode(), headers={"Retry-After": "0"})

        ok = _ok_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

        with patch("urllib.request.urlopen", side_effect=[err, ok]) as m:
            result = backend._make_api_request(
                {"model": "openai/gpt-5.4-nano", "messages": [], "max_tokens": 100}
            )
        assert m.call_count == 2
        assert result["content"] == "ok"

    def test_retry_after_is_capped(self):
        """ROB-16: Retry-After: 3600 must sleep at most _BACKOFF_CAP (60s)."""
        backend = _make_backend()
        slept_values: list[float] = []

        def fake_sleep(seconds):
            slept_values.append(seconds)

        err = _http_error(429, b'{"error": {"message": "slow"}}',
                          headers={"Retry-After": "3600"})
        ok = _ok_response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

        with patch("urllib.request.urlopen", side_effect=[err, ok]):
            with patch("agentkthx.plugins.pollinations.pollinations.time.sleep",
                       side_effect=fake_sleep):
                backend._make_api_request(
                    {"model": "openai/gpt-5.4-nano", "messages": [], "max_tokens": 100}
                )

        assert slept_values, "expected at least one sleep call"
        for s in slept_values:
            assert s <= backend._BACKOFF_CAP + 1e-9, (
                f"Retry-After exceeded cap: slept {s}s"
            )

    def test_400_context_length_recovery_retries(self):
        """ARCH-03: a context-length 400 reduces max_tokens and retries."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01

        ctx_msg = json.dumps({
            "error": {"code": "BAD_REQUEST",
                      "message": "maximum context length is 8192 tokens "
                                 "(8541 of text input) — reduce max_tokens"},
        }).encode()
        err = _http_error(400, ctx_msg)

        captured_bodies: list[dict] = []

        ok = _ok_response({
            "choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

        def capturing_urlopen(req, timeout=None):
            if len(captured_bodies) == 0:
                captured_bodies.append(json.loads(req.data.decode()))
                raise err
            captured_bodies.append(json.loads(req.data.decode()))
            return ok

        with patch("urllib.request.urlopen", side_effect=capturing_urlopen):
            result = backend._make_api_request(
                {"model": "openai/gpt-5.4-nano", "messages": [],
                 "max_tokens": 8192}
            )

        assert result["content"] == "recovered"
        assert captured_bodies[1]["max_tokens"] < captured_bodies[0]["max_tokens"]


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

class TestParseResponse(unittest.TestCase):

    def test_success_shape(self):
        raw = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        parsed = PollinationsBackend._parse_pollinations_response(raw)
        assert parsed["content"] == "Hello!"
        assert parsed["tool_calls"] == []
        assert parsed["finish_reason"] == "stop"
        assert parsed["usage"]["total_tokens"] == 15

    def test_tool_calls_parsed(self):
        raw = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "get_weather",
                                     "arguments": "{\"city\": \"Toronto\"}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
        }
        parsed = PollinationsBackend._parse_pollinations_response(raw)
        assert parsed["finish_reason"] == "tool_calls"
        assert parsed["tool_calls"][0]["name"] == "get_weather"
        assert parsed["tool_calls"][0]["arguments"] == {"city": "Toronto"}

    def test_malformed_tool_arguments_tolerated(self):
        raw = {
            "choices": [{
                "message": {"tool_calls": [{
                    "id": "call_x",
                    "function": {"name": "t", "arguments": "{not json"},
                }]},
            }],
            "usage": None,
        }
        parsed = PollinationsBackend._parse_pollinations_response(raw)
        assert parsed["tool_calls"][0]["arguments"] == {"_raw_arguments": "{not json"}

    def test_usage_null_normalized_to_zeros(self):
        """Media-model chat responses carry usage: null — must not crash."""
        raw = {
            "choices": [{
                "message": {"content": "![image](https://media.pollinations.ai/x)"},
                "finish_reason": "stop",
            }],
            "usage": None,
        }
        parsed = PollinationsBackend._parse_pollinations_response(raw)
        assert parsed["usage"]["total_tokens"] == 0

    def test_provider_error_on_http_200_raises(self):
        raw = {"error": {"message": "upstream failed", "code": 502}}
        with pytest.raises(RuntimeError, match="upstream failed"):
            PollinationsBackend._parse_pollinations_response(raw)

    def test_no_choices_raises(self):
        with pytest.raises(RuntimeError, match="no choices"):
            PollinationsBackend._parse_pollinations_response({})


# ---------------------------------------------------------------------------
# Health-aware fallback ordering (Pollinations-exclusive)
# ---------------------------------------------------------------------------

class TestHealthyFallbacks(unittest.TestCase):

    def test_orders_by_success_rate_desc_price_asc(self):
        """healthy_fallbacks sorts by success_rate descending, price ascending."""
        backend = _make_backend()
        cards = _fake_cards()
        # community + toolless cards must be excluded; only the two text
        # non-community tool-capable cards remain. glm has 99.99 > nano's
        # 99.95 → glm first.
        backend._model_cards = cards
        chain = backend.healthy_fallbacks(limit=5)
        assert chain[0] == "z-ai/glm-5.3-flash"
        assert chain[1] == "openai/gpt-5.4-nano"
        assert "community/someone/free-model" not in chain
        assert "black-forest-labs/flux.1-schnell" not in chain

    def test_limit_respected(self):
        backend = _make_backend()
        backend._model_cards = _fake_cards()
        assert len(backend.healthy_fallbacks(limit=1)) == 1

    def test_offline_falls_back_to_static_pair(self):
        """No live cards (offline) → default + fallback model pair."""
        backend = _make_backend()
        # ensure no network fetch happens: cards empty AND fetch mocked empty
        with patch.object(backend, "_fetch_model_cards", return_value={}):
            chain = backend.healthy_fallbacks()
        assert chain[0] == "openai/gpt-5.4-nano"
        assert chain[1] == "z-ai/glm-5.3-flash"


# ---------------------------------------------------------------------------
# list_models with injected live cards
# ---------------------------------------------------------------------------

class TestListModels(unittest.TestCase):

    def test_live_cards_surface_text_category_only(self):
        """Image-category cards are excluded from the chat backend list."""
        backend = _make_backend()
        with patch.object(backend, "_fetch_model_cards", return_value=_fake_cards()):
            models = backend.list_models()
        names = [m["name"] for m in models]
        assert "openai/gpt-5.4-nano" in names
        assert "black-forest-labs/flux.1-schnell" not in names

    def test_alias_map_built_from_live_cards(self):
        """aliases[] from live cards populate the alias map."""
        backend = _make_backend()
        with patch.object(backend, "_fetch_model_cards", return_value=_fake_cards()):
            backend.list_models()
        assert backend._alias_map.get("glm") == "z-ai/glm-5.3-flash"
        assert backend._alias_map.get("flux") == "black-forest-labs/flux.1-schnell"

    def test_card_context_length_surfaced(self):
        backend = _make_backend()
        with patch.object(backend, "_fetch_model_cards", return_value=_fake_cards()):
            models = backend.list_models()
        by_name = {m["name"]: m for m in models}
        assert by_name["openai/gpt-5.4-nano"]["details"]["context_length"] == 400000
        assert by_name["openai/gpt-5.4-nano"]["details"]["health"]["success_rate"] == 99.95

    def test_free_community_model_flagged(self):
        backend = _make_backend()
        with patch.object(backend, "_fetch_model_cards", return_value=_fake_cards()):
            models = backend.list_models()
        by_name = {m["name"]: m for m in models}
        assert by_name["community/someone/free-model"]["details"]["free_tier"] is True
        assert by_name["openai/gpt-5.4-nano"]["details"]["free_tier"] is False

    def test_offline_falls_back_to_static_catalog(self):
        """Catalog unreachable → static POLLINATIONS_MODELS still listed."""
        backend = _make_backend()
        with patch.object(backend, "_fetch_model_cards", return_value={}):
            models = backend.list_models()
        names = {m["name"] for m in models}
        assert "openai/gpt-5.4-nano" in names
        assert "z-ai/glm-5.3-flash" in names
        assert len(models) == len(POLLINATIONS_MODELS)

    def test_free_only_filter(self):
        """POLLINATIONS_FREE_ONLY=true keeps only zero-priced models."""
        backend = _make_backend()
        with patch.dict(os.environ, {"POLLINATIONS_FREE_ONLY": "true"}):
            with patch.object(backend, "_fetch_model_cards", return_value=_fake_cards()):
                models = backend.list_models()
        names = [m["name"] for m in models]
        assert "community/someone/free-model" in names
        assert "openai/gpt-5.4-nano" not in names


# ---------------------------------------------------------------------------
# Model defaults & info
# ---------------------------------------------------------------------------

class TestModelDefaults(unittest.TestCase):

    def test_catalog_context_used_for_nano(self):
        d = _make_backend()._get_model_defaults("openai/gpt-5.4-nano")
        assert d["context_length"] == 400_000
        assert d["temperature"] == 0.7

    def test_alias_resolved_in_defaults(self):
        d = _make_backend()._get_model_defaults("openai")
        assert d["context_length"] == 400_000

    def test_live_card_context_wins(self):
        backend = _make_backend()
        backend._model_cards = {
            "openai/gpt-5.4-nano": {"context_length": 999_999, "category": "text"}
        }
        d = backend._get_model_defaults("openai/gpt-5.4-nano")
        assert d["context_length"] == 999_999

    def test_unknown_model_safe_fallback(self):
        d = _make_backend()._get_model_defaults("unknown vendor/model-x")
        assert d["context_length"] == 128_000

    def test_get_model_info_never_none(self):
        """Unknown models get a safe default entry (ZAI/Mistral pattern)."""
        info = _make_backend().get_model_info("weird/new-model")
        assert info is not None
        assert info["details"]["context_length"] == 128_000


# ---------------------------------------------------------------------------
# Streaming (SSE path through the inherited base + our wrapper)
# ---------------------------------------------------------------------------

class TestStreaming(unittest.TestCase):

    def test_stream_yields_deltas(self):
        """The full inherited SSE path parses deltas + [DONE]."""
        backend = _make_backend()

        chunks = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b'data: {"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n',
            b'data: [DONE]\n\n',
        ]
        fake = MagicMock()
        fake.__iter__ = MagicMock(return_value=iter(chunks))
        fake.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake):
            out = list(backend.generate_completions_stream(
                model="openai/gpt-5.4-nano",
                messages=[{"role": "user", "content": "hi"}],
            ))

        deltas = [c["delta"] for c in out if c.get("delta")]
        assert deltas == ["Hel", "lo"]
        # usage chunk captured (PERF-02)
        assert any(c.get("_usage") for c in out)

    def test_generate_stream_text_only(self):
        backend = _make_backend()
        chunks = [
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]
        fake = MagicMock()
        fake.__iter__ = MagicMock(return_value=iter(chunks))
        fake.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake):
            out = list(backend.generate_stream(
                model="openai/gpt-5.4-nano",
                messages=[{"role": "user", "content": "hi"}],
            ))
        assert out == ["ok"]

    def test_stream_402_raises_budget_error(self):
        """Streaming 402 surfaces immediately — never retried."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01
        err = _http_error(402, json.dumps({
            "error": {"code": "PAYMENT_REQUIRED",
                      "message": "no pollen", "requestId": "req_s402"},
        }).encode())
        with patch("urllib.request.urlopen", side_effect=err) as m:
            with pytest.raises(RuntimeError, match="req_s402"):
                list(backend._iter_sse_lines(
                    url="https://gen.pollinations.ai/v1/chat/completions",
                    body={"model": "openai/gpt-5.4-nano", "messages": []},
                    headers={"Authorization": "Bearer x"},
                ))
        assert m.call_count == 1


# ---------------------------------------------------------------------------
# Plugin registration smoke (PluginManager)
# ---------------------------------------------------------------------------

class TestPluginRegistration(unittest.TestCase):

    def test_register_and_unregister(self):
        from agentkthx.plugins._loader import PluginManager

        pm = PluginManager()
        register(pm)
        assert pm.get_backend_class("pollinations") is PollinationsBackend
        assert pm.get_backend_class("poll") is PollinationsBackend  # alias

        unregister(pm)
        assert pm.get_backend_class("pollinations") is None
        assert pm.get_backend_class("poll") is None

    def test_discovery_finds_manifest(self):
        """The builtin plugins dir discovery picks up pollinations."""
        from agentkthx.plugins._loader import get_plugin_manager

        pm = get_plugin_manager()
        names = [m.name for m in pm.discover()]
        assert "pollinations" in names


# ---------------------------------------------------------------------------
# Live API tests (gated on POLLINATIONS_API_KEY — auto-skipped in CI)
# ---------------------------------------------------------------------------

LIVE_KEY = os.environ.get("POLLINATIONS_API_KEY", "")

@pytest.mark.skipif(not LIVE_KEY, reason="POLLINATIONS_API_KEY not set")
class TestLiveAPI(unittest.TestCase):
    """Live gateway probes — skipped without a key."""

    def test_live_catalog_reachable(self):
        backend = _make_backend(api_key=LIVE_KEY)
        models = backend.list_models()
        assert len(models) > 10
        assert any(m["name"] == "openai/gpt-5.4-nano" for m in models)

    def test_live_chat_ping(self):
        backend = _make_backend(api_key=LIVE_KEY)
        result = backend.generate(
            model="openai/gpt-5.4-nano",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
        )
        assert result["content"]
        assert result["usage"]["total_tokens"] > 0

    def test_live_balance(self):
        backend = _make_backend(api_key=LIVE_KEY)
        balance = backend.get_balance()
        assert "balance" in balance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

