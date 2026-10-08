"""
R07.28 closure batch 3 — the four quick wins (ROB-43, ROB-44, MAINT-29,
MAINT-30), all Low-priority findings from the R07.26/R07.27 new-backend
surface.

ROB-43 (quota-exhaustion fast-fail gated to attempt == 0):
  - a quota 429 arriving AFTER >=1 transient retry now raises the clear
    quota message immediately — no budget burn, no generic "API error
    429" — on the shared non-streaming AND streaming paths, for both
    new backends (the gate lived in CloudBackend._check_quota_429)
  - transient rate-limit 429s still retry normally (the quota
    classifier still refuses to match "rate limit" wording)

ROB-44 (Cloudflare discovery hardcoded api.cloudflare.com):
  - _get_models_url() derives scheme+host from _base_url, so a
    CLOUDFLARE_BASE_URL override routes catalog discovery to the same
    host as chat traffic
  - the default-template URL is byte-identical to the pre-fix literal
  - the literal fallback survives when _base_url is unset/empty

MAINT-29 (test_tool_support doc drift):
  - both overrides' docstrings state the name-pattern-only contract and
    no longer promise a first-use probe + tool_support.json cache write
  - the module-level _is_free_model docstring no longer claims unknown
    models are "conservatively treated as paid" (the live-merge path
    treats them as free)
  - the classification behavior itself is unchanged (REACT/NATIVE
    pattern tables still decide)

MAINT-30 (docs/SUPPORT.md tier tables missing NVIDIA):
  - the Fully Supported table includes NVIDIA NIM with NVIDIA_API_KEY
  - all 10 cloud backends are classified (7 Fully + 3 Limited)
  - the stale "all 9 cloud backends" smoke-test count is gone
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

# Make agentkthx importable when run from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core.types import ToolSupportLevel  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures + helpers (same shapes as test_r07_28_batch1_closures.py)
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


def _http_error(code: int, body: str) -> urllib.error.HTTPError:
    """Build a real HTTPError whose .read() yields ``body``."""
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
# ROB-43 — the attempt == 0 gate is gone
# ---------------------------------------------------------------------------


class TestRob43QuotaGateDropped:
    """A quota 429 fast-fails at ANY attempt, on both transport paths."""

    def test_quota_429_after_transient_retry_fast_fails_cloudflare(self, cf_backend, monkeypatch):
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "1")
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                # Transient rate limit — must NOT trip the neuron classifier.
                raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')
            raise _http_error(429, '{"error":{"message":"daily neuron quota exhausted"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        with pytest.raises(RuntimeError, match="daily neuron quota exhausted"):
            cf_backend._make_api_request({"model": "@cf/x/y"}, stream=False)
        # Attempt 0 retried, attempt 1 raised the quota message — the
        # remaining budget was NOT burned.
        assert len(calls) == 2

    def test_quota_429_after_transient_retry_fast_fails_streaming(
        self, nvidia_backend, monkeypatch
    ):
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "1")
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')
            raise _http_error(429, '{"error":{"message":"monthly credit quota exhausted now"}}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        gen = nvidia_backend._iter_sse_lines("https://unit.test", {"model": "m"}, {})
        with pytest.raises(RuntimeError, match="monthly credit quota exhausted"):
            list(gen)
        assert len(calls) == 2

    def test_transient_rate_limit_still_retries_then_succeeds(self, nvidia_backend, monkeypatch):
        """The classifier separation is intact: "rate limit" wording never
        trips the quota fast-fail, so transient 429s still recover."""
        _no_sleep(monkeypatch)
        monkeypatch.setenv("AGENTKTHX_MAX_API_RETRIES", "2")
        calls = []
        ok_body = (
            '{"choices":[{"message":{"role":"assistant","content":"ok"},'
            '"finish_reason":"stop"}]}'
        )

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) <= 2:
                raise _http_error(429, '{"error":{"message":"rate limit exceeded"}}')
            return io.BytesIO(json.dumps(json.loads(ok_body)).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = nvidia_backend._make_api_request({"model": "m", "messages": []}, stream=False)
        assert result["content"] == "ok"
        assert len(calls) == 3


# ---------------------------------------------------------------------------
# ROB-44 — discovery honors CLOUDFLARE_BASE_URL
# ---------------------------------------------------------------------------


class TestRob44DiscoveryHonorsBaseUrl:
    def test_default_template_url_is_unchanged(self, cf_backend):
        """Pre-fix byte-for-byte URL is preserved for the default template —
        the official-host path didn't move."""
        assert cf_backend._get_models_url() == (
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
            "/ai/models/search?per_page=100"
        )

    def test_discovery_honors_base_url_override(self, cf_backend):
        """ROB-44 core contract: an overridden base URL routes discovery to
        the SAME origin as chat traffic (was hardcoded api.cloudflare.com)."""
        cf_backend._base_url = (
            f"https://cfproxy.example.com:8443/client/v4/accounts/{CF_ACCOUNT}/ai/v1"
        )
        assert cf_backend._get_models_url() == (
            f"https://cfproxy.example.com:8443/client/v4/accounts/{CF_ACCOUNT}"
            "/ai/models/search?per_page=100"
        )

    def test_chat_and_discovery_share_the_origin(self, cf_backend):
        """Split-brain endpoints are structurally impossible: both URL
        builders resolve to the same scheme+host."""
        cf_backend._base_url = "https://gateway.internal/client/v4/accounts/X/ai/v1"
        chat = urllib.parse.urlsplit(cf_backend._get_chat_completions_url())
        models = urllib.parse.urlsplit(cf_backend._get_models_url())
        assert (chat.scheme, chat.netloc) == (models.scheme, models.netloc)

    def test_models_path_not_under_the_v1_subpath(self, cf_backend):
        """The path is still rebuilt (the endpoint lives at account-root,
        NOT under /ai/v1) — the fix must not turn into a plain append."""
        cf_backend._base_url = f"https://cfproxy.example.com/client/v4/accounts/{CF_ACCOUNT}/ai/v1"
        url = cf_backend._get_models_url()
        assert "/ai/v1/ai/models/search" not in url
        assert url.endswith(f"/accounts/{CF_ACCOUNT}/ai/models/search?per_page=100")

    def test_fallback_when_base_url_unusable(self, cf_backend):
        """Empty/unparseable _base_url falls back to the official host."""
        cf_backend._base_url = ""
        assert cf_backend._get_models_url() == (
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
            "/ai/models/search?per_page=100"
        )
        cf_backend._base_url = "not-a-url"
        assert cf_backend._get_models_url() == (
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}"
            "/ai/models/search?per_page=100"
        )


