"""`agentkthx tools` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import yellow, dim, cyan, bright_green, bright_cyan
from ...tools import make_builtin_registry




def cmd_tools(args: argparse.Namespace) -> int:
    """Execute the tools command."""
    tools = make_builtin_registry()

    print()
    print(f"{bright_cyan('⚛ AgentKthx')} - Available Tools")
    print(dim("-" * 60))

    for tool in tools.all():
        params = ", ".join(p.name for p in tool.params)
        print(f"  {cyan(tool.name):<29} {tool.description[:40]}")
        if params:
            print(f"    {dim('Parameters:')} {yellow(params)}")

    print(dim("-" * 60))
    print(f"Total: {bright_green(str(len(tools.all())))} tools")

    return 0
