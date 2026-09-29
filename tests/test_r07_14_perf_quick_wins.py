"""R07.14 — performance quick-wins release regression tests.

Four audit findings closed (the PERF batch). This
file pins every closure in the house per-release style:

- PERF-01  Memory.get_messages() only re-runs the O(n × tool_calls)
           sanitize_history repair after a mutation — the idempotent
           two-pass repair used to run on EVERY call (per agentic step)
           even when nothing had changed
- PERF-02  CompactionMixin's threshold check + running-token snapshot
           route through Memory.estimated_chars() (cache invalidated on
           every mutation) instead of three full history scans — each
           with a json.dumps per assistant tool_calls message — per step;
           foreign duck-typed memories keep the fallback scan
- PERF-04  PluginManager.discover(force=True) re-uses manifests whose
           plugin.json mtime is unchanged and re-parses only new/modified
           files; removed plugins drop out of results AND the cache
- PERF-06  update_check._fetch_json reads the PyPI response through a
           256KB cap parsed directly from bytes — the ~94KB document
           (live-measured 2026-09-29, README included) parses
           byte-identically, a larger body fails the per-source check
           silently, and no unbounded bytes+str pair is ever held

(PERF-03 is NOT in this batch — its DuckDuckGo recommendation was
corrected in the register instead: DDG has no JSON web-search API.)
"""

import json
from pathlib import Path

import pytest

import agentkthx.core.compaction as compaction_module
from agentkthx.core.compaction import CompactionMixin
from agentkthx.core.memory import Memory
from agentkthx.plugins import _loader as loader_module
from agentkthx.plugins._loader import PluginManager
from agentkthx import update_check


# ---------------------------------------------------------------------------
# PERF-01 — sanitize_history runs once per mutation, not once per call
# ---------------------------------------------------------------------------

