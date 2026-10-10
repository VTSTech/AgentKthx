"""
R07.08 Maintainability batch — regression tests for MAINT-11:

  MAINT-11 (Low): ``Path.home()`` in ``_default_roots`` +
    ``plugin_data_dir`` returns the wrong path on Windows under UAC
    impersonation / service accounts (``C:\\Windows\\System32\\config\\
    systemprofile`` instead of the user's profile). The user plugin root
    ``~/.agentkthx/plugins/`` then lands somewhere the user can't find.

    Fix: new ``PluginManager._user_home()`` helper resolves the real user
    home via ``%APPDATA%`` → ``%LOCALAPPDATA%`` → ``%USERPROFILE%`` (each
    guarded against the systemprofile leak) → ``expanduser("~")`` on
    Windows, and ``$HOME`` → ``expanduser("~")`` on POSIX. ``_default_roots``
    and ``plugin_data_dir`` both route through it.

NOTE: the audit register has an ID collision — two findings both numbered
MAINT-11. This file covers the Path.home one (Low, plugins/_loader.py).
The OrcaRouter retry-duplication one (Medium, orcarouter.py) is a separate
follow-up.

Written by VTSTech — https://www.vts-tech.org
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentkthx.plugins._loader import PluginManager

# ------------------------------------------------------------------ #
#  _user_home — POSIX (the platform these tests run on)                #
# ------------------------------------------------------------------ #


def test_user_home_posix_uses_HOME():
    """On POSIX, $HOME wins (matches the previous Path.home() behaviour)."""
    if os.name == "nt" or sys.platform == "win32":
        pytest.skip("POSIX-only test")
    saved = os.environ.pop("HOME", None)
    try:
        os.environ["HOME"] = "/tmp/fake-home-xyz"
        result = PluginManager._user_home()
        assert result == Path("/tmp/fake-home-xyz"), f"expected $HOME, got {result}"
    finally:
        if saved is not None:
            os.environ["HOME"] = saved
        else:
            os.environ.pop("HOME", None)


def test_user_home_posix_falls_back_to_expanduser():
    """If $HOME is unset, expanduser('~') is the fallback (never raises)."""
    if os.name == "nt" or sys.platform == "win32":
        pytest.skip("POSIX-only test")
    saved = os.environ.pop("HOME", None)
    try:
        result = PluginManager._user_home()
        # expanduser('~') returns *some* path — just assert it's a real Path
        assert isinstance(result, Path)
        assert result.exists() or result == Path(os.path.expanduser("~"))
    finally:
        if saved is not None:
            os.environ["HOME"] = saved


# ------------------------------------------------------------------ #
#  _user_home — Windows impersonation guard (simulated via mock)       #
# ------------------------------------------------------------------ #


@pytest.fixture
def fake_windows():
    """Force the Windows code path regardless of host platform.

    R07.32 (py311 CI fix): patching only ``os.name``/``sys.platform`` made
    ``_user_home()``'s ``Path(val)`` dispatch to a concrete ``WindowsPath``.
    pathlib 3.12+ tolerates instantiating that on POSIX (the tests passed
    there by luck), but 3.11 refuses (``NotImplementedError: cannot
    instantiate 'WindowsPath' on your system`` — the flavour check is
    import-time there). Worse: pytest formats a failure report BEFORE
    fixture teardown, so with ``os.name`` still ``"nt"`` its own
    ``Path(os.getcwd())`` in ``nodes.py`` hit the same refusal and the
    session died with INTERNALERROR instead of a clean test failure.

    Fix: also patch the ``Path`` symbol the loader module references to
    ``PureWindowsPath`` — no concrete-path dispatch happens on any
    version, on any host. ``_user_home()``'s Windows branch only builds
    paths and calls ``str()`` on them (no ``.home()``/syscalls), so pure
    paths are sufficient. This is the same technique
    ``test_plugin_data_dir_windows_uses_LOCALAPPDATA`` below already uses.
    """
    from pathlib import PureWindowsPath

    import agentkthx.plugins._loader as loader_mod

    with (
        patch("agentkthx.plugins._loader.os.name", "nt"),
        patch("agentkthx.plugins._loader.sys.platform", "win32"),
        patch.object(loader_mod, "Path", PureWindowsPath),
    ):
        yield


def test_user_home_windows_prefers_APPDATA(fake_windows):
    """%APPDATA% wins over everything else when set."""
    with patch.dict(
        os.environ,
        {
            "APPDATA": r"C:\Users\alice\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
            "USERPROFILE": r"C:\Users\alice",
        },
        clear=False,
    ):
        result = PluginManager._user_home()
    assert str(result) == r"C:\Users\alice\AppData\Roaming", f"expected APPDATA, got {result}"


def test_user_home_windows_falls_back_to_LOCALAPPDATA(fake_windows):
    """If %APPDATA% is unset, %LOCALAPPDATA% wins."""
    env = {"LOCALAPPDATA": r"C:\Users\alice\AppData\Local", "USERPROFILE": r"C:\Users\alice"}
    # APPDATA intentionally absent
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("APPDATA", None)
        result = PluginManager._user_home()
    assert str(result) == r"C:\Users\alice\AppData\Local", f"expected LOCALAPPDATA, got {result}"


def test_user_home_windows_falls_back_to_USERPROFILE(fake_windows):
    """If both APPDATA + LOCALAPPDATA are unset, USERPROFILE is trusted
    (only if it isn't the systemprofile path)."""
    env = {"USERPROFILE": r"C:\Users\alice"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("APPDATA", None)
        os.environ.pop("LOCALAPPDATA", None)
        result = PluginManager._user_home()
    assert str(result) == r"C:\Users\alice", f"expected USERPROFILE, got {result}"


def test_user_home_windows_rejects_systemprofile_via_USERPROFILE(fake_windows):
    """MAINT-11 core: if USERPROFILE points at the systemprofile path
    (the impersonation/service-account bug), it must NOT be used —
    fall through to expanduser instead."""
    env = {"USERPROFILE": r"C:\Windows\System32\config\systemprofile"}
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("APPDATA", None)
        os.environ.pop("LOCALAPPDATA", None)
        result = PluginManager._user_home()
    # Must NOT be the systemprofile path.
    assert (
        "system32\\config\\systemprofile" not in str(result).lower()
    ), f"MAINT-11 regression: returned systemprofile path {result}"


def test_user_home_windows_rejects_systemprofile_via_APPDATA(fake_windows):
    """Even if APPDATA is set, a systemprofile leak through it is rejected."""
    env = {
        "APPDATA": r"C:\Windows\System32\config\systemprofile\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\alice\AppData\Local",
    }
    with patch.dict(os.environ, env, clear=False):
        result = PluginManager._user_home()
    # Should have fallen through to LOCALAPPDATA, not the systemprofile APPDATA.
    assert (
        str(result) == r"C:\Users\alice\AppData\Local"
    ), f"MAINT-11 regression: accepted systemprofile via APPDATA: {result}"


# ------------------------------------------------------------------ #
#  _default_roots routes through _user_home                            #
# ------------------------------------------------------------------ #


def test_default_roots_user_root_uses_user_home():
    """The 'user' root must come from _user_home(), not Path.home()."""
    fake_home = Path("/tmp/fake-ak-home-xyz")
    with patch.object(PluginManager, "_user_home", return_value=fake_home):
        roots = PluginManager._default_roots()
    user_roots = [r for r in roots if r[1] == "user"]
    assert len(user_roots) == 1, f"expected one user root, got {user_roots}"
    assert (
        user_roots[0][0] == fake_home / ".agentkthx" / "plugins"
    ), f"user root not derived from _user_home(): {user_roots[0][0]}"


def test_default_roots_builtin_root_unchanged():
    """The 'builtin' root is always the package's own directory — untouched."""
    roots = PluginManager._default_roots()
    builtin = [r for r in roots if r[1] == "builtin"]
    assert len(builtin) == 1
    # builtin root is the parent of _loader.py (i.e. agentkthx/plugins/)
    expected = Path(__file__).resolve().parent.parent / "agentkthx" / "plugins"
    assert (
        builtin[0][0] == expected
    ), f"builtin root changed: expected {expected}, got {builtin[0][0]}"


# ------------------------------------------------------------------ #
#  plugin_data_dir routes through _user_home (POSIX branch)            #
# ------------------------------------------------------------------ #


def test_plugin_data_dir_posix_uses_user_home():
    """POSIX plugin_data_dir falls back to _user_home() when XDG_STATE_HOME
    is unset (was previously Path.home())."""
    if os.name == "nt" or sys.platform == "win32":
        pytest.skip("POSIX-only test")
    fake_home = Path("/tmp/fake-ak-home-xyz2")
    with (
        patch.dict(os.environ, {}, clear=False),
        patch.object(PluginManager, "_user_home", return_value=fake_home),
    ):
        os.environ.pop("XDG_STATE_HOME", None)
        result = PluginManager().plugin_data_dir("myplugin")
    assert (
        result == fake_home / ".local" / "state" / "agentkthx" / "plugins" / "myplugin"
    ), f"plugin_data_dir not using _user_home(): {result}"


def test_plugin_data_dir_posix_XDG_wins():
    """XDG_STATE_HOME still wins over _user_home (preserve existing behaviour)."""
    if os.name == "nt" or sys.platform == "win32":
        pytest.skip("POSIX-only test")
    with patch.dict(os.environ, {"XDG_STATE_HOME": "/tmp/fake-xdg"}, clear=False):
        result = PluginManager().plugin_data_dir("myplugin")
    assert result == Path(
        "/tmp/fake-xdg/agentkthx/plugins/myplugin"
    ), f"XDG_STATE_HOME didn't win: {result}"


def test_plugin_data_dir_windows_uses_LOCALAPPDATA():
    """Windows plugin_data_dir prefers %LOCALAPPDATA% (unchanged), but
    falls back through _user_home (was Path.home()) when it's unset.

    On a POSIX host we can't instantiate ``WindowsPath`` (pathlib refuses),
    so this test patches the ``Path`` referenced inside ``_loader`` to
    ``PureWindowsPath`` for the duration of the call — that's sufficient
    to verify the env-var selection logic without needing a real Windows
    interpreter. The actual Windows runtime uses concrete ``WindowsPath``.
    """
    from pathlib import PureWindowsPath

    import agentkthx.plugins._loader as loader_mod

    fake_local = r"C:\Users\alice\AppData\Local"
    pm = PluginManager.__new__(PluginManager)
    with (
        patch("agentkthx.plugins._loader.os.name", "nt"),
        patch("agentkthx.plugins._loader.sys.platform", "win32"),
        patch.object(loader_mod, "Path", PureWindowsPath),
        patch.dict(os.environ, {"LOCALAPPDATA": fake_local}, clear=False),
    ):
        result = pm.plugin_data_dir("myplugin")
    expected = PureWindowsPath(fake_local) / "agentkthx" / "plugins" / "myplugin"
    assert result == expected, f"LOCALAPPDATA not used: {result} (expected {expected})"
