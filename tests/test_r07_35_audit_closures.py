"""R07.35 (MCP-07 + audit-closure batch) — regression tests for the six
quick audit fixes shipped inside R07.35. Pins:

* SEC-21  — DDG Node helper spawns run on an env ALLOWLIST (never
  ``os.environ``): a canary secret in the parent env must NOT reach the
  subprocess, ``DDG_SOLVE_UA`` must, ``NODE_OPTIONS`` must not.
* MAINT-32 — capture proof mode is RESERVED: setting it warns loudly
  instead of silently no-op'ing, and the phantom ``ddg_capture.js``
  references are gone (constant + remediation text).
* MAINT-33 — the ``BackendType.DUCKDUCKGO`` comment teaches the shipped
  x-vqd-hash-1 protocol, not the retired x-vqd-4 handshake.
* ROB-49 — an out-of-range ``--max-steps`` on the sd backend exits with
  the clean usage-error shape, not a constructor traceback; a genuine
  bug ``ValueError`` with no sd remap still raises loudly.
* ROB-50 — SD artifact writes are exclusive-create: a same-second
  collision (model-switch rebuild / second process) bumps the counter
  instead of silently truncating an existing image.

The DDG Node subprocesses are FAKED the same way
``tests/test_duckduckgo_backend.py`` fakes them (subprocess.run patched
at module level) — no Node, no network.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentkthx.cli import agent_factory as af  # noqa: E402
from agentkthx.core import types as core_types  # noqa: E402
from agentkthx.plugins.duckduckgo import duckduckgo as ddg  # noqa: E402
from agentkthx.plugins.duckduckgo.duckduckgo import DuckDuckGoBackend  # noqa: E402
from agentkthx.plugins.stablediffusion import backend as sd_be  # noqa: E402

# ---------------------------------------------------------------------------
# SEC-21 — env allowlist on BOTH Node helper spawns
# ---------------------------------------------------------------------------


class _Proc:
    returncode = 0
    stdout = json.dumps({"ok": True, "publicJwk": {"kty": "RSA", "n": "x", "e": "AQAB"}}).encode(
        "utf-8"
    )
    stderr = b""


class TestSec21EnvAllowlist:
    """The synth proof executes server-supplied JS — the subprocess must
    see an allowlisted environment, never the parent's secrets."""

    def test_solver_env_is_allowlisted(self, monkeypatch):
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(kwargs)
            return _Proc()

        monkeypatch.setattr(ddg.subprocess, "run", fake_run)
        # Canaries a hostile challenge must NOT be able to read.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-canary-secret")
        monkeypatch.setenv("NODE_OPTIONS", "--require /tmp/evil.js")
        monkeypatch.setenv("NODE_PATH", "/tmp/evil-modules")

        b = DuckDuckGoBackend(user_agent="MyUA/1.0")
        b._node_ok = True
        b._solve_challenge("CHALLENGE-B64")

        env = captured["env"]
        assert env["DDG_SOLVE_UA"] == "MyUA/1.0"  # proof binding survives
        assert "OPENROUTER_API_KEY" not in env
        assert "NODE_OPTIONS" not in env
        assert "NODE_PATH" not in env
        assert "PATH" in env  # the helper still runs in a workable env

    def test_keygen_env_is_allowlisted(self, monkeypatch):
        """_mint_durable previously inherited the FULL environment by
        omission (no env= kwarg at all) — it must now pass the allowlist."""
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(kwargs)
            return _Proc()

        monkeypatch.setattr(ddg.subprocess, "run", fake_run)
        monkeypatch.setenv("ZAI_API_KEY", "zai-canary-secret")
        monkeypatch.setenv("DDG_SOLVE_UA", "leftover-junk")

        b = DuckDuckGoBackend(user_agent="MyUA/1.0")
        b._node_ok = True
        b._mint_durable()

        assert "env" in captured  # explicitly passed, not inherited
        env = captured["env"]
        assert "ZAI_API_KEY" not in env
        assert "DDG_SOLVE_UA" not in env  # the keygen needs no UA

    def test_allowlist_never_grows_from_parent(self, monkeypatch):
        """An arbitrary unknown var in the parent env stays out."""
        captured = {}

        def fake_run(argv, **kwargs):
            captured.update(kwargs)
            return _Proc()

        monkeypatch.setattr(ddg.subprocess, "run", fake_run)
        monkeypatch.setenv("AGENTKTHX_SOMETHING_FUTURE", "x")

        b = DuckDuckGoBackend()
        b._node_ok = True
        b._mint_durable()
        assert "AGENTKTHX_SOMETHING_FUTURE" not in captured["env"]


# ---------------------------------------------------------------------------
# MAINT-32 — capture proof mode is RESERVED, phantom helper gone
# ---------------------------------------------------------------------------