class TestPerf01SanitizeCache:
    def _counting_sanitize(self, monkeypatch):
        """Wrap Memory.sanitize_history with a call counter."""
        calls = {"n": 0}
        original = Memory.sanitize_history

        def counting(self):
            calls["n"] += 1
            return original(self)

        monkeypatch.setattr(Memory, "sanitize_history", counting)
        return calls

    def test_get_messages_twice_sanitizes_once(self, monkeypatch):
        """Two back-to-back get_messages() with NO mutation in between:
        the old code ran the two-pass repair on both; now only the first
        call pays (the state it produced is already sanitized)."""
        calls = self._counting_sanitize(monkeypatch)
        mem = Memory()
        mem.add("user", "hello")
        mem.add("assistant", "hi")
        assert calls["n"] == 0  # add() marks dirty; nothing sanitized yet

        mem.get_messages()
        assert calls["n"] == 1
        mem.get_messages()
        mem.get_messages()
        assert calls["n"] == 1, "clean state must NOT re-run sanitize_history"

    def test_mutation_reenables_sanitization(self, monkeypatch):
        """add() after a sanitize must flip the flag — a NEW orphan added
        after the first get_messages() is still dropped by the next one."""
        calls = self._counting_sanitize(monkeypatch)
        mem = Memory()
        mem.add("user", "q")
        mem.get_messages()
        first = calls["n"]
        assert first == 1

        mem.add_tool_result("orphan-1", "shell", "stale result")
        assert calls["n"] == first, "add() flags dirty but does not sanitize"
        out = mem.get_messages()
        assert calls["n"] == first + 1, "mutation must re-enable sanitization"
        roles = [m["role"] for m in out]
        assert "tool" not in roles, "new orphan must still be dropped"

    def test_placeholder_refilled_after_remutation(self, monkeypatch):
        """A dangling tool_call added after the first sanitize still gets
        its placeholder tool result on the next get_messages()."""
        calls = self._counting_sanitize(monkeypatch)
        mem = Memory()
        mem.add("user", "q")
        mem.get_messages()
        first = calls["n"]

        mem.add_tool_call("assistant", "", [
            {"id": "call_9", "name": "shell", "arguments": {"command": "ls"}}
        ])
        out = mem.get_messages()
        assert calls["n"] == first + 1
        tool_msgs = [m for m in out if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_9"
        assert "no result was recorded" in tool_msgs[0]["content"]

    def test_clear_reenables_sanitization(self, monkeypatch):
        calls = self._counting_sanitize(monkeypatch)
        mem = Memory()
        mem.add("user", "q")
        mem.get_messages()
        assert calls["n"] == 1

        mem.clear()
        mem.add("user", "next")
        mem.get_messages()
        assert calls["n"] == 2, "clear() must invalidate the sanitize cache"


# ---------------------------------------------------------------------------
# PERF-02 — cached size estimate for the compaction heuristic
# ---------------------------------------------------------------------------

class _Msg:
    """Minimal message stand-in matching the compaction host contract."""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _ForeignHost(CompactionMixin):
    """Host whose memory does NOT carry Memory.estimated_chars — must keep
    working via the fallback scan (the documented duck-typed contract)."""

    def __init__(self, messages, num_ctx=8192, threshold=0.85):
        self._messages = list(messages)
        self.num_ctx = num_ctx
        self._compaction_threshold = threshold
        self._running_tokens_in = 0
        self._running_tokens_out = 0

    def __iter__(self):
        return iter(self._messages)

    @property
    def memory(self):
        return self


class _MemoryHost(CompactionMixin):
    """Host backed by a REAL Memory object (the cache path)."""

    def __init__(self, memory: Memory, num_ctx=8192, threshold=0.85):
        self.memory = memory
        self.num_ctx = num_ctx
        self._compaction_threshold = threshold
        self._running_tokens_in = 0
        self._running_tokens_out = 0


class TestPerf02EstimatedChars:
    def test_estimated_chars_matches_manual_scan(self):
        mem = Memory()
        mem.add("user", "abcd" * 10)  # 40 chars
        mem.add_tool_call("assistant", "ok", [
            {"id": "c1", "name": "shell", "arguments": {"command": "ls"}}
        ])
        mem.add_tool_result("c1", "shell", "out")
        manual = 0
        for m in mem:
            manual += len(m.content or "")
            if m.tool_calls:
                manual += len(json.dumps(m.tool_calls, ensure_ascii=False))
        assert mem.estimated_chars() == manual

    def test_estimate_cached_until_mutation(self):
        """The cache is a snapshot: in-place content pokes do NOT move it
        (they'd be invisible to the compaction heuristic anyway — the
        documented contract is invalidate-on-mutation), while add() does."""
        mem = Memory()
        mem.add("user", "hello")
        first = mem.estimated_chars()
        assert mem.estimated_chars() == first  # warm hit

        mem._messages[0].content = "x" * 10_000  # in-place poke
        assert mem.estimated_chars() == first, (
            "cache must hold until an invalidating mutation")

        mem.add("user", "more")
        assert mem.estimated_chars() > first, "add() must invalidate"

    def test_check_compaction_zero_scans_on_warm_cache(self, monkeypatch):
        """Warm cache + no mutations: _check_compaction and
        _snapshot_running_tokens must perform ZERO json.dumps calls —
        the old code did two full scans (one dumps per tool_call message)
        on this every-step path."""
        dumps_calls = {"n": 0}
        real_dumps = compaction_module.json.dumps

        def counting_dumps(*a, **kw):
            dumps_calls["n"] += 1
            return real_dumps(*a, **kw)

        monkeypatch.setattr(compaction_module.json, "dumps", counting_dumps)

        mem = Memory()
        mem.add("user", "x" * 100)
        mem.add_tool_call("assistant", "ok", [
            {"id": "c1", "name": "shell", "arguments": {"command": "ls"}}
        ])
        mem.add_tool_result("c1", "shell", "out")
        host = _MemoryHost(mem)
        mem.estimated_chars()  # warm the cache
        dumps_calls["n"] = 0

        assert host._check_compaction() == 0
        host._snapshot_running_tokens()
        assert dumps_calls["n"] == 0, (
            "warm-cache compaction check must not rescan (no json.dumps)")

    def test_check_compaction_recomputes_once_after_mutation(self, monkeypatch):
        """One mutation → exactly ONE recompute (json.dumps once per
        tool_calls message in the new scan), not one per consumer."""
        dumps_calls = {"n": 0}
        real_dumps = compaction_module.json.dumps

        def counting_dumps(*a, **kw):
            dumps_calls["n"] += 1
            return real_dumps(*a, **kw)

        monkeypatch.setattr(compaction_module.json, "dumps", counting_dumps)

        mem = Memory()
        host = _MemoryHost(mem)
        mem.estimated_chars()  # warm on empty
        dumps_calls["n"] = 0

        mem.add_tool_call("assistant", "", [
            {"id": "c1", "name": "shell", "arguments": {"command": "ls"}}
        ])
        host._check_compaction()   # recomputes (1 dumps call)
        host._snapshot_running_tokens()  # warm hit (0)
        assert dumps_calls["n"] == 1, (
            f"one mutation must cost exactly one recompute, "
            f"got {dumps_calls['n']}")

    def test_foreign_memory_falls_back_to_scan(self):
        """Duck-typed memories without estimated_chars keep working — the
        mixin contract (test_compaction_subsystem.py) must not regress."""
        host = _ForeignHost([_Msg("y" * 800)])
        host._snapshot_running_tokens()
        # 800 chars // 4 = 200 tokens; split 90/10
        assert host._running_tokens_in == 180
        assert host._running_tokens_out == 20
        assert host._check_compaction() == 0

    def test_compaction_still_fires_over_threshold(self):
        """Behavior pin: a real-Memory host over the token threshold still
        compacts, and the post-compaction recount sees the truncated state
        (compact_messages invalidated the cache)."""
        mem = Memory()
        for i in range(30):
            mem.add_tool_result(f"c{i}", "read_file", "z" * 2000)
        host = _MemoryHost(mem, num_ctx=1000, threshold=0.85)
        assert host._check_compaction() > 0, "over-threshold must compact"
        # Post-compaction snapshot must reflect the SHRUNK state, not a
        # stale pre-compaction total.
        host._snapshot_running_tokens()
        total = host._running_tokens_in + host._running_tokens_out
        assert total < 30 * 2000 // 4, "stale pre-compaction total leaked"


# ---------------------------------------------------------------------------
# PERF-04 — discover(force=True) mtime-aware rescan
# ---------------------------------------------------------------------------

def _write_plugin(root: Path, name: str, description: str = "demo") -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "name": name,
            "version": "0.1.0",
            "description": description,
            "compatibility": {"agentkthx": ">=0.7.0"},
        }),
        encoding="utf-8",
    )
    return plugin_dir


