"""
R07.12 closure-batch regression tests.

Closures shipped in this release (code fixes):

- SEC-17 — ``_SSRFSafeRedirectHandler`` now enforces an explicit per-request
  redirect-hop budget (5), bounding the per-hop SSRF validation cost an
  attacker controls.
- ROB-27 — DNS resolution in the SSRF validator runs on a daemon thread with
  a wall-clock budget (``_DNS_RESOLVE_TIMEOUT_SECONDS``); a timed-out lookup
  fails CLOSED in ``is_safe_url`` instead of stalling the agent outside the
  HTTP request timeout.
- ROB-23 — OrcaRouter free-model detection now honors the live upstream
  ``-free`` suffix convention, so new free models surface under
  ``ORCAROUTER_FREE_ONLY`` without a code update; the static whitelist
  remains the outage-fallback floor.
- ROB-24 — ZAI ``get_model_info`` marks unknown-model placeholder entries
  with ``catalog_status: "unknown"``, warns under ``AGENTKTHX_DEBUG``, and
  suggests close catalog matches for likely typos.

Also decided this release (no code change): SEC-18 and SEC-19 are WONTFIX —
trusted first-party providers; the response channel strictly dominates the
error channel, and backend error prose terminates at the human terminal.

DNS record cap (``_MAX_DNS_RECORDS``) is covered here as part of ROB-27's
bounded-work contract; it also materially mitigates SEC-11.
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core import helpers as core_helpers
from agentkthx.core.helpers import (
    _DNS_RESOLVE_TIMEOUT_SECONDS,
    _MAX_DNS_RECORDS,
    _iter_hostname_ips,
    _resolve_hostname_bounded,
    is_safe_url,
)
from agentkthx.tools.builtins import _SSRFSafeRedirectHandler


# ---------------------------------------------------------------------------
# ROB-27 — bounded DNS resolution
# ---------------------------------------------------------------------------


class TestBoundedDnsResolution:
    """_resolve_hostname_bounded: wall-clock budget on getaddrinfo."""

    def test_timeout_returns_none_fast(self, monkeypatch):
        """A getaddrinfo slower than the budget returns None quickly."""
        monkeypatch.setattr(core_helpers, "_DNS_RESOLVE_TIMEOUT_SECONDS", 0.25)

        def slow_getaddrinfo(host, port):
            time.sleep(1.0)
            return []

        monkeypatch.setattr(core_helpers.socket, "getaddrinfo", slow_getaddrinfo)
        start = time.monotonic()
        result = _resolve_hostname_bounded("slow-dns.example.com")
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.9  # bounded well below the 1.0s sleep

    def test_success_returns_address_list(self, monkeypatch):
        """A fast getaddrinfo returns the resolved addresses."""

        def fast_getaddrinfo(host, port):
            # getaddrinfo tuple shape: (family, type, proto, canonname, sockaddr)
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(core_helpers.socket, "getaddrinfo", fast_getaddrinfo)
        assert _resolve_hostname_bounded("example.com") == ["93.184.216.34"]

    def test_gaierror_maps_to_empty_fail_open(self, monkeypatch):
        """Genuine resolution failure (NXDOMAIN) stays fail-open (empty)."""

        def failing_getaddrinfo(host, port):
            raise core_helpers.socket.gaierror("Name or service not known")

        monkeypatch.setattr(core_helpers.socket, "getaddrinfo", failing_getaddrinfo)
        assert _resolve_hostname_bounded("nonexistent.invalid") == []

    def test_record_cap_truncates(self, monkeypatch):
        """_iter_hostname_ips examines at most _MAX_DNS_RECORDS records."""
        hundred_public = [f"93.184.{i // 256}.{i % 256}" for i in range(100)]
        monkeypatch.setattr(
            core_helpers, "_resolve_hostname_bounded", lambda host: hundred_public
        )
        assert len(_iter_hostname_ips("big-record-set.example.com")) == _MAX_DNS_RECORDS

    def test_dns_timeout_fails_closed_in_is_safe_url(self, monkeypatch):
        """The __DNS_TIMEOUT__ sentinel makes is_safe_url fail CLOSED."""
        monkeypatch.setattr(
            core_helpers, "_iter_hostname_ips", lambda host: ["__DNS_TIMEOUT__"]
        )
        safe, error = is_safe_url("http://stalled-resolver.example.com/")
        assert safe is False
        assert "timed out" in error

    def test_unresolvable_still_fail_open(self, monkeypatch):
        """Pre-R07.12 fail-open contract for unresolvable names is preserved."""
        monkeypatch.setattr(core_helpers, "_resolve_hostname_bounded", lambda host: [])
        safe, error = is_safe_url("http://nonexistent.invalid/")
        assert safe is True
        assert error == ""

    def test_ip_literals_never_touch_dns(self, monkeypatch):
        """Literals short-circuit before the DNS path entirely."""

        def boom(host):
            raise AssertionError("DNS resolver must not be called for IP literals")

        monkeypatch.setattr(core_helpers, "_resolve_hostname_bounded", boom)
        assert _iter_hostname_ips("127.0.0.1") == ["127.0.0.1"]
        assert _iter_hostname_ips("::1") == ["::1"]

    def test_timeout_constant_is_bounded(self):
        """The default budget stays a small, sane number (regression pin)."""
        assert 1.0 <= _DNS_RESOLVE_TIMEOUT_SECONDS <= 10.0
        assert 8 <= _MAX_DNS_RECORDS <= 64


# ---------------------------------------------------------------------------
# SEC-17 — redirect hop budget
# ---------------------------------------------------------------------------


def _fake_req(url: str = "http://example.com/start") -> urllib.request.Request:
    return urllib.request.Request(url)


class TestRedirectHopCap:
    """_SSRFSafeRedirectHandler: explicit per-request hop budget."""

    def test_five_hops_allowed_then_sixth_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "agentkthx.tools.builtins.is_safe_url", lambda url: (True, "")
        )
        handler = _SSRFSafeRedirectHandler()
        for i in range(5):
            out = handler.redirect_request(
                _fake_req(), None, 302, "Found", {}, f"http://example.com/hop{i}"
            )
            assert isinstance(out, urllib.request.Request)
        with pytest.raises(urllib.error.URLError, match="exceeded 5 hops"):
            handler.redirect_request(
                _fake_req(), None, 302, "Found", {}, "http://example.com/hop5"
            )

    def test_budget_is_per_handler_instance(self, monkeypatch):
        """Each request builds a fresh handler — budgets don't leak across."""
        monkeypatch.setattr(
            "agentkthx.tools.builtins.is_safe_url", lambda url: (True, "")
        )
        first = _SSRFSafeRedirectHandler()
        for _ in range(5):
            first.redirect_request(
                _fake_req(), None, 302, "Found", {}, "http://example.com/a"
            )
        # New handler (new request): budget resets
        second = _SSRFSafeRedirectHandler()
        out = second.redirect_request(
            _fake_req(), None, 302, "Found", {}, "http://example.com/b"
        )
        assert isinstance(out, urllib.request.Request)

    def test_unsafe_target_still_blocked_first_hop(self, monkeypatch):
        """SEC-03 behavior preserved: a private redirect target is refused."""
        monkeypatch.setattr(
            "agentkthx.tools.builtins.is_safe_url",
            lambda url: (False, "hostname resolves to a non-public address"),
        )
        handler = _SSRFSafeRedirectHandler()
        with pytest.raises(urllib.error.URLError, match="blocked"):
            handler.redirect_request(
                _fake_req(), None, 302, "Found", {}, "http://127.0.0.1/evil"
            )

    def test_max_hops_constant_is_tight(self):
        """The explicit budget stays tighter than urllib's own 10."""
        assert _SSRFSafeRedirectHandler._MAX_HOPS < 10


