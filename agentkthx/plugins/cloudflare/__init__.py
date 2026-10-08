"""
AgentKthx Plugin \u2014 Cloudflare Workers AI Cloud Backend

Cloudflare Workers AI backend for 20+ open models (Llama, Mistral, Qwen,
DeepSeek, Phi, Gemma, GPT-OSS) via the OpenAI Chat-Completions API at
api.cloudflare.com. Free tier with 10,000 neurons per day (UTC reset),
no credit card required. Unique among AgentKthx cloud backends: requires
both CLOUDFLARE_API_KEY AND CLOUDFLARE_ACCOUNT_ID (the account ID is
baked into the URL path, not derived from the Bearer token).

See docs/api/CLOUDFLARE_API_TECHNICAL_REFERENCE.md for full details.

Written by VTSTech \u2014 https://www.vts-tech.org
"""

from __future__ import annotations


def register(manager) -> None:
    """Register the Cloudflare backend with the plugin manager."""
    from .cloudflare import CloudflareBackend

    manager.register_backend("cloudflare", CloudflareBackend)
    # Alias for convenience \u2014 `--backend cloudflare` and `--backend cf`
    # both work. Mirrors the OpenAI plugin's `oai` alias pattern.
    manager.register_backend("cf", CloudflareBackend)


def unregister(manager) -> None:
    """Unregister the Cloudflare backend from the plugin manager."""
    manager.unregister_backend("cloudflare")
    manager.unregister_backend("cf")
