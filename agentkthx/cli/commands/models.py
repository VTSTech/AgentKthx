"""`agentkthx models` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...backends import OllamaBackend, get_backend
from ...colors import yellow, dim, pad_colored, green, cyan, bright_green, red, bright_cyan
from ...config import get_config

from ..utils import _tool_status

def cmd_models(args: argparse.Namespace) -> int:
    """Execute the models command."""

    # R07.00: resolve shared collaborators through the cli facade so that
    # monkeypatch.setattr(agentkthx.cli, '<name>', ...) keeps working.
    from agentkthx import cli as _cli

    from ...core.tool_cache import cache_tool_support, get_cached_tool_support
    from ...core.types import ToolSupportLevel, ApiMode
    
    config = get_config()
    backend_name = args.backend or config.backend
    api_mode_arg = getattr(args, 'api_mode', None)  # None = both modes

    # Which modes to test?  --api openai → only openai; otherwise both
    modes_to_test = [api_mode_arg] if api_mode_arg else ["openre", "openai"]
    # Always display both columns
    modes_display = ["openre", "openai"]

    # Use appropriate API mode for the backend
    # R06.57 (MAINT-05): replaced hardcoded ("openrouter", "gemini") allowlist
    # with backend.is_cloud check — a 5th cloud backend will automatically
    # default to OPENAI mode without needing to edit this list.
    from ...core.types import ApiMode
    # Instantiate a temporary backend to check is_cloud. Pass api_mode=None
    # to avoid the validation error (some backends raise ValueError on OPENRE).
    # Backends with is_cloud=True get OPENAI; local backends get OPENRE
    # (their native /api/chat mode).
    _probe_backend = get_backend(backend_name, api_mode=ApiMode.OPENAI)
    if getattr(_probe_backend, 'is_cloud', False):
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
    from ...config import OPENROUTER_FREE_ONLY, ZAI_FREE_ONLY
    
    if backend_name == "openrouter" and OPENROUTER_FREE_ONLY:
        # OpenRouter free models have :free suffix
        models = [m for m in models if m["name"].endswith(":free")]
        if not models:
            print("No free models found on OpenRouter.")
            return 0
    elif backend_name == "zai" and ZAI_FREE_ONLY:
        # Free = zero pricing in the ZAI catalog (glm-4.5-flash and
        # glm-4.7-flash ONLY — glm-5.3-flash is paid despite the name).
        # Use the backend's _is_free_model() instead of a hard-coded list
        # so catalog updates are picked up automatically.
        models = [m for m in models if getattr(backend, "_is_free_model", None) and backend._is_free_model(m["name"])]
        if not models:
            print("No free models found on ZAI.")
            return 0

    # Initialize ACP plugin if requested
    acp, _ = _cli._init_acp(args, config, "AgentKthx-Models")

    # Column widths
    NAME_W = 36
    SIZE_W = 8
    CTX_W = 12
    TOOLS_W = 12  # fits "✓ native"
    FAMILY_W = 12

    # Detect backend type early — cloud providers need different column layout
    # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
    # with backend.is_cloud — a 5th cloud backend will automatically get
    # the cloud column layout (wider NAME_W, no Size/Family columns).
    is_cloud_provider = getattr(backend, 'is_cloud', False)

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
