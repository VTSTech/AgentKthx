"""`agentkthx config` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import yellow, dim, green, cyan, bright_green, bright_cyan
from ...config import get_config

def cmd_config(args: argparse.Namespace) -> int:
    """Show current configuration."""
    from ...config import (
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
