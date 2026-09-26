"""argparse parser construction for the agentkthx CLI.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import os

from ..backends import get_backend_choices
from ..shared_args import add_agent_args

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

    from ..colors import yellow, dim, green, red

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
