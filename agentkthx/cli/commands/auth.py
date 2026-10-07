"""``agentkthx auth`` subcommand.

Top-level entry point for the interactive ``/auth`` picker — lets users
set API keys + toggle FREE_ONLY flags for every cloud backend without
having to start a chat session first. Changes persist to
``~/.agentkthx/.env`` (loaded on every startup; shell exports still take
precedence).

Backs the in-chat ``/auth`` slash command (chat.py:546) — the same
``run_auth_picker`` function from ``agentkthx.cli.auth`` is invoked, just
exposed as a top-level subcommand so users can configure keys BEFORE
launching a chat session (which is exactly when the missing-key error
fires).

Example::

    agentkthx auth

Selection flow:
  - Arrow keys to navigate (or numbered fallback on non-TTY stdin)
  - Enter to set/toggle the highlighted entry
  - For "key" entries: prompts for the value (input is hidden)
      - Type the key + Enter to set
      - Type ``-`` + Enter to clear
      - Empty Enter / Ctrl+C to leave unchanged
  - For "flag" entries: Enter toggles immediately
  - ``q`` / Esc / Ctrl+C closes the menu

Once closed, every backend's auth state is re-read from the env file on
the next CLI invocation — no shell export needed.
"""

from __future__ import annotations

import argparse


def cmd_auth(args: argparse.Namespace) -> int:
    """Run the interactive auth picker (sets keys + flags, persists to .env).

    Returns 0 on clean exit, 1 on unexpected error (Ctrl+C during the
    picker itself is treated as a clean exit by ``run_auth_picker``).
    """
    from ..auth import run_auth_picker

    # ``agent=None`` is the correct call for the top-level CLI path — the
    # /auth slash command in chat.py passes the live agent so the current
    # session picks up the change. The top-level command has no live
    # agent (we're configuring BEFORE launching one), so the picker
    # only writes to ~/.agentkthx/.env. The next CLI invocation reads
    # that file via env_file.load_env_file() at config.py import time.
    try:
        run_auth_picker(agent=None)
    except KeyboardInterrupt:
        # Ctrl+C at the top-level menu = clean exit (the picker itself
        # already prints the "saved to ..." summary on its own exit path).
        print()
        return 0
    except Exception as e:
        # Surface unexpected errors clearly — the picker is interactive
        # so failures should be visible to the user.
        from ...colors import red

        print(red(f"auth picker error: {e}"))
        return 1
    return 0
