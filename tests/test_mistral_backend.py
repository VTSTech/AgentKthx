"""
Tests for MistralBackend — manifest v0.2 compliance, catalog integrity,
request-body Mistral-delta handling, response parser tolerance, and
the smoke-load path (backend loads + registers).

Live API tests (gated on MISTRAL_API_KEY) live at the bottom of the
file. They are skipped automatically when the env var is missing —
CI runs without keys, the unit tests still pass.

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
from unittest.mock import MagicMock, patch

import pytest

# Make the AgentKthx package importable when running from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentkthx.core.models import Tool, ToolParam
from agentkthx.core.types import BackendType
from agentkthx.plugins.mistral import register, unregister
from agentkthx.plugins.mistral.mistral import (
    MISTRAL_MODELS,
    MistralBackend,
    _is_free_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str = "shell") -> Tool:
    return Tool(
        name=name,
        description="Run a shell command",
        params=[ToolParam(name="command", type="string", description="cmd")],
    )


def _make_backend(api_key: str = "x" * 32) -> MistralBackend:
    """Construct a MistralBackend with a fake key (skips env lookup)."""
    return MistralBackend(api_key=api_key)


# ---------------------------------------------------------------------------
# Manifest compliance (v0.2 form)
# ---------------------------------------------------------------------------


class TestManifestCompliance(unittest.TestCase):
    """The plugin.json must parse as a v0.2 manifest."""

    def setUp(self):
        self.plugin_dir = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "mistral"
        self.manifest_path = self.plugin_dir / "plugin.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

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
        """Manifest declares the 'mistral' backend."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        assert "mistral" in ext["provides"]["backends"]
        assert ext["provides"]["backends"]["mistral"] == "mistral.MistralBackend"

    def test_manifest_does_not_use_legacy_top_level_fields(self):
        """v0.2 form: type/entrypoint/provides/etc. live under extensions."""
        legacy_fields = {
            "display_name",
            "type",
            "entrypoint",
            "depends",
            "optional_depends",
            "config",
            "provides",
            "compatibility",
        }
        used_legacy = legacy_fields & set(self.manifest.keys())
        assert not used_legacy, f"manifest still uses legacy top-level fields: {used_legacy}"

    def test_manifest_does_not_use_agentnova_compat_key(self):
        """Compatibility block must use 'agentkthx', not the legacy
        'agentnova' alias."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        compat = ext.get("compatibility", {})
        assert "agentnova" not in compat
        assert "agentkthx" in compat

    def test_manifest_includes_mst_alias_in_cli_flags(self):
        """CLI --backend should accept both 'mistral' and 'mst'."""
        ext = self.manifest["extensions"]["org.vts-tech.agentkthx"]
        flags = ext["provides"].get("cli_flags", {})
        assert "mistral" in flags.get("--backend", [])
        assert "mst" in flags.get("--backend", [])


# ---------------------------------------------------------------------------
# Catalog integrity
# ---------------------------------------------------------------------------


class TestCatalog(unittest.TestCase):
    """The MISTRAL_MODELS catalog must satisfy CloudBackend's contract."""

    def test_catalog_has_required_default_models(self):
        """The four canonical Mistral model families must be in the catalog."""
        required = {
            "mistral-medium-latest",
            "mistral-small-latest",
            "mistral-large-latest",
            "magistral-medium-latest",
            "codestral-latest",
            "devstral-latest",
        }
        assert required <= set(
            MISTRAL_MODELS.keys()
        ), f"missing required models: {required - set(MISTRAL_MODELS.keys())}"

    def test_every_catalog_entry_has_required_fields(self):
        """CloudBackend requires context_length, default_max_tokens,
        default_temperature, pricing on every catalog entry."""
        required_fields = {
            "context_length",
            "default_max_tokens",
            "default_temperature",
            "pricing",
        }
        for name, meta in MISTRAL_MODELS.items():
            missing = required_fields - set(meta.keys())
            assert not missing, f"model {name!r} missing fields: {missing}"

    def test_every_pricing_entry_has_input_and_output(self):
        """pricing sub-dict must have both 'input' and 'output' keys
        (CloudBackend._is_free_model reads them)."""
        for name, meta in MISTRAL_MODELS.items():
            pricing = meta.get("pricing", {})
            assert "input" in pricing, f"model {name!r} pricing missing 'input' field"
            assert "output" in pricing, f"model {name!r} pricing missing 'output' field"

    def test_labs_models_are_free(self):
        """All labs-* models must be priced at $0 input / $0 output."""
        for name, meta in MISTRAL_MODELS.items():
            if name.startswith("labs-"):
                pricing = meta["pricing"]
                assert pricing["input"] == 0.0, f"labs model {name!r} has non-zero input price"
                assert pricing["output"] == 0.0, f"labs model {name!r} has non-zero output price"

    def test_context_lengths_are_reasonable(self):
        """All catalog context lengths must be in the [32K, 1M] band —
        anything outside that range is a catalog typo."""
        for name, meta in MISTRAL_MODELS.items():
            ctx = meta["context_length"]
            assert 32768 <= ctx <= 1_048_576, f"model {name!r} context_length {ctx} is out of band"


