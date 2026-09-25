"""`agentkthx agent` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import shutil
import sys

from ... import __version__
from ...agent_mode import AgentMode
from ...colors import bright_yellow, yellow, dim, green, cyan, bright_green, bright_red, red, bright_cyan
from ...config import get_config




def cmd_agent(args: argparse.Namespace) -> int:
    """Execute the agent command."""

    # R07.00: resolve shared collaborators through the cli facade so that
    # monkeypatch.setattr(agentkthx.cli, '<name>', ...) keeps working.
    from agentkthx import cli as _cli

    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _cli._init_acp(args, config, "AgentKthx-Agent")
    if should_stop:
        return 1

    agent = _cli._build_agent(args, config)

    # R06.58: resolve stream flag the same way cmd_chat does — explicit
    # --stream wins, otherwise default to streaming for cloud backends
    # (ZAI, OpenRouter, Gemini) and non-streaming for local (Ollama).
    # Previously cmd_agent hardcoded verbose=True but never passed stream
    # to AgentMode, so --stream was silently ignored in agent mode.
    _is_cloud = getattr(agent.backend, 'is_cloud', False)
    _explicit_stream = getattr(args, 'stream', None)
    if _explicit_stream is True:
        _agent_stream = True
    elif _explicit_stream is False:
        _agent_stream = False
    else:
        _agent_stream = _is_cloud

    agent_mode = AgentMode(agent, verbose=True, stream=_agent_stream)

    _cli._print_session_header(agent, args, config, "Agent Mode")
    print("Give the agent a goal to accomplish autonomously.")
    print(f"Commands: {cyan('/status')}, {cyan('/pause')}, {cyan('/resume')}, {cyan('/stop')}, {cyan('/quit')}\n")

    # ── Persistent footer (R06.58): ported from cmd_chat ────────────────
    # Same 2-line scroll-region footer as chat mode: shows version, model,
    # ctx, prompt size, max_tokens, temperature on line 1; backend, token
    # usage, ctx% on line 2. Updates in place during streaming via the
    # _on_step_callback hook (same mechanism as cmd_chat).
    _session_tokens_in = 0
    _session_tokens_out = 0

    def _footer_line1() -> str:
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
        backend = getattr(agent.backend, 'backend_type', None)
        bname = backend.value if backend and hasattr(backend, 'value') else str(backend) if backend else '?'
        def _fmt_tok(n):
            n = int(str(n).strip())
            if n >= 1000:
                return f"{n/1000:.1f}k"
            return str(n)
        _tok_in = getattr(agent, '_running_tokens_in', 0) or _session_tokens_in
        _tok_out = getattr(agent, '_running_tokens_out', 0) or _session_tokens_out
        tok_str = f"\u2191{_fmt_tok(_tok_in)} \u2193{_fmt_tok(_tok_out)}"
        _total_session = _tok_in + _tok_out
        _ctx = agent.num_ctx or 8192
        _ctx_pct = min(100, int((_total_session / _ctx) * 100)) if _ctx > 0 else 0
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

    _FOOTER_LINES = 2
    _is_tty = sys.stdout.isatty()
    _term_size = shutil.get_terminal_size() if _is_tty else None
    _use_persistent_footer = bool(
        _is_tty and _term_size and _term_size.lines >= 6
    )

    def _setup_footer_region():
        if not _use_persistent_footer:
            return
        bottom = _term_size.lines - _FOOTER_LINES
        sys.stdout.write(f"\033[1;{bottom}r")
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.flush()

    def _teardown_footer_region():
        if not _use_persistent_footer:
            return
        sys.stdout.write("\033[r")
        if _term_size:
            for i in range(_FOOTER_LINES):
                row = _term_size.lines - i
                sys.stdout.write(f"\033[{row};1H\033[2K")
            sys.stdout.write(f"\033[{_term_size.lines - _FOOTER_LINES};1H")
        sys.stdout.flush()

    def _update_footer():
        nonlocal _term_size
        if not _use_persistent_footer:
            return
        new_size = shutil.get_terminal_size()
        if (new_size.lines != _term_size.lines or
            new_size.columns != _term_size.columns):
            _term_size = new_size
            bottom = _term_size.lines - _FOOTER_LINES
            sys.stdout.write(f"\033[1;{bottom}r")
            sys.stdout.flush()

        line1 = _footer_line1()
        line2 = _footer_line2()
        sys.stdout.write("\033[s")
        sys.stdout.write("\033[?7l")
        try:
            row1 = _term_size.lines - 1
            sys.stdout.write(f"\033[{row1};1H")
            sys.stdout.write("\033[2K")
            sys.stdout.write(line1)
            row2 = _term_size.lines
            sys.stdout.write(f"\033[{row2};1H")
            sys.stdout.write("\033[2K")
            sys.stdout.write(line2)
        finally:
            sys.stdout.write("\033[?7h")
        sys.stdout.write("\033[u")
        sys.stdout.flush()

    def _position_for_input():
        if not _use_persistent_footer:
            return
        bottom = _term_size.lines - _FOOTER_LINES
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.write("\033[2K")
        sys.stdout.write("\033[?7h")
        sys.stdout.flush()

    # Setup scroll region + register step callback so the footer updates
    # token counts and ctx% during streaming (not just after each step).
    _setup_footer_region()
    agent._on_step_callback = lambda step, tin, tout: _update_footer()

    try:
      while True:
        _update_footer()
        _position_for_input()
        try:
            user_input = input("Goal: ").strip()
        except (EOFError, KeyboardInterrupt):
            # Ensure persistent memory is flushed and closed
            if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'close'):
                agent.memory.close()
            print("\n👋 Goodbye!")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.split()[0]

            if cmd == "/quit":
                if acp:
                    acp.log_chat("user", "/quit")
                    acp.a2a_unregister()
                # Ensure persistent memory is flushed and closed
                if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'close'):
                    agent.memory.close()
                print(bright_cyan("👋 Goodbye!"))
                break
            elif cmd == "/status":
                status = agent_mode.get_status()
                print(f"State: {cyan(status['state'])}")
                if "goal" in status and status["goal"]:
                    print(f"Goal: {bright_yellow(status['goal'])}")
                    if "progress_percent" in status:
                        pct = status['progress_percent']
                        pct_str = green(f"{pct:.0f}%") if pct >= 50 else yellow(f"{pct:.0f}%")
                        print(f"Progress: {pct_str}")
                continue
            elif cmd == "/pause":
                success, msg = agent_mode.pause()
                print(yellow(msg) if success else red(msg))
                continue
            elif cmd == "/resume":
                success, msg = agent_mode.resume()
                print(green(msg) if success else red(msg))
                continue
            elif cmd == "/stop":
                success, msg = agent_mode.stop(rollback=True)
                print(red(msg))
                continue

        # Log goal to ACP
        if acp:
            acp.log_chat("user", f"Goal: {user_input}")

        try:
            success, result = agent_mode.run_task(user_input)
        except KeyboardInterrupt:
            print(f"\n{yellow('Cancelled.')}\n")
            continue
        icon = bright_green("✅") if success else bright_red("❌")
        print(f"\n{icon} {result}\n")

        # Log result to ACP
        if acp:
            acp.log_chat("assistant", f"Result: {result}")
    finally:
        # R06.58: tear down the scroll region on every exit path (quit,
        # EOF, Ctrl+C, unexpected exception) so the terminal is never
        # left in a broken state. Mirrors cmd_chat's try/finally pattern.
        _teardown_footer_region()

    return 0
