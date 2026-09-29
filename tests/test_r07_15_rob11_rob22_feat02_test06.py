"""R07.15 four-finding closure batch — TEST-06, FEAT-02, ROB-11, ROB-22.

- ROB-22: OrcaRouter ``_iter_sse_lines`` gains the post-loop exhaustion
  raise matching the non-streaming path (a bounded retry generator ends
  in yield-or-raise; silent empty streams are impossible).
- ROB-11: plugin load failures roll back imperative ``register_*``
  calls via a per-plugin transaction (prev-value-restore semantics).
- FEAT-02: per-tool ``timeout`` parameter for ``http_get``/``web_search``
  + concurrent execution of independent tool-call batches (ThreadPool,
  max 4 workers, results committed in original call order).
- TEST-06: CI runs a non-blocking ``lint`` job (ruff check + black
  --check over agentkthx/ and tests/).
"""

from __future__ import annotations

import json
import textwrap
import threading
import time
import urllib.error
from pathlib import Path

import pytest

import agentkthx.plugins.orcarouter.orcarouter as orcarouter_module
from agentkthx.core import agentic_loop as agentic_loop_module
from agentkthx.core.agentic_loop import (
    _SEQUENTIAL_ONLY_TOOLS,
    AgenticLoopMixin,
    LoopCallbacks,
    _calls_independent,
    _LoopState,
)
from agentkthx.core.models import Tool
from agentkthx.core.tool_execution import ToolExecutionMixin
from agentkthx.plugins._loader import PluginManager
from agentkthx.plugins.orcarouter.orcarouter import OrcaRouterBackend
from agentkthx.tools.builtins import make_builtin_registry

# ═══════════════════════════════════════════════════════════════════════
# ROB-22 — streaming exhaustion raise
# ═══════════════════════════════════════════════════════════════════════


class _FakeStreamResponse:
    """Minimal urllib response: iterable of raw SSE lines, close()-able."""

    def __init__(self, lines):
        self._lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


@pytest.fixture
def orca_env(monkeypatch):
    """OrcaRouterBackend() requires an API key at construction."""
    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test-key-1234567890")


class TestROB22StreamExhaustionRaise:
    def test_stream_exhaustion_raises_matching_nonstreaming(self, monkeypatch, orca_env):
        """Every attempt hits a retry-continue path (classifier stubbed to
        always return kind="retry" — the exact future-drift shape this
        closure guards) → the generator must RAISE, not fall off the end
        into a silent empty stream."""
        b = OrcaRouterBackend()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                "https://api.orcarouter.ai/v1/chat/completions",
                429,
                "free rate limited",
                None,
                None,
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr(
            type(b),
            "_classify_and_handle_http_error",
            lambda self, **kw: orcarouter_module._HttpErrorAction.retry(),
        )
        # The real classifier sleeps on retry; the stub does not — but pin
        # time.sleep anyway so a partial stub can never stall the suite.
        monkeypatch.setattr("time.sleep", lambda s: None)

        gen = b._iter_sse_lines(
            "https://api.orcarouter.ai/v1/chat/completions",
            {"model": "orcarouter/auto", "messages": []},
            {"Authorization": "Bearer x"},
        )
        with pytest.raises(RuntimeError, match="OrcaRouter-Stream: exhausted retries"):
            list(gen)

    def test_stream_success_path_unaffected(self, monkeypatch, orca_env):
        """Guard against over-blocking: a healthy response still yields
        its SSE lines and returns normally."""
        b = OrcaRouterBackend()
        resp = _FakeStreamResponse([b'data: {"x": 1}\n', b"data: [DONE]\n"])
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)

        out = list(
            b._iter_sse_lines(
                "https://api.orcarouter.ai/v1/chat/completions",
                {"model": "orcarouter/auto", "messages": []},
                {},
            )
        )
        assert out == [b'data: {"x": 1}\n', b"data: [DONE]\n"]
        assert resp.closed, "ROB-06 deterministic close must still fire"

    def test_exhaustion_raise_matches_nonstreaming_message_shape(self):
        """Source pin: the post-loop raise exists in _iter_sse_lines AFTER
        the success return, and mirrors the non-streaming message shape
        (OrcaRouter...: exhausted retries (4 attempts) for model ...)."""
        src = Path(orcarouter_module.__file__).read_text(encoding="utf-8")
        stream_body = src.split("def _iter_sse_lines", 1)[1].split("def generate_stream", 1)[0]
        # The raise must be OUTSIDE the for-loop's success return.
        assert "return  # success — don't retry" in stream_body
        raise_part = stream_body.split("return  # success — don't retry", 1)[1]
        assert (
            "exhausted retries (4 attempts)" in raise_part
        ), "post-loop exhaustion raise missing from _iter_sse_lines"
        assert "OrcaRouter-Stream" in raise_part
        # Non-streaming counterpart still carries its own exhaustion raise.
        nonstream_body = src.split("def _generate_with_auth", 1)[1].split("def _iter_sse_lines", 1)[
            0
        ]
        assert "exhausted retries (4 attempts)" in nonstream_body