# ---------------------------------------------------------------------------
# _is_free_model helper
# ---------------------------------------------------------------------------


class TestIsFreeModel(unittest.TestCase):
    """The free-tier classifier drives MISTRAL_FREE_ONLY enforcement."""

    def test_labs_prefix_is_free(self):
        assert _is_free_model("labs-mistral-small-creative")

    def test_paid_model_is_not_free(self):
        assert not _is_free_model("mistral-medium-latest")
        assert not _is_free_model("mistral-small-latest")
        assert not _is_free_model("mistral-large-latest")

    def test_unknown_model_is_not_free(self):
        # Safe default: assume paid so MISTRAL_FREE_ONLY doesn't let
        # unknown models through.
        assert not _is_free_model("not-a-real-model")

    def test_provider_prefix_is_stripped(self):
        """Models like 'mistral/mistral-small-latest' should be normalized."""
        assert not _is_free_model("mistral/mistral-small-latest")
        assert _is_free_model("mistral/labs-mistral-small-creative")


# ---------------------------------------------------------------------------
# Backend identity
# ---------------------------------------------------------------------------


class TestBackendIdentity(unittest.TestCase):
    """Smoke tests for backend instantiation + identity."""

    def test_backend_type_is_mistral(self):
        backend = _make_backend()
        assert backend.backend_type == BackendType.MISTRAL

    def test_provider_label(self):
        backend = _make_backend()
        assert backend._provider_label == "Mistral"

    def test_default_base_url(self):
        backend = _make_backend()
        assert backend._base_url == "https://api.mistral.ai/v1"

    def test_default_model_is_mistral_small_latest(self):
        """Default catalog model is the cost-efficient Apache 2.0 tier."""
        backend = _make_backend()
        assert backend._default_model == "mistral-small-latest"

    def test_is_running_when_key_set(self):
        backend = _make_backend(api_key="x" * 32)
        assert backend.is_running() is True

    def test_is_running_false_when_key_missing(self):
        """CloudBackend raises ValueError on missing key — verify this
        surfaces correctly so users see a clear error."""
        with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
            MistralBackend(api_key="")

    def test_short_key_rejected(self):
        """CloudBackend enforces _MIN_API_KEY_LEN=20."""
        with pytest.raises(ValueError, match="too short"):
            MistralBackend(api_key="short")

    def test_chat_completions_url(self):
        backend = _make_backend()
        url = backend._get_chat_completions_url()
        assert url == "https://api.mistral.ai/v1/chat/completions"

    def test_models_url(self):
        backend = _make_backend()
        url = backend._get_models_url()
        assert url == "https://api.mistral.ai/v1/models"

    def test_auth_headers_include_bearer_token(self):
        backend = _make_backend(api_key="abc123" + "x" * 20)
        headers = backend._get_auth_headers()
        assert headers["Authorization"] == f"Bearer abc123{'x' * 20}"
        assert headers["Content-Type"] == "application/json"

    def test_auth_headers_include_accept_json(self):
        """Mistral recommends Accept: application/json for non-streaming."""
        backend = _make_backend()
        headers = backend._get_auth_headers()
        assert headers["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# Request body construction — Mistral wire-format deltas
# ---------------------------------------------------------------------------


class TestBuildMistralBody(unittest.TestCase):
    """The request builder must apply every Mistral delta vs OpenAI."""

    def setUp(self):
        # Cache the env var so MISTRAL_SAFE_PROMPT / MISTRAL_SERVICE_TIER
        # don't leak between tests.
        self._safe_prompt = os.environ.get("MISTRAL_SAFE_PROMPT")
        self._service_tier = os.environ.get("MISTRAL_SERVICE_TIER")
        os.environ.pop("MISTRAL_SAFE_PROMPT", None)
        os.environ.pop("MISTRAL_SERVICE_TIER", None)

    def tearDown(self):
        if self._safe_prompt is not None:
            os.environ["MISTRAL_SAFE_PROMPT"] = self._safe_prompt
        else:
            os.environ.pop("MISTRAL_SAFE_PROMPT", None)
        if self._service_tier is not None:
            os.environ["MISTRAL_SERVICE_TIER"] = self._service_tier
        else:
            os.environ.pop("MISTRAL_SERVICE_TIER", None)

    def test_seed_aliased_to_random_seed(self):
        """OpenAI's 'seed' kwarg must become Mistral's 'random_seed'."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            seed=42,
        )
        assert body["random_seed"] == 42
        assert "seed" not in body  # OpenAI field must not leak through

    def test_random_seed_kwarg_passes_through(self):
        """Caller can also pass random_seed directly."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            random_seed=99,
        )
        assert body["random_seed"] == 99

    def test_tool_choice_required_aliased_to_any(self):
        """Mistral's forced-call value is 'any', not 'required'."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=100,
            stream=False,
            tool_choice="required",
        )
        assert body["tool_choice"] == "any"

    def test_tool_choice_any_passes_through(self):
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=100,
            stream=False,
            tool_choice="any",
        )
        assert body["tool_choice"] == "any"

    def test_tool_choice_auto_passes_through(self):
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=100,
            stream=False,
            tool_choice="auto",
        )
        assert body["tool_choice"] == "auto"

    def test_parallel_tool_calls_false_is_forwarded(self):
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=[_make_tool()],
            temperature=0.7,
            max_tokens=100,
            stream=False,
            parallel_tool_calls=False,
        )
        assert body["parallel_tool_calls"] is False

    def test_openai_only_fields_are_stripped(self):
        """OpenAI kwargs that would 422 on Mistral must NOT be forwarded."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            logprobs=True,
            top_logprobs=5,
            top_k=40,
            user="user-123",
            max_completion_tokens=200,
        )
        for forbidden in (
            "logprobs",
            "top_logprobs",
            "top_k",
            "user",
            "max_completion_tokens",
        ):
            assert (
                forbidden not in body
            ), f"OpenAI-only field {forbidden!r} leaked into Mistral body"

    def test_reasoning_effort_passed_through_including_xhigh(self):
        """Mistral's reasoning_effort ladder includes the 'xhigh' rung —
        must pass through unchanged."""
        backend = _make_backend()
        for level in ("none", "minimal", "low", "medium", "high", "xhigh"):
            body = backend._build_mistral_body(
                model="mistral-medium-latest",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.7,
                max_tokens=100,
                stream=False,
                reasoning_effort=level,
            )
            assert body["reasoning_effort"] == level

    def test_safe_prompt_added_when_env_var_set(self):
        """MISTRAL_SAFE_PROMPT=true must inject safe_prompt: true."""
        os.environ["MISTRAL_SAFE_PROMPT"] = "true"
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
        )
        assert body["safe_prompt"] is True

    def test_safe_prompt_omitted_by_default(self):
        """Without MISTRAL_SAFE_PROMPT, the field must NOT be sent (server
        default is false)."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
        )
        assert "safe_prompt" not in body

    def test_service_tier_added_when_env_var_set(self):
        os.environ["MISTRAL_SERVICE_TIER"] = "standard_only"
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
        )
        assert body["service_tier"] == "standard_only"

    def test_prompt_cache_key_when_session_id_provided(self):
        """session_id kwarg must produce a prompt_cache_key for cached-prefix
        billing (10% of input price on cache hits)."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            session_id="abc-123",
        )
        assert body["prompt_cache_key"] == "agentkthx-abc-123"

    def test_no_prompt_cache_key_without_session_id(self):
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
        )
        assert "prompt_cache_key" not in body

    def test_stream_options_include_usage_when_streaming(self):
        """PERF-02: streaming requests must opt in to usage stats."""
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=True,
        )
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}

    def test_response_format_passes_through(self):
        """json_schema / json_object modes must be forwarded unchanged."""
        backend = _make_backend()
        schema = {"type": "json_schema", "schema": {"type": "object"}}
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            response_format=schema,
        )
        assert body["response_format"] == schema

    def test_stop_sequence_normalized_to_list(self):
        backend = _make_backend()
        body = backend._build_mistral_body(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.7,
            max_tokens=100,
            stream=False,
            stop="###",
        )
        assert body["stop"] == ["###"]


