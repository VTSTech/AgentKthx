"""
⚛️ AgentKthx — Central Configuration

Single source of truth for core framework configuration.
Plugin-owned config (BitNet, ZAI, ACP, TurboQuant) is read from
environment variables and defaults are defined in each plugin's
plugin.json manifest.  The module-level variables below are kept for
backward compatibility — they simply read from the environment.

Status: Alpha

Written by VTSTech — https://www.vts-tech.org
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# R07.20: load the persisted env file (~/.agentkthx/.env, AGENTKTHX_ENV_FILE
# overrides) BEFORE any constant below reads the environment. This is what
# makes /auth's saved API keys and FREE_ONLY flags apply to every future
# CLI invocation — shell exports keep precedence (the loader never
# clobbers an already-set variable).
from agentkthx.env_file import load_env_file as _load_env_file

_load_env_file()

# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA CONFIGURATION (native backend)
# ═══════════════════════════════════════════════════════════════════════════════
# Default for local Ollama
OLLAMA_BASE_URL = "http://localhost:11434"

# Override via environment variable (takes precedence if set)
_ollama_env = os.environ.get("OLLAMA_BASE_URL")
if _ollama_env:
    OLLAMA_BASE_URL = _ollama_env


# ═══════════════════════════════════════════════════════════════════════════════
# TURBOQUANT CONFIGURATION (native backend)
# ═══════════════════════════════════════════════════════════════════════════════
# Default for local TurboQuant backend (native, always available).
# R07.16: env var renamed LLAMA_SERVER_BASE_URL → TURBOQUANT_BASE_URL to match
# the backend rename (llama-server → turboquant). The old env var name is still
# read as a backward-compat fallback so existing setups keep working — set
# EITHER name in your shell, the new one (TURBOQUANT_BASE_URL) wins if both
# are set. The binary itself is still `llama-server` (llama.cpp upstream name);
# only the AgentKthx backend name and this env var changed.
TURBOQUANT_BASE_URL = "http://localhost:8764"

# Override via environment variable (new name takes precedence; old name is
# backward-compat fallback for existing user setups).
_turboquant_env = os.environ.get("TURBOQUANT_BASE_URL")
if _turboquant_env:
    TURBOQUANT_BASE_URL = _turboquant_env
else:
    # Backward-compat: fall back to the old LLAMA_SERVER_BASE_URL env var
    # if the user hasn't set the new TURBOQUANT_BASE_URL.
    _llama_server_env = os.environ.get("LLAMA_SERVER_BASE_URL")
    if _llama_server_env:
        TURBOQUANT_BASE_URL = _llama_server_env

# Backward-compat alias: keep LLAMA_SERVER_BASE_URL exported from this module
# so any code that does `from ..config import LLAMA_SERVER_BASE_URL` still
# works. It points at the same value as TURBOQUANT_BASE_URL.
LLAMA_SERVER_BASE_URL = TURBOQUANT_BASE_URL


# ═══════════════════════════════════════════════════════════════════════════════
# PLUGIN-OWNED CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
# The following config variables are owned by their respective plugins
# (bitnet, zai, acp, turboquant).  They read from environment variables
# with defaults defined in each plugin's plugin.json manifest.
# Kept here for backward compatibility — plugin code imports these.

# BitNet plugin (agentkthx/plugins/bitnet/)
BITNET_BASE_URL = os.environ.get("BITNET_TUNNEL") or os.environ.get(
    "BITNET_BASE_URL", "http://localhost:8765"
)

# ZAI plugin (agentkthx/plugins/zai/)
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai")
ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
ZAI_FREE_ONLY = os.environ.get("ZAI_FREE_ONLY", "").lower() in ("1", "true", "yes")
ZAI_FREE_FALLBACK_MODEL = os.environ.get("ZAI_FREE_FALLBACK_MODEL", "glm-4.5-flash")

# ACP plugin (agentkthx/plugins/acp/)
ACP_BASE_URL = os.environ.get("ACP_BASE_URL", "http://localhost:8766")
ACP_USER = os.environ.get("ACP_USER", "admin")
ACP_PASS = os.environ.get("ACP_PASS", "secret")

# TurboQuant plugin (agentkthx/plugins/turboquant/)
TURBOQUANT_SERVER_PATH = os.environ.get("TURBOQUANT_SERVER_PATH", "llama-server")
TURBOQUANT_PORT = int(os.environ.get("TURBOQUANT_PORT", "8764"))
TURBOQUANT_CTX = int(os.environ.get("TURBOQUANT_CTX", "8192"))

# OpenRouter plugin (agentkthx/plugins/openrouter/)
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_DEFAULT_MODEL = os.environ.get("OPENROUTER_DEFAULT_MODEL", "openrouter/free")
OPENROUTER_FREE_ONLY = os.environ.get("OPENROUTER_FREE_ONLY", "").lower() in ("1", "true", "yes")

# OrcaRouter plugin (agentkthx/plugins/orcarouter/)
# OrcaRouter is a zero-markup gateway to 11 upstream providers (OpenAI,
# Anthropic, Google, DeepSeek, Grok, Qwen, Kimi, MiniMax, ZAI, Kling,
# BytePlus). Free tier has 4 genuinely $0/token models; paid tier is
# the upstream provider's per-token rate with no markup. See
# docs/api/ORCAROUTER_API_TECHNICAL_REFERENCE.md for full details.
ORCAROUTER_BASE_URL = os.environ.get("ORCAROUTER_BASE_URL", "https://api.orcarouter.ai/v1")
ORCAROUTER_API_KEY = os.environ.get("ORCAROUTER_API_KEY", "")
# Default to the "auto" named router — picks the cheapest live chat
# model at request time. Set ORCAROUTER_DEFAULT_MODEL to a specific
# provider-prefixed model (e.g. "openai/gpt-4o-mini") to override.
ORCAROUTER_DEFAULT_MODEL = os.environ.get("ORCAROUTER_DEFAULT_MODEL", "orcarouter/auto")
# When true, restricts model usage to the 4 genuinely-free models in
# ORCAROUTER_FREE_MODEL_WHITELIST (see orcarouter.py). Prevents accidental
# paid API calls.
ORCAROUTER_FREE_ONLY = os.environ.get("ORCAROUTER_FREE_ONLY", "").lower() in ("1", "true", "yes")
# Used when ORCAROUTER_FREE_ONLY=false and HTTP 403 free_quota_exhausted /
# 429 err_free_rate is received mid-run — swap to the free router and retry.
# Mirrors the ZAI/HF FREE_FALLBACK_MODEL pattern.
ORCAROUTER_FREE_FALLBACK_MODEL = os.environ.get("ORCAROUTER_FREE_FALLBACK_MODEL", "orcarouter/free")
# Comma-separated list of up to 5 provider-prefixed models to use as a
# fallback chain via extra_body.models (route="fallback"). Empty by
# default — set to enable cross-provider resilience.
# Example: "openai/gpt-4o-mini,anthropic/claude-haiku-4.5,google/gemini-2.5-flash"
ORCAROUTER_FALLBACK_MODELS = os.environ.get("ORCAROUTER_FALLBACK_MODELS", "")
# When true (default), adds X-OrcaRouter-Include-Cost: true to every
# request so the response includes usage.cost_usd. Disable to suppress.
ORCAROUTER_INCLUDE_COST = os.environ.get("ORCAROUTER_INCLUDE_COST", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Gemini plugin (agentkthx/plugins/gemini/)
# Google AI Studio / Gemini API via OpenAI-compatible endpoint.
# Trailing slash on base URL matters — OpenAI SDK appends paths like
# "/chat/completions" without a leading slash. We preserve it here.
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
)
# GEMINI_API_KEY is the documented env var. GOOGLE_API_KEY is accepted
# as a fallback, mirroring Google's own SDK precedence.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_DEFAULT_MODEL = os.environ.get("GEMINI_DEFAULT_MODEL", "gemini-3.8-flash")
GEMINI_FREE_ONLY = os.environ.get("GEMINI_FREE_ONLY", "").lower() in ("1", "true", "yes")
# Default thinking level for Gemini 3.x (cannot be fully disabled).
# One of: "minimal", "low", "medium", "high". Set to "" to leave unset.
GEMINI_THINKING_LEVEL = os.environ.get("GEMINI_THINKING_LEVEL", "")
# Default service tier: "standard" (default), "flex" (cheaper, slower),
# "priority" (faster, costs more). Free tier ignores this.
GEMINI_SERVICE_TIER = os.environ.get("GEMINI_SERVICE_TIER", "standard")


# Hugging Face plugin (agentkthx/plugins/huggingface/)
# Inference Router — OpenAI-compatible /v1/chat/completions endpoint.
# The router proxies to ~18 partner providers (Together, Groq, Novita,
# DeepInfra, Fireworks, etc.) with :fastest / :cheapest / :preferred
# / :provider-name model-id suffixes for routing control.
HF_BASE_URL = os.environ.get("HF_BASE_URL", "https://router.huggingface.co/v1")
# Legacy Serverless TGI surface — documented in the API Technical Reference
# but NOT used by the v0.1 backend (router is preferred). Kept here so
# future versions can fall back without re-parsing env vars.
HF_BASE_URL_LEGACY = os.environ.get("HF_BASE_URL_LEGACY", "https://api-inference.huggingface.co")
# HF_TOKEN is the documented env var. HUGGING_FACE_HUB_TOKEN is the
# older form (still used by huggingface_hub SDK). HF_API_KEY is accepted
# as a third fallback (some users set this instead of HF_TOKEN).
HF_TOKEN = (
    os.environ.get("HF_TOKEN")
    or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    or os.environ.get("HF_API_KEY", "")
)
HF_DEFAULT_MODEL = os.environ.get("HF_DEFAULT_MODEL", "openai/gpt-oss-120b")
# Strict free-tier enforcement: when true, only models in the
# HF_FREE_MODEL_WHITELIST are accepted; the :cheapest suffix is
# auto-appended to the model id; HTTP 402 (free-tier credit exhausted)
# is treated as a hard failure (no retry) instead of triggering fallback.
HF_FREE_ONLY = os.environ.get("HF_FREE_ONLY", "").lower() in ("1", "true", "yes")
# Used when HF_FREE_ONLY=false and HTTP 402 is received mid-run — the
# backend swaps to this model and retries. Mirrors the ZAI plugin's
# ZAI_FREE_FALLBACK_MODEL pattern (zai.py:706-714).
HF_FREE_FALLBACK_MODEL = os.environ.get(
    "HF_FREE_FALLBACK_MODEL", "prism-ml/Ternary-Bonsai-27B-gguf"
)
# Provider routing policy — auto-appended as a suffix to the model id
# when no explicit suffix is present. Empty string (default) means no
# suffix (router's :fastest default applies). One of:
#   ""          — no suffix (router uses :fastest by default)
#   "fastest"   — highest throughput (router default)
#   "cheapest"  — lowest price per output token
#   "preferred" — user's configured preference order at
#                 https://huggingface.co/settings/inference-providers
#   "<name>"    — pin to a specific partner provider (groq, together,
#                 novita, fireworks, deepinfra, cerebras, ...)
HF_PROVIDER_POLICY = os.environ.get("HF_PROVIDER_POLICY", "")


# OpenAI plugin (agentkthx/plugins/openai/)
# OpenAI API direct surface — Chat Completions (/v1/chat/completions) is
# the primary endpoint for v0.1. The Responses API (/v1/responses) is
# newer and stateful — deferred to v0.2 of the plugin. Service tiers
# (auto/default/flex/scale/priority/fast) control pricing and latency.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
# OPENAI_API_KEY accepts four key types (detected via prefix):
#   sk-...      — legacy user key (deprecated, lacks project scoping)
#   sk-proj-... — project key (recommended for production)
#   sk-admin-.. — admin key (administration endpoints only, never inference)
#   sk-sa-...   — service account key (long-running service workloads)
# The backend surfaces a warning when a legacy `sk-` key is used in
# production — see _detect_key_type() in openai.py.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# Optional headers for multi-org or project-scoped billing (only
# relevant for sk-proj- and legacy sk- keys). Empty by default.
OPENAI_ORGANIZATION_ID = os.environ.get("OPENAI_ORGANIZATION_ID", "")
OPENAI_PROJECT_ID = os.environ.get("OPENAI_PROJECT_ID", "")
OPENAI_DEFAULT_MODEL = os.environ.get("OPENAI_DEFAULT_MODEL", "gpt-6-sol")
# Strict free-tier enforcement: when true, only models in the
# OPENAI_FREE_MODEL_WHITELIST (6 very-low-cost models) are accepted,
# `service_tier` is forced to `default` (never priority/fast/scale),
# `reasoning_effort` is capped at `low` (reasoning tokens are billed
# at output rate and can quickly exhaust trial credit), and HTTP 429
# with `insufficient_quota` is treated as a hard failure (no retry).
OPENAI_FREE_ONLY = os.environ.get("OPENAI_FREE_ONLY", "").lower() in ("1", "true", "yes")
# Used when OPENAI_FREE_ONLY=false and HTTP 429 insufficient_quota is
# received mid-run — the backend swaps to this model and retries once.
# Mirrors the ZAI plugin's ZAI_FREE_FALLBACK_MODEL pattern.
OPENAI_FREE_FALLBACK_MODEL = os.environ.get("OPENAI_FREE_FALLBACK_MODEL", "gpt-4o-mini")
# Service tier — auto/default/flex/scale/priority/fast. Empty (default)
# means don't send the parameter (OpenAI uses `auto` which resolves to
# the project's configured tier, usually `default`). When OPENAI_FREE_ONLY
# is true, this is forced to `default` regardless of the env var.
OPENAI_SERVICE_TIER = os.environ.get("OPENAI_SERVICE_TIER", "")
# Reasoning effort — none/minimal/low/medium/high/xhigh/max. Empty
# (default) means don't send the parameter (model uses its default,
# usually `medium` for gpt-5.5+ and gpt-6.x). When OPENAI_FREE_ONLY is
# true, this is capped at `low`.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "")


# Mistral plugin (agentkthx/plugins/mistral/)
# La Plateforme public API — OpenAI Chat-Completions wire format with
# deliberate deltas (random_seed instead of seed, finish_reason
# "model_length" for context overflow, reasoning_effort ladder includes
# the extra "xhigh" rung, optional safe_prompt + AssistantMessage.prefix
# prefill). See docs/api/MISTRAL_API_TECHNICAL_REFERENCE.md.
MISTRAL_BASE_URL = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
# Single bearer-token scheme. Created in Studio → API keys; shown only
# once. Canonical env var matches the official Python and TypeScript SDKs.
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
# Default to mistral-small-latest (Apache 2.0, hybrid instruct+reasoning,
# 256K context) — cost-efficient choice for the framework default. Use
# mistral-medium-latest for frontier-class agentic + coding workloads.
MISTRAL_DEFAULT_MODEL = os.environ.get("MISTRAL_DEFAULT_MODEL", "mistral-small-latest")
# Strict free-tier enforcement: when true, only the Labs models
# (labs-* prefix, free of charge) are accepted. Mistral's free surface
# is narrower than OpenRouter's, so this is more of a guardrail than
# a daily driver. Default false.
MISTRAL_FREE_ONLY = os.environ.get("MISTRAL_FREE_ONLY", "").lower() in ("1", "true", "yes")
# When MISTRAL_FREE_ONLY=false and the model 404s as unknown_model or
# the org is rate-limited past the daily quota, swap to this model and
# retry. Labs models are the safest fallback since they're free.
MISTRAL_FREE_FALLBACK_MODEL = os.environ.get(
    "MISTRAL_FREE_FALLBACK_MODEL", "labs-mistral-small-creative"
)
# Mistral-specific: inject the safety system prompt before all
# conversations (slightly changes tone + costs a few prompt tokens).
# Default false — the agent's soul/system prompt owns behavior.
MISTRAL_SAFE_PROMPT = os.environ.get("MISTRAL_SAFE_PROMPT", "").lower() in ("1", "true", "yes")
# Service tier: "auto" (default — allows Priority routing if entitled)
# or "standard_only". Empty (default) means don't send the parameter
# (Mistral's server default is "auto").
MISTRAL_SERVICE_TIER = os.environ.get("MISTRAL_SERVICE_TIER", "")

# Pollinations plugin (agentkthx/plugins/pollinations/)
# Unified gateway https://gen.pollinations.ai/v1 — OpenAI-compatible
# aggregator: one key -> dozens of vendor families (openai/, anthropic/,
# google/, z-ai/, deepseek/, qwen/, mistralai/, meta/, community/...),
# plus media generation (images / video / TTS / embeddings) under the
# same credential. See docs/api/POLLINATIONS_API_TECHNICAL_REFERENCE.md.
POLLINATIONS_BASE_URL = os.environ.get("POLLINATIONS_BASE_URL", "https://gen.pollinations.ai/v1")
# OPTIONAL — the only backend in AgentKthx that runs with no key at all.
# Empty (default) = legacy anonymous text surface (IP-rate-limited, the
# openai-fast GPT-OSS-20B tier) — fine for smoke tests, not agent loops.
# With a key: sk_ secret keys (full account access, server-side only) are
# the canonical choice; raw pk_ keys are legacy (1 pollen/IP/hour) but
# tolerated. Create keys at https://enter.pollinations.ai/keys.
POLLINATIONS_API_KEY = os.environ.get("POLLINATIONS_API_KEY", "")
# Platform default — cheapest workhorse tier (0.15 pollen / 1M prompt
# tokens, 400K context, tools + reasoning capable).
POLLINATIONS_DEFAULT_MODEL = os.environ.get("POLLINATIONS_DEFAULT_MODEL", "openai/gpt-5.4-nano")
# Cheap alternate family for the offline fallback chain (used when the
# live /v1/models catalog is unreachable and no health telemetry exists).
POLLINATIONS_FALLBACK_MODEL = os.environ.get("POLLINATIONS_FALLBACK_MODEL", "z-ai/glm-5.3-flash")
# Comma-separated safety filters or a shorthand: true = privacy,secrets;
# nsfw = sexual,violence; or an explicit list (privacy,secrets,sexual,
# violence,shield). Empty (default) = off — upstream moderation still
# blocks as 422 content_policy_violation regardless of this flag.
POLLINATIONS_SAFE = os.environ.get("POLLINATIONS_SAFE", "")
# Strict free-tier enforcement: when true, only zero-cost models are
# listed/accepted. The gateway encodes zero-cost as a currency-only
# pricing dict ({"currency": "pollen"}, no price fields) — the ':free'/
# '-free' community variants. (The broader paid_only free TIER —
# Quest-Pollen-eligible models — is visible via scripts/probe_pollinations.sh
# on the bare GET /models endpoint.) Default false.
POLLINATIONS_FREE_ONLY = os.environ.get("POLLINATIONS_FREE_ONLY", "").lower() in (
    "1",
    "true",
    "yes",
)
# Browse the PUBLIC 307-card catalog anonymously even when a key is set.
# The gateway scopes GET /v1/models to the key's entitlements (observed
# 2026-09-28: 307 cards anonymous vs 134 keyed — the keyed feed drops
# premium vendors AND every zero-priced community model, which starves
# FREE_ONLY). Catalog-only toggle: generation POSTs still authenticate.
# Default false (keyed catalog when a key is present).
POLLINATIONS_ANON_CATALOG = os.environ.get("POLLINATIONS_ANON_CATALOG", "").lower() in (
    "1",
    "true",
    "yes",
)


# NVIDIA NIM plugin (agentkthx/plugins/nvidia/)
# Cloud-hosted OpenAI-compatible endpoint at integrate.api.nvidia.com/v1
# backed by vLLM. Catalog: Llama, Mistral, Qwen, Phi, DeepSeek, NV Nemotron,
# Granite, GLM (80+ models). Free tier: 1,000 credits on signup, resets
# MONTHLY, up to 5,000 by request. 40 RPM. No credit card. See
# docs/api/NVIDIA_API_TECHNICAL_REFERENCE.md.
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
# NVIDIA cloud NIM API key. Issued at https://build.nvidia.com → Account →
# API Keys. Same `nvapi-` prefix as NGC personal keys (which pull NIM
# containers from nvcr.io); the cloud endpoint distinguishes them by scope.
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
# Default model — NVIDIA's Llama-3.1-Nemotron-70B-Instruct (flagship chat
# model based on Llama 3.1 70B, 128K context, supports tools + streaming).
# NOTE: NVIDIA's cloud endpoint does NOT serve bare "meta/llama-3.3-70b-instruct"
# — they serve their Nemotron-tuned variant instead. Override with
# NVIDIA_DEFAULT_MODEL env var. Verified against the live /v1/models endpoint.
NVIDIA_DEFAULT_MODEL = os.environ.get(
    "NVIDIA_DEFAULT_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"
)
# Strict free-tier enforcement: when true, the 429-with-credit-exhausted
# error message surfaces a clear "monthly quota exhausted" hint instead of
# the generic rate-limit boilerplate. NVIDIA's quota is account-wide (not
# per-model), so FREE_ONLY does NOT filter the catalog — every model is
# "free" within the monthly credit budget. Default false.
NVIDIA_FREE_ONLY = os.environ.get("NVIDIA_FREE_ONLY", "").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════════════════════════════════════════
# BACKEND SELECTION
# ═══════════════════════════════════════════════════════════════════════════════
# Set AGENTKTHX_BACKEND to select a backend.
# Accept any value — plugin backends are loaded lazily via PluginManager.
# Default: "ollama"
AGENTKTHX_BACKEND = os.environ.get("AGENTKTHX_BACKEND", "ollama").lower()


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT MODEL
# ═══════════════════════════════════════════════════════════════════════════════
# Default model for tests and examples
# BitNet default: bitnet-b1.58-2b-4t
# Ollama default: qwen2.5-coder:0.5b-instruct-q4_k_m
# ZAI default: glm-5.1
# OpenRouter default: openrouter/free
if AGENTKTHX_BACKEND == "bitnet":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "bitnet-b1.58-2b-4t")
elif AGENTKTHX_BACKEND in ("llama-server", "llama_server"):
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "default")
elif AGENTKTHX_BACKEND == "zai":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "glm-5.1")
elif AGENTKTHX_BACKEND == "openrouter":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "openrouter/free")
elif AGENTKTHX_BACKEND == "gemini":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "gemini-3.8-flash")
elif AGENTKTHX_BACKEND == "huggingface" or AGENTKTHX_BACKEND == "hf":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "openai/gpt-oss-120b")
elif AGENTKTHX_BACKEND == "openai" or AGENTKTHX_BACKEND == "oai":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "gpt-6-sol")
elif AGENTKTHX_BACKEND == "nvidia":
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
else:
    DEFAULT_MODEL = os.environ.get("AGENTKTHX_MODEL", "qwen2.5:0.5b")


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
MAX_STEPS = int(os.environ.get("AGENTKTHX_MAX_STEPS", "25"))
DEBUG = os.environ.get("AGENTKTHX_DEBUG", "").lower() in ("1", "true", "yes")
VERBOSE = os.environ.get("AGENTKTHX_VERBOSE", "").lower() in ("1", "true", "yes")

# Context window size (Ollama default is 2048)
# Set OLLAMA_NUM_CTX or AGENTKTHX_NUM_CTX to override
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX") or os.environ.get("AGENTKTHX_NUM_CTX") or "0")
# 0 means use Ollama's default (2048)


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR RETRY SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════
# Whether to automatically retry failed tool calls (default: enabled)
RETRY_ON_ERROR = os.environ.get("AGENTKTHX_RETRY_ON_ERROR", "true").lower() in ("1", "true", "yes")

# Maximum retries per tool call failure (default: 2)
MAX_TOOL_RETRIES = int(os.environ.get("AGENTKTHX_MAX_TOOL_RETRIES") or "2")


@dataclass
class Config:
    """AgentKthx configuration."""

    # Backend URLs (plugin-owned URLs are read directly from their env
    # constants — R07.01 dropped the never-read mirror fields)
    ollama_base_url: str = field(default_factory=lambda: OLLAMA_BASE_URL)
    acp_base_url: str = field(default_factory=lambda: ACP_BASE_URL)

    # ACP Credentials
    acp_user: str = field(default_factory=lambda: ACP_USER)
    acp_pass: str = field(default_factory=lambda: ACP_PASS)

    # Backend selection
    backend: str = field(default_factory=lambda: AGENTKTHX_BACKEND)

    # Default model
    default_model: str = field(default_factory=lambda: DEFAULT_MODEL)

    # Agent settings
    max_steps: int = field(default_factory=lambda: MAX_STEPS)
    temperature: float = 0.1
    max_tokens: int = 8192
    num_ctx: int | None = field(default_factory=lambda: _get_num_ctx())

    # Memory settings
    memory_max_messages: int = 50
    memory_max_tokens: int = 4096

    # Security settings
    allow_shell: bool = True
    allow_network: bool = True
    allowed_paths: list[str] = field(default_factory=lambda: ["./output", "./data", "/tmp"])

    # Error retry
    retry_on_error: bool = field(default_factory=lambda: RETRY_ON_ERROR)
    max_tool_retries: int = field(default_factory=lambda: MAX_TOOL_RETRIES)

    # Debug
    debug: bool = field(default_factory=lambda: DEBUG)
    verbose: bool = field(default_factory=lambda: VERBOSE)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls()


def _get_num_ctx() -> int | None:
    """Get num_ctx from environment (reads fresh each time)."""
    val = os.environ.get("OLLAMA_NUM_CTX") or os.environ.get("AGENTKTHX_NUM_CTX") or "0"
    num = int(val) if val else 0
    return num if num > 0 else None


# Global config instance
_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    """Get the global configuration.

    Args:
        reload: If True, re-read from environment variables
    """
    global _config
    if _config is None or reload:
        _config = Config.from_env()
    return _config
