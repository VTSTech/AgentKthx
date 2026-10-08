"""
R07.28 closure batch 2 — MAINT-28 follow-through: shared CloudBackend
HTTP transport + ROB-42 catch-narrowing on the older backends.

Batch 2 lifts (all verified line-for-line identical pre-lift):
  - ``_make_api_request`` / ``_iter_sse_lines`` — the request loops
    themselves (batch 1 lifted only the error handlers; the remaining
    loops differed only in the ``_provider_label`` log prefix and the
    ``_error_brand`` "retries exhausted" text)
  - ``generate_stream`` — the ARCH-01 thin text-delta wrapper
  - ``_jev_call_completions`` — JEV decision call via the shared path,
    with the Cloudflare ``top_k`` pop moved into the new
    ``_tweak_request_body`` hook (single choke point fired by
    ``_make_api_request``)
  - ``_extra_auth_headers`` — the docstring-only ``return {}`` copies
    deleted (plain-Bearer backends inherit the base hook)
  - the 4-method "does NOT strip the provider prefix" catalog family
    (``get_model_info`` / ``_get_model_defaults`` /
    ``get_model_max_context`` / ``_is_free_model``) replaced by the
    single ``_catalog_model_key`` hook

ROB-42 follow-through (registered in the R07.28 delta as part of the
ROB-42 closure class):
  - zai + pollinations ``list_models()`` narrow the bare
    ``except Exception`` to the ROB-42 4-tuple — malformed-shape
    programming errors propagate instead of being masked as "discovery
    failed" (store-on-success + stale-first service were already
    correct on these backends; only the masking half was open)
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.cloud_base import CloudBackend  # noqa: E402
from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend  # noqa: E402
from agentkthx.plugins.nvidia.nvidia import NvidiaBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures + helpers (same contract as batch 1)
# ---------------------------------------------------------------------------

NVIDIA_KEY = "nvapi-test-key-12345678901234567890"
CF_KEY = "cf-test-token-12345678901234567890"
CF_ACCOUNT = "abcdef0123456789abcdef0123456789"
ZAI_KEY = "zai-test-key-1234567890123456789012345"


@pytest.fixture
def nvidia_backend(monkeypatch):
    from agentkthx import config as _config

    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    monkeypatch.setattr(_config, "NVIDIA_API_KEY", NVIDIA_KEY, raising=False)
    return NvidiaBackend()


@pytest.fixture
def cf_backend(monkeypatch):
    from agentkthx import config as _config

    monkeypatch.setenv("CLOUDFLARE_API_KEY", CF_KEY)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT)
    monkeypatch.setattr(_config, "CLOUDFLARE_API_KEY", CF_KEY, raising=False)
    monkeypatch.setattr(_config, "CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT, raising=False)
    return CloudflareBackend()


@pytest.fixture
def zai_backend(monkeypatch):
    from agentkthx import config as _config
    from agentkthx.plugins.zai.zai import ZaiBackend

    monkeypatch.setenv("ZAI_API_KEY", ZAI_KEY)
    monkeypatch.setattr(_config, "ZAI_API_KEY", ZAI_KEY, raising=False)
    return ZaiBackend()


@pytest.fixture
def pollinations_backend(monkeypatch):
    from agentkthx.plugins.pollinations.pollinations import PollinationsBackend

    monkeypatch.delenv("POLLINATIONS_API_KEY", raising=False)
    return PollinationsBackend(api_key="sk_anon_tier_12345")


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    from agentkthx import model_cache as _mc

    monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")
    return _mc


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://unit.test",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda s: None)


def _ok_response(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


def _chat_payload(content: str = "ok") -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]
    }


# ---------------------------------------------------------------------------
# Batch 2 — the transport is shared, not copied
# ---------------------------------------------------------------------------


class TestTransportIsInherited:
    """The lifted methods resolve to the CloudBackend implementations."""

    def test_nvidia_has_no_local_transport_copies(self):
        for name in (
            "_make_api_request",
            "_iter_sse_lines",
            "generate_stream",
            "_jev_call_completions",
            "_extra_auth_headers",
            "get_model_info",
            "_get_model_defaults",
            "get_model_max_context",
            "_is_free_model",
        ):
            assert getattr(NvidiaBackend, name) is getattr(
                CloudBackend, name
            ), f"NvidiaBackend.{name} should be inherited from CloudBackend"

    def test_cloudflare_has_no_local_transport_copies(self):
        for name in (
            "_make_api_request",
            "_iter_sse_lines",
            "generate_stream",
            "_jev_call_completions",
            "_extra_auth_headers",
            "get_model_info",
            "_get_model_defaults",
            "get_model_max_context",
            "_is_free_model",
        ):
            assert getattr(CloudflareBackend, name) is getattr(
                CloudBackend, name
            ), f"CloudflareBackend.{name} should be inherited from CloudBackend"

    def test_catalog_key_hook_contract(self, nvidia_backend, cf_backend):
        """Default: strip prefix. NVIDIA/Cloudflare: full prefixed ID."""
        assert (
            CloudBackend._catalog_model_key(SimpleNamespace(), "zai/glm-4-flash") == "glm-4-flash"
        )
        assert nvidia_backend._catalog_model_key("meta/llama-3.3-70b-instruct") == (
            "meta/llama-3.3-70b-instruct"
        )
        assert cf_backend._catalog_model_key("@cf/meta/llama-3.3-70b-instruct-fp8-fast") == (
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        )

    def test_catalog_lookup_via_inherited_methods(self, nvidia_backend, cf_backend):
        """The base catalog methods + hook reproduce the deleted overrides."""
        # NVIDIA: full-ID lookup works, free_tier from the module helper's
        # uniform-True-within-quota contract for cataloged models
        model = "moonshotai/kimi-k3"
        info = nvidia_backend.get_model_info(model)
        assert info is not None and info["name"] == model
        assert info["details"]["free_tier"] is True
        assert nvidia_backend._is_free_model(model) is True
        assert nvidia_backend._is_free_model("totally/unknown-model") is False

        # Cloudflare: @cf/ prefix is part of the ID, not slashable
        cf_model = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        cf_info = cf_backend.get_model_info(cf_model)
        assert cf_info is not None and cf_info["name"] == cf_model

        # defaults + max context flow through the hook too
        defaults = nvidia_backend._get_model_defaults(model)
        assert set(defaults) >= {"temperature", "max_tokens"}
        assert nvidia_backend.get_model_max_context(model) > 0
        assert cf_backend.get_model_max_context(cf_model) > 0

    def test_extra_auth_headers_default_empty(self, nvidia_backend, cf_backend):
        """Plain-Bearer backends inherit the base hook (deleted copies)."""
        assert nvidia_backend._extra_auth_headers() == {}
        assert cf_backend._extra_auth_headers() == {}


# ---------------------------------------------------------------------------
# Batch 2 — behavioral: the inherited transport still drives both backends
# ---------------------------------------------------------------------------


class TestSharedTransportBehavior:
    """The inherited _make_api_request / _iter_sse_lines / generate_stream
    behave identically to the deleted per-backend copies."""

    def test_non_streaming_success_via_shared_loop(self, nvidia_backend, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=None: _ok_response(_chat_payload())
        )
        result = nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert result is not None

    def test_tweak_request_body_fires_once_per_request(self, cf_backend, monkeypatch):
        """Cloudflare's hook pops top_k BEFORE the first send (not per
        attempt) — captured request bodies must never carry top_k."""
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _ok_response(_chat_payload())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        body = {"model": "@cf/x/y", "messages": [], "top_k": 50}
        cf_backend._make_api_request(body, stream=False)
        assert captured == [{"model": "@cf/x/y", "messages": []}]

    def test_nvidia_keeps_top_k_via_noop_hook(self, nvidia_backend, monkeypatch):
        """NVIDIA has no body quirks — top_k passes through untouched."""
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _ok_response(_chat_payload())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        body = {"model": "m", "messages": [], "top_k": 50}
        nvidia_backend._make_api_request(body, stream=False)
        assert captured[0]["top_k"] == 50

    def test_streaming_success_via_shared_loop(self, cf_backend, monkeypatch):
        """_iter_sse_lines yields raw SSE bytes and closes the response."""
        # io.BytesIO iterates by line (the same contract as a real
        # urllib response in tests) and carries .close for ROB-06.
        resp = io.BytesIO(b'data: {"x":1}\ndata: [DONE]\n')
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        gen = cf_backend._iter_sse_lines("https://unit.test", {"model": "@cf/x/y"}, {})
        lines = list(gen)
        assert lines == [b'data: {"x":1}\n', b"data: [DONE]\n"]

    def test_streaming_uses_log_prefix_via_provider_label(
        self, nvidia_backend, monkeypatch, capsys
    ):
        """The shared loop derives [NVIDIA-Stream] / [Cloudflare-Stream] from
        _provider_label — same output the deleted copies printed."""
        _no_sleep(monkeypatch)

        def fake_urlopen(req, timeout=None):
            raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "1")

        gen = nvidia_backend._iter_sse_lines("https://unit.test", {"model": "m"}, {})
        with pytest.raises(RuntimeError, match="NVIDIA NIM API error 429"):
            list(gen)
        captured = capsys.readouterr()
        assert "[NVIDIA-Stream] 429" in (captured.out + captured.err)

    def test_generate_stream_yields_deltas(self, nvidia_backend, monkeypatch):
        """The inherited thin wrapper yields text deltas only."""

        def fake_stream(**kwargs):
            for chunk in ({"delta": "hel", "finish_reason": None}, {"delta": ""}, {"delta": "lo"}):
                yield chunk

        monkeypatch.setattr(nvidia_backend, "generate_completions_stream", fake_stream)
        assert list(nvidia_backend.generate_stream("m", [])) == ["hel", "lo"]

    def test_jev_call_completions_routes_through_shared_path(self, cf_backend, monkeypatch):
        """The inherited JEV hook builds a no-tools body (response_format
        passthrough) and reuses _make_api_request — top_k popped by the
        Cloudflare hook."""
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            return _ok_response(_chat_payload(content="decision"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = cf_backend._jev_call_completions(
            model="@cf/x/y",
            messages=[{"role": "user", "content": "decide"}],
            response_format={"type": "json_object"},
            top_k=50,
        )
        assert result is not None
        sent = captured[0]
        assert "tools" not in sent or sent["tools"] in (None, [])
        assert sent.get("response_format") == {"type": "json_object"}
        assert "top_k" not in sent

    def test_url_error_exhaustion_message_shared(self, nvidia_backend, monkeypatch):
        """'{brand} connection error' surfaces through the inherited loop."""
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "0")

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("conn refused"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(RuntimeError, match="NVIDIA NIM connection error: conn refused"):
            nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)


# ---------------------------------------------------------------------------
# Batch 2 — ROB-42 catch-narrowing on zai + pollinations
# ---------------------------------------------------------------------------


class TestRob42CatchNarrowingLegacyBackends:
    """zai + pollinations list_models(): bare `except Exception` narrowed
    to the ROB-42 4-tuple (masking half of the closure class)."""

    def test_zai_malformed_shape_raises_not_masked(self, zai_backend, monkeypatch, isolated_cache):
        """A programming-error shape (JSON list where a dict was expected)
        propagates instead of being masked as 'discovery failed'."""

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(b"[]")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(AttributeError):
            zai_backend.list_models()

    def test_zai_network_failure_serves_seed_without_store(
        self, zai_backend, monkeypatch, isolated_cache
    ):
        from agentkthx import model_cache as _mc

        store_calls = []
        real_store = _mc.store_models

        def spy_store(*args, **kwargs):
            store_calls.append(args)
            return real_store(*args, **kwargs)

        monkeypatch.setattr(_mc, "store_models", spy_store)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = zai_backend.list_models()
        assert store_calls == []
        assert len(models) >= 5
        assert all(m.get("name") for m in models)

    def test_pollinations_malformed_shape_raises_not_masked(
        self, pollinations_backend, monkeypatch, isolated_cache
    ):
        """Pollinations _fetch_live_models raises RuntimeError on an
        unreachable/empty gateway (caught) but AttributeError on a
        malformed card shape (propagates — the narrowing contract)."""

        def fake_urlopen(req, timeout=None):
            # a valid-JSON body whose shape breaks the card parser
            return io.BytesIO(json.dumps([{"not": "a dict map"}]).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises((AttributeError, TypeError)):
            pollinations_backend.list_models()

    def test_pollinations_unreachable_serves_seed_without_store(
        self, pollinations_backend, monkeypatch, isolated_cache
    ):
        from agentkthx import model_cache as _mc

        store_calls = []
        real_store = _mc.store_models

        def spy_store(*args, **kwargs):
            store_calls.append(args)
            return real_store(*args, **kwargs)

        monkeypatch.setattr(_mc, "store_models", spy_store)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("gateway down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = pollinations_backend.list_models()
        assert store_calls == []
        assert len(models) >= 5
        assert all(m.get("name") for m in models)

    def test_zai_stale_beats_seed_on_failure(self, zai_backend, monkeypatch, isolated_cache):
        """A last-known-good live catalog (stale past TTL) is served on
        discovery failure and is NOT overwritten by the seed."""
        from agentkthx import model_cache as _mc

        stale_live = [
            {
                "name": "zai-fake-live-only-model",
                "size": 0,
                "details": {"context_length": 12345, "free_tier": True},
            }
        ]
        _mc.store_models(zai_backend.MODEL_CACHE_KEY, stale_live)
        monkeypatch.setattr(_mc, "get_ttl", lambda: 0)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        names = {m["name"] for m in zai_backend.list_models()}
        assert "zai-fake-live-only-model" in names
