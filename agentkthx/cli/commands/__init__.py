"""One module per `agentkthx` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8. Re-exported here so the
facade (agentkthx.cli) and main() can import every handler in one place.
"""

from __future__ import annotations

from .agent import cmd_agent
from .chat import cmd_chat
from .config import cmd_config
from .mcp import cmd_mcp
from .modelfile import cmd_modelfile
from .models import cmd_models
from .plugins import cmd_plugins
from .run import cmd_run
from .sessions import cmd_sessions
from .skills import cmd_skills
from .soul import cmd_soul
from .souls import cmd_souls
from .test import cmd_test
from .tools import cmd_tools
from .turbo import cmd_turbo
from .version import cmd_update, cmd_version

__all__ = [
    "cmd_run",
    "cmd_chat",
    "cmd_agent",
    "cmd_models",
    "cmd_tools",
    "cmd_test",
    "cmd_turbo",
    "cmd_version",
    "cmd_update",
    "cmd_config",
    "cmd_modelfile",
    "cmd_skills",
    "cmd_soul",
    "cmd_souls",
    "cmd_sessions",
    "cmd_plugins",
    "cmd_mcp",
]