class TestPerf04DiscoverMtime:
    def test_force_rediscover_reuses_unchanged_manifests(self, tmp_path, monkeypatch):
        root = tmp_path / "plugins"
        root.mkdir()
        _write_plugin(root, "alpha")
        _write_plugin(root, "beta")

        pm = PluginManager(plugins_dir=root)
        first = pm.discover()
        assert [m.name for m in first] == ["alpha", "beta"]

        parse_calls = {"n": 0}
        real_parse = loader_module._parse_manifest

        def counting_parse(*a, **kw):
            parse_calls["n"] += 1
            return real_parse(*a, **kw)

        monkeypatch.setattr(loader_module, "_parse_manifest", counting_parse)

        again = pm.discover(force=True)
        assert parse_calls["n"] == 0, (
            "unchanged plugin.json files must not be re-parsed")
        # Identity pin: the SAME manifest objects are reused, not clones.
        assert again[0] is first[0]
        assert again[1] is first[1]

    def test_force_rediscover_reparses_only_changed(self, tmp_path, monkeypatch):
        import os
        root = tmp_path / "plugins"
        root.mkdir()
        _write_plugin(root, "alpha")
        plugin_dir = _write_plugin(root, "beta")

        pm = PluginManager(plugins_dir=root)
        first = {m.name: m for m in pm.discover()}

        # Rewrite beta's manifest with an explicitly bumped mtime (mtime
        # tick granularity makes a same-tick rewrite unreliable).
        manifest_path = plugin_dir / "plugin.json"
        manifest_path.write_text(
            json.dumps({
                "name": "beta",
                "version": "0.2.0",
                "description": "changed",
                "compatibility": {"agentkthx": ">=0.7.0"},
            }),
            encoding="utf-8",
        )
        st = manifest_path.stat()
        os.utime(manifest_path, (st.st_atime + 10, st.st_mtime + 10))

        parse_calls = {"n": 0}
        real_parse = loader_module._parse_manifest

        def counting_parse(*a, **kw):
            parse_calls["n"] += 1
            return real_parse(*a, **kw)

        monkeypatch.setattr(loader_module, "_parse_manifest", counting_parse)

        second = pm.discover(force=True)
        assert parse_calls["n"] == 1, "only the modified manifest re-parses"
        by_name = {m.name: m for m in second}
        assert by_name["beta"].version == "0.2.0"
        assert by_name["beta"] is not first["beta"], "changed file → new object"
        assert by_name["alpha"] is first["alpha"], "unchanged file → same object"

    def test_removed_plugin_drops_from_results_and_cache(self, tmp_path):
        import shutil
        root = tmp_path / "plugins"
        root.mkdir()
        plugin_dir = _write_plugin(root, "gone")
        _write_plugin(root, "stays")

        pm = PluginManager(plugins_dir=root)
        assert [m.name for m in pm.discover()] == ["gone", "stays"]

        shutil.rmtree(plugin_dir)
        assert [m.name for m in pm.discover(force=True)] == ["stays"]
        assert str(plugin_dir / "plugin.json") not in pm._manifest_cache

        # Re-create the same path with a stale-equal mtime: the cache entry
        # was pruned, so it MUST re-parse and reappear.
        _write_plugin(root, "gone")
        assert [m.name for m in pm.discover(force=True)] == ["gone", "stays"]

    def test_unforced_discover_keeps_list_cache(self, tmp_path):
        root = tmp_path / "plugins"
        root.mkdir()
        _write_plugin(root, "alpha")

        pm = PluginManager(plugins_dir=root)
        pm.discover()
        _write_plugin(root, "newkid")
        assert [m.name for m in pm.discover()] == ["alpha"], (
            "unforced discover() must keep returning the cached list")


