"""Interactive arrow-key menu — pure stdlib (R07.19 follow-up #7).

Backs the chat ``/models`` switcher and the startup model picker that
runs when ``agentkthx chat`` is launched without ``-m/--model``. Also
usable for any future "pick one from a list" surface (souls, skills, …).

Design notes:

- **Rendering is separated from terminal I/O.** Every frame is produced
  by :meth:`ArrowMenu.render` (a pure string) and navigation is plain
  state mutation on the menu instance — both are unit-testable without
  a terminal. ``run()`` is the only piece that touches a real terminal.
- **POSIX** key input uses :mod:`termios` + :mod:`tty` in cbreak mode:
  canonical-mode line buffering is off (keys arrive immediately) while
  ISIG stays on, so Ctrl+C still raises ``KeyboardInterrupt`` — caught
  in the loop and mapped to "cancel", never killing the host chat
  session. Escape sequences (``ESC [ A`` …) are disambiguated from a
  lone Esc press with a ``select()`` peek after the ESC byte.
- **Windows** uses :mod:`msvcrt.getwch()`; arrow keys arrive as an
  ``\\xe0`` (or ``\\x00``) prefix followed by a scan code.
- **Non-TTY fallback** (piped scripts, tests, ACP): ``run()`` prints a
  numbered list and reads a choice from stdin, so the component
  degrades gracefully everywhere instead of hanging on raw-mode reads.

Frame layout (constant height while items exceed the viewport)::

    <title> (↑2 ↓5 more)
      ❯ glm-5.1
        glm-4.7-flash  ✓
        qwen2.5:0.5b
    ↑/↓ move · Enter select · q/Esc cancel

The viewport slides to keep the cursor visible; the "N more" scroll
indicators live on the title line so the frame height never changes
between redraws (no stale-line artifacts when the window slides).
"""

from __future__ import annotations

import os
import sys

# Platform capability probes (import-time, guarded — the module must
# import cleanly on every platform so chat.py can import it unconditionally).
try:
    import select as _select
    import termios as _termios
    import tty as _tty

    _HAVE_TERMIOS = True
except ImportError:  # pragma: no cover — Windows
    _HAVE_TERMIOS = False

try:
    import msvcrt as _msvcrt

    _HAVE_MSVCRT = True
except ImportError:  # pragma: no cover — POSIX
    _HAVE_MSVCRT = False


# ============================================================================
# Key normalization
# ============================================================================

# Escape sequences / control chars → semantic key names. Single printable
# characters (q, j, k, g, G) are returned as-is by map_key() and interpreted
# by the menu loop, keeping this table purely about byte sequences.
_KEY_NAMES = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1bOH": "home",  # application cursor mode (some terminals)
    "\x1bOF": "end",
    "\x1b[1~": "home",
    "\x1b[4~": "end",
    "\x1b[5~": "pgup",
    "\x1b[6~": "pgdn",
    "\r": "enter",
    "\n": "enter",
    "\x1b": "escape",
    "\x03": "ctrl-c",
    "\x04": "ctrl-d",
}

# Windows msvcrt extended-key scan codes (after the \x00 or \xe0 prefix).
_WINDOWS_SCAN_CODES = {
    "H": "up",
    "P": "down",
    "K": "left",
    "M": "right",
    "G": "home",
    "O": "end",
    "I": "pgup",
    "Q": "pgdn",
}


def map_key(ch: str) -> str:
    """Normalize one key event (char or escape sequence) to a key name.

    Returns the semantic name from the table above, the bare character
    for single printable characters (so the menu loop can treat ``q``,
    ``j``, ``k`` … as commands), or ``""`` for unrecognized bytes
    (ignored by the loop).
    """
    if ch in _KEY_NAMES:
        return _KEY_NAMES[ch]
    if len(ch) == 1 and ch.isprintable():
        return ch
    return ""


# ============================================================================
# Platform key readers
# ============================================================================


def _read_key_posix() -> str:
    """Read one key event from stdin — POSIX, cbreak already active.

    A lone ESC is told apart from an escape sequence by peeking the fd
    with ``select()``: terminals transmit sequences (e.g. ``ESC [ A``)
    atomically, so bytes are already buffered when a sequence follows.
    """
    fd = sys.stdin.fileno()
    data = os.read(fd, 1)
    if not data:
        return "ctrl-d"  # EOF
    ch = data.decode("utf-8", "replace")
    if ch != "\x1b":
        return map_key(ch)
    ready, _, _ = _select.select([fd], [], [], 0.05)
    if not ready:
        return "escape"
    rest = os.read(fd, 16).decode("utf-8", "replace")
    return map_key("\x1b" + rest)


