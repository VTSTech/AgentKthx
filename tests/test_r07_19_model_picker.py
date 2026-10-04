"""R07.19 follow-up #7 — arrow-key model picker, /models interactive switch,
optional ``--model`` with a startup picker, and the ``agentkthx souls``
subcommand.

Covers five layers:

1. ``ArrowMenu`` pure core (``agentkthx/cli/picker.py``) — navigation
   clamps, viewport sliding, frame rendering, empty-item guard.
2. Key normalization + the I/O shell — ``map_key`` sequences, the
   ``_loop`` accept/cancel contract with a canned key reader, the
   POSIX cbreak enter/restore contract, the numbered non-TTY fallback
   and the ``run()`` dispatch guards.
3. Chat helpers (``agentkthx/cli/commands/chat.py``) —
   ``_model_menu_labels`` hint formatting, ``_report_model_switch``
   output, ``_interactive_model_switch`` apply/cancel/same-model
   contracts and ``_startup_model_pick`` degradation paths.
4. cmd_chat wiring pins — the startup guard conditions, the /models
   TTY branch, /help text, shared switch reporting.
5. ``agentkthx souls`` — listing (with the introspected default-soul
   marker), name-resolved detail view, fuzzy-miss behavior, parser and
   dispatch wiring.
"""

import argparse
import io
import re
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from agentkthx.cli.picker import ArrowMenu, map_key

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ═══════════════════════════════════════════════════════════════════════
# 1. ArrowMenu pure core
# ═══════════════════════════════════════════════════════════════════════


class TestArrowMenuNavigation:
    def test_cursor_clamped_to_bounds_at_init(self):
        assert ArrowMenu(["a", "b"], cursor=99).cursor == 1
        assert ArrowMenu(["a", "b"], cursor=-5).cursor == 0

    def test_move_up_down_clamp(self):
        menu = ArrowMenu(["a", "b", "c"])
        menu.move_up()
        assert menu.cursor == 0  # stays at top
        menu.move_down()
        menu.move_down()
        menu.move_down()
        assert menu.cursor == 2  # stays at bottom

    def test_home_end(self):
        menu = ArrowMenu(["a", "b", "c"], cursor=1)
        menu.move_home()
        assert menu.cursor == 0
        menu.move_end()
        assert menu.cursor == 2

    def test_page_up_down_use_visible(self):
        menu = ArrowMenu([str(i) for i in range(20)], visible=5, cursor=10)
        menu.page_up()
        assert menu.cursor == 5
        menu.page_up()
        assert menu.cursor == 0
        menu.page_up()
        assert menu.cursor == 0  # clamped
        menu.page_down()
        assert menu.cursor == 5

    def test_empty_items_rejected(self):
        with pytest.raises(ValueError):
            ArrowMenu([])

    def test_single_item_menu(self):
        menu = ArrowMenu(["only"])
        menu.move_down()
        assert menu.cursor == 0
        assert menu.window() == (0, 1)


class TestArrowMenuWindow:
    def test_short_list_renders_in_full(self):
        menu = ArrowMenu(["a", "b"], visible=10)
        assert menu.window() == (0, 2)

    def test_long_list_window_is_visible_tall(self):
        items = [str(i) for i in range(30)]
        menu = ArrowMenu(items, visible=10, cursor=0)
        assert menu.window() == (0, 10)

    def test_window_slides_to_keep_cursor_centered(self):
        items = [str(i) for i in range(30)]
        menu = ArrowMenu(items, visible=10, cursor=15)
        start, stop = menu.window()
        assert (start, stop) == (10, 20)  # cursor - visible//2, clamped
        assert start <= menu.cursor < stop

    def test_window_never_exceeds_bottom(self):
        items = [str(i) for i in range(30)]
        menu = ArrowMenu(items, visible=10, cursor=29)
        start, stop = menu.window()
        assert stop == 30 and start <= menu.cursor < stop


