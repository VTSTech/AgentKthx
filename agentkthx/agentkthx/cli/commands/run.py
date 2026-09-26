"""`agentkthx run` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import sys

from ...colors import yellow, dim, red
from ...config import get_config

from ..headers import _print_run_header, _print_run_summary
from ..utils import _print_agent_steps




def cmd_run(args: argparse.Namespace) -> int:
    """Execute the run command."""

    # R07.00: resolve shared collaborators through the cli facade so that
    # monkeypatch.setattr(agentkthx.cli, '<name>', ...) keeps working.
    from agentkthx import cli as _cli

    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _cli._init_acp(args, config, "AgentKthx-Run")
    if should_stop:
        return 1

    agent = _cli._build_agent(args, config)

    # Print run info header
    if not getattr(args, 'quiet', False):
        _print_run_header(agent, args, config)

    try:
        # Enable streaming by default for cloud providers, but respect
        # explicit --stream / --no-stream from the user.
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
        result = agent.run(args.prompt, stream=stream)
    except KeyboardInterrupt:
        print(f"\n{yellow('Cancelled.')}")
        if acp:
            acp.a2a_unregister()
        return 130
    except RuntimeError as e:
        # Surface rate limits / API errors clearly instead of crashing
        print(f"\n{red('Error:')} {e}", file=sys.stderr)
        if "rate limit" in str(e).lower() or "429" in str(e):
            print(f"{red('This appears to be a rate limit error.')}", file=sys.stderr)
        if acp:
            acp.a2a_unregister()
        return 1
    # Print tool-call summary so the user sees what the agent did,
    # not just the final answer. Skipped in quiet mode (debug shows verbose steps).
    if not getattr(args, 'quiet', False):
        _print_agent_steps(result, debug=agent.debug, show_reasoning=getattr(agent, '_show_reasoning', False))

    # Display reasoning_content under the answer when --think is set
    # (only if the model emitted reasoning_content). Same logic as chat mode.
    show_reasoning = getattr(agent, '_show_reasoning', False)
    reasoning_content = ""
    if show_reasoning and result.steps:
        from ...core.types import StepResultType
        for step in reversed(result.steps):
            if step.type == StepResultType.FINAL_ANSWER:
                reasoning_content = getattr(step, 'reasoning_content', '') or ""
                break

    # PERF-01: when streaming, the final answer was already printed by
    # the typewriter effect in _generate_stream(). Don't print it again.
    if stream:
        if reasoning_content:
            print(f"{dim('  reasoning:')}")
            for line in reasoning_content.splitlines():
                if len(line) > 200:
                    line = line[:197] + "..."
                print(f"    {dim(line)}")
            print()
    elif reasoning_content:
        # R06.57: Non-streaming path — show reasoning ABOVE the answer
        # (matching the streaming UX-01 layout and chat mode).
        print(f"{dim('  reasoning:')}")
        for line in reasoning_content.splitlines():
            if len(line) > 200:
                line = line[:197] + "..."
            print(f"    {dim(line)}")
        print()
        print(result.final_answer)
        print()
    else:
        print(result.final_answer)

    # Print run summary (unless quiet)
    if not getattr(args, 'quiet', False):
        _print_run_summary(result, agent)

    # Ensure persistent memory is flushed and closed
    if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'close'):
        agent.memory.close()

    # Log to ACP
    if acp:
        acp.log_chat("assistant", result.final_answer)
        acp.a2a_unregister()

    return 0
