"""`agentkthx models` subcommand.

Extracted verbatim from cli.py in R07.00 Phase 8.

R07.19 (follow-up #10): the openre/openai dual tool-support columns are
GONE — one authoritative capability check per model feeds ONE `tools`
column (the capabilities verdict is API-mode-independent, so the two old
columns could only ever agree). Detected NONE is retired: any verdict that
would be none falls back to react ("None is essentially untested" — no
models should show none).

R07.19 (follow-up #11): new `think` column — thinking/reasoning support
per model, detected for free (Ollama capabilities declaration, cloud
name-heuristics) and cached under `thinking:<model>` keys.
"""

from __future__ import annotations

import argparse

from ...backends import OllamaBackend, get_backend
from ...colors import bright_cyan, bright_green, cyan, dim, green, pad_colored, yellow
from ...config import get_config
from ...core.types import ApiMode, ToolSupportLevel
from ..utils import _thinking_status, _tool_status


def cmd_models(args: argparse.Namespace) -> int:
    """Execute the models command."""

    # R07.00: resolve shared collaborators through the cli facade so that
    # monkeypatch.setattr(agentkthx.cli, '<name>', ...) keeps working.
    from agentkthx import cli as _cli

    from ...core.tool_cache import (
        cache_tool_support,
        get_cached_thinking_support,
        get_cached_tool_support,
    )

    config = get_config()
    backend_name = args.backend or config.backend

    # Use appropriate API mode for the backend
    # R06.57 (MAINT-05): replaced hardcoded ("openrouter", "gemini") allowlist
    # with backend.is_cloud check — a 5th cloud backend will automatically
    # default to OPENAI mode without needing to edit this list.
    # Instantiate a temporary backend to check is_cloud. Pass api_mode=None
    # to avoid the validation error (some backends raise ValueError on OPENRE).
    # Backends with is_cloud=True get OPENAI; local backends get OPENRE
    # (their native /api/chat mode).
    _probe_backend = get_backend(backend_name, api_mode=ApiMode.OPENAI)
    if getattr(_probe_backend, "is_cloud", False):
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
            print("   Or set OLLAMA_BASE_URL to your remote server")
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
        # R07.15 fix: use the plugin's shared _is_free_model() instead of
        # the bare ``:free``-suffix check. The named ``openrouter/free``
        # router (the plugin's default model) is also a free model, but
        # ``"openrouter/free".endswith(":free")`` is False, so the old
        # suffix check stripped it here even though the backend's own
        # R07.09 filter had correctly accepted it. Mirrors the ZAI
        # branch's helper-based filter below.
        from ...plugins.openrouter.openrouter import _is_free_model

        models = [m for m in models if _is_free_model(m["name"])]
        if not models:
            print("No free models found on OpenRouter.")
            return 0
    elif backend_name == "zai" and ZAI_FREE_ONLY:
        # Free = zero pricing in the ZAI catalog (glm-4.5-flash and
        # glm-4.7-flash ONLY — glm-5.3-flash is paid despite the name).
        # Use the backend's _is_free_model() instead of a hard-coded list
        # so catalog updates are picked up automatically.
        models = [
            m
            for m in models
            if getattr(backend, "_is_free_model", None) and backend._is_free_model(m["name"])
        ]
        if not models:
            print("No free models found on ZAI.")
            return 0

    # Initialize ACP plugin if requested
    acp, _ = _cli._init_acp(args, config, "AgentKthx-Models")

    # Column widths
    # R07.18: NAME_W widened from 36→48 to fit long Ollama names without
    # truncation (the user needs the full name to copy into `-m`).
    # R07.19 (ROB-37): NAME_W is now DYNAMIC — measured from the longest
    # model name in the already-loaded (and free-filtered) `models` list,
    # floored at the R07.18 defaults (48 local / 50 cloud). The fixed 48
    # was 2 chars short of real-world Ollama names like
    # krith/meta-llama-3.2-1b-instruct-uncensored:IQ4_XS (50 chars),
    # which pushed the Size/Quant/Context columns right for that row —
    # with the R07.18 no-truncation policy pad_colored cannot absorb an
    # overflow, so the column must grow instead. Measurement happens
    # BEFORE the header/separator render, so header, separator and every
    # data row share one width.
    SIZE_W = 9  # R07.18: was 8, but "xxx.xx GB" (e.g. "  0.75 GB") is 9 chars — the 1-char overflow cascaded to every column after Size
    QUANT_W = 8  # weight quant column (Q4_K_M, Q8_0, F16, etc.)
    CTX_W = 12
    TOOLS_W = 12  # fits "✓ native"
    THINK_W = 9  # fits "? unknown" / "✓ yes" (follow-up #11)
    FAMILY_W = 12

    # Detect backend type early — cloud providers need different column layout
    # R06.57 (MAINT-05): replaced hardcoded [OPENROUTER, ZAI, GEMINI] list
    # with backend.is_cloud — a 5th cloud backend will automatically get
    # the cloud column layout (wider NAME_W, no Size/Family columns).
    is_cloud_provider = getattr(backend, "is_cloud", False)

    # R07.19 (ROB-37): dynamic Name width — the longest actual model name
    # vs the R07.18 floors (48 local / 50 cloud; the floor keeps short
    # listings byte-identical to R07.18). Cloud names still run longer on
    # average ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free" = 49
    # chars), hence the higher cloud floor. Cloud providers don't show
    # Size/Family columns, so a wider Name costs them nothing.
    longest_name = max((len(str(m.get("name", ""))) for m in models), default=0)
    NAME_W = max(50 if is_cloud_provider else 48, longest_name)

    if is_cloud_provider:
        # R07.19 (follow-up #10/#11): 2+W+1+CTX+2+TOOLS+2+THINK = 40 + NAME_W
        sep_len = 2 + NAME_W + 1 + CTX_W + 2 + TOOLS_W + 2 + THINK_W
    else:
        # R07.19 (follow-up #10/#11): the two tool columns collapsed into
        # one `tools` column and a new `think` column added:
        # 2+W+1+SIZE+1+QUANT+1+CTX+2+TOOLS+2+THINK+2+FAMILY = 73 + NAME_W
        sep_len = (
            2
            + NAME_W
            + 1
            + SIZE_W
            + 1
            + QUANT_W
            + 1
            + CTX_W
            + 2
            + TOOLS_W
            + 2
            + THINK_W
            + 2
            + FAMILY_W
        )

    print()
    print(f"{bright_cyan('⚖ AgentKthx')} - Available Models")
    print(dim(f"  Backend: {backend.base_url}"))
    if args.tool_support:
        print(dim("  Testing: tools + thinking (single capability check)"))
    if acp:
        print(f"  {dim('ACP:')} {green('✓ Connected')} ({acp.base_url})")
    print(dim("-" * sep_len))

    if not is_cloud_provider:
        # R07.18: added Quant column between Size and Context
        # R07.19 (follow-up #10/#11): single `tools` column + new `think` column
        header = (
            f"  {'Name':<{NAME_W}} {'Size':>{SIZE_W}} {'Quant':<{QUANT_W}} "
            f"{'Context':>{CTX_W}}  {'tools':>{TOOLS_W}}  {'think':>{THINK_W}}  "
            f"{'Family':<{FAMILY_W}}"
        )
    else:
        # Cloud providers - skip Size/Quant column (always 'unknown') and Family column
        header = (
            f"  {'Name':<{NAME_W}} {'Context':>{CTX_W}}  {'tools':>{TOOLS_W}}  {'think':>{THINK_W}}"
        )

    print(header)
    print(dim("-" * sep_len))

    def _get_tools_status(name: str, family: str) -> str:
        """ONE authoritative tool-support verdict for a model.

        R07.19 (follow-up #10): the openre/openai per-mode loop is gone.
        Single check → single cache entry → single column. Legacy "none"
        verdicts (old cache entries) normalize to react; no model is ever
        displayed as none.
        """
        if not args.no_cache:
            cached = get_cached_tool_support(name)
            if cached is not None:
                return ToolSupportLevel.effective(cached).value

        try:
            support = backend.test_tool_support(name, family=family, force_test=True)
            return ToolSupportLevel.effective(support).value
        except Exception:
            # Defensive: backends own their caching; on a propagated error
            # fall back to the ReAct default rather than none (follow-up #10).
            cache_tool_support(name, ToolSupportLevel.REACT, family=family, error="models scan")
            return "error"

    def _get_think_status(name: str, family: str) -> str:
        """Thinking/reasoning verdict for a model (R07.19 follow-up #11).

        Free where a signal exists (Ollama capabilities declaration /
        cloud name-heuristics — both cached); unknown when the backend
        offers no signal at all (stubs, exotic third-party plugins).
        """
        if not args.no_cache:
            cached = get_cached_thinking_support(name)
            if cached is not None:
                return cached.value

        think_fn = getattr(backend, "test_thinking_support", None)
        if think_fn is None:
            return "unknown"
        try:
            return think_fn(name, family=family).value
        except Exception:
            return "unknown"

    for m in models:
        name = m.get("name", "unknown")
        size = m.get("size", 0)
        size_gb = size / (1024**3) if size else 0
        details = m.get("details", {}) or {}
        family = details.get("family", "unknown")
        # R07.18: detected weight quant (Q4_K_M, Q8_0, F16, etc.) from the
        # Ollama /api/tags details block. Empty for backends that don't
        # report it — rendered as "unknown" in the Quant column.
        weight_quant = details.get("quantization_level", "") or ""

        # Get both runtime and max context
        backend.get_model_runtime_context(name)
        max_ctx = backend.get_model_max_context(name, family=family)

        # R07.18: format context size as 128K / 1M style (was plain int)
        from ..footer import fmt_token_size

        ctx_str = fmt_token_size(max_ctx)

        # Fixed columns
        # R07.18: NO truncation — the user needs to see the full model name
        # to copy it into `-m`. Long names push subsequent columns right
        # for that row only (pad_colored pads short names but doesn't
        # truncate long ones). Widened NAME_W from 36→48 to fit the
        # longest realistic Ollama name (cryptidbleh/gemma4-claude-opus-4.6:latest = 42 chars).
        name_col = pad_colored(cyan(name), NAME_W)
        # R07.18: pad size_col to SIZE_W=9 for alignment (was raw string,
        # which overflowed by 1 char and cascaded to all columns after it)
        size_col = pad_colored(f"{size_gb:>6.2f} GB", SIZE_W, "right")
        # R07.18: quant column — pad to QUANT_W, dim if unknown
        quant_col = pad_colored(
            dim(weight_quant) if not weight_quant else yellow(weight_quant),
            QUANT_W,
            "left",
        )
        ctx_col = pad_colored(dim(ctx_str), CTX_W, "right")

        # Resolve both capability columns once per model (single check each)
        tools_val = _get_tools_status(name, family)
        think_val = _get_think_status(name, family)
        tool_col = pad_colored(_tool_status(tools_val), TOOLS_W, "right")
        think_col = pad_colored(_thinking_status(think_val), THINK_W, "right")

        if isinstance(backend, OllamaBackend) and not is_cloud_provider:
            print(
                f"  {name_col} {size_col} {quant_col} {ctx_col}  {tool_col}  {think_col}  {dim('(' + family + ')')}"
            )
        else:
            # Cloud provider: no Size/Quant column, no Family column
            print(f"  {name_col} {ctx_col}  {tool_col}  {think_col}")

        # Log per-model capability scan to ACP
        if acp:
            acp.model_name = name
            acp.log_chat("user", "Scanning model capabilities...")
            acp.log_chat(
                "assistant",
                f"tools={tools_val} think={think_val} | "
                + (
                    f"{size_gb:.2f} GB | quant={weight_quant or '?'} | " f"ctx {max_ctx}"
                    if not is_cloud_provider
                    else f"ctx {max_ctx}"
                ),
            )

    print(dim("-" * sep_len))
    print(f"Total: {bright_green(str(len(models)))} models")

    # Show legend
    # R07.19 (follow-up #10): no "✗ none" anymore — NONE is retired, every
    # model resolves to native/react. (follow-up #11): think legend added.
    print(
        f"\n{dim('Legend:')} {bright_green('✓ native')} (API tools) | {yellow('○ react')} (text parsing) | {dim('? untested')}"
    )
    print(
        f"{dim('Think:')} {bright_green('✓ yes')} (reasoning model) | {dim('✗ no')} | {dim('? unknown')}"
    )
    print(f"{dim('Context:')} Max context window from model API")
    print(
        f"{dim('Single capability check per model (API-mode-independent); none falls back to react.')}"
    )
    print(
        f"{dim('Use')} {cyan('--tool-support')} {dim('to force re-testing.')} {cyan('--no-cache')} {dim('to ignore cached verdicts.')}"
    )

    # Log summary to ACP and clean up
    if acp:
        if args.tool_support:
            acp.log_chat("assistant", f"Capability scan complete: {len(models)} models tested")
        acp.a2a_unregister()

    return 0