class TestArrowMenuRender:
    def test_frame_is_title_plus_rows_plus_hints(self):
        menu = ArrowMenu(["a", "b", "c"], title="Pick", visible=10)
        lines = menu.render().split("\n")
        assert len(lines) == 5  # title + 3 rows + key hints
        assert "Pick" in lines[0]

    def test_cursor_row_marked_current_row_marked(self):
        menu = ArrowMenu(["a", "b", "c"], cursor=1, current_index=2)
        lines = menu.render().split("\n")
        plain = [_plain(line) for line in lines]
        assert "❯" in plain[2] and "b" in plain[2]  # cursor row (index 1)
        assert "❯" not in plain[1] and "❯" not in plain[3]
        assert "✓" in plain[3]  # current marker (index 2)
        assert "✓" not in plain[1]

    def test_ascii_fallback_glyph_when_unicode_off(self, monkeypatch):
        from agentkthx import colors

        monkeypatch.setattr(colors, "is_unicode_ok", lambda: False)
        menu = ArrowMenu(["a", "b"], cursor=1)
        plain = _plain(menu.render())
        assert ">" in plain
        assert "❯" not in plain

    def test_scroll_indicators_show_remaining_counts(self):
        items = [str(i) for i in range(30)]
        top = ArrowMenu(items, visible=10, cursor=0)
        assert "↓20 more" in _plain(top.render())
        bottom = ArrowMenu(items, visible=10, cursor=29)
        assert "↑20 more" in _plain(bottom.render())
        middle = ArrowMenu(items, visible=10, cursor=15)
        rendered = _plain(middle.render())
        assert "↑" in rendered and "↓" in rendered

    def test_no_scroll_indicators_on_short_list(self):
        menu = ArrowMenu(["a", "b"], visible=10)
        assert "more" not in _plain(menu.render())


# ═══════════════════════════════════════════════════════════════════════
# 2. Key normalization + I/O shell
# ═══════════════════════════════════════════════════════════════════════


class TestMapKey:
    @pytest.mark.parametrize(
        ("seq", "name"),
        [
            ("\x1b[A", "up"),
            ("\x1b[B", "down"),
            ("\x1b[C", "right"),
            ("\x1b[D", "left"),
            ("\x1b[H", "home"),
            ("\x1b[F", "end"),
            ("\x1bOH", "home"),
            ("\x1bOF", "end"),
            ("\x1b[1~", "home"),
            ("\x1b[4~", "end"),
            ("\x1b[5~", "pgup"),
            ("\x1b[6~", "pgdn"),
            ("\r", "enter"),
            ("\n", "enter"),
            ("\x1b", "escape"),
            ("\x03", "ctrl-c"),
            ("\x04", "ctrl-d"),
        ],
    )
    def test_sequences(self, seq, name):
        assert map_key(seq) == name

    @pytest.mark.parametrize("ch", ["q", "j", "k", "g", "G", "x"])
    def test_printable_chars_pass_through(self, ch):
        assert map_key(ch) == ch

    @pytest.mark.parametrize("bad", ["\x1b[200~", "\x00", "\x7f", "\x1b[?"])
    def test_unknown_sequences_ignored(self, bad):
        assert map_key(bad) == ""


class _TtyStdin(types.SimpleNamespace):
    """Stdin stand-in: isatty() True so run() takes an interactive path."""

    data: str = ""

    def isatty(self):
        return True

    def readline(self):
        return self.data