# ═══════════════════════════════════════════════════════════════════════
# ROB-11 — transactional plugin registration
# ═══════════════════════════════════════════════════════════════════════

from agentkthx.backends.base import BaseBackend  # noqa: E402  (after docstring imports)


class _HostBackendA(BaseBackend):
    pass


class _HostBackendB(BaseBackend):
    pass


def _write_plugin(root: Path, name: str, body: str) -> Path:
    """Create an external-root plugin with the given __init__.py body."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "ROB-11 test plugin",
                "compatibility": {"agentkthx": ">=0.7.0"},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return plugin_dir


_REGISTER_ALL_THEN_RAISE = """
    from agentkthx.backends.base import BaseBackend

    class _FailBackend(BaseBackend):
        pass

    def register(manager):
        manager.register_backend("failb", _FailBackend, plugin="failall")
        manager.register_backend("failalias", _FailBackend, alias_of="ollama",
                                 plugin="failall")
        manager.register_tool(
            _TOOL, plugin="failall")
        manager.register_cli_command("failcmd", lambda *a: None, plugin="failall")
        manager.register_hook("on_init", lambda *a: None, plugin="failall")
        raise RuntimeError("boom during register")
"""


class TestROB11TransactionalRegistration:
    def _manager(self, tmp_path):
        root = tmp_path / "plugins"
        root.mkdir(exist_ok=True)
        return root, PluginManager(plugins_dir=root)

    def test_failed_register_rolls_back_imperative_registrations(self, tmp_path):
        """register() registers a backend + alias + tool + CLI command +
        hook, then raises — with NO unregister() at all. The transaction
        must roll every imperative registration back."""

        root, pm = self._manager(tmp_path)
        tool_src = 'Tool(name="fail_tool", description="x", params=[], ' 'handler=lambda **k: "ok")'
        body = _REGISTER_ALL_THEN_RAISE.replace("_TOOL", tool_src)
        # The plugin body needs the Tool import:
        body = "    from agentkthx.core.models import Tool\n" + body
        _write_plugin(root, "failall", body)

        loaded = pm.load_all()
        assert loaded == [], "failing plugin must not report as loaded"
        assert pm._failed.get("failall"), "failure must be recorded"
        # Nothing imperative survived:
        assert "failb" not in pm._backend_classes
        assert "failalias" not in pm._backend_aliases
        assert pm.get_tool("fail_tool") is None
        assert "failcmd" not in pm._cli_commands
        assert all(e.get("plugin") != "failall" for entries in pm._hooks.values() for e in entries)
        # The transaction stack is drained after load:
        assert pm._txn_stack == []

    def test_failed_register_with_broken_unregister_still_clean(self, tmp_path):
        """The audit's core complaint: unregister() raising used to leave
        partial registrations in place. Now the transaction cleans up and
        the warning is logged."""

        root, pm = self._manager(tmp_path)
        body = textwrap.dedent("""
            from agentkthx.backends.base import BaseBackend
            from agentkthx.core.models import Tool

            class _FailBackend(BaseBackend):
                pass

            def register(manager):
                manager.register_backend("failb", _FailBackend, plugin="brokenup")
                manager.register_tool(
                    Tool(name="fail_tool", description="x", params=[],
                         handler=lambda **k: "ok"),
                    plugin="brokenup")
                raise RuntimeError("boom")

            def unregister(manager):
                raise ValueError("unregister exploded")
        """)
        _write_plugin(root, "brokenup", body)

        pm.load_all()
        assert "failb" not in pm._backend_classes
        assert pm.get_tool("fail_tool") is None
        assert any(
            "unregister() also failed" in w for w in pm.warnings
        ), "the unregister() failure must still be surfaced as a warning"

    def test_partial_unregister_completed_by_transaction(self, tmp_path):
        """unregister() that removes only SOME state (the tool) — the
        transaction covers the rest (backend + CLI + hook)."""
        root, pm = self._manager(tmp_path)
        body = textwrap.dedent("""
            from agentkthx.backends.base import BaseBackend
            from agentkthx.core.models import Tool

            class _FailBackend(BaseBackend):
                pass

            def register(manager):
                manager.register_backend("failb", _FailBackend, plugin="partial")
                manager.register_tool(
                    Tool(name="fail_tool", description="x", params=[],
                         handler=lambda **k: "ok"),
                    plugin="partial")
                manager.register_cli_command("failcmd", lambda *a: None,
                                             plugin="partial")
                manager.register_hook("on_init", lambda *a: None,
                                      plugin="partial")
                raise RuntimeError("boom")

            def unregister(manager):
                manager.unregister_tool("fail_tool")
        """)
        _write_plugin(root, "partial", body)

        pm.load_all()
        assert pm.get_tool("fail_tool") is None, "removed by unregister()"
        assert "failb" not in pm._backend_classes, "must be rolled back"
        assert "failcmd" not in pm._cli_commands, "must be rolled back"
        assert all(
            e.get("plugin") != "partial" for entries in pm._hooks.values() for e in entries
        ), "must be rolled back"

    def test_preexisting_same_name_registration_restored(self, tmp_path):
        """Prev-value-restore, not blind delete: a same-name backend that
        existed BEFORE the failing plugin (registered by host code) must
        be restored on rollback, not removed."""
        root, pm = self._manager(tmp_path)
        body = textwrap.dedent("""
            from agentkthx.backends.base import BaseBackend

            class _FailBackend(BaseBackend):
                pass

            def register(manager):
                manager.register_backend("shared_b", _FailBackend,
                                         plugin="overwriter")
                raise RuntimeError("boom")
        """)
        _write_plugin(root, "overwriter", body)

        # Host-level registration (outside any transaction): prev state.
        pm.register_backend("shared_b", _HostBackendA, plugin="host")
        pm.load_all()
        entry = pm._backend_classes.get("shared_b")
        assert entry is not None, "pre-existing registration must survive"
        assert (
            entry[0] is _HostBackendA
        ), "rollback must RESTORE the previous value, not blind-delete"

    def test_successful_register_keeps_registrations(self, tmp_path):
        """The transaction must not over-purge: a plugin whose register()
        succeeds keeps every imperative registration."""
        root, pm = self._manager(tmp_path)
        body = textwrap.dedent("""
            from agentkthx.backends.base import BaseBackend
            from agentkthx.core.models import Tool

            class _OkBackend(BaseBackend):
                pass

            def register(manager):
                manager.register_backend("okb", _OkBackend, plugin="okplug")
                manager.register_backend("okalias", _OkBackend,
                                         alias_of="ollama", plugin="okplug")
                manager.register_tool(
                    Tool(name="ok_tool", description="x", params=[],
                         handler=lambda **k: "ok"),
                    plugin="okplug")
                manager.register_cli_command("okcmd", lambda *a: None,
                                             plugin="okplug")
                manager.register_hook("on_init", lambda *a: None,
                                      plugin="okplug")
        """)
        _write_plugin(root, "okplug", body)

        loaded = pm.load_all()
        assert [p.manifest.name for p in loaded] == ["okplug"]
        assert "okb" in pm._backend_classes
        assert pm._backend_aliases.get("okalias") == "ollama"
        assert pm.get_tool("ok_tool") is not None
        assert "okcmd" in pm._cli_commands
        assert any(e.get("plugin") == "okplug" for entries in pm._hooks.values() for e in entries)

    def test_host_registrations_outside_register_not_recorded(self, tmp_path):
        """Documented behavior: register_* calls made by host code outside
        a register() run are NOT transactional (no active transaction →
        _record_undo is a no-op)."""
        root, pm = self._manager(tmp_path)
        pm.register_backend("host_b", _HostBackendB, plugin="host")
        assert pm._txn_stack == []
        assert "host_b" in pm._backend_classes


# ═══════════════════════════════════════════════════════════════════════
# FEAT-02 — per-tool timeout + concurrent execution
# ═══════════════════════════════════════════════════════════════════════


class _StubTracker:
    def __init__(self):
        self.failures = []

    def should_block_repeat(self, tool_name, arguments):
        return False

    def record_failure(self, **kw):
        self.failures.append(kw)

    def record_success(self, tool_name):
        pass

    def should_terminate(self):
        return False

    def build_recovery_message(self, *args, **kwargs):
        return "(recovery hint)"

    consecutive_all = 0
    max_total_failures = 3


class _StubMemory:
    def __init__(self):
        self.calls = []

    def add(self, role, content):
        self.calls.append(("add", role, content))

    def add_tool_call(self, role, content, calls):
        self.calls.append(("add_tool_call", role, content))

    def add_tool_result(self, *, tool_call_id, name, content):
        self.calls.append(("add_tool_result", tool_call_id, name, content))


class _StubResponse:
    def __init__(self):
        self.items = []
        self.cancelled = False
        self.failed = None

    def add_output_item(self, item, debug=False):
        self.items.append(item)

    def mark_cancelled(self, debug=False):
        self.cancelled = True

    def mark_failed(self, err):
        self.failed = err


class _StubRegistry:
    def __init__(self, tools):
        self._tools = {t.name: t for t in tools}

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return sorted(self._tools)


class _LoopHost(ToolExecutionMixin, AgenticLoopMixin):
    """Minimal Agent stand-in exposing the FEAT-02 dispatcher methods."""

    def __init__(self, registry, allowed_tools=None, debug=False):
        self.tools = registry
        self._allowed_tools = allowed_tools
        self._error_tracker = _StubTracker()
        self.memory = _StubMemory()
        self.debug = debug
        self._is_comp_mode = True
        self._confirm_dangerous = None
        self._retry_on_error = False
        self._max_tool_retries = 2


def _thread_recording_tool(name, recorder, delay=0.0, result=None):
    def handler(**kw):
        if delay:
            time.sleep(delay)
        recorder.append((name, threading.current_thread().name))
        return result if result is not None else f"{name} ok"

    return Tool(name=name, description="x", params=[], handler=handler)


def _dispatch(host, tcs, **overrides):
    state = overrides.get("state", _LoopState())
    steps = overrides.get("steps", [])
    host._execute_tool_calls(
        tcs,
        state=state,
        prompt="p",
        step_num=1,
        tokens=0,
        content="",
        native_tool_calls=[],
        steps=steps,
        response=overrides.get("response", _StubResponse()),
        callbacks=overrides.get("callbacks", LoopCallbacks()),
    )
    return state, steps


class TestFeat02CallsIndependent:
    def test_different_tools_independent(self):
        assert _calls_independent(
            [
                {"name": "http_get", "arguments": {"url": "a"}},
                {"name": "web_search", "arguments": {"q": "b"}},
            ]
        )

    def test_same_tool_different_args_independent(self):
        assert _calls_independent(
            [
                {"name": "http_get", "arguments": {"url": "a"}},
                {"name": "http_get", "arguments": {"url": "b"}},
            ]
        )

    def test_identical_call_not_independent(self):
        """The R06.52 identical-repeat guard counts per (tool, args) —
        duplicates must run sequentially to avoid racing the tracker."""
        assert not _calls_independent(
            [
                {"name": "http_get", "arguments": {"url": "a"}},
                {"name": "http_get", "arguments": {"url": "a"}},
            ]
        )

    def test_stateful_tools_force_sequential(self):
        for stateful in ("shell", "write_file", "edit_file", "todo"):
            batch = [
                {"name": stateful, "arguments": {"x": 1}},
                {"name": "http_get", "arguments": {"url": "a"}},
            ]
            assert not _calls_independent(batch), stateful
        assert (
            "python_repl" not in _SEQUENTIAL_ONLY_TOOLS
        ), "python_repl is sandboxed (no fs/network) — parallelizable"

    def test_single_call_trivially_independent(self):
        assert _calls_independent([{"name": "http_get", "arguments": {}}])


class TestFeat02ParallelExecution:
    def test_independent_batch_runs_concurrently(self):
        """Deterministic concurrency proof: both handlers block on a
        2-party barrier. If the pool serialized them on one worker thread,
        the second party would never arrive → BrokenBarrierError → the
        results would read ':broken'. Only genuine parallel execution
        passes the barrier."""
        barrier = threading.Barrier(2)

        def make(name):
            def handler(**kw):
                try:
                    barrier.wait(timeout=10)
                    return f"{name}:passed"
                except threading.BrokenBarrierError:
                    return f"{name}:broken"

            return Tool(name=name, description="x", params=[], handler=handler)

        host = _LoopHost(_StubRegistry([make("t1"), make("t2")]))
        tcs = [
            {"name": "t1", "arguments": {}, "id": "c1"},
            {"name": "t2", "arguments": {}, "id": "c2"},
        ]
        state, steps = _dispatch(host, tcs)
        assert state.terminated is False
        assert [s.tool_result for s in steps] == ["t1:passed", "t2:passed"], (
            "both calls must pass the 2-party barrier — only possible on "
            "distinct concurrent worker threads"
        )

    def test_commit_order_preserved_regardless_of_completion_order(self):
        """Call 0 is slow, call 1 is fast — commits (steps, callbacks,
        memory) must still happen in ORIGINAL call order."""
        recorder = []
        host = _LoopHost(
            _StubRegistry(
                [
                    _thread_recording_tool("slow", recorder, delay=0.15, result="slow-A"),
                    _thread_recording_tool("fast", recorder, delay=0.0, result="fast-B"),
                ]
            )
        )
        tcs = [
            {"name": "slow", "arguments": {}, "id": "c1"},
            {"name": "fast", "arguments": {}, "id": "c2"},
        ]
        callbacks = LoopCallbacks()
        committed_order = []
        callbacks.on_tool_executed = lambda count, name, args, result: committed_order.append(name)
        state, steps = _dispatch(host, tcs, callbacks=callbacks)
        assert committed_order == [
            "slow",
            "fast",
        ], "commits must follow call order, not completion order"
        assert [s.tool_result for s in steps] == ["slow-A", "fast-B"]

    def test_stateful_tools_stay_sequential(self, monkeypatch):
        """A batch containing shell (or any _SEQUENTIAL_ONLY_TOOLS member)
        must never reach the parallel path."""

        def boom(*a, **kw):
            raise AssertionError("parallel path must not be used")

        recorder = []
        host = _LoopHost(
            _StubRegistry(
                [
                    _thread_recording_tool("shell", recorder),
                    _thread_recording_tool("http_get", recorder),
                ]
            )
        )
        monkeypatch.setattr(host, "_execute_tool_calls_parallel", boom)
        tcs = [
            {"name": "shell", "arguments": {"command": "ls"}, "id": "c1"},
            {"name": "http_get", "arguments": {"url": "x"}, "id": "c2"},
        ]
        state, steps = _dispatch(host, tcs)
        assert len(steps) == 2
        threads = {t for (_n, t) in recorder}
        assert threads == {
            threading.current_thread().name
        }, "stateful batch must execute on the calling thread"

    def test_env_toggle_disables_parallel(self, monkeypatch):
        """AGENTKTHX_PARALLEL_TOOLS=0 forces the sequential path even for
        independent batches (escape hatch)."""

        def boom(*a, **kw):
            raise AssertionError("parallel path must not be used")

        monkeypatch.setenv("AGENTKTHX_PARALLEL_TOOLS", "0")
        recorder = []
        host = _LoopHost(
            _StubRegistry(
                [
                    _thread_recording_tool("t1", recorder),
                    _thread_recording_tool("t2", recorder),
                ]
            )
        )
        monkeypatch.setattr(host, "_execute_tool_calls_parallel", boom)
        tcs = [
            {"name": "t1", "arguments": {}, "id": "c1"},
            {"name": "t2", "arguments": {}, "id": "c2"},
        ]
        state, steps = _dispatch(host, tcs)
        assert len(steps) == 2

    def test_gate_blocked_call_swallowed_to_sequential(self):
        """One call blocked by allowed_tools → one runnable call remains →
        it finishes through the sequential single-call lifecycle (no pool,
        blocked call still recorded in memory for history pairing)."""
        recorder = []
        host = _LoopHost(
            _StubRegistry(
                [
                    _thread_recording_tool("t1", recorder),
                    _thread_recording_tool("t2", recorder),
                ]
            ),
            allowed_tools=["t1"],
        )
        tcs = [
            {"name": "t1", "arguments": {}, "id": "c1"},
            {"name": "t2", "arguments": {}, "id": "c2"},
        ]
        state, steps = _dispatch(host, tcs)
        assert [s.tool_result for s in steps] == ["t1 ok"]
        # native_tool_calls=[] → the ReAct memory form (memory.add)
        blocked = [c for c in host.memory.calls if c[0] == "add" and "not in allowed_tools" in c[2]]
        assert blocked, "blocked call must be recorded in memory for pairing"

    def test_parallel_tool_error_results_committed_normally(self):
        """A tool raising inside a worker → _execute_tool formats the
        error string → committed through the normal pipeline (no pool
        exception leaks)."""

        def bad_handler(**kw):
            raise ValueError("worker exploded")

        host = _LoopHost(
            _StubRegistry(
                [
                    Tool(name="bad", description="x", params=[], handler=bad_handler),
                    _thread_recording_tool("good", [], result="fine"),
                ]
            )
        )
        tcs = [
            {"name": "bad", "arguments": {}, "id": "c1"},
            {"name": "good", "arguments": {}, "id": "c2"},
        ]
        state, steps = _dispatch(host, tcs)
        assert state.terminated is False
        assert "worker exploded" in steps[0].tool_result
        assert steps[1].tool_result == "fine"


class TestFeat02PerToolTimeout:
    def test_http_get_schema_has_timeout_param(self):
        registry = make_builtin_registry()
        tool = registry.get("http_get")
        timeout_params = [p for p in tool.params if p.name == "timeout"]
        assert len(timeout_params) == 1
        p = timeout_params[0]
        assert p.type == "integer" and p.required is False and p.default == 30
        schema = tool.to_json_schema()
        assert "timeout" in schema["function"]["parameters"]["properties"]
        assert "timeout" not in schema["function"]["parameters"]["required"]

    def test_web_search_schema_has_timeout_param(self):
        registry = make_builtin_registry()
        tool = registry.get("web_search")
        timeout_params = [p for p in tool.params if p.name == "timeout"]
        assert len(timeout_params) == 1
        p = timeout_params[0]
        assert p.type == "integer" and p.required is False and p.default == 15

    def test_http_get_timeout_reaches_urlopen_and_clamps(self, monkeypatch):
        """The model-supplied timeout is passed to the HTTP call and
        clamped to [1, 300] — urlopen with timeout <= 0 would disable the
        socket timeout entirely."""
        captured = []

        class _FakeResponse:
            headers = {"Content-Type": "text/plain; charset=utf-8"}

            def read(self, n=-1):
                return b"hello"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeOpener:
            def open(self, req, timeout=None):
                captured.append(timeout)
                return _FakeResponse()

        monkeypatch.setattr("urllib.request.build_opener", lambda *a, **kw: _FakeOpener())

        assert http_get("https://example.com").startswith("hello")
        assert captured == [30], "default must remain 30"

        captured.clear()
        http_get("https://example.com", timeout=600)
        assert captured == [300], "over-cap timeout must clamp to 300"

        captured.clear()
        http_get("https://example.com", timeout=0)
        assert captured == [1], "timeout <= 0 must clamp to 1"

        captured.clear()
        http_get("https://example.com", timeout="45")
        assert captured == [45], "string numerics coerce (R06.52 parity)"

    def test_web_search_timeout_reaches_both_endpoints(self, monkeypatch):
        """The html endpoint fails (URLError) → the lite fallback fires —
        BOTH urlopen calls must carry the clamped per-call timeout."""
        captured = []

        class _FakeResponse:
            def read(self, n=-1):
                return b"<html>no results here</html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured.append(timeout)
            if len(captured) == 1:
                raise urllib.error.URLError("html endpoint down")
            return _FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        out = web_search("test query", num_results=1, timeout="600")
        assert len(captured) == 2, "html attempt + lite fallback"
        assert all(
            t == 300 for t in captured
        ), f"both endpoint attempts must carry the clamped timeout, got {captured}"
        assert isinstance(out, str)


from agentkthx.tools.builtins import http_get, web_search  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════
# TEST-06 — CI lint job
# ═══════════════════════════════════════════════════════════════════════


class TestTest06CILintJob:
    def _workflow(self):
        # pyyaml ships in the [dev] extras (pyproject.toml) — these pins
        # exist to verify the CI workflow shape, so they must RUN in CI,
        # never skip: an importorskip here would leave CI unable to
        # check itself. (First CI failure this caused: pyyaml was
        # missing from [dev] and every pytest job went red with
        # ModuleNotFoundError: No module named 'yaml'.)
        import yaml

        return yaml.safe_load(
            (
                Path(agentic_loop_module.__file__).parents[2] / ".github" / "workflows" / "ci.yml"
            ).read_text(encoding="utf-8")
        )

    def test_lint_job_exists_and_is_gating(self):
        wf = self._workflow()
        lint = wf["jobs"].get("lint")
        assert lint is not None, "TEST-06: CI must run a lint job"
        # Launched non-blocking (continue-on-error: true) while the
        # pre-tooling drift was burned down; promoted to a required check
        # once the tree went lint-clean. Re-adding continue-on-error would
        # let style regressions pass CI silently — keep it gating.
        assert (
            lint.get("continue-on-error") is None
        ), "lint must stay a REQUIRED check after the R07.15 drift burn-down"

    def test_lint_job_runs_ruff_and_black_over_package_and_tests(self):
        wf = self._workflow()
        run_commands = [step.get("run", "") for step in wf["jobs"]["lint"]["steps"]]
        joined = "\n".join(run_commands)
        assert "ruff check agentkthx/ tests/" in joined
        assert "black --check agentkthx/ tests/" in joined

    def test_test_and_coverage_jobs_unchanged(self):
        """The lint job is ADDITIVE — the test matrix and coverage jobs
        keep their exact commands."""
        wf = self._workflow()
        assert "test" in wf["jobs"] and "coverage" in wf["jobs"]
        test_steps = [s.get("run", "") for s in wf["jobs"]["test"]["steps"]]
        assert any("pytest tests/ -q" in c for c in test_steps)