# ---------------------------------------------------------------------------
# ROB-23 — live free-model detection (OrcaRouter)
# ---------------------------------------------------------------------------


class TestOrcaRouterLiveFreeDetection:
    """-free suffix convention makes the live catalog authoritative."""

    def test_new_live_free_model_is_free(self):
        """A brand-new -free model not in the whitelist classifies free."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model

        assert _is_free_model("qwen/qwen4-free") is True
        assert _is_free_model("deepseek/new-release-free") is True

    def test_suffix_detection_is_case_insensitive(self):
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model

        assert _is_free_model("Qwen/Qwen4-FREE") is True

    def test_paid_models_still_not_free(self):
        """Regression: paid IDs without -free remain not-free."""
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model

        assert _is_free_model("openai/gpt-4o-mini") is False
        assert _is_free_model("anthropic/claude-sonnet-4.6") is False
        assert _is_free_model("orcarouter/auto") is False
        assert _is_free_model("nonexistent/model") is False

    def test_whitelist_floor_still_recognized(self):
        from agentkthx.plugins.orcarouter.orcarouter import _is_free_model

        assert _is_free_model("tencent/hy3-free") is True  # whitelist AND suffix
        assert _is_free_model("orcarouter/free") is True  # named router

    @pytest.fixture
    def orca_free_env(self, monkeypatch):
        """OrcaRouterBackend with FREE_ONLY on, cache cold."""
        monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test-key-1234567890")
        from agentkthx.plugins.orcarouter import orcarouter as _orca_mod

        monkeypatch.setattr(_orca_mod, "ORCAROUTER_FREE_ONLY", True, raising=False)
        backend = _orca_mod.OrcaRouterBackend()
        backend._model_cache = None
        return backend

    @staticmethod
    def _fake_urlopen(payload: dict):
        mock_response = MagicMock()
        mock_response.read.return_value = __import__("json").dumps(payload).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        return MagicMock(return_value=mock_response)

    def test_free_only_listing_includes_new_live_free_model(
        self, orca_free_env, monkeypatch
    ):
        """FREE_ONLY over a live feed surfaces NEW -free models (ROB-23)."""
        feed = {
            "object": "list",
            "data": [
                {"id": "openai/gpt-4o-mini", "owned_by": "openai"},
                {"id": "deepseek/deepseek-v4-flash-free", "owned_by": "deepseek"},
                # NEW upstream free model — NOT in the static whitelist:
                {"id": "qwen/qwen4-free", "owned_by": "qwen"},
            ],
        }
        monkeypatch.setattr(
            urllib.request, "urlopen", self._fake_urlopen(feed)
        )
        models = orca_free_env.list_models()
        names = {m["name"] for m in models}
        assert "qwen/qwen4-free" in names  # the ROB-23 assertion
        assert "deepseek/deepseek-v4-flash-free" in names
        assert "orcarouter/free" in names
        assert "openai/gpt-4o-mini" not in names
        assert "orcarouter/auto" not in names

    def test_outage_fallback_is_static_floor(self, orca_free_env, monkeypatch):
        """On discovery failure the fallback stays the static whitelist+routers."""

        def down(req, timeout=None):
            raise urllib.error.URLError("catalog unreachable")

        monkeypatch.setattr(urllib.request, "urlopen", down)
        models = orca_free_env.list_models()
        names = {m["name"] for m in models}
        # Whitelist floor + free router only — documented outage behavior.
        assert "deepseek/deepseek-v4-flash-free" in names
        assert "orcarouter/free" in names
        assert len(names) == 5  # 4 whitelist models + free router


# ---------------------------------------------------------------------------
# ROB-24 — honest ZAI placeholder entries
# ---------------------------------------------------------------------------


def _make_zai_backend():
    """ZaiBackend without network or __init__ side effects (house pattern)."""
    from agentkthx.plugins.zai.zai import ZaiBackend

    b = ZaiBackend.__new__(ZaiBackend)
    b._base_url = "https://api.z.ai"
    b._api_key = "test-key"
    return b


class TestZaiPlaceholderHonesty:
    """get_model_info: unknown models are marked, not silently fabricated."""

    def test_unknown_model_marked_unknown(self):
        b = _make_zai_backend()
        info = b.get_model_info("totally-unknown-model")
        assert info is not None
        assert info["details"]["catalog_status"] == "unknown"
        assert info["details"]["context_length"] == 128000
        assert info["details"]["free_tier"] is False

    def test_known_catalog_entry_not_marked_unknown(self):
        b = _make_zai_backend()
        info = b.get_model_info("glm-5.3")
        assert info is not None
        assert info["details"].get("catalog_status") != "unknown"

    def test_debug_warning_fires_with_typo_hint(self, monkeypatch, capsys):
        """AGENTKTHX_DEBUG prints the placeholder warning + close match."""
        monkeypatch.setenv("AGENTKTHX_DEBUG", "1")
        b = _make_zai_backend()
        info = b.get_model_info("glm-5.3-flas")  # typo of glm-5.3-flash
        out = capsys.readouterr().out
        assert "not in static catalog" in out
        assert "did you mean 'glm-5.3-flash'" in out
        assert info["details"]["catalog_status"] == "unknown"

    def test_no_debug_output_by_default(self, monkeypatch, capsys):
        monkeypatch.delenv("AGENTKTHX_DEBUG", raising=False)
        b = _make_zai_backend()
        b.get_model_info("some-other-unknown-model")
        out = capsys.readouterr().out
        assert "not in static catalog" not in out

    def test_provider_prefix_stripped_for_placeholder(self):
        b = _make_zai_backend()
        info = b.get_model_info("zai/unknown-future-model")
        assert info["name"] == "unknown-future-model"
        assert info["details"]["catalog_status"] == "unknown"