class TestRunLoop:
    def _drive(self, monkeypatch, keys, items=("a", "b", "c"), **kwargs):
        """Run menu._loop with a canned key reader; return (result, output)."""
        import agentkthx.cli.picker as picker

        seq = iter(list(keys))
        monkeypatch.setattr(picker, "_read_key", lambda: next(seq))
        menu = ArrowMenu(list(items), **kwargs)
        out = io.StringIO()
        result = menu._loop(out)
        return result, out.getvalue()

    def test_enter_accepts_current_cursor(self, monkeypatch):
        result, out = self._drive(monkeypatch, ["down", "down", "enter"])
        assert result == 2
        # The frame was drawn at least twice (initial + one redraw)
        assert out.count("a") >= 1

    def test_q_cancels(self, monkeypatch):
        result, _ = self._drive(monkeypatch, ["q"])
        assert result is None

    def test_escape_cancels(self, monkeypatch):
        result, _ = self._drive(monkeypatch, ["escape"])
        assert result is None

    def test_ctrl_c_byte_cancels(self, monkeypatch):
        result, _ = self._drive(monkeypatch, ["ctrl-c"])
        assert result is None

    def test_keyboard_interrupt_cancels(self, monkeypatch):
        import agentkthx.cli.picker as picker

        def raise_interrupt():
            raise KeyboardInterrupt

        monkeypatch.setattr(picker, "_read_key", raise_interrupt)
        menu = ArrowMenu(["a", "b"])
        out = io.StringIO()
        assert menu._loop(out) is None

    def test_unknown_keys_ignored_without_redraw(self, monkeypatch):
        import agentkthx.cli.picker as picker

        seq = iter(["x", "\x1b[200~", "down", "enter"])
        monkeypatch.setattr(picker, "_read_key", lambda: next(seq))
        menu = ArrowMenu(["a", "b", "c"])
        draws = []
        monkeypatch.setattr(ArrowMenu, "_draw", lambda self, out, first: draws.append(first))
        monkeypatch.setattr(ArrowMenu, "_erase", lambda self, out: None)
        result = menu._loop(io.StringIO())
        assert result == 1
        # Initial frame + exactly one redraw (after "down") — "x" and the
        # bogus CSI sequence must not trigger renders.
        assert draws == [True, False]

    def test_vim_keys_navigate(self, monkeypatch):
        result, _ = self._drive(monkeypatch, ["j", "j", "k", "enter"])
        assert result == 1

    def test_g_and_G_jump(self, monkeypatch):
        result, _ = self._drive(monkeypatch, ["G", "enter"])
        assert result == 2
        result, _ = self._drive(monkeypatch, ["g", "enter"])
        assert result == 0

    def test_erase_leaves_no_frame_lines(self, monkeypatch):
        import agentkthx.cli.picker as picker

        monkeypatch.setattr(picker, "_read_key", lambda: "enter")
        menu = ArrowMenu(["a", "b"])
        out = io.StringIO()
        menu._loop(out)
        value = out.getvalue()
        # After the final frame, _erase clears every frame row again
        assert value.endswith("\r\x1b[2K")


class TestRunPosixTerminalState:
    def test_cbreak_set_and_restored(self, monkeypatch):
        """_run_posix must restore the terminal state even after return."""
        import agentkthx.cli.picker as picker

        calls = []
        monkeypatch.setattr(picker._termios, "tcgetattr", lambda fd: ["saved"])
        monkeypatch.setattr(
            picker._termios,
            "tcsetattr",
            lambda fd, how, attrs: calls.append((how, attrs)),
        )
        monkeypatch.setattr(picker._tty, "setcbreak", lambda fd: calls.append(("cbreak",)))
        monkeypatch.setattr(picker, "_read_key", lambda: "enter")

        menu = ArrowMenu(["a", "b"])
        result = menu._run_posix(types.SimpleNamespace(fileno=lambda: 3), io.StringIO())
        assert result == 0
        assert calls[0] == ("cbreak",)
        # Restore happens AFTER the loop returns, with the saved attrs
        assert calls[1][1] == ["saved"]

    def test_terminal_state_restored_on_cancel(self, monkeypatch):
        import agentkthx.cli.picker as picker

        calls = []
        monkeypatch.setattr(picker._termios, "tcgetattr", lambda fd: ["saved"])
        monkeypatch.setattr(
            picker._termios, "tcsetattr", lambda fd, how, attrs: calls.append(attrs)
        )
        monkeypatch.setattr(picker._tty, "setcbreak", lambda fd: None)
        monkeypatch.setattr(picker, "_read_key", lambda: "q")

        menu = ArrowMenu(["a", "b"])
        assert menu._run_posix(types.SimpleNamespace(fileno=lambda: 3), io.StringIO()) is None
        assert calls == [["saved"]]


