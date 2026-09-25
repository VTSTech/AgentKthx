"""`agentkthx chat` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import shutil
import sys
import threading

from ... import __version__
from ...colors import bold, magenta, yellow, dim, green, cyan, bright_green, red, bright_cyan
from ...config import get_config
from ...tools import make_builtin_registry

from ..utils import _print_agent_steps




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

    agent = _cli._build_agent(args, config)

    _cli._print_session_header(agent, args, config, "Chat Mode")
    print("Type '/quit' to exit, '/help' for commands\n")

    # Update notice under the banner — printed BEFORE the persistent footer
    # takes over the bottom of the terminal (see _update_footer scroll regions).
    _cli._print_update_notice()

    _session_tokens_in = 0
    _session_tokens_out = 0

    def _footer_line1() -> str:
        """Build the first footer line: version, model, prompt, context, tokens."""
        ctx = agent.num_ctx
        ctx_str = f"{ctx // 1024}K" if ctx and ctx >= 1024 else str(ctx) if ctx else '?'
        max_t = agent._num_predict if agent._num_predict is not None else agent.model_config.default_max_tokens
        max_t_str = f"{max_t // 1024}K" if max_t >= 1024 else str(max_t)
        temp = agent._temperature if agent._temperature is not None else agent.model_config.default_temperature
        def _fmt_tok(n):
            n = int(str(n).strip())
            if n >= 1000:
                return f"{n/1000:.1f}k"
            return str(n)
        _sys_prompt = getattr(agent, '_custom_system_prompt', '') or ''
        _prompt_chr = len(_sys_prompt)
        _prompt_tok = _prompt_chr // 4
        prompt_str = f"{_fmt_tok(_prompt_chr)} chr {_fmt_tok(_prompt_tok)} tok"
        _e_brand = '\u269b\ufe0f'
        _e_model = '\U0001f9e0'
        _e_ctx   = '\U0001f4e6'
        _e_resp  = '\U0001f4ac'
        _e_temp  = '\U0001f321\ufe0f'
        _e_prmpt = '\U0001f4dd'
        parts = [
            f"{dim(_e_brand)} {cyan(__version__)}",
            f"{dim(_e_model)} {cyan(agent.model)}",
            f"{dim(_e_prmpt)} {yellow(prompt_str)}",
            f"{dim(_e_ctx)} {yellow(ctx_str)}",
            f"{dim(_e_resp)} {yellow(max_t_str)}",
            f"{dim(_e_temp)} {yellow(str(temp))}",
        ]
        return ' '.join(parts)

    def _footer_line2() -> str:
        """Build the second footer line: backend, token usage, context %, debug flag."""
        backend = getattr(agent.backend, 'backend_type', None)
        bname = backend.value if backend and hasattr(backend, 'value') else str(backend) if backend else '?'
        def _fmt_tok(n):
            n = int(str(n).strip())
            if n >= 1000:
                return f"{n/1000:.1f}k"
            return str(n)
        # Use agent's running totals (updated during the streaming loop)
        # instead of the post-run _session_tokens_in/out closure vars
        # which only update after agent.run() returns.
        _tok_in = getattr(agent, '_running_tokens_in', 0) or _session_tokens_in
        _tok_out = getattr(agent, '_running_tokens_out', 0) or _session_tokens_out
        tok_str = f"\u2191{_fmt_tok(_tok_in)} \u2193{_fmt_tok(_tok_out)}"
        # Session context usage percentage: (in + out) / num_ctx
        _total_session = _tok_in + _tok_out
        _ctx = agent.num_ctx or 8192
        _ctx_pct = min(100, int((_total_session / _ctx) * 100)) if _ctx > 0 else 0
        # Color the percentage based on usage level
        if _ctx_pct >= 85:
            _ctx_pct_str = red(f"{_ctx_pct}%")
        elif _ctx_pct >= 60:
            _ctx_pct_str = yellow(f"{_ctx_pct}%")
        else:
            _ctx_pct_str = green(f"{_ctx_pct}%")
        _e_be    = '\U0001f50c'
        _e_tok   = '\U0001f4c8'
        _e_dbg   = '\U0001f41b'
        parts = [
            f"{dim(_e_be)} {green(bname)}",
            f"{dim(_e_tok)} {yellow(tok_str)}",
            f"{dim('ctx')} {_ctx_pct_str}",
        ]
        if agent.debug:
            parts.append(f"{red(_e_dbg + ' debug')}")
        return ' '.join(parts)

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
    _use_persistent_footer = bool(
        _is_tty and _term_size and _term_size.lines >= 6
    )

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
        if (new_size.lines != _term_size.lines or
            new_size.columns != _term_size.columns):
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
        sys.stdout.write("\033[s")                            # save cursor
        sys.stdout.write("\033[?7l")                           # disable line wrap
        try:
            # Line 1: second-to-last terminal line
            row1 = _term_size.lines - 1
            sys.stdout.write(f"\033[{row1};1H")                # move to line 1
            sys.stdout.write("\033[2K")                         # clear entire line
            sys.stdout.write(line1)                             # write footer line 1
            # Line 2: last terminal line
            row2 = _term_size.lines
            sys.stdout.write(f"\033[{row2};1H")                # move to line 2
            sys.stdout.write("\033[2K")                         # clear entire line
            sys.stdout.write(line2)                             # write footer line 2
        finally:
            sys.stdout.write("\033[?7h")                       # re-enable line wrap
        sys.stdout.write("\033[u")                             # restore cursor
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
    _SPINNER_FRAMES = ['\u2807', '\u2839', '\u2838', '\u283C', '\u2834', '\u2826', '\u2836', '\u282D', '\u282F', '\u280F']
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
        sys.stderr.write('\r' + ' ' * 30 + '\r')
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
            import readline
            # Force horizontal-scroll-mode OFF so long input wraps to a new
            # visual line instead of scrolling horizontally within one line.
            # Default is OFF, but an ~/.inputrc could enable it. Without this,
            # input at the `You:` prompt would overwrite the rightmost column
            # instead of scrolling the scroll region up for a new input line.
            readline.parse_and_bind("set horizontal-scroll-mode off")

            # Prompt uses \001 ... \002 (readline's RL_PROMPT_START_IGNORE /
            # RL_PROMPT_END_IGNORE) around ANSI escape codes so readline
            # counts them as zero-width. Without these markers, readline
            # treats `\033[90m` + `You:` + `\033[0m` + ` ` as 14 visible
            # chars, miscounting the prompt width and breaking wrap detection.
            user_input = input(
                "\001\033[90m\002You:\001\033[0m\002 "
            ).strip()

            # Track last user_input for in-session recall (replaces file-based history)
            if user_input:
                _last_user_input = user_input
        except (EOFError, KeyboardInterrupt):
            # Ensure persistent memory is flushed and closed
            if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'close'):
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
            if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'close'):
                agent.memory.close()
            print(bright_cyan("👋 Goodbye!"))
            break

        if user_input == "/help":
            print(f"  {cyan('/clear')}      Clear conversation memory")
            print(f"  {cyan('/debug')}      Toggle debug output on/off")
            print(f"  {cyan('/help')}       Show this help message")
            print(f"  {cyan('/model')}      Show or change the model (e.g. /model gemini-3.8-flash)")
            print(f"  {cyan('/models')}     List all available models from the current backend (✓ = current)")
            print(f"  {cyan('/param')}      Show or set generation parameters (temp, top_p, top_k, etc.)")
            print(f"  {cyan('/security')}   Show or set security mode (max|off)")
            print(f"  {cyan('/skills')}     Show available skills (✓ = loaded)")
            print(f"  {cyan('/skill')}      Load a skill mid-session (e.g. /skill codebase-audit, crypto-signals)")
            print(f"  {cyan('/status')}     Show model, backend, tools, skills, and memory info")
            print(f"  {cyan('/system')}     Print the current system prompt")
            print(f"  {cyan('/tools')}      Show available tools (✓ = loaded)")
            print(f"  {cyan('/tool')}       Load a tool mid-session (e.g. /tool shell,read_file,write_file)")
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
                        print(yellow("  The model can now run any command, read/write any path,"))
                        print(yellow("  and fetch any URL. Use with caution."))
                else:
                    print(yellow(f"Invalid security mode: {mode!r}. Use 'max' or 'off'."))
            continue

        if user_input == "/system":
            prompt = getattr(agent, '_custom_system_prompt', '')
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
                    desc = t.description.split('.')[0] if t.description else 'No description'
                    if len(desc) > 60:
                        desc = desc[:57] + '...'
                    params = ", ".join(p.name for p in t.params) if t.params else ""
                    param_str = dim(f"  ({params})") if params else ""
                    print(f"  {marker} {cyan(t.name):<28}{desc}{param_str}")
                    if is_loaded:
                        loaded_count += 1
                print()
                print(dim(f"  {loaded_count}/{len(all_tool_list)} tools loaded. "
                          f"Use {cyan('/tool <name,name,...>')} to load more."))
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
                print(green(f"  ✓ Loaded {len(newly_loaded)} tool(s): ") + cyan(", ".join(newly_loaded)))
            if already_loaded:
                print(yellow(f"  ⚠ Already loaded ({len(already_loaded)}): ") + dim(", ".join(already_loaded)))
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
            loaded = getattr(agent, '_loaded_skills', [])
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
                    desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
                except Exception as e:
                    desc = red(f"Error: {e}")
                print(f"  {marker} {magenta(name):<28}{desc}")
            print()
            print(dim(f"  {len(loaded)}/{len(available)} skills loaded. "
                      f"Use {cyan('/skill <name,name,...>')} to load more."))
            continue

        # ── /skill slash command ───────────────────────────────────────────
        # Load skills mid-session. Supports comma-separated list.
        # Usage:
        #   /skill                      — show usage
        #   /skill codebase-audit       — load one skill
        #   /skill codebase-audit,crypto-signals  — load multiple skills
        if user_input == "/skill" or user_input.startswith("/skill "):
            parts = user_input.split(None, 1)
            loaded = getattr(agent, '_loaded_skills', [])
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
                        old_prompt = getattr(agent, '_custom_system_prompt', '') or ''
                        agent._custom_system_prompt = f"{old_prompt}\n\n# Skill: {skill.name}\n{skill_text}"
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
                print(green(f"  ✓ Loaded {len(newly_loaded)} skill(s): ") + magenta(", ".join(newly_loaded)))
            if already_loaded:
                print(yellow(f"  ⚠ Already loaded ({len(already_loaded)}): ") + dim(", ".join(already_loaded)))
            if not_found:
                print(red(f"  ✗ Not found ({len(not_found)}): ") + dim(", ".join(not_found)))
                if available:
                    print(dim(f"  Available: {', '.join(available)}"))
            if not newly_loaded and not already_loaded and not not_found:
                print(yellow("  No skills specified."))
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
            from ...core.types import BackendType

            # Get current backend type
            backend_type = getattr(agent.backend, 'backend_type', None)
            backend_name = backend_type.value if hasattr(backend_type, 'value') else str(backend_type)

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
            if not hasattr(agent, '_runtime_kwargs'):
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
                    if 'range' in spec:
                        print(f"    {dim('Range:')} {dim(spec['range'])}")
                    if 'note' in spec:
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
                print(yellow(f"Parameter '{name}' is not supported by backend '{backend_name}'."))
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
                            print(yellow(f"Invalid value: {value!r}. Must be one of: {', '.join(valid_values)}"))
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
                print(green(f"Set {name} = {value!r}  →  think={think_val}, reasoning_effort={effort_val}"))
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

            current_model = agent.model
            backend_name = getattr(agent.backend, 'backend_type', None)
            backend_str = backend_name.value if hasattr(backend_name, 'value') else str(backend_name)

            filter_desc = ""
            if filter_free and filter_chat:
                filter_desc = " (free + chat only)"
            elif filter_free:
                filter_desc = " (free tier only)"
            elif filter_chat:
                filter_desc = " (chat-capable only)"

            print(f"{bold(f'Available models')} ({backend_str}, {len(models)} total{filter_desc}):")
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
            print(dim(f"  Switch with: /model <name>"))
            continue

        if user_input == "/model":
            print(f"Current model: {cyan(agent.model)}")
            continue

        if user_input.startswith("/model "):
            new_model = user_input[7:].strip()
            if not new_model:
                print(yellow("Usage: /model <model_name>"))
            else:
                old_model = agent.model
                agent.model = new_model
                print(green(f"Model changed: {old_model} -> {new_model}"))
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
            backend_name = getattr(agent.backend, 'backend_type', None)
            if backend_name is not None:
                print(f"Backend: {green(backend_name.value if hasattr(backend_name, 'value') else str(backend_name))}")
            print(f"API mode: {green(agent._is_comp_mode and 'openai' or 'openre')}")
            print(f"Tools: {yellow(str(agent.tools.names()))}")
            print(f"Tool choice: {yellow(agent.tool_choice.type.value)}")
            print(f"Security: {green('max') if get_security_mode() == 'max' else red('off')}")
            print(f"Max steps: {yellow(str(agent.max_steps))}")
            print(f"Memory turns: {yellow(str(len(agent.memory)))}")
            # Show loaded skills (R06.2+)
            loaded_skills = getattr(agent, '_loaded_skills', [])
            if loaded_skills:
                print(f"Skills: {magenta(', '.join(loaded_skills))}")
            else:
                print(f"Skills: {dim('(none — use --skills <name> to load)')}")
            print(f"Debug: {green('ON') if agent.debug else red('OFF')}")
            if agent.soul:
                print(f"Soul: {cyan(agent.soul.display_name)} v{agent.soul.version}")
            continue

        # Log user message to ACP
        if acp:
            acp.log_chat("user", user_input)

        # Run with spinner (suppress spinner when debug is on — debug already prints progress).
        # PERF-01: also suppress the spinner when stream=True — streaming output
        # itself is the progress indicator (typewriter effect on stdout), and a
        # spinning cursor on stderr would visually compete with it.
        spinner_t = None
        # Pre-compute stream flag so we know whether to suppress the spinner.
        # This must mirror the logic used below when calling agent.run().
        # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
        # with backend.is_cloud — a 5th cloud backend will automatically stream.
        _is_cloud = getattr(agent.backend, 'is_cloud', False)
        _explicit = getattr(args, 'stream', None)
        _will_stream = (
            _explicit is True or
            (_explicit is None and _is_cloud)
        )
        if not agent.debug and not _will_stream:
            print()  # blank line before spinner
            spinner_t = _spinner_start()
        try:
            # Enable streaming by default for cloud providers, but respect
            # explicit --stream / --no-stream from the user.
            #   --stream       → always stream (even for local backends)
            #   --no-stream    → never stream (even for cloud providers)
            #   (neither)      → stream for cloud providers, non-stream for local
            # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
            # with backend.is_cloud — a 5th cloud backend will automatically stream.
            is_cloud_provider = getattr(agent.backend, 'is_cloud', False)
            explicit_stream = getattr(args, 'stream', None)
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
                print(f"{red('OpenRouter returned no content. This may be a temporary API issue.')}")
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
            _print_agent_steps(result, debug=agent.debug, show_reasoning=getattr(agent, '_show_reasoning', False))

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
            _throttled = (
                "rate limit" in _low
                or "ratelimit" in _low
                or "429" in _low
                or "empty response" in _low
                or "no choices" in _low
                or "provider returned error" in _low
            )
            if _throttled:
                print(f"\n{yellow('⏸  Run paused — the provider kept rate-limiting this model '
                                   'even after repeated retries.')}")
                print(yellow("   Your conversation history is intact: just send 'continue' "
                             "(or any message) to pick up where it left off."))
                print(yellow("   Tip: ':free' models throttle hard on long agentic runs. A paid "
                             "model avoids this, or raise"))
                print(yellow("   AGENTKTHX_MAX_API_RETRIES / OPENROUTER_MAX_429_RETRIES to "
                             "give the harness more patience."))
            else:
                print(f"\n{red('AgentKthx: (empty response)')}")
                print(yellow("  The model returned no content. This is likely a "
                             "rate limit (429) or content filter."))
                print(yellow("  Try again in a few seconds, or use /debug to see "
                             "what happened."))
        else:
            # Display reasoning_content under the answer when --think is set
            # (only if the model emitted reasoning_content).
            show_reasoning = getattr(agent, '_show_reasoning', False)
            reasoning_content = ""
            if show_reasoning and result.steps:
                # Get reasoning_content from the LAST FINAL_ANSWER step
                from ...core.types import StepResultType
                for step in reversed(result.steps):
                    if step.type == StepResultType.FINAL_ANSWER:
                        reasoning_content = getattr(step, 'reasoning_content', '') or ""
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

        # Refresh the persistent footer with updated token counts.
        # The footer lives on the reserved bottom line (scroll region)
        # and updates in place — no old footer text enters scrollback.
        _update_footer()

        # Log assistant response to ACP
        if acp:
            acp.log_chat("assistant", result.final_answer)

    finally:
        # Tear down terminal scroll region on ALL exit paths so the
        # terminal is never left in a broken state.
        _teardown_footer_region()

    return 0
