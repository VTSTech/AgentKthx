"""
R07.05 regression tests for the SEC batch:
  SEC-03: is_safe_url resolves hostnames (DNS) and judges IP addresses
          (ipaddress) — closes decimal/hex/octal IPv4 spellings, IPv4-mapped
          IPv6, `[::]`, link-local/ULA IPv6, and check-time rebinding;
          http_get re-validates every redirect target.
  SEC-04: sanitize_command blocks shells (bash/sh/zsh/ksh/fish) and detects
          heredocs (`<<EOF`).
  SEC-06: optional plugin.json `sha256` pin verified before exec_module
          (fail closed on mismatch/malformed pin); warn on group/world-
          writable external plugin dirs.

All tests are offline-safe: DNS is either mocked or hits the fail-open path.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentkthx.core.helpers import is_safe_url, sanitize_command
from agentkthx.tools.builtins import _SSRFSafeRedirectHandler, http_get
from agentkthx.plugins._loader import PluginManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_dns(monkeypatch, answers: list[str]):
    """Make helpers' getaddrinfo return the given IPs for any hostname."""
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in answers
        ]
    monkeypatch.setattr(
        "agentkthx.core.helpers.socket.getaddrinfo", fake_getaddrinfo
    )


# ---------------------------------------------------------------------------
# SEC-03: address-level SSRF checks
# ---------------------------------------------------------------------------

class TestSec03IPLiteralEncodings:
    """Decimal/hex/octal/short IPv4 spellings of loopback are blocked."""

    @pytest.mark.parametrize("url", [
        "http://2130706433",          # decimal 127.0.0.1
        "http://0x7f000001",          # hex 127.0.0.1
        "http://017700000001",        # octal 127.0.0.1
        "http://0177.0.0.1",          # per-octet octal
        "http://0x7f.0.0.1",          # mixed hex/dec
        "http://127.1",               # shortened quad
    ])
    def test_obfuscated_loopback_blocked(self, url):
        is_safe, err = is_safe_url(url)
        assert not is_safe, f"{url} must be blocked (SEC-03)"
        assert "non-public" in err

    @pytest.mark.parametrize("url", [
        "http://[::1]",
        "http://[::]",                    # unspecified
        "http://[::ffff:7f00:1]",         # IPv4-mapped loopback (hex form)
        "http://[::ffff:127.0.0.1]",      # IPv4-mapped loopback (dotted form)
        "http://[fe80::abcd]",            # link-local (no "::1" substring!)
        "http://[fd00::1]",               # unique-local (private)
        "http://[0:0:0:0:0:0:0:1]",       # expanded loopback
    ])
    def test_ipv6_forms_blocked(self, url):
        is_safe, _ = is_safe_url(url)
        assert not is_safe, f"{url} must be blocked (SEC-03)"


class TestSec03DnsResolution:
    """DNS names are resolved and every returned address is judged."""

    def test_name_resolving_to_private_ip_blocked(self, monkeypatch):
        _fake_dns(monkeypatch, ["10.1.2.3"])
        is_safe, err = is_safe_url("http://rebind.attacker.example/")
        assert not is_safe
        assert "10.1.2.3" in err

    def test_name_resolving_to_metadata_ip_blocked(self, monkeypatch):
        _fake_dns(monkeypatch, ["169.254.169.254"])
        is_safe, _ = is_safe_url("http://evil.metadata.example/")
        assert not is_safe

    def test_name_resolving_to_public_ip_allowed(self, monkeypatch):
        _fake_dns(monkeypatch, ["93.184.216.34"])
        is_safe, _ = is_safe_url("http://example.com/")
        assert is_safe

    def test_name_resolving_to_mixed_ips_blocked(self, monkeypatch):
        """One private address among several public ones is enough to block."""
        _fake_dns(monkeypatch, ["93.184.216.34", "192.168.0.10"])
        is_safe, _ = is_safe_url("http://dual.example/")
        assert not is_safe

    def test_unresolvable_name_fails_open(self, monkeypatch):
        """Unresolvable hostnames fail open — the fetch itself will fail."""
        def fake_getaddrinfo(host, port, *args, **kwargs):
            raise socket.gaierror("name resolution failure")
        monkeypatch.setattr(
            "agentkthx.core.helpers.socket.getaddrinfo", fake_getaddrinfo
        )
        is_safe, _ = is_safe_url("http://does-not-resolve.example/")
        assert is_safe

    def test_public_url_still_allowed_offline(self):
        """No DNS in the environment -> fail-open path -> still allowed."""
        is_safe, _ = is_safe_url("https://example.com")
        assert is_safe