class TestNumberedFallback:
    def _run_fallback(self, answer, items=("a", "b", "c"), **kwargs):
        menu = ArrowMenu(list(items), **kwargs)
        out = io.StringIO()
        stdin = io.StringIO(answer)
        result = menu._numbered_fallback(stdin, out)
        return result, out.getvalue()

    def test_valid_number_selected(self):
        result, out = self._run_fallback("2\n")
        assert result == 1
        assert "1. a" in out and "3. c" in out

    def test_empty_answer_cancels(self):
        result, _ = self._run_fallback("\n")
        assert result is None

    def test_eof_cancels(self):
        result, _ = self._run_fallback("")
        assert result is None

    def test_invalid_number_cancels(self):
        result, out = self._run_fallback("abc\n")
        assert result is None
        assert "Invalid selection" in out

    def test_out_of_range_cancels(self):
        result, out = self._run_fallback("99\n")
        assert result is None
        assert "Out of range" in out

    def test_current_model_marked(self):
        result, out = self._run_fallback("\n", current_index=1)
        assert result is None
        assert "✓ current" in _plain(out)


class TestRunDispatch:
    def test_non_tty_stdin_uses_numbered_fallback(self, monkeypatch):
        """Piped stdin must never enter the raw-mode loop."""
        menu = ArrowMenu(["a", "b", "c"])
        out = io.StringIO()
        stdin = io.StringIO("3\n")
        assert menu.run(stdin=stdin, stdout=out) == 2
        assert "Select number" in out.getvalue()

    def test_non_termios_non_msvcrt_platform_falls_back(self, monkeypatch):
        """A platform with neither termios nor msvcrt uses the numbered prompt."""
        import agentkthx.cli.picker as picker

        monkeypatch.setattr(picker, "_HAVE_TERMIOS", False)
        monkeypatch.setattr(picker, "_HAVE_MSVCRT", False)
        menu = ArrowMenu(["a", "b"])
        out = io.StringIO()
        stdin = _TtyStdin(data="1\n")
        assert menu.run(stdin=stdin, stdout=out) == 0
        assert "Select number" in out.getvalue()

    def test_tty_stdin_takes_posix_path(self, monkeypatch):
        seen = {"called": False}

        def _fake_run_posix(self, stdin, stdout):
            seen["called"] = True
            return 0

        monkeypatch.setattr(ArrowMenu, "_run_posix", _fake_run_posix)
        menu = ArrowMenu(["a"])
        assert menu.run(stdin=_TtyStdin(), stdout=io.StringIO()) == 0
        assert seen["called"] is True

    def test_viewport_clamped_to_terminal_height(self, monkeypatch):
        """The frame must fit small terminals (chat scroll region)."""
        import os as _os
        import shutil

        monkeypatch.setattr(shutil, "get_terminal_size", lambda: _os.terminal_size((80, 10)))
        menu = ArrowMenu([str(i) for i in range(100)], visible=50)
        monkeypatch.setattr(ArrowMenu, "_run_posix", lambda self, stdin, stdout: self.visible)
        result = menu.run(stdin=_TtyStdin(), stdout=io.StringIO())
        assert result == 6  # 10 lines - 4 reserved


# ═════════════════════════════════════════════════════════════════
# 3. Chat helpers — labels, switch reporting, interactive switch, startup pick
# ═════════════════════════════════════════════════════════════════

from agentkthx.cli.commands.chat import (  # noqa: E402
    _interactive_model_switch,
    _model_menu_labels,
    _report_model_switch,
    _startup_model_pick,
)


class TestModelMenuLabels:
    def test_ctx_hint_from_context_length(self):
        models = [
            {"name": "glm-5.1", "details": {"context_length": 204800}},
            {"name": "qwen2.5:0.5b", "details": {}},
        ]
        labels, current = _model_menu_labels(models, "glm-5.1")
        assert labels[0] == "glm-5.1  (200K)"
        assert labels[1] == "qwen2.5:0.5b"  # no ctx → bare name
        assert current == 0

    def test_ctx_hint_falls_back_to_n_ctx(self):
        models = [{"name": "llama", "details": {"n_ctx": 8192}}]
        labels, _ = _model_menu_labels(models, None)
        assert labels[0] == "llama  (8K)"

    def test_small_ctx_rendered_raw(self):
        models = [{"name": "m", "details": {"context_length": 512}}]
        labels, _ = _model_menu_labels(models, None)
        assert labels[0] == "m  (512)"

    def test_current_index_none_when_absent(self):
        models = [{"name": "a", "details": {}}, {"name": "b", "details": {}}]
        _, current = _model_menu_labels(models, "zzz")
        assert current is None
        _, current = _model_menu_labels(models, None)
        assert current is None


