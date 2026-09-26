"""
Tests for the version git-suffix resolution (ROB-07 fix).

Covers agentkthx/__init__.py::_get_git_short_hash:
  1. Live verified checkout   — .git found + remote.origin.url is ours
  2. Baked build metadata     — agentkthx._git_meta.SOURCE_COMMIT (setup.py)
  3. Plain version            — nothing trusted, no suffix

Guards the misattribution bug: a pip-installed package sitting inside an
unrelated git repository must never report that repository's hash.
"""

import subprocess
import sys
import types

import pytest

import agentkthx


def _proc(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr="")


def _point_into(monkeypatch, tmp_path, has_git_at=(), git_is_file=False):
    """Point agentkthx.__file__ at <tmp>/pkg/agentkthx/__init__.py and
    optionally plant .git markers at the given relative parents."""
    pkg_dir = tmp_path / "pkg" / "agentkthx"
    pkg_dir.mkdir(parents=True)
    init = pkg_dir / "__init__.py"
    init.write_text("", encoding="utf-8")
    monkeypatch.setattr(agentkthx, "__file__", str(init))
    for rel in has_git_at:
        d = pkg_dir
        for _ in range(rel):
            d = d.parent
        if git_is_file:
            d.joinpath(".git").write_text("gitdir: /nowhere\n", encoding="utf-8")
        else:
            d.joinpath(".git").mkdir()
    return pkg_dir


def _mock_git(monkeypatch, origin_url=None, origin_rc=0,
              describe_stdout="", describe_rc=0):
    """Mock subprocess.run for the two git queries the resolver makes."""
    def fake_run(cmd, **_kwargs):
        if "get-url" in cmd or "remote" in cmd:
            return _proc(origin_url or "", origin_rc)
        if "describe" in cmd:
            return _proc(describe_stdout, describe_rc)
        return _proc("", 1)
    monkeypatch.setattr(subprocess, "run", fake_run)


def _bake_meta(monkeypatch, commit):
    fake = types.ModuleType("agentkthx._git_meta")
    fake.SOURCE_COMMIT = commit
    monkeypatch.setitem(sys.modules, "agentkthx._git_meta", fake)


def _unbake(monkeypatch):
    monkeypatch.setitem(sys.modules, "agentkthx._git_meta", None)


class TestLiveVerifiedCheckout:
    def test_https_remote_accepted(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch,
                  origin_url="https://github.com/VTSTech/AgentKthx.git",
                  describe_stdout="acf1d72\n")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_ssh_remote_accepted(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch,
                  origin_url="git@github.com:VTSTech/AgentKthx.git",
                  describe_stdout="acf1d72\n")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_dirty_marker_passed_through(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch,
                  origin_url="https://github.com/VTSTech/AgentKthx.git",
                  describe_stdout="acf1d72-dirty\n")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == "acf1d72-dirty"

    def test_git_file_worktree_marker_detected(self, monkeypatch, tmp_path):
        # .git may be a FILE (git worktrees / submodules), not a directory
        _point_into(monkeypatch, tmp_path, has_git_at=[1], git_is_file=True)
        _mock_git(monkeypatch,
                  origin_url="https://github.com/VTSTech/AgentKthx.git",
                  describe_stdout="acf1d72\n")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == "acf1d72"


class TestForeignRepoRejected:
    def test_foreign_remote_falls_to_baked(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch, origin_url="https://github.com/someone/else.git")
        _bake_meta(monkeypatch, "acf1d72")
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_foreign_remote_no_baked_gives_plain(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch, origin_url="https://github.com/someone/else.git")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == ""

    def test_missing_origin_falls_to_baked(self, monkeypatch, tmp_path):
        # A local-only repo (no remotes) must not be trusted either
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch, origin_rc=1)
        _bake_meta(monkeypatch, "acf1d72")
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_walk_stops_at_foreign_git(self, monkeypatch, tmp_path):
        # Foreign .git at the package dir level; our real repo would be one
        # level higher — the resolver must STOP at the foreign .git, not walk
        # past it into deeper parents.
        _point_into(monkeypatch, tmp_path, has_git_at=[0])
        _mock_git(monkeypatch, origin_url="https://github.com/someone/else.git")
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == ""

    def test_no_git_within_three_levels(self, monkeypatch, tmp_path):
        # Deep foreign .git (like a venv nested in a user repo) is never even
        # reached: walk-up is capped at 3 levels.
        pkg_dir = tmp_path / "a" / "b" / "c" / "pkg" / "agentkthx"
        pkg_dir.mkdir(parents=True)
        init = pkg_dir / "__init__.py"
        init.write_text("", encoding="utf-8")
        monkeypatch.setattr(agentkthx, "__file__", str(init))
        tmp_path.joinpath(".git").mkdir()  # 5 levels above the package dir
        _mock_git(monkeypatch)
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == ""


class TestBakedBuildMetadata:
    def test_baked_commit_used_when_no_git(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path)
        _mock_git(monkeypatch)
        _bake_meta(monkeypatch, "acf1d72")
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_baked_none_gives_plain(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path)
        _mock_git(monkeypatch)
        _bake_meta(monkeypatch, None)
        assert agentkthx._get_git_short_hash() == ""

    def test_no_meta_module_gives_plain(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path)
        _mock_git(monkeypatch)
        _unbake(monkeypatch)
        assert agentkthx._get_git_short_hash() == ""


class TestDegradation:
    def test_git_binary_missing(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])

        def no_git(*_a, **_k):
            raise FileNotFoundError("git not installed")
        monkeypatch.setattr(subprocess, "run", no_git)
        _bake_meta(monkeypatch, "acf1d72")
        # Subprocess failure is swallowed; baked value still available
        assert agentkthx._get_git_short_hash() == "acf1d72"

    def test_describe_failure_falls_to_baked(self, monkeypatch, tmp_path):
        _point_into(monkeypatch, tmp_path, has_git_at=[1])
        _mock_git(monkeypatch,
                  origin_url="https://github.com/VTSTech/AgentKthx.git",
                  describe_rc=1)
        _bake_meta(monkeypatch, "acf1d72")
        assert agentkthx._get_git_short_hash() == "acf1d72"


class TestVersionSuffixFormat:
    def test_base_version_still_parses_with_dirty(self):
        from agentkthx.update_check import base_version, git_hash
        assert base_version("0.7.00-acf1d72-dirty") == "0.7.00"
        assert git_hash("0.7.00-acf1d72-dirty") == "acf1d72-dirty"
        assert base_version("0.7.00-acf1d72") == "0.7.00"


@pytest.mark.parametrize("suffix", ["acf1d72", "acf1d72-dirty"])
def test_banner_style_split_survives_suffix(suffix):
    # banner.py does __version__.split('.') then R{parts[1]}.{parts[2]}
    v = f"0.7.00-{suffix}"
    parts = v.split(".")
    display = f"R{int(parts[1]):02d}.{parts[2]}"
    assert display == f"R07.00-{suffix}"
