"""
⚛️ AgentKthx — CLI
Command-line interface for AgentKthx R06.41.

Status: Alpha

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import threading
from pathlib import Path
from typing import Callable, Optional

from .agent import Agent
from .agent_mode import AgentMode
from .orchestrator import Orchestrator, AgentCard
from .tools import make_builtin_registry
from .backends import get_backend, get_default_backend, get_backend_choices, OllamaBackend
from .config import get_config, AGENTKTHX_BACKEND, OLLAMA_BASE_URL
from . import __version__
from .model_discovery import match_models, get_models
from .core.types import ApiMode
from .shared_args import add_agent_args
from .colors import (
    Color, c, dim, bold, cyan, green, yellow, red, magenta, blue,
    bright_cyan, bright_green, bright_yellow, bright_magenta, bright_red,
    visible_len, pad_colored, is_color_enabled
)


# ============================================================================
# ASCII Banner
# ============================================================================

BANNER_ATOM_BRAILLE = """
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m  \x1b[95;1mAgentKthx\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀\x1b[0m  \x1b[2mAutonomous Agents with Local LLMs\x1b[0m
\x1b[96m⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀\x1b[0m
\x1b[96m⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄\x1b[0m  \x1b[2mStatus:\x1b[0m \x1b[33mAlpha\x1b[0m
\x1b[96m⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇\x1b[0m  \x1b[2mhttps://kthx.vts-tech.org\x1b[0m
\x1b[96m⢸⣿⣿⣿⣧⠀⠀⠀⢸⣟⣠⣾⠟⠋⠀⠙⠻⣶⣄⣿⡄⠀⠀⠀⠀⢀⣾⠃\x1b[0m
\x1b[96m⠘⢿⣿⣿⣏⠀⠀⢀⣼⡿⠋⣠⣶⣿⣿⣿⣦⡌⠙⣿⣧⡀⠀⠀⣠⣾⠋⠀\x1b[0m
\x1b[96m⠀⠀⠀⠈⢻⣦⣴⠟⣹⡇⢰⣿⣿⣿⣿⣿⣿⣿⡄⢸⡟⠻⣦⣴⠟⠁⠀⠀\x1b[0m
\x1b[96m⠀⠀⠀⢀⣴⡟⢿⣦⣿⡗⠸⣿⣿⣿⣿⣿⣿⣿⠃⢸⣇⣴⡿⢿⣦⡀⠀⠀\x1b[0m
\x1b[96m⠀⠀⢠⣾⠋⠀⠀⠙⢿⣧⣄⠙⢿⣿⣿⣿⠿⠃⣠⣿⡟⠁⠀⠀⠙⣷⡄⠀\x1b[0m
\x1b[96m⠀⢠⣿⠁⠀⠀⠀⠀⢸⣯⠛⢷⣦⣀⠀⣠⣴⡿⠋⣿⠃⠀⠀⠀⠀⠘⣿⡄\x1b[0m
\x1b[96m⠀⣾⡇⠀⠀⠀⠀⠀⠈⣿⠀⢀⣩⣿⣿⣿⣅⡀⢠⣿⠀⠀⠀⠀⠀⠀⢸⡇\x1b[0m
\x1b[96m⠀⠹⣷⣄⣀⣀⣀⣠⣤⣿⡿⠟⠋⠁⠀⠈⠙⠻⣿⣷⣤⣄⣀⣀⣀⣠⣾⠇\x1b[0m
\x1b[96m⠀⠀⠈⠉⠛⠛⠛⠉⠉⠘⣿⡀⠀⠀⠀⢀⣴⣶⣿⣄⠈⠉⠙⠛⠛⠋⠁⠀\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣷⡀⠀⠀⢸⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠿⣦⣤⣶⠟⠛⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m
"""

BANNER_ATOM_PLAIN = """
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀  AgentKthx
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀  Autonomous Agents with Local LLMs
⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀
⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄  Status: Alpha
⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇  https://kthx.vts-tech.org
⢸⣿⣿⣿⣧⠀⠀⠀⢸⣟⣠⣾⠟⠋⠀⠙⠻⣶⣄⣿⡄⠀⠀⠀⠀⢀⣾⠃
⠘⢿⣿⣿⣏⠀⠀⢀⣼⡿⠋⣠⣶⣿⣿⣿⣦⡌⠙⣿⣧⡀⠀⠀⣠⣾⠋⠀
⠀⠀⠀⠈⢻⣦⣴⠟⣹⡇⢰⣿⣿⣿⣿⣿⣿⣿⡄⢸⡟⠻⣦⣴⠟⠁⠀⠀
⠀⠀⠀⢀⣴⡟⢿⣦⣿⡗⠸⣿⣿⣿⣿⣿⣿⣿⠃⢸⣇⣴⡿⢿⣦⡀⠀⠀
⠀⠀⢠⣾⠋⠀⠀⠙⢿⣧⣄⠙⢿⣿⣿⣿⠿⠃⣠⣿⡟⠁⠀⠀⠙⣷⡄⠀
⠀⢠⣿⠁⠀⠀⠀⠀⢸⣯⠛⢷⣦⣀⠀⣠⣴⡿⠋⣿⠃⠀⠀⠀⠀⠘⣿⡄
⠀⣾⡇⠀⠀⠀⠀⠀⠈⣿⠀⢀⣩⣿⣿⣿⣅⡀⢠⣿⠀⠀⠀⠀⠀⠀⢸⡇
⠀⠹⣷⣄⣀⣀⣀⣠⣤⣿⡿⠟⠋⠁⠀⠈⠙⠻⣿⣷⣤⣄⣀⣀⣀⣠⣾⠇
⠀⠀⠈⠉⠛⠛⠛⠉⠉⠘⣿⡀⠀⠀⠀⢀⣴⣶⣿⣄⠈⠉⠙⠛⠛⠋⠁⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⣷⡀⠀⠀⢸⣿⣿⣿⣿⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠿⣦⣤⣶⠟⠛⠛⠁⠀⠀⠀⠀⠀⠀⠀⠀
"""


def print_banner() -> None:
    """Print the AgentKthx ASCII banner."""
    from . import __version__, __status__
    # Convert 0.3.3 to R03.3 format for display
    parts = __version__.split('.')
    display_version = f"R{int(parts[1]):02d}.{parts[2]}" if len(parts) >= 2 else __version__    
    version_str = f"{display_version} [{__status__}]"
    if is_color_enabled():
        # Replace ANSI-colored "Status: Alpha" with version
        banner = BANNER_ATOM_BRAILLE.replace("\x1b[2mStatus:\x1b[0m \x1b[33mAlpha\x1b[0m", f"\x1b[2m{version_str}\x1b[0m")
        print(banner)
    else:
        banner = BANNER_ATOM_PLAIN.replace("Status: Alpha", version_str)
        print(banner)


# ============================================================================
# Update Check (pip-style "new release available" notice)
# ============================================================================

# Result of the daily-cached PyPI update check for this process, stashed by
# main() so the notice can be printed under the chat banner (cmd_chat) and
# after non-interactive commands (post-run) without hitting the network twice.
_LAST_UPDATE_CHECK = None


def _run_update_check(timeout: float = 1.0) -> None:
    """Run the daily-cached update check once; stash the result. Never raises."""
    global _LAST_UPDATE_CHECK
    try:
        from .update_check import check_for_update
        _LAST_UPDATE_CHECK = check_for_update(timeout=timeout)
    except Exception:
        _LAST_UPDATE_CHECK = None


def _print_update_notice() -> None:
    """Print the pip-style 'new release' notice if a newer version is on PyPI."""
    result = _LAST_UPDATE_CHECK
    if not result:
        return
    try:
        from .update_check import format_notice
        text = format_notice(result, current=__version__)
    except Exception:
        return
    if text:
        for line in text.splitlines():
            print(dim(line))


# ============================================================================
# Model Matching
# ============================================================================

def resolve_model_pattern(
    pattern: str,
    backend_name: str = "ollama",
    allow_multiple: bool = False,
) -> str | list[str]:
    """
    Resolve a model pattern to actual model name(s).
    
    Shows helpful output when multiple models match.
    
    Parameters
    ----------
    pattern : str
        Model name or pattern (e.g., "qwen", "g", ":0.5b")
    backend_name : str
        Backend to use for model discovery
    allow_multiple : bool
        If True, return all matches; if False, return first match
    
    Returns
    -------
    str or list[str]
        Resolved model name(s), or empty list if no matches
    """
    backend = get_backend(backend_name)
    matches = match_models(pattern, backend=backend)
    
    if not matches:
        print(f"{red('Error:')} No models found matching '{pattern}'")
        available = get_models(client=backend)
        if available:
            print(f"\n{dim('Available models:')}")
            for m in sorted(available)[:10]:
                print(f"  {cyan(m)}")
            if len(available) > 10:
                print(f"  {dim(f'... and {len(available) - 10} more')}")
        return [] if allow_multiple else ""
    
    # If allow_multiple, always return a list
    if allow_multiple:
        return matches
    
    # Single match - return as string
    if len(matches) == 1:
        return matches[0]
    
    # Multiple matches - show and use first
    print(f"{yellow('Multiple models match')} '{pattern}':")
    for i, m in enumerate(matches[:5]):
        marker = green("→") if i == 0 else " "
        print(f"  {marker} {cyan(m)}")
    if len(matches) > 5:
        print(f"    {dim(f'... and {len(matches) - 5} more')}")
    print(f"{dim('Using first match:')} {cyan(matches[0])}")
    return matches[0]


# ============================================================================
# CLI Commands
# ============================================================================


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="agentkthx",
        description="⚛️ AgentKthx - Autonomous agents with local LLMs (Alpha)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    # Stash the _SubParsersAction so plugin CLI commands can be added later in main()
    parser._subparsers_action = subparsers

    # Agent command
    agent_parser = subparsers.add_parser("agent", help="Autonomous agent mode")
    add_agent_args(agent_parser, tools_default="calculator,shell,write_file")

    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Interactive chat mode")
    add_agent_args(chat_parser, tools_default="")

    # Config command
    config_parser = subparsers.add_parser("config", help="Show current configuration")
    config_parser.add_argument("--urls", action="store_true", help="Show only backend URLs")
    config_parser.add_argument("--full", action="store_true", help="Show all configuration variables including dataclass defaults")

    # Models command
    models_parser = subparsers.add_parser("models", help="List available models")
    models_parser.add_argument("--backend", choices=get_backend_choices(), default=None, help="Backend to use")
    models_parser.add_argument("--api", choices=["openre", "openai", "jev"], default=None, dest="api_mode",
                           help="API mode for tool support testing (default: test both openre/openai; 'jev' uses System-One decision mode)")
    models_parser.add_argument("--tool-support", action="store_true", help="Test tool calling support (skips already-cached models)")
    models_parser.add_argument("--no-cache", action="store_true", help="Ignore cached results and re-test all models")
    models_parser.add_argument("--acp", action="store_true", help="Enable ACP logging to Agent Control Panel")
    models_parser.add_argument("--acp-url", default=None, help="ACP server URL (default: from config)")

    # Modelfile command
    modelfile_parser = subparsers.add_parser("modelfile", help="Show model's Modelfile info")
    modelfile_parser.add_argument("-m", "--model", default=None, help="Model to inspect")
    modelfile_parser.add_argument("--backend", choices=get_backend_choices(), default=None, help="Backend to use")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a single prompt")
    run_parser.add_argument("prompt", help="The prompt to process")
    add_agent_args(run_parser, tools_default="calculator")
    # --stream / --no-stream now come from add_agent_args() (shared_args.py)
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    run_parser.add_argument("-q", "--quiet", action="store_true", help="Suppress header and summary")

    # Sessions command
    sessions_parser = subparsers.add_parser("sessions", help="List and manage saved sessions")
    sessions_parser.add_argument(
        "--delete",
        metavar="SESSION_ID",
        default=None,
        help="Delete a specific session by ID",
    )

    # Skills command
    subparsers.add_parser("skills", help="List available skills")
    
    # Soul command
    soul_parser = subparsers.add_parser("soul", help="Inspect a Soul Spec package")
    soul_parser.add_argument("path", help="Path to soul package directory or soul.json")
    soul_parser.add_argument("--level", type=int, default=2, choices=[1, 2, 3],
                            help="Progressive disclosure level (1=quick, 2=full, 3=deep)")
    soul_parser.add_argument("--validate", action="store_true", help="Run validation checks")
    soul_parser.add_argument("--prompt", action="store_true", help="Show generated system prompt")

    # Test command
    test_parser = subparsers.add_parser("test", help="Run diagnostic tests")
    test_parser.add_argument("test_id", nargs="?", default="all", 
                             help="Test to run: 00, 01, 02, ... 11, or 'all' (default: all)")
    test_parser.add_argument("-m", "--model", default=None, 
                             help="Model to test (supports patterns: 'qwen', 'g', ':0.5b')")
    test_parser.add_argument("--backend", choices=get_backend_choices(), default=None, help="Backend to use")
    test_parser.add_argument("--api", choices=["openre", "openai", "jev"], default="openre", dest="api_mode",
                           help="API mode: 'openre' (OpenResponses), 'openai' (Chat-Completions), or 'jev' (System-One decision mode)")
    test_parser.add_argument("--debug", action="store_true", help="Enable debug output")
    test_parser.add_argument("--list", action="store_true", help="List available tests")
    test_parser.add_argument("--acp", action="store_true", help="Enable ACP logging to Agent Control Panel")
    test_parser.add_argument("--acp-url", default=None, help="ACP server URL (default: http://localhost:8766)")
    test_parser.add_argument("--use-mf-sys", action="store_true", dest="use_modelfile_system",
                             help="Use the model's Modelfile system prompt instead of custom test prompts")
    test_parser.add_argument("--num-ctx", type=int, default=None, dest="num_ctx",
                           help="Context window size in tokens (Ollama default is 2048)")
    test_parser.add_argument("--num-predict", type=int, default=None, dest="num_predict",
                           help="Maximum tokens to generate (default: model-specific)")
    test_parser.add_argument("--temp", "--temperature", type=float, default=None, dest="temperature",
                           help="Sampling temperature 0.0-2.0 (default: model-specific)")
    test_parser.add_argument("--top-p", type=float, default=None, dest="top_p",
                           help="Nucleus sampling probability 0.0-1.0 (default: model-specific)")
    test_parser.add_argument("--timeout", type=int, default=None,
                           help="Request timeout in seconds (default: 120)")
    test_parser.add_argument("--warmup", action="store_true",
                           help="Send warmup request before testing (avoids cold start timeout)")
    test_parser.add_argument("--force-react", action="store_true", help="Force ReAct mode for tool calling")
    test_parser.add_argument("--soul", default=None, help="Path to Soul Spec package (disabled by default)")
    test_parser.add_argument("--soul-level", type=int, default=2, choices=[1, 2, 3],
                           help="Soul progressive disclosure level (1=quick, 2=full, 3=deep)")
    test_parser.add_argument("--tools-only", action="store_true", dest="tools_only",
                           help="Only run Phase 1 (direct tool tests, no model)")
    test_parser.add_argument("--model-only", action="store_true", dest="model_only",
                           help="Only run Phase 2 (model tool calling tests)")
    test_parser.add_argument("--quick", action="store_true",
                           help="Quick mode: only run 5 fastest tests per test module")

    # Turbo command
    turbo_parser = subparsers.add_parser("turbo", help="TurboQuant server management (start/stop/list Ollama models)")
    turbo_sub = turbo_parser.add_subparsers(dest="turbo_command", help="TurboQuant subcommand")

    # turbo list
    turbo_list_parser = turbo_sub.add_parser("list", help="List Ollama models available for TurboQuant")
    turbo_list_parser.add_argument("--all", action="store_true", help="Show all models, including missing blobs")
    turbo_list_parser.add_argument("--ollama-dir", default=None, help="Override Ollama models directory")

    # turbo start
    turbo_start_parser = turbo_sub.add_parser("start", help="Start TurboQuant server with an Ollama model")
    turbo_start_parser.add_argument("model", help="Ollama model name (e.g. qwen2.5:7b) or path to GGUF file")
    turbo_start_parser.add_argument("--server", default=None, help="Path to llama-server binary (env: TURBOQUANT_SERVER_PATH)")
    turbo_start_parser.add_argument("--port", type=int, default=None, help=f"Server port (default: {os.environ.get('TURBOQUANT_PORT', '8764')})")
    turbo_start_parser.add_argument("--ctx", type=int, default=None, help=f"Context window (default: {os.environ.get('TURBOQUANT_CTX', '8192')})")
    turbo_start_parser.add_argument("--turbo-k", default=None, choices=["q8_0", "q4_0", "turbo2", "turbo3", "turbo4", "f16"], help="K cache type (default: auto-detected)")
    turbo_start_parser.add_argument("--turbo-v", default=None, choices=["q8_0", "q4_0", "turbo2", "turbo3", "turbo4", "f16"], help="V cache type (default: auto-detected)")
    turbo_start_parser.add_argument("--flash-attn", action="store_true", help="Enable flash attention (-fa)")
    turbo_start_parser.add_argument("--sparsity", type=float, default=0.0, help="Sparse V decoding threshold (0.0=off)")
    turbo_start_parser.add_argument("--threads", type=int, default=0, help="CPU thread count (0=auto)")
    turbo_start_parser.add_argument("--no-wait", action="store_true", help="Don't wait for server to be ready")
    turbo_start_parser.add_argument("--timeout", type=int, default=120, help="Max seconds to wait for readiness (default: 120)")
    turbo_start_parser.add_argument("--", dest="extra_args", nargs="*", help="Extra arguments to pass to llama-server")

    # turbo stop
    turbo_stop_parser = turbo_sub.add_parser("stop", help="Stop the running TurboQuant server")
    turbo_stop_parser.add_argument("--force", action="store_true", help="Force kill (SIGKILL)")

    # turbo status
    turbo_sub.add_parser("status", help="Show TurboQuant server status")

    # Tools command
    subparsers.add_parser("tools", help="List available tools")

    # Plugins command (v0.2 spec §CLI integration)
    plugins_parser = subparsers.add_parser("plugins", help="List and manage plugins")
    plugins_parser.add_argument("--verbose", action="store_true", help="Show detailed plugin info")
    plugins_parser.add_argument("--load", metavar="NAME", help="Load a single plugin by name")
    plugins_parser.add_argument("--unload", metavar="NAME", help="Unload a loaded plugin")
    plugins_parser.add_argument("--reload", metavar="NAME", help="Unload then load a plugin")
    plugins_parser.add_argument("--json", action="store_true", help="Machine-readable listing")

    # Update command
    subparsers.add_parser("update", help="Update AgentKthx to the latest version from GitHub")

    # Version command
    subparsers.add_parser("version", help="Show version information")

    return parser


def _make_confirm_callback(args: argparse.Namespace):
    """
    Build a confirm_dangerous callback from CLI --confirm flag.
    
    When --confirm is set, the user is prompted (y/n) before any
    dangerous tool (shell, write_file, edit_file) executes.
    Returns None if --confirm is not set (no confirmation needed).
    """
    if not getattr(args, 'confirm_dangerous', False):
        return None

    from .colors import yellow, dim, green, red

    def _confirm(tool_name: str, args_dict: dict) -> bool:
        # Format the args for display
        arg_str = "  ".join(f"{k}={v}" for k, v in args_dict.items())
        # Truncate very long values (e.g. file content)
        if len(arg_str) > 200:
            arg_str = arg_str[:200] + "..."
        print(f"\n{yellow('⚠')}  Dangerous tool: {yellow(tool_name)}")
        print(f"{dim('  ' + arg_str)}")
        try:
            import readline
            choice = input(f"  {dim('Execute?')} [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(f"  {red('Blocked.')}")
            return False
        if choice == "y":
            print(f"  {green('Allowed.')}")
            return True
        else:
            print(f"  {red('Blocked.')}")
            return False

    return _confirm


def _init_acp(args: argparse.Namespace, config, agent_name: str = "AgentKthx") -> tuple:
    """
    Initialize ACP plugin if requested.
    
    Returns:
        tuple: (acp_plugin or None, should_stop bool)
    """
    if not getattr(args, 'acp', False):
        return None, False
    
    try:
        from .plugins.acp.acp_plugin import ACPPlugin
        acp_url = getattr(args, 'acp_url', None) or config.acp_base_url
        acp = ACPPlugin(
            base_url=acp_url,
            agent_name=agent_name,
            model_name=getattr(args, 'model', None) or config.default_model,
            debug=getattr(args, 'debug', False),
        )
        # Bootstrap ACP connection
        bootstrap_result = acp.bootstrap()
        if bootstrap_result.get("stop_flag"):
            print(f"{red('Error:')} ACP STOP flag is set: {bootstrap_result.get('warnings')}")
            return None, True
        return acp, False
    except ImportError:
        print(f"{yellow('Warning:')} ACP plugin not available, continuing without ACP logging")
        return None, False
    except Exception as e:
        print(f"{yellow('Warning:')} Failed to connect to ACP: {e}")
        return None, False


def _load_skills_prompt(args: argparse.Namespace) -> tuple[str | None, list[str]]:
    """
    Load skills specified via --skills flag.

    Returns:
        Tuple of (system_prompt_addition, loaded_skill_names).
        Prompt is None if no skills specified or all failed to load.
        loaded_skill_names is the list of skill names that loaded OK
        (used by /skills and /status slash commands in chat mode).
    """
    skills_str = getattr(args, 'skills', None)
    if not skills_str:
        return (None, [])

    try:
        from .skills import SkillLoader, SkillRegistry
    except ImportError:
        print(f"{yellow('Warning:')} Skills module not available, skipping --skills")
        return (None, [])

    loader = SkillLoader()
    registry = SkillRegistry()
    skill_names = [s.strip() for s in skills_str.split(",") if s.strip()]

    loaded = []
    failed = []
    for name in skill_names:
        try:
            skill = loader.load(name)
            registry.add(skill)
            loaded.append(name)
        except FileNotFoundError:
            failed.append(name)
            print(f"{yellow('Warning:')} Skill '{name}' not found (run 'agentkthx skills' to list available)")
        except Exception as e:
            failed.append(name)
            print(f"{yellow('Warning:')} Failed to load skill '{name}': {e}")

    if loaded:
        prompt = registry.to_system_prompt_addition()
        if prompt:
            return (prompt, loaded)

    return (None, loaded)


def _build_agent(args: argparse.Namespace, config) -> Agent:
    """Build an Agent from parsed CLI args and config.

    Centralises the ~20-parameter Agent construction that was previously
    duplicated in cmd_run, cmd_chat, and cmd_agent.  Every new CLI flag
    only needs to be added here (and in add_agent_args).
    """
    # Apply security mode from --security flag (default "max").
    # The /security slash command can change this at runtime.
    from .core.helpers import set_security_mode
    security_mode = getattr(args, "security", "max") or "max"
    set_security_mode(security_mode)

    backend_name = args.backend or config.backend
    
    # Set default timeout and API mode first
    timeout = getattr(args, "timeout", None)
    api_mode = getattr(args, "api_mode", "openre")
    
    # When --backend bitnet is used without --model, discover the actual
    # model name from the server via list_models() (/props endpoint).
    # This ensures correct family config resolution (stop tokens, prompt
    # format) instead of falling back to generic "bitnet" with no family.
    if args.model:
        model = args.model
    elif backend_name == "bitnet":
        # Initialize backend temporarily for model discovery
        temp_backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
        discovered = temp_backend.list_models()
        if discovered and discovered[0].get("name") and discovered[0]["name"] != "bitnet":
            model = discovered[0]["name"]
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"  [bitnet] Discovered model: {model}")
        else:
            model = "bitnet"
    else:
        model = config.default_model

    # Initialize backend with proper API mode
    backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    # Default API mode: cloud providers use OpenAI, local providers use OpenResponses
    from .core.types import BackendType
    if hasattr(backend, 'backend_type') and backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]:
        api_mode = getattr(args, "api_mode", "openai")
        # Re-initialize backend with correct API mode for cloud providers
        backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    # Handle truncation configuration
    truncation = getattr(args, "truncation", "auto")
    if args.debug:
        print(f"[AgentKthx] Truncation mode: {truncation}")
        print(f"[AgentKthx] API mode: {api_mode}")

    # Parse compaction threshold (--compaction auto=85% / off / N)
    compaction_arg = getattr(args, "compaction", "auto")
    if compaction_arg in ("off", "0", "disabled"):
        compaction_threshold = 1.0  # never compact (100% = always under)
    elif compaction_arg == "auto" or compaction_arg is None:
        compaction_threshold = 0.85
    else:
        try:
            pct = float(compaction_arg)
            compaction_threshold = pct / 100.0 if pct > 1.0 else pct
        except (ValueError, TypeError):
            compaction_threshold = 0.85  # fallback to auto
    if args.debug:
        print(f"[AgentKthx] Compaction: {int(compaction_threshold * 100)}% of num_ctx")

    # Build tools
    # In JEV mode, tools are irrelevant — decisions never call tools
    # (generate_decision() always passes tools=None to the backend).
    # Suppress tool loading to avoid confusing the model and wasting
    # the system prompt slot on tool definitions.
    if api_mode == "jev":
        tools = None
        if args.debug and args.tools:
            print(f"[AgentKthx] JEV mode — suppressing tools (decisions don't use them)")
    elif args.tools:
        all_tools = make_builtin_registry()
        tool_names = [t.strip() for t in args.tools.split(",")]
        tools = all_tools.subset(tool_names)
    else:
        tools = None

    # Resolve --response-format CLI arg to Agent parameter
    # "json" → {"type": "json_object"}, "text" → None (default)
    cli_rf = getattr(args, "response_format", "text")
    if cli_rf == "json":
        response_format = {"type": "json_object"}
    else:
        response_format = None

    # Load skills if requested
    skills_prompt, loaded_skills = _load_skills_prompt(args)

    # Get catalog defaults for cloud providers
    catalog_defaults = _get_catalog_defaults(backend, model)
    
    # Apply catalog defaults only if user didn't specify explicit values
    final_num_ctx = (
        getattr(args, "num_ctx", None)
        if getattr(args, "num_ctx", None) is not None
        else catalog_defaults.get('num_ctx') or config.num_ctx
    )
    
    final_num_predict = (
        getattr(args, "num_predict", None)
        if getattr(args, "num_predict", None) is not None
        else catalog_defaults.get('num_predict')
    )
    
    # Enable streaming by default for cloud providers
    from .core.types import BackendType
    default_stream = (
        hasattr(backend, 'backend_type') and 
        backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]
    )
    
    # Resolve --thinking CLI arg to (think, reasoning_effort)
    # off  → (False, None)   — disable thinking entirely (fastest, best for JEV)
    # auto → (None, None)    — let model decide (default)
    # low/medium/high → (True, "<level>")  — pass reasoning_effort for o-series/GLM-5
    from .core.types import parse_thinking_arg
    thinking_level = getattr(args, "thinking_level", "auto")
    think_param, reasoning_effort = parse_thinking_arg(thinking_level)

    # --think flag controls DISPLAY of reasoning_content in CLI output
    show_reasoning = getattr(args, "show_reasoning", False)

    agent = Agent(
        model=model,
        tools=tools,
        backend=backend,
        force_react=args.force_react,
        debug=args.debug,
        soul=getattr(args, "soul", None),
        soul_level=getattr(args, "soul_level", 2),
        num_ctx=final_num_ctx,
        temperature=getattr(args, "temperature", None),
        top_p=getattr(args, "top_p", None),
        num_predict=final_num_predict,
        skills_prompt=skills_prompt,
        retry_on_error=not getattr(args, "no_retry", False),
        max_tool_retries=getattr(args, "max_tool_retries", None) or config.max_tool_retries,
        confirm_dangerous=_make_confirm_callback(args),
        response_format=response_format,
        session_id=getattr(args, "session", None),
        truncation=truncation,
        max_steps=getattr(args, "max_steps", 25),
        # Thinking controls
        thinking_level=thinking_level,
        think=think_param,
        reasoning_effort=reasoning_effort,
        show_reasoning=show_reasoning,
    )
    # Stash loaded skill names on the agent so /skills and /status can show them
    # (Agent itself doesn't track skill names — only the prompt gets injected)
    agent._loaded_skills = loaded_skills
    # Set compaction threshold from --compaction arg
    agent._compaction_threshold = compaction_threshold
    return agent


def _get_catalog_defaults(backend, model: str) -> dict:
    """
    Get model defaults from backend catalog if available.
    
    Returns:
        dict: num_ctx and num_predict defaults from catalog
    """
    from .core.types import BackendType
    
    # Only apply catalog defaults for cloud providers
    if not hasattr(backend, 'backend_type') or backend.backend_type not in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]:
        return {}
    
    try:
        family = None
        if hasattr(backend, 'get_model_info'):
            model_info = backend.get_model_info(model)
            if model_info and 'details' in model_info:
                family = model_info['details'].get('family')
        
        # Get context length and max tokens from catalog
        max_ctx = backend.get_model_max_context(model, family=family)
        
        defaults = {
            'num_ctx': max_ctx,
            'num_predict': None,  # Will be handled by _get_model_defaults
        }
        
        # Try to get max tokens if the backend supports it
        if hasattr(backend, '_get_model_defaults'):
            try:
                model_defaults = backend._get_model_defaults(model)
                defaults['num_predict'] = model_defaults.get('max_tokens', 4096)
            except Exception:
                # Fallback to reasonable defaults
                defaults['num_predict'] = 4096
        
        return defaults
    except Exception:
        # If catalog lookup fails, return empty dict
        return {}

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


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the run command."""
    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _init_acp(args, config, "AgentKthx-Run")
    if should_stop:
        return 1

    agent = _build_agent(args, config)

    # Print run info header
    if not getattr(args, 'quiet', False):
        _print_run_header(agent, args, config)

    try:
        # Enable streaming by default for cloud providers, but respect
        # explicit --stream / --no-stream from the user.
        from .core.types import BackendType
        is_cloud_provider = (
            hasattr(agent.backend, 'backend_type') and 
            agent.backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]
        )
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
        from .core.types import StepResultType
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
        print(result.final_answer)
        print(f"{dim('  reasoning:')}")
        for line in reasoning_content.splitlines():
            if len(line) > 200:
                line = line[:197] + "..."
            print(f"    {dim(line)}")
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


def _print_agent_steps(result, debug: bool = False, show_reasoning: bool = False) -> None:
    """Print a brief summary of each agent step (tool calls + results).

    Visible by default in chat mode so the user can see what the agent is
    doing, not just the final answer. Suppressed when debug is on
    (debug already prints verbose step-by-step output).

    Args:
        result: AgentRun returned by agent.run()
        debug: If True, the agent already printed verbose step output —
               skip the summary to avoid duplication.
        show_reasoning: If True, also print the model's reasoning_content
               (chain-of-thought) under each step. Set by the --think flag.
    """
    from .core.types import StepResultType

    # In debug mode the agent already printed verbose step output.
    if debug:
        return

    # Show all steps, not just tool calls
    if not result.steps:
        return

    # Skip step summary if there's only 1 step (just the final answer)
    if len(result.steps) == 1:
        return

    print()  # blank line before step summary
    for i, step in enumerate(result.steps, 1):
        if step.type == StepResultType.TOOL_CALL and step.tool_call:
            name = step.tool_call.name
            args = step.tool_call.arguments or {}
            # Compact one-line arg preview
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = str(args)
            if len(args_str) > 120:
                args_str = args_str[:117] + "..."

            # Truncate result for display
            result_str = str(step.tool_result) if step.tool_result is not None else ""
            if len(result_str) > 200:
                result_str = result_str[:197] + "..."

            print(f"  {dim(f'[{i}]')} {cyan('tool')} {yellow(name)}"
                  f" {dim(args_str)}")
            if result_str:
                print(f"      {dim('→')} {dim(result_str)}")
        elif step.type == StepResultType.FINAL_ANSWER:
            content = step.content[:100] + "..." if len(step.content) > 100 else step.content
            print(f"  {dim(f'[{i}]')} {cyan('answer')} {dim(content)}")
        elif step.type == StepResultType.ERROR:
            error_msg = step.error[:100] + "..." if step.error and len(step.error) > 100 else step.error or "Error"
            print(f"  {dim(f'[{i}]')} {red('error')} {dim(error_msg)}")
        elif step.type == StepResultType.MAX_STEPS:
            content = step.content[:100] + "..." if step.content and len(step.content) > 100 else step.content or "Max steps reached"
            print(f"  {dim(f'[{i}]')} {yellow('max-steps')} {dim(content)}")

        # R05.8: Display reasoning_content (chain-of-thought) when --think is set.
        # The model emits this before its final answer on thinking-capable
        # backends (GLM-4.5+, o-series, deepseek-r1, qwen3 in thinking mode).
        if show_reasoning and getattr(step, 'reasoning_content', ''):
            rc = step.reasoning_content
            # Indent and dim the reasoning so it's visually distinct from
            # the actual step content.
            print(f"      {dim('reasoning:')}")
            for line in rc.splitlines():
                # Truncate very long lines for terminal display
                if len(line) > 200:
                    line = line[:197] + "..."
                print(f"        {dim(line)}")
    print()  # blank line before final answer


def cmd_chat(args: argparse.Namespace) -> int:
    """Execute the chat command."""
    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _init_acp(args, config, "AgentKthx-Chat")
    if should_stop:
        return 1

    agent = _build_agent(args, config)

    _print_session_header(agent, args, config, "Chat Mode")
    print("Type '/quit' to exit, '/help' for commands\n")

    # Update notice under the banner — printed BEFORE the persistent footer
    # takes over the bottom of the terminal (see _update_footer scroll regions).
    _print_update_notice()

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
            print(f"  {cyan('/model')}      Show or change the model (e.g. /model glm-4.7-flash)")
            print(f"  {cyan('/param')}      Show or set generation parameters (temp, top_p, top_k, etc.)")
            print(f"  {cyan('/security')}   Show or set security mode (max|off)")
            print(f"  {cyan('/skills')}     Show loaded skills (and available skills if none loaded)")
            print(f"  {cyan('/status')}     Show model, backend, tools, skills, and memory info")
            print(f"  {cyan('/system')}     Print the current system prompt")
            print(f"  {cyan('/tools')}      List available tools with descriptions")
            print(f"  {cyan('/quit')}       Exit AgentKthx")
            continue

        if user_input == "/security" or user_input.startswith("/security "):
            from .core.helpers import get_security_mode, set_security_mode
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
            tools = agent.tools.all()
            if not tools:
                print(yellow("No tools loaded."))
            else:
                for t in tools:
                    desc = t.description.split('.')[0] if t.description else 'No description'
                    if len(desc) > 60:
                        desc = desc[:57] + '...'
                    print(f"  {cyan(t.name)}  {desc}")
            continue

        if user_input == "/skills":
            loaded = getattr(agent, '_loaded_skills', [])
            if not loaded:
                print(yellow("No skills loaded."))
                print(dim(f"  Use --skills <name1,name2> at startup, e.g."))
                print(dim(f"  agentkthx chat --skills codebase-audit --tools shell,read_file,write_file"))
                # Also show available skills (read-only, doesn't load them)
                try:
                    from .skills import SkillLoader
                    loader = SkillLoader()
                    available = loader.list_skills()
                    if available:
                        print()
                        print(dim(f"  Available skills:"))
                        for name in available:
                            print(f"    {magenta(name)}")
                except Exception:
                    pass
            else:
                print(f"{bold('Loaded skills:')}")
                try:
                    from .skills import SkillLoader
                    loader = SkillLoader()
                    for name in loaded:
                        try:
                            skill = loader.load(name)
                            desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
                            print(f"  {magenta(name):<20} {desc}")
                        except Exception as e:
                            print(f"  {magenta(name):<20} {red(f'Error: {e}')}")
                except Exception:
                    # Fallback if skills module unavailable — just show names
                    for name in loaded:
                        print(f"  {magenta(name)}")
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
            from .core.types import BackendType

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
            from .core.helpers import get_security_mode
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
        from .core.types import BackendType
        _is_cloud = (
            hasattr(agent.backend, 'backend_type') and
            agent.backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]
        )
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
            from .core.types import BackendType
            is_cloud_provider = (
                hasattr(agent.backend, 'backend_type') and
                agent.backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI, BackendType.GEMINI]
            )
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
                from .core.types import StepResultType
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
                # Non-streaming path — reasoning wasn't displayed inline,
                # so show it as a panel under the answer (original behavior).
                print(f"\n{bright_green('AgentKthx')}: {result.final_answer}")
                print(f"{dim('  reasoning:')}")
                for line in reasoning_content.splitlines():
                    if len(line) > 200:
                        line = line[:197] + "..."
                    print(f"    {dim(line)}")
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


def cmd_agent(args: argparse.Namespace) -> int:
    """Execute the agent command."""
    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _init_acp(args, config, "AgentKthx-Agent")
    if should_stop:
        return 1

    agent = _build_agent(args, config)
    agent_mode = AgentMode(agent, verbose=True)

    _print_session_header(agent, args, config, "Agent Mode")
    print("Give the agent a goal to accomplish autonomously.")
    print(f"Commands: {cyan('/status')}, {cyan('/pause')}, {cyan('/resume')}, {cyan('/stop')}, {cyan('/quit')}\n")

    while True:
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

    return 0


def _get_cache_dir() -> Path:
    """Get the cache directory for AgentKthx."""
    # Use platform-appropriate cache directory
    if os.name == "nt":
        # Windows: %LOCALAPPDATA%\agentkthx\cache
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        cache_dir = Path(base) / "agentkthx" / "cache"
    else:
        # Unix: ~/.cache/agentkthx
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        cache_dir = Path(base) / "agentkthx"
    
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _load_tool_cache() -> dict:
    """Load cached tool support results."""
    cache_file = _get_cache_dir() / "tool_support.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
                # Validate it's a dict
                if isinstance(data, dict):
                    return data
                # Corrupted - not a dict
                if os.environ.get("AGENTKTHX_DEBUG"):
                    print(f"Warning: Cache file corrupted (not a dict), ignoring", file=sys.stderr)
                return {}
        except json.JSONDecodeError as e:
            # Corrupted JSON - warn in debug mode
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"Warning: Cache file has invalid JSON: {e}", file=sys.stderr)
            # Try to remove corrupted file
            try:
                cache_file.unlink()
            except Exception:
                pass
            return {}
        except IOError as e:
            if os.environ.get("AGENTKTHX_DEBUG"):
                print(f"Warning: Could not read cache file: {e}", file=sys.stderr)
    return {}


def _save_tool_cache(cache: dict) -> None:
    """Save tool support results to cache using atomic writes."""
    import tempfile
    
    cache_dir = _get_cache_dir()
    cache_file = cache_dir / "tool_support.json"
    
    try:
        # Write to a temp file first, then rename for atomicity
        # This prevents partial writes if the process is interrupted
        fd, temp_path = tempfile.mkstemp(
            dir=str(cache_dir),
            prefix=".tool_support_",
            suffix=".json.tmp"
        )
        
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(cache, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            # Atomic rename (on POSIX systems)
            os.replace(temp_path, str(cache_file))
        except Exception:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
            
    except IOError as e:
        # Log the error but don't fail - cache is optional
        print(f"Warning: Could not save tool cache: {e}", file=sys.stderr)


def _get_cloud_model_size(model_name: str, backend) -> int:
    """Get model size for cloud providers when available."""
    try:
        # Try to get model info from the backend
        model_info = backend.get_model_info(model_name)
        if model_info and model_info.get("size", 0) > 0:
            return model_info["size"]
        
        # For cloud providers that don't provide size, return 0 (unknown)
        return 0
    except Exception:
        # If we can't determine the size, return 0 (unknown)
        return 0


def _tool_status(status: str) -> str:
    """Format a tool support status with color."""
    if status == "native":
        return bright_green("✓ native")
    elif status == "react":
        return yellow("○ react")
    elif status == "none":
        return red("✗ none")
    elif status == "error":
        return red("✗ error")
    return dim("? untested")


def cmd_models(args: argparse.Namespace) -> int:
    """Execute the models command."""
    from .core.tool_cache import cache_tool_support, get_cached_tool_support
    from .core.types import ToolSupportLevel, ApiMode
    
    config = get_config()
    backend_name = args.backend or config.backend
    api_mode_arg = getattr(args, 'api_mode', None)  # None = both modes

    # Which modes to test?  --api openai → only openai; otherwise both
    modes_to_test = [api_mode_arg] if api_mode_arg else ["openre", "openai"]
    # Always display both columns
    modes_display = ["openre", "openai"]

    # Use appropriate API mode for the backend
    from .core.types import ApiMode
    if backend_name in ("openrouter", "gemini"):
        # OpenRouter and Gemini only support OpenAI Chat-Completions
        api_mode = ApiMode.OPENAI
    else:
        api_mode = ApiMode.OPENRE
    
    backend = get_backend(backend_name, api_mode=api_mode)  # default for list_models etc.

    if not isinstance(backend, OllamaBackend):
        print(f"Models command works best with Ollama backend (current: {backend_name})")

    if not backend.is_running():
        print(f"❌ {backend_name.capitalize()} is not running at {backend.base_url}")
        if backend_name == "ollama":
            print("   Start with: ollama serve")
            print(f"   Or set OLLAMA_BASE_URL to your remote server")
        return 1

    models = backend.list_models()

    if not models:
        print("No models found.")
        if backend_name == "ollama":
            print("Pull one with: ollama pull qwen2.5:0.5b")
        return 0
    
    # Apply free-only filtering at the CLI level
    from .config import OPENROUTER_FREE_ONLY, ZAI_FREE_ONLY
    
    if backend_name == "openrouter" and OPENROUTER_FREE_ONLY:
        # OpenRouter free models have :free suffix
        models = [m for m in models if m["name"].endswith(":free")]
        if not models:
            print("No free models found on OpenRouter.")
            return 0
    elif backend_name == "zai" and ZAI_FREE_ONLY:
        # Only glm-4.5-flash and glm-4.7-flash are free on ZAI
        models = [m for m in models if m["name"] in ["glm-4.5-flash", "glm-4.7-flash"]]
        if not models:
            print("No free models found on ZAI.")
            return 0

    # Initialize ACP plugin if requested
    acp, _ = _init_acp(args, config, "AgentKthx-Models")

    # Column widths
    NAME_W = 36
    SIZE_W = 8
    CTX_W = 12
    TOOLS_W = 12  # fits "✓ native"
    FAMILY_W = 12

    # Detect backend type early — cloud providers need different column layout
    from .core.types import BackendType
    is_cloud_provider = backend.backend_type in [
        BackendType.OPENROUTER,
        BackendType.ZAI,
        BackendType.GEMINI,
    ]

    # Cloud providers have longer model names (e.g.
    # "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free" = 49 chars)
    # and don't show Size/Family columns. Widen NAME_W so names don't
    # overflow and push the Context column out of alignment.
    if is_cloud_provider:
        NAME_W = 50  # accommodates longest OpenRouter model names
        sep_len = 2 + NAME_W + 1 + CTX_W + 2 + TOOLS_W + 2 + TOOLS_W  # 81
    else:
        sep_len = 2 + NAME_W + 1 + SIZE_W + 1 + CTX_W + 2 + TOOLS_W + 2 + TOOLS_W + 2 + FAMILY_W  # 106

    print()
    print(f"{bright_cyan('\u2696 AgentKthx')} - Available Models")
    print(dim(f"  Backend: {backend.base_url}"))
    if args.tool_support:
        mode_label = ", ".join(modes_to_test)
        print(dim(f"  Testing: {mode_label}"))
    if acp:
        print(f"  {dim('ACP:')} {green('\u2713 Connected')} ({acp.base_url})")
    print(dim("-" * sep_len))

    if not is_cloud_provider:
        # Ollama and other local backends - show family column + size
        header = f"  {'Name':<{NAME_W}} {'Size':>{SIZE_W}}  {'Context':>{CTX_W}}  {'openre':>{TOOLS_W}}  {'openai':>{TOOLS_W}}  {'Family':<{FAMILY_W}}"
    else:
        # Cloud providers - skip Size column (always 'unknown') and Family column (encoded in name)
        header = f"  {'Name':<{NAME_W}} {'Context':>{CTX_W}}  {'openre':>{TOOLS_W}}  {'openai':>{TOOLS_W}}"

    print(header)
    print(dim("-" * sep_len))

    for m in models:
        name = m.get("name", "unknown")
        size = m.get("size", 0)
        size_gb = size / (1024**3) if size else 0
        family = m.get("details", {}).get("family", "unknown")
        
        # Get both runtime and max context
        runtime_ctx = backend.get_model_runtime_context(name)
        max_ctx = backend.get_model_max_context(name, family=family)
        
        # Format context size — show max context as plain int
        ctx_str = str(max_ctx)
        
        # Fixed columns
        name_col = pad_colored(cyan(name), NAME_W)
        size_col = f"{size_gb:>6.2f} GB"
        ctx_col = pad_colored(dim(ctx_str), CTX_W, 'right')

        # Handle Ollama with full tool support testing (not cloud providers)
        if isinstance(backend, OllamaBackend) and not is_cloud_provider:
            results = {}  # mode -> status string

            if args.tool_support:
                # Test each requested mode, skipping cached results
                modes_label = " + ".join(modes_to_test)
                print(f"  {dim('Testing:')} {cyan(name)} [{dim(modes_label)}]...", end="", flush=True)
                for mode in modes_to_test:
                    # Skip models that are already cached (unless --no-cache)
                    if not args.no_cache:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value
                            continue
                    backend.api_mode = ApiMode(mode)
                    try:
                        support = backend.test_tool_support(name, family=family, force_test=True)
                        cache_tool_support(name, support, family=family, api_mode=mode)
                        results[mode] = support.value
                    except Exception as e:
                        cache_tool_support(name, ToolSupportLevel.NONE, family=family,
                                           error=str(e)[:100], api_mode=mode)
                        results[mode] = "error"

                # Fill untested display modes from cache
                for mode in modes_display:
                    if mode not in results and not args.no_cache:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value

                # Overwrite the "Testing..." line with the final row
                tool_re = pad_colored(_tool_status(results.get("openre")), TOOLS_W, 'right')
                tool_ai = pad_colored(_tool_status(results.get("openai")), TOOLS_W, 'right')
                print(f"\r  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}  {dim('(' + family + ')')}")

                # Log per-model test result to ACP
                if acp:
                    acp.model_name = name
                    re_status = results.get('openre', '?')
                    ai_status = results.get('openai', '?')
                    acp.log_chat("user", f"Testing tool support...")
                    acp.log_chat("assistant", f"openre={re_status} openai={ai_status} | {size_gb:.2f} GB | ctx {max_ctx}")
            else:
                # Read from cache for both display modes
                for mode in modes_display:
                    if not args.no_cache:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value
                # Format missing modes as untested
                tool_re = pad_colored(_tool_status(results.get("openre")), TOOLS_W, 'right')
                tool_ai = pad_colored(_tool_status(results.get("openai")), TOOLS_W, 'right')
                print(f"  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}  {dim('(' + family + ')')}")
        
        # Handle cloud providers (ZAI, OpenRouter) with proper metadata and tool support
        elif is_cloud_provider:
            # Initialize results for cloud providers
            results = {}
            
            # Cloud providers don't provide reliable size info
            size_col = dim("unknown")
            
            # Format context size with units for better readability
            if max_ctx >= 1000:
                ctx_display = f"{max_ctx // 1024}K"
            else:
                ctx_display = str(max_ctx)
            ctx_col = pad_colored(dim(ctx_display), CTX_W, 'right')
            
            # Format context size with units for better readability
            if max_ctx >= 1000:
                ctx_display = f"{max_ctx // 1024}K"
            else:
                ctx_display = str(max_ctx)
            ctx_col = pad_colored(dim(ctx_display), CTX_W, 'right')
            
            # Get tool support for cloud providers
            if args.tool_support:
                modes_label = ", ".join(modes_to_test)
                print(f"  {dim('Testing:')} {cyan(name)} [{dim(modes_label)}]...", end="", flush=True)
                
                for mode in modes_to_test:
                    # Skip models that are already cached (unless --no-cache)
                    if not args.no_cache:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value
                            continue
                    
                    try:
                        support = backend.test_tool_support(name, family=family, force_test=True)
                        cache_tool_support(name, support, family=family, api_mode=mode)
                        results[mode] = support.value
                    except Exception as e:
                        cache_tool_support(name, ToolSupportLevel.NONE, family=family,
                                           error=str(e)[:100], api_mode=mode)
                        results[mode] = "error"
                
                # Fill untested display modes from cache
                for mode in modes_display:
                    if mode not in results and not args.no_cache:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value
                
                # Overwrite the "Testing..." line with the final row
                tool_re = pad_colored(_tool_status(results.get("openre", "untested")), TOOLS_W, 'right')
                tool_ai = pad_colored(_tool_status(results.get("openai", "untested")), TOOLS_W, 'right')
                print(f"\r  {name_col} {ctx_col}  {tool_re}  {tool_ai}")
                
                # Log per-model test result to ACP
                if acp:
                    acp.model_name = name
                    re_status = results.get('openre', '?')
                    ai_status = results.get('openai', '?')
                    acp.log_chat("user", f"Testing tool support...")
                    acp.log_chat("assistant", f"openre={re_status} openai={ai_status} | ctx {max_ctx}")
            else:
                # Read from cache or show default tool support for cloud providers
                results = {}
                
                # Only read from cache if not forcing fresh tests
                if not args.no_cache:
                    for mode in modes_display:
                        cached = get_cached_tool_support(name, api_mode=mode)
                        if cached is not None:
                            results[mode] = cached.value
                
                # For cloud providers, provide intelligent default tool support status
                if is_cloud_provider:
                    # Cloud providers have already validated tool support - always default to native
                    # Override any cached results since cloud providers have confirmed tool support
                    results = {"openre": "native", "openai": "native"}
                else:
                    # For non-cloud providers, use cached results or mark as untested
                    if not results:
                        results = {"openre": "untested", "openai": "untested"}
                
                tool_re = pad_colored(_tool_status(results.get("openre", "untested")), TOOLS_W, 'right')
                tool_ai = pad_colored(_tool_status(results.get("openai", "untested")), TOOLS_W, 'right')
                if not is_cloud_provider:
                    print(f"  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}  {dim('(' + family + ')')}")
                else:
                    # Cloud provider: no Size column, no Family column
                    print(f"  {name_col} {ctx_col}  {tool_re}  {tool_ai}")

    print(dim("-" * sep_len))
    print(f"Total: {bright_green(str(len(models)))} models")
    
    # Show legend
    print(f"\n{dim('Legend:')} {bright_green('✓ native')} (API tools) | {yellow('○ react')} (text parsing) | {red('✗ none')} (no tools) | {dim('? untested')}")
    print(f"{dim('Context:')} Max context window from model API")
    print(f"{dim('Tool support columns show openre (OpenResponses) and openai (Chat-Completions) results.')}")
    print(f"{dim('Use')} {cyan('--tool-support')} {dim('to test both API modes.')} {cyan('--tool-support --api openai')} {dim('to test only Chat-Completions.')}")

    # Log summary to ACP and clean up
    if acp:
        if args.tool_support:
            acp.log_chat("assistant", f"Tool-support scan complete: {len(models)} models tested")
        acp.a2a_unregister()

    return 0


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


def cmd_test(args: argparse.Namespace) -> int:
    """Execute the test command."""
    # Available tests
    TESTS = {
        "00": {
            "name": "Basic Agent",
            "desc": "Simple conversation without tools",
            "module": "agentkthx.examples.00_basic_agent",
        },
        "01": {
            "name": "Quick Diagnostic",
            "desc": "5-question math reasoning test",
            "module": "agentkthx.examples.01_quick_diagnostic",
        },
        "02": {
            "name": "Tool Tests",
            "desc": "Calculator, shell, datetime tools",
            "module": "agentkthx.examples.02_tool_test",
        },
        "03": {
            "name": "Reasoning Test",
            "desc": "Multi-step reasoning challenges",
            "module": "agentkthx.examples.03_reasoning_test",
        },
        "04": {
            "name": "GSM8K Benchmark",
            "desc": "Grade school math problems",
            "module": "agentkthx.examples.04_gsm8k_benchmark",
        },
        "05": {
            "name": "Common Sense",
            "desc": "Everyday knowledge and reasoning (25 questions)",
            "module": "agentkthx.examples.05_common_sense",
        },
        "06": {
            "name": "Causal Reasoning",
            "desc": "Cause and effect understanding (25 questions)",
            "module": "agentkthx.examples.06_causal_reasoning",
        },
        "07": {
            "name": "Logical Deduction",
            "desc": "Syllogisms and logic puzzles (25 questions)",
            "module": "agentkthx.examples.07_logical_deduction",
        },
        "08": {
            "name": "Reading Comprehension",
            "desc": "Text understanding and inference (25 questions)",
            "module": "agentkthx.examples.08_reading_comprehension",
        },
        "09": {
            "name": "General Knowledge",
            "desc": "Geography, science, and facts (25 questions)",
            "module": "agentkthx.examples.09_general_knowledge",
        },
        "10": {
            "name": "Implicit Reasoning",
            "desc": "Understanding implied meanings (25 questions)",
            "module": "agentkthx.examples.10_implicit_reasoning",
        },
        "11": {
            "name": "Analogical Reasoning",
            "desc": "Pattern and relationship mapping (25 questions)",
            "module": "agentkthx.examples.11_analogical_reasoning",
        },
    }
    
    # List tests
    if args.list:
        print(f"\n{bright_cyan('⚛ AgentKthx')} - Available Tests")
        print(dim("-" * 50))
        for tid, info in TESTS.items():
            print(f"  {cyan(tid)}  {info['name']:<20} {dim(info['desc'])}")
        print(dim("-" * 50))
        print(f"\n  Usage: {cyan('agentkthx test 01')} or {cyan('agentkthx test all')}")
        return 0
    
    # Determine which tests to run
    test_id = args.test_id.lower()
    if test_id == "all":
        tests_to_run = list(TESTS.keys())
    elif test_id in TESTS:
        tests_to_run = [test_id]
    else:
        print(f"{red('Error:')} Unknown test '{test_id}'")
        print(f"  Run {cyan('agentkthx test --list')} to see available tests")
        return 1
    
    # Check backend
    config = get_config()
    backend_name = args.backend or config.backend
    api_mode = getattr(args, 'api_mode', 'openre')
    timeout = getattr(args, 'timeout', None)
    backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    if not backend.is_running():
        print(f"{red('Error:')} {backend_name.capitalize()} not running at {backend.base_url}")
        if backend_name == "ollama":
            print(f"  Start with: {cyan('ollama serve')}")
            print(f"  Or set OLLAMA_BASE_URL to your remote server")
        return 1
    
    # Initialize ACP plugin if requested
    acp = None
    if args.acp:
        try:
            from .plugins.acp.acp_plugin import ACPPlugin
            acp_url = args.acp_url or config.acp_base_url
            acp = ACPPlugin(
                base_url=acp_url,
                agent_name="AgentKthx-Test",
                model_name=args.model or config.default_model,
                debug=args.debug,
            )
            # Bootstrap ACP connection
            bootstrap_result = acp.bootstrap()
            if bootstrap_result.get("stop_flag"):
                print(f"{red('Error:')} ACP STOP flag is set: {bootstrap_result.get('warnings')}")
                return 1
            acp_enabled = True
        except ImportError:
            print(f"{yellow('Warning:')} ACP plugin not available, continuing without ACP logging")
            acp = None
        except Exception as e:
            print(f"{yellow('Warning:')} Failed to connect to ACP: {e}")
            acp = None
    
    # Set environment for tests
    if args.debug:
        os.environ["AGENTKTHX_DEBUG"] = "1"
    if args.backend:
        os.environ["AGENTKTHX_BACKEND"] = args.backend
    if getattr(args, 'num_ctx', None):
        os.environ["AGENTKTHX_NUM_CTX"] = str(args.num_ctx)
    
    # Reload config to pick up new env vars
    config = get_config(reload=True)
    
    # Resolve model pattern to actual model(s)
    model_pattern = args.model

    # BitNet model discovery: when --backend bitnet without --model,
    # discover the actual model name from the server via list_models().
    # This avoids using the generic "bitnet-b1.58-2b-4t" placeholder and
    # ensures correct family config resolution (stop tokens, prompt format).
    # Mirrors the same logic in _build_agent().
    if not model_pattern and backend_name == "bitnet":
        try:
            discovered = backend.list_models()
            if (discovered and discovered[0].get("name")
                    and discovered[0]["name"] not in ("bitnet", "default")):
                model_pattern = discovered[0]["name"]
                if args.debug:
                    print(f"  [bitnet] Discovered model: {model_pattern}")
        except Exception:
            pass  # Fall through to config.default_model

    if model_pattern:
        models_to_test = resolve_model_pattern(model_pattern, backend_name, allow_multiple=True)
        if not models_to_test:
            return 1  # Error already printed
    else:
        models_to_test = [config.default_model]
    
    # Run tests
    print_banner()
    print(f"{bright_magenta('Test Runner')} — {len(tests_to_run)} test(s), {len(models_to_test)} model(s)")
    print(f"{dim('Backend:')} {backend_name} ({backend.base_url})")
    if len(models_to_test) == 1:
        print(f"{dim('Model:')} {cyan(models_to_test[0])}")
    else:
        print(f"{dim('Models:')} {cyan(str(len(models_to_test)))} matching '{model_pattern}'")
    num_ctx_val = getattr(args, 'num_ctx', None) if getattr(args, 'num_ctx', None) is not None else config.num_ctx
    if num_ctx_val:
        ctx_display = f"{num_ctx_val // 1024}K" if num_ctx_val >= 1024 else str(num_ctx_val)
        print(f"{dim('Context:')} {yellow(ctx_display)}")
    if acp:
        print(f"{dim('ACP:')} {green('✓ Connected')} ({acp.base_url})")
    print()
    
    # Track results per model
    all_results = {}  # model -> {test_id -> result}
    
    for model in models_to_test:
        model_results = {}
        print(f"\n{dim('═' * 50)}")
        print(f"{bright_magenta('Model:')} {cyan(model)}")
        print(dim("═" * 50))
        
        # Set model env var for this run
        os.environ["AGENTKTHX_MODEL"] = model
        
        for tid in tests_to_run:
            info = TESTS[tid]
            print(f"\n{dim('─' * 50)}")
            print(f"{cyan(f'[{tid}]')} {bright_magenta(info['name'])}")
            print(f"{dim(info['desc'])}")
            print(dim("─" * 50))
            
            # Log test start to ACP
            if acp:
                acp.log_chat("user", f"[{model}] Starting test: {info['name']}")
            
            try:
                # Import and run the test module
                import importlib
                module = importlib.import_module(info["module"])
                
                # Build argv for test modules (they have their own argparse)
                test_argv = ["-m", model]
                if args.debug:
                    test_argv.append("--debug")
                if args.backend:
                    test_argv.extend(["--backend", args.backend])
                if getattr(args, 'api_mode', 'openre') != 'openre':
                    test_argv.extend(["--api", args.api_mode])
                if getattr(args, 'force_react', False):
                    test_argv.append("--force-react")
                if getattr(args, 'use_modelfile_system', False):
                    test_argv.append("--use-mf-sys")
                if getattr(args, 'soul', None):
                    test_argv.extend(["--soul", args.soul])
                    test_argv.extend(["--soul-level", str(getattr(args, 'soul_level', 2))])
                if getattr(args, 'timeout', None):
                    test_argv.extend(["--timeout", str(args.timeout)])
                if getattr(args, 'warmup', False):
                    test_argv.append("--warmup")
                if getattr(args, 'num_ctx', None) is not None:
                    test_argv.extend(["--num-ctx", str(args.num_ctx)])
                if getattr(args, 'num_predict', None) is not None:
                    test_argv.extend(["--num-predict", str(args.num_predict)])
                if getattr(args, 'temperature', None) is not None:
                    test_argv.extend(["--temp", str(args.temperature)])
                if getattr(args, 'top_p', None) is not None:
                    test_argv.extend(["--top-p", str(args.top_p)])
                if getattr(args, 'tools_only', False):
                    test_argv.append("--tools-only")
                if getattr(args, 'model_only', False):
                    test_argv.append("--model-only")
                if getattr(args, 'quick', False):
                    test_argv.append("--quick")
                
                # Override sys.argv for the test module's argparse
                old_argv = sys.argv
                sys.argv = ["test"] + test_argv
                
                try:
                    result = module.main()
                finally:
                    sys.argv = old_argv
                
                # Handle both old-style exit code and new-style result dict
                if isinstance(result, dict):
                    # New-style: granular results
                    passed = result.get("passed", 0)
                    total = result.get("total", 1)
                    time_s = result.get("time", 0)
                    exit_code = result.get("exit_code", 0)
                    model_results[tid] = {
                        "passed": exit_code == 0,
                        "exit_code": exit_code,
                        "granular": f"{passed}/{total}",
                        "time": time_s,
                    }
                else:
                    # Old-style: just exit code
                    exit_code = result if result is not None else 1
                    model_results[tid] = {"passed": exit_code == 0, "exit_code": exit_code}
                
                # Log test result to ACP
                if acp:
                    status = "passed" if exit_code == 0 else "failed"
                    granular = model_results[tid].get("granular", "")
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: {status} {granular}")
                
            except ImportError as e:
                print(f"{red('Error:')} Could not import test module: {e}")
                model_results[tid] = {"passed": False, "error": str(e)}
                if acp:
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: import error - {e}")
            except Exception as e:
                print(f"{red('Error:')} {e}")
                model_results[tid] = {"passed": False, "error": str(e)}
                if acp:
                    acp.log_chat("assistant", f"[{model}] Test {info['name']}: error - {e}")
        
        all_results[model] = model_results
    
    # Summary
    print(f"\n{dim('=' * 50)}")
    print(f"{bright_magenta('Test Summary')}")
    print(dim("=" * 50))
    
    # If multiple models, show per-model summary
    if len(models_to_test) > 1:
        print(f"\n{bright_magenta('Results by Model:')}")
        for model in models_to_test:
            model_results = all_results.get(model, {})
            passed = sum(1 for r in model_results.values() if r.get("passed"))
            total = len(model_results)
            granular_sum = ""
            # Sum up granular scores if available
            total_score = 0
            total_possible = 0
            total_time = 0
            for r in model_results.values():
                if "granular" in r:
                    try:
                        parts = r["granular"].split("/")
                        total_score += int(parts[0])
                        total_possible += int(parts[1])
                    except (ValueError, IndexError):
                        pass
                total_time += r.get("time", 0)
            
            if total_possible > 0:
                granular_sum = f"  {cyan(f'{total_score}/{total_possible}')}"
            time_str = f"  {dim(f'({total_time:.1f}s)')}" if total_time else ""
            status = bright_green("✓") if passed == total else red("✗")
            print(f"  {status} {cyan(model):<30} {passed}/{total}{granular_sum}{time_str}")
    else:
        # Single model - show per-test breakdown
        model = models_to_test[0]
        model_results = all_results.get(model, {})
        
        for tid, result in model_results.items():
            status = bright_green("✓ PASS") if result.get("passed") else red("✗ FAIL")
            granular = result.get("granular", "")
            time_s = result.get("time", 0)
            
            # Show granular results if available
            if granular:
                time_str = f" ({time_s:.1f}s)" if time_s else ""
                print(f"  [{tid}] {TESTS[tid]['name']:<20} {status}  {cyan(granular)}{time_str}")
            else:
                print(f"  [{tid}] {TESTS[tid]['name']:<20} {status}")
        
        print(dim("-" * 50))
        passed = sum(1 for r in model_results.values() if r.get("passed"))
        total = len(model_results)
        pct = 100 * passed // total if total > 0 else 0
        print(f"  {bright_green(str(passed))}/{total} tests passed ({pct}%)")
    
    # Log final summary to ACP and unregister (don't shutdown the server!)
    if acp:
        total_passed = sum(
            1 for model_results in all_results.values() 
            for r in model_results.values() if r.get("passed")
        )
        total_tests = sum(len(mr) for mr in all_results.values())
        acp.log_chat("assistant", f"All tests complete: {total_passed}/{total_tests} passed")
        # Only unregister from A2A, don't shutdown the ACP server
        acp.a2a_unregister()
        acp._log("Test complete, unregistered from A2A (ACP server remains running)")
    
    # Return success only if all tests passed
    total_passed = sum(
        1 for model_results in all_results.values() 
        for r in model_results.values() if r.get("passed")
    )
    total_tests = sum(len(mr) for mr in all_results.values())
    return 0 if total_passed == total_tests else 1


def cmd_version(args: argparse.Namespace) -> int:
    """Show version information."""
    from . import __version__, __status__, __author__

    print_banner()
    print(f"   {dim('Version:')} {bright_green(__version__)}")
    print(f"   {dim('Status:')}  {yellow(__status__)}")
    print(f"   {dim('Author:')}  {cyan(__author__)}")
    print(f"   {dim('Repo:')}    {dim('https://github.com/VTSTech/AgentKthx')}")

    # Latest releases (daily-cached checks — silent on failure / opt-out):
    # stable track via PyPI, development track via GitHub main commits
    # (the commit line only appears for git checkouts, which have a baseline).
    try:
        from .update_check import base_version, check_for_update, git_hash, is_newer
        _latest_info = check_for_update(timeout=1.0)
    except Exception:
        _latest_info = None
    if _latest_info:
        _latest = str(_latest_info.get("pypi_latest") or "").strip()
        if _latest:
            if is_newer(_latest, base_version(__version__)):
                print(f"   {dim('Latest on PyPI:')} {bright_green(_latest)} {yellow('(stable update available)')}")
            else:
                print(f"   {dim('Latest on PyPI:')} {_latest} {dim('(up to date)')}")
        _gh = str(_latest_info.get("github_sha") or "").strip().lower()
        _installed = git_hash(__version__)
        if _gh and _installed:
            if not _gh.startswith(_installed):
                print(f"   {dim('GitHub main:')} {bright_green(_gh[:7])} {yellow('(development release available)')}")
            else:
                print(f"   {dim('GitHub main:')} {_gh[:7]} {dim('(up to date)')}")
    print()

    return 0


def cmd_turbo(args: argparse.Namespace) -> int:
    """TurboQuant server management commands."""
    from .plugins.turboquant.turbo import (
        start_server, stop_server, get_status,
        print_model_list, print_status,
        TURBOQUANT_SERVER_PATH,
    )
    from .backends.ollama_registry import discover_models

    turbo_cmd = getattr(args, "turbo_command", None)

    if not turbo_cmd:
        # No subcommand — show status or help
        state = get_status()
        if state:
            print_status(state)
        else:
            print(bold(bright_cyan("TURBOQUANT")) + dim(" — server management"))
            print()
            print(f"  {bold('Usage:')}")
            print(f"    agentkthx turbo list             List Ollama models")
            print(f"    agentkthx turbo start <model>     Start TurboQuant server")
            print(f"    agentkthx turbo stop              Stop TurboQuant server")
            print(f"    agentkthx turbo status            Show server status")
            print()
            print(f"  {dim('No server running.')} Run {cyan('agentkthx turbo list')} to see available models.")
            print()
        return 0

    if turbo_cmd == "list":
        from .backends.ollama_registry import OllamaModel

        ollama_dir = Path(args.ollama_dir) if args.ollama_dir else None
        only_existing = not getattr(args, "all", False)
        config = get_config()

        # Try API-based discovery first (same source as `agentkthx models`)
        api_models = None
        api_url = None
        backend_name = getattr(args, "backend", None) or config.backend
        if backend_name == "ollama":
            try:
                backend = get_backend("ollama", api_mode="openre")
                api_models = backend.list_models()
                if api_models:
                    api_url = backend.base_url
            except Exception:
                api_models = None

        if api_models is not None:
            # Got models from API — merge with local GGUF metadata
            local_models = discover_models(ollama_dir=ollama_dir, only_existing=False)

            # Build lookup by both short name ("repo:tag") and full name ("library/repo:tag")
            # discover_models uses short names; the API returns full names with library prefix
            local_lookup: dict[str, OllamaModel] = {}
            for m in local_models:
                local_lookup[m.name] = m
                # Derive full name from manifest path: .../registry.ollama.ai/<library>/<repo>/<tag>
                if m.manifest_path != Path("") and m.manifest_path.parent.parent.name != "registry.ollama.ai":
                    library = m.manifest_path.parent.parent.name
                    full_name = f"{library}/{m.name}"
                    local_lookup[full_name] = m

            models: list = []
            for api_m in api_models:
                name = api_m.get("name", "")
                if not name:
                    continue
                if name in local_lookup:
                    models.append(local_lookup[name])
                else:
                    # Not pulled locally — create minimal OllamaModel from API data
                    repo, tag = name, "latest"
                    if ":" in name:
                        repo, tag = name.rsplit(":", 1)
                    models.append(OllamaModel(
                        name=name,
                        repo=repo,
                        tag=tag,
                        blob_path=Path(""),
                        size_bytes=api_m.get("size", 0),
                        weight_quant="not pulled",
                        manifest_path=Path(""),
                        model_digest="",
                    ))
            models.sort(key=lambda m: m.name)
            print_model_list(models, source="api", backend_url=api_url)
        else:
            # API unreachable — fall back to filesystem discovery
            models = discover_models(ollama_dir=ollama_dir, only_existing=only_existing)
            print_model_list(models, source="local")
        return 0

    elif turbo_cmd == "start":
        try:
            state = start_server(
                model_name=args.model,
                server_path=args.server,
                port=args.port,
                ctx=args.ctx,
                cache_type_k=args.turbo_k,
                cache_type_v=args.turbo_v,
                flash_attn=getattr(args, "flash_attn", False),
                sparsity=getattr(args, "sparsity", 0.0),
                num_threads=getattr(args, "threads", 0),
                wait_ready=not getattr(args, "no_wait", False),
                ready_timeout=getattr(args, "timeout", 120),
                extra_args=getattr(args, "extra_args", None),
            )
            # Show how to use
            print(dim("  Use with AgentKthx:"))
            _cmd1 = f"agentkthx run --backend llama-server --model {args.model} \"<prompt>\""
            _cmd2 = f"OLLAMA_BASE_URL=http://localhost:{state.port} agentkthx run \"<prompt>\""
            print(f"    {cyan(_cmd1)}")
            print(f"    {cyan(_cmd2)}")
            print()
            return 0
        except FileNotFoundError as e:
            print(bright_red(f"Error: {e}"))
            return 1
        except RuntimeError as e:
            print(bright_red(f"Error: {e}"))
            return 1
        except ValueError as e:
            print(bright_red(f"Error: {e}"))
            return 1

    elif turbo_cmd == "stop":
        stopped = stop_server(force=getattr(args, "force", False))
        if not stopped:
            print(yellow("No TurboQuant server is running."))
            print()
        return 0

    elif turbo_cmd == "status":
        state = get_status()
        if state:
            print_status(state)
        else:
            print(yellow("No TurboQuant server is running."))
            print()
            print(dim("  Start one with:"))
            print(f"    {cyan('agentkthx turbo list')}        # see available models")
            print(f"    {cyan('agentkthx turbo start <model>')}  # start server")
            print()
        return 0

    return 1


def cmd_config(args: argparse.Namespace) -> int:
    """Show current configuration."""
    from .config import (
        OLLAMA_BASE_URL, BITNET_BASE_URL, LLAMA_SERVER_BASE_URL,
        ZAI_BASE_URL, ZAI_API_KEY, ZAI_FREE_ONLY, ZAI_FREE_FALLBACK_MODEL,
        OPENROUTER_BASE_URL, OPENROUTER_API_KEY, OPENROUTER_DEFAULT_MODEL, OPENROUTER_FREE_ONLY,
        ACP_BASE_URL, ACP_USER, ACP_PASS,
        TURBOQUANT_SERVER_PATH, TURBOQUANT_PORT, TURBOQUANT_CTX,
        AGENTKTHX_BACKEND, DEFAULT_MODEL, NUM_CTX,
        MAX_STEPS, DEBUG, VERBOSE,
        RETRY_ON_ERROR, MAX_TOOL_RETRIES,
    )

    # ── --urls: compact URL dump ──────────────────────────────────────────
    if args.urls:
        urls = [
            ("OLLAMA_BASE_URL",        OLLAMA_BASE_URL),
            ("BITNET_BASE_URL",        BITNET_BASE_URL),
            ("LLAMA_SERVER_BASE_URL",  LLAMA_SERVER_BASE_URL),
            ("ZAI_BASE_URL",           ZAI_BASE_URL),
            ("OPENROUTER_BASE_URL",    OPENROUTER_BASE_URL),
            ("ACP_BASE_URL",           ACP_BASE_URL),
        ]
        for name, val in urls:
            print(f"{name}={val}")
        return 0

    # ── --full: dump every config var ─────────────────────────────────────
    if getattr(args, "full", False):
        cfg = get_config()
        all_vars = {
            "Backend": [
                ("AGENTKTHX_BACKEND", AGENTKTHX_BACKEND),
                ("DEFAULT_MODEL", DEFAULT_MODEL),
            ],
            "URLs": [
                ("OLLAMA_BASE_URL",       OLLAMA_BASE_URL),
                ("BITNET_BASE_URL",       BITNET_BASE_URL),
                ("LLAMA_SERVER_BASE_URL", LLAMA_SERVER_BASE_URL),
                ("ZAI_BASE_URL",          ZAI_BASE_URL),
                ("OPENROUTER_BASE_URL",   OPENROUTER_BASE_URL),
                ("ACP_BASE_URL",          ACP_BASE_URL),
            ],
            "OpenRouter": [
                ("OPENROUTER_API_KEY",      _mask_key(OPENROUTER_API_KEY)),
                ("OPENROUTER_DEFAULT_MODEL", OPENROUTER_DEFAULT_MODEL),
                ("OPENROUTER_FREE_ONLY",    str(OPENROUTER_FREE_ONLY)),
            ],
            "ZAI": [
                ("ZAI_API_KEY",             _mask_key(ZAI_API_KEY)),
                ("ZAI_FREE_ONLY",           str(ZAI_FREE_ONLY)),
                ("ZAI_FREE_FALLBACK_MODEL", ZAI_FREE_FALLBACK_MODEL),
            ],
            "ACP": [
                ("ACP_USER", ACP_USER),
                ("ACP_PASS", _mask_key(ACP_PASS)),
            ],
            "TurboQuant": [
                ("TURBOQUANT_SERVER_PATH", TURBOQUANT_SERVER_PATH),
                ("TURBOQUANT_PORT",        str(TURBOQUANT_PORT)),
                ("TURBOQUANT_CTX",         str(TURBOQUANT_CTX)),
            ],
            "Agent": [
                ("MAX_STEPS",       str(MAX_STEPS)),
                ("NUM_CTX",         str(NUM_CTX) if NUM_CTX > 0 else "0 (backend default)"),
                ("DEBUG",           str(DEBUG)),
                ("VERBOSE",         str(VERBOSE)),
            ],
            "Retry": [
                ("RETRY_ON_ERROR",  str(RETRY_ON_ERROR)),
                ("MAX_TOOL_RETRIES", str(MAX_TOOL_RETRIES)),
            ],
            "Config Dataclass": [
                ("temperature",           str(cfg.temperature)),
                ("max_tokens",            str(cfg.max_tokens)),
                ("memory_max_messages",   str(cfg.memory_max_messages)),
                ("memory_max_tokens",     str(cfg.memory_max_tokens)),
                ("allow_shell",           str(cfg.allow_shell)),
                ("allow_network",         str(cfg.allow_network)),
                ("allowed_paths",         ", ".join(cfg.allowed_paths)),
            ],
        }
        print()
        print(f"{bright_cyan('AgentKthx')} - Full Configuration")
        print(dim("=" * 50))
        for section, entries in all_vars.items():
            print(f"\n  {yellow(section)}")
            for name, val in entries:
                print(f"    {dim(f'{name}:')} {cyan(val)}")
        print()
        print(dim("=" * 50))
        return 0

    # ── Default: pretty summary ───────────────────────────────────────────
    _print_config_summary(
        backend=AGENTKTHX_BACKEND,
        model=DEFAULT_MODEL,
        num_ctx=NUM_CTX,
        max_steps=MAX_STEPS,
        debug=DEBUG,
        verbose=VERBOSE,
        urls={
            "Ollama":       OLLAMA_BASE_URL,
            "BitNet":       BITNET_BASE_URL,
            "llama-server": LLAMA_SERVER_BASE_URL,
            "ZAI":          ZAI_BASE_URL,
            "OpenRouter":   OPENROUTER_BASE_URL,
            "ACP":          ACP_BASE_URL,
        },
        openrouter_key=_mask_key(OPENROUTER_API_KEY),
        openrouter_default=OPENROUTER_DEFAULT_MODEL,
        openrouter_free_only=OPENROUTER_FREE_ONLY,
        zai_key=ZAI_API_KEY,
        zai_free_only=ZAI_FREE_ONLY,
        zai_fallback=ZAI_FREE_FALLBACK_MODEL,
        acp_user=ACP_USER,
        acp_pass=ACP_PASS,
        turboquant={
            "Server Path": TURBOQUANT_SERVER_PATH,
            "Port":        str(TURBOQUANT_PORT),
            "Context":     str(TURBOQUANT_CTX),
        },
        retry_on_error=RETRY_ON_ERROR,
        max_tool_retries=MAX_TOOL_RETRIES,
    )
    return 0


def _mask_key(key: str) -> str:
    """Mask a secret, showing first 4 and last 4 chars if long enough."""
    if not key:
        return dim("(not set)")
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _print_config_summary(
    backend: str, model: str, num_ctx: int,
    max_steps: int, debug: bool, verbose: bool,
    urls: dict[str, str],
    zai_key: str, zai_free_only: bool, zai_fallback: str,
    openrouter_key: str, openrouter_default: str, openrouter_free_only: bool,
    acp_user: str, acp_pass: str,
    turboquant: dict[str, str],
    retry_on_error: bool, max_tool_retries: int,
) -> None:
    """Pretty-print the default config summary."""
    print()
    print(f"{bright_cyan('AgentKthx')} - Configuration")
    print(dim("-" * 50))

    # ── Active backend & model ────────────────────────────────────────────
    print(f"\n  {yellow('Active Backend')}")
    print(f"    {dim('Backend:')}       {green(backend)}")
    print(f"    {dim('Default Model:')} {cyan(model)}")
    print(f"    {dim('Max Steps:')}     {cyan(str(max_steps))}")
    if num_ctx and num_ctx > 0:
        ctx_display = f"{num_ctx // 1024}K" if num_ctx >= 1024 else str(num_ctx)
        print(f"    {dim('Context Window:')} {yellow(ctx_display)} (num_ctx)")
    flags = []
    if debug:
        flags.append(green("DEBUG"))
    if verbose:
        flags.append(green("VERBOSE"))
    if flags:
        print(f"    {dim('Flags:')}         {' '.join(flags)}")

    # ── Backend URLs ──────────────────────────────────────────────────────
    print(f"\n  {yellow('Backend URLs')}")
    max_name = max(len(n) for n in urls)
    for name, url in urls.items():
        pad = " " * (max_name - len(name))
        marker = bright_green(" *") if name.lower().replace("-", "") == backend.lower().replace("_", "").replace("-", "") else "  "
        print(f"   {marker} {dim(name)}{pad}: {cyan(url)}")

    # ── ZAI settings ──────────────────────────────────────────────────────
    print(f"\n  {yellow('ZAI API')}")
    key_status = _mask_key(zai_key)
    free_badge = green("ON") if zai_free_only else dim("OFF")
    print(f"    {dim('API Key:')}       {key_status}")
    print(f"    {dim('Free Only:')}     {free_badge}")
    print(f"    {dim('Fallback Model:')} {cyan(zai_fallback)}")

    # ── ACP credentials ───────────────────────────────────────────────────
    print(f"\n  {yellow('ACP (Agent Control Panel)')}")
    print(f"    {dim('User:')}          {cyan(acp_user)}")
    print(f"    {dim('Password:')}      {_mask_key(acp_pass)}")

    # ── OpenRouter API ────────────────────────────────────────────────────
    print(f"\n  {yellow('OpenRouter API')}")
    print(f"    {dim('API Key:')}       {openrouter_key}")
    print(f"    {dim('Default Model:')} {cyan(openrouter_default)}")
    free_badge = green("ON") if openrouter_free_only else dim("OFF")
    print(f"    {dim('Free Only:')}     {free_badge}")

    # ── TurboQuant ────────────────────────────────────────────────────────
    print(f"\n  {yellow('TurboQuant')}")
    for name, val in turboquant.items():
        print(f"    {dim(f'{name}:')} {cyan(val)}")

    # ── Retry settings ────────────────────────────────────────────────────
    print(f"\n  {yellow('Error Handling')}")
    retry_badge = green("ON") if retry_on_error else dim("OFF")
    print(f"    {dim('Retry on Error:')}  {retry_badge}")
    print(f"    {dim('Max Tool Retries:')} {cyan(str(max_tool_retries))}")

    # ── Environment variable reference ────────────────────────────────────
    env_vars = [
        ("AGENTKTHX_BACKEND",       "Default backend (ollama|bitnet|llama-server|zai|openrouter)"),
        ("AGENTKTHX_MODEL",         "Override default model"),
        ("AGENTKTHX_MAX_STEPS",     "Max agent steps (default: 10)"),
        ("AGENTKTHX_DEBUG",         "Enable debug output (1/true/yes)"),
        ("AGENTKTHX_VERBOSE",       "Enable verbose output (1/true/yes)"),
        ("OLLAMA_NUM_CTX",          "Ollama context window size"),
        ("AGENTKTHX_NUM_CTX",       "Generic context window size (fallback)"),
        ("AGENTKTHX_RETRY_ON_ERROR","Auto-retry failed tool calls (default: true)"),
        ("AGENTKTHX_MAX_TOOL_RETRIES","Max retries per tool call (default: 2)"),
        ("AGENTKTHX_FORCE_REACT",   "Force ReAct text-based tool calling"),
        ("AGENTKTHX_USE_MF_SYS",    "Use Modelfile system prompt"),
        ("AGENTKTHX_NUM_PREDICT",   "Max tokens to generate"),
        ("AGENTKTHX_TEMPERATURE",   "Sampling temperature"),
        ("AGENTKTHX_TOP_P",         "Nucleus sampling parameter"),
        ("AGENTKTHX_FAST",          "Fast mode preset"),
        ("OLLAMA_BASE_URL",         "Ollama server URL"),
        ("BITNET_BASE_URL",         "BitNet server URL"),
        ("BITNET_TUNNEL",           "BitNet remote tunnel URL"),
        ("LLAMA_SERVER_BASE_URL",   "llama-server URL"),
        ("ZAI_BASE_URL",            "ZAI API URL"),
        ("ZAI_API_KEY",             "ZAI API key"),
        ("ZAI_FREE_ONLY",           "Restrict to free ZAI models only"),
        ("ZAI_FREE_FALLBACK_MODEL", "Fallback model when credits insufficient"),
        ("OPENROUTER_BASE_URL",     "OpenRouter API URL"),
        ("OPENROUTER_API_KEY",      "OpenRouter API key"),
        ("OPENROUTER_DEFAULT_MODEL", "Default OpenRouter model"),
        ("OPENROUTER_FREE_ONLY",     "Restrict to free OpenRouter models only"),
        ("ACP_BASE_URL",            "ACP server URL"),
        ("ACP_USER",                "ACP username (default: admin)"),
        ("ACP_PASS",                "ACP password (default: secret)"),
        ("TURBOQUANT_SERVER_PATH",  "Path to llama-server binary"),
        ("TURBOQUANT_PORT",         "TurboQuant server port (default: 8764)"),
        ("TURBOQUANT_CTX",          "TurboQuant context window (default: 8192)"),
    ]
    print(f"\n  {yellow('Environment Variables')}")
    max_env = max(len(v[0]) for v in env_vars)
    for var, desc in env_vars:
        pad = " " * (max_env - len(var))
        print(f"    {dim(var)}{pad}  {dim('-')} {desc}")

    print(dim("-" * 50))


def cmd_modelfile(args: argparse.Namespace) -> int:
    """Show model's Modelfile system prompt and other info."""
    from .backends import OllamaBackend

    config = get_config()
    backend_name = args.backend or config.backend
    backend = get_backend(backend_name)

    if not isinstance(backend, OllamaBackend):
        print(f"{red('Error:')} Modelfile command requires Ollama backend")
        return 1

    if not backend.is_running():
        print(f"{red('✗')}  Ollama is not running. Start it with: {cyan('ollama serve')}")
        return 1

    model = args.model or config.default_model
    print(bold(f"\n⚛️ AgentKthx Modelfile") + dim(" · Written by VTSTech · https://kthx.vts-tech.org"))
    print()

    try:
        info = backend.get_model_info(model)
    except Exception as e:
        print(f"{red('✗')}  Could not get info for model '{model}': {e}")
        return 1

    # Display model information
    print(bold(f"Model: {model}"))
    print(dim("─" * 70))
    print()

    # System prompt from Modelfile
    system_prompt = info.get("system")
    if system_prompt:
        print(cyan("SYSTEM PROMPT (from Modelfile):"))
        print()
        print(system_prompt)
        print()
    else:
        print(dim("(No SYSTEM prompt defined in Modelfile)"))
        print()

    # Template
    template = info.get("template")
    if template:
        print(cyan("TEMPLATE:"))
        print()
        print(template)
        print()

    # Parameters
    params = info.get("parameters", "")
    if params:
        print(cyan("PARAMETERS:"))
        print()
        for line in params.strip().split("\n"):
            if line.strip():
                print(dim(f"  {line.strip()}"))
        print()

    # Details
    details = info.get("details", {})
    if details:
        print(cyan("DETAILS:"))
        print()
        for key, value in details.items():
            print(dim(f"  {key}: {value}"))
        print()

    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    """List available skills."""
    from .skills import SkillLoader

    loader = SkillLoader()
    skills = loader.list_skills()

    print(bold(f"\n⚛️ AgentKthx Skills") + dim(" · Written by VTSTech · https://kthx.vts-tech.org"))

    if not skills:
        print(yellow("  No skills found."))
        print(dim("  Skills are loaded from agentkthx/skills/*/SKILL.md"))
        return 0

    print(bold(f"{'Skill':<20} Description"))
    print(dim("─" * 70))

    for name in skills:
        try:
            skill = loader.load(name)
            desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
            print(f"  {magenta(name):<26} {desc}")

            # Show resources
            resources = []
            if skill.scripts_dir:
                scripts = list(skill.scripts_dir.glob("*.py"))
                if scripts:
                    resources.append(f"{len(scripts)} scripts")
            if skill.references_dir:
                refs = list(skill.references_dir.glob("*.md"))
                if refs:
                    resources.append(f"{len(refs)} refs")
            if resources:
                print(f"  {'':<26} {dim('Has: ' + ', '.join(resources))}")
        except Exception as e:
            print(f"  {magenta(name):<26} {red(f'Error: {e}')}")

    print()
    print(dim(f"  Use with: {cyan('--skills')} {','.join(skills[:2])}"))
    print(dim("  Skills provide knowledge/instructions to the agent."))
    print(dim(f"  Available commands support: {cyan('run --skills <list>')}, {cyan('chat --skills <list>')}, {cyan('agent --skills <list>')}"))
    print()

    return 0


def cmd_soul(args: argparse.Namespace) -> int:
    """Inspect a Soul Spec package."""
    try:
        from .soul import load_soul, build_system_prompt, SoulLoader
    except ImportError:
        print(f"{red('Error:')} Soul module not available")
        return 1
    
    path = args.path
    level = args.level
    
    try:
        loader = SoulLoader()
        soul = loader.load(path, level=level)
    except FileNotFoundError as e:
        print(f"{red('Error:')} Soul package not found: {e}")
        return 1
    except Exception as e:
        print(f"{red('Error:')} Failed to load soul: {e}")
        return 1
    
    # Display soul info
    print()
    print(bold(f"👻 Soul Spec Package") + dim(" · ClawSouls v0.5"))
    print(dim("─" * 70))
    print()
    
    # Basic info
    print(f"  {cyan('Name:')}        {soul.display_name} ({dim(soul.name)})")
    print(f"  {cyan('Version:')}     {soul.version}")
    print(f"  {cyan('Spec:')}        v{soul.spec_version}")
    print(f"  {cyan('Author:')}      {soul.author.name}" + (f" ({soul.author.github})" if soul.author.github else ""))
    print(f"  {cyan('License:')}     {soul.license}")
    print()
    
    print(f"  {cyan('Description:')}")
    print(f"    {soul.description}")
    print()
    
    # Disclosure summary
    if soul.disclosure and soul.disclosure.summary:
        print(f"  {cyan('Summary:')}")
        print(f"    {soul.disclosure.summary}")
        print()
    
    # Tags and category
    if soul.tags:
        print(f"  {cyan('Tags:')}       {', '.join(soul.tags)}")
    print(f"  {cyan('Category:')}   {soul.category}")
    print()
    
    # Environment
    if soul.environment.value != "virtual":
        print(f"  {yellow('Environment:')} {soul.environment.value}")
        print(f"  {yellow('Interaction:')} {soul.interaction_mode.value}")
        if soul.hardware_constraints:
            hc = soul.hardware_constraints
            caps = []
            if hc.has_display: caps.append("display")
            if hc.has_speaker: caps.append("speaker")
            if hc.has_microphone: caps.append("microphone")
            if hc.has_camera: caps.append("camera")
            if caps:
                print(f"  {yellow('Hardware:')}   {', '.join(caps)}")
        if soul.safety and soul.safety.physical:
            print(f"  {yellow('Safety:')}     {soul.safety.physical.contact_policy.value}")
        print()
    
    # Allowed tools
    if soul.allowed_tools:
        print(f"  {cyan('Allowed Tools:')} {', '.join(soul.allowed_tools)}")
    
    # Recommended skills
    if soul.recommended_skills:
        required = [s.name for s in soul.recommended_skills if s.required]
        optional = [s.name for s in soul.recommended_skills if not s.required]
        if required:
            print(f"  {cyan('Required Skills:')} {', '.join(required)}")
        if optional:
            print(f"  {cyan('Optional Skills:')} {', '.join(optional)}")
    
    # Compatibility
    if soul.compatibility.frameworks:
        print(f"  {cyan('Frameworks:')}   {', '.join(soul.compatibility.frameworks)}")
    if soul.compatibility.models:
        print(f"  {cyan('Models:')}       {', '.join(soul.compatibility.models)}")
    
    print()
    
    # Validation
    if args.validate:
        issues = soul.validate()
        if issues:
            print(f"  {red('Validation Issues:')}")
            for issue in issues:
                print(f"    {red('✗')} {issue}")
        else:
            print(f"  {green('✓')} Validation passed")
        print()
    
    # Loaded content
    if level >= 2:
        print(dim("─" * 70))
        if soul.soul_content:
            print(f"\n  {cyan('SOUL.md:')}")
            lines = soul.soul_content.split("\n")
            for line in lines[:10]:
                print(f"    {line}")
            if len(lines) > 10:
                remaining = len(lines) - 10
                print(f"    {dim('...')} ({remaining} more lines)")
        
        if soul.identity_content:
            print(f"\n  {cyan('IDENTITY.md:')}")
            lines = soul.identity_content.split("\n")
            for line in lines[:10]:
                print(f"    {line}")
            if len(lines) > 10:
                remaining = len(lines) - 10
                print(f"    {dim('...')} ({remaining} more lines)")
    
    # Show generated system prompt
    if args.prompt:
        prompt = loader.build_system_prompt(soul, level=level)
        print()
        print(dim("─" * 70))
        print(f"\n  {cyan('Generated System Prompt:')}")
        print(dim("─" * 70))
        print(prompt)
    
    print()
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    """List or delete saved sessions."""
    try:
        from .core.persistent_memory import PersistentMemory
    except ImportError:
        print(f"{red('Error:')} Persistent memory not available (requires sqlite3)")
        return 1

    if args.delete:
        # Delete mode
        session_id = args.delete
        deleted = PersistentMemory.delete_session(session_id)
        if deleted:
            print(f"{green('✓')} Deleted session: {cyan(session_id)}")
        else:
            print(f"{red('✗')} Session not found: {cyan(session_id)}")
            return 1
    else:
        # List mode
        sessions = PersistentMemory.list_sessions()
        if not sessions:
            print("No saved sessions found.")
            _hint = 'agentkthx run --session <name> "<prompt>"'
            print(f"\n  Start a session with: {cyan(_hint)}")
            return 0

        ID_W = 20
        MSGS_W = 8
        CREATED_W = 19
        UPDATED_W = 19

        print()
        print(f"{bright_cyan('⚛ AgentKthx')} - Saved Sessions")
        print(f"{dim('  DB:')} ~/.agentkthx/memory.db")
        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))
        print(f"  {'Session':<{ID_W}} {'Msgs':>{MSGS_W}}  {'Created':<{CREATED_W}}  {'Updated':<{UPDATED_W}}")
        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))

        for s in sessions:
            sid = s["session_id"]
            msgs = s["message_count"]
            created = s["created_at"][:19].replace("T", " ")
            updated = s["updated_at"][:19].replace("T", " ")
            print(f"  {cyan(sid):<{ID_W}} {msgs:>{MSGS_W}}  {dim(created):<{CREATED_W}}  {dim(updated):<{UPDATED_W}}")

        print(dim("-" * (4 + ID_W + MSGS_W + CREATED_W + UPDATED_W)))
        print(f"Total: {bright_green(str(len(sessions)))} sessions")
        _resume = 'agentkthx run --session <name> "<prompt>"'
        print(f"\n{dim('Resume a session:')} {cyan(_resume)}")
        print(f"{dim('Delete a session:')} {cyan('agentkthx sessions --delete <name>')}")

    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    """List and manage plugins (v0.2 spec §CLI integration)."""
    from .plugins import get_plugin_manager

    pm = get_plugin_manager()

    # --- Management actions (--load / --unload / --reload) ---
    action = getattr(args, "load", None) and ("load", args.load) \
        or getattr(args, "unload", None) and ("unload", args.unload) \
        or getattr(args, "reload", None) and ("reload", args.reload)
    if action:
        verb, name = action
        if verb == "load":
            ok = pm.load(name) is not None
        elif verb == "unload":
            ok = pm.unload(name)
        else:  # reload
            pm.unload(name)
            pm.discover(force=True)
            ok = pm.load(name) is not None
        if ok:
            print(green(f"\u2713 {verb} '{name}' OK (state: {pm.get_plugin_state(name)})"))
            return 0
        print(red(f"\u2717 {verb} '{name}' failed"))
        for w in pm.warnings[-5:]:
            print(dim(f"  {w}"))
        return 1

    # --- Machine-readable listing (--json) ---
    if getattr(args, "json", False):
        import json as _json
        manifests = pm.discover()
        out = []
        for m in sorted(manifests, key=lambda x: x.name):
            out.append({
                "name": m.name,
                "version": m.version,
                "display_name": m.display_name,
                "description": m.description,
                "author": m.author,
                "license": m.license,
                "type": m.type,
                "entrypoint": m.entrypoint,
                "depends": m.depends,
                "optional_depends": m.optional_depends,
                "config": m.config,
                "provides": m.provides,
                "compatibility": m.compatibility,
                "schema": m.schema,
                "root": str(m.dir) if m.dir else None,
                "root_kind": m.root_kind,
                "legacy_fields_used": m.legacy_fields_used,
                "state": pm.get_plugin_state(m.name),
            })
        print(_json.dumps(out, indent=2))
        return 0

    # Discover all plugins
    manifests = pm.discover()

    if not manifests:
        print(yellow("No plugins found."))
        print(dim("  Plugins should be in agentkthx/plugins/<name>/plugin.json,"))
        print(dim(f"  ~/.agentkthx/plugins/, or $AGENTKTHX_PLUGIN_PATH"))
        return 0

    print(bold(bright_cyan("PLUGINS")))
    print()

    # Table header
    name_w = max(len(m.name) for m in manifests) + 4
    name_w = max(name_w, 14)
    type_w = 10
    ver_w = 10

    header = (
        pad_colored(bold("Name"), name_w) +
        pad_colored(bold("Type"), type_w) +
        pad_colored(bold("Version"), ver_w) +
        bold("Description")
    )
    print(header)
    print(dim("  " + "-" * (name_w + type_w + ver_w + 40)))

    for m in sorted(manifests, key=lambda x: x.name):
        state = pm.get_plugin_state(m.name)
        if state == "loaded":
            status = green("●")
        elif state == "failed":
            status = red("✗")
        else:
            status = dim("○")
        desc = m.description[:60] + ("..." if len(m.description) > 60 else "")

        line = (
            pad_colored(f"{status} {m.name}", name_w) +
            pad_colored(cyan(m.type), type_w) +
            pad_colored(dim(m.version), ver_w) +
            desc
        )
        print(line)

        if getattr(args, "verbose", False):
            if m.depends:
                print(f"    {dim('depends:')} {', '.join(m.depends)}")
            if m.provides.get("backends"):
                print(f"    {dim('backends:')} {', '.join(m.provides['backends'].keys())}")
            if m.provides.get("cli_commands"):
                print(f"    {dim('cli_commands:')} {', '.join(m.provides['cli_commands'])}")
            if m.provides.get("tools"):
                print(f"    {dim('tools:')} {', '.join(m.provides['tools'])}")
            if m.provides.get("hooks"):
                print(f"    {dim('hooks:')} {', '.join(m.provides['hooks'].keys())}")
            entry = m.entrypoint or m.name
            print(f"    {dim('entrypoint:')} {entry}")
            if m.dir:
                print(f"    {dim('root:')} {m.dir} ({m.root_kind})")
            if m.legacy_fields_used:
                print(f"    {yellow('deprecated top-level fields:')} {', '.join(m.legacy_fields_used)}")
            if state == "failed":
                print(f"    {red('error:')} {pm._failed.get(m.name, 'unknown')}")

    print()
    n_loaded = sum(1 for m in manifests if pm.is_loaded(m.name))
    print(dim(f"  {len(manifests)} plugin(s) found, {n_loaded} loaded"))

    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Update AgentKthx to the latest version from GitHub.

    On PEP 668 externally-managed-environment errors (Debian/Ubuntu/Fedora
    system Python), prompts the user once with a y/n to retry with
    ``--break-system-packages``. Never silently enables the flag — the user
    always has to opt in after seeing the failure.
    """
    print(f"{bright_cyan('\u2696 AgentKthx')} - Updating from GitHub...")
    base_cmd = [
        sys.executable, "-m", "pip", "install",
        "git+https://github.com/VTSTech/AgentKthx.git", "--force-reinstall",
    ]
    print(f"{dim('Running:')} {' '.join(base_cmd[1:])}")
    print()

    import subprocess as sp
    result = sp.run(base_cmd, capture_output=True, text=True)

    # PEP 668 detection: pip exits non-zero with a stderr mention of
    # "externally-managed-environment" on Debian/Ubuntu/Fedora system
    # Python installs. We do NOT silently add --break-system-packages —
    # we surface the failure and ask the user explicitly.
    if result.returncode != 0 and _is_externally_managed_error(result.stderr):
        print(f"{red('\u2717 Update failed.')}")
        print(f"{yellow('This Python environment is externally managed (PEP 668).')}")
        print(f"{dim('The system Python on Debian/Ubuntu/Fedora blocks pip installs to')} "
              f"{dim('protect the OS package manager — overriding it risks breaking the OS.')}")
        print()
        try:
            choice = input(f"  {dim('Retry with')} --break-system-packages{dim('? [y/N]')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice in ("y", "yes"):
            retry_cmd = base_cmd + ["--break-system-packages"]
            print()
            print(f"{dim('Running:')} {' '.join(retry_cmd[1:])}")
            print()
            result = sp.run(retry_cmd, capture_output=True, text=True)
        else:
            print(f"{dim('Skipped. Use a venv, or re-run with --break-system-packages manually.')}")
            return 1

    if result.returncode == 0:
        print(f"{green('\u2713 Updated successfully!')}")
        # Show the installed version
        try:
            version_result = sp.run(
                [sys.executable, "-m", "agentkthx", "version"],
                capture_output=True,
                text=True,
            )
            if version_result.returncode == 0 and version_result.stdout.strip():
                print(version_result.stdout.strip())
        except Exception:
            pass
    else:
        print(f"{red('\u2717 Update failed.')}")
        if result.stderr:
            print()
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"  {dim(line)}")
        return 1

    return 0


def _is_externally_managed_error(stderr: str) -> bool:
    """Return True if pip stderr indicates a PEP 668 externally-managed env.

    Matches the actual phrases pip emits under PEP 668 on Debian, Ubuntu,
    Fedora, and downstream distros. Conservative matcher — only fires on
    the real externally-managed-environment signal, not on unrelated pip
    failures (auth, network, missing package, etc.).
    """
    if not stderr:
        return False
    text = stderr.lower()
    # The PEP 668 marker phrase — appears in pip's stderr verbatim.
    if "externally-managed-environment" in text:
        return True
    # Spaced variant — older / reworded phrasings on some distros.
    if "externally managed environment" in text:
        return True
    # Even looser: "This environment is externally managed" (Debian's
    # human-readable explanation line). We require both "externally" and
    # "managed" near "environment" to avoid false positives.
    if "externally" in text and "managed" in text and "environment" in text:
        return True
    # The hint pip appends pointing the user at --break-system-packages.
    # We treat the hint alone as a positive signal because some distros
    # reword the main error but keep the hint verbatim.
    if "--break-system-packages" in text and "pep 668" in text:
        return True
    return False


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    
    # Load plugins early so that CLI arguments can include plugin backends
    try:
        from .plugins import get_plugin_manager
        pm = get_plugin_manager()
        pm.load_all()  # Load all plugins to register backends
    except Exception as e:
        # Plugin loading should not prevent CLI from working
        pass

    # on_shutdown: emit once before interpreter exit (spec §Hooks, §Lifecycle)
    try:
        import atexit as _atexit
        from .plugins import get_plugin_manager as _get_pm
        _pm_ref = _get_pm(init=False)

        def _emit_on_shutdown_once():
            try:
                if _pm_ref is not None and not getattr(_pm_ref, "_shutdown_emitted", False):
                    _pm_ref._shutdown_emitted = True
                    _pm_ref.emit("on_shutdown", {})
            except Exception:
                pass

        _atexit.register(_emit_on_shutdown_once)
    except Exception:
        pass
    
    parser = create_parser()

    # Discover plugin-provided CLI commands, add as subparsers, and mark with *
    _plugin_cli_handlers: dict[str, Callable] = {}
    try:
        from .plugins import get_plugin_manager
        pm = get_plugin_manager()
        manifests = pm.discover()

        if manifests:
            plugin_commands = set()
            for m in manifests:
                for cmd in m.provides.get("cli_commands", []):
                    plugin_commands.add(cmd)

            if plugin_commands:
                # Use the stashed _SubParsersAction (not parser._subparsers, which
                # is an _ArgumentGroup, not the subparsers registry).
                subparsers_action = getattr(parser, "_subparsers_action", None)
                if subparsers_action is not None:
                    for cmd_name in plugin_commands:
                        if cmd_name in subparsers_action.choices:
                            # Already exists as a native subparser — mark with *
                            # Help text lives in _choices_actions, not on the parser
                            for ca in subparsers_action._choices_actions:
                                if ca.dest == cmd_name:
                                    ca.help = f"* {ca.help} [plugin]"
                                    break
                        else:
                            # Add a new subparser for this plugin command
                            subparsers_action.add_parser(
                                cmd_name, help=f"* {cmd_name} [plugin]"
                            )

                # Load all plugins so their register_cli_command() handlers are wired
                pm.load_all()

                # Collect the handlers for dispatch
                for cmd_name, cmd_info in pm.get_cli_commands().items():
                    _plugin_cli_handlers[cmd_name] = cmd_info["handler"]
    except Exception as e:
        import sys
        print(f"[PluginManager] Warning: plugin CLI discovery failed: {e}", file=sys.stderr)

    args = parser.parse_args(argv)

    if args.command is None:
        print_banner()
        parser.print_help()
        return 0

    # Update check — daily-cached PyPI query, silent on failure / opt-out
    # (AGENTKTHX_NO_UPDATE_CHECK=1). `version` does its own inline check;
    # `update` obviously doesn't need one.
    if args.command not in ("version", "update"):
        _run_update_check()

    commands = {
        "run": cmd_run,
        "chat": cmd_chat,
        "agent": cmd_agent,
        "models": cmd_models,
        "tools": cmd_tools,
        "test": cmd_test,
        "turbo": cmd_turbo,
        "version": cmd_version,
        "config": cmd_config,
        "modelfile": cmd_modelfile,
        "skills": cmd_skills,
        "soul": cmd_soul,
        "sessions": cmd_sessions,
        "plugins": cmd_plugins,
        "update": cmd_update,
    }

    handler = commands.get(args.command)
    if handler is None:
        # Check plugin-provided CLI commands
        handler = _plugin_cli_handlers.get(args.command)

    if handler:
        rc = handler(args)
        # Post-run notice (pip-style) for non-interactive commands. Chat already
        # printed it under the banner; version/update never stashed a result.
        # --json invocations stay machine-readable — no notice, either stream.
        if args.command != "chat" and not getattr(args, "json", False):
            _print_update_notice()
        return rc

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())