"""Session/run header + run-summary printers.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ..agent import Agent
from ..colors import yellow, dim, green, cyan, bright_magenta

from .banner import print_banner



def _print_session_header(agent: Agent, args: argparse.Namespace, config, label: str) -> None:
    """Print the common header shown by chat and agent modes."""
    model = agent.model
    backend_name = args.backend or config.backend
    api_mode = getattr(args, "api_mode", "openre")
    timeout = getattr(args, "timeout", None)

    print_banner()
    print(f"{bright_magenta(label)} — {cyan(model)}")
    print(f"{dim('Backend:')} {backend_name} ({dim(agent.backend.base_url)})")
    print(f"{dim('API Mode:')} {yellow(api_mode)}")
    if agent.soul:
        print(f"{dim('Soul:')} {green(agent.soul.display_name)} v{agent.soul.version}")
    if agent.num_ctx:
        ctx_display = f"{agent.num_ctx // 1024}K" if agent.num_ctx >= 1024 else str(agent.num_ctx)
        print(f"{dim('Context:')} {yellow(ctx_display)}")
    if timeout:
        print(f"{dim('Timeout:')} {yellow(str(timeout) + 's')}")
    acp = getattr(args, '_acp', None)
    if acp:
        print(f"{dim('ACP:')} {green('✓ Connected')} ({acp.base_url})")
    if agent._response_format:
        print(f"{dim('Output:')} {yellow('JSON mode')}")
    if getattr(agent, '_is_persistent', False) and hasattr(agent.memory, 'session_id'):
        print(f"{dim('Session:')} {green(agent.memory.session_id)}")
    print(f"{dim('Status:')} {yellow('Alpha')}")




def _print_run_header(agent: Agent, args: argparse.Namespace, config) -> None:
    """Print a concise info line for the run command."""
    backend_name = args.backend or config.backend
    api_mode = getattr(args, "api_mode", "openre")

    # Resolve effective generation params
    eff_temp = agent._temperature if agent._temperature is not None else agent.model_config.default_temperature
    eff_top_p = agent._top_p if agent._top_p is not None else agent.model_config.default_top_p
    eff_max_tokens = agent._num_predict if agent._num_predict is not None else agent.model_config.default_max_tokens

    parts = [
        f"{cyan(agent.model)}",
        f"{dim('backend=')}{backend_name}",
    ]

    if agent.num_ctx:
        ctx_str = f"{agent.num_ctx // 1024}K" if agent.num_ctx >= 1024 else str(agent.num_ctx)
        parts.append(f"{dim('ctx=')}{yellow(ctx_str)}")

    parts.append(f"{dim('api=')}{api_mode}")

    # Generation params
    params = []
    params.append(f"temp={eff_temp}")
    params.append(f"top_p={eff_top_p}")
    params.append(f"max_tokens={eff_max_tokens}")

    if agent.soul:
        parts.append(f"{dim('soul=')}{green(agent.soul.display_name)}")

    if agent.tools and len(agent.tools) > 0:
        tool_names = ", ".join(agent.tools.names())
        parts.append(f"{dim('tools=[')}{tool_names}{dim(']')}")

    # Print on two lines: main info + params
    line1 = "  ".join(parts)
    line2 = dim("params:") + " " + ", ".join(params)
    print(f"{dim('─') * 60}")
    print(f"  {line1}")
    print(f"  {line2}")
    print(f"{dim('─') * 60}")




def _print_run_summary(result, agent: Agent) -> None:
    """Print a summary line after run completes."""
    steps = result.iterations
    tokens = result.total_tokens
    ms = result.total_ms

    parts = []
    if steps > 0:
        parts.append(f"steps={steps}")
    if tokens > 0:
        parts.append(f"tokens={tokens}")
    if ms > 0:
        parts.append(f"{ms:.0f}ms")

    if parts:
        print(f"  {dim('Completed:')} {', '.join(parts)}")