# ---------------------------------------------------------------------------
# PERF-06 — bounded, bytes-direct _fetch_json
# ---------------------------------------------------------------------------

class _FakeResp:
    """urllib-response stand-in recording the read() cap argument."""

    def __init__(self, body: bytes):
        self.body = body
        self.read_caps: list = []
        self.closed = False

    def read(self, amt=-1):
        self.read_caps.append(amt)
        if amt is None or amt < 0:
            return self.body
        return self.body[:amt]

    def close(self):
        self.closed = True


class TestPerf06BoundedFetch:
    def test_fetch_json_reads_exactly_the_cap(self, monkeypatch):
        body = json.dumps({"info": {"version": "0.7.13"}}).encode("utf-8")
        resp = _FakeResp(body)
        monkeypatch.setattr(update_check, "_urlopen", lambda req, timeout: resp)

        payload = update_check._fetch_json("https://example.test/json", timeout=1.0)
        assert payload["info"]["version"] == "0.7.13"
        assert resp.read_caps == [update_check._MAX_UPDATE_JSON_BYTES], (
            "the bounded read must request exactly _MAX_UPDATE_JSON_BYTES")
        assert resp.closed

    def test_pypi_track_still_parses_normal_document(self, monkeypatch):
        """Behavior pin: a well-formed body (smaller than the cap) parses
        byte-identically to the old read-all + decode path."""
        body = json.dumps({
            "info": {"version": "0.7.13"},
            "releases": {"0.7.12": [], "0.7.13": []},
        }).encode("utf-8")
        resp = _FakeResp(body)
        monkeypatch.setattr(update_check, "_urlopen", lambda req, timeout: resp)

        assert update_check._fetch_pypi_latest(timeout=1.0) == "0.7.13"

    def test_oversized_body_fails_the_source_silently(self, monkeypatch, capsys):
        """A body larger than the cap truncates → JSONDecodeError →
        check_for_update's per-source except swallows it (the check is
        best-effort and retried next invocation). No raise escapes, and
        the other tracks are unaffected by the failure."""
        huge_garbage = b'{"info": {"version": "' + b"x" * (update_check._MAX_UPDATE_JSON_BYTES + 1024)
        resp = _FakeResp(huge_garbage)
        monkeypatch.setattr(update_check, "_urlopen", lambda req, timeout: resp)
        monkeypatch.setattr(update_check, "git_hash", lambda: None)  # pip mode

        result = update_check.check_for_update(timeout=1.0)
        assert result is not None
        assert result["pypi_latest"] is None
        assert result["github_latest_version"] is None

    def test_cap_is_generous_vs_live_pypi_document(self):
        """The cap must stay well above the live document size so routine
        checks never truncate. Live-measured 2026-09-29: 94,189 bytes
        (README 44,291 chars included). 256KB ≈ 2.7x headroom."""
        assert update_check._MAX_UPDATE_JSON_BYTES >= 262_144
        assert update_check._MAX_UPDATE_JSON_BYTES > 94_189 * 2