class TestReportModelSwitch:
    def test_model_line_and_deltas(self, capsys):
        _report_model_switch(
            {"model": ("old", "new"), "num_ctx": (1024, 2048), "num_predict": (512, 1024)},
            "new",
        )
        out = _plain(capsys.readouterr().out)
        assert "Model changed: old -> new" in out
        assert "num_ctx: 1024 -> 2048" in out
        assert "num_predict: 512 -> 1024" in out

    def test_none_predict_renders_model_default(self, capsys):
        _report_model_switch({"num_predict": (512, None)}, "same")
        out = _plain(capsys.readouterr().out)
        assert "num_predict: 512 -> (model default)" in out

    def test_same_model_reports_and_deltas_print(self, capsys):
        # Same-model switches carry no "model" key; the reporter falls back
        # to (new, new) — the exact rendering the pre-picker /model handler
        # had — so only genuinely moved values appear as separate lines.
        _report_model_switch({"num_ctx": (1024, 2048)}, "same")
        out = _plain(capsys.readouterr().out)
        assert "Model changed: same -> same" in out
        assert "num_ctx: 1024 -> 2048" in out


class _FakeAgent:
    def __init__(self, model="m1"):
        self.model = model


class TestInteractiveModelSwitch:
    MODELS = [
        {"name": "m1", "details": {}},
        {"name": "m2", "details": {}},
        {"name": "m3", "details": {}},
    ]

    def _run(self, monkeypatch, picked_index, models, agent):
        import agentkthx.cli.picker as picker

        monkeypatch.setattr(picker.ArrowMenu, "run", lambda self, **kw: picked_index)
        calls = []

        def fake_switch(ag, new_model):
            calls.append(new_model)
            ag.model = new_model  # the real apply_model_switch does this
            return {"model": ("m1", new_model), "num_ctx": (1024, 2048)}

        monkeypatch.setattr("agentkthx.cli.apply_model_switch", fake_switch)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _interactive_model_switch(agent, models, title="Switch model (test)")
        return _plain(buf.getvalue()), calls

    def test_selection_switches_via_apply_model_switch(self, monkeypatch):
        agent = _FakeAgent("m1")
        out, calls = self._run(monkeypatch, 2, self.MODELS, agent)
        assert calls == ["m3"]
        assert agent.model == "m3"
        assert "Model changed: m1 -> m3" in out
        assert "num_ctx: 1024 -> 2048" in out

    def test_cancel_leaves_model_untouched(self, monkeypatch):
        agent = _FakeAgent("m1")
        out, calls = self._run(monkeypatch, None, self.MODELS, agent)
        assert calls == []
        assert agent.model == "m1"
        assert "Cancelled" in out

    def test_picking_current_model_is_a_noop(self, monkeypatch):
        agent = _FakeAgent("m2")
        out, calls = self._run(monkeypatch, 1, self.MODELS, agent)
        assert calls == []
        assert agent.model == "m2"
        assert "Already on m2" in out

    def test_menu_title_and_current_marker_reach_picker(self, monkeypatch):
        """The picker gets the filtered title and the ✓ current index."""
        import agentkthx.cli.picker as picker

        captured = {}

        class _SpyMenu(picker.ArrowMenu):
            def __init__(self, items, **kwargs):
                captured.update(kwargs)
                captured["items"] = list(items)
                super().__init__(items, **kwargs)

            def run(self, **kw):
                return None

        monkeypatch.setattr(picker, "ArrowMenu", _SpyMenu)
        agent = _FakeAgent("m2")
        with redirect_stdout(io.StringIO()):
            _interactive_model_switch(agent, self.MODELS, title="Switch model (ollama)")
        assert captured["title"] == "Switch model (ollama)"
        assert captured["current_index"] == 1
        assert captured["items"][0] == "m1"


