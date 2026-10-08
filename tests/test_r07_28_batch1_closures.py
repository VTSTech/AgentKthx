"""
R07.28 closure batch 1 — MAINT-28 + ROB-42 regression tests.

MAINT-28 (retry-loop skeleton deduplicated ×4 → shared CloudBackend):
  - the shared HTTPError/URLError handlers exist on CloudBackend
  - both new backends' ``_make_api_request`` / ``_iter_sse_lines``
    delegate to them behaviorally: quota-429 fast-fail, remediation
    texts, transient-429 retry, fixed-param 400 recovery, URLError
    exhaustion — all through the shared path
  - the cloudflare "limit" docstring drift can't fire again (the
    docstring and the classifier now agree, and the classifier is the
    single source of truth)
  - ROB-43 (closed in batch 3, same release): the ``attempt == 0``
    quota gate has been REMOVED — the pin test below was re-pointed to
    the new contract (quota 429 fast-fails at ANY attempt) and
    tests/test_r07_28_batch3_quick_wins.py pins the flip side

ROB-42 (list_models() failure path — both new backends):
  - a failed live fetch NEVER persists the seed fallback as fresh
    ``source="api"`` cache (the R07.24 ROB-28/ROB-30 closure class)
  - a failed live fetch serves the stale last-known-good cache first,
    then the static seed
  - malformed-shape programming errors propagate instead of being
    masked as "discovery failed" (catch-narrowing, Mistral pattern)
  - the success path still stores with ``source="api"`` (provenance
    preserved for the dashboard / cache consumers)
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.backends.cloud_base import CloudBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

NVIDIA_KEY = "nvapi-test-key-12345678901234567890"
CF_KEY = "cf-test-token-12345678901234567890"
CF_ACCOUNT = "abcdef0123456789abcdef0123456789"


@pytest.fixture
def nvidia_backend(monkeypatch):
    """An NvidiaBackend with a test key (env-isolated)."""
    from agentkthx import config as _config
    from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    monkeypatch.setattr(_config, "NVIDIA_API_KEY", NVIDIA_KEY, raising=False)
    return NvidiaBackend()


@pytest.fixture
def cf_backend(monkeypatch):
    """A CloudflareBackend with a test key + account id (env-isolated)."""
    from agentkthx import config as _config
    from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

    monkeypatch.setenv("CLOUDFLARE_API_KEY", CF_KEY)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT)
    monkeypatch.setattr(_config, "CLOUDFLARE_API_KEY", CF_KEY, raising=False)
    monkeypatch.setattr(_config, "CLOUDFLARE_ACCOUNT_ID", CF_ACCOUNT, raising=False)
    return CloudflareBackend()


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path):
    """Point the persistent model_catalog.json cache at a temp file."""
    from agentkthx import model_cache as _mc

    monkeypatch.setattr(_mc, "get_cache_path", lambda: tmp_path / "model_catalog.json")
    return _mc


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    """Build a real HTTPError whose .read() yields ``body``.

    Passing fp=io.BytesIO(...) makes ``e.fp`` truthy so the shared
    handler's ``e.read() if e.fp else b""`` branch reads the body —
    the same contract as a real urllib 4xx response.
    """
    return urllib.error.HTTPError(
        url="https://unit.test",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


def _no_sleep(monkeypatch) -> None:
    """Neutralize backoff sleeps (shared handler + base helpers)."""
    monkeypatch.setattr(time, "sleep", lambda s: None)


# ---------------------------------------------------------------------------
# MAINT-28 — shared skeleton exists on CloudBackend
# ---------------------------------------------------------------------------


class TestMaint28SharedSkeleton:
    """The MAINT-28 template methods + hooks live on CloudBackend."""

    def test_shared_handlers_exist_on_cloudbackend(self):
        for name in (
            "_handle_http_error_for_retry",
            "_handle_url_error_for_retry",
            "_parse_error_envelope",
            "_check_quota_429",
            "_raise_non_retryable_status",
            "_looks_like_quota_exhaustion",
            "_quota_exhaustion_message",
            "_handle_fixed_param_400",
        ):
            assert hasattr(CloudBackend, name), f"CloudBackend.{name} missing"
        assert isinstance(CloudBackend._STATUS_REMEDIATIONS, dict)
        assert isinstance(CloudBackend._error_brand, str)

    def test_parse_error_envelope_shapes(self):
        parse = CloudBackend._parse_error_envelope
        # OpenAI-spec envelope
        assert parse('{"error":{"message":"boom"}}', 500) == "boom"
        # error-as-string gateway shape
        assert parse('{"error":"plain fail"}', 500) == "plain fail"
        # plain message envelope
        assert parse('{"message":"plain msg"}', 500) == "plain msg"
        # fallbacks: malformed JSON / non-dict / empty body
        assert parse("not json at all", 500) == "not json at all"
        assert parse('["a","list"]', 500) == '["a","list"]'
        assert parse("", 503) == "HTTP 503"

    def test_remediation_tables_carry_legacy_texts(self, nvidia_backend, cf_backend):
        """The 401/404/422 texts survive the dedup (content, not just shape)."""
        n = nvidia_backend._STATUS_REMEDIATIONS
        assert "nvapi-" in n[401]
        assert "build.nvidia.com" in n[404] and "{err_msg}" in n[404]
        assert "force_react=True" in n[422]
        c = cf_backend._STATUS_REMEDIATIONS
        assert "Workers AI-scoped" in c[401]
        assert "CLOUDFLARE_ACCOUNT_ID" in c[404] and "{err_msg}" in c[404]
        assert "Responses API" in c[422]

    def test_default_hooks_are_inert(self):
        """The base-class hooks default to 'no quota model, no recovery'."""
        from types import SimpleNamespace

        # CloudBackend is abstract — call the hooks unbound with a
        # stand-in self carrying the only attribute they read.
        dummy = SimpleNamespace(_error_brand="cloud backend")
        assert CloudBackend._looks_like_quota_exhaustion(dummy, 429, "quota") is False
        assert CloudBackend._handle_fixed_param_400(dummy, "body", {}, "[X]") is False
        assert CloudBackend._quota_exhaustion_message(dummy) == "cloud backend quota exhausted."

    def test_cloudflare_docstring_no_longer_lists_limit_as_indicator(self, cf_backend):
        """Drift guard (MAINT-28): no docstring may list "limit" among the
        quota indicators — that was the fired drift (the classifier
        deliberately excludes "limit" because "rate limit exceeded" is the
        canonical transient 429 wording).

        R07.28 batch 2: the per-backend ``_make_api_request`` copy no
        longer exists (inherited from CloudBackend), so the drift surface
        moved to (a) the shared transport docstring — which must DEFER to
        the classifier as the single source of truth and explicitly
        disclaim "limit" — and (b) the Cloudflare remediation-hook
        docstring, which keeps the historical drift note."""
        from agentkthx.backends.cloud_base import CloudBackend as _CB
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend

        # the per-backend copy is gone — inherited, single source of truth
        assert CloudflareBackend._make_api_request is _CB._make_api_request

        doc = cf_backend._make_api_request.__doc__ or ""
        assert '"daily" / "limit"' not in doc
        assert 'does NOT treat "limit"' in doc

        qdoc = cf_backend._quota_exhaustion_message.__doc__ or ""
        assert "deliberately NOT" in qdoc


# ---------------------------------------------------------------------------
# MAINT-28 — both backends delegate to the shared handler (behavioral)
# ---------------------------------------------------------------------------


class TestMaint28Quota429Delegation:
    """Quota-429 fast-fail routes through the shared skeleton."""

    def test_nvidia_quota_429_fast_fails_non_streaming(self, nvidia_backend, monkeypatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(
                429,
                '{"error":{"message":"monthly credit balance exhausted for this account"}}',
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="monthly credit quota exhausted"):
            nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)
        # Fast-fail: exactly ONE request — no retry budget burned.
        assert len(calls) == 1

    def test_nvidia_quota_429_fast_fails_streaming(self, nvidia_backend, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise _http_error(429, '{"error":{"message":"monthly credit quota gone"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        gen = nvidia_backend._iter_sse_lines("https://unit.test", {"model": "m"}, {})
        with pytest.raises(RuntimeError, match="monthly credit quota exhausted"):
            list(gen)

    def test_nvidia_quota_details_carry_parsed_envelope(self, nvidia_backend, monkeypatch):
        """The streaming path now embeds the PARSED envelope message in the
        Details: line (pre-dedup it embedded a raw body slice — the drift
        MAINT-28 removes by parsing before the quota check)."""
        body = '{"error":{"message":"credit balance is empty"}}'

        def fake_urlopen(req, timeout=None):
            raise _http_error(429, body)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        gen = nvidia_backend._iter_sse_lines("https://unit.test", {"model": "m"}, {})
        with pytest.raises(RuntimeError, match="Details: credit balance is empty"):
            list(gen)

    def test_cloudflare_quota_429_fast_fails_non_streaming(self, cf_backend, monkeypatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(
                429,
                '{"error":{"message":"daily neuron quota exhausted, wait for reset"}}',
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="daily neuron quota exhausted"):
            cf_backend._make_api_request({"model": "@cf/x/y", "messages": []}, stream=False)
        assert len(calls) == 1

    def test_cloudflare_quota_429_fast_fails_streaming(self, cf_backend, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise _http_error(429, '{"error":{"message":"daily neuron quota exhausted"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        gen = cf_backend._iter_sse_lines("https://unit.test", {"model": "@cf/x/y"}, {})
        with pytest.raises(RuntimeError, match="daily neuron quota exhausted"):
            list(gen)

    def test_rob43_quota_429_after_transient_retry_fast_fails(self, nvidia_backend, monkeypatch):
        """ROB-43 (CLOSED, R07.28 batch 3): the attempt == 0 gate is GONE —
        a quota 429 arriving on attempt 1 raises the clear quota message
        immediately instead of burning the retry budget on a fatal 429."""
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "1")
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')
            raise _http_error(429, '{"error":{"message":"monthly credit quota exhausted now"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="monthly credit quota exhausted"):
            nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)
        # Two calls: attempt 0 (transient — retried) + attempt 1 (quota —
        # immediate raise). No budget burn, no generic "API error 429".
        assert len(calls) == 2


class TestMaint28RetryDelegation:
    """Retryable statuses, 400 recovery, and URLError exhaustion all
    route through the shared skeleton."""

    def test_transient_429_retries_then_succeeds_nvidia(self, nvidia_backend, monkeypatch):
        _no_sleep(monkeypatch)
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')
            return io.BytesIO(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert result is not None
        assert len(calls) == 2

    def test_fixed_param_400_recovers_nvidia(self, nvidia_backend, monkeypatch):
        _no_sleep(monkeypatch)
        calls = []
        body = {"model": "moonshotai/kimi-k3", "messages": [], "top_p": 0.7}

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(
                    400,
                    "Validation: `top_p` is fixed at 0.95 for Kimi K3; the value is not configurable",
                )
            return io.BytesIO(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ).encode("utf-8")
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = nvidia_backend._make_api_request(body, stream=False)
        assert result is not None
        assert len(calls) == 2
        # The hook set the param to the fixed value before retrying.
        assert body["top_p"] == 0.95

    def test_401_remediation_via_shared_table_both_backends(
        self, nvidia_backend, cf_backend, monkeypatch
    ):
        """401 (no table ambiguity) raises the per-backend remediation text
        from the shared _STATUS_REMEDIATIONS table — in ONE call."""

        def nvidia_401(req, timeout=None):
            raise _http_error(401, '{"error":{"message":"bad key"}}')

        monkeypatch.setattr("urllib.request.urlopen", nvidia_401)
        with pytest.raises(RuntimeError, match="nvapi-"):
            nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)

        def cf_401(req, timeout=None):
            raise _http_error(401, '{"error":{"message":"bad token"}}')

        monkeypatch.setattr("urllib.request.urlopen", cf_401)
        with pytest.raises(RuntimeError, match="Workers AI-scoped"):
            cf_backend._make_api_request({"model": "@cf/x/y", "messages": []}, stream=False)

    def test_403_5035_cache_write_survives_via_override(self, cf_backend, monkeypatch, tmp_path):
        """The Cloudflare 403 override keeps its side effect: a 5035 body
        caches the paid-only verdict before raising (existing behavior,
        now via _raise_non_retryable_status)."""
        from agentkthx.core import tool_cache

        monkeypatch.setattr(tool_cache, "get_cache_file", lambda: tmp_path / "tool_support.json")

        paid_body = (
            '{"result":{},"success":false,'
            '"errors":[{"code":5035,"message":"AiError: Model @cf/zai-org/glm-5.3-flash '
            'is not available on the Workers Free plan"}],"messages":[]}'
        )

        def fake_urlopen(req, timeout=None):
            raise _http_error(403, paid_body)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="Workers Paid plan"):
            cf_backend._make_api_request(
                {"model": "@cf/zai-org/glm-5.3-flash", "messages": []}, stream=False
            )
        assert tool_cache.is_cached_cloudflare_paid_only("@cf/zai-org/glm-5.3-flash") is True

    def test_url_error_exhaustion_message_via_shared_handler(
        self, nvidia_backend, cf_backend, monkeypatch
    ):
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "0")

        reason = urllib.error.URLError(OSError("conn refused"))

        def nvidia_fail(req, timeout=None):
            raise reason

        monkeypatch.setattr("urllib.request.urlopen", nvidia_fail)
        with pytest.raises(RuntimeError, match="NVIDIA NIM connection error: conn refused"):
            nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)

        def cf_fail(req, timeout=None):
            raise reason

        monkeypatch.setattr("urllib.request.urlopen", cf_fail)
        with pytest.raises(RuntimeError, match="Cloudflare connection error: conn refused"):
            cf_backend._make_api_request({"model": "@cf/x/y", "messages": []}, stream=False)


# ---------------------------------------------------------------------------
# ROB-42 — list_models() failure path (both new backends)
# ---------------------------------------------------------------------------


class TestRob42Nvidia:
    """NVIDIA list_models(): no seed-as-api poisoning, stale-first service,
    narrowed except."""

    def test_failed_fetch_does_not_persist_seed_as_api(
        self, nvidia_backend, monkeypatch, isolated_cache
    ):
        """A URLError on the live fetch must NOT call store_models — the
        seed must never land in the persistent cache labeled source="api"."""
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

        models = nvidia_backend.list_models()

        assert store_calls == []
        # Still functional: the seed catalog serves as the offline fallback.
        assert len(models) >= 20
        assert all(m.get("name") for m in models)

    def test_failed_fetch_serves_stale_over_seed(self, nvidia_backend, monkeypatch, isolated_cache):
        """A last-known-good live catalog (stale past TTL) beats the seed on
        discovery failure — and is NOT overwritten by it."""
        from agentkthx import model_cache as _mc

        stale_live = [
            {
                "name": "meta/fake-live-only-model",
                "size": 0,
                "details": {"context_length": 12345, "free_tier": True},
            }
        ]
        _mc.store_models(nvidia_backend.MODEL_CACHE_KEY, stale_live)
        # Make the entry stale for get_cached_models while get_stale_models
        # still serves it (get_fresh: ttl <= 0 → None).
        monkeypatch.setattr(_mc, "get_ttl", lambda: 0)

        store_calls = []
        real_store = _mc.store_models

        def spy_store(*args, **kwargs):
            store_calls.append(args)
            return real_store(*args, **kwargs)

        monkeypatch.setattr(_mc, "store_models", spy_store)

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError(OSError("network down"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = nvidia_backend.list_models()

        assert store_calls == []
        names = {m["name"] for m in models}
        assert "meta/fake-live-only-model" in names

    def test_malformed_shape_raises_not_masked(self, nvidia_backend, monkeypatch, isolated_cache):
        """A programming-error shape (JSON list where a dict was expected)
        propagates instead of being masked as 'discovery failed' (ROB-28
        catch-narrowing class)."""

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(b"[]")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(AttributeError):
            nvidia_backend.list_models()

    def test_success_path_still_stores_api_source(
        self, nvidia_backend, monkeypatch, isolated_cache
    ):
        """The happy path is unchanged: live models returned, cache stored
        with source="api" (provenance preserved for cache consumers)."""
        from agentkthx import model_cache as _mc

        live = {"object": "list", "data": [{"id": "meta/test-live-model", "owned_by": "META"}]}

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps(live).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = nvidia_backend.list_models()

        names = {m["name"] for m in models}
        assert "meta/test-live-model" in names
        info = _mc.cache_info(nvidia_backend.MODEL_CACHE_KEY)
        assert info is not None and info["source"] == "api"


class TestRob42Cloudflare:
    """Cloudflare list_models(): same ROB-42 contract (incl. the
    success=false RuntimeError surfaced by _fetch_live_models)."""

    def test_failed_fetch_does_not_persist_seed_as_api(
        self, cf_backend, monkeypatch, isolated_cache
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

        models = cf_backend.list_models()

        assert store_calls == []
        assert len(models) >= 10
        assert all(m.get("name") for m in models)

    def test_success_false_serves_stale_not_seed_poison(
        self, cf_backend, monkeypatch, isolated_cache
    ):
        """RuntimeError(success=false) is a legitimate discovery failure —
        the stale last-known-good catalog is served, nothing is stored."""
        from agentkthx import model_cache as _mc

        stale_live = [
            {
                "name": "@cf/fake/live-only-model",
                "size": 0,
                "details": {"context_length": 12345, "free_tier": True},
            }
        ]
        _mc.store_models(cf_backend.MODEL_CACHE_KEY, stale_live)
        monkeypatch.setattr(_mc, "get_ttl", lambda: 0)

        store_calls = []
        real_store = _mc.store_models

        def spy_store(*args, **kwargs):
            store_calls.append(args)
            return real_store(*args, **kwargs)

        monkeypatch.setattr(_mc, "store_models", spy_store)

        bad = {
            "success": False,
            "result": [],
            "errors": [{"code": 1, "message": "x"}],
            "messages": [],
        }

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps(bad).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = cf_backend.list_models()

        assert store_calls == []
        names = {m["name"] for m in models}
        assert "@cf/fake/live-only-model" in names

    def test_success_path_still_stores_api_source(self, cf_backend, monkeypatch, isolated_cache):
        from agentkthx import model_cache as _mc

        live = {
            "success": True,
            "result": [
                {
                    "name": "@cf/test/live-model",
                    "task": {"name": "Text Generation"},
                    "properties": [{"property_id": "context_window", "value": 24000}],
                }
            ],
            "errors": [],
            "messages": [],
        }

        def fake_urlopen(req, timeout=None):
            return io.BytesIO(json.dumps(live).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        models = cf_backend.list_models()

        names = {m["name"] for m in models}
        assert "@cf/test/live-model" in names
        info = _mc.cache_info(cf_backend.MODEL_CACHE_KEY)
        assert info is not None and info["source"] == "api"
