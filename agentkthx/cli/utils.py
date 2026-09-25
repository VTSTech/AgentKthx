"""CLI utilities: model-pattern resolution, step printing, tool-cache helpers.

Extracted verbatim from cli.py in R07.00 Phase 8. Note: _load_tool_cache,
_save_tool_cache and _get_cloud_model_size have no callers anywhere in the
codebase (R06.0 legacy, kept verbatim pending a dead-code sweep)."""

from __future__ import annotations

import json
import os
import sys

from ..backends import get_backend
from ..colors import yellow, dim, green, cyan, bright_green, red
from ..model_discovery import match_models, get_models
from pathlib import Path




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
    from ..core.types import StepResultType

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