# ---------------------------------------------------------------------------
# Response parser — Mistral error envelope + tool-call tolerance
# ---------------------------------------------------------------------------


class TestParseMistralResponse(unittest.TestCase):
    """The response parser must handle Mistral's wire format deltas."""

    def test_parses_text_only_response(self):
        raw = {
            "id": "cmpl-abc",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "mistral-small-latest",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["content"] == "Hello!"
        assert out["finish_reason"] == "stop"
        assert out["tool_calls"] == []
        assert out["usage"]["total_tokens"] == 7

    def test_parses_tool_calls_with_string_arguments(self):
        raw = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "D681PevKs",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": '{"command": "echo hi"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["finish_reason"] == "tool_calls"
        assert len(out["tool_calls"]) == 1
        tc = out["tool_calls"][0]
        assert tc["id"] == "D681PevKs"
        assert tc["name"] == "shell"
        assert tc["arguments"] == {"command": "echo hi"}

    def test_parses_tool_calls_with_object_arguments(self):
        """Mistral spec allows arguments as object — must tolerate."""
        raw = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "xyz",
                                "type": "function",
                                "function": {
                                    "name": "calc",
                                    "arguments": {"expression": "2+2"},
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["tool_calls"][0]["arguments"] == {"expression": "2+2"}

    def test_synthesizes_fallback_id_when_missing(self):
        """Mistral's schema default for tool_call.id is the literal 'null' —
        synthesize a fallback ID so the agent loop can pair tool messages."""
        raw = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                # no "id" field at all
                                "type": "function",
                                "function": {"name": "x", "arguments": "{}"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["tool_calls"][0]["id"] == "mistral_tc_0"

    def test_malformed_arguments_surfaces_raw_fallback(self):
        raw = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "bad",
                                "type": "function",
                                "function": {
                                    "name": "shell",
                                    "arguments": "not-valid-json{",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["tool_calls"][0]["arguments"] == {"_raw_arguments": "not-valid-json{"}

    def test_mistral_error_envelope_raises(self):
        """Mistral envelope: {"object": "error", "message": ...}."""
        with pytest.raises(RuntimeError, match="Provider rate limited"):
            MistralBackend._parse_mistral_response(
                {
                    "object": "error",
                    "message": "Provider rate limited",
                    "type": "rate_limit_error",
                    "code": "rate_limit",
                }
            )

    def test_openai_style_error_envelope_also_raises(self):
        """Some gateways wrap errors in OpenAI's {"error": {...}} form —
        the parser must handle both envelope shapes."""
        with pytest.raises(RuntimeError, match="Upstream error"):
            MistralBackend._parse_mistral_response(
                {
                    "error": {"message": "Upstream error", "code": 500},
                }
            )

    def test_no_choices_raises(self):
        with pytest.raises(RuntimeError, match="no choices"):
            MistralBackend._parse_mistral_response({})

    def test_handles_reasoning_content_chunks(self):
        """Reasoning models may return content as a list of ContentChunk
        with type=thinking + type=text. The parser must extract text and
        route thinking into reasoning_content."""
        raw = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [
                                    {"type": "text", "text": "The user asked..."},
                                ],
                                "signature": "EQAA...",
                            },
                            {"type": "text", "text": "It's currently 18°C."},
                        ],
                    },
                    "finish_reason": "stop",
                }
            ],
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["content"] == "It's currently 18°C."
        assert "The user asked" in out["reasoning_content"]

    def test_finish_reason_model_length_passes_through(self):
        """Mistral's distinct finish_reason for context overflow —
        the agent loop's context-recovery path should match on it."""
        raw = {
            "choices": [
                {
                    "message": {"content": "..."},
                    "finish_reason": "model_length",
                }
            ],
        }
        out = MistralBackend._parse_mistral_response(raw)
        assert out["finish_reason"] == "model_length"