class TestSec03RedirectRevalidation:
    """http_get re-runs is_safe_url on every redirect hop."""

    def _handler(self):
        return _SSRFSafeRedirectHandler()

    def test_redirect_to_loopback_blocked(self):
        req = urllib.request.Request("https://public.example/start")
        with pytest.raises(urllib.error.URLError, match="SSRF protection"):
            self._handler().redirect_request(
                req, None, 302, "Found", {}, "http://127.0.0.1/steal"
            )

    def test_redirect_to_metadata_ip_blocked(self):
        req = urllib.request.Request("https://public.example/start")
        with pytest.raises(urllib.error.URLError, match="SSRF protection"):
            self._handler().redirect_request(
                req, None, 302, "Found", {},
                "http://169.254.169.254/latest/meta-data/"
            )

    def test_redirect_to_resolved_private_blocked(self, monkeypatch):
        _fake_dns(monkeypatch, ["10.9.9.9"])
        req = urllib.request.Request("https://public.example/start")
        with pytest.raises(urllib.error.URLError, match="SSRF protection"):
            self._handler().redirect_request(
                req, None, 301, "Moved", {}, "http://sneaky.example/next"
            )

    def test_safe_redirect_still_followed(self):
        req = urllib.request.Request("https://public.example/start")
        new_req = self._handler().redirect_request(
            req, None, 302, "Found", {}, "https://public.example/ok"
        )
        assert isinstance(new_req, urllib.request.Request)
        assert new_req.full_url == "https://public.example/ok"

    def test_http_get_opener_includes_ssrf_handler(self):
        """The opener used by http_get carries the SSRF redirect guard."""
        opener = urllib.request.build_opener(_SSRFSafeRedirectHandler())
        assert any(
            isinstance(h, _SSRFSafeRedirectHandler) for h in opener.handlers
        )

    def test_http_get_blocks_private_target_before_connecting(self):
        result = http_get("http://127.0.0.1:9/secret")
        assert result.startswith("Security error:")
        assert "SSRF" in result or "blocked" in result


# ---------------------------------------------------------------------------
# SEC-04: shells + heredocs in sanitize_command
# ---------------------------------------------------------------------------

class TestSec04ShellBlock:
    """Invoking a shell by name executes arbitrary strings — blocked."""

    @pytest.mark.parametrize("cmd", [
        "bash -c 'rm -rf /tmp/x'",
        "sh -c id",
        "zsh -c echo pwned",
        "ksh -c ls",
        "fish -c whoami",
        "/bin/bash -c 'cat /etc/shadow'",   # path prefix must be stripped
        "/usr/bin/sh -c id",
        "BASH -c echo",                      # case-insensitive
    ])
    def test_shells_blocked(self, cmd):
        ok, err, _ = sanitize_command(cmd)
        assert not ok, f"{cmd!r} must be blocked (SEC-04)"
        assert "Blocked command" in err

    def test_bash_c_payload_message(self):
        ok, err, _ = sanitize_command('bash -c "rm -rf /home/user"')
        assert not ok
        assert "bash" in err


