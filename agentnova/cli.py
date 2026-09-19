"""
⚛️ AgentNova — CLI
Command-line interface for AgentNova R05.4.

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
from .config import get_config, AGENTNOVA_BACKEND, OLLAMA_BASE_URL
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
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀\x1b[0m  \x1b[95;1mAgentNova\x1b[0m
\x1b[96m⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀\x1b[0m  \x1b[2mAutonomous Agents with Local LLMs\x1b[0m
\x1b[96m⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀\x1b[0m
\x1b[96m⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄\x1b[0m  \x1b[2mStatus:\x1b[0m \x1b[33mAlpha\x1b[0m
\x1b[96m⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇\x1b[0m  \x1b[2mhttps://www.vts-tech.org\x1b[0m
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
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣴⠿⠛⢷⣦⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀  AgentNova
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠃⠀⠀⠀⠙⣷⡀⠀⠀⢀⣀⠀⠀⠀⠀⠀  Autonomous Agents with Local LLMs
⠀⠀⠀⣀⣀⣀⣀⣀⠀⢀⣿⠃⠀⠀⠀⠀⠀⠸⣷⠀⣰⣿⣿⣿⣆⣀⠀⠀
⠀⣰⡿⠛⠉⠉⠉⠛⠻⣿⣷⣤⣀⠀⠀⠀⣀⣤⣿⡿⠿⣿⣿⣿⠏⠛⣷⡄  Status: Alpha
⠀⣿⣇⣀⠀⠀⠀⠀⢀⣿⠅⠉⢛⣿⣶⣿⡋⠉⠘⣿⠀⠀⠉⠀⠀⠀⢸⡇  https://www.vts-tech.org
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
    """Print the AgentNova ASCII banner."""
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
        prog="agentnova",
        description="⚛️ AgentNova - Autonomous agents with local LLMs (Alpha)",
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
    models_parser.add_argument("--api", choices=["openre", "openai"], default=None, dest="api_mode",
                           help="API mode for tool support testing (default: test both)")
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
    run_parser.add_argument("--stream", action="store_true", help="Stream output")
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
    test_parser.add_argument("--api", choices=["openre", "openai"], default="openre", dest="api_mode",
                           help="API mode: 'openre' (OpenResponses) or 'openai' (Chat-Completions)")
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

    # Plugins command
    plugins_parser = subparsers.add_parser("plugins", help="List and manage plugins")
    plugins_parser.add_argument("--verbose", action="store_true", help="Show detailed plugin info")

    # Update command
    subparsers.add_parser("update", help="Update AgentNova to the latest version from GitHub")

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


def _init_acp(args: argparse.Namespace, config, agent_name: str = "AgentNova") -> tuple:
    """
    Initialize ACP plugin if requested.
    
    Returns:
        tuple: (acp_plugin or None, should_stop bool)
    """
    if not getattr(args, 'acp', False):
        return None, False
    
    try:
        from .acp_plugin import ACPPlugin
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


def _load_skills_prompt(args: argparse.Namespace) -> str | None:
    """
    Load skills specified via --skills flag and return the system prompt addition.
    
    Uses lazy import to avoid circular dependencies.
    
    Args:
        args: Parsed CLI arguments (must have 'skills' attribute)
        
    Returns:
        System prompt addition string, or None if no skills specified
    """
    skills_str = getattr(args, 'skills', None)
    if not skills_str:
        return None
    
    try:
        from .skills import SkillLoader, SkillRegistry
    except ImportError:
        print(f"{yellow('Warning:')} Skills module not available, skipping --skills")
        return None
    
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
            print(f"{yellow('Warning:')} Skill '{name}' not found (run 'agentnova skills' to list available)")
        except Exception as e:
            failed.append(name)
            print(f"{yellow('Warning:')} Failed to load skill '{name}': {e}")
    
    if loaded:
        prompt = registry.to_system_prompt_addition()
        if prompt:
            return prompt
    
    return None


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
            if os.environ.get("AGENTNOVA_DEBUG"):
                print(f"  [bitnet] Discovered model: {model}")
        else:
            model = "bitnet"
    else:
        model = config.default_model

    # Initialize backend with proper API mode
    backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    # Default API mode: cloud providers use OpenAI, local providers use OpenResponses
    from .core.types import BackendType
    if hasattr(backend, 'backend_type') and backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]:
        api_mode = getattr(args, "api_mode", "openai")
        # Re-initialize backend with correct API mode for cloud providers
        backend = get_backend(backend_name, timeout=timeout, api_mode=api_mode)
    
    # Handle truncation configuration
    truncation = getattr(args, "truncation", "auto")
    if args.debug:
        print(f"[AgentNova] Truncation mode: {truncation}")
        print(f"[AgentNova] API mode: {api_mode}")

    # Build tools
    if args.tools:
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
    skills_prompt = _load_skills_prompt(args)

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
        backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]
    )
    
    return Agent(
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
        max_steps=getattr(args, "max_steps", 10)
    )


def _get_catalog_defaults(backend, model: str) -> dict:
    """
    Get model defaults from backend catalog if available.
    
    Returns:
        dict: num_ctx and num_predict defaults from catalog
    """
    from .core.types import BackendType
    
    # Only apply catalog defaults for cloud providers
    if not hasattr(backend, 'backend_type') or backend.backend_type not in [BackendType.OPENROUTER, BackendType.ZAI]:
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
    acp, should_stop = _init_acp(args, config, "AgentNova-Run")
    if should_stop:
        return 1

    agent = _build_agent(args, config)

    # Print run info header
    if not getattr(args, 'quiet', False):
        _print_run_header(agent, args, config)

    try:
        # Enable streaming by default for cloud providers
        from .core.types import BackendType
        is_cloud_provider = (
            hasattr(agent.backend, 'backend_type') and 
            agent.backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]
        )
        
        stream = getattr(args, "stream", False) or is_cloud_provider
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
        _print_agent_steps(result, debug=agent.debug)
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


def _print_agent_steps(result, debug: bool = False) -> None:
    """Print a brief summary of each agent step (tool calls + results).

    Visible by default in chat mode so the user can see what the agent is
    doing, not just the final answer. Suppressed when debug is on
    (debug already prints verbose step-by-step output).

    Args:
        result: AgentRun returned by agent.run()
        debug: If True, the agent already printed verbose step output —
               skip the summary to avoid duplication.
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
    print()  # blank line before final answer