# ---------------------------------------------------------------------------
# MAINT-29 — docstrings state the real contract
# ---------------------------------------------------------------------------


class TestMaint29DocstringContract:
    def test_test_tool_support_docstrings_do_not_promise_probe(self):
        from agentkthx.plugins.cloudflare.cloudflare import CloudflareBackend
        from agentkthx.plugins.nvidia.nvidia import NvidiaBackend

        for backend in (NvidiaBackend, CloudflareBackend):
            doc = backend.test_tool_support.__doc__ or ""
            assert "probed on first use" not in doc, backend.__name__
            assert "probe is cached" not in doc, backend.__name__
            assert "MAINT-29" in doc, backend.__name__

    def test_is_free_model_docstring_does_not_claim_conservative_paid(self):
        import agentkthx.plugins.cloudflare.cloudflare as cf_module

        doc = cf_module._is_free_model.__doc__ or ""
        assert "those are conservatively treated as paid" not in doc
        assert "MAINT-29" in doc

    def test_classification_behavior_unchanged(self, nvidia_backend, cf_backend):
        """Doc fixes must not have perturbed the pattern tables."""
        # NVIDIA: reasoning names → REACT, everything else → NATIVE
        assert nvidia_backend.test_tool_support("deepseek-ai/deepseek-r1") is ToolSupportLevel.REACT
        assert nvidia_backend.test_tool_support("qwen3-14b-thinking") is ToolSupportLevel.REACT
        assert (
            nvidia_backend.test_tool_support("meta/llama-3.3-70b-instruct")
            is ToolSupportLevel.NATIVE
        )
        # Cloudflare: vision / r1 / gpt-oss → REACT, chat → NATIVE
        assert (
            cf_backend.test_tool_support("@cf/meta/llama-3.2-11b-vision-instruct")
            is ToolSupportLevel.REACT
        )
        assert cf_backend.test_tool_support("@cf/openai/gpt-oss-120b") is ToolSupportLevel.REACT
        assert (
            cf_backend.test_tool_support("@cf/meta/llama-3.3-70b-instruct-fp8-fast")
            is ToolSupportLevel.NATIVE
        )


# ---------------------------------------------------------------------------
# MAINT-30 — SUPPORT.md classifies all 10 cloud backends
# ---------------------------------------------------------------------------

FULLY_SUPPORTED = (
    "ZAI",
    "OpenRouter",
    "HuggingFace",
    "Gemini",
    "Mistral",
    "NVIDIA NIM",
    "Cloudflare",
)
# R07.29: SiliconFlow added as Limited (scaffold — pending the first
# live smoke test). Count-pins updated in the same diff, the conscious
# MAINT-30 path (a tier change is a diff, not an accident).
LIMITED = ("Pollinations", "OrcaRouter", "OpenAI", "SiliconFlow")


class TestMaint30SupportMdCompleteness:
    @staticmethod
    def _support_md() -> str:
        path = Path(__file__).resolve().parents[1] / "docs" / "SUPPORT.md"
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _section(text: str, start: str, end: str) -> str:
        return text.split(start, 1)[1].split(end, 1)[0]

    def test_nvidia_row_in_fully_supported_table(self):
        text = self._support_md()
        fully = self._section(text, "## Fully Supported", "## Limited Support")
        assert "**NVIDIA NIM**" in fully
        assert "NVIDIA_API_KEY" in fully
        assert "build.nvidia.com" in fully

    def test_all_ten_cloud_backends_classified(self):
        """R07.29: 11 cloud backends classified (7 Fully + 4 Limited —
        SiliconFlow joined Limited as a new scaffold). Historical name
        retained."""
        text = self._support_md()
        fully = self._section(text, "## Fully Supported", "## Limited Support")
        limited = self._section(text, "## Limited Support", "### ")
        for name in FULLY_SUPPORTED:
            assert name in fully, f"missing from Fully Supported: {name}"
        for name in LIMITED:
            assert name in limited, f"missing from Limited Support: {name}"

    def test_siliconflow_row_is_limited_with_promotion_path(self):
        """R07.29: the SiliconFlow scaffold row carries the honest
        status (Limited until the first live smoke test) + the
        documented promotion path (Cloudflare's R07.27 precedent)."""
        text = self._support_md()
        limited = self._section(text, "## Limited Support", "### ")
        assert "**SiliconFlow**" in limited
        assert "SILICONFLOW_API_KEY" in limited
        assert "smoke_test.sh --backend siliconflow" in limited

    def test_no_stale_backend_count(self):
        text = self._support_md()
        assert "all 9 cloud backends" not in text
        assert "all 10 cloud backends" not in text
        assert "all 11 cloud backends" in text

    def test_r0728_changelog_note_present(self):
        text = self._support_md()
        changelog = self._section(text, "## Changelog", "\n- **R07.27**")
        assert "MAINT-30" in changelog
