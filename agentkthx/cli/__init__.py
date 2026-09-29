"""
⚛️ AgentKthx — CLI package

Command-line interface for AgentKthx. Split from the original 4270-line
cli.py monolith in R07.00 Phase 8 — pure reorganization, zero behavior
change.

Layout:
    agentkthx.cli.parser         argparse construction (create_parser)
    agentkthx.cli.agent_factory  Agent construction shared by run/chat/agent
    agentkthx.cli.banner         ASCII banner + update-check notice
    agentkthx.cli.headers        session/run header + summary printers
    agentkthx.cli.utils          model resolution, step printing, tool cache
    agentkthx.cli.main           main() — dispatch + plugin wiring
    agentkthx.cli.commands.*     one module per subcommand

This __init__ is a compatibility facade: every module-level name that
existed on the old agentkthx.cli module (all cmd_* handlers, helpers,
constants, main, create_parser) is re-exported here, so
``from agentkthx.cli import X`` and ``monkeypatch.setattr(cli, 'X', ...)``
keep working unchanged.

Patch-compatibility contract: the four collaborators shared across
command modules (_build_agent, _init_acp, _print_session_header,
_print_update_notice) are resolved by command modules through this
facade at call time, so patching ``agentkthx.cli.<name>`` affects all
consumers — exactly as it did pre-split.

Status: Alpha

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

from .. import __version__  # noqa: F401  (was a cli module attribute pre-split)

# ── agent factory ──
from .agent_factory import (
    _build_agent,
    _get_catalog_defaults,
    _init_acp,
    _load_skills_prompt,
    apply_model_switch,
)

# ── banner ──
from .banner import (
    _LAST_UPDATE_CHECK,
    BANNER_ATOM_BRAILLE,
    BANNER_ATOM_PLAIN,
    _print_update_notice,
    _run_update_check,
    print_banner,
)

# ── commands ──
from .commands import (
    cmd_agent,
    cmd_chat,
    cmd_config,
    cmd_modelfile,
    cmd_models,
    cmd_plugins,
    cmd_run,
    cmd_sessions,
    cmd_skills,
    cmd_soul,
    cmd_test,
    cmd_tools,
    cmd_turbo,
    cmd_update,
    cmd_version,
)

# ── config helpers (shared with cmd_config; importable from cli pre-split) ──
from .commands.config import _mask_key, _print_config_summary

# ── headers ──
from .headers import (
    _print_run_header,
    _print_run_summary,
    _print_session_header,
)

# ── entry point ──
from .main import main

# ── parser ──
from .parser import _make_confirm_callback, create_parser

# ── utils ──
from .utils import (
    _get_cache_dir,
    _is_externally_managed_error,
    _print_agent_steps,
    _tool_status,
    resolve_model_pattern,
)

__all__ = [
    "BANNER_ATOM_BRAILLE",
    "BANNER_ATOM_PLAIN",
    "_LAST_UPDATE_CHECK",
    "print_banner",
    "_run_update_check",
    "_print_update_notice",
    "resolve_model_pattern",
    "create_parser",
    "_make_confirm_callback",
    "_init_acp",
    "_load_skills_prompt",
    "_build_agent",
    "_get_catalog_defaults",
    "apply_model_switch",
    "_print_session_header",
    "_print_run_header",
    "_print_run_summary",
    "_print_agent_steps",
    "_get_cache_dir",
    "_tool_status",
    "_is_externally_managed_error",
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
    "cmd_sessions",
    "cmd_plugins",
    "_mask_key",
    "_print_config_summary",
    "main",
]
