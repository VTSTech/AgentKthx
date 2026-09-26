"""Shared Agent construction used by the run/chat/agent/models commands.

Every new CLI flag that changes agent construction lands in _build_agent()
here (extracted verbatim from cli.py in R07.00 Phase 8)."""

from __future__ import annotations

import argparse
import os

from ..agent import Agent
from ..backends import get_backend
from ..colors import yellow, red
from ..tools import make_builtin_registry

from .parser import _make_confirm_callback




def _init_acp(args: argparse.Namespace, config, agent_name: str = "AgentKthx") -> tuple:
    """
    Initialize ACP plugin if requested.
    
    Returns:
        tuple: (acp_plugin or None, should_stop bool)
    """
    if not getattr(args, 'acp', False):
        return None, False
    
    try:
        from ..plugins.acp.acp_plugin import ACPPlugin
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
        from ..skills import SkillLoader, SkillRegistry
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
    from ..core.helpers import set_security_mode
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
    # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list with
    # backend.is_cloud — a 5th cloud backend will automatically get this behavior.
    if getattr(backend, 'is_cloud', False):
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
    # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
    # with backend.is_cloud — a 5th cloud backend will automatically stream.
    default_stream = getattr(backend, 'is_cloud', False)
    
    # Resolve --thinking CLI arg to (think, reasoning_effort)
    # off  → (False, None)   — disable thinking entirely (fastest, best for JEV)
    # auto → (None, None)    — let model decide (default)
    # low/medium/high → (True, "<level>")  — pass reasoning_effort for o-series/GLM-5
    from ..core.types import parse_thinking_arg
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
    # Only apply catalog defaults for cloud providers
    # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
    # with backend.is_cloud — a 5th cloud backend will automatically get
    # catalog-based defaults if it implements _get_model_defaults.
    if not getattr(backend, 'is_cloud', False):
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