class TestMaint32CaptureReserved:
    def test_capture_mode_warns_loudly_and_constructs(self, monkeypatch, capsys):
        """Setting the env var must say 'RESERVED' on stderr instead of
        silently behaving as synth (the silent no-op was the finding)."""
        monkeypatch.setattr(ddg, "_PROOF_MODE", "capture")
        DuckDuckGoBackend()  # must not raise
        err = capsys.readouterr().err
        assert "RESERVED" in err
        assert "capture" in err and "synth" in err

    def test_synth_mode_is_quiet(self, monkeypatch, capsys):
        monkeypatch.setattr(ddg, "_PROOF_MODE", "synth")
        DuckDuckGoBackend()
        assert "RESERVED" not in capsys.readouterr().err

    def test_phantom_capture_helper_references_are_gone(self):
        """ddg_capture.js never shipped — the constant and the
        remediation text that told users to copy it must be gone."""
        assert not hasattr(ddg, "_CAPTURE_JS")
        b = DuckDuckGoBackend()
        with pytest.raises(RuntimeError) as ei:
            b._require_helper(os.path.join(ddg._PLUGIN_DIR, "definitely_missing.js"), "test")
        assert "ddg_capture" not in str(ei.value)


# ---------------------------------------------------------------------------
# MAINT-33 — enum comment teaches the shipped protocol
# ---------------------------------------------------------------------------


class TestMaint33EnumComment:
    def test_duckduckgo_enum_comment_matches_shipped_protocol(self):
        src = Path(core_types.__file__).read_text(encoding="utf-8")
        # The retired handshake must not be taught as current anywhere in
        # the contract surface, and the shipped proof protocol must be.
        assert "x-vqd-4" not in src
        assert "x-vqd-hash-1" in src


# ---------------------------------------------------------------------------
# ROB-49 — clean usage error for out-of-range --max-steps on sd
# ---------------------------------------------------------------------------


class TestRob49CleanMaxStepsError:
    @staticmethod
    def _args(max_steps):
        # security="max" (the default) on purpose: _build_agent applies
        # set_security_mode() as a GLOBAL side effect before the backend
        # construction — "off" here would leak into every later test in
        # the full suite (the existing sd factory tests use "max" too).
        return argparse.Namespace(
            backend="sd",
            model="sd_turbo",
            security="max",
            timeout=None,
            api_mode=None,
            max_steps=max_steps,
        )

    def test_out_of_range_becomes_clean_usage_error(self, monkeypatch):
        def fake_get_backend(name, timeout=None, api_mode=None, **kwargs):
            if "sample_steps" in kwargs:
                raise ValueError(
                    "sample_steps must be an integer in 1..100 "
                    "(sd-server clamp range), got 200"
                )
            raise AssertionError("expected the sample_steps construction path")

        monkeypatch.setattr(af, "get_backend", fake_get_backend)
        with pytest.raises(SystemExit) as ei:
            af._build_agent(self._args(200), SimpleNamespace())
        msg = str(ei.value.code)
        assert "--max-steps" in msg
        assert "sample_steps" in msg
        assert "got 200" in msg  # the original diagnostic survives

    def test_genuine_bug_valueerror_still_raises_loudly(self, monkeypatch):
        """No sd remap in play (max_steps unset) — the narrow guard must
        NOT convert an unrelated constructor ValueError into SystemExit."""

        def fake_get_backend(name, timeout=None, api_mode=None, **kwargs):
            raise ValueError("genuine bug")

        monkeypatch.setattr(af, "get_backend", fake_get_backend)
        with pytest.raises(ValueError, match="genuine bug"):
            af._build_agent(self._args(None), SimpleNamespace())


# ---------------------------------------------------------------------------
# ROB-50 — artifact writes are exclusive-create
# ---------------------------------------------------------------------------


class TestRob50ArtifactCollision:
    @pytest.fixture(autouse=True)
    def _frozen_clock(self, monkeypatch):
        """Pin the UTC stamp so both writes land on the same second —
        the exact collision shape from the finding."""
        fixed = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

        class _FrozenDT:
            @staticmethod
            def now(tz=None):
                return fixed

        monkeypatch.setattr(sd_be, "datetime", _FrozenDT)

    def test_second_instance_same_second_does_not_overwrite(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENTKTHX_ARTIFACTS_DIR", str(tmp_path))
        stub1 = SimpleNamespace(_artifact_counter=0)
        p1 = sd_be.StableDiffusionBackend._write_artifact(stub1, b"IMAGE-ONE")
        assert p1.name == "sd_20261024-120000_001.png"

        # A model-switch rebuild: NEW backend instance, per-instance
        # counter restarts at 001, same wall-clock second.
        stub2 = SimpleNamespace(_artifact_counter=0)
        p2 = sd_be.StableDiffusionBackend._write_artifact(stub2, b"IMAGE-TWO")

        assert p2 != p1
        assert p2.name == "sd_20261024-120000_002.png"  # bumped, not clobbered
        assert p1.read_bytes() == b"IMAGE-ONE"  # the first image survived
        assert stub2._artifact_counter == 2
        assert stub1._artifact_counter == 1

    def test_pre_existing_file_never_truncated(self, monkeypatch, tmp_path):
        """Even a foreign file wearing the generated name blocks the
        write instead of being overwritten."""
        monkeypatch.setenv("AGENTKTHX_ARTIFACTS_DIR", str(tmp_path))
        (tmp_path / "sd_20261024-120000_001.png").write_bytes(b"PRECIOUS")

        stub = SimpleNamespace(_artifact_counter=0)
        p = sd_be.StableDiffusionBackend._write_artifact(stub, b"NEW-IMAGE")

        assert p.name == "sd_20261024-120000_002.png"
        assert (tmp_path / "sd_20261024-120000_001.png").read_bytes() == b"PRECIOUS"
