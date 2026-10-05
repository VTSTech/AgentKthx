"""argparse parser construction for the agentkthx CLI.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse
import os

from ..backends import get_backend_choices
from ..shared_args import _parse_token_size, add_agent_args

# ============================================================================
# Help formatting
# ============================================================================


class SortedHelpFormatter(argparse.HelpFormatter):
    """``-h`` formatter that lists options alphabetically (R07.19 follow-up #6).

    ``chat -h`` (and every other ``-h``) rendered options in registration
    order, which made flags hard to find once ``add_agent_args()`` grew
    past 30 entries. This formatter sorts the "options:" listing AND the
    ``usage:`` line by each flag's primary long name (``-m, --model``
    sorts under "model"); short-only flags sort by their own name and the
    bare ``--`` passthrough sorts first.

    Positional arguments keep registration order everywhere — their order
    is semantic (``run prompt``, ``soul path``, ``turbo start model``).
    Subcommand listings are kept alphabetical by the registration order
    in create_parser().
    """

    def add_arguments(self, actions):
        """Render one help section with its optionals sorted A→Z."""
        super().add_arguments(sorted(actions, key=self._sort_key))

    def add_usage(self, usage, actions, groups, prefix=None):
        """Render the ``usage:`` line with its optionals sorted A→Z."""
        super().add_usage(usage, sorted(actions, key=self._sort_key), groups, prefix)

    @staticmethod
    def _sort_key(action):
        """(positionals-first, name) key; positionals keep insertion order."""
        if not action.option_strings:
            return (0, ())  # positional: stable sort preserves semantic order
        longs = [s for s in action.option_strings if s.startswith("--")]
        primary = (longs or action.option_strings)[0]
        return (1, (primary.lstrip("-").lower(),))


def apply_sorted_help(parser: argparse.ArgumentParser) -> None:
    """Apply SortedHelpFormatter to *parser* and every subparser below it.

    ``formatter_class`` is read at help-render time, so setting it after
    all add_argument()/add_parser() calls is sufficient. Subcommand
    registries are re-sorted in place so late-registered (plugin) commands
    also render alphabetically. Called at the end of create_parser() and
    again in main() once plugin CLI subparsers are registered (idempotent).
    """
    parser.formatter_class = SortedHelpFormatter
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            # Re-sort choices + the pseudo-action listing IN PLACE —
            # choices IS _name_parser_map (rebinding would desync parse
            # lookup from the rendered metavar), so plugin commands
            # appended by main() land in alphabetical position instead
            # of at the end of the {…} metavar and command list.
            ordered = sorted(action.choices.items())
            action.choices.clear()
            action.choices.update(ordered)
            action._choices_actions.sort(key=lambda ca: ca.dest)
            for sub in action.choices.values():
                apply_sorted_help(sub)


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
    # R07.19: Primary User — the name rendered in the REPL prompt instead of
    # "You:". Setting it skips the interactive "Primary User [...]" naming
    # prompt at session start (AGENTKTHX_USER env var does the same).
    chat_parser.add_argument(
        "-u",
        "--user",
        default=None,
        help="Primary User name for the chat prompt (skips the startup naming "
        "prompt; falls back to AGENTKTHX_USER, then the OS login name)",
    )

    # Config command
    config_parser = subparsers.add_parser("config", help="Show current configuration")
    config_parser.add_argument("--urls", action="store_true", help="Show only backend URLs")
    config_parser.add_argument(
        "--full",
        action="store_true",
        help="Show all configuration variables including dataclass defaults",
    )

    # Models command
    models_parser = subparsers.add_parser("models", help="List available models")
    models_parser.add_argument(
        "--backend", choices=get_backend_choices(), default=None, help="Backend to use"
    )
    models_parser.add_argument(
        "--api",
        choices=["openre", "openai", "jev"],
        default=None,
        dest="api_mode",
        help="API mode hint for the backend (R07.19 follow-up #10: tool support is a single API-mode-independent capability check, so this no longer splits the table)",
    )
    models_parser.add_argument(
        "--tool-support",
        action="store_true",
        help="Re-test tool calling + thinking support (skips already-cached models)",
    )
    models_parser.add_argument(
        "--no-cache", action="store_true", help="Ignore cached results and re-test all models"
    )
    models_parser.add_argument(
        "--acp", action="store_true", help="Enable ACP logging to Agent Control Panel"
    )
    models_parser.add_argument(
        "--acp-url", default=None, help="ACP server URL (default: from config)"
    )
    # R07.20: persistent JSON model-catalog cache management (no API calls)
    models_parser.add_argument(
        "--persist",
        metavar="MODEL",
        default=None,
        help="Mark a model persistent in the JSON model cache (never expires, never cleared)",
    )
    models_parser.add_argument(
        "--unpersist",
        metavar="MODEL",
        default=None,
        help="Remove the persistent flag from a model in the JSON model cache",
    )
    models_parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Drop the backend's cached model catalog (persistent models survive)",
    )
    models_parser.add_argument(
        "--cache-status",
        action="store_true",
        help="Show model-cache status (path, TTL, per-backend freshness) and exit",
    )

    # Modelfile command
    modelfile_parser = subparsers.add_parser("modelfile", help="Show model's Modelfile info")
    modelfile_parser.add_argument("-m", "--model", default=None, help="Model to inspect")
    modelfile_parser.add_argument(
        "--backend", choices=get_backend_choices(), default=None, help="Backend to use"
    )

    # MCP command (R07.22 Phase 1.6) — manage MCP client config + probes
    mcp_parser = subparsers.add_parser(
        "mcp", help="Manage MCP (Model Context Protocol) client servers"
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", help="MCP subcommand")
    mcp_sub.add_parser("list", help="List configured MCP servers (from ~/.agentkthx/mcp.json)")
    mcp_init = mcp_sub.add_parser(
        "init", help="Write a commented example ~/.agentkthx/mcp.json (refuses to overwrite)"
    )
    mcp_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing mcp.json (you will lose current config)",
    )
    mcp_init.add_argument(
        "--path",
        default=None,
        help="Write to this path instead of ~/.agentkthx/mcp.json",
    )
    mcp_probe = mcp_sub.add_parser(
        "probe", help="Connect to one MCP server, list its tools, then disconnect"
    )
    mcp_probe.add_argument("name", help="Server name (from `mcp list`) to probe")
    mcp_probe.add_argument(
        "--call",
        nargs=2,
        metavar=("TOOL", "JSON_ARGS"),
        default=None,
        help="After probing, call one tool with a JSON arguments object and print the result",
    )
    mcp_probe.add_argument(
        "--config",
        default=None,
        help="Path to an MCP config file (default: ~/.agentkthx/mcp.json)",
    )
    # R07.23: `mcp search` — live search across npm, with a 10m TTL cache.
    # Replaces the curated offline catalog that briefly shipped mid-release
    # (catalog.py deleted). The catalog approach was too brittle — npm packages
    # change, new ones appear, and a Python literal can't keep up. Live search
    # + cache gives us freshness without paying the network cost on every invocation.
    #
    # R07.23 follow-up: GitHub search was removed from `mcp search` — too many
    # non-stdio results (Java/Go/Rust repos, Ghidra/IDA extensions, browser
    # plugins) that look like MCP servers but can't be launched as subprocesses.
    # GitHub installs still work via `mcp install github:owner/repo` when the
    # operator has manually verified the repo is stdio-capable.
    mcp_search = mcp_sub.add_parser(
        "search",
        help="Search npm for MCP servers (live, 10m cache)",
    )
    mcp_search.add_argument(
        "query",
        nargs="?",
        default="mcp",
        help="Substring to search (default: 'mcp' — lists popular MCP servers). "
        "Matched against package name, description, and tags.",
    )
    mcp_search.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Max results (default: 25, max: 250).",
    )
    mcp_search.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the cache and force a fresh fetch. The cache is updated with the "
        "new results so subsequent calls within 10m are still fast.",
    )
    mcp_search.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (one entry per object) instead of the human-readable table.",
    )
    # R07.23: `mcp install` — fetch live metadata for one server and write it
    # directly to ~/.agentkthx/mcp.json. Always live (no offline catalog fallback).
    # Overwrites existing entries with the same name by default (per maintainer spec).
    #
    # Name format (R07.23 redesign — avoids collisions since many MCP servers
    # share project names like "ghidra", "git", "memory"):
    #   - @scope/package         → npm install (e.g. @modelcontextprotocol/server-filesystem)
    #   - github:owner/repo      → GitHub install (e.g. github:LaurieWired/GhidraMCP)
    #   - owner/repo (no prefix)  → also GitHub, convenience shorthand
    #   - bare-name               → npm search, prefers @modelcontextprotocol/* hits
    mcp_install = mcp_sub.add_parser(
        "install",
        help="Install an MCP server to ~/.agentkthx/mcp.json (live fetch)",
    )
    mcp_install.add_argument(
        "name",
        help="Server to install. Formats: '@scope/package' (npm), "
        "'github:owner/repo' (GitHub), 'owner/repo' (GitHub shorthand), or "
        "'bare-name' (resolves via npm search).",
    )
    mcp_install.add_argument(
        "--as",
        dest="as_name",
        default=None,
        help="Override the short name written to mcp.json (default: derived from package).",
    )
    mcp_install.add_argument(
        "--command",
        dest="launch_command",
        default=None,
        help="Override the launch command (default: 'npx' for npm, 'uvx' for GitHub).",
    )
    mcp_install.add_argument(
        "--args",
        default=None,
        help="Override the launch args as a single string (split on spaces). "
        "Example: --args='-y @modelcontextprotocol/server-filesystem /tmp'.",
    )
    mcp_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the snippet that would be written to mcp.json, but don't write it.",
    )
    mcp_install.add_argument(
        "--json",
        action="store_true",
        help="Emit the resulting mcp.json entry as JSON instead of writing to the file.",
    )
    mcp_install.add_argument(
        "--config",
        default=None,
        help="Path to the mcp.json file (default: ~/.agentkthx/mcp.json).",
    )
    # R07.24 (SEC-20): --no-overwrite refuses to clobber an existing entry.
    # Inverse of `mcp init --force`. Fails with rc=5 if an entry with the
    # same short name already exists in mcp.json. Pairs with --dry-run to
    # preview without writing.
    mcp_install.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Refuse to overwrite an existing mcp.json entry with the same name "
        "(exit code 5 if it exists). Default is to overwrite — use this flag "
        "when re-installing after manual edits to mcp.json to avoid losing them.",
    )
    # R07.23: `mcp uninstall <name>` — remove a server entry from mcp.json.
    # Pairs with `mcp install` so users don't have to edit mcp.json by hand
    # when an install doesn't work out (e.g. Java-only repos that can't be
    # launched as a stdio subprocess).
    mcp_uninstall = mcp_sub.add_parser(
        "uninstall",
        help="Remove a server entry from ~/.agentkthx/mcp.json",
    )
    mcp_uninstall.add_argument(
        "name",
        help="Server name (from `mcp list`) to remove from mcp.json.",
    )
    mcp_uninstall.add_argument(
        "--config",
        default=None,
        help="Path to the mcp.json file (default: ~/.agentkthx/mcp.json).",
    )

    # Plugins command (v0.2 spec §CLI integration) — registered before run
    # so the root -h subcommand list stays alphabetical (R07.19 follow-up #6)
    plugins_parser = subparsers.add_parser("plugins", help="List and manage plugins")
    plugins_parser.add_argument("--verbose", action="store_true", help="Show detailed plugin info")
    plugins_parser.add_argument("--load", metavar="NAME", help="Load a single plugin by name")
    plugins_parser.add_argument("--unload", metavar="NAME", help="Unload a loaded plugin")
    plugins_parser.add_argument("--reload", metavar="NAME", help="Unload then load a plugin")
    plugins_parser.add_argument("--json", action="store_true", help="Machine-readable listing")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a single prompt")
    run_parser.add_argument("prompt", help="The prompt to process")
    add_agent_args(run_parser, tools_default="calculator")
    # --stream / --no-stream now come from add_agent_args() (shared_args.py)
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    run_parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress header and summary"
    )

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
    soul_parser.add_argument(
        "--level",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Progressive disclosure level (1=quick, 2=full, 3=deep)",
    )
    soul_parser.add_argument("--validate", action="store_true", help="Run validation checks")
    soul_parser.add_argument("--prompt", action="store_true", help="Show generated system prompt")

    # Souls command (R07.19 follow-up #7) — listing surface for the bundled
    # Soul Spec packages; registered after soul so the root -h subcommand
    # list stays alphabetical (soul < souls < test).
    souls_parser = subparsers.add_parser("souls", help="List available souls")
    souls_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Show details for one bundled soul by name (e.g. kthx-trading)",
    )

    # Test command
    test_parser = subparsers.add_parser("test", help="Run diagnostic tests")
    test_parser.add_argument(
        "test_id",
        nargs="?",
        default="all",
        help="Test to run: 00, 01, 02, ... 11, or 'all' (default: all)",
    )
    test_parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="Model to test (supports patterns: 'qwen', 'g', ':0.5b')",
    )
    test_parser.add_argument(
        "--backend", choices=get_backend_choices(), default=None, help="Backend to use"
    )
    test_parser.add_argument(
        "--api",
        choices=["openre", "openai", "jev"],
        default="openre",
        dest="api_mode",
        help="API mode: 'openre' (OpenResponses), 'openai' (Chat-Completions), or 'jev' (System-One decision mode)",
    )
    test_parser.add_argument("--debug", action="store_true", help="Enable debug output")
    test_parser.add_argument("--list", action="store_true", help="List available tests")
    test_parser.add_argument(
        "--acp", action="store_true", help="Enable ACP logging to Agent Control Panel"
    )
    test_parser.add_argument(
        "--acp-url", default=None, help="ACP server URL (default: http://localhost:8766)"
    )
    test_parser.add_argument(
        "--use-mf-sys",
        action="store_true",
        dest="use_modelfile_system",
        help="Use the model's Modelfile system prompt instead of custom test prompts",
    )
    test_parser.add_argument(
        "--num-ctx",
        type=_parse_token_size,
        default=None,
        dest="num_ctx",
        help="Context window size in tokens. Accepts plain ints (131072) or "
        "human-friendly forms like 128k, 1m, 2g. R07.18+.",
    )
    test_parser.add_argument(
        "--num-predict",
        type=_parse_token_size,
        default=None,
        dest="num_predict",
        help="Maximum tokens to generate. Accepts plain ints (2048) or "
        "human-friendly forms like 2k, 4k. R07.18+.",
    )
    test_parser.add_argument(
        "--num-batch",
        type=int,
        default=None,
        dest="num_batch",
        help="Prompt-processing batch size (Ollama per-request option; "
        "llama-server/TurboQuant use 'turbo start --batch-size N' at server "
        "start; cloud backends ignore it). Default: backend default.",
    )
    test_parser.add_argument(
        "--repeat-penalty",
        type=float,
        default=None,
        dest="repeat_penalty",
        help="Repetition penalty (llama.cpp native, >1.0 discourages repetition). "
        "Ollama + llama-server/TurboQuant/BitNet only; cloud backends drop it.",
    )
    test_parser.add_argument(
        "--repeat-last-n",
        type=int,
        default=None,
        dest="repeat_last_n",
        help="Tokens to consider for repetition penalty (llama.cpp native). "
        "0 = full context, -1 = model default. Ollama + llama-server only.",
    )
    test_parser.add_argument(
        "--temp",
        "--temperature",
        type=float,
        default=None,
        dest="temperature",
        help="Sampling temperature 0.0-2.0 (default: model-specific)",
    )
    test_parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        dest="top_p",
        help="Nucleus sampling probability 0.0-1.0 (default: model-specific)",
    )
    test_parser.add_argument(
        "--timeout", type=int, default=None, help="Request timeout in seconds (default: 120)"
    )
    test_parser.add_argument(
        "--warmup",
        action="store_true",
        help="Send warmup request before testing (avoids cold start timeout)",
    )
    test_parser.add_argument(
        "--force-react",
        nargs="?",
        const="on",
        default=None,
        choices=["on", "off", "auto"],
        # MAINT-25 (R07.21 CLOSED): tri-state — bare flag = 'on' (backwards compat).
        help="Force ReAct mode: 'on' (default bare), 'off' forces native, 'auto' preserves auto-detection",
    )
    test_parser.add_argument(
        "--soul", default=None, help="Path to Soul Spec package (disabled by default)"
    )
    test_parser.add_argument(
        "--soul-level",
        type=int,
        default=2,
        choices=[1, 2, 3],
        help="Soul progressive disclosure level (1=quick, 2=full, 3=deep)",
    )
    test_parser.add_argument(
        "--tools-only",
        action="store_true",
        dest="tools_only",
        help="Only run Phase 1 (direct tool tests, no model)",
    )
    test_parser.add_argument(
        "--model-only",
        action="store_true",
        dest="model_only",
        help="Only run Phase 2 (model tool calling tests)",
    )
    test_parser.add_argument(
        "--quick", action="store_true", help="Quick mode: only run 5 fastest tests per test module"
    )

    # Tools command — registered before turbo so the root -h subcommand
    # list stays alphabetical (R07.19 follow-up #6)
    subparsers.add_parser("tools", help="List available tools")

    # Turbo command
    turbo_parser = subparsers.add_parser(
        "turbo", help="TurboQuant server management (start/stop/list Ollama models)"
    )
    turbo_sub = turbo_parser.add_subparsers(dest="turbo_command", help="TurboQuant subcommand")

    # turbo list
    turbo_list_parser = turbo_sub.add_parser(
        "list", help="List Ollama models available for TurboQuant"
    )
    turbo_list_parser.add_argument(
        "--all", action="store_true", help="Show all models, including missing blobs"
    )
    turbo_list_parser.add_argument(
        "--ollama-dir", default=None, help="Override Ollama models directory"
    )

    # turbo start
    turbo_start_parser = turbo_sub.add_parser(
        "start", help="Start TurboQuant server with an Ollama model"
    )
    turbo_start_parser.add_argument(
        "model", help="Ollama model name (e.g. qwen2.5:7b) or path to GGUF file"
    )
    turbo_start_parser.add_argument(
        "--server", default=None, help="Path to llama-server binary (env: TURBOQUANT_SERVER_PATH)"
    )
    turbo_start_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Server port (default: {os.environ.get('TURBOQUANT_PORT', '8764')})",
    )
    turbo_start_parser.add_argument(
        "--ctx",
        type=int,
        default=None,
        help="Context window (default: model's context_length from the Ollama "
        "catalog, falls back to TURBOQUANT_CTX env or 8192)",
    )
    turbo_start_parser.add_argument(
        "--num-predict",
        type=int,
        default=None,
        help="Max tokens to predict (default: ctx // 32 per R06.55 empirical "
        "finding; pass 0 to disable the cap and let the server use its "
        "own default of -1 / unlimited)",
    )
    turbo_start_parser.add_argument(
        "--turbo-k",
        default=None,
        choices=["q8_0", "q4_0", "turbo2", "turbo3", "turbo4", "f16"],
        help="K cache type (default: auto-detected)",
    )
    turbo_start_parser.add_argument(
        "--turbo-v",
        default=None,
        choices=["q8_0", "q4_0", "turbo2", "turbo3", "turbo4", "f16"],
        help="V cache type (default: auto-detected)",
    )
    turbo_start_parser.add_argument(
        "--flash-attn",
        choices=["on", "off", "auto"],
        default=None,
        help="Flash attention mode (llama-server's -fa flag). 'on' forces FA, "
        "'off' disables it, 'auto' lets the server decide (server default is "
        "'auto'). Not passing --flash-attn means no -fa flag is sent and the "
        "server uses its own default.",
    )
    turbo_start_parser.add_argument(
        "--sparsity", type=float, default=0.0, help="Sparse V decoding threshold (0.0=off)"
    )
    turbo_start_parser.add_argument(
        "--threads", type=int, default=0, help="CPU thread count for generation (0=auto, -t)"
    )
    turbo_start_parser.add_argument(
        "--threads-batch",
        type=int,
        default=0,
        help="CPU thread count for prompt/batch processing (0=same as --threads, -tb). "
        "Often set 2x higher than --threads since prompt eval is more parallelizable.",
    )
    turbo_start_parser.add_argument(
        "--batch-size",
        type=int,
        default=0,
        help="Logical batch size for prompt processing (0=server default 2048, -b). "
        "Larger values = fewer iterations but more memory.",
    )
    turbo_start_parser.add_argument(
        "--ubatch-size",
        type=int,
        default=0,
        help="Physical ubatch size for matmul parallelism (0=server default 512, -ub). "
        "Larger values = better CPU cache utilization in matmuls.",
    )
    turbo_start_parser.add_argument(
        "--mlock",
        action="store_true",
        help="Pin model in RAM (prevent swap). Recommended for systems with "
        "tight RAM (e.g. Colab CPU 12GB) where swap = death.",
    )
    turbo_start_parser.add_argument(
        "--numa",
        choices=["distribute", "isolate", "numactl"],
        default=None,
        help="NUMA optimization mode. distribute=spread execution evenly; "
        "isolate=only use CPUs on the starting node; numactl=use numactl CPU map. "
        "Single-socket VMs (e.g. Colab) sometimes still benefit from 'distribute'.",
    )
    turbo_start_parser.add_argument(
        "--no-wait", action="store_true", help="Don't wait for server to be ready"
    )
    turbo_start_parser.add_argument(
        "--timeout", type=int, default=120, help="Max seconds to wait for readiness (default: 120)"
    )
    turbo_start_parser.add_argument(
        "--", dest="extra_args", nargs="*", help="Extra arguments to pass to llama-server"
    )

    # turbo status (registered before stop so turbo -h stays alphabetical)
    turbo_sub.add_parser("status", help="Show TurboQuant server status")

    # turbo stop
    turbo_stop_parser = turbo_sub.add_parser("stop", help="Stop the running TurboQuant server")
    turbo_stop_parser.add_argument("--force", action="store_true", help="Force kill (SIGKILL)")

    # Update command
    subparsers.add_parser("update", help="Update AgentKthx to the latest version from GitHub")

    # Version command
    subparsers.add_parser("version", help="Show version information")

    # R07.19 (follow-up #6): render every -h with options sorted A→Z
    apply_sorted_help(parser)

    return parser


def _make_confirm_callback(args: argparse.Namespace):
    """
    Build a confirm_dangerous callback from CLI --confirm flag.

    When --confirm is set, the user is prompted (y/n) before any
    dangerous tool (shell, write_file, edit_file) executes.
    Returns None if --confirm is not set (no confirmation needed).
    """
    if not getattr(args, "confirm_dangerous", False):
        return None

    from ..colors import dim, green, red, yellow

    # SEC-05 (R07.08): strip ANSI escape sequences from model-controlled
    # tool names + args before printing them in the confirmation dialog.
    # A malicious prompt-injected tool call could include terminal escapes
    # (e.g. \x1b[2J to clear the screen, \x1b]0;evil\x07 to rewrite the
    # terminal title, \x1b[?1000h to enable mouse tracking). Stripping
    # ensures the user sees the actual tool name + args, not a manipulated
    # terminal state.
    _ANSI_ESCAPE_RE = __import__("re").compile(
        r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]"
    )

    def _strip_ansi(s: str) -> str:
        """Remove CSI, OSC, and other ANSI escape sequences."""
        return _ANSI_ESCAPE_RE.sub("", s)

    def _confirm(tool_name: str, args_dict: dict) -> bool:
        # Format the args for display — strip ANSI from model-controlled values
        tool_name = _strip_ansi(tool_name)
        arg_str = "  ".join(f"{k}={_strip_ansi(str(v))}" for k, v in args_dict.items())
        # Truncate very long values (e.g. file content)
        if len(arg_str) > 200:
            arg_str = arg_str[:200] + "..."
        print(f"\n{yellow('\u26a0')}  Dangerous tool: {yellow(tool_name)}")
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
