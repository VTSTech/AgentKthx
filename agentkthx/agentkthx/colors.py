"""
\u269b\ufe0f AgentKthx -- Colors
Shared ANSI color utilities for terminal output.

Patched for Windows:
  1. Enables Virtual Terminal processing on the legacy console
     (cmd.exe / conhost) at import time so raw ANSI escape sequences are
     interpreted instead of leaking as literal `\\x1b[96m...` text.
  2. Switches the console output codepage to UTF-8 (CP_UTF8 = 65001) so
     non-OEM glyphs (braille, checkmark, etc.) survive pipe/redirection
     and so `more`/`find`/etc. don't mangle them.
  3. Provides ASCII fallbacks for the few Unicode symbols used in the CLI
     (checkmark / circle / cross / filled dot) so a font without those
     glyphs still produces legible output.  Auto-fallback can be
     overridden with the env var `AGENTKTHX_GLYPHS=unicode|ascii|auto`.

stdlib-only. No external deps (no colorama).

Written by VTSTech -- https://www.vts-tech.org
"""

from __future__ import annotations

import os
import re
import sys


# ---------------------------------------------------------------------------
# Windows console setup.
#
# Order matters: enable VT first, then set the UTF-8 codepage. Both are
# idempotent and safe on any Windows 10 1607+ system. On older Windows
# the calls silently no-op (GetConsoleMode returns 0, SetConsoleOutputCP
# returns 0) and we leave the user with whatever font/codepage they had.
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # --- VT processing --------------------------------------------------
        _kernel32.GetStdHandle.restype = wintypes.HANDLE
        _kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        _kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE,
                                              ctypes.POINTER(wintypes.DWORD)]
        _kernel32.GetConsoleMode.restype = wintypes.BOOL
        _kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _kernel32.SetConsoleMode.restype = wintypes.BOOL

        _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        _ENABLE_PROCESSED_OUTPUT = 0x0001
        _STD_OUTPUT_HANDLE = 0xFFFFFFF5  # -11 as DWORD
        _STD_ERROR_HANDLE = 0xFFFFFFF4   # -12 as DWORD

        def _enable_vt(handle_value: int) -> None:
            h = _kernel32.GetStdHandle(handle_value)
            if not h:
                return
            mode = wintypes.DWORD()
            if not _kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                return
            # Only flip the VT bit; preserve whatever else was set
            # (wrap-at-EOL, processed-output, etc.).
            _kernel32.SetConsoleMode(h, mode.value |
                                     _ENABLE_VIRTUAL_TERMINAL_PROCESSING)

        # --- UTF-8 output codepage -----------------------------------------
        # CP_UTF8 = 65001.  Returns 0 on failure, non-zero on success.
        # We call it unconditionally -- it's harmless and means anything
        # downstream (pipes, `more`, redirected file, etc.) gets UTF-8
        # bytes rather than cp437/cp1252.
        try:
            _kernel32.SetConsoleOutputCP.restype = wintypes.BOOL
            _kernel32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
            _kernel32.SetConsoleOutputCP(65001)
            # Input side, for symmetry (helps any future readline work).
            _kernel32.SetConsoleCP.restype = wintypes.BOOL
            _kernel32.SetConsoleCP.argtypes = [wintypes.UINT]
            _kernel32.SetConsoleCP(65001)
        except Exception:
            pass

        if sys.stdout.isatty():
            _enable_vt(_STD_OUTPUT_HANDLE)
        if sys.stderr.isatty():
            _enable_vt(_STD_ERROR_HANDLE)
    except Exception:
        # Any failure here (restricted permissions, ancient Windows,
        # stripped-down Wine, etc.) must not crash the CLI on import.
        pass


# ---------------------------------------------------------------------------
# Unicode glyph support.
#
# Braille (U+2800-U+28FF), Dingbats (U+2700-U+27BF) and Geometric Shapes
# (U+25A0-U+25FF) are not present in cmd.exe's default raster font.  Even
# on Windows 10/11, if the user hasn't switched the console font to
# Cascadia Mono / Consolas / similar, these codepoints render as `\\u25a1`
# tofu boxes.
#
# We provide both the "real" Unicode glyphs and ASCII fallbacks.  The
# choice is controlled by:
#   AGENTKTHX_GLYPHS=auto  -- heuristically decide (default)
#   AGENTKTHX_GLYPHS=unicode  -- force Unicode
#   AGENTKTHX_GLYPHS=ascii   -- force ASCII fallback
# ---------------------------------------------------------------------------

_GLYPH_MODE = os.environ.get("AGENTKTHX_GLYPHS", "auto").lower()


def _looks_unicode_capable() -> bool:
    """Heuristic: is the active console likely to render our glyphs?

    Returns False on Windows unless we see a positive signal that the
    user is on a modern terminal (Windows Terminal, ConEmu, ANSICON, or
    has WT_SESSION set).  On POSIX we assume yes -- xterm/gnome/foot/etc.
    all ship fonts that cover braille and the basic symbol blocks.
    """
    if _GLYPH_MODE == "unicode":
        return True
    if _GLYPH_MODE == "ascii":
        return False
    # auto
    if os.name != "nt":
        return True
    # Windows auto-detect: trust a few well-known modern-terminal env vars.
    # Classic cmd.exe / conhost without these almost certainly uses a
    # raster font that lacks braille + checkmark glyphs.
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("ANSICON"):
        return True
    if os.environ.get("ConEmuANSI") == "ON":
        return True
    if os.environ.get("TERM_PROGRAM") in {"WezTerm", "vscode", "hyper"}:
        return True
    if os.environ.get("MINTTY"):
        return True
    return False