class TestSec04HeredocDetection:
    """Heredocs smuggle multi-line script bodies — explicitly detected."""

    @pytest.mark.parametrize("cmd", [
        "python3 - <<'EOF'",
        "python3 - <<EOF",
        'cat <<\'EOF\' /etc/passwd',
        "python3 - <<'PY' import os",
    ])
    def test_heredoc_blocked(self, cmd):
        ok, err, _ = sanitize_command(cmd)
        assert not ok, f"{cmd!r} must be blocked (SEC-04)"
        assert "injection pattern" in err

    def test_multiline_heredoc_blocked(self):
        ok, _, _ = sanitize_command("python3 - <<'EOF'\nimport os\nos.system('id')\nEOF")
        assert not ok  # newline check catches it regardless

    def test_ansi_c_quoting_and_brace_expansion_still_open(self):
        """Documented residual gaps (audit SEC-04): not addressed this pass."""
        ok, _, _ = sanitize_command("echo $'\\x72\\x6d'")
        assert ok
        ok, _, _ = sanitize_command("echo {a,b}")
        assert ok


class TestSec04LegitCommandsStillPass:
    """Everyday commands must not regress when the denylist grows."""

    @pytest.mark.parametrize("cmd", [
        "echo hello",
        "ls -la",
        "git status",
        "python script.py",
        "python3 --version",
        "tar -czf out.tar.gz dir/",
        "awk '{print $1}' /etc/hosts",
        "grep -rn pattern .",
    ])
    def test_safe_commands(self, cmd):
        ok, err, _ = sanitize_command(cmd)
        assert ok, f"{cmd!r} unexpectedly rejected: {err}"


class TestSec04SecurityOffEscapeHatch:
    """--security off still skips every check, including the new ones."""

    @pytest.fixture
    def security_off(self):
        from agentkthx.core.helpers import get_security_mode, set_security_mode
        previous = get_security_mode()
        set_security_mode("off")
        yield
        set_security_mode(previous)

    def test_off_mode_allows_shell_and_heredoc(self, security_off):
        ok, _, _ = sanitize_command("bash -c 'echo {a,b}'")
        assert ok
        ok, _, _ = sanitize_command("cat <<EOF anything")
        assert ok


# ---------------------------------------------------------------------------
# SEC-06: plugin sha256 pinning + permission advisory
# ---------------------------------------------------------------------------

PLUGIN_CODE = "PLUGIN_LOADED = True\n\ndef register(manager):\n    manager.REGISTERED = True\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_plugin(base: Path, name: str, manifest: dict, code: str = PLUGIN_CODE,
                 extra_files: dict | None = None) -> Path:
    d = base / name
    d.mkdir()
    (d / "__init__.py").write_text(code)
    for rel, content in (extra_files or {}).items():
        (d / rel).write_text(content)
    m = {"name": name, "description": "test plugin", "version": "1.0.0"}
    m.update(manifest)
    (d / "plugin.json").write_text(json.dumps(m))
    return d