def cmd_chat(args: argparse.Namespace) -> int:
    """Execute the chat command."""
    config = get_config()

    # Initialize ACP if requested
    acp, should_stop = _init_acp(args, config, "AgentNova-Chat")
    if should_stop:
        return 1

    agent = _build_agent(args, config)

    _print_session_header(agent, args, config, "Chat Mode")
    print("Type '/quit' to exit, '/help' for commands\n")

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
        """Build the second footer line: backend, token usage, debug flag."""
        backend = getattr(agent.backend, 'backend_type', None)
        bname = backend.value if backend and hasattr(backend, 'value') else str(backend) if backend else '?'
        def _fmt_tok(n):
            n = int(str(n).strip())
            if n >= 1000:
                return f"{n/1000:.1f}k"
            return str(n)
        tok_str = f"\u2191{_fmt_tok(_session_tokens_in)} \u2193{_fmt_tok(_session_tokens_out)}"
        _e_be    = '\U0001f50c'
        _e_tok   = '\U0001f4c8'
        _e_dbg   = '\U0001f41b'
        parts = [
            f"{dim(_e_be)} {green(bname)}",
            f"{dim(_e_tok)} {yellow(tok_str)}",
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
        # Save cursor, move to footer area, clear + write both lines, restore
        sys.stdout.write("\033[s")                            # save cursor
        sys.stdout.write("\033[?7l")                           # disable line wrap
        # Line 1: second-to-last terminal line
        row1 = _term_size.lines - 1
        sys.stdout.write(f"\033[{row1};1H")                    # move to line 1
        sys.stdout.write("\033[2K")                            # clear entire line
        sys.stdout.write(line1)                                # write footer line 1
        # Line 2: last terminal line
        row2 = _term_size.lines
        sys.stdout.write(f"\033[{row2};1H")                    # move to line 2
        sys.stdout.write("\033[2K")                            # clear entire line
        sys.stdout.write(line2)                                # write footer line 2
        sys.stdout.write("\033[?7h")                           # re-enable line wrap
        sys.stdout.write("\033[u")                             # restore cursor
        sys.stdout.flush()

    def _position_for_input():
        """Move cursor to the bottom of the scroll region for the `You:` prompt.

        This ensures the input prompt always appears one line above the
        footer, regardless of where the previous response left the cursor.
        """
        if not _use_persistent_footer:
            return
        # Move to last line of scroll region (just above footer)
        bottom = _term_size.lines - _FOOTER_LINES
        sys.stdout.write(f"\033[{bottom};1H")
        sys.stdout.write("\033[2K")  # clear the line (remove stale text)
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
            user_input = input(f"\033[90mYou:\033[0m ").strip()
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
            print(f"  {cyan('/security')}   Show or set security mode (max|off)")
            print(f"  {cyan('/status')}     Show model, backend, tools, and memory info")
            print(f"  {cyan('/system')}     Print the current system prompt")
            print(f"  {cyan('/tools')}      List available tools with descriptions")
            print(f"  {cyan('/quit')}       Exit AgentNova")
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
            print(f"Memory turns: {yellow(str(len(agent.memory)))}")
            print(f"Debug: {green('ON') if agent.debug else red('OFF')}")
            if agent.soul:
                print(f"Soul: {cyan(agent.soul.display_name)} v{agent.soul.version}")
            continue

        # Log user message to ACP
        if acp:
            acp.log_chat("user", user_input)

        # Run with spinner (suppress spinner when debug is on — debug already prints progress)
        spinner_t = None
        if not agent.debug:
            print()  # blank line before spinner
            spinner_t = _spinner_start()
        try:
            # Enable streaming by default for cloud providers
            from .core.types import BackendType
            is_cloud_provider = (
                hasattr(agent.backend, 'backend_type') and 
                agent.backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]
            )
            
            stream = getattr(args, 'stream', False) or is_cloud_provider
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
            print(f"\n{red('Unexpected Error:')} {type(e).__name__}: {e}\n")
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
        # printed verbose step output).
        _print_agent_steps(result, debug=agent.debug)

        # Detect empty final answers — the agent ran but produced no
        # response text. This usually means the model hit a rate limit
        # or content filter mid-conversation. Surface it as an error
        # instead of showing a blank "Agent Nova: " line.
        if not result.final_answer or not result.final_answer.strip():
            print(f"\n{red('Agent Nova: (empty response)')}")
            print(yellow("  The model returned no content. This is likely a "
                         "rate limit (429) or content filter."))
            print(yellow("  Try again in a few seconds, or use /debug to see "
                         "what happened."))
        else:
            print(f"\n{bright_green('Agent Nova')}: {result.final_answer}\n")

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
    acp, should_stop = _init_acp(args, config, "AgentNova-Agent")
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
    """Get the cache directory for AgentNova."""
    # Use platform-appropriate cache directory
    if os.name == "nt":
        # Windows: %LOCALAPPDATA%\agentnova\cache
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        cache_dir = Path(base) / "agentnova" / "cache"
    else:
        # Unix: ~/.cache/agentnova
        base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
        cache_dir = Path(base) / "agentnova"
    
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
                if os.environ.get("AGENTNOVA_DEBUG"):
                    print(f"Warning: Cache file corrupted (not a dict), ignoring", file=sys.stderr)
                return {}
        except json.JSONDecodeError as e:
            # Corrupted JSON - warn in debug mode
            if os.environ.get("AGENTNOVA_DEBUG"):
                print(f"Warning: Cache file has invalid JSON: {e}", file=sys.stderr)
            # Try to remove corrupted file
            try:
                cache_file.unlink()
            except Exception:
                pass
            return {}
        except IOError as e:
            if os.environ.get("AGENTNOVA_DEBUG"):
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
    if backend_name == "openrouter":
        # OpenRouter only supports OpenAI Chat-Completions
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
    acp, _ = _init_acp(args, config, "AgentNova-Models")

    # Column widths
    NAME_W = 36
    SIZE_W = 8
    CTX_W = 12
    TOOLS_W = 12  # fits "✓ native"
    FAMILY_W = 12
    sep_len = 4 + NAME_W + SIZE_W + CTX_W + TOOLS_W + TOOLS_W + FAMILY_W + 10

    print()
    print(f"{bright_cyan('⚛ AgentNova')} - Available Models")
    print(dim(f"  Backend: {backend.base_url}"))
    if args.tool_support:
        mode_label = ", ".join(modes_to_test)
        print(dim(f"  Testing: {mode_label}"))
    if acp:
        print(f"  {dim('ACP:')} {green('✓ Connected')} ({acp.base_url})")
    print(dim("-" * sep_len))
    # Build header based on backend type
    from .core.types import BackendType
    is_cloud_provider = backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]
    
    if not is_cloud_provider:
        # Ollama and other local backends - show family column
        header = f"  {'Name':<{NAME_W}} {'Size':>{SIZE_W}}  {'Context':>{CTX_W}}  {'openre':>{TOOLS_W}}  {'openai':>{TOOLS_W}}  {'Family':<{FAMILY_W}}"
    else:
        # Cloud providers - skip family column since it's in the model name
        header = f"  {'Name':<{NAME_W}} {'Size':>{SIZE_W}}  {'Context':>{CTX_W}}  {'openre':>{TOOLS_W}}  {'openai':>{TOOLS_W}}"
        sep_len = 4 + NAME_W + SIZE_W + CTX_W + TOOLS_W + TOOLS_W + 10
    
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

        # Detect if this is a cloud provider backend
        from .core.types import BackendType
        is_cloud_provider = backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]
        
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
                print(f"\r  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}  {dim('(' + family + ')')}")
                
                # Log per-model test result to ACP
                if acp:
                    acp.model_name = name
                    re_status = results.get('openre', '?')
                    ai_status = results.get('openai', '?')
                    acp.log_chat("user", f"Testing tool support...")
                    acp.log_chat("assistant", f"openre={re_status} openai={ai_status} | {size_gb:.2f} GB | ctx {max_ctx}")
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
                from .core.types import BackendType
                if backend.backend_type in [BackendType.OPENROUTER, BackendType.ZAI]:
                    # Cloud providers have already validated tool support - always default to native
                    # Override any cached results since cloud providers have confirmed tool support
                    results = {"openre": "native", "openai": "native"}
                else:
                    # For non-cloud providers, use cached results or mark as untested
                    if not results:
                        results = {"openre": "untested", "openai": "untested"}
                
                from .core.types import BackendType
                tool_re = pad_colored(_tool_status(results.get("openre", "untested")), TOOLS_W, 'right')
                tool_ai = pad_colored(_tool_status(results.get("openai", "untested")), TOOLS_W, 'right')
                if backend.backend_type == BackendType.OLLAMA:
                    print(f"  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}  {dim('(' + family + ')')}")
                else:
                    print(f"  {name_col} {size_col}  {ctx_col}  {tool_re}  {tool_ai}")

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
    print(f"{bright_cyan('⚛ AgentNova')} - Available Tools")
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
            "module": "agentnova.examples.00_basic_agent",
        },
        "01": {
            "name": "Quick Diagnostic",
            "desc": "5-question math reasoning test",
            "module": "agentnova.examples.01_quick_diagnostic",
        },
        "02": {
            "name": "Tool Tests",
            "desc": "Calculator, shell, datetime tools",
            "module": "agentnova.examples.02_tool_test",
        },
        "03": {
            "name": "Reasoning Test",
            "desc": "Multi-step reasoning challenges",
            "module": "agentnova.examples.03_reasoning_test",
        },
        "04": {
            "name": "GSM8K Benchmark",
            "desc": "Grade school math problems",
            "module": "agentnova.examples.04_gsm8k_benchmark",
        },
        "05": {
            "name": "Common Sense",
            "desc": "Everyday knowledge and reasoning (25 questions)",
            "module": "agentnova.examples.05_common_sense",
        },
        "06": {
            "name": "Causal Reasoning",
            "desc": "Cause and effect understanding (25 questions)",
            "module": "agentnova.examples.06_causal_reasoning",
        },
        "07": {
            "name": "Logical Deduction",
            "desc": "Syllogisms and logic puzzles (25 questions)",
            "module": "agentnova.examples.07_logical_deduction",
        },
        "08": {
            "name": "Reading Comprehension",
            "desc": "Text understanding and inference (25 questions)",
            "module": "agentnova.examples.08_reading_comprehension",
        },
        "09": {
            "name": "General Knowledge",
            "desc": "Geography, science, and facts (25 questions)",
            "module": "agentnova.examples.09_general_knowledge",
        },
        "10": {
            "name": "Implicit Reasoning",
            "desc": "Understanding implied meanings (25 questions)",
            "module": "agentnova.examples.10_implicit_reasoning",
        },
        "11": {
            "name": "Analogical Reasoning",
            "desc": "Pattern and relationship mapping (25 questions)",
            "module": "agentnova.examples.11_analogical_reasoning",
        },
    }
    
    # List tests
    if args.list:
        print(f"\n{bright_cyan('⚛ AgentNova')} - Available Tests")
        print(dim("-" * 50))
        for tid, info in TESTS.items():
            print(f"  {cyan(tid)}  {info['name']:<20} {dim(info['desc'])}")
        print(dim("-" * 50))
        print(f"\n  Usage: {cyan('agentnova test 01')} or {cyan('agentnova test all')}")
        return 0
    
    # Determine which tests to run
    test_id = args.test_id.lower()
    if test_id == "all":
        tests_to_run = list(TESTS.keys())
    elif test_id in TESTS:
        tests_to_run = [test_id]
    else:
        print(f"{red('Error:')} Unknown test '{test_id}'")
        print(f"  Run {cyan('agentnova test --list')} to see available tests")
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
            from .acp_plugin import ACPPlugin
            acp_url = args.acp_url or config.acp_base_url
            acp = ACPPlugin(
                base_url=acp_url,
                agent_name="AgentNova-Test",
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
        os.environ["AGENTNOVA_DEBUG"] = "1"
    if args.backend:
        os.environ["AGENTNOVA_BACKEND"] = args.backend
    if getattr(args, 'num_ctx', None):
        os.environ["AGENTNOVA_NUM_CTX"] = str(args.num_ctx)
    
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
        os.environ["AGENTNOVA_MODEL"] = model
        
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
    print(f"   {dim('Repo:')}    {dim('https://github.com/VTSTech/AgentNova')}")
    print()

    return 0


def cmd_turbo(args: argparse.Namespace) -> int:
    """TurboQuant server management commands."""
    from .turbo import (
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
            print(f"    agentnova turbo list             List Ollama models")
            print(f"    agentnova turbo start <model>     Start TurboQuant server")
            print(f"    agentnova turbo stop              Stop TurboQuant server")
            print(f"    agentnova turbo status            Show server status")
            print()
            print(f"  {dim('No server running.')} Run {cyan('agentnova turbo list')} to see available models.")
            print()
        return 0

    if turbo_cmd == "list":
        from .backends.ollama_registry import OllamaModel

        ollama_dir = Path(args.ollama_dir) if args.ollama_dir else None
        only_existing = not getattr(args, "all", False)
        config = get_config()

        # Try API-based discovery first (same source as `agentnova models`)
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
            print(dim("  Use with AgentNova:"))
            _cmd1 = f"agentnova run --backend llama-server --model {args.model} \"<prompt>\""
            _cmd2 = f"OLLAMA_BASE_URL=http://localhost:{state.port} agentnova run \"<prompt>\""
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
            print(f"    {cyan('agentnova turbo list')}        # see available models")
            print(f"    {cyan('agentnova turbo start <model>')}  # start server")
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
        AGENTNOVA_BACKEND, DEFAULT_MODEL, NUM_CTX,
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
                ("AGENTNOVA_BACKEND", AGENTNOVA_BACKEND),
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
        print(f"{bright_cyan('AgentNova')} - Full Configuration")
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
        backend=AGENTNOVA_BACKEND,
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
    print(f"{bright_cyan('AgentNova')} - Configuration")
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
        ("AGENTNOVA_BACKEND",       "Default backend (ollama|bitnet|llama-server|zai|openrouter)"),
        ("AGENTNOVA_MODEL",         "Override default model"),
        ("AGENTNOVA_MAX_STEPS",     "Max agent steps (default: 10)"),
        ("AGENTNOVA_DEBUG",         "Enable debug output (1/true/yes)"),
        ("AGENTNOVA_VERBOSE",       "Enable verbose output (1/true/yes)"),
        ("OLLAMA_NUM_CTX",          "Ollama context window size"),
        ("AGENTNOVA_NUM_CTX",       "Generic context window size (fallback)"),
        ("AGENTNOVA_RETRY_ON_ERROR","Auto-retry failed tool calls (default: true)"),
        ("AGENTNOVA_MAX_TOOL_RETRIES","Max retries per tool call (default: 2)"),
        ("AGENTNOVA_FORCE_REACT",   "Force ReAct text-based tool calling"),
        ("AGENTNOVA_USE_MF_SYS",    "Use Modelfile system prompt"),
        ("AGENTNOVA_NUM_PREDICT",   "Max tokens to generate"),
        ("AGENTNOVA_TEMPERATURE",   "Sampling temperature"),
        ("AGENTNOVA_TOP_P",         "Nucleus sampling parameter"),
        ("AGENTNOVA_FAST",          "Fast mode preset"),
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
    print(bold(f"\n⚛️ AgentNova Modelfile") + dim(" · Written by VTSTech · https://www.vts-tech.org"))
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

    print(bold(f"\n⚛️ AgentNova Skills") + dim(" · Written by VTSTech · https://www.vts-tech.org"))

    if not skills:
        print(yellow("  No skills found."))
        print(dim("  Skills are loaded from agentnova/skills/*/SKILL.md"))
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
            _hint = 'agentnova run --session <name> "<prompt>"'
            print(f"\n  Start a session with: {cyan(_hint)}")
            return 0

        ID_W = 20
        MSGS_W = 8
        CREATED_W = 19
        UPDATED_W = 19

        print()
        print(f"{bright_cyan('⚛ AgentNova')} - Saved Sessions")
        print(f"{dim('  DB:')} ~/.agentnova/memory.db")
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
        _resume = 'agentnova run --session <name> "<prompt>"'
        print(f"\n{dim('Resume a session:')} {cyan(_resume)}")
        print(f"{dim('Delete a session:')} {cyan('agentnova sessions --delete <name>')}")

    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    """List and manage plugins."""
    from .plugins import get_plugin_manager

    pm = get_plugin_manager()

    # Discover all plugins
    manifests = pm.discover()

    if not manifests:
        print(yellow("No plugins found."))
        print(dim("  Plugins should be in agentnova/plugins/<name>/plugin.json"))
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
        loaded = pm.is_loaded(m.name)
        status = green("●") if loaded else dim("○")
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
            entry = m.entrypoint or m.name
            print(f"    {dim('entrypoint:')} {entry}")

    print()
    n_loaded = sum(1 for m in manifests if pm.is_loaded(m.name))
    print(dim(f"  {len(manifests)} plugin(s) found, {n_loaded} loaded"))

    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Update AgentNova to the latest version from GitHub."""
    print(f"{bright_cyan('⚛ AgentNova')} - Updating from GitHub...")
    print(f"{dim('Running:')} pip install git+https://github.com/VTSTech/AgentNova.git --force-reinstall")
    print()

    import subprocess as sp
    result = sp.run(
        [sys.executable, "-m", "pip", "install",
         "git+https://github.com/VTSTech/AgentNova.git", "--force-reinstall"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(f"{green('✓ Updated successfully!')}")
        # Show the installed version
        try:
            version_result = sp.run(
                [sys.executable, "-m", "agentnova", "version"],
                capture_output=True,
                text=True,
            )
            if version_result.returncode == 0 and version_result.stdout.strip():
                print(version_result.stdout.strip())
        except Exception:
            pass
    else:
        print(f"{red('✗ Update failed.')}")
        if result.stderr:
            print()
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"  {dim(line)}")
        return 1

    return 0


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
        return handler(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())