class TestStartupModelPick:
    MODELS = [
        {"name": "glm-5.1", "details": {"context_length": 204800}},
        {"name": "qwen2.5:0.5b", "details": {}},
    ]

    def _config(self, default="glm-5.1"):
        return types.SimpleNamespace(backend="ollama", default_model=default)

    def _args(self, backend=None, model=None):
        return argparse.Namespace(backend=backend, model=model)

    def _patch_backend(self, monkeypatch, models=None, error=None):
        import agentkthx.backends as backends

        class _FakeBackend:
            is_cloud = False

            @staticmethod
            def list_models():
                if error:
                    raise RuntimeError(error)
                return models or []

        monkeypatch.setattr(backends, "get_backend", lambda name, **kw: _FakeBackend())

    def _run(self, monkeypatch, picked_index, args, config):
        import agentkthx.cli.picker as picker

        monkeypatch.setattr(picker.ArrowMenu, "run", lambda self, **kw: picked_index)
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _startup_model_pick(args, config)
        return result, _plain(buf.getvalue())

    def test_pick_returns_chosen_model(self, monkeypatch):
        self._patch_backend(monkeypatch, self.MODELS)
        result, out = self._run(monkeypatch, 1, self._args(), self._config(default="glm-5.1"))
        assert result == "qwen2.5:0.5b"

    def test_cancel_returns_none_with_hint(self, monkeypatch):
        self._patch_backend(monkeypatch, self.MODELS)
        result, out = self._run(monkeypatch, None, self._args(), self._config())
        assert result is None
        assert "cancelled" in out.lower()

    def test_backend_failure_degrades_to_none(self, monkeypatch):
        self._patch_backend(monkeypatch, error="connection refused")
        result, out = self._run(monkeypatch, 0, self._args(), self._config())
        assert result is None
        assert "Could not list models" in out

    def test_empty_backend_degrades_to_none(self, monkeypatch):
        self._patch_backend(monkeypatch, [])
        result, out = self._run(monkeypatch, 0, self._args(), self._config())
        assert result is None
        assert "No models found" in out

    def test_default_model_marked_current(self, monkeypatch):
        """config.default_model gets the ✓ current marker in the menu."""
        import agentkthx.cli.picker as picker

        self._patch_backend(monkeypatch, self.MODELS)
        captured = {}

        class _SpyMenu(picker.ArrowMenu):
            def __init__(self, items, **kwargs):
                captured.update(kwargs)
                captured["items"] = list(items)
                super().__init__(items, **kwargs)

            def run(self, **kw):
                return None

        monkeypatch.setattr(picker, "ArrowMenu", _SpyMenu)
        with redirect_stdout(io.StringIO()):
            _startup_model_pick(self._args(), self._config(default="qwen2.5:0.5b"))
        assert captured["current_index"] == 1
        assert captured["title"] == "Select a model (ollama)"
        assert captured["items"][0] == "glm-5.1  (200K)"

    # ── R07.23: FREE_ONLY filter ─────────────────────────────────────────

    MIXED_MODELS = [
        {"name": "openai/gpt-4o", "details": {}},  # paid
        {"name": "meta-llama/llama-3.1-8b-instruct:free", "details": {}},  # free
        {"name": "openrouter/free", "details": {}},  # free (named router)
        {"name": "openai/gpt-4o-mini", "details": {}},  # paid
    ]

    def test_free_only_filters_paid_models(self, monkeypatch):
        """R07.23: when OPENROUTER_FREE_ONLY=1, the picker should only show
        free models — paid models must NOT appear in the menu items."""
        import agentkthx.cli.picker as picker
        import agentkthx.config as cfg

        # Force OPENROUTER_FREE_ONLY=True
        monkeypatch.setattr(cfg, "OPENROUTER_FREE_ONLY", True)

        # Mock the openrouter _is_free_model to recognize our test data
        import agentkthx.plugins.openrouter.openrouter as or_plugin

        def _fake_is_free(name):
            return ":free" in name or name == "openrouter/free"

        monkeypatch.setattr(or_plugin, "_is_free_model", _fake_is_free)

        # Patch the backend to return our mixed list
        self._patch_backend(monkeypatch, self.MIXED_MODELS)

        # Spy on the menu to capture what items it was given
        captured = {}

        class _SpyMenu(picker.ArrowMenu):
            def __init__(self, items, **kwargs):
                captured["items"] = list(items)
                super().__init__(items, **kwargs)

            def run(self, **kw):
                return 0  # pick the first (free) model

        monkeypatch.setattr(picker, "ArrowMenu", _SpyMenu)

        config = types.SimpleNamespace(backend="openrouter", default_model="openrouter/free")
        with redirect_stdout(io.StringIO()):
            result = _startup_model_pick(self._args(backend="openrouter"), config)

        # Only 2 free models should be in the menu
        assert len(captured["items"]) == 2
        assert any("llama-3.1-8b" in item for item in captured["items"])
        assert any("openrouter/free" in item for item in captured["items"])
        # Paid models must NOT appear
        assert not any("gpt-4o" in item for item in captured["items"])
        # Result should be the first free model
        assert result is not None

    def test_free_only_empty_returns_none(self, monkeypatch):
        """R07.23: if FREE_ONLY filters to empty, the picker should return
        None (fall back to default model) with a helpful message."""
        import agentkthx.config as cfg

        monkeypatch.setattr(cfg, "OPENROUTER_FREE_ONLY", True)

        import agentkthx.plugins.openrouter.openrouter as or_plugin

        # Mock _is_free_model to return False for everything — simulates
        # a catalog with no free models
        monkeypatch.setattr(or_plugin, "_is_free_model", lambda name: False)

        self._patch_backend(monkeypatch, self.MIXED_MODELS)

        config = types.SimpleNamespace(backend="openrouter", default_model="openrouter/free")
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _startup_model_pick(self._args(backend="openrouter"), config)

        assert result is None
        out = _plain(buf.getvalue())
        assert "No free models found" in out
        assert "FREE_ONLY" in out

    def test_no_free_only_env_shows_all_models(self, monkeypatch):
        """R07.23 regression guard: when FREE_ONLY is NOT set, the picker
        should show ALL models (paid + free) — no filtering."""
        import agentkthx.cli.picker as picker
        import agentkthx.config as cfg

        monkeypatch.setattr(cfg, "OPENROUTER_FREE_ONLY", False)
        self._patch_backend(monkeypatch, self.MIXED_MODELS)

        captured = {}

        class _SpyMenu(picker.ArrowMenu):
            def __init__(self, items, **kwargs):
                captured["items"] = list(items)
                super().__init__(items, **kwargs)

            def run(self, **kw):
                return 0

        monkeypatch.setattr(picker, "ArrowMenu", _SpyMenu)

        config = types.SimpleNamespace(backend="openrouter", default_model="openrouter/free")
        with redirect_stdout(io.StringIO()):
            _startup_model_pick(self._args(backend="openrouter"), config)

        # All 4 models should appear — no filtering
        assert len(captured["items"]) == 4


