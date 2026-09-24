"""
Tests for agentkthx/update_check.py — the "how will users know there's a new
version" feature. Dual release tracks:

  - Stable:  newer package on PyPI        (pypi.org/pypi/agentkthx/json)
  - Dev:     new commits on GitHub main   (api.github.com/.../commits/HEAD),
             only checked for git checkouts (pip installs have no SHA baseline)
  - Dev (R06.57): also fetches the version string from __init__.py on GitHub
             main (raw.githubusercontent.com/.../__init__.py) so pip-installed
             users can see dev releases that haven't been pushed to PyPI yet.

Covers: version parsing/comparison, git_hash extraction, per-source cache
(fresh hit / stale refetch / expiry / negative cache), opt-out env, silent
failures per source, notice formatting for both tracks.
"""

import json
import time

import pytest

from agentkthx import update_check
from agentkthx.update_check import (
    FAILURE_TTL,
    GITHUB_COMMITS_URL,
    GITHUB_RAW_INIT_URL,
    PYPI_JSON_URL,
    SUCCESS_TTL,
    base_version,
    check_for_update,
    format_notice,
    git_hash,
    is_newer,
    parse_version,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

class _FakeResponse:
    """Minimal stand-in for urllib's response object."""

    def __init__(self, payload):
        self._payload = payload
        self.closed = False

    def read(self):
        return self._payload

    def close(self):
        self.closed = True


def _pypi_payload(version):
    return json.dumps({"info": {"version": version}}).encode("utf-8")


def _github_payload(sha):
    return json.dumps({"sha": sha, "commit": {"message": "wip"}}).encode("utf-8")


def _github_init_payload(version):
    """Fake raw __init__.py body containing a __version__ assignment."""
    return (
        f'"""\n⚛️ AgentKthx R06.57\nA minimal framework.\n"""\n\n'
        f'__version__ = "{version}"  # R06.57\n'
        f'__author__ = "VTSTech"\n'
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure the opt-out env var is unset for every test unless set explicitly."""
    monkeypatch.delenv("AGENTKTHX_NO_UPDATE_CHECK", raising=False)


@pytest.fixture
def counter_urlopen(monkeypatch):
    """
    Replace the network call with a counting fake routed by URL.

    Returns (calls, setter). `calls` is a list of {"url", "timeout"} dicts;
    `setter(responder)` installs a responder(request, timeout) -> response.
    """
    calls = []

    def _set(responder):
        calls.clear()

        def _handler(request, timeout=None):
            url = getattr(request, "full_url", str(request))
            calls.append({"url": url, "timeout": timeout})
            return responder(url, timeout)

        monkeypatch.setattr(update_check, "_urlopen", _handler)

    return calls, _set


@pytest.fixture
def pip_install(monkeypatch):
    """Simulate a pip-installed copy: no git hash in __version__."""
    monkeypatch.setattr(update_check, "__version__", "0.6.51")


@pytest.fixture
def git_checkout(monkeypatch):
    """Simulate a git checkout: short hash suffix in __version__."""
    monkeypatch.setattr(update_check, "__version__", "0.6.51-f754294")


# ----------------------------------------------------------------------------
# base_version / git_hash / parse_version / is_newer
# ----------------------------------------------------------------------------

class TestBaseVersion:
    def test_strips_git_hash_suffix(self):
        assert base_version("0.6.51-f754294") == "0.6.51"

    def test_strips_local_segment(self):
        assert base_version("0.6.51+local") == "0.6.51"

    def test_plain_version_unchanged(self):
        assert base_version("0.6.41") == "0.6.41"

    def test_defaults_to_package_version(self):
        assert base_version("") == base_version(update_check.__version__)


class TestGitHash:
    @pytest.mark.parametrize("raw,expected", [
        ("0.6.51-f754294", "f754294"),
        ("0.6.51", ""),
        ("0.6.51+local", ""),
        ("0.6.51+local-abc", ""),   # local segment wins, no hash after strip
        ("", base_version(update_check.__version__).strip() and git_hash(update_check.__version__)),
    ])
    def test_extract(self, raw, expected):
        assert git_hash(raw) == expected

    def test_package_version_carries_hash_in_checkouts(self):
        # In this repo (a git checkout) the real __version__ has a suffix;
        # pip installs would not. Either way git_hash must not raise.
        assert isinstance(git_hash(), str)


class TestParseVersion:
    @pytest.mark.parametrize("raw,expected", [
        ("0.6.51", (0, 6, 51)),
        ("0.6.51-f754294", (0, 6, 51)),
        ("0.6.51+local", (0, 6, 51)),
        ("0.6", (0, 6, 0)),          # padded to 3 segments
        ("1.2.3.4", (1, 2, 3, 4)),   # longer preserved
        ("0.6.5rc1", (0, 6, 5)),     # leading digits tolerated
        ("garbage", (0, 0, 0)),      # never raises
        ("", (0, 0, 0)),
    ])
    def test_parse(self, raw, expected):
        assert parse_version(raw) == expected


class TestIsNewer:
    @pytest.mark.parametrize("latest,current,expected", [
        ("0.6.51", "0.6.50", True),
        ("0.6.50", "0.6.51", False),
        ("0.6.51", "0.6.51", False),      # equal is not newer
        ("0.7.0", "0.6.99", True),
        ("1.0.0", "0.9.9", True),
        ("0.6.51-f754294", "0.6.50", True),  # git suffix ignored
        ("0.6", "0.6.41", False),          # padded: (0,6,0) < (0,6,41)
        ("garbage", "0.6.41", False),      # unparseable -> (0,0,0), not newer
    ])
    def test_compare(self, latest, current, expected):
        assert is_newer(latest, current) is expected


# ----------------------------------------------------------------------------
# check_for_update — cache + network behavior (per source)
# ----------------------------------------------------------------------------

class TestCheckForUpdate:
    def test_opt_out_env_disables_check(self, counter_urlopen, tmp_path, pip_install):
        import os
        calls, _ = counter_urlopen
        os.environ["AGENTKTHX_NO_UPDATE_CHECK"] = "1"
        try:
            assert check_for_update(cache_file=tmp_path / "c.json") is None
            assert calls == []  # never touched the network
        finally:
            del os.environ["AGENTKTHX_NO_UPDATE_CHECK"]

    @pytest.mark.parametrize("val", ["true", "yes", "ON", "True"])
    def test_opt_out_accepts_common_truthy(self, monkeypatch, tmp_path, pip_install, val):
        monkeypatch.setenv("AGENTKTHX_NO_UPDATE_CHECK", val)
        assert check_for_update(cache_file=tmp_path / "c.json") is None

    def test_pip_install_skips_github_sha_but_fetches_init_version(
        self, counter_urlopen, tmp_path, pip_install
    ):
        """R06.57: pip installs skip the commits/SHA API but DO fetch raw __init__.py.

        github_sha stays None (no SHA baseline to compare against), but
        github_latest_version is populated from the version string parsed
        out of __init__.py on GitHub main.
        """
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.6.99"))
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        result = check_for_update(cache_file=tmp_path / "c.json")
        assert result["from_git"] is False
        assert result["github_sha"] is None              # SHA API never hit
        assert result["github_latest_version"] == "0.6.99"  # raw __init__.py was hit
        # commits/HEAD API never hit, but raw __init__.py was
        assert all("commits/HEAD" not in c["url"] for c in calls)
        assert any("raw.githubusercontent.com" in c["url"] for c in calls)

    def test_fresh_cache_hit_skips_network(self, counter_urlopen, tmp_path, pip_install):
        calls, _ = counter_urlopen
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time(),
            "pypi": {"latest_version": "9.9.9"},
        }))
        result = check_for_update(cache_file=cache)
        assert result["pypi_latest"] == "9.9.9"
        assert result["source"] == "cache"
        assert calls == []  # cache answer — zero network

    def test_legacy_single_source_cache_is_migrated(self, counter_urlopen, tmp_path, pip_install):
        """Pre-0.6.51 cache files used a flat latest_version key."""
        calls, _ = counter_urlopen
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time(),
            "latest_version": "8.8.8",
        }))
        result = check_for_update(cache_file=cache)
        assert result["pypi_latest"] == "8.8.8"
        assert result["source"] == "cache"
        assert calls == []

    def test_stale_cache_triggers_refetch(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.6.51"))
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time() - SUCCESS_TTL - 10,
            "pypi": {"latest_version": "0.6.50"},
        }))
        result = check_for_update(cache_file=cache)
        assert result["pypi_latest"] == "0.6.51" and result["source"] == "network"
        # R06.57: pip installs hit pypi + raw __init__.py = 2 calls
        assert len(calls) == 2
        # cache rewritten with the fresh per-source entry
        assert json.loads(cache.read_text())["pypi"]["latest_version"] == "0.6.51"

    def test_github_fetched_for_git_checkout(self, counter_urlopen, tmp_path, git_checkout):
        calls, set_fake = counter_urlopen
        full_sha = "f754294" + "0" * 33  # installed short hash + padding = same commit
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.6.51"))
            if "github" in url:  # commits API
                return _FakeResponse(_github_payload(full_sha))
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        result = check_for_update(cache_file=tmp_path / "c.json")
        assert result["github_sha"] == full_sha
        # R06.57: git checkouts hit pypi (1) + commits API (1) + raw __init__.py (1) = 3
        assert sum("github" in c["url"] for c in calls) == 2  # commits + raw both contain "github"
        assert sum("pypi" in c["url"] for c in calls) == 1

    def test_github_error_is_negative_cached_independently(
        self, counter_urlopen, tmp_path, git_checkout
    ):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "github" in url:  # commits API and raw both fail
                raise ConnectionError("rate limited")
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        cache = tmp_path / "c.json"
        first = check_for_update(cache_file=cache)
        assert first["pypi_latest"] == "0.6.51"      # pypi fine
        assert first["github_sha"] is None            # commits API failed silently
        assert first["github_latest_version"] is None # raw __init__.py also failed
        data = json.loads(cache.read_text())
        assert data["pypi"].get("latest_version") == "0.6.51"
        assert data["github"].get("error") is True
        assert data["github_version"].get("error") is True

        # second call: pypi from cache, github sources from negative cache — no network
        second = check_for_update(cache_file=cache)
        assert second["pypi_latest"] == "0.6.51" and second["github_sha"] is None
        # R06.57: 1 pypi + 1 commits + 1 raw __init__.py = 3 calls, all on first cycle
        assert len(calls) == 3

    def test_negative_cache_expires(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.7.0"))
            return _FakeResponse(_pypi_payload("0.7.0"))
        set_fake(_route)
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time() - FAILURE_TTL - 10,
            "pypi": {"error": True},
        }))
        result = check_for_update(cache_file=cache)
        assert result["pypi_latest"] == "0.7.0"
        # R06.57: pip installs hit pypi + raw __init__.py = 2 calls
        assert len(calls) == 2

    def test_network_failure_returns_none_value_silently(
        self, counter_urlopen, tmp_path, pip_install
    ):
        calls, set_fake = counter_urlopen
        def _boom(url, timeout=None):
            raise ConnectionError("no internet")
        set_fake(_boom)
        cache = tmp_path / "c.json"
        result = check_for_update(cache_file=cache)
        assert result is not None  # structured result, sources just empty
        assert result["pypi_latest"] is None
        assert json.loads(cache.read_text())["pypi"].get("error") is True

    def test_force_bypasses_fresh_cache(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.8.0"))
            return _FakeResponse(_pypi_payload("0.8.0"))
        set_fake(_route)
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time(),
            "pypi": {"latest_version": "9.9.9"},
        }))
        result = check_for_update(force=True, cache_file=cache)
        assert result["pypi_latest"] == "0.8.0"
        # R06.57: force=True bypasses cache for ALL sources — pypi + raw __init__.py = 2 calls
        assert len(calls) == 2

    def test_corrupt_cache_is_ignored(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.6.51"))
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        cache = tmp_path / "c.json"
        cache.write_text("{not valid json!!")
        result = check_for_update(cache_file=cache)
        assert result["pypi_latest"] == "0.6.51"
        # R06.57: corrupt cache forces both pypi and raw __init__.py to refetch = 2 calls
        assert len(calls) == 2

    def test_malformed_pypi_payload_fails_silently(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(b'{"info": {}}'))
        result = check_for_update(cache_file=tmp_path / "c.json")
        assert result["pypi_latest"] is None

    def test_timeout_is_forwarded(self, counter_urlopen, tmp_path, pip_install):
        calls, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(_pypi_payload("0.6.51")))
        check_for_update(timeout=0.25, cache_file=tmp_path / "c.json")
        assert calls and calls[0]["timeout"] == 0.25

    def test_creates_cache_parent_dir(self, counter_urlopen, tmp_path, pip_install):
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(_pypi_payload("0.6.51")))
        nested = tmp_path / "deep" / "dir" / "c.json"
        assert check_for_update(cache_file=nested) is not None
        assert nested.exists()

    def test_urls_are_the_documented_endpoints(self, counter_urlopen, tmp_path, git_checkout):
        calls, set_fake = counter_urlopen
        def _route(url, timeout=None):
            if "github" in url and "raw" not in url:
                return _FakeResponse(_github_payload("a" * 40))
            if "raw.githubusercontent.com" in url:
                return _FakeResponse(_github_init_payload("0.6.51"))
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_route)
        check_for_update(cache_file=tmp_path / "c.json")
        hit = {c["url"] for c in calls}
        assert any(u.startswith(PYPI_JSON_URL) for u in hit)
        assert any(u.startswith(GITHUB_COMMITS_URL) for u in hit)
        # R06.57: raw __init__.py URL is also hit (for both pip and git installs)
        assert any(u.startswith(GITHUB_RAW_INIT_URL) for u in hit)


# ----------------------------------------------------------------------------
# R06.57: _fetch_github_latest_version — parsing __init__.py from raw GitHub
# ----------------------------------------------------------------------------

class TestFetchGithubLatestVersion:
    """R06.57: Tests for the new pip-installed dev track."""

    def test_parses_double_quoted_version(self, counter_urlopen, monkeypatch):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(_github_init_payload("0.6.57")))
        assert _fetch_github_latest_version(timeout=1.0) == "0.6.57"

    def test_parses_single_quoted_version(self, counter_urlopen, monkeypatch):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(
            b'__version__ = \'0.6.57\'\n'
        ))
        assert _fetch_github_latest_version(timeout=1.0) == "0.6.57"

    def test_strips_git_hash_suffix_from_response(self, counter_urlopen):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(
            b'__version__ = "0.6.57-cf9f134"\n'
        ))
        # base_version() strips the suffix
        assert _fetch_github_latest_version(timeout=1.0) == "0.6.57"

    def test_raises_on_missing_assignment(self, counter_urlopen):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(
            b'"""AgentKthx"""\n__author__ = "VTSTech"\n'
        ))
        with pytest.raises(ValueError, match="no __version__"):
            _fetch_github_latest_version(timeout=1.0)

    def test_raises_on_empty_version_string(self, counter_urlopen):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        set_fake(lambda url, timeout=None: _FakeResponse(
            b'__version__ = ""\n'
        ))
        with pytest.raises(ValueError, match="empty"):
            _fetch_github_latest_version(timeout=1.0)

    def test_response_closed_after_read(self, counter_urlopen):
        from agentkthx.update_check import _fetch_github_latest_version
        _, set_fake = counter_urlopen
        resp_holder = {}
        def _track(url, timeout=None):
            r = _FakeResponse(_github_init_payload("0.6.57"))
            resp_holder["r"] = r
            return r
        set_fake(_track)
        _fetch_github_latest_version(timeout=1.0)
        assert resp_holder["r"].closed is True

    def test_cached_github_version_is_reused(self, counter_urlopen, tmp_path, pip_install):
        """Fresh github_version cache entry skips the network on next call."""
        calls, _ = counter_urlopen
        cache = tmp_path / "c.json"
        cache.write_text(json.dumps({
            "checked_at": time.time(),
            "pypi": {"latest_version": "0.6.51"},
            "github_version": {"version": "0.6.99"},
        }))
        result = check_for_update(cache_file=cache)
        assert result["github_latest_version"] == "0.6.99"
        assert result["source"] == "cache"
        assert calls == []  # zero network

    def test_github_version_error_is_negative_cached(self, counter_urlopen, tmp_path, pip_install):
        """Failed github_version fetch is cached as error for FAILURE_TTL."""
        calls, set_fake = counter_urlopen
        def _boom(url, timeout=None):
            if "raw.githubusercontent.com" in url:
                raise ConnectionError("GitHub raw down")
            return _FakeResponse(_pypi_payload("0.6.51"))
        set_fake(_boom)
        cache = tmp_path / "c.json"
        first = check_for_update(cache_file=cache)
        assert first["github_latest_version"] is None
        assert json.loads(cache.read_text())["github_version"].get("error") is True


# ----------------------------------------------------------------------------
# format_notice — stable and development tracks
# ----------------------------------------------------------------------------

class TestFormatNotice:
    def test_stable_notice_when_newer(self, monkeypatch):
        monkeypatch.setattr(update_check, "__version__", "0.6.50")
        text = format_notice({"pypi_latest": "0.6.51", "github_sha": None, "from_git": False})
        assert text is not None
        assert "0.6.50" in text and "0.6.51" in text
        assert "Stable:" in text
        assert "pip install --upgrade agentkthx" in text
        assert "Development:" not in text  # no github info -> no dev line

    def test_none_when_not_newer(self, pip_install):
        assert format_notice({"pypi_latest": "0.6.51", "github_sha": None}) is None

    def test_none_when_equal(self, pip_install):
        assert format_notice({"pypi_latest": "0.6.51", "github_sha": None}) is None

    def test_none_on_empty_result(self, pip_install):
        assert format_notice(None) is None
        assert format_notice({"pypi_latest": "", "github_sha": None}) is None

    def test_dev_notice_for_behind_git_checkout(self, git_checkout, counter_urlopen):
        # installed f754294; GitHub main is at deadbeef -> development release
        text = format_notice({"pypi_latest": "0.6.51", "github_sha": "deadbeef" + "0" * 33})
        assert text is not None
        assert "Development:" in text
        assert "deadbee" in text          # 7-char short sha shown
        assert "agentkthx update" in text

    def test_no_dev_notice_when_up_to_date_commit(self, git_checkout):
        same = "f754294" + "0" * 33
        assert format_notice({"pypi_latest": "0.6.51", "github_sha": same}) is None

    def test_no_dev_notice_for_pip_when_github_only_has_sha(self, pip_install):
        """Pip installs with only github_sha (no github_latest_version) → no dev notice.

        R06.57: This is the legacy path — the SHA-based dev track still
        only fires for git checkouts. The new github_latest_version field
        is what unlocks the pip-installed dev notice (see other tests).
        """
        text = format_notice({"pypi_latest": "0.6.51", "github_sha": "deadbeef" + "0" * 33})
        assert text is None

    def test_pip_dev_notice_when_github_init_version_is_newer(self, pip_install):
        """R06.57: pip-installed users see a dev notice when __init__.py on
        main declares a newer version than what's installed.
        """
        text = format_notice({
            "pypi_latest": "0.6.51",        # PyPI same as installed → no stable notice
            "github_sha": None,              # no SHA baseline (pip install)
            "github_latest_version": "0.6.57",
        })
        assert text is not None
        assert "Development:" in text
        assert "0.6.51" in text and "0.6.57" in text
        assert "pip install --force-reinstall git+https://github.com/VTSTech/AgentKthx.git" in text

    def test_pip_dev_notice_skipped_when_pypi_already_covers_it(self, pip_install):
        """R06.57: skip dev notice if PyPI stable is already at or beyond the dev version.

        Avoids the duplicate "you're behind" notice when stable catches up
        to dev. Example: PyPI 0.6.57, GitHub main 0.6.57, installed 0.6.54
        → stable notice fires for 0.6.57 → 0.6.54, dev notice is suppressed.
        """
        text = format_notice({
            "pypi_latest": "0.6.57",
            "github_sha": None,
            "github_latest_version": "0.6.57",
        })
        # __version__ is 0.6.51 (pip_install fixture) → stable fires,
        # dev should be suppressed because pypi already covers it.
        assert text is not None
        assert "Stable:" in text
        assert "Development:" not in text

    def test_pip_dev_notice_fires_when_dev_is_ahead_of_pypi(self, pip_install):
        """R06.57: dev notice fires when GitHub main version > PyPI latest.

        Example: PyPI 0.6.55, GitHub main 0.6.57, installed 0.6.54
        → both stable and dev notices should fire (dev is newer than stable).
        """
        text = format_notice({
            "pypi_latest": "0.6.55",
            "github_sha": None,
            "github_latest_version": "0.6.57",
        })
        assert text is not None
        assert "Stable:" in text and "Development:" in text

    def test_both_tracks_in_one_block(self, git_checkout, monkeypatch):
        monkeypatch.setattr(update_check, "__version__", "0.6.50-f754294")
        text = format_notice({"pypi_latest": "0.6.51", "github_sha": "deadbeef" + "0" * 33})
        assert text is not None
        assert "Stable:" in text and "Development:" in text
        assert text.count("agentkthx update") >= 1

    def test_git_checkout_stable_hint_uses_agentkthx_update(self, git_checkout):
        text = format_notice({"pypi_latest": "99.0.0", "github_sha": None})
        assert text is not None
        assert "pip install --upgrade" not in text
        assert "agentkthx update" in text
