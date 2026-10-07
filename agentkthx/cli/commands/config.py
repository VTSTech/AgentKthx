"""`agentkthx config` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8."""

from __future__ import annotations

import argparse

from ...colors import bright_cyan, bright_green, cyan, dim, green, pad_colored, yellow
from ...config import get_config

# slug (AGENTKTHX_BACKEND value) -> row label in the Cloud Backends listing
_BACKEND_SLUG_TO_LABEL = {
    "zai": "ZAI",
    "openrouter": "OpenRouter",
    "orcarouter": "OrcaRouter",
    "gemini": "Gemini",
    "hf": "HuggingFace",
    "huggingface": "HuggingFace",
    "openai": "OpenAI",
    "mistral": "Mistral",
    "pollinations": "Pollinations",
    "nvidia": "NVIDIA",
    "nim": "NVIDIA",
}


def cmd_config(args: argparse.Namespace) -> int:
    """Show current configuration."""
    from ...config import (
        ACP_BASE_URL,
        ACP_PASS,
        ACP_USER,
        AGENTKTHX_BACKEND,
        BITNET_BASE_URL,
        DEBUG,
        DEFAULT_MODEL,
        GEMINI_API_KEY,
        GEMINI_BASE_URL,
        GEMINI_DEFAULT_MODEL,
        GEMINI_FREE_ONLY,
        GEMINI_SERVICE_TIER,
        GEMINI_THINKING_LEVEL,
        HF_BASE_URL,
        HF_DEFAULT_MODEL,
        HF_FREE_FALLBACK_MODEL,
        HF_FREE_ONLY,
        HF_PROVIDER_POLICY,
        HF_TOKEN,
        MAX_STEPS,
        MAX_TOOL_RETRIES,
        MISTRAL_API_KEY,
        MISTRAL_BASE_URL,
        MISTRAL_DEFAULT_MODEL,
        MISTRAL_FREE_FALLBACK_MODEL,
        MISTRAL_FREE_ONLY,
        MISTRAL_SAFE_PROMPT,
        MISTRAL_SERVICE_TIER,
        NUM_CTX,
        NVIDIA_API_KEY,
        NVIDIA_BASE_URL,
        NVIDIA_DEFAULT_MODEL,
        NVIDIA_FREE_ONLY,
        OLLAMA_BASE_URL,
        OPENAI_API_KEY,
        OPENAI_BASE_URL,
        OPENAI_DEFAULT_MODEL,
        OPENAI_FREE_FALLBACK_MODEL,
        OPENAI_FREE_ONLY,
        OPENAI_ORGANIZATION_ID,
        OPENAI_PROJECT_ID,
        OPENAI_REASONING_EFFORT,
        OPENAI_SERVICE_TIER,
        OPENROUTER_API_KEY,
        OPENROUTER_BASE_URL,
        OPENROUTER_DEFAULT_MODEL,
        OPENROUTER_FREE_ONLY,
        ORCAROUTER_API_KEY,
        ORCAROUTER_BASE_URL,
        ORCAROUTER_DEFAULT_MODEL,
        ORCAROUTER_FALLBACK_MODELS,
        ORCAROUTER_FREE_FALLBACK_MODEL,
        ORCAROUTER_FREE_ONLY,
        ORCAROUTER_INCLUDE_COST,
        POLLINATIONS_ANON_CATALOG,
        POLLINATIONS_API_KEY,
        POLLINATIONS_BASE_URL,
        POLLINATIONS_DEFAULT_MODEL,
        POLLINATIONS_FALLBACK_MODEL,
        POLLINATIONS_FREE_ONLY,
        POLLINATIONS_SAFE,
        RETRY_ON_ERROR,
        TURBOQUANT_BASE_URL,
        TURBOQUANT_CTX,
        TURBOQUANT_PORT,
        TURBOQUANT_SERVER_PATH,
        VERBOSE,
        ZAI_API_KEY,
        ZAI_BASE_URL,
        ZAI_FREE_FALLBACK_MODEL,
        ZAI_FREE_ONLY,
    )

    # ── --urls: compact URL dump ──────────────────────────────────────────
    if args.urls:
        urls = [
            ("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
            ("BITNET_BASE_URL", BITNET_BASE_URL),
            ("TURBOQUANT_BASE_URL", TURBOQUANT_BASE_URL),
            ("ZAI_BASE_URL", ZAI_BASE_URL),
            ("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
            ("GEMINI_BASE_URL", GEMINI_BASE_URL),
            ("HF_BASE_URL", HF_BASE_URL),
            ("OPENAI_BASE_URL", OPENAI_BASE_URL),
            ("MISTRAL_BASE_URL", MISTRAL_BASE_URL),
            ("ORCAROUTER_BASE_URL", ORCAROUTER_BASE_URL),
            ("POLLINATIONS_BASE_URL", POLLINATIONS_BASE_URL),
            ("NVIDIA_BASE_URL", NVIDIA_BASE_URL),
            ("ACP_BASE_URL", ACP_BASE_URL),
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
                ("OLLAMA_BASE_URL", OLLAMA_BASE_URL),
                ("BITNET_BASE_URL", BITNET_BASE_URL),
                ("TURBOQUANT_BASE_URL", TURBOQUANT_BASE_URL),
                ("ZAI_BASE_URL", ZAI_BASE_URL),
                ("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
                ("GEMINI_BASE_URL", GEMINI_BASE_URL),
                ("HF_BASE_URL", HF_BASE_URL),
                ("OPENAI_BASE_URL", OPENAI_BASE_URL),
                ("MISTRAL_BASE_URL", MISTRAL_BASE_URL),
                ("ORCAROUTER_BASE_URL", ORCAROUTER_BASE_URL),
                ("POLLINATIONS_BASE_URL", POLLINATIONS_BASE_URL),
                ("ACP_BASE_URL", ACP_BASE_URL),
            ],
            "OpenRouter": [
                ("OPENROUTER_API_KEY", _mask_key(OPENROUTER_API_KEY)),
                ("OPENROUTER_DEFAULT_MODEL", OPENROUTER_DEFAULT_MODEL),
                ("OPENROUTER_FREE_ONLY", str(OPENROUTER_FREE_ONLY)),
            ],
            "ZAI": [
                ("ZAI_API_KEY", _mask_key(ZAI_API_KEY)),
                ("ZAI_FREE_ONLY", str(ZAI_FREE_ONLY)),
                ("ZAI_FREE_FALLBACK_MODEL", ZAI_FREE_FALLBACK_MODEL),
            ],
            "Gemini": [
                ("GEMINI_API_KEY", _mask_key(GEMINI_API_KEY)),
                ("GEMINI_DEFAULT_MODEL", GEMINI_DEFAULT_MODEL),
                ("GEMINI_FREE_ONLY", str(GEMINI_FREE_ONLY)),
                ("GEMINI_THINKING_LEVEL", GEMINI_THINKING_LEVEL or "(default)"),
                ("GEMINI_SERVICE_TIER", GEMINI_SERVICE_TIER),
            ],
            "Hugging Face": [
                ("HF_TOKEN", _mask_key(HF_TOKEN)),
                ("HF_DEFAULT_MODEL", HF_DEFAULT_MODEL),
                ("HF_FREE_ONLY", str(HF_FREE_ONLY)),
                ("HF_FREE_FALLBACK_MODEL", HF_FREE_FALLBACK_MODEL),
                ("HF_PROVIDER_POLICY", HF_PROVIDER_POLICY or "(default: fastest)"),
            ],
            "OpenAI": [
                ("OPENAI_API_KEY", _mask_key(OPENAI_API_KEY)),
                ("OPENAI_ORGANIZATION_ID", OPENAI_ORGANIZATION_ID or "(not set)"),
                ("OPENAI_PROJECT_ID", OPENAI_PROJECT_ID or "(not set)"),
                ("OPENAI_DEFAULT_MODEL", OPENAI_DEFAULT_MODEL),
                ("OPENAI_FREE_ONLY", str(OPENAI_FREE_ONLY)),
                ("OPENAI_FREE_FALLBACK_MODEL", OPENAI_FREE_FALLBACK_MODEL),
                ("OPENAI_SERVICE_TIER", OPENAI_SERVICE_TIER or "(default: auto)"),
                ("OPENAI_REASONING_EFFORT", OPENAI_REASONING_EFFORT or "(default: model default)"),
            ],
            "Mistral": [
                ("MISTRAL_API_KEY", _mask_key(MISTRAL_API_KEY)),
                ("MISTRAL_DEFAULT_MODEL", MISTRAL_DEFAULT_MODEL),
                ("MISTRAL_FREE_ONLY", str(MISTRAL_FREE_ONLY)),
                ("MISTRAL_FREE_FALLBACK_MODEL", MISTRAL_FREE_FALLBACK_MODEL),
                ("MISTRAL_SAFE_PROMPT", str(MISTRAL_SAFE_PROMPT)),
                ("MISTRAL_SERVICE_TIER", MISTRAL_SERVICE_TIER or "(default: auto)"),
            ],
            "OrcaRouter": [
                ("ORCAROUTER_API_KEY", _mask_key(ORCAROUTER_API_KEY)),
                ("ORCAROUTER_DEFAULT_MODEL", ORCAROUTER_DEFAULT_MODEL),
                ("ORCAROUTER_FREE_ONLY", str(ORCAROUTER_FREE_ONLY)),
                ("ORCAROUTER_FREE_FALLBACK_MODEL", ORCAROUTER_FREE_FALLBACK_MODEL),
                ("ORCAROUTER_FALLBACK_MODELS", ORCAROUTER_FALLBACK_MODELS or "(not set)"),
                ("ORCAROUTER_INCLUDE_COST", str(ORCAROUTER_INCLUDE_COST)),
            ],
            "Pollinations": [
                ("POLLINATIONS_API_KEY", _mask_key(POLLINATIONS_API_KEY)),
                ("POLLINATIONS_DEFAULT_MODEL", POLLINATIONS_DEFAULT_MODEL),
                ("POLLINATIONS_FALLBACK_MODEL", POLLINATIONS_FALLBACK_MODEL),
                ("POLLINATIONS_SAFE", POLLINATIONS_SAFE or "(off)"),
                ("POLLINATIONS_FREE_ONLY", str(POLLINATIONS_FREE_ONLY)),
                ("POLLINATIONS_ANON_CATALOG", str(POLLINATIONS_ANON_CATALOG)),
            ],
            "NVIDIA NIM": [
                ("NVIDIA_API_KEY", _mask_key(NVIDIA_API_KEY)),
                ("NVIDIA_DEFAULT_MODEL", NVIDIA_DEFAULT_MODEL),
                ("NVIDIA_FREE_ONLY", str(NVIDIA_FREE_ONLY)),
            ],
            "ACP": [
                ("ACP_USER", ACP_USER),
                ("ACP_PASS", _mask_key(ACP_PASS)),
            ],
            "TurboQuant": [
                ("TURBOQUANT_SERVER_PATH", TURBOQUANT_SERVER_PATH),
                ("TURBOQUANT_PORT", str(TURBOQUANT_PORT)),
                ("TURBOQUANT_CTX", str(TURBOQUANT_CTX)),
            ],
            "Agent": [
                ("MAX_STEPS", str(MAX_STEPS)),
                ("NUM_CTX", str(NUM_CTX) if NUM_CTX > 0 else "0 (backend default)"),
                ("DEBUG", str(DEBUG)),
                ("VERBOSE", str(VERBOSE)),
            ],
            "Retry": [
                ("RETRY_ON_ERROR", str(RETRY_ON_ERROR)),
                ("MAX_TOOL_RETRIES", str(MAX_TOOL_RETRIES)),
            ],
            "Config Dataclass": [
                ("temperature", str(cfg.temperature)),
                ("max_tokens", str(cfg.max_tokens)),
                ("memory_max_messages", str(cfg.memory_max_messages)),
                ("memory_max_tokens", str(cfg.memory_max_tokens)),
                ("allow_shell", str(cfg.allow_shell)),
                ("allow_network", str(cfg.allow_network)),
                ("allowed_paths", ", ".join(cfg.allowed_paths)),
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
            "Ollama": OLLAMA_BASE_URL,
            "BitNet": BITNET_BASE_URL,
            "turboquant": TURBOQUANT_BASE_URL,
            "ZAI": ZAI_BASE_URL,
            "OpenRouter": OPENROUTER_BASE_URL,
            "Gemini": GEMINI_BASE_URL,
            "HuggingFace": HF_BASE_URL,
            "OpenAI": OPENAI_BASE_URL,
            "Mistral": MISTRAL_BASE_URL,
            "OrcaRouter": ORCAROUTER_BASE_URL,
            "Pollinations": POLLINATIONS_BASE_URL,
            "NVIDIA": NVIDIA_BASE_URL,
            "ACP": ACP_BASE_URL,
        },
        acp_user=ACP_USER,
        acp_pass=ACP_PASS,
        turboquant={
            "Server Path": TURBOQUANT_SERVER_PATH,
            "Port": str(TURBOQUANT_PORT),
            "Context": str(TURBOQUANT_CTX),
        },
        retry_on_error=RETRY_ON_ERROR,
        max_tool_retries=MAX_TOOL_RETRIES,
        backend_rows=_backend_auth_rows(AGENTKTHX_BACKEND),
    )
    return 0


def _mask_key(key: str) -> str:
    """Mask a secret, showing first 4 and last 4 chars if long enough."""
    if not key:
        return dim("(not set)")
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _backend_auth_rows(active_backend: str) -> list[tuple[str, str, str, str, str]]:
    """One row per cloud backend — ``(marker, label, key, FREE_ONLY, fallback)``.

    Pure so tests can pin the listing without a terminal. ``marker`` is
    ``' *'`` for the row matching ``active_backend`` (mirrors the Backend
    URLs section), ``'  '`` otherwise. Keys show ``Set (***last4)`` /
    ``Not Set`` (never more than the last 4 characters); backends without
    a free-fallback env var show ``—``.
    """
    from ...config import (
        GEMINI_API_KEY,
        GEMINI_FREE_ONLY,
        HF_FREE_FALLBACK_MODEL,
        HF_FREE_ONLY,
        HF_TOKEN,
        MISTRAL_API_KEY,
        MISTRAL_FREE_FALLBACK_MODEL,
        MISTRAL_FREE_ONLY,
        NVIDIA_API_KEY,
        NVIDIA_FREE_ONLY,
        OPENAI_API_KEY,
        OPENAI_FREE_FALLBACK_MODEL,
        OPENAI_FREE_ONLY,
        OPENROUTER_API_KEY,
        OPENROUTER_FREE_ONLY,
        ORCAROUTER_API_KEY,
        ORCAROUTER_FREE_FALLBACK_MODEL,
        ORCAROUTER_FREE_ONLY,
        POLLINATIONS_API_KEY,
        POLLINATIONS_FALLBACK_MODEL,
        POLLINATIONS_FREE_ONLY,
        ZAI_API_KEY,
        ZAI_FREE_FALLBACK_MODEL,
        ZAI_FREE_ONLY,
    )
    from ..auth import mask_key

    active_label = _BACKEND_SLUG_TO_LABEL.get((active_backend or "").lower().strip(), None)
    specs = [
        ("ZAI", ZAI_API_KEY, ZAI_FREE_ONLY, ZAI_FREE_FALLBACK_MODEL),
        ("OpenRouter", OPENROUTER_API_KEY, OPENROUTER_FREE_ONLY, ""),
        ("OrcaRouter", ORCAROUTER_API_KEY, ORCAROUTER_FREE_ONLY, ORCAROUTER_FREE_FALLBACK_MODEL),
        ("Gemini", GEMINI_API_KEY, GEMINI_FREE_ONLY, ""),
        ("HuggingFace", HF_TOKEN, HF_FREE_ONLY, HF_FREE_FALLBACK_MODEL),
        ("OpenAI", OPENAI_API_KEY, OPENAI_FREE_ONLY, OPENAI_FREE_FALLBACK_MODEL),
        ("Mistral", MISTRAL_API_KEY, MISTRAL_FREE_ONLY, MISTRAL_FREE_FALLBACK_MODEL),
        ("Pollinations", POLLINATIONS_API_KEY, POLLINATIONS_FREE_ONLY, POLLINATIONS_FALLBACK_MODEL),
        ("NVIDIA", NVIDIA_API_KEY, NVIDIA_FREE_ONLY, ""),
    ]
    rows: list[tuple[str, str, str, str, str]] = []
    for label, key_val, free_only, fallback in specs:
        marker = " *" if label == active_label else "  "
        key_display = "Not Set"
        if key_val:
            raw = mask_key(key_val)
            key_display = raw[0].upper() + raw[1:]  # not set→Not Set, set (***x)→Set (***x)
        free_display = green("ON") if free_only else dim("off")
        fallback_display = cyan(fallback) if fallback else dim("—")
        rows.append((marker, label, key_display, free_display, fallback_display))
    return rows


def _print_config_summary(
    backend: str,
    model: str,
    num_ctx: int,
    max_steps: int,
    debug: bool,
    verbose: bool,
    urls: dict[str, str],
    backend_rows: list[tuple[str, str, str, str, str]],
    acp_user: str,
    acp_pass: str,
    turboquant: dict[str, str],
    retry_on_error: bool,
    max_tool_retries: int,
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
        marker = (
            bright_green(" *")
            if name.lower().replace("-", "") == backend.lower().replace("_", "").replace("-", "")
            else "  "
        )
        print(f"   {marker} {dim(name)}{pad}: {cyan(url)}")

    # ── Cloud backends — one line each: API key · FREE_ONLY · fallback ───
    # R07.20: was two verbose sections (ZAI + OpenRouter only); now every
    # cloud backend gets one compact row so the whole auth picture fits
    # in a single glance (mirrors the /auth picker's registry).
    print(f"\n  {yellow('Cloud Backends')} {dim('(api key / FREE_ONLY / free fallback)')}")
    for marker, label, key_display, free_display, fallback_display in backend_rows:
        print(
            f"   {marker} {pad_colored(dim(label), 14)} {pad_colored(key_display, 17)} "
            f"FREE_ONLY {pad_colored(free_display, 8)} fallback {fallback_display}"
        )

    # ── ACP credentials ───────────────────────────────────────────────────
    print(f"\n  {yellow('ACP (Agent Control Panel)')}")
    print(f"    {dim('User:')}          {cyan(acp_user)}")
    print(f"    {dim('Password:')}      {_mask_key(acp_pass)}")

    # ── TurboQuant ────────────────────────────────────────────────────────
    print(f"\n  {yellow('TurboQuant')}")
    for name, val in turboquant.items():
        print(f"    {dim(f'{name}:')} {cyan(val)}")

    # ── Retry settings ────────────────────────────────────────────────────
    print(f"\n  {yellow('Error Handling')}")
    retry_badge = green("ON") if retry_on_error else dim("OFF")
    print(f"    {dim('Retry on Error:')}  {retry_badge}")
    print(f"    {dim('Max Tool Retries:')} {cyan(str(max_tool_retries))}")

    # ── Environment variable reference ───────────────────────────────────
    env_vars = [
        # ── AgentKthx core ──
        (
            "AGENTKTHX_BACKEND",
            "Default backend (ollama|bitnet|llama-server|zai|openrouter|gemini|hf|openai)",
        ),
        ("AGENTKTHX_MODEL", "Override default model"),
        ("AGENTKTHX_MAX_STEPS", "Max agent steps (default: 10)"),
        ("AGENTKTHX_DEBUG", "Enable debug output (1/true/yes)"),
        ("AGENTKTHX_VERBOSE", "Enable verbose output (1/true/yes)"),
        ("AGENTKTHX_NUM_CTX", "Context window size"),
        ("AGENTKTHX_RETRY_ON_ERROR", "Auto-retry failed tool calls (default: true)"),
        ("AGENTKTHX_MAX_TOOL_RETRIES", "Max retries per tool call (default: 2)"),
        ("AGENTKTHX_FORCE_REACT", "Force ReAct text-based tool calling"),
        ("AGENTKTHX_USE_MF_SYS", "Use Modelfile system prompt"),
        ("AGENTKTHX_FAST", "Fast mode preset"),
        ("AGENTKTHX_USER", "Primary User name for the chat prompt (skips the startup question)"),
        (
            "AGENTKTHX_NO_ENV_PROBE",
            "Skip the host-environment system-prompt section (1/true/yes)",
        ),
        ("AGENTKTHX_NO_UPDATE_CHECK", "Opt out of the version update check (1/true/yes)"),
        (
            "AGENTKTHX_MAX_API_RETRIES",
            "Consecutive transient API failures tolerated per step (default: 5)",
        ),
        (
            "AGENTKTHX_PARALLEL_TOOLS",
            "Run independent tool calls in parallel (default: enabled; 0 disables)",
        ),
        (
            "AGENTKTHX_MODEL_CACHE",
            "Full path of the persistent model-catalog JSON cache (default: <cache-dir>/model_catalog.json)",
        ),
        (
            "AGENTKTHX_MODEL_CACHE_TTL",
            "Model-catalog cache TTL in seconds (default: 1800 = 30 minutes; 0 disables caching)",
        ),
        (
            "AGENTKTHX_MODEL_SEED",
            "Override path of the packaged static-catalog seed JSON (testing/offline)",
        ),
        (
            "AGENTKTHX_ENV_FILE",
            "Persisted env file written by /auth (default ~/.agentkthx/.env; loaded at startup, shell exports win)",
        ),
        (
            "AGENTKTHX_USER_AGENT",
            "User-Agent header for the http_fetch tool (default: spoofed Firefox UA)",
        ),
        ("AGENTKTHX_ACP", "Enable ACP logging without --acp (1/true/yes)"),
        ("AGENTKTHX_ACP_URL", "ACP server URL (same as ACP_BASE_URL)"),
        (
            "AGENTKTHX_PLUGIN_PATH",
            "Extra plugin root dirs (os.pathsep-separated; trusted code paths)",
        ),
        # ── Ollama / BitNet / llama-server ──
        ("OLLAMA_BASE_URL", "Ollama server URL"),
        ("OLLAMA_NUM_CTX", "Ollama context window size"),
        ("OLLAMA_MODELS", "Ollama models directory (default: ~/.ollama/models)"),
        ("BITNET_BASE_URL", "BitNet server URL"),
        ("BITNET_TUNNEL", "BitNet remote tunnel URL"),
        (
            "TURBOQUANT_BASE_URL",
            "TurboQuant backend URL (R07.16 primary; LLAMA_SERVER_BASE_URL also accepted as backward-compat alias)",
        ),
        (
            "LLAMA_SERVER_BASE_URL",
            "(Deprecated alias for TURBOQUANT_BASE_URL — still read as backward-compat fallback)",
        ),
        # ── ZAI ──
        ("ZAI_BASE_URL", "ZAI API URL"),
        ("ZAI_API_KEY", "ZAI API key"),
        ("ZAI_FREE_ONLY", "Restrict to free ZAI models only"),
        ("ZAI_FREE_FALLBACK_MODEL", "Fallback model when credits insufficient"),
        # ── OpenRouter ──
        ("OPENROUTER_BASE_URL", "OpenRouter API URL"),
        ("OPENROUTER_API_KEY", "OpenRouter API key"),
        ("OPENROUTER_DEFAULT_MODEL", "Default OpenRouter model"),
        ("OPENROUTER_FREE_ONLY", "Restrict to free OpenRouter models only"),
        ("OPENROUTER_MAX_429_RETRIES", "Max 429 retries (default: 6)"),
        # ── Gemini ──
        ("GEMINI_BASE_URL", "Gemini API URL (OpenAI-compat endpoint)"),
        ("GEMINI_API_KEY", "Gemini API key (or GOOGLE_API_KEY)"),
        ("GEMINI_DEFAULT_MODEL", "Default Gemini model"),
        ("GEMINI_FREE_ONLY", "Restrict to free-tier Gemini models only"),
        ("GEMINI_THINKING_LEVEL", "Thinking level: minimal|low|medium|high"),
        ("GEMINI_SERVICE_TIER", "Service tier: standard|flex|priority"),
        ("GEMINI_MAX_429_RETRIES", "Max 429 retries for Gemini (default: 6)"),
        # ── Hugging Face ──
        ("HF_BASE_URL", "HF Inference Router URL"),
        ("HF_TOKEN", "HF access token (or HUGGING_FACE_HUB_TOKEN or HF_API_KEY)"),
        ("HF_DEFAULT_MODEL", "Default HF model"),
        ("HF_FREE_ONLY", "Restrict to HF free-tier whitelist (unset = auto-detect via whoami)"),
        ("HF_FREE_FALLBACK_MODEL", "Fallback model on HTTP 402 credit exhaustion"),
        ("HF_PROVIDER_POLICY", "Routing suffix: fastest|cheapest|preferred|<partner>"),
        ("HF_MAX_429_RETRIES", "Max 429 retries for HF (default: 6)"),
        (
            "HF_BASE_URL_LEGACY",
            "Legacy Serverless TGI URL (documented; unused by the v0.1 router-based backend)",
        ),
        # ── OpenAI ──
        ("OPENAI_BASE_URL", "OpenAI API URL"),
        ("OPENAI_API_KEY", "OpenAI API key (sk-proj- recommended)"),
        ("OPENAI_ORGANIZATION_ID", "OpenAI org ID (for multi-org accounts)"),
        ("OPENAI_PROJECT_ID", "OpenAI project ID (for project-scoped billing)"),
        ("OPENAI_DEFAULT_MODEL", "Default OpenAI model"),
        ("OPENAI_FREE_ONLY", "Restrict to OpenAI free-tier whitelist"),
        ("OPENAI_FREE_FALLBACK_MODEL", "Fallback model on 429 insufficient_quota"),
        ("OPENAI_SERVICE_TIER", "Service tier: auto|default|flex|scale|priority|fast"),
        ("OPENAI_REASONING_EFFORT", "Reasoning effort: none|minimal|low|medium|high|xhigh|max"),
        ("OPENAI_MAX_429_RETRIES", "Max 429 retries for OpenAI (default: 6)"),
        # ── Mistral ──
        ("MISTRAL_BASE_URL", "Mistral La Plateforme API URL"),
        ("MISTRAL_API_KEY", "Mistral API key (created in Studio, shown once)"),
        ("MISTRAL_DEFAULT_MODEL", "Default Mistral model (default: mistral-small-latest)"),
        ("MISTRAL_FREE_ONLY", "Restrict to free Labs models only (labs-* prefix)"),
        ("MISTRAL_FREE_FALLBACK_MODEL", "Fallback model on 404 unknown_model / quota exhaustion"),
        ("MISTRAL_SAFE_PROMPT", "Inject Mistral's safety system prompt (1/true/yes)"),
        ("MISTRAL_SERVICE_TIER", "Service tier: auto|standard_only"),
        ("MISTRAL_MAX_RETRIES", "Max transient-error retries (default: 5)"),
        # ── OrcaRouter ──
        ("ORCAROUTER_BASE_URL", "OrcaRouter gateway API URL"),
        ("ORCAROUTER_API_KEY", "OrcaRouter API key"),
        ("ORCAROUTER_DEFAULT_MODEL", "Default model (default: orcarouter/auto)"),
        ("ORCAROUTER_FREE_ONLY", "Restrict to the 4 genuinely-free models only"),
        (
            "ORCAROUTER_FREE_FALLBACK_MODEL",
            "Fallback model on free_quota_exhausted / err_free_rate",
        ),
        (
            "ORCAROUTER_FALLBACK_MODELS",
            "Comma-separated fallback chain (up to 5) via extra_body.models route=fallback",
        ),
        ("ORCAROUTER_INCLUDE_COST", "Request usage.cost_usd in responses (default: true)"),
        # ── Pollinations ──
        ("POLLINATIONS_BASE_URL", "Pollinations gateway API URL"),
        ("POLLINATIONS_API_KEY", "Pollinations API key (optional — anonymous tier works keyless)"),
        ("POLLINATIONS_DEFAULT_MODEL", "Default model (default: openai/gpt-5.4-nano)"),
        (
            "POLLINATIONS_FALLBACK_MODEL",
            "Offline-catalog fallback model (default: z-ai/glm-5.3-flash)",
        ),
        (
            "POLLINATIONS_SAFE",
            "Safety filters: true|nsfw|privacy,secrets,sexual,violence,shield list",
        ),
        ("POLLINATIONS_FREE_ONLY", "Restrict to zero-cost (pollen-free) models only"),
        (
            "POLLINATIONS_ANON_CATALOG",
            "Browse the public catalog anonymously even when a key is set",
        ),
        ("POLLINATIONS_MAX_RETRIES", "Max transient-error retries (default: 5)"),
        # ── NVIDIA NIM ──
        ("NVIDIA_BASE_URL", "NVIDIA NIM API URL (default: https://integrate.api.nvidia.com/v1)"),
        (
            "NVIDIA_API_KEY",
            "NVIDIA NIM API key (nvapi-... prefix, issued at https://build.nvidia.com)",
        ),
        (
            "NVIDIA_DEFAULT_MODEL",
            "Default model (default: nvidia/llama-3.1-nemotron-70b-instruct)",
        ),
        (
            "NVIDIA_FREE_ONLY",
            "Surfaces monthly-quota-exhausted 429 message clearly (default: false)",
        ),
        # ── ACP / TurboQuant ──
        ("ACP_BASE_URL", "ACP server URL"),
        ("ACP_USER", "ACP username (default: admin)"),
        ("ACP_PASS", "ACP password (default: secret)"),
        ("TURBOQUANT_SERVER_PATH", "Path to llama-server binary"),
        ("TURBOQUANT_PORT", "TurboQuant server port (default: 8764)"),
        ("TURBOQUANT_CTX", "TurboQuant context window (default: 8192)"),
        # ── Display / platform ──
        ("NO_COLOR", "Disable all ANSI color output (any non-empty value)"),
        ("CLICOLOR", "Set to 0 to disable colors (standard convention)"),
        ("CLICOLOR_FORCE", "Force colors even when stdout is not a TTY"),
        ("AGENTKTHX_GLYPHS", "Unicode glyph mode: auto|unicode|ascii (default: auto)"),
        ("XDG_CACHE_HOME", "Base dir for caches — tool-support + model cache (default: ~/.cache)"),
        ("XDG_STATE_HOME", "Base dir for plugin state (default: ~/.local/state)"),
    ]
    print(f"\n  {yellow('Environment Variables')}")
    max_env = max(len(v[0]) for v in env_vars)
    for var, desc in env_vars:
        pad = " " * (max_env - len(var))
        print(f"    {dim(var)}{pad}  {dim('-')} {desc}")

    print(dim("-" * 50))