# ═══════════════════════════════════════════════════════════════════════
# 4. cmd_chat wiring pins
# ═══════════════════════════════════════════════════════════════════════

CHAT_PATH = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "commands" / "chat.py"


class TestChatWiringPins:
    @pytest.fixture(scope="class")
    def chat_source(self):
        return CHAT_PATH.read_text(encoding="utf-8")

    def test_startup_picker_guard_conditions(self, chat_source):
        """The startup picker fires ONLY when: no -m, no ACP, TTY, no env override."""
        assert 'getattr(args, "model", None) is None' in chat_source
        assert "and acp is None" in chat_source
        assert "and sys.stdin.isatty()" in chat_source
        assert 'os.environ.get("AGENTKTHX_MODEL")' in chat_source
        assert "picked = _startup_model_pick(args, config)" in chat_source

    def test_startup_pick_runs_before_agent_build(self, chat_source):
        """Picker must set args.model BEFORE _build_agent consumes it."""
        pick_pos = chat_source.index("picked = _startup_model_pick(args, config)")
        build_pos = chat_source.index("agent = _cli._build_agent(args, config)")
        assert pick_pos < build_pos

    def test_models_command_has_tty_interactive_branch(self, chat_source):
        assert 'if user_input == "/models" or user_input.startswith("/models "):' in chat_source
        assert "_interactive_model_switch(" in chat_source
        # The non-TTY listing is kept (piped scripts / tests keep the table)
        assert "Available models" in chat_source

    def test_models_interactive_branch_uses_same_switch_path(self, chat_source):
        """Picker and /model <name> share apply_model_switch + the reporter."""
        assert (
            "_report_model_switch(_cli.apply_model_switch(agent, new_model), new_model)"
            in chat_source
        )

    def test_help_mentions_interactive_switcher(self, chat_source):
        assert "Interactive model switcher" in chat_source

    def test_picker_module_exists_and_is_stdlib_only(self):
        """No third-party imports in the picker (runtime zero-dep contract)."""
        import ast

        picker_path = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "picker.py"
        tree = ast.parse(picker_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        third_party = imported - {
            "os",
            "sys",
            "select",
            "termios",
            "tty",
            "msvcrt",
            "shutil",
            "__future__",
        }
        assert not third_party, f"picker.py imports non-stdlib modules: {third_party}"


# ═══════════════════════════════════════════════════════════════════════
# 5. agentkthx souls subcommand
# ═══════════════════════════════════════════════════════════════════════

from agentkthx.cli.commands.souls import _default_soul_name, cmd_souls  # noqa: E402


def _run_souls(name=None):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_souls(argparse.Namespace(name=name))
    return rc, _plain(buf.getvalue())


class TestSoulsCommand:
    def test_listing_shows_all_bundled_souls(self):
        rc, out = _run_souls()
        assert rc == 0
        for name in ("kthx-helper", "kthx-skills", "kthx-trading"):
            assert name in out
        assert "v1.0.0" in out and "v0.1.0" in out

    def test_listing_marks_the_default_soul(self):
        rc, out = _run_souls()
        assert rc == 0
        assert "(default)" in out
        # The marker sits on the introspected default's row
        default = _default_soul_name()
        row = next(line for line in out.splitlines() if default in line)
        assert "✓" in row and "(default)" in row

    def test_listing_carries_usage_hints(self):
        rc, out = _run_souls()
        assert "--soul <name>" in out
        assert "/soul <name>" in out
        assert "agentkthx souls <name>" in out

    def test_detail_view_by_name(self):
        rc, out = _run_souls("kthx-trading")
        assert rc == 0
        assert "Kthx Trading Analyst" in out
        assert "kthx-trading v0.1.0" in out
        assert "finance" in out
        assert "Allowed tools:" in out
        assert "web_search" in out

    def test_detail_unknown_name_errors_with_fuzzy_suggestion(self):
        rc, out = _run_souls("kthx-trade")
        assert rc == 1
        assert "Soul not found: kthx-trade" in out
        assert "did you mean 'kthx-trading'?" in out
        assert "kthx-helper, kthx-skills, kthx-trading" in out

    def test_default_soul_name_follows_constructor(self):
        """Drift-proof: introspected from AgentSetupMixin, no literal here."""
        from inspect import signature

        from agentkthx.core.agent_setup import AgentSetupMixin

        expected = signature(AgentSetupMixin.__init__).parameters["soul"].default
        assert _default_soul_name() == expected == "kthx-helper"


class TestSoulsWiring:
    def test_parser_accepts_bare_souls(self):
        from agentkthx.cli.parser import create_parser

        args = create_parser().parse_args(["souls"])
        assert args.command == "souls"
        assert args.name is None

    def test_parser_accepts_souls_with_name(self):
        from agentkthx.cli.parser import create_parser

        args = create_parser().parse_args(["souls", "kthx-helper"])
        assert args.name == "kthx-helper"

    def test_root_help_lists_souls_alphabetically(self):
        from agentkthx.cli.parser import create_parser

        names = list(create_parser()._subparsers_action.choices)
        assert "souls" in names
        assert names.index("soul") < names.index("souls") < names.index("test")

    def test_main_dispatch_registered(self):
        main_path = Path(__file__).resolve().parent.parent / "agentkthx" / "cli" / "main.py"
        src = main_path.read_text(encoding="utf-8")
        assert '"souls": cmd_souls' in src

    def test_commands_facade_exports_cmd_souls(self):
        from agentkthx.cli.commands import cmd_souls as exported

        assert callable(exported)
