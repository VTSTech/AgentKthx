"""One module per `agentkthx` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8. Re-exported here so the
facade (agentkthx.cli) and main() can import every handler in one place.
"""

from __future__ import annotations

from .run import cmd_run
from .chat import cmd_chat
from .agent import cmd_agent
from .models import cmd_models
from .tools import cmd_tools
from .test import cmd_test
from .turbo import cmd_turbo
from .version import cmd_version, cmd_update
from .config import cmd_config
from .modelfile import cmd_modelfile
from .skills import cmd_skills
from .soul import cmd_soul
from .sessions import cmd_sessions
from .plugins import cmd_plugins

__all__ = [
    "cmd_run", "cmd_chat", "cmd_agent", "cmd_models", "cmd_tools", "cmd_test",
    "cmd_turbo", "cmd_version", "cmd_update", "cmd_config", "cmd_modelfile",
    "cmd_skills", "cmd_soul", "cmd_sessions", "cmd_plugins",
]