_UNICODE_OK = _looks_unicode_capable()


# Glyph constants -- import these instead of hardcoding `\\u2713` etc.
# so all of AgentKthx gets the same fallback behaviour.
if _UNICODE_OK:
    GLYPH_OK = "\u2713"        # check mark
    GLYPH_REACT = "\u25cb"     # white circle
    GLYPH_FAIL = "\u2717"     # ballot X
    GLYPH_UNKNOWN = "?"        # already ASCII
    GLYPH_DOT_ON = "\u25cf"    # black circle
    GLYPH_DOT_OFF = "\u25cb"   # white circle (same as REACT)
    GLYPH_ATOM = "\u269b"      # atom symbol (also missing from raster fonts)
else:
    GLYPH_OK = "v"             # ASCII fallback: v for "verified/valid"
    GLYPH_REACT = "o"          # ASCII fallback: little circle
    GLYPH_FAIL = "x"           # ASCII fallback: x for "fail"
    GLYPH_UNKNOWN = "?"
    GLYPH_DOT_ON = "*"
    GLYPH_DOT_OFF = "."
    GLYPH_ATOM = "@"           # ASCII fallback for the atom symbol


class Color:
    """ANSI color codes for terminal output."""
    # Reset
    RESET = "\033[0m"

    # Basic colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Styles
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    @classmethod
    def supports_color(cls) -> bool:
        """Check if terminal supports color.

        Decision order:
          1. NO_COLOR env var set  -> off
          2. CLICOLOR=0           -> off
          3. stdout not a tty      -> off (unless CLICOLOR_FORCE)
          4. otherwise            -> on  (VT processing was enabled
                                          at import on Windows)
        """
        if os.environ.get("NO_COLOR"):
            return False
        if os.environ.get("CLICOLOR") == "0":
            return False
        if not sys.stdout.isatty():
            if not os.environ.get("CLICOLOR_FORCE"):
                return False
        return True


# Global color enabled flag
_COLOR_ENABLED = Color.supports_color()


def set_color_enabled(enabled: bool) -> None:
    """Enable or disable color output globally."""
    global _COLOR_ENABLED
    _COLOR_ENABLED = enabled


def is_color_enabled() -> bool:
    """Check if color output is enabled."""
    return _COLOR_ENABLED


def is_unicode_ok() -> bool:
    """True if we can safely emit braille / symbol glyphs."""
    return _UNICODE_OK


def c(text: str, *colors: str) -> str:
    """Apply colors to text if color is enabled."""
    if not _COLOR_ENABLED or not colors:
        return text
    return "".join(colors) + text + Color.RESET


def dim(text: str) -> str:
    """Dim text."""
    return c(text, Color.DIM)


def bold(text: str) -> str:
    """Bold text."""
    return c(text, Color.BOLD)


def cyan(text: str) -> str:
    """Cyan text."""
    return c(text, Color.CYAN)


def green(text: str) -> str:
    """Green text."""
    return c(text, Color.GREEN)


def yellow(text: str) -> str:
    """Yellow text."""
    return c(text, Color.YELLOW)


def red(text: str) -> str:
    """Red text."""
    return c(text, Color.RED)


def magenta(text: str) -> str:
    """Magenta text."""
    return c(text, Color.MAGENTA)


def blue(text: str) -> str:
    """Blue text."""
    return c(text, Color.BLUE)


def bright_cyan(text: str) -> str:
    """Bright cyan text."""
    return c(text, Color.BRIGHT_CYAN)


def bright_green(text: str) -> str:
    """Bright green text."""
    return c(text, Color.BRIGHT_GREEN)


def bright_yellow(text: str) -> str:
    """Bright yellow text."""
    return c(text, Color.BRIGHT_YELLOW)


def bright_magenta(text: str) -> str:
    """Bright magenta text."""
    return c(text, Color.BRIGHT_MAGENTA)


def bright_red(text: str) -> str:
    """Bright red text."""
    return c(text, Color.BRIGHT_RED)


# ANSI escape code pattern for stripping
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')


def visible_len(text: str) -> int:
    """Get the visible length of text (excluding ANSI codes)."""
    return len(_ANSI_ESCAPE.sub('', text))


def pad_colored(text: str, width: int, align: str = 'left') -> str:
    """Pad colored text to a given visible width.

    Args:
        text: Text that may contain ANSI color codes
        width: Target visible width
        align: 'left', 'right', or 'center'

    Returns:
        Text padded with spaces to reach the target visible width
    """
    visible = visible_len(text)
    padding = width - visible

    if padding <= 0:
        return text

    if align == 'left':
        return text + ' ' * padding
    elif align == 'right':
        return ' ' * padding + text
    else:  # center
        left = padding // 2
        right = padding - left
        return ' ' * left + text + ' ' * right


__all__ = [
    "Color",
    "set_color_enabled",
    "is_color_enabled",
    "is_unicode_ok",
    "GLYPH_OK",
    "GLYPH_REACT",
    "GLYPH_FAIL",
    "GLYPH_UNKNOWN",
    "GLYPH_DOT_ON",
    "GLYPH_DOT_OFF",
    "GLYPH_ATOM",
    "c",
    "dim",
    "bold",
    "cyan",
    "green",
    "yellow",
    "red",
    "magenta",
    "blue",
    "bright_cyan",
    "bright_green",
    "bright_yellow",
    "bright_magenta",
    "bright_red",
    "visible_len",
    "pad_colored",
]
