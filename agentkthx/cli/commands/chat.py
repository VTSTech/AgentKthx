"""`agentkthx chat` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
import threading

from ...agent_mode import _format_response_stats
from ...colors import bold, bright_cyan, bright_green, cyan, dim, green, magenta, red, yellow
from ...config import get_config
from ...tools import make_builtin_registry
from ..footer import footer_line1, footer_line2
from ..utils import _print_agent_steps

# ============================================================================
# Primary User (R07.19)
# ============================================================================

# The chat prompt used to be a hardcoded "You:". R07.19 asks who is chatting
# and renders their name instead ("VTSTech: "). Resolution order:
#
#   1. --user flag            (explicit, skips the startup prompt)
#   2. AGENTKTHX_USER env var (scriptable, skips the startup prompt)
#   3. Interactive prompt     (TTY only — "Primary User [<os-user>]: ",
#                              Enter accepts the OS login name as default)
#   4. OS login name          (non-TTY fallback via getpass.getuser())
#   5. "You"                  (last resort — getpass unavailable)

_MAX_PRIMARY_USER_LEN = 32  # prompt-width sanity: the name renders on every line


def _sanitize_primary_user(name: str) -> str:
    """Make a Primary User name safe to render inside an input() prompt.

    The name is interpolated into the REPL prompt string on EVERY turn, so
    it must not carry newlines, ANSI escapes or other control characters
    (a pasted "bob\nquit" would otherwise inject phantom lines / escape
    sequences into the terminal). Keeps printable characters only, then
    caps length at 32.
    """
    cleaned = "".join(ch for ch in name.strip() if ch.isprintable())
    return cleaned[:_MAX_PRIMARY_USER_LEN]


def _default_primary_user() -> str:
    """Default Primary User: the OS login name, or "You" when unavailable."""
    try:
        name = getpass.getuser().strip()
    except Exception:
        # getpass.getuser() raises when no login name is determinable
        # (e.g. stripped containers with no passwd entry) — last resort.
        return "You"
    return _sanitize_primary_user(name) or "You"


def _resolve_primary_user(args: argparse.Namespace) -> str:
    """Resolve the Primary User for this chat session (see order above).

    The interactive prompt (step 3) only fires when stdin is a TTY — piped
    input (tests, ACP, scripting) never blocks on it.
    """
    # 1. --user flag
    flag = getattr(args, "user", None)
    if flag and flag.strip():
        return _sanitize_primary_user(flag)
    # 2. AGENTKTHX_USER env var
    env = os.environ.get("AGENTKTHX_USER", "").strip()
    if env:
        return _sanitize_primary_user(env)
    # 3. Interactive prompt — TTY only
    default = _default_primary_user()
    if not sys.stdin.isatty():
        return default
    try:
        raw = input(f"Primary User [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        # Ctrl+D / Ctrl+C at the naming prompt — fall through to default
        # instead of killing the whole session.
        print()
        return default
    return _sanitize_primary_user(raw) if raw else default


# ============================================================================
# Interactive model switcher (R07.19 follow-up #7)
# ============================================================================


def _model_menu_labels(
    models: list[dict], current_model: str | None
) -> tuple[list[str], int | None]:
    """Build ArrowMenu labels + the current-model index from list_models() rows.

    Labels carry the context hint where the backend provides one (128K
    style, same formatting the /models list renders); they stay plain
    text — the menu itself colors the cursor and the current-model ✓.
    """
    labels: list[str] = []
    current_index: int | None = None
    for i, m in enumerate(models):
        name = m.get("name", "unknown")
        details = m.get("details", {}) or {}
        ctx = details.get("context_length", 0) or details.get("n_ctx", 0) or 0
        if ctx >= 1000:
            labels.append(f"{name}  ({ctx // 1024}K)")
        elif ctx:
            labels.append(f"{name}  ({ctx})")
        else:
            labels.append(name)
        if current_model is not None and name == current_model:
            current_index = i
    return labels, current_index


def _report_model_switch(changes: dict, new_model: str) -> None:
    """Print the outcome of a model switch (shared by /model and the picker)."""
    old_model, _ = changes.get("model", (new_model, new_model))
    print(green(f"Model changed: {old_model} -> {new_model}"))

    def _fmt_pred(v):
        return "(model default)" if v is None else str(v)

    if "num_ctx" in changes:
        old_ctx, new_ctx = changes["num_ctx"]
        print(dim(f"  num_ctx: {old_ctx} -> {new_ctx}"))
    if "num_predict" in changes:
        old_pred, new_pred = changes["num_predict"]
        print(dim(f"  num_predict: {_fmt_pred(old_pred)} -> {_fmt_pred(new_pred)}"))


def _interactive_model_switch(agent, models: list[dict], title: str) -> None:
    """Run the arrow-key picker over `models` and apply the choice to `agent`.

    The switch re-derives num_ctx/num_predict/family config through the
    SAME `apply_model_switch` path `/model <name>` uses (ROB-14), so
    picking from the menu is byte-equivalent to typing the name.
    """
    # R07.00 convention: resolve collaborators through the cli facade so
    # monkeypatch.setattr(agentkthx.cli, 'apply_model_switch', ...) works.
    from agentkthx import cli as _cli

    from ..picker import ArrowMenu

    current_model = agent.model
    labels, current_index = _model_menu_labels(models, current_model)
    menu = ArrowMenu(labels, title=title, current_index=current_index)
    idx = menu.run()
    if idx is None:
        print(dim("Cancelled — model unchanged."))
        return
    chosen = models[idx].get("name", "unknown")
    if chosen == current_model:
        print(f"Already on {cyan(chosen)} — model unchanged.")
        return
    changes = _cli.apply_model_switch(agent, chosen)
    _report_model_switch(changes, chosen)


def _startup_model_pick(args: argparse.Namespace, config) -> str | None:
    """Interactive model selection at chat startup (no ``-m/--model`` given).

    Lists models from the resolved backend (cloud → OPENAI mode probe,
    local → OPENRE — the same probe layout ``agentkthx models`` uses)
    and runs the arrow-key picker. Returns the chosen model name, or
    ``None`` to fall back to the classic resolution inside
    ``_build_agent`` (bitnet discovery, then ``config.default_model``):
    listing failures, empty backends and cancellations all degrade to
    ``None`` instead of blocking the session.

    R07.23: now applies the same FREE_ONLY filter that ``cmd_models``
    uses (via ``apply_free_only_filter``) so the picker doesn't show paid
    models when ``OPENROUTER_FREE_ONLY=1`` or ``ZAI_FREE_ONLY=1`` is set.
    """
    from ...backends import get_backend
    from ...core.types import ApiMode
    from .models import apply_free_only_filter

    backend_name = getattr(args, "backend", None) or config.backend
    try:
        # Probe backend type first (some backends raise on OPENRE, so the
        # probe passes OPENAI — mirrors cmd_models).
        probe = get_backend(backend_name, api_mode=ApiMode.OPENAI)
        api_mode = ApiMode.OPENAI if getattr(probe, "is_cloud", False) else ApiMode.OPENRE
        backend = get_backend(backend_name, api_mode=api_mode)
        models = backend.list_models()
    except Exception as e:
        print(yellow(f"Could not list models ({e}) — using the default model."))
        return None

    if not models:
        print(yellow("No models found on this backend — using the default model."))
        return None

    # R07.23: apply FREE_ONLY filter so the picker only shows free models
    # when the operator has set OPENROUTER_FREE_ONLY / ZAI_FREE_ONLY.
    models = apply_free_only_filter(backend_name, backend, models)
    if not models:
        print(
            yellow(
                f"No free models found on {backend_name} "
                f"(FREE_ONLY is set) — using the default model."
            )
        )
        return None

    from ..picker import ArrowMenu

    labels, current_index = _model_menu_labels(models, config.default_model)
    menu = ArrowMenu(labels, title=f"Select a model ({backend_name})", current_index=current_index)
    idx = menu.run()
    if idx is None:
        print(dim("Model picker cancelled — using the default model."))
        return None
    return models[idx].get("name") or None


def cmd_chat(args: argparse.Namespace) -> int:
    """Execute the chat command."""

    # R07.00: resolve shared collaborators through the cli facade so that
    # monkeypatch.setattr(agentkthx.cli, '<name>', ...) keeps working.
    from agentkthx import cli as _cli

    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _cli._init_acp(args, config, "AgentKthx-Chat")
    if should_stop:
        return 1

    # R07.19 (follow-up #7): -m/--model is now OPTIONAL on chat. When it is
    # omitted, no AGENTKTHX_MODEL override is set, no ACP session is driving
    # us and stdin is a real terminal, the arrow-key model picker (the same
    # component /models uses) runs BEFORE the agent is built so the session
    # starts on the model you picked. Every other case keeps the classic
    # resolution inside _build_agent: bitnet server discovery, then
    # config.default_model (which itself honors AGENTKTHX_MODEL).
    if (
        getattr(args, "model", None) is None
        and acp is None
        and sys.stdin.isatty()
        and not os.environ.get("AGENTKTHX_MODEL")
    ):
        picked = _startup_model_pick(args, config)
        if picked:
            args.model = picked

    agent = _cli._build_agent(args, config)

    _cli._print_session_header(agent, args, config, "Chat Mode")

    # R07.19: Primary User — who is chatting? Resolved before the persistent
    # footer takes over the bottom of the terminal so the naming prompt
    # renders on a clean line. The name replaces "You:" on every REPL turn.
    primary_user = _resolve_primary_user(args)
    print(
        f"Chatting with {bright_cyan(primary_user)} — type '/quit' to exit, '/help' for commands\n"
    )

    # Update notice under the banner — printed BEFORE the persistent footer
    # takes over the bottom of the terminal (see _update_footer scroll regions).
    _cli._print_update_notice()

    _session_tokens_in = 0
    _session_tokens_out = 0

    # R07.12 dedup (CodeFlow duplicate-block report): both footer lines are
    # rendered by the shared agentkthx.cli.footer builder — the logic had been
    # copy-forked between cmd_chat and cmd_agent (incl. 4 identical nested
    # _fmt_tok definitions) since R06.58. Thin closures keep every call site
    # unchanged and read the live session counters (accumulated below and in
    # the turn loop) at call time.
    def _footer_line1() -> str:
        """Build the first footer line: version, model, prompt, context, tokens."""
        return footer_line1(agent)

    def _footer_line2() -> str:
        """Build the second footer line: backend, token usage, context %, debug flag."""
        return footer_line2(agent, _session_tokens_in, _session_tokens_out)

    def _footer_text() -> str:
        """Build the full 2-line footer (backward-compat wrapper).

        Returns both footer lines joined with a newline. Used by
        legacy code and tests that expect a single _footer_text() call.
        The actual rendering uses _footer_line1() + _footer_line2()
        separately for the 2-line scroll-region footer.
        """
        return f"{_footer_line1()}\n{_footer_line2()}"

    # ── Footer bar — persistent scroll-region approach (R05.4) ──────────
    # R05.1 drew the footer below `You:` using ANSI cursor-up, but never
    # erased the previous turn's footer → each turn stacked another footer
    # line in the scrollback.
    # R05.2 removed the footer entirely.
    # R05.4 first attempt: print footer once per turn after the response.
    #   → User complained that old footer text scrolled by in the chat log.
    #
    # R05.4 final fix: use a terminal SCROLL REGION (DECSTBM) to reserve
    # the bottom TWO lines for the footer. The conversation scrolls within
    # the region above; the footer stays fixed at the bottom and updates
    # in place via save/restore cursor. No footer text EVER enters the
    # scrollback history — exactly one footer (2 lines) visible at all times.

    _FOOTER_LINES = 2  # number of reserved footer lines at terminal bottom

    _is_tty = sys.stdout.isatty()
    _term_size = shutil.get_terminal_size() if _is_tty else None
    # Need at least 6 lines for a usable chat + 2-line footer area
    _use_persistent_footer = bool(_is_tty and _term_size and _term_size.lines >= 6)

    def _setup_footer_region():
        """Reserve the terminal's bottom 2 lines for the footer via DECSTBM."""
        if not _use_persistent_footer:
            return
        # Set scroll region: lines 1 through (height - 2).
        # The bottom 2 lines are excluded from scrolling and reserved for
        # the footer.
        bottom = _term_size.lines - _FOOTER_LINES  # last line of scroll region
        sys.stdout.write(f"\033[1;{bottom}r")
        # Move cursor to the BOTTOM of the scroll region (just above the
        # footer) so the first `You:` prompt appears there, not at the top.
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.flush()

    def _teardown_footer_region():
        """Reset terminal: restore full-screen scroll region, clear footer."""
        if not _use_persistent_footer:
            return
        # Reset scroll region to full terminal
        sys.stdout.write("\033[r")
        # Clear the footer lines (bottom 2 lines)
        if _term_size:
            for i in range(_FOOTER_LINES):
                row = _term_size.lines - i
                sys.stdout.write(f"\033[{row};1H\033[2K")
            # Move cursor to the line just above where the footer was
            sys.stdout.write(f"\033[{_term_size.lines - _FOOTER_LINES};1H")
        sys.stdout.flush()

    def _update_footer():
        """Redraw the 2-line footer in place on the reserved bottom lines."""
        nonlocal _term_size
        if not _use_persistent_footer:
            return
        # Re-query terminal size to handle resize
        new_size = shutil.get_terminal_size()
        if new_size.lines != _term_size.lines or new_size.columns != _term_size.columns:
            _term_size = new_size
            # Re-establish scroll region with new dimensions
            bottom = _term_size.lines - _FOOTER_LINES
            sys.stdout.write(f"\033[1;{bottom}r")
            sys.stdout.flush()

        line1 = _footer_line1()
        line2 = _footer_line2()
        # Save cursor, move to footer area, clear + write both lines, restore.
        # try/finally guarantees auto-wrap is re-enabled even if line1/line2
        # raise — otherwise an exception here would leave the terminal in
        # no-wrap mode and the next `You:` prompt would overwrite its line
        # instead of scrolling the region up on wrap.
        sys.stdout.write("\033[s")  # save cursor
        sys.stdout.write("\033[?7l")  # disable line wrap
        try:
            # Line 1: second-to-last terminal line
            row1 = _term_size.lines - 1
            sys.stdout.write(f"\033[{row1};1H")  # move to line 1
            sys.stdout.write("\033[2K")  # clear entire line
            sys.stdout.write(line1)  # write footer line 1
            # Line 2: last terminal line
            row2 = _term_size.lines
            sys.stdout.write(f"\033[{row2};1H")  # move to line 2
            sys.stdout.write("\033[2K")  # clear entire line
            sys.stdout.write(line2)  # write footer line 2
        finally:
            sys.stdout.write("\033[?7h")  # re-enable line wrap
        sys.stdout.write("\033[u")  # restore cursor
        sys.stdout.flush()

    def _position_for_input():
        """Move cursor to the bottom of the scroll region for the `You:` prompt.

        This ensures the input prompt always appears one line above the
        footer, regardless of where the previous response left the cursor.

        Also explicitly re-enables terminal auto-wrap (DECAWM, ``\033[?7h``)
        before each prompt. ``_update_footer()`` toggles it OFF/ON around the
        footer redraw; if anything between then and ``input()`` leaves the
        terminal with auto-wrap OFF, long input at the ``You:`` prompt
        overwrites the last column instead of wrapping to a new line.
        Forcing it ON here guarantees the scroll region scrolls up by one
        line when the user's input reaches the right edge — the intended
        behavior.
        """
        if not _use_persistent_footer:
            return
        # Move to last line of scroll region (just above footer)
        bottom = _term_size.lines - _FOOTER_LINES
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.write("\033[2K")  # clear the line (remove stale text)
        sys.stdout.write("\033[?7h")  # ensure auto-wrap is ON for input
        sys.stdout.flush()

    # ── Spinner ───────────────────────────────────────────────────────
    _SPINNER_FRAMES = [
        "\u2807",
        "\u2839",
        "\u2838",
        "\u283c",
        "\u2834",
        "\u2826",
        "\u2836",
        "\u282d",
        "\u282f",
        "\u280f",
    ]
    _spinner_active = False
    _spinner_stop = threading.Event()

    def _spinner_thread():
        """Animate a braille spinner on stderr."""
        idx = 0
        while not _spinner_stop.is_set():
            frame = _SPINNER_FRAMES[idx % len(_SPINNER_FRAMES)]
            sys.stderr.write(f"\r  {cyan(frame)} {dim('thinking...')}")
            sys.stderr.flush()
            idx += 1
            _spinner_stop.wait(0.08)
        # Clear the spinner line
        sys.stderr.write("\r" + " " * 30 + "\r")
        sys.stderr.flush()

    def _spinner_start():
        nonlocal _spinner_active
        _spinner_active = True
        _spinner_stop.clear()
        t = threading.Thread(target=_spinner_thread, daemon=True)
        t.start()
        return t

    def _spinner_stop_thread(t):
        nonlocal _spinner_active
        _spinner_active = False
        _spinner_stop.set()
        t.join(timeout=1)

    # ── Main loop ─────────────────────────────────────────────────────
    # Setup terminal scroll region for persistent footer BEFORE the loop.
    # The try/finally ensures _teardown_footer_region() runs on EVERY exit
    # path (quit, EOF, Ctrl+C, unexpected exception) so the terminal is
    # never left in a broken scroll-region state.
    _setup_footer_region()
    # Register footer-refresh callback so the persistent footer updates
    # token counts and context % during streaming (not just after the run).
    agent._on_step_callback = lambda step, tin, tout: _update_footer()
    # In-memory last-message recall (R06.4): no history file.
    # Previously used readline.read_history_file(~/.agentkthx_history) +
    # write_history_file() on every prompt, which grew unboundedly
    # (one user hit 600MB). Now we just track the last user_input in a
    # variable so UP arrow can recall it within the current session.
    # readline is still imported for arrow-key / line-editing support
    # in input(), but no file I/O happens.
    _last_user_input = ""
    try:
        while True:
            # Refresh the persistent footer at the top of each iteration.
            # This updates token counts, handles terminal resize, and ensures
            # the footer is visible before the user types.
            _update_footer()
            # Position cursor at the bottom of the scroll region (one line
            # above the footer) so the `You:` prompt appears there — not at
            # the top of the screen or wherever the last response left it.
            _position_for_input()
            try:
                # Import readline for arrow-key / line-editing support in input().
                # We do NOT read or write a history file — that caused unbounded
                # growth (600MB+ reported). In-memory recall only.
                #
                # NOTE: stdlib `readline` is Unix-only (wraps GNU readline /
                # libedit). On Windows it doesn't exist — Python's built-in
                # input() falls back to plain text entry, which still works
                # (just no arrow-key history / line editing). If the user has
                # installed `pyreadline3` (a third-party drop-in), it registers
                # itself as `readline` on Windows and `import readline` will
                # succeed transparently.
                try:
                    import readline
                except ImportError:
                    readline = None  # Windows without pyreadline3

                if readline is not None:
                    # Force horizontal-scroll-mode OFF so long input wraps to a new
                    # visual line instead of scrolling horizontally within one line.
                    # Default is OFF, but an ~/.inputrc could enable it. Without this,
                    # input at the `You:` prompt would overwrite the rightmost column
                    # instead of scrolling the scroll region up for a new input line.
                    readline.parse_and_bind("set horizontal-scroll-mode off")

                # Prompt uses \001 ... \002 (readline's RL_PROMPT_START_IGNORE /
                # RL_PROMPT_END_IGNORE) around ANSI escape codes so readline
                # counts them as zero-width. Without these markers, readline
                # treats `\033[33m` + `{NAME}:` + `\033[0m` + ` ` as 14 visible
                # chars, miscounting the prompt width and breaking wrap detection.
                # On Windows without readline, these markers are passed through
                # to the terminal as raw bytes — Windows Terminal / modern
                # consoles handle ANSI escapes natively, but the SOH/STX (0x01/
                # 0x02) control chars can render as little boxes. Use a clean
                # ANSI-only prompt when readline isn't available (R07.19: the
                # sequence is a proper CSI form — bare-ESC prompts would be
                # consumed as 2-byte VT escapes, see ROB-34).
                if readline is not None:
                    _prompt = f"\001\033[33m\002{primary_user}:\001\033[0m\002 "
                else:
                    _prompt = f"\033[33m{primary_user}:\033[0m "
                user_input = input(_prompt).strip()

                # Track last user_input for in-session recall (replaces file-based history)
                if user_input:
                    _last_user_input = user_input
            except (EOFError, KeyboardInterrupt):
                # Ensure persistent memory is flushed and closed
                if getattr(agent, "_is_persistent", False) and hasattr(agent.memory, "close"):
                    agent.memory.close()
                print("\n👋 Goodbye!")
                break

            if not user_input:
                continue

            if user_input == "/quit":
                if acp:
                    acp.log_chat("user", "/quit")
                    acp.a2a_unregister()
                # Ensure persistent memory is flushed and closed
                if getattr(agent, "_is_persistent", False) and hasattr(agent.memory, "close"):
                    agent.memory.close()
                print(bright_cyan("👋 Goodbye!"))
                break

            if user_input == "/auth":
                # R07.20: interactive auth picker — arrow-key menu over every
                # cloud backend's API_KEY and FREE_ONLY env vars. Enter on a
                # flag flips it; Enter on a key prompts for a value (hidden
                # input on a TTY). Changes persist to the env file and rebind
                # os.environ + the config module + every imported plugin
                # global, and patch the live session backend when it matches.
                from ..auth import run_auth_picker

                run_auth_picker(agent)
                continue

            if user_input == "/help":
                print(
                    f"  {cyan('/auth')}       Set API keys / toggle FREE_ONLY flags (interactive picker)"
                )
                print(f"  {cyan('/clear')}      Clear conversation memory")
                print(f"  {cyan('/debug')}      Toggle debug output on/off")
                print(f"  {cyan('/help')}       Show this help message")
                print(
                    f"  {cyan('/model')}      Show or change the model (e.g. /model gemini-3.8-flash)"
                )
                print(
                    f"  {cyan('/models')}     Interactive model switcher (↑/↓ + Enter; plain list when piped)"
                )
                print(
                    f"  {cyan('/param')}      Show or set generation parameters (temp, top_p, top_k, etc.)"
                )
                print(f"  {cyan('/security')}   Show or set security mode (max|off)")
                print(f"  {cyan('/skills')}     Show available skills (✓ = loaded)")
                print(
                    f"  {cyan('/sh')}         Run a local shell command (display + add to context; -n to display only)"
                )
                print(
                    f"  {cyan('/skill')}      Load a skill mid-session (e.g. /skill codebase-audit, crypto-signals)"
                )
                print(f"  {cyan('/souls')}      Show available souls (\u2713 = active)")
                print(
                    f"  {cyan('/soul')}       Show or switch the active soul (e.g. /soul kthx-trading)"
                )
                print(
                    f"  {cyan('/status')}     Show model, backend, tools, skills, and memory info"
                )
                print(f"  {cyan('/system')}     Print the current system prompt")
                print(f"  {cyan('/tools')}      Show available tools (✓ = loaded)")
                print(
                    f"  {cyan('/tool')}       Load a tool mid-session (e.g. /tool shell,read_file,write_file)"
                )
                print(f"  {cyan('/quit')}       Exit AgentKthx")
                continue

            if user_input == "/security" or user_input.startswith("/security "):
                from ...core.helpers import get_security_mode, set_security_mode

                parts = user_input.split(None, 1)
                if len(parts) < 2:
                    # No argument — show current mode
                    current = get_security_mode()
                    label = green("max (strict)") if current == "max" else red("off (unrestricted)")
                    print(f"Security mode: {label}")
                    print(dim("  Usage: /security max   — all checks enabled (default)"))
                    print(dim("         /security off  — disable all checks (use with caution)"))
                else:
                    mode = parts[1].strip().lower()
                    if mode in ("max", "off"):
                        set_security_mode(mode)
                        if mode == "max":
                            print(green("Security mode: max (all checks enabled)"))
                        else:
                            print(red("Security mode: off (ALL CHECKS DISABLED)"))
                            print(
                                yellow("  The model can now run any command, read/write any path,")
                            )
                            print(yellow("  and fetch any URL. Use with caution."))
                    else:
                        print(yellow(f"Invalid security mode: {mode!r}. Use 'max' or 'off'."))
                continue

            if user_input == "/system":
                prompt = getattr(agent, "_custom_system_prompt", "")
                if prompt:
                    print(prompt)
                else:
                    print(yellow("No system prompt set."))
                continue

            if user_input == "/tools":
                # R06.56: Show ALL available tools (not just loaded ones),
                # with a ✓ marker for loaded tools and ○ for available-but-not-loaded.
                all_tools = make_builtin_registry()
                loaded_names = set(agent.tools.names()) if agent.tools else set()
                all_tool_list = all_tools.all()
                if not all_tool_list:
                    print(yellow("No tools available in the builtin registry."))
                else:
                    loaded_count = 0
                    for t in all_tool_list:
                        is_loaded = t.name in loaded_names
                        marker = green("✓") if is_loaded else dim("○")
                        desc = t.description.split(".")[0] if t.description else "No description"
                        if len(desc) > 60:
                            desc = desc[:57] + "..."
                        params = ", ".join(p.name for p in t.params) if t.params else ""
                        param_str = dim(f"  ({params})") if params else ""
                        print(f"  {marker} {cyan(t.name):<28}{desc}{param_str}")
                        if is_loaded:
                            loaded_count += 1
                    print()
                    print(
                        dim(
                            f"  {loaded_count}/{len(all_tool_list)} tools loaded. "
                            f"Use {cyan('/tool <name,name,...>')} to load more."
                        )
                    )
                continue

            # ── /tool slash command ────────────────────────────────────────────
            # Load tools mid-session. Supports comma-separated list like --tools.
            # Usage:
            #   /tool                       — show usage
            #   /tool shell                 — load one tool
            #   /tool shell,read_file,calc  — load multiple tools
            if user_input == "/tool" or user_input.startswith("/tool "):
                parts = user_input.split(None, 1)
                if len(parts) < 2 or not parts[1].strip():
                    # No args — show usage + currently loaded tools
                    loaded_names = set(agent.tools.names()) if agent.tools else set()
                    print(dim("  Usage: /tool <name,name,...>  (comma-separated, like --tools)"))
                    print(dim("  Example: /tool shell,read_file,write_file"))
                    print()
                    if loaded_names:
                        print(f"  Currently loaded: {cyan(', '.join(sorted(loaded_names)))}")
                    else:
                        print(yellow("  No tools currently loaded."))
                    continue
                # Parse comma-separated list (same logic as --tools at line 551)
                requested = [t.strip() for t in parts[1].split(",") if t.strip()]
                all_tools = make_builtin_registry()
                loaded_names = set(agent.tools.names()) if agent.tools else set()
                newly_loaded = []
                already_loaded = []
                not_found = []
                for name in requested:
                    tool = all_tools.get(name)
                    if tool is None:
                        # Try fuzzy match for a helpful suggestion
                        fuzzy = all_tools.get_fuzzy(name, threshold=0.6)
                        if fuzzy and fuzzy.name != name:
                            not_found.append(f"{name} (did you mean '{fuzzy.name}'?)")
                        else:
                            not_found.append(name)
                    elif name in loaded_names:
                        already_loaded.append(name)
                    else:
                        agent.tools.register_tool(tool)
                        newly_loaded.append(name)
                        loaded_names.add(name)
                # Report
                if newly_loaded:
                    print(
                        green(f"  ✓ Loaded {len(newly_loaded)} tool(s): ")
                        + cyan(", ".join(newly_loaded))
                    )
                if already_loaded:
                    print(
                        yellow(f"  ⚠ Already loaded ({len(already_loaded)}): ")
                        + dim(", ".join(already_loaded))
                    )
                if not_found:
                    print(red(f"  ✗ Not found ({len(not_found)}): ") + dim(", ".join(not_found)))
                    # Show available tools that weren't requested
                    available = [n for n in all_tools.names() if n not in loaded_names]
                    if available:
                        print(dim(f"  Available: {', '.join(sorted(available))}"))
                if not newly_loaded and not already_loaded and not not_found:
                    print(yellow("  No tools specified."))
                continue

            if user_input == "/skills":
                # R06.56: Show ALL available skills (not just loaded ones),
                # with a ✓ marker for loaded skills and ○ for available-but-not-loaded.
                loaded = getattr(agent, "_loaded_skills", [])
                try:
                    from ...skills import SkillLoader

                    loader = SkillLoader()
                    available = loader.list_skills()
                except Exception as e:
                    print(yellow(f"Skills module unavailable: {e}"))
                    continue
                if not available:
                    print(yellow("No skills available."))
                    print(dim("  Skills live in agentkthx/skills/<name>/SKILL.md"))
                    continue
                print(f"{bold('Available skills:')}")
                for name in available:
                    is_loaded = name in loaded
                    marker = green("✓") if is_loaded else dim("○")
                    try:
                        skill = loader.load(name)
                        desc = (
                            skill.description[:60] + "..."
                            if len(skill.description) > 60
                            else skill.description
                        )
                    except Exception as e:
                        desc = red(f"Error: {e}")
                    print(f"  {marker} {magenta(name):<28}{desc}")
                print()
                print(
                    dim(
                        f"  {len(loaded)}/{len(available)} skills loaded. "
                        f"Use {cyan('/skill <name,name,...>')} to load more."
                    )
                )
                continue

            # ── /skill slash command ───────────────────────────────────────────
            # Load skills mid-session. Supports comma-separated list.
            # Usage:
            #   /skill                      — show usage
            #   /skill codebase-audit       — load one skill
            #   /skill codebase-audit,crypto-signals  — load multiple skills
            if user_input == "/skill" or user_input.startswith("/skill "):
                parts = user_input.split(None, 1)
                loaded = getattr(agent, "_loaded_skills", [])
                if len(parts) < 2 or not parts[1].strip():
                    # No args — show usage + currently loaded skills
                    print(dim("  Usage: /skill <name,name,...>  (comma-separated)"))
                    print(dim("  Example: /skill codebase-audit,crypto-signals"))
                    print()
                    if loaded:
                        print(f"  Currently loaded: {magenta(', '.join(loaded))}")
                    else:
                        print(yellow("  No skills currently loaded."))
                    continue
                # Parse comma-separated list
                requested = [s.strip() for s in parts[1].split(",") if s.strip()]
                try:
                    from ...skills import SkillLoader

                    loader = SkillLoader()
                except Exception as e:
                    print(red(f"Skills module unavailable: {e}"))
                    continue
                newly_loaded = []
                already_loaded = []
                not_found = []
                available = loader.list_skills()
                for name in requested:
                    if name not in available:
                        # Suggest closest match
                        from ...core.helpers import fuzzy_match

                        fuzzy = fuzzy_match(name, available, threshold=0.6)
                        if fuzzy:
                            not_found.append(f"{name} (did you mean '{fuzzy}'?)")
                        else:
                            not_found.append(name)
                        continue
                    if name in loaded:
                        already_loaded.append(name)
                        continue
                    try:
                        skill = loader.load(name)
                        # Append the skill's instructions to the system prompt
                        # so the agent has access to them on the next message.
                        # The skill instructions are added to _custom_system_prompt
                        # and the memory's system message is updated via memory.add()
                        # which handles replacing any existing system message.
                        skill_text = skill.instructions.strip()
                        if skill_text:
                            old_prompt = getattr(agent, "_custom_system_prompt", "") or ""
                            skill_block = f"\n\n# Skill: {skill.name}\n{skill_text}"
                            agent._custom_system_prompt = old_prompt + skill_block
                            # R07.19: track the accumulated skills text so the
                            # /soul switch can rebuild the prompt (new soul +
                            # every loaded skill + environment) without losing
                            # mid-session skill loads.
                            agent._skills_prompt = (
                                getattr(agent, "_skills_prompt", None) or ""
                            ) + skill_block
                            # memory.add("system", ...) automatically removes any
                            # existing system messages and appends the new one —
                            # no need to manually find/replace in _messages.
                            agent.memory.add("system", agent._custom_system_prompt)
                        loaded.append(name)
                        newly_loaded.append(name)
                    except Exception as e:
                        not_found.append(f"{name} (load error: {e})")
                # Update the agent's loaded-skills list
                agent._loaded_skills = loaded
                # Report
                if newly_loaded:
                    print(
                        green(f"  ✓ Loaded {len(newly_loaded)} skill(s): ")
                        + magenta(", ".join(newly_loaded))
                    )
                if already_loaded:
                    print(
                        yellow(f"  ⚠ Already loaded ({len(already_loaded)}): ")
                        + dim(", ".join(already_loaded))
                    )
                if not_found:
                    print(red(f"  ✗ Not found ({len(not_found)}): ") + dim(", ".join(not_found)))
                    if available:
                        print(dim(f"  Available: {', '.join(available)}"))
                if not newly_loaded and not already_loaded and not not_found:
                    print(yellow("  No skills specified."))
                continue

            # ── /souls slash command ──────────────────────────────────────
            # List every bundled Soul Spec package. ✓ = the agent's active
            # soul. R07.19 companion to /skills — souls are the heavier
            # persona packages (selected with --soul at startup); /soul
            # shows/switches them mid-session.
            if user_input == "/souls":
                try:
                    from ...soul import SoulLoader

                    available = SoulLoader().list_souls()
                except Exception as e:
                    print(yellow(f"Soul module unavailable: {e}"))
                    continue
                if not available:
                    print(yellow("No souls available."))
                    print(dim("  Souls live in agentkthx/souls/<name>/soul.json"))
                    continue
                current_name = getattr(agent.soul, "name", None)
                print(f"{bold('Available souls:')}")
                for m in available:
                    marker = green("\u2713") if m.name == current_name else dim("\u25cb")
                    desc = m.description or ""
                    if len(desc) > 60:
                        desc = desc[:57] + "..."
                    print(
                        f"  {marker} {cyan(m.name.ljust(20))}{dim(('v' + m.version).ljust(10))}{desc}"
                    )
                print()
                if current_name:
                    print(dim(f"  Active: {current_name}. Switch with {cyan('/soul <name>')}."))
                else:
                    print(
                        dim(
                            f"  No soul active (default prompt). Load one with {cyan('/soul <name>')}."
                        )
                    )
                continue

            # ── /soul slash command ───────────────────────────────────────
            # Show or switch the active Soul Spec package.
            # Usage:
            #   /soul                — show the active soul
            #   /soul kthx-trading   — switch mid-session (prompt rebuilt,
            #                          conversation history preserved)
            if user_input == "/soul" or user_input.startswith("/soul "):
                parts = user_input.split(None, 1)
                if len(parts) < 2 or not parts[1].strip():
                    # No args — show the active soul
                    if agent.soul:
                        s = agent.soul
                        print(
                            f"Current soul: {magenta(s.display_name)} ({cyan(s.name)}) v{s.version}"
                        )
                        print(
                            dim(
                                f"  Level: {getattr(agent, '_soul_level', 2)} (1=quick, 2=full, 3=deep)"
                            )
                        )
                        if s.description:
                            desc = s.description
                            if len(desc) > 70:
                                desc = desc[:67] + "..."
                            print(f"  {dim(desc)}")
                        if s.allowed_tools:
                            print(dim(f"  Allowed tools: {', '.join(s.allowed_tools)}"))
                        print(dim(f"  Switch with: {cyan('/soul <name>')} (see /souls)"))
                    else:
                        print(yellow("No soul active — the default system prompt is in use."))
                        print(
                            dim(
                                f"  Load one now with {cyan('/soul <name>')} (see /souls), "
                                "or restart with --soul <name>."
                            )
                        )
                    continue
                name = parts[1].strip()
                # Validate against the bundled souls (fuzzy suggestion like /skill)
                try:
                    from ...soul import SoulLoader

                    available = [m.name for m in SoulLoader().list_souls()]
                except Exception:
                    available = []
                if name not in available:
                    suggestion = ""
                    if available:
                        from ...core.helpers import fuzzy_match

                        fuzzy = fuzzy_match(name, available, threshold=0.6)
                        if fuzzy:
                            suggestion = f" (did you mean '{fuzzy}'?)"
                    print(red(f"Soul not found: {name}{suggestion}"))
                    if available:
                        print(dim(f"  Available: {', '.join(available)}"))
                    continue
                old_name = agent.soul.name if agent.soul else None
                old_tools = set(agent.tools.names())
                try:
                    new_soul = agent.switch_soul(name)
                except Exception as e:
                    print(red(f"Failed to switch soul: {e}"))
                    continue
                new_tools = set(agent.tools.names())
                removed = old_tools - new_tools
                was = old_name or "(default prompt)"
                print(green(f"Soul switched: {was} -> {new_soul.name}"))
                print(
                    dim(
                        f"  {new_soul.display_name} v{new_soul.version} — system prompt rebuilt, "
                        "conversation preserved"
                    )
                )
                if removed:
                    print(
                        yellow(f"  Tools filtered out by this soul: {', '.join(sorted(removed))}")
                    )
                    print(
                        dim(
                            "  They stay filtered while this soul is active — reload with /tool "
                            "after switching back."
                        )
                    )
                if not new_tools:
                    print(
                        dim(
                            "  Tip: no tools loaded — use /tool <name,name,...> to load some "
                            "(e.g. /tool calculator,shell)."
                        )
                    )
                continue

            # ── /param slash command ────────────────────────────────────────
            # Show or set model generation parameters. Per-backend support
            # matrix — only params the current backend actually forwards to
            # the API are settable. Other params show as "not supported".
            #
            # Usage:
            #   /param                        — show all current values
            #   /param <name>                 — show value of one param
            #   /param <name> <value>         — set value
            #   /param reset <name>           — reset to None (use model default)
            if user_input == "/param" or user_input.startswith("/param "):

                # Get current backend type
                backend_type = getattr(agent.backend, "backend_type", None)
                backend_name = (
                    backend_type.value if hasattr(backend_type, "value") else str(backend_type)
                )

                # Per-backend supported parameter matrix.
                # Format: param_name → (type, description, supported_backends)
                # supported_backends: set of backend name strings (matching BackendType.value)
                # Special marker "all" means supported everywhere.
                PARAM_MATRIX = {
                    # ── Generation control ──────────────────────────────────
                    "temperature": {
                        "type": "float",
                        "range": "0.0-2.0",
                        "description": "Sampling temperature. Lower = focused, higher = creative",
                        "backends": {"all"},
                        "agent_attr": "_temperature",
                    },
                    "top_p": {
                        "type": "float",
                        "range": "0.0-1.0",
                        "description": "Nucleus sampling probability mass",
                        "backends": {"all"},
                        "agent_attr": "_top_p",
                    },
                    "max_tokens": {
                        "type": "int",
                        "range": "1-N",
                        "description": "Maximum tokens to generate (also: num_predict)",
                        "backends": {"all"},
                        "agent_attr": "_num_predict",
                        "aliases": ["num_predict", "max_predict"],
                    },
                    "max_steps": {
                        "type": "int",
                        "range": "1-1000",
                        "description": "Maximum agent reasoning steps",
                        "backends": {"all"},
                        "agent_attr": "max_steps",
                    },
                    "num_ctx": {
                        "type": "int",
                        "range": "2048-N",
                        "description": "Context window size in tokens",
                        "backends": {"all"},
                        "agent_attr": "num_ctx",
                    },
                    # ── R07.17: per-request prompt-processing batch size ──
                    "num_batch": {
                        "type": "int",
                        "range": "1-N",
                        "description": (
                            "Prompt-processing batch size. Ollama per-request option "
                            "(options.num_batch); lower = less peak RAM during prompt eval. "
                            "llama-server/TurboQuant set this at server start "
                            "('turbo start --batch-size N'); cloud backends ignore it."
                        ),
                        # Only Ollama native /api/chat forwards it (via the
                        # generic kwargs-to-options loop). The OpenAI-compat
                        # path (/v1/chat/completions) does NOT support per-request
                        # num_batch — switch to --api openre to use it.
                        "backends": {"ollama"},
                        "agent_attr": "_num_batch",
                    },
                    # ── R07.18: llama.cpp repetition sampling ──────────────
                    "repeat_penalty": {
                        "type": "float",
                        "range": "0.0-2.0 (1.0=off)",
                        "description": (
                            "Repetition penalty (llama.cpp native, >1.0 discourages "
                            "repetition). BitNet default is 1.3 for small models prone "
                            "to looping. Forwarded to Ollama (options.*) + llama-server "
                            "(top-level on /completion). Cloud backends drop it."
                        ),
                        "backends": {"ollama", "llama_server", "bitnet"},
                        "agent_attr": "_repeat_penalty",
                    },
                    "repeat_last_n": {
                        "type": "int",
                        "range": "-1 to N (0=full ctx, -1=model default)",
                        "description": (
                            "Tokens to consider for repetition penalty (llama.cpp native). "
                            "0 = full context, -1 = model default (typically 64). "
                            "Ollama + llama-server/TurboQuant/BitNet only."
                        ),
                        "backends": {"ollama", "llama_server", "bitnet"},
                        "agent_attr": "_repeat_last_n",
                    },
                    # ── OpenAI / OpenRouter-specific ────────────────────────
                    "top_k": {
                        "type": "int",
                        "range": "0-N (0=disabled)",
                        "description": "Top-K sampling: consider only K most likely tokens",
                        "backends": {"openrouter", "ollama", "llama_server", "bitnet"},  # not ZAI
                        "agent_attr": None,  # passed through kwargs at generate time
                    },
                    "seed": {
                        "type": "int",
                        "range": "any integer",
                        "description": "Reproducibility seed (best-effort, provider-dependent)",
                        "backends": {"openrouter", "ollama", "llama_server", "bitnet"},
                        "agent_attr": None,
                    },
                    "n": {
                        "type": "int",
                        "range": "1-10",
                        "description": "Number of completions to generate",
                        "backends": {"openrouter", "ollama"},
                        "agent_attr": None,
                    },
                    "presence_penalty": {
                        "type": "float",
                        "range": "-2.0 to 2.0",
                        "description": "Penalize tokens already present (encourages new topics)",
                        "backends": {"openrouter", "zai", "ollama"},
                        "agent_attr": None,
                    },
                    "frequency_penalty": {
                        "type": "float",
                        "range": "-2.0 to 2.0",
                        "description": "Penalize tokens proportional to frequency",
                        "backends": {"openrouter", "zai", "ollama"},
                        "agent_attr": None,
                    },
                    # ── Thinking controls (R05.8+) ──────────────────────────
                    "thinking": {
                        "type": "str",
                        "range": "off|auto|low|medium|high",
                        "description": "Thinking / reasoning effort level",
                        "backends": {"all"},
                        "agent_attr": "_thinking_level",
                        "aliases": ["thinking_level"],
                        # Special setter: also updates _think and _reasoning_effort
                        "special_setter": "_set_thinking_level",
                    },
                    "think": {
                        "type": "bool",
                        "range": "true|false",
                        "description": "Display reasoning_content (chain-of-thought) in CLI output",
                        "backends": {"all"},
                        "agent_attr": "_show_reasoning",
                        "aliases": ["show_reasoning"],
                    },
                    # ── AgentKthx-internal (not forwarded to API) ──────────
                    "stream": {
                        "type": "bool",
                        "range": "true|false",
                        "description": "Whether to stream responses (cloud providers default to true)",
                        "backends": {"all"},
                        "agent_attr": None,  # stashed on agent._runtime_kwargs; read by chat loop
                        # Special: /param stream true/false updates args.stream
                        "special_setter": "_set_stream",
                    },
                }

                # Stash runtime kwargs on agent for params without agent_attr
                # (top_k, seed, n, presence_penalty, frequency_penalty)
                if not hasattr(agent, "_runtime_kwargs"):
                    agent._runtime_kwargs = {}

                parts = user_input.split(None, 2)  # split into ["/param", name?, value?]
                if len(parts) == 1:
                    # /param — show all current values
                    print(f"{bold('Backend:')} {cyan(backend_name)}")
                    print(f"{bold('Parameters:')}")
                    print()
                    for name, spec in PARAM_MATRIX.items():
                        supported = "all" in spec["backends"] or backend_name in spec["backends"]
                        if not supported:
                            marker = dim("✗")
                            val_str = dim("not supported by this backend")
                        else:
                            marker = green("✓")
                            # Get current value
                            attr = spec.get("agent_attr")
                            if attr:
                                val = getattr(agent, attr, None)
                            else:
                                val = agent._runtime_kwargs.get(name)
                            if val is None:
                                val_str = dim("(model default)")
                            else:
                                val_str = yellow(str(val))
                        aliases = spec.get("aliases", [])
                        alias_str = dim(f" (aliases: {', '.join(aliases)})") if aliases else ""
                        print(f"  {marker} {magenta(name):<20} {val_str}{alias_str}")
                        print(f"    {dim(spec['description'])}")
                        if "range" in spec:
                            print(f"    {dim('Range:')} {dim(spec['range'])}")
                        if "note" in spec:
                            print(f"    {yellow('Note:')} {dim(spec['note'])}")
                    print()
                    print(dim("  Usage:"))
                    print(dim("    /param <name>              — show current value"))
                    print(dim("    /param <name> <value>      — set value"))
                    print(dim("    /param reset <name>        — reset to model default"))
                    continue

                param_name = parts[1].lower().strip()

                # Handle reset
                if param_name == "reset" and len(parts) >= 3:
                    target = parts[2].lower().strip()
                    # Find by name or alias
                    found = None
                    for n, spec in PARAM_MATRIX.items():
                        if n == target or target in spec.get("aliases", []):
                            found = (n, spec)
                            break
                    if not found:
                        print(yellow(f"Unknown parameter: {target}"))
                        continue
                    name, spec = found
                    attr = spec.get("agent_attr")
                    if attr:
                        if attr == "max_steps":
                            setattr(agent, attr, 25)  # reset to default
                        elif attr == "num_ctx":
                            setattr(agent, attr, 8192)
                        else:
                            setattr(agent, attr, None)
                        # ROB-14: reset un-pins the value — a later /model switch
                        # may re-derive it from the new model's catalog again.
                        if attr == "num_ctx":
                            agent._num_ctx_explicit = False
                        elif attr == "_num_predict":
                            agent._num_predict_explicit = False
                        elif attr == "_num_batch":
                            # R07.17: num_batch is per-request (not model-derived),
                            # so /model never re-derives it — but we still clear the
                            # pin flag for parity with num_ctx / num_predict.
                            agent._num_batch_explicit = False
                        elif attr == "_repeat_penalty":
                            # R07.18: same per-request semantics as num_batch.
                            agent._repeat_penalty_explicit = False
                        elif attr == "_repeat_last_n":
                            agent._repeat_last_n_explicit = False
                    else:
                        agent._runtime_kwargs.pop(name, None)
                    print(green(f"Reset {name} to model default."))
                    continue

                # Find parameter by name or alias
                found = None
                for n, spec in PARAM_MATRIX.items():
                    if n == param_name or param_name in spec.get("aliases", []):
                        found = (n, spec)
                        break

                if not found:
                    print(yellow(f"Unknown parameter: {param_name}"))
                    print(dim("  Available params: " + ", ".join(sorted(PARAM_MATRIX.keys()))))
                    continue

                name, spec = found

                # Check backend support
                supported = "all" in spec["backends"] or backend_name in spec["backends"]
                if not supported:
                    print(
                        yellow(f"Parameter '{name}' is not supported by backend '{backend_name}'.")
                    )
                    print(dim(f"  Supported backends: {', '.join(sorted(spec['backends']))}"))
                    continue

                # Check for read-only
                if spec.get("note") and "Read-only" in spec["note"]:
                    print(yellow(f"Parameter '{name}' is read-only."))
                    print(dim(f"  {spec['note']}"))
                    continue

                # If no value provided, show current value
                if len(parts) < 3:
                    attr = spec.get("agent_attr")
                    if attr:
                        val = getattr(agent, attr, None)
                    else:
                        val = agent._runtime_kwargs.get(name)
                    if val is None:
                        print(f"{magenta(name)}: {dim('(model default)')}")
                    else:
                        print(f"{magenta(name)}: {yellow(str(val))}")
                    print(dim(f"  {spec['description']}"))
                    print(dim(f"  Range: {spec.get('range', 'any')}"))
                    continue

                # Parse and set value
                raw_value = parts[2].strip()
                ptype = spec["type"]

                try:
                    if ptype == "float":
                        value = float(raw_value)
                        # Range check
                        if "range" in spec and "-" in spec["range"]:
                            parts_range = spec["range"].split("-")
                            if len(parts_range) == 2:
                                try:
                                    lo = float(parts_range[0])
                                    hi = float(parts_range[1].split()[0])  # strip "N" etc.
                                    if value < lo or value > hi:
                                        print(yellow(f"Value {value} out of range [{lo}, {hi}]"))
                                        continue
                                except ValueError:
                                    pass  # range like "1-N" — skip validation
                    elif ptype == "int":
                        value = int(raw_value)
                    elif ptype == "bool":
                        if raw_value.lower() in ("true", "1", "yes", "on"):
                            value = True
                        elif raw_value.lower() in ("false", "0", "no", "off"):
                            value = False
                        else:
                            print(yellow(f"Invalid bool value: {raw_value!r}. Use true/false."))
                            continue
                    elif ptype == "str":
                        value = raw_value.lower()
                        # Validate against range if it's a pipe-list
                        if "range" in spec and "|" in spec["range"]:
                            valid_values = spec["range"].split("|")
                            if value not in valid_values:
                                print(
                                    yellow(
                                        f"Invalid value: {value!r}. Must be one of: {', '.join(valid_values)}"
                                    )
                                )
                                continue
                    else:
                        print(yellow(f"Unknown parameter type: {ptype}"))
                        continue
                except ValueError as e:
                    print(yellow(f"Invalid value for {name} ({ptype}): {raw_value!r} — {e}"))
                    continue

                # Handle special setters (e.g. thinking_level updates multiple attrs)
                if spec.get("special_setter") == "_set_thinking_level":
                    from agentkthx.core.types import parse_thinking_arg

                    think_val, effort_val = parse_thinking_arg(value)
                    agent._thinking_level = value
                    agent._think = think_val
                    agent._reasoning_effort = effort_val
                    print(
                        green(
                            f"Set {name} = {value!r}  →  think={think_val}, reasoning_effort={effort_val}"
                        )
                    )
                    continue

                if spec.get("special_setter") == "_set_stream":
                    # /param stream true|false — override args.stream at runtime
                    agent._runtime_kwargs["stream"] = value
                    # Also update args.stream so the chat loop picks it up on next turn
                    args.stream = value
                    print(green(f"Set {name} = {value!r}  (takes effect on next message)"))
                    continue

                # Standard setter
                attr = spec.get("agent_attr")
                if attr:
                    setattr(agent, attr, value)
                    # ROB-14: pin explicitly-set num_ctx / num_predict so a later
                    # /model switch re-derives only UNPINNED values.
                    if attr == "num_ctx":
                        agent._num_ctx_explicit = True
                    elif attr == "_num_predict":
                        agent._num_predict_explicit = True
                    elif attr == "_num_batch":
                        # R07.17: pin num_batch too — though /model never
                        # re-derives it (per-request, not model-derived), the
                        # pin flag is consulted by /param reset for parity.
                        agent._num_batch_explicit = True
                    elif attr == "_repeat_penalty":
                        # R07.18: same per-request pin semantics as num_batch.
                        agent._repeat_penalty_explicit = True
                    elif attr == "_repeat_last_n":
                        agent._repeat_last_n_explicit = True
                else:
                    agent._runtime_kwargs[name] = value

                print(green(f"Set {name} = {value!r}"))
                continue

            # ── /models slash command ──────────────────────────────────────────
            # List all available models from the current backend. Shows:
            #   ✓ = current model
            #   free / paid markers (from free_tier flag)
            #   chat / non-chat markers (from is_chat_model flag)
            #   ⚠ = deprecated (e.g. gemini-2.5-* for new users)
            # Usage:
            #   /models                     — list all models
            #   /models free                — list only free-tier models
            #   /models chat                — list only chat-capable models
            #   /models free chat           — both filters (AND)
            if user_input == "/models" or user_input.startswith("/models "):
                filter_parts = user_input.split()[1:] if user_input != "/models" else []
                filter_free = "free" in filter_parts
                filter_chat = "chat" in filter_parts

                # Get the model list from the backend
                try:
                    models = agent.backend.list_models()
                except Exception as e:
                    print(red(f"Failed to list models: {e}"))
                    continue

                if not models:
                    print(yellow("No models available from this backend."))
                    continue

                # Apply filters
                if filter_free:
                    models = [m for m in models if m.get("details", {}).get("free_tier", False)]
                if filter_chat:
                    models = [m for m in models if m.get("details", {}).get("is_chat_model", True)]

                if not models:
                    print(yellow("No models match the filter."))
                    continue

                # R07.19 (follow-up #7): on a real terminal /models is now an
                # INTERACTIVE switcher — the same (filtered) list, arrow-
                # navigable, and Enter switches via the same
                # apply_model_switch path as /model <name>. Piped stdin
                # (scripts, tests, ACP) keeps the plain listing below.
                if sys.stdin.isatty():
                    backend_name = getattr(agent.backend, "backend_type", None)
                    backend_str = (
                        backend_name.value if hasattr(backend_name, "value") else str(backend_name)
                    )
                    filter_desc = ""
                    if filter_free and filter_chat:
                        filter_desc = " — free + chat only"
                    elif filter_free:
                        filter_desc = " — free tier only"
                    elif filter_chat:
                        filter_desc = " — chat-capable only"
                    _interactive_model_switch(
                        agent, models, title=f"Switch model ({backend_str}{filter_desc})"
                    )
                    continue

                current_model = agent.model
                backend_name = getattr(agent.backend, "backend_type", None)
                backend_str = (
                    backend_name.value if hasattr(backend_name, "value") else str(backend_name)
                )

                filter_desc = ""
                if filter_free and filter_chat:
                    filter_desc = " (free + chat only)"
                elif filter_free:
                    filter_desc = " (free tier only)"
                elif filter_chat:
                    filter_desc = " (chat-capable only)"

                print(
                    f"{bold('Available models')} ({backend_str}, {len(models)} total{filter_desc}):"
                )
                for m in models:
                    name = m.get("name", "unknown")
                    details = m.get("details", {})
                    ctx = details.get("context_length", 0)
                    ctx_str = f"{ctx // 1024}K" if ctx >= 1000 else str(ctx)
                    is_free = details.get("free_tier", False)
                    is_chat = details.get("is_chat_model", True)
                    is_current = name == current_model

                    # Build markers
                    markers = []
                    if is_current:
                        markers.append(green("✓"))
                    else:
                        markers.append(dim("○"))
                    markers.append(green("free") if is_free else red("paid"))
                    markers.append(cyan("chat") if is_chat else dim("non-chat"))
                    # Deprecated check (gemini-2.5-* models restricted for new users)
                    if name.startswith("gemini-2.5") and name != current_model:
                        markers.append(yellow("⚠deprecated"))

                    marker_str = " ".join(markers)
                    print(f"  {marker_str} {name:<45} {dim(ctx_str):>8}")

                print()
                print(dim(f"  Current: {current_model}"))
                filter_hint = "free, chat" if not (filter_free or filter_chat) else ""
                if filter_hint:
                    print(dim(f"  Filters: /models {filter_hint}"))
                print(dim("  Switch with: /model <name>"))
                continue

            if user_input == "/model":
                print(f"Current model: {cyan(agent.model)}")
                continue

            if user_input.startswith("/model "):
                new_model = user_input[7:].strip()
                if not new_model:
                    print(yellow("Usage: /model <model_name>"))
                else:
                    # R07.06 (ROB-14): switching models must also re-derive the
                    # per-model settings — num_ctx, num_predict, family config —
                    # which previously stayed on the OLD model (stale num_ctx
                    # invited context-length 400s after switching to a smaller
                    # window). Explicitly pinned values (--num-ctx/--num-predict,
                    # /param) survive; everything else follows the new model's
                    # catalog entry, mirroring startup precedence.
                    # R07.19 (follow-up #7): the outcome reporting is shared
                    # with the /models arrow-key switcher (_report_model_switch).
                    _report_model_switch(_cli.apply_model_switch(agent, new_model), new_model)
                continue

            if user_input == "/debug":
                agent.debug = not agent.debug
                state = green("ON") if agent.debug else red("OFF")
                print(f"Debug output: {state}")
                continue

            if user_input == "/clear":
                agent.clear_memory()
                print(green("Memory cleared."))
                continue

            if user_input == "/status":
                from ...core.helpers import get_security_mode

                print(f"Model: {cyan(agent.model)}")
                backend_name = getattr(agent.backend, "backend_type", None)
                if backend_name is not None:
                    print(
                        f"Backend: {green(backend_name.value if hasattr(backend_name, 'value') else str(backend_name))}"
                    )
                print(f"API mode: {green(agent._is_comp_mode and 'openai' or 'openre')}")
                print(f"Tools: {yellow(str(agent.tools.names()))}")
                print(f"Tool choice: {yellow(agent.tool_choice.type.value)}")
                print(f"Security: {green('max') if get_security_mode() == 'max' else red('off')}")
                # R07.32: on the sd backend --max-steps is remapped to
                # diffusion sample steps — show the effective value (the
                # loop ceiling is decorative for a one-shot image backend).
                from ...core.types import BackendType

                if getattr(agent.backend, "backend_type", None) == BackendType.STABLE_DIFFUSION:
                    print(f"Sample steps: {yellow(agent.backend.sample_steps_display())}")
                else:
                    print(f"Max steps: {yellow(str(agent.max_steps))}")
                print(f"Memory turns: {yellow(str(len(agent.memory)))}")
                # Show loaded skills (R06.2+)
                loaded_skills = getattr(agent, "_loaded_skills", [])
                if loaded_skills:
                    print(f"Skills: {magenta(', '.join(loaded_skills))}")
                else:
                    print(f"Skills: {dim('(none — use --skills <name> to load)')}")
                print(f"Debug: {green('ON') if agent.debug else red('OFF')}")
                if agent.soul:
                    print(f"Soul: {cyan(agent.soul.display_name)} v{agent.soul.version}")
                continue

            # ───────────────────────────────────────────────────────────────
            # /sh — run a local shell command, display output, and (by default)
            # inject the output into the agent's context as a user-role
            # message so the model can use it on the next turn. Pass `-n`
            # before the command to display-only (skip the context injection).
            #
            # Examples:
            #   /sh ls -la              → display + inject into context
            #   /sh -n ls -la           → display only (don't inject)
            #   /sh git log --oneline   → display + inject
            #   /sh -n pwd              → display only
            #
            # Reuses the built-in `shell()` tool from agentkthx.tools.builtins
            # so the same security checks (sanitize_command — blocked patterns,
            # heredoc/shell-injection guards), timeout clamping (max 300s), and
            # exit-code formatting apply. The user's prompt is NOT routed
            # through the model — it runs locally and synchronously.
            #
            # R07.21.
            if user_input == "/sh" or user_input.startswith("/sh "):
                from ...tools.builtins import shell as _shell_tool

                # Parse: /sh [-n] <command...>
                # -n must be the first token after /sh to count as the
                # display-only flag. Anything else is treated as the command.
                _sh_parts = user_input.split(None, 1)
                if len(_sh_parts) < 2 or not _sh_parts[1].strip():
                    print(yellow("Usage: /sh [-n] <command...>"))
                    print(dim("  Run a local shell command and display the output."))
                    print(dim("  By default, the output is also added to the agent's context."))
                    print(
                        dim("  Pass -n before the command to display only (no context injection).")
                    )
                    print(dim("  Examples:"))
                    print(dim("    /sh ls -la"))
                    print(dim("    /sh -n pwd"))
                    print(dim("    /sh git log --oneline -5"))
                    continue

                _sh_arg = _sh_parts[1].strip()
                _sh_no_inject = False
                if _sh_arg == "-n":
                    # Bare `/sh -n` with no command
                    print(yellow("Usage: /sh -n <command...>"))
                    continue
                if _sh_arg.startswith("-n ") or _sh_arg == "-n":
                    _sh_no_inject = True
                    _sh_arg = _sh_arg[2:].lstrip()
                if not _sh_arg:
                    print(yellow("No command provided. Usage: /sh [-n] <command...>"))
                    continue

                # Run the command via the shell builtin. This applies
                # sanitize_command (blocked patterns, injection guards) and
                # the 1-300s timeout clamp. The user sees the raw output
                # (exit code marker + stdout + stderr per builtins.shell).
                print(dim(f"  $ {_sh_arg}"))
                _sh_output = _shell_tool(_sh_arg)

                # Display to the user (always, regardless of -n).
                print()
                print(_sh_output)
                print()

                # Inject into the agent's context as a user-role message
                # (skip when -n was passed). The message shape mirrors the
                # shell tool's `<tool_output>` convention so the model can
                # parse it cleanly: a tagged block with the command + result.
                if not _sh_no_inject:
                    _sh_context_msg = (
                        f"<shell_output command={_sh_arg!r}>\n" f"{_sh_output}\n" f"</shell_output>"
                    )
                    agent.memory.add("user", _sh_context_msg)
                    print(
                        dim(
                            f"  [Added shell output to context — "
                            f"{len(agent.memory)} turns in memory]"
                        )
                    )
                else:
                    print(dim("  [-n] output not added to context"))
                continue

            # Log user message to ACP
            if acp:
                acp.log_chat("user", user_input)

            # Run with spinner (suppress spinner when debug is on — debug already prints progress).
            # The spinner shows "⠇ thinking..." on stderr while waiting for the
            # first response. For streaming, the spinner covers the gap before
            # the first token arrives (thinking models can take 60-90+ seconds);
            # once streaming output starts on stdout, the spinner is still
            # running on stderr but visually the streaming output takes over.
            spinner_t = None
            # Pre-compute stream flag so we know whether to suppress the spinner.
            # This must mirror the logic used below when calling agent.run().
            # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
            # with backend.is_cloud — a 5th cloud backend will automatically stream.
            _is_cloud = getattr(agent.backend, "is_cloud", False)
            _explicit = getattr(args, "stream", None)
            _will_stream = _explicit is True or (_explicit is None and _is_cloud)
            # R07.26: always start the spinner unless debug is on — even for
            # streaming. Thinking models (GLM-5.3-flash, DeepSeek-V4.1-flash,
            # kimi-k3) can take 60-90+ seconds before the first token; without
            # the spinner, the user sees nothing and assumes it's hung.
            if not agent.debug:
                print()  # blank line before spinner
                spinner_t = _spinner_start()

                # R07.26: set a callback so the spinner is stopped + cleared
                # the moment the first streaming chunk arrives (content or
                # reasoning). Without this, the spinner keeps overwriting
                # streaming text on stderr until agent.run() returns.
                # The callback is one-shot: streaming.py sets it to None
                # after the first invocation so subsequent chunks don't
                # re-trigger it.
                def _stop_spinner_on_first_chunk():
                    if spinner_t:
                        _spinner_stop_thread(spinner_t)

                agent._on_first_stream_chunk = _stop_spinner_on_first_chunk
            try:
                # Enable streaming by default for cloud providers, but respect
                # explicit --stream / --no-stream from the user.
                #   --stream       → always stream (even for local backends)
                #   --no-stream    → never stream (even for cloud providers)
                #   (neither)      → stream for cloud providers, non-stream for local
                # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
                # with backend.is_cloud — a 5th cloud backend will automatically stream.
                is_cloud_provider = getattr(agent.backend, "is_cloud", False)
                explicit_stream = getattr(args, "stream", None)
                if explicit_stream is True:
                    stream = True
                elif explicit_stream is False:
                    stream = False
                else:
                    stream = is_cloud_provider
                result = agent.run(user_input, stream=stream)
            except KeyboardInterrupt:
                print(f"\n{yellow('Cancelled.')}\n")
                continue
            except RuntimeError as e:
                # Handle rate limits and other runtime errors
                print(f"\n{red('Error:')} {e}\n")
                if "rate limit" in str(e).lower() or "429" in str(e):
                    print(f"{red('This appears to be a rate limit error.')}")
                elif "empty response" in str(e).lower() or "no choices" in str(e).lower():
                    print(
                        f"{red('OpenRouter returned no content. This may be a temporary API issue.')}"
                    )
                continue
            except Exception as e:
                # Catch any other unexpected errors
                import traceback

                print(f"\n{red('Unexpected Error:')} {type(e).__name__}: {e}")
                print(f"{dim('Full Traceback:')}")
                traceback.print_exc()
                print()
                continue
            finally:
                if spinner_t:
                    _spinner_stop_thread(spinner_t)
            # Accumulate session token counts
            for step in result.steps:
                # Estimate: ~60% prompt, ~40% completion (rough heuristic)
                _session_tokens_in += int(step.tokens_used * 0.6)
                _session_tokens_out += int(step.tokens_used * 0.4)
            # Print tool-call summary so the user sees what the agent did,
            # not just the final answer. Skipped in debug mode (agent already
            # printed verbose step output) AND in streaming mode (tool calls
            # are printed inline as they execute — the post-run summary would
            # be redundant).
            if not _will_stream:
                _print_agent_steps(
                    result,
                    debug=agent.debug,
                    show_reasoning=getattr(agent, "_show_reasoning", False),
                )

            # Detect empty final answers — the agent ran but produced no
            # response text. This usually means the model hit a rate limit
            # or content filter mid-conversation. Surface it as an error
            # instead of showing a blank "AgentKthx: " line.
            if not result.final_answer or not result.final_answer.strip():
                # R06.52+: if the run was paused by sustained provider
                # throttling, say so plainly and tell the user how to resume —
                # the old advice ("try again in a few seconds") was wrong once
                # the resilience layer had already been waiting for minutes.
                _last_err = ""
                if result.steps:
                    _last_err = getattr(result.steps[-1], "error", "") or ""
                _low = _last_err.lower()
                # ROB-38 (R07.21 CLOSED): detect definitive FATAL errors
                # (auth/quota/credits) BEFORE the throttle branch. The old
                # boilerplate blamed every empty answer on a rate limit and
                # advised "try again in a few seconds" — correct for 429,
                # wrong for 401 (key bad), 402 (out of credits), 403 (key
                # lacks permission for this model). A user who followed the
                # advice for a 402 would just hit the same wall again. The
                # fatal branch names the real problem and points at the
                # remedy (regenerate key / add credits / pick a different
                # model). The throttle branch is unchanged for genuine 429s.
                #
                # R07.21 follow-up: broadened "api key" marker — Gemini
                # returns HTTP 400 (not 401) with "Please pass a valid API
                # key" for auth failures. The original "invalid api key"
                # marker missed this (the message says "valid", not
                # "invalid"). The bare "api key" substring catches every
                # variant: "valid api key", "invalid api key", "missing api
                # key", "no api key", "api key not set", "api key required".
                _fatal = (
                    "401" in _low
                    or "unauthorized" in _low
                    or "authentication failed" in _low
                    or "invalid api key" in _low
                    or "api key" in _low  # broadened — catches "valid api key"
                    or "402" in _low
                    or "payment required" in _low
                    or "insufficient credit" in _low
                    or "out of credit" in _low
                    or "quota" in _low
                    or "403" in _low
                    or "forbidden" in _low
                    or "permission" in _low
                )
                _throttled = (
                    "rate limit" in _low
                    or "ratelimit" in _low
                    or "429" in _low
                    or "empty response" in _low
                    or "no choices" in _low
                    or "provider returned error" in _low
                )
                if _fatal:
                    print(f"\n{red('AgentKthx: (empty response)')}")
                    print(
                        red(
                            "  The model returned no content because of a fatal "
                            "error — NOT a rate limit."
                        )
                    )
                    # Print the actual error so the user can see the real
                    # cause (the upstream error message is the truth).
                    if _last_err:
                        print(dim(f"  Last error: {_last_err[:200]}"))
                    print(
                        yellow(
                            "  Fix: check your API key (401), add credits (402), "
                            "or switch to a model your key can access (403)."
                        )
                    )
                    print(
                        dim(
                            "  Use /auth to set a new key, or /model to pick a "
                            "different model, then send 'continue'."
                        )
                    )
                elif _throttled:
                    _pause_msg = (
                        "⏸  Run paused — the provider kept rate-limiting this model "
                        "even after repeated retries."
                    )
                    print(f"\n{yellow(_pause_msg)}")
                    print(
                        yellow(
                            "   Your conversation history is intact: just send 'continue' "
                            "(or any message) to pick up where it left off."
                        )
                    )
                    print(
                        yellow(
                            "   Tip: ':free' models throttle hard on long agentic runs. A paid "
                            "model avoids this, or raise"
                        )
                    )
                    print(
                        yellow(
                            "   AGENTKTHX_MAX_API_RETRIES / OPENROUTER_MAX_429_RETRIES to "
                            "give the harness more patience."
                        )
                    )
                else:
                    print(f"\n{red('AgentKthx: (empty response)')}")
                    print(
                        yellow(
                            "  The model returned no content. This is likely a "
                            "rate limit (429) or content filter."
                        )
                    )
                    print(
                        yellow(
                            "  Try again in a few seconds, or use /debug to see " "what happened."
                        )
                    )
            else:
                # Display reasoning_content under the answer when --think is set
                # (only if the model emitted reasoning_content).
                show_reasoning = getattr(agent, "_show_reasoning", False)
                reasoning_content = ""
                if show_reasoning and result.steps:
                    # Get reasoning_content from the LAST FINAL_ANSWER step
                    from ...core.types import StepResultType

                    for step in reversed(result.steps):
                        if step.type == StepResultType.FINAL_ANSWER:
                            reasoning_content = getattr(step, "reasoning_content", "") or ""
                            break

                # PERF-01: when streaming, the final answer was already printed
                # by the typewriter effect in _generate_stream(). Don't print it
                # again — that would duplicate the response.
                #
                # R06.56: reasoning_content is now streamed to a "reasoning:"
                # panel ABOVE the AgentKthx: prompt during _generate_stream()
                # (see agent.py:_emit_reasoning_panel_header). So we DON'T need
                # to print the reasoning panel again here — that would duplicate
                # the display. Skip the post-stream reasoning panel for the
                # streaming path entirely.
                if _will_stream:
                    # R06.56: Streaming already printed both the reasoning panel
                    # (above AgentKthx:) and the content (under AgentKthx:).
                    # Don't print either again — would duplicate.
                    # Just add a trailing blank line for spacing before the next
                    # "You: " prompt.
                    if reasoning_content:
                        # Reasoning was streamed above the prefix — add a blank
                        # line after the answer for visual separation.
                        print()
                    # No "AgentKthx: <answer>" line — content already streamed.
                    # No "reasoning:" panel — already streamed above the prefix.
                elif reasoning_content:
                    # Non-streaming path — show reasoning panel ABOVE the
                    # AgentKthx: response, matching the streaming UX-01 layout.
                    # R06.57: was AgentKthx first then reasoning below; now
                    # reasoning first, then AgentKthx response.
                    print(f"{dim('  reasoning:')}")
                    for line in reasoning_content.splitlines():
                        if len(line) > 200:
                            line = line[:197] + "..."
                        print(f"    {dim(line)}")
                    print(f"\n{bright_green('AgentKthx')}: {result.final_answer}")
                    print()
                else:
                    print(f"\n{bright_green('AgentKthx')}: {result.final_answer}\n")

            # R07.15 amendment (user request): per-response stats line, the
            # chat counterpart of agent mode's verbose ⏱️ footer. One dim
            # line under EVERY completed response — streaming and
            # non-streaming, debug and non-debug — showing step count,
            # tool-call count and wall-clock ms for this turn (plus the
            # tools used when the run made tool calls). Shared format via
            # agent_mode._format_response_stats; kept OUTSIDE the
            # empty-answer branch so a throttled/failed turn still reports
            # how long it burned. Printed before the footer refresh so the
            # persistent footer stays the last visual element.
            for _stats_line in _format_response_stats(result, indent="  "):
                print(dim(_stats_line))

            # Refresh the persistent footer with updated token counts.
            # The footer lives on the reserved bottom line (scroll region)
            # and updates in place — no old footer text enters scrollback.
            _update_footer()

            # Log assistant response to ACP
            if acp:
                acp.log_chat("assistant", result.final_answer)

    finally:
        # ROB-12 (R07.12 intra): unregister the footer-refresh callback on
        # ALL exit paths. The lambda closes over this dead frame's footer
        # state — if the Agent instance is reused after cmd_chat returns,
        # the stale callback would keep firing (benign ANSI noise in
        # production, AttributeError bait in tests). Mirrors the scroll-
        # region teardown contract below.
        agent._on_step_callback = None
        # Tear down terminal scroll region on ALL exit paths so the
        # terminal is never left in a broken state.
        _teardown_footer_region()
        # R07.22 MCP client (Phase 1.5): close every MCP server subprocess
        # the agent opened. Skip silently if MCP wasn't enabled (no
        # _mcp_manager attr). Best-effort — close errors are swallowed
        # because we're tearing down anyway and the user can't act on
        # them mid-exit.
        _mcp_mgr = getattr(agent, "_mcp_manager", None)
        if _mcp_mgr is not None:
            try:
                _mcp_mgr.close_all(timeout=1.0)
            except Exception:
                pass

    return 0
