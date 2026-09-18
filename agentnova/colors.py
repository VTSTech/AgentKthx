"""
\u269b\ufe0f AgentNova -- Colors
Shared ANSI color utilities for terminal output.

Patched for Windows: enables Virtual Terminal processing on the legacy
console (cmd.exe / conhost) at import time so raw ANSI escape sequences are
interpreted instead of leaking as literal `\\x1b[96m...` text.

stdlib-only. No external deps (no colorama).

Written by VTSTech -- https://www.vts-tech.org
"""

from __future__ import annotations

import os
import re
import sys


# ---------------------------------------------------------------------------
# Windows console: flip on ENABLE_VIRTUAL_TERMINAL_PROCESSING.
#
# On Windows 10 1607+ this is a one-shot SetConsoleMode call on STD_OUTPUT
# and STD_ERROR. On older Windows it silently no-ops; the escape codes will
# still leak there, but those systems are vanishingly rare in 2026 and
# forcing ANSI.SYS or a wrapper was always the user's job anyway.
#
# This is exactly what colorama.just_fix_windows_console() does internally --
# we just inline it so we stay stdlib-only.
# ---------------------------------------------------------------------------
if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes

        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # signatures
        _kernel32.GetStdHandle.restype = wintypes.HANDLE
        _kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        _kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE,
                                              ctypes.POINTER(wintypes.DWORD)]
        _kernel32.GetConsoleMode.restype = wintypes.BOOL
        _kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _kernel32.SetConsoleMode.restype = wintypes.BOOL

        _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        _STD_OUTPUT_HANDLE = 0xFFFFFFF5  # -11 as DWORD
        _STD_ERROR_HANDLE = 0xFFFFFFF4   # -12 as DWORD

        def _enable_vt(handle_value: int) -> None:
            h = _kernel32.GetStdHandle(handle_value)
            if not h:
                return
            mode = wintypes.DWORD()
            if not _kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                return
            # only flip the VT bit, leave the rest alone
            _kernel32.SetConsoleMode(h, mode.value |
                                     _ENABLE_VIRTUAL_TERMINAL_PROCESSING)

        # Only touch real console handles -- if stdout is redirected to a
        # file/pipe, GetConsoleMode returns 0 and we skip harmlessly.
        if sys.stdout.isatty():
            _enable_vt(_STD_OUTPUT_HANDLE)
        if sys.stderr.isatty():
            _enable_vt(_STD_ERROR_HANDLE)
    except Exception:
        # Any failure here (restricted permissions, ancient Windows,
        # stripped-down Wine, etc.) must not crash the CLI on import.
        pass


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
          2. stdout not a tty      -> off (piping to file/pipe)
          3. Windows               -> on (we enabled VT processing at
                                      import; WT_SESSION / ANSICON are
                                      also fine; legacy conhost without
                                      VT support would just no-op the
                                      escapes harmlessly)
          4. POSIX                 -> on (xterm/gnome/foot/etc. all
                                      interpret ANSI natively)

        CLI flags (CLICOLOR_FORCE / NO_COLOR) are honored here.
        """
        if os.environ.get("NO_COLOR"):
            return False
        # CLICOLOR=0 also disables on some platforms
        if os.environ.get("CLICOLOR") == "0":
            return False
        if not sys.stdout.isatty():
            # ...unless user forces it
            if not os.environ.get("CLICOLOR_FORCE"):
                return False
        # Windows: VT was flipped on above (or the terminal already
        # supports it -- WT_SESSION / ANSICON / ConEmu). Treat as on.
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