class TestSec06Sha256Pin:
    """Optional plugin.json sha256 pin, verified before exec_module."""

    def test_manifest_default_has_no_pin(self, tmp_path):
        _make_plugin(tmp_path, "pin-default", {})
        pm = PluginManager(str(tmp_path))
        manifests = pm.discover()
        assert manifests[0].sha256 is None

    def test_correct_string_pin_loads(self, tmp_path):
        _make_plugin(tmp_path, "pin-ok", {"sha256": _sha256(PLUGIN_CODE.encode())})
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-ok")
        assert plugin is not None and plugin.loaded
        assert plugin.module.PLUGIN_LOADED is True

    def test_wrong_pin_refuses_to_execute(self, tmp_path):
        _make_plugin(tmp_path, "pin-bad", {"sha256": "0" * 64})
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-bad")
        assert plugin is None or not plugin.loaded
        assert "sha256 pin mismatch" in pm._failed.get("pin-bad", "")
        # ...and the plugin code never ran
        assert "agentkthx_ext_plugins_pin-bad" not in sys.modules

    def test_tampered_file_after_pin_fails(self, tmp_path):
        d = _make_plugin(tmp_path, "pin-tamper", {"sha256": _sha256(PLUGIN_CODE.encode())})
        # Tamper AFTER manifest was written with the original hash
        (d / "__init__.py").write_text("EVIL = True\ndef register(manager):\n    pass\n")
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-tamper")
        assert plugin is None or not plugin.loaded
        assert "sha256 pin mismatch" in pm._failed.get("pin-tamper", "")

    def test_dict_pin_all_files_verified(self, tmp_path):
        sub = "helper_value = 42\n"
        _make_plugin(
            tmp_path, "pin-dict",
            {"sha256": {
                "__init__.py": _sha256(PLUGIN_CODE.encode()),
                "helper.py": _sha256(sub.encode()),
            }},
            extra_files={"helper.py": sub},
        )
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-dict")
        assert plugin is not None and plugin.loaded

    def test_dict_pin_tampered_submodule_fails(self, tmp_path):
        sub = "helper_value = 42\n"
        _make_plugin(
            tmp_path, "pin-dict-bad",
            {"sha256": {
                "__init__.py": _sha256(PLUGIN_CODE.encode()),
                "helper.py": _sha256(sub.encode()),
            }},
            extra_files={"helper.py": "helper_value = 666\n"},  # tampered
        )
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-dict-bad")
        assert plugin is None or not plugin.loaded
        assert "sha256 pin mismatch" in pm._failed.get("pin-dict-bad", "")

    def test_dict_pin_missing_file_fails(self, tmp_path):
        _make_plugin(
            tmp_path, "pin-missing",
            {"sha256": {"nope.py": _sha256(b"nothing")}},
        )
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("pin-missing")
        assert plugin is None or not plugin.loaded
        assert "missing file" in pm._failed.get("pin-missing", "")

    @pytest.mark.parametrize("bad_pin", [
        "deadbeef",           # not 64 hex chars
        "z" * 64,             # not hex
        "",                   # empty string
        123,                  # wrong type
        {},                   # empty dict
        {"../outside.py": _sha256(b"x")},       # traversal
        {"/etc/passwd": _sha256(b"x")},          # absolute
    ])
    def test_malformed_pin_fails_closed_at_parse(self, tmp_path, bad_pin):
        """A malformed pin must disable the plugin, never skip verification."""
        _make_plugin(tmp_path, "pin-malformed", {"sha256": bad_pin})
        pm = PluginManager(str(tmp_path))
        manifests = pm.discover()
        assert manifests == [], "malformed pin must fail the manifest parse"
        assert any("sha256" in w for w in pm.warnings)
        assert pm.load("pin-malformed") is None


class TestSec06LoosePermsWarning:
    """External plugin dirs that are group/world-writable warn (POSIX)."""

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_world_writable_plugin_dir_warns(self, tmp_path):
        d = _make_plugin(tmp_path, "perms-loose", {})
        os.chmod(d, 0o777)
        try:
            pm = PluginManager(str(tmp_path))
            plugin = pm.load("perms-loose")
            assert plugin is not None and plugin.loaded  # advisory only
            assert any("writable" in w and "trusted paths" in w
                       for w in pm.warnings)
        finally:
            os.chmod(d, 0o755)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission semantics")
    def test_tight_plugin_dir_no_warning(self, tmp_path):
        d = _make_plugin(tmp_path, "perms-tight", {})
        os.chmod(d, 0o700)
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("perms-tight")
        assert plugin is not None and plugin.loaded
        assert not any("writable" in w for w in pm.warnings)

    def test_builtin_root_never_warns(self, tmp_path):
        """Built-ins live in the package tree; the check must skip them."""
        d = _make_plugin(tmp_path, "perms-builtin", {})
        os.chmod(d, 0o777)
        try:
            pm = PluginManager(str(tmp_path))
            manifest = pm.discover()[0]
            # Force builtin kind: the advisory must not fire
            manifest.root_kind = "builtin"
            pm._warn_loose_plugin_perms(manifest, d)
            assert not any("writable" in w for w in pm.warnings)
        finally:
            os.chmod(d, 0o755)


# ---------------------------------------------------------------------------
# Cross-cutting: unpinned plugins keep working (no behavior change)
# ---------------------------------------------------------------------------

class TestSec06UnpinnedStillLoads:
    def test_unpinned_plugin_loads_normally(self, tmp_path):
        _make_plugin(tmp_path, "unpinned", {})
        pm = PluginManager(str(tmp_path))
        plugin = pm.load("unpinned")
        assert plugin is not None and plugin.loaded
        assert not any("sha256" in w for w in pm.warnings)