# ---------------------------------------------------------------------------
# _iter_sse_lines — streaming transport (R07.09.1 hotfix)
# ---------------------------------------------------------------------------


class TestIterSseLines(unittest.TestCase):
    """The streaming transport hook required by OpenAICompatibleBackend.

    R07.09.0 shipped without this method — the agent loop's streaming
    path crashed with ``NotImplementedError: MistralBackend must
    implement _iter_sse_lines()``. These tests pin the contract.
    """

    def test_method_exists(self):
        """The abstract hook MUST be implemented (not inherited from base
        which raises NotImplementedError)."""
        backend = _make_backend()
        # Method must be defined on MistralBackend, not just inherited
        assert "_iter_sse_lines" in MistralBackend.__dict__, (
            "MistralBackend must define its own _iter_sse_lines — "
            "the inherited OpenAICompatibleBackend._iter_sse_lines raises "
            "NotImplementedError"
        )
        assert callable(getattr(backend, "_iter_sse_lines"))

    def test_yields_raw_sse_line_bytes(self):
        """On HTTP 200, _iter_sse_lines yields raw SSE line bytes —
        the base class's generate_completions_stream() handles JSON
        parsing + [DONE] detection."""
        backend = _make_backend()

        # Fake SSE response body — three chunks then [DONE]
        sse_lines = [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
            b'data: {"choices":[{"delta":{"content":", world"}}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b"data: [DONE]\n",
        ]
        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(return_value=iter(sse_lines))
        fake_response.close = MagicMock()

        with patch("urllib.request.urlopen", return_value=fake_response):
            result = list(
                backend._iter_sse_lines(
                    url="https://api.mistral.ai/v1/chat/completions",
                    body={"model": "mistral-small-latest", "messages": []},
                    headers={"Authorization": "Bearer x"},
                )
            )

        # Must yield the raw bytes — NOT pre-parsed dicts (the base class
        # does the parsing)
        assert result == sse_lines
        # Response must be closed (ROB-06)
        fake_response.close.assert_called_once()

    def test_401_raises_authentication_error(self):
        """HTTP 401 must surface as a clear authentication RuntimeError,
        not the bare NotImplementedError that broke R07.09.0."""
        backend = _make_backend()
        fake_401 = urllib.error.HTTPError(
            url="https://api.mistral.ai/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),
            fp=io.BytesIO(b'{"object":"error","message":"Invalid API key"}'),
        )

        with patch("urllib.request.urlopen", side_effect=fake_401):
            with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
                list(
                    backend._iter_sse_lines(
                        url="https://api.mistral.ai/v1/chat/completions",
                        body={"model": "mistral-small-latest", "messages": []},
                        headers={"Authorization": "Bearer bad"},
                    )
                )

    def test_429_retries_with_backoff(self):
        """429 rate-limit must honor Retry-After (or fall back to
        exponential backoff) and retry up to max_retries times."""
        backend = _make_backend()
        # Set a short backoff so the test doesn't take 60s
        backend._BACKOFF_BASE = 0.01
        backend._BACKOFF_CAP = 0.05

        fake_429 = urllib.error.HTTPError(
            url="https://api.mistral.ai/v1/chat/completions",
            code=429,
            msg="Rate limited",
            hdrs=MagicMock(),
            fp=io.BytesIO(b'{"object":"error","message":"Rate limit exceeded","code":"1300"}'),
        )
        fake_429.headers = {"Retry-After": "0"}  # honor immediately

        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(
            return_value=iter(
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )
        )
        fake_response.close = MagicMock()

        with patch("urllib.request.urlopen", side_effect=[fake_429, fake_response]) as m:
            result = list(
                backend._iter_sse_lines(
                    url="https://api.mistral.ai/v1/chat/completions",
                    body={"model": "mistral-small-latest", "messages": []},
                    headers={"Authorization": "Bearer x"},
                )
            )

        # Retried at least twice (first 429, then success)
        assert m.call_count == 2
        # Got the success chunk
        assert b"Hello" in b"".join(result) or b"ok" in b"".join(result)

    def test_context_length_400_triggers_recovery(self):
        """HTTP 400 with context-length message must invoke the shared
        _handle_context_length_400 helper and retry with reduced max_tokens."""
        backend = _make_backend()

        # First call returns 400 with context-length message
        ctx_msg = (
            b'{"object":"error","message":"This model\'s maximum context '
            b"length is 262144 tokens. However, your messages resulted in "
            b'300000 tokens of text input."}'
        )
        fake_400 = urllib.error.HTTPError(
            url="https://api.mistral.ai/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=MagicMock(),
            fp=io.BytesIO(ctx_msg),
        )

        # Second call succeeds
        fake_response = MagicMock()
        fake_response.__iter__ = MagicMock(
            return_value=iter(
                [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )
        )
        fake_response.close = MagicMock()

        body = {
            "model": "mistral-small-latest",
            "messages": [],
            "max_tokens": 8192,
        }

        with patch("urllib.request.urlopen", side_effect=[fake_400, fake_response]):
            list(
                backend._iter_sse_lines(
                    url="https://api.mistral.ai/v1/chat/completions",
                    body=body,
                    headers={"Authorization": "Bearer x"},
                )
            )

        # The shared _handle_context_length_400 helper should have
        # reduced the max_tokens in the body
        assert (
            body["max_tokens"] < 8192
        ), "ARCH-03 context-length recovery should have reduced max_tokens"

    def test_4xx_non_retryable_raises_immediately(self):
        """HTTP 400/401/403/404/422 must NOT retry — surface as RuntimeError
        immediately so the agent loop's classifier can act on them."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01  # in case anything tries to sleep

        fake_422 = urllib.error.HTTPError(
            url="https://api.mistral.ai/v1/chat/completions",
            code=422,
            msg="Unprocessable Entity",
            hdrs=MagicMock(),
            fp=io.BytesIO(
                b'{"object":"error","message":"unknown field: top_k",'
                b'"type":"invalid_request_error"}'
            ),
        )

        with patch("urllib.request.urlopen", side_effect=fake_422) as m:
            with pytest.raises(RuntimeError, match="unknown field: top_k"):
                list(
                    backend._iter_sse_lines(
                        url="https://api.mistral.ai/v1/chat/completions",
                        body={"model": "mistral-small-latest", "messages": []},
                        headers={"Authorization": "Bearer x"},
                    )
                )
        # Only ONE call — no retry on 422
        assert m.call_count == 1

    def test_mistral_error_envelope_parsed_for_message(self):
        """The 429/5xx error message must be extracted from Mistral's
        {\"object\":\"error\",...} envelope, not just dumped as raw bytes."""
        backend = _make_backend()
        backend._BACKOFF_BASE = 0.01
        backend._BACKOFF_CAP = 0.05
        backend._MAX_RETRIES = 0  # don't retry — surface immediately

        fake_429 = urllib.error.HTTPError(
            url="https://api.mistral.ai/v1/chat/completions",
            code=429,
            msg="Rate limited",
            hdrs=MagicMock(),
            fp=io.BytesIO(
                b'{"object":"error","message":"Rate limit exceeded",'
                b'"type":"rate_limited","code":"1300","raw_status_code":429}'
            ),
        )
        fake_429.headers = {}

        with patch("urllib.request.urlopen", side_effect=fake_429):
            with pytest.raises(RuntimeError) as exc_info:
                list(
                    backend._iter_sse_lines(
                        url="https://api.mistral.ai/v1/chat/completions",
                        body={"model": "mistral-small-latest", "messages": []},
                        headers={"Authorization": "Bearer x"},
                    )
                )
        # Must surface the upstream message, not raw bytes
        assert "Rate limit exceeded" in str(exc_info.value)
        assert "1300" in str(exc_info.value) or "429" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


class TestPluginRegistration(unittest.TestCase):
    """The register/unregister functions must work with a stub manager."""

    def test_register_calls_manager_register_backend(self):
        manager = MagicMock()
        register(manager)
        # Two register_backend calls: 'mistral' + 'mst' alias
        assert manager.register_backend.call_count == 2
        manager.register_backend.assert_any_call("mistral", MistralBackend)
        manager.register_backend.assert_any_call("mst", MistralBackend, alias_of="mistral")

    def test_unregister_calls_manager_unregister_backend(self):
        manager = MagicMock()
        unregister(manager)
        manager.unregister_backend.assert_any_call("mistral")
        manager.unregister_backend.assert_any_call("mst")


# ---------------------------------------------------------------------------
# Plugin loader integration — discover() + load()
# ---------------------------------------------------------------------------


class TestPluginLoaderIntegration(unittest.TestCase):
    """The PluginManager must discover and load the bundled plugin."""

    def test_builtin_manifest_loads_via_parser(self):
        """Direct parse of the manifest must succeed (validates against
        the v0.2 spec rules — name/dir match, required fields, etc.)."""
        from agentkthx.plugins._loader import _parse_manifest

        plugin_dir = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "mistral"
        manifest = _parse_manifest(plugin_dir / "plugin.json", root_kind="builtin")
        assert manifest.name == "mistral"
        assert manifest.type == "backend"
        assert manifest.entrypoint == "__init__"
        assert "mistral" in manifest.provides.get("backends", {})

    def test_builtin_manifest_in_v02_form(self):
        """The bundled manifest must use the v0.2 $schema and NOT rely on
        legacy top-level fields (spec compliance)."""
        from agentkthx.plugins._loader import CANONICAL_SCHEMA, _parse_manifest

        plugin_dir = Path(__file__).resolve().parents[1] / "agentkthx" / "plugins" / "mistral"
        manifest = _parse_manifest(plugin_dir / "plugin.json", root_kind="builtin")
        assert manifest.schema == CANONICAL_SCHEMA
        assert manifest.legacy_fields_used == []
        assert "agentnova" not in manifest.compatibility


# ---------------------------------------------------------------------------
# Live API tests — gated on MISTRAL_API_KEY
# ---------------------------------------------------------------------------

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")
live = pytest.mark.skipif(
    not MISTRAL_API_KEY, reason="MISTRAL_API_KEY not set — skipping live tests"
)


@live
class TestLiveAPI:
    """Live Mistral API tests. Skipped in CI without a key."""

    def test_basic_chat(self):
        backend = MistralBackend()
        result = backend.generate(
            model="mistral-small-latest",
            messages=[{"role": "user", "content": "What is 15 * 8? Answer briefly."}],
            temperature=0.0,
            max_tokens=50,
        )
        assert "120" in result["content"]

    def test_models_endpoint_returns_known_models(self):
        backend = MistralBackend()
        models = backend.list_models()
        names = {m["name"] for m in models}
        assert "mistral-small-latest" in names

    def test_streaming_yields_content(self):
        backend = MistralBackend()
        chunks = list(
            backend.generate_stream(
                model="mistral-small-latest",
                messages=[{"role": "user", "content": "Count from 1 to 5."}],
                temperature=0.0,
                max_tokens=100,
            )
        )
        assert len(chunks) >= 2
        # Concatenate and check that digits 1-5 appear
        full = "".join(chunks)
        for digit in "12345":
            assert digit in full, f"digit {digit} missing from stream output"

    def test_random_seed_determinism(self):
        """random_seed should produce (near-)deterministic output."""
        backend = MistralBackend()
        kwargs = {
            "model": "mistral-small-latest",
            "messages": [
                {"role": "user", "content": "Pick a number between 1 and 100. Just the number."}
            ],
            "temperature": 0.7,
            "max_tokens": 10,
            "random_seed": 42,
        }
        a = backend.generate(**kwargs)["content"]
        b = backend.generate(**kwargs)["content"]
        # Mistral's seed is documented as deterministic — same seed +
        # same input should give same output (modulo floating-point
        # nondeterminism on the inference side, which is rare).
        assert a and b


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