def _read_key_windows() -> str:  # pragma: no cover — Windows only
    """Read one key event via msvcrt (console)."""
    ch = _msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = _msvcrt.getwch()
        return _WINDOWS_SCAN_CODES.get(code, "")
    return map_key(ch)


def _read_key() -> str:
    """Read one key event (dispatches per platform).

    Module-level so tests can monkeypatch
    ``agentkthx.cli.picker._read_key`` and drive the menu with canned
    key sequences without a terminal.
    """
    if _HAVE_MSVCRT:  # pragma: no cover — Windows only
        return _read_key_windows()
    return _read_key_posix()


# ============================================================================
# ArrowMenu
# ============================================================================


class ArrowMenu:
    """Single-select menu navigable with the arrow keys.

    Usage::

        menu = ArrowMenu(labels, title="Select a model", current_index=0)
        choice = menu.run()          # index, or None when cancelled
        if choice is not None:
            print(f"picked: {labels[choice]}")

    The class is deliberately split into a pure core (``move_*``,
    ``window``, ``render`` — fully unit-testable) and a thin ``run()``
    I/O shell that switches between the termios loop (POSIX), the
    msvcrt loop (Windows) and a numbered-input fallback (non-TTY
    stdin), so scripted / piped invocations never hang on raw reads.
    """

    def __init__(
        self,
        items: list[str],
        *,
        title: str = "Select an item",
        cursor: int = 0,
        visible: int = 10,
        current_index: int | None = None,
    ):
        if not items:
            raise ValueError("ArrowMenu needs at least one item")
        self.items = list(items)
        self.title = title
        self.visible = max(1, visible)
        self.current_index = current_index
        self.cursor = min(max(0, cursor), len(items) - 1)

    # ── navigation (pure) ────────────────────────────────────────────

    def move_up(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_down(self) -> None:
        self.cursor = min(len(self.items) - 1, self.cursor + 1)

    def move_home(self) -> None:
        self.cursor = 0

    def move_end(self) -> None:
        self.cursor = len(self.items) - 1

    def page_up(self) -> None:
        self.cursor = max(0, self.cursor - self.visible)

    def page_down(self) -> None:
        self.cursor = min(len(self.items) - 1, self.cursor + self.visible)

    # ── viewport (pure) ──────────────────────────────────────────────

    def window(self) -> tuple[int, int]:
        """``(start, stop)`` slice bounds of the items currently visible.

        Short lists render in full; long lists slide a ``visible``-tall
        viewport so the cursor row always stays inside it.
        """
        if len(self.items) <= self.visible:
            return 0, len(self.items)
        start = min(
            max(0, self.cursor - self.visible // 2),
            len(self.items) - self.visible,
        )
        return start, start + self.visible

    # ── rendering (pure) ─────────────────────────────────────────────

    def render(self) -> str:
        """Render the full frame (title + item rows + key hints).

        The cursor row is marked with ``❯`` (``>`` when the terminal
        lacks unicode support per ``colors.is_unicode_ok``); the row
        matching ``current_index`` carries a green ``✓`` suffix.
        """
        from ..colors import dim, green, is_unicode_ok, yellow

        cursor_glyph = "❯" if is_unicode_ok() else ">"
        start, stop = self.window()
        above, below = start, len(self.items) - stop

        scroll = []
        if above:
            scroll.append(f"↑{above}")
        if below:
            scroll.append(f"↓{below}")
        scroll_s = dim(f" ({' '.join(scroll)} more)") if scroll else ""

        lines = [f"{yellow(self.title)}{scroll_s}"]
        for idx in range(start, stop):
            if idx == self.cursor:
                row = f" {cursor_glyph} {self.items[idx]}"
            else:
                row = f"   {self.items[idx]}"
            if self.current_index is not None and idx == self.current_index:
                row += green(" \u2713")
            lines.append(row)
        lines.append(
            dim(
                "(\u2191/\u2193 or j/k move \u00b7 g/G home/end \u00b7 Enter select \u00b7 q/Esc cancel)"
            )
        )
        return "\n".join(lines)

    # ── drawing (I/O) ────────────────────────────────────────────────

    def _frame_height(self) -> int:
        return len(self.render().split("\n"))

    def _draw(self, stdout, first: bool) -> None:
        """Redraw the frame in place (cursor-up + clear-line per row)."""
        lines = self.render().split("\n")
        if not first:
            stdout.write(f"\x1b[{len(lines) - 1}A")  # up to frame top
        for i, line in enumerate(lines):
            stdout.write("\r\x1b[2K")  # return + clear the line
            stdout.write(line + ("\n" if i < len(lines) - 1 else ""))
        stdout.flush()

    def _erase(self, stdout) -> None:
        """Wipe the frame (the caller prints a one-line outcome instead)."""
        height = self._frame_height()
        stdout.write(f"\x1b[{height - 1}A")
        for i in range(height):
            stdout.write("\r\x1b[2K" + ("\n" if i < height - 1 else ""))
        stdout.flush()

    # ── main loop (I/O) ──────────────────────────────────────────────

    def run(self, *, stdin=None, stdout=None) -> int | None:
        """Run the interactive loop.

        Returns the selected index, or ``None`` when the user cancelled
        (Esc / q / Ctrl+C / Ctrl+D / empty numbered-fallback answer).
        Falls back to a numbered prompt when stdin is not a TTY.
        """
        stdin = sys.stdin if stdin is None else stdin
        stdout = sys.stdout if stdout is None else stdout
        if not stdin.isatty():
            return self._numbered_fallback(stdin, stdout)
        self._clamp_visible_to_terminal()
        if _HAVE_TERMIOS:
            return self._run_posix(stdin, stdout)
        if _HAVE_MSVCRT:  # pragma: no cover — Windows only
            return self._loop(stdout)
        return self._numbered_fallback(stdin, stdout)

    def _clamp_visible_to_terminal(self) -> None:
        """Cap the viewport so the frame fits the terminal (chat scroll region)."""
        try:
            import shutil

            lines = shutil.get_terminal_size().lines
        except Exception:
            return
        self.visible = max(3, min(self.visible, lines - 4))

    def _run_posix(self, stdin, stdout) -> int | None:
        """cbreak-mode loop with a guaranteed terminal-state restore."""
        fd = stdin.fileno()
        old = _termios.tcgetattr(fd)
        try:
            _tty.setcbreak(fd)
            return self._loop(stdout)
        finally:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old)

    def _loop(self, stdout) -> int | None:
        """Read keys → mutate state → redraw, until select or cancel."""
        self._draw(stdout, first=True)
        while True:
            try:
                key = _read_key()
            except KeyboardInterrupt:
                # POSIX cbreak keeps ISIG on — Ctrl+C surfaces here as
                # SIGINT instead of a byte; cancel, don't kill the REPL.
                key = "ctrl-c"
            if key in ("up", "k"):
                self.move_up()
            elif key in ("down", "j"):
                self.move_down()
            elif key in ("home", "g"):
                self.move_home()
            elif key in ("end", "G"):
                self.move_end()
            elif key == "pgup":
                self.page_up()
            elif key == "pgdn":
                self.page_down()
            elif key == "enter":
                self._erase(stdout)
                return self.cursor
            elif key in ("escape", "q", "ctrl-c", "ctrl-d"):
                self._erase(stdout)
                return None
            else:
                continue  # unrecognized key — ignore without a redraw
            self._draw(stdout, first=False)

    def _numbered_fallback(self, stdin, stdout) -> int | None:
        """Non-TTY fallback: numbered list + one line read from stdin."""
        from ..colors import dim, green

        print(self.title, file=stdout)
        for i, item in enumerate(self.items, 1):
            mark = " " + green("✓ current") if self.current_index == i - 1 else ""
            print(f"  {i}. {item}{mark}", file=stdout)
        print("Select number (Enter cancels): ", end="", file=stdout)
        try:
            raw = (stdin.readline() or "").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=stdout)
            return None
        if not raw:
            print(file=stdout)
            return None
        try:
            n = int(raw)
        except ValueError:
            print(dim("Invalid selection — cancelled."), file=stdout)
            return None
        if 1 <= n <= len(self.items):
            return n - 1
        print(dim("Out of range — cancelled."), file=stdout)
        return None